# Ch 4 — Hash 的威力與陷阱

> 目標:搞懂 hash 的實際成本、什麼時候是救世主什麼時候是陷阱、幾個把 hash 用到極致的經典 pattern。

## Hash 是什麼

Hash table 把「平均 O(n) 的查找」壓到「平均 O(1)」。這個「平均」很重要——**最壞情況仍是 O(n)**(全部 hash collision)。面試通常不需要構造最壞情況,但被問到時要會答。

Python 的 `dict` / `set` 都是 hash table。實作細節:Python 3.6+ 用 compact hash table,保持插入順序。

## 三大用法

Hash 在面試題裡通常扮演三種角色:

### 1. 去重 / 查詢存在性(set)

```python
seen = set()
for x in arr:
    if x in seen:
        return True
    seen.add(x)
```

經典題:Contains Duplicate (217)、Happy Number (202)、Valid Sudoku (36)。

### 2. 計數 / 頻率(Counter / dict)

```python
from collections import Counter
cnt = Counter(arr)
# 找出現次數最多的前 k 個
cnt.most_common(k)
```

經典題:Top K Frequent Elements (347)、Valid Anagram (242)、Group Anagrams (49)。

### 3. 「補集查找」(dict 記錄需要什麼)

最強大也最優雅的用法。

```python
# Two Sum (1)
def two_sum(arr, target):
    seen = {}  # value -> index
    for i, x in enumerate(arr):
        if target - x in seen:
            return [seen[target - x], i]
        seen[x] = i
```

關鍵心法:**邊掃邊查,不是先建 hash 再查**。先建再查會處理重複元素錯誤(自己跟自己配對)。

## 前綴和 + Hash:subarray 殺器

很多 subarray 題的模板:

```
你要找滿足 f(subarray[i..j]) = target 的所有 (i, j)
  ↓
如果 f 是可拆解的(sum、xor、count),記 prefix[r]
  ↓
條件變成 prefix[r] - prefix[l-1] = target
  ↓
→ 等於找 prefix[l-1] = prefix[r] - target
  ↓
邊掃 r 邊查 hash(記錄到目前為止出現過的所有 prefix 值)
```

### Subarray Sum Equals K (560)

```python
def subarray_sum(arr, k):
    count = 0
    prefix = 0
    seen = {0: 1}   # prefix=0 出現過 1 次(空 prefix)
    for x in arr:
        prefix += x
        # 我想找 subarray[l..r] sum = k
        # 即 prefix - prefix_l = k → prefix_l = prefix - k
        count += seen.get(prefix - k, 0)
        seen[prefix] = seen.get(prefix, 0) + 1
    return count
```

**為什麼 sliding window 不 work(再提一次)**:`arr` 可能有負數,擴張視窗不是單調增加 sum。

**seen[0] = 1 這個初始化**:代表「空 prefix 前面有 1 份」,處理 subarray 從 index 0 開始的情形。這是最容易忘的點。

### 變形:連續子陣列和為 k 的倍數 (523)

`(prefix[r] - prefix[l-1]) % k == 0`
等價於 `prefix[r] % k == prefix[l-1] % k`
所以記 `prefix % k` 而不是 `prefix` 本身。

### 變形:Continuous Subarray XOR (1442)

相同思路,`prefix_xor[r] ^ prefix_xor[l-1] = target`。

## 進階 idiom:Group By

```python
from collections import defaultdict

def group_anagrams(strs):
    groups = defaultdict(list)
    for s in strs:
        key = tuple(sorted(s))    # 或 ''.join(sorted(s))
        groups[key].append(s)
    return list(groups.values())
```

**Key 的選擇是學問**:
- Anagram:sorted 字元或字元計數 tuple
- 相似矩形:(rows, cols) 正規化
- 平移等價:都減去第一個點

## 陷阱清單

### 陷阱 1:Key 必須 hashable

```python
d = {}
d[[1,2]] = 'x'    # TypeError: unhashable type: 'list'
d[(1,2)] = 'x'    # OK, tuple 可以
d[frozenset({1,2})] = 'x'  # OK
```

二維 DP 想用 `(i, j)` 當 key,寫成 `d[i, j]` 也可以(Python 會自動成 tuple),但 `d[[i, j]]` 不行。

### 陷阱 2:浮點數當 key

```python
d = {0.1 + 0.2: 'x'}   # key 是 0.30000000000000004
d[0.3]                 # KeyError
```

不要拿浮點當 key。要嘛轉整數(乘 10^k),要嘛用 `round(x, 6)` 當近似 key(但很危險)。

### 陷阱 3:Mutable 物件當 key 後又改它

```python
key = [1, 2]
# 不能當 key,所以這個例子用 tuple 搬到 frozenset:
key = frozenset({1, 2})
d[key] = 'x'
# key 是 frozenset 不能 mutate,所以沒這個問題
```

Python 用 hashable 的設計避免了這個陷阱,但其他語言(如 Java 的 `HashMap`)會踩——把 mutable 物件當 key,改它之後 hash 值變了,找不到 entry。

### 陷阱 4:漏掉「自己」

Two Sum 新手常寫:

```python
# BAD
d = {x: i for i, x in enumerate(arr)}  # 先建完
for i, x in enumerate(arr):
    if target - x in d and d[target - x] != i:
        return [i, d[target - x]]
```

這版 `arr = [3, 3], target = 6` 會錯——兩個 3 互相指向對方但 index 同一個。必須邊建邊查。

### 陷阱 5:預設值陷阱(defaultdict)

```python
from collections import defaultdict
d = defaultdict(list)
if d["never_inserted"]:   # 這行會「插入」一個空 list!
    ...
print(d)   # {'never_inserted': []}
```

**讀取不存在的 key 會創造它**。檢查存在用 `"key" in d`,不要 `d["key"]`。

### 陷阱 6:hash collision 的最壞情況

Python 的 hash 對字串有隨機種子(PYTHONHASHSEED),但對 int 是 `hash(n) = n`。理論上可以構造攻擊(給一堆同餘 2^k 的 int),但面試場景幾乎不會被問。

---

## 高頻題型範例

### Longest Consecutive Sequence (128)

> 給一個未排序 array,找最長的連續整數序列長度。要求 O(n)。

```python
def longest_consecutive(arr):
    s = set(arr)
    best = 0
    for x in s:
        # 只從「序列開頭」開始數,避免重複
        if x - 1 not in s:
            y = x
            while y + 1 in s:
                y += 1
            best = max(best, y - x + 1)
    return best
```

**關鍵心法**:`x - 1 not in s` 保證每個序列只被從起點數一次,總工作量是 O(n) 不是 O(n²)。很多人寫 for x in s,遇到中間點也往兩邊擴,會 TLE。

### First Missing Positive (41, Hard)

> 給 unsorted arr,找最小的缺失正整數。O(n) time,O(1) extra space。

不能用 hash(要 O(1) space)。這題的妙解是「原地 hash」——把 `arr[i]` 放到 index `arr[i] - 1` 的位置,用 array 本身當 hash table。

```python
def first_missing_positive(arr):
    n = len(arr)
    for i in range(n):
        while 1 <= arr[i] <= n and arr[arr[i] - 1] != arr[i]:
            # 把 arr[i] 放到它該在的位置
            j = arr[i] - 1
            arr[i], arr[j] = arr[j], arr[i]
    for i in range(n):
        if arr[i] != i + 1:
            return i + 1
    return n + 1
```

這題不是常規 hash,但把 hash 思想用到極致。被問到「hash 但不准用 hash」,先往「原地 hash」想。

---

## 自我檢核

- [ ] Two Sum 為什麼要邊掃邊查,不能先建完 hash 再查?
- [ ] 前綴和 + hash 的 `seen[0] = 1` 初始化的意義?
- [ ] `subarray sum % k == 0` 的 hash key 該記什麼?
- [ ] `defaultdict(list)` 什麼時候會意外插入 key?
- [ ] 用 set 在 O(n) 解最長連續序列,關鍵的剪枝條件是?

→ [Ch 5 Stack / Queue / Monotonic Stack](./05-stack-queue.md)
