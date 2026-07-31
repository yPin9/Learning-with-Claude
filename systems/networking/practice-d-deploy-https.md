# 練習 D — 部署一個 HTTPS 網站

> **目標**：整合 Part 8 的全部知識，在你的 VPS 上完整部署一個對外的 HTTPS 網站——從 DNS 設定、nginx reverse proxy、Let's Encrypt HTTPS、到安全加固和驗證。這是把 Ch 33-36 串成一個端到端的部署流程，做出一個真正能用網址訪問、有綠色鎖頭的網站。完成後你掌握了「從零部署一個生產服務」的完整能力，這是 DevOps 的核心交付物，也是 Final Project 的基礎。

## 背景與動機

你買了 VPS（Ch 33）、加固了安全（Ch 35）、學了 nginx 部署（Ch 36）。現在把它們串成一個完整的部署——一個真正對外的 HTTPS 網站。

這是真實工作的縮影：產品要上線，你要把它部署到伺服器、配域名、上 HTTPS、確保安全。完成這個練習，你能向任何人展示「我部署的網站」（一個真實的 URL，有 HTTPS 鎖頭）——這是把整門課從「知識」變成「能交付的成果」。它也是 Final Project（完整生產 VPS）的核心部分。這個練習強調**端到端**和**驗證**——不只是「設好」，而是「真的能從外面正常訪問且安全」。

## 任務規格

在你的 VPS 上部署一個 HTTPS 網站，達成：

| 目標 | 涉及 |
|---|---|
| VPS 已加固（SSH 金鑰/防火牆/fail2ban）| Ch 33/35 |
| 域名指向 VPS（A 記錄）| Ch 9 |
| 部署一個後端服務（網站/API）| Ch 36 |
| nginx reverse proxy 到後端 | Ch 28/36 |
| Let's Encrypt HTTPS（有效憑證）| Ch 11/36 |
| HTTP 自動跳轉 HTTPS | Ch 10/36 |
| 後端只聽 127.0.0.1（不對外）| Ch 13 |
| 服務開機自啟（systemd）| Ch 31 |

**驗收標準**：
- 從外部用 `https://你的域名` 能正常訪問
- 瀏覽器顯示有效的 HTTPS（綠鎖，Let's Encrypt 簽）
- HTTP 自動跳轉 HTTPS
- `nmap` 從外部掃描：只有 22(或改的)/80/443 開放，後端 port 不對外
- 重開機後服務自動恢復
- SSL Labs 測試（ssllabs.com）拿 A 或以上

## 期望成果

```
$ curl -I https://你的域名.com
HTTP/2 200
server: nginx
...

$ curl -I http://你的域名.com
HTTP/1.1 301 Moved Permanently        ← HTTP 自動跳 HTTPS
location: https://你的域名.com/

瀏覽器訪問 → 綠鎖 + 你的網站內容
SSL Labs 測試 → A 評級
```

## 如果你卡住了

1. 先確認 VPS 已加固（Ch 35）和 DNS 指向對了（dig 驗證，Ch 15）
2. 分階段：先 HTTP 通（nginx proxy 到後端），再加 HTTPS（certbot），別一次全做
3. 502 = nginx 連不上後端 → 確認後端在 127.0.0.1 聽（ss -tlnp，Ch 13/36）
4. certbot 失敗常是 DNS 沒生效（A 記錄要先指對）或防火牆沒開 80（Let's Encrypt 要連 80 驗證）
5. 後端要做成 systemd service（Ch 31）才會開機自啟，否則重開機就沒了
6. 用 nmap 從外部驗證攻擊面（Ch 17/35）
7. 每一步都驗證再下一步（DNS→HTTP→HTTPS→安全）

## 實作步驟建議

### Step 1：確認 VPS 加固 + DNS 指向
### Step 2：部署後端服務 + 做成 systemd service（開機自啟）
### Step 3：nginx reverse proxy（先 HTTP 通）
### Step 4：Let's Encrypt HTTPS + HTTP 跳轉
### Step 5：安全驗證（nmap 外部掃描 + SSL Labs）

## 完整參考解答

**自己部署一次再看！** 端到端親手做才學得到。

<details>
<summary>完整部署流程</summary>

```bash
# ========== Step 1：前置確認 ==========
# 確認 VPS 已加固（Ch 35）：金鑰登入、防火牆、fail2ban
sudo ufw status                    # 防火牆開了 SSH/80/443
# 確認 DNS 指向（Ch 9/15）
dig example.com +short             # 你的 VPS IP

# ========== Step 2：部署後端 + systemd service ==========
# 範例後端：一個簡單的 Node/Python/靜態網站
# 這裡用 Python 簡單 app 示範（實際換成你的應用）

# 建一個簡單的後端（聽 127.0.0.1:3000，只本機，Ch 13）
mkdir -p /opt/myapp
cat > /opt/myapp/app.py <<'EOF'
from http.server import HTTPServer, BaseHTTPRequestHandler
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(b'<h1>Hello from my VPS!</h1>')
HTTPServer(('127.0.0.1', 3000), H).serve_forever()   # 只聽 127.0.0.1！
EOF

# 做成 systemd service（開機自啟，Ch 31）
sudo tee /etc/systemd/system/myapp.service > /dev/null <<'EOF'
[Unit]
Description=My App
After=network.target
[Service]
ExecStart=/usr/bin/python3 /opt/myapp/app.py
Restart=on-failure
User=deploy
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now myapp
# 確認後端在聽（Ch 13）
ss -tlnp | grep 3000               # LISTEN 127.0.0.1:3000

# ========== Step 3：nginx reverse proxy（先 HTTP）==========
sudo apt install -y nginx
sudo tee /etc/nginx/sites-available/example.com > /dev/null <<'EOF'
server {
    listen 80;
    server_name example.com www.example.com;
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
sudo ln -sf /etc/nginx/sites-available/example.com /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default     # 移除預設站
sudo nginx -t && sudo systemctl reload nginx
# 防火牆開 80/443（Ch 35）
sudo ufw allow 'Nginx Full'
# 測試 HTTP 通
curl -I http://example.com         # 應該 200（透過 nginx → 後端）

# ========== Step 4：Let's Encrypt HTTPS ==========
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d example.com -d www.example.com --redirect
#   --redirect：自動設 HTTP → HTTPS 跳轉
# certbot 會驗證域名、取憑證、改 nginx 加 HTTPS、設跳轉

# 測試 HTTPS
curl -I https://example.com        # HTTP/2 200
curl -I http://example.com         # 301 跳轉 HTTPS

# ========== Step 5：安全驗證 ==========
# 從外部掃描攻擊面（Ch 17/35）
nmap -p 22,80,443,3000 example.com
# 22/80/443 open, 3000 filtered（後端不對外！）

# 確認後端沒對外（Ch 13）
ss -tlnp | grep 3000               # 127.0.0.1:3000（只本機）✓

# 憑證自動更新（Ch 11/36）
sudo certbot renew --dry-run

# SSL Labs 測試（瀏覽器訪問）：
# https://www.ssllabs.com/ssltest/analyze.html?d=example.com
# → 應該拿 A（Let's Encrypt + nginx 預設配置夠好）
```

**解答說明**：

- **後端聽 127.0.0.1**（Ch 13）：後端只本機可連，外部只能透過 nginx——這是安全的核心（後端不暴露）
- **systemd service**（Ch 31）：`enable --now` 讓後端開機自啟 + 立即啟動，`Restart=on-failure` 掛了自動重啟、`User=deploy` 不用 root 跑（Ch 28 最小權限）
- **nginx reverse proxy**（Ch 28/36）：proxy_pass 轉給後端 + 傳真實客戶端 header；移除預設站避免衝突
- **分階段**：先 HTTP 通（確認 proxy 對）再加 HTTPS（certbot）——別一次全做，每步驗證
- **certbot --redirect**（Ch 11/36）：一鍵 HTTPS + 自動設 HTTP 跳轉，憑證自動更新
- **nmap 驗證**（Ch 17/35）：從外部確認只有該開的 port 開、後端 3000 不對外（filtered）
- **SSL Labs**：第三方驗證 HTTPS 配置品質（A 級代表 TLS 配置安全，Ch 11）

</details>

## 測試用案例

| 操作 | 預期 | 驗證 |
|---|---|---|
| `curl -I https://域名` | HTTP/2 200 | HTTPS 通 |
| `curl -I http://域名` | 301 跳 HTTPS | 自動跳轉 |
| `nmap -p 3000 域名` | filtered | 後端不對外 |
| `ss -tlnp \| grep 3000` | 127.0.0.1:3000 | 後端只本機 |
| 重開機後訪問 | 服務恢復 | systemd 自啟 |
| SSL Labs | A 評級 | TLS 配置 |
| 瀏覽器訪問 | 綠鎖 + 內容 | 完整成果 |

## 延伸挑戰（加分）

- **挑戰一**：多服務——在同一 VPS 用不同域名/子域名部署多個服務（多個 nginx server block），理解虛擬主機

- **挑戰二**：安全標頭——加 HSTS、CSP、X-Frame-Options 等安全標頭，SSL Labs 衝 A+

- **挑戰三**：用 Docker 部署後端——後端跑在容器裡（接 docker 課），nginx proxy 到容器，理解容器網路（Ch 37 預習）

- **挑戰四**：監控——加一個 uptime 監控（如自架 Uptime Kuma 或用免費服務），服務掛了通知你

- **挑戰五**：CI/CD——用 GitHub Actions，push 程式碼自動部署到 VPS（rsync/ssh，Ch 34），體驗自動化部署

- **挑戰六**：rate limiting——在 nginx 設請求限流，防止單一 IP 打爆你的服務（DDoS 基礎防護）

## 自我檢核

- [ ] 能從零端到端部署一個 HTTPS 網站（DNS→後端→nginx→HTTPS）
- [ ] 理解 nginx reverse proxy + 後端只聽本機的架構和安全意義
- [ ] 會用 Let's Encrypt 上 HTTPS，知道自動更新
- [ ] 會用 nmap 從外部驗證攻擊面（後端不對外）
- [ ] 能做出開機自啟、有有效 HTTPS、安全的生產服務

這個練習做出了一個真正對外的 HTTPS 服務——你成為了「網路的提供者」。接下來 Part 9 進階速覽，補完容器網路、IPv6、QUIC/BGP 等現代主題，然後是整合全課的 Final Project。

→ [Ch 37 容器網路](./37-container-networking.md)
