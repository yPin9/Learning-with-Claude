# 西洋棋學習筆記：從零到穩定下 Rapid

> 給會下一點棋但升不動、或完全沒實戰過的人。

這系列不陪你背開局，也不讓你去研究 GM 棋譜。新手真正會卡的就三件事：**戰術 pattern 要一眼看到**、**基本殘局要下得出來**、**中局要知道要做什麼**。我們用 Lichess puzzle 練 pattern，用 ASCII 棋盤 + PGN 講局面，最後要你真的下 5 盤 Rapid 並自己復盤。開局只講原則加一白一黑，不背主變化。

## 為什麼學這個？

- **升分瓶頸是 pattern，不是理論**：Lichess 1400 以下的輸棋九成是沒看到一個 fork 或 pin，不是開局劣勢。先練 pattern recognition，分數就會動。
- **殘局是第二個鴻溝**：多數業餘到殘局就亂走。K+R vs K、王兵 opposition、Lucena/Philidor — 這幾個會了，你已經贏過大多數同級。
- **想 plan 才是成年人下棋**：從「走一步看一步」升級到「這盤我這方有什麼優勢、我要達成什麼」— 這是從新手變中階的分水嶺。

## 課程地圖

### Part 0 — 準備與基本功
- [Ch 0 環境搭建與工具](./00-environment-setup.md)
- [Ch 1 規則全面確認](./01-rules-refresh.md)
- [Ch 2 子力價值與交換邏輯](./02-piece-values.md)

### Part 1 — 戰術模式識別
- [Ch 3 Fork（雙擊）](./03-fork.md)
- [Ch 4 Pin（牽制）](./04-pin.md)
- [Ch 5 Skewer 與 Discovered Attack](./05-skewer-discovered.md)
- [Ch 6 Deflection 與 Overloaded Piece](./06-deflection-overload.md)
- [Ch 7 Back Rank 與 Smothered Mate](./07-back-rank-smothered.md)
- [Ch 8 核心將殺圖案](./08-mating-patterns.md)
- [Ch 9 Combinations 多步戰術](./09-combinations.md)
- [練習 A：戰術 50 題](./practice-a-tactics-50.md)

### Part 2 — 殘局基本功
- [Ch 10 K+Q vs K](./10-kq-vs-k.md)
- [Ch 11 K+R vs K](./11-kr-vs-k.md)
- [Ch 12 王兵殘局：Opposition](./12-kp-vs-k-opposition.md)
- [Ch 13 Square Rule 與通路兵](./13-square-rule-promotion.md)
- [Ch 14 車兵殘局：Lucena 與 Philidor](./14-rook-endgames-basics.md)
- [Ch 15 殘局過渡思路](./15-endgame-transition.md)
- [練習 B：殘局 50 題](./practice-b-endgame-50.md)

### Part 3 — 局面要素與判斷
- [Ch 16 活動度與子力協調](./16-activity-coordination.md)
- [Ch 17 兵形結構](./17-pawn-structure.md)
- [Ch 18 弱格、強格、雙象優勢](./18-weak-squares-outpost.md)
- [Ch 19 開放線與第七排](./19-open-files-seventh-rank.md)
- [Ch 20 王的安全與攻擊](./20-king-safety-attack.md)
- [練習 C：局面判斷 10 題](./practice-c-position-assessment.md)

### Part 4 — 開局原則
- [Ch 21 開局三大原則與新手陷阱](./21-opening-principles.md)
- [Ch 22 白方：Italian Game](./22-italian-game.md)
- [Ch 23 黑方：對 1.e4 / 1.d4 的應對](./23-black-defenses.md)

### Part 5 — 實戰整合
- [Ch 24 思考流程](./24-thinking-process.md)
- [Ch 25 時間管理與心理坑](./25-time-management-psychology.md)
- [Ch 26 復盤方法](./26-analysis-review.md)
- [Final Project：5 盤 Rapid 復盤報告](./final-project-five-rapid-games.md)

## 學習方式建議

1. **每天 10 題 puzzle 是底線**：戰術 pattern 要靠量累積，讀 10 章文字不如做 300 題 puzzle。Lichess puzzle 免費無限，沒理由不做。
2. **故意下爛棋看會發生什麼**：學完 pin，下一盤故意讓自己的馬被 pin，感受這有多難受。這比背「要避開 pin」有用 10 倍。
3. **不要盯著引擎評估值**：業餘級 -0.3 和 +0.3 根本沒差。看引擎主變化跟你的走法差在哪，不要看分數。
4. **復盤優先於繼續下**：輸了想立刻 rematch 是陷阱。先 10 分鐘復盤再下下一盤，進步速度差十倍。

## 參考資料

- **書**：
  - 《Bobby Fischer Teaches Chess》— Fischer — 戰術 pattern 入門最經典，題目量夠
  - 《Silman's Complete Endgame Course》— Silman — 殘局按 rating 分級，只讀自己那級
  - 《How to Reassess Your Chess》— Silman — 局面要素思考的代表作
- **網站**：
  - Lichess.org — puzzle、study、無廣告、免費，本課預設平台
  - Chess.com — puzzle 分類更細，免費額度較緊
  - lichess.org/practice — 免費互動教學（內建 opposition、基本將殺訓練）
- **引擎**：Stockfish 16+（Lichess / Chess.com 都內建，也可本地裝）
