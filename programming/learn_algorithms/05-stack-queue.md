# Ch 5 — Stack / Queue / Monotonic Stack

> 目標:用 stack / queue 解決括號類與歷史狀態類題目;理解 monotonic stack 為什麼是「下一個更大元素」這類問題的標準解。

## Stack:歷史狀態的 LIFO 容器

Python 直接用 `list`:

```python
stack = []
stack.append(x)    # push, O(1)
stack.pop()        # pop, O(1)
stack[-1]          # top, O(1)
```

不要用 `deque` 寫 stack——能用但多餘。

**Stack 的三大用途**:
1. **括號 / 匹配**:看到「閉」要對到最近的「開」。
2. **歷史狀態**:需要「撤銷」或「回到前一個狀態」。
3. **遞迴的 iterative 改寫**:系統 stack 有深度限制,手動 stack 沒有。

## Queue / Deque

```python
from collections import deque
q = deque()
q.append(x)        # 右加
q.popleft()        # 左出
q.appendleft(x)    # 左加
q.pop()            # 右出
```

**純 queue 場景**:BFS、排隊模擬。
**需要雙端**:Sliding Window Maximum 的單調 deque、LRU。

---

## 經典題型

### 1. Valid Parentheses (20)

```python
def is_valid(s: str) -> bool:
    pairs = {')': '(', ']': '[', '}': '{'}
    stack = []
    for c in s:
        if c in "([{":
            stack.append(c)
        else:
            if not stack or stack[-1] != pairs[c]:
                return False
            stack.pop()
    return not stack
```

**易錯點**:忘了最後檢查 `stack` 為空(否則 `"(("` 會誤判為 True)。

### 2. Min Stack (155)

> 設計一個 stack,`push / pop / top / getMin` 都是 O(1)。

```python
class MinStack:
    def __init__(self):
        self.stack = []       # (value, current_min)

    def push(self, x):
        cur_min = min(x, self.stack[-1][1]) if self.stack else x
        self.stack.append((x, cur_min))

    def pop(self):
        self.stack.pop()

    def top(self):
        return self.stack[-1][0]

    def getMin(self):
        return self.stack[-1][1]
```

**心法**:把「當前最小」隨值一起存。空間換時間。不能只存一個 min 變數,pop 後無法回溯。

### 3. Evaluate Reverse Polish Notation (150)

```python
def eval_rpn(tokens):
    stack = []
    for t in tokens:
        if t in "+-*/":
            b, a = stack.pop(), stack.pop()   # 注意順序!
            if t == '+': stack.append(a + b)
            elif t == '-': stack.append(a - b)
            elif t == '*': stack.append(a * b)
            else: stack.append(int(a / b))    # 題目要求向零 truncate
        else:
            stack.append(int(t))
    return stack[0]
```

**陷阱**:`a - b` 順序,`int(a / b)` 不是 `a // b`(`//` 是向下,`-7 // 2 = -4` 而不是 `-3`)。

---

## Monotonic Stack:面試高頻殺器

這是 stack 家族裡最值得背熟的 pattern。

### 解決什麼問題?

「對每個元素,找其**左邊 / 右邊第一個比它大 / 小**的元素(或位置)」這類問題。

關鍵字:

- Next Greater Element
- Daily Temperatures
- Largest Rectangle in Histogram
- Trapping Rain Water

### 模板(「右邊第一個更大」版本)

```python
def next_greater_elements(arr):
    n = len(arr)
    ans = [-1] * n
    stack = []    # 存 index,對應的值單調遞減

    for i in range(n):
        while stack and arr[stack[-1]] < arr[i]:
            j = stack.pop()
            ans[j] = arr[i]
        stack.append(i)
    return ans
```

**三個 invariant**(面試要能講出來):

1. Stack 裡存 **index**(不是值,因為常要距離)。
2. Stack 對應的值是 **單調遞減**(新進元素把所有 ≤ 它的都彈掉)。
3. 彈出時,`arr[i]` 就是 `arr[j]` 的「右邊第一個更大」。

**複雜度 O(n)**:每個 index 最多 push 一次 pop 一次。

### 四種變形

| 問題 | stack 內值順序 | while 條件 |
|---|---|---|
| 右邊第一個更大 | 遞減 | `arr[stack[-1]] < arr[i]` |
| 右邊第一個更小 | 遞增 | `arr[stack[-1]] > arr[i]` |
| 左邊第一個更大 | 遞減,從右往左掃 | 同上 |
| 左邊第一個更小 | 遞增,從右往左掃 | 同上 |

或者「左邊第一個更大」可以從左往右掃,記錄 stack 當前 top 即可(但這其實就是 pop 別人之前,stack 裡剩下的 top)。

### Daily Temperatures (739)

> 給每日氣溫,回傳每天還要等幾天才會有更高溫。

```python
def daily_temperatures(T):
    n = len(T)
    ans = [0] * n
    stack = []    # index,對應溫度遞減
    for i, t in enumerate(T):
        while stack and T[stack[-1]] < t:
            j = stack.pop()
            ans[j] = i - j
        stack.append(i)
    return ans
```

### Largest Rectangle in Histogram (84)

> 給 heights array,找面積最大的矩形。

這題是 monotonic stack 的頂點題。

```python
def largest_rectangle_area(heights):
    stack = []     # index,對應高度遞增
    best = 0
    heights.append(0)   # 哨兵,強制結尾清空 stack

    for i, h in enumerate(heights):
        while stack and heights[stack[-1]] > h:
            top = stack.pop()
            # 以 heights[top] 為高的矩形:
            # 右界:i - 1(因為 i 是第一個比它矮的)
            # 左界:stack[-1] + 1(上一個 stack 頂是第一個比它矮的左側),若 stack 空則 0
            left = stack[-1] + 1 if stack else 0
            width = i - left
            best = max(best, heights[top] * width)
        stack.append(i)

    heights.pop()   # 還原
    return best
```

**核心想法**:對每個 bar,找「以這個 bar 為最低的最大矩形」。那矩形的左右邊界分別是「左邊第一個比它矮的」和「右邊第一個比它矮的」。monotonic stack 一次掃描搞定。

**哨兵技巧**:結尾放個 0 強制清空 stack,省掉 loop 結束後再處理剩餘的麻煩 code。

### Trapping Rain Water (42)

有雙指針解和 monotonic stack 解。stack 版的心法:

- Stack 存 index,對應高度遞減。
- 每次遇到更高的,彈 stack 中間的 bar,跟「更高的」和「新來的」夾出來的區域算蓄水。

```python
def trap(height):
    stack = []
    total = 0
    for i, h in enumerate(height):
        while stack and height[stack[-1]] < h:
            bottom = stack.pop()
            if not stack: break
            left = stack[-1]
            w = i - left - 1
            h_diff = min(height[left], h) - height[bottom]
            total += w * h_diff
        stack.append(i)
    return total
```

---

## Monotonic Deque:Sliding Window Maximum

> 給 arr 和視窗 k,回傳每個視窗的最大值。

用單調 deque 是 O(n) 解(heap 解是 O(n log k),也可以,但 onsite 通常要 O(n))。

```python
from collections import deque

def max_sliding_window(arr, k):
    q = deque()    # 存 index,對應值單調遞減
    ans = []
    for i, x in enumerate(arr):
        # 1. 踢掉 front 過期的(視窗外)
        while q and q[0] <= i - k:
            q.popleft()
        # 2. 踢掉 back 比新來還小的(它們永遠不會是最大了)
        while q and arr[q[-1]] < x:
            q.pop()
        q.append(i)
        # 3. 視窗滿了開始記答案
        if i >= k - 1:
            ans.append(arr[q[0]])
    return ans
```

Deque 的兩端都用:front 踢過期、back 踢廢棄。這是 monotonic deque 的典型形狀。

---

## Queue 在 BFS 中的角色(預告)

BFS 用 queue 因為「先發現的先處理」保證最短路徑(無權圖)。Ch 11 會詳講。現在記一個骨架:

```python
from collections import deque
def bfs(start):
    q = deque([start])
    visited = {start}
    while q:
        node = q.popleft()
        for nb in neighbors(node):
            if nb not in visited:
                visited.add(nb)
                q.append(nb)
```

---

## 自我檢核

- [ ] Min Stack 為什麼不能只存一個 `min` 變數?
- [ ] Monotonic stack 為什麼是 O(n)?(答:均攤,每個元素最多 push/pop 一次)
- [ ] Largest Rectangle 的哨兵技巧是什麼?為什麼 work?
- [ ] Sliding Window Maximum 用 deque 為什麼能到 O(n)?
- [ ] 寫一下「Next Greater Element II」(arr 是環狀)。提示:讓 arr 變 `arr + arr`。

→ [Ch 6 Linked List](./06-linked-list.md)
