# Ch 25 — 監控與指標

> 目標：從 `docker stats` 出發，搭建 cAdvisor + Prometheus + Grafana 完整監控 stack，理解關鍵 container metrics 的含義，並設定記憶體超限告警。

## docker stats：最快的即時快照

不需要任何額外工具，直接看容器的資源用量：

```bash
# 即時顯示所有容器（每秒更新）
docker stats

# 只看特定容器
docker stats myapp db

# 顯示一次後退出（適合在腳本裡用）
docker stats --no-stream

# 格式化輸出（適合 monitoring script）
docker stats --no-stream --format \
  "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}"
```

輸出欄位說明：

| 欄位 | 含義 | 注意事項 |
|------|------|----------|
| `CPU %` | 相對於總 CPU 的使用率 | 4 核心機器，單 container 上限 400% |
| `MEM USAGE / LIMIT` | 目前用量 / cgroup 限制 | 沒設限制時顯示 host 總記憶體 |
| `MEM %` | 記憶體使用百分比 | 超過 limit 會被 OOMKill |
| `NET I/O` | 累計網路收發 | 從容器啟動後的累計值 |
| `BLOCK I/O` | 累計磁碟讀寫 | 同上 |
| `PIDS` | 容器內的 process 數量 | 超過 `pids-limit` 會無法 fork |

`docker stats` 的問題：沒有歷史記錄，重新整理就不見了。生產環境需要 time-series 資料。

## Docker Daemon Metrics Endpoint

Docker daemon 可以暴露 Prometheus 格式的 metrics：

```json
// /etc/docker/daemon.json
{
  "metrics-addr": "127.0.0.1:9323",
  "experimental": true
}
```

```bash
sudo systemctl reload docker

# 看看有什麼 metrics
curl -s localhost:9323/metrics | head -40
```

這個 endpoint 主要是 daemon 本身的 metrics（image pull 次數、API 請求數等），不是每個容器的資源用量。容器級別的 metrics 要靠 cAdvisor。

## cAdvisor：Container 資源 Metrics 標準工具

cAdvisor（Container Advisor）是 Google 出品的 container 監控 agent，每隔幾秒讀取 cgroup 資訊，把每個容器的 CPU、記憶體、網路、I/O metrics 以 Prometheus 格式暴露出來。

```yaml
# compose.yml（cAdvisor 單獨跑）
services:
  cadvisor:
    image: gcr.io/cadvisor/cadvisor:v0.47.0
    container_name: cadvisor
    privileged: true           # 需要讀 /sys/fs/cgroup
    devices:
      - /dev/kmsg
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - /var/lib/docker/:/var/lib/docker:ro
      - /dev/disk/:/dev/disk:ro
    ports:
      - "8080:8080"
    restart: unless-stopped
```

```bash
docker compose up -d cadvisor

# 看 cAdvisor 的 metrics
curl -s localhost:8080/metrics | grep container_cpu_usage
```

## Prometheus + Grafana + cAdvisor 完整 Stack

```
container  -->  cAdvisor (:8080/metrics)  <-- scrape --  Prometheus
                                                               |
                                                           Grafana
                                                          (dashboard)
```

### 目錄結構

```
monitoring/
├── compose.yml
├── prometheus/
│   └── prometheus.yml
└── grafana/
    └── provisioning/
        └── datasources/
            └── prometheus.yml
```

### prometheus/prometheus.yml

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'cadvisor'
    static_configs:
      - targets: ['cadvisor:8080']

  - job_name: 'docker'
    static_configs:
      - targets: ['host.docker.internal:9323']  # daemon metrics
```

### grafana/provisioning/datasources/prometheus.yml

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

### compose.yml 完整版

```yaml
services:
  cadvisor:
    image: gcr.io/cadvisor/cadvisor:v0.47.0
    container_name: cadvisor
    privileged: true
    devices:
      - /dev/kmsg
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - /var/lib/docker/:/var/lib/docker:ro
    expose:
      - "8080"

  prometheus:
    image: prom/prometheus:v2.48.0
    container_name: prometheus
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.retention.time=15d'
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:10.2.0
    container_name: grafana
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
      - grafana_data:/var/lib/grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin123
      - GF_USERS_ALLOW_SIGN_UP=false

volumes:
  prometheus_data:
  grafana_data:
```

```bash
cd monitoring
docker compose up -d

# 等 30 秒讓 cAdvisor 收集到資料
curl -s "http://localhost:9090/api/v1/query?query=container_cpu_usage_seconds_total" \
  | python3 -m json.tool | head -30
```

### Grafana Dashboard

開啟 `http://localhost:3000`（admin / admin123），匯入現成的 cAdvisor dashboard：

1. 左側選 Dashboards -> Import
2. 輸入 Dashboard ID：`193`（cAdvisor 官方 dashboard）
3. 選擇 Prometheus 作為 data source
4. Import

立刻看到所有容器的 CPU、記憶體、網路趨勢圖。

## 關鍵 Metrics

| Metric 名稱 | 類型 | 含義 |
|-------------|------|------|
| `container_cpu_usage_seconds_total` | counter | 累計 CPU 時間（需算 rate） |
| `container_memory_usage_bytes` | gauge | 目前記憶體用量 |
| `container_memory_limit_bytes` | gauge | cgroup 記憶體限制 |
| `container_network_receive_bytes_total` | counter | 累計網路接收 bytes |
| `container_network_transmit_bytes_total` | counter | 累計網路發送 bytes |
| `container_fs_reads_bytes_total` | counter | 累計磁碟讀取 bytes |
| `container_oomkill_total` | counter | 被 OOMKill 的次數 |

在 Prometheus 查詢語言（PromQL）裡用法：

```promql
# CPU 使用率（5 分鐘平均）
rate(container_cpu_usage_seconds_total{name="myapp"}[5m]) * 100

# 記憶體使用百分比
container_memory_usage_bytes{name="myapp"} /
container_memory_limit_bytes{name="myapp"} * 100

# 網路接收速率（bytes/s）
rate(container_network_receive_bytes_total{name="myapp"}[5m])
```

## Alert 範例：記憶體超過 Limit 的 80% 就告警

在 `prometheus/alerts.yml` 新增：

```yaml
groups:
  - name: container_alerts
    rules:
      - alert: ContainerHighMemory
        expr: |
          container_memory_usage_bytes{name!=""}
          /
          container_memory_limit_bytes{name!=""}
          > 0.8
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Container {{ $labels.name }} high memory"
          description: "Memory usage is {{ $value | humanizePercentage }} of limit"

      - alert: ContainerOOMKilled
        expr: increase(container_oomkill_total[5m]) > 0
        for: 0m
        labels:
          severity: critical
        annotations:
          summary: "Container {{ $labels.name }} was OOM killed"
```

在 `prometheus.yml` 裡引用：

```yaml
rule_files:
  - /etc/prometheus/alerts.yml
```

告警觸發後可以接 Alertmanager 發 Slack / Email / PagerDuty 通知，這裡不展開。

## 自我檢核

- [ ] 能解釋 `docker stats` 各欄位的含義，特別是 CPU % 在多核機器的計算方式
- [ ] 知道 Docker daemon metrics endpoint 和 cAdvisor 的差別（daemon 層 vs container 層）
- [ ] 能從頭搭起 cAdvisor + Prometheus + Grafana compose stack
- [ ] 理解 Prometheus counter 和 gauge 的差別，知道為什麼 CPU 要用 `rate()`
- [ ] 能寫一條 PromQL 查詢容器記憶體使用百分比
- [ ] 知道 `container_oomkill_total` 增加代表什麼，以及怎麼避免

資源監控到位了，下一章進 Swarm——同一台機器跑多個副本、rolling update、secrets 管理。

→ [Ch 26 Docker Swarm 入門](./26-swarm.md)
