# Ch 33 — BPF 與 Service Mesh

> **目標**：理解傳統 sidecar service mesh 的 overhead、eBPF 如何透過 socket acceleration 和 sidecar bypass 降低 latency、以及 Cilium 和 Istio ambient mesh 的設計取捨。

## 傳統 Sidecar Service Mesh 的 Overhead

```
傳統 Envoy sidecar（每個 Pod 一個）：

App → localhost → iptables REDIRECT → Envoy（port 15001）
                                        → L7 policy check
                                        → TLS encryption
                                        → networking stack × 2
                                        → destination Pod 的 Envoy
                                        → App

每個請求要過 4 次 networking stack（app → sidecar → network → sidecar → app）
iptables 的 REDIRECT 本身也有 overhead（conntrack、NAT table）
```

測試數字：在高 QPS 下，sidecar 會增加 2–5 ms 的額外 latency，消耗額外 10–20% CPU。

## eBPF Socket Acceleration

Cilium 的 socket acceleration 用 sk_msg + CGROUP_SOCK_ADDR 做 **sidecar bypass**：

```
Cilium socket acceleration：

App sendmsg() → SK_MSG BPF
                  │ 查 sockhash 找到 Envoy 的 socket
                  └── bpf_msg_redirect_hash
                         → Envoy socket（直接注入，不走 TCP/IP）
                              → Envoy 做 L7 policy + TLS
                              → bpf_msg_redirect_hash
                                 → destination App（直接注入）
```

兩個 socket 之間的資料傳輸不走 networking stack，省掉了兩次完整的 TCP/IP roundtrip。

效能提升：latency 降低 30–50%，吞吐量提升 40%。

## Ambient Mesh：完全移除 Sidecar

Istio 的 ambient mesh 走得更進一步：完全移除 sidecar，改用節點級的 proxy（ztunnel）和 waypoint proxy：

```
Ambient Mesh 架構：

Pod A → [ztunnel（節點級，用 HBONE + mTLS）] → [ztunnel]→ Pod B

L4 policy 和 mTLS 在 ztunnel 處理（eBPF 幫助 traffic steering）
L7 policy 在 waypoint proxy 處理（只有需要 L7 policy 的 service 才有 waypoint）
```

Cilium 的 ambient-like 實作（Cilium Mesh）：

```
Cilium Mesh：

Pod A → [Cilium BPF datapath] → [direct routing] → Pod B

L4 policy：純 BPF（不需要 proxy）
L7 policy：可選的 Envoy（只在需要時 inject）
mTLS：Cilium 的 WireGuard node-to-node encryption（不需要 sidecar）
```

## eBPF + Service Mesh 的效能對比

| 方案 | P50 latency | P99 latency | CPU overhead |
|---|---|---|---|
| 無 service mesh | ~0.3ms | ~1ms | baseline |
| Envoy sidecar（iptables）| ~2ms | ~8ms | +15–20% |
| Envoy sidecar（Cilium bypass）| ~0.8ms | ~3ms | +8% |
| Cilium Mesh（eBPF only）| ~0.4ms | ~1.5ms | +3% |

（數字來自 Cilium 和 Istio 的公開 benchmark，實際數字視負載和配置而定）

## eBPF 在 Service Mesh 的應用點

| 功能 | eBPF 程式 | 說明 |
|---|---|---|
| Traffic steering | CGROUP_SOCK_ADDR | 把流量導到正確的 proxy |
| Socket shortcircuit | SK_MSG | localhost 通訊繞過 TCP/IP |
| L4 policy | TC BPF | 在 data path 直接做策略 |
| Connection tracking | LRU_HASH map | 連線狀態 |
| Metrics collection | kprobe/tracepoint | 收集 latency、error rate |
| mTLS（可選）| WireGuard + BPF | Node-to-node 加密 |

## 踩雷集錦

1. **Socket acceleration 和 TLS 的互動**：如果 App 做 TLS（application-level TLS），socket acceleration 不影響安全性（TLS 在 socket API 層，下面的 transport 怎麼做不影響加密）；如果 service mesh 做 mTLS，需要 proxy 可見 plaintext

2. **L7 policy 仍然需要 proxy**：eBPF 可以做 L3/L4 policy（IP/port），但 HTTP path-based、gRPC method-based 等 L7 policy 仍然需要 Envoy 這樣的 L7 proxy；eBPF 可以加速 steering，但不能替代 L7 inspection

3. **Ambient mesh 的 ztunnel 仍然有 overhead**：比起 pure eBPF（Cilium），ztunnel 還是多了一個 userspace proxy；差距在有 mTLS 的場景

## 動手練習

1. 在 Cilium 環境裡啟用 socket acceleration（`helm upgrade cilium --set socketLB.enabled=true`），用 `bpftool cgroup tree` 確認 sk_msg 和 CGROUP_SOCK_ADDR programs 已 attach

2. 用 `cilium monitor` 追蹤 pod 之間的流量，觀察哪些流量走了 socket shortcircuit（在 cilium monitor 輸出裡找 `to-overlay` 和 `from-overlay`）

## 本章重點整理

- 傳統 sidecar service mesh 增加 4 次 networking stack roundtrip
- eBPF socket acceleration（sk_msg + CGROUP_SOCK_ADDR）讓 localhost 流量繞過 TCP/IP，降低 50%+ latency
- Ambient mesh 移除 sidecar，但 L7 policy 仍然需要 waypoint proxy
- 純 eBPF datapath（L4 only）是效能最好的方案，L7 需要 tradeoff

→ [練習 E：XDP stateful firewall](./practice-e-xdp-stateful-firewall.md)
