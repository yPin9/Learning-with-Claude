# Ch 37 — 容器網路

> 目標：搞懂 Docker / Kubernetes 的網路模型 — bridge / host / overlay / CNI。

## Container 網路的挑戰

容器跑在同 host 上 → 需要：

- 容器間互通
- 容器跟 host 通
- 容器對外網
- 多 host 容器互通（Kubernetes）
- service discovery（容器 IP 動態）

每個都用不同網路機制。

## Docker 4 種 network mode

| Mode | 機制 | 用途 |
|---|---|---|
| `bridge`（預設） | docker0 bridge + veth | 一般 |
| `host` | 共用 host netns | 高性能 |
| `none` | 沒 network | 隔離 |
| `container:X` | 共用 X container 的 netns | sidecar |

### bridge mode

預設。每個 container 在獨立 netns，veth 連到 docker0 bridge：

```
 ┌────────────────────────────────────────────┐
 │ host                                       │
 │                                            │
 │  eth0 (公網)                               │
 │  │                                          │
 │  │ NAT (iptables)                          │
 │  │                                          │
 │  docker0 (172.17.0.1)                      │
 │  ├── veth-X ──→ container A (172.17.0.2)   │
 │  └── veth-Y ──→ container B (172.17.0.3)   │
 │                                            │
 └────────────────────────────────────────────┘
```

跟你 Ch 22 手建的一樣。

### host mode

```bash
docker run --network host nginx
```

container 直接用 host 的 netns。**沒隔離**。

優點：性能最好（無 bridge / NAT）。
缺點：port 衝突、隔離差。

用於：高性能網路 service / debug。

### overlay mode

跨 host 的 container 互通。**Kubernetes / Docker Swarm** 用：

```
 host A (1.2.3.4)             host B (5.6.7.8)
   ├── ctr1 (10.0.0.1) ◄─VXLAN tunnel─► ctr3 (10.0.0.3)
   └── ctr2 (10.0.0.2)                 └── ctr4 (10.0.0.4)
```

VXLAN encapsulation 讓不同 host 上的容器**像在同個虛擬網段**。

## Docker network 命令

```bash
# 列 networks
docker network ls

# 看 network 細節
docker network inspect bridge

# 建 network
docker network create mynet
docker network create --driver bridge --subnet 10.10.0.0/24 mynet

# 連 container 到 network
docker run --network mynet nginx
docker network connect mynet existing-container

# 斷
docker network disconnect mynet container1
```

## Custom bridge 比 default 好

Docker 預設 bridge 缺：

- 無 service discovery (容器互連要 IP)
- 無自動 DNS

建自己的 bridge：

```bash
docker network create app-net

docker run --name web --network app-net nginx
docker run --name db --network app-net postgres

# 容器內，能用 name 互連
docker exec web ping db    # OK
```

**建議所有 production 用 custom network**。

## Docker compose

宣告式定義 multi-container：

```yaml
# docker-compose.yml
version: '3'

services:
  web:
    image: nginx
    ports:
      - "80:80"
    networks:
      - app-net
  
  db:
    image: postgres
    environment:
      POSTGRES_PASSWORD: secret
    networks:
      - app-net

networks:
  app-net:
    driver: bridge
```

```bash
docker compose up -d
```

自動建 network、跑 containers、互連。

## Kubernetes 網路：CNI

K8s 的網路用 **CNI** (Container Network Interface) plugin：

| CNI | 特色 |
|---|---|
| **Flannel** | 簡單 overlay |
| **Calico** | BGP routing，性能好 |
| **Cilium** | eBPF-based，現代 |
| **Weave** | 老牌 mesh |
| **AWS VPC CNI** | 雲原生 |

K8s 4 個基本網路問題：

1. **Pod-to-Pod**：CNI 解決（每 pod 獨立 IP）
2. **Pod-to-Service**：kube-proxy + iptables / IPVS
3. **External-to-Service**：LoadBalancer / NodePort / Ingress
4. **Pod-to-External**：default route + NAT

## kube-proxy + Service

K8s 有 `Service` 抽象 — 給多個 pod 一個穩定 VIP：

```
 Service: my-svc, ClusterIP 10.96.0.10
   ├── Pod 1 (10.244.0.5)
   ├── Pod 2 (10.244.1.8)
   └── Pod 3 (10.244.2.3)
```

任何 pod 連 `my-svc:80` 透過 kube-proxy 路由到後端 pod 之一。

實作：iptables 或 IPVS rules（kube-proxy 自動維護）。

## Ingress

把外部 traffic 路由到 cluster 內 service：

```
 internet ──► Ingress controller (nginx / traefik / istio)
                    │
                    ├── path /api → service A
                    └── path /web → service B
```

通常用 Cloud LoadBalancer + Ingress controller。

## Service Mesh

進階：每 pod 旁掛 sidecar proxy（Envoy / Istio）做：

- mTLS
- traffic management
- observability
- retries / circuit breaker

「**所有網路功能在 mesh 層處理**」。複雜但強大。

## 觀察 docker 網路

```bash
# 看 docker0 bridge
ip a show docker0
sudo bridge link

# 看 container 的 netns
docker inspect <container> | grep Pid
sudo nsenter -t <PID> -n ip a
sudo nsenter -t <PID> -n ss -tnlp

# 看 NAT rules
sudo iptables -t nat -L -n -v | grep DOCKER
```

## 一個常見誤解：「容器有自己的 network stack 跟 VM 一樣」

**錯**。容器跟 VM 不同：

- VM：有自己的 kernel，完整網路 stack
- 容器：共用 host kernel，只有獨立 netns

容器只「**看起來像**」獨立網路，但 kernel 是 host 的。

## 一個常見誤解：「Docker bridge 自動 firewall」

**錯**。Docker 預設**對外 expose** 你 publish 的 port：

```bash
docker run -p 80:80 nginx
# 任何人連 host:80 就能訪問
```

Docker 自動加 iptables rules**繞過 host 的 ufw**！

對策：明確指定 IP：

```bash
docker run -p 127.0.0.1:80:80 nginx
# 只 localhost 能連
```

或用 `iptables -I DOCKER-USER` 加自己的 rule。

## 一個常見誤解：「K8s 自帶 ingress」

**錯**。K8s 提供 `Ingress` resource 定義，但需要**安裝 Ingress controller** 才能用。

常見：

- nginx-ingress
- traefik
- HAProxy ingress
- Istio Gateway

## 一個常見誤解：「容器網路慢」

**部分對**。bridge mode 比 host mode 慢（額外 NAT）。但大多場景**慢的可忽略**。

真正性能要求：用 host mode 或 macvlan。

## 動手練習

**1. 建 docker custom network**

```bash
docker network create test-net
docker run -d --name n1 --network test-net nginx
docker run -d --name n2 --network test-net nginx
docker exec n1 ping -c 3 n2     # 用 name 互連
docker network rm test-net
```

**2. 看 docker0**

```bash
ip a show docker0
sudo iptables -L -t nat -n | grep -i docker | head
```

**3. 進 container netns**

```bash
docker run -d --rm --name web nginx
PID=$(docker inspect web | grep '"Pid"' | grep -oP '\d+')
sudo nsenter -t $PID -n ip a
sudo nsenter -t $PID -n ss -tnlp
docker stop web
```

**4. compose 多 container**

```bash
mkdir compose-test && cd compose-test
cat > docker-compose.yml <<EOF
services:
  web:
    image: nginx
    ports:
      - "8080:80"
  redis:
    image: redis
EOF
docker compose up -d
docker compose ps
docker exec compose-test-web-1 ping -c 1 redis
docker compose down
```

**5. 看 K8s（如果有）**

```bash
kubectl get pods -o wide
kubectl get svc
kubectl get endpoints
kubectl get ingress
```

## 自我檢核

- [ ] Docker 4 種 network mode 知道
- [ ] 自建 docker bridge network、用 name 互連
- [ ] 知道 docker compose 預設怎麼處理 network
- [ ] K8s 4 個網路問題清楚
- [ ] 知道 CNI 是什麼
- [ ] 用 nsenter 看過 container netns

下一章看 IPv6。

→ [Ch 38 IPv6](./38-ipv6.md)
