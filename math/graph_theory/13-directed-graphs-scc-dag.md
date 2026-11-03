# Ch 13 — 有向圖：SCC 與 DAG

> **目標**：證明「強連通分量縮點後一定是 DAG」「DAG ⇔ 有拓撲序」；完整證明 Kosaraju 與 Tarjan 兩個 SCC 演算法的正確性，寫出兩者的迭代版並與 NetworkX 對拍；用拓撲序做 DAG 上的動態規劃（關鍵路徑、路徑計數）；最後從 Python 原始碼建出 call graph，用 SCC 找出所有遞迴與互相遞迴的函式群組。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

無向圖裡「連通」只有一種意思。加上方向之後，`u` 能走到 `v` 不代表 `v` 能走回 `u`，「連通」分裂成兩個概念：**弱連通**（忽略方向後連通）與**強連通**（任兩點互相可達）。

你每天碰到的有向圖：

- **相依圖**：`make`、`npm`、`cargo`、Bazel 的建置相依；套件管理器偵測「循環相依」就是在找有向圈。
- **Call graph**：函式 `f` 呼叫 `g` 就有邊 `f → g`。互相遞迴的函式形成一個強連通分量。LLVM 的 inliner 與過程間最佳化以「SCC 為單位、由下而上」處理 call graph（見 [ssa_optimizations 的 Call Graph 章](../../compilers/ssa_optimizations/27-call-graph.md)）。
- **控制流程圖（CFG）**：迴圈就是 CFG 裡的有向圈，Ch 14 與練習 B 會深入。

這章的結構定理很簡潔：任何有向圖都可以唯一地分解成強連通分量，把每個分量縮成一點，剩下的是一張**沒有圈的有向圖（DAG）**。圈都被「關」在分量裡，分量之間只有單向的依賴。這讓很多問題變成兩段式：分量內部處理圈，分量之間沿著 DAG 做動態規劃。

## 先建立直覺

```text
   a → b → e ⇄ f ← g         SCC：{a, b, c}、{d}、{e, f}、{g}
   ↑   ↓                      縮點之後：
   └── c → d ⟲                    {g} → {e,f} ← {a,b,c} → {d}
                              這張縮點圖沒有圈：它是 DAG
```

`a → b → c → a` 是一個圈，三個點互相可達，是一個 SCC。`d` 有自環但只能自己到自己。`e ⇄ f` 是一個 SCC。`g` 能到 `f`，但 `f` 回不到 `g`。

拓撲序的直覺是「穿衣服的順序」：襪子在鞋子之前，內衣在褲子之前。只要相依關係沒有循環（沒有「A 要在 B 之前，B 又要在 A 之前」），就排得出來；有循環就排不出來，而且排不出來的時候，我們要能**指出那個循環**，而不是只說「失敗」。

> 本章大量使用 DFS 的發現時間與完成時間。無向圖 DFS 的結構在 [Ch 11 — 連通度：割點、橋與 Tarjan](./11-connectivity-tarjan.md) 的「底層機制」一節；有向圖多了橫向邊，下面會說明。拓撲排序的刷題角度在 [leetcode_patterns 的拓撲排序章](../../algorithms/leetcode_patterns/21-topological-sort.md)。

## 定義與兩個結構定理

有向圖 `G = (V, E)`，`u ⇝ v` 表示有一條從 `u` 到 `v` 的有向路徑（長度 0 也算，所以 `u ⇝ u`）。

**互相可達**是等價關係：自反（長度 0 路徑）、對稱（定義本身對稱）、遞移（`u ⇝ v ⇝ w`，路徑接起來）。它的等價類就叫**強連通分量（strongly connected component, SCC）**。所以每個點恰好屬於一個 SCC，分解是唯一的。

**縮點圖（condensation）** `G^SCC`：每個 SCC 一個點，若 `G` 有邊從 SCC `C` 指到另一個 SCC `C'`，就畫 `C → C'`。

**定理 1**：`G^SCC` 是 DAG。

**證明**：若 `G^SCC` 有圈 `C1 → C2 → … → Ck → C1`（`k ≥ 2`），沿著這些邊與各分量內部的路徑，`C1` 的任一點能走到 `C2` 的任一點、也能走回來，所以 `C1` 和 `C2` 的點互相可達，它們應該是同一個 SCC，矛盾。∎

**拓撲序（topological order）**：把點排成 `v1, …, vn`，使每條邊 `vi → vj` 都滿足 `i < j`。

**定理 2**：`G` 有拓撲序 ⇔ `G` 沒有有向圈。

**證明**：（⇒）每條邊都往後指，沿著邊走編號嚴格遞增，回不到起點。（⇐）先證：非空的 DAG 一定有入度 0 的點。反設每個點入度都 ≥ 1，從任一點出發沿著「某條入邊」往回走，永遠走得下去；點是有限的，所以某個點會重複，重複的那一段反過來就是一個有向圈，矛盾。把入度 0 的點放第一個、刪掉它，剩下的圖仍是 DAG，歸納即得。∎

這個證明就是 **Kahn 演算法**（1962）。而且它在失敗時也是構造性的：Kahn 卡住時，剩下的每個點在剩下的子圖裡入度都 ≥ 1，照證明「往回走直到重複」就能交出一個圈。

## 有向 DFS：多了橫向邊

有向圖 DFS 的非樹邊有三種：指向祖先的**回邊**、指向後代的**前向邊**、指向「已經完成、不是祖先也不是後代」的點的**橫向邊（cross edge）**。無向圖沒有橫向邊（Ch 11 引理 1），有向圖有，因為「`u` 能到 `v`」不代表「`v` 能到 `u`」，DFS 從 `v` 那一側先走完了，回頭從 `u` 指過去時 `v` 早已完成。

用 `d(v)` 表示發現時間、`f(v)` 表示完成時間。我們會反覆用到：

**白色路徑定理（white-path theorem）**：`w` 會成為 `v` 的後代 ⇔ 在 `v` 被發現的那一刻，存在一條從 `v` 到 `w`、全部由**尚未發現**的點組成的路徑。（證明：⇒ 樹路徑本身就是；⇐ 對路徑長度歸納——DFS 在 `v` 結束前會掃完 `v` 的出邊，路徑上下一個點若還沒被別人發現，就會成為 `v` 的後代。）

由此得到：SCC `C` 中第一個被發現的點 `r(C)`（稱為 `C` 的**根**）被發現時，`C` 的其他點都還沒被發現，而且 `C` 內部互相可達，所以 **`C` 的所有點都是 `r(C)` 的後代**。

## Kosaraju：兩趟 DFS

```text
第一趟：在 G 上 DFS，記錄完成順序
第二趟：在 Gᵀ（所有邊反向）上，依完成時間「由晚到早」挑起點做 DFS
        每一棵 DFS 樹恰好是一個 SCC
```

對 SCC `C` 定義 `f(C) = max_{v∈C} f(v)`（第一趟的完成時間）。

**關鍵引理**：若有邊從 SCC `C` 指到另一個 SCC `C'`，則 `f(C) > f(C')`。

**證明**：分兩種情況看哪個分量先被碰到。

- `C` 先被發現：設 `x` 是 `C ∪ C'` 中第一個被發現的點（在 `C` 裡）。那一刻 `C ∪ C'` 全都還沒被發現，而 `x` 能沿著它們走到每一點（`C` 內互通，再經那條邊進 `C'`、`C'` 內互通）。由白色路徑定理，`C ∪ C'` 都是 `x` 的後代，都在 `x` 之前完成，所以 `f(C) ≥ f(x) > f(C')`。
- `C'` 先被發現：設 `y` 是 `C'` 中第一個被發現的點，`C'` 全部是 `y` 的後代。`C'` 走不到 `C`（否則兩者互相可達，會是同一個 SCC），所以 `y` 完成時 `C` 的點一個都還沒被發現，之後才完成：`f(C) > f(y) = f(C')`。∎

**正確性**：第二趟從完成時間最晚的點出發，它所在的 SCC `C` 的 `f(C)` 最大。在 `Gᵀ` 中，從 `C` 指出去的邊對應 `G` 中「從別的分量 `C'` 指向 `C`」的邊，由引理 `f(C') > f(C)`，與 `f(C)` 最大矛盾，所以這樣的邊不存在，`Gᵀ` 上的 DFS 恰好走遍 `C`（`C` 在 `Gᵀ` 中仍強連通）而不外溢。接下來的每一個起點，它的 SCC 在 `Gᵀ` 中指出去的邊只會通到 `f` 值更大、**已經被走過**的分量，所以同樣恰好走遍自己的 SCC。對處理順序歸納即得。∎

## Tarjan：一趟 DFS 加一個堆疊

Tarjan 把每個點在發現時推進一個額外的堆疊 `S`，並維護

```text
low(v) = min( index(v),
              index(w)：v 的子樹中某點有邊指向 w，且當時 w 還在 S 上 )
```

當 `v` 完成時若 `low(v) = index(v)`，就從 `S` 彈到 `v` 為止，彈出的點是一個 SCC。

**正確性**。我們對完成順序歸納，同時證明：(i) 每個 SCC 在它的根完成時被一次完整彈出；(ii) 一個點在 `S` 上 ⇔ 它的 SCC 的根還在 DFS 的路徑上（已發現、未完成）。

1. **非根的點 `u ∈ C` 有 `low(u) < index(u)`。** `u` 能走到根 `r = r(C)`。沿著這條路徑（它全在 `C` 內：路徑上每一點都能到 `r` 又能被 `r` 走到），取第一個不在 `u` 子樹裡的點 `x`，以及指向它的邊 `y → x`（`y` 在 `u` 的子樹）。`x` 不可能在 `y` 掃描這條邊時還沒被發現（那樣它會變成 `y` 的後代，落在 `u` 的子樹裡），而 `u` 的子樹之外、在 `u` 完成前被發現的點都比 `u` 早被發現，所以 `index(x) < index(u)`。`x ∈ C` 而 `C` 的根 `r` 還在 DFS 路徑上（`u` 是它的後代），由 (ii) `x` 仍在 `S` 上，所以 `low(u) ≤ index(x) < index(u)`。因此非根的點**不會**觸發彈出。
2. **根 `r` 有 `low(r) = index(r)`。** 反設 `r` 的子樹中某點有邊指向仍在 `S` 上、`index(x) < index(r)` 的 `x`。由 (ii)，`x` 的 SCC 的根 `r'` 還在 DFS 路徑上，又比 `r` 早被發現，所以 `r'` 是 `r` 的祖先。於是 `r ⇝ x ⇝ r' ⇝ r`，`r` 和 `r'` 在同一個 SCC，但 `r'` 比 `r` 早被發現，和 `r` 是根矛盾。
3. **根彈出的恰好是它的 SCC。** 根 `r` 完成時，`S` 上在 `r` 之上的點都是 `r` 子樹裡尚未被彈出的點。`C` 的點都在 `r` 子樹裡（白色路徑定理），由第 1 點它們沒有被自己彈出過；它們也不會被 `r` 子樹裡其他 SCC 的根 `r''` 彈出——若 `C` 的某點在 `r''` 子樹裡，`r'' ⇝ C ⇝ r ⇝ r''`，`r''` 就屬於 `C`。反過來，`r` 子樹裡不屬於 `C` 的點，各自的 SCC 根也在 `r` 子樹裡、已經完成並把它們彈走了（歸納假設）。所以彈出的集合正好是 `C`。

(ii) 由 1–3 隨之成立：點在發現時入堆疊，在它的 SCC 根完成時出堆疊。∎

兩個演算法都是 `O(n + m)`。Tarjan 只需一趟、不需要建反向圖；Kosaraju 的概念比較單純，而且兩趟各自只是普通的 DFS。注意輸出順序：Tarjan 先輸出「下游」的 SCC（反拓撲序），Kosaraju 輸出拓撲序——下面的對拍會驗證這一點。

## 實作：迭代版 Tarjan 與 Kosaraju

和 Ch 11 一樣，堆疊的每一格存「點 + 它的出邊迭代器」來模擬遞迴；Python 的 `for … else` 正好表達「出邊都掃完了」。

```python
import random
import networkx as nx


def tarjan_scc(succ):
    """迭代版 Tarjan。回傳 SCC 列表，順序是「反拓撲序」（沒有出邊的 SCC 先出來）。"""
    index, low, on_stack = {}, {}, set()
    stack, sccs, counter = [], [], 0
    for root in succ:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(succ[root]))]
        while work:
            v, it = work[-1]
            for w in it:
                if w not in index:                       # 樹邊：往下
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(succ[w])))
                    break
                if w in on_stack:                        # 回邊，或指向同一個 SCC 的橫向邊
                    low[v] = min(low[v], index[w])
            else:                                        # v 的出邊都處理完了
                work.pop()
                if work:
                    p = work[-1][0]
                    low[p] = min(low[p], low[v])
                if low[v] == index[v]:                   # v 是它所在 SCC 的根
                    comp = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    sccs.append(comp)
    return sccs


def kosaraju_scc(succ):
    """第一趟：在 G 上 DFS 記錄完成順序；第二趟：依完成時間遞減，在 G^T 上 DFS。"""
    order, seen = [], set()
    for root in succ:
        if root in seen:
            continue
        seen.add(root)
        work = [(root, iter(succ[root]))]
        while work:
            v, it = work[-1]
            for w in it:
                if w not in seen:
                    seen.add(w)
                    work.append((w, iter(succ[w])))
                    break
            else:
                work.pop()
                order.append(v)                          # 完成時間由小到大
    pred = {v: [] for v in succ}
    for u in succ:
        for w in succ[u]:
            pred[w].append(u)
    comp_of, sccs = {}, []
    for root in reversed(order):
        if root in comp_of:
            continue
        comp, todo = [], [root]
        comp_of[root] = len(sccs)
        while todo:
            v = todo.pop()
            comp.append(v)
            for u in pred[v]:
                if u not in comp_of:
                    comp_of[u] = len(sccs)
                    todo.append(u)
        sccs.append(comp)
    return sccs                                          # 拓撲序（源頭 SCC 先出來）


def canon(sccs):
    return sorted(sorted(c) for c in sccs)


# 用 list 而不是 set 當鄰接：字串的 set 迭代順序每次執行可能不同，輸出順序會跟著變
g = {"a": ["b"], "b": ["c", "e"], "c": ["a", "d"], "d": ["d"], "e": ["f"], "f": ["e"], "g": ["f"]}
print("Tarjan  :", [sorted(c) for c in tarjan_scc(g)])
print("Kosaraju:", [sorted(c) for c in kosaraju_scc(g)])

rng = random.Random(13)
for t in range(1500):
    n = rng.randint(1, 15)
    G = nx.gnp_random_graph(n, rng.choice([0.05, 0.1, 0.2, 0.4]), seed=rng.randint(0, 10**9), directed=True)
    succ = {v: set(G[v]) for v in G}
    truth = canon(nx.strongly_connected_components(G))
    T, K = tarjan_scc(succ), kosaraju_scc(succ)
    assert canon(T) == canon(K) == truth
    # 順序性質：Tarjan 輸出反拓撲序、Kosaraju 輸出拓撲序
    pos_t = {v: i for i, c in enumerate(T) for v in c}
    pos_k = {v: i for i, c in enumerate(K) for v in c}
    for u, w in G.edges():
        assert pos_t[u] >= pos_t[w] and pos_k[u] <= pos_k[w]
print("1500 張隨機有向圖：Tarjan = Kosaraju = NetworkX，且兩者的輸出順序符合（反）拓撲序")

chain = {i: {i + 1} for i in range(199_999)}
chain[199_999] = {0}                                     # 20 萬個點的大圈
print("20 萬點的圈：Tarjan 找到", len(tarjan_scc(chain)), "個 SCC，Kosaraju 找到", len(kosaraju_scc(chain)), "個")
```

```text
Tarjan  : [['d'], ['e', 'f'], ['a', 'b', 'c'], ['g']]
Kosaraju: [['g'], ['a', 'b', 'c'], ['e', 'f'], ['d']]
1500 張隨機有向圖：Tarjan = Kosaraju = NetworkX，且兩者的輸出順序符合（反）拓撲序
20 萬點的圈：Tarjan 找到 1 個 SCC，Kosaraju 找到 1 個
```

實作細節對照證明：

- Tarjan 的 `if w in on_stack` 涵蓋回邊**和**部分橫向邊。指向「已完成、但它的 SCC 還沒被彈出」的點的橫向邊也要算——那個點和 `v` 在同一個 SCC（證明第 1 點的 `x` 可以是這種點）。指向「已經被彈出」的點的橫向邊則不能算，那是另一個已經結束的 SCC。初學者把條件寫成 `if w in index`（只要被發現過就算）是最常見的錯誤。
- Kosaraju 第二趟用普通的堆疊 DFS 即可（不需要記錄完成時間），因為它只是在找可達集合。
- 對拍除了比較分量集合，還驗證了**輸出順序**：對每條邊 `u → w`，Tarjan 中 `w` 的分量不晚於 `u` 的分量出現（反拓撲序），Kosaraju 剛好相反。這個性質讓 Tarjan 的輸出可以直接拿來「由下而上」處理 call graph。

## 拓撲排序、圈證據與 DAG 上的 DP

```python
import random
from collections import deque
import networkx as nx


def kahn_toposort(succ):
    """Kahn：反覆拿掉入度 0 的點。回傳 (順序, None) 或 (None, 一個有向圈)。"""
    indeg = {v: 0 for v in succ}
    for u in succ:
        for w in succ[u]:
            indeg[w] += 1
    q = deque(v for v in succ if indeg[v] == 0)
    order = []
    while q:
        u = q.popleft()
        order.append(u)
        for w in succ[u]:
            indeg[w] -= 1
            if indeg[w] == 0:
                q.append(w)
    if len(order) == len(succ):
        return order, None
    # 剩下的點入度都 ≥ 1（只算剩下的點之間）：沿著「前驅」往回走，一定會撞到圈
    left = {v for v in succ if indeg[v] > 0}
    pred = {}
    for u in sorted(left):
        for w in succ[u]:
            if w in left:
                pred[w] = u                              # 每個剩下的點都有一個剩下的前驅
    v, seen = next(iter(sorted(left))), {}
    while v not in seen:
        seen[v] = len(seen)
        v = pred[v]
    cyc = [v]
    u = pred[v]
    while u != v:
        cyc.append(u)
        u = pred[u]
    return None, cyc[::-1]


def longest_path_dag(succ, w, order):
    """DAG 上的最長路徑（例如關鍵路徑）：依拓撲序鬆弛，O(n + m)。"""
    best = {v: 0 for v in succ}
    parent = {v: None for v in succ}
    for u in order:
        for x in succ[u]:
            if best[u] + w[u, x] > best[x]:
                best[x], parent[x] = best[u] + w[u, x], u
    end = max(best, key=best.get)
    path = [end]
    while parent[path[-1]] is not None:
        path.append(parent[path[-1]])
    return best[end], path[::-1]


def count_paths(succ, order, s, t):
    ways = {v: 0 for v in succ}
    ways[s] = 1
    for u in order:
        for x in succ[u]:
            ways[x] += ways[u]
    return ways[t]


# 建置任務：權重 = 任務 u 做完到 x 可以開始的時間（這裡取 u 的耗時）
tasks = {"fetch": 2, "configure": 1, "gen_headers": 3, "compile": 5, "test": 4, "docs": 2, "package": 1}
deps = [("fetch", "configure"), ("configure", "gen_headers"), ("configure", "docs"),
        ("gen_headers", "compile"), ("compile", "test"), ("compile", "package"),
        ("docs", "package"), ("test", "package")]
succ = {t: [] for t in tasks}
for u, x in deps:
    succ[u].append(x)
order, cyc = kahn_toposort(succ)
length, path = longest_path_dag(succ, {(u, x): tasks[u] for u, x in deps}, order)
print("拓撲序:", order)
print("關鍵路徑:", " → ".join(path), " 長度", length + tasks[path[-1]])
print("fetch 到 package 的路徑數:", count_paths(succ, order, "fetch", "package"))

succ["package"].append("configure")                    # 製造循環依賴
print("加入 package → configure 之後:", kahn_toposort(succ))

# 對拍：隨機 DAG（只保留 i<j 的邊）與隨機有向圖
rng = random.Random(5)
for t in range(1000):
    n = rng.randint(1, 12)
    G = nx.gnp_random_graph(n, 0.3, seed=rng.randint(0, 10**9), directed=True)
    if t % 2 == 0:
        G.remove_edges_from([(u, v) for u, v in list(G.edges()) if u >= v])
    succ = {v: sorted(G[v]) for v in G}
    order, cyc = kahn_toposort(succ)
    assert (order is not None) == nx.is_directed_acyclic_graph(G)
    if order:
        pos = {v: i for i, v in enumerate(order)}
        assert all(pos[u] < pos[v] for u, v in G.edges())
        wts = {(u, v): 1 for u, v in G.edges()}
        assert longest_path_dag(succ, wts, order)[0] == nx.dag_longest_path_length(G)
    else:
        assert all(cyc[(i + 1) % len(cyc)] in succ[cyc[i]] for i in range(len(cyc)))
        assert len(set(cyc)) == len(cyc)
    C = nx.condensation(G)                               # 縮點圖一定是 DAG
    assert nx.is_directed_acyclic_graph(C)
print("1000 張隨機圖：拓撲序、圈證據、最長路徑全部驗過；每張圖的 condensation 都是 DAG")
```

```text
拓撲序: ['fetch', 'configure', 'gen_headers', 'docs', 'compile', 'test', 'package']
關鍵路徑: fetch → configure → gen_headers → compile → test → package  長度 16
fetch 到 package 的路徑數: 3
加入 package → configure 之後: (None, ['test', 'package', 'configure', 'gen_headers', 'compile'])
1000 張隨機圖：拓撲序、圈證據、最長路徑全部驗過；每張圖的 condensation 都是 DAG
```

三個重點：

1. **DP 的正確性來自拓撲序**：處理 `u` 時，`u` 的所有前驅都已處理完，所以 `best[u]` 已經是最終值。這和 Ch 10 的「不變量 + 歸納」是同一種論證。關鍵路徑長 16 = 2 + 1 + 3 + 5 + 4 + 1，即使有 8 核心平行建置，這次 build 也不可能少於 16 個時間單位。
2. **失敗時交出圈**：循環依賴的錯誤訊息應該是「`test → package → configure → gen_headers → compile → test`」，而不是「偵測到循環」。上面的程式照定理 2 的證明往回走，拿到的就是這個圈。
3. **最長路徑只在 DAG 上容易**：一般有向圖的最長**簡單**路徑問題包含 Hamilton 路徑（Ch 8）當特例，是 NP-hard。DAG 上之所以容易，正是因為拓撲序讓「簡單」這個條件自動成立——沿著拓撲序走不可能重複點。

## 應用：從原始碼找出遞迴

```python
import ast
import networkx as nx

SOURCE = '''
def parse(tokens):      return parse_expr(tokens)
def parse_expr(t):      return parse_term(t) or parse_atom(t)
def parse_term(t):      return parse_atom(t)
def parse_atom(t):      return parse_expr(t[1:]) if t and t[0] == "(" else t
def fact(n):            return 1 if n == 0 else n * fact(n - 1)
def is_even(n):         return n == 0 or is_odd(n - 1)
def is_odd(n):          return n != 0 and is_even(n - 1)
def main():             return parse("x"), fact(5), is_even(4), log("done")
def log(msg):           print(msg)
'''


def call_graph(src):
    """極簡 call graph：只看「在函式 f 內直接以名字呼叫 g()」，g 必須是同檔案定義的函式。"""
    tree = ast.parse(src)
    funcs = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)}
    succ = {name: [] for name in funcs}
    for name, f in funcs.items():
        for node in ast.walk(f):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in funcs and node.func.id not in succ[name]:
                succ[name].append(node.func.id)
    return succ


succ = call_graph(SOURCE)
G = nx.DiGraph([(u, v) for u in succ for v in succ[u]])
G.add_nodes_from(succ)
recursive = []
for comp in nx.strongly_connected_components(G):
    if len(comp) > 1 or any(G.has_edge(v, v) for v in comp):   # 自環 = 直接遞迴
        recursive.append(sorted(comp))
print("遞迴群組:", sorted(recursive))

C = nx.condensation(G)
bottom_up = [sorted(C.nodes[c]["members"]) for c in reversed(list(nx.topological_sort(C)))]
print("bottom-up 處理順序（被呼叫者先）:", bottom_up)
```

```text
遞迴群組: [['fact'], ['is_even', 'is_odd'], ['parse_atom', 'parse_expr', 'parse_term']]
bottom-up 處理順序（被呼叫者先）: [['parse_atom', 'parse_expr', 'parse_term'], ['log'], ['is_even', 'is_odd'], ['fact'], ['parse'], ['main']]
```

這個小工具示範了三件事：

- **遞迴的精確定義是「在大小 > 1 的 SCC 裡，或有自環」**。`fact` 是單點 SCC 但有自環；`is_even`/`is_odd` 是互相遞迴；遞迴下降 parser 的 `parse_expr → parse_term → parse_atom → parse_expr` 是三個函式的互相遞迴。`parse` 本身不遞迴，它只是呼叫了一個遞迴群組。
- **bottom-up 順序就是縮點圖的反拓撲序**：編譯器的 inliner 先處理被呼叫者，才能把已經最佳化過的函式內聯進呼叫者；同一個 SCC 內的函式必須一起處理，因為它們互相依賴。這也是為什麼 Tarjan 的「反拓撲序輸出」在 LLVM 的 CGSCC pass manager 裡這麼實用。
- **這個 call graph 是不完整的**：它看不到間接呼叫（`f = parse; f()`）、方法呼叫、`getattr`、跨模組呼叫。真實的 call graph 建構需要 points-to 分析，而且永遠是近似（不可判定）——資安的程式碼審計在這裡吃了很多苦頭，見 [code_auditing 的程式表示法章](../../security/code_auditing/03-program-representations-cpg.md)。

## 對比與取捨

| 演算法 | 趟數 | 額外空間 | 輸出順序 | 優點 | 缺點 |
| --- | --- | --- | --- | --- | --- |
| Tarjan | 1 次 DFS | `index`、`low`、堆疊 | 反拓撲序 | 不用建反向圖，常數小 | 正確性證明較繞，`on_stack` 條件易寫錯 |
| Kosaraju | 2 次 DFS | 反向圖 | 拓撲序 | 概念簡單，兩趟都是普通 DFS | 需要 `Gᵀ`（記憶體翻倍） |
| Pearce（2016） | 1 次 | 比 Tarjan 少 | 反拓撲序 | 省空間 | 較少人知道 |
| 暴力（每點做兩次 BFS） | `n` 次 | `O(n)` | — | 不會錯，適合對拍 | `O(n(n + m))` |

| 拓撲排序 | 想法 | 失敗時 |
| --- | --- | --- |
| Kahn（入度歸零） | 定理 2 的證明直譯 | 剩下的點都在圈上或圈的下游，往回走找圈 |
| DFS 完成時間遞減 | 完成晚的點排前面 | 遇到回邊即有圈，堆疊上的那段就是圈 |

## 踩雷集錦

1. **Tarjan 寫成 `if w in index`**：把「已經被彈出的其他 SCC」的點也算進 `low`，會把兩個 SCC 錯誤地合併。條件必須是 `w` 還在堆疊上。
2. **把「弱連通」當成「強連通」**：`nx.is_connected` 對有向圖會丟例外，有人改用 `nx.is_weakly_connected` 了事，但「弱連通」只代表忽略方向後連通，和「互相可達」差很遠。依賴分析要的幾乎都是強連通。
3. **以為自環不影響 SCC**：自環不改變 SCC 的劃分（單點本來就是自己的 SCC），但它改變「是否有圈」。用「SCC 大小 > 1」偵測遞迴會漏掉直接遞迴的 `fact`。
4. **在有圈的圖上跑 DAG DP**：Kahn 會靜靜地只輸出一部分點，DP 結果看起來合理但是錯的。一定要檢查 `len(order) == n`。`nx.topological_sort` 在有圈時會丟 `NetworkXUnfeasible`，這是好的設計。
5. **遞迴版 DFS 的深度**：一條 20 萬個點的相依鏈在真實的 monorepo 裡並不罕見；遞迴版 Tarjan 會 `RecursionError`。上面的迭代版處理 20 萬點的圈沒有問題。

## 進階：再往深一層

- **2-SAT**：每個變數 `x` 拆成 `x`、`¬x` 兩個點，子句 `(a ∨ b)` 變成 `¬a → b`、`¬b → a`。公式可滿足 ⇔ 沒有 `x` 和 `¬x` 落在同一個 SCC；而 Tarjan 的反拓撲序直接給出一組賦值。這是 SCC 最漂亮的應用之一，也是 [sat_smt 課程](../../programming/sat_smt/README.md)裡「為什麼 2-SAT 是多項式、3-SAT 是 NP-complete」的另一個例子。
- **增量式循環偵測**：建置系統或資料庫的 deadlock 偵測需要「一邊加邊、一邊判斷有沒有圈」。每次重跑 DFS 是 `O(m)`；有專門的增量演算法（例如 Pearce–Kelly 的動態拓撲序維護）能做得更好。
- **傳遞閉包與可達性查詢**：「`u` 能不能到 `v`」的大量查詢，先縮點成 DAG 再做（DAG 上的閉包可以用 bitset DP）。程式分析裡的 Datalog 引擎（CodeQL 底層）大量做這件事，Ch 38 會回來。
- **有向圖的 Menger**：Ch 12 的定理對有向圖也成立；有向圖的「必經點」問題則由下一章的 dominator tree 處理。

## 動手練習

1. 把 Tarjan 的 `if w in on_stack` 改成 `if w in index`，在對拍迴圈裡找出第一張出錯的圖，畫出來並解釋哪兩個 SCC 被錯誤合併。
2. 用 DFS 完成時間實作第二種拓撲排序，並在有圈時從遞迴堆疊（迭代版的 `work`）中取出圈。和 Kahn 版對拍。
3. 實作 2-SAT：輸入子句列表，用本章的 `tarjan_scc` 判斷可滿足性並給出賦值；用 `itertools.product` 暴力列舉所有賦值驗證 200 個隨機公式（變數 ≤ 8）。
4. 擴充 `call_graph`：處理類別方法（`self.f()`）和巢狀函式，對你自己寫過的某個 Python 專案跑一次，列出所有遞迴群組。

## 本章重點整理

- 互相可達是等價關係，等價類 = SCC；縮點圖一定是 DAG。DAG ⇔ 有拓撲序，Kahn 演算法就是這個證明，失敗時可以交出圈。
- Kosaraju 的關鍵引理：`C → C'` ⇒ `f(C) > f(C')`；依完成時間遞減在反向圖上 DFS，每棵樹是一個 SCC。
- Tarjan：一趟 DFS，`low(v) = index(v)` ⇔ `v` 是 SCC 的根；`low` 只能用仍在堆疊上的點更新。輸出是反拓撲序。
- DAG 上沿拓撲序做 DP：關鍵路徑、路徑計數都是 `O(n + m)`；一般圖的最長路徑則是 NP-hard。
- Call graph 的遞迴 = 大小 > 1 的 SCC 或自環；編譯器依縮點圖的反拓撲序由下而上處理。

## 自我檢核

- [ ] 不看筆記，證明 Kosaraju 的關鍵引理的兩種情況，並說明第二種情況哪裡用到了「`C'` 走不到 `C`」。
- [ ] 能解釋 Tarjan 為什麼要用「還在堆疊上」而不是「已經被發現」來更新 `low`，並舉一個會出錯的小圖。
- [ ] 如果同事的建置工具只回報「circular dependency detected」，你能說明怎麼改成印出整個圈，以及為什麼這總是做得到。
- [ ] 能說出為什麼 DAG 上的最長路徑是線性時間，一般圖卻是 NP-hard，兩者的差別在證明的哪一步。
- [ ] 能用 SCC 的語言精確定義「遞迴函式」，並指出只看 SCC 大小會漏掉哪種情況。

## 延伸閱讀

### 論文

- **[Depth-First Search and Linear Graph Algorithms](https://doi.org/10.1137/0201010)** — Robert Tarjan，SIAM Journal on Computing 1(2):146–160（1972）
  - **核心貢獻**：一趟 DFS 的 SCC 演算法原始論文（與 Ch 11 的雙連通分量同一篇）；有向 DFS 的四種邊分類也在這裡系統化。
  - **讀哪裡**：強連通分量那一節；它的 `LOWLINK` 定義和本章的 `low` 一致，證明可以和本章的三步對照。
  - **前提**：本章 Tarjan 一節。
- **[A strong-connectivity algorithm and its applications in data flow analysis](https://doi.org/10.1016/0898-1221(81)90008-0)** — Micha Sharir，Computers & Mathematics with Applications 7(1):67–72（1981）
  - **核心貢獻**：兩趟 DFS 的 SCC 演算法的第一篇正式發表。這個演算法通常歸功於 Kosaraju（1978 年左右的未發表講義），Sharir 獨立發現並把它用在資料流分析上——標題就點出了 SCC 和編譯器的關係。
  - **讀哪裡**：演算法描述與它在資料流分析中的應用段落。
  - **前提**：本章 Kosaraju 一節。
- **[Topological sorting of large networks](https://doi.org/10.1145/368996.369025)** — A. B. Kahn，Communications of the ACM 5(11):558–562（1962）
  - **核心貢獻**：「反覆拿掉入度 0 的點」的演算法；原始動機是大型 PERT 排程網路。
  - **讀哪裡**：演算法與它如何偵測錯誤（循環）的段落。
  - **前提**：本章定理 2。
- **[A space-efficient algorithm for finding strongly connected components](https://doi.org/10.1016/j.ipl.2015.08.010)** — David J. Pearce，Information Processing Letters 116(1):47–52（2016）
  - **核心貢獻**：把 Tarjan 演算法的額外空間壓到大約每點一個整數（省掉獨立的 `index`、`low` 兩個陣列），並討論了迭代版的寫法。
  - **讀哪裡**：全文；它的迭代版虛擬碼可以和本章的實作對照。
  - **前提**：本章 Tarjan 實作。

### 書籍

- **《Introduction to Algorithms》** — Cormen、Leiserson、Rivest、Stein（MIT Press，第 4 版，2022）
  - **讀哪幾章**：Ch 20「Elementary Graph Algorithms」：DFS 的括號定理、白色路徑定理、拓撲排序，以及用 Kosaraju 演算法講 SCC（本章的關鍵引理就是 CLRS 的寫法）。
  - **前提**：寫過 DFS。
- **《The Art of Computer Programming, Vol. 1》** — Donald E. Knuth（Addison-Wesley，第 3 版，1997）
  - **讀哪幾章**：§2.2.3「Linked Allocation」裡的 Algorithm T（拓撲排序），用的是 Kahn 式的入度計數；Knuth 對資料結構的細節處理是這本書的招牌。
  - **前提**：能讀 MIX 風格的演算法描述。

Tarjan 與 Kosaraju 告訴我們哪些點**互相**可達。編譯器還需要一個更精細的問題：從程式入口出發，哪些基本塊是到達某個基本塊的**必經之路**？下一章的 dominator tree 回答它，並成為 SSA 建構的基石。

→ [Ch 14 Dominator tree 與 Lengauer–Tarjan](./14-dominator-tree.md)
