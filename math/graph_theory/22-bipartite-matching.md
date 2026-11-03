# Ch 22 — 二分圖匹配：Hall、König、Hopcroft–Karp

> **目標**：能用增廣路解釋「為什麼匹配還能再大」，完整證明 Hall 定理與 König 定理，並且自己寫出 Kuhn 與 Hopcroft–Karp、說得出後者 `O(E√V)` 的來源。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

排 on-call 表：五個工程師、五套系統，每人只會其中幾套，每套系統要剛好一個人負責。排班的人通常這樣做：從第一個人開始，給他一個他會的、還沒被拿走的系統。這叫**貪婪（greedy）**，而它會失敗——下一節你會看到它排出 4 人，但其實 5 人全排得進去。

更麻煩的是失敗之後的問題：排不滿 5 人時，你怎麼**證明**真的排不滿，而不是你的演算法太笨？老闆不會接受「我試了很多次」。這一章給你兩種「證書」：

- 排不滿時，Hall 定理保證存在一群人 `S`，他們會的系統總數 `|N(S)|` 比人數少。把 `S` 拿給老闆看，爭論結束。
- 匹配大小是 `k` 時，König 定理保證能找出 `k` 個點蓋住所有邊，這是「不可能超過 `k`」的證書。

歷史上，這兩個定理比演算法早。Dénes Kőnig 在 1931 年證明二分圖的最大匹配等於最小點覆蓋；Philip Hall 在 1935 年給出「相異代表系」存在的充要條件。真正有效率的演算法要等到 1973 年 Hopcroft 與 Karp 的 `O(E√V)`。中間這幾十年，大家知道答案長什麼樣子，但沒有快的方法算。

> 這章會用到 Ch 20 的 max-flow：二分匹配是 max-flow 的特例。如果你對 residual graph 與增廣路還不熟，先回看 [Ch 20 — 最大流最小割與 Edmonds–Karp](./20-max-flow-min-cut.md)。

## 先建立直覺

把匹配想成「每個人手上最多拿一張票、每張票最多給一個人」。增廣路是一條**換票鏈**：

```text
  bob 想要 db，但 db 在 amy 手上
  amy 也會 k8s，k8s 沒人拿

  bob ──(想要)── db ══(已配)══ amy ──(想要)── k8s
  未配                                          未配

  沿鏈交換：bob 拿 db、amy 改拿 k8s
  bob ══ db ── amy ══ k8s        配對數 +1
```

鏈的兩端都是「沒配到」的點，中間「想要」與「已配」交替出現。沿鏈把兩種邊對調，配對數剛好加一，而且每個人仍然最多一張票。整章的演算法都是在找這種鏈；整章的定理都是在說「找不到鏈時，你可以拿出什麼證書」。

## 定義

設 `G = (X ∪ Y, E)` 是**二分圖（bipartite graph）**：每條邊一端在 `X`、一端在 `Y`。

- **匹配（matching）** `M`：兩兩沒有共同端點的邊集合。被 `M` 中某條邊碰到的點叫**被飽和（saturated）**，否則叫**自由（free / exposed）**。
- **最大匹配（maximum matching）**：邊數最多的匹配，大小記為 `ν(G)`。**極大匹配（maximal matching）**是「不能再直接加邊」的匹配——貪婪給的就是極大，不一定最大。
- **交錯路（alternating path）**：邊交替屬於 `M`、不屬於 `M` 的路。
- **增廣路（augmenting path）**：兩端都自由的交錯路。它的邊數是奇數，非匹配邊比匹配邊多一條。
- **對稱差** `M Δ P`：屬於恰好其中一個集合的邊。若 `P` 是 `M` 的增廣路，`M Δ P` 是匹配且 `|M Δ P| = |M| + 1`。
- **點覆蓋（vertex cover）** `C`：每條邊至少一端在 `C` 裡。最小點覆蓋大小記為 `τ(G)`。

任何匹配的每條邊都需要一個覆蓋點，而且不同匹配邊的端點互不相同，所以永遠有 `ν(G) ≤ τ(G)`。König 定理說二分圖上等號成立。

## 範例一：貪婪失敗，Kuhn 修正

Kuhn 演算法對每個左點做一次 DFS 找增廣路。中文競程圈常把它直接叫「匈牙利演算法」，但嚴格說匈牙利演算法是 Ch 24 的加權版本，兩者共用增廣路這個核心。

```python
import networkx as nx


def greedy_matching(adj, left):
    """先到先選：每個左點拿第一個還沒被拿走的右點。"""
    taken = {}
    for u in left:
        for v in sorted(adj[u]):
            if v not in taken:
                taken[v] = u
                break
    return taken


def kuhn_matching(adj, left):
    """adj: dict[左點, set[右點]]；回傳 dict 右點 -> 左點 的最大匹配。"""
    match_r = {}                      # 右點 -> 配到的左點

    def try_augment(u, seen):
        for v in sorted(adj[u]):      # sorted 讓輸出可重現
            if v in seen:
                continue
            seen.add(v)
            # v 沒人配，或 v 的舊伴侶能改配別人 -> 找到增廣路
            if v not in match_r or try_augment(match_r[v], seen):
                match_r[v] = u
                return True
        return False

    for u in left:
        try_augment(u, set())
    return match_r


# 工程師 -> 會的 on-call 系統
adj = {
    "amy":  {"db", "k8s"},
    "bob":  {"db"},
    "cara": {"k8s", "ci"},
    "dan":  {"net"},
    "eve":  {"dns", "net"},
}
left = list(adj)
g = greedy_matching(adj, left)
print("貪婪    :", len(g), sorted((u, v) for v, u in g.items()))
m = kuhn_matching(adj, left)
print("自刻 Kuhn:", len(m), sorted((u, v) for v, u in m.items()))

G = nx.Graph()
G.add_nodes_from(left, bipartite=0)
for u, vs in adj.items():
    for v in vs:
        G.add_edge(u, v)
nm = nx.bipartite.hopcroft_karp_matching(G, top_nodes=left)
print("NetworkX :", len(nm) // 2)    # 回傳的 dict 兩個方向都有，所以除以 2
```

```text
貪婪    : 4 [('amy', 'db'), ('cara', 'ci'), ('dan', 'net'), ('eve', 'dns')]
自刻 Kuhn: 5 [('amy', 'k8s'), ('bob', 'db'), ('cara', 'ci'), ('dan', 'net'), ('eve', 'dns')]
NetworkX : 5
```

貪婪讓 amy 先拿走 db，bob 就沒得選了。Kuhn 處理 bob 時，`try_augment("bob")` 發現 db 被 amy 拿走，遞迴問 amy「你能不能換一個」，amy 換到 k8s——這正是上一節那條換票鏈。

`seen` 為什麼每次重設、在同一次 DFS 內共用？同一次搜尋裡，一個右點如果已經試過且失敗，再試一次結果一樣，所以不重試；這讓每次 DFS 是 `O(E)`，全部是 `O(V·E)`。

## 底層機制：為什麼「沒有增廣路」就代表最大？

演算法在找不到增廣路時停下。我們要證明那時的匹配真的最大，否則整個方法沒有根據。

**引理（對稱差引理）**：設 `M`、`M*` 是任意圖的兩個匹配，`|M*| > |M|`。則 `M Δ M*` 含有至少 `|M*| − |M|` 條點不相交的 `M`-增廣路。

**證明**：令 `H` 是只由 `M Δ M*` 的邊組成的子圖。每個點最多碰一條 `M` 邊、一條 `M*` 邊，所以 `H` 中每個點度數 ≤ 2，`H` 的每個連通分量是路或環。沿著分量走，相鄰兩條邊不能同屬一個匹配（否則共用端點），所以邊交替屬於 `M` 與 `M*`。交替的環必為偶數長，`M` 與 `M*` 邊數相等。一條交替路的兩種邊數相差至多 1。所以每個分量的「`M*` 邊數減 `M` 邊數」屬於 `{−1, 0, 1}`，全部加起來等於 `|M* \ M| − |M \ M*| = |M*| − |M|`。因此至少有 `|M*| − |M|` 個分量的差是 `+1`，這種分量是兩端都是 `M*` 邊的交替路。它的端點不被 `M` 飽和：端點 `v` 若被某條 `M` 邊 `e` 碰到，`e` 不在 `M*`（`v` 已經被路上的 `M*` 邊佔用），所以 `e ∈ M Δ M*`，`v` 在 `H` 中的度數會是 2，與「`v` 是路的端點」矛盾。所以這些分量都是 `M`-增廣路，彼此點不相交。∎

**推論**：`M` 是最大匹配，若且唯若沒有 `M`-增廣路。（有增廣路就能加大；沒有時，若存在更大的 `M*`，引理會造出增廣路，矛盾。）這個推論對**一般圖**也成立，在 [Ch 23](./23-general-matching-blossom.md) 叫 Berge 定理；二分圖的特殊之處在於「找增廣路」這一步很好做。

還差一件事：Kuhn 每個左點只做一次 DFS，失敗的左點之後不再嘗試，這樣會不會漏？不會。

**引理（失敗點永遠失敗）**：設自由左點 `u` 沒有 `M`-增廣路，`P` 是另一條 `M`-增廣路。則 `u` 也沒有 `(M Δ P)`-增廣路。

**證明**：令 `R` 是從 `u` 出發經交錯路（左到右走非匹配邊、右到左走匹配邊）可達的點集。`R` 有兩個封閉性質：`R` 中左點的所有鄰居都在 `R`（非匹配邊直接走到；匹配邊的另一端是它的伴侶，而 `u` 以外的左點本來就是從伴侶走過來的）；`R` 中右點都被配、伴侶也在 `R`（右點若自由，`u` 到它就是增廣路）。`P` 的兩端是自由左點 `x ≠ u` 與自由右點 `y`。`x ∉ R`，因為 `R` 中除了 `u` 的左點都被配；`y ∉ R`，因為 `R` 沒有自由右點。假設 `P` 碰到 `R`，從 `x` 沿 `P` 走，第一個落在 `R` 的點 `z` 的前一個點不在 `R`。`z` 若是左點，前一個點是它的鄰居，必在 `R`，矛盾。所以 `z` 是右點，接下來 `P` 沿匹配邊走到 `z` 的伴侶（在 `R`），再沿某條邊走到該左點的鄰居（在 `R`）……每一步都留在 `R`，最後走到 `y`，於是 `y ∈ R`，矛盾。所以 `P` 與 `R` 不相交，增廣只改動 `R` 以外的匹配邊，`R` 的兩個封閉性質在 `M Δ P` 下照樣成立，`R` 裡仍然沒有自由右點，`u` 仍然沒有增廣路。∎

## Hall 定理

**Hall 定理（1935）**：二分圖 `G = (X ∪ Y, E)` 存在飽和整個 `X` 的匹配，若且唯若對每個 `S ⊆ X`，`|N(S)| ≥ |S|`。這裡 `N(S)` 是 `S` 的鄰居集合。

**必要性**：若 `M` 飽和 `X`，`S` 中每個點的伴侶互不相同且都在 `N(S)` 裡，所以 `|N(S)| ≥ |S|`。

**充分性**：對 `|X|` 歸納。`|X| = 1` 時，Hall 條件說那個點至少有一個鄰居，配給它。設 `|X| ≥ 2`，分兩種情況。

1. **對每個非空的真子集 `S ⊊ X`，都有 `|N(S)| ≥ |S| + 1`**（條件有餘裕）。任選邊 `xy`，把 `x`、`y` 刪掉得到 `G'`。對 `G'` 中左側的任意非空 `S ⊆ X − {x}`：`S` 是 `X` 的非空真子集，`|N_G(S)| ≥ |S| + 1`，刪掉 `y` 最多少一個鄰居，所以 `|N_{G'}(S)| ≥ |S|`。由歸納假設，`G'` 有飽和 `X − {x}` 的匹配，加上 `xy` 即可。
2. **存在非空真子集 `S0 ⊊ X` 使 `|N(S0)| = |S0|`**（條件剛好卡住）。
   - 子圖 `G[S0 ∪ N(S0)]` 滿足 Hall 條件（`S0` 的子集的鄰居都在 `N(S0)` 裡，條件從 `G` 繼承），且 `|S0| < |X|`，歸納得到飽和 `S0` 的匹配 `M1`。它用光了 `N(S0)`。
   - 令 `G'' = G − S0 − N(S0)`。對 `T ⊆ X − S0`，有 `N_{G''}(T) = N_G(T ∪ S0) − N(S0)`，所以 `|N_{G''}(T)| = |N_G(T ∪ S0)| − |N(S0)| ≥ (|T| + |S0|) − |S0| = |T|`。`G''` 滿足 Hall 條件，歸納得到飽和 `X − S0` 的匹配 `M2`。
   - `M1 ∪ M2` 的端點互不重疊，飽和整個 `X`。∎

上面的證明是存在性的。演算法給的是更實用的版本：Kuhn 跑完後，從一個沒配到的左點 `u` 出發做交錯搜尋，可達的左點集合 `S` 就是違反 Hall 條件的證書。理由：可達的每個右點都已被配（否則找到增廣路），而且它的伴侶也可達；所以可達右點 `N(S)` 與 `S − {u}` 一一對應，`|N(S)| = |S| − 1`。

```python
import itertools
import random


def kuhn_matching(adj, left):
    match_r = {}

    def try_augment(u, seen):
        for v in sorted(adj[u]):
            if v in seen:
                continue
            seen.add(v)
            if v not in match_r or try_augment(match_r[v], seen):
                match_r[v] = u
                return True
        return False

    for u in left:
        try_augment(u, set())
    return match_r


def hall_witness(adj, left):
    """若左側無法全部配對，回傳 S ⊆ left 使 |N(S)| < |S|；否則回傳 None。"""
    match_r = kuhn_matching(adj, left)
    matched_left = set(match_r.values())
    free = [u for u in left if u not in matched_left]
    if not free:
        return None
    # 從一個沒配到的左點出發，沿「非匹配邊到右、匹配邊回左」走
    S, NS, stack = {free[0]}, set(), [free[0]]
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in NS:
                NS.add(v)
                w = match_r[v]          # v 一定有伴侶，否則就找到增廣路了
                if w not in S:
                    S.add(w)
                    stack.append(w)
    return S, NS


adj = {
    "amy":  {"db", "k8s"},
    "bob":  {"db"},
    "cara": {"k8s", "ci"},
    "dan":  {"net"},
    "eve":  {"net"},              # eve 不再會 dns
}
S, NS = hall_witness(adj, list(adj))
print("Hall 違反者 S =", sorted(S), " N(S) =", sorted(NS))


# 暴力驗證 Hall 定理：隨機二分圖上，「左側可全配」<=> 「所有 S 都滿足 |N(S)| >= |S|」
def hall_holds(adj, left):
    for r in range(1, len(left) + 1):
        for S in itertools.combinations(left, r):
            if len(set().union(*(adj[u] for u in S))) < len(S):
                return False
    return True


rng = random.Random(22)
agree = total = saturating = 0
for _ in range(3000):
    nl, nr = rng.randint(1, 6), rng.randint(1, 6)
    p = rng.random()
    adj = {u: {v for v in range(nr) if rng.random() < p} for u in range(nl)}
    full = len(kuhn_matching(adj, list(adj))) == nl
    saturating += full
    agree += full == hall_holds(adj, list(adj))
    total += 1
print(f"{agree}/{total} 張圖兩邊一致（其中 {saturating} 張可全配）")
```

```text
Hall 違反者 S = ['dan', 'eve']  N(S) = ['net']
3000/3000 張圖兩邊一致（其中 1153 張可全配）
```

這就是給老闆的證書：dan 和 eve 都只會 net，兩個人搶一套系統。注意暴力檢查要列舉 `2^|X|` 個子集，只能當驗證工具；演算法給的證書是線性時間。

**推論（正則二分圖有完美匹配）**：若 `G` 是 `k`-正則二分圖（每點度數都是 `k ≥ 1`），則 `G` 有完美匹配。證明：數邊得 `k|X| = |E| = k|Y|`，所以 `|X| = |Y|`。對任意 `S ⊆ X`，從 `S` 出發的 `k|S|` 條邊全部落在 `N(S)`，而 `N(S)` 總共只接 `k|N(S)|` 條邊，所以 `k|S| ≤ k|N(S)|`，Hall 條件成立。∎ 反覆拿掉一個完美匹配，剩下的圖仍是正則二分圖，於是 `k`-正則二分圖的邊可以分成 `k` 個完美匹配——這就是 König 的邊著色定理在正則情況的版本。

**推論（König 1916 邊著色定理）**：任何二分圖 `G` 的邊色數都等於最大度數 `Δ`。證明：把 `G` 擴充成 `Δ`-正則的二分**多重圖**（允許重邊）。先在較小的一側補孤立點，讓 `|X| = |Y|`。只要還有點度數小於 `Δ`：兩側度數總和都等於邊數，又兩側點數相同，所以如果 `X` 側有點度數不足，`Y` 側也一定有，在這兩點之間加一條邊。每加一條邊，總缺額減 2，所以會停，停下時每點度數都是 `Δ`。上面的推論證明只用到數邊，對多重圖一樣成立，所以擴充後的圖可以分成 `Δ` 個完美匹配。每個匹配塗一色，再拿掉補上的點和邊，就得到 `G` 的 `Δ`-邊著色；又邊色數至少 `Δ`，因為同一點的 `Δ` 條邊必須互不同色。∎ 回連 [Ch 15 — 著色：貪婪、Brooks、Vizing](./15-graph-coloring.md)：二分圖永遠是 Class 1，不會落到 Vizing 的 `Δ + 1` 那一類。

## König 定理

**König 定理（1931）**：二分圖中 `ν(G) = τ(G)`。

**證明（構造性）**：設 `M` 是最大匹配，`U ⊆ X` 是自由左點。令 `Z` 是從 `U` 出發、經交錯路（左到右走非匹配邊、右到左走匹配邊）可達的所有點（包含 `U` 本身）。定義

```text
C = (X − Z) ∪ (Y ∩ Z)
```

**`C` 是點覆蓋**：取邊 `xy`，`x ∈ X`。若 `x ∉ Z`，`x ∈ C`。若 `x ∈ Z`，分兩種：`xy ∉ M` 時，從 `x` 走非匹配邊可到 `y`，所以 `y ∈ Z ∩ Y ⊆ C`；`xy ∈ M` 時，`x` 不是自由點（不在 `U`），它被加進 `Z` 只能是從伴侶 `y` 走匹配邊過來，所以 `y ∈ Z`。

**`|C| = |M|`**：

- `Y ∩ Z` 的每個點都被 `M` 飽和，否則從 `U` 到它的交錯路是增廣路，與 `M` 最大矛盾。
- `X − Z` 的每個點都被 `M` 飽和，因為自由左點都在 `U ⊆ Z`。
- 沒有一條 `M` 邊兩端同時在 `C`：若 `xy ∈ M`、`y ∈ Z`，則從 `y` 走匹配邊到 `x`，`x ∈ Z`，於是 `x ∉ C`。

所以 `C` 的每個點對應一條不同的 `M` 邊，`|C| ≤ |M|`。配合一般的 `|M| ≤ τ ≤ |C|`，得 `|C| = |M| = τ`。∎

```python
import itertools
import random

import networkx as nx


def kuhn_matching(adj, left):
    match_r = {}

    def try_augment(u, seen):
        for v in sorted(adj[u]):
            if v in seen:
                continue
            seen.add(v)
            if v not in match_r or try_augment(match_r[v], seen):
                match_r[v] = u
                return True
        return False

    for u in left:
        try_augment(u, set())
    return match_r


def konig_cover(adj, left):
    """由最大匹配建出同樣大小的點覆蓋：C = (X \\ Z) ∪ (Y ∩ Z)。"""
    match_r = kuhn_matching(adj, left)
    matched_left = set(match_r.values())
    # Z = 從未匹配左點出發，交錯路（左->右走非匹配邊，右->左走匹配邊）可達的點
    Z_left = {u for u in left if u not in matched_left}
    Z_right = set()
    stack = list(Z_left)
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in Z_right:
                Z_right.add(v)
                w = match_r[v]
                if w not in Z_left:
                    Z_left.add(w)
                    stack.append(w)
    cover = (set(left) - Z_left) | Z_right
    return match_r, cover


def min_cover_brute(adj, left, right):
    nodes = list(left) + list(right)
    edges = [(u, v) for u in left for v in adj[u]]
    for k in range(len(nodes) + 1):
        for C in itertools.combinations(nodes, k):
            Cs = set(C)
            if all(u in Cs or v in Cs for u, v in edges):
                return k


adj = {"amy": {"db", "k8s"}, "bob": {"db"}, "cara": {"k8s", "ci"},
       "dan": {"net"}, "eve": {"net"}}
m, C = konig_cover(adj, list(adj))
print("|M| =", len(m), " cover =", sorted(C))

rng = random.Random(7)
ok = 0
for trial in range(500):
    nl, nr = rng.randint(1, 5), rng.randint(1, 5)
    adj = {("L", i): {("R", j) for j in range(nr) if rng.random() < 0.4}
           for i in range(nl)}
    left, right = list(adj), [("R", j) for j in range(nr)]
    m, C = konig_cover(adj, left)
    assert all(u in C or v in C for u in left for v in adj[u])   # 真的是覆蓋
    assert len(C) == len(m) == min_cover_brute(adj, left, right)
    G = nx.Graph()
    G.add_nodes_from(left)
    G.add_nodes_from(right)
    G.add_edges_from((u, v) for u in left for v in adj[u])
    nxm = nx.bipartite.hopcroft_karp_matching(G, top_nodes=left)
    nxc = nx.bipartite.to_vertex_cover(G, nxm, top_nodes=left)
    assert len(nxc) == len(C)
    ok += 1
print(ok, "張隨機二分圖：|最大匹配| = |自刻覆蓋| = |暴力最小覆蓋| = |NetworkX 覆蓋|")
```

```text
|M| = 4  cover = ['amy', 'bob', 'cara', 'net']
500 張隨機二分圖：|最大匹配| = |自刻覆蓋| = |暴力最小覆蓋| = |NetworkX 覆蓋|
```

`{amy, bob, cara, net}` 四個點蓋住全部邊，所以任何匹配都不可能超過 4——這是「排不到 5 人」的第二種證書。`nx.bipartite.to_vertex_cover` 的文件說它用「König 定理保證的程序」從最大匹配建覆蓋，也就是這個 `Z` 構造；它要求傳入的匹配必須是最大匹配。

**另一條路：從 max-flow min-cut 推 König。** 加源點 `s` 連到每個 `x ∈ X`、每個 `y ∈ Y` 連到匯點 `t`，原來的邊由 `X` 指向 `Y`，全部容量 1。整數流定理（Ch 20）說最大流有整數解，它就是一個匹配。最小割 `(S, T)` 的容量裡，`s → x`（`x ∈ T`）與 `y → t`（`y ∈ S`）各算 1；把中間 `X → Y` 的邊容量改成無限大，最小割就不會切中間邊，於是 `(X ∩ T) ∪ (Y ∩ S)` 是點覆蓋、大小等於割的容量。max-flow = min-cut 就翻譯成 `ν = τ`。Ch 26 會用 LP 對偶與全單模矩陣，把這類 min-max 定理放在同一個框架下。

## Hopcroft–Karp：一次找一整批最短增廣路

Kuhn 每次只增廣一條，最壞要 `V` 次、每次 `O(E)`。Hopcroft–Karp 的想法：每個 **phase** 先做一次 BFS 把圖分層，然後找一組**極大的、點不相交的、最短**增廣路，一口氣全部增廣。

```text
phase k：
  層 0          層 1          層 2
  自由左點 ──→ 右點 ══> 左點 ──→ 右點 ══> 左點 ──→ 自由右點
            非匹配    匹配     非匹配    匹配     非匹配

  BFS：算每個左點的層號，記下第一次碰到自由右點的層 limit
  DFS：只走「層號 +1」的邊，到 limit 層碰到自由右點就增廣
       失敗的點標成死路（dist = ∞），同一 phase 不再走
```

**為什麼是 `O(E√V)`**：每個 phase 是一次 BFS 加一次 DFS 掃描，`O(E)`，因為每個點失敗一次就被標死。關鍵是 phase 數。

1. **引理 A（最短增廣路長度嚴格遞增）**：每個 phase 結束後，新的最短增廣路比這個 phase 用的長度嚴格更長。這裡只給證明架構：先證「沿一條最短增廣路增廣，不會讓最短長度變短」（用對稱差引理配合長度計數），再證「一組極大的點不相交最短路全部增廣後，同長度的增廣路不再存在」——若還有一條同長度的，它必與這組中某條路共用點，從長度計數可推出它其實更長。完整證明見 Hopcroft–Karp 原論文的 Theorem 1–2。
2. **計數**：做完 `√V` 個 phase 後，最短增廣路長度至少 `2√V + 1`（長度都是奇數，每個 phase 至少加 2）。設 `M*` 最大、目前匹配 `M`。由對稱差引理，`M Δ M*` 含 `|M*| − |M|` 條點不相交的增廣路，每條至少有 `2√V + 2` 個點，所以 `|M*| − |M| ≤ V / (2√V + 2) < √V`。
3. **收尾**：剩下不到 `√V` 單位，每個 phase 至少增廣一條，最多再 `√V` 個 phase。總共 `O(√V)` 個 phase，總時間 `O(E√V)`。∎（步驟 2、3 是完整的；步驟 1 是證明架構。）

```python
import random
import sys
import time
from collections import deque

import networkx as nx

sys.setrecursionlimit(100_000)    # DFS 深度 = 增廣路長度，大圖可能很深


def kuhn_matching(adj, left):
    match_r = {}

    def try_augment(u, seen):
        for v in adj[u]:
            if v in seen:
                continue
            seen.add(v)
            if v not in match_r or try_augment(match_r[v], seen):
                match_r[v] = u
                return True
        return False

    for u in left:
        try_augment(u, set())
    return match_r


def hopcroft_karp(adj, left):
    """回傳 dict 左點 -> 右點。每個 phase：BFS 分層 + DFS 找一組極大的點不相交最短增廣路。"""
    mate_l = {u: None for u in left}
    mate_r = {}
    INF = float("inf")
    phases = 0
    while True:
        # --- BFS：從所有自由左點出發，算左點的層號 dist ---
        dist = {}
        q = deque()
        for u in left:
            if mate_l[u] is None:
                dist[u] = 0
                q.append(u)
        limit = INF                      # 最短增廣路在哪一層碰到自由右點
        while q:
            u = q.popleft()
            if dist[u] >= limit:
                continue
            for v in adj[u]:
                w = mate_r.get(v)
                if w is None:
                    limit = min(limit, dist[u])
                elif w not in dist:
                    dist[w] = dist[u] + 1
                    q.append(w)
        if limit == INF:
            return mate_l, phases
        phases += 1

        # --- DFS：只沿 dist 遞增 1 的邊走，用過就把點標成死路 ---
        def dfs(u):
            for v in adj[u]:
                w = mate_r.get(v)
                if (w is None and dist[u] == limit) or \
                   (w is not None and dist.get(w) == dist[u] + 1 and dfs(w)):
                    mate_l[u] = v
                    mate_r[v] = u
                    return True
            dist[u] = INF                # 這個點在本 phase 不會再成功
            return False

        for u in left:
            if mate_l[u] is None and dist.get(u) == 0:
                dfs(u)


def random_bipartite(n, avg_deg, seed):
    rng = random.Random(seed)
    adj = {("L", i): set() for i in range(n)}
    for _ in range(n * avg_deg):
        adj[("L", rng.randrange(n))].add(("R", rng.randrange(n)))
    return {u: sorted(vs) for u, vs in adj.items()}


# 1) 正確性對拍
for seed in range(200):
    adj = random_bipartite(random.Random(seed).randint(1, 40), 2, seed)
    left = list(adj)
    G = nx.Graph()
    G.add_nodes_from(left)
    G.add_edges_from((u, v) for u in left for v in adj[u])
    ref = len(nx.bipartite.hopcroft_karp_matching(G, top_nodes=left)) // 2
    hk, _ = hopcroft_karp(adj, left)
    assert sum(v is not None for v in hk.values()) == ref == len(kuhn_matching(adj, left))
print("200 張隨機圖：HK、Kuhn、NetworkX 基數一致")

# 2) 計時
for n, d in [(2000, 2), (2000, 6), (20000, 2), (20000, 6)]:
    adj = random_bipartite(n, d, seed=2026)
    left = list(adj)
    G = nx.Graph()
    G.add_nodes_from(left)
    G.add_edges_from((u, v) for u in left for v in adj[u])
    t = time.perf_counter(); km = kuhn_matching(adj, left); tk = time.perf_counter() - t
    t = time.perf_counter(); hk, ph = hopcroft_karp(adj, left); th = time.perf_counter() - t
    t = time.perf_counter(); nm = nx.bipartite.hopcroft_karp_matching(G, top_nodes=left)
    tn = time.perf_counter() - t
    print(f"n={n:>5} 平均度={d}  |M|={len(km):>5}  Kuhn {tk:6.3f}s  "
          f"自刻HK {th:6.3f}s ({ph} phases)  nx {tn:6.3f}s")
```

```text
200 張隨機圖：HK、Kuhn、NetworkX 基數一致
n= 2000 平均度=2  |M|= 1594  Kuhn  0.002s  自刻HK  0.007s (9 phases)  nx  0.019s
n= 2000 平均度=6  |M|= 1994  Kuhn  0.190s  自刻HK  0.014s (6 phases)  nx  0.030s
n=20000 平均度=2  |M|=15684  Kuhn  0.053s  自刻HK  0.190s (15 phases)  nx  0.516s
n=20000 平均度=6  |M|=19948  Kuhn 46.109s  自刻HK  0.734s (9 phases)  nx  1.505s
```

計時條件：AMD Ryzen 7 5700G、Windows 11、Python 3.13.12 單執行緒，單次執行（沒有重複取中位數，數字會有幾十 % 的抖動）。讀法：

- 平均度 2 時圖很稀疏，大部分增廣路長度 1，Kuhn 幾乎不需要遞迴，反而比 HK 快——HK 每個 phase 固定付出一次全圖 BFS。
- 平均度 6 時匹配接近完美，最後幾百條增廣路很長，Kuhn 每條都從頭 DFS；`n = 20000` 時本機兩次執行分別是 46 秒與 59 秒，HK 都在 0.8 秒內，差距約 60–75 倍。HK 的 phase 數只有 9，遠小於 `2√V ≈ 400` 的上界——隨機圖不是最壞情況。
- 自刻 HK 比 NetworkX 快，不代表 NetworkX 寫得差：它要處理任意 hashable 節點、回傳雙向 dict。不要把這個比例推廣到別的圖。

## 對比與取捨

| 方法 | 時間 | 適用 | 備註 |
| --- | --- | --- | --- |
| 貪婪 | `O(E)` | 只要極大匹配 | 至少是最大匹配的一半大（每條貪婪邊最多擋掉最大匹配的兩條） |
| Kuhn（逐點 DFS） | `O(VE)` | 小圖、稀疏圖 | 20 行寫完，競程常用 |
| Hopcroft–Karp | `O(E√V)` | 大圖 | 最壞情況保證最好的經典演算法 |
| 化成 max-flow + Dinic | `O(E√V)` | 已有 flow 函式庫 | 單位容量網路上 Dinic 的 phase 數同樣是 `O(√V)`，見 [Ch 21](./21-dinic-push-relabel.md) |
| Blossom（Edmonds） | `O(V³)` 常見實作 | **非二分圖** | Ch 23 |
| 匈牙利 / min-cost flow | `O(V³)` | 邊有權重 | Ch 24 |

## 踩雷集錦

1. **「極大」不是「最大」**：貪婪停下來是因為不能再直接加邊，不是因為最大。範例一的貪婪給 4、最大是 5。判斷最大的唯一標準是「沒有增廣路」。
2. **`top_nodes` 給錯，NetworkX 會安靜地給錯答案**：

   ```python
   import itertools

   import networkx as nx


   def nu_tau(G):
       """暴力算最大匹配大小 ν 與最小點覆蓋大小 τ。"""
       E = list(G.edges())
       nu = max(k for k in range(len(E) + 1)
                for M in itertools.combinations(E, k)
                if len({x for e in M for x in e}) == 2 * k)
       tau = min(k for k in range(G.number_of_nodes() + 1)
                 for C in itertools.combinations(G.nodes(), k)
                 if all(u in C or v in C for u, v in E))
       return nu, tau


   for name, G in [("C4", nx.cycle_graph(4)), ("C5", nx.cycle_graph(5)),
                   ("K3", nx.complete_graph(3)), ("Petersen", nx.petersen_graph())]:
       print(f"{name:9} 二分圖={nx.is_bipartite(G)!s:5}  ν, τ = {nu_tau(G)}")

   # 二分圖，但 top_nodes 給錯：P4 = 0-1-2-3，正確分邊是 {0, 2} / {1, 3}
   P4 = nx.path_graph(4)
   bad = nx.bipartite.hopcroft_karp_matching(P4, top_nodes=[0])
   print("top 給錯:", len(bad) // 2, " 真正的 ν:", len(nx.max_weight_matching(P4, maxcardinality=True)))

   # 把非二分圖硬塞給二分圖演算法
   try:
       nx.bipartite.hopcroft_karp_matching(nx.cycle_graph(5), top_nodes=[0, 2, 4])
   except Exception as e:
       print(type(e).__name__, ":", e)
   ```

   ```text
   C4        二分圖=True   ν, τ = (2, 2)
   C5        二分圖=False  ν, τ = (2, 3)
   K3        二分圖=False  ν, τ = (1, 2)
   Petersen  二分圖=False  ν, τ = (5, 6)
   top 給錯: 1  真正的 ν: 2
   KeyError : 4
   ```

   `P4` 給錯分邊時回傳 1（真值 2），沒有任何警告；`C5` 則丟出看不出原因的 `KeyError`。NetworkX 3.7 的這個函式**不驗證**輸入是否真的二分。上線前先 `nx.is_bipartite(G)`，分邊用 `nx.bipartite.sets(G)` 算，不要手寫。
3. **König 只對二分圖成立**：上面輸出的 `C5`、`K3`、Petersen 都有 `ν < τ`。奇圈是兩者分開的原因：`2k + 1` 個點的奇圈最多配 `k` 條邊，卻要 `k + 1` 個點才蓋得住。把 König 用在一般圖上，等於假設了一個不存在的證書。
4. **NetworkX 回傳的 dict 是雙向的**：`hopcroft_karp_matching` 回傳 `{u: v, v: u, ...}`，`len()` 是匹配大小的兩倍。`max_weight_matching` 則回傳 `set` of tuple，`len()` 就是大小。兩個 API 的慣例不同，對拍時要各自換算。
5. **Python 遞迴深度**：Kuhn 與 HK 的 DFS 深度等於增廣路長度，在長鏈圖上可達 `V`。預設上限 1000 會讓 `n = 20000` 的圖丟 `RecursionError`；範例用 `sys.setrecursionlimit` 頂著，生產程式碼應改寫成顯式 stack。
6. **Hall 條件要檢查所有子集，包括大的**：常見誤解是「每個左點度數 ≥ 1 就夠」或「只看兩兩一組」。`{dan, eve}` 的例子裡每個人都有一個鄰居，但兩人共用同一個。

## 進階：再往深一層

- **Dulmage–Mendelsohn 分解**：König 證明中的 `Z` 構造可以推廣成把二分圖切成「一定被配、可能被配、一定不被配」三區的標準分解。稀疏矩陣的 block triangular form（例如求解器把方程組拆成可依序解的小塊）就是用它算的。
- **Gallai 恆等式**：沒有孤立點的圖中，`ν + ρ = n`，`ρ` 是最小邊覆蓋大小；`α + τ = n`，`α` 是最大獨立集大小。配合 König，二分圖的最大獨立集可以多項式時間求出——一般圖的最大獨立集卻是 NP-hard（Ch 34）。試著自己證這兩條，它們只用到本章的定義。
- **穩定匹配（Gale–Shapley）不是最大匹配問題**：它要的是沒有「互相更喜歡對方」的配對，條件是偏好序而不是邊數。兩者常被混淆。
- **線上匹配**：右點一個一個到來、到了就要立刻決定配不配。貪婪的競爭比是 1/2，Karp–Vazirani–Vazirani 的 RANKING 演算法達到 `1 − 1/e`。廣告投放系統是典型場景。
- **加權**：邊有成本、要找最便宜的完美匹配時，增廣路要沿最短路走，這是 [Ch 24](./24-min-cost-flow-assignment.md) 的匈牙利演算法。

## 動手練習

1. 把範例一的 `kuhn_matching` 改成 `left` 逆序處理，匹配結果會變嗎？大小會變嗎？用對稱差引理解釋為什麼大小不變。
2. 寫 `is_maximum(adj, left, matching)`：不呼叫任何匹配演算法，只做一次交錯 BFS 判斷匹配是否最大。用 `random.Random(1)` 產生 100 張小圖對拍。
3. 把 `hopcroft_karp` 的 `dist[u] = INF` 那行刪掉，在 `n = 20000`、平均度 6 的圖上重新計時。慢了多少？為什麼結果仍然正確？
4. 建構一張二分圖，讓 Kuhn 的總工作量接近 `V·E`（提示：讓每次增廣都要沿一條很長的鏈翻轉）。用計數器量 `try_augment` 的呼叫次數。
5. 用 Ch 20 的 max-flow 實作（或 `nx.maximum_flow`）把範例一建模成網路流，確認流量值等於 5，並從最小割讀出 König 覆蓋。

## 本章重點整理

- 匹配最大若且唯若沒有增廣路；證明的核心是對稱差 `M Δ M*` 分解成交替路與偶環。
- Hall 定理給「左側全配」的充要條件；演算法跑不滿時，交錯搜尋可達的左點集合就是違反條件的證書。
- König 定理：二分圖 `ν = τ`，覆蓋由 `C = (X − Z) ∪ (Y ∩ Z)` 構造；它也可以從 max-flow min-cut 推出。奇圈讓它在一般圖失效。
- Hopcroft–Karp 每個 phase 增廣一整批最短路，phase 數 `O(√V)`，總時間 `O(E√V)`；稀疏隨機圖上 Kuhn 可能更快，接近完美的匹配上差距可達數十倍（本機實測）。

## 自我檢核

- [ ] 不看筆記，能用「對稱差的分量只有交替路與偶環」證明「沒有增廣路 ⇒ 最大」嗎？
- [ ] Hall 定理充分性證明裡，為什麼要分成「條件有餘裕」和「條件剛好卡住」兩種情況？只處理第一種會卡在哪？
- [ ] 如果面試官問「你怎麼證明這個排班真的排不滿」，你會交出什麼？要多少時間算出來？
- [ ] 能說出 König 構造中 `C` 為什麼恰好每條匹配邊用一個點，而不是兩個或零個？
- [ ] Hopcroft–Karp 的 `√V` 是從哪個不等式來的？哪一步需要「最短增廣路長度嚴格遞增」？
- [ ] 想一個你工作中的分配問題（CI runner 配 job、review 配 reviewer），它是二分匹配嗎？如果邊有權重或容量大於 1，要換成哪一章的工具？

## 延伸閱讀

### 論文

- **[An n^5/2 Algorithm for Maximum Matchings in Bipartite Graphs](https://doi.org/10.1137/0202019)** — John E. Hopcroft、Richard M. Karp，SIAM Journal on Computing 2(4):225–231（1973）
  - **核心貢獻**：提出「每個 phase 找一組極大的點不相交最短增廣路」，證明只需 `O(√n)` 個 phase，總時間 `O((m + n)√n)`。
  - **讀哪裡**：前三頁的 Theorem 1–2 與 Corollary，就是本章引理 A 省略的完整證明；全文只有 7 頁。密西根大學 Seth Pettie 的課程頁有[掃描版](https://web.eecs.umich.edu/~pettie/matching/Hopcroft-Karp-bipartite-matching.pdf)。
  - **前提知識**：本章的對稱差引理。
- **[On Representatives of Subsets](https://gauss.dartmouth.edu/~m38s12/sources/Hall1935.pdf)** — P. Hall，Journal of the London Mathematical Society s1-10(1):26–30（1935）
  - **核心貢獻**：相異代表系（system of distinct representatives）存在的充要條件，也就是本章的 Hall 定理。
  - **讀哪裡**：全文 5 頁，原始證明的符號是集合族而不是二分圖，讀的時候把「集合 `A_i`」翻成「左點 `i` 的鄰居」。連結是 Dartmouth 課程頁上的掃描版。
  - **和本章的關聯**：本章的歸納證明與原證明思路不同，對照兩者可以看出同一個定理的多種證法。
- **[Graphs and Matrices（英譯）](https://arxiv.org/abs/2009.03780)** — Dénes Kőnig 原著，Gábor Szárnyas 英譯，arXiv:2009.03780（原文 *Gráfok és mátrixok*，Matematikai és Fizikai Lapok 38:116–119，1931）
  - **核心貢獻**：König 定理的原始論文，原本用矩陣語言陳述：0-1 矩陣中能蓋住所有 1 的最少行列數，等於兩兩不同行不同列的 1 的最大個數。
  - **讀哪裡**：英譯全文很短，重點看主定理如何從矩陣翻成二分圖。
  - **和本章的關聯**：理解「矩陣的行列覆蓋 = 二分圖點覆蓋」之後，Ch 24 的匈牙利演算法與 Ch 26 的全單模矩陣就接得起來。

### 書籍

- **《Introduction to Graph Theory》** — Douglas B. West（Prentice Hall，第 2 版，2001）
  - **讀哪幾章**：Section 3.1（Matchings and Covers）有 Hall、König、Gallai 恆等式，習題量大；Section 3.2 的演算法部分涵蓋增廣路演算法與加權版本。
  - **前提知識**：本章即可；書比本章多了很多變形題，適合練證明。
- **《Matching Theory》** — László Lovász、Michael D. Plummer（North-Holland 1986；AMS Chelsea 修訂重印 2009）
  - **這本書的定位**：匹配理論的百科全書。第 1 章「Matchings in bipartite graphs」涵蓋 König、Hall、Frobenius、匈牙利方法與 deficiency；第 2 章「Flow theory」從 max-flow min-cut 推回匹配定理，正好對應本章「從 Ch 20 推 König」那段。
  - **讀哪幾章**：第 1 章；第 3 章之後是一般圖，留到 Ch 23 之後再讀。

### 官方文件

- **[NetworkX bipartite matching 模組](https://networkx.org/documentation/stable/reference/algorithms/bipartite.html#module-networkx.algorithms.bipartite.matching)**
  - **讀哪裡**：`hopcroft_karp_matching`、`eppstein_matching`、`to_vertex_cover` 三個函式頁；`to_vertex_cover` 註明它實作 König 定理的構造，並要求輸入最大匹配。
  - **注意**：文件要求呼叫者提供正確的 `top_nodes`，函式本身不檢查——對照踩雷第 2 條。

二分圖的增廣路好找，是因為交錯路的左右層永遠不會撞在一起。只要圖裡有奇圈，同一個點可能同時在「偶數層」和「奇數層」，這個好性質就瓦解了。下一章看 Edmonds 怎麼把奇圈縮成一個點來處理它。

→ [Ch 23 一般圖匹配：Tutte 定理與 Blossom](./23-general-matching-blossom.md)
