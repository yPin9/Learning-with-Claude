# Ch 43 — Task/inode/sk local storage

> **目標**：理解 BPF local storage 的設計——`BPF_MAP_TYPE_TASK_STORAGE`、`BPF_MAP_TYPE_INODE_STORAGE`、`BPF_MAP_TYPE_SK_STORAGE` 如何讓你把資料「附加」在 kernel object 上，以及它和 hash map 的效能和語意差異。

## 問題：在 Kernel Object 上附加資料

你常常需要追蹤「某個 task 的某個狀態」或「某個 socket 的某個 metadata」。傳統方式用 hash map：

```c
/* 傳統方式：用 hash map 儲存 per-task 資料 */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 10240);
    __type(key, pid_t);       /* PID 作為 key */
    __type(value, u64);
} task_data SEC(".maps");

/* 問題：
   1. 需要手動清理（task 退出後 entry 不會自動刪除）
   2. LRU eviction 可能誤刪活躍 task 的資料
   3. Hash lookup 有 overhead（hash + bucket 搜索）
*/
```

BPF local storage 解決了這些問題：把資料直接「附加」在 kernel object 上，lifecycle 和 object 綁定，自動清理。

## Task Storage（`BPF_MAP_TYPE_TASK_STORAGE`）

```c
/* 定義 task storage map */
struct {
    __uint(type, BPF_MAP_TYPE_TASK_STORAGE);
    __uint(map_flags, BPF_F_NO_PREALLOC);
    __type(key, int);    /* key 必須是 int（大小固定，值忽略）*/
    __type(value, struct my_task_data);
} task_storage SEC(".maps");

struct my_task_data {
    u64 start_time;    /* task 的 startuptime */
    u32 syscall_count; /* 這個 task 的 syscall 計數 */
};

SEC("tracepoint/raw_syscalls/sys_enter")
int trace_syscall(struct trace_event_raw_sys_enter *ctx)
{
    /* 取得目前 task（必須是 BTF-typed）*/
    struct task_struct *task = bpf_get_current_task_btf();

    /* 查找或建立這個 task 的 storage */
    struct my_task_data *data;
    data = bpf_task_storage_get(&task_storage, task, 0,
                                 BPF_LOCAL_STORAGE_GET_F_CREATE);
    if (!data) return 0;

    /* 更新（不需要 hash lookup，直接附加在 task 上）*/
    data->syscall_count++;
    return 0;
}

/* task 退出時自動清理 storage（不需要手動）*/
```

## Inode Storage（`BPF_MAP_TYPE_INODE_STORAGE`）

把資料附加在 file inode 上：

```c
struct {
    __uint(type, BPF_MAP_TYPE_INODE_STORAGE);
    __uint(map_flags, BPF_F_NO_PREALLOC);
    __type(key, int);
    __type(value, struct file_access_count);
} inode_storage SEC(".maps");

struct file_access_count { u64 read_count; u64 write_count; };

SEC("lsm/file_open")
int BPF_PROG(track_file_open, struct file *file)
{
    struct inode *inode = BPF_CORE_READ(file, f_inode);

    struct file_access_count *cnt;
    cnt = bpf_inode_storage_get(&inode_storage, inode, 0,
                                 BPF_LOCAL_STORAGE_GET_F_CREATE);
    if (!cnt) return 0;

    cnt->read_count++;
    return 0;
}
```

## Socket Storage（`BPF_MAP_TYPE_SK_STORAGE`）

把資料附加在 socket 上（最早的 local storage，kernel 5.2+）：

```c
struct {
    __uint(type, BPF_MAP_TYPE_SK_STORAGE);
    __uint(map_flags, BPF_F_NO_PREALLOC);
    __type(key, int);
    __type(value, struct conn_meta);
} sk_storage SEC(".maps");

struct conn_meta {
    u64  connect_time_ns;
    u32  bytes_sent;
    u32  bytes_recv;
};

SEC("fentry/tcp_connect")
int BPF_PROG(track_connect, struct sock *sk)
{
    struct conn_meta *meta;
    meta = bpf_sk_storage_get(&sk_storage, sk, 0,
                               BPF_LOCAL_STORAGE_GET_F_CREATE);
    if (!meta) return 0;

    meta->connect_time_ns = bpf_ktime_get_ns();
    return 0;
}

SEC("fentry/tcp_close")
int BPF_PROG(track_close, struct sock *sk)
{
    struct conn_meta *meta = bpf_sk_storage_get(&sk_storage, sk, 0, 0);
    if (!meta) return 0;

    u64 duration = bpf_ktime_get_ns() - meta->connect_time_ns;
    bpf_printk("conn duration: %llu ns, sent: %u, recv: %u\n",
               duration, meta->bytes_sent, meta->bytes_recv);
    return 0;
    /* socket 關閉時，storage 自動被清理 */
}
```

## Local Storage vs Hash Map 比較

| 面向 | Local Storage | Hash Map |
|---|---|---|
| **Lifecycle** | 和 object 綁定（自動清理）| 手動管理 |
| **Lookup 效率** | 直接從 object 取得（O(1)，無 hash）| hash lookup（有 overhead）|
| **LRU eviction** | 不會（不需要）| 可能誤刪活躍的 |
| **記憶體佔用** | 只在 object 存在時分配 | 預先分配（`BPF_F_NO_PREALLOC` 可選）|
| **適合場景** | per-object state，object 有清晰的 lifecycle | 任意 key，不需要和 object 綁定 |

## 踩雷集錦

1. **`BPF_F_NO_PREALLOC` 是必要的**：task/inode/sk storage 必須設 `BPF_F_NO_PREALLOC`；否則 map 在 load time 就嘗試分配所有 entry 的記憶體，通常超出限制

2. **task storage 要用 BTF-typed task pointer**：`bpf_task_storage_get` 需要 BTF-typed 的 `struct task_struct *`；用 `bpf_get_current_task_btf()` 而不是 `bpf_get_current_task()`

3. **`BPF_LOCAL_STORAGE_GET_F_CREATE` 用於第一次存取**：如果你傳 `0` 作為 flags，lookup 找不到時返回 NULL；`F_CREATE` 讓它自動建立一個零值 entry

4. **sk_storage 在 XDP 裡不可用**：XDP 在 sk_buff 分配前執行，沒有 socket context；sk_storage 只能在有 socket context 的 hook（TC、socket filter、kprobe 等）裡使用

## 動手練習

1. 用 task storage 追蹤每個 process 從開始到退出的總 syscall 次數；在 `sched:sched_process_exit` 時輸出結果

2. 用 sk_storage 追蹤每個 TCP connection 的 RTT（從 SYN-ACK 到 ACK 的時間）

## 本章重點整理

- Local storage 把資料附加在 kernel object（task/inode/socket）上，lifecycle 自動管理
- 比 hash map 效率高（直接從 object 取得，不需要 hash）且不會因 LRU eviction 丟失資料
- `BPF_F_NO_PREALLOC` 是必須的
- task storage 需要 BTF-typed task pointer（`bpf_get_current_task_btf()`）

→ [Ch 44 Debugging：verifier 錯誤與 bpf_printk](./44-debugging-bpf.md)
