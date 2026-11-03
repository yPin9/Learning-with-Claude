# Ch 21 — Dinic 與 push-relabel

> **目標**：實作 Dinic（level graph + blocking flow + current-arc）與 FIFO push-relabel（含 gap heuristic），證明 Dinic 的 phase 數 ≤ `V − 1`、push-relabel 的高度 ≤ `2V − 1`，並在這台機器上實測它們和 Edmonds–Karp、NetworkX 各演算法的差距。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11；效能數字在 AMD Ryzen 7 5700G（8 核 16 緒）、44 GB RAM 上量測。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

[Ch 20](./20-max-flow-min-cut.md) 的 Edmonds–Karp 每增廣一條路徑就重跑一次 BFS。BFS 的結果其實包含很多資訊：所有最短路徑都在裡面，EK 卻只用了一條就丟掉。

1970 年，莫斯科的 Yefim Dinitz（英譯論文把名字拼成 Dinic，所以西方習慣叫 Dinic's algorithm）想到：同一次 BFS 算出的「分層」，可以一次把**所有**等長的最短增廣路灌到飽和，才進入下一層。後來以色列的 Shimon Even 和 Alon Itai 把它整理成今天教科書的版本並帶到西方；Dinitz 2006 年的回憶文章講了這段兩個版本並存的歷史（見延伸閱讀）。

另一條路線完全放棄「路徑」。1986 年 Goldberg 與 Tarjan 提出 push-relabel（1988 年發表於 JACM）：先從源點把水灌爆，讓中間節點暫時「進多出少」，每個節點只看自己的鄰居，把多出來的水往低處推；推不動就把自己抬高。它是局部操作，適合平行化，也是 NetworkX 的預設最大流演算法（`flow_func` 預設 `preflow_push`）。

工程上的意義：Ch 20 的 EK 在本章的 60×60 格子圖上跑了 10 秒，Dinic 和 push-relabel 都在 0.12 秒左右（後面有完整數據）。影像分割、大規模匹配、練習 D 的建模，實際跑得動靠的是這一章。

## 先建立直覺

**Dinic：先蓋樓層，再一次灌滿每層之間的所有樓梯。**

```text
 level 0       level 1       level 2       level 3
    s ----------> a ----------> c ----------> t
    |             |                           ^
    |             v  a->b：同在 level 1，      |
    |             |  不在 level graph 裡       |
    +-----------> b ----------> d ------------+
                  ^             |
                  +-------------+  d->b：往回一層，也不在 level graph 裡
```

BFS 給每個點一個樓層編號 `level(v)` = `s` 到 `v` 的最短距離。**Level graph** 只保留「往上走一層」的殘餘邊（`level(v) = level(u) + 1`）。在 level graph 上反覆找 `s→t` 路徑並增廣，直到找不到為止——這時得到的叫 **blocking flow**：level graph 上每條 `s→t` 路徑都至少有一條邊被灌滿。然後重新 BFS 蓋樓，進入下一個 phase。

**Push-relabel：每個點是一個有高度的水池，水只能往正好低一階的鄰居流。**

```text
 高度
  6 │  s (h = V)          一開始把 s 的出邊全部灌滿
  … │
  2 │        a ●~~~       a 有多餘的水（excess）
  1 │      ↙   ╲          往高度正好低 1 的鄰居推
  0 │  b            t     推不出去 → relabel：把自己抬到「最低鄰居 + 1」
```

源點的高度固定為 `V`、匯點固定為 0。中間的點積了水（excess > 0）就叫 **active**，要嘛推給剛好低一階的鄰居，要嘛把自己抬高。水推不到 `t` 的，最後會因為高度超過 `V` 而倒流回 `s`。演算法停下時，每個中間點都沒有積水，剩下的就是一個合法的流——而且是最大流。

這個類比的限制：真實的水會同時流向所有低處，push-relabel 是一次推一條邊；而且「高度」只是一個距離的下界估計，不是物理高度。

## Dinic：實作

下面的檔案存成 `dinic.py`，本章後面兩個 block 會 `import` 它。圖的表示換成了**邊陣列**：第 `i` 條邊的反向邊永遠是 `i ^ 1`（成對加入，編號 `2k`、`2k+1`），比 Ch 20 的字典快，也天然支援重邊與反平行邊——Ch 20 踩雷第 1 條的問題在這裡不存在，因為每條原始邊有自己的編號。

```python
"""dinic.py：Dinic 最大流（level graph + blocking flow + current-arc）。"""
from collections import deque


class FlowNetwork:
    """邊存成平行陣列；第 i 條邊的反向邊是 i ^ 1（成對加入，所以編號 2k、2k+1 互為反向）。"""

    def __init__(self, n):
        self.n = n
        self.adj = [[] for _ in range(n)]   # adj[u] = 從 u 出發的邊編號
        self.to, self.cap = [], []

    def add_edge(self, u, v, c):
        self.adj[u].append(len(self.to)); self.to.append(v); self.cap.append(c)
        self.adj[v].append(len(self.to)); self.to.append(u); self.cap.append(0)

    def bfs_levels(self, s, t):
        level = [-1] * self.n
        level[s] = 0
        q = deque([s])
        while q:
            u = q.popleft()
            for e in self.adj[u]:
                v = self.to[e]
                if self.cap[e] > 0 and level[v] < 0:
                    level[v] = level[u] + 1
                    q.append(v)
        return level

    def max_flow(self, s, t, current_arc=True, stats=None):
        flow, phases, visits = 0, 0, 0
        while (level := self.bfs_levels(s, t))[t] >= 0:
            phases += 1
            it = [0] * self.n                # current-arc 指標：adj[u][it[u]:] 還沒被判死
            while True:                      # 在 level graph 上反覆找路，直到 blocking
                # 迭代式 DFS（避免 Python 遞迴深度限制）
                path, u = [], s
                while u != t:
                    adj_u = self.adj[u]
                    i = it[u] if current_arc else 0
                    while i < len(adj_u):
                        e = adj_u[i]
                        visits += 1
                        v = self.to[e]
                        if self.cap[e] > 0 and level[v] == level[u] + 1:
                            break
                        i += 1
                    if current_arc:
                        it[u] = i
                    if i == len(adj_u):      # 死路：u 在這個 phase 再也用不到
                        level[u] = -1        # 從 level graph 中拿掉 u
                        if not path:
                            break
                        e = path.pop()
                        u = self.to[e ^ 1]   # 退回上一個點
                        if current_arc:
                            it[u] += 1       # 那條邊通往死路，指標前進
                        continue
                    path.append(adj_u[i])
                    u = self.to[adj_u[i]]
                if u != t:
                    break                    # s 本身成為死路 → blocking flow 完成
                b = min(self.cap[e] for e in path)
                for e in path:
                    self.cap[e] -= b
                    self.cap[e ^ 1] += b
                flow += b
        if stats is not None:
            stats.update(phases=phases, visits=visits)
        return flow


if __name__ == "__main__":
    import random
    import networkx as nx

    # Ch 20 的第一個例子
    names = ["s", "a", "b", "c", "d", "t"]
    idx = {x: i for i, x in enumerate(names)}
    edges = [("s", "a", 10), ("s", "b", 10), ("a", "b", 2), ("a", "c", 4), ("a", "d", 8),
             ("b", "d", 9), ("c", "t", 10), ("d", "c", 6), ("d", "t", 10)]
    net = FlowNetwork(len(names))
    for u, v, c in edges:
        net.add_edge(idx[u], idx[v], c)
    st = {}
    print("Ch 20 example: max flow =", net.max_flow(idx["s"], idx["t"], stats=st), st)

    rng = random.Random(21)
    for trial in range(500):
        n = rng.randint(2, 30)
        G = nx.DiGraph()
        G.add_nodes_from(range(n))
        net = FlowNetwork(n)
        for _ in range(rng.randint(0, 4 * n)):
            u, v, c = rng.randrange(n), rng.randrange(n), rng.randint(1, 50)
            if u != v:
                net.add_edge(u, v, c)                   # 允許重邊與反平行邊
                old = G[u][v]["capacity"] if G.has_edge(u, v) else 0
                G.add_edge(u, v, capacity=old + c)      # NetworkX 用合併後的容量
        st = {}
        assert net.max_flow(0, n - 1, stats=st) == nx.maximum_flow_value(G, 0, n - 1)
        assert st["phases"] <= max(n - 1, 0)            # 定理：phase 數 ≤ V - 1
    print("500 random networks: Dinic == networkx, phases <= V-1")
```

```text
Ch 20 example: max flow = 19 {'phases': 2, 'visits': 38}
500 random networks: Dinic == networkx, phases <= V-1
```

程式裡三個關鍵決策：

- **current-arc 指標 `it[u]`**：一條邊一旦被判定「這個 phase 用不到」（沒殘餘容量、或不往上一層、或通往死路），以後就不再看。這是 Dinic 複雜度證明的核心，下面有實驗看拿掉它會怎樣。
- **死路剪枝 `level[u] = -1`**：DFS 從 `u` 找不到往上一層的出路時，把 `u` 踢出 level graph，其他點就不會再走進來。
- **迭代式 DFS**：Python 預設遞迴深度約 1000，長路徑的網路（例如本章的 30 層分層圖）用遞迴寫法會撞到 `RecursionError`；用顯式的 `path` 堆疊避開。

## Dinic 為什麼快：兩個證明

### 引理 1（每個 phase 之後，s 到 t 的距離嚴格增加）

令某個 phase 開始時的殘餘圖為 `G_f`，BFS 距離為 `level(·)`，`d = level(t)`。phase 結束（得到 blocking flow）後殘餘圖為 `G_{f'}`。則 `G_{f'}` 中 `s` 到 `t` 的最短距離 ≥ `d + 1`。

**證明**：先看 `G_{f'}` 的每一條邊 `u→v` 滿足 `level(v) ≤ level(u) + 1`：

- 若 `u→v` 本來就在 `G_f` 裡：BFS 距離的性質保證 `level(v) ≤ level(u) + 1`。
- 若 `u→v` 是這個 phase 新長出來的：它是某次增廣經過 `v→u` 產生的反向邊。增廣只走 level graph，所以 `level(u) = level(v) + 1`，即 `level(v) = level(u) − 1`。

沿著 `G_{f'}` 中任一條 `s→t` 路徑，每走一步 level 最多加 1，從 `level(s) = 0` 到 `level(t) = d`，路徑長度至少 `d`。長度**恰好**是 `d` 的話，每一步都得剛好加 1，所以每條邊都不是新長出來的（新邊讓 level 減 1），也就是說整條路徑都在原本的 level graph 裡，而且每條邊在 `G_{f'}` 仍有殘餘容量。這和 blocking flow 的定義（level graph 上每條 `s→t` 路徑都有一條邊被灌滿）矛盾。所以長度 ≥ `d + 1`。∎

距離從 ≥ 1 開始、每個 phase 至少加 1、而且不超過 `V − 1`（簡單路徑），所以 **phase 數 ≤ `V − 1`**。上面的程式在 500 張隨機圖上用 `assert` 檢查了這一點。

這個引理是 Ch 20「EK 距離單調」引理的加強版：EK 每次增廣距離「不減」，Dinic 每個 phase 距離「嚴格增」。

### 引理 2（一個 phase 的 blocking flow 是 O(V E)）

在 level graph 上，DFS 的每一步要嘛前進一條邊、要嘛退回（死路）、要嘛完成一條路徑：

- **退回**：current-arc 指標前進一格（那條邊通往死路）。每條邊最多讓指標前進一次。
- **完成一條路徑**：推瓶頸量，至少一條邊被灌滿，之後它的殘餘容量為 0，下次掃到時指標會跳過它。所以增廣次數 ≤ `E`；每次增廣的路徑長度 ≤ `V`，沿路推流與「從 `s` 重新出發走回原處」各花 `O(V)`。
- **前進**：每次前進要嘛最後屬於某條完成的路徑（算在上一條的 `O(V)` 裡），要嘛最後被退回（算在退回裡）。

合計 `O(E) + O(E · V) = O(V E)`。乘上 `V − 1` 個 phase，**Dinic 是 `O(V² E)`**。∎

和 EK 的 `O(V E²)` 比：稠密圖 `E ≈ V²` 時，EK 是 `O(V⁵)`、Dinic 是 `O(V⁴)`。

### 單位容量時更好（這裡只給結論與證明架構）

當所有容量都是 1（匹配、邊不相交路徑都是這種）：

- 一般單位容量網路：phase 數是 `O(min(√E, V^(2/3)))`，總時間 `O(E · min(√E, V^(2/3)))`（一般歸功於 Karzanov 1973 與 Even–Tarjan 1975）。
- 單位容量二分圖（每個點只有一條入邊或一條出邊有容量 1）：phase 數 `O(√V)`，總時間 `O(E √V)`——這正是 [Ch 22](./22-bipartite-matching.md) 的 Hopcroft–Karp。

證明架構：前 `√V` 個 phase 每個 `O(E)`（單位容量下 blocking flow 是線性的，因為每條邊被灌滿就永久用掉）；`√V` 個 phase 之後最短增廣路長度 ≥ `√V`，而剩下還能增廣的流量對應到點不相交的增廣路，每條都要用掉 `√V` 個點，所以最多再 `√V` 條。完整證明在 Ch 22 用匹配的語言寫。

## 失敗示範：拿掉 current-arc

把 `current_arc=False` 傳進去，每次 DFS 都從鄰接串列的頭開始掃。答案不會錯，但同一條已經灌滿的邊會被看很多次。以下實驗也順便量了單位容量二分圖上的 phase 數：

```python
"""ex_arc.py：current-arc 的效果，以及單位容量二分圖上 Dinic 的 phase 數。需要 dinic.py。"""
import math
import random

from dinic import FlowNetwork


def layered(rng, layers=30, width=30, p=0.5):
    """分層網路：每層 width 個點，相鄰層之間隨機連邊。很多死路、很多平行的最短路。"""
    n = layers * width + 2
    s, t, edges = n - 2, n - 1, []
    for j in range(width):
        edges.append((s, j, 10**6))
        edges.append(((layers - 1) * width + j, t, 10**6))
    for L in range(layers - 1):
        for a in range(width):
            for b in range(width):
                if rng.random() < p:
                    edges.append((L * width + a, (L + 1) * width + b, rng.randint(1, 5)))
    return n, s, t, edges


def run(n, s, t, edges, current_arc):
    net = FlowNetwork(n)
    for u, v, c in edges:
        net.add_edge(u, v, c)
    st = {}
    f = net.max_flow(s, t, current_arc=current_arc, stats=st)
    return f, st


rng = random.Random(7)
case = layered(rng)
for ca in (True, False):
    f, st = run(*case, current_arc=ca)
    print(f"current_arc={ca!s:<5}: flow = {f}, phases = {st['phases']}, edge visits = {st['visits']:,}")

print("\nunit bipartite (half = k per side, 5k random edges):")
for k in (100, 400, 1600, 6400):
    n, s, t = 2 * k + 2, 2 * k, 2 * k + 1
    edges = [(s, i, 1) for i in range(k)] + [(k + j, t, 1) for j in range(k)]
    edges += [(i, k + j, 1) for i, j in {(rng.randrange(k), rng.randrange(k)) for _ in range(5 * k)}]
    f, st = run(n, s, t, edges, True)
    print(f"  V = {n:>6}: matching = {f:>5}, phases = {st['phases']:>2}, sqrt(V) = {math.sqrt(n):6.1f}")
```

```text
current_arc=True : flow = 1127, phases = 11, edge visits = 263,749
current_arc=False: flow = 1127, phases = 11, edge visits = 1,093,758

unit bipartite (half = k per side, 5k random edges):
  V =    202: matching =    98, phases =  5, sqrt(V) =   14.2
  V =    802: matching =   396, phases =  6, sqrt(V) =   28.3
  V =   3202: matching =  1587, phases =  8, sqrt(V) =   56.6
  V =  12802: matching =  6341, phases = 10, sqrt(V) =  113.1
```

分層網路是故意挑的形狀：30 層、每層 30 個點、相鄰層之間一半的點對有邊，最短路非常多、死路也多。拿掉 current-arc 後邊的檢查次數變成約 4.1 倍。我們的實作還保留了死路剪枝，所以差距沒有理論上那麼誇張；兩個都拿掉的話，最壞情況會退化成每條增廣路都可能重掃整張 level graph。

二分圖那一半：`V` 從 202 長到 12802（63 倍），phase 數只從 5 長到 10，遠低於 `√V`。`O(√V)` 是最壞情況的上界，隨機圖的增廣路通常很短。

## Push-relabel：實作

**Preflow**：和流一樣滿足容量限制，但中間點允許「流入 ≥ 流出」，差額叫 `excess(v)`。**高度函數** `h` 要一直滿足：

```text
h(s) = V，h(t) = 0，且對每條殘餘邊 u→v：h(u) ≤ h(v) + 1      (valid labeling)
```

兩個基本操作：

- **push(u, v)**：`u` active（`excess(u) > 0`）、殘餘邊 `u→v` 存在、`h(u) = h(v) + 1`（admissible）時，推 `min(excess(u), res(u,v))`。推完如果 `u→v` 被灌滿叫**飽和推**（saturating），否則叫**非飽和推**（`u` 的 excess 歸零）。
- **relabel(u)**：`u` active 但沒有 admissible 邊時，令 `h(u) = 1 + min{h(v) : u→v 是殘餘邊}`。

下面存成 `push_relabel.py`（需要同目錄的 `dinic.py`，共用 `FlowNetwork`）。用 FIFO 佇列決定下一個處理哪個 active 點，並加上 **gap heuristic**：

```python
"""push_relabel.py：FIFO push-relabel（含 gap heuristic）。需要同目錄的 dinic.py。"""
from collections import deque

from dinic import FlowNetwork


def push_relabel(net, s, t, gap=True, stats=None):
    n, to, cap, adj = net.n, net.to, net.cap, net.adj
    h = [0] * n                       # 高度（label）
    excess = [0] * n
    count = [0] * (2 * n + 1)         # count[k] = 高度為 k 的點數（gap heuristic 用）
    h[s] = n
    count[0], count[n] = n - 1, 1
    active = deque()
    pushes = relabels = gaps = 0

    for e in adj[s]:                  # 初始化：把 s 的出邊全部灌滿
        if cap[e] > 0:
            v, d = to[e], cap[e]
            cap[e] -= d; cap[e ^ 1] += d
            excess[v] += d; excess[s] -= d
            if v != t and excess[v] == d:
                active.append(v)

    it = [0] * n                      # current-arc，和 Dinic 同一個想法
    while active:
        u = active.popleft()
        while excess[u] > 0:          # discharge(u)
            if it[u] == len(adj[u]):  # 沒有可推的邊 → relabel
                old = h[u]
                h[u] = 2 * n
                for e in adj[u]:
                    if cap[e] > 0:
                        h[u] = min(h[u], h[to[e]] + 1)
                relabels += 1
                it[u] = 0
                count[old] -= 1
                count[h[u]] += 1
                if gap and count[old] == 0 and 0 < old < n:
                    # gap：高度 old 沒人了，所有 old < h < n 的點都不可能再到 t，直接抬到 n+1
                    gaps += 1
                    for v in range(n):
                        if old < h[v] < n:
                            count[h[v]] -= 1
                            h[v] = n + 1
                            count[h[v]] += 1
                            it[v] = 0
                continue
            e = adj[u][it[u]]
            v = to[e]
            if cap[e] > 0 and h[u] == h[v] + 1:     # admissible edge
                d = min(excess[u], cap[e])
                cap[e] -= d; cap[e ^ 1] += d
                excess[u] -= d; excess[v] += d
                pushes += 1
                if v not in (s, t) and excess[v] == d:
                    active.append(v)
            else:
                it[u] += 1
    if stats is not None:
        stats.update(pushes=pushes, relabels=relabels, gaps=gaps, max_height=max(h))
    return excess[t]


if __name__ == "__main__":
    import random
    import networkx as nx

    rng = random.Random(21)
    worst_h = 0
    for trial in range(500):
        n = rng.randint(2, 30)
        G = nx.DiGraph()
        G.add_nodes_from(range(n))
        net = FlowNetwork(n)
        for _ in range(rng.randint(0, 4 * n)):
            u, v, c = rng.randrange(n), rng.randrange(n), rng.randint(1, 50)
            if u != v:
                net.add_edge(u, v, c)
                old = G[u][v]["capacity"] if G.has_edge(u, v) else 0
                G.add_edge(u, v, capacity=old + c)
        st = {}
        assert push_relabel(net, 0, n - 1, gap=trial % 2 == 0, stats=st) == nx.maximum_flow_value(G, 0, n - 1)
        assert st["max_height"] <= 2 * n - 1               # 引理：高度 ≤ 2V - 1
        worst_h = max(worst_h, st["max_height"] / (2 * n - 1))
    print("500 random networks: push-relabel == networkx; max height/(2V-1) =", round(worst_h, 3))
```

```text
500 random networks: push-relabel == networkx; max height/(2V-1) = 0.857
```

測試一半開 gap、一半不開（`gap=trial % 2 == 0`），兩種都和 NetworkX 一致，而且每次都檢查高度 ≤ `2V − 1`。

**Gap heuristic 為什麼對**：如果某個高度 `k`（`0 < k < V`）上一個點都沒有，那所有高度介於 `k` 和 `V` 之間的點都不可能再把水送到 `t`：任何通往 `t` 的殘餘路徑，每走一步高度最多降 1，從 `> k` 降到 0 必須經過高度恰好 `k` 的點。所以可以直接把它們抬到 `V + 1`，讓它們儘快把水退回 `s`，省下一格一格 relabel 的時間。抬完之後 valid labeling 仍然成立：被抬的點 `v` 的任何殘餘出邊 `v→w`，`w` 的高度不可能 `< k`（否則原本 `h(v) ≤ h(w) + 1 ≤ k`，與 `h(v) > k` 矛盾），所以 `w` 要嘛也被抬了、要嘛高度 ≥ `V`。

## Push-relabel 的正確性與複雜度

### 引理 3（valid labeling 下沒有 s→t 殘餘路徑）

殘餘圖中任一條簡單 `s→t` 路徑長度 ≤ `V − 1`，每走一條邊高度最多降 1，所以 `h(s) ≤ h(t) + V − 1 = V − 1`，和 `h(s) = V` 矛盾。∎

push 和 relabel 都維持 valid labeling（push 只會新增一條 `v→u` 反向邊，此時 `h(v) = h(u) − 1 ≤ h(u) + 1`；relabel 按定義取到最大的合法值）。所以演算法停下時（沒有 active 點），preflow 就是一個合法的流，且殘餘圖沒有增廣路——由 [Ch 20 的 max-flow min-cut 定理](./20-max-flow-min-cut.md) 的 (2) ⇒ (1)，它是最大流。

### 引理 4（active 點一定有殘餘路徑回到 s）

**證明**：設 `u` active，令 `X` 為殘餘圖中從 `u` 走得到的點，假設 `s ∉ X`。看 `X` 的總 excess：

```text
Σ_{v∈X} excess(v) = f(進入 X 的邊) − f(離開 X 的邊)
```

若某條原始邊 `w→x`（`w ∉ X`、`x ∈ X`）上有正流量，殘餘圖就有反向邊 `x→w`，`w` 會在 `X` 裡，矛盾。所以進入 `X` 的流為 0，總 excess ≤ 0。但除了 `s` 之外每個點的 excess ≥ 0，`s ∉ X`，而 `u ∈ X` 的 excess > 0，總和 > 0。矛盾。∎

推論：active 點 `u` 到 `s` 有長度 ≤ `V − 1` 的殘餘路徑，沿路每步高度最多降 1，所以 `h(u) ≤ h(s) + V − 1 = 2V − 1`。高度只會在 `u` active 時（relabel）增加，所以**所有點的高度永遠 ≤ `2V − 1`**。實驗印出的 `max height/(2V-1) = 0.857` 和這個上界一致。

### 定理（generic push-relabel 是 O(V² E)）

- **Relabel 次數**：每次 relabel 讓高度至少加 1，每個點最多 `2V − 1` 次，總共 `O(V²)`。
- **飽和推次數**：對同一對 `(u, v)`，兩次 `u→v` 飽和推之間，必須先有一次 `v→u` 的推（否則 `u→v` 沒有殘餘容量）。第一次推時 `h(u) = h(v) + 1`；中間 `v→u` 推時 `h(v) = h(u) + 1`，所以 `h(v)` 至少漲了 2；再一次 `u→v` 推時 `h(u)` 又至少漲 2。高度上限 `2V − 1`，每對邊最多 `O(V)` 次，總共 `O(V E)`。
- **非飽和推次數**：令 `Φ = Σ_{v active} h(v)`。非飽和推讓 `u` 不再 active（`Φ` 減 `h(u)`），可能讓 `v` 變 active（`Φ` 加 `h(v) = h(u) − 1`），淨減至少 1。`Φ` 的增加只來自 relabel（總共 ≤ `V(2V − 1)`）和飽和推（每次讓一個點變 active，加 ≤ `2V − 1`，共 `O(V E)` 次）。`Φ ≥ 0`，所以非飽和推 ≤ `O(V²) + O(V E) · O(V) = O(V² E)`。∎

**FIFO 版本是 `O(V³)`**：這裡只給證明架構——把執行切成「pass」（佇列裡目前所有點各處理一次），用潛勢 `max{h(v) : v active}` 論證 pass 數是 `O(V²)`，每個 pass 每個點最多一次非飽和推，得到非飽和推 `O(V³)`。Goldberg–Tarjan 的論文有完整證明；搭配 dynamic tree 可以做到 `O(V E log(V²/E))`。

## 實測：這台機器上誰快

`bench.py` 需要同目錄的 `dinic.py` 與 `push_relabel.py`。三種網路：隨機稀疏圖、模擬影像分割的格子圖（每個像素連 `s`、連 `t`、四鄰居雙向）、單位容量二分圖。每個演算法跑 3 次取最快；我們的版本計時**包含建圖**，NetworkX 的版本也包含它內部建 residual network 的時間。

```python
"""bench.py：需要同目錄的 dinic.py、push_relabel.py。"""
import random
import time
from collections import deque

import networkx as nx
from networkx.algorithms import flow as nxflow

from dinic import FlowNetwork
from push_relabel import push_relabel


def edmonds_karp(net, s, t):
    to, cap, adj, total = net.to, net.cap, net.adj, 0
    while True:
        pe = [-1] * net.n
        pe[s] = -2
        q = deque([s])
        while q and pe[t] == -1:
            u = q.popleft()
            for e in adj[u]:
                if cap[e] > 0 and pe[to[e]] == -1:
                    pe[to[e]] = e
                    q.append(to[e])
        if pe[t] == -1:
            return total
        path, v = [], t
        while v != s:
            path.append(pe[v])
            v = to[pe[v] ^ 1]
        b = min(cap[e] for e in path)
        for e in path:
            cap[e] -= b
            cap[e ^ 1] += b
        total += b


def random_sparse(rng, n=2000, m=10000):
    return n, 0, n - 1, [(rng.randrange(n), rng.randrange(n), rng.randint(1, 100)) for _ in range(m)]


def grid_segmentation(rng, k=60):
    """k×k 像素；每個像素連 s、連 t，四鄰居之間雙向邊（影像分割的典型形狀）。"""
    n, s, t, edges = k * k + 2, k * k, k * k + 1, []
    for p in range(k * k):
        edges.append((s, p, rng.randint(1, 100)))
        edges.append((p, t, rng.randint(1, 100)))
        r, c = divmod(p, k)
        for q in ((p + 1) if c + 1 < k else None, (p + k) if r + 1 < k else None):
            if q is not None:
                w = rng.randint(1, 30)
                edges += [(p, q, w), (q, p, w)]
    return n, s, t, edges


def unit_bipartite(rng, half=1000, m=5000):
    n, s, t = 2 * half + 2, 2 * half, 2 * half + 1
    edges = [(s, i, 1) for i in range(half)] + [(half + j, t, 1) for j in range(half)]
    pairs = {(rng.randrange(half), rng.randrange(half)) for _ in range(m)}
    edges += [(i, half + j, 1) for i, j in sorted(pairs)]
    return n, s, t, edges


def best_of(fn, reps=3):
    best, val = float("inf"), None
    for _ in range(reps):
        t0 = time.perf_counter()
        val = fn()
        best = min(best, time.perf_counter() - t0)
    return best, val


def build(n, edges):
    net = FlowNetwork(n)
    for u, v, c in edges:
        if u != v:
            net.add_edge(u, v, c)
    return net


rng = random.Random(2026)
for name, (n, s, t, edges) in [("random sparse", random_sparse(rng)),
                               ("grid 60x60", grid_segmentation(rng)),
                               ("unit bipartite", unit_bipartite(rng))]:
    G = nx.DiGraph()
    G.add_nodes_from(range(n))
    for u, v, c in edges:
        if u != v:
            G.add_edge(u, v, capacity=G[u][v]["capacity"] + c if G.has_edge(u, v) else c)
    print(f"== {name}: V = {n}, E = {G.number_of_edges()}")
    rows = [
        ("ours: Edmonds-Karp", lambda: edmonds_karp(build(n, edges), s, t)),
        ("ours: Dinic", lambda: build(n, edges).max_flow(s, t)),
        ("ours: push-relabel FIFO+gap", lambda: push_relabel(build(n, edges), s, t)),
        ("nx: edmonds_karp", lambda: nx.maximum_flow_value(G, s, t, flow_func=nxflow.edmonds_karp)),
        ("nx: dinitz", lambda: nx.maximum_flow_value(G, s, t, flow_func=nxflow.dinitz)),
        ("nx: preflow_push", lambda: nx.maximum_flow_value(G, s, t, flow_func=nxflow.preflow_push)),
        ("nx: boykov_kolmogorov", lambda: nx.maximum_flow_value(G, s, t, flow_func=nxflow.boykov_kolmogorov)),
    ]
    ref = None
    for label, fn in rows:
        sec, val = best_of(fn)
        ref = val if ref is None else ref
        assert val == ref, (label, val, ref)
        print(f"  {label:<30} {sec * 1000:9.1f} ms   flow = {val}")
```

```text
== random sparse: V = 2000, E = 9989
  ours: Edmonds-Karp                  10.7 ms   flow = 221
  ours: Dinic                         18.3 ms   flow = 221
  ours: push-relabel FIFO+gap         20.6 ms   flow = 221
  nx: edmonds_karp                    30.8 ms   flow = 221
  nx: dinitz                          55.4 ms   flow = 221
  nx: preflow_push                    42.4 ms   flow = 221
  nx: boykov_kolmogorov               40.3 ms   flow = 221
== grid 60x60: V = 3602, E = 21360
  ours: Edmonds-Karp               10119.0 ms   flow = 157122
  ours: Dinic                        114.3 ms   flow = 157122
  ours: push-relabel FIFO+gap        112.1 ms   flow = 157122
  nx: edmonds_karp                 19154.8 ms   flow = 157122
  nx: dinitz                       59318.0 ms   flow = 157122
  nx: preflow_push                   216.8 ms   flow = 157122
  nx: boykov_kolmogorov             4282.0 ms   flow = 157122
== unit bipartite: V = 2002, E = 6990
  ours: Edmonds-Karp                 425.9 ms   flow = 988
  ours: Dinic                         22.0 ms   flow = 988
  ours: push-relabel FIFO+gap         22.3 ms   flow = 988
  nx: edmonds_karp                   494.0 ms   flow = 988
  nx: dinitz                        2728.0 ms   flow = 988
  nx: preflow_push                   151.6 ms   flow = 988
  nx: boykov_kolmogorov              135.1 ms   flow = 988
```

條件：AMD Ryzen 7 5700G、Windows 11、Python 3.13.12、NetworkX 3.7，單執行緒，seed 2026，單次執行時間取 3 次最小值。換一台機器絕對數字會變；我們只看同一台機器上的相對關係。怎麼讀：

1. **隨機稀疏圖上大家都快**：`s = 0` 在隨機圖裡只有五條左右的出邊，最大流只有 221，幾輪增廣就結束。這種實例分不出演算法的好壞，最佳化前先確認你的實例是不是「難」的。
2. **格子圖是分水嶺**：最大流 157122 由幾千條短增廣路累積而成。EK 每條都重跑一次 BFS，花了 10 秒；Dinic 和 push-relabel 都在 0.12 秒左右，快了將近 90 倍。這就是影像分割圖的形狀，也是 Boykov–Kolmogorov 那篇論文的研究對象。
3. **單位容量二分圖**：Dinic 比 EK 快約 19 倍，對應前面「phase 數很少」的實驗。
4. **NetworkX 的數字不代表演算法本身**：NetworkX 用 dict-of-dict 存圖、每條邊的屬性是一個 dict，常數因子比我們的平行陣列大。它的 `dinitz` 在格子圖上要 59 秒，比它自己的 `edmonds_karp` 還慢；讀原始碼可以看到它的 BFS 一碰到 `t` 就停、DFS 從 `t` 沿 `parents` 反向走，沒有我們的 current-arc 與死路剪枝。我沒有用 profiler 逐行確認慢在哪裡，這點請當成待驗證的推測；結論只有一個：**在 NetworkX 3.7 上需要速度時用預設的 `preflow_push`**。
5. **Python 版本的天花板**：這些都是純 Python。同樣的演算法用 C 寫，常數通常差一到兩個數量級（本章沒有量；這類常數因子是 [Ch 41](./41-large-graph-engineering.md) 大圖工程的主題）。

## 對比與取捨

| 演算法 | 最壞時間 | 實務特性 | 適用情境 |
| --- | --- | --- | --- |
| Edmonds–Karp | `O(V E²)` | 程式最短；每條增廣路都重跑 BFS | 教學、流量值很小的實例 |
| Dinic | `O(V² E)`；單位容量二分圖 `O(E √V)` | 實作中等；單位容量極強 | 匹配、邊不相交路徑、競程 |
| Push-relabel（FIFO + gap） | `O(V³)` | 局部操作、易平行化；需要 heuristic 才快 | 大型稠密圖、NetworkX 預設 |
| Boykov–Kolmogorov | 沒有好的多項式上界（論文自己也這麼說） | 在視覺類格子圖上很快 | 影像分割（用 C++ 實作時） |

為什麼不全部用 push-relabel？它的實務表現非常依賴 heuristic（gap、global relabeling：定期從 `t` 反向 BFS 重算精確高度），沒有這些常常比 Dinic 慢；程式也比較難除錯，因為中間狀態不是合法的流。為什麼不全部用 Dinic？在某些稠密、長路徑的實例上 push-relabel 明顯較快，Cherkassky–Goldberg 1997 的實驗有系統性比較。

## 踩雷集錦

1. **以為 Dinic 每個 phase 只增廣一條路**：一個 phase 要做到 blocking flow，也就是 level graph 上所有 `s→t` 路徑都被堵住，才重新 BFS。只增廣一條就重新 BFS，就退化成 EK。
2. **以為 blocking flow 就是 level graph 上的最大流**：不是。blocking flow 只保證每條路徑上有一條飽和邊，沒有利用反向邊，值可能小於 level graph 上的最大流。正確性靠的是「下一個 phase 會重建 level graph、反向邊會回來」。
3. **忘了 current-arc**：答案不會錯，複雜度證明卻不成立。本章分層網路上邊的檢查次數變成 4.1 倍；更糟的實例可以差更多。
4. **push-relabel 的 excess 沒推完就以為結束**：演算法要做到「沒有 active 點」為止。只看 `excess[t]` 是否「看起來夠大」就停，得到的是 preflow 不是 flow，中間點不守恆。
5. **遞迴 DFS 撞到 Python 深度限制**：level graph 的深度可以到 `V − 1`。遞迴版本在 30 層的分層網路上還沒事，換成一條 2000 點的長鏈就會 `RecursionError`；`sys.setrecursionlimit` 調大又可能讓 C stack 溢位直接崩潰。用迭代式 DFS。

## 進階：再往深一層

- **Global relabeling**：每做一定數量的 relabel，就從 `t` 在殘餘圖上反向 BFS，把每個點的高度設成到 `t` 的精確距離。和 gap heuristic 並用是 push-relabel 實務上快的主因（Cherkassky–Goldberg 1997）。
- **Highest-label 選點規則**：每次處理最高的 active 點，可以證明是 `O(V² √E)`。
- **平行化**：push 與 relabel 只碰鄰居，GPU 上的最大流多數基於 push-relabel；如果你讀過 [GPU/CUDA 課](../../parallel/gpu_cuda/README.md)，可以想想 excess 的原子加法和高度的競爭條件怎麼處理。
- **理論前沿**：2022 年 almost-linear time 最大流（Chen、Kyng、Liu、Peng、Probst Gutenberg、Sachdeva）走的是內點法路線，和本章的組合式演算法是兩個世界。
- **面試題**：「Dinic 的複雜度怎麼證？」——兩句話：phase 數 ≤ `V − 1`（距離嚴格增加），每個 phase `O(VE)`（current-arc 讓每條邊最多被跳過一次，每次增廣至少飽和一條邊）。

## 動手練習

1. 在 `dinic.py` 裡把 `level[u] = -1` 那行註解掉，同時傳 `current_arc=False`，重跑 `ex_arc.py` 的分層網路，記錄邊的檢查次數。三種組合（都有、只有 current-arc、都沒有）各是多少？
2. 在 `push_relabel.py` 的 `stats` 裡加上「飽和推」與「非飽和推」的分開計數，在格子圖上看兩者的比例，對照定理裡兩者的上界。
3. 實作 global relabeling（每 `V` 次 relabel 做一次從 `t` 出發的反向 BFS），在 `bench.py` 的格子圖上量它帶來的差距。
4. 造一條 `s → v1 → v2 → … → v2000 → t` 的長鏈，用遞迴寫一個 Dinic 的 DFS，觀察它在哪裡失敗；再用本章的迭代版本跑同一張圖。

## 本章重點整理

- Dinic 一個 phase = 一次 BFS 建 level graph + 一個 blocking flow；phase 之間 `s→t` 距離嚴格增加，所以 phase 數 ≤ `V − 1`。
- current-arc 讓 blocking flow 是 `O(VE)`，Dinic 總共 `O(V²E)`；單位容量二分圖上只要 `O(√V)` 個 phase。
- Push-relabel 維持 preflow 與 valid labeling；active 點必有殘餘路徑回 `s`，所以高度 ≤ `2V − 1`；generic 版 `O(V²E)`、FIFO 版 `O(V³)`。
- Gap heuristic：某個高度空了，上面的點都到不了 `t`，直接抬到 `V + 1`。
- 實測（Ryzen 7 5700G、純 Python）：格子圖上 EK 10.1 秒，Dinic 0.114 秒，push-relabel 0.112 秒；演算法的差距要在「難」的實例上才看得出來。

## 自我檢核

- [ ] 不看書，畫出一個 level graph，並說明為什麼新長出來的反向邊一定讓 level 減 1
- [ ] 如果面試官問「blocking flow 和 level graph 上的最大流有什麼不同？為什麼 Dinic 不需要後者？」，你怎麼回答
- [ ] 用自己的話講「active 點一定能回到 `s`」的證明，並說出它在複雜度分析裡被用在哪裡
- [ ] 解釋 gap heuristic 為什麼不會讓答案出錯
- [ ] 你手上有一個「很多短增廣路」的實例和一個「幾條長增廣路」的實例，分別會先試哪個演算法？為什麼

## 延伸閱讀

### 論文

- **[Dinitz' Algorithm: The Original Version and Even's Version](https://doi.org/10.1007/11685654_10)** — Yefim Dinitz，收錄於 *Theoretical Computer Science: Essays in Memory of Shimon Even*，LNCS 3895（Springer，2006），218–240
  - **核心貢獻**：作者本人說明 1970 年原始版本（用了和本章不同的資料結構維護 level graph）和 Even–Itai 改寫版的差異，以及蘇聯演算法學派的背景。
  - **讀哪裡**：前兩節的歷史與原始版本的描述；後面講 Karzanov 等人的延伸可以選讀。
  - **和本章的關聯**：本章的 Dinic 是 Even 版本加上 current-arc；讀完你會知道「Dinic」這個名字和這個演算法本身都有一段誤譯史。前提：讀完本章引理 1、2。
- **[A New Approach to the Maximum-Flow Problem](https://doi.org/10.1145/48014.61051)** — Andrew V. Goldberg、Robert E. Tarjan，Journal of the ACM 35(4)（1988），921–940
  - **核心貢獻**：提出 preflow + 距離標記的 push-relabel 框架，證明 FIFO 版 `O(V³)`，加 dynamic tree 為 `O(VE log(V²/E))`。
  - **讀哪裡**：Section 2–3（generic 演算法與分析）對應本章引理 3、4 與定理；Section 4 的 FIFO 分析是本章只給架構的部分。
  - **和本章的關聯**：`push_relabel.py` 的直接來源。
- **[On Implementing the Push-Relabel Method for the Maximum Flow Problem](https://doi.org/10.1007/PL00009180)** — B. V. Cherkassky、A. V. Goldberg，Algorithmica 19（1997），390–410
  - **核心貢獻**：系統性比較選點規則與 gap、global relabeling 等 heuristic 的實務效果，並和 Dinic 比較。
  - **讀哪裡**：heuristic 的描述與實驗結果表格。
  - **和本章的關聯**：解釋為什麼本章「對比與取捨」說 push-relabel 的速度依賴 heuristic。前提：本章 push-relabel 一節。
- **[An Experimental Comparison of Min-Cut/Max-Flow Algorithms for Energy Minimization in Vision](https://doi.org/10.1109/TPAMI.2004.60)** — Yuri Boykov、Vladimir Kolmogorov，IEEE TPAMI 26(9)（2004），1124–1137
  - **核心貢獻**：提出 NetworkX 裡 `boykov_kolmogorov` 的演算法（搜尋樹重用），並在影像復原、立體視覺、分割的格子圖上和 push-relabel、Dinic 比較。
  - **讀哪裡**：Section 3 的演算法與 Section 5 的實驗；作者網站有開放版本。
  - **和本章的關聯**：本章格子圖實驗的背景；練習 D 的影像分割就是這類圖。注意他們用 C++，本章 NetworkX 版本在格子圖上的表現不代表演算法本身。

### 書籍

- **《Network Flows: Theory, Algorithms, and Applications》** — Ravindra K. Ahuja、Thomas L. Magnanti、James B. Orlin（Prentice Hall，1993）
  - **讀哪幾章**：Ch 7「Maximum Flows: Polynomial Algorithms」（最短增廣路、Dinic 類演算法與 preflow-push 都在這一章；小節編號請以目錄為準）、Ch 8「Maximum Flows: Additional Topics」選讀。三十年前的書，但最大流的組合演算法這部分沒有過時，是最完整的單一參考。
  - **前提**：Ch 20 的定理與本章的兩個實作。
- **《Introduction to Algorithms》第 3 版** — Cormen、Leiserson、Rivest、Stein（MIT Press，2009）
  - **讀哪幾章**：26.4 Push-relabel algorithms 與 26.5 The relabel-to-front algorithm。依 MIT Press 的第 4 版改版說明，這兩節在第 4 版被拿掉了，要讀推送重標記的教科書證明請找第 3 版。

### 官方文件

- **[NetworkX：Flows](https://networkx.org/documentation/stable/reference/algorithms/flow.html)**
  - **讀哪裡**：各 `flow_func`（`edmonds_karp`、`dinitz`、`preflow_push`、`shortest_augmenting_path`、`boykov_kolmogorov`）的頁面，每頁列出原始論文與複雜度。
  - **注意**：`preflow_push` 的頁面提到它實作了 global relabeling（參數 `global_relabel_freq`），這是它在格子圖上比我們的版本慢不到兩倍的原因之一。

到這裡我們有了快的最大流。下一章把最大流用在最經典的組合問題上：二分圖匹配。你會看到 Hall 定理、König 定理都是 max-flow min-cut 換了一身衣服，而 Dinic 在單位容量二分圖上就是 Hopcroft–Karp。

→ [Ch 22 二分圖匹配：Hall、König、Hopcroft–Karp](./22-bipartite-matching.md)
