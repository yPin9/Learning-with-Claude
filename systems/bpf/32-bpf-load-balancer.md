# Ch 32 — BPF Load Balancer 設計

> **目標**：理解用 eBPF 實作 L4 load balancer 的設計模式——consistent hashing in BPF maps、DNAT 的實作、connection tracking、以及 Maglev 演算法在 BPF 裡的應用。

## BPF L4 Load Balancer 的架構

```
client → [BPF LB]（XDP 或 TC）→ backend-1
                               → backend-2
                               → backend-3

BPF LB 做的事：
1. 解析 L4 header（TCP/UDP 4-tuple）
2. 用 consistent hash 選擇 backend
3. 做 DNAT（修改 dest IP 和 port）
4. 記錄 connection state（保證同一 connection 總到同一個 backend）
5. 轉發封包（XDP_TX 或 bpf_redirect）
```

## Service Map 設計

```c
/* service 定義 */
struct service_key {
    __be32  vip;          /* virtual IP */
    __be16  vport;        /* virtual port */
    __u8    proto;        /* TCP or UDP */
};

struct service_value {
    __u32   count;        /* backend 數量 */
    __u32   rev_nat_index;/* reverse NAT table index */
};

/* backend 定義 */
struct backend_key {
    __u32   service_id;   /* service id */
    __u32   slot;         /* 0-indexed slot */
};

struct backend_value {
    __be32  ip;
    __be16  port;
    __u8    state;        /* ACTIVE / QUARANTINE / TERMINATING */
};

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 65536);
    __type(key, struct service_key);
    __type(value, struct service_value);
} services SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 65536 * 256);  /* service_count × max_backends */
    __type(key, struct backend_key);
    __type(value, struct backend_value);
} backends SEC(".maps");
```

## Consistent Hashing：讓 Connection 到同一個 Backend

普通的 hash（`hash(src_ip, src_port, dst_ip, dst_port) % N`）在 backend 數量改變時，大部分 connection 會被重新分配——對於有 state 的服務（如資料庫 connection pool）是災難。

**Rendezvous hashing（HRW）**：

```c
static __always_inline __u32
select_backend(struct service_value *svc, struct flow_key *flow)
{
    __u32 best_slot = 0;
    __u32 best_hash = 0;

    /* 對每個 backend slot 計算 hash(flow + slot)，選最大的 */
    #pragma unroll
    for (__u32 slot = 0; slot < MAX_BACKENDS; slot++) {
        if (slot >= svc->count) break;

        __u32 h = jhash2((__u32 *)flow, sizeof(*flow) / 4, slot);
        if (h > best_hash) {
            best_hash = h;
            best_slot = slot;
        }
    }

    return best_slot;
}
```

BPF 裡的 unroll 限制（100 iterations 左右），所以 MAX_BACKENDS 有上限。Cilium 用 Maglev 演算法解決這個問題。

## Maglev 演算法

Maglev 是 Google 的 consistent hashing 演算法，特點是：
- 預先計算一個大的 lookup table（M 個 slot，M 通常是 65537）
- 每個 slot 映射到一個 backend
- Lookup 是 O(1）：`backend = table[hash(flow) % M]`
- 添加/移除 backend 只影響最少量的 slot

```c
/* Maglev lookup（簡化）*/
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 65537);  /* Maglev M */
    __type(key, __u32);
    __type(value, __u32);        /* backend id */
} maglev_table SEC(".maps");

static __always_inline __u32
maglev_lookup(__u32 flow_hash)
{
    __u32 slot = flow_hash % 65537;
    __u32 *backend_id = bpf_map_lookup_elem(&maglev_table, &slot);
    return backend_id ? *backend_id : 0;
}
```

Maglev table 在 userspace 計算，寫入 BPF map；BPF 程式只做 O(1) lookup。

## Connection Tracking

為保證同一 TCP connection 到同一個 backend（TCP 三次握手後的後續包也必須到同一個 backend）：

```c
struct ct_key {
    __be32 src_ip, dst_ip;
    __be16 src_port, dst_port;
    __u8   proto;
};

struct ct_entry {
    __u32  backend_id;
    __u64  last_seen_ns;  /* 用於 LRU eviction */
};

struct {
    __uint(type, BPF_MAP_TYPE_LRU_HASH);
    __uint(max_entries, 1 << 20);  /* 1M connections */
    __type(key, struct ct_key);
    __type(value, struct ct_entry);
} ct_map SEC(".maps");

/* LB 主程式（簡化）*/
SEC("xdp")
int lb_main(struct xdp_md *ctx)
{
    /* ... parse headers ... */

    struct ct_key ckey = { ... };  /* 4-tuple */

    /* 先查 connection table */
    struct ct_entry *ct = bpf_map_lookup_elem(&ct_map, &ckey);
    __u32 backend_id;

    if (ct) {
        /* 已有 connection，用之前的 backend */
        backend_id = ct->backend_id;
        ct->last_seen_ns = bpf_ktime_get_ns();
    } else {
        /* 新 connection，做 load balancing */
        backend_id = maglev_lookup(jhash(&ckey, sizeof(ckey), 0));

        /* 儲存到 connection table */
        struct ct_entry new_entry = {
            .backend_id = backend_id,
            .last_seen_ns = bpf_ktime_get_ns(),
        };
        bpf_map_update_elem(&ct_map, &ckey, &new_entry, BPF_ANY);
    }

    /* 做 DNAT */
    struct backend_value *be = get_backend(backend_id);
    if (!be) return XDP_PASS;

    /* 修改 dest IP 和 port */
    iph->daddr = be->ip;
    tcph->dest = be->port;

    /* 更新 checksum */
    /* bpf_l3_csum_replace / bpf_l4_csum_replace */

    return XDP_TX;  /* 發回去（假設 router 會轉給 backend）*/
}
```

## 踩雷集錦

1. **LRU_HASH 在高並發下有 lock contention**：1M connections 的 LRU map 在很多 CPU 並發存取時效能會退化；考慮用 PERCPU_HASH（但需要自己管理 eviction）

2. **Checksum 更新必須做**：修改 IP 或 TCP header 的任何欄位都要更新 checksum；忘記更新會讓封包被接收端丟棄

3. **Connection table 的 TTL 管理**：LRU_HASH 會自動 evict 舊 entry，但在低流量的情況下，舊 entry 可能長時間不被 evict；可以用 bpf_timer 週期性清理

4. **XDP_TX 需要網路卡支援**：不是所有 NIC 的 XDP native mode 都支援 `XDP_TX`；generic mode 可以，但效能差

## 動手練習

1. 實作一個最簡單的 BPF round-robin LB：有 3 個 backend（hardcoded IP），輪流選擇（用 PERCPU_ARRAY 的 counter 做 round-robin），attach 到 lo，用 nc 測試

2. 把上面的 LB 加入 connection tracking，確認同一個 TCP connection 的所有封包到同一個 backend

## 本章重點整理

- BPF LB 的核心：service map + backend selection（consistent hash）+ DNAT + connection tracking
- Maglev 的 O(1) lookup 透過預計算的大型 table 實現
- Connection tracking（LRU_HASH）保證同一 TCP connection 到同一個 backend
- XDP 是 LB 的最佳 attach point（最快），TC 也可以做（更多功能）

→ [Ch 33 BPF 與 Service Mesh](./33-bpf-service-mesh.md)
