# Ch 8 — Euler 與 Hamilton：容易與困難的分水嶺

> **目標**：完整證明 Euler 定理，並把證明「展開」成 Hierholzer 線性時間演算法；完整證明 Ore 定理（Dirac 定理是推論）；用小圖窮舉看清楚為什麼「每條邊走一次」是多項式問題，「每個點走一次」卻是 NP-complete 的代表。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

1736 年 Euler 回答柯尼斯堡七橋問題時，沒有試遍所有走法——他證明了一個只看**度數**的條件，讓「能不能一筆畫」變成數數就能回答的問題。這是圖論的起點，也是演算法史上第一個「把搜尋換成刻畫」的例子。

兩百年後，Hamilton 把問題改成「每個**點**恰好經過一次」。字面上只差一個字，命運完全不同：至今沒有人找到只看局部資訊的刻畫，Karp 在 1972 年把它列進最早的 21 個 NP-complete 問題。今天它化身成 TSP、排程、基因組定序裡的難題。

這章的重點不只是兩個定理，而是讓你看出「容易」與「困難」在證明層面長什麼樣：Euler 的證明能直接變成演算法；Hamilton 的充分條件（Dirac、Ore）只能保證存在，而且離「必要」很遠。

## 先建立直覺

```text
           B (北岸)
          /│ \
         / │  \           七座橋，四塊陸地
        A══╪═══D          A: 5 座橋   B: 3   C: 3   D: 3
         \ │  /
          \│ /            一筆畫要「進一次就出一次」：
           C (南岸)        除了起點終點，每塊陸地的橋數都得是偶數
```

（A 是島，A–B、A–C 各有兩座橋，圖上用一條線代表；`══` 表示 A–D 一座橋，B–D、C–D 各一座。）

直覺是「進出配對」：走一條不重複邊的路線，每次**穿過**一個點，就用掉它兩條邊（一進一出）。所以中間點的度數都要是偶數；只有起點和終點可以各剩一條沒配到對。四塊陸地全是奇數，答案是不行。

Hamilton 沒有這種配對結構。「經過一個點」不會消耗什麼可以數的局部資源——它是全域的選擇問題：這一步走 `u` 而不走 `w`，可能在二十步之後才發現走死了。

> 本章用到 Ch 7 的術語：**走**（walk）可重複點與邊，**跡**（trail）不重複邊，**路徑**不重複點。如果不熟，先回看 [Ch 7 — 圖的定義、表示與同構](./07-graph-definitions.md) 的「定義」一節。

## Euler 定理與完整證明

**定義**：圖 `G` 的 **Euler 迴路（Euler circuit）** 是一條經過每條邊恰好一次、起點等於終點的跡；**Euler 跡（Euler trail）** 不要求回到起點。本節的 `G` 是**多重圖**（允許平行邊與自環，自環對度數貢獻 2），因為七橋本身就有平行邊。

**定理（Euler 1736 給出必要性；Hierholzer 1873 證明充分性）**：設 `G` 的所有邊都在同一個連通分量裡（孤立點不管）。則 `G` 有 Euler 迴路 ⇔ 每個點的度數都是偶數。

**必要性（⇒）**：沿 Euler 迴路走。每次經過一個中間點，用掉它一條「進」邊和一條「出」邊；起點的第一條出邊和最後一條進邊也配成一對。每條邊恰好被走一次，所以每個點的度數 = 2 ×（被經過的次數），是偶數。自環在同一次經過裡同時是進和出，度數算 2，同樣成立。

**充分性（⇐）先證一個引理。**

**引理**：若圖 `H` 至少有一條邊，且每個點的度數都是偶數，則 `H` 含有一個圈（這裡自環算長度 1 的圈，兩條平行邊算長度 2 的圈）。

**證明**：若 `H` 有自環或平行邊，直接得證。否則 `H` 是簡單圖；在有邊的點之間取一條最長路徑 `P = v0 v1 … vk`（`k ≥ 1`）。`v0` 度數是正的偶數，所以至少 2，除了 `v0v1` 之外還有一條邊 `v0w`。若 `w` 不在 `P` 上，把 `w` 接到 `P` 前面得到更長的路徑，矛盾；所以 `w = vi`，且 `i ≥ 2`（`w ≠ v1`，因為沒有平行邊）。`v0 v1 … vi v0` 就是一個圈。∎

**充分性證明**：對邊數 `m` 做強歸納。`m = 0` 時空迴路就是答案。設 `m ≥ 1`，所有度數偶數。由引理，`G` 有一個圈 `C`。從 `G` 刪掉 `C` 的所有邊得到 `G'`。`C` 對每個點的度數貢獻 0 或 2，所以 `G'` 的度數仍全是偶數。

設 `G'` 中有邊的連通分量是 `H1, …, Hr`。每個 `Hi` 邊數少於 `m`、度數全偶、且自己連通，由歸納假設各有一條 Euler 迴路 `Ci`。

每個 `Hi` 都和 `C` 共用至少一個點：否則 `Hi` 的點在 `G` 中的所有邊都不在 `C` 上，因而都在 `G'` 中、也就都在 `Hi` 中，`Hi` 會是 `G` 中一個和 `C` 分開的有邊分量，違反「所有邊都在同一個分量」。

**拼接**：沿 `C` 走；第一次走到某個 `Hi` 的點 `x` 時，先繞完 `Ci`（從 `x` 出發、回到 `x`），再繼續沿 `C` 走。每個 `Hi` 只在第一次碰到時繞一次。`C` 的邊和各 `Ci` 的邊互不重疊、合起來正好是 `E(G)`，所以得到 `G` 的 Euler 迴路。∎

**推論（Euler 跡）**：所有邊在同一分量的 `G` 有 Euler 跡 ⇔ 奇度點有 0 或 2 個。（⇐，2 個的情況）在兩個奇點 `a, b` 之間加一條新邊 `e`，度數全變偶數，取 Euler 迴路，再把 `e` 刪掉，迴路斷成一條從 `a` 到 `b` 的跡。（⇒）跡的起終點以外都是配對的，奇點只能是起點和終點。

**有向版本**：有向圖的所有邊在同一個（弱）連通分量裡，則有 Euler 迴路 ⇔ 每個點入度 = 出度。證明完全一樣，只是把「進出配對」換成「入邊和出邊配對」。下面的 de Bruijn 序列會用到它。

```python
import networkx as nx

# 柯尼斯堡：4 塊陸地 A(島) B(北岸) C(南岸) D(東邊)，7 座橋 → 多重圖
bridges = [("A", "B"), ("A", "B"), ("A", "C"), ("A", "C"),
           ("A", "D"), ("B", "D"), ("C", "D")]
G = nx.MultiGraph(bridges)
odd = [v for v, d in sorted(G.degree()) if d % 2]
print("度數:", dict(sorted(G.degree())))
print("奇度點:", odd)
print("有 Euler 迴路?", nx.is_eulerian(G), " 有 Euler 跡?", nx.has_eulerian_path(G))

# 多蓋一座 B–C 橋：奇度點剩 A、D 兩個 → 有跡沒迴路
G.add_edge("B", "C")
print("加橋後奇度點:", [v for v, d in sorted(G.degree()) if d % 2],
      " 迴路?", nx.is_eulerian(G), " 跡?", nx.has_eulerian_path(G))
```

```text
度數: {'A': 5, 'B': 3, 'C': 3, 'D': 3}
奇度點: ['A', 'B', 'C', 'D']
有 Euler 迴路? False  有 Euler 跡? False
加橋後奇度點: ['A', 'D']  迴路? False  跡? True
```

## 底層機制：Hierholzer 是把歸納證明「攤平」

上面的證明有兩個動作：找一個圈、把子迴路拼進去。Hierholzer 演算法就是這兩個動作的迭代版：

```text
stack = [start]
重複：
  看 stack 頂端的點 v
  v 還有沒用過的邊 (v, w)？ → 標記用過，把 w 推上 stack      ← 「往前走」：在找圈
  否則                     → 把 v 彈出，接到答案尾端           ← 「退回」：子迴路拼接完成
最後把答案反轉
```

為什麼「往前走」一定會走回原點才卡住？因為度數全偶：走進一個點 `v ≠ start` 時，`v` 已用掉的邊數是奇數（之前每次經過用 2 條，這次進來又用 1 條），偶數減奇數不為 0，所以 `v` 一定還有未用的邊。唯一可能卡住的點是 `start`——這正是引理「偶度數圖一定有圈」的演算法版本。

退回時，stack 上較低的點若還有未用的邊，就從那裡再往前走出一個新的閉合跡；這個新跡在答案序列裡會被插在那個點的位置——正是證明中「第一次碰到 `Hi` 時繞完 `Ci`」。每條邊被標記一次、每個點的鄰接串列指標只往前移，總時間 `O(n + m)`。

```python
import random
from collections import Counter
import networkx as nx


def euler_circuit(n, edges, start=0):
    """Hierholzer（迭代版）。n 個點 0..n-1，edges 是 (u, v) 列表，允許平行邊與自環。
    回傳 Euler 迴路的邊編號序列；條件不成立時丟 ValueError。"""
    inc = [[] for _ in range(n)]            # inc[v] = [(邊編號, 另一端)]
    deg = [0] * n
    for i, (u, v) in enumerate(edges):
        inc[u].append((i, v))
        if u != v:
            inc[v].append((i, u))           # 自環只放一次，免得被走兩次
        deg[u] += 1
        deg[v] += 1                         # 自環的度數算 2
    odd = [v for v in range(n) if deg[v] % 2]
    if odd:
        raise ValueError(f"奇度點 {odd}")
    used = [False] * len(edges)
    ptr = [0] * n                           # 每個點的鄰接串列讀到哪：保證每條邊只掃一次
    stack = [(start, None)]                 # (目前節點, 走進來的邊)
    circuit = []
    while stack:
        v, via = stack[-1]
        while ptr[v] < len(inc[v]) and used[inc[v][ptr[v]][0]]:
            ptr[v] += 1
        if ptr[v] == len(inc[v]):           # v 沒有未用的邊了：退回並記錄
            stack.pop()
            if via is not None:
                circuit.append(via)
        else:
            i, w = inc[v][ptr[v]]
            used[i] = True
            stack.append((w, i))
    if len(circuit) != len(edges):
        raise ValueError("有邊的部分不連通")
    return circuit[::-1]


def check(n, edges, circ, start=0):
    assert sorted(circ) == list(range(len(edges)))   # 每條邊恰一次
    v = start
    for i in circ:                                   # 首尾相接
        a, b = edges[i]
        assert v in (a, b)
        v = b if v == a else a
    assert v == start


edges = [(0, 1), (1, 2), (2, 0), (0, 3), (3, 4), (4, 0), (1, 1)]   # 兩個三角形共用 0，加自環
c = euler_circuit(5, edges)
check(5, edges, c)
print("迴路（邊編號）:", c)

try:
    euler_circuit(4, [(0, 1), (1, 2), (2, 3)])
except ValueError as e:
    print("路徑圖:", e)
try:
    euler_circuit(6, [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3)])
except ValueError as e:
    print("兩個不相連的三角形:", e)

# 對拍：隨機多重圖。先隨機加邊，再把奇度點兩兩配對補邊，讓度數全偶
rng = random.Random(2024)
agree = 0
for t in range(500):
    n = rng.randint(2, 9)
    edges = [(rng.randrange(n), rng.randrange(n)) for _ in range(rng.randint(1, 20))]
    deg = Counter()
    for u, v in edges:
        deg[u] += 1
        deg[v] += 1
    odd = [v for v in range(n) if deg[v] % 2]
    edges += list(zip(odd[::2], odd[1::2]))
    G = nx.MultiGraph()
    G.add_nodes_from(range(n))
    G.add_edges_from(edges)
    H = G.subgraph([v for v in G if G.degree(v) > 0])
    expect = nx.is_eulerian(H) if len(H) else True
    start = edges[0][0]
    try:
        check(n, edges, euler_circuit(n, edges, start), start)
        got = True
    except ValueError:
        got = False
    assert got == expect, (n, edges)
    agree += got
print(f"500 張隨機多重圖判斷全部一致：{agree} 張有 Euler 迴路（每條都驗過），其餘有邊的部分不連通")
```

```text
迴路（邊編號）: [0, 6, 1, 2, 3, 4, 5]
路徑圖: 奇度點 [0, 3]
兩個不相連的三角形: 有邊的部分不連通
500 張隨機多重圖判斷全部一致：437 張有 Euler 迴路（每條都驗過），其餘有邊的部分不連通
```

看第一個例子的輸出 `[0, 6, 1, 2, 3, 4, 5]`：從 0 出發走邊 0 到點 1，在 1 繞自環（邊 6），再走完第一個三角形回到 0，接著走第二個三角形。自環被「插」進了三角形中間——拼接動作在輸出裡看得見。

幾個實作決策：

- **為什麼是迭代版**：遞迴版 Hierholzer 很短，但遞迴深度可以到 `m`。Python 預設遞迴上限是 1000，一條 10 萬條邊的迴路就會 `RecursionError`。本課 Part 2 的演算法一律寫迭代版。
- **為什麼用 `ptr`**：若每次都從頭掃 `inc[v]` 找沒用過的邊，高度數點會被重複掃描，最壞變成 `O(m²)`。
- **連通性用「答案長度」判斷**：`len(circuit) != len(edges)` 代表有邊沒被走到，也就是有邊的部分不連通。不需要另外跑一次 BFS。

在同一台機器（AMD Ryzen 7 5700G、Windows 11、Python 3.13.12）上實測，這個函式處理約 112 萬條邊的隨機多重圖約 4.8 秒（含建鄰接串列），時間大致和邊數成正比。

### 歷史上的對手：Fleury

Fleury（1883）的演算法是「每一步都避免走橋（bridge），除非別無選擇」。它是正確的，但每一步要判斷某條邊是不是當下的橋，樸素作法每步 `O(m)`，總共 `O(m²)`。Hierholzer 比它早十年發表、而且是線性時間；教科書仍常教 Fleury，是因為它的「不要燒掉退路」很好直覺理解。橋的線性時間判定在 [Ch 11](./11-connectivity-tarjan.md)。

## 應用：de Bruijn 序列與循環 pattern

**問題**：找一個最短的循環序列，讓每個長度 `n` 的 `k` 進位字串都恰好出現一次當連續子字串。

把每個長度 `n−1` 的字串當點，每個長度 `n` 的字串 `a1…an` 當一條邊 `a1…a(n−1) → a2…an`。每個點恰有 `k` 條出邊（後面接哪個符號）和 `k` 條入邊（前面是哪個符號），而且任兩點之間可以靠「一次推進一個符號」互相到達。由有向版 Euler 定理，有 Euler 迴路；沿迴路把每條邊新增的那個符號抄下來，就得到長度 `k^n` 的 de Bruijn 序列 `B(k, n)`。

```python
from itertools import product


def de_bruijn(k, n):
    """B(k, n)：長度 k^n 的循環序列，每個長度 n 的 k 進位字串恰好出現一次（n >= 2）。
    節點 = 長度 n-1 的元組，邊 = 長度 n 的元組（前 n-1 位 → 後 n-1 位）。
    每個點入度 = 出度 = k，而且強連通 → 有 Euler 迴路（有向版 Hierholzer）。"""
    nodes = list(product(range(k), repeat=n - 1))
    ptr = {v: 0 for v in nodes}           # 每個點下一條沒走過的出邊（= 附加的符號）
    stack, popped = [nodes[0]], []
    while stack:
        v = stack[-1]
        if ptr[v] < k:
            a = ptr[v]
            ptr[v] += 1
            stack.append(v[1:] + (a,))    # 沿邊 v -> v[1:]+a 前進
        else:
            popped.append(stack.pop())
    path = popped[::-1]                   # Euler 迴路的節點序列，長度 k^n + 1
    return [v[-1] for v in path[1:]]      # 走進每個點的那條邊，貢獻它的最後一個符號


def covers_all(seq, k, n):
    s = list(seq) + list(seq[: n - 1])    # 循環：尾巴接回開頭
    windows = {tuple(s[i:i + n]) for i in range(len(seq))}
    return len(seq) == k ** n and len(windows) == k ** n


seq = de_bruijn(2, 4)
print("".join(map(str, seq)), len(seq), covers_all(seq, 2, 4))

seq = de_bruijn(10, 4)                    # 4 位數 PIN
print("B(10,4) 長度", len(seq), covers_all(seq, 10, 4),
      "→ 線性展開按", len(seq) + 3, "個鍵；逐組輸入要按", 4 * 10**4, "個鍵")
```

```text
0100110101111000 16 True
B(10,4) 長度 10000 True → 線性展開按 10003 個鍵；逐組輸入要按 40000 個鍵
```

資安工程師其實天天用它：pwntools 的 `cyclic()` 產生的 pattern 就是 de Bruijn 序列，buffer overflow 蓋到 return address 後，看暫存器裡那 4（或 8）個 byte，就能從「每個子字串只出現一次」反查出偏移量。PIN 的例子則說明了「沒有逐鍵重設」的鍵盤為什麼用約四分之一的按鍵數就能遍歷所有密碼。

## Hamilton 圈：只有充分條件

**定義**：**Hamilton 圈（Hamiltonian cycle）**是經過每個點恰好一次的圈；有 Hamilton 圈的圖叫 **Hamiltonian**。（只考慮簡單圖，`n ≥ 3`。）

必要條件很容易找（連通、沒有割點、二分圖兩側大小相等……），但沒有一個是充分的。反方向，我們有的是「度數夠大就一定有」的充分條件。

**定理（Ore 1960）**：設 `G` 是 `n ≥ 3` 個點的簡單圖，且**任兩個不相鄰**的點 `u, v` 都滿足 `deg(u) + deg(v) ≥ n`。則 `G` 是 Hamiltonian。

**推論（Dirac 1952）**：`n ≥ 3` 且最小度數 `δ(G) ≥ n/2`，則 `G` 是 Hamiltonian。（任兩點度數和 `≥ n/2 + n/2 = n`，套 Ore。）

**Ore 定理的證明**：反設存在反例 `G`。只要還不是 Hamiltonian，就繼續往 `G` 加邊——加邊只會讓度數變大，Ore 條件保持成立。`K_n`（`n ≥ 3`）是 Hamiltonian，所以這個過程會停在某個**極大非 Hamiltonian** 圖 `G*`：`G*` 滿足 Ore 條件、不是 Hamiltonian，但再加任何一條缺的邊就變成 Hamiltonian。

`G*` 不是完全圖，取不相鄰的 `u, v`。`G* + uv` 有 Hamilton 圈，而且它一定用到 `uv`（否則 `G*` 本身就有）。把 `uv` 拿掉，得到 `G*` 中一條 Hamilton **路徑**：

```text
u = w1 ─ w2 ─ w3 ─ … ─ wi ─ w(i+1) ─ … ─ wn = v
```

定義兩個下標集合（都是 `{1, …, n−1}` 的子集）：

- `S = { i : u ~ w(i+1) }`。`u` 的每個鄰居都是某個 `w(i+1)`，`i ∈ {1, …, n−1}`，所以 `|S| = deg(u)`。
- `T = { i : v ~ wi }`。`v` 的每個鄰居都是某個 `wi`，`i ∈ {1, …, n−1}`，所以 `|T| = deg(v)`。

`|S| + |T| = deg(u) + deg(v) ≥ n`，但兩者都在大小 `n−1` 的集合裡，由鴿籠原理 `S ∩ T ≠ ∅`。取 `i ∈ S ∩ T`：`u ~ w(i+1)` 且 `v ~ wi`。注意 `i ≠ 1`（`v ~ w1 = u` 不成立）且 `i ≠ n−1`（`u ~ wn = v` 不成立）。於是

```text
w1 → w(i+1) → w(i+2) → … → wn → wi → w(i−1) → … → w2 → w1

u ─ w2 ─ … ─ wi    w(i+1) ─ … ─ v
│            ╰──────────────────╯ (v ~ wi)
╰──────────────────╯ (u ~ w(i+1))
```

這是一個經過所有點的圈：用 `u w(i+1)` 和 `v wi` 兩條邊取代路徑上的 `wi w(i+1)`。`G*` 是 Hamiltonian，矛盾。∎

證明裡藏著更強的結論，叫 **Bondy–Chvátal 閉包**：若 `u ≁ v` 且 `deg(u) + deg(v) ≥ n`，則 `G` Hamiltonian ⇔ `G + uv` Hamiltonian。反覆加這種邊直到不能再加，得到的閉包若是完全圖，原圖就是 Hamiltonian。Ore 與 Dirac 都是「閉包是完全圖」的特例。

### 窮舉驗證：條件成立就一定有，但差得很遠

Hamilton 判定沒有已知的多項式演算法，小圖上我們用 Held–Karp 式的位元 DP：`reach[mask]` 記錄「從 0 出發、恰好走過 `mask` 這些點的路徑，可以停在哪些點」。時間 `O(2^n · n²)`，比 `n!` 好很多，但仍是指數。

```python
import networkx as nx


def hamiltonian_cycle(adj):
    """Held–Karp 式位元 DP：O(2^n · n^2)。回傳一個 Hamilton 圈（節點列表）或 None。"""
    V = sorted(adj)
    n = len(V)
    if n < 3:
        return None
    idx = {v: i for i, v in enumerate(V)}
    nb = [sum(1 << idx[w] for w in adj[v]) for v in V]   # 鄰居的 bitmask
    # reach[mask] 的第 v 位 = 存在一條從 0 出發、恰好走過 mask、停在 v 的路徑
    reach = [0] * (1 << n)
    reach[1] = 1
    for mask in range(1, 1 << n):
        if not mask & 1:
            continue
        ends = reach[mask]
        v = 0
        while ends:
            if ends & 1:
                for w in range(n):
                    if nb[v] >> w & 1 and not mask >> w & 1:
                        reach[mask | 1 << w] |= 1 << w
            ends >>= 1
            v += 1
    full = (1 << n) - 1
    last = [v for v in range(1, n) if reach[full] >> v & 1 and nb[v] & 1]
    if not last:
        return None
    # 從尾端往回還原路徑
    path, mask, v = [], full, last[0]
    while v != 0:
        path.append(V[v])
        mask ^= 1 << v
        v = next(u for u in range(n) if reach[mask] >> u & 1 and nb[u] >> v & 1)
    return [V[0]] + path[::-1]


def ore(adj):
    n = len(adj)
    return all(len(adj[u]) + len(adj[v]) >= n
               for u in adj for v in adj if u < v and v not in adj[u])


def dirac(adj):
    n = len(adj)
    return all(2 * len(adj[v]) >= n for v in adj)


for n in range(3, 8):
    tot = ham = n_ore = n_dirac = 0
    for G in nx.graph_atlas_g():
        if G.number_of_nodes() != n:
            continue
        adj = {v: set(G[v]) for v in G}
        cyc = hamiltonian_cycle(adj)
        if cyc:
            assert sorted(cyc) == sorted(adj)
            assert all(cyc[(i + 1) % n] in adj[cyc[i]] for i in range(n))
        tot += 1
        ham += cyc is not None
        if ore(adj):
            n_ore += 1
            assert cyc is not None, "Ore 定理被推翻了?!"
        if dirac(adj):
            n_dirac += 1
            assert ore(adj)
    print(f"n={n}: {tot:4d} 類，Hamilton {ham:3d}，滿足 Ore {n_ore:3d}，滿足 Dirac {n_dirac:3d}")

# 緊性：K_{k,k+1} 的最小度數 k = (n-1)/2，差一點點就滿足 Dirac，但沒有 Hamilton 圈
for k in (2, 3, 4):
    G = nx.complete_bipartite_graph(k, k + 1)
    adj = {v: set(G[v]) for v in G}
    print(f"K_{k},{k + 1}: n={len(adj)} δ={min(map(len, adj.values()))} Hamilton? {hamiltonian_cycle(adj) is not None}")
```

```text
n=3:    4 類，Hamilton   1，滿足 Ore   1，滿足 Dirac   1
n=4:   11 類，Hamilton   3，滿足 Ore   3，滿足 Dirac   3
n=5:   34 類，Hamilton   8，滿足 Ore   5，滿足 Dirac   3
n=6:  156 類，Hamilton  48，滿足 Ore  21，滿足 Dirac  19
n=7: 1044 類，Hamilton 383，滿足 Ore  68，滿足 Dirac  29
K_2,3: n=5 δ=2 Hamilton? False
K_3,4: n=7 δ=3 Hamilton? False
K_4,5: n=9 δ=4 Hamilton? False
```

三件事值得看：

1. **定理在所有 ≤ 7 點的圖上都沒被推翻**，每個 DP 找到的圈也都驗過。Hamiltonian 圖的個數 1、3、8、48、383 和 OEIS A003216（Number of Hamiltonian graphs with n nodes）一致，可以當 DP 正確的旁證。
2. **充分條件差得很遠**：7 點時有 383 類 Hamiltonian，Ore 只認得 68 類、Dirac 只認得 29 類。`C7` 每個點度數 2，任何條件都套不上，但它本身就是一個 Hamilton 圈。
3. **Dirac 是緊的**：`K_{k,k+1}` 的 `δ = k = (n−1)/2`，只比 `n/2` 少半個，就沒有 Hamilton 圈（二分圖的圈在兩側交替，兩側大小不同就不可能經過所有點）。

## 為什麼一個多項式、一個 NP-complete？

| | Euler | Hamilton |
| --- | --- | --- |
| 要遍歷的 | 每條邊一次 | 每個點一次 |
| 刻畫 | 度數全偶 + 連通（充要） | 只有充分條件（Dirac、Ore、閉包） |
| 檢查條件 | `O(n + m)` | 判定是 NP-complete（Karp 1972） |
| 找出解 | Hierholzer `O(n + m)` | 已知最好的一般演算法是指數時間 |
| 局部修補 | 走錯了可以「插入子迴路」補救 | 走錯一步可能要全部重來 |

直覺上的差別在證明裡就看得到：Euler 的歸納在每一步都可以**局部修補**——找到任何一個圈都行，剩下的拼進去就好，沒有走錯的可能。Hamilton 的 Ore 證明是**非構造性的**：它用「極大反例」的論證，沒有告訴你一般圖上怎麼找圈（雖然在滿足 Ore 條件的圖上，反覆做那個交叉換邊動作其實可以寫成多項式演算法）。

精確的說法是：「判斷一張圖有沒有 Hamilton 圈」是 NP-complete，意思是如果它有多項式演算法，所有 NP 問題都有（P = NP）。這不是「已證明沒有快速演算法」——那是 P ≠ NP 猜想本身。Ch 34 會給出從 3-SAT（經由 vertex cover）到 Hamilton 圈的歸約，並解釋 NP-complete 的精確定義；這裡先記住「目前沒有人知道比指數更好的一般解法」。

在 AMD Ryzen 7 5700G 上用上面的 `hamiltonian_cycle` 實測 `G(n, 0.5)` 隨機圖（seed = n）：`n = 16` 約 0.4 秒、`n = 18` 約 1.6 秒、`n = 20` 約 15 秒。每多 2 個點，`2^n` 就乘 4，再加上 Python 的 list 在 `2^20` 規模開始吃記憶體頻寬，實測放大接近 10 倍。對照 Euler：百萬條邊不到 5 秒。

## 對比與取捨

| 方法 | 問題 | 時間 | 優點 | 缺點 |
| --- | --- | --- | --- | --- |
| Hierholzer | Euler 迴路/跡 | `O(n + m)` | 線性、證明即演算法 | 需注意迭代實作 |
| Fleury | Euler 迴路/跡 | `O(m²)` 樸素 | 直覺易懂 | 慢，需要橋判定 |
| 回溯搜尋 | Hamilton | 最壞 `O(n!)` | 稀疏圖上常常很快 | 最壞情況爆炸 |
| Held–Karp 位元 DP | Hamilton / TSP | `O(2^n n²)` 時間、`O(2^n n)` 空間 | 最壞情況保證 | 空間是瓶頸，`n` 約 25 以上就不實際 |
| SAT 編碼 | Hamilton | 視 solver | 工業 solver 很強 | 編碼要小心，見 Ch 35 |

## 踩雷集錦

1. **忘記連通條件**：「度數全偶 ⇒ 有 Euler 迴路」是錯的——兩個不相連的三角形度數全是 2。正確敘述是「有邊的部分連通 + 度數全偶」。反過來，孤立點不影響答案，`nx.is_eulerian` 卻會因為孤立點回傳 `False`（它要求整張圖連通），對拍時要先把孤立點拿掉，上面的程式就是這樣做的。
2. **自環的度數**：自環對度數貢獻 2，不是 1。實作時若把自環放進鄰接串列兩次，Hierholzer 會試圖走它兩次；放一次、度數加 2 才對。
3. **遞迴版 Hierholzer 在大圖上爆掉**：Python 預設遞迴深度 1000。`sys.setrecursionlimit` 調大可能讓直譯器 C stack 溢位直接崩潰，不如寫迭代版。
4. **把 Dirac/Ore 當成必要條件**：「度數不夠大所以沒有 Hamilton 圈」是錯的推理；`C_n` 就是反例。這兩個定理只能用來說「有」。
5. **以為 Euler 跡可以從任何點出發**：有兩個奇點時，Euler 跡**必須**從其中一個奇點出發、在另一個結束。從偶點出發的 Hierholzer 會卡住並漏掉邊。

## 進階：再往深一層

- **中國郵差問題（Chinese postman / route inspection）**：圖沒有 Euler 迴路時，最少要重複走多少邊長？答案是把奇點兩兩配對、讓配對路徑總長最小——一個最小權完美匹配問題，Ch 23 的 Blossom 演算法讓它變成多項式時間。這是 Euler 「容易」的延伸：加了權重仍然容易。
- **TSP 與近似**：Hamilton 加上權重就是 TSP。度量 TSP 的 Christofides 演算法正是「MST（Ch 10）+ 奇點的最小匹配 + Euler 迴路 + 抄捷徑」，近似比 1.5——這門課 Part 2 的三個工具一次用上。
- **更強的充分條件**：Chvátal（1972）的度數序列條件比 Ore 更強，Bondy–Chvátal 閉包統一了它們；Dirac 型條件在極值圖論（Ch 32）裡還有大量推廣。
- **組合學裡的 Euler 迴路**：de Bruijn 序列的個數可以用 BEST 定理（數有向圖的 Euler 迴路）加矩陣樹定理（Ch 28）精確算出來。

## 動手練習

1. 把 `euler_circuit` 改成 `euler_trail`：先檢查奇點個數是 0 或 2，自動選奇點當起點。故意從偶點出發，看看漏掉哪些邊。
2. 把 `hamiltonian_cycle` 的「找到一條就停」改成「數有幾條 Hamilton 圈」，驗證 `K_n` 有 `(n−1)!/2` 條。
3. 用 `nx.graph_atlas_g()` 找出 7 點中**滿足 Ore 但不滿足 Dirac** 的一張圖，手動找出它的 Hamilton 圈，再對照 Ore 證明裡的交叉換邊。
4. 寫一個「用 Bondy–Chvátal 閉包判斷」的函式，在 `n ≤ 7` 上數它能認得幾個 Hamiltonian 類別，和 Ore 的 68 類比較。

## 本章重點整理

- Euler：有邊部分連通 + 度數全偶 ⇔ Euler 迴路；證明是「找圈 + 拼接」的強歸納，Hierholzer 是它的迭代展開，`O(n + m)`。
- 有向版本把「度數偶」換成「入度 = 出度」，de Bruijn 序列與 pwntools 的 `cyclic()` 都來自它。
- Hamilton：Ore（不相鄰點度數和 ≥ n）⇒ Hamiltonian，Dirac 是推論；證明靠極大反例 + 鴿籠找交叉邊。條件只是充分，離必要很遠。
- Euler 判定是多項式、Hamilton 判定是 NP-complete；這不代表 Hamilton 已被證明沒有快速演算法，而是「有的話 P = NP」。

## 自我檢核

- [ ] 不看筆記，用「進出配對」說服一個同事：為什麼七橋問題沒有解，以及加哪一座橋就有一筆畫（跡）。
- [ ] 能說出 Hierholzer 演算法的哪一步對應 Euler 證明的「找圈」，哪一步對應「拼接」。
- [ ] 能在白板上重建 Ore 證明中的集合 `S`、`T`，並解釋為什麼 `|S ∪ T| ≤ n − 1`。
- [ ] 如果面試官問「Euler 和 Hamilton 只差一個字，為什麼難度天差地遠」，你能用一分鐘回答，而且不說出「已經證明 Hamilton 沒有多項式演算法」這種錯話。
- [ ] 能解釋 pwntools `cyclic()` 為什麼能從 crash 時的暫存器值反推出 overflow 偏移量。

## 延伸閱讀

### 論文

- **[Solutio problematis ad geometriam situs pertinentis（E53）](https://scholarlycommons.pacific.edu/euler-works/53/)** — Leonhard Euler，Commentarii academiae scientiarum Petropolitanae 8:128–140（1736 年卷，1741 年出版）
  - **核心貢獻**：七橋問題的原始論文；Euler 給出必要條件，並斷言（但沒有證明）充分性。
  - **讀哪裡**：Euler Archive 頁面有英譯本；看後半段的計數論證，你會發現他沒有「圖」這個詞，用的是陸地與橋的計數。
  - **前提**：本章的必要性證明。
- **[Ueber die Möglichkeit, einen Linienzug ohne Wiederholung und ohne Unterbrechung zu umfahren](https://doi.org/10.1007/BF01442866)** — Carl Hierholzer（Christian Wiener 整理），Mathematische Annalen 6(1):30–32（1873）
  - **核心貢獻**：第一個充分性證明，就是本章「找圈 + 拼接」的論證。Hierholzer 在論文發表前過世，由 Wiener 根據他的口述整理。
  - **讀哪裡**：全文只有三頁（德文）；看不懂德文的話，對照本章證明讀結構即可。
  - **前提**：本章的充分性證明。
- **[Note on Hamilton Circuits](https://doi.org/10.2307/2308928)** — Øystein Ore，American Mathematical Monthly 67(1):55（1960）
  - **核心貢獻**：一頁的論文，把 Dirac（[Some Theorems on Abstract Graphs](https://doi.org/10.1112/plms/s3-2.1.69)，Proc. London Math. Soc. 1952）的最小度數條件放寬成「不相鄰點度數和」。
  - **讀哪裡**：整頁；對照本章證明的「極大反例 + 交叉邊」。
  - **前提**：本章 Ore 定理一節。
- **[A method in graph theory](https://doi.org/10.1016/0012-365X(76)90078-9)** — J. A. Bondy、V. Chvátal，Discrete Mathematics 15(2):111–135（1976）
  - **核心貢獻**：提出閉包（closure）方法，把 Ore、Chvátal 等一系列 Hamilton 充分條件統一成一個框架，並推廣到其他性質。
  - **讀哪裡**：第 1–2 節（閉包的定義與 Hamilton 性的穩定性）。
  - **前提**：本章 Ore 證明；會讀定理–證明格式。
- **[Reducibility among Combinatorial Problems](https://doi.org/10.1007/978-1-4684-2001-2_9)** — Richard M. Karp，收於 *Complexity of Computer Computations*（1972），pp. 85–103
  - **核心貢獻**：一次證明 21 個問題 NP-complete，Hamilton 圈（有向與無向）都在其中。
  - **讀哪裡**：開頭的歸約樹圖與 Hamilton 那一段；完整的歸約細節留到 Ch 34。
  - **前提**：知道「歸約」的大意即可。

### 書籍

- **《Introduction to Graph Theory》** — Douglas B. West（第 2 版，2001）
  - **讀哪幾章**：Ch 1「Fundamental Concepts」講 Euler 迴路；§7.2「Hamiltonian Cycles」講 Dirac、Ore、Chvátal 條件與閉包。
  - **前提**：本章。
- **《Graph Theory》** — Reinhard Diestel（第 6 版，2025）
  - **讀哪幾章**：§10.1「Sufficient conditions」，比本章多了 Chvátal–Erdős 定理（用連通度與獨立數給條件），連到 Ch 11–12 的連通度。
  - **前提**：本章 + Ch 12 的 Menger 定理會讓 Chvátal–Erdős 的證明更好讀。

Euler 迴路的證明裡，「刪掉一個圈後各分量獨立處理」是一種把圖拆小的思考方式。下一章研究**沒有圈**的連通圖——樹——並數出 `n` 個標記點上恰好有 `n^(n−2)` 棵。

→ [Ch 9 樹：等價定義、Cayley 公式、Prüfer 編碼](./09-trees-cayley-prufer.md)
