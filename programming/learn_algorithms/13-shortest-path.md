# Ch 13 — Shortest Path

> 目標:搞懂 BFS / Dijkstra / Bellman-Ford / Floyd 各自的適用條件,知道為什麼某個演算法對某種邊權不 work。

## 四個演算法,四種場景

| 演算法 | 邊權 | 單源 / 多源 | 複雜度 |
|---|---|---|---|
| BFS | 全相同(通常 1) | 單源 | O(V + E) |
| Dijkstra | 非負 | 單源 | O((V + E) log V) |
| Bellman-Ford | 可負(無負環) | 單源 | O(V × E) |
| Floyd-Warshall | 可負(無負環) | 全對全 | O(V³) |

面試 90% 考 BFS 和 Dijkstra。Bellman-Ford 偶爾(需要偵測負環的時候)。Floyd 極罕見(V 夠小才能用)。

---

## BFS:等權圖最短路

**訊號**:「最少步數」「最短變換」「邊權全 1」。

上一章已細講。模板:

```python
from collections import deque

def bfs_shortest(start, target, neighbors_fn):
    q = deque([(start, 0)])
    visited = {start}
    while q:
        node, d = q.popleft()
        if node == target: return d
        for nb in neighbors_fn(node):
            if nb not in visited:
                visited.add(nb)
                q.append((nb, d + 1))
    return -1
```

### 0-1 BFS(邊權只有 0 和 1)

Dijkstra 的「O(V+E)」版。用 **deque**:邊權 0 的鄰居 appendleft,邊權 1 的 append。

```python
from collections import deque

def bfs_01(n, graph, start):
    dist = [float('inf')] * n
    dist[start] = 0
    q = deque([start])
    while q:
        u = q.popleft()
        for v, w in graph[u]:    # w 只能是 0 或 1
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
                if w == 0: q.appendleft(v)
                else: q.append(v)
    return dist
```

經典題:Shortest Path in Binary Matrix 變體、Minimum Cost to Make at Least One Valid Path。

---

## Dijkstra:非負邊權的單源最短路

**訊號**:有正邊權,要單源到各點的最短路徑。

### 為什麼要 heap

Naive 版每次線性找當前最小 dist 節點是 O(V²)。用 **min-heap** 找最小,變 O((V+E) log V)。

### 標準模板

```python
import heapq

def dijkstra(n, graph, start):
    dist = [float('inf')] * n
    dist[start] = 0
    h = [(0, start)]
    while h:
        d, u = heapq.heappop(h)
        if d > dist[u]: continue    # 已有更短的,這條過期了
        for v, w in graph[u]:
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                heapq.heappush(h, (nd, v))
    return dist
```

**三個重點**:

1. `if d > dist[u]: continue`:因為我們不做「decrease-key」,而是 push 多份,老的 pop 出來要丟掉。這叫 **lazy deletion**。
2. `heapq.heappush((dist, node))`:dist 要在前,Python 會用 tuple 第一個元素比較。
3. **Pop 時確認最短**:由於 lazy deletion,pop 出的可能是過期值。

### 為什麼負邊會壞 Dijkstra

核心假設:「當前 dist 最小的節點,它的 dist 已經是最終答案」。負邊破壞這個假設——即使 A 當前 dist 最小,之後可能有條經過負邊的路讓 dist 更小。

**被問「能不能把所有邊權 +常數讓它變正」也不行**——路徑長度不同會被偏置不同,扭曲答案。真的有負邊就用 Bellman-Ford。

### 面試常見 Dijkstra 題

- Network Delay Time (743)
- Cheapest Flights Within K Stops (787)——加狀態維度:`(cost, node, stops)`
- Path with Minimum Effort (1631)——dist 是「路徑最大邊」,改 relax 條件
- Swim in Rising Water (778)

### 變形:Dijkstra 找「路徑最大邊最小」

不是 sum,而是 max。relax 條件從 `nd < dist[v]` 改成 `max(d, w) < dist[v]`。

```python
nd = max(d, w)
if nd < dist[v]:
    dist[v] = nd
    ...
```

**這類變形的通用心法**:把 dist 定義成「路徑上你想最小化的那個值」,調 relax 公式即可。

---

## Bellman-Ford:允許負邊,能偵測負環

**訊號**:有負邊,或題目要偵測負環。

```python
def bellman_ford(n, edges, start):
    dist = [float('inf')] * n
    dist[start] = 0
    for _ in range(n - 1):
        for u, v, w in edges:
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
    # 再跑一輪,若還能 relax 代表有負環
    for u, v, w in edges:
        if dist[u] + w < dist[v]:
            return None    # 有負環
    return dist
```

**為什麼 n-1 輪夠**:無負環時,最短路徑最多 n-1 條邊,每輪「至少正確一層」,n-1 輪就收斂。

### SPFA(Bellman-Ford 加 queue 剪枝)

只對「dist 剛變小的節點」繼續 relax。Python 實作:

```python
def spfa(n, graph, start):
    dist = [float('inf')] * n
    dist[start] = 0
    in_q = [False] * n
    cnt = [0] * n    # 入隊次數,偵測負環
    q = deque([start])
    in_q[start] = True
    while q:
        u = q.popleft()
        in_q[u] = False
        for v, w in graph[u]:
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
                cnt[v] += 1
                if cnt[v] >= n: return None    # 負環
                if not in_q[v]:
                    q.append(v)
                    in_q[v] = True
    return dist
```

面試少見,知道概念就好。SPFA 在競程常用,但最壞仍 O(V × E)。

---

## Floyd-Warshall:全對全最短路(DP)

**訊號**:V ≤ 500,要知道「所有點對」之間的最短距離。

```python
def floyd(n, edges):
    INF = float('inf')
    dist = [[INF] * n for _ in range(n)]
    for i in range(n): dist[i][i] = 0
    for u, v, w in edges:
        dist[u][v] = w
        dist[v][u] = w    # 無向才加

    for k in range(n):
        for i in range(n):
            for j in range(n):
                if dist[i][k] + dist[k][j] < dist[i][j]:
                    dist[i][j] = dist[i][k] + dist[k][j]
    return dist
```

**k 迴圈必須在最外面**。這是 DP:「只用前 k 個節點作中間點時,i 到 j 的最短路」。

面試題例:Find the City With the Smallest Number of Neighbors (1334)。

---

## 訊號速查

```
邊權全相同(或 0/1)  → BFS(或 0-1 BFS)
非負權、單源         → Dijkstra
有負權、單源         → Bellman-Ford
有負權、全對全       → Floyd
V 很大、E 稀疏       → adjacency list + Dijkstra
V 很小、E 密         → matrix + Floyd
需要「第 k 短」       → 變形 Dijkstra(Eppstein / k-heap)
帶狀態的最短路       → 狀態空間擴展(例如 (node, stops, mask))
```

## 帶狀態的最短路(面試變形題心法)

很多進階題把「最短路」加狀態:

- 「走 k 步內到 target 的最少花費」→ state = (node, steps_used)
- 「鑰匙 / 物品收集」→ state = (node, collected_items_mask)
- 「燃料 / 錢」→ state = (node, fuel_remaining)

**把圖上的節點換成 (原節點, 狀態),然後跑 Dijkstra / BFS**。Cheapest Flights Within K Stops 就是典型。

```python
# Cheapest Flights Within K Stops (787)
def find_cheapest(n, flights, src, dst, k):
    graph = defaultdict(list)
    for u, v, w in flights:
        graph[u].append((v, w))
    h = [(0, src, 0)]    # (cost, node, stops)
    best = {}            # (node, stops) -> cost
    while h:
        cost, u, stops = heapq.heappop(h)
        if u == dst: return cost
        if stops > k: continue
        if (u, stops) in best and best[(u, stops)] <= cost: continue
        best[(u, stops)] = cost
        for v, w in graph[u]:
            heapq.heappush(h, (cost + w, v, stops + 1))
    return -1
```

---

## 陷阱

### 陷阱 1:Dijkstra 用錯成有負邊

面試題若沒明說「邊權非負」,要自己檢查。不確定就問面試官。

### 陷阱 2:沒處理多重邊

一對節點有多條邊,rel​ax 時不用特別處理(heap 版自然會取小)。但用 matrix 建圖時要 `matrix[u][v] = min(matrix[u][v], w)`。

### 陷阱 3:Dijkstra 忘了 `if d > dist[u]: continue`

Python heap 沒 decrease-key,不 skip 過期值會做多餘 relax,複雜度退化。

### 陷阱 4:Bellman-Ford 偵測負環,要跑「所有點都可達」

如果負環在起點不可達的子圖,單從 start 的 Bellman-Ford 偵測不到。要偵測整個圖的任何負環,加一個虛擬節點連到所有節點(邊權 0),從它跑。

---

## 自我檢核

- [ ] 為什麼 Dijkstra 對負邊失效?舉一個反例。
- [ ] 0-1 BFS 為什麼可以用 deque 取代 heap?
- [ ] Bellman-Ford 為什麼是 n-1 輪?
- [ ] Floyd 的 `k` 迴圈為什麼必須是最外層?
- [ ] Cheapest Flights Within K Stops 如何把狀態加入圖?

→ [Ch 14 MST(點到即可)](./14-mst.md)
