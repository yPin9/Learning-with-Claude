# Ch 36 — 部署服務 (nginx + HTTPS)

> 目標：在 VPS 上完整部署 nginx 跑 HTTPS，含 Let's Encrypt 自動 cert、reverse proxy、log 設定。

## nginx 是什麼

最廣用的 web server / reverse proxy / load balancer。

3 大角色：

1. **靜態 web server**：serve HTML / image
2. **Reverse proxy**：把 request forward 給 backend (Node / Python / Go)
3. **Load balancer**：分流到多 backend

production 95% 跑 nginx 在最前面。

## 安裝 nginx

```bash
sudo apt install nginx
sudo systemctl enable nginx
sudo systemctl start nginx
```

打開 http://VPS-IP/ 應該看到「Welcome to nginx!」。

確認：

```bash
sudo systemctl status nginx
sudo ss -tnlp | grep nginx
# LISTEN ... :80 users:(("nginx",pid=...,fd=6))
```

## nginx config 結構

```
/etc/nginx/
├── nginx.conf              # 主 config
├── sites-available/        # 各 site config（只是檔案集合）
│   ├── default
│   └── mysite
├── sites-enabled/          # 啟用的 site（symlink 到 sites-available）
│   └── default
├── conf.d/                 # global conf snippets
└── snippets/               # 可重用 snippet
```

「**主 config include sites-enabled/**」。修改後：

```bash
sudo nginx -t              # 檢查 syntax
sudo systemctl reload nginx
```

`reload` 不重啟 process，graceful 套用 config。

## 第一個簡單 site

`/etc/nginx/sites-available/mysite`：

```nginx
server {
    listen 80;
    server_name mysite.example.com;
    
    root /var/www/mysite;
    index index.html;
    
    location / {
        try_files $uri $uri/ =404;
    }
}
```

啟用：

```bash
sudo ln -s /etc/nginx/sites-available/mysite /etc/nginx/sites-enabled/
sudo mkdir -p /var/www/mysite
echo "<h1>Hello!</h1>" | sudo tee /var/www/mysite/index.html
sudo nginx -t
sudo systemctl reload nginx
```

打開 http://mysite.example.com/ 應該看到「Hello!」（DNS 要先指向 VPS）。

## DNS 設定

買 domain（Namecheap / Cloudflare 等）後，DNS panel 加 A record：

```
Type: A
Name: mysite (or @)
Value: <VPS-IP>
TTL: 3600
```

10-30 分鐘 propagate（看 TTL）。確認：

```bash
dig mysite.example.com
# 應該看到 VPS-IP
```

## Let's Encrypt cert（HTTPS）

免費、自動化、3 個月有效（自動 renew）。

### 安裝 certbot

```bash
sudo apt install certbot python3-certbot-nginx
```

### 申請 cert

```bash
sudo certbot --nginx -d mysite.example.com
```

互動式：

- email 通知
- 同意 ToS
- 是否自動 redirect HTTP → HTTPS（建議 yes）

certbot **自動改你的 nginx config**：

```nginx
server {
    server_name mysite.example.com;
    
    root /var/www/mysite;
    index index.html;
    
    location / {
        try_files $uri $uri/ =404;
    }

    listen 443 ssl;    # 加上
    ssl_certificate /etc/letsencrypt/live/mysite.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mysite.example.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}

server {
    if ($host = mysite.example.com) {
        return 301 https://$host$request_uri;
    }
    listen 80;
    server_name mysite.example.com;
    return 404;
}
```

reload：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

打開 https://mysite.example.com 應該看到 HTTPS！

### Auto renew

certbot 自動加 cron / systemd timer：

```bash
sudo systemctl status certbot.timer
sudo certbot renew --dry-run    # 測 renew
```

3 個月前自動 renew，**完全不需要管**。

## Reverse proxy（最常用）

把 nginx 接到後端 app（Node / Django / Go）：

```nginx
upstream backend {
    server 127.0.0.1:3000;     # 後端跑 Node 在 3000
}

server {
    listen 443 ssl;
    server_name api.example.com;
    
    # SSL config (let certbot 加)
    
    location / {
        proxy_pass http://backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        
        # Timeout
        proxy_connect_timeout 60s;
        proxy_read_timeout 60s;
    }
}
```

backend 不需要管 HTTPS / domain，nginx 在前處理一切。

## Load balancer

多 backend 分流：

```nginx
upstream backend {
    server 10.0.0.1:3000 weight=3;
    server 10.0.0.2:3000 weight=1;
    server 10.0.0.3:3000 backup;     # 主壞才上
}

server {
    location / {
        proxy_pass http://backend;
    }
}
```

預設 round-robin。其他 algo：

- `least_conn` — 最少連線
- `ip_hash` — 同 IP 永遠到同 backend（session affinity）

## Static file + cache

```nginx
location /static/ {
    root /var/www/mysite;
    expires 30d;            # browser cache 30 天
    add_header Cache-Control "public";
}

# Gzip
gzip on;
gzip_types text/plain text/css application/json application/javascript;
```

## Log

```nginx
access_log /var/log/nginx/access.log;
error_log /var/log/nginx/error.log;
```

預設位置。看：

```bash
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

format 可自訂。production 加 JSON 格式方便解析。

## 一個常見踩雷：「nginx 改了沒生效」

```bash
sudo systemctl reload nginx     # 確認跑完
sudo nginx -t                    # 確認 config OK
sudo systemctl status nginx      # 確認跑著
```

或是 cache：

```bash
# Browser hard refresh: Ctrl-Shift-R
# Curl
curl -I https://example.com
```

## 一個常見踩雷：「502 Bad Gateway」

backend 不通：

```bash
# 測 backend 自己
curl http://localhost:3000

# 看 nginx error log
sudo tail -50 /var/log/nginx/error.log
```

可能：

- backend 沒跑
- backend 在 listen 不同 port
- proxy_pass URL 寫錯

## 一個常見踩雷：「Let's Encrypt rate limit」

Let's Encrypt 對同 domain 一週限 5 次 issue。亂試 cert 容易 hit limit → 一週不能申請。

對策：

- 用 `--dry-run` 測
- 用 staging environment

```bash
sudo certbot certonly --staging -d test.example.com
```

## 一個常見踩雷：「DNS 還沒 propagate」

申請 cert 前 DNS 要解析到 VPS。沒解析 → certbot 驗證失敗。

先：

```bash
dig mysite.example.com
# 確認指到 VPS-IP
```

通了再 certbot。

## 動手練習

**1. 部署完整 site**

完整跑：

1. 買 domain（如果沒）
2. DNS A record 指 VPS
3. nginx config + index.html
4. certbot
5. https 通

**2. 部署 reverse proxy**

跑個簡單 Node app（或 Python flask）在 :3000，nginx 接到 :443。

**3. 看 nginx log**

```bash
sudo tail -f /var/log/nginx/access.log
# 跑 curl 看 log 即時更新
```

**4. nginx benchmark**

```bash
sudo apt install apache2-utils
ab -n 1000 -c 10 https://mysite.example.com/
```

看 throughput。

**5. test SSL**

```bash
# 在線工具
# https://www.ssllabs.com/ssltest/

# 或本機
nmap --script ssl-enum-ciphers -p 443 mysite.example.com
```

希望 A/A+ 評分。

## 自我檢核

- [ ] nginx 跑著、能 reload config
- [ ] 部署過 1 個靜態 site
- [ ] Let's Encrypt cert + auto renew 跑過
- [ ] 寫過 reverse proxy 配置
- [ ] 看過 nginx access.log / error.log
- [ ] benchmark 過自己的 nginx

Part 8 結束。練習 D 整合所有 — 買 VPS 部署 nginx + HTTPS。

→ [練習 D：買 VPS + 部署 nginx + HTTPS](./practice-d-deploy-https.md)
