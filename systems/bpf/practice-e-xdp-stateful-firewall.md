# 練習 E — XDP Stateful Firewall

> **目標**：整合 Ch 26–32 的 networking 知識，用 XDP + BPF maps 實作一個完整的 stateful IPv4 防火牆，包含：connection tracking、per-IP rate limiting、動態黑名單，以及 userspace 的管理介面。

## 背景與動機

Stateless 防火牆（只看每個封包的 src/dst IP 和 port）很容易繞過。Stateful 防火牆追蹤 TCP 連線狀態（SYN / ESTABLISHED / FIN / RESET），只允許 established connection 的後續封包通過。加上 rate limiting 可以防 SYN flood。

這個練習的成品可以作為實際的 edge firewall：每秒能處理幾百萬個封包，比 iptables 快 10x，管理介面透過 BPF map 更新不需要 reload rules。

## 任務規格

**功能需求**：

1. **Connection tracking（TCP）**：只允許 established connection 的封包（不是第一個 SYN）通過；新 connection 的 SYN 封包根據 policy 決定

2. **Per-IP SYN rate limiting**：每個 source IP 每秒最多允許 N 個新 TCP connection（可配置）；超過閾值的 SYN 封包 DROP 並把 IP 加入臨時黑名單

3. **靜態黑名單**：userspace 可以用 BPF map 動態加入/移除封鎖的 IP（立即生效，不需要 reload）

4. **統計**：per-IP 的 dropped packets count；全域的 pass/drop counter

**技術規格**：
- Program type：XDP（native mode，attach 到你的物理網卡或虛擬介面）
- Maps：LRU_HASH（connection table）、PERCPU_ARRAY（stats）、LRU_HASH（rate limit state）
- 管理介面：一個 userspace C 程式，能 `add-block <ip>`、`remove-block <ip>`、`show-stats`

**驗收標準**：
- `nmap -sS <your-ip>` 做 SYN scan，大部分 SYN 應該被 rate limit 丟棄
- 連接到允許的 port 後，connection 被追蹤，後續封包不需要再過 policy
- userspace 加入 block list 後，來自那個 IP 的封包立刻被丟棄

## 如果你卡住了

1. 先實作最簡單的版本：只有靜態黑名單（`XDP_DROP` if src IP in hash map）
2. 加入統計（PERCPU_ARRAY），確認 counters 正確
3. 再加 connection tracking（LRU_HASH，key 是 4-tuple）
4. 最後加 rate limiting（每個 IP 的 last_seen + count）
5. Checksum 更新：這個 firewall 只做 DROP 不做修改封包，所以不需要更新 checksum

## 實作步驟建議

### Step 1：Kernel-side BPF 結構設計

```c
/* xdp_fw.bpf.c */

/* Maps */
/* 1. 靜態黑名單：ip → blocked（bool）*/
/* 2. Connection table：4-tuple → {state, last_seen} */
/* 3. Rate limit：src_ip → {count, window_start_ns} */
/* 4. Stats：index → count（PERCPU）*/

/* Connection state */
#define CT_STATE_NEW         1
#define CT_STATE_ESTABLISHED 2
#define CT_STATE_FIN         3

/* Stats index */
#define STAT_PASS    0
#define STAT_DROP    1
#define STAT_CT_NEW  2
#define STAT_RATELIM 3
```

### Step 2：Header parsing helper

```c
/* 解析 Ethernet → IPv4 → TCP/UDP */
static __always_inline int
parse_headers(struct xdp_md *ctx,
              struct ethhdr **eth_out,
              struct iphdr  **iph_out,
              struct tcphdr **tcp_out)
{
    void *data_end = (void *)(long)ctx->data_end;
    void *data     = (void *)(long)ctx->data;

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end) return -1;
    if (bpf_ntohs(eth->h_proto) != ETH_P_IP) return -1;
    *eth_out = eth;

    struct iphdr *iph = (void *)(eth + 1);
    if ((void *)(iph + 1) > data_end) return -1;
    *iph_out = iph;

    if (iph->protocol != IPPROTO_TCP) return 1;  /* non-TCP: pass */

    struct tcphdr *tcp = (void *)iph + (iph->ihl * 4);
    if ((void *)(tcp + 1) > data_end) return -1;
    *tcp_out = tcp;
    return 0;
}
```

### Step 3：主程式邏輯

```c
SEC("xdp")
int xdp_firewall(struct xdp_md *ctx)
{
    struct ethhdr *eth = NULL;
    struct iphdr  *iph = NULL;
    struct tcphdr *tcp = NULL;

    int ret = parse_headers(ctx, &eth, &iph, &tcp);
    if (ret < 0) return XDP_PASS;  /* parse error = pass */
    if (ret == 1) {
        /* Non-TCP：只做黑名單檢查 */
        /* ... */
        return XDP_PASS;
    }

    __be32 src = iph->saddr;

    /* Step 1：靜態黑名單 */
    /* Step 2：Rate limiting（SYN 封包）*/
    /* Step 3：Connection tracking */
    /* Step 4：Policy decision */
    /* Step 5：更新統計 */
}
```

### Step 4：Userspace 管理程式（驗收：能 add/remove IP）

```c
/* fw_ctl.c */
/* 命令列介面：
   ./fw_ctl add-block 192.168.1.100
   ./fw_ctl remove-block 192.168.1.100
   ./fw_ctl show-stats
   ./fw_ctl show-ct （顯示 connection table 的前 10 條）
*/
```

## 完整參考解答

**先做完再看！**

<details>
<summary>xdp_fw.bpf.c（完整 kernel-side）</summary>

```c
/* xdp_fw.bpf.c */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

#define MAX_IPS          100000
#define MAX_CONNECTIONS  1000000
#define RATE_LIMIT_N     10        /* max new connections per second per IP */
#define RATE_WINDOW_NS   1000000000ULL  /* 1 second */

/* 靜態黑名單 */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, MAX_IPS);
    __type(key, __be32);
    __type(value, u64);  /* drop count */
} blocklist SEC(".maps");

/* Connection tracking */
struct ct_key {
    __be32 src_ip, dst_ip;
    __be16 src_port, dst_port;
};

struct ct_entry {
    u8  state;
    u64 last_seen_ns;
};

struct {
    __uint(type, BPF_MAP_TYPE_LRU_HASH);
    __uint(max_entries, MAX_CONNECTIONS);
    __type(key, struct ct_key);
    __type(value, struct ct_entry);
} ct_map SEC(".maps");

/* Rate limiting */
struct rate_entry {
    u64 window_start_ns;
    u32 count;
};

struct {
    __uint(type, BPF_MAP_TYPE_LRU_HASH);
    __uint(max_entries, MAX_IPS);
    __type(key, __be32);
    __type(value, struct rate_entry);
} rate_map SEC(".maps");

/* Stats */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 8);
    __type(key, u32);
    __type(value, u64);
} stats SEC(".maps");

#define STAT_PASS       0
#define STAT_DROP_BLOCK 1
#define STAT_DROP_RATE  2
#define STAT_DROP_CT    3
#define STAT_CT_NEW     4

static __always_inline void inc_stat(u32 idx)
{
    u64 *v = bpf_map_lookup_elem(&stats, &idx);
    if (v) (*v)++;
}

SEC("xdp")
int xdp_firewall(struct xdp_md *ctx)
{
    void *data_end = (void *)(long)ctx->data_end;
    void *data     = (void *)(long)ctx->data;

    /* Parse Ethernet */
    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end) goto pass;
    if (bpf_ntohs(eth->h_proto) != ETH_P_IP) goto pass;

    /* Parse IPv4 */
    struct iphdr *iph = (void *)(eth + 1);
    if ((void *)(iph + 1) > data_end) goto pass;
    if (iph->protocol != IPPROTO_TCP) goto pass;

    /* Parse TCP */
    struct tcphdr *tcp = (void *)iph + (iph->ihl * 4);
    if ((void *)(tcp + 1) > data_end) goto pass;

    __be32 src = iph->saddr;

    /* 1. 靜態黑名單檢查 */
    u64 *drop_cnt = bpf_map_lookup_elem(&blocklist, &src);
    if (drop_cnt) {
        __sync_fetch_and_add(drop_cnt, 1);
        inc_stat(STAT_DROP_BLOCK);
        return XDP_DROP;
    }

    struct ct_key ckey = {
        .src_ip   = iph->saddr,
        .dst_ip   = iph->daddr,
        .src_port = tcp->source,
        .dst_port = tcp->dest,
    };

    /* 2. Connection tracking */
    struct ct_entry *ct = bpf_map_lookup_elem(&ct_map, &ckey);

    if (tcp->syn && !tcp->ack) {
        /* 新的 SYN，做 rate limiting */
        u64 now = bpf_ktime_get_ns();
        struct rate_entry zero = { .window_start_ns = now, .count = 0 };
        struct rate_entry *re = bpf_map_lookup_or_try_init(&rate_map, &src, &zero);

        if (re) {
            /* 如果超過時間窗口，重置計數 */
            if (now - re->window_start_ns > RATE_WINDOW_NS) {
                re->window_start_ns = now;
                re->count = 0;
            }
            re->count++;
            if (re->count > RATE_LIMIT_N) {
                inc_stat(STAT_DROP_RATE);
                return XDP_DROP;
            }
        }

        /* 允許的 SYN：建立 connection entry */
        struct ct_entry new_ct = { .state = 1 /* NEW */, .last_seen_ns = bpf_ktime_get_ns() };
        bpf_map_update_elem(&ct_map, &ckey, &new_ct, BPF_ANY);
        inc_stat(STAT_CT_NEW);

    } else if (ct) {
        /* 已知的 connection：更新 last_seen */
        ct->last_seen_ns = bpf_ktime_get_ns();

        /* 檢查 FIN/RST */
        if (tcp->fin || tcp->rst) {
            bpf_map_delete_elem(&ct_map, &ckey);
        }
    } else {
        /* 沒有 SYN 但也沒有 connection entry：可疑封包，DROP */
        inc_stat(STAT_DROP_CT);
        return XDP_DROP;
    }

pass:
    inc_stat(STAT_PASS);
    return XDP_PASS;
}

char LICENSE[] SEC("license") = "GPL";
```

</details>

<details>
<summary>fw_ctl.c（userspace 管理程式）</summary>

```c
/* fw_ctl.c — Firewall control utility */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <arpa/inet.h>
#include <bpf/libbpf.h>

static int get_map_fd(const char *pin_path)
{
    int fd = bpf_obj_get(pin_path);
    if (fd < 0) {
        fprintf(stderr, "failed to open map %s: %m\n", pin_path);
        exit(1);
    }
    return fd;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        printf("Usage: %s <add-block|remove-block|show-stats> [ip]\n", argv[0]);
        return 1;
    }

    if (strcmp(argv[1], "add-block") == 0 && argc == 3) {
        int map_fd = get_map_fd("/sys/fs/bpf/blocklist");
        in_addr_t ip = inet_addr(argv[2]);
        u64 count = 0;
        bpf_map_update_elem(map_fd, &ip, &count, BPF_ANY);
        printf("Blocked %s\n", argv[2]);

    } else if (strcmp(argv[1], "remove-block") == 0 && argc == 3) {
        int map_fd = get_map_fd("/sys/fs/bpf/blocklist");
        in_addr_t ip = inet_addr(argv[2]);
        bpf_map_delete_elem(map_fd, &ip);
        printf("Unblocked %s\n", argv[2]);

    } else if (strcmp(argv[1], "show-stats") == 0) {
        int map_fd = get_map_fd("/sys/fs/bpf/stats");
        int ncpus = libbpf_num_possible_cpus();
        u64 *vals = calloc(ncpus, sizeof(u64));

        const char *names[] = {"pass", "drop_block", "drop_rate",
                               "drop_ct", "ct_new"};
        for (int i = 0; i < 5; i++) {
            u32 key = i;
            bpf_map_lookup_elem(map_fd, &key, vals);
            u64 total = 0;
            for (int c = 0; c < ncpus; c++) total += vals[c];
            printf("%-15s: %llu\n", names[i], total);
        }
        free(vals);
    }

    return 0;
}
```

**編譯和使用**：

```bash
# 編譯和 attach
clang -g -O2 -target bpf -D__TARGET_ARCH_x86_64 -c xdp_fw.bpf.c -o xdp_fw.bpf.o
# （生成 skeleton + 編譯 userspace loader 略）

sudo ip link set dev eth0 xdpgeneric obj xdp_fw.bpf.o sec xdp

# pin maps（這步驟通常由 loader 做）
sudo bpftool map pin name blocklist /sys/fs/bpf/blocklist
sudo bpftool map pin name stats /sys/fs/bpf/stats

# 使用管理介面
./fw_ctl add-block 1.2.3.4
./fw_ctl show-stats
./fw_ctl remove-block 1.2.3.4
```

</details>

## 測試用案例

| 測試 | 預期結果 |
|---|---|
| `ping <your-ip>` | ICMP：通過（非 TCP）|
| `nmap -sS <your-ip>` | 大部分 SYN 被 rate limit 丟棄 |
| `./fw_ctl add-block <nmap-ip>; nmap -sS ...` | 全部被 block list 丟棄 |
| 正常 SSH 連線 | 能建立 connection，後續封包通過 |
| `show-stats` 輸出 | drop_block、drop_rate、ct_new 數字合理 |

## 延伸挑戰（加分）

- **挑戰一**：加入 UDP 的 "connection tracking"（UDP 沒有 SYN/FIN，用 first-packet 建立 entry，TTL 過後清除）
- **挑戰二**：實作 per-IP 的流量統計（bytes/packets），每 10 秒輸出 top-10 IP
- **挑戰三**：加入 GeoIP filtering（需要在 BPF maps 裡存 IP prefix → country code 的 mapping）

## 自我檢核

- [ ] 能解釋 stateful 和 stateless 防火牆的差異，以及為什麼 stateful 更難繞過
- [ ] 知道 connection tracking 的 LRU_HASH 的 key 和 value 各包含什麼
- [ ] 能說出 rate limiting 的 time window 設計（per-IP per-second 的實作方式）
- [ ] 知道如何讓 userspace 的 map 更新立即對 BPF program 生效（不需要 reload）

→ [Ch 34 seccomp-bpf：syscall 過濾](./34-seccomp-bpf.md)
