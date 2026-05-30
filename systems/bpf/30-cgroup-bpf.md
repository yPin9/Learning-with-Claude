# Ch 30 — cgroup BPF：容器網路控制

> **目標**：理解 cgroup BPF 的 attach 機制和繼承模型，掌握 CGROUP_SKB、CGROUP_SOCK、CGROUP_SOCK_ADDR 三種常用類型的用途，以及如何在 Kubernetes 環境用 cgroup BPF 做 per-container 的網路策略。

## 為什麼需要 Cgroup BPF？

XDP 和 TC BPF 是「per-interface」的——一個程式 attach 到一個網路介面。但 Kubernetes 需要 **per-container** 的網路策略：不同 container 有不同的 network policy，但它們都共享同一個物理網路介面。

Cgroup BPF 解決了這個問題：attach 到 cgroup hierarchy，繼承關係讓你能為整個 container（或 pod）設定統一的策略。

## Cgroup 繼承模型

```
Cgroup hierarchy：

/ (root cgroup)
├── system.slice
│   └── docker.service
└── kubepods
    ├── pod-abc123
    │   ├── container-frontend  ← attach 的 BPF program 對這個 container 有效
    │   └── container-backend
    └── pod-def456
        └── container-db
```

**繼承規則**：

- Attach 在 `/kubepods` 的 BPF program 對所有 pod 有效
- Attach 在 `/kubepods/pod-abc123` 的 BPF program 只對 pod-abc123 有效
- 子 cgroup 可以有自己的 BPF program（用 `BPF_F_ALLOW_MULTI`）

## 主要的 Cgroup BPF 類型

### CGROUP_SKB：封包進出 cgroup

在 cgroup 的所有 process 發送/接收封包時觸發：

```c
/* 封鎖 cgroup 裡的所有 DNS 查詢（UDP port 53）*/
SEC("cgroup_skb/egress")
int block_dns(struct __sk_buff *skb)
{
    if (skb->protocol != bpf_htons(ETH_P_IP)) return 1;  /* 1 = allow */

    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;

    struct iphdr *iph = data;
    if ((void *)(iph + 1) > data_end) return 1;
    if (iph->protocol != IPPROTO_UDP) return 1;

    struct udphdr *udph = (void *)iph + (iph->ihl * 4);
    if ((void *)(udph + 1) > data_end) return 1;

    if (bpf_ntohs(udph->dest) == 53)
        return 0;  /* 0 = drop（DNS 查詢）*/

    return 1;
}

char LICENSE[] SEC("license") = "GPL";
```

**Attach（需要 cgroup fd）**：

```c
int cgroup_fd = open("/sys/fs/cgroup/kubepods/pod-abc123", O_RDONLY);
bpf_prog_attach(prog_fd, cgroup_fd, BPF_CGROUP_INET_EGRESS, 0);
```

或用 libbpf：

```c
struct bpf_link *link = bpf_program__attach_cgroup(prog, cgroup_fd);
```

### CGROUP_SOCK：控制 socket 建立

在 cgroup 裡的 process 建立 socket 時觸發：

```c
/* 拒絕特定 cgroup 建立 IPv6 socket */
SEC("cgroup/sock_create")
int restrict_ipv6(struct bpf_sock *sk)
{
    if (sk->family == AF_INET6)
        return 0;  /* 0 = 拒絕 */
    return 1;      /* 1 = 允許 */
}
```

### CGROUP_SOCK_ADDR：修改 bind/connect 的地址

這是最強大的 cgroup BPF 類型——可以在 connect 或 bind 時修改目標地址，做 **透明代理** 或 **NAT**：

```c
/* 把 cgroup 裡所有連到 port 80 的 TCP 連線重導到 proxy（port 10080）*/
SEC("cgroup/connect4")
int redirect_to_proxy(struct bpf_sock_addr *ctx)
{
    /* 只修改連到 port 80 的連線 */
    if (ctx->user_port != bpf_htons(80))
        return 1;  /* 不修改 */

    /* 改成連到本地的 proxy port */
    ctx->user_ip4 = bpf_htonl(0x7F000001);  /* 127.0.0.1 */
    ctx->user_port = bpf_htons(10080);       /* proxy port */

    return 1;  /* 允許繼續（用修改後的地址）*/
}
```

Cilium 用這個機制實現 **service mesh 的 transparent redirect**——不修改 application，直接在 connect 時把流量導到 sidecar。

## Cgroup BPF 類型全覽

| 類型 | 觸發時機 | 用途 |
|---|---|---|
| `CGROUP_SKB/ingress` | 收到封包時 | 封包過濾 |
| `CGROUP_SKB/egress` | 發送封包時 | 封包過濾 |
| `CGROUP_SOCK/sock_create` | 建立 socket | 限制 socket 類型 |
| `CGROUP_SOCK_ADDR/connect4/6` | TCP connect | 重導目標地址 |
| `CGROUP_SOCK_ADDR/bind4/6` | bind | 重導 bind 地址 |
| `CGROUP_SOCK_ADDR/getpeername` | getpeername | 修改 peer address |
| `CGROUP_SYSCTL` | sysctl 讀寫 | 限制 sysctl 存取 |
| `CGROUP_DEVICE` | 設備存取 | 限制 device file 存取 |

## 查看目前 attach 的 cgroup BPF

```bash
# 列出某個 cgroup 上 attach 的 BPF programs
sudo bpftool cgroup tree /sys/fs/cgroup/

# 輸出（簡化）：
# /sys/fs/cgroup
# ID       AttachType      AttachFlags     Name
# 42       cgroup_skb_egress              block_dns
# 43       cgroup_connect4               redirect_to_proxy
```

## Kubernetes 中的 Cgroup BPF（Cilium）

Cilium 大量使用 cgroup BPF 實現以下功能：

1. **Network policy enforcement**：CGROUP_SKB 在每個 pod 的 cgroup 上 attach，強制執行 NetworkPolicy
2. **Service redirect**：CGROUP_SOCK_ADDR（connect4/6）在 connect 時把 ClusterIP 重導到 backend pod IP（繞過 iptables 的 kube-proxy）
3. **Local endpoint acceleration**：sock_addr + sk_msg 讓 same-node 的 pod 通訊不走 TCP/IP stack

```bash
# 在 Cilium 的節點上查看 cgroup BPF programs
sudo bpftool cgroup tree /sys/fs/cgroup/ | grep cilium
```

## 踩雷集錦

1. **Cgroup v2 vs v1**：cgroup BPF 只支援 cgroup v2；確認 `mount | grep cgroup2`；如果你的系統只有 cgroup v1，需要升級或開啟 unified hierarchy

2. **`BPF_F_ALLOW_MULTI` 的繼承語意**：預設 attach 模式（`BPF_F_ALLOW_OVERRIDE`）讓子 cgroup 的 program 覆蓋父的；`BPF_F_ALLOW_MULTI` 讓父子的 program 都執行；在生產環境要仔細設計繼承策略

3. **CGROUP_SOCK_ADDR 的 return value 和 CGROUP_SKB 不同**：1 = 允許，0 = 拒絕（和 XDP 的語意相反）

4. **Cgroup BPF 和 Kubernetes namespace 的關係**：Kubernetes 的 network namespace 隔離和 cgroup 是正交的（不同機制）；cgroup BPF 作用在 cgroup hierarchy，不是 network namespace

## 動手練習

1. 在你的系統上找到 Docker container 的 cgroup path（`cat /proc/<container-pid>/cgroup`），用 `bpftool cgroup show` 查看是否有 BPF program attach

2. 寫一個 CGROUP_SKB egress program，過濾特定 IP 的 outbound 流量，attach 到你的 user slice cgroup（`/sys/fs/cgroup/user.slice`），確認你的 process 的流量被過濾

## 本章重點整理

- Cgroup BPF 在 cgroup hierarchy 上 attach，繼承模型讓 per-container 策略成為可能
- CGROUP_SKB 做封包過濾；CGROUP_SOCK 控制 socket 建立；CGROUP_SOCK_ADDR 做透明代理
- Cilium 大量用 cgroup BPF 實現 Kubernetes network policy 和 service mesh 功能
- Cgroup v2 是必要條件

## 自我檢核

- [ ] 能解釋 cgroup BPF 的繼承模型（父 cgroup 的 program 對子 cgroup 的 process 也有效）
- [ ] 知道 CGROUP_SOCK_ADDR 如何實現透明代理
- [ ] 能說出 `BPF_F_ALLOW_MULTI` 和預設 attach 的語意差異

→ [Ch 31 BPF 與 Cilium：Kubernetes CNI](./31-cilium-kubernetes.md)
