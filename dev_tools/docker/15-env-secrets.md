# Ch 15 — 環境變數與 Secrets

> 目標：搞清楚 Compose 傳環境變數的三種方式各有什麼坑，掌握 `.env` 的自動讀取行為，並學會用 Secrets 機制避免敏感資料洩漏。

---

## 三種傳環境變數的方式

```
方式 1：environment: 直接寫值
  compose.yml 裡明文 → 方便但危險（會進 git）

方式 2：env_file: 從 .env 讀
  值放在獨立檔案 → .env 加入 .gitignore，只提交 .env.example

方式 3：${VAR} 從 shell 繼承
  值在 shell 環境裡 → CI/CD 系統最常用這招
```

| 方式 | 語法 | 優點 | 缺點 |
|------|------|------|------|
| `environment:` 直接寫 | `KEY: value` | 一目瞭然 | 明文進 compose.yml，容易 commit 進 git |
| `env_file:` | `env_file: .env` | 值和設定分離 | 需要多管理一個檔案 |
| `${VAR}` 繼承 shell | `KEY: ${MY_VAR}` | 適合 CI/CD 用 secret 注入 | 部署者要先設好 shell env，沒設就是空字串 |

三種方式可以混用，同一個 key 存在多個地方時，**覆蓋優先順序**：

```
shell env  >  .env 檔  >  environment: 裡寫的 default
（高）                                           （低）
```

---

## 方式 1：`environment:` 直接寫

```yaml
services:
  app:
    image: myapp:latest
    environment:
      APP_ENV: production
      APP_PORT: "3000"
      DEBUG: "false"
```

或者 list 格式（兩種都合法，mapping 格式比較清楚）：

```yaml
    environment:
      - APP_ENV=production
      - APP_PORT=3000
```

只寫 key 不寫值，就是從 shell 繼承該 key 的值：

```yaml
    environment:
      - DATABASE_URL    # 繼承 shell 裡的 DATABASE_URL
```

---

## 方式 2：`env_file:` 讀檔案

`.env` 檔案格式：

```
POSTGRES_PASSWORD=secret123
APP_ENV=production
# 這是註解
REDIS_URL=redis://redis:6379/0
```

compose.yml 裡引用：

```yaml
services:
  app:
    image: myapp:latest
    env_file:
      - .env
      - .env.local    # 可以疊加多個，後面的優先
```

---

## `.env` 的自動讀取行為（最常讓人混淆）

這裡有一個行為很多人搞錯：

**Compose 會自動讀取 compose.yml 同目錄下的 `.env` 檔，不需要你在 compose.yml 裡寫 `env_file: .env`。**

這個自動讀取的用途是**展開 compose.yml 裡的 `${VAR}` 變數**，不是直接把 `.env` 裡的值注入容器。

```
.env
  DB_IMAGE=postgres:16
  APP_PORT=3000

compose.yml
  services:
    db:
      image: ${DB_IMAGE}       <- Compose 讀 .env，展開成 postgres:16
    app:
      ports:
        - "${APP_PORT}:3000"   <- 展開成 3000:3000
```

如果你要把 `.env` 裡的值**注入進容器**，你還是要明確寫 `env_file: .env`：

```yaml
services:
  app:
    env_file: .env      # 這樣容器裡才看得到 DB_IMAGE 這個環境變數
```

用 `docker compose config` 可以看展開後的最終設定，debug 必備：

```bash
docker compose config
# 輸出展開所有 ${VAR} 後的完整 compose.yml
```

---

## Secrets（Compose v2）：比環境變數更安全

敏感資料（資料庫密碼、API key、憑證）不該放在環境變數裡。原因：

- `docker inspect <container>` 直接看到所有環境變數，明文
- 程式 crash dump 可能把環境變數印出來
- 環境變數會被子進程繼承

Compose v2 的 Secrets 機制把值以檔案形式掛進容器，路徑固定在 `/run/secrets/<name>`：

```yaml
services:
  app:
    image: myapp:latest
    secrets:
      - db_password
      - api_key
    environment:
      DB_HOST: db

  db:
    image: postgres:16
    secrets:
      - db_password
    environment:
      POSTGRES_PASSWORD_FILE: /run/secrets/db_password

secrets:
  db_password:
    file: ./secrets/db_password.txt
  api_key:
    file: ./secrets/api_key.txt
```

應用程式從 `/run/secrets/db_password` 讀取密碼：

```python
with open('/run/secrets/db_password') as f:
    password = f.read().strip()
```

PostgreSQL 支援 `_FILE` 後綴的環境變數，直接讀檔，不用改應用程式邏輯。

**在真正的 Docker Swarm 模式下**，secret 的值存在 Swarm 的 Raft 加密 log 裡，傳輸走 TLS，安全性更高。Compose 單機模式只是把 host 檔案 bind mount 進去，安全性差一些，但至少不在 `docker inspect` 裡看得到。

---

## ARG 會留在 docker history（重要安全提醒）

Dockerfile 的 `ARG` 傳進去的值會留在 image history 裡：

```bash
docker history myapp:latest
# IMAGE          CREATED BY
# ...
# <missing>      |1 SECRET_KEY=mysecretvalue /bin/sh -c ...  <- 洩漏了
```

敏感資料不能用 `ARG` 傳。要在 build 時使用 secret（例如 npm private registry token），用 BuildKit 的 `--mount=type=secret`：

```dockerfile
# syntax=docker/dockerfile:1
RUN --mount=type=secret,id=npm_token \
    NPM_TOKEN=$(cat /run/secrets/npm_token) npm install
```

```bash
docker build --secret id=npm_token,src=./secrets/npm_token .
```

這樣 secret 只在那一個 `RUN` 指令執行時存在，不會進 image layer，`docker history` 裡看不到值。

---

## 實務建議

`.gitignore` 至少要包含：

```
.env
.env.local
secrets/
```

用 `.env.example` 當模板提交進 git，記錄有哪些 key 但不放真實值：

```
# .env.example
POSTGRES_PASSWORD=your-password-here
APP_ENV=development
REDIS_URL=redis://redis:6379/0
```

新人 clone 專案後：

```bash
cp .env.example .env
# 然後填入真實值
```

---

## 自我檢核

- [ ] 能說明 `.env` 自動讀取和 `env_file:` 的差別，以及哪個把值注入容器、哪個展開 compose.yml 的變數
- [ ] 知道 shell env / .env / environment: 三者的覆蓋優先順序
- [ ] 能寫一個用 Secrets 機制傳資料庫密碼的 compose.yml
- [ ] 知道為什麼 `ARG` 不能傳敏感資料，以及 `--mount=type=secret` 怎麼解決這個問題
- [ ] 知道 `docker compose config` 的用途

下一章解決一個常見的啟動順序問題：容器跑起來不代表服務 ready，health check 和 depends_on 是正確的解法。

→ [Ch 16 Health Check 與 depends_on](./16-healthcheck-depends.md)
