# Ch 22 — Union-Find（並查集）：路徑壓縮 + 按秩合併

> 目標：理解 Union-Find 的結構與兩種最佳化，能用它解連通性和動態合併問題。

## 什麼是 Union-Find？

Union-Find（並查集）管理一組元素的分組，支援兩種操作：

- `find(x)`：找 x 所在的組的代表元素（根）
- `union(x, y)`：把 x 和 y 的組合併

核心應用：**動態地判斷兩個元素是否在同一個連通分量**。

## 基本結構

每個元素有一個 `parent`。一開始每個元素的父節點是自己（各自獨立）。

`find(x)` 沿著 parent 往上走，直到找到根（`parent[x] == x`）。

`union(x, y)` 找到兩個根，把一個根指向另一個。

```cpp
vector<int> parent;

void init(int n) {
    parent.resize(n);
    iota(parent.begin(), parent.end(), 0);  // parent[i] = i
}

int find(int x) {
    if (parent[x] != x) return find(parent[x]);  // 遞迴找根
    return x;
}

void unite(int x, int y) {
    parent[find(x)] = find(y);
}

bool connected(int x, int y) {
    return find(x) == find(y);
}
```

這個基本版本，最壞情況下 `find` 是 O(N)（退化成鏈狀）。

## 最佳化 1：路徑壓縮（Path Compression）

`find` 時，把路徑上所有節點直接指向根，下次查詢就快了。

```cpp
int find(int x) {
    if (parent[x] != x)
        parent[x] = find(parent[x]);  // 路徑壓縮：把 parent 直接設為根
    return parent[x];
}
```

一行差異，但效果巨大：之後同一路徑的查詢都是 O(1)。

## 最佳化 2：按秩合併（Union by Rank）

合併時，把「矮的樹」接到「高的樹」下面，避免樹高度增加。

```cpp
vector<int> parent, rank;

void init(int n) {
    parent.resize(n);
    rank.assign(n, 0);
    iota(parent.begin(), parent.end(), 0);
}

void unite(int x, int y) {
    int rx = find(x), ry = find(y);
    if (rx == ry) return;
    if (rank[rx] < rank[ry]) swap(rx, ry);
    parent[ry] = rx;
    if (rank[rx] == rank[ry]) rank[rx]++;
}
```

## 完整模板（路徑壓縮 + 按秩合併）

```cpp
struct UnionFind {
    vector<int> parent, rank;
    int components;

    UnionFind(int n) : parent(n), rank(n, 0), components(n) {
        iota(parent.begin(), parent.end(), 0);
    }

    int find(int x) {
        if (parent[x] != x) parent[x] = find(parent[x]);
        return parent[x];
    }

    bool unite(int x, int y) {
        int rx = find(x), ry = find(y);
        if (rx == ry) return false;  // 已在同一組
        if (rank[rx] < rank[ry]) swap(rx, ry);
        parent[ry] = rx;
        if (rank[rx] == rank[ry]) rank[rx]++;
        components--;
        return true;
    }

    bool connected(int x, int y) { return find(x) == find(y); }
};
```

兩種最佳化合用，均攤時間複雜度接近 O(α(N))，其中 α 是反 Ackermann 函數，實際上可視為常數。

## 應用：Number of Provinces（另一種解法）

Ch 20 用 DFS 解，這裡用 Union-Find：

```cpp
int findCircleNum(vector<vector<int>>& isConnected) {
    int n = isConnected.size();
    UnionFind uf(n);
    for (int i = 0; i < n; i++)
        for (int j = i+1; j < n; j++)
            if (isConnected[i][j]) uf.unite(i, j);
    return uf.components;
}
```

## 應用：Redundant Connection（LeetCode 684）

找無向圖中加入哪條邊產生了環（回傳這條多餘的邊）。

按順序加邊，第一條「兩端已在同一連通分量」的邊就是答案。

```cpp
vector<int> findRedundantConnection(vector<vector<int>>& edges) {
    int n = edges.size();
    UnionFind uf(n + 1);  // 節點編號從 1 開始
    for (auto& e : edges) {
        if (!uf.unite(e[0], e[1])) return e;  // unite 失敗 = 已連通 = 有環
    }
    return {};
}
```

## Union-Find vs BFS/DFS

| 場景 | Union-Find | BFS/DFS |
|---|---|---|
| 靜態連通性查詢 | 兩者都可以 | 兩者都可以 |
| **動態加邊**後查詢連通性 | Union-Find（更快） | 不適合（每次重新搜尋）|
| 找具體路徑 | 不行 | BFS/DFS |

Union-Find 的核心優勢：**動態合併**，邊加進來時能即時更新連通性。

## 自我檢核

- [ ] 能從頭寫出路徑壓縮版的 `find`
- [ ] 能解釋按秩合併為什麼能控制樹高
- [ ] 能用 Union-Find 找多餘邊（Redundant Connection）
- [ ] 知道 Union-Find 和 BFS/DFS 的使用場景差異

→ [Ch 23 最短路徑：Dijkstra](./23-dijkstra.md)
