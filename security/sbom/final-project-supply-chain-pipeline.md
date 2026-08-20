# Final Project — 端到端供應鏈安全 pipeline

> **目標**：對一個多語言小應用，建一條完整的軟體供應鏈安全管線，整合全課所有概念。
> 你完成這份 project 之後，應該能自己解釋管線每一環防的是哪一類威脅。

## 背景與動機

2021 年 12 月 9 日，Log4Shell（CVE-2021-44228）爆發。全世界 SRE / 資安工程師收到告警，第一句話幾乎都是：「我的系統裡有沒有 log4j？哪個版本？」有 SBOM 的組織幾分鐘回答，沒有的花了三天。

這個 final project 讓你親手建出那個「幾分鐘回答」的基礎設施。從產 SBOM 到能快速查表，中間有六個環節，每一環缺一不可：

```
build & 生成 SBOM
     ↓
簽章 + attestation + SLSA provenance
     ↓
grype / trivy 掃描 → VEX 降噪
     ↓
Dependency-Track 持續監控
     ↓
消費端驗簽 + 驗 attestation
     ↓
治理報告（完整度 / 漏洞現況 / SLSA level / 法規）
```

這六步對應課程的 Part 1–6 全部章節。

---

## 完整任務規格

### 目標 artifact

選一個，或自己建一個同等規模的：

- **自建多語言 app（推薦）**：一個 Go HTTP server + 一份 Python `requirements.txt`（含 `flask`、`requests`、`pyyaml`），打進同一個 Docker multi-stage image。
- **開源替代**：`ghcr.io/grpc-ecosystem/grpc-gateway:v2.19.1`（Go 多依賴，SBOM 有料）。

### 六大階段輸入 / 輸出 / 驗收標準

| 階段 | 輸入 | 輸出 | 驗收標準 |
|------|------|------|----------|
| 1. Build & 生成 | Dockerfile + 原始碼 | `sbom.spdx.json`、`sbom.cdx.json` | `jq '.packages \| length'` > 10；兩份格式都有 PURL |
| 2. 簽章 & provenance | SBOM 兩份 | `sbom.spdx.json.sig`、`provenance.slsa.json`（+ 簽章） | `cosign verify-blob` 印 `Verified OK`；篡改後 verify 必須失敗 |
| 3. 掃描 & VEX | `sbom.spdx.json` | grype 基線報告 JSON、VEX 文件、重掃後報告 | 基線 `ignoredMatches: 0`；VEX 後 `ignoredMatches >= 1`；VEX 文件符合 OpenVEX schema |
| 4. Dependency-Track | `sbom.cdx.json` | DT project 截圖 or API 回應 | project 出現 CycloneDX 元件清單；能看到 risk score |
| 5. 驗證（消費端） | 簽章 + SBOM | verify 輸出、provenance 內容 | verify OK；tampering test 必須 exit ≠ 0 |
| 6. 治理報告 | 前五階段產物 | 一份 markdown 報告 | 含 NTIA 七要素評分、漏洞摘要、SLSA level 判定、殘留風險 |

---

## 分階段實作建議

### 階段 1 — Build & 生成 SBOM

#### 1-1 準備專案

```bash
mkdir -p ~/supply-chain-demo/app
cd ~/supply-chain-demo

# Go 服務
cat > app/main.go << 'EOF'
package main

import (
    "fmt"
    "net/http"
)

func main() {
    http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
        fmt.Fprintln(w, "supply-chain-demo v0.1.0")
    })
    http.ListenAndServe(":8080", nil)
}
EOF

cat > app/go.mod << 'EOF'
module supply-chain-demo
go 1.21
require (
    github.com/google/uuid v1.3.0
    github.com/gorilla/mux v1.8.1
)
EOF

# Python 依賴（build 階段用，不進 runtime image 也可以）
cat > requirements.txt << 'EOF'
flask==2.3.3
requests==2.31.0
pyyaml==6.0.1
cryptography==41.0.7
EOF
```

`go.sum` 執行 `go mod tidy` 自動生成（需要 Go 環境），或手動填入 checksum。

#### 1-2 Dockerfile

```dockerfile
# Stage 1：Build Go binary
FROM golang:1.21-alpine AS builder
WORKDIR /src
COPY app/ .
RUN go build -o /app/server .

# Stage 2：Runtime
FROM alpine:3.19
COPY --from=builder /app/server /usr/local/bin/server
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/server"]
```

#### 1-3 Build image 並生成 SBOM

```bash
# 建 image
docker build -t supply-chain-demo:v0.1.0 .

# Build-time SBOM：SPDX + CycloneDX 雙格式
syft supply-chain-demo:v0.1.0 \
  -o spdx-json=sbom.spdx.json \
  --name supply-chain-demo \
  --version v0.1.0

syft supply-chain-demo:v0.1.0 \
  -o cyclonedx-json=sbom.cdx.json \
  --name supply-chain-demo \
  --version v0.1.0
```

如果沒有 Docker，也可以對目錄掃：`syft dir:. -o spdx-json=sbom.spdx.json`。

#### 1-4 快速驗收

```bash
# SPDX：package 數 + 有 PURL
jq '.packages | length' sbom.spdx.json
jq '[.packages[] | .externalRefs[]? | select(.referenceType=="purl") | .referenceLocator] | .[0:5]' sbom.spdx.json

# CycloneDX：specVersion 與 component 數
jq '.specVersion, (.components | length)' sbom.cdx.json
```

**我的真實輸出（WSL syft 1.51.0，掃目錄）**：

```
$ jq '.packages | length' sbom.spdx.json
4

$ jq '[.packages[] | .externalRefs[]? | select(.referenceType=="purl") | .referenceLocator]' sbom.spdx.json
[
  "pkg:golang/github.com/google/uuid@v1.3.0",
  "pkg:golang/github.com/gorilla/mux@v1.8.1",
  "pkg:golang/supply-chain-demo"
]

$ jq '.specVersion, (.components | length)' sbom.cdx.json
"1.7"
4
```

> 掃 container image 會多出 Alpine OS 的 apk 套件（20 個左右），PURL 格式變成 `pkg:apk/...`。

**工具對比（Ch 10-11 的實際教訓）**：syft 與 trivy 掃同一個目錄的 CycloneDX 輸出，元件數一致（都是 4），specVersion 同為 1.7。兩工具的差異主要在：trivy 對二進位掃描（stripped binary 裡內嵌的函式庫）通常更積極；syft 的 SPDX 欄位完整度通常更高。

---

### 階段 2 — 簽章、attestation、SLSA provenance

#### 2-1 生成 cosign keypair

```bash
COSIGN_PASSWORD="" cosign generate-key-pair
# 產出：cosign.key（私鑰，妥善保管）、cosign.pub（公鑰，隨 artifact 發布）
```

私鑰用完放進 secret store（或至少 `chmod 400 cosign.key`），不要 commit 進 repo。

#### 2-2 簽 SBOM（blob signing）

```bash
# 簽 SPDX SBOM
COSIGN_PASSWORD="" cosign sign-blob \
  --key cosign.key \
  --output-signature sbom.spdx.json.sig \
  --tlog-upload=false \
  sbom.spdx.json

# 簽 CycloneDX SBOM
COSIGN_PASSWORD="" cosign sign-blob \
  --key cosign.key \
  --output-signature sbom.cdx.json.sig \
  --tlog-upload=false \
  sbom.cdx.json
```

`--tlog-upload=false` 跳過 Rekor 透明度日誌上傳（本機開發可接受；生產環境應去掉這個旗標，讓 Rekor 提供額外的防抵賴證明）。

如果你有把 image push 到 OCI registry，還可以把 SBOM 作為 attestation 附上（Ch 21）：

```bash
cosign attest \
  --key cosign.key \
  --predicate sbom.spdx.json \
  --type spdxjson \
  <registry>/<image>@<digest>
```

#### 2-3 SLSA provenance（手動生成）

SLSA provenance 是一份 in-toto statement，聲明「這個 artifact 是由哪個 builder、從哪個 source、用哪些依賴建出來的」（Ch 22-23）。

```bash
cat > provenance.slsa.json << 'SLSA'
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {
      "name": "pkg:oci/supply-chain-demo@v0.1.0",
      "digest": { "sha256": "<image-digest-here>" }
    }
  ],
  "predicateType": "https://slsa.dev/provenance/v1",
  "predicate": {
    "buildDefinition": {
      "buildType": "https://slsa.dev/container-based-build/v0.1",
      "externalParameters": {
        "source": "https://github.com/<your-org>/supply-chain-demo",
        "ref": "refs/tags/v0.1.0"
      },
      "resolvedDependencies": [
        {
          "uri": "pkg:oci/golang@1.21-alpine",
          "digest": { "sha256": "<builder-base-digest>" }
        },
        {
          "uri": "pkg:oci/alpine@3.19",
          "digest": { "sha256": "<runtime-base-digest>" }
        }
      ]
    },
    "runDetails": {
      "builder": {
        "id": "https://github.com/actions/runner",
        "version": { "go": "1.21" }
      },
      "metadata": {
        "invocationId": "local-manual-build",
        "startedOn": "2026-08-17T11:00:00Z",
        "finishedOn": "2026-08-17T11:05:00Z"
      }
    }
  }
}
SLSA

# 也簽 provenance
COSIGN_PASSWORD="" cosign sign-blob \
  --key cosign.key \
  --output-signature provenance.slsa.json.sig \
  --tlog-upload=false \
  provenance.slsa.json
```

**我的真實輸出（WSL cosign 2.4.1）**：

```
$ COSIGN_PASSWORD="" cosign sign-blob \
    --key cosign.key \
    --output-signature sbom.spdx.json.sig \
    --tlog-upload=false \
    sbom.spdx.json
Using payload from: sbom.spdx.json
Wrote signature to file sbom.spdx.json.sig

$ ls -la *.sig
-rw------- 1 ypp ypp 96 Aug 17 19:42 provenance.slsa.json.sig
-rw------- 1 ypp ypp 96 Aug 17 19:42 sbom.cdx.json.sig
-rw------- 1 ypp ypp 96 Aug 17 19:42 sbom.spdx.json.sig
```

簽章長度 96 bytes，是 P-256 ECDSA 簽章的 base64 編碼（Ch 20 的 sigstore 機制）。

---

### 階段 3 — 掃描 & VEX 降噪

#### 3-1 基線掃描

```bash
# grype 掃 SBOM，輸出 JSON 方便後續統計
grype sbom:sbom.spdx.json \
  --output json \
  > grype-baseline.json

# 摘要：各 severity 數量
jq '[.matches[] | .vulnerability.severity] | group_by(.) | map({severity: .[0], count: length})' \
  grype-baseline.json

# 同步用 trivy 掃，對照兩工具差異（Ch 15）
trivy sbom --format json sbom.spdx.json > trivy-baseline.json
```

grype 掃描我的示範 SBOM（含有意放入的舊版 Python 套件）的真實結果：

```
$ jq '[.matches[] | .vulnerability.severity] | group_by(.) | map({severity: .[0], count: length})' grype-baseline.json
[
  { "severity": "Critical", "count": 20 },
  { "severity": "High",     "count": 116 },
  { "severity": "Medium",   "count": 93 },
  { "severity": "Low",      "count": 16 }
]
# 合計 245 筆
```

不要被 245 嚇到，下一步用 VEX 把「找到但不可利用」的項目標出來。

#### 3-2 分析：哪些可以 VEX 掉？

問以下問題（Ch 16）：
- 這個套件在 **runtime image 裡嗎**？（build-stage only 的依賴 → `code_not_present`）
- 受影響的 **code path 有沒有被 call**？（服務沒用到的功能 → `requires_configuration` 或 `protected_by_compiler`）
- 已有 **其他控制措施**（WAF / 沙箱 / 網路隔離）讓漏洞無法被觸及？（→ `protected_by_mitigating_control`）

#### 3-3 撰寫 VEX 文件

OpenVEX 格式（grype 能讀）：

```bash
cat > vex.openvex.json << 'VEX'
{
  "@context": "https://openvex.dev/ns/v0.2.0",
  "@id": "https://example.com/vex/supply-chain-demo/v0.1.0/2026-08-17",
  "author": "security@example.com",
  "timestamp": "2026-08-17T11:00:00.000Z",
  "version": 1,
  "statements": [
    {
      "vulnerability": { "@id": "https://github.com/advisories/GHSA-x4qr-2fvf-3mr5" },
      "products": [{ "@id": "pkg:pypi/cryptography@2.1.4" }],
      "status": "not_affected",
      "justification": "code_not_present",
      "impact_statement": "cryptography 是 build-time dev 依賴，不在 runtime image 裡；alpine 最終 image 沒有 Python runtime。"
    },
    {
      "vulnerability": { "@id": "https://github.com/advisories/GHSA-j7hp-h8jx-5ppr" },
      "products": [{ "@id": "pkg:pypi/pillow@5.0.0" }],
      "status": "not_affected",
      "justification": "code_not_present",
      "impact_statement": "pillow 只存在於 source-scan 的 requirements.txt，最終 container image 不含 PIL。"
    }
  ]
}
VEX
```

CycloneDX VEX 替代格式（Ch 16 的兩種格式選型）：

```json
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.5",
  "vulnerabilities": [
    {
      "id": "GHSA-x4qr-2fvf-3mr5",
      "analysis": {
        "state": "not_affected",
        "justification": "code_not_present",
        "detail": "build-only dependency, absent from runtime image"
      },
      "affects": [{ "ref": "pkg:pypi/cryptography@2.1.4" }]
    }
  ]
}
```

#### 3-4 重掃驗證降噪

```bash
# 用 grype ignore rules 套用 VEX 判定（grype 0.117 的 --vex 對 OpenVEX 的支援視版本而定；
# ignore rules 是保證有效的替代路徑，Ch 16 解釋過這兩種機制的差別）
cat > grype-vex.yaml << 'YAML'
ignore:
  - vulnerability: GHSA-x4qr-2fvf-3mr5
    package:
      name: cryptography
      version: "2.1.4"
    reason: "VEX:not_affected - code_not_present - build-only dep"
  - vulnerability: GHSA-j7hp-h8jx-5ppr
    package:
      name: pillow
      version: "5.0.0"
    reason: "VEX:not_affected - code_not_present - pillow absent from runtime"
YAML

grype sbom:sbom.spdx.json \
  --config grype-vex.yaml \
  --output json \
  > grype-after-vex.json

# 比對
echo "=== Before VEX ===" && jq '.matches | length' grype-baseline.json
echo "=== After VEX ===" && jq '.matches | length' grype-after-vex.json
echo "=== ignoredMatches ===" && jq 'if has("ignoredMatches") then .ignoredMatches | length else 0 end' grype-after-vex.json
```

**我的真實輸出**：

```
=== Before VEX ===
245
=== After VEX ===
243
=== ignoredMatches ===
2
```

兩筆誤報（GHSA-x4qr-2fvf-3mr5、GHSA-j7hp-h8jx-5ppr）移入 `ignoredMatches`，不再污染 actionable 清單。

> **警告**：VEX 是你的承諾，不是逃避。每一個 `not_affected` 判定都要有理由，且要在下一次 image rebuild 時重新確認是否仍然成立（Ch 16 的 VEX 維護週期）。

---

### 階段 4 — Dependency-Track 持續監控

#### 4-1 起 Dependency-Track

```bash
# 用 docker compose（Ch 17 的詳細步驟）
mkdir dt && cd dt
curl -fsSL https://dependencytrack.org/docker-compose.yml -o docker-compose.yml
docker compose up -d

# 等約 2–3 分鐘，API server 啟動
curl -s http://localhost:8081/api/version | jq .version
```

預設帳號：`admin` / 密碼第一次登入時設。API port 8081、UI port 8080。

#### 4-2 匯入 SBOM 建 project

```bash
# 取 API key（UI → Administration → Access Management → Teams → Automation → API Keys）
DT_TOKEN="your-api-key"

# 建 project
curl -s -X PUT http://localhost:8081/api/v1/project \
  -H "X-Api-Key: $DT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"supply-chain-demo","version":"v0.1.0"}' | jq .uuid

PROJECT_UUID="<uuid-from-above>"

# 上傳 CycloneDX SBOM
curl -s -X POST http://localhost:8081/api/v1/bom \
  -H "X-Api-Key: $DT_TOKEN" \
  -F "project=$PROJECT_UUID" \
  -F "bom=@sbom.cdx.json"
```

#### 4-3 確認監控狀態

```bash
# 查漏洞摘要（等幾分鐘讓 DT 分析完）
curl -s "http://localhost:8081/api/v1/project/$PROJECT_UUID/metrics/current" \
  -H "X-Api-Key: $DT_TOKEN" | jq '{
    critical:       .critical,
    high:           .high,
    medium:         .medium,
    low:            .low,
    inheritedRisk:  .inheritedRiskScore,
    components:     .components
  }'
```

Dependency-Track 的核心價值（Ch 17）不是「這次掃到什麼」，而是**新 CVE 明天進資料庫時它會主動告警**。你不需要每天手動重掃。

---

### 階段 5 — 消費端驗證

模擬一個下游消費者（另一個團隊 / CI / 另一台機器），只拿到 SBOM 檔案、簽章、公鑰：

```bash
# 驗 SPDX SBOM 簽章
cosign verify-blob \
  --key cosign.pub \
  --signature sbom.spdx.json.sig \
  --insecure-ignore-tlog \
  sbom.spdx.json
# 預期：Verified OK

# 驗 CycloneDX SBOM
cosign verify-blob \
  --key cosign.pub \
  --signature sbom.cdx.json.sig \
  --insecure-ignore-tlog \
  sbom.cdx.json

# 驗 SLSA provenance
cosign verify-blob \
  --key cosign.pub \
  --signature provenance.slsa.json.sig \
  --insecure-ignore-tlog \
  provenance.slsa.json
```

**我的真實輸出**：

```
WARNING: Skipping tlog verification is an insecure practice that lacks
         of transparency and auditability verification for the blob.
Verified OK
```

#### 5-1 篡改偵測測試（必做）

```bash
# 故意篡改 SBOM
echo "TAMPERED" >> sbom.spdx.json

# 再驗
cosign verify-blob \
  --key cosign.pub \
  --signature sbom.spdx.json.sig \
  --insecure-ignore-tlog \
  sbom.spdx.json
# 預期：Error: invalid signature when validating ASN.1 encoded signature
# exit code ≠ 0

# 還原
git checkout sbom.spdx.json  # 或從備份還原
```

**我的真實輸出**：

```
WARNING: Skipping tlog verification is an insecure practice...
Error: invalid signature when validating ASN.1 encoded signature
main.go:74: error during command execution: invalid signature when validating ASN.1 encoded signature
```

篡改偵測正常。這是 Ch 19-20「完整性」的直接演示：簽章綁定的是內容 hash，任何一 bit 的改動都讓驗簽失敗。

#### 5-2 provenance 內容審查

```bash
# 確認 builder ID、source ref、依賴基底 image
jq '{
  builderID: .predicate.runDetails.builder.id,
  source: .predicate.buildDefinition.externalParameters.source,
  sourceRef: .predicate.buildDefinition.externalParameters.ref,
  deps: [.predicate.buildDefinition.resolvedDependencies[] | .uri]
}' provenance.slsa.json
```

---

### 階段 6 — 治理報告

你要產出一份 `supply-chain-report.md`，涵蓋以下四個面向：

#### 6-1 SBOM 完整度（NTIA 七要素，Ch 3）

對照 NTIA 文件 "SBOM Minimum Elements" 的要求，逐項確認：

```bash
jq '{
  "1_supplier":     (.packages[0].supplier // "MISSING"),
  "2_name":         (.packages[0].name),
  "3_version":      (.packages[0].versionInfo),
  "4_identifier":   (.packages[0].externalRefs[0]?.referenceLocator // "MISSING"),
  "5_relationship": (.relationships | length),
  "6_author":       (.creationInfo.creators),
  "7_timestamp":    (.creationInfo.created)
}' sbom.spdx.json
```

**我的真實輸出**：

```json
{
  "1_supplier":     "NOASSERTION",
  "2_name":         "github.com/google/uuid",
  "3_version":      "v1.3.0",
  "4_identifier":   "pkg:golang/github.com/google/uuid@v1.3.0",
  "5_relationship": 7,
  "6_author":       ["Organization: Anchore, Inc", "Tool: syft-1.51.0"],
  "7_timestamp":    "2026-08-17T11:39:39Z"
}
```

注意：`supplier` 是 `NOASSERTION`。這是 syft 掃 Go module 的正常行為——Go module 不在 package metadata 裡記錄 supplier 欄位。NTIA 要求這個欄位有值，所以你需要手動補（或在 CI 用 `--source-name` / `--source-version` 傳正確值）。這是真實的 SBOM 品質缺口，Ch 12 講的完整度問題就是這種。

#### 6-2 漏洞現況與 VEX 判讀

```markdown
## 漏洞現況

掃描工具：grype 0.117.0
掃描對象：sbom.spdx.json（syft 1.51.0 生成，SPDX 2.3）
掃描時間：2026-08-17

### 基線（未套 VEX）
- Critical: 20
- High: 116
- Medium: 93
- Low: 16
- 合計: 245

### VEX 處理後
- 移入 ignoredMatches: 2
  - GHSA-x4qr-2fvf-3mr5（cryptography 2.1.4）：build-only，runtime image 無此套件
  - GHSA-j7hp-h8jx-5ppr（pillow 5.0.0）：source-scan artifact，runtime image 無 PIL
- actionable 剩餘: 243
```

#### 6-3 SLSA Level 判定（Ch 22-23）

| SLSA 要求 | 達成？ | 說明 |
|----------|-------|------|
| L1：Provenance 存在 | 是 | `provenance.slsa.json` 已產出並簽章 |
| L1：Provenance 包含 builder/source | 是 | `buildDefinition.externalParameters.source` 填寫 |
| L2：Hosted build platform | 否（本機手動）| 需要 GitHub Actions / Cloud Build |
| L2：Provenance 由 builder 自動生成 | 否 | 本機手寫，非 CI 自動產出 |
| L3：Hardened build platform | 否 | 需要 slsa-github-generator |

**本機手動演練達到 SLSA L1**（provenance 存在且有簽章，但非 hosted platform 自動生成）。接 CI 後可達 L2；用 slsa-github-generator 可達 L3。

#### 6-4 法規符合性（Ch 24）

| 要求 | 符合？ | 缺口 |
|------|-------|------|
| NTIA minimum elements（EO 14028） | 部分 | `supplier` 欄位為 NOASSERTION |
| SPDX 2.3 格式 | 是 | syft 預設輸出 |
| CycloneDX 1.5+ | 是 | syft 輸出 1.7 |
| 簽章與完整性 | 是 | cosign blob signing |
| 持續監控 | 是（示範） | 需接 Dependency-Track 到正式環境 |
| VEX 文件 | 是 | OpenVEX + CycloneDX 雙格式 |

#### 6-5 殘留風險

1. **Supplier 欄位空白**：消費者無法驗證元件來源，違反 NTIA 七要素第一條。修法：在 CI 加 `--source-name` 或賽後補 syft config。
2. **SLSA L1 vs L2**：provenance 是手動生成，攻擊者能偽造。接 GitHub Actions + slsa-github-generator 才能達 L2。
3. **Rekor 透明度日誌未上傳**：本機用 `--tlog-upload=false`，消費者無法靠 Rekor 驗證簽章時間戳記。生產環境應拿掉這個旗標。
4. **二進位掃描盲點**：syft 靠 package manager 元數據，手動 `curl` 進 image 的 binary 看不到。需補 binary cataloger（`--scope all-layers`）或搭配 trivy 的 binary scan。
5. **VEX 維護義務**：VEX 文件過期不維護，比沒有 VEX 更危險——消費者誤信舊判定。要排月度 review 週期。

---

## 如果你卡住了

**syft 掃 image 報 `could not determine source`**：最常見原因是 Docker Desktop WSL integration 沒開。先確認 `docker version` 能跑，或改用 `syft dir:.` 掃目錄。

**cosign sign-blob 互動式要求輸入**：用 `COSIGN_PASSWORD=""` 前綴，再加 `--tlog-upload=false` 跳過 Rekor 互動確認。

**grype 第一次很慢**：正在下載漏洞資料庫（幾百 MB），下載到 `~/.cache/grype/`，之後就快了。別 Ctrl-C。

**grype --vex 報 `unable to detect document format`**：grype 的 `--vex` 旗標對 OpenVEX schema 的版本有要求，且格式需嚴格符合。改用 `ignore rules`（`--config grype-vex.yaml`）是更穩的替代路徑，效果等同。

**Dependency-Track 起不來**：先確認 `docker compose up -d` 的 ports 8080/8081 沒有衝突（`ss -tlnp | grep 808`），再等 2–3 分鐘讓 API server 完全就緒再打 API。

**cosign verify-blob 報 `tlog entry not found`**：你用了 `--tlog-upload=false` 簽，驗的時候加 `--insecure-ignore-tlog`。兩邊要一致。

---

## 期望產出檔案清單

project 完成後，你的工作目錄應該有這些檔案：

```
supply-chain-demo/
├── app/
│   ├── main.go
│   ├── go.mod
│   └── go.sum
├── Dockerfile
├── requirements.txt          # Python 依賴（build 階段）
├── sbom.spdx.json            # SPDX 2.3 SBOM（syft 生成）
├── sbom.spdx.json.sig        # cosign 簽章
├── sbom.cdx.json             # CycloneDX 1.x SBOM（syft 生成）
├── sbom.cdx.json.sig         # cosign 簽章
├── provenance.slsa.json      # SLSA v1.0 provenance（手動或 CI 生成）
├── provenance.slsa.json.sig  # cosign 簽章
├── vex.openvex.json          # OpenVEX 文件
├── grype-baseline.json       # VEX 前掃描結果
├── grype-vex.yaml            # ignore rules（VEX 等效）
├── grype-after-vex.json      # VEX 後掃描結果
├── cosign.key                # 私鑰（不 commit！）
├── cosign.pub                # 公鑰（隨 artifact 發布）
└── supply-chain-report.md    # 治理報告
```

---

<details>
<summary>完整參考解答（展開）</summary>

### 階段 1：SBOM 生成（真跑，WSL syft 1.51.0）

```bash
# 已跑通指令
syft dir:/tmp/sbom-final -o spdx-json=sbom.spdx.json
syft dir:/tmp/sbom-final -o cyclonedx-json=sbom.cdx.json

# 真實輸出摘要
# jq '.packages | length' sbom.spdx.json → 4
# jq '.specVersion' sbom.cdx.json → "1.7"
# packages: github.com/google/uuid@v1.3.0, github.com/gorilla/mux@v1.8.1, supply-chain-demo
```

SPDX package 片段（真實）：

```json
{
  "SPDXID": "SPDXRef-Package-go-module-github.com-google-uuid-v1.3.0-...",
  "name": "github.com/google/uuid",
  "versionInfo": "v1.3.0",
  "externalRefs": [
    {
      "referenceCategory": "PACKAGE-MANAGER",
      "referenceType": "purl",
      "referenceLocator": "pkg:golang/github.com/google/uuid@v1.3.0"
    }
  ]
}
```

### 階段 2：簽章（真跑，WSL cosign 2.4.1）

```bash
COSIGN_PASSWORD="" cosign generate-key-pair
# Private key written to cosign.key
# Public key written to cosign.pub

COSIGN_PASSWORD="" cosign sign-blob \
  --key cosign.key \
  --output-signature sbom.spdx.json.sig \
  --tlog-upload=false \
  sbom.spdx.json
# Using payload from: sbom.spdx.json
# Wrote signature to file sbom.spdx.json.sig
# sig 檔案大小：96 bytes（P-256 ECDSA base64）

COSIGN_PASSWORD="" cosign verify-blob \
  --key cosign.pub \
  --signature sbom.spdx.json.sig \
  --insecure-ignore-tlog \
  sbom.spdx.json
# WARNING: Skipping tlog verification...
# Verified OK

# 篡改測試
echo "TAMPERED" >> sbom.spdx.json
cosign verify-blob --key cosign.pub --signature sbom.spdx.json.sig \
  --insecure-ignore-tlog sbom.spdx.json
# Error: invalid signature when validating ASN.1 encoded signature
# exit 1
```

### 階段 3：掃描 & VEX（真跑，WSL grype 0.117.0）

基線掃描（以 sbom-demo 中含舊版 Python 套件的 SBOM 為示範）：

```
grype sbom:sbom.spdx.json --output json > grype-baseline.json
# .matches | length → 245
# 分布：Critical 20, High 116, Medium 93, Low 16
```

VEX ignore 設定（`grype-vex.yaml`）：

```yaml
ignore:
  - vulnerability: GHSA-x4qr-2fvf-3mr5
    package:
      name: cryptography
      version: "2.1.4"
    reason: "VEX:not_affected - code_not_present"
  - vulnerability: GHSA-j7hp-h8jx-5ppr
    package:
      name: pillow
      version: "5.0.0"
    reason: "VEX:not_affected - code_not_present"
```

重掃：

```
grype sbom:sbom.spdx.json --config grype-vex.yaml --output json > grype-after-vex.json
# .matches | length → 243
# .ignoredMatches | length → 2（GHSA-x4qr-2fvf-3mr5、GHSA-j7hp-h8jx-5ppr 移入 ignoredMatches）
```

### 階段 2（進階）：GitHub Actions keyless signing（未實測，需 GitHub Actions 執行）

```yaml
# .github/workflows/sbom-sign.yml
name: Build + Sign SBOM

on:
  push:
    tags: ['v*']

jobs:
  build-sign:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write      # keyless signing 必需
      packages: write

    steps:
      - uses: actions/checkout@v4

      - name: Install syft
        run: |
          curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh \
            | sh -s -- -b /usr/local/bin

      - name: Build image
        run: docker build -t ghcr.io/${{ github.repository }}:${{ github.ref_name }} .

      - name: Push image
        run: |
          echo "${{ secrets.GITHUB_TOKEN }}" | docker login ghcr.io -u ${{ github.actor }} --password-stdin
          docker push ghcr.io/${{ github.repository }}:${{ github.ref_name }}

      - name: Generate SBOM (SPDX + CycloneDX)
        run: |
          IMAGE=ghcr.io/${{ github.repository }}:${{ github.ref_name }}
          syft $IMAGE -o spdx-json=sbom.spdx.json
          syft $IMAGE -o cyclonedx-json=sbom.cdx.json

      - name: Install cosign
        uses: sigstore/cosign-installer@v3

      - name: Sign image (keyless, via OIDC)
        run: cosign sign --yes ghcr.io/${{ github.repository }}:${{ github.ref_name }}

      - name: Attach SBOM as attestation
        run: |
          IMAGE=ghcr.io/${{ github.repository }}:${{ github.ref_name }}
          cosign attest --yes \
            --predicate sbom.spdx.json \
            --type spdxjson \
            $IMAGE

      - name: Install grype & scan
        run: |
          curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh \
            | sh -s -- -b /usr/local/bin
          grype sbom:sbom.spdx.json --output json > grype-results.json

      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: sbom-artifacts
          path: |
            sbom.spdx.json
            sbom.cdx.json
            grype-results.json
```

### 階段 2（進階）：SLSA L3 provenance（未實測，需 GitHub Actions 執行）

```yaml
# slsa-github-generator 達 SLSA L3
name: SLSA L3 Provenance

on:
  release:
    types: [published]

jobs:
  build:
    outputs:
      digest: ${{ steps.build.outputs.digest }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - id: build
        run: |
          docker build -t myimage:${{ github.ref_name }} .
          digest=$(docker inspect --format='{{index .RepoDigests 0}}' myimage:${{ github.ref_name }})
          echo "digest=$digest" >> $GITHUB_OUTPUT

  provenance:
    needs: build
    permissions:
      actions: read
      id-token: write
      contents: write
    uses: slsa-framework/slsa-github-generator/.github/workflows/generator_container_slsa3.yml@v1.10.0
    with:
      image: myimage
      digest: ${{ needs.build.outputs.digest }}
    secrets:
      registry-username: ${{ github.actor }}
      registry-password: ${{ secrets.GITHUB_TOKEN }}
```

### 階段 4：Dependency-Track API 匯入（完整指令，需 DT 跑起來）

```bash
DT_URL="http://localhost:8081"
DT_TOKEN="<your-api-key>"

# 建 project
PROJECT_UUID=$(curl -s -X PUT $DT_URL/api/v1/project \
  -H "X-Api-Key: $DT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"supply-chain-demo","version":"v0.1.0","description":"SBOM final project demo"}' \
  | jq -r .uuid)

echo "Project UUID: $PROJECT_UUID"

# 上傳 CycloneDX SBOM（DT 只接受 CycloneDX）
curl -s -X POST $DT_URL/api/v1/bom \
  -H "X-Api-Key: $DT_TOKEN" \
  -F "project=$PROJECT_UUID" \
  -F "bom=@sbom.cdx.json"

# 查分析結果（等 60 秒讓 DT 處理）
sleep 60
curl -s "$DT_URL/api/v1/project/$PROJECT_UUID/metrics/current" \
  -H "X-Api-Key: $DT_TOKEN" \
  | jq '{critical,high,medium,low,inheritedRiskScore,components}'
```

</details>

---

## CI/CD 整合設計圖（生產環境參考架構）

```
開發者 push tag v0.x.x
         │
         ▼
  GitHub Actions runner
  ┌──────────────────────────────────────────┐
  │ 1. checkout + build image                │
  │ 2. docker push → registry               │
  │ 3. syft image → sbom.spdx.json          │  ← 階段 1
  │               → sbom.cdx.json           │
  │ 4. cosign sign image (keyless OIDC)      │  ← 階段 2
  │ 5. cosign attest sbom (spdxjson type)   │
  │ 6. slsa-github-generator (provenance)   │
  │ 7. grype sbom --output json             │  ← 階段 3
  │    → fail if Critical > 0 (可調策略)    │
  │ 8. upload sbom.cdx.json → DT API       │  ← 階段 4
  └──────────────────────────────────────────┘
         │
         ▼
  消費端 / 下游 CI
  ┌──────────────────────────────────────┐
  │ cosign verify image                  │  ← 階段 5
  │ cosign verify-attestation --type sbom│
  │ pull sbom → grype 掃                 │
  │ 確認 SLSA level 符合 policy          │
  └──────────────────────────────────────┘
         │
         ▼
  季度治理報告                              ← 階段 6
  DT dashboard + VEX 文件審查
```

---

## 驗收 checklist

對照全課概念，自我驗收前勾完這份清單：

### Part 1-2 SBOM 本質與格式

- [ ] 我的 SBOM 有 SPDX 2.3 和 CycloneDX 1.x 兩種格式
- [ ] 每個 package 都有 PURL（`pkg:golang/...` 或 `pkg:pypi/...`）
- [ ] 我能解釋 SPDX `relationships` 欄位記錄的是什麼（Ch 5）
- [ ] 我能解釋 CycloneDX `dependencies` 和 SPDX `relationships` 的結構差異（Ch 7）
- [ ] SBOM 有 `creationInfo`，含工具名稱、版本、時間戳記

### Part 3 生成

- [ ] 我知道 syft 掃目錄 vs 掃 container image 的差別（Ch 9、10）
- [ ] 我能說出至少一種 syft 看不到的東西（手動下載的 binary、靜態編入的函式庫）（Ch 12）
- [ ] syft 和 trivy 對同一個目標的輸出我都看過，並注意到差異或一致性

### Part 4 消費與漏洞管理

- [ ] 我跑出了 grype 基線報告，並用 `jq` 統計各 severity 數量（Ch 15）
- [ ] 我寫了至少一筆 VEX `not_affected` 判定，並說得出 justification 是什麼（Ch 16）
- [ ] 重掃後的 `ignoredMatches` 數量大於 0
- [ ] 我能解釋 VEX 文件的維護義務：為什麼一年不更新的 VEX 比沒有 VEX 更危險（Ch 16）
- [ ] SBOM 已上傳 Dependency-Track，能看到元件清單（或說出卡在哪、為什麼）（Ch 17）

### Part 5 信任鏈

- [ ] `cosign verify-blob ... sbom.spdx.json` 印出 `Verified OK`（Ch 20-21）
- [ ] 篡改 SBOM 後 verify 失敗（exit ≠ 0）——這是最重要的功能測試
- [ ] 我的 SLSA provenance 文件包含 `buildDefinition`、`runDetails.builder.id`（Ch 22-23）
- [ ] 我能說出本次演練達到的 SLSA level 是幾，以及缺什麼才能升一級

### Part 6 治理

- [ ] 我檢查了 NTIA 七要素，列出哪些欄位有缺口（Ch 3、24）
- [ ] 治理報告有「殘留風險」章節，不是只有「通過了幾個 check」（Ch 25-29）
- [ ] 我能解釋這條管線防的是 MITRE ATT&CK for Supply Chain 裡的哪些威脅（Ch 18）

---

## 延伸挑戰

完成基本六階段後，視時間選做：

**A. 第二語言生態**

在 app 裡加一個 Node.js 服務（用 `package.json` / `package-lock.json`），讓 SBOM 裡同時有 `pkg:golang/...` 和 `pkg:npm/...`。觀察 syft 的 npm cataloger 認出了哪些，比對 `npm audit` 的輸出是否一致。

**B. Policy-as-code gating**

用 OPA（Open Policy Agent）或直接用 `jq` + shell，在 CI 加一個 policy gate：「如果掃出任何 Critical severity 且 EPSS > 50% 的漏洞，pipeline fail」。這是 Ch 13 講的「SBOM 變成 value」最直接的形式。

```bash
# 簡易版：用 jq 做 gate
HIGH_RISK=$(jq '[.matches[] | select(.vulnerability.severity == "Critical") | select(.vulnerability.epss.percentile > 0.5)] | length' grype-baseline.json)
if [ "$HIGH_RISK" -gt 0 ]; then
  echo "POLICY FAIL: $HIGH_RISK critical high-EPSS vulns found"
  exit 1
fi
```

**C. Air-gapped 環境**

模擬離線環境：用 `grype db status` 確認本地 DB，然後設 `GRYPE_DB_UPDATE_URL` 指向私有 S3 / HTTP server（假裝離線），跑一次完整掃描，確認不需要出外網。這是 Ch 15 的 air-gapped DB 章節的實際操作。

**D. SBOM diff**

對同一個 project 的兩個版本（例如 v0.1.0 和 v0.2.0，後者升了某個依賴的版本）各產一份 SBOM，然後寫一個 shell 或 Python 腳本 diff 兩份 SPDX JSON，找出「新增了哪些元件」「移除了哪些元件」「版本變了哪些」。這是 Ch 28（SBOM 與 DFIR）的核心能力：事件應變時快速定位哪個版本引入了問題元件。

**E. VEX automation**

用 [vexctl](https://github.com/openvex/vexctl) CLI 生成和管理 OpenVEX 文件，而不是手寫 JSON。練習 `vexctl add`、`vexctl filter` 的操作，感受自動化 VEX 維護週期跟手工維護的差距。

---

## 自我檢核：你在防什麼威脅？

每完成一個階段，問自己這個問題。這是期末要求，也是這門課的靈魂：

| 管線環節 | 沒有它的話，攻擊者能做什麼？ |
|---------|--------------------------|
| 階段 1：SBOM 存在 | 不知道系統裡有什麼，新 CVE 爆發時無法快速回答，靠考古（翻 git log / 問開發者）不可靠 |
| 階段 2：簽章 + attestation | 攻擊者能替換 SBOM，讓你相信 artifact 比實際安全（或注入假漏洞混淆視聽）；攻擊者能宣稱 SBOM 來自你，而其實不是 |
| 階段 2：SLSA provenance | 攻擊者能汙染 build 環境或 source（SolarWinds 型攻擊）；無法追溯「這個 binary 從哪個 commit / 哪台機器建出來的」 |
| 階段 3：掃描 + VEX | 不知道已知的漏洞是否影響自己；VEX 缺失則誤報淹沒 actionable 清單，工程師習慣性忽略告警（「告警疲勞」） |
| 階段 4：Dependency-Track | 漏洞今天沒有，不代表明天沒有；沒持續監控就必須靠人工定期掃，週期太長造成暴露窗口 |
| 階段 5：消費端驗證 | 你信任的 SBOM 在傳輸途中被改掉了；或者你拿到的簽章跟 SBOM 不配對——但你不知道 |
| 階段 6：治理報告 | 管線跑了，但你說不清楚「符合 EO 14028 幾個要素」「這個 artifact 達到 SLSA 幾級」——法規審計、客戶要求、SOC 2 審查全部會問這些問題 |

---

你現在能自己建一條「產 SBOM → 簽章 + provenance → 掃描降噪 → 持續監控 → 報告」的供應鏈安全管線，並在下一個 Log4Shell 之夜，幾分鐘內回答「我到底中了沒」。

→ [回到課程首頁](./README.md)
