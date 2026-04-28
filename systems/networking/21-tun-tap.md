# Ch 21 — tun/tap interface

> 目標：搞懂 tun / tap 是什麼、怎麼運作、為什麼是所有 VPN 的核心。

## tun / tap 是什麼

**虛擬網路介面**，但「另一端」是 user-space 程式（不是物理網卡）。

```
 應用程式 ─── socket ─── kernel TCP/IP ─── tun0 ─── user-space 程式（VPN）
                                                          │
                                                          ▼
                                                       加密 / 包裝
                                                          │
                                                          ▼
                                                     真實 socket → 網路
```

tun/tap 讓你「**截獲整個 IP packet**」，user-space 程式能看到、改、加密、送到別處。

**所有 VPN（OpenVPN、WireGuard、IPSec）核心都是 tun/tap**。

## tun vs tap

| 類型 | Layer | 處理 |
|---|---|---|
| **tun** | 3 (network) | IP packet（沒 Ethernet header） |
| **tap** | 2 (link) | Ethernet frame（含 MAC） |

差別：

- tun — packet **送到** kernel 像來自網路，kernel 走 routing
- tap — frame **送到** kernel 像來自網卡，kernel 走 bridging

VPN 多用 **tun**（不需要 Ethernet 層）。需要 bridging（如某些 site-to-site VPN）用 tap。

## 建立 tun interface

```bash
sudo ip tuntap add tun0 mode tun
sudo ip link set tun0 up
sudo ip addr add 10.0.0.1/24 dev tun0
ip a show tun0
```

現在 `tun0` 是個 interface，IP 10.0.0.1/24。但**沒有「另一端」程式**處理它的 packet → 任何送到 tun0 的 packet 黑洞掉。

## 簡單 tun 程式

C / Python 都能寫。用 Python 例：

```python
# tun_demo.py
import os, fcntl, struct

TUNSETIFF = 0x400454ca
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000

# 開 /dev/net/tun
tun = os.open('/dev/net/tun', os.O_RDWR)

# Bind 到 tun0
ifr = struct.pack('16sH', b'tun0', IFF_TUN | IFF_NO_PI)
fcntl.ioctl(tun, TUNSETIFF, ifr)

print("tun0 ready, capturing packets...")
while True:
    packet = os.read(tun, 2048)
    print(f"Received {len(packet)} bytes:")
    print(' '.join(f'{b:02x}' for b in packet[:40]))
```

```bash
# 建 tun（如果還沒）
sudo ip tuntap add tun0 mode tun
sudo ip link set tun0 up
sudo ip addr add 10.0.0.1/24 dev tun0

# 跑 demo
sudo python3 tun_demo.py &

# 觸發 traffic（任何送到 10.0.0.0/24 的）
ping -c 3 10.0.0.5
```

你會看到 demo 印出 ICMP packet 的 hex。

**就這樣** — VPN 的本質就是「程式 read tun0，加密後 write 真實 socket」。

## VPN 工作流程（含 tun）

```
 應用程式 (curl)
     │
     ▼
 socket (open + send to 1.2.3.4)
     │
     ▼
 kernel TCP/IP stack
     │
     ▼
 routing 決定走 tun0
     │
     ▼
 ┌──── tun0 (虛擬 interface) ────┐
 │                               │
 │   ──→ user-space VPN 程式     │
 │       │                        │
 │       ▼ 加密 + 包 UDP         │
 │   socket → eth0 → 網路        │
 └───────────────────────────────┘
                 │
                 ▼
             VPN server
                 │
                 ▼
              對方 server (1.2.3.4)
```

關鍵：

- 應用程式不知道走 VPN
- 一切「魔法」在 tun0 → user-space → 加密 → 真實 socket

## 觀察 tun 流量

```bash
# 啟用 VPN（OpenVPN / WireGuard）後
ip a               # 看到 tun0 / wg0 等
sudo tcpdump -nn -i tun0     # 看 VPN「內部」流量
sudo tcpdump -nn -i eth0     # 看 VPN「加密後」的流量
```

兩個 interface 同時抓，能看到「明文 → 加密」的對應。

## 一個常見誤解：「tun / tap 是物理設備」

**錯**。它是純軟體 interface。但對 kernel / 應用程式來說，**跟真實網卡無區別**。

「**虛擬但功能完整**」是它的價值。

## 一個常見誤解：「VPN 用 tun 是因為 tun 加密」

**錯**。tun 自己**不加密**。它只是「**讓 user-space 看到 packet**」的機制。

加密是 user-space VPN 程式做的（如 WireGuard 用 ChaCha20）。

## 一個常見誤解：「TUN 跟 TAP 任選」

**部分對**。多數場景兩者都行，但：

- VPN client：tun 通常夠（IP 層 routing）
- bridge VPN：tap 必要（要 forward Ethernet frame）

OpenVPN 預設 tun，可選 tap。WireGuard 只支援 tun。

## 一個常見誤解：「tun 慢」

**部分對**。tun 的「**packet 跨 kernel-userspace 邊界**」確實慢。每 packet 一次 syscall。

但**現代實作優化好** — WireGuard 在 kernel 內，沒有 user-space 來回。OpenVPN 純 user-space 較慢。

性能：WireGuard > IPSec (kernel) > OpenVPN (user-space)。

## 動手練習

**1. 建個 tun 看一下**

```bash
sudo ip tuntap add tun0 mode tun
sudo ip link set tun0 up
sudo ip addr add 10.99.99.1/24 dev tun0
ip a show tun0
```

**2. 跑 Python tun demo**

按上面 code，看 packet 印出。

**3. 觀察 docker / VPN 的 interface**

```bash
ip a | grep -E "tun|wg|tap|docker|veth"
```

看你機器上有什麼虛擬 interface。

**4. 抓 VPN 流量**

如果你有 WireGuard / OpenVPN：

```bash
# 內部明文
sudo tcpdump -nn -i wg0    # WireGuard 介面

# 加密後
sudo tcpdump -nn -i eth0 'udp port 51820'   # WireGuard 走 UDP
```

對比兩邊 packet 數量、大小。

**5. 清理**

```bash
sudo ip link delete tun0
```

## 自我檢核

- [ ] 知道 tun / tap 是虛擬 network interface
- [ ] tun (L3) vs tap (L2) 差別
- [ ] 講得出 VPN 跟 tun 的關係
- [ ] 自己建過 tun interface
- [ ] 知道 user-space VPN 程式從 tun read packet 的工作流

下一章看 bridge / veth pair — Docker bridge 怎麼構造。

→ [Ch 22 bridge / veth pair](./22-bridge-veth.md)
