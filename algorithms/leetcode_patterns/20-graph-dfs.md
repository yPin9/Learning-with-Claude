# Ch 20 — DFS：連通分量 + Cycle Detection

> 目標：掌握 Graph DFS 模板，能用「三色標記法」偵測有向圖的環。

## Graph DFS 模板

和 Tree DFS 的差異：Graph 可能有環，需要 `visited` 陣列防止無限遞迴。

```cpp
vector<bool> visited;

void dfs(vector<vector<int>>& adj, int node) {
    visited[node] = true;
    for (int neighbor : adj[node]) {
        if (!visited[neighbor])
            dfs(adj, neighbor);
    }
}

// 計算連通分量數量
int countComponents(int n, vector<vector<int>>& edges) {
    vector<vector<int>> adj(n);
    for (auto& e : edges) {
        adj[e[0]].push_back(e[1]);
        adj[e[1]].push_back(e[0]);
    }

    visited.assign(n, false);
    int count = 0;
    for (int i = 0; i < n; i++)
        if (!visited[i]) { dfs(adj, i); count++; }
    return count;
}
```

## 連通分量：Number of Provinces（LeetCode 547）

給 `isConnected[i][j]` 矩陣，求連通分量數。

```cpp
int findCircleNum(vector<vector<int>>& isConnected) {
    int n = isConnected.size();
    vector<bool> visited(n, false);
    int provinces = 0;

    function<void(int)> dfs = [&](int i) {
        visited[i] = true;
        for (int j = 0; j < n; j++)
            if (isConnected[i][j] && !visited[j])
                dfs(j);
    };

    for (int i = 0; i < n; i++)
        if (!visited[i]) { dfs(i); provinces++; }
    return provinces;
}
```

## Cycle Detection：無向圖

無向圖有環的判斷：DFS 時，如果訪問到已訪問的節點（且不是直接父節點），就有環。

```cpp
bool hasCycleUndirected(vector<vector<int>>& adj, int n) {
    vector<bool> visited(n, false);

    function<bool(int, int)> dfs = [&](int node, int parent) -> bool {
        visited[node] = true;
        for (int neighbor : adj[node]) {
            if (!visited[neighbor]) {
                if (dfs(neighbor, node)) return true;
            } else if (neighbor != parent) {  // 非父節點的已訪問鄰居 → 環
                return true;
            }
        }
        return false;
    };

    for (int i = 0; i < n; i++)
        if (!visited[i] && dfs(i, -1)) return true;
    return false;
}
```

## Cycle Detection：有向圖（三色標記）

有向圖更複雜。「已訪問」的節點不一定構成環，只有「當前 DFS 路徑上」的節點才會。

用三種顏色：
- **0（白色）**：未訪問
- **1（灰色）**：在當前 DFS 路徑上（正在處理）
- **2（黑色）**：已完全處理（所有子節點都搜尋完畢）

若 DFS 到一個**灰色**節點 → 有環（找到了後向邊 back edge）。

```cpp
bool hasCycleDirected(vector<vector<int>>& adj, int n) {
    vector<int> color(n, 0);

    function<bool(int)> dfs = [&](int node) -> bool {
        color[node] = 1;  // 標記為灰色（正在處理）
        for (int neighbor : adj[node]) {
            if (color[neighbor] == 1) return true;   // 遇到灰色 → 有環
            if (color[neighbor] == 0 && dfs(neighbor)) return true;
        }
        color[node] = 2;  // 標記為黑色（處理完畢）
        return false;
    };

    for (int i = 0; i < n; i++)
        if (color[i] == 0 && dfs(i)) return true;
    return false;
}
```

**為什麼遇到黑色不算環？**

黑色代表從這個節點出發的所有路徑都已經探索完，且沒有環。如果能到達黑色節點，那條路徑是安全的（一條 cross edge，不是 back edge）。

## 應用：Course Schedule（LeetCode 207）

課程有前置需求，判斷是否能修完所有課（即有向圖是否有環）。

直接套有向圖的三色 cycle detection。

## DFS vs BFS 的選擇

| 問題 | 選擇 |
|---|---|
| 找最短路徑 | BFS |
| 判斷連通性 | DFS 或 BFS，DFS 程式碼更短 |
| 偵測有向圖環 | DFS（三色法）或 Topological Sort |
| 深度探索、回溯 | DFS |

## 自我檢核

- [ ] 能寫出 Graph DFS 的基本模板（含 visited 陣列）
- [ ] 知道無向圖 cycle detection 中為什麼要傳入 parent
- [ ] 能解釋三色標記法中，遇到「灰色」為何代表有環
- [ ] 知道「黑色」和「灰色」的語意差異

→ [Ch 21 Topological Sort（Kahn + DFS 兩種）](./21-topological-sort.md)
