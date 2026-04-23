# Ch 21 — DP 三:經典題型

> 目標:LIS、LCS、Knapsack、股票系列、打家劫舍——這些是 DP 面試題的「必考族」,要能閉著眼寫。

## LIS:最長遞增子序列

### O(n²) DP(Ch 19 已寫)

```python
def lis_n2(arr):
    if not arr: return 0
    dp = [1] * len(arr)
    for i in range(1, len(arr)):
        for j in range(i):
            if arr[j] < arr[i]:
                dp[i] = max(dp[i], dp[j] + 1)
    return max(dp)
```

### O(n log n)(patience sorting)

```python
import bisect

def lis(arr):
    tails = []    # tails[k] = 長度 k+1 的 LIS 中,最小的結尾
    for x in arr:
        i = bisect.bisect_left(tails, x)
        if i == len(tails):
            tails.append(x)
        else:
            tails[i] = x
    return len(tails)
```

**tails 不是真的 LIS**,只是各長度 LIS 的最小尾巴。但 `len(tails)` 是正確答案。

**要非嚴格遞增**(`<=` 而非 `<`),用 `bisect_right`。

### 變形

- **Longest Non-decreasing**: `bisect_right`
- **Longest Decreasing**: 取負或反向
- **Number of LIS (673)**: 需要額外 count array,O(n²)
- **Russian Doll Envelopes (354)**: 2D LIS。先按 width 升序,同 width 則 height 降序,然後對 heights 做 LIS

---

## LCS 家族

### Longest Common Subsequence (1143)

Ch 19 已寫。

### 最長重複子陣列(不是子序列,連續)

```python
# dp[i][j] = 以 A[i-1] 和 B[j-1] 結尾的最長連續共同
# dp[i][j] = dp[i-1][j-1] + 1 if A[i-1] == B[j-1] else 0

def find_length(A, B):
    m, n = len(A), len(B)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    best = 0
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if A[i-1] == B[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
                best = max(best, dp[i][j])
    return best
```

**對比 LCS**:LCS 的轉移有 `max(dp[i-1][j], dp[i][j-1])` 的選項,允許跳過;這題要求「連續」,不能跳過,不相等就歸零。

### Edit Distance (Ch 19 已寫)

---

## Knapsack 家族

### 0/1 Knapsack(Ch 19 已寫)

### 完全背包(每物品無限次)

一維壓縮時 `w` **從小到大**:

```python
def unbounded_knapsack(weights, values, W):
    dp = [0] * (W + 1)
    for i in range(len(weights)):
        for w in range(weights[i], W + 1):    # 從小到大
            dp[w] = max(dp[w], dp[w - weights[i]] + values[i])
    return dp[W]
```

### Coin Change (322)

> 最少硬幣湊出 amount。

```python
def coin_change(coins, amount):
    dp = [float('inf')] * (amount + 1)
    dp[0] = 0
    for a in range(1, amount + 1):
        for c in coins:
            if c <= a:
                dp[a] = min(dp[a], dp[a - c] + 1)
    return dp[amount] if dp[amount] != float('inf') else -1
```

**注意**:外層 amount、內層 coins,順序不影響「最少枚數」答案。

### Coin Change II (518,組合數)

> 有幾種湊法。

```python
def change(amount, coins):
    dp = [0] * (amount + 1)
    dp[0] = 1
    for c in coins:                      # 外層 coins!
        for a in range(c, amount + 1):   # 內層 amount
            dp[a] += dp[a - c]
    return dp[amount]
```

**順序對這題有致命影響**。
- 外層 coins、內層 amount → **組合數**(不同順序不算新方案)
- 外層 amount、內層 coins → **排列數**(不同順序算不同)

**這個差別要背下來**。

### Partition Equal Subset Sum (416)

> 能不能把 array 分成兩組 sum 相等。

轉化為:能不能選若干元素 sum = `total / 2`。01 背包 + 布林 DP。

```python
def can_partition(nums):
    s = sum(nums)
    if s % 2: return False
    target = s // 2
    dp = [False] * (target + 1)
    dp[0] = True
    for x in nums:
        for w in range(target, x - 1, -1):    # 01 背包:從大到小
            dp[w] = dp[w] or dp[w - x]
    return dp[target]
```

---

## 股票買賣系列

LeetCode 121, 122, 123, 188, 309, 714——**同一套狀態機**。

### 通用 state

`dp[i][k][s]` = 第 i 天,剩餘交易次數 k,狀態 s(0 = 不持股,1 = 持股)時的最大利潤。

轉移:

```
dp[i][k][0] = max(dp[i-1][k][0], dp[i-1][k][1] + prices[i])   # 休息 or 賣
dp[i][k][1] = max(dp[i-1][k][1], dp[i-1][k-1][0] - prices[i]) # 休息 or 買(買時消耗一次交易次數)
```

**交易次數的記法**:買時 k-1(有人寫賣時 k-1,結果一樣,用哪個自己一致)。

### Best Time to Buy and Sell Stock (121, k=1)

```python
def max_profit_1(prices):
    min_price = float('inf')
    profit = 0
    for p in prices:
        min_price = min(min_price, p)
        profit = max(profit, p - min_price)
    return profit
```

### Best Time ... II (122, k=∞)

k 無限等價於每天都可以買賣,貪婪即可:

```python
def max_profit_inf(prices):
    return sum(max(0, prices[i] - prices[i-1]) for i in range(1, len(prices)))
```

### Best Time ... III (123, k=2)

```python
# 展開:dp[i][k][s],k=0,1,2
# 直接四個變數:buy1, sell1, buy2, sell2

def max_profit_2(prices):
    buy1 = buy2 = float('-inf')
    sell1 = sell2 = 0
    for p in prices:
        buy1 = max(buy1, -p)
        sell1 = max(sell1, buy1 + p)
        buy2 = max(buy2, sell1 - p)
        sell2 = max(sell2, buy2 + p)
    return sell2
```

這個 4 變數寫法優雅得要命。看懂就秒殺這系列。

### Best Time ... IV (188, k 一般)

```python
def max_profit_k(k, prices):
    if not prices: return 0
    n = len(prices)
    if k >= n // 2:    # 視為無限
        return sum(max(0, prices[i]-prices[i-1]) for i in range(1, n))
    dp = [[[0, 0] for _ in range(k + 1)] for _ in range(n)]
    for j in range(k + 1):
        dp[0][j][1] = -prices[0]
    for i in range(1, n):
        for j in range(1, k + 1):
            dp[i][j][0] = max(dp[i-1][j][0], dp[i-1][j][1] + prices[i])
            dp[i][j][1] = max(dp[i-1][j][1], dp[i-1][j-1][0] - prices[i])
    return dp[n-1][k][0]
```

### With Cooldown (309)、Transaction Fee (714)

在轉移式加一天間隔(cooldown 的 `dp[i-2][0]` 代替 `dp[i-1][0]`)或減手續費。套模板即可。

---

## 打家劫舍系列

### House Robber (198,Ch 19 已寫)

### House Robber II (213,環形)

環形:第一家和最後一家相鄰。**分成兩個線性問題**:偷頭不偷尾,或偷尾不偷頭,取 max。

```python
def rob_ii(nums):
    if len(nums) == 1: return nums[0]
    def rob_line(arr):
        prev, curr = 0, 0
        for x in arr:
            prev, curr = curr, max(curr, prev + x)
        return curr
    return max(rob_line(nums[:-1]), rob_line(nums[1:]))
```

### House Robber III(Ch 20 已寫)

---

## 其他經典

### Decode Ways (91)

> "12" 可以解成 "AB"(1, 2)或 "L"(12),問有幾種解法。

一維 DP,類似爬樓梯但要檢查每個單字元和雙字元是否合法。

### Longest Palindromic Substring (5)

**二維 DP**(O(n²) space)或**中心擴展**(O(1) space,更常用)。

```python
def longest_palindrome(s):
    if not s: return ""
    start, end = 0, 0
    for i in range(len(s)):
        l1 = expand(s, i, i)       # 奇長度,中心是 s[i]
        l2 = expand(s, i, i + 1)   # 偶長度,中心在 s[i], s[i+1] 之間
        l = max(l1, l2)
        if l > end - start:
            start = i - (l - 1) // 2
            end = i + l // 2
    return s[start:end+1]

def expand(s, l, r):
    while l >= 0 and r < len(s) and s[l] == s[r]:
        l -= 1; r += 1
    return r - l - 1
```

中心擴展 O(n²) time, O(1) space,比 DP 版省空間。

### Unique Paths II (63)

有障礙物的 Unique Paths。障礙物設 `dp[i][j] = 0`。

### Minimum Path Sum (64)

二維 grid 最小路徑和。典型二維 DP。

---

## 題型辨識表

這張表背熟:

| 題目敘述特徵 | 對應 DP 形狀 |
|---|---|
| 「前 i 個 / 前 n 天」 | 一維 `dp[i]` |
| 「從 (0,0) 到 (m,n)」 | 二維 grid DP |
| 「兩個序列」 | 二維 `dp[i][j]`(LCS、Edit Distance) |
| 「買賣 / 持股狀態」 | 狀態機 DP |
| 「每個物品選或不選」 | 01 背包 |
| 「每個物品選幾個」 | 完全背包 |
| 「切割方案數」 | 一維 `dp[i]`,內層枚舉切點 |
| 「區間合併 / 回文」 | 區間 DP |
| 「選集合的子集」+ n 小 | 狀壓 DP |
| 「tree 上最優化」 | 樹形 DP |

---

## 自我檢核

- [ ] LIS 的 O(n log n) 版,`tails` 的定義是什麼?
- [ ] Coin Change 和 Coin Change II 為什麼迴圈順序不同?
- [ ] 股票買賣 IV 的 state 有幾個維度?各代表什麼?
- [ ] 環形打家劫舍為什麼要拆成兩個線性問題?
- [ ] Longest Palindromic Substring 的中心擴展解為什麼空間 O(1)?

→ [Practice C — DP 專項](./practice-c-dp.md)(先略過)

→ [Ch 22 Bit Manipulation](./22-bit-manipulation.md)
