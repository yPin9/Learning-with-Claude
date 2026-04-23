# Ch 27 — 常見坑:edge case、off-by-one、Python gotcha

> 目標:一份「寫完就檢查一下」的清單,避免 AC 率在細節裡漏血。

## 一、邊界條件清單

寫完每一題,掃一遍這張表:

### Array / String

- [ ] 空輸入 `[]` / `""`
- [ ] 單元素 `[x]` / `"a"`
- [ ] 全相同 `[1,1,1]`
- [ ] Sorted 或 reverse sorted
- [ ] 負數 / 混合正負
- [ ] 很大的值(接近 MAX_INT)
- [ ] 很小的值(接近 MIN_INT,取反會溢位)
- [ ] 重複元素

### Tree / Graph

- [ ] 空樹 / 空圖 (root = None)
- [ ] 單節點
- [ ] 鏈狀(degenerate tree)
- [ ] 完全平衡
- [ ] 不連通的圖
- [ ] 自環(self-loop)
- [ ] 重複邊

### Linked List

- [ ] 空 list(head = None)
- [ ] 單節點
- [ ] 要刪除的節點是 head
- [ ] 要刪除的節點是 tail
- [ ] 所有節點同值

### DP

- [ ] n = 0 / n = 1(base case)
- [ ] 只能向一個方向走(2D DP)
- [ ] 起點或終點不可達
- [ ] 所有值都是負數(狀態初始化要注意)

---

## 二、Off-by-One 的常見場景

### `range(n)` vs `range(n+1)`

```python
for i in range(len(arr)):       # 0 到 n-1
for i in range(len(arr) + 1):   # 0 到 n(前綴和常用)
```

**前綴和題要 `n+1`**(多一個 0 當 base)。

### `arr[i]` vs `arr[i-1]`

DP 常見:`dp[i]` 對應 `arr[i-1]`(dp 多一個 base),很容易 index 錯一位。

### Binary search 的 `hi = len(arr)` vs `hi = len(arr) - 1`

兩套模板兩個起始值,**不要混用**。

### 二維 grid 的邊界

```python
if 0 <= r < rows and 0 <= c < cols:    # 正確
if 0 <= r <= rows - 1 and 0 <= c <= cols - 1:  # 等價但易錯
```

用前者。

### `while l < r` vs `while l <= r`

Ch 15 已講。挑一套模板用。

---

## 三、Python 特有 gotcha

### 1. 切片複製 vs 引用

```python
arr2 = arr         # 同一個!改 arr2 會影響 arr
arr2 = arr[:]      # 淺拷貝
arr2 = list(arr)   # 等同淺拷貝
```

**二維還要更小心**:

```python
grid = [[0] * n] * m      # BAD!所有 row 是同一個 list
grid = [[0] * n for _ in range(m)]    # GOOD
```

### 2. Mutable default argument

```python
def f(arr=[]):     # 危險!所有 call 共享同一個 []
    arr.append(1)
    return arr
f()  # [1]
f()  # [1, 1] 不是 [1]!
```

改用 `None` 哨兵:

```python
def f(arr=None):
    if arr is None: arr = []
    ...
```

### 3. 負數 `%`

```python
-7 % 3    # 2(Python)
         # -1(C)
```

環狀 index 用 Python 版很好(`(i - 1) % n` 給正數)。但 port 到 C++ 要注意。

### 4. 整數除法 `//`

```python
-7 // 2    # -4(Python,向下取整)
          # -3(C,向零截斷)
```

**真的要向零截斷用 `int(a / b)`**:

```python
int(-7 / 2)    # -3
```

面試題 Evaluate Reverse Polish Notation 就是坑這個。

### 5. `sort` 穩定性

Python 的 sort 是穩定的(Timsort)。相同 key 的順序不變。有些 comparator 題靠這點。

### 6. `==` vs `is`

```python
a = [1, 2]
b = [1, 2]
a == b    # True
a is b    # False
```

**比較值用 `==`,比較物件同一性用 `is`**。不要用 `is` 比較 int(小 int 被 cache 看起來像 True,大的就不是)。

### 7. `for x in arr` 修改 arr

```python
for x in arr:
    if x < 0: arr.remove(x)     # 不安全!
```

邊迭代邊修改會跳過元素。改用 list comprehension 或倒序迭代。

### 8. `copy.deepcopy` 很慢

```python
import copy
copy.deepcopy(big_struct)   # O(size),慢
```

面試能 avoid 就 avoid。用 tuple 當不可變、傳 index 避免 copy list。

### 9. 閉包捕獲 variable

```python
funcs = []
for i in range(3):
    funcs.append(lambda: i)
funcs[0]()    # 2,不是 0!
```

閉包捕獲的是 **variable 本身**,loop 結束時 i=2。修正:

```python
funcs.append(lambda i=i: i)   # 用 default value 固定
```

### 10. `heapq` 只有 min-heap

要 max-heap 取負。面試寫一半忘了會 bug 半小時。

---

## 四、演算法本身的常見錯

### Sorting 的穩定性

stable sort 保持相同 key 的相對順序。不穩定 sort 不保。Python sort 是 stable,C++ `std::sort` **不是** stable(`std::stable_sort` 才是)。

### Binary search 的單調性

寫前確認:「我二分的對象真的單調嗎?」不單調就不能二分。

### DFS / BFS 的 visited 時機

**進入時標記** vs **訪問時標記**,在有環圖行為不同。見 Ch 11。

### Dijkstra 負邊

不 work。碰到有負邊要用 Bellman-Ford。

### 遞迴深度

n = 10^5 的 tree / graph 遞迴會爆。`sys.setrecursionlimit(10**6)` 或改 iterative。

---

## 五、面試專屬陷阱

### 陷阱 1:題目說的不一定等你以為的

「subarray」是連續的,「subsequence」不一定連續。這對 LIS vs 最長連續子陣列的解完全不同。

面試中每個**術語**都 clarify。

### 陷阱 2:題目範圍沒說 → 你得問

「n 大約多少?」**一定**要問。

### 陷阱 3:題目暗示了演算法但你沒意識

- 「排序的 array」→ 很可能要二分 / 雙指針
- 「sum of subarray」→ 前綴和或 sliding window
- 「top k」→ heap / quickselect
- 「最短」→ BFS / Dijkstra / DP

訊號都在題目字眼裡。

### 陷阱 4:以為面試官給的 example 覆蓋了所有 case

面試官的 example **只是示意**。要自己造 edge case。

---

## 六、寫完的 self-review 清單

寫完按下面清單快速過一次:

1. [ ] 命名是否清楚
2. [ ] 邊界 check 是否在前面
3. [ ] 迴圈是否有對應的 update / terminate
4. [ ] 遞迴是否有 base case
5. [ ] 回傳型別是否符合題目
6. [ ] 空輸入是否處理
7. [ ] 時間複雜度是否達目標
8. [ ] 空間複雜度能否更優

---

## 自我檢核

- [ ] `[[0] * n] * m` 為什麼不能用?
- [ ] Python 的 `-7 % 3` 和 C 的 `-7 % 3` 差在哪?
- [ ] `sort` 在 Python 中是穩定的,Java 的 `Arrays.sort` 對 int 呢?(答:不穩定——它用 dual-pivot quicksort)
- [ ] `int(-7 / 2)` 和 `-7 // 2` 結果為什麼不同?
- [ ] 為什麼不能邊 iterate 邊 remove?

→ [Ch 28 題目辨識速查](./28-pattern-recognition.md)
