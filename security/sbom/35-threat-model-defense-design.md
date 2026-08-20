# Ch 35 — 威脅模型與防禦設計

> **目標**：把供應鏈攻擊樹當設計 checklist，系統性地問「SBOM、簽章、provenance、掃描，各自擋得住或偵測得到哪些攻擊向量」，最終產出一張「攻擊向量 × 防禦機制」對映表，讓你在設計生成引擎或消費平台時知道防禦邊界在哪。

## 為什麼需要這個？

工程師設計 SBOM 系統時，最常見的盲點不是「不懂攻擊」，而是「防禦設計跟攻擊向量沒有對應關係」——加了 cosign 簽章，但沒想清楚它擋的是哪個威脅、擋不了哪個；跑了掃描，但沒想到攻擊者根本在掃描之前的環節下手。

威脅模型（threat model）的作用不是列一張恐嚇清單，而是把攻擊分類結構化，讓每個防禦機制都能對應到具體的威脅。SBOM 作為供應鏈安全的核心元件，本身也是攻擊目標——如果 SBOM 被篡改、被偽造、或刻意遺漏某些元件，它不只沒用，還會給你錯誤的安全感。

## 先建立直覺

想像你的軟體供應鏈是一條組裝線：

```
開發者提交代碼
  → 代碼倉庫合併
    → CI 構建
      → 構件打包
        → 套件庫發布
          → 下游消費（依賴管理器拉進來）
            → 運行時執行
```

攻擊者可以在每一個箭頭上下手。Ladisa 等人（IEEE S&P 2023）把這條線系統化成攻擊樹，識別出 **107 個獨特攻擊向量**，並連結到 **94 個真實事件**，對映到 **33 個防禦措施**。這不是學術分類遊戲——它告訴我們攻擊空間有多大，以及防禦要從哪裡開始。

Ohm 等人（DIMVA 2020，Backstabber's Knife Collection）從另一個角度切入：直接解剖 **174 個真實惡意套件**（62.6% 來自 npm、16.1% PyPI、21.3% RubyGems，時間跨度 2015-2019），測量攻擊者真正在用什麼技術。最常見的結論很直白：

- 61% 用 typosquatting（名稱仿冒，Levenshtein 距離平均 2.3）
- 56% 在安裝階段觸發惡意行為（`postinstall` script），而不是執行階段
- 55% 的目標是資料竊取（SSH 金鑰、憑證、系統資訊）
- 49% 使用某種形式的混淆

這兩個數字集合放在一起很清楚：大多數攻擊很「廉價」，不需要零日，只需要一個讓人看漏的名字、一段在 install script 裡跑的惡意代碼。

## 攻擊分類架構

### Ladisa SoK 的三大階段分類

Ladisa 的攻擊樹（SoK：Taxonomy of Attacks on Open-Source Software Supply Chains，IEEE S&P 2023）把 107 個向量組織成三個大類：

**1. Code Contribution（代碼貢獻）**

攻擊者在代碼進入倉庫之前或之時下手：
- 提交惡意 PR（社工維護者 merge）
- 帳號劫持（釣魚、憑證填充攻擊）
- 維護者離職後接管長期無人維護的套件（abandoned package takeover）

**2. Build and Distribution（構建與分發）**

代碼看起來乾淨，但在構建或打包時被修改：
- CI/CD 環境被入侵（build script 篡改、環境變數注入惡意命令）
- 套件庫帳號劫持（已構建的 artifact 被替換）
- Dependency confusion（內部套件名稱被外部惡意同名套件劫持）
- Typosquatting（名稱仿冒，等下游開發者打錯字或複製貼上）

**3. Dependency Resolution（依賴解析）**

攻擊者利用套件管理器的解析邏輯：
- Version range 操縱（讓 `^1.0.0` 解析到惡意的 `1.9.9`）
- 傳遞依賴感染（感染一個底層套件，影響所有上層）

### Zimmermann 的 blast radius 結構

Zimmermann 等人（USENIX Security 2019，npm 生態研究）測量了 npm 的「隱性信任爆炸半徑」：一個普通 npm 套件平均隱性信任 **79 個第三方套件**和 **39 個維護者**，熱門套件常影響超過 **100,000 個其他套件**。

這個數字的防禦含義是：你不能只信任你直接依賴的 20 個套件，你在信任一個幾乎無法人工審計的隱性信任網絡。防禦必須是系統性的，不能是逐套件審計。

### Ohm 的攻擊者技術分類

Backstabber's Knife Collection 的 174 個惡意套件，攻擊路徑可以拆成兩個維度：

**注入技術**（怎麼讓惡意套件進入依賴樹）：
- Typosquatting — 佔 61%，成本最低，利用人的輸入錯誤
- 帳號劫持後上傳替換版本
- 全新套件偽裝成合法套件（star bombing、假 README）

**執行觸發**（惡意代碼何時跑）：
- 安裝時（`postinstall`、`preinstall` script）— 佔 56%，在你知道之前就跑了
- 導入時（`import` / `require`）— 在使用時才跑，但已在你的進程裡
- 41% 有條件執行邏輯（例如只在 CI 環境偷憑證、只在特定時區跑）

**目標分類**（惡意代碼要做什麼）：
- 資料竊取 — 佔 55%（SSH 金鑰、`~/.npmrc` 的 token、AWS credentials、`/etc/passwd`）
- 下載第二階段 payload（dropper）— 佔 34%
- 後門（reverse shell）— 佔 5%
- 拒絕服務和加密貨幣挖礦 — 各 3%

這個目標分布告訴設計者：大多數供應鏈攻擊的直接受害者不是「你的用戶」，而是「你的 CI 環境裡的憑證」。被竊取的 npm token 可以再發布惡意套件，形成二次傳播；被竊取的 AWS key 可以直接橫移到雲端基礎設施。這說明 SBOM 消費平台在企業環境裡應該把 CI runner 的安全設計視為一個配套問題，而不只是軟體元件問題。

### SoK 的 94 個真實事件：案例的含義

Ladisa 的攻擊樹連結了 94 個有公開記錄的真實供應鏈攻擊事件，讓每個攻擊節點不只是理論向量，而是有真實樣本。幾個有代表性的：

**SolarWinds（2020）**：Build and Distribution 類的極端案例。攻擊者潛伏在 SolarWinds 的構建系統裡超過 9 個月，在構建過程中注入惡意代碼，而不是修改 source code——因此 code review 看不到、靜態分析看不到。SLSA Level 3 的「hermetic、ephemeral builder」設計正是回應這類攻擊：每次構建在隔離環境跑，構建環境本身不能被「汙染」並保持汙染。

**event-stream（2018）**：Code Contribution 類的案例。一個 JavaScript 套件的原作者轉讓了 npm 包的控制權給一個新的「維護者」（社工），新的維護者在套件裡加入惡意依賴。Ohm 的 dataset 也收錄了這個事件，event-stream 有超過 1,600 個下游套件、每週超過 150 萬次下載。Zimmermann 的 blast radius 概念在這裡最直觀——入侵一個套件，影響的是整個依賴網絡。

**eslint-scope（2018）**：Dependency Resolution 和 Build Distribution 的混合案例。eslint-scope 的 npm 帳號被攻陷，新版本在 postinstall 執行時竊取 `.npmrc` 裡的 npm token，再用這些 token 去發布更多惡意版本。MalOSS 論文以這個案例說明動態分析能在安裝時捕捉這個網路呼叫——但即使工具能抓到，套件倉庫端沒有做 vetting 的話，已裝的開發者就是受害者。

這三個案例說明：每個攻擊類別都有已知的真實樣本，防禦設計不是在防「假設的」攻擊，而是在防已經發生過的攻擊。SBOM 系統設計者應該把這些案例當做設計 review 的 checklist：「我的系統，對這三個案例各能偵測或阻止哪個環節？」

## 攻擊向量 × 防禦機制對映表

這張表是本章的核心產出。縱軸是攻擊類型，橫軸是防禦層。「擋」表示能阻止攻擊成功，「偵」表示能偵測但不能事先阻止，「×」表示該層對這個攻擊基本無效。

| 攻擊向量 | SBOM（清單記錄）| SBOM 完整性驗證 | Provenance/SLSA | 漏洞掃描 | 行為/動態分析 |
|---|---|---|---|---|---|
| Typosquatting | 偵（記錄真實名稱）| × | × | 偵（若 DB 有收錄）| 偵（安裝時捕捉）|
| Dependency confusion | 偵（PURL 記錄 registry）| × | 擋（驗 build 來源）| 偵（若 DB 有）| × |
| 帳號劫持後替換 artifact | × | 擋（簽章不匹配）| 擋（builder identity）| × | × |
| CI 構建環境被入侵 | × | × | 擋（SLSA L3 隔離）| × | × |
| 傳遞依賴感染 | 偵（完整傳遞依賴清單）| × | × | 擋（已知 CVE）| 偵（執行時行為）|
| 惡意 postinstall script | × | × | × | 偵（若 DB 有）| 擋（sandbox 執行）|
| 維護者帳號社工 | × | × | 偵（committor identity）| × | × |
| SBOM 本身被篡改 | — | 擋（簽章 + 透明日誌）| 擋（attestation 綁定）| × | × |
| Version range 操縱 | 偵（記錄解析後真實版本）| × | × | 擋（掃解析後版本）| × |
| 代碼混淆/反偵測 | × | × | × | × | 偵（entropy 分析）|

### 讀這張表的幾個結論

**SBOM 生成**的防禦貢獻集中在「偵測」，不是「阻止」。更重要的是，只有當 SBOM 記錄了完整傳遞依賴、且記錄的是解析後真實版本（而非 manifest 裡的 version range），偵測才有效。生成引擎的品質直接決定防禦效能。

**簽章與 provenance**（sigstore、SLSA）擅長擋「artifact 在傳遞過程被替換」類的攻擊。但對「代碼貢獻階段的社工」和「惡意 script 執行」幾乎無效。

**行為/動態分析**（MalOSS 風格的 vetting pipeline）是唯一能對付混淆代碼和惡意安裝 script 的工具，但成本高、覆蓋率低——你不可能對每個傳遞依賴都跑 sandbox。

沒有任何一列全部是「擋」。供應鏈安全的本質是縱深防禦，每個機制都有死角。

### 延伸：如何把對映表用在生成引擎設計評審

對映表不只是一個靜態的「防禦有沒有」清單，它可以作為生成引擎設計評審的模板。以下是一個設計評審的問答流程，把每個防禦層轉化成具體的設計問題：

**SBOM 生成層的評審問題**：
- 「你的 SBOM 有沒有記錄每個元件的 PURL，以及 PURL 裡的 `repository_url`？」（防 dependency confusion 的偵測）
- 「你的 SBOM 記錄的版本是 manifest 裡的 range（如 `^4.0.0`），還是依賴解析後的真實版本（如 `4.17.21`）？」（決定漏洞比對精確度）
- 「你的 SBOM 是在 build-time 生成的，還是 binary 分析後生成的？前者能看到 manifest 的 dependency confusion，後者能看到 binary 裡的靜態鏈接函式庫——但兩者的覆蓋範圍不同」
- 「你的生成工具的版本是否被釘定並記錄在 SBOM 的 metadata 裡？」（SBOM 是可重現的，還是「每次跑出來略有不同」？）

**SBOM 完整性驗證層的評審問題**：
- 「你有對你的 SBOM 做 cosign sign 嗎？下游有設定 cosign verify 作為接收的前提條件嗎？」（簽章 + 強制驗章才構成完整性保護）
- 「如果你的 SBOM 被篡改，最早多快你或你的下游會知道？」（如果答案是「不知道」或「等到 audit」，你的完整性保護是空的）

**Provenance/SLSA 層的評審問題**：
- 「你的 SBOM 有 SLSA provenance 嗎？如果有，provenance 裡的 builder.id 是什麼，下游如何驗證它是被信任的構建者？」
- 「你的 SLSA level 是什麼？Level 3 要求 hermetic builder，你的構建環境有沒有在每次構建後被丟棄重建（ephemeral）？」

**漏洞掃描層的評審問題**：
- 「你的漏洞掃描是在哪個環節跑的？CI push 時跑？還是 deploy 前跑？還是在生產環境持續跑？」（新 CVE 可以在任何時刻爆發，只在 CI 跑一次不夠）
- 「你的 VEX 流程是什麼？你有沒有機制讓工程師標記『這個 CVE 在我們的環境不可達』並讓這個標記被追蹤和審核？」

這個評審流程把對映表從「設計輸入」變成「設計驗證工具」，讓每個 SBOM 相關的設計決策都能被明確地測試。

## 方法與形式化

### MalOSS：惡意套件偵測管線的三層設計

Duan 等人（Georgia Tech，NDSS 2021）的 MalOSS vetting pipeline 代表「在 SBOM 生成之前加一層篩選」的設計路線，值得理解其架構邏輯：

```
待審套件
  │
  ├── Metadata 分析（快速、成本低、精確度低）
  │     作者信譽評分、發布歷史時間序列、下載量異常曲線
  │     命名模式（Levenshtein 距離 ≤ 2）
  │     maintainer 數量、email domain 可信度
  │
  ├── Static 分析（中等成本、能處理混淆）
  │     AST 解析：是否有 eval、exec、os.system 呼叫
  │     entropy 分析：base64/hex 字串塊
  │     網路連線相關 API 呼叫
  │     混淆偵測：識別符過短、字串拼接替代字面量
  │
  └── Dynamic 分析（昂貴、但精確）
        在隔離 sandbox 裡安裝並執行
        監控系統呼叫：網路連線、檔案讀寫、子進程
        環境感知偵測：是否在 CI 環境才觸發
```

這三層的設計邏輯是：用 metadata 快速過濾掉大部分乾淨套件，只對可疑的子集做 static 分析，再進一步篩選後才做昂貴的 dynamic 分析。Ohm 的 41% 「條件執行」數字說明為什麼 dynamic 分析必須模擬多種環境：惡意套件可能只在 CI 環境（有 `CI=true` 環境變數）才觸發。

MalOSS 論文以 eslint-scope 被入侵事件（2018 年）作為案例：攻擊者在 postinstall script 裡竊取 npm 憑證。動態分析的 sandbox 本來能抓到這個網路呼叫——但沒有任何套件倉庫在事發前做這件事。

### in-toto layout 驗證模型

in-toto（Torres-Arias 等，USENIX Security 2019）的設計把整條供應鏈分成定義好的步驟，每一步都有「允許執行者」和「期望的輸入/輸出 hash」：

```
Layout（由產品 owner 定義，簽名後分發）
  step "clone":   允許者=[developer keys]
                  materials={}，products={src/*.c: hash}
  step "test":    允許者=[ci-key]
                  materials={src/*.c: hash}，products={src/*.c: hash}
  step "build":   允許者=[builder-key]
                  materials={src/*.c: hash}，products={binary: hash}
  step "release": 允許者=[release-key]
                  materials={binary: hash}，products={binary: hash, SBOM: hash}

Link metadata（每步執行後產生，由該步操作者簽名）
  clone-link:   { materials:{}, products:{src/foo.c: sha256:abc}, signer:dev_key }
  build-link:   { materials:{src/foo.c: sha256:abc},
                  products:{binary: sha256:def}, signer:builder_key }
```

驗證者拿 layout 和所有 link metadata，確認：
1. 每個 link 的 signer 是 layout 裡允許的 key
2. 每個 link 的 materials hash 等於上一步的 products hash（沒有中間被篡改）
3. 最終的 products hash 和你拿到的 artifact hash 相符

論文在 30 個真實供應鏈入侵案例上驗證，每個案例都有至少一個違反 layout 規則的地方會被 in-toto 驗證抓到——這個驗證說明了「每步都記錄 hash」的設計的實際效果，而不只是理論保證。

### 三個框架的設計比較

把 MalOSS、in-toto、SLSA 放在同一個維度比較，可以看出它們各自保護的是哪段管線：

```
套件倉庫端 vetting        你自己的供應鏈               下游消費
（MalOSS 的目標）         （in-toto / SLSA）           （SBOM 掃描）
      │                         │                         │
      │  ← 你不能控制 →         │  ← 你能控制 →           │  ← 消費者能控制 →
      │                         │                         │
 套件倉庫            [你的 source → CI → artifact]   [你的 SBOM + 漏洞 DB]
 惡意包偵測          構建完整性保護                   漏洞告警與 VEX
```

這個視覺化說明了一個關鍵洞察：**你只能控制自己供應鏈的那一段**。套件倉庫端的 vetting（是否有 MalOSS 風格的分析）不在你的控制範圍；你能做的是在你的 CI/CD 管線加上 in-toto 或 SLSA，讓你的 artifact 有可驗證的 provenance；消費你軟體的下游能做的是驗你的 provenance 和掃你的 SBOM。整個防禦是多方合作的結果，不是任何一方單獨完成的。

這個設計能偵測「CI 被入侵後靜默修改構建輸出」——被篡改的 binary 的 hash 不會和 build-link 裡記錄的 products hash 吻合。

### 攻擊樹作為設計 checklist 的操作方法

實際設計時，建議這樣用 Ladisa 的 107 個向量：

1. **識別你的系統在哪個階段**：你的生成引擎在 build time 生成 SBOM，還是 binary 分析後生成？這決定了你能看到哪個階段的攻擊。Build-time 能看到 manifest 解析的 dependency confusion；binary 分析看不到 source 階段的惡意 PR。

2. **對每個向量問兩個問題**：「我的 SBOM 有沒有記錄能偵測這個攻擊的資訊？」（例如 PURL 有沒有 registry 來源）、「如果有，消費者知道怎麼用這個欄位來偵測嗎？」（例如他們有沒有在驗 registry 白名單？）

3. **誠實標記死角**：沒有 SBOM 系統能防所有 107 個向量。重要的是明確告訴下游「這個 SBOM 由哪個工具、在哪個階段生成、所以對哪些攻擊有盲點」——這就是 SBOM 品質（Ch 12）和 provenance（Ch 19）要記錄的東西。

### 把 Williams 的三大攻擊向量和對映表連起來

Williams TOSEM 2025 的三大向量（Code Dependencies、Build Infrastructure、Humans）和 Ladisa 的三大類別（Code Contribution、Build and Distribution、Dependency Resolution）有重疊但不完全一樣。把它們對齊：

- **Code Dependencies** ≈ Ladisa 的 Dependency Resolution：你依賴的套件本身有問題。SBOM 的最直接貢獻在這裡。
- **Build Infrastructure** ≈ Ladisa 的 Build and Distribution：你的構建環境被入侵。SLSA provenance 的主要戰場。
- **Humans** ≈ Ladisa 的 Code Contribution：你的開發者或維護者被社工。技術工具覆蓋最薄弱的地方。

Williams 把「Humans」單獨列出（而不是把它藏在 Build Infrastructure 裡）是有意義的設計選擇：它強調了「你的技術防禦再好，如果你的 CI key 被社工拿走，一切都白費」。這個向量沒有出現在對映表裡，因為任何技術層的防禦機制對「合法帳號被社工後的操作」都是「×」——這是威脅模型誠實標記死角的典型例子。

## 對比與取捨

| 防禦策略 | 成本 | 攻擊覆蓋廣度 | 主要死角 | 適用場景 |
|---|---|---|---|---|
| SBOM（清單記錄）| 低（工具自動）| 廣（記錄傳遞依賴）| 只偵不擋、品質不足則失效 | 所有場景的基礎層 |
| 簽章驗證（cosign/sigstore）| 中（需要 OIDC/PKI）| 窄（artifact 替換）| 對代碼貢獻階段無效 | 高風險 artifact 發布 |
| SLSA provenance | 中高（需要 CI 改造）| 中（構建完整性）| 不管代碼內容品質 | 軟體廠商、OSS 維護者 |
| in-toto layout 驗證 | 高（全鏈部署複雜）| 廣（每步都驗）| 部署複雜、側信道不防 | 關鍵基礎設施 |
| MalOSS 風格 vetting | 高（動態分析昂貴）| 中（能抓惡意 script）| 不可能全量覆蓋 | 套件倉庫、企業內部 registry |
| 純漏洞掃描（grype/trivy）| 低（工具自動）| 窄（已知 CVE）| 不認識新型態攻擊 | 所有場景的基礎層 |

### 從「做了什麼防禦」到「確認防禦有效」

這張對比表說的是「每個工具設計上能防什麼」，但實際上更重要的問題是「你怎麼確認你的防禦真的有效」。以下是幾個「驗證防禦有效性」的設計問題，很少被工程師問到：

**SBOM 的「生成 → 消費」鏈驗證**：你能在新 CVE 爆發後 N 分鐘內回答「我的系統裡有沒有受影響的元件」嗎？如果不能，可能是 SBOM 不夠即時（還在用上個月生成的）、或者 SBOM 沒有整合到漏洞告警流程。「SBOM 有，但在共享磁碟裡沒人看」的情況比想像中常見。

**簽章驗證的「沒有強制 policy」問題**：你能確認「所有進入生產環境的 image 都被 cosign verify 檢查過」嗎？很多組織的情況是：CI 跑了 `cosign sign`，但 Kubernetes admission webhook 或 CD 管線沒有跑 `cosign verify`。有簽章但沒有強制驗章，等於沒有防禦。

**in-toto 的「layout 和真實流程同步」問題**：你的 in-toto layout 上次更新是什麼時候？如果你的 CI 流程在半年前增加了一個新的步驟（例如加了個 SBOM 生成步驟），但 layout 沒有同步更新，驗證會失敗——或者更糟，你關掉了驗證因為它一直失敗。Layout 和流程同步是 in-toto 部署最常見的維護問題。

這三個問題說明：防禦設計不只是「選工具」，還要設計「怎麼確認工具真的在跑、跑的設定是正確的、覆蓋了你認為它覆蓋的東西」。這是 DevSecOps 的核心思想：把安全控制的有效性納入持續監控，而不是假設「部署了就能用」。

## 踩雷集錦

1. **SBOM 被當成「有就夠了」的合規票**。一份只記錄直接依賴、或只記錄 manifest range 而不是解析後真實版本的 SBOM，在對映表上幾乎每一欄都是「×」。生成引擎的品質決定防禦品質，這個因果關係必須在設計時就建立，不能等到出事再說「SBOM 沒用」。

2. **誤以為簽章驗證覆蓋了代碼安全**。「這個 artifact 是由 Alice 簽的」告訴你 Alice 確實發布了它，但如果 Alice 的倉庫裡有惡意 PR 被合併、或 Alice 的帳號被劫持，簽章照樣通過。Ohm 的數據裡，帳號劫持後替換 artifact 的攻擊在真實惡意套件裡佔有相當比例。簽章防的是「中間人替換」，不防「源頭被汙染」。

3. **忽略 postinstall script 的威脅面**。174 個惡意套件裡 56% 在安裝時觸發，而且安裝時你的 SBOM 還沒生成（你要先裝才能掃）。這是一個設計上的雞生蛋問題：要在安裝之前就知道「這個套件有沒有惡意 script」，需要套件倉庫層的 vetting，不是你自己的消費端掃描。生成引擎設計時要誠實承認這個時序問題。

4. **Dependency confusion 被低估**。很多 SBOM 系統沒有記錄套件的來源 registry（只記名稱和版本）。沒有 registry 資訊的 SBOM，無法被用來驗證「是從預期的 registry 拿的」——而 dependency confusion 的核心就是讓你從外部公共 registry 拿到一個和內部套件同名的惡意包。PURL 格式支援 `repository_url` 欄位，設計時要強制填寫。

5. **威脅模型設計完就不動了**。Ladisa 的 107 個向量是 2023 年的快照；Ohm 的資料集截止到 2019 年。攻擊手法在進化（AI-generated malicious code、LLM-assisted typosquatting），威脅模型要定期重新跑，不是設計完就鎖死。

6. **把 blast radius 當純數字而不是設計工具**。Zimmermann 的「平均 79 個隱性信任套件」不是用來嚇人的數字，而是設計工具：它告訴你依賴圖的哪些節點是高風險的（影響大量下游的節點），應該優先做 vetting 或 pinning。設計生成引擎時，可以加一個「blast radius 計算器」——對每個直接依賴計算它的傳遞依賴鏈大小，讓開發者知道「這個套件影響的元件數量，所以它的維護者帳號被社工的後果是 X」。

## 進階：再往深一層

**Reachability-aware 威脅篩選**

對映表裡的「漏洞掃描」那欄是粗粒度的——只知道「有這個元件」不等於「這個 CVE 真的能被利用」。真正降低誤報的防禦設計需要 reachability 分析（Ch 34），把「理論上有漏洞」和「攻擊者實際能觸達的代碼路徑」分開。威脅模型應該加上「reachability 是否已確認」這個維度，才能做有意義的漏洞優先排序。

**SBOM 本身作為攻擊目標**

一個鮮少被討論的威脅向量：攻擊者故意讓生成引擎產生不完整或誤導性的 SBOM，而不是攻擊它描述的軟體。例如，在構建時注入代碼讓 SBOM 漏掉某個惡意元件、或者讓 VEX 標記某個真實漏洞為 `not_affected`。SBOM 的完整性保護（Ch 36）正是為了讓這種篡改留下可偵測的痕跡。

**社會工程維度**

Ladisa 的 33 個防禦措施裡，有幾個是純社會面的：多因素認證、維護者聲譽系統、最小權限套件發布。這些不是 SBOM 系統本身能提供的，但 SBOM 資料能輔助決策：「這個套件只有一個維護者、上次更新是 3 年前、但有 100 萬個下游依賴」——這個風險側寫應該觸發更高強度的審核，不是自動放行。

**新型攻擊的研究進展**

Ladisa 的 107 個向量是 2023 年的快照，但供應鏈攻擊技術在持續進化。幾個值得追蹤的新興方向：

**AI-assisted typosquatting**：大型語言模型可以大量生成「語義上接近」某個流行套件的假名字，不只是單純的 Levenshtein distance 接近，而是語義上讓人覺得「這應該是那個套件的一部分」的名字（例如 `lodash-extras`、`react-utils-core`）。傳統的 typosquatting 偵測用 edit distance，對這類攻擊的偵測率會降低。

**軟體物料清單的格式欺騙**：攻擊者可以生成一份看起來完整但刻意遺漏某些高風險元件的 SBOM，配合 SBOM 完整性保護的不普及（很多組織收到 SBOM 不驗簽章），讓下游以為已知所有元件。這正是「SBOM 本身作為攻擊目標」在實踐中的樣子。

**CI/CD 的 supply chain as a service**：越來越多的組織使用 SaaS 的 CI/CD（GitHub Actions、CircleCI）。攻擊 CI provider 本身（而非個別組織的 CI 設定）可以同時影響大量組織的構建——這是 Build Infrastructure 向量的規模化版本，SolarWinds 攻擊的邏輯放大到雲端 CI 生態。這個攻擊向量在 Ladisa 的 2023 版論文裡已有提及，但工具防禦幾乎仍是空白。

這些新方向告訴我們：威脅模型不只是一次性的設計輸入，而是需要定期更新的動態文件。設計 SBOM 系統時，要有機制追蹤攻擊技術的演化（例如訂閱 CISA 的 Known Exploited Vulnerabilities、追蹤 Ladisa 論文作者的後續研究），並評估現有防禦對新攻擊的有效性。

## 動手練習

1. 選你專案裡三個真實的傳遞依賴（用 `syft . -o cyclonedx-json` 生成 SBOM，找三個你沒有直接 `require` 的間接依賴），在上面的對映表裡走一遍：你目前的防禦機制對哪些攻擊向量是「偵」，哪些是「×」？

2. 檢查你的 SBOM 裡的 PURL，有幾個含有 `repository_url` 欄位？有幾個沒有？如果沒有，這表示你的 SBOM 對 dependency confusion 的偵測能力是「×」。思考：你的生成引擎要怎麼補上這個欄位？

3. 去查你專案裡的任一直接依賴，找它的 `package.json` 或 `setup.py`，看看有沒有 `postinstall` 或 `preinstall` script。如果有，想想：在你裝它之前，你有任何機制知道那個 script 要做什麼嗎？

4. 用「攻擊者視角」回顧你的 SBOM 生成流程：如果你是攻擊者，要讓你的生成引擎輸出一份「漏掉了某個惡意元件」的 SBOM，你會在哪個環節下手？這個思考實驗告訴你，你的信任鏈（Ch 36）需要在哪個環節加強保護。

5. 去 Ladisa 的 Risk Explorer（可在論文的 artifact 鏈接找到）或在論文附錄的真實事件表裡，找三個你從沒聽過的供應鏈攻擊事件（不要選 SolarWinds 或 Log4Shell 這種大家都知道的），研究它們屬於哪個攻擊類別（Code Contribution / Build and Distribution / Dependency Resolution），以及當時哪個防禦機制如果存在的話能偵測到它。

## 威脅模型的生命週期管理

威脅模型不是「做完就鎖死」的文件，它有自己的生命週期。幾個維護威脅模型的設計模式：

**觸發更新的事件**：
- 出現了一個新的真實攻擊案例（Ladisa 的 94 個案例每隔一段時間會新增）
- 你的系統架構有重大變更（例如從 binary 生成改成 build-time 生成，能看到的攻擊向量不同）
- 你的上下游改變了（例如新增了一個不提供簽章 SBOM 的供應商）
- 新的標準或工具改變了某個防禦層的覆蓋範圍

**讓威脅模型可操作的設計**：
把對映表的每一個「偵」和「擋」都連結到一個具體的監控指標或 CI 檢查：
- 「偵（記錄真實名稱）」→ 連結到「是否有 job 在 CI 裡驗證 PURL 命名格式」
- 「擋（簽章不匹配）」→ 連結到「是否有 admission webhook 強制驗章」

這樣，威脅模型的有效性就可以通過「每個防禦點都有對應的可量化指標」來追蹤，而不是一份「設計了但不知道是否有效」的文件。

**威脅模型 vs 合規文件的區分**：很多組織把 SBOM 的威脅模型當成「合規文件的一部分」——寫了，但只是為了要有東西可以提交給審計員，不是為了改進設計。真正有用的威脅模型應該讓工程師在讀完後能說「我知道我設計的哪個部分在防什麼，哪個部分有死角，我要怎麼監控死角有沒有被利用」。合規文件和有用的設計文件外表看起來一樣，但後者需要明確列出「我的死角是什麼」——這正是合規思維最難要求的。

## 本章重點整理

- 威脅模型的正確用法：讓每個防禦機制都對應到它防的具體威脅，暴露死角。
- Ladisa SoK（IEEE S&P 2023）的 107 個攻擊向量分三類：Code Contribution、Build and Distribution、Dependency Resolution；SBOM 對三類的防禦能力不均等。
- Ohm Backstabber（DIMVA 2020）的 174 個真實惡意套件：61% 靠 typosquatting、56% 在安裝時觸發、49% 有混淆。廉價攻擊是主流。
- Zimmermann（USENIX Security 2019）的 blast radius（隱性信任 79 個套件、39 個維護者）說明了為什麼防禦必須是系統性的。
- 對映表的核心結論：SBOM 對傳遞依賴攻擊提供可見性；簽章防 artifact 替換；SLSA provenance 防構建被篡改；行為分析防惡意 script——沒有任何一個機制能全防。
- 生成引擎設計含義：PURL 要含 registry 來源、要記錄解析後真實版本、要誠實記錄自身盲點。

## 補充：Ladisa 的 33 個防禦措施分類

Ladisa SoK 除了識別 107 個攻擊向量，也整理了 33 個對應的防禦措施，分成幾個大類。理解這個分類讓你在設計系統時知道「我的防禦屬於哪個類別，這個類別還有哪些我沒用到的防禦選項」：

**Artifact 完整性保護（覆蓋 Build and Distribution 向量）**：
- 加密哈希（SHA-256）
- 數位簽章（GPG、cosign）
- Transparency log（Rekor、CT log）
- Attestation（in-toto、SLSA provenance）

**元件識別與驗證（覆蓋 Dependency Resolution 向量）**：
- Package URL（PURL）標準化識別
- Dependency pinning（釘定精確版本，不用 range）
- Lock files（package-lock.json、go.sum、Cargo.lock）
- 私有 registry 的 namespace 隔離（防 dependency confusion）

**代碼審查與貢獻控制（覆蓋 Code Contribution 向量）**：
- 強制 code review（至少一個 reviewer）
- 多因素認證（MFA）
- 簽名 commit（GPG signed commits）
- Branch protection rules（不允許 force push）

**監控與偵測**：
- 持續漏洞掃描（grype、trivy）
- 套件倉庫的惡意包偵測（MalOSS 風格的 vetting）
- 依賴更新自動化（Dependabot、Renovate）

**值得注意的是**：SBOM 本身在 Ladisa 的 33 個防禦措施裡被歸類為「資產管理和可見性」類別，而不是「阻止攻擊」類別。這和我們的對映表結論一致：SBOM 的主要防禦貢獻是偵測（可見性），不是阻止（blocking）。把 SBOM 定位為「安全工具」而不是「可見性工具」的期望管理是一個重要的設計決策。

## 自我檢核

- [ ] 我能不查表說出 Ladisa 三大攻擊類別各針對供應鏈的哪個階段
- [ ] 我能解釋為什麼 cosign 簽章無法防止「維護者帳號被社工後上傳惡意版本」
- [ ] 我知道 postinstall script 攻擊為什麼在時序上繞過了消費端 SBOM 掃描
- [ ] 我能說明 dependency confusion 需要 PURL 裡哪個欄位才能被 SBOM 偵測
- [ ] 我理解 blast radius 數字對「設計防禦策略」的含義

## 設計補充：SBOM 在 DFIR（數位鑑識與事件應變）裡的威脅模型角色

威脅模型不只是「防禦設計」的輸入，也是「事件應變」的加速器。當攻擊發生後，SBOM 可以作為鑑識工具：

**攻擊期間**：如果你有攻擊發生前的 SBOM（有時間戳、有簽章），你可以把它和攻擊後的系統狀態對比，找出「什麼元件被更換了、什麼元件被新增了」。這個「before/after SBOM diff」的概念在 Ch 28（SBOM 與 DFIR）有詳細討論，但它的有效性直接依賴「攻擊前的 SBOM 是否有完整性保護（不能被攻擊者修改）」——這就是 Ch 36 的信任鏈在 incident response 裡的直接應用。

**溯源分析**：在供應鏈攻擊（例如 dependency 被感染）的 DFIR 場景裡，SBOM 的歷史記錄可以幫助回答「我的系統什麼時候開始使用這個有問題的版本」。這需要 SBOM 是時間序列的（每次構建都有一份，有時間戳），而不只是一份靜態的「現在版本」。

**對威脅模型的含義**：如果你的組織有 DFIR 需求（大多數企業都有），在威脅模型裡加上「事件應變場景」維度：「當這個攻擊發生後，我的 SBOM 能幫 IR 團隊做什麼？需要哪些設計（時序記錄、完整性保護）才能讓 SBOM 在 DFIR 裡有用？」這把威脅模型從「防禦設計」延伸到「整個安全運營」的視角。

## 精讀論文 / 延伸閱讀

**SoK: Taxonomy of Attacks on Open-Source Software Supply Chains**
Ladisa, Plate, Martinez, Barais — IEEE S&P 2023
- 核心方法：系統性攻擊樹，覆蓋 code contribution → distribution 全鏈，語言/生態無關
- 關鍵數字：107 個獨特攻擊向量、94 個真實事件、33 個防禦措施
- 讀哪節：Section 3（攻擊樹結構）、Section 5（防禦對映）、附錄（真實事件表）
- 和本章關聯：本章對映表的分類骨架直接來自這篇；論文附帶的 Risk Explorer 視覺化工具可以互動式探索攻擊樹

**Backstabber's Knife Collection: A Review of Open Source Software Supply Chain Attacks**
Ohm, Plate, Sykosch, Meier — DIMVA 2020（Springer LNCS 12223，pp. 23–43）
- 核心方法：手動收集 174 個真實惡意套件、多維度分類（注入技術 × 觸發時機 × 目標 × 混淆）
- 關鍵數字：61% typosquatting、56% 安裝時觸發、55% 資料竊取目標、49% 使用混淆
- 讀哪節：Section 3（攻擊樹）、Section 4（數據分析）、Table 2（技術分布）
- 和本章關聯：提供「攻擊者真實在用什麼」的實證基礎，校正「高級攻擊是主流」的誤解

**Small World with High Risks: A Study of Security Threats in the npm Ecosystem**
Zimmermann, Staicu, Tenny, Pradel — USENIX Security 2019, pp. 995–1010
- 核心方法：大規模圖分析 npm 依賴網絡，測量傳遞信任半徑
- 關鍵數字：平均 79 個隱性信任套件、39 個維護者；熱門套件影響 >100,000 個下游
- 讀哪節：Section 4（trust analysis）、Section 5（attack scenarios）
- 和本章關聯：「blast radius」概念直接影響防禦設計優先級

**Towards Measuring Supply Chain Attacks on Package Managers for Interpreted Languages**
Duan, Alrawi 等（Georgia Tech）— NDSS 2021
- 核心方法：MalOSS vetting pipeline（metadata + static + dynamic 三層），跨 npm/PyPI/RubyGems
- 和本章關聯：提供「生成引擎上游加 vetting 層」的架構參考；eslint-scope 案例說明 postinstall 攻擊的偵測點

**in-toto: Providing farm-to-table guarantees for bits and bytes**
Torres-Arias, Afzali, Kuppusamy, Curtmola, Cappos — USENIX Security 2019
- 核心方法：layout 定義步驟 + link metadata 記錄每步 + 驗證整鏈；在 30 個真實入侵案例上驗證
- 讀哪節：Section 3（系統設計）、Section 5（安全分析）
- 和本章關聯：本章的 in-toto layout 範例直接來自這篇；理解 materials/products 模型是理解 provenance 防禦的基礎

→ [Ch 36 信任與完整性的系統設計](./36-trust-integrity-system-design.md)
