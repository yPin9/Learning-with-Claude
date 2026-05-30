# Ch 17 — cilium/ebpf：Go 生態系

> **目標**：理解 cilium/ebpf library 的架構和 API，能用 Go 寫出完整的 eBPF 工具，並知道它和 libbpf 的設計差異。

> **環境**：Go 1.21+，`github.com/cilium/ebpf` v0.13+，Ubuntu 22.04，kernel 5.15+。

## 為什麼學 Go 的 eBPF？

很多 modern infrastructure 工具用 Go 寫（Kubernetes、Docker、Prometheus），在這個生態系中整合 eBPF 觀測點，Go 是更自然的選擇。cilium/ebpf 是目前最成熟的 Go eBPF library，Cilium、Tetragon、Falco 都用它。

它和 libbpf 的核心差異：**cilium/ebpf 是純 Go 實作，不依賴 libbpf C library**，不需要 CGO，可以靜態編譯。

## 先建立直覺：cilium/ebpf 的架構

```
你的 .bpf.c（kernel-side，和 libbpf 完全一樣）
         │
         ▼ clang -target bpf
      .bpf.o（BPF bytecode + BTF）
         │
         ▼ bpf2go（code generator）
      bpf_bpfel.go / bpf_bpfeb.go（自動生成）
         │  包含：
         │  - 嵌入的 .bpf.o bytes
         │  - BTF-typed map accessor
         │  - Load/Close 函式
         ▼
      你的 main.go（userspace）
```

`bpf2go` 工具和 libbpf 的 skeleton 作用相同：生成型別安全的 Go wrapper。

## 安裝和設定

```bash
# 安裝 bpf2go
go install github.com/cilium/ebpf/cmd/bpf2go@latest

# go.mod 加入依賴
go get github.com/cilium/ebpf@latest
```

**目錄結構**：

```
myebpf/
  ├── go.mod
  ├── main.go             ← 你寫的 userspace Go 程式
  ├── bpf/
  │   ├── trace.bpf.c     ← kernel-side C
  │   └── vmlinux.h       ← 從 bpftool 生成
  └── bpf_bpfel.go        ← bpf2go 自動生成
```

**觸發 bpf2go**（在 main.go 裡加 `//go:generate`）：

```go
// main.go
//go:generate go run github.com/cilium/ebpf/cmd/bpf2go -cc clang bpf ./bpf/trace.bpf.c -- -I./bpf
```

```bash
go generate ./...  # 觸發 bpf2go，生成 bpf_bpfel.go
```

## 完整範例：追蹤 openat

**`bpf/trace.bpf.c`**（和 libbpf 範例完全相同）：

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>

struct event {
    __u32 pid;
    char  comm[16];
    char  filename[128];
};

struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024);
} rb SEC(".maps");

const struct event *unused __attribute__((unused));

SEC("tracepoint/syscalls/sys_enter_openat")
int trace_openat(struct trace_event_raw_sys_enter *ctx)
{
    struct event *e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) return 0;

    e->pid = bpf_get_current_pid_tgid() >> 32;
    bpf_get_current_comm(&e->comm, sizeof(e->comm));
    bpf_probe_read_user_str(&e->filename, sizeof(e->filename),
                            (void *)ctx->args[1]);
    bpf_ringbuf_submit(e, 0);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

**`main.go`**：

```go
//go:generate go run github.com/cilium/ebpf/cmd/bpf2go -cc clang bpf ./bpf/trace.bpf.c -- -I./bpf -D__TARGET_ARCH_x86_64

package main

import (
    "bytes"
    "encoding/binary"
    "fmt"
    "os"
    "os/signal"
    "syscall"

    "github.com/cilium/ebpf/link"
    "github.com/cilium/ebpf/ringbuf"
    "github.com/cilium/ebpf/rlimit"
)

// event 必須和 .bpf.c 裡的 struct event 完全一致
type event struct {
    Pid      uint32
    Comm     [16]byte
    Filename [128]byte
}

func main() {
    // 某些系統需要提高 rlimit（kernel 5.11 之前）
    if err := rlimit.RemoveMemlock(); err != nil {
        fmt.Fprintf(os.Stderr, "removing memlock: %v\n", err)
    }

    // 載入 BPF 程式（bpf2go 生成的）
    objs := bpfObjects{}
    if err := loadBpfObjects(&objs, nil); err != nil {
        fmt.Fprintf(os.Stderr, "loading objects: %v\n", err)
        os.Exit(1)
    }
    defer objs.Close()

    // attach 到 tracepoint
    tp, err := link.Tracepoint("syscalls", "sys_enter_openat",
        objs.TraceOpenat, nil)
    if err != nil {
        fmt.Fprintf(os.Stderr, "attaching tracepoint: %v\n", err)
        os.Exit(1)
    }
    defer tp.Close()

    // 建立 ring buffer reader
    rd, err := ringbuf.NewReader(objs.Rb)
    if err != nil {
        fmt.Fprintf(os.Stderr, "ring buffer: %v\n", err)
        os.Exit(1)
    }
    defer rd.Close()

    // 捕獲 SIGINT/SIGTERM
    sigs := make(chan os.Signal, 1)
    signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
    go func() {
        <-sigs
        rd.Close()
    }()

    fmt.Printf("%-8s %-16s %s\n", "PID", "COMM", "FILENAME")

    for {
        record, err := rd.Read()
        if err != nil {
            if err == ringbuf.ErrClosed {
                break
            }
            fmt.Fprintf(os.Stderr, "read: %v\n", err)
            continue
        }

        var e event
        if err := binary.Read(bytes.NewReader(record.RawSample),
                              binary.LittleEndian, &e); err != nil {
            fmt.Fprintf(os.Stderr, "parse: %v\n", err)
            continue
        }

        fmt.Printf("%-8d %-16s %s\n",
            e.Pid,
            nullTermString(e.Comm[:]),
            nullTermString(e.Filename[:]))
    }
}

func nullTermString(b []byte) string {
    n := bytes.IndexByte(b, 0)
    if n < 0 {
        n = len(b)
    }
    return string(b[:n])
}
```

**執行**：

```bash
go generate ./...
go build -o openat_tracer
sudo ./openat_tracer
```

## Map 存取（Go 側）

bpf2go 生成的 `bpfObjects` 包含型別化的 map accessor：

```go
// 假設你的 map 定義是：
// struct { __uint(type, BPF_MAP_TYPE_HASH); ... __type(key, u32); __type(value, u64); } stats

// bpf2go 生成：
// objs.Stats 的型別是 *ebpf.Map

// 讀取
var value uint64
key := uint32(1234)
if err := objs.Stats.Lookup(key, &value); err != nil {
    log.Printf("lookup: %v", err)
}

// 寫入
newVal := uint64(100)
objs.Stats.Put(key, newVal)  // BPF_ANY
objs.Stats.Update(key, newVal, ebpf.UpdateExist)
objs.Stats.Delete(key)

// 遍歷
var k uint32
var v uint64
iter := objs.Stats.Iterate()
for iter.Next(&k, &v) {
    fmt.Printf("key=%d val=%d\n", k, v)
}
```

## cilium/ebpf vs libbpf 的設計差異

| 面向 | libbpf（C）| cilium/ebpf（Go）|
|---|---|---|
| CGO 依賴 | 不需要（C library） | 不需要（純 Go）|
| Static binary | 可以（靜態連結 libbpf） | 容易（Go 預設靜態）|
| Code generation | `bpftool gen skeleton` | `bpf2go` |
| Ring buffer | `ring_buffer__new` + poll | `ringbuf.NewReader` + Read |
| Map type safety | struct member（skeleton）| `*ebpf.Map` + generic API |
| Error handling | errno（負整數）| Go error interface |
| BTF / CO-RE | libbpf 處理 | 純 Go 實作（co-re package）|
| Cross-compile | 複雜 | Go 原生支援 GOARCH 交叉編譯 |

**cilium/ebpf 的主要優勢**：
- 純 Go，不需要 CGO，靜態 binary 容易
- 和 Go 生態系（goroutine、channel、context）無縫整合
- 更好的 error 訊息（Go error wrapping）

**libbpf 的主要優勢**：
- 更快的 map 操作（C 的 overhead 更低）
- 對 kernel 最新功能的支援通常先到 libbpf
- kernel source tree 自帶，是「官方」實作

## 踩雷集錦

1. **struct 的 byte order**：Go 的 `binary.Read` 用 `binary.LittleEndian` 解析 BPF 事件（x86-64 是 little-endian）；ARM 大 endian 系統要特別注意

2. **struct alignment**：Go struct 的欄位會自動對齊，但和 C struct 的 layout 可能不一致；用 `encoding/binary` 而不是 `unsafe.Pointer` 轉換，比較安全

3. **`rlimit.RemoveMemlock()` 在 kernel 5.11 後不需要**：5.11 之前需要提高 `RLIMIT_MEMLOCK` 才能 lock 記憶體給 BPF 用；5.11 之後改用 cgroup memory，不再需要

4. **`ringbuf.ErrClosed` 是正常退出**：當你 close ring buffer reader 時，`rd.Read()` 回傳 `ringbuf.ErrClosed`；這是正常的退出信號，不是錯誤

5. **`bpf2go` 生成兩個檔案（`_bpfel.go` 和 `_bpfeb.go`）**：分別是 little-endian 和 big-endian 版本；在 x86-64 上用 `_bpfel.go`；`_bpfeb.go` 是給 MIPS、s390 等大端平台的

## 動手練習

1. 用 cilium/ebpf 實作一個 TCP 連線追蹤器：attach `kprobe/tcp_connect`，讀取 `struct sock` 的 dest IP 和 port，用 ring buffer 傳給 userspace

2. 實作 map iterator：在 userspace 每秒輸出 `stats` map 的所有 key-value，比較 libbpf 和 Go 的遍歷 API 的可讀性

3. 把練習一的工具交叉編譯成 ARM64 binary：`GOARCH=arm64 go build`，觀察是否成功（kernel-side 的 `.bpf.o` 仍然是 x86-64 BPF bytecode，但 userspace binary 是 ARM64 native code）

## 本章重點整理

- cilium/ebpf 是純 Go 的 eBPF library，不需要 CGO，靜態 binary 容易
- `bpf2go` 工具從 `.bpf.c` 生成型別安全的 Go wrapper
- Kernel-side C 和 libbpf 完全一樣；差異只在 userspace 工具
- Go 的 ring buffer API（`ringbuf.NewReader`）比 libbpf 的 poll loop 更符合 Go 慣用風格

## 自我檢核

- [ ] 知道 cilium/ebpf 為什麼不需要 CGO
- [ ] 能說出 `bpf2go` 和 `bpftool gen skeleton` 各自生成什麼，以及設計上的相似之處
- [ ] 知道為什麼 Go struct 和 C struct 的 layout 可能不一致，以及怎麼正確 parse BPF 事件

## 延伸閱讀

### 官方文件

- **[cilium/ebpf documentation](https://pkg.go.dev/github.com/cilium/ebpf)**
  - **讀哪裡**：`link`、`ringbuf`、`ebpf.Map` 的 API doc
  - **學什麼**：所有 Go API 的完整說明

- **[cilium/ebpf examples](https://github.com/cilium/ebpf/tree/main/examples)**
  - **讀哪裡**：`kprobe`、`ringbuffer`、`tracepoint` 這三個例子
  - **學什麼**：完整的、可以直接跑的 Go eBPF 程式範例

→ [Ch 18 交叉比較：選哪個工具？](./18-toolchain-comparison.md)
