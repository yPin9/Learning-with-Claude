# Ch 20 — Tracepoints 與 raw_tracepoints

> **目標**：理解 tracepoint 和 raw_tracepoint 的底層架構——靜態 hook 的插入方式、ABI 穩定性保證、格式資訊的讀取，以及 raw_tracepoint 比普通 tracepoint 效能更好的原因。

## Tracepoint 的設計：靜態標記

和 kprobe 不同，tracepoint 是 kernel 開發者**主動插入**在 source code 裡的觀測點：

```c
/* kernel/sched/core.c（簡化）*/
void __sched schedule(void)
{
    struct task_struct *tsk = current;

    /* 這行宏展開成一個靜態 hook */
    trace_sched_switch(false, prev, next);
    /* ...實際 context switch 邏輯... */
}
```

`TRACE_EVENT(sched_switch, ...)` 宏在 kernel 編譯時：
1. 生成一個靜態的 tracepoint site（`nop` 指令，未啟用時 overhead 接近零）
2. 生成 format 資訊（`/sys/kernel/debug/tracing/events/sched/sched_switch/format`）
3. 生成 `trace_sched_switch()` 函式

當你 attach 一個 BPF program 到這個 tracepoint 時，kernel 把 `nop` 替換成 `call` 指令，讓 BPF program 被呼叫。**未啟用時幾乎零 overhead**，這是 tracepoint 比 kprobe 效能好的關鍵。

## Tracepoint 的格式資訊

每個 tracepoint 有一個 format 文件，描述它能傳遞哪些資料：

```bash
cat /sys/kernel/debug/tracing/events/syscalls/sys_enter_openat/format
```

```
name: sys_enter_openat
ID: 630
format:
	field:unsigned short common_type;	offset:0;	size:2;	signed:0;
	field:unsigned char common_flags;	offset:2;	size:1;	signed:0;
	field:unsigned char common_preempt_count;	offset:3;	size:1;	signed:0;
	field:int common_pid;	offset:4;	size:4;	signed:1;

	field:int __syscall_nr;	offset:8;	size:4;	signed:1;
	field:int dfd;	offset:16;	size:8;	signed:1;
	field:const char * filename;	offset:24;	size:8;	signed:1;
	field:int flags;	offset:32;	size:8;	signed:1;
	field:umode_t mode;	offset:40;	size:8;	signed:1;

print fmt: "dfd: 0x%08lx, filename: 0x%p, flags: 0x%08lx, mode: 0x%08lx",
	    ((unsigned long)(REC->dfd)), REC->filename, ((unsigned long)(REC->flags)),
	    ((unsigned long)(REC->mode))
```

在 BPF 程式裡，對應這個 context 的 struct：

```c
/* vmlinux.h 裡的定義（從 BTF 生成）*/
struct trace_event_raw_sys_enter {
    struct trace_entry ent;    /* 公共 header */
    long int id;               /* syscall number */
    long unsigned int args[6]; /* syscall 參數 */
    char __data[0];
};
```

用法：

```c
SEC("tracepoint/syscalls/sys_enter_openat")
int trace_openat(struct trace_event_raw_sys_enter *ctx)
{
    /* ctx->id = __NR_openat（syscall 號碼）*/
    /* ctx->args[0] = dfd */
    /* ctx->args[1] = filename（userspace pointer）*/
    /* ctx->args[2] = flags */

    char filename[64];
    bpf_probe_read_user_str(filename, sizeof(filename),
                            (void *)ctx->args[1]);
    bpf_printk("openat: %s flags=0x%x\n", filename, (int)ctx->args[2]);
    return 0;
}
```

## 查看所有可用的 Tracepoints

```bash
# 列出所有 tracepoint category
ls /sys/kernel/debug/tracing/events/
# block  ext4  kmem  migrate  net  nfs  random  rcu  sched  ...

# 列出某個 category 的所有 tracepoints
ls /sys/kernel/debug/tracing/events/sched/
# sched_kthread_stop  sched_migrate_task  sched_process_exec ...

# 用 bpftrace 列出（更方便）
sudo bpftrace -l 'tracepoint:*' | wc -l      # 總數
sudo bpftrace -l 'tracepoint:sched:*'         # sched 的全部
sudo bpftrace -lv 'tracepoint:sched:sched_switch'  # 看參數
```

## ABI 穩定性：tracepoint 的承諾

Tracepoint 是 Linux kernel 的**穩定 ABI**：一旦 tracepoint 加進 kernel 並出現在 release 裡，它的名稱和基本欄位不能被刪除或破壞相容性。

但有一個細微的例外：**新欄位可以被加到 tracepoint 的末尾**，舊的 BPF 程式（只存取前面的欄位）不受影響。

對比 kprobe：函式簽名是 kernel 的內部實作細節，任何 kernel 版本都可能改變（升 kernel 後 `PT_REGS_PARM3(ctx)` 拿到的是不同的值）。

**建議**：對於 observability 工具，優先用 tracepoint；只在 tracepoint 不存在或粒度不夠時才用 kprobe。

## raw_tracepoint：效能更高

`BPF_PROG_TYPE_RAW_TRACEPOINT` 比普通 `TRACEPOINT` 更快，原因在於**不做 context format 的轉換**：

- **普通 tracepoint**：kernel 把 tracepoint 的 raw args 格式化成 `trace_event_raw_*` struct，然後呼叫 BPF program
- **raw_tracepoint**：kernel 直接把 raw args（`unsigned long *args`）傳給 BPF program，不做格式化

```c
/* 普通 tracepoint：格式化 context，有額外 overhead */
SEC("tracepoint/sched/sched_switch")
int trace_switch(struct trace_event_raw_sched_switch *ctx)
{
    /* ctx 已經格式化好，有 prev_comm、next_comm 等 field */
    bpf_printk("%s → %s\n", ctx->prev_comm, ctx->next_comm);
    return 0;
}

/* raw_tracepoint：raw args，效能更好 */
SEC("raw_tracepoint/sched_switch")
int trace_switch_raw(struct bpf_raw_tracepoint_args *ctx)
{
    /* ctx->args 是 raw 的 tracepoint arguments */
    /* sched_switch 的 args: preempt, prev_task, next_task */
    struct task_struct *prev = (struct task_struct *)ctx->args[1];
    struct task_struct *next = (struct task_struct *)ctx->args[2];

    bpf_printk("%s → %s\n", prev->comm, next->comm);
    return 0;
}
```

**raw_tracepoint 的 args 對應**：要查詢某個 tracepoint 的 raw args 順序，看 kernel source 的 `TRACE_EVENT` 定義，或用 BTF：

```bash
# 用 bpftrace 查看 raw tracepoint args
sudo bpftrace -lv 'rawtracepoint:sched_switch'
```

## 使用 libbpf 的 args struct（更型別安全）

對於 syscall tracepoints，libbpf 有一種更型別安全的方式（不用 `ctx->args[N]`）：

```c
/* 用 __attribute__((btf_type_tag("tracepoint"))) 風格 */
/* 這讓你直接用 struct 名稱存取 field，而不是 args[N] */

/* 注意：這需要 vmlinux.h 裡有對應的 struct 定義 */
struct trace_event_raw_sys_enter__openat {
    /* 由 vmlinux.h 定義，field 和 format 檔案對應 */
};
```

實際上在現代 libbpf，直接用 `struct trace_event_raw_sys_enter` 和 `ctx->args` 是最通用的方式。

## 重要的 Tracepoint 類別

| Category | 說明 | 常用 tracepoint |
|---|---|---|
| `syscalls` | 所有 syscall 的進入和退出 | `sys_enter_*`, `sys_exit_*` |
| `sched` | scheduler 事件 | `sched_switch`, `sched_process_fork`, `sched_process_exec` |
| `block` | block I/O 事件 | `block_rq_insert`, `block_rq_complete` |
| `net` | 網路事件 | `net_dev_xmit`, `netif_receive_skb` |
| `kmem` | 記憶體分配 | `kmalloc`, `kfree`, `mm_page_alloc` |
| `irq` | interrupt events | `irq_handler_entry`, `softirq_entry` |
| `ext4` / `xfs` / ... | 檔案系統事件 | `ext4_read_folio`, `xfs_file_write_iter` |
| `raw_syscalls` | 所有 syscall（不按名稱） | `sys_enter`, `sys_exit` |

## 踩雷集錦

1. **Tracepoint 的 context struct 名稱不直覺**：不是 `struct sys_enter_openat`，而是 `struct trace_event_raw_sys_enter`（對所有 syscall enter 共用）；`ctx->args[N]` 才是具體 syscall 的參數，對應方式看 format 文件

2. **`raw_tracepoint` 的 args 是 `unsigned long`，不是型別化的**：讀出來要自己轉型；錯誤的轉型（signed/unsigned 混淆）會導致靜默錯誤

3. **Tracepoint ID 不是固定的**：format 文件裡的 `ID` 欄位（用於 perf_event_open）在每次 boot 後可能改變；不要 hardcode ID，永遠用名稱（libbpf 幫你做這件事）

4. **不是所有 kernel 都有所有 tracepoint**：某些 tracepoint 只在特定 kernel config（例如 `CONFIG_SCHEDSTATS=y`）下存在；attach 之前先確認

5. **普通 tracepoint 的 `ctx->args[N]` 和 raw_tracepoint 的 `ctx->args[N]` 指向不同的東西**：前者是 syscall 參數；後者是 tracepoint 的 raw args（可能是 struct pointer，如 `sched_switch` 的 `prev_task`）

## 動手練習

1. 用 bpftrace 查看 `sched:sched_process_exit` tracepoint 的格式，然後寫一個 BPF 程式追蹤每個 process 退出時的 exit_code

2. 比較 `tracepoint/sched/sched_switch` 和 `raw_tracepoint/sched_switch` 兩種方式存取 `prev` 和 `next` task 的 comm；哪種更直觀？哪種效能更好？

3. 找出 `block` category 下的 tracepoints，寫一個測量 block I/O 完成延遲的程式

## 本章重點整理

- Tracepoint 是靜態 hook，未啟用時幾乎零 overhead；ABI 穩定（欄位不會消失）
- 用 `format` 文件了解 tracepoint 的欄位；用 `ctx->args[N]` 存取具體 syscall 參數
- raw_tracepoint 不做 context 格式化，效能更好，但需要自己轉型 args
- 選 tracepoint 優先，沒有對應 tracepoint 再考慮 kprobe 或 fentry

## 自我檢核

- [ ] 能說出 tracepoint 未啟用時為什麼 overhead 幾乎是零
- [ ] 知道如何用 format 文件確認某個 tracepoint 的 context struct 欄位
- [ ] 能解釋 raw_tracepoint 為什麼比普通 tracepoint 效能更好
- [ ] 知道 tracepoint ABI 穩定性的承諾和限制

→ [Ch 21 fentry / fexit：BTF-based hooks](./21-fentry-fexit.md)
