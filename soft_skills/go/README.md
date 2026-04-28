# 圍棋學習筆記：從零到業餘高段

> 給完全沒下過、或下過幾盤但都忘了的成年人。目標：用一套有系統的教材 + 對局訓練，走到業餘 1-3 段（強業餘），再往上靠時間累積。

## 為什麼學這個？

- **腦力遊戲的天花板**：圍棋的搜尋空間比西洋棋大幾十個數量級，AlphaGo 之後人類仍在重新理解這遊戲
- **訓練長線思考**：一盤 19x19 平均 200+ 手，每一手都要算「現在 vs 全局 vs 30 手後」
- **沒有捷徑反而是優點**：你必須真的下、真的算、真的死活，沒有速成。學的是耐心
- **AI 時代學棋的甜蜜點**：KataGo 免費開源、強過任何人類冠軍、能即時告訴你哪步壞 — 過去 30 年沒人有這個資源

## 一個必須先講清楚的事

**沒有任何教材能讓你「讀完就成業餘 5 段」**。業餘高段需要：

- 死活練到反射動作（每天 30-50 題，持續半年起跳）
- 對局量（3000+ 盤是入門級數字）
- 真人對手 + KataGo 復盤的循環
- 3-5 年累積

這套教材給你的是**完整知識架構** + **正確的訓練方法**。你照做、再撐 1-2 年，業餘 1-3 段是合理目標。再往上是時間問題，不是教材問題。**別讀完課程沒下棋就以為自己會了**。

## 課程地圖

### Part 1 — 基礎
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 圍棋全貌與段級制度](./01-go-overview.md)
- [Ch 2 規則完整版](./02-rules-complete.md)

### Part 2 — 9x9 入門
- [Ch 3 為什麼從 9x9 開始：兩眼活棋](./03-why-9x9-and-two-eyes.md)
- [Ch 4 9x9 戰術：切斷與攻擊](./04-9x9-tactics.md)
- [Ch 5 9x9 對局實戰](./05-9x9-practical.md)
- [練習 A：9x9 自戰 10 盤 + KataGo 復盤](./practice-a-9x9-games.md)

### Part 3 — 13x13 過渡
- [Ch 6 13x13 的策略差異](./06-13x13-strategy.md)
- [Ch 7 連接與切斷](./07-connection-and-cutting.md)
- [Ch 8 厚實 vs 實地](./08-thickness-vs-territory.md)

### Part 4 — 死活
- [Ch 9 死活基礎：兩眼必活、大眼活棋](./09-life-and-death-basics.md)
- [Ch 10 死活基本型](./10-life-death-shapes.md)
- [Ch 11 角部死活](./11-corner-life-death.md)
- [Ch 12 邊上死活](./12-side-life-death.md)
- [Ch 13 死活訓練方法與資源](./13-life-death-training.md)
- [練習 B：100 道死活集中訓練](./practice-b-100-life-death.md)

### Part 5 — 手筋
- [Ch 14 手筋是什麼](./14-tesuji-intro.md)
- [Ch 15 基本手筋：撲、倒撲、滾打、接不歸](./15-basic-tesuji.md)
- [Ch 16 切斷與連接手筋](./16-cutting-connecting-tesuji.md)
- [Ch 17 攻擊與收氣手筋](./17-attack-liberty-tesuji.md)
- [練習 C：100 道手筋題](./practice-c-100-tesuji.md)

### Part 6 — 形
- [Ch 18 好形 vs 愚形](./18-good-shape-bad-shape.md)
- [Ch 19 厚味、外勢、模樣](./19-influence-and-moyo.md)
- [Ch 20 棄子的藝術](./20-sacrifice.md)

### Part 7 — 佈局
- [Ch 21 佈局原則](./21-fuseki-principles.md)
- [Ch 22 角的選擇：星位 / 小目 / 三三 / 高目](./22-corner-choices.md)
- [Ch 23 經典佈局速覽](./23-classical-fuseki.md)
- [Ch 24 AI 時代佈局](./24-ai-era-fuseki.md)

### Part 8 — 中盤
- [Ch 25 中盤思考順序](./25-middle-game-thinking.md)
- [Ch 26 攻擊的真意](./26-attack-true-meaning.md)
- [Ch 27 治孤、入侵、淺消](./27-invasion-reduction.md)
- [Ch 28 戰鬥技巧：對殺與雙活](./28-fighting-techniques.md)
- [Ch 29 形勢判斷](./29-positional-judgment.md)
- [練習 D：中盤判斷題 + 對局復盤](./practice-d-middle-game.md)

### Part 9 — 收官
- [Ch 30 收官的價值計算](./30-yose-value.md)
- [Ch 31 先手、後手、逆官子](./31-sente-gote-reverse.md)
- [Ch 32 數目方法：中國 vs 日本 vs Tromp-Taylor](./32-counting-and-rules.md)

### Part 10 — AI 時代的學棋方法
- [Ch 33 KataGo 完整指南](./33-katago-guide.md)
- [Ch 34 用 AI 復盤的正確方法](./34-ai-review-method.md)
- [Ch 35 AI 推薦 vs 人類習慣](./35-ai-vs-human.md)
- [練習 E：自戰 5 盤完整 KataGo 復盤](./practice-e-katago-deep-review.md)

### Part 11 — 棋風與名局
- [Ch 36 古今名手與棋風流派](./36-masters-and-styles.md)
- [Ch 37 名局欣賞](./37-famous-games.md)

### Final Project
- [Final Project：50 盤升段挑戰](./final-project-50-games-rank-up.md)

## 學習方式建議

1. **每天 30 題死活**，比讀任何理論章節都有效。死活弱 = 上不去
2. **下棋比讀書重要**：每讀一章，下 2 盤實戰、用 KataGo 復盤至少 1 盤
3. **不要背定石**：理解原則 + 跟 KataGo 對 → 比背 200 個定石強
4. **故意輸 100 盤**：剛起步時連輸是常態，每盤輸都看 KataGo 找 1 個關鍵錯
5. **段位提升慢是正常的**：圍棋升 1 級需要的對局量比西洋棋升 100 ELO 還多

## 參考資料

- 《圍棋發陽論》— 古典死活題集，必啃
- 《玄玄棋經》— 元代死活題，比發陽論早
- 《官子譜》— 收官題集
- 《吳清源回憶錄：以文會友》— 棋手心法
- KataGo: https://github.com/lightvector/KataGo
- KaTrain: https://github.com/sanderland/katrain（最人性的 KataGo 前端）
- Sensei's Library: https://senseis.xmp.net（圍棋維基，英文）
- Goproblems: https://www.goproblems.com（線上死活題）
- OGS / 野狐 / KGS / Tygem / Fox — 主流對局平台
