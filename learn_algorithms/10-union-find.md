# Ch 10 — Union-Find(並查集)

> 目標:把 union-find 寫到閉著眼睛也能寫出的程度,並辨識它的典型訊號——「連通性 / 動態合併」。

## 解決什麼問題

**動態維持一群元素的「等價類」(equivalence classes)**。支援兩個操作:

- `find(x)`:x 屬於哪個等價類(用「代表元素」表示)。
- `union(x, y)`:合併 x 和 y 所在的兩個等價類。

應用訊號:

- 連通分量數量(Number of Connected Components)
- 判斷兩點是否連通
- 動態添加邊 / 動態合併群組
- Kruskal MST(Ch 14 會用)
- 檢測環(無向圖)

## 實作(背熟這個模板)

```python
class DSU:
    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n
        self.count = n      # 等價類數量(選用)

    def find(self, x):
        # Path compression
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x, y):
        px, py = self.find(x), self.find(y)
        if px == py: return False   # 已在同一組
        # Union by rank:掛矮的到高的
        if self.rank[px] < self.rank[py]:
            px, py = py, px
        self.parent[py] = px
        if self.rank[px] == self.rank[py]:
            self.rank[px] += 1
        self.count -= 1
        return True
```

**兩個優化**:

1. **Path compression**:`find` 時把路上所有節點直接掛到根。
2. **Union by rank(或 by size)**:合併時把小樹掛到大樹上。

兩者**都有**,操作攤銷複雜度是 **α(n)**,α 是反阿克曼函數,在所有實際 n 下 ≤ 4。可以當作 O(1)。

只做 path compression(無 rank),攤銷 O(log n)。只做 union by rank(無 compression),最壞 O(log n)。**面試就兩個都做,不折衷**。

## 節點是字串 / 任意對象?

用 dict 當 parent,初始化 lazy:

```python
class DSU:
    def __init__(self):
        self.parent = {}
        self.rank = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x, y):
        px, py = self.find(x), self.find(y)
        if px == py: return False
        if self.rank[px] < self.rank[py]: px, py = py, px
        self.parent[py] = px
        if self.rank[px] == self.rank[py]: self.rank[px] += 1
        return True
```

---

## 經典題

### 1. Number of Islands (200) 的 DSU 解

> 2D grid,`1` 是陸地 `0` 是水,找連通陸地塊數。

DFS / BFS 更直觀,但 **DSU 可以解**,尤其當題目變成「動態加陸地,每加一次問當前有幾塊」(Number of Islands II, 305)時,DFS 要重跑,DSU 只要 union。

```python
def num_islands_ii(m, n, positions):
    dsu = DSU(m * n)
    dsu.count = 0       # 陸地塊計數
    grid = [[0] * n for _ in range(m)]
    ans = []
    for r, c in positions:
        if grid[r][c] == 1:
            ans.append(dsu.count)
            continue
        grid[r][c] = 1
        dsu.count += 1
        for dr, dc in [(0,1),(1,0),(0,-1),(-1,0)]:
            nr, nc = r+dr, c+dc
            if 0 <= nr < m and 0 <= nc < n and grid[nr][nc] == 1:
                dsu.union(r*n + c, nr*n + nc)
        ans.append(dsu.count)
    return ans
```

**2D 壓扁成 1D**:`r * n + c`。這是 DSU 處理 grid 的通用技巧。

### 2. Accounts Merge (721)

> 同一人可能有多個 email,email 相同代表同一人。合併帳戶。

```python
def accounts_merge(accounts):
    dsu = DSU()
    email_to_name = {}
    for account in accounts:
        name = account[0]
        for email in account[1:]:
            email_to_name[email] = name
            dsu.union(account[1], email)   # 把該人所有 email 合併

    # 收集每個 root 下的 emails
    from collections import defaultdict
    groups = defaultdict(list)
    for email in email_to_name:
        groups[dsu.find(email)].append(email)

    return [[email_to_name[emails[0]]] + sorted(emails) for emails in groups.values()]
```

**訊號**:「相同元素要歸同一組」「合併操作無限次」→ DSU。

### 3. Redundant Connection (684)

> 找一條邊移除後讓圖變成樹。

```python
def find_redundant_connection(edges):
    dsu = DSU(len(edges) + 1)
    for u, v in edges:
        if not dsu.union(u, v):
            return [u, v]
```

**心法**:加邊時 `union` 返回 False 代表這條邊的兩端已經連通,加上去就形成環。直接回傳。

### 4. Satisfiability of Equality Equations (990)

> 一堆形如 `a==b` 或 `a!=b` 的式子,判斷是否有解。

先處理所有 `==`(用 DSU 合併),再檢查所有 `!=`(看兩端是不是已經在同一組)。

```python
def equations_possible(equations):
    dsu = DSU(26)
    for eq in equations:
        if eq[1:3] == '==':
            dsu.union(ord(eq[0]) - 97, ord(eq[3]) - 97)
    for eq in equations:
        if eq[1:3] == '!=':
            if dsu.find(ord(eq[0]) - 97) == dsu.find(ord(eq[3]) - 97):
                return False
    return True
```

**兩遍掃的順序很重要**:`==` 先合併,`!=` 後檢查。反過來會漏。

### 5. Kruskal MST(Ch 14 會詳講)

> 按邊權排序,依序加入不成環的邊。

```python
def kruskal(n, edges):   # edges: [(w, u, v)]
    edges.sort()
    dsu = DSU(n)
    mst_weight = 0
    for w, u, v in edges:
        if dsu.union(u, v):
            mst_weight += w
    return mst_weight
```

DSU 是 Kruskal 的核心。

---

## 訊號辨識

**何時想到 DSU**:

- 題目有「connected」、「groups」、「merge」、「equivalent」
- 有「加一條邊 / 加一個元素,問當前狀態」的動態成分
- 圖題要算連通分量數量
- 需要判斷「加這條邊會不會造成環」

**何時 DSU 不如 DFS/BFS**:

- 圖是靜態的、只問一次連通性 → DFS/BFS 更直觀
- 需要路徑 / 距離 → DSU 只告訴你「是否同組」,沒路徑資訊
- 需要反向操作(刪邊) → DSU **不支援刪除**

## DSU 的進階變形(點到即可)

### Weighted DSU

節點之間有權重關係(例如「a 是 b 的 2 倍」),find 時把路徑上的權重累積。

應用:Evaluate Division (399)——除法等式 `a/b = 2.0`,查 `x/y = ?`。可以 DFS 也可以 weighted DSU。

### 帶 Rollback 的 DSU

線下題可能需要「撤銷 union」。方法是不用 path compression,用 union by rank/size 並記錄操作 stack。

面試罕見,知道存在即可。

---

## 常見陷阱

### 陷阱 1:Rank 維護錯

合併時,rank 只有在「兩個樹 rank 相等」時才 +1。很多人寫成每次合併都 +1,退化成 O(log n)。

### 陷阱 2:Find 沒做 path compression

寫 `return self.parent[x] if self.parent[x] == x else self.find(self.parent[x])`,看起來對但**沒更新 `self.parent[x]`**。要改成:

```python
if self.parent[x] != x:
    self.parent[x] = self.find(self.parent[x])
return self.parent[x]
```

### 陷阱 3:用錯代表元素比較

要比「是否同組」,必須 `find(x) == find(y)`,不能 `parent[x] == parent[y]`(path compression 沒做完時可能不相等但 root 相同)。

---

## 自我檢核

- [ ] Union-find 的「α(n)」是什麼?實際值大概多少?
- [ ] 只做 path compression 不做 union by rank,最壞複雜度?
- [ ] 為什麼 Kruskal 用 DSU 而不用 DFS?
- [ ] DSU 能做動態刪邊嗎?為什麼?
- [ ] 寫一下 `DSU(n).union(0, 1)` 後 `parent[1]` 的值。

→ [Ch 11 Graph:BFS / DFS](./11-graph-bfs-dfs.md)
