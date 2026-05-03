# Ch 21 — Topological Sort（Kahn + DFS 兩種）

> 目標：理解拓撲排序的原理，掌握 Kahn 算法（BFS）和 DFS 兩種實作，並能識別何時需要拓撲排序。

## 什麼是拓撲排序？

有向無環圖（DAG）的拓撲排序：把所有節點排成一個線性順序，使得每條邊 u→v，u 都在 v 之前。

直覺：課程的修課順序。要修課 A 才能修課 B，A 就必須排在 B 之前。

**重要**：只有 DAG（無環圖）才有拓撲排序。有環圖沒有。

## 方法一：Kahn 算法（BFS，推薦）

思路：
1. 計算每個節點的入度（in-degree，有幾條邊指向它）
2. 把所有入度為 0 的節點加入 queue（沒有前置需求）
3. 每次從 queue 取出一個節點，把它的所有鄰居的入度減 1
4. 若鄰居的入度變為 0，加入 queue
5. 最後輸出的節點總數 == n，則排序成功；否則有環

```cpp
vector<int> topoSort(int n, vector<vector<int>>& adj) {
    vector<int> indegree(n, 0);
    for (int u = 0; u < n; u++)
        for (int v : adj[u])
            indegree[v]++;

    queue<int> q;
    for (int i = 0; i < n; i++)
        if (indegree[i] == 0) q.push(i);

    vector<int> order;
    while (!q.empty()) {
        int u = q.front(); q.pop();
        order.push_back(u);
        for (int v : adj[u]) {
            if (--indegree[v] == 0)
                q.push(v);
        }
    }

    return order.size() == n ? order : vector<int>{};  // 有環回傳空
}
```

## 方法二：DFS（後序收集）

DFS 完成後，以完成時間的**逆序**就是拓撲排序。

用 Ch 20 的三色標記，當節點被標為黑色（處理完畢）時加入 result。最後 reverse。

```cpp
vector<int> topoSortDFS(int n, vector<vector<int>>& adj) {
    vector<int> color(n, 0);
    vector<int> order;
    bool hasCycle = false;

    function<void(int)> dfs = [&](int u) {
        if (hasCycle) return;
        color[u] = 1;
        for (int v : adj[u]) {
            if (color[v] == 1) { hasCycle = true; return; }
            if (color[v] == 0) dfs(v);
        }
        color[u] = 2;
        order.push_back(u);  // 後序：處理完才加入
    };

    for (int i = 0; i < n; i++)
        if (color[i] == 0) dfs(i);

    if (hasCycle) return {};
    reverse(order.begin(), order.end());
    return order;
}
```

## 應用：Course Schedule II（LeetCode 210）

找修完所有課的順序（即拓撲排序）。直接套 Kahn 算法。

```cpp
vector<int> findOrder(int numCourses, vector<vector<int>>& prerequisites) {
    vector<vector<int>> adj(numCourses);
    vector<int> indegree(numCourses, 0);

    for (auto& p : prerequisites) {
        adj[p[1]].push_back(p[0]);  // p[1] 是 p[0] 的前置
        indegree[p[0]]++;
    }

    queue<int> q;
    for (int i = 0; i < numCourses; i++)
        if (indegree[i] == 0) q.push(i);

    vector<int> order;
    while (!q.empty()) {
        int u = q.front(); q.pop();
        order.push_back(u);
        for (int v : adj[u])
            if (--indegree[v] == 0) q.push(v);
    }

    return order.size() == numCourses ? order : vector<int>{};
}
```

## Kahn vs DFS

| | Kahn（BFS） | DFS |
|---|---|---|
| 實作難度 | 稍複雜（入度計算） | 稍簡單（但要 reverse） |
| 偵測環 | 看 `order.size() == n` | 三色標記 |
| 面試偏好 | 較常見，更直覺 | 也可以，但解釋要清楚 |

建議記 Kahn，更好解釋給面試官聽。

## 自我檢核

- [ ] 能解釋 Kahn 算法的步驟（入度、queue、減入度）
- [ ] 能說出「Kahn 算法中，如果最後 order.size() < n 代表什麼」
- [ ] 能從頭寫出 Course Schedule II
- [ ] 知道為什麼 DFS 版本要 reverse

→ [Ch 22 Union-Find（並查集）：路徑壓縮 + 按秩合併](./22-union-find.md)
