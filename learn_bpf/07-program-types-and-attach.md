# Ch 7 — Program types 與 attach 點全景

> 目標：建立 BPF program type × attach point 的全景地圖。後面 Part 3–6 寫 BPF 都會回頭看這張圖 — 知道自己在哪、為什麼選這個 type、能用哪些 helper。

## 為什麼要分 program type？

新手第一次寫 BPF 常被 `SEC("...")` 這串字搞混 — 為什麼要寫 `SEC("kprobe/...")` 不能寫 `SEC("xdp")` 來追 kprobe？

答案是：**BPF 不是「一種程式」，是十幾種**。每種有自己的：

1. **Context**（你拿到的「事件參數」）— XDP 拿到的是 `xdp_md`（packet 描述），kprobe 拿到的是 `pt_regs`（CPU register 快照），完全不同的 struct。
2. **可用 helper 子集**：`bpf_get_current_pid_tgid` 在 tracing 程式可用，在 XDP 程式不可用（XDP 跑在 NIC interrupt context，沒有 current task）。
3. **Verifier 行為**：什麼可以做、什麼會被拒，每種 type 有自己的規則。
4. **可 attach 的點**：tracing program 可以 attach kprobe / tracepoint / fentry，但不能 attach 到網卡。

`SEC("xxx")` 字串是給 libbpf / kernel 看的「我是哪一種」標記。一個 BPF program 載入時就**綁定一種 type**，不能改。

## Program type、attach point、hook —— 別搞混

```
┌────────────────────┐
│  Program type      │ ← 你寫程式時宣告（SEC 字串）
│  (BPF_PROG_TYPE_*) │   決定：context、helpers、verifier 規則
└─────────┬──────────┘
          │
          │ 一個 type 通常能掛在多個 attach point
          ▼
┌────────────────────┐
│  Attach point      │ ← 載入後 attach 時指定
│  (具體掛在哪個事件) │   例：哪個 kernel function、哪張網卡
└─────────┬──────────┘
          │
          │ 底層
          ▼
┌────────────────────┐
│  Hook 機制          │ ← Ch 4 講的那套
│  (kprobe, XDP, ...) │   kernel 怎麼把控制權交給 BPF
└────────────────────┘
```

例：「我寫一個 `BPF_PROG_TYPE_KPROBE` 的程式（program type），attach 到 `do_sys_openat2` 這個 function（attach point），底層用 kprobe 機制（hook）。」

## 全景表

按「應用場景」分四大類，最常用的列出來：

### Tracing / Observability（Part 4 主場）

| Program type | SEC 寫法 | Context | 典型用途 |
|---|---|---|---|
| `BPF_PROG_TYPE_KPROBE` | `SEC("kprobe/foo")` | `pt_regs *` | 追 kernel function 進入 |
| `BPF_PROG_TYPE_KPROBE` | `SEC("kretprobe/foo")` | `pt_regs *` | 追 kernel function 退出 |
| `BPF_PROG_TYPE_TRACING` | `SEC("fentry/foo")` | function args | kprobe 的現代替代 |
| `BPF_PROG_TYPE_TRACING` | `SEC("fexit/foo")` | function args + ret | kretprobe 的現代替代 |
| `BPF_PROG_TYPE_TRACEPOINT` | `SEC("tracepoint/sched/sched_switch")` | tracepoint args struct | 穩定 ABI 的 kernel 事件 |
| `BPF_PROG_TYPE_RAW_TRACEPOINT` | `SEC("raw_tp/sys_enter")` | raw args | 高效能 tracing |
| `BPF_PROG_TYPE_PERF_EVENT` | `SEC("perf_event")` | `bpf_perf_event_data *` | profiling、sampling |

### Networking（Part 5 主場）

| Program type | SEC 寫法 | Context | 典型用途 |
|---|---|---|---|
| `BPF_PROG_TYPE_XDP` | `SEC("xdp")` | `xdp_md *` | NIC driver 層 packet 處理 |
| `BPF_PROG_TYPE_SCHED_CLS` | `SEC("tc")` | `__sk_buff *` | TC ingress/egress |
| `BPF_PROG_TYPE_SOCK_OPS` | `SEC("sockops")` | `bpf_sock_ops *` | socket 事件（accept、connect） |
| `BPF_PROG_TYPE_SK_SKB` | `SEC("sk_skb/...")` | `__sk_buff *` | socket-level packet redirect |
| `BPF_PROG_TYPE_SK_MSG` | `SEC("sk_msg")` | `sk_msg_md *` | socket layer message 改寫 |
| `BPF_PROG_TYPE_SOCKET_FILTER` | `SEC("socket")` | `__sk_buff *` | tcpdump 風格的 socket 過濾 |

### Security（Part 6 主場）

| Program type | SEC 寫法 | Context | 典型用途 |
|---|---|---|---|
| `BPF_PROG_TYPE_LSM` | `SEC("lsm/file_open")` | LSM hook args | kernel 級安全鉤子 |
| `BPF_PROG_TYPE_CGROUP_SKB` | `SEC("cgroup_skb/ingress")` | `__sk_buff *` | cgroup 級網路過濾 |
| `BPF_PROG_TYPE_CGROUP_SOCK` | `SEC("cgroup/sock_create")` | `bpf_sock *` | cgroup 級 socket 控制 |

### 其他

| Program type | SEC 寫法 | 用途 |
|---|---|---|
| `BPF_PROG_TYPE_SYSCALL` | `SEC("syscall")` | 自訂 syscall 風格的 BPF |
| `BPF_PROG_TYPE_STRUCT_OPS` | `SEC("struct_ops/...")` | 用 BPF 實作 kernel ops 介面（TCP CC algo） |
| `BPF_PROG_TYPE_EXT` | `SEC("freplace/...")` | 動態替換另一個 BPF function |
| `BPF_PROG_TYPE_LWT_*` | `SEC("lwt_in")` 等 | Lightweight Tunnel BPF |

**全部完整列表**：`enum bpf_prog_type` in `include/uapi/linux/bpf.h`，目前約 30 種。

## Context — 你拿到的「事件描述」

每個 program type 進入時 `R1` 會被 kernel 設成一個 context pointer。你拿到的就是它。

### Tracing 類：`pt_regs *`

```c
SEC("kprobe/do_sys_openat2")
int trace_open(struct pt_regs *ctx) {
    // 從 register 拿原 function 的參數
    int dfd = (int)PT_REGS_PARM1(ctx);
    const char *filename = (const char *)PT_REGS_PARM2(ctx);
    ...
}
```

`PT_REGS_PARM1..6` 是巨集，因為**不同架構參數放的 register 不同**（x86_64 用 rdi/rsi/rdx，arm64 用 x0/x1...）。

### XDP：`xdp_md *`

```c
SEC("xdp")
int my_xdp(struct xdp_md *ctx) {
    void *data = (void *)(long)ctx->data;
    void *data_end = (void *)(long)ctx->data_end;
    // data..data_end 是封包內容
    ...
    return XDP_PASS;  // 或 XDP_DROP, XDP_TX, XDP_REDIRECT
}
```

### TC：`__sk_buff *`

```c
SEC("tc")
int my_tc(struct __sk_buff *skb) {
    // skb 是 socket buffer 的 BPF view
    return TC_ACT_OK;  // 或 TC_ACT_SHOT
}
```

### Tracepoint：自動生成的 args struct

每個 tracepoint 有自己的 args 結構：

```c
SEC("tracepoint/syscalls/sys_enter_openat")
int trace_openat(struct trace_event_raw_sys_enter *ctx) {
    // ctx->args[0] = dfd
    // ctx->args[1] = filename
    ...
}
```

每個 type 的 context 拿什麼，**vmlinux.h 裡都有定義**。寫的時候查它。

## 一個 type 通常對應多個 attach point

注意 program type **不等於** attach point。例如 `BPF_PROG_TYPE_KPROBE`：

- 可以 attach 到 `do_sys_openat2` 的入口（kprobe）
- 可以 attach 到 `do_sys_openat2` 的退出（kretprobe）
- 可以 attach 到 `/usr/bin/bash` 的 `readline`（uprobe — 同 type！）

是的，**uprobe 跟 kprobe 用同一個 program type** — 因為兩者都是「dynamic probe，給你 pt_regs」。差別只在 attach 時指定的目標。

但 `BPF_PROG_TYPE_TRACING`（fentry/fexit）就不一樣 — 這個 type 的程式只能 attach 到 BTF-aware 的 kernel function，不能掛 user space。

## Helper function 不是所有 type 都能用

helper 跟 program type 是**多對多**關係。例如：

| Helper | Tracing | XDP | TC | LSM |
|---|---|---|---|---|
| `bpf_get_current_pid_tgid` | ✅ | ❌ | ❌ | ✅ |
| `bpf_skb_store_bytes` | ❌ | ❌ | ✅ | ❌ |
| `bpf_xdp_adjust_head` | ❌ | ✅ | ❌ | ❌ |
| `bpf_map_lookup_elem` | ✅ | ✅ | ✅ | ✅ |
| `bpf_probe_read_kernel` | ✅ | ❌ | ❌ | ✅ |

寫了不該用的 helper，verifier 會罵：

```
program of this type cannot use helper bpf_xxx#NN
```

這是新手最常見的卡關之一。**先確認 program type，再查能用什麼 helper**。

## 怎麼查當前 kernel 支援哪些 type 與 helper

```bash
# 列出當前 kernel 支援的所有 program type 與 map type
sudo bpftool feature probe | head -100

# 對特定 program type 列出能用的 helper
sudo bpftool feature probe | grep -A 50 "eBPF helpers supported for program type kprobe"
```

這個指令在你寫 BPF 時很有用 — 寫某個程式之前先確認「我目前的 kernel 支援這個 type 嗎、這個 helper 能用嗎」。

## SEC 字串怎麼跟 program type 對上的

`SEC("kprobe/...")` 不是 kernel 認得的，是 **libbpf 解析的**。libbpf 有個對照表：

```
"kprobe/..."           → BPF_PROG_TYPE_KPROBE  (kprobe attach)
"kretprobe/..."        → BPF_PROG_TYPE_KPROBE  (kretprobe attach)
"fentry/..."           → BPF_PROG_TYPE_TRACING (fentry attach)
"fexit/..."            → BPF_PROG_TYPE_TRACING (fexit attach)
"tracepoint/..."       → BPF_PROG_TYPE_TRACEPOINT
"xdp"                  → BPF_PROG_TYPE_XDP
"tc"                   → BPF_PROG_TYPE_SCHED_CLS
"lsm/..."              → BPF_PROG_TYPE_LSM
...
```

完整對照寫在 `tools/lib/bpf/libbpf.c` 的 `section_defs[]` 陣列。寫 BPF 時不確定 SEC 字串怎麼寫，去那邊查。

## 一個常見誤解

「我 attach 一個 BPF 程式到 N 個 kprobe，會載入 N 次」 — **不全然**。

實際是：**載入一次（one BPF program object），attach N 次（N attachment）**。BPF 程式跟 attach 是分開的兩件事 — 你可以把同一個 program 掛到 100 個不同的 kprobe 上，kernel 裡只有一份 code。

```bash
sudo bpftool prog list   # 看 loaded programs
sudo bpftool link list   # 看 attachments（5.7+ 用 link API 才有）
```

## 動手練習

1. **看你機器上有哪些 program type 在跑**：
   ```bash
   sudo bpftool prog list | awk '{print $2}' | sort | uniq -c
   ```
   猜哪些是 systemd / docker / 你自己的。
2. **挑一個 program 看它的 SEC 與 type**：
   ```bash
   sudo bpftool prog show id <id> --pretty
   ```
3. **印出 fentry 在你 kernel 能掛多少 function**：
   ```bash
   sudo bpftrace -l 'fentry:*' | wc -l
   ```
4. **查 helper 在不同 program type 的可用性**：
   ```bash
   sudo bpftool feature probe full | grep -B 1 -A 200 "eBPF helpers supported for program type xdp"
   ```

## 自我檢核

- [ ] 我能用一句話區分 program type、attach point、hook 機制
- [ ] 我能說出至少 3 個 tracing 類、3 個 networking 類、2 個 security 類的 program type
- [ ] 我能解釋為什麼 helper 跟 program type 是多對多
- [ ] 我能用 `bpftool` 查 kernel 支援哪些 type 與 helper
- [ ] 我知道 `SEC()` 字串是 libbpf 解析的，不是 kernel

下一章我們來看 BPF 的「狀態存放」 — maps。沒有 maps，BPF 程式只能算個無狀態的 lambda，幾乎做不了真正的 observability。

→ [Ch 8 BPF maps：kernel 與 user space 共享狀態](./08-bpf-maps.md)
