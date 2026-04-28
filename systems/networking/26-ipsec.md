# Ch 26 — IPSec

> 目標：認識 IPSec 的兩個 sub-protocol（AH / ESP）、兩個模式（transport / tunnel）、為什麼企業 site-to-site 還用它。

## IPSec 是什麼

**IP Security** — 1995 年起的協定家族（RFC 2401, 4301）。

**不是單一協定**，是「**一組協定的集合**」：

- **IKE** (Internet Key Exchange) — 協商 + 交換 key
- **AH** (Authentication Header) — 完整性 + 驗證
- **ESP** (Encapsulating Security Payload) — 加密 + 完整性

實際用最多 **IKE + ESP**。

## 為什麼還用 IPSec

1. **企業 site-to-site VPN 標準** — Cisco / Juniper / Palo Alto 互通
2. **OS 內建支援** — Windows / Mac / iOS / Android 不需安裝 client
3. **kernel 實作** — 性能好
4. **跟 MPLS / SD-WAN 整合**

但**個人用 IPSec 痛苦**：

- 設定極複雜（10+ 個參數要對齊）
- 沒對應好就連不上、debug 難
- NAT 穿透問題

## AH vs ESP

| Protocol | 提供 |
|---|---|
| **AH** (IP protocol 51) | 完整性 + 驗證，**沒加密** |
| **ESP** (IP protocol 50) | 加密 + 完整性 + 驗證 |

99% 場景用 ESP。AH 幾乎沒用了（沒加密，且 NAT 不友善）。

## Tunnel vs Transport mode

### Transport mode

只加密 IP packet 的 payload（TCP/UDP 內容）：

```
 原始：[ IP header | TCP | data ]
 ESP：  [ IP header | ESP | TCP | data ] ← TCP+data 加密
```

src/dst IP 看得到。**端到端**加密用。

### Tunnel mode

整個原始 IP packet 包進新 IP packet：

```
 原始：[ IP header | TCP | data ]
 ESP：  [ NEW IP header | ESP | IP header | TCP | data ] ← 全部加密
```

新 IP header 是「**VPN gateway 對 VPN gateway**」。原 src/dst 隱藏。**Site-to-site VPN 用**。

## IKE：金鑰交換

兩端要建立加密 channel 前，先**協商 cipher + 交換 key**。這就是 IKE。

兩個版本：

- **IKEv1**：1998 年，複雜
- **IKEv2**：2005 年，簡化、更可靠、roaming 支援

**新部署用 IKEv2**。IKEv1 還在因為老設備。

IKE 跑在 UDP port 500 / 4500。

## strongSwan / Libreswan

Linux 上最常用的 IPSec 實作：

- **strongSwan**：較新，IKEv2 支援好
- **Libreswan**：FreeS/WAN 後繼，有些企業愛用

```bash
sudo apt install strongswan strongswan-pki

ipsec --version
```

## 自架 IPSec（site-to-site，strongSwan）

兩個 office 互通範例。極簡版：

### Office A (1.2.3.4, LAN 192.168.1.0/24)

`/etc/ipsec.conf`：

```
config setup
    charondebug="ike 2, knl 2, cfg 2"

conn site-to-site
    auto=start
    keyexchange=ikev2
    
    # local
    left=1.2.3.4
    leftsubnet=192.168.1.0/24
    leftid=@officeA
    leftauth=psk
    
    # remote
    right=5.6.7.8
    rightsubnet=10.0.0.0/24
    rightid=@officeB
    rightauth=psk
    
    ike=aes256-sha256-modp2048
    esp=aes256-sha256-modp2048
```

`/etc/ipsec.secrets`：

```
@officeA @officeB : PSK "your-pre-shared-key-here"
```

### Office B (5.6.7.8, LAN 10.0.0.0/24)

對稱配置（左右互換）：

`/etc/ipsec.conf`：

```
conn site-to-site
    auto=start
    keyexchange=ikev2
    
    left=5.6.7.8
    leftsubnet=10.0.0.0/24
    leftid=@officeB
    leftauth=psk
    
    right=1.2.3.4
    rightsubnet=192.168.1.0/24
    rightid=@officeA
    rightauth=psk
    
    ike=aes256-sha256-modp2048
    esp=aes256-sha256-modp2048
```

`/etc/ipsec.secrets`：

```
@officeB @officeA : PSK "your-pre-shared-key-here"
```

### 兩端啟動

```bash
sudo systemctl restart strongswan-starter
sudo ipsec status
sudo ipsec statusall
```

## 觀察 IPSec

```bash
# 看 SA (Security Association)
sudo ip xfrm state

# 看 policy
sudo ip xfrm policy

# 看 log
sudo journalctl -u strongswan -f
```

## NAT 穿透問題

IPSec ESP packet（IP protocol 50）**沒 port** → NAT 處理麻煩。

解決：

- **NAT-T** (NAT Traversal) — UDP encapsulation，把 ESP 包進 UDP 4500
- 雙方都支援自動協商

但**對複雜 NAT（CGNAT / multi-NAT）仍會壞**。對比 WireGuard / OpenVPN 的 NAT 友善度差很多。

## 一個常見踩雷：兩端 cipher 不一致

兩端的 `ike=` 跟 `esp=` 要完全一樣（或有 overlap）。一邊 `aes256-sha256` 一邊 `aes128-sha1` → 連不上。

```bash
sudo journalctl -u strongswan -n 50 | grep -i "no proposal"
```

看 log 有 `no proposal chosen` → cipher 不對齊。

## 一個常見踩雷：ID 不對

`leftid` / `rightid` 要對應。一邊 `@officeA` 一邊看作對方時要設 `right` `@officeA`。

「**雙方視角**」對齊。常出錯。

## 一個常見踩雷：firewall 擋

需要開：

- UDP 500 (IKE)
- UDP 4500 (NAT-T)
- IP protocol 50 (ESP，**不是 port，是 IP protocol number**)

iptables：

```bash
sudo iptables -A INPUT -p udp --dport 500 -j ACCEPT
sudo iptables -A INPUT -p udp --dport 4500 -j ACCEPT
sudo iptables -A INPUT -p esp -j ACCEPT
```

## 一個常見誤解：「IPSec 比 WireGuard 安全」

**不一定**。IPSec 老 cipher（DES、3DES、MD5）已不安全。**現代強配置**（AES-256-GCM + SHA-256）才安全。

WireGuard default cipher 已經是現代強算法，**不會選錯**。

## 一個常見誤解：「IPSec 適合個人用」

**錯**。IPSec 設定極複雜，個人用 OpenVPN / WireGuard 簡單百倍。

IPSec 強在**企業環境** + **跟硬體 router 互通**。

## 一個常見誤解：「IPSec 一定 site-to-site」

**錯**。IPSec 也能 remote access（個人 VPN）— 商業叫 IKEv2 VPN。Windows / iOS 內建支援。

但配置仍比 WireGuard 複雜。

## 動手練習

**1. 看你機器有沒有 IPSec module**

```bash
lsmod | grep -E "esp|ah|xfrm"
```

通常 ESP 是 kernel 內建。

**2. 用 strongSwan 試 self-loopback**

如果沒第二台 server，可以用 docker 模擬：

```bash
docker run -d --name vpn-server --cap-add=NET_ADMIN --cap-add=SYS_MODULE -e PSK=test123 ipsec-server-image
```

或單純看 strongSwan 的 example config：

```bash
ls /usr/share/doc/strongswan-starter/examples/
```

**3. 對比 IPSec 跟 WireGuard 配置複雜度**

對自己同樣 use case：

- 寫 WireGuard config（10 行）
- 寫 IPSec config（30+ 行）

數一下行數差。

**4. 看商業設備的 IPSec UI**

(若你能用) 看 Cisco ASA / Fortigate / Sophos 的 web UI 配 IPSec。**極簡 — 但底層還是上面那套**。

## 自我檢核

- [ ] 知道 IPSec 的 AH / ESP / IKE 角色
- [ ] Tunnel vs Transport mode 差別
- [ ] 知道 IKEv1 vs IKEv2
- [ ] 知道 IPSec NAT 穿透難
- [ ] 知道個人用為什麼選 WireGuard / OpenVPN

下一章對比三家 VPN，幫你選擇 production 用什麼。

→ [Ch 27 三家對比與選擇](./27-vpn-comparison.md)
