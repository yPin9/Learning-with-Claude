# Ch 16 — Health Check 與 depends_on

> 目標：搞清楚 container 啟動和服務 ready 是兩件事，學會用 health check 定義「服務真的好了」的標準，並用 depends_on condition 讓啟動順序有意義。

---

## 問題：container 跑起來 ≠ 服務 ready

這是 Compose 最常見的 bug 來源：

```
timeline

t=0   docker compose up
t=1   db container: STATUS = Up
t=1   app container: 嘗試連 db
t=3   db: PostgreSQL 還在跑 initdb...（初始化要幾秒）
t=3   app: connection refused -> crash
t=3   app container: exit 1
```

`depends_on` 的預設行為只等 container **started**，不等服務實際可用。解法是在 db 上加 health check，然後讓 app `depends_on` 等到 db **healthy**。

---

## Dockerfile `HEALTHCHECK` 指令

在 image 層定義健康檢查，所有用這個 image 的容器都繼承：

```dockerfile
FROM python:3.11-slim

# ...

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1
```

參數說明：

| 參數 | 預設值 | 說明 |
|------|--------|------|
| `--interval` | 30s | 每隔多久檢查一次 |
| `--timeout` | 30s | 單次檢查的超時時間 |
| `--start-period` | 0s | 啟動後多久才開始計算失敗次數（初始化期間的寬限時間） |
| `--retries` | 3 | 連續失敗幾次才判定 unhealthy |

---

## 容器健康狀態機

```
              container 啟動
                    |
                    v
              [ starting ]
                    |
         經過 start-period 後開始檢查
                    |
        +-----------+-----------+
        |                       |
   檢查成功                  連續失敗 retries 次
        |                       |
        v                       v
   [ healthy ]            [ unhealthy ]
        |                       |
        +---再次失敗 retries 次--+
```

`docker ps` 的 STATUS 欄會顯示健康狀態：

```bash
docker ps
# CONTAINER ID  IMAGE             STATUS
# abc123        myapp:latest      Up 2 minutes (healthy)
# def456        myapp:latest      Up 10 seconds (health: starting)
# ghi789        myapp:latest      Up 5 minutes (unhealthy)
```

---

## Compose `healthcheck:` 區塊

在 compose.yml 裡定義，**覆蓋** Dockerfile 裡的 HEALTHCHECK：

```yaml
services:
  db:
    image: postgres:16
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d mydb"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s   # Postgres 初始化要一點時間
```

`test` 的格式有兩種：

```yaml
# SHELL 格式：走 /bin/sh -c，可以用 || 和 &&
test: ["CMD-SHELL", "curl -f http://localhost/ || exit 1"]

# EXEC 格式：直接 exec，不走 shell，效率略好
test: ["CMD", "pg_isready", "-U", "postgres"]
```

停用 Dockerfile 裡的 health check：

```yaml
healthcheck:
  disable: true
```

---

## `depends_on` 三種 condition

```yaml
services:
  app:
    depends_on:
      db:
        condition: service_healthy            # 等 db healthy 才啟動 app
      redis:
        condition: service_started            # 只等 redis container 啟動（預設行為）
      migrate:
        condition: service_completed_successfully   # 等 migrate 這個 one-shot 跑完且 exit 0
```

| condition | 等到什麼狀態 | 用途 |
|-----------|-------------|------|
| `service_started` | container 啟動（PID 1 存在） | 預設值，幾乎沒意義 |
| `service_healthy` | health check 回傳成功 | DB、cache、第三方服務 |
| `service_completed_successfully` | container 跑完且 exit code = 0 | db migration、data seed 等 init job |

---

## 完整範例：PostgreSQL + FastAPI

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: myuser
      POSTGRES_PASSWORD: mypassword
      POSTGRES_DB: mydb
    volumes:
      - db_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U myuser -d mydb"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s
    networks:
      - backend

  migrate:
    image: myapp:latest
    command: alembic upgrade head
    environment:
      DATABASE_URL: postgresql://myuser:mypassword@db:5432/mydb
    depends_on:
      db:
        condition: service_healthy
    networks:
      - backend

  app:
    image: myapp:latest
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql://myuser:mypassword@db:5432/mydb
    depends_on:
      db:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 10s
    networks:
      - backend

networks:
  backend:

volumes:
  db_data:
```

啟動順序：

```
db 啟動 -> db healthy
    -> migrate 啟動 -> migrate exit 0
        -> app 啟動
```

---

## 重啟策略

容器掛掉後要不要自動重啟，`restart:` 控制：

```yaml
services:
  app:
    restart: unless-stopped
```

| 策略 | 行為 |
|------|------|
| `no` | 不自動重啟（預設） |
| `always` | 永遠重啟，包括 Docker daemon 重啟後 |
| `unless-stopped` | 重啟，但如果是手動 `docker stop` 就不重啟 |
| `on-failure[:N]` | 只在非零 exit code 時重啟，可指定最多重試次數 |

**生產環境選哪個：**

- 長跑服務（web server、API）：`unless-stopped`
- 批次 job、migration：`on-failure:3`（不要無限重試）
- 開發環境：`no`（讓它死，你去看 log）

`unless-stopped` 和 `always` 的差別在於手動 `docker stop` 之後，重啟 Docker daemon 時，`unless-stopped` 不會自動把你手動停掉的容器拉起來，`always` 會。

---

## 自我檢核

- [ ] 能解釋為什麼 `depends_on` 預設行為解決不了 DB 還在初始化的問題
- [ ] 知道 `--start-period` 的作用，以及為什麼 Postgres 需要設得長一點
- [ ] 能寫出 `pg_isready` 的 health check，以及 HTTP API 的 health check
- [ ] 知道三種 condition 各自等的是什麼狀態，以及 `service_completed_successfully` 的使用場景
- [ ] 能說明 `unless-stopped` 和 `always` 的差別

下一章處理「同一份 compose.yml 怎麼同時服務開發和生產」的問題，答案是 override 和 profiles。

→ [Ch 17 Compose Override 與 Profiles](./17-compose-override-profiles.md)
