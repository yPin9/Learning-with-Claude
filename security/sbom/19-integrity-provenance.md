# Ch 19 — 完整性與來源證明：hashing / in-toto

> **目標**：建立兩個緊密相關但不同的概念——**完整性（integrity）** 和 **來源證明（provenance）**——並理解它們的技術實作：SHA-256 hash 是完整性的技術基礎，in-toto framework 是目前最完整的端到端 provenance 機制。這章結束後，你能說清楚「SBOM 裡的 component hash 在驗什麼」，以及「為什麼 hash 不夠、還要 in-toto」。

## 為什麼需要這個？

Ch 18 說明了五個攻擊面，並指出「SBOM 是清點工具，不是信任工具」。那麼信任工具長什麼樣？

最直觀的答案是：「hash 就夠了。只要驗 hash，我就知道檔案沒被動。」

這句話有一半是對的。Hash 確實能證明**完整性（integrity）**——你下載的 artifact 和原始的一模一樣，傳輸途中沒有任何位元被改動。這個保證的範圍，比很多人以為的更窄：

- hash 不能告訴你原始的 artifact **本身**是不是乾淨的
- hash 不能告訴你這個 artifact 是**誰做的**
- hash 不能告訴你這個 artifact 是**怎麼做出來的**

SolarWinds 案例裡，如果你在 build 完成後算了 SUNBURST 版 Orion.exe 的 SHA-256，然後官方公布的 hash 也是這個值（因為他們是從被入侵的 build 環境發布的），hash 驗證 100% 通過，你一樣中招。

這就是為什麼需要 **provenance（來源證明）**：不只是「這個東西沒被動過」，還要能回答「這個東西是由誰、在什麼環境、用什麼流程、從什麼材料做出來的」。

這章分兩段：先把 hash 講透（它能做什麼、不能做什麼、SBOM 裡怎麼用），再把 in-toto 從頭到尾講清楚。

---

## 先建立直覺

把 hash 想成一個零售食品的「重量驗證」：你在超市拿到一包 100g 的堅果，回家秤是 100g，能確認包裝沒有被偷偷開過。但你不知道工廠在裝袋時有沒有摻雜質、也不知道這包堅果的原料農場是不是你以為的那一家。

**provenance** 是食品溯源標籤：農場 A → 加工廠 B（批次 2024-Q1）→ 包裝廠 C → 這包。每一步都有記錄，每一步的記錄都有對應的鑑章。

in-toto 就是在軟體供應鏈上實作這種溯源標籤的框架。

---

## Part A：完整性（Integrity）

### SHA-256 hash 的機制

SHA-256 是目前供應鏈安全裡的標準 hash 演算法（MD5/SHA-1 已被淘汰，不要用）。它的性質：

- **固定長度輸出**：任意長度的輸入，輸出都是 256 bits（64 個 hex 字元）
- **雪崩效應（avalanche effect）**：輸入改一個 bit，輸出完全不同
- **碰撞抗性**：找到兩個輸入有相同輸出（碰撞），目前計算上不可行
- **單向性**：從輸出還原輸入，目前計算上不可行

這些性質讓 SHA-256 可以作為 artifact 的「數位指紋」：如果兩個檔案的 SHA-256 相同，可以以極高置信度認定它們內容相同。

### 真跑：sha256sum 驗證 artifact

在 WSL 裡：

```bash
$ cd /tmp && echo "hello supply chain" > artifact.txt
$ sha256sum artifact.txt
8476f315c391755bba43619799704b0d72b418da7534d6fa9acc6c23595d04c5  artifact.txt
```

（這是本課環境真跑出來的值。你跑同一個 `echo` 指令會得到相同 hash，因為輸入相同。）

現在改動一個位元組：

```bash
$ echo "hello supply chainX" > artifact_tampered.txt
$ sha256sum artifact_tampered.txt
26ee0dd9f7c501fa10a8866cb636e30f0453007fa22f24cf52aa207a5ee73b5c  artifact_tampered.txt
```

兩個 hash 完全不同。這就是雪崩效應。

用 `-c` 做批次驗證：

```bash
$ sha256sum artifact.txt > artifact.txt.sha256
$ sha256sum -c artifact.txt.sha256
artifact.txt: OK

# 如果檔案被改了：
$ cp artifact_tampered.txt artifact.txt
$ sha256sum -c artifact.txt.sha256
artifact.txt: FAILED
sha256sum: WARNING: 1 computed checksum did NOT match
```

### SBOM 裡的 component hash

SBOM 標準都有欄位記錄元件的 hash。以 SPDX 為例：

```json
{
  "SPDXID": "SPDXRef-Package-libc",
  "name": "libc",
  "versionInfo": "2.35-0ubuntu3.1",
  "checksums": [
    {
      "algorithm": "SHA256",
      "checksumValue": "a3b4c5d6e7f80192..."
    },
    {
      "algorithm": "SHA1",
      "checksumValue": "1234abcd..."
    }
  ]
}
```

CycloneDX 格式類似：

```json
{
  "type": "library",
  "name": "libc",
  "version": "2.35-0ubuntu3.1",
  "hashes": [
    {
      "alg": "SHA-256",
      "content": "a3b4c5d6e7f80192..."
    }
  ]
}
```

這些 hash 的用途是：**在掃漏洞或做元件驗證時，確認你拿到的這份 artifact file 和 SBOM 描述的是同一個東西**。重要的是，SBOM 裡的 hash 本身也需要被保護——如果攻擊者能同時替換 artifact 和 SBOM，hash 就失去意義。這帶出下一章的簽章（cosign）。

### Container Image Digest

Container image 的完整性用 OCI digest（sha256 of the image manifest）表示：

```bash
$ docker pull alpine:3.19
3.19: Pulling from library/alpine
Digest: sha256:13b7e62e8df80264dbb747995705a986aa530415763a6c58f84a3ca8af9a5bcd
Status: Image is up to date for alpine:3.19
```

`sha256:13b7e62e8df80264...` 就是這個 image 的 digest。用 digest 部署 Kubernetes workload：

```yaml
# 這樣不安全：tag 可變
image: alpine:3.19

# 這樣安全：digest 不可變
image: alpine@sha256:13b7e62e8df80264dbb747995705a986aa530415763a6c58f84a3ca8af9a5bcd
```

---

## Part B：來源證明（Provenance）

### 為什麼 hash 不夠

用一個具體場景說明：

```
你要驗證的問題：
   「這個 myapp:v1.2.0 image 是不是真的從 github.com/myorg/myapp 的 main branch
    由 GitHub Actions 建出來的？」

hash 能告訴你：
   「這個 image 和 registry 上的 myapp:v1.2.0 內容相同。」

hash 不能告訴你：
   「registry 上的 myapp:v1.2.0 是怎麼被放上去的。
    也許是合法 CI 建的，也許是有人手動 push 的，
    也許是 build env 被入侵後建的。」
```

**來源證明（provenance）** 是一份描述「這個 artifact 的生產履歷」的 metadata，內容包括：

- **who**：是哪個身分（CI pipeline、build service、哪個工程師）執行的
- **what**：輸入材料是什麼（source commit SHA、dependencies）
- **how**：執行了什麼步驟、用什麼 build 工具
- **when**：在什麼時間點執行的
- **where**：在哪個環境執行的（GitHub Actions 的某個 runner instance）

這份 provenance 本身也需要被簽章，才能被信任。

### in-toto：端到端 supply chain 完整性框架

[in-toto](https://in-toto.io/) 是一個 CNCF Graduated 專案（2019 年加入 sandbox，2023 年 graduate），由 NYU 的 Trishank Kuppusamy、Lukas Pühringer 等人開發，設計目標是讓供應鏈的每一步都能被驗證。

SLSA（Ch 22）的 provenance attestation 格式，和 sigstore（Ch 20）的 cosign attestation，底層格式都是 in-toto Attestation Framework。in-toto 是整個現代 supply chain security 生態的基礎語言。

### in-toto 的三個核心概念

**1. Layout（布局）**：定義供應鏈「應該是什麼樣子」的規格文件。

Layout 列出：
- 有哪些 **step**（`clone`、`build`、`test`、`package`）
- 每個 step 由哪個 **functionary**（授權執行者，用公鑰識別）執行
- 每個 step 允許哪些輸入材料（`materials`）和輸出產物（`products`）
- step 之間的順序和依賴關係

Layout 本身由專案負責人（project owner）簽章，代表「這是我們定義的合法 supply chain 規格」。

**2. Link Metadata（連結 metadata）**：每個 step 執行後產生的「執行證明」。

當 functionary 執行某個 step，in-toto 的工具（`in-toto-run`）會記錄：
- 這個 step 叫什麼名字（對應 layout 裡的 step 名稱）
- 執行了什麼命令
- 執行前，相關材料的 hash（materials）
- 執行後，產出的產物的 hash（products）

這份記錄由執行 step 的 functionary 用自己的 key 簽章，產生一個 `*.link` 檔案。

**3. Verification（驗證）**：消費端拿著 Layout + 所有 Link Metadata，驗證整條 supply chain 是否合規。

驗證步驟：
1. 用 project owner 的公鑰驗 Layout 簽章
2. 確認 link metadata 的數量和 step 對應
3. 用各 functionary 的公鑰驗每個 link 的簽章
4. 確認材料的 hash 在整條鏈上是一致的（上一個 step 的 product hash = 下一個 step 的 material hash）
5. 確認最終的 product 就是你拿到的 artifact

### ASCII 圖：in-toto 的完整驗證流程

```
Project Owner
  │
  │ 簽章
  ▼
┌─────────────────────────────────────────────────────────────────┐
│ Layout（supply chain 規格）                                      │
│  step: clone_source                                              │
│    functionary: key_alice                                        │
│    expected_materials: []                                        │
│    expected_products:  [src/**, *.go]                           │
│  step: build                                                     │
│    functionary: key_ci_bot                                       │
│    expected_materials: [src/**, go.sum]                         │
│    expected_products:  [myapp_binary]                           │
│  step: package                                                   │
│    functionary: key_ci_bot                                       │
│    expected_materials: [myapp_binary]                           │
│    expected_products:  [myapp_v1.2.0.tar.gz]                   │
└─────────────────────────────────────────────────────────────────┘

執行階段：每個 step 的 functionary 各自簽章

Alice 執行 clone_source：
  in-toto-run --step-name clone_source --key alice.pem \
    --products src/ *.go -- git clone https://github.com/myorg/myapp
  → 產出 clone_source.link（含 products 的 SHA-256，alice 簽章）

CI Bot 執行 build：
  in-toto-run --step-name build --key ci_bot.pem \
    --materials src/ *.go --products myapp -- go build ./...
  → 產出 build.link（含 materials+products hash，ci_bot 簽章）

CI Bot 執行 package：
  in-toto-run --step-name package --key ci_bot.pem \
    --materials myapp --products myapp_v1.2.0.tar.gz -- tar czf ...
  → 產出 package.link

驗證階段（消費端）：

  ┌───────────────────────────────────────────────────────────────┐
  │ in-toto-verify                                                 │
  │   --layout root.layout                                         │
  │   --layout-keys project_owner.pub                             │
  │   clone_source.link  build.link  package.link                 │
  │                                                                │
  │ 驗證路徑：                                                     │
  │   Layout 簽章 OK?          ✓（project_owner key）             │
  │   clone_source.link 簽章?  ✓（alice key）                     │
  │   build materials ⊆ clone products?  ✓（hash 一致）           │
  │   build.link 簽章?         ✓（ci_bot key）                    │
  │   package materials ⊆ build products? ✓（hash 一致）          │
  │   package.link 簽章?       ✓（ci_bot key）                    │
  │   最終 product = 你手上的 artifact? ✓                          │
  └───────────────────────────────────────────────────────────────┘

結論：整條 supply chain 都符合 layout 定義的規格。
```

如果任何一步被竄改（比如 build 時注入了額外程式碼），build.link 裡的 products hash 就會和實際產出的 binary hash 不符，或者和 package.link 的 materials hash 不一致，驗證就會失敗。

### in-toto Attestation Framework：現代格式

原始的 in-toto（上面說的 layout/link）是用 PGP key 簽章的，設計上偏向傳統的 PKI 模型。隨著 sigstore 和 SLSA 的出現，in-toto 也定義了新的 **in-toto Attestation Framework**，用一個更通用的格式描述 provenance：

```json
{
  "_type": "https://in-toto.io/Statement/v0.1",
  "subject": [
    {
      "name": "myapp_v1.2.0.tar.gz",
      "digest": { "sha256": "a1b2c3d4e5f6..." }
    }
  ],
  "predicateType": "https://slsa.dev/provenance/v1",
  "predicate": {
    "builder": { "id": "https://github.com/actions/runner" },
    "buildType": "https://github.com/slsa-framework/slsa-github-generator/go@v1",
    "invocation": {
      "configSource": {
        "uri": "git+https://github.com/myorg/myapp@refs/heads/main",
        "digest": { "sha1": "abc123..." },
        "entryPoint": ".github/workflows/release.yml"
      }
    },
    "materials": [
      {
        "uri": "git+https://github.com/myorg/myapp@refs/heads/main",
        "digest": { "sha1": "abc123..." }
      }
    ]
  }
}
```

這個 JSON document 分兩個部分：

**subject**（主詞）：「這個 attestation 是關於哪個 artifact 的」，用 artifact 的 hash 識別。

**predicate**（述詞）：「關於這個 artifact，我們要聲明什麼事」，predicate type 決定聲明的內容類型。常見類型：

| predicateType | 描述 |
|---|---|
| `https://slsa.dev/provenance/v1` | SLSA provenance，描述 build 過程（v0.2 為舊格式，本課全程用 v1）|
| `https://in-toto.io/Link/v0.3` | 傳統 in-toto link 格式 |
| `https://spdx.dev/Document` | SPDX SBOM 作為 predicate |
| `https://cyclonedx.org/bom` | CycloneDX SBOM 作為 predicate |
| `https://cosign.sigstore.dev/attestation/vuln/v1` | 漏洞掃描結果 |

這個設計非常靈活：你可以把 SBOM 本身作為一個 in-toto attestation 的 predicate，用 cosign 簽章後 attach 到 OCI registry 上。Ch 21 就是在做這件事。

### in-toto 和 SLSA 的關係

一句話：**SLSA provenance 是 in-toto Attestation Framework 的一個 predicate type 的應用**。

SLSA 定義了「一個標準化的 build provenance predicate 長什麼樣子」（builder、buildType、materials、invocation），而這份 provenance document 用 in-toto Attestation Framework 的 Statement 格式包裹，然後用 sigstore 的 cosign 簽章。

三者的關係：

```
in-toto Attestation Framework
          │ 定義通用的 subject + predicate 封裝格式
          ▼
    SLSA provenance
          │ 定義標準化的 build provenance predicate 內容
          ▼
       sigstore
          │ 用 keyless signing 簽章並用 Rekor 存證
          ▼
  cosign attach attest ...  ← Ch 21 的動手範例
```

---

## 底層機制：hash 是怎麼「連結」整條鏈的

in-toto 的核心安全性建立在 hash 的**傳遞一致性**上。設想 build pipeline 有三步：

```
Step A 的 products：
  src/main.go  SHA256=aaa...

Step B 的 materials：
  src/main.go  SHA256=aaa...  ← 必須和 Step A 的 products 相同

Step B 的 products：
  myapp        SHA256=bbb...

Step C 的 materials：
  myapp        SHA256=bbb...  ← 必須和 Step B 的 products 相同
```

如果有人在 Step B 和 Step C 之間偷偷替換了 `myapp` binary，Step C 記錄的 materials hash 就會是 `bbb...`（被替換前的值），但實際上被替換的 binary 的 hash 是 `ccc...`。驗證時，消費端算一下 `myapp` 的 hash，會是 `ccc...`，和 Step C 記錄的 `bbb...` 不一致，驗證失敗。

這個「materials-products hash 鏈」是 in-toto 在技術上阻止「中間人替換 artifact」的核心機制。

```
  ┌─────────┐    products    ┌─────────┐    products    ┌─────────┐
  │ Step A  │─────────────▶ │ Step B  │─────────────▶ │ Step C  │
  │ link    │   hash=aaa    │ link    │   hash=bbb    │ link    │
  └─────────┘               └─────────┘               └─────────┘
                  ▲                         ▲
          materials 必須                materials 必須
          和 A 的 products               和 B 的 products
          hash 相同                     hash 相同

  如果攻擊者在箭頭上偷換了 artifact：
  B 的 materials hash ≠ A 的 products hash
  → 驗證失敗，攻擊被偵測
```

---

## 對比與取捨

| 機制 | 能保證什麼 | 不能保證什麼 | 實施成本 |
|---|---|---|---|
| SHA-256 hash | 傳輸/儲存完整性 | 原始 build 是否乾淨、誰做的 | 接近零 |
| SBOM 裡的 component hash | 你的 artifact 和 SBOM 描述的是同一個 | SBOM 本身是否可信 | 低（工具自動做） |
| OCI image digest | image pull 完整性 | image 是怎麼被 build 的 | 低（使用 digest 而非 tag） |
| in-toto layout/link | 每個 step 的執行者和 artifact hash 鏈 | functionary key 被盜的情境 | 中（需要在每個 CI step 加 instrumentation） |
| in-toto Attestation + cosign | 簽章綁定 identity、可用 Rekor 查歷史 | 依賴 OIDC provider 的安全性 | 中（Ch 20-21 的範圍） |
| SLSA Level 3 provenance | build 在 hardened 環境執行、provenance 不可偽造 | 攻擊者控制 layout 定義者的情境 | 高（需要 hardened build service） |

---

## 踩雷集錦

1. **「SBOM 裡有 hash，所以這份 SBOM 已經有完整性保護了」**：SBOM 裡的 hash 是「描述 artifact 的 hash」，不是「SBOM 自身的 hash」。如果 SBOM 本身沒有被簽章，攻擊者可以同時替換 artifact 和 SBOM，讓新的 SBOM 描述新的（惡意的）artifact。SBOM 的完整性要靠 cosign 簽章（Ch 21）。

2. **「in-toto 需要傳統 PKI，很難部署」**：這是舊版 in-toto（用 PGP key 的 layout/link）的問題。現代 in-toto Attestation Framework 配合 sigstore keyless signing，不需要管理長期私鑰。SLSA 的 GitHub Generator 可以讓你用幾行 YAML 在 GitHub Actions 裡輸出符合規格的 signed provenance。

3. **「link metadata 存在哪？」**：傳統 in-toto 把 `*.link` 檔案和 artifact 一起放在 release 裡。現代做法是把 attestation 作為 OCI artifact attach 到 registry（和 image 放在同一個 repo 的不同 tag 下），或者推到 Rekor transparency log。Ch 21 的 `cosign attest` 就是在做後者。

4. **「materials 漏記」**：in-toto layout 的 `expected_materials` 如果沒有列完所有輸入（例如忘了列 build 環境的 base image），攻擊者可以在沒有被監控的 material 裡藏後門，in-toto 的驗證無法發現。SLSA 的 provenance 格式明確要求記錄所有 materials，但「記錄完整性」本身是個難題——build system 需要知道「所有輸入」。

5. **「in-toto 和 SLSA 是不同的東西，選一個就好」**：這個認知偏差很常見。in-toto 是框架，SLSA 是建立在 in-toto 上的具體標準。你不是在「選」，它們是層疊關係。使用 SLSA provenance，你自動在用 in-toto Attestation Framework。

## 進階：再往深一層

**in-toto Attestation Framework v1.0 規範**：最新的 in-toto attestation 規範（[https://github.com/in-toto/attestation](https://github.com/in-toto/attestation)）定義了 Statement、ResourceDescriptor、Envelope 等標準格式，並且和 SLSA v1.0 的 provenance 格式對齊。如果你要自己實作一個自訂的 predicate type（例如「安全掃描結果 attestation」），這是規範的入口。

**Sigstore Bundle 格式**：2023 年後，sigstore 推出了 `.sigstore` bundle 格式，把簽章、certificate、Rekor log entry 全部打包在一起，不需要分別儲存。cosign verify 現在可以接受這個格式，讓 offline 驗證更容易。

**Software Heritage**：如果想讓 source code 本身的 provenance 可驗證（「這個 source code 確實曾經在某個時間點存在於某個公開 repo」），Software Heritage（[https://www.softwareheritage.org/](https://www.softwareheritage.org/)）提供了一個去中心化的源碼存檔服務，可以產生不可竄改的 source code 識別符（SWHID）。

## 動手練習

1. 在 WSL 裡對任意一個你的 Go / Python 專案目錄跑 `find . -type f -exec sha256sum {} \; > project.sha256sums`，然後改一個小地方，再跑 `sha256sum -c project.sha256sums` 看哪個檔案報 FAILED。感受一下「靠 hash 做完整性監控」是什麼感覺。

2. 查看 Docker Hub 上任意一個 image 的 manifest：`docker manifest inspect alpine:3.19 | jq '.config.digest'`，對比你本地 pull 下來的 `docker images --digests alpine:3.19`。兩個 digest 應該一樣，想想為什麼一樣能保證你拿到的是同一個 image。

3. 翻開你的任一個 `go.sum` 或 `package-lock.json`，找到裡面的 hash 值。這些 hash 在保護什麼？如果 registry 上的套件被替換成惡意版本，這些 hash 能不能發現？為什麼能或不能？

## 本章重點整理

- **完整性（integrity）**：SHA-256 hash 驗證「這個 artifact 沒有被動過」，但無法驗證「原始 artifact 是乾淨的」和「誰做的」。
- **SBOM 裡的 component hash** 把 artifact 和 SBOM 的描述綁在一起，但 SBOM 自身需要簽章才能被信任。
- **OCI image digest** 是 container image 的不可變身分識別，應優先用 digest 而非 tag 部署。
- **in-toto** 透過 layout（規格）、link metadata（執行證明）、verification（驗證）三層，實現端到端 supply chain 完整性驗證——每個 step 的 functionary 簽章 + materials-products hash 傳遞一致性，讓「中間替換 artifact」無法躲過驗證。
- **in-toto Attestation Framework** 是 SLSA provenance 和 sigstore attestation 的底層通用格式，predicate type 決定聲明的內容，subject 綁定 artifact。
- 三者的層次：hash（完整性）< in-toto attestation（provenance）< SLSA（流程規格）。

## 自我檢核

- [ ] 我能說清楚「hash 驗通了，是否代表 artifact 安全」，並舉 SolarWinds 為反例
- [ ] 我能說明 in-toto 的三個核心概念（layout / link / verify）各自的作用
- [ ] 我能畫出 in-toto 的 materials-products hash 鏈，並解釋為什麼中間替換 artifact 會被偵測到
- [ ] 我能解釋 in-toto Attestation Framework 的 subject/predicate 結構，以及它和 SLSA provenance 的關係
- [ ] 我知道「SBOM 作為 in-toto attestation 的 predicate」是什麼意思，以及它解決了什麼問題

## 延伸閱讀

- **[in-toto 官方文件](https://in-toto.io/)**（CNCF Graduated）
  先看首頁的 concepts，再看 Getting Started；layout + link + verify 的概念文件是核心；spec 完整版在 [https://github.com/in-toto/in-toto](https://github.com/in-toto/in-toto)。

- **[in-toto Attestation Framework](https://github.com/in-toto/attestation)**
  Statement 格式規範（ITE-6），這是 SLSA provenance 和所有現代 cosign attestation 的底層；特別看 `spec/v1.0/` 目錄下的 statement.md 和 resource_descriptor.md。

- **[SLSA Provenance v1.0 Spec](https://slsa.dev/provenance/v1)**
  SLSA provenance predicate 的完整定義，看完你會知道 `cosign attest --predicate` 裡貼的那份 JSON 每個欄位是什麼意思。

- **[Reproducible Builds](https://reproducible-builds.org/)**
  in-toto 保證「鏈的每步有人簽章了」，reproducible builds 保證「同樣的 source + build env 一定產出 byte-identical 的 artifact」——兩者互補，合起來才能端到端驗證 artifact 的來源。Debian、Tor 都在推。

- **[Turing Complete Blog: in-toto Explained](https://www.cncf.io/blog/2023/08/17/unleashing-in-toto-the-api-of-devsecops/)**（CNCF Blog）
  in-toto 的主要開發者寫的，用 DevSecOps API 的角度解釋 in-toto，文字不長但概念密度高。

---

hash 給了我們「完整性」，in-toto 給了我們「供應鏈完整性」，但還缺一個關鍵：「誰簽的」。我們知道有一個 link 被 ci_bot key 簽了，但怎麼確認那個 key 確實屬於合法的 CI 系統、而不是攻擊者生成的？這就是下一章要解決的問題：**sigstore 的 keyless signing——用短命憑證和公開 transparency log，讓「誰在何時簽了什麼」不依賴長期私鑰管理就能被驗證**。

→ [Ch 20 sigstore 原理：cosign / fulcio / rekor](./20-sigstore.md)
