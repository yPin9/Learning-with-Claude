# 練習 E — DP 綜合（簡單 → 中等 → 困難）

> 目標：用三道難度遞增的 DP 題，練習「定義狀態 → 找轉移 → 確認 base case」的完整流程。

**寫完再看！**

---

## 題目一（簡單）：Coin Change（LeetCode 322）

**題目規格**

給硬幣面值 `coins[]` 和目標金額 `amount`，每種硬幣可無限使用。求湊出 `amount` 的最少硬幣數，若不可能回傳 -1。

**期望輸出**

```
coins=[1,5,6], amount=11 → 2（5+6）
coins=[2], amount=3 → -1
coins=[1], amount=0 → 0
```

**實作步驟**

**Step 1**：這是**完全背包**（每種可以用多次）。

**Step 2**：狀態定義：`dp[j]` = 湊出金額 j 所需的最少硬幣數。

**Step 3**：轉移：`dp[j] = min(dp[j], dp[j - coin] + 1)`，對每個 coin 遍歷。

**Step 4**：初始化 `dp[0] = 0`，其他為 `INT_MAX`（代表暫時湊不到）。

**Step 5**：完全背包：內層迴圈從左往右（允許重複使用）。

---

## 題目二（中等）：Unique Paths with Obstacles（LeetCode 63）

**題目規格**

m×n 的網格，`obstacleGrid[i][j] = 1` 是障礙物，只能往右或往下走，找從左上到右下的路徑數。

**期望輸出**

```
obstacleGrid=[[0,0,0],[0,1,0],[0,0,0]] → 2
obstacleGrid=[[0,1],[0,0]] → 1
obstacleGrid=[[1]] → 0（起點是障礙物）
```

**實作步驟**

**Step 1**：和 Unique Paths 一樣的 DP，但障礙物的格子 `dp[i][j] = 0`。

**Step 2**：第一行和第一列的初始化：遇到障礙物後，後面所有格子都是 0（走不過去）。

**Step 3**：起點是障礙物時直接回傳 0。

---

## 題目三（困難）：Longest Increasing Path in a Matrix（LeetCode 329）

**題目規格**

給一個 m×n 的矩陣，可以向四個方向移動，只能往數字更大的格子走，找最長路徑的長度。

**期望輸出**

```
matrix=[[9,9,4],[6,6,8],[2,1,1]] → 4（1→2→6→9）
matrix=[[3,4,5],[3,2,6],[2,2,1]] → 4（3→4→5→6）
```

**實作步驟**

**Step 1**：這是「DAG 上的最長路徑」問題（只能往更大走，所以沒有環，是 DAG）。

**Step 2**：用記憶化 DFS（Top-Down DP）：`dp[i][j]` = 從 `(i, j)` 出發的最長路徑長度。

**Step 3**：遞迴：對四個方向，若鄰居比當前格子大，`dp[i][j] = max(dp[i][j], 1 + dfs(neighbor))`。

**Step 4**：每個格子只算一次，整體 O(MN)。

---

## 完整參考解答

**寫完再看！**

<details>
<summary>點開參考實作</summary>

```cpp
// 題目一：Coin Change（完全背包）
int coinChange(vector<int>& coins, int amount) {
    vector<int> dp(amount+1, INT_MAX);
    dp[0] = 0;
    for (int j = 1; j <= amount; j++) {
        for (int coin : coins) {
            if (coin <= j && dp[j-coin] != INT_MAX)
                dp[j] = min(dp[j], dp[j-coin] + 1);
        }
    }
    return dp[amount] == INT_MAX ? -1 : dp[amount];
}
// 注意：完全背包「外層金額、內層硬幣」或「外層硬幣、內層金額從左到右」都可以。
// 和 0/1 背包（外層物品、內層從右到左）不同。

// 題目二：Unique Paths with Obstacles
int uniquePathsWithObstacles(vector<vector<int>>& grid) {
    int m = grid.size(), n = grid[0].size();
    if (grid[0][0] == 1 || grid[m-1][n-1] == 1) return 0;

    vector<vector<long long>> dp(m, vector<long long>(n, 0));
    dp[0][0] = 1;

    for (int j = 1; j < n; j++) dp[0][j] = grid[0][j] ? 0 : dp[0][j-1];
    for (int i = 1; i < m; i++) dp[i][0] = grid[i][0] ? 0 : dp[i-1][0];

    for (int i = 1; i < m; i++)
        for (int j = 1; j < n; j++)
            dp[i][j] = grid[i][j] ? 0 : dp[i-1][j] + dp[i][j-1];

    return dp[m-1][n-1];
}

// 題目三：Longest Increasing Path in a Matrix
int longestIncreasingPath(vector<vector<int>>& matrix) {
    int m = matrix.size(), n = matrix[0].size();
    vector<vector<int>> memo(m, vector<int>(n, 0));
    vector<vector<int>> dirs = {{0,1},{0,-1},{1,0},{-1,0}};

    function<int(int,int)> dfs = [&](int r, int c) -> int {
        if (memo[r][c]) return memo[r][c];
        memo[r][c] = 1;
        for (auto& d : dirs) {
            int nr = r+d[0], nc = c+d[1];
            if (nr>=0&&nr<m&&nc>=0&&nc<n&&matrix[nr][nc]>matrix[r][c])
                memo[r][c] = max(memo[r][c], 1 + dfs(nr, nc));
        }
        return memo[r][c];
    };

    int ans = 0;
    for (int i = 0; i < m; i++)
        for (int j = 0; j < n; j++)
            ans = max(ans, dfs(i, j));
    return ans;
}
```

</details>

---

## 測試用例

```cpp
// Coin Change
assert(coinChange({1,5,6}, 11) == 2);
assert(coinChange({2}, 3) == -1);

// Unique Paths with Obstacles
assert(uniquePathsWithObstacles({{0,0,0},{0,1,0},{0,0,0}}) == 2);
assert(uniquePathsWithObstacles({{1}}) == 0);

// Longest Increasing Path
assert(longestIncreasingPath({{9,9,4},{6,6,8},{2,1,1}}) == 4);
```

## 自我檢核

- [ ] Coin Change：能說出完全背包和 0/1 背包的遍歷差異
- [ ] Obstacles：能說出第一行 / 列遇到障礙物後如何初始化
- [ ] Longest Increasing Path：能說出為什麼用記憶化 DFS（而不是普通 BFS）
- [ ] 三題能說出時間和空間複雜度

→ [Ch 31 Greedy 思維：何時能用？反例在哪裡？](./31-greedy-thinking.md)
