# Ch 2 — SPEC CPU：業界 benchmark 之王

> **目標**：理解 SPEC CPU 的歷史、結構（intrate/fprate/intspeed/fpspeed、組成的真實程式）、報告規範（base vs peak、reportable run 的規則）、常見誤用。就算你不花錢買 license，懂這套 benchmark 方法論是和 CPU/compiler 團隊對話的基本功——SiFive 的 core 要跑 SPEC，不懂它就無法參與效能討論。

> **環境**：概念為主。SPEC CPU 需要 license（付費），這章講方法論（不需實際跑）。

## 為什麼要懂 SPEC CPU？

SPEC CPU 是業界評估 CPU 和 compiler 效能的**標準 benchmark**——CPU 廠商發新晶片、compiler 團隊改優化，都用 SPEC 的分數來宣稱和比較效能。當你看到「這個 CPU 的 SPECint 分數是 X」「這個 compiler 優化讓 SPEC 提升 Y%」，那就是 SPEC CPU。

即使你不花錢買 SPEC license（一份要幾千美元），**懂它的方法論**是和硬體/compiler 團隊對話的基本功——SiFive 的 core 要跑 SPEC 來證明效能、compiler 改動要用 SPEC 驗證。不懂 SPEC 的結構（base vs peak、哪些子 benchmark、報告規則），你就無法解讀別人的 SPEC 數字、無法參與「我們的 SPEC 分數怎麼提升」的討論。這章講 SPEC 的結構和方法論——這是業界效能評估的共同語言。

## 先建立直覺:一籃子真實程式

```
SPEC CPU = 一籃子「真實程式」的 benchmark 套件

  不是合成的 micro-benchmark，而是「真實的程式」：
    編譯器（gcc）、壓縮（xz）、AI（deepsjeng 下棋）、
    物理模擬、流體力學、影像處理...
        │
  分四類（SPEC CPU 2017）：
    SPECrate（吞吐量，throughput）：同時跑多份，測「總處理量」
      intrate（整數）、fprate（浮點）
    SPECspeed（速度，latency）：跑一份，測「跑多快」
      intspeed（整數）、fpspeed（浮點）
        │
  分數怎麼來：
    每個子 benchmark 對比一個「參考機器」的時間
    算出比值（ratio），多個子 benchmark 取 geomean（幾何平均，Ch 4）
        │
  → SPEC 用「一籃子真實程式」評估綜合效能
    比單一 micro-benchmark 更能代表「真實的 CPU/compiler 效能」
    這是它成為業界標準的原因——夠真實、夠全面
```

關鍵心智：SPEC CPU 是「一籃子真實程式」的 benchmark——不是合成的 micro-benchmark，而是真實程式（編譯器、壓縮、AI、物理模擬）。分四類：**rate**（吞吐量，同時跑多份）和 **speed**（速度，跑一份），各有整數（int）和浮點（fp）。分數是各子 benchmark 對比參考機器的比值取**幾何平均**。它用「真實程式的籃子」評估綜合效能，比單一 benchmark 更代表真實——這是它成為業界標準的原因。

> SPEC 是 macro-benchmark（Ch 1）的代表——測真實的完整程式。它的分數計算用 geomean（Ch 4 統計會深入為什麼用幾何平均）。

## SPEC CPU 2017 的結構

```
SPEC CPU 2017 的四個套件：

  整數（Integer）：
    SPECrate 2017 Integer  —— intrate（吞吐量）
    SPECspeed 2017 Integer —— intspeed（速度）
    組成（部分）：
      perlbench（Perl 直譯器）、gcc（編譯器）、mcf（路徑規劃）、
      omnetpp（網路模擬）、xalancbmk（XML）、x264（影片編碼）、
      deepsjeng（下棋 AI）、leela（圍棋 AI）、exchange2（數獨）、xz（壓縮）
        │
  浮點（Floating Point）：
    SPECrate 2017 FP  —— fprate
    SPECspeed 2017 FP —— fpspeed
    組成（部分）：
      bwaves（流體）、cactuBSSN（相對論）、lbm（流體）、
      wrf（氣象）、cam4（氣候）、pop2（海洋）、imagick（影像）、
      nab（分子動力學）、fotonik3d（電磁）、roms（海洋模型）
        │
  → 每個子 benchmark 是真實領域的程式
    整數套件代表「一般運算」（編譯/壓縮/AI）
    浮點套件代表「科學運算」（模擬/物理）
```

```
rate vs speed 的差別（重要）：

  SPECrate（吞吐量）：
    同時跑「多份 copy」（如 N 個核心各跑一份）
    測「總共能處理多少」（throughput）
    → 反映「多核心、多工」的效能
        │
  SPECspeed（速度）：
    跑「一份」，測它「跑多快」（latency）
    通常開 OpenMP（用多執行緒加速單一 workload）
    → 反映「單一任務跑多快」
        │
  → rate 看「總處理量」（伺服器、多工場景）
    speed 看「單一任務速度」（互動、延遲敏感場景）
    報效能要說清楚是 rate 還是 speed（常被混淆）
```

> **SPECrate（吞吐量，多份同時跑）vs SPECspeed（速度，單份跑多快）是關鍵區別——報效能要說清楚是哪個**。SPEC CPU 2017 分**整數**（一般運算：編譯器 gcc、壓縮 xz、AI deepsjeng）和**浮點**（科學運算：流體、氣象、物理模擬）。更重要的區別是 **rate vs speed**：**SPECrate**（吞吐量）同時跑**多份 copy**（如每個核心跑一份），測「**總共能處理多少**」（throughput）——反映多核多工效能（伺服器場景）；**SPECspeed**（速度）跑**一份**測它「**跑多快**」（latency，通常開 OpenMP 用多執行緒加速單一任務）——反映單一任務速度（互動/延遲敏感場景）。這個區別很重要——**報效能時要說清楚是 rate 還是 speed**（常被混淆，數字不能直接比）。一個 CPU 可能 rate 高（多核吞吐好）但 speed 一般，或反之。對 compiler 工作，兩者都重要——優化要看對哪個有幫助。子 benchmark 各代表真實領域（gcc 代表編譯器 workload、x264 代表影片編碼、流體模擬代表 HPC）——這讓 SPEC 的綜合分數能代表「真實的多樣 workload」，而非單一情境。理解這個結構，你看 SPEC 報告時知道它在測什麼、rate 和 speed 的差別、哪些子 benchmark 對你的場景最相關。

## base vs peak:報告規範

```
SPEC 的 base vs peak（報告的兩種模式）：

  base（基準）：
    所有子 benchmark 用「相同的 compiler flags」
    限制：flag 數量有限、不能 per-benchmark 調
    → 代表「一般使用者用一套 flag」的效能（公平、可比）
        │
  peak（峰值）：
    每個子 benchmark 可以「個別調 flags」（per-benchmark 優化）
    甚至用 feedback-directed（PGO）、不同的 flag 組合
    → 代表「極致調校」的效能（廠商展示最佳能力）
        │
  → base 比較公平（同一套 flag），peak 展示極限（個別調校）
    報 SPEC 分數要說是 base 還是 peak（差異可能很大）
    廠商常報 peak（好看），但 base 更代表「一般情況」
        │
  reportable run 的規則（嚴格）：
    要跑足夠次數、用官方的 tool、符合 run rules
    才能宣稱是「正式的 SPEC 分數」（official）
    隨便跑的不能拿來宣稱
```

> **base（同一套 flag，公平可比）vs peak（個別調校，展示極限）——廠商常報好看的 peak，但 base 更代表一般情況**。SPEC 報告有兩種模式：**base**——所有子 benchmark 用**相同的 compiler flags**（flag 數量有限制、不能針對個別 benchmark 調），代表「一般使用者用一套 flag 編譯」的效能（**公平、可比**——大家同樣條件）；**peak**——每個子 benchmark 可以**個別調 flags**（per-benchmark 優化、甚至用 PGO、不同 flag 組合），代表「**極致調校**」的效能（廠商展示最佳能力）。**關鍵**：報 SPEC 分數要說清楚是 **base 還是 peak**（差異可能很大——peak 通常比 base 高，因為個別調校）。廠商行銷常報 **peak**（數字好看），但 **base 更代表「一般情況」**（你不會為每個程式個別調 flag）。對 compiler 工作這很重要——你的優化是改善 base（影響所有人）還是 peak（極致調校）？另外 SPEC 有嚴格的 **reportable run 規則**——要跑足夠次數、用官方 tool、符合 run rules，才能宣稱是「正式的 SPEC 分數」（official）；隨便跑的數字（如 `-noreportable` 快跑）只能自己參考，不能對外宣稱。理解 base/peak 和 reportable 規則，你才能正確解讀別人的 SPEC 數字（「他報的是 peak，我們的 base 不能直接比」）和正確地呈現自己的（說清楚條件）。這是 SPEC 方法論的核心——數字要在「相同的條件」下才能比較。

## SPEC 的常見誤用

```
SPEC 數字的常見誤用（理解這些才不會被誤導）：

  1. base 和 peak 混比：
     用自己的 base 比別人的 peak → 不公平
        │
  2. 不同 SPEC 版本混比：
     SPEC 2006 和 2017 的分數不能直接比（不同 workload）
        │
  3. rate 和 speed 混比：
     吞吐量分數和速度分數是不同的東西
        │
  4. 忽略編譯器和 flag：
     SPEC 分數高度依賴 compiler 和 flags
     同 CPU 用不同 compiler 分數可能差 10-20%
        │
  5. 過度優化單一 benchmark（gaming）：
     針對 SPEC 的特定 benchmark 加 hack（如特殊的 idiom recognition）
     → SPEC 分數高但對真實程式沒幫助（甚至有害）
        │
  → SPEC 是好工具，但數字要在「相同條件」下比
    版本、base/peak、rate/speed、compiler、flag 都要一致
    否則「比較」毫無意義
```

> **SPEC 數字只能在「完全相同的條件」下比較——版本/base-peak/rate-speed/compiler/flag 任一不同就不可比**。SPEC 的常見誤用都源於「比較條件不一致」：(1) **base 比 peak**（不公平，peak 是極致調校的）；(2) **不同 SPEC 版本混比**（2006 和 2017 是不同 workload，分數不能直接比）；(3) **rate 比 speed**（吞吐量 vs 速度，不同的東西）；(4) **忽略 compiler/flag**——SPEC 分數**高度依賴 compiler 和 flags**（同一個 CPU 用 gcc vs clang、用不同 flag，分數可能差 10-20%！所以「CPU A 的 SPEC 比 CPU B 高」可能只是用了更好的 compiler）；(5) **gaming（針對 SPEC 過度優化）**——廠商/compiler 針對 SPEC 的特定 benchmark 加 hack（如針對某個迴圈的特殊 idiom recognition），讓 SPEC 分數高但對真實程式沒幫助甚至有害（這是 benchmark 的根本問題——「優化 benchmark」不等於「優化真實效能」，極端的 gaming 讓 SPEC 失去代表性）。理解這些誤用，你看 SPEC 數字時會問「是什麼條件下測的」（版本/base-peak/rate-speed/compiler/flag），而非盲目相信「分數高就是好」。對 compiler 工作，你也要警惕「優化 SPEC 但不幫助真實程式」的陷阱——SiFive 的 compiler 改動應該改善真實效能，SPEC 是驗證工具不是優化目標（優化 benchmark 本身是 gaming）。SPEC 是好工具（真實、全面、業界標準），但要正確使用——相同條件下比較、警惕 gaming、理解它的限制。

## 沒有 license 怎麼學 SPEC 方法論

```
沒買 SPEC license 也能學方法論：

  1. 讀公開的 SPEC 報告（spec.org 有公開的 result）：
     看廠商怎麼報（base/peak、flag、machine config）
        │
  2. 用替代的開源 benchmark（類似的真實程式）：
     LLVM test-suite（含很多真實程式的 benchmark）
     Coremark（Ch 3，嵌入式的）
        │
  3. 理解 SPEC 的子 benchmark（很多是開源程式）：
     gcc、xz、x264 都是開源的，可以單獨研究
        │
  4. 學 run rules 和方法論（公開文件）：
     SPEC 的 run rules、reportable 規則都是公開的
        │
  → 重點是「懂方法論」（base/peak、geomean、run rules、誤用）
    不一定要真的跑 SPEC（license 貴）
    懂方法論才能解讀別人的數字、參與討論
```

> **學 SPEC 重點是「懂方法論」（base/peak/geomean/run rules/誤用），不一定要真的跑（license 貴）**。SPEC CPU 的 license 要幾千美元，但**懂它的方法論不需要實際跑**：(1) **讀公開的 SPEC 報告**（spec.org 有大量公開的 result——看廠商怎麼報 base/peak、用什麼 flag、machine config，學習正式報告的格式和條件揭露）；(2) **用替代的開源 benchmark**——**LLVM test-suite**（含很多真實程式的 benchmark，免費，類似 SPEC 的「真實程式籃子」）、Coremark（Ch 3）；(3) **研究 SPEC 的開源子 benchmark**（gcc、xz、x264 都是開源的，可以單獨拿來測和研究）；(4) **學 run rules**（SPEC 的規則是公開文件）。對 perf_bench 的目標（懂方法論、能對話），重點是理解 **base/peak（報告模式）、geomean（分數計算，Ch 4）、run rules（正式性）、常見誤用（條件一致性、gaming）**——這些讓你能解讀別人的 SPEC 數字、參與「我們的 SPEC 怎麼提升」的討論、正確地呈現自己的測量。實際跑 SPEC 是 compiler/CPU 團隊的日常（他們有 license），但作為效能工程師，懂方法論是基本功。如果你的工作真的需要跑 SPEC，公司會有 license——這章讓你準備好正確地使用它。對學習，用 LLVM test-suite 或 Coremark（Ch 3）做類似的「真實程式 benchmark」實驗，能體會 macro-benchmark 的方法論。

## 動手練習

1. 讀 SPEC 報告：去 spec.org 看一份公開的 SPEC CPU 2017 result，找出 base/peak、compiler、flag

2. 理解 rate/speed：對照一個報告的 intrate 和 intspeed，理解兩者的差別

3. 研究子 benchmark：下載 xz 或 gcc（SPEC 的開源子 benchmark），單獨測它

4. 替代 benchmark：clone LLVM test-suite，看它的真實程式 benchmark（類似 SPEC）

5. 識破誤用：找兩份不同條件的 SPEC 報告，思考它們能不能直接比（版本/base-peak/compiler）

## 本章重點整理

- SPEC CPU 是「一籃子真實程式」的業界標準 benchmark（編譯器/壓縮/AI/科學模擬），代表綜合真實效能
- 四類：rate（吞吐量，多份同時跑）vs speed（速度，單份跑多快），各有 int（一般）和 fp（科學）
- base（同一套 flag，公平可比）vs peak（個別調校，展示極限）；報分數要說清楚是哪個
- 常見誤用都源於「條件不一致」：版本/base-peak/rate-speed/compiler/flag 任一不同就不可比；警惕 gaming
- 懂方法論（base/peak/geomean/run rules/誤用）不需買 license；用 LLVM test-suite/Coremark 做類似實驗

## 自我檢核

- [ ] 知道 SPEC CPU 是什麼、為什麼是業界標準
- [ ] 能區分 rate/speed、int/fp、base/peak
- [ ] 知道 SPEC 數字只能在相同條件下比較，能識破誤用
- [ ] 理解 gaming（優化 benchmark 不等於優化真實效能）的問題
- [ ] 知道沒 license 怎麼學方法論（讀報告、用替代 benchmark）

## 延伸閱讀

### 官方

- **[SPEC CPU 2017](https://www.spec.org/cpu2017/)** — SPEC
  - **讀哪裡**：Documentation（run rules、子 benchmark 說明）、公開的 results
  - **為什麼值得讀**：SPEC 的權威；公開報告是學習報告格式的最佳素材

### 文章

- **[Understanding SPEC benchmarks](https://www.anandtech.com/show/16315/the-ampere-altra-review/3) / 各種 SPEC 分析文章**
  - **這篇說什麼**：怎麼解讀 SPEC 報告（CPU review 常用 SPEC）
  - **為什麼值得讀**：實際的 SPEC 數字解讀範例

### 替代 benchmark

- **[LLVM test-suite](https://github.com/llvm/llvm-test-suite)** — LLVM
  - **為什麼值得讀**：免費的「真實程式 benchmark」集合，類似 SPEC 的方法論

### 書籍

- **《Computer Architecture: A Quantitative Approach》— benchmarking 章** — Hennessy & Patterson
  - **讀哪幾章**：效能評估、benchmark 那節
  - **為什麼值得讀**：benchmark 方法論的學術權威（geomean、誤用的理論基礎）

下一章看 Coremark 和 Embench——RISC-V 和嵌入式的主力 benchmark，比 SPEC 輕量、免費、適合嵌入式。理解它們的結構和怎麼跑（這章能實際動手）。

→ [Ch 3 Coremark / Embench：RISC-V 與嵌入式主力](./03-coremark-embench.md)
