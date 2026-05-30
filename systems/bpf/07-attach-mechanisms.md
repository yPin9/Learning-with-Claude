# Ch 7 — Attach 機制與 bpf_link 生命週期

> **目標**：理解 BPF program 如何被「附加」到 kernel 的 hook point——各種 attach 機制的底層實作、`bpf_link` 物件的生命週期與 reference counting、以及如何用 bpftool 和 libbpf 做 attach/detach。

## 為什麼需要這個？

一個 loaded 的 BPF program 只是「存在 kernel 記憶體裡的 code」，還沒有任何效果。它需要被 **attach** 到某個 hook point，kernel 才會在適當的時機呼叫它。

不同的 program type 有完全不同的 attach 機制：
- tracepoint：透過 `perf_event_open` + `ioctl`
- kprobe：透過 `perf_event_open` + kprobe 基礎設施
- fentry/fexit：直接用 `bpf_link_create`
- XDP：透過 netlink
- TC：透過 `tc` CLI 或 netlink
- cgroup：透過 `bpf_prog_attach` 系統呼叫

了解 attach 機制，才能理解為什麼某個程式在 detach 之後仍然在跑（還有 link 持有它），以及如何在不同場景下正確地清理。

## 先建立直覺：Attach 的本質

```
BPF Program（loaded，有 fd 或 pin path）
       │
       │  attach 操作
       ▼
  hook point（tracepoint / kprobe / NIC interface / cgroup / ...）

attach 成功後，觸發條件發生時：
  kernel event ──▶ 找到 attached BPF program ──▶ JIT call

attach 可以透過：
  1. bpf_link（現代方式，推薦）
  2. 直接 attach（老方式，例如 perf_event ioctl）
  3. 工具 CLI（bpftool / ip / tc / bpftrace）
```

## bpf_link：現代的 Attach 抽象

在 kernel 5.7 之前，attach 的方式因 program type 而異，沒有統一的介面。kernel 5.7 引入了 `bpf_link`：一個 kernel object，代表「BPF program 和 hook point 之間的連結」，有自己的 fd 和 reference count。

```
bpf_link 物件

  ┌─────────────────────────────────────────┐
  │             struct bpf_link              │
  │  prog:  *bpf_prog  ←── 持有 prog 的引用  │
  │  ops:   *bpf_link_ops（type-specific）   │
  │  id:    kernel-assigned id               │
  │  refcnt: atomic（link 的生命週期）         │
  └─────────────────────────────────────────┘

生命週期：
  bpf_link_create() ──▶ 回傳 link fd
  fd 關閉（或 pin 失效）──▶ bpf_link_release() ──▶ detach
```

用 libbpf 的 `bpf_link`：

```c
/* 用 libbpf 建立 link */
struct bpf_link *link = bpf_program__attach(prog);
if (!link) {
    fprintf(stderr, "failed to attach: %s\n", strerror(errno));
    return 1;
}

/* link 存活期間，BPF program 在 hook 上執行 */
/* ... 你的 userspace 程式做事 ... */

/* 釋放 link：detach BPF program */
bpf_link__destroy(link);
/* 等同於 close(bpf_link__fd(link)) */
```

**關鍵點**：`bpf_link` 的 fd 是生命週期的控制器。只要 fd 存在（或被 pin 到 BPF filesystem），BPF program 就在 hook 上執行。fd 關閉後，`bpf_link` 的 refcount 降到 0，kernel 自動 detach program。

## 各 Program Type 的 Attach 機制

### Tracepoint / Kprobe / Perf Event

這三種 type 都使用 `perf_event_open` syscall 建立 perf event，再把 BPF program 附加到這個 perf event 上。

```c
/*
 * 底層流程（libbpf 幫你做這些，這裡展示原理）：
 *
 * 1. 用 perf_event_open 建立 perf event（kprobe / tracepoint / hw counter）
 * 2. 用 PERF_EVENT_IOC_SET_BPF ioctl 把 BPF program 附加到這個 event
 * 3. 用 PERF_EVENT_IOC_ENABLE ioctl 啟用 event
 */

/* 用 libbpf 封裝後的介面（推薦） */
struct bpf_link *link;

/* kprobe */
link = bpf_program__attach_kprobe(prog, false, "vfs_read");

/* tracepoint */
link = bpf_program__attach_tracepoint(prog, "syscalls", "sys_enter_openat");

/* uprobe（userspace probe） */
link = bpf_program__attach_uprobe(prog, false, -1, "/lib/x86_64-linux-gnu/libc.so.6", 0x12345);
```

### fentry / fexit

fentry/fexit 用 `bpf_link_create` 的 `BPF_TRACE_FENTRY / FEXIT` 型別，直接把 program 掛在 kernel function 的 trampoline 上：

```c
/* libbpf 自動從 SEC("fentry/vfs_read") 識別 attach type */
link = bpf_program__attach(prog);
/* bpf_program__attach 內部呼叫 bpf_link_create(BPF_TRACE_FENTRY) */
```

### XDP

XDP 透過 netlink（`RTM_NEWLINK`）把 program 附加到網路介面：

```bash
# 用 ip 指令（底層是 netlink）
sudo ip link set dev eth0 xdp obj prog.o sec xdp
sudo ip link set dev eth0 xdp off  # detach

# 查看目前 attached 的 XDP program
sudo ip link show eth0 | grep xdp
```

XDP 有三種 attach mode：

| Mode | 說明 | 需求 |
|---|---|---|
| `native` | 在 NIC driver 裡執行（最快）| NIC driver 支援 |
| `offload` | 在 NIC hardware 執行（最最快）| 特殊 NIC，如 Netronome |
| `generic` | 在 kernel networking stack 執行（最慢，相容性最好）| 任何 NIC |

```bash
sudo ip link set dev eth0 xdp obj prog.o sec xdp  # native（預設）
sudo ip link set dev eth0 xdpgeneric obj prog.o sec xdp  # generic
```

### TC（Traffic Control）

TC 使用 netlink 的 `tc` 子系統：

```bash
# 建立 qdisc（clsact）
sudo tc qdisc add dev eth0 clsact

# 附加 ingress BPF program
sudo tc filter add dev eth0 ingress bpf obj prog.o sec tc direct-action

# 附加 egress BPF program
sudo tc filter add dev eth0 egress bpf obj prog.o sec tc direct-action

# 查看
sudo tc filter show dev eth0 ingress

# 刪除
sudo tc filter del dev eth0 ingress
sudo tc qdisc del dev eth0 clsact
```

`direct-action`（da）flag 讓 classifier 同時做 action，省去一層間接呼叫，是現代 TC BPF 的標準用法。

### Cgroup

Cgroup BPF 用 `bpf_prog_attach` syscall（`BPF_PROG_ATTACH`）附加到 cgroup path：

```c
/* 用 libbpf */
link = bpf_program__attach_cgroup(prog, cgroup_fd);
/* cgroup_fd 是用 open("/sys/fs/cgroup/...", O_RDONLY) 取得的 */
```

```bash
# 用 bpftool
sudo bpftool cgroup attach /sys/fs/cgroup/<path> sock_create pinned /sys/fs/bpf/my_prog
```

Cgroup BPF 有一個重要特性：**繼承（inheritance）**。附加在上層 cgroup 的 BPF program，對所有子 cgroup 也有效（可以設定 `BPF_F_ALLOW_MULTI` 讓子 cgroup 也附加自己的 program）。

## bpf_link 的生命週期管理

理解生命週期是避免 BPF program 意外 detach 或意外持續執行的關鍵：

```
情況一：程式退出後 BPF program 也消失（預設行為）
  userspace 程式持有 link fd
  → 程式退出，fd 自動關閉
  → bpf_link refcount 降到 0
  → kernel 自動 detach program

情況二：程式退出後 BPF program 繼續執行（pin）
  bpf_link__pin(link, "/sys/fs/bpf/my_link")
  → link 被 pin 到 BPF filesystem
  → 程式退出，fd 關閉，但 pin 還在
  → bpf_link refcount 還是 1（pin 持有）
  → program 繼續在 hook 上執行

情況三：移除 pin，program 停止
  rm /sys/fs/bpf/my_link
  → pin 消失，refcount 降到 0
  → kernel 自動 detach
```

```c
/* pin link 讓 BPF program 在你的 process 退出後繼續執行 */
err = bpf_link__pin(link, "/sys/fs/bpf/my_link");
if (err) {
    fprintf(stderr, "failed to pin link: %s\n", strerror(-err));
}
/* 現在可以安全地 exit，BPF program 還在跑 */
bpf_link__disconnect(link);  /* 斷開 link 和 fd 的連結，但不 destroy */
bpf_link__destroy(link);     /* 釋放 userspace 的 link 物件（不影響 kernel 的 link）*/
```

## 用 bpftool 管理 Links

```bash
# 列出所有 bpf_link
sudo bpftool link list

# 輸出例：
# 1: tracing  prog 5
#    prog_type kprobe  attach_type kprobe
#    pinned /sys/fs/bpf/my_link

# 查看 link 詳情
sudo bpftool link show id 1

# pin 一個已有的 link
sudo bpftool link pin id 1 /sys/fs/bpf/link_1

# detach（刪除 pin）
sudo rm /sys/fs/bpf/link_1
```

## 老式 Attach 方式（不推薦但要看得懂）

在 `bpf_link` 出現之前（kernel 5.7 前），某些 attach 用不同的機制：

```bash
# 老式 XDP attach（用 bpf 系統呼叫的 BPF_PROG_ATTACH）
# 現在 ip link set 底層還是這樣做

# 老式 cgroup attach（直接 setsockopt，沒有 link）
# 這種方式 detach 需要手動呼叫 BPF_PROG_DETACH
```

老式方式的問題：沒有統一的 fd 抽象，很難知道哪個 program 附加在哪裡，refcount 管理複雜。新程式碼一律用 `bpf_link`。

## 踩雷集錦

1. **BPF program 不見了但你以為它還在跑**：你沒有 pin link，userspace 程式退出了，link fd 關閉，program 被 detach。解法：在需要 program 持續的場景，永遠 pin link

2. **Pin 後 BPF program 一直在跑，系統重開後消失**：BPF filesystem 掛載在 `tmpfs` 上（`/sys/fs/bpf`），系統重開後 pin 消失，要重新 attach

3. **同一個 hook point 可以有多個 BPF program**（某些 type 支援）：tracepoint 和 kprobe 支援多個 program 同時 attach；XDP 只允許一個（native mode）。多個 program 的執行順序是按 attach 順序

4. **`bpf_program__attach` 和 `bpf_program__attach_kprobe` 的差別**：前者根據 `SEC()` annotation 自動判斷 attach type；後者是明確指定 kprobe attach。兩者都回傳 `bpf_link *`

5. **Cgroup BPF 的繼承問題**：在 `/sys/fs/cgroup/` 根掛的 BPF program 對所有 cgroup 都有效，包括 container；在生產環境裡要小心不要意外影響所有 container

## 進階：link update（不 detach 直接替換）

`bpf_link_update()` 讓你把一個 link 上的 program 替換成另一個，不需要 detach/re-attach（避免 hook point 有短暫的空白期）：

```c
/* 原子替換 link 上的 program（零停機時間更新）*/
err = bpf_link__update_program(link, new_prog);
if (err)
    fprintf(stderr, "link update failed: %s\n", strerror(-err));
```

這在生產環境做 hot reload 很有用。

## 動手練習

1. 用 libbpf 寫一個完整的 kprobe attach / pin / detach 流程：attach `vfs_read`，pin link 到 `/sys/fs/bpf/my_kprobe_link`，程式退出後用 `bpftool link list` 確認 link 還在，然後手動 `rm /sys/fs/bpf/my_kprobe_link` 並確認 link 消失

2. 附加一個 XDP program 到 `lo`（loopback），用 `ip link show lo` 確認附加成功，觀察 `run_cnt` 在你 ping localhost 時增加

3. 用 `bpftool link list` 查看系統上所有的 links，識別每個 link 的 prog id 和它附加在哪個 hook

## 本章重點整理

- `bpf_link` 是 BPF program 和 hook point 之間連結的 kernel object；fd 是它的生命週期控制器
- 不同 program type 用不同的底層機制 attach：tracepoint/kprobe 用 perf_event_open，XDP 用 netlink，TC 用 tc 子系統，cgroup 用 BPF_PROG_ATTACH
- Pin link 讓 program 在 userspace 退出後繼續執行；刪除 pin 觸發 detach
- `bpf_link_update()` 允許零停機時間替換 attached program

## 自我檢核

- [ ] 能解釋 `bpf_link` fd 的生命週期，以及 fd 關閉時發生什麼
- [ ] 知道 XDP 的三種 attach mode（native / offload / generic）及其效能差異
- [ ] 能說出 pin 的作用，以及如何讓 BPF program 在 userspace 退出後繼續執行
- [ ] 知道為什麼 cgroup BPF 的繼承特性在生產環境裡需要特別注意

## 延伸閱讀

### 官方文件

- **[bpf_link kernel documentation](https://www.kernel.org/doc/html/latest/bpf/libbpf/programs.html)**
  - **讀哪裡**："Pinning" 和 "Link" 那兩節
  - **學什麼**：libbpf 的 attach 和 pin API 的完整列表

- **[tc-bpf man page](https://man7.org/linux/man-pages/man8/tc-bpf.8.html)**
  - **讀哪裡**：整頁；特別是 direct-action 的說明
  - **學什麼**：TC BPF 的 attach 語法和選項

### 部落格

- **[BPF ring buffer](https://nakryiko.com/posts/bpf-ringbuf/)** — Andrii Nakryiko（libbpf 主要作者）
  - **這篇說什麼**：雖然主題是 ring buffer，但對 link/attach 機制的描述很清楚
  - **讀哪裡**：intro 那幾段
  - **為什麼值得讀**：作者是 libbpf 的主要開發者，說的就是 libbpf 的設計意圖

→ [Ch 8 BPF Maps：所有資料結構](./08-bpf-maps.md)
