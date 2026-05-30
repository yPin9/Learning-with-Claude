# Ch 6 — Program Types 完整解析

> **目標**：理解 eBPF 的所有主要 program type——每種類型的 attach point 在哪、context 結構是什麼、能存取什麼資料、回傳值的語意是什麼——讓你選擇 program type 時不再靠猜測。

## 為什麼需要這個？

eBPF 的 program type 不是隨意分類的——每種 type 對應一個不同的 kernel 執行路徑，有不同的 context（傳進來的 `struct *ctx`），允許不同的 helper set，回傳值有不同的語意。

選錯了 program type，你拿不到你要的資料；選對了，你能看到 kernel 執行路徑的完整細節。這章系統性地過一遍所有重要的 type。

> 如果你對 BPF program 的載入機制（`bpf()` syscall 的 `BPF_PROG_LOAD`）還不熟，先讀完本章再看 [Ch 12 BPF syscall 底層序列](./12-bpf-syscall-internals.md)。

## 先建立直覺：Program Type = Hook 的位置 + 資料格式

```
kernel 執行路徑（簡化）

userspace syscall
  │
  ▼ sys_enter
  ├── [TRACEPOINT/syscalls/sys_enter_*]
  │     context: struct trace_event_raw_sys_enter
  │
  ▼ kernel function execution
  ├── [KPROBE / kprobe]
  │     context: struct pt_regs *
  │
  ├── [KRETPROBE]
  │     context: struct pt_regs *（r0 = 回傳值）
  │
  ├── [FENTRY / fexit]（基於 BTF，更穩定）
  │     context: 函式原本的參數型別
  │
  ▼ sys_exit
  ├── [TRACEPOINT/syscalls/sys_exit_*]

network path
  NIC driver
  │ ▲ [XDP]
  ▼ context: struct xdp_md *
  traffic control layer
  │ ▲ [TC / sched_cls / sched_act]
  ▼ context: struct __sk_buff *
  socket layer
  │ ▲ [SOCKET_FILTER]
  ▼ context: struct __sk_buff *
  process recv()

cgroup / container
  ├── [CGROUP_SKB]
  ├── [CGROUP_SOCK]
  └── [CGROUP_SYSCTL]

LSM security hooks
  └── [LSM]
      context: 各 LSM hook 的特定 args
```

## Tracepoint（`BPF_PROG_TYPE_TRACEPOINT`）

**最穩定的 attach 點**。tracepoint 是 kernel 開發者在 source code 裡手動插入的靜態標記，ABI 穩定（upgrading kernel 不會移掉 tracepoint，但欄位可能改變）。

**attach 語法**：`SEC("tracepoint/<category>/<name>")`

**context 型別**：每個 tracepoint 有自己的 `trace_event_raw_<name>` 結構，定義在 kernel source 的 `include/trace/events/` 下。用 vmlinux.h 或直接讀 format 檔案。

```c
/* 查看 tracepoint 的欄位 */
/* cat /sys/kernel/debug/tracing/events/syscalls/sys_enter_openat/format */
/*
 * name: sys_enter_openat
 * field: int __syscall_nr;  offset:8;
 * field: int dfd;           offset:16;
 * field: const char * filename; offset:24;
 * field: int flags;         offset:32;
 * field: umode_t mode;      offset:40;
 */

#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

SEC("tracepoint/syscalls/sys_enter_openat")
int trace_openat(struct trace_event_raw_sys_enter *ctx)
{
    // ctx->args[0] = dfd, ctx->args[1] = filename, ctx->args[2] = flags
    char filename[64];
    bpf_probe_read_user_str(filename, sizeof(filename),
                            (void *)ctx->args[1]);
    bpf_printk("openat: %s\n", filename);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

**回傳值語意**：永遠回傳 0；回傳值被忽略。

**優點**：ABI 穩定；**限制**：只有 kernel 開發者放的 instrumentation 點，不是所有函式都有。

## kprobe / kretprobe（`BPF_PROG_TYPE_KPROBE`）

**最靈活的 attach 點**。可以 attach 到任何 **未被 inline** 的 kernel 函式，包括 tracepoint 沒有覆蓋的函式。

**attach 語法**：`SEC("kprobe/<function_name>")` / `SEC("kretprobe/<function_name>")`

**context 型別**：`struct pt_regs *`（platform-specific register 狀態）

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

/* kprobe：attach 到 vfs_read 的入口 */
SEC("kprobe/vfs_read")
int trace_vfs_read_entry(struct pt_regs *ctx)
{
    // 用 PT_REGS_PARM1/2/3 取得函式參數
    // vfs_read(struct file *file, char __user *buf, size_t count, loff_t *pos)
    struct file *filep = (struct file *)PT_REGS_PARM1(ctx);
    size_t count = (size_t)PT_REGS_PARM3(ctx);
    bpf_printk("vfs_read: count=%zu\n", count);
    return 0;
}

/* kretprobe：attach 到 vfs_read 的出口 */
SEC("kretprobe/vfs_read")
int trace_vfs_read_return(struct pt_regs *ctx)
{
    // 回傳值在 PT_REGS_RC
    long ret = PT_REGS_RC(ctx);
    bpf_printk("vfs_read returned: %ld\n", ret);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

**回傳值語意**：永遠回傳 0；kprobe **不能**修改 kernel 的執行流程（無法修改參數或回傳值）。

**優點**：可以 attach 到幾乎任何函式；**限制**：inline 函式無法 attach；ABI 不穩定（kernel 版本更新可能改函式簽名）；`PT_REGS_PARM1` 等 macro 是 arch-specific 的。

## fentry / fexit（`BPF_PROG_TYPE_TRACING`，subtypes `BPF_TRACE_FENTRY / FEXIT`）

**kprobe 的現代替代品**（kernel 5.5+）。基於 BTF，能直接用函式的 C 型別存取參數，不需要 `PT_REGS_*` macro。效能比 kprobe 好（不用保存所有 registers）。

**attach 語法**：`SEC("fentry/<function_name>")` / `SEC("fexit/<function_name>")`

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

/*
 * fexit 讓你同時看到函式的參數和回傳值
 * 參數型別從 BTF 自動推導，不需要 PT_REGS
 */
SEC("fexit/vfs_read")
int BPF_PROG(trace_vfs_read_exit,
             struct file *file,   /* 原本的第 1 個參數 */
             char __user *buf,    /* 第 2 個參數 */
             size_t count,        /* 第 3 個參數 */
             loff_t *pos,         /* 第 4 個參數 */
             long ret)            /* 回傳值（fexit 獨有）*/
{
    bpf_printk("vfs_read: count=%zu, ret=%ld\n", count, ret);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

`BPF_PROG` macro（來自 `bpf_tracing.h`）把函式參數從 context struct 展開成 C 型別，對比 kprobe 的 `PT_REGS_PARM1(ctx)` 更直接。

**回傳值語意**：永遠回傳 0。

**優點**：型別安全（BTF-based）；效能比 kprobe 好；fexit 能看回傳值；**限制**：需要 kernel 5.5+；只能 attach 到在 BTF 裡有型別資訊的函式。

## raw_tracepoint（`BPF_PROG_TYPE_RAW_TRACEPOINT`）

比普通 tracepoint 更底層，效能更好，但 ABI 穩定性低一點。raw_tracepoint 直接傳遞 kernel 的 raw args，不經過格式化。

```c
SEC("raw_tracepoint/sched_switch")
int trace_sched_switch(struct bpf_raw_tracepoint_args *ctx)
{
    // ctx->args 是 raw 的 tracepoint args，型別是 unsigned long[]
    // sched_switch 的 args：prev_comm, prev_pid, prev_prio, prev_state,
    //                       next_comm, next_pid, next_prio
    struct task_struct *prev = (struct task_struct *)ctx->args[1];
    struct task_struct *next = (struct task_struct *)ctx->args[5];

    bpf_printk("sched: %s -> %s\n",
               prev->comm, next->comm);
    return 0;
}
```

## XDP（`BPF_PROG_TYPE_XDP`）

在網路卡 **driver** 層處理封包，在 kernel networking stack 的 sk_buff 分配之前執行。這是 Linux 上最快的封包處理路徑。

**attach 方式**：`ip link set dev eth0 xdp obj prog.o sec xdp`

**context 型別**：`struct xdp_md *`

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, __be32);    /* IPv4 address */
    __type(value, u8);      /* blocked: 1 */
} blocked_ips SEC(".maps");

SEC("xdp")
int xdp_drop_blocked(struct xdp_md *ctx)
{
    void *data_end = (void *)(long)ctx->data_end;
    void *data     = (void *)(long)ctx->data;

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return XDP_PASS;

    if (bpf_ntohs(eth->h_proto) != ETH_P_IP)
        return XDP_PASS;

    struct iphdr *iph = (void *)(eth + 1);
    if ((void *)(iph + 1) > data_end)
        return XDP_PASS;

    __be32 src = iph->saddr;
    u8 *blocked = bpf_map_lookup_elem(&blocked_ips, &src);
    if (blocked && *blocked)
        return XDP_DROP;

    return XDP_PASS;
}

char LICENSE[] SEC("license") = "GPL";
```

**回傳值語意**：

| 回傳值 | 意義 |
|---|---|
| `XDP_PASS` | 讓封包繼續進 kernel networking stack |
| `XDP_DROP` | 直接丟棄封包 |
| `XDP_TX` | 把封包從同一張網卡送出去 |
| `XDP_REDIRECT` | 把封包送到另一張網卡或 AF_XDP socket |
| `XDP_ABORTED` | 程式錯誤（記錄 trace，丟棄封包）|

## TC（Traffic Control）（`BPF_PROG_TYPE_SCHED_CLS` / `SCHED_ACT`）

在 kernel 的 traffic control 層處理封包。比 XDP 晚（sk_buff 已分配），但能修改封包內容、能看 socket context。

**context 型別**：`struct __sk_buff *`（比 `xdp_md` 豐富得多）

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <linux/pkt_cls.h>

SEC("tc")
int tc_log_ports(struct __sk_buff *skb)
{
    // __sk_buff 提供了很多 parsed 的 metadata
    bpf_printk("TC: src_port=%u dst_port=%u\n",
               skb->remote_port >> 16,
               bpf_ntohs(skb->local_port));
    return TC_ACT_OK;  // 讓封包繼續
}

char LICENSE[] SEC("license") = "GPL";
```

**回傳值語意**：`TC_ACT_OK`（繼續）、`TC_ACT_SHOT`（丟棄）、`TC_ACT_REDIRECT`（重導）、`TC_ACT_UNSPEC`（使用預設 action）。

## Socket Filter（`BPF_PROG_TYPE_SOCKET_FILTER`）

和 Classic BPF socket filter 的接班人，attach 到一個 socket 上，過濾進入這個 socket 的封包。

```c
SEC("socket")
int socket_filter(struct __sk_buff *skb)
{
    // 回傳 > 0 表示接受，回傳 0 表示丟棄
    // 回傳值是接受的封包長度（通常回傳 skb->len 或 -1）
    if (skb->protocol == bpf_htons(ETH_P_IP))
        return skb->len;  // 接受所有 IPv4
    return 0;             // 丟棄非 IPv4
}
```

## Cgroup BPF（`BPF_PROG_TYPE_CGROUP_*`）

在 cgroup 層次攔截網路操作，對整個 container 或 process group 做策略控制。

| Subtype | Attach 時機 |
|---|---|
| `CGROUP_SKB` | cgroup 的 ingress/egress 封包 |
| `CGROUP_SOCK` | socket 建立時 |
| `CGROUP_SOCK_ADDR` | bind/connect 時，可以改 IP/port |
| `CGROUP_SYSCTL` | sysctl 讀寫 |
| `CGROUP_DEVICE` | 設備存取控制 |

```c
/* 攔截 cgroup 裡的 socket 建立，拒絕建立 IPv6 socket */
SEC("cgroup/sock_create")
int restrict_ipv6(struct bpf_sock *sk)
{
    if (sk->family == AF_INET6)
        return 0;  // 0 = 拒絕
    return 1;      // 1 = 允許
}
```

## LSM（`BPF_PROG_TYPE_LSM`）（kernel 5.7+）

在 Linux Security Module 的 hook 上執行，做強制存取控制。

```c
/* 拒絕特定 UID 的 process 執行任何程式 */
SEC("lsm/bprm_check_security")
int prevent_exec(struct linux_binprm *bprm)
{
    u32 uid = bpf_get_current_uid_gid() & 0xFFFFFFFF;
    if (uid == 1234)
        return -EPERM;  // 拒絕
    return 0;           // 允許
}
```

## Perf Event（`BPF_PROG_TYPE_PERF_EVENT`）

在 hardware / software perf event 觸發時執行，用於 profiling。

```c
/* CPU sampling：每 N 個 clock 觸發一次 */
SEC("perf_event")
int cpu_sampler(struct bpf_perf_event_data *ctx)
{
    // ctx->regs 是觸發 event 時的 register 狀態
    // 可以讀 PC（instruction pointer）做 stack unwinding
    u64 ip = PT_REGS_IP(&ctx->regs);
    bpf_printk("sample at ip=0x%llx\n", ip);
    return 0;
}
```

## 所有 Program Types 總覽

| Program Type | Attach Point | Context 型別 | 主要用途 |
|---|---|---|---|
| `TRACEPOINT` | kernel tracepoint | `trace_event_raw_*` | syscall 追蹤，穩定 ABI |
| `KPROBE` | 任意 kernel 函式入口 | `struct pt_regs *` | 動態 instrumentation |
| `KRETPROBE` | 任意 kernel 函式出口 | `struct pt_regs *` | 追蹤回傳值 |
| `TRACING/fentry` | 任意 BTF 函式入口 | 函式原型 args | 現代 kprobe 替代 |
| `TRACING/fexit` | 任意 BTF 函式出口 | 函式 args + 回傳值 | 同時看 input/output |
| `RAW_TRACEPOINT` | tracepoint（raw） | `bpf_raw_tracepoint_args` | 高效 tracing |
| `XDP` | NIC driver 層 | `struct xdp_md *` | 高效封包處理 |
| `SCHED_CLS` | TC ingress/egress | `struct __sk_buff *` | 流量整形、過濾 |
| `SOCKET_FILTER` | socket recv | `struct __sk_buff *` | socket 過濾 |
| `CGROUP_SKB` | cgroup 封包 | `struct __sk_buff *` | 容器網路策略 |
| `CGROUP_SOCK` | socket 建立 | `struct bpf_sock *` | socket 創建控制 |
| `LSM` | LSM hook | hook-specific | 強制存取控制 |
| `PERF_EVENT` | perf event 觸發 | `bpf_perf_event_data` | CPU profiling |
| `UPROBE` | userspace 函式 | `struct pt_regs *` | userspace 追蹤 |
| `SK_MSG` | sendmsg | `struct sk_msg_md *` | socket redirect |
| `SK_SKB` | sockmap recv | `struct __sk_buff *` | socket steering |
| `ITER` | BPF iterator | iter-specific | 批次讀取 kernel objects |

## 踩雷集錦

1. **選了 kprobe 但函式被 inline 了**：如果 `/proc/kallsyms` 找不到這個函式名，它被 inline 了，kprobe 掛不上去。改用 fexit/fentry，或找一個沒被 inline 的上層函式

2. **Tracepoint 的欄位 offset 不固定**：`ctx->args[0]` 是第一個 raw argument，但不同 tracepoint 的 args 順序不同；要先查 `/sys/kernel/debug/tracing/events/<category>/<name>/format`

3. **XDP 的 bounds check 必須做**：verifier 強制要求；`if ((void *)(hdr + 1) > data_end)` 不只是好習慣，是必要條件

4. **`SCHED_CLS` 和 `SCHED_ACT` 的差別**：`cls` 是 classifier（分類封包），`act` 是 action（對封包做操作）；現代用法通常用 `SCHED_CLS` 搭配 direct-action flag，一個 program 同時做分類和 action

5. **LSM program 需要 `CAP_MAC_ADMIN` 才能載入**（kernel 5.7+）：普通 root 不夠；需要 `CAP_MAC_ADMIN + CAP_BPF`

## 動手練習

1. 寫一個 `fentry/vfs_read` 程式，印出每個 `vfs_read()` 呼叫的 file 名稱和讀取的 bytes 數（提示：用 `bpf_probe_read_kernel_str` 讀 `file->f_path.dentry->d_name.name`）

2. 比較 `kprobe/vfs_read` 和 `fentry/vfs_read` 兩個版本：
   - 在 `bpftool prog dump jited` 輸出裡，哪個生成的 native code 更短？
   - 在效能上哪個更好？（用 `run_cnt` / `run_time_ns` 比較）

3. 載入一個 XDP program（用 `XDP_PASS`，不過濾任何封包），確認它被附加到網路介面上：`ip link show dev lo`

## 本章重點整理

- Program type 決定了 attach point、context 型別、可用的 helper set、回傳值語意
- tracepoint 最穩定（靜態 ABI）；kprobe 最靈活（任意函式）；fentry/fexit 是現代 kprobe（基於 BTF，型別安全）
- XDP 效能最高（driver 層，sk_buff 分配前）；TC 功能最豐富（sk_buff 可用）
- cgroup BPF 和 LSM 用於容器和安全策略，不是 observability

## 自我檢核

- [ ] 能說出 tracepoint、kprobe、fentry 三者的主要差別（穩定性、效能、功能）
- [ ] 知道 XDP 和 TC BPF 各自 attach 在 networking stack 的哪個位置
- [ ] 知道 `XDP_DROP` 和 `TC_ACT_SHOT` 各自的語意，以及哪個更早執行
- [ ] 給一個觀測需求，能說出應該用哪種 program type

## 延伸閱讀

### 官方文件

- **[Linux kernel: BPF program types](https://www.kernel.org/doc/html/latest/bpf/bpf_prog_types.html)**
  - **讀哪裡**：每個 program type 的描述；這是最完整的官方列表
  - **學什麼**：每個 type 的 kernel 版本需求和具體的 context 型別

- **[Cilium BPF Reference: Program Types](https://docs.cilium.io/en/stable/reference-guides/bpf/#program-types)**
  - **讀哪裡**：整節；表格格式很清晰
  - **學什麼**：每個 program type 的 context、helper 集合、回傳值——比 kernel docs 更易讀

### 部落格

- **[A tour of program types](https://blogs.oracle.com/linux/post/bpf-a-tour-of-program-types)** — Alan Maguire, Oracle Linux Blog, 2021
  - **這篇說什麼**：系列文章，每種主要 program type 一篇；有實際的 code 範例和執行輸出
  - **讀哪裡**：根據你最感興趣的 type 選讀；kprobe、tracepoint、XDP 篇是必讀
  - **為什麼值得讀**：作者是 Oracle 的 kernel 開發者；程式碼範例品質高，能直接跑

→ [Ch 7 Attach 機制與 bpf_link 生命週期](./07-attach-mechanisms.md)
