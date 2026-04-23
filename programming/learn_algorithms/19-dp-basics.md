# Ch 19 — DP 一:辨識與模板

> 目標:學會辨識「這題是 DP」,掌握一維 / 二維 DP 的核心套路。DP 不是背題,是建模。

## 病:看題就想公式

DP 題最常見的錯學法:看一題、查解答、抄那題的轉移方程。下一題變形一下就不會了。

**DP 是方法論,不是公式集**。要學的是:

1. 辨識這是 DP 題(而不是貪婪 / 回溯)
2. **定義 state**:什麼參數能唯一描述子問題?
3. **寫轉移**:這個 state 怎麼從更小的 state 推出來?
4. **考慮 base case 和 order**

## 辨識 DP 的訊號

### 訊號 1:「最優化」問題

「找最小 / 最大」、「有幾種方式」、「能否達到」。

但貪婪也是最優化,所以還要結合下面。

### 訊號 2:最優子結構 + 重疊子問題

- **最優子結構**:大問題的最優解依賴於子問題的最優解。
- **重疊子問題**:遞迴展開會重複計算相同子問題。

### 訊號 3:「從 n 推 n-1」或「從 (i, j) 推 (i-1, j), (i, j-1) ...」

題目能自然分解成「去掉最後一個元素後的答案」或「前 i 個的答案」。

### 訊號 4:Brute force 是指數,但子問題數是多項式

Backtracking 寫出來是 O(2^n),但子問題只有 O(n) 或 O(n²) 個——加 memo 變 DP。

---

## DP 的五步法

按這個順序想,寫不崩:

### Step 1: 定義 state

用 1–3 個參數描述子問題。寫法:

> `dp[i]` 表示 _____(用自然語言)

### Step 2: 寫轉移方程

> `dp[i] = ...`(用更小的 state 表達)

### Step 3: Base case

最小的 state 是什麼、值是多少。

### Step 4: 迭代順序

從小 state 算到大,確保每個 state 用到的子 state 已經算好。

### Step 5: 提取答案

最終答案在 `dp[某個位置]`,不一定是 `dp[n-1]`。

---

## 實作兩種寫法

### A. Top-down(memoization + 遞迴)

```python
from functools import cache

@cache
def dp(i):
    if i == 0: return BASE
    return f(dp(i - 1), dp(i - 2), ...)

return dp(n)
```

**優點**:思路順,直接把轉移寫成遞迴。
**缺點**:有 stack 深度限制。

### B. Bottom-up(迭代 + table)

```python
dp = [BASE] * (n + 1)
for i in range(1, n + 1):
    dp[i] = f(dp[i - 1], dp[i - 2], ...)
return dp[n]
```

**優點**:沒 stack 問題,常數更小,可做滾動優化。
**缺點**:要自己排迴圈順序。

**面試建議**:先用 `@cache` 快速驗證邏輯對,再改 bottom-up 展示能優化空間。兩種都要會。

---

## 一維 DP 經典題

### Climbing Stairs (70)

> 每次爬 1 或 2 階,到 n 階有幾種方式。

```python
# dp[i] = 到第 i 階的方法數
# dp[i] = dp[i-1] + dp[i-2]
# base: dp[0] = dp[1] = 1

def climb(n):
    if n <= 1: return 1
    a, b = 1, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b
```

**滾動優化**:dp[i] 只用 dp[i-1] 和 dp[i-2],不用整個 array,兩個變數就夠。空間 O(1)。

### House Robber (198)

> 不能偷相鄰,最大偷法總和。

```python
# dp[i] = 偷到第 i 家為止的最大值
# dp[i] = max(dp[i-1], dp[i-2] + nums[i])

def rob(nums):
    prev, curr = 0, 0
    for x in nums:
        prev, curr = curr, max(curr, prev + x)
    return curr
```

### Word Break (139)

> 字串能否被字典裡的詞切開。

```python
# dp[i] = s[:i] 能被切分
# dp[i] = any(dp[j] for j in range(i) if s[j:i] in wordset)

def word_break(s, wordDict):
    wordset = set(wordDict)
    dp = [False] * (len(s) + 1)
    dp[0] = True
    for i in range(1, len(s) + 1):
        for j in range(i):
            if dp[j] and s[j:i] in wordset:
                dp[i] = True
                break
    return dp[-1]
```

### Longest Increasing Subsequence (300)

> 最長遞增子序列。

```python
# dp[i] = 以 arr[i] 結尾的 LIS 長度
# dp[i] = max(dp[j] for j < i if arr[j] < arr[i]) + 1

def length_of_lis(arr):
    if not arr: return 0
    dp = [1] * len(arr)
    for i in range(1, len(arr)):
        for j in range(i):
            if arr[j] < arr[i]:
                dp[i] = max(dp[i], dp[j] + 1)
    return max(dp)
```

O(n²)。Ch 21 會講 O(n log n) 版本(配 binary search)。

---

## 二維 DP 經典題

### Unique Paths (62)

> m × n 格子,從左上到右下,只能右 / 下,多少條路徑?

```python
# dp[i][j] = 到 (i, j) 的路徑數
# dp[i][j] = dp[i-1][j] + dp[i][j-1]

def unique_paths(m, n):
    dp = [[1] * n for _ in range(m)]
    for i in range(1, m):
        for j in range(1, n):
            dp[i][j] = dp[i-1][j] + dp[i][j-1]
    return dp[-1][-1]
```

**滾動優化**:dp[i] 只用 dp[i-1],可以用一維:

```python
def unique_paths(m, n):
    dp = [1] * n
    for i in range(1, m):
        for j in range(1, n):
            dp[j] += dp[j-1]    # dp[j] 是上方,dp[j-1] 是左方
    return dp[-1]
```

### Longest Common Subsequence (1143)

> 兩字串的最長共同子序列(不必連續)。

```python
# dp[i][j] = s1[:i] 和 s2[:j] 的 LCS 長度
# dp[i][j] = dp[i-1][j-1] + 1          if s1[i-1] == s2[j-1]
#          = max(dp[i-1][j], dp[i][j-1]) otherwise

def lcs(s1, s2):
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i-1] == s2[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])
    return dp[m][n]
```

LCS 是二維 DP 的模範題。Edit Distance、Interleaving String 都是同樣 state。

### Edit Distance (72)

> 把 s1 變成 s2 的最少操作(insert / delete / replace)。

```python
# dp[i][j] = s1[:i] 變 s2[:j] 的最少操作
# 若 s1[i-1] == s2[j-1]: dp[i][j] = dp[i-1][j-1]
# 否則: dp[i][j] = 1 + min(dp[i-1][j-1], dp[i-1][j], dp[i][j-1])
#   分別對應:replace, delete s1, insert

def min_distance(s1, s2):
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1): dp[i][0] = i
    for j in range(n + 1): dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i-1] == s2[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = 1 + min(dp[i-1][j-1], dp[i-1][j], dp[i][j-1])
    return dp[m][n]
```

---

## 0/1 Knapsack(01 背包)

> N 個物品,每個有重量 w 和價值 v,背包容量 W,求最大價值。每個物品只能用一次。

```python
# dp[i][w] = 前 i 個物品,容量 w 下的最大價值
# dp[i][w] = max(dp[i-1][w],              # 不拿第 i 個
#                dp[i-1][w - weights[i-1]] + values[i-1])   # 拿第 i 個

def knapsack_01(weights, values, W):
    n = len(weights)
    dp = [[0] * (W + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for w in range(W + 1):
            dp[i][w] = dp[i-1][w]
            if w >= weights[i-1]:
                dp[i][w] = max(dp[i][w], dp[i-1][w - weights[i-1]] + values[i-1])
    return dp[n][W]
```

**滾動優化**(一維):`w` 要**從大到小**,避免同一個物品被用兩次:

```python
def knapsack_01(weights, values, W):
    dp = [0] * (W + 1)
    for i in range(len(weights)):
        for w in range(W, weights[i] - 1, -1):    # 從大到小!
            dp[w] = max(dp[w], dp[w - weights[i]] + values[i])
    return dp[W]
```

**完全背包**(每物品可無限用):同樣一維,但 `w` 從小到大。方向就是關鍵差別。

Ch 21 會再講更多 knapsack 變形。

---

## DP 的空間優化

- 只用到 `dp[i-1]` 和 `dp[i-2]` → 兩個變數
- 只用到前一列 → 一維 array
- 只用到前一行 → 滾動陣列或覆蓋技巧

面試寫完先寫完整版,然後說「可以優化到 O(1) space」,寫給看。

---

## 訊號 → DP 速查

| 訊號 | 可能的 state 定義 |
|---|---|
| 「前 i 個」 | `dp[i]` |
| 「兩個序列的關係」 | `dp[i][j]` |
| 「區間 [i, j]」 | `dp[i][j]`(Ch 20 區間 DP) |
| 「二維網格」 | `dp[i][j]` |
| 「帶狀態 / 模式」 | `dp[i][state]`(如買賣股票的持股狀態) |
| 「位元選擇」 | `dp[mask]`(狀壓 DP,Ch 20) |

---

## 自我檢核

- [ ] 辨識 DP 的四個訊號是什麼?
- [ ] Top-down 和 bottom-up 的優缺點?
- [ ] 01 背包一維壓縮為什麼要從大到小?
- [ ] LCS 的 state 定義?
- [ ] Word Break 的時間複雜度?

→ [Ch 20 DP 二:區間 / 樹形 / 狀壓](./20-dp-advanced.md)
