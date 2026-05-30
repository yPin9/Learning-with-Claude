# Ch 19 — kprobes / kretprobes

> **目標**：深入理解 kprobe 和 kretprobe 的底層實作機制——int3 breakpoint 的工作原理、register 狀態的存取、symbol resolution、inline function 的限制——讓你在 attach 失敗或取得錯誤資料時能診斷問題。

## 為什麼需要這個？

Ch 6 已經介紹了 kprobe 的使用方式。這章深挖底層：kprobe 是**動態 instrumentation**——它在執行時修改目標函式的 text 段，插入一個 breakpoint（`int3` 指令），讓你的 BPF 程式能在任意函式的入口和出口執行。

理解這個機制，你才能解釋：為什麼 inline 函式無法 kprobe、為什麼 kprobe 的效能 overhead 比 tracepoint 高、以及為什麼 `PT_REGS_PARM1` 在不同架構上需要不同的 macro。

## 先建立直覺：kprobe 的執行流程

```
正常執行：
  call vfs_read  →  vfs_read 的 prologue  →  函式主體

kprobe 插入後：
  call vfs_read
    ↓
  vfs_read 的第一條指令被替換成 int3（0xCC）
    ↓
  CPU 觸發 breakpoint exception
    ↓
  kernel kprobe handler
    ↓ 保存所有 register 到 struct pt_regs
    ↓
  你的 BPF program（可以讀 pt_regs）
    ↓
  恢復被替換的指令，繼續執行 vfs_read
```

kprobe 的 overhead 主要來自：breakpoint exception 的進入/退出（大約 100–200 ns per hit），以及保存 / 恢復所有 register。

## 存取函式參數

在 kprobe BPF 程式裡，參數透過 `struct pt_regs *` 存取：

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

/*
 * vfs_read 的簽名：
 * ssize_t vfs_read(struct file *file, char __user *buf, size_t count, loff_t *pos)
 *
 * 在 x86-64 上，函式參數放在：
 *   arg1 = rdi, arg2 = rsi, arg3 = rdx, arg4 = rcx
 */
SEC("kprobe/vfs_read")
int trace_vfs_read(struct pt_regs *ctx)
{
    /* 用 macro 存取函式參數（arch-independent）*/
    struct file *filep = (struct file *)PT_REGS_PARM1(ctx);
    size_t count       = (size_t)PT_REGS_PARM3(ctx);

    bpf_printk("vfs_read: count=%zu\n", count);
    return 0;
}
```

**`PT_REGS_PARM*` macro 的展開**（x86-64）：

```c
/* <bpf/bpf_tracing.h> 裡的定義（x86-64）*/
#define PT_REGS_PARM1(x) ((x)->di)   /* rdi */
#define PT_REGS_PARM2(x) ((x)->si)   /* rsi */
#define PT_REGS_PARM3(x) ((x)->dx)   /* rdx */
#define PT_REGS_PARM4(x) ((x)->cx)   /* rcx */
#define PT_REGS_PARM5(x) ((x)->r8)   /* r8 */
#define PT_REGS_RC(x)    ((x)->ax)   /* rax（回傳值）*/
#define PT_REGS_IP(x)    ((x)->ip)   /* rip（instruction pointer）*/
```

注意：超過 6 個參數的函式，第 7 個以後的參數放在 stack，不在 register。

## kretprobe：存取回傳值

kretprobe 在函式返回時觸發：

```c
SEC("kretprobe/vfs_read")
int trace_vfs_read_return(struct pt_regs *ctx)
{
    /* 回傳值在 PT_REGS_RC */
    long ret = (long)PT_REGS_RC(ctx);

    if (ret < 0)
        bpf_printk("vfs_read error: %ld\n", ret);
    else
        bpf_printk("vfs_read read %ld bytes\n", ret);

    return 0;
}
```

**kprobe + kretprobe 的配對（測量 latency）**：

```c
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 10240);
    __type(key, u32);   /* tid */
    __type(value, u64); /* entry timestamp */
} start_ts SEC(".maps");

SEC("kprobe/vfs_read")
int trace_entry(struct pt_regs *ctx)
{
    u32 tid = bpf_get_current_pid_tgid() & 0xFFFFFFFF;
    u64 ts  = bpf_ktime_get_ns();
    bpf_map_update_elem(&start_ts, &tid, &ts, BPF_ANY);
    return 0;
}

SEC("kretprobe/vfs_read")
int trace_return(struct pt_regs *ctx)
{
    u32 tid = bpf_get_current_pid_tgid() & 0xFFFFFFFF;
    u64 *ts = bpf_map_lookup_elem(&start_ts, &tid);
    if (!ts) return 0;

    u64 latency_us = (bpf_ktime_get_ns() - *ts) / 1000;
    bpf_map_delete_elem(&start_ts, &tid);

    bpf_printk("latency=%llu us\n", latency_us);
    return 0;
}
```

## 用 libbpf 精確指定 kprobe

```c
/* libbpf 的 API 有多種 attach 方式 */

/* 方式一：用 SEC() annotation（最簡單）*/
SEC("kprobe/vfs_read")
int my_probe(struct pt_regs *ctx) { ... }
/* bpf_program__attach(prog) 自動 attach */

/* 方式二：明確指定（適合 runtime 決定要 attach 哪個函式）*/
struct bpf_link *link = bpf_program__attach_kprobe(
    prog,
    false,       /* false = kprobe，true = kretprobe */
    "vfs_read"   /* 函式名稱 */
);

/* 方式三：指定 offset（attach 到函式內部某個位置）*/
struct bpf_link *link = bpf_program__attach_kprobe_opts(
    prog, "vfs_read",
    &(struct bpf_kprobe_opts){
        .bpf_cookie = 42,
        .offset     = 0,  /* 函式入口的 offset，0 = 函式開頭 */
    }
);
```

## Symbol Resolution：`/proc/kallsyms`

kprobe 的目標函式名稱在 load time 透過 `/proc/kallsyms` 解析成地址：

```bash
# 查找 vfs_read 的地址
sudo grep "vfs_read" /proc/kallsyms
# ffffffff81234567 T vfs_read
# ffffffff81234abc t __vfs_read   # t = static（小寫），仍然可以 kprobe

# 確認函式是否可以 kprobe
sudo cat /sys/kernel/debug/kprobes/blacklist  # 這些不能 kprobe
```

**Inline function 的問題**：如果一個函式被 compiler 內聯了，它在 `.text` 裡沒有獨立的 symbol，kprobe 無法 attach。判斷方法：

```bash
# 如果 /proc/kallsyms 找不到這個函式名，它可能被 inline 了
sudo grep "^.*T do_my_func" /proc/kallsyms
# 沒有輸出 → inline 了，無法 kprobe
```

解法：找一個呼叫它的上層函式（不被 inline 的）；或改用 tracepoint；或改用 fentry（如果有 BTF 資訊）。

## kprobe 的底層實作細節

Linux kprobe 的實作（`kernel/kprobes.c`）：

1. **Register**：把目標位址的第一條指令（通常 1 byte）替換成 `int3`（0xCC）
2. **Execute**：CPU 執行到 `int3` 時觸發 exception，進入 kprobe handler
3. **Pre-handler**：執行 pre_handler（這裡呼叫你的 BPF program）
4. **Single-step**：把原始指令複製到 scratch area，single-step 執行
5. **Post-handler**：執行 post_handler（kretprobe 在這裡追蹤）
6. **Return**：繼續執行目標函式的下一條指令

**效能數字**（x86-64 上）：
- kprobe hit overhead：約 100–300 ns
- tracepoint hit overhead：約 10–50 ns（靜態 hook，不需要 int3）
- fentry hit overhead：約 10–30 ns（BPF trampoline，更快）

## 踩雷集錦

1. **PT_REGS_PARM* 和 x86-64 calling convention 的對應**：Standard C calling convention（System V AMD64）：`rdi, rsi, rdx, rcx, r8, r9`；這和 `PT_REGS_PARM1–6` 對應。如果函式有 6 個以上的參數，第 7 個以後在 stack（用 `bpf_probe_read_kernel` 讀）

2. **kretprobe 的 tid-based 配對在特定情況下不正確**：如果一個函式可以被遞迴呼叫（或是 syscall 被 signal 中斷），tid-based 的 entry/exit 配對可能錯位；用 struct 存 entry stack 或用 per-thread per-depth 的 map

3. **kprobe 目標函式被 kprobe 自己呼叫**：如果你的 BPF program 觸發了你 kprobe 的函式（例如 `bpf_printk` 最終呼叫了一個有 kprobe 的 write 函式），可能觸發遞迴。kernel 有保護（kprobe reentrancy guard），但還是要注意

4. **NOKPROBE_SYMBOL 標記的函式**：某些 kernel 函式被標記為不能 kprobe（例如 kprobe handler 自己），`/sys/kernel/debug/kprobes/blacklist` 列出這些函式；attach 到 blacklisted 函式會得到 -EINVAL

5. **kernel 5.15+ 的 kprobe multi（`BPF_LINK_TYPE_KPROBE_MULTI`）**：允許一個 BPF program attach 到多個函式，overhead 更低；用 `bpf_program__attach_kprobe_multi_opts()` API

## 動手練習

1. 用 kprobe + kretprobe 配對，測量系統上所有 `__x64_sys_read` 呼叫的延遲，用 `hist()` 顯示 distribution；比較 `bpftrace -e 'kprobe:... / kretprobe:...'` 和 libbpf 兩種方式的差異

2. 嘗試 kprobe 一個你懷疑被 inline 的函式（例如某個 static helper）；確認 `/proc/kallsyms` 找不到它，然後找到它的 non-inline 的上層函式

3. 用 `sudo cat /sys/kernel/debug/kprobes/list` 查看目前系統上所有 active kprobes；說出每個 kprobe 對應的 BPF program

## 本章重點整理

- kprobe 把目標函式第一條指令替換成 `int3`；每次執行到那裡就觸發 exception 並呼叫你的 BPF program
- `PT_REGS_PARM*` macro 讀函式參數；`PT_REGS_RC` 讀回傳值（kretprobe 用）
- Inline 函式沒有 symbol，無法 kprobe；改用 tracepoint 或 fentry
- Kprobe overhead（~200 ns）比 tracepoint（~30 ns）和 fentry（~20 ns）高得多

## 自我檢核

- [ ] 能解釋 kprobe 的底層機制（int3 breakpoint），以及它和 tracepoint 的效能差異
- [ ] 能說出在 x86-64 上 `PT_REGS_PARM1` 到 `PT_REGS_PARM5` 各對應哪個 register
- [ ] 知道如何判斷一個函式是否可以 kprobe，以及 inline 函式的替代方案
- [ ] 理解 kprobe + kretprobe 配對做 latency 測量的 tid-based map 方案的前提假設

## 延伸閱讀

### 官方文件

- **[Linux kernel: kprobes documentation](https://www.kernel.org/doc/html/latest/trace/kprobes.html)**
  - **讀哪裡**：整份；特別是 "How Does a Kprobe Work" 那一節
  - **學什麼**：kprobe 的完整底層實作說明；pre-handler、post-handler、fault-handler 的語意

### 部落格

- **[Linux kprobe internals](https://jvns.ca/blog/2017/07/05/linux-tracing-systems/#kprobes)** — Julia Evans
  - **這篇說什麼**：用圖解方式說明 kprobe 工作原理，非常易懂
  - **讀哪裡**：kprobes 那一節
  - **為什麼值得讀**：Julia Evans 的文章以清晰著稱；這篇是 tracing 系統的好入門

→ [Ch 20 Tracepoints 與 raw_tracepoints](./20-tracepoints-raw-tracepoints.md)
