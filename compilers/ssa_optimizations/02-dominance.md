# Ch 2 — 支配關係（Dominance）

> 目標：精確定義支配關係，理解支配者樹的結構，並能手動推導簡單 CFG 的支配關係。

## 為什麼優化需要支配關係

Ch 1 說 SSA 需要插入 φ-function，而且只在「控制流匯合點且有多個不同定義到達」的地方插入。

但怎麼精確判斷「哪些地方需要 φ-function」？答案是**支配邊界（Dominance Frontier）**——而支配邊界建立在支配關係上。

更廣泛地說，支配關係是幾乎所有中端優化的基礎：

```
SSA 構造   → 支配邊界決定 φ-function 插入位置
LICM       → 迴圈 header 支配所有迴圈節點
GVN        → 支配樹 DFS 決定值編號傳播方向
Inlining   → 後支配關係判斷 must-execute 指令
```

## 控制流圖（CFG）

程式的基本塊和跳轉關係構成**控制流圖（Control Flow Graph）**：

```
       entry
         |
         v
         A
        / \
       v   v
       B   C
        \ /
         v
         D
```

約定：有唯一的 **entry** 節點，程式從這裡開始執行。

## 支配的定義

**定義**：節點 `d` **支配（dominates）** 節點 `n`（記作 `d dom n`），當且僅當：

> 從 entry 到 n 的**每一條**路徑都經過 d。

用遞迴集合方程表達：

```
Dom(entry) = {entry}
Dom(n)     = {n} ∪ (∩ Dom(p))，p 為 n 的所有前驅節點
```

注意：每個節點都支配自己（自反性）。

### 對上面的 CFG 計算

```
Dom(entry) = {entry}
Dom(A)     = {entry, A}
Dom(B)     = {entry, A, B}
Dom(C)     = {entry, A, C}
Dom(D)     = ?
```

D 的前驅是 B 和 C：

```
Dom(D) = {D} ∪ (Dom(B) ∩ Dom(C))
       = {D} ∪ ({entry,A,B} ∩ {entry,A,C})
       = {D} ∪ {entry, A}
       = {entry, A, D}
```

B 不支配 D——因為存在路徑 `entry → A → C → D` 完全繞過 B。C 同理。

## 嚴格支配與立即支配

**嚴格支配（strict dominance）**：`d sdom n` iff `d dom n` 且 `d ≠ n`。

**立即支配（immediate dominator）**：節點 n 的 `idom(n)` 是離 n 最近的嚴格支配者：

```
idom(n) = 唯一的 d，使得：
  d sdom n
  且不存在 m 使得 d sdom m sdom n
```

每個節點（除了 entry）有唯一的立即支配者——這個唯一性可以從 Dom 集合的交集結構證明，不需要特別假設。

對上面的例子：

```
idom(A) = entry
idom(B) = A
idom(C) = A
idom(D) = A       ← 注意：不是 B 也不是 C
```

## 支配者樹（Dominator Tree）

把立即支配關係可視化：父節點是每個節點的 idom。

```
      entry
        |
        A
      / | \
     B  C  D
```

B、C、D 都是 A 的子節點，因為 `idom(B) = idom(C) = idom(D) = A`。

**關鍵性質**：

> `d dom n` ⟺ `d` 是支配者樹中 `n` 的祖先（ancestor）

這把「所有路徑都必然經過」的全局性質，轉換成了樹上的祖先查詢，複雜度從全局分析降到 O(depth)。

## 一個更複雜的例子

```
     1(entry)
         |
         2
        / \
       3   4
       |  / \
       5 6   7
         |   |
         8   9
          \ /
          10
```

（假設 5 沒有通往 10 的路徑）

計算 `Dom(10)`：到 10 的所有路徑都必須經過哪些節點？

路徑枚舉：
- 1 → 2 → 4 → 6 → 8 → 10
- 1 → 2 → 4 → 7 → 9 → 10

兩條路徑的公共節點：`{1, 2, 4, 10}`

```
Dom(10) = {1, 2, 4, 10}
idom(10) = 4
```

再算 `Dom(8)`：到 8 的路徑只有 `1 → 2 → 4 → 6 → 8`。

```
Dom(8) = {1, 2, 4, 6, 8}
idom(8) = 6
```

支配者樹（部分）：

```
  1
  └─ 2
     └─ 4
        ├─ 6
        │  └─ 8
        ├─ 7
        │  └─ 9
        └─ 10
```

## 後支配（Post-Dominance）

把 CFG 的邊方向反轉，從 exit 出發做同樣的分析：

> `d` **後支配（post-dominates）** `n`：從 n 到 exit 的每一條路徑都經過 d。

後支配者樹用於：
- 判斷指令是否必然執行（ADCE 需要）
- Control Dependence Graph 的計算（Ch 14 激進死碼消除用到）

## 手動練習

對下面的 CFG，計算每個節點的 Dom 集合和 idom：

```
    entry
      |
      1
     / \
    2   3
    |   |
    4   5
     \ /
      6
      |
      7
     / \
    8   9
     \ /
     10
```

<details>
<summary>參考解答</summary>

```
Dom(entry) = {entry}
Dom(1)  = {entry, 1}
Dom(2)  = {entry, 1, 2}
Dom(3)  = {entry, 1, 3}
Dom(4)  = {entry, 1, 2, 4}
Dom(5)  = {entry, 1, 3, 5}
Dom(6)  = {entry, 1, 6}        ← 2,3,4,5 都不支配 6
Dom(7)  = {entry, 1, 6, 7}
Dom(8)  = {entry, 1, 6, 7, 8}
Dom(9)  = {entry, 1, 6, 7, 9}
Dom(10) = {entry, 1, 6, 7, 10}

idom:
idom(1) = entry
idom(2) = 1, idom(3) = 1
idom(4) = 2, idom(5) = 3
idom(6) = 1   ← 不是 2 或 3
idom(7) = 6
idom(8) = 7, idom(9) = 7
idom(10) = 7

支配者樹：
entry
└─ 1
   ├─ 2 ── 4
   ├─ 3 ── 5
   └─ 6
      └─ 7
         ├─ 8
         ├─ 9
         └─ 10
```

</details>

## LLVM 中的支配樹

```bash
cat > /tmp/dom_example.c << 'EOF'
int f(int cond, int x) {
    int result;
    if (cond) {
        result = x * 2;
    } else {
        result = x + 1;
    }
    return result;
}
EOF

clang -O0 -S -emit-llvm /tmp/dom_example.c -o /tmp/dom_example.ll
opt -passes="print<domtree>" /tmp/dom_example.ll -o /dev/null 2>&1
```

輸出的縮排結構就是支配者樹，`[深度]` 是節點在樹中的層數。

## 自我檢核

- [ ] 能用集合方程 `Dom(n) = {n} ∪ (∩ Dom(p))` 手動計算
- [ ] 理解立即支配者的唯一性
- [ ] 能從 Dom 集合推導支配者樹
- [ ] `d dom n` ⟺ `d` 是支配者樹中 `n` 的祖先
- [ ] 完成手動練習並核對解答

→ [Ch 3 支配樹算法：Lengauer-Tarjan](./03-dominator-tree-algorithm.md)
