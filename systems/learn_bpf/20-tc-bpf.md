# Ch 20 — TC BPF：ingress/egress 流量控制

> 目標：搞懂 TC（Traffic Control）BPF 在 packet 路徑上的位置、跟 XDP 的取捨、`__sk_buff` 比 `xdp_md` 多了什麼、TC return code、什麼時候選 TC 而不選 XDP。

## TC 在 packet 路徑哪裡

XDP 在 driver 層，TC 在 kernel network stack **內部**：

```
NIC → driver → XDP → ... → TC ingress → routing → ... → TC egress → driver → NIC
                            ↑                              ↑
                       這裡也能掛 BPF                這裡 XDP 沒有
```

**關鍵差異**：

- TC 在 packet 已經被包成 `sk_buff`（kernel 內部最重要的封包資料結構）後跑
- XDP 在 packet 還是 raw memory 時跑
- **TC 同時支援 ingress 與 egress**（XDP 只 ingress）
- TC 拿到的 `sk_buff` 已經有完整 metadata（routing 決定、conntrack 結果、socket 關聯...）

代價：開銷比 XDP 高（建 sk_buff 不是免費的），但仍遠低於 iptables。

## __sk_buff 比 xdp_md 多什麼

`__sk_buff` 是 kernel `sk_buff` 的 BPF view（不是直接給你 raw struct，而是 kernel 暴露的安全 subset）：

```c
struct __sk_buff {
    __u32 len;              // packet 長度
    __u32 pkt_type;         // host/broadcast/multicast...
    __u32 mark;             // socket mark
    __u32 queue_mapping;
    __u32 protocol;         // L3 protocol
    __u32 vlan_present;
    __u32 vlan_tci;
    __u32 vlan_proto;
    __u32 priority;
    __u32 ingress_ifindex;
    __u32 ifindex;
    __u32 tc_index;
    __u32 cb[5];            // BPF 自由用的 32-byte
    __u32 hash;
    __u32 tc_classid;
    __u32 data;
    __u32 data_end;
    __u32 napi_id;
    __u32 family;
    __u32 remote_ip4;       // L4 connection info
    __u32 local_ip4;
    __u32 remote_ip6[4];
    __u32 local_ip6[4];
    __u32 remote_port;
    __u32 local_port;
    /* ... */
};
```

對比 `xdp_md` 只有 `data / data_end / data_meta / ingress_ifindex / rx_queue_index` — TC 多了一堆現成 metadata，少做很多解析。

## TC return action

| 回傳值 | 動作 |
|---|---|
| `TC_ACT_OK` (0) | 繼續處理（最常用） |
| `TC_ACT_SHOT` (2) | 丟掉 |
| `TC_ACT_STOLEN` (4) | BPF 接管，kernel 不再處理 |
| `TC_ACT_REDIRECT` (7) | redirect 到別的 device |
| `TC_ACT_UNSPEC` (-1) | 「沒意見」，繼續 |

## 第一支 TC BPF

寫 `tc_drop_icmp.bpf.c`：

```c
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/in.h>
#include <linux/pkt_cls.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

SEC("tc")
int drop_icmp(struct __sk_buff *skb) {
    void *data     = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end) return TC_ACT_OK;
    if (eth->h_proto != bpf_htons(ETH_P_IP)) return TC_ACT_OK;

    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end) return TC_ACT_OK;

    if (ip->protocol == IPPROTO_ICMP) return TC_ACT_SHOT;
    return TC_ACT_OK;
}
```

寫法跟 XDP 幾乎一樣 — bound check pattern 共用。

## Attach 方式

TC 歷史上靠 `tc` command attach：

```bash
sudo tc qdisc add dev lo clsact
sudo tc filter add dev lo ingress bpf obj tc_drop_icmp.bpf.o sec tc
```

5.10+ 有更乾淨的 libbpf API：

```c
LIBBPF_OPTS(bpf_tc_hook, hook, .ifindex = ifindex, .attach_point = BPF_TC_INGRESS);
LIBBPF_OPTS(bpf_tc_opts, opts, .handle = 1, .priority = 1);

bpf_tc_hook_create(&hook);
opts.prog_fd = bpf_program__fd(skel->progs.drop_icmp);
bpf_tc_attach(&hook, &opts);
```

或最現代的 6.6+ 用 `BPF_PROG_TYPE_SCHED_CLS` 直接 link：

```c
bpf_program__attach_tcx(prog, ifindex, NULL);
```

## 直接修改 packet

TC BPF 可以**改 packet 內容**：

```c
SEC("tc")
int rewrite_dst(struct __sk_buff *skb) {
    // ... 解到 ip header ...

    __u32 new_addr = bpf_htonl(0x0A000001);    // 10.0.0.1
    __u32 old_addr = ip->daddr;

    bpf_skb_store_bytes(skb,
        offsetof(struct ethhdr, ...) + offsetof(struct iphdr, daddr),
        &new_addr, sizeof(new_addr), 0);

    bpf_l3_csum_replace(skb, csum_offset, old_addr, new_addr, sizeof(__u32));
    bpf_l4_csum_replace(skb, l4_csum_offset, old_addr, new_addr,
                         BPF_F_PSEUDO_HDR | sizeof(__u32));

    return TC_ACT_OK;
}
```

**XDP 也能改 packet，但 TC 提供更多 helper 來處理 checksum、conntrack 等麻煩事**。

## TC vs XDP 何時選哪個

| 場景 | 選 |
|---|---|
| DDoS drop（追求極致 PPS） | XDP |
| L4 LB（轉發） | XDP（XDP_REDIRECT） |
| Egress filter | TC（XDP 不能） |
| 修改 packet 並繼續走 stack | TC |
| 需要 sk_buff metadata（socket、conntrack） | TC |
| Container network plugin（CNI） | 多用 TC |
| 觀察封包不修改 | TC（更方便） |

**規則記憶**：「快但限制多 → XDP；彈性多但稍慢 → TC」。

## TC ingress vs egress 的對稱性

TC 同一份 BPF code 可以掛 ingress 也可以掛 egress：

```bash
sudo tc filter add dev eth0 ingress bpf obj prog.bpf.o sec tc
sudo tc filter add dev eth0 egress  bpf obj prog.bpf.o sec tc
```

差別只在掛在哪。`skb->ingress_ifindex == 0` 通常表示 egress。

實務上很多 BPF 工具兩邊都掛，做雙向統計或對稱 NAT。

## Cilium 用 TC 做什麼

Cilium 把 TC BPF 當 Kubernetes pod 網路的 dataplane：

- Pod 進出的封包都過 TC BPF
- L3 routing、L4 LB、conntrack、policy enforcement、observability — 全在 BPF
- 完全不依賴 iptables（kube-proxy mode `iptables` 是預設備案）

這個架構在 1000 node、10000 pod 規模上**比 iptables 模式快 10 倍以上**，因為 iptables 規則 O(N) scaling。

## 一個常見誤解

「TC 比 XDP 慢很多，所以能用 XDP 就用 XDP」 — **不對**。

兩者差距只在「處理一個 packet 的開銷」，**對 throughput 100K PPS 以下的 service 完全無感**。選 TC 還是 XDP 主要看：

1. 你需不需要 egress 處理（XDP 沒有）
2. 你需不需要 sk_buff metadata
3. 你的 NIC driver 是否支援 native XDP

別為了 5% 的開銷選 XDP，多開發 50% 時間。

## 動手練習

1. **TC drop ICMP**：寫上面那支，attach 到 lo，ping 測試。
2. **TC egress drop**：在 egress 掛同一支 — ICMP echo reply 出不去，外面 ping 你會 timeout。
3. **`tc -s filter` 看統計**：
   ```bash
   sudo tc -s filter show dev lo ingress
   ```
4. **改 packet**：用 TC 把所有出去的 packet src IP 改成另一個 — 用 wireshark 驗證。

## 自我檢核

- [ ] 我能在 packet 路徑圖上指出 TC 與 XDP 各自位置
- [ ] 我能列出 `__sk_buff` 比 `xdp_md` 多哪些 metadata
- [ ] 我能列出 TC return action 與作用
- [ ] 我知道何時該選 TC、何時該選 XDP
- [ ] 我能解釋為什麼 Cilium 把 TC 當 dataplane

下一章看 socket 層的 BPF — sockops、sk_msg 是 Cilium 加速 service mesh 的祕密武器。

→ [Ch 21 Socket-level BPF：sockops、sk_msg、sock_filter](./21-socket-level-bpf.md)
