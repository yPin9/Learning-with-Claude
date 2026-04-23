# Ch 25 — Ring buffer vs perf buffer

> 目標：徹底搞懂 perf buffer 與 ring buffer 在內部結構、ordering、loss 處理、wake-up 行為、效能上的差異，學會在現代 BPF 工具中做正確選擇。

## 兩種上報機制的定位

BPF kernel 端要把 event 丟給 user space 消費，主流兩條路：

| | perf buffer | ring buffer |
|---|---|---|
| 進 mainline | 4.3 (2015) | 5.8 (2020) |
| Map type | `BPF_MAP_TYPE_PERF_EVENT_ARRAY` | `BPF_MAP_TYPE_RINGBUF` |
| 結構 | per-CPU buffer | **全域共享 buffer** |
| 生產者 | 只能單 CPU | **多 CPU**（atomic reservation） |
| 消費者 | user space epoll | user space epoll |
| 順序 | 各 CPU 自己 ordered | **全域 ordered** |
| Wake-up | 每事件 IPI | 自適應 batch |
| Loss 行為 | 滿了 drop event | 滿了 reserve 失敗（你決定怎麼處理） |
| 寫入流程 | 1-step copy | 2-step reserve + commit |
| Memory 使用 | nr_cpus × buffer_size | 一份 buffer_size |

## perf buffer 的內部結構

```
CPU 0:  [event][event][event][...buffer 0...]  ← user epoll fd 0
CPU 1:  [event][event][event][...buffer 1...]  ← user epoll fd 1
CPU 2:  [event][event][event][...buffer 2...]  ← user epoll fd 2
CPU 3:  [event][event][event][...buffer 3...]  ← user epoll fd 3
```

**N 顆 CPU = N 個獨立 buffer，N 個 fd**。

優點：
- 純 per-CPU 寫入，零 contention
- 4.3 就有，舊 kernel 也能用

缺點：
- **記憶體 N 倍消耗**（每 CPU 都要 256KB → 64-core 機器吃 16MB）
- 每事件都觸發 wake-up（IPI 中斷）→ user space 反應快但 IPI 開銷大
- 跨 CPU 沒順序保證（A CPU 早寫的可能晚被 user 看到）
- BCC 老工具大量使用，但**新專案不推薦**

## ring buffer 的內部結構

```
              ┌────────────────────────────────────────┐
   全部 CPU →  [event][event][event][event][...buffer]  ← 一個 fd
              └────────────────────────────────────────┘
                  ↑                  ↑
                consumer        producers (atomic reserve)
```

**全域單一 buffer**，多 CPU 透過 atomic 分配寫入位置。

優點：
- **記憶體不隨 CPU 數變大**
- **全域 ordered**：寫入順序就是 user 看到的順序
- **自適應 wake-up**：consumer 在跑時 producer 不發 IPI，省 overhead
- 兩階段 API（reserve + commit）允許**寫到一半中止**（不浪費 ring 空間）

缺點：
- 5.8+ 才有
- 多 CPU 寫入時有 atomic contention（極高 PPS 場景才感覺得到）

## reserve / submit / discard

ring buffer 的兩段式 API：

```c
struct event *e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
if (!e) {
    // ring 滿了，event 丟掉
    return 0;
}

e->pid = ...;
e->ts  = ...;

// 看條件決定：commit 還是丟掉
if (e->latency > threshold) {
    bpf_ringbuf_submit(e, 0);     // 真的提交
} else {
    bpf_ringbuf_discard(e, 0);    // 還掉空間
}
```

`discard` 是 ring buffer 的特色 — 你可以「先寫好、評估後決定是否上報」。perf buffer 沒這個。

## Loss 處理

兩者滿了行為不同：

| | 滿了會怎樣 |
|---|---|
| perf buffer | event 直接丟掉、kernel 內計數 lost event，user 端可以查 |
| ring buffer | `bpf_ringbuf_reserve` 回 NULL，BPF 要自己決定（通常也是丟） |

ring buffer 的「reserve 失敗回 NULL」設計**讓你能 in-kernel 統計 loss**：

```c
struct event *e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
if (!e) {
    __sync_fetch_and_add(&loss_counter, 1);
    return 0;
}
```

## Wake-up 行為（為什麼 ring buffer 通常更快）

每次 producer 寫一個 event，user space epoll 是不是要立刻 wake up？

- **perf buffer**：有 watermark 設定，超過就 wake；預設行為偏向「快」（每事件都 wake）
- **ring buffer**：consumer 還在 polling 時不 wake（自適應）

對「user space 來得及消費」的場景，ring buffer 大幅減少 wake-up overhead。對「user space 慢、producer 快」的場景，兩者表現接近（都 backlog）。

## User 端 polling

兩者 user space API 都是 epoll 為基礎：

### perf buffer (libbpf)

```c
struct perf_buffer *pb;
pb = perf_buffer__new(map_fd, 64 /* pages per CPU */,
                      handle_event, handle_lost, NULL, NULL);
while (running) {
    perf_buffer__poll(pb, 100);
}
```

`handle_event` callback 對每個 event 跑一次。`handle_lost` 對每個 lost batch 跑。

### ring buffer (libbpf)

```c
struct ring_buffer *rb;
rb = ring_buffer__new(map_fd, handle_event, NULL, NULL);
while (running) {
    ring_buffer__poll(rb, 100);
}
```

只一個 callback，處理就比 perf buffer 簡單。

## 測量差異

簡單實驗：寫個 BPF 在 `vfs_read` 上發 100 byte event，跑 30 秒：

| Buffer 類型 | 平均吞吐 | CPU usage | Memory |
|---|---|---|---|
| perf buffer (256 KB/CPU, 16 CPU) | ~500K events/s | 中等 | 4 MB |
| ring buffer (4 MB total) | ~600K events/s | 略低 | 4 MB |

ring buffer 一致性勝出，且記憶體不隨 CPU 數膨脹。

## 何時還可能用 perf buffer

1. **目標 kernel < 5.8** — 沒得選
2. **真的需要 per-CPU 隔離**：不希望某顆 CPU 的高量事件影響其他 CPU 的延遲
3. **既有 BCC 工具兼容**：bcc 老 macro 很多還是 `BPF_PERF_OUTPUT`

對其他 99% 場景，**用 ring buffer**。

## 一個常見誤解

「ring buffer 是 perf buffer 的修正版」 — **嚴格說只對一半**。

ring buffer 解了 perf buffer 多數痛點，但**設計取捨不同**。perf buffer 仍然是「per-CPU 嚴格隔離」的選擇 — 高頻 perf event sampling 場景仍然首選 perf buffer（事實上 BPF profile 仍用 perf event）。

## 動手練習

1. **改 Ch 13 minimal 用 ringbuf vs perfbuf 兩個版本**：分別寫一個，比較程式碼長度。
2. **量 latency**：在 BPF 寫 timestamp 進 event、user 端比較收到時的 timestamp，得 end-to-end latency。
3. **製造 loss**：寫一個小 ringbuf（4096 byte），讓 BPF 一秒寫 100K event、user 端 sleep 不消費，看 reserve 多快開始失敗。
4. **bpftrace 用哪個**：用 `sudo bpftool map list` 看 bpftrace 跑時建的 map type。

## 自我檢核

- [ ] 我能畫出 perf buffer 與 ring buffer 的內部結構差異
- [ ] 我能解釋為什麼 ring buffer 全域 ordered
- [ ] 我能描述 reserve / submit / discard 的兩段式 API
- [ ] 我能說出何時仍應用 perf buffer
- [ ] 我能用 libbpf 寫兩種 buffer 的 user 端 polling

下一章我們處理「BPF 如何組合大型系統」的問題 — tail call、map-in-map、subprogram，這些是構建 Cilium / Falco 等大型系統的關鍵 mechanism。

→ [Ch 26 Tail call、program chain、map-in-map](./26-tailcall-and-composition.md)
