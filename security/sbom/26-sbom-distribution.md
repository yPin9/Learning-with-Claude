# Ch 26 — SBOM 分發與交換

> **目標**：搞清楚 SBOM 怎麼從產出端送到消費端——OCI registry 的 referrers API、Rekor transparency log、廠商 portal、VDR 格式各自適合什麼場景；理解跨組織交換的識別子不一致、格式轉換損失、機密性兩難這三個核心難題，並能替自己的組織設計出實際可執行的 SBOM 分發矩陣。

---

## 為什麼需要這個？

SBOM 產出之後，問題才一半解決。另一半是：這份清單要怎麼到達需要它的人手上？

這個問題比看起來複雜。消費者可能是：CI pipeline 裡的掃描工具、採購你軟體的企業客戶、法規要求你交件的主管機關、出事之後做 DFIR 的應變團隊。不同消費者對格式、管道、驗真方式、保留年限的要求都不一樣。

再加上：
- OCI 容器世界有自己的 artifact graph 機制（referrers API），跟「把 SBOM 放 S3 傳連結」是完全不同的思路。
- 格式轉換（SPDX ↔ CycloneDX）有真實的資料損失，不是跑個 converter 就結束。
- 把完整 SBOM 公開 → 攻擊者拿去找你用的舊版元件直接打。不公開 → 監管機關和客戶不買單。這個矛盾沒有完美解法。

本章把這些難題攤開講清楚，讓你能做出有意識的選擇，而不是跟著 README 的範例指令走。

---

## 先建立直覺

把 SBOM 分發想成「把文件寄給不同對象」：

- **OCI registry**：你把 SBOM 裝進信封，貼標籤，跟 image 放在同一個郵箱格子裡。任何有權取這個 image 的人，也能拿到 SBOM。驗真能力：強（可用簽章）。
- **Rekor transparency log**：你把 SBOM 摘要投進公告欄，永遠在那裡，全世界可查。驗真能力：最強（不可竄改）。代價是公開。
- **Vendor portal / VDR**：你透過廠商提供的 web 介面或 API 交件給客戶或主管機關。靈活、可控，但中間有個平台，信任鏈斷一截。
- **File share**：最簡單，最危險。沒有版本管控，沒有驗真，沒有更新通知。大部分人現在還在用這個。

---

## OCI Registry：用 referrers API 把 SBOM 附在 image 旁邊

### 背景：OCI Image Spec v1.1 發生了什麼事

2024 年 2 月 15 日，OCI Image Spec v1.1 正式發布。這版加了兩個東西：

1. **`subject` 欄位**：讓一個 manifest 可以指向另一個 manifest。SBOM 的 manifest 說「我是 artifact，我的 subject 是這個 image digest」。
2. **Referrers API**（`GET /v2/<name>/referrers/<digest>`）：給定一個 image digest，列出所有指向它的 artifact（SBOM、簽章、掃描結果、provenance）。

效果：registry 維護了一張 **artifact graph**。同一個 image 可以有多個 referrer，每個 referrer 有自己的 `artifactType`（例如 `application/spdx+json`、`application/vnd.cyclonedx+json`）。

這個設計的好處是：SBOM 的生命週期跟著 image，而不是跟著某個外部 URL。你 pull image 的地方，就能查到對應的 SBOM。

### ORAS attach

ORAS（OCI Registry As Storage）是操作 OCI artifact 的參考 CLI。把 SBOM attach 到一個 image：

```bash
# 假設你已經 push image，digest 為 sha256:abc...
oras attach \
  --distribution-spec v1.1-referrers-api \
  --artifact-type application/spdx+json \
  myregistry.example.com/myapp:latest \
  sbom.spdx.json
```

查詢 referrers：

```bash
oras discover \
  --distribution-spec v1.1-referrers-api \
  myregistry.example.com/myapp:latest
```

輸出會列出所有 artifact type、digest、加上時間戳。你可以拿 SBOM 的 digest 去做後續驗簽。

要注意：`--distribution-spec v1.1-referrers-api` 這個 flag 是要求 registry 必須支援 v1.1 的 Referrers API。如果你的 registry 是舊版（不支援 subject + Referrers API），ORAS 會退回 fallback 機制（用特殊 tag 命名），但那是相容性補丁，不是正途。

### cosign：SBOM attachment 已 deprecated，改用 attestation

cosign 有兩個功能容易混淆：

- `cosign attach sbom`（舊）：把 SBOM 作為 OCI artifact 附在 image 旁邊。**2024-02-22 後已 deprecated**，cosign 官方文件明確標出。舊版的 attach 機制用的是不同的 artifact 格式，跟 OCI v1.1 的 referrers 機制不相容。
- `cosign attest`（新，推薦）：把 SBOM 包成 in-toto attestation，用 keyless signing 或 key-based signing 簽章後推上 registry。這條路可以搭配 Rekor，同時拿到簽章 + transparency log 兩個保證。

```bash
# 產 SBOM
syft myapp:latest -o spdx-json > sbom.spdx.json

# 打包成 attestation 並推上 registry + Rekor
cosign attest \
  --predicate sbom.spdx.json \
  --type spdxjson \
  myregistry.example.com/myapp@sha256:abc...
```

驗證：

```bash
cosign verify-attestation \
  --type spdxjson \
  myregistry.example.com/myapp@sha256:abc...
```

舊的 `cosign attach sbom` 指令還能用，但輸出的警告已經告訴你「這東西要死了」。很多 2023 年以前的教材和 CI 範例還在用舊做法，這是本章最大的踩雷點之一。

---

## Rekor：transparency log 提供不可竄改的存在證明

Rekor 是 sigstore 家族的 transparency log 服務。原理跟 Certificate Transparency 一樣：每筆記錄插入後就在一棵公開的 Merkle tree 上，任何人都能驗證特定記錄確實在某個時間點存在，而且沒有被竄改。

SBOM attestation 推上 registry 的同時，cosign 預設會把簽章記錄推進公開 Rekor（`https://rekor.sigstore.dev`）。你拿到一份 SBOM 之後，可以：

1. 用 cosign 驗簽，確認簽章有效。
2. 用 `rekor-cli` 查 log，確認這筆記錄的時間戳和 artifact hash 跟聲稱的吻合。
3. 任何第三方都可以獨立重複這兩步，不需要你的參與。

這解決了「收到的 SBOM 怎麼確認不是偽造的」這個問題——只要看 Rekor 的記錄是否存在，且 hash 是否跟你拿到的 SBOM 吻合。

代價：Rekor 公開的，意思是你的 SBOM metadata（artifact hash、簽章、時間戳）會進公開記錄。如果你的 SBOM 本身是機密（見後面的機密性兩難），你需要考慮是否用自建的 Rekor（需要自己維護）。

細節在 Ch 20（sigstore 原理）與 Ch 21（簽 SBOM）有更完整的拆解。

---

## 廠商 portal 與 VDR

### Vendor portal

很多大型採購場景是這樣運作的：甲方（政府機關、企業客戶）要求乙方（軟體廠商）在指定的 portal 上傳 SBOM，每次釋出版本更新都要重新上傳。Portal 可能是甲方自建的、第三方服務（例如 Anchore Enterprise、FOSSA）、或是某個監管機關的官方平台。

這條路的優缺點很直接：
- 好：甲方對接收流程有完整控制，可以做驗證、版本留存、存取稽核。
- 壞：乙方要為每個甲方維護不同格式、不同 portal 的上傳流程；沒有標準 API，靠人工操作很容易出錯。

### VDR（Vulnerability Disclosure Report）

VDR 是 CycloneDX 定義的格式，不是單純的元件清單，而是「廠商對已知漏洞的處理狀態報告」。

SBOM 告訴你：我的產品裡有哪些元件。
VDR 告訴你：這些元件裡哪些有 CVE、哪些我確認不受影響（對應 VEX 的 not_affected）、哪些我正在修（affected + remediation_plan）、哪些已經修好了。

VDR 的定位比 SBOM 更接近「廠商的漏洞責任聲明」，在政府採購和 IoT 設備交付場景有具體的法規需求。FDA 的醫療器材 SBOM 指引（2023 年）就明確提到 VDR 作為持續更新機制。

實務上，VDR 通常作為 SBOM 的配套文件交付，而不是替代品。你先交 SBOM，有新漏洞時更新 VDR，讓客戶不用每次都要求你重新產 SBOM。

---

## 跨組織交換的三個核心難題

### 1. 格式轉換有損失

SPDX 和 CycloneDX 的概念重疊但不完全一致。你把 SPDX 轉成 CycloneDX，以下東西有風險：

- **License 表達**：SPDX 的 license expression（`GPL-2.0 OR MIT`）在 CycloneDX 有對應欄位，但複雜的 license exception（`GPL-2.0-only WITH Classpath-exception-2.0`）轉換後可能降級成 free text。
- **Snippet**（SPDX 特有）：描述某個檔案裡的局部版權段落，CycloneDX 沒有對應概念。
- **Element relationship 細節**：SPDX 的 relationship type 有 30 種以上，CycloneDX 的 dependency type 較少，轉換時會有語意合併。
- **CycloneDX 特有欄位**：Services、SWID、hardware ref，SPDX 沒有，反方向轉換同樣有損。

**protobom**（2024 年 4 月由 CISA、DHS、OpenSSF 聯合發布）嘗試解這個問題。它定義了一個 format-agnostic 的資料模型，把 SPDX 和 CycloneDX 都對應到同一個中間表示，再從中間表示輸出目標格式。理論上可以做到更接近無損的雙向轉換。

實際情況：protobom 目前還在早期（截至 2026 年中），生態工具採用率有限。如果你的流程要求「接收 SPDX、交付 CycloneDX」，最安全的做法是先確認哪些欄位是下游消費者真正在讀的，針對那些欄位做轉換驗證，而不是假設 converter 全部搞定了。

### 2. 識別子不一致

同一個元件在不同地方叫不同名字，這在 Ch 4 講過，跨組織交換讓這個問題更痛：

- 你的 SBOM 裡 `purl:pkg:npm/express@4.18.2`
- 對方的 CVE 資料庫查的是 `CPE cpe:2.3:a:expressjs:express:4.18.2:*:*:*:*:node.js:*:*`
- NVD 的 CPE 匹配規則跟對方的工具的匹配規則不一樣

結果：你交過去的 SBOM，對方工具自動比對 CVE 時，有可能把有漏洞的版本比對不上（漏報），或把沒漏洞的版本比對錯（誤報）。

這不是 SBOM 格式的問題，是命名和識別子生態還沒收斂的問題。短期解法是在 SBOM 裡同時帶 PURL 和 CPE（或 SWID），讓接收端工具有更多比對依據。長期要靠 VEX 降噪（Ch 16）和接收端工具成熟度提升。

### 3. 機密性兩難

這是本章最難的部分，也是業界分歧最大的地方。

**問題核心**：完整 SBOM 列出你所有元件、版本、第三方依賴關係，攻擊者拿到這份清單可以直接：

- 找有已知 CVE 的版本，有針對性地打你。
- 推斷你的技術棧和架構（用了什麼商業組件、自研比例）。
- 知道你何時沒跟上安全更新（版本太舊）。

透明度和攻擊面之間的矛盾是真實的，不是聳人聽聞。

**業界怎麼應對：**

| 情境 | 做法 | 理由 |
|---|---|---|
| 政府採購 / 法規要求 | 交完整 SBOM 給主管機關，不公開 | 法規義務優先，監管機關有保密義務 |
| B2B 軟體銷售 | 合約保密 + 雙邊 NDA，只交給客戶 | 客戶有合理需求，合約規範責任 |
| 開源 / 公共安全 | 公開，鼓勵社群審計 | 透明度本身是保護機制 |
| 雲端 SaaS | 通常不公開，視需要交給監管機關 | 服務邊界模糊，元件清單變動頻繁 |

**EU Cyber Resilience Act（CRA）的立場**：SBOM 交給主管機關，法規文字不強制公開給一般大眾。這讓廠商有操作空間：做到合規，但不把 SBOM 當行銷素材公開。

如果你需要對外交一份「精簡版 SBOM」（去掉你認為敏感的細節），你必須在文件裡明確標出它是不完整的。假裝完整 SBOM 其實是精簡版，才是真正危險的——接收方以為自己拿到全貌，但其實沒有。

---

## Transparency Exchange API

CISA 和 NTIA 正在推動一個標準化的 SBOM 交換 API，讓軟體供應商能透過統一的 API 介面把 SBOM 自動交付給多個消費者（監管機關、客戶、掃描平台），而不是為每個對象維護不同的交付流程。

截至 2026 年中，這個規格仍在草案和早期討論階段，沒有成熟的參考實作。概念是對的——標準化 API 會大幅降低廠商的交付成本，也讓接收端能自動化處理——但現在就把它規劃進你的正式流程，等於押注一個還沒落地的東西。

可以關注，不要現在就依賴。

---

## 對比與取捨

| 分發方式 | 適用場景 | 所需工具 | 機密性控制 | 驗真能力 |
|---|---|---|---|---|
| OCI Referrers API | 容器 image 交付，CI/CD 全自動化 | ORAS / cosign / 支援 v1.1 的 registry | 依 registry 存取控制，天然綁 image 權限 | 強（配合 cosign attest 可驗簽） |
| Rekor transparency log | 需要不可竄改存在證明，開源或公共安全場景 | cosign + rekor-cli | 記錄公開，本體可在 registry 做存取控制 | 最強（Merkle tree 防竄改） |
| Vendor portal | 政府採購、B2B、法規遵循交件 | 各平台 CLI/UI（無統一標準） | 平台自帶存取控制，可加 NDA | 中（取決於 portal 是否驗簽） |
| VDR 檔案交換 | 漏洞狀態持續更新，配合 SBOM 的責任聲明 | CycloneDX 工具鏈 | 同 SBOM，視交付管道而定 | 弱（通常沒有簽章驗證） |
| File share / email | 小規模、一次性、緊急情況 | 無 | 低（靠 ACL，沒有完整性保證） | 無（沒有機制驗真） |

---

## 踩雷集錦

**1. cosign attach sbom 已 deprecated，很多 CI 範例還在教舊做法**

2024 年 2 月 22 日之後，cosign 官方建議改用 `cosign attest`。`cosign attach sbom` 的問題不只是「deprecated」這個標籤，而是它產出的 OCI artifact 格式跟 OCI v1.1 referrers API 的 `subject` 機制不相容，意思是你以為有做 SBOM attach，但用 referrers API 查不到。GitHub 上大量 2022-2023 年的 CI 範例直接複製過來就錯。

**2. ORAS referrers API 要 registry 支援 OCI v1.1，很多 on-prem registry 還沒跟上**

Harbor 2.9+、AWS ECR、GHCR、Docker Hub 比較新的版本都支援。但很多企業自建的 Nexus、JFrog Artifactory 舊版、GitLab Container Registry 舊版不支援。ORAS 遇到不支援的 registry 會用 fallback tag（在 image tag 後面加 referrers 相關的特殊字串），這個 fallback 機制的格式跟標準 Referrers API 的格式不一樣，後續查詢和驗證的指令也不一樣。如果你的 registry 升不了、又要用 referrers，先確認你的工具鏈走的是 fallback 路徑，才不會在 debug 時看到預期輸出但底層其實走了不同機制。

**3. SPDX 轉 CycloneDX 不能假設無損，特別是 license expression**

這個問題在單純 CI 掃描場景通常看不出來（因為你只在乎 purl 和版本），但在 license compliance 場景就很痛。SPDX 的 `GPL-2.0-only WITH Classpath-exception-2.0` 轉成 CycloneDX 之後，如果工具不明確支援這個 exception，可能降格成 `GPL-2.0-only`，讓接收方誤以為這個元件的 license 比實際更嚴格。在跨組織交換時，如果你的 license compliance 報告是用對方收到的 CycloneDX 產生的，那份報告的準確度取決於轉換時有沒有丟欄位。轉換前先比對來源和目標格式的欄位對照表，針對你用到的欄位做轉換驗證，不要假設工具全部搞定了。

**4. 精簡版 SBOM 不標明是精簡版**

有些廠商為了機密性把 SBOM 裡的部分欄位去掉，但交出去的文件看起來像完整 SBOM。接收方以為拿到全貌，拿去做漏洞掃描，結果覆蓋率有洞。正確做法是在 document metadata 裡標明「此 SBOM 為針對特定對象的精簡版，不涵蓋所有元件」。CycloneDX 有 `metadata.properties` 可以放這種自訂資訊，SPDX 有 comment 欄位。

---

## 進階：再往深一層

### Artifact graph 的深層意涵

OCI referrers API 建立的 artifact graph 不只是「SBOM 跟著 image 走」。同一個 image digest 可以同時有：SBOM（`application/spdx+json`）、SLSA provenance（`application/vnd.slsa-framework.slsa+json`）、簽章（cosign 簽章 manifest）、CVE 掃描結果（各廠商自訂 artifactType）。

這讓 registry 變成一個完整的 artifact 倉庫，而不只是 image 倉庫。你可以用 policy engine（例如 Ratify + Gatekeeper）在 Kubernetes 的 admission controller 層強制：如果這個 image 沒有對應的 SBOM attestation + SLSA provenance，就拒絕部署。這是把 SBOM 從「生出來就交件」變成「執行時強制策略」的入口。

### protobom 的現況

protobom 的核心概念：定義一個 SBOM Protocol Buffer 資料模型，SPDX 和 CycloneDX 都是這個資料模型的序列化格式之一。你匯入一份 SPDX，得到 protobom 的 Document 物件；你輸出成 CycloneDX，是從同一個 Document 物件轉換。沒有欄位對應的部分，protobom 會在 metadata 裡留存原始資料，讓接收方知道有東西沒有對應成功。

這比現有的 "convert then lose" 做法前進一步，但 protobom 自己的 schema 也在演進，沒有到穩定可以當基礎架構的地步。適合拿來研究和試驗，不適合現在就寫進正式生產流程的合約。

### 自建 Rekor vs. 公開 Rekor

公開 Rekor（`rekor.sigstore.dev`）的問題：你的 artifact hash 和簽章資訊是公開的。雖然 Rekor 記錄的不是 SBOM 本體，但有心人把你 Rekor 記錄的 image digest 對照公開 registry，可以知道你在什麼時間點 release 了什麼版本，乃至推斷你的 release 頻率和供應鏈環節。

對機密性有要求的組織，Rekor 可以自建（`sigstore/rekor` 是開源的）。自建 Rekor 的代價是：你要自己維護這個服務的高可用性和完整性，失去公開透明性，而且你的客戶驗簽時需要指定你自建的 Rekor endpoint，不能用預設的公開服務。

---

## 動手練習

### SBOM 分發矩陣設計

這個練習是治理類的，沒有標準答案，但做完你應該有一份可以對外解釋的文件。

**情境一（組織內部）**：

假設你負責一個 SaaS 產品，交付形式是 container image，部署在 Kubernetes。填寫你的分發矩陣：

| 消費者 | 分發管道 | 格式 | 驗真方式 | 觸發時機 | 保留年限 |
|---|---|---|---|---|---|
| CI/CD pipeline 內部掃描 | | | | | |
| 監管機關（假設需要法規遵循） | | | | | |
| 企業客戶（B2B 合約） | | | | | |
| 事件應變團隊（DFIR） | | | | | |

**情境二（開源專案）**：

假設你維護一個開源工具，釋出格式是 GitHub Release + container image。填寫：

| 消費者 | 分發管道 | 格式 | 驗真方式 | 觸發時機 | 保留年限 |
|---|---|---|---|---|---|
| 任意外部使用者 | | | | | |
| 下游整合商（把你的工具打包進自己的產品） | | | | | |
| 漏洞研究社群 | | | | | |

**填寫時需要回答的問題：**

1. 你的 SBOM 包含哪些欄位是機密的？你打算怎麼處理？
2. 你的 registry 有沒有支援 OCI v1.1 referrers API？如果沒有，你用什麼替代方案？
3. 你的客戶有沒有能力驗簽？如果沒有，你要怎麼設計驗真流程讓他們能用？
4. 當你發布新版本時，舊版本的 SBOM 要保留多久？用什麼機制讓客戶知道有更新？

---

## 本章重點整理

- OCI Image Spec v1.1（2024-02-15）引入 `subject` + Referrers API，讓 SBOM 可以作為 artifact graph 的一員附在 image 上。ORAS attach 是操作這個機制的 CLI 工具。
- `cosign attach sbom` 已在 2024-02-22 後 deprecated，應改用 `cosign attest --type spdxjson`。attest 路徑同時支援 Rekor 存在證明。
- Rekor 提供不可竄改的 transparency log，是驗真能力最強的機制，代價是記錄公開。機密性要求高的場景要評估是否自建 Rekor。
- VDR（CycloneDX）是 SBOM 的配套格式，描述廠商對已知漏洞的處理狀態，適合持續更新的漏洞責任聲明場景。
- 跨組織交換的三個核心難題：格式轉換有損失（protobom 嘗試解決但未成熟）、識別子不一致（同一元件在不同系統叫不同名）、機密性兩難（透明度 vs. 攻擊面）。
- 機密性沒有完美解法：法規場景交主管機關但不公開，B2B 靠合約保密，開源場景公開。交精簡版 SBOM 必須明確標注。
- Transparency Exchange API（CISA/NTIA）是標準化 SBOM 交換 API 的嘗試，截至 2026 年中仍在早期草案，不適合現在就依賴。

---

## 自我檢核

1. OCI referrers API 的 `subject` 欄位做了什麼事？為什麼這讓 SBOM 分發方式有本質改變？
2. `cosign attach sbom` 和 `cosign attest` 的差異是什麼？為什麼前者被 deprecated？
3. Rekor 怎麼提供不可竄改的保證？它的公開性有什麼代價？
4. VDR 和 SBOM 的關係是什麼？VDR 提供了什麼 SBOM 沒有的東西？
5. SPDX 轉 CycloneDX 為什麼可能有損失？protobom 的解法是什麼，現況如何？
6. 機密性兩難的根本矛盾是什麼？業界的三種主要做法分別適合什麼情境？

---

## 延伸閱讀

- [OCI Image Spec v1.1 — Referrers API](https://github.com/opencontainers/image-spec/blob/main/referrers.md)：規格原文，`subject` 欄位和 Referrers API 的定義
- [OCI Distribution Spec v1.1](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)：registry 端的 Referrers API 實作要求
- [ORAS CLI 文件](https://oras.land/docs/)：`oras attach` 和 `oras discover` 的完整選項說明
- [cosign attest 文件](https://docs.sigstore.dev/cosign/signing/attestation/)：attestation 流程說明，包含 predicate type 清單
- [CycloneDX VDR 規格](https://cyclonedx.org/use-cases/#vulnerability-disclosure-report)：VDR 格式定義和使用案例
- [protobom GitHub](https://github.com/protobom/protobom)：format-agnostic SBOM 資料模型的參考實作
- [CISA SBOM Sharing Roles and Considerations](https://www.cisa.gov/resources-tools/resources/sbom-sharing-roles-and-considerations)：CISA 對 SBOM 分發角色的官方討論文件

---

SaaSBOM、AI-BOM、HBOM——當 SBOM 的概念擴展到服務、模型、硬體，格式和工具都還在形成期，但法規和採購的壓力已經開始落地。

→ [Ch 27 SBOM 之外的 xBOM：SaaSBOM / AI-BOM / HBOM](./27-xbom.md)
