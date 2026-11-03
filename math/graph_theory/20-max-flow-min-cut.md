# Ch 20 — 最大流最小割與 Edmonds–Karp

> **目標**：能從定義證明 max-flow min-cut 定理、說清楚殘餘圖（residual graph）裡的反向邊為什麼非有不可、親手跑出 Ford–Fulkerson 在無理容量下不收斂到最大流的例子，並證明 Edmonds–Karp 是 `O(V E²)`；最後用 flow 重新證一次 Menger 定理。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

1955 年，RAND 的 T. E. Harris 和 F. S. Ross 寫了一份機密報告：蘇聯到東歐的鐵路網，最少要炸掉哪些路段，才能讓運量歸零？這是「最小割」。同一張網路反過來問：最多能運多少貨？這是「最大流」。Ford 和 Fulkerson 在 1956 年證明兩個問題的答案永遠相等，並給了一個演算法。（這段歷史的細節見延伸閱讀裡 Schrijver 的文章。）

在那之前，人們把運輸問題當成一般的線性規劃來解。可行，但看不到結構：LP 不會告訴你「卡住整個網路的是這三條邊」。max-flow min-cut 定理把「最多能送多少」和「最便宜的封鎖線」綁成同一個數字，這種 min-max 定理是 Part 4 的主旋律——Ch 22 的 König、Ch 26 的 LP 對偶都是它的親戚。

對系統/資安工程師，這不是古董：

- **攻擊圖的最小切斷集**（Ch 39）：要切斷攻擊者到 Domain Admin 的所有路徑，最少要修補幾個節點？這是最小點割，本章最後一節的拆點技巧直接可用。
- **網路可靠度**：兩台機器之間有幾條邊不相交的路徑，就能容忍幾條鏈路同時壞掉——這是 Menger 定理，[分散式系統課](../../systems/distributed_systems/README.md)談容錯拓撲時的隱含前提。
- **影像分割、排班、選課配對**：練習 D 會把它們全部建模成 flow。

## 先建立直覺

把網路想成水管系統。每條管子有一個最大流速（容量），水從源點 `s` 流到匯點 `t`，中間每個接頭進多少就出多少。問：`s` 每秒最多能送多少水到 `t`？

```text
        10          4           10
   s -------> a -------> c -------> t
   |          |  \       ^          ^
 10|         2|   \ 8    | 6        | 10
   |          v     \    |          |
   +--------> b --9----> d ---------+
```

本章第一個例子就用這張網路（程式裡的 `cap` 字典是它的精確版本）。直覺上的貪婪做法：找一條還有空間的路，灌滿它，重複。問題是貪婪會「選錯路」，而且選錯後沒辦法撤回。Ford–Fulkerson 的關鍵發明是**允許撤回**：如果 `a→b` 上已經有 1 單位的流，殘餘圖裡就多一條容量 1 的 `b→a`，走它等於「把剛才那 1 單位退回去，改走別條」。

另一個直覺是切割：把點分成兩邊 `S ∋ s`、`T ∋ t`，從 `S` 到 `T` 的邊容量總和就是一道「關卡」。所有水都得穿過這道關卡，所以**任何流 ≤ 任何割**。定理說的是：最緊的那道關卡剛好等於最大流。

> 如果你對 BFS 的最短路性質還不熟，先回看 [leetcode_patterns 的 BFS 章](../../algorithms/leetcode_patterns/19-graph-bfs.md)；本章 Edmonds–Karp 的證明完全建立在「BFS 找到的是邊數最少的路徑」上。

## 正式定義

- **流網路（flow network）**：有向圖 `G = (V, E)`，每條邊 `e` 有容量 `c(e) ≥ 0`，指定源點 `s`、匯點 `t`。為了記號乾淨，假設沒有反平行邊（若 `u→v`、`v→u` 都在，可以在其中一條中間插一個點）；程式裡我們用字典累加，不受這個限制。
- **流（flow）**：函數 `f: E → R`，滿足
  - 容量限制：`0 ≤ f(e) ≤ c(e)`
  - 流量守恆：對每個 `v ∉ {s, t}`，`Σ_{(u,v)∈E} f(u,v) = Σ_{(v,w)∈E} f(v,w)`
- **流值**：`|f| = Σ_{(s,v)} f(s,v) − Σ_{(u,s)} f(u,s)`，`s` 的淨流出量。
- **割（s-t cut）**：點集分割 `(S, T)`，`s ∈ S`、`t ∈ T`。容量 `c(S,T) = Σ_{u∈S, v∈T, (u,v)∈E} c(u,v)`——**只算 S 到 T 方向**的邊。
- **殘餘圖（residual graph）** `G_f`：對每條 `(u,v) ∈ E`
  - 若 `f(u,v) < c(u,v)`，放一條正向殘餘邊 `u→v`，殘餘容量 `c(u,v) − f(u,v)`；
  - 若 `f(u,v) > 0`，放一條反向殘餘邊 `v→u`，殘餘容量 `f(u,v)`。
- **增廣路（augmenting path）**：`G_f` 上一條 `s→t` 的簡單路徑。路徑上最小的殘餘容量叫瓶頸（bottleneck）。沿路徑推瓶頸量：正向邊加流，反向邊減流。

推完之後仍然是合法的流：正向邊加的量不超過剩餘空間，反向邊減的量不超過已有的流；路徑上每個中間點恰好一進一出，增減抵銷，守恆不壞。流值增加瓶頸量，因為路徑第一條邊從 `s` 出發。

## 第一個實作：Ford–Fulkerson

```python
import networkx as nx


def reachable(res, s):
    """殘餘圖上從 s 出發（只走殘餘容量 > 0 的邊）的 DFS，回傳 parent 字典。"""
    parent, stack = {s: None}, [s]
    while stack:
        u = stack.pop()
        for v, r in res[u].items():
            if r > 0 and v not in parent:
                parent[v] = u
                stack.append(v)
    return parent


def ford_fulkerson(cap, s, t):
    res = {u: dict(nb) for u, nb in cap.items()}      # 殘餘容量 res[u][v]
    for u in cap:
        for v in cap[u]:
            res[v].setdefault(u, 0)                    # 反向邊，初始 0
    value = 0
    while t in (parent := reachable(res, s)):
        path = [t]
        while parent[path[-1]] is not None:
            path.append(parent[path[-1]])
        path.reverse()
        b = min(res[u][v] for u, v in zip(path, path[1:]))   # bottleneck
        for u, v in zip(path, path[1:]):
            res[u][v] -= b
            res[v][u] += b
        value += b
        print(f"  augment {'->'.join(path):<14} by {b}, value = {value}")
    return value, set(parent)      # 最後一次搜尋走得到的點 = 最小割的 S 側


cap = {
    "s": {"a": 10, "b": 10},
    "a": {"b": 2, "c": 4, "d": 8},
    "b": {"d": 9},
    "c": {"t": 10},
    "d": {"c": 6, "t": 10},
    "t": {},
}
value, S = ford_fulkerson(cap, "s", "t")
cut_edges = sorted((u, v) for u in S for v in cap[u] if v not in S)
print("max flow =", value, " S side =", sorted(S))
print("cut edges =", cut_edges, "capacity =", sum(cap[u][v] for u, v in cut_edges))

G = nx.DiGraph([(u, v, {"capacity": c}) for u in cap for v, c in cap[u].items()])
cut_value, (S2, T2) = nx.minimum_cut(G, "s", "t")
print("networkx: max flow =", nx.maximum_flow_value(G, "s", "t"),
      " min cut =", cut_value, " S side =", sorted(S2))
```

輸出：

```text
  augment s->b->d->t     by 9, value = 9
  augment s->a->d->t     by 1, value = 10
  augment s->a->c->t     by 4, value = 14
  augment s->a->d->c->t  by 5, value = 19
max flow = 19  S side = ['b', 's']
cut edges = [('b', 'd'), ('s', 'a')] capacity = 19
networkx: max flow = 19  min cut = 19  S side = ['b', 's']
```

幾個設計決策：

- **殘餘圖用 `res[u][v]` 一張表存**，不分開存 `f` 和 `c`。正向殘餘是 `c − f`，反向殘餘是 `f`，推流時一減一加，兩個量自動同步。這是幾乎所有實作的寫法，也是練習 D 的函式庫會沿用的表示。
- **最小割是附贈的**：演算法停下時，從 `s` 在殘餘圖上走得到的點集 `S` 就是最小割的一側。下面的證明會說明為什麼。
- **割邊 `b→d`（9）+ `s→a`（10）= 19**：注意 `a→b` 是從 `T` 指回 `S` 的邊，不計入割容量。

## 失敗示範：沒有反向邊會怎樣

最小的反例只有四個點：`s→a`、`s→b`、`a→b`、`a→t`、`b→t`，容量全是 1。最大流是 2（`s→a→t` 與 `s→b→t`）。如果第一步走了 `s→a→b→t`，沒有反向邊的貪婪法就卡住了。下面這個腳本同時準備了下一節要用的「對手選路」實驗：

```python
from collections import deque


def residual(cap):
    res = {u: dict(nbrs) for u, nbrs in cap.items()}
    for u in cap:
        for v in cap[u]:
            res[v].setdefault(u, 0)
    return res


def push(res, path, with_reverse=True):
    b = min(res[u][v] for u, v in zip(path, path[1:]))
    for u, v in zip(path, path[1:]):
        res[u][v] -= b
        if with_reverse:
            res[v][u] += b
    return b


def bfs_path(res, s, t):
    parent, q = {s: None}, deque([s])
    while q:
        u = q.popleft()
        for v, r in res[u].items():
            if r > 0 and v not in parent:
                parent[v] = u
                q.append(v)
    if t not in parent:
        return None
    path = [t]
    while parent[path[-1]] is not None:
        path.append(parent[path[-1]])
    return path[::-1]


def longest_path(res, s, t):
    """對手策略：在所有正殘餘的簡單路徑裡挑最長的（必然穿過中間橫槓）。"""
    best, stack = None, [(s, [s])]
    while stack:
        u, path = stack.pop()
        if u == t:
            best = path if best is None or len(path) > len(best) else best
            continue
        stack.extend((v, path + [v]) for v, r in res[u].items() if r > 0 and v not in path)
    return best


def run(cap, chooser, with_reverse=True, first=None):
    res, value, rounds = residual(cap), 0, 0
    if first:                                   # 指定第一條路徑
        value, rounds = push(res, first, with_reverse), 1
    while (p := chooser(res, "s", "t")) is not None:
        value += push(res, p, with_reverse)
        rounds += 1
    return value, rounds


# (1) 沒有反向邊：第一步走 s->a->b->t 就卡住
small = {"s": {"a": 1, "b": 1}, "a": {"b": 1, "t": 1}, "b": {"t": 1}, "t": {}}
print("without reverse edges:", run(small, bfs_path, False, list("sabt")))
print("with reverse edges   :", run(small, bfs_path, True, list("sabt")))

# (2) 路徑選法：中間橫槓網路，對手 vs BFS
print(f"{'C':>6} | {'adversarial (value, rounds)':>28} | {'BFS (value, rounds)':>20}")
for C in (10, 100, 1000):
    cap = {"s": {"a": C, "b": C}, "a": {"b": 1, "t": C}, "b": {"t": C}, "t": {}}
    print(f"{C:>6} | {str(run(cap, longest_path)):>28} | {str(run(cap, bfs_path)):>20}")
```

```text
without reverse edges: (1, 1)
with reverse edges   : (2, 2)
     C |  adversarial (value, rounds) |  BFS (value, rounds)
    10 |                     (20, 20) |              (20, 2)
   100 |                   (200, 200) |             (200, 2)
  1000 |                 (2000, 2000) |            (2000, 2)
```

`run` 回傳 `(流值, 增廣次數)`。前兩行是這一節的重點：同樣先走 `s→a→b→t`，沒有反向邊時流值停在 1；有反向邊時，BFS 找到的第二條路徑是 `s→b→a→t`，走的是反向邊 `b→a`。後面的表格留到「增廣路怎麼選」一節再看。

第二條路徑 `s→b→a→t` 走了反向邊 `b→a`：把原本 `a→b` 上的 1 單位退掉，`a` 的那 1 單位改走 `a→t`，`b` 收到的 1 單位改由 `s→b` 供應。**反向邊不是物理上的管子，是「重新安排」的記帳方式。**

## Max-flow min-cut 定理：完整證明

### 引理 1（流穿過任何割的淨量等於流值）

對任意流 `f` 與任意割 `(S, T)`：

```text
|f| = f(S,T) − f(T,S)
```

其中 `f(S,T) = Σ_{u∈S, v∈T} f(u,v)`。

**證明**：對 `S` 中每個點 `v` 寫下「流出 − 流入」：

- `v = s` 時這個量是 `|f|`（定義）；
- `v ∈ S \ {s}` 時，因為 `t ∉ S`，`v` 是中間點，守恆給出 0。

把這 `|S|` 條等式加起來，左邊是 `|f|`。右邊把每條邊 `(u,w)` 的流按端點分類：

- 兩端都在 `S`：在 `u` 的式子算一次 `+f(u,w)`，在 `w` 的式子算一次 `−f(u,w)`，抵銷；
- `u ∈ S, w ∈ T`：只在 `u` 的式子出現，貢獻 `+f(u,w)`；
- `u ∈ T, w ∈ S`：只在 `w` 的式子出現，貢獻 `−f(u,w)`；
- 兩端都在 `T`：不出現。

所以總和是 `f(S,T) − f(T,S)`。∎

### 推論（弱對偶）

`|f| = f(S,T) − f(T,S) ≤ f(S,T) ≤ c(S,T)`。任何流 ≤ 任何割。

這一行已經很有用：如果你手上有一個流和一個割，數值相等，那兩者**同時**是最佳的——不需要知道演算法，就能驗證答案。NetworkX 回傳的 `minimum_cut` 就是這種「證書」。

### 定理（Ford–Fulkerson 1956）

對流網路中的流 `f`，以下三者等價：

1. `f` 是最大流；
2. 殘餘圖 `G_f` 中沒有增廣路；
3. 存在割 `(S, T)` 使 `|f| = c(S, T)`。

**證明**：

- **(1) ⇒ (2)**：反證。若有增廣路，沿它推瓶頸量 `b > 0`，得到流值 `|f| + b` 的合法流（上一節已說明推完仍合法），與 `f` 最大矛盾。
- **(2) ⇒ (3)**：令 `S` = 在 `G_f` 中從 `s` 走得到的點，`T = V \ S`。因為沒有增廣路，`t ∉ S`，所以 `(S,T)` 是割。看跨越的邊：
  - 若 `(u,v) ∈ E`、`u ∈ S`、`v ∈ T`：必有 `f(u,v) = c(u,v)`。否則正向殘餘邊 `u→v` 存在，`v` 就走得到，矛盾。
  - 若 `(u,v) ∈ E`、`u ∈ T`、`v ∈ S`：必有 `f(u,v) = 0`。否則反向殘餘邊 `v→u` 存在，`u` 就走得到，矛盾。

  代入引理 1：`|f| = f(S,T) − f(T,S) = c(S,T) − 0`。
- **(3) ⇒ (1)**：由弱對偶，任何流 `f'` 滿足 `|f'| ≤ c(S,T) = |f|`。∎

**最大流存在嗎？** 定理假設了 `f` 是最大流，所以要另外說明最大值取得到。兩種論證都行：(a) 可行流構成 `R^E` 中的有界閉集（一堆線性不等式），流值是連續函數，緊緻集合上的連續函數取得最大值；(b) 構造性地，下面會證 Edmonds–Karp 在任何實數容量下都會在有限步停止，停下時 (2) 成立，(2) ⇒ (1)。

**整數性定理**：若所有容量是整數，Ford–Fulkerson 每次推的瓶頸都是整數（歸納：殘餘容量一直是整數），所以存在**整數值**的最大流。這條看似不起眼，卻是 Menger、König（Ch 22）、各種「建模成 flow」能成立的關鍵：問題要的是「選或不選」，整數性保證 LP 鬆弛不會給你 0.5 條路徑。Ch 26 會用全單模矩陣從另一個角度解釋同一件事。

## 增廣路怎麼選，差很多

整數容量下 Ford–Fulkerson 會停，因為每次至少推 1、流值不超過 `C = Σ c(s,v)`。但次數可能和 `C` 成正比——這是**偽多項式**，不是多項式。經典的壞例子：

```text
              C
         s ------> a
         |         | \
        C|        1|  \C
         v         v   v
         b ------------> t
               C
   （s→a、s→b、a→t、b→t 容量 C；中間橫槓 a→b 容量 1）
```

讓「對手」每次挑最長的增廣路（`longest_path`，一定經過中間那條容量 1 的邊，時而正向時而反向），對照 BFS。這就是上一節腳本的第 (2) 部分，輸出：

```text
     C |  adversarial (value, rounds) |  BFS (value, rounds)
    10 |                     (20, 20) |              (20, 2)
   100 |                   (200, 200) |             (200, 2)
  1000 |                 (2000, 2000) |            (2000, 2)
```

對手版本的輪數是 `2C`，每輪只推 1；BFS 永遠兩輪。容量用 32 位元整數存，`C` 可以是 `2³¹`，對手版本就要跑二十億輪。**演算法的正確性不依賴路徑選法，執行時間完全依賴。**

## 無理容量：Ford–Fulkerson 可能永遠不停

整數容量至少保證會停。容量是無理數時更糟：存在網路與一組（合法的）增廣路選擇，使 Ford–Fulkerson 永不停止，而且流值收斂到的極限**嚴格小於**最大流——不只是慢，是收斂到錯的答案。

歷史：Ford 與 Fulkerson 自己在 1962 年的書《Flows in Networks》中給過這類例子，網路比較大（文獻記載為 10 點 48 邊；我沒有翻到原書，這個數字引自後人的整理）。Uri Zwick 在 1995 年的論文中構造了 6 點、8 條邊的網路，並證明這是會發生不終止的最小網路；論文裡也給了兩個 6 點 9 邊、更好理解的版本。下面用的是 Wikipedia「Ford–Fulkerson algorithm」條目裡的 6 點 9 邊例子：

```text
  特殊邊：e1 = v2→v1 容量 1；e2 = v4→v3 容量 r；e3 = v2→v3 容量 1
  其餘邊：s→v1, s→v2, s→v4, v1→t, v3→t, v4→t，容量 M = 2
  r = (√5 − 1) / 2 ≈ 0.618，滿足 r² = 1 − r
```

`r² = 1 − r` 是整個構造的引擎：它讓 `r^n − r^(n+1) = r^(n+2)`。增廣順序是先走 `s→v2→v3→t`，之後無限重複 `p1, p2, p1, p3`：

```text
p1 = s → v4 → v3 → v2 → v1 → t   （v3→v2 是 e3 的反向邊）
p2 = s → v2 → v3 → v4 → t        （v3→v4 是 e2 的反向邊）
p3 = s → v1 → v2 → v3 → t        （v1→v2 是 e1 的反向邊）
```

每跑完一輪四步，三條特殊邊的殘餘容量從 `(r^n, r^(n+1), 0)` 變成 `(r^(n+2), r^(n+3), 0)`，流值增加 `2r^(n+1) + 2r^(n+2) = 2r^n`。從初始的 1 出發，流值是 `1 + 2(r + r² + r³ + …)`，收斂到 `1 + 2r/(1−r) = 3 + 2r ≈ 4.236`。但 `s→v1→t`、`s→v2→v3→t`、`s→v4→t` 三條路就能送 `M + 1 + M = 5`。

浮點數會在 `r^n` 小到 `1e-16` 時把殘餘容量捨入成 0，讓演算法「意外終止」，掩蓋了真正的行為。所以下面用 `Q(√5)` 的精確算術：每個數存成 `a + b·r`（`a, b` 是有理數），乘法用 `r² = 1 − r` 化簡，比大小時把 `a + b·r` 寫成 `(x + y√5)/2` 再平方比較——全程不碰浮點，只有印出時才轉成 60 位十進位。

```python
from collections import deque
from decimal import Decimal, getcontext
from fractions import Fraction

getcontext().prec = 60      # 只用在印出；運算本身全部精確


class QR:
    """精確表示 a + b*r，r = (sqrt(5) - 1) / 2，滿足 r*r = 1 - r。"""

    def __init__(self, a, b=0):
        self.a, self.b = Fraction(a), Fraction(b)

    def __add__(self, o):
        return QR(self.a + o.a, self.b + o.b)

    def __sub__(self, o):
        return QR(self.a - o.a, self.b - o.b)

    def __mul__(self, o):   # (a+br)(c+dr) = (ac+bd) + (ad+bc-bd)r
        return QR(self.a * o.a + self.b * o.b, self.a * o.b + self.b * o.a - self.b * o.b)

    def sign(self):         # a + b*r = (x + y*sqrt5)/2，x = 2a - b，y = b
        x, y = 2 * self.a - self.b, self.b
        if x >= 0 and y >= 0:
            return int(x != 0 or y != 0)
        if x <= 0 and y <= 0:
            return -1
        return (1 if x * x > 5 * y * y else -1) if x > 0 else (1 if 5 * y * y > x * x else -1)

    def __lt__(self, o):
        return (self - o).sign() < 0

    def __eq__(self, o):
        return (self - o).sign() == 0

    def __float__(self):    # 直接 float(a) + float(b)*r 在 a、b 很大時會嚴重相消
        r = (Decimal(5).sqrt() - 1) / 2
        dec = lambda q: Decimal(q.numerator) / Decimal(q.denominator)
        return float(dec(self.a) + dec(self.b) * r)


ZERO, ONE, R, M = QR(0), QR(1), QR(0, 1), QR(2)
cap = {
    "s": {"v1": M, "v2": M, "v4": M},
    "v1": {"t": M}, "v3": {"t": M},
    "v4": {"t": M, "v3": R},            # e2 = (v4,v3)，容量 r
    "v2": {"v1": ONE, "v3": ONE},       # e1 = (v2,v1)，e3 = (v2,v3)
    "t": {},
}


def residual():
    res = {u: dict(nb) for u, nb in cap.items()}
    for u in cap:
        for v in cap[u]:
            res[v].setdefault(u, ZERO)
    return res


def augment(res, path):
    b = min(res[u][v] for u, v in zip(path, path[1:]))   # 精確比較
    assert ZERO < b, f"path {path} is not augmenting"
    for u, v in zip(path, path[1:]):
        res[u][v], res[v][u] = res[u][v] - b, res[v][u] + b
    return b


res = residual()
p1 = ["s", "v4", "v3", "v2", "v1", "t"]
p2 = ["s", "v2", "v3", "v4", "t"]
p3 = ["s", "v1", "v2", "v3", "t"]
value = augment(res, ["s", "v2", "v3", "t"])
for rnd in range(1, 41):
    for p in (p1, p2, p1, p3):
        value = value + augment(res, p)
    e1, e2, e3 = res["v2"]["v1"], res["v4"]["v3"], res["v2"]["v3"]
    assert e3 == ZERO and e2 == e1 * R          # 不變式：(r^n, r^(n+1), 0)
    assert res["s"]["v2"] == e1                 # s->v2 的殘餘也恰好是 r^n
    if rnd <= 3 or rnd % 10 == 0:
        print(f"round {rnd:>2}: value = {float(value):.12f}  "
              f"residual(e1,e2,e3) = ({float(e1):.3e}, {float(e2):.3e}, 0)")
print("limit 3 + 2r =", float(QR(3, 2)))
print("value after 40 rounds, exact =", value.a, "+", value.b, "* r")


def edmonds_karp(res, s, t):
    total = ZERO
    while True:
        parent, q = {s: None}, deque([s])
        while q:
            u = q.popleft()
            for v, r in res[u].items():
                if ZERO < r and v not in parent:
                    parent[v] = u
                    q.append(v)
        if t not in parent:
            return total
        path = [t]
        while parent[path[-1]] is not None:
            path.append(parent[path[-1]])
        total = total + augment(res, path[::-1])


mf = edmonds_karp(residual(), "s", "t")
print("Edmonds-Karp exact max flow =", mf.a, "+", mf.b, "* r")
```

```text
round  1: value = 3.000000000000  residual(e1,e2,e3) = (3.820e-01, 2.361e-01, 0)
round  2: value = 3.763932022500  residual(e1,e2,e3) = (1.459e-01, 9.017e-02, 0)
round  3: value = 4.055728090001  residual(e1,e2,e3) = (5.573e-02, 3.444e-02, 0)
round 10: value = 4.235854050879  residual(e1,e2,e3) = (6.611e-05, 4.086e-05, 0)
round 20: value = 4.236067963358  residual(e1,e2,e3) = (4.370e-09, 2.701e-09, 0)
round 30: value = 4.236067977499  residual(e1,e2,e3) = (2.889e-13, 1.785e-13, 0)
round 40: value = 4.236067977500  residual(e1,e2,e3) = (1.910e-17, 1.180e-17, 0)
limit 3 + 2r = 4.23606797749979
value after 40 rounds, exact = 17888788647582931 + -28944668049352440 * r
Edmonds-Karp exact max flow = 5 + 0 * r
```

讀這份輸出的三個重點：

1. **160 次增廣全部合法**（`augment` 裡的 `assert` 沒有觸發），每一輪都驗證了 `(r^n, r^(n+1), 0)` 的不變式。程式只能跑有限輪，「永不停止」是由不變式的歸納推出的：只要殘餘容量是這個形狀，下一輪的四條路徑就都還是增廣路。還要顧到那六條容量 `M` 的邊：我們實際印過各邊的流量，`s→v2` 是最緊的一條，它的殘餘容量**恰好**也是 `r^n`（第二個 `assert`），永遠是正的、只在極限才變成 0；其他五條的流量極限不超過 `1 + r ≈ 1.618 < 2`。所以 `M = 2` 剛好夠用，`M` 再小構造就會破（動手練習第 2 題）。
2. **精確係數長得像 Fibonacci 數**：`r^n = (−1)^n (F(n−1) − F(n)·r)`，所以 160 次增廣後 `a`、`b` 已經是 17 位數。如果用 `float(a) + float(b)*r`，兩個 `1e16` 量級的數相減，有效位數全部消失——這就是 `__float__` 改用 60 位十進位的原因。
3. **同一張網路，Edmonds–Karp 精確地停在 5**。下一節的證明會解釋：EK 的步數上界只跟 `V`、`E` 有關，跟容量是不是無理數無關。

## Edmonds–Karp：選最短的增廣路

Edmonds 與 Karp（1972，JACM）提出的修改只有一句話：**每次用 BFS 找邊數最少的增廣路**。（Dinitz 在 1970 年獨立發表了更強的版本，Ch 21 的主角；EK 的「最短路」想法在兩者中都有。）

先把它寫完整，並在 300 張隨機網路上同時對拍 NetworkX 與「暴力窮舉所有割」：

```python
import itertools
import random
from collections import deque

import networkx as nx


def edmonds_karp(cap, s, t):
    """回傳 (最大流值, 每條原始邊的流量, 增廣次數)；順便檢查距離單調引理。"""
    res = {u: dict(nb) for u, nb in cap.items()}
    for u in cap:
        for v in cap[u]:
            res[v].setdefault(u, 0)
    value, rounds, prev = 0, 0, {}
    while True:
        dist, parent, q = {s: 0}, {s: None}, deque([s])
        while q:
            u = q.popleft()
            for v, r in res[u].items():
                if r > 0 and v not in dist:
                    dist[v], parent[v] = dist[u] + 1, u
                    q.append(v)
        # 引理 2：殘餘圖中 s 到每個點的 BFS 距離不會變小
        assert all(dist.get(v, float("inf")) >= d for v, d in prev.items())
        prev = dist
        if t not in dist:
            break
        path = [t]
        while parent[path[-1]] is not None:
            path.append(parent[path[-1]])
        path.reverse()
        b = min(res[u][v] for u, v in zip(path, path[1:]))
        for u, v in zip(path, path[1:]):
            res[u][v] -= b
            res[v][u] += b
        value, rounds = value + b, rounds + 1
    # 有反平行邊 u→v、v→u 時，res[u][v] 混了兩者，c - res 是「淨流量」，可能為負；
    # 取 max(0, ·) 把淨流量放到正確方向的那條邊上
    flow = {u: {v: max(0, c - res[u][v]) for v, c in cap[u].items()} for u in cap}
    return value, flow, rounds


def brute_min_cut(cap, s, t):
    """窮舉所有含 s、不含 t 的 S，回傳最小割容量（2^(n-2) 個割，只適用小圖）。"""
    others = [v for v in cap if v not in (s, t)]
    return min(sum(w for u in S for v, w in cap[u].items() if v not in S)
               for k in range(len(others) + 1)
               for S in ({s, *extra} for extra in itertools.combinations(others, k)))


rng = random.Random(20)
worst = 0.0
for trial in range(300):
    n = rng.randint(2, 9)
    cap = {u: {v: rng.randint(1, 20) for v in range(n) if v != u and rng.random() < 0.35}
           for u in range(n)}
    value, flow, rounds = edmonds_karp(cap, 0, n - 1)
    for u in cap:                                   # 容量限制
        assert all(0 <= flow[u][v] <= cap[u][v] for v in cap[u])
    for x in range(1, n - 1):                       # 流量守恆
        assert sum(flow[u].get(x, 0) for u in cap) == sum(flow[x].values())
    G = nx.DiGraph()
    G.add_nodes_from(range(n))
    G.add_edges_from((u, v, {"capacity": c}) for u in cap for v, c in cap[u].items())
    assert value == nx.maximum_flow_value(G, 0, n - 1) == brute_min_cut(cap, 0, n - 1)
    if G.number_of_edges():
        worst = max(worst, rounds / (n * G.number_of_edges()))
print("300 random networks: EK == networkx == brute-force min cut")
print(f"max rounds / (V*E) observed = {worst:.3f}")
```

```text
300 random networks: EK == networkx == brute-force min cut
max rounds / (V*E) observed = 0.500
```

注意這份程式**同時檢查了三件事**：流的合法性（容量、守恆）、和 NetworkX 一致、和暴力窮舉 `2^(n−2)` 個割的最小值一致。第三項是在小圖上直接驗證 max-flow min-cut 定理本身，不依賴任何演算法。迴圈裡那行 `assert all(...)` 每一輪都檢查下面的「距離單調」引理，300 張圖上沒有一次違反。

`0.500` 這個最大比值來自 `n = 2`、只有一條邊 `0→1` 的退化網路（1 輪 / (2·1)）；有意義大小的隨機圖離 `VE` 的上界遠得很。上界是最壞情況保證，不是典型值。

### 引理 2（距離單調）

令 `d_f(v)` 為殘餘圖 `G_f` 中 `s` 到 `v` 的最短路邊數（走不到記為 ∞）。EK 每做一次增廣（`f → f'`），對所有 `v` 有 `d_{f'}(v) ≥ d_f(v)`。

**證明**：反證。假設有點的距離變小，取其中 `d_{f'}(v)` 最小的那個 `v`（`v ≠ s`，因為 `d(s) = 0` 恆成立）。令 `u` 是 `G_{f'}` 中某條 `s→v` 最短路上 `v` 的前一個點，則 `d_{f'}(u) = d_{f'}(v) − 1`；由 `v` 的極小性，`u` 的距離沒有變小：`d_f(u) ≤ d_{f'}(u)`。

- 若 `u→v` 在 `G_f` 裡也存在：`d_f(v) ≤ d_f(u) + 1 ≤ d_{f'}(u) + 1 = d_{f'}(v)`，與 `d_{f'}(v) < d_f(v)` 矛盾。
- 若 `u→v` 不在 `G_f`、但在 `G_{f'}`：這條殘餘邊是這次增廣「新長出來的」，唯一的可能是增廣路經過了 `v→u`（推流到 `v→u` 會產生反向的 `u→v`）。EK 的增廣路是 `G_f` 的最短路，所以 `d_f(u) = d_f(v) + 1`。於是 `d_f(v) = d_f(u) − 1 ≤ d_{f'}(u) − 1 = d_{f'}(v) − 2 < d_{f'}(v)`，又矛盾。∎

### 引理 3（每條邊當瓶頸的次數有限）

稱殘餘邊 `u→v` 在某次增廣中是**關鍵的**（critical），如果它在增廣路上且殘餘容量等於瓶頸——推完後它從殘餘圖消失。則每條 `u→v` 最多當 `|V|/2` 次關鍵邊。

**證明**：`u→v` 第一次當關鍵邊時（流為 `f`），它在最短路上，`d_f(v) = d_f(u) + 1`。推完後它消失。它要再出現，之後某次增廣（流為 `f'`）必須經過 `v→u`，此時 `d_{f'}(u) = d_{f'}(v) + 1`。由引理 2，`d_{f'}(v) ≥ d_f(v)`，所以

```text
d_{f'}(u) = d_{f'}(v) + 1 ≥ d_f(v) + 1 = d_f(u) + 2
```

每兩次「當關鍵邊」之間，`d(u)` 至少增加 2。`u` 在增廣路上而且不是 `t`，所以 `d(u) ≤ |V| − 2`；從 `≥ 0` 開始每次加 2，最多 `(|V| − 2)/2 + 1 = |V|/2` 次。∎

### 定理（Edmonds–Karp 是 O(V E²)）

每次增廣至少有一條關鍵邊。殘餘圖裡可能出現的有向邊（原邊加反向邊）最多 `2|E|` 條，每條最多當 `|V|/2` 次關鍵邊，所以增廣總次數 ≤ `2|E| · |V|/2 = |V||E|`。每次 BFS 加推流是 `O(E)`（假設每個點至少有一條邊，`V = O(E)`），總時間 `O(V E²)`。∎

注意這個證明**沒有用到容量是整數**。這就是為什麼上一節的無理容量網路上，EK 精確地停在 5：EK 的終止性來自距離的組合結構，跟數值無關。演算法分析裡，這種「強多項式」（strongly polynomial，步數只依賴 `V`、`E`）和「偽多項式」（依賴數值大小）的區分，在 Ch 24 的最小費用流還會再出現。

## 用 flow 證明 Menger 定理

> 回看 [Ch 12 — Menger 定理](./12-menger-theorem.md)：那裡用圖論的歸納法證明了 Menger。這裡給第二個證明，展示 max-flow min-cut 的威力。

**邊版本**：在有向圖中，`s` 到 `t` 的邊不相交路徑最大數目 = 最少要刪幾條邊才能讓 `s` 走不到 `t`。

**證明**：每條邊容量設 1。

- 若有 `k` 條邊不相交路徑，每條送 1 單位，得到值 `k` 的流；所以 max-flow ≥ 路徑數。
- 反方向：由整數性定理，存在整數最大流 `f`，每條邊流量是 0 或 1。從 `s` 出發沿著流量為 1 的邊走，用過的邊就把流量歸零。守恆保證每到一個中間點都還有一條流量 1 的出邊可走；如果走回已經到過的點，就形成一個環，把環上的流量歸零（流值不變）再繼續。最後必然走到 `t`，得到一條路徑，流值減 1。重複 `|f|` 次得到 `|f|` 條邊不相交路徑（這個過程叫 flow decomposition）。
- 最小割的容量就是割邊數，而刪掉一個割的所有 S→T 邊，`s` 就到不了 `t`；反過來，任何讓 `s` 到不了 `t` 的刪邊集合 `F`，令 `S` = 刪後 `s` 走得到的點，則所有 S→T 邊都在 `F` 裡，`|F| ≥ c(S,T)`。

三者連起來：路徑最大數 = max-flow = min-cut = 最小刪邊數。∎ 無向圖把每條邊換成兩條相反方向、容量 1 的弧；若某條邊的兩個方向都有流，兩者相消不影響流值，所以路徑仍是無向邊不相交的。

**點版本**：`s`、`t` 不相鄰時，內部點不相交的 `s-t` 路徑最大數 = 最少要刪幾個點（不含 `s`、`t`）才能分開 `s`、`t`。

**證明**：**拆點**（vertex splitting）。每個點 `v` 換成 `v_in → v_out`，容量 1（`s`、`t` 不限）；原來的邊 `u→w` 變成 `u_out → w_in`，容量 ∞。流過一個點就必須經過它的那條容量 1 的內部邊，所以「點不相交」變成「內部邊不相交」，套用邊版本的論證。最小割的容量有限，不可能包含容量 ∞ 的原邊，所以只由內部邊組成，每條對應一個被刪的點。`s`、`t` 不相鄰的條件保證不會有 `s_out → t_in` 這種不經過任何內部邊、容量 ∞ 的路徑。∎

```python
import random
from collections import deque

import networkx as nx
from networkx.algorithms.connectivity import local_edge_connectivity, local_node_connectivity


def unit_max_flow(cap, s, t):
    """每次 BFS 增廣 1 單位的 Edmonds–Karp（本節的網路每條增廣路瓶頸都 ≥ 1）。"""
    for u in list(cap):
        for v in list(cap[u]):
            cap.setdefault(v, {}).setdefault(u, 0)
    value = 0
    while True:
        parent, q = {s: None}, deque([s])
        while q and t not in parent:
            u = q.popleft()
            for v, r in cap[u].items():
                if r > 0 and v not in parent:
                    parent[v] = u
                    q.append(v)
        if t not in parent:
            return value
        v = t
        while parent[v] is not None:
            u = parent[v]
            cap[u][v], cap[v][u], v = cap[u][v] - 1, cap[v][u] + 1, u
        value += 1


def edge_disjoint(adj, s, t):     # 無向邊 {u,v} → u→v、v→u 各容量 1
    return unit_max_flow({u: {v: 1 for v in adj[u]} for u in adj}, s, t)


def vertex_disjoint(adj, s, t):   # 拆點：(v,in) -1-> (v,out)；原邊容量 big = n 當作無限大
    big, cap = len(adj), {}
    for v in adj:
        cap.setdefault((v, "in"), {})[(v, "out")] = big if v in (s, t) else 1
        for w in adj[v]:
            cap.setdefault((v, "out"), {})[(w, "in")] = big
    return unit_max_flow(cap, (s, "out"), (t, "in"))


P = nx.petersen_graph()
adj = {u: set(P[u]) for u in P}
print("Petersen 0-7: edge =", edge_disjoint(adj, 0, 7), " vertex =", vertex_disjoint(adj, 0, 7),
      "| networkx:", local_edge_connectivity(P, 0, 7), local_node_connectivity(P, 0, 7))

rng, checked = random.Random(12), 0
for trial in range(200):
    n = rng.randint(4, 12)
    G = nx.gnp_random_graph(n, 0.4, seed=rng.randrange(10**9))
    adj = {u: set(G[u]) for u in G}
    s, t = rng.sample(range(n), 2)
    assert edge_disjoint(adj, s, t) == local_edge_connectivity(G, s, t)
    if not G.has_edge(s, t):          # 點版本要求 s、t 不相鄰
        assert vertex_disjoint(adj, s, t) == local_node_connectivity(G, s, t)
        checked += 1
print(f"200 random graphs OK (vertex version checked on {checked} non-adjacent pairs)")

# 蝴蝶結：兩個三角形共用中心點 c
bow = {"s": {"a", "c"}, "a": {"s", "c"}, "c": {"s", "a", "b", "t"}, "b": {"c", "t"}, "t": {"b", "c"}}
print("bowtie: edge =", edge_disjoint(bow, "s", "t"), " vertex =", vertex_disjoint(bow, "s", "t"))
```

```text
Petersen 0-7: edge = 3  vertex = 3 | networkx: 3 3
200 random graphs OK (vertex version checked on 115 non-adjacent pairs)
bowtie: edge = 2  vertex = 1
```

蝴蝶結（兩個三角形共用中心 `c`）是忘記拆點的反例：邊不相交的路有兩條（`s→a→c→t`、`s→c→b→t`），但它們都經過 `c`，點不相交的只有一條。拿攻擊圖來說，「最少修補幾台主機」是點割，算成邊割會給出錯誤的（過大的）防守成本。`unit_max_flow` 每次只推 1 單位而不算瓶頸：所有容量都是正整數，殘餘容量也一直是整數，所以任何增廣路的瓶頸都 ≥ 1，推 1 永遠合法（只是在瓶頸大於 1 時多跑幾輪）。

## 對比與取捨

表中 `F` 是最大流的值。

| 方法 | 每輪找路 | 增廣次數上界 | 總時間 | 備註 |
| --- | --- | --- | --- | --- |
| Ford–Fulkerson（任意路） | `O(E)` | `F`（整數容量） | `O(E·F)` | 偽多項式；無理容量可能不停 |
| 最大瓶頸路（fattest path） | `O(E log V)` | `O(E log F)`（整數容量） | `O(E² log V log F)` | 也出自 Edmonds–Karp 1972；依賴容量大小 |
| Edmonds–Karp（BFS 最短路） | `O(E)` | `V·E` | `O(V E²)` | 強多項式，教學標準 |
| Dinic | 一次 BFS + blocking flow | `V − 1` 個 phase | `O(V² E)` | Ch 21 |
| Push-relabel | 不找路 | — | `O(V³)`（FIFO） | Ch 21 |

為什麼教 EK 而不是直接教 Dinic？EK 的證明（距離單調 + 關鍵邊計數）是 Dinic 證明的直接前身；先懂 EK，Dinic 的「每個 phase 距離嚴格增加」只是同一個引理的加強版。實務上 EK 很少是最快的選擇，Ch 21 會實測。

## 踩雷集錦

1. **以為反向邊是「圖本身的邊」**：反向邊只存在於殘餘圖，代表「可以撤回多少」。最終輸出的流要從殘餘容量反推，而且要對每條**原始**邊算。還有一個陷阱：圖裡同時有 `u→v`、`v→u` 時，`res[u][v]` 混了兩條邊的資訊，`c − res` 是淨流量、可能是負的。本章 EK 程式第一版直接寫 `c - res[u][v]`，在隨機圖上立刻撞到 `assert 0 <= flow` 失敗；改成 `max(0, c − res)`（把淨流量放到正確方向的那條邊）才通過。
2. **以為 Ford–Fulkerson 一定會停**：整數容量才保證停；無理容量下可能不停，而且極限是錯的（本章實測：極限 4.236 對上最大流 5）。浮點數容量時程式「會停」，但那是捨入誤差造成的偶然，不是正確性保證。
3. **割容量把反方向的邊也算進去**：`c(S,T)` 只算 `S→T` 方向的邊。第一個例子裡 `a→b` 從 `T` 指向 `S`，不計入。弱對偶裡 `− f(T,S)` 那一項就是在處理這些邊。
4. **點不相交和邊不相交混用**：需要點割時忘了拆點，答案只會偏大（蝴蝶結：2 vs 1）。還有 `s`、`t` 相鄰時點版本的 Menger 不成立——刪掉多少內部點都分不開相鄰的兩點，NetworkX 的 `local_node_connectivity` 對相鄰點也不是在回答 Menger 的問題，對拍時要跳過。
5. **把 `O(VE²)` 當成實際跑的時間**：上界是最壞情況；本章 300 張隨機圖的增廣次數離 `VE` 很遠。反過來，也有特製的網路能逼近上界（Zadeh 1972 同一期 JACM 的論文就在研究 EK 的最壞行為）。比較演算法要實測，Ch 21 做這件事。

## 進階：再往深一層

- **最小割不唯一**：`S` = 殘餘圖中 `s` 走得到的點給出「最靠近 `s`」的最小割；從 `t` 反向走得到的點的補集給出「最靠近 `t`」的最小割。所有最小割形成一個格（lattice），Picard–Queyranne（1980）證明它們可以用殘餘圖的 SCC 縮點表示。攻擊圖裡這代表「有不同的最少修補方案」。
- **全對最小割**：無向圖所有點對的最小割只要 `V − 1` 次最大流就能算出，結果存成一棵 Gomory–Hu 樹。NetworkX 有 `nx.gomory_hu_tree`。
- **近年的突破**：2022 年 Chen、Kyng、Liu、Peng、Probst Gutenberg、Sachdeva 發表了 almost-linear time（`m^(1+o(1))`）的最大流演算法（FOCS 2022 最佳論文）。它建立在內點法和動態資料結構上，目前是理論結果，實務上還是 Ch 21 的演算法在跑。
- **常見面試題**：「為什麼最大流演算法停下時找得到最小割？」——答案就是定理 (2) ⇒ (3) 的證明：殘餘圖可達集合的邊界，正向全飽和、反向全為零。

## 動手練習

1. 把第一個例子的 `a→b` 容量從 2 改成 20，預測新的最大流與最小割，再跑程式驗證。最小割的 `S` 側會變嗎？
2. 在無理容量的例子裡把 `M` 改成 1。增廣序列還合法嗎？`assert` 會在第幾步觸發？從 `3 + 2r ≈ 4.236` 和「容量 1 的邊上最多能累積多少流」推推看為什麼。
3. 改寫 `edmonds_karp`，讓它在每次增廣時記錄哪些邊是關鍵邊，統計每條邊當關鍵邊的次數，檢查都不超過 `|V|/2`。
4. 用 `ex4` 的 `QR` 類別，實作 DFS 版 Ford–Fulkerson（`find_path_dfs`）跑同一張無理網路。它會停嗎？停在多少？（提示：DFS 的路徑選擇不等於本章的對手策略。）

## 本章重點整理

- 流是滿足容量限制與守恆的邊函數；割只算 S→T 方向的容量。任何流 ≤ 任何割（弱對偶），由「流過任何割的淨量都等於流值」推出。
- Max-flow min-cut：沒有增廣路 ⇔ 是最大流 ⇔ 某個割的容量等於流值。演算法停下時，殘餘圖中 `s` 可達的點集就是最小割的一側。
- 反向邊讓「撤回」成為可能；沒有它，貪婪推流會卡在次佳解。
- Ford–Fulkerson 在整數容量下是偽多項式，無理容量下可能不停且收斂到錯誤的值；Edmonds–Karp 用 BFS 最短路，由「距離單調」與「關鍵邊每次讓距離加 2」得到與容量無關的 `O(VE²)`。
- 整數性定理讓 flow 能解組合問題；Menger 定理（邊版本、拆點後的點版本）是它的直接推論。

## 自我檢核

- [ ] 合上書，從流量守恆出發推導 `|f| = f(S,T) − f(T,S)`，並說出每一類邊在求和中貢獻什麼
- [ ] 如果有人問「max-flow 演算法停下來時，最小割在哪？為什麼那是最小的？」，你能在白板上兩分鐘講完嗎
- [ ] 不看程式，說明無理容量反例中 `r² = 1 − r` 在哪一步被用到
- [ ] 解釋 EK 的距離單調引理的證明裡，「新長出來的殘餘邊」為什麼只可能來自增廣路經過它的反方向
- [ ] 想一個你工作上的「最少要切斷幾個東西」問題（服務依賴、網路分段、權限路徑），它是邊割還是點割？要不要拆點？

## 延伸閱讀

### 論文

- **[Maximal Flow Through a Network](https://doi.org/10.4153/CJM-1956-045-5)** — L. R. Ford Jr.、D. R. Fulkerson，Canadian Journal of Mathematics 8（1956），399–404
  - **核心貢獻**：把最大流問題形式化，並證明 max-flow min-cut 定理。
  - **讀哪裡**：全文只有 6 頁，可以整篇讀；注意當時的記號和現代教科書不同。
  - **和本章的關聯**：本章「定理（Ford–Fulkerson 1956）」的源頭。前提：讀完本章的證明再讀，比較容易對上記號。
- **[Theoretical Improvements in Algorithmic Efficiency for Network Flow Problems](https://doi.org/10.1145/321694.321699)** — Jack Edmonds、Richard M. Karp，Journal of the ACM 19(2)（1972），248–264
  - **核心貢獻**：證明最短增廣路的步數上界與容量無關，也提出最大瓶頸路的變體與最小費用流的縮放技巧。
  - **讀哪裡**：講最大流的前半部（最短增廣路與最大瓶頸路）；最小費用流部分留到 Ch 24 再讀。
  - **和本章的關聯**：本章 EK 定理與引理 2、3 的原始來源。
- **[The Smallest Networks on Which the Ford–Fulkerson Maximum Flow Procedure May Fail to Terminate](https://doi.org/10.1016/0304-3975(95)00022-O)** — Uri Zwick，Theoretical Computer Science 148(1)（1995），165–170
  - **核心貢獻**：構造 6 點 8 邊的不終止網路並證明它是最小的；另給兩個 6 點 9 邊的簡單版本。
  - **讀哪裡**：前兩節的構造即可，最小性的證明是窮舉論證，可以跳過。
  - **和本章的關聯**：本章無理容量實驗所用網路的出處脈絡。前提：本章「無理容量」一節。
- **[On the History of the Transportation and Maximum Flow Problems](https://ir.cwi.nl/pub/10084/10084D.pdf)** — Alexander Schrijver，Mathematical Programming 91（2002），437–445
  - **這篇說什麼**：從 1930 年 Tolstoi 的運輸問題、1955 年 Harris–Ross 的 RAND 鐵路報告，一路講到 Ford–Fulkerson。
  - **讀哪裡**：整篇，十頁左右，不需要數學背景；本章開頭的歷史就是根據這篇。
  - **為什麼值得讀**：作者是組合最佳化的權威（README 裡那本百科全書的作者），把「最小割」最早的軍事動機講得很清楚。

### 書籍

- **《Introduction to Algorithms》第 4 版** — Cormen、Leiserson、Rivest、Stein（MIT Press，2022）
  - **讀哪幾章**：Ch 24 Maximum Flow（第 3 版是 Ch 26）。24.2 的 EK 分析與本章引理 2、3 同一條路線，記號更細。
  - **前提**：BFS、基本的漸進分析。
- **《Algorithm Design》** — Jon Kleinberg、Éva Tardos（Pearson，2005）
  - **讀哪幾章**：Ch 7 Network Flow。它的強項是**建模**：7.5 以後一連串把排班、選課、project selection、影像分割化成 flow 的例子，正好是練習 D 的預習。年代雖久，內容沒有過時。
- **《Introduction to Graph Theory》第 2 版** — Douglas B. West（Prentice Hall，2001）
  - **讀哪幾章**：4.3 Network Flow Problems；它用 flow 證 Menger 的寫法和本章一致，並把這套方法延伸到更多連通度定理。

### 官方文件

- **[NetworkX：Flows](https://networkx.org/documentation/stable/reference/algorithms/flow.html)**
  - **讀哪裡**：`maximum_flow`、`minimum_cut` 的參數說明，以及 `flow_func` 可選的演算法列表（`edmonds_karp`、`dinitz`、`preflow_push`、`shortest_augmenting_path`、`boykov_kolmogorov`）。
  - **注意**：預設 `flow_func` 是 `preflow_push`，不是 EK；Ch 21 會解釋 push-relabel 為什麼常常比較快。

EK 每輪只增廣一條路徑，而且每次都重跑整個 BFS。如果一次 BFS 就能把同樣長度的所有最短路一起灌滿呢？這就是 Dinic；再進一步，乾脆不找路徑、讓每個點自己往低處「倒水」，就是 push-relabel。

→ [Ch 21 Dinic 與 push-relabel](./21-dinic-push-relabel.md)
