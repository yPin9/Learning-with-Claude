# Ch 3 — 並行 vs 平行、硬體平行的層次

> **目標**：把「並行」和「平行」這兩個常被混用的詞，在腦裡釘死兩個清晰的概念；然後把硬體平行從最細粒度（指令級）到最粗粒度（分散式）拆成四層，每層說清楚加速來源和程式設計代價。這是寫高效平行程式的第一塊地基，也是理解 GPU 為什麼設計成那個樣子的前置知識。

> **環境**：gcc 14 + OpenMP + AVX2, x86-64 Windows（MSYS2）

回頭看 Ch 2 的 Roofline，你知道了理論上界在哪。但還沒觸碰「怎麼讓程式真的跑快」的核心問題：**什麼叫做平行？不同硬體層次的平行有什麼差別？** 不搞清楚這個，你的優化是在黑暗中摸索。

---

## 一、Rob Pike 的那個關鍵區分

2012 年 Google 的 Rob Pike 在一個演講裡說了這句話，之後被傳了無數遍：

> **Concurrency is about dealing with lots of things at once. Parallelism is about doing lots of things at once.**

這句話本身有點繞。用更具體的說法：

**並行（Concurrency）** 是**結構**，是一種程式組織方式，把問題拆成多個可以獨立推進的任務，這些任務之間可以交替執行（interleave），不一定同時。

**平行（Parallelism）** 是**執行**，是多個計算在**物理上同時**發生，需要多個硬體執行單元。

```
並行 (Concurrency) — 結構
─────────────────────────────────────────────────────────
Thread A: ──────────────────────────────────
Thread B:                   ────────────────────

時間軸：   [A runs] [switch] [B runs] [switch] [A runs]...
          ↑ 任意時刻只有一個在跑（單核 + 時間分片）
          但 A 和 B 的邏輯是獨立、交織的 → 這叫「並行」

平行 (Parallelism) — 同時執行
─────────────────────────────────────────────────────────
Core 0: [A runs]  [A runs]  [A runs]
Core 1: [B runs]  [B runs]  [B runs]

時間軸：   ↑ 兩個核心真的同時在做事 → 這叫「平行」
```

**平行一定有並行的結構，但並行不一定有平行的執行。**

在 C 裡寫 pthread 管 I/O 多工，是並行（程式結構上有多條執行流）。在 OpenMP 把迴圈分派到 16 個核心，是平行（真正同時跑）。一個 event loop（如 Node.js）是並行，但通常是單線程——沒有平行。

這個區分為什麼重要？因為：
- 並行主要解決**延遲**問題（讓一個慢的 I/O 不卡住其他工作）
- 平行主要解決**吞吐量**問題（把大計算分配給多個執行單元）

GPU 的設計目標是最大化**平行**，它的 SIMT 模型幾乎沒有並行的概念——就是暴力的「同時執行幾千條 thread」。

---

## 二、硬體平行的四個層次

現代 x86 處理器是層層套疊的平行機器。從細到粗：

```
層次                    英文縮寫      代表技術
──────────────────────────────────────────────────────────
指令級平行 (Instruction  ILP          亂序執行 / superscalar /
          Level Parallelism)          流水線 / 分支預測
資料級平行 (Data Level   DLP / SIMD   SSE / AVX / AVX-512
          Parallelism)                (GPU 的 warp = 放大版 SIMD)
執行緒級平行 (Thread     TLP          多核 / SMT (HyperThreading)
          Level Parallelism)          OpenMP / pthreads
節點級平行 (Node Level   NLP          MPI / RDMA / 分散式訓練
          Parallelism)
```

這四層的關鍵差別：

| 層次 | 硬體誰負責    | 程式設計師要做什麼         | 粒度（一次多少工作）    |
|------|-------------|--------------------------|----------------------|
| ILP  | CPU 自己     | 減少資料依賴（展開迴圈）    | 1–8 條指令同時發射     |
| SIMD | 由你或編譯器  | 用 intrinsics 或讓編譯器向量化 | 4–16 個資料元素       |
| TLP  | OS + 你     | 開 thread、管同步          | 幾千到幾億條指令       |
| NLP  | 你 + 框架   | 切分資料、設計通訊         | 整個程式的子任務       |

重要的觀察：**越細粒度的平行，代價越低，但能榨出的加速越有限；越粗粒度，可能加速越大，但協調開銷也越大。**

---

## 三、ILP（指令級平行）— 最透明但也最被忽視的一層

ILP 是 CPU 自動做的。你寫循序程式，CPU 卻偷偷在裡面找可以同時做的指令。

### 現代超純量 CPU 能做什麼

以 Intel Core i7（10 代以後）為例：
- 每個時鐘週期最多發射 **4–6 條指令**（4-wide to 6-wide superscalar）
- **亂序執行（Out-of-Order Execution, OoO）**：CPU 有一個 Re-order Buffer（ROB），能看未來 200+ 條指令，只要依賴關係允許就先做
- **流水線（Pipeline）**：每條指令分成多個 stage（fetch/decode/execute/writeback），多條指令同時在不同 stage

但 ILP 有一個殺手：**資料依賴（data dependency）**。

```c
// 單條依賴鏈：每次 s += 都必須等上一次 s += 完成
double s = 0.0;
for (int i = 0; i < N; i++) {
    s += arr[i];  // s 依賴前一次 s，形成長度為 N 的依賴鏈
}
// CPU 的 FP add 延遲是 4-5 個時鐘週期
// 這個迴圈有效率只有 1 FP add / 5 cycles ≈ 20%
```

解法：用**迴圈展開（loop unrolling）** 打破單條依賴鏈，讓 CPU 有多條獨立的計算可以同時推進：

```c
// 4 路展開：四條獨立依賴鏈，CPU 能同時排程所有四條
double s0 = 0.0, s1 = 0.0, s2 = 0.0, s3 = 0.0;
for (int i = 0; i + 3 < N; i += 4) {
    s0 += arr[i];      // 依賴鏈 0
    s1 += arr[i+1];    // 依賴鏈 1，和鏈 0 獨立
    s2 += arr[i+2];    // 依賴鏈 2
    s3 += arr[i+3];    // 依賴鏈 3
}
// ... 收尾
return s0 + s1 + s2 + s3;
```

### 動手跑：ILP 展開的加速

實際測這台機器（gcc 14.2, -O2, i7-10700, 16 threads 但這裡只用單核）：

```c
// 編譯指令：gcc -O2 ilp_demo.c -o ilp_demo
#include <time.h>
#define N 500000000LL  // 5億次加法

double sum_serial(long n) {
    double s = 0.0;
    for (long i = 0; i < n; i++) s += 1.0;  // 單條依賴鏈
    return s;
}

double sum_unroll4(long n) {
    double s0=0, s1=0, s2=0, s3=0;
    for (long i = 0; i+3 < n; i += 4) {
        s0 += 1.0; s1 += 1.0; s2 += 1.0; s3 += 1.0;
    }
    // 收尾略
    return s0+s1+s2+s3;
}
```

**真實輸出**（這台機器實測）：
```
serial  (1 dep chain): result=500000000  time=0.443 s
unroll4 (4 dep chain): result=500000000  time=0.111 s  speedup=4.01x
```

4 路展開得到接近 4x 的加速——因為 FP add 的延遲是 4–5 cycles，4 路展開剛好讓 CPU 的 pipeline 塞滿。

**為什麼 -O2 的 compiler 不自動幫你做？**

`-O2` 下 gcc 確實會做一些展開，但它不展開浮點累加的依賴鏈，因為這會改變浮點運算的結合順序（違反 IEEE 754 精確語意）。你要加 `-ffast-math` 才允許編譯器這樣做：

```bash
gcc -O3 -ffast-math ilp_demo.c -o ilp_demo_fast
```

加了之後 `-O3 -ffast-math` 生成的 serial 版本通常自動被向量化，反而快得多。但注意 `-ffast-math` 會讓 `NaN` 和 `Inf` 的行為不再 IEEE 相容——嵌入式和金融領域要小心。

---

## 四、SIMD（資料級平行）— 一條指令，多個資料

SIMD 是「Single Instruction, Multiple Data」的縮寫。一條 AVX2 指令可以同時處理 8 個 float（256 bits / 32 bits/float）。

這是 CPU 平行的第二層，也是 GPU 的核心設計——GPU 的 warp（32 個 thread 同時執行同一條指令）就是 SIMD 的放大版。

下一章（Ch 4）會完整深挖 SIMD，這裡只建立直覺：

```
純量 (scalar):
  加法: [f0] + [g0] = [h0]   — 一次 1 個

SSE (128-bit):
  加法: [f0,f1,f2,f3] + [g0,g1,g2,g3] = [h0,h1,h2,h3]  — 一次 4 個

AVX2 (256-bit):
  加法: [f0..f7] + [g0..g7] = [h0..h7]  — 一次 8 個

AVX-512 (512-bit):
  加法: [f0..f15] + [g0..g15] = [h0..h15]  — 一次 16 個
```

**加速來源**：更寬的資料路徑，相同的指令條數做更多工作。  
**代價**：程式設計師要重寫迴圈（或讓編譯器自動向量化，但不是每次都成功）；資料要對齊到適當邊界；不規則的記憶體存取模式讓向量化很難做。

---

## 五、TLP（執行緒級平行）— 多核心同時推進

這是最直覺的平行：多個 CPU 核心各跑一條 thread，真正同時做不同的事情。

現代桌上型 CPU 有 8–24 個核心，資料中心有 64–192 個。這台機器有 8 個實體核心 × 2 HyperThreading = 16 threads。

TLP 的加速上界由 Amdahl's Law 決定（見 Ch 2）。程式的串列部分（同步點、I/O、無法分解的算法）是天花板。

**代價**：thread 之間共享記憶體，要處理：
- **Race condition**（競態條件）：多個 thread 同時讀寫同一資料，結果不確定
- **Deadlock**（死鎖）：互相等待對方釋放 lock
- **False sharing**（偽共用）：看似各自獨立，但在同一條 cache line 上，互相 invalidate

Ch 5 會完整處理這些問題，包含真跑 race condition 看到錯誤。

SMT（Simultaneous Multi-Threading，Intel 叫 HyperThreading）是介於 ILP 和 TLP 之間的東西：一個實體核心假裝成兩個邏輯核心，兩條 thread 共享執行單元但有各自的 register set。當一條 thread 在等記憶體時，另一條可以用它的執行單元。**SMT 不是真正的平行**——它的加速取決於你的工作負載和 ILP 饑餓程度，有時甚至會因為 cache 競爭變慢。

---

## 六、NLP（節點級平行）— 超出單機的邊界

當問題大到單台機器裝不下（TB 級資料集、訓練巨大模型），就需要多節點平行：

- **MPI（Message Passing Interface）**：每個節點有自己的記憶體，通過顯式訊息傳遞（`MPI_Send` / `MPI_Recv`）溝通
- **分散式訓練（Data Parallelism / Model Parallelism）**：把模型參數或訓練資料切分到多個 GPU，用 NCCL、AllReduce 同步梯度
- **RDMA（Remote Direct Memory Access）**：透過 InfiniBand 或 RoCE 直接存取遠端記憶體，繞過 CPU

這門課不深入 NLP（那是另一門課的主題）。但如果你後來學 PyTorch DDP 或 NCCL，會發現它的 AllReduce 本質上就是一個 tree reduction——跟 Ch 6 講的 reduce pattern 在邏輯上完全一樣，只是跨機器做。

---

## 七、四層比較總表

| 層次   | 代表技術       | 理論加速上限          | 主要瓶頸       | 下一章覆蓋 |
|--------|--------------|---------------------|--------------|---------|
| ILP    | OoO / 展開   | ~4–8x（依賴鏈決定）   | 資料依賴       | Ch 3 本章 |
| SIMD   | AVX2         | ~8x（float 寬度）    | 記憶體帶寬、對齊 | Ch 4    |
| TLP    | OpenMP / 多核 | ~16x（這台機器）      | Amdahl / 同步 | Ch 5    |
| NLP    | MPI / NCCL   | ~1000x+（節點數）    | 通訊延遲       | 本課不深入 |

理想情況下三層 CPU 平行全開：ILP × SIMD × TLP，理論上在這台機器可以拿到 4（ILP展開）× 8（AVX2）× 16（核心） = 512x——這當然是紙面上的，實際上記憶體帶寬才是真正的天花板。

---

## 踩雷集錦

**1. 把「並行」和「平行」當同義詞**

工程師常把 concurrent 和 parallel 混用。在你討論設計、優化或 bug 時，要區分清楚：「我有 16 個 thread 在跑，但可能只有 4 個真的在做運算」——這是並行但平行度有限（如 lock contention 導致大部分 thread 在等）。

**2. 誤以為 ILP 是免費的**

ILP 依賴 CPU 的 OoO 窗口（ROB 大小，約 200–300 條指令）。如果你的依賴鏈很長，OoO 窗口塞不了多少獨立工作，就算 superscalar 也沒用。展開迴圈、降低依賴深度才能真正讓 ILP 發揮。

**3. 只看單一層次的加速**

你可能對一個 AVX2 版本得到 3x 很失望（理論是 8x），但如果你同時加了 OpenMP 16 個核心，總加速可能是 30x+。不要只看一層的數字——在效能分析時，把三層分別量測再組合。

**4. 忽視記憶體帶寬的限制**

ILP 和 SIMD 增加計算吞吐量，但如果程式是 memory-bound（如 SAXPY：每個 FMA 只做 1 次乘加，但要讀 2 個 float 寫 1 個），加速效果會被記憶體帶寬封頂，不是算力。Roofline（Ch 2）告訴你在哪個 region。

**5. SMT（HyperThreading）不是 2x 多核**

SMT 是共享執行單元的。compute-bound 的程式開 SMT 通常沒幫助，甚至因為搶 L1D 和 TLB 而變慢。只有 memory-bound 或 I/O 混合的工作負載才能從 SMT 獲益。

---

## 本章重點

- **並行（Concurrency）= 結構**，任務可以交錯推進，不一定同時。**平行（Parallelism）= 執行**，物理上同時發生。
- CPU 平行有四層：ILP（CPU 自動）→ SIMD（你或編譯器）→ TLP（OS + 你）→ NLP（你 + 框架）。
- ILP 的敵人是資料依賴鏈；迴圈展開讓 CPU OoO 窗口看到更多獨立工作，這台機器 4 路展開實測 4.01x。
- GPU 的 SIMT 本質是 SIMD 的放大版（warp = 32 路 SIMD），這是 Part 2 的核心主題。
- 記憶體帶寬往往比算力更先到達天花板——Ch 2 的 Roofline 就是分析這個。

---

## 自我檢核

1. Rob Pike 說的 "concurrency is not parallelism" 是什麼意思？舉一個生活中的例子分別說明。
2. ILP 的最大障礙是什麼？用 `double s = 0; for(i) s += arr[i];` 解釋為什麼這個迴圈很難讓 CPU 找到 ILP。
3. AVX2 有 256-bit 寬，對 float（32-bit）一次處理幾個？理論加速是幾倍？
4. 這台機器有 16 個邏輯 thread，但 Amdahl's Law 說 10% 的串列部分讓最大加速只有 9.1x，你怎麼解讀這個數字和 16x 的差距？
5. SMT（HyperThreading）為什麼對 compute-bound 程式幫助有限？

---

## 延伸閱讀

1. **Rob Pike, "Concurrency is not Parallelism" (2012)**  
   slides: `talks.golang.org/2012/waza.slide` / YouTube 搜「Concurrency is not Parallelism Rob Pike」  
   讀哪裡：看完整個演講（30 分鐘）。前提：不需要懂 Go。關聯：直接來源，比任何二手說明都清楚。

2. **《Computer Systems: A Programmer's Perspective》（CS:APP）Ch 5**  
   「Optimizing Program Performance」：依賴分析、迴圈展開、ILP。  
   前提：知道基本 C 和組合語言概念。關聯：本章 ILP 段落的核心教科書，有詳細的 CPE (Cycles Per Element) 分析框架。

3. **Agner Fog, "Optimizing C++" (PDF, agner.org/optimize)**  
   「8. Instruction throughput and latency」和「12. Loops」。  
   前提：懂 C，對 x86 組語有概念更好。關聯：最詳盡的 ILP / 依賴鏈量化分析，有各代 CPU 的延遲表格，是本章數字的來源之一。

4. **Intel Architecture Optimization Reference Manual**  
   Chapter 2 「Intel Core Microarchitecture」—— Execution Units、Out-of-Order Engine。  
   前提：有一點硬體架構概念。關聯：真實硬體規格來源，OoO 窗口大小、ROB 深度都在這裡。

5. **"What Every Programmer Should Know About Memory" (Ulrich Drepper, 2007, lwn.net)**  
   Part 2「CPU caches」。  
   前提：懂 C。關聯：Ch 5 的 false sharing 和 cache 效應的完整背景，本課最推薦的補充讀物之一。

---

我們已經確立了平行的概念地圖和硬體四層次。下一章的主題是 SIMD——把資料級平行從概念變成你能真正控制的工具，包含手寫 AVX2 intrinsics 和自動向量化的取捨。

→ [Ch 4 SIMD 向量化](./04-simd-vectorization.md)
