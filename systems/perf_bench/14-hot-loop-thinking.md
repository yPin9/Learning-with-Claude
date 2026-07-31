# Ch 14 — 從 hot loop 倒推「該加什麼 optimization」

> **目標**：本課終章。把前面 13 章的工具整合成一個**思考框架**——給你一個 hot loop，怎麼系統化地判斷「瓶頸是什麼、該加什麼優化、compiler 能不能做」。這是 SiFive job spec「analyze performance results and suggest new compiler optimizations」的直接對應——把效能分析變成「提出 compiler 改進」的能力。讀完你能面對一個 hot loop，系統地分析並提出優化建議。

> **環境**：綜合前面所有工具（perf/llvm-mca/vectorization report 等）。

## 為什麼需要「倒推優化」的思考框架？

前面 13 章給了你工具——測量（hyperfine/perf）、分析（llvm-mca/flame graph）、compiler 優化（flag/PGO/LTO/vectorization）。但工具是零散的。真實的效能工作是：**看到一個 hot loop（profiling 找出的瓶頸），系統化地分析「為什麼慢、該加什麼優化」**。這需要一個**思考框架**——把零散的工具和知識整合成「從 hot loop 到優化建議」的流程。

這正是 SiFive compiler 工程師的核心工作——benchmarking team 找出某個 workload 慢，你要分析「瓶頸是什麼、compiler 該加什麼優化來改善」。這章把前面的一切整合成這個思考框架——給你一個 hot loop，你能系統地走完「測量 → 定位瓶頸 → 判斷優化 → 提出建議」。這是全課的綜合，也是把「效能分析」變成「行動（提出優化）」的關鍵。

## 先建立直覺:從症狀到處方

```
hot loop 優化的思考框架（從症狀到處方）：

  1. 定位 hot loop（profiling）：
     perf 找出「哪個 loop 最花時間」（Ch 7/9）
        │
  2. 判斷瓶頸類型（perf events）：
     IPC、cache miss、branch miss → 是 cache/branch/compute bound？（Ch 6）
        │
  3. 分析 loop 結構（看 code + llvm-mca）：
     有相依嗎？能向量化嗎？分支多嗎？記憶體存取模式？（Ch 8/13）
        │
  4. 對應到優化（瓶頸 → 處方）：
     cache bound → locality 優化、prefetch、loop tiling
     branch bound → PGO、branchless
     相依瓶頸 → unroll + 多累加器
     能向量化但沒 → 幫助向量化（restrict、簡化）
        │
  5. compiler 能做嗎？（提出建議）：
     現有 flag 能做 → 用對的 flag
     compiler 該加的優化 → 提出 compiler-level 建議
        │
  → 從「hot loop 慢」到「該加什麼優化」的系統流程
    這是 compiler 效能工作的核心思考
```

關鍵心智：hot loop 優化的思考框架是「從症狀到處方」——(1) profiling 定位 hot loop；(2) perf events 判斷瓶頸類型（cache/branch/compute bound）；(3) 分析 loop 結構（相依/向量化/分支/記憶體）；(4) 對應到優化（瓶頸 → 處方）；(5) 判斷 compiler 能不能做（用 flag 或提出 compiler 改進）。這把前面的工具整合成系統流程。

## 完整的分析流程

```bash
# 對一個 hot loop 的完整分析流程（綜合前面所有工具）
cd ~/perflab
cat > hotloop.c <<'EOF'
#include <stdio.h>
#include <stdlib.h>
// 一個待分析的 hot loop
void process(float *a, float *b, float *c, int n) {
    for (int i = 0; i < n; i++) {
        c[i] = a[i] * b[i] + c[i];   // FMA-like，看起來能向量化
    }
}
int main() {
    int n = 50000000;
    float *a = malloc(n*4), *b = malloc(n*4), *c = malloc(n*4);
    for (int i = 0; i < n; i++) { a[i]=i; b[i]=i*0.5; c[i]=0; }
    for (int iter = 0; iter < 10; iter++) process(a, b, c, n);
    printf("%f\n", c[n-1]);
    return 0;
}
EOF
gcc -O2 -g hotloop.c -o hotloop

# === Step 1: 定位 hot loop（perf，Ch 7）===
perf record -g ./hotloop > /dev/null 2>&1
perf report --stdio 2>/dev/null | head -3
# 85% process  ← process 是 hot loop（確認）

# === Step 2: 判斷瓶頸類型（perf events，Ch 6）===
perf stat -e instructions,cycles,cache-misses,L1-dcache-load-misses ./hotloop 2>&1 | \
    grep -E 'insn per|cache-misses'
# IPC 和 cache miss → 是 cache bound 還 compute bound？
# 這個 loop 存取 3 個陣列循序（cache 友善）+ 計算（FMA）
# → 可能 compute-bound 或 memory-bandwidth-bound（看 IPC 和 cache miss）

# === Step 3: 分析 loop 結構（看 code + 向量化 report，Ch 13）===
gcc -O2 -fopt-info-vec -fopt-info-vec-missed hotloop.c -c 2>&1 | grep process
# 看 process 的 loop 向量化了嗎？
# 如果沒：為什麼（別名？）→ 加 restrict

# === Step 4: llvm-mca 分析指令層（Ch 8）===
clang -O2 -S hotloop.c -o hotloop.s
# llvm-mca 分析 process 的 hot loop 組合語言
# → 看 throughput、瓶頸（FMA 單元？記憶體？）

# === Step 5: 嘗試優化並驗證 ===
# 加 restrict（如果別名阻礙向量化）
# 用 -march=native（用 FMA/AVX 指令）
gcc -O2 -march=native -ffast-math hotloop.c -o hotloop_opt
hyperfine './hotloop' './hotloop_opt'    # 驗證提升（統計嚴謹，Ch 4）
```

> **完整的 hot loop 分析流程整合了前面所有工具——perf 定位、perf events 判斷瓶頸、report/llvm-mca 分析結構、優化驗證**。對一個 hot loop 的系統分析：(1) **perf record/report 定位**（Ch 7——確認哪個 loop 是 hot）；(2) **perf stat 判斷瓶頸類型**（Ch 6——IPC/cache-miss 看是 cache bound 還 compute bound）；(3) **vectorization report 分析結構**（Ch 13——loop 向量化了嗎？沒有的話為什麼）；(4) **llvm-mca 分析指令層**（Ch 8——throughput、瓶頸是哪個執行單元/相依）；(5) **優化並驗證**（加 restrict/march/PGO，用 hyperfine 統計嚴謹驗證，Ch 4）。這個流程把前面 13 章的工具串成「從 hot loop 到優化」的系統方法——不是「隨便試優化」，而是「測量 → 理解瓶頸 → 針對性優化 → 驗證」。每一步用對的工具：perf（找熱點、判斷瓶頸）、report/llvm-mca（分析結構/指令）、hyperfine（驗證）。這個系統性是 perf_bench 的核心——**資料驅動的優化**，每個決策有測量支撐。對 compiler 工作，這個流程是日常——benchmarking team 給你一個慢的 workload，你走這個流程：定位 hot loop、判斷瓶頸、分析為什麼、提出優化（用 flag 或 compiler 改進）、驗證。掌握這個流程，你能系統地處理任何 hot loop 的效能問題——這是把零散的工具變成「解決效能問題」的能力。

## 瓶頸 → 優化的對應表

```
瓶頸類型 → 該加什麼優化（處方表）：

  Compute-bound（IPC 高、計算多）：
    → 向量化（SIMD/RVV，一次處理多個）
    → 減少指令（更好的演算法、strength reduction）
    → 打破相依鏈（unroll + 多累加器，Ch 8）
        │
  Memory-bound / cache-bound（cache miss 高）：
    → 改善 locality（循序存取、資料佈局）
    → loop tiling/blocking（讓資料放進 cache）
    → prefetch（提前載入）
    → 減少 working set（更小的資料結構）
        │
  Branch-bound（branch miss 高）：
    → PGO（用 profile 優化分支，Ch 11）
    → branchless（用算術/cmov 消除分支）
    → 規律化資料（讓分支可預測）
        │
  Front-end bound（取指瓶頸，I-cache miss）：
    → 減少 code size（-Os、減少 inline）
    → BOLT/Propeller（code layout 優化，Ch 11）
    → PGO（冷熱分離）
        │
  → 先用 perf 判斷瓶頸類型（Ch 6），再查這個表找優化方向
    這是「從瓶頸到優化」的系統對應
```

> **「瓶頸類型 → 優化處方」的對應表是 hot loop 思考框架的核心——先判斷瓶頸（perf），再查表找優化方向**。這個對應表把「瓶頸類型」對應到「該加什麼優化」：**Compute-bound**（IPC 高、計算多）→ 向量化（SIMD/RVV）、減少指令（更好的演算法、strength reduction）、打破相依鏈（unroll + 多累加器）；**Memory/cache-bound**（cache miss 高）→ 改善 locality（循序存取、資料佈局）、loop tiling（讓資料放進 cache）、prefetch、減少 working set；**Branch-bound**（branch miss 高）→ PGO、branchless、規律化資料；**Front-end bound**（I-cache miss）→ 減少 code size、BOLT/Propeller（layout）、PGO（冷熱分離）。這個表是思考框架的核心——**先用 perf 判斷瓶頸類型（Ch 6），再查表找對應的優化方向**。這讓優化**有系統**（不是亂試）——每個瓶頸有對應的優化策略。對 compiler 工作，這個對應也指導「compiler 該加什麼優化」——如果一個 workload 是 cache-bound，compiler 可以加記憶體優化（loop tiling、prefetch generation）；如果是 compute-bound 且能向量化，compiler 改善 auto-vectorization；如果是 front-end bound，compiler 改善 code layout（或用 BOLT）。這是 SiFive job spec「suggest compiler optimizations」的核心——**從 workload 的瓶頸，推斷 compiler 該加什麼優化**。掌握這個對應表，你能從「這個 workload 慢」（瓶頸）推到「compiler 該做什麼」（優化建議）——這是把效能分析變成 compiler 改進建議的關鍵能力。當然，這是個指引（每個情況要具體分析），但它給了系統化的起點——根據瓶頸類型找優化方向。

## 提出 compiler-level 建議

```
從分析到 compiler 建議（SiFive 工作的核心）：

  分析完一個 hot loop，提出建議的層次：
        │
  1. 現有 flag 能解決（最簡單）：
     「這個 loop 沒向量化是因為別名，用 restrict / 加 -march=rv64gcv」
        │
  2. 現有優化沒觸發（compiler 該更積極）：
     「這個 loop 能向量化但 compiler 沒做（cost model 太保守）
      建議調整 vectorization cost model」
        │
  3. compiler 缺少的優化（提出新優化）：
     「這個 pattern（如特定的 reduction）compiler 沒識別
      建議加 idiom recognition / 新的 pass」
        │
  4. 微架構特定的優化（針對目標 CPU）：
     「針對 SiFive 的 U74，這個 loop 的指令排程可以改善
      （llvm-mca 顯示某個 port 是瓶頸）」
        │
  → 從「分析」到「具體的 compiler 改進建議」
    用資料支撐（perf/llvm-mca/report 的數字）
    這是 compiler 工程師的核心交付：可行動的優化建議
```

> **從分析到「具體的 compiler 改進建議」（用 flag、調 cost model、加新優化、微架構特定優化）——這是 SiFive compiler 工作的核心交付**。分析完一個 hot loop，提出 compiler-level 建議有層次：(1) **現有 flag 能解決**（最簡單）——「這個 loop 沒向量化是因為別名，加 restrict 或用 `-march=rv64gcv` 啟用 RVV」；(2) **現有優化沒觸發**（compiler 該更積極）——「這個 loop 能向量化但 compiler 沒做（cost model 判斷不值得，太保守），建議調整 vectorization cost model」；(3) **compiler 缺少的優化**（提出新優化）——「這個 pattern（如特定的 reduction idiom）compiler 沒識別，建議加 idiom recognition 或新的 optimization pass」；(4) **微架構特定的優化**（針對目標 CPU）——「針對 SiFive U74，這個 loop 的指令排程可以改善（llvm-mca 顯示某個執行 port 是瓶頸，可以重排指令）」。這是 compiler 工程師的**核心交付——可行動的優化建議**，且**用資料支撐**（perf 的瓶頸數字、llvm-mca 的指令分析、vectorization report 的失敗原因——這些數字讓建議可信、可驗證）。這正是 SiFive job spec「analyze performance results and suggest new compiler optimizations」的具體展現——你不只「分析效能」，還「提出 compiler 改進」，且建議有層次（從用 flag 到提出新 pass）和資料支撐。這是 perf_bench 培養的最終能力——**從 hot loop 的效能分析，產出可行動的 compiler 優化建議**。這需要綜合全課：測量嚴謹（Ch 4）、理解微架構（Ch 5-6）、會用工具（Ch 7-9）、懂 compiler 優化（Ch 10-13）、和系統的思考框架（這章）。掌握這個，你能勝任 compiler 效能工程師的核心工作——把 benchmarking team 找出的效能問題，轉化成 compiler 的改進。Final Project 會讓你完整走一遍——分析一個 case，提出 compiler optimization proposal。

## 故意弄壞:完整的優化案例

```bash
cd ~/perflab
# 一個完整的「分析 → 優化 → 提出建議」案例
cat > case.c <<'EOF'
// 矩陣乘法的 hot loop（經典的可優化案例）
void matmul(float *A, float *B, float *C, int n) {
    for (int i = 0; i < n; i++)
        for (int j = 0; j < n; j++) {
            float sum = 0;
            for (int k = 0; k < n; k++)
                sum += A[i*n+k] * B[k*n+j];   // B 按列存取（cache 不友善！）
            C[i*n+j] = sum;
        }
}
EOF
# n=512 的矩陣乘法
# Step 1: profile → matmul 是 hot loop（明顯）
# Step 2: perf stat → cache miss 高（B[k*n+j] 按列存取，cache 不友善，Ch 5/17）
#         → memory/cache-bound
# Step 3: 分析結構 → B 的存取模式差（按列跳，每次 cache miss）
#         內層 loop 有相依（sum += ...）
# Step 4: 瓶頸 → 優化對應：
#         cache-bound → loop tiling/blocking（讓 B 的 block 放進 cache）
#         或 loop interchange（換迴圈順序讓存取循序）
#         相依 → 多累加器
# Step 5: 優化（loop interchange 或 tiling）+ 驗證
#
# 提出的 compiler 建議：
#   "matmul 的瓶頸是 B 的按列存取造成 cache miss（perf: LLC miss 40%）。
#    compiler 應該能做 loop interchange（換 j/k 迴圈順序讓 B 循序存取）
#    或 loop tiling（分塊讓資料放進 cache）。
#    建議：1. 確認 compiler 的 loop interchange pass 為什麼沒觸發
#         2. 對 RISC-V 的 cache 大小調整 tiling 的 block size
#    資料支撐：perf 顯示 cache-bound、cachegrind 確認 B 的存取是 miss 源"
```

> **矩陣乘法的完整案例展示「分析 → 瓶頸 → 優化對應 → compiler 建議」——這是 perf_bench 全課能力的綜合**。這個矩陣乘法案例完整走了思考框架：**Step 1** profile 找出 matmul 是 hot loop；**Step 2** perf stat 顯示 **cache miss 高**（`B[k*n+j]` 按列存取，每次跳一整行 = cache 不友善，Ch 5/17）→ **memory/cache-bound**；**Step 3** 分析結構——B 的存取模式差（按列跳）、內層有相依（sum 累加）；**Step 4** 瓶頸對應優化——cache-bound → **loop tiling/blocking**（讓 B 的 block 放進 cache）或 **loop interchange**（換迴圈順序讓存取循序）、相依 → 多累加器；**Step 5** 優化+驗證。最後**提出 compiler 建議**——用資料支撐（perf 顯示 cache-bound、cachegrind 確認 B 是 miss 源）、具體（loop interchange/tiling）、可行動（確認 compiler 的 pass 為什麼沒觸發、為 RISC-V 的 cache 調 block size）。這是全課能力的綜合——測量（perf/hyperfine）、瓶頸判斷（cache-bound）、結構分析（存取模式）、優化對應（tiling/interchange）、compiler 建議（具體可行動、資料支撐）。矩陣乘法是經典案例——它的 cache 行為（按列存取的 B）是效能殺手，優化（tiling/interchange/向量化）能快好幾倍，是 compiler 優化（如 polyhedral 優化、loop transformation）的重要目標。這個案例展示了 perf_bench 培養的最終能力——**面對一個 hot loop，系統地分析、定位瓶頸、提出有資料支撐的 compiler 優化建議**。這正是 SiFive compiler 工程師的核心工作。全課（Ch 0-14）到此完成——你從「測量嚴謹性」（Ch 0-4）、「微架構和事件」（Ch 5-6）、「profiling 工具」（Ch 7-9）、「compiler 優化」（Ch 10-13）到「整合的思考框架」（Ch 14），具備了完整的效能分析和 compiler 優化建議能力。接下來的練習和 Final Project 讓你實戰這套能力。

## 動手練習

1. 走框架：對一個 hot loop 走完整流程（perf 定位 → 判斷瓶頸 → 分析結構 → 優化 → 驗證）

2. 瓶頸對應：對幾個不同瓶頸（cache/branch/compute）的 loop，查處方表找優化方向

3. 矩陣乘法：分析「故意弄壞」的 matmul，用 perf 確認 cache-bound，嘗試 loop interchange/tiling

4. 提建議：對一個分析過的 hot loop，寫一個「compiler 優化建議」（有資料支撐、具體、可行動）

5. 整合：回顧全課，確認你能從 hot loop 走到 compiler 建議

## 本章重點整理

- hot loop 優化思考框架：profiling 定位 → perf events 判斷瓶頸 → 分析結構 → 瓶頸對應優化 → 提建議
- 瓶頸→優化處方表：compute（向量化/減指令/打破相依）、cache（locality/tiling/prefetch）、branch（PGO/branchless）、front-end（減 code size/BOLT）
- compiler 建議有層次：用 flag、調 cost model、加新優化、微架構特定——都要用資料支撐（perf/llvm-mca/report）
- 這是 SiFive 工作的核心：從效能分析產出可行動的 compiler 優化建議
- 全課綜合：測量嚴謹 + 微架構 + 工具 + compiler 優化 + 思考框架 = 完整的效能分析和優化能力

## 自我檢核

- [ ] 能對一個 hot loop 走完整的分析流程（定位→瓶頸→結構→優化→驗證）
- [ ] 知道瓶頸類型對應的優化方向（處方表）
- [ ] 能從分析提出具體的 compiler 優化建議（有資料支撐）
- [ ] 理解這是 SiFive compiler 工作的核心
- [ ] 能整合全課的工具和知識解決效能問題

## 延伸閱讀

### 書籍

- **《Performance Analysis and Tuning on Modern CPUs》— 全書** — Denis Bakhvalov
  - **讀哪幾章**：整本（這本書就是「從分析到優化」的系統方法）
  - **這本書的定位**：本課思考框架的最佳延伸
  - **連結**：免費 PDF

- **《Computer Architecture: A Quantitative Approach》** — Hennessy & Patterson
  - **為什麼值得讀**：理解微架構和優化的理論基礎

### 文章

- **[Brendan Gregg 的方法論](https://www.brendangregg.com/methodology.html)** — Brendan Gregg
  - **這篇說什麼**：效能分析的系統方法論
  - **為什麼值得讀**：本章思考框架的方法論基礎

### 實戰

- **[LLVM/GCC 的 optimization passes](https://llvm.org/docs/Passes.html)**
  - **為什麼值得讀**：理解 compiler 有哪些優化（提建議時知道 compiler 能做什麼）

Part 4 和所有章節到此完成。接下來是練習和 Final Project——把整套能力用在實戰：寫 Coremark 報告（練習 A）、用 llvm-mca 分析 hot loop（練習 B）、完整的 performance case study + compiler optimization proposal（Final）。

→ [練習 A：寫一份 Coremark 效能報告](./practice-a-coremark-report.md)
