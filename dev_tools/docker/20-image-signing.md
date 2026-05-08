# Ch 20 — 映像簽名

> 目標：搞清楚為什麼 image 需要簽名，知道 DCT 和 cosign 的差異，能用 cosign 完成 key pair 簽名和 keyless 簽名，並了解 SBOM 在供應鏈安全的角色。

---

## 為什麼要簽名

你從 registry pull 下來的 image，你怎麼確認它是：

1. 真的是你自己 build 的（沒有被 registry 方篡改）
2. 在傳輸過程中沒有被中間人換掉

這就是**供應鏈攻擊（supply chain attack）**的攻擊面。幾個真實案例：

- **SolarWinds（2020）**：攻擊者滲透 build pipeline，在合法的軟體更新裡植入後門。軟體是合法簽名的，但 build 過程被污染了。
- **codecov（2021）**：攻擊者竄改 codecov 的 bash uploader script，所有下載這個 script 的 CI 環境都把環境變數（包含 secret）傳給攻擊者。
- **Docker Hub namespace 攻擊**：在 Docker Hub 上建立和知名 image 相似的 namespace（`nginx` vs `ngi_nx`），希望打錯字的人 pull 到惡意 image。

簽名讓你能在 deploy 前驗證：這個 image 確實是某個特定 key 的擁有者所簽署的，沒有被篡改。

---

## DCT / Notary v1：舊機制，現在少用

Docker Content Trust（DCT）是 Docker 舊的簽名機制，基於 The Update Framework（TUF）和 Notary v1：

```bash
# 啟用 DCT（設定環境變數）
export DOCKER_CONTENT_TRUST=1

# 之後的 docker push 會自動簽名，docker pull 會驗簽
docker push myapp:v1
docker pull myapp:v1
```

為什麼現在少用：

- 只支援 Docker Hub 和少數 registry
- key 管理複雜，Notary server 設定麻煩
- 不支援 Kubernetes、CI 系統整合差
- 社群已經把精力轉向 cosign

---

## cosign：現代標準

cosign 是 Sigstore 專案的一部分，由 Google、Red Hat、Chainguard 共同推動，目前是業界主流。

```
Sigstore 生態
  cosign   <- image / artifact 簽名驗證
  fulcio   <- 無金鑰簽名的憑證頒發機構（CA）
  rekor    <- 公開的簽名透明記錄（類比 certificate transparency log）
```

**安裝**：

```bash
# macOS
brew install cosign

# Linux（下載二進位）
curl -LO https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64
sudo mv cosign-linux-amd64 /usr/local/bin/cosign
sudo chmod +x /usr/local/bin/cosign

# 確認版本
cosign version
```

---

## Key Pair 簽名

自己管理 private key，適合不依賴外部 OIDC 的環境：

```bash
# 產生 key pair（cosign.key 是 private key，cosign.pub 是 public key）
cosign generate-key-pair
# Enter password for private key: （設定 key 的密碼）
# Private key written to cosign.key
# Public key written to cosign.pub
```

**私鑰要保管好，不能進 git。**

```bash
# 先 build 並 push image（簽名對象是 digest，image 必須在 registry 上）
docker build -t ghcr.io/yourname/myapp:v1 .
docker push ghcr.io/yourname/myapp:v1

# 簽名
cosign sign --key cosign.key ghcr.io/yourname/myapp:v1
# 輸入 key 密碼
# 簽名存在 registry 裡（和 image 同一個 repo，但是附加的 artifact）

# 驗證
cosign verify --key cosign.pub ghcr.io/yourname/myapp:v1
# Verification for ghcr.io/yourname/myapp:v1 -- The following checks were performed:
# - The cosign claims were validated
# - The signatures were verified against the specified public key
```

簽名不存在 image layer 裡，而是存在 registry 的 OCI artifact 機制裡（同一個 repo，以 `sha256-<digest>.sig` tag 識別）。

---

## Keyless Signing：不用管 Private Key

Keyless signing（無金鑰簽名）是 cosign 最強的功能，適合 CI/CD 環境。原理：

```
GitHub Actions 執行時有 OIDC token（證明「我是 github.com/yourname/repo 的 workflow」）
     |
     v
cosign 用這個 OIDC token 向 Fulcio CA 申請短效憑證（10 分鐘有效）
     |
     v
用這個短效憑證簽名 image
     |
     v
簽名記錄寫進 Rekor 透明記錄（公開、不可篡改）
```

你不需要管理任何 private key，整個信任鏈靠 OIDC identity（GitHub、Google、Microsoft 帳號）和公開透明記錄。

GitHub Actions 裡的寫法：

```yaml
# .github/workflows/release.yml
- name: Log in to GHCR
  uses: docker/login-action@v3
  with:
    registry: ghcr.io
    username: ${{ github.actor }}
    password: ${{ secrets.GITHUB_TOKEN }}

- name: Build and push
  id: build-push
  uses: docker/build-push-action@v5
  with:
    push: true
    tags: ghcr.io/${{ github.repository }}:${{ github.sha }}

- name: Sign image (keyless)
  uses: sigstore/cosign-installer@v3

- name: Cosign keyless sign
  env:
    COSIGN_EXPERIMENTAL: "1"
  run: |
    cosign sign --yes \
        ghcr.io/${{ github.repository }}@${{ steps.build-push.outputs.digest }}
```

驗證 keyless 簽名（指定你期望的 OIDC issuer 和 subject）：

```bash
cosign verify \
    --certificate-identity-regexp="https://github.com/yourname/myapp/.*" \
    --certificate-oidc-issuer="https://token.actions.githubusercontent.com" \
    ghcr.io/yourname/myapp:v1
```

---

## SBOM（Software Bill of Materials，軟體物料清單）

SBOM（軟體物料清單）記錄 image 裡裝了哪些套件、版本、授權，類比硬體產品的零件表。美國政府已要求聯邦採購的軟體要附 SBOM。

生成並附加 SBOM 到 image：

```bash
# 安裝 syft（生成 SBOM 的工具）
brew install syft

# 生成 SBOM，SPDX 格式（另一個格式是 CycloneDX）
syft ghcr.io/yourname/myapp:v1 -o spdx-json > myapp.spdx.json

# 把 SBOM 附加到 image（存在 registry，和簽名同一個機制）
cosign attach sbom --sbom myapp.spdx.json ghcr.io/yourname/myapp:v1

# 驗證 SBOM
cosign verify-attestation \
    --type spdxjson \
    --key cosign.pub \
    ghcr.io/yourname/myapp:v1
```

有了 SBOM，當一個新 CVE 出來時（例如 Log4Shell），你可以快速搜尋你所有的 image 有沒有用到那個套件，不需要一個個 pull 下來掃。

---

## 在 Kubernetes 強制驗簽

只產生簽名不夠，要在 deploy 時強制驗簽才有意義。Kubernetes 用 admission controller（准入控制器）做到這件事：

- **Cosign + Kyverno**：Kyverno policy 驗證 image 有合法簽名，否則拒絕 pod 建立
- **Sigstore Policy Controller**：Sigstore 官方的 Kubernetes admission controller

這屬於 Kubernetes 的範疇，Docker 本身的 `--policy` 功能（`docker trust`）也可以做類似事情，但支援度有限。

---

## 自我檢核

- [ ] 能解釋 SolarWinds 和 codecov 攻擊的攻擊面，以及簽名如何（部分）解決這個問題
- [ ] 知道 DCT 和 cosign 的主要差異，以及為什麼現在推薦 cosign
- [ ] 能用 cosign 完成 key pair 產生、image 簽名、以及驗證的完整流程
- [ ] 能解釋 keyless signing 的信任鏈（OIDC → Fulcio → Rekor）
- [ ] 知道 SBOM 是什麼，以及它在 CVE 應急回應時的用途

下一章從「image 有沒有問題」轉向「容器跑起來的權限控制」，學非 root 執行和 read-only 檔案系統。

→ [Ch 21 非 root 與 Read-only](./21-non-root-readonly.md)
