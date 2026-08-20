# Ch 20 — sigstore 原理：cosign / Fulcio / Rekor

> **目標**：搞清楚 sigstore 解決的問題是什麼，以及它的三個元件——Fulcio（短命憑證 CA）、Rekor（transparency log）、cosign（CLI）——各自在做什麼。動手跑通 cosign generate-key-pair 和本地 key 簽驗 blob（離線，真跑）；keyless signing 的互動流程貼出完整指令並說明預期結果。

## 為什麼需要這個？

Ch 19 的 in-toto layout 需要每個 functionary 有自己的 key，且 project owner 的 public key 要被消費端信任。這帶出了一個沒有被解決的問題：

**「那個 key 是怎麼分發和管理的？」**

傳統 GPG 簽章的 release 是這樣的：

1. 開發者或 CI 系統生成一個 RSA/DSA 私鑰，長期保存
2. 把公鑰上傳到 keyserver（`keyserver.ubuntu.com` 等）
3. 用私鑰簽 release tarball，把簽章放在網站或 GitHub release 頁面
4. 使用者下載 tarball + 簽章，從 keyserver 抓公鑰，用 `gpg --verify` 驗

這套流程的問題：

- **私鑰保管是地雷**：私鑰放在哪裡？硬碟壞掉呢？被盜呢？CI 系統的私鑰要存哪裡才安全？
- **撤銷幾乎不管用**：GPG revocation certificate 在實踐上很少有人真的去更新、去查。私鑰洩漏了，攻擊者還是能在一段時間內用它生成看似有效的簽章。
- **keyserver 不可靠**：GPG keyserver 網路設計上不需要驗證 email，任何人可以上傳任何 key 聲稱是任何人的；keyserver 也常常不同步。
- **CI 裡的 secret 洩漏**：把長期私鑰存在 CI 系統的 secret storage（GitHub Secrets、AWS SSM）是常見做法，但這讓私鑰的暴露面非常大——任何能讀那個 secret 的流程都等同於有簽章能力。

**sigstore 的解法**：如果私鑰只活幾分鐘，這些問題大部分就消失了。你不需要保管私鑰，也不需要撤銷它，因為它在簽完的瞬間就沒有意義了。但這樣的話，「誰做的這個簽章」的身分怎麼證明？這就是 Fulcio（CA）+ Rekor（transparency log）的角色。

## 先建立直覺

想像一個公證場景：你要公證一份合約，但不帶自己的印章（不想管理長期私鑰）。

**sigstore 的做法**：

1. 你去公證處（Fulcio），出示你的政府認可身分證（OIDC token，由 Google/GitHub/Microsoft 等 OIDC provider 發的）
2. 公證處驗了身分證，現場給你一枚「今天只能用四小時」的臨時印章，上面刻著你的名字（短命 X.509 憑證）
3. 你用那枚臨時印章蓋在合約上（簽章）
4. 公證處把這個事件記在一本公開的不可竄改帳本裡：「某某人在 XX 時間蓋了章在 YY 合約上」（Rekor transparency log）
5. 那枚印章四小時後自動失效，你不需要保管它

驗證者以後拿到合約，可以去帳本查「當時確實有這次蓋章事件」，即使印章早就失效了，帳本記錄永遠在。

---

## 三個元件的角色

### Fulcio：短命憑證 CA

Fulcio 是 sigstore 的 Certificate Authority，核心設計是：

**不發長期憑證，只發短命憑證（short-lived certificate）。**

每次 keyless 簽章流程，cosign 在本地臨時生成一個 ECDSA 私鑰（P-256），然後把對應的公鑰連同 OIDC token 送給 Fulcio。Fulcio 驗完 OIDC token 後，發一張 X.509 憑證，這張憑證：

- **有效期只有約 10 分鐘**（短命是設計，不是缺陷）
- **Subject Alternative Name（SAN）裡包含你的身分**：
  - 互動登入（瀏覽器 OIDC）：你的 email 地址，例如 `user@gmail.com`
  - CI 環境（workload identity）：GitHub Actions 的工作流 URI，例如 `https://github.com/myorg/myapp/.github/workflows/release.yml@refs/heads/main`

這個 SAN 是「我是誰」的聲明，由受信任的 OIDC provider 背書，由 Fulcio 用自己的 CA root key 簽章。

Fulcio 本身也把每一張發出去的憑證記錄到 Rekor，確保 CA 的行為是可稽核的（這是「certificate transparency」的概念，類似 TLS CT log 的做法）。

### Rekor：不可竄改的 Transparency Log

Rekor 是一個 append-only 的 Merkle tree（Merkle log），用來記錄簽章事件。每一筆記錄稱為一個 **log entry**，包含：

- 被簽的 artifact 的 hash
- 簽章本身
- 簽章者的 Fulcio 憑證（包含 SAN 身分資訊）
- 時間戳（由 Rekor 的時間戳服務 TSA 加蓋）

Rekor 的 Merkle tree 性質保證：

- **Append-only**：已記錄的條目不能被修改或刪除（Merkle tree 的任何修改都會改變 root hash，對外可見）
- **Inclusion proof**：可以用密碼學方法證明「某個 entry 確實在 log 裡」，而不需要下載整個 log
- **Consistency proof**：可以驗證新版 log 是舊版 log 的正當延伸，確保沒有 entry 被刪除

這讓 Rekor 成為一個**可稽核的公開帳本**：任何人都可以查詢「某個 artifact 的 hash 是否有對應的簽章記錄、誰在什麼時間簽的」。

Rekor 的公開實例在 `rekor.sigstore.dev`，可以直接用 `rekor-cli` 查詢，或透過 cosign verify 時自動查詢。

### cosign：把兩者包成好用的 CLI

cosign 是 sigstore 的 CLI 工具，把 Fulcio + Rekor 的互動封裝起來，提供：

- `cosign generate-key-pair`：生成本地 key pair（傳統模式，不用 Fulcio）
- `cosign sign-blob`：簽署任意檔案（blob）
- `cosign sign`：簽署 OCI container image
- `cosign attest`：對 OCI artifact 附加 in-toto attestation
- `cosign verify-blob`：驗證 blob 簽章
- `cosign verify`：驗證 OCI image 簽章
- `cosign verify-attestation`：驗證 attestation

---

## ASCII 圖：Keyless 簽章流程

```
開發者 / CI 流程
       │
       │ 1. 向 OIDC provider 取得 ID token
       │    （GitHub Actions: 內建 OIDC，不用互動
       │     本地開發: 打開瀏覽器登入 Google/GitHub）
       ▼
 OIDC token（JWT）
  iss: accounts.google.com
  sub: user@gmail.com
  aud: sigstore

       │
       │ 2. cosign 在本地生成臨時 ECDSA keypair
       │    （只活到簽章完成，用完即丟）
       ▼
  臨時 private key（P-256）
  臨時 public key

       │
       │ 3. 把臨時公鑰 + OIDC token 送給 Fulcio
       ▼
┌─────────────────────────────────────────┐
│  Fulcio (CA)                            │
│  驗 OIDC token 的 JWT 簽章              │
│  從 token 的 subject 拿到身分           │
│  → 發一張 X.509 短命憑證（~10 min）     │
│    SAN = user@gmail.com                 │
│    公鑰 = 你的臨時公鑰                   │
│  把這張憑證記到 Rekor                    │
└─────────────────────────────────────────┘
       │
       │ 4. cosign 收到短命憑證
       │    用臨時私鑰對 artifact hash 做 ECDSA 簽章
       │    （這時簽章和那張憑證綁在一起）
       ▼
  signature（ECDSA）

       │
       │ 5. 把 (artifact hash, signature, 憑證) 送給 Rekor 記錄
       ▼
┌─────────────────────────────────────────┐
│  Rekor (Transparency Log)               │
│  驗簽章和憑證的一致性                    │
│  寫入 Merkle tree                        │
│  → 回傳 log entry（含 log index）        │
└─────────────────────────────────────────┘
       │
       │ 6. cosign 收到 Rekor log entry
       │    臨時私鑰在這一刻可以安全丟棄
       ▼
  簽章流程完成。
  私鑰不存在了，Rekor 記錄了「user@gmail.com 在 T 時間
  用某個短命憑證簽了 artifact SHA256=xxx」。

────────────────────────────────────────────────────────
驗證流程（任何人、任何時間都可以做）

  拿到 artifact + 簽章 + 憑證

       │
       │ 1. 用憑證裡的公鑰驗 signature
       │    （確認簽章和 artifact 一致）
       ▼
       │ 2. 驗憑證的 Fulcio CA 簽章
       │    （確認憑證是 Fulcio 發的）
       ▼
       │ 3. 查 Rekor：確認這個簽章事件確實在 log 裡
       │    （inclusion proof）
       ▼
       │ 4. 確認 SAN（user@gmail.com）符合你的 policy
       │    （這個人有沒有資格發布這個 artifact？）
       ▼
  驗證通過。
  即使憑證早就過期（10 min 後），Rekor 的記錄永遠在，
  驗證任何時候都能進行。
```

---

## 動手：本地 Key 模式（真跑，離線可做）

keyless 需要連線到 Fulcio/Rekor 且需要 OIDC 互動，自動化環境裡不方便。這一節用傳統的本地 key 做簽驗，讓你先感受 cosign 的操作模式，理解指令的語意。

### Step 1：生成 key pair

```bash
$ cd /tmp && mkdir -p cosign-demo && cd cosign-demo
$ cosign generate-key-pair
Enter password for private key:
Enter password for private key again:
Private key written to cosign.key
Public key written to cosign.pub
```

（本課環境真跑時按 Enter 兩次設空密碼，之後用 `COSIGN_PASSWORD=""` 避免互動提示。）

`cosign.key` 的格式：

```
-----BEGIN ENCRYPTED SIGSTORE PRIVATE KEY-----
eyJrZGYiOnsibmFtZSI6InNjcnlwdCIsInBhcmFtcyI6eyJOIjo2NTUzNiwiciI6
... （base64 編碼的加密私鑰，scrypt KDF）
```

這是 sigstore 自訂的加密格式，底層是 ECDSA P-256 private key 用 AES-256-GCM 加密後存的。`cosign.pub` 是標準 PEM 格式的 EC 公鑰（本課環境的公鑰內容如下）：

```
-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEfhnJ1Q+y2kER5dbUjQQHpdc+Xs4Z
9WnIb8tFiVC6BIwDNlIPb0he7pM+bImXhUJTM/9Ybz2MsA2eDfgz4RITVg==
-----END PUBLIC KEY-----
```

### Step 2：準備 artifact 並簽章

```bash
$ echo "hello supply chain" > artifact.txt
$ sha256sum artifact.txt
8476f315c391755bba43619799704b0d72b418da7534d6fa9acc6c23595d04c5  artifact.txt
```

用本地 key 簽章，跳過 Rekor upload（`--tlog-upload=false`，因為這是測試用途，不想在公開 log 留記錄）：

```bash
$ COSIGN_PASSWORD="" cosign sign-blob \
    --key cosign.key \
    --output-signature artifact.sig \
    --tlog-upload=false \
    artifact.txt
Using payload from: artifact.txt
Wrote signature to file artifact.sig
```

簽章檔案是 Base64 編碼的 ECDSA 簽章（本課環境真跑輸出）：

```bash
$ cat artifact.sig
MEUCIQDTs5jh/by5P/42yXQ5Z5iHqH+Tn15qeYYPhi73TKyOZQIgQQum6IgWseKFqJhAVRnZ5EOw+ShPTgVLFLY2UygS5dI=
```

96 bytes（Base64 編碼後），DER 格式的 ECDSA (r, s) 簽章。

### Step 3：驗章

```bash
$ COSIGN_PASSWORD="" cosign verify-blob \
    --key cosign.pub \
    --signature artifact.sig \
    --insecure-ignore-tlog=true \
    artifact.txt
WARNING: Skipping tlog verification is an insecure practice that lacks of transparency and auditability verification for the blob.
Verified OK
```

### Step 4：故意驗章失敗

把 artifact 改一個字元：

```bash
$ echo "tampered content" > artifact_tampered.txt
$ COSIGN_PASSWORD="" cosign verify-blob \
    --key cosign.pub \
    --signature artifact.sig \
    --insecure-ignore-tlog=true \
    artifact_tampered.txt
WARNING: Skipping tlog verification is an insecure practice that lacks of transparency and auditability verification for the blob.
Error: invalid signature when validating ASN.1 encoded signature
main.go:74: error during command execution: invalid signature when validating ASN.1 encoded signature
```

`invalid signature when validating ASN.1 encoded signature`——artifact 內容和簽章時的不一致，驗章失敗。這就是「傳輸/儲存完整性被破壞」長什麼樣。

---

## Keyless 簽章：完整指令（未實測，互動步驟）

> **說明**：Keyless signing 需要連線 Fulcio/Rekor 公共服務，且本地互動模式需要打開瀏覽器完成 OIDC 登入。本課環境（WSL 自動化）無法完成瀏覽器互動，以下指令未實測，但在有瀏覽器的環境（Linux desktop 或 macOS）應可正確執行。CI 環境（GitHub Actions）不需要瀏覽器，見「CI 環境」一節。

### 互動模式（本地，有瀏覽器）

**簽章：**

```bash
# 簽一個 blob（本機 artifact）
# 不指定 --key → 自動觸發 keyless，打開瀏覽器完成 OIDC
cosign sign-blob artifact.txt --output-signature artifact.sig --output-certificate artifact.pem

# 預期行為：
# 1. cosign 在瀏覽器開啟 Fulcio 的 OIDC 頁面
# 2. 選擇 Google/GitHub/Microsoft 帳號登入
# 3. 登入完成後，瀏覽器顯示 token 已傳回，可以關閉
# 4. cosign 完成簽章，輸出：
#    Using payload from: artifact.txt
#    Fulcio certificate:
#      Issuer: https://accounts.google.com
#      Subject:  <你的 email>
#    Wrote signature to file artifact.sig
#    Wrote certificate to file artifact.pem
```

**驗章（keyless，查詢 Rekor）：**

```bash
# --certificate-identity 指定允許的簽章者 email
# --certificate-oidc-issuer 指定允許的 OIDC provider
cosign verify-blob artifact.txt \
  --signature artifact.sig \
  --certificate artifact.pem \
  --certificate-identity user@gmail.com \
  --certificate-oidc-issuer https://accounts.google.com

# 預期輸出（成功）：
# Verified OK
```

`--certificate-identity` 和 `--certificate-oidc-issuer` 是 **policy enforcement** 的關鍵：它們讓你不只驗「有沒有合法簽章」，還驗「是不是正確的人做的簽章」。在實際的 CI/CD 驗章流程，你會指定「只接受來自 `github.com/myorg/myapp` 的 GitHub Actions CI 系統的簽章」，這樣即使有其他人的 keyless 簽章，也不會通過你的 policy。

### CI 環境（GitHub Actions，workload identity）

在 GitHub Actions 裡，cosign v2+ 可以自動使用 GitHub 的 OIDC token，不需要任何秘密金鑰或瀏覽器：

```yaml
# .github/workflows/sign.yml
permissions:
  id-token: write   # 讓 GitHub Actions 能取得 OIDC token
  contents: read

jobs:
  sign:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: sigstore/cosign-installer@v3

      - name: Build artifact
        run: go build -o myapp ./...

      - name: Sign artifact (keyless)
        run: |
          cosign sign-blob myapp \
            --output-signature myapp.sig \
            --output-certificate myapp.pem
        env:
          COSIGN_EXPERIMENTAL: "1"  # cosign v1 用，v2 不需要
```

這時 SAN 是 GitHub 的 workload identity URI，例如：

```
https://github.com/myorg/myapp/.github/workflows/sign.yml@refs/heads/main
```

驗章時指定：

```bash
cosign verify-blob myapp \
  --signature myapp.sig \
  --certificate myapp.pem \
  --certificate-identity \
    "https://github.com/myorg/myapp/.github/workflows/sign.yml@refs/heads/main" \
  --certificate-oidc-issuer \
    "https://token.actions.githubusercontent.com"
```

這個驗章策略的語意是：「只接受由 myorg/myapp 這個 repo 的特定 workflow，在 GitHub 的 CI 環境中簽出來的 artifact。」

---

## 簽署 Container Image

對 OCI image 的 keyless 簽章（未實測，需要有效的 registry 帳號和 OIDC 環境）：

```bash
# 簽 Docker Hub 上的 image（需要先 docker login）
# IMAGE_REF 用 digest 而非 tag，因為 cosign sign 需要 digest 識別 image
IMAGE_REF="myuser/myapp@sha256:abc123..."

cosign sign \
  --key cosign.key \                # 用本地 key 的話加這行；keyless 不加
  ${IMAGE_REF}

# 預期輸出：
# Pushing signature to: index.docker.io/myuser/myapp

# 簽章存在 registry 裡，tag 格式為：
# myuser/myapp:sha256-abc123...sig
```

驗章：

```bash
cosign verify \
  --certificate-identity ... \
  --certificate-oidc-issuer ... \
  ${IMAGE_REF}
```

---

## 底層機制：Rekor 的 Merkle Tree

Rekor 用的 Merkle tree 結構和 Certificate Transparency（CT Log）基本一樣：

```
                   Root Hash
                  /         \
           Internal          Internal
           Node H3           Node H4
          /      \          /      \
      Leaf H1  Leaf H2  Leaf H5  Leaf H6
       (E1)     (E2)     (E5)     (E6)

Entry E1 = (artifact_hash, signature, certificate, timestamp)
```

每個 leaf 是一個 log entry 的 hash。Internal node 是左右子節點 hash 的 concat 後再 hash。Root hash 代表整個 log 的狀態。

**Inclusion Proof（包含證明）**：要證明 E1 在 log 裡，只需要提供 `[H2, H4]`（路徑上的 sibling hashes）。驗證者自己算 `H3 = hash(H1, H2)`，然後 `Root = hash(H3, H4)`，比對 Root 是否和公告的一致。不需要下載整個 log。

**Consistency Proof（一致性證明）**：要證明「新版 log（新 root）是舊版 log（舊 root）的正當延伸」，可以用密碼學方法驗證沒有舊 entry 被刪除或修改。監控者（Rekor 的 log monitor）定期做這個檢查。

```
時間 T1 的 log（root1）：
  [E1, E2, E3, E4]

時間 T2 的 log（root2）：
  [E1, E2, E3, E4, E5, E6]

Consistency proof 保證：
  root2 是在 root1 基礎上只做 append 而得到的
  沒有任何 E1-E4 被修改或刪除
```

---

## 對比與取捨

| 方式 | 身分綁定 | 私鑰管理 | 撤銷機制 | 離線驗章 | 稽核性 |
|---|---|---|---|---|---|
| GPG 簽章（傳統） | weak（keyserver 不可信） | 長期保管，洩漏是災難 | 理論上有，實踐上幾乎沒人用 | 可以 | 差（沒有中央 log） |
| cosign + 本地 key | 由 key 本身代表（無 CA 背書） | 同 GPG，需要保管 | 無（需手動建機制） | 可以 | 差（除非自己建 log） |
| cosign keyless（Fulcio + Rekor） | 強（OIDC provider 背書） | 零管理（短命 key 用完即丟） | 無需撤銷（key 本來就是短命的） | 部分（需查 Rekor） | 強（公開 transparency log） |
| cosign keyless + private Fulcio/Rekor | 強（自建 CA + log） | 零管理 | 無需撤銷 | 可以（用私有 log） | 強（可控稽核） |

**什麼情況用本地 key？**

- air-gapped 環境，無法連到公共 Fulcio/Rekor
- 組織有自建 Rekor 實例和私有 Fulcio
- 需要離線驗章的情境

**什麼情況用 keyless？**

- 開源專案的公開 release（在公共 Rekor 留記錄，任何人可查）
- GitHub Actions / GitLab CI 的自動化簽章（workload identity，不需要在 CI secret 存私鑰）
- 想要最少的 key 管理負擔

---

## 踩雷集錦

1. **「cosign sign-blob 一直問我要不要上傳到 Rekor，我按 N 結果簽章完就失敗了」**：預設的 `sign-blob` 會問是否同意把個人資料上傳到 Rekor（因為 keyless 的 email 是個人資料）。直接按 Enter 接受；或者在腳本裡加 `--yes`；或者用 `--tlog-upload=false` 完全跳過 Rekor（但這樣就沒有公開稽核性了）。

2. **「cosign verify 一直說 certificate 過期」**：keyless 憑證只活 ~10 分鐘，過期是正常的。但驗章時不應該用憑證的有效期去判斷——應該用 Rekor 的 log entry 裡的時間戳。`cosign verify` 預設會查 Rekor 做時間戳驗證，所以憑證過期不影響驗章成功。如果你看到這個錯誤，可能是沒有正確傳入 `--certificate` 路徑，或是 Rekor 無法連線。

3. **「`cosign sign` 沒有指定 `--key` 但也沒跳出瀏覽器」**：在 GitHub Actions 環境裡，cosign 會自動偵測 `ACTIONS_ID_TOKEN_REQUEST_URL` 環境變數，用 GitHub 的 OIDC 取得 token，不需要瀏覽器。在本地環境沒有這個變數，才會跳出瀏覽器。

4. **「--insecure-ignore-tlog 讓我不安」**：它確實是 insecure。這個 flag 的意思是「跳過 Rekor 的 transparency log 驗證」，等同於放棄了「有公開稽核記錄」這個保證。在測試、本地開發、air-gapped 環境裡用是合理的，但生產環境的驗章不應該這樣做。

5. **「我的 OCI registry 沒有支援 cosign 的 OCI artifact 格式」**：cosign 把 image 的簽章存成 registry 裡的另一個 tag（`sha256-<digest>.sig`）。不是所有 registry 都支援這個格式。Docker Hub、GitHub Container Registry (GHCR)、Google Artifact Registry (GAR)、Amazon ECR 都有支援；舊版本的 Nexus、Harbor 可能需要升級。

6. **「keyless 簽章的 Rekor 記錄有我的 email，我不想公開」**：這是 sigstore 的 trade-off。在公共 Rekor 上用互動模式登入，你的 email 確實會出現在公開 log 裡，而且 Rekor 的 immutable 性質讓它不可撤銷。選項是：在 CI 環境用 workload identity（SAN 是 workflow URI 而非 email）、或自建私有 Rekor 實例。

## 進階：再往深一層

**Private Fulcio + Rekor（企業自部署）**：公共 sigstore 對開源專案很好，但企業環境可能有需要：不想把內部 artifact 的簽章事件記到公開 log、需要更嚴格的身分控制（企業 SSO 而非 Google/GitHub）。sigstore 所有元件都有開源版本可以自部署，Chainguard 等公司提供企業支援。`cosign` 的 `--rekor-url` 和 `--fulcio-url` 可以指向自建的實例。

**Sigstore Trust Root（TUF）**：Fulcio 的 CA root certificate 和 Rekor 的公鑰，是透過 The Update Framework（TUF）分發的，而不是 hardcode 在 cosign binary 裡。這讓 sigstore 的 trust root 可以在不需要升級 cosign binary 的情況下輪換。`cosign` 在初次執行時會從 TUF 下載最新的 trust root，快取在 `~/.sigstore/`。

**SigstoreBundle 格式**：cosign v2 開始支援 `.sigstore` bundle 格式，把 signature、certificate、Rekor log entry 打包成一個 JSON 檔案。這讓簽章更容易攜帶（一個檔案而不是三個）、也讓 offline 驗章更容易（bundle 裡包含 Rekor 的 signed entry，不需要重新查詢 Rekor）。`cosign sign-blob --bundle` 輸出這個格式。

## 動手練習

1. 在本地環境照著「本地 Key 模式」那一節，完整跑一遍 generate-key-pair → sign-blob → verify-blob → 改檔案再 verify（看失敗）。做完你對 cosign 的 API 有了手感。

2. 用 `rekor-cli` 查詢公共 Rekor 裡的任一條真實記錄（你需要先裝 `rekor-cli`，或直接用 Rekor 的 HTTP API）：
   ```bash
   curl -s "https://rekor.sigstore.dev/api/v1/log/entries?limit=1" | jq '.[].body' | base64 -d | jq .
   ```
   看看一個真實 log entry 長什麼樣子，找到 `spec.signature.publicKey` 和 `spec.data.hash` 欄位。

3. 如果你有 GitHub Actions，找一個你的 repo，加一個 workflow，在 build 完成後用 cosign（keyless 模式）對產出的 binary 簽章，並在另一個 job 裡驗章。這是最接近生產環境的練習。

## 本章重點整理

- **sigstore 解決的問題**：傳統 GPG/PKI 簽章的私鑰管理地獄——私鑰保管、洩漏、撤銷。sigstore 用短命憑證（~10 min）讓私鑰不需要管理。
- **Fulcio**：短命憑證 CA，用 OIDC token（Google/GitHub 身分）換一張只活幾分鐘的 X.509 憑證，SAN 包含你的身分。每張憑證都記到 Rekor。
- **Rekor**：append-only Merkle tree transparency log，記錄每個簽章事件（artifact hash + signature + certificate + timestamp），任何人可查，不可竄改。讓「誰在何時簽了什麼」可永久稽核，即使憑證早就過期。
- **cosign**：把 Fulcio + Rekor 的互動封裝成好用的 CLI，支援 blob、OCI image、in-toto attestation 的簽驗。
- **本地 key 模式**（`--key cosign.key`）：傳統 key 管理，適合 air-gapped 環境；`--tlog-upload=false` 跳過 Rekor。
- **Keyless 模式**：生產推薦，CI 環境用 workload identity 不需要管私鑰；驗章時指定 `--certificate-identity` 和 `--certificate-oidc-issuer` 做 policy enforcement。

## 自我檢核

- [ ] 我能說出傳統 GPG 簽章的三個主要問題，以及 sigstore 各用什麼機制解決它們
- [ ] 我能畫出 keyless 簽章的七步流程（臨時 keypair → Fulcio → 短命憑證 → 簽章 → Rekor → 丟私鑰）
- [ ] 我能解釋為什麼 keyless 憑證「過期了還能驗章」——Rekor log + timestamp 是關鍵
- [ ] 我能說出 `--certificate-identity` 和 `--certificate-oidc-issuer` 在驗章時的作用
- [ ] 我跑通了本地 key 的 generate-key-pair → sign-blob → verify-blob → 驗失敗的完整流程

## 延伸閱讀

- **[sigstore 官方文件](https://docs.sigstore.dev/)**
  最直接的資料來源；先看 `cosign/signing/overview/` 確認 keyless 的概念，再看 `about/security/` 理解 trust model 和 Rekor 的 consistency check。

- **[Rekor API 文件](https://www.sigstore.dev/swagger/)**
  如果想直接對 Rekor HTTP API 操作（不透過 cosign），這是規格；用 `curl` 打 `/api/v1/log/entries` 是理解 Rekor log entry 格式最快的方式。

- **[Sigstore Deep Dive: Unmasking the Magic Behind Keyless Verification](https://dev.to/kanywst/sigstore-deep-dive-unmasking-the-magic-behind-keyless-verification-lmh)**（DEV.to）
  從密碼學機制往下挖的好文；如果你對「Merkle proof 是怎麼讓 Rekor 不可竄改」有疑問，這篇有圖解。

- **[OpenSSF Blog: Scaling Up Supply Chain Security with Sigstore](https://openssf.org/blog/2024/02/16/scaling-up-supply-chain-security-implementing-sigstore-for-seamless-container-image-signing/)**
  從 OpenSSF 的角度講 sigstore 在更大生態裡的位置，以及主要 package registry 開始整合 sigstore 的進展（npm provenance、PyPI Trusted Publishers）。

- **[EverTrust: Sigstore Explained](https://evertrust.io/guide/sigstore/)**
  面向 PKI 背景讀者的解釋，清楚說明 Fulcio 和傳統 PKI CA 的相同與不同之處，對理解 certificate lifecycle 有幫助。

---

現在你知道了 sigstore 如何用 Fulcio + Rekor 解決身分和稽核的問題，也知道 cosign 怎麼把這些接在一起。下一章要把這些能力直接用在 SBOM 上：**用 cosign 對 SBOM 本身簽章、並且產生 in-toto attestation，讓「這份 SBOM 是誰在什麼時間針對什麼 artifact 產出的」這件事也有密碼學保證**。

→ [Ch 21 簽 SBOM 與 attestation](./21-signing-sbom-attestation.md)
