# Ch 21 — Docker Compose 完整部署：Postgres + n8n + Nginx

> 目標：把 n8n 部署在 VPS 上，用 Postgres 存資料，Nginx 處理 SSL，對外公開服務。

## 為什麼要 Self-host

Ch 2 的本地 Docker 只適合開發。生產部署需要：

- 公開可存取的域名（Webhook 要對外）
- HTTPS（安全、瀏覽器不擋）
- Postgres（比 SQLite 穩定，支援並發）
- 自動重啟（VPS 重開後 n8n 自動起）
- 定期備份

---

## 環境需求

一台 VPS（推薦 2 vCPU / 2GB RAM 以上），安裝好：

```bash
docker --version         # 24+
docker compose version   # v2+
```

有一個域名，DNS A Record 指向你的 VPS IP：

```
n8n.yourdomain.com → xxx.xxx.xxx.xxx
```

---

## 目錄結構

```
~/n8n-production/
├── docker-compose.yml
├── .env                  ← 敏感設定（不要 commit）
├── nginx/
│   ├── nginx.conf
│   └── ssl/
│       ├── cert.pem
│       └── key.pem
└── backups/              ← 備份輸出目錄
```

---

## .env 檔案

```bash
# 資料庫
POSTGRES_USER=n8n_user
POSTGRES_PASSWORD=強密碼請換掉
POSTGRES_DB=n8n

# n8n
N8N_HOST=n8n.yourdomain.com
N8N_PORT=5678
N8N_PROTOCOL=https
WEBHOOK_URL=https://n8n.yourdomain.com/
N8N_ENCRYPTION_KEY=32位元隨機字串   # openssl rand -hex 16
GENERIC_TIMEZONE=Asia/Taipei
N8N_BASIC_AUTH_ACTIVE=true
N8N_BASIC_AUTH_USER=admin
N8N_BASIC_AUTH_PASSWORD=你的管理員密碼
```

---

## docker-compose.yml

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    container_name: n8n-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER:     ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB:       ${POSTGRES_DB}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 5

  n8n:
    image: docker.n8n.io/n8nio/n8n:latest
    container_name: n8n
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      N8N_HOST:             ${N8N_HOST}
      N8N_PORT:             ${N8N_PORT}
      N8N_PROTOCOL:         ${N8N_PROTOCOL}
      WEBHOOK_URL:          ${WEBHOOK_URL}
      N8N_ENCRYPTION_KEY:   ${N8N_ENCRYPTION_KEY}
      GENERIC_TIMEZONE:     ${GENERIC_TIMEZONE}
      DB_TYPE:              postgresdb
      DB_POSTGRESDB_HOST:   postgres
      DB_POSTGRESDB_PORT:   5432
      DB_POSTGRESDB_DATABASE: ${POSTGRES_DB}
      DB_POSTGRESDB_USER:   ${POSTGRES_USER}
      DB_POSTGRESDB_PASSWORD: ${POSTGRES_PASSWORD}
      N8N_BASIC_AUTH_ACTIVE:   ${N8N_BASIC_AUTH_ACTIVE}
      N8N_BASIC_AUTH_USER:     ${N8N_BASIC_AUTH_USER}
      N8N_BASIC_AUTH_PASSWORD: ${N8N_BASIC_AUTH_PASSWORD}
    volumes:
      - n8n_data:/home/node/.n8n
    expose:
      - "5678"

  nginx:
    image: nginx:alpine
    container_name: n8n-nginx
    restart: unless-stopped
    depends_on:
      - n8n
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro

volumes:
  postgres_data:
  n8n_data:
```

---

## nginx/nginx.conf

```nginx
server {
    listen 80;
    server_name n8n.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name n8n.yourdomain.com;

    ssl_certificate     /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    location / {
        proxy_pass         http://n8n:5678;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection 'upgrade';
        proxy_set_header   Host $host;
        proxy_cache_bypass $http_upgrade;

        # WebSocket 支援（n8n 用 WebSocket 更新執行狀態）
        proxy_read_timeout 86400;
    }
}
```

---

## SSL 憑證

### 方法 1：Let's Encrypt（免費，自動更新）

在 VPS 上安裝 certbot：

```bash
apt install certbot
certbot certonly --standalone -d n8n.yourdomain.com
# 憑證放在 /etc/letsencrypt/live/n8n.yourdomain.com/
```

複製到 nginx/ssl：

```bash
cp /etc/letsencrypt/live/n8n.yourdomain.com/fullchain.pem nginx/ssl/cert.pem
cp /etc/letsencrypt/live/n8n.yourdomain.com/privkey.pem   nginx/ssl/key.pem
```

設定 cron 自動更新：

```bash
0 3 * * * certbot renew --quiet && \
  cp /etc/letsencrypt/live/n8n.yourdomain.com/fullchain.pem /root/n8n-production/nginx/ssl/cert.pem && \
  cp /etc/letsencrypt/live/n8n.yourdomain.com/privkey.pem   /root/n8n-production/nginx/ssl/key.pem && \
  docker compose -f /root/n8n-production/docker-compose.yml restart nginx
```

### 方法 2：Cloudflare Origin Certificate

如果 DNS 走 Cloudflare，可以用它的 Origin Certificate，在 Cloudflare dashboard 建立後下載。

---

## 部署指令

```bash
cd ~/n8n-production

# 第一次啟動
docker compose up -d

# 查看狀態
docker compose ps

# 查看 n8n log
docker compose logs -f n8n

# 停止
docker compose down

# 更新 n8n 版本
docker compose pull n8n
docker compose up -d n8n
```

---

## 防火牆設定

```bash
# Ubuntu/Debian 用 ufw
ufw allow 22    # SSH
ufw allow 80    # HTTP (重定向到 HTTPS)
ufw allow 443   # HTTPS
ufw enable
```

不要直接開 5678，讓 Nginx 擋在前面。

---

## 自我檢核

- [ ] 能寫出完整的 docker-compose.yml（三個 service：postgres、n8n、nginx）
- [ ] 知道 `N8N_ENCRYPTION_KEY` 的用途（credential 加密）
- [ ] 能設定 Nginx SSL 反向代理
- [ ] 知道 `depends_on: condition: service_healthy` 防止 n8n 在 postgres 還沒好時啟動

→ [Ch 22 環境變數、Secrets、使用者權限管理](./22-secrets-permissions.md)
