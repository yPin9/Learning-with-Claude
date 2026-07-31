# Ch 36 — 用 nginx 部署服務

> **目標**：用 nginx 在 VPS 上部署一個真正的服務——nginx 當 reverse proxy（Ch 28）的角色、設定 server block、用 Let's Encrypt（Ch 11）自動拿 HTTPS 憑證、反向代理到後端應用、處理常見問題（502/504）。這把全課串起來：DNS（Ch 9）指向你的 VPS、nginx 提供 HTTPS（Ch 11）、reverse proxy（Ch 28）轉給後端。完成後你的 VPS 對外提供一個 HTTPS 服務——你成為「網路的另一端」。

> **環境**：VPS（已加固，Ch 35），nginx，一個域名（指向你的 VPS）。

## 為什麼用 nginx 部署？

你架了 VPS、加固了安全，現在要讓它**對外提供服務**——一個網站、API、或其他應用。但直接把應用暴露到公網有問題：應用通常不擅長處理 TLS、不擅長同時服務很多連線、沒有快取。**nginx**（reverse proxy，Ch 28）擋在前面解決這些——它處理 HTTPS、負載平衡、快取，把請求轉給後端應用。

這章把全課串起來——你會看到 DNS（Ch 9）怎麼指向你的 VPS、nginx 怎麼用 Let's Encrypt 提供 HTTPS（Ch 11）、reverse proxy（Ch 28）怎麼轉給後端。完成後你的 VPS 對外提供一個真正的 HTTPS 服務——你從「網路的使用者」變成「網路的提供者」，這是 Part 8 的高潮，也是 Final Project 的核心。

## 先建立直覺:nginx 是服務的門面

```
nginx 當 reverse proxy（Ch 28）的角色：

  外面的客戶端
       │ HTTPS（443）
       ▼
  ┌──────────────────┐
  │  nginx（門面）    │ ← 在 VPS 上，對外
  │  - 處理 HTTPS/TLS │   （Ch 11，TLS 終止）
  │  - reverse proxy  │   （Ch 28，轉給後端）
  │  - 靜態檔/快取     │
  └────────┬─────────┘
           │ HTTP（本機，明文也沒關係，內部）
           ▼
  ┌──────────────────┐
  │  後端應用          │ ← 跑在 127.0.0.1:3000（只本機，Ch 13）
  │  (Node/Python/Go) │   不直接對外（nginx 擋在前面）
  └──────────────────┘
        │
  → nginx 是「門面」：對外處理 HTTPS、轉給後端
    後端只聽 127.0.0.1（安全，Ch 13），不直接暴露
    這是現代 web 部署的標準架構
```

關鍵心智：nginx 是服務的「門面」（reverse proxy，Ch 28）——它對外處理 HTTPS（TLS 終止，Ch 11）、轉發請求給後端應用。後端應用只聽 127.0.0.1（不直接對外，Ch 13 的安全），nginx 擋在前面。這是現代 web 部署的標準架構——nginx 處理它擅長的（TLS、連線、靜態檔），後端專注應用邏輯。

> nginx 用 Ch 28 的 reverse proxy 概念、Ch 11 的 TLS、Ch 9 的 DNS（域名指向 VPS）。它讓後端應用只聽 127.0.0.1（Ch 13 的安全）。這章把這些串成實際部署。

## DNS:讓域名指向你的 VPS

```bash
# 部署前：讓你的域名指向 VPS（Ch 9）
# 在你的域名 DNS 設定（域名商或 Cloudflare）加 A 記錄：
#   example.com      A    192.0.2.123（你的 VPS IP）
#   www.example.com  A    192.0.2.123

# 驗證 DNS 生效（Ch 15）
dig example.com +short
# 192.0.2.123   ← 指向你的 VPS 了

# 記得：DNS 有 TTL（Ch 9），剛設可能要等一下才全球生效
# 沒有域名也能測（用 IP，但 HTTPS 憑證需要域名）
```

> **部署的第一步是 DNS——讓域名的 A 記錄指向你的 VPS IP（Ch 9），這也是 HTTPS 憑證的前提**。要對外提供服務，使用者要能用域名找到你的 VPS——在域名的 DNS 設定加 **A 記錄**（Ch 9）指向 VPS 的 IP（`example.com A 192.0.2.123`）。用 `dig example.com +short`（Ch 15）驗證指向對了。注意 DNS 的 TTL（Ch 9）——剛設可能要等才全球生效。**域名是 HTTPS 的前提**——Let's Encrypt（下節）要驗證「你擁有這個域名」才簽憑證，所以沒有域名就拿不到受信任的 HTTPS 憑證（只能用自簽，會有警告，Ch 11）。這把 Ch 9（DNS）的知識落到實際——你設 A 記錄、用 dig 驗證，就是把「域名 → IP」這個 Ch 1 旅程的第一步，從「別人的網站」變成「你的 VPS」。如果還沒有域名，便宜的域名一年幾美元（或用免費的二級域名服務），這對體驗完整的 HTTPS 部署值得。

## nginx:reverse proxy 設定

```bash
# === 安裝 nginx ===
sudo apt install nginx
sudo systemctl enable --now nginx
# 防火牆開 80/443（Ch 35）
sudo ufw allow 'Nginx Full'        # 開 80 和 443

# === 設定一個 server block（reverse proxy 到後端）===
sudo tee /etc/nginx/sites-available/example.com > /dev/null <<'EOF'
server {
    listen 80;
    server_name example.com www.example.com;

    location / {
        proxy_pass http://127.0.0.1:3000;       # 轉給後端（Ch 28）
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr; # 傳真實客戶端 IP 給後端
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

# 啟用這個 site
sudo ln -s /etc/nginx/sites-available/example.com /etc/nginx/sites-enabled/
sudo nginx -t                      # 測試設定語法（重要！）
sudo systemctl reload nginx        # 套用

# === 測試 reverse proxy（後端要先跑著）===
# 假設後端跑在 127.0.0.1:3000
curl -I http://example.com         # 應該轉給後端並回應
```

> **nginx 的 reverse proxy 設定核心是 `proxy_pass` + 傳遞真實客戶端資訊的 header——後者常被忘記**。`proxy_pass http://127.0.0.1:3000` 是核心——把請求轉給後端（Ch 28，後端只聽本機）。但有個容易忘的關鍵：**傳遞真實客戶端資訊給後端**——`proxy_set_header X-Real-IP $remote_addr`（真實客戶端 IP，否則後端只看到 nginx 的 127.0.0.1）、`X-Forwarded-For`（經過的代理鏈）、`X-Forwarded-Proto`（原始是 HTTP 還 HTTPS）。漏了這些，後端會以為所有請求都來自 127.0.0.1（log 記錯 IP、限流/地理判斷失效、HTTPS 重導向迴圈）。`server_name` 決定這個 server block 處理哪個域名（一台 nginx 能用多個 server block 服務多個域名，靠 server_name 區分——這是「虛擬主機」）。**`nginx -t`（測試設定語法）是改 nginx 設定的必備步驟**——它在 reload 前檢查語法錯誤（漏分號、路徑錯），避免「reload 後 nginx 掛掉整個服務中斷」。`reload`（不是 restart）平滑套用設定（不中斷現有連線）。這個 reverse proxy 設定是現代 web 部署的標準——nginx 在前、後端在後，是 Ch 28「reverse proxy 在伺服器側」的實際應用。

## Let's Encrypt:自動 HTTPS

```bash
# === certbot：自動取得 + 設定 Let's Encrypt 憑證（Ch 11）===
sudo apt install certbot python3-certbot-nginx

# 一鍵取得憑證並設定 nginx（HTTPS）
sudo certbot --nginx -d example.com -d www.example.com
# certbot 會：
#   1. 驗證你擁有這個域名（Ch 11 的 CA 驗證）
#   2. 取得 Let's Encrypt 簽的憑證（免費，Ch 11）
#   3. 自動修改 nginx 設定加上 HTTPS（443 + 憑證）
#   4. 設定 HTTP 自動跳轉 HTTPS

# 驗證 HTTPS 生效
curl -I https://example.com        # HTTP/2 200，有有效憑證
# 用 openssl 看憑證（Ch 11）
echo | openssl s_client -connect example.com:443 2>/dev/null | openssl x509 -noout -issuer
# issuer = Let's Encrypt   ← 受信任的 CA 簽的（Ch 11）

# === 自動更新（Let's Encrypt 憑證 90 天到期）===
sudo systemctl status certbot.timer    # certbot 裝了自動更新的 timer（Ch 30 的 systemd timer）
sudo certbot renew --dry-run           # 測試自動更新
# → 憑證會自動更新，不用手動（解決 Ch 11 的「憑證過期」問題）
```

> **Let's Encrypt + certbot 讓 HTTPS 變成「一鍵 + 自動更新」——這是 Ch 11「憑證/CA」知識的實際落地，也讓 HTTPS 普及**。Ch 11 講了 HTTPS 需要受信任 CA 簽的憑證。**Let's Encrypt** 是免費的 CA，**certbot** 自動化整個流程——`certbot --nginx -d example.com` 一個命令就：驗證你擁有域名（Ch 11 的 CA 驗證，透過放一個檔案或 DNS 記錄證明）、取得憑證、自動改 nginx 設定加上 HTTPS、設定 HTTP 跳轉 HTTPS。幾分鐘你的服務就有了**受信任的 HTTPS**（瀏覽器顯示鎖頭，不是 Ch 11 的自簽警告）。關鍵是**自動更新**——Let's Encrypt 憑證只有 90 天有效（短期是安全設計），certbot 裝了 systemd timer（Ch 30）**自動更新**，解決了 Ch 11 提的「憑證過期」問題（網站忘了更新憑證的常見災難——certbot 自動處理）。這個「免費 + 自動」是 HTTPS 從「麻煩昂貴」變成「人人都該有」的關鍵（Let's Encrypt 讓全球 HTTPS 普及率大幅提升）。用 `openssl s_client`（Ch 11）驗證憑證是 Let's Encrypt 簽的、有效。完成後你的服務有了和大網站一樣的 HTTPS——這是把 Ch 11 的 TLS 知識變成「真的能用的 HTTPS 服務」。

## 故意弄壞:502/504 的 debug

```bash
# 部署最常見的問題：502 Bad Gateway / 504 Gateway Timeout（Ch 10）

# 502 Bad Gateway = nginx 連不上後端（Ch 10）
curl -I https://example.com
# HTTP/2 502   ← nginx 收到請求但連不上後端
# 排查：
sudo ss -tlnp | grep 3000          # 後端真的在 127.0.0.1:3000 聽嗎？（Ch 13）
#   沒在聽 → 後端沒啟動 / port 錯 / 綁錯位址
curl -I http://127.0.0.1:3000      # nginx 能連到的話，這個應該通
sudo tail /var/log/nginx/error.log # nginx 的錯誤 log（連後端失敗的細節）
# connect() failed (111: Connection refused) → 後端沒在那個 port

# 504 Gateway Timeout = 後端太慢（Ch 10）
# HTTP/2 504   ← 後端有在聽但回應太慢（超過 nginx 的 timeout）
# 排查：後端為什麼慢？（負載/卡住/資料庫慢）
# 調 nginx timeout（治標）：proxy_read_timeout 60s;

# 其他常見：
sudo nginx -t                      # 設定語法錯（reload 前一定要測）
sudo systemctl status nginx        # nginx 跑著嗎？
sudo journalctl -u nginx           # nginx 服務 log
# 防火牆有開 443 嗎？（Ch 35）—— 連不上可能是防火牆
```

> **502（nginx 連不上後端）和 504（後端太慢）是部署最常見的問題——它們對應 Ch 10 的狀態碼，根因在 reverse proxy 和後端的關係**。部署服務後最常遇到這兩個錯（Ch 10 提過）：**502 Bad Gateway** = nginx 收到請求但**連不上後端**——排查：`ss -tlnp | grep 3000`（後端真的在那個 port 聽嗎？Ch 13——沒在聽就是後端沒啟動/port 錯/綁錯位址）、`curl http://127.0.0.1:3000`（nginx 能連的話這應該通）、看 `/var/log/nginx/error.log`（"Connection refused" = 後端不在那個 port）。**504 Gateway Timeout** = 後端**有在聽但回應太慢**（超過 nginx timeout）——根因在後端（負載高/卡住/資料庫慢），治本要優化後端，治標可調 `proxy_read_timeout`。這些 debug 用到全課的工具：`ss`（Ch 13 看後端在不在聽）、`curl`（Ch 17 測各層）、nginx 的 error.log、`journalctl`（Ch 31 看服務 log）、防火牆檢查（Ch 35）。debug 邏輯是 Ch 2 的分層——502/504 是「nginx 到後端」這段的問題（nginx 本身正常，後端或它們之間有問題），對比「連不上 nginx」（防火牆/nginx 沒跑）是「客戶端到 nginx」的問題。**`nginx -t`（測設定）在 reload 前必做**——設定語法錯會讓 nginx 掛掉。這些是部署服務的日常 debug——掌握它們，你能讓服務穩定運行。

## 動手練習

1. 設 DNS：把域名 A 記錄指向你的 VPS，用 dig 驗證

2. 跑後端 + nginx proxy：在 VPS 跑一個簡單後端（如 python http.server，綁 127.0.0.1:3000），設 nginx reverse proxy 到它

3. HTTPS：用 certbot 一鍵取得 Let's Encrypt 憑證，驗證 HTTPS 生效、憑證是 Let's Encrypt 簽的

4. 自動更新：`certbot renew --dry-run` 測試憑證自動更新

5. 跑「故意弄壞」：故意停掉後端看 502、設定語法錯看 nginx -t 抓出，練 debug

## 本章重點整理

- nginx 當 reverse proxy（Ch 28）：對外處理 HTTPS（TLS 終止，Ch 11）、轉發給只聽 127.0.0.1 的後端（Ch 13 安全）
- 部署第一步是 DNS（Ch 9）：A 記錄指向 VPS IP，也是 HTTPS 憑證的前提
- reverse proxy 設定：proxy_pass + 傳真實客戶端 header（X-Real-IP/X-Forwarded-*，常忘）；nginx -t 測語法
- Let's Encrypt + certbot：一鍵取得 + 自動更新 HTTPS（90 天，自動 renew）——Ch 11 的落地，解決憑證過期
- 502（nginx 連不上後端）/504（後端慢）是部署最常見問題，用 ss/curl/error.log 分層排查（Ch 2）

## 自我檢核

- [ ] 理解 nginx 當 reverse proxy 的架構（門面 + 後端只聽本機）
- [ ] 會設 DNS 讓域名指向 VPS，知道它是 HTTPS 的前提
- [ ] 會設 nginx reverse proxy，知道為什麼要傳真實客戶端 header
- [ ] 會用 certbot 取得 Let's Encrypt HTTPS，理解自動更新
- [ ] 會 debug 502/504（分層排查 nginx 和後端）

## 延伸閱讀

### 官方教學

- **[nginx reverse proxy + Let's Encrypt](https://www.digitalocean.com/community/tutorials/how-to-secure-nginx-with-let-s-encrypt-on-ubuntu-22-04)** — DigitalOcean
  - **讀哪裡**：reverse proxy 設定 + certbot 那幾節
  - **為什麼值得讀**：本章部署的標準教學，最權威

### 文件

- **[nginx 官方文件](https://nginx.org/en/docs/)** + **[Let's Encrypt](https://letsencrypt.org/docs/)** — 官方
  - **讀哪裡**：nginx 的 proxy 模組、Let's Encrypt 的運作
  - **為什麼值得讀**：nginx 設定和 Let's Encrypt 的權威

### 文章

- **[nginx 設定最佳實踐](https://www.digitalocean.com/community/tutorials/understanding-nginx-server-and-location-block-selection-algorithms)** — DigitalOcean
  - **這篇說什麼**：nginx 的 server/location block 選擇邏輯
  - **為什麼值得讀**：深入理解 nginx 怎麼匹配請求（多 server block 時）

Part 8 的章節到此完成。接下來是練習 D——完整部署一個 HTTPS 網站，把 DNS/nginx/Let's Encrypt/安全綜合應用，做出一個真正對外的服務。

→ [練習 D：部署一個 HTTPS 網站](./practice-d-deploy-https.md)
