# Ch 41 — bpf_timer 與非同步事件

> **目標**：理解 BPF timer 的設計——`bpf_timer_init/set/cancel` API、callback 的執行 context、和 map value 的 lifecycle 綁定關係，以及常見的應用場景（定期清理、心跳、超時 detection）。

## 為什麼需要 BPF Timer？

BPF 程式是事件驅動的——它只在特定事件觸發時執行（kprobe、tracepoint、perf event 等）。但有些任務需要**定期執行**，不依賴外部事件：

- 定期清理 map 裡過期的 entry（connection tracking TTL cleanup）
- 週期性計算統計（每秒輸出 throughput）
- 心跳機制（BPF program 自我更新）

BPF timer（kernel 5.15+）讓 BPF 程式排程一個 callback，在指定的時間後執行。

## bpf_timer 的基本 API

```c
/* bpf_timer 的三個 syscall */

/* 初始化 timer（綁定到 map value 的 lifetime）*/
long bpf_timer_init(struct bpf_timer *timer, struct bpf_map *map, u64 flags);

/* 設定 callback 和觸發時間 */
long bpf_timer_set_callback(struct bpf_timer *timer, void *callback_fn);
long bpf_timer_start(struct bpf_timer *timer, u64 nsecs, u64 flags);

/* 取消 timer */
long bpf_timer_cancel(struct bpf_timer *timer);
```

**重要**：`bpf_timer` 必須存在 BPF map 的 value 裡（不能是 stack 上的臨時變數），因為它的 lifetime 和 map entry 的 lifetime 綁定：刪除 map entry 時，timer 自動被取消。

## 最小範例：每秒輸出 counter

```c
/* timer_counter.bpf.c */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>

/* 存放 timer 和 counter 的結構 */
struct timer_value {
    struct bpf_timer timer;
    u64              count;
};

/* ARRAY map 來存放 timer（key=0）*/
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, struct timer_value);
} timer_map SEC(".maps");

/* timer callback（每秒觸發）*/
static int timer_cb(void *map, int *key, struct timer_value *val)
{
    bpf_printk("count: %llu\n", val->count);
    val->count = 0;  /* 重置 counter */

    /* 重新排程（1 秒後再觸發）*/
    bpf_timer_start(&val->timer, 1000000000, 0);
    return 0;
}

/* 用 event 觸發 BPF 程式，讓它初始化 timer */
SEC("tracepoint/raw_syscalls/sys_enter")
int count_syscalls(void *ctx)
{
    u32 key = 0;
    struct timer_value *val = bpf_map_lookup_elem(&timer_map, &key);
    if (!val) return 0;

    val->count++;

    /* 檢查 timer 是否已初始化（用 count 作為 init flag，不完美但簡單）*/
    /* 生產環境應該用另一個 flag 或在 userspace 初始化 */

    return 0;
}

/* 初始化 timer（需要從 BPF context 呼叫）*/
SEC("fentry/__init_submodule")
int init_timer(void *ctx)
{
    u32 key = 0;
    struct timer_value *val = bpf_map_lookup_elem(&timer_map, &key);
    if (!val) return 0;

    bpf_timer_init(&val->timer, &timer_map, CLOCK_MONOTONIC);
    bpf_timer_set_callback(&val->timer, timer_cb);
    bpf_timer_start(&val->timer, 1000000000, 0);  /* 1 秒 */

    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

**Userspace 初始化 timer（更可靠的方式）**：

```c
/* 用一個特殊的 BPF program 或 map update 觸發 timer init */
/* libbpf 提供了 bpf_map__update_elem 直接操作 */

/* 另一種方式：用 map_extra 欄位設定 timer */
/* 或用 bpf_prog_test_run 觸發一個 init program */
```

## Timer Callback 的執行 Context

Timer callback 執行在：
- **軟中斷 context**（softirq），不是 process context
- 允許大部分 BPF helper，但不允許 sleep 或 block
- 不允許用 spinlock（軟中斷和 spinlock 可能死鎖）
- 最多遞迴深度：1（callback 不能再觸發同一個 timer 的 callback）

## Connection Tracking TTL Cleanup

一個實際的應用：定期清理 connection tracking map 裡的過期 entry：

```c
struct ct_entry {
    struct bpf_timer cleanup_timer;
    u8   state;
    u64  last_seen_ns;
};

/* timer callback：如果 entry 超過 300 秒沒有更新，刪除它 */
static int cleanup_ct_entry(struct bpf_map *map,
                             struct ct_key *key,
                             struct ct_entry *entry)
{
    u64 now = bpf_ktime_get_ns();
    if (now - entry->last_seen_ns > 300ULL * 1000000000) {
        /* 刪除這個 entry（這也會自動 cancel timer）*/
        bpf_map_delete_elem(map, key);
    } else {
        /* 還沒過期，再過 60 秒 check 一次 */
        bpf_timer_start(&entry->cleanup_timer, 60ULL * 1000000000, 0);
    }
    return 0;
}

/* 在新 connection 建立時初始化 timer */
SEC("xdp")
int lb_new_connection(struct xdp_md *ctx)
{
    /* ... create ct_entry ... */
    struct ct_entry new_entry = { ... };

    bpf_timer_init(&new_entry.cleanup_timer, &ct_map, CLOCK_MONOTONIC);
    bpf_timer_set_callback(&new_entry.cleanup_timer, cleanup_ct_entry);
    bpf_timer_start(&new_entry.cleanup_timer, 300ULL * 1000000000, 0);

    bpf_map_update_elem(&ct_map, &key, &new_entry, BPF_NOEXIST);
    return XDP_TX;
}
```

## 踩雷集錦

1. **bpf_timer 必須在 map value 裡**：不能在 stack 上建立 `struct bpf_timer`；verifier 會拒絕

2. **timer 和 map entry 的 lifetime 綁定**：刪除 map entry 時 timer 自動取消；如果你在 callback 裡刪除 entry，callback 結束後 timer 就消失了

3. **callback 函式的簽名必須精確匹配**：`bpf_timer_set_callback` 的 callback 參數型別必須和 map 的 key/value 型別完全匹配；型別不對 verifier 會 reject

4. **初始化順序**：必須先 `bpf_timer_init`，再 `bpf_timer_set_callback`，最後 `bpf_timer_start`；順序錯誤會得到 -EINVAL

5. **CLOCK_MONOTONIC vs CLOCK_BOOTTIME**：`CLOCK_MONOTONIC` 在系統 suspend 期間停止計時；`CLOCK_BOOTTIME` 包含 suspend 時間；選哪個取決於你的 TTL 語意

## 動手練習

1. 實作一個 BPF timer，每 5 秒輸出目前系統上 `sys_enter_openat` 的呼叫次數，然後重置計數器（需要一個存放 counter 的 map 和一個存放 timer 的 map）

2. 把 Ch 39 的 connection tracking 加入 timer-based TTL cleanup：connection entry 建立時設定一個 5 分鐘的 timer，超時後自動刪除 entry

## 本章重點整理

- BPF timer 讓 BPF 程式排程非同步的定期執行，不依賴外部事件
- `bpf_timer` 必須存在 map value 裡，lifetime 和 map entry 綁定
- Callback 在軟中斷 context 執行，不能 sleep / spinlock
- 初始化順序：init → set_callback → start

## 自我檢核

- [ ] 知道為什麼 `bpf_timer` 不能是 stack 變數
- [ ] 能說出 timer callback 的執行 context 和限制
- [ ] 知道刪除 map entry 對 timer 的影響

→ [Ch 42 BPF Iterator 與批次操作](./42-bpf-iterator.md)
