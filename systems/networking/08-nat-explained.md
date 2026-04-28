# Ch 8 — NAT 完整解析

> 目標：搞懂 NAT 怎麼讓多裝置共用 1 個公網 IP、各種 NAT 類型的差別、對 P2P 跟 VPN 的影響。

## NAT 是什麼

**Network Address Translation** — 路由器把 packet 的**源 / 目標 IP 跟 port**改寫，達成多裝置共用 1 個公網 IP。

```
                              公網 IP: 203.0.113.5
                                      │
   ┌──────────┐                       ▼
   │ 192.168.1.10 │ ──┐         ┌──────────┐
   ├──────────┤      ├─────────►│ 路由器   │ ─────► Internet
   │ 192.168.1.20 │ ──┤         │ (NAT)    │
   ├──────────┤      ├          └──────────┘
   │ 192.168.1.30 │ ──┘
   └──────────┘
```

3 台設備共用 1 個公網 IP。每個對外連線時，**路由器改寫** packet，讓對方以為是路由器在連。

## NAT 怎麼運作

家裡 192.168.1.10 想連 example.com (93.184.216.34:443)：

### Step 1：你送 SYN

```
 src: 192.168.1.10:54321
 dst: 93.184.216.34:443
```

到路由器。

### Step 2：路由器改寫（SNAT）

```
 src: 203.0.113.5:62000  ← 改成路由器公網 IP + 路由器選的新 port
 dst: 93.184.216.34:443
```

**路由器記住對應**：

```
 NAT table:
 內部                     外部
 192.168.1.10:54321 ◄──► 203.0.113.5:62000
```

送出去。

### Step 3：server 回包

```
 src: 93.184.216.34:443
 dst: 203.0.113.5:62000   ← 看到的是路由器
```

到路由器。

### Step 4：路由器改寫（DNAT）回去

```
 src: 93.184.216.34:443
 dst: 192.168.1.10:54321   ← 查 NAT table 反向改寫
```

送給 192.168.1.10。

整個過程：

- 你以為自己直接跟 server 通
- server 以為是路由器跟它通
- 路由器當「**翻譯員**」

## 4 種 NAT 類型（Cone NAT 分類）

按「**外部 IP 的對應規則**」分：

### 1. Full Cone NAT（最開放）

「同一個內部 IP:port → 永遠用同一個外部 IP:port」、「**任何外部主機**都能用這外部 port 主動連回」

```
 內部 192.168.1.10:54321 ◄──► 外部 203.0.113.5:62000
 任何 server 都能送到 203.0.113.5:62000，路由器轉給你
```

### 2. Restricted Cone NAT

跟 Full Cone 同，但**只有「你主動連過的 IP」**能用這 port 連回。

### 3. Port-Restricted Cone NAT

更嚴：只有「**你主動連過的 IP:port**」能用這 port 連回。

### 4. Symmetric NAT（最封閉）

「同一個內部 IP:port，但連**不同 server** 就用**不同的外部 port**」 + 「只有那個 server 能用對應外部 port 連回」。

```
 內部 192.168.1.10:54321 → server A (1.2.3.4) → 用 203.0.113.5:62000
 內部 192.168.1.10:54321 → server B (5.6.7.8) → 用 203.0.113.5:62001  ← 不同 port！
```

「**同樣的內部 socket，外部對不同 server 不同 port**」 — 這破壞了 P2P 的假設。

家用路由器多數是 Restricted / Port-Restricted；企業 / CGNAT 常是 Symmetric。

## NAT 對 P2P 的影響

P2P（如 BitTorrent / WebRTC / VoIP）需要兩台設備互相直連：

```
 A (NAT 後)  ←─────?─────→  B (NAT 後)
```

兩端都在 NAT 後 → 都沒公網 IP → 怎麼連？

### NAT 穿透（NAT traversal）

幾種技術：

#### 1. **STUN**（Session Traversal Utilities for NAT）

A 跟 B 各自連到一個公網 STUN server，server 告訴他們各自的「**外部 IP:port**」。然後兩端嘗試直連對方的外部位址。

對 Cone NAT 通常成功；對 Symmetric NAT 通常失敗。

#### 2. **TURN**（Traversal Using Relays around NAT）

如果 STUN 失敗 → 用第三方 server 中繼。

代價：流量都過 server，貴、慢。

#### 3. **ICE**（Interactive Connectivity Establishment）

WebRTC 的標準 — 同時試 STUN 跟 TURN，挑成功的。

#### 4. **Hole Punching**

兩端**同時**對對方送 packet，建立 NAT mapping。

#### 5. **UPnP / NAT-PMP**

家用路由器允許設備「**主動要求**」開個 port forwarding。但很多路由器禁用 / 不支援。

## Port forwarding

**主動把外部 port 轉到內部設備**。家用路由器設定：

```
外部 port 8080 → 內部 192.168.1.20:80
```

對外 `203.0.113.5:8080` = 內部設備的 web server。

用途：

- 自架 web server / SSH server
- 家裡某設備接收連線（遊戲主機 / NAS）
- 雖然在 NAT 後，但**手動戳一個洞**

## CGNAT（Carrier-Grade NAT）

ISP 級的 NAT — **多用戶共用 1 個公網 IP**。

```
       公網 IP: 100.64.50.5     ←  ISP 用 100.64.0.0/10 範圍
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
   用戶 A 路由器       用戶 B 路由器
   (內部 192.168.1.x)  (內部 192.168.1.x)
```

ISP 沒夠多公網 IP 給每個用戶 → 多用戶共用 → 兩層 NAT。

副作用：

- 用戶**完全無法**架 server / port forward
- P2P 更難
- 看起來「同一個 IP」共享，但實際上是 1000+ 用戶共享

亞太區 / 行動網路 / 4G LTE 大量用 CGNAT。

要避開：

- 買付費「公網 IP」option
- 用 VPN 到自己 VPS（VPS 有真公網 IP）

## NAT 對協定的影響

NAT 只認 TCP / UDP（看 port）。其他 protocol 麻煩：

- **ICMP**：理論上沒 port，但 NAT 用 ID 欄位當 port 模擬
- **IPSec ESP**：傳輸模式 break NAT（Ch 26 詳細）
- **GRE / 其他 tunnel**：看 NAT 設備支援

某些 NAT 設備有「**ALG（Application Layer Gateway）**」 — 看應用層協定（FTP / SIP）改寫內容。但常常壞事，建議 disable。

## 一個常見誤解：「NAT 是安全機制」

**部分對**。NAT 帶來「**外部主動連不進來**」的副作用，看似 firewall。

但 **NAT 設計目的是省 IP，不是安全**。真正 firewall 要明確配規則（Ch 18）。

「NAT = 安全」依賴的話，內網單一漏洞就突破。

## 一個常見誤解：「IPv6 沒 NAT」

**部分對**。IPv6 位址夠多，**理論上不需要 NAT**。但實務上：

- **NAT66**：IPv6 的 NAT，少用但存在
- **NPTv6**：IPv6 prefix translation
- 多數家用 IPv6 不做 NAT，每設備直接對外

但「**沒 NAT** = 每設備直接暴露到 Internet」，所以 IPv6 預設 firewall 也要配好。

## 一個常見誤解：「Symmetric NAT 完全無解」

**錯**。WebRTC 的 TURN 一定能繞過。代價是流量過 TURN server。

某些「**hole punching**」技巧也能繞過 Symmetric NAT，雖然成功率低。

## 動手練習

**1. 看自己的 NAT mapping**

```bash
# 連個 server 後立刻看
curl https://example.com &
ss -tan
```

看 src port = 你的內部 port。對方看到的是你公網 IP + 路由器分配的 port（不同）。

**2. 公網 IP vs 內部 IP**

```bash
ip a                       # 內部 IP
curl ifconfig.me           # 公網 IP
```

通常不同（除非你直接連到公網）。

**3. 試 STUN server**

```bash
# 安裝 stun 工具
sudo apt install stuntman-client
stunclient stun.l.google.com 19302
# 印出你的「外部 IP:port」
```

跑兩次，**如果你 NAT 是 Symmetric，兩次 port 不同**。Cone NAT 兩次 port 一樣。

**4. 設 port forward（如果家裡能進路由器設定）**

家裡路由器 admin page → port forwarding → 設一個 forward。例如：

```
 外部 8080 → 192.168.1.10:80
```

從外部試 `curl http://YOUR_PUBLIC_IP:8080`。

**5. NAT 對 SSH 的影響**

家裡電腦不能直接從外面 SSH（除非 port forward）。試：

- 在 VPS 上 SSH 你家 → 失敗（沒公網 IP）
- 在家裡 SSH VPS → 成功（VPS 有公網 IP）

## 自我檢核

- [ ] 解釋 NAT 怎麼運作（4 步流程）
- [ ] 知道 4 種 NAT 類型對 P2P 的影響
- [ ] 知道 STUN / TURN / ICE 是什麼
- [ ] 知道 CGNAT 為什麼存在、影響什麼
- [ ] 跑過 STUN client 看自己 NAT 類型
- [ ] 知道「NAT ≠ firewall」

Part 2 結束。下一個 Part 看應用層 — DNS / HTTP / TLS / SSH。

→ [Ch 9 DNS](./09-dns.md)
