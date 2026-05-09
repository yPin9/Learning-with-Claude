# Ch 23 — 監控、日誌、備份與版本升級

> 目標：讓 n8n 在生產環境穩定跑，出問題時能快速發現，能無縫升級版本，能在災難後還原。

## 監控 n8n 健康狀態

### Health Check Endpoint

n8n 提供一個 health check API：

```bash
curl http://localhost:5678/healthz
# 回傳：{"status": "ok"}
```

把這個接到你的監控工具（UptimeRobot、Grafana、Datadog）：

```bash
# UptimeRobot 設定
Monitor Type: HTTP(s)
URL: https://n8n.yourdomain.com/healthz
Check Interval: 5 minutes
```

掛了就發通知（email / SMS）。

### 自製監控 Workflow

n8n 監控自己：建一個 workflow，每 5 分鐘打一次 httpbin（確認 n8n 還活著、能發 HTTP 請求）：

```
[Schedule: */5 * * * *]
       │
[HTTP Request: GET https://httpbin.org/get]
       │（失敗觸發 Error Trigger）
[Telegram: 已確認正常運作，時間：{{ new Date().toLocaleString('zh-TW') }}]
```

（這個 workflow 只在 Telegram 收不到訊息時才知道有問題，但能發現 n8n 服務本身掛掉的情況。）

---

## 查看日誌

### Docker Logs

```bash
# 即時查看 n8n log
docker compose logs -f n8n

# 查看最後 100 行
docker compose logs --tail=100 n8n

# 查看 Postgres log
docker compose logs -f postgres
```

### 調整 Log Level

```bash
# .env
N8N_LOG_LEVEL=info    # debug / info / warn / error
N8N_LOG_OUTPUT=console  # console / file
N8N_LOG_FILE_LOCATION=/home/node/.n8n/logs/n8n.log
```

`debug` 模式輸出非常多，只在除錯時打開。

---

## Execution 歷史清理

Execution 歷史會一直長大。設定自動清理：

```bash
# .env
EXECUTIONS_DATA_PRUNE=true
EXECUTIONS_DATA_MAX_AGE=168      # 168 小時 = 7 天
EXECUTIONS_DATA_PRUNE_MAX_COUNT=5000
```

或手動清理（n8n 介面）：Settings → Executions → Clear Executions。

---

## 備份策略

### 備份什麼

| 資料 | 在哪 | 重要性 |
|---|---|---|
| Workflow 定義 | Postgres DB | ⭐⭐⭐ |
| Credential（加密） | Postgres DB | ⭐⭐⭐ |
| Execution 歷史 | Postgres DB | ⭐⭐（可重建）|
| Binary 檔案 | n8n_data volume | 視需求 |
| N8N_ENCRYPTION_KEY | .env | ⭐⭐⭐（單獨備份）|

### Postgres 備份腳本

```bash
#!/bin/bash
# backup.sh

BACKUP_DIR="/root/n8n-production/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/n8n_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

docker exec n8n-postgres pg_dump \
  -U n8n_user \
  -d n8n \
  | gzip > "$BACKUP_FILE"

# 保留最近 30 天
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +30 -delete

echo "備份完成：$BACKUP_FILE"
```

設定 cron 每天跑：

```bash
crontab -e
# 加入：
0 4 * * * /root/n8n-production/backup.sh >> /var/log/n8n-backup.log 2>&1
```

### 備份 workflow 為 JSON（輕量備份）

n8n 介面 → 每個 workflow → 右上角「...」→ Export → 下載 JSON。

或用 n8n API 批次匯出：

```bash
curl -X GET https://n8n.yourdomain.com/api/v1/workflows \
  -H "X-N8N-API-KEY: your-api-key" \
  > workflows_backup.json
```

n8n API key 在介面 Settings → n8n API → Create API Key。

### 還原備份

```bash
# 還原 Postgres
gunzip -c backups/n8n_20260508_040000.sql.gz | \
  docker exec -i n8n-postgres psql -U n8n_user -d n8n

# 重啟 n8n（讓它重新讀取 DB）
docker compose restart n8n
```

---

## 版本升級

n8n 更新頻繁（差不多每週一個小版本）。升級前：

1. 查 [n8n release notes](https://github.com/n8n-io/n8n/releases) 有沒有 breaking change
2. **先備份資料庫**
3. 測試環境先升（如果你有）

升級指令：

```bash
cd ~/n8n-production

# 備份
./backup.sh

# 拉新版 image
docker compose pull n8n

# 重啟（n8n 啟動時會自動執行 DB migration）
docker compose up -d n8n

# 確認運作正常
docker compose ps
docker compose logs --tail=50 n8n
```

如果升級後出問題（罕見但有可能）：

```bash
# 回滾到上一版
docker compose stop n8n

# 還原 DB
./restore.sh backups/n8n_pre_upgrade.sql.gz

# 指定舊版本重啟
# 先改 docker-compose.yml image 為舊版號
docker compose up -d n8n
```

---

## 常見問題排查

| 症狀 | 查哪裡 | 可能原因 |
|---|---|---|
| Workflow 沒跑 | Executions 頁面 | 啟用開關關了、trigger 時間設錯 |
| 全部 workflow 不動 | `docker logs n8n` | n8n container 掛了、DB 連線失敗 |
| 某個 node 一直失敗 | 點 node 看錯誤訊息 | credential 過期、API 回傳格式變了 |
| Execution 很慢 | DB 大小、`pg_stat_activity` | execution 歷史沒清理、DB 需要 VACUUM |

---

## 自我檢核

- [ ] 知道 `/healthz` 端點用途，能接到外部監控
- [ ] 能寫備份腳本並設 cron 定期跑
- [ ] 知道備份時除了 DB 還要備份什麼（ENCRYPTION_KEY）
- [ ] 能執行 n8n 版本升級（pull → up -d）並知道失敗時如何回滾

Part 5 結束。最後兩章把 AI 接進來。

→ [Ch 24 AI Agent Node — 把 LLM 接進 Workflow](./24-ai-agent-node.md)
