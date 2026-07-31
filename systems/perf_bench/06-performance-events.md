# Ch 6 — Performance events：IPC / cache miss / branch miss

> **目標**：把 Ch 5 的微架構概念變成可測量的數字——硬體效能計數器（PMU）怎麼測 IPC、cache miss、branch miss，`perf stat` 的核心事件，組合成高階指標（IPC、cache hit rate、branch miss rate），以及 top-down 分析（把瓶頸分類成 front-end/back-end/retiring/bad-speculation）。這是「從效能數字定位瓶頸」的核心技能。

> **環境**：Linux，perf（Ch 0）。需要 perf_event_paranoid 設定或 sudo。

## 為什麼效能計數器是「硬體真相」？

Ch 5 講了微架構概念（IPC、cache miss、branch miss）——但怎麼**實際測量**它們？答案是 **PMU（Performance Monitoring Unit）**——CPU 內建的硬體計數器，能精確計數「執行了幾條指令、幾個 cycle、幾次 cache miss、幾次 branch miss」。`perf` 讀這些計數器，給你「硬體層的真相」。

這比「猜程式哪裡慢」精確得多——PMU 直接告訴你「這段程式 IPC 0.3（在等）、L2 miss rate 40%（記憶體瓶頸）、branch miss 8%（分支問題）」。有了這些數字，你能精確定位瓶頸（是 cache 問題、分支問題、還是計算問題）並針對性優化。這章把 Ch 5 的概念變成可測量的數字，教你用 perf stat 測量並解讀——這是效能分析從「概念」到「實作」的關鍵。

## 先建立直覺:CPU 的內建碼錶

```
PMU（效能監測單元）= CPU 內建的「碼錶群」

  CPU 內建一組硬體計數器，能精確計數各種事件：
    instructions：執行了幾條指令
    cycles：經過幾個 CPU 週期
    cache-misses：cache 失誤幾次
    branch-misses：分支猜錯幾次
    ... 還有幾百種事件
        │
  perf 設定這些計數器、跑程式、讀結果：
    perf stat ./prog
    → 印出各事件的計數和組合的指標（IPC 等）
        │
  特點：
    硬體計數（精確、低開銷）—— 不像 ptrace 攔截（慢）
    這就是 perf 為什麼低開銷（Ch 12 of observability_tools）
        │
  → PMU 是「硬體層的真相」
    精確告訴你 CPU 實際做了什麼（多少指令、多少 miss）
    比軟體測量精確、低開銷
```

關鍵心智：**PMU** 是 CPU 內建的「碼錶群」——硬體計數器，精確計數各種事件（指令數、cycle、cache miss、branch miss…幾百種）。`perf stat` 設定計數器、跑程式、讀結果。它是硬體計數（精確、低開銷），給你「硬體層的真相」——比軟體測量精確。

> 這章把 Ch 5 的微架構概念（IPC/cache/branch）變成 perf 測量的數字。如果對微架構概念不熟，回看 [Ch 5](./05-microarch-primer.md)。

## perf stat:核心事件

```bash
cd ~/perflab
gcc -g -O2 demo.c -o demo 2>/dev/null || cat > demo.c <<'EOF'
#include <stdio.h>
long compute(long n) { long s=0; for(long i=0;i<n;i++) s += i*i%13; return s; }
int main() { printf("%ld\n", compute(200000000L)); return 0; }
EOF
gcc -g -O2 demo.c -o demo

# === perf stat：核心效能事件 ===
perf stat ./demo
#  Performance counter stats for './demo':
#    250.00 msec task-clock
#    800,000,000  cycles                    ← CPU 週期數
#  2,400,000,000  instructions   # 3.00 insn per cycle    ← IPC = 3.0（好！）
#     50,000,000  branches
#        500,000  branch-misses  # 1.00% of all branches  ← branch miss rate
#     10,000,000  cache-references
#        200,000  cache-misses   # 2.00% of all cache refs ← cache miss rate
#        │
#  關鍵指標（perf 自動算）：
#    IPC（insn per cycle）：3.0 = 好；<1 = CPU 在等
#    branch-miss rate：分支猜錯率（高 = 分支問題）
#    cache-miss rate：cache 失誤率（高 = 記憶體問題）

# === 指定要測的事件 ===
perf stat -e instructions,cycles,cache-misses,branch-misses ./demo
perf stat -e L1-dcache-loads,L1-dcache-load-misses ./demo   # L1 資料 cache
perf stat -e LLC-loads,LLC-load-misses ./demo               # 最後一層 cache（最致命）

# === 看有哪些事件可測 ===
perf list | head -30    # 列出 PMU 支援的事件
```

> **`perf stat` 的 IPC、cache-miss rate、branch-miss rate 是定位瓶頸的核心三指標——它們直接對應 Ch 5 的 IPC 殺手**。`perf stat ./prog` 一個命令給你最重要的效能指標：**IPC**（insn per cycle，Ch 5——3.0 是好、<1 表示 CPU 在等）；**branch-miss rate**（分支猜錯率——高表示有難預測的分支）；**cache-miss rate**（cache 失誤率——高表示記憶體瓶頸）。這三個直接對應 Ch 5 的「IPC 殺手」——看 IPC 知道「CPU 有沒有在有效工作」，看 cache-miss 和 branch-miss 知道「如果 IPC 低，是哪個原因」。**定位流程**：先看 IPC——如果低（<1），程式在等；然後看 cache-miss（高 = 等記憶體）和 branch-miss（高 = 分支問題）找出原因。`perf stat -e <事件>` 指定要測的事件——特別有用的是 **LLC（最後一層 cache）miss**（`LLC-load-misses`——LLC miss 最致命，要去 RAM 等幾百 cycle，Ch 5）。`perf list` 列出 PMU 支援的所有事件（幾百種，不同 CPU 不同）。注意 perf stat 預設多次跑取統計（可加 `-r N` 指定次數，配合 Ch 4 的統計嚴謹）。這是效能分析的起手式——**先 `perf stat` 看整體指標（IPC/cache/branch），判斷瓶頸大方向**，再深入（perf record 找熱點函式 Ch 7、cachegrind 精確分析 cache Ch 17）。這把 Ch 5 的概念變成了可操作的測量——你不再「猜」程式哪裡慢，而是用 PMU 精確測量。

## 組合成高階指標

```bash
# 從原始事件計算有意義的指標
cat > metrics.sh <<'EOF'
#!/bin/bash
# 跑 perf stat 並計算高階指標
perf stat -e instructions,cycles,cache-references,cache-misses,branches,branch-misses "$@" 2>&1 | \
awk '
/instructions/ { insn = $1 }
/cycles/ { cyc = $1 }
/cache-references/ { cref = $1 }
/cache-misses/ { cmiss = $1 }
/branches/ { br = $1 }
/branch-misses/ { brmiss = $1 }
END {
    gsub(/,/, "", insn); gsub(/,/, "", cyc); ...
    printf "IPC: %.2f\n", insn/cyc
    printf "Cache miss rate: %.1f%%\n", cmiss/cref*100
    printf "Branch miss rate: %.1f%%\n", brmiss/br*100
}'
EOF
```

```
高階指標的解讀（怎麼判斷瓶頸）：

  IPC：
    > 2.0   好（superscalar 發揮）
    1.0-2.0 普通
    < 1.0   差（CPU 在等，要找原因）
        │
  Cache miss rate：
    L1 miss < 5%   通常 OK
    LLC miss 高    → 記憶體 bound（去 RAM 等很久）
        │
  Branch miss rate：
    < 2%   好（分支可預測）
    > 5%   差（有難預測的分支）
        │
  → 組合判斷瓶頸：
    IPC 低 + cache miss 高 → 記憶體瓶頸（優化 locality）
    IPC 低 + branch miss 高 → 分支瓶頸（規律化/branchless）
    IPC 低 + 兩者都低 → 資料相依/執行單元（增加平行）
    IPC 高 → CPU 有效工作（瓶頸可能是「指令太多」，減少工作）
```

> **組合 IPC、cache-miss、branch-miss 判斷瓶頸類型——這是「從數字到優化方向」的關鍵推理**。單看一個指標不夠，要**組合判斷**：**IPC 低 + cache miss 高** → **記憶體瓶頸**（CPU 在等 RAM，優化方向是改善 cache locality——資料佈局、存取模式、減少 working set，Ch 17 cachegrind）；**IPC 低 + branch miss 高** → **分支瓶頸**（分支難預測，優化方向是規律化分支或 branchless 技巧）；**IPC 低 + 兩者都不高** → 可能是**資料相依**（指令鏈相依，沒平行度）或執行單元不足（優化方向是增加指令級平行、向量化）；**IPC 高** → CPU **有效工作**（瓶頸不在等，可能是「指令太多」——優化方向是減少工作量、更好的演算法、減少指令數）。這個推理是效能分析的核心——**從 perf 的數字推斷「瓶頸是什麼類型」，進而知道「該往哪個方向優化」**。不懂這個推理，你看到 perf 數字也不知道怎麼用；懂了，你能從「IPC 0.4、LLC miss 30%」立刻推斷「記憶體瓶頸，去優化 cache locality」。這是 perf_bench 的核心技能——**用效能數字驅動優化決策**（資料驅動，而非瞎猜）。對 compiler 工作，這也指導「該加什麼優化」（Ch 14）——cache bound 的 workload 受益於改善記憶體存取的優化（如 loop tiling）、branch bound 的受益於分支優化（如 PGO）、compute bound 的受益於向量化。理解「數字 → 瓶頸類型 → 優化方向」的推理鏈，是把效能分析從觀察變成行動的關鍵。

## top-down 分析

```
top-down 分析：系統化地把瓶頸分類（Intel 提出的方法）

  把每個 cycle 分成四類（pipeline slot 的去向）：
        │
  Retiring（有用工作）：
    cycle 用在「真的完成指令」→ 好（這是想要的）
        │
  Bad Speculation（壞推測）：
    cycle 浪費在「猜錯的分支」（推測執行後清掉）→ 分支問題
        │
  Front-End Bound（前端瓶頸）：
    cycle 浪費在「取指/解碼跟不上」→ I-cache miss、分支預測
        │
  Back-End Bound（後端瓶頸）：
    cycle 浪費在「執行被卡」→ D-cache miss、執行單元不足
        │
  → top-down 告訴你「cycle 浪費在哪一類」
    一眼看出瓶頸是 front-end、back-end、還是 bad speculation
    然後針對那一類深入（如 back-end bound → 看是 cache 還執行單元）
        │
  perf 支援 top-down（新的 CPU）：
    perf stat -M Frontend_Bound,Backend_Bound,Bad_Speculation,Retiring ./prog
    或 perf stat --topdown ./prog
```

```bash
# top-down 分析（如果 CPU 支援）
perf stat --topdown ./demo 2>/dev/null || \
perf stat -M Retiring,Bad_Speculation,Frontend_Bound,Backend_Bound ./demo
# 顯示各類的百分比：
# Retiring: 60%  Backend_Bound: 30%  Frontend_Bound: 5%  Bad_Speculation: 5%
# → 60% 有用工作、30% 後端瓶頸（cache/執行單元）→ 往 back-end 深入
```

> **top-down 分析把每個 cycle 分成四類（Retiring/Bad Speculation/Front-End/Back-End）——一眼看出瓶頸的大類，是現代效能分析的方法論**。**top-down 分析**（Intel 提出，現代效能分析的標準方法）系統化地把 CPU 的每個 cycle 分類——看「cycle 浪費在哪」：**Retiring**（有用工作——cycle 真的在完成指令，這是想要的，越高越好）；**Bad Speculation**（壞推測——浪費在猜錯的分支，對應 branch misprediction）；**Front-End Bound**（前端瓶頸——取指/解碼跟不上，如 I-cache miss、分支預測問題）；**Back-End Bound**（後端瓶頸——執行被卡，如 D-cache miss、執行單元不足）。這四類的百分比一眼告訴你**瓶頸的大類**——如果 Back-End Bound 高，瓶頸在執行（再深入看是 cache 還是執行單元）；Front-End Bound 高，瓶頸在指令供給；Bad Speculation 高，分支問題。這比單看個別事件**更系統化**——它是「層級式」的分析（先分大類，再往瓶頸的類深入），不會漏掉或誤判。`perf stat --topdown`（新 CPU 支援）或 `-M` 指定 metric 能做 top-down。這是和硬體團隊對話的共同語言（「我們的 workload 是 60% retiring、30% back-end bound，主要是 D-cache miss」）。對 compiler 工作，top-down 指導優化——back-end bound（cache）的 workload 受益於記憶體優化、front-end bound 受益於 code layout 優化（如 BOLT，Ch 11）、bad speculation 受益於分支優化（PGO）。理解 top-down，你的效能分析就有了系統化的框架——從「整體 cycle 怎麼花的」逐層深入到「具體的瓶頸」。這是 Ch 5 微架構概念的綜合應用，也是現代效能分析的標準方法論。Part 2（硬體事件）到此，你能用 PMU 測量並用 top-down 系統化地定位瓶頸。

## 故意弄壞:cache-bound vs compute-bound

```bash
cd ~/perflab
# 製造 cache-bound 和 compute-bound 的程式，用 perf 區分
cat > bound.c <<'EOF'
#include <stdlib.h>
#include <stdio.h>
#define N 16000000
int main(int argc, char **argv) {
    int *arr = malloc(N * sizeof(int));
    long sum = 0;
    if (argv[1][0] == 'c') {
        // cache-bound：隨機存取大陣列（大量 cache miss）
        for (int i = 0; i < N; i++) {
            int idx = (i * 2654435761U) % N;   // 隨機跳（cache 不友善）
            sum += arr[idx];
        }
    } else {
        // compute-bound：循序存取 + 大量計算（cache 友善，計算多）
        for (int i = 0; i < N; i++) {
            sum += arr[i] * arr[i] % 13;       // 循序 + 計算
        }
    }
    printf("%ld\n", sum);
    free(arr);
    return 0;
}
EOF
gcc -O2 bound.c -o bound

# cache-bound（隨機存取）
echo "=== cache-bound（隨機存取）==="
perf stat -e instructions,cycles,cache-misses ./bound c 2>&1 | grep -E 'insn per|cache-misses'
# IPC 低（如 0.5）、cache-misses 高 → 記憶體瓶頸（在等 RAM）

# compute-bound（循序+計算）
echo "=== compute-bound（循序+計算）==="
perf stat -e instructions,cycles,cache-misses ./bound x 2>&1 | grep -E 'insn per|cache-misses'
# IPC 高（如 2.5）、cache-misses 低 → CPU 有效工作（瓶頸是計算量）

# → 同樣遍歷一個陣列，但：
#   cache-bound：IPC 低 + cache miss 高（優化方向：改善 locality）
#   compute-bound：IPC 高 + cache miss 低（優化方向：減少計算/向量化）
#   perf 讓你「區分瓶頸類型」，知道往哪優化
```

> **perf 區分 cache-bound（IPC 低+cache miss 高）和 compute-bound（IPC 高+cache miss 低）——這決定優化方向**。這個實驗展示 perf 最重要的能力——**區分瓶頸類型**。兩個程式都遍歷一個大陣列，但：**cache-bound 版**（隨機存取，cache 不友善）→ perf 顯示 **IPC 低 + cache miss 高**（CPU 在等 RAM）→ 優化方向是**改善 cache locality**（循序存取、資料佈局）；**compute-bound 版**（循序存取 + 大量計算）→ perf 顯示 **IPC 高 + cache miss 低**（CPU 有效工作，瓶頸是計算量）→ 優化方向是**減少計算或向量化**（用 SIMD 一次算多個）。**這個區分至關重要**——如果你對 cache-bound 的程式做「向量化」優化，沒用（瓶頸是等記憶體不是計算）；如果你對 compute-bound 的程式優化 cache，也沒用（cache 已經很好）。**用對的優化要先知道瓶頸類型**，而 perf 讓你精確判斷。這是 perf_bench 的核心——**資料驅動的優化**：用 perf 測量瓶頸類型，針對性優化，而非瞎猜或套用通用優化。對 compiler 工作，這指導「該對什麼 workload 加什麼優化」——cache-bound 的受益於記憶體優化（loop tiling、prefetch）、compute-bound 的受益於向量化和指令排程。理解「perf 區分瓶頸類型 → 決定優化方向」，你的優化就有的放矢。這也呼應 Ch 17 的「按行 vs 按列」（cache locality 對效能的影響）——現在你能用 perf 量化它（cache miss rate）。Part 2 完成，你能測量微架構事件並判斷瓶頸——這是 Part 3（profiling 工具深入）和 Part 4（compiler 優化）的基礎。

## 動手練習

1. perf stat：對一個程式 `perf stat`，看 IPC/cache-miss/branch-miss，判斷瓶頸

2. 指定事件：用 `-e` 測 LLC-load-misses（最致命的 cache miss），看你的程式

3. top-down：用 `perf stat --topdown`（或 -M）看瓶頸分類（retiring/back-end/front-end/bad-spec）

4. 高階指標：寫腳本從原始事件算 IPC/cache-miss-rate/branch-miss-rate

5. 跑「故意弄壞」：對 cache-bound 和 compute-bound 程式測 perf，看 IPC/cache-miss 的差別，判斷優化方向

## 本章重點整理

- PMU（硬體效能計數器）精確計數指令/cycle/cache-miss/branch-miss；perf 讀它，低開銷且精確
- perf stat 核心三指標：IPC（CPU 有沒有在等）、cache-miss rate（記憶體瓶頸）、branch-miss rate（分支問題）
- 組合判斷瓶頸：IPC 低+cache miss 高=記憶體 bound、IPC 低+branch miss 高=分支 bound、IPC 高=有效工作
- top-down 分析把 cycle 分四類（Retiring/Bad Speculation/Front-End/Back-End），系統化定位瓶頸大類
- perf 區分 cache-bound vs compute-bound，決定優化方向（locality vs 向量化）——資料驅動的優化

## 自我檢核

- [ ] 知道 PMU 是什麼，perf stat 怎麼測效能事件
- [ ] 會用 IPC/cache-miss/branch-miss 判斷瓶頸類型
- [ ] 理解 top-down 分析的四類，會用它定位瓶頸
- [ ] 能區分 cache-bound 和 compute-bound，知道各自的優化方向
- [ ] 理解「perf 數字 → 瓶頸類型 → 優化方向」的推理鏈

## 延伸閱讀

### 必讀

- **[Top-down Microarchitecture Analysis](https://www.intel.com/content/www/us/en/develop/documentation/vtune-cookbook/top/methodologies/top-down-microarchitecture-analysis-method.html)** — Intel
  - **這篇說什麼**：top-down 分析方法論的原始來源
  - **為什麼值得讀**：本章 top-down 的權威

- **《Performance Analysis and Tuning on Modern CPUs》— Ch 4-6** — Denis Bakhvalov
  - **讀哪幾章**：Ch 4（PMU/效能計數器）、Ch 6（top-down）
  - **這本書的定位**：效能計數器和 top-down 的最佳實用版
  - **連結**：免費 PDF

### 文章

- **[perf stat 詳解](https://www.brendangregg.com/blog/2017-05-09/cpu-utilization-is-wrong.html)** — Brendan Gregg
  - **這篇說什麼**：用 perf 的效能事件分析（含 IPC 的重要性）
  - **為什麼值得讀**：本章 perf stat 的實戰補充

### 官方文件

- **[perf-stat(1)](https://man7.org/linux/man-pages/man1/perf-stat.1.html)** — perf
  - **讀哪裡**：事件選擇、-M metric
  - **為什麼值得讀**：perf stat 的權威

Part 2（硬體事件與效能計數器）到此完成。接下來 Part 3 深入 profiling 工具——perf record/report（找熱點）、llvm-mca（靜態分析）、flamegraph（視覺化）。從「整體指標」到「定位熱點」。

→ [Ch 7 perf record / perf report 實戰](./07-perf-tool.md)
