# Ch 14 — MST:最小生成樹(點到即可)

> 目標:知道 MST 是什麼、Kruskal 和 Prim 各自的實作,但不需要鑽太深——面試罕見,見了也通常是變形。

## 什麼是 MST

**最小生成樹(Minimum Spanning Tree)**:無向連通圖裡,一個包含所有 n 個節點、剛好 n-1 條邊、邊權總和最小的子圖。

訊號:

- 「用最小總成本連通所有節點 / 城市 / 房子」
- 「建設最便宜的網路」
- Connecting Cities with Minimum Cost (1135)
- Min Cost to Connect All Points (1584)
- Optimize Water Distribution (1168)

**前提**:圖必須是**無向**。有向圖對應概念叫 Minimum Arborescence,面試完全不考。

---

## Kruskal:按邊權排序 + DSU

```python
def kruskal(n, edges):   # edges: [(weight, u, v)]
    edges.sort()
    dsu = DSU(n)
    total = 0
    used = 0
    for w, u, v in edges:
        if dsu.union(u, v):
            total += w
            used += 1
            if used == n - 1: break
    return total if used == n - 1 else -1
```

**心法**:按邊權從小到大,貪婪加邊——只要兩端不在同一連通塊就加。`DSU.union` 返回 False 代表已連通,跳過。

複雜度 O(E log E)(排序為主)。

## Prim:類似 Dijkstra

```python
import heapq

def prim(n, graph):   # graph: adjacency list of (neighbor, weight)
    visited = [False] * n
    h = [(0, 0)]    # (weight, node), 從節點 0 開始
    total = 0
    used = 0
    while h and used < n:
        w, u = heapq.heappop(h)
        if visited[u]: continue
        visited[u] = True
        total += w
        used += 1
        for v, wt in graph[u]:
            if not visited[v]:
                heapq.heappush(h, (wt, v))
    return total if used == n else -1
```

**跟 Dijkstra 差在哪**:Dijkstra 的 relax 是「從 start 到 v 的總路徑長」,Prim 是「單一條邊的權重」。結構相同但比較的量不同。

複雜度 O(E log V)。

---

## Kruskal vs Prim

| | Kruskal | Prim |
|---|---|---|
| 寫法 | 排序 + DSU | 類 Dijkstra |
| 圖密集 | 相對差 | 好 |
| 圖稀疏 | 好 | 相對差 |
| 面試常見 | 更常見 | 次之 |

**建議**:面試寫 Kruskal,code 乾淨,有 DSU 這張「通用牌」。Prim 遇到再說。

---

## 經典題

### Min Cost to Connect All Points (1584)

> N 個 2D 點,點對之間邊權是 Manhattan 距離。連通所有點的最小成本。

用 Kruskal:

```python
def min_cost_connect_points(points):
    n = len(points)
    edges = []
    for i in range(n):
        for j in range(i + 1, n):
            w = abs(points[i][0] - points[j][0]) + abs(points[i][1] - points[j][1])
            edges.append((w, i, j))
    edges.sort()

    dsu = DSU(n)
    total = 0
    for w, u, v in edges:
        if dsu.union(u, v):
            total += w
    return total
```

建圖 O(N²),Kruskal O(E log E) = O(N² log N)。N ≤ 1000 可以。

### Optimize Water Distribution (1168)

> 每個房子可以挖井(成本 `wells[i]`),或跟鄰居共用管線(成本 `pipes[(u, v)]`)。最小總成本。

**技巧**:加一個**虛擬節點 0**,從 0 到每個房子 i 連一條邊 weight = `wells[i-1]`(代表挖井)。這樣就轉化成普通 MST。

```python
def min_cost_to_supply_water(n, wells, pipes):
    edges = [(w, 0, i + 1) for i, w in enumerate(wells)]
    edges.extend((w, u, v) for u, v, w in pipes)
    edges.sort()
    dsu = DSU(n + 1)
    total = 0
    for w, u, v in edges:
        if dsu.union(u, v):
            total += w
    return total
```

**虛擬節點**這個技巧常用在 MST 變形——把「每個節點的內在成本」轉成「從根連一條邊」。記下來。

---

## 不太考的細節

以下內容**面試遇到的機率 < 5%**,掃過就好:

- **MST 的唯一性**:所有邊權不同時,MST 唯一。
- **Borůvka 演算法**:第三種 MST 演算法,並行友好,面試不考。
- **Steiner Tree**:MST 的推廣(允許加中間節點),NP-hard,也不考。
- **Kirchhoff 定理**:算「生成樹個數」的矩陣公式,純競程。

---

## 自我檢核

- [ ] MST 的條件(節點數、邊數、是否環)是什麼?
- [ ] Kruskal 用什麼資料結構避免環?
- [ ] Prim 跟 Dijkstra 的差別用一句話說。
- [ ] 「每個節點有自己的成本」這類 MST 變形怎麼處理?

---

Part 2 結束。接下來進入演算法範式(Part 3)——DP、貪婪、backtracking、binary search 這些大頭。

→ [Practice B — 樹與圖](./practice-b-tree-graph.md)(先略過,繼續章節)

→ [Ch 15 Binary Search:找答案不是找數字](./15-binary-search.md)
