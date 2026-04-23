# Ch 17 — Backtracking:剪枝才是關鍵

> 目標:背熟 backtracking 模板,並理解「為什麼沒剪枝的 backtracking 就是 brute force」。

## 什麼是 backtracking

**在「選擇樹」上做 DFS,每做一個選擇前改狀態、返回後還原**。

概念:

```
for 所有可能的選擇:
    做選擇(改狀態)
    遞迴
    撤銷選擇(還原狀態)
```

看起來是遞迴,本質是 DFS。

## 模板

```python
def backtrack(path, choices):
    if is_goal(path):
        result.append(path[:])    # 複製一份,path 之後還會改
        return
    for choice in valid_choices(path, choices):
        path.append(choice)       # 做選擇
        backtrack(path, choices)
        path.pop()                # 撤銷
```

**三個設計決策**:

1. **Goal**:什麼時候算完成?(通常 path 長度達目標、或到 leaf)
2. **Choices**:當前可以選什麼?(通常要避開已選過的)
3. **撤銷**:每種狀態改法都要有對應的還原。

---

## 經典題 1:排列(Permutations)

### Permutations (46)

```python
def permute(nums):
    res = []
    def bt(path, used):
        if len(path) == len(nums):
            res.append(path[:])
            return
        for i, x in enumerate(nums):
            if used[i]: continue
            used[i] = True
            path.append(x)
            bt(path, used)
            path.pop()
            used[i] = False
    bt([], [False] * len(nums))
    return res
```

**為什麼要 `used`**:排列不同於組合,順序重要,且同一數字不能重複使用。

### Permutations II (47,有重複)

`[1, 1, 2]` 應該輸出 3 種(不是 6 種)。

**關鍵剪枝**:先 sort,然後遇到「跟前一個相同、且前一個沒用過」就跳過。

```python
def permute_unique(nums):
    nums.sort()
    res = []
    def bt(path, used):
        if len(path) == len(nums):
            res.append(path[:])
            return
        for i in range(len(nums)):
            if used[i]: continue
            if i > 0 and nums[i] == nums[i-1] and not used[i-1]:
                continue    # 關鍵剪枝
            used[i] = True
            path.append(nums[i])
            bt(path, used)
            path.pop()
            used[i] = False
    bt([], [False] * len(nums))
    return res
```

**`not used[i-1]` 的直覺**:確保「相同元素」的使用順序固定(只允許「前一個先用」)。這避免了枚舉到「換個順序但是排列相同」的重複結果。

---

## 經典題 2:組合(Combinations)

### Combinations (77)

從 `1..n` 選 k 個。

```python
def combine(n, k):
    res = []
    def bt(start, path):
        if len(path) == k:
            res.append(path[:])
            return
        for i in range(start, n + 1):
            path.append(i)
            bt(i + 1, path)     # 下次從 i+1 開始,避免重複組合
            path.pop()
    bt(1, [])
    return res
```

**組合 vs 排列差在哪**:組合用 `start` 參數,每次往後選——自動去重。

### Subsets (78)

```python
def subsets(nums):
    res = []
    def bt(start, path):
        res.append(path[:])     # 每個節點都是一個子集
        for i in range(start, len(nums)):
            path.append(nums[i])
            bt(i + 1, path)
            path.pop()
    bt(0, [])
    return res
```

### Combination Sum (39,允許重複用)

```python
def combination_sum(candidates, target):
    res = []
    candidates.sort()
    def bt(start, path, remain):
        if remain == 0:
            res.append(path[:])
            return
        for i in range(start, len(candidates)):
            if candidates[i] > remain: break    # 剪枝!sorted 之後直接 break
            path.append(candidates[i])
            bt(i, path, remain - candidates[i])    # 注意是 i,不是 i+1
            path.pop()
    bt(0, [], target)
    return res
```

**兩個技巧**:

- **sort 後 break**:因為 sorted,一旦超過 remain,後面更大的也都不用試。
- **傳 i 不是 i+1**:允許同一元素重複使用。

---

## 經典題 3:分割(Partition)

### Palindrome Partitioning (131)

> 把字串切成全是回文的子串。

```python
def partition(s):
    res = []
    def is_palin(l, r):
        while l < r:
            if s[l] != s[r]: return False
            l, r = l + 1, r - 1
        return True
    def bt(start, path):
        if start == len(s):
            res.append(path[:])
            return
        for end in range(start, len(s)):
            if is_palin(start, end):
                path.append(s[start:end+1])
                bt(end + 1, path)
                path.pop()
    bt(0, [])
    return res
```

---

## 經典題 4:棋盤類

### N-Queens (51)

```python
def solve_n_queens(n):
    res = []
    cols, d1, d2 = set(), set(), set()   # 列、對角、反對角
    def bt(row, board):
        if row == n:
            res.append(board[:])
            return
        for c in range(n):
            if c in cols or (row - c) in d1 or (row + c) in d2:
                continue
            cols.add(c); d1.add(row - c); d2.add(row + c)
            board.append('.' * c + 'Q' + '.' * (n - c - 1))
            bt(row + 1, board)
            board.pop()
            cols.remove(c); d1.remove(row - c); d2.remove(row + c)
    bt(0, [])
    return res
```

**心法**:一行一個 queen,用三個 set 快速判斷衝突:
- `cols`:佔用的列
- `d1 = row - col`:主對角(↘)
- `d2 = row + col`:副對角(↙)

對角線的編碼技巧要記。

### Sudoku Solver (37)

```python
def solve_sudoku(board):
    rows = [set() for _ in range(9)]
    cols = [set() for _ in range(9)]
    boxes = [set() for _ in range(9)]
    empties = []
    for r in range(9):
        for c in range(9):
            if board[r][c] == '.':
                empties.append((r, c))
            else:
                d = board[r][c]
                rows[r].add(d); cols[c].add(d)
                boxes[(r // 3) * 3 + c // 3].add(d)

    def bt(i):
        if i == len(empties): return True
        r, c = empties[i]
        b = (r // 3) * 3 + c // 3
        for d in '123456789':
            if d in rows[r] or d in cols[c] or d in boxes[b]: continue
            board[r][c] = d
            rows[r].add(d); cols[c].add(d); boxes[b].add(d)
            if bt(i + 1): return True
            board[r][c] = '.'
            rows[r].remove(d); cols[c].remove(d); boxes[b].remove(d)
        return False
    bt(0)
```

---

## 剪枝才是關鍵

Backtracking 最壞是指數級,面試題能 AC 靠的是**剪枝**:

### 1. 有序 break(Combination Sum)

sort 後遇到超過就 break,不再往後試。

### 2. 重複剪枝(Permutations II)

先 sort,跳過「跟前一個相同且前一個未使用」的選擇。

### 3. 可行性剪枝(N-Queens)

當前行的每一列試之前先檢查衝突,不合法就不遞迴。

### 4. 最優性剪枝

做最優化 backtracking(求 min/max)時,當前部分解已比已知最優差就不繼續。

```python
# 範例:找最短 path
if current_length >= best:
    return   # 不可能更好,剪掉
```

---

## Backtracking 的複雜度

通常是指數級。寫出來後:

- 上界:選擇樹的節點數 × 每節點的工作量
- Permutations: O(n × n!)
- Subsets: O(n × 2^n)
- N-Queens: O(n!)(剪枝後實際遠小於)

面試被問「你的複雜度」,說「最壞 O(2^n) / O(n!),但剪枝下實際快很多」就過關。

---

## 陷阱

### 陷阱 1:忘了 `path[:]`

```python
res.append(path)     # BAD,存的是 reference
res.append(path[:])  # GOOD,存 copy
```

後面 path 還在改,忘了 copy 會讓所有答案變成空的。

### 陷阱 2:撤銷不對稱

做選擇改了三個 state,撤銷時只還原兩個——後續會錯。每改一個就要對應一個 undo。

### 陷阱 3:遞迴參數寫成 mutable default

```python
# BAD
def bt(path=[]):    # 每次呼叫共用同一個 list!
    ...
```

### 陷阱 4:沒想剪枝就寫

Naive backtracking 通常 TLE。寫之前花 30 秒想「哪裡可以剪」。

---

## 自我檢核

- [ ] 組合題的 `start` 參數作用是什麼?
- [ ] Permutations II 的去重剪枝為什麼要先 sort?
- [ ] N-Queens 對角線怎麼編碼?兩條對角各用什麼?
- [ ] 為什麼 `res.append(path)` 會出錯?
- [ ] 寫一下 Combination Sum II(每元素只能用一次)。

→ [Ch 18 Divide & Conquer](./18-divide-conquer.md)
