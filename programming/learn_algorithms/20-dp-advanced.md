# Ch 20 — DP 二:區間 / 樹形 / 狀態壓縮

> 目標:掌握三種進階 DP 形狀。它們的共同點是 state 不是「前 i 個」,而是更結構化的表達。

## 區間 DP(Interval DP)

**state 是「區間 [i, j] 的最優值」**。轉移通常是枚舉一個**分割點 k**,把區間切成 `[i, k]` 和 `[k+1, j]`。

### 訊號

- 「合併 / 分割」
- 「回文」
- 「括號匹配」
- 「以 k 為某種意義的中間點」

### 模板

```python
n = len(arr)
dp = [[0] * n for _ in range(n)]

# 枚舉區間長度(從小到大)
for length in range(2, n + 1):
    for i in range(n - length + 1):
        j = i + length - 1
        # 枚舉分割點 k
        for k in range(i, j):
            dp[i][j] = optimum(dp[i][j], combine(dp[i][k], dp[k+1][j], ...))
```

**迴圈順序很重要**:外層長度從小到大,才能保證算 `dp[i][j]` 時 `dp[i][k]` 和 `dp[k+1][j]` 都算過。

### 經典題:Matrix Chain Multiplication(面試罕見但模範)

給一串矩陣,找乘法順序使總乘法次數最少。

```python
# dp[i][j] = 乘完 [i..j] 這些矩陣的最少次數
# dp[i][j] = min(dp[i][k] + dp[k+1][j] + dims[i] * dims[k+1] * dims[j+1])
```

### Burst Balloons (312, Hard)

> 戳破氣球獲得 `nums[l] * nums[i] * nums[r]` 分(l, r 是當前左右鄰居),求最大總分。

**反直覺**:枚舉「**最後**戳破的氣球」k,而不是第一個。因為枚舉最後戳的,左右區間就互不干擾。

```python
def max_coins(nums):
    nums = [1] + nums + [1]    # 邊界虛擬 1
    n = len(nums)
    dp = [[0] * n for _ in range(n)]

    for length in range(2, n):     # 區間長度(不含兩端的 1 邊界)
        for i in range(n - length):
            j = i + length
            for k in range(i + 1, j):
                dp[i][j] = max(dp[i][j],
                               dp[i][k] + dp[k][j] + nums[i] * nums[k] * nums[j])
    return dp[0][n - 1]
```

**這題的精髓是「枚舉最後動作」**。直接枚舉第一個戳的會失敗,因為戳了之後影響後續所有 state。

### Palindrome Partitioning II (132)

> 切最少刀讓字串每段都是回文。

```python
# 先 O(n²) 預處理 is_palin
# dp[i] = s[:i+1] 最少切幾刀
# dp[i] = 0 if is_palin(0, i)
# dp[i] = min(dp[j] + 1) for j < i if is_palin(j+1, i)
```

嚴格來說這是一維 DP,但常被歸在區間 DP 因為要預處理回文表。

---

## 樹形 DP(Tree DP)

**state 定義在節點上,通常 postorder 推算**。跟 Ch 7 的「回傳值 ≠ 答案」形狀類似。

### 訊號

- 輸入是 tree(二元或多叉)
- 每個節點的結果依賴子節點
- 常常「跟父不同 vs 相同」、「選 vs 不選」

### House Robber III (337)

> 二元樹版打家劫舍:不能同時偷父和子。

```python
# 每個節點回傳 (rob_it, not_rob_it)
#   rob_it:偷這個節點時,以此為根子樹的最大
#   not_rob_it:不偷

def rob(root):
    def dfs(node):
        if not node: return (0, 0)
        l = dfs(node.left)
        r = dfs(node.right)
        rob_it = node.val + l[1] + r[1]      # 子不能偷
        not_rob = max(l) + max(r)            # 子偷不偷自由
        return (rob_it, not_rob)
    return max(dfs(root))
```

**雙 state 的 tree DP**。這類「選 vs 不選」的變形非常多。

### Binary Tree Cameras (968)

三 state:節點裝了 camera / 被覆蓋但沒裝 / 沒被覆蓋。貪婪 + 樹形 DP。

### Diameter of Binary Tree(Ch 7 已寫)

Diameter、Max Path Sum 都是樹形 DP 的雛形——遞迴回傳「從這節點往下的最好值」,答案在過程中更新。

---

## 狀態壓縮 DP(Bitmask DP)

**把一組 0/1 狀態編碼成整數,當 state 用**。通常 n ≤ 20 的訊號。

### 訊號

- n ≤ 20(2^n ≤ 10^6)
- 有「一組元素的子集」要當 state
- 「每個元素已使用 / 未使用」

### 模板:TSP(Travelling Salesman)

> n 個城市,找遍歷所有城市一次的最短路徑。

```python
# dp[mask][i] = 已訪問 mask 中的城市,當前在 i 的最短距離
# mask 是 n 位 bitmask

def tsp(n, dist):
    INF = float('inf')
    dp = [[INF] * n for _ in range(1 << n)]
    dp[1][0] = 0    # 從 0 出發,只訪問了 0

    for mask in range(1 << n):
        for u in range(n):
            if dp[mask][u] == INF: continue
            if not (mask >> u) & 1: continue    # u 必須在 mask 中
            for v in range(n):
                if (mask >> v) & 1: continue    # v 已在 mask,跳過
                new_mask = mask | (1 << v)
                dp[new_mask][v] = min(dp[new_mask][v], dp[mask][u] + dist[u][v])

    return min(dp[(1 << n) - 1][i] + dist[i][0] for i in range(n))
```

**複雜度 O(2^n × n²)**。n = 20 仍可行(4M 次迴圈,勉強)。

### Bitmask 的常用操作

```python
mask | (1 << i)       # 加入 i
mask & ~(1 << i)      # 移除 i
(mask >> i) & 1       # 檢查 i 是否在
bin(mask).count('1')  # popcount(Python 3.10+ 有 int.bit_count())
mask & -mask          # 最低位的 1
```

### 其他狀壓題

- Partition to K Equal Sum Subsets (698)
- Smallest Sufficient Team (1125)
- Number of Ways to Wear Different Hats (1434)

這類題面試見到就想:**是不是 n 很小可以狀壓?**

---

## 進階 DP 的共同心法

1. **state 不一定是「前 i 個」**。可以是區間、樹節點、bitmask、甚至多個維度疊加。
2. **找對 state 就贏一半**。state 錯了後面怎麼做都錯,state 對了轉移自然出來。
3. **先暴力,再優化**。從 O(2^n) 的 brute force 開始,加 memo 變 DP,再看能不能降維或換順序。

## DP 優化技巧預告(不深入)

- **滾動陣列**:空間 O(n²) → O(n)
- **單調隊列優化**:某類 DP 的 O(n²) → O(n)
- **斜率優化 / 四邊形不等式**:更深的優化,面試不考
- **矩陣快速冪**:線性遞推的 O(n) → O(log n),偶爾在難題見到

面試認得滾動陣列、會寫單調隊列優化(罕見)就夠了。

---

## 自我檢核

- [ ] 區間 DP 為什麼要外層枚舉「長度」?
- [ ] Burst Balloons 為什麼枚舉「最後戳的」而不是第一個?
- [ ] 樹形 DP 的 state 一般定在節點上,為什麼要 postorder?
- [ ] 狀壓 DP 的 n 上限一般是多少?為什麼?
- [ ] Bitmask 操作:怎麼判斷 i 是否在 mask 中?

→ [Ch 21 DP 三:經典題型](./21-dp-classics.md)
