# Ch 29 — Shadowsocks

> 目標：搞懂 Shadowsocks 的設計、跟 VPN 差異、為什麼成為翻牆主流。

## Shadowsocks 是什麼

中國程序員 clowwindy 在 2012 年釋出的「**加密 SOCKS5 proxy**」，專為對抗 GFW 設計。

設計原則：

- **看起來像隨機 byte**（沒明顯協定特徵）
- **client + server 預設密碼 + cipher**（沒握手暴露身份）
- **跟 SOCKS5 同界面**（應用設定簡單）
- **輕量**（C / Python / Go 各種實作）

GFW 對 OpenVPN / IPSec 易識別 → 直接 block。Shadowsocks **流量像噪音**，初期效果好。

## SS vs VPN

| 維度 | Shadowsocks | VPN (WireGuard) |
|---|---|---|
| 範圍 | 應用層（瀏覽器 / 設定的程式） | 整台機器 |
| 加密 | 對稱 cipher（AES, ChaCha20） | 公私鑰 |
| 流量特徵 | 隨機 | 有 (UDP packet 結構) |
| GFW 對抗 | 中（早期強，現代被識別） | 弱（UDP 易被 throttle） |
| 設定 | 簡單 (account + password) | 較複雜 (key pairs) |
| 速度 | 快 | 快 |

## SS 工作流

```
 your browser
      │ SOCKS5（明文）
      ▼
 SS local client (你機器跑)
      │ 加密後傳
      ▼
 SS server (VPS)
      │ 解密
      ▼
 destination
```

兩件事：

1. browser 把流量送本機 SS client（SOCKS5）
2. SS client 加密 + 偽裝後送 SS server
3. SS server 解密、forward 到 destination

## 設定 SS server

```bash
sudo apt install shadowsocks-libev

# 設定檔 /etc/shadowsocks-libev/config.json
{
    "server": "0.0.0.0",
    "server_port": 8388,
    "password": "your-strong-password",
    "method": "aes-256-gcm",
    "timeout": 60,
    "fast_open": true
}

# 啟動
sudo systemctl enable shadowsocks-libev
sudo systemctl start shadowsocks-libev
```

開 firewall：

```bash
sudo ufw allow 8388/tcp
sudo ufw allow 8388/udp
```

## 設定 SS client

```bash
# Linux
sudo apt install shadowsocks-libev   # 同 package

# /etc/shadowsocks-libev/config.json (本機)
{
    "server": "1.2.3.4",          # VPS IP
    "server_port": 8388,
    "password": "your-strong-password",
    "method": "aes-256-gcm",
    "local_address": "127.0.0.1",
    "local_port": 1080,
    "timeout": 60,
    "fast_open": true
}

# 啟動 client
ss-local -c /etc/shadowsocks-libev/config.json
```

之後 `localhost:1080` 是 SOCKS5 → 流量走加密管道到 VPS。

## SS 配 proxychains / browser

### proxychains

```bash
sudo apt install proxychains
sudo vi /etc/proxychains.conf
# 最後加
socks5 127.0.0.1 1080

# 用
proxychains curl ifconfig.me
# 應該顯示 VPS IP
```

### Browser (Firefox)

Settings → Network → Manual → SOCKS5 = 127.0.0.1:1080

## 加密 method

老 method 已不安全：

- `rc4`、`aes-256-cfb` — **不要用**

現代用：

- **`aes-256-gcm`**（推）
- **`chacha20-ietf-poly1305`**（推，CPU 弱時用）

## SS 跟 SS-Plugin

「**插件**」讓 SS 流量混淆：

- **simple-obfs**：偽裝成 HTTP / TLS（GFW 早期繞過）
- **v2ray-plugin**：偽裝 WebSocket / TLS（更強，已過時）

```bash
# server config 加
"plugin": "obfs-server",
"plugin_opts": "obfs=tls"

# client 同樣加
"plugin": "obfs-local",
"plugin_opts": "obfs=tls;obfs-host=cloud.tencent.com"
```

但**現代 GFW 都能識別 obfs**。需要 V2Ray / Trojan 等更新工具（Ch 30）。

## SS 的歷史

2012：clowwindy 釋出  
2014-2015：成為翻牆主流  
2015：clowwindy 被中國公安「**喝茶**」  
2015：clowwindy 把專案交給社群  
2016+：被 GFW 加強識別 → SS 流量易被 detect → 用戶轉向 V2Ray  
2020+：純 SS 翻牆效果差，**ShadowsocksR (SSR)** / **V2Ray** / **Trojan** 取代

「**SS 是翻牆 1.0**」的代名詞。**現代用 V2Ray / Trojan**。

## 一個常見誤解：「SS 安全 = SS 加密強」

**部分對**。AES-256-GCM 確實強。

但 SS 設計**沒身份驗證**（沒前向安全 / 完整性簽名）— **誰知道你 password 就連得上**。如果 password 弱、被偷 → 整個 stream 解密。

## 一個常見誤解：「SS 還是當前主流翻牆」

**錯**。2025 年 SS 已不主流。GFW 對 SS 識別好，**Trojan / V2Ray VLESS 才是現代主流**。

## 一個常見誤解：「SS 跟 SOCKS5 是同個東西」

**錯**。SS 用 SOCKS5 對 client 端，但「**SS server ↔ SS client 之間是自家加密協定**」，不是 SOCKS5。

「**外部接口 SOCKS5、內部加密 SS**」。

## 一個常見誤解：「SS 在中國以外用沒意義」

**錯**。SS 仍是「**輕量加密 SOCKS5 proxy**」的好選擇：

- 公共 WiFi 加密
- ISP 隱私
- 應用層 proxy（不需要 root）

只是「**對抗強審查**」用 V2Ray / Trojan 更好。

## 動手練習

**1. 自架 SS server + client**

按本章流程，VPS 跑 ss-server、本機跑 ss-local。

驗證：

```bash
# 不走 SS
curl ifconfig.me

# 走 SS
curl --socks5 127.0.0.1:1080 ifconfig.me

# 應該不同
```

**2. 測 throughput**

```bash
# 走 SS
iperf3 -c <VPS-IP> --bind-interface lo
# 或用 proxychains
proxychains iperf3 -c <VPS-IP>
```

跟 WireGuard 比。

**3. 故意密碼錯**

client 改錯 password 看會怎樣（連線失敗 / timeout）。

**4. 看 SS traffic**

```bash
sudo tcpdump -nn -i any -X 'port 8388' -c 10
```

看到的 payload 是隨機 byte（vs HTTP / SS 內部明文）。

**5. 玩 client GUI**

```bash
# Mac: ShadowsocksX-NG
# Windows: Shadowsocks
# 或 web client: shadowsocks-web
```

GUI 比命令列友善。

## 自我檢核

- [ ] 知道 SS 是「加密 SOCKS5 proxy」
- [ ] SS vs VPN 差別清楚
- [ ] 自架 SS server + client 過
- [ ] 知道 SS 已不是現代主流（V2Ray / Trojan 取代）
- [ ] 知道 SS 沒身份驗證的設計弱點
- [ ] 配過 proxychains 走 SS

下一章看 V2Ray / Xray — 現代翻牆主流。

→ [Ch 30 V2Ray / Xray](./30-v2ray-xray.md)
