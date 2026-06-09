# Ch 30 — Issue triage

> **目標**：學會管理 issue 洪流——triage（分類、標籤、優先序）、複現與釐清、關閉的藝術（含 wontfix / duplicate / stale）、引導貢獻者（good first issue）、以及用 bot 自動化。維護者每天面對一堆 issue，會 triage 才不會被淹沒。

> **環境**：GitHub、`gh` CLI。前置：Ch 12（issue 基礎，從貢獻者端）、Ch 28（維護者心態）。

## 為什麼 triage 是維護者的生存技能

一個稍有名氣的專案，issue 會以驚人速度湧入：bug 回報、功能請求、問題、重複、垃圾、AI 生成的東西。如果不管理，issue tracker 會變成「幾百個 open issue、沒人知道哪個重要、新人不敢碰」的墳場——這本身就是專案不健康的訊號（Ch 16 貢獻者會看）。

**triage（分診）** 借自醫療——急診室分類傷患輕重緩急。維護者對 issue 做同樣的事：快速分類每個 issue（是什麼、嚴不嚴重、可不可行動），讓有限的時間用在對的地方。會 triage，你掌控 issue 洪流；不會，你被它淹沒。

## 先建立直覺：issue tracker 是待辦，不是許願池

```
   不健康的 issue tracker            健康的 issue tracker
   ─────────────────              ─────────────────
   500 個 open，沒分類              每個都有 label、清楚狀態
   不知道哪個重要                   優先序明確
   重複/問題/垃圾混在一起           bug/feature/question 分開
   新人不敢碰                       good first issue 標好，引導新人
   → 像許願池/墳場                  → 像有條理的待辦清單
```

維護者的目標：讓 issue tracker 是「**可行動的、有條理的待辦清單**」，不是「什麼都往裡丟的許願池」。triage 就是把湧入的雜訊整理成這個狀態的持續工作。

## triage 的流程：每個 issue 的處理

對每個新 issue，快速判斷（不用深入解決，只是分類）：

```
   新 issue 進來
        │
   1. 它是什麼？     → bug / feature / question / 重複 / 垃圾
        │
   2. 該在這嗎？     → question 引導去 Discussions（Ch 12）；垃圾關掉
        │
   3. 重複嗎？       → 搜尋既有 issue，是的話標 duplicate 關閉、連到原 issue
        │
   4. 資訊夠嗎？     → bug 缺複現步驟 → 要求補充（needs-info），不補就關
        │
   5. 能複現嗎？     → 複現確認（bug 真的存在）
        │
   6. 加 label + 優先序 → bug/enhancement、priority、good first issue...
        │
   7. （選）指派 / 排進 milestone
```

關鍵：**triage 不是「解決 issue」，是「分類 issue」**——快速過一遍，讓每個 issue 有清楚的狀態和標籤。解決是後面的事（或交給貢獻者）。維護者該定期（每天/每週）花固定時間 triage 新 issue，別讓它們積壓。

## label 系統：issue 的分類維度

承 Ch 12（從貢獻者看 label），維護者要**設計**一套 label 系統。常見維度：

```
   類型：    bug / enhancement / documentation / question
   優先序：  priority: critical / high / low（或 P0/P1/P2）
   狀態：    needs-info / needs-repro / confirmed / blocked / wontfix
   難度：    good first issue / help wanted（招募貢獻者！）
   領域：    area: api / area: ui / area: docs（大專案分模組）
   特殊：    duplicate / invalid / stale
```

好的 label 系統讓你（和貢獻者）能快速篩選：`label:bug label:"priority: high"` = 該優先修的。`label:"good first issue"` = 招募新人（Ch 16 貢獻者就是搜這個）。

```bash
gh issue list --label "bug" --label "priority: high"   # 篩重要的 bug
gh issue edit 42 --add-label "good first issue"         # 標新手任務
```

> good first issue 的招募作用：主動把「適合新手、定義清楚」的小任務標出來，是**招募和培養新貢獻者**的關鍵（Ch 16 的另一端）。維護者花時間把 issue 拆成 good first issue 並寫清楚，是投資未來的貢獻者——比自己默默做掉更有長期價值。

## 關閉的藝術

維護者最該練、也最容易做不好的：**關閉 issue**。一個健康的 tracker 需要持續關閉（解決的、不做的、無效的），否則只增不減變墳場。

各種關閉情境與得體做法：

```
   解決了        → "Fixed in #456. Thanks for reporting!" + 連到修復的 PR
   重複          → "Duplicate of #123, let's track it there." + 連結（不是冷冷一句 "dup"）
   不做（wontfix）→ 解釋為什麼不做（scope/複雜度/方向）+ 感謝（像拒絕 PR，Ch 29）
   無法複現      → "Can't reproduce with the steps given. Could you provide X? 
                  Closing for now, feel free to reopen with more details."
   資訊不足      → 要求補充，給合理時間，沒回應再關（needs-info → stale）
   問題（非 issue）→ "This is a usage question — let's move it to Discussions." + 連結
```

關閉的禮儀（和拒絕 PR 同理，Ch 29）：

- **解釋原因**：別冷冷一句 "closing" / "dup"——說為什麼，讓對方理解、不覺得被無視。
- **感謝**：即使是無效 issue，對方花了時間回報。
- **留後路**：「feel free to reopen if...」——關閉不是永久判決。
- **連結相關**：重複連到原 issue、解決連到 PR——維持可追溯。

> 「關閉」不是「失敗」或「不在乎」：很多維護者不敢關 issue（怕傷人、怕漏掉），結果 tracker 爆炸。健康的維護者**果斷但得體地關閉**——一個乾淨的 tracker 比一個塞滿「不會處理」的 issue 的 tracker 對所有人都好（包括回報者，他至少知道狀態）。

## 處理 stale issue（陳舊議題）

很多 issue 會「卡住」——回報者消失、沒人跟進、不夠重要沒人做。這些 **stale issue** 累積會塞爆 tracker。處理方式：

- **stale bot**：自動化——issue 一段時間（如 60 天）沒活動，bot 留言「這個 issue 60 天沒動靜，再 7 天沒回應將關閉」，到期自動關。讓 tracker 自我清理。
- **手動 needs-info 流程**：缺資訊的 issue 標 `needs-info`，問了沒回應、過合理時間就關（可 reopen）。

```yaml
# .github/workflows/stale.yml（用 actions/stale）
- uses: actions/stale@v9
  with:
    days-before-stale: 60
    days-before-close: 7
    stale-issue-message: "This issue has been inactive for 60 days..."
```

> 認識論誠實 + 爭議：stale bot 有爭議——有些人覺得它「機械地關閉真實的 bug」很討厭（issue 沒人理不代表 bug 不存在，自動關閉像在掩蓋問題）。用它要謹慎：對「需要回報者補充資訊」的 issue 合理；對「已確認但還沒人做」的真 bug 別亂關（可能該標 `help wanted` 招人，而非關掉）。stale bot 是工具不是免責——別用它逃避真正該處理的 issue。

## 自動化 triage

維護者的時間有限，盡量自動化（呼應 Ch 27/14）：

```
   ├─ issue 範本 / forms（Ch 12）→ 強制回報者提供完整資訊（少 needs-info）
   ├─ 自動 label：依標題/內容/範本選擇自動加 label
   ├─ stale bot：自動清理陳舊 issue
   ├─ 自動歡迎首次貢獻者（welcome bot）
   ├─ 自動把 issue 加進 project board
   └─ gh + script 批次 triage（Ch 15）
```

```bash
# 批次操作範例（gh，Ch 15）
gh issue list --label "needs-info" --json number,updatedAt --jq '...'  # 找久未更新的
# 批次加 label、批次留言提醒...
```

issue forms（Ch 12）是最有效的前置——強制回報者填環境/複現步驟，大幅減少「資訊不足」的 issue，省下大量 triage 來回。

## 一個完整的 triage 工作流

維護者的日常 triage（每天/每週固定時間）：

```bash
# 1. 看新 issue（上次 triage 後的）
gh issue list --search "is:open no:label"   # 還沒 triage 的（無 label）

# 2. 對每個快速判斷：
#    - 垃圾/問題 → 關閉/引導 Discussions
#    - 重複 → 標 duplicate 連到原 issue 關閉
#    - 缺資訊 → needs-info 要求補充
#    - 真 bug → 複現、加 label + 優先序，小的標 good first issue
#    - feature → 評估 scope（Ch 28），標 enhancement，回應要不要做

# 3. 批次處理 stale（或交給 bot）

# 4. 確保 good first issue / help wanted 有足夠的、寫清楚的任務（招募）
```

目標：每次 triage 後，所有新 issue 都有清楚狀態，tracker 維持「可行動的待辦清單」。

## 踩雷集錦

1. **不 triage，issue 積壓成墳場**：幾百個無分類 open issue，沒人知道哪個重要。定期固定時間 triage。
2. **不敢關 issue**：怕傷人/漏掉，結果 tracker 爆炸。果斷但得體地關（解釋+感謝+留後路）。
3. **冷冷關閉**："dup" / "closing" 一句話。要解釋、連結、感謝（像拒絕 PR，Ch 29）。
4. **把 question 留在 issue**：用法問題該去 Discussions（Ch 12）。引導過去，保持 issue 是可行動的工作。
5. **stale bot 亂關真 bug**：對「已確認但沒人做」的 bug 自動關 = 掩蓋問題。stale bot 對 needs-info 合理，對真 bug 該標 help wanted 招人。
6. **不標 good first issue**：錯失招募新貢獻者的機會。主動把適合的小任務標出來、寫清楚。
7. **triage 變成「解決」**：triage 是分類不是解決。快速過、分類好，解決是後面的事（或交給貢獻者）。

## 進階：再往深一層

- **priority 的客觀化**：用清楚的優先序定義（P0=線上壞了、P1=重要、P2=nice to have），而非憑感覺——讓 triage 一致、可溝通。
- **issue 轉 discussion**：GitHub 可把開錯地方的 issue「轉成 Discussion」（不是關掉），對 question 類友善。
- **project board 整合**：triage 後把 issue 拉進 project board（Todo/In Progress/Done），視覺化規劃（Ch 12）。
- **community triage**：大專案讓社群幫忙 triage（給信任的貢獻者 triage 權限）——分散維護者負擔（Ch 31）。
- **「support」的負擔**：很多「issue」其實是使用者求助（不是 bug）。設好 Discussions/論壇/FAQ 分流，否則 support 會吃掉維護者所有時間。
- **AI slop issue**（Ch 20）：AI 生成的假 bug report / 灌水 issue 也在增加。triage 時辨識（無法複現、內容空洞、像範本填充）並果斷處理。
- **metrics**：open issue 數、平均回應時間、關閉率——這些是專案健康的指標，也幫你看 triage 跟不跟得上。

## 動手練習

1. 在測試 repo 設計一套 label 系統（類型 + 優先序 + 狀態 + good first issue），建幾個 label。
2. 開幾個假 issue（一個 bug、一個 feature、一個重複、一個問題、一個資訊不足），練習 triage：各加對的 label、該關的關（寫得體的關閉留言）、問題引導去 Discussions。
3. 練習寫各種「得體關閉」留言：解決了、重複、wontfix、無法複現——每個都解釋+感謝+留後路。
4. 設一個 stale bot（actions/stale），理解它的參數；思考哪些 issue 適合/不適合被它自動關。
5. 看一個管理良好的大型開源專案的 issue tracker，分析它的 label 系統、good first issue 怎麼標、怎麼關 issue——學健康 tracker 的樣子。
6. 反思：你（Ch 12）當貢獻者開 issue 時，希望維護者怎麼對待你的 issue？這怎麼指導你 triage 別人的？

## 本章重點整理

- triage 是維護者的生存技能——把湧入的 issue 雜訊整理成「可行動、有條理的待辦清單」，而非許願池/墳場。
- triage 流程：判斷類型 → 該不該在這（問題引導 Discussions）→ 重複？→ 資訊夠？→ 複現 → 加 label+優先序 →（選）指派。triage 是分類不是解決。
- 設計 label 系統（類型/優先序/狀態/難度/領域）；good first issue 是招募新貢獻者的關鍵。
- 關閉的藝術：果斷但得體（解釋+感謝+留後路+連結），別冷冷一句；健康 tracker 需要持續關閉。
- stale bot 自動清理陳舊 issue，但有爭議——對 needs-info 合理，別拿來亂關真 bug。
- 盡量自動化（issue forms、自動 label、stale bot、welcome bot、gh 批次）放大有限時間。

## 自我檢核

- [ ] triage 是什麼？它是「解決 issue」還是「分類 issue」？
- [ ] 一個健康的 issue tracker 和墳場差在哪？維護者怎麼維持健康？
- [ ] 關閉 issue 的得體做法包含哪些要素？為什麼不敢關 issue 是問題？
- [ ] good first issue 標籤對維護者有什麼戰略價值？
- [ ] stale bot 的爭議是什麼？什麼 issue 適合/不適合被它自動關？

## 延伸閱讀

### 官方指南

- **[Open Source Guides: Best Practices — Tame your workload / Issue triage](https://opensource.guide/best-practices/#tame-your-workload)** — GitHub
  - **讀哪裡**:"Tame your workload"、"Use templates and automation" 各節。
  - **和本章的關聯**：triage 與工作量管理的官方權威。

### 工具 / 文章

- **[actions/stale](https://github.com/actions/stale)** — GitHub
  - **讀哪裡**:README 的參數與範例。
  - **和本章的關聯**：stale bot 的官方實作；注意社群對它的爭議討論。

- **[How we triage issues (大型專案的 triage 文件)]** 如 Rust、Kubernetes 的 triage 指南
  - **這些是什麼**:成熟專案公開的 triage 流程與 label 系統。
  - **為什麼值得讀**:學大專案怎麼系統化管理 issue 洪流。

issue 管好了，但維護不只是技術——下一章是維護者最軟、卻最決定專案生死的工作：經營社群、避免 burnout、找幫手。

→ [Ch 31 經營社群](./31-community.md)
