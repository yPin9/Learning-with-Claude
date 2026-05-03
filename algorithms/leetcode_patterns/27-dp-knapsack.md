# Ch 27 — 0/1 背包

> 目標：理解背包問題的狀態設計，掌握標準 0/1 背包的二維和一維（滾動）兩種寫法。

## 背包問題的形式

**標準 0/1 背包**：有 n 個物品，每個物品有重量 `w[i]` 和價值 `v[i]`。背包容量為 W。每個物品只能放 0 或 1 次，求最大價值。

「0/1」的含義：每個物品要麼不拿（0），要麼拿一個（1），不能拆開或多拿。

## 狀態設計

```
dp[i][j] = 考慮前 i 個物品，背包容量為 j 時，能取得的最大價值
```

轉移：對第 i 個物品，選不選它？

```
不放第 i 個：dp[i][j] = dp[i-1][j]（容量不變，不取 i）
放第 i 個：  dp[i][j] = dp[i-1][j-w[i]] + v[i]（容量減少 w[i]，加入 v[i]）

取最大值：dp[i][j] = max(dp[i-1][j], dp[i-1][j-w[i]] + v[i])
（前提：j >= w[i]，裝得下才能放）
```

Base case：`dp[0][j] = 0`（沒有物品，價值為 0）。

## 二維實作

```cpp
int knapsack(vector<int>& w, vector<int>& v, int W) {
    int n = w.size();
    vector<vector<int>> dp(n+1, vector<int>(W+1, 0));

    for (int i = 1; i <= n; i++) {
        for (int j = 0; j <= W; j++) {
            dp[i][j] = dp[i-1][j];  // 不放第 i 個
            if (j >= w[i-1])
                dp[i][j] = max(dp[i][j], dp[i-1][j-w[i-1]] + v[i-1]);
        }
    }
    return dp[n][W];
}
```

## 一維滾動陣列（空間優化）

`dp[i][j]` 只依賴 `dp[i-1][...]`（上一行），可以壓縮成一維。

**關鍵**：內層迴圈必須**從右往左**遍歷，否則同一行的值會被覆蓋，導致同一個物品被重複選取。

```cpp
int knapsack1D(vector<int>& w, vector<int>& v, int W) {
    int n = w.size();
    vector<int> dp(W+1, 0);

    for (int i = 0; i < n; i++) {
        for (int j = W; j >= w[i]; j--) {  // ← 從右往左！
            dp[j] = max(dp[j], dp[j-w[i]] + v[i]);
        }
    }
    return dp[W];
}
```

為什麼從右往左？

更新 `dp[j]` 時，需要用到 `dp[j-w[i]]`（處理第 i 個物品之前的狀態）。
如果從左往右更新，更新 `dp[j]` 後，後面更大的 j 用到 `dp[j-w[i]]` 時，`dp[j-w[i]]` 已被更新（代表已放了物品 i），就會重複放。

## 應用：Partition Equal Subset Sum（LeetCode 416）

**題目**：能否把陣列分成兩個總和相等的子集？

轉化：找是否存在子集，總和等於 `total/2`。

這是背包的特殊形式：每個元素的重量 = 價值 = `nums[i]`，背包容量 = `total/2`，看能否恰好填滿。

```cpp
bool canPartition(vector<int>& nums) {
    int total = accumulate(nums.begin(), nums.end(), 0);
    if (total % 2 != 0) return false;
    int target = total / 2;

    vector<bool> dp(target+1, false);
    dp[0] = true;

    for (int x : nums) {
        for (int j = target; j >= x; j--) {  // 從右往左
            dp[j] = dp[j] || dp[j-x];
        }
    }
    return dp[target];
}
```

`dp[j]` = 是否存在子集總和恰好等於 j。

## 背包問題的三種變形

| 問題類型 | 轉移方式 | 遍歷方向 |
|---|---|---|
| 0/1 背包（每個只能用一次） | `dp[j] = max(dp[j], dp[j-w]+v)` | 內層從右往左 |
| 完全背包（每個可用無限次） | 同上 | 內層**從左往右** |
| 恰好填滿（計數/判斷） | `dp[j] = dp[j] \|\| dp[j-x]` | 視問題而定 |

完全背包從左往右：允許同一個物品被再次選取（因為更新 `dp[j-w]` 後，後面的 `dp[j]` 用到它時，代表「已放了當前物品」，是允許的）。

## 自我檢核

- [ ] 能說出 0/1 背包的狀態定義
- [ ] 能解釋為什麼一維滾動陣列要從右往左遍歷
- [ ] 能把 Partition Equal Subset Sum 轉化成背包問題
- [ ] 知道 0/1 背包和完全背包的遍歷方向差異

→ [Ch 28 子序列 DP：LIS / LCS](./28-dp-subsequence.md)
