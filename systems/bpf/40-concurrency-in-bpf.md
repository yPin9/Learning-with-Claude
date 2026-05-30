# Ch 40 — 並發控制：spinlock, per-CPU maps, atomic

> **目標**：理解 BPF 程式裡的並發問題——多個 CPU 同時存取共享 map 的競爭條件、三種解法（per-CPU maps / atomic operations / BPF spinlock）的取捨，以及什麼時候每種方法是正確的選擇。

## BPF 的並發模型

BPF 程式在 **每個 CPU 上獨立執行**，沒有隱式的保護。如果你的程式存取共享的 map，可能發生競爭條件：

```
CPU 0：
  r0 = map[key]      (讀取 value = 5)
  r0 += 1            (r0 = 6)
  map[key] = r0      (寫入 6)

CPU 1（同時）：
  r0 = map[key]      (也讀到 5，因為 CPU 0 還沒寫回)
  r0 += 1            (r0 = 6)
  map[key] = r0      (寫入 6)

結果：map[key] = 6（應該是 7，少了一次加法）
```

這就是典型的 read-modify-write race condition。

## 解法一：Per-CPU Maps（最快，無鎖）

把 counter 分到每個 CPU 上，每個 CPU 只操作自己的那份：

```c
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, u64);
} pkt_count SEC(".maps");

SEC("xdp")
int count_pkts(struct xdp_md *ctx)
{
    u32 key = 0;
    u64 *cnt = bpf_map_lookup_elem(&pkt_count, &key);
    if (cnt)
        (*cnt)++;  /* 不需要 atomic！每個 CPU 有自己的 copy */
    return XDP_PASS;
}
```

**Userspace 讀取（加總所有 CPU）**：

```c
int ncpus = libbpf_num_possible_cpus();
u64 values[ncpus];  /* buffer 必須是 ncpus × value_size */
u32 key = 0;
bpf_map_lookup_elem(map_fd, &key, values);
u64 total = 0;
for (int i = 0; i < ncpus; i++) total += values[i];
printf("total packets: %llu\n", total);
```

**適合場景**：
- 高頻 counter（每個 packet/syscall 一次）
- 不需要跨 CPU 一致性的場景
- 統計類資料（可以接受 "eventually consistent"）

**不適合**：需要精確的跨 CPU 一致性（例如 rate limiter、唯一 ID 生成）。

## 解法二：Atomic Operations（適合整數操作）

用 CPU 的 atomic 指令做無鎖的整數操作：

```c
/* Atomic fetch-and-add：用 __sync_fetch_and_add（GCC builtin）*/
SEC("xdp")
int count_atomic(struct xdp_md *ctx)
{
    u32 key = 0;
    u64 *cnt = bpf_map_lookup_elem(&counter, &key);
    if (cnt)
        __sync_fetch_and_add(cnt, 1);  /* 展開成 lock xadd（x86-64）*/
    return XDP_PASS;
}
```

**BPF atomic 指令（kernel 5.12+，更完整）**：

```c
/* 新的 BPF atomic 指令（kernel 5.12+ 的 ATOMIC opcode）*/

/* atomic add */
__sync_fetch_and_add(val, 1);

/* compare-and-exchange（CAS）*/
u64 old = 5, new = 6;
u64 result = __sync_val_compare_and_swap(val, old, new);

/* fetch-and-store */
u64 prev = __sync_lock_test_and_set(val, 42);

/* atomic AND / OR / XOR */
__sync_fetch_and_and(flags, ~FLAG_BIT);
__sync_fetch_and_or(flags, FLAG_BIT);
```

**限制**：
- 只能對整數做 atomic（不能對 struct 做）
- 有 overhead（`lock` prefix 或類似指令）
- 在高並發下仍然有 cache line contention

**適合場景**：共享的整數 counter 或 flags，需要跨 CPU 一致性。

## 解法三：BPF Spinlock（kernel 5.1+）

對於需要保護複合操作（read-modify-write with multiple fields）的場景：

```c
/* 定義帶 spinlock 的 value */
struct account {
    struct bpf_spin_lock lock;   /* BPF spinlock */
    u64  balance;
    u64  tx_count;
};

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1000);
    __type(key, u32);
    __type(value, struct account);
} accounts SEC(".maps");

SEC("kprobe/do_transaction")
int update_account(struct pt_regs *ctx)
{
    u32 account_id = (u32)PT_REGS_PARM1(ctx);
    s64 amount     = (s64)PT_REGS_PARM2(ctx);

    struct account *acc = bpf_map_lookup_elem(&accounts, &account_id);
    if (!acc) return 0;

    /* 獲得 spinlock */
    bpf_spin_lock(&acc->lock);

    /* 保護的臨界區：複合操作，必須是 atomic 的 */
    if (acc->balance + amount < 0) {
        bpf_spin_unlock(&acc->lock);
        return -EINVAL;  /* 餘額不足 */
    }
    acc->balance += amount;
    acc->tx_count++;

    /* 釋放 spinlock */
    bpf_spin_unlock(&acc->lock);
    return 0;
}
```

**BPF spinlock 的限制**：
- 只在 BPF 程式裡持有；不能跨越 helper call（`bpf_spin_lock` 和 `bpf_spin_unlock` 之間不能呼叫任何 helper）
- 不能在 NMI context 使用（perf_event 程式不能用 spinlock）
- verifier 強制要求：每個 `bpf_spin_lock` 必須有對應的 `bpf_spin_unlock`，且不能有 early return

## 選擇指南

```
需要並發保護？
├── 是整數 counter/flags 嗎？
│   ├── 可以接受 per-CPU 最終一致？ → PERCPU_ARRAY（最快）
│   └── 需要跨 CPU 一致？ → __sync_fetch_and_add（atomic）
│
├── 是複合操作（多個欄位）嗎？
│   ├── 操作頻率高？ → 考慮用 PERCPU_HASH，定期合併
│   └── 操作頻率低？ → bpf_spin_lock
│
└── 是指標操作？ → 重新設計，BPF 不支援指標的 atomic 操作
```

## 實際範例：Rate Limiter

```c
/* 每個 IP 每秒最多 N 個請求 */
struct rate_state {
    struct bpf_spin_lock lock;
    u64  window_start_ns;
    u32  count;
};

struct {
    __uint(type, BPF_MAP_TYPE_LRU_HASH);
    __uint(max_entries, 100000);
    __type(key, __be32);           /* src IP */
    __type(value, struct rate_state);
} rate_map SEC(".maps");

/* 注意：LRU_HASH + spinlock 可能有效能問題；
   生產環境用 PERCPU_LRU_HASH 更好 */

SEC("xdp")
int rate_limit(struct xdp_md *ctx)
{
    /* ... parse headers ... */
    __be32 src = iph->saddr;

    struct rate_state zero = {};
    struct rate_state *rs = bpf_map_lookup_or_try_init(&rate_map, &src, &zero);
    if (!rs) return XDP_PASS;

    u64 now = bpf_ktime_get_ns();
    const u64 WINDOW = 1000000000ULL;  /* 1 second */
    const u32 LIMIT  = 100;            /* 100 req/s */

    bpf_spin_lock(&rs->lock);

    if (now - rs->window_start_ns > WINDOW) {
        rs->window_start_ns = now;
        rs->count = 0;
    }
    rs->count++;
    int over_limit = (rs->count > LIMIT);

    bpf_spin_unlock(&rs->lock);

    if (over_limit) return XDP_DROP;
    return XDP_PASS;
}
```

## 踩雷集錦

1. **在 spinlock 臨界區呼叫 helper 會被 verifier reject**：`bpf_spin_lock` 到 `bpf_spin_unlock` 之間不能有任何 helper call（包括 `bpf_printk`）；把所有 helper call 移到臨界區外

2. **NMI 不能用 spinlock**：perf_event、hardware PMU 觸發的 BPF program 是 NMI context，不能用 `bpf_spin_lock`；改用 atomic 操作

3. **`__sync_fetch_and_add` 的 cache line 問題**：所有 CPU 都 atomic 存取同一個 cache line 會導致嚴重的 cache coherence traffic（MESI protocol）；在 100 個 CPU 的系統上，這比 per-CPU 慢 100 倍以上

4. **bpf_spin_lock 的 struct 必須是 map value 的第一個欄位（或至少 8-byte aligned）**：verifier 要求 `struct bpf_spin_lock` 在 struct 裡的 offset 必須 8-byte aligned

5. **PERCPU_ARRAY 在 userspace 讀取時需要正確 buffer size**：`bpf_map_lookup_elem` 的 buffer 必須是 `value_size × ncpus`；用 `libbpf_num_possible_cpus()` 取 CPU 數

## 動手練習

1. 寫一個有 race condition 的 BPF counter（故意不用 atomic），在高並發下觸發競爭；然後改成三種版本（per-CPU / atomic / spinlock），用 `perf stat` 比較它們的 CPU overhead

2. 實作一個 per-IP rate limiter（每秒 10 個請求），用 bpf_spin_lock 保護；用 hping3 或 scapy 生成高速流量，確認超過閾值的封包被 DROP

## 本章重點整理

- BPF 程式在多個 CPU 上並發執行，共享 map 需要保護
- 三種方案：PERCPU（最快，無鎖，最終一致）、atomic（整數操作，有 overhead）、spinlock（複合操作，最嚴格）
- Spinlock 臨界區不能有 helper call；NMI context 不能用 spinlock
- 選擇順序：per-CPU（能用就用）→ atomic（需要一致性）→ spinlock（複合操作）

## 自我檢核

- [ ] 能描述 PERCPU map 和 regular map 在並發行為上的差異
- [ ] 知道 BPF spinlock 的兩個限制（臨界區不能有 helper call、NMI 不能用）
- [ ] 能說出在高並發的 counter 場景下，per-CPU 比 atomic 快的原因

→ [Ch 41 bpf_timer 與非同步事件](./41-bpf-timer.md)
