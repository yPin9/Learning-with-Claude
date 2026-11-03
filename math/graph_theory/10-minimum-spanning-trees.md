# Ch 10 — 最小生成樹與 cut/cycle property

> **目標**：證明 cut property 與 cycle property，並用同一個交換論證證明 Kruskal、Prim、Borůvka 三個貪婪演算法都正確（包含權重平手的情況）；自刻三者與 union-find，和 NetworkX 的三種實作及暴力窮舉三方對拍，並親眼看到「平手沒處理好」與「union-find 少了優化」會怎麼壞。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11（AMD Ryzen 7 5700G）。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

1926 年，捷克數學家 Borůvka 被請去幫摩拉維亞地區規劃電網：怎麼用最短的總線長把所有城鎮連起來？這就是最小生成樹（Minimum Spanning Tree, MST）。接下來三十年，同一個問題被至少三組人獨立重新發現：Jarník（1930）、Kruskal（1956）、Prim（1957）——Prim 的演算法今天在部分文獻裡叫 Jarník 演算法就是這個原因。

MST 在工程上的價值不只是「省電線」：單連結階層式分群（single-linkage clustering）就是 MST 砍掉最重的 `k−1` 條邊；度量 TSP 的 2-近似與 Christofides 演算法從 MST 出發；網路裡的 spanning tree protocol 也是生成樹（雖然它求的不是最小權重）。

但這章真正的主角是**證明**。你刷題時寫過 Kruskal，多半沒想過「為什麼每次挑最便宜的邊不會出錯」。貪婪演算法大多是錯的（找零錢、背包、最短路徑上的貪婪都有反例）；MST 是少數貪婪正確的問題，而它正確的理由——cut property——是 Ch 25 matroid 理論的原型。

## 先建立直覺

```text
             切一刀：S = {a, b}         V∖S = {c, d, e}
     a ─── 4 ─── c
     │ ╲         │                   跨過這一刀的邊：a–c (4)、a–d (7)、b–d (2)
     1   7       3                   最輕的是 b–d (2)
     │     ╲     │
     b ─── 2 ─── d ─── 5 ─── e        cut property：b–d 一定可以放進某棵 MST
```

想像你要把 `{a, b}` 這個「已經接好的島」和其他城鎮連起來。無論最終的 MST 長什麼樣，它總得**至少**有一條邊跨過這一刀（否則不連通）。如果某棵 MST 跨刀用的是比較貴的邊，把它換成最便宜的那條跨刀邊，總長只會變短或不變——這就是整章唯一的證明技巧：**交換論證（exchange argument）**。

> 本章大量使用 Ch 9 的樹等價定義：「連通 + `n−1` 條邊 ⇒ 樹」，以及「樹加一條邊恰好產生一個圈」。不熟的話先回看 [Ch 9 — 樹：等價定義、Cayley 公式、Prüfer 編碼](./09-trees-cayley-prufer.md) 的「六個等價定義」。union-find 的基本操作在 [leetcode_patterns 的 union-find 章](../../algorithms/leetcode_patterns/22-union-find.md) 有刷題角度的介紹，本章補上它的正確性與複雜度。

## 定義與兩個性質

連通的加權圖 `G = (V, E, w)` 的**生成樹**是包含所有點的樹子圖；**MST** 是總權重 `w(T) = Σ_{e∈T} w(e)` 最小的生成樹。`G` 不連通時，每個分量各取一棵 MST，合起來叫最小生成森林；下面的實作都直接處理這種情況。

**切（cut）**：點集的一個分割 `(S, V∖S)`，`S` 非空且不等於 `V`。兩端分屬兩側的邊叫**跨切邊（crossing edge）**。說一個邊集合 `F` **尊重**這個切，意思是 `F` 裡沒有跨切邊。

### 一個基本事實：圈跨切的次數是偶數

沿著圈走一圈，每次經過跨切邊就換一側；回到起點時在原來那一側，所以換側次數是偶數。因此**圈只要用到一條跨切邊，就至少用到兩條**。這是下面兩個證明的共同引擎。

### Cut property（含平手的版本）

**定理（cut property）**：設 `F` 是某棵 MST `T` 的子集，`(S, V∖S)` 是一個尊重 `F` 的切，`e` 是**一條**最輕的跨切邊（可能有並列）。則 `F ∪ {e}` 也是某棵 MST 的子集。

**證明**：若 `e ∈ T`，`T` 本身就是答案。否則 `T + e` 恰好有一個圈 `C`（Ch 9：樹加一條邊產生唯一的圈），`C` 經過 `e`，`e` 跨切，所以 `C` 上還有另一條跨切邊 `f`。`F` 尊重這個切，所以 `f ∉ F`。令 `T' = T + e − f`：

- `T'` 是生成樹：從 `T + e` 拿掉圈上的一條邊不會破壞連通（`f` 的兩端還能沿 `C` 的其餘部分相連），邊數回到 `n − 1`，由 Ch 9 定義 (b) 是樹。
- `w(T') = w(T) + w(e) − w(f) ≤ w(T)`，因為 `e` 是最輕的跨切邊、`f` 也跨切，`w(e) ≤ w(f)`。

所以 `T'` 也是 MST，而且 `F ∪ {e} ⊆ T'`（`f ∉ F`）。∎

**嚴格版**：若 `e` 是**唯一**最輕的跨切邊，則 `e` 屬於**每一棵** MST。證明同上：若某棵 MST `T` 不含 `e`，換出來的 `T'` 嚴格更輕，矛盾。

### Cycle property

**定理（cycle property）**：設 `C` 是 `G` 的一個圈，`e` 是 `C` 上**唯一**最重的邊。則 `e` 不屬於任何 MST。

**證明**：反設 MST `T` 含 `e`。`T − e` 分成兩個分量，定義一個切；`e` 是 `T` 中唯一跨這個切的邊。圈 `C` 經過 `e`，所以 `C` 上還有另一條跨切邊 `f`，且 `w(f) < w(e)`（`e` 是 `C` 上唯一最重）。`T − e + f` 連通、`n − 1` 條邊，是生成樹，而且嚴格更輕，矛盾。∎

（平手的版本：若 `e` 是 `C` 上最重的邊之一，則存在一棵不含 `e` 的 MST。證明換成「`≤`」即可。）

### 權重相異 ⇒ MST 唯一

**推論**：若所有邊權重相異，MST 唯一。

**證明**：取任一 MST `T` 與其中一條邊 `e`。`T − e` 定義一個切，`e` 必須是這個切唯一最輕的跨切邊——否則用更輕的跨切邊 `f` 換掉 `e`，得到更輕的生成樹。由 cut property 嚴格版，`e` 屬於每一棵 MST。所以 `T` 的每條邊都在每一棵 MST 裡；所有 MST 都有 `n − 1` 條邊，因此全都等於 `T`。∎

實務上權重常有平手（例如所有權重都是整數）。標準處理是**用 `(w(e), e 的編號)` 的字典序當全序**：在這個全序下沒有平手，MST 唯一；而它在原權重下也是 MST（全序只是在原本平手的邊之間指定先後，不改變任何嚴格不等式）。下面的 Borůvka 實作就靠這招。

## 三個演算法，同一個證明

```text
Kruskal：把邊從輕到重掃一遍，不會造成圈就加入        ← 全域觀點：一條一條邊
Prim   ：從一個點長出一棵樹，每次加入最輕的跨切邊    ← 局部觀點：一棵樹往外長
Borůvka：每個分量同時挑自己最輕的出邊，全部一起加入  ← 平行觀點：所有分量一起長
```

三者的正確性都是「維持不變量：已選的邊集合 `F` 是某棵 MST 的子集」，每一步找一個尊重 `F` 的切，證明新加的邊是最輕的跨切邊，然後套 cut property。

**Kruskal**：考慮加入邊 `e = uv`（`u`、`v` 在 `F` 的不同分量）。取 `S` = `F` 中 `u` 所在的分量，切 `(S, V∖S)` 尊重 `F`。任何比 `e` 輕的邊 `g` 都已經處理過：若 `g` 被加入，它在 `F` 裡，不跨切；若 `g` 被跳過，那時它兩端已在同一分量，分量只會合併不會分裂，所以現在仍在同一分量，也不跨切。因此 `e` 是最輕的跨切邊。被跳過的邊兩端已連通，加入只會造成圈，不影響不變量。最後 `F` 有 `n − 1` 條邊（`G` 連通時），是生成樹又是某棵 MST 的子集，所以就是 MST。

**Prim**：取 `S` = 目前樹的點集，切 `(S, V∖S)` 尊重 `F`（`F` 的邊全在 `S` 內），Prim 加入的正是最輕的跨切邊。直接套 cut property。

**Borůvka**：在全序（無平手）下，每個分量 `K` 挑的邊是切 `(K, V∖K)` **唯一**最輕的跨切邊，由嚴格版 cut property，它屬於**唯一的那棵** MST。所以這一輪挑出來的所有邊都在同一棵樹裡，一起加入不會產生圈。每輪每個分量至少和另一個合併，分量數至少減半，最多 `⌈log₂ n⌉` 輪。

注意 Borůvka 的論證**必須**有全序：如果兩個分量挑了「不同但等重」的邊，各自都是某棵 MST 的邊，卻不一定是**同一棵**的，合起來就可能成圈。下面會實際看到它壞掉。

## 實作與三方對拍

圖用加權鄰接字典 `wadj: dict[node, dict[node, weight]]`。

```python
import heapq
import random
from itertools import combinations
import networkx as nx


class DSU:
    """Union-find：路徑壓縮（path halving）+ 按大小合併。"""
    def __init__(self, nodes):
        self.parent = {v: v for v in nodes}
        self.size = {v: 1 for v in nodes}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]   # 指向祖父：路徑減半
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False                                  # 已同一分量：這條邊會閉合成圈
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]
        return True


def edge_list(wadj):
    """加權鄰接字典 → [(w, u, v)]，每條邊一次。"""
    return [(w, u, v) for u in wadj for v, w in wadj[u].items() if u < v]


def kruskal(wadj):
    dsu = DSU(wadj)
    return [(u, v, w) for w, u, v in sorted(edge_list(wadj)) if dsu.union(u, v)]


def prim(wadj):
    """lazy Prim：heap 裡可能有過期的邊，彈出時再檢查。對每個分量各做一次 → 生成森林。"""
    seen, tree = set(), []
    for root in sorted(wadj):
        if root in seen:
            continue
        seen.add(root)
        heap = [(w, root, v) for v, w in wadj[root].items()]
        heapq.heapify(heap)
        while heap:
            w, u, v = heapq.heappop(heap)
            if v in seen:
                continue                                  # 兩端都已在樹內：過期
            seen.add(v)
            tree.append((u, v, w))
            for x, wx in wadj[v].items():
                if x not in seen:
                    heapq.heappush(heap, (wx, v, x))
    return tree


def boruvka(wadj):
    """每一輪：每個分量挑「最輕的出邊」，全部一起加入。用 (w, u, v) 當全序打破平手。"""
    dsu = DSU(wadj)
    edges = sorted(edge_list(wadj))
    tree = []
    while True:
        best = {}
        for e in edges:
            w, u, v = e
            ru, rv = dsu.find(u), dsu.find(v)
            if ru == rv:
                continue
            for r in (ru, rv):
                if r not in best or e < best[r]:
                    best[r] = e
        if not best:
            return tree
        for w, u, v in set(best.values()):
            if dsu.union(u, v):
                tree.append((u, v, w))


def weight(tree):
    return sum(w for _, _, w in tree)


def brute_mst_weight(wadj):
    nodes = sorted(wadj)
    E = edge_list(wadj)
    best = None
    for sub in combinations(E, len(nodes) - 1):
        G = nx.Graph()
        G.add_nodes_from(nodes)
        G.add_edges_from((u, v) for _, u, v in sub)
        if nx.is_tree(G):
            s = sum(w for w, _, _ in sub)
            best = s if best is None else min(best, s)
    return best


rng = random.Random(10)
for trial in range(300):
    n = rng.randint(2, 7)
    G = nx.gnp_random_graph(n, 0.6, seed=rng.randint(0, 10**9))
    for u, v in G.edges():
        G[u][v]["weight"] = rng.randint(1, 4)          # 權重範圍小 → 大量平手
    wadj = {u: {v: G[u][v]["weight"] for v in G[u]} for u in G}
    ws = {weight(kruskal(wadj)), weight(prim(wadj)), weight(boruvka(wadj))}
    for algo in ("kruskal", "prim", "boruvka"):
        ws.add(nx.minimum_spanning_tree(G, algorithm=algo).size(weight="weight"))
    assert len(ws) == 1, ws
    if nx.is_connected(G):
        assert ws == {brute_mst_weight(wadj)}
    for f in (kruskal, prim, boruvka):                  # 結構檢查：是生成森林
        F = nx.Graph()
        F.add_nodes_from(G)
        F.add_edges_from((u, v) for u, v, _ in f(wadj))
        assert nx.is_forest(F) and nx.number_connected_components(F) == nx.number_connected_components(G)
print("300 張隨機加權圖（含大量平手、含不連通）：自刻三法 = NetworkX 三法 = 暴力窮舉")
```

```text
300 張隨機加權圖（含大量平手、含不連通）：自刻三法 = NetworkX 三法 = 暴力窮舉
```

對拍的設計重點：

- **權重只取 1–4**：故意製造大量平手。平手時 MST 不唯一，三個演算法挑出的**樹**可能不同，所以我們比的是**總權重**，再另外檢查結構（是森林、分量數和原圖相同）。只比樹本身會在平手時誤報。
- **暴力窮舉**：列舉所有 `n − 1` 條邊的子集，取是樹的那些的最小總權重。`n ≤ 7`、邊數 ≤ 21，子集數最多 `C(21, 6) = 54264`，跑得動。這是唯一不依賴任何 MST 理論的標準答案。
- **lazy Prim**：heap 裡允許同一個點有多筆過期紀錄，彈出時檢查 `v in seen`。另一種是 decrease-key 版本，Python 的 `heapq` 不支援 decrease-key，lazy 版是 Python 的慣用寫法，代價是 heap 大小變成 `O(m)` 而非 `O(n)`，時間仍是 `O(m log m) = O(m log n)`。

## 故意弄壞一：Borůvka 不處理平手

```python
# Borůvka 的平手陷阱：每個點只用「權重」挑最輕出邊，平手時看鄰接順序
wadj = {
    "a": {"b": 1, "c": 1},
    "b": {"c": 1, "a": 1},
    "c": {"a": 1, "b": 1},
}

def lightest_by_weight_only(v):
    return min(wadj[v].items(), key=lambda item: item[1])   # 平手時取第一個遇到的

chosen = {frozenset((v, lightest_by_weight_only(v)[0])) for v in wadj}
print("只比權重:", sorted(tuple(sorted(e)) for e in chosen), "→", len(chosen), "條邊、3 個點：是一個圈")

def lightest_total_order(v):
    return min((w, *sorted((v, u))) for u, w in wadj[v].items())  # (權重, 端點) 字典序

chosen = {lightest_total_order(v)[1:] for v in wadj}
print("全序打破平手:", sorted(chosen), "→", len(chosen), "條邊：一棵樹")
```

```text
只比權重: [('a', 'b'), ('a', 'c'), ('b', 'c')] → 3 條邊、3 個點：是一個圈
全序打破平手: [('a', 'b'), ('a', 'c')] → 2 條邊：一棵樹
```

三個點各自挑了「一條最輕的邊」，每一條單獨看都合法（都在某棵 MST 裡），但 `a` 挑 `ab`、`b` 挑 `bc`、`c` 挑 `ca`，合起來是一個圈。我們的 `boruvka()` 用 `dsu.union` 檢查，就算沒有全序也不會真的加入成圈的邊——但在**平行或分散式**的 Borůvka（這正是它的主要用途：每個分量獨立決策）裡，沒有一個中央 DSU 幫你擋，平手必須靠全序解決。分散式 MST 的經典 GHS 演算法（Gallager–Humblet–Spira）同樣要求邊權相異或以 ID 打破平手。

## 故意弄壞二：union-find 少了優化

Kruskal 的時間瓶頸是排序 `O(m log m)` 加上 `m` 次 find/union。union-find 若不做任何優化，樹可以退化成一條鏈：

```python
# 計時實驗：輸出的秒數因機器而異
import time


def run(n, compress, by_size):
    parent = list(range(n))
    size = [1] * n

    def find(x):
        if compress:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        while parent[x] != x:
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if by_size and size[ra] < size[rb]:
            ra, rb = rb, ra
        parent[rb] = ra            # 沒有按大小合併時：永遠把 b 的根掛到 a 的根下
        size[ra] += size[rb]

    t = time.perf_counter()
    for i in range(1, n):          # 惡意順序：每次把舊的大樹掛到新點下面 → 一條長鏈
        union(i, 0)
    for _ in range(n):
        find(0)                    # 反覆查詢最深的點
    return time.perf_counter() - t


for n in (2_500, 5_000, 10_000):
    cells = [f"{run(n, c, s):6.3f}s" for c, s in [(False, False), (True, False), (False, True), (True, True)]]
    print(f"n={n:6d}  都不用 {cells[0]}  只壓縮 {cells[1]}  只按大小 {cells[2]}  兩者 {cells[3]}")
```

```text
n=  2500  都不用  0.274s  只壓縮  0.001s  只按大小  0.001s  兩者  0.001s
n=  5000  都不用  1.069s  只壓縮  0.002s  只按大小  0.002s  兩者  0.002s
n= 10000  都不用  5.165s  只壓縮  0.008s  只按大小  0.007s  兩者  0.007s
```

（AMD Ryzen 7 5700G、Windows 11、Python 3.13.12 實測；你的數字會不同，但比例會類似。）

`union(i, 0)` 每次把「目前包含 0 的整棵樹」掛到新點 `i` 下面，0 的深度每次加 1，最後是一條長 `n−1` 的鏈。沒有優化時，每次 find(0) 都走完整條鏈，總時間 `Θ(n²)`——`n` 加倍、時間約乘 4，表格裡 1.07 → 5.17 秒正是這樣。任一種優化都把它壓回接近線性。

理論上的結論（Tarjan 1975）：路徑壓縮加按秩（或按大小）合併，`m` 次操作總時間 `O(m α(n))`，`α` 是 inverse Ackermann 函數，對任何物理上可能的 `n` 都 ≤ 4。單用其中一種的界比較弱（只按大小合併是 `O(log n)` 每次；只用路徑壓縮是攤銷 `O(log n)`），但在這個惡意例子上兩者都已經夠快。本課不證明 `α(n)` 界，這裡只給結論。

## 對比與取捨

| 演算法 | 時間（二元 heap / 排序） | 適合 | 平行性 | 需要的資料結構 |
| --- | --- | --- | --- | --- |
| Kruskal | `O(m log m)` | 稀疏圖、邊已排序、需要生成森林 | 差（依序掃邊） | 排序 + union-find |
| Prim | `O(m log n)`；Fibonacci heap `O(m + n log n)`；鄰接矩陣版 `O(n²)` | 稠密圖（矩陣版） | 差（一棵樹長） | priority queue |
| Borůvka | `O(m log n)` | 平行、分散式、外部記憶體 | 好（每輪各分量獨立） | union-find 或分量標籤 |
| Karger–Klein–Tarjan | 期望 `O(m)` | 理論 | — | Borůvka 步驟 + 隨機取樣 + MST 驗證 |

為什麼不全用 Prim 的 Fibonacci heap 版？漸進最好，但常數大、實作複雜，在常見規模上通常輸給二元 heap；這是「漸進複雜度 ≠ 實際速度」的經典例子（這是一般經驗，本章沒有實測 Fibonacci heap）。

## 踩雷集錦

1. **以為「MST = 最短路徑樹」**：MST 最小化**總權重**，不保證任兩點間的路徑最短。三角形 `ab = 2, bc = 2, ac = 3`，MST 是 `{ab, bc}`，但 `a` 到 `c` 在樹上距離 4，直接走 `ac` 只要 3。要最短路徑請用 Dijkstra 的最短路徑樹。
2. **平手時比較「樹」而不是「權重」**：對拍時拿自刻 Kruskal 的邊集合和 NetworkX 的比，平手時會隨機不一致，然後你花一小時找一個不存在的 bug。比總權重，再另外驗結構。
3. **Borůvka 沒有全序**：如上所示，平行版本會產生圈。用 `(權重, 端點)` 或 `(權重, 邊 ID)` 的字典序。
4. **以為貪婪對任何「選一組邊」的問題都對**：把「最小生成樹」換成「最小權重的 Hamilton 路徑」或「最小 Steiner 樹（只需連通指定點）」，貪婪立刻失敗。MST 能貪婪是因為生成樹構成一個 matroid 的基——這是 Ch 25 的主題。
5. **負權重**：MST 對負權重完全沒問題（cut property 的證明沒用到正負），這點和 Dijkstra 不同。把所有權重加同一個常數，MST 也不變，因為每棵生成樹恰有 `n − 1` 條邊。要求**最大**生成樹，把權重全部取負再求 MST，或讓 Kruskal 從重到輕掃，兩者等價。

## 進階：再往深一層

- **線性時間 MST**：Karger–Klein–Tarjan（1995）用「隨機取樣一半的邊 → 遞迴求 MST 森林 → 用 cycle property 刪掉被它『F-heavy』的邊」得到期望線性時間。決定性的最佳結果是 Chazelle（2000）的 `O(m α(m, n))`；確切的決定性線性時間演算法是否存在仍是開放問題。
- **MST 驗證**：給一棵樹，判斷它是不是 MST，可以在 `O(m)` 內做到（King 1997 等），比重算快。它是 KKT 的關鍵子程序，也是「certifying algorithm」（Ch 7）的另一個例子——cycle property 本身就是證據：MST 外的每條邊都是它所在基本圈上最重的。
- **Matroid 觀點**：cut/cycle property 在 matroid 上有一般化版本，「貪婪演算法對所有權重都給最佳解」⇔「可行集合構成 matroid」（Rado–Edmonds 定理）。Ch 25 會證明它，並說明為什麼 MST 是、而最大匹配不是。
- **應用**：單連結分群（Kruskal 停在剩 `k` 個分量時）、影像分割（Felzenszwalb–Huttenlocher 演算法就是帶自適應門檻的 Kruskal）、近似 TSP（Ch 8 的進階一節提過 Christofides）。

## 動手練習

1. 把 Prim 改成鄰接矩陣、不用 heap 的 `O(n²)` 版本，在 `n = 2000` 的完全圖上和 lazy heap 版比時間。哪個快？為什麼？
2. 修改對拍程式，讓所有權重相異（例如 `rng.sample`），改成比較**邊集合**而不是總權重，確認三法與 NetworkX 完全一致——這驗證了「權重相異 ⇒ MST 唯一」。
3. 實作「反向刪除（reverse-delete）」演算法：從最重的邊開始，刪掉後仍連通就刪。用 cycle property 證明它正確，再加進對拍。
4. 寫 `is_mst(wadj, tree)`：對每條非樹邊 `uv`，檢查它不比樹上 `u`–`v` 路徑的任何一條邊輕。這是用 cycle property 當證據的驗證器。

## 本章重點整理

- Cut property：尊重 `F` 的切上，最輕的跨切邊可以安全加入。Cycle property：圈上唯一最重的邊不在任何 MST 裡。兩者都是「圈跨切偶數次」+ 交換論證。
- Kruskal、Prim、Borůvka 是同一個不變量（`F` 是某棵 MST 的子集）的三種切法：分量切、樹切、每個分量各切一刀。
- 平手用 `(權重, ID)` 全序解決；Borůvka 的平行版本非用不可。權重相異時 MST 唯一。
- union-find 需要路徑壓縮或按大小合併，否則在惡意順序下退化成 `Θ(n²)`；兩者並用是 `O(m α(n))`。

## 自我檢核

- [ ] 不看筆記，在白板上證明 cut property，並指出「`F` 尊重切」這個條件在證明哪一步用到。
- [ ] 能說明 Kruskal 為什麼可以安全跳過「兩端已連通」的邊，以及為什麼被跳過的邊之後永遠不會跨切。
- [ ] 能舉出一個 Borůvka 不處理平手就出錯的三點例子，並解釋為什麼單機版有 DSU 擋著卻不代表演算法正確。
- [ ] 如果有人說「MST 就是把每個點連到離它最近的點」，你能舉反例說明這樣甚至不一定連通。
- [ ] 能解釋為什麼 MST 可以處理負權重，但 Dijkstra 不行。

## 延伸閱讀

### 論文

- **[On the shortest spanning subtree of a graph and the traveling salesman problem](https://doi.org/10.1090/S0002-9939-1956-0078686-7)** — Joseph B. Kruskal，Proceedings of the AMS 7(1):48–50（1956）
  - **核心貢獻**：三頁論文，提出依權重從輕到重加邊的演算法；標題提到 TSP，但論文的主體是生成樹。
  - **讀哪裡**：整篇三頁；對照本章 Kruskal 的正確性論證，看 1956 年的寫法和今天的「安全邊」框架差多少。
  - **前提**：本章的 cut/cycle property。
- **[Shortest Connection Networks And Some Generalizations](https://doi.org/10.1002/j.1538-7305.1957.tb01515.x)** — R. C. Prim，Bell System Technical Journal 36(6):1389–1401（1957）
  - **核心貢獻**：從電話網路規劃出發的「一棵樹往外長」演算法。
  - **讀哪裡**：前半的演算法描述與正確性論證；後半的推廣可略讀。
  - **前提**：本章 Prim 一節。
- **[Otakar Borůvka on minimum spanning tree problem: Translation of both the 1926 papers, comments, history](https://doi.org/10.1016/S0012-365X(00)00224-7)** — J. Nešetřil、E. Milková、H. Nešetřilová，Discrete Mathematics 233(1–3):3–36（2001）
  - **核心貢獻**：把 Borůvka 1926 年的兩篇捷克文論文翻成英文並加註，交代了 MST 演算法誰先誰後的歷史。
  - **讀哪裡**：翻譯部分與歷史評論；看 Borůvka 怎麼在沒有「圖」術語的年代描述電網問題。
  - **前提**：本章 Borůvka 一節。
- **[On the History of the Minimum Spanning Tree Problem](https://doi.org/10.1109/MAHC.1985.10011)** — R. L. Graham、Pavol Hell，IEEE Annals of the History of Computing 7(1):43–57（1985）
  - **核心貢獻**：MST 歷史的標準文獻，整理 Borůvka、Jarník、Kruskal、Prim、Sollin 的獨立發現。
  - **讀哪裡**：前三節；本章「為什麼需要這個」的歷史敘述可以在這裡查證細節。
  - **前提**：無。
- **[Efficiency of a Good But Not Linear Set Union Algorithm](https://doi.org/10.1145/321879.321884)** — Robert E. Tarjan，JACM 22(2):215–225（1975）
  - **核心貢獻**：證明路徑壓縮 + 按秩合併的 union-find 是 `Θ(m α(m, n))`，而且這個界是緊的。
  - **讀哪裡**：引言與主定理敘述；完整證明很技術，第一次讀可以跳過。
  - **前提**：本章「故意弄壞二」。
- **[A randomized linear-time algorithm to find minimum spanning trees](https://doi.org/10.1145/201019.201022)** — D. R. Karger、P. N. Klein、R. E. Tarjan，JACM 42(2):321–328（1995）
  - **核心貢獻**：Borůvka 步驟 + 隨機取樣 + cycle property 過濾，期望 `O(m)`。
  - **讀哪裡**：第 2 節（演算法）與「F-heavy 邊」的定義，它就是 cycle property 的演算法化。
  - **前提**：本章 cycle property；基本機率（期望值）。

### 書籍

- **《Introduction to Algorithms》** — Cormen、Leiserson、Rivest、Stein（MIT Press，第 4 版，2022）
  - **讀哪幾章**：Ch 21「Minimum Spanning Trees」用的就是本章「`F` 是某棵 MST 子集 + 安全邊」的框架；Ch 19「Data Structures for Disjoint Sets」有 `α(n)` 界的完整證明。
  - **前提**：本章。
- **《Introduction to Graph Theory》** — Douglas B. West（第 2 版，2001）
  - **讀哪幾章**：§2.3「Optimization and Trees」，含 Kruskal 與 Prim 的證明及習題。

最小生成樹裡的每條邊，拿掉之後樹就斷成兩半——它們都是「橋」。下一章研究一般圖裡的橋與割點：哪些邊或點一旦失效，網路就會分裂，以及如何用一次 DFS 全部找出來。

→ [Ch 11 連通度：割點、橋與 Tarjan](./11-connectivity-tarjan.md)
