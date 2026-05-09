# Ch 7 — 資料流分析框架：格論與 Worklist

> 目標：建立資料流分析的數學基礎，理解格、單調函數、不動點定理，以及 Worklist 算法的正確性與終止性。

## 為什麼需要框架

Ch 8–10 會分別講活躍變數、到達定義、常數傳播——這三個看起來很不同的問題，背後其實共享同一個數學結構：**格上的單調資料流分析**。

理解這個框架，你就能把任何新的優化問題套進去，而不是每次從頭推導。

## 格（Lattice）

**偏序集（Poset）**：集合 `L` 加上二元關係 `⊑`，滿足自反、反對稱、傳遞。

**格（Lattice）**：每對元素 `x, y` 都有 meet（最大下界，`x ⊓ y`）和 join（最小上界，`x ⊔ y`）。

**完全格（Complete Lattice）**：任意子集都有 meet 和 join。必有最大元 `⊤`（top）和最小元 `⊥`（bottom）。

資料流分析用的格通常是：

```
常數傳播：
  ⊤ = "不確定"（可能是任何值）
  ⊥ = "不可能執行到"（unreachable）
  中間：具體常數值

活躍變數：
  L = 2^{所有變數}（冪集格）
  ⊑ 是 ⊆
  meet = ∩，join = ∪
```

## 單調函數與不動點

**轉換函數（transfer function）** `f_B`：對每個基本塊 B，描述「從 B 的進入狀態計算 B 的退出狀態」（或反向）。

要求：`f_B` 必須是單調的：`x ⊑ y → f(x) ⊑ f(y)`。

**Tarski 不動點定理**：在完全格上，任何單調函數 `f: L → L` 都有最小不動點（least fixed point）和最大不動點（greatest fixed point）。

最小不動點 `lfp(f) = ⊔{f^n(⊥) | n ≥ 0}`，即從 ⊥ 開始反覆應用 f，最終收斂到的值。

**對資料流分析的意義**：

- 初始化所有節點為 ⊥（或 ⊤，取決於分析方向）
- 反覆應用轉換函數，直到不動點
- 不動點一定存在（格是完全格），且算法一定終止（格的高度有限，單調函數只往上走）

## 資料流方程

前向分析（Forward）：從 entry 往下算，傳播「定義」。

```
out[B] = f_B(in[B])
in[B]  = meet_{P ∈ pred(B)} out[P]
初始：in[entry] = 初始值，其他 = ⊤
```

後向分析（Backward）：從 exit 往上算，傳播「使用」。

```
in[B]  = f_B(out[B])
out[B] = meet_{S ∈ succ(B)} in[S]
初始：out[exit] = 初始值，其他 = ⊤
```

## Worklist 算法

朴素迭代：每輪掃描所有基本塊，直到沒有任何值改變。

Worklist 優化：只把「上一輪有改變」的節點加入下一輪處理佇列。

```
worklist = {entry}   （前向分析）
while worklist 非空:
    取出節點 B
    計算新的 out[B] = f_B(in[B])
    if out[B] ≠ old_out[B]:
        old_out[B] = out[B]
        worklist.add(B 的所有後繼)
```

**正確性**：到達不動點時，所有方程都已滿足，結果是最小不動點（最保守的安全近似）。

**終止性**：格的高度有限，且每個節點的值只會「上升」（往 ⊤ 方向），不會下降。最多 `n × height(L)` 次更新。

## May-Analysis vs Must-Analysis

| | May-Analysis | Must-Analysis |
|---|---|---|
| **語意** | 某條路徑上可能成立 | 所有路徑上都成立 |
| **meet 算子** | join（∪）| meet（∩）|
| **初始值** | ⊥（空集）| ⊤（所有）|
| **典型例子** | Reaching definitions | Available expressions |
| **安全方向** | 寧可多報（false positive）| 寧可少報（false negative）|

**活躍變數是 May-analysis**：「x 在此處活躍」= 存在某條路徑從此處到達 x 的使用。用 ∪ 合併分支。

**可用表達式是 Must-analysis**：「e 在此處可用」= 所有路徑都已計算過 e 且未修改。用 ∩ 合併分支。

## 轉換函數的 Gen/Kill 形式

大多數分析的轉換函數可以寫成 Gen/Kill 形式：

```
f_B(x) = Gen_B ∪ (x \ Kill_B)
```

- `Gen_B`：B 產生的新資訊（不受輸入影響）
- `Kill_B`：B 消除的資訊（被覆蓋的定義等）

Gen/Kill 形式保證單調性：`x ⊆ y → f(x) ⊆ f(y)`。

## 收斂速度

不同遍歷順序對 Worklist 的迭代次數影響很大：

- **前向分析**：按 reverse postorder（RPO）遍歷——每個節點在其所有前驅之後處理。在無迴圈的 CFG 上，RPO 一次就夠。
- **後向分析**：按 postorder 遍歷。
- **有迴圈**：迴圈 header 需要多次迭代，次數等於最深的迴圈嵌套深度。

LLVM 的 Worklist 都使用 RPO 或 inverse RPO，以最小化迭代次數。

## 自我檢核

- [ ] 完全格的定義：⊤、⊥、meet、join
- [ ] Tarski 定理：單調函數在完全格上有最小不動點
- [ ] Worklist 算法的終止性：格有限高度 + 單調函數
- [ ] May vs Must 分析的差別：meet 算子和初始值不同
- [ ] Gen/Kill 轉換函數的形式

→ [Ch 8 活躍變數分析（Liveness Analysis）](./08-liveness-analysis.md)
