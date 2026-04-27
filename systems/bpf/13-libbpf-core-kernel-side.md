# Ch 13 — libbpf + CO-RE 入門（kernel side C）

> 目標：學會用 clang 編出 CO-RE-ready 的 .bpf.o — 引入 vmlinux.h、用 SEC()、宣告 maps、用 BPF_CORE_READ 讀 kernel struct、寫 ringbuf event。本章只談 kernel 那一側，user space loader 留到 Ch 14。

## 開發環境

確認 Ch 0 裝的東西就位：

```bash
clang --version          # >= 12
ls /sys/kernel/btf/vmlinux
pkg-config --libs libbpf # 應該回傳 -lbpf
```

建工作目錄：

```bash
mkdir -p ~/bpf-workspace/minimal
cd ~/bpf-workspace/minimal
sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c > vmlinux.h
```

`vmlinux.h` 是後面所有 BPF C 都會 include 的「kernel 全套型別」標頭。

## 最小 BPF 程式

寫 `minimal.bpf.c`：

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

SEC("kprobe/do_sys_openat2")
int BPF_KPROBE(do_openat2, int dfd, const char *filename, struct open_how *how)
{
    pid_t pid = bpf_get_current_pid_tgid() >> 32;
    bpf_printk("openat: pid=%d filename=%s\n", pid, filename);
    return 0;
}
```

編譯：

```bash
clang -O2 -g -Wall -target bpf \
    -D__TARGET_ARCH_x86 \
    -I. \
    -c minimal.bpf.c -o minimal.bpf.o
```

關鍵 flag：

| Flag | 作用 |
|---|---|
| `-target bpf` | 產生 BPF bytecode 而非 native |
| `-O2` | verifier 喜歡優化過的 code，O0 容易爆指令數上限 |
| `-g` | **必加** — 嵌入 BTF 與 CO-RE relocation metadata |
| `-D__TARGET_ARCH_x86` | 給 `BPF_KPROBE` macro 用，告訴它從哪些 register 拿參數 |

驗證 .bpf.o 有 BTF：

```bash
sudo bpftool btf dump file minimal.bpf.o | head
# 應該看到 type 定義
```

## 解析這支程式的每一行

### License 宣告

```c
char LICENSE[] SEC("license") = "Dual BSD/GPL";
```

**沒這行 verifier 會拒**。BPF 要求宣告 license — GPL 才能用全部 helper（部分 helper 標 `gpl_only`）。`SEC("license")` 把這個變數放到特殊 ELF section，libbpf 載入時會讀。

### Section 標記

```c
SEC("kprobe/do_sys_openat2")
```

告訴 libbpf：「這個 function 是 kprobe program type，attach 到 `do_sys_openat2`」。前面 Ch 7 講過完整 SEC 寫法表。

### BPF_KPROBE macro

```c
int BPF_KPROBE(do_openat2, int dfd, const char *filename, struct open_how *how)
```

`BPF_KPROBE` 展開成：

```c
int do_openat2(struct pt_regs *ctx)
{
    int dfd = (int)PT_REGS_PARM1(ctx);
    const char *filename = (const char *)PT_REGS_PARM2(ctx);
    struct open_how *how = (struct open_how *)PT_REGS_PARM3(ctx);
    /* ... 原本的 function body ... */
}
```

**幫你把 register 解出來**。否則你要自己寫 `PT_REGS_PARM1(ctx)` 等等 — 醜且容易錯。

### bpf_printk

```c
bpf_printk("openat: pid=%d filename=%s\n", pid, filename);
```

最簡單的 debug 工具。它會把訊息寫到 `/sys/kernel/tracing/trace_pipe`：

```bash
sudo cat /sys/kernel/tracing/trace_pipe
# 載入後就會看到輸出
```

**bpf_printk 是 debug 用，不是 production 用** — 全域共享 buffer、會丟訊息、開銷大。生產級要用 ringbuf（下一節）。

## Maps 宣告（生產寫法）

把 minimal 改成「累計每個 PID 的 openat 次數」：

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 10240);
    __type(key, pid_t);
    __type(value, u64);
} open_counts SEC(".maps");

SEC("kprobe/do_sys_openat2")
int BPF_KPROBE(do_openat2)
{
    pid_t pid = bpf_get_current_pid_tgid() >> 32;
    u64 *count, init = 1;

    count = bpf_map_lookup_elem(&open_counts, &pid);
    if (count) {
        __sync_fetch_and_add(count, 1);
    } else {
        bpf_map_update_elem(&open_counts, &pid, &init, BPF_NOEXIST);
    }
    return 0;
}
```

**這就是現代 libbpf+CO-RE 寫 BPF 的標準形 form**：

- `struct { __uint(...) ... } name SEC(".maps");` 宣告 map
- `bpf_map_lookup_elem` / `bpf_map_update_elem` 操作
- `__sync_fetch_and_add` 原子加（多 CPU 安全）

## 用 Ringbuf 上報 event

`bpf_printk` 不適合 production。Ringbuf 才是上報 event 給 user space 的正確方式：

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

struct event {
    pid_t pid;
    char comm[16];
    char filename[128];
};

struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024);
} events SEC(".maps");

SEC("kprobe/do_sys_openat2")
int BPF_KPROBE(do_openat2, int dfd, const char *filename)
{
    struct event *e;

    e = bpf_ringbuf_reserve(&events, sizeof(*e), 0);
    if (!e) return 0;

    e->pid = bpf_get_current_pid_tgid() >> 32;
    bpf_get_current_comm(&e->comm, sizeof(e->comm));
    bpf_probe_read_user_str(&e->filename, sizeof(e->filename), filename);

    bpf_ringbuf_submit(e, 0);
    return 0;
}
```

`bpf_ringbuf_reserve` → 填欄位 → `bpf_ringbuf_submit`。**user space 那邊用 epoll 等 ringbuf 有資料就消費**（Ch 14 寫）。

## 用 BPF_CORE_READ 跨版本讀 kernel struct

直接從 task_struct 拿 parent 的 PID，跨 kernel 版本：

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

SEC("kprobe/do_sys_openat2")
int BPF_KPROBE(do_openat2)
{
    struct task_struct *task = (struct task_struct *)bpf_get_current_task();
    pid_t pid = BPF_CORE_READ(task, pid);
    pid_t ppid = BPF_CORE_READ(task, real_parent, pid);   // 多層也 OK

    bpf_printk("pid=%d ppid=%d\n", pid, ppid);
    return 0;
}
```

`BPF_CORE_READ(task, real_parent, pid)` 等同：

```c
struct task_struct *parent;
pid_t ppid;
parent = BPF_CORE_READ(task, real_parent);
bpf_probe_read_kernel(&ppid, sizeof(ppid), &parent->pid);
```

但更乾淨且**自動處理 CO-RE relocation**。

## 跨版本欄位 fallback

`task->state` 在 5.14 後改名 `__state`：

```c
unsigned int get_state(struct task_struct *t)
{
    if (bpf_core_field_exists(t->__state))
        return BPF_CORE_READ(t, __state);
    else
        return BPF_CORE_READ(t, state);
}
```

`bpf_core_field_exists` 是 compile-time check — 載入時 libbpf 會把它換成 0 或 1，verifier 看到 dead branch 直接砍。

## 完整 build pipeline

把上面寫的東西包成可以重複用的 Makefile：

```makefile
CLANG ?= clang
ARCH ?= x86

CFLAGS = -O2 -g -Wall \
         -target bpf \
         -D__TARGET_ARCH_$(ARCH) \
         -I.

%.bpf.o: %.bpf.c vmlinux.h
	$(CLANG) $(CFLAGS) -c $< -o $@

vmlinux.h:
	sudo bpftool btf dump file /sys/kernel/btf/vmlinux format c > $@

clean:
	rm -f *.bpf.o vmlinux.h
```

```bash
make minimal.bpf.o
sudo bpftool prog load minimal.bpf.o /sys/fs/bpf/minimal autoattach
sudo bpftool prog list | grep minimal
```

`autoattach` 讓 bpftool 自動處理 SEC 對應的 attach。pin 到 `/sys/fs/bpf/` 後 program 就常駐 — 不用 user space loader 也活著。

## 一個常見誤解

「我寫的 BPF C 跟一般 C 一樣」 — **錯誤**。

幾個關鍵差異：

- **沒 stdlib**：不能 `printf`、不能 `malloc`、不能 `memcpy`（用 `__builtin_memcpy`）
- **不能用 global 變數的方式**（早期）：5.2+ 才支援，且底層仍是 1-entry array map
- **stack 限制 512 byte**
- **不能解參考 kernel pointer**：要用 `bpf_probe_read_kernel` 或 `BPF_CORE_READ`
- **string 處理痛苦**：沒 strcmp，要 `bpf_strncmp` 或自寫 unrolled loop
- **整數除法限制**：早期不支援，現在有限度支援

學 BPF C 要把它當「另一種語言」，不是「kernel 環境的 C」。

## 動手練習

1. **build 出 minimal.bpf.o** — 上面的 minimal version。
2. **load + 觀察**：
   ```bash
   sudo bpftool prog load minimal.bpf.o /sys/fs/bpf/minimal autoattach
   sudo cat /sys/kernel/tracing/trace_pipe   # 看 bpf_printk 輸出
   ```
3. **故意忘記 LICENSE**：把 `char LICENSE[] ...` 那行刪掉，重新 build + load — 看 verifier 怎麼罵。
4. **故意忘記 NULL check**：把 `bpf_map_lookup_elem` 後直接解參考、不檢查 — 看 verifier log。
5. **加上 ringbuf**：把 minimal 改成上面的 ringbuf 版（**不需要 user space**，先用 `sudo bpftool map dump` 看內容）。
6. **clone libbpf-bootstrap**：
   ```bash
   git clone --recurse-submodules https://github.com/libbpf/libbpf-bootstrap
   cd libbpf-bootstrap/examples/c
   make
   sudo ./minimal
   ```
   讀 `minimal.bpf.c` 跟 `minimal.c`。

## 自我檢核

- [ ] 我能解釋 SEC() 的作用、為什麼 LICENSE 必須宣告
- [ ] 我能用 BPF_KPROBE / BPF_KRETPROBE macro 寫 probe handler
- [ ] 我能宣告 hash map 跟 ringbuf
- [ ] 我能用 BPF_CORE_READ 讀多層 kernel struct
- [ ] 我能用 bpf_core_field_exists 寫跨版本 fallback
- [ ] 我知道為什麼 -g 不能省

下一章我們補上另一半 — user space loader。學 libbpf 的 skeleton API、ringbuf polling、map 操作，把 .bpf.o 變成可以從 cmdline 跑的完整工具。

→ [Ch 14 User space loader：用 C 寫 loader](./14-userspace-loader-c.md)
