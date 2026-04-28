# Ch 28 — HTTP Proxy / SOCKS5

> 目標：搞懂 HTTP Proxy 跟 SOCKS5 的本質、跟 VPN 的差別、各自應用場景。

## Proxy 是什麼

「**中間人**」 — 客戶端不直接連目標，而是叫 proxy 代為連、回傳結果。

```
 client ──────► proxy ──────► destination
        request           request
        (我想連 example.com)
                          (proxy 替你連)
        ◄──────         ◄──────
        response          response
```

跟 VPN 不同：proxy 是 **應用層** 的，每個應用要單獨設定。

## 為什麼用 Proxy

- **匿名 / 翻牆**：destination 看到的是 proxy IP
- **快取**：企業 / ISP proxy 快取常見資源
- **過濾**：黑名單 / 白名單管制
- **負載平衡**：多 backend 分流（reverse proxy）
- **Inspect**：企業 audit traffic（含 SSL inspection）

## HTTP Proxy

最簡單的 proxy — 只代理 HTTP / HTTPS。

### HTTP（明文）

client 送 request 到 proxy：

```
GET http://example.com/path HTTP/1.1
Host: example.com
```

注意：**Request line 是完整 URL**，不是只 path（直接連時是 `GET /path`）。

proxy 收到後 → 連 example.com → 取結果 → 回 client。

### HTTPS（CONNECT method）

HTTPS 是端到端加密，proxy 不能解密。用 **CONNECT method** 建立 tunnel：

```
client 送：
CONNECT example.com:443 HTTP/1.1
Host: example.com:443

proxy 連到 example.com:443，回：
HTTP/1.1 200 Connection Established

之後 client ↔ proxy ↔ destination 用 raw TCP（client 跟 destination 做 TLS 握手）
```

proxy 只是「**中介管道**」，看不到加密內容。

### 配置 HTTP Proxy

```bash
# 環境變數
export http_proxy=http://proxy.company.com:8080
export https_proxy=http://proxy.company.com:8080

# 認證
export http_proxy=http://user:pass@proxy.company.com:8080

# curl
curl --proxy http://proxy:8080 https://example.com

# wget
wget --proxy=on --http-proxy=proxy:8080 http://example.com

# git
git config --global http.proxy http://proxy:8080
```

## SOCKS5

更通用的 proxy — **任意 TCP / UDP**，不只 HTTP。

### SOCKS 演進

- SOCKS4：1992，TCP only，沒 auth
- SOCKS4a：DNS 也走 proxy
- **SOCKS5**：1996（RFC 1928），TCP + UDP + auth + IPv6

「**SOCKS5 是現代標準**」。

### SOCKS5 流程

```
 client ─── 連 proxy:1080
      ──► greeting (auth methods)
      ◄── chosen auth
      ──► auth (username/pass etc)
      ◄── auth result
      ──► CONNECT example.com:443
      ◄── connect result
      ◄── ↕ ─── raw bytes (TCP)
```

跟 HTTP CONNECT 類似，但**任意協定** (TCP/UDP/IPv6 都行)。

### 配置 SOCKS5

很多應用支援：

```bash
# curl
curl --socks5 proxy:1080 https://example.com
curl --socks5-hostname proxy:1080 https://example.com   # DNS 也走 proxy

# Firefox：Settings → Network → Manual proxy → SOCKS5
# Chrome：用 extension 或啟動參數
```

### SSH 內建 SOCKS5 server

SSH 一行建 SOCKS5 proxy：

```bash
ssh -D 1080 user@vps
```

之後本機 `localhost:1080` 是 SOCKS5 proxy → 走 VPS。**極簡 VPN 替代**。

```bash
# 用它
curl --socks5 localhost:1080 https://example.com
```

## VPN vs Proxy

| 維度 | VPN | Proxy |
|---|---|---|
| 範圍 | 整台機器 | 應用 / browser |
| 層級 | L3 (IP) | L4-7 |
| 設定 | OS 層 / 整體 | 每應用 |
| 加密 | 是 | 視 proxy 而定 |
| Speed overhead | 中 | 低 |
| DNS | 走 VPN（如果配對） | 看設定（容易 leak） |
| Routing | 整路 | 應用自己決定 |

簡單版：

- **VPN**：「整台用」  
- **Proxy**：「單一程式用」

## 透明 Proxy（Transparent Proxy）

某些場景：proxy **不需要 client 配置**，路由器自動把 traffic 導 proxy。

例：企業 / 學校網路。你瀏覽器以為直連 google.com，**實際 routes 過 proxy**。

技術：iptables `REDIRECT` / `TPROXY`。

副作用：你不知道流量被中間人，**HTTPS 偵測得到**（因為 TLS cert 對不上）。

## Reverse Proxy

跟「正向 proxy」相反 — **server 端**的中介：

```
 internet client ──► reverse proxy ──► backend server 1
                  (nginx / haproxy)  ──► backend server 2
                                     ──► backend server 3
```

用途：

- **load balance**：多 backend 分流
- **SSL termination**：proxy 處理 HTTPS，backend 用 HTTP（簡化）
- **快取**：減少 backend load
- **safety**：backend 不直接暴露
- **routing**：按 URL 分流到不同服務

nginx / haproxy / Caddy 都是 reverse proxy。**現代 web app 必備**。

Ch 36 詳細。

## 一個常見誤解：「Proxy 跟 VPN 一樣安全」

**錯**。Proxy 不一定加密。HTTP proxy 走明文，**任何中間人能看**。

要安全用：

- HTTPS proxy（少見）
- SOCKS5 over TLS / SSH tunnel
- 連 proxy 的 channel 自己加密

## 一個常見誤解：「設了 proxy 整台機器都走」

**錯**。proxy 是**應用層**設定。設 `http_proxy` 環境變數**只影響讀這個變數的程式**（curl / wget / git 等）。

瀏覽器 / 其他應用要單獨設。

要全機走 → VPN 或 transparent proxy。

## 一個常見誤解：「DNS 一定走 proxy」

**部分對**。看設定：

- `curl --socks5 proxy:1080`：**DNS 在本機解**，再連 IP
- `curl --socks5-hostname proxy:1080`：**DNS 也走 proxy**

「DNS leak」常見場景：你以為翻牆但 ISP 看到你查了 google.com。

## 一個常見誤解：「Proxy 都很慢」

**部分對**。HTTP proxy 額外解析請求 / 快取查詢有 overhead。

但**SOCKS5 / SSH tunnel** 接近 wire speed（只是中介傳）。

## 動手練習

**1. SSH 一行 SOCKS5**

```bash
# 在本機
ssh -D 1080 user@vps -N    # -N 不開 shell

# 另開 terminal
curl --socks5-hostname localhost:1080 ifconfig.me
# 應該顯示 VPS IP
```

**2. 看 HTTP proxy CONNECT**

```bash
sudo tcpdump -nn -i any 'port 8080' &
curl --proxy http://localhost:8080 https://example.com
```

如果你跑了 proxy（如 squid），看 CONNECT method packet。

**3. 配 Firefox SOCKS5**

Settings → Network → Manual proxy → SOCKS5 host = localhost, port 1080。打開 ipchicken.com 看 IP 變了。

**4. 寫個 simple HTTP proxy（python）**

```python
import socket, threading

def handle(conn):
    data = conn.recv(4096)
    if not data: return
    
    # 解析第一行
    first_line = data.split(b'\n')[0]
    method, url, _ = first_line.split()
    
    if method == b'CONNECT':
        host, port = url.split(b':')
        port = int(port)
    else:
        # parse Host header for HTTP
        for line in data.split(b'\n'):
            if line.lower().startswith(b'host:'):
                host = line.split(b':')[1].strip()
                port = 80
                break
    
    # 連 destination
    upstream = socket.socket()
    upstream.connect((host.decode(), port))
    
    if method == b'CONNECT':
        conn.send(b'HTTP/1.1 200 OK\r\n\r\n')
    else:
        upstream.send(data)
    
    # forward 兩邊
    def forward(src, dst):
        try:
            while True:
                data = src.recv(4096)
                if not data: break
                dst.send(data)
        finally:
            src.close(); dst.close()
    
    threading.Thread(target=forward, args=(conn, upstream)).start()
    threading.Thread(target=forward, args=(upstream, conn)).start()

s = socket.socket()
s.bind(('0.0.0.0', 8888))
s.listen(5)
print("proxy on 8888")
while True:
    conn, _ = s.accept()
    threading.Thread(target=handle, args=(conn,)).start()
```

```bash
python3 proxy.py
# 另一個 terminal
curl --proxy http://localhost:8888 http://example.com
```

簡化版 HTTP proxy，玩玩看。

**5. SSH SOCKS5 + browser**

開 SSH SOCKS5 + Firefox 設 proxy → 整個 Firefox traffic 走 VPS。同時 Chrome 不設 → 走本地。**雙瀏覽器雙 IP**。

## 自我檢核

- [ ] HTTP Proxy 跟 SOCKS5 差別清楚
- [ ] 知道 CONNECT method 怎麼處理 HTTPS
- [ ] 用 SSH `-D` 建過 SOCKS5
- [ ] 知道「proxy 不一定加密」
- [ ] 知道 reverse proxy 跟正向的區別
- [ ] 配置過 curl / Firefox 走 proxy

下一章看 Shadowsocks — 翻牆界的經典工具。

→ [Ch 29 Shadowsocks](./29-shadowsocks.md)
