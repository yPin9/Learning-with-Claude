# 練習 D — Graph 綜合

> 目標：綜合運用 BFS、DFS、Topological Sort、Union-Find，能識別哪道題用哪種工具。

**寫完再看！**

---

## 題目一：Word Ladder（LeetCode 127）

**題目規格**

給 `beginWord`、`endWord` 和字典 `wordList`。每次只能改變一個字母，且改變後的字必須在字典中。找最短轉換序列的長度（含首尾）。不存在回傳 0。

**期望輸出**

```
beginWord="hit", endWord="cog", wordList=["hot","dot","dog","lot","log","cog"]
→ 5（hit → hot → dot → dog → cog）

beginWord="hit", endWord="cog", wordList=["hot","dot","dog","lot","log"]
→ 0（cog 不在字典）
```

**實作步驟**

**Step 1**：這是「最短路徑」問題 → BFS。每個單字是節點，能互相轉換的單字之間有邊。

**Step 2**：建立 `unordered_set<string>` 存字典，方便 O(1) 查詢。

**Step 3**：BFS 中，對當前單字的每個字母，嘗試換成 a-z，若新單字在字典中且未訪問，加入 queue。

**Step 4**：回傳層數（步驟數 + 1，因為包含 beginWord）。

---

## 題目二：Course Schedule II（已在 Ch 21 見過，這次從頭寫）

**題目規格**

`n` 門課，`prerequisites[i] = [a, b]` 代表修 a 前必須先修 b。找一個修課順序，若有環回傳空陣列。

**期望輸出**

```
n=4, prerequisites=[[1,0],[2,0],[3,1],[3,2]] → [0,1,2,3] 或 [0,2,1,3]
n=2, prerequisites=[[1,0],[0,1]] → []（有環）
```

**實作步驟**

**Step 1**：建鄰接表，計算每個節點的入度。

**Step 2**：把所有入度為 0 的節點加入 queue。

**Step 3**：Kahn 算法：每次取出一個節點，減少其鄰居的入度，若入度變 0 則加入 queue。

**Step 4**：最後判斷輸出的節點數是否等於 n（不等則有環）。

---

## 題目三：Number of Islands II（LeetCode 305）

**題目規格**

m×n 的海洋網格（初始全是水），給一系列操作 `positions[i]`，每次把某格變成陸地。每次操作後回傳島嶼數量。

**期望輸出**

```
m=3, n=3, positions=[[0,0],[0,1],[1,2],[2,1]]
→ [1, 1, 2, 3]
```

**實作步驟**

**Step 1**：這是「動態加邊後查連通性」→ Union-Find。

**Step 2**：把二維座標 `(r, c)` 轉成一維 index：`r * n + c`。

**Step 3**：每次加陸地時，與四個方向的陸地 union。每次 union 成功（合併不同分量），島嶼數 -1；新增陸地時島嶼數 +1。

**Step 4**：維護一個 `isLand[]` 陣列，避免重複添加。

---

## 完整參考解答

**寫完再看！**

<details>
<summary>點開參考實作</summary>

```cpp
// 題目一：Word Ladder
int ladderLength(string beginWord, string endWord, vector<string>& wordList) {
    unordered_set<string> dict(wordList.begin(), wordList.end());
    if (!dict.count(endWord)) return 0;

    queue<string> q;
    q.push(beginWord);
    dict.erase(beginWord);
    int steps = 1;

    while (!q.empty()) {
        int size = q.size();
        for (int i = 0; i < size; i++) {
            string word = q.front(); q.pop();
            if (word == endWord) return steps;
            for (int j = 0; j < word.size(); j++) {
                char orig = word[j];
                for (char c = 'a'; c <= 'z'; c++) {
                    word[j] = c;
                    if (dict.count(word)) {
                        dict.erase(word);
                        q.push(word);
                    }
                }
                word[j] = orig;
            }
        }
        steps++;
    }
    return 0;
}

// 題目二：Course Schedule II（Kahn）
vector<int> findOrder(int n, vector<vector<int>>& prereqs) {
    vector<vector<int>> adj(n);
    vector<int> indegree(n, 0);
    for (auto& p : prereqs) { adj[p[1]].push_back(p[0]); indegree[p[0]]++; }

    queue<int> q;
    for (int i = 0; i < n; i++) if (indegree[i] == 0) q.push(i);

    vector<int> order;
    while (!q.empty()) {
        int u = q.front(); q.pop();
        order.push_back(u);
        for (int v : adj[u]) if (--indegree[v] == 0) q.push(v);
    }
    return order.size() == n ? order : vector<int>{};
}

// 題目三：Number of Islands II
vector<int> numIslands2(int m, int n, vector<vector<int>>& positions) {
    vector<int> parent(m*n, -1), rank(m*n, 0);
    int count = 0;
    vector<int> dirs = {0,1,0,-1,1,0,-1,0};

    function<int(int)> find = [&](int x) -> int {
        if (parent[x] != x) parent[x] = find(parent[x]);
        return parent[x];
    };
    auto unite = [&](int x, int y) {
        int rx = find(x), ry = find(y);
        if (rx == ry) return;
        if (rank[rx] < rank[ry]) swap(rx, ry);
        parent[ry] = rx;
        if (rank[rx] == rank[ry]) rank[rx]++;
        count--;
    };

    vector<int> result;
    for (auto& pos : positions) {
        int r = pos[0], c = pos[1], id = r*n+c;
        if (parent[id] != -1) { result.push_back(count); continue; }
        parent[id] = id; count++;
        for (int d = 0; d < 8; d += 2) {
            int nr = r+dirs[d], nc = c+dirs[d+1];
            int nid = nr*n+nc;
            if (nr>=0&&nr<m&&nc>=0&&nc<n&&parent[nid]!=-1) unite(id, nid);
        }
        result.push_back(count);
    }
    return result;
}
```

</details>

---

## 自我檢核

- [ ] Word Ladder：能說出為什麼用 BFS（最短路徑），並說出時間複雜度
- [ ] Course Schedule II：能說出 Kahn 算法的五個步驟
- [ ] Number of Islands II：能說出為什麼這題用 Union-Find 比 BFS 更適合
- [ ] 三題都能在 10 分鐘內寫完（練習速度）

→ [Ch 24 DP 核心思維：記憶化遞迴 → 遞推](./24-dp-fundamentals.md)
