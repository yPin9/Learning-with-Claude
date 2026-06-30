# Ch 30 — AWS Kiro：三檔規格、EARS、steering、hooks

> **目標**：理解 Kiro 如何把「先寫規格、後跑實作」具體落地成三個 Markdown 檔案（requirements.md / design.md / tasks.md）、兩種對話模式（Spec session vs Vibe session）、持久 context 機制（.kiro/steering/）與事件驅動自動化（.kiro/hooks/），並且能夠判斷什麼情況用 Spec session、什麼情況用 Vibe、何時需要 steering、何時用 hooks。
>
> **環境**：Kiro 是一個獨立桌面 IDE（非插件），從 Code OSS（VS Code 的開源核心）分叉。Public preview 2025-07-14，GA（正式上線）2025-11-17。定價是信用點（credit）制，版本相依，以下數字與模型名均標注「查證日期 2026-06-30」。

---

## 在 Kiro 出現之前，人們怎麼做

2024 年以前，主流 AI 輔助開發的工作流是：開一個 chat 視窗，把需求用自然語言說一遍，AI 直接輸出程式碼，你貼進去，如果不對再說一遍。這個流程有個結構性缺陷——AI 生成的每一步都不留下可審閱的中間產物。需求有沒有被正確理解？架構決策是哪幾個、為什麼這樣選？沒有紀錄。下一次 session 從零開始，之前建立的 context 消失了。

Amazon Q Developer（Kiro 的前身）走的也是插件路線，貼在 VS Code 或 JetBrains 旁邊，用聊天的方式協助寫程式。它的問題不是能力不夠，而是沒有給「規格」一個正式的位置：需求存在哪裡？設計存在哪裡？

Kiro 的答案是把規格當成一等公民，讓三個 Markdown 檔案在 .kiro/specs/ 裡安靜地坐著，讓 agent 實作之前必須先把這三個檔案填完並讓你審閱。這是 AWS 官方部落格的說法：「the first AI coding tool built around spec-driven development」（查證日期 2026-06-30）。

---

## 全貌：一張圖

```
你的想法
  │
  ▼
┌─────────────────────────────────────────────────┐
│  .kiro/specs/<feature>/                         │
│  ┌────────────────┐  ┌─────────────┐            │
│  │ requirements.md│  │  design.md  │            │
│  │  EARS 句型      │  │  架構/時序圖 │            │
│  │  user stories  │  │  介面定義    │            │
│  └───────┬────────┘  └──────┬──────┘            │
│          │                  │                   │
│          └──────────────────▼                   │
│                    tasks.md                     │
│              依賴圖 → Wave 1 → Wave 2 → ...      │
└─────────────────────────────────────────────────┘
               ▼  (你審閱、修改、確認)
         Kiro agent 實作
               ▼
      .kiro/steering/  ──── 持久 context（每次都注入）
      .kiro/hooks/     ──── 事件驅動自動化
```

規格在左邊，自動化基礎設施在右邊，兩者都住在 .kiro/ 目錄下，可以被 git 追蹤。

---

## 三檔規格的用途與分工

> 如果你對「規格 vs 設計 vs 實作」的概念層次還不清楚，先回看 [Ch 7 規格 vs 設計 vs 實作](./07-spec-design-implementation.md)。

### requirements.md——用 EARS 寫出「系統應該做什麼」

EARS（Easy Approach to Requirements Syntax，簡易需求句法）由 Alistair Mavin 等人在 Rolls-Royce 開發，2009 年發表於 IEEE RE09。核心直覺：自然語言需求最容易出現的毛病是**模糊詞**（「盡量快」）和**缺少觸發條件**（「系統顯示錯誤」——什麼時候？什麼錯誤？）。EARS 用限縮的句型模板強迫你填滿這兩個空白。

> 如果你對 EARS 的五種句型還不熟，先回看 [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md)。

Kiro 的 requirements.md 採用的主要模板是：

```
WHEN [條件/事件] THE SYSTEM SHALL [預期行為]
```

官方文件給的具體例子（查證日期 2026-06-30）：

```
WHEN a user submits a form with invalid data
THE SYSTEM SHALL display validation errors next to the relevant fields
```

這句話可以直接變成一個測試案例的前置條件與斷言。這是 EARS 的核心價值：寫完需求，測試幾乎自動長出來。

對於 bugfix spec，第一個檔案改名為 bugfix.md，句型改為：

```
WHEN [條件]
THEN the system SHALL CONTINUE TO [既有行為]
```

這是回歸保護的語意——我要確保修了 A 之後，B 仍然正確運作。

requirements.md 還包含 user stories（以「身為 X，我想要 Y，以便 Z」的格式），每個 user story 下面跟著 EARS 格式的驗收條件。

### design.md——技術架構的快照

requirements.md 說「做什麼」，design.md 說「怎麼做」。它的內容是：

- 架構圖（通常是 ASCII 或 Mermaid）
- 資料模型（entity / schema / 型別定義）
- 序列圖（Sequence Diagram）——把幾個元件之間的呼叫順序畫出來
- 介面定義（API endpoint、函式簽名、事件格式）
- 錯誤處理策略

design.md 是你和 agent 之間最重要的協商文件。你在這裡修改架構決策，agent 後面的實作就會跟著走。如果你在 requirements.md 已經定義清楚，Kiro 會自動提案 design.md；但你仍然要審閱，因為 agent 看不見你腦袋裡隱含的架構偏好。

### tasks.md——依賴圖驅動的實作計畫

tasks.md 把設計切成可追蹤的最小工作單元，每個 task 有：

- 唯一 ID（task 1、task 2 或更細的 task 1.1）
- 描述（一句話）
- 依賴清單（depends_on: [1, 2]）
- 狀態（pending / in-progress / done）

Kiro 分析依賴關係，把沒有相互依賴的 task 分進同一個「波（wave）」，波與波之間**循序**執行，同一個波內的 task **並行**執行。官方文件的描述（查證日期 2026-06-30）：「Waves execute sequentially; tasks within a wave execute concurrently.」

視覺化：

```
Wave 1: [建 DB schema] [建 API route 骨架]   ← 互不依賴，並行
         ↓                ↓
Wave 2: [實作 handler]                        ← 依賴 Wave 1 兩者
         ↓
Wave 3: [寫整合測試] [更新 OpenAPI doc]       ← 互不依賴，並行
```

這個機制讓「task 太多時 agent 跑亂」的問題消失——執行順序由依賴圖決定，不靠 agent 自己猜。

---

## Spec session vs Vibe session

在 Kiro 裡，每次開新 session 時，你用 **session picker（工作階段選擇器）**選擇模式。

| 維度 | Spec session | Vibe session |
|------|-------------|--------------|
| 用途 | 複雜功能、完整 app、重大重構、團隊協作 | 快問快答、探索、學習、概念驗證 |
| 結構 | 強制走 requirements → design → tasks 三階段 | 對話式，無固定產物 |
| 產出 | .kiro/specs/<feature>/ 三個 Markdown 檔 | 直接修改程式碼（或只是回答） |
| 可轉換嗎？ | 本身就是 spec | 可以在 vibe 對話中啟動 spec 流程（具體啟動方式以 kiro.dev/docs 最新文件為準） |

「Vibe」這個詞來自「vibe coding」（2025 年 Karpathy 在 X 上推廣的說法），意思是隨興、憑感覺地和 AI 一起寫程式。Kiro 把它收進來，給它一個正式的槽位，而不是假裝所有工作都需要走 spec。

> 「Spec session 給你複雜任務的清晰；Vibe session 給你探索的流動。」——kiro.dev/blog/introducing-kiro/（查證日期 2026-06-30）

### Autopilot vs Supervised：另一個維度

這兩個模式和 Spec/Vibe 是**正交**的，任意組合：

- **Autopilot**（預設）：agent 端到端自主跑——建檔、跨檔修改、執行命令、做架構決策，不問你。
- **Supervised**：每一輪包含檔案修改的 turn，agent 都停下來等你批准，以 hunk 粒度接受或拒絕。

規則：Spec session 搭配 Supervised，適合審閱規格轉程式碼的每個步驟。Vibe session 搭配 Autopilot，適合快速試驗。但這只是習慣，你可以自由組合。

---

## 底層機制：一個 Spec session 的完整流程

以「在電商後台新增 CSV 批次匯入商品」為例，走一遍 Requirements-First 流程：

**Step 1 — 啟動**

在 Kiro 開一個 Spec session，輸入：

```
我需要讓後台管理員能上傳 CSV 檔批次新增商品，
欄位包含 name, price, stock, category_id。
最多 1000 筆，超過報錯。
```

**Step 2 — requirements.md 生成**

Kiro agent 提案 requirements.md，你看到的片段可能是：

```markdown
## User Stories

### US-1：CSV 批次匯入商品
身為後台管理員，我想要上傳一份 CSV 檔來批次新增商品，
以便減少逐筆手動輸入的時間。

### 驗收條件

WHEN 管理員上傳一份格式正確、筆數 ≤ 1000 的 CSV
THE SYSTEM SHALL 解析每一列並批次建立商品記錄，
並顯示成功匯入的筆數。

WHEN 管理員上傳一份筆數超過 1000 的 CSV
THE SYSTEM SHALL 拒絕處理並顯示錯誤訊息，
說明最大允許筆數為 1000。

WHEN CSV 任一列缺少必填欄位（name / price / stock / category_id）
THE SYSTEM SHALL 記錄該列的列號與錯誤原因，
繼續處理其餘合法列，最後回報部分匯入結果。
```

你審閱這份文件。注意第三條：你原來的需求沒有說「部分匯入」還是「全失敗」，Kiro 選了「partial import」。如果這不是你要的，現在改，比事後改程式碼便宜。

**Step 3 — design.md 生成**

你確認 requirements.md 之後，Kiro 提案 design.md：

```markdown
## 架構概覽

上傳 → CsvImportController → CsvParserService → ProductRepository

## 序列圖

管理員 → POST /admin/products/import (multipart/form-data)
  → CsvImportController.handle()
  → CsvParserService.parse(stream) → [row₁, row₂, ...]
  → 驗證每列（ProductValidator.validate(row)）
  → ProductRepository.bulkCreate(validRows)
  ← 回傳 { imported: N, errors: [...] }

## 介面定義

POST /admin/products/import
  Body: multipart/form-data, field=file (.csv)
  Response 200: { imported: number, errors: Array<{ row: number, reason: string }> }
  Response 400: { error: "CSV exceeds 1000 rows" }
  Response 422: { error: "Invalid CSV format" }
```

**Step 4 — tasks.md 生成**

```markdown
- task 1: 建立 products 資料表的 bulk insert migration
- task 2: 實作 CsvParserService（parse + validate）
  depends_on: []
- task 3: 實作 ProductRepository.bulkCreate()
  depends_on: [1]
- task 4: 實作 CsvImportController + 路由
  depends_on: [2, 3]
- task 5: 寫整合測試（正常路徑 + 超量 + 部分失敗）
  depends_on: [4]
- task 6: 更新 OpenAPI spec
  depends_on: [4]
```

Wave 1 = {task 1, task 2}（並行），Wave 2 = {task 3}，Wave 3 = {task 4}，Wave 4 = {task 5, task 6}（並行）。

**Step 5 — 實作**

你確認 tasks.md，按下執行。Kiro 按 wave 跑，每個 wave 結束你都能看到進度。

---

## .kiro/steering/：讓 agent 記住「這個專案是什麼」

每次新開 session，agent 的 context 是空的。它不知道你的專案是 TypeScript 還是 Python、用 Prisma 還是 TypeORM、資料夾要怎麼命名。你每次都要重新說，很累。

Steering 解決這個問題：把這些「永遠為真的背景知識」寫進 Markdown 檔，放在 .kiro/steering/ 下，agent 根據 inclusion mode 自動注入。

### 三個預設檔案

```
.kiro/
  steering/
    product.md     ← 這個產品是什麼、目標用戶、核心功能、商業目標
    tech.md        ← 框架、函式庫、工具、技術限制
    structure.md   ← 資料夾結構、命名慣例、import 規則、架構邊界
```

具體範例：

**product.md**

```markdown
---
inclusion: always
---

# Merch Store — 商品後台

這是一個給小型電商店主使用的商品管理後台。
主要用戶：獨立店主，技術能力中等，不會 SQL。
核心功能：商品 CRUD、庫存管理、CSV 批次匯入、訂單查看（唯讀）。
商業目標：降低店主日常維運時間，目標操作流程 < 3 步。
```

**tech.md**

```markdown
---
inclusion: always
---

# 技術棧

- Runtime: Node.js 22 + TypeScript 5.4
- Framework: Fastify 4（非 Express）
- ORM: Prisma 5（非 TypeORM、非 Sequelize）
- DB: PostgreSQL 16
- 測試: Vitest + Supertest
- 打包: 不需要，直接 tsx
```

**structure.md**

```markdown
---
inclusion: always
---

# 目錄規則

src/
  modules/<domain>/     ← 每個領域一個資料夾
    controller.ts       ← Fastify route handler
    service.ts          ← 業務邏輯
    repository.ts       ← DB 存取
    schema.ts           ← Prisma schema types + Zod validators
  shared/               ← 跨模組共用工具
  app.ts                ← Fastify 實例

命名：camelCase 函式、PascalCase class、snake_case DB 欄位。
```

### 四種 inclusion mode

| mode | 何時注入 | 使用時機 |
|------|---------|---------|
| `always` | 每個 session | 所有 agent 都必須知道的事（tech stack、產品定義） |
| `fileMatch: "src/modules/products/**"` | 開啟或修改到 products 模組的檔案時 | 針對特定模組的規則 |
| `manual` | 你在 prompt 裡打 `#steering-file-name` 時 | 不常用、只在需要時參考的文件（如部署規則） |
| `auto` | agent 根據 steering 檔的 description 自行判斷 | 讓 agent 決定要不要引入 |

Workspace 的 steering 檔（.kiro/steering/）優先順序高於全域 steering（~/.kiro/steering/）。

**為什麼不直接把這些貼進 system prompt？**

因為 steering 是版本控管的一部分，放在 .kiro/steering/ 裡可以被 git 追蹤、在 PR 中 review、用 fileMatch 做精確範圍控制。把它貼在某個工具的設定頁面，同事就看不到，也沒有歷史紀錄。

---

## .kiro/hooks/：事件驅動的 agent 自動化

hooks 是 Kiro 的 CI/CD-like 機制，但跑在你的本地開發環境——每次 IDE 發生指定事件，Kiro 自動執行一個 agent prompt 或 shell 命令。

> 注意：官方文件（kiro.dev/docs/hooks/，查證日期 2026-06-30）描述 hooks 為「automated triggers that execute agent prompts or shell commands」。「GitHub Actions for your local IDE」這個比喻來自早期部落格的說法，在查證時的正式文件中未能原文確認，此處作為理解直覺用，非引言。

### 支援的觸發事件

- 檔案層面：`PostFileSave`、`PostFileCreate`、`PostFileDelete`
- prompt 層面：`PostPromptSubmit`
- agent turn 層面：`PostAgentTurn`
- 工具呼叫層面：`PreToolInvocation`、`PostToolInvocation`
- spec task 層面：`PreSpecTaskExecution`、`PostSpecTaskExecution`
- 手動觸發

### JSON hook 格式

hooks 存在 .kiro/hooks/<hook-name>.json：

```json
{
  "name": "sync-types-on-prisma-change",
  "trigger": "PostFileSave",
  "matcher": "prisma/schema.prisma",
  "action": {
    "type": "agent",
    "prompt": "prisma/schema.prisma 被修改了。請執行 `npx prisma generate`，然後檢查 src/modules/**/schema.ts 裡的 Zod schema 是否需要隨 Prisma model 更新，若需要則修改。"
  },
  "timeout": 60,
  "enabled": true
}
```

這個 hook 做的事：每次你存 schema.prisma，Kiro agent 自動跑 prisma generate，並比對現有的 Zod schema，找出不一致的地方。

### 另一個實用範例：存 React component 自動更新測試

```json
{
  "name": "update-tests-on-component-change",
  "trigger": "PostFileSave",
  "matcher": "src/components/**/*.tsx",
  "action": {
    "type": "agent",
    "prompt": "剛才修改了 {{file}}。請查看對應的測試檔（__tests__/{{file_base}}.test.tsx），確認測試仍然覆蓋修改後的行為。若有 gap 請補齊。"
  },
  "enabled": true
}
```

### 也可以跑 shell 命令

```json
{
  "name": "lint-on-save",
  "trigger": "PostFileSave",
  "matcher": "src/**/*.ts",
  "action": {
    "type": "command",
    "command": "npx eslint {{file}} --fix"
  },
  "enabled": true
}
```

### hooks 的建立方式

有三種：
1. **自然語言**：在 Kiro 的 hooks 面板輸入「每次存 .prisma 檔就跑 prisma generate 並更新相關 Zod schema」，Kiro 幫你產生 JSON。
2. **表單**：GUI 填欄位。
3. **直接寫 JSON**：放進 .kiro/hooks/。

workspace hooks（.kiro/hooks/）是專案層級，通常納入 git；user hooks（~/.kiro/hooks/）是個人層級，不納入專案。

---

## 定價與 Auto 模型

（所有數字查證日期 2026-06-30，版本相依，正式使用前請至 kiro.dev/pricing 確認最新。）

| 方案 | 月費 | 信用點 |
|------|------|-------|
| Free | $0 | 50 |
| Pro | $20 | 1,000 |
| Pro+ | $40 | 2,000 |
| Pro Max | $100 | 5,000 |
| Power | $200 | 10,000 |

超出方案的用量：$0.04 / credit（逐量計費）。

計費範圍：vibe & spec 對話、spec refinement、task 執行、hook 執行——都消耗 credit，最小計量單位 0.01。

**Auto 模型**（查證日期 2026-06-30）：預設的模型選擇，Kiro 內部混合 frontier 模型與專門化模型。同樣一個 task，用 Auto 消耗 X credits，切換到 Sonnet（目前實際版本請查 kiro.dev/pricing，版本相依）則消耗約 1.3X credits。Auto 是省錢路徑，Sonnet 是確定性路徑。

充電起始日：Kiro 在 preview 期間（至 2025-09-30）免費，2025-10-01 開始計費。

---

## 對比：Kiro spec vs GitHub Spec Kit

> 如果你對 GitHub Spec Kit 還不熟，先看 [Ch 27 GitHub Spec Kit（一）：安裝與 bootstrap](./27-spec-kit-install.md) 與 [Ch 28 GitHub Spec Kit（二）：/speckit.* 工作流端到端](./28-spec-kit-workflow.md)。

| 維度 | AWS Kiro | GitHub Spec Kit |
|------|---------|----------------|
| 形式 | 獨立桌面 IDE（Code OSS fork） | Copilot 插件（需要 VS Code + GitHub Copilot） |
| 規格檔位置 | .kiro/specs/<feature>/ | .specify/<number>-<feature>/ |
| 規格產物 | requirements.md + design.md + tasks.md | spec.md + design.md + plan.md + tasks.md（四檔） |
| 需求記法 | EARS 明確推薦 | 模板強制「WHAT + WHY，不含 HOW」，不指定 EARS |
| 持久 context | .kiro/steering/ | .specify/constitution.md（project constitution） |
| 事件驅動自動化 | .kiro/hooks/ JSON | 無對應機制（靠 CI 或手動） |
| 執行模式 | Autopilot / Supervised | 無切換，靠 agent 逐步執行 /speckit.implement |
| 並行執行 | waves（依賴圖驅動） | [P] 標記的 parallel group |
| 開源 | 否（closed-source，IDE 基於 Code OSS） | 是（MIT，~116k stars，查證日期 2026-06-30） |
| 成本模型 | credit 制，有免費層 | 依 GitHub Copilot 方案 |

兩者在「先規格、後實作」的核心理念上完全一致。差異在形式：Kiro 是一個完整的 IDE，Spec Kit 是一組掛在 Copilot 上的 prompt 模板。如果你的團隊已深度使用 GitHub + Copilot，Spec Kit 摩擦更小；如果你想要 steering 和 hooks 的完整生態，Kiro 的整合更緊密。

---

## 踩雷集錦

### 雷 1：以為 Vibe session 就不需要規格

**錯誤直覺**：Vibe session 是「不用思考的快速模式」，什麼需求都往裡丟。

**正確認識**：Vibe session 適合的是「我想快速探索 X 是不是可行」或「幫我解釋這個 error」這類問題。一旦你決定真的要把某個功能做進去，切換到 Spec session——或從 vibe 對話中啟動 spec 流程。用 vibe 走完一個複雜功能，你會發現需求沒被記錄、設計沒被審閱、任務沒有依賴關係，最後 agent 的輸出難以追蹤和維護。

### 雷 2：確認 requirements.md 後就不改了

**錯誤直覺**：requirements.md 只有 agent 生成的那一刻重要，之後就是擺著看的文件。

**正確認識**：三檔規格是活文件。如果在看 design.md 時發現原來的 requirements 有遺漏（例如沒寫「部分匯入」的行為），**先回去改 requirements.md**，再讓 agent 重新產出 design.md。如果先跑實作才改規格，你就在製造規格漂移（spec drift）。規格漂移的代價比重跑一次設計階段昂貴，因為你還要搞清楚程式碼和規格現在到底哪個是真相。

> 「規格漂移」這個概念在 [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md) 有更完整的討論。

### 雷 3：把 steering 當成 prompt 補丁

**錯誤直覺**：steering 是「每次 agent 做壞事我就補一條規則進去」的地方。

**正確認識**：Addy Osmani 的觀察很準確（查證日期 2026-06-30）：「As you pile on more instructions... the model's performance in adhering to each one drops significantly」。steering 檔案要保持精簡、高訊噪比。每一條都應該是「所有情境都適用的背景知識」，不是「上次 agent 犯的那個錯誤」的補救指令。補救指令該放在個別 task 的 description 裡，或在 prompt 裡當場說。

### 雷 4：hooks 的 matcher 太寬，觸發太頻繁

**錯誤直覺**：`matcher: "src/**"` 覆蓋所有 src 下的改動，這樣最安全。

**正確認識**：hooks 觸發就消耗 credit、消耗時間。matcher 越寬，每次存檔都可能觸發一個 agent 去跑幾十秒的任務。一個同事在下午連續改了 30 個小檔案，可能觸發 30 次 hook，每次跑 20 秒，光等就等了 10 分鐘。把 matcher 縮到真正需要監控的範圍，並且設定合理的 timeout，避免掛起。

### 雷 5：以為 tasks.md 的 wave 是自動更新的

**錯誤直覺**：我改了 tasks.md，wave 分組會自動重新計算。

**正確認識**：wave 在你確認 tasks.md、開始實作時由 Kiro 計算。如果中途手動修改了 tasks.md 的依賴關係，行為取決於當前版本（查證日期 2026-06-30，建議以 kiro.dev/docs/specs/ 最新說明為準）。安全做法：在確認 tasks.md 前徹底想清楚依賴關係，把高風險的任務放在依賴鏈後面，讓早期 wave 的失敗不至於影響太多後續工作。

---

## 進階延伸

### Requirements-First vs Design-First

Kiro 支援兩種 spec 啟動路徑（查證日期 2026-06-30）：

- **Requirements-First**：先從用戶行為出發，寫 requirements.md，再讓 Kiro 推導 design.md。適合「我知道要做什麼功能，但還沒想好技術方案」的場景。
- **Design-First**：先寫技術設計（例如你已知必須用 WebSocket 實作，或要整合既有 API），再讓 Kiro 反推可行的 requirements 和 tasks。適合技術限制先於需求的場景，例如「我只能用這個第三方 API，功能要繞著它設計」。

### GA 後的新功能（版本相依）

GA（2025-11-17）加入了幾個值得注意的機制（查證日期 2026-06-30）：

- **Kiro CLI**：agent 進入終端機，可以在 shell 環境中執行 spec task。
- **Checkpointing**：agent 執行中可以標記 checkpoint，失敗時回滾到上一個已知良好狀態，而不是從頭跑。
- **Property-based testing**：讓 agent 驗證程式碼是否符合 spec 描述的行為，而不只是通過手寫測試。這是個很早期的功能，以官方最新說明為準。
- **Team management**：透過 AWS IAM Identity Center 管理團隊帳號，適合企業環境。

### Kiro 作為 Amazon Q Developer 的繼任者

Amazon Q Developer（前身是 AWS CodeWhisperer）原本是一個掛在 VS Code、JetBrains 等 IDE 上的插件。Kiro 是一個獨立的 IDE，AWS 提供了從 Q Developer 遷移到 Kiro 的指南（kiro.dev/docs/migrating-from-q-developer/）。關於 Q Developer 具體的下線時程，AWS 官方文件截至查證日期（2026-06-30）未明確給出正式棄用截止日，以官方公告為準。

---

## 動手練習

**目標**：用 Kiro 對一個真實的小功能跑完整 spec → implement 循環。

**前置條件**：安裝 Kiro（kiro.dev，免費層 50 credits 足夠練習），有一個 Node.js 或 Python 小專案，或從空白開始。

**任務**：

1. 建立三個 steering 檔案：product.md、tech.md、structure.md，寫進你的專案背景知識。用 `inclusion: always`。

2. 開一個 Spec session（Requirements-First），輸入：
   ```
   新增一個 REST endpoint GET /api/health，
   回傳 { status: "ok", version: "1.0.0", timestamp: <ISO string> }，
   並寫單元測試。
   ```

3. 審閱 Kiro 生成的 requirements.md。確認它用了 EARS 句型，且至少有一條處理異常路徑（例如伺服器無法取得時間時的行為）。

4. 審閱 design.md，確認介面定義完整（response schema、HTTP status code）。

5. 審閱 tasks.md，確認測試 task 在 implementation task 之後（wave 順序正確）。

6. 開啟 Supervised 模式，跑 implement，逐 hunk 確認每個修改。

7. 建立一個 hook：每次存 .ts 或 .py 檔後，自動跑 lint（`eslint` 或 `ruff`）。

**邊界案例**：故意在 Step 2 的需求裡加一個模糊條件（「盡量快回應」），觀察 Kiro 怎麼在 requirements.md 裡處理（是轉成可測量的條件，還是原封不動保留模糊詞）。這個觀察結果反映了 EARS 強制結構的程度。

---

## 本章重點整理

- Kiro 是 AWS 打造的獨立 SDD IDE（Code OSS fork），不是插件。
- 每個 spec 在 .kiro/specs/<feature>/ 下產出三個 Markdown 檔：requirements.md（EARS 句型）、design.md（架構、序列圖、介面）、tasks.md（依賴圖 → waves）。
- Bugfix spec 用 bugfix.md 取代 requirements.md，句型是「WHEN ... THE SYSTEM SHALL CONTINUE TO ...」。
- Spec session 走三階段結構化流程，適合複雜功能；Vibe session 是對話式探索，適合快問快答。兩者可以切換。
- Autopilot（預設）和 Supervised（逐 turn 審閱）是執行自主度的控制軸，與 Spec/Vibe 正交。
- .kiro/steering/ 提供持久 context，四種 inclusion mode 控制何時注入：always / fileMatch / manual / auto。
- .kiro/hooks/ 提供事件驅動自動化，觸發事件涵蓋檔案存取、agent turn、spec task 邊界等，action 可以是 agent prompt 或 shell 命令。
- tasks.md 執行時，Kiro 分析依賴關係建 waves：waves 之間循序，wave 內並行。
- 定價是信用點制，Auto 模型比直接選 Sonnet 便宜約 23%（Auto X credits ≈ Sonnet 1.3X credits，查證日期 2026-06-30，版本相依）。
- 規格文件要保持活性——需求變就先改 requirements.md，不是先改程式碼。

---

## 自我檢核

- [ ] 用自己的話解釋：requirements.md、design.md、tasks.md 各自解決什麼問題，如果少了其中一個會發生什麼事。
- [ ] 面試被問「Kiro 的 EARS 句型長什麼樣、為什麼用它」，你會怎麼答？舉一個自己想到的例子（不要用文件裡那個表單驗證例子）。
- [ ] steering 的 `fileMatch` 和 `manual` 模式分別在什麼情況下比 `always` 更合適？
- [ ] 你打算在一個 10 人團隊的 Node.js 後端專案導入 Kiro。你會建哪些 steering 檔、哪些 hooks？把清單和理由寫下來。
- [ ] Wave 機制解決了什麼問題？如果沒有 wave，agent 跑 tasks.md 時可能發生什麼錯誤？
- [ ] Autopilot 和 Supervised 各有什麼代價？你在 Spec session 的哪個階段會用 Supervised，為什麼？

---

## 延伸閱讀

1. **Kiro 官方 Specs 文件** — kiro.dev/docs/specs/  
   三檔規格的規範定義、Requirements-First vs Design-First 兩種啟動路徑、wave 執行機制。這是本章所有事實的一級來源，讀「Workflow」與「Task Execution」兩個 section。

2. **Kiro Feature Specs 文件** — kiro.dev/docs/specs/feature-specs/  
   EARS 句型的原始文件頁面，有 verbatim 的需求範例。如果你對 EARS 模板還不清楚，從這裡的範例入手。

3. **Kiro Steering 文件** — kiro.dev/docs/steering/  
   四種 inclusion mode 的完整說明與 YAML front matter 格式。讀「Inclusion Modes」表格。

4. **Kiro Hooks 文件** — kiro.dev/docs/hooks/  
   所有支援的觸發事件清單與 JSON 格式規範。讀 trigger 清單和 JSON 範例，對照你想要自動化的場景。

5. **Introducing Kiro（官方 launch blog，2025-07-14）** — kiro.dev/blog/introducing-kiro/  
   Nikhil Swaminathan 與 Deepak Singh 寫的產品定位文章。說明 vibe-vs-spec 的設計哲學，以及 steering 和 hook 的原始設計意圖（「update tests when a React component is saved」的例子就來自這裡）。

6. **Announcing new pricing plans and Auto（2025-09-15）** — kiro.dev/blog/new-pricing-plans-and-auto/  
   Credit 模型的詳細說明，Auto 模型的原理，以及為何 Auto 比直接選 Sonnet 便宜。如果你要控制成本，這是必讀的。

7. **EARS: Easy Approach to Requirements Syntax** — Mavin, Wilkinson, Harwood, Novak（IEEE RE09, 2009）  
   EARS 的學術來源論文，由 Rolls-Royce 的需求工程師開發。本章只碰了 WHEN/THE SYSTEM SHALL 這個最核心的句型，原論文定義了五種句型的完整語意與適用情境。搭配 [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md) 一起讀。

8. **Forked Again: AWS's Kiro Is Latest AI Assistant Based on VS Code（2025-07-21）** — David Ramel, Visual Studio Magazine  
   visualstudiomagazine.com/articles/2025/07/21/forked-again-awss-kiro-latest-ai-assistant-based-on-vs-code.aspx  
   獨立技術媒體對 Kiro 的 Code OSS 架構的分析，補充官方文件不太強調的 IDE 分叉脈絡（Cursor / Windsurf / Kiro 都走同一條路）。

---

Kiro 做的是把「規格」從一個你可能會或不會寫的可選步驟，變成一個 IDE 工作流的必經節點。下一章我們拉高視角，看看 2025–2026 年 SDD 版圖裡的其餘玩家——Tessl 的「spec-as-source」走得更激進，BMAD 和 Cursor 走的是不同路線，Claude Code 和 Codex 各自有什麼位置。

→ [Ch 31 其餘版圖：Tessl / BMAD / Cursor / Claude Code / Codex / Devin](./31-tooling-landscape.md)
