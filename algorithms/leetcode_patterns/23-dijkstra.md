# Ch 23 — 最短路徑：Dijkstra

> 目標：理解 Dijkstra 的貪心邏輯，用 priority_queue 實作，能解有權圖的最短路徑問題。

## 為什麼 BFS 不夠用？

BFS 找的是「最少邊數」的路徑。
若邊有權重（距離不同），需要 Dijkstra。

**前提**：邊的權重**非負**。有負權重要用 Bellman-Ford（面試較少考）。

## Dijkstra 的核心思維

貪心策略：每次從「目前已知距離最短的未確定節點」出發，更新其鄰居的距離。

```
dist[v] = 從起點到 v 的當前已知最短距離
```

為什麼這樣的貪心是正確的？

一旦從 priority_queue 取出節點 u（距離為 d），就**確定**了 `dist[u] = d`。
因為所有其他路徑都必須經過距離更大的節點，不可能更短（前提：無負權重）。

## 實作：用 min-heap（priority_queue）

```cpp
vector<int> dijkstra(int n, vector<vector<pair<int,int>>>& adj, int src) {
    // adj[u] = {(v, w), ...}：從 u 出發，到 v 的邊，權重 w
    vector<int> dist(n, INT_MAX);
    dist[src] = 0;

    // min-heap: {distance, node}
    priority_queue<pair<int,int>, vector<pair<int,int>>, greater<>> pq;
    pq.push({0, src});

    while (!pq.empty()) {
        auto [d, u] = pq.top(); pq.pop();

        if (d > dist[u]) continue;  // 舊的、過時的距離，跳過

        for (auto [v, w] : adj[u]) {
            if (dist[u] + w < dist[v]) {
                dist[v] = dist[u] + w;
                pq.push({dist[v], v});
            }
        }
    }

    return dist;  // dist[i] = src 到 i 的最短距離，INT_MAX 代表不可達
}
```

**`if (d > dist[u]) continue`**：
因為我們是直接把新的距離 push 進 pq，而不是更新舊的，pq 裡可能有同一個節點的多個過時距離。遇到過時的直接跳過。

## 範例：Network Delay Time（LeetCode 743）

**題目**：有向有權圖，從節點 k 發出訊號，問多少時間後所有節點都收到？（即從 k 出發的最大最短距離）

```cpp
int networkDelayTime(vector<vector<int>>& times, int n, int k) {
    vector<vector<pair<int,int>>> adj(n + 1);
    for (auto& t : times)
        adj[t[0]].push_back({t[1], t[2]});

    vector<int> dist = dijkstra(n + 1, adj, k);

    int ans = 0;
    for (int i = 1; i <= n; i++) {
        if (dist[i] == INT_MAX) return -1;
        ans = max(ans, dist[i]);
    }
    return ans;
}
```

## 複雜度

- 時間：O((V + E) log V)（每條邊最多推入 pq 一次，pq 操作 O(log V)）
- 空間：O(V + E)

## Dijkstra 的變形

**點到點的最短路徑**：找到目標節點就可以提前返回：

```cpp
if (u == target) return dist[target];
```

**路徑重建**：用 `prev[v] = u` 記錄前驅，最後回溯。

## 與其他最短路徑算法比較

| 算法 | 時間複雜度 | 適用場景 |
|---|---|---|
| BFS | O(V+E) | 無權圖（邊權重 = 1） |
| Dijkstra | O((V+E) log V) | 非負權重圖 |
| Bellman-Ford | O(VE) | 有負權重（面試少見） |
| Floyd-Warshall | O(V³) | 所有點對最短路徑（V 很小時） |

面試 95% 的最短路徑題用 BFS（無權）或 Dijkstra（有正權重）就夠了。

## 自我檢核

- [ ] 能解釋為什麼 Dijkstra 需要「非負權重」的前提
- [ ] 能解釋 `if (d > dist[u]) continue` 這行的作用
- [ ] 能從頭寫出 Dijkstra（包含 min-heap 的宣告）
- [ ] 知道 `priority_queue` 預設是 max-heap，Dijkstra 要用 min-heap

→ [練習 D：Graph 綜合](./practice-d-graph.md)
