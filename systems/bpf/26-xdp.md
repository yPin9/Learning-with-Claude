# Ch 26 — XDP：最快的 packet 處理

> **目標**：理解 XDP（eXpress Data Path）的架構——在哪個 kernel 層執行、三種 attach mode（native/offload/generic）的差異、所有的 XDP action、以及如何用 BPF maps 實作有狀態的封包過濾。

## XDP 的定位：封包進入 kernel 之前

```
封包進入路徑（由快到慢）：

NIC hardware → NIC driver
                  │ ← XDP（native mode：最快）
                  ↓
              sk_buff 分配（記憶體分配，不小的 overhead）
                  │ ← XDP（generic mode：較慢，不需要 driver 支援）
                  ↓
              Network stack（L2→L3→L4 解析）
                  │ ← TC BPF（ingress）
                  ↓
              Socket receive buffer
                  │ ← Socket filter
                  ↓
              Application recv()
```

XDP 的核心優勢：在 sk_buff 分配之前執行，省掉了大量的記憶體分配和 networking stack overhead。

**效能對比**（x86-64，單核）：
- 普通 networking stack：約 2–5 Mpps（million packets per second）
- TC BPF：約 5–15 Mpps
- XDP generic：約 5–15 Mpps
- XDP native：約 20–40 Mpps
- XDP offload（SmartNIC）：100+ Mpps

## XDP Context：`struct xdp_md`

```c
struct xdp_md {
    __u32 data;       /* 封包資料的開始地址（物理）*/
    __u32 data_end;   /* 封包資料的結束地址 */
    __u32 data_meta;  /* metadata 空間的起始（在 data 之前）*/
    __u32 ingress_ifindex;  /* 接收封包的介面 index */
    __u32 rx_queue_index;   /* 接收的 queue index */
    __u32 egress_ifindex;   /* XDP_REDIRECT 的目標介面（只在 redirect 時有效）*/
};
```

存取封包資料：

```c
void *data     = (void *)(long)ctx->data;
void *data_end = (void *)(long)ctx->data_end;

/* 必須做 bounds check，verifier 強制要求 */
struct ethhdr *eth = data;
if ((void *)(eth + 1) > data_end)
    return XDP_PASS;

/* 現在可以安全存取 eth 的欄位 */
u16 proto = bpf_ntohs(eth->h_proto);
```

## XDP Actions

| Action | 說明 |
|---|---|
| `XDP_PASS` | 讓封包繼續進入 kernel networking stack |
| `XDP_DROP` | 直接丟棄封包（不送給任何 handler）|
| `XDP_TX` | 把封包從同一個網卡送回去（例如 ICMP reply）|
| `XDP_REDIRECT` | 把封包重導到另一個網卡或 AF_XDP socket |
| `XDP_ABORTED` | 程式錯誤（記錄到 trace，丟棄封包）|

## 完整範例：IP 黑名單過濾器

```c
/* xdp_filter.bpf.c */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

/* 黑名單 map：被封鎖的 IPv4 source address */
struct {
    __uint(type, BPF_MAP_TYPE_LRU_HASH);
    __uint(max_entries, 10000);
    __type(key, __be32);    /* IPv4 address（big-endian）*/
    __type(value, u64);     /* drop count */
} blocked_ips SEC(".maps");

/* 統計 */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 2);
    __type(key, u32);
    __type(value, u64);
} stats SEC(".maps");

#define STAT_PASS 0
#define STAT_DROP 1

static __always_inline void inc_stat(u32 idx)
{
    u64 *cnt = bpf_map_lookup_elem(&stats, &idx);
    if (cnt) (*cnt)++;
}

SEC("xdp")
int xdp_ip_filter(struct xdp_md *ctx)
{
    void *data_end = (void *)(long)ctx->data_end;
    void *data     = (void *)(long)ctx->data;

    /* Parse Ethernet header */
    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return XDP_PASS;

    /* 只過濾 IPv4 */
    if (bpf_ntohs(eth->h_proto) != ETH_P_IP) {
        inc_stat(STAT_PASS);
        return XDP_PASS;
    }

    /* Parse IP header */
    struct iphdr *iph = (void *)(eth + 1);
    if ((void *)(iph + 1) > data_end)
        return XDP_PASS;

    /* 查黑名單 */
    __be32 src = iph->saddr;
    u64 *drop_cnt = bpf_map_lookup_elem(&blocked_ips, &src);
    if (drop_cnt) {
        __sync_fetch_and_add(drop_cnt, 1);
        inc_stat(STAT_DROP);
        return XDP_DROP;
    }

    inc_stat(STAT_PASS);
    return XDP_PASS;
}

char LICENSE[] SEC("license") = "GPL";
```

**Userspace：動態更新黑名單**：

```c
/* userspace 動態加入 / 移除封鎖的 IP */
int block_ip(int map_fd, uint32_t ip_be)
{
    uint64_t drop_cnt = 0;
    return bpf_map_update_elem(map_fd, &ip_be, &drop_cnt, BPF_ANY);
}

int unblock_ip(int map_fd, uint32_t ip_be)
{
    return bpf_map_delete_elem(map_fd, &ip_be);
}
```

## Attach 方式

```bash
# 用 ip 命令 attach（native mode）
sudo ip link set dev eth0 xdp obj xdp_filter.bpf.o sec xdp
sudo ip link set dev eth0 xdp off  # detach

# 指定 generic mode
sudo ip link set dev eth0 xdpgeneric obj xdp_filter.bpf.o sec xdp

# 查看目前 attached 的 XDP program
ip link show dev eth0 | grep xdp

# 用 libbpf API
struct bpf_link *link = bpf_program__attach_xdp(prog, ifindex);
```

## 修改封包：重寫 header

XDP 不只能丟棄封包，也能修改封包內容：

```c
SEC("xdp")
int xdp_rewrite_ttl(struct xdp_md *ctx)
{
    void *data_end = (void *)(long)ctx->data_end;
    void *data     = (void *)(long)ctx->data;

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end) return XDP_PASS;
    if (bpf_ntohs(eth->h_proto) != ETH_P_IP) return XDP_PASS;

    struct iphdr *iph = (void *)(eth + 1);
    if ((void *)(iph + 1) > data_end) return XDP_PASS;

    /* 修改 TTL，需要同時修改 checksum */
    u8 old_ttl = iph->ttl;
    iph->ttl = 64;

    /* 更新 checksum（incremental update，不用重算整個 checksum）*/
    bpf_l3_csum_replace(ctx, sizeof(*eth) + offsetof(struct iphdr, check),
                        old_ttl, (u64)iph->ttl, sizeof(u8));

    return XDP_PASS;
}
```

## XDP 的 Metadata Space

在 `ctx->data` 之前有一段 metadata 空間（`ctx->data_meta`），XDP 程式可以在這裡存放 metadata，讓後面的 TC BPF 或 socket 程式讀取：

```c
SEC("xdp")
int add_metadata(struct xdp_md *ctx)
{
    /* 擴展 headroom 作為 metadata 空間 */
    if (bpf_xdp_adjust_meta(ctx, -(int)sizeof(u32)) != 0)
        return XDP_PASS;

    /* 現在 ctx->data_meta 到 ctx->data 之間有 4 bytes 空間 */
    u32 *meta = (u32 *)(long)ctx->data_meta;
    if ((void *)(meta + 1) > (void *)(long)ctx->data)
        return XDP_PASS;

    *meta = bpf_ktime_get_ns();  /* 存放進入時間 */
    return XDP_PASS;
}
```

## 踩雷集錦

1. **bounds check 忘記做**：verifier 強制要求所有 packet access 都有 bounds check；`if ((void *)(hdr + 1) > data_end) return XDP_PASS;` 不能省

2. **VLAN tag 讓 ETH_P_IP 比較失敗**：有 VLAN tag 的封包，Ethernet type 是 `ETH_P_8021Q`；需要先 parse VLAN header

3. **XDP_REDIRECT 需要設定 DEVMAP**：用 `bpf_redirect(ifindex, 0)` 重導到另一個介面，但如果目標介面不支援 XDP，會 fallback 到 generic mode（效能差）

4. **修改封包後 checksum 必須更新**：修改 IP 或 TCP header 的任何欄位都要更新對應的 checksum；用 `bpf_l3_csum_replace` 和 `bpf_l4_csum_replace`

5. **generic mode 不在 driver 層執行**：XDP generic 在 networking stack 裡的 `netif_receive_skb` 執行，已經有 sk_buff，效能沒有 native 那麼好，且不能用某些 action（如 `XDP_TX`）

## 動手練習

1. 用 XDP 實作一個 per-IP rate limiter：每秒超過 N 個封包的 source IP 自動被 drop 1 秒（提示：用 LRU_HASH 儲存 `{ip, last_seen_ns, pkt_count}`）

2. 用 `ip link show` 確認 XDP program 已 attach，查看 program id；用 `bpftool prog show id <id>` 查看 run_cnt

3. 在 loopback interface 上 attach XDP（generic mode），用 `ping localhost` 觸發，確認 run_cnt 增加

## 本章重點整理

- XDP 在 NIC driver 層執行（native mode），在 sk_buff 分配前處理封包，是 Linux 最快的 packet processing
- 所有 packet access 必須做 bounds check（`data` 到 `data_end` 之間）
- XDP action：PASS / DROP / TX / REDIRECT / ABORTED
- 修改封包內容後必須更新 checksum

## 自我檢核

- [ ] 能解釋 native XDP、generic XDP、TC BPF 在 networking stack 的位置和效能差異
- [ ] 知道為什麼 XDP 需要 bounds check，以及 verifier 如何驗證
- [ ] 能說出修改封包 IP header 後需要做什麼（checksum 更新）

→ [Ch 27 AF_XDP：零拷貝 userspace packet I/O](./27-af-xdp.md)
