# Ch 36 — 信任與完整性的系統設計

> **目標**：從設計決策角度，理解「你的 SBOM 憑什麼被下游信任」——Merkle tree / transparency log 的資料結構設計、in-toto attestation 的資料模型、sigstore 的 keyless 設計論證。不是操作手冊（那在 Ch 19-21），而是「為什麼要這樣設計，取捨在哪裡」。

## 為什麼需要這個？

你生成了一份 SBOM，簽了章，放到某個地方讓下游拉。下游拿到這份 SBOM 時，他憑什麼相信它？

這個問題不只是密碼學問題。密碼學（簽章）告訴你「這份 SBOM 沒有被篡改，而且是聲稱的那個人發出的」——但它沒有告訴你：
- 發布者是真的看過它才簽，還是自動化管線盲簽？
- 這份 SBOM 對應的構建步驟是什麼？跑在哪個環境？
- 如果發布者的 key 洩漏了，你怎麼追溯哪些簽章是在洩漏前還是後的？
- 如果你沒有發布者的 public key，你去哪裡拿？trust root 在哪裡？

這些是系統設計問題，不是演算法問題。這一章把三個核心機制——Merkle tree / transparency log、in-toto attestation 資料模型、sigstore 的 keyless 設計——從設計決策角度拆開，理解每個決策防的是什麼、代價是什麼。

## 先建立直覺

信任問題的核心緊張關係是：

```
集中式信任
（一個 CA 說了算）
  優點：查詢簡單、撤銷有效
  缺點：單點故障、CA 被攻陷就全壞
  
      ⟺  你要的其實是一個平衡點  ⟺

去中心式信任
（每個人各自管自己的 key）
  優點：沒有單點
  缺點：key 分發困難、撤銷幾乎不可能、人類無法管理長期 key
```

傳統 PGP/GPG 生態的問題就是偏向「去中心」：每個人管自己的 key，key 簽章 web-of-trust 在實踐上幾乎沒人真的維護，key 洩漏後撤銷也沒人知道。大多數人的解法是「反正就下載然後不驗」——這是最壞的情況：有簽章的假象，但實際上沒有驗。

Transparency log 的設計哲學是不同的切入點：「與其試圖讓每個人管好自己的 key，不如讓每一次簽章事件都公開可審計——任何人都能看到有沒有異常的簽章行為」。

## 核心設計一：Merkle Tree 與 Transparency Log

### 為什麼需要 append-only log？

先想一個更簡單的問題：你要記錄所有的簽章事件（哪個 artifact 被哪個 key 在什麼時間簽了），讓審計者能驗「這個簽章有沒有在記錄裡」。

最直覺的方案是一個中央資料庫，每次插入一筆記錄。問題是：誰能保證資料庫沒有被篡改？如果 log 操作員把某筆可疑記錄刪掉，你怎麼知道？

Transparency log 的核心洞察是：**讓資料結構本身防止刪除和修改**，而不依賴操作員的誠信。

Merkle tree（又稱 hash tree）是實現這個性質的資料結構：

```
                     Root Hash
                    /          \
              H(L1,L2)        H(L3,L4)
             /       \        /       \
           H(L1)   H(L2)  H(L3)   H(L4)
            |        |      |        |
           L1       L2    L3       L4
      (entry 1) (entry 2) (entry 3) (entry 4)
```

每個葉子節點（`L1`-`L4`）是一筆 log entry 的 hash，每個內部節點是其兩個子節點 hash 的合併再 hash，root 節點是整棵樹的「指紋」。

這個結構有兩個關鍵性質：

**Inclusion proof（包含證明）**：要證明「`L3` 在這棵樹裡」，你不需要提供全部的樹，只需要提供 `H(L4)` 和 `H(L1,L2)` 這個路徑（log n 個 hash），收驗者自己算出 Root Hash，跟他手上的 Root 比較就夠了。

**Consistency proof（一致性證明）**：要證明「大小為 N 的樹是大小為 M 的樹的前綴（只有追加、沒有修改）」，可以提供一個 sub-proof，讓收驗者確認兩棵樹有共同的「前綴 root」。

**Append-only 的保證來自哪裡？** 如果操作員修改了一筆舊的 entry，Root Hash 就會改變。任何人手上持有舊 Root Hash 的（例如在 CT log 的 gossip 協議裡、或者直接存在客戶端）都能偵測到「Root 變了」——這意味著操作員要麼追加了新記錄（正常），要麼修改了舊記錄（異常）。

### Rekor：Sigstore 的 transparency log 設計

Rekor 是 sigstore 的 transparency log 元件，儲存所有簽章事件。每筆 entry 包含：
- artifact 的 hash
- 簽章本身
- 簽章者的憑證（短暫的，由 Fulcio 發出）
- 時間戳

這個設計的關鍵決策：**時間戳由 log 提供，不由簽章者提供**。這解決了「key 洩漏後追溯」的問題：你可以查 log，看這個 artifact 的簽章事件發生在洩漏前還是後。

操作者定期發布 Signed Tree Head（STH），就像 Git commit 的 hash 一樣——一個 Root Hash + 時間戳，由操作者簽名。任何人能拿最新的 STH 和之前存的 STH 跑 consistency proof，確認 log 沒有被回滾。

### 對 SBOM 設計的含義

如果你要讓 SBOM 的簽章被「公開可審計」，把 SBOM 的簽章事件記到 transparency log 是最有力的保證：

- 下游可以驗「這個 SBOM 的簽章真的被記錄了」（inclusion proof）
- 審計者可以監控 log，看有沒有不預期的 SBOM 版本被發布
- log 的 append-only 性質讓你事後知道「什麼時候發生的」

代價是：必須有人運營這個 log（或者用 Rekor 這個公共服務），而且 log 的隱私問題需要考慮——公共 log 會把你的 artifact hash 和簽章者 identity 公開。

### CT log（Certificate Transparency）的先行者

Transparency log 在 Web PKI 裡先有了 Certificate Transparency（CT log，RFC 6962）的實踐，後來 Rekor 把同樣的設計搬到軟體簽章。CT log 要求所有 CA 把它們發出的每個 TLS 憑證都記到公開 log，讓任何人能監控「有沒有不明 CA 發出了某個 domain 的憑證」。

CT log 告訴我們這個設計的實際效果：多個 CT log 操作者（Google、Cloudflare、DigiCert 等）讓任何一個操作者的故障不會讓系統崩潰，同時讓「被記錄在 log 裡」成為 TLS 憑證被信任的前提條件（瀏覽器會拒絕沒有 SCT（Signed Certificate Timestamp）的 EV 憑證）。Rekor 對軟體簽章的角色設計上與此類似，但目前「沒有 Rekor 記錄的簽章被拒絕」還不是業界主流的 policy——這是部署現狀而非設計缺陷。

### Gossip 協議：讓監控去中心化

Transparency log 的操作者誠信問題靠的不是「信任操作者」，而是讓 STH（Signed Tree Head）廣泛傳播，任何監控者都能發現 log 不一致。CT log 用的 gossip 協議讓不同客戶端互相比較 STH，如果兩個客戶端拿到同一個 log 的不同 STH（相同樹大小但不同 Root Hash），就知道 log 被篡改。

對 SBOM 系統設計者的含義：如果你用了 Rekor，你的 CI 系統應該在每次簽章後把 Rekor 的 inclusion proof 存在你控制的地方（例如 CI artifact 的 metadata），而不只是信任 Rekor 永遠可用。這樣即使 Rekor 事後被篡改，你有獨立的記錄可以對照。

## 核心設計二：in-toto Attestation 資料模型

### Layout 與 Link 的關係

in-toto（Torres-Arias 等，USENIX Security 2019）的資料模型有兩個核心概念：

**Layout**：由 software owner 定義、簽名的「供應鏈藍圖」，描述「這個軟體應該經過哪些步驟、每步允許誰執行、期望看到什麼輸入和輸出」。可以把它想成一份合約。

**Link**：每個步驟執行後產生的「執行記錄」，由執行那個步驟的功能方簽名。記錄了實際的 materials（輸入 artifacts 和 hash）、products（輸出 artifacts 和 hash）、執行指令和環境。

驗證時，驗證者持有 Layout 和所有 Link metadata，做三件事：
1. 每個 Link 的簽名者是 Layout 裡允許執行那一步的 key 嗎？
2. 每個 Link 的 materials hash 等於上一步的 products hash 嗎？（沒有中間被修改）
3. 最終 products 的 hash 和你拿到的 artifact 相符嗎？

### SLSA provenance 的關係

SLSA provenance（Ch 22-23）是 in-toto attestation 的一個具體化 schema。它定義了一個特定格式的 attestation，記錄：

```json
{
  "subject": [{ "name": "artifact.tar.gz", "digest": {"sha256": "..."} }],
  "predicateType": "https://slsa.dev/provenance/v1",
  "predicate": {
    "buildDefinition": {
      "buildType": "https://github.com/slsa-framework/slsa-github-generator/...",
      "externalParameters": {
        "workflow": { "ref": "refs/heads/main", "repository": "..." }
      }
    },
    "runDetails": {
      "builder": { "id": "https://github.com/..._BUILDER_ID" },
      "buildMetadata": { "startedOn": "...", "finishedOn": "..." }
    }
  }
}
```

關鍵設計決策：`builder.id` 是個 URI，指向構建這個 artifact 的具體構建器的 identity。SLSA Level 3 要求這個 builder 是隔離的（ephemeral、hermetic），讓攻擊者即使入侵了 CI 環境，也無法讓 artifact 通過 SLSA L3 驗證。

### Predicate 的可延伸性

in-toto attestation 框架的設計是可延伸的：`predicateType` 是個 URI，定義了 `predicate` 欄位的語意。SBOM 可以作為 predicate 的一種：

```
predicateType: "https://cyclonedx.org/bom"
predicate: { <整份 CycloneDX SBOM> }
```

這個設計讓 SBOM 和 provenance 可以用同一個 attestation 框架管理，而不是兩個分開的系統。消費端只需要知道「這個 artifact 有哪些 attestations」，就能同時拿到 SBOM 和 provenance。

### in-toto 的 30 個真實入侵案例驗證

Torres-Arias 等人（USENIX Security 2019）在論文裡做了一個系統性的驗證：收集了 30 個有公開記錄的真實供應鏈入侵案例，逐一分析「如果當時部署了 in-toto，攻擊的哪個環節會被偵測到」。

驗證結果顯示，在 30 個案例裡，每個案例都有至少一個環節違反了 in-toto 的 layout 規則——也就是說，如果 in-toto 被部署並啟用了驗證，所有 30 個攻擊都會在驗證時被擋下或至少被偵測到。

這個驗證的重要意義在於：它說明了 in-toto 的設計不是理論上的完整性保護，而是對真實攻擊有實際效果的防禦。但同樣重要的是去理解這個驗證的限制：這 30 個案例都是「已知的、有公開記錄的攻擊」，攻擊者知道自己被分析了。真實部署中的 in-toto 面對的是「不知道 layout 定義是什麼」的攻擊者，他們可能嘗試操縱 link metadata 本身——而 link 的簽章要求讓這很難，但不是不可能（例如內部人員攻擊，他本來就有合法的 key）。

### Predicate 作為可查詢資產的設計

OCI（Open Container Initiative）的 attestation spec 把 in-toto attestation 作為 container image 的 layer 存在 registry 裡，讓消費者能用 `cosign download attestation` 查詢一個 image 有哪些 attestations。這個設計把「有哪些 attestations」本身變成可程式化查詢的資產：

```bash
# 查詢某個 image 的所有 attestations
cosign download attestation <image>

# 用 policy engine 驗「有 SLSA L3 provenance 才允許部署」
cosign verify-attestation \
  --type slsaprovenance \
  --policy policy.rego \
  <image>
```

policy.rego 可以表達「builder.id 必須是我信任的 GitHub Actions builder」、「buildType 必須是 hermetic build」之類的條件，讓信任不再只是「有沒有 attestation」的二元判斷，而是「這個 attestation 的內容是否符合我的要求」的細粒度 policy。

這個設計方向（attestation + policy engine）是 SBOM 信任體系的發展方向：SBOM 作為一種 attestation predicate，加上 VEX 作為另一種 predicate，配上一個能解讀 SBOM 的 policy engine（例如「如果 SBOM 裡有 CVE > 9.0 且 VEX 沒有標記 not_affected，部署被拒絕」），整個信任決策就變成可程式化的、可審計的、而不是依賴人工判斷的。

## 核心設計三：Sigstore 的 Keyless 設計論證

### 傳統 key 管理的問題

Sigstore（Newman, Meyers, Torres-Arias，CCS 2022，pp. 2353–2367）的出發點是：傳統軟體簽章的最大障礙不是密碼學，而是 key 管理的人因問題。

傳統流程：
1. 開發者生成一對 key pair
2. 把 private key 安全地儲存（在哪裡？本機？HSM？CI secrets？）
3. 把 public key 分發給所有下游（怎麼分發？怎麼更新？怎麼撤銷？）
4. key 永久有效，一旦洩漏就全部的歷史簽章都不可信

實務上，「安全地儲存 private key」對大多數開源維護者來說難以做到，「把 public key 分發給所有下游」在沒有基礎設施的情況下幾乎不可能做到規模化，因此大家的解法是不簽——或者簽了但下游不驗。

### OIDC 身份綁定

Sigstore 的 keyless 設計把問題框架換掉：不問「開發者有沒有安全地管理 key」，問「開發者有沒有合法的 OIDC identity（GitHub Actions、Google、Microsoft 帳號）」。

流程：
1. 開發者（或 CI）向 Fulcio 出示 OIDC token（例如 GitHub Actions 的 `id-token`）
2. Fulcio 驗證 OIDC token，發出一個短暫的（10 分鐘有效）X.509 憑證，憑證的 Subject 欄位包含 OIDC 身份（例如 `https://github.com/alice/repo/.github/workflows/release.yml@refs/tags/v1.0`）
3. 開發者用臨時生成的 private key 簽署 artifact
4. 把簽章和憑證記到 Rekor（包含時間戳）
5. 立刻丟棄 private key（它已沒有用，有效期 10 分鐘）

驗證者不需要提前拿到 public key——他從 Rekor 查記錄，拿到當時的憑證，驗 Fulcio 的憑證鏈（Fulcio 的 root CA 是公開的），確認簽章時間（在憑證有效期內，且 Rekor entry 的時間戳可驗）。

### 這個設計的取捨

**Keyless 的代價**：

- **依賴 Rekor 可用性**：如果驗證時 Rekor 不可用，你不能驗歷史簽章。這可以用 Rekor 的 checkpoint 機制（操作者定期 dump bundle）來緩解，但需要設計。
- **OIDC provider 成為 trust root**：你信任這個簽章，實際上是信任「GitHub 的 OIDC service 沒有被攻陷」。如果 GitHub Actions 的 OIDC 被攻陷，攻擊者可以偽裝成任何 GitHub repo 的 CI。
- **公開 identity**：Rekor 的記錄是公開的。你的 artifact hash 和你的 GitHub 帳號之間的關聯是公開可查的。對私有軟體這是問題（論文的後續工作 Speranza 嘗試用 privacy-preserving 技術解決這個問題）。

**Keyless 的優點**：

- **zero key management**：開發者不需要生成、儲存、撤銷任何 long-lived key
- **自動整合 CI**：GitHub Actions 的 `id-token: write` permission 直接給你 OIDC token，幾行 YAML 就能整合
- **時間可追溯**：log 裡有 Rekor 的時間戳，可以確認簽章時間，即使事後 OIDC provider 出問題也能知道「這個簽章是在出問題之前的」

### Sigstore 的形式化安全分析

Newman 等人的論文（CCS 2022）不只是系統描述，還對 Sigstore 做了形式化的 attacker model 分析。他們識別出幾個威脅假設：

**Rekor 操作者不誠信**：如果 Rekor 的操作者想隱藏某個簽章事件（例如刪除一個惡意發布的記錄），consistency proof 讓這個操作被發現。但前提是有人在監控 STH 的一致性——如果沒有人監控，Rekor 的 append-only 保證在實踐上是空的。這是「transparency log 的安全保證依賴監控生態」的深層邏輯。

**Fulcio CA 被攻陷**：如果 Fulcio 的 CA key 洩漏，攻擊者可以自己發出憑證，偽裝成任何 OIDC identity。這是最嚴重的攻擊面。Sigstore 的應對是：Fulcio 的 CA key 本身記錄在 CT log（Certificate Transparency log），任何人能監控「有沒有不明的 Fulcio 簽章」。但 CT log 的監控同樣依賴有人真的在看。

**OIDC provider 被攻陷**：GitHub Actions 的 OIDC service 如果被攻陷，攻擊者能拿到任何 workflow 的 OIDC token，進而取得 Fulcio 憑證。這是 Sigstore 最難防的威脅，因為它要求信任 OIDC provider。論文誠實地標注這個依賴，並建議「多個 OIDC provider 的互相驗證」作為緩解——但這個功能目前還在研究階段，不是 Sigstore 的正式功能。

這種形式化分析的價值在於：它讓你知道「信任邊界在哪裡」，而不只是「工具怎麼用」。設計 SBOM 信任系統時，能清楚說出「我信任 X，因此如果 X 被攻陷，我的保證就失效」，是一個成熟的安全設計態度。

### 離線驗證的取捨

一個重要的設計決策：你的消費者可能在 air-gapped 環境，無法即時查 Rekor。Sigstore 的解法是 bundle 格式——把 Rekor 的 inclusion proof、憑證、簽章打包成一個 `.sigstore.bundle` 檔案，跟 artifact 一起分發。驗證者可以拿 bundle 做完整的離線驗證，只需要 Rekor 的 root public key（這個可以提前存）。

離線驗證的取捨是：你無法即時查「這個憑證是否已被撤銷」——短期憑證的設計（10 分鐘有效期）避免了傳統 CRL/OCSP 的複雜性，但代價是「驗完之後不知道後來有沒有問題」。

## SBOM 信任根的設計決策

把上面三個機制放在一起，設計「你的 SBOM 信任鏈」時需要做幾個關鍵決策：

**決策 1：誰是 trust root？**

| 選項 | 優點 | 缺點 |
|---|---|---|
| 自建 CA（internal PKI）| 完全控制、可離線 | 基礎設施成本高、金鑰管理複雜 |
| Sigstore（Fulcio + Rekor）| 零 key 管理、公開可審計 | 依賴 Rekor 可用性、OIDC provider 是信任根 |
| 硬體 HSM + root CA | 最高安全性 | 成本最高、運營最複雜 |
| 混合（內部簽章 + Rekor 記錄）| 平衡 | 需要設計兩個系統的互動 |

**決策 2：granularity——什麼應該被 attest？**

最小：只簽最終的 SBOM 檔案（你知道 SBOM 內容是真的，但不知道構建過程）。
中間：SBOM + provenance（你知道 SBOM 是在特定構建環境產出的）。
最大：in-toto layout 全鏈（你能驗每個中間步驟）。

更高的粒度提供更強的保證，但部署成本更高。

### 信任層次的具體設計範例

假設你在設計一個企業內部的 SBOM 分發系統，從生成到消費的信任鏈可以這樣設計：

```
                    [你的 CI pipeline]
                           │
                    生成 SBOM
                           │
              ┌─────────────────────────┐
              │   Attestation 生成步驟   │
              │                         │
              │  1. cosign sign SBOM     │  ← OIDC token (GitHub Actions)
              │     → 取 Fulcio 短期憑證 │
              │     → 簽 SBOM 檔案      │
              │     → 記到 Rekor        │
              │                         │
              │  2. in-toto link 生成    │  ← build-step key
              │     → 記錄 materials/   │
              │       products hash     │
              │                         │
              └─────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │  SBOM Distribution      │
              │  (你的 artifact repo)   │
              │                         │
              │  SBOM 檔案              │
              │  .sigstore.bundle        │  ← 含 inclusion proof
              │  in-toto link           │
              └─────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │  消費端驗證             │
              │                         │
              │  1. cosign verify-blob   │  ← 驗 Fulcio 憑證鏈
              │     --bundle ...         │     驗 OIDC identity
              │                          │     驗 Rekor inclusion
              │  2. in-toto 驗 layout   │  ← 驗每步 materials/products
              │                         │     驗簽名者授權
              └─────────────────────────┘
```

這個設計的關鍵在於三個層次：
- **cosign/sigstore**：確認 SBOM 沒有被中間人替換（artifact-level integrity）
- **Rekor**：確認這個簽章事件是公開可審計的（auditable event record）
- **in-toto layout**：確認 SBOM 是在正確的構建流程中產出的（provenance chain）

三個層次各自防不同的威脅（對應 Ch 35 的對映表）。只用其中一個不是「省力」，是「讓其他兩個防的威脅變成盲點」。

**決策 3：如何分發 trust root？**

| 方案 | 適用場景 |
|---|---|
| 把 root CA cert 放進 container image | 封閉生態（你控制消費者環境）|
| 用 sigstore 的公開 trust root（`tuf.sigstore.dev`）| 公開 OSS 分發 |
| TUF（The Update Framework）管理 root keys | 需要 root key 撤銷能力的場景 |

## 對比與取捨

| 機制 | 防的核心威脅 | 主要取捨 | 離線支援 |
|---|---|---|---|
| 簡單哈希（SHA-256）| artifact 內容被篡改 | 不防發布者被假冒 | 完整支援 |
| 非對稱簽章（GPG）| 發布者身份偽造 | key 管理是人因難題 | 需提前分發 public key |
| in-toto layout + link | 供應鏈中間步驟被篡改 | 部署複雜、需全鏈合作 | 完整支援（bundle 化）|
| Transparency log（Rekor）| 簽章事件可被隱藏 | 依賴 log 可用性、隱私問題 | 部分支援（bundle）|
| Keyless（sigstore）| 長期 key 管理失敗 | OIDC provider 是信任根 | 部分支援（bundle）|
| TUF（The Update Framework）| Trust root 被替換 | 最複雜，但最完整 | 完整支援 |

## 踩雷集錦

1. **把「有簽章」等同於「簽章被驗了」**。生產環境裡，常見的狀況是 CI 自動在每次 push 都呼叫 `cosign sign`，但下游的消費端從來沒有設定 `cosign verify`，更沒有設定 policy 說「沒有有效簽章的 image 不能部署」。有簽章的假象比完全沒有簽章更危險，因為它給你安全感，但實際上驗證環節是空的。

2. **Transparency log 的隱私問題在私有軟體裡踩到**。Rekor 是公開的 log。如果你的私有軟體（例如企業內部系統）的構建被記到 Rekor，你的 artifact hash 和構建者的 GitHub identity 就成了公開資訊。對私有軟體要用 private Rekor instance 或者 sigstore 的 private mirror，不能直接用公開 Rekor。

3. **in-toto layout 設計沒有對應實際的構建流程**。Layout 是一份「應該是什麼」的合約，如果你把 layout 設計成跟真實流程不符（例如 layout 說 step 2 由 CI key 執行，但實際上你的 CI 用了不同 key），每次構建都會驗證失敗，最後的解法是「暫時關掉驗證」——然後你再也沒有開回去。Layout 設計必須從頭就和真實流程對應，不能先把系統搭起來再去設計 layout。

4. **短暫憑證（10 分鐘有效期）跟構建時間的衝突**。Fulcio 發出的憑證只有 10 分鐘有效，如果你的構建（尤其是大型 C++ 或 Rust 專案）超過 10 分鐘，簽章步驟會失敗。解法是讓簽章在構建結束後才做，而不是在構建中間做——或者讓 CI 在簽章步驟重新取 OIDC token。這個時序問題在複雜 CI pipeline 裡很容易踩到。

5. **信任根設計沒有考慮撤銷場景**。「如果你的簽章 key（或 OIDC provider）被攻陷，你要怎麼通知所有下游不要信任舊的簽章？」這個問題在設計時幾乎沒人問，但出事的時候才發現沒有撤銷機制。TUF 的設計核心就是「key 撤銷」——如果你的系統不用 TUF，你需要有替代的撤銷流程。

## 進階：再往深一層

**Speranza 與隱私保護的 transparency log**

Sigstore CCS 2022 論文誠實地標注了公開 Rekor 的隱私問題。後續的 Speranza（CCS 2023，相同團隊）嘗試用 anonymous credentials 讓 Fulcio 簽發憑證、且讓 Rekor 能驗簽章但不洩漏簽章者 identity。這是一個有意思的研究方向，但目前還沒有廣泛部署。

**TUF（The Update Framework）與 root key 的管理**

如果你需要撤銷和輪換信任根，TUF 是最完整的框架。它用一個階層式 key 結構（root → targets → snapshot → timestamp），讓 root key 可以在不中斷服務的情況下被撤銷和替換。Python/PyPI 就用了 TUF 來管理其 release 簽章的 trust root。複雜度遠高於 sigstore，但在需要強撤銷保證的場景裡是必要的。

**SBOM 作為 attestation predicate 的標準化狀態**

目前 in-toto 的 SBOM predicate schema 還沒有完全標準化。CycloneDX 和 SPDX 各自有不同的 predicate type URI，但消費端工具（cosign 的 policy engine、OPA 等）對 SBOM predicate 的支援程度參差不齊。這是一個設計決策要先確認的：你的消費端工具能不能解析 SBOM-as-attestation，還是你需要把 SBOM 分開存放？

## 信任鏈的完整性邊界

在設計 SBOM 信任系統時，一個關鍵的設計原則是：**信任鏈只能保護它覆蓋的那段管線**。用一個具體的例子說明邊界在哪裡：

假設你有一個完整的信任鏈（cosign 簽章 + Rekor 記錄 + in-toto layout 全鏈驗證 + SLSA L3 provenance）。這個信任鏈保護的是：
- 「從 source code 到 artifact 的構建過程沒有被篡改」✓
- 「artifact 在傳輸過程中沒有被替換」✓
- 「簽章事件是公開可審計的，操作者無法悄悄撤回」✓

但這個信任鏈**不保護**：
- 「source code 本身是乾淨的」✗（惡意 PR 被 merge 了，in-toto 的 layout 裡的 developer key 的所有者確實做了這個操作）
- 「依賴的第三方套件是乾淨的」✗（SBOM 記錄了這個依賴的 PURL，但 PURL 對應的套件本身可能有惡意代碼）
- 「你的 CI 的 OIDC provider 是可信的」✗（這是 trust root，對它的信任是假設，不是可驗的保證）

這個邊界分析對設計者的含義是：你必須對「這個信任鏈的消費者會怎麼理解它的保證」有明確的認識。如果下游以為「有 SLSA L3 provenance 的 artifact 就沒有安全問題」，他們會有錯誤的安全感。信任鏈的設計文件（包括 SBOM 的 attestation）應該明確說明「這個信任鏈保護的是什麼、不保護的是什麼」——就像本章每個機制的「主要取捨」那一欄。

### 設計評審問題：信任鏈的誰在監控它？

一個常被忽視的設計問題：你的信任鏈有沒有人在監控它的有效性？

- Rekor 的 consistency proof 需要有人定期對比 STH——你的系統有沒有設定這個監控？
- Fulcio 的憑證鏈需要 root CA 保持可信——你有沒有訂閱 sigstore 的安全公告，當 root CA 有問題時能得到通知？
- in-toto 的 layout 需要和真實構建流程保持同步——你有沒有設計機制在流程改變時強制更新 layout（否則更新了流程但沒更新 layout，驗證失敗，最終的結局是關掉驗證）？

這些「監控信任鏈本身」的問題比「選用什麼信任機制」更難，因為它們需要長期的維護紀律，而不只是一次性的設計決策。

## 動手練習

1. 手算一個 4-leaf Merkle tree 的 inclusion proof：給定 `L1=sha256("a")`, `L2=sha256("b")`, `L3=sha256("c")`, `L4=sha256("d")`，計算 Root Hash，然後生成「`L3` 在這棵樹裡」的 inclusion proof（只需要提供哪些節點？為什麼？）。

2. 看一個真實的 sigstore bundle：用 `cosign download attestation <any-public-image>` 找一個有 attestation 的公開 image（例如 `chainguard/static`），把 bundle JSON 解析一遍，找到 Rekor 的 inclusion proof、Fulcio 憑證的 Subject 欄位（裡面有什麼 OIDC identity？）、和 artifact hash。

3. 設計一個三步驟的 in-toto layout（clone → build → package），列出每步的 `materials`、`products`、和允許的 `signer`。然後想一個攻擊場景（例如 CI 被入侵，build 步驟悄悄修改了 binary），說明 layout 驗證的哪一步會抓到這個攻擊。

## 本章重點整理

- Transparency log 的核心性質：append-only（Merkle tree 確保修改可被偵測）、inclusion proof（不需要下載整個 log 就能驗）、consistency proof（新舊版本 log 是前綴關係）。
- in-toto 的 layout + link 模型：layout 是合約，link 是執行記錄；驗證確認每步的執行者是被授權的、每步的輸入是上步的輸出。
- Sigstore keyless 的設計論證：把「長期 key 管理」換成「OIDC 身份 + 短暫憑證 + 時間可追溯的 log」，降低了人因失敗的機率，代價是 OIDC provider 成為信任根、Rekor 有隱私問題。
- 信任根設計的三個決策：誰是 trust root（自建 CA/Sigstore/HSM）、什麼粒度的 attestation（SBOM 本身/加 provenance/全鏈）、如何分發 trust root（bundle in image/TUF/公開 registry）。
- 離線驗證的取捨：bundle 格式讓離線驗證可行，但要提前設計 bundle 的分發機制。

## 自我檢核

- [ ] 我能說明 Merkle tree 為什麼能保證 transparency log 是 append-only（修改舊記錄會發生什麼）
- [ ] 我能說明 in-toto 的 link metadata 如何偵測「CI 被入侵後靜默修改構建輸出」
- [ ] 我知道 sigstore keyless 的「信任根是 OIDC provider」意味著什麼風險
- [ ] 我能說明短暫憑證（10 分鐘有效期）相對於長期 key 的取捨
- [ ] 我能說明為什麼公開 Rekor 對私有軟體是個問題

## 精讀論文 / 延伸閱讀

**in-toto: Providing farm-to-table guarantees for bits and bytes**
Torres-Arias, Afzali, Kuppusamy, Curtmola, Cappos — USENIX Security 2019
- 核心方法：layout 定義步驟 + link metadata 記錄每步 + 驗證整鏈；在 30 個真實供應鏈入侵案例上驗證
- 關鍵設計決策：materials/products hash 鏈確保中間步驟不能被靜默修改
- 讀哪節：Section 3（系統設計，重點是 Section 3.2 layout 格式和 Section 3.3 link 格式）、Section 5（安全分析）
- 和本章關聯：本章 layout/link 範例直接來自這篇；理解這個設計是理解 SLSA provenance 的前提

**Sigstore: Software Signing for Everybody**
Newman, Meyers, Torres-Arias — ACM CCS 2022, pp. 2353–2367
- 核心方法：OIDC 身份綁定 + 短暫憑證（Fulcio）+ transparency log（Rekor）；形式化 attacker model
- 關鍵設計決策：把「key 管理」的人因問題換成「OIDC identity」問題
- 讀哪節：Section 3（系統架構）、Section 4（安全分析和 threat model）、Section 5（privacy 討論）
- 和本章關聯：本章 keyless 設計論證直接來自這篇的 Section 3-4

→ [Ch 37 SBOM 的實證現況與研究地圖](./37-empirical-state-research-map.md)
