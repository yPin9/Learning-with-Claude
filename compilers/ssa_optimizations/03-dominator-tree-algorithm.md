# Ch 3 — 支配樹算法：Lengauer-Tarjan

> 目標：理解為什麼暴力算法不夠用，完整推導 Lengauer-Tarjan 算法的每個步驟，並理解它的近線性時間複雜度來自哪裡。

## 暴力算法的問題

Ch 2 的集合方程：

```
Dom(n) = {n} ∪ (∩ Dom(p))，p 為前驅節點
```

迭代求解：從 `Dom(n) = {所有節點}` 開始，反覆應用方程直到不動點。

```
每次迭代：O(n²)（n 個節點，每個節點掃描所有前驅的 Dom 集合取交集）
迭代次數：O(n)（最壞情況）
總複雜度：O(n³)
```

對幾千個基本塊的大函式，這是無法接受的。LLVM 用的是 **Lengauer-Tarjan 算法**（1979），複雜度接近 O(n α(n))，其中 α 是反阿克曼函數——實務上幾乎是線性的。

## 算法概覽

Lengauer-Tarjan 分三個階段：

```
1. DFS 編號：把 CFG 壓縮成 DFS spanning tree + 分類後向邊
2. 半支配者計算：找每個節點的「近似」支配者（半支配者）
3. 立即支配者推導：從半支配者得到真正的 idom
```

## 階段一：DFS 編號

對 CFG 做 DFS，給每個節點分配 **DFS 序號（semi-number）**，記作 `dfnum[v]` 或 `semi[v]`（初始值）。

DFS 過程同時記錄：
- `parent[v]`：DFS 樹中 v 的父節點
- `vertex[i]`：DFS 序號為 i 的節點

```python
def dfs(v, n):
    dfnum[v] = n
    vertex[n] = v
    n += 1
    for w in successors(v):
        if dfnum[w] == 0:   # 未訪問
            parent[w] = v
            n = dfs(w, n)
    return n
```

DFS 把 CFG 的邊分成四類：

```
Tree edges：    DFS 樹的邊（parent → child）
Forward edges： 從祖先到後代（非樹邊）
Back edges：    從後代到祖先（形成迴圈）
Cross edges：   其他（只在有向圖中存在）
```

**關鍵性質**：在 DFS 樹中，如果有一條非樹邊 `u → v`，則：
- 若是 back edge：`dfnum[v] < dfnum[u]`（v 是 u 的祖先）
- 若是 forward/cross edge：`dfnum[v] > dfnum[u]`

## 階段二：半支配者（Semi-Dominator）

**定義**：節點 v 的**半支配者** `sdom(v)` 是 DFS 序號最小的節點 u，使得存在一條路徑 `u = v₀ → v₁ → ... → vₖ = v`，其中對所有 `1 ≤ i ≤ k-1`，`dfnum[vᵢ] > dfnum[v]`。

直覺：從 u 可以到達 v，且中間所有節點在 DFS 序中都「比 v 晚被發現」（即 v 之後才進入的節點）。

為什麼有用？**定理（Lengauer-Tarjan 1979）**：

> `idom(v)` 要嘛是 `sdom(v)`，要嘛是 DFS 樹路徑 `sdom(v) → ... → parent(v)` 上半支配者序號最小的節點的立即支配者。

這個定理把 idom 的計算規約到了 sdom 的計算，而 sdom 有高效的計算方法。

### 計算 sdom 的方程

```
sdom(v) = min(
    {parent(v)}   -- 直接前驅（樹邊）
    ∪ {sdom(u) | u → v 是 non-tree edge 且 dfnum[u] > dfnum[v]}
                  -- 通過非樹邊到達 v 的節點
)
```

「min」是按 DFS 序號取最小值。

按照 DFS 序號**逆序**（從最大到最小）處理每個節點，可以保證計算 sdom(v) 時所有需要的 sdom 值已經算好。

## 路徑壓縮：Union-Find

計算 sdom 的過程中，需要頻繁查詢「DFS 樹路徑上半支配者序號最小的節點」。

朴素做法：每次都走一遍路徑，O(n) per query。

高效做法：用**路徑壓縮的 Union-Find** 結構。這正是算法複雜度接近線性的來源。

```cpp
// eval(v)：返回以 v 為根的路徑壓縮樹中，sdom 序號最小的節點
int eval(int v) {
    if (ancestor[v] == v) return v;
    compress(v);
    return label[v];
}

// compress：路徑壓縮
void compress(int v) {
    if (ancestor[ancestor[v]] != ancestor[v]) {
        compress(ancestor[v]);
        if (semi[label[ancestor[v]]] < semi[label[v]])
            label[v] = label[ancestor[v]];
        ancestor[v] = ancestor[ancestor[v]];
    }
}
```

`label[v]` 維護這條路徑上 sdom 序號最小的節點，路徑壓縮讓每次 eval 的均攤複雜度接近 O(1)。

## 階段三：從 sdom 推導 idom

按 DFS 序號逆序遍歷，對每個節點 v：

```
令 u = eval(parent(v))  // 路徑上 sdom 最小的節點

if semi[u] == semi[v]:
    idom(v) = sdom(v)
else:
    idom(v) = idom(u)   // 稍後在正序遍歷時填入
```

最後按 DFS 序號**正序**掃一遍，把 `idom(v) = idom(u)` 的延遲賦值解析完。

## 完整算法偽代碼

```
輸入：CFG，入口節點 entry
輸出：每個節點的 idom

1. DFS(entry)，計算 dfnum, parent, vertex

2. 按 dfnum 逆序處理節點 v（從 n-1 到 1）：
   a. 對 v 的每個前驅 u：
      - 如果 dfnum[u] < dfnum[v]（forward/tree edge）：
          candidate = u
      - 否則（back/cross edge）：
          candidate = sdom(eval(u))
      sdom(v) = min(sdom(v), candidate)   // 取 dfnum 最小的
   b. link(parent(v), v)   // 加入 Union-Find
   c. 對所有 w 使得 sdom(w) == v（bucket[v] 中的節點）：
      u = eval(w)
      if semi[u] == semi[w]:
          idom(w) = v
      else:
          idom(w) = u   // 待填入

3. 按 dfnum 正序處理節點 v（從 1 到 n-1）：
   if idom(v) != sdom(v):
       idom(v) = idom(idom(v))
```

## 走一遍小例子

```
    entry(0)
        |
       A(1)
      / \
    B(2) C(3)
      \  /
      D(4)
```

括號內是 DFS 序號。

DFS 樹邊：`entry→A, A→B, B→D, A→C`。非樹邊：`C→D`。

`parent: A←entry, B←A, D←B, C←A`

逆序處理（D=4, C=3, B=2, A=1）：

**D(4)**：前驅是 B(2)（tree edge）和 C(3)（tree edge）。
```
sdom(D) = min(dfnum[B], dfnum[C]) = min(2, 3) = 2，即 B
```
但 B 是 C 的兄弟，不是 D 的祖先，所以 sdom(D) ≠ idom(D)——`idom(D) = idom(B)` 的延遲賦值，之後解析為 A。

**最終結果**：
```
idom(A) = entry
idom(B) = A
idom(C) = A
idom(D) = A   ← 和 Ch 2 手算的一致
```

## 複雜度分析

| 階段 | 複雜度 |
|------|--------|
| DFS | O(n + e) |
| sdom 計算（含路徑壓縮） | O((n+e) α(n)) |
| idom 推導 | O(n) |
| **總計** | **O((n+e) α(n)) ≈ O(n+e)** |

α(n) 是反阿克曼函數，對任何現實規模的 n 都小於 5，實務上就是常數。

對比暴力算法的 O(n³)，Lengauer-Tarjan 在有數千個基本塊的大函式上快了幾個數量級。

## LLVM 的實作

LLVM 實作在 `llvm/include/llvm/Support/GenericDomTree.h` 和 `llvm/lib/Support/GenericDomTree.cpp`。

核心函式是 `Calculate<...>`，實作了上面描述的完整算法。LLVM 還有 Simple Dominator 算法（用於小函式），會根據函式規模自動切換。

```bash
# 在 LLVM 源碼裡找 Lengauer-Tarjan 實作
grep -r "LengauerTarjan\|SemiNCA" llvm/lib/Support/
```

**注意**：LLVM 17 之後主要使用 SemiNCA 算法（Semi-NCA，Cooper et al. 2001），在實踐中比原版 LT 更快，但理論基礎相同，都建在 semi-dominator 定理上。

## 動手驗證

```bash
# 生成一個有迴圈的 IR，驗證支配樹
cat > /tmp/loop.c << 'EOF'
int sum(int n) {
    int s = 0;
    for (int i = 0; i < n; i++) {
        s += i;
    }
    return s;
}
EOF

clang -O0 -S -emit-llvm /tmp/loop.c -o /tmp/loop.ll
opt -passes="print<domtree>" /tmp/loop.ll -o /dev/null 2>&1

# 手動計算 idom，對照輸出
```

迴圈的 header 應該支配迴圈體，但反過來不成立。

## 自我檢核

- [ ] 能說清楚 DFS 編號把 CFG 邊分成哪四類
- [ ] 理解半支配者的定義和直覺
- [ ] 理解「sdom → idom 推導定理」的直覺（不需要記住完整證明）
- [ ] 知道路徑壓縮是 Lengauer-Tarjan 近線性複雜度的來源
- [ ] 能在小例子上手動追蹤算法的前兩步

→ [Ch 4 支配邊界（Dominance Frontier）](./04-dominance-frontier.md)
