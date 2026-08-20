# 設計 Capstone — 你自己的 SBOM 系統架構設計文件

> **這個 Capstone 不跑工具**。你要做的是「設計一個 SBOM 系統，並用一份架構文件說清楚每個關鍵決策背後的理由，以及你設計的系統防的是哪些攻擊、有哪些已知限制」。設計決策必須有論文或標準支撐——不允許「我覺得這樣比較好」的說法出現在交付文件裡。

## 背景動機

Part 8 從四個角度拆解了 SBOM 系統：
- Ch 30-32：架構空間與生成引擎設計
- Ch 33-34：消費平台與 reachability 分析
- Ch 35：威脅模型與防禦設計
- Ch 36：信任與完整性的系統設計
- Ch 37：實證現況與研究地圖

這些章節提供了設計工具箱。這個 Capstone 要求你把工具箱用起來：選一個定位，做出決策，說清楚理由。

一個不能解釋自己設計決策的系統，是一個沒人能安全地修改和擴展的系統。架構設計文件的價值不是讓你「有文件可以交」，而是強迫你把隱性的假設和取捨顯式化——這樣後來的工程師（包括六個月後的你）才能在不破壞原有假設的情況下改動系統。

## 完整規格

### 第一步：選定位

你要選一個定位（二擇一）：

**定位 A：SBOM 生成引擎**
你設計的系統負責從軟體 artifact（源碼目錄、container image、或 binary）產出高品質、有信任保證的 SBOM。下游消費者（漏洞掃描工具、合規系統、另一個組織的平台）會信任你產出的 SBOM 並用它做決策。

**定位 B：SBOM 消費平台**
你設計的系統從多個上游（供應商、CI 系統、套件倉庫）接收 SBOM，做統一的漏洞關聯、VEX 管理、和持續監控。你的系統是「真相的中心」，負責把多份 SBOM 的資訊合併成可操作的告警。

選一個你更感興趣的定位。

### 第二步：完成以下七個 deliverable

每個 deliverable 的驗收標準寫在括號裡。

**Deliverable 1：需求與威脅模型（驗收：列出至少 5 個功能性需求 + 至少 3 個安全需求；威脅模型明確對照 Ch 35 的對映表）**

- 你的系統要解決什麼問題？用戶是誰？
- 功能性需求：系統能做什麼
- 安全需求：系統需要防什麼
- 威脅模型：從 Ch 35 的攻擊向量對映表裡，挑出你的系統必須處理的向量（至少 5 個），說明每個向量你的防禦機制是什麼、為什麼那個機制有效、這個向量是你的「擋」還是「偵」

**Deliverable 2：系統架構圖（驗收：有元件圖 + 資料流圖；每個元件說明職責和技術選型理由）**

- 畫出你的系統由哪些元件組成（可以用 ASCII 圖）
- 每個元件的職責是什麼
- 元件之間的資料流（誰向誰傳什麼）
- 技術選型（用哪個工具或協議）以及選它的理由（不允許「因為它很流行」）

**Deliverable 3：元件識別策略（驗收：涵蓋「有 manifest」和「無 manifest / binary only」兩個情境；引用 Ch 32 相關論文）**

- 你的系統怎麼識別一個 artifact 裡有哪些元件？
- 在有 manifest（package.json、go.mod、requirements.txt）的情況下，你怎麼把 manifest 的資訊轉換成 PURL？記錄的是 manifest range 還是解析後版本？為什麼？
- 在沒有 manifest 或需要 binary 分析的情況下（例如 vendored C 函式庫），你的策略是什麼？準確性預期是什麼？你怎麼在 SBOM 裡標記這個識別的不確定性？
- 引用：至少一篇 Ch 32 引用的相關論文支撐你的識別策略

**Deliverable 4：依賴圖資料模型與可合併性（驗收：有資料模型定義；說明兩份 SBOM 合併的規則和衝突處理）**

- 你的內部資料模型長什麼樣（元件的表示、依賴邊的類型）
- 如何處理兩份 SBOM 描述同一個元件但欄位不同的情況（不同 name、不同 version、不同 PURL）
- 合併規則：什麼情況認為是「同一個元件」？依據是什麼？（PURL 精確比對？名稱模糊比對？）
- 合併衝突如何處理（保留哪個？怎麼記錄衝突？）

**Deliverable 5：漏洞關聯與可達性方案（驗收：說明 CVE 到元件的比對邏輯；說明你的系統在 reachability 上的能力邊界；引用 Ch 33-34 相關論文）**

- 你的系統如何把 CVE 關聯到 SBOM 裡的元件？用什麼 database？PURL-to-CVE 的比對用什麼 schema（OSV？NVD CPE matching？）
- 你的系統有沒有 reachability 分析能力？如果有，支援哪些語言、精確度預期是什麼？如果沒有，你怎麼處理誤報問題？
- VEX 整合：你的系統如何接收 VEX 並用它過濾漏洞告警？自動 vs 人工？
- 引用：至少一篇 Ch 33-34 引用的相關論文支撐你的方案選擇

**Deliverable 6：信任鏈設計（驗收：說明 trust root、signing 機制、透明日誌使用；對照 Ch 36 的取捨分析；說明離線驗證如何支援）**

- 你的系統用什麼機制讓下游信任你的 SBOM？（簽章？attestation？透明日誌？）
- Trust root 是什麼？為什麼選這個設計？（參考 Ch 36 的決策框架）
- 使用 sigstore/Rekor 還是自建 PKI？說明選擇的理由（引用 Ch 36 的取捨分析）
- 離線驗證：你的下游如果在 air-gapped 環境，如何驗證你的 SBOM 的完整性？
- 信任撤銷：如果你的 signing key 或 OIDC 身份被攻陷，通知機制是什麼？

**Deliverable 7：已知限制與未解問題（驗收：誠實列出至少 4 個已知限制；說明緩解措施或監控計畫）**

- 你的系統有哪些已知的盲點（對照 Ch 35 的對映表，哪些攻擊向量是「×」）？
- 對於你的限制，你有什麼緩解措施？（例如：「binary 識別不可靠，我在 SBOM 的 tool metadata 裡標記信心度，讓消費者能過濾低信心的元件」）
- 哪些你列在 Ch 37「還沒解的硬問題」裡的問題直接影響你的設計？你怎麼處理？

## 如果你卡住了

**卡在選定位**：問自己「如果我是一個 Java 工具鏈工程師 vs 一個企業資安架構師，我的日常工作更像哪個」。生成引擎偏向 compilers/parsers/工具開發，消費平台偏向系統設計/安全運營。

**卡在威脅模型**：打開 Ch 35 的對映表，從第一列開始，每個向量問「這個攻擊的後果是什麼？我的系統有沒有可以偵測或阻止它的設計點？」。不需要對每個向量都有完整的防禦——誠實說「這個向量超出我的系統邊界，由上下游解決」也是有效的設計決策。

**卡在信任鏈設計**：從最簡單的情況開始想：「下游拿到我的 SBOM，他怎麼知道這份 SBOM 是我發的、而不是被中間人替換的」。然後問「如果我用的 key 洩漏了怎麼辦」。Ch 36 的選擇矩陣（trust root 決策 1/2/3）直接可以用。

**卡在寫設計文件**：參考下面的範例設計文件，看它是怎麼結構化的。但先自己寫一遍再看，否則學不到東西。

**卡在引用**：Part 8 每一章的「精讀論文」段落都標出了「和本章關聯」——那就是告訴你在哪個設計決策下應該引用哪篇。

## 分階段建議

如果你要分多個時間段完成：

**第一次坐下（60-90 分鐘）**：選定位、寫 Deliverable 1（需求與威脅模型）、畫架構圖草稿（Deliverable 2）。這兩個是其他 deliverable 的基礎，先定下來。

**第二次坐下（60-90 分鐘）**：完成 Deliverable 3（元件識別）和 Deliverable 4（資料模型）。這兩個最需要查 Part 8 前半的細節。

**第三次坐下（60-90 分鐘）**：完成 Deliverable 5（漏洞關聯）和 Deliverable 6（信任鏈）。這兩個最需要引用論文，把你的選擇決策寫清楚。

**最後修整（30 分鐘）**：完成 Deliverable 7（已知限制），然後整體讀一遍，確認每個關鍵決策都有文獻支撐，確認威脅模型和設計決策是一致的。

---

<details>
<summary>範例設計文件（選消費平台定位）——寫完再看，否則學不到東西</summary>

# 企業 SBOM 消費平台架構設計文件

**版本**：0.1（設計稿）
**定位**：SBOM 消費平台
**作者**：（你的名字）
**日期**：2026-08-18

---

## 1. 需求與威脅模型

### 背景

這個平台服務於一家有 200 個內部服務的中型技術公司。每個服務有自己的 SBOM（由各自的 CI 生成），每週的新 CVE 可能影響數十個服務。目前的狀況是：每個團隊各自跑 grype，產生不一致的報告，沒有跨服務的統一視圖，Log4Shell 爆發時花了 3 天才知道哪些服務受影響。

### 功能性需求

1. **FR-1**：接收來自多個 CI 系統（GitHub Actions、GitLab CI）的 SBOM（SPDX 和 CycloneDX 兩種格式）
2. **FR-2**：對所有接收的 SBOM 持續做漏洞掃描，新 CVE 發布後 1 小時內觸發重新評估
3. **FR-3**：支援接收和應用 VEX 文件，讓服務團隊標記「這個 CVE 在我們的部署場景不可達」
4. **FR-4**：提供跨服務的統一告警儀表板，支援按嚴重度、服務、元件篩選
5. **FR-5**：提供 API 讓其他系統（部署審批、合規報告）查詢某個服務的目前漏洞狀態

### 安全需求

1. **SR-1**：平台只接受有有效簽章的 SBOM，拒絕未簽章的 SBOM（防止 SBOM 本身被篡改）
2. **SR-2**：SBOM 的提交來源必須對應 CI 系統的 OIDC identity（防止任意方提交假冒的 SBOM）
3. **SR-3**：平台自身的 CVE 比對邏輯和 VEX 處理邏輯必須有版本控制和審計日誌（讓告警來源可追溯）

### 威脅模型

對照 Ch 35 的對映表，這個平台需要處理以下向量：

| 攻擊向量 | 對我們的影響 | 我們的對策 | 防/偵 | 文獻支撐 |
|---|---|---|---|---|
| SBOM 本身被篡改 | 篡改後的 SBOM 讓我們對漏洞視而不見 | SR-1：驗簽章（cosign verify） | 擋 | Sigstore CCS 2022：keyless 簽章讓每份 SBOM 有可驗的 OIDC 身份 |
| 虛假 SBOM（CI 被入侵後上傳假 SBOM）| 惡意元件被標記為安全 | SR-2：OIDC identity 綁定 CI 工作流 URL | 擋 | Sigstore CCS 2022 Section 3：Fulcio 憑證的 Subject 包含 workflow path |
| 傳遞依賴感染 | 底層元件有漏洞但 SBOM 沒有記錄 | 要求 SBOM 必須包含完整傳遞依賴（sbomqs `complete-dependencies` 分數 ≥ 8） | 偵 | Zimmermann USENIX 2019：blast radius 概念說明完整傳遞依賴的必要性 |
| 惡意 postinstall script | 超出本平台邊界（安裝時已發生）| 在告警裡標記「此元件有已知惡意 postinstall 記錄」（依賴 OSV 和 Backstabber 資料庫） | 偵 | Ohm DIMVA 2020：56% 惡意套件在安裝時觸發，已超出消費平台能防的範圍 |
| VEX 被濫用（服務團隊虛假標記 not_affected）| 真實漏洞被隱藏 | SR-3：所有 VEX 標記需有 audit trail，定期人工複審高風險的 not_affected 標記 | 偵 | Xia ICSE 2023：VEX actionability 問題；自動化 VEX 精確度不足，需人工複審 |
| Version range 操縱（SBOM 記錄的是 range 非解析後版本）| 比對到錯誤版本 | 接收 SBOM 時驗證「所有元件必須有精確版本號」，有 range 的拒絕接收並要求重新生成 | 擋 | — |

超出本平台邊界、由生成側或 CI 處理的向量（dependency confusion、typosquatting 的識別、code contribution 階段的社工）：明確標記為「本平台不防，依賴上游 SBOM 生成方的品質保證」。

---

## 2. 系統架構

### 元件圖

```
         外部 CI 系統                 外部漏洞 DB
    (GitHub Actions / GitLab CI)   (OSV / NVD / GitHub Advisory)
            │                              │
            │ SBOM + 簽章 bundle           │ CVE feeds
            ▼                              ▼
    ┌──────────────────┐         ┌──────────────────┐
    │  Ingestion API   │         │   CVE Sync       │
    │  - 驗 cosign 簽章│         │   - 每小時同步   │
    │  - 驗 OIDC 來源  │         │   - OSV schema   │
    │  - 格式正規化    │         └─────────┬────────┘
    │    (SPDX↔CDX→    │                   │
    │     internal)    │                   ▼
    └────────┬─────────┘         ┌──────────────────┐
             │                   │   CVE Store      │
             │ internal SBOM     │  (PostgreSQL,    │
             ▼                   │   PURL indexed)  │
    ┌──────────────────┐         └─────────┬────────┘
    │   SBOM Store     │◄────────────────┐ │
    │  (PostgreSQL,    │                 │ │ CVE-to-PURL
    │   component      │                 │ │ matching
    │   + dep graph)   │                 │ │
    └────────┬─────────┘         ┌───────┴─┴──────┐
             │                   │  Correlation   │
             │ component list    │  Engine        │
             └──────────────────►│  - PURL exact  │
                                 │    match       │
                                 │  - alias 解析  │
                                 └───────┬────────┘
                                         │
                          ┌──────────────┴──────────────┐
                          ▼                             ▼
                 ┌──────────────┐             ┌──────────────┐
                 │ Alert Store  │             │  VEX Engine  │
                 │ + Audit Log  │◄────────────│  - 接收 VEX  │
                 └──────┬───────┘             │  - 驗 VEX 簽章│
                        │                     │  - 過濾告警  │
                        ▼                     └──────────────┘
                 ┌──────────────┐
                 │  Dashboard   │
                 │  + API       │
                 └──────────────┘
```

### 元件職責與技術選型

**Ingestion API**
- 職責：接收 SBOM bundle，驗證簽章，正規化格式，存入 SBOM Store
- 技術：Go HTTP server（選 Go 的理由：型別安全、並發易寫、cosign library 有官方 Go SDK）
- 設計決策：格式正規化在 ingestion 時做（統一轉成 internal graph 格式），不是在 query 時做。理由：讓 Correlation Engine 只處理一種格式，降低比對邏輯的複雜度。代價：ingestion 略慢（但一份 SBOM 通常在 30MB 以下，秒級可接受）。

**SBOM Store**
- 職責：儲存元件資訊（名稱、版本、PURL、依賴邊）、SBOM metadata（產生時間、生成工具、服務名稱）
- 技術：PostgreSQL，用 JSONB 欄位存元件的 extra properties，用 ltree 擴充存依賴樹
- 設計決策：用 relational DB 而不是 graph DB（Neo4j）。理由：我們的查詢模式是「給定 CVE，找所有受影響的服務」（一個 PURL join CVE 的查詢），而不是圖遍歷查詢；PostgreSQL 的 PURL index + JSONB 效能足夠，且運維成本遠低於 graph DB。如果未來需要做 impact blast radius 查詢，再評估是否遷移。（這個決策是設計選型，不是論文支撐的技術問題）

**Correlation Engine**
- 職責：把 SBOM 裡的元件 PURL 對比 CVE Store 裡的受影響版本範圍，產生告警
- 核心比對邏輯：OSV schema 的 `affected[].package.purl` + `affected[].ranges` 版本比對（使用 OSV 的官方 Go library，它實現了 SemVer 和 ecosystem-specific 版本比對）
- 設計決策：不自己寫版本比對邏輯，使用 OSV 的 library。理由：版本比對的細節（npm 的 `^` range、Go 的 pseudo-version）非常容易出錯，OSV library 已經是業界標準且有測試覆蓋。
- 引用：Ch 33 的 OSV schema 討論——OSV 的跨生態設計讓 PURL-to-CVE 的比對邏輯統一化

**VEX Engine**
- 職責：接收 VEX 文件、驗簽章、解析 `not_affected` / `fixed` / `under_investigation` 聲明、用 VEX 過濾 Correlation Engine 的輸出
- 設計決策：VEX 的 `not_affected` 標記需要人工審核才能確認狀態。理由：Xia ICSE 2023 指出 VEX actionability 問題——服務團隊可能在沒有充分分析的情況下標記 `not_affected`，自動信任會讓真實漏洞被隱藏。因此我們的流程是：VEX 標記進入「待審核」狀態，7 天內沒有安全工程師確認則告警繼續出現。
- 限制：沒有自動 reachability 分析能力。Ch 37 指出自動 VEX 生成的精確度問題：目前所有工具的自動 reachability 判斷仍有高誤報，我們不打算在第一版引入。這意味著所有 VEX 標記需要人工，是已知的 scalability 限制。

---

## 3. 元件識別策略

我們是消費平台，不負責識別——我們接收生成方的 SBOM。但我們需要一個「接收品質門檻」。

**有 manifest 的情況（品質驗收）**：
- 要求：所有元件必須有精確版本號（不接受 version range）
- 要求：所有元件必須有 PURL（缺少 PURL 的元件無法做 CVE 比對，列為「blind spot 元件」）
- 使用 sbomqs 驗證接收的 SBOM 必須達到 `components-have-purl` ≥ 8.0、`components-have-checksums` ≥ 8.0

**無 manifest / binary-only 的情況（識別不確定性處理）**：

一些服務的 SBOM 裡會有「信心度低」的元件——由 binary 分析識別出的、沒有精確 PURL 的元件。生成方必須用 CycloneDX 的 `properties` 欄位標記：

```json
{
  "type": "library",
  "name": "openssl",
  "version": "1.1.1k",
  "properties": [
    { "name": "sbom:identificationConfidence", "value": "low" },
    { "name": "sbom:identificationMethod", "value": "binary-string-matching" }
  ]
}
```

我們的平台對 `identificationConfidence: low` 的元件，漏洞告警會帶有「低信心」標記，讓工程師知道這個元件的識別可能不準確。

**引用**：Ch 32 的論文識別出 binary 元件識別的精確度問題——靜態字串比對有偽陽性（看起來像 openssl 1.1.1k 但其實是自行修改過的 fork），因此我們的設計選擇「接收信心度元資料」而不是「盲目信任」。

---

## 4. 依賴圖資料模型與可合併性

### 內部資料模型

```sql
-- 元件表
CREATE TABLE components (
    id          UUID PRIMARY KEY,
    purl        TEXT NOT NULL,          -- pkg:npm/lodash@4.17.21
    name        TEXT NOT NULL,
    version     TEXT NOT NULL,
    ecosystem   TEXT,                   -- npm, pypi, go, maven, ...
    sbom_id     UUID REFERENCES sboms(id),
    confidence  TEXT DEFAULT 'high',   -- high / low（binary 識別的）
    raw_props   JSONB                  -- 原始的 extra properties
);

-- 依賴關係表
CREATE TABLE dependencies (
    from_component UUID REFERENCES components(id),
    to_component   UUID REFERENCES components(id),
    dep_type       TEXT,  -- 'direct' | 'transitive' | 'dev' | 'runtime'
    PRIMARY KEY (from_component, to_component)
);
```

### 合併規則

當同一個服務提交多份 SBOM（例如同一天的兩次 CI 構建），或者我們要合併兩份不同範疇的 SBOM（source SBOM + binary SBOM）：

**同一元件的判斷邏輯（優先順序）**：
1. PURL 完全一致（最優先）
2. name + version + ecosystem 一致（PURL 缺失時的 fallback）
3. content hash 一致（適用於 binary 元件）

**合併衝突處理**：
- 版本不同（同名元件，版本 `1.0.0` vs `1.0.1`）：保留兩個，在 SBOM metadata 裡標記「版本衝突，來源 A vs 來源 B」，觸發告警要求服務團隊確認。不自動選擇哪個更「正確」——版本衝突往往意味著生成流程有問題，需要人工確認。
- 欄位不完整（A 有 PURL，B 沒有）：合併後使用 A 的 PURL，在 audit log 裡記錄來源。
- 完全矛盾（A 說版本是 `1.0.0`，B 說是 `2.0.0`，但 PURL 和 name 一樣）：拒絕合併，要求人工解決，在儀表板上顯示「SBOM 合併衝突」。

---

## 5. 漏洞關聯與可達性方案

### CVE 比對邏輯

我們使用 OSV（Open Source Vulnerability）schema 作為統一比對格式：
1. 每小時從 OSV API（`osv.dev/v1/vulns`）和 GitHub Advisory Database 同步 CVE
2. 把 CVE 的 `affected[].package.purl` + `affected[].ranges` 存入 CVE Store
3. 對每份新進的 SBOM，用 PURL 做精確比對，版本比對使用 OSV 的 ecosystem-specific rules

**為什麼用 OSV 而不是 NVD CPE**：NVD 的 CPE 比對有長期存在的精確度問題（Ch 14 的討論）——過時的 CPE 字典、PURL 到 CPE 的映射缺失。OSV schema 設計時就以 PURL 為優先，比對精確度更高。代價：OSV 對 C/C++ 的覆蓋率低於 Java/Python/npm，C/C++ 元件的漏洞可能漏掉。這是已知限制（見 Deliverable 7）。

**引用**：Ch 33 的 OSV schema 設計討論支撐了選 OSV 的決策。

### Reachability 分析

第一版不做自動 reachability 分析。

理由（引用 Ch 37 的硬問題 #2）：Williams TOSEM 2025 和 Xia ICSE 2023 都指出 reachability 規模化是一個未解的硬問題，尤其對 C/C++ 代碼庫。在精確度未達到「能可靠地減少誤報」的水平之前，引入自動 reachability 分析會給用戶錯誤的「漏洞被降級了」的信心。我們的決策是：告警用「有漏洞但未確認 reachability」，讓工程師手動確認或提交 VEX，而不是用不可靠的自動分析來過濾。

這個決策的已知代價：告警量會比有 reachability 的系統高，誤報率會更高，工程師會更容易有「告警疲勞」。緩解措施：在儀表板上支援「按元件在代碼庫裡的 import 深度」排序，讓最可能被執行到的元件的漏洞排在前面（這不是精確 reachability，但提供了粗粒度的優先排序）。

### VEX 整合

接受 CycloneDX VEX 和 CSAF VEX 格式。VEX 文件必須有 cosign 簽章，簽章者必須是提交 SBOM 的同一個 OIDC identity（防止其他方提交假的 VEX）。

VEX 狀態流：
```
new CVE
  → OPEN（預設，需要關注）
    → UNDER_INVESTIGATION（工程師開始分析）
      → NOT_AFFECTED（VEX 標記 + 理由，進入 pending_review）
        → CONFIRMED_NOT_AFFECTED（安全工程師確認，告警消失）
        → REOPENED（7 天沒有人確認，回到 OPEN）
      → AFFECTED（確認受影響，進入修復追蹤）
```

---

## 6. 信任鏈設計

### Trust Root 決策

**選 sigstore（公開 Rekor + Fulcio）而不是自建 CA**。

理由（對照 Ch 36 的決策矩陣）：
- 我們的工程師有 GitHub 帳號，CI 跑在 GitHub Actions，OIDC token 已有現成基礎設施
- 自建 CA 需要 PKI 基礎設施維護（key rotation、CRL/OCSP、HSM 管理）——以我們的規模（200 個服務，10 個安全工程師），這個運維成本不合理
- sigstore 的公開 Rekor 讓外部審計者也能驗我們的 SBOM 簽章，未來有外部合規需求時不需要改信任設計

代價（誠實列出）：
- OIDC provider（GitHub）成為信任根。如果 GitHub 的 OIDC service 被攻陷，攻擊者能偽裝成我們的 CI。這在我們的風險評估裡是可接受的（我們已經在 GitHub 上存放代碼，這不是新增的信任面）。
- 公開 Rekor 會公開我們 SBOM 的 artifact hash 和 CI identity。我們的 SBOM 不包含非公開的業務邏輯，所以這個隱私問題可接受。如果未來有業務線的服務 SBOM 包含敏感資訊，需要評估 private Rekor instance。

**引用**：Ch 36 的 sigstore keyless 設計論證（Newman, Meyers, Torres-Arias, CCS 2022）——OIDC 身份綁定 + 短暫憑證 + 公開可審計 log，降低長期 key 管理的人因失敗風險。

### Signing 機制

CI 在 SBOM 生成後立即用 `cosign sign --fulcio-url ... --rekor-url ... <sbom-file>` 產生簽章 bundle。bundle 跟 SBOM 一起上傳到平台。

Ingestion API 在接收時執行：
```
cosign verify-blob \
  --bundle <sbom.bundle> \
  --certificate-identity-regexp "^https://github.com/myorg/.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  <sbom-file>
```

失敗的驗證會被拒絕，並在 audit log 裡記錄「來源 IP、聲稱的服務名稱、拒絕原因」。

### 離線驗證支援

bundle 格式已包含 Rekor inclusion proof 和 Fulcio 憑證，消費者可以做完整的離線驗證，只需要：
- sigstore 的 root CA（公開，可預先存）
- Rekor 的 root public key（公開，可預先存）

對 air-gapped 的下游（例如某個政府客戶需要接收我們的 SBOM 在離線環境驗證），提供 bundle + SBOM 兩個檔案，配上 `cosign verify-blob --bundle` 的說明文件。

### 信任撤銷

目前的撤銷機制：如果 GitHub Actions 的某個工作流被攻陷，我們的應對是：
1. 立即聯繫 sigstore 社群，在 Rekor 上查詢那個 CI identity 的所有簽章記錄
2. 把那個時間窗口的所有 SBOM 標記為「需要重新驗證」
3. 重新觸發那些服務的 CI 產出新的 SBOM

這個流程沒有自動化，是已知的弱點。長期解法是引入 TUF（The Update Framework）管理信任根，讓 key 撤銷可以機器可讀地傳播——但這超出第一版的範疇，記錄為技術債。

---

## 7. 已知限制與未解問題

### 設計盲點（對照 Ch 35 的對映表）

| 攻擊向量 | 我們的對策 | 為什麼有限制 |
|---|---|---|
| 惡意 postinstall script | 偵（OSV 資料庫有收錄時）| 安裝時已發生，消費平台的邊界外；需要上游的 vetting pipeline |
| 代碼貢獻階段的社工 | × | 超出本平台邊界；我們信任 SBOM 來自正常構建流程 |
| Typosquatting 識別 | 偵（OSV 有記錄時）| 消費平台不做命名分析，依賴 OSV 的覆蓋率 |
| Binary-only 元件的準確識別 | 偵（低信心標記）| Ch 32 指出 binary 識別精確度問題，我們選擇透明化而不是假裝準確 |

### 硬問題的影響

對照 Ch 37 的五個硬問題：

1. **Naming alignment**：跨生態的同名元件（npm `uuid` vs PyPI `uuid`）在我們的比對邏輯裡是兩個不同元件，如果某個 CVE 只在 npm 的 UUID 庫有，PyPI 版本的告警不會出現——即使它們可能有相同的漏洞代碼。緩解：定期人工審核「跨生態同名元件」清單。

2. **Reachability 規模化**：第一版不做，已知會有告警疲勞問題。監控計畫：追蹤「被 VEX 標記 not_affected 的告警佔比」，如果超過 60%，說明系統對工程師的 actionability 太低，需要引入粗粒度的 reachability 近似。

3. **VEX 自動化**：沒有自動化，全部人工。這是 scalability 風險：200 個服務每個月新 CVE 可能有數十個，人工確認的速度可能趕不上。長期解法待評估（可能是引入 CodeQL 對 Java 服務做粗粒度 reachability）。

4. **C/C++ 元件的 OSV 覆蓋率低**：我們的 CVE 比對對 C/C++ 函式庫的覆蓋率不完整。緩解：對 C/C++ 元件額外對比 NVD CPE（用 NVD 的 CPE match API 作為補充，但接受其較低的精確度）。

5. **跨組織信任**：目前沒有接受外部供應商 SBOM 的能力。如果未來需要整合，信任決策（「我接受哪些供應商的 SBOM 簽章」）需要一個 policy engine，這超出第一版範疇。

</details>

---

## 驗收 Checklist

完成後你應該能回答：

**架構設計的完整性**
- [ ] 我的設計文件有明確的定位（生成引擎 or 消費平台），不是兩個都要
- [ ] 每個 deliverable 都有完成，沒有「待補充」的空欄位
- [ ] 架構圖有元件和資料流，不只是一個方塊

**論文引用的嚴謹性**
- [ ] 每個關鍵設計決策至少有一個論文支撐（而不是「我覺得這樣比較好」）
- [ ] 引用的論文都是本課引用過的（Ladisa/Zimmermann/Ohm/Torres-Arias/Newman/Xia/Williams），沒有虛構的引用
- [ ] 引用論文時說明「這篇論文的哪個結論支撐了你的哪個決策」

**威脅模型的一致性**
- [ ] 威脅模型裡列出的攻擊向量，在設計裡都有對應的處理（防/偵/誠實說「×」）
- [ ] 對「×」（無法防禦）的向量，有說明它超出哪個邊界、由誰來處理
- [ ] 威脅模型和設計決策是一致的（不能威脅模型說要防 X，但設計裡完全沒有對應機制）

**誠實性（最重要的驗收項）**
- [ ] Deliverable 7（已知限制）有列出至少 4 個真實的限制，而不是「目前沒有限制」
- [ ] 對 Ch 37 的「還沒解的硬問題」，有說明哪些問題直接影響你的設計
- [ ] 沒有「SBOM 解決了所有供應鏈問題」這樣的過度聲明

**Part 8 概念的整合**
- [ ] 元件識別策略引用了 Ch 32 的相關論文
- [ ] 漏洞關聯方案引用了 Ch 33-34 的論文
- [ ] 信任鏈設計引用了 Ch 36 的取捨分析（並且引用了 Sigstore 論文或 in-toto 論文）
- [ ] 威脅模型引用了 Ch 35 的攻擊向量對映表（並且引用了 Ladisa 或 Ohm 論文）
- [ ] 已知限制對照了 Ch 37 的「硬問題」清單

## 延伸挑戰

如果你完成了基本規格，想要更深：

**挑戰 1：設計一個 SBOM diff 系統**
你的消費平台（或生成引擎）每次 CI 構建都會收到新版 SBOM。設計一個 SBOM diff 算法：給定版本 N 和版本 N+1 的 SBOM，產出「哪些元件被新增、移除、或版本更新了」，並且識別出「哪些版本更新引入了新的 CVE」。考慮：diff 的正規化（PURL 一樣但 checksum 不同算不算變化？）、diff 的儲存格式、告警的觸發邏輯。

**挑戰 2：設計一個 SBOM 信任聯邦協議**
你的平台開始接受外部供應商的 SBOM。設計一個「我信任哪些供應商的 SBOM」的 policy 格式（機器可讀的信任政策），以及一個接收外部 SBOM 時的驗證流程。考慮：trust root 的傳遞性（我信任 A，A 信任 B，我多大程度上應該信任 B？）、信任政策的版本管理（昨天信任的，今天可以撤銷嗎？）。

**挑戰 3：設計一個 SBOM 品質閘道**
設計一個 CI 插件，在 SBOM 提交到平台之前做品質驗證，不符合品質標準的 SBOM 被拒絕（就像 linter 阻止品質不夠的代碼進 main）。定義你的品質規則（哪些是硬性要求、哪些是警告）、定義「失敗了怎麼辦」的工作流（是直接阻擋部署，還是只記錄告警？）、考慮如何讓工程師不把你的閘道視為障礙而是工具。

## 自我檢核

做完 Capstone 後，你應該能用自己的話解釋以下問題：

- 我的設計防的是哪些攻擊向量？依據是哪篇論文？
- 我的設計在哪些攻擊向量上是「×」？我有沒有誠實地在文件裡說清楚？
- 如果我的 signing key 或 OIDC identity 被攻陷，我的系統會怎麼通知下游？我的撤銷機制是什麼？
- 我的 VEX 策略為什麼不做（或做）自動化？依據是 Ch 37 的哪個硬問題？
- 我的元件識別對「binary-only、沒有 manifest」的情況怎麼處理？精確度預期是什麼？

---

整個 Part 8 的旅程走到這裡，從「怎麼設計一個生成引擎的架構空間」（Ch 30）、「命名和版本的理論地基」（Ch 31）、「生成引擎的 manifest 到 binary 識別」（Ch 32）、「消費平台的漏洞關聯」（Ch 33-34）、「威脅模型讓防禦設計有根據」（Ch 35）、「信任鏈讓下游能相信你的 SBOM」（Ch 36）、「實證告訴你從業者在痛什麼、哪些問題還沒解」（Ch 37）——你現在有足夠的設計工具，去面對一個真實的 SBOM 系統設計任務，並且不只說「這樣設計比較好」，而是說得出「這樣設計，因為 Ladisa 的攻擊樹告訴我 dependency confusion 的 PURL registry 資訊很重要，Xia 的實證告訴我不做 reachability 自動化是合理的起點，Newman 的 keyless 設計讓我能在沒有 PKI 基礎設施的情況下做到 artifact 完整性保護」。

→ [回到課程首頁](./README.md)
