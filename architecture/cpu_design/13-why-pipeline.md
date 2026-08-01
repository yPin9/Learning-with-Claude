# Ch 13 — 為什麼要 pipeline：throughput vs latency

> **目標**：搞懂單週期 CPU 的致命瓶頸——時脈被「最慢那條指令」綁死。你會分清 latency（一條指令從頭到尾多久）和 throughput（單位時間吐幾條指令）這兩件常被混為一談的事，用洗衣店類比建立 pipeline 直覺，看懂指令 vs cycle 的斜階梯時序圖，算出理想 pipeline 的 speedup 上限與為什麼 CPI=1 是理想值，最後理解 pipeline 深度不是越深越好的取捨。這章不寫 RTL，是進 pipeline 前把「為什麼」想透的地基。

我們花了整個 Part 1 做出一顆能跑真程式的單週期 RV32I core。它對、它乾淨、每條指令一個 cycle 搞定。那為什麼真實 CPU 沒有一顆長這樣？因為它**慢**——慢在一個你可能沒細想過的地方。這章把那個地方挖出來。

## 為什麼需要 pipeline？單週期的時脈被最慢指令綁死

單週期 CPU 的規則是：**一條指令，一個 clock cycle 內全部做完**。fetch、decode、讀暫存器、ALU 運算、存取記憶體、寫回——全塞進一個 cycle。

問題來了：clock 週期要設多長？它必須長到**連最慢的那條指令都能在一個 cycle 內走完**。因為 clock 是全晶片統一的節拍，你不能對 `add` 用快節拍、對 `lw` 用慢節拍——同一個 clock，所有指令共用。

我們來估各類指令走完 datapath 要多久（數字是示意，單位任意，重點是相對關係）：

| 指令類型 | 經過的階段 | 大約延遲 |
|---|---|---|
| `add x3,x1,x2`（R-type） | fetch + decode/讀reg + ALU + 寫回 | 200 + 100 + 200 + 100 = 600 |
| `beq`（branch） | fetch + decode/讀reg + ALU比較 + PC更新 | 200 + 100 + 200 + 0 = 500 |
| `sw`（store） | fetch + decode/讀reg + ALU算址 + 寫記憶體 | 200 + 100 + 200 + 250 = 750 |
| `lw x5,0(x1)`（load） | fetch + decode/讀reg + ALU算址 + 讀記憶體 + 寫回 | 200 + 100 + 200 + 250 + 100 = 850 |

`lw` 最長：850。所以 clock 週期至少要 850，時脈 = 1/850。即使 `add` 走完自身路徑僅 600，它也得等滿 850 才進下一拍——**多出來的 250 純粹在浪費**。

這就是單週期的死穴：**時脈被 critical path（關鍵路徑，全部指令裡最長那條的組合邏輯延遲）綁死**。你的 CPU 快不快，不取決於平均指令多快，而取決於最慢那條有多慢。而且每條指令都被迫用這個最慢節拍跑，浪費驚人。

pipeline 的核心洞見：**與其讓一條指令霸佔整個長 cycle，不如把 datapath 切成幾段短的，讓多條指令像工廠流水線一樣同時各佔一段。**

## 先建立直覺：洗衣店流水線

想像你經營一間洗衣店，處理一批衣服要四步，每步 30 分鐘：

```
   洗 (Wash) → 烘 (Dry) → 摺 (Fold) → 收 (Store)
    30分        30分        30分        30分
```

**笨方法（單週期式）**：一批衣服洗完、烘完、摺完、收完（120 分鐘），才開始下一批。四批要 4 × 120 = 480 分鐘。洗衣機在烘乾時閒著、烘乾機在摺衣時閒著——同一時間只有一台機器在動，其他三台發呆。

**流水線方法（pipeline）**：第一批進烘乾機的那一刻，第二批就進洗衣機。四台機器同時運轉：

```
     時間 →  30   60   90   120  150  180  210
   批次1:   [洗][烘][摺][收]
   批次2:        [洗][烘][摺][收]
   批次3:             [洗][烘][摺][收]
   批次4:                  [洗][烘][摺][收]
```

- 第一批仍要 120 分鐘走完全程（這叫 **latency**，沒變）。
- 但第一批走完後，**每 30 分鐘就吐出一批**（這叫 **throughput，變快了**）。
- 四批總共只要 120 + 3×30 = 210 分鐘，比 480 快超過一倍。批次越多，越接近「每 30 分鐘一批」的理想速率。

關鍵洞見有兩個：

1. **單一批次沒變快**（latency 不變，甚至因為要在機器間交接可能略增）。
2. **吞吐量大增**（throughput 逼近「一步的時間」而非「全程的時間」）。

CPU pipeline 就是這件事。指令 = 衣服批次，datapath 的每個階段 = 一台洗衣機。**我們不是讓每條指令更快，而是讓多條指令重疊執行，拉高吞吐量。**

## 核心概念：latency vs throughput，別再混為一談

這是全章最該內化的分野：

- **latency（延遲）**：一條指令從發射到完成要多久。單一任務的「總耗時」。
- **throughput（吞吐量）**：單位時間內完成幾條指令。批量任務的「完成速率」。

洗衣店：latency = 120 分（一批的全程），throughput = 每 30 分一批。

CPU：一條指令流過五級仍要五級的時間（latency 沒少，反而因為多了 pipeline register 的 setup 略增）；但因為五條指令重疊，**理想上每個 cycle 就有一條指令完成（retire）**，throughput 逼近「五倍」。

為什麼我們願意犧牲（甚至略增）latency 去換 throughput？因為**程式有成千上萬條指令**。你在乎的是「跑完整支程式多久」，那由 throughput 主導，不是單條指令的 latency。單條快 1 ns 對一億條指令的程式無感；throughput 拉高五倍，整支程式就快五倍。

一句話記住：**pipeline 不縮短單條指令的路，它讓路上同時跑更多指令。**

## 底層機制：把 datapath 切成五段

單週期那條長長的組合邏輯路徑，我們找幾個自然的斷點切開，每段之間插一排暫存器（叫 **pipeline register**，下一章的主角）暫存中間結果。RISC-V 經典切成五級：

```
   IF  →  ID  →  EX  →  MEM  →  WB
  取指   解碼    運算   存取記憶  寫回
        讀暫存器        體
```

- **IF**（Instruction Fetch）：用 PC 從記憶體抓指令，PC += 4。
- **ID**（Instruction Decode）：解碼、讀 register file、產生立即數、算控制訊號。
- **EX**（Execute）：ALU 運算（算術/位址/比較）。
- **MEM**（Memory）：load/store 存取資料記憶體。
- **WB**（Write Back）：把結果寫回 register file。

切開後，每一級的組合邏輯延遲，只涵蓋**那一級**的工作，不再是全程。假設五級大致均衡，每級約 200：clock 週期從單週期的 850 降到約 200（加上 pipeline register 的 setup 開銷，實際約 250）。時脈拉高 3~4 倍。

而且——**五級可以同時各站一條不同的指令**：

```
     cycle→   1    2    3    4    5    6    7    8
   指令1:    IF   ID   EX   MEM  WB
   指令2:         IF   ID   EX   MEM  WB
   指令3:              IF   ID   EX   MEM  WB
   指令4:                   IF   ID   EX   MEM  WB
   指令5:                        IF   ID   EX   MEM  WB
```

這張**斜階梯圖**是理解 pipeline 的核心。橫軸是 cycle，縱軸是指令。看兩個方向：

- **橫著看一條指令**：它花五個 cycle 走完五級（latency = 5 cycle）。
- **豎著看某一個 cycle**：例如 cycle 5，五級同時有 5 條不同指令在跑（指令1 在 WB、指令2 在 MEM、指令3 在 EX、指令4 在 ID、指令5 在 IF）。硬體全開，沒有一級閒著。

從 cycle 5 開始（pipeline 填滿後），**每個 cycle 都有一條指令從 WB 退出**。這就是理想的「每 cycle retire 一條」，也就是 CPI = 1。

## 核心概念：理想 CPI = 1 與 speedup 上限

**CPI**（Cycles Per Instruction，每指令平均週期數）是衡量微架構效率的關鍵指標。

- **單週期**：每條指令 1 個 cycle，CPI = 1。但那個 cycle 超長（850）。
- **多週期**（本課沒做，順帶一提）：每條指令拆成多個短 cycle，CPI > 1（例如平均 4），但 cycle 短。
- **理想 pipeline**：cycle 短（200），且穩態下每 cycle retire 一條，**CPI 也是 1**。

pipeline 的美妙在於：它同時拿到「短 cycle」和「CPI = 1」。單週期 CPI=1 但 cycle 長；pipeline CPI 一樣是 1，但 cycle 短了 3~4 倍。所以整體快 3~4 倍。

**speedup 上限 = pipeline 級數**。直覺：把工作切成 N 段、每段等長、無縫重疊，吞吐量最多變 N 倍。五級 pipeline 理論加速上限是 5 倍。

但這是**理想值**，實際達不到，因為：

1. **級不均衡**：切成五級但每級延遲不等，clock 由最慢那級決定。若某級特別慢，其他級陪它等，加速打折。
2. **填管與排管**：pipeline 前幾個 cycle 在「填滿」（fill）、程式結尾在「排空」（drain），這段沒有滿載。程式越長，這段佔比越小，越接近理想。
3. **pipeline register 開銷**：每級之間插的暫存器有 setup/clk-to-Q 延遲，白吃掉一點時脈預算。切越多級，這開銷佔比越重。
4. **hazard**（危害）：指令之間有相依、有分支，會讓 pipeline 卡住（stall）或做白工（flush）。這是 Ch 15–19 整整五章要對付的敵人，也是實際 CPI > 1 的主因。

實務上五級 pipeline 的 speedup 大概落在 3~4 倍，不是滿分的 5 倍。但這已經是巨大的勝利。

## 核心概念：填管與排管——為什麼短程式吃不到滿速

斜階梯圖的頭尾兩端，是 pipeline 兩個「非滿載」的區間，值得單獨拆開看：

```
     cycle→   1    2    3    4    5    6    7    8    9
   指令1:    IF   ID   EX   MEM  WB
   指令2:         IF   ID   EX   MEM  WB
   指令3:              IF   ID   EX   MEM  WB
   指令4:                   IF   ID   EX   MEM  WB
   指令5:                        IF   ID   EX   MEM  WB
            └──填管(fill)──┘└滿載┘└──排管(drain)──┘
             cycle 1~4        c5   cycle 6~9
             只有部分級在做事       只有部分級在做事
```

- **填管（fill）**：cycle 1~4。第一條指令要走完五級才 retire，這期間 pipeline 逐級被填滿——cycle 1 只有 IF 在做事、cycle 2 有 IF+ID、……要到 cycle 5 五級才全滿。前 4 個 cycle 有級在閒置。
- **穩態（滿載）**：cycle 5 起，五級全滿，每 cycle retire 一條。這才是理想速率。
- **排管（drain）**：程式最後幾條指令陸續離開，後面沒有新指令補進來，pipeline 逐級排空，又有級閒置。

填管固定要 (級數 − 1) = 4 個額外 cycle。所以執行 N 條指令的實際 cycle 數 ≈ **N + 4**（穩態每條一個 cycle，加填管的 4）。

- N = 5（本圖）：5 + 4 = 9 cycle。填管的 4 佔了 44%，加速嚴重打折。
- N = 100：104 cycle。填管佔 4%，接近理想。
- N = 1,000,000：填管佔 0.0004%，可忽略。

**結論**：pipeline 對短程式（幾條指令）幾乎沒好處，甚至因為 latency 增加而略慢；對長程式（真實程式動輒億條指令）才吃得到接近上限的加速。這也呼應 Ch 13 開頭：我們在乎的是海量指令的 throughput，不是單條的 latency。

## 核心概念：hazard——理想與現實的最後一道鴻溝

前面算 speedup 時，我們一直假設「每 cycle 穩穩 retire 一條」。但真實 pipeline 做不到，因為指令彼此有牽連，會讓 pipeline 卡住或做白工。這些牽連叫 **hazard（危害）**，分三類，是 Ch 15–19 的主線，這裡先建立輪廓：

```
   data hazard（資料危害）：
     add x3, x1, x2   ← 算出 x3
     add x4, x3, ...  ← 立刻要用 x3，但 x3 還在前一條的 pipeline 裡沒寫回
     → 後一條讀到舊值，算錯（Ch 15 會親眼看到，Ch 16–17 修）

   control hazard（控制危害）：
     beq x1, x2, L    ← 要跳嗎？EX 級才知道
     ???              ← 但 IF 已經抓了下一條，跳的話這條白抓要作廢
     → branch 讓 pipeline 猜錯路，得 flush（Ch 18 修）

   structural hazard（結構危害）：
     兩條指令同一 cycle 都要用某個硬體（如記憶體），資源不夠
     → 得排隊或加硬體（Ch 19 綜合處理）
```

每種 hazard 都會讓某些 cycle「沒 retire 出指令」（stall 卡管、或 flush 丟掉白跑的指令），實際 CPI 因此 > 1。pipeline 微架構的功力，一大半就在「用最小代價把這三種 hazard 處理掉、讓實際 CPI 盡量逼近 1」。這也是為什麼 Part 2 有整整八章——切五級是骨架，對付 hazard 才是真功夫。本章你只要建立「有這三種敵人在等著」的意識，下一章開始逐一交手。

## 範例 1：算一支程式在兩種 CPU 上的執行時間

假設一支程式有 100 條指令，各類指令延遲用本章開頭那張表。

**單週期 CPU**：
- clock 週期 = critical path = 850（被 `lw` 綁死）。
- 執行時間 = 100 條 × 1 cycle × 850 = **85,000**。

**五級 pipeline CPU**（先假設無 hazard，理想情況）：
- clock 週期 ≈ 最慢一級 + pipeline register 開銷 ≈ 200 + 50 = 250。
- 執行時間 = (100 條 + 4 填管 cycle) × 250 = 104 × 250 = **26,000**。
- 那個「+4」是 pipeline 填滿要的額外 cycle（第一條指令要 5 cycle 才 retire，之後每 cycle 一條）。

speedup = 85,000 / 26,000 ≈ **3.3 倍**。

注意：不是滿分 5 倍。差距來自 (a) clock 週期不是 850/5=170 而是 250（pipeline register 開銷 + 級不均衡），(b) 填管的 +4 cycle（程式短時佔比明顯，長程式可忽略）。把指令數從 100 拉到 100,000，填管的 4 cycle 就微不足道，speedup 更逼近 850/250 = 3.4 倍——由 clock 週期比決定。

## 範例 2：級不均衡如何吃掉加速

假設你把 datapath 切成五級，但延遲分別是 100 / 100 / **400** / 100 / 100（EX 級塞了慢的乘法器）。

clock 週期由**最慢一級**決定 = 400。就算其他四級只要 100，它們每 cycle 也得空等 300。這條 pipeline 的吞吐量 = 每 400 一條，遠不如「每 200 一條」的均衡設計。

**教訓**：pipeline 設計最重要的功夫之一是**平衡各級延遲**。切點要選在讓每級盡量等長的地方。把慢運算（乘除、浮點）獨立出去多週期處理，或再細切，就是為了不讓它拖垮整條 pipeline 的時脈。這也解釋了為什麼「切越多級一定越快」是錯的——見下一節。

## 對比取捨：單週期 vs pipeline vs 更深的 pipeline

| 面向 | 單週期 | 五級 pipeline | 超深 pipeline（如 20 級） |
|---|---|---|---|
| clock 週期 | 長（全程 critical path） | 短（單級延遲 + reg 開銷） | 更短（單級更小） |
| 理想 CPI | 1 | 1 | 1 |
| 實際 CPI | 1 | 略 > 1（hazard） | 明顯 > 1（hazard 代價放大） |
| 單條指令 latency | 1 cycle（但很長） | 5 cycle | 20 cycle |
| 硬體複雜度 | 低 | 中（pipeline reg + hazard 處理） | 高（大量 forwarding、預測） |
| 分支預測錯誤代價 | 無 | 小（丟幾條指令） | 大（丟十幾條指令） |
| 適用 | 教學、極簡場合 | 嵌入式、教學 core 主流 | 高效能桌面/伺服器 CPU |

## 踩雷區

**雷 1：以為 pipeline 讓「每條指令」變快。**
- 錯誤直覺：「pipeline 加速 CPU，所以每條指令跑得更快」。
- 正確認識：pipeline 讓每條指令**變慢**（latency 從單週期的 1 個長 cycle，變成 5 個 cycle 才走完，還多了 pipeline register 開銷）。它加速的是**吞吐量**——多條指令重疊，穩態下每 cycle retire 一條。你在乎整支程式（海量指令）跑多久，那由吞吐量決定，所以整體變快。單條指令的 latency 反而是犧牲品。

**雷 2：以為 speedup 就等於級數。**
- 錯誤直覺：「五級 pipeline 就是快 5 倍」。
- 正確認識：5 倍是**理想上限**，實務打對折到 3~4 倍。四個原因吃掉加速：級不均衡（clock 由最慢級決定，不是平均）、pipeline register 開銷（每級白吃 setup/clk-to-Q）、填管排管（頭尾非滿載）、hazard（相依與分支讓 pipeline 卡住/做白工）。把「級數」當「加速倍數」是最常見的誤算。

**雷 3：以為 pipeline 越深越快，深度可以無限加。**
- 錯誤直覺：「五級快 3 倍，二十級就快十幾倍」。
- 正確認識：深度有甜蜜點。切越深，(a) pipeline register 開銷佔每級比例爆炸（真正做事的時間佔比越低），(b) 一次分支預測錯誤要丟掉的白跑指令越多，hazard 代價放大。Pentium 4 切到 20~31 級衝時脈反被拖垮就是教訓。現代高效能 CPU 落在 14~20 級，不是越深越好。

**雷 4：以為 CPI = 1 代表 pipeline「沒有代價」。**
- 錯誤直覺：「理想 pipeline CPI = 1，和單週期一樣，所以沒損失」。
- 正確認識：CPI = 1 只是**理想值**。真實 pipeline 因為 hazard（Ch 15–19 要對付的 data/control/structural hazard），會有 stall（卡管，某些 cycle 沒 retire）和 flush（丟棄白跑指令），實際 CPI **大於 1**。pipeline 設計的一大半功夫，就是把實際 CPI 壓回接近 1。別把理想 CPI 當成免費午餐。

## 進階：pipeline 深度不是越深越好

既然切五級快 3~4 倍，切二十級是不是快近二十倍？不是。深 pipeline 有反噬：

- **pipeline register 開銷佔比爆炸**：每級之間的暫存器有固定的 setup + clk-to-Q 延遲（假設 50）。切五級時每級 200+50，開銷佔 20%；切二十級每級可能只剩 50+50，開銷佔了一半——你切得越細，真正做事的時間佔比越低，邊際效益急遽遞減。
- **hazard 代價放大**：pipeline 越深，一次分支預測錯誤要丟掉的「已進管但白跑」的指令越多。五級丟幾條，二十級丟十幾條。branch 一多，深 pipeline 的優勢被 flush 代價吃光。這是 Ch 18 control hazard 的核心。
- **功耗與複雜度**：更多暫存器、更多 forwarding 路徑、更兇的分支預測器，面積與功耗都上去。

歷史教訓：Intel Pentium 4（NetBurst 微架構）一度把 pipeline 拉到 20~31 級衝時脈，結果分支預測錯誤代價與功耗失控，後來的 Core 架構反而**退回較淺的 pipeline**。這是「深度取捨」最有名的業界案例。現代高效能 CPU 大多落在 14~20 級的甜蜜點，不是越深越好。

本課做五級——教學最經典、hazard 種類齊全又不至於失控，正是理解 pipeline 一切原理的最佳深度。

## 本章重點整理

- 單週期 CPU 的死穴：clock 週期被 **critical path**（最慢指令的組合延遲，本例 `lw` 的 850）綁死，快指令被迫陪跑，浪費驚人。
- **latency**（單條指令全程耗時）與 **throughput**（單位時間 retire 幾條）是兩回事。pipeline 犧牲一點 latency，換取 throughput 大增——因為程式有海量指令，我們在乎的是 throughput。
- 洗衣店類比：多批衣服重疊處理，單批沒變快，但每 30 分吐一批。CPU pipeline 讓多條指令重疊，穩態下每 cycle retire 一條。
- 五級切分 IF/ID/EX/MEM/WB，clock 週期降到單級延遲 + pipeline register 開銷，時脈拉高 3~4 倍。**斜階梯時序圖**：橫看一條指令走五級，豎看一個 cycle 五級各站一條指令。
- 理想 **CPI = 1**，**speedup 上限 = 級數**（五級最多 5 倍）；實際打折於級不均衡、填管、pipeline register 開銷、hazard。實測約 3~4 倍。
- pipeline 深度有甜蜜點：太深則 register 開銷佔比爆炸、hazard/分支預測錯誤代價放大。Pentium 4 是「切太深反被拖累」的經典教訓。

## 自我檢核

- [ ] 我能解釋為什麼單週期 CPU 的 clock 週期由最慢指令決定，並算出本章那張表裡 clock 週期是多少、由哪條指令綁死。
- [ ] 我能用自己的話說清 latency 和 throughput 的差別，並說明 pipeline 改善的是哪一個、犧牲的是哪一個。
- [ ] 我能畫出五條指令的斜階梯時序圖，並指出某個 cycle（如 cycle 5）五級各站哪條指令。
- [ ] 我能解釋理想 CPI 為什麼是 1、speedup 上限為什麼等於級數，以及實際達不到上限的四個原因。
- [ ] 我能舉出兩個「pipeline 越深不一定越好」的具體理由，並說出 Pentium 4 的教訓。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 4.5 節「An Overview of Pipelining」**：本章的教科書原型。它用「洗衣」同一個類比、同一張斜階梯圖講 pipeline 起手式，並給出 single-cycle vs pipeline 的執行時間計算。讀它把本章的直覺補足量化細節。
- **《Digital Design and Computer Architecture, RISC-V Edition》(Harris & Harris) 第 7.5 節「Pipelined Processor」開頭**：從時序與 critical path 角度切入，對「為什麼 clock 週期由最慢一級決定」講得比 Patterson 更貼近電路，適合你這種想在腦中畫時序的人。
- **《Computer Architecture: A Quantitative Approach》(Hennessy & Patterson) Appendix C.1「Introduction」**：進階版 pipeline 導論，把 CPI、speedup 公式、pipeline hazard 分類講得最嚴謹。想把「speedup 上限」推導清楚、之後做 Ch 23 CPI 分析，這是最扎實的來源。
- **[Sodor 教學 core 的 rv32_1stage 與 rv32_5stage](https://github.com/ucb-bar/riscv-sodor)**：同一顆 RISC-V core 的單週期版與五級版並列。把兩份程式碼對照著讀，你會直接看到「多了哪些 pipeline register、多了哪些 hazard 邏輯」——正是本 Part 接下來要一步步加上去的東西。

概念地基打好了。下一章我們動手把單週期的 datapath 切成五段，插入 pipeline register，並真跑驗證訊號如何逐級傳遞。

→ [Ch 14 IF/ID/EX/MEM/WB 切分與 pipeline register](./14-five-stage-split.md)
