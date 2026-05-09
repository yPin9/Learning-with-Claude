# Ch 2 — 環境搭建：雲端試玩 + 本地 Docker

> 目標：用兩種方式把 n8n 跑起來，選定一個作為後續練習環境。

## 兩條路

```
路線 A：n8n Cloud（雲端）
  優點：零安裝，開帳號就能用
  缺點：免費方案有 workflow 數量限制，資料在 n8n 伺服器

路線 B：本地 Docker（推薦）
  優點：完全免費，資料在自己機器，和生產環境一致
  缺點：需要 Docker，webhook 對外需要額外設定（ngrok）
```

這章兩條都教，但後續範例全用路線 B 示範。

---

## 路線 A：n8n Cloud

1. 打開 https://app.n8n.cloud/register
2. 填 email + 密碼，完成 email 驗證
3. 選 trial plan（14 天免費）
4. 直接進 dashboard，點「New Workflow」

就這樣。如果你只是想先感受介面，Cloud 是最快的方式。

---

## 路線 B：本地 Docker（推薦）

### 前置需求

先確認 Docker 和 Docker Compose 有裝好：

```bash
docker --version
# Docker version 24.x.x

docker compose version
# Docker Compose version v2.x.x
```

沒裝的話去 https://docs.docker.com/get-docker/ 裝 Docker Desktop（Windows/Mac 都有），它會一起把 Compose 裝好。

### 最簡單的啟動方式（單容器）

```bash
docker run -it --rm \
  --name n8n \
  -p 5678:5678 \
  -v ~/.n8n:/home/node/.n8n \
  docker.n8n.io/n8nio/n8n
```

跑起來後打開 http://localhost:5678 就能看到 n8n 介面。

這個方式適合快速試用，但 `-it --rm` 表示關掉終端機 n8n 就停了，資料也可能不持久（雖然 `-v` 有掛 volume）。

### 正確的方式：Docker Compose

建一個目錄放設定：

```bash
mkdir ~/n8n-local && cd ~/n8n-local
```

建 `docker-compose.yml`：

```yaml
version: '3.8'

services:
  n8n:
    image: docker.n8n.io/n8nio/n8n
    container_name: n8n
    restart: unless-stopped
    ports:
      - "5678:5678"
    environment:
      - N8N_HOST=localhost
      - N8N_PORT=5678
      - N8N_PROTOCOL=http
      - NODE_ENV=production
      - WEBHOOK_URL=http://localhost:5678/
      - GENERIC_TIMEZONE=Asia/Taipei
      - TZ=Asia/Taipei
    volumes:
      - n8n_data:/home/node/.n8n

volumes:
  n8n_data:
```

啟動：

```bash
docker compose up -d
```

查看狀態：

```bash
docker compose ps
# NAME    STATUS    PORTS
# n8n     running   0.0.0.0:5678->5678/tcp
```

查看 log：

```bash
docker compose logs -f n8n
```

停止：

```bash
docker compose down
```

### 驗證安裝

打開瀏覽器，輸入 http://localhost:5678

第一次進入會要求建立 owner 帳號：

```
First Name: 任意
Last Name:  任意
Email:      你的 email（本地用，不需要真實）
Password:   至少 8 位，含大小寫和數字
```

填完送出，進到 n8n 主介面就代表安裝成功。

---

## Webhook 的問題

n8n 的 Webhook node 需要從外部接收 HTTP 請求。本地跑的 n8n `localhost:5678` 外部存取不到，這會影響 Ch 12 的練習。

解法：用 **ngrok** 把本地 port 暴露到公開 URL。

```bash
# 安裝 ngrok（先去 https://ngrok.com 免費註冊）
ngrok http 5678
```

跑起來後 ngrok 會給你一個 `https://xxxx.ngrok-free.app` 的 URL。把這個填進 n8n 的環境變數：

```yaml
# docker-compose.yml 的 environment 加這行
- WEBHOOK_URL=https://xxxx.ngrok-free.app/
```

改完 `docker compose up -d` 重啟。

---

## 版本選擇

n8n 更新很頻繁，有兩個 tag：

| tag | 說明 |
|---|---|
| `latest` | 最新穩定版，適合本地練習 |
| `1.x.x` | 固定版本，適合生產環境 |

本套教材用 `latest`，但你如果想固定版本：

```yaml
image: docker.n8n.io/n8nio/n8n:1.40.0
```

版本號查 https://github.com/n8n-io/n8n/releases

---

## 常見問題

**Port 5678 被佔用**：改 `ports` 為 `"5679:5678"`，然後打開 http://localhost:5679

**M1/M2 Mac 跑很慢**：正常，n8n image 有 ARM 版，Docker Desktop 會自動選，耐心等第一次啟動

**資料放在哪**：Docker volume `n8n_data`，查路徑用 `docker volume inspect n8n_data`

---

## 自我檢核

- [ ] 能用 `docker compose up -d` 把 n8n 跑起來
- [ ] 打開 http://localhost:5678 能看到 n8n 登入頁
- [ ] 建好 owner 帳號並進入主介面
- [ ] 知道 webhook 為什麼要配 ngrok

下一章看清楚 n8n 介面長什麼樣。

→ [Ch 3 介面導覽 — Canvas、Node、Connection、Execution Log](./03-ui-tour.md)
