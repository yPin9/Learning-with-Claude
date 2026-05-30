# Final Project — 生產級 eBPF Observability Agent

> **目標**：整合本課 70%+ 的核心概念，設計並實作一個可以在生產環境部署的 eBPF observability agent：多 program 協同、ringbuf 事件流、userspace daemon、Prometheus metrics 匯出、Docker 打包，以及完整的測試和文件。

## 專案概覽

你要建立 **BPFWatch**——一個生產等級的 Linux observability agent，用 eBPF 做系統行為的全面監控：

```
BPFWatch 架構：

Kernel Layer（eBPF programs）：
  ├── sys_enter tracepoint → process exec/syscall monitoring
  ├── fentry/fexit → function latency tracking
  ├── TC BPF → per-process network I/O
  ├── perf_event → CPU profiling (99 Hz)
  └── kprobe → memory allocation tracking

Event Transport：
  └── BPF ringbuf → userspace consumer

Userspace Daemon（bpfwatch）：
  ├── libbpf-based BPF loader
  ├── ring_buffer__poll loop
  ├── Event aggregation（每 15 秒）
  ├── Prometheus metrics endpoint（:9090/metrics）
  └── JSON log output

Docker Container：
  └── 單一 image，自包含（embedded .bpf.o），掛 host cgroup + bpffs
```

## 功能需求

### Core Monitoring

1. **Process Lifecycle**：追蹤所有 exec/exit，記錄 pid/ppid/comm/argv/uid
2. **CPU Top**：每 15 秒輸出 CPU 使用率 top 10 processes（基於 99 Hz sampling）
3. **Syscall Frequency**：per-process 的 syscall 分布（哪個 process 最常呼叫什麼 syscall）
4. **Network I/O**：per-process 的 outbound/inbound bytes（TC BPF on default interface）
5. **Memory Allocation**：追蹤 `kmalloc` 的 top allocators（按 bytes）

### Security Audit

6. **Exec Audit**：所有 exec 事件（含 argv）
7. **Sensitive File Access**：`/etc/shadow`, `/etc/sudoers`, `/etc/passwd` 的存取
8. **Privilege Escalation**：`setuid(0)` 呼叫
9. **Network Anomalies**：連到非預期 IP（可配置白名單）

### Metrics

所有監控資料匯出為 Prometheus metrics：

```
bpfwatch_exec_total{comm="bash", uid="1000"} 42
bpfwatch_cpu_usage_percent{pid="1234", comm="nginx"} 15.2
bpfwatch_syscall_total{pid="1234", syscall="read"} 1502
bpfwatch_net_bytes_total{pid="1234", direction="outbound"} 1048576
bpfwatch_security_events_total{type="exec", severity="info"} 3
```

## 技術要求

### BPF Side
- 所有 BPF programs 用 libbpf + skeleton
- 用 CO-RE（不需要 kernel headers on target）
- 用 ringbuf（不是 perfbuf）
- 用 task local storage 做 per-process state
- 用 bpf_timer 做週期性清理
- 至少使用 5 種不同的 program type

### Userspace
- C 或 Go 實作（選一個你更熟悉的）
- 用 libbpf（C）或 cilium/ebpf（Go）
- Prometheus metrics 用 `/metrics` endpoint（HTTP）
- JSON log 輸出到 stdout（方便 log aggregation）
- Graceful shutdown on SIGTERM

### Deployment
- Dockerfile（FROM scratch 或 distroless 最佳）
- docker-compose.yml（含 Prometheus + Grafana）
- 必要的 Kubernetes YAML（DaemonSet，帶正確的 security context）

## 架構設計文件

在開始寫 code 之前，先設計以下內容（寫在 README 裡）：

### 1. Event Schema

定義所有 event 的結構（kernel-side 和 userspace 共享）：

```
- ProcessEvent: { type, pid, ppid, uid, comm, argv, timestamp }
- SyscallEvent: { pid, syscall_nr, count, timestamp }
- NetworkEvent: { pid, direction, bytes, connections, timestamp }
- SecurityEvent: { type, pid, comm, detail, severity, timestamp }
```

### 2. BPF Program 清單

列出所有 BPF programs，每個的：
- SEC 標注（attach type）
- Attach 目標（哪個 hook）
- 產生哪種 event

### 3. Map 設計

列出所有 maps，每個的：
- 型別（HASH / ARRAY / PERCPU / RINGBUF / TASK_STORAGE）
- Key/Value 結構
- 生命週期

## 交付標準

### 基本要求（必須完成）
- [ ] 能在 Ubuntu 22.04 + kernel 6.x 上正確 load（不跑 verifier 錯誤）
- [ ] 5 種以上 program type 都有對應的實作
- [ ] ringbuf 事件流能正確傳遞到 userspace
- [ ] Prometheus metrics endpoint 能被 curl 到（`curl localhost:9090/metrics`）
- [ ] SIGTERM 後優雅退出，BPF programs 正確 detach

### 進階要求（加分）
- [ ] CO-RE 支援（在不同 kernel 版本上都能跑）
- [ ] Docker 打包（`docker run --privileged bpfwatch`）
- [ ] Grafana dashboard JSON（匯入後能看到主要 metrics）
- [ ] Kubernetes DaemonSet deployment（在 kind 或 minikube 上測試）
- [ ] 完整的 README（架構圖、安裝說明、使用範例）

## 參考實作（開源工具）

在寫自己的 agent 之前，先讀這些優秀的開源 eBPF agent 的架構：

- **[Pixie](https://github.com/pixie-io/pixie)**：CNCF 的 k8s observability，eBPF + Stirling engine
  - 重點看：`src/stirling/bpf_tools/` 的 BPF program 結構
- **[Grafana Beyla](https://github.com/grafana/beyla)**：Application auto-instrumentation
  - 重點看：`pkg/internal/ebpf/` 的 program 設計
- **[inspektor-gadget](https://github.com/inspektor-gadget/inspektor-gadget)**：Kubernetes eBPF 工具集
  - 重點看：gadget 的 interface 設計（每個 gadget 是一個 BPF program）

## 自我評估 Checklist

完成後，用這個 checklist 評估你的實作品質：

**BPF 技術深度**
- [ ] 使用了 CO-RE（`BPF_CORE_READ`），不是 hardcoded offset
- [ ] 用了 ringbuf 而不是 perfbuf
- [ ] 用了 task local storage 做跨 program 的 state 共享
- [ ] 有至少一個用 bpf_timer 做的週期性操作
- [ ] Verifier log 裡沒有 warning（`log_level = 1` 確認）

**程式碼品質**
- [ ] 所有 BPF map 的 lifetime 正確管理（不會洩漏）
- [ ] Userspace 優雅處理 ring buffer drop（有 drop counter metric）
- [ ] 正確處理 SIGTERM（cleanup + unpin）
- [ ] 每個 BPF program 都有對應的測試（至少 smoke test）

**生產可用性**
- [ ] 能在 kernel 5.15 到 6.x 的任意版本上運行
- [ ] docker image < 50 MB
- [ ] CPU overhead < 3%（99 Hz profiling 的基線）
- [ ] 沒有記憶體洩漏（leaksanitizer 或 valgrind 確認）

## 本課所覆蓋的核心概念（對照表）

| 本課章節 | Final Project 使用到的地方 |
|---|---|
| Ch 4–5：ISA + Verifier | CO-RE 存取的正確性；verifier 通過 |
| Ch 8：BPF Maps | RINGBUF / HASH / PERCPU / TASK_STORAGE |
| Ch 9–10：BTF + CO-RE | 跨 kernel 版本相容 |
| Ch 11：Helpers | bpf_probe_read / bpf_timer / bpf_get_current_task_btf |
| Ch 15–16：libbpf + skeleton | BPF object lifecycle |
| Ch 19–22：Tracing | kprobe / tracepoint / fentry / USDT |
| Ch 23–25：perf + ringbuf | CPU profiling pipeline |
| Ch 28：TC BPF | Network I/O tracking |
| Ch 35：BPF-LSM | Security event audit（optional）|
| Ch 39：Tail calls | Program pipeline（optional）|
| Ch 40：Concurrency | per-CPU counter + atomic |
| Ch 41：bpf_timer | 週期性 cleanup |
| Ch 43：Local storage | per-process state sharing |
| Ch 44：Debugging | Production-quality error handling |

## 最終說明

這個 Final Project 沒有「唯一正確的答案」——重要的是你的設計決策是否有意義，程式碼是否能解釋為什麼這樣做。

完成後，嘗試在 GitHub 開一個 repo 分享你的 BPFWatch。eBPF 社群很小，高品質的範例工具很有價值。

你已經讀完了一門包含 44 章、6 個練習的 eBPF 完整課程。你現在能：
- 讀懂 `kernel/bpf/` 的 source code
- 設計自己的 BPF program，知道哪種 hook 最合適
- Debug verifier 的拒絕訊息
- 在生產環境部署 eBPF 工具

接下來去做就對了。
