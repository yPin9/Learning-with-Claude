# Ch 19 — 映像掃描

> 目標：搞清楚為什麼 image 裡一堆 CVE 是常態，學會用 trivy 和 docker scout 掃描並解讀結果，以及在 CI 裡阻擋有 CRITICAL 漏洞的 image 進入生產。

---

## 為什麼要掃描

你的 Dockerfile 可能只寫了幾行，但你繼承的 base image 裡可能有幾十個 CVE（Common Vulnerabilities and Exposures，通用漏洞披露）：

```bash
docker pull node:18
docker run --rm node:18 apt list --installed 2>/dev/null | wc -l
# 幾百個套件，每一個都可能有已知漏洞
```

不掃不知道，一掃嚇一跳。`node:18`（Debian base）歷史上常有 50+ 個 CVE，其中不乏 HIGH 等級。換 `node:18-alpine` 通常降到個位數。

---

## trivy（最推薦的掃描工具）

trivy（Aqua Security 開源）是目前功能最完整、最好用的 container 安全掃描工具。它的漏洞資料庫包含 OS 套件（Debian/Alpine/Ubuntu/RHEL）、語言套件（npm/pip/gem/go/cargo）、以及設定檔（Dockerfile/compose.yml/k8s manifest）。

**安裝**：

```bash
# macOS
brew install trivy

# Debian/Ubuntu
sudo apt-get install wget apt-transport-https gnupg lsb-release
wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | sudo apt-key add -
echo "deb https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc) main" \
    | sudo tee /etc/apt/sources.list.d/trivy.list
sudo apt-get update && sudo apt-get install trivy

# 或直接用 docker 跑
docker run --rm aquasec/trivy image nginx:1.25-alpine
```

---

## 掃描 image

```bash
# 掃描 image，輸出所有漏洞
trivy image nginx:1.25-alpine

# 只顯示 HIGH 和 CRITICAL
trivy image --severity HIGH,CRITICAL nginx:1.25-alpine

# 輸出成 JSON（CI 處理用）
trivy image --format json --output result.json nginx:1.25-alpine

# 掃描本地建好的 image（不需要推到 registry）
docker build -t myapp:dev .
trivy image myapp:dev
```

---

## 解讀 trivy 輸出

```
nginx:1.25-alpine (alpine 3.18.4)
==================================
Total: 3 (HIGH: 1, MEDIUM: 2)

+-------------------+---------------+----------+-------------------+------------------+
| Library           | Vulnerability | Severity | Installed Version | Fixed Version    |
+-------------------+---------------+----------+-------------------+------------------+
| libssl3           | CVE-2023-5363 | HIGH     | 3.1.3-r0          | 3.1.4-r0         |
| libcrypto3        | CVE-2023-5363 | HIGH     | 3.1.3-r0          | 3.1.4-r0         |
| busybox           | CVE-2023-42363| MEDIUM   | 1.36.1-r2         | 1.36.1-r5        |
+-------------------+---------------+----------+-------------------+------------------+
```

重要欄位：

| 欄位 | 意義 |
|------|------|
| Library | 哪個套件有漏洞 |
| Vulnerability | CVE ID，在 NVD 或 MITRE 可查詳情 |
| Severity | CRITICAL / HIGH / MEDIUM / LOW / UNKNOWN |
| Installed Version | 你 image 裡的版本 |
| Fixed Version | 修復版本，空白表示目前沒有修復 |

Severity 等級：

| 等級 | 含義 | 處理方式 |
|------|------|---------|
| CRITICAL | 嚴重，通常可遠端執行代碼 | 必修，阻擋 deploy |
| HIGH | 高危，可能提權或資料洩漏 | 應修，盡快升級 |
| MEDIUM | 中危 | 排入修復計劃 |
| LOW | 低危 | 可接受，記錄在案 |
| UNKNOWN | 資訊不足 | 人工評估 |

---

## 掃描 Dockerfile 和 compose.yml（靜態分析）

trivy 不只掃 image，也做靜態設定分析：

```bash
# 掃描 Dockerfile（不需要 build image）
trivy config ./Dockerfile

# 掃描 compose.yml
trivy config ./compose.yml

# 掃描整個目錄裡的設定檔
trivy config .
```

靜態分析會找出：

- Dockerfile 用 `root` 使用者
- 沒有 `HEALTHCHECK`
- `apt-get install` 沒有固定版本
- 敏感資料（密碼、token）硬寫在 Dockerfile 裡
- compose.yml 裡的 `privileged: true`

---

## docker scout（Docker 官方整合）

`docker scout` 是 Docker Desktop 和 Docker CLI 內建的掃描工具（需要登入 Docker Hub）：

```bash
# 掃描 CVE
docker scout cves nginx:1.25-alpine

# 比較兩個版本的漏洞差異
docker scout compare nginx:1.24-alpine nginx:1.25-alpine

# 建議換哪個 base image（很實用）
docker scout recommendations nginx:1.25-alpine
```

`recommendations` 指令會建議類似功能但 CVE 更少的 base image tag，省掉手動比較的時間。

trivy 和 docker scout 用不同的漏洞資料庫，掃出的結果可能略有差異，建議以 trivy 為主（離線可用、CI 整合更成熟）。

---

## 修 CVE 的策略

| 策略 | 什麼時候有效 | 說明 |
|------|------------|------|
| 換更新的 tag | 最常見 | `nginx:1.25` 改成 `nginx:1.26`，或改用 `nginx:mainline-alpine` |
| 換 alpine base | 很有效 | Alpine 用 musl libc，套件少，CVE 通常少很多 |
| 在 Dockerfile 升級套件 | 有時有效 | `RUN apk upgrade --no-cache` 或 `apt-get upgrade`，但不是所有 CVE 都有修復版本 |
| Distroless image | 最乾淨 | `gcr.io/distroless/base`，幾乎沒有 OS 套件，CVE 趨近於零 |
| Accept risk | MEDIUM/LOW | 記錄在案，CVE 沒有修復版本或攻擊面不存在就接受 |

`apk upgrade` 在 Dockerfile 裡的寫法：

```dockerfile
FROM nginx:1.25-alpine
RUN apk upgrade --no-cache
```

注意這會讓 image 不可重現（每次 build 升的版本不同），在需要精確重現的環境是個問題。

---

## CI 整合：CRITICAL 就擋下來

在 CI pipeline 裡掃描，有 CRITICAL 就讓 build 失敗，不讓問題進入生產：

```bash
# --exit-code 1 表示有漏洞時回傳 exit code 1，讓 CI 失敗
trivy image \
    --exit-code 1 \
    --severity CRITICAL \
    --ignore-unfixed \
    myapp:${COMMIT_SHA}

# --ignore-unfixed 跳過沒有修復版本的 CVE（避免被卡死）
```

GitHub Actions 範例：

```yaml
- name: Run Trivy vulnerability scanner
  uses: aquasecurity/trivy-action@master
  with:
    image-ref: myapp:${{ github.sha }}
    format: sarif
    output: trivy-results.sarif
    severity: CRITICAL,HIGH
    exit-code: 1
    ignore-unfixed: true

- name: Upload Trivy scan results to GitHub Security tab
  uses: github/codeql-action/upload-sarif@v2
  with:
    sarif_file: trivy-results.sarif
```

SARIF 格式可以直接上傳到 GitHub Security tab，在 PR 裡看到漏洞報告。

---

## 自我檢核

- [ ] 能用 trivy 掃一個 image，並解讀輸出裡的 Library / CVE ID / Fixed Version 欄位
- [ ] 知道 alpine base image 通常 CVE 比 Debian 少的原因
- [ ] 能用 `trivy config` 掃 Dockerfile，知道它在找哪類問題
- [ ] 知道 `--ignore-unfixed` 旗標在 CI 裡為什麼有時是必要的
- [ ] 能寫一條在 CI 裡遇到 CRITICAL CVE 就讓 pipeline 失敗的 trivy 指令

下一章講簽名，解決「你怎麼確認這個 image 是你自己 build 的，沒有被人動過」的問題。

→ [Ch 20 映像簽名](./20-image-signing.md)
