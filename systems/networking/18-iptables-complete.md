# Ch 18 — iptables 完整指南

> 目標：搞懂 iptables 的 4 表 5 鏈、寫 firewall 規則、看別人的規則。

## iptables 是什麼

Linux kernel 的 **netfilter** 框架的 user-space 控制工具。能：

- **過濾** packet（firewall）
- **改寫** packet（NAT）
- **mark** packet（QoS）
- **記錄** 流量

雖然有新的 nftables（Ch 19）取代，但**現存系統 90% 還用 iptables**。會 iptables 必修。

## 4 表 5 鏈

iptables 用「**表（table）+ 鏈（chain）**」組織規則：

### 4 個 table

| Table | 用途 |
|---|---|
| **filter** | 過濾（INPUT / OUTPUT / FORWARD）— 預設 |
| **nat** | NAT（PREROUTING / POSTROUTING） |
| **mangle** | 修改 packet field |
| **raw** | 跳過 connection tracking |

### 5 個 chain

| Chain | 觸發時機 |
|---|---|
| **PREROUTING** | packet 進來、路由前 |
| **INPUT** | 路由後，往本機 |
| **FORWARD** | 路由後，要轉發 |
| **OUTPUT** | 本機產的 packet 出去前 |
| **POSTROUTING** | 真正送出去前 |

不是每個 table 都有所有 chain。常用組合：

| 用途 | Table | Chain |
|---|---|---|
| 阻擋進來 | filter | INPUT |
| 阻擋本機出去 | filter | OUTPUT |
| 阻擋 forward (router) | filter | FORWARD |
| SNAT (改 src) | nat | POSTROUTING |
| DNAT (改 dst) | nat | PREROUTING |

## packet 旅程

```
                          ┌──────────────────┐
                          │   PREROUTING     │
       packet 進來 ───────►│ raw / mangle /  │
                          │      nat         │
                          └────────┬─────────┘
                                   │
                          ┌────────┴─────────┐
                          │    routing       │
                          │   decision       │
                          └────┬───────┬─────┘
                               │       │
                  to local ────┘       └──── to forward
                       │                            │
              ┌────────┴────────┐         ┌────────┴────────┐
              │     INPUT       │         │     FORWARD     │
              │ mangle / filter │         │ mangle / filter │
              └────────┬────────┘         └────────┬────────┘
                       │                            │
                  local process                     │
                       │                            │
              ┌────────┴────────┐                  │
              │    OUTPUT       │                  │
              │ raw / mangle /  │                  │
              │ nat / filter    │                  │
              └────────┬────────┘                  │
                       │                            │
                       └────────┬───────────────────┘
                                │
                       ┌────────┴─────────┐
                       │  POSTROUTING     │
                       │ mangle / nat     │
                       └────────┬─────────┘
                                │
                       packet 送出
```

新手只要記：

- **進來**：PREROUTING → INPUT
- **出去**：OUTPUT → POSTROUTING
- **轉發**（router）：PREROUTING → FORWARD → POSTROUTING

## 基本命令

```bash
# 看現有規則
sudo iptables -L                  # filter table
sudo iptables -L -n               # 不解 DNS
sudo iptables -L -n -v            # 含 packet count
sudo iptables -L -t nat           # nat table
sudo iptables -L INPUT -n --line-numbers   # 加 line number
```

範例輸出：

```
Chain INPUT (policy ACCEPT)
target     prot opt source     destination
ACCEPT     all  --  anywhere   anywhere   ctstate RELATED,ESTABLISHED
ACCEPT     tcp  --  anywhere   anywhere   tcp dpt:ssh
DROP       all  --  anywhere   anywhere
```

「policy ACCEPT」= 預設行為（沒匹配規則的 packet 怎麼處理）。

### 加規則

```bash
# 接受 SSH (port 22)
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT

# 阻擋特定 IP
sudo iptables -A INPUT -s 192.168.1.100 -j DROP

# 接受 HTTPS
sudo iptables -A INPUT -p tcp --dport 443 -j ACCEPT

# 預設拒絕（最後加）
sudo iptables -A INPUT -j DROP
```

選項：

- `-A` append（最後）
- `-I` insert（最前 / 指定位置）
- `-D` delete
- `-F` flush（清整個 chain）
- `-p tcp/udp/icmp/all`
- `-s` source
- `-d` dest
- `--sport / --dport` source/dest port
- `-i / -o` input/output interface
- `-j ACCEPT/DROP/REJECT/LOG`

### 刪規則

```bash
# 用 line number
sudo iptables -L INPUT -n --line-numbers
sudo iptables -D INPUT 3

# 完整 spec match
sudo iptables -D INPUT -s 192.168.1.100 -j DROP
```

### 持久化

iptables 規則是 runtime，重開機消失。要持久：

```bash
# Ubuntu/Debian
sudo apt install iptables-persistent
sudo netfilter-persistent save     # 存到 /etc/iptables/rules.v4
```

或手動：

```bash
sudo iptables-save > /etc/iptables/rules.v4
sudo iptables-restore < /etc/iptables/rules.v4
```

## ACCEPT vs DROP vs REJECT

| Action | 行為 |
|---|---|
| ACCEPT | 通過 |
| DROP | 丟棄，沒回應（對方 timeout） |
| REJECT | 丟棄 + 回 ICMP "Connection refused" |
| LOG | 記到 syslog，繼續比對下一條 |

DROP vs REJECT：

- DROP 對攻擊者更安全（看起來主機不存在）
- REJECT 對 user 友善（立刻 fail，不 timeout）

## stateful firewall（connection tracking）

iptables 能追蹤連線狀態：

```bash
# 接受已建立 / 相關連線（必加）
sudo iptables -A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
```

意思：「不管什麼 src/port，**只要是現有連線的後續 packet**就接受」。

這條讓你不必為每個出去連線的回包寫規則。**標準 firewall 的第一條**。

連線狀態：

- `NEW`：第一個 packet
- `ESTABLISHED`：已成立
- `RELATED`：跟現有連線相關（如 FTP data channel）
- `INVALID`：壞 packet

## 標準 firewall ruleset

```bash
#!/bin/bash
# 清乾淨
iptables -F
iptables -X
iptables -Z

# 預設拒絕
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT ACCEPT

# 1. 已建立連線 + loopback 接受
iptables -A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
iptables -A INPUT -i lo -j ACCEPT

# 2. SSH (改 port 更安全，這裡用 default 22)
iptables -A INPUT -p tcp --dport 22 -j ACCEPT

# 3. HTTP/HTTPS（如果是 web server）
iptables -A INPUT -p tcp --dport 80 -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j ACCEPT

# 4. ping 接受（可選）
iptables -A INPUT -p icmp --icmp-type echo-request -j ACCEPT

# 5. log 被 drop 的（可選，方便 debug）
iptables -A INPUT -j LOG --log-prefix "iptables-DROP: " --log-level 4

# 6. 預設 DROP（已在 policy 設）
```

## NAT 範例

### SNAT（source NAT，多裝置共享 IP）

```bash
# 把 LAN 出去的 packet 改成本機 IP
sudo iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
```

`MASQUERADE` = 動態 SNAT（用 outgoing interface 的 IP）。

開 IP forward：

```bash
sudo sysctl -w net.ipv4.ip_forward=1
# 持久化
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
```

### DNAT（dest NAT，port forwarding）

```bash
# 把外部 8080 轉到 192.168.1.100:80
sudo iptables -t nat -A PREROUTING -p tcp --dport 8080 -j DNAT --to-destination 192.168.1.100:80
```

## 檢視 stateful 規則

```bash
sudo conntrack -L           # 看現有 conntrack（需 conntrack-tools）
sudo cat /proc/net/nf_conntrack  # 直接看 kernel
```

## 一個常見踩雷：「policy 設 DROP 後 SSH 斷了」

```bash
sudo iptables -P INPUT DROP    # 沒先加 SSH ACCEPT
# 你的 SSH 立刻斷
```

**順序很重要**：先加 ACCEPT 規則，再改 policy。

或用 `iptables-restore` 一次性 atomic 套用整套規則。

## 一個常見踩雷：忘了 `-i lo`

```bash
# 沒接受 loopback
iptables -A INPUT -m conntrack --ctstate ESTABLISHED -j ACCEPT
iptables -P INPUT DROP

# localhost 上的服務（127.0.0.1）連不到
```

**`lo` interface 一定要 ACCEPT**，否則本機程式互通失敗。

## 一個常見踩雷：規則順序錯

```bash
# 錯：先 DROP 再 ACCEPT
iptables -A INPUT -j DROP
iptables -A INPUT -p tcp --dport 22 -j ACCEPT   # 永遠不會 match
```

iptables 是「**從上到下匹配，第一個 match 就執行**」。**ACCEPT 規則要在 DROP 之前**。

## 動手練習

**1. 看現有規則**

```bash
sudo iptables -L -n -v
sudo iptables -L -n -v -t nat
```

**2. 設個簡單 firewall（在 VPS 上練）**

**警告**：在 VPS 上練 firewall，搞錯 SSH 會斷。**先設 console / 救援方案**。

```bash
# 安全做法：先設 reset 計時
echo "iptables -F; iptables -P INPUT ACCEPT" | at now + 5 min
# 5 分鐘後自動 reset

# 然後實驗
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -P INPUT DROP

# 測試 SSH 能不能連、HTTP 通不通
```

**3. 阻擋特定 IP**

```bash
sudo iptables -I INPUT 1 -s 1.2.3.4 -j DROP
# 從 1.2.3.4 完全進不來

# 移除
sudo iptables -D INPUT -s 1.2.3.4 -j DROP
```

**4. 看 packet 統計**

```bash
sudo iptables -L -n -v
# pkts bytes target     prot ...
# 1234  56789 ACCEPT     tcp  ...
```

跑一段時間後看哪些規則被 hit。

**5. NAT 練習**

如果你 VPS 跑 docker / podman，看一下 nat table 的 rule（會看到大量 docker 自動加的）：

```bash
sudo iptables -L -t nat -n
```

## 自我檢核

- [ ] 4 表 5 鏈背得出
- [ ] 知道 packet 在 chain 中走的順序
- [ ] 寫得出基本 firewall ruleset（5+ 條規則）
- [ ] 知道 stateful firewall（conntrack）的價值
- [ ] DROP / REJECT / ACCEPT 各意義清楚
- [ ] 至少在 VPS 上實驗過（小心斷 SSH）

下一章看 nftables — 現代版的 iptables。

→ [Ch 19 nftables](./19-nftables.md)
