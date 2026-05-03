# Ch 29 — 區間 DP

> 目標：理解區間 DP「從小區間推大區間」的遍歷模式，能解 Burst Balloons 和回文類問題。

## 區間 DP 的特徵

問題中存在「區間 [i, j] 的最佳解」這樣的子問題，且大區間依賴小區間的結果。

**關鍵遍歷順序**：先算小區間，再算大區間。

通常按**區間長度**從小到大遍歷：

```cpp
for (int len = 2; len <= n; len++) {       // 區間長度
    for (int i = 0; i + len - 1 < n; i++) { // 起點
        int j = i + len - 1;               // 終點
        // 計算 dp[i][j]
    }
}
```

## 題目 1：Minimum Cost to Merge Stones（石子合併）

**簡化版**：一排石子，每次合併相鄰的兩堆，代價是兩堆的總重量。求最小總代價。

```
dp[i][j] = 合併 i..j 的最小代價
轉移：dp[i][j] = min(dp[i][k] + dp[k+1][j]) + sum(i..j) for all k in [i, j-1]
Base：dp[i][i] = 0（單堆不需要合併）
```

```cpp
int minCost(vector<int>& stones) {
    int n = stones.size();
    // 前綴和
    vector<int> prefix(n+1, 0);
    for (int i = 0; i < n; i++) prefix[i+1] = prefix[i] + stones[i];

    vector<vector<int>> dp(n, vector<int>(n, 0));

    for (int len = 2; len <= n; len++) {
        for (int i = 0; i + len - 1 < n; i++) {
            int j = i + len - 1;
            dp[i][j] = INT_MAX;
            for (int k = i; k < j; k++) {
                dp[i][j] = min(dp[i][j], dp[i][k] + dp[k+1][j]);
            }
            dp[i][j] += prefix[j+1] - prefix[i];  // 加上合併這段的代價
        }
    }
    return dp[0][n-1];
}
```

## 題目 2：Burst Balloons（LeetCode 312）

**題目**：n 個氣球，第 i 個的數字是 `nums[i]`。戳破第 i 個氣球得 `nums[i-1] * nums[i] * nums[i+1]`（相鄰氣球），求最大金幣數。

**關鍵轉化**（難點在這）：

不要想「先戳哪個」，而是想「**最後**戳哪個」。

若 `dp[i][j]` = 戳破 `(i, j)` 之間所有氣球的最大金幣（不含 i 和 j），最後戳破的那個 k 的金幣是：

`nums[i] * nums[k] * nums[j]`（因為 k 是最後一個，左邊是 i，右邊是 j）

```cpp
int maxCoins(vector<int>& nums) {
    int n = nums.size();
    // 在兩端加 1 作為哨兵
    nums.insert(nums.begin(), 1);
    nums.push_back(1);
    int m = nums.size();

    vector<vector<int>> dp(m, vector<int>(m, 0));

    for (int len = 2; len < m; len++) {
        for (int i = 0; i + len < m; i++) {
            int j = i + len;
            for (int k = i+1; k < j; k++) {  // k 是最後被戳的
                dp[i][j] = max(dp[i][j],
                    dp[i][k] + nums[i]*nums[k]*nums[j] + dp[k][j]);
            }
        }
    }
    return dp[0][m-1];
}
```

**為什麼「最後戳」而非「最先戳」？**

「最先戳」時，戳完後相鄰關係改變了，難以描述子問題。「最後戳」時，k 被戳的時候左右鄰居固定是 i 和 j，子問題獨立。

## 題目 3：Palindrome Partitioning II（LeetCode 132）

**題目**：把字串分割成若干回文子串，求最少分割次數。

先用 `isPalin[i][j]` 預處理哪些子串是回文（區間 DP）：

```cpp
// 預處理回文
vector<vector<bool>> isPalin(n, vector<bool>(n, false));
for (int len = 1; len <= n; len++) {
    for (int i = 0; i + len - 1 < n; i++) {
        int j = i + len - 1;
        if (s[i] == s[j] && (len <= 2 || isPalin[i+1][j-1]))
            isPalin[i][j] = true;
    }
}
```

再用一維 DP 求最少分割：`dp[i]` = `s[0..i]` 最少分割次數。

## 自我檢核

- [ ] 能說出區間 DP 的遍歷模式（先長度後起點）
- [ ] 能解釋 Burst Balloons 為什麼要「想最後戳哪個」
- [ ] 能手推石子合併的 dp 表（3 個石子的情況）
- [ ] 知道區間 DP 和一般二維 DP 的遍歷順序差異

→ [Ch 30 DP 空間優化（滾動陣列）](./30-dp-space-optimization.md)
