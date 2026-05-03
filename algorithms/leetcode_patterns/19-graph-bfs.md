# Ch 19 — Graph 表示法 + BFS

> 目標：建立 Graph 的鄰接表表示，掌握 BFS 模板，能解決「最短步數」和「連通性」問題。

## Graph 的兩種表示法

**鄰接矩陣（Adjacency Matrix）**：`adj[i][j] = 1` 代表有邊 i→j。
- 空間 O(V²)，查詢某條邊 O(1)
- 節點多時浪費空間，LeetCode 題幾乎不用

**鄰接表（Adjacency List）**：`adj[i]` 存所有 i 能到達的節點。
- 空間 O(V+E)，是標準做法

```cpp
// 建立無向圖
int n = 5;
vector<vector<int>> adj(n);

// 加邊
adj[0].push_back(1);
adj[1].push_back(0);  // 無向邊要加兩次
adj[1].push_back(2);
adj[2].push_back(1);
// ...

// 有權重的圖
vector<vector<pair<int,int>>> wadj(n);  // {neighbor, weight}
wadj[0].push_back({1, 5});  // 0→1，權重 5
```

## BFS 模板

BFS 的本質：從起點出發，一層一層展開，保證「第一次到達某節點時，走的步數最少」。

```cpp
int bfs(vector<vector<int>>& adj, int start, int target, int n) {
    vector<bool> visited(n, false);
    queue<int> q;

    visited[start] = true;
    q.push(start);
    int steps = 0;

    while (!q.empty()) {
        int size = q.size();
        for (int i = 0; i < size; i++) {
            int node = q.front(); q.pop();
            if (node == target) return steps;

            for (int neighbor : adj[node]) {
                if (!visited[neighbor]) {
                    visited[neighbor] = true;
                    q.push(neighbor);
                }
            }
        }
        steps++;
    }
    return -1;  // 到不了
}
```

**visited 的時機**：入 queue 時就標記（不要等到 pop 時），否則同一節點可能被加入 queue 多次。

## 範例：Number of Islands（LeetCode 200）

**題目**：`'1'` 是陸地，`'0'` 是水，計算島嶼數量。

對每個未訪問的 `'1'`，用 BFS 把整個島嶼淹沒（標記為已訪問），計數加 1。

```cpp
int numIslands(vector<vector<char>>& grid) {
    int m = grid.size(), n = grid[0].size();
    int count = 0;
    vector<vector<int>> dirs = {{0,1},{0,-1},{1,0},{-1,0}};

    auto bfs = [&](int r, int c) {
        queue<pair<int,int>> q;
        q.push({r, c});
        grid[r][c] = '0';  // 立即標記，防止重複加入

        while (!q.empty()) {
            auto [row, col] = q.front(); q.pop();
            for (auto& d : dirs) {
                int nr = row + d[0], nc = col + d[1];
                if (nr >= 0 && nr < m && nc >= 0 && nc < n && grid[nr][nc] == '1') {
                    grid[nr][nc] = '0';
                    q.push({nr, nc});
                }
            }
        }
    };

    for (int i = 0; i < m; i++)
        for (int j = 0; j < n; j++)
            if (grid[i][j] == '1') { bfs(i, j); count++; }

    return count;
}
```

四方向移動的 `dirs` 陣列是 Grid BFS 的標準做法，記起來。

## 範例：Shortest Path in Binary Matrix（LeetCode 1091）

8 方向，找左上到右下的最短路徑（值為 0 才能走）。

```cpp
int shortestPathBinaryMatrix(vector<vector<int>>& grid) {
    int n = grid.size();
    if (grid[0][0] || grid[n-1][n-1]) return -1;

    queue<tuple<int,int,int>> q;  // (row, col, steps)
    q.push({0, 0, 1});
    grid[0][0] = 1;  // 標記已訪問

    vector<vector<int>> dirs = {{-1,-1},{-1,0},{-1,1},{0,-1},{0,1},{1,-1},{1,0},{1,1}};

    while (!q.empty()) {
        auto [r, c, steps] = q.front(); q.pop();
        if (r == n-1 && c == n-1) return steps;

        for (auto& d : dirs) {
            int nr = r + d[0], nc = c + d[1];
            if (nr >= 0 && nr < n && nc >= 0 && nc < n && grid[nr][nc] == 0) {
                grid[nr][nc] = 1;
                q.push({nr, nc, steps + 1});
            }
        }
    }
    return -1;
}
```

## 自我檢核

- [ ] 能用鄰接表表示一個無向圖
- [ ] 能默寫 BFS 模板（含 visited 標記時機的注意事項）
- [ ] 能用 BFS 解 Number of Islands
- [ ] 知道 4 方向和 8 方向 dirs 陣列怎麼寫

→ [Ch 20 DFS：連通分量 + Cycle Detection](./20-graph-dfs.md)
