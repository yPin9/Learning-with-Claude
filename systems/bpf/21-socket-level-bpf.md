# Ch 21 — Socket-level BPF：sockops、sk_msg、sock_filter

> 目標：認識 socket 層的 BPF 程式類型、sockmap 怎麼讓兩個 socket 的資料直接 splice、為什麼這是 Cilium 加速 service mesh 的核心。

## Socket 層 BPF 的位置

XDP/TC 處理 packet（L2/L3/L4 raw bytes），socket 層 BPF 處理**已經建好的 connection 與 user space 看到的 byte stream**：

```
                     User space app
                           ▲
                           │ recv/send (byte stream)
   ┌───────────────────────┴──────────────────────────┐
   │  Socket layer                                    │
   │   sockops          sk_msg           sk_skb       │ ← 各種 socket BPF
   │   (state events)   (user→TCP)       (TCP→user)   │
   └───────────────────────┬──────────────────────────┘
                           │ TCP / UDP
                           ▼
                       network stack
                           ▼
                          NIC
```

幾種主要 program type：

| Type | 觸發點 | 能做什麼 |
|---|---|---|
| `sockops` | TCP state 事件（accept、connect、retransmit） | 動態調 TCP 參數、加入 sockmap |
| `sk_skb` | socket 收到 packet 時 | redirect 到別的 socket |
| `sk_msg` | user space `send()` 時 | 改 / drop / redirect data |
| `sock_filter` (cBPF) | socket recv | 過濾收進來的 packet（tcpdump 風格） |
| `cgroup_sock` | socket 建立 | per-cgroup 控制 |

## sockmap：核心抽象

`BPF_MAP_TYPE_SOCKMAP` 是這套東西的核心。它是「socket fd 的 map」 — value 是 socket reference。

關鍵能力：**透過 sockmap，BPF 可以把一個 socket 收到的資料直接 splice 到另一個 socket，繞過 user space**。

```
傳統 proxy：
  socket A recv → user space buf → socket B send

BPF + sockmap：
  socket A recv → sk_skb BPF → bpf_sk_redirect_map() → socket B send
                    (kernel 內直接搬運，不過 user space)
```

對 service mesh sidecar（Envoy / Linkerd）這意義巨大：sidecar 進 / 出兩條 connection 的資料**不用 copy 到 user space 再寫回去**，直接 kernel 內 splice。

## 第一個 sockops 範例

```c
SEC("sockops")
int track_tcp(struct bpf_sock_ops *skops) {
    switch (skops->op) {
    case BPF_SOCK_OPS_TCP_CONNECT_CB:
        bpf_printk("TCP connecting to %x:%d\n",
                   skops->remote_ip4, skops->remote_port);
        break;
    case BPF_SOCK_OPS_PASSIVE_ESTABLISHED_CB:
        bpf_printk("Accepted connection\n");
        break;
    case BPF_SOCK_OPS_RTT_CB:
        bpf_printk("RTT updated\n");
        break;
    }
    return 0;
}
```

attach 到 cgroup：

```bash
sudo bpftool prog load track_tcp.bpf.o /sys/fs/bpf/track_tcp
sudo bpftool cgroup attach /sys/fs/cgroup/ sock_ops pinned /sys/fs/bpf/track_tcp
```

每個 socket 事件都會跑一次。常見用法：

- TCP 參數調整：`bpf_setsockopt(skops, SOL_TCP, TCP_NODELAY, ...)`
- 註冊 socket 到 sockmap（為 sk_msg / sk_skb 做準備）
- 觀測：connection 建立 / 銷毀 / RTT 變化都是事件

## sk_msg：在 send 時介入

```c
struct {
    __uint(type, BPF_MAP_TYPE_SOCKHASH);
    __type(key, __u32);
    __type(value, __u64);
    __uint(max_entries, 65536);
} sock_map SEC(".maps");

SEC("sk_msg")
int redirect_msg(struct sk_msg_md *msg) {
    __u32 key = ...;   // 算出 redirect 目標的 key
    return bpf_msg_redirect_hash(msg, &sock_map, &key, BPF_F_INGRESS);
}
```

當 user space 對 socket A `send()` 時，`sk_msg` BPF 跑 — 可以查 sock_map 找到 socket B、把資料**直接送到 B 的 recv queue**，繞過 TCP 重新封包。

**這就是 Cilium「sidecar acceleration」的本質**：service mesh 兩個 socket（A → B 跟 B → C）之間的資料直接 splice，不用過 TCP/IP stack。延遲可以從 100us 級降到 10us 級。

## sock_filter (legacy cBPF)

最老的 socket BPF — `tcpdump` 用的就是這個。寫 cBPF（不是 eBPF）：

```c
struct sock_filter filter[] = {
    BPF_STMT(BPF_LD | BPF_W | BPF_ABS, 12),
    BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, ETH_P_IP, 0, 1),
    BPF_STMT(BPF_RET | BPF_K, 0xFFFFFFFF),
    BPF_STMT(BPF_RET | BPF_K, 0),
};

setsockopt(sock, SOL_SOCKET, SO_ATTACH_FILTER, &filter, sizeof(filter));
```

寫法古老，但 raw socket / packet socket 仍大量在用。新專案應該用 eBPF socket filter type 取代。

## cgroup_sock — per-cgroup socket 控制

attach 到 cgroup，每個 socket 建立時跑：

```c
SEC("cgroup/sock_create")
int restrict_sock(struct bpf_sock *sk) {
    if (sk->family == AF_INET6) return 0;   // 不准 IPv6
    return 1;
}
```

容器 sandbox / Kubernetes NetworkPolicy 的底層機制之一。

## Cilium 用 socket BPF 做什麼

整理一下 Cilium 在 socket 層的 BPF：

1. **Service redirect**：user space `connect("clusterIP:port")` → cgroup BPF 改成直接 `connect("backend pod IP")`，省掉 kube-proxy 的 NAT
2. **Sidecar acceleration**：app ↔ sidecar 之間用 sockmap splice
3. **Observability**：sockops 抓 connection 生命週期事件給 Hubble

這套機制讓 Cilium 在大規模 cluster 上比 iptables 模式快數倍。

## 一個常見誤解

「socket BPF 跟 TC BPF 是同一件事」 — **不全然**。

TC 處理 raw packet（`__sk_buff` 是 packet 的 view），socket BPF 處理 socket 層級的事件與 byte stream。兩者掛點完全不同：
- packet 從 NIC 進來：先過 XDP/TC
- 變成 socket 的 byte stream 後：socket BPF 才看得到

實務上一個 BPF 系統會兩種都用 — packet 層做 routing/filter，socket 層做 connection 級別的優化。

## 動手練習

1. **跑 sockops 範例**：寫個簡單 sockops 印 TCP 事件，attach 到 root cgroup，curl 一個網址看 trace_pipe 輸出。
2. **看 Cilium 範例**：clone Cilium repo，看 `bpf/sockops/bpf_sockops.c` — 是 production 級的 sockops 程式碼。
3. **量延遲**：用 `bpftrace` 抓 `BPF_SOCK_OPS_RTT_CB`，記每個 connection 的 RTT 變化。

## 自我檢核

- [ ] 我能在 packet 路徑圖指出 socket BPF 在哪
- [ ] 我能說出 sockmap 是什麼、為什麼讓 splice 變可能
- [ ] 我能解釋 sk_msg 如何加速 service mesh
- [ ] 我能說出 sockops 五個常見事件
- [ ] 我知道 cgroup_sock 用在哪些場景

下一站練習 D：寫一個 XDP 防火牆，把 LPM trie blocklist + perf event 統計整合起來。

→ [練習 D：XDP 防火牆](./practice-d-xdp-firewall.md)
