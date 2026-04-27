# Ch 4 — docker-compose：服務從來不是單機

> 目標：搞懂 compose 是什麼（以及它不是什麼）、用它把 `tasktrack` 從 SQLite 升級成 FastAPI + PostgreSQL 雙容器本地開發環境。

## compose 不是 K8s 的窮人版

很多人第一次看到 docker-compose 會以為「這是部署工具」。**不是**。

- **Compose** = 本地開發工具。幫你把 `docker run db && docker run app && docker run cache && ...` 這堆指令寫成一個 `docker-compose.yml`，一條 `docker compose up` 就起整套
- **K8s / Nomad / ECS** = 生產部署工具。負責 scale、rolling update、service discovery、健康監控這些 compose 不處理的事

compose 也能用於生產（`docker compose up -d` 在一台 VM 跑就是個簡單部署），但那是「小專案湊合用」的場景。這門課把它當 **開發者的瑞士刀**。

**為什麼 CI/CD 課要教這個？** 因為：

1. 你本地 development 想要的環境，要能跟 CI 對應
2. Integration test 常需要起 DB / Redis，compose 幫你搞定本地那端
3. Ch 11 的 GitHub Actions **service container** 概念跟 compose 完全是同一套思維

## YAML 長什麼樣

最小的 `docker-compose.yml`：

```yaml
services:
  app:
    image: nginx
    ports:
      - "8080:80"
```

`docker compose up` 等同於：

```bash
docker run -p 8080:80 nginx
```

但真正值錢的是多個 service：

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: devpass
  app:
    image: tasktrack
    depends_on:
      - db
```

一條 `docker compose up` 啟動兩個 container，還自動建了一個 network 讓它們互通。

## 核心概念速覽

### services — 每個 service 是一個 container

```yaml
services:
  app:          # service name，也會成為 hostname
    image: ...   # 用現成 image
    build: .     # 或從 Dockerfile build
```

**重點**：service name 會變 hostname。`app` 裡要連 `db`，直接用 `db` 當 hostname 就對（不是 `localhost`！那是常見坑）。

### networks — 預設自動建

compose 預設會建一個 bridge network，所有 service 接上去。**同一個 compose file 的 services 彼此可見**。

進階：可自己定義網路，把 services 分組。這章不深入。

### volumes — 持久化

Container 停掉就乾淨，你的 DB 資料會跟著死。要活下來要掛 volume：

```yaml
services:
  db:
    image: postgres:16
    volumes:
      - pgdata:/var/lib/postgresql/data   # 左邊是 volume 名，右邊是 container 內路徑

volumes:
  pgdata:       # 宣告一個 named volume
```

Named volume 由 Docker 管，`docker volume ls` 看得到。也可用 **bind mount**（掛本地資料夾）：

```yaml
volumes:
  - ./data:/var/lib/postgresql/data
```

bind mount 對 dev 方便（能直接看檔案），但效能在 Mac 上會慘（Docker Desktop 的 VM 跨越 filesystem 有 overhead）。

### depends_on + healthcheck

`depends_on` 只保證 **啟動順序**，不保證 service 已經 ready：

```yaml
app:
  depends_on:
    - db           # ← db container 啟動後就繼續，不管它好沒好
```

想等 db **真的能接受連線**：

```yaml
db:
  image: postgres:16
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U postgres"]
    interval: 3s
    timeout: 3s
    retries: 10

app:
  depends_on:
    db:
      condition: service_healthy     # ← 等 db 健康才啟動
```

**這很重要**。不然 app 先起、db 還沒好，app 連線失敗直接 crash。

### environment / env_file

```yaml
services:
  app:
    environment:
      DATABASE_URL: postgresql+psycopg2://postgres:devpass@db:5432/tasktrack
      DEBUG: "1"
    env_file:
      - .env                # 從檔案讀（不進 git）
```

**環境變數處理 secrets 是開發時權宜之計**。正式 secret 要用 Docker secrets 或 external 機制，Ch 9 再談 GitHub Actions 那邊的。

## 實作：把 `tasktrack` 升級到 Postgres

Ch 0 起，tasktrack 用的是 SQLite：`sqlite:///./tasktrack.db`。我們現在把它改用 Postgres，用 compose 串。

### Step 1：`requirements.txt` 加 psycopg

```
fastapi>=0.115
uvicorn[standard]>=0.30
sqlalchemy>=2.0
pydantic>=2.5
psycopg[binary]>=3.2
```

`psycopg[binary]` 是 psycopg3 的預編版本，不用編譯 C。Ch 3 的 slim + multi-stage 依然成立。

### Step 2：`app/db.py` 的 URL default 改動

你其實不需要改 code — 只要透過環境變數覆蓋即可：

```python
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./tasktrack.db")
```

這行原本就設計成：**有 env 就用 env，沒有就 fallback SQLite**。這是 12-factor app 的思維：**config 外置**。

### Step 3：寫 `docker-compose.yml`

在 `tasktrack/` 根目錄：

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: devpass
      POSTGRES_DB: tasktrack
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d tasktrack"]
      interval: 3s
      timeout: 3s
      retries: 10

  app:
    build: .
    depends_on:
      db:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+psycopg://postgres:devpass@db:5432/tasktrack
    ports:
      - "8000:8000"

volumes:
  pgdata:
```

幾件事注意：

- `build: .` 用本地 Dockerfile（Ch 3 multi-stage 那版）build
- `db` 在 DATABASE_URL 裡是 hostname — 只在這個 network 內解析得到
- `postgresql+psycopg://` 是 SQLAlchemy 指定用 psycopg3 driver 的語法
- 密碼 `devpass` 寫在 compose 裡 **是故意的**：本地開發環境。生產要用 secret

### Step 4：起動

```bash
docker compose up --build
```

`--build` 第一次加，之後如果你改 Dockerfile 或 code 就需要加。

看 log：

```
db-1   | database system is ready to accept connections
db-1   | (healthy check passed)
app-1  | INFO:     Uvicorn running on http://0.0.0.0:8000
```

另開終端：

```bash
curl -X POST localhost:8000/tasks \
  -H 'content-type: application/json' \
  -d '{"title": "學 compose", "milestone": "week-1"}'
# {"id":1,...}
```

**停掉**：

```bash
docker compose down             # 停 container、清 network
docker compose down -v          # 再清 volume（資料也沒了，慎用）
```

### Step 5：驗證資料真的有持久化

```bash
docker compose up -d                     # -d 背景跑
curl -X POST localhost:8000/tasks -H 'content-type: application/json' -d @task.json

docker compose down                      # 停 container
docker compose up -d                     # 再起
curl localhost:8000/tasks                # ← 應該還在
```

如果你 `down -v`，volume 也清了，資料就沒了。

## compose 常用指令

| 指令 | 幹嘛 |
|---|---|
| `docker compose up` | 起全部 service（前景） |
| `docker compose up -d` | 背景起 |
| `docker compose up --build` | 順便 rebuild image |
| `docker compose down` | 停並清 container、network |
| `docker compose down -v` | 再清 volume（資料刪掉） |
| `docker compose logs -f app` | 跟某個 service 的 log |
| `docker compose exec app bash` | 進某 container 的 shell（debug 用） |
| `docker compose ps` | 看哪些 service 在跑 |
| `docker compose restart app` | 重啟某 service（不 rebuild） |

## 動手練習

1. 完成上面 Step 1–5，`docker compose up` 起整套 + `curl` 打得通
2. 跑 `docker compose exec db psql -U postgres -d tasktrack -c "select * from tasks;"` 直接查 DB
3. `docker compose down && docker compose up`，驗證資料還在
4. 故意把 `depends_on.db.condition: service_healthy` 拿掉，重跑看 app 起得比 db 快會發生什麼（會 crash）
5. 把 DB 密碼改成 `.env` 檔讀（`env_file:`），跑一次

## 常見誤解

- 「**compose 能 deploy 生產**」 — 小專案勉強，但缺 rolling update、scale、service discovery。生產要 K8s / ECS
- 「**service 之間用 localhost 通訊**」 — 錯。要用 service name 當 hostname（`db:5432`，不是 `localhost:5432`）
- 「**depends_on 保證順序就是保證 ready**」 — 不是。要 healthcheck + `condition: service_healthy`
- 「**bind mount 跟 named volume 一樣快**」 — Mac/Windows Docker Desktop 跨 VM，bind mount 有顯著 overhead
- 「**`docker compose down` 會清 volume**」 — 不會。加 `-v` 才清，你的 dev DB 資料預設保留

## 驗收標準

- [ ] `tasktrack/docker-compose.yml` 完整、`docker compose up --build` 起得來
- [ ] `db` 的 healthcheck 有配，`app` 只在 db 健康後啟動
- [ ] `curl` POST/GET/PATCH 三個 endpoint 都通
- [ ] `docker compose down` 再 `up`，資料還在（volume 生效）
- [ ] 你能解釋 `DATABASE_URL` 裡的 `db` 為什麼不能寫 `localhost`

## 自我檢核

- [ ] 我知道 compose 是 dev 工具不是部署工具
- [ ] 我會用 service name 當 hostname
- [ ] 我懂 `depends_on` + `healthcheck` + `condition: service_healthy` 組合
- [ ] 我會用 named volume 做持久化
- [ ] 我理解為什麼「config 外置到 env」是 12-factor 的核心

下一章處理兩個常被忽略的生產議題：**容器以 root 跑很危險**、**只 build `amd64` 在 arm 機器上跑不起來**。

→ [Ch 5 容器安全與多平台建構](./05-container-security-multiarch.md)
