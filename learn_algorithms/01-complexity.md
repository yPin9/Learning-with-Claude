# Ch 1 — 複雜度:面試官真正想聽的答案

> 目標:把 Big-O 從學術概念變成面試場上的反射動作,並認識 Python 特有的複雜度陷阱。

## 什麼是「真正想聽的答案」

你寫完一個題,面試官問:「What's the time complexity?」

**錯誤回答**:
- "Big-O is an upper bound on growth rate..."(在背定義)
- "O(n²)"(沒說清楚 n 是什麼)
- "O(n)"(但實際是 O(n log n),你沒算 sort)

**正確回答**長這樣:

> "Time is O(n log n), where n is the length of the input array. The sort dominates; the scan after that is O(n). Space is O(n) for the hash map, or O(1) extra if the output isn't counted."

組成三要素:
1. **明確的界**(不是 "linear",要 O(n))
2. **定義 n 是什麼**(題目常有多個變數)
3. **指出瓶頸在哪**(sort?recursion depth?scan?)

## Big-O 的心智模型

面試題 n 通常在 10³–10⁵。把這個表背起來:

| 複雜度 | 每秒可處理 n(概估) | 典型範例 |
|---|---|---|
| O(1) | 無限 | hash lookup、陣列索引 |
| O(log n) | 10¹⁸ | binary search |
| O(n) | 10⁸ | single pass |
| O(n log n) | 10⁷ | sort、heap-based |
| O(n²) | 10⁴ | brute force 兩兩比較 |
| O(n³) | 500 | Floyd-Warshall、三重 DP |
| O(2ⁿ) | 20 | 子集枚舉、不剪枝 backtracking |
| O(n!) | 10 | 排列枚舉 |

## 從約束倒推演算法(超重要)

這是面試作弊技巧:**題目給你的 n 範圍就是提示**。

```
n ≤ 10         O(n!) O(2^n) 可接受    → 枚舉 / 回溯
n ≤ 20         O(2^n) 可接受           → 狀壓 DP / 子集枚舉
n ≤ 500        O(n^3) 可接受           → Floyd / 三維 DP
n ≤ 5000       O(n^2) 可接受           → 二維 DP
n ≤ 10^5       O(n log n)              → sort / 二分 / 堆
n ≤ 10^6       O(n)                    → 單次掃描 / 雙指針
n ≤ 10^9       O(log n) / O(√n)       → 二分答案 / 數學
```

看到 n ≤ 20 而你想的是 O(n²) 的解?多半是想少了,應該是 2ⁿ 的子集枚舉。看到 n = 10⁵ 而你寫 O(n²)?TLE 在等你。

## 攤銷(Amortized)

`list.append(x)` 在 Python 是**攤銷 O(1)**,不是絕對 O(1)。底層是動態擴容,偶爾 copy 整個陣列(O(n)),但均攤下來是 O(1)。

面試官要聽的:

> "Append is amortized O(1)."

不要說 "constant time",不精確。

其他攤銷場景:
- `deque.append` / `deque.popleft`:**O(1) worst case**,不是攤銷。
- `dict[key] = v`:攤銷 O(1),最壞 O(n)(rehash)。
- Union-Find + path compression:**攤銷 α(n)**,幾乎 O(1)。

## Python 特有的陷阱(面試最常掉分處)

### 1. `list.pop(0)` 是 **O(n)**

```python
# BAD:每次 pop(0) 都要搬整個 list
q = [1, 2, 3, 4]
while q:
    x = q.pop(0)   # O(n)!
# 整體 O(n²)
```

正解:

```python
from collections import deque
q = deque([1, 2, 3, 4])
while q:
    x = q.popleft()  # O(1)
```

### 2. `list.insert(0, x)` 也是 **O(n)**

一樣理由。需要 O(1) 前插就用 `deque`。

### 3. 字串拼接在迴圈中是 **O(n²)**

```python
# BAD
s = ""
for c in chars:
    s += c   # 每次建新字串,O(len(s))
```

正解:

```python
s = "".join(chars)   # O(n)
```

或用 list buffer:

```python
buf = []
for c in chars:
    buf.append(c)
s = "".join(buf)
```

### 4. `in` 對 list 是 O(n),對 set/dict 是 O(1)

```python
# BAD:整體 O(n²)
seen = []
for x in arr:
    if x in seen:
        ...
    seen.append(x)

# GOOD:整體 O(n)
seen = set()
for x in arr:
    if x in seen:
        ...
    seen.add(x)
```

### 5. 切片 `arr[i:j]` 是 **O(j-i)**

不是 O(1),切片會複製。遞迴時傳切片會讓複雜度多一個因子:

```python
# BAD:整體 O(n²),不是 O(n)
def solve(arr):
    if not arr: return
    solve(arr[1:])   # 每次 O(n) 切片

# GOOD:傳 index
def solve(arr, i):
    if i == len(arr): return
    solve(arr, i + 1)
```

### 6. 遞迴深度預設 **1000**

```python
import sys
sys.setrecursionlimit(10**6)
```

n = 10⁵ 的 tree / graph 遞迴會爆 stack。要嘛 iterative,要嘛調 limit(並告訴面試官你知道)。

### 7. 整數沒有 overflow(但不免費)

Python 的 `int` 自動升級成 bignum。好處是不用擔心 overflow,壞處是超過 machine word 的運算是 O(log value),不是 O(1)。

面試中通常忽略這件事,但被問「Is Python's integer addition O(1)?」正確答案:

> "For small ints yes, arbitrary precision ints are O(log value) in time and space."

### 8. 負數取模 `%` 行為不同於 C

```python
-7 % 3   # Python: 2
         # C:      -1
```

Python 的 `%` 結果跟除數同號。寫環狀 index 時很方便(`(i - 1) % n` 直接給你正數),但轉 C++ 解答時要注意。

## 複雜度符號的細節

| 符號 | 意義 | 白話 |
|---|---|---|
| O(f) | 上界 | 不會比 f 慢 |
| Ω(f) | 下界 | 不會比 f 快 |
| Θ(f) | 緊界 | 剛好是 f |
| o(f) | 嚴格上界 | 比 f 嚴格慢 |

面試幾乎只用 O。但被問到「merge sort 的下界是?」要能答 Ω(n log n)(這是 comparison sort 的理論下界)。

## Big-O 不是一切

兩個 O(n) 的演算法常數可能差 10 倍。實務要考慮:

- **Cache locality**:連續 array 比 linked list 快到飛起。
- **常數**:hash table 有 hash 開銷,小 n 可能比 sorted array 還慢。
- **最壞 vs 平均**:quicksort 平均 O(n log n) 但最壞 O(n²)。

面試通常不為常數扣分,但被問「哪個更快」時,能講這些細節會加分。

## 練手題

快速判斷下列程式碼的複雜度(答案在最下面,先自己想):

```python
# A
def f(n):
    for i in range(n):
        for j in range(i, n):
            pass

# B
def f(arr):
    return sorted(arr, reverse=True)[:10]

# C
def f(n):
    s = ""
    for i in range(n):
        s += str(i)
    return s

# D
def f(arr):
    return list(set(arr))

# E
def f(n):
    if n <= 1: return 1
    return f(n-1) + f(n-2)
```

<details>
<summary>答案</summary>

- A: O(n²),內層平均 n/2。
- B: O(n log n),排序是瓶頸,切片 O(1)(10 是常數)。若用 heap `nlargest` 可降到 O(n log 10) = O(n)。
- C: **O(n²)!** 字串拼接陷阱。每次 `s += str(i)` 都是 O(len(s))。
- D: O(n),set 建構、list 建構各 O(n)。
- E: O(2ⁿ),沒 memo 的 naive Fibonacci。
</details>

## 自我檢核

- [ ] 給你 n ≤ 18,你的第一反應是什麼演算法範式?
- [ ] `sorted(arr)` 和 `arr.sort()` 的差別?後者的空間複雜度?
- [ ] 為什麼 `heapq.heappush` 是 O(log n) 不是 O(1)?
- [ ] `set1 & set2` 的時間複雜度?(答案:O(min(|s1|, |s2|)))
- [ ] 寫一個在 Python 裡看起來 O(n) 但實際 O(n²) 的迴圈。

→ [Ch 2 Python 的刀:標準庫速查](./02-python-toolkit.md)
