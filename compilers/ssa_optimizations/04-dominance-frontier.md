# Ch 4 — 支配邊界（Dominance Frontier）

> 目標：定義支配邊界，推導其計算算法，理解為什麼「支配邊界 = φ-function 的插入位置」。

## 問題：哪裡需要插入 φ-function？

回到 Ch 1 的例子：

```
    entry
      |
      A
     / \
    B   C
     \ /
      D
```

假設變數 `x` 在 B 和 C 都有定義，那 D 的開頭需要一個 φ-function 來選擇兩者之一。

但如果有更複雜的 CFG，手動判斷就不夠了。我們需要一個系統性的方法，對任意 CFG 和任意變數，確定需要在哪些節點插入 φ-function。

## 支配邊界的定義

**定義**：節點 `x` 的支配邊界 `DF(x)` 是所有節點 `y` 的集合，使得：

1. `x` 支配 `y` 的某個前驅節點（`x dom pred(y)`）
2. `x` **不**嚴格支配 `y`（`x ∤sdom y`）

直覺：`x` 的「控制影響」在 `y` 處「到達邊界」。`x` 能控制進入 `y` 的某條邊，但無法控制所有進入 `y` 的路徑。

另一種表述：`y ∈ DF(x)` iff `y` 是 CFG 中某條邊 `(a, y)` 的終點，其中 `x dom a`，但 `x ∤sdom y`。

## 手動計算

```
    entry
      |
      A
     / \
    B   C
     \ /
      D
```

支配者樹（從 Ch 2）：

```
entry → A → {B, C, D}
```

計算 `DF(B)`：

找所有節點 y，使得 B 支配 y 的某個前驅，但 B 不嚴格支配 y。

- y = D：D 的前驅是 B 和 C。B 支配 B（B dom B ✓），但 B 不嚴格支配 D（B ∤sdom D ✓）。
  → **D ∈ DF(B)**

其他節點？B 支配的前驅只有 B 本身，所以只有 D 滿足條件。

```
DF(B) = {D}
DF(C) = {D}   （對稱）
DF(A) = {}    （A 嚴格支配所有後代，沒有 DF）
DF(D) = {}
DF(entry) = {}
```

這說明：在 B 和 C 都有定義的變數，在 D 需要插入 φ-function。符合直覺。

## 支配邊界的計算算法

暴力算法是 O(n²)。利用支配者樹，可以做到 O(n + e)（n 節點、e 邊）。

**算法（Cytron et al. 1991）**：在支配者樹上做後序遍歷（post-order）。

```
for each node x in dominator tree (post-order):
    DF(x) = {}
    
    // Local: CFG 中 x 的後繼節點，如果 x 不嚴格支配它們
    for each CFG successor y of x:
        if idom(y) ≠ x:
            DF(x) = DF(x) ∪ {y}
    
    // Up: 子節點的 DF 往上傳
    for each child z of x in dominator tree:
        for each y in DF(z):
            if idom(y) ≠ x:
                DF(x) = DF(x) ∪ {y}
```

後序遍歷保證計算 `DF(x)` 時所有子節點的 `DF` 已算好。

### 為什麼條件是 `idom(y) ≠ x`？

`idom(y) ≠ x` 等價於 `x ∤sdom y`（x 不嚴格支配 y）。

如果 `idom(y) = x`，說明 x 是 y 的立即支配者，x 嚴格支配 y，y 就不在 DF(x) 裡。

## 迭代支配邊界 DF+

DF 只算了「從單個節點直接可達的邊界」。對於 φ-function 插入，我們需要**迭代支配邊界（Iterated Dominance Frontier，DF+）**：

```
DF+(S) = 不動點迭代：
    DF1 = DF(S)       （S 是有定義的節點集合）
    DFi+1 = DF(S ∪ DFi)
    直到 DFi+1 = DFi
```

直覺：如果在 `DF(S)` 插入了 φ-function，那個 φ-function 本身也是一個「新的定義」，可能又觸發更多位置需要 φ-function。迭代到不動點。

**定理**：最終需要插入 φ-function 的節點集合，正好是 `DF+(S)`，其中 `S` 是原始定義的節點集合。

## 不同 SSA 變體

並非所有位置的 φ-function 都是必要的。根據插入策略不同，有三種 SSA 變體：

```
最小 SSA（Minimal SSA）：只在 DF+(S) 插入（理論最少，Cytron 原文）
半剪枝 SSA（Semi-pruned）：只在活躍變數處插入（減少無用 phi）
剪枝 SSA（Pruned SSA）：只在活躍且必要的地方插入（最少但需要先算 liveness）
```

LLVM 的 `mem2reg` 用的是 pruned SSA 的近似：先做基本的活躍性檢查，再插入 φ-function。

## LLVM 中的 DF

LLVM 沒有單獨暴露支配邊界的 API（它被封裝在 `mem2reg` / SSA construction 內部），但可以用以下方式觀察效果：

```bash
# 生成有多個定義路徑的 IR
cat > /tmp/df_example.c << 'EOF'
int f(int a, int b, int c) {
    int x;
    if (a > 0) {
        x = b;
    } else {
        x = c;
    }
    return x;  // 這裡應該有 phi
}
EOF

clang -O0 -S -emit-llvm /tmp/df_example.c -o /tmp/df_O0.ll
opt -S -passes=mem2reg /tmp/df_O0.ll -o /tmp/df_ssa.ll
cat /tmp/df_ssa.ll   # 找 phi node 的位置
```

φ-function 出現的基本塊，就是支配邊界所在的位置。

## 自我檢核

- [ ] 能用定義手動計算簡單 CFG 的 DF(x)
- [ ] 理解 `idom(y) ≠ x` 等價於 `x ∤sdom y`
- [ ] 知道 DF+(S) 是迭代不動點，且是 φ-function 插入位置的精確刻畫
- [ ] 能區分 Minimal / Semi-pruned / Pruned SSA 的差異

→ [Ch 5 φ-node 插入：Cytron 算法](./05-phi-insertion.md)
