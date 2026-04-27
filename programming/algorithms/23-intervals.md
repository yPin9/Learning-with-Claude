# Ch 23 — Interval Problems

> 目標:熟練「區間合併 / 插入 / 重疊」的三種模板,辨識 sweep line 的時機。

## 區間題的三種處理

### 1. Sort + 掃描

90% 的區間題。

**排序的關鍵**:按 start 排?按 end 排?取決於題目。

### 2. Sweep Line(掃描線)

把每個區間拆成「開始事件」和「結束事件」,按時間排序,依序處理。

### 3. 區間 Tree / Segment Tree

面試極罕見。若 V 小用 brute force,V 大用 sort 掃也通常夠。

---

## 經典題:Sort + 掃描

### Merge Intervals (56)

```python
def merge(intervals):
    intervals.sort(key=lambda x: x[0])
    ans = []
    for s, e in intervals:
        if ans and s <= ans[-1][1]:
            ans[-1][1] = max(ans[-1][1], e)
        else:
            ans.append([s, e])
    return ans
```

**按 start 排**。掃的時候:若當前 start ≤ 前一段 end,就合併;否則開新段。

### Insert Interval (57)

> 一組已 sort 且不重疊的區間,插入一個新區間並合併。

```python
def insert(intervals, newInterval):
    ans = []
    i, n = 0, len(intervals)
    # 1. 加入所有「結束 < 新的開始」的區間
    while i < n and intervals[i][1] < newInterval[0]:
        ans.append(intervals[i])
        i += 1
    # 2. 合併所有跟新區間重疊的
    while i < n and intervals[i][0] <= newInterval[1]:
        newInterval[0] = min(newInterval[0], intervals[i][0])
        newInterval[1] = max(newInterval[1], intervals[i][1])
        i += 1
    ans.append(newInterval)
    # 3. 加入剩下的
    ans.extend(intervals[i:])
    return ans
```

**三段式**。比先合併再 sort 更清楚,O(n) 達成。

### Non-overlapping Intervals (435,Ch 16 已寫)

按 end 排,貪婪。

### Meeting Rooms II (253,Ch 9 已寫)

Heap 解是最優雅。**另一個 sweep line 解**:

```python
def min_meeting_rooms(intervals):
    starts = sorted(s for s, e in intervals)
    ends = sorted(e for s, e in intervals)
    rooms = max_rooms = 0
    i = j = 0
    while i < len(starts):
        if starts[i] < ends[j]:
            rooms += 1
            i += 1
        else:
            rooms -= 1
            j += 1
        max_rooms = max(max_rooms, rooms)
    return max_rooms
```

分開排 start 和 end,用兩個指標推進。**每有 start 進入且還沒有 end 出來,就加一間房**。

---

## Sweep Line 模板

當題目有「同時間有多少活動」「最大重疊」類訊號時:

```python
events = []
for start, end in intervals:
    events.append((start, +1))    # 開始
    events.append((end, -1))      # 結束
events.sort()
cur = peak = 0
for t, delta in events:
    cur += delta
    peak = max(peak, cur)
```

**細節**:若「start = end」算不重疊(meeting 類),要讓 `-1` 排在 `+1` 前(相同 t,先結束再開始):

```python
events.sort(key=lambda x: (x[0], x[1]))   # +1 > -1,所以 -1 自然先
```

### The Skyline Problem (218, Hard)

> 給建築群(left, right, height),畫天際線。

```python
import heapq

def get_skyline(buildings):
    events = []
    for l, r, h in buildings:
        events.append((l, -h, r))    # 開始(h 取負,max-heap)
        events.append((r, 0, 0))     # 結束(占位)
    events.sort()
    ans = []
    h = [(0, float('inf'))]    # (negative_height, end)
    for x, neg_h, r in events:
        if neg_h:    # 建築開始
            heapq.heappush(h, (neg_h, r))
        # 清除已經結束的
        while h[0][1] <= x:
            heapq.heappop(h)
        cur_h = -h[0][0]
        if not ans or ans[-1][1] != cur_h:
            ans.append([x, cur_h])
    return ans
```

**心法**:
- 事件點是每個建築的左右邊界
- Heap 維持「當前覆蓋此 x 的所有建築高度」
- 取 heap 頂就是當前天際線高度
- 高度變化時記錄新點

這題是 sweep line 的經典 Hard,能寫出來說明對 heap + 事件排序很熟。

---

## Interval Intersection (986)

> 兩個已排序、每個內部互不相交的區間集合,求交集。

```python
def interval_intersection(A, B):
    i = j = 0
    ans = []
    while i < len(A) and j < len(B):
        lo = max(A[i][0], B[j][0])
        hi = min(A[i][1], B[j][1])
        if lo <= hi:
            ans.append([lo, hi])
        if A[i][1] < B[j][1]: i += 1
        else: j += 1
    return ans
```

**雙指針**。誰的 end 小誰前進——它沒機會再跟後面的區間相交了。

---

## Employee Free Time (759, Hard)

> 每員工有忙碌時段,找所有人都空的共同時段。

**先合併所有員工的時段**,再找空缺。合併用 sort。或 heap(k-way merge)。

```python
def employee_free_time(schedule):
    intervals = [i for employee in schedule for i in employee]
    intervals.sort(key=lambda x: x.start)
    ans = []
    prev_end = intervals[0].end
    for i in intervals[1:]:
        if i.start > prev_end:
            ans.append(Interval(prev_end, i.start))
        prev_end = max(prev_end, i.end)
    return ans
```

---

## 陷阱

### 陷阱 1:「邊界碰撞」算不算重疊

`[1, 2]` 和 `[2, 3]` 算重疊嗎?

- 會議室類:通常**不算**(end 是開區間)
- 合併類:通常**算**(`s <= prev_end`)

**先跟面試官確認**。題意不清就問。

### 陷阱 2:Sweep Line 事件排序

`(time, +1)` 和 `(time, -1)` 同時,誰先?決定「碰撞」算不算。通常先 `-1`(先釋放再佔用)。

### 陷阱 3:Sort 的 key 錯誤

有些題按 start 排,有些按 end 排,要想清楚理由。隨便 sort 再掃,會錯。

---

## 自我檢核

- [ ] Merge Intervals 按 start 排;Non-overlapping Intervals 為什麼按 end?
- [ ] Sweep line 的「+1、-1」事件排序規則是什麼?
- [ ] Meeting Rooms II 的 heap 解和 sweep line 解,複雜度一樣嗎?
- [ ] Interval Intersection 雙指針:誰的 end 小誰前進的理由?
- [ ] 區間 `[1,2]` 和 `[2,3]` 的重疊判定,如何跟面試官 clarify?

→ [Ch 24 字串進階(KMP / Rabin-Karp)](./24-string-advanced.md)
