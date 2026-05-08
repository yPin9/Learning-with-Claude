# Final Project：完整 CI Pipeline

整合所有章節：Dockerfile hardening（Ch 10–13、21–22）、image 掃描與簽名（Ch 19–20）、Compose 生產設定（Ch 14–17）、監控（Ch 25）、CI/CD（GitHub Actions）

---

## 目標

把練習 B 的 FastAPI + PostgreSQL + Redis + Nginx stack 接上完整的 CI/CD pipeline（持續整合 / 持續交付），讓每次 `git push` 自動完成：

1. 跑 unit test（單元測試）
2. build Docker image
3. trivy 掃描（有 CRITICAL CVE 就失敗，不 push）
4. push 到 GHCR（GitHub Container Registry，GitHub 容器倉庫）
5. cosign 簽名（image provenance，鏡像溯源）
6. SSH 到 server，執行 `docker compose pull && docker compose up -d`

這條 pipeline 可以直接複製到任何專案用。

---

## 架構圖

```
開發者 git push main
         │
         ▼
  ┌─────────────────────────────────────────┐
  │         GitHub Actions                  │
  │                                         │
  │  ┌──────────┐                           │
  │  │  job:    │                           │
  │  │  test    │                           │
  │  └────┬─────┘                           │
  │       │ 通過才繼續                       │
  │  ┌────▼──────────────┐                  │
  │  │  job:             │                  │
  │  │  build-and-scan   │                  │
  │  │  ─ docker buildx  │                  │
  │  │  ─ trivy scan     │  CRITICAL → fail │
  │  │  ─ push to GHCR   │                  │
  │  └────┬──────────────┘                  │
  │       │ 只在 main branch                 │
  │  ┌────▼──────────────┐                  │
  │  │  job:             │                  │
  │  │  deploy           │                  │
  │  │  ─ cosign sign    │                  │
  │  │  ─ SSH to server  │                  │
  │  │  ─ compose up -d  │                  │
  │  └───────────────────┘                  │
  └─────────────────────────────────────────┘
         │
         ▼
  ┌─────────────────────────────────────────┐
  │  Production Server                      │
  │  ─ docker compose pull                  │
  │  ─ docker compose up -d                 │
  │  ─ cAdvisor + Prometheus + Grafana      │
  └─────────────────────────────────────────┘
```

---

## Phase 1：準備 production-ready image

### 目錄結構

從練習 B 的 `myapp/` 出發，擴充成：

```
myapp/
├── app/
│   ├── main.py
│   ├── requirements.txt
│   ├── test_main.py          ← 新增：unit test
│   └── Dockerfile            ← 升級：更嚴格的 hardening
├── nginx/
│   └── nginx.conf
├── monitoring/               ← 新增：監控 stack
│   ├── prometheus.yml
│   └── compose.monitoring.yml
├── compose.yml               ← 升級：用 GHCR image
├── compose.override.yml      ← 本地開發用（build: context）
├── .env.example
├── .dockerignore             ← 新增
└── .github/
    └── workflows/
        └── ci.yml            ← 新增：完整 CI workflow
```

### Dockerfile（最終版）

```dockerfile
# syntax=docker/dockerfile:1

# ── Stage 1: builder ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: production ───────────────────────────────────────────
FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/YOUR_USER/myapp"
LABEL org.opencontainers.image.description="FastAPI demo app"
LABEL org.opencontainers.image.licenses="MIT"

RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/sh -m appuser

WORKDIR /app

COPY --from=builder /install /usr/local

COPY --chown=appuser:appuser app/ .

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["python3", "-c", \
         "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]

CMD ["python3", "-m", "uvicorn", "main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--no-access-log"]
```

### `.dockerignore`

```
.git
.github
.env
.env.*
secrets/
__pycache__
*.pyc
*.pyo
*.egg-info
.pytest_cache
.mypy_cache
.coverage
htmlcov/
.venv
venv
*.log
Dockerfile
Dockerfile.*
docker-compose*.yml
compose*.yml
.dockerignore
README.md
monitoring/
```

### `app/test_main.py`（Unit test）

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

# 用 mock 避免 test 需要真實的 DB 和 Redis
with patch.dict("os.environ", {
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test",
    "POSTGRES_DB": "test",
}):
    # mock asyncpg 和 redis，避免實際連線
    import sys
    sys.modules["asyncpg"] = MagicMock()
    sys.modules["redis"] = MagicMock()
    sys.modules["redis.asyncio"] = MagicMock()

    from main import app

client = TestClient(app)


def test_health():
    """健康檢查 endpoint 應回 {"status": "ok"}"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_items_structure():
    """items endpoint 回傳格式應為 list of {id, name}"""
    mock_rows = [
        {"id": 1, "name": "item-a"},
        {"id": 2, "name": "item-b"},
    ]
    with patch("main.db_pool") as mock_pool, \
         patch("main.redis_client") as mock_redis:

        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock(return_value=True)

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=mock_rows)
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        response = client.get("/items")
        assert response.status_code == 200
        items = response.json()
        assert isinstance(items, list)
        assert len(items) >= 1
```

### 本地掃描（上 CI 之前先跑）

```bash
# 安裝 trivy（macOS/Linux）
# macOS: brew install aquasecurity/trivy/trivy
# Linux: curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh

# build
docker build -t myapp:local ./app

# 掃描
trivy image --severity HIGH,CRITICAL myapp:local
# 如果有 CRITICAL，要換 base image 版本或更新 requirements.txt

# 本地確認 image 大小
docker images myapp:local
# 目標：< 200 MB
```

---

## Phase 2：GitHub Actions workflow

### 前置設定（在 GitHub 設定 Secrets）

在你的 GitHub repo 的 `Settings → Secrets and variables → Actions` 裡新增：

| Secret 名稱 | 說明 |
|---|---|
| `DEPLOY_HOST` | server IP 或 domain（例如 `123.45.67.89`） |
| `DEPLOY_USER` | SSH 登入帳號（例如 `ubuntu`） |
| `DEPLOY_SSH_KEY` | SSH private key 內容（`cat ~/.ssh/id_ed25519`） |
| `DEPLOY_PATH` | server 上 compose 目錄（例如 `/home/ubuntu/myapp`） |

`GITHUB_TOKEN` 是 GitHub 自動提供的，不用手動加。

### `.github/workflows/ci.yml`（完整）

```yaml
name: CI/CD Pipeline

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}/app

jobs:

  # ──────────────────────────────────────────────────────────────
  # Job 1：Unit Test
  # ──────────────────────────────────────────────────────────────
  test:
    name: Unit Test
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
          cache-dependency-path: app/requirements.txt

      - name: Install dependencies
        run: |
          pip install --no-cache-dir \
            -r app/requirements.txt \
            pytest pytest-asyncio

      - name: Run tests
        run: pytest app/test_main.py -v

  # ──────────────────────────────────────────────────────────────
  # Job 2：Build、Scan、Push
  # ──────────────────────────────────────────────────────────────
  build-and-scan:
    name: Build and Scan
    runs-on: ubuntu-latest
    needs: test       # 等 test job 通過
    permissions:
      contents: read
      packages: write   # 允許 push 到 GHCR
      id-token: write   # cosign keyless signing 需要 OIDC token

    outputs:
      image-digest: ${{ steps.build.outputs.digest }}
      image-ref: ${{ steps.build.outputs.imageid }}

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      # 設定 QEMU（跨平台 build 用，可選）
      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3

      # 設定 Docker Buildx（多平台 build、cache 支援）
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      # 登入 GHCR
      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      # 產生 image tag（git SHA + latest）
      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=sha,prefix=sha-,format=short
            type=raw,value=latest,enable=${{ github.ref == 'refs/heads/main' }}
            type=ref,event=pr

      # Build + push（PR 只 build 不 push；push main 才 push）
      - name: Build and push
        id: build
        uses: docker/build-push-action@v6
        with:
          context: ./app
          platforms: linux/amd64,linux/arm64
          push: ${{ github.ref == 'refs/heads/main' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          provenance: true   # 產生 SBOM provenance

      # Trivy 掃描（針對 build 出來的 image）
      - name: Run Trivy vulnerability scanner
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest
          format: table
          exit-code: "1"          # CRITICAL 就讓 job 失敗
          ignore-unfixed: true    # 忽略還沒有修復版本的 CVE
          severity: CRITICAL      # 只有 CRITICAL 才 fail（HIGH 印出但不 fail）
          vuln-type: os,library

      # 把 trivy 結果也存成 SARIF 上傳到 GitHub Security tab
      - name: Trivy SARIF upload
        uses: aquasecurity/trivy-action@master
        if: always()    # 即使前一步失敗也跑，確保 report 上傳
        with:
          image-ref: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest
          format: sarif
          output: trivy-results.sarif
          severity: HIGH,CRITICAL

      - name: Upload Trivy SARIF to GitHub Security
        uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: trivy-results.sarif

  # ──────────────────────────────────────────────────────────────
  # Job 3：Deploy（只在 push main 時跑）
  # ──────────────────────────────────────────────────────────────
  deploy:
    name: Deploy to Production
    runs-on: ubuntu-latest
    needs: build-and-scan
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'
    permissions:
      contents: read
      packages: read
      id-token: write   # cosign OIDC

    environment:
      name: production
      url: http://${{ secrets.DEPLOY_HOST }}

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      # 安裝 cosign（容器簽名工具）
      - name: Install cosign
        uses: sigstore/cosign-installer@v3

      # Keyless signing（無密鑰簽名）：用 GitHub Actions OIDC token 作為身份
      # 不需要管理 private key，身份綁定在 GitHub Actions job 上
      - name: Sign the image with cosign (keyless)
        run: |
          cosign sign --yes \
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}@${{ needs.build-and-scan.outputs.image-digest }}
        env:
          COSIGN_EXPERIMENTAL: 1

      # SSH 到 server 執行 deploy 腳本
      - name: Deploy to server
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.DEPLOY_HOST }}
          username: ${{ secrets.DEPLOY_USER }}
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: |
            set -e
            cd ${{ secrets.DEPLOY_PATH }}

            # 登入 GHCR（用機器帳號 token，或設定 server 上的 GITHUB_TOKEN）
            echo ${{ secrets.GITHUB_TOKEN }} | \
              docker login ghcr.io -u ${{ github.actor }} --password-stdin

            # Pull 最新 image
            docker compose pull app

            # 滾動更新，不中斷服務
            docker compose up -d --no-build

            # 確認健康
            sleep 10
            docker compose ps

            # 清理舊 image（保留最近 3 個）
            docker image prune -f
```

---

## Phase 3：Server 端設定

### Production `compose.yml`

```yaml
# compose.yml（server 上的版本）
# image 指向 GHCR，不在 server 上 build

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
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 15s
    security_opt:
      - no-new-privileges:true
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
    security_opt:
      - no-new-privileges:true
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"

  app:
    image: ghcr.io/YOUR_USER/myapp/app:latest
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
    security_opt:
      - no-new-privileges:true
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
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
    security_opt:
      - no-new-privileges:true
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"

volumes:
  db_data:
```

### 本地開發用 `compose.override.yml`

```yaml
# compose.override.yml（本地開發，自動 build，不 push）
# docker compose up 會自動合併這個 override

services:
  app:
    build:
      context: ./app
      dockerfile: Dockerfile
    image: myapp-app:dev
```

開發時跑 `docker compose up`，自動用 build 版本；production server 上只有 `compose.yml`，用 GHCR image。

### Deploy 腳本（server 上備用）

```bash
#!/bin/bash
# deploy.sh（在 server 上手動執行用）
set -euo pipefail

COMPOSE_DIR=/home/ubuntu/myapp
REGISTRY=ghcr.io
IMAGE=ghcr.io/YOUR_USER/myapp/app:latest

cd "$COMPOSE_DIR"

echo "[1] 登入 GHCR..."
# 假設 server 上有設 GITHUB_TOKEN 環境變數
echo "${GITHUB_TOKEN}" | docker login ghcr.io -u YOUR_USER --password-stdin

echo "[2] Pull 最新 image..."
docker compose pull app

echo "[3] 驗證 cosign 簽名..."
cosign verify \
  --certificate-identity-regexp "https://github.com/YOUR_USER/myapp/.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  "$IMAGE"
echo "    簽名驗證通過"

echo "[4] 滾動更新..."
docker compose up -d --no-build

echo "[5] 確認狀態..."
sleep 10
docker compose ps

echo "[6] 清理舊 image..."
docker image prune -f

echo "Deploy 完成。"
```

---

## 監控 Stack（cAdvisor + Prometheus + Grafana）

### `monitoring/compose.monitoring.yml`

```yaml
name: myapp-monitoring

services:

  cadvisor:
    image: gcr.io/cadvisor/cadvisor:latest
    restart: unless-stopped
    volumes:
      - /:/rootfs:ro
      - /var/run:/var/run:ro
      - /sys:/sys:ro
      - /var/lib/docker/:/var/lib/docker:ro
    ports:
      - "8080:8080"
    privileged: true     # cAdvisor 需要讀 cgroup 資訊

  prometheus:
    image: prom/prometheus:latest
    restart: unless-stopped
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    ports:
      - "9090:9090"
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.retention.time=15d"

  grafana:
    image: grafana/grafana:latest
    restart: unless-stopped
    volumes:
      - grafana_data:/var/lib/grafana
    ports:
      - "3000:3000"
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD:-admin}

volumes:
  prometheus_data:
  grafana_data:
```

### `monitoring/prometheus.yml`

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: cadvisor
    static_configs:
      - targets: ["cadvisor:8080"]

  - job_name: myapp
    static_configs:
      - targets: ["app:8000"]    # 如果 FastAPI 有 /metrics endpoint
```

啟動監控：

```bash
docker compose -f compose.yml -f monitoring/compose.monitoring.yml up -d
```

Grafana 在 `http://server:3000`，預設帳號 `admin`/`admin`，import dashboard ID `11600`（cAdvisor）或 `13240`。

---

## 驗收標準

| 檢查項目 | 驗證方式 | 通過條件 |
|---|---|---|
| PR 觸發 test | 開 PR，看 GitHub Actions tab | tests 全部綠燈才能 merge |
| push main 觸發 build | push 到 main，看 Actions | image 成功出現在 GHCR packages 頁 |
| trivy 有 CRITICAL | 在 `requirements.txt` 故意裝有洞的版本測試 | workflow 失敗，image 不 push |
| cosign 簽名 | `cosign verify ... ghcr.io/YOUR_USER/myapp/app:latest` | 驗證通過 |
| deploy 生效 | SSH 到 server，`docker compose ps` | 顯示新 image SHA，service 全 healthy |
| 服務不中斷 | deploy 期間用 `while true; do curl -s localhost/health; sleep 1; done` 監控 | 無 error 回應 |

### cosign 驗證命令

```bash
# 在任何機器上都能驗證 image 是從你的 repo Actions 簽出來的
cosign verify \
  --certificate-identity-regexp "https://github.com/YOUR_USER/myapp/.github/workflows/ci.yml@refs/heads/main" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/YOUR_USER/myapp/app:latest

# 輸出類似：
# Verification for ghcr.io/YOUR_USER/myapp/app:latest --
# The following checks were performed on each of these signatures:
#   - The cosign claims were validated
#   - Existence of the claims in the transparency log was verified offline
#   - The code-signing certificate claims were validated
```

---

## 整合測試腳本

```bash
#!/bin/bash
# integration_test.sh
# 在 server 上跑，驗證整條 pipeline 是否正常
set -euo pipefail

BASE_URL=http://localhost

echo "=== 整合測試開始 ==="

echo "[T1] /health..."
RESP=$(curl -sf "$BASE_URL/health")
echo "$RESP" | python3 -c "import json,sys; assert json.load(sys.stdin)['status']=='ok'"
echo "    PASS"

echo "[T2] /items..."
RESP=$(curl -sf "$BASE_URL/items")
echo "$RESP" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert isinstance(d, list) and len(d) >= 2
names = {i['name'] for i in d}
assert 'item-a' in names
"
echo "    PASS"

echo "[T3] 所有 container 是 Up 狀態..."
UNHEALTHY=$(docker compose ps --format json 2>/dev/null \
  | python3 -c "
import json, sys
count = 0
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    d = json.loads(line)
    state = d.get('State', '')
    if state not in ('running',):
        print(f'  NOT running: {d[\"Name\"]} ({state})', file=sys.stderr)
        count += 1
print(count)
")
[ "$UNHEALTHY" = "0" ]
echo "    PASS（全部 running）"

echo "[T4] app 非 root user..."
UID_OUT=$(docker compose exec app id -u)
[ "$UID_OUT" != "0" ]
echo "    PASS（uid=$UID_OUT）"

echo "[T5] Redis cache 有寫入..."
docker compose exec cache redis-cli get items | grep -q "item"
echo "    PASS（cache hit）"

echo "[T6] cosign 簽名驗證..."
IMAGE=$(docker compose images --format json app 2>/dev/null \
  | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['Image'])" 2>/dev/null \
  || docker compose ps --format json \
  | python3 -c "
import json, sys
for line in sys.stdin:
    d = json.loads(line.strip())
    if 'app' in d.get('Name', ''):
        print(d.get('Image', ''))
        break
")

if echo "$IMAGE" | grep -q "ghcr.io"; then
  cosign verify \
    --certificate-identity-regexp "https://github.com/.*" \
    --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
    "$IMAGE" > /dev/null 2>&1
  echo "    PASS（cosign 驗證通過）"
else
  echo "    SKIP（本地 build image，不驗 cosign）"
fi

echo ""
echo "=== 所有測試通過 ==="
```

---

## 常見問題排查

**GHCR push 失敗：403 Forbidden**

- 確認 workflow 的 `permissions` 有 `packages: write`
- 確認 `GITHUB_TOKEN` 有 write packages 權限（repo Settings → Actions → General → Workflow permissions）

**trivy scan 找不到 image**

- `push: ${{ github.ref == 'refs/heads/main' }}` 在 PR 時是 false，沒有 push 到 registry
- 在 PR 跑 scan 時，改成掃 `docker.io/library/myapp:test` 之類的 local image，或用 `--input` 掃 tar 檔

**cosign sign 失敗：OIDC token 問題**

- 確認 job 的 `permissions` 有 `id-token: write`
- keyless signing 需要 GitHub Actions 環境，本地跑不了

**deploy job SSH 超時**

- 確認 `DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_SSH_KEY` 都有設
- server 的防火牆要開 22 port 給 GitHub Actions IP ranges
- GitHub Actions IP 範圍：`curl -s https://api.github.com/meta | jq .actions`

**rolling update 時服務短暫 502**

- Nginx 的 `proxy_read_timeout` 調高
- 或加 upstream 的 `max_fails` 和 `fail_timeout`：
  ```nginx
  upstream app_backend {
      server app:8000 max_fails=3 fail_timeout=10s;
  }
  ```

---

## 自我檢核

- [ ] GitHub Actions workflow 跑通，三個 job 全部綠燈
- [ ] GHCR packages 頁面能看到 image，有 `latest` 和 `sha-xxxxxx` tag
- [ ] `docker history ghcr.io/YOUR_USER/myapp/app:latest` 看不到任何 secret
- [ ] `trivy image` 本地掃描通過（無 CRITICAL）
- [ ] `cosign verify` 指令跑通
- [ ] server 上 `docker compose ps` 全部 `Up (healthy)`
- [ ] deploy 期間用 `curl` 監控沒有出現 error
- [ ] Grafana dashboard 能看到 container 的 CPU / memory 曲線

---

上一個練習：[練習 C：Dockerfile 資安審查](./practice-c-security-audit.md)
