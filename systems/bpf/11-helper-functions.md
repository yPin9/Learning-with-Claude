# Ch 11 — Helper Functions 系統

> **目標**：理解 BPF helper functions 的分類體系、呼叫規範、GPL 授權限制，以及最重要的 helper group——memory read、map ops、event output、context access、network helpers——的完整用法。

## 為什麼需要這個？

BPF 程式不能直接呼叫 kernel 函式（那樣 verifier 就失去控制了）。它能做的所有和 kernel 互動的事，都必須透過 **helper functions**：一組由 kernel 定義的、驗證過安全的 API。

Helper 的選擇直接影響你能做什麼。不知道 `bpf_get_stackid()` 存在，你就不能做 flamegraph；不知道 `bpf_sk_redirect_map()` 存在，你就不能做 socket acceleration。這章是 helper 的系統性地圖。

> 完整的 helper list 在 `include/uapi/linux/bpf.h` 的 `enum bpf_func_id`，以及 `man 7 bpf-helpers`。本章重點在最重要和最常用的那些。

## 先建立直覺：Helper 是 Kernel 提供的 Syscall-like API

```
BPF 程式（kernel context）
  │
  │  BPF_CALL 指令（imm = helper id）
  │  參數放在 r1–r5（最多 5 個）
  │  回傳值在 r0
  ▼
Kernel helper function（whitelist）
  │  例如：bpf_map_lookup_elem, bpf_printk, bpf_get_current_pid_tgid
  ▼
結果回到 BPF 程式
```

每個 helper 有一個唯一的數字 ID（`enum bpf_func_id`），並非所有 helper 對所有 program type 都可用——能用哪些 helper 取決於 program type。

## 呼叫規範與 GPL 授權

**呼叫規範**：
- 參數：`r1`–`r5`（最多 5 個，超過 5 個參數的用 struct 打包）
- 回傳值：`r0`
- `r1`–`r5` 在 helper 呼叫後變成 `NOT_INIT`（不能再使用，除非重新賦值）
- `r6`–`r9` 是 callee-saved（helper 呼叫前後保持）

**GPL 授權限制**：某些 helper 標記為 GPL-only，只有宣告 `char LICENSE[] SEC("license") = "GPL"` 的程式才能使用。非 GPL 授權的程式只能用非 GPL helper 的子集。

```c
/* 宣告 GPL license（大部分情況下必須） */
char LICENSE[] SEC("license") = "GPL";
```

## 分類一：Memory Read

這些 helper 讓你安全地讀取 kernel 和 userspace 的記憶體（verifier 不允許直接 dereference 非 BTF-typed pointer）。

### `bpf_probe_read_kernel(dst, size, src)`

從 kernel 記憶體讀取 `size` bytes 到 `dst`。失敗（如 kernel pointer 無效）回傳負錯誤碼。

```c
/* 讀取 kernel struct 的欄位 */
struct task_struct *task = (struct task_struct *)bpf_get_current_task();
char comm[16];
bpf_probe_read_kernel(comm, sizeof(comm), task->comm);
bpf_printk("comm: %s\n", comm);
```

> **注意**：在有 CO-RE 和 BTF 的情況下，優先用 `BPF_CORE_READ()`；`bpf_probe_read_kernel` 是更底層的 fallback，沒有 relocation 支援。

### `bpf_probe_read_user(dst, size, src)`

從 userspace 記憶體讀取。如果 `src` 是 userspace 位址（例如從 syscall 參數拿到的 pointer），用這個；用 `bpf_probe_read_kernel` 讀 userspace 位址會失敗。

```c
SEC("tracepoint/syscalls/sys_enter_openat")
int trace_open(struct trace_event_raw_sys_enter *ctx)
{
    /* ctx->args[1] 是 userspace 的 filename 指標 */
    char filename[64];
    bpf_probe_read_user_str(filename, sizeof(filename), (void *)ctx->args[1]);
    bpf_printk("open: %s\n", filename);
    return 0;
}
```

### `bpf_probe_read_kernel_str(dst, size, src)` / `bpf_probe_read_user_str()`

讀取 null-terminated string，回傳實際讀取的 bytes（包含 null terminator）。比 `bpf_probe_read_kernel` 更安全，自動在 `size` 處截斷。

## 分類二：Map Operations

這是最核心的 helper group——BPF 程式的大部分狀態操作都在這裡。

### `bpf_map_lookup_elem(map, key)`

查找 map 裡的 key，回傳 value 的 pointer，找不到回傳 NULL。

```c
u32 key = 0;
u64 *val = bpf_map_lookup_elem(&my_map, &key);
if (!val)  /* 必須做 NULL check！*/
    return 0;
(*val)++;  /* 現在可以安全使用 */
```

**關鍵**：回傳的是 map value 的 **in-place pointer**，不是 copy；直接修改 `*val` 就是修改 map 裡的資料。

### `bpf_map_update_elem(map, key, value, flags)`

插入或更新 map 裡的 key-value pair。

`flags` 的選項：
- `BPF_ANY`（0）：不管 key 存不存在，都更新
- `BPF_NOEXIST`：只在 key 不存在時插入（atomic compare-and-swap）
- `BPF_EXIST`：只在 key 存在時更新

```c
u32 key = bpf_get_current_pid_tgid() >> 32;
u64 now = bpf_ktime_get_ns();
/* 插入或更新（BPF_ANY）*/
bpf_map_update_elem(&start_ts, &key, &now, BPF_ANY);
```

### `bpf_map_delete_elem(map, key)`

刪除 map 裡的 key。對 ARRAY map 無效（回傳 -EINVAL）。

### `bpf_map_push_elem / bpf_map_pop_elem / bpf_map_peek_elem`

用於 QUEUE 和 STACK map type。

## 分類三：Event Output

### `bpf_perf_event_output(ctx, map, flags, data, size)`

把 `data` 送到 PERF_EVENT_ARRAY map（傳給 userspace）。`BPF_F_CURRENT_CPU` 讓資料送到目前 CPU 的 buffer。

```c
struct {
    __uint(type, BPF_MAP_TYPE_PERF_EVENT_ARRAY);
    __uint(key_size, sizeof(int));
    __uint(value_size, sizeof(u32));
} events SEC(".maps");

SEC("kprobe/vfs_write")
int on_vfs_write(struct pt_regs *ctx)
{
    struct {
        u32 pid;
        u64 size;
    } data = {
        .pid  = bpf_get_current_pid_tgid() >> 32,
        .size = PT_REGS_PARM3(ctx),
    };
    bpf_perf_event_output(ctx, &events, BPF_F_CURRENT_CPU,
                          &data, sizeof(data));
    return 0;
}
```

### `bpf_ringbuf_reserve / bpf_ringbuf_submit / bpf_ringbuf_discard`

Ring buffer 的零拷貝輸出（推薦）。詳見 [Ch 8](./08-bpf-maps.md) 和 [Ch 25](./25-ringbuf-vs-perfbuf.md)。

```c
struct event *e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
if (!e) return 0;
e->pid = bpf_get_current_pid_tgid() >> 32;
bpf_ringbuf_submit(e, 0);
```

### `bpf_printk(fmt, ...)`

輸出 debug 訊息到 `/sys/kernel/debug/tracing/trace_pipe`。最多 3 個參數（kernel 5.13 之前），或用 `bpf_trace_printk`（更靈活但 overhead 更高）。

```c
/* 注意：bpf_printk 是 macro，展開成 bpf_trace_printk */
bpf_printk("hello: pid=%d comm=%s\n", pid, comm);
/* 讀取輸出 */
/* sudo cat /sys/kernel/debug/tracing/trace_pipe */
```

> `bpf_printk` 有不小的 overhead，只用於開發 debug，不要在 production 的 hot path 用。

## 分類四：Process / Thread Context

### `bpf_get_current_pid_tgid()`

回傳 `(tgid << 32 | pid)`。

```c
u64 pt = bpf_get_current_pid_tgid();
u32 tgid = pt >> 32;   /* POSIX PID（process）*/
u32 pid  = pt & 0xFFFFFFFF;  /* POSIX TID（thread）*/
```

### `bpf_get_current_uid_gid()`

回傳 `(gid << 32 | uid)`，使用 *real* UID/GID（不是 effective）。

### `bpf_get_current_comm(buf, size)`

填充目前 process 的名稱（`task->comm`，最長 15 字元）到 `buf`。

```c
char comm[16];
bpf_get_current_comm(&comm, sizeof(comm));
```

### `bpf_get_current_task()`

回傳目前 task 的 `struct task_struct *`（raw pointer，需配合 `bpf_probe_read_kernel` 或 `BPF_CORE_READ`）。

### `bpf_get_current_task_btf()`（kernel 5.11+）

同 `bpf_get_current_task()`，但回傳 BTF-typed 的指標，可以直接做 CO-RE field access：

```c
struct task_struct *task = bpf_get_current_task_btf();
/* 直接 field access，有 CO-RE relocation */
pid_t pid = task->tgid;
```

## 分類五：Timing

### `bpf_ktime_get_ns()`

回傳從 boot 以來的 nanoseconds（不包含 suspend 時間）。用於測量時間間隔。

```c
u64 start = bpf_ktime_get_ns();
/* ... 做一些事 ... */
u64 delta_ns = bpf_ktime_get_ns() - start;
```

### `bpf_ktime_get_boot_ns()`（kernel 5.8+）

同上，但包含 suspend 時間（wall clock 的 boot 時間基準）。

## 分類六：Tail Call

### `bpf_tail_call(ctx, prog_array, index)`

跳轉到 `prog_array` map（`BPF_MAP_TYPE_PROG_ARRAY`）裡 index `index` 的 BPF 程式，不返回（或 index 無效時返回到目前程式繼續）。

```c
struct {
    __uint(type, BPF_MAP_TYPE_PROG_ARRAY);
    __uint(max_entries, 10);
    __uint(key_size, sizeof(u32));
    __uint(value_size, sizeof(u32));
} prog_array SEC(".maps");

SEC("kprobe/vfs_read")
int dispatch(struct pt_regs *ctx)
{
    u32 index = some_condition ? 0 : 1;
    bpf_tail_call(ctx, &prog_array, index);
    /* 如果 tail call 失敗（index 無效或 prog_array 沒有對應 entry），繼續執行 */
    bpf_printk("tail call failed\n");
    return 0;
}
```

詳見 [Ch 39 Tail calls](./39-tail-calls-bpf-to-bpf.md)。

## 分類七：Stack / Stackid

### `bpf_get_stackid(ctx, map, flags)`

取得目前的 call stack trace，存入 `BPF_MAP_TYPE_STACK_TRACE` map，回傳 stack id（用於去重）。

```c
long stack_id = bpf_get_stackid(ctx, &stacks, BPF_F_REUSE_STACKID);
```

### `bpf_get_stack(ctx, buf, size, flags)`

直接把 stack frames 填入 `buf`（每個 frame 是 u64 instruction pointer）。

## 分類八：Network Helpers

### `bpf_skb_load_bytes(skb, offset, to, len)`

從 `sk_buff` 讀取 `len` bytes 到 `to`（處理 skb 可能是 fragmented 的情況）。

### `bpf_skb_store_bytes(skb, offset, from, len, flags)`

修改封包內容（例如修改 IP 或 TCP header）。

### `bpf_l3_csum_replace / bpf_l4_csum_replace`

更新 L3（IP）或 L4（TCP/UDP）的 checksum，在修改封包內容後呼叫。

### `bpf_redirect(ifindex, flags)`

在 TC 或 XDP 程式裡把封包重導到另一個網路介面。

### `bpf_sk_lookup_tcp / bpf_sk_lookup_udp`

查找符合條件的 socket（用於 sockmap / load balancer）。

## Helper Availability by Program Type

Helper 的可用性取決於 program type。驗證方式：

```bash
sudo bpftool feature probe | grep helper
# 輸出類似：
# helper bpf_map_lookup_elem is available
# helper bpf_map_update_elem is available
# ...
```

一般規則：
- 所有 program type 都能用 map ops、`bpf_printk`、time、pid/comm
- `bpf_probe_read_*` 系列：kprobe / tracepoint / perf event 可用；socket / XDP 不需要（有更好的存取方式）
- 網路 helpers：只有 socket / TC / XDP 相關 program types 可用
- `bpf_get_stackid`：perf event / kprobe / tracepoint 可用

## 踩雷集錦

1. **`bpf_probe_read_kernel` 讀 userspace 指標**：`bpf_probe_read_kernel` 只能讀 kernel 記憶體；讀 userspace 的 pointer（如 syscall 參數傳進來的 buffer 位址）必須用 `bpf_probe_read_user`

2. **呼叫 helper 後 r1–r5 失效**：呼叫 helper 後，r1–r5 被 verifier 標記為 uninitialized；如果你需要在 helper 呼叫前後使用同一個值，先存到 r6–r9

3. **`bpf_map_lookup_elem` 回傳的指標不是 copy**：直接修改回傳的 pointer 就是修改 map 裡的資料，不需要再 `bpf_map_update_elem`；反過來說，如果你想要 copy，要手動 copy 出來

4. **`bpf_printk` 的格式化限制**：在 kernel 5.13 之前，`bpf_printk` 最多支援 3 個格式化參數，且不支援 `%s` 搭配 pointer（要先用 `bpf_probe_read_kernel_str` 讀到 stack，再傳 stack buffer）

5. **GPL-only helper 的限制**：`bpf_probe_read_*`、`bpf_get_stackid`、`bpf_perf_event_output` 等是 GPL-only；如果你的 LICENSE 不是 "GPL"，這些 helper 會被 verifier reject

## 動手練習

1. 寫一個 kprobe 程式，在 `vfs_read` 的入口同時記錄：pid、comm、fd、想讀的 byte 數（用 PT_REGS_PARM3）；用 RINGBUF 傳給 userspace，每秒輸出前 10 筆

2. 用 `bpf_ktime_get_ns()` 測量 `vfs_read` 的執行時間：在 kprobe（入口）記錄開始時間存到 hash map，在 kretprobe（出口）計算 delta；用 PERCPU_ARRAY 統計時間分布（<1us / 1-10us / >10us）

3. 用 `bpf_get_stackid` 在 perf_event 裡收集 CPU stack trace，搭配 flamegraph.pl 生成 flamegraph（可以先用 BCC 的 `profile` 對照理解原理）

## 本章重點整理

- BPF helper 是 BPF 程式和 kernel 互動的唯一合法管道；helper 的可用性取決於 program type
- Memory read helpers 有 kernel/user 版本；只能用對應的 API 讀對應的記憶體空間
- `bpf_map_lookup_elem` 回傳 in-place pointer，不是 copy；直接修改就是修改 map
- GPL-only helper 需要程式宣告 GPL license

## 自我檢核

- [ ] 能解釋為什麼 r1–r5 在 helper 呼叫後失效，以及如何保存需要的值
- [ ] 知道 `bpf_probe_read_kernel` 和 `bpf_probe_read_user` 的差別，以及什麼時候用哪個
- [ ] 能說出 `bpf_map_lookup_elem` 回傳 in-place pointer 的意涵，以及它的安全性保證
- [ ] 知道至少 3 個 GPL-only helper，以及 license 宣告對哪些功能有影響

## 延伸閱讀

### 官方文件

- **[bpf-helpers man page](https://man7.org/linux/man-pages/man7/bpf-helpers.7.html)**
  - **讀哪裡**：整頁；按 helper 名稱查找，有每個 helper 的完整說明
  - **學什麼**：所有 helper 的參數、回傳值、可用的 program type、kernel 版本需求

- **[include/uapi/linux/bpf.h: enum bpf_func_id](https://elixir.bootlin.com/linux/latest/source/include/uapi/linux/bpf.h)**
  - **讀哪裡**：`enum bpf_func_id` 和其後的 helper 定義 comment
  - **學什麼**：完整的 helper list，按 ID 排列；每個 helper 的完整說明就在 enum comment 裡

### 部落格

- **[BPF helper functions](https://docs.cilium.io/en/stable/reference-guides/bpf/#helper-functions)** — Cilium docs
  - **這篇說什麼**：按 category 分類的 helper 說明，比 man page 更易讀
  - **讀哪裡**：整節；特別是 "Generic helpers" 和 "Network helpers"
  - **為什麼值得讀**：Cilium 大量使用 BPF，他們的文件品質很高

→ [Ch 12 BPF syscall 底層序列](./12-bpf-syscall-internals.md)
