# Ch 8 — 活躍變數分析（Liveness Analysis）

> 目標：用 Ch 7 的框架推導活躍變數分析，理解它在 SSA 下的簡化，並看它如何驅動暫存器分配。

## 定義

**變數 v 在程式點 p 是活躍的（live）**：存在一條從 p 開始的執行路徑，在不重新定義 v 的前提下到達 v 的某個使用點。

直覺：v 在 p 是活躍的 = 「v 現在的值之後還有用」。

如果 v 在 p **不**活躍，我們可以安全地：
- 刪除對 v 的賦值（死碼消除）
- 把 v 的暫存器分配給別的變數（暫存器分配）

## 套入資料流框架

活躍變數是**後向 May-analysis**：

- **後向**：從使用點往回看定義點（信息逆著控制流傳播）
- **May**：某條路徑上活躍即可（取 ∪ 合併）

每個基本塊的轉換函數：

```
UEVar_B（Upward Exposed Variables）= B 中在被定義之前就被使用的變數
Kill_B（VarKill）= B 中被定義的變數

LiveIn[B]  = UEVar_B ∪ (LiveOut[B] \ Kill_B)
LiveOut[B] = ∪_{S ∈ succ(B)} LiveIn[S]
```

`UEVar_B` 是 Gen，`Kill_B` 是 Kill。

初始值：所有 `LiveIn[B] = {}`，`LiveOut[exit] = {}`。

## 手動例子

```c
// 基本塊 B1
x = a + b   // 定義 x；使用 a, b
y = x * c   // 定義 y；使用 x, c
z = y       // 定義 z；使用 y

// 基本塊 B2（B1 的唯一後繼）
return z    // 使用 z
```

計算 B1 的 `UEVar` 和 `Kill`：

逐指令掃描（注意：在 Kill 前出現的使用才算 UEVar）：

- `x = a + b`：a, b 在被定義前使用 → UEVar += {a, b}；Kill += {x}
- `y = x * c`：x 在 Kill 中（x 已被 B1 定義），所以 x 不是 UEVar；c 不在 Kill，UEVar += {c}；Kill += {y}
- `z = y`：y 在 Kill 中，不是 UEVar；Kill += {z}

```
UEVar_B1 = {a, b, c}
Kill_B1  = {x, y, z}
```

已知 `LiveOut[B2] = {}` 且 `LiveIn[B2] = {z}`（return z 使用了 z）。

```
LiveOut[B1] = LiveIn[B2] = {z}
LiveIn[B1]  = UEVar_B1 ∪ (LiveOut[B1] \ Kill_B1)
            = {a,b,c} ∪ ({z} \ {x,y,z})
            = {a,b,c} ∪ {}
            = {a, b, c}
```

結論：進入 B1 時，a、b、c 是活躍的；x、y、z 在 B1 內部定義，不在 LiveIn 中。

## SSA 下的活躍變數

在 SSA 中，每個變數只有一個定義，這讓活躍性分析有了額外性質：

**性質 1**：SSA 中，變數 `v` 的活躍區間是 CFG 中「定義點到所有使用點」的路徑的並集，且是連通的（因為只有一個定義）。

**性質 2**：φ-function 的引數活躍性需要特別處理：

```llvm
D:
  x3 = phi [x1, B], [x2, C]
```

`x1` 在 B→D 這條邊上活躍，`x2` 在 C→D 這條邊上活躍——但它們不一定在 D 的開頭都活躍（因為執行時只選其中一個）。

實作上，SSA 的活躍性分析把 φ-function 引數的活躍區間限制到它對應的前驅邊：`x1` 活躍到 B 的末尾，不延伸到 D 的開頭。

## 活躍變數與暫存器分配

兩個同時活躍的變數**互相干擾（interfere）**——它們不能分配到同一個暫存器。

活躍變數分析建立**干擾圖（Interference Graph）**：

```
節點：每個變數
邊：兩個變數在同一程式點同時活躍
```

暫存器分配 = 干擾圖著色（用 k 種顏色 = k 個物理暫存器）。

這是 NP-完全問題，但 Chaitin 等人的啟發式算法在實務上效果很好。LLVM 的暫存器分配器（`RegAllocGreedy`）就建在這個框架上。

## LLVM 中的 LiveVariables

```bash
# 計算活躍變數並列印
opt -passes="print<live-vars>" /tmp/test.ll -o /dev/null 2>&1

# 更詳細的活躍區間分析（用於暫存器分配）
# 需要先轉成 MachineIR 才能觀察 LiveIntervals
llc -print-machineinstrs -stop-after=livevars /tmp/test.ll 2>&1
```

在 SSA IR 層，LLVM 主要用 `livevar` analysis 分析。在 Machine IR 層，用更精確的 `LiveIntervals`（包含 slot index 級別的精度）。

## 自我檢核

- [ ] 活躍變數的定義：後面還有使用，且沒有被重定義
- [ ] 後向 May-analysis：`LiveIn = UEVar ∪ (LiveOut \ Kill)`
- [ ] SSA 中 φ-function 引數的活躍性是「邊敏感」的
- [ ] 干擾圖：同時活躍 → 互相干擾 → 不能同一暫存器

→ [Ch 9 到達定義（Reaching Definitions）](./09-reaching-definitions.md)
