# Ch 39 — Tail calls 與 BPF-to-BPF calls

> **目標**：理解 BPF tail call 和 BPF-to-BPF function call 的設計差異——stack 的行為、call depth 限制、程式拆分策略——以及如何用它們突破 BPF 的 1M instruction 限制。

## 為什麼需要程式組合？

BPF 程式有幾個大小限制：
- 最大 1,000,000 verified instructions
- BPF-to-BPF call 最大深度 8（stack frame）
- 單個 tail call chain 最大深度 33（從 kernel 4.2 到現在基本不變）

對於複雜的應用（完整的 packet processing pipeline、多階段 policy engine），這些限制可能不夠。解法是把程式拆分成多個 BPF program，用 tail call 或 BPF-to-BPF call 串接。

## Tail Call：跳轉不返回

`bpf_tail_call(ctx, prog_array, index)` 跳轉到 `prog_array` map 裡 index `index` 的 BPF 程式，**不返回**（stack frame 被丟棄）：

```
tail call 的 stack 行為：

Program A（stack frame A）
  │
  └── bpf_tail_call(ctx, array, 0)
         → 替換 stack frame A
         → Program B（使用 A 的 stack frame，A 的 frame 被回收）
              │
              └── bpf_tail_call(ctx, array, 1)
                     → Program C（使用 B 的 frame）
                          │
                          └── return 0  （C 的 return 就是整個 chain 的 return）
```

這讓你可以串接 33 個程式而不超過 stack 限制，因為每次 tail call 都不消耗新的 stack 空間。

**使用 tail call**：

```c
/* tail_calls.bpf.c */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>

/* Tail call 的 prog array map */
struct {
    __uint(type, BPF_MAP_TYPE_PROG_ARRAY);
    __uint(max_entries, 10);
    __uint(key_size, sizeof(u32));
    __uint(value_size, sizeof(u32));
} prog_array SEC(".maps");

/* 第一個程式：做 L2 parsing */
SEC("xdp/parse_l2")
int xdp_parse_l2(struct xdp_md *ctx)
{
    /* ... parse Ethernet ... */
    u32 next = 1;  /* 跳轉到 prog array 的 index 1 */
    bpf_tail_call(ctx, &prog_array, next);
    /* 如果 tail call 失敗（index 無效 / array 空），繼續執行這裡 */
    return XDP_PASS;
}

/* 第二個程式：做 L3 parsing */
SEC("xdp/parse_l3")
int xdp_parse_l3(struct xdp_md *ctx)
{
    /* ... parse IP ... */
    u32 next = 2;
    bpf_tail_call(ctx, &prog_array, next);
    return XDP_PASS;
}

/* 第三個程式：做 policy enforcement */
SEC("xdp/enforce")
int xdp_enforce(struct xdp_md *ctx)
{
    /* ... apply policy ... */
    return XDP_DROP;  /* or XDP_PASS */
}

char LICENSE[] SEC("license") = "GPL";
```

**Userspace 把 program fd 放入 prog array**：

```c
/* 載入所有程式 */
struct tail_calls_bpf *skel = tail_calls_bpf__open_and_load();

/* 把程式放入 prog array */
int prog_fd_l3 = bpf_program__fd(skel->progs.xdp_parse_l3);
int prog_fd_en = bpf_program__fd(skel->progs.xdp_enforce);

int map_fd = bpf_map__fd(skel->maps.prog_array);
u32 key = 1;
bpf_map_update_elem(map_fd, &key, &prog_fd_l3, BPF_ANY);
key = 2;
bpf_map_update_elem(map_fd, &key, &prog_fd_en, BPF_ANY);

/* attach 第一個程式到 XDP */
bpf_program__attach_xdp(skel->progs.xdp_parse_l2, ifindex);
```

## Tail Call 和 Context 傳遞

Tail call 的各個程式共享同一個 `ctx`，但 **local variables 會消失**（stack frame 被替換了）。跨 tail call 傳遞資料要用 maps 或 `ctx->data_meta`：

```c
/* 傳遞資料：用 per-CPU array（有 overhead）*/
struct meta { u32 l3_proto; u32 l4_proto; };

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, struct meta);
} meta_map SEC(".maps");

SEC("xdp/parse_l2")
int xdp_parse_l2(struct xdp_md *ctx)
{
    /* 把 L2 解析的結果存到 meta_map */
    u32 key = 0;
    struct meta m = { .l3_proto = ETH_P_IP };
    bpf_map_update_elem(&meta_map, &key, &m, BPF_ANY);
    bpf_tail_call(ctx, &prog_array, 1);
    return XDP_PASS;
}

/* 或用 xdp_md 的 data_meta headroom 傳遞（更快，但需要 bounds check）*/
```

## BPF-to-BPF Call：函式呼叫（kernel 4.16+）

和 tail call 不同，BPF-to-BPF call **會返回**，像一般的函式呼叫：

```c
/* BPF 函式（不需要 SEC 標注，作為 static helper）*/
static __always_inline int check_ip(struct iphdr *iph)
{
    /* 這是一般的 C 函式，clang 可能 inline 或生成 BPF CALL 指令 */
    if (iph->protocol == IPPROTO_TCP) return 1;
    return 0;
}

SEC("xdp")
int xdp_main(struct xdp_md *ctx)
{
    /* ... parse headers ... */
    if (check_ip(iph)) {
        /* 處理 TCP */
    }
    return XDP_PASS;
}
```

`__always_inline` 強制 inline（不生成 call 指令）。如果不用 `__always_inline`，clang 可能生成 BPF-to-BPF call（call depth 最多 8 層）。

## 比較：Tail Call vs BPF-to-BPF Call

| 面向 | Tail Call | BPF-to-BPF Call |
|---|---|---|
| **返回** | 不返回（跳轉）| 返回（call/ret）|
| **Stack** | 不增加（替換 frame）| 增加一個 frame（最多 8 層）|
| **最大深度** | 33 | 8 |
| **局部變數** | 消失 | 保留 |
| **程式關係** | 獨立程式（不同 fd）| 同一個 ELF 的函式 |
| **用途** | 流水線（pipeline）| 模組化、程式碼重用 |

## Cilium 的 Tail Call 使用

Cilium 的 BPF datapath 大量使用 tail call 做 packet processing pipeline：

```
cilium_call_policy（TC 入口）
  ↓ tail call
cilium_drop_notify（丟棄通知）
  ↓ tail call
cilium_send_signal（通知 agent）
```

Cilium 的 call table 有 40+ 個 tail call 程式，每個處理特定的 packet 類型或策略。

## 踩雷集錦

1. **tail call 的 `bpf_tail_call` 在 tail call 失敗後繼續執行**：如果 `prog_array` 的 index 是空的，`bpf_tail_call` 立刻返回（不執行 tail call），程式繼續執行後面的指令；要加 fallback 處理

2. **tail call chain 的總長度是 33，不是每個 program 的長度**：從 entry program 到最後一個 tail-called program，加起來最多 33 個 tail call

3. **不能 tail call 到不同 program type**：XDP program 不能 tail call 到 TC program；tail call 的目標必須是相同 type

4. **BPF-to-BPF call 的 stack frame 疊加**：每層 call 消耗 stack 空間（最多 512 bytes per frame × 8 frames = 4KB，但實際上 verifier 會計算每個 frame 的實際用量）

5. **tail call 失敗的原因**：index 超出 `max_entries`、那個 slot 是空的、target program 和當前 program type 不同

## 動手練習

1. 把 Ch 26 的 XDP IP filter 改成 tail call 版本：L2 parsing → L3 parsing → policy check，三個獨立的 XDP 程式用 tail call 串接

2. 用 `bpftool prog dump xlated` 查看 tail call 指令（`call bpf_tail_call`），以及 BPF-to-BPF call（`call pc+N`）的外觀差異

## 本章重點整理

- Tail call 跳轉不返回，不消耗額外 stack，最深 33 層；用於 pipeline 和繞過 1M instruction 限制
- BPF-to-BPF call 是一般函式呼叫，有返回，最深 8 層；用於模組化
- Tail call 的各 program 共享 `ctx`，跨 call 傳遞資料用 map 或 metadata
- Tail call 目標必須和 caller 是相同 program type

## 自我檢核

- [ ] 能解釋 tail call 和一般函式呼叫的 stack 差異
- [ ] 知道 tail call 的最大 chain 深度和 BPF-to-BPF call 的最大深度
- [ ] 能說出 tail call 失敗後程式的行為（繼續執行）

→ [Ch 40 並發控制：spinlock, per-CPU maps, atomic](./40-concurrency-in-bpf.md)
