# Ch 32 — 工具橫向對比：什麼任務選什麼

> **目標**：把 GitHub Spec Kit、AWS Kiro、Tessl、BMAD-METHOD、plan-mode agents 排進同一張比較矩陣，找出各自真正擅長的場景、被忽略的鎖定風險、以及最常被低估的失敗模式，讓你選型時有具體依據。
>
> **環境**：本章涉及 Spec Kit v0.11.10、Kiro（GA，2025-11-17）、Tessl Framework（closed beta）、BMAD-METHOD V6.9.0、Claude Code（2026-06-18 blog）、Cursor（截至 2026-06）。所有工具版本、定價、指令集均有查證日期，請以各工具官方文件為準（查證日期 2026-06-30）。

---

## 為什麼選型比想像中複雜

在選工具之前，先說一個容易踩的陷阱：**這些工具宣稱解決同一個問題，但其實各自在不同抽象層面發力**。

把它們粗略地排在一條「結構化程度」軸上：

```
低  ←────────────────────────────────────────→  高
                                               結構化程度

  AGENTS.md   Aider         Cursor      Spec Kit     Kiro       Tessl
 (純約定檔)  (CONVENTIONS) (Plan Mode + rules)  (多檔管線)  (三文件+IDE)  (spec-as-source)
              │                │                │              │             │
              │                │                │              │             │
       靠提示工程        靠規則檔約束       靠 Markdown      靠 EARS +      spec 是
       （幾乎不鎖定）                       管線鎖定        IDE 整合       唯一真相
```

這個位置決定三件事：
1. 你願意提前投入多少規格撰寫時間？
2. 失控時，倒退有多難？
3. 鎖定到哪個廠商或工具鏈？

沒有哪一端天生更好。Kiro 的高結構化在啟動一個新功能時省掉大量模糊討論；在修一個 CSS 間距的 bug 時卻是殺雞用牛刀。

> 如果你對「規格漂移」與「雙文件維護成本」還不熟，先回看 [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md)。

---

## 歷史脈絡：選型焦慮從何而來

2024 年以前，「coding agent 要不要寫 spec」這個問題幾乎不存在——大家都是直接貼需求讓 Copilot 補碼。這造成了兩個問題：

- **Agent 把直覺當規格**：「幫我做登入功能」會得到一份假設了 JWT、bcrypt、PostgreSQL 的程式碼——這些假設從未被確認過。
- **沒有共同語言**：PM 寫的 story、工程師的 issue、agent 看到的 prompt，三件事說的可能不是同一個功能。

2025 年中，GitHub Spec Kit（2025-08-21 建立，2025-09-02 公告）、AWS Kiro（2025-07-14 preview，2025-11-17 GA）、Tessl Framework（2025-09-23 launch）幾乎同時出現，都在試圖解決這個問題：**給 agent 明確的書面意圖，而不只是對話上下文**。

值得注意的是，「規格驅動開發」這個詞本身沒有單一的發明者——它是 2025 年間在 Grove 的演講、Spec Kit / Kiro / Tessl 工具群、以及開發者社群討論中有機浮現的術語，和更老的「可執行規格（Specification by Example / BDD）」傳統有部分重疊但又不同（如果你對這段歷史有興趣，[Ch 25 TDD/BDD/MDA 譜系](./25-tdd-bdd-mda-lineage.md) 有完整梳理）。問題是工具解法不同，帶來了選型混亂。

---

## 六個維度的對比

### 1. 核心哲學

| 工具 | 核心主張 | Spec 在哪裡 |
|------|----------|------------|
| **GitHub Spec Kit** | 規格是 agent 的作業說明 | `.specify/specs/<feature>/` 下的 spec.md / plan.md / tasks.md |
| **AWS Kiro** | IDE 原生的三文件 SDD | `.kiro/specs/<feature>/` 下的 requirements.md / design.md / tasks.md |
| **Tessl Framework** | Spec 是唯一真相，程式碼是衍生物 | 用 Component spec / Usage spec（closed beta，格式以官方最新為準） |
| **BMAD-METHOD** | 文件（PRD、架構、user story）是源頭，程式碼在下游 | 無固定目錄；產出 PRD、架構文件、user story，供 IDE agent 使用 |
| **Cursor Plan Mode** | 進 IDE 之前先讓 agent 研究、提問、出計畫 | 預設存家目錄，可 Save to workspace（路徑依版本） |
| **Claude Code Plan Mode** | 在 read-only 模式下探索，不改檔案 | 無固定產物；探索結果留在對話裡或手動記錄 |

### 2. 結構化程度

「結構化程度」可以用一個具體問題衡量：**我不在場的時候，工具能否阻止 agent 做出超出規格範圍的決策？**

| 工具 | 結構化程度 | 說明 |
|------|-----------|------|
| GitHub Spec Kit | 中～高 | 模板明確分離 WHAT（spec）/ HOW（plan）/ WHEN（tasks）；`[NEEDS CLARIFICATION]` 標記未決事項；tasks 標 `[P]` 說明平行安全性（查證日期 2026-06-30） |
| AWS Kiro | 高 | EARS 語法強制句型（`WHEN ... THE SYSTEM SHALL ...`）讓需求可測；Supervised 模式每轉都要人批准；Autopilot 模式則全自動（查證日期 2026-06-30） |
| Tessl Framework | 最高（理論上） | 1:1 spec-to-file，`// GENERATED FROM SPEC - DO NOT EDIT` 標記使程式碼真正成為衍生物；但仍在 closed beta，實際行為以官方最新為準（查證日期 2026-06-30） |
| BMAD-METHOD | 中 | 有 12+ persona agents 把關（PM、Architect、Dev），流程有 34+ workflow；但最終執行仍依賴 IDE agent 遵守文件 |
| Cursor Plan Mode | 低～中 | 計畫是 Markdown，agent 不保證逐條執行 |
| Claude Code Plan Mode | 低 | Read-only 探索確保不誤改，但計畫本身沒有強制格式 |

### 3. 鎖定程度（Lock-in）

鎖定來自三個維度：**產物格式**、**IDE 依賴**、**指令集演化速度**。

| 工具 | 格式鎖定 | IDE 鎖定 | 指令演化風險 |
|------|---------|---------|------------|
| GitHub Spec Kit | 低（純 Markdown + 目錄結構） | 無（支援 30+ agent；查證日期 2026-06-30） | 高：v0.11.10，2026-06-29；175+ releases；指令從 `/specify` 改成 `/speckit.*`，未來可能再改 |
| AWS Kiro | 中（EARS 格式、`.kiro/` 目錄結構） | 高（獨立 IDE，非插件） | 中：GA 後已穩定，但功能仍快速增加 |
| Tessl | 高（spec 是程式碼的唯一來源，整個開發模式都要改） | 中（以 spec 為中心，不綁特定 IDE） | 高：closed beta，格式未定案 |
| BMAD-METHOD | 低（產出是標準 Markdown；供任何 IDE agent 用） | 無 | 中：V6.9.0，但版本快速迭代 |
| Cursor Plan Mode | 無（計畫存在硬碟上的 Markdown） | 高（Cursor 本身） | 低（Plan Mode 是 Cursor 的子功能） |
| Claude Code Plan Mode | 無 | 中（Claude Code CLI/IDE） | 低 |

### 4. 典型適用場景

| 場景 | 推薦工具 | 理由 |
|------|---------|------|
| 全新功能、需要跨 PM/Dev 對齊需求 | AWS Kiro（Spec session + Supervised） | EARS 讓需求可測；三文件流程天然對齊產品-工程-QA |
| 想要 spec-first 但保留 agent 選擇自由 | GitHub Spec Kit | 支援 30+ agent；Markdown 產物可攜 |
| 現有大型 codebase 的 bug 修復 | Cursor Plan Mode 或 Claude Code Plan Mode | 輕量，不強迫生成一堆文件；Zaninotto 指出 SDD 在大型既有 codebase「幾乎無法使用」 |
| 組織要建立 AI-native 開發流程、長期維護 | BMAD-METHOD + Spec Kit 組合 | BMAD 產 PRD/架構；Spec Kit 驅動實作；兩者都是 Markdown |
| 你想實驗「spec 是真相、程式碼是衍生物」 | Tessl（closed beta） | 唯一真正做到 spec-as-source 的工具，但要接受 beta 風險 |
| 快速原型，腦中有完整想法 | Kiro Vibe session 或 Claude Code（無 plan mode） | 結構化帶來的是開銷，不是幫助 |

### 5. 失敗模式

這是選型時最容易被忽略的一欄。

| 工具 | 主要失敗模式 | 來源 |
|------|------------|------|
| GitHub Spec Kit | Agent 產生大量 Markdown 但不遵守；`/speckit.implement` 跳過測試；spec 和程式碼很快就漂移（spec drift） | Eberhardt（Scott Logic）、HN yoaviram |
| AWS Kiro | 一個小 bug 膨脹成 4 個 user story / 16 條驗收條件（scope inflation）；Autopilot 刪程式碼且不自動回復 | Böckeler（martinfowler.com）、HN hatmanstack |
| Tessl | 同一份 spec 在不同 run 產生不同程式碼（非決定性，Böckeler 觀察）；closed beta 所有細節可能大改 | Böckeler（martinfowler.com） |
| BMAD-METHOD | 文件層次豐富但執行完全依賴下游 agent；如果 agent 不讀文件，流程照樣崩 | 無系統性研究；BMAD 本身誠實記錄為「高槓桿，高複雜度」 |
| Cursor Plan Mode | 計畫只是 Markdown，agent 可以不遵守；Plan Mode 的 read-only 邊界只在切換之前 | Cursor 官方文件 |
| Claude Code Plan Mode | 同上；Plan Mode 沒有強制產物格式，容易淪為走形式 | 無硬性結構 |

Addy Osmani（Google）總結了一個跨工具的核心問題：「指令詛咒（curse of instructions）」——當你堆越來越多規則進 spec，每條規則的遵守率不是維持原有水準，而是下降；context 太大時模型直接崩潰。不要以為 spec 寫越詳細越好（查證日期 2026-06-30）。

### 6. 定價與成本結構

| 工具 | 定價模型 | 試用門檻 |
|------|---------|---------|
| GitHub Spec Kit | 開源，MIT 授權，免費。需要底層 agent（如 Copilot、Claude）的費用 | 低：`uv tool install` 即可，無須帳號 |
| AWS Kiro | Credit 制：Free 50 credits、Pro $20/月 1,000、Pro+ $40/月 2,000、Pro Max $100/月 5,000、Power $200/月 10,000；超出 $0.04/credit；Auto 模型比 Sonnet 便宜約 1.3 倍（查證日期 2026-06-30；定價隨時可能調整，請以 kiro.dev/pricing 為準） | 中：需要安裝獨立 IDE |
| Tessl | Closed beta，定價未公開 | 高：需要申請資格 |
| BMAD-METHOD | 開源，免費；`npx` 即用 | 低 |
| Cursor | IDE 本身 $20/月（Pro），Plan Mode 是內建功能 | 低（若已有 Cursor） |
| Claude Code | 依 Anthropic API 費率（查證日期 2026-06-30；請以官方 API 定價為準） | 低（若已有帳號） |

---

## 具體情境：同一個功能，六種工具怎麼處理

假設你要在一個電商平台加一個功能：**用戶可以把多筆訂單合併成一張運費單**。

這個功能有業務規則（不同賣家的訂單不能合併）、狀態機（訂單必須在「已付款」狀態）、以及 UI 工作流（checkbox 勾選、確認彈窗、成功通知）。

### Spec Kit 的路徑

你在 Claude Code 裡執行：

```
/speckit.constitution   # 如果還沒有
/speckit.specify        # 描述合併運費單功能的 WHAT 和 WHY
/speckit.clarify        # 讓 agent 問你未決事項（e.g. 同一賣家不同倉庫算幾個運費？）
/speckit.plan           # 產出技術設計：API schema、狀態流、資料模型
/speckit.tasks          # 把 plan 切成帶 [P] 標記的任務清單
/speckit.analyze        # 交叉驗證 spec/plan/tasks 沒有矛盾
/speckit.implement      # 讓 agent 逐條執行任務
```

產出：`.specify/specs/007-merge-shipping/` 下會有 spec.md（用戶故事 + 業務規則）、plan.md（含 API spec JSON 和 data-model.md）、tasks.md（66 步左右）。整個流程耗時取決於你投入多少 review，但要有心理準備：plan 本身可能超過 1,000 行 Markdown（Eberhardt 的實測資料；查證日期 2026-06-30）。

### Kiro 的路徑

開啟一個 Spec session，用自然語言描述需求。Kiro 自動在 `.kiro/specs/merge-shipping/` 下產生三個檔案：

- `requirements.md`：每條需求用 EARS 格式表達，例如：
  ```
  WHEN a user selects orders from multiple sellers
  THE SYSTEM SHALL display a warning that cross-seller merging is not allowed
  ```
- `design.md`：架構、序列圖、錯誤處理
- `tasks.md`：含 dependency graph，Kiro 會把可並行的任務分入同一個「wave」執行

風險：如果你的描述偏模糊，Kiro 有一定機率把「合併運費單」膨脹成包含退款流程、物流追蹤整合、多幣別處理的一套功能（Böckeler 記錄的 scope inflation；查證日期 2026-06-30）。

### Plan Mode 的路徑

在 Cursor 按 Shift+Tab 進 Plan Mode，或在 Claude Code 開 `--plan-mode`（read-only），描述你要做什麼。Agent 會搜尋 codebase，詢問澄清問題，然後輸出一份 Markdown 計畫。

計畫存在對話記錄裡（Cursor 預設存家目錄），**沒有版控、沒有格式約束、也沒有 phase gate**。對這個中等規模功能來說，Plan Mode 可以在 5 分鐘內產出夠用的計畫——但它會不會真的被 agent 逐條遵守，取決於你把多少計畫貼進實際的 implement 指令裡。

### 這個對比的教訓

三條路徑都不是「錯的」。Spec Kit 和 Kiro 給你的是**可版控、可審查、有角色邊界的意圖紀錄**；Plan Mode 給你的是**快速啟動、低開銷的對齊**。選哪條，看你對這個功能的審查需求、團隊規模、以及這個功能在系統裡的重要程度。

---

## 踩雷集錦

### 錯誤直覺 1：「工具結構化程度越高，agent 越聽話」

**正確認識**：結構化程度決定的是你可以**審查哪些決策**，不是 agent 的服從率。Kiro Supervised 模式讓你在每一轉批准，但 agent 仍可能在你批准後做出你不期望的行為。Spec Kit 的 tasks.md 雖然很詳細，但 `/speckit.implement` 在 HN 用戶 yoaviram 的真實案例中跳過了建立測試的步驟。**結構化給你更多檢查點，不給你保證**。

### 錯誤直覺 2：「Spec Kit 和 Kiro 的 spec 是同一種東西，可以互換」

**正確認識**：兩者的核心文件看起來都是 Markdown，但定位完全不同。Spec Kit 的 spec.md 強調「WHAT and WHY, not HOW」（刻意不定技術棧）；Kiro 的 requirements.md 使用 EARS 句型，設計上要直接對應到測試條件。把 Spec Kit 的 spec 貼進 Kiro 不會自動得到有效的 EARS 文件，反之亦然。

### 錯誤直覺 3：「只要工具支援 30+ agents，就不會有鎖定問題」

**正確認識**：Spec Kit 支援 30+ agent 是指**安裝整合**（指令/skill 檔案落在哪個目錄），不是指 `.specify/` 目錄結構或 `/speckit.*` 指令集不會綁定你的工作流程。如果 Spec Kit 的指令集再次大改（歷史先例：`/specify` → `/speckit.specify`），你的 CI 腳本和團隊習慣都要跟著改。格式可攜性（Markdown）和工具鎖定（指令集、腳本、模板）是兩回事。

### 錯誤直覺 4：「Tessl spec-as-source 是 SDD 的終態，遲早要遷移過去」

**正確認識**：Tessl 的「spec 是唯一真相，程式碼是衍生物」在理論上很誘人，但 Böckeler（Thoughtworks）的實測發現同一份 spec 在不同 run 產生不同程式碼——非決定性的 LLM 和「spec 是單一真相」的承諾之間有根本張力。Tessl 的 closed beta 狀態意味著整個模型可能在 GA 前大幅改變。「遲早遷移」的前提是它確實解決了這個張力，目前尚未確認。

### 錯誤直覺 5：「Plan Mode 跑完等於 spec 寫完」

**正確認識**：Cursor 的 Plan Mode 和 Claude Code 的 Plan Mode 產出的是對話裡的一份計畫，沒有強制格式，沒有 phase gate，也沒有把它版控的機制。「跑 Plan Mode」只是降低 agent 無中生有的機率，不是在建立 Spec Kit 或 Kiro 意義上的「spec」。把 Plan Mode 的輸出 copy-paste 到版控倉庫、讓它真正可審查，這步驟需要人主動做。

### 錯誤直覺 6：「Kiro 的 Autopilot 和 Supervised 只是速度快慢的差異」

**正確認識**：兩者的差異是**代理邊界的定義**不同。Autopilot 允許 Kiro 自主跨多個檔案做架構性決策並執行 shell 命令，中間不停下來；Supervised 在每一輪有檔案修改後停下來讓人做細粒度的 hunk-level accept/reject。HN 用戶 hatmanstack 的 Kiro 案例（12+ tasks，4+ subtasks，刪程式碼且不還原）說明 Autopilot 在大型任務上的風險是實質性的。

---

## 選型決策樹

下面給一個快速決策路徑，不是最終答案，是起點：

```
Q1: 這個任務是新功能，還是修 bug / 小調整？
  │
  ├─ 小調整 / bug fix
  │     → 用 Cursor Plan Mode 或 Claude Code Plan Mode
  │       （SDD 工具的 overhead 在這裡是純成本）
  │
  └─ 新功能
        │
        Q2: 有 PM/Design 要一起 review 需求嗎？
        │
        ├─ 有，需求對齊很重要
        │     → AWS Kiro（Spec session + Supervised）
        │       EARS 語法讓驗收條件對 PM 也可讀
        │
        └─ 主要是工程師自己把問題想清楚
              │
              Q3: 想鎖定哪個 IDE / agent？
              │
              ├─ 想保持彈性，換工具成本低
              │     → GitHub Spec Kit
              │       支援 30+ agent，產物是純 Markdown
              │
              ├─ 已有完整的企業文件流程（PRD/架構）
              │     → BMAD-METHOD（先建文件）+ Spec Kit（驅動實作）
              │
              └─ 願意接受 beta 風險，想探索 spec-as-source
                    → Tessl（申請 closed beta；預期介面和格式會大改）
```

---

## 進階延伸：三個設計問題值得繼續思考

**問題一：誰來維護 spec 的正確性？**

Spec drift（規格漂移）是所有結構化工具共同的隱憂——當程式碼改了但 spec 沒有同步更新，spec 就開始說謊。Spec Kit 和 Kiro 都沒有原生的 drift 偵測機制（2026-06-30 查證）。目前有人在探索「agent 在同一個 commit 裡更新受影響的 spec」的方法，但這仍是活躍的、未解決的問題。

> 這個問題在 [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md) 有更完整的討論。

**問題二：Böckeler 的三個層次對選型的影響**

Birgitta Böckeler（Thoughtworks，發表於 martinfowler.com）定義了三個 SDD 嚴謹程度：

- **spec-first**：spec 引導生成，但程式碼是被維護的真實產物
- **spec-anchored**：spec 是活文件，和程式碼並排版控
- **spec-as-source**：spec 是唯一真相，程式碼是衍生物

Spec Kit 和 Kiro 宣稱 spec-anchored，但在實踐中通常落在 spec-first。Tessl 真正在嘗試 spec-as-source。選型時要問自己：你接受的是哪個層次的承諾，不是工具宣傳的是哪個層次。

**問題三：什麼時候根本不需要 SDD 工具？**

Marc Brooker（AWS VP/Distinguished Engineer）在 2026-04-09 的文章裡替 SDD 辯護說：「迭代的是 spec，不是實作，所以不是瀑布。」這個論點成立——前提是你真的在迭代 spec。如果你用 Spec Kit 或 Kiro 只是走形式（先產 spec，然後 approve 一切，讓 agent 跑），你得到的就是加了文件開銷的瀑布。

> 什麼時候不應該用 SDD，在 [Ch 42 什麼時候不要用 SDD](./42-when-not-to-use-sdd.md) 有完整討論。

**問題四：AGENTS.md 作為最小公因數**

當你不確定要選哪個工具時，有一個跨工具都支援的「最小 spec」值得認識：[AGENTS.md](https://agents.md/)。這個由 Agentic AI Foundation（Linux Foundation）維護的開放格式，被 OpenAI Codex、Cursor、Aider、Devin、Roo Code、Gemini CLI、GitHub Copilot coding agent 等 20+ 工具支援（查證日期 2026-06-30）。它不像 Spec Kit 有複雜的命令管線，也不像 Kiro 要求 EARS 格式——它只是一份給 agent 讀的 Markdown 指引，說明這個 repo 的慣例、禁忌、測試方式。

如果你的團隊還在評估選哪套 SDD 工具，先把 AGENTS.md 建起來是安全的第一步：成本幾乎為零，讓 agent 至少知道你的 repo 的規則，未來要遷移到任何工具都不會衝突。

---

## 動手練習

**練習 32-A：給你的下一個任務選工具，說出三個「為什麼不選其他的」**

找一個你接下來一週內要做的任務（哪怕很小），按照上面的決策樹選出一個工具。接著明確寫下：

1. 我選了什麼工具，理由是什麼（對應上面哪個場景）？
2. 為什麼不選 Kiro？
3. 為什麼不選 Tessl？

「因為沒裝」不算理由。要說出這個任務的特性讓另一個工具不合適。

**練習 32-B：找出一個 spec drift 的例子**

在你現有的 repo（或任何開源 repo）裡，找一個 README 或文件描述和實際程式碼行為不符的地方。用這個例子回答：如果這個 repo 用了 Spec Kit 或 Kiro，spec drift 會在哪個環節被發現或被阻止？哪個環節會讓它溜過去？

**練習 32-C：對比相同需求的 EARS 寫法和 Spec Kit spec.md 寫法**

取一個你熟悉的小功能（例：「用戶可以重設密碼」），分別用：
1. Kiro 的 EARS 格式寫出至少 3 條驗收條件（`WHEN ... THE SYSTEM SHALL ...`）
2. Spec Kit 的 spec.md 風格寫出同一功能（只說 WHAT 和 WHY，不提技術實作）

比較兩份文件：哪些資訊在 EARS 版本裡更清楚？哪些資訊在 Spec Kit 版本裡更豐富？這個差異對你的專案意味著什麼？

---

## 本章重點整理

- 這些工具在「結構化程度」軸上佔據不同位置，選錯位置帶來的是開銷，不是保護。
- GitHub Spec Kit：低鎖定、高彈性（30+ agent）、指令集仍在快速演化（查證日期 2026-06-30）。
- AWS Kiro：高結構化、強 IDE 整合、EARS 讓需求可測；scope inflation 是真實風險（查證日期 2026-06-30）。
- Tessl：唯一真正嘗試 spec-as-source 的工具；非決定性問題仍未解；closed beta（查證日期 2026-06-30）。
- BMAD-METHOD：文件驅動、流程完整，但最終執行品質仍由下游 agent 決定。
- Plan Mode 工具（Cursor、Claude Code）：輕量、無鎖定，但計畫沒有強制格式，不能叫做 spec。
- 「指令詛咒」（Addy Osmani）是跨工具的系統性問題：spec 不是越詳細越好。
- Spec drift 是所有工具都沒有真正解決的問題（2026-06-30 查證）。

---

## 自我檢核

- [ ] 我能用自己的話解釋，為什麼「結構化程度高」不等於「agent 更聽話」——如果面試官問我這個問題，我不會只說「因為 agent 會忽略規則」，而是能說出具體的機制。
- [ ] 我能區分 Böckeler 的三個層次（spec-first / spec-anchored / spec-as-source），並說出 Spec Kit 和 Tessl 各自落在哪裡。
- [ ] 我能解釋「指令詛咒」是什麼，以及它對 spec 深度的具體建議。
- [ ] 我能說出 Spec Kit 的指令集從 2025 年到 2026 年發生了什麼改變，以及這件事對「鎖定風險」評估意味著什麼。
- [ ] 我能給「修一個 CSS 間距 bug」和「設計一個新的帳號管理功能」各推薦一個工具，並說出不同的原因。

---

## 延伸閱讀

**[Understanding Spec-Driven Development: Kiro, spec-kit, and Tessl](https://martinfowler.com/articles/exploring-gen-ai/sdd-3-tools.html)**
Birgitta Böckeler（Thoughtworks），發表於 martinfowler.com，2025-10-15。這篇是目前對三大工具最系統、最公平的實測報告。三個層次分類（spec-first / spec-anchored / spec-as-source）是本章比較矩陣的基礎詞彙，必讀。與本章的關聯：驗證第 5 欄「失敗模式」的來源。

**[Putting Spec Kit Through Its Paces: Radical Idea or Reinvented Waterfall?](https://blog.scottlogic.com/2025/11/26/putting-spec-kit-through-its-paces-radical-idea-or-reinvented-waterfall.html)**
Colin Eberhardt（Scott Logic CTO），2025-11-26。目前對 Spec Kit 最完整的逐階段時間測量：constitution 161 行、specify 230 行、plan 2,067 行、tasks 66 步、最後產出 700 行程式碼，總耗時約 4 小時，對比迭代提示 23 分鐘。讀「Plan」和「Implementation」節以及結論。與本章的關聯：量化了高結構化的成本，為「選型要看任務規模」的建議提供數字基礎。

**[Spec Driven Development isn't Waterfall](https://brooker.co.za/blog/2026/04/09/waterfall-vs-spec.html)**
Marc Brooker（AWS VP/Distinguished Engineer），2026-04-09。針對「SDD 是瀑布復辟」最強的反駁：「迭代的是 spec，不是實作」。全文短，建議完整閱讀。與本章的關聯：補充了本章決策樹背後的設計哲學——SDD 的價值在於讓 spec 快速迭代，而不是要求前期完整。

**[GitHub Spec Kit — 官方 Repository（README）](https://github.com/github/spec-kit)**
Den Delimarsky et al.，持續更新（最新 v0.11.10，2026-06-29）。看「Get Started」、「Available Slash Commands」（Core/Optional 分區），以及 spec-driven.md 的詳細流程。與本章的關聯：本章所有 Spec Kit 指令集細節的一手來源；「版本 175+ releases，指令集快速演化」的具體證據。

**[Kiro Docs — Specs](https://kiro.dev/docs/specs/)**
Kiro/AWS 官方文件（查證日期 2026-06-30）。三文件結構（requirements.md / design.md / tasks.md）、EARS 範例、wave 並行執行的定義。搭配 [Autopilot docs](https://kiro.dev/docs/chat/autopilot/) 和 [Steering docs](https://kiro.dev/docs/steering/) 讀完。與本章的關聯：本章 Kiro 欄位的所有具體細節出處；也是第 5 欄「鎖定程度」評估的基礎。

**[How to write a good spec for AI agents](https://addyosmani.com/blog/good-spec/)**
Addy Osmani（Google），2026-01-13。寫 spec 的實作建議，以及三個最重要的誠實警告：指令詛咒、context 超載、人類仍是最終判斷者。讀「curse of instructions」和「you remain the filter」兩節。與本章的關聯：「指令詛咒」是本章踩雷集錦的理論依據；這篇把它說清楚了。

**[BMAD-METHOD Repository](https://github.com/bmad-code-org/bmad-method)**
bmad-code-org，V6.9.0，2026-06-22。如果你想理解「文件驅動、persona agents 分工」這條路線，這是最完整的開源實作。讀 README 頂部和 docs.bmad-method.org 的 workflow 目錄。與本章的關聯：本章中「組織想建立 AI-native 開發流程」場景推薦的工具，提供背景。

**[AGENTS.md — open standard](https://agents.md/)**
Agentic AI Foundation（Linux Foundation 下），持續維護。一份工具中立的 spec-like 指引格式，支援 20+ 工具。讀首頁了解哪些工具支援它、格式規則是什麼。與本章的關聯：「進階延伸問題四」推薦的最小公因數起點，選型未定前的過渡方案。

---

→ [練習 E 用 Spec Kit 把練習 D 的 spec 跑成可動小功能](./practice-e-spec-kit-run.md)
