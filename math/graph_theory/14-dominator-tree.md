# Ch 14 — Dominator tree 與 Lengauer–Tarjan

> **目標**：證明「支配者構成一條鏈」所以 dominator tree 存在；證明集合方程的迭代解正好是支配關係，並由此推出 Cooper–Harvey–Kennedy（CHK）演算法；講清楚 Lengauer–Tarjan 的半支配者（semidominator）在做什麼、為什麼正確（給出關鍵引理的證明與兩個主定理的證明架構）；兩個演算法自刻後與暴力定義、`nx.immediate_dominators` 四方對拍；推導並驗證支配邊界（dominance frontier），接上 SSA 的 φ 放置。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11（AMD Ryzen 7 5700G）。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

編譯器最佳化時一直在問同一個問題：「程式走到基本塊 `B` 時，`A` 是不是**一定**已經執行過？」如果是，`A` 裡算出的值在 `B` 一定可用（可以做冗餘消除）；`A` 裡的檢查在 `B` 一定已經通過（可以刪掉 `B` 的重複檢查）；`B → A` 的邊就是迴圈的回邊（迴圈辨識）。這個關係叫**支配（dominance）**：`A` 支配 `B`，若從入口到 `B` 的**每一條**路徑都經過 `A`。

1959 年 Prosser 在分析流程圖時第一次定義了它，用布林矩陣計算。之後的演算法史是一場「從 `O(n³)` 往線性逼近」的競賽：集合方程迭代、Lengauer–Tarjan（1979）的近線性演算法、Harel 與 Buchsbaum 等人的線性時間演算法，以及 2001 年 Cooper、Harvey、Kennedy 回頭證明「聰明地做集合方程迭代」在實務 CFG 上可以和 LT 一樣快。今天 NetworkX 用 CHK，較新版本的 LLVM 用 LT 家族的 Semi-NCA。

這章是 Part 2 的終點，也是全課第一個「直接長在工業編譯器裡」的演算法。它用到前面幾乎所有工具：DFS 樹的結構（Ch 11、13）、不變量與不動點（Ch 10、13）、樹上的最近共同祖先。學完後，[ssa_optimizations 課程](../../compilers/ssa_optimizations/README.md)的 Ch 2–5 對你來說應該是「看過證明的東西拿來用」。

## 先建立直覺

```text
           entry
             │
             ▼
     ┌────▶  B1 ───────┐              Dom(B4) = {entry, B1, B4}：到 B4 一定經過 B1，
     │       │          │              但不一定經過 B2（可以走 B3）
     │       ▼          ▼
     │       B2        B3              dominator tree（每個點的父親 = 立即支配者）：
     │       │          │                  entry
     │       └──▶ B4 ◀──┘                    └ B1
     │             │                           ├ B2
     │             ▼                           ├ B3
     └──────────── B5 ──▶ exit                 └ B4
                                                   └ B5
                                                       └ exit
```

把 CFG 想成一座城市的單行道，`entry` 是唯一的城門。`A` 支配 `B` 的意思是：`A` 是去 `B` 的**必經關卡**，在 `A` 設檢查哨就能攔下所有去 `B` 的人。必經關卡會排成一列（越靠近城門越早經過），最後一個關卡就是**立即支配者（immediate dominator, idom）**。

注意 dominator tree 和 CFG 的長相可以差很多：`B5 → B1` 這條回邊在 dominator tree 裡完全看不到；`B2`、`B3`、`B4` 在 CFG 上是「上下游」，在 dominator tree 上卻是兄弟。

> 本章大量使用 DFS 的前序編號、後序編號與 DFS 樹的祖先關係。如果不熟，先回看 [Ch 13 — 有向圖：SCC 與 DAG](./13-directed-graphs-scc-dag.md) 的「有向 DFS」一節。

## 定義與 dominator tree 的存在性

**流程圖（flowgraph）** `(G, r)`：有向圖 `G` 加上一個入口 `r`。只考慮從 `r` 可達的點；**不可達的點沒有支配關係**，所有演算法都必須先把它們排除（真實 CFG 裡的 dead code 就是這種點）。

- `d` **支配（dominates）** `v`，記 `d dom v`：每條從 `r` 到 `v` 的路徑都經過 `d`。每個點支配自己，`r` 支配所有可達的點。
- `d` **嚴格支配** `v`：`d dom v` 且 `d ≠ v`。
- `v ≠ r` 的**立即支配者** `idom(v)`：嚴格支配 `v`、而且被 `v` 的其他每個嚴格支配者支配的那一個。

`idom` 存在且唯一，靠的是下面這個引理。

**引理 1（支配者成鏈）**：若 `a` 和 `b` 都支配 `v`，則 `a dom b` 或 `b dom a`。

**證明**：設 `a` 不支配 `b`，要證 `b dom a`。因為 `a` 不支配 `b`，有一條從 `r` 到 `b` 的路徑 `P` 不經過 `a`。任取一條從 `b` 到 `v` 的**簡單**路徑 `Q`。`P` 接 `Q` 是一條從 `r` 到 `v` 的走訪，必經過 `a`，而 `P` 不含 `a`，所以 `a` 在 `Q` 上；令 `Q'` 為 `Q` 從 `a` 到 `v` 的後段，`Q` 是簡單路徑而 `b` 是它的起點，所以 `Q'` 不含 `b`（`a ≠ b`，因為 `b dom b` 而 `a` 不支配 `b`）。現在任取一條從 `r` 到 `a` 的路徑 `R`，`R` 接 `Q'` 是 `r` 到 `v` 的走訪，必經過 `b`；`Q'` 不含 `b`，所以 `b` 在 `R` 上。`R` 是任意的，所以 `b dom a`。∎

`dom` 是自反、遞移的（路徑論證），也是反對稱的：若 `a dom b`、`b dom a` 且 `a ≠ b`，取一條 `r` 到 `a` 的簡單路徑，它經過 `b`，它在 `b` 之前的前段是 `r` 到 `b` 的路徑卻不含 `a`（`a` 只出現在終點），與 `a dom b` 矛盾。所以 `dom` 是偏序；由引理 1，`v` 的支配者在這個偏序下是一條**全序的鏈** `r = d0 dom d1 dom … dom dk = v`。`idom(v) = d(k−1)`，唯一。把每個 `v` 接到 `idom(v)` 下面，得到以 `r` 為根的樹：**dominator tree**，`d dom v` ⇔ `d` 是 `v` 在這棵樹上的祖先。

## 方法一：集合方程與它的正確性

**引理 2**：對可達的 `v ≠ r`，

```text
Dom(v) = {v} ∪ ⋂ { Dom(p) : p 是 v 的可達前驅 }
```

**證明**：設 `d ≠ v`。（⇐）`d` 支配 `v` 的每個可達前驅：任何 `r` 到 `v` 的路徑最後一步是某個前驅 `p → v`，前段是 `r` 到 `p` 的路徑，經過 `d`。（⇒）若 `d` 不支配某個可達前驅 `p`，取 `r` 到 `p` 不經過 `d` 的路徑，接上 `p → v`，得到不經過 `d` 的 `r` 到 `v` 路徑（`d ≠ v`），所以 `d` 不支配 `v`。∎

所以真正的支配集合是這組方程的一個解。但方程可能有多個解（迴圈會產生「自我支撐」的解），我們要的是哪一個？

**定理 3**：從 `Dom(r) = {r}`、其他 `Dom(v) = V`（全集）開始，反覆套用方程直到不變，得到的正是真正的支配集合。

**證明**：令 `D*` 為真正的支配集合。

- **(a) 任何不動點 `F` 都滿足 `F(v) ⊆ D*(v)`。** 設 `d ∈ F(v)`，對 `r` 到 `v` 的路徑 `r = u0, …, uk = v` 的長度歸納，證明 `d` 在路徑上。`k = 0`：`F(r) = {r}`，`d = r`。`k ≥ 1`：若 `d = v` 已成立；否則由方程 `d ∈ F(u(k−1))`，歸納假設說 `d` 在前段上。
- **(b) 迭代過程中始終 `Dom(v) ⊇ D*(v)`。** 初始時成立（全集）。若當前每個集合都 `⊇ D*`，套用方程得到 `{v} ∪ ⋂ Dom(p) ⊇ {v} ∪ ⋂ D*(p) = D*(v)`（最後一步是引理 2）。
- **(c) 會停。** 由 (b) 同樣的單調性論證，每個集合只會縮小，有限集合不能無限縮小。

停下來時得到一個不動點，由 (a)、(b) 它同時 `⊆` 且 `⊇ D*`。∎

「從全集開始」是關鍵：從空集合開始會收斂到**最小**不動點，在有迴圈的圖上那不是支配關係。這和 Ch 13 DAG 上的 DP 不同——有圈時，迭代的初始值決定你得到哪一個解。資料流分析的「may vs must」分析就是這個差別（見 [ssa_optimizations 的資料流框架章](../../compilers/ssa_optimizations/07-dataflow-framework.md)）。

```python
# 範例 CFG：一個 if/else 包在 while 迴圈裡
#
#   entry → B1 → B2 → B4 → B5 → exit
#            ↘ B3 ↗       │
#            ↑────────────┘ (B5 → B1：迴圈回邊)
succ = {
    "entry": ["B1"], "B1": ["B2", "B3"], "B2": ["B4"], "B3": ["B4"],
    "B4": ["B5"], "B5": ["B1", "exit"], "exit": [],
}


def reachable(succ, r, banned=None):
    seen, stack = {r}, [r]
    while stack:
        u = stack.pop()
        for w in succ[u]:
            if w != banned and w not in seen:
                seen.add(w)
                stack.append(w)
    return seen


def dominators_brute(succ, r):
    """定義：d dom v ⇔ 刪掉 d 之後 v 從 r 走不到（d = v 或 d = r 時恆成立）。"""
    R = reachable(succ, r)
    dom = {v: {v, r} for v in R}
    for d in R - {r}:
        cut = reachable(succ, r, banned=d)
        for v in R - cut - {d}:
            dom[v].add(d)
    return dom


def dominators_dataflow(succ, r):
    """集合方程 Dom(v) = {v} ∪ ⋂ Dom(p)，從「全集」開始迭代到不動點。"""
    R = reachable(succ, r)
    pred = {v: [u for u in R if v in succ[u]] for v in R}
    dom = {v: set(R) for v in R}
    dom[r] = {r}
    rounds, changed = 0, True
    while changed:
        changed, rounds = False, rounds + 1
        for v in sorted(R - {r}):
            new = {v} | set.intersection(*(dom[p] for p in pred[v]))
            if new != dom[v]:
                dom[v], changed = new, True
    return dom, rounds


brute = dominators_brute(succ, "entry")
flow, rounds = dominators_dataflow(succ, "entry")
assert brute == flow
order = ["entry", "B1", "B2", "B3", "B4", "B5", "exit"]
for v in order:
    strict = brute[v] - {v}
    idom = max(strict, key=lambda d: len(brute[d])) if strict else None   # 支配者是一條鏈：最深的那個
    print(f"{v:5s} Dom = {sorted(brute[v], key=order.index)}  idom = {idom}")
print("集合方程迭代", rounds, "輪收斂，結果與定義一致")
```

```text
entry Dom = ['entry']  idom = None
B1    Dom = ['entry', 'B1']  idom = entry
B2    Dom = ['entry', 'B1', 'B2']  idom = B1
B3    Dom = ['entry', 'B1', 'B3']  idom = B1
B4    Dom = ['entry', 'B1', 'B4']  idom = B1
B5    Dom = ['entry', 'B1', 'B4', 'B5']  idom = B4
exit  Dom = ['entry', 'B1', 'B4', 'B5', 'exit']  idom = B5
集合方程迭代 2 輪收斂，結果與定義一致
```

輸出的每一行都可以用直覺檢查：`B5` 的支配者是 `entry, B1, B4, B5`，因為從 `B1` 往下兩條分支都在 `B4` 會合；`idom` 取的是支配鏈上最深的那個（支配集合最大者）。這個版本每個集合存 `O(n)` 個元素、每輪要做 `O(m)` 次集合交集，總成本可達 `O(n² m)`，只適合當標準答案。

## 方法二：Cooper–Harvey–Kennedy

CHK 的兩個觀察，把定理 3 變成快速演算法：

1. **集合用樹表示**。由引理 1，`Dom(v)` 是 dominator tree 上從 `v` 到根的路徑。迭代過程中的暫定集合也用同樣的方式表示：只存暫定的 `idom`，集合就是沿 `idom` 往上走到根的那條鏈。兩條鏈的交集是「從兩者的最近共同祖先往上」的鏈，所以集合交集變成**在暫定 idom 樹上找最近共同祖先**（`intersect`）。
2. **用後序編號找共同祖先**。DFS 樹上祖先的後序編號比後代大，CHK 證明暫定 idom 樹也維持這個性質，所以 `intersect(a, b)` 只要「編號小的那個往上爬」直到兩者相遇。依**反後序（reverse postorder）**處理點，讓多數前驅在被用到之前已經有暫定值。

CHK 論文的實驗結論是：在真實 CFG 上通常兩三輪就收斂，因為反後序讓資訊沿著前向邊一輪就傳到，只有回邊需要多繞。理論最壞情況是 `O(n²)` 每輪、輪數也可能很多；Georgiadis、Tarjan、Werneck（2006）的實驗發現 CHK 在某些人造的困難輸入上明顯變慢。

## 方法三：Lengauer–Tarjan

LT 不迭代，而是利用 DFS 樹的結構一次算出答案。以下把點和它的 **DFS 前序編號**視為同一個東西，`v < w` 表示 `v` 先被發現。

**半支配者（semidominator）**：

```text
sdom(w) = min{ v : 存在路徑 v = v0, v1, …, vk = w，且所有中間點 vi（0 < i < k）都 > w }
```

直覺：`sdom(w)` 是「繞過所有比 `w` 早發現的點、還能走到 `w` 的最早起點」。它是 idom 的一個容易計算的近似。

**引理 4（路徑引理）**：若 `v ≤ w`，任何從 `v` 到 `w` 的路徑都經過 `v` 和 `w` 在 DFS 樹上的某個共同祖先。

**證明**：令 `x` 為路徑上編號最小的點。一條邊若從 `x` 的子樹指到子樹外，它的終點一定比 `x` 早被發現（否則它會在 `x` 完成前被發現而成為後代），編號 `< x`。路徑從 `x` 之後若離開 `x` 的子樹，就會碰到比 `x` 小的點，違反 `x` 最小；所以 `w` 在 `x` 的子樹裡。`x` 的子樹恰好佔據一段連續的前序編號 `[x, x + |子樹| − 1]`，`x ≤ v ≤ w` 而 `w` 在這段裡，所以 `v` 也在，`x` 是兩者的共同祖先。∎

**引理 5**：對 `w ≠ r`，`sdom(w)` 是 `w` 的真祖先，`idom(w)` 是 `sdom(w)` 的祖先。

**證明**：DFS 樹上的父節點給出一條沒有中間點的路徑，所以 `sdom(w) ≤ parent(w) < w`。取實現 `sdom(w)` 的路徑，由引理 4 它經過 `sdom(w)` 與 `w` 的某個共同祖先；中間點都 `> w`，不可能是 `w` 的祖先（祖先編號較小），所以這個共同祖先只能是端點 `sdom(w)`——它是 `w` 的祖先，而且因為 `sdom(w) < w`，是真祖先。

`idom(w)` 支配 `w`，所以出現在 DFS 樹路徑 `r → w` 上，是 `w` 的祖先。若它是 `sdom(w)` 的**真後代**，考慮「樹路徑 `r → sdom(w)`」接「半支配路徑 `sdom(w) → w`」：前段只含 `sdom(w)` 和它的祖先，不含 `idom(w)`；後段的中間點都 `> w > idom(w)`，端點也不是 `idom(w)`。這條路徑避開了 `idom(w)`，矛盾。∎

接下來兩個主定理是 LT 演算法的核心。**這裡只給定理敘述與證明架構，不給完整證明**（完整證明在 Lengauer–Tarjan 1979 原論文，大約兩頁，用的工具正是引理 4、5）：

**半支配者定理**：

```text
sdom(w) = min( { v : (v, w) ∈ E 且 v < w }
             ∪ { sdom(u) : u > w，且存在邊 (v, w) 使 u 是 v 的祖先（含 v 本身） } )
```

架構：取實現 `sdom(w)` 的路徑的最後一條邊 `(v, w)`。若 `v < w`，路徑只有一條邊；否則路徑上「最後一個 `< v`…」的論證（引理 4）把它切成「到某個祖先 `u` 的半支配路徑」加上「沿樹往下」。

**支配者定理**：令 `u` 是 DFS 樹路徑 `sdom(w) → w`（不含 `sdom(w)`、含 `w`）上 `sdom` 最小的點。則

```text
idom(w) = sdom(w)    若 sdom(u) = sdom(w)
idom(w) = idom(u)    否則
```

架構：若路徑上沒有點的 `sdom` 比 `sdom(w)` 更早，就沒有路徑能從 `sdom(w)` 上面「繞過」它跳進這段，`sdom(w)` 就是 idom；否則 `u` 提供了一條繞道，`w` 的 idom 和 `u` 的 idom 相同。

**演算法**把兩個定理翻成資料結構操作：依前序編號**由大到小**處理 `w`；一個「link/eval 森林」裡恰好放著所有已處理（編號 `> w`）的點及它們的樹邊。`eval(v)` 回傳 `v` 在森林中往上的路徑上 `sdom` 最小的點——正好是半支配者定理第二項要的「`u > w` 且是 `v` 的祖先」。支配者定理需要的「樹路徑 `sdom(w) → w` 上 `sdom` 最小的點」要等這段路徑全部進入森林才能查，所以把 `w` 放進 `bucket[sdom(w)]`，等處理到 `sdom(w)` 的子節點、把它 link 上去之後再查。`eval` 做路徑壓縮，總時間 `O(m log n)`；再加上平衡的 link 就是 `O(m α(m, n))`。

## 實作與四方對拍

```python
import random
import networkx as nx


def postorder(succ, r):
    """迭代 DFS 後序（只含 r 可達的點）。"""
    order, seen, work = [], {r}, [(r, iter(succ[r]))]
    while work:
        v, it = work[-1]
        for w in it:
            if w not in seen:
                seen.add(w)
                work.append((w, iter(succ[w])))
                break
        else:
            work.pop()
            order.append(v)
    return order


def idom_chk(succ, r):
    """Cooper–Harvey–Kennedy：用 idom 樹表示支配者集合，依反後序迭代到不動點。"""
    po = postorder(succ, r)
    num = {v: i for i, v in enumerate(po)}             # 後序編號：越靠近 r 越大
    pred = {v: [] for v in po}
    for u in po:
        for w in succ[u]:
            pred[w].append(u)                          # u 可達 ⇒ w 可達，pred 只含可達的點
    idom = {r: r}

    def intersect(a, b):                               # 兩個點在 idom 樹上的最近共同祖先
        while a != b:
            while num[a] < num[b]:
                a = idom[a]
            while num[b] < num[a]:
                b = idom[b]
        return a

    changed = True
    while changed:
        changed = False
        for v in reversed(po):                         # 反後序
            if v == r:
                continue
            ps = [p for p in pred[v] if p in idom]     # 只用已經有暫定 idom 的前驅
            new = ps[0]
            for p in ps[1:]:
                new = intersect(p, new)
            if idom.get(v) != new:
                idom[v], changed = new, True
    del idom[r]
    return idom


def idom_lengauer_tarjan(succ, r):
    """Lengauer–Tarjan（簡單版：路徑壓縮，O(m log n)）。內部用 DFS 前序編號 0..N-1。"""
    vertex, num, parent = [], {}, []
    work = [(r, -1)]
    while work:                                        # 迭代 DFS 前序：模擬遞迴的發現順序
        v, p = work.pop()
        if v in num:
            continue
        num[v] = len(vertex)
        vertex.append(v)
        parent.append(p)
        for w in reversed(succ[v]):
            if w not in num:
                work.append((w, num[v]))
    N = len(vertex)
    pred = [[] for _ in range(N)]
    for v in vertex:
        for w in succ[v]:
            pred[num[w]].append(num[v])
    semi = list(range(N))
    ancestor = [-1] * N                                # link/eval 森林
    best = list(range(N))                              # 森林路徑上 semi 最小的點
    idom = [-1] * N
    samedom = [-1] * N
    bucket = [[] for _ in range(N)]

    def eval_(v):
        """回傳 v 在森林中往上到（不含）樹根的路徑上，semi 最小的點；順便壓縮路徑。"""
        path = []
        while ancestor[ancestor[v]] != -1:
            path.append(v)
            v = ancestor[v]
        for x in reversed(path):                       # 從最上面往下更新
            a = ancestor[x]
            if semi[best[a]] < semi[best[x]]:
                best[x] = best[a]
            ancestor[x] = ancestor[a]
        return best[path[0]] if path else best[v]

    for w in range(N - 1, 0, -1):
        p = parent[w]
        s = p
        for v in pred[w]:
            s2 = v if v <= w else semi[eval_(v)]       # 半支配者定理的兩種候選
            s = min(s, s2)
        semi[w] = s
        bucket[s].append(w)
        ancestor[w] = p                                # link(p, w)
        for v in bucket[p]:                            # 這時 p 到 v 的樹路徑已全部連上
            y = eval_(v)
            if semi[y] == semi[v]:
                idom[v] = p                            # 支配者定理：idom(v) = sdom(v)
            else:
                samedom[v] = y                         # idom(v) = idom(y)，晚點再補
        bucket[p] = []
    for w in range(1, N):                              # 依前序補完延後的那些
        if samedom[w] != -1:
            idom[w] = idom[samedom[w]]
    return {vertex[w]: vertex[idom[w]] for w in range(1, N)}


def idom_brute(succ, r):
    def reach(banned):
        seen, stack = {r}, [r]
        while stack:
            u = stack.pop()
            for w in succ[u]:
                if w != banned and w not in seen:
                    seen.add(w)
                    stack.append(w)
        return seen
    R = reach(None)
    dom = {v: {v, r} for v in R}
    for d in R - {r}:
        for v in R - reach(d) - {d}:
            dom[v].add(d)
    return {v: max(dom[v] - {v}, key=lambda d: len(dom[d])) for v in R - {r}}


rng = random.Random(14)
for t in range(2000):
    n = rng.randint(1, 16)
    G = nx.gnp_random_graph(n, rng.choice([0.08, 0.15, 0.3]), seed=rng.randint(0, 10**9), directed=True)
    succ = {v: sorted(G[v]) for v in G}
    truth = idom_brute(succ, 0)
    assert idom_chk(succ, 0) == truth
    assert idom_lengauer_tarjan(succ, 0) == truth
    assert nx.immediate_dominators(G, 0) == truth      # NetworkX 3.7：結果不含 start 本身
print("2000 張隨機流程圖（含不可達點、不可約迴圈）：暴力 = CHK = Lengauer–Tarjan = NetworkX")

succ = {"entry": ["B1"], "B1": ["B2", "B3"], "B2": ["B4"], "B3": ["B4"],
        "B4": ["B5"], "B5": ["B1", "exit"], "exit": [], "dead": ["B4"]}
print("CHK:", dict(sorted(idom_chk(succ, "entry").items())))
print("LT :", dict(sorted(idom_lengauer_tarjan(succ, "entry").items())))
print("NX :", dict(sorted(nx.immediate_dominators(nx.DiGraph([(u, v) for u in succ for v in succ[u]]), "entry").items())))
```

```text
2000 張隨機流程圖（含不可達點、不可約迴圈）：暴力 = CHK = Lengauer–Tarjan = NetworkX
CHK: {'B1': 'entry', 'B2': 'B1', 'B3': 'B1', 'B4': 'B1', 'B5': 'B4', 'exit': 'B5'}
LT : {'B1': 'entry', 'B2': 'B1', 'B3': 'B1', 'B4': 'B1', 'B5': 'B4', 'exit': 'B5'}
NX : {'B1': 'entry', 'B2': 'B1', 'B3': 'B1', 'B4': 'B1', 'B5': 'B4', 'exit': 'B5'}
```

這 2000 張隨機圖裡（seed 14），另外統計過有 1538 張含有從入口不可達的點、509 張含有不可約（irreducible）的迴圈——也就是有 DFS 回溯邊 `u → v` 但 `v` 不支配 `u`。手寫的 CFG 範例幾乎都是可約的，隨機圖才會把這些情況大量測到。最後的範例多加了一個從入口走不到的 `dead → B4`：三種實作都不把 `dead` 放進結果，`B4` 的 idom 也不受影響。

實作重點：

- **兩種 DFS 寫法**：CHK 需要後序，用 Ch 13 的「點 + 迭代器」寫法；LT 要的是前序與 DFS 樹的父節點，用「彈出時才標記」的堆疊寫法——它產生的仍是一棵合法的 DFS 樹（每個被彈出的點都是目前最深、還有未訪鄰居的點的鄰居），而且 `reversed(succ[v])` 讓走訪順序和遞迴版一致。
- **`eval_` 的迭代版路徑壓縮**：教科書（Appel 的《Modern Compiler Implementation》）寫成遞迴；這裡先收集到森林根的路徑，再從上往下更新 `best` 與 `ancestor`，避免深 CFG 的遞迴上限。
- **NetworkX 3.7 的結果不含 `start` 本身**：較舊的 NetworkX 版本回傳 `{start: start, ...}`，3.7 的原始碼在最後 `del idom[start]`。上面的對拍直接拿 `nx.immediate_dominators(G, 0) == truth` 比較，`truth` 也不含入口；在其他版本上跑要先 `pop` 掉入口。

### 計時：Python 裡誰比較快

```python
# 計時實驗（接續上一段程式：貼在它的最後面執行）：輸出的秒數因機器而異
import time


def cfg_like(n, seed):
    """像 CFG 的隨機圖：一條主幹 0→1→…→n-1，加上前向跳躍（if/else）與少量回邊（迴圈）。"""
    rng = random.Random(seed)
    succ = {i: [i + 1] if i + 1 < n else [] for i in range(n)}
    for i in range(n - 2):
        if rng.random() < 0.3:
            succ[i].append(rng.randint(i + 2, min(n - 1, i + 20)))   # 往前跳
        if rng.random() < 0.1 and i > 0:
            succ[i].append(rng.randint(max(0, i - 50), i))           # 回邊
    return succ


for n in (10_000, 100_000):
    succ = cfg_like(n, seed=n)
    G = nx.DiGraph([(u, v) for u in succ for v in succ[u]])
    res = {}
    for name, f in [("CHK", lambda: idom_chk(succ, 0)),
                    ("Lengauer–Tarjan", lambda: idom_lengauer_tarjan(succ, 0)),
                    ("nx.immediate_dominators", lambda: nx.immediate_dominators(G, 0))]:
        t = time.perf_counter()
        res[name] = f()
        print(f"n={n:7d} {name:24s} {time.perf_counter() - t:6.2f}s")
    assert len({tuple(sorted(r.items())) for r in res.values()}) == 1
```

```text
n=  10000 CHK                        0.02s
n=  10000 Lengauer–Tarjan            0.02s
n=  10000 nx.immediate_dominators    0.05s
n= 100000 CHK                        0.40s
n= 100000 Lengauer–Tarjan            0.25s
n= 100000 nx.immediate_dominators    0.70s
```

（這段要接在上一段程式後面執行，所以實際畫面上前面還會先印出上一段的四行輸出；這裡只列計時部分。AMD Ryzen 7 5700G、Windows 11、Python 3.13.12 實測一次，你的數字會不同。）在這種「主幹 + 前向跳躍 + 短回邊」的 CFG 型隨機圖上，三者都是近線性，自刻 LT 稍快於 CHK，`nx.immediate_dominators` 多了建 `DiGraph` 視圖與一般化介面的成本。這不能推廣成「LT 一定比 CHK 快」：CHK 原論文在真實程式的 CFG 上量到的是相反的結論，GTW 2006 則發現結果依輸入類型而定。在 Python 裡，常數因子（字典查找、函式呼叫）往往比漸進複雜度更重要。

## 支配邊界：φ 該放在哪裡

**定義**：`DF(x) = { y : x 支配 y 的某個前驅，但 x 不嚴格支配 y }`。直覺：`DF(x)` 是 `x` 的「支配範圍」剛好結束的地方——從 `x` 支配的區域走一步就出去的那些點。

為什麼 SSA 需要它：若變數 `a` 在 `x` 被賦值，這個定義在 `x` 支配的區域裡是唯一能到達的定義；一旦走到 `DF(x)` 裡的點 `y`，`y` 還可以從別的前驅到達（那些前驅不被 `x` 支配），`a` 的值可能來自不同的定義，就需要 `φ`。Cytron 等人（1991）證明：最小 SSA 的 φ 位置正好是定義點集合的**迭代支配邊界** `DF+`。

**CHK 的 DF 演算法**：對每個匯合點 `y`、每個前驅 `p`，從 `p` 沿 idom 往上走，把路上每個點的 DF 加入 `y`，直到碰到 `idom(y)` 為止。

**正確性**：設 `p` 是 `y` 的前驅。`idom(y)` 嚴格支配 `y`，由引理 2 它支配 `p`，所以 `idom(y)` 在 `p` 的支配鏈上。`p` 的支配鏈上，`idom(y)` **之下**的點 `x` 都支配 `p`、但不嚴格支配 `y`（`y` 的嚴格支配者是 `idom(y)` 和它的祖先），所以 `y ∈ DF(x)`；`idom(y)` 和它之上的點嚴格支配 `y`，不在。反過來，任何 `y ∈ DF(x)` 的 `x` 都支配某個前驅 `p`，所以在 `p` 的支配鏈上、且在 `idom(y)` 之下——正好被走到。只有一個前驅的 `y`，`idom(y)` 就是那個前驅，迴圈一步都不走，所以跳過它只是加速。∎

這個證明有一個邊界情況：**入口 `r` 沒有 `idom`**。若有邊指回入口（整個函式是一個迴圈），`r` 即使只有一個前驅也可能在某些點的 DF 裡。我們第一版程式照教科書寫「只處理 ≥ 2 個前驅的點」，對拍時在一張 `4 → 0` 的隨機圖上和定義、NetworkX 都對不上；修正後的版本把 `y == r` 也當匯合點處理（NetworkX 的原始碼也是 `if u == start or len(G.pred[u]) >= 2`）。

```python
import random
import networkx as nx


def dominance_frontier(succ, r, idom):
    """CHK 的 DF 演算法：只看匯合點（≥2 個可達前驅），從每個前驅往上爬到 idom(y) 為止。"""
    reach = set(idom) | {r}
    pred = {v: [u for u in reach if v in succ[u]] for v in reach}
    df = {v: set() for v in reach}
    full = dict(idom)
    full[r] = None
    for y in reach:
        if y != r and len(pred[y]) < 2:            # 入口若有前驅（迴圈回到入口），也要當匯合點
            continue
        for p in pred[y]:
            runner = p
            while runner != full[y]:
                df[runner].add(y)
                runner = full[runner]
    return df


def dominance_frontier_by_definition(succ, r, idom):
    """DF(x) = { y : x 支配 y 的某個前驅，但 x 不嚴格支配 y }。"""
    reach = set(idom) | {r}
    full = dict(idom)
    full[r] = None

    def dominates(a, b):                               # 沿 idom 鏈往上找
        while b is not None:
            if b == a:
                return True
            b = full[b]
        return False

    return {x: {y for y in reach for p in reach if y in succ[p]
                and dominates(x, p) and not (dominates(x, y) and x != y)}
            for x in reach}


def phi_blocks(df, defs):
    """迭代支配邊界 DF+(defs)：Cytron 等人的 φ 放置位置。"""
    result, work = set(), list(defs)
    while work:
        x = work.pop()
        for y in df[x]:
            if y not in result:
                result.add(y)
                if y not in defs:
                    work.append(y)
    return result


succ = {"entry": ["B1"], "B1": ["B2", "B3"], "B2": ["B4"], "B3": ["B4"],
        "B4": ["B5"], "B5": ["B1", "exit"], "exit": []}
G = nx.DiGraph([(u, v) for u in succ for v in succ[u]])
idom = nx.immediate_dominators(G, "entry")
df = dominance_frontier(succ, "entry", idom)
assert df == dominance_frontier_by_definition(succ, "entry", idom) == nx.dominance_frontiers(G, "entry")
for v in ["entry", "B1", "B2", "B3", "B4", "B5", "exit"]:
    print(f"DF({v}) = {sorted(df[v])}")

# x = 0 在 entry；B2 裡 x = x + 1；B3 裡 x = x * 2  → 哪裡需要 φ(x)？
print("φ(x) 放在:", sorted(phi_blocks(df, {"entry", "B2", "B3"})))
print("只在 B2 定義的 y:", sorted(phi_blocks(df, {"B2"})))

rng = random.Random(99)
for t in range(1000):
    n = rng.randint(1, 14)
    H = nx.gnp_random_graph(n, rng.choice([0.1, 0.2, 0.35]), seed=rng.randint(0, 10**9), directed=True)
    s = {v: sorted(H[v]) for v in H}
    idom = nx.immediate_dominators(H, 0)
    a = dominance_frontier(s, 0, idom)
    assert a == dominance_frontier_by_definition(s, 0, idom) == nx.dominance_frontiers(H, 0)
print("1000 張隨機流程圖：CHK 的 DF 演算法 = 定義 = NetworkX")
```

```text
DF(entry) = []
DF(B1) = ['B1']
DF(B2) = ['B4']
DF(B3) = ['B4']
DF(B4) = ['B1']
DF(B5) = ['B1']
DF(exit) = []
φ(x) 放在: ['B1', 'B4']
只在 B2 定義的 y: ['B1', 'B4']
1000 張隨機流程圖：CHK 的 DF 演算法 = 定義 = NetworkX
```

讀法：

- `DF(B2) = DF(B3) = {B4}`：if/else 兩個分支在 `B4` 會合。`B2` 裡改了 `x`，`B4` 就需要 `φ(x)`。
- `DF(B1) = {B1}`、`DF(B4) = DF(B5) = {B1}`：迴圈的 header `B1` 在自己的支配邊界裡——迴圈內的定義會沿回邊流回 header，和入口進來的值會合。
- 只在 `B2` 定義的 `y`，`φ` 位置也是 `{B1, B4}`：`DF(B2) = {B4}`，而 `B4` 的 φ 本身也是一個定義，`DF(B4) = {B1}`，迭代後加入 `B1`。這就是 `DF+` 要「迭代」的原因。（`B1` 的 φ 有一個運算元來自入口、此時 `y` 尚未定義——最小 SSA 不考慮活性，pruned SSA 會把這種死 φ 刪掉，見 [ssa_optimizations Ch 4 的 SSA 變體](../../compilers/ssa_optimizations/04-dominance-frontier.md)。）

## 對比與取捨

| 方法 | 時間 | 實作難度 | 適合 |
| --- | --- | --- | --- |
| 暴力（刪點 + 可達性） | `O(n(n + m))` | 最低 | 對拍標準答案 |
| 集合方程（位元集合） | 最壞 `O(n² m)`，實務好很多 | 低 | 教學；位元向量在小函式很快 |
| CHK | 理論最壞較差；實測真實 CFG 很快 | 低（約 30 行） | NetworkX、許多 JIT |
| Lengauer–Tarjan（路徑壓縮） | `O(m log n)` | 中高 | 大型、病態 CFG |
| LT（平衡 link） | `O(m α(m, n))` | 高 | 理論保證 |
| Semi-NCA | 最壞 `O(n²)`，實務比 SLT 略快 | 中 | 較新版本 LLVM 的 `GenericDomTreeConstruction.h`，支援增量更新 |

## 踩雷集錦

1. **不可達的點**：dead code 在 CFG 裡很常見。若把它當成普通點餵進集合方程，它沒有可達前驅，`⋂` 是空交集——寫成「全集」的話它會被每個點支配，污染結果。正確做法是先限制在可達的點上，上面的三個實作都這樣做。
2. **以為 NetworkX 的結果包含入口**：NetworkX 3.7 的 `immediate_dominators` 不再包含 `start: start`（我們實測，原始碼最後一行是 `del idom[start]`），從舊版教材抄來的 `idom[start] == start` 判斷會 `KeyError`。`dominance_frontiers` 反而**包含** `start`。
3. **DF 演算法漏掉入口**：入口有前驅（例如整個函式體是 `while (1)`）時，只處理「≥ 2 個前驅」的點會漏掉 `r ∈ DF(x)`。我們自己的第一版就踩了這個雷，是隨機對拍抓到的。
4. **從空集合開始迭代**：集合方程從空集合起跑會收斂到最小不動點，在有迴圈時得到錯誤的支配集合。支配是「must」分析，必須從全集（最樂觀的「所有點都支配」）往下縮。
5. **把 DFS 祖先當成支配者**：支配者一定是 DFS 樹上的祖先，反之不然。上例中 `B2` 是 `B4` 的 DFS 祖先（若 DFS 先走 `B2`），卻不支配 `B4`。LT 的 `sdom` 就是在修正「祖先」和「支配者」之間的落差。

## 進階：再往深一層

- **後支配（post-dominance）與控制相依**：在反向圖上以 `exit` 為入口算 dominator tree 就是 post-dominator tree。`y` 控制相依於 `x` ⇔ `x` 在反向圖的支配邊界裡——Cytron 等人同一篇論文就給出了這個關係，它是 program slicing 與 CodeQL 等工具建 PDG 的基礎（見 [code_auditing 的程式表示法章](../../security/code_auditing/03-program-representations-cpg.md)）。
- **增量維護**：最佳化 pass 一直在改 CFG，每次重算 dominator tree 太貴。LLVM 的 `DomTreeUpdater` 支援插入／刪除邊後的增量更新，背後是 Georgiadis 等人的動態支配者演算法。
- **線性時間**：Harel（1985）、Alstrup 等人與 Buchsbaum 等人給出了線性時間演算法，但實作複雜、常數大；GTW 2006 的實驗是了解「理論最快」與「實務最快」差距的好起點。
- **逆向工程中的應用**：反編譯器做控制流結構化（把 goto 還原成 if/while）時，dominator tree 與 post-dominator tree 是辨識 if-then-else 的會合點與迴圈出口的主要工具；練習 B 的 CFG 分析器就是這條管線的第一步（見 [reverse_engineering 的控制流辨識章](../../security/reverse_engineering/05-recognizing-control-flow.md)）。

## 動手練習

1. 在 `dominators_dataflow` 裡把初始值改成空集合（入口仍是 `{r}`），在範例 CFG 上看結果哪裡錯，並用定理 3 的 (b) 解釋。
2. 數 CHK 的輪數：讓 `idom_chk` 回傳 `while changed` 跑了幾輪，在 2000 張隨機圖與 `cfg_like` 圖上做統計。再試試依**前序**而非反後序處理，輪數變多少？
3. 在 LT 實作裡加入「平衡 link」（按子樹大小合併森林），用 `cfg_like(10**6)` 比較兩版的時間。理論上漸進較好，實際呢？
4. 用 `idom_chk` 在反向圖上算 post-dominator tree，再用 `dominance_frontier` 算控制相依，在範例 CFG 上驗證「`B2`、`B3` 控制相依於 `B1`」。
5. 構造一個讓 CHK 需要很多輪的 CFG（提示：一長串互相交錯的回邊），測它與 LT 的時間差。

## 本章重點整理

- 支配者構成一條鏈（引理 1），所以 idom 唯一，dominator tree 存在；`d dom v` ⇔ `d` 是 `v` 在樹上的祖先。
- `Dom(v) = {v} ∪ ⋂ Dom(p)` 有多個解；從全集開始迭代得到的最大不動點才是支配關係（定理 3）。CHK 把集合存成 idom 樹、交集變成用後序編號找共同祖先。
- LT 的半支配者是「繞過所有更早的點還能到達 `w` 的最早起點」；`idom(w)` 是 `sdom(w)` 的祖先（引理 5），兩個主定理把 `sdom` 修正成 `idom`。link/eval 森林 + bucket 讓它在 `O(m log n)` 內完成。
- `DF(x)` 是 `x` 支配範圍的邊界；CHK 的 DF 演算法從每個前驅沿 idom 往上爬到 `idom(y)`。φ 放在定義點的 `DF+`。
- 不可達點、NetworkX 3.7 不含入口、入口有前驅，是三個實際會咬人的邊界情況。

## 自我檢核

- [ ] 不看筆記，證明「兩個支配 `v` 的點必有一個支配另一個」，並說明證明中為什麼要取**簡單**路徑 `Q`。
- [ ] 能解釋為什麼支配集合的迭代必須從全集開始，並舉出從空集合開始會得到的錯誤結果。
- [ ] 能用一句話說出 `sdom(w)` 的直覺，並證明它是 `w` 的真祖先。
- [ ] 給你一個 CFG，能手算每個點的 DF，並說明迴圈 header 為什麼會在自己的 DF 裡。
- [ ] 如果有人說「Lengauer–Tarjan 是 `O(m α)` 所以一定比 CHK 快」，你能說出至少兩個反駁的理由。

## 延伸閱讀

### 論文

- **[A Fast Algorithm for Finding Dominators in a Flowgraph](https://doi.org/10.1145/357062.357071)** — Thomas Lengauer、Robert Endre Tarjan，ACM TOPLAS 1(1):121–141（1979）
  - **核心貢獻**：半支配者的定義、半支配者定理與支配者定理，以及 link/eval 森林的兩種版本（路徑壓縮 `O(m log n)`、平衡 `O(m α)`）。
  - **讀哪裡**：定理與證明的部分（本章只給了架構的那兩個主定理），以及附錄的完整虛擬碼；可以逐行和本章的 `idom_lengauer_tarjan` 對照。
  - **前提**：本章引理 4、5；熟悉 DFS 前序編號。
- **[A Simple, Fast Dominance Algorithm](https://www.cs.tufts.edu/comp/150FP/archive/keith-cooper/dom14.pdf)** — Keith D. Cooper、Timothy J. Harvey、Ken Kennedy（Rice University 技術報告 TR-06-33870；NetworkX 文件引用的版本見 [Rice 典藏](https://hdl.handle.net/1911/96345)）
  - **核心貢獻**：證明「用 idom 樹表示集合 + 反後序 + 後序編號求交集」讓集合方程迭代在實務上和 LT 一樣快，並給出本章使用的 DF 演算法。這篇常被引用為 2001 年的期刊論文，但我們查不到出版社的紀錄，以技術報告引用最保險。
  - **讀哪裡**：整篇（十幾頁）；特別是 `intersect` 的正確性論證與實驗比較那一節。
  - **前提**：本章定理 3。
- **[Efficiently Computing Static Single Assignment Form and the Control Dependence Graph](https://doi.org/10.1145/115372.115320)** — Ron Cytron、Jeanne Ferrante、Barry K. Rosen、Mark N. Wegman、F. Kenneth Zadeck，ACM TOPLAS 13(4):451–490（1991）
  - **核心貢獻**：用支配邊界定義並計算 φ 位置（`DF+`），同時用反向圖的 DF 建控制相依圖。SSA 能普及就是因為這篇讓它可以有效率地建構。
  - **讀哪裡**：支配邊界的定義與計算、φ 放置的定理；控制相依那一節可配合本章「進階」讀。
  - **前提**：本章 DF 一節；知道 SSA 是什麼（[ssa_optimizations Ch 1](../../compilers/ssa_optimizations/01-why-ssa.md)）。
- **[Finding Dominators in Practice](https://doi.org/10.7155/jgaa.00119)** — Loukas Georgiadis、Robert E. Tarjan、Renato F. Werneck，Journal of Graph Algorithms and Applications 10(1):69–94（2006）
  - **核心貢獻**：在真實 CFG 與人造困難輸入上，系統比較迭代法、LT 的兩個版本與 Semi-NCA，結論是沒有一個演算法在所有輸入上都最好。
  - **讀哪裡**：實驗設計與結果討論；它解釋了為什麼本章的計時不能推廣。
  - **前提**：本章 CHK 與 LT 兩節。
- **[Applications of Boolean Matrices to the Analysis of Flow Diagrams](https://doi.org/10.1145/1460299.1460314)** — Reese T. Prosser，Eastern Joint IRE-AIEE-ACM Computer Conference（1959），pp. 133–138
  - **核心貢獻**：支配概念的起源，用布林矩陣分析流程圖。
  - **讀哪裡**：給歷史興趣的讀者，看「支配」最早是怎麼被描述的。
  - **前提**：無。

### 書籍

- **《Advanced Compiler Design and Implementation》** — Steven S. Muchnick（Morgan Kaufmann，1997）
  - **讀哪幾章**：Ch 7「Control-Flow Analysis」：§7.3 支配者（含迭代法與 LT）、§7.4 迴圈與 SCC、§7.5 可約性。書雖然出版快 30 年，控制流分析的內容沒有過時；它是練習 B 的主要參考。
  - **前提**：本章；知道基本塊與 CFG。

### 相關課程

- **[ssa_optimizations Ch 3 — 支配樹算法](../../compilers/ssa_optimizations/03-dominator-tree-algorithm.md)** 與 **[Ch 5 — φ-node 插入](../../compilers/ssa_optimizations/05-phi-insertion.md)**：同一套演算法在 LLVM 裡的樣子。本章補上的是那邊略過的正確性證明、CHK 演算法，以及可以對拍的 Python 實作。

Part 2 到此結束。從度數序列到 dominator tree，我們證明了一系列「圖的結構」定理，也把它們寫成可以對拍的程式。下一個練習把 Ch 13–14 拼成一個完整工具：讀進一張 CFG，交出 idom tree、支配邊界、回邊與自然迴圈，並正確處理不可約迴圈與不可達的點。

→ [練習 B：CFG 分析器](./practice-b-cfg-analyzer.md)
