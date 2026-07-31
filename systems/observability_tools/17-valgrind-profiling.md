# Ch 17 — valgrind profiling（callgrind/cachegrind/massif）

> **目標**：用 valgrind 的 profiling 工具做「精確」的效能分析——callgrind（精確的指令計數 + 呼叫圖）、cachegrind（快取命中/失誤模擬）、massif（heap 記憶體用量 profiling）。理解它們和 perf（Ch 12）的根本差別：perf 是「取樣」（快但不精確），valgrind 是「精確計數」（慢但精確且可重現）。以及 cache miss 為什麼是效能殺手、massif 怎麼找記憶體用量的成長。從「找熱點」（perf）到「精確分析為什麼慢」。

> **環境**：Linux，valgrind，kcachegrind（視覺化，選裝）。`gcc -g`。

## 為什麼需要 valgrind profiling？

Ch 12 的 perf 用取樣找熱點——快、低開銷、能用於生產。但取樣有兩個限制：**不精確**（取樣可能漏，結果每次略不同）、**難重現**（依賴執行時的時序）。有時你需要**精確且可重現**的分析——「這個函式精確被呼叫幾次」「精確哪一行最慢」「cache miss 在哪」。這是 valgrind profiling 的領域。

**callgrind**（精確的指令計數 + 完整呼叫圖）、**cachegrind**（快取行為模擬，看 cache miss）、**massif**（heap 記憶體 profiling，看記憶體用量怎麼成長）——它們用 valgrind 的模擬執行（Ch 15）精確計數每個操作。代價是慢（10-50 倍，和 memcheck 一樣），但給你「精確、可重現、細到指令層」的分析。理解它們和 perf 的取捨，你在不同場景選對工具——快速找熱點用 perf，精確分析用 valgrind。

## 先建立直覺:取樣 vs 精確計數

```
perf（取樣）vs valgrind profiling（精確計數）：

  perf（Ch 12）：取樣
    定期拍快照 → 統計「大概」時間花在哪
    優點：快（低開銷）、能用於生產、看真實的時間（含 IO 等待）
    缺點：不精確（取樣誤差）、難重現（每次略不同）
        │
  valgrind callgrind：精確計數
    模擬執行，「精確數」每個函式被呼叫幾次、執行幾條指令
    優點：精確（不是估計）、可重現（每次一樣）、細到指令/行
    缺點：慢（10-50倍）、不反映真實時間（不含 IO 等待，只算指令）
        │
  → 場景：
    快速找熱點、生產環境、看真實延遲 → perf（取樣）
    精確分析、可重現、細到指令、cache 行為 → valgrind（精確）
        │
  類比：
    perf = 民調（取樣估計，快）
    valgrind = 普查（精確計數，慢）
```

關鍵心智：perf（Ch 12）用**取樣**（快、低開銷、看真實時間，但不精確、難重現）；valgrind profiling 用**精確計數**（模擬執行，精確數每個操作，但慢、不反映真實時間）。場景：快速找熱點/生產用 perf，精確分析/可重現/cache 行為用 valgrind。類比：perf 是民調（取樣估計）、valgrind 是普查（精確計數）。

> valgrind profiling 用 Ch 15 的模擬執行機制（動態插樁），所以一樣慢。它和 perf（Ch 12）互補——取樣 vs 精確。如果對 perf 的取樣不熟，回看 [Ch 12](./12-perf-fundamentals.md)。

## callgrind:精確的呼叫圖

```bash
cd ~/obslab
# 用之前的 slow.c（有熱點 slow_function）
gcc -g -O0 slow.c -o slow

# === callgrind：精確計數 + 呼叫圖 ===
valgrind --tool=callgrind ./slow
# 產生 callgrind.out.<pid>

# 看報告（文字）
callgrind_annotate callgrind.out.*
# Ir         function
# 8,000,000,000  slow_function    ← 精確的指令數（Ir = Instruction reads）
#         ...    main
# → 精確知道每個函式執行了幾條指令（不是 perf 的取樣估計）

# === 視覺化（kcachegrind，最好用）===
# sudo apt install kcachegrind
# kcachegrind callgrind.out.*
# → 圖形化的呼叫圖、每個函式的指令數、source 對照
#   能看「每一行」的指令計數（精確到行）

# === callgrind 的優勢：精確的呼叫關係和計數 ===
# 「函式 A 被呼叫幾次」「A 花的指令裡多少在子函式 B」
# 這些 perf 的取樣難以精確回答
```

> **callgrind 給「精確的指令計數和呼叫圖」——「函式被呼叫幾次、哪一行幾條指令」，比 perf 的取樣估計精確**。callgrind 模擬執行程式，**精確計數**每個函式執行了幾條指令（Ir = Instruction reads）、被呼叫幾次、呼叫了誰。`callgrind_annotate` 顯示文字報告（每個函式的指令數），但最好用的是 **kcachegrind**（視覺化）——它顯示圖形化的呼叫圖、每個函式的指令數、**source 對照（精確到每一行的指令計數）**。callgrind 的優勢是**精確和呼叫關係**：perf 的取樣告訴你「大概 80% 在 slow_function」（估計），callgrind 告訴你「slow_function 精確執行了 80 億條指令、被呼叫 1 次、其中多少在子函式」（精確）。這對需要精確分析的場景有用——「這個函式到底被呼叫幾次」（perf 取樣難精確數呼叫次數）、「哪一行精確最慢」（callgrind 精確到行）、「呼叫關係怎樣」（完整的呼叫圖）。代價是慢（10-50 倍）和「指令數 ≠ 真實時間」（callgrind 算指令，不含 IO 等待和真實的指令耗時差異——一條 cache miss 的指令和 cache hit 的指令在 callgrind 都算一條，但真實耗時差很多，所以要配 cachegrind 看 cache）。**選擇**：快速找熱點、看真實延遲用 perf；精確分析呼叫次數/行級計數/可重現用 callgrind。kcachegrind 的視覺化是 callgrind 的殺手鐧——複雜程式的呼叫圖一目了然。

## cachegrind:快取行為

```bash
# === cachegrind：模擬 CPU 快取，看 cache miss ===
valgrind --tool=cachegrind ./slow
# ==12345== I refs:      8,000,000,000     ← 指令讀取
# ==12345== I1 misses:           1,234     ← L1 指令快取失誤
# ==12345== D refs:      3,000,000,000     ← 資料讀寫
# ==12345== D1 misses:      50,000,000     ← L1 資料快取失誤（高 = 問題！）
# ==12345== LLd misses:     10,000,000     ← 最後一層快取失誤（最慢）
# ==12345== D1 miss rate:        1.6%
# → 看快取命中/失誤率（cache miss 是效能殺手）

cg_annotate cachegrind.out.*    # 看哪一行 cache miss 多

# 為什麼 cache miss 重要：
#   CPU 存取 L1 cache：~1 ns
#   存取主記憶體（cache miss）：~100 ns（慢 100 倍！）
#   → cache miss 多 = 程式花大量時間等記憶體 = 慢
```

```
cache 的層次（為什麼 cache miss 是效能殺手）：

  CPU 暫存器      ~0.3 ns   （最快）
  L1 cache        ~1 ns
  L2 cache        ~4 ns
  L3 cache (LLC)  ~40 ns
  主記憶體 (RAM)  ~100 ns   （cache miss 要來這裡，慢 100 倍！）
        │
  → 程式如果 cache miss 多（資料不在 cache），就一直等記憶體
    cachegrind 模擬 cache，告訴你 miss 率和哪裡 miss
    優化：改善記憶體存取模式（locality）減少 miss
```

> **cache miss 是效能殺手（主記憶體比 L1 慢 100 倍），cachegrind 模擬 cache 告訴你「哪裡 cache miss 多」**。現代 CPU 的瓶頸常常不是「計算」而是「等記憶體」——CPU 存取 L1 cache 約 1ns，但 cache miss 要去主記憶體約 100ns（**慢 100 倍**！）。所以一個程式如果 cache miss 多（資料常不在 cache），CPU 就一直「等記憶體」，即使指令數不多也很慢。這解釋了一個常見現象：兩個演算法指令數差不多，但一個快很多——因為它的記憶體存取模式好（cache 命中率高）。**cachegrind** 模擬 CPU 的快取階層，告訴你 cache miss 率（`D1 miss rate` = L1 資料快取失誤率、`LLd misses` = 最後一層快取失誤——這些 miss 直接對應「等記憶體」的時間）和**哪一行 cache miss 多**（`cg_annotate`）。這對效能優化的進階場景重要——當 perf 顯示某函式慢但「指令不多」，cachegrind 揭示「它 cache miss 多，在等記憶體」。優化方法：改善**記憶體存取的局部性（locality）**——循序存取（而非隨機跳）、把常用資料放近、結構體佈局優化（讓常一起用的欄位在同一 cache line）。經典例子：遍歷二維陣列「按行」（cache 友善）vs「按列」（cache 不友善，每次跳一整行 = miss）——同樣的計算，按行可能快好幾倍。cachegrind 讓你看到「cache 行為」這個 perf 不直接顯示的維度（perf stat 有 cache-misses 計數，但 cachegrind 精確到行）。理解 cache 是效能殺手，你的優化視野就從「減少指令」擴展到「改善記憶體存取模式」——這常是更大的優化空間。

## massif:heap 記憶體 profiling

```bash
# === massif：看 heap 記憶體用量隨時間怎麼變 ===
cat > memgrow.c <<'EOF'
#include <stdlib.h>
#include <unistd.h>
int main() {
    char *blocks[100];
    for (int i = 0; i < 100; i++) {
        blocks[i] = malloc(100000);   // 每次配 100KB，累積
        usleep(10000);
    }
    for (int i = 0; i < 100; i++) free(blocks[i]);
    return 0;
}
EOF
gcc -g memgrow.c -o memgrow

valgrind --tool=massif ./memgrow
# 產生 massif.out.<pid>
ms_print massif.out.*
# 顯示 heap 用量隨時間的圖（ASCII）：
#     MB
# 9.5^                                    ##
#    |                            @@@@@@@@##
#    |                    @@@@@@@@@        ...
#    |            @@@@@@@@@
#    |    @@@@@@@@@
#  0 +-------------------------------------->
#    0                                  時間
# → 看到 heap 記憶體怎麼成長（什麼時候、哪裡分配最多）
#   也告訴你「哪個函式分配最多記憶體」

# massif vs memcheck：
#   memcheck（Ch 15）：找 leak（沒 free 的）
#   massif：看記憶體「用量」（即使有 free，看高峰和成長）
```

> **massif 看「heap 記憶體用量隨時間的變化」——它找「記憶體用太多/成長」，補上 memcheck（找 leak）的另一個記憶體視角**。memcheck（Ch 15）找 **leak**（malloc 沒 free 的），但有時問題不是 leak，而是「記憶體**用量**太高」——程式雖然有 free，但某時刻同時持有太多記憶體（高峰太高），或記憶體用量持續成長（即使有 free，淨用量在漲）。**massif** 是 heap 記憶體的 profiler——它記錄「記憶體用量隨時間怎麼變」（`ms_print` 顯示 ASCII 圖），告訴你「什麼時候用最多、哪個函式分配最多」。這對 debug「為什麼這個程式吃這麼多記憶體」「記憶體高峰在哪」有用（不是 leak，是「同時持有太多」或「某段程式分配特別多」）。massif 和 memcheck 互補——**memcheck 找「沒 free 的」（leak），massif 看「用了多少」（用量/高峰/成長）**。例如一個程式可能沒有 leak（所有 malloc 都 free 了）但記憶體用量很高（某時刻同時 malloc 一堆），massif 揭示這個。這完成了 valgrind 的工具家族：**memcheck**（記憶體錯誤/leak）、**helgrind/drd**（並發）、**callgrind**（呼叫圖/指令）、**cachegrind**（快取）、**massif**（heap 用量）——涵蓋記憶體正確性、並發正確性、效能、記憶體用量。valgrind 是一個「動態插樁框架」，這些是它的不同工具（`--tool=`）。理解整個家族，你知道「不同的 debug 需求用 valgrind 的哪個工具」——記憶體錯誤用 memcheck、race 用 helgrind、精確 profiling 用 callgrind、cache 用 cachegrind、記憶體用量用 massif。

## 故意弄壞:cache 不友善 vs 友善

```bash
cd ~/obslab
# 展示 cache 對效能的影響（同樣計算，記憶體存取模式不同）
cat > cache_demo.c <<'EOF'
#include <stdio.h>
#include <stdlib.h>
#define N 2048
int main(int argc, char **argv) {
    int (*m)[N] = malloc(sizeof(int) * N * N);
    long sum = 0;
    if (argv[1][0] == 'r') {
        // 按行遍歷（cache 友善：循序存取）
        for (int i = 0; i < N; i++)
            for (int j = 0; j < N; j++)
                sum += m[i][j];
    } else {
        // 按列遍歷（cache 不友善：每次跳一整行）
        for (int j = 0; j < N; j++)
            for (int i = 0; i < N; i++)
                sum += m[i][j];
    }
    printf("%ld\n", sum);
    free(m);
    return 0;
}
EOF
gcc -O2 cache_demo.c -o cache_demo

# 比較兩種遍歷的時間（同樣計算！）
time ./cache_demo r    # 按行（cache 友善）—— 快
time ./cache_demo c    # 按列（cache 不友善）—— 慢好幾倍！（同樣的計算）

# 用 cachegrind 看為什麼（cache miss 差異）
valgrind --tool=cachegrind ./cache_demo r 2>&1 | grep 'miss rate'
valgrind --tool=cachegrind ./cache_demo c 2>&1 | grep 'miss rate'
# 按列的 D1 miss rate 高很多 → cache miss 多 → 慢
# → 同樣的計算，記憶體存取模式（cache locality）決定效能！
#   這是 perf 的取樣難看出的「為什麼慢」（cachegrind 精確顯示 cache 行為）
```

> **「按行 vs 按列遍歷陣列」同樣計算但效能差好幾倍——cachegrind 揭示原因是 cache miss，這是「記憶體存取模式決定效能」的經典展示**。這個實驗展示 cache 對效能的巨大影響——**按行遍歷**二維陣列（`m[i][j]`，j 內層）是**cache 友善**的（循序存取記憶體，一個 cache line 載入後連續用，命中率高）；**按列遍歷**（i 內層）是**cache 不友善**的（每次 `m[i][j]` 跳一整行 = N×4 bytes，超過 cache line，每次都 miss）。**同樣的計算**（都是加總所有元素），但按列可能慢好幾倍——純粹因為 cache 行為不同！這顛覆了「指令數決定效能」的直覺——記憶體存取模式（cache locality）常常比指令數更影響效能。`cachegrind` 揭示原因——按列的 D1 miss rate 高很多（cache miss 多 = 一直等記憶體 = 慢）。這是 perf 的取樣**難直接看出**的「為什麼慢」（perf 顯示「這個迴圈慢」，但不直接告訴你「因為 cache miss」——要 perf stat 看 cache-misses 計數，或 cachegrind 精確分析）。這個例子的教訓對效能優化極重要：(1) **記憶體存取模式（cache locality）是效能的關鍵**，常比演算法的指令數更重要；(2) 優化時除了「減少計算」也要「改善記憶體存取」（循序、locality）；(3) cachegrind/perf 的 cache 分析揭示「指令數看不出的效能問題」。這完成了 Part 6 的效能視角——從 perf（找熱點）到 callgrind（精確呼叫圖）到 cachegrind（cache 行為）。你現在有完整的效能分析工具——找熱點、精確計數、cache 分析、記憶體用量。

## 動手練習

1. callgrind：對 slow.c 用 callgrind，用 kcachegrind 看呼叫圖和精確的指令計數

2. perf vs callgrind：同個程式用 perf（取樣）和 callgrind（精確），比較結果，理解取樣 vs 精確

3. cachegrind：對一個程式用 cachegrind，看 cache miss 率，理解 cache miss 是效能殺手

4. massif：對 memgrow.c 用 massif，看 heap 記憶體用量隨時間成長的圖

5. 跑「故意弄壞」：比較按行 vs 按列遍歷的時間和 cache miss，理解記憶體存取模式決定效能

## 本章重點整理

- perf（取樣，快、看真實時間、生產用）vs valgrind profiling（精確計數，慢、可重現、細到指令）——互補
- callgrind：精確的指令計數 + 完整呼叫圖（kcachegrind 視覺化），「函式呼叫幾次、哪行幾條指令」
- cachegrind：模擬 CPU 快取，看 cache miss（主記憶體比 L1 慢 100 倍，cache miss 是效能殺手）
- massif：heap 記憶體用量 profiling（看高峰/成長），補上 memcheck（找 leak）的記憶體用量視角
- 記憶體存取模式（cache locality）常比指令數更影響效能（按行 vs 按列遍歷差好幾倍）

## 自我檢核

- [ ] 理解 perf（取樣）和 valgrind profiling（精確）的取捨，何時用哪個
- [ ] 會用 callgrind + kcachegrind 看精確的呼叫圖和指令計數
- [ ] 知道 cache miss 為什麼是效能殺手，會用 cachegrind 看 cache 行為
- [ ] 知道 massif 看記憶體用量，和 memcheck（找 leak）的差別
- [ ] 理解記憶體存取模式（locality）對效能的影響

## 延伸閱讀

### 官方文件

- **[Callgrind manual](https://valgrind.org/docs/manual/cl-manual.html)** + **[Cachegrind](https://valgrind.org/docs/manual/cg-manual.html)** + **[Massif](https://valgrind.org/docs/manual/ms-manual.html)** — Valgrind
  - **讀哪裡**：各工具的輸出解讀
  - **為什麼值得讀**：三個工具的權威

### 文章

- **[What every programmer should know about memory](https://www.akkadia.org/drepper/cpumemory.pdf)** — Ulrich Drepper
  - **核心貢獻**：CPU cache、記憶體、locality 的權威長文
  - **讀哪裡**：cache 那幾節
  - **為什麼值得讀**：理解 cache 為什麼是效能關鍵的權威，本章 cache 那節的深入版

### 書籍

- **《Systems Performance》— Ch 6 (CPUs)** — Brendan Gregg
  - **讀哪幾章**：CPU cache 和效能那節
  - **為什麼值得讀**：把 cache 放進效能分析的框架

下一章看 sanitizers——編譯時插樁的執行期檢查（ASan/TSan/UBSan/MSan），比 valgrind 快很多的記憶體/並發 bug 偵測。理解它和 valgrind 的取捨（編譯時 vs 執行時插樁）。

→ [Ch 18 sanitizers（ASan/TSan/UBSan/MSan）](./18-sanitizers.md)
