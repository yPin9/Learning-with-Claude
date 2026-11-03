# Ch 15 — 著色：貪婪、Brooks、Vizing

> **目標**：能證明貪婪著色的 `Δ+1` 上界與 Brooks 定理，理解頂點排序如何決定貪婪法的好壞，親手構造出「沒有三角形但要很多色」的 Mycielski 圖，並用 Misra–Gries 演算法實作 Vizing 邊著色定理；所有結論都在 n ≤ 7 的全部圖上用暴力求色數對拍。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

排考試時間表是著色問題最早的工業應用：每門課是一個點，有共同修課學生的兩門課連一條邊，同一時段的課不能相鄰。要用幾個時段？這就是**色數（chromatic number）** `χ(G)`：讓相鄰點顏色不同所需的最少顏色數。

同一個模型換個名字到處出現：

- **暫存器分配**：變數是點，同時活著的兩個變數連邊，顏色是暫存器（Ch 17 的主角）。
- **無線頻道分配**：基地台是點，訊號範圍重疊的連邊，顏色是頻道。
- **平行排程**：互相衝突（寫同一塊記憶體）的工作連邊，同色的工作可以同時跑。

壞消息是：判斷 `χ(G) ≤ 3` 已經是 NP-complete（Ch 34 會證明歸約）。所以實務上大家用**貪婪法**，而這一章要回答三個問題：

1. 貪婪法最壞會差多少？差的原因是什麼？（排序）
2. 什麼時候可以保證比貪婪法的 `Δ+1` 更好？（Brooks 定理）
3. 色數的下界能不能靠「找最大團」得到？（不能，Mycielski 構造）

最後換到邊著色，會看到一個罕見的好消息：邊的色數只有兩種可能（Vizing 定理）。

## 先建立直覺

把著色想成「一個一個點走過去，每點挑最小的可用顏色」。一個點最多有 `Δ`（最大度數）個鄰居，就算鄰居全部顏色不同，也只擋掉 `Δ` 個顏色，所以第 `Δ+1` 個顏色一定還在：

```text
           鄰居最多 Δ 個
      c0   c1   c2  ...  c(Δ-1)
        \   |   |       /
         \  |   |      /
             [ v ]  ← 第 Δ+1 種顏色永遠可用
```

但這是**最壞**情況的保證。真正的問題在於：同一張圖，換個走訪順序，結果可以天差地遠。下面這張「皇冠圖」（crown graph，`K_{n,n}` 拿掉一組完美匹配）是二分圖，`χ = 2`，但順序選錯，貪婪法會用到 n 色：

```text
 a0   a1   a2   a3        a_i 和 b_j 相連 ⟺ i ≠ j
 |\ / |\ / |\ / |
 | X  | X  | X  |        交錯順序 a0 b0 a1 b1 ...：
 |/ \ |/ \ |/ \ |        a_i、b_i 不相鄰，所以兩個都拿「新」顏色 i
 b0   b1   b2   b3
```

> 如果你對度數、最大度數 `Δ`、團（clique）這些術語還不熟，先回看 [Ch 7 — 圖的定義、表示與同構](./07-graph-definitions.md)。

## 貪婪著色與它的上界

**定理（貪婪上界）**：對任何順序，貪婪著色用的顏色數 `≤ Δ(G) + 1`。

**證明**：輪到點 v 時，已著色的鄰居至多 `deg(v) ≤ Δ` 個，它們最多佔掉 `Δ` 個顏色，所以 `{0, 1, ..., Δ}` 這 `Δ+1` 個顏色裡至少有一個可用，貪婪法挑到的顏色編號 `≤ Δ`。∎

**定理（存在完美順序）**：對任何圖，存在一個順序讓貪婪法恰好用 `χ(G)` 色。

**證明**：取一個最佳著色，色類為 `V_0, V_1, ..., V_{k-1}`（`k = χ`）。先排 `V_0` 的點，再排 `V_1`，依此類推（類內順序任意）。對順序做歸納，證明 `V_i` 裡的點拿到的貪婪色 `≤ i`：輪到 `v ∈ V_i` 時，排在它前面的點屬於 `V_0, ..., V_i`；但 `V_i` 是獨立集，所以 v 在前面的**鄰居**全在 `V_0, ..., V_{i-1}`，由歸納假設它們的貪婪色都 `≤ i-1`。它們最多擋掉 `{0, ..., i-1}`，v 拿到的顏色 `≤ i`。所以總色數 `≤ k = χ`，又不可能少於 χ，恰好等於。∎

這個定理告訴我們：**貪婪法的品質完全取決於順序**。找最佳順序和求色數一樣難，所以我們退而求其次，用啟發式排序。

### Welsh–Powell 與 DSATUR

| 排序法 | 規則 | 直覺 | 保證 |
| --- | --- | --- | --- |
| 自然順序 | 照點的編號 | 無 | `≤ Δ+1` |
| Welsh–Powell（1967） | 度數由大到小，一次排好 | 難搞的點先處理，選擇多 | `≤ max_i min(d_i + 1, i)`，`d_1 ≥ d_2 ≥ ...` |
| DSATUR（Brélaz 1979） | 每步動態挑**飽和度**最大的點 | 飽和度 = 鄰居已用了幾種不同顏色；越「被逼到角落」越先處理 | 二分圖上一定給 2 色 |

Welsh–Powell 的上界證明：排第 i 個的點，在它前面的鄰居既不超過 `d_i` 個，也不超過 `i-1` 個，所以它拿到的顏色編號 `≤ min(d_i, i-1)`，用色數 `≤ max_i min(d_i, i-1) + 1`。

DSATUR 在二分圖上為什麼一定給 2 色：對每個連通分量（兩邊 A、B，設第一個著色的點在 A）歸納證明「已著色的 A 點都是 0、B 點都是 1」。第一個點飽和度 0，拿 0。之後只要分量裡還有未著色點，因為分量連通，一定有某個未著色點有已著色鄰居，飽和度 `≥ 1`，所以 DSATUR 挑的點 v 也有已著色鄰居。v 的鄰居全在對面那一邊，由歸納假設它們同色，v 只被擋掉一個顏色，拿到的正是自己那一邊的顏色。換分量時重新從飽和度 0 開始，同樣的論證再走一次。

先跑皇冠圖，看排序的影響：

```python
import networkx as nx


def greedy(adj, order):
    """依 order 逐點著色，每點取鄰居沒用過的最小顏色。"""
    color = {}
    for v in order:
        used = {color[u] for u in adj[v] if u in color}
        c = 0
        while c in used:
            c += 1
        color[v] = c
    return color


def welsh_powell(adj):
    order = sorted(adj, key=lambda v: (-len(adj[v]), str(v)))
    return greedy(adj, order)


def dsatur(adj):
    color = {}
    while len(color) < len(adj):
        def key(v):
            sat = len({color[u] for u in adj[v] if u in color})       # 飽和度
            free_deg = sum(1 for u in adj[v] if u not in color)       # 平手時比未著色鄰居數
            return (sat, free_deg, str(v))                            # 再平手比名字，結果可重現
        v = max((v for v in adj if v not in color), key=key)
        used = {color[u] for u in adj[v] if u in color}
        c = 0
        while c in used:
            c += 1
        color[v] = c
    return color


def ncolors(col):
    return len(set(col.values()))


def crown(n):
    adj = {f"a{i}": set() for i in range(n)} | {f"b{i}": set() for i in range(n)}
    for i in range(n):
        for j in range(n):
            if i != j:
                adj[f"a{i}"].add(f"b{j}")
                adj[f"b{j}"].add(f"a{i}")
    return adj


adj = crown(5)
interleaved = [x for i in range(5) for x in (f"a{i}", f"b{i}")]
sides = [f"a{i}" for i in range(5)] + [f"b{i}" for i in range(5)]
print("crown(5): Δ =", max(len(s) for s in adj.values()))
print("  greedy 交錯順序  :", ncolors(greedy(adj, interleaved)))
print("  greedy 先 a 後 b :", ncolors(greedy(adj, sides)))
print("  Welsh–Powell     :", ncolors(welsh_powell(adj)))
print("  DSATUR           :", ncolors(dsatur(adj)))
G = nx.Graph([(u, v) for u in adj for v in adj[u]])
for strat in ["largest_first", "DSATUR", "smallest_last"]:
    print(f"  nx {strat:14s}:", ncolors(nx.greedy_color(G, strategy=strat)))
```

```text
crown(5): Δ = 4
  greedy 交錯順序  : 5
  greedy 先 a 後 b : 2
  Welsh–Powell     : 2
  DSATUR           : 2
  nx largest_first : 2
  nx DSATUR        : 2
  nx smallest_last : 2
```

交錯順序把 `Δ+1 = 5` 的上界整個用滿，換一個順序就回到 2。NetworkX 的 `greedy_color` 只是貪婪法加不同排序策略，`largest_first` 就是 Welsh–Powell。

## 底層機制：暴力求色數與全圖對拍

啟發式好不好，要有標準答案才能評。色數可以用**回溯法**求：k 從 1 往上試，每次問「能不能 k 色」。關鍵剪枝是**顏色對稱性**：顏色名稱可以互換，所以輪到某點時，新顏色最多試一個就夠，不必把 k 個新顏色都試一遍。

```text
can(k):  依度數大到小排點
  bt(i): 點 i 試顏色 0 .. min(k, 目前最大色 + 2) - 1
         ↑ 這個上限就是對稱性剪枝：新顏色只試一個
```

NetworkX 的 `graph_atlas_g()` 收錄了 0 到 7 個點的全部非同構圖，共 1253 張（第 0 張是空圖）。我們在每一張上量三件事：各啟發式有沒有超過 `χ`、有沒有違反 `Δ+1`、Welsh–Powell 上界有沒有成立。

```python
# 接續上一個 block 的定義
import random


def chromatic_number(adj):
    """回溯法求 χ(G)。"""
    nodes = sorted(adj, key=lambda v: -len(adj[v]))
    def can(k):
        color = {}
        def bt(i):
            if i == len(nodes):
                return True
            v = nodes[i]
            used = {color[u] for u in adj[v] if u in color}
            limit = min(k, max(color.values(), default=-1) + 2)   # 對稱性剪枝
            for c in range(limit):
                if c not in used:
                    color[v] = c
                    if bt(i + 1):
                        return True
                    del color[v]
            return False
        return bt(0)
    k = 1 if adj else 0
    while not can(k):
        k += 1
    return k


random.seed(15)
worse = {"natural": 0, "random": 0, "welsh_powell": 0, "dsatur": 0}
wp_ok = True
graphs = nx.graph_atlas_g()[1:]
for H in graphs:
    a = {u: set(H[u]) for u in H}
    chi = chromatic_number(a)
    D = max(len(s) for s in a.values())
    order = sorted(a)
    shuffled = order[:]
    random.shuffle(shuffled)
    res = {"natural": ncolors(greedy(a, order)), "random": ncolors(greedy(a, shuffled)),
           "welsh_powell": ncolors(welsh_powell(a)), "dsatur": ncolors(dsatur(a))}
    for name, c in res.items():
        assert chi <= c <= D + 1
        worse[name] += c > chi
    ds = sorted((len(s) for s in a.values()), reverse=True)
    wp_ok &= res["welsh_powell"] <= max(min(d + 1, i + 1) for i, d in enumerate(ds))
print("圖數:", len(graphs))
print("用色 > χ 的圖數:", worse)
print("Welsh–Powell 上界全部成立:", wp_ok)

# 8 個點：DSATUR 也會失手（用 seed=1 的隨機搜尋找到的一張）
E8 = [(0, 1), (0, 3), (0, 6), (0, 7), (1, 4), (2, 3), (2, 4), (2, 7),
      (3, 5), (4, 5), (4, 7), (5, 6), (6, 7)]
a8 = {u: set() for u in range(8)}
for u, v in E8:
    a8[u].add(v)
    a8[v].add(u)
print("8 點反例: DSATUR =", ncolors(dsatur(a8)), " χ =", chromatic_number(a8))
```

```text
圖數: 1252
用色 > χ 的圖數: {'natural': 157, 'random': 182, 'welsh_powell': 25, 'dsatur': 0}
Welsh–Powell 上界全部成立: True
8 點反例: DSATUR = 4  χ = 3
```

三個觀察：

1. 所有啟發式都守住 `χ ≤ 用色 ≤ Δ+1`，`assert` 一次都沒觸發。
2. 隨機順序和自然順序在 1252 張小圖裡有一成多會多用顏色；Welsh–Powell 大幅改善；DSATUR（在我們這組平手規則下）在 7 點以內全對。
3. 但 DSATUR **不是**精確演算法：8 個點就找得到反例。結果也依賴平手規則（tie-break），換一種平手規則，同一張圖可能就著對了。研究「怎麼平手都著錯」的最小圖是一個獨立的題目，見延伸閱讀的 Janczewski 等人。

## Brooks 定理：什麼時候能少用一色

`Δ+1` 什麼時候是緊的？兩個例子：完全圖 `K_n`（`Δ = n-1`，`χ = n`）和奇圈 `C_{2k+1}`（`Δ = 2`，`χ = 3`）。Brooks（1941）證明這兩個是**唯二**的例外：

**定理（Brooks）**：G 連通，不是完全圖也不是奇圈，則 `χ(G) ≤ Δ(G)`。

`Δ ≤ 2` 的連通圖是路徑或圈，路徑和偶圈是二分圖，`χ ≤ 2 = Δ`（`Δ = 1` 時 G 是 `K_2`，屬於例外；`Δ = 2` 的奇圈也是例外），剩下要處理的是 `Δ ≥ 3`。以下照 Lovász（1975）的證明，核心想法只有一個：

> **貪婪法的 `Δ+1` 來自「輪到 v 時所有鄰居都已著色」。只要安排順序，讓每個點著色時至少有一個鄰居還沒著色，它就只會被擋掉 `Δ-1` 個顏色。**

把點依「到某個根 r 的距離」由遠到近排序，r 最後。除了 r 之外，每個點在 BFS 樹上的父親離 r 更近，排在它後面，所以著色時至少有一個鄰居（父親）還沒上色。整個問題縮成：**怎麼讓根 r 也安全？**

**證明**（`Δ ≥ 3`，G 連通、非完全圖）：

**情況 1：G 不是 `Δ`-正則。** 取一個 `deg(r) < Δ` 的點當根。r 最後著色時鄰居最多 `Δ-1` 個，也安全。用 `Δ` 色完成。

**情況 2：G 是 `Δ`-正則但有割點 v。** 設 `G - v` 的連通分量為 `C_1, ..., C_m`（`m ≥ 2`），令 `G_i` 為 `C_i` 加回 v 的導出子圖。v 在每個 `G_i` 裡的度數都 `< Δ`（它的鄰居分散在至少兩個分量），所以每個 `G_i` 都落在情況 1，各自可以 `Δ` 著色。把每個 `G_i` 的顏色名稱重新排列，讓 v 在各塊裡顏色一致；不同分量之間沒有邊，拼起來就是 G 的 `Δ`-著色。

**情況 3：G 是 `Δ`-正則且 2-連通。** 我們要找三個點 `x - v - y`：x、y 都是 v 的鄰居，x 與 y **不相鄰**，而且 `G - x - y` 仍連通。找到之後：先把 x、y 都塗顏色 0（它們不相鄰，合法），再對 `G - x - y` 以 v 為根、由遠到近貪婪著色。非 v 的點都有尚未著色的父親，安全；輪到 v 時，它有 `Δ` 個鄰居全部著色，但 x、y 同色，所以最多佔 `Δ-1` 種顏色，v 也安全。

剩下證明這樣的 `x, v, y` 一定存在：

- **若 G 是 3-連通**：G 不是完全圖又連通，所以存在距離為 2 的兩點 x、y，取它們的共同鄰居當 v。拿掉兩個點不會讓 3-連通圖斷開，`G - x - y` 連通。
- **若 G 的連通度恰為 2**：存在一點 v 使 `G - v` 有割點（`{v, w}` 是一組 2-點割）。`G - v` 是連通但不是 2-連通的圖，它的區塊樹（block-cutpoint tree，Ch 11）至少有兩個葉子區塊 `B_1`、`B_2`。G 是 2-連通，所以 v 在每個葉子區塊的「非割點」部分都有鄰居（否則那個葉子區塊的割點就會是 G 的割點），各取一個：`x ∈ B_1`、`y ∈ B_2`。x、y 在不同區塊且都不是割點，所以不相鄰。葉子區塊不是 `K_2`（否則 x 在 `G - v` 中度數 1，`deg_G(x) ≤ 2 < Δ`，和正則矛盾），所以是 2-連通的，拿掉一個非割點的 x 不會斷開它；因此 `G - v - x - y` 仍連通。最後 `deg(v) = Δ ≥ 3`，v 除了 x、y 還有別的鄰居，加回 v 後 `G - x - y` 連通。∎

> 區塊、割點、葉子區塊這些詞在 [Ch 11 — 連通度：割點、橋與 Tarjan](./11-connectivity-tarjan.md)。

這個證明是**構造性**的，直接翻成程式。情況 3 的三元組我們用暴力搜尋找（證明保證它存在），找不到就 `raise`——對拍時如果觸發，代表證明或程式有洞。

```python
# 接續上一個 block 的定義
from collections import deque


def bfs_order(a, root, banned=frozenset()):
    seen, order, q = {root}, [root], deque([root])
    while q:
        u = q.popleft()
        for w in sorted(a[u], key=str):
            if w not in seen and w not in banned:
                seen.add(w)
                order.append(w)
                q.append(w)
    return order


def greedy_k(a, order, color, k):
    for v in order:
        used = {color[u] for u in a[v] if u in color}
        color[v] = next(c for c in range(k) if c not in used)   # 沒有可用色會丟 StopIteration
    return color


def connected_without(a, banned):
    rest = [v for v in a if v not in banned]
    return len(bfs_order(a, rest[0], banned)) == len(rest)


def brooks_color(a):
    """連通、Δ >= 3、非完全圖 → Δ-著色。回傳 (著色, 走了哪個情況)。"""
    D = max(len(s) for s in a.values())
    low = [v for v in a if len(a[v]) < D]
    if low:                                                   # 情況 1
        return greedy_k(a, bfs_order(a, low[0])[::-1], {}, D), 1
    for v in a:                                               # 情況 2
        if not connected_without(a, {v}):
            color, rest = {}, set(a) - {v}
            while rest:
                comp = set(bfs_order(a, next(iter(rest)), {v}))
                rest -= comp
                part = {u: a[u] & (comp | {v}) for u in comp | {v}}
                sub = greedy_k(part, bfs_order(part, v)[::-1], {}, D)
                if v in color and sub[v] != color[v]:         # 交換兩個顏色名稱，對齊 v
                    p, q = sub[v], color[v]
                    sub = {u: (q if c == p else p if c == q else c) for u, c in sub.items()}
                color.update(sub)
            return color, 2
    for v in a:                                               # 情況 3
        for x in a[v]:
            for y in a[v]:
                if str(x) < str(y) and y not in a[x] and connected_without(a, {x, y}):
                    color = greedy_k(a, bfs_order(a, v, {x, y})[::-1], {x: 0, y: 0}, D)
                    return color, 3
    raise AssertionError("證明保證到不了這裡")


def proper(a, col):
    return all(col[u] != col[w] for u in a for w in a[u])


cases = {1: 0, 2: 0, 3: 0}
for H in graphs:
    if not nx.is_connected(H):
        continue
    a = {u: set(H[u]) for u in H}
    n, D = len(a), max(len(s) for s in a.values())
    if D < 3 or H.number_of_edges() == n * (n - 1) // 2:
        continue
    col, case = brooks_color(a)
    assert proper(a, col) and max(col.values()) < D
    cases[case] += 1
print("atlas 上 Brooks 著色成功（依情況分）:", cases)

# 情況 2 在 7 點以內不會出現：做一張有橋的 3-正則圖
# 兩份「K4 細分一條邊」，把兩個細分點連起來
B = nx.Graph()
for s in (0, 10):
    B.add_edges_from([(s, s + 1), (s, s + 2), (s + 1, s + 2), (s + 1, s + 3), (s + 2, s + 3),
                      (s, s + 4), (s + 4, s + 3)])
B.add_edge(4, 14)
a = {u: set(B[u]) for u in B}
col, case = brooks_color(a)
print("有橋 3-正則圖:", "度數", sorted({d for _, d in B.degree()}), "情況", case,
      "用色", ncolors(col), "合法", proper(a, col))
P = nx.petersen_graph()
a = {u: set(P[u]) for u in P}
col, case = brooks_color(a)
print("Petersen:", "情況", case, "用色", ncolors(col), "合法", proper(a, col))
```

```text
atlas 上 Brooks 著色成功（依情況分）: {1: 975, 2: 0, 3: 5}
有橋 3-正則圖: 度數 [3] 情況 2 用色 3 合法 True
Petersen: 情況 3 用色 3 合法 True
```

7 點以內的正則圖都沒有割點，所以情況 2 要另外造一張圖來測：兩個「K4 細分一條邊」用一條橋接起來，每點度數都是 3。三個分支都實際被走到，而且沒有任何一張圖觸發 `AssertionError`。

## Clique 與色數的差距：Mycielski 構造

`χ(G) ≥ ω(G)`（`ω` 是最大團大小）：團裡的點兩兩相鄰，必須全部不同色。這是最常用的下界，Ch 16 的 chordal graph 上它還是精確的。很自然會猜：「色數大，是不是因為藏了一個大團？」

**不是。** Mycielski（1955）給了一個構造，每做一次色數加 1、但永遠不產生三角形（`ω = 2`）：

```text
給 G，點 v_1..v_n。M(G) 加入：
  u_1..u_n ：u_i 連到 v_i 在 G 中的所有鄰居（「影子」）
  w        ：連到所有 u_i

  K2 ──M──▶ C5 ──M──▶ Grötzsch 圖（11 點）──M──▶ 23 點 ...
  χ=2        χ=3       χ=4                        χ=5
```

**定理**：若 G 沒有三角形，`M(G)` 也沒有三角形，且 `χ(M(G)) = χ(G) + 1`。

**證明**：

- **沒有三角形**：u 之間互不相鄰，w 只連 u，所以含 w 的三角形要有兩個相鄰的 u，不可能。任何三角形頂多含一個 u，設為 `u_i v_j v_l`，那 `v_j`、`v_l` 都是 `v_i` 的鄰居且彼此相鄰，`v_i v_j v_l` 就是 G 的三角形，矛盾。
- **`χ(M) ≤ χ(G) + 1`**：v_i 和 u_i 都塗 G 中 v_i 的顏色（u_i 的鄰居是 v_i 的鄰居，不衝突），w 塗新顏色。
- **`χ(M) ≥ χ(G) + 1`**：設 `k = χ(G)`，假設 M 有 k 著色 f，不妨設 `f(w) = k`。所有 u 都和 w 相鄰，所以 u 都不是顏色 k。把 G 裡顏色是 k 的每個 `v_i` 改塗 `f(u_i)`。檢查新著色合法：顏色 k 的 v 兩兩不相鄰（原本就同色），所以被改色的點彼此不衝突；被改色的 `v_i` 和沒被改的鄰居 `v_j` 之間，`u_i` 也和 `v_j` 相鄰，所以 `f(u_i) ≠ f(v_j)`。於是 G 得到只用 `k-1` 色的合法著色，和 `χ(G) = k` 矛盾。∎

```python
# 接續上一個 block 的定義
import time


def mycielski(a):
    new = {}
    for v in a:
        new[("v", v)] = {("v", u) for u in a[v]}
        new[("u", v)] = set()
    new["w"] = set()
    for v in a:
        for u in a[v]:
            new[("u", v)].add(("v", u))
            new[("v", u)].add(("u", v))
        new[("u", v)].add("w")
        new["w"].add(("u", v))
    idx = {x: i for i, x in enumerate(sorted(new, key=str))}          # 重新編號成整數
    return {idx[x]: {idx[y] for y in new[x]} for x in new}


def has_triangle(a):
    return any(a[u] & a[v] for u in a for v in a[u])


g = {0: {1}, 1: {0}}                                                   # K2
for k in range(2, 6):
    H = nx.Graph([(u, v) for u in g for v in g[u]])
    t = time.perf_counter()
    chi = chromatic_number(g)
    print(f"M_{k}: n={len(g):2d} m={H.number_of_edges():2d} 三角形={has_triangle(g)} "
          f"χ={chi} 與 nx.mycielski_graph({k}) 同構={nx.is_isomorphic(H, nx.mycielski_graph(k))}")
    g = mycielski(g)
```

```text
M_2: n= 2 m= 1 三角形=False χ=2 與 nx.mycielski_graph(2) 同構=True
M_3: n= 5 m= 5 三角形=False χ=3 與 nx.mycielski_graph(3) 同構=True
M_4: n=11 m=20 三角形=False χ=4 與 nx.mycielski_graph(4) 同構=True
M_5: n=23 m=71 三角形=False χ=5 與 nx.mycielski_graph(5) 同構=True
```

`ω = 2` 但 `χ = 5`，而且繼續做下去色數可以任意大。這對 Ch 17 的意義很直接：**「同時活著的變數最多 k 個」（區域性的團大小）不保證 k 個暫存器夠用**——除非 interference graph 有額外結構，例如 Ch 16 要講的 chordal。

## 邊著色與 Vizing 定理

把顏色塗在邊上：共用端點的兩條邊不同色，最少色數記作 `χ'(G)`（chromatic index）。應用：每條邊是一場比賽或一次資料傳輸，同一個點同時只能參加一個，顏色就是時段。

下界很直接：度數 `Δ` 的點上 `Δ` 條邊兩兩相鄰，`χ' ≥ Δ`。神奇的是上界：

**定理（Vizing 1964）**：簡單圖的 `χ'(G)` 只可能是 `Δ` 或 `Δ+1`。

前者叫 Class 1、後者叫 Class 2。對比頂點著色：`χ` 可以落在 `ω` 到 `Δ+1` 之間任何地方，差距可以任意大（Mycielski）；邊著色卻被夾死在兩個值。判斷是哪一類仍是 NP-complete（Holyer 1981 對 3-正則圖證明），但 `Δ+1` 色**一定可以多項式時間做到**。

**這裡只給證明架構**（完整證明見延伸閱讀的 Misra–Gries 論文，三頁）。Misra–Gries 演算法一次塗一條邊 `(u, v)`，只用 `Δ+1` 色，每步維持合法：

```text
1. fan：從 v 出發，找 u 的鄰居序列 F = [f0=v, f1, ..., fk]，
        使得邊 (u, f_{i+1}) 的顏色在 f_i 上「空著」（free）。取到不能再延長。
2. 取 c = u 上空著的色，d = fk 上空著的色（Δ+1 色而度數 ≤ Δ，一定有空色）。
3. 把從 u 出發、顏色 d/c 交錯的路徑整條反色（Kempe 交換，Ch 19 的主角）。
   之後 d 在 u 上變成空著。
4. 找 fan 的一個前綴 [f0..w] 使得 d 在 w 上空著；把前綴「旋轉」：
   (u,f_i) 改塗原本 (u,f_{i+1}) 的色，最後 (u,w) 塗 d。
```

證明的關鍵在第 4 步：論文證明反色之後，要嘛 fk 本身、要嘛 fan 裡某個點 w 的前綴仍是合法 fan 且 d 在 w 上空著。我們的程式碼直接搜尋這個 w，並且用暴力 `χ'` 對拍（`χ'(G) = χ(L(G))`，L(G) 是線圖：每條邊一個點，共用端點的兩條邊相連）：

```python
# 接續上一個 block 的定義
def misra_gries(a):
    D = max(len(s) for s in a.values())
    col = {}
    def c_of(x, y): return col.get(frozenset((x, y)))
    def free(x): return set(range(D + 1)) - {c_of(x, y) for y in a[x]}
    def is_fan(u, F):
        return c_of(u, F[0]) is None and all(
            c_of(u, F[i]) is not None and c_of(u, F[i]) in free(F[i - 1]) for i in range(1, len(F)))
    for u in a:
        for v in a[u]:
            if c_of(u, v) is not None:
                continue
            F, grown = [v], True                                         # 1. 極大 fan
            while grown:
                grown = False
                for w in a[u]:
                    if w not in F and c_of(u, w) is not None and c_of(u, w) in free(F[-1]):
                        F.append(w)
                        grown = True
                        break
            c, d = min(free(u)), min(free(F[-1]))                         # 2.
            x, want, path = u, d, []                                      # 3. 反色 cd 路徑
            while True:
                nxt = next((y for y in a[x] if c_of(x, y) == want and frozenset((x, y)) not in path), None)
                if nxt is None:
                    break
                path.append(frozenset((x, nxt)))
                x, want = nxt, (c if want == d else d)
            for e in path:
                col[e] = c if col[e] == d else d
            j = next(j for j in range(len(F)) if is_fan(u, F[:j + 1]) and d in free(F[j]))  # 4.
            for i in range(j):
                col[frozenset((u, F[i]))] = col[frozenset((u, F[i + 1]))]
            col[frozenset((u, F[j]))] = d
    return col, D


def edge_proper(a, col):
    return all(len({col[frozenset((x, y))] for y in a[x]}) == len(a[x]) for x in a)


cls = {1: 0, 2: 0}
for H in graphs:
    if H.number_of_edges() == 0:
        continue
    a = {u: set(H[u]) for u in H}
    col, D = misra_gries(a)
    assert edge_proper(a, col) and len(set(col.values())) <= D + 1
    L = nx.line_graph(H)
    chi_e = chromatic_number({e: set(L[e]) for e in L})
    assert D <= chi_e <= D + 1
    cls[chi_e - D + 1] += 1
print("有邊的 atlas 圖，Class 1 / Class 2:", cls)
for name, H in [("Petersen", nx.petersen_graph()), ("K4", nx.complete_graph(4)),
                ("K5", nx.complete_graph(5)), ("K_{3,4}", nx.complete_bipartite_graph(3, 4))]:
    a = {u: set(H[u]) for u in H}
    col, D = misra_gries(a)
    L = nx.line_graph(H)
    print(f"{name:8s} Δ={D} Misra–Gries 用 {len(set(col.values()))} 色, χ'={chromatic_number({e: set(L[e]) for e in L})}")
```

```text
有邊的 atlas 圖，Class 1 / Class 2: {1: 1176, 2: 69}
Petersen Δ=3 Misra–Gries 用 4 色, χ'=4
K4       Δ=3 Misra–Gries 用 4 色, χ'=3
K5       Δ=4 Misra–Gries 用 5 色, χ'=5
K_{3,4}  Δ=4 Misra–Gries 用 4 色, χ'=4
```

幾個值得記住的事實都在輸出裡：大部分小圖是 Class 1；Petersen 圖是 Class 2 的經典代表；`K_n` 在 n 偶數時是 Class 1、n 奇數時是 Class 2；二分圖永遠是 Class 1（König 1916 的邊著色定理，Ch 22 會從匹配的角度證明）。Misra–Gries 保證 `Δ+1`，**不保證**找到 `Δ`：它在 Class 1 的圖上也可能用到 `Δ+1` 色。

## 著色多項式與 deletion–contraction

到目前為止我們只問「最少幾色」。換一個問題：**用 k 色有幾種合法著色？** 記作 `P(G, k)`。`χ(G)` 就是讓 `P(G, k) > 0` 的最小 k。Birkhoff（1912）引入這個函數，原本是想用代數方法攻四色問題（Ch 19）。

先算幾個能直接數的：

- 沒有邊的 n 點圖：每點獨立挑色，`P = k^n`。
- 完全圖 `K_n`：第一點 k 種、第二點 `k-1` 種……`P = k(k-1)(k-2)...(k-n+1)`。
- n 點的樹：從根往下，根 k 種，其餘每點只要避開父親，`P = k(k-1)^{n-1}`。

一般圖靠一條遞迴：

**定理（deletion–contraction）**：對 G 的任一條邊 `e = uv`，`P(G, k) = P(G - e, k) - P(G / e, k)`。其中 `G - e` 是刪掉 e，`G / e` 是把 u、v 縮成一點（重邊合併成一條）。

**證明**：把 `G - e` 的合法 k 著色分成兩類。(a) u、v 不同色：這些恰好是 G 的合法著色（e 的限制本來就滿足），共 `P(G, k)` 種。(b) u、v 同色：把 u、v 當成同一點，這些恰好一一對應到 `G / e` 的合法著色，共 `P(G / e, k)` 種。所以 `P(G - e, k) = P(G, k) + P(G / e, k)`，移項即得。∎

**推論**：`P(G, k)` 是 k 的多項式，次數為 n、首項係數 1。**證明**：對邊數歸納，沒有邊時是 `k^n`；`G - e` 是 n 點、邊更少，`G / e` 是 n−1 點、邊更少，由歸納假設兩者都是多項式，相減的首項是 `k^n`。∎

遞迴兩支都讓邊數減一，樸素實作是指數時間（和 Ch 34 的 NP-hardness 一致：能算 P 就能判定 `χ ≤ 3`）。我們用自刻的係數串列表示多項式，加上記憶化，並和暴力計數對拍：

```python
from functools import lru_cache
from itertools import product
import networkx as nx


def chrom_poly(edges, n):
    """deletion–contraction。回傳係數 list，c[i] 是 k^i 的係數。"""
    @lru_cache(maxsize=None)
    def P(E, n):
        if not E:
            return (0,) * n + (1,)                                   # 沒有邊：k^n
        e = min(E, key=sorted)
        u, v = sorted(e)
        Ed = E - {e}                                                 # 刪除 e
        Ec = frozenset(frozenset(u if x == v else x for x in f) for f in Ed)   # v 併入 u；重邊自動合併
        a, b = P(Ed, n), P(Ec, n - 1)
        b = b + (0,) * (len(a) - len(b))
        return tuple(x - y for x, y in zip(a, b))
    return list(P(frozenset(frozenset(e) for e in edges), n))


def evaluate(c, k):
    return sum(ci * k ** i for i, ci in enumerate(c))


def show(c):
    return " ".join(f"{ci:+d}k^{i}" for i, ci in reversed(list(enumerate(c))) if ci)


def count_colorings(G, k):
    nodes = list(G)
    idx = {v: i for i, v in enumerate(nodes)}
    return sum(all(c[idx[u]] != c[idx[w]] for u, w in G.edges())
               for c in product(range(k), repeat=len(nodes)))


for name, G in [("P4（路徑）", nx.path_graph(4)), ("C5", nx.cycle_graph(5)),
                ("K4", nx.complete_graph(4)), ("Petersen", nx.petersen_graph())]:
    c = chrom_poly(G.edges(), G.number_of_nodes())
    chi = next(k for k in range(1, 11) if evaluate(c, k) > 0)
    print(f"{name}: P(k) = {show(c)}   χ = {chi}   P(3) = {evaluate(c, 3)}")

bad = 0
for G in nx.graph_atlas_g()[1:]:
    if G.number_of_nodes() <= 6:
        c = chrom_poly(G.edges(), G.number_of_nodes())
        bad += sum(evaluate(c, k) != count_colorings(G, k) for k in range(4))
print("n ≤ 6 的 atlas 圖、k = 0..3：多項式值與暴力計數不符的次數 =", bad)
```

```text
P4（路徑）: P(k) = +1k^4 -3k^3 +3k^2 -1k^1   χ = 2   P(3) = 24
C5: P(k) = +1k^5 -5k^4 +10k^3 -10k^2 +4k^1   χ = 3   P(3) = 30
K4: P(k) = +1k^4 -6k^3 +11k^2 -6k^1   χ = 4   P(3) = 0
Petersen: P(k) = +1k^10 -15k^9 +105k^8 -455k^7 +1353k^6 -2861k^5 +4275k^4 -4305k^3 +2606k^2 -704k^1   χ = 3   P(3) = 120
n ≤ 6 的 atlas 圖、k = 0..3：多項式值與暴力計數不符的次數 = 0
```

對照公式：P4 是樹，`k(k-1)^3` 展開正是第一行；`K4` 是 `k(k-1)(k-2)(k-3)`，`P(3) = 0` 說明 3 色不夠；`C5` 符合圈的公式 `(k-1)^5 - (k-1)`。Petersen 圖有 120 種 3 著色。暴力計數只做到 6 點、`k ≤ 3`，因為 `k^n` 種指派在 7 點、`k = 3` 時就要對 1252 張圖各數 2187 次以上，教學上沒必要。NetworkX 也有 `nx.chromatic_polynomial`，但它依賴 SymPy，本課環境沒裝，所以這裡全部自刻。

## 對比與取捨

| 方法 | 時間 | 保證 | 適用情境 |
| --- | --- | --- | --- |
| 自然順序貪婪 | `O(n + m)` | `≤ Δ+1` | 只要合法、不在乎色數 |
| Welsh–Powell | `O(n log n + m)` | `≤ max min(d_i+1, i)` | 度數分布很不均時明顯比較好 |
| smallest-last | `O(n + m)` | `≤ 1 + degeneracy` | 稀疏圖（平面圖保證 `≤ 6` 色，Ch 18） |
| DSATUR | `O(n²)`（樸素實作） | 二分圖最佳 | 中小型圖的預設首選 |
| Lovász / Brooks | 多項式；本章暴力找三元組約 `O(n·Δ²·(n+m))`，文獻有線性時間版本 | `≤ Δ`（非例外圖） | 需要理論保證時 |
| 回溯求 χ | 指數 | 精確 | n 小（幾十個點）或當作對拍標準 |
| Misra–Gries（邊） | `O(n·m)` | `≤ Δ+1` | 邊著色、排程 |

Brooks 的 `Δ` vs 貪婪的 `Δ+1` 看起來只差一色，但在暫存器分配這種「差一個就要 spill」的場景，這一色就是有沒有記憶體存取的差別。不過實務的 interference graph `χ` 通常遠小於 `Δ`，Brooks 給的保證太鬆，編譯器靠的是下兩章的結構（chordal）和啟發式（simplify）。

## 踩雷集錦

1. **「貪婪法用了 k 色，所以 `χ = k`」**：貪婪法只給上界。皇冠圖上它用了 5 色，真實答案是 2。要證明 `χ ≥ k` 得靠下界論證（找團、奇圈、Mycielski 式論證）或窮舉。
2. **「找到大小 k 的團就知道 `χ = k`」**：只知道 `χ ≥ k`。Mycielski 的 23 點圖 `ω = 2`、`χ = 5`。
3. **「DSATUR 是精確演算法」**：它在二分圖上精確，一般圖上不是。我們的 8 點反例它用 4 色、`χ = 3`。而且結果會隨平手規則改變——比較兩份 DSATUR 實作的結果前，先確認平手規則一致。
4. **Brooks 漏掉奇圈**：`Δ = 2` 時 `C_5` 需要 3 色，不在 `≤ Δ` 範圍內。另外「不連通」的圖要分量各自套用，而且是看每個分量是不是例外圖，不是看整張圖。
5. **把 Vizing 當成「邊著色沒有難題」**：`Δ+1` 好做，判斷能不能 `Δ` 仍是 NP-complete。另外 Vizing 的 `Δ+1` 只對**簡單圖**成立；有重邊的多重圖上界是 `Δ + μ`（μ 是最大重數），Shannon 定理給 `⌊3Δ/2⌋`。

## 進階：再往深一層

- **List coloring（清單著色）**：每個點只能從自己的清單挑色。Brooks 定理有清單版本（Erdős–Rubin–Taylor 1979 / Vizing 1976），也是 Ch 19 Thomassen 五清單著色定理的背景。
- **Degeneracy 與 smallest-last**：反覆拿掉最小度數點得到的順序倒過來著色，用色 `≤ 1 + max_H δ(H)`。平面圖必有度數 `≤ 5` 的點（Ch 18），所以它保證平面圖 6 色，Ch 19 再推到 5。
- **近似困難度**：除非 P = NP，色數無法在 `n^{1-ε}` 倍以內近似（Zuckerman 2007 的結果，屬 PCP 領域）。所以「找個好近似演算法」這條路在一般圖上走不通，實務靠結構與啟發式。
- **Total coloring、Hadwiger 猜想**：把點和邊一起著色的 total coloring 猜想（`≤ Δ+2`）至今未解；Hadwiger 猜想把四色定理推廣到「沒有 `K_t` minor 則 `t-1` 可著色」，t ≥ 7 仍開放。

## 動手練習

1. 把皇冠圖的交錯順序改成 `a0 a1 b0 b1 a2 b2 ...`，預測用色數再實跑驗證。
2. 把 `brooks_color` 情況 1 的 `[::-1]` 拿掉（改成由近到遠），在 atlas 上跑，看 `StopIteration` 從哪張圖丟出來，並解釋為什麼。
3. 用 `chromatic_number` 驗證 Grötzsch 圖去掉任何一條邊之後 `χ` 降為 3（它是 4-critical）。
4. 寫一個暴力程式，對 atlas 裡每張圖求「最佳貪婪順序」需要排在第一個的點有哪些可能，觀察和度數的關係。

## 本章重點整理

- 貪婪著色 `≤ Δ+1`，而且一定存在讓它達到 `χ` 的順序；問題全在排序。
- Welsh–Powell 靜態排度數，DSATUR 動態看飽和度；DSATUR 在二分圖最佳，一般圖不是。
- Brooks：連通、非完全圖、非奇圈 → `χ ≤ Δ`。證明的核心是「讓每個點著色時都還有一個未著色的鄰居」。
- `χ ≥ ω` 可以差任意多：Mycielski 構造保持無三角形、每次色數 +1。
- 著色多項式 `P(G, k)` 數 k 著色的方法數，deletion–contraction：`P(G) = P(G − e) − P(G / e)`。
- 邊著色：`Δ ≤ χ' ≤ Δ+1`（Vizing），Misra–Gries 用 fan 旋轉 + Kempe 交換構造 `Δ+1` 著色。

## 自我檢核

- [ ] 不看筆記，寫出「存在一個順序讓貪婪法達到 χ」的證明，並指出歸納哪一步用到「同色點不相鄰」。
- [ ] 如果有人說「我的圖最大團是 3，所以 3 個暫存器一定夠」，你會用哪個構造反駁？畫出它的前兩步。
- [ ] Brooks 證明情況 3 裡，為什麼 x、y 必須不相鄰、`G - x - y` 必須連通？少了任一條，哪一步會壞掉？
- [ ] 解釋 DSATUR 的結果為什麼會依賴平手規則，以及這對「對拍兩份實作」有什麼影響。
- [ ] 說出一張 Class 2 的圖，並用 `χ' = χ(L(G))` 解釋你會怎麼驗證它。

## 延伸閱讀

### 論文

- **[Three short proofs in graph theory](https://doi.org/10.1016/0095-8956(75)90089-1)** — L. Lovász，Journal of Combinatorial Theory Series B 19(3)（1975）
  - **核心貢獻**：用「排序讓每點都有未著色鄰居」的想法，給出 Brooks 定理一頁長的證明，本章的三個情況就是它的展開。
  - **讀哪裡**：第一個證明（Brooks）；原文很精簡，細節省略多，對照本章的情況 2、3 讀。
  - **前提**：懂 2-連通、割點（Ch 11）。
- **[New methods to color the vertices of a graph](https://cacm.acm.org/research/new-methods-to-color-the-vertices-of-a-graph)** — D. Brélaz，Communications of the ACM 22(4)（1979）
  - **核心貢獻**：提出 DSATUR，並說明它在二分圖上精確；也把它用在求最大團的分支定界。
  - **讀哪裡**：演算法描述與實驗段落。
  - **和本章的關聯**：本章 `dsatur` 的飽和度定義與平手規則（比未著色鄰居數）都來自這篇。
- **[A constructive proof of Vizing's theorem](https://www.cs.utexas.edu/~misra/psp.dir/vizing.pdf)** — J. Misra、D. Gries，Information Processing Letters 41(3)（1992）
  - **核心貢獻**：把 Vizing 定理寫成可直接實作的 fan / inversion / rotation 演算法，三頁內完成證明。
  - **讀哪裡**：全文；本章只給架構的「第 4 步為什麼找得到 w」就在它的引理裡。
  - **前提**：懂 Kempe 交換（交錯路徑反色）。
- **The smallest hard-to-color graph for algorithm DSATUR** — R. Janczewski、M. Kubale、K. Manuszewski、K. Piwakowski，Discrete Mathematics 236（2001）
  - **這篇說什麼**：定義「不管怎麼平手都著錯」的 hard-to-color 圖，並找出 DSATUR 的最小這種圖。
  - **和本章的關聯**：本章的 8 點反例只對「我們的平手規則」成立；這篇處理的是更強的「所有平手規則」版本。

### 書籍

- **《Introduction to Graph Theory》** — Douglas B. West（Prentice Hall，第 2 版，2001）
  - **讀哪幾章**：5.1（上界、Brooks）、5.2（Mycielski、critical graph）、7.1（線圖與邊著色、Vizing）。
  - **定位**：習題豐富，Brooks 有另一種證明可以和 Lovász 版對照。
- **《Graph Theory》** — Reinhard Diestel（Springer GTM 173，第 5 版，2017）
  - **讀哪幾章**：第 5 章 Colouring；5.3 Edge colouring 給 Vizing 的完整證明。
  - **前提**：本章讀完即可；Diestel 寫得精煉，每行都要停下來想。

下一章換一個角度：不是找更好的著色演算法，而是找一類**著色變容易**的圖——chordal graph，它正是 SSA 形式程式的 interference graph。

→ [Ch 16 Chordal graph 與完美消去序](./16-chordal-graphs.md)
