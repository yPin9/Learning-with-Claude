# Ch 28 — TC BPF：流量整形與分類

> **目標**：理解 TC（Traffic Control）BPF 的架構——clsact qdisc、ingress/egress 的位置、direct-action 模式、sk_buff 的豐富 metadata——以及和 XDP 的差異和互補關係。

## TC BPF 的位置

TC BPF 執行在 kernel networking stack 的 traffic control 層，比 XDP 更晚（sk_buff 已分配），但能存取更豐富的 metadata：

```
封包進入（ingress）：
  NIC driver → XDP
             → skb 分配
             → TC ingress（clsact）
             → Netfilter/iptables
             → L3 routing
             → Socket receive

封包離開（egress）：
  Application send
    → L3 routing
    → TC egress（clsact）
    → XDP_TX hook（某些 driver）
    → NIC driver
```

**TC BPF 的優勢（相比 XDP）**：
- 可以存取 `struct __sk_buff` 的所有 metadata（socket info、mark、priority、protocol）
- 可以做 egress filtering（XDP 只能 ingress）
- 可以修改 sk_buff 的 mark/priority（影響後續的 qdisc 行為）
- 可以 redirect 到其他 socket（socket steering）

## 建立 TC BPF 的流程

```bash
# Step 1：建立 clsact qdisc（特殊的 qdisc，同時支援 ingress 和 egress）
sudo tc qdisc add dev eth0 clsact

# Step 2：attach BPF program 到 ingress
sudo tc filter add dev eth0 ingress bpf obj prog.o sec tc direct-action

# Step 3：attach 到 egress
sudo tc filter add dev eth0 egress bpf obj prog.o sec tc direct-action

# 查看
sudo tc filter show dev eth0 ingress
sudo tc filter show dev eth0 egress

# 刪除
sudo tc filter del dev eth0 ingress
sudo tc qdisc del dev eth0 clsact
```

## `__sk_buff` Context

TC BPF 的 context 是 `struct __sk_buff`（BPF 可見的 skb 視圖，並不是完整的 `struct sk_buff`）：

```c
/* <linux/bpf.h> 裡的 __sk_buff（BPF 可見部分）*/
struct __sk_buff {
    __u32 len;            /* 封包長度 */
    __u32 pkt_type;       /* PACKET_HOST, PACKET_BROADCAST, ... */
    __u32 mark;           /* skb->mark（可讀寫，用於 policy routing）*/
    __u32 queue_mapping;  /* queue mapping */
    __u32 protocol;       /* ETH_P_IP, ETH_P_IPV6, ... */
    __u32 vlan_present;
    __u32 vlan_tci;
    __u32 vlan_proto;
    __u32 priority;       /* skb->priority（可讀寫）*/
    __u32 ingress_ifindex;/* 進入介面 index */
    __u32 ifindex;        /* 輸出介面 index */
    __u32 tc_index;       /* TC index（可讀寫）*/
    __u32 cb[5];          /* 可讀寫的 scratch area */
    __u32 hash;
    __u32 tc_classid;
    __u32 data;           /* 封包資料起始（需要 bounds check）*/
    __u32 data_end;
    __u32 napi_id;
    /* ... 更多 metadata 欄位 */
    __u32 wire_len;
    __u32 gso_segs;
    /* socket info（ingress only）*/
    __u32 remote_ip4;
    __u32 local_ip4;
    __u32 remote_ip6[4];
    __u32 local_ip6[4];
    __u32 remote_port;
    __u32 local_port;
    __u32 data_meta;
};
```

## Direct-Action Mode

傳統 TC 的架構是「classifier 分類 + action 執行」兩個步驟。`direct-action`（da）flag 讓 classifier（BPF program）直接返回 action，省掉一層間接呼叫：

```
傳統 TC：
  classifier（決定 class）→ 找對應的 action → 執行 action

Direct-action TC：
  BPF program 直接返回 TC_ACT_*（classifier 和 action 合一）
```

TC action 的回傳值：

| 回傳值 | 說明 |
|---|---|
| `TC_ACT_OK` | 繼續處理（正常）|
| `TC_ACT_SHOT` | 丟棄封包 |
| `TC_ACT_UNSPEC` | 使用 tc 的預設 action |
| `TC_ACT_REDIRECT` | 重導（用 `bpf_redirect()`）|
| `TC_ACT_STOLEN` | skb 被接管（不再 free）|

## 完整範例：TCP 流量標記

```c
/* tc_mark.bpf.c — 根據 dest port 標記 TCP 流量的 priority */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>
#include <linux/pkt_cls.h>

SEC("tc")
int tc_mark_traffic(struct __sk_buff *skb)
{
    void *data_end = (void *)(long)skb->data_end;
    void *data     = (void *)(long)skb->data;

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end) return TC_ACT_OK;
    if (bpf_ntohs(eth->h_proto) != ETH_P_IP) return TC_ACT_OK;

    struct iphdr *iph = (void *)(eth + 1);
    if ((void *)(iph + 1) > data_end) return TC_ACT_OK;
    if (iph->protocol != IPPROTO_TCP) return TC_ACT_OK;

    /* 計算 IP header 長度（IHL 欄位 × 4）*/
    int iph_len = iph->ihl * 4;
    struct tcphdr *tcp = (void *)iph + iph_len;
    if ((void *)(tcp + 1) > data_end) return TC_ACT_OK;

    u16 dport = bpf_ntohs(tcp->dest);

    /* 根據 dest port 設定 priority */
    if (dport == 443)       /* HTTPS：高優先 */
        skb->priority = 1;
    else if (dport == 3306) /* MySQL：中優先 */
        skb->priority = 4;
    else
        skb->priority = 7;  /* 其他：低優先 */

    return TC_ACT_OK;
}

char LICENSE[] SEC("license") = "GPL";
```

## Socket Redirect：TC + sockmap

TC BPF 和 sockmap 結合可以做 socket-level redirect（讓兩個 socket 之間的資料繞過 networking stack）：

```c
/* 把進來的封包 redirect 到 sockmap 裡的 socket */
struct {
    __uint(type, BPF_MAP_TYPE_SOCKHASH);
    /* ... */
} sock_map SEC(".maps");

SEC("tc")
int tc_redirect(struct __sk_buff *skb)
{
    /* 用 4-tuple 查找目標 socket */
    struct bpf_sock_tuple tuple = {};
    tuple.ipv4.saddr = skb->remote_ip4;
    tuple.ipv4.daddr = skb->local_ip4;
    tuple.ipv4.sport = skb->remote_port;
    tuple.ipv4.dport = skb->local_port;

    return bpf_sk_redirect_hash(skb, &sock_map, &tuple, BPF_F_INGRESS);
}
```

詳細的 sockmap 用法在 [Ch 29](./29-socket-bpf.md)。

## XDP + TC BPF 的組合

實際生產系統通常 XDP 和 TC BPF 組合使用：

```
XDP（ingress）：做快速的 DROP（DDoS 防護、黑名單過濾）
TC ingress：   做細緻的封包修改（header rewrite、DSCP 標記）
TC egress：    做出口的流量整形（rate limiting、priority queue）
```

Cilium 就是這樣設計的：XDP 做 L3 policy 的快速路徑，TC 做完整的 L4+ policy。

## 踩雷集錦

1. **忘記建立 clsact qdisc**：`tc filter add` 如果沒有先 `qdisc add clsact`，會得到 "qdisc not found" 錯誤

2. **TC BPF 的 `data`/`data_end` 存取方式和 XDP 一樣，都需要 bounds check**：verifier 同樣強制要求

3. **`priority` 欄位在 egress 才有意義**：在 ingress 設定 `skb->priority` 對 qdisc 的 scheduling 沒有效果；egress 才看這個欄位

4. **`TC_ACT_OK` 而不是 `0`**：回傳 `0` 在 TC BPF 是 `TC_ACT_UNSPEC`（讓上層 qdisc 決定），不是 `TC_ACT_OK`；永遠用 named constant

5. **libbpf 的 TC attach API**：`bpf_program__attach_tc_ingress` / `_egress` 是新的 API（libbpf 0.7+）；舊版本需要手動用 netlink

## 動手練習

1. 用 TC BPF 在 egress 把所有 TCP port 80 的流量的 `mark` 設成 1，然後用 `ip rule`/`ip route` 設定 policy routing，讓 mark=1 的流量走另一個路由表

2. 用 TC BPF 計算每個介面的流量（bytes/packets），每秒輸出統計

## 本章重點整理

- TC BPF 在 networking stack 的 traffic control 層執行，比 XDP 更晚但能存取更多 metadata
- clsact qdisc 支援 ingress 和 egress；direct-action mode 讓 BPF program 直接返回 action
- `__sk_buff` 提供豐富的封包 metadata（protocol、mark、priority、socket info）
- XDP + TC 組合：XDP 做快速 DROP，TC 做細緻策略

## 自我檢核

- [ ] 能說出 TC BPF 和 XDP 在 networking stack 中的位置，以及各自的優缺點
- [ ] 知道 clsact qdisc 和 direct-action 的作用
- [ ] 能解釋 `TC_ACT_OK`、`TC_ACT_SHOT`、`TC_ACT_REDIRECT` 的語意

→ [Ch 29 Socket BPF：sk_msg, sk_skb, sockmap](./29-socket-bpf.md)
