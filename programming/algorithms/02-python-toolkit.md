# Ch 2 — Python 的刀:標準庫速查

> 目標:把面試會用到的 Python 標準庫全部過一遍,知道每個容器的操作成本,以及該用哪把刀。

**寫 Python 面試不用 `collections` / `heapq` / `bisect` / `itertools`,等於赤手空拳**。本章是速查表 + 關鍵陷阱,看完記住這幾個 import 就對了:

```python
from collections import deque, defaultdict, Counter, OrderedDict
import heapq
import bisect
from functools import lru_cache, cache, reduce
from itertools import combinations, permutations, accumulate, product, chain
import math
```

---

## 1. `list` — 動態陣列

| 操作 | 複雜度 | 備註 |
|---|---|---|
| `arr[i]` | O(1) | 隨機訪問 |
| `arr.append(x)` | 攤銷 O(1) | |
| `arr.pop()` | O(1) | 尾端 |
| `arr.pop(0)` | **O(n)** | **不要!用 deque** |
| `arr.insert(i, x)` | O(n-i) | 前面插入很貴 |
| `x in arr` | O(n) | 要 O(1) 用 set |
| `arr[i:j]` | O(j-i) | 切片會複製 |
| `arr.sort()` | O(n log n) | in-place |
| `sorted(arr)` | O(n log n) | 回傳新 list |
| `arr.reverse()` | O(n) | |
| `arr[::-1]` | O(n) | 反轉但會複製 |

**常見 idiom**:

```python
arr.sort(key=lambda x: (x[0], -x[1]))   # 多鍵排序,第二鍵降序
arr.sort(reverse=True)
max(arr, key=lambda x: x.value)
```

---

## 2. `dict` — Hash Map

| 操作 | 複雜度 |
|---|---|
| `d[k]` / `d[k] = v` / `del d[k]` | 攤銷 O(1) |
| `k in d` | 攤銷 O(1) |
| `d.get(k, default)` | O(1) |
| `len(d)` | O(1) |

**Python 3.7+ 的 dict 保持插入順序**。這個 guarantee 很重要——面試時需要 LRU 可以用 `OrderedDict`,或直接用 `dict`(`.popitem(last=False)` 相當於 `popleft`)。

**最有用的 idiom**:

```python
d = {}
d[k] = d.get(k, 0) + 1         # 計數

# 更乾淨:
from collections import defaultdict
d = defaultdict(int)
d[k] += 1

d = defaultdict(list)
d[k].append(v)                  # 分組
```

---

## 3. `set` — Hash Set

| 操作 | 複雜度 |
|---|---|
| `s.add(x)` / `x in s` / `s.remove(x)` | O(1) |
| `s1 & s2` (交集) | O(min(|s1|, |s2|)) |
| `s1 \| s2` (聯集) | O(|s1| + |s2|) |
| `s1 - s2` (差集) | O(|s1|) |
| `s1 ^ s2` (對稱差) | O(|s1| + |s2|) |

**注意**:`set` 的元素必須 hashable。`list` 不行,`tuple` 可以。要把 list 裝進 set,先 `tuple(arr)`。

---

## 4. `collections.deque` — 雙端佇列

BFS / sliding window 的骨幹。

```python
from collections import deque

q = deque()
q.append(x)       # O(1) 右加
q.appendleft(x)   # O(1) 左加
q.pop()           # O(1) 右出
q.popleft()       # O(1) 左出
q[0], q[-1]       # O(1)
```

`deque(maxlen=k)` 可自動淘汰最舊元素,某些滑動視窗題很方便。

**索引中間**:`q[i]` 是 **O(n)**,不是 O(1)。要隨機訪問用 list。

---

## 5. `collections.Counter` — 計數器

```python
from collections import Counter

c = Counter("abbccc")
# Counter({'c': 3, 'b': 2, 'a': 1})

c.most_common(2)      # [('c', 3), ('b', 2)]
c['a'] += 1
c - Counter("ab")     # 支援減法,只保留正數
c & Counter("ac")     # 取小(交集)
```

**面試高頻用法**:anagram 判斷、子字串包含、頻率題。

```python
# 判斷 anagram
Counter(s1) == Counter(s2)
```

---

## 6. `heapq` — 最小堆

Python **只有 min-heap**。要 max-heap,value 取負數。

```python
import heapq

h = []
heapq.heappush(h, 3)       # O(log n)
heapq.heappush(h, 1)
heapq.heappush(h, 2)
x = heapq.heappop(h)       # 1, O(log n)

heapq.heapify(arr)         # O(n) in-place
heapq.nlargest(k, arr)     # O(n log k)
heapq.nsmallest(k, arr)    # O(n log k)

# max-heap 技巧
heapq.heappush(h, -x)
-heapq.heappop(h)

# 堆排 tuple,第一個是優先順序
heapq.heappush(h, (priority, item))
```

**nlargest / nsmallest 的實作是 O(n log k) 的 heap**,當 k 很小、n 很大時比 `sorted(arr)[-k:]`(O(n log n))快。

---

## 7. `bisect` — 有序陣列二分

你有一個已排序的 list,要找插入點或維護排序。

```python
import bisect

arr = [1, 3, 5, 7]
bisect.bisect_left(arr, 5)    # 2,第一個 >= 5 的位置
bisect.bisect_right(arr, 5)   # 3,第一個 > 5 的位置
bisect.insort(arr, 4)         # O(n),因為 list 插入 O(n)
```

**注意**:`insort` 雖然二分找位置(O(log n)),但 list 插入本身是 O(n)。要 O(log n) 插入請用 `SortedList`(見下)。

**面試高頻用法**:

- LIS(最長遞增子序列)的 O(n log n) 解。
- Count of smaller numbers after self。
- 區間問題找最近的端點。

---

## 8. `sortedcontainers.SortedList`(不是標準庫但 LeetCode 有)

```python
from sortedcontainers import SortedList

sl = SortedList()
sl.add(x)         # O(log n)
sl.remove(x)      # O(log n)
sl[0], sl[-1]     # O(log n)
sl.bisect_left(x) # O(log n)
```

**LeetCode 的 Python 環境預載了它**。面試 onsite 可以問面試官能不能用;不能用的話就自己用 heap + lazy delete 或平衡樹(罕見)。

---

## 9. `functools` — 記憶化與工具

```python
from functools import lru_cache, cache, reduce

@cache                    # Python 3.9+,無限大 memo
def fib(n):
    if n < 2: return n
    return fib(n-1) + fib(n-2)

@lru_cache(maxsize=None)  # 跟 @cache 等價,舊版 Python 用這個
def fib(n): ...

reduce(lambda a, b: a + b, arr, 0)   # 累加
```

**DP 面試大殺器**:直接 `@cache` + 遞迴寫法,連 memo dict 都不用寫。

**陷阱**:`@cache` 要求參數 hashable。傳 list 不行,轉 tuple。

---

## 10. `itertools` — 組合與迭代

```python
from itertools import combinations, permutations, product, accumulate, chain

list(combinations([1,2,3], 2))   # [(1,2), (1,3), (2,3)]
list(permutations([1,2,3], 2))   # [(1,2), (1,3), (2,1), ...]
list(product([0,1], repeat=3))   # 2^3 = 8 種
list(accumulate([1,2,3,4]))      # [1, 3, 6, 10] 前綴和
list(chain([1,2], [3,4]))        # [1,2,3,4] 串接
```

**前綴和**直接 `accumulate`。

---

## 11. `math` 常用

```python
import math

math.gcd(12, 8)           # 4
math.lcm(4, 6)            # 12 (3.9+)
math.isqrt(17)            # 4 (整數平方根,不浮點)
math.floor, math.ceil, math.log2
math.inf, -math.inf       # 無窮大,寫初值很好用
math.comb(5, 2)           # C(5,2) = 10 (3.8+)
math.perm(5, 2)           # P(5,2) = 20 (3.8+)
```

**用 `math.inf` 而不是 `float('inf')` 或 `sys.maxsize`**,更清楚。

---

## 12. 字串的刀

```python
s.isalpha() / s.isdigit() / s.isalnum()
s.lower() / s.upper() / s.swapcase()
s.strip() / s.lstrip() / s.rstrip()
s.split(" ") / " ".join(arr)
s.replace("a", "b")
s.find("ab")    # 沒找到回 -1,不會 raise
s.index("ab")   # 沒找到會 raise
s.count("a")
ord("a")  # 97
chr(97)   # 'a'
```

**字串比較是字典序**,`"abc" < "abd"` → True。面試寫 comparator 很方便。

---

## 13. 面試高頻技巧速查

### 前綴和

```python
from itertools import accumulate
prefix = [0] + list(accumulate(arr))   # prefix[i+1] - prefix[j] = sum(arr[j:i+1])
```

### 座標方向

```python
DIRS = [(0,1), (1,0), (0,-1), (-1,0)]         # 4 方向
DIRS_8 = [(dx,dy) for dx in [-1,0,1] for dy in [-1,0,1] if (dx,dy) != (0,0)]
```

### 二維矩陣邊界檢查

```python
def in_bounds(r, c, rows, cols):
    return 0 <= r < rows and 0 <= c < cols
```

### 自訂 comparator(需要 `functools.cmp_to_key`)

```python
from functools import cmp_to_key
arr.sort(key=cmp_to_key(lambda a, b: a - b))   # 升序
```

### 一行交換

```python
a, b = b, a
```

### Zip 轉置矩陣

```python
matrix = [[1,2,3], [4,5,6]]
transposed = list(zip(*matrix))   # [(1,4), (2,5), (3,6)]
```

### Tuple 多鍵比較

```python
# 按 a 升序,同 a 按 b 降序
arr.sort(key=lambda x: (x.a, -x.b))
```

### List comprehension 過濾

```python
[x for x in arr if x > 0]
# vs
list(filter(lambda x: x > 0, arr))   # 通常前者更快更 Pythonic
```

---

## 14. 幾個容易踩雷的細節

**預設參數用 mutable 會炸**:

```python
# BAD
def f(arr=[]):
    arr.append(1)
    return arr
# 第二次呼叫 arr 還是上次那個

# GOOD
def f(arr=None):
    if arr is None:
        arr = []
```

**淺拷貝 vs 深拷貝**:

```python
a = [[1,2], [3,4]]
b = a.copy()       # 淺拷貝,b[0] is a[0]
b[0].append(99)    # a 也變了!

import copy
c = copy.deepcopy(a)  # 真正複製
```

面試改二維矩陣時常踩,牢記。

**`range` 不是 list**:

```python
r = range(10)
r[0]       # OK
r[-1]      # OK
list(r)    # 要 O(n)
```

好處是不佔記憶體,`range(10**9)` 沒事。

---

## 自我檢核

- [ ] 面試題給你 1 GB 資料,要找 top 100,你選哪個函式?
- [ ] 用 `defaultdict(list)` 和 `{}` + `setdefault` 寫出同一個「分組」邏輯。
- [ ] 為什麼 `@cache` 的函數不能傳 list?
- [ ] `bisect.insort` 整體複雜度為什麼不是 O(log n)?
- [ ] 寫一行 Python 算 `arr` 的前綴和。

→ [Ch 3 Array / String:雙指針與滑動視窗](./03-two-pointers-sliding-window.md)
