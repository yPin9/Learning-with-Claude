# Ch 15 — Binary Search:找答案不是找數字

> 目標:擺脫「binary search 只用在 sorted array 找數字」的誤解,掌握「二分答案」的威力。

## 病:以為 binary search 只是「找數字」

課本講 binary search 都是「sorted array 裡找 target」。但面試題最常考的**變形**叫**「二分答案」**:

- 你有個答案空間 `[lo, hi]`(可能是數值、可能是索引)
- 你能定義一個 `check(x) -> bool` 判斷 x 是否可行
- `check` 是**單調**的:x 可行則所有更大 / 更小的也可行
- 答案就是邊界點

**這時 binary search 的本質是「在單調區間上找分界」**,不是找特定值。

## 模板選擇:**lo < hi** 派

面試寫 binary search 容易因為邊界錯誤掉分。背**一套模板**,所有變形都用它。

推薦 `while lo < hi` 的版本。

### 模板 A:找**第一個**滿足 condition 的位置

```python
def binary_search(lo, hi, check):
    # 找最小的 x 使得 check(x) == True
    # 前提:check 單調(False...True)
    while lo < hi:
        mid = (lo + hi) // 2
        if check(mid):
            hi = mid       # mid 可能是答案,保留
        else:
            lo = mid + 1
    return lo
```

### 模板 B:找**最後一個**滿足 condition 的位置

```python
def binary_search(lo, hi, check):
    # 找最大的 x 使得 check(x) == True
    # 前提:check 單調(True...False)
    while lo < hi:
        mid = (lo + hi + 1) // 2    # 注意 +1,避免死循環
        if check(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo
```

**為什麼 +1**:當 `lo + 1 == hi` 且 `check(mid)` 為 True,若 `mid = (lo + hi) // 2 = lo`,`lo = mid` 不動,死循環。`+1` 讓 mid 偏右。

背熟這兩個,所有二分題都是**把題目翻譯成 check**。

---

## 經典題 1:Sorted Array 找 target(入門)

### 普通版 (704)

```python
def search(arr, target):
    lo, hi = 0, len(arr) - 1
    while lo <= hi:    # 經典模板,也可用 lo<hi 改寫
        mid = (lo + hi) // 2
        if arr[mid] == target: return mid
        elif arr[mid] < target: lo = mid + 1
        else: hi = mid - 1
    return -1
```

### Find First / Last Position (34)

用模板 A 找第一個 `>= target` 的位置:

```python
def search_range(arr, target):
    def first_ge(t):
        lo, hi = 0, len(arr)
        while lo < hi:
            mid = (lo + hi) // 2
            if arr[mid] >= t: hi = mid
            else: lo = mid + 1
        return lo

    left = first_ge(target)
    if left == len(arr) or arr[left] != target: return [-1, -1]
    right = first_ge(target + 1) - 1
    return [left, right]
```

**小技巧**:last position of x = first position of x+1 minus 1。這招用熟了很多邊界題瞬間變簡單。

---

## 經典題 2:Rotated Sorted Array

### Search in Rotated Sorted Array (33)

> sorted array 被旋轉某個 pivot,找 target。

```python
def search_rotated(arr, target):
    lo, hi = 0, len(arr) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid] == target: return mid
        if arr[lo] <= arr[mid]:    # 左半 sorted
            if arr[lo] <= target < arr[mid]:
                hi = mid - 1
            else:
                lo = mid + 1
        else:                       # 右半 sorted
            if arr[mid] < target <= arr[hi]:
                lo = mid + 1
            else:
                hi = mid - 1
    return -1
```

**心法**:即使整體不 sorted,`mid` 兩邊**至少一邊 sorted**。判斷哪邊 sorted、target 在不在 sorted 那邊,決定往哪邊走。

### Find Minimum in Rotated Sorted Array (153)

```python
def find_min(arr):
    lo, hi = 0, len(arr) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] > arr[hi]: lo = mid + 1
        else: hi = mid
    return arr[lo]
```

**為什麼跟 arr[hi] 比,不跟 arr[lo] 比**:比 `arr[lo]` 在數組完全未旋轉時會有邊界問題。`arr[hi]` 的比較永遠指向正確的半邊。這是細節,背下來。

---

## 經典題 3:二分答案(面試的真正主題)

### Koko Eating Bananas (875)

> Koko 每小時吃 k 根香蕉,要在 H 小時內吃完 piles,求最小 k。

**答案空間**:`[1, max(piles)]`。
**check(k)**:用 k 的速度能不能 H 小時內吃完?單調(k 大則必可行)。

```python
def min_eating_speed(piles, H):
    def can_finish(k):
        return sum((p + k - 1) // k for p in piles) <= H

    lo, hi = 1, max(piles)
    while lo < hi:
        mid = (lo + hi) // 2
        if can_finish(mid): hi = mid
        else: lo = mid + 1
    return lo
```

這就是「二分答案」的典型套路:**答案範圍 + check 函數**。

### 識別「二分答案」的訊號

- 「最小的 k 使得 ...」 / 「最大的 k 使得 ...」
- check 函數容易寫、但直接枚舉太慢
- 條件「k 越大越容易滿足」(或反過來)

### Split Array Largest Sum (410)

> 把 array 切成 m 段,使最大段的 sum 最小。

**答案空間**:`[max(arr), sum(arr)]`。
**check(s)**:能不能用 m 段達成每段 sum ≤ s?greedy 從左往右分就好。

```python
def split_array(arr, m):
    def check(s):
        groups, cur = 1, 0
        for x in arr:
            if cur + x > s:
                groups += 1
                cur = x
            else:
                cur += x
        return groups <= m

    lo, hi = max(arr), sum(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if check(mid): hi = mid
        else: lo = mid + 1
    return lo
```

### Capacity to Ship Packages (1011)、Find the Smallest Divisor Given a Threshold (1283)

同一套路,套模板,寫 `check`。

---

## 經典題 4:在二維矩陣上二分

### Search a 2D Matrix (74)

每行 sorted、行首 > 前一行末。**把矩陣當一維 array** 做 binary search。

```python
def search_matrix(matrix, target):
    rows, cols = len(matrix), len(matrix[0])
    lo, hi = 0, rows * cols - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        val = matrix[mid // cols][mid % cols]
        if val == target: return True
        elif val < target: lo = mid + 1
        else: hi = mid - 1
    return False
```

### Search a 2D Matrix II (240,Hard)

行、列各自 sorted,但行與行之間沒保證。**從右上角走**:比 target 小往下,比 target 大往左,O(m + n)。這題不是 binary search,是 staircase search。

---

## 經典題 5:LIS 的 O(n log n) 解

> 最長遞增子序列。

```python
import bisect

def length_of_lis(arr):
    tails = []
    for x in arr:
        i = bisect.bisect_left(tails, x)
        if i == len(tails): tails.append(x)
        else: tails[i] = x
    return len(tails)
```

**心法**:`tails[i]` 儲存「長度 i+1 的 LIS 最小尾巴」。binary search 找插入位置。這個不是直接 DP,是 DP + binary search 的混合。

Ch 21 會再講 LIS。這裡先放,感受一下 binary search 的應用廣度。

---

## 陷阱

### 陷阱 1:邊界條件寫錯

`lo <= hi` vs `lo < hi` vs `lo + 1 < hi` 三種模板混用會出事。**挑一套,全部一致**。

### 陷阱 2:Mid 溢位

Python 沒這問題(int 無限大),但寫 C++/Java 要用 `lo + (hi - lo) // 2`。

### 陷阱 3:`check` 不單調

二分答案的前提是 `check` 單調。寫完要自己驗證這點——不單調就不能二分。

### 陷阱 4:答案空間邊界錯

Koko 的上界寫 `sum(piles)` 也對,但 `max(piles)` 是更緊的上界。寫對即可,但寫越緊越顯示你懂。

---

## 自我檢核

- [ ] 「找第一個 True」和「找最後一個 True」模板的差別?
- [ ] Rotated Sorted Array 找 min 時,為什麼跟 `arr[hi]` 比?
- [ ] 「二分答案」的三個識別訊號是什麼?
- [ ] Koko Eating Bananas 的 check 函式怎麼寫?
- [ ] 為什麼 Python 的 `mid = (lo + hi) // 2` 不會溢位?

→ [Ch 16 Greedy:什麼時候可以貪](./16-greedy.md)
