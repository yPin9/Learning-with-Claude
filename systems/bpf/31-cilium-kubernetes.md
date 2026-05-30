# Ch 31 — BPF 與 Cilium：Kubernetes CNI

> **目標**：理解 Cilium 如何用 eBPF 取代傳統 kube-proxy + iptables，實現 identity-based network policy，以及 eBPF datapath 的整體架構。

## 傳統 Kubernetes 網路的問題

```
傳統方式（iptables + kube-proxy）：

Pod A 連線到 Service ClusterIP
  → iptables DNAT 規則（每個 Service 有數條）
  → 選擇一個 backend Pod IP
  → 封包走 veth + bridge（每個 node 一個 bridge）

問題：
1. iptables 規則數量隨 Service 線性增長（1000 個 Service = 10000+ 規則）
2. 每條連線都要 iptables 遍歷（sequential lookup，O(N)）
3. Network policy 也用 iptables 實現，規則更多
4. iptables 不支援 application-layer（L7）policy
```

## Cilium 的 eBPF Datapath

```
Cilium 的方式：

Pod A 連線到 Service ClusterIP
  → CGROUP_SOCK_ADDR（connect4）BPF：直接把 ClusterIP 換成 backend IP
    （在 TCP connect 之前，不需要 DNAT，不需要 conntrack）
  → 封包走 BPF TC ingress/egress（每個 veth 上 attach BPF program）
  → 基於 identity 的 network policy（不是 IP，是 pod label）

優點：
1. Service DNAT 是 BPF map lookup，O(1) 而不是 iptables 的 O(N)
2. Network policy 用 BPF LRU_HASH，不是 iptables
3. 支援 L7 policy（用 Envoy sidecar，但可選 sidecar-less）
4. Direct node routing（不需要 overlay 網路）
```

## Cilium 的核心 BPF Programs

```
Cilium 的 BPF program 分布：

每個 network interface（Pod 的 veth pair）：
  ├── TC ingress：identity check + policy enforcement
  └── TC egress： policy enforcement + service redirect

節點 cgroup（node-level）：
  ├── CGROUP_SOCK_ADDR/connect4：Service ClusterIP → backend IP
  └── CGROUP_SOCK_ADDR/bind4：Nodeport 處理

Loopback：
  └── sk_msg：pod-to-pod same-node local forwarding
```

## Identity-based Policy

Cilium 的 network policy 不是基於 IP address（Pod 的 IP 是動態分配的），而是基於 **identity**（從 pod 的 label 派生的一個整數）：

```
Pod labels → identity hash → 32-bit identity ID（例如 12345）

network policy：
  "Pod A（identity 12345）可以連接 Pod B（identity 67890）的 TCP port 8080"

在 BPF map 裡存儲：
  {src_identity: 12345, dst_identity: 67890, port: 8080, proto: TCP} → ALLOW
```

```c
/* Cilium 的 policy 查詢（簡化）*/
SEC("tc")
int cilium_ingress(struct __sk_buff *skb)
{
    u32 src_identity = get_src_identity(skb);  /* 從封包 metadata 讀 */
    u32 dst_identity = get_local_ep_identity(); /* 本機 endpoint 的 identity */

    struct policy_key key = {
        .identity = src_identity,
        .dport = skb->local_port,
        .proto = /* ... */,
    };

    if (!bpf_map_lookup_elem(&policy_map, &key))
        return TC_ACT_SHOT;  /* policy 不允許 */

    return TC_ACT_OK;
}
```

## Cilium 的 Service Load Balancing（kube-proxy replacement）

```
傳統 kube-proxy：
  iptables DNAT 規則：
    ClusterIP:80 → backend-1:8080 (33%)
    ClusterIP:80 → backend-2:8080 (33%)
    ClusterIP:80 → backend-3:8080 (33%)

Cilium eBPF：
  service_map: {ClusterIP:80} → [{backend-1:8080}, {backend-2:8080}, {backend-3:8080}]
  在 connect4 的 CGROUP_SOCK_ADDR 裡做一次 map lookup + consistent hashing
  → 直接把 connect() 的目標 IP 改成 backend IP
  → 沒有 conntrack，沒有 DNAT，沒有 iptables
```

```bash
# 查看 Cilium 的 service map（需要在 Cilium 節點上）
sudo cilium service list

# 查看對應的 BPF map
sudo bpftool map show name cilium_lxc
sudo bpftool map show name cilium_service
sudo bpftool map dump name cilium_lb4_services_v2
```

## Cilium 的 eBPF Datapath 圖示

```
[Pod A veth] ←→ [TC egress BPF]         發送時做 policy + redirect
                      ↕
[internal bridge/routing]
                      ↕
[Pod B veth] ←→ [TC ingress BPF]        接收時做 identity check + policy

node-level cgroup hooks:
  CGROUP_SOCK_ADDR → Service ClusterIP 的 L4 load balancing
  SK_MSG → same-node 的 socket shortcircuit
```

## 查看 Cilium 使用的 BPF Programs

```bash
# 列出所有 Cilium 使用的 BPF programs
sudo bpftool prog list | grep cilium

# 查看 Cilium 的 cgroup BPF
sudo bpftool cgroup tree /sys/fs/cgroup/ | head -50

# 查看 policy map（需要 Cilium installed）
sudo cilium bpf policy list
sudo cilium bpf lb list  # load balancer entries

# Cilium 的 BPF programs 都 pin 在
ls /sys/fs/bpf/tc/globals/
```

## 踩雷集錦

1. **Cilium 和 kube-proxy 不能同時使用**：啟用 Cilium 的 kube-proxy replacement 後，必須停用 kube-proxy；兩者同時運行會衝突

2. **Direct Node Routing 需要 node 間的路由設定**：Cilium 的 native routing 模式不用 overlay（VXLAN/Geneve），但需要路由器能轉發 pod CIDR；雲端環境通常需要額外設定

3. **BPF datapath 的除錯**：問題發生時，`cilium monitor` 可以 dump BPF datapath 的決策；`cilium endpoint list` 顯示 endpoint identity

## 動手練習

1. 安裝 Cilium（local kind cluster：`kind create cluster; helm install cilium cilium/cilium`），用 `bpftool prog list | grep cilium` 查看有多少個 BPF programs

2. 在 Cilium 環境裡建立一個 NetworkPolicy，用 `cilium bpf policy list` 查看對應的 BPF map entries

## 本章重點整理

- Cilium 用 TC BPF + CGROUP_SOCK_ADDR + SK_MSG 取代 iptables + kube-proxy
- Identity-based policy（基於 pod label 的整數 ID）比 IP-based 更靈活（Pod IP 是動態的）
- Service load balancing 用 CGROUP_SOCK_ADDR 的 connect4 hook，在 connect 時做 O(1) map lookup

## 自我檢核

- [ ] 能解釋 Cilium 如何用 eBPF 取代 iptables（CGROUP_SOCK_ADDR 的角色）
- [ ] 知道什麼是 identity-based policy，以及為什麼比 IP-based 更適合 Kubernetes
- [ ] 能說出 kube-proxy 的 O(N) 問題和 Cilium 的 O(1) 解法

→ [Ch 32 BPF Load Balancer 設計](./32-bpf-load-balancer.md)
