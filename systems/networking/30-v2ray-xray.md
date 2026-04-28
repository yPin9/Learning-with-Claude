# Ch 30 — V2Ray / Xray

> 目標：認識 V2Ray / Xray 平台、4 大 protocol（VMess / VLESS / Trojan / Shadowsocks），知道現代翻牆生態。

## V2Ray 是什麼

2015 年起的「**proxy 平台**」，比 SS 更強：

- 多 protocol 支援
- 多 transport 支援（TCP / WebSocket / HTTP/2 / QUIC）
- 路由規則複雜
- 統計 / 監控
- 可串接（chain）

「**Shadowsocks 替代品**」。GFW 對 SS 識別好後，V2Ray 上位。

## Xray 是什麼

V2Ray 的 fork（2020 年起），由 RPRX 主導。原因：

- V2Ray 開發節奏慢
- Xray 加新 protocol（VLESS, XTLS）更積極

**現代翻牆界 Xray 用得比 V2Ray 多**。配置高度相容（同設計）。

## 4 大 protocol

| Protocol | 特徵 | 對抗 GFW |
|---|---|---|
| **VMess** | V2Ray 自家加密 | 已被識別，不推 |
| **VLESS** | 無加密 + UUID 認證 | 配 TLS 用，現代主流 |
| **Trojan** | 偽裝 HTTPS | 強 |
| **Shadowsocks** | 同 SS | 中-弱 |

### VMess（過時）

V2Ray 自家「加密 proxy」。問題：

- 流量有特徵（被 GFW 識別）
- 加密 overhead

**已不推薦新部署**。

### VLESS（現代主流）

「**沒加密**」的傳輸 + UUID 身份驗證 — 看似 insecure，但**搭配 TLS 用**：

```
 client ─── VLESS over TLS ──── server
                ↑
              加密由 TLS 提供
              VLESS 只負責 routing
```

優點：

- 設計簡單
- TLS 處理加密 → 看起來像 HTTPS
- **支援 XTLS Vision / Reality** — 進階偽裝

VLESS 是 2024-2025 翻牆主力。

### Trojan

直接用 TLS，連 VLESS layer 都沒。

```
 client ─── Trojan over TLS ──── server
                ↑
            完全偽裝 HTTPS server
```

從外觀看，**100% 是 HTTPS server**。沒辦法區分。

「最強的偽裝」 = 跟真實 HTTPS server 共存（同 nginx 同 IP）。

### Shadowsocks

V2Ray / Xray 也支援 SS protocol（向下相容）。

## Reality（最新）

XTLS Reality 是 2023 年的新 protocol：

- **不需要自己 cert**
- 用「真實大公司 server 的 cert」做 TLS handshake（如 Apple / Cloudflare 的 cert）
- 流量看起來是「**連 Apple**」

對抗 GFW 的最新利器。**配置複雜，但成效驚人**。

## 設定 Xray-VLESS-TLS（典型現代配置）

### 前置：domain + cert

需要：

- 一個 domain（如 `vpn.yourdomain.com`）
- DNS A record 指向 VPS
- TLS cert（Let's Encrypt 免費）

### Server 配置

```bash
# 安裝 Xray
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install

# /usr/local/etc/xray/config.json
{
  "log": {
    "loglevel": "warning"
  },
  "inbounds": [
    {
      "port": 443,
      "protocol": "vless",
      "settings": {
        "clients": [
          {
            "id": "your-uuid-here",
            "flow": ""
          }
        ],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "tcp",
        "security": "tls",
        "tlsSettings": {
          "certificates": [
            {
              "certificateFile": "/etc/letsencrypt/live/vpn.yourdomain.com/fullchain.pem",
              "keyFile": "/etc/letsencrypt/live/vpn.yourdomain.com/privkey.pem"
            }
          ]
        }
      }
    }
  ],
  "outbounds": [
    {
      "protocol": "freedom"
    }
  ]
}
```

UUID 用 `xray uuid` 生成。

啟動：

```bash
sudo systemctl restart xray
sudo systemctl status xray
```

### Client 配置

各種 GUI client：

- **v2rayN**（Windows）
- **Shadowrocket / Quantumult X**（iOS，付費）
- **v2rayNG**（Android）
- **Qv2ray**（跨平台）

設定：

```
Protocol: VLESS
Address: vpn.yourdomain.com
Port: 443
UUID: <your-uuid>
Network: tcp
Security: tls
SNI: vpn.yourdomain.com
```

## 路由規則（V2Ray / Xray 強項）

可以設「**哪些 traffic 走 proxy、哪些直連**」：

```json
{
  "routing": {
    "rules": [
      {
        "type": "field",
        "domain": ["geosite:cn"],
        "outboundTag": "direct"
      },
      {
        "type": "field",
        "ip": ["geoip:cn"],
        "outboundTag": "direct"
      },
      {
        "type": "field",
        "outboundTag": "proxy",
        "network": "tcp,udp"
      }
    ]
  }
}
```

中國 domain / IP 直連，其他走 proxy。**「智能分流」標準配置**。

## 一個常見誤解：「VLESS 不加密就不安全」

**錯**。VLESS 自身不加密，但**配 TLS 用**就有加密。

「**TLS 處理加密、VLESS 處理路由 / 認證**」是現代設計哲學 — 一個 protocol 做一件事。

## 一個常見誤解：「翻牆工具排隊：SS → SSR → V2Ray → Trojan → ...」

**部分對**。新工具不一定取代舊：

- SS 還能用（弱場景）
- V2Ray 還流行
- Trojan 強但不適合所有場景
- Reality 最新但配置複雜

「**多備幾個工具**」是翻牆老手的策略。

## 一個常見誤解：「翻牆工具越多越安全」

**錯**。**一個專注配置的工具** > **多個半設好的工具**。

每個翻牆工具都需要：

- 強 password / UUID
- Server 安全配置（fail2ban, firewall）
- 定期更新

選 1-2 個用熟比 5 個都半生不熟好。

## 一個常見誤解：「翻牆對網路安全不影響」

**錯**。多數翻牆工具：

- 開了端口暴露 server
- 流量都過 VPS（第三方）
- VPS provider 可能 log

「**翻牆 = 信任 VPS provider 跟 protocol**」。

## 動手練習

**1. 自架 Xray + VLESS + TLS**

要：

- domain
- VPS  
- Let's Encrypt cert（Ch 36 詳細）

按本章流程，連得上 = 成功。

**2. 測連線**

```bash
# 連到 Xray-managed proxy
curl --socks5 127.0.0.1:10808 ifconfig.me   # GUI client 預設 local port
```

**3. 看 traffic 像 HTTPS**

```bash
# 在 server
sudo tcpdump -nn -i any -X 'port 443' -c 5
```

看 packet — 應該全是 TLS handshake / encrypted。**跟真 HTTPS 不可區分**。

**4. 路由分流**

設 routing rules，讓 google.com 走 proxy、example.com 直連。`curl ifconfig.me` 跟 `curl ifconfig.me --socks5 ...` 應該不同 IP。

**5. 對比 V2Ray / Xray / Trojan**

各設一份，相同 condition 跑 throughput 測試。看誰最快、誰最隱蔽。

## 自我檢核

- [ ] 知道 V2Ray / Xray 是 proxy 平台
- [ ] VMess / VLESS / Trojan / SS 4 種 protocol 對比
- [ ] 知道 VLESS + TLS 是現代主流
- [ ] Reality 是最新對抗 GFW 利器
- [ ] 自架過 Xray VLESS（如果在乎翻牆）
- [ ] 知道路由規則（geoip 分流）

下一章看 GFW 對抗演進史 — 為什麼工具一直在進化。

→ [Ch 31 GFW 對抗演進史](./31-gfw-evolution.md)
