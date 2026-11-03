# Ch 24 — 最小費用流與指派問題

> **目標**：把 max-flow 加上「每單位流量要付錢」，證明 successive shortest path 為什麼每一步都最優，看懂 potential 怎麼讓 Dijkstra 在有負邊的 residual graph 上合法運作，最後把匈牙利演算法理解成「二分圖上的 successive shortest path」，並和 SciPy、NetworkX 對拍。
>
> **環境**：Python 3.13.12、NetworkX 3.7、NumPy 2.5.4、SciPy 1.18.1，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

[Ch 20](./20-max-flow-min-cut.md) 的 max-flow 只問「最多能送多少」。真實問題幾乎都多一個維度：每條管線、每個指派、每台機器都有成本。把 n 個工作分給 n 個工人、每對 (工人, 工作) 有不同耗時，求總耗時最小——這是**指派問題（assignment problem）**，二戰後美國海軍研究辦公室資助的作業研究圈花了很多力氣在它身上。

1955 年 Harold Kuhn 發表「The Hungarian Method for the Assignment Problem」，名字裡的 Hungarian 是在致敬兩位匈牙利數學家 Dénes Kőnig 與 Jenő Egerváry：Kuhn 的演算法本質上是把 Egerváry 1931 年的對偶定理（[Ch 22](./22-bipartite-matching.md) König 定理的加權版）變成可執行的程序。在那之前的做法是什麼？要嘛暴力列舉 n! 種排列，要嘛把它寫成線性規劃丟給 Dantzig 1947 年的 simplex——simplex 可行，但它不利用問題的組合結構，而且當時的電腦算一個中型 LP 很吃力。

同一時期，Ford 與 Fulkerson 把指派問題、運輸問題、最短路、max-flow 統一成一個模型：**最小費用流（min-cost flow）**。它是組合最佳化裡的「萬用轉接頭」：

| 特例 | 怎麼從 min-cost flow 得到 |
| --- | --- |
| 最短路 | s 供給 1、t 需求 1、容量無限 |
| max-flow | 所有需求 0、原邊費用 0，加一條容量無限、費用 -1 的 t→s 邊（環流越大越省錢） |
| 指派問題 | 二分圖、所有容量 1、每個左點供給 1、每個右點需求 1 |
| 運輸問題 | 二分圖、供給與需求任意 |

這章的主角是 **successive shortest path（SSP）**：反覆沿著「最便宜的增廣路」送流量。它的正確性證明是本章的核心，而那個證明會自然長出 **potential（勢能）**——這個概念同時是 Dijkstra 能處理負邊的鑰匙、匈牙利演算法的對偶變數、以及 [Ch 26](./26-lp-duality.md) LP 對偶的具體化身。

## 先建立直覺

想像你是物流調度員，要從倉庫 s 送 2 箱貨到門市 t。你的策略是「每次都挑目前最便宜的路線送一箱」。問題是：第一箱走了最便宜的路，可能佔掉了第二箱更需要的路段。

```text
     s --1--> a --1--> b --1--> t        (邊上數字 = 每單位費用，容量都是 1)
     |        |                 ^
     |        +--------5--------+
     +--------5-------> b

第 1 箱：s->a->b->t，費用 3（最便宜）
第 2 箱：s->b ... 但 b->t 已滿；s->a 也滿了
```

貪婪地「再找下一條原圖上的便宜路」會卡死或做出次佳解。解法和 max-flow 一樣：在 **residual graph** 上找路，而 residual graph 裡的反向邊帶**負費用**——走反向邊 b→a 等於「把第一箱在 a→b 這段退貨」，退貨會退錢。

```text
residual graph（第 1 箱送完後）：
     s <-(-1)- a <-(-1)- b <-(-1)- t      反向邊：退貨，費用為負
     s --5--> b   a --5--> t              原本沒用到的邊

第 2 箱：s->b (5) -> a (-1，退掉 a->b) -> t (5) = 9
總費用 3 + 9 = 12 = (s->a->t: 6) + (s->b->t: 6)
```

這就是 SSP 的全部直覺：**每一步都走 residual graph 上的最短路，負邊讓你有反悔的機會**。要證明的是：這種「每步最便宜」的貪婪，最後一定是全域最便宜。

> 如果你對 residual graph 與增廣路還不熟，先回看 [Ch 20 最大流最小割與 Edmonds–Karp](./20-max-flow-min-cut.md) 的 residual graph 一節。

## 問題定義

**最小費用流問題**：有向圖 G=(V,E)，每條邊有容量 `c(u,v) ≥ 0` 與單位費用 `w(u,v)`（可為負），每個點有需求 `b(v)`（負數代表供給），且 `Σ b(v) = 0`。找流量 f 滿足

- 容量限制：`0 ≤ f(u,v) ≤ c(u,v)`
- 流量守恆：流入 − 流出 = `b(v)`

並最小化 `cost(f) = Σ w(u,v)·f(u,v)`。本章程式主要解它的常見變體 **min-cost max-flow**：在所有 s-t 最大流裡找費用最小的。

最直接的演算法：從 f = 0 開始，每輪在 residual graph 上用 Bellman–Ford 找 s 到 t 的最短路（residual graph 有負邊，所以不能直接用 Dijkstra——這點下面會實際弄壞給你看），沿它推瓶頸容量，直到 t 不可達。這就是 SSP。它的實作和 Ch 20 的 Edmonds–Karp 幾乎一樣，差別只在「BFS 找最少邊數的路」換成「找費用最短的路」。程式碼放在後面的 potential 一節，因為我們會先證明它為什麼對，再用證明裡長出來的 potential 把 Bellman–Ford 換成 Dijkstra。

## 底層機制：為什麼「每步最便宜」會是全域最便宜？

兩個定理撐起整個 SSP。第一個刻畫「什麼樣的流是最優的」，第二個說明 SSP 每一步都維持這個性質。

**定理 1（負圈最優性條件）**：一個滿足需求的可行流 f 是最小費用流，若且唯若 residual graph `G_f` 裡沒有費用為負的有向圈。

證明。(⇒) 若 `G_f` 有負圈 C，令 ε 為 C 上 residual 容量的最小值（> 0）。沿 C 推 ε 單位：每個點流入與流出各增加 ε，守恆不變；容量不超過 residual 容量，所以仍可行。新費用 = `cost(f) + ε·cost(C) < cost(f)`，f 不是最優。

(⇐) 設 `G_f` 沒有負圈，`f*` 是任一滿足同樣需求的可行流。在 `G_f` 的邊上定義 g：對原圖每條邊 (u,v)，若 `f*(u,v) > f(u,v)`，令前向 residual 邊上 `g = f*(u,v) − f(u,v)`（不超過其 residual 容量 `c − f`）；若 `f*(u,v) < f(u,v)`，令反向 residual 邊 (v,u) 上 `g = f(u,v) − f*(u,v)`（不超過其 residual 容量 `f`）。因為 f 和 `f*` 在每個點的淨流量都等於 `b(v)`，g 在每個點的淨流量是 0——g 是 `G_f` 上的一個非負**環流（circulation）**。反向邊費用是 `−w`，所以 `cost(g) = cost(f*) − cost(f)`。

接著用**流分解**：任何非負環流都能拆成若干有向圈的和。做法是挑一條 `g > 0` 的邊往前走；因為守恆，每走進一個點就一定有一條 `g > 0` 的邊可以走出去；點數有限，最後必回到走過的點，得到一個圈。把圈上最小的 g 值從整個圈扣掉，至少一條邊歸零，重複直到 g 全為 0。每個圈都在 `G_f` 裡，費用 ≥ 0，所以 `cost(g) ≥ 0`，即 `cost(f*) ≥ cost(f)`。∎

**定理 2（SSP 保持最優性）**：若 `G_f` 沒有負圈，P 是 `G_f` 中 s 到 t 的最短路，沿 P 推任意 δ 單位（δ 不超過瓶頸）得到 f'，則 `G_{f'}` 也沒有負圈。

證明。令 `d(v)` 為 `G_f` 中 s 到 v 的最短距離（沒有負圈所以定義良好），只對 s 可達的點定義。對可達點之間每條 residual 邊 (u,v)，最短路的三角不等式給出 `d(v) ≤ d(u) + w(u,v)`，也就是**簡化費用（reduced cost）**

```text
w_d(u,v) = w(u,v) + d(u) − d(v) ≥ 0
```

最短路 P 上每條邊等號成立，`w_d = 0`。推流後 `G_{f'}` 相對 `G_f` 的變化只有兩種：P 上某些邊容量歸零而消失（消失的邊不會造成負圈）、以及 P 上邊的反向邊出現，其簡化費用是 `−w(u,v) + d(v) − d(u) = −w_d(u,v) = 0`。所以 `G_{f'}` 裡可達點之間每條邊的簡化費用仍 ≥ 0。

對任意有向圈 C，`Σ_{C} w_d = Σ_{C} w + Σ_{C}(d(u) − d(v)) = Σ_{C} w`，因為 d 的項沿圈頭尾相消。所以全在可達點上的圈，費用 ≥ 0。至於含不可達點的圈：在 `G_f` 裡沒有從可達點指向不可達點的 residual 邊（否則它就可達了），而新出現的邊都在 P 上、兩端都可達，所以 `G_{f'}` 裡仍沒有這種邊，任何圈要嘛全在可達點、要嘛全在不可達點；後者的邊在推流前後沒變，原本就沒有負圈。∎

兩個定理合起來：把「送 k 單位從 s 到 t」看成 `b(s) = −k`、`b(t) = k` 的最小費用流問題。從 f = 0 出發（要求原圖沒有負圈），每推一次，f 都是「同樣流量值下費用最小」的流。當 `G_f` 沒有 s-t 路徑時，依 [Ch 20](./20-max-flow-min-cut.md) 的 max-flow min-cut 定理，f 已是最大流——所以 SSP 輸出的是 min-cost max-flow。

順便證明了下面 trace 會看到的「長度單調不減」：推流後 s 可達的點對每條 residual 邊都有 `w_d ≥ 0`，新的最短路長度 = 舊 d(t) + 新圖上簡化費用的最短路長 ≥ d(t)。

**終止與複雜度**：容量是整數時，每次至少推 1 單位，最多 `F`（最大流值）輪。Bellman–Ford 版每輪 `O(VE)`，總共 `O(F·VE)`。注意這是**偽多項式**：F 可以是容量的數值，而不是輸入的位元長度。容量是無理數時，Ch 20 裡 Ford–Fulkerson 可能不終止的問題在這裡同樣存在。

## Potential：讓 Dijkstra 在負邊上合法

定理 2 的證明裡藏了一個寶物：**上一輪的最短距離 d，正好讓這一輪所有 residual 邊的簡化費用非負**。簡化費用非負就能用 Dijkstra。這就是 **potential（勢能）** `h(v)`：

```text
每輪：
  1. 在 G_f 上用簡化費用 w(u,v) + h(u) − h(v) 跑 Dijkstra，得到 dist'(v)
  2. 真實距離 d(v) = dist'(v) + h(v) − h(s)；本實作 h(s) 恆為 0
  3. h(v) += dist'(v)    ← 新的 h 就是這輪的 d，下一輪繼續保證非負
  4. 沿最短路推流
```

第 2 步成立是因為任何 s-v 路徑的簡化長度 = 真實長度 + h(s) − h(v)，對所有路徑差同一個常數，最短路不變。第 3 步之後 `h = d`，由定理 2 的證明，下一輪的 residual 邊簡化費用都 ≥ 0。

兩個細節：

- **初始 potential**：原圖若有負邊，第一輪還是要跑一次 Bellman–Ford 算 h（原圖必須沒有負圈）。原圖費用全非負時 h = 0 即可。
- **不可達的點**：這輪 Dijkstra 到不了的點不更新 h。定理 2 的證明說過，不可達點永遠不會再變可達（新邊只出現在可達點之間），所以它們的 h 不會被用到。

下面是完整實作。`use_potential=False` 是故意留的開關，等一下用來把它弄壞。底部跑開頭的「退貨」例子和另一個小圖，並和 NetworkX 對拍：

```python
"""Min-cost max-flow：successive shortest path + potentials。"""
import heapq

import networkx as nx

INF = float("inf")


class MCMF:
    def __init__(self):
        self.idx, self.names = {}, []           # 節點名稱 <-> 整數編號
        self.g, self.to, self.cap, self.cost = [], [], [], []

    def _id(self, x):
        if x not in self.idx:
            self.idx[x] = len(self.names)
            self.names.append(x)
            self.g.append([])
        return self.idx[x]

    def add_edge(self, u, v, cap, cost):
        u, v = self._id(u), self._id(v)
        # 第 i 條邊與第 i^1 條互為反向邊：反向邊容量 0、費用 -cost
        for a, b, c, w in ((u, v, cap, cost), (v, u, 0, -cost)):
            self.g[a].append(len(self.to))
            self.to.append(b)
            self.cap.append(c)
            self.cost.append(w)

    def _bellman_ford(self, s):
        # 原圖可能有負邊（不能有負圈）：算初始 potential
        h = [INF] * len(self.names)
        h[s] = 0
        for _ in range(len(self.names) - 1):
            for u, es in enumerate(self.g):
                for e in es:
                    if h[u] < INF and self.cap[e] > 0 and h[u] + self.cost[e] < h[self.to[e]]:
                        h[self.to[e]] = h[u] + self.cost[e]
        return [x if x < INF else 0 for x in h]

    def _dijkstra(self, s, h):
        n = len(self.names)
        dist, prev, done = [INF] * n, [-1] * n, [False] * n
        dist[s] = 0
        pq = [(0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if done[u]:
                continue
            done[u] = True                      # 教科書版：pop 出來就定案
            for e in self.g[u]:
                v = self.to[e]
                if self.cap[e] > 0 and not done[v]:
                    nd = d + self.cost[e] + h[u] - h[v]   # reduced cost
                    if nd < dist[v]:
                        dist[v], prev[v] = nd, e
                        heapq.heappush(pq, (nd, v))
        return dist, prev

    def solve(self, s, t, use_potential=True, trace=False):
        s, t = self._id(s), self._id(t)
        h = self._bellman_ford(s) if use_potential else [0] * len(self.names)
        flow = cost = 0
        while True:
            dist, prev = self._dijkstra(s, h)
            if dist[t] == INF:
                return flow, cost
            if use_potential:
                h = [h[v] + dist[v] if dist[v] < INF else h[v] for v in range(len(h))]
            path, v = [], t
            while v != s:
                path.append(prev[v])
                v = self.to[prev[v] ^ 1]
            push = min(self.cap[e] for e in path)
            for e in path:
                self.cap[e] -= push
                self.cap[e ^ 1] += push
            length = sum(self.cost[e] for e in path)
            flow, cost = flow + push, cost + push * length
            if trace:
                walk = [self.names[s]] + [self.names[self.to[e]] for e in reversed(path)]
                print(f"  augment {'->'.join(walk)}  len={length}  push={push}")


if __name__ == "__main__":
    tests = {
        "basic":  [("s", "a", 4, 2), ("s", "b", 2, 2), ("a", "b", 2, 1),
                   ("a", "t", 2, 6), ("b", "t", 5, 3)],
        "regret": [("s", "a", 1, 1), ("a", "b", 1, 1), ("b", "t", 1, 1),
                   ("s", "b", 1, 5), ("a", "t", 1, 5)],
    }
    for name, edges in tests.items():
        m, G = MCMF(), nx.DiGraph()
        for u, v, c, w in edges:
            m.add_edge(u, v, c, w)
            G.add_edge(u, v, capacity=c, weight=w)
        print(f"[{name}]")
        ours = m.solve("s", "t", trace=True)
        F = nx.max_flow_min_cost(G, "s", "t")
        print("  ours", ours, " nx", (nx.maximum_flow_value(G, "s", "t"), nx.cost_of_flow(G, F)))
```

存成 `mcmf.py` 執行：

```text
[basic]
  augment s->b->t  len=5  push=2
  augment s->a->b->t  len=6  push=2
  augment s->a->t  len=8  push=2
  ours (6, 38)  nx (6, 38)
[regret]
  augment s->a->b->t  len=3  push=1
  augment s->b->a->t  len=9  push=1
  ours (2, 12)  nx (2, 12)
```

三個值得停下來看的地方：

- **退貨真的發生了**：`[regret]` 的第二條增廣路 `s->b->a->t` 走了反向邊 b→a，長度 `5 + (-1) + 5 = 9`。最終的流是 s→a→t 與 s→b→t 各一單位，a→b 被完全退掉。
- **增廣路長度單調不減**：`[basic]` 裡 5 → 6 → 8，正是上面定理 2 證明最後一段的結論。
- **為什麼用邊陣列而不是課程慣用的 `dict[node, dict[node, weight]]`？** 因為 residual graph 裡 (u,v) 的反向邊 (v,u) 可能和原圖本來就有的 (v,u) 撞在一起，而兩者費用不同（一個是 `-w(u,v)`，一個是 `w(v,u)`）。字典會把它們合併成一格，費用就錯了。邊陣列配 `e ^ 1` 找反向邊是標準做法：第 0、1 條是一對，第 2、3 條是一對，以此類推。

NetworkX 的 `max_flow_min_cost` 在 3.7 版是先用 `maximum_flow_value`（預設 `preflow_push`）算最大流值，再把它變成需求丟給 `min_cost_flow`，後者呼叫 `network_simplex`——這是讀 3.7 原始碼與錯誤 traceback 確認的。所以我們是拿 SSP 和完全不同的演算法對拍，對得上才有說服力。

接著在 500 個含負邊的隨機圖上對拍，並把 potential 關掉看教科書版 Dijkstra（點一旦 pop 就定案）怎麼出錯。存成 `test_mcmf.py`，和 `mcmf.py` 放同一個資料夾：

```python
import random

import networkx as nx

from mcmf import MCMF


def random_instance(rng, n):
    # cost = 非負基底 + p[u] - p[v]：單邊可為負，但沿任何圈 p 項相消 → 沒有負圈
    p = [rng.randint(0, 8) for _ in range(n)]
    m, edges = rng.randint(n, n * (n - 1) // 2), {}
    while len(edges) < m:
        u, v = rng.randrange(n), rng.randrange(n)
        if u != v and (v, u) not in edges:
            edges[(u, v)] = (rng.randint(1, 6), rng.randint(0, 6) + p[u] - p[v])
    return edges


for use_potential in (True, False):
    wrong, first, neg = 0, None, 0
    for seed in range(500):
        rng = random.Random(seed)
        n = rng.randint(4, 9)
        edges = random_instance(rng, n)
        neg += any(w < 0 for _, w in edges.values())
        m, G = MCMF(), nx.DiGraph()
        G.add_nodes_from(range(n))
        for x in range(n):
            m._id(x)
        for (u, v), (c, w) in edges.items():
            m.add_edge(u, v, c, w)
            G.add_edge(u, v, capacity=c, weight=w)
        F = nx.max_flow_min_cost(G, 0, n - 1)
        ref = (nx.maximum_flow_value(G, 0, n - 1), nx.cost_of_flow(G, F))
        got = m.solve(0, n - 1, use_potential)
        if got != ref:
            wrong += 1
            first = first or (seed, got, ref)
    print(f"use_potential={use_potential!s:5}  wrong={wrong}/500  "
          f"(with negative edges: {neg})  first={first}")
```

```text
use_potential=True   wrong=0/500  (with negative edges: 442)  first=None
use_potential=False  wrong=23/500  (with negative edges: 442)  first=(1, (3, 4), (3, 2))
```

關掉 potential 後，500 題錯 23 題，而且錯的方式很陰險：第一個錯例流量值對（3），費用錯（4 vs 2）。如果你只檢查 max-flow 值，這個 bug 不會被發現。另外注意 `random_instance` 產生費用的方式：`base + p[u] − p[v]` 這個技巧本身就是 potential——任何圈上 p 項相消，所以圈費用等於非負基底的和，保證沒有負圈。這是在測試中「造出有負邊但沒有負圈的圖」最乾淨的方法。

一個常見的混淆：`heapq` 的「lazy」Dijkstra（不設 `done`，dist 變小就重新 push）在沒有負圈時其實會算出正確答案，只是最壞情況可能指數時間。我們刻意用「pop 即定案」的版本，因為那才是 Dijkstra 正確性證明所描述的演算法，也才看得出負邊破壞的是哪個假設。

## 指派問題與匈牙利演算法

指派問題：n×m 成本矩陣 a（n ≤ m），每列選一行、行不重複，總成本最小。建成 min-cost flow 就是：源點連每列（容量 1）、每列連每行（容量 1、費用 `a[i][j]`）、每行連匯點（容量 1）。直接套 SSP 是 n 輪 Dijkstra，每輪 `O(nm log(nm))`。

匈牙利演算法是同一件事的特化版，做得更緊：

- 列一個一個加入（外層迴圈 i）。加入第 i 列等於找一條從它出發的最短增廣路。
- `u[i]`、`v[j]` 就是列點與行點的 potential，`a[i][j] − u[i] − v[j]` 就是簡化費用，演算法始終保持它 ≥ 0。
- 內層迴圈是一個 `O(m)` 掃描版的 Dijkstra（稠密圖不用 heap 反而快）：`minv[j]` 是目前交錯樹到行 j 的最小簡化費用，每次取最小的那一行 `j1` 加進樹，並把差額 `delta` 灌進 potential，讓一條新的「緊邊」（簡化費用 0）出現。
- 走到沒被配對的行時，沿 `way` 回溯翻轉——和 [Ch 22](./22-bipartite-matching.md) 的增廣路翻轉一模一樣。

```python
"""匈牙利演算法 O(n^2 m)，n <= m；回傳 (總成本, 每列分到的行, 對偶 u, v)。"""
import random

import numpy as np
from scipy.optimize import linear_sum_assignment


def hungarian(a):
    n, m = len(a), len(a[0])
    assert n <= m, "列數不能多於行數（多的那邊當行）"
    INF = float("inf")
    u = [0] * (n + 1)          # 列的 potential（1-indexed，0 號是虛擬）
    v = [0] * (m + 1)          # 行的 potential
    p = [0] * (m + 1)          # p[j] = 目前配到行 j 的列；p[0] 是正在插入的列
    way = [0] * (m + 1)        # 交錯樹裡行 j 的前一個行
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)  # minv[j] = 樹內各列到行 j 的最小 reduced cost
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = a[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            # 調整對偶：樹內列 +delta、樹內行 -delta，讓一條新的 tight 邊出現
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:         # 走到空行：找到增廣路
                break
        while j0:                  # 沿 way 翻轉交錯路
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    col_of = [0] * n
    for j in range(1, m + 1):
        if p[j]:
            col_of[p[j] - 1] = j - 1
    total = sum(a[i][col_of[i]] for i in range(n))
    return total, col_of, u[1:], v[1:]


if __name__ == "__main__":
    a = [[4, 1, 3],
         [2, 0, 5],
         [3, 2, 2]]
    total, cols, u, v = hungarian(a)
    print("cost =", total, " assignment =", cols)
    print("u =", u, " v =", v, " sum(u)+sum(v) =", sum(u) + sum(v))
    r, c = linear_sum_assignment(np.array(a))
    print("scipy:", int(np.array(a)[r, c].sum()), list(map(int, c)))

    rng = random.Random(2024)
    bad = 0
    for t in range(1000):
        n = rng.randint(1, 8)
        m = rng.randint(n, 10)
        mat = [[rng.randint(-20, 50) for _ in range(m)] for _ in range(n)]
        ours = hungarian(mat)[0]
        r, c = linear_sum_assignment(np.array(mat))
        bad += ours != int(np.array(mat)[r, c].sum())
    print("random 1000 (n<=m, 含負數):  mismatches =", bad)
```

存成 `hungarian.py`（下一個範例會 import 它）：

```text
cost = 5  assignment = [1, 0, 2]
u = [3, 2, 2]  v = [0, -2, 0]  sum(u)+sum(v) = 5
scipy: 5 [1, 0, 2]
random 1000 (n<=m, 含負數):  mismatches = 0
```

注意第二行：`Σu + Σv = 5`，正好等於最優成本。這不是巧合，而是一張**最優性證書**：

**命題**：若 `u[i] + v[j] ≤ a[i][j]` 對所有 i, j 成立、`v[j] ≤ 0`、未被配到的行 `v[j] = 0`，且配對 σ 上每條邊等號成立，則 σ 是最優指派。

證明。演算法裡 v 只會被減，所以 `v ≤ 0`；從沒進過交錯樹的行 v 保持 0，而進過樹的行最後都被配到（樹裡的行除了終點都已配對，終點在翻轉後也被配對），所以未配對行 `v = 0`。對任何指派 τ：`Σ_i a[i][τ(i)] ≥ Σ_i (u[i] + v[τ(i)]) ≥ Σu + Σ_{全部 j} v[j]`，第二個不等號是因為多加的 `v[j]` 都 ≤ 0。而對 σ：`Σ_i a[i][σ(i)] = Σu + Σ_{σ 用到的 j} v[j] = Σu + Σ_{全部 j} v[j]`。所以 σ 的成本是所有指派的下界，σ 最優。∎

這個「找一組 (u, v) 證明沒人能更便宜」的結構，就是 Egerváry 定理，也是 [Ch 26](./26-lp-duality.md) LP 對偶在指派問題上的樣子。

## 失敗、建模與速度

接在 `hungarian.py` 同一資料夾：

```python
import random
import time

import networkx as nx
import numpy as np
from scipy.optimize import linear_sum_assignment

from hungarian import hungarian

# 1) 貪婪：每列挑目前最便宜的可用行
a = [[1, 2, 100],
     [1, 50, 100],
     [100, 3, 1]]
used, greedy = set(), 0
for row in a:
    j = min((j for j in range(3) if j not in used), key=lambda j: row[j])
    used.add(j)
    greedy += row[j]
print("greedy =", greedy, "  hungarian =", hungarian(a)[0])

# 2) 對偶可行性：u_i + v_j <= a_ij，且配對邊上等號成立
total, cols, u, v = hungarian(a)
print("dual feasible:", all(u[i] + v[j] <= a[i][j] for i in range(3) for j in range(3)),
      " tight on matching:", all(u[i] + v[cols[i]] == a[i][cols[i]] for i in range(3)))

# 3) 同一題建成 min-cost flow 丟給 NetworkX
G = nx.DiGraph()
n = len(a)
G.add_node("S", demand=-n)
G.add_node("T", demand=n)
for i in range(n):
    G.add_edge("S", f"r{i}", capacity=1, weight=0)
    G.add_edge(f"c{i}", "T", capacity=1, weight=0)
    for j in range(n):
        G.add_edge(f"r{i}", f"c{j}", capacity=1, weight=a[i][j])
flow = nx.min_cost_flow(G)
print("nx.min_cost_flow cost =", nx.cost_of_flow(G, flow),
      " pairs =", sorted((r, c) for r in flow if r.startswith("r")
                         for c, f in flow[r].items() if f == 1))

# 4) 最大化 + 長方形：scipy 有 maximize 參數；我們的版本取負號
profit = [[7, 3, 9, 4], [2, 8, 6, 1]]
r, c = linear_sum_assignment(np.array(profit), maximize=True)
print("scipy maximize:", int(np.array(profit)[r, c].sum()), list(map(int, c)),
      "  ours (negate):", -hungarian([[-x for x in row] for row in profit])[0])

# 5) 速度（純 Python vs scipy 的 C 實作）
rng = random.Random(7)
for size in (100, 300):
    mat = [[rng.randint(0, 1000) for _ in range(size)] for _ in range(size)]
    t0 = time.perf_counter()
    ours = hungarian(mat)[0]
    t1 = time.perf_counter()
    arr = np.array(mat)
    r, c = linear_sum_assignment(arr)
    t2 = time.perf_counter()
    print(f"n={size}: ours {t1 - t0:.3f}s  scipy {t2 - t1:.4f}s  "
          f"same={ours == int(arr[r, c].sum())}")
```

```text
greedy = 52   hungarian = 4
dual feasible: True  tight on matching: True
nx.min_cost_flow cost = 4  pairs = [('r0', 'c1'), ('r1', 'c0'), ('r2', 'c2')]
scipy maximize: 17 [2, 1]   ours (negate): 17
n=100: ours 0.009s  scipy 0.0005s  same=True
n=300: ours 0.160s  scipy 0.0075s  same=True
```

貪婪在第一列就拿走了第二列唯一便宜的第 0 行，結果第二列被迫付 50——差了 13 倍。速度數字是在 AMD Ryzen 7 5700G、Windows 11、Python 3.13.12 上各跑一次的 wall-clock，矩陣元素均勻取自 0–1000（seed 7）；均勻隨機矩陣對匈牙利法算是容易的輸入，最壞情況仍是 `O(n^3)`，不要拿這兩個數字外推。SciPy 1.18.1 文件說 `linear_sum_assignment` 實作的是 Crouse（2016）描述的「modified Jonker–Volgenant algorithm with no initialization」，用 C 寫成。

NetworkX 在需求無法滿足時會丟例外；而它的 network simplex 能處理**容量有限的負圈**，這是 SSP 做不到的：

```python
import networkx as nx

# 需求 3，但 s 出去的容量總和只有 2
G = nx.DiGraph()
G.add_node("s", demand=-3)
G.add_node("t", demand=3)
G.add_edge("s", "a", capacity=2, weight=1)
G.add_edge("a", "t", capacity=5, weight=1)
try:
    nx.min_cost_flow(G)
except nx.NetworkXUnfeasible as e:
    print("NetworkXUnfeasible:", e)

# 有負圈 a->b->a（容量有限）：network simplex 會把負圈灌滿
H = nx.DiGraph()
H.add_node("s", demand=-1)
H.add_node("t", demand=1)
H.add_edge("s", "t", capacity=1, weight=4)
H.add_edge("a", "b", capacity=2, weight=-3)
H.add_edge("b", "a", capacity=2, weight=1)
F = nx.min_cost_flow(H)
print("cost =", nx.cost_of_flow(H, F), " a->b:", F["a"]["b"], " b->a:", F["b"]["a"])
```

```text
NetworkXUnfeasible: no flow satisfies all node demands
cost = 0  a->b: 2  b->a: 2
```

第二個例子的總費用是 `4 + 2·(−3) + 2·1 = 0`：s→t 付 4，負圈 a→b→a 轉兩圈賺回 4。SSP 從 f = 0 出發，原圖有負圈就違反定理 2 的前提，Bellman–Ford 算 potential 那一步就已經沒有意義。

## 對比與取捨

| 方法 | 複雜度（整數資料） | 優點 | 缺點 | 適用情境 |
| --- | --- | --- | --- | --- |
| SSP + Bellman–Ford | `O(F·VE)` | 最好寫、允許負邊 | 偽多項式、慢 | 教學、小圖 |
| SSP + Dijkstra + potential | `O(F·E log V)` | 實務上很快 | 原圖不可有負圈；仍偽多項式 | 流量值 F 小（例如指派、配對） |
| Cycle canceling（先找可行流再消負圈） | 視找圈策略而定 | 直接對應定理 1；可處理負圈 | 天真版很慢 | 已有可行流要改善 |
| Network simplex（NetworkX 用的） | 最壞非多項式，實務快 | 處理任意需求與負圈 | 實作複雜（樹結構、退化處理） | 通用求解器 |
| 匈牙利法 | `O(n^2 m)` | 稠密指派問題的專用版 | 只解指派 | n 在幾千以內的稠密矩陣 |
| Jonker–Volgenant（SciPy 用的） | `O(n^3)` 最壞 | 常數小、實務上很快 | 只解指派 | 生產環境的指派 |

為什麼不全部丟給 LP solver？可以，而且 [Ch 26](./26-lp-duality.md) 會證明 LP 的最優頂點自動是整數。但專用演算法能利用圖結構，通常快一到兩個數量級，而且給出的是組合物件（路徑、配對）而不是浮點向量。

## 踩雷集錦

1. **以為 Dijkstra 可以直接跑 residual graph**：即使原圖費用全為正，第一次增廣後就會出現負費用的反向邊。上面實測關掉 potential 錯 23/500；流量值一定對（只要 t 可達就會繼續增廣），錯的是費用。要嘛用 potential，要嘛用 Bellman–Ford/SPFA。
2. **用 dict-of-dict 存 residual graph**：原圖有 (u,v) 與 (v,u) 兩條邊時，反向邊會和原邊撞格。費用不同的兩條邊被合併，答案靜默地錯。用邊陣列加 `e ^ 1`。
3. **以為 SSP 能處理任何負費用**：負邊可以（先跑 Bellman–Ford 拿初始 potential），**負圈不行**。定理 2 需要起點的 `G_0` 無負圈。有負圈時要先把負圈灌滿（cycle canceling）或改用 network simplex。
4. **把「最小費用流」和「最小費用最大流」混為一談**：前者給定需求 `b(v)`；後者先要流量最大再求費用最小。如果你只想要「費用最小、流量不限」（例如有負費用時送越多越賺），SSP 要在最短路長度 `d(t) ≥ 0` 時停下，而不是送到最大流。
5. **長方形矩陣直接丟進只支援方陣的實作**：本章 `hungarian` 要求 n ≤ m，多的一邊必須當行；若列比較多，先轉置。SciPy 的 `linear_sum_assignment` 會自己處理長方形，但它回傳的是「較短那一邊全部配對」的解，不是「允許某些列不配」——要允許不配，得補一排「不配對」的虛擬行並給它成本。

## 進階：再往深一層

- **縮放（scaling）讓複雜度變多項式**：SSP 的 `O(F·…)` 依賴容量大小。容量縮放（capacity scaling）每次只考慮 residual 容量 ≥ Δ 的邊，Δ 逐次減半，可把 F 換成 `log U`。Goldberg 與 Tarjan 的 cost scaling 從費用面縮放，是許多高效 min-cost flow 實作的基礎。
- **強多項式**：Tardos（1985）首先給出不依賴數值大小的強多項式 min-cost flow 演算法，Orlin 之後給出更快的版本。這些結果的細節超出本課範圍，有興趣從 Ahuja–Magnanti–Orlin 的書下手。
- **指派問題的對偶視角**：`u, v` 是 LP 的對偶變數；Kuhn 原論文的推導就是從 König–Egerváry 的對偶定理出發。等你讀完 [Ch 26](./26-lp-duality.md)，回頭看匈牙利法的 `delta` 調整，會發現它是一種 primal-dual 演算法：維持對偶可行，逐步擴大滿足互補鬆弛的配對。
- **生產注意**：資料若是浮點數，`cur < minv[j]` 這類比較會受捨入誤差影響；需要精確答案時，把成本放大成整數再算。

## 動手練習

1. 在 `test_mcmf.py` 印出 seed 1 那張圖（關掉 potential 時的第一個錯例），用 `trace=True` 跑兩種模式，找出是哪一輪 Dijkstra 因為「pop 即定案」而錯過了經過負邊的更短路。
2. 在 `mcmf.py` 的 `solve` 裡加一個 `assert`：每輪 Dijkstra 前，檢查所有 `cap[e] > 0` 且兩端都有限的邊，其簡化費用 ≥ 0。跑 500 題確認它從不觸發，這就是定理 2 的實測。
3. 用 `hungarian` 解一個 4×4 矩陣，然後用 `itertools.permutations` 暴力窮舉 24 種排列驗證最優值，再驗證 `Σu + Σv` 等於它。
4. 讓 `hungarian` 支援 n > m（提示：轉置後解，再把結果轉回來）。

## 本章重點整理

- 可行流是最小費用流 ⟺ residual graph 沒有負圈；證明靠把兩個流的差分解成 residual graph 上的圈。
- SSP 每次沿最短路增廣，用上一輪的最短距離當 potential，所有 residual 邊的簡化費用保持非負，所以永遠不會產生負圈，也讓 Dijkstra 合法。
- 匈牙利演算法是二分圖指派上的 SSP：`u, v` 是 potential 也是對偶變數，`Σu + Σv` 等於最優成本這件事本身就是最優性證書。
- SSP 不處理負圈、複雜度是偽多項式；通用情境用 network simplex 或 scaling 類演算法。

## 自我檢核

- [ ] 不看筆記，能不能用自己的話說明「為什麼兩個可行流的差是 residual graph 上的環流」？
- [ ] 如果有人問「Dijkstra 不能處理負邊，為什麼 min-cost flow 能用 Dijkstra？」，你能在白板上用三行式子回答嗎？
- [ ] 能舉出一個 SSP 前提不成立（原圖有負圈）的例子，並說出應該改用什麼方法？
- [ ] 能解釋匈牙利法裡 `delta` 加到列、從行扣掉的那一步，對應 SSP 的哪一個動作？
- [ ] 想一個你工作中的「把 A 分給 B」問題（例如測試案例分給 CI runner、告警分給值班人員），寫出它的成本矩陣長什麼樣，以及哪些限制無法直接放進指派問題。

## 延伸閱讀

### 論文

- **[The Hungarian Method for the Assignment Problem](https://doi.org/10.1002/nav.3800020109)** — Harold W. Kuhn，Naval Research Logistics Quarterly 2(1–2):83–97（1955）
  - **核心貢獻**：把 König 與 Egerváry 的組合對偶定理變成多項式步數的演算法，演算法名字的由來。
  - **讀哪裡**：開頭的歷史說明與第一個數值例子最有價值；記號和現代教科書不同，讀時對照本章的 `u, v`。
  - **和本章的關聯**：本章匈牙利法的對偶證書就是 Kuhn 從 Egerváry 那裡借來的核心想法。前提：讀過本章與 Ch 22 的 König 定理。
- **[Algorithms for the Assignment and Transportation Problems](http://www.jstor.org/stable/2098689)** — James Munkres，Journal of the Society for Industrial and Applied Mathematics 5(1):32–38（1957）
  - **核心貢獻**：給出匈牙利法的精確步驟與步數上界，並推廣到運輸問題；所以這個演算法也常被叫做 Kuhn–Munkres。
  - **讀哪裡**：第一節的演算法與正確性證明，只有幾頁。
  - **和本章的關聯**：如果你想知道「starred zeros / primed zeros」那套常見的手算教法從哪來，就是這篇。
- **[Theoretical Improvements in Algorithmic Efficiency for Network Flow Problems](https://projects.csail.mit.edu/jacm/References/edmondsk1972:248.html)** — Jack Edmonds、Richard M. Karp，Journal of the ACM 19(2):248–264（1972）
  - **核心貢獻**：除了 Ch 20 的 BFS 增廣路，這篇也處理最小費用流，包含容量縮放與用 node potential 讓最短路計算保持非負邊權的想法（就我所知 Tomizawa 1971 獨立提出了類似技巧）。
  - **讀哪裡**：最小費用流的部分；max-flow 部分在 Ch 20 已經讀過。
  - **和本章的關聯**：本章 `mcmf.py` 的 potential 技巧的經典出處之一。
- **[On implementing 2D rectangular assignment algorithms](https://doi.org/10.1109/TAES.2016.140952)** — David F. Crouse，IEEE Transactions on Aerospace and Electronic Systems 52(4):1679–1696（2016）
  - **這篇說什麼**：比較多種指派演算法的實作細節，包含長方形矩陣的處理；SciPy `linear_sum_assignment` 的文件指名採用這篇描述的方法。
  - **讀哪裡**：講最短增廣路演算法的章節；對照本章 `hungarian` 的內層迴圈。
  - **為什麼值得讀**：想知道生產等級的指派求解器和教科書版差在哪裡。

### 官方文件

- **[scipy.optimize.linear_sum_assignment](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.linear_sum_assignment.html)**
  - **讀哪裡**：`maximize` 參數與 Notes 一節（演算法出處）。
  - **注意**：回傳的是 `(row_ind, col_ind)` 兩個陣列，總成本要自己用 `cost[row_ind, col_ind].sum()` 算。

### 書籍

- **《Network Flows: Theory, Algorithms, and Applications》** — Ravindra K. Ahuja、Thomas L. Magnanti、James B. Orlin（Prentice Hall，1993）
  - **這本書的定位**：網路流的標準參考書，涵蓋最短路、max-flow、min-cost flow 與大量應用建模。出版超過 30 年，演算法核心內容仍適用，但最新的近線性時間 max-flow 結果不在裡面。
  - **讀哪幾章**：依出版社目錄，第 9 章「Minimum Cost Flows: Basic Algorithms」對應本章；第 10 章「Polynomial Algorithms」與第 11 章「Network Simplex Algorithms」是進階版本；第 12 章「Assignments and Matchings」對應匈牙利法一節。前提：本章與 Ch 20。
- **[On the history of combinatorial optimization (till 1960)](https://homepages.cwi.nl/~lex/files/histco.pdf)** — Alexander Schrijver，收錄於《Discrete Optimization》（Handbooks in OR & MS 12，Elsevier，2005）
  - **這篇說什麼**：指派、運輸、最短路、MST 等問題在 1960 年以前的歷史，包含 Egerváry、Kuhn、Ford–Fulkerson 的來龍去脈。
  - **讀哪裡**：指派問題與運輸問題兩節。
  - **為什麼值得讀**：作者是組合最佳化的權威，資料來源考證細；讀完你會知道本章開頭的歷史是怎麼來的。

min-cost flow 的 SSP 是「每一步都貪婪、但可以反悔」。下一章換個方向問：什麼樣的結構可以讓貪婪**完全不需要反悔**就保證最優？答案是 matroid，而你在 Ch 10 寫過的 Kruskal 就是它最有名的例子。

→ [Ch 25 Matroid：貪婪法為什麼有效](./25-matroids.md)
