# Ch 12 — Topological Sort

> 目標:辨識「有依賴關係」的題,熟練兩種實作(BFS/Kahn 與 DFS),知道拓撲序可以順便偵測環。

## 解決什麼問題

**DAG(有向無環圖)上,找一個線性順序使得所有邊都往同方向**。白話:A 依賴 B,那 B 必須排在 A 前面。

訊號:

- 「排課 / 修課順序」
- 「任務依賴」
- 「build system」
- 「字典序推導」
- 「有向圖中找某種順序」

**前置條件**:圖必須是 DAG。有環就沒有拓撲序——演算法能順便告訴你有沒有環。

## 兩種實作

### 方法 A:Kahn's Algorithm(BFS 版)

**核心思想**:反覆拿掉「入度為 0 的節點」。

```python
from collections import defaultdict, deque

def topo_sort_kahn(n, edges):
    graph = defaultdict(list)
    indeg = [0] * n
    for u, v in edges:    # u -> v,u 必須排在 v 前
        graph[u].append(v)
        indeg[v] += 1

    q = deque([i for i in range(n) if indeg[i] == 0])
    order = []
    while q:
        u = q.popleft()
        order.append(u)
        for v in graph[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)

    return order if len(order) == n else []    # 不等於 n → 有環
```

**Kahn 的優點**:

- 結構清楚,好 debug。
- 可以輸出「字典序最小的拓撲序」——把 deque 換成 min-heap 即可。

### 方法 B:DFS + postorder reverse

**核心思想**:DFS 到底再回退,後序(postorder)記錄節點,最後反轉就是拓撲序。

```python
def topo_sort_dfs(n, edges):
    graph = defaultdict(list)
    for u, v in edges:
        graph[u].append(v)

    WHITE, GRAY, BLACK = 0, 1, 2
    color = [WHITE] * n
    order = []

    def dfs(u):
        color[u] = GRAY
        for v in graph[u]:
            if color[v] == GRAY: return False    # 環
            if color[v] == WHITE and not dfs(v): return False
        color[u] = BLACK
        order.append(u)
        return True

    for i in range(n):
        if color[i] == WHITE and not dfs(i):
            return []    # 有環

    return order[::-1]
```

**DFS 版為什麼要反轉**:postorder 是「孩子先、父後」,拓撲序要「父先、孩子後」,所以反轉。

---

## 哪個版本用哪個

| 情境 | 偏好 |
|---|---|
| 要判斷有環無環 | 兩個都可 |
| 要輸出字典序最小 | **Kahn + heap** |
| 要 SCC(強連通分量) | **DFS 版**(Tarjan / Kosaraju 都基於 DFS) |
| 面試白板,要 bug-free | **Kahn 更直觀** |

## 經典題

### Course Schedule (207)

> 給課程數 n 和先修關係,判斷能否修完。

```python
def can_finish(n, prerequisites):
    graph = defaultdict(list)
    indeg = [0] * n
    for a, b in prerequisites:   # a 依賴 b,即 b -> a
        graph[b].append(a)
        indeg[a] += 1
    q = deque([i for i in range(n) if indeg[i] == 0])
    done = 0
    while q:
        u = q.popleft()
        done += 1
        for v in graph[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    return done == n
```

### Course Schedule II (210)

> 輸出修課順序(任一合法即可)。上面 Kahn 的 `order` 就是答案,差別只是最後 return 的東西。

### Alien Dictionary (269, Hard)

> 外星字典給你一串按字典序排列的字串,推導這種字母表的順序。

```python
def alien_order(words):
    graph = defaultdict(set)
    indeg = {c: 0 for w in words for c in w}

    for i in range(len(words) - 1):
        w1, w2 = words[i], words[i + 1]
        min_len = min(len(w1), len(w2))
        # 邊界:w1 比 w2 長但是是 prefix,無解
        if len(w1) > len(w2) and w1[:min_len] == w2[:min_len]:
            return ""
        for j in range(min_len):
            if w1[j] != w2[j]:
                if w2[j] not in graph[w1[j]]:
                    graph[w1[j]].add(w2[j])
                    indeg[w2[j]] += 1
                break   # 只比第一個不同字元

    q = deque([c for c in indeg if indeg[c] == 0])
    order = []
    while q:
        c = q.popleft()
        order.append(c)
        for nb in graph[c]:
            indeg[nb] -= 1
            if indeg[nb] == 0:
                q.append(nb)

    return "".join(order) if len(order) == len(indeg) else ""
```

**面試高頻 Hard 題**,考點:

1. 從相鄰字串推導字母順序(只看第一個不同字元)。
2. 建圖 + 拓撲排序。
3. 兩種失敗 case:環、前綴長度異常。

`set` 避免重複加邊。

### Parallel Courses (1136)

> 多個課程可同時修,問最少學期。

Kahn 變體:每次把**所有當前入度 0** 的節點一起取出,算一學期。

```python
def minimum_semesters(n, relations):
    graph = defaultdict(list)
    indeg = [0] * (n + 1)
    for a, b in relations:
        graph[a].append(b)
        indeg[b] += 1
    q = deque([i for i in range(1, n + 1) if indeg[i] == 0])
    sem = 0
    done = 0
    while q:
        sem += 1
        for _ in range(len(q)):   # 一學期取完
            u = q.popleft()
            done += 1
            for v in graph[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    q.append(v)
    return sem if done == n else -1
```

**層化 BFS 技巧**:`for _ in range(len(q))` 再次出現(tree level order → multi-source BFS → 拓撲層化)。認得這模式。

---

## 常見陷阱

### 陷阱 1:方向搞反

「a 依賴 b」通常畫成 `b -> a`(先做完 b 才能 a)。也有人反過來寫。**先跟面試官確認方向**。

### 陷阱 2:沒處理孤立節點

題目給 n 個節點,但 edges 只覆蓋部分。初始化時要把所有節點都當候選,不能只遍歷 edges。

### 陷阱 3:重複邊

```python
# BAD
graph[u].append(v)
indeg[v] += 1
```

如果 `(u, v)` 在 edges 出現兩次,indeg 會多加一次,導致錯誤。視題目確認是否去重,或用 set 存鄰居。

### 陷阱 4:以為 Kahn 順序唯一

多個入度 0 節點時順序任意,拓撲序不唯一。題目若要「字典序最小」用 heap。

---

## 自我檢核

- [ ] 拓撲排序的前置條件是?不滿足時演算法會怎樣?
- [ ] Kahn 版為什麼 `len(order) != n` 代表有環?
- [ ] DFS 版為什麼要反轉 postorder?
- [ ] 要「最小字典序拓撲序」怎麼改 Kahn?
- [ ] Alien Dictionary 兩種失敗 case 分別是什麼?

→ [Ch 13 Shortest Path](./13-shortest-path.md)
