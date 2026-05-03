# Ch 28 — 子序列 DP：LIS / LCS

> 目標：掌握兩道子序列經典題的 DP 設計，並學會 LIS 的 O(N log N) 最佳化。

## 什麼是子序列？

子序列（Subsequence）：從原序列中選取若干元素，保持相對順序，但不需要連續。

- 原序列：`[3, 1, 4, 1, 5, 9]`
- 合法子序列：`[3, 4, 5]`、`[1, 1, 9]`
- 不合法：`[9, 5]`（順序反了）

子序列 ≠ 子陣列（子陣列必須連續）。

## Longest Increasing Subsequence（LIS，LeetCode 300）

**題目**：找最長的嚴格遞增子序列的長度。

### O(N²) DP

```
狀態：dp[i] = 以 nums[i] 結尾的最長遞增子序列長度
轉移：dp[i] = max(dp[j] + 1) for all j < i where nums[j] < nums[i]
Base：dp[i] = 1（至少包含自己）
答案：max(dp[0..n-1])
```

```cpp
int lengthOfLIS(vector<int>& nums) {
    int n = nums.size();
    vector<int> dp(n, 1);
    int ans = 1;

    for (int i = 1; i < n; i++) {
        for (int j = 0; j < i; j++) {
            if (nums[j] < nums[i])
                dp[i] = max(dp[i], dp[j] + 1);
        }
        ans = max(ans, dp[i]);
    }
    return ans;
}
```

時間 O(N²)，n 到 2500 沒問題，到 10⁵ 就要用 O(N log N)。

### O(N log N)：Patience Sorting

維護一個陣列 `tails`，`tails[i]` 是長度為 i+1 的遞增子序列的「最小尾元素」。

```
nums = [10, 9, 2, 5, 3, 7, 101, 18]

tails = []
10 → [10]
9  → [9]        （替換 10，9 更有潛力）
2  → [2]        （替換 9）
5  → [2, 5]     （5 > 2，延伸）
3  → [2, 3]     （替換 5）
7  → [2, 3, 7]  （延伸）
101→ [2, 3, 7, 101]（延伸）
18 → [2, 3, 7, 18] （替換 101）

LIS 長度 = tails.size() = 4
```

對每個新元素，用 `lower_bound` 找它在 `tails` 中的插入位置（O(log N)）：
- 若比所有 tails 都大 → push_back（LIS 延伸）
- 否則 → 替換第一個 ≥ 它的位置（保持最小尾元素）

```cpp
int lengthOfLIS(vector<int>& nums) {
    vector<int> tails;
    for (int x : nums) {
        auto it = lower_bound(tails.begin(), tails.end(), x);
        if (it == tails.end()) tails.push_back(x);
        else *it = x;
    }
    return tails.size();
}
```

注意：`tails` 本身不是 LIS，只是長度正確。

## Longest Common Subsequence（LCS，LeetCode 1143）

**題目**：兩個字串 `text1` 和 `text2`，找最長公共子序列的長度。

### 狀態設計

```
dp[i][j] = text1[0..i-1] 和 text2[0..j-1] 的 LCS 長度
```

轉移：
```
若 text1[i-1] == text2[j-1]：dp[i][j] = dp[i-1][j-1] + 1（最後字元相同，LCS 延伸）
否則：dp[i][j] = max(dp[i-1][j], dp[i][j-1])（不用其中一個字元，取最大）
```

```cpp
int longestCommonSubsequence(string text1, string text2) {
    int m = text1.size(), n = text2.size();
    vector<vector<int>> dp(m+1, vector<int>(n+1, 0));

    for (int i = 1; i <= m; i++) {
        for (int j = 1; j <= n; j++) {
            if (text1[i-1] == text2[j-1])
                dp[i][j] = dp[i-1][j-1] + 1;
            else
                dp[i][j] = max(dp[i-1][j], dp[i][j-1]);
        }
    }
    return dp[m][n];
}
```

LCS 是很多字串問題的基礎：Shortest Common Supersequence、Edit Distance 都和 LCS 有關。

## 子序列 DP 的通用框架

```
dp[i] = 以第 i 個元素「結尾」的最佳子序列
dp[i][j] = 前 i 個元素和前 j 個元素的最佳匹配
```

幾乎所有子序列 DP 都是這兩種之一。

## 自我檢核

- [ ] 能寫出 LIS 的 O(N²) DP（含狀態定義）
- [ ] 能解釋 O(N log N) Patience Sorting 的 `tails` 陣列含義
- [ ] 能寫出 LCS 的 DP（含 `text1[i-1]==text2[j-1]` 時的轉移）
- [ ] 知道子序列和子陣列的差異

→ [Ch 29 區間 DP](./29-dp-interval.md)
