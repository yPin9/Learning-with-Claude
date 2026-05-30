# Ch 21 — fentry / fexit：BTF-based hooks

> **目標**：理解 fentry/fexit 的設計——為什麼它比 kprobe 效能更好、如何用 `BPF_PROG` macro 存取型別安全的函式參數、fexit 能同時看 input 和 output 的機制。

## 為什麼 fentry/fexit 優於 kprobe

kprobe 用 `int3` breakpoint 做動態 instrumentation，每次觸發需要 exception handler 的進入/退出。fentry/fexit（kernel 5.5+）用**BPF trampoline**：在 kernel 函式的 prologue 插入一個跳轉指令，直接呼叫 BPF program，不需要 exception。

```
kprobe overhead：
  函式呼叫 → int3 exception → kernel exception handler → BPF program
  → 恢復執行（約 100-300 ns per hit）

fentry overhead：
  函式呼叫 → BPF trampoline（call BPF program）→ 繼續執行
  （約 10-30 ns per hit，接近 tracepoint）
```

此外，fentry 基於 BTF，函式參數型別直接從 kernel BTF 推導，不需要手寫 `PT_REGS_PARM*`。

## fentry 的使用

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

/*
 * BPF_PROG macro：從 BTF 推導 vfs_read 的參數型別
 * vfs_read(struct file *file, char __user *buf, size_t count, loff_t *pos)
 */
SEC("fentry/vfs_read")
int BPF_PROG(trace_vfs_read_entry,
             struct file *file,       /* 第 1 個參數 */
             char __user *buf,        /* 第 2 個參數 */
             size_t count,            /* 第 3 個參數 */
             loff_t *pos)             /* 第 4 個參數 */
{
    bpf_printk("vfs_read: count=%zu\n", count);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

比 kprobe 的對比：

```c
/* kprobe：需要 PT_REGS_PARM*，arch-specific */
SEC("kprobe/vfs_read")
int kprobe_read(struct pt_regs *ctx)
{
    size_t count = (size_t)PT_REGS_PARM3(ctx);  /* 需要知道是第幾個 */
    /* ... */
}

/* fentry：直接用函式原型，型別安全 */
SEC("fentry/vfs_read")
int BPF_PROG(fentry_read, struct file *file, char *buf, size_t count, loff_t *pos)
{
    /* 直接用 count，型別是 size_t */
}
```

## fexit：同時看 input 和 output

fexit 是 fentry 的出口版，獨有的特性：**同時能看到函式的所有輸入參數和回傳值**。

```c
/*
 * fexit：參數 = 原函式的所有參數 + 最後加一個回傳值
 * vfs_read 的回傳型別是 ssize_t
 */
SEC("fexit/vfs_read")
int BPF_PROG(trace_vfs_read_exit,
             struct file *file,       /* 原本的參數（和 fentry 一樣）*/
             char __user *buf,
             size_t count,
             loff_t *pos,
             ssize_t ret)             /* 最後加回傳值（fexit 獨有）*/
{
    if (ret < 0)
        bpf_printk("vfs_read error: %ld\n", ret);
    else
        bpf_printk("vfs_read: req=%zu, got=%ld\n", count, ret);
    return 0;
}
```

這比 kprobe + kretprobe 配對更方便，不需要 tid-based map。

## `BPF_PROG` macro 的展開

`BPF_PROG(name, args...)` 展開成（簡化版）：

```c
/* 展開後大概是這樣 */
int name(unsigned long long *ctx)
{
    /* 從 ctx 數組讀取參數（BTF relocation 幫你計算 offset）*/
    struct file *file = (struct file *)ctx[0];
    char *buf         = (char *)ctx[1];
    size_t count      = (size_t)ctx[2];
    loff_t *pos       = (loff_t *)ctx[3];
    /* fexit 還有 */
    ssize_t ret       = (ssize_t)ctx[4];

    /* 你寫的 body */
    bpf_printk("vfs_read: count=%zu\n", count);
    return 0;
}
```

實際的 `BPF_PROG` 用 CO-RE relocation 讓參數的 offset 在 load time 根據 BTF 計算，不是 hardcoded。

## fentry/fexit 的限制

1. **需要 kernel 5.5+**（`BPF_TRACE_FENTRY / FEXIT` program type）
2. **目標函式必須在 kernel BTF 裡有型別資訊**：大部分 non-static 函式都在 BTF；static helper 可能沒有
3. **某些函式無法 fentry**：最佳化掉的函式、`__init` 函式（已卸載）、kernel 某些早期 boot 函式
4. **和 kprobe 的語意差異**：fentry 在函式 prologue（保存 callee-saved registers 之前）觸發，而 kprobe 在第一條指令（通常也是 prologue）；大部分情況下無差，但極少數情況下 frame pointer 的狀態不同

## 和 kprobe 的選擇

| 面向 | kprobe | fentry/fexit |
|---|---|---|
| Kernel 版本 | 4.x+ | 5.5+ |
| 效能 | ~200 ns | ~20 ns |
| 型別安全 | 否（PT_REGS_PARM）| 是（BTF）|
| 同時看 in/out | 否（需配對）| 是（fexit）|
| 適用範圍 | 幾乎所有函式 | BTF 有資訊的函式 |
| Arch 相容 | 需要 arch-specific macro | 不需要 |

**結論**：在 kernel 5.5+ 上，優先選 fentry/fexit；在舊 kernel 或需要存取 BTF 沒有的函式時，用 kprobe。

## 動手練習

1. 把 Ch 19 的 kprobe latency 測量（vfs_read 的 kprobe + kretprobe 配對）改用 fexit 版本；比較程式碼的複雜度和執行的 overhead（用 `bpftool prog show` 的 `run_time_ns / run_cnt`）

2. 用 fentry/fexit 追蹤 `do_sys_openat2`（或 `__x64_sys_openat`），同時輸出 filename（input）和 fd（output）；驗證兩個資料來自同一個 syscall 呼叫

## 本章重點整理

- fentry/fexit 用 BPF trampoline 替代 kprobe 的 int3，overhead 約少 10x
- `BPF_PROG` macro 用 BTF 推導函式參數型別，不需要 `PT_REGS_PARM*`
- fexit 獨有：同時能看所有輸入參數和回傳值，不需要 kprobe/kretprobe 配對
- 優先選 fentry（5.5+），在舊 kernel 或函式沒有 BTF 時才退回 kprobe

## 自我檢核

- [ ] 能解釋 fentry 比 kprobe 快的原因（BPF trampoline vs int3 exception）
- [ ] 知道 `BPF_PROG` macro 做了什麼（BTF-based 參數存取）
- [ ] 能說出 fexit 比 kretprobe 方便的具體原因（不需要 tid-based 配對）
- [ ] 知道什麼情況下 fentry 不適用，需要退回 kprobe

→ [Ch 22 USDT：userspace 靜態探針](./22-usdt.md)
