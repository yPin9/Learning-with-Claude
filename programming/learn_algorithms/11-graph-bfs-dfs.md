# Ch 11 — Graph:BFS / DFS

> 目標:把圖的表示法、BFS/DFS 模板、典型訊號整合起來。看到「grid」、「有向無環」、「最短步數」時能直覺選對工具。

## 圖的表示法

三種常見:

### 1. Adjacency List(最常用)

```python
from collections import defaultdict
graph = defaultdict(list)
for u, v in edges:
    graph[u].append(v)
    graph[v].append(u)   # 無向圖才加這行
```

優點:空間 O(V + E),遍歷鄰居 O(deg(v))。**面試首選**。

### 2. Adjacency Matrix

```python
matrix = [[0] * n for _ in range(n)]
for u, v in edges:
    matrix[u][v] = 1
```

只有當 V 小(≤ 500)且圖密集才用。空間 O(V²)。Floyd-Warshall 必用。

### 3. Grid 本身就是圖

```python
DIRS = [(0,1),(1,0),(0,-1),(-1,0)]
for dr, dc in DIRS:
    nr, nc = r + dr, c + dc
    if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] != WALL:
        ...
```

Grid 類題不用顯式建圖,直接用方向向量。

---

## BFS 模板

```python
from collections import deque

def bfs(start):
    q = deque([start])
    visited = {start}
    while q:
        node = q.popleft()
        # 處理 node
        for nb in neighbors(node):
            if nb not in visited:
                visited.add(nb)
                q.append(nb)
```

**BFS 的保證**:在**無權圖**(或邊權全相同)中,第一次訪問到某節點的路徑是**最短**的。

### 帶距離 / 層數的 BFS

```python
def bfs_distance(start, target):
    q = deque([start])
    visited = {start}
    dist = 0
    while q:
        for _ in range(len(q)):   # 鎖定當前層
            node = q.popleft()
            if node == target: return dist
            for nb in neighbors(node):
                if nb not in visited:
                    visited.add(nb)
                    q.append(nb)
        dist += 1
    return -1
```

**`for _ in range(len(q))` 這招**在 tree level order 看過,這裡是一樣的意思:鎖住「當前這一層」的節點數。

### Multi-Source BFS

多個起點同時出發,找每個節點到「最近起點」的距離。

```python
def multi_source_bfs(grid, sources):
    q = deque(sources)
    dist = {s: 0 for s in sources}
    while q:
        node = q.popleft()
        for nb in neighbors(node):
            if nb not in dist:
                dist[nb] = dist[node] + 1
                q.append(nb)
    return dist
```

**經典題**:Rotting Oranges (994)、01 Matrix (542)、Walls and Gates (286)。

---

## DFS 模板

### 遞迴版

```python
def dfs(node, visited):
    if node in visited: return
    visited.add(node)
    # 處理 node
    for nb in neighbors(node):
        dfs(nb, visited)
```

### Iterative 版(配 stack)

```python
def dfs_iter(start):
    stack = [start]
    visited = set()
    while stack:
        node = stack.pop()
        if node in visited: continue
        visited.add(node)
        # 處理 node
        for nb in neighbors(node):
            if nb not in visited:
                stack.append(nb)
```

**Iterative 的用處**:深度可能超過 Python recursion limit(預設 1000)。

---

## BFS vs DFS:何時選哪個

| 用 BFS | 用 DFS |
|---|---|
| 最短路徑(無權 / 等權圖) | 路徑存在性 |
| 「最少步數」「最少變換」 | 連通分量 |
| 按層處理 | 拓撲排序 |
| Multi-source | 檢測環(有向圖) |
| | 島嶼類(通常,BFS 也行) |
| | 回溯 / 枚舉所有路徑 |

**記一條**:題目問「最短 / 最少」且邊權等 → **BFS**。

---

## 經典題:Grid 類

### Number of Islands (200)

```python
def num_islands(grid):
    rows, cols = len(grid), len(grid[0])
    count = 0
    def dfs(r, c):
        if not (0 <= r < rows and 0 <= c < cols) or grid[r][c] != '1':
            return
        grid[r][c] = '0'   # 原地標記 visited
        for dr, dc in [(0,1),(1,0),(0,-1),(-1,0)]:
            dfs(r + dr, c + dc)
    for r in range(rows):
        for c in range(cols):
            if grid[r][c] == '1':
                count += 1
                dfs(r, c)
    return count
```

**原地標記 visited** 省空間。如果不能改 grid,就用額外 set。

### Rotting Oranges (994,multi-source BFS)

> 每分鐘腐爛的橘子會傳染鄰居,問多久全部腐爛。

```python
def oranges_rotting(grid):
    from collections import deque
    rows, cols = len(grid), len(grid[0])
    q = deque()
    fresh = 0
    for r in range(rows):
        for c in range(cols):
            if grid[r][c] == 2: q.append((r, c, 0))
            elif grid[r][c] == 1: fresh += 1

    t = 0
    while q:
        r, c, t = q.popleft()
        for dr, dc in [(0,1),(1,0),(0,-1),(-1,0)]:
            nr, nc = r+dr, c+dc
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == 1:
                grid[nr][nc] = 2
                fresh -= 1
                q.append((nr, nc, t + 1))
    return t if fresh == 0 else -1
```

**訊號**:「多起點同時擴散」「問最短時間」→ multi-source BFS。

### Word Ladder (127)

> 從 beginWord 變到 endWord,每次只能改一個字母,改後必須在字典裡。求最少變換次數。

```python
from collections import deque, defaultdict

def ladder_length(beginWord, endWord, wordList):
    wordSet = set(wordList)
    if endWord not in wordSet: return 0

    # 預先建「pattern -> words」的索引
    L = len(beginWord)
    pattern_map = defaultdict(list)
    for word in wordSet:
        for i in range(L):
            pattern_map[word[:i] + '*' + word[i+1:]].append(word)

    q = deque([(beginWord, 1)])
    visited = {beginWord}
    while q:
        word, steps = q.popleft()
        if word == endWord: return steps
        for i in range(L):
            p = word[:i] + '*' + word[i+1:]
            for nb in pattern_map[p]:
                if nb not in visited:
                    visited.add(nb)
                    q.append((nb, steps + 1))
    return 0
```

**暴力鄰居計算 O(L × 26)** 每詞,總共 O(N × L × 26)。pattern 索引版 O(N × L) 建索引 + O(E) BFS,更快。面試要能討論這個優化。

---

## DFS 的細節陷阱

### 陷阱 1:visited 的位置

```python
# BAD(對 grid 類通常 OK,但對一般圖會錯)
def dfs(node):
    if node in visited: return
    visited.add(node)
    ...

# GOOD(通用)
def dfs(node):
    visited.add(node)
    ...
    for nb in neighbors(node):
        if nb not in visited:
            dfs(nb)
```

重點:**進入函數立刻標記,還是走到鄰居時先檢查**。兩種寫法差異在有環圖 / 重入場景。grid 常用第一種(先檢查 + 改格子)。

### 陷阱 2:Backtrack 忘了 undo

找所有路徑時:

```python
def dfs(node, path):
    if node == target:
        ans.append(path[:])
        return
    for nb in neighbors(node):
        path.append(nb)
        dfs(nb, path)
        path.pop()    # ← 別忘
```

### 陷阱 3:有向圖的「visited」三狀態

偵測有向圖環要用三色:未訪問(white)、正在訪問(gray)、已完成(black)。**只用一個 visited set 會錯**——遇到 visited 不代表是環,可能只是另一條路徑先到。

```python
def has_cycle(graph):
    WHITE, GRAY, BLACK = 0, 1, 2
    color = defaultdict(int)
    def dfs(node):
        if color[node] == GRAY: return True      # 正在訪問,遇到自己 → 環
        if color[node] == BLACK: return False
        color[node] = GRAY
        for nb in graph[node]:
            if dfs(nb): return True
        color[node] = BLACK
        return False
    for node in graph:
        if color[node] == WHITE and dfs(node):
            return True
    return False
```

---

## 經典題:圖的其他形狀

### Clone Graph (133)

```python
def clone_graph(node):
    if not node: return None
    mapping = {}
    def dfs(n):
        if n in mapping: return mapping[n]
        copy = Node(n.val)
        mapping[n] = copy
        for nb in n.neighbors:
            copy.neighbors.append(dfs(nb))
        return copy
    return dfs(node)
```

**心法**:hash map 存「原節點 → 新節點」,先建空新節點、再填 neighbors,避免環導致無限遞迴。

### Course Schedule (207, 210)

拓撲排序題。下一章專門講。

### Pacific Atlantic Water Flow (417)

**反向思考**:不是從每個格子模擬水流,而是從「海邊」反向 BFS,找能被海淹到的格子。兩個海各做一次,取交集。

---

## 自我檢核

- [ ] 為什麼 BFS 保證最短路徑(無權圖)?DFS 可以嗎?
- [ ] Multi-source BFS 和普通 BFS 的差別?
- [ ] Grid 題「原地改值當 visited」什麼時候會不 work?
- [ ] 有向圖環檢測為什麼要三色?
- [ ] 寫一個 iterative DFS,用 stack 模擬。

→ [Ch 12 Topological Sort](./12-topological-sort.md)
