# Ch 19 — XDP：最快的封包處理路徑

> 目標：搞懂 XDP 在 packet 路徑上的位置、三種運行模式、xdp_md context 與動作回傳碼、寫第一支 XDP drop filter、認識 packet bound check 的痛苦與技巧。

## XDP 在 kernel 哪裡

XDP（eXpress Data Path）是 Linux network stack 上**最早**的 BPF 掛點 — 在 packet 還沒進 kernel network stack 之前就跑：

```
NIC ──→ DMA → ring buffer ──→ XDP ──→ kernel network stack ──→ socket
                                ▲
                         你在這裡攔截
```

對比一般 packet 路徑：
- iptables / netfilter：在 routing 之後，相對深處
- TC（Ch 20）：在 network stack 內，但比 netfilter 早
- XDP：在 driver 層，最早

**XDP 的價值就是「早」**：丟掉一個 packet 的開銷比 netfilter 低 10×，因為避免了把 packet 包成 sk_buff 的成本。

典型用途：
- DDoS mitigation（Cloudflare 用 XDP 在每個 edge node 過濾）
- L4 load balancer（Meta Katran）
- 高效能 firewall

## 三種 XDP 模式

| 模式 | 跑在哪 | 速度 | 限制 |
|---|---|---|---|
| **Native** | NIC driver 的 RX hook 點 | 最快 | NIC driver 必須支援 |
| **Generic** | kernel 通用 stack 入口 | 中等 | 任意 NIC 都行（fallback） |
| **Offload** | NIC 硬體 | 極快 | 只有特殊 NIC（Netronome） |

絕大多 distro / NIC 預設用 native（intel ixgbe、mellanox mlx5、virtio-net 都支援）。檢查方式：

```bash
ip link show eth0
# 看 xdpgeneric / xdp 字樣
```

開發跟測試先用 generic 比較不挑硬體。

## xdp_md context

```c
SEC("xdp")
int my_filter(struct xdp_md *ctx) {
    void *data     = (void *)(long)ctx->data;       // packet 起始
    void *data_end = (void *)(long)ctx->data_end;   // packet 結束
    __u32 ifindex  = ctx->ingress_ifindex;
    ...
}
```

`ctx->data` / `ctx->data_end` 是 packet 的記憶體範圍。**所有對 packet 的存取都要先 bound check**（Ch 9）。

## XDP 動作回傳碼

| 回傳值 | 動作 |
|---|---|
| `XDP_PASS` | 放行，繼續走 kernel network stack |
| `XDP_DROP` | 丟掉 packet（最便宜） |
| `XDP_TX` | 從同一張 NIC 直接發回去（routing 之前） |
| `XDP_REDIRECT` | redirect 到別的 device 或 CPU（透過 DEVMAP / CPUMAP） |
| `XDP_ABORTED` | 同 DROP，但會觸發 tracepoint（debug 用） |

## 第一支 XDP — drop ICMP

寫 `xdp_drop_icmp.bpf.c`：

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/in.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

SEC("xdp")
int drop_icmp(struct xdp_md *ctx) {
    void *data     = (void *)(long)ctx->data;
    void *data_end = (void *)(long)ctx->data_end;

    // 1. Ethernet header
    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end) return XDP_PASS;

    if (eth->h_proto != bpf_htons(ETH_P_IP)) return XDP_PASS;

    // 2. IP header
    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end) return XDP_PASS;

    // 3. 是 ICMP 就 drop
    if (ip->protocol == IPPROTO_ICMP) return XDP_DROP;

    return XDP_PASS;
}
```

**每次解 packet 一個 layer 都要 bound check**。少一個 verifier 就拒絕。

Build & attach：

```bash
clang -O2 -g -target bpf -c xdp_drop_icmp.bpf.c -o xdp_drop_icmp.bpf.o
sudo bpftool net attach xdp obj xdp_drop_icmp.bpf.o sec xdp dev lo

# 測試 - ping localhost 應該 100% 丟失
ping -c 3 127.0.0.1

# detach
sudo bpftool net detach xdp dev lo
```

## bound check 的「展開技巧」

每解一層 protocol 寫一次 bound check 很煩。技巧：用 helper macro 把 pattern 包起來：

```c
#define ENSURE(p, type, end) ({ \
    if ((void *)(p) + sizeof(type) > (end)) return XDP_PASS; \
})

ENSURE(data, struct ethhdr, data_end);
struct ethhdr *eth = data;

ENSURE(eth + 1, struct iphdr, data_end);
struct iphdr *ip = (void *)(eth + 1);

ENSURE(ip + 1, struct tcphdr, data_end);
struct tcphdr *tcp = (void *)(ip + 1);
```

這樣寫起來乾淨，verifier 也理解。

## XDP 與 maps：常見 pattern

XDP 大多搭配 maps 做動態行為：

- **`LPM_TRIE` blocklist**：IP 黑名單
- **`HASH` rate limit**：per-IP 計數器
- **`PERCPU_ARRAY` 統計**：丟了多少 packet
- **`DEVMAP` redirect**：跨 NIC 轉發

範例：用 LPM_TRIE 的 IP blocklist：

```c
struct {
    __uint(type, BPF_MAP_TYPE_LPM_TRIE);
    __type(key, struct {
        __u32 prefixlen;
        __u32 addr;
    });
    __type(value, __u8);
    __uint(max_entries, 1024);
    __uint(map_flags, BPF_F_NO_PREALLOC);
} blocklist SEC(".maps");

SEC("xdp")
int filter(struct xdp_md *ctx) {
    /* ...解到 ip header... */

    struct { __u32 prefixlen; __u32 addr; } key = {
        .prefixlen = 32,
        .addr      = ip->saddr,
    };

    if (bpf_map_lookup_elem(&blocklist, &key)) return XDP_DROP;
    return XDP_PASS;
}
```

User space 動態加 IP（CIDR 也支援）：

```bash
# 加 192.168.1.100 (prefix 32 = single IP)
sudo bpftool map update pinned /sys/fs/bpf/blocklist \
    key 32 0 0 0 100 1 168 192 \
    value 1
```

## XDP 改 packet

改 packet 就用 helper：

```c
// 改 source IP
ip->saddr = bpf_htonl(0xC0A80101);

// 重新算 checksum
__u32 csum = ~bpf_ntohs(ip->check);
csum -= old_addr & 0xFFFF; csum -= old_addr >> 16;
csum += new_addr & 0xFFFF; csum += new_addr >> 16;
ip->check = ~bpf_htons(csum);
```

或用 `bpf_csum_diff()` helper 自動算。

更高階改動（把 packet 從 IPv4 變 IPv6、加/減 header）用 `bpf_xdp_adjust_head()` / `bpf_xdp_adjust_tail()` 調整 packet 範圍。

## 效能數字（拿來建立直覺）

Cloudflare 公開過 benchmark：

| 方案 | PPS（單核） |
|---|---|
| iptables `-j DROP` | ~1M |
| `tc -e bpf` drop | ~3M |
| **XDP drop** | **~10M（native mode）** |
| **XDP drop（offload）** | **~25M** |

差一個量級。**這就是 Cloudflare 一台 commodity server 能扛 10Gbps DDoS 的原因**。

## 一個常見誤解

「XDP 能取代 iptables」 — **不全然**。

XDP 只看 ingress 路徑、只在 packet 進來時跑、只有 driver 層的資訊（沒有 conntrack、沒有 routing 結果）。**iptables 提供的 stateful firewall、NAT、L7 inspection、egress filter，XDP 都沒有**（要嘛自己實作得超痛苦）。

實務上 XDP 用來做「快、簡單、ingress 可決定」的事 — DDoS drop、L4 LB、heavy hitter rate limit。其他還是 iptables / nftables / Cilium 上層 BPF 來處理。

## 動手練習

1. **跑 drop ICMP 範例**：在 lo 上 attach 試 ping。
2. **改成 drop 特定 source IP**：硬編碼一個 IP，loopback 測試（packet 從 lo 進來時 src 通常是 127.0.0.1）。
3. **加 stats**：用 PERCPU_ARRAY 計數，每秒從 user 端讀印。
4. **觀察 verifier 拒絕**：故意把 bound check 拿掉，看 verifier 怎麼罵。
5. **試 xdp-tools**：clone [xdp-tools](https://github.com/xdp-project/xdp-tools)，跑 `xdp-bench drop` 看你機器最高 PPS。

## 自我檢核

- [ ] 我能在 packet 路徑圖上指出 XDP 在哪、為什麼快
- [ ] 我能說出 XDP 三種模式的差別與該怎麼選
- [ ] 我能列出 XDP 五個 return action 與用途
- [ ] 我能寫一個多層 protocol 解析的 XDP 並做正確 bound check
- [ ] 我知道 XDP 跟 iptables 的能力邊界

下一章轉到 TC BPF — 比 XDP 略上層，少了「driver 層極致速度」但多了「能改 packet、能掛 egress、有更豐富的 sk_buff context」。

→ [Ch 20 TC BPF：ingress/egress 流量控制](./20-tc-bpf.md)
