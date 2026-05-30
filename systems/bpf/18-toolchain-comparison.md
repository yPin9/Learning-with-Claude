# Ch 18 — 交叉比較：選哪個工具？

> **目標**：建立一個決策框架——在給定需求和約束的情況下，知道選 bpftrace / BCC / libbpf C / cilium-ebpf Go 哪個最合適，以及各自的邊界在哪裡。

## 為什麼需要這個？

你現在知道四種工具怎麼用了。但在實際工作中，問題是「我現在要做某件事，應該用哪個工具」——這需要對各工具的適用場景有清晰的判斷，而不只是知道語法。

## 工具定位矩陣

先用一個維度組合理解各工具的定位：

```
                 開發速度快 ◄────────────────────► 效能 / 可控性高
                    │                                    │
                    │                                    │
      bpftrace      │                              libbpf C
   (one-liner，      │                         （生產工具，最低
    ad-hoc 分析）    │                           overhead，最大控制）
                    │
      BCC Python    │                           cilium/ebpf Go
   （prototype，     │                         （生產工具，Go 生態）
    教學，一次性）   │
                    │
       快速回答 ────────────────────────────────── 長期維護
```

## 決策指南

### 場景一：快速回答一個問題

「哪個 process 最常呼叫 `open`？」、「這個系統的 TCP 連線建立 latency 是多少？」

**選 bpftrace**：

```bash
# 30 秒內得到答案
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_openat { @[comm]++; }
                  interval:s:5 { print(@); clear(@); exit(); }'
```

原因：不需要編譯，語法簡潔，適合臨時性的 ad-hoc 觀測。

---

### 場景二：寫一個一次性的分析工具（Python 環境可用）

你需要比 bpftrace 更複雜的邏輯（例如連接 kernel 事件和 userspace 的 DB 查詢），或想用 Python 的資料處理能力（pandas、matplotlib）。

**選 BCC Python**：

```python
# 快速 prototype，有 Python 生態系
b = BPF(text="...")
b.attach_kprobe(event="...", fn_name="...")
```

原因：Python 的 quick iteration cycle；可以直接用 Python 後處理 BPF 資料。

---

### 場景三：需要分發給客戶或在生產機器上執行的 C 工具

- 目標機器可能沒有 kernel headers
- 需要在不同 kernel 版本上運作
- 啟動時間不能超過 100ms
- 記憶體 footprint 要小

**選 libbpf C + CO-RE**：

```c
// 預先編譯，有 CO-RE，一個 binary 走天下
struct myprog_bpf *skel = myprog_bpf__open();
myprog_bpf__load(skel);
myprog_bpf__attach(skel);
```

原因：預先編譯（沒有 runtime 編譯 overhead）；CO-RE 讓一個 binary 可以在多個 kernel 版本執行；不需要 kernel headers on target。

---

### 場景四：Go 程式需要整合 eBPF 觀測點

你的主程式是 Go（如 Kubernetes controller、microservice daemon），需要在裡面嵌入 eBPF 功能。

**選 cilium/ebpf Go**：

```go
// 完全 Go，不需要 CGO，和 Go 生態系無縫整合
objs := bpfObjects{}
loadBpfObjects(&objs, nil)
link.Tracepoint("syscalls", "sys_enter_openat", objs.TraceOpenat, nil)
```

原因：純 Go 不需要 CGO；和 goroutine、channel、context 自然結合；Go 的靜態 binary 容易部署。

---

### 場景五：需要最大化效能或最細緻的控制

你在寫一個高效能的 XDP load balancer，需要精確控制 map 大小、per-CPU 分配、tail call chain 的每個細節。

**選 libbpf C（或直接 raw syscall）**：

原因：C 的 overhead 最低；libbpf 的 API 最完整，最新的 kernel 功能（BPF arena、BPF exception、kfunc）最先在 libbpf 有支援；对底层细节控制最好。

---

## 工具比較表

| 面向 | bpftrace | BCC Python | libbpf C | cilium/ebpf Go |
|---|---|---|---|---|
| **啟動時間** | <1s | 1–3s（LLVM compile）| <100ms | <100ms |
| **Kernel header 依賴** | 不需要（BTF）| 需要（runtime 編譯）| 不需要（CO-RE）| 不需要（CO-RE）|
| **Binary 分發** | 腳本 | Python + 依賴 | 單一 binary | 單一 binary |
| **記憶體 footprint** | 中（LLVM）| 高（LLVM + Python）| 低 | 低 |
| **迭代速度** | 最快 | 快 | 慢（需重新編譯）| 慢 |
| **CO-RE 支援** | 是（透過 BTF）| 否（runtime 編譯）| 是 | 是 |
| **生態系** | standalone | Python | C | Go |
| **最新 kernel 功能** | 中 | 中 | 最快 | 稍慢 |
| **適合 production** | 否 | 否 | 是 | 是 |

## 什麼時候用多個工具組合

這四個工具不是互斥的——好的 workflow 通常是：

1. **bpftrace one-liner** 快速確認問題在哪、驗證 kernel 支援這個 probe
2. **BCC Python** 快速 prototype 完整邏輯（有 Python 的幫助快很多）
3. **libbpf C** 把驗證過的邏輯重寫成生產工具

```
問題發現 ──▶ bpftrace（驗證 probe 存在）
            ──▶ BCC Python（prototype 完整邏輯）
               ──▶ libbpf C / cilium/ebpf（生產工具）
```

## 踩雷集錦

1. **bpftrace 無法取代 libbpf 做 stateful 邏輯**：bpftrace 的 map 和 aggregation 功能有限；複雜的狀態機（例如 connection tracking）還是要用 libbpf

2. **BCC 在 container 裡需要特別設定**：BCC 需要 `/usr/src/linux-headers-*`、debugfs mount、`SYS_ADMIN` capability；在 container 環境用 libbpf 通常更簡單

3. **libbpf 和 cilium/ebpf 的版本不一定同步**：cilium/ebpf 有自己的 BTF 和 CO-RE 實作；某個 kernel 的新功能可能 libbpf 已支援，但 cilium/ebpf 還沒；關注各自的 changelog

## 決策 Cheatsheet

```
需求 → 工具
───────────────────────────────────────────────────────
快速 one-liner / ad-hoc 分析              → bpftrace
Python 生態系 / 一次性分析                → BCC
C 生產工具 / 最佳效能 / 最新功能          → libbpf
Go 生態系整合 / 靜態 binary              → cilium/ebpf
教學 / 範例演示                          → BCC 或 bpftrace
XDP / TC 高效能 networking               → libbpf（C）
Kubernetes operator / Go 服務            → cilium/ebpf
```

## 本章重點整理

- bpftrace 和 BCC 是探索工具；libbpf C 和 cilium/ebpf 是生產工具
- 選 bpftrace：ad-hoc 問題，不需要 distribute
- 選 libbpf C：C 生態系、最佳效能、最新功能支援
- 選 cilium/ebpf：Go 生態系、靜態 binary、goroutine 整合

## 自我檢核

- [ ] 給一個具體場景，能在 30 秒內說出應該用哪個工具，以及原因
- [ ] 能解釋為什麼 BCC 在生產環境不如 libbpf（三個理由）
- [ ] 知道 bpftrace 和 BCC 都有 runtime 編譯，以及這對部署的影響

## 延伸閱讀

### 部落格

- **[BPF tools comparison](https://www.brendangregg.com/blog/2019-01-01/learn-ebpf-tracing.html)** — Brendan Gregg
  - **這篇說什麼**：從 observability 角度比較各工具的適用場景
  - **讀哪裡**：工具比較那一節
  - **為什麼值得讀**：Gregg 每個工具都用過，他的判斷有實戰依據

→ [練習 C：libbpf execve tracer](./practice-c-libbpf-execve-tracer.md)
