# Ch 12 — Menger 定理

> **目標**：完整證明 Menger 定理的集合版本（Göring 2000 的邊歸納證明），從它推出點版本、邊版本與全域版本（Whitney 的 k-連通刻畫）；在所有 6 點以下的圖上用暴力最小分隔集 + NetworkX 交出的路徑雙向驗證；看到「貪婪地取最短路徑」為什麼會失敗；最後預告 Ch 20 用最大流重新證明它的方法。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

Ch 11 講了「刪掉一個點就會斷」的割點。現在問一般版本：兩台機器 `s`、`t` 之間，**最少**要拿掉幾台中繼機器才能把它們隔開？以及，`s` 到 `t` **最多**能同時拉幾條不共用中繼機器的路線？

第一個問題是「攻擊者／故障」的觀點：要破壞連線，最少要打掉幾個點。第二個是「設計者」的觀點：我能保證幾條獨立的備援路徑。1927 年 Menger 證明了這兩個數**永遠相等**。這是一個「最小 = 最大」的對偶定理（min-max theorem），它的家族成員遍布本課後半：max-flow min-cut（Ch 20）、König 的匹配定理（Ch 22）、LP 對偶（Ch 26）。

這種定理的工程價值在於**證書（certificate）**：如果你找到 `k` 條不相交路徑，再找到一個大小 `k` 的分隔集，兩者互相證明對方是最佳的。你不必相信演算法，檢查這兩個物件就夠了。

## 先建立直覺

```text
        a ─── b
       ╱       ╲              s 到 t 有兩條內部不相交路徑：s-a-b-t、s-c-d-t
      s         t             拿掉 {a, d}（或 {b, c}、{a, c}…）就隔開了
       ╲       ╱              兩條路徑、兩個點：每條路徑至少要被打掉一個點，
        c ─── d               所以「最少要打幾個」≥「最多有幾條」
```

「最少要刪的點數 ≥ 最多的不相交路徑數」這個方向是容易的鴿籠論證：每條路徑都必須包含分隔集的某個點，而路徑彼此不共用內部點，所以分隔集至少要有一點對應一條路徑。

困難的是另一個方向：如果最少要刪 `k` 個點，為什麼一定**找得到** `k` 條不相交路徑？會不會路徑們互相糾纏，使得每條路徑都擋住別人？下面的「貪婪失敗」例子會顯示這種糾纏是真的存在的，而 Menger 定理說：糾纏總是可以被解開。

> 本章用到 Ch 11 的 `κ`、`λ` 定義與 Whitney 不等式。如果不熟，先回看 [Ch 11 — 連通度：割點、橋與 Tarjan](./11-connectivity-tarjan.md) 的「Whitney 不等式」一節。

## 定義：集合版本

為了讓歸納證明順利進行，我們證明一個更一般的版本，這也是 Diestel 的寫法。

設 `G = (V, E)` 是有限無向圖，`A, B ⊆ V`。

- **A–B 路徑**：路徑 `P = x0 x1 … xk`，滿足 `V(P) ∩ A = {x0}`、`V(P) ∩ B = {xk}`。也就是說它從 `A` 出發，第一次碰到 `B` 就結束，中途不再碰到 `A`。`k = 0` 是允許的：`A ∩ B` 裡的單一點本身就是一條 A–B 路徑。
- 一組 A–B 路徑**不相交**：兩兩沒有共同的點（包含端點）。
- **A–B 分隔集（separator）**：`S ⊆ V`，使得每條 A–B 路徑都至少含 `S` 的一個點。`S` 可以包含 `A` 或 `B` 的點；`A` 和 `B` 本身都是 A–B 分隔集。

**定理（Menger 1927，集合版本）**：A–B 分隔集的最小大小，等於不相交 A–B 路徑的最大條數。

**容易的方向（最大 ≤ 最小）**：任一 A–B 分隔集 `S` 必須和每條路徑相交；路徑兩兩不相交，所以各自用掉 `S` 中不同的點，`|S| ≥` 路徑條數。

## 完整證明：Göring 的邊歸納

令 `k` = 最小 A–B 分隔集的大小。要證：`G` 有 `k` 條不相交的 A–B 路徑。對邊數 `|E|` 做歸納（對所有 `A`、`B` 同時成立）。

**基礎**：`G` 沒有邊。此時 A–B 路徑只有 `A ∩ B` 裡的單點，所以 `A ∩ B` 是分隔集，且任何分隔集都必須包含 `A ∩ B` 的每一點。`k = |A ∩ B|`，這 `k` 個單點就是 `k` 條不相交路徑。

**歸納步驟**：設 `G` 有邊 `e = xy`。若 `G − e` 已經有 `k` 條不相交 A–B 路徑，它們也是 `G` 的路徑，完成。

否則由歸納假設，`G − e` 的最小 A–B 分隔集比 `k` 小：存在 `S`，`|S| ≤ k − 1`，在 `G − e` 中分隔 `A` 與 `B`。

**第一步：`|S| = k − 1`，且 `x`、`y` 分居兩側。** 在 `G` 中避開 `S` 的 A–B 路徑一定用到 `e`（否則它在 `G − e` 中避開了 `S`），因而同時經過 `x` 與 `y`。所以 `S ∪ {x}` 是 `G` 的 A–B 分隔集，`k ≤ |S| + 1`，得 `|S| = k − 1`。又因為 `|S| < k`，`S` 在 `G` 中**不是**分隔集，所以確實存在這樣一條用到 `e` 的路徑。

在 `G − e − S` 中說一個點「在 A 側」若它能從 `A` 走到，「在 B 側」若它能走到 `B`。沒有點同時在兩側（否則 `G − e` 中有避開 `S` 的 A–B 路徑）。那條用到 `e` 的路徑說明 `x`、`y` 一個在 A 側、一個在 B 側；**重新命名使 `x` 在 A 側、`y` 在 B 側**（`x`、`y` 都不在 `S` 中，因為路徑避開了 `S`）。

令 `X = S ∪ {x}`、`Y = S ∪ {y}`，兩者大小都是 `k`。

**第二步：`G − e` 中每個 A–X 分隔集，都是 `G` 的 A–B 分隔集（因而大小 `≥ k`）。** 設 `T` 是 `G − e` 的 A–X 分隔集，反設 `G` 中有避開 `T` 的 A–B 路徑 `P`。我們從 `P` 找出一條避開 `T`、在 `G − e` 裡的 A–X 路徑，得到矛盾：

- 若 `P` 沒用到 `e`：`P` 在 `G − e` 中，必定經過 `S ⊆ X`。取 `P` 從起點到第一個 `X` 點的那一段，它是 `G − e` 中避開 `T` 的 A–X 路徑。
- 若 `P` 用到 `e` 且先經過 `x` 再經過 `y`：`P` 從起點到 `x` 這段沒用到 `e`，終點 `x ∈ X`；取它到第一個 `X` 點為止的前段，同上。
- 若 `P` 先經過 `y` 再經過 `x`：`P` 從起點到 `y` 這段在 `G − e` 中。若它碰到 `S`，取到第一個 `S` 點的前段即可。若沒碰到 `S`，則 `y` 在 `G − e − S` 中能從 `A` 走到，`y` 在 A 側——但 `y` 在 B 側，矛盾。

**同理**：`G − e` 中每個 Y–B 分隔集都是 `G` 的 A–B 分隔集。（把上面論證中 `A` 與 `B`、`x` 與 `y` 的角色對調。）

**第三步：套歸納假設。** `G − e` 的邊比 `G` 少。在 `G − e` 中，最小 A–X 分隔集的大小 `≥ k`（第二步），又 `X` 本身就是 A–X 分隔集、大小 `k`，所以恰好是 `k`。由歸納假設，`G − e` 有 `k` 條不相交 A–X 路徑 `P1, …, Pk`；同理有 `k` 條不相交 Y–B 路徑 `Q1, …, Qk`。`k` 條路徑結束在 `k` 個不同的 `X` 點上，所以 `X` 的每個點恰是一條 `Pi` 的終點；`Y` 的每個點恰是一條 `Qj` 的起點。

**第四步：`Pi` 與 `Qj` 只在 `S` 相交。** 設某個點 `v ∉ S` 同時在 `Pi` 和 `Qj` 上。`Pi` 除了終點之外不碰 `X ⊇ S`，`Qj` 除了起點之外不碰 `Y ⊇ S`，所以「`Pi` 從起點到 `v`」接上「`Qj` 從 `v` 到終點」是 `G − e − S` 中一條從 `A` 到 `B` 的走訪，違反 `S` 在 `G − e` 中分隔 `A`、`B`。（`v = x` 或 `v = y` 的情況也包含在內：例如 `v = x` 在 `Qj` 上，則 `x` 能走到 `B`，`x` 同時在兩側，矛盾。）

**第五步：拼起來。** 對每個 `s ∈ S`，把以 `s` 結尾的 `Pi` 和以 `s` 開頭的 `Qj` 接起來；再把以 `x` 結尾的 `Pi`、邊 `e`、以 `y` 開頭的 `Qj` 接起來。由第四步，這 `k` 條都是路徑（不重複點），而且兩兩不相交：不同的 `Pi` 互不相交、不同的 `Qj` 互不相交，`Pi` 與不配對的 `Qj` 只可能交在 `S`，但 `Pi ∩ S` 只有它自己的終點。它們是 `G` 中 `k` 條不相交的 A–B 路徑。∎

```text
          A 側                 S                B 側
   A ══ P1 ═══════════════▶  s1  ═══ Q1 ═════════▶ B
   A ══ P2 ═══════════════▶  s2  ═══ Q2 ═════════▶ B
   A ══ Pk ══════▶ x ─── e ─── y ═══ Qk ═════════▶ B
```

## 推論：點版本、邊版本、全域版本

**點版本（local, vertex）**：`s`、`t` 是**不相鄰**的相異點，則「最少要刪幾個點（不含 `s, t`）才能隔開 `s` 與 `t`」=「最多有幾條內部不相交的 s–t 路徑」。

**證明**：在 `G' = G − s − t` 上取 `A = N(s)`、`B = N(t)` 套集合版本。`G` 中刪掉 `S ⊆ V ∖ {s, t}` 能隔開 `s, t` ⇔ `S` 是 `G'` 的 A–B 分隔集；`G'` 中的不相交 A–B 路徑 `P`，前面接 `s`、後面接 `t`，就是內部不相交的 s–t 路徑，反之亦然（取 s–t 路徑扣掉兩端點，它的第一個點在 `N(s)`；截到第一個碰到 `N(t)` 的點，仍然是 A–B 路徑，再接回 `t`）。不相鄰的條件保證不會有「`s` 直接連 `t`」這種無法被任何點擋住的路徑。∎

**邊版本（local, edge）**：對任意相異 `s, t`，「最少要刪幾條邊才能隔開 `s` 與 `t`」=「最多有幾條邊不相交的 s–t 路徑」。

**證明**：在**線圖（line graph）** `L(G)` 上套集合版本：`L(G)` 的點是 `G` 的邊，兩條邊共用端點就相鄰。取 `A` = 碰到 `s` 的邊、`B` = 碰到 `t` 的邊。`G` 中邊不相交的 s–t 路徑對應 `L(G)` 中不相交的 A–B 路徑（路徑只經過 `s` 一次，所以只有第一條邊碰到 `s`），`G` 的 s–t 邊割對應 `L(G)` 的 A–B 分隔集。∎

**全域版本（Whitney 1932）**：`n ≥ k + 1` 的圖是 k-連通 ⇔ 任兩點之間都有 `k` 條內部不相交的路徑。

**證明**：（⇐）若刪掉少於 `k` 個點使圖不連通，取被分開的兩點，它們之間的 `k` 條內部不相交路徑至少有一條沒被刪到，矛盾；`n ≥ k + 1` 保證刪不到「只剩一點」。（⇒）不相鄰的 `u, v` 直接由點版本得到。相鄰的 `u, v`：我們證明 `G − uv` 中 `u`、`v` 之間至少有 `k − 1` 條內部不相交路徑，再加上邊 `uv` 本身就是 `k` 條。反設 `G − uv` 中有大小 `≤ k − 2` 的點集 `S` 隔開 `u`、`v`。`n ≥ k + 1`，所以有點 `w ∉ S ∪ {u, v}`。`w` 不能同時和 `u`、`v` 在 `G − uv − S` 的同一分量裡；不妨設 `w` 不和 `u` 同分量。那麼 `S ∪ {v}`（大小 `≤ k − 1`）在 `G` 中隔開 `u` 與 `w`：在 `G − S − v` 中從 `u` 出發的路徑都不經過 `v`，所以也不經過邊 `uv`，它們是 `G − uv − S` 的路徑，到不了 `w`。這和 `G` 是 k-連通矛盾。∎

## 窮舉驗證：雙向證書

下面在所有 2–6 點的圖、所有點對上檢查兩個版本。關鍵設計：**最小分隔集用暴力列舉**（不依賴任何 flow 理論），**路徑由 NetworkX 交出後逐條驗證**。因為「路徑數 ≤ 分隔集大小」是已證明的容易方向，只要兩者相等，這個點對上的 Menger 定理就被一組可檢查的證書證明了。

```python
from itertools import combinations
import networkx as nx


def reachable(adj, s, t, removed_nodes=(), removed_edges=()):
    bad_e = {frozenset(e) for e in removed_edges}
    seen, stack = {s}, [s]
    while stack:
        u = stack.pop()
        for w in adj[u]:
            if w not in seen and w not in removed_nodes and frozenset((u, w)) not in bad_e:
                seen.add(w)
                stack.append(w)
    return t in seen


def min_vertex_sep_brute(adj, s, t):
    others = [v for v in adj if v not in (s, t)]
    for k in range(len(others) + 1):
        for S in combinations(others, k):
            if not reachable(adj, s, t, removed_nodes=set(S)):
                return set(S)


def min_edge_cut_brute(adj, s, t):
    E = sorted({tuple(sorted((u, v))) for u in adj for v in adj[u]})
    for k in range(len(E) + 1):
        for F in combinations(E, k):
            if not reachable(adj, s, t, removed_edges=F):
                return set(F)


def check_paths(adj, s, t, paths, vertex_disjoint):
    used_v, used_e = set(), set()
    for P in paths:
        assert P[0] == s and P[-1] == t and len(set(P)) == len(P)
        assert all(P[i + 1] in adj[P[i]] for i in range(len(P) - 1))
        inner = set(P[1:-1])
        es = {frozenset(P[i:i + 2]) for i in range(len(P) - 1)}
        if vertex_disjoint:
            assert not inner & used_v
        assert not es & used_e
        used_v |= inner
        used_e |= es


pairs_v = pairs_e = 0
for G in nx.graph_atlas_g():
    if not 2 <= G.number_of_nodes() <= 6:
        continue
    adj = {v: set(G[v]) for v in G}
    for s, t in combinations(sorted(G), 2):
        # 邊版本：任意兩點（相鄰也可以）
        F = min_edge_cut_brute(adj, s, t)
        Pe = list(nx.edge_disjoint_paths(G, s, t)) if F else []
        check_paths(adj, s, t, Pe, vertex_disjoint=False)
        assert len(Pe) == len(F)
        pairs_e += 1
        # 點版本：不相鄰的兩點
        if t in adj[s]:
            continue
        S = min_vertex_sep_brute(adj, s, t)
        Pv = list(nx.node_disjoint_paths(G, s, t)) if S else []
        check_paths(adj, s, t, Pv, vertex_disjoint=True)
        assert len(Pv) == len(S)
        pairs_v += 1
print(f"所有 2–6 點的圖。點版本：{pairs_v} 組不相鄰點對，最小分隔集大小 = 內部不相交路徑數")
print(f"邊版本：{pairs_e} 組點對，最小邊割大小 = 邊不相交路徑數")
```

```text
所有 2–6 點的圖。點版本：1380 組不相鄰點對，最小分隔集大小 = 內部不相交路徑數
邊版本：2760 組點對，最小邊割大小 = 邊不相交路徑數
```

`if F else []` 處理 `s`、`t` 本來就不連通的情況：最小分隔集是空集合，路徑數是 0，NetworkX 在這時會丟 `NetworkXNoPath` 而不是回傳空列表。把範圍擴大到 7 點也會通過，但暴力邊割要列舉最多 `2^21` 個子集，在我們的機器上要跑將近一分鐘。

全域版本也驗一次：

```python
import networkx as nx

# 全域版本（Whitney）：κ(G) = 所有點對之間內部不相交路徑數的最小值（相鄰點對也算，直連邊算一條）
for G in nx.graph_atlas_g():
    n = G.number_of_nodes()
    if n < 2 or not nx.is_connected(G):
        continue
    k = nx.node_connectivity(G)
    local = min(len(list(nx.node_disjoint_paths(G, s, t)))
                for s in G for t in G if s < t)
    assert k == local, (sorted(G.edges()), k, local)
print("所有 2–7 點連通圖：κ(G) = min 點對間的不相交路徑數")
```

```text
所有 2–7 點連通圖：κ(G) = min 點對間的不相交路徑數
```

## 故意弄壞：貪婪取最短路徑

最自然的演算法是「找一條最短路徑，刪掉它的內部點，再找下一條」。它找到的路徑一定不相交，但**不一定最多**：

```python
import networkx as nx

#   s ─ a ─ b ─ c ─ t        兩條不相交路徑：s-a-b-c-t、s-d-e-f-t
#   │    ╲          │        加一條「捷徑」a–f
#   └─ d ─ e ─ f ───┘
G = nx.Graph([("s", "a"), ("a", "b"), ("b", "c"), ("c", "t"),
              ("s", "d"), ("d", "e"), ("e", "f"), ("f", "t"), ("a", "f")])


def greedy_disjoint_paths(G, s, t):
    """錯誤做法：反覆取最短路徑，刪掉它的內部點。"""
    H, paths = G.copy(), []
    while nx.has_path(H, s, t):
        P = nx.shortest_path(H, s, t)
        paths.append(P)
        if len(P) == 2:
            H.remove_edge(s, t)
        H.remove_nodes_from(P[1:-1])
    return paths


print("貪婪最短路徑:", greedy_disjoint_paths(G, "s", "t"))
print("最大不相交  :", sorted(nx.node_disjoint_paths(G, "s", "t")))
print("最小分隔集  :", sorted(nx.minimum_node_cut(G, "s", "t")))
```

```text
貪婪最短路徑: [['s', 'a', 'f', 't']]
最大不相交  : [['s', 'a', 'b', 'c', 't'], ['s', 'd', 'e', 'f', 't']]
最小分隔集  : ['c', 'f']
```

貪婪先拿走最短的 `s-a-f-t`，一次用掉了兩條真正路徑各自需要的 `a` 和 `f`，結果只找到 1 條。正確答案是 2，而分隔集 `{c, f}` 證明不可能有第 3 條。

這個失敗正是 Göring 證明第五步的影子：最佳解裡的路徑會「穿過」分隔集的不同點，捷徑 `a–f` 讓一條路徑同時佔了兩條路徑的資源。修正的方法不是更聰明的貪婪，而是允許**反悔**：Ch 20 的增廣路徑（augmenting path）可以「撤銷」先前選的一段路徑，把 `a–f` 這段退回去重新分配。

## 預告：Ch 20 的 flow 證法

Göring 的證明是純組合的。另一條路是把 Menger 變成最大流問題：

```text
點版本：每個點 v（除了 s, t）拆成 v_in → v_out，容量 1
        原圖每條邊 uv 變成 u_out → v_in 和 v_out → u_in，容量 ∞（或 1）

  s ──▶ a_in ─1─▶ a_out ──▶ b_in ─1─▶ b_out ──▶ t
              ↑ 點容量 1 = 「每個點最多被一條路徑用」
```

在這個網路上，整數最大流 = 不相交路徑數（流量整數性），最小割 = 最小分隔集（只有容量 1 的「點邊」會出現在有限割裡）。於是 Menger 定理變成 **max-flow min-cut 定理**的一個特例；而 max-flow min-cut 本身是由 Ford–Fulkerson 的增廣路徑論證證明的。`nx.node_disjoint_paths` 與 `nx.minimum_node_cut` 內部做的就是這件事。Ch 20 會證明 max-flow min-cut，並回頭用它在三行內重新推出本章的點版本與邊版本。

兩種證明的取捨：flow 證法是**構造性**的（直接給出多項式時間演算法），但需要先建立整個 flow 理論；Göring 的證明短、自足，但把它直接翻成演算法會是指數時間（每一步都要找最小分隔集）。

## 對比與取捨

| Menger 的證明 | 想法 | 長度 | 能直接變成有效率的演算法嗎 |
| --- | --- | --- | --- |
| Göring（本章） | 對邊數歸納，刪一條邊、拼兩組路徑 | 約一頁 | 否 |
| 對點數歸納 + 縮邊（Diestel 第一證明） | 縮掉一條邊，討論分隔集是否含縮點 | 約一頁 | 否 |
| 增廣路徑（Diestel 第二證明、Ch 20） | 交替路徑反悔 | 較長 | 是：`O(k·m)`（`k` 條路徑，每條一次 BFS） |
| LP 對偶 + 全單模（Ch 26） | 路徑 LP 的對偶是分隔 LP | 需要 LP 理論 | 是 |

| 計算問題 | 方法 | 時間 |
| --- | --- | --- |
| `κ(s, t)`、`λ(s, t)` | 拆點 + 單位容量最大流 | `O(k·m)` 或 `O(√n · m)`（Even–Tarjan 型） |
| 全域 `λ(G)` | 固定 `s`，對所有 `t` 算 `λ(s, t)`；或 Stoer–Wagner | `O(n·m·…)`；Stoer–Wagner `O(nm + n² log n)` |
| 全域 `κ(G)` | 對少數挑選過的點對算 `κ(s, t)`（Even 1975 的技巧） | 多項式 |

## 踩雷集錦

1. **以為容易的方向就是全部**：「找到 `k` 條路徑、所以至少要刪 `k` 個點」是容易的；「最少要刪 `k` 個、所以找得到 `k` 條」才是定理的內容。兩者合起來才是「最小 = 最大」。
2. **點版本忘記「不相鄰」**：`s ~ t` 時，直連邊無法被任何點擋住，「最少刪幾個點」沒有定義（或說無限大）。全域版本對相鄰點對要另外處理，上面證明的 `G − uv` 技巧就是為此而生。NetworkX 3.7 的 `minimum_node_cut(G, s, t)` 在 `s ~ t` 時不報錯，而是回傳空集合（我們實測 `nx.minimum_node_cut(nx.path_graph(3), 0, 1)` 得到 `set()`）——空集合當然隔不開兩個相鄰點，呼叫前要自己先檢查相鄰。
3. **混淆「內部不相交」與「邊不相交」**：兩條共用中間點但不共用邊的路徑是邊不相交、但不是內部不相交。領結圖中兩側的點之間有 2 條邊不相交路徑，卻只有 1 條內部不相交路徑（都要經過中間點）。對應 Ch 11 的 `κ < λ`。
4. **貪婪最短路徑**：如上所示會少算。這個錯誤在網路可靠度評估裡很常見——「我找到一條備援路徑，刪掉它，找不到第二條，所以只有一條」。
5. **把 Menger 套到有向圖時忘了方向**：Menger 對有向圖同樣成立（路徑沿著箭頭方向），但 s→t 和 t→s 的答案可以不同。Ch 13、Ch 14 處理有向圖時要記得這點。

## 進階：再往深一層

- **Fan lemma 與 Expansion lemma**：k-連通圖中，一個點到任何 `k` 個點的集合，有 `k` 條只在起點相交的路徑。這是證明「k-連通圖中任 `k` 個點落在同一個圈上」（Dirac）的工具。
- **Mader 定理與 Lovász–Cherkassky**：Menger 給出「`s` 到 `t`」的不相交路徑；「一組終端點兩兩之間」的版本（`T`-paths）由 Mader 處理，是更深的結果。
- **網路可靠度與 SRE**：「兩個資料中心之間有 `k` 條點不相交路徑」等價於「任意 `k − 1` 台中繼設備故障都不會斷線」。這是 Menger 最直接的工程翻譯，也是分散式系統做 failure domain 分析的基礎（見 [distributed_systems 課程](../../systems/distributed_systems/README.md) 對故障模型的討論）。
- **攻擊圖**：Ch 39 把 AD 權限圖的「最少要修補幾台主機才能切斷所有攻擊路徑」建模成有向圖的最小點割，Menger 保證它等於「攻擊者最多有幾條互不相依的路徑」，而且兩者可以同時算出來當作報告裡的證據。

## 動手練習

1. 寫一個 `verify_certificate(G, s, t, paths, cut)`：檢查路徑兩兩內部不相交、分隔集真的隔開 `s, t`、而且 `len(paths) == len(cut)`。用它驗證 `nx.node_disjoint_paths` 與 `nx.minimum_node_cut` 在 100 張 `G(30, 0.15)` 隨機圖（seed 0–99）上的輸出。
2. 把邊版本的「線圖證明」寫成程式：用 `nx.line_graph(G)` 建 `L(G)`，取 `A`、`B`，用 `nx.node_disjoint_paths` 的集合版本思路（加超級源點、超級匯點）算出邊不相交路徑數，和 `nx.edge_connectivity(G, s, t)` 對拍。
3. 在貪婪失敗的例子上，手動執行 Göring 證明：選一條邊 `e`，找出 `G − e` 的分隔集 `S`，確定 `x`、`y` 各在哪一側，再看兩組路徑怎麼拼起來。
4. 證明（或用小圖窮舉找反例）：「`G` 是 2-連通 ⇔ 任兩點落在同一個圈上」。它是全域版本在 `k = 2` 的特例。

## 本章重點整理

- Menger 集合版本：最小 A–B 分隔集大小 = 最大不相交 A–B 路徑數。容易方向是鴿籠，困難方向用 Göring 的邊歸納：刪一條邊、得到大小 `k − 1` 的分隔集 `S`、分別找 `A → S∪{x}` 與 `S∪{y} → B` 的路徑再拼接。
- 點版本取 `A = N(s)`、`B = N(t)`；邊版本走線圖；全域版本（Whitney）處理相鄰點對時用 `G − uv` 的技巧。
- 路徑集合 + 等大小的分隔集是雙向證書，可以獨立驗證。
- 貪婪最短路徑會失敗；正確的演算法需要「反悔」，也就是 Ch 20 的增廣路徑。

## 自我檢核

- [ ] 不看筆記，說出 Göring 證明的五個步驟各自在做什麼，以及第一步為什麼要把 `x`、`y` 重新命名。
- [ ] 能解釋為什麼證明要用「集合版本」而不是直接證「s–t 點版本」。
- [ ] 如果主管問「我們兩個機房之間到底有幾條真正獨立的路徑」，你會交出哪兩樣東西，讓他不需要相信你的程式？
- [ ] 能畫出一個貪婪最短路徑少算的小圖，並說明 Ch 20 的增廣路徑如何修正它。
- [ ] 能說明點版本為什麼要求 `s`、`t` 不相鄰，以及全域版本怎麼繞過這個限制。

## 延伸閱讀

### 論文

- **[Zur allgemeinen Kurventheorie](https://doi.org/10.4064/fm-10-1-96-115)** — Karl Menger，Fundamenta Mathematicae 10:96–115（1927）
  - **核心貢獻**：定理的原始出處。Menger 是在研究拓撲學的「曲線理論」時得到這個組合結果的，圖論只是副產品；一般的說法是原始證明有缺口，König 在 1930 年代初給出了完整的證明。
  - **讀哪裡**：給歷史興趣的讀者；德文、拓撲語言，不建議當學習材料。
  - **前提**：無。
- **[Short proof of Menger's Theorem](https://doi.org/10.1016/S0012-365X(00)00088-1)** — Frank Göring，Discrete Mathematics 219(1–3):295–296（2000）
  - **核心貢獻**：本章證明的出處，全文兩頁。
  - **讀哪裡**：整篇。對照本章的五個步驟，看原文如何用更精簡的符號寫出第二步與第四步。
  - **前提**：本章的集合版本定義。

### 書籍

- **《Graph Theory》** — Reinhard Diestel（Springer GTM 173，第 6 版，2025）
  - **這本書的定位**：Menger 定理的「標準參考」：§3.3「Menger's theorem」給了三個證明（縮邊歸納、增廣路徑、Göring 的邊歸納），本章採用的是第三個，記號也跟 Diestel 一致。
  - **讀哪幾章**：§3.3 全部；接著讀同一章的 Mader 定理，體會推廣有多難。官網有正文的免費線上預覽。
  - **前提**：本章；Ch 11 的連通度定義。
- **《Introduction to Graph Theory》** — Douglas B. West（第 2 版，2001）
  - **讀哪幾章**：§4.2「k-connected Graphs」（Whitney 的刻畫、Expansion lemma、Fan lemma）與 §4.3「Network Flow Problems」（Menger 的 flow 證法，對應本課 Ch 20）。
  - **前提**：本章。

### 官方文件

- **[NetworkX connectivity 模組](https://networkx.org/documentation/stable/reference/algorithms/connectivity.html)**
  - **讀哪裡**：`node_disjoint_paths`、`minimum_node_cut`、`local_node_connectivity` 的說明，以及 `build_auxiliary_node_connectivity`——它就是本章「預告」一節的拆點構造。
  - **注意**：這些函式都接受 `flow_func` 參數，可以換成不同的最大流演算法；Ch 20–21 會實作其中幾個。

到這裡，我們處理的都是無向圖。下一章給邊加上方向：連通的概念分裂成「強連通」與「弱連通」，DFS 開始出現橫向邊，而沒有圈的有向圖（DAG）成為排程、編譯、依賴解析的共同骨架。

→ [Ch 13 有向圖：SCC 與 DAG](./13-directed-graphs-scc-dag.md)
