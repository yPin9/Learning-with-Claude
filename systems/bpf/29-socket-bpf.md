# Ch 29 — Socket BPF：sk_msg, sk_skb, sockmap

> **目標**：理解 socket-level BPF 的三個主要 program type（sk_msg / sk_skb / socket_filter）和兩種 map（SOCKMAP / SOCKHASH）——如何實現 socket redirection，讓兩個 socket 之間的資料繞過 networking stack。

## 為什麼需要 Socket-level BPF？

傳統的 proxy 流量（例如 sidecar proxy 在 localhost 收發）：

```
Client → kernel networking stack → sidecar proxy（localhost）
       → kernel networking stack → backend service

每次「跨越 networking stack」都有 overhead（copy、checksum、socket buffer）
```

Socket-level BPF 的解法：

```
Client → sockmap redirect → backend service
         （不過 networking stack，直接在 socket 之間轉發）
```

Cilium 的 local traffic acceleration 和 Envoy sidecar bypass 都是這樣做的。

## Sockmap 和 Sockhash

SOCKMAP 和 SOCKHASH 存儲 socket reference：

```c
/* SOCKMAP：array 形式，key 是 index */
struct {
    __uint(type, BPF_MAP_TYPE_SOCKMAP);
    __uint(max_entries, 65536);
    __type(key, u32);
    __type(value, u32);
} sock_map SEC(".maps");

/* SOCKHASH：hash 形式，可以用 4-tuple 作為 key */
struct {
    __uint(type, BPF_MAP_TYPE_SOCKHASH);
    __uint(max_entries, 65536);
    __type(key, struct bpf_sock_tuple);
    __type(value, u32);
} sock_hash SEC(".maps");
```

## sk_skb：從 socket 收到的 packet 的 redirect

`SK_SKB` program 在 socket 收到資料時執行（在 socket buffer 入隊之前），可以用來把資料 redirect 到另一個 socket：

```c
/* sk_skb 的兩種 subtype */

/* SK_SKB_STREAM_PARSER：解析 stream，找到完整的 message */
/* 通常用於 parsing length-prefixed protocols（TLS、HTTP/2）*/
SEC("sk_skb/stream_parser")
int my_parser(struct __sk_buff *skb)
{
    /* 返回 message 的長度，告訴 kernel 何時算「一個完整 message」*/
    return skb->len;  /* 對於非 framing protocol，返回全部長度 */
}

/* SK_SKB_STREAM_VERDICT：決定 message 的 fate */
SEC("sk_skb/stream_verdict")
int my_verdict(struct __sk_buff *skb)
{
    /* 查找目標 socket */
    struct bpf_sock_tuple key = {
        .ipv4.saddr = skb->remote_ip4,
        .ipv4.daddr = skb->local_ip4,
        .ipv4.sport = skb->remote_port,
        .ipv4.dport = skb->local_port,
    };

    /* redirect 到 sockhash 裡的 socket */
    return bpf_sk_redirect_hash(skb, &sock_hash, &key, BPF_F_INGRESS);
    /* 如果找不到，回傳 SK_PASS 讓封包繼續正常處理 */
}
```

**把 socket 放入 sockmap**（userspace）：

```c
/* 把 accept 得到的 socket fd 放入 sockhash */
int accepted_fd = accept(listen_fd, ...);

/* 建立 4-tuple key */
struct bpf_sock_tuple key = { ... };
bpf_map_update_elem(sockhash_fd, &key, &accepted_fd, BPF_ANY);
```

## sk_msg：發送資料時的 redirect

`SK_MSG` program 在 process 呼叫 `sendmsg()`/`sendfile()` 時執行，攔截要發送的資料：

```c
SEC("sk_msg")
int my_msg_verdict(struct sk_msg_md *msg)
{
    /* 查找目標 socket，做 local redirect（繞過 networking stack）*/
    struct bpf_sock_tuple key = {
        .ipv4.saddr = msg->local_ip4,
        .ipv4.daddr = msg->remote_ip4,
        .ipv4.sport = msg->local_port,
        .ipv4.dport = msg->remote_port >> 16,
    };

    return bpf_msg_redirect_hash(msg, &sock_hash, &key, BPF_F_INGRESS);
    /* SK_PASS = 讓資料正常發送；SK_DROP = 丟棄 */
}
```

## socket_filter：legacy 的 socket 過濾

這是最早的 socket-level BPF，直接 attach 到 socket 的 `SO_ATTACH_BPF`：

```c
SEC("socket")
int my_filter(struct __sk_buff *skb)
{
    /* 過濾：只保留 TCP */
    if (skb->protocol != bpf_htons(ETH_P_IP))
        return 0;  /* 0 = 丟棄 */

    /* 讀取 IP header */
    void *data = (void *)(long)skb->data;
    /* ... bounds check + parse ... */

    return skb->len;  /* 非 0 = 接受，值是截斷後的長度 */
}
```

在 userspace attach：

```c
int sock = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
int prog_fd = /* load BPF program */;
setsockopt(sock, SOL_SOCKET, SO_ATTACH_BPF, &prog_fd, sizeof(prog_fd));
```

## 實際場景：透明 Proxy（Envoy sidecar bypass）

Cilium 的 socket-level acceleration 原理：

```
App → sendmsg() → SK_MSG BPF program
                    │ 查 sockhash 找到 Envoy 的 socket
                    └── bpf_msg_redirect_hash → Envoy 直接收到資料
                         （不需要繞過 loopback 的整個 TCP stack）
```

```c
/* 簡化的 Cilium socket acceleration 邏輯 */
SEC("sk_msg")
int sock_sendmsg(struct sk_msg_md *msg)
{
    /* 查找這個 connection 是否有對應的 proxy socket */
    struct {
        u32 sip; u32 dip; u16 sport; u16 dport;
    } key = {
        .sip = msg->local_ip4, .dip = msg->remote_ip4,
        .sport = msg->local_port, .dport = msg->remote_port >> 16,
    };

    /* 如果找到 proxy socket，redirect；否則正常發送 */
    int ret = bpf_msg_redirect_hash(msg, &proxy_map, &key, BPF_F_INGRESS);
    if (ret == SK_PASS && /* lookup 成功 */)
        return SK_PASS;  /* redirect 成功 */
    return SK_PASS;      /* 正常路徑 */
}
```

## 踩雷集錦

1. **sk_skb 需要同時 attach stream_parser 和 stream_verdict**：只 attach 其中一個無效；兩個都要 attach 到同一個 sockmap

2. **放進 sockmap 的 socket 必須是 TCP（對 SOCKMAP）**：SOCKMAP 只支援 TCP/IPv4 和 TCP/IPv6；UDP socket 不能放入

3. **redirect 之後的 skb 不會再走原始的 socket 路徑**：redirect 後的 skb 直接進入目標 socket 的 receive buffer；原始 socket 不會再收到

4. **sockhash 的 key 大小必須和定義時的一致**：`bpf_sk_redirect_hash` 的 key 必須和 SOCKHASH 定義的 `key_type` 大小完全一致

## 動手練習

1. 寫一個最簡單的 sk_skb stream_parser（回傳 `skb->len`）和 stream_verdict（直接回傳 `SK_PASS`），附加到 loopback 的 TCP socket，確認不影響正常流量

2. 讀 Cilium 的 socket-level acceleration 文章（`https://cilium.io/blog/2020/11/10/ebpf-future-of-networking/`），理解它如何用 sk_msg 做 sidecar bypass

## 本章重點整理

- SOCKMAP/SOCKHASH 存儲 socket reference，是 socket redirect 的核心
- sk_skb（stream_parser + verdict）在 socket 收到資料時做 redirect
- sk_msg 在 sendmsg() 時做 redirect，實現 zero-copy local forwarding
- Socket-level BPF 讓 localhost proxy bypass 成為可能（Envoy sidecar bypass）

## 自我檢核

- [ ] 能解釋 sk_skb 的 stream_parser 和 stream_verdict 的角色分工
- [ ] 知道 sk_msg redirect 如何讓兩個 socket 繞過完整的 TCP/IP stack
- [ ] 能說出 sockmap 和 sockhash 的差異

→ [Ch 30 cgroup BPF：容器網路控制](./30-cgroup-bpf.md)
