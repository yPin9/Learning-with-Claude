# Ch 9 — 樹：等價定義、Cayley 公式、Prüfer 編碼

> **目標**：證明「樹」的六種定義互相等價，並用一個計數引理把證明縮成幾行；用 Prüfer 編碼建立「標記樹 ↔ 長度 `n−2` 的序列」的雙射，完整證明 Cayley 公式 `n^(n−2)`；實作編碼、解碼，在 `n ≤ 7` 上窮舉驗證，並用它做**真正均勻**的隨機樹產生器。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

你處理過的樹多半有根、有方向：AST、DOM、檔案系統、dominator tree（Ch 14）。圖論裡的樹沒有根，是「連通而且沒有圈」的圖。這個看似單薄的定義，有至少六種等價說法，每一種都在不同演算法裡被當成判準：Kruskal（Ch 10）用「無圈 + 邊數 `n−1`」，union-find 用「加邊時兩端不在同一分量」，割點與橋（Ch 11）用「每條邊都是橋」。能在這些說法之間自由切換，是讀懂後面每一個正確性證明的前提。

計數方面，1889 年 Cayley 寫下「`n` 個標記點上恰有 `n^(n−2)` 棵樹」（Borchardt 1860 年已經得到同一個結果，Cayley 的論文也引用了他）。Cayley 的論證只處理了小例子與一個不完整的推廣；1918 年 Prüfer 給了一個漂亮的雙射：每棵樹對應一個長度 `n−2`、每項在 `{1, …, n}` 的序列。雙射證明的好處是它**可以執行**——它同時是一個壓縮格式、一個解碼器，和一個均勻隨機產生器。

## 先建立直覺

```text
      0   1   2                反覆「拔掉編號最小的葉子，記下它的鄰居」：
       \  |  /
          3                    拔 0，記 3  → [3]
          |                    拔 1，記 3  → [3, 3]
          4                    拔 2，記 3  → [3, 3, 3]
          |                    拔 3，記 4  → [3, 3, 3, 4]   （3 這時變成葉子了）
          5                    剩 4–5 兩點，停
```

觀察兩件事：度數 4 的點 `3` 在序列裡出現 3 次，度數 2 的點 `4` 出現 1 次，葉子 `0, 1, 2, 5` 一次都沒出現。每個點出現「度數 − 1」次——這就是整個雙射證明的關鍵引理。

> 本章用到 Ch 7 的術語（路徑、圈、連通分量、誘導子圖）。如果不熟，先回看 [Ch 7 — 圖的定義、表示與同構](./07-graph-definitions.md)。計數部分用到 Ch 3 的多項式係數。

## 六個等價定義

**定理**：設 `G` 是 `n ≥ 1` 個點、`m` 條邊的簡單圖。以下六個敘述等價，滿足任一個就叫**樹（tree）**：

```text
(a) 連通且無圈
(b) 連通且 m = n − 1
(c) 無圈且 m = n − 1
(d) 任兩點之間恰有一條路徑
(e) 極小連通：連通，且刪掉任何一條邊就不連通
(f) 極大無圈：無圈，且加上任何一條不存在的邊就產生圈
```

教科書常見的寫法是一條一條互推，冗長且容易漏情況。我們改用一個計數引理把 (a)(b)(c) 一次解決。

### 關鍵引理：一條一條加邊

**引理 1**：從 `n` 個孤立點開始，依任意順序把 `G` 的 `m` 條邊一條一條加進去。稱一條邊是「**閉合邊**」，若它加入時兩端點**已經**在同一個連通分量裡。設閉合邊有 `k` 條，`c(G)` 是連通分量數，則

```text
c(G) = n − m + k，而且 G 無圈 ⇔ k = 0
```

**證明**：一開始 `c = n`。加一條非閉合邊，兩個分量合成一個，`c` 減 1；加一條閉合邊，分量不變。所以最後 `c = n − (m − k)`。

若有閉合邊 `uv`：加入前 `u`、`v` 已連通，存在 `u` 到 `v` 的路徑 `P`（不含 `uv`），`P + uv` 是圈，所以 `G` 有圈。反過來，若 `G` 有圈 `C`，看 `C` 上**最後**被加入的那條邊 `uv`：它加入時 `C − uv` 這條 `u` 到 `v` 的路徑已經全在了，所以 `uv` 是閉合邊。∎

**(a)(b)(c) 互推**：

- (a) ⇒ (b)：連通 `c = 1`、無圈 `k = 0`，代入得 `m = n − 1`。
- (b) ⇒ (c)：`c = 1`、`m = n − 1` 代入得 `k = 0`，所以無圈。
- (c) ⇒ (a)：`k = 0`、`m = n − 1` 代入得 `c = 1`，所以連通。

### 剩下三個

**引理 2**：若 `u` 到 `v` 有兩條不同的路徑，則 `G` 有圈。

**證明**：設 `P = u p1 p2 …`、`Q = u q1 q2 …`。兩條路徑從 `u` 出發，令 `x` 是它們**第一次分岔前**的最後一個共同點（`P`、`Q` 不同，所以會分岔；兩者都在 `v` 結束，所以分岔後會再碰到）。令 `y` 是 `P` 在 `x` 之後第一個也落在 `Q`（`x` 之後的部分）上的點。`P` 從 `x` 到 `y` 的段與 `Q` 從 `x` 到 `y` 的段除了端點不共點，而且兩段不同（第一步就分開了），合起來是一個圈。∎

- (a) ⇒ (d)：連通給出存在性；若有兩條路徑，引理 2 給出圈，矛盾。
- (d) ⇒ (a)：連通是明顯的。若有圈 `C`，取 `C` 上相鄰的 `u, v`，則邊 `uv` 與 `C − uv` 是兩條不同的 `u`–`v` 路徑，矛盾。
- (a) ⇔ (e)：一條邊 `uv` 刪掉後仍連通 ⇔ 存在不經過 `uv` 的 `u`–`v` 路徑 ⇔ `uv` 在某個圈上。所以「連通且每條邊刪掉都會斷」⇔「連通且沒有邊在圈上」⇔ (a)。
- (a) ⇔ (f)：無圈圖加上邊 `uv` 產生圈 ⇔ `u`、`v` 原本就連通（引理 1 的閉合邊論證）。所以「無圈且任何缺的邊都會產生圈」⇔「無圈且任兩個不相鄰點都連通」⇔ (a)。∎

暴力確認六個定義在所有小圖上真的同真同假：

```python
from collections import Counter
import networkx as nx


def props(G):
    n, m = G.number_of_nodes(), G.number_of_edges()
    conn = nx.is_connected(G)
    acyc = nx.is_forest(G)
    # (d) 任兩點恰一條路徑
    uniq = all(len(list(nx.all_simple_paths(G, u, v))) == 1
               for u in G for v in G if u < v)
    # (e) 極小連通：連通，且刪掉任何一條邊就不連通
    def disconnects(e):
        H = G.copy()
        H.remove_edge(*e)
        return not nx.is_connected(H)
    min_conn = conn and all(disconnects(e) for e in G.edges())
    # (f) 極大無圈：無圈，且加上任何一條缺的邊就有圈
    def creates_cycle(e):
        H = G.copy()
        H.add_edge(*e)
        return not nx.is_forest(H)
    max_acyc = acyc and all(creates_cycle(e) for e in nx.non_edges(G))
    return (conn and acyc, conn and m == n - 1, acyc and m == n - 1,
            uniq, min_conn, max_acyc)


stat = Counter()
for G in nx.graph_atlas_g():
    if G.number_of_nodes() == 0:
        continue
    p = props(G)
    assert len(set(p)) == 1, (sorted(G.edges()), p)   # 六個條件同真同假
    stat[G.number_of_nodes(), p[0]] += 1
print(f"六個定義在 {sum(stat.values())} 個同構類（1–7 點）上全部同真同假")
print("各 n 的非同構樹個數:", [stat[n, True] for n in range(1, 8)])
print("nonisomorphic_trees  :", [1] + [sum(1 for _ in nx.nonisomorphic_trees(n)) for n in range(2, 8)])
```

```text
六個定義在 1252 個同構類（1–7 點）上全部同真同假
各 n 的非同構樹個數: [1, 1, 1, 2, 3, 6, 11]
nonisomorphic_trees  : [1, 1, 1, 2, 3, 6, 11]
```

注意這裡數的是**非同構**的樹（不帶標籤）：7 個點只有 11 種形狀。下面的 Cayley 公式數的是**標記樹**：同一個形狀換不同標籤算不同棵，7 個點有 16807 棵。兩個數相差這麼多，是因為多數樹的對稱很少，一個形狀對應很多種標籤方式（Ch 6 的軌道計數）。

### 葉子引理

後面的證明還需要一個小事實。

**引理 3**：`n ≥ 2` 的樹至少有兩片葉子（度數 1 的點），而刪掉一片葉子後仍是樹。

**證明**：取一條最長路徑 `v0 … vk`（`k ≥ 1`，因為連通且 `n ≥ 2`）。若 `v0` 度數 ≥ 2，它有另一個鄰居 `w ≠ v1`。`w` 若不在路徑上，路徑可以延長；`w` 若在路徑上，就形成圈。兩者都矛盾，所以 `v0` 是葉子，同理 `vk` 也是。刪掉葉子 `ℓ`：點少 1、邊少 1，`m = n − 1` 保持；圈不會憑空出現；所以由 (c) 仍是樹。∎

## Cayley 公式與 Prüfer 雙射

**定理（Cayley 公式）**：點集 `{1, …, n}`（`n ≥ 2`）上的標記樹恰有 `n^(n−2)` 棵。

我們建立標記樹集合 `𝒯(V)` 到序列集合 `V^(n−2)` 的雙射，其中 `V` 是任意 `n` 個元素、有全序的標籤集合（歸納時標籤集合會變，所以要用這個一般形式）。

**編碼 `code(T)`**：重複 `n − 2` 次——取當前編號最小的葉子 `ℓ`，把 `ℓ` 唯一的鄰居寫進序列，刪掉 `ℓ`。由引理 3，每一步都有葉子可拔，且剩下的仍是樹。

**引理 4（出現次數）**：在 `code(T)` 裡，每個點 `v` 恰好出現 `deg_T(v) − 1` 次。特別地，`T` 的葉子恰好是**不在** `code(T)` 中的點。

**證明**：`v` 被寫進序列，唯一的時機是它的某個鄰居被當成葉子拔掉（那時那個鄰居唯一的鄰居就是 `v`）。分兩種情況：

- `v` 在某一步自己被拔掉：那時 `v` 只剩一個鄰居，其餘 `deg(v) − 1` 個鄰居都已先被拔掉，每次都寫下 `v`；`v` 被拔掉之後不會再出現。
- `v` 留到最後兩點之一：最後剩下的兩點是一棵兩點樹，也就是一條邊，所以 `v` 恰剩一個鄰居，其餘 `deg(v) − 1` 個都被拔掉並寫下 `v`。

兩種情況都恰好是 `deg(v) − 1` 次。∎

**定理（Prüfer 1918）**：`code: 𝒯(V) → V^(n−2)` 是雙射。

**證明**：對 `n` 歸納。`n = 2`：只有一棵樹（一條邊），序列是空的，雙射。設 `n ≥ 3`，對所有 `n − 1` 個元素的標籤集合成立。

*單射*。設 `code(T) = s = (s1, …, s(n−2))`。由引理 4，`T` 的葉子是 `V` 中不在 `s` 裡的點，所以編碼第一步拔掉的 `ℓ` 必然是 `min(V \ set(s))`，它的鄰居是 `s1`。拔掉 `ℓ` 之後，編碼程序在 `T − ℓ` 上繼續執行的每一步，和直接對 `T − ℓ`（標籤集合 `V \ {ℓ}`）做編碼完全一樣，所以 `code(T − ℓ) = (s2, …, s(n−2))`。由歸納假設，`T − ℓ` 被這個較短序列唯一決定；`T = (T − ℓ) + ℓs1` 也就被 `s` 唯一決定。

*滿射*。任給 `s ∈ V^(n−2)`。`s` 最多用到 `n − 2` 個不同的值，所以 `V \ set(s)` 非空，令 `ℓ = min(V \ set(s))`。`s' = (s2, …, s(n−2))` 的每一項都不是 `ℓ`（`ℓ` 不在 `s` 裡），所以 `s' ∈ (V \ {ℓ})^(n−3)`。由歸納假設，存在 `V \ {ℓ}` 上的樹 `T'` 使 `code(T') = s'`。令 `T = T' + ℓs1`（`s1 ≠ ℓ`，所以這是在樹上掛一片新葉子，仍是樹）。

要驗證 `code(T) = s`，只需確認 `ℓ` 是 `T` 中編號最小的葉子。`T` 的葉子是 `ℓ`，再加上 `T'` 中除了 `s1` 以外的葉子（`s1` 在 `T` 中多了一個鄰居 `ℓ`）。由引理 4，`T'` 的葉子是 `V \ {ℓ}` 中不在 `s'` 的點；去掉 `s1` 之後，它們都不在 `s` 裡，而 `ℓ` 是不在 `s` 裡的最小點，所以它們都比 `ℓ` 大。於是編碼第一步拔 `ℓ`、寫 `s1`，接著在 `T'` 上產生 `s'`，合起來正是 `s`。∎

**推論**：`|𝒯({1, …, n})| = n^(n−2)`。更細：度數恰為 `(d1, …, dn)` 的標記樹個數，等於「`i` 恰好出現 `di − 1` 次」的序列個數，也就是多項式係數

```text
(n − 2)! / ((d1 − 1)! (d2 − 1)! … (dn − 1)!)
```

## 實作：編碼、解碼、窮舉驗證

解碼器直接照滿射證明寫：反覆取「不在剩餘序列裡的最小點」當葉子，接到序列的下一項。用 heap 維護候選葉子，兩個方向都是 `O(n log n)`。

```python
import heapq
from itertools import combinations, product
import networkx as nx


def prufer_encode(n, edges):
    """標記樹（節點 0..n-1）→ 長度 n-2 的 Prüfer 序列。O(n log n)。"""
    adj = {v: set() for v in range(n)}
    for u, v in edges:
        adj[u].add(v)
        adj[v].add(u)
    leaves = [v for v in range(n) if len(adj[v]) == 1]
    heapq.heapify(leaves)
    seq = []
    for _ in range(n - 2):
        leaf = heapq.heappop(leaves)          # 編號最小的葉子
        (nb,) = adj[leaf]                     # 它唯一的鄰居
        seq.append(nb)
        adj[nb].discard(leaf)
        del adj[leaf]
        if len(adj[nb]) == 1:                 # 鄰居變成新葉子
            heapq.heappush(leaves, nb)
    return seq


def prufer_decode(seq):
    """Prüfer 序列 → 標記樹的邊列表。O(n log n)。"""
    n = len(seq) + 2
    remaining = [0] * n                       # v 在序列「剩下的部分」出現幾次
    for x in seq:
        remaining[x] += 1
    leaves = [v for v in range(n) if remaining[v] == 0]
    heapq.heapify(leaves)
    edges = []
    for x in seq:
        leaf = heapq.heappop(leaves)          # 不在剩餘序列中的最小點
        edges.append((leaf, x))
        remaining[x] -= 1
        if remaining[x] == 0:
            heapq.heappush(leaves, x)
    edges.append((heapq.heappop(leaves), heapq.heappop(leaves)))   # 最後剩兩個點
    return edges


T = [(0, 3), (1, 3), (2, 3), (3, 4), (4, 5)]
s = prufer_encode(6, T)
print("編碼:", s)
print("解碼:", sorted(tuple(sorted(e)) for e in prufer_decode(s)))
print("NetworkX:", nx.to_prufer_sequence(nx.Graph(T)))

# 窮舉驗證 Cayley 公式與雙射
for n in range(2, 8):
    all_edges = list(combinations(range(n), 2))
    trees = []
    for E in combinations(all_edges, n - 1):        # 所有 n-1 條邊的子集
        G = nx.Graph(E)
        G.add_nodes_from(range(n))
        if nx.is_tree(G):
            trees.append(frozenset(E))
    codes = set()
    for E in trees:
        c = tuple(prufer_encode(n, E))
        codes.add(c)
        assert frozenset(tuple(sorted(e)) for e in prufer_decode(list(c))) == E
        assert list(c) == nx.to_prufer_sequence(nx.Graph(E))
    assert codes == set(product(range(n), repeat=n - 2))   # 打到每一個序列
    print(f"n={n}: 樹 {len(trees):5d} 棵 = n^(n-2) = {n ** (n - 2):5d}，編碼是雙射")
```

```text
編碼: [3, 3, 3, 4]
解碼: [(0, 3), (1, 3), (2, 3), (3, 4), (4, 5)]
NetworkX: [3, 3, 3, 4]
n=2: 樹     1 棵 = n^(n-2) =     1，編碼是雙射
n=3: 樹     3 棵 = n^(n-2) =     3，編碼是雙射
n=4: 樹    16 棵 = n^(n-2) =    16，編碼是雙射
n=5: 樹   125 棵 = n^(n-2) =   125，編碼是雙射
n=6: 樹  1296 棵 = n^(n-2) =  1296，編碼是雙射
n=7: 樹 16807 棵 = n^(n-2) = 16807，編碼是雙射
```

這段驗證了三件事，每一件對應證明的一部分：樹的個數等於 `n^(n−2)`（Cayley）、`decode(encode(T)) = T`（單射）、編碼打到**每一個**序列（滿射）。`n = 7` 要檢查 `C(21, 6) = 54264` 個邊子集，幾秒內跑完。我們的編碼和 `nx.to_prufer_sequence` 一致，因為 NetworkX 用的是同一個「拔最小葉子」慣例；不同書若改成「拔最大葉子」，序列會不同，但雙射性質不變。

## 應用：均勻隨機樹，以及一個常見的錯誤做法

測試圖演算法時常需要「隨機的樹」。最直覺的寫法是「點 `i` 依序接到前面隨機一個點」。它產生的確實是樹，但**不是均勻分布**：

```python
import heapq
import random
from collections import Counter
from math import factorial, prod


def prufer_decode(seq):
    n = len(seq) + 2
    remaining = [0] * n
    for x in seq:
        remaining[x] += 1
    leaves = [v for v in range(n) if remaining[v] == 0]
    heapq.heapify(leaves)
    edges = []
    for x in seq:
        leaf = heapq.heappop(leaves)
        edges.append((leaf, x))
        remaining[x] -= 1
        if remaining[x] == 0:
            heapq.heappush(leaves, x)
    edges.append((heapq.heappop(leaves), heapq.heappop(leaves)))
    return frozenset(tuple(sorted(e)) for e in edges)


n, trials = 4, 160_000
rng = random.Random(42)

# 錯誤做法：點 i 依序接到「前面隨機一個點」上
naive = Counter()
for _ in range(trials):
    E = frozenset((rng.randrange(i), i) for i in range(1, n))
    naive[E] += 1

# 正確做法：均勻抽一個 Prüfer 序列再解碼
good = Counter(prufer_decode([rng.randrange(n) for _ in range(n - 2)])
               for _ in range(trials))

print(f"n={n} 共 {n ** (n - 2)} 棵標記樹，每棵理想次數 {trials // n ** (n - 2)}")
print("逐點接上 :", len(naive), "種樹出現過，次數範圍", min(naive.values()), "~", max(naive.values()))
print("Prüfer 抽:", len(good), "種樹出現過，次數範圍", min(good.values()), "~", max(good.values()))

# 推論：度數為 d1..dn 的標記樹有 (n-2)! / Π(di-1)! 棵
# 檢查：n=7、度數 (3,3,2,1,1,1,1)（總和 12 = 2(n-1)）
target = [3, 3, 2, 1, 1, 1, 1]
n = len(target)
formula = factorial(n - 2) // prod(factorial(d - 1) for d in target)
count = 0
for code in range(n ** (n - 2)):
    seq = [code // n ** i % n for i in range(n - 2)]
    deg = Counter(v for e in prufer_decode(seq) for v in e)
    count += all(deg[v] == target[v] for v in range(n))
print("指定度數的樹：窮舉", count, "棵，公式", formula)
```

```text
n=4 共 16 棵標記樹，每棵理想次數 10000
逐點接上 : 6 種樹出現過，次數範圍 26549 ~ 26884
Prüfer 抽: 16 種樹出現過，次數範圍 9885 ~ 10241
指定度數的樹：窮舉 30 棵，公式 30
```

「逐點接上」只產生了 16 棵中的 6 棵：它產生的每棵樹都滿足「從 0 出發往外走，標籤一路遞增」（這叫 recursive tree，`n` 點恰有 `(n−1)! = 6` 棵），像 `0–2–1–3` 這種路徑永遠不會出現。用它測演算法，你等於只測了 6/16 的輸入空間，而且這個比例隨 `n` 增大急速下降（`(n−1)!/n^(n−2)` 趨近 0）。

Prüfer 抽樣把「均勻抽一棵樹」化成「均勻抽 `n − 2` 個數」，正確性直接來自雙射。

最後一段驗證多項式係數推論：度數 `(3, 3, 2, 1, 1, 1, 1)` 的樹有 `5!/(2!·2!·1!) = 30` 棵。寫這段時我們一開始打成 `(3, 2, 2, 1, 1, 1, 1)`，窮舉結果是 0、公式卻給 60——因為度數總和 11 不等於 `2(n−1) = 12`，這種樹根本不存在，而公式不會替你檢查這個前提。

## 對比與取捨

| Cayley 公式的證明 | 想法 | 優點 | 缺點 |
| --- | --- | --- | --- |
| Prüfer 雙射（本章） | 樹 ↔ 序列 | 可執行：編碼、解碼、抽樣一次到位 | 證明細節（引理 4）要小心 |
| 雙重計數（Pitman 1999） | 數「有根森林加邊的序列」兩種方式 | 幾行就證完，非常漂亮 | 不給出具體雙射 |
| Joyal 的雙射 | 雙標記樹 ↔ 函數 `[n] → [n]` | 和函數圖（functional graph）連結，很優雅 | 構造比 Prüfer 抽象 |
| 矩陣樹定理（Ch 28） | `K_n` 的 Laplacian 行列式 | 推廣到**任意圖**的生成樹計數 | 需要線性代數；對 `K_n` 而言大材小用 |

Aigner–Ziegler《Proofs from THE BOOK》的 Cayley 公式那一章收了四種證明：雙射、線性代數、遞迴、Pitman 的雙重計數，可以和這張表對照讀。

| 隨機樹產生法 | 分布 | 時間 |
| --- | --- | --- |
| 逐點接到前面隨機點 | 偏向「淺而寬」，只覆蓋 recursive tree | `O(n)` |
| 隨機 Prüfer 序列 + 解碼 | 在所有標記樹上均勻 | `O(n log n)`（可做到 `O(n)`） |
| 隨機權重 + MST（Ch 10） | **不**均勻 | `O(m log m)` |
| Wilson 演算法（loop-erased random walk） | 在任意圖的生成樹上均勻 | 期望時間和圖的 hitting time 相關 |

## 踩雷集錦

1. **把「無圈」或「`m = n−1`」單獨當成樹的定義**：一個三角形加一個孤立點，4 點 3 邊，`m = n − 1` 但不連通也有圈。等價定義永遠是「三個條件（連通、無圈、`m = n−1`）任取兩個」。
2. **混淆標記樹與非同構樹**：`n^(n−2)` 數的是標記樹。7 點有 16807 棵標記樹，但只有 11 種形狀。問題若問「有幾種結構」，答案不是 Cayley 公式。
3. **Prüfer 編碼拔錯葉子**：「拔最小葉子」的「最小」是**當下**的葉子集合裡最小，不是原始樹的葉子裡最小。拔掉葉子後，它的鄰居可能變成新葉子而且比其他葉子更小（例子裡的 `3` 就是這樣在第四步被拔掉）。用 heap 而不是預先排序的 list，就是為了處理這件事。
4. **度數公式不檢查前提**：`(n − 2)!/Π(di − 1)!` 只在 `Σ di = 2(n − 1)` 且每個 `di ≥ 1` 時才有意義；違反時公式照樣吐出一個正數（上面的 60），但真正的答案是 0。
5. **以為「隨機權重 + MST」是均勻隨機生成樹**：直覺上權重是均勻的，樹也該是均勻的，但不是。小圖窮舉就能看出偏差（動手練習 3）。

## 進階：再往深一層

- **線性時間 Prüfer**：維護一個指標從小到大掃描「目前最小葉子」，並利用「新產生的葉子若比指標小就立刻處理」的觀察，可以拿掉 heap，做到 `O(n)`。這是常見的面試追問。
- **矩陣樹定理**：Kirchhoff 的定理說任意圖的生成樹數等於 Laplacian 任一個餘子式。對 `K_n` 算出來正是 `n^(n−2)`。Ch 28 會證明它，屆時你可以用它重新推一次 Cayley。
- **樹的同構是容易的**：一般圖同構沒有已知多項式演算法（Ch 7），但樹的同構有線性時間演算法（AHU 演算法，把每棵子樹編碼成括號字串再排序）。`nx.to_nested_tuple(T, root, canonical_form=True)` 就是同一個想法。這說明「結構限制」能讓困難問題變容易，Ch 36 的 treewidth 把這個想法推到極致。
- **Prüfer 序列在資料結構上的用途**：把樹壓成 `n − 2` 個整數，是隨機測試、模糊測試（fuzzing）產生樹狀輸入時的標準技巧——你可以對序列做 mutation，保證解碼後永遠是合法的樹。

## 動手練習

1. 實作線性時間的 `prufer_encode_linear`，和 heap 版在 `n ≤ 7` 的所有樹上對拍，再在 `n = 10⁶` 的隨機樹上比較時間。
2. 修改窮舉程式，數「`n` 點中，點 `0` 是葉子的標記樹」有幾棵。用引理 4 預測答案（提示：序列中不出現 0），再用程式驗證。
3. 在 `K_4` 上給 6 條邊均勻隨機權重（固定 seed），重複 16 萬次取 MST，看 16 棵生成樹的出現次數是否均勻。哪些樹比較常出現？為什麼？
4. 把引理 1 改寫成程式：用 union-find 一邊加邊一邊數閉合邊 `k`，驗證 `c = n − m + k` 在 1000 張隨機圖上都成立。（Ch 10 的 Kruskal 就是這個程式加上「跳過閉合邊」。）

## 本章重點整理

- 樹的六種定義等價；核心是引理 1：`c(G) = n − m + k`，閉合邊數 `k = 0` ⇔ 無圈。連通、無圈、`m = n − 1` 三者任二推第三。
- 每棵 `n ≥ 2` 的樹至少兩片葉子，拔葉子保持是樹——這是樹上歸納法的標準起手式。
- Prüfer 編碼中每個點出現 `deg − 1` 次；由此「拔最小葉子」的過程可以逆推，給出標記樹與 `V^(n−2)` 的雙射，證明 Cayley 公式 `n^(n−2)`。
- 雙射證明可以執行：均勻抽序列再解碼 = 均勻隨機樹；直覺的「逐點接上」不是均勻的。

## 自我檢核

- [ ] 不看筆記，用引理 1 在三行內證明「連通 + `m = n − 1` ⇒ 無圈」。
- [ ] 能解釋為什麼 Prüfer 證明要對「任意有全序的標籤集合」歸納，而不是只對 `{1, …, n}`。
- [ ] 給你序列 `[2, 2, 0]`，能手動解碼出樹，並說出每一步拔的葉子是誰。
- [ ] 如果同事寫了「每個新點接到前面隨機一個點」來產生測試用的樹，你能說明它漏掉哪類輸入，並給出修正。
- [ ] 能說出「標記樹的個數」和「非同構樹的個數」差在哪，各自在什麼問題裡出現。

## 延伸閱讀

### 論文

- **A theorem on trees** — Arthur Cayley，Quarterly Journal of Pure and Applied Mathematics 23:376–378（1889）
  - **核心貢獻**：以 Cayley 命名的公式出處。論文很短，只用 `n = 6` 的例子說明想法，並引用了 Borchardt 更早的工作——讀它可以看到「公式」和「證明」在 19 世紀數學裡的落差。
  - **讀哪裡**：全文三頁；收錄在 Cayley 的《Collected Mathematical Papers》第 13 卷。沒有 DOI，透過大學圖書館的期刊回溯檔或文集取得。
  - **前提**：本章的 Cayley 公式敘述。
- **Neuer Beweis eines Satzes über Permutationen** — Heinz Prüfer，Archiv der Mathematik und Physik (3) 27:142–144（1918）
  - **核心貢獻**：本章雙射的原始出處。有趣的是標題講的是「排列」——Prüfer 原本是在處理一個關於排列的計數問題，樹是他的工具。
  - **讀哪裡**：全文三頁（德文）；MacTutor 的 [Prüfer 著作列表](https://mathshistory.st-andrews.ac.uk/Extras/Prufer_publications) 有書目資訊。
  - **前提**：本章的雙射證明。

### 書籍

- **《Proofs from THE BOOK》** — Martin Aigner、Günter M. Ziegler（Springer，第 6 版，2018，[DOI](https://doi.org/10.1007/978-3-662-57265-8)）
  - **這本書的定位**：收錄「最漂亮的證明」的名著，每章獨立，適合當證明技巧的範本讀。
  - **讀哪幾章**：第 6 版第 33 章「Cayley's formula for the number of trees」，一章給四種證明（雙射、線性代數、遞迴、雙重計數）。讀完本章再讀，你會特別欣賞 Pitman 雙重計數證明的短。
  - **前提**：本章；線性代數證明需要行列式。
- **《Introduction to Graph Theory》** — Douglas B. West（第 2 版，2001）
  - **讀哪幾章**：Ch 2「Trees and Distance」的前兩節：樹的基本性質與等價定義、Prüfer 碼與 Cayley 公式，以及用矩陣樹定理計數生成樹。習題裡有大量「證明某個樹性質」的練習。
  - **前提**：本章。

### 官方文件

- **[NetworkX `to_prufer_sequence`](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.tree.coding.to_prufer_sequence.html)**
  - **讀哪裡**：說明節點必須標為 `0..n−1`、樹要至少 2 個點；同一模組的 `from_prufer_sequence` 是解碼，`to_nested_tuple` 是有根樹的標準形（樹同構）。
  - **注意**：docstring 寫明它採用 Wang、Wang、Wu（2009）的 `O(n)` 演算法，原始碼不到 20 行，可以當動手練習 1 的參考答案。

樹是「剛好連通」的極小結構。下一章給邊加上權重，問「最便宜的生成樹是哪一棵」，並證明為什麼貪婪地一條一條挑最便宜的邊，竟然不會出錯。

→ [Ch 10 最小生成樹與 cut/cycle property](./10-minimum-spanning-trees.md)
