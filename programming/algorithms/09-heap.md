# Ch 9 — Heap / Priority Queue

> 目標:辨識「top-k / 貪婪選最優 / k-way merge」類問題,熟用 Python 的 heapq 和它特有的陷阱。

## Heap 是什麼

**Heap 是一個「快速拿最小/最大」的資料結構**。實作上是完全二元樹存在 array 裡,滿足 heap property(父 ≤ 子,min-heap)。

核心操作:

| 操作 | 複雜度 |
|---|---|
| 插入 | O(log n) |
| 取最小 | O(1) |
| 彈最小 | O(log n) |
| 建堆 | **O(n)**(不是 O(n log n)!) |

**建堆 O(n)** 很多人不知道。heapify 是從下往上 sift down,數學證明可證 O(n)。面試被問到可以提這點加分。

## Python heapq 基本

Python 只有 min-heap:

```python
import heapq

h = []
heapq.heappush(h, 3)
heapq.heappush(h, 1)
heapq.heappush(h, 2)
heapq.heappop(h)    # 1

# 從 list 建堆(原地,O(n))
arr = [3, 1, 4, 1, 5, 9, 2, 6]
heapq.heapify(arr)

# 一口氣拿 k 個最大 / 最小(內部 O(n log k))
heapq.nlargest(3, arr)
heapq.nsmallest(3, arr)

# push 然後 pop(比分開做快一點)
heapq.heappushpop(h, x)
heapq.heapreplace(h, x)   # pop 然後 push
```

## Max-heap 技巧

Python 沒 max-heap,兩種繞法:

**方法 A:值取負(整數 / 浮點最乾淨)**

```python
heapq.heappush(h, -x)
max_val = -heapq.heappop(h)
```

**方法 B:tuple(priority, item),priority 取負**

```python
heapq.heappush(h, (-priority, item))
```

當 `item` 本身不好比大小(如 `ListNode`),還要加 tiebreaker:

```python
heapq.heappush(h, (-priority, tiebreaker_index, item))
```

## 題型 1:Top K

> 找 array 中最大的 k 個(或最小的 k 個、第 k 個)。

**三種解法**,複雜度不同:

| 解法 | 時間 | 空間 | 備註 |
|---|---|---|---|
| sort 後取 | O(n log n) | O(1) | 簡單粗暴 |
| heap(n 大 k 小時) | O(n log k) | O(k) | 用最小 k 的 max-heap,或最大 k 的 min-heap |
| Quickselect | O(n) 平均 | O(1) | 最快但寫起來難 |

### Heap 版(最常用)

找**最大**的 k 個,用 **min-heap 維持大小 k**:

```python
def top_k_largest(arr, k):
    h = []
    for x in arr:
        heapq.heappush(h, x)
        if len(h) > k:
            heapq.heappop(h)    # 踢掉最小的
    return h    # 剩下就是最大的 k 個
```

**為什麼 min-heap 而不是 max-heap**:我們要留「當前 k 個最大」,每次來一個新的就跟當前最小比——最小者可能被取代。min-heap 的 top 就是「當前 k 個裡最小的」,方便比較。

### Quickselect(O(n) 找第 k 大)

```python
import random

def quickselect(arr, k):
    # 找第 k 小(1-indexed)
    def partition(lo, hi):
        pivot = arr[random.randint(lo, hi)]
        l, r = lo, hi
        while l <= r:
            while arr[l] < pivot: l += 1
            while arr[r] > pivot: r -= 1
            if l <= r:
                arr[l], arr[r] = arr[r], arr[l]
                l += 1
                r -= 1
        return l   # arr[lo..r] <= pivot, arr[l..hi] >= pivot

    lo, hi = 0, len(arr) - 1
    while lo < hi:
        p = partition(lo, hi)
        if k - 1 < p: hi = p - 1
        else: lo = p
    return arr[k - 1]
```

**平均 O(n)**,最壞 O(n²)。Random pivot 讓最壞機率極小。面試寫出 quickselect 會明顯加分。

## 題型 2:K-way Merge

> 合併 k 個 sorted 序列。

**Heap 維持「每個序列當前最前頭的元素」**:

```python
def merge_k_sorted(lists):
    h = []
    for i, lst in enumerate(lists):
        if lst:
            heapq.heappush(h, (lst[0], i, 0))   # (val, list_index, pos_in_list)
    ans = []
    while h:
        val, i, j = heapq.heappop(h)
        ans.append(val)
        if j + 1 < len(lists[i]):
            heapq.heappush(h, (lists[i][j+1], i, j+1))
    return ans
```

複雜度 O(N log k),N 總元素數。

### Find K Pairs with Smallest Sums (373)

> 給兩個 sorted array,找 sum 最小的 k 對 `(arr1[i], arr2[j])`。

**把「從每個 arr1[i] 出發,配對 arr2 的序列」當成 k 個 sorted stream**,用 heap 合併。

```python
def k_smallest_pairs(a, b, k):
    h = [(a[i] + b[0], i, 0) for i in range(min(len(a), k))]
    heapq.heapify(h)
    ans = []
    while h and len(ans) < k:
        s, i, j = heapq.heappop(h)
        ans.append((a[i], b[j]))
        if j + 1 < len(b):
            heapq.heappush(h, (a[i] + b[j+1], i, j+1))
    return ans
```

## 題型 3:兩個 heap(中位數 / 區間)

### Find Median from Data Stream (295)

> 動態加入數字,隨時查詢中位數。

**兩個 heap**:
- `small`:max-heap,存較小的一半
- `large`:min-heap,存較大的一半
- Invariant:`len(small) == len(large)` 或 `len(small) == len(large) + 1`

```python
class MedianFinder:
    def __init__(self):
        self.small = []   # max-heap(存負值)
        self.large = []   # min-heap

    def addNum(self, num):
        heapq.heappush(self.small, -num)
        # 保證 small 的最大 <= large 的最小
        heapq.heappush(self.large, -heapq.heappop(self.small))
        # 維持大小差 ≤ 1
        if len(self.large) > len(self.small):
            heapq.heappush(self.small, -heapq.heappop(self.large))

    def findMedian(self):
        if len(self.small) > len(self.large):
            return -self.small[0]
        return (-self.small[0] + self.large[0]) / 2
```

**關鍵心法**:不是直接 push 到正確那邊,而是「先 push small,再從 small 移一個到 large」,保證有序性。再處理大小平衡。背下來。

## 題型 4:Scheduling / Meeting Rooms

### Meeting Rooms II (253)

> 給會議時段,求最少需要幾間房。

```python
def min_meeting_rooms(intervals):
    intervals.sort(key=lambda x: x[0])   # 按開始時間排序
    h = []    # 存會議結束時間
    for start, end in intervals:
        if h and h[0] <= start:
            heapq.heappop(h)    # 有房釋出可重用
        heapq.heappush(h, end)
    return len(h)
```

**心法**:heap 存「當前所有進行中會議的結束時間」。新會議進來時,看 heap 頂(最早結束的)有沒有在 start 前結束——有就複用,沒有就開新房。

## 題型 5:貪婪 + heap

### Task Scheduler (621)、Reorganize String (767)

需要「每次取當前最多 / 最大的某類東西」的題,heap 是標配。

```python
# Reorganize String:重排字串使相鄰字元不同
from collections import Counter

def reorganize_string(s):
    cnt = Counter(s)
    if max(cnt.values()) > (len(s) + 1) // 2:
        return ""
    h = [(-v, c) for c, v in cnt.items()]
    heapq.heapify(h)
    ans = []
    prev_v, prev_c = 0, ''
    while h:
        v, c = heapq.heappop(h)
        ans.append(c)
        if prev_v < 0:
            heapq.heappush(h, (prev_v, prev_c))   # 冷卻完放回
        prev_v, prev_c = v + 1, c
    return "".join(ans)
```

**心法**:每次取剩餘數量最多的字元放下去,**放完不立刻 push 回**,要等下一輪才可以(保證相鄰不同)。

---

## Heap 陷阱

### 陷阱 1:找第 k 個,直接 sort 也能解

面試官問 O(n log k),你寫 sort 是 O(n log n)——被問「能不能更快」就要切到 heap 或 quickselect。

### 陷阱 2:忘了 tuple 的 tiebreaker

存 `(priority, object)`,當 object 不可比較時 crash。加 index 當第二 key。

### 陷阱 3:以為 heap 是 sorted

**heap 只保證 root 最小,中間順序不定**。想要「遍歷 top-k 按順序」要每次 pop,不能直接 iterate heap。

```python
# WRONG
for x in h:
    print(x)   # 不是升序!

# RIGHT
while h:
    print(heapq.heappop(h))
```

### 陷阱 4:Heap 不支援「刪除任意元素」

面試要你「刪除 heap 中某個特定元素」沒有直接 API。兩種做法:

- **Lazy deletion**:標記要刪的,pop 時檢查、丟棄已標記的。
- **SortedList** 代替 heap。

---

## 自我檢核

- [ ] 為什麼找「前 k 大」用 min-heap 而不是 max-heap?
- [ ] `heapify` 的時間複雜度?為什麼不是 O(n log n)?
- [ ] Median from Stream:兩個 heap 怎麼維持平衡?
- [ ] 寫一個 quickselect 找第 k 小。
- [ ] 為什麼 heap 不支援 O(log n) 刪任意元素?

→ [Ch 10 Union-Find](./10-union-find.md)
