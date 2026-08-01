# Ch 25 — 為什麼要 cache：memory wall、locality、記憶體階層

> **目標**：搞懂一個殘酷事實——你的 pipeline 每拍能吃一條指令，但 DRAM 一次存取要幾十到上百拍。中間這道鴻溝叫 memory wall。這章你會親手用 C 程式量到「同一份資料、換個遍歷順序就慢 7 倍」，用 pointer chasing 把記憶體階層的每一層延遲一層層量出來，並學會用 AMAT 公式把「加一層 cache 值不值得」算成一個數字。這是後面三章（I-cache/D-cache/整合）的動機來源。
> **環境**：WSL + gcc 11.4。所有時間與延遲數字皆本機真跑量測（CPU L1d=32 KiB、L2=2 MiB、L3=16 MiB、cache line=64 B）。

## 為什麼需要 cache：先看那道牆

到 Ch 24 為止，我們的 `core` 一直假設一件事：**取指和 load/store 都是一拍完成**。`imem` 和 `dmem` 給位址、下一拍就回資料。這在模擬裡成立，因為我們用 SystemVerilog 陣列當記憶體——它就是一拍。

但真實的矽不是這樣。真實記憶體是 DRAM，離 CPU 很遠（跨過封裝、走 memory controller、經過幾道 bus），而且 DRAM 的物理特性決定它慢：一次隨機存取要充放電、要 row activate、要跨晶片。以現代機器的量級：

```
      CPU 一拍         ~0.3 ns  (3 GHz)
      L1 cache 命中    ~1 ns    (約 3~4 拍)
      L2 cache 命中    ~3 ns    (約 10 拍)
      L3 cache 命中    ~10 ns   (約 30~40 拍)
      DRAM 存取        ~60~100 ns (約 200~300 拍)   ← 這道牆
```

差距不是幾倍，是**兩三個數量級**。如果你的 pipeline 每次 load 都要等 DRAM，那 IPC 從 1 掉到 1/200——前面 24 章拼命做的 forwarding、分支預測全部白費，因為 CPU 99% 的時間在等記憶體。

這道 CPU 與 DRAM 之間愈拉愈大的速度差，就是 **memory wall（記憶體牆）**。

### memory wall 的歷史成因

1980 到 2000 年代，CPU 時脈每年約增快 55%，DRAM 存取延遲每年只快約 7%。兩條指數曲線，一條爬得快一條爬得慢，缺口逐年放大——這就是 Wulf 與 McKee 1995 年那篇著名短文命名的 "hitting the memory wall"。今天時脈成長雖已趨緩（改走多核），但單次 DRAM 延遲仍在 50~100 ns 這個量級動不了，牆一直都在。

DRAM 延遲難降的根因是物理：電容充放電時間、訊號跨越封裝的傳播時間，這些不隨製程微縮而等比例變快。頻寬（每秒搬多少 bytes）可以靠更寬的 bus、更多 channel 堆上去，但**延遲（一次要等多久）**幾乎卡死。cache 存在的全部理由，就是把常用資料放到離 CPU 近、延遲低的地方，讓大多數存取不必去碰那道牆。

## 先建立直覺：桌面、抽屜、倉庫

把記憶體階層想成你工作時的東西擺放：

```
   register  = 你手上正拿著的那張紙        （0 拍，立刻）
   L1 cache  = 桌面上攤開的幾張常用文件    （~1 ns）
   L2 cache  = 手邊抽屜裡的資料夾          （~3 ns）
   L3 cache  = 辦公室角落的檔案櫃          （~10 ns）
   DRAM      = 走廊盡頭的大倉庫            （~60 ns）
   disk/SSD  = 城外的檔案倉儲中心          （~100 µs 起跳，慢一萬倍）
```

你不會把所有文件都攤在桌上——桌面小，放不下。你把**現在正在用、以及接下來很可能用到**的放桌上，其餘丟抽屜、櫃子、倉庫。要用倉庫的東西時走一趟很累，但只要你「大多數時間都在用桌上那幾張」，平均下來就快。

cache 賭的就是這件事：**程式的記憶體存取不是隨機的，它有規律**。這個規律叫 locality（局部性）。

## 核心概念：兩種 locality

cache 能成立，完全依賴程式存取記憶體的兩個統計規律：

- **temporal locality（時間局部性）**：剛存取過的位址，很可能**馬上又被存取**。迴圈變數 `i`、迴圈裡反覆呼叫的函式、堆疊上的區域變數——都是一存取就會連續存取好一陣子。
- **spatial locality（空間局部性）**：存取過某位址，它**附近的位址**很可能接著被存取。陣列一個一個掃、struct 一個欄位接一個欄位讀、指令一條接一條抓（PC 通常 +4）——都是相鄰位址接連被碰。

cache 的設計直接對應這兩者：
- 為了吃 temporal locality：**存過的資料就留著**，下次再要不必去 DRAM。
- 為了吃 spatial locality：**一次搬一整塊（cache line / block，通常 64 B）**進來，不是只搬你要的那 4 B。你要 `A[0]`，它連 `A[1..15]` 一起搬進來（假設 int），你接著掃 `A[1]` 就直接命中。

**cache 不理解你的程式，它只賭這兩個統計規律。** 賭對了（大多數程式都對），存取平均變快幾十倍；賭錯了（故意亂跳的存取），cache 反而添亂（每次都 miss 還多搬了用不到的 line）。下面我們就真跑一個「賭錯」的程式看它慢多少。

## 範例一：同一份資料，換個順序慢 7 倍

這是理解 spatial locality 最直接的實驗。一個 4096×4096 的 `int` 二維陣列（64 MiB，遠大於這台機器 16 MiB 的 L3），我們用兩種順序把每個元素加總：

- **row-major（逐列）**：`A[i][j]` 的 `j` 跑內圈。C 的二維陣列 row-major 佈局，`A[i][0], A[i][1], ...` 記憶體位址連續。每搬進一條 64 B 的 line（16 個 int），接下來 15 次存取都命中。**spatial locality 拉滿。**
- **column-major（逐行）**：`i` 跑內圈。每次 `A[i][j]` 到 `A[i+1][j]` 位址跳 4096×4=16 KiB，遠超一條 line。**每次存取幾乎都 miss，還把整條 line 搬進來卻只用了 4 B。**

```c
#include <stdio.h>
#include <time.h>

#define N 4096  /* 4096x4096 int = 64 MiB，遠大於 32 KiB L1 與 16 MiB L3 */
static int A[N][N];

static double now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000.0 + ts.tv_nsec / 1e6;
}

int main(void) {
    for (int i = 0; i < N; i++)          /* 先填真實資料，逼每個元素被真的讀出 */
        for (int j = 0; j < N; j++)
            A[i][j] = (i * 31 + j) & 0xff;

    double t0 = now_ms();
    volatile long sum1 = 0;
    for (int i = 0; i < N; i++)          /* row-major：位址連續 */
        for (int j = 0; j < N; j++)
            sum1 += A[i][j];
    double t1 = now_ms();

    volatile long sum2 = 0;
    for (int j = 0; j < N; j++)          /* col-major：每次跳 16 KiB */
        for (int i = 0; i < N; i++)
            sum2 += A[i][j];
    double t2 = now_ms();

    printf("row-major (cache-friendly):   %8.1f ms\n", t1 - t0);
    printf("col-major (cache-unfriendly): %8.1f ms\n", t2 - t1);
    printf("slowdown: %.1fx\n", (t2 - t1) / (t1 - t0));
    return 0;
}
```

`sum1`、`sum2` 宣告成 `volatile` 是為了阻止編譯器把整個迴圈最佳化掉；`-fno-tree-vectorize` 是為了關掉自動向量化，讓我們量的是記憶體行為而不是 SIMD。真跑（`gcc -O2 -fno-tree-vectorize`，同一台機器連跑兩次）：

```
row-major (cache-friendly):       21.9 ms  sum=2139095040
col-major (cache-unfriendly):    152.4 ms  sum=2139095040
slowdown: 7.0x
--- run2 ---
row-major (cache-friendly):       22.0 ms
col-major (cache-unfriendly):    153.5 ms
slowdown: 7.0x
```

**同一份資料、同樣加總 1600 萬個 int、算出同樣的和，只因為遍歷順序不同，慢了 7 倍。** 這 7 倍不是玄學，是每條 64 B line 你用 16 個 int（row）還是只用 1 個就丟掉（col）的差別。cache 的存在讓 row-major 快，也讓寫程式的人必須懂 locality——不然你會在不知不覺中把程式寫慢 7 倍。

> 為什麼不是慢 200 倍（DRAM/L1 的比值）？因為即使是 col-major，硬體的 prefetcher 還是猜到了部分 stride 提前搬了一些 line，而且 row 之間偶爾有 L2/L3 命中。7 倍是「大部分 miss」對「幾乎全命中」的實測綜合結果，不是理論極值。實測往往比純理論溫和，因為真實硬體有一堆你沒寫進模型的幫手（prefetcher、多層 cache）。

## 範例二：把記憶體階層的每一層延遲量出來

範例一證明 locality 有影響，但沒告訴我們「每一層到底多快」。要量出階層本身，我們用 **pointer chasing（指標追逐）**：一個陣列，每格存「下一格要跳到哪」，形成一個環。存取完全序列相依（下一次的位址要等這一次讀出來才知道），這會**打死 prefetcher**——它猜不到你下一步跳哪。於是每次存取的耗時就直接反映「這個 working set 落在哪一層」。

```c
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

/* 建一個 stride=一條 line 的環狀 permutation，逼每步一次 miss */
static double ns_per_access(int *arr, long n, long iters) {
    long stride = 64 / sizeof(int);              /* 16：跨一條 cache line */
    for (long i = 0; i < n; i++) arr[i] = (i + stride) % n;
    struct timespec a, b;
    clock_gettime(CLOCK_MONOTONIC, &a);
    long p = 0;
    for (long i = 0; i < iters; i++) p = arr[p];  /* 序列相依：打死 prefetcher */
    clock_gettime(CLOCK_MONOTONIC, &b);
    volatile long sink = p; (void)sink;
    double ns = (b.tv_sec - a.tv_sec) * 1e9 + (b.tv_nsec - a.tv_nsec);
    return ns / iters;
}

int main(void) {
    long sizes[] = {8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 65536};
    printf("working set   ns/access\n");
    for (int k = 0; k < (int)(sizeof(sizes)/sizeof(sizes[0])); k++) {
        long bytes = sizes[k] * 1024, n = bytes / sizeof(int);
        int *arr = malloc(bytes);
        printf("%7ld KiB   %6.2f ns\n", sizes[k], ns_per_access(arr, n, 50000000));
        free(arr);
    }
    return 0;
}
```

真跑（`gcc -O2`）：

```
working set   ns/access
      8 KiB     1.11 ns
     16 KiB     1.11 ns
     32 KiB     1.12 ns      ← L1d 邊界（此機 L1d=32 KiB）
     64 KiB     2.65 ns      ← 掉出 L1，落到 L2：延遲跳升
    128 KiB     2.72 ns
    256 KiB     2.91 ns
    512 KiB     3.03 ns
   1024 KiB     3.07 ns
   2048 KiB     3.11 ns      ← L2 邊界（此機 L2=2 MiB）
   4096 KiB     3.15 ns
   8192 KiB     3.96 ns
  16384 KiB     6.22 ns      ← 掉出 L3（此機 L3=16 MiB），落到 DRAM
  65536 KiB     5.87 ns
```

這條曲線就是**記憶體階層的指紋**。它有清楚的階梯：

- **≤ 32 KiB**：~1.1 ns。整個 working set 塞得進 L1d，每次命中 L1。
- **64 KiB ~ 2 MiB**：~2.7~3.1 ns。掉出 L1、命中 L2。延遲跳約 2.5 倍。
- **> 2 MiB**：~3.2 ns 起，到 16 MiB 後跳到 ~6 ns，落到 DRAM。

你不用去查 spec，這支程式自己把每一層的容量與延遲量了出來——階梯的每個轉折點就是一層 cache 的容量，每階的高度就是那層的延遲。**這就是 cache 為什麼分層：** 不是分一層（要嘛全快但小、要嘛全大但慢），而是分多層，讓「小而快」和「大而慢」各司其職，平均下來又快又能裝。

## 底層機制：AMAT——把「值不值得加 cache」算成數字

工程上要不要多加一層 cache、要多大，靠一個公式決定：**AMAT（Average Memory Access Time，平均記憶體存取時間）**。

單層 cache 的 AMAT：

```
AMAT = hit_time + miss_rate × miss_penalty
```

- `hit_time`：命中時要花的時間（cache 本身的存取延遲）。
- `miss_rate`：miss 的比例（= 1 − hit rate）。
- `miss_penalty`：一次 miss 額外要付的代價（去下一層拿的時間）。

舉個具體數字。假設 L1 命中要 1 拍，miss 要去 DRAM 花 100 拍，程式的 L1 miss rate = 5%：

```
AMAT = 1 + 0.05 × 100 = 1 + 5 = 6 拍
```

平均每次存取 6 拍——比沒 cache 的 100 拍好太多，但那 5% 的 miss 就佔了 AMAT 的 5/6。**miss penalty 很大時，miss rate 的每一個百分點都很貴。** 這解釋了為什麼要多層：加一層 L2（命中 10 拍）接在 L1 後面，miss 就不用一路掉到 DRAM：

```
AMAT = L1_hit + L1_miss_rate × (L2_hit + L2_miss_rate × DRAM_penalty)
     = 1 + 0.05 × (10 + 0.5 × 100)     假設 L2 自己 miss rate 50%
     = 1 + 0.05 × 60 = 1 + 3 = 4 拍
```

從 6 拍降到 4 拍。多這層 L2 值不值得？AMAT 直接告訴你：省了 2 拍。這就是架構師決定 cache 層數與大小的量化依據——不是拍腦袋，是把 hit rate、各層延遲、penalty 代進公式比大小。

範例二那條延遲曲線，本質就是不同 working set 下 AMAT 的實測值：working set 小 → miss rate 低 → AMAT 接近 L1 hit time（1.1 ns）；working set 大到掉出所有 cache → miss rate 高 → AMAT 逼近 DRAM latency（6 ns）。**公式和實測對得上。**

## 對比取捨：為什麼分這幾層

| 層級 | 典型容量 | 典型延遲 | 為什麼是這個定位 |
|---|---|---|---|
| register | 32 個（RV32I） | 0 拍 | 直接接 ALU，最快但極少，compiler 精打細算地用 |
| L1 (I/D 分開) | 16~64 KiB | ~1 ns / 3~4 拍 | 每拍都要碰，必須極快，所以小；I/D 分開避免搶 port |
| L2 | 256 KiB~2 MiB | ~3 ns / ~10 拍 | 接住 L1 miss，容量換速度，通常 unified |
| L3 (共享) | 幾~幾十 MiB | ~10 ns / ~30 拍 | 多核共享，接住 L2 miss，減少上 DRAM |
| DRAM | 幾~幾百 GiB | ~60~100 ns / ~200 拍 | 主記憶體，大但慢，就是那道牆 |
| SSD/disk | 幾百 GB~TB | ~10 µs~ms | 掉出 DRAM（page fault）才碰，慢到要作業系統介入（Ch 28） |

要點：**沒有「又大又快又便宜」的記憶體**，這是物理與成本的硬約束。分層是工程上的妥協——用小而快的接住大部分存取，用大而慢的當後盾，靠 locality 讓「大部分」真的是大部分。我們這門課的 core 要加的是最靠近 CPU 的 L1（Ch 26 I-cache、Ch 27 D-cache），因為它對 pipeline 的影響最直接。

## 踩雷區

**雷 1：以為「cache 是硬體自動的，我寫程式不用管」。**
- 錯誤直覺：「cache 是 CPU 的事，透明的，跟我的 C code 無關」。
- 正確認識：cache 對正確性透明，但對**效能絕不透明**。範例一同一份資料換個迴圈順序慢 7 倍，就是你的 code 直接決定了 hit rate。寫出對 cache 友善的存取模式（連續遍歷、struct-of-arrays、blocking/tiling）是效能工程的基本功。「cache 自動」只保證結果對，不保證快。

**雷 2：以為 cache 一次只搬你要的那幾個 byte。**
- 錯誤直覺：「我讀 `A[0]` 一個 int，cache 就搬 4 B 進來」。
- 正確認識：cache 以 **line/block（通常 64 B）** 為單位搬。你讀 `A[0]`，它把包含 `A[0]` 的整條 64 B line（`A[0..15]`）一起搬進來。這是 spatial locality 的實作機制，也是為什麼「連續存取快、亂跳存取慢」——亂跳時你為每個元素搬一整條 line 卻只用其中 4 B，頻寬全浪費。範例一 col-major 慢就是這個原因。

**雷 3：把 latency（延遲）和 bandwidth（頻寬）搞混。**
- 錯誤直覺：「DDR5 頻寬幾十 GB/s，記憶體很快啊」。
- 正確認識：頻寬（每秒搬多少）和延遲（一次要等多久）是兩回事。DRAM 頻寬可以靠加寬 bus、加 channel 堆得很高，但**單次隨機存取的延遲**卡在 50~100 ns 動不了——memory wall 是延遲的牆，不是頻寬的牆。範例二用序列相依的 pointer chasing 量的正是延遲：每步都要等上一步結果，頻寬再高也幫不上忙。cache 主要解決的是延遲問題。

**雷 4：以為 miss rate 低（例如 5%）就沒事。**
- 錯誤直覺：「95% 命中，很好了，miss 那 5% 不重要」。
- 正確認識：算 AMAT 你就知道 miss 有多貴。hit 1 拍、miss penalty 100 拍時，`AMAT = 1 + 0.05×100 = 6`——那 5% 的 miss 貢獻了 AMAT 的 5/6。**miss penalty 愈大，低 miss rate 也可能主宰效能。** 這就是為什麼架構師拚命把 miss rate 再往下壓（更大的 cache、更好的替換、prefetch），因為 penalty 那一端降不下來，只能從 rate 這端使力。

## 進階延伸

- **3C 模型（三種 miss）**：miss 可分成 compulsory（冷啟動，第一次碰這條 line，任何 cache 都躲不掉）、capacity（cache 裝不下整個 working set）、conflict（映射衝突，即使沒滿也互踢，direct-mapped 特別嚴重）。這是 Ch 26 分析 hit rate 的框架，也是判斷「該加大 cache 還是加關聯度」的依據。範例二延遲曲線的每個階梯轉折，就是 working set 超過某層容量時 capacity miss 爆發的點。
- **prefetching（預取）**：硬體 prefetcher 偵測到 stride pattern（例如連續 +64 B）會提前把後面的 line 搬進來，隱藏延遲。這是為什麼範例一 col-major 只慢 7 倍而非 200 倍——prefetcher 救了一部分。範例二用序列相依 pointer chasing 就是為了關掉 prefetcher 的幫忙，量到「純」延遲。真實 core 的 prefetcher 是一大塊獨立邏輯，本課 L1 設計不含它，但你要知道它在真實效能裡舉足輕重。
- **cache 對 memory model 的影響**：多核時每顆核有自己的 L1，同一份資料可能有多份副本，要靠 cache coherence 協定（MESI/MOESI）維持一致——這是《A Primer on Memory Consistency and Cache Coherence》整本書在講的事，也是 Ch 27 結尾會淺提、留給進階的方向。單核（本課主線）沒這問題，但你一旦想做多核就繞不開。
- **為什麼 I-cache 和 D-cache 分開（Harvard）**：L1 通常拆成 I-cache（放指令）和 D-cache（放資料），因為 pipeline 同一拍既要 fetch 指令（IF 級）又要 load/store 資料（MEM 級），兩者若共用一個 cache 會 structural hazard 搶 port。分開就能同拍各取所需。這是 Ch 26 先做 I-cache、Ch 27 再做 D-cache 的原因，也對應你在 Ch 19 學過的 structural hazard。

## 本章重點整理

- **memory wall**：CPU 每拍能吃一條指令，DRAM 一次存取要 ~200 拍，兩者速度差達兩三個數量級且延遲難降。cache 的全部目的是讓大多數存取不必碰這道牆。
- **兩種 locality**：temporal（剛存過的馬上又存）、spatial（存過的附近也會存）。cache 用「存過就留著」吃前者，用「一次搬一整條 line」吃後者。
- **實測 locality 的代價**：同一份 64 MiB 資料，row-major 對 col-major 遍歷慢 7 倍（21.9 ms vs 152.4 ms），純因存取順序破壞 spatial locality。
- **記憶體階層是指紋**：pointer-chasing 延遲曲線量出 L1(~1.1 ns/≤32 KiB)、L2(~3 ns/≤2 MiB)、DRAM(~6 ns/>16 MiB) 的清楚階梯，證明「小而快 + 大而慢」的分層設計。
- **AMAT = hit_time + miss_rate × miss_penalty**：把「加一層 cache 值不值得」算成一個可比較的數字。miss penalty 大時，低 miss rate 也可能主宰效能。

## 自我檢核

- [ ] 我能說出 memory wall 是什麼、成因（CPU/DRAM 兩條成長曲線）、以及為什麼是延遲而非頻寬的牆。
- [ ] 我能分辨 temporal 和 spatial locality，並各舉一個程式裡的實例，說出 cache 分別用什麼機制吃它們。
- [ ] 我能解釋範例一 row/col-major 為什麼差 7 倍，關鍵在 64 B line 被用滿還是只用 4 B 就丟。
- [ ] 我能看懂範例二的延遲階梯，指出每個轉折點對應哪一層 cache 的容量。
- [ ] 我能寫出 AMAT 公式，代入數字算單層與雙層 cache 的 AMAT，並解釋為什麼 miss penalty 大時 miss rate 很關鍵。
- [ ] 我能反駁「cache 是硬體自動的、寫程式不用管」這個說法。

## 延伸閱讀

- **《Computer Organization and Design, RISC-V Edition》(Patterson & Hennessy) 第 5.1~5.3 節「Introduction / The Basics of Caches / Measuring and Improving Cache Performance」**：本章的教科書主線。5.1 講 locality 與階層、5.3 把 AMAT 與 CPU time 的關係推導得很完整（含把 miss 併進 CPI 的算法），是你把「cache 效能」接回 Ch 23 CPI 分析的橋。讀它把 AMAT 從一個公式變成能算整支程式的工具。
- **Wulf & McKee, "Hitting the Memory Wall: Implications of the Obvious" (ACM SIGARCH Computer Architecture News, 1995)**：memory wall 這個詞的出處，只有兩頁。讀它看當年他們怎麼從「CPU 55%/年、DRAM 7%/年」兩條曲線外推出「牆遲早撞上」，理解這不是新問題而是三十年的老命題。
- **Ulrich Drepper, "What Every Programmer Should Know About Memory" (2007, LWN 系列)**：從程式員視角把 cache/DRAM/NUMA 講到骨子裡的長文。第 3、5 節（CPU caches、NUMA）尤其該讀，它的 pointer-chasing 量測方法就是本章範例二的原型。你想寫出 cache 友善的 code，這是最實用的一份。
- **《A Primer on Memory Consistency and Cache Coherence》(Nagarajan, Sorin, Hill, Wood) 第 1~2 章**：本章結尾提到的多核 coherence 方向。第 1 章先把「為什麼多核 cache 會不一致」講清楚，是你單核做完想跨到多核時的起點。現在讀個開頭建立問題意識即可，深入等你真的要做多核。

下一章我們把「cache」從概念變成 SystemVerilog：切 tag/index/offset、做出一個 direct-mapped I-cache，灌真實的取指 trace 進去量 hit rate，親眼看到 compulsory miss 之後全命中。

→ [Ch 26 Cache 設計：direct-mapped / set-associative，實作 I-cache](./26-cache-design-icache.md)
