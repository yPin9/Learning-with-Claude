# Ch 17 — Dependency-Track 營運

> **目標**：理解 Dependency-Track 補了 CLI 掃描哪個本質缺口（時間維度）；學會用 docker compose 起它、上傳 SBOM、設定 policy；把「一次性掃描」升級成「持續監控」。

## 為什麼需要這個？

到目前為止你做的事都是**時間點快照**：在 CI 生成 SBOM、跑 grype 掃描、找到漏洞、決定修不修。這有一個根本缺陷：

> **今天沒有 CVE，不代表明天還沒有。**

你的 requirements.txt 裡的套件版本沒有變，但漏洞資料庫每天都在更新。三個月後，你上個月 deploy 出去的版本可能多了 5 個 Critical CVE。但因為 CI 只在 build time 掃，你不會知道——除非你重新觸發 build，或者有人一直記得要手動重掃。

Dependency-Track（OWASP）解決的正是這個問題：它是一個**持續監控 server**。你把 SBOM 上傳進去建立一個「project」，它持續訂閱最新的漏洞情報，每當有新的 CVE 命中你的任何元件，就發告警。

```
                    今天            一個月後            三個月後
                     │                │                  │
  你的 SBOM ─────────▼────────────────▼──────────────────▼
  (固定)         0 CVE            +3 CVE              +8 CVE
                                   ↑                   ↑
                              NVD 新發布           NVD 新發布
                              GHSA 新增            GHSA 新增

  只靠 CI 掃描：
  build time 掃了一次，之後不知道              ← 盲點

  Dependency-Track：
  SBOM 上傳後持續監控，CVE 增加就告警          ← 有時間維度
```

這就是 CLI 工具（grype/trivy）和 Dependency-Track 的根本差異：**前者是時間點工具，後者是時間維度的監控平台**。

## 先建立直覺：Dependency-Track 的架構

```
  使用者/CI                Dependency-Track               外部資料源
  ─────────────────────────────────────────────────────────────────
  上傳 SBOM                ┌────────────────┐
  (CycloneDX/SPDX)  ───── ▶│  API Server    │ ◀─── NVD feed
                           │  (port 8081)   │ ◀─── GHSA feed
  查詢結果     ◀─────────── │  Java/Alpine   │ ◀─── OSS Index
  設定 policy              │                │ ◀─── Sonatype OSS
  收 webhook 告警          └───────┬────────┘      VulnDB 等
                                  │
                                  │ 持續比對
                                  ▼
                           ┌────────────────┐
                           │  H2 / PG / DB  │ ← SBOM 元件、漏洞記錄
                           └────────────────┘
                                  ▲
  瀏覽器 ──────────────────── ┌───┴────────────┐
  (port 8080)                │  Frontend      │
                              │  (nginx+Vue)   │
                              └────────────────┘
```

**API Server**：核心，接 SBOM 上傳、做 component intelligence（把元件比對漏洞 DB）、處理 policy、發 notification。
**Frontend**：一個 Vue.js 的 Web UI，API Server 的圖形化介面。
**DB**：預設用內嵌的 H2（適合開發/小規模），生產環境換 PostgreSQL。

Dependency-Track 的「component intelligence」不只是一次性比對——它有內部的排程器，會定期從 NVD、GHSA、OSS Index 等來源同步最新的漏洞情報，再回頭比對所有已上傳的 SBOM。

## 起 Dependency-Track：docker compose

> **環境說明**：這一節需要能跑 docker compose 的環境（Docker Desktop + WSL integration 開啟，或直接在有 Docker 的 Linux 上跑）。本課程的 WSL 環境 Docker daemon 不可用，以下步驟展示官方流程並標注哪些是未實測的。

### 方法一：官方 bundle image（最簡單）

Dependency-Track 提供一個 bundled image，把 API server 和 frontend 打包成一個容器：

```bash
# 下載官方 docker-compose.yml
curl -LO https://dependencytrack.org/docker-compose.yml

# 起服務
docker compose up -d

# 等待初始化（首次約 2-5 分鐘可登入 UI；完整漏洞 DB 同步另需 10-30 分鐘，見下方踩雷）
docker compose logs -f dtrack-apiserver | grep "initialization"
```

預設埠：
- Frontend（Web UI）：`http://localhost:8080`
- API Server：`http://localhost:8081`

### 方法二：拆開 API + Frontend

```yaml
# docker-compose.yml（簡化版）
version: '3'
services:
  dtrack-apiserver:
    image: dependencytrack/apiserver:latest
    ports:
      - "8081:8080"
    environment:
      ALPINE_DATABASE_MODE: embedded
    volumes:
      - dtrack-data:/data
    restart: unless-stopped

  dtrack-frontend:
    image: dependencytrack/frontend:latest
    ports:
      - "8080:8080"
    environment:
      API_BASE_URL: "http://localhost:8081"
    restart: unless-stopped

volumes:
  dtrack-data:
```

**初始登入**：預設帳號 `admin` / 密碼 `admin`，第一次登入強制改密碼。

> **未實測**（本環境 Docker daemon 不可用）：以上 docker compose 步驟和後續 UI 截圖均未在本環境真跑。指令語法來自官方文件，在有正常 Docker Desktop 的環境應可直接執行。

## 設定 API Key 與上傳 SBOM

### 取得 API Key（UI 操作）

1. 登入後，進入 **Administration → Access Management → Teams**
2. 建立一個 team（例如 `CI-Pipeline`）
3. 在 team 裡點 **+ API Key**，複製顯示的 key（只顯示一次）

### 建立 Project

```bash
# 建立 project，取得 projectUuid
curl -s -X "PUT" "http://localhost:8081/api/v1/project" \
  -H 'Content-Type: application/json' \
  -H 'X-Api-Key: YOUR_API_KEY' \
  -d '{"name": "my-python-app", "version": "1.0.0"}' | jq '.uuid'
```

### 上傳 SBOM（PUT + base64）

```bash
# 把 SBOM 轉成 base64
BOM_B64=$(base64 --wrap=0 < sbom.cdx.json)

# 上傳
curl -s -X "PUT" "http://localhost:8081/api/v1/bom" \
  -H 'Content-Type: application/json' \
  -H 'X-Api-Key: YOUR_API_KEY' \
  -d "{
    \"project\": \"YOUR_PROJECT_UUID\",
    \"bom\": \"${BOM_B64}\"
  }"
```

### 上傳 SBOM（POST + multipart，更直觀）

```bash
curl -s -X "POST" "http://localhost:8081/api/v1/bom" \
  -H 'X-Api-Key: YOUR_API_KEY' \
  -F "projectName=my-python-app" \
  -F "projectVersion=1.0.0" \
  -F "autoCreate=true" \
  -F "bom=@/tmp/vuln-demo/sbom.cdx.json;type=application/json"
```

`autoCreate=true` 讓它自動建立 project（不需要先建）。上傳後，Dependency-Track 會在背景跑 component intelligence 分析，通常幾分鐘內就能在 UI 看到漏洞清單。

## 底層機制：Component Intelligence 怎麼運作

```
SBOM 上傳
    │
    ▼
Component 正規化
  name + version → purl/cpe 標準化
    │
    ▼
Analyzer 鏈（並行）
  ┌─────────────────┬─────────────────┬─────────────────┐
  │ NVD Analyzer    │ GHSA Analyzer   │ OSS Index        │
  │ CPE 比對        │ PURL 比對       │ (Sonatype)       │
  └────────┬────────┴────────┬────────┴────────┬─────────┘
           │                 │                 │
           ▼                 ▼                 ▼
        結果合併（去重、保留最高嚴重度的記錄）
    │
    ▼
Policy Engine
  評估 project 的 policy 規則
  是否觸發 violation（違規）
    │
    ▼
Notification
  Webhook / Email / Slack / Teams / ...
```

Dependency-Track 定期（通常每 24 小時）重新執行這個流程。你的 SBOM 元件沒有變，但它會去比對最新的漏洞 DB——這就是「持續監控」的核心。

## Policy：設定你的安全閘

Policy 讓你定義「什麼情況算是違規」（violation），獨立於掃描工具的 `--fail-on`。

**常見 policy 設定（UI 操作）**：

1. **Severity Policy**：有 Critical 漏洞 → violation
2. **License Policy**：含 GPL-3.0 的元件 → violation（對商業軟體）
3. **Component Age Policy**：元件版本超過 N 天沒更新 → warning
4. **EOL Policy**：元件已 EOL（End of Life）→ violation

Policy 可以設定 violation 的嚴重度（`FAIL` / `WARN` / `INFO`），也可以設定例外（suppress specific violation）。

## Dependency-Track 和 VEX 整合

Dependency-Track 支援上傳 VEX 文件，把你的 `not_affected` 聲明和漏洞記錄關聯起來：

```bash
# 上傳 VEX（CycloneDX VEX 格式）
curl -s -X "PUT" "http://localhost:8081/api/v1/vex" \
  -H 'Content-Type: application/json' \
  -H 'X-Api-Key: YOUR_API_KEY' \
  -d "{
    \"project\": \"YOUR_PROJECT_UUID\",
    \"vex\": \"$(base64 --wrap=0 < my-app.vex.json)\"
  }"
```

上傳後，被標記為 `not_affected` 的漏洞在 UI 裡會顯示為 suppressed 狀態，不會觸發告警。

## Dependency-Track 補了 CLI 的什麼？

| 面向 | CLI 掃描（grype/trivy） | Dependency-Track |
|---|---|---|
| 時間維度 | 時間點快照（build time） | 持續監控（有新 CVE 就告警） |
| 多專案管理 | 需要腳本自動化 | 原生 project 概念，集中看板 |
| 歷史趨勢 | 無 | SBOM 版本間的漏洞變化 |
| VEX 管理 | `--vex` 旗標（per-invocation） | 持久化，和 project 綁定 |
| Policy 告警 | 需要自己寫腳本 | 原生 policy engine |
| 通知整合 | 需要自己寫 webhook | 原生 Webhook/Email/Slack |
| 法規合規報告 | 無 | 可匯出合規摘要 |
| 元件 reuse 分析 | 無 | 同一元件跨多 project 的風險視圖 |

CLI 掃描是「CI pipeline 的把關」，Dependency-Track 是「資產庫的長期監控」——兩者不是替代關係，而是互補。

## 踩雷集錦

1. **「初始同步」需要 10-30 分鐘**：第一次啟動後，Dependency-Track 會從 NVD 和 GHSA 做全量漏洞 DB 同步。這段時間 UI 可以登入，但漏洞分析結果可能是空的或不完整的。等 log 出現 "Synchronization completed" 才算好。
2. **H2 不適合生產**：預設的內嵌 H2 資料庫不支援多個 API server 實例，也沒有好的備份機制。任何超過玩玩的用途，都應該換 PostgreSQL。
3. **API Key 只顯示一次**：建立 API Key 後，Dependency-Track 只顯示明文一次就儲存 hash。沒複製就再也看不到，要重新建一個。
4. **base64 不能有換行**：curl 的 PUT 方法上傳 SBOM 時，base64 要用 `--wrap=0`（Linux）確保是一行。帶換行的 base64 JSON 會讓 API server 返回 400。
5. **version 欄位要同步**：Dependency-Track 用 `(name, version)` 識別 project。同一個應用的不同版本 SBOM，要帶不同的 version 字串，否則會覆蓋掉舊版的記錄（覆蓋不一定是壞事，但要有意識地選擇）。

## 進階：再往深一層

### CI 整合腳本

典型的 CI pipeline（概念性，需依環境調整）：

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. 生成 SBOM
syft . -o cyclonedx-json=sbom.cdx.json

# 2. 上傳到 Dependency-Track
curl -s -X "POST" "${DTRACK_URL}/api/v1/bom" \
  -H "X-Api-Key: ${DTRACK_API_KEY}" \
  -F "projectName=${PROJECT_NAME}" \
  -F "projectVersion=${GIT_TAG:-$(git rev-parse --short HEAD)}" \
  -F "autoCreate=true" \
  -F "bom=@sbom.cdx.json;type=application/json"

# 3. 本地掃描做 CI gating（不等 Dependency-Track 的非同步分析）
grype sbom:sbom.cdx.json --fail-on critical || {
  echo "Critical vulnerabilities found in build. Blocking."
  exit 1
}
```

注意：上傳到 Dependency-Track 是「存起來持續監控」；本地掃描是「build time 的即時 gating」。兩個動作都要做，各自解決不同的問題。

### 用 Dependency-Track API 查詢 project 漏洞

```bash
# 取得 project 的 findings
curl -s "http://localhost:8081/api/v1/finding/project/YOUR_PROJECT_UUID" \
  -H "X-Api-Key: YOUR_API_KEY" | \
  jq '[.[] | {pkg:.component.name, ver:.component.version,
               vuln:.vulnerability.vulnId, sev:.vulnerability.severity}] |
      sort_by(.sev) | .[0:10]'
```

### Notification webhook

```json
# Dependency-Track Notification 設定（在 UI Administration → Notifications 建立）
{
  "publisher": "SLACK",
  "level": "HIGH",
  "groups": ["NEW_VULNERABILITY"],
  "destinations": ["https://hooks.slack.com/services/..."]
}
```

收到新 CVE 命中你的任何 project 時，自動推 Slack 通知。這是從「有人記得要掃」到「有新漏洞自動通知」的關鍵轉變。

## 動手練習

1. （**如果你有 Docker 環境**）：照官方步驟 `curl -LO https://dependencytrack.org/docker-compose.yml && docker compose up -d`，等初始化後登入 `http://localhost:8080`，用 `admin/admin` 登入並改密碼。建立一個 project，把 `/tmp/vuln-demo/sbom.cdx.json` 用 multipart POST 上傳，等幾分鐘看到漏洞清單出現。
2. （**不論有沒有 Docker**）：研究 Dependency-Track 的 [REST API 文件](http://localhost:8081/api/openapi.yaml)（如果有起服務）或 [線上 API 範例](https://docs.dependencytrack.org/integrations/rest-api/)，理解 `GET /api/v1/finding/project/{uuid}` 的回應結構——特別是 `component`、`vulnerability`、`analysis` 三個物件。
3. 思考：在你目前的工作環境（或你正在維護的專案），哪些地方是「只在 build time 掃一次」的盲點？如果某個第三方套件明天爆出 Critical CVE，你要多久才能知道？

## 本章重點整理

- Dependency-Track 補的是**時間維度**：你的 SBOM 元件沒變，但漏洞 DB 每天更新，今天安全不代表明天安全。Dependency-Track 讓「持續監控」成為預設，而不是需要人工記得去觸發的動作。
- 架構：API Server（port 8081）+ Frontend（port 8080）+ DB（預設 H2，生產用 PG）。
- 上傳 SBOM 的方式：multipart POST（`-F "bom=@file.json"`）最直觀；base64 PUT 也可以。`autoCreate=true` 不需要先建 project。
- CLI 掃描（grype/trivy）= build time 把關；Dependency-Track = 運行期持續監控。兩者互補，不是替代。

## 自我檢核

- [ ] 我能解釋「今天沒有 CVE、明天可能有」這個時間維度問題，以及 Dependency-Track 怎麼解決它
- [ ] 我知道 Dependency-Track 的 `(API Server, Frontend, DB)` 三元架構各自負責什麼
- [ ] 我能寫出用 curl 上傳 SBOM 到 Dependency-Track 的指令（multipart 或 base64 PUT 擇一）
- [ ] 我能解釋 Dependency-Track 的 policy engine 和 grype `--fail-on` 的差別

## 延伸閱讀

- **[OWASP Dependency-Track 官方文件](https://docs.dependencytrack.org/)** — 起點；特別看「Getting Started > Deploy Docker」和「Usage > CI-CD」兩節
- **[Dependency-Track REST API](https://docs.dependencytrack.org/integrations/rest-api/)** — CI 整合的所有 endpoint，上傳 SBOM、查詢 findings、管理 policy 全在這裡
- **[Dependency-Track + GitHub Actions 範例](https://docs.dependencytrack.org/integrations/github-actions/)** — 官方提供的 GitHub Actions 整合範例，可以直接拿來改

---

Part 4 完成了。你現在有了完整的「消費端」能力：把 SBOM 轉成漏洞情報（Ch 13）、理解漏洞資料來源的局限（Ch 14）、三工具並排實戰（Ch 15）、用 VEX 降噪（Ch 16）、用 Dependency-Track 持續監控（Ch 17）。下一個練習把這些整合起來動手做一遍。

→ [練習 B：VEX 降噪 + Dependency-Track 監控](./practice-b-vex-and-monitoring.md)
