# Ch 24 — 日誌管理

> 目標：理解 Docker log driver 架構，設定 json-file log rotation 避免磁碟爆炸，並能整合 fluentd 把 log 集中送到後端。

## Docker log driver 架構

容器裡 process 寫到 `stdout` 和 `stderr` 的所有輸出，都被 Docker 的 log driver 接走：

```
container process
    |
    | stdout / stderr
    v
Docker log driver
    |
    +-- json-file  -> /var/lib/docker/containers/<id>/<id>-json.log
    +-- syslog     -> syslog daemon（/var/log/syslog）
    +-- journald   -> systemd journal
    +-- fluentd    -> fluentd daemon -> 後端（ES / Loki / S3）
    +-- awslogs    -> AWS CloudWatch Logs
    +-- none       -> 直接丟棄（benchmark 或不需要 log 的場景）
```

`docker logs <container>` 這個指令，背後讀的是 log driver 的輸出。如果用了 `syslog` 或 `fluentd`，`docker logs` 就看不到了（log 已經送走了）。

## Log Driver 比較

| Driver | 儲存位置 | `docker logs` 可用 | 適用場景 |
|--------|----------|-------------------|----------|
| `json-file` | 本機 JSON 檔 | 是 | 預設，本地開發 / 小型生產 |
| `local` | 本機二進位格式 | 是 | 比 json-file 節省空間 |
| `syslog` | syslog daemon | 否 | 集中到現有 syslog 基礎設施 |
| `journald` | systemd journal | 否 | systemd 主機 |
| `fluentd` | fluentd daemon | 否 | 大型集中式 log pipeline |
| `awslogs` | CloudWatch Logs | 否 | AWS ECS / EC2 |
| `gcplogs` | GCP Cloud Logging | 否 | Google Cloud |
| `none` | 丟棄 | 否 | 完全不需要 log |

## json-file：預設 driver 的設定

不設定的話，每個容器的 log 檔會無限成長直到磁碟爆掉。這不是理論，是生產環境的常見事故。

### 全域設定（daemon.json）

影響這台機器上所有容器的預設行為：

```json
// /etc/docker/daemon.json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  }
}
```

套用設定：

```bash
sudo systemctl reload docker
# 注意：只影響新建立的容器，已跑的容器不受影響
```

`max-size` + `max-file` 的儲存上限計算：`50m * 3 = 150MB / 容器`。

### 單一容器設定（docker run）

```bash
docker run -d \
  --log-driver json-file \
  --log-opt max-size=50m \
  --log-opt max-file=3 \
  nginx:alpine
```

### Compose 設定

```yaml
services:
  app:
    image: myapp:latest
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "3"

  db:
    image: postgres:16-alpine
    logging:
      driver: json-file
      options:
        max-size: "100m"   # DB log 通常較多
        max-file: "5"
```

### 查看 log 檔案位置

```bash
# 找到 container ID
docker ps --format "{{.ID}} {{.Names}}"

# 查 log 檔案位置
docker inspect <container_id> | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(d[0]['LogPath'])"

# 直接讀原始 JSON log
sudo cat /var/lib/docker/containers/<id>/<id>-json.log | head -5
# 每一行是一個 JSON 物件：{"log":"...\n","stream":"stdout","time":"..."}
```

## 常用 debug 指令

```bash
# 最近 100 行，持續追蹤
docker logs --tail 100 -f mycontainer

# 最近 1 小時的 log
docker logs --since 1h mycontainer

# 特定時間範圍
docker logs --since "2024-01-15T10:00:00" --until "2024-01-15T11:00:00" mycontainer

# 只看 stderr
docker logs mycontainer 2>&1 1>/dev/null

# 同時看多個容器（用 compose）
docker compose logs -f app db

# 加 timestamp
docker logs -t --tail 50 mycontainer
```

## Structured Logging：讓 log 可以 parse

app 直接輸出純文字 log，日後要 parse 很痛苦。改成輸出 JSON：

**Python（使用 structlog）：**

```python
import structlog
import logging

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ]
)

log = structlog.get_logger()
log.info("request_handled", method="GET", path="/api/users", status=200, duration_ms=42)
```

輸出：

```json
{"event": "request_handled", "method": "GET", "path": "/api/users", "status": 200, "duration_ms": 42, "timestamp": "2024-01-15T10:30:00Z"}
```

這樣的 log 進 Elasticsearch 或 Loki 之後，可以直接按 `status` 欄位 filter，不需要 regex parse。

## fluentd 集中式 log

fluentd（流式資料收集器）可以接收 Docker log，做 transform，再轉發到 Elasticsearch、Loki、S3 等後端。

```
container stdout/stderr
    |
    v (Docker fluentd driver)
fluentd daemon (port 24224)
    |
    +---> Elasticsearch / OpenSearch（搜尋）
    +---> Loki（Grafana 的 log 後端）
    +---> AWS S3（長期保存）
```

### 最小可跑的 fluentd 設定

`fluentd.conf`：

```
<source>
  @type forward
  port 24224
  bind 0.0.0.0
</source>

<match docker.**>
  @type stdout
</match>
```

`compose.yml`：

```yaml
services:
  fluentd:
    image: fluent/fluentd:v1.16-debian-1
    volumes:
      - ./fluentd.conf:/fluentd/etc/fluentd.conf
    ports:
      - "24224:24224"

  app:
    image: myapp:latest
    logging:
      driver: fluentd
      options:
        fluentd-address: "localhost:24224"
        tag: "docker.myapp"
    depends_on:
      - fluentd
```

### fluentd log driver 的注意事項

用了 `fluentd` driver 後，`docker logs` **看不到輸出**，因為 log 已經送到 fluentd 了。這在 debug 時要記得。

保留 `docker logs` 能力的做法：用 `fluentd` 但同時設定 local file：

```
# fluentd.conf 同時輸出到 stdout 和檔案
<match docker.**>
  @type copy
  <store>
    @type stdout
  </store>
  <store>
    @type file
    path /fluentd/log/docker
    append true
  </store>
</match>
```

## log 磁碟使用量監控

```bash
# 查看所有容器的 log 大小
du -sh /var/lib/docker/containers/*/*-json.log | sort -h

# 單一容器
docker inspect <id> -f '{{.LogPath}}' | xargs du -sh

# 超過 100MB 的 log 檔
find /var/lib/docker/containers -name '*-json.log' -size +100M -exec ls -lh {} \;
```

## 自我檢核

- [ ] 能說明 Docker log driver 在架構中的位置（container stdout 到最終儲存的流程）
- [ ] 知道不設 log rotation 的後果，並能在 daemon.json 和 Compose 都設定
- [ ] 能計算 `max-size * max-file` 的最大磁碟用量
- [ ] 知道用了 fluentd / syslog driver 後 `docker logs` 為什麼看不到輸出
- [ ] 能解釋 structured JSON log 為什麼比純文字 log 更好 parse
- [ ] 能用 `docker logs --since` / `--tail` / `-f` 做基本 debug

Part 7 開頭的 log 設定做完了，下一章進 metrics 監控——知道 log 之後，還需要知道數字。

→ [Ch 25 監控與指標](./25-monitoring.md)
