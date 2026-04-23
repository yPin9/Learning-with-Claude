# Final Project — Mini observability + security agent

> 目標：把整套教材的東西組成一個獨立 daemon — 監聽 process / file / network 事件，用 ring buffer 上報、Go 寫 user space、輸出結構化 JSON event stream，可選加 LSM hook 做阻擋。這是你「會 BPF」的證明專案。

## 規格總覽

```
mini-agent
   │
   ├── BPF kernel side (C)
   │   ├── exec tracer       (tracepoint sys_enter_execve)
   │   ├── file open tracer  (tracepoint sys_enter_openat)
   │   ├── connect tracer    (kprobe tcp_v4_connect)
   │   └── (optional) file_open LSM enforcer
   │
   ├── user space (Go)
   │   ├── 載入 + attach 所有 BPF program
   │   ├── 從 ringbuf consume event
   │   ├── 結構化處理（PID → comm cache）
   │   ├── 輸出 JSON stream stdout
   │   ├── HTTP /metrics endpoint (Prometheus)
   │   └── HTTP /policy endpoint (動態管理 LSM 規則)
   │
   └── 整合
       ├── systemd service file
       ├── README + min kernel
       └── basic test suite
```

## 階段任務

把 final project 拆成 5 個階段，每階段都能跑：

| 階段 | 內容 | 對應章節 |
|---|---|---|
| **Stage 1** | exec tracer + JSON output | Ch 11–15 |
| **Stage 2** | + file open tracer + ringbuf 整合 | Ch 8, 14, 25 |
| **Stage 3** | + tcp connect tracer | Ch 4, 13 |
| **Stage 4** | + Prometheus metrics | Ch 28 |
| **Stage 5** | + LSM 阻擋規則（optional） | Ch 23 |

把每個 stage 視為獨立的 milestone — 不要等全部寫完才測。

## Event schema

設計 event JSON 格式：

```json
{"ts":"2026-04-23T14:23:45.123Z", "type":"exec", "pid":12345, "ppid":1234, "comm":"bash",  "filename":"/usr/bin/ls", "args":["ls","-la"]}
{"ts":"2026-04-23T14:23:45.234Z", "type":"open", "pid":12345, "comm":"ls", "filename":"/etc/passwd", "flags":"O_RDONLY"}
{"ts":"2026-04-23T14:23:46.456Z", "type":"connect", "pid":12346, "comm":"curl", "saddr":"10.0.0.5:43210", "daddr":"142.250.80.46:443", "proto":"tcp"}
```

每行一個 event（NDJSON）— 適合 jq、fluentd、Loki 後續處理。

## Stage 1：Exec tracer（最小可跑）

把 Ch 13–15 的 minimal 升級成完整 exec tracer。

#### `bpf/agent.bpf.c` (Stage 1)

<details>
<summary>展開 Stage 1 BPF code</summary>

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

#define EVT_EXEC 1
#define MAX_FILENAME 128
#define MAX_ARGS 4
#define ARG_LEN 32

struct event {
    __u64 ts_ns;
    __u32 type;
    __u32 pid;
    __u32 ppid;
    char  comm[16];
    char  filename[MAX_FILENAME];
    char  args[MAX_ARGS][ARG_LEN];
};

struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 4 * 1024 * 1024);
} events SEC(".maps");

SEC("tracepoint/syscalls/sys_enter_execve")
int handle_exec(struct trace_event_raw_sys_enter *ctx)
{
    struct event *e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
    if (!e) return 0;

    struct task_struct *task = (struct task_struct *)bpf_get_current_task();
    e->ts_ns = bpf_ktime_get_ns();
    e->type  = EVT_EXEC;
    e->pid   = bpf_get_current_pid_tgid() >> 32;
    e->ppid  = BPF_CORE_READ(task, real_parent, tgid);
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    const char *filename = (const char *)ctx->args[0];
    bpf_probe_read_user_str(&e->filename, sizeof(e->filename), filename);

    const char *const *argv = (const char *const *)ctx->args[1];
    for (int i = 0; i < MAX_ARGS; i++) {
        const char *p = NULL;
        bpf_probe_read_user(&p, sizeof(p), &argv[i]);
        if (!p) break;
        bpf_probe_read_user_str(&e->args[i], sizeof(e->args[i]), p);
    }

    bpf_ringbuf_submit(e, 0);
    return 0;
}
```

</details>

#### `main.go` (Stage 1)

<details>
<summary>展開 Stage 1 Go code</summary>

```go
package main

//go:generate go run github.com/cilium/ebpf/cmd/bpf2go -cc clang -cflags "-O2 -g -Wall" agent ./bpf/agent.bpf.c -- -I./bpf

import (
    "bytes"
    "encoding/binary"
    "encoding/json"
    "fmt"
    "log"
    "os"
    "os/signal"
    "syscall"
    "time"

    "github.com/cilium/ebpf/link"
    "github.com/cilium/ebpf/ringbuf"
    "github.com/cilium/ebpf/rlimit"
)

const (
    evtExec    = 1
    maxArgs    = 4
    argLen     = 32
    maxFilename = 128
)

type rawEvent struct {
    TsNs     uint64
    Type     uint32
    PID      uint32
    PPID     uint32
    Comm     [16]byte
    Filename [maxFilename]byte
    Args     [maxArgs][argLen]byte
}

type Event struct {
    TS       string   `json:"ts"`
    Type     string   `json:"type"`
    PID      uint32   `json:"pid"`
    PPID     uint32   `json:"ppid,omitempty"`
    Comm     string   `json:"comm"`
    Filename string   `json:"filename,omitempty"`
    Args     []string `json:"args,omitempty"`
}

var bootTime = time.Now().Add(-readUptime())

func readUptime() time.Duration {
    b, _ := os.ReadFile("/proc/uptime")
    var s float64
    fmt.Sscanf(string(b), "%f", &s)
    return time.Duration(s * float64(time.Second))
}

func cstr(b []byte) string {
    return string(bytes.TrimRight(b, "\x00"))
}

func handle(rec *ringbuf.Record) {
    var raw rawEvent
    if err := binary.Read(bytes.NewReader(rec.RawSample),
                         binary.LittleEndian, &raw); err != nil {
        return
    }
    e := Event{
        TS:   bootTime.Add(time.Duration(raw.TsNs)).UTC().Format(time.RFC3339Nano),
        PID:  raw.PID,
        PPID: raw.PPID,
        Comm: cstr(raw.Comm[:]),
    }
    switch raw.Type {
    case evtExec:
        e.Type = "exec"
        e.Filename = cstr(raw.Filename[:])
        for i := 0; i < maxArgs; i++ {
            a := cstr(raw.Args[i][:])
            if a == "" {
                break
            }
            e.Args = append(e.Args, a)
        }
    }
    if data, err := json.Marshal(e); err == nil {
        fmt.Println(string(data))
    }
}

func main() {
    if err := rlimit.RemoveMemlock(); err != nil {
        log.Fatal(err)
    }

    objs := agentObjects{}
    if err := loadAgentObjects(&objs, nil); err != nil {
        log.Fatal(err)
    }
    defer objs.Close()

    tp, err := link.Tracepoint("syscalls", "sys_enter_execve", objs.HandleExec, nil)
    if err != nil {
        log.Fatal(err)
    }
    defer tp.Close()

    rd, err := ringbuf.NewReader(objs.Events)
    if err != nil {
        log.Fatal(err)
    }
    defer rd.Close()

    sig := make(chan os.Signal, 1)
    signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
    go func() { <-sig; rd.Close() }()

    for {
        rec, err := rd.Read()
        if err != nil {
            if err == ringbuf.ErrClosed {
                return
            }
            log.Printf("read: %v", err)
            continue
        }
        handle(&rec)
    }
}
```

</details>

跑：

```bash
sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c > bpf/vmlinux.h
go generate
go build -o mini-agent .
sudo ./mini-agent | jq -c
```

開另一個 terminal 跑指令，第一個 terminal 飛快滾出 JSON。

## Stage 2：加 file open tracer

加 tracepoint `sys_enter_openat`、event type 2、Go 端對應處理。

關鍵變更：BPF 的 `event` struct 要 union 化或保留共用前綴 + 不同 trailer。簡單做法：每個 event type 用一個獨立 fixed-size struct。

## Stage 3：加 TCP connect tracer

```c
SEC("kprobe/tcp_v4_connect")
int BPF_KPROBE(tcp_connect, struct sock *sk)
{
    struct event *e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
    if (!e) return 0;

    e->ts_ns = bpf_ktime_get_ns();
    e->type  = EVT_CONNECT;
    e->pid   = bpf_get_current_pid_tgid() >> 32;
    bpf_get_current_comm(&e->comm, sizeof(e->comm));

    e->saddr = BPF_CORE_READ(sk, __sk_common.skc_rcv_saddr);
    e->daddr = BPF_CORE_READ(sk, __sk_common.skc_daddr);
    e->dport = bpf_ntohs(BPF_CORE_READ(sk, __sk_common.skc_dport));

    bpf_ringbuf_submit(e, 0);
    return 0;
}
```

## Stage 4：Prometheus metrics

加 `net/http`：

```go
go func() {
    http.Handle("/metrics", promhttp.Handler())
    http.ListenAndServe(":9100", nil)
}()
```

定義 counter：

```go
var (
    eventsTotal = promauto.NewCounterVec(
        prometheus.CounterOpts{Name: "agent_events_total"},
        []string{"type"},
    )
    ringbufLost = promauto.NewCounter(
        prometheus.CounterOpts{Name: "agent_ringbuf_lost_total"},
    )
)
```

每收到 event 觸發 `eventsTotal.WithLabelValues(e.Type).Inc()`。

## Stage 5：LSM 阻擋（optional）

加 file_open hook：

```c
SEC("lsm/file_open")
int BPF_PROG(deny_path, struct file *file)
{
    /* 從 deny_paths map 查 path 是否該擋 */
    ...
    return blocked ? -1 : 0;
}
```

User 端維護 `deny_paths` map：

```go
http.HandleFunc("/policy/deny", func(w http.ResponseWriter, r *http.Request) {
    /* 收 path、寫 deny_paths map */
})
```

## 測試

```bash
# Stage 1
sudo ./mini-agent | grep '"type":"exec"' | head
# 開 terminal 跑指令，看是否每個指令都被印

# Stage 2
sudo ./mini-agent | grep '"type":"open"' | grep '/etc/passwd'

# Stage 3
sudo ./mini-agent | grep '"type":"connect"' &
curl http://example.com

# Stage 4
curl localhost:9100/metrics

# Stage 5
curl -XPOST localhost:9100/policy/deny -d '/etc/shadow'
cat /etc/shadow   # 應該被擋
```

## 整合到 systemd

`/etc/systemd/system/mini-agent.service`：

```ini
[Unit]
Description=Mini BPF observability+security agent
After=network.target

[Service]
ExecStart=/usr/local/bin/mini-agent
StandardOutput=append:/var/log/mini-agent/events.ndjson
Restart=on-failure
AmbientCapabilities=CAP_BPF CAP_PERFMON CAP_NET_ADMIN
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
```

```bash
sudo cp mini-agent /usr/local/bin/
sudo systemctl enable --now mini-agent
journalctl -fu mini-agent
```

## 寫 README

```markdown
# mini-agent

BPF-based mini observability + security agent.

## Requirements
- Linux kernel >= 5.15 with `CONFIG_DEBUG_INFO_BTF=y`
- (Stage 5) BPF LSM enabled (`lsm=...,bpf` in boot params)

## Build
go generate
go build -o mini-agent .

## Run
sudo ./mini-agent

## Metrics
http://localhost:9100/metrics
```

## 自我檢核

完成的標準：

- [ ] Stage 1–4 全部跑通
- [ ] JSON output 結構乾淨、可被 jq 處理
- [ ] /metrics endpoint 有 events_total counter
- [ ] systemd service 跑得起來、retry on failure
- [ ] 跨 kernel 測試（你的 + 至少一個老一點的）通過
- [ ] README 完整
- [ ] (optional) Stage 5 LSM 阻擋有作用

完成這個專案，你已經把這套教材所有的概念都串起來了 — 從 verifier 到 ringbuf、從 kprobe 到 LSM、從 BPF C 到 Go 整合。**Production BPF 工程師的入門已完成**。

## 接下來該學什麼

這套教材覆蓋現代 BPF 90% 場景。下一步建議：

- **讀 Cilium 原始碼**：production-grade BPF 的最佳教科書
- **跟進 LWN BPF 文章**：每兩週的 BPF kernel patch summary
- **參與 [iovisor-dev](https://lists.iovisor.org/g/iovisor-dev) / Cilium Slack**：BPF 社群很活
- **挑一個 unsolved 場景做工具**：例如 GPU profiling、io_uring 觀測、kernel symbol resolution 等等 BPF 還在發展的方向

歡迎進入 BPF 的世界。
