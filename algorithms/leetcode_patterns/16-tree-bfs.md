# Ch 16 — BFS on Tree：層序遍歷

> 目標：掌握 BFS 模板，能解決「按層處理節點」的所有變形題。

## BFS vs DFS

DFS（深度優先）：一條路走到底再回頭。
BFS（廣度優先）：一層一層展開。

```
        1
       / \
      2   3
     / \   \
    4   5   6

DFS 前序：1, 2, 4, 5, 3, 6
BFS 層序：1, 2, 3, 4, 5, 6
```

BFS 用 Queue（佇列，FIFO）而不是 Stack。

## 標準 BFS 模板

```cpp
vector<vector<int>> levelOrder(TreeNode* root) {
    vector<vector<int>> result;
    if (!root) return result;

    queue<TreeNode*> q;
    q.push(root);

    while (!q.empty()) {
        int levelSize = q.size();  // 當層的節點數
        vector<int> level;

        for (int i = 0; i < levelSize; i++) {
            TreeNode* node = q.front(); q.pop();
            level.push_back(node->val);

            if (node->left)  q.push(node->left);
            if (node->right) q.push(node->right);
        }
        result.push_back(level);
    }
    return result;
}
```

**關鍵**：`int levelSize = q.size()` 在進入每層之前固定「這層有幾個節點」，這樣才能準確地分層。

## BFS 的常見變形

**1. 從右到左的層序（LeetCode 107）**：結果 reverse 就好：

```cpp
reverse(result.begin(), result.end());
```

**2. 右視圖（LeetCode 199）**：每層最後一個節點：

```cpp
vector<int> rightSideView(TreeNode* root) {
    vector<int> res;
    if (!root) return res;
    queue<TreeNode*> q;
    q.push(root);

    while (!q.empty()) {
        int size = q.size();
        for (int i = 0; i < size; i++) {
            TreeNode* node = q.front(); q.pop();
            if (i == size - 1) res.push_back(node->val);  // 每層最後一個
            if (node->left)  q.push(node->left);
            if (node->right) q.push(node->right);
        }
    }
    return res;
}
```

**3. 樹的最小深度（LeetCode 111）**：第一個葉節點所在的層：

```cpp
int minDepth(TreeNode* root) {
    if (!root) return 0;
    queue<TreeNode*> q;
    q.push(root);
    int depth = 0;

    while (!q.empty()) {
        depth++;
        int size = q.size();
        for (int i = 0; i < size; i++) {
            TreeNode* node = q.front(); q.pop();
            if (!node->left && !node->right) return depth;  // 第一個葉節點
            if (node->left)  q.push(node->left);
            if (node->right) q.push(node->right);
        }
    }
    return depth;
}
```

**BFS 找最短路徑的關鍵**：第一次到達目標就是最短路徑（因為 BFS 是層序展開）。

## 何時用 BFS，何時用 DFS？

| 問題 | 建議 |
|---|---|
| 找最短路徑 / 最短距離 | BFS |
| 按層收集結果 | BFS |
| 深度 / 路徑 / 遞迴關係 | DFS |
| 只需要「存在性」 | DFS 通常更簡單 |

## 常見錯誤

- 沒有在進入 for 迴圈前固定 `levelSize = q.size()`，導致層界混亂
- 忘記 base case `if (!root) return result`（空樹）

## 自我檢核

- [ ] 能默寫 BFS 層序遍歷模板
- [ ] 知道 `int levelSize = q.size()` 為什麼要在 for 迴圈外面
- [ ] 能改寫模板來只取每層第一個 / 最後一個節點
- [ ] 知道 BFS 為什麼能保證找到最短路徑

→ [Ch 17 Recursion on Tree：分治思維](./17-tree-recursion.md)
