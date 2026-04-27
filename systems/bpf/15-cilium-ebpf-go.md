# Ch 15 — cilium/ebpf：Go 寫 user space

> 目標：學會用 [cilium/ebpf](https://github.com/cilium/ebpf) 套件 + bpf2go 把 BPF object 嵌進 Go binary，做出單一執行檔的雲原生 BPF 工具。

## 為什麼選 Go

C 寫 user space loader（Ch 14）能跑、效能也夠 — 但對「寫個 daemon、export Prometheus、串 gRPC、輸出 JSON」這類工作很笨重。

Go 的優勢：

- **單一靜態 binary**：把 BPF object 嵌進去，部署一個檔
- **生態完整**：HTTP server、JSON、Prometheus client、gRPC、log lib 全是一行 import
- **跟 Kubernetes 同語言**：Cilium、Tetragon、Pixie 全部 Go
- **並行容易**：goroutine + channel 自然處理 ringbuf event 串流

代價是：要學一個新 toolchain（bpf2go），但學起來不到一小時。

## cilium/ebpf 是什麼

不是 binding 層 — 是**純 Go 重寫的 BPF library**，**不依賴 libbpf**。直接呼叫 `bpf()` syscall、自己解析 .bpf.o ELF、自己做 CO-RE relocation。

優點：
- Go binary 不需要 cgo
- 部署不需要 libbpf 套件
- 跨平台 cross-compile 容易

缺點：
- 比 libbpf 晚一些 feature（但近年已經追上）
- helper 列表自己維護

## bpf2go：產生 Go binding

`bpf2go` 是 cilium/ebpf 的 codegen 工具。它做的事跟 libbpf 的 skeleton 類似 — 把 .bpf.o 包成 Go struct。

安裝：

```bash
go install github.com/cilium/ebpf/cmd/bpf2go@latest
```

## 完整 hello-world Go BPF

```bash
mkdir tracer-go && cd tracer-go
go mod init tracer-go
go get github.com/cilium/ebpf
```

寫 BPF 那邊 `bpf/tracer.bpf.c`：

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

struct event {
    int  pid;
    char comm[16];
    char filename[128];
};

struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024);
} events SEC(".maps");

SEC("kprobe/do_sys_openat2")
int BPF_KPROBE(do_openat2, int dfd, const char *filename)
{
    struct event *e;
    e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
    if (!e) return 0;

    e->pid = bpf_get_current_pid_tgid() >> 32;
    bpf_get_current_comm(&e->comm, sizeof(e->comm));
    bpf_probe_read_user_str(&e->filename, sizeof(e->filename), filename);

    bpf_ringbuf_submit(e, 0);
    return 0;
}
```

寫 Go side `main.go`：

```go
package main

//go:generate go run github.com/cilium/ebpf/cmd/bpf2go -cc clang -cflags "-O2 -g -Wall" tracer ./bpf/tracer.bpf.c -- -I./bpf

import (
    "bytes"
    "encoding/binary"
    "fmt"
    "log"
    "os"
    "os/signal"
    "syscall"

    "github.com/cilium/ebpf/link"
    "github.com/cilium/ebpf/ringbuf"
    "github.com/cilium/ebpf/rlimit"
)

type Event struct {
    PID      int32
    Comm     [16]byte
    Filename [128]byte
}

func main() {
    if err := rlimit.RemoveMemlock(); err != nil {
        log.Fatal(err)
    }

    objs := tracerObjects{}
    if err := loadTracerObjects(&objs, nil); err != nil {
        log.Fatalf("loading objects: %v", err)
    }
    defer objs.Close()

    kp, err := link.Kprobe("do_sys_openat2", objs.DoOpenat2, nil)
    if err != nil {
        log.Fatalf("attach kprobe: %v", err)
    }
    defer kp.Close()

    rd, err := ringbuf.NewReader(objs.Events)
    if err != nil {
        log.Fatalf("opening ringbuf: %v", err)
    }
    defer rd.Close()

    sig := make(chan os.Signal, 1)
    signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
    go func() { <-sig; rd.Close() }()

    fmt.Printf("%-16s %-7s %s\n", "COMM", "PID", "FILENAME")

    var ev Event
    for {
        record, err := rd.Read()
        if err != nil {
            if err == ringbuf.ErrClosed {
                return
            }
            log.Printf("reading: %v", err)
            continue
        }
        if err := binary.Read(bytes.NewBuffer(record.RawSample),
                              binary.LittleEndian, &ev); err != nil {
            continue
        }
        comm := string(bytes.TrimRight(ev.Comm[:], "\x00"))
        fname := string(bytes.TrimRight(ev.Filename[:], "\x00"))
        fmt.Printf("%-16s %-7d %s\n", comm, ev.PID, fname)
    }
}
```

Generate + build + run：

```bash
# 生成 vmlinux.h（一次）
sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c > bpf/vmlinux.h

# 生成 Go binding（每次改 .bpf.c 後）
go generate

# build & run
go build -o tracer .
sudo ./tracer
```

bpf2go 會生成兩個檔：
- `tracer_bpfel.go`（little-endian arch）
- `tracer_bpfeb.go`（big-endian arch）

兩個都嵌入了 .bpf.o 的 byte array — 編出的 binary 完全自包含。

## 結構解讀

```go
type tracerObjects struct {
    tracerPrograms      // 所有 SEC programs
    tracerMaps          // 所有 maps
}

type tracerPrograms struct {
    DoOpenat2 *ebpf.Program
}

type tracerMaps struct {
    Events *ebpf.Map
}
```

bpf2go 自動把 BPF C 裡的 program 名跟 map 名轉成 Go field（CamelCase）。型別都是 `*ebpf.Program` / `*ebpf.Map`，跟 cilium/ebpf API 配合。

## Attach 各種 hook

cilium/ebpf 的 `link` package 包了所有 attach 方式：

```go
import "github.com/cilium/ebpf/link"

// kprobe / kretprobe
link.Kprobe("vfs_read", prog, nil)
link.Kretprobe("vfs_read", prog, nil)

// tracepoint
link.Tracepoint("syscalls", "sys_enter_openat", prog, nil)

// uprobe
ex, _ := link.OpenExecutable("/usr/bin/bash")
ex.Uprobe("readline", prog, nil)

// XDP
link.AttachXDP(link.XDPOptions{
    Program:   prog,
    Interface: ifaceIdx,
})

// LSM
link.AttachLSM(link.LSMOptions{Program: prog})
```

每個都回傳一個 `Link`，呼叫 `.Close()` 就 detach。

## 操作 map

```go
// lookup
var pid uint32 = 1234
var count uint64
objs.OpenCounts.Lookup(&pid, &count)

// update
err := objs.OpenCounts.Put(&pid, &count)

// iterate
iter := objs.OpenCounts.Iterate()
var k uint32
var v uint64
for iter.Next(&k, &v) {
    fmt.Printf("pid=%d count=%d\n", k, v)
}
```

## 跨平台 cross-compile

Go binary 跨架構編譯一句話：

```bash
GOOS=linux GOARCH=arm64 go build -o tracer-arm64 .
```

但**注意 BPF object 跟架構綁定** — bpf2go 對每個架構產生一份 .bpf.o（裝飾 build tag），所以你要 build 完所有目標架構的 .bpf.o：

```bash
go generate
# 預設只 build host 架構的 BPF
# 要其他架構：
GOARCH=arm64 go generate
```

實務上自動化會在 CI 跑這事。

## 一個常見誤解

「cilium/ebpf 是 Cilium 專案的內部東西」 — **錯**。

cilium/ebpf 雖然 Cilium 維護，但是個**獨立、廣泛使用**的 library。Tetragon、Inspektor Gadget、Aqua Tracee、Pixie 全用它。**這是 Go 寫 BPF 的事實標準**，不是只給 Cilium 自己用。

## 動手練習

1. **跑通上面的 tracer**：完整流程跑一次。
2. **改成印 ppid**：在 BPF 那邊用 `BPF_CORE_READ(task, real_parent, pid)` 拿 ppid，加進 event struct。
3. **加 HTTP metrics**：用 `net/http` 開一個 `/metrics` endpoint，輸出 Prometheus 格式的 open count by comm。
4. **看 cilium/ebpf 範例**：clone 它、看 `examples/`：
   ```bash
   git clone https://github.com/cilium/ebpf
   ls ebpf/examples/
   ```

## 自我檢核

- [ ] 我能解釋 cilium/ebpf 跟 libbpf 在實作層的關係（不依賴）
- [ ] 我能用 bpf2go 產生 Go binding
- [ ] 我能用 `link.Kprobe` 等 API attach BPF
- [ ] 我能用 `ringbuf.Reader` 消費 BPF event
- [ ] 我能從 Go 操作 map（lookup / put / iterate）

下一站：練習 B — 用 bpftrace、bcc、libbpf+CO-RE 三種寫法做同一支 execve tracer，徹底感受三者的取捨。

→ [練習 B：execve tracer 三種寫法](./practice-b-execve-tracer.md)
