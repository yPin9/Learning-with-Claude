# Ch 43 — 把 SDD 織進團隊

> **目標**：理解如何讓規格驅動開發（Spec-Driven Development, SDD）在整個團隊中落地——用 spec review 取代部分的 code review、用 constitution／steering 建立共同規範、把 spec 與 code 一起進入版本控管，並設計讓這套流程能活下去的儀式與工作流。

---

## 個人工具 vs 團隊實踐

一個人用 SDD，失敗成本很低——你可以隨時丟棄 spec、改流程、跳過某個步驟。五個人一起用 SDD，少了一個共同語言，就會出現：

- 甲寫了 spec，乙直接 vibe code 進去蓋掉設計
- PR 裡有 code 沒有 spec，reviewer 不知道「這段在實現什麼意圖」
- 每個人的 constitution 不同，AI 幫每個人產出的風格天差地遠
- spec 寫了一半，下個 sprint 沒人更新，越來越像廢紙

這些問題都不是工具問題，是**協作協定**（collaboration protocol）的問題。本章把 SDD 從「個人習慣」升級成「團隊規範」。

### SDD 進入團隊的三個層次

```
Level 1：共同語言
  constitution / steering 統一規範
  │
  ▼
Level 2：流程接口
  spec review 作為 code review 的前置關卡
  spec 進 git，與 code 同生命週期
  │
  ▼
Level 3：文化與儀式
  spec 走查、Definition of Ready、持續維護規範
```

三個層次缺一不可。很多團隊只做 Level 1（裝工具），然後等 Level 3 自然長出來。它不會自然長出來。

---

## 歷史脈絡：在這之前人們怎麼做

2010 年代，「讓 AI 參與協作」還不是問題——問題只是「讓人協作」。敏捷流程給了我們：

- **Definition of Done**：task 什麼時候算完
- **PR template**：強制填寫變更動機
- **Architecture Decision Record（ADR）**：重大決策留痕

但這些工件幾乎全是事後文件，它們描述「已經做了什麼」，不約束「接下來要做什麼」。

SDD 把這個方向倒過來：spec 是前置文件，它約束 AI 編碼前的決策空間。問題是，PR template 已經被開發人員內化為習慣；SDD 的 spec 還沒有。在 2025 年以前，沒有成熟的「團隊 SDD 工作流」可以直接搬用。

GitHub Spec Kit（github/spec-kit，2025 年 9 月由 Den Delimarsky 推出）和 AWS Kiro（2025 年 7 月上線、11 月 GA）是目前最具代表性的工具，但它們的設計重心都是個人開發者。**把工具搬進團隊需要額外的協定設計。** 本章的任務就是給你這些協定。

---

## 一、共同規範：constitution 與 steering 的團隊角色

### 什麼是 constitution？

> 如果你還不熟悉 constitution 的概念，先回看 [Ch 28 GitHub Spec Kit（二）：/speckit.* 工作流端到端](./28-spec-kit-workflow.md)

`/speckit.constitution` 指令在 `.specify/memory/constitution.md` 寫下整個專案的治理原則——什麼是這個 codebase 的核心價值、架構原則、品質標準。它不是 README，也不是 ADR；它是 AI 每次接收指令前的「憲法」。

在 Kiro 的對應物是 steering 檔案（`.kiro/steering/product.md`、`tech.md`、`structure.md`）。

> 如果你還不熟悉 steering，先回看 [Ch 30 AWS Kiro：三檔規格、EARS、steering、hooks](./30-kiro.md)

**個人用法**：你自己維護，AI 用它約束輸出。  
**團隊用法**：constitution／steering 是**全團隊共同語言的實體化**。它必須：

1. 被整個團隊 review 才能修改（不是某個人的私貨）
2. 進入 git，變更有完整的 commit history
3. 有人負責定期 review（建議每個 sprint 開始時掃一眼）

### 一個可以直接用的 constitution 章節結構

```markdown
# Project Constitution — [PROJECT NAME]

## Core Values
<!-- 最多 5 條，每條一句話。是「我們是誰」，不是技術規格 -->

## Architecture Principles
<!-- 例：API-first。每個對外接口必須有 OpenAPI spec 先於實作。-->

## Quality Gates
<!-- 什麼條件才算一個 feature 可以進 main -->

## What the AI Agent Must Not Do
<!-- 例：不允許直接存取 production DB；不允許刪除 migration 檔案 -->

## Ubiquitous Language
<!-- 與 DDD 通用語言對接：列出本 bounded context 的核心術語 -->
```

> 如果你還不熟悉通用語言，先回看 [Ch 15 通用語言 Ubiquitous Language](./15-ubiquitous-language.md)

最後一節「Ubiquitous Language」是個人 constitution 幾乎不會寫、但團隊 constitution 非寫不可的欄位。它讓 AI 產出的術語與人類討論的術語一致。

### 팀 steering（Kiro 情境）

Kiro 的 `.kiro/steering/` 資料夾支援四種 inclusion mode（查證日期 2026-06-30）：

| inclusion mode | 行為 |
|---|---|
| `always` | 每次 agent context 都注入 |
| `fileMatch` | 只在 glob 匹配的檔案被觸碰時注入 |
| `manual` | 用 `#steering-file-name` 手動引用 |
| `auto` | 由 agent 依 description 自動判斷是否注入 |

團隊建議：
- `product.md` → `always`（所有人所有 session 都看到）
- `tech.md` → `always`
- `structure.md` → `always`
- 各 bounded context 的子規範 → `fileMatch`（只在對應目錄活動時注入）

---

## 二、spec review 作為新的 code review

### 問題的本質

Code review 的核心問題是：你在 diff 裡看到一行 `if (order.status === 'shipped') { ... }`，但你不知道為什麼這個條件是 `shipped` 而不是 `delivered`。你得去 Jira 找、去 Slack 問，甚至找寫這段的人。這是**意圖缺失**（missing intent）。

SDD 解決這個問題的方式是：code review 之前先有 spec review。Spec 裡清楚寫著需求是什麼、為什麼這樣設計、哪些是有意的取捨。Code review 的時候 reviewer 帶著這個脈絡，立刻就能判斷這個 `shipped` 是按 spec 實作還是 AI 自己發明的。

### 工作流設計：spec review 的位置

```
功能需求到來
    │
    ▼
[Dev] /speckit.specify → 產出 spec.md
    │
    ▼
[PR #1] Spec Review  ← 這裡讓整個 team 對齊意圖
    │                   reviewer 提問、補充需求缺口
    ▼                   merge 代表「我們同意要做這個」
[Dev] /speckit.plan → plan.md
[Dev] /speckit.tasks → tasks.md
    │
    ▼
[PR #2] Plan+Tasks Review  ← 確認技術路線、任務切割
    │
    ▼
[Dev] /speckit.implement
    │
    ▼
[PR #3] Code Review  ← 這時候 reviewer 有 spec/plan/tasks 脈絡
```

三個 PR，各有不同的 reviewer 焦點：

| PR | 評審重點 | 誰來審 |
|---|---|---|
| Spec Review | 需求是否完整？術語是否符合通用語言？驗收條件是否可測試？ | PM + Tech Lead |
| Plan+Tasks Review | 架構決策合理？任務顆粒度夠細？依賴關係正確？ | Tech Lead + Senior Dev |
| Code Review | 實作符合 spec/plan 嗎？AI 有沒有超出任務範圍？測試覆蓋率？ | Dev peers |

### spec review 的 PR template

把以下欄位加進你的 `.github/pull_request_template/spec_review.md`：

```markdown
## Spec Review Checklist

### 需求完整性
- [ ] 所有 user story 都有 EARS 格式的驗收條件
- [ ] 有明確定義「不在範圍內」的邊界
- [ ] 術語與 constitution 的 Ubiquitous Language 一致

### 驗收條件品質
- [ ] 每條 acceptance criteria 可以獨立地被測試
- [ ] 沒有「使用者應該能夠方便地...」這類模糊條件
- [ ] 包含錯誤路徑（unhappy path）

### 意圖捕捉
- [ ] Spec 解釋了「為什麼」，不只是「什麼」
- [ ] 有意的取捨被明確記錄（例：選 REST 而非 GraphQL，原因是...）

### 範圍控制
- [ ] 這個 spec 能被一個 sprint 完成嗎？
- [ ] 如果不行，建議如何拆分？
```

> 如果你還不熟悉 EARS 格式，先回看 [Ch 11 EARS 深入：五種句型馴服英文](./11-ears-notation.md)

### 一個具體的 spec review 對話

假設有人提了一個 spec，其中有這條驗收條件：

```
WHEN a user places an order
THE SYSTEM SHALL send a confirmation email
```

好的 spec review 留言不是「LGTM」，而是：

> 這個驗收條件缺少幾個條件：  
> 1. 如果信箱格式無效，系統應該做什麼？  
> 2. email 發送失敗（SMTP 超時）時，order 應該繼續還是 rollback？  
> 3. 「confirmation email」的格式有 spec 嗎？還是 AI 自行決定？  
>
> 建議拆成：  
> ```
> WHEN a user places an order with a valid email address
> THE SYSTEM SHALL queue a confirmation email within 5 seconds
>
> WHEN the email queue service is unavailable
> THE SYSTEM SHALL complete the order and log a retry task
> ```

這才是 spec review 的核心價值：在 AI 寫任何一行 code 之前，把這些模糊性消滅掉。

---

## 三、spec 與 code 一起進版本控管

### 為什麼要這樣做？

常見的反對意見：「spec 只是過渡文件，用完就沒用了，為什麼要進 git？」

這個直覺錯了。Spec 進 git 至少有三個長期價值：

1. **考古用途**：六個月後某個行為改了，你能追回「當初為什麼這樣設計」
2. **規格漂移（spec drift）偵測**：CI 可以比對 spec 與 code，發現不一致
3. **新人 onboarding**：新成員讀 spec 比讀 code 快一個數量級地理解意圖

> 如果你還不熟悉規格漂移，先回看 [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md)

### 目錄結構

以 Spec Kit 的 scaffold 為基礎，團隊版本的建議結構：

```
project-root/
├── .specify/
│   ├── memory/
│   │   └── constitution.md        ← 版控、需 PR 才能改
│   └── scripts/                   ← 不動，Spec Kit 管
├── specs/
│   ├── 001-user-auth/
│   │   ├── spec.md                ← Spec Review 的對象
│   │   ├── plan.md
│   │   ├── tasks.md
│   │   └── research.md
│   ├── 002-order-checkout/
│   │   └── ...
│   └── README.md                  ← 索引，每個 spec 一行摘要
├── .kiro/                         ← 若用 Kiro
│   ├── steering/
│   │   ├── product.md
│   │   ├── tech.md
│   │   └── structure.md
│   └── hooks/
│       └── *.json
└── src/
    └── ...
```

### `.gitignore` 的取捨

有些人主張把 `tasks.md` 排除在 git 之外，因為它會被 AI 頻繁修改、製造 diff 噪音。

我們的建議：**都進 git，但分開 commit**。

```bash
# 錯誤做法：把 spec 修改混進 code commit
git add src/ specs/001-user-auth/tasks.md
git commit -m "feat: implement user auth"

# 正確做法：spec 變更有獨立的 commit
git add specs/001-user-auth/tasks.md
git commit -m "spec(001): mark task 3 as complete, add edge case note"

git add src/
git commit -m "feat(auth): implement JWT refresh token flow"
```

這樣在 `git log` 裡能清楚區分「意圖的演化」和「實作的演化」。

### CI 加入 spec lint

如果你有 CI/CD 流程，可以加一個簡單的 linter 確保 spec 格式不爛掉：

```yaml
# .github/workflows/spec-lint.yml
name: Spec Lint
on:
  pull_request:
    paths:
      - 'specs/**'

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Check spec completeness
        run: |
          # 每個 spec 目錄必須有 spec.md
          for dir in specs/*/; do
            if [ ! -f "$dir/spec.md" ]; then
              echo "ERROR: $dir missing spec.md"
              exit 1
            fi
          done

          # spec.md 必須包含 acceptance criteria 段落
          for spec in specs/*/spec.md; do
            if ! grep -q "## Acceptance Criteria" "$spec"; then
              echo "ERROR: $spec missing Acceptance Criteria section"
              exit 1
            fi
          done
          echo "All specs look good."
```

這不是「spec 正確性的自動驗證」——那太難了，需要 AI 參與。這是防止 spec 在忙碌的 sprint 裡被完全晾著不更新。

---

## 四、讓流程活下去的儀式

### Definition of Ready（DoR）整合 SDD

很多 scrum team 有 Definition of Done（DoD），但少有人認真執行 Definition of Ready（DoR）。SDD 給了你一個非常具體的 DoR：

**一個 user story 被視為 Ready，當且僅當：**

1. 有一份通過 Spec Review 的 `spec.md`（EARS 格式的驗收條件，無 `[NEEDS CLARIFICATION]` 殘留）
2. 有一份通過 Plan Review 的 `plan.md`（架構決策與取捨已記錄）
3. `tasks.md` 的所有任務都有明確的完成判斷準則

如果 sprint planning 時某個 story 沒有這三份文件，它不進這個 sprint。這聽起來嚴苛，但它的效果是：AI 執行時不會因為需求模糊而走偏，甚至返工。

### Spec 走查（Spec Walkthrough）

每個 sprint 開始前，Tech Lead 或 PM 帶著團隊快速過一遍本 sprint 所有的 spec。這個會議不是讓所有人讀 spec——讀是回家功課。會議的目的是：

- 問出「這個設計在我們的系統裡有什麼衝突？」
- 確認術語一致（AI 後來產出的 code 術語才會一致）
- 讓每個人都知道這個 sprint 我們在建什麼、不建什麼

**建議時間**：每個 story 最多 10 分鐘，整個走查不超過 40 分鐘。

### Constitution 定期評審

Constitution（或 Kiro steering 檔案）是有生命的文件，不是刻在石頭上的。建議：

- **每季度**：做一次完整的 constitution 評審，刪掉已過時的規則，加入從實際踩雷中提煉的新原則
- **每次重大架構決策後**：同步更新 architecture principles 段落
- **每次 AI 產出「奇怪但對的」東西時**：評估是否要把那個「奇怪」的限制明文化進 constitution

---

## 對比：不同規模的團隊怎麼適配

| 面向 | 2-3 人小團隊 | 5-10 人中型團隊 | 10+ 人大型團隊 |
|---|---|---|---|
| Spec Review | 非正式，PR comment | 專門的 Spec Review PR | 分層 review（PM + Arch + Dev） |
| Constitution 治理 | 一個人主導，其他人 comment | PR 需 2 人 approve | Architecture Review Board |
| Spec 與 Code 分支策略 | 同一個 feature branch | spec branch → PR1 merge → impl branch | spec 進 main 才開 impl branch |
| 儀式 | 臨時 spec walkthrough | 固定 sprint ceremony 整合 | 分 squad 各跑自己的儀式，跨 squad 有 spec 同步會 |
| 工具選擇 | Spec Kit 個人安裝 | Spec Kit + BMAD-METHOD | Kiro（團隊功能 + IAM Identity Center）|

Kiro 在 GA（2025 年 11 月 17 日）後加入了透過 AWS IAM Identity Center 進行團隊管理的功能，讓大型組織可以統一管理存取權限與 steering 配置（查證日期 2026-06-30）。

---

## 踩雷集錦

### 雷 1：「我們有 constitution 了，所以 AI 不會出錯」

**錯誤直覺**：constitution 寫了「不允許直接存取 production DB」，所以 AI 產出的 code 一定乾淨。

**正確認識**：Constitution 是 AI context window 裡的一段文字，不是程式層面的執行限制。Context 越大，AI 對 constitution 的遵從率越低（Addy Osmani 稱之為「指令的詛咒」——指令越多，每條的遵從率越低，查證日期 2026-06-30）。Constitution 需要搭配 CI gate、code review、以及 Kiro hooks 等自動化機制一起用，才能真正有約束力。

### 雷 2：「Spec 進 git 以後我們就不動它了」

**錯誤直覺**：Spec 是計畫文件，實作完成後就凍結。

**正確認識**：Spec 凍結等於規格腐化的開始。實作過程中必然會遭遇 spec 裡沒有預期到的情況，這些情況要回頭更新 spec。如果不更新，半年後沒有人知道 spec 說的「user」指的是不是現在 code 裡的 `Customer` 實體。**Spec 與 code 的同步更新要被當成 task 的一部分**，進 tasks.md，不能是口頭承諾。

### 雷 3：「Spec Review 就是讓 PM 讀一下有沒有功能缺」

**錯誤直覺**：Spec Review 是需求確認，不是技術活動，讓 PM 簽個字就好。

**正確認識**：Spec Review 最重要的價值是讓技術人員在 AI 實作前，先看到 AI 會「讀到什麼」。EARS 的驗收條件若有歧義，AI 會做出你意料之外的選擇。François Zaninotto（Marmelab CEO）在 2025 年 11 月的研究指出 Spec Kit 的一個簡單 spec 展開成 8 個 markdown 檔案、超過 1,300 行——若這些 spec 沒有被技術人員認真 review，AI 就靠這些自動展開的內容做決策。Tech Lead 必須在 Spec Review 裡扮演主動角色。

### 雷 4：「這套流程太重，我們 sprint cycle 很短」

**錯誤直覺**：SDD 是「比敏捷更重量」的流程，sprint cycle 短的團隊不適合。

**正確認識**：Spec Review 的核心工件（一份清楚的 spec.md）通常 30-60 分鐘就能寫完，比一次 sprint planning 的討論時間短很多。真正的成本是**心態轉換**：從「先做後想」換成「先想清楚再做」。對 AI 協作來說，這個轉換的投報率很高——AI 不懂「我們通常在這種情況下會…」，它只懂你寫在 spec 裡的東西。

### 雷 5：「我們只需要一份 constitution，不需要 steering 細分」

**錯誤直覺**：一份主 constitution 管全局就好，steering 的分層太麻煩。

**正確認識**：在有多個 bounded context 的專案裡（例如電商有 Order context、Payment context、Inventory context），一份全局 constitution 很快就會變成「什麼都有但什麼都模糊」的文件。Kiro 的 `fileMatch` inclusion mode 讓你針對不同目錄注入不同的 steering 規則；Spec Kit 的 presets 機制讓你把不同子系統的規範模組化。團隊 SDD 需要分層的規範，就像 DDD 需要分層的 bounded context 一樣。

---

## 進階延伸

### 跨工具的 constitution 同步

若團隊同時用多個 AI agent（例如某人用 Spec Kit + Claude Code，某人用 Kiro），constitution 的核心原則需要同步。一個務實的做法：

```
docs/
└── architecture/
    └── CONSTITUTION_SOURCE.md   ← 唯一真相來源（人工維護）

.specify/memory/constitution.md  ← 從 CONSTITUTION_SOURCE.md 編譯而來
.kiro/steering/product.md        ← 從 CONSTITUTION_SOURCE.md 編譯而來
```

可以用 GitHub Actions 在 `CONSTITUTION_SOURCE.md` 被更新後自動同步到各工具的格式。這個架構本身就是 SDD 的遞歸應用：用 spec 管理 spec。

### Kiro Hooks 作為 spec 守衛

Kiro 的 agent hooks（`.kiro/hooks/`）可以在特定事件發生時觸發 agent prompt 或 shell 命令（查證日期 2026-06-30）。結合 SDD，幾個有用的 hook 模式：

```json
{
  "name": "Spec Drift Detector",
  "trigger": "PostFileSave",
  "matcher": "src/**/*.ts",
  "action": {
    "type": "agent",
    "prompt": "Check if this file change conflicts with the corresponding spec in specs/. If there is a drift, describe it concisely."
  }
}
```

這讓每次存檔時 AI 自動比對 spec 與 code，把規格漂移消滅在開發當下而不是 code review 時。

### BMAD-METHOD 的 spec-first persona 分工

BMAD-METHOD（V6.9.0，2026-06-22，查證日期 2026-06-30）提供了一套 12+ 個 persona agent（PM、Architect、Developer、UX），每個 persona 有自己的視角和輸出格式。在團隊 SDD 流程裡可以把它當作「spec 生成的多視角交叉驗證」：

1. **PM persona** 生成 PRD（Product Requirements Document）
2. **Architect persona** 把 PRD 轉成架構 spec
3. **Developer persona** 把架構 spec 轉成 tasks.md

每個轉換點都是一個人工 review 的機會。這比「一個人寫完整 spec」的單點失敗更健壯。

---

## 動手練習

選一個你們團隊正在進行的功能（或假設一個電商的「購物車結帳」功能），完成以下任務：

**Task 1**：寫一份 constitution（或 steering 檔案），包含 Core Values、Architecture Principles、Quality Gates、What the AI Must Not Do、Ubiquitous Language 五個段落。限制：每個段落最多 5 條，每條一句話。

**Task 2**：用 EARS 格式寫出「結帳」功能的 5 條驗收條件，確保涵蓋至少 2 條 unhappy path。

**Task 3**：設計一個 Spec Review PR template，包含至少 8 個 checklist 項目，並說明每個項目的「失敗案例」（什麼樣的 spec 會被這個項目擋掉）。

**Task 4**：假設你的 constitution 加入了「所有 API 必須有 OpenAPI spec 先於實作」這條規則。設計一個 GitHub Actions workflow，在 PR 裡有 `src/api/` 變更但沒有對應 `specs/` 變更時，自動留下 comment 警告。

---

## 本章重點整理

- SDD 進入團隊需要三個層次：共同規範（constitution/steering）、流程接口（spec review + 版控）、文化儀式（DoR、spec walkthrough）。
- Constitution 和 steering 是全團隊共同語言的實體化，必須進 git、需 PR 才能改。
- Spec Review 是在 AI 寫 code 之前消滅模糊性的關卡，reviewer 的焦點是「意圖是否完整、驗收條件是否可測試」而非「功能有沒有做」。
- Spec 與 code 分開 commit，讓「意圖的演化」和「實作的演化」在 git history 裡清晰可查。
- 讓流程活下去需要儀式：Definition of Ready 整合 spec 產物、sprint 前的 spec 走查、constitution 定期評審。
- 常見陷阱：把 constitution 當程式執行限制、凍結 spec 不更新、讓 PM 獨立做 spec review、把 SDD 視為重量級流程。

---

## 自我檢核

- [ ] 用你自己的話解釋：為什麼 spec review 要在 code review 之前，而不是「直接 code review 時帶著 spec 讀」？
- [ ] 如果你被問到「constitution 跟 README 有什麼不同」，你會怎麼回答？
- [ ] 描述一個「spec 進 git 但沒有分開 commit」會帶來什麼具體問題。
- [ ] 假設你的團隊有人問「spec review 是 PM 的事還是工程師的事」，你會怎麼回答？
- [ ] 描述至少兩個具體的 constitution 條款，能讓一個 AI agent 的行為更可預測。
- [ ] 「Definition of Ready 整合 SDD」跟原本的 DoR 有什麼差別？差別在哪裡？

---

## 延伸閱讀

- **GitHub Spec Kit 官方倉庫（github/spec-kit）** — https://github.com/github/spec-kit  
  讀 README 的「Detailed Process」段落，看 constitution → specify → plan → tasks → implement 的完整 Taskify 範例，對照本章的三個 PR 工作流。直接看一個具體範例如何落地。

- **Spec-driven development with AI: Get started with a new open source toolkit（GitHub Blog，Den Delimarsky，2025-09-02）** — https://github.blog/ai-and-ml/generative-ai/spec-driven-development-with-ai-get-started-with-a-new-open-source-toolkit/  
  Spec Kit 的發布文，包含「coding agents as literal-minded pair programmers」這個核心心智模型。理解為什麼工具這樣設計，再來思考團隊流程。

- **Kiro Steering 文件** — https://kiro.dev/docs/steering/  
  Kiro steering 的四種 inclusion mode 和 YAML front matter 格式。理解如何用 `fileMatch` 讓不同 bounded context 有各自的規範注入。（查證日期 2026-06-30）

- **Kiro Hooks 文件** — https://kiro.dev/docs/hooks/  
  Event-driven automation 的完整 trigger 清單和 JSON schema。設計「spec drift detector」hook 時的必讀文件。（查證日期 2026-06-30）

- **BMAD-METHOD GitHub 倉庫（bmad-code-org/bmad-method）** — https://github.com/bmad-code-org/bmad-method  
  V6.9.0 的 12+ persona agents 和 34+ workflows。讀 README 和 docs 的「PM → Architect → Developer」persona 分工，了解如何把 spec 生成做成多角色協作。

- **Waterfall Strikes Back（François Zaninotto，Marmelab，2025-11-12）** — https://marmelab.com/blog/2025/11/12/spec-driven-development-waterfall-strikes-back.html  
  批評 SDD 的最強一篇。讀「Markdown Madness」段落，了解 spec 若缺乏良好 review 流程會膨脹成什麼樣子，反過來確認本章的 spec review 有多重要。

- **The Good Spec（Addy Osmani，Google，2026-01-13）** — https://addyosmani.com/blog/good-spec/  
  Google 工程師的 spec 最佳實踐，包含「指令的詛咒」和「不要 over-spec 瑣碎任務」。對照本章的 constitution 設計原則，理解什麼樣的規範有用、什麼樣的規範會適得其反。（查證日期 2026-06-30）

- **Steering Claude Code: skills, hooks, rules, subagents and more（Anthropic，2026-06-18）** — https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more  
  Claude Code 的分層 context 機制：CLAUDE.md、`.claude/rules/`（支援 path-scoped）、skills、subagents。了解如何用 Claude Code 建立「不同目錄有不同規範」的 bounded context 效果。

---

下一章我們要面對一個更根本的問題：當 spec 越來越完整、AI 越來越可靠，我們應該給它多少自主空間？這不是技術問題，是信任與風險管理的問題。

→ [Ch 44 信任階梯：從輔助規格到自主實作](./44-trust-ladder.md)
