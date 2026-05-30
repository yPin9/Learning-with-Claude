# Ch 10 — CO-RE：Compile Once Run Everywhere

> **目標**：理解 CO-RE（Compile Once Run Everywhere）如何讓一個 BPF 二進位在不同 kernel 版本上正確執行——BTF relocation 的運作機制、`BPF_CORE_READ` 系列 macro 的用法、以及如何處理 kernel 版本差異。

> 如果你對 BTF 還不熟，先讀 [Ch 9 BTF 深入](./09-btf-deep-dive.md)。CO-RE 建立在 BTF 之上。

## 為什麼需要 CO-RE？

在 CO-RE 之前，eBPF 有一個根本的可攜性問題。

假設你在 Ubuntu 22.04（kernel 5.15）上寫了一個追蹤 `task_struct.comm` 的 BPF 程式：

```c
/* 錯誤：hardcode struct field offset */
char *comm_ptr = (char *)task + 3200;  /* 在 5.15 上 comm 在 offset 3200 */
```

這在 kernel 5.15 上跑得很好。但裝到 kernel 6.1 的機器上，`task_struct` 的 layout 可能改變了，`comm` 可能跑到 offset 3216，你的程式就讀到錯誤的位置。

即使你用 BTF-typed 的存取：

```c
struct task_struct *task = bpf_get_current_task();
char *comm = task->comm;  /* clang 會把這個編譯成 offset 3200 */
```

Clang 在編譯時就把 `task->comm` 的 offset 算死在 bytecode 裡，到不同 kernel 上一樣是錯的。

傳統解法是在 target machine 上重新編譯——這需要 kernel headers 和工具鏈，不適合部署 BPF 工具到客戶的生產環境。

CO-RE 的解法：**在編譯時只記錄「你想存取的 field 的名稱」，在 load time 用目標 kernel 的 BTF 計算正確的 offset**。

## 先建立直覺：編譯時記錄意圖，load time 解析 offset

```
傳統方式：
  編譯機 kernel BTF ──▶ task->comm offset = 3200
  編譯成 bytecode: *(char *)(r1 + 3200)
  載入到 kernel 5.15: 正確（offset 3200）
  載入到 kernel 6.1:  錯誤（offset 變了）

CO-RE 方式：
  編譯時：
    BPF_CORE_READ(task, comm)
    ──▶ bytecode: *(char *)(r1 + 3200)（以編譯機的 offset 為初始值）
    ──▶ .BTF.ext: relocation record {
         "我想存取 struct task_struct 的 comm field"
         "目前 bytecode 裡的 offset 是 3200"
       }

  Load time（在 kernel 6.1 上）：
    libbpf 讀 kernel BTF → comm 的 offset 是 3216
    修改 bytecode: *(char *)(r1 + 3200) → *(char *)(r1 + 3216)
    載入到 kernel: 正確！
```

## CO-RE 的三個組成部分

1. **Clang 支援**：當你用 `BPF_CORE_READ()` 或直接做 struct field access，clang 生成一個 `.BTF.ext` 裡的 relocation record，記錄你存取了哪個 struct 的哪個 field

2. **Kernel BTF**：`/sys/kernel/btf/vmlinux` 提供目標 kernel 的 struct layout

3. **libbpf 的 relocation engine**：在 load time，libbpf 讀 relocation records，查 kernel BTF，計算正確的 offset，修改 bytecode

## vmlinux.h：不用 include kernel headers

CO-RE 讓你不需要 `<linux/sched.h>` 等 kernel headers。取而代之，用從 kernel BTF 生成的 `vmlinux.h`：

```bash
# 生成 vmlinux.h
sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c > vmlinux.h
```

`vmlinux.h` 包含所有 kernel struct、union、enum、typedef 的定義，可以在任何 BPF 程式裡直接 include：

```c
/* 不需要這些 */
/* #include <linux/sched.h> */
/* #include <linux/fs.h> */
/* #include <linux/skbuff.h> */

/* 只需要這一個 */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>

SEC("kprobe/vfs_read")
int trace_vfs_read(struct pt_regs *ctx)
{
    /* task_struct 的定義來自 vmlinux.h */
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();
    /* ... */
    return 0;
}
```

> **注意**：vmlinux.h 裡的 struct 定義是編譯機 kernel 的 layout，用於 type checking；CO-RE relocation 在 load time 修正 offset，讓程式能在其他 kernel 上執行。

## BPF_CORE_READ：CO-RE 的主要 API

`BPF_CORE_READ(type, field)` macro 展開成一個帶有 CO-RE relocation record 的 field access：

```c
#include "vmlinux.h"
#include <bpf/bpf_core_read.h>

SEC("kprobe/vfs_read")
int trace_read(struct pt_regs *ctx)
{
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();

    /* BPF_CORE_READ：帶 CO-RE relocation 的 field access */
    pid_t pid = BPF_CORE_READ(task, pid);
    pid_t tgid = BPF_CORE_READ(task, tgid);

    /* 嵌套 field access（自動展開成多層 read）*/
    const unsigned char *filename = BPF_CORE_READ(task, mm, exe_file, f_path.dentry, d_name.name);

    bpf_printk("pid=%d tgid=%d\n", pid, tgid);
    return 0;
}
```

`BPF_CORE_READ` 也有 `_STR` 版本（讀字串）、`_USER` 版本（讀 userspace 記憶體）：

```c
/* 讀 userspace 記憶體 */
BPF_CORE_READ_USER(task, some_user_ptr);

/* 讀 userspace 字串 */
char buf[64];
BPF_CORE_READ_STR_INTO(&buf, task, comm);
```

## 直接 field access（btf_ptr）

如果你直接對 struct pointer 做 field access（不用 `BPF_CORE_READ`），clang 也會生成 CO-RE relocation——但前提是 struct 是 BTF-typed 的：

```c
/* 這樣寫也有 CO-RE relocation（clang 自動生成）*/
struct task_struct *task = (struct task_struct *)bpf_get_current_task();
pid_t pid = task->pid;  /* clang 會生成 relocation record */

/* 這樣寫沒有 CO-RE（raw offset，不安全）*/
pid_t *pid_ptr = (pid_t *)((char *)task + 4);  /* hardcoded offset！*/
pid_t pid = *pid_ptr;  /* 無法 relocate */
```

**關鍵規則**：只要 pointer 的型別是 BTF-typed struct（來自 vmlinux.h 或程式的 BTF），直接的 field access 就有 CO-RE；轉換成 raw pointer 之後就沒有。

## CO-RE Relocation Types

CO-RE 支援多種 relocation 型別：

**Field offset relocation**（最常見）：

```c
/* ACCESS: struct task_struct 的 pid 在哪 */
pid_t pid = task->pid;
```

**Field existence check**（處理 kernel 版本差異加了/刪了 field）：

```c
/* 如果這個 field 存在，存取它；否則用預設值 */
if (bpf_core_field_exists(task->exit_code)) {
    int code = BPF_CORE_READ(task, exit_code);
    bpf_printk("exit_code=%d\n", code);
}
```

**Field size relocation**（某個 field 在新 kernel 換了大小）：

```c
/* 取得 field 的實際大小（runtime）*/
size_t sz = bpf_core_field_size(task->pid);
```

**Type existence check**（某個 struct 在新 kernel 出現了）：

```c
/* 如果 struct xyz 存在（kernel 版本夠新）*/
if (bpf_core_type_exists(struct new_kernel_struct)) {
    /* 使用新功能 */
}
```

**Enum value existence / value relocation**：

```c
/* 某個 enum 值在新 kernel 裡改了值 */
int val = bpf_core_enum_value(enum_type, SOME_ENUM_VALUE);
```

## 處理 Kernel 版本差異：條件存取

當某個 field 在舊 kernel 不存在時，你需要處理：

```c
/* 方法一：field existence check + 條件 */
struct task_struct *task = (struct task_struct *)bpf_get_current_task();
if (bpf_core_field_exists(task->exit_code)) {
    /* kernel >= 5.x 有這個 field */
    int code = BPF_CORE_READ(task, exit_code);
} else {
    /* 舊 kernel 沒有這個 field */
    int code = 0;  /* 預設值 */
}

/* 方法二：volatile 變數讓 verifier 不做 dead-code elimination */
/* （舊 kernel 上這個 branch 的 code 會被 verifier 標記為 unreachable，
     但還是要通過 verifier，所以要用 volatile）*/
extern volatile int MY_KERNEL_HAS_FEATURE __kconfig;
if (MY_KERNEL_HAS_FEATURE) {
    /* 只在新 kernel 上執行 */
}
```

## CO-RE 實際範例：追蹤 process 的 mount namespace

`task_struct` 裡的 mount namespace 在不同 kernel 版本可能有不同路徑：

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>

SEC("tracepoint/syscalls/sys_enter_execve")
int trace_exec_mnt_ns(struct trace_event_raw_sys_enter *ctx)
{
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();

    /*
     * mount namespace inode 的存取路徑（跨版本）：
     * task->nsproxy->mnt_ns->ns.inum
     *
     * BPF_CORE_READ 處理每個 dereference 的 relocation
     */
    unsigned int mnt_ns_inum = BPF_CORE_READ(task,
                                              nsproxy,
                                              mnt_ns,
                                              ns.inum);

    bpf_printk("pid=%d mnt_ns=%u\n",
               BPF_CORE_READ(task, pid),
               mnt_ns_inum);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

## 踩雷集錦

1. **用了 BPF_CORE_READ 但沒有 vmlinux.h**：`BPF_CORE_READ` 需要 struct 的 BTF 定義；如果你只有 `<linux/sched.h>` 而沒有 `vmlinux.h`，CO-RE relocation 可能不完整

2. **Cast 到 void* 後失去 CO-RE**：`(void *)task` 之後再做 field access，clang 不知道型別，不生成 relocation。永遠保留 struct pointer 的型別

3. **CO-RE 不能處理完全不同的 struct layout**：如果 struct 被重新設計（不只是 field 移位，而是 field 消失或換型別），CO-RE 會失敗。用 `bpf_core_field_exists` 做 fallback

4. **vmlinux.h 太大導致編譯慢**：vmlinux.h 通常 50000+ 行，每次編譯都要 parse。可以只 include 需要的 struct（從 vmlinux.h 提取），或用 precompiled headers

5. **`bpf_core_field_exists` 的結果只有在 load time 才確定**：在編譯時你不知道 field 是否存在；verifier 會根據 target kernel 的 BTF 做 dead code elimination，通不過 existence check 的分支會被刪掉

## 進階：`__kconfig` 存取 kernel config

除了 struct field 的 relocation，CO-RE 也支援讀取 kernel config 值：

```c
/* 宣告 kernel config 變數 */
extern int CONFIG_HZ __kconfig;
extern int CONFIG_PREEMPT __kconfig;

SEC("kprobe/schedule")
int check_hz(void *ctx)
{
    bpf_printk("CONFIG_HZ = %d\n", CONFIG_HZ);
    return 0;
}
```

libbpf 在 load time 從 `/boot/config-$(uname -r)` 或 `/proc/config.gz` 讀取 kernel config 值，填入 BPF 程式。

## 動手練習

1. 寫一個 BPF 程式，用 `BPF_CORE_READ` 讀取 `task_struct.comm`（process 名稱）和 `task_struct.mm->start_brk`（heap 起始位址），並用 bpftrace 的 `bpftool prog dump xlated` 查看 CO-RE relocation 是否出現在 bytecode 的 comment 裡

2. 在 vmlinux.h 裡找 `struct sk_buff` 的 `data` field，用 `BPF_CORE_READ` 存取它，確認 load 成功（即使 kernel 版本不同）

3. 用 `bpf_core_field_exists` 做 capability check：如果 `task_struct.pid_links` 存在，印出它的值；否則印出 "field not available"

4. 故意不用 `BPF_CORE_READ`，直接用 hardcoded offset（`*(pid_t *)((char *)task + 4)`）存取 pid，然後在不同 kernel 版本（如果你有的話，或用 VM）上跑，觀察兩種方式的結果差異

## 本章重點整理

- CO-RE 讓你的 BPF binary 在不同 kernel 版本上正確執行，不需要在目標機器上重新編譯
- 機制：clang 在 `.BTF.ext` 裡記錄 relocation，libbpf 在 load time 用 target kernel BTF 計算正確 offset
- `BPF_CORE_READ` 和直接的 BTF-typed struct access 都會生成 CO-RE relocation；cast 到 void* 後就沒有
- `bpf_core_field_exists / type_exists / enum_value` 讓你處理不同 kernel 版本有不同 field/type 的情況

## 自我檢核

- [ ] 能解釋 CO-RE 解決了什麼問題，以及「relocation」在這裡的意思
- [ ] 知道 `.BTF.ext` 裡的 relocation record 包含哪些資訊
- [ ] 能說出 `BPF_CORE_READ(task, pid)` 和 `task->pid` 在有沒有 CO-RE 支援上的差別（提示：都有，只要 task 是 BTF-typed）
- [ ] 知道在哪些情況下 CO-RE 無法幫你處理版本差異（field 消失）

## 延伸閱讀

### 部落格

- **[BPF CO-RE reference guide](https://nakryiko.com/posts/bpf-core-reference-guide/)** — Andrii Nakryiko
  - **這篇說什麼**：CO-RE 的完整指南；BPF_CORE_READ 的所有變體、relocation types、kernel config、feature detection
  - **讀哪裡**：整篇；這是必讀的參考文件
  - **為什麼值得讀**：作者是 CO-RE 的設計者和 libbpf 的主要維護者；這是最準確的技術規格

- **[Portability and CO-RE](https://ebpf.io/blog/bpf-portability-and-co-re/)** — Andrii Nakryiko, eBPF.io, 2020
  - **這篇說什麼**：從 problem statement 出發，說明為什麼需要 CO-RE，以及它的設計原則
  - **讀哪裡**：整篇；特別是 "Before CO-RE" 那一節
  - **為什麼值得讀**：對照本章，給你更多歷史背景

### 官方文件

- **[libbpf: BPF CO-RE](https://libbpf.readthedocs.io/en/latest/bpf_core_read.html)**
  - **讀哪裡**：整頁；macro 和 helper function 的完整列表
  - **學什麼**：所有 CO-RE API 的完整說明；作為參考文件查閱

→ [Ch 11 Helper Functions 系統](./11-helper-functions.md)
