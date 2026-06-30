# Ch 35 — Bounded Context = Agent Scope

> **目標**：學會用界限上下文（Bounded Context）切出 subagent 的任務邊界，設計 monorepo 的目錄結構，讓每個 agent 在一個語義清晰的范疇裡工作，避免因為跨越太多 context 而失焦、互相踩踏。

---

## 直覺：一個地圖的邊框

想像你手上有一張舊城區地圖，另一張是郊區地圖。地圖各自的圖例可以重疊——舊城把「廣場」標成紅色，郊區把「公園」也標成紅色。只要你不把兩張地圖強行拼在一起，就沒有衝突。但如果有人把它們縫成一張大圖，「紅色」這個符號就必須對全圖只有一個意義，衝突就出現了。

界限上下文解決的就是這個問題。在它確立的邊界之內，「Order（訂單）」是一個精確的詞；越過邊界，隔壁的 context 裡「Order」可能是採購單或工作指示。只要兩邊不強行合併，就不需要妥協。

現在把「地圖」換成「AI agent」：一個 agent 如果跨越了太多 bounded context，它就要在同一個對話裡同時理解訂單管理的 Order 和倉儲管理的 Order，兩套術語、兩套規則、兩套 invariant。注意力視窗（context window）不只是受到 token 數限制，更受到語義密度的限制——術語衝突會讓模型產生幻覺，就像兩張地圖強行拼接後圖例就失去意義。

> 如果你對 Bounded Context 的 DDD 原始定義還不熟，先回看 [Ch 16 Bounded Context：模型在哪裡為真](./16-bounded-context.md)。

---

## 歷史脈絡：為什麼這個問題在 AI 時代更尖銳

在人工開發時代，「context 污染」的主要症狀是：一個函式同時操作訂單資料庫和庫存資料庫，然後因為 `Order` 這個詞在兩個地方有不同欄位，bug 就藏在那裡。解決方法是在設計層面劃清 module 邊界，靠 code review 和架構守則維持。

Eric Evans 在 2003 年的 *Domain-Driven Design*（藍皮書）裡提出 Bounded Context，正是要給「一張大地圖 vs 多張小地圖」這個選擇提供原則。他的結論是：**total unification of the domain model for a large system will not be feasible**。大系統裡嘗試維護一個通用模型，代價遠高於允許多個局部模型各自保持一致。

Martin Fowler 在 2014 年的 BoundedContext bliki 裡用「meter（公尺 vs 電表）」這個多義詞舉例：人際對話裡可以靠上下文化解歧義，但「在電腦的精確世界裡無法平滑帶過（could be smoothed over in conversation but not in the precise world of computers）」。

這句話對 LLM 的時代有了新的衝擊。LLM 是歧義放大器（ambiguity amplifier）。Daniel Schleicher 在 2026 年 1 月的分析裡說得直白：「當我們給 AI agent 一個像 Order 這樣能代表十幾件事的模糊指令，它會把混亂放大（amplify the chaos）。」人工程式設計師能靠歷史記憶和問同事來化解歧義；agent 卻活在一個平坦的上下文視窗裡，靠什麼化解？靠你餵進去的資訊品質。

所以 Bounded Context 在 AI 時代不是比較重要，是**結構性依賴**：沒有它，agent 就沒有辦法知道自己在哪張地圖上工作。

---

## 核心對應：Bounded Context → Agent Scope

以下的對應關係，左欄是 DDD 概念，右欄是 agent 場景下的實踐意義：

```
DDD 概念                     Agent 場景對應
─────────────────────────────────────────────────────
Bounded Context              一個 subagent 的職責範疇
Ubiquitous Language          agent 的 context（glossary、AGENTS.md）
Context Map                  多 agent 協作的介面契約（API、事件 schema）
Anti-Corruption Layer (ACL)  翻譯層，避免一個 context 的詞彙滲入另一個
Aggregate boundary           agent 一次可以合法修改的最大範圍
```

這不是比喻，是功能上的同構。讓我們用具體例子展開。

---

## 具體場景：電商 monorepo 的 agent 分工

假設你有一個電商平台，領域初步切了三個 bounded context：

```
monorepo/
├── contexts/
│   ├── ordering/          ← 訂單 context
│   │   ├── AGENTS.md      ← agent 指令：這裡的 Order 是顧客訂單
│   │   ├── src/
│   │   └── specs/
│   ├── inventory/         ← 庫存 context
│   │   ├── AGENTS.md      ← agent 指令：這裡的 Item 是可售庫存單位
│   │   ├── src/
│   │   └── specs/
│   └── billing/           ← 帳務 context
│       ├── AGENTS.md      ← agent 指令：這裡的 Invoice 是應收帳款憑證
│       ├── src/
│       └── specs/
├── AGENTS.md              ← 全域指令：說明整體架構、共用規則
└── shared-kernel/         ← 真正跨 context 共用的少量模型（CustomerID、Money）
```

每個 `AGENTS.md` 的角色相當於這個 context 的通用語言宣告（Ubiquitous Language declaration）。`ordering/AGENTS.md` 裡會寫：

```markdown
# Ordering Context — Agent Instructions

## Scope
You work ONLY inside `contexts/ordering/`. Do not read or modify files
in `contexts/inventory/` or `contexts/billing/`.

## Ubiquitous Language
- Order: a customer's purchase intent, consisting of OrderLines and a
  ShippingAddress. An Order is NOT a purchase order to a supplier.
- OrderLine: one product variant + quantity within an Order.
- Confirm: transition an Order from `pending` to `confirmed`; triggers
  an OrderConfirmed domain event consumed by inventory.
- Cancel: transition from `pending|confirmed` to `cancelled`; does NOT
  modify inventory directly.

## Invariants
- An Order must have at least one OrderLine before it can be confirmed.
- TotalAmount = sum(OrderLine.unitPrice × quantity). Never derive it
  from the billing context.

## Integration points (READ-ONLY references)
- Publishes: OrderConfirmed event (schema: shared-kernel/events/order-confirmed.json)
- Consumes: InventoryReserved event (schema: shared-kernel/events/inventory-reserved.json)
```

這份 `AGENTS.md` 做的事，和 DDD 裡的 context boundary 做的事完全一樣：宣告詞彙、宣告 invariant、宣告整合點。差別只在於對象從「人類工程師」變成「AI agent」。

> 如果你對通用語言的構成還不熟，先回看 [Ch 15 通用語言 Ubiquitous Language](./15-ubiquitous-language.md)。
> 如果你對 context map 和整合模式還不熟，先回看 [Ch 17 Context Mapping 與整合模式](./17-context-mapping.md)。

---

## 可跑範例：用 dependency-cruiser 強制邊界

光靠 `AGENTS.md` 裡的文字宣告，agent 可能還是會出錯（例如要求你引入隔壁 context 的程式碼）。Nick Tune（PayFit Sr Staff Engineer，2026 年 3 月受訪）的做法是**確定性執行（deterministic enforcement）**：用工具在 build time 就把違規阻斷，不依賴模型的判斷。

```bash
# 安裝 dependency-cruiser（Node.js 專案）
npm install --save-dev dependency-cruiser

# 初始化設定
npx depcruise --init
```

在 `.dependency-cruiser.cjs` 加入跨 context 的禁止規則：

```javascript
module.exports = {
  forbidden: [
    {
      name: "no-cross-context-import",
      comment:
        "Bounded contexts must not import directly from each other. " +
        "Communicate via shared-kernel or published events only.",
      severity: "error",
      from: { path: "^contexts/([^/]+)/" },
      to: {
        path: "^contexts/([^/]+)/",
        // 同一個 context 內的 import 允許；跨 context 禁止
        // $1 in 'from' must equal $1 in 'to' to be allowed
        pathNot: "^contexts/\\1/",
      },
    },
    {
      name: "no-direct-shared-kernel-mutation",
      comment: "shared-kernel is read-only for all contexts.",
      severity: "error",
      from: { path: "^contexts/" },
      to: { path: "^shared-kernel/", dependencyTypes: ["local"] },
      // 僅禁止 write/mutation，read 允許
      // 這個規則示意：實際上需要結合 ESLint no-restricted-imports
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    outputType: "err-long",
  },
};
```

執行驗證：

```bash
npx depcruise contexts/ --config .dependency-cruiser.cjs
```

**預期輸出（違規時）：**

```
error no-cross-context-import
  contexts/ordering/src/services/OrderService.ts
    → contexts/inventory/src/models/StockItem.ts
  Rule: Bounded contexts must not import directly from each other.
```

**預期輸出（乾淨時）：**

```
✔ no dependency violations found (148 modules, 312 dependencies checked)
```

把這條指令加進 CI pipeline：

```yaml
# .github/workflows/arch-check.yml
jobs:
  boundary-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
      - run: npx depcruise contexts/ --config .dependency-cruiser.cjs
```

現在，不管 agent 建議什麼程式碼，只要它跨越 context 邊界，CI 就會擋下來。這是架構邊界從「文件建議」升級到「機器驗證」的關鍵一步。

---

## 底層機制：為什麼邊界可以對應到 Aggregate boundary

Bardia Khosravi 在 2025 年 7 月的實作筆記裡點出一個幾乎被忽略的精確規則：**Aggregate 邊界應該對應到交易邊界（transaction boundary）**。一次資料庫交易裡你能合法修改的最大範圍，就是一個 Aggregate。

把這個規則平移到 agent 行為：

**一個 agent 在一次任務裡能合法修改的最大範圍 = 一個 Bounded Context 內的 Aggregate**。

為什麼？因為跨 context 的一致性，DDD 靠的是 Domain Event（非同步，最終一致）而不是分散式交易（2PC）。如果一個 agent 被允許在一次任務裡同時修改 `ordering/` 和 `inventory/` 的資料，它隱含地需要做跨 context 的協調，這是架構設計明確拒絕的。

Annegret Junker（codecentric，2026 年 3 月）的 recipe 平台案例印證了這一點：在協作建模工作坊之後，她把系統拆成三個鬆耦合的 OpenAPI spec，設計原則是「share IDs, not schemas」。三個 agent 各自擁有一份 spec，介面透過 ID 引用而非直接共享物件。這是 Anti-Corruption Layer 的最輕量實現：每個 context 有自己的 schema，跨 context 的翻譯只發生在整合層。

> 如果你對 Aggregate 設計還不熟，先回看 [Ch 19 戰術建模：Entity / Value Object / Aggregate](./19-entities-value-objects-aggregates.md)。

---

## 通用語言作為 Agent 的詞彙表：連接 Ch 34

> 如果你剛從 [Ch 34 通用語言作為 LLM 的詞彙表](./34-ubiquitous-language-as-glossary.md) 過來，這裡是實作那篇理論的具體做法。

AGENTS.md 裡的 Ubiquitous Language 段落，就是 Ch 34 裡說的「glossary」的落地形式。但它不是孤立的詞典，它有結構：

1. **詞彙宣告**：每個術語一句定義，說清楚這個 context 裡這個詞意味著什麼。
2. **否定宣告**：說清楚這個詞在這裡**不是**什麼（`Order is NOT a purchase order to a supplier`）。
3. **Invariant**：業務規則用無歧義的語言寫出來（`TotalAmount = sum(...)，不從 billing context 衍生`）。
4. **整合點**：這個 context 發布什麼事件、消費什麼事件，指向 shared-kernel 裡的 schema 檔案。

這個結構讓 agent 的詞彙和架構邊界是同一份文件，不可能出現「文件說一套、邊界劃另一套」的漂移。

---

## 對比取捨

| 策略 | 優點 | 缺點 | 適合情境 |
|------|------|------|----------|
| 單一 agent 跑整個 monorepo | 設定最簡單 | 上下文污染嚴重；術語衝突；agent 容易失焦或幻覺 | 極小型專案（<3 個 context） |
| 按 bounded context 切 subagent，共用 knowledge base | 各 agent 詞彙清晰；邊界可機器驗證 | 需要維護多份 AGENTS.md；跨 context 協作需要協調 agent | 中型系統（3–8 個 context） |
| 按 bounded context 切 subagent，完全隔離 | 最高清晰度；最容易 debug | orchestrator 協調複雜；整合測試難寫 | 大型系統或高合規需求 |
| 按技術層切 agent（backend/frontend/infra） | 對現有 fullstack 團隊直覺 | 技術層通常不對應語義邊界；context 污染依然存在 | 不推薦；這是反模式 |

「按技術層切」是最常見的陷阱，值得特別說：把 agent 切成「負責 API」和「負責 UI」，解決的是**技術分工**，不是**語義分工**。Order 在 API 層和 UI 層的含義一樣，所以語義污染沒有消失，只是被技術邊界掩蓋了。Bounded context 切的是語義邊界，不是技術邊界。

---

## 「分工沿著 DDD 線劃 subagent」的現況誠實評估

這裡需要認識論誠實（查證日期 2026-06-30）：

根據 area brief 的評估，「把多個 subagent 嚴格沿 DDD bounded context 劃分，能優於其他方案」的說法，目前**主要是實踐者的直覺和理論推論**，缺乏嚴格的對照實驗。有支撐的是：
- 用 bounded context 範疇**單一 agent 的任務**（Nick Tune 的做法）——有具體的生產案例。
- 用 AGENTS.md 把 context 的詞彙鎖進 monorepo 目錄——是有紀錄的實踐模式。

「分多個 subagent 各守一個 context、subagent 之間透過事件協調」這個更進一步的架構，目前主要是概念性的（Russ Miles、Bhuvaneswari Subramani 等人的論述），實際生產案例和量化數據相當稀少。

所以本章的主張：**單一 agent 用 bounded context 來定義自己的工作範疇**——這部分有充分的實踐支撐。**多 subagent 按 context 分工的 orchestration 架構**——這部分仍在發展中，值得探索，但不要把它當作已被驗證的最佳實踐。

---

## 踩雷集錦

### 雷 1：用技術層當 agent 邊界

**錯誤直覺**：「我有一個 backend agent 和一個 frontend agent，這樣就分開了，不會互相干擾。」

**正確認識**：技術層邊界不是語義邊界。Backend 和 frontend 都需要理解同一個 `Order` 的概念，語義污染依然存在。一旦 `Order` 在 frontend 的含義（顯示狀態）和 backend 的含義（業務狀態機）出現分歧，你在兩個「清晰分開」的 agent 之間製造了更難察覺的 context mismatch。Bounded context 要切的是業務含義的邊界，不是部署的邊界。

### 雷 2：AGENTS.md 只寫「範疇」，不寫「不是什麼」

**錯誤直覺**：「我告訴 agent 這個 context 是什麼就夠了，它會自己推斷不該做什麼。」

**正確認識**：**否定宣告和 invariant 比正面定義更重要**。Fowler 舉的 `meter` 例子說明得很清楚：歧義不是因為你沒說清楚「電表」是什麼，而是因為你沒說清楚「它不是公尺」。在 AGENTS.md 裡明確寫 `Order is NOT a purchase order`、`TotalAmount must NOT be derived from billing context`，比花三段描述「Order 是什麼」更有效。LLM 的注意力對否定式的 hard constraint 響應比開放式正面描述好（在大多數情境下通常如此，版本相依）。

### 雷 3：Context 邊界只存在於文件，不存在於工具

**錯誤直覺**：「我在 AGENTS.md 裡寫了邊界，agent 應該會遵守。」

**正確認識**：Agent 不會永遠遵守文字宣告的邊界，尤其在長任務或多步驟推理時。Nick Tune 的做法是把邊界**烙進工具**：dependency-cruiser 規則在 CI 擋住跨 context import，ESLint `no-restricted-imports` 在 IDE 即時報警。規則違反是確定性的（deterministic），不依賴模型的判斷。文字宣告是「第一道防線」，工具驗證是「底線」。兩者都要，缺一不可。

### 雷 4：把 shared-kernel 當成「統一大模型」

**錯誤直覺**：「共用的東西越多越好，放到 shared-kernel 就能讓所有 agent 保持一致。」

**正確認識**：shared-kernel 應該**極度克制**。Junker 的原則是「share IDs, not schemas」——不同 context 共用的只是穩定的識別符（CustomerID、Money 值物件），不是業務物件的完整定義。把太多東西放進 shared-kernel，你把「多張局部地圖」重新拼回「一張大地圖」，Bounded Context 的作用就被抵銷了。

### 雷 5：忽略最終一致性在 agent 協作裡的影響

**錯誤直覺**：「我的 ordering agent 和 inventory agent 都確認完成了，所以整個操作成功了。」

**正確認識**：跨 bounded context 的操作，在 DDD 裡靠 Domain Event 實現最終一致性（eventual consistency），不是強一致性（strong consistency）。在 agent 協作裡同樣如此：ordering agent 發布 `OrderConfirmed` 事件，inventory agent 消費該事件並預留庫存——中間有延遲視窗。如果你的 orchestrator 假設所有 subagent 同步完成，就會在這個視窗裡讀到不一致的狀態。設計 agent 協作流程時，必須明確處理「事件尚未消費」的中間狀態。

> 如果你對 Domain Event 的發布/消費模式還不熟，先回看 [Ch 20 Repository / Domain Service / Factory / Domain Event](./20-repositories-services-events.md)。

---

## 進階延伸：把 Bounded Context 織進 Spec Kit 和 Kiro 的工作流

> 以下工具資訊以查證日期 2026-06-30 為準，版本相依，請以官方最新文件為準。

**GitHub Spec Kit（~116k stars，查證日期 2026-06-30）** 的 `/speckit.constitution` 檔案是自然的 bounded context 宣告位置。你可以在 constitution 裡明確說：「本次 spec 的範疇是 `ordering` context。以下是這個 context 的通用語言和 invariant……」然後在 `/speckit.specify` 和 `/speckit.plan` 的產出裡，要求 agent 持續引用 constitution 裡的術語，不引入其他 context 的詞彙。

**AWS Kiro** 的三份核心文件（requirements.md / design.md / tasks.md）同樣可以在開頭明確標注這份 spec 所屬的 bounded context 和通用語言。Kiro 的 EARS 格式需求句天然適合搭配 invariant 宣告：EARS 的 `WHILE [condition] the [system] shall [requirement]` 句型可以直接把 aggregate invariant 轉成可驗證的需求。

> 如果你對 Spec Kit 工作流還不熟，先回看 [Ch 28 GitHub Spec Kit（二）：/speckit.* 工作流端到端](./28-spec-kit-workflow.md)。
> 如果你對 Kiro 的三份規格還不熟，先回看 [Ch 30 AWS Kiro：三檔規格、EARS、steering、hooks](./30-kiro.md)。

---

## 進階延伸：Nick Tune 的 DDD-as-agent-state-machine

Nick Tune 在 2026 年 3 月的訪談裡提出一個把 DDD 概念應用到 agent **本身**的做法：把 AI 工作流建模成一個**狀態機（state machine），像 Aggregate 一樣有 invariant**。

```
狀態機：OrderAgentWorkflow
┌──────────┐  SpecReady   ┌──────────┐  PlanApproved  ┌────────────┐
│  IDLE    │ ──────────▶  │ PLANNING │ ─────────────▶  │ EXECUTING  │
└──────────┘              └──────────┘                  └────────────┘
                                │                             │
                                │ SpecIncomplete              │ ValidationFailed
                                ▼                             ▼
                          ┌──────────┐              ┌────────────────┐
                          │  CLARIFY │              │  ROLLBACK_PLAN │
                          └──────────┘              └────────────────┘
```

每個狀態轉移有 guard 條件（invariant），等同 Aggregate 的業務規則。例如：`PLANNING → EXECUTING` 的 guard 是「spec 裡所有 [NEEDS CLARIFICATION] 標記都已解決」。這讓 agent 行為從「prompt → response」的黑盒，變成有明確 invariant 的狀態機，更容易測試和 audit。

---

## 動手練習

選一個你正在做或計劃做的小專案，或用下方提供的電商範例：

**練習 1：識別 bounded context（20 分鐘）**

對一個電商系統畫 context map：
- 先列出所有你能想到的業務概念（Order、Customer、Product、Cart、Invoice、Shipment、Review……）
- 找出哪些概念在不同地方有不同含義（例如：`Customer` 在促銷 context 是「行銷目標」，在 billing context 是「付款方」）
- 把語義上一致的概念群圈起來，每個圓就是一個 bounded context 候選

**練習 2：寫一份 AGENTS.md（30 分鐘）**

選練習 1 裡的一個 bounded context，寫一份 `AGENTS.md`：
- Scope：這個 context 的目錄範疇
- Ubiquitous Language：至少 5 個術語，每個都有「是什麼」和「不是什麼」
- Invariants：至少 3 條業務規則，用「系統必須確保……」的句型
- Integration points：這個 context 發布哪些事件、消費哪些事件

**練習 3：設定邊界守護（45 分鐘，需要 Node.js 環境）**

在你的 monorepo 裡安裝 dependency-cruiser，加入一條「禁止跨 context 直接 import」的規則，跑一次驗證，修改一個違規的 import，讓驗證通過。

**邊界例子：故意踩雷**

在 `contexts/ordering/src/` 裡加一行：

```typescript
// 故意違規的 import
import { StockItem } from "../../inventory/src/models/StockItem";
```

跑 `npx depcruise contexts/ --config .dependency-cruiser.cjs`，確認你看到錯誤。然後把這個 import 改成消費 shared-kernel 的事件 schema（read-only），讓驗證再次通過。

---

## 本章重點整理

- Bounded Context 的核心洞察：**total unification of the domain model for a large system will not be feasible**。這在 AI agent 時代更急迫，因為 LLM 是歧義放大器。
- Agent 的工作範疇應該和 Bounded Context 對齊：一個 agent 在一次任務裡只在一個 context 的語義世界裡工作。
- AGENTS.md 是通用語言的落地形式，它宣告詞彙、否定宣告、invariant、整合點——這四個要素缺一不可。
- 邊界要**工具化**，不只是文件化。dependency-cruiser + CI 是把 context 邊界從「文字規範」變成「機器驗證」的關鍵。
- 跨 bounded context 的協作靠 Domain Event + 最終一致性，不靠共享 schema 或分散式交易。
- 「多 subagent 按 context 分工」仍是主要實踐者直覺而非嚴格驗證，值得探索但需誠實標注不確定性（查證日期 2026-06-30）。

---

## 自我檢核

- [ ] 我能用自己的話解釋「為什麼 LLM 讓 Bounded Context 的需要更急迫」，而不是翻書找定義。
- [ ] 面試被問「你怎麼設計 monorepo 裡 AI agent 的分工」，我能提出 bounded context 的切法，並說出至少一個工具驗證手段。
- [ ] 我能說出 AGENTS.md 的四個要素，並解釋「否定宣告」為什麼和「正面定義」一樣重要。
- [ ] 我能解釋 shared-kernel 應該保持「極度克制」的原因，以及「share IDs, not schemas」這個原則的意思。
- [ ] 我知道「按技術層切 agent」和「按 bounded context 切 agent」的根本差異，能舉例說明前者的缺陷。
- [ ] 我能描述 dependency-cruiser 怎麼把 context 邊界從文件規範變成機器驗證。

---

## 延伸閱讀

**Bounded Context（bliki）** — Martin Fowler  
https://martinfowler.com/bliki/BoundedContext.html  
最短的時間了解 Bounded Context 的根本動機（polyseme 問題、「電腦的精確世界」論點），以及為何這個 1970 年代就有人注意到的問題，到今天仍是 AI agent 的頭號殺手。讀 polysemy 那節即可，全文不長。

**Agentic Code Workflows with Nick Tune** — Nick Tune（Senior Staff Engineer, PayFit）via Techworld with Milan  
https://newsletter.techworld-with-milan.com/p/agentic-code-workflows-with-nick  
目前最接地氣的「把 DDD 邊界用在 AI agent」實踐報告。重點讀「state machine like an aggregate」和「dependency-cruiser deterministic enforcement」兩節。Nick Tune 是 *Architecture Modernization*（2024）和 Bounded Context Canvas 的作者，這篇是他把 DDD 知識體系移植到 agentic workflow 的第一手記錄（查證日期 2026-06-30）。

**From Stories to Code: How Domain Storytelling and EventStorming Give LLMs the Context They Need** — Annegret Junker（codecentric）  
https://www.codecentric.de/en/knowledge-hub/blog/from-stories-to-code-how-domain-storytelling-and-eventstorming-give-llms-the-context-they-need  
本章最重要的實證支撐：recipe 平台三輪原型，協作建模後 OpenAPI schema 從 3 個增加到 9 個，以及「share IDs, not schemas」原則的實際來源。讀 v1→v2→v3 的 schema 對比那節。（查證日期 2026-06-30）

**Spec-Driven Development is Domain-Driven Design's Impatient Cousin** — Daniel Westheide（INNOQ Senior Consultant）  
https://www.innoq.com/en/blog/2026/03/sdd-ddd-why-bmad-wont-save-you/  
SDD 和 DDD 共用同一個根本問題（領域專家的可及性）、共享同一個失敗模式（組織把領域專家隔絕在牆後）的最清晰論述。「DDD's impatient cousin」這個說法點出兩者的關係——讀完本章後再讀這篇，會有不同的共鳴層次。

**Domain Driven Agent Design** — Russ Miles  
https://engineeringagents.substack.com/p/domain-driven-agent-design  
把 DDD 概念（bounded context、ubiquitous language）應用到 agent **自身設計**的概念性文章，搭配 Rod Johnson 的 DICE（Domain-Integrated Context Engineering）框架。注意：屬於概念性論述，implementation 細節較少。讀「bounded contexts keep agents from bleeding across business units」那節。（查證日期 2026-06-30）

**Spec-Driven Development | Technology Radar Vol 34** — Thoughtworks  
https://www.thoughtworks.com/en-us/radar/techniques/spec-driven-development  
這個領域的「誠實錨點」。Thoughtworks 把 SDD 放在 Assess 環（不是 Adopt），明確說工作流「elaborate and opinionated」、輸出「hard to review」。讀完本章對 bounded context 切 agent 充滿信心之後，讀這篇是必要的反向校準（Nov 2025，查證日期 2026-06-30）。

---

下一章我們把 Bounded Context 裡的領域模型，從概念圖轉成 spec 的骨架結構——讓 agent 不只知道自己的邊界，還知道邊界裡的世界長什麼形狀。

→ [Ch 36 領域模型作為 spec 的骨架](./36-domain-model-as-spec-backbone.md)
