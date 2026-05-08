# 練習 B：FastAPI + PostgreSQL + Redis + Nginx

整合章節：Ch 3（Dockerfile basics）、Ch 10（multi-stage build）、Ch 14（Compose basics）、Ch 15（env/secrets）、Ch 16（healthcheck/depends_on）、Ch 17（override/profiles）

---

## 背景

一個服務少有只跑一個 container 的。真實系統最少是：應用程式 + 資料庫 + 快取 + 反向代理（反向代理伺服器）。這個練習把這四件事用 Docker Compose（容器編排）串起來，從空目錄出發，完整跑通。

目標清單：

- `app`：FastAPI（快速 API 框架），multi-stage build，non-root user
- `db`：PostgreSQL（關聯式資料庫），health check，`app` 等它健康才啟動
- `cache`：Redis（記憶體快取），health check
- `proxy`：Nginx（反向代理），把 `localhost:80` 導向 `app:8000`
- 密碼用 `.env` 管，不 hardcode
- 每個 service 的 log rotation（日誌輪轉）設為 `max-size: 50m`

---

## 題目規格

### 目錄結構

你需要建出這個結構（**從空目錄手刻**）：

```
myapp/
├── app/
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── nginx/
│   └── nginx.conf
├── compose.yml
├── .env
└── .env.example
```

### app 規格

`GET /health` 回：

```json
{"status": "ok"}
```

`GET /items` 從 PostgreSQL 讀資料，回：

```json
[
  {"id": 1, "name": "item-a"},
  {"id": 2, "name": "item-b"}
]
```

資料庫初始化：`app` 啟動時自動建 `items` table 並插入兩筆測試資料（如果不存在）。

Redis：每次 `/items` 請求先查 Redis cache，cache miss 才查 DB，結果寫回 cache（TTL 60 秒）。

### Dockerfile 規格

- multi-stage（多階段建置）：`builder` 階段裝 build 依賴；`production` 階段用 `python:3.11-slim`
- non-root user：`appuser`，UID 1000
- 不包含 build tools（`gcc`、`pip` cache 等）在 production image 裡

### Compose 規格

- `app` 的 `depends_on`：db condition `service_healthy`，cache condition `service_healthy`
- PostgreSQL health check：`pg_isready -U $POSTGRES_USER`
- Redis health check：`redis-cli ping`
- 每個 service 加 log rotation：`max-size: 50m`，`max-file: 5`
- DB 資料用 named volume 持久化

### 驗收命令

```bash
docker compose up -d
curl http://localhost/health
curl http://localhost/items
docker compose ps
docker compose logs --tail=20 app
```

---

## 期望輸出

```bash
$ curl -s http://localhost/health
{"status":"ok"}

$ curl -s http://localhost/items
[{"id":1,"name":"item-a"},{"id":2,"name":"item-b"}]

$ docker compose ps
NAME              IMAGE           COMMAND                  SERVICE   CREATED         STATUS                   PORTS
myapp-app-1       myapp-app       "python -m uvicorn m…"   app       2 minutes ago   Up 2 minutes             8000/tcp
myapp-cache-1     redis:7-alpine  "docker-entrypoint.s…"   cache     2 minutes ago   Up 2 minutes (healthy)   6379/tcp
myapp-db-1        postgres:16…    "docker-entrypoint.s…"   db        2 minutes ago   Up 2 minutes (healthy)   5432/tcp
myapp-proxy-1     nginx:alpine    "/docker-entrypoint.…"   proxy     2 minutes ago   Up 2 minutes             0.0.0.0:80->80/tcp
```

---

## 實作步驟建議

1. 建目錄結構：
   ```bash
   mkdir -p myapp/app myapp/nginx
   cd myapp
   ```

2. 先寫 `.env`（不要 commit 這個）和 `.env.example`

3. 寫 `app/requirements.txt`

4. 寫 `app/main.py`（FastAPI + asyncpg + redis）

5. 寫 `app/Dockerfile`（multi-stage）

6. 寫 `nginx/nginx.conf`（upstream app:8000，listen 80）

7. 寫 `compose.yml`（四個 service，health check，depends_on，log rotation）

8. 跑起來驗證

---

## 參考解答

<details>
<summary>點開參考實作</summary>

### `.env.example`

```bash
# 複製成 .env 並填入真實值
POSTGRES_USER=appuser
POSTGRES_PASSWORD=changeme
POSTGRES_DB=appdb
```

### `.env`

```bash
POSTGRES_USER=appuser
POSTGRES_PASSWORD=supersecret123
POSTGRES_DB=appdb
```

### `app/requirements.txt`

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
asyncpg==0.29.0
redis[hiredis]==5.0.4
```

### `app/main.py`

```python
from fastapi import FastAPI
import asyncpg
import redis.asyncio as aioredis
import json
import os

app = FastAPI()

DATABASE_URL = (
    f"postgresql://{os.environ['POSTGRES_USER']}"
    f":{os.environ['POSTGRES_PASSWORD']}"
    f"@db:5432/{os.environ['POSTGRES_DB']}"
)
REDIS_URL = "redis://cache:6379"

db_pool: asyncpg.Pool | None = None
redis_client: aioredis.Redis | None = None


@app.on_event("startup")
async def startup():
    global db_pool, redis_client

    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)

    # 初始化 table 和測試資料
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id   SERIAL PRIMARY KEY,
                name TEXT NOT NULL
            )
        """)
        count = await conn.fetchval("SELECT COUNT(*) FROM items")
        if count == 0:
            await conn.executemany(
                "INSERT INTO items (name) VALUES ($1)",
                [("item-a",), ("item-b",)]
            )


@app.on_event("shutdown")
async def shutdown():
    if db_pool:
        await db_pool.close()
    if redis_client:
        await redis_client.close()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/items")
async def get_items():
    # 先查 Redis cache
    cached = await redis_client.get("items")
    if cached:
        return json.loads(cached)

    # cache miss，查 DB
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name FROM items ORDER BY id")

    result = [{"id": r["id"], "name": r["name"]} for r in rows]

    # 寫回 cache，TTL 60 秒
    await redis_client.setex("items", 60, json.dumps(result))
    return result
```

### `app/Dockerfile`

```dockerfile
# syntax=docker/dockerfile:1

# ── Stage 1: builder ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# 只安裝 build 階段需要的工具
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: production ───────────────────────────────────────────
FROM python:3.11-slim

# 建立 non-root user
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/sh -m appuser

WORKDIR /app

# 從 builder 複製已安裝的 packages（不含 gcc 等 build tools）
COPY --from=builder /install /usr/local

# 複製應用程式
COPY --chown=appuser:appuser main.py .

USER appuser

EXPOSE 8000

# exec form（執行格式）：Python 是 PID 1，能收到 SIGTERM
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### `nginx/nginx.conf`

```nginx
# nginx.conf

upstream app_backend {
    server app:8000;
}

server {
    listen 80;
    server_name _;

    # 把所有請求代理到 FastAPI
    location / {
        proxy_pass         http://app_backend;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        # 逾時設定
        proxy_connect_timeout  10s;
        proxy_read_timeout     30s;
        proxy_send_timeout     30s;
    }

    # 健康檢查 endpoint（不過 proxy，直接 nginx 回）
    location /nginx-health {
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }
}
```

### `compose.yml`

```yaml
name: myapp

services:

  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER:     ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB:       ${POSTGRES_DB}
    volumes:
      - db_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 10s
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"

  cache:
    image: redis:7-alpine
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"

  app:
    build:
      context: ./app
      dockerfile: Dockerfile
    restart: unless-stopped
    environment:
      POSTGRES_USER:     ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB:       ${POSTGRES_DB}
    depends_on:
      db:
        condition: service_healthy
      cache:
        condition: service_healthy
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"

  proxy:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "80:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on:
      - app
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"

volumes:
  db_data:
```

### `.dockerignore`（放在 `app/` 目錄內）

```
__pycache__
*.pyc
*.pyo
.env
.env.*
*.egg-info
.git
.pytest_cache
```

### 啟動與驗證腳本

```bash
#!/bin/bash
# verify.sh
set -e

echo "[1] 啟動 stack..."
docker compose up -d --build

echo "[2] 等待所有 service healthy（最多 60 秒）..."
for i in $(seq 1 30); do
  UNHEALTHY=$(docker compose ps --format json \
    | python3 -c "
import json, sys
data = sys.stdin.read().strip()
# docker compose ps --format json 輸出每行一個 JSON
lines = [l for l in data.splitlines() if l.strip()]
unhealthy = [json.loads(l)['Name'] for l in lines
             if json.loads(l).get('Health','') not in ('healthy','')]
print(len(unhealthy))
" 2>/dev/null || echo "999")

  if [ "$UNHEALTHY" = "0" ]; then
    echo "    所有 service 已 healthy"
    break
  fi
  sleep 2
done

echo "[3] 驗證 /health..."
HEALTH=$(curl -sf http://localhost/health)
echo "    回應：$HEALTH"
echo "$HEALTH" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['status']=='ok'" \
  && echo "    PASS /health"

echo "[4] 驗證 /items..."
ITEMS=$(curl -sf http://localhost/items)
echo "    回應：$ITEMS"
echo "$ITEMS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert isinstance(d, list) and len(d) >= 2
assert any(i['name'] == 'item-a' for i in d)
print('    PASS /items')
"

echo "[5] docker compose ps..."
docker compose ps

echo "[6] app log（最後 10 行）..."
docker compose logs --tail=10 app

echo ""
echo "全部通過。"
```

</details>

---

## 測試用例

```bash
# T1：/health 回 {"status":"ok"}
RESP=$(curl -sf http://localhost/health)
echo "$RESP" | python3 -c "import json,sys; assert json.load(sys.stdin)['status']=='ok'" \
  && echo "PASS T1"

# T2：/items 回 list，有 item-a 和 item-b
RESP=$(curl -sf http://localhost/items)
echo "$RESP" | python3 -c "
import json, sys
d = json.load(sys.stdin)
names = {i['name'] for i in d}
assert 'item-a' in names and 'item-b' in names
print('PASS T2')
"

# T3：Redis cache 有效（第二次請求不碰 DB，回應更快）
# 先確認 cache 裡有 key
docker compose exec cache redis-cli get items | grep -q item-a && echo "PASS T3（cache hit）"

# T4：所有 service 都是 healthy 或 running
docker compose ps | grep -v "Up" | grep -v "NAME" | wc -l | grep -q '^0$' \
  && echo "PASS T4（全部 Up）" || echo "FAIL T4（有 service 不是 Up）"

# T5：db volume 存在
docker volume ls | grep -q myapp_db_data && echo "PASS T5（volume 存在）"

# T6：app image 以 non-root 跑
docker compose exec app id | grep -q "uid=1000" && echo "PASS T6（non-root）"
```

---

## 自我檢核

- [ ] `app` 真的等 `db` 和 `cache` 都 `healthy` 才啟動（不是只等 `started`）
- [ ] `.env` 沒有被 COPY 進 image（`docker inspect myapp-app-1` 看 env，密碼是注入的，不是 hardcoded）
- [ ] `docker history myapp-app-1` 看不到密碼
- [ ] Nginx 跑在 port 80，app 不直接對外
- [ ] 停掉 Redis 後，`/items` 仍然能回應（只是慢一點，從 DB 讀）
- [ ] `docker compose logs --tail=20 app` 能看到 uvicorn 的 access log
- [ ] `docker compose down -v` 能乾淨清理（-v 刪 volume）

---

## 常見錯誤

**`app` 一直 restart，log 說 connection refused**

原因：`db` 啟動了但 PostgreSQL 還沒 ready。
解法：確認 `depends_on` 用的是 `condition: service_healthy`，不是預設的 `condition: service_started`。

**`pg_isready` 指令找不到**

原因：用了 `postgres:latest` 而不是 `postgres:16-alpine`，或者 exec 格式寫錯。
解法：`test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]`，注意 `CMD-SHELL` 才會展開 shell 變數。

**Nginx 回 502 Bad Gateway**

原因：`proxy` 啟動時 `app` 還沒好。
解法：`proxy` 加 `depends_on: - app`，或者調大 Nginx 的 `proxy_connect_timeout`。

**image build 很慢**

原因：沒有 `.dockerignore`，把 `__pycache__` 和 `.git` 都傳進 build context。
解法：在 `app/` 目錄建 `.dockerignore`。

---

上一個練習：[練習 A：從零手刻最小容器](./practice-a-minimal-container.md)

下一個練習：[練習 C：Dockerfile 資安審查](./practice-c-security-audit.md)
