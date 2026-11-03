# Ch 23 — 一般圖匹配：Tutte 定理與 Blossom

> **目標**：看懂奇圈為什麼讓二分圖的增廣路搜尋失效，完整證明 Berge、Tutte 與 Tutte–Berge 公式，證明「縮花」不改變增廣路的存在性，並自己寫出 Edmonds Blossom 演算法、與 NetworkX 對拍。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

[Ch 22](./22-bipartite-matching.md) 的每個演算法都依賴「圖是二分的」。真實的配對問題常常不是：

- **兩人一組**：把 `n` 個工程師兩兩配成 pair programming 組，邊是「兩人願意合作」。沒有左邊右邊，任何兩人都可能配。
- **中國郵差問題**：要走遍每條邊再回到起點，最少要重複走哪些邊？答案是把奇度點兩兩配對、讓配對間最短路總長最小——這是一般圖的最小權完美匹配。回連 [Ch 8 — Euler 與 Hamilton](./08-euler-hamilton.md) 的 Euler 迴路條件。
- **Christofides 演算法**：metric TSP 的 1.5 倍近似，第二步同樣是在奇度點上找最小權完美匹配。

歷史順序很值得記住。存在性的定理先出現：Julius Petersen 在 1891 年證明無橋三正則圖有完美匹配；W. T. Tutte 在 1947 年給出一般圖有完美匹配的充要條件；Claude Berge 在 1957 年證明「最大 ⇔ 沒有增廣路」對一般圖也成立。但這些定理都沒有給出有效率的演算法——Tutte 條件要檢查 `2^n` 個子集。Jack Edmonds 在 1961 年想出縮花，1965 年發表〈Paths, Trees, and Flowers〉。這篇論文的另一個遺產是觀念上的：Edmonds 主張用「步數被輸入大小的某個多項式限制住」來定義「好的演算法」，這是今天 P 這個複雜度類別的直覺來源之一（Ch 34 會回到這裡）。

## 先建立直覺

二分圖裡，從自由點 `r` 出發，每個點和 `r` 的距離奇偶性是固定的：左側一律偶數層、右側一律奇數層。所以交錯搜尋時「一個點只要標一次 even 或 odd」是對的。

奇圈打破了這件事：

```text
            r（自由，even）
            │
            1（odd）
            ‖               ‖ = 匹配邊
            2（even）  ← 花的底座 base
           ╱ ╲
     (odd) 3   6 (odd)      從 2 往左走：3 是 odd、4 是 even
          ‖     ‖           從 2 往右走：6 是 odd、5 是 even
    (even) 4───5 (even)
           │
           ...              但繞另一邊：2-6=5-4=3，3 在偶數位置！
           3 ── 7（自由）    7 只能從 3 往外走，3 卻被標成 odd
```

`3` 從左邊看是 odd，從右邊繞過來卻是 even。二分圖演算法給每個點一個固定標籤，於是看不見「先繞花一圈、再從 3 走出去」的那條增廣路。Edmonds 的解法：既然這個奇圈裡每個點都能用某一側到達成 even，那就**把整個奇圈縮成一個點**，它整體是 even，可以從任何一個成員往外長。

## Berge 定理

**Berge 定理（1957）**：圖 `G` 的匹配 `M` 是最大匹配，若且唯若 `G` 沒有 `M`-增廣路。

**證明**：若有增廣路 `P`，`M Δ P` 是更大的匹配，`M` 不是最大。反過來，若 `M` 不是最大，取更大的匹配 `M*`；[Ch 22 的對稱差引理](./22-bipartite-matching.md)（它的證明沒有用到二分性）說 `M Δ M*` 中至少有 `|M*| − |M| ≥ 1` 條 `M`-增廣路。∎

這個定理說「要找最大匹配，就去找增廣路」，在一般圖上照樣成立。問題完全出在**怎麼找**。

## 為什麼奇圈讓增廣路搜尋失效

把 Ch 22 的交錯 BFS 原封不動搬到上圖，並且用暴力列舉所有簡單交錯路當對照：

```python
from collections import deque

# 一朵 5 點的花（奇圈 2-3-4-5-6-2），= 是匹配邊，0 和 7 是自由點
#
#   0 --- 1 === 2 --- 3 === 4
#               |     |     |
#               |     7     |
#               6 === 5 ----+
#
# 增廣路：0-1=2-6=5-4=3-7（繞花的另一側走）
edges = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 2), (3, 7)]
M = {(1, 2), (3, 4), (5, 6)}
mate = {}
for u, v in M:
    mate[u], mate[v] = v, u


def build_adj(order_seed):
    adj = {v: [] for v in range(8)}
    for u, v in edges:
        adj[u].append(v)
        adj[v].append(u)
    for v in adj:                      # 用不同的鄰居順序重跑，看結果會不會變
        adj[v].sort(reverse=order_seed % 2 == 1)
    return adj


def naive_search(adj, mate, root):
    """把二分圖的交錯 BFS 原封不動搬到一般圖：每個點只標一次 even/odd。"""
    label = {root: "even"}
    parent = {root: None}
    q = deque([root])
    while q:
        v = q.popleft()                      # 佇列裡的點都是 even
        for w in adj[v]:
            if w in label:                   # 看過就跳過——在二分圖裡這樣是對的
                continue
            label[w], parent[w] = "odd", v
            if w not in mate:                # 自由點：找到增廣路
                path = [w]
                while path[-1] != root:
                    path.append(parent[path[-1]])
                return path[::-1]
            m = mate[w]
            label[m], parent[m] = "even", w
            q.append(m)
    return None


def brute_augmenting(adj, mate, root):
    """暴力列舉所有從 root 出發的簡單交錯路。"""
    def dfs(path, need_matched):
        v = path[-1]
        for w in adj[v]:
            if w in path:
                continue
            is_m = mate.get(v) == w
            if is_m != need_matched:
                continue
            if not need_matched and w not in mate:
                return path + [w]
            r = dfs(path + [w], not need_matched)
            if r:
                return r
        return None
    return dfs([root], False)


for s in (0, 1):
    adj = build_adj(s)
    print(f"鄰居順序 {s}: 樸素搜尋 ->", naive_search(adj, mate, 0),
          " 暴力 ->", brute_augmenting(adj, mate, 0))
```

```text
鄰居順序 0: 樸素搜尋 -> None  暴力 -> [0, 1, 2, 6, 5, 4, 3, 7]
鄰居順序 1: 樸素搜尋 -> None  暴力 -> [0, 1, 2, 6, 5, 4, 3, 7]
```

不論鄰居順序，樸素搜尋都回報「沒有增廣路」，但 Berge 定理說這時 `M` 不是最大，而暴力找到了一條。失敗的機制：BFS 從 `2` 出發時同時把 `3` 和 `6` 標成 odd，之後 odd 點只能沿匹配邊走，`3` 永遠沒有機會以 even 身分往 `7` 走。

為什麼不改成「允許一個點被標兩次」？那樣搜尋會走出**不簡單**的路——同一個點進出兩次，翻轉時會把它配給兩個人。Edmonds 的縮花是一個精確的折衷：只在奇圈這個特定結構上「允許兩種身分」，而且證明了這樣做不會漏、也不會多。

## Tutte 定理

令 `odd(H)` 表示圖 `H` 中點數為奇數的連通分量個數。

**Tutte 定理（1947）**：`G` 有完美匹配，若且唯若對每個 `S ⊆ V(G)`，`odd(G − S) ≤ |S|`。

**必要性**：設 `M` 是完美匹配。`G − S` 的每個奇分量 `K` 自己配不完，至少有一個點的伴侶在 `K` 外；伴侶不在別的分量（分量之間沒有邊），所以在 `S`。不同奇分量的這些伴侶互不相同，於是 `odd(G − S) ≤ |S|`。

**充分性**（Lovász 的證明）：假設 `G` 滿足 Tutte 條件卻沒有完美匹配，推出矛盾。

1. **`n` 是偶數**：取 `S = ∅`，`odd(G) ≤ 0`，所以每個分量都是偶數個點。
2. **加邊保持 Tutte 條件**：加一條邊 `e` 時，對任意 `S`，`G − S` 的分量要嘛不變，要嘛有兩個合併成一個。合併兩個奇分量得偶分量（奇數個數減 2），合併奇與偶得奇（不變），合併兩個偶得偶（不變）。所以 `odd` 不會增加。
3. **取極大反例**：不斷往 `G` 加邊，只要加完仍然沒有完美匹配就加。完全圖 `K_n`（`n` 偶）有完美匹配，所以這個過程會在某個 `G` 停下：`G` 滿足 Tutte 條件、沒有完美匹配，但**加任何一條不存在的邊都會出現完美匹配**。以下都對這個 `G` 論證。
4. 令 `U` 是與其他所有點都相鄰的點集合。**主張：`G − U` 的每個連通分量都是完全圖。** 先承認這個主張，看它如何導出矛盾：
   - 由 Tutte 條件，`G − U` 的奇分量個數 `≤ |U|`。每個奇分量（完全圖）挑一個點，配給一個不同的 `U` 中的點（`U` 的點和所有點相鄰，所以這條邊存在）。
   - 每個分量剩下的點是偶數個，且是完全圖，兩兩配完。
   - `U` 中剩下的點兩兩相鄰；剩下的點數 = `n` 減掉已配的點數，`n` 是偶數、已配點數是偶數，所以剩下偶數個，兩兩配完。
   - 這是 `G` 的完美匹配，矛盾。
5. **證明主張**：假設 `G − U` 有某個分量不是完全圖。分量連通但不完全，取其中不相鄰的兩點，它們之間的最短路長度 ≥ 2，最短路的前三個點 `x, y, z` 滿足 `xy, yz ∈ E`、`xz ∉ E`。`y ∉ U`，所以存在某個點 `w` 與 `y` 不相鄰（`w ≠ y`）。由極大性，`G + xz` 有完美匹配 `M1`，`G + yw` 有完美匹配 `M2`。因為 `G` 本身沒有完美匹配，`xz ∈ M1`、`yw ∈ M2`。
   - 令 `F = M1 Δ M2`。每個點恰好碰一條 `M1` 邊、一條 `M2` 邊，所以 `F` 中每點度數 0 或 2，`F` 是一些交錯偶圈的聯集。`xz ∈ M1 − M2`（`M2 ⊆ G + yw` 不含 `xz`），所以 `xz ∈ F`；同理 `yw ∈ F`。令 `C` 是 `F` 中含 `xz` 的圈。
   - **情況一：`yw ∉ C`**。`M1 Δ C` 把 `C` 上的 `M1` 邊換成 `C` 上的 `M2` 邊。新的匹配不含 `xz`（它是 `C` 上的 `M1` 邊，被換掉了），不含 `yw`（`yw` 不在 `C`，也不在 `M1`）。它是 `G` 的完美匹配，矛盾。
   - **情況二：`yw ∈ C`**。從 `y` 出發沿 `C` 先走 `yw` 這條邊，一路走到第一次碰到 `x` 或 `z` 為止，這段路叫 `P`。因為 `x`、`y` 與 `z`、`y` 都相鄰，`x` 與 `z` 的角色對稱，不妨設先碰到 `z`。`P` 不含 `xz`（要走 `xz` 必須先到 `x` 或 `z`）。`P` 的第一條邊 `yw ∈ M2`；到達 `z` 的最後一條邊不是 `xz`，所以是 `z` 在 `C` 上的另一條邊，屬於 `M2`。`P` 在 `C` 上交錯，頭尾都是 `M2` 邊，所以 `P` 有奇數條邊。`P` 加上邊 `zy`（它在 `G` 裡）形成偶圈 `C'`，在 `C'` 上 `M2` 邊與「非 `M2` 邊」交錯。考慮 `M2 Δ C'`：拿掉 `P` 上的 `M2` 邊（包括 `yw`），換成 `P` 上的 `M1` 邊（都不是 `xz`，所以都在 `G` 裡）再加上 `yz`。`C'` 上每個點仍恰好被蓋一次，`C'` 外照舊用 `M2`。這是 `G` 的完美匹配，矛盾。
   - 兩種情況都矛盾，主張成立。∎

**Tutte–Berge 公式**：對任意圖，

```text
ν(G) = min over S ⊆ V 的 ( n + |S| − odd(G − S) ) / 2
```

**`≤` 方向**：對任意 `S`，每個奇分量至少有一個點沒被配到分量內部，只能配給 `S`，而 `S` 最多接住 `|S|` 個，所以至少 `odd(G − S) − |S|` 個點沒被配到，`ν ≤ (n − odd(G − S) + |S|) / 2`。

**`≥` 方向**（由 Tutte 定理推出）：令 `d = max over S 的 (odd(G − S) − |S|)`。取 `S = ∅` 知 `d ≥ 0`；又 `G − S` 的點數 `n − |S|` 與 `odd(G − S)` 同奇偶，所以 `d ≡ n (mod 2)`。造 `G'`：加入 `d` 個新點，彼此相連，也和 `G` 的所有點相連。檢查 `G'` 滿足 Tutte 條件：

- 若 `S'` 沒有包含全部新點，`G' − S'` 還有一個新點，它連到所有剩下的點，`G' − S'` 連通，`odd ≤ 1`。`S' ≠ ∅` 時 `1 ≤ |S'|`；`S' = ∅` 時 `G'` 有 `n + d` 個點（偶數），`odd = 0`。（`d = 0` 時沒有新點，`G' = G`，而 `d = 0` 正是 Tutte 條件本身。）
- 若 `S'` 包含全部新點，`S' = 新點 ∪ S`，`odd(G' − S') = odd(G − S) ≤ |S| + d = |S'|`。

所以 `G'` 有完美匹配，大小 `(n + d) / 2`。拿掉碰到新點的邊（最多 `d` 條），剩下的是 `G` 的匹配，大小至少 `(n + d)/2 − d = (n − d)/2`。這正是右式的最小值。∎

用窮舉驗證兩個定理：

```python
import itertools
import time

import networkx as nx


def odd_components(G, S):
    H = G.subgraph(set(G) - set(S))
    return sum(len(c) % 2 for c in nx.connected_components(H))


def tutte_berge(G):
    """回傳 (min over S 的 (n + |S| - odd(G-S)) / 2, 達到最小值的一個 S)。"""
    n = G.number_of_nodes()
    best = None
    for k in range(n + 1):
        for S in itertools.combinations(G.nodes(), k):
            val = (n + k - odd_components(G, S)) // 2
            if best is None or val < best[0]:
                best = (val, S)
    return best


# 1) 三個三角形掛在同一個中心點：10 個點，沒有完美匹配
G = nx.Graph()
for t in range(3):
    a, b, c = 3 * t + 1, 3 * t + 2, 3 * t + 3
    G.add_edges_from([(a, b), (b, c), (c, a), (0, a)])
nu = len(nx.max_weight_matching(G, maxcardinality=True))
bound, S = tutte_berge(G)
print(f"三角形花束：n={G.number_of_nodes()} ν={nu}  Tutte-Berge 上界={bound}  "
      f"證書 S={S}  odd(G-S)={odd_components(G, S)}")

# 2) 窮舉 n <= 6 的所有標號圖，驗證 Tutte-Berge 公式（Tutte 定理是它的特例）
t0 = time.perf_counter()
for n in range(1, 7):
    pairs = list(itertools.combinations(range(n), 2))
    count = perfect = 0
    for mask in range(1 << len(pairs)):
        G = nx.empty_graph(n)
        G.add_edges_from(p for i, p in enumerate(pairs) if mask >> i & 1)
        nu = len(nx.max_weight_matching(G, maxcardinality=True))
        assert nu == tutte_berge(G)[0]
        has_pm = 2 * nu == n
        tutte_ok = all(odd_components(G, S) <= len(S)
                       for k in range(n + 1)
                       for S in itertools.combinations(range(n), k))
        assert has_pm == tutte_ok
        count += 1
        perfect += has_pm
    print(f"n={n}: {count:>5} 張圖全部符合（其中 {perfect} 張有完美匹配）")
print(f"耗時 {time.perf_counter() - t0:.1f}s")
```

```text
三角形花束：n=10 ν=4  Tutte-Berge 上界=4  證書 S=(0,)  odd(G-S)=3
n=1:     1 張圖全部符合（其中 0 張有完美匹配）
n=2:     2 張圖全部符合（其中 1 張有完美匹配）
n=3:     8 張圖全部符合（其中 0 張有完美匹配）
n=4:    64 張圖全部符合（其中 37 張有完美匹配）
n=5:  1024 張圖全部符合（其中 0 張有完美匹配）
n=6: 32768 張圖全部符合（其中 24823 張有完美匹配）
耗時 89.4s
```

三角形花束是 Tutte 證書最直觀的樣子：拿掉中心點 `0`，剩下三個三角形（奇分量），但 `S` 只有一個點能接住其中一個。`n = 6` 的 32768 張圖要跑 77–90 秒（AMD Ryzen 7 5700G、單執行緒，兩次執行），`n = 7` 有 `2^21` 張圖，用這個寫法要跑幾十倍久——這也提醒你 Tutte 條件本身不是演算法。

## Edmonds Blossom：縮花

### 名詞

從一個自由點 `r` 長**交錯樹（alternating tree）**：`r` 是 even；even 點 `v` 沿非匹配邊碰到一個不在樹上的點 `w`，`w` 成為 odd；`w` 的伴侶成為 even。搜尋時看 even 點的每條邊 `vw`：

```text
w 不在樹上、自由      → 找到增廣路（r ... v - w）
w 不在樹上、已被配    → 長樹：w 成為 odd，mate(w) 成為 even
w 是 odd             → 什麼都不做（偶數長度的圈，不影響）
w 是 even            → v 與 w 到 r 的樹路徑加上 vw 形成奇圈：這就是花
```

**花（blossom）**：一個長度 `2k + 1` 的奇圈 `B`，上面有 `k` 條匹配邊，唯一沒在 `B` 內被配的點叫**底座（base）**`b`。從樹根 `r` 到 `b` 的交錯路叫**莖（stem）**，它長度為偶數，以匹配邊結尾（或 `r = b`、長度 0）。

**縮花**：把 `B` 的所有點合成一個新點 `B`，`B` 和原本任一花上點相鄰的點相鄰。得到 `G / B` 與 `M / B`（拿掉 `B` 內部的 `k` 條匹配邊）。

```text
縮花前                         縮花後
   r                              r
   ‖ (stem)                       ‖
   b                              B   ← 整朵花當成一個 even 點
  ╱ ╲                              │
 x   y                             7
 ‖   ‖
 x'──y'
 │
 7
```

### 核心引理：縮花不改變增廣路的存在性

**引理**：設 `B` 是對 `M` 的花。`G` 有 `M`-增廣路，若且唯若 `G / B` 有 `(M / B)`-增廣路。

**先把底座變成自由點**。令 `Q` 是莖，`M' = M Δ Q`。`Q` 是偶數長度、從自由點 `r` 出發的交錯路，翻轉後 `|M'| = |M|`、`r` 被配上、`b` 變成自由點，而 `B` 仍然是 `M'` 的花（`B` 內部的匹配邊沒動）。`M` 最大若且唯若 `M'` 最大；`M / B` 與 `M' / B` 在 `G / B` 中大小相同，也是一個最大另一個就最大。由 Berge 定理，「有增廣路」等價於「不是最大匹配」，所以只要對 `M'`（底座自由）證明引理。

**`G / B` 有增廣路 ⇒ `G` 有增廣路**（展開）：設 `P` 是 `G / B` 中的 `(M' / B)`-增廣路。若 `P` 不經過 `B`，它在 `G` 中照樣是增廣路。若經過，因為 `B` 在 `M' / B` 中是自由點，`B` 必是 `P` 的一個端點，`P` 的最後一條邊是 `uB`，對應 `G` 中某條邊 `uv`，`v ∈ B`。在奇圈 `B` 上從 `v` 到 `b` 有兩條路，長度一奇一偶；取偶數長度的那條，它以匹配邊離開 `v`（偶數長且交錯）、在 `b` 結束。把它接在 `P` 後面，得到 `G` 中一條從 `P` 的另一端到自由點 `b` 的交錯路，就是 `M'`-增廣路。

**`G` 有增廣路 ⇒ `G / B` 有增廣路**（收縮）：設 `P` 是 `G` 中的 `M'`-增廣路。若 `P` 不碰 `B`，它在 `G / B` 中照樣是增廣路。若碰到 `B`：`B` 中只有 `b` 是自由點，其餘點都被 `B` 內部的匹配邊配掉。`P` 的兩個端點都自由，最多一個是 `b`，所以至少有一個端點 `u ∉ B`。從 `u` 沿 `P` 走到第一個碰到 `B` 的點 `v`，這段路叫 `P'`。進入 `v` 的那條邊連接 `B` 外與 `B` 內，而 `B` 內的點的匹配邊都在 `B` 內（`b` 根本沒匹配邊），所以這條邊不是匹配邊。`P'` 在 `G / B` 中變成從自由點 `u` 到自由點 `B` 的交錯路，以非匹配邊開頭、非匹配邊結尾，是增廣路。∎

**由引理得到演算法**：長交錯樹；碰到自由點就增廣；碰到 even-even 邊就縮花、繼續長（縮出來的點是 even）；樹長不動時這個根沒有增廣路。找到增廣路時，用「展開」那一半的構造把縮過的花一層層展開回 `G` 的路。

### 實作：用 `base[]` 代替真的縮圖

真的建出 `G / B` 再遞迴很慢也很難寫。經典的陣列寫法維持原圖，用 `base[v]` 記錄「`v` 目前被縮進哪朵花、那朵花的底座是誰」，縮花等於把一批點的 `base` 改成同一個值。展開則藏在 `parent` 的改寫裡：`mark_path` 把花上 odd 點的 `parent` 指向花的另一側，之後沿 `parent` / `match` 回頭走，就自動走出「繞花偶數側」的那條路。

```python
import random
import time
from collections import deque

import networkx as nx


def edmonds_blossom(adj, init=(), stats=None):
    """一般圖最大基數匹配。adj: dict[node, set[node]]；回傳 set of frozenset 邊。

    經典陣列版 Edmonds：花不真的建成新點，而是用 base[v] 記
    「v 目前被縮進哪朵花、那朵花的底座是誰」。
    """
    nodes = sorted(adj, key=repr)
    idx = {v: i for i, v in enumerate(nodes)}
    n = len(nodes)
    g = [sorted(idx[w] for w in adj[v]) for v in nodes]
    match = [-1] * n
    for u, v in init:                 # 可選：從給定的匹配開始
        match[idx[u]], match[idx[v]] = idx[v], idx[u]

    def find_augmenting(root):
        """從自由點 root 長交錯樹；回傳 (自由終點, parent)，找不到時終點為 -1。"""
        even = [False] * n          # 是否為樹上的 even（外層）點
        parent = [-1] * n           # odd 點在樹上的父親（某個 even 點）
        base = list(range(n))
        even[root] = True
        q = deque([root])

        def lca(a, b):
            """兩個 even 點在樹上的最近共同祖先（以花底座為單位往上爬）。"""
            seen = [False] * n
            while True:
                a = base[a]
                seen[a] = True
                if match[a] == -1:              # 爬到樹根
                    break
                a = parent[match[a]]
            while True:
                b = base[b]
                if seen[b]:
                    return b
                b = parent[match[b]]

        def mark_path(v, b, child, in_blossom):
            """從 v 往上爬到底座 b：標記花上的點，並把 odd 點的 parent 改指向花的另一側。"""
            while base[v] != b:
                in_blossom[base[v]] = in_blossom[base[match[v]]] = True
                parent[v] = child
                child = match[v]
                v = parent[match[v]]

        while q:
            v = q.popleft()
            for to in g[v]:
                if base[v] == base[to] or match[v] == to:
                    continue                    # 花內部的邊，或 v 自己的匹配邊
                if to == root or (match[to] != -1 and parent[match[to]] != -1):
                    # to 也是 even 點：even-even 邊閉合出奇圈 -> 縮花
                    b = lca(v, to)
                    if stats is not None:
                        stats["blossoms"] = stats.get("blossoms", 0) + 1
                    in_blossom = [False] * n
                    mark_path(v, b, to, in_blossom)
                    mark_path(to, b, v, in_blossom)
                    for i in range(n):
                        if in_blossom[base[i]]:
                            base[i] = b
                            if not even[i]:     # 花上原本的 odd 點，縮花後也能當 even 往外長
                                even[i] = True
                                q.append(i)
                elif parent[to] == -1:
                    parent[to] = v              # to 成為 odd 點
                    if match[to] == -1:
                        return to, parent       # 自由點：增廣路終點
                    even[match[to]] = True      # to 的伴侶成為 even 點
                    q.append(match[to])
        return -1, parent

    for r in range(n):
        if match[r] != -1:
            continue
        v, parent = find_augmenting(r)
        # 沿 parent / match 回頭翻轉；花已經由 mark_path 的 parent 改寫自動展開
        while v != -1:
            pv = parent[v]
            nxt = match[pv]
            match[v], match[pv] = pv, v
            v = nxt
    return {frozenset((nodes[i], nodes[match[i]])) for i in range(n) if match[i] > i}


def is_matching(adj, M):
    seen = set()
    for e in M:
        u, v = tuple(e)
        if v not in adj[u] or u in seen or v in seen:
            return False
        seen |= {u, v}
    return True


# 1) 上一節讓樸素搜尋失敗的那張圖，從同一個 M = {1-2, 3-4, 5-6} 開始
adj = {v: set() for v in range(8)}
for u, v in [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 2), (3, 7)]:
    adj[u].add(v)
    adj[v].add(u)
stats = {}
M = edmonds_blossom(adj, init=[(1, 2), (3, 4), (5, 6)], stats=stats)
print("花圖:", len(M), sorted(tuple(sorted(e)) for e in M), stats)
P = nx.petersen_graph()
print("Petersen:", len(edmonds_blossom({u: set(P[u]) for u in P})))

# 2) 對拍：隨機圖上與 nx.max_weight_matching(maxcardinality=True) 比基數
rng = random.Random(23)
for trial in range(2000):
    n = rng.randint(1, 30)
    G = nx.gnp_random_graph(n, rng.choice([0.05, 0.1, 0.2, 0.4]), seed=trial)
    adj = {u: set(G[u]) for u in G}
    M = edmonds_blossom(adj)
    assert is_matching(adj, M)
    assert len(M) == len(nx.max_weight_matching(G, maxcardinality=True)), trial
print("2000 張隨機圖：自刻 Blossom 與 NetworkX 基數全部一致")

# 3) 計時
for n, p in [(1000, 0.01), (2000, 0.0015), (3000, 0.002)]:
    G = nx.gnp_random_graph(n, p, seed=2026)
    adj = {u: set(G[u]) for u in G}
    t = time.perf_counter(); M = edmonds_blossom(adj); t1 = time.perf_counter() - t
    t = time.perf_counter(); R = nx.max_weight_matching(G, maxcardinality=True)
    t2 = time.perf_counter() - t
    assert len(M) == len(R)
    print(f"n={n:>4} |E|={G.number_of_edges():>5}  |M|={len(M):>3}  "
          f"自刻 {t1:6.3f}s  nx {t2:6.3f}s")
```

```text
花圖: 4 [(0, 1), (2, 6), (3, 7), (4, 5)] {'blossoms': 1}
Petersen: 5
2000 張隨機圖：自刻 Blossom 與 NetworkX 基數全部一致
n=1000 |E|= 4973  |M|=500  自刻  0.022s  nx  0.140s
n=2000 |E|= 3033  |M|=927  自刻  0.064s  nx  0.627s
n=3000 |E|= 9013  |M|=1496  自刻  0.108s  nx  2.874s
```

花圖這次縮了一朵花、找到 `0-1=2-6=5-4=3-7`，翻轉後得到 4 條邊的完美匹配，正是樸素搜尋漏掉的那條路。

逐段對照引理：

- **`to` 是不是 even**：`to == root`，或 `to` 已被配而且它的伴侶在樹上有 `parent`（伴侶是 odd，`to` 就是 even）。這兩個條件就是「even-even 邊」。
- **`lca`**：從兩端沿「`base` → 伴侶 → `parent`」往上爬，第一個共同的底座就是新花的底座 `b`。以 `base` 為單位爬，是因為已經縮過的花要當成一個點。
- **`mark_path`**：把兩側路徑上的點標成花的成員，同時把 odd 點的 `parent` 改指向「從另一側繞過來」的鄰居。這一步就是引理「展開」那一半的構造被預先存好。
- **花上 odd 點入佇列**：縮花後整朵花是 even，原本 odd 的成員現在也能往外長——這正是樸素搜尋缺的那一步（範例中的點 `3`）。

**複雜度**：一次 `find_augmenting` 中，每個點最多入佇列一次，掃鄰居共 `O(E)`；每次縮花至少讓兩個不同底座合併，所以最多 `n/2` 次縮花，每次 `lca` 與重標 `base` 是 `O(n)`。一次搜尋 `O(n² + E) = O(n²)`，對 `n` 個根各一次，總共 `O(n³)`。這裡有一個本章沒證的前提：某個根搜尋失敗後，之後的增廣也不會讓它重新有增廣路，所以每個根只搜一次。二分圖的對應引理在 Ch 22 證過；一般圖版本要額外處理花，本章不證，上面 2000 張圖的對拍是經驗支持，不是證明。

計時條件同 Ch 22：AMD Ryzen 7 5700G、Windows 11、單執行緒、單次執行；重跑幾次，`nx` 在 `n = 3000` 的時間在 2.3–3.2 秒之間浮動。自刻版比 NetworkX 快不代表更好：`nx.max_weight_matching` 做的是**加權**的 primal-dual Edmonds，`maxcardinality=True` 只是要求它在最大基數的匹配中找最大權重，它要維護對偶變數，常數本來就大。

## 對比與取捨

| 方法 | 時間 | 能做什麼 | 備註 |
| --- | --- | --- | --- |
| Ch 22 交錯 BFS / HK | `O(E√V)` | 只限二分圖 | 用在一般圖會漏增廣路（上面的實驗） |
| Edmonds 原始版（1965） | 原論文估計 `O(n⁴)` | 一般圖最大基數 | 真的縮圖 |
| 陣列版 Edmonds（本章，Gabow 1976 的同類寫法） | `O(n³)` | 一般圖最大基數 | 競程與教學最常見 |
| Micali–Vazirani（1980） | `O(E√V)` | 一般圖最大基數 | 與 HK 同階，但極難實作，完整正確性證明拖了很多年 |
| 加權 Edmonds（primal-dual） | `O(n³)` | 一般圖最大權匹配 | `nx.max_weight_matching` |
| Tutte 矩陣 + 隨機代值（Lovász 1979） | 一次行列式 | 判定有無完美匹配 | 隨機演算法，見「進階」 |

## 踩雷集錦

1. **「二分圖的增廣路演算法改一下就能用」**：不行，上面的樸素搜尋在任何鄰居順序下都失敗。更危險的是：它在大部分圖上會給對的答案，所以你很可能不會發現。把 `edmonds_blossom` 裡「縮花」分支的第一行換成 `continue`（碰到 even-even 邊直接跳過），用同樣的 2000 張隨機圖（`random.Random(23)`）重跑，本機結果是 **12 張**基數錯誤——只有 0.6%。隨機測試抓不太到花的 bug，要針對性地構造含奇圈的例子（例如花圖），並配合 Tutte–Berge 證書檢查。
2. **`max_weight_matching` 預設不是最大基數**：

   ```python
   import networkx as nx

   G = nx.Graph()
   G.add_edge("a", "b", weight=1)
   G.add_edge("b", "c", weight=3)
   G.add_edge("c", "d", weight=1)
   print("預設            :", sorted(map(sorted, nx.max_weight_matching(G))))
   print("maxcardinality  :", sorted(map(sorted, nx.max_weight_matching(G, maxcardinality=True))))
   H = nx.Graph([(1, 2), (0, 1), (2, 3)])     # 還是路 0-1-2-3，只是邊的插入順序不同
   print("maximal_matching:", sorted(map(sorted, nx.maximal_matching(H))))
   ```

   ```text
   預設            : [['b', 'c']]
   maxcardinality  : [['a', 'b'], ['c', 'd']]
   maximal_matching: [[1, 2]]
   ```

   有權重時，預設回傳總權重最大的匹配（3 > 2），邊數反而較少。要最大基數一定加 `maxcardinality=True`；沒有 `weight` 屬性的圖每條邊權重視為 1，兩者才一致。
3. **`nx.maximal_matching` 是貪婪的極大匹配**：上面同一條路換個邊的插入順序，它只給 1 條邊。名字差一個字，語意差很多。
4. **Tutte 條件不是演算法**：檢查所有 `S` 是 `2^n`。要證書時，先跑 Blossom，再從最後一次失敗的交錯樹讀出 Gallai–Edmonds 分解（見「進階」），而不是去窮舉 `S`。
5. **König 定理在一般圖不成立，別用最小點覆蓋當上界證書**：Ch 22 的 König 證書（`ν = τ`）在 Petersen 圖上是 5 對 6。一般圖的正確證書是 Tutte–Berge 的 `S`；最小點覆蓋在一般圖是 NP-hard。

## 進階：再往深一層

- **Gallai–Edmonds 結構定理**：令 `D` 是「至少被一個最大匹配漏掉」的點，`A` 是 `D` 的鄰居中不在 `D` 的點，`C` 是其餘。定理說 `G[C]` 有完美匹配、`G[D]` 的每個分量是 factor-critical（拿掉任一點後有完美匹配），而 `A` 就是讓 Tutte–Berge 公式取到最小值的 `S`。Blossom 跑完後，最後一輪搜尋中的 even 點（含花）大致對應 `D`、odd 點對應 `A`。這裡只給定理陳述，證明見 Lovász–Plummer 第 3 章。
- **Petersen 定理**：無橋三正則圖有完美匹配。證明架構：對任意 `S`，數 `S` 與每個奇分量之間的邊——三正則加上奇分量點數為奇，推得每個奇分量往外至少 3 條邊（無橋排除 1 條，奇偶性排除 2 條），而 `S` 最多接 `3|S|` 條，於是 `3·odd(G − S) ≤ 3|S|`，Tutte 條件成立。可以試著自己把細節補完。
- **Tutte 矩陣**：令 `T[i][j] = x_ij`、`T[j][i] = −x_ij`（`ij` 是邊，`i < j`），其餘為 0。Tutte 證明 `det T` 作為多項式不恆為 0 若且唯若 `G` 有完美匹配；Lovász（1979）觀察到把變數換成大質數體上的隨機值，就得到隨機判定演算法，錯判機率由 Schwartz–Zippel 引理控制在 `n / p` 以下：

  ```python
  import random

  import networkx as nx

  P = 2_147_483_647          # 2^31 - 1，是質數，在這個有限體裡做高斯消去


  def det_mod_p(A, p=P):
      A = [row[:] for row in A]
      n, det = len(A), 1
      for c in range(n):
          piv = next((r for r in range(c, n) if A[r][c] % p), None)
          if piv is None:
              return 0
          if piv != c:
              A[c], A[piv] = A[piv], A[c]
              det = -det
          det = det * A[c][c] % p
          inv = pow(A[c][c], p - 2, p)
          for r in range(c + 1, n):
              f = A[r][c] * inv % p
              for k in range(c, n):
                  A[r][k] = (A[r][k] - f * A[c][k]) % p
      return det % p


  def tutte_test(G, rng):
      """Tutte 矩陣代入隨機值：det != 0 => 一定有完美匹配；det == 0 => 很可能沒有。"""
      nodes = list(G)
      pos = {v: i for i, v in enumerate(nodes)}
      n = len(nodes)
      T = [[0] * n for _ in range(n)]
      for u, v in G.edges():
          x = rng.randrange(1, P)
          i, j = sorted((pos[u], pos[v]))
          T[i][j], T[j][i] = x, (-x) % P     # 反對稱：T[i][j] = x_ij, T[j][i] = -x_ij
      return det_mod_p(T) != 0


  rng = random.Random(1947)
  bouquet = nx.Graph()
  for t in range(3):
      a, b, c = 3 * t + 1, 3 * t + 2, 3 * t + 3
      bouquet.add_edges_from([(a, b), (b, c), (c, a), (0, a)])
  bouquet.add_edge(10, 11)                    # 補成 12 個點（偶數），仍然沒有完美匹配
  for name, G in [("Petersen", nx.petersen_graph()), ("三角形花束+1 邊", bouquet),
                  ("K4", nx.complete_graph(4)), ("C6", nx.cycle_graph(6))]:
      truth = 2 * len(nx.max_weight_matching(G, maxcardinality=True)) == G.number_of_nodes()
      print(f"{name:12}  Tutte 矩陣判定={tutte_test(G, rng)!s:5}  真值={truth}")
  ```

  ```text
  Petersen      Tutte 矩陣判定=True   真值=True
  三角形花束+1 邊     Tutte 矩陣判定=False  真值=False
  K4            Tutte 矩陣判定=True   真值=True
  C6            Tutte 矩陣判定=True   真值=True
  ```

  代數方法的意義在平行化：行列式可以高度平行地算，這條路線導出了匹配問題的 RNC 演算法（Karp–Upfal–Wigderson、Mulmuley–Vazirani–Vazirani）。
- **加權一般圖匹配**：在 Ch 24 的 LP 對偶觀點下，Edmonds 的加權版本要對每個奇集合加一條「奇集合約束」，這正是一般圖的匹配多面體比二分圖多出來的那些面；Ch 26 會說明二分圖不需要它們的原因（全單模性）。

## 動手練習

1. 把 `edmonds_blossom` 的縮花分支改成 `continue`，在 `random.Random(23)` 的 2000 張圖上重跑，找出第一張出錯的圖，畫出它（`nx.draw(G, with_labels=True)`，你會看到一個奇圈），用它解釋錯在哪。
2. 加一個函式 `tutte_witness(adj)`：跑完 Blossom 後，用最後一輪失敗搜尋的 odd 點集合當 `S`，檢查 `(n + |S| − odd(G − S)) / 2` 是否等於 `ν`。在 1000 張隨機圖上驗證。（若不總是相等，想想要從哪幾個根出發才能湊齊 Gallai–Edmonds 的 `A`。）
3. 構造一張圖，讓 `stats["blossoms"]` 至少是 10，並讓某朵花巢狀包含另一朵已縮的花。追蹤 `base[]` 在每次縮花後的值。
4. 寫出 Petersen 定理證明架構裡「每個奇分量往外至少 3 條邊」的完整論證，並在 `nx.petersen_graph()` 上窮舉所有 `S` 驗證 Tutte 條件。

## 本章重點整理

- Berge 定理讓「找最大匹配」等於「找增廣路」，對一般圖也成立；難處在搜尋，不在定理。
- 二分圖中每個點的 even/odd 身分固定；奇圈讓同一點同時可以是 even 與 odd，樸素搜尋因此漏路，而且隨機測試很難發現。
- Tutte 定理（`odd(G − S) ≤ |S|`）與 Tutte–Berge 公式是一般圖的 min-max 證書；Lovász 的極大反例證明是本章最值得自己重寫一次的論證。
- 縮花引理：先翻轉莖讓底座自由，再證「縮」與「展開」兩個方向；陣列實作用 `base[]` 縮、用 `parent` 改寫展開，`O(n³)`。

## 自我檢核

- [ ] 不看筆記，畫出一張樸素交錯搜尋會漏掉增廣路的圖，並指出哪個點被標錯身分。
- [ ] Tutte 充分性證明的情況二中，為什麼 `P` 必須從 `yw` 那個方向出發？從另一個方向出發會在哪裡卡住？
- [ ] 縮花引理的「收縮」方向，為什麼要先翻轉莖？如果底座不自由，哪一句論證會失效？
- [ ] 如果有人說「Blossom 太難寫，我用二分圖演算法加一些隨機測試就好」，你會用什麼數據反駁？
- [ ] 能說出 `nx.max_weight_matching`、`nx.maximal_matching`、`nx.bipartite.hopcroft_karp_matching` 三者語意的差別，以及各自什麼時候會給出「看起來對但其實不是最大」的結果嗎？
- [ ] 想一個你遇過的「兩兩配對」問題（測試機配對、code review 互審），它需要一般圖匹配還是二分圖匹配就夠？

## 延伸閱讀

### 論文

- **[Paths, Trees, and Flowers](https://doi.org/10.4153/CJM-1965-045-4)** — Jack Edmonds，Canadian Journal of Mathematics 17:449–467（1965）
  - **核心貢獻**：一般圖最大匹配的第一個多項式時間演算法（縮花），以及「好的演算法 = 多項式步數」這個觀念。
  - **讀哪裡**：開頭討論「good algorithm」的那段是複雜度理論史上常被引用的段落；接著是交錯樹與縮花的描述。符號較舊，建議先讀完本章再讀。
  - **和本章的關聯**：本章的縮花引理與 `base[]` 實作都是這篇論文的現代化版本。
- **[The Factorization of Linear Graphs](https://doi.org/10.1112/jlms/s1-22.2.107)** — W. T. Tutte，Journal of the London Mathematical Society s1-22(2):107–111（1947）
  - **核心貢獻**：Tutte 定理的原始論文。就我所知，原證明走代數路線，用到反對稱矩陣的 Pfaffian，也就是後來 Tutte 矩陣的源頭。
  - **讀哪裡**：全文 5 頁，可以對照本章 Lovász 的組合證明。密西根大學 Seth Pettie 的匹配論文收藏頁有[掃描版](https://web.eecs.umich.edu/~pettie/matching/Tutte-factorization-of-linear-graphs.pdf)。
  - **前提知識**：行列式與 Pfaffian 的定義。
- **[Two Theorems in Graph Theory](https://doi.org/10.1073/pnas.43.9.842)** — Claude Berge，PNAS 43(9):842–844（1957）
  - **核心貢獻**：證明「最大匹配 ⇔ 沒有增廣路」對一般圖成立（這個觀察 Petersen 在 1891 年已經用過，Berge 給了一般陳述與證明）。
  - **讀哪裡**：全文 3 頁，第一個定理就是本章的 Berge 定理。
  - **和本章的關聯**：本章 Berge 定理的出處；Tutte–Berge 公式以 Berge 命名，但本章沒有逐字核對它是否出自這篇，引用時請以原文為準。
- **An O(√|V|·|E|) Algorithm for Finding Maximum Matching in General Graphs** — Silvio Micali、Vijay V. Vazirani，21st IEEE FOCS，pp. 17–27（1980）
  - **核心貢獻**：把 Hopcroft–Karp 的「每 phase 一批最短增廣路」推廣到一般圖，達到 `O(E√V)`。
  - **讀哪裡**：先讀 Vazirani 後來寫的正確性證明論文的導論（他在 arXiv:2012.03582 說明了這個演算法為什麼難證）；原論文細節非常多，不建議第一次就硬讀。
  - **前提知識**：Ch 22 的 Hopcroft–Karp 與本章的縮花。

### 講義

- **[Lecture Notes on Non-bipartite Matching](https://people.math.umass.edu/~raymond/nonbip.pdf)** — Michel X. Goemans（MIT 18.433 Combinatorial Optimization 講義，2015 年版；連結是 UMass 鏡像）
  - **這篇說什麼**：從 Tutte–Berge 公式出發，用交錯樹與縮花證明 Edmonds 演算法正確，最後收斂出 Tutte–Berge 的證書。
  - **讀哪裡**：整份；縮花引理的證明與本章同一路線，可以互相對照。
  - **為什麼值得讀**：Goemans 是組合最佳化的權威，這份講義比教科書精簡、比論文好讀；同一門課的 bipartite matching 講義也可以回頭補 Ch 22。

### 書籍

- **《Matching Theory》** — László Lovász、Michael D. Plummer（North-Holland 1986；AMS Chelsea 修訂重印 2009）
  - **讀哪幾章**：第 3 章「Size and structure of maximum matchings」（含 Gallai–Edmonds 結構）與第 9 章「Matching algorithms」。重印版附錄整理了 1986 年後的進展。
  - **前提知識**：本章即可；書的密度很高，一次讀一節。
- **《Introduction to Graph Theory》** — Douglas B. West（Prentice Hall，第 2 版，2001）
  - **讀哪幾章**：Section 3.3（Matchings in General Graphs）：Tutte 定理、Berge–Tutte 公式、Edmonds 演算法、Petersen 定理，習題可以用來練本章的證明技巧。

### 背景文章

- **[Paths, Trees, and Flowers（NIST 百年回顧收錄文章）](https://nvlpubs.nist.gov/nistpubs/sp958-lide/html/140-144.html)** — NIST Special Publication 958
  - **這篇說什麼**：Edmonds 在美國國家標準局（NBS）時期發現 Blossom 演算法的背景、「good algorithm」觀念的歷史意義。
  - **為什麼值得讀**：沒有數學門檻，十分鐘讀完，幫你把本章放回演算法史的脈絡。

匹配到這裡都是「有沒有、有幾條」。真實的指派問題幾乎都帶成本：誰配誰要花多少錢、多少延遲。下一章把增廣路換成「最短」增廣路，配上 potentials，處理帶權的指派與最小費用流。

→ [Ch 24 最小費用流與指派問題](./24-min-cost-flow-assignment.md)
