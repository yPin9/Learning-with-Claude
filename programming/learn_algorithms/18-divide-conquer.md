# Ch 18 — Divide & Conquer

> 目標:知道 D&C 是什麼、什麼時候比其他範式好、熟練合併排序這類代表題。這一章短,因為 D&C 在面試常跟 DP / binary search 混著考。

## 定義

**切成小問題,遞迴解,再合併**。三步:

1. **Divide**:把問題拆成獨立的子問題
2. **Conquer**:遞迴解子問題
3. **Combine**:合併子解

**D&C vs DP 的差別**:DP 子問題**會重疊**(所以要 memo),D&C 子問題**獨立**(不會重疊)。

## 代表題:Merge Sort

```python
def merge_sort(arr):
    if len(arr) <= 1: return arr
    mid = len(arr) // 2
    left = merge_sort(arr[:mid])
    right = merge_sort(arr[mid:])
    return merge(left, right)

def merge(a, b):
    res = []
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            res.append(a[i]); i += 1
        else:
            res.append(b[j]); j += 1
    res.extend(a[i:])
    res.extend(b[j:])
    return res
```

**複雜度**:T(n) = 2 T(n/2) + O(n) → O(n log n)。

**空間**:O(n) 額外(合併時需要)。

## Master Theorem(面試偶爾問)

遞迴 T(n) = a · T(n/b) + O(n^d),解是:

- a > b^d → T(n) = O(n^(log_b a))
- a = b^d → T(n) = O(n^d log n)
- a < b^d → T(n) = O(n^d)

背一下 log_2 3 ≈ 1.585(Strassen 矩陣乘法)、log_2 2 = 1(merge sort 和 quick sort 平均)。

---

## 經典題

### Count of Smaller Numbers After Self (315, Hard)

> 對每個位置,數後面有幾個比它小。

**Merge sort 的妙用**:合併時,當右半取出一個數 < 左半當前最前面的數,就意味著「右半當前這個數,比左半剩下的所有數都小」——對應位置就能 count。

```python
def count_smaller(nums):
    ans = [0] * len(nums)
    arr = list(enumerate(nums))    # (original_index, value)

    def merge_sort(a):
        if len(a) <= 1: return a
        mid = len(a) // 2
        left = merge_sort(a[:mid])
        right = merge_sort(a[mid:])
        merged = []
        i = j = 0
        while i < len(left) and j < len(right):
            if left[i][1] <= right[j][1]:
                ans[left[i][0]] += j   # left[i] 之前已經有 j 個 right 被搬走,全都 < left[i]
                merged.append(left[i])
                i += 1
            else:
                merged.append(right[j])
                j += 1
        while i < len(left):
            ans[left[i][0]] += j
            merged.append(left[i])
            i += 1
        merged.extend(right[j:])
        return merged

    merge_sort(arr)
    return ans
```

這題不懂 merge sort 的「count inversions」idea 會卡到死。

### Reverse Pairs (493, Hard)

同樣思路,merge sort 的 partition 順便算「i < j 且 arr[i] > 2 × arr[j]」的對數。

### Quickselect(Ch 9 已提)

找第 k 小,D&C 平均 O(n)。

### Pow(x, n) (50)

快速冪:

```python
def my_pow(x, n):
    if n < 0: return 1 / my_pow(x, -n)
    if n == 0: return 1
    half = my_pow(x, n // 2)
    return half * half * (x if n & 1 else 1)
```

**複雜度 O(log n)**。這題算 D&C,但跟 binary search 同源。

---

## D&C vs DP 判斷

| | D&C | DP |
|---|---|---|
| 子問題 | 獨立(不會重疊) | 會重疊 |
| 需要 memo | 不用 | 必須 |
| 典型題 | Merge sort、quickselect、FFT | Fibonacci、knapsack |

**判斷方法**:畫遞迴樹,若有節點出現兩次以上 → DP(需要 memo)。

---

## 自我檢核

- [ ] D&C 和 DP 最核心的差別?
- [ ] Merge sort 為什麼是 O(n log n)?對應 Master Theorem 的哪個 case?
- [ ] Count of Smaller Numbers 為什麼能在 merge sort 過程中計算?
- [ ] Pow(x, n) 的 log n 來自哪裡?

→ [Ch 19 DP 一:辨識與模板](./19-dp-basics.md)
