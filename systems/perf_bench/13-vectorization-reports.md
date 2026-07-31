# Ch 13 — Vectorization report 閱讀

> **目標**：理解自動向量化（auto-vectorization）——compiler 怎麼把純量迴圈轉成 SIMD/向量指令（一條指令處理多個資料）、怎麼讀 compiler 的 vectorization report（哪些迴圈被向量化、哪些失敗、為什麼）、阻礙向量化的常見原因（相依、不對齊、控制流）、以及怎麼幫助 compiler 向量化。這對 RISC-V 的 RVV（向量擴展）特別相關——破除「auto-vectorize 就快」的迷思。

> **環境**：Linux，gcc/clang。向量化 report 用 -fopt-info（gcc）/-Rpass（clang）。

## 為什麼向量化重要又難？

現代 CPU 有 **SIMD（Single Instruction Multiple Data）** 指令——一條指令同時處理多個資料（如一條加法同時加 8 個 float）。這能大幅加速「對大量資料做相同運算」的迴圈（影像處理、科學計算、ML）。RISC-V 的 **RVV（Vector extension）** 就是它的向量化能力。

**auto-vectorization** 是 compiler 自動把純量迴圈轉成 SIMD 指令——理想上你寫普通的迴圈，compiler 自動向量化加速。但**它常常失敗**——很多迴圈因為各種原因（相依、控制流、不對齊）compiler 無法或不敢向量化。「auto-vectorize 就快」是迷思——很多時候 compiler 沒向量化（你以為快其實沒有）。這章教你**讀 vectorization report**（看 compiler 到底有沒有向量化、為什麼失敗）和**怎麼幫助 compiler**。對 RISC-V 的 RVV 工作，這是核心——讓 code 能被 RVV 向量化是效能的關鍵。

## 先建立直覺:一條指令處理多個資料

```
SIMD/向量化：一條指令處理多個資料

  純量（scalar）：一次處理一個
    for (i) c[i] = a[i] + b[i];
    → 一條加法處理一對 → N 個元素要 N 條加法
        │
  向量化（vectorized）：一次處理多個
    一條向量加法同時加 8 對（如果向量寬度 8）
    → N 個元素只要 N/8 條向量加法 → 快 ~8 倍（理想）
        │
  視覺：
    純量：  a[0]+b[0], a[1]+b[1], a[2]+b[2]...（一個一個）
    向量：  [a0,a1,a2,a3,a4,a5,a6,a7] + [b0...b7]（一次 8 個）
        │
  → 向量化讓「對大量資料的相同運算」快幾倍
    SIMD 寬度：x86 的 AVX(256/512 bit)、ARM 的 NEON/SVE、RISC-V 的 RVV
    RVV 的特點：向量長度可變（vector-length agnostic）
        │
  但：不是所有迴圈都能向量化（很多障礙，後述）
```

關鍵心智：**SIMD/向量化**讓一條指令同時處理多個資料（如一條向量加法同時加 8 對），對「大量資料的相同運算」快幾倍。RISC-V 的 **RVV**（向量擴展）是它的向量化能力（特點是向量長度可變）。但**不是所有迴圈都能向量化**——很多障礙（相依、控制流、不對齊）讓 compiler 無法向量化。

> 向量化是 Ch 10 的 -march（rv64gcv 啟用 RVV）的應用。它能大幅加速 compute-bound 的迴圈（Ch 6 的 compute-bound）。

## 讀 vectorization report

```bash
cd ~/perflab
cat > vec.c <<'EOF'
// 能向量化的迴圈
void add_arrays(float *a, float *b, float *c, int n) {
    for (int i = 0; i < n; i++) {
        c[i] = a[i] + b[i];      // 簡單、無相依 → 能向量化
    }
}
// 不能向量化的迴圈（有相依）
void prefix_sum(float *a, int n) {
    for (int i = 1; i < n; i++) {
        a[i] = a[i] + a[i-1];    // a[i] 依賴 a[i-1] → 相依，不能向量化
    }
}
EOF

# === GCC 的 vectorization report ===
gcc -O3 -fopt-info-vec -fopt-info-vec-missed vec.c -c 2>&1
# vec.c:4: optimized: loop vectorized using 16 byte vectors   ← add_arrays 向量化成功
# vec.c:10: missed: couldn't vectorize loop                   ← prefix_sum 失敗
# vec.c:10: missed: not vectorized: relevant stmt not supported / data dependency
# → 報告告訴你「哪個迴圈向量化成功、哪個失敗、為什麼」

# === Clang 的 vectorization report ===
clang -O3 -Rpass=loop-vectorize -Rpass-missed=loop-vectorize -Rpass-analysis=loop-vectorize vec.c -c 2>&1
# vec.c:4: remark: vectorized loop (vectorization width: 4...)  ← 成功
# vec.c:10: remark: loop not vectorized: ... dependence         ← 失敗原因

# === RISC-V RVV（要 -march=rv64gcv）===
# riscv64-linux-gnu-gcc -O3 -march=rv64gcv -fopt-info-vec vec.c -c
# → 看哪些迴圈用 RVV 向量化
```

> **vectorization report（-fopt-info-vec / -Rpass）告訴你「哪個迴圈向量化成功、哪個失敗、為什麼」——這是破除「auto-vectorize 就快」迷思的關鍵**。compiler 的 **vectorization report** 是理解向量化的關鍵工具——它告訴你 compiler **到底有沒有向量化**（很多人以為「開了 -O3 就向量化」，但很多迴圈其實沒被向量化）。**GCC**：`-fopt-info-vec`（顯示向量化成功的）+ `-fopt-info-vec-missed`（顯示失敗的和原因）。**Clang**：`-Rpass=loop-vectorize`（成功）+ `-Rpass-missed`（失敗）+ `-Rpass-analysis`（分析原因）。report 告訴你：`add_arrays`（簡單、無相依）**向量化成功**，`prefix_sum`（`a[i]` 依賴 `a[i-1]`，有**相依**）**向量化失敗**（"data dependency"）。這破除了「auto-vectorize 就快」的迷思——**compiler 對很多迴圈無法向量化**（因為相依、控制流等障礙），你以為快其實沒有。所以**要讀 report 確認 compiler 真的向量化了你的熱點迴圈**（不要假設）。對 **RISC-V RVV** 工作，這是核心——用 `-march=rv64gcv` 啟用 RVV 後，讀 report 看「哪些迴圈用 RVV 向量化、哪些失敗」，這決定 RVV 的效能發揮。如果你的熱點迴圈 report 顯示「沒向量化」，那 RVV 的能力沒用上（效能沒提升）。理解怎麼讀 report，你能**驗證向量化**（確認熱點真的向量化）和**診斷失敗**（為什麼沒向量化，下節）——這是向量化工作的基礎。對 compiler 工作（如改善 RVV 的 auto-vectorization），讀 report 是日常——看哪些迴圈 compiler 沒向量化、為什麼、能不能改善。

## 阻礙向量化的常見原因

```
為什麼 compiler 無法向量化（report 的常見原因）：

  1. 資料相依（dependency）：
     a[i] = a[i-1] + x（這次依賴上次）→ 不能平行 → 不能向量化
        │
  2. 控制流（control flow）：
     迴圈裡有複雜的 if/break/continue → 難向量化
     （簡單的 if 能用 mask 向量化，複雜的不行）
        │
  3. 函式呼叫：
     迴圈裡呼叫不能 inline 的函式 → 不能向量化
        │
  4. 不對齊/別名（aliasing）：
     compiler 不確定 a 和 b 會不會重疊（指標別名）
     → 不敢向量化（怕結果錯）→ 用 restrict 告訴它不重疊
        │
  5. 不規則的記憶體存取：
     a[index[i]]（間接存取）→ 難向量化（要 gather/scatter）
        │
  6. 迴圈次數未知/太少：
     compiler 不確定值不值得向量化
        │
  → 讀 report 知道「為什麼沒向量化」，對症下藥：
    相依 → 重構演算法打破相依
    別名 → 用 restrict
    控制流 → 簡化分支
    函式呼叫 → inline 或移出迴圈
```

> **阻礙向量化的常見原因（相依、控制流、別名、函式呼叫）——讀 report 知道原因才能對症下藥幫助 compiler**。compiler 無法向量化的常見原因（report 會告訴你）：(1) **資料相依**（`a[i] = a[i-1] + x`，這次依賴上次，不能平行，所以不能向量化——這是最常見的障礙）；(2) **控制流**（迴圈裡有複雜的 if/break/continue——簡單的 if 能用 mask 向量化，但複雜的控制流不行）；(3) **函式呼叫**（迴圈裡呼叫不能 inline 的函式——向量化不了）；(4) **別名（aliasing）**（compiler 不確定指標 a 和 b 會不會重疊，怕重疊時向量化結果錯，所以**不敢向量化**——這個常見且可解，用 `restrict` 告訴 compiler「這些指標不重疊」）；(5) **不規則記憶體存取**（`a[index[i]]` 間接存取，要 gather/scatter 指令，難向量化）；(6) **迴圈次數未知/太少**（compiler 不確定值不值得）。**讀 report 知道原因，才能對症下藥幫助 compiler**：相依 → 重構演算法打破相依（如 Ch 8 的多累加器）、別名 → 用 `restrict`（最常見的 quick win——告訴 compiler 指標不重疊，它就敢向量化）、控制流 → 簡化分支、函式呼叫 → inline 或移出迴圈。這是向量化工作的核心——**不是「希望 compiler 向量化」，而是「讀 report 看為什麼沒向量化，然後幫助它」**。`restrict` 特別重要——很多迴圈 compiler 因為「怕指標別名」不敢向量化，加 `restrict` 就能向量化（大幅加速）。對 RVV 工作，這個「診斷 + 幫助向量化」的能力是讓 RISC-V 的向量能力發揮的關鍵。理解這些障礙和解法，你能讓更多熱點迴圈被向量化（用上 SIMD/RVV 的能力）。

## 用 restrict 幫助向量化

```bash
cd ~/perflab
# 展示 restrict 怎麼解除「別名」障礙，讓 compiler 向量化
cat > alias.c <<'EOF'
// 沒有 restrict：compiler 怕 a/b/c 重疊，不敢向量化
void add1(float *a, float *b, float *c, int n) {
    for (int i = 0; i < n; i++) c[i] = a[i] + b[i];
}
// 有 restrict：告訴 compiler「a/b/c 不重疊」，能向量化
void add2(float * restrict a, float * restrict b, float * restrict c, int n) {
    for (int i = 0; i < n; i++) c[i] = a[i] + b[i];
}
EOF

# 看 vectorization report
gcc -O3 -fopt-info-vec alias.c -c 2>&1
# add1: 可能 missed（怕別名）或 vectorized with runtime checks（加檢查，較慢）
# add2: optimized: loop vectorized   ← restrict 讓它直接向量化（沒有 runtime check）
# → restrict 告訴 compiler 指標不重疊 → 能更好地向量化

# 為什麼別名阻礙向量化：
# 如果 c 和 a 重疊（如 c = a+1），向量化「一次處理 8 個」可能結果錯
# （因為向量化改變了存取順序）
# → compiler 不確定就不向量化（或加 runtime check 確認不重疊，較慢）
# restrict 保證不重疊 → compiler 放心向量化（沒有 check 的開銷）
```

> **`restrict` 告訴 compiler「指標不重疊」，解除別名障礙讓它向量化——這是最常見的向量化 quick win**。**別名（aliasing）** 是阻礙向量化最常見且最易解的障礙。問題：compiler 看到 `c[i] = a[i] + b[i]`，**不確定 a/b/c 會不會重疊**（如果 `c` 和 `a` 重疊，向量化「一次處理 8 個」會改變存取順序，可能結果錯）。所以 compiler **不敢向量化**（或加 runtime check 確認不重疊，但 check 有開銷且 code 變大）。解法是 **`restrict`**——`float * restrict a` 告訴 compiler「這個指標**不和其他指標重疊**」，compiler 就**放心向量化**（沒有 check 的開銷）。這是最常見的**向量化 quick win**——很多迴圈 compiler 只因為「怕別名」不敢向量化，加 `restrict` 就能向量化（大幅加速）。對 compiler/RVV 工作，`restrict` 是讓 code 能向量化的重要工具（C99 的關鍵字，C++ 用 `__restrict__`）。注意 `restrict` 是**對 compiler 的承諾**——你保證指標不重疊，如果實際重疊了（違背承諾），行為未定義（可能結果錯）。所以用 `restrict` 要確定指標真的不重疊。這個例子展示了「讀 report 診斷 + 用 restrict 幫助」的完整流程——report 顯示「沒向量化（別名）」→ 加 `restrict` → report 顯示「向量化了」→ 驗證效能提升。這是向量化工作的標準方法——**診斷障礙、針對性幫助 compiler、驗證效果**。對 RISC-V 的 RVV，讓更多迴圈向量化（用 restrict 等技巧解除障礙）是發揮向量能力的關鍵。理解 restrict 和其他幫助向量化的技巧，你能讓 compiler 產生更多向量化的 code。

## 故意弄壞:auto-vectorize 沒發生的真相

```bash
cd ~/perflab
# 破除「-O3 就向量化」的迷思——很多迴圈其實沒向量化
cat > maybe_vec.c <<'EOF'
#include <math.h>
// 看起來能向量化，但有隱藏的障礙
void process(double *a, double *b, int n) {
    for (int i = 0; i < n; i++) {
        if (a[i] > 0) {              // 控制流
            b[i] = sqrt(a[i]);       // 函式呼叫（sqrt）
        } else {
            b[i] = 0;
        }
    }
}
EOF

# 看 compiler 有沒有向量化（不要假設！）
gcc -O3 -fopt-info-vec -fopt-info-vec-missed maybe_vec.c -c 2>&1
# 可能：missed: not vectorized（因為 sqrt 函式呼叫 + 控制流）
# 或：vectorized（如果 compiler 能處理——取決於版本和 -ffast-math）
# → 不讀 report，你不知道「到底有沒有向量化」
#   「-O3 就向量化」是迷思——很多迴圈因障礙沒向量化

# 加 -march=native（用更寬的向量）和 -ffast-math（放寬浮點）試試
gcc -O3 -march=native -ffast-math -fopt-info-vec maybe_vec.c -c 2>&1
# 可能變成 vectorized（-ffast-math 讓 sqrt 能用向量版、放寬浮點重排）

# → 教訓：
#   1. 不要假設「-O3 就向量化」—— 讀 report 確認
#   2. 向量化常需要幫助（restrict、-ffast-math、簡化控制流、避免函式呼叫）
#   3. 對熱點迴圈，確認它真的向量化了（否則 SIMD/RVV 的能力沒用上）
```

> **「-O3 就向量化」是迷思——很多迴圈因障礙（控制流、函式呼叫）沒向量化，要讀 report 確認**。這個例子破除最後一個迷思——「開 -O3 迴圈就會向量化」。`process` 函式看起來能向量化（對陣列做運算），但有**隱藏的障礙**：**控制流**（if/else）+ **函式呼叫**（sqrt）。讀 report 可能顯示「沒向量化」（因為這些障礙）——**你不讀 report 就不知道「到底有沒有向量化」**。加 `-march=native`（更寬的向量）+ `-ffast-math`（放寬浮點，讓 sqrt 能用向量版、允許重排）可能讓它向量化。**教訓**：(1) **不要假設「-O3 就向量化」**——讀 report 確認（很多人以為向量化了其實沒有，效能沒發揮）；(2) **向量化常需要幫助**（restrict 解別名、-ffast-math 放寬浮點、簡化控制流、避免函式呼叫——這些幫 compiler 跨越障礙）；(3) **對熱點迴圈，確認它真的向量化了**（否則 SIMD/RVV 的能力沒用上，白白浪費硬體的向量能力）。這呼應 perf_bench 的核心信條——**破除迷思（「auto-vectorize 就快」），用 report 確認、用技巧幫助**。向量化是「有條件發生」的優化（要 compiler 能跨越障礙），不是「開了就有」。對 RISC-V 的 RVV 工作，這特別重要——RVV 的硬體能力要靠 compiler 向量化才能發揮，而很多迴圈需要幫助才能向量化。理解「讀 report 確認 + 用技巧幫助」，你能讓更多熱點迴圈用上向量化（發揮 SIMD/RVV），這是 compute-bound workload 效能的關鍵。Part 4 的向量化章完成了「compiler-centric 效能分析」的工具——你能診斷和幫助向量化、用 PGO/LTO、選對 flag。最後 Ch 14 把這些綜合——從 hot loop 倒推「該加什麼優化」。

## 動手練習

1. 讀 report：對一個有迴圈的程式用 `-fopt-info-vec -fopt-info-vec-missed`，看哪些向量化、哪些失敗

2. 診斷失敗：看一個沒向量化的迴圈的 report 原因（相依/別名/控制流/函式呼叫）

3. restrict：對一個因別名沒向量化的迴圈加 `restrict`，看 report 變成向量化

4. -ffast-math：對一個浮點迴圈試 `-ffast-math`，看是否讓它向量化（放寬浮點重排）

5. 跑「故意弄壞」：對熱點迴圈讀 report 確認「真的向量化了嗎」，破除「-O3 就向量化」的假設

## 本章重點整理

- SIMD/向量化讓一條指令處理多個資料，加速大量資料的相同運算；RISC-V 的 RVV 是向量擴展（長度可變）
- vectorization report（-fopt-info-vec / -Rpass）告訴你哪些迴圈向量化、哪些失敗、為什麼——要讀它確認
- 阻礙向量化：資料相依、控制流、函式呼叫、別名（aliasing）、不規則存取——讀 report 對症下藥
- restrict 解除別名障礙（告訴 compiler 指標不重疊）是最常見的向量化 quick win
- 「auto-vectorize 就快」是迷思——很多迴圈因障礙沒向量化，要讀 report 確認 + 用技巧幫助

## 自我檢核

- [ ] 理解 SIMD/向量化怎麼加速，RISC-V 的 RVV
- [ ] 會讀 vectorization report（GCC -fopt-info / Clang -Rpass）
- [ ] 知道阻礙向量化的常見原因，怎麼診斷
- [ ] 會用 restrict 等技巧幫助 compiler 向量化
- [ ] 破除「-O3 就向量化」的迷思，知道要讀 report 確認

## 延伸閱讀

### 文章

- **[Auto-vectorization 指南](https://llvm.org/docs/Vectorizers.html)** — LLVM
  - **讀哪裡**：loop vectorizer、怎麼讀 remark
  - **為什麼值得讀**：LLVM 向量化的權威

- **[GCC vectorization](https://gcc.gnu.org/projects/tree-ssa/vectorization.html)** — GCC
  - **讀哪裡**：向量化的能力和限制
  - **為什麼值得讀**：GCC 向量化的權威

### RISC-V

- **[RISC-V Vector extension (RVV)](https://github.com/riscv/riscv-v-spec)** — RISC-V
  - **為什麼值得讀**：RVV 的規格（向量長度可變的設計）

### 書籍

- **《Performance Analysis and Tuning on Modern CPUs》— 向量化章** — Denis Bakhvalov
  - **為什麼值得讀**：向量化的實用分析

下一章是 Part 4 的最後，也是全課的綜合——從 hot loop 倒推「該加什麼 optimization」。把前面的所有工具和知識整合成「看到一個 hot loop，提出 compiler-level 改進」的能力。這是 SiFive 工作的核心。

→ [Ch 14 從 hot loop 倒推「該加什麼 optimization」](./14-hot-loop-thinking.md)
