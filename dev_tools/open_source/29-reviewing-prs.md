# Ch 29 — 審 PR（審人方）

> **目標**：學會當 reviewer——怎麼給建設性的 code review、審查時看什麼、怎麼用 approve / request changes / comment、怎麼禮貌但堅定地拒絕、以及怎麼審外部貢獻者的 PR（含安全考量）。這是維護者最高頻的工作。會被審（Ch 13）和會審人是兩套技能。

> **環境**：GitHub、`gh` CLI。前置：Ch 13（被審方）、Ch 19（迭代）、Ch 28（維護者心態）。

## 為什麼審人是一門獨立技能

你會被審了（Ch 13），但**審別人是完全不同的技能**。當 reviewer，你要：在有限時間內判斷一個你沒寫的改動好不好、給出有用又不傷人的回饋、決定接受還是拒絕、還要兼顧教育新貢獻者。

審不好的後果很實際：太鬆 → 爛 code 進專案、技術債累積；太嚴/太兇 → 嚇跑貢獻者、社群變有毒；太慢 → PR 堆積、貢獻者失望離開。會審 PR，是維護者最核心、最高頻的技能。

## 先建立直覺：review 的雙重目的

好的 code review 同時做兩件事：

```
   1. 把關品質       → 確保進專案的 code 夠好（正確、可維護、符合方向）
   2. 培養貢獻者     → 教育、鼓勵，讓他下次更好、願意繼續貢獻

   差的 review 只想到 1（把關），忘了 2（人）：
   → code 是擋下來了，但貢獻者被打擊、不再來
```

最好的維護者把每次 review 當成「既把關品質、又投資一個未來的長期貢獻者」。尤其對新人——一次溫暖、有教導性的 review，可能造就一個長期貢獻者；一次冷酷、挑剔的 review，永遠趕走一個人。**review 是技術行為，更是社群行為。**

## 審查時看什麼（優先序）

reviewer 容易陷入「逐行挑剔小毛病」，但其實該按優先序看：

```
   優先序（由高到低）：
   1. 正確性     → 它真的有效嗎？有 bug 嗎？邊界情況？
   2. 設計       → 方法對嗎？符合專案架構嗎？有更簡單的解嗎？
   3. 可維護性   → 別人（含未來的你）看得懂、改得動嗎？
   4. 測試       → 有測試嗎？涵蓋夠嗎？
   5. 符合方向   → 這該進專案嗎？（scope，Ch 28）
   6. 風格/小事   → 命名、格式（這些該自動化，Ch 27，別人肉審）
```

**重點：別把時間花在第 6 項。** 風格、格式、命名這些小事應該由 linter/formatter 自動處理（Ch 27），reviewer 不該當人肉 linter。把寶貴的 review 注意力放在高優先的「正確性、設計、可維護性」——這些機器抓不到、只有人能判斷。新手 reviewer 最常見的錯就是糾結縮排和命名，卻漏看設計問題。

> 「nit:」前綴：對真的想提的小事（非阻擋性的小建議），用 `nit:` 前綴（nitpick）標明「這是小事、不強求」——讓貢獻者知道哪些必改、哪些只是建議。例：「nit: maybe rename `x` to `count`?」

## 怎麼給建設性的回饋

review comment 的措辭，決定它是「幫助」還是「打擊」。承 Ch 20 的溝通原則，reviewer 端尤其要注意：

```
   傷人 / 無建設性                    建設性
   ─────────────                    ─────────
   "This is wrong."                 "This will fail when X is empty — 
                                     consider adding a check."
   "Why didn't you use Y?"          "Have you considered Y? It might
                                     handle the Z case more cleanly."
   "Bad code."                       "I think we can simplify this — 
                                     what if we ...?"
   "Obviously this should be..."     （刪掉「obviously」）
```

原則：

- **對事不對人**：批評 code，不批評人。「這段 code 有個問題」不是「你寫錯了」。
- **解釋為什麼**：不只說「改這個」，說「為什麼」——讓貢獻者學到東西、也讓他能反駁（也許你錯了）。
- **用問句 / 建議句**：「能不能考慮 X?」比「用 X」好——給對話空間、尊重對方。
- **具體 + 可行動**：「這裡有問題」不如「這裡 X 情況會 fail，加個檢查」。
- **肯定好的部分**：看到寫得好的地方說一句「Nice solution here!」——review 不只是挑錯，鼓勵讓人願意繼續。
- **區分必改與建議**：用 `nit:` / "blocking:" 標明哪些是阻擋性的、哪些只是想法。

## approve / request changes / comment 怎麼用

承 Ch 13 的三種表態，從 reviewer 端：

```
   ✅ Approve         → 我認可，可以合（達到品質標準）
   🔴 Request changes → 有阻擋性問題，改了才能合
   💬 Comment         → 有想法/問題，但不阻擋（讓作者斟酌或回答）
```

用法判斷：

- **Approve**：正確、設計 OK、測試夠——即使有 nit（小建議），主要問題都沒了就 approve（別為小事卡住）。
- **Request changes**：有**阻擋性**問題（bug、設計缺陷、缺測試、不符方向）——明確說明哪些必須改。
- **Comment**：你只是有疑問、或非阻擋的建議、或你不是這部分的主審——不表態擋不擋。

> 別濫用 request changes：對只有 nit 的 PR 用 request changes 會卡住作者（多數專案 request changes 擋 merge，Ch 23）。如果只是小建議，用 comment + approve（「approve，但建議考慮 X」）。把 request changes 留給真正該擋的。

## 怎麼拒絕一個 PR

承 Ch 28「優雅地說不」——這是維護者最難的。拒絕一個 PR（不是要求修改，是根本不接受）：

```
   常見拒絕原因：
   - 不符專案方向/scope（即使 code 很好）
   - 增加太多複雜度/維護負擔
   - 已有更好的解法 / 重複功能
   - 該用別的方式（不是 PR 能改的，是根本方向問題）
```

怎麼拒絕得體：

```
   差："We don't want this. Closing."   ← 冷酷、不解釋、傷人

   好："Thanks for taking the time to work on this! However, this
       falls outside the project's scope — we're keeping X focused on
       Y, and Z would add significant maintenance burden. I really
       appreciate the effort, and hope you'll consider contributing
       to [other area] where we'd love help. Closing this PR."
```

要素：**感謝付出 + 解釋原因 + 可能的話指引別的方向 + 尊重的結尾**。貢獻者花了時間，被拒已經失望，至少讓他覺得被尊重、學到原因、不被趕跑（也許下個 PR 就合適）。一個被好好拒絕的人,可能還會再來；一個被冷酷對待的人,永遠不來。

> 越早拒絕越好：如果一個 PR 的方向注定不被接受，**盡早說**（別讓對方一直改、來回十輪才說「其實我們不要這個」——那是最浪費、最傷人的）。這也是為什麼大改動該「先開 issue 討論方向」（Ch 17/18）——讓維護者能在動手前就說不。

## 審外部貢獻者的 PR（含安全）

審「陌生人」的 PR 和審同事不同——多了信任與安全考量：

- **CI 安全**（Ch 14/34）：外部 PR 的 CI 在你的 repo 跑。惡意 PR 可能想偷 secret、濫用資源——GitHub 的 fork PR 保護（需 approve 才跑 CI、拿不到 secret）就是為此。審外部 PR 前先看 code，再決定要不要跑 CI。
- **惡意 code 警覺**：外部貢獻可能藏惡意（後門、偷資料、供應鏈攻擊）。審時注意：奇怪的網路請求、obfuscated code、動到 CI/build/依賴的可疑改動、`eval` 之類。`.github/workflows` 和依賴的改動要特別謹慎審。
- **AI slop 警覺**（Ch 20）：大量 AI 生成的低品質 PR。審時判斷「貢獻者是否真的理解他的 PR」——問幾個問題就知道。對明顯沒理解、沒測試的 AI slop，禮貌但明確地處理（要求說明/測試，或拒絕）。
- **新人友善**：第一次貢獻者的 PR 多一點耐心和教導——他的第一次體驗決定他會不會繼續（培養未來貢獻者）。

## review 的時效

維護者常被詬病「PR 沒人理」。雖然你無償、忙，但 review 時效影響貢獻者體驗巨大：

- **盡快給「第一個回應」**：即使沒空細審，先說一句「Thanks, I'll review this soon」——讓對方知道沒被無視。
- **設定預期**：忙的話說明「我這週很忙，下週看」——比沉默好。
- **別讓 PR 爛掉**：堆積的 PR 是社群健康的警訊（也是 Ch 16 貢獻者評估健康度看的）。
- **善用工具**：`gh pr checkout`（Ch 15）拉下來本地測、batch review 一次處理多個。

> 但也別過度自責：你無償維護，沒義務即時回應(Ch 28)。設界限、批次處理、找 co-maintainer 分擔(Ch 31)——可持續比即時更重要。

## 一個完整的 review 流程

```bash
# 1. 拉下來本地看/測（Ch 15）
gh pr checkout 123
# 跑測試、實際試功能

# 2. 在 GitHub 上 review，按優先序看：
#    - 正確性、設計、可維護性、測試（高優先）
#    - 風格小事交給 linter，別人肉審（用 nit: 標非阻擋小建議）

# 3. 給建設性回饋：
#    - 對事不對人、解釋為什麼、用問句、肯定好的部分
#    - 區分 blocking（request changes）vs nit（comment）

# 4. 表態：
gh pr review 123 --approve                        # 夠好了
gh pr review 123 --request-changes --body "..."   # 有阻擋問題
gh pr review 123 --comment --body "..."           # 疑問/非阻擋建議

# 5. 來回（Ch 19 的另一端）：作者改了，re-review

# 6. 達標 → merge（squash/merge/rebase 依專案，Ch 10）
gh pr merge 123 --squash --delete-branch
```

## 踩雷集錦

1. **當人肉 linter（糾結風格/格式）**：小事該自動化（Ch 27）。把注意力放在正確性、設計、可維護性。用 `nit:` 標小建議。
2. **review 傷人（對人不對事）**："This is wrong/bad" → 對 code 不對人、解釋為什麼、用問句。
3. **濫用 request changes**：只有 nit 也擋 merge，卡住作者。小建議用 comment + approve。
4. **拒絕得冷酷**："Closing. We don't want this." → 感謝 + 解釋 + 指引 + 尊重。越早拒越好（別來回十輪才說不要）。
5. **只挑錯不鼓勵**：review 全是批評，貢獻者被打擊。肯定好的部分，尤其對新人。
6. **忽略外部 PR 的安全**：CI 偷 secret、惡意 code、可疑的 workflow/依賴改動——審外部 PR 要警覺（Ch 34）。
7. **PR 沒回應爛掉**：即使沒空細審，先給第一個回應/設定預期。堆積的 PR 傷社群健康。
8. **完美主義卡住 PR**：追求完美讓 PR 永遠合不了。「夠好且改進現狀」就該 approve——別讓完美是夠好的敵人。

## 進階：再往深一層

- **review 的「夠好」原則**：Google 的準則——「approve 當 PR 確實改善了 codebase 的整體健康，即使不完美」。別追求完美卡住合理的改進。
- **review latency 與貢獻者留存**：研究顯示 review 越快，貢獻者越可能繼續貢獻。時效是社群健康的關鍵指標。
- **教育性 review**：對新人，多解釋「為什麼」、附文件連結、甚至直接用 GitHub suggestion 給範例 code——把 review 當教學機會。
- **review 自己沒把握的部分**：不是你領域的 PR，找對的人（CODEOWNERS，Ch 24）或說「我對 X 不熟，@someone 能看看嗎」——別硬審你不懂的。
- **批次 review 與 review 預算**：維護者設定「每天花 X 時間 review」，批次處理，避免被 PR 通知綁架（時間管理，Ch 28/31）。
- **review bot / 自動化輔助**：CI 跑 lint/test/coverage 先過濾（Ch 14/27），讓人只審機器抓不到的——放大維護者的有限時間。
- **共識決策**：大改動可能需要多個維護者討論（不是一個人說了算），尤其有爭議的方向（Ch 28 的治理）。

## 動手練習

1. 用練習 C/E 的測試 repo（或找一個真實 PR），練習 review：按優先序看、寫 3 條建設性 comment（對事不對人、解釋為什麼、用問句）。
2. 練習區分：哪些該 request changes（阻擋）、哪些用 `nit:` + comment（建議）——對同一個 PR 標出來。
3. 練習寫一個「優雅拒絕」的回應：一個 code 寫得好但不符 scope 的 PR，感謝+解釋+指引+尊重。
4. 找一個真實開源專案，看維護者怎麼 review（語氣、優先序、怎麼拒絕）——學好的 reviewer 範例。再找一個「兇/糟糕」的 review，分析它怎麼傷人。
5. `gh pr checkout` 一個真實 PR 到本地，跑跑看——體驗「拉下來測再 review」。
6. 反思：你（練習 D）當貢獻者被 review 時的感受，怎麼讓你當 reviewer 時對別人更好？

## 本章重點整理

- 審人和被審是兩套技能；review 有雙重目的：把關品質 + 培養貢獻者（別只想到把關、忘了人）。
- 審查優先序：正確性 > 設計 > 可維護性 > 測試 > 方向 > 風格小事；**小事交給 linter，別當人肉 linter**（用 `nit:` 標非阻擋建議）。
- 建設性回饋：對事不對人、解釋為什麼、用問句、肯定好的、區分 blocking vs nit。
- approve（夠好就批，別為小事卡）/ request changes（真阻擋才用）/ comment（疑問/非阻擋）。
- 優雅拒絕：感謝 + 解釋 + 指引 + 尊重；越早拒越好（別來回十輪才說不要）。
- 審外部 PR 多一層信任與安全考量（CI secret、惡意 code、AI slop、新人友善）；review 時效影響貢獻者留存。

## 自我檢核

- [ ] review 的雙重目的是什麼？只想到「把關」會怎樣？
- [ ] 審查該按什麼優先序？為什麼不該當人肉 linter？
- [ ] 建設性 comment 和傷人 comment 差在哪？舉例。
- [ ] approve / request changes / comment 各什麼時候用？為什麼別濫用 request changes？
- [ ] 怎麼優雅地拒絕一個 PR？為什麼「越早拒越好」？
- [ ] 審外部貢獻者的 PR 要多注意什麼（安全、AI slop、新人）？

## 延伸閱讀

### 部落格 / 指南

- **[Google Engineering Practices: How to do a code review](https://google.github.io/eng-practices/review/reviewer/)** — Google
  - **讀哪裡**:整個 reviewer guide——優先序、「夠好」原則、怎麼寫 comment、speed of reviews。
  - **為什麼值得讀**：code review（reviewer 端）最系統化的權威，本章的深度版。

- **[How to Do Code Reviews Like a Human](https://mtlynch.io/human-code-reviews-1/)** — Michael Lynch
  - **這篇說什麼**：怎麼給有人性、建設性、不傷人的 review。
  - **讀哪裡**:兩部分都讀。
  - **為什麼值得讀**：本章「建設性回饋」與「培養貢獻者」的最佳實踐。

### 官方指南

- **[Open Source Guides: Best Practices — Learning to say no](https://opensource.guide/best-practices/#learning-to-say-no)** — GitHub
  - **讀哪裡**:"Learning to say no" 那節。
  - **和本章的關聯**：優雅拒絕的官方建議。

審 PR 是回應「別人送來的東西」，下一章是維護者主動管理待辦的工作——issue triage：分類、優先序、關閉的藝術。

→ [Ch 30 Issue triage](./30-issue-triage.md)
