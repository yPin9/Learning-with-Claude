# Ch 25 — ringbuf vs perfbuf：事件傳輸設計

> **目標**：深入理解 `BPF_MAP_TYPE_RINGBUF`（ringbuf）和 `BPF_MAP_TYPE_PERF_EVENT_ARRAY`（perfbuf）的設計差異——記憶體模型、producer/consumer 的交互、wakeup policy——讓你能在正確的場景選對工具。

## 問題：BPF 程式如何把資料傳給 userspace？

BPF 程式在 kernel context 執行，資料要傳給 userspace 有兩種機制：

1. **Shared maps（同步讀取）**：userspace 定期 poll map 讀取資料（適合 counter、stats）
2. **Event streaming（異步推送）**：BPF 程式把事件 push 到 buffer，userspace 消費（適合 execve、network event 等串流）

Event streaming 有兩種 buffer 類型：perfbuf（2016 年引入）和 ringbuf（kernel 5.8，2020 年引入）。

## perfbuf（`BPF_MAP_TYPE_PERF_EVENT_ARRAY`）的設計

```
perfbuf 架構：

每個 CPU 一個獨立的 ring buffer
  CPU 0: [event][event][event]...
  CPU 1: [event][event][event]...
  CPU N: [event][event][event]...

Userspace 需要同時監控所有 CPU 的 buffer（epoll 或 poll N 個 fd）
```

**特性**：
- Per-CPU：每個 CPU 有獨立的 buffer，BPF 程式只寫自己 CPU 的 buffer，**不需要跨 CPU 鎖**
- 記憶體佔用：`N_CPUs × buffer_size_per_cpu`（32 CPU × 1MB = 32 MB）
- Userspace 需要每個 CPU 一個 epoll fd
- 每次 output 都是一次 **拷貝**（`bpf_perf_event_output` 複製資料到 buffer）

## ringbuf（`BPF_MAP_TYPE_RINGBUF`）的設計

```
ringbuf 架構：

所有 CPU 共享一個 ring buffer
  ┌─────────────────────────────────────────┐
  │           shared ring buffer             │
  │  [event][event][       reserved      ]  │
  │                  ↑ producer tail         │
  │                              ↑ consumer head│
  └─────────────────────────────────────────┘

所有 CPU 的 BPF 程式都寫入同一個 buffer
Userspace 只需要一個 fd
```

**特性**：
- Shared：所有 CPU 共享一個 buffer，記憶體更省
- **Zero-copy**：BPF 程式先 `reserve`（在 ring 裡拿到一塊空間），直接寫入，再 `submit`；userspace 直接讀這塊記憶體，不用再次複製
- **Write-side is wait-free**（非阻塞寫入）：producer 用 atomic CAS 更新 tail pointer，不需要 lock
- Read side：只有一個 consumer，不需要鎖
- Wakeup 更精確：可以設定 `BPF_RB_NO_WAKEUP` 累積多個事件再一次 wakeup，減少 context switch

## 直接比較

| 特性 | perfbuf | ringbuf |
|---|---|---|
| **記憶體模型** | Per-CPU（N 份）| Shared（1 份）|
| **記憶體佔用** | N × size | size |
| **拷貝** | 有（一次 copy）| 無（zero-copy）|
| **Write synchronization** | 無（per-CPU 不需要）| CAS（wait-free）|
| **Userspace fd** | N 個（每 CPU 一個）| 1 個 |
| **Drop behavior** | Per-CPU drop | Shared drop |
| **Kernel 版本** | 4.x+ | 5.8+ |
| **Wakeup 控制** | 有限 | 精細（`BPF_RB_NO_WAKEUP`）|

## ringbuf 的 API 詳解

```c
/* 方式一：reserve + submit（推薦，零拷貝）*/
struct event *e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
if (!e)
    return 0;  /* ring 滿了，drop */

/* 直接寫入 ring buffer 的記憶體（不是 stack copy）*/
e->pid = bpf_get_current_pid_tgid() >> 32;
bpf_get_current_comm(&e->comm, sizeof(e->comm));

/* 提交（讓 userspace 可以讀到）*/
bpf_ringbuf_submit(e, 0);  /* flags = 0：立即 wakeup userspace */

/* 或 */
bpf_ringbuf_submit(e, BPF_RB_NO_WAKEUP);  /* 不立即 wakeup，後面批次處理 */
bpf_ringbuf_submit(e, BPF_RB_FORCE_WAKEUP);  /* 強制 wakeup */

/* 如果 reserve 成功但後來決定不 submit */
bpf_ringbuf_discard(e, 0);

/* 方式二：output（有一次拷貝，但更簡單）*/
struct event ev = { .pid = ... };
bpf_ringbuf_output(&rb, &ev, sizeof(ev), 0);
```

## 什麼時候 drop？

**ringbuf drop**：ring 滿了時，`bpf_ringbuf_reserve` 回傳 NULL。

決策：ring 滿的情況代表 consumer（userspace）跟不上 producer（BPF 程式）。解法：
1. 增大 `max_entries`（增加 ring 的容量）
2. 加速 consumer（減少 `ring_buffer__poll` 的處理時間）
3. 降低 BPF 程式的 event rate（filter 掉不重要的事件）

**perfbuf drop**：per-CPU buffer 滿了時，`bpf_perf_event_output` 回傳 -ENOSPC。

**監控 drop**：

```c
/* 在 BPF 程式裡追蹤 drop */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, u64);
} dropped SEC(".maps");

SEC("...")
int my_prog(void *ctx)
{
    struct event *e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
    if (!e) {
        u32 k = 0;
        u64 *d = bpf_map_lookup_elem(&dropped, &k);
        if (d) __sync_fetch_and_add(d, 1);
        return 0;
    }
    /* ... fill event ... */
    bpf_ringbuf_submit(e, 0);
    return 0;
}
```

## Wakeup Policy 的選擇

```c
/* 預設（BPF_RB_FORCE_WAKEUP or flags=0）：每個 event 都 wakeup userspace */
/* 適合：低 event rate（< 10K/s）；latency 要求高（< 1ms）*/
bpf_ringbuf_submit(e, 0);

/* BPF_RB_NO_WAKEUP：不 wakeup，讓 userspace 在 timeout 後醒來 */
/* 適合：高 event rate（> 100K/s）；可以接受批次處理的 latency */
bpf_ringbuf_submit(e, BPF_RB_NO_WAKEUP);

/* BPF_RB_FORCE_WAKEUP：不管 consumer 的設定，強制 wakeup */
bpf_ringbuf_submit(e, BPF_RB_FORCE_WAKEUP);
```

**實際的 wakeup overhead**：每次 wakeup 大約 5–50 μs（syscall + context switch）。在 100K events/s 的情況下，不做批次處理 = 100K wakeup/s = 500ms overhead。

## 選擇建議

**選 ringbuf 如果**：
- Kernel 5.8+（幾乎所有現代系統）
- 想要零拷貝
- 想要簡單的 userspace（只需要一個 fd）
- 記憶體有限

**選 perfbuf 如果**：
- 需要支援 kernel 5.8 以前的系統
- 有嚴格的 per-CPU locality 要求（例如 NUMA-aware 工具）
- 已有大量基於 perfbuf 的程式碼，不想重寫

**不管選哪個**，都要監控 drop count，並根據 event rate 設計合適的 buffer 大小和 wakeup policy。

## 踩雷集錦

1. **ringbuf 的 `max_entries` 必須是 2 的幂次且 ≥ PAGE_SIZE**：`256 * 1024`（256 KB）是常用的初始值；過小會頻繁 drop，過大浪費記憶體

2. **reserve 失敗後不能用 output 作 fallback**：`bpf_ringbuf_output` 也需要在 ring 裡分配空間；ring 滿了的時候 output 也會失敗

3. **userspace 沒有及時 consume 導致 ring 滿**：`ring_buffer__poll` 用太長的 timeout，或 callback 太慢，都會讓 ring 堆積；監控 `consumed` 和 `lost` 計數

4. **BPF_RB_NO_WAKEUP 讓 userspace 感覺延遲高**：No wakeup 意味著 userspace 只在 timeout 時才醒來；如果你的 poll timeout 是 1000ms，你的事件延遲最高是 1000ms；高頻率事件搭配短 timeout + NO_WAKEUP 是好方案

## 動手練習

1. 寫一個程式，用 ringbuf 傳輸事件，故意把 `max_entries` 設很小（4096），然後快速生成大量 event（在另一個 terminal 快速執行很多 `ls`），觀察 drop 計數增加

2. 對比 `bpf_ringbuf_submit(e, 0)` 和 `bpf_ringbuf_submit(e, BPF_RB_NO_WAKEUP)` 在高 event rate（10K events/s）下的 CPU overhead（用 `perf stat` 測量 context switch 次數）

## 本章重點整理

- perfbuf 是 per-CPU 的，不需要跨 CPU 同步；ringbuf 是 shared 的，用 CAS 做 wait-free 寫入
- ringbuf 零拷貝（reserve → 直接寫入 → submit）；perfbuf 有一次拷貝
- Ring 滿時 reserve 失敗（drop）；要監控 drop 並設計合適的 buffer size 和 wakeup policy
- 在 kernel 5.8+ 上優先選 ringbuf

## 自我檢核

- [ ] 能解釋 ringbuf 和 perfbuf 在記憶體模型上的根本差異（shared vs per-CPU）
- [ ] 知道 ringbuf 的「零拷貝」是怎麼實現的（reserve + 直接寫入 ring 記憶體）
- [ ] 知道 `BPF_RB_NO_WAKEUP` 的適用場景和副作用（高 event rate vs 高延遲）

## 延伸閱讀

### 部落格

- **[BPF ring buffer](https://nakryiko.com/posts/bpf-ringbuf/)** — Andrii Nakryiko
  - **這篇說什麼**：ringbuf 的完整設計說明，包括 perfbuf 的問題、ringbuf 的解法、API 的每個細節
  - **讀哪裡**：整篇；這是 ringbuf 最完整的設計文件
  - **為什麼值得讀**：作者是 ringbuf 的設計者

→ [練習 D：PostgreSQL slow query tracer](./practice-d-postgresql-slow-query.md)
