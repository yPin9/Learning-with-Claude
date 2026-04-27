# Ch 26 — Tail call、program chain、map-in-map

> 目標：搞懂單一 BPF program 的容量上限、為什麼需要組合、tail call 的機制與限制、map-in-map 動態 dispatch、BPF subprogram（function call）的角色。

## 為什麼要組合

單一 BPF program 受限：

- **指令數上限**：5.0 之前 4096 instruction，現在 100 萬
- **Verifier complexity 上限**：path explosion 比 instruction 上限更早爆
- **單一 type / context**：一個 program 只能對應一個 type
- **不能根據 runtime state 動態載入別的邏輯**：static dispatch only

對中型 BPF 系統（Cilium dataplane、Falco rule engine、Tetragon policy engine）這些限制不夠用。**Tail call、map-in-map、subprogram 是組合的三個機制**。

## Tail call — 程式間跳轉

`bpf_tail_call(ctx, &prog_array, index)` — 從一個 BPF program **跳到另一個**，**像 goto，不是 call**。

關鍵性質：
- **不返回**：tail call 後原 program 結束，跳去的 program 用同一個 stack
- **沒 stack 累加**：避免 unbounded recursion
- **call depth 上限 33**（5.10+）

API：

```c
struct {
    __uint(type, BPF_MAP_TYPE_PROG_ARRAY);
    __uint(max_entries, 16);
    __type(key, __u32);
    __type(value, __u32);
} prog_array SEC(".maps");

SEC("xdp")
int dispatcher(struct xdp_md *ctx) {
    /* ... 解 protocol header ... */

    if (ip->protocol == IPPROTO_TCP) {
        bpf_tail_call(ctx, &prog_array, 0);   // 跳到 TCP handler
    } else if (ip->protocol == IPPROTO_UDP) {
        bpf_tail_call(ctx, &prog_array, 1);   // 跳到 UDP handler
    }
    // 跳轉失敗才會跑到這
    return XDP_PASS;
}

SEC("xdp")
int tcp_handler(struct xdp_md *ctx) {
    /* ... 處理 TCP ... */
    return XDP_PASS;
}

SEC("xdp")
int udp_handler(struct xdp_md *ctx) {
    /* ... 處理 UDP ... */
    return XDP_PASS;
}
```

User space 把 program fd 寫進 PROG_ARRAY map：

```c
__u32 key = 0;
__u32 fd = bpf_program__fd(skel->progs.tcp_handler);
bpf_map_update_elem(prog_array_fd, &key, &fd, BPF_ANY);
```

**典型用途**：把大型程式拆成「dispatcher → 各 handler」結構，讓 verifier 對每一支獨立分析、避開 complexity 爆炸。

## Map-in-map — 動態 map dispatch

`BPF_MAP_TYPE_ARRAY_OF_MAPS` 與 `BPF_MAP_TYPE_HASH_OF_MAPS`：value 是 map fd。

```c
struct inner_map {
    __uint(type, BPF_MAP_TYPE_HASH);
    __type(key, __u32);
    __type(value, __u64);
    __uint(max_entries, 1024);
};

struct {
    __uint(type, BPF_MAP_TYPE_HASH_OF_MAPS);
    __uint(max_entries, 256);
    __type(key, __u32);
    __array(values, struct inner_map);
} outer SEC(".maps");

SEC("kprobe/...")
int prog(...) {
    __u32 key = some_value;
    void *inner = bpf_map_lookup_elem(&outer, &key);
    if (!inner) return 0;

    __u32 inner_key = 12345;
    __u64 *val = bpf_map_lookup_elem(inner, &inner_key);
    if (val) (*val)++;
    return 0;
}
```

**典型用途**：per-tenant 統計、per-namespace state、per-pod blocklist — 根據 runtime context 取得不同的 map。

Cilium 大量用 map-in-map 做 per-endpoint policy lookup。

## BPF subprograms — 真正的 function call

5.5 加入。在 BPF 內部寫多個 function，互相 call：

```c
__noinline int helper_func(int x) {
    return x * 2 + 1;
}

SEC("kprobe/...")
int main_prog(struct pt_regs *ctx) {
    int result = helper_func(42);
    bpf_printk("result=%d\n", result);
    return 0;
}
```

`__noinline` **必加** — 讓 verifier 知道這是個獨立 subprogram。

跟 tail call 的差別：

| | Tail call | Subprogram |
|---|---|---|
| 返回 | 不返回 | 返回 |
| Stack | 共用 | 各自一份 (512 byte/each) |
| 適合 | dispatch | 重用邏輯 |
| Verifier 行為 | 各 program 獨立分析 | 各 subprogram 獨立分析 |
| Call depth | 33 | 8 |

## Tail call vs subprogram 怎麼選

```
要重用一段邏輯？──→ subprogram
要根據 runtime 條件 dispatch？──→ tail call
要組合大型 protocol 處理？──→ 兩個都用（dispatcher + subprograms）
```

實務常用 pattern：**dispatcher 用 tail call 把 packet 分流到 protocol handler，handler 內部用 subprogram 做重用**。

## Cilium 怎麼組合

Cilium 的 datapath 是這個 pattern 的教科書：

```
TC ingress entry
  ├── parse Ethernet ──→ tail call → IPv4 handler
  │                                      ├── parse IP ──→ tail call → TCP handler
  │                                      │                            ├── conntrack lookup (subprogram)
  │                                      │                            ├── policy enforcement (subprogram)
  │                                      │                            └── L7 redirect (subprogram)
  │                                      └── ──→ tail call → UDP handler
  └── ──→ tail call → IPv6 handler ...
```

整個 dataplane 拆成 30+ 支 BPF program，用 PROG_ARRAY 串起來。**沒這個拆分，complexity 早就爆了**。

## kfunc — 比 helper 更靈活

7.x 開始 kernel 有「kfunc」概念 — 把 kernel function 標記為「BPF 可呼叫」，不需要寫成 helper（傳統 helper 要修改 kernel）。

```c
// kernel 內標
__bpf_kfunc int my_kernel_func(int x);

// BPF 端宣告 + 用
extern int my_kernel_func(int x) __ksym;

SEC("...") int prog(...) {
    int r = my_kernel_func(42);
    ...
}
```

kfunc 是組合的另一個維度 — 把「kernel 已存在的 function」直接開放給 BPF 用，不用走「先加 helper、merge upstream」的長路。Cilium / Tetragon 大量用 kfunc 拓展能力。

## 一個常見誤解

「tail call 是 BPF 的函式呼叫」 — **錯**。

tail call 不返回。如果你需要的是「跑完一段邏輯回來繼續」，要用 subprogram，不是 tail call。把 tail call 當 goto，不是 call。

## 動手練習

1. **寫一支 dispatcher + handler pattern**：XDP 解 ethernet → tail call 到 IPv4/IPv6 handler。
2. **看 Cilium 的 prog_array**：clone Cilium repo，看 `bpf/bpf_lxc.c` 與 `bpf/lib/jmp_table.h`。
3. **subprogram 範例**：寫一個共用的 `is_blocked(ip)` subprogram，給多個 BPF program 用。
4. **map-in-map**：寫一個 per-PID 的 stats map，每個 PID 自己一個 inner map。

## 自我檢核

- [ ] 我能解釋為什麼大型 BPF 系統需要組合
- [ ] 我能寫 PROG_ARRAY + bpf_tail_call dispatch
- [ ] 我能解釋 tail call 跟 subprogram 的差別
- [ ] 我能用 map-in-map 做動態 per-context state
- [ ] 我能說出 kfunc 跟 helper 的差別

下一章我們整理 BPF debug 的所有武器 — verifier log、bpftool dump、bpf_printk、trace_pipe、libbpf log level。

→ [Ch 27 Debug 技巧：verifier log、bpftool、bpf_printk](./27-debugging-bpf.md)
