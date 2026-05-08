# Ch 14 — Compose 基礎

> 目標：搞清楚 docker compose v2 和舊版的差別，掌握 compose.yml 的三大區塊結構，能用 Compose 跑一個完整的多容器服務，並熟悉日常操作指令。

---

## v1 vs v2：別再用舊版

很多教學還在教 `docker-compose`（有橫線），現在要用的是 `docker compose`（空格），差異不只是名字：

| 維度 | docker-compose v1 | docker compose v2 |
|------|-------------------|-------------------|
| 實作語言 | Python | Go |
| 安裝方式 | `pip install` 或獨立二進位 | Docker CLI 內建 plugin |
| 設定檔名 | `docker-compose.yml` | `compose.yml`（新名稱，優先讀） |
| 效能 | 較慢，啟動有 Python overhead | 快 |
| 維護狀態 | 2023 年已停止維護 | 持續開發中 |
| Compose Spec | 部分支援 | 完整支援 |

**Compose Spec（Compose 規範）**是從 Docker Compose 格式衍生出的開放標準，由 Compose Working Group 維護。v2 完整實作這個規範。

設定檔名優先順序：`compose.yaml` > `compose.yml` > `docker-compose.yaml` > `docker-compose.yml`。舊檔名還是能用，只是新專案直接用 `compose.yml`。

```bash
# 確認 v2 已安裝
docker compose version
# Docker Compose version v2.x.x
```

---

## 三大區塊結構

```
compose.yml
+----------------------------------------------------------+
|  services:                                               |
|    web:          <- 服務（容器）定義                      |
|      image: ...                                          |
|      ports: ...                                          |
|      networks:                                           |
|        - frontend                                        |
|    db:                                                   |
|      image: postgres:16                                  |
|      networks:                                           |
|        - backend                                         |
|                                                          |
|  networks:                                               |
|    frontend:     <- 宣告 network，Compose 自動建立        |
|    backend:                                              |
|                                                          |
|  volumes:                                                |
|    db_data:      <- 宣告 named volume，Compose 自動建立   |
+----------------------------------------------------------+
```

三個頂層 key 的用途：

| 區塊 | 作用 | 不宣告時 |
|------|------|---------|
| `services` | 定義要跑哪些容器、用什麼 image、怎麼設定 | 必填，沒有 services 什麼都跑不起來 |
| `networks` | 定義自訂網路，services 裡可以引用 | Compose 自動建一個預設 network，所有 service 都在裡面 |
| `volumes` | 定義 named volume，services 裡可以引用 | 只能用 bind mount，無法用 named volume |

---

## 完整範例：Nginx + 自訂 HTML

專案結構：

```
mysite/
  compose.yml
  html/
    index.html
```

`html/index.html`：

```html
<!DOCTYPE html>
<html>
<body>
  <h1>Hello from Compose</h1>
</body>
</html>
```

`compose.yml`：

```yaml
services:
  web:
    image: nginx:1.25-alpine
    ports:
      - "8080:80"
    volumes:
      - ./html:/usr/share/nginx/html:ro
    networks:
      - frontend

networks:
  frontend:
```

幾個細節：

- `ports` 的格式是 `"HOST:CONTAINER"`，加引號避免 YAML 把 `8080:80` 解析成數字
- volume 的 `:ro` 是 read-only，靜態檔案不需要容器有寫入權限
- `networks: frontend:` 後面沒有任何 key 也合法，Compose 用預設設定建立這個 network

---

## 常用指令

### `docker compose up -d`

啟動所有服務，`-d` 是 detached（背景）模式。

```bash
cd mysite
docker compose up -d
# [+] Running 2/2
#  Network mysite_frontend  Created
#  Container mysite-web-1   Started
```

Compose 自動給資源加上 project 前綴（預設是目錄名），`mysite_frontend`、`mysite-web-1`。要自訂 project name：

```bash
docker compose -p myproject up -d
```

### `docker compose logs -f web`

追蹤指定服務的 log，`-f` 是 follow（持續輸出）。不加服務名字就看所有服務。

```bash
docker compose logs -f web
# 加 --tail=50 只看最後 50 行
docker compose logs --tail=50 web
```

### `docker compose exec web sh`

進入正在跑的容器，等同於 `docker exec -it <container_id> sh`。

```bash
docker compose exec web sh
# 在 alpine image 裡用 sh，不是 bash
```

### `docker compose ps`

看各服務狀態。

```bash
docker compose ps
# NAME           IMAGE                COMMAND                  SERVICE   CREATED        STATUS
# mysite-web-1   nginx:1.25-alpine   "/docker-entrypoint.…"   web       2 minutes ago  Up 2 minutes
```

### `docker compose down` vs `down -v`

這兩個差很多，要分清楚：

```bash
# 停止並刪除 container + network，保留 volume
docker compose down

# 停止並刪除 container + network + volume（資料全清）
docker compose down -v
```

`down` 不刪 volume 是有意設計的，DB 資料不應該因為重新部署就消失。`-v` 是你真的要清掉所有狀態時才用。

### `docker compose build`

重新建立所有有 `build:` 區塊的 service image。

```bash
docker compose build
# 只建指定的 service
docker compose build app
# 不用 cache 強制重建
docker compose build --no-cache app
```

---

## `build:` 區塊：用自己的 Dockerfile

當你需要自訂 image 而不是直接用現成的，用 `build:` 取代 `image:`：

```yaml
services:
  app:
    build:
      context: .          # Dockerfile 所在目錄
      dockerfile: Dockerfile.prod   # 預設是 Dockerfile
      args:
        BUILD_VERSION: "1.2.0"
    ports:
      - "3000:3000"
    networks:
      - backend

networks:
  backend:
```

`build:` 和 `image:` 可以同時寫，這時 `image:` 指定建好後要 tag 成什麼名字：

```yaml
services:
  app:
    build: .
    image: myapp:latest   # build 完後 tag 成這個名字
```

---

## Replicas：`scale` vs `deploy.replicas`

水平擴展有兩種寫法，用途不同：

**指令層（臨時）**：

```bash
# 把 web 擴展到 3 個 replica
docker compose up -d --scale web=3
```

**設定層（宣告式）**，`deploy.replicas` 是 Compose Spec 的一部分，但完整的 Swarm 排程功能要在 Docker Swarm（叢集編排）模式才生效：

```yaml
services:
  web:
    image: nginx:1.25-alpine
    deploy:
      replicas: 3
    ports:
      - "8080-8082:80"   # 多個 replica 要用 port range
```

單機跑 Compose 時，`deploy.replicas` 有效，但 `deploy` 下的其他 Swarm 專屬設定（`placement`、`resources` 等）會被忽略。多個 replica 的情況下不能用固定的 `HOST:CONTAINER` port mapping，因為多個容器不能同時 bind 同一個 host port。

---

## 自我檢核

- [ ] 能說明 docker-compose v1 和 docker compose v2 的核心差異
- [ ] 知道 compose.yml 和 docker-compose.yml 哪個優先
- [ ] 能從零寫一個有 service / network / volume 的 compose.yml
- [ ] 知道 `docker compose down` 和 `down -v` 的差別，以及為什麼預設不刪 volume
- [ ] 知道 `build:` 區塊的 context / dockerfile / args 各控制什麼
- [ ] 能解釋多 replica 時為什麼不能用固定 host port mapping

下一章處理環境變數的正確傳法，以及比環境變數更安全的 Secrets 機制。

→ [Ch 15 環境變數與 Secrets](./15-env-secrets.md)
