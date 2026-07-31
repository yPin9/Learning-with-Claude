# Ch 5 — CPU 微架構速成：pipeline / OoO / ROB / cache

> **目標**：用最少篇幅讓你熟悉現代 CPU 微架構的詞彙和概念——pipeline（管線）、superscalar（超純量）、out-of-order（亂序執行）、ROB（重排序緩衝）、branch predictor（分支預測）、cache 階層。讀完你聽得懂 IPC、branch miss、ROB size、L2 miss、dispatch width 等詞，足以跟硬體團隊對話，也理解「效能數字背後的硬體」。這是讀懂 perf 事件（Ch 6）的前置。

> **環境**：概念章。理解微架構是讀懂效能計數器的基礎。

## 為什麼效能工程師要懂微架構？

效能計數器（Ch 6 的 perf events）會告訴你「IPC 0.3」「L2 miss rate 40%」「branch misprediction 5%」——但這些數字**只有懂微架構才能解讀**。IPC 低為什麼是問題？cache miss 怎麼影響效能？branch miss 為什麼慢？不懂微架構，這些數字就是天書。

而且 compiler 優化的目標就是「讓程式碼更適合微架構」——減少 cache miss（改善 locality）、減少 branch miss（讓分支可預測）、增加指令級平行（讓亂序執行能塞滿管線）。不懂微架構，你無法理解「為什麼這個優化有用」或「該加什麼優化」（Ch 14）。這章用最少篇幅給你微架構的核心概念——不是完整的計算機架構課，而是「效能工程師必須懂的微架構」，讓你能解讀效能數字、跟硬體團隊對話、理解 compiler 優化。

## 先建立直覺:工廠的流水線

```
現代 CPU = 一條高度優化的工廠流水線

  基本 pipeline（管線）：把指令執行分成階段
    取指(fetch) → 解碼(decode) → 執行(execute) → 寫回(writeback)
    像工廠流水線：每個階段同時處理不同的指令
    → 多條指令「重疊」執行（不是一條做完才下一條）
        │
  superscalar（超純量）：每個 cycle 處理「多條」指令
    不只一條流水線，有多個執行單元
    dispatch width = 每 cycle 能發射幾條指令（如 4-wide）
        │
  out-of-order（亂序執行）：不按程式順序執行
    如果指令 A 在等記憶體（cache miss），先執行不相依的 B、C
    → 不讓一條卡住的指令擋住整條管線
    ROB（重排序緩衝）：記錄亂序執行的指令，最後按序「退休」
        │
  → 現代 CPU 用 pipeline + superscalar + OoO 榨出平行度
    目標：每個 cycle 完成越多指令越好（高 IPC）
    compiler 的工作：產生「能餵飽這個機器」的程式碼
```

關鍵心智：現代 CPU 是高度優化的「流水線工廠」——**pipeline**（管線，指令分階段重疊執行）、**superscalar**（超純量，每 cycle 處理多條指令）、**out-of-order**（亂序，不讓卡住的指令擋住管線，用 ROB 管理）。目標是「每個 cycle 完成越多指令越好」（高 IPC）。compiler 的工作是產生「能餵飽這個機器」的程式碼。

> 這章的微架構概念是讀懂 Ch 6（perf events）的前置。如果你修過計組課或 mtk_firmware 課的計組部分，這裡是複習。

## IPC:效能的核心指標

```
IPC（Instructions Per Cycle，每週期指令數）：

  IPC = 執行的指令數 / CPU 週期數
        │
  IPC 越高 = 每個 cycle 完成越多指令 = 越有效率
    IPC 1.0 = 每 cycle 完成 1 條（基本）
    IPC 3.0+ = 每 cycle 完成 3 條（superscalar 發揮，好）
    IPC 0.3 = 每 cycle 才 0.3 條（差！大部分 cycle 在「等」）
        │
  IPC 低的原因（CPU 在「等」什麼）：
    - cache miss：等記憶體（最常見，最致命）
    - branch misprediction：分支猜錯，管線清空重來
    - 資料相依：指令 B 要等 A 的結果（沒平行度）
    - 執行單元不足：某類指令的單元都忙
        │
  → IPC 是「CPU 用得多有效率」的指標
    低 IPC = CPU 大部分時間在等（不是在算）
    優化的目標常是「提高 IPC」（減少等待）
        │
  注意：高 IPC 不一定快（可能執行了很多無用指令）
    要看 IPC × 頻率 × 指令數的整體（後述）
```

> **IPC（每週期指令數）是效能的核心指標——低 IPC 表示「CPU 大部分時間在等而非在算」**。**IPC = 執行的指令數 / CPU 週期數**——它衡量「CPU 用得多有效率」。IPC 高（3.0+）= 每個 cycle 完成多條指令（superscalar 發揮得好）；IPC 低（0.3）= 大部分 cycle 在「等」（CPU 沒在算，在等東西）。**IPC 低的原因**（CPU 在等什麼）：(1) **cache miss**（等記憶體——最常見最致命，Ch 17 提過主記憶體比 L1 慢 100 倍）；(2) **branch misprediction**（分支猜錯，管線要清空重來，浪費好幾個 cycle）；(3) **資料相依**（指令 B 要等 A 的結果，沒有平行度可榨）；(4) **執行單元不足**（某類指令的單元都忙）。所以**低 IPC 是「CPU 在等」的信號**——優化的目標常是提高 IPC（減少這些等待：改善 cache locality、讓分支可預測、增加指令級平行）。**但要注意**：高 IPC 不一定代表程式快——可能執行了很多無用指令（IPC 高但指令數也多）。真正的執行時間是 `指令數 × CPI（=1/IPC）× cycle 時間`——所以要看整體（指令數 × IPC × 頻率），不能只看 IPC。compiler 優化可以：減少指令數（更少的工作）、提高 IPC（更有效率地執行）、兩者兼顧。理解 IPC，你看 perf stat 的 IPC 數字就知道「CPU 是在有效工作還是在等」——這是效能分析的起點。

## cache 階層:記憶體的速度鴻溝

```
cache 階層（為什麼記憶體存取是效能關鍵）：

  CPU 暫存器      ~0.3 ns   （最快，但極少）
  L1 cache        ~1 ns     （小，~32-64 KB）
  L2 cache        ~4 ns     （中，~256KB-1MB）
  L3 cache (LLC)  ~10-40 ns （大，~數 MB-數十 MB，多核共享）
  主記憶體 (RAM)  ~100 ns   （大，但慢 100 倍！）
        │
  cache 怎麼運作：
    存取資料時，先找 L1 → 沒有(miss)找 L2 → ... → 最後 RAM
    cache line：一次載入一塊（通常 64 bytes，不是單個 byte）
    → 存取一個 byte 會載入整個 cache line（空間局部性）
        │
  cache miss 的代價（IPC 殺手）：
    L1 miss → L2（多等幾 cycle）
    LLC miss → RAM（等 ~100 ns = 幾百個 cycle！）
    → 一個 LLC miss 可能讓 CPU 等幾百個 cycle（IPC 暴跌）
        │
  → cache 是記憶體速度鴻溝的橋樑
    cache 命中 = 快；cache miss = CPU 枯等記憶體
    優化記憶體存取模式（locality）是效能的關鍵（Ch 17 cachegrind）
```

> **cache miss 是 IPC 的頭號殺手——一個 LLC miss 讓 CPU 枯等幾百個 cycle，這是優化記憶體存取的根本原因**。CPU 和記憶體之間有巨大的速度鴻溝——CPU 每個 cycle 不到 1ns，但主記憶體存取要 ~100ns（**慢 100 倍**）。**cache 階層**（L1/L2/L3）是這個鴻溝的橋樑——常用資料放在快的 cache，存取時先找 L1→L2→L3→RAM（一層層找，越遠越慢）。關鍵概念：**cache line**（一次載入一塊，通常 64 bytes，不是單個 byte——所以存取相鄰資料快，這是 Ch 17 的 locality）。**cache miss 的代價**是 IPC 的頭號殺手——一個 **LLC（最後一層 cache）miss → 要去 RAM → 等 ~100ns = 幾百個 cycle**！在這幾百個 cycle，CPU 可能枯等（如果沒有其他不相依的指令可做），IPC 暴跌。這就是為什麼**記憶體存取模式（locality）是效能的關鍵**（Ch 17 的按行 vs 按列遍歷差好幾倍——純粹因為 cache 行為）。compiler 和程式設計師的重要優化是**改善 cache 行為**：循序存取（cache 友善）、資料結構佈局（常一起用的放近）、減少 working set（讓常用資料放得進 cache）。perf 能測 cache miss（Ch 6），cachegrind 能精確分析（Ch 17）。理解 cache 階層和 miss 的代價，你就懂了「為什麼記憶體存取模式這麼重要」「為什麼同樣的計算 cache 行為不同會差好幾倍」——這是現代效能（memory-bound 而非 compute-bound）的核心。很多現代程式的瓶頸不是「計算太多」而是「等記憶體」（cache miss），所以「優化記憶體存取」常比「優化計算」更有效。

## 分支預測:猜測的代價

```
branch prediction（分支預測）：CPU 怎麼處理 if/迴圈

  問題：遇到 if（分支），CPU 不知道走哪邊
    但 pipeline 要持續取指（不能停下來等判斷）
        │
  解法：分支預測器「猜」走哪邊，先執行（推測執行）
    猜對：賺到（沒浪費）
    猜錯（misprediction）：管線裡推測執行的都白做
      → 清空管線、從正確的分支重新取指
      → 浪費好幾個 cycle（pipeline 深度，10-20 cycle）
        │
  分支預測器很準（現代 >95%）：
    規律的分支（迴圈、總是 true 的 if）很好預測
    不規律的分支（資料相依的、隨機的）難預測
        │
  → branch misprediction 是 IPC 殺手（清管線重來）
    優化：讓分支可預測（規律化）、用 branchless 技巧消除分支
    perf 能測 branch-misses（Ch 6）
        │
  例：排序過的資料的分支比沒排序的好預測（經典的 branch prediction 範例）
```

> **branch misprediction（分支猜錯）讓管線清空重來，浪費 10-20 cycle——這是另一個 IPC 殺手**。CPU 遇到分支（if/迴圈）時不知道走哪邊，但 pipeline 要持續取指（不能停下等判斷）。解法是**分支預測器「猜」**走哪邊並推測執行——猜對就賺到，**猜錯（misprediction）就要清空管線**（推測執行的都白做）、從正確分支重新取指，浪費**好幾個 cycle（管線深度，10-20 cycle）**。現代分支預測器很準（>95%）——**規律的分支**（迴圈、總是 true 的 if）很好預測；**不規律的分支**（資料相依、隨機的）難預測。經典例子：對**排序過的資料**做條件判斷（如「if (x > 128)」）比沒排序的快很多——因為排序後分支變規律（一段都 true、一段都 false），預測器準；沒排序則隨機，預測器常猜錯。所以 **branch misprediction 是 IPC 殺手**（每次猜錯浪費 10-20 cycle）。優化方法：**讓分支可預測**（規律化資料/邏輯）、**branchless 技巧**（用算術/位元運算消除分支，如用條件移動 cmov 代替 if）。compiler 也會做分支優化（PGO 用 profile 知道分支的傾向，Ch 11）。perf 能測 branch-misses（Ch 6）。理解分支預測，你看到 perf 的高 branch-miss rate 就知道「程式有難預測的分支拖累效能」，並知道怎麼優化（規律化或 branchless）。這和 cache miss 是兩大 IPC 殺手——CPU 大部分的「等待」來自這兩個（等記憶體 = cache miss、清管線 = branch miss）。

## 微架構詞彙速查

```
和硬體團隊對話的詞彙（讀懂這些）：

  IPC / CPI          每週期指令數 / 每指令週期數（效能核心）
  dispatch width     每 cycle 能發射幾條指令（如 4-wide superscalar）
  ROB (Reorder Buffer)  亂序執行的指令緩衝（越大能榨越多平行）
  branch predictor   分支預測器（準確率影響 IPC）
  speculation        推測執行（猜測分支/資料先做）
  cache hierarchy    L1/L2/L3 cache 階層
  cache line         cache 的最小單位（通常 64 bytes）
  TLB                位址翻譯快取（虛擬→實體位址，miss 也慢）
  prefetch           預取（CPU 預測會用什麼，提前載入 cache）
  in-flight          正在執行中（還沒退休）的指令
  retire             指令「退休」（按序完成，OoO 的最後一步）
  bound：
    front-end bound  瓶頸在「取指/解碼」（如 I-cache miss、分支）
    back-end bound   瓶頸在「執行」（如 D-cache miss、執行單元不足）
        │
  → 這些詞讓你能讀懂 perf 的 top-down 分析（Ch 6）
    和硬體團隊討論「瓶頸在 front-end 還 back-end」
```

> **front-end bound vs back-end bound 是現代效能分析的關鍵分類——它告訴你「瓶頸在取指還是執行」**。和硬體團隊對話需要這些詞彙，最重要的是**bound 的分類**（top-down 分析，Ch 6 會深入）：**front-end bound**（瓶頸在「**取指/解碼**」——CPU 餵不飽，如 I-cache miss、分支預測問題讓取指卡住）；**back-end bound**（瓶頸在「**執行**」——CPU 想算但被卡，如 D-cache miss 等記憶體、執行單元不足）。這個分類直接指向優化方向——front-end bound 要改善指令供給（減少 I-cache miss、改善分支）、back-end bound 要改善執行（減少 D-cache miss、增加平行度）。其他詞彙：**ROB**（重排序緩衝，越大能榨越多亂序平行）、**dispatch width**（每 cycle 發射幾條，如 4-wide）、**TLB**（位址翻譯快取，miss 也慢）、**prefetch**（預取，CPU 預測會用什麼提前載入）、**retire**（指令退休，按序完成）。這些讓你能讀懂 perf 的 **top-down 分析**（Ch 6——把效能瓶頸分類成 front-end/back-end/retiring/bad-speculation）和跟硬體團隊討論（「我們的 workload 是 back-end bound，主要是 D-cache miss」）。對 RISC-V/compiler 工作，這些詞彙是日常——compiler 優化要知道「目標微架構的 dispatch width、ROB size、cache 大小」才能產生最適合的程式碼。理解這些詞彙，你不只能解讀效能數字，還能參與微架構層級的效能討論。這章是「最少篇幅的微架構」——不是完整的計組課，而是「效能工程師必須懂的」。深入的微架構（亂序執行的細節、各種預測器、記憶體一致性）是計算機架構課的內容，但這些核心概念足以讓你解讀 perf 事件（Ch 6）和理解 compiler 優化（Part 4）。

## 動手練習

1. 理解 IPC：用 `perf stat ./prog` 看 IPC，判斷程式是「有效工作」還是「在等」

2. cache 階層：查你的 CPU 的 cache 大小（`lscpu | grep cache`），理解 L1/L2/L3

3. 分支預測：寫一個對排序 vs 未排序資料做條件判斷的程式，用 perf 比較 branch-misses

4. 詞彙：對照本章的詞彙表，確認你能解釋 IPC/cache miss/branch miss/front-back-end bound

5. 對話練習：用微架構詞彙描述一個效能問題（如「這個程式 back-end bound，D-cache miss 高」）

## 本章重點整理

- 現代 CPU 是流水線工廠：pipeline（重疊執行）+ superscalar（每 cycle 多條）+ OoO（亂序，ROB 管理）
- IPC（每週期指令數）是效能核心：低 IPC = CPU 在等（cache miss/branch miss/相依），高 IPC = 有效率
- cache 階層（L1~RAM 速度差 100 倍）；cache miss 是 IPC 頭號殺手（LLC miss 等幾百 cycle）——locality 是關鍵
- branch misprediction 讓管線清空重來（浪費 10-20 cycle）；規律分支好預測、不規律難——優化用規律化/branchless
- front-end bound（取指瓶頸）vs back-end bound（執行瓶頸）是 top-down 分析的關鍵分類

## 自我檢核

- [ ] 理解 pipeline/superscalar/OoO 怎麼榨出平行度
- [ ] 知道 IPC 是什麼，低 IPC 代表 CPU 在等什麼
- [ ] 理解 cache 階層和 cache miss 為什麼是 IPC 殺手
- [ ] 理解 branch misprediction 的代價和優化方向
- [ ] 能用微架構詞彙（front/back-end bound 等）描述效能問題

## 延伸閱讀

### 書籍

- **《Computer Architecture: A Quantitative Approach》— Ch 3 (ILP)** — Hennessy & Patterson
  - **讀哪幾章**：Ch 2（記憶體階層）、Ch 3（指令級平行、亂序執行、分支預測）
  - **這本書的定位**：計算機架構的權威；微架構的完整版
  - **前提**：本章

- **《Performance Analysis and Tuning on Modern CPUs》— Ch 3 (CPU Microarchitecture)** — Denis Bakhvalov
  - **讀哪幾章**：Ch 3（微架構）、Ch 4（效能計數器）
  - **這本書的定位**：從效能分析角度講微架構，最貼近本課
  - **連結**：免費 PDF

### 文章

- **[Modern CPU 微架構](https://www.lighterra.com/papers/modernmicroprocessors/)** — Jason Robert Carey Patterson
  - **這篇說什麼**：現代微處理器的完整介紹（pipeline/superscalar/OoO）
  - **為什麼值得讀**：本章的視覺化深入版，圖很多

- **[Agner Fog 的 microarchitecture manual](https://www.agner.org/optimize/)** — Agner Fog
  - **這篇說什麼**：各 x86 微架構的詳細分析
  - **為什麼值得讀**：微架構優化的權威（雖然 x86，概念通用）

下一章看 perf 的效能事件——IPC、cache miss、branch miss 怎麼用 perf 實際測量，以及 top-down 分析。把這章的微架構概念變成可測量的數字。

→ [Ch 6 Performance events：IPC / cache miss / branch miss](./06-performance-events.md)
