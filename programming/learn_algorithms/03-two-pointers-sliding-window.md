# Ch 3 — Array / String:雙指針與滑動視窗

> 目標:把「雙指針」和「sliding window」兩個常被混用的名詞分清楚,建立辨識訊號 → 模板的對應。

## 解決什麼問題?

兩個都是把 O(n²) 的 brute force 壓到 O(n) 的通用工具,但**適用場景不同**。面試很多人會說「我用雙指針解」然後寫的其實是 sliding window,反之亦然。名字講錯不扣分,但分不清概念會卡題。

## 雙指針 vs Sliding Window:定義釐清

| | 雙指針 | Sliding Window |
|---|---|---|
| 指針動作 | 兩個指針可以各自獨立移動(常是相向或同向) | 兩個指針形成「一個視窗」,`[l, r]` 範圍有意義 |
| 題目訊號 | 「pair」「triplet」「兩個元素滿足 X」 | 「subarray」「substring」「連續區段滿足 X」 |
| 典型題 | Two Sum II(sorted)、3Sum、盛水容器 | 最長無重複子字串、和為 k 的最短 subarray |

白話:**雙指針是「找兩個點」,sliding window 是「找一段」**。

## 雙指針模板

### 模板 A — 相向(從兩端往中間)

需要 **input 已排序** 或性質允許兩端同時移動。

```python
def two_pointers_inward(arr, target):
    l, r = 0, len(arr) - 1
    while l < r:
        s = arr[l] + arr[r]
        if s == target:
            return [l, r]
        elif s < target:
            l += 1      # 要大一點
        else:
            r -= 1      # 要小一點
    return None
```

**為什麼 work**:sorted 條件下,`s < target` 時 `r` 已經是最大了,`l` 必須右移才可能增大 `s`。每步排除一個 index,共 n 步。

經典題:

- Two Sum II(已排序)
- 3Sum / 4Sum(先 sort,固定一個 loop 外,剩下兩個用雙指針)
- Container With Most Water(11)
- Valid Palindrome(125)

### 模板 B — 同向(快慢指針)

```python
def two_pointers_same_direction(arr):
    slow = 0
    for fast in range(len(arr)):
        if should_keep(arr[fast]):
            arr[slow] = arr[fast]
            slow += 1
    return slow
```

**slow 指向下一個要寫入的位置,fast 掃整個 array**。

經典題:

- Remove Duplicates from Sorted Array(26)
- Remove Element(27)
- Move Zeroes(283)

## Sliding Window 模板

### 模板 C — 不定長視窗

最常見。視窗動態伸縮直到滿足/不滿足條件。

```python
def sliding_window(s, condition):
    l = 0
    state = init_state()
    best = 0
    for r in range(len(s)):
        # 擴張:把 s[r] 加入視窗
        add_to_state(state, s[r])

        # 收縮:當視窗不合法時,從左邊縮
        while not valid(state):
            remove_from_state(state, s[l])
            l += 1

        # 更新答案
        best = max(best, r - l + 1)

    return best
```

**三個決策點**:
1. 擴張:`s[r]` 對 state 的影響。
2. 收縮條件:state 何時「過頭」了。
3. 答案更新時機:**縮完之後**更新,因為此時 `[l, r]` 才合法。

經典題:

- Longest Substring Without Repeating Characters(3)
- Minimum Window Substring(76)
- Longest Repeating Character Replacement(424)

### 模板 D — 定長視窗

```python
def fixed_window(arr, k):
    window_sum = sum(arr[:k])
    best = window_sum
    for r in range(k, len(arr)):
        window_sum += arr[r] - arr[r - k]  # 加右邊新的,減左邊舊的
        best = max(best, window_sum)
    return best
```

經典題:

- Max Sum of k-length Subarray
- Sliding Window Maximum(239,要配合單調 deque,Ch 5 再講)

## Minimum Window Substring 深入(模板 C 的實戰)

> 給 s 和 t,回傳 s 最短的子字串使其包含 t 所有字元。

```python
from collections import Counter

def min_window(s: str, t: str) -> str:
    need = Counter(t)
    missing = len(t)        # 還缺多少字元
    l = 0
    best = (float('inf'), 0, 0)  # (len, l, r)

    for r, ch in enumerate(s):
        if need[ch] > 0:    # 這個字元還需要
            missing -= 1
        need[ch] -= 1       # 負的也允許,代表多了

        if missing == 0:    # 視窗合法,開始收縮
            while need[s[l]] < 0:   # 左邊是多餘的
                need[s[l]] += 1
                l += 1

            if r - l + 1 < best[0]:
                best = (r - l + 1, l, r)

            need[s[l]] += 1
            missing += 1
            l += 1          # 收縮一格,下一輪繼續擴張

    return "" if best[0] == float('inf') else s[best[1]:best[2]+1]
```

**關鍵 invariant**:
- `need[ch]`:`t` 中該字元的剩餘需求。負數代表視窗中這個字元「溢出」了。
- `missing`:整個需求還差多少(**只數需要的部分,溢出不扣**)。

這題是 sliding window 的試金石,寫熟了大部分變形題都不是問題。

## 訊號辨識

看到以下字眼,條件反射到對應工具:

| 題目字眼 | 候選 |
|---|---|
| 「sorted array」+「找一對 sum」 | 雙指針(模板 A) |
| 「longest / shortest substring with condition」 | Sliding window(模板 C) |
| 「exactly k distinct」 | Sliding window,常要「至多 k - 至多 k-1」 |
| 「remove / move inplace」 | 快慢指針(模板 B) |
| 「subarray with sum = k」 | **前綴和 + hash**(不是 sliding window,有負數時 window 不單調) |
| 「連續元素 XOR」 | 前綴 XOR + hash |

## 常見陷阱

**陷阱 1:sliding window 要求視窗擴張是「單調惡化」、收縮是「單調改善」**

有負數的 subarray 和問題不能用 sliding window,因為加一個負數會讓 sum 變小、視窗擴張不是單調變大。

```python
# 這題不能用 sliding window!
# Subarray Sum Equals K (560),arr 可能有負數
# 正解:前綴和 + hash map
```

**陷阱 2:雙指針忘了排序**

「找兩個數和為 target」如果題目沒說 sorted,就不能直接雙指針。要嘛先 sort(O(n log n)),要嘛 hash(O(n))。

**陷阱 3:定長 window 忘了初始化**

```python
# BAD
for r in range(len(arr)):
    if r >= k:
        window_sum -= arr[r - k]
    window_sum += arr[r]
    if r >= k - 1:
        best = max(best, window_sum)
```

`r >= k - 1` 這個條件很容易忘,導致前 k-1 步誤更新答案。我推薦模板 D 的寫法,先算滿 k 個再開始。

## 完整範例:盛水容器(Container With Most Water)

> `height[i]` 是第 i 條垂直線的高度,找兩條線使其能盛最多水。

```python
def max_area(height):
    l, r = 0, len(height) - 1
    best = 0
    while l < r:
        h = min(height[l], height[r])
        best = max(best, h * (r - l))
        # 關鍵:移動較矮的那條
        if height[l] < height[r]:
            l += 1
        else:
            r -= 1
    return best
```

**為什麼移動較矮的?**

假設 `height[l] < height[r]`。若固定 l 不動、r--,新的 `r' < r`,寬度變小,高度最多還是 `height[l]`(甚至更小),面積必然更小。所以固定 l 不可能找到更好答案,必須移動 l。

**這就是「證明單調性」**——雙指針能 work 的理由。面試時能講出這段,從「會寫」變成「懂為什麼寫」。

## 自我檢核

- [ ] 雙指針和 sliding window 的區別,用一句話解釋。
- [ ] 為什麼 Two Sum(未排序版)不能用雙指針?
- [ ] Sliding window 為什麼不能處理有負數的 subarray sum?
- [ ] 寫一下 "最長包含至多 k 個不同字元的子字串" 的 O(n) 解。
- [ ] 寫一下「從 sorted array 找 3Sum = 0」的 O(n²) 解。

→ [Ch 4 Hash 的威力與陷阱](./04-hash.md)
