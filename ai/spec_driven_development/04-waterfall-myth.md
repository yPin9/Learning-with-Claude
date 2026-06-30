# Ch 4 — 瀑布的真相：Royce 1970 與一個誤會

> **目標**：搞清楚 Winston Royce 的 1970 年論文到底寫了什麼、「瀑布」這個名字從哪裡來、嚴格瀑布的真實缺陷在哪，以及這段歷史如何鋪墊了迭代開發與 SDD 的出現。

---

## 你心裡的那張圖，不是 Royce 畫的

大多數工程師腦中的「瀑布」長這樣：

```
需求 ──▶ 設計 ──▶ 實作 ──▶ 測試 ──▶ 部署
         ↓           ↓          ↓          ↓
       凍結       凍結        凍結       凍結
```

每一格做完就鎖起來，往下流，不回頭。上游錯了，下游承擔。

這張圖有個廣泛流傳的標籤：「Royce 1970」。

這個標籤是錯的。

準確地說：Royce 的確畫了一個七階段的流程圖，長得有點像瀑布；但他在同一篇論文裡，用一整節告訴讀者，**這樣做法「risky and invites failure」（高風險且招致失敗）**，並且建議至少要跑兩遍。

他從來沒有叫過它「waterfall」。

---

## 1970：Royce 實際說了什麼

Winston W. Royce 在 1970 年 IEEE WESCON 發表的論文標題是 *Managing the Development of Large Software Systems*。他提出七個階段：

```
系統需求 (System Requirements)
    │
軟體需求 (Software Requirements)
    │
分析 (Analysis)
    │
程式設計 (Program Design)
    │
程式撰寫 (Coding)
    │
測試 (Testing)
    │
運行 (Operations)
```

這個圖本身確實是線性往下的。問題是 Royce **立刻在後文補刀**。他說，如果你真的用這個純線性流程跑一個大型專案，這種做法：

> **risky and invites failure**

他的論點是：在測試階段之前，你不可能真正發現設計有多糟。等到測試出問題，你已經在倒數第二步了，回頭修代價極大。

他的解法？**至少跑兩次**（do it at least twice）：先跑一個前期的小循環來驗證關鍵設計，再跑完整的正式循環。換句話說，他在 1970 年就在倡導迭代。

Royce 還有另一個重要建議，也常被忽略：讓客戶或操作者（operator）參與整個過程，而不是只在「需求」和「驗收」兩端現身。他認為，孤立在封閉環境裡產出的設計，最終交到客戶手上必然讓人吃驚——而這種驚喜通常不是好的驚喜。

這兩個建議——多次迭代、讓客戶持續參與——現在讀起來幾乎像是敏捷的預告。差別只在：Royce 是在大型國防/航太專案的脈絡下說這些話，他的「第一次跑」仍然是相對正式的，不是兩週一個 sprint。

> **[史料考據提醒]** 「risky and invites failure」這個措辭被多份二手文獻引用，並由 2025 年 arXiv 的瀑布史研究（arXiv:2510.03894）所支持。但本課程沒有直接對照 Royce 1970 的原始會議論文頁面，若要在學術引用中使用確切原句，建議查找原始文件。

---

## 1976：「瀑布」這個名字從哪裡來

Royce 沒有用過「waterfall」這個詞。

真正讓這個詞廣泛流傳的，是 Bell 與 Thayer 在 1976 年的論文。他們用了「waterfall」來形容那張圖的視覺形狀——水從上往下流，每一層流到下一層。

時間軸：

| 年份 | 事件 |
|------|------|
| 1970 | Royce 發表七階段流程，同時警告單趟跑法有風險 |
| 1976 | Bell & Thayer 首次使用「waterfall」一詞命名這個圖 |
| 1980s | 軟體工程教科書把「瀑布模型」當成標準範本傳開 |
| 1981 | Boehm 的《Software Engineering Economics》讓「越早修越便宜」成為業界口號 |
| 2001 | 敏捷宣言（Agile Manifesto）問世，直接反彈 |

業界把 Bell & Thayer 的「視覺比喻」與 Royce 的「七階段圖」混在一起，再把 Royce 明確否定的「單趟做法」貼回給 Royce，就這樣製造了一個延續五十年的誤會。

---

## 嚴格瀑布的真實問題在哪

誤會歸誤會，嚴格一趟式瀑布的缺陷是真實的，值得正視。

```
需求鎖定
    │   ← 客戶說「我要一個藍色的大按鈕」
    │
設計鎖定
    │   ← 架構師說「好，這裡放一個 512px × 512px 的藍色 div」
    │
實作
    │   ← 工程師照做
    │
測試
    │   ← 客戶：「不對，我要的大按鈕是用來做確認的，
    │           不是放在首頁的！而且顏色是 teal，不是藍色。」
    ▼
回頭重做需求 → 重做設計 → 重做實作 → 重做測試
（此時成本已累積 6 個月）
```

核心問題有三個：

**1. 前期需求的認識論問題（Epistemic Problem）**

人類不擅長在不看到東西之前說清楚自己要什麼。客戶在一份需求文件上簽字，不代表他們真的理解那份文件描述的軟體會長什麼樣。Fred Brooks 在 1986 年的 *No Silver Bullet* 說得更直接：

> The hardest single part of building a software system is deciding precisely what to build... No other part of the work so cripples the resulting system if done wrong. No other part is more difficult to rectify later.

Brooks 把軟體困難分成兩種：**本質困難（Essence）**是規格與設計，天生複雜；**偶發困難（Accident）**是語言工具，可以靠更好的工具解決。純線性瀑布的問題，恰好出在本質困難上：你無法在開工前就完全掌握「要做什麼」。

**2. 回饋延遲**

在嚴格瀑布中，第一次可以「看到軟體實際跑起來」要等到進入測試階段，通常已經過了專案的 70%～80% 時間。在這個時間點發現方向錯誤，代價高得嚇人。

**3. 文件的假確定性**

需求文件寫了五十頁，看起來很完整。但完整 ≠ 正確。文件讓所有人產生一種「這件事搞定了」的感覺，推遲了真正的對話。客戶簽了文件，工程師拿著文件開工，但雙方腦海中的「系統」可能是完全不同的兩個東西。等到演示，才第一次發現這個落差。

還有一個結構性問題：在瀑布的前期，參與需求討論的人（業務分析師、PM、客戶）和後期實際建系統的人（開發者、測試者）不重疊，資訊在交接時不可避免地失真。文件是這個傳遞過程的媒介，而所有媒介都有損耗。

---

## 成本曲線：對的方向，可疑的數字

說到瀑布缺陷，就一定會提 Barry Boehm 的**變更成本曲線（Cost-of-Change Curve）**。

Boehm 在 1981 年的《Software Engineering Economics》中，根據 1970 年代 TRW 和 IBM 的專案資料，描繪了一條曲線：

```
          成本（相對值）
           │
  100x ────┤                                    ●
           │                              ●
   50x ────┤
           │                        ●
   10x ────┤
           │                  ●
    5x ────┤
           │           ●
    1x ────┤     ●
           └────────────────────────────────────
           需求 設計   實作   測試  維運/上線
                        ← SDLC 階段 →
```

「越早修越便宜」這個**方向**是有廣泛實證支持的。常識上也成立：在一張白板上抹掉一個決策，比在一百萬行程式碼裡追蹤這個決策的所有後果，代價差天差地。

**但是「1:100」這個具體數字的出處成謎。**

記者 Tim Anderson 在 The Register 的報導（2021 年）整理了 Laurent Bossavit 與 Hillel Wayne 的調查結果：

- Boehm 的數字最終追溯到 1960 年代末到 1970 年代初的 IBM 內部課程講義
- 常被引用的「IBM Systems Sciences Institute study」——根本找不到這份研究
- Hillel Wayne 的結論：「There's one tiny problem with the IBM Systems Sciences Institute study: it doesn't exist.」
- Wayne 的底線：方向上（後期修比早期修貴）的研究**大致指向同一方向**，但倍率數字無法可靠引用

結論：引用「越早修越便宜」這個方向，沒問題。引用「1:100」或「1:150」這個精確數字，就是在引用無法查證的民間傳說。

> 如果你對這條曲線和它與 SDD 的關係想深究，下一章 [Ch 6 變更成本曲線——以及怎麼誠實引用它](./06-cost-of-change-curve.md) 會完整展開。

---

## CHAOS 數據：同樣需要小心

你可能聽過「只有 16% 的 IT 專案成功」或「需求問題是最大失敗原因」。這些數字通常引自 Standish Group 的 **CHAOS 報告（CHAOS Report）**。

這份報告的方向感——「很多專案出了問題，需求問題是大宗」——大致可信。但具體數字有根本性的方法論問題，由 Eveleens & Verhoef 在 IEEE Software 2010 年發表的 *The Rise and Fall of the Chaos Report Figures* 中提出：

1. 「成功」定義只看工時/成本/功能是否精確符合原始估算，不考慮低估超交付的情況
2. 定義是單向的，導致失敗率系統性高估
3. 原始資料從未公開，無法審計
4. 把不同基數的百分比平均起來，在統計上沒有意義

Jørgensen & Moløkken-Østvold（2006）也提出類似的批評。

結論：把 CHAOS 當成「方向性佐證」可以，但不要用它的具體百分比做任何嚴肅的論證。

---

## 對比：嚴格瀑布 vs. Royce 建議的迭代

| 面向 | 嚴格瀑布（誤會版） | Royce 的本意 |
|------|-------------------|-------------|
| 循環次數 | 一次 | 至少兩次 |
| 前期需求是否凍結 | 是 | 否——第一次跑主要是驗證假設 |
| 回饋時間點 | 測試階段才看到軟體 | 第一次迭代就嘗試跑起來 |
| 誰強調的 | Bell & Thayer 1976 之後的教科書 | Royce 1970 論文本身 |
| 主要失敗模式 | 後期需求變更成本爆炸 | 明顯減輕——因為早暴露問題 |

---

## 踩雷集錦

**錯誤直覺 1：「Royce 發明了瀑布模型」**

正確認識：Royce 畫了一個線性流程圖，但他明確在同一篇文章裡說單趟跑法「risky and invites failure」。「瀑布」這個名字是 Bell & Thayer 1976 年命名的，而那個嚴格的單趟版本是業界誤讀的結果，不是 Royce 的建議。

---

**錯誤直覺 2：「變更成本 1:100 是 Boehm 的嚴謹實證」**

正確認識：Boehm 的**曲線方向**有 1970 年代的業界資料支撐，「越晚越貴」這個方向被廣泛認可。但「1:100」這個精確數字追蹤不到可靠出處，「IBM Systems Sciences Institute study」這份研究找不到原件。引用方向沒問題，引用精確倍率會被較真的人打臉。

---

**錯誤直覺 3：「CHAOS 報告的數字可以直接引用」**

正確認識：Standish CHAOS 報告的「16% 成功率」或「需求是最大失敗原因」背後有方法論問題：成功的定義本身有偏誤、原始資料不公開、統計方式有問題。當成方向性故事講可以，但不要用它的具體百分比支撐嚴肅論證。

---

**錯誤直覺 4：「瀑布是失敗的、敏捷是正確的，時代已經翻頁」**

正確認識：瀑布在需求**真的穩定**的領域（航太、醫療設備、法規合規項目）仍有合理用途，因為可追溯性和嚴格文件在那些場景有硬需求。敏捷解決了需求不穩定的問題，但也付出了代價：意圖只活在人頭裡、沒有持久的規格文件。這個代價在 AI 時代被放大了——LLM 沒辦法讀取工程師的腦袋。

---

**錯誤直覺 5：「Royce 的圖裡完全沒有反向箭頭」**

正確認識：Royce 1970 的圖其實包含了一些反向箭頭，指向相鄰階段的回饋迴圈。他並不是純線性的。是後來的教科書簡化版本移掉了這些箭頭，讓「瀑布」看起來比 Royce 原圖更嚴格。

---

## 進階延伸：為什麼這段歷史對 SDD 重要

嚴格瀑布的失敗，讓 2001 年的敏捷宣言採取了相反策略：**把規格的重要性往下調，把工作軟體的重要性往上調**。「Responding to change over following a plan」。

敏捷宣言四個核心價值中，有兩個直接針對瀑布的症狀：
- **Working software over comprehensive documentation**（打中「文件假確定性」）
- **Customer collaboration over contract negotiation**（打中「前期凍結、後期驚喜」）

這個擺盪是對的——在那個年代。它把「先跑起來，再聊正確性」提升為正當策略，讓業界得以從「五十頁需求文件也照樣翻船」的痛苦中解脫。

但敏捷本身也帶來了一個新的問題，只不過等到 AI 工具出現才被放大。

**敏捷的隱性假設**：人可以持續參與，隨時提供上下文。Product Owner 在旁邊，工程師有問題可以問，意圖可以即時澄清。「工作軟體」作為最高溝通媒介，是因為有人能即時回答「這個行為對嗎」。

這個假設在 AI 代理人（AI agent）的語境下完全不成立。

當你叫 AI 代理人去「實作這個功能」，它沒辦法走到你旁邊問「這個 edge case 要怎麼處理？」它唯一能依靠的，是任務開始時提供的文字上下文。Sprint 看板上「作為用戶，我想要能登入」這行字，對 AI 代理人來說就是全部的規格——連業務規則、約束、不能做什麼都沒有。

Brooks 說的「deciding precisely what to build」是最難的部分——現在「build」這件事可以外包給 LLM，「deciding precisely what」的瓶頸被放大了十倍。

瀑布的失敗是規格太重、太早鎖定；純粹敏捷在 AI 代理人面前的失敗，是規格太輕、太分散、只活在人頭裡。SDD 試圖在這兩個極端之間找到一個定位：**規格夠完整、夠結構化，讓 AI 代理人能理解意圖——但同時夠精簡、夠靈活，讓它不會在開工前就過期。**

這也是為什麼 Böckeler（Thoughtworks，2025）提醒我們，SDD 有重蹈模型驅動開發（Model-Driven Development，MDD）覆轍的風險：MDD 試圖用模型完全代替程式設計師的創意空間，結果發現模型也可以過度複雜、也可以和實作脫節。歷史的諷刺在於：每一次試圖「用一種更高層的制約物取代代碼」的努力，都必須解答同樣的問題——**這個制約物本身如何保持誠實？**

> 如果你對 SDD 為何在 AI 時代重新被重視感興趣，這個脈絡在 [Ch 1 為什麼「規格」突然重要了：AI 把瓶頸推到意圖上](./01-why-specs-matter-now.md) 有完整鋪陳。

---

## 動手練習

這個章節是歷史/概念性的，沒有可以「跑」的代碼，但有一個值得花時間做的思考練習：

**練習：反向工程一個真實失敗**

找一個你親身經歷過（或讀過）的軟體專案失敗案例，試著用本章的框架回答：

1. 這個失敗最像「嚴格瀑布的哪個缺陷」？（前期需求認識論問題、回饋延遲、還是文件的假確定性？）
2. 如果這個專案在「需求鎖定」之前先跑了一個短的驗證循環，哪個問題可以提早暴露？
3. 你能為這個案例估算一個「發現成本」嗎——在哪個階段發現問題、如果在需求階段就發現大概要多少代價？（不用精確數字，估一個數量級就好）

寫下你的答案，大概三到五段。這個練習在後續 [Ch 6](./06-cost-of-change-curve.md) 討論成本曲線時可以拿出來對照。

---

## 本章重點整理

- **Royce 1970** 畫了七階段線性圖，但他本人說單趟跑法「risky and invites failure」，建議至少跑兩次——他是迭代的倡議者，不是嚴格瀑布的發明者。
- **「瀑布」這個名字**來自 Bell & Thayer 1976，不是 Royce。業界把 Bell & Thayer 的命名和 Royce 明確否定的做法綁在一起，製造了五十年的誤會。
- **嚴格瀑布的真實缺陷**：需求的認識論問題（人不擅長事先說清楚自己要什麼）、回饋延遲（測試階段才看到軟體）、文件假確定性。
- **Boehm 成本曲線**：方向可信，具體倍率數字（1:100）的出處成謎，不要精確引用。
- **CHAOS 報告**：方向性參考可以，具體百分比有方法論問題，不要當嚴肅論證的依據。
- **這段歷史的啟示**：敏捷擺盪把規格的地位壓低；AI 讓代碼變便宜後，這個擺盪產生了副作用——「deciding what to build」的成本比例更高了，這是 SDD 重新被重視的起點。

---

## 自我檢核

- [ ] 用自己的話解釋：Royce 1970 的圖和「嚴格瀑布」到底有什麼關係、差在哪？如果被問到，你的答案不能只是「他沒有發明瀑布」，要說出他實際建議的是什麼。
- [ ] 「waterfall」這個詞最早是誰用的、在哪一年？
- [ ] Boehm 成本曲線：你可以合理引用的是什麼，不可以合理引用的是什麼？把這個區別用一句話說清楚。
- [ ] 面試時有人問「為什麼不用瀑布？」——你的回答要包含哪三個具體缺陷？
- [ ] Brooks 的「essence vs accident」和瀑布失敗有什麼關係？能不能在不翻書的情況下連成一條線？
- [ ] CHAOS 報告：你在什麼情況下可以引用它，又在什麼情況下不應該用它的數字？

---

## 延伸閱讀

1. **A Brief History of the Waterfall Model: Past, Present, and Future**（arXiv:2510.03894v3，2025）
   - https://arxiv.org/html/2510.03894v3
   - 從哪裡讀：從 "Royce's 1970 Formalization" 那一節開始。
   - 學什麼：第一手考據，說明 Royce 如何在同一篇論文裡警告單趟瀑布的風險，以及 Bell & Thayer 1976 命名的脈絡。本章歷史部分最重要的一級來源。

2. **No Silver Bullet — Essence and Accident in Software Engineering**，Frederick P. Brooks Jr.（1986 IFIP，重刊於 IEEE Computer 1987 年 4 月）
   - https://www.cin.ufpe.br/~phmb/ip/MaterialDeEnsino/BrooksNoSilverBullet.html
   - 從哪裡讀：從 "Essence" 小節開始，讀到 "Past Breakthroughs Solved Accidental Difficulties"。
   - 學什麼：「deciding precisely what to build」是最難也最不可補救的部分；essence vs accident 的完整論述。這是理解為何規格困難的最重要短文之一。

3. **Everyone cites that 'bugs are 100x more expensive to fix in production' research, but the study might not even exist**，Tim Anderson，The Register，2021 年 7 月 22 日
   - https://www.theregister.com/2021/07/22/bugs_expense_bs/
   - 從哪裡讀：從 Hillel Wayne 的引述開始，再回頭看 Bossavit 的追蹤過程。
   - 學什麼：1:100 這個數字的出處有多可疑，以及如何在不放棄曲線方向的情況下誠實引用它。和本章「成本曲線」那一節直接對應。

4. **The Rise and Fall of the Chaos Report Figures**，J. Laurenz Eveleens & Chris Verhoef，IEEE Software vol. 27，Jan/Feb 2010，pp. 30-36
   - https://www.cs.vu.nl/~x/the_rise_and_fall_of_the_chaos_report_figures.pdf
   - 從哪裡讀：摘要與第一節就足夠，四個「問題」是核心內容。
   - 學什麼：CHAOS 報告的定義偏誤、數據不透明問題，以及為什麼這些數字無法用於嚴肅論證。是「引用 CHAOS 前要先讀這篇」的論文。

5. **Manifesto for Agile Software Development**，Beck et al.（2001）
   - https://agilemanifesto.org/
   - 從哪裡讀：先讀四個 values，再讀 "twelve principles" 連結頁。
   - 學什麼：敏捷宣言的完整原文與那句常被漏掉的補充（「there is value in the items on the right」）。本章說「敏捷是對嚴格瀑布的擺盪」，這是原始文件。

---

→ [Ch 5 迭代與敏捷：用快速回饋換掉大份前期規格](./05-iterative-agile.md)
