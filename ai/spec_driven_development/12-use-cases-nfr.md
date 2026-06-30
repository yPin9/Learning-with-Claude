# Ch 12 — Use Case 與非功能需求

> **目標**：掌握 Jacobson 發明的 Use Case 概念與 Cockburn fully-dressed 模板（happy path + extensions），理解 ISO/IEC 25010:2023 定義的九個品質特性，能把非功能需求（Non-Functional Requirements, NFR）寫成可量測的陳述，而不是空洞的「系統應該要快」。

---

## 在這之前：需求記法的光譜

我們在前幾章已經走過：

- [Ch 9 User Story 與 INVEST](./09-user-stories-invest.md) — 刻意保持模糊的「對話起點」
- [Ch 10 從驗收條件到 BDD：Given-When-Then](./10-acceptance-criteria-bdd.md) — 把驗收條件結構化、可執行
- [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md) — 把句子模式化、去除歧義

這一章是 Part 2 的最後一塊：Use Case 與 NFR。它們在需求工程史上是不同的發展路線，各自解決 User Story 無法處理的問題。

---

## 為什麼需要 Use Case？一個現實場景

想像你在 1986 年的 Ericsson。系統有幾十個角色、幾百條互動路徑、還有十幾種「如果這步失敗了怎麼辦」。User Story 還沒被發明；你的工具是自然語言需求文件，充斥著 [Ch 8](./08-why-requirements-hard.md) 點名的八種病。

Ivar Jacobson（Ericsson）的洞察是：**功能需求的核心不是「系統的屬性」，而是「使用者想達成的目標」**。

他在 1986 年的論文裡引入了「Use Case（用例）」的概念，1992 年的書 *Object-Oriented Software Engineering: A Use Case Driven Approach* 把它系統化。基本圖像是：

```
Actor ──────▶ [ Use Case ] ──────▶ System
              「我要達成 X」
```

Actor（行為者）不一定是人——可以是另一個系統、定時器、外部事件。Use Case 描述的是 Actor 為了達成某個目標而與系統進行的一系列互動。

---

## Cockburn 的 Fully-Dressed 模板

Jacobson 給了概念，Alistair Cockburn 給了格式。

2000 年的 *Writing Effective Use Cases* 定義了「fully-dressed（完全裝備的）」模板。它比 User Story 更重，因為它要把所有成功與失敗路徑都寫清楚。

以下是一個具體範例——電商平台的「買家完成結帳」。

---

### 範例：Use Case 5 — 完成結帳

| 欄位 | 內容 |
|---|---|
| **Use Case 編號** | UC-5 |
| **Use Case 名稱** | 完成結帳（Complete Checkout） |
| **作用域（Scope）** | 電商購物網站 |
| **層次（Level）** | Sea-level（使用者目標） |
| **主要行為者（Primary Actor）** | 已登入買家 |
| **利害關係人與利益** | 買家：確認訂單與付款；賣家：收到可履行的訂單；支付閘道：處理交易 |
| **前置條件（Preconditions）** | 買家已登入；購物車至少有一件商品；商品庫存仍足夠 |
| **成功後置條件** | 訂單已建立、付款已授權、庫存已扣除、確認信已寄出 |
| **失敗後置條件** | 購物車維持原狀、庫存未變動、未產生訂單 |
| **觸發條件** | 買家點擊「前往結帳」 |

**主要成功情境（Main Success Scenario / Happy Path）**

```
1. 系統顯示購物車商品與合計金額
2. 買家確認收件地址（可沿用已儲存地址或輸入新地址）
3. 系統計算運費並更新總金額
4. 買家選擇付款方式（信用卡 / 電子錢包）
5. 買家輸入或選擇付款資訊
6. 買家點擊「確認付款」
7. 系統向支付閘道發送授權請求
8. 支付閘道回傳授權成功
9. 系統扣除庫存
10. 系統建立訂單（狀態：待出貨）
11. 系統寄出訂單確認信
12. 系統顯示訂單完成頁面，含訂單編號
```

**擴充情境（Extensions）**：對應 happy path 步驟編號

```
2a. 買家沒有儲存的地址：
    2a1. 系統顯示地址表單
    2a2. 買家填寫並儲存
    繼續第 3 步

5a. 買家選擇「新增信用卡」：
    5a1. 系統顯示信用卡輸入表單（PCI-DSS 合規 iFrame）
    5a2. 買家輸入卡號、有效期、CVV
    繼續第 6 步

7a. 網路逾時，無法連線支付閘道：
    7a1. 系統等待 3 秒後重試（最多 2 次）
    7a2. 若仍失敗，顯示「付款暫時失敗，請稍後再試」
    7a3. 訂單進入「付款待確認」佇列，由後台非同步重試
    → 失敗後置條件

8a. 支付閘道回傳授權失敗（餘額不足 / 卡片遭拒）：
    8a1. 系統顯示「授權失敗：[原因]，請更換付款方式」
    8a2. 返回第 4 步
    → 失敗後置條件

9a. 庫存在付款授權後、扣除前已被其他訂單佔用：
    9a1. 系統撤銷授權（Void）
    9a2. 通知買家「商品庫存不足，已退款」
    → 失敗後置條件
```

---

### 目標層次：風箏 / 海平面 / 魚

Cockburn 用三個比喻區分 Use Case 的抽象層次：

| 層次 | 比喻 | 說明 | 例 |
|---|---|---|---|
| Summary | 風箏（Kite）🪁 | 高階業務目標，跨多個使用者目標 | 「管理訂單生命週期」 |
| User Goal | 海平面（Sea-level） | 一個使用者坐下來、完成之後起身 | 「完成結帳」 |
| Subfunction | 魚（Fish）🐟 | 技術子步驟，被 sea-level UC 呼叫 | 「驗證信用卡格式」 |

寫 Use Case 時，**主力寫 sea-level**。Wind-kite 用來規劃系統邊界；fish 只在子函式複雜到需要獨立描述時才寫。

---

## User Story vs Use Case：不是替代，是工具箱

這是業界最常見的混淆點。釐清一下：

| 面向 | User Story | Use Case（Fully-Dressed） |
|---|---|---|
| **用途** | 對話起點，刻意保持可協商 | 完整記錄已協商好的互動 |
| **規模** | 短，一張索引卡 | 長，可能一頁以上 |
| **失敗路徑** | 不處理（放在 AC 裡） | Extensions 段落，系統且完整 |
| **生命週期** | Sprint 規劃工具，用後即棄 | 需求文件，跟著系統演進 |
| **適合場景** | 敏捷小團隊、快速迭代 | 合約交付、醫療/航太/金融合規 |

敏捷環境下常見的折衷：**用 User Story 管 backlog，針對複雜流程補寫 Use Case 的 Extensions**，不要非得二選一。

---

## 非功能需求：「系統應該要快」不算需求

非功能需求（Non-Functional Requirements, NFR）——ISO/IEC 25010 稱之為「品質需求（Quality Requirements）」——描述系統「做得多好」，而不是「做什麼」。

在 1990 年代之前，NFR 常被寫成這樣：

> 系統應有良好的效能。
> 系統必須安全。
> 介面需友善使用者。

這種寫法不可量測、不可測試、不可驗收，等於沒寫。

---

## ISO/IEC 25010:2023 的九個品質特性

ISO/IEC 25010（版本注意：2011 年有舊版；2023 年的 Edition 2 增加了 Safety、重命名了兩個特性，以下依 2023 年版本描述）定義了九個頂層品質特性：

| # | 特性（中文） | 特性（英文） | 2023 版變動 |
|---|---|---|---|
| 1 | 功能適切性 | Functional Suitability | 同舊版 |
| 2 | 效能效率 | Performance Efficiency | 同舊版 |
| 3 | 相容性 | Compatibility | 同舊版 |
| 4 | 互動能力 | Interaction Capability | 舊版稱 Usability |
| 5 | 可靠性 | Reliability | 同舊版 |
| 6 | 安全性 | Security | 2023 增加 Resistance 子特性 |
| 7 | 可維護性 | Maintainability | 同舊版 |
| 8 | 彈性 | Flexibility | 舊版稱 Portability；2023 增加 Scalability |
| 9 | 安全（功能安全） | Safety | **2023 年全新加入** |

（版本警示：精確的子特性清單應查閱 ISO/IEC 25010:2023 原文或 iso25000.com，子特性命名在不同摘要版本間有差異。）

---

## 把 NFR 寫成可量測陳述：SMART NFR

可量測的 NFR 要回答四個問題：

1. **什麼** 被量測？（指標）
2. **在什麼條件下**？（前提、負載情況）
3. **目標值是什麼**？（數字、上限/下限）
4. **怎麼驗證**？（量測方式）

以下是同一個功能需求配上四個品質維度的 NFR 寫法：

---

### 場景：電商結帳系統

**功能需求（FR）**

```
FR-7: 系統應支援使用者以信用卡完成結帳。
```

**Performance Efficiency（效能效率）**

```
NFR-PE-1: 在每秒 500 筆並行結帳請求的負載下，
          UC-5 第 7 步（送出付款授權）的 P95 回應時間
          不得超過 2,000 ms。
量測方式：k6 壓測腳本，持續 10 分鐘，
          從 CI pipeline 每次 release 前執行一次。
```

**Reliability（可靠性）**

```
NFR-RE-1: 結帳付款流程（UC-5 步驟 6–11）的月可用性
          不得低於 99.9%（每月最多允許 43.8 分鐘停機）。
量測方式：CloudWatch Availability Metric，
          以 5 分鐘粒度，monthly SLA report 追蹤。
```

**Security（安全性）**

```
NFR-SE-1: 信用卡號碼在系統內部任何 log、trace、
          或 error message 中均不得出現未遮罩的完整 16 位數。
量測方式：每次 PR 觸發 static analysis（Semgrep 規則集），
          並每季對 staging 環境 log 執行 PAN 掃描。
```

**Maintainability（可維護性）**

```
NFR-MA-1: 結帳模組（checkout-service）的單元測試覆蓋率
          不得低於 80%（branch coverage），
          並在 CI 流程中強制執行。
量測方式：JaCoCo report，PR merge gate。
```

---

## 架構心智圖：FR 與 NFR 的關係

```
          ┌─────────────────────────────────┐
          │          Use Case UC-5          │
          │       完成結帳（Happy Path）    │
          │   + Extensions（失敗路徑）      │
          └──────────────┬──────────────────┘
                         │ 功能邊界
              ┌──────────▼──────────┐
              │   Functional Req.   │
              │  FR-7: 信用卡結帳   │
              └──────────┬──────────┘
                         │ 同一功能，品質維度
    ┌────────────────┬───┴────────────┬────────────────┐
    ▼                ▼                ▼                ▼
NFR-PE-1         NFR-RE-1         NFR-SE-1         NFR-MA-1
(P95 < 2s)     (可用 99.9%)    (不得露出 PAN)  (覆蓋率 ≥ 80%)
```

每條 NFR 都應該可以追溯到至少一個 FR 或 Use Case。沒有功能基礎的 NFR 是「懸空需求」，無從測試也無法排優先序。

---

## SDD 視角：Use Case + NFR 給 LLM Agent 看的差異

> 如果你對 AI coding agent 在規格驅動開發中的定位還不熟，先回看 [Ch 1 為什麼「規格」突然重要了](./01-why-specs-matter-now.md)。

傳統上，Use Case 的 Extensions 是寫給人看的——人讀得懂「若付款失敗，回到步驟 4」。LLM agent 處理這段文字時，如果用的是自然語言敘述而不是帶有明確鍵值的結構，agent 很容易漏實作某個 extension branch。

這也是 AWS Kiro 的 requirements.md 採用 EARS 句型（而非 Use Case prose）的原因：structured keywords 比 free-form story 更能讓 agent 逐條對照。

Use Case 在 SDD workflow 中的最佳位置，是**在 Agent 生成 requirements.md 之前**，用來對齊人類團隊對「這個功能的邊界在哪裡」的共識。Use Case 是人與人協作的產物；EARS / Given-When-Then 是人與 Agent 的介面。

---

## 底層機制：為什麼 Extensions 這麼重要

需求工程的研究持續發現：**大多數 bug 不在 happy path，而在 Extensions 沒有描述到的邊界情況**。

Happy path 是「系統在最理想情況下的行為」——每個 actor 做了正確的事、每個外部服務都回應正確。現實中，這條路徑發生的機率可能只有 60–80%，剩下的 20–40% 都是某種 extension。

Cockburn 的 fully-dressed 格式把 Extensions 寫在獨立段落，每條鍵入對應的 happy path 步驟編號（如 `8a`、`9a`），這種結構有兩個好處：

1. **窮舉壓力**：你得對每個步驟問「如果這步失敗了怎麼辦？」
2. **追溯性**：code review 時可以逐條比對 extension 是否有實作

沒有 Extensions 的 Use Case，等於只寫了演出腳本的一半。

---

## 對比取捨

| 記法 | 學習曲線 | 完整度 | 可執行 | 適合文件化 | 適合 AI Agent |
|---|---|---|---|---|---|
| User Story | 低 | 低 | 否 | 否 | 差（太模糊） |
| Use Case（Casual） | 中 | 中 | 否 | 是 | 差（自由文字） |
| Use Case（Fully-Dressed） | 高 | 高 | 否 | 是 | 中（結構化但非機器語法） |
| BDD Given-When-Then | 中 | 中 | 是 | 是 | 好 |
| EARS 句型 | 中 | 高 | 否 | 是 | 好 |
| NFR（可量測陳述） | 中 | 高 | 間接可（CI gate） | 是 | 中 |

---

## 踩雷集錦

**錯誤直覺 1：「Sea-level Use Case 就是一個 User Story 的放大版」**

正確認識：User Story 和 Use Case 設計目標不同。User Story 是「對話起點」，刻意留下可協商空間，不描述 extensions；Use Case 是「已協商好的完整互動記錄」，Extensions 是核心產物，不是補充。把 Use Case 寫成只有 happy path 而沒有 Extensions，等於用了重型工具卻放棄了它的主要優勢。

**錯誤直覺 2：「NFR 是系統整體的屬性，不用對應到具體功能」**

正確認識：「整個系統的回應時間必須小於 2 秒」這種 NFR 是無法測試的——不同功能的負載模型和架構路徑完全不同。NFR 必須有上下文：哪個操作、在什麼負載下、量測什麼指標。ISO/IEC 25010 給你分類框架，但不幫你填數字——數字必須來自業務需求與工程現實的協商。

**錯誤直覺 3：「Interaction Capability（互動能力）= 好看的 UI」**

正確認識：ISO/IEC 25010:2023 的 Interaction Capability 包含 Appropriateness recognizability、Learnability、Operability、User error protection、User engagement、Inclusivity、Self-descriptiveness、Accessibility。「好看」是 user engagement 的一部分，而且還得量測。Inclusivity 是 2023 年新增的子特性，關注的是不同能力使用者的可及性，不是美觀。

**錯誤直覺 4：「Extensions 用文字描述就夠了，不需要有鍵值結構」**

正確認識：沒有步驟鍵（如 `8a`）的 Extensions 很快就會發生「哪條 Extension 對應哪個步驟」的歧義，特別是在 Use Case 有 10 步以上時。Cockburn 的 `<步驟號>.<Extension序號>` 格式不是形式主義，是追溯鏈的保障。

**錯誤直覺 5：「Safety（功能安全）就是 Security（資訊安全）」**

正確認識：ISO/IEC 25010:2023 明確把這兩個分開。Safety 的子特性包含 Operational constraint（防止超出安全邊界運作）、Hazard warning、Fail safe（失效時進入安全狀態）——這是電梯、醫療設備、車輛控制系統的語彙。Security 關注的是資料機密性、存取控制、對攻擊的抵抗。混用這兩個詞在 embedded systems 或航空域的合規審查中會造成嚴重誤解。

---

## 進階延伸

**Use Case 2.0（Jacobson, 2011）**：Jacobson 本人後來提出了 Use Case 2.0，把 Use Case 切成 Use Case Slice，讓它更容易融入敏捷迭代。核心洞察是：一個 Use Case 的 Extensions 可以分批交付，每個 slice 是 happy path 的一段加上對應的幾個 extension。如果你的團隊用 fully-dressed Use Case 覺得太重，Use Case 2.0 是值得研究的折衷。

**品質屬性 Workshop（QAW）**：Carnegie Mellon SEI 的 Quality Attribute Workshop 是一套擷取 NFR 的工作坊方法，特別適合大型架構決策前的 NFR 對齊。它的輸出格式和 ISO 25010 的分類框架可以直接對應。

**ATAM（Architecture Tradeoff Analysis Method）**：一旦有了量測性 NFR，ATAM 提供系統性方法評估架構決策是否滿足這些 NFR，並找出它們之間的取捨（例如：提高可靠性 vs 降低效能）。

---

## 動手練習

選一個你最近實際在用的 App（電商、社群、工具都可），完成以下三件事：

**練習 A：寫一份 Fully-Dressed Use Case**

挑一個你認為「失敗情況很多」的功能（例：修改已下訂的訂單），寫出：

- 所有必填欄位（Actor、Preconditions、Success/Failed 後置條件、Trigger）
- 完整 Main Success Scenario（至少 6 步）
- 至少 3 條 Extensions，每條標出對應步驟編號

**練習 B：把三個 NFR 寫得可量測**

針對上面那個功能，分別從 Performance Efficiency、Security、Reliability 三個維度各寫一條 NFR。每條必須包含：指標、前提條件、目標值、量測方式。

**練習 C：NFR 缺陷診斷**

評估以下三條 NFR 各有什麼問題，並改寫：

```
（壞）NFR-1: 系統要很快。
（壞）NFR-2: 使用者介面應該直觀且用戶友善。
（壞）NFR-3: 系統必須安全，保護使用者資料。
```

---

## 本章重點整理

- Use Case 由 Ivar Jacobson（1986）發明，Alistair Cockburn（2000）的 fully-dressed 模板是業界最廣泛採用的格式。
- Fully-dressed 模板的核心是 Main Success Scenario（happy path）加上 Extensions（替代與失敗路徑），每條 extension 以步驟鍵標記對應位置。
- 目標層次分三級：風箏（summary）、海平面（user goal）、魚（subfunction）。主力寫 sea-level。
- User Story 和 Use Case 不是互相替代的競爭關係——User Story 是協商起點，Use Case 是已協商結果的完整記錄。
- ISO/IEC 25010:2023 定義九個品質特性：Functional Suitability、Performance Efficiency、Compatibility、Interaction Capability、Reliability、Security、Maintainability、Flexibility、Safety。Safety 是 2023 年新增；Usability 改名為 Interaction Capability；Portability 改名為 Flexibility。
- 可量測的 NFR 必須包含指標、前提條件（負載/情境）、目標值、量測方式。「系統應該要快」不是需求。
- 在 SDD 工作流中，Use Case 是人與人協作的共識工具；EARS / Given-When-Then 才是給 Agent 消費的格式。

---

## 自我檢核

- [ ] 不翻書，用自己的話說出 Cockburn fully-dressed 模板裡哪些欄位是核心的（可以少，不能錯）
- [ ] 解釋 Extensions 的步驟鍵（如 `8a`）在格式上的作用，以及沒有它會出現什麼問題
- [ ] 說出「風箏 / 海平面 / 魚」三個目標層次各對應什麼類型的 Use Case
- [ ] ISO/IEC 25010:2023 新增了哪個品質特性？哪兩個舊特性被重命名了？
- [ ] 拿 NFR-1「系統要很快」問自己：缺少了哪四個要素，才讓它變成可量測的陳述？
- [ ] 如果面試官問「User Story 和 Use Case 差在哪裡」，你會怎麼在 90 秒內說清楚？

---

## 延伸閱讀

- **Alistair Cockburn — Use Case Template**
  URL: https://www.cs.otago.ac.nz/coursework/cosc461/uctempla.htm
  讀這裡學什麼：所有欄位的定義、海平面目標層次的說明、「Buy Goods」完整範例。從頭讀到尾，只有一頁，是 *Writing Effective Use Cases*（2000）精華的自由版本。

- **ISO/IEC 25010:2023 官方標準頁面**
  URL: https://www.iso.org/standard/78176.html
  讀這裡學什麼：九個品質特性及子特性的官方定義，2023 年版本相對 2011 年版的變動。免費摘要可看 https://iso25000.com/index.php/en/iso-25000-standards/iso-25010 ，後者以可視化形式整理子特性樹狀結構，適合快速定位。

- **Alistair Mavin — EARS 官方網站**
  URL: https://alistairmavin.com/ears/
  讀這裡學什麼：如何把 Use Case Extensions 和 NFR 改用 EARS 句型陳述。延伸本章「把 NFR 送給 Agent」的脈絡——EARS 的 `While` / `When` / `If` 前綴讓機器更容易解析條件分支，恰好對應 Extensions 的觸發條件。（本課程 [Ch 11](./11-ears-notation.md) 已深入介紹 EARS，這裡作為對照補充）

- **Thoughtworks — Spec-Driven Development: Unpacking 2025's Key New AI-Assisted Engineering Practices**（Liu Shangqi，2025-12-04）
  URL: https://www.thoughtworks.com/en-us/insights/blog/agile-engineering-practices/spec-driven-development-unpacking-2025-new-engineering-practices
  讀這裡學什麼：為什麼 2025 年的 AI coding agent（Kiro、Spec Kit）需要比 Use Case prose 更結構化的需求格式；structured input 如何降低 LLM 幻覺率。這是從本章 classical RE 過渡到 Part 3 AI 工具的橋梁文章。

- **Alistair Cockburn — *Writing Effective Use Cases*（Addison-Wesley，2000）**
  讀這裡學什麼：fully-dressed 格式的完整理論基礎、goal-level 的完整論述、寫作常見錯誤與修正（書的第 4–6 章）。這本書在敏捷崛起後被很多人忽略，但其中關於 Extensions 設計的論述至今沒有被取代。

- **Ivar Jacobson, Ian Spence, Kurt Bittner — *Use Case 2.0*（Ivar Jacobson International，2011）**
  讀這裡學什麼：如何把 Use Case 切成 Slice 讓它融入敏捷迭代，以及 Use Case Slice 和 User Story 的對應關係。免費 PDF 可從 Jacobson International 官網取得；適合對 fully-dressed 太重、User Story 太輕的團隊研究折衷點。

---

下一章我們來到需求記法光譜的最嚴格端：用數學語言描述系統行為，讓電腦幫你找出設計階段就存在的 bug——AWS 用 TLA+ 找到了 DynamoDB 的缺陷，這種故事在人工智慧工具普及前已經發生了數十年。

→ [Ch 13 嚴謹的另一端：形式化規格 TLA+ / Alloy](./13-formal-specs-tla-alloy.md)
