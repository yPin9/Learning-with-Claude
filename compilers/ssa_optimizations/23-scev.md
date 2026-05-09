# Ch 23 — Scalar Evolution（SCEV）

> 目標：理解 SCEV 表示式的結構，掌握 AddRecExpr 的推導，以及 SCEV 如何讓 trip count 計算和感應變數分析成為可能。

## 什麼是 Scalar Evolution

**Scalar Evolution（標量演化）** 分析迴圈中每個純量值如何隨迭代次數變化。

核心思想：用閉合形式（closed-form）表示值，而不是「跑迴圈跟蹤每一步」。

```c
for (int i = 0; i < n; i++) {
    int j = i * 2 + 1;   // j 的值隨 i 線性增長
    int k = i * i;        // k 是 i 的二次函數
}
```

SCEV 把 `j` 表示成 `{1, +, 2}_loop`（從 1 開始，每次加 2），把 `k` 表示成 `{0, +, 0, +, 2}_loop`（加速度是 2 的等差數列）。

## AddRecExpr：核心表示式

SCEV 的核心是 **AddRecExpr（加法遞推表達式）**：

```
{start, +, step}_L

表示：在迴圈 L 中，第 i 次迭代的值是 start + step * i
```

- `start`：第 0 次迭代的起始值（可以是另一個 SCEV 表達式）
- `step`：每次迭代增加的量（感應變數的步長）
- `L`：對應的迴圈

更廣義地，AddRecExpr 可以嵌套（非線性）：

```
{0, +, {1, +, 2}_L}_L
= 第 0 次：0
= 第 1 次：0 + 1 = 1
= 第 2 次：1 + 3 = 4
= 第 3 次：4 + 5 = 9
= 第 i 次：i²
```

這是 i² 的 SCEV 表示：外層 step 本身是一個 AddRec（等差數列）。

## SCEV 的代數操作

SCEV 支持基本代數操作，且保持 AddRec 形式：

**加法**：

```
{s1, +, t1}_L + {s2, +, t2}_L = {s1+s2, +, t1+t2}_L
```

**乘法**（常數倍）：

```
k * {s, +, t}_L = {k*s, +, k*t}_L
```

**乘以另一個 AddRec**（產生高次）：

```
{s1, +, t1}_L * {s2, +, t2}_L = {s1*s2, +, s1*t2 + s2*t1 + t1*t2, +, 2*t1*t2}_L
（可以用多項式乘法推導）
```

## Trip Count 計算

**Trip count**：迴圈執行的次數（或最大次數）。

SCEV 讓 trip count 的計算變成代數問題。對迴圈 `for (i = 0; i < n; i++)`：

```
感應變數：{0, +, 1}_L（i）
退出條件：i < n
trip count = 找第一個 i 使得 i >= n → n（如果 n ≥ 0，否則 0）
```

更複雜的情況：`for (i = a; i < b; i += step)`：

```
trip count = ceil((b - a) / step) = (b - a + step - 1) / step（整數除法）
```

SCEV 的 `getSmallConstantTripCount` 和 `getTripCount` 提供了這個計算。

## 感應變數識別

SCEV 能識別所有「由基礎感應變數派生的值」：

```c
for (int i = 0; i < n; i++) {
    int j = i * stride;       // j = {0, +, stride}_L
    int k = j + base;         // k = {base, +, stride}_L
    float *ptr = arr + j;     // ptr = {arr, +, stride*sizeof(float)}_L
}
```

這讓向量化（Ch 25）能識別連續的記憶體訪問模式，以及迴圈展開（Ch 24）能計算展開後的索引。

## 推導過程（以 phi 為起點）

SCEV 的推導從迴圈 phi 節點開始：

```llvm
loop_header:
  %i = phi [0, preheader], [%i_next, latch]   ; 起始 0，每次 +1
  %j = mul i32 %i, %stride
  ...
  %i_next = add i32 %i, 1
```

推導步驟：

1. `%i` 的 phi：起始值 0，每次 latch 帶來的是 `%i_next = %i + 1`
   → `%i = {0, +, 1}_loop`

2. `%j = %i * stride`（假設 stride 是常數 8）
   → `%j = {0, +, 1}_loop * 8 = {0, +, 8}_loop`

## LLVM 的 ScalarEvolution

```cpp
#include "llvm/Analysis/ScalarEvolution.h"

ScalarEvolution &SE = FAM.getResult<ScalarEvolutionAnalysis>(F);

// 取得某個值的 SCEV 表達式
const SCEV *S = SE.getSCEV(someValue);
errs() << *S << "\n";   // 印出如 {0,+,1}<loop>

// 判斷是否是 AddRec
if (auto *AR = dyn_cast<SCEVAddRecExpr>(S)) {
    errs() << "Start: " << *AR->getStart() << "\n";
    errs() << "Step: " << *AR->getStepRecurrence(SE) << "\n";
    errs() << "Loop: " << AR->getLoop()->getHeader()->getName() << "\n";
}

// 取得迴圈的 trip count
const SCEV *TripCount = SE.getBackedgeTakenCount(L);
if (!isa<SCEVCouldNotCompute>(TripCount)) {
    errs() << "Trip count: " << *TripCount << "\n";
}
```

```bash
# 觀察 SCEV 分析結果
opt -passes="print<scalar-evolution>" /tmp/loop.ll -o /dev/null 2>&1
```

## SCEV 的限制

SCEV 只分析**純量值（scalar values）**，不分析指針的被指向內容（那是別名分析的工作）。

SCEV 假設**整數不溢出**（至少對有符號運算假設 nsw flag）。有溢出的感應變數分析結果是保守的。

SCEV **無法計算 trip count** 的情況：

```
迴圈退出條件是指針比較（非整數）
迴圈內有複雜的控制流（break, continue 不在單一退出邊）
感應變數的演化不是多項式（指數增長等）
```

這些情況 `SE.getBackedgeTakenCount(L)` 返回 `SCEVCouldNotCompute`。

## 自我檢核

- [ ] AddRecExpr `{start, +, step}_L`：第 i 次迭代值 = start + step * i
- [ ] 嵌套 AddRec 表示高次多項式（二次、三次...）
- [ ] Trip count 計算：SCEV 代數推導，不依賴跑迴圈
- [ ] `SE.getSCEV()` + `dyn_cast<SCEVAddRecExpr>()` 的用法
- [ ] `SCEVCouldNotCompute`：trip count 無法靜態確定的情況

→ [Ch 24 迴圈展開（Loop Unrolling）](./24-loop-unrolling.md)
