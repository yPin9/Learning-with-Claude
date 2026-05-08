# Ch 17 — Compose Override 與 Profiles

> 目標：學會用 override 機制讓同一套服務定義在開發和生產之間靈活切換，用 profiles 讓輔助工具只在需要時啟動，不讓 compose.yml 變成一鍋粥。

---

## 問題：開發和生產的設定差很多

開發時你想要：
- 掛進 source code 做 hot reload
- 開 debug port
- 用 `build: .` 而不是已發布的 image
- 跑 db-admin 工具（Adminer、pgAdmin）

生產時你只想要：
- 固定版本的 image
- 沒有多餘的 port
- 沒有 source code 掛載

把兩套設定硬塞進同一個 compose.yml，靠 if-else 環境變數區分，是一條走不完的路。正確做法是 **override 機制**。

---

## Override 機制：自動 merge

Compose 在同一個目錄下，自動把 `compose.yml` 和 `compose.override.yml` **合併**起來執行：

```
compose.yml          （base，提交進 git，包含生產設定）
compose.override.yml （本地開發用，加進 .gitignore）
         |
         v
   Compose 合併兩份設定，以 override 優先
```

不需要任何旗標，`docker compose up` 就自動 merge。

---

## 合併規則

這是最容易出錯的地方，要記清楚：

| 資料型別 | 合併行為 | 例子 |
|---------|---------|------|
| mapping（key-value） | 合併，override 的值蓋掉 base | `environment:` 同 key 以 override 為準 |
| list | **附加**，不是替換 | `volumes:` 兩邊都保留 |
| scalar（字串/數字） | override 蓋掉 base | `image:` 以 override 為準 |

`volumes:` 和 `ports:` 是 list，所以兩份設定的 volume 和 port **都生效**，這點常讓人意外。

---

## 完整範例：開發 vs 生產

`compose.yml`（base，提交進 git）：

```yaml
services:
  app:
    image: myapp:latest
    environment:
      APP_ENV: production
      DATABASE_URL: postgresql://myuser:${DB_PASSWORD}@db:5432/mydb
    depends_on:
      db:
        condition: service_healthy
    networks:
      - backend
    restart: unless-stopped

  db:
    image: postgres:16
    environment:
      POSTGRES_USER: myuser
      POSTGRES_PASSWORD: ${DB_PASSWORD}
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

networks:
  backend:

volumes:
  db_data:
```

`compose.override.yml`（開發用，加進 .gitignore）：

```yaml
services:
  app:
    build: .              # 用本地 Dockerfile 建，不用 myapp:latest
    volumes:
      - .:/app            # source code bind mount，hot reload
      - /app/node_modules # 匿名 volume，避免 host 的 node_modules 蓋掉容器的
    environment:
      APP_ENV: development
      DEBUG: "true"
    ports:
      - "5678:5678"       # debugger port（例如 debugpy）
    restart: "no"         # 開發時讓它死，不要自動重啟

  db:
    ports:
      - "5432:5432"       # 開發時開放給本機連
```

合併後的效果：

```
app:
  image: myapp:latest     <- base
  build: .                <- override 新增，build 優先於 image
  volumes:
    - .:/app              <- override 的 list 附加
    - /app/node_modules
  environment:
    APP_ENV: development  <- override 蓋掉 production
    DATABASE_URL: ...     <- base 保留
    DEBUG: "true"         <- override 新增
  ports:
    - "5678:5678"         <- override 新增
  restart: "no"           <- override 蓋掉 unless-stopped
  ...
```

---

## 明確指定：`-f` 旗標

自動 merge 只在有 `compose.override.yml` 時才發生。生產環境部署時，不要有 override 檔案，直接跑 `compose.yml`：

```bash
# 生產：只用 base
docker compose -f compose.yml up -d

# 或明確合併兩份（也可以這樣指定自訂名稱的 override）
docker compose -f compose.yml -f compose.prod.yml up -d

# 多環境範例
docker compose -f compose.yml -f compose.staging.yml up -d
```

多個 `-f` 的合併順序：左邊是 base，右邊是 override，後面的蓋前面的。

---

## Profiles：按需啟動的 service

有些服務不是每次都需要，例如：

- `adminer`（DB 管理介面）只在開發時用
- `prometheus` + `grafana` 只在 debug 效能時需要
- `mailcatcher`（攔截 email）只在測試時用

用 `profiles:` 標記這些服務屬於哪個 profile，不帶 `--profile` 旗標時這些服務**不啟動**：

```yaml
services:
  app:
    image: myapp:latest
    # 沒有 profiles，永遠啟動

  db:
    image: postgres:16
    # 沒有 profiles，永遠啟動

  adminer:
    image: adminer:latest
    ports:
      - "8080:8080"
    profiles: [dev]     # 只在 --profile dev 時啟動

  db-backup:
    image: prodrigestivill/postgres-backup-local
    profiles: [prod]    # 只在 --profile prod 時啟動

  prometheus:
    image: prom/prometheus:latest
    profiles: [dev, debug]   # 在 dev 或 debug profile 時啟動
```

啟動方式：

```bash
# 只啟動沒有 profiles 的服務（app + db）
docker compose up -d

# 啟動 dev profile 的服務（app + db + adminer + prometheus）
docker compose --profile dev up -d

# 啟動多個 profile
docker compose --profile dev --profile debug up -d

# 環境變數方式（CI 裡常用）
COMPOSE_PROFILES=dev,debug docker compose up -d
```

---

## 實務檔案結構

```
project/
  compose.yml             <- 提交 git，包含生產設定
  compose.override.yml    <- 加進 .gitignore，本地開發用
  compose.staging.yml     <- 提交 git，staging 環境的差異
  .env.example            <- 提交 git，變數模板
  .env                    <- 加進 .gitignore，本地實際值
```

`.gitignore` 要加的：

```
compose.override.yml
.env
secrets/
```

---

## 自我檢核

- [ ] 能說明 Compose 什麼情況下自動 merge `compose.override.yml`
- [ ] 知道 list 和 mapping 的合併行為差在哪，能舉例說明 volumes 的附加行為
- [ ] 知道生產部署時怎麼避免自動 merge 到 override 檔案
- [ ] 能寫一個有 dev profile 的 compose.yml，讓 adminer 只在 --profile dev 時啟動
- [ ] 知道 COMPOSE_PROFILES 環境變數的用途

下一章來看如果你不想用 Docker Hub，怎麼自己架一個 private registry。

→ [練習 B：FastAPI + PostgreSQL + Redis + Nginx](./practice-b-compose-stack.md)
