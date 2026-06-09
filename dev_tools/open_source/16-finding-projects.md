# Ch 16 — 找到適合貢獻的專案

> **目標**：解決新手最大的卡點——「我想貢獻，但不知道做什麼專案」。學會找到適合你的專案、判斷專案健康度（值不值得投入）、用 `good first issue` 等標籤找入口、讀懂 CONTRIBUTING，以及一個被低估的真相：最好的第一個專案往往是你自己在用的。

> **環境**：GitHub。本章偏方法與判斷，少 git 操作。

## 為什麼「找專案」是真正的第一道關卡

技術上你已經會了（Part 1-3）：branch、PR、review、CI 都熟了。但很多人卡在更前面——**不知道貢獻什麼**。打開 GitHub 幾千萬個 repo，無從下手；隨便挑一個熱門專案，發現 codebase 太大讀不懂、issue 都太難、或維護者根本不回。

找對專案，貢獻就順；找錯，你會挫折到放棄。這章給你一套「怎麼找」的方法——這不是技術問題，是策略問題，卻決定你能不能踏出第一步。

## 先建立直覺：從你已經在用的東西開始

新手最常見的錯：想著「我要貢獻一個很厲害/很有名的專案」，然後去挑 React、Kubernetes、Linux——結果 codebase 龐大、議題艱深、競爭者眾，根本插不上手。

**最好的起點，是你自己已經在用的工具/函式庫。** 因為：

```
   你已經在用的專案
   ├─ 你懂它在做什麼（不用從零理解）
   ├─ 你遇過它的 bug / 缺的功能（天然的貢獻點！）
   ├─ 你有真實的使用場景（feature request 有說服力）
   └─ 你修好的東西自己會用到（有動力）
```

想一下你最近用過、遇到過小問題的：某個 CLI 工具的 help 訊息有錯字、某個套件文件範例跑不起來、某個 library 缺一個你想要的小功能。**那就是你的第一個貢獻。** 不用找「偉大的專案」，找「你能幫上忙的專案」。

## 用標籤找入口：good first issue

開源社群發明了一套標籤，專門幫新貢獻者找到「適合上手」的任務：

- **`good first issue`**（最重要）：維護者標記的「適合新手的任務」——範圍小、定義清楚、不需要深入 codebase。
- **`help wanted`**：維護者明說「歡迎外部來做」的任務（可能比 good first issue 難一點）。
- **`documentation`**：文件相關，通常技術門檻低、貢獻價值高（文件永遠缺人）。

```bash
# 用 gh 在某專案找新手任務（Ch 15）
gh issue list --repo owner/project --label "good first issue"
gh issue list --repo owner/project --label "help wanted"
```

GitHub 還有跨專案聚合這些標籤的入口：

- **<https://github.com/topics/good-first-issue>**
- GitHub 搜尋：`label:"good first issue" state:open language:python`（依語言/條件篩）
- 第三方：[Good First Issue](https://goodfirstissue.dev/)、[Up For Grabs](https://up-for-grabs.net/)、[First Timers Only](https://www.firsttimersonly.com/)

> 認識論誠實：`good first issue` 不保證真的簡單——有些被標了但其實要懂很多背景，有些早就有人在做（看有沒有 assignee 或留言）。它是「入口」不是「保證」。挑的時候看：有沒有人已認領、討論清不清楚、是不是你能理解的範圍。

## 判斷專案健康度：值不值得投入

挑專案前，花五分鐘評估它的「健康度」——避免把時間投進一個沒人維護、PR 永遠不會被合併的專案。

看這幾個訊號：

```
   健康的專案                       要小心的專案
   ─────────────────              ─────────────────
   ✅ 最近有 commit（幾天/幾週內）   ❌ 半年沒動靜
   ✅ PR 有在被 review/merge         ❌ PR 堆積幾十個沒人理
   ✅ issue 維護者有回應             ❌ issue 石沉大海
   ✅ 有 CONTRIBUTING / 清楚流程     ❌ 沒有貢獻指引
   ✅ 友善的 review 語氣             ❌ 維護者對貢獻者很兇
   ✅ 有 good first issue / 歡迎貢獻  ❌ 明說「不接受外部 PR」
```

怎麼快速查：
- 看 repo 首頁的「最近 commit 時間」、Insights → Pulse（最近活動摘要）。
- 翻幾個最近的 PR：多久被 review？維護者語氣如何？合併率高嗎？
- 看 issue：維護者回不回？有沒有「我們不接受 PR」之類的聲明。

> 一個常見的傷心場景：新手對一個半死不活的專案花一週做了完美的 PR，結果維護者三個月不出現，PR 爛在那。先查健康度，把力氣花在「會回應你」的專案。一個活躍但較小的專案，比一個有名但停滯的專案值得貢獻。

## 讀 CONTRIBUTING：每個專案的規矩

承 Ch 1「很多規矩是專案約定」——每個專案有自己的貢獻流程，寫在 **CONTRIBUTING.md**（或 docs、wiki）。**貢獻前必讀。**

CONTRIBUTING 通常會說：

```markdown
# 典型 CONTRIBUTING 內容
- 怎麼設定開發環境
- 怎麼跑測試（push 前必跑——Ch 14）
- commit message 規範（Conventional Commits？Ch 27）
- branch 命名規範
- PR 要 rebase 還是 merge（Ch 6）
- 要不要簽 CLA / DCO（Ch 21）
- code style（用什麼 formatter/linter）
- review 流程、要幾個 approve
- 行為準則（Code of Conduct）
```

不讀 CONTRIBUTING 就貢獻，常踩雷：commit 格式不對被退、忘了簽 CLA PR 卡住、沒跑 formatter CI 紅、branch 命名不符。**讀它是尊重專案、也是讓你的 PR 順利的捷徑。** 大專案還可能有 `docs/` 下更詳細的開發者指南。

## 從小做起：貢獻的階梯

別一開始就想做大功能。貢獻有個自然的難度階梯，從低往高爬：

```
   1. 文件 / typo / 範例修正    ← 門檻最低，先做這個熟悉流程
   2. 改善錯誤訊息 / 小 UX
   3. 修一個 good first issue（小 bug）
   4. 修一般 bug
   5. 加小功能（先討論）
   6. 加大功能 / 重構（深入後）
   7. 成為常駐貢獻者 / maintainer
```

**第一個 PR 強烈建議從第 1-2 層開始**——目的不是「做大事」，而是**走通流程、和維護者建立第一次互動**。一個被合併的 typo 修正，比一個被無視的大功能 PR 有價值（你得到了「成功貢獻」的經驗和維護者的初步信任）。Ch 18 會深入「第一個 PR」。

> 文件貢獻被嚴重低估：很多人覺得「改文件不算真貢獻」。錯。文件是專案最缺人、最影響使用者的部分，維護者通常超歡迎文件 PR。它技術門檻低但價值高，是完美的入門貢獻。

## 一個完整的「找專案」流程

```
1. 列出你最近用過的 3-5 個開源工具/套件
2. 回想：哪個你遇過小問題（bug、文件錯、缺功能）？
3. 去那個 repo，查健康度（最近 commit、PR 合併率、維護者回應）
4. 讀 CONTRIBUTING——它歡迎貢獻嗎？流程清楚嗎？
5. 翻 issue：
   - 你遇到的問題有人回報過嗎？（沒有→你可以開 issue，Ch 12）
   - 有 good first issue 嗎？挑一個你能理解、沒人在做的
6. 從小做起：先一個 typo/文件/小修，走通流程
7. 成功後，往難度階梯上爬
```

## 踩雷集錦

1. **一開始就挑超大/超有名的專案**：codebase 太大、議題太難、競爭者眾。從你在用的、較小的專案開始。
2. **不查健康度就投入**：對停滯的專案做完美 PR，結果維護者消失，PR 爛掉。先查最近活動、PR 合併率。
3. **不讀 CONTRIBUTING 就發 PR**：commit 格式錯、忘簽 CLA、沒跑 formatter——被退或卡住。必讀。
4. **第一個 PR 就想做大功能**：難、慢、易被拒。先 typo/文件/小修走通流程。
5. **挑已經有人在做的 issue**：看 assignee 和留言，別重複別人的工作（Ch 17 認領）。
6. **覺得文件貢獻「不算」**：文件是高價值、低門檻的完美入門，維護者超歡迎。
7. **挑自己看不懂的 issue**：good first issue 不保證你懂。挑你能理解範圍的，否則做不出來、浪費時間。

## 進階：再往深一層

- **Hacktoberfest 等活動**：每年 10 月的 Hacktoberfest 鼓勵貢獻（但也帶來大量低品質 spam PR，維護者又愛又恨——別為了拿 T-shirt 發垃圾 PR，Ch 20）。
- **追蹤你想貢獻的專案**：watch repo、看它的 roadmap/discussions，了解方向，找到契合你能力的貢獻點。
- **從「修自己遇到的 bug」開始最強**：你能複現、有動機、PR 描述有真實場景——比挑陌生 issue 容易成功。
- **公司在用的開源專案**：你工作上依賴的開源工具，修它的 bug 對你和公司都有益（但注意公司政策與 Ch 21 的授權/CLA）。
- **較新/成長中的專案**：有時比成熟大專案更需要貢獻、維護者更熱情、你的影響更大——但也要平衡健康度（太新可能不穩）。
- **non-code 貢獻**：翻譯、設計、社群答題、教學文章——開源不只是 code。`opensource.guide` 列了很多。

## 動手練習

1. 列出你最近用過的 5 個開源工具/套件，標出你曾遇過小問題的那幾個。
2. 挑其中一個，查它的健康度：最近 commit 時間、Insights → Pulse、最近 5 個 PR 的合併情況與 review 語氣。
3. 讀那個專案的 CONTRIBUTING（沒有的話這本身是個訊號），列出它的關鍵規矩（測試指令、commit 規範、CLA/DCO）。
4. `gh issue list --label "good first issue"`（或上 goodfirstissue.dev）找 3 個你能理解、沒人認領的任務。
5. 在那些 issue 裡找一個「你遇過或能複現」的——這是你練習 D 的候選。
6. 比較兩個專案：一個活躍的小專案 vs 一個有名但 PR 堆積的大專案，說出你會選哪個貢獻、為什麼。

## 本章重點整理

- 找專案是策略問題，不是技術問題——卻是新手真正的第一道關卡。
- **從你已經在用的工具開始**：你懂它、遇過它的問題、有真實場景、有動機。
- 用 `good first issue` / `help wanted` / `documentation` 標籤找入口（gh CLI、goodfirstissue.dev 等聚合站）。
- 貢獻前查**專案健康度**（最近 commit、PR 合併率、維護者回應、語氣）——別對停滯的專案投入。
- **讀 CONTRIBUTING**（必讀）——它是專案的規矩，不讀會踩雷。
- 從難度階梯低端開始（typo/文件/小修），目的是走通流程 + 建立第一次互動，不是做大事。文件貢獻被低估。

## 自我檢核

- [ ] 為什麼「從你已經在用的工具」開始貢獻比「挑有名專案」好？
- [ ] 貢獻前怎麼判斷一個專案的健康度？哪些是警訊？
- [ ] 找新手任務有哪些標籤/管道？good first issue 有什麼侷限？
- [ ] 為什麼第一個 PR 該從小做起、甚至從文件開始？
- [ ] CONTRIBUTING 通常包含什麼？不讀它會踩哪些雷？

## 延伸閱讀

### 官方指南

- **[GitHub Open Source Guides: How to Contribute — Finding a project](https://opensource.guide/how-to-contribute/#finding-a-project-to-contribute-to)** — GitHub
  - **讀哪裡**："Finding a project" 與 "Orienting yourself to a new project" 兩節。
  - **和本章的關聯**：找專案、評估專案的官方完整版。

### 工具 / 站點

- **[goodfirstissue.dev](https://goodfirstissue.dev/)**、**[Up For Grabs](https://up-for-grabs.net/)**、**[First Timers Only](https://www.firsttimersonly.com/)**
  - **這些是什麼**：聚合各專案新手友善 issue 的站點。
  - **和本章的關聯**：找第一個任務的實用入口。

### 部落格 / 文章

- **[How to find your first open source project to contribute to](https://www.freecodecamp.org/news/how-to-contribute-to-open-source/)** 類 freeCodeCamp 指南
  - **這篇說什麼**：找專案、評估、第一步的實務流程。
  - **為什麼值得讀**：補充本章方法的實例（注意挑有作者署名、近年的版本）。

找到專案了，下一章是動手前的功課——複現問題、讀 codebase、認領 issue、處理 CLA/DCO，這些決定你的 PR 是「有備而來」還是「亂槍打鳥」。

→ [Ch 17 貢獻前的功課](./17-before-contributing.md)
