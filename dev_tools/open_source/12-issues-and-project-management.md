# Ch 12 — Issue 與專案管理

> **目標**：學會 issue——開一個好的 issue（bug report / feature request）、issue 怎麼跟 PR 串連、label/milestone/assignee、issue 範本、以及 GitHub Projects 與 Discussions。issue 是專案的「待辦清單與對話紀錄」，也是你貢獻的起點。

> **環境**：GitHub。

## 為什麼 issue 是貢獻的起點

很多新手以為「貢獻 = 寫 code 發 PR」。但真實流程往往是**從 issue 開始**：

- 你發現一個 bug → 開 issue 回報（即使你不修，這就是貢獻）
- 你想加功能 → **先開 issue 討論**，確認維護者要不要，再動手（避免白做，Ch 11/17）
- 你想貢獻但不知做什麼 → 翻 issue 找 `good first issue`（Ch 16）

issue 是專案的「待辦清單 + bug 追蹤 + 功能討論 + 對話紀錄」。會開好的 issue，本身就是有價值的貢獻——一個清楚、可複現的 bug report，對維護者的幫助不亞於一個小 PR。

## 先建立直覺：issue 是給維護者的工單

把 issue 想成你向專案提交的「工單」。維護者收到一堆工單，他要能：快速判斷這是什麼（bug？功能？問題？）、嚴不嚴重、能不能複現、優先序多高。

```
   壞的 issue（工單）：
   標題："不能用"
   內容："你的程式壞了，修一下"
   → 維護者：壞在哪？什麼版本？怎麼觸發？我無從下手。關閉/冷處理。

   好的 issue：
   標題："Crash on startup when config file has BOM (v2.1.0)"
   內容：環境 + 複現步驟 + 預期 vs 實際 + 錯誤訊息 + 最小範例
   → 維護者：清楚！我能複現，這就修。
```

好 issue 和好 PR（Ch 11）同理：**主動提供維護者需要的資訊，別讓他猜、別讓他問。**

## 開一個好的 Bug Report

bug report 的黃金結構——四個必備元素：

```markdown
### Environment（環境）
- 版本：v2.1.0
- OS：Ubuntu 22.04
- （相關的：runtime 版本、瀏覽器、相依套件版本）

### Steps to reproduce（複現步驟）
1. 建立一個有 BOM 的 config.json
2. 執行 `myapp start`
3. ...

### Expected behavior（預期）
程式正常啟動。

### Actual behavior（實際）
崩潰，錯誤訊息：
```
Error: Unexpected token  in JSON at position 0
  at JSON.parse ...
```

### Minimal reproduction（最小複現，加分）
附一個最小的能重現問題的範例/repo。
```

**最關鍵的是「複現步驟」**——維護者無法修一個他不能複現的 bug。能附上「最小可複現範例」（minimal reproducible example，MRE）的 bug report 是黃金等級——它把問題濃縮到最小，維護者一跑就看到，修復速度快十倍。

> 「我這邊不能複現」是 bug report 最常見的死法。如果維護者照你的步驟複現不出來，issue 就卡住。所以複現步驟要**精確、完整、從乾淨環境可重跑**——別假設維護者知道你的特殊設定。

## 開一個好的 Feature Request

功能請求不同於 bug——重點是**說服**維護者這個功能值得加：

```markdown
### Problem（問題）
我想做 X，但目前做不到 / 很麻煩，因為...
（講你遇到的真實問題，不是直接講解法）

### Proposed solution（建議解法）
也許可以加一個 Y...

### Alternatives considered（考慮過的替代）
我試過 Z，但因為...不行。

### Use case（使用場景）
這在...情況下會幫助到...這類使用者。
```

關鍵心法：**先講問題（problem），再講解法（solution）。** 新手常直接說「加這個功能」（解法），但維護者更想知道「你遇到什麼問題」（也許有更好的解法、也許這問題已有別的解）。「我想要 X 功能」不如「我遇到 Y 問題，X 可能可以解」。

> 重要：**大功能動手前先開 issue 討論。** 別悶頭寫完 500 行 PR 才發現維護者根本不想要這個功能/方向（白做）。先 issue 對齊：「我想做 X 解決 Y，你們有興趣嗎？方向對嗎？」有共識再寫。這是 Ch 17 會強調的「貢獻前功課」。

## issue 與 PR 的串連

issue 和 PR 在 GitHub 緊密相連：

```
   issue #123「Crash on BOM config」
        │ 有人決定修
        ▼
   PR「Strip BOM before parsing config (closes #123)」
        │ merge
        ▼
   issue #123 自動關閉（因為 "closes #123"）
```

串連機制（Ch 11 提過）：

- PR 描述/commit 寫 `Closes #123` / `Fixes #123` → merge 時自動關閉該 issue。
- issue 和 PR 互相顯示交叉引用（在 issue 裡看得到「PR #456 提到了我」）。
- 在留言打 `#123` 自動變成連結。

這讓「問題 → 修復」有完整的可追溯鏈：任何人看 issue #123 都能找到修它的 PR，看 PR 也知道它解決哪個 issue。

## label / milestone / assignee：組織 issue

維護者用這些把一堆 issue 組織起來（你當維護者時會用，Ch 30 triage）：

- **label（標籤）**：分類 issue。常見：`bug`、`enhancement`、`documentation`、`good first issue`、`help wanted`、`wontfix`、`duplicate`、優先序 `priority: high`。一個 issue 可多標籤。
- **milestone（里程碑）**：把 issue 歸到某個版本/階段（如 `v3.0`）。追蹤「這個 release 還剩哪些沒做」。
- **assignee（指派）**：誰負責這個 issue。你想做某 issue，可以留言「I'd like to work on this」請維護者指派給你（避免兩人重複做，Ch 17）。

對貢獻者：
- 找 `good first issue` / `help wanted` 標籤 = 維護者歡迎外部貢獻的入口（Ch 16）。
- 看 milestone 知道專案近期重心。
- 認領 issue 前看有沒有 assignee（已有人做就別重複）。

## issue 範本

像 PR 範本（Ch 11），專案常有 **issue 範本**（`.github/ISSUE_TEMPLATE/`）——你開 issue 時，GitHub 讓你選類型（Bug / Feature），自動帶出對應的表單。

```
   點 "New issue" →
   ┌──────────────────────────┐
   │ 🐛 Bug Report            │  ← 選這個，帶出 bug 表單（環境/複現/預期...）
   │ ✨ Feature Request       │  ← 選這個，帶出功能表單（問題/解法...）
   │ 💬 Question → Discussions │  ← 引導到 Discussions（不是 issue）
   └──────────────────────────┘
```

填範本（不要清空亂寫）——它是維護者明說「我需要這些資訊」。現代專案用 **issue forms**（YAML 定義的結構化表單，有必填欄位），比舊的 markdown 範本更能確保你提供完整資訊。Ch 33 教你為自己的專案設範本。

## GitHub Projects 與 Discussions

GitHub 還有兩個協作工具：

- **Projects**：看板（kanban）/表格式的專案管理，把 issue/PR 排進「Todo / In Progress / Done」欄位、加自訂欄位（優先序、估時）。維護者用它規劃，你大概只會在被加入團隊時碰到。
- **Discussions**：論壇式的討論區，給「不是 bug 也不是明確功能」的東西——問題、想法、Q&A、公告。**很多專案要求「問問題去 Discussions，不要開 issue」**——因為 issue 是給「可行動的工作項目」（bug/feature），問題不該佔用 issue 追蹤。

> 重要區分：**issue = 可行動的工作（bug 要修、feature 要做）；Discussions = 對話（問題、想法、求助）。** 把「怎麼用這個功能？」開成 issue 是常見失禮——那是 Discussions（或該專案指定的 Q&A 管道）的事。亂開 issue 會被維護者轉走或關閉。

## 踩雷集錦

1. **bug report 沒複現步驟**：維護者不能複現就修不了。精確、完整、可從乾淨環境重跑的步驟是核心。
2. **「不能用」「壞了」式 issue**：沒環境、沒步驟、沒錯誤訊息——等於沒提供資訊。維護者無從下手。
3. **feature request 只講解法不講問題**：「加 X 功能」不如「我遇到 Y，X 可能可解」——讓維護者理解真實需求、可能提供更好解法。
4. **大功能不先討論就直接寫 PR**：可能白做（維護者不想要/方向錯）。先開 issue 對齊（Ch 17）。
5. **把問題（怎麼用）開成 issue**：那是 Discussions/Q&A 的事。issue 給可行動的 bug/feature。
6. **無視 issue 範本**：範本是維護者要的資訊，清空亂填很失禮。
7. **重複開 issue 不先搜尋**：開 issue 前**先搜尋**有沒有人回報過（包括已關閉的）——重複 issue 浪費維護者時間，會被標 `duplicate` 關閉。

## 進階：再往深一層

- **搜尋既有 issue**：開 issue/PR 前用 GitHub 的搜尋（`is:issue is:open`、`is:issue is:closed`）找有沒有人提過——避免重複，也可能找到已有解法或討論。
- **issue forms（YAML）**：比 markdown 範本強，能設必填欄位、下拉選單、checkbox——強制提供完整資訊（Ch 33）。
- **`good first issue` / `help wanted` 的生態**：GitHub 有專門頁面聚合這些標籤跨專案（Ch 16）。維護者用它招募貢獻者。
- **issue 的關閉禮儀**：維護者關 issue 時該說明原因（修好了/wontfix/duplicate/無法複現）。被關了別玻璃心——理解原因，必要時禮貌追問（Ch 20）。
- **任務追蹤 task list**：issue 描述裡的 `- [ ]` 能拆子任務、追蹤進度，甚至引用其他 issue（`- [ ] #45`）做成 tracking issue。
- **自動化**：label 自動指派（依檔案路徑）、stale bot 自動關閉久無回應的 issue、issue→project 自動歸類——Ch 30 triage 自動化。

## 動手練習

1. 找一個你用過的開源專案，看它的 issue 列表：有哪些 label？有沒有 issue 範本（點 New issue 看）？有沒有 Discussions？
2. 練習寫一個完整的 bug report（含環境/複現/預期/實際/最小範例），就算不真的送出，練結構。
3. 練習寫一個 feature request，刻意「先講問題再講解法」。
4. 在你自己的測試 repo 開幾個 issue、加 label、開一個 PR 用 `Closes #N` 關掉一個——體驗 issue↔PR 串連。
5. 找一個專案的 `good first issue` 標籤頁，看那些 issue 長怎樣（為 Ch 16 找專案鋪路）。
6. 找一個被標 `duplicate` 或 `wontfix` 關閉的 issue，理解維護者為什麼關它——學「什麼 issue 不該開」。

## 本章重點整理

- issue 是貢獻的起點（回報 bug、討論 feature、找 good first issue），會開好 issue 本身就是貢獻。
- 好的 bug report：環境 + **精確可複現的步驟** + 預期 vs 實際 + 錯誤訊息 +（加分）最小複現範例。
- 好的 feature request：**先講問題再講解法** + 替代方案 + 使用場景；大功能動手前先開 issue 對齊。
- issue↔PR 串連：`Closes #N` 自動關 issue、互相交叉引用，形成可追溯鏈。
- label/milestone/assignee 組織 issue；`good first issue`/`help wanted` 是貢獻入口。
- issue = 可行動的工作（bug/feature）；Discussions = 對話（問題/想法）。別把問題開成 issue。
- 開 issue 前先搜尋避免重複。

## 自我檢核

- [ ] 一個好的 bug report 最關鍵的元素是什麼？為什麼？
- [ ] feature request 為什麼要「先講問題再講解法」？
- [ ] issue 和 PR 怎麼串連？`Closes #N` 做什麼？
- [ ] 什麼該開 issue、什麼該去 Discussions？舉例。
- [ ] 開 issue 前該先做什麼（避免被標 duplicate）？

## 延伸閱讀

### 官方文件

- **[GitHub Docs: About issues](https://docs.github.com/en/issues/tracking-your-work-with-issues/about-issues)** 與 **[About issue and PR templates](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests)**
  - **讀哪裡**：issue 基礎、範本/forms。
  - **和本章的關聯**：本章操作面的權威；Ch 33 設範本會再用。

- **[GitHub Docs: About discussions](https://docs.github.com/en/discussions/collaborating-with-your-community-using-discussions/about-discussions)**
  - **讀哪裡**：issue vs discussions 的定位。
  - **和本章的關聯**：釐清「什麼該去 Discussions」。

### 部落格 / 文章

- **[How to write a good bug report](https://www.chiark.greenend.org.uk/~sgtatham/bugs.html)** — Simon Tatham（PuTTY 作者）
  - **這篇說什麼**：經典的「如何有效回報 bug」，複現步驟、最小範例的重要性。
  - **為什麼值得讀**：bug report 主題最權威的一篇，作者是資深開源維護者。

- **[Minimal Reproducible Example](https://stackoverflow.com/help/minimal-reproducible-example)** — Stack Overflow
  - **這篇說什麼**：怎麼做出最小可複現範例。
  - **和本章的關聯**：bug report「最小複現」的具體方法。

issue 和 PR 都會開了，下一章進入 PR 之後的關鍵環節——code review，從「被審」的一方學起：怎麼回應 review、push 更新、處理意見。

→ [Ch 13 Code Review（被審方）](./13-code-review-as-author.md)
