# Ch 32 — 供應鏈防護：SBOM / cosign / SLSA / admission 驗簽

> **目標**：把 Ch 31 的攻擊面轉為防禦落地——產 SBOM、用 cosign 簽 image、理解 SLSA 框架的 provenance 概念、在 K8s 的 admission 層強制驗簽。重點是每個工具「能擋哪些攻擊、擋不了哪些」，而不是走完 CLI 手冊。
>
> **環境**：cosign v2.x（`brew install cosign` / `go install github.com/sigstore/cosign/v2/cmd/cosign@latest`）、syft v1.x（`brew install syft` / Scoop）、trivy v0.54+。admission 部分的 policy-controller 和 Kyverno 需要 K8s cluster，標記未實測的段落請見本段說明。

Ch 31 把 CI/CD 的五個攻擊面拆完了。攻擊者的共同手法是：在你執行的 code/image 路徑上植入惡意內容，而你的 pipeline 完全不知情。防禦的核心邏輯只有一句話：**對每個你會執行的 artifact 建立可驗證的信任鏈，執行前驗證。**

---

## 為什麼需要

傳統的「掃描」是必要條件，但不是充分條件：

- `trivy image myapp:latest`：掃描的是 image 的現在狀態，不驗證這個 image 是不是你預期的 build 產物
- 如果 image 在 registry 到 K8s pull 之間被替換了，trivy 掃了也沒用
- 如果 CI 產生 image 的步驟本身被投毒（Ch 31 的 D-PPE），掃描的是惡意 image

供應鏈防護解決的是更根本的問題：**這個 artifact 是從哪裡來的、怎麼 build 的、沒有被篡改嗎？**

對照攻擊：

| Ch 31 攻擊 | 供應鏈防護如何擋 |
|---|---|
| 惡意 image 被推上 registry 取代正版 | cosign 驗簽：沒有簽章或簽章不匹配 → 拒絕 |
| CI 被投毒，產生帶後門的 image | SLSA provenance：可以驗證 image 是從哪個 git commit 建的、在哪個 CI job 建的 |
| 第三方 action 被換成惡意版本 | action pin SHA + cosign 驗簽 action |
| typosquatting base image | digest pin + registry policy（只允許特定 registry 的 image）|

---

## 先建直覺：信任鏈的四層

```
程式碼（source code）
    │  ← SLSA provenance 把這條連結記錄成可驗證的聲明
    ▼
建構過程（CI build）
    │  ← cosign 在 build 完成後簽章
    ▼
Artifact（image / binary / package）
    │  ← SBOM 列出 artifact 裡的所有元件
    ▼
執行環境（K8s cluster / runtime）
       ← admission controller 在這層驗簽才放行
```

每一層都有對應的工具和機制：
- **Source → Build**：SLSA（Software Artifacts Levels for Supply Chain Assurance，供應鏈保證等級）定義了 build process 必須滿足什麼條件才算可信
- **Build → Artifact**：cosign/sigstore 在 artifact 上附加密碼學簽章
- **Artifact 的組成**：SBOM（Software Bill of Materials，軟體元件清單）列出所有依賴
- **Artifact → 執行**：admission controller 強制要求有效簽章才允許啟動

---

## 底層機制

### SBOM（軟體元件清單）

SBOM 回答的問題是：**這個 image/binary 裡有什麼？**

有兩個主流格式：

**SPDX**（Software Package Data Exchange，軟體套件資料交換格式）：Linux Foundation 主導，ISO 標準（ISO/IEC 5962:2021），比較成熟

**CycloneDX**：OWASP 主導，JSON/XML，工具生態支援廣，適合和漏洞資料庫整合

兩者都能表達同樣的資訊：套件名稱、版本、授權、依賴關係、雜湊值。選哪個看你的工具鏈，多數工具兩種都能產。

SBOM 的實際用途：
1. 建立 inventory（我的 image 裡有哪些元件）
2. 新 CVE 出來時快速查詢是否受影響（`log4j` 事件時有 SBOM 的公司兩小時內知道自己中沒中）
3. 授權合規（SBOM 列出所有元件的授權，自動比對 policy）

### cosign / sigstore

`cosign` 是 Sigstore 計畫（Linux Foundation 旗下）的主要工具，做 image 和 artifact 的密碼學簽章。

**Keyless OIDC 簽章**是 cosign v2 的預設模式，不需要管理 signing key：

```
CI workflow 執行 cosign sign
        │
        │ 1. cosign 向 Fulcio（Sigstore 的 CA）請求短期 signing certificate
        │    Fulcio 驗證 GitHub OIDC token（workflow 的身份）
        │    Fulcio 簽出一個包含 workflow identity 的 X.509 cert（10 分鐘有效期）
        │
        ▼
cosign 用這個 cert 簽 image
        │
        │ 2. cosign 把簽章和 cert 上傳到 Rekor（Sigstore 的 transparency log）
        │    Rekor 是 append-only 的公開日誌，類似 Certificate Transparency
        │
        ▼
任何人可以執行 cosign verify 驗證：
  - 簽章是否有效
  - 簽出這個 image 的 workflow 是什麼（github.com/myorg/myrepo 的哪個 workflow）
  - 簽章記錄在 Rekor log 裡（防止 signing key 偷偷簽了什麼）
```

Keyless 的優點：沒有 long-lived signing key 需要保護，signing identity 和 CI workflow 綁定（而不是一個任何人都能偷的 key file）。

### SLSA framework

SLSA（發音 "salsa"）定義了 build 可信度的四個等級：

```
SLSA Level 0：不符合任何要求（相當於沒有 SLSA）
         │
SLSA Level 1：build 過程有文件、產生 provenance（不需要簽章）
         │    攻擊者能偽造 provenance，但至少有 audit trail
         │
SLSA Level 2：build 服務是 hosted（不是本機 build），provenance 由 build service 簽章
         │    GitHub Actions / Google Cloud Build 可達 Level 2
         │    攻擊者需要入侵 CI 服務才能偽造
         │
SLSA Level 3：build 過程 hermetic（隔離，不能存取外部網路），source 是受版本控制的
         │    更難投毒，因為 build 環境被隔離
         │
SLSA Level 4（原規格，新版改為 3）：雙人 review + hermetic build
              最嚴格，等於在整個 build process 加密碼學保證
```

對大多數工程團隊，SLSA Level 2 是現實可行的起點（GitHub Actions + cosign 就能達到）。

**Provenance**（出處記錄）是 SLSA 的核心輸出：一份說明「這個 artifact 是從哪個 git commit、由哪個 CI job 的哪個 step、在什麼時間點建的」的簽章文件。

---

## 具體範例

### 範例一：syft 產 SBOM

```bash
# 安裝 syft（macOS）
brew install syft

# 安裝 syft（Linux / WSL）
curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh | sh -s -- -b /usr/local/bin

# 對 image 產 SBOM（CycloneDX JSON 格式）
syft nginx:1.25 -o cyclonedx-json > sbom-nginx.json

# 對 image 產 SBOM（SPDX JSON 格式）
syft nginx:1.25 -o spdx-json > sbom-nginx.spdx.json

# 對本地目錄產 SBOM（掃原始碼依賴）
syft dir:. -o cyclonedx-json > sbom-source.json
```

**本段為理論說明，實際輸出格式依版本不同。** syft 對 nginx:1.25 的典型輸出結構（節錄）：

```json
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.5",
  "metadata": {
    "timestamp": "2024-10-01T12:00:00Z",
    "component": {
      "name": "nginx",
      "version": "1.25.3",
      "type": "container"
    }
  },
  "components": [
    {
      "type": "library",
      "name": "libc6",
      "version": "2.31-13+deb11u8",
      "purl": "pkg:deb/debian/libc6@2.31-13+deb11u8"
    },
    {
      "type": "library",
      "name": "openssl",
      "version": "1.1.1w-0+deb11u1",
      "purl": "pkg:deb/debian/openssl@1.1.1w-0+deb11u1"
    }
    // ... 數十到數百個套件
  ]
}
```

`purl`（Package URL）是跨生態系的套件識別格式，`pkg:deb/debian/libc6@2.31-13+deb11u8` 能被 OSV（Open Source Vulnerabilities，開源漏洞資料庫）、GitHub Advisory Database 等漏洞資料庫直接查詢。

SBOM + trivy 的組合工作流：

```bash
# 先產 SBOM，再用 SBOM 做 CVE 掃描（不需要再 pull image）
syft nginx:1.25 -o cyclonedx-json > sbom.json
trivy sbom sbom.json --severity HIGH,CRITICAL
```

### 範例二：cosign sign / verify

前提：CI 環境是 GitHub Actions（keyless 模式的 OIDC token 由 Actions 提供）。

**CI workflow 裡的 signing 步驟：**

```yaml
# .github/workflows/build-and-sign.yml
on:
  push:
    branches: [main]

permissions:
  contents: read
  id-token: write    # 必須：让 cosign 取 OIDC token
  packages: write    # push image 到 ghcr.io

jobs:
  build-sign:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2

      - name: Log in to GHCR
        uses: docker/login-action@9780b0c442fbb1117ed29e0efdff1e18412f7567  # v3.3.0
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push image
        id: build
        uses: docker/build-push-action@4f58ea79222b3b9dc2c8bbdd6debcef730109a75  # v6.9.0
        with:
          push: true
          tags: ghcr.io/${{ github.repository }}:latest
          # 記錄 digest 供簽章使用

      - name: Install cosign
        uses: sigstore/cosign-installer@dc72c7d5c4d10cd6bcb8cf6e3fd625a9e5e537da  # v3.7.0

      - name: Sign image with keyless OIDC
        run: |
          cosign sign --yes \
            ghcr.io/${{ github.repository }}@${{ steps.build.outputs.digest }}
        # --yes：接受 Sigstore TUF 根憑證（生產環境用 --key 更好）
        # 用 digest 而非 tag：確保簽的是這個特定 image，不是 latest 這個可變 tag
```

**驗證 image 的簽章：**

```bash
# 驗證 image 是從 github.com/myorg/myrepo 的 workflow 簽出的
cosign verify \
  --certificate-identity-regexp "https://github.com/myorg/myrepo/.github/workflows/build-and-sign.yml@refs/heads/main" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/myorg/myrepo@sha256:abc123...

# 預期成功輸出（簡化）：
# Verification for ghcr.io/myorg/myrepo@sha256:abc123... --
# The following checks were performed on each of these signatures:
#   - The cosign claims were validated
#   - Existence of the claims in the transparency log was verified
#   - The code-signing certificate was verified using trusted certificate authority
# [{"critical":{"identity":{"docker-reference":"ghcr.io/myorg/myrepo"},...}]
```

**失敗案例：簽章不匹配**

```bash
# 如果有人在 CI 之外重新 build 並 push（覆蓋了簽過的 image）：
cosign verify \
  --certificate-identity-regexp "https://github.com/myorg/myrepo/.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/myorg/myrepo:latest

# 輸出：
# Error: no matching signatures:
# verifying signature
# [WARN] ⚠️  This can happen if:
# 1. The image was not signed by the expected identity
# 2. The image was updated after signing (tag moved to different digest)
# 3. The certificate has expired (cosign certs expire in 10 minutes after signing)
```

這個錯誤告訴你：有一個「latest」tag 但沒有對應的有效簽章——可能是 image 被替換了，或者是在 CI 之外 build 並 push 的。

**本段部分內容為理論預期行為**，cosign 的具體錯誤訊息格式在各版本間有差異；自驗方法：在你的 CI 環境跑 `cosign sign` 後立即 `cosign verify`，再手動 push 一個沒簽名的 image 驗證錯誤路徑。

### 範例三：K8s admission 驗簽（Kyverno verifyImages）

**本段為理論預期行為，需要實際 K8s cluster 驗證。** 自驗方法：用 `kind create cluster` 或 minikube 建本地 cluster，安裝 Kyverno v1.11+，套用以下 policy 後嘗試 deploy 未簽名 image。

安裝 Kyverno：

```bash
kubectl create -f https://github.com/kyverno/kyverno/releases/download/v1.11.4/install.yaml
```

建立驗簽 policy：

```yaml
# kyverno-verify-images.yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: verify-image-signatures
spec:
  validationFailureAction: Enforce   # Enforce 拒絕，Audit 只記錄
  background: false
  rules:
    - name: verify-cosign-signature
      match:
        any:
          - resources:
              kinds:
                - Pod
              namespaces:
                - production    # 只在 production namespace 執行
      verifyImages:
        - imageReferences:
            - "ghcr.io/myorg/*"    # 只驗這個 registry 的 image
          attestors:
            - count: 1
              entries:
                - keyless:
                    subject: "https://github.com/myorg/myrepo/.github/workflows/build-and-sign.yml@refs/heads/main"
                    issuer: "https://token.actions.githubusercontent.com"
                    rekor:
                      url: https://rekor.sigstore.dev    # Sigstore 的公開 transparency log
          mutateDigest: true    # 自動把 tag 換成 digest（防止 tag 被替換）
          verifyDigest: true    # 確保 Pod spec 裡用的是 digest 而非 tag
```

套用 policy：

```bash
kubectl apply -f kyverno-verify-images.yaml
```

測試拒絕行為（部署沒有簽名的 image）：

```bash
kubectl -n production run test \
  --image=nginx:latest \
  --restart=Never

# 預期輸出：
# Error from server: admission webhook "mutate.kyverno.svc.cluster.local" denied the request:
# resource Pod/production/test was blocked due to the following policies
# verify-image-signatures:
#   verify-cosign-signature: .spec.containers[0].image:
#     image nginx:latest not verified; signature check failed
```

**Sigstore policy-controller**（另一個選項，Sigstore 官方出的 admission controller）：

```bash
# 安裝 policy-controller
helm repo add sigstore https://sigstore.github.io/helm-charts
helm install policy-controller sigstore/policy-controller \
  --namespace cosign-system \
  --create-namespace
```

```yaml
# ClusterImagePolicy（policy-controller 的 CRD）
apiVersion: policy.sigstore.dev/v1beta1
kind: ClusterImagePolicy
metadata:
  name: require-signature
spec:
  images:
    - glob: "ghcr.io/myorg/**"
  authorities:
    - keyless:
        url: https://fulcio.sigstore.dev
        identities:
          - issuer: https://token.actions.githubusercontent.com
            subject: https://github.com/myorg/myrepo/.github/workflows/*.yml@refs/heads/main
```

---

## 對比取捨表

| 工具 | 解決的問題 | 不解決的問題 | 適合場景 |
|---|---|---|---|
| SBOM（syft）| 知道 image 裡有什麼 | 不驗証 image 來源是否可信 | inventory + 漏洞查詢 |
| cosign sign | 提供 image 來源的密碼學保證 | 不保證 build 過程沒被投毒 | image 完整性驗證 |
| SLSA provenance | 記錄 build 過程，可驗証 | 本身不阻止執行 | 審計、complience |
| admission 驗簽（Kyverno）| 在 K8s 層強制執行 policy | 覆蓋不到 K8s 之外的執行（Lambda/EC2）| K8s 環境 |
| registry policy（ECR/GHCR）| 在 push 層拒絕不符合 policy 的 image | 覆蓋不到自架 registry | 雲端 registry 場景 |

| 簽章方式 | 管理成本 | 安全性 | 適合場景 |
|---|---|---|---|
| Keyless OIDC（cosign v2 預設）| 低：無 key 需管理 | 高：binding 到 CI identity | GitHub Actions / Google Cloud Build |
| 靜態 key（cosign --key）| 中：key 需要輪換和保護 | 中：key 洩漏 = 任何人都能簽 | 有 key management 基礎設施 |
| KMS key（AWS KMS / GCP KMS）| 高：需要 KMS 設定 | 最高：private key 永不離開 HSM | 高安全性要求的生產環境 |

---

## 踩雷集錦

**1. cosign sign 用 tag，日後 tag 移動後 verify 失敗**

```bash
# 危險：對 tag 簽章
cosign sign ghcr.io/myorg/myrepo:latest

# 之後 latest 更新，指向新 digest，舊簽章失效
# 應該用 digest 簽：
cosign sign ghcr.io/myorg/myrepo@sha256:abc123...
```

Kyverno 的 `mutateDigest: true` 會自動把 Pod spec 裡的 tag 換成 digest，但 cosign sign 本身需要手動用 digest。CI 的最佳實踐是在 build push 後取得 digest，再用 digest 簽（範例二有示範）。

**2. SBOM 產了但沒有簽章，任何人都能偽造**

SBOM 本身是一個 JSON/XML 文件，如果沒有簽章，攻擊者可以提供一個假的 SBOM 宣稱「這個 image 沒有 CVE」。syft + cosign 的標準做法是把 SBOM 作為 attestation 附加到 image 上：

```bash
# 產 SBOM
syft nginx:1.25 -o cyclonedx-json > sbom.json

# 把 SBOM 作為 attestation 附加到 image（同時簽章）
cosign attest --yes \
  --predicate sbom.json \
  --type cyclonedx \
  ghcr.io/myorg/myrepo@sha256:abc123...

# 驗證 SBOM attestation
cosign verify-attestation \
  --type cyclonedx \
  --certificate-identity-regexp ".*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/myorg/myrepo@sha256:abc123...
```

**3. Kyverno 的 `mutateDigest` 在 init containers 和 ephemeral containers 不一定生效**

Kyverno 的 `verifyImages` 規則預設只處理 `containers` 欄位，`initContainers` 和 `ephemeralContainers` 需要明確在 rule 裡指定，否則攻擊者可以用 initContainer 跑未簽名的 image，main container 有簽名照樣過驗證。

**4. Rekor 的 transparency log 是公開的**

Sigstore 的 Rekor log 是公開的——任何人都能查到你的 image 的簽章記錄，包括 signing identity（哪個 GitHub org/repo 的哪個 workflow 簽的）。如果你的 internal tooling 不想暴露這些資訊，需要 self-host Rekor（cosign 支援 `--rekor-url` 指定私有 instance）。

**5. SLSA 的等級不代表「code 本身安全」**

SLSA Level 3 保證的是 build process 的可信度，不保證 source code 本身沒有惡意邏輯。SolarWinds 的案例中，攻擊者直接在 build server 上做手腳，SLSA 的 source 保護無法阻止 build server 本身被入侵。SLSA 是必要條件之一，不是萬靈丹。

---

## 進階延伸

### SLSA provenance 自動產生

GitHub Actions 的 `slsa-framework/slsa-github-generator` 可以自動產生 SLSA Level 3 的 provenance：

```yaml
jobs:
  build:
    outputs:
      hashes: ${{ steps.hash.outputs.hashes }}
    steps:
      - uses: actions/checkout@...
      - name: Build
        run: make build
      - name: Hash artifacts
        id: hash
        run: |
          echo "hashes=$(sha256sum myapp | base64 -w0)" >> "$GITHUB_OUTPUT"

  provenance:
    needs: [build]
    permissions:
      actions: read
      id-token: write
      contents: write
    uses: slsa-framework/slsa-github-generator/.github/workflows/generator_generic_slsa3.yml@v2.0.0
    with:
      base64-subjects: "${{ needs.build.outputs.hashes }}"
```

這個 generator 是由 Google SLSA 團隊維護的，產出的 provenance 包含：build invocation 的 git commit SHA、workflow 名稱、runner 環境、build 時間，並由 Fulcio 簽章。

### 依賴鎖定（lockfile 策略）

供應鏈防護不只在 image 層，npm/pip/Go module 的依賴鎖定也是關鍵：

```bash
# npm：package-lock.json 鎖定完整的依賴樹
npm ci  # 嚴格按照 lockfile 安裝，不允許升版

# pip：requirements.txt + hash 鎖定
pip install --require-hashes -r requirements.txt

# Go：go.sum 鎖定所有 module 的 hash
go mod verify  # 驗證 go.sum 裡的 hash 和實際下載的匹配
```

`npm ci` 和 `npm install` 的差別：`npm ci` 在 lockfile 存在時嚴格遵守，任何不匹配都會報錯退出；`npm install` 在 lockfile 不匹配時會自動更新。CI 必須用 `npm ci`。

---

## 本章重點整理

- 供應鏈防護的核心是對 artifact 建立可驗證的信任鏈：source → build → artifact → 執行
- SBOM 回答「artifact 裡有什麼」，syft 支援 SPDX 和 CycloneDX 兩種格式；SBOM 應該和 image 一起用 cosign 簽章
- cosign keyless 模式不需要管理 signing key，簽章 identity 綁定到 CI workflow；應用 digest 而非 tag 簽章
- SLSA 框架定義 build 可信度等級；GitHub Actions + cosign 可達 Level 2，加 hermetic build 可達 Level 3
- Kyverno `verifyImages` 和 Sigstore policy-controller 在 K8s admission 層強制驗簽，沒有有效簽章的 image 無法啟動
- 四個踩雷：用 tag 簽（應用 digest）、SBOM 沒簽章（應附加 attestation）、忽略 init containers、Rekor 是公開 log
- 這些工具是分層防禦，不是互斥的——SBOM + cosign + admission 驗簽是推薦的最小 baseline

---

## 自我檢核

1. SBOM 的 SPDX 和 CycloneDX 格式的核心差異是什麼？各自適合哪些工具鏈？
2. cosign keyless 簽章的流程是什麼？Fulcio 在這個流程裡做什麼？
3. 為什麼 cosign sign 應該用 digest 而不是 tag？
4. SLSA Level 2 和 Level 3 的核心差別在哪裡？達到 Level 2 需要什麼？
5. Kyverno `verifyImages` 的 `mutateDigest: true` 解決了什麼問題？
6. SLSA provenance 能防止 SolarWinds 那種「攻擊者入侵 build server」的攻擊嗎？為什麼？

---

## 延伸閱讀

- [Sigstore 官方文件](https://docs.sigstore.dev/)（cosign、Fulcio、Rekor 的完整技術說明，包含 keyless 簽章的 OIDC 流程）
- [SLSA 官方框架說明](https://slsa.dev/spec/v1.0/)（各等級的具體要求、provenance 格式說明）
- [Anchore syft README](https://github.com/anchore/syft)（SBOM 產生工具的完整用法，含 CI 整合範例）
- [Kyverno verifyImages 文件](https://kyverno.io/docs/writing-policies/verify-images/)（policy 語法、keyless 設定、多 attestor 設定的完整參考）
- [Google SLSA GitHub Generator](https://github.com/slsa-framework/slsa-github-generator)（現成的 SLSA Level 3 GitHub Actions workflow，直接可用）

---

image 和 artifact 的信任鏈建好了。接下來看另一個供應鏈入口：Infrastructure as Code。Terraform 的 state file 含明文 secret、CloudFormation template 寫了公開 S3、security group 允許 0.0.0.0/0——IaC 的 misconfig 怎麼靜態掃描、怎麼把掃描卡進 CI pipeline。

→ [Ch 33 IaC 安全：Terraform/CloudFormation misconfig 與掃描](./33-iac-security.md)
