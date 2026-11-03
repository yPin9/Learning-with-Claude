# Ch 34 — NP-complete 圖問題與歸約

> **目標**：能用自己的話精確說出 P、NP、NP-hard、NP-complete 的定義；親手寫出 3-SAT → Independent Set → Vertex Cover → Clique、3-SAT → 3-Coloring、3-SAT → Hamilton cycle 的歸約，證明「原題有解 ⇔ 歸約後有解」，並用程式在幾百個小實例上驗證兩個方向。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11（AMD Ryzen 7 5700G）。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。在繁中 Windows 上把輸出導到檔案或 pipe 時，先設 `PYTHONUTF8=1`，否則中文會變成 cp950 亂碼。

## 為什麼需要這個？

前面 33 章我們一直在做同一件事：找一個多項式時間的演算法，然後證明它對。MST、max-flow、匹配、dominator tree 都是這樣。但 [Ch 8](./08-euler-hamilton.md) 已經埋下伏筆：Euler 迴路有線性時間演算法，Hamilton 迴路卻沒有人找得到；[Ch 15](./15-graph-coloring.md) 的 2-著色是 BFS，3-著色卻只剩回溯；[Ch 17](./17-register-allocation.md) 的 Chaitin 分配器用啟發式處理著色，而不是精確解。

1960 年代的人面對這種「找不到」只能說「我們很努力了」。1971 年 Cook 給了一個完全不同的回答方式：**證明這個問題至少和一整類問題一樣難**。如果你能多項式時間解 3-著色，你就能多項式時間解 SAT，進而解所有「答案好驗證」的問題。隔年 Karp 用同一招一口氣把 21 個組合問題（clique、vertex cover、chromatic number、Hamilton cycle……）綁在一起。從此「找不到快速演算法」有了一個可以證明的替代說法：「它是 NP-complete，找到了你就證明了 P = NP」。

對工程師，這章的價值在兩件事：

1. **停損**：老闆要你寫「保證最佳」的 register allocator、測試案例最小化、攻擊路徑最小切斷（有權重、有限制時），先判斷它是不是偽裝過的 NP-complete 問題。是的話，該討論的是近似、啟發式、參數化（[Ch 36](./36-treewidth.md)）或交給 SAT solver（[Ch 35](./35-reductions-to-sat.md)），而不是「再想一個禮拜」。
2. **歸約本身是建模技術**：把問題 A 翻譯成問題 B 的那段程式，就是你之後把排程、著色、路徑問題丟給 SAT/SMT solver 的同一種程式。這章寫的每個歸約都是一個小編譯器。

## 先建立直覺

把 NP 想成「有人遞給你一張紙條，你能很快檢查紙條上的答案對不對」的問題集合：

```text
問題            紙條（證書, certificate）       檢查方式（多項式時間）
---------------------------------------------------------------------
SAT             每個變數的真假                   代進去每個子句算一遍
k-Clique        k 個點                           兩兩查一下有沒有邊
3-Coloring      每個點的顏色                     每條邊兩端不同色
Hamilton cycle  點的一個排列                     相鄰兩點有邊、每點恰一次
```

難的是**沒有紙條**時自己找。而**歸約（reduction）**是一台翻譯機：

```text
     A 的實例 x ──── f（多項式時間）────▶ B 的實例 f(x)
                                              │
                               B 的解題器 ────┘
                                              ▼
     x 是 yes  ⇔  f(x) 是 yes        （只回答 yes/no）
```

如果 B 好解，A 也好解（先翻譯再解）。反過來讀才是這章的用法：**如果 A 已知很難，B 不可能比 A 容易**。方向是「從已知難的問題，歸約到你想證明難的問題」，這是第一個踩雷點，後面會再強調。

> 如果你對 Hamilton cycle 與 Euler 迴路的對比還不熟，先回看 [Ch 8 — Euler 與 Hamilton：容易與困難的分水嶺](./08-euler-hamilton.md)。

## 精確定義：給工程師的版本

我們只處理**判定問題（decision problem）**：輸入一個字串，回答 yes 或 no。最佳化問題（「最大團多大？」）先轉成判定版（「有沒有大小至少 k 的團？」）；兩者在多項式時間內互相轉換，因為可以對 k 二分搜尋，再用「刪一個點看答案變不變」把解本身找出來（這叫 self-reducibility）。

輸入長度 `|x|` 指的是編碼成位元的長度。一張 n 點 m 邊的圖用鄰接串列編碼約 `O((n + m) log n)` 位元；多項式時間指的是 `|x|` 的多項式，對圖來說等價於 `n` 的多項式。

- **P**：存在一個演算法，對每個輸入 x 在 `O(|x|^c)` 步內正確回答（c 是常數）。
- **NP**：存在一個多項式時間的**驗證器** `V(x, w)` 和多項式 `p`，使得
  `x 是 yes  ⇔  存在 w，|w| ≤ p(|x|)，且 V(x, w) = 1`。
  w 就是紙條。注意 NP 的定義**不對稱**：no 實例不需要有能讓人信服的紙條（那是 coNP）。
- **多項式時間歸約（Karp reduction, many-one reduction）**`A ≤p B`：存在多項式時間可算的函式 f，使得 `x ∈ A ⇔ f(x) ∈ B`。
- **NP-hard**：所有 NP 問題都能多項式時間歸約到它。它本身不必在 NP 裡（例如停機問題是 NP-hard，但不可判定）。
- **NP-complete**：在 NP 裡，**而且** NP-hard。

兩個馬上會用到的性質：

1. **傳遞性**：`A ≤p B` 且 `B ≤p C` ⇒ `A ≤p C`。證明：f 跑 `p(|x|)` 步，所以 `|f(x)| ≤ p(|x|)`；g 再跑 `q(p(|x|))` 步，多項式的合成還是多項式。
2. **證 NP-complete 的標準流程**：要證 B 是 NP-complete，(a) 給 B 的驗證器，證明 `B ∈ NP`；(b) 挑一個已知 NP-complete 的 A，寫出 `A ≤p B`。由傳遞性，所有 NP 問題都經由 A 歸約到 B。

「NP」是 nondeterministic polynomial 的縮寫，**不是** non-polynomial。P ⊆ NP（驗證器可以無視紙條自己解），P 是否等於 NP 是公開問題。

### Cook–Levin 定理

**定理（Cook 1971；Levin 1973 獨立得到）**：SAT 是 NP-complete。更強地，3-SAT（每個子句恰 3 個文字的 CNF）是 NP-complete。

這裡只給證明架構，不寫完整證明。對任意 NP 問題 L，取它的驗證器 V 與多項式時間上界 `T(n)`。V 在輸入 `(x, w)` 上的計算可以畫成一張 `T × T` 的表格（tableau）：第 t 列是第 t 步時 Turing machine 的帶子內容、讀寫頭位置與狀態。對每一格、每個可能的符號開一個布林變數，然後寫子句說：

- 第 0 列是 `x` 接上某個未知的 w（w 的位元就是自由變數）；
- 每個 2×3 小窗內的變化符合轉移函式（局部性：一步只改讀寫頭附近）；
- 最後某一列進入接受狀態。

子句數是 `O(T^2)`，所以公式大小是多項式；而公式可滿足 ⇔ 存在 w 讓 V 接受 ⇔ `x ∈ L`。從 SAT 到 3-SAT 再用 Tseitin 式的切子句（長子句引入新變數切成 3 文字一段），這一步在 [sat_smt 的 Ch 4 — Tseitin 轉換](../../programming/sat_smt/04-tseitin-transformation.md) 有完整處理。

Cook–Levin 是整個理論唯一「直接從定義出發」的 NP-hard 證明。之後每一個 NP-complete 證明都是歸約，最終追溯回它。

## 底層機制：歸約的三件套

每個歸約都要交出三樣東西，少一樣就不算證完：

```text
(1) 建構：給 x，在多項式時間內造出 f(x)        ← 寫成程式
(2) ⇒ 方向：x 有解 w  ⇒  從 w 造出 f(x) 的解     ← 「解的翻譯」
(3) ⇐ 方向：f(x) 有解 w' ⇒ 從 w' 讀回 x 的解     ← 最容易漏、最容易錯
```

(3) 是大多數錯誤歸約死掉的地方：你造的 gadget 允許了某種「作弊解」，它讓 f(x) 有解，但讀不回 x 的解。所以我們的程式對每個隨機實例做三件事：原題暴力解、歸約後精確解、以及「從歸約後的解讀回原題的解並檢查它」。

這一章的歸約地圖：

```text
                 SAT（Cook–Levin）
                  │  切子句
                  ▼
                3-SAT ───────────────┬──────────────────┐
                  │                  │                  │
                  ▼                  ▼                  ▼
          Independent Set       3-Coloring      directed Ham path
             │       │                                  │  每點拆三
             ▼       ▼                                  ▼
       Vertex Cover  Clique                   undirected Ham cycle
```

## 歸約一：3-SAT → Independent Set

**問題**：Independent Set（IS）——給圖 G 和整數 k，有沒有 k 個兩兩不相鄰的點？

**IS ∈ NP**：紙條是 k 個點，檢查 `C(k, 2)` 對都不相鄰，`O(k^2)` 次查表。

**建構**：給 m 個子句的 3-CNF φ。每個子句 `(l1 ∨ l2 ∨ l3)` 造一個三角形，三個點分別標上 l1、l2、l3。另外，任何兩個標著互補文字（`x` 與 `¬x`）的點之間連一條邊。令 `k = m`。

```text
φ = (x1 ∨ x2 ∨ ¬x3) ∧ (¬x1 ∨ x2 ∨ x3) ∧ (x1 ∨ ¬x2 ∨ ¬x3)

   子句0        子句1        子句2
    x1  ─────── ¬x1 ──────── x1          三角形內的邊沒畫
   /  \        /   \        /  \         橫線是互補文字的邊
  x2──¬x3     x2 ── x3    ¬x2──¬x3       （x2 與 ¬x2、x3 與 ¬x3 也有）
```

**定理**：φ 可滿足 ⇔ G 有大小為 m 的獨立集。

**證明**。（⇒）取滿足賦值 a。每個子句至少有一個文字在 a 下為真，每個子句挑一個為真的文字，對應的點組成集合 S，`|S| = m`。S 內任兩點來自不同子句（每個三角形只挑一點），所以沒有三角形邊；兩點的文字都在 a 下為真，所以不可能互補（`x` 和 `¬x` 不會同時為真），沒有互補邊。S 是獨立集。

（⇐）設 S 是大小 m 的獨立集。每個三角形最多貢獻一點（三角形內兩兩相鄰），m 個三角形、m 個點，所以恰好每個三角形一點。S 裡不會同時有 `x` 和 `¬x`（它們相鄰），所以「把 S 裡出現的文字設為真」是一致的部分賦值；沒出現的變數隨便設。每個子句都有一個被選中的文字為真，φ 被滿足。∎

建構是 `O(m^2)`：3m 個點，兩兩檢查一次。程式：

```python
# reduce_is.py
import itertools
import random


def brute_sat(n, clauses):
    """窮舉 2^n 組賦值；回傳一組滿足賦值（dict var->bool），或 None。"""
    for bits in itertools.product([False, True], repeat=n):
        a = {i + 1: bits[i] for i in range(n)}
        if all(any(a[abs(l)] == (l > 0) for l in c) for c in clauses):
            return a
    return None


def sat_to_is(clauses):
    """3-SAT -> (adj, k)。每個子句一個三角形，互補文字之間連邊。"""
    nodes = [(j, p, lit) for j, c in enumerate(clauses) for p, lit in enumerate(c)]
    adj = {v: set() for v in nodes}
    for u, v in itertools.combinations(nodes, 2):
        if u[0] == v[0] or u[2] == -v[2]:  # 同一子句，或互補文字
            adj[u].add(v)
            adj[v].add(u)
    return adj, len(clauses)


def max_independent_set(adj):
    """精確最大獨立集：度數 <= 1 的點直接拿，否則在最大度數點上分支。"""
    def solve(alive):
        if not alive:
            return frozenset()
        deg = {v: len(adj[v] & alive) for v in alive}
        v = min(alive, key=lambda x: (deg[x], repr(x)))
        if deg[v] <= 1:  # 拿 v 不會比拿它的唯一鄰居差
            return solve(alive - {v} - adj[v]) | {v}
        v = max(alive, key=lambda x: (deg[x], repr(x)))
        without = solve(alive - {v})
        with_v = solve(alive - {v} - adj[v]) | {v}
        return with_v if len(with_v) >= len(without) else without
    return solve(frozenset(adj))


def random_3sat(n, m, rng):
    clauses = []
    for _ in range(m):
        vs = rng.sample(range(1, n + 1), 3)
        clauses.append(tuple(v if rng.random() < 0.5 else -v for v in vs))
    return clauses


if __name__ == "__main__":
    # φ = (x1 ∨ x2 ∨ ¬x3) ∧ (¬x1 ∨ x2 ∨ x3) ∧ (x1 ∨ ¬x2 ∨ ¬x3)
    phi = [(1, 2, -3), (-1, 2, 3), (1, -2, -3)]
    adj, k = sat_to_is(phi)
    print("節點", len(adj), "邊", sum(map(len, adj.values())) // 2, "k =", k)
    S = max_independent_set(adj)
    print("最大獨立集大小", len(S), sorted(S))
    print("讀回的(部分)賦值", {abs(l): l > 0 for (_, _, l) in S})

    rng = random.Random(34)
    stats = {"sat": 0, "unsat": 0}
    for trial in range(400):
        n, m = 4, rng.randint(6, 22)
        cl = random_3sat(n, m, rng)
        sat = brute_sat(n, cl) is not None
        g, k = sat_to_is(cl)
        S = max_independent_set(g)
        assert sat == (len(S) >= k), cl
        if len(S) >= k:  # ⇐ 方向：從獨立集讀回賦值，確認它真的滿足 φ
            a = {abs(l): l > 0 for (_, _, l) in S}
            assert all(any(a.get(abs(l)) == (l > 0) for l in c) for c in cl)
        stats["sat" if sat else "unsat"] += 1
    print("400 個隨機實例全部一致", stats)
```

```text
節點 9 邊 15 k = 3
最大獨立集大小 3 [(0, 0, 1), (1, 2, 3), (2, 1, -2)]
讀回的(部分)賦值 {3: True, 1: True, 2: False}
400 個隨機實例全部一致 {'sat': 353, 'unsat': 47}
```

15 條邊的來源：3 個三角形 9 條，加上互補對 x1/¬x1 兩條、x2/¬x2 兩條、x3/¬x3 兩條。`n = 4` 個變數、子句數 6 到 22，是刻意調的：子句太少幾乎全部可滿足，測不到 (⇐) 方向在 no 實例上的表現；這組參數產生了 47 個 UNSAT 實例。`max_independent_set` 的「度數 ≤ 1 直接拿」是正確的化簡：如果最佳解用了 v 的唯一鄰居 u，把 u 換成 v 還是獨立集、大小不變。

## 歸約二、三：Independent Set → Vertex Cover → Clique

這兩個歸約短到可以一起講，但都值得寫完。

**Vertex Cover（VC）**：有沒有 k 個點，碰到每一條邊？**引理**：S 是獨立集 ⇔ `V \ S` 是點覆蓋。證明：S 獨立 ⇔ 沒有邊兩端都在 S ⇔ 每條邊至少一端在 `V \ S`。所以 `(G, k) ∈ IS ⇔ (G, n − k) ∈ VC`，f 只改數字，`O(1)`。

**Clique**：有沒有 k 個兩兩相鄰的點？**引理**：S 在 G 中獨立 ⇔ S 在補圖 `Ḡ` 中是團（`Ḡ` 的邊恰好是 G 的非邊）。所以 `(G, k) ∈ IS ⇔ (Ḡ, k) ∈ Clique`，f 是取補圖，`O(n^2)`。

兩者都在 NP（紙條是點集）。所以 VC 與 Clique 都是 NP-complete。驗證時改用窮舉：n = 5 的所有 `2^10 = 1024` 張帶標號圖、每個 k：

```python
# reduce_vc_clique.py
import itertools
import networkx as nx


def all_graphs(n):
    """枚舉頂點 0..n-1 上所有 2^(n choose 2) 張帶標號簡單圖。"""
    pairs = list(itertools.combinations(range(n), 2))
    for mask in range(1 << len(pairs)):
        adj = {v: set() for v in range(n)}
        for b, (u, v) in enumerate(pairs):
            if mask >> b & 1:
                adj[u].add(v)
                adj[v].add(u)
        yield adj


def is_independent(adj, S):
    return all(v not in adj[u] for u, v in itertools.combinations(S, 2))


def is_vertex_cover(adj, C):
    return all(u in C or v in C for u in adj for v in adj[u])


def is_clique(adj, S):
    return all(v in adj[u] for u, v in itertools.combinations(S, 2))


def complement(adj):
    return {u: set(adj) - adj[u] - {u} for u in adj}


def exists(adj, k, pred):
    return any(pred(adj, set(S)) for S in itertools.combinations(adj, k))


n = 5
checked = 0
for adj in all_graphs(n):
    comp = complement(adj)
    alpha = max(k for k in range(n + 1) if exists(adj, k, is_independent))
    for k in range(n + 1):
        has_is = exists(adj, k, is_independent)
        has_vc = exists(adj, n - k, is_vertex_cover)   # 歸約 IS -> VC：k 換成 n-k
        has_cl = exists(comp, k, is_clique)            # 歸約 IS -> Clique：取補圖
        assert has_is == has_vc == has_cl
        checked += 1
    # 和 NetworkX 對答案：補圖的最大團 = 原圖的最大獨立集
    G = nx.Graph()
    G.add_nodes_from(adj)          # 先加點：孤立點也要在補圖裡
    G.add_edges_from((u, v) for u in adj for v in adj[u])
    clique, size = nx.max_weight_clique(nx.complement(G), weight=None)
    assert size == alpha
    checked += 1
print(f"n={n}: 1024 張圖、{checked} 個 (圖, k) 檢查全部通過")
```

```text
n=5: 1024 張圖、7168 個 (圖, k) 檢查全部通過
```

7168 = 1024 × (6 個 k 值 + 1 次 NetworkX 對拍)。註解「先加點」那行是我第一次寫錯的地方：原本寫 `nx.complement(nx.Graph(邊串列))` 再補點，孤立點在取補圖時根本不存在，補圖少了它連出去的所有邊，`assert size == alpha` 在第一張有孤立點的圖上就炸了。歸約程式裡「圖的點集」和「邊串列裡出現過的點」不是同一件事。

## 歸約四：3-SAT → 3-Coloring

**3-Coloring ∈ NP**：紙條是著色，檢查每條邊。**建構**分三層 gadget：

```text
調色盤：三角形 T─F─B─T。三個點必須三種顏色；我們把 T 的顏色叫「真」、F 的叫「假」。

變數：每個 x_i 有兩個點 x_i、¬x_i，連成三角形 x_i ─ ¬x_i ─ B。
      → 兩者都不是 B 的顏色、彼此不同：一個「真」一個「假」。

子句 (a ∨ b ∨ c)：兩個串接的 OR gadget，共 6 個新點 u0..u5
      a ─ u0      u0, u1, u2 三角形         u2 ─ u3      u3, u4, u5 三角形
      b ─ u1      （輸出 u2）               c  ─ u4      （輸出 u5）
      最後 u5 連到 F 和 B → u5 被逼成「真」
```

圖有 `3 + 2n + 6m` 個點，多項式大小。

**子句引理**：文字 a、b、c 的顏色限定在 {真, 假} 時，子句 gadget 可以合法著色 ⇔ a、b、c 不全是「假」。

**證明**。（全假 ⇒ 無法著色）a = b = 假，u0、u1 都不能是假，又彼此相鄰，所以 {u0, u1} = {真, B}，u2 被逼成假。接著 u3 ≠ u2 = 假、u4 ≠ c = 假，所以 {u3, u4} = {真, B}，u5 被逼成假；但 u5 和 F 相鄰，矛盾。

（有一個真 ⇒ 可著色，並且 u5 = 真）先看 c = 真。第一層永遠可著色（例如 a = b = 假時取 u0 = 真、u1 = B、u2 = 假；其他情況類似），u2 的顏色記為 y。第二層要 u5 = 真，需要 {u3, u4} = {假, B} 且 u3 ≠ y、u4 ≠ c = 真：y = 假時取 u3 = B、u4 = 假；y = B 時取 u3 = 假、u4 = B；y = 真時兩種都行。再看 c = 假而 a 或 b 為真。設 a = 真（b = 真對稱）：取 u0 = 假，u1 = B（若 b = 假）或 u1 = B（若 b = 真，B ≠ 真也成立），u2 = 真。第二層取 u3 = 假（≠ u2 = 真）、u4 = B（≠ c = 假）、u5 = 真。∎

**定理**：φ 可滿足 ⇔ G(φ) 可 3 著色。（⇒）依賦值給文字點上真/假色，調色盤三色，子句 gadget 依引理補色。（⇐）任取合法 3-著色，把顏色重新命名使 T、F、B 分別是「真、假、B」（顏色名稱可任意置換）；文字點只能是真或假；令「x_i 和 T 同色」為 x_i = 真。每個子句 gadget 都合法著色，由引理它的三個文字不全假，φ 被滿足。∎

程式裡的 3-著色解題器值得說一句：第一版是單純的回溯加前向檢查，n = 4、m = 20 的實例在 60 秒內跑不完（m = 12 時還只要 2 秒）。原因是子句 gadget 在文字定色之後彼此**獨立**，但回溯不知道：gadget 7 失敗時，它會回頭把 gadget 0–6 的所有內部選法重試一輪，指數爆炸。修法是每次分支前，把「還沒定色的點」切成連通分量，分量逐一解、失敗就整體失敗，不互相回溯。這個結構觀察在 [Ch 36](./36-treewidth.md) 會變成正式的工具（分量之間只隔著一個小分隔集）。

```python
# reduce_3col.py（需要同目錄的 reduce_is.py）
import itertools
import random
from reduce_is import brute_sat, random_3sat


def add_edge(adj, u, v):
    adj.setdefault(u, set()).add(v)
    adj.setdefault(v, set()).add(u)


def sat_to_3col(n, clauses):
    adj = {}
    # 調色盤三角形：T、F、B 的顏色就是「真」「假」「其他」
    add_edge(adj, "T", "F"); add_edge(adj, "F", "B"); add_edge(adj, "B", "T")
    lit = lambda l: ("x", l)  # 文字節點：("x", 3) 是 x3，("x", -3) 是 ¬x3
    for i in range(1, n + 1):
        add_edge(adj, lit(i), lit(-i))      # x 和 ¬x 不同色
        add_edge(adj, lit(i), "B")          # 都不能是 B，所以一個 T 一個 F
        add_edge(adj, lit(-i), "B")
    for j, (a, b, c) in enumerate(clauses):
        u = [("g", j, t) for t in range(6)]
        # 第一個 OR：輸入 a, b，輸出 u[2]
        add_edge(adj, lit(a), u[0]); add_edge(adj, lit(b), u[1])
        add_edge(adj, u[0], u[1]); add_edge(adj, u[0], u[2]); add_edge(adj, u[1], u[2])
        # 第二個 OR：輸入 u[2], c，輸出 u[5]
        add_edge(adj, u[2], u[3]); add_edge(adj, lit(c), u[4])
        add_edge(adj, u[3], u[4]); add_edge(adj, u[3], u[5]); add_edge(adj, u[4], u[5])
        # 輸出被逼成 T
        add_edge(adj, u[5], "F"); add_edge(adj, u[5], "B")
    return adj


def three_color(adj, fixed=None):
    """回溯 + 傳播：某點只剩一色就立刻定下來並刪掉鄰居的該色。回傳著色或 None。"""
    def assign(dom, v, c):
        stack = [(v, c)]
        while stack:
            v, c = stack.pop()
            if c not in dom[v]:
                return False
            dom[v] = {c}
            for w in adj[v]:
                if c in dom[w]:
                    dom[w] = dom[w] - {c}
                    if not dom[w]:
                        return False
                    if len(dom[w]) == 1:
                        stack.append((w, next(iter(dom[w]))))
        return True

    def components(dom, scope):
        """scope 裡未定色的點，依「只走未定色的點」切成連通分量。"""
        free = {v for v in scope if len(dom[v]) > 1}
        seen, comps = set(), []
        for s in free:
            if s in seen:
                continue
            comp, stack = [], [s]
            seen.add(s)
            while stack:
                v = stack.pop()
                comp.append(v)
                for w in adj[v]:
                    if w in free and w not in seen:
                        seen.add(w)
                        stack.append(w)
            comps.append(comp)
        return comps

    def solve(dom, scope):
        # 分量之間只透過已定色的點相連，彼此獨立：逐一解，失敗不必回頭重試別的分量
        for comp in components(dom, scope):
            dom = solve_comp(dom, comp)
            if dom is None:
                return None
        return dom

    def solve_comp(dom, comp):
        v = min(comp, key=lambda x: (len(dom[x]), -len(adj[x]), repr(x)))
        for c in sorted(dom[v]):
            d2 = dict(dom)
            if assign(d2, v, c):
                res = solve(d2, comp)
                if res is not None:
                    return res
        return None

    dom = {v: {0, 1, 2} for v in adj}
    for v, c in (fixed or {}).items():  # 固定的點逐一指定並傳播
        if not assign(dom, v, c):
            return None
    res = solve(dom, list(adj))
    return {v: next(iter(d)) for v, d in res.items()} if res else None


if __name__ == "__main__":
    # 1) 子句 gadget 的真值表：輸入顏色限定在 {T, F}，看 gadget 能不能 3 著色
    T, F, B = 0, 1, 2
    for bits in itertools.product([T, F], repeat=3):
        adj = sat_to_3col(3, [(1, 2, 3)])
        fixed = {"T": T, "F": F, "B": B}
        fixed.update({("x", i + 1): bits[i] for i in range(3)})
        fixed.update({("x", -(i + 1)): F if bits[i] == T else T for i in range(3)})
        ok = three_color(adj, fixed) is not None
        print("abc =", "".join("T" if b == T else "F" for b in bits), "可著色" if ok else "不可著色")

    # 2) 隨機 3-SAT：φ 可滿足 ⇔ G(φ) 可 3 著色
    rng = random.Random(343)
    stats = {"sat": 0, "unsat": 0}
    for trial in range(300):
        n, m = 4, rng.randint(8, 26)
        cl = random_3sat(n, m, rng)
        sat = brute_sat(n, cl) is not None
        g = sat_to_3col(n, cl)
        col = three_color(g)
        assert sat == (col is not None), cl
        if col is not None:  # ⇐ 方向：和 T 同色的文字設為真
            assert all(col[u] != col[v] for u in g for v in g[u])
            a = {i: col[("x", i)] == col["T"] for i in range(1, n + 1)}
            assert all(any(a[abs(l)] == (l > 0) for l in c) for c in cl)
        stats["sat" if sat else "unsat"] += 1
    print("300 個隨機實例全部一致", stats)
    print("節點數公式 3 + 2n + 6m，例如 n=4, m=20 ->", len(sat_to_3col(4, random_3sat(4, 20, rng))))
```

```text
abc = TTT 可著色
abc = TTF 可著色
abc = TFT 可著色
abc = TFF 可著色
abc = FTT 可著色
abc = FTF 可著色
abc = FFT 可著色
abc = FFF 不可著色
300 個隨機實例全部一致 {'sat': 244, 'unsat': 56}
節點數公式 3 + 2n + 6m，例如 n=4, m=20 -> 131
```

前八行是子句引理的機器版：gadget 就是一個 OR 閘的真值表。`3 + 2·4 + 6·20 = 131`，和公式一致。

## 歸約五：3-SAT → Hamilton cycle（證明架構）

Hamilton cycle 的 NP-completeness 通常分三步，我們全部實作，但證明只寫到架構層級：

```text
3-SAT ──(鑽石 gadget)──▶ 有向 Hamilton path (s→t)
      ──(每點拆三)──────▶ 無向 Hamilton path
      ──(加一點 z 連 s,t)▶ 無向 Hamilton cycle
```

**第一步：鑽石**（Sipser 教科書的構造）。每個變數 x_i 一顆鑽石：頂點 top_i、底點 bot_i，中間一排 `3m + 1` 個點，排內相鄰點雙向相連；top_i 可以進入這排的最左或最右端，兩端都能出到 bot_i；bot_i 接到 top_{i+1}。s = top_1、t = bot_n。由左往右走這排代表 x_i = 真，由右往左代表假。排裡第 `3j+1, 3j+2` 兩點是保留給子句 j 的一對，中間夾分隔點，讓不同子句的繞道互不干擾。每個子句一個點 c_j：若 x_i 在 c_j 中正向出現，加 `r[3j+1] → c_j → r[3j+2]`（只有由左往右走時能順路繞過去）；若 ¬x_i 出現，加 `r[3j+2] → c_j → r[3j+1]`（由右往左時能繞）。

```text
          top_i
         ↙     ↘
  r0 ⇄ r1 ⇄ r2 ⇄ r3 ⇄ r4 ⇄ r5 ⇄ r6        m = 2：一排 7 點
       ↓↑   ↑↓                            r1,r2 留給子句 0；r4,r5 留給子句 1
        c_0                               r0, r3, r6 是分隔點
         ↘     ↙
          bot_i
```

（⇒）有滿足賦值時，依真假決定每顆鑽石的方向，每個子句挑一個為真的文字，在那個變數的那一排繞去 c_j 再回來，得到一條 s 到 t 的 Hamilton 路徑。（⇐）的關鍵引理是：**Hamilton 路徑從某排的 r[3j+1] 跳到 c_j，必須從同一排的 r[3j+2] 回來**；否則 r[3j+2] 的鄰居（r[3j+1] 已走過、分隔點 r[3j+3]）會讓它變成死路。有了這個引理，路徑在每顆鑽石內只能整排同方向走，方向就是賦值，每個 c_j 被某個方向正確的文字拜訪，所以賦值滿足 φ。引理的細節要逐格討論兩個方向與分隔點，這裡不展開，Sipser 的書寫得完整。

**第二步：有向 → 無向**。每點 v 拆成 `v_in − v_mid − v_out` 的路徑，有向邊 `u → v` 變成無向邊 `u_out − v_in`。v_mid 度數 2，任何 Hamilton 路徑都必須走 `v_in, v_mid, v_out` 這三連（或反過來），這就把方向「焊」進了無向圖。**第三步：path → cycle**。加一點 z 連 s_in 與 t_out：z 度數 2，Hamilton 圈必經 `s_in … t_out`，拿掉 z 就是 s 到 t 的 Hamilton 路徑。

```python
# reduce_ham.py（需要同目錄的 reduce_is.py）
import random
from reduce_is import brute_sat, random_3sat


def sat_to_dham(n, clauses):
    """3-SAT -> 有向圖 succ 與起終點 (s, t)。每個變數一顆鑽石，中間一排節點。"""
    m = len(clauses)
    L = 3 * m + 1                       # 一排的節點數：m 對 + (m+1) 個分隔點
    succ = {}

    def edge(u, v):
        succ.setdefault(u, set()).add(v)
        succ.setdefault(v, set())

    for i in range(1, n + 1):
        top, bot = ("top", i), ("bot", i)
        row = [("r", i, p) for p in range(L)]
        edge(top, row[0]); edge(top, row[-1])      # 從左進或從右進
        edge(row[0], bot); edge(row[-1], bot)
        for p in range(L - 1):                     # 一排內雙向
            edge(row[p], row[p + 1]); edge(row[p + 1], row[p])
        if i < n:
            edge(bot, ("top", i + 1))              # 鑽石串起來
        for j, c in enumerate(clauses):
            a, b = row[3 * j + 1], row[3 * j + 2]  # 子句 j 在第 i 排佔的那一對
            if i in c:                             # x_i 正向出現：由左往右走時可繞道
                edge(a, ("c", j)); edge(("c", j), b)
            if -i in c:                            # ¬x_i 出現：由右往左走時可繞道
                edge(b, ("c", j)); edge(("c", j), a)
    return succ, ("top", 1), ("bot", n)


def ham_path(succ, s, t):
    """有向 Hamilton 路徑（s 到 t）回溯搜尋。"""
    N = len(succ)
    pred = {v: set() for v in succ}
    for u in succ:
        for v in succ[u]:
            pred[v].add(u)
    path, seen = [s], {s}

    def dead_end(u):
        """剪枝：還沒走到的點必須還有路進得去、也還有路出得來。"""
        for w in succ:
            if w in seen:
                continue
            if not any(x == u or x not in seen for x in pred[w]):
                return True
            if w != t and not any(x not in seen for x in succ[w]):
                return True
        return False

    def dfs(u):
        if len(path) == N:
            return u == t
        if dead_end(u):
            return False
        for v in sorted(succ[u], key=repr):
            if v in seen or (v == t and len(path) != N - 1):
                continue
            seen.add(v); path.append(v)
            if dfs(v):
                return True
            seen.discard(v); path.pop()
        return False

    return list(path) if dfs(s) else None


def to_undirected(succ, s, t):
    """每點 v 拆成 v_in - v_mid - v_out；有向邊 u->v 變成無向邊 u_out - v_in。
    再加一個點 z 連 s_in 與 t_out，把「s 到 t 的路徑」變成「圈」。"""
    adj = {}

    def e(a, b):
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    for v in succ:
        e((v, "in"), (v, "mid")); e((v, "mid"), (v, "out"))
    for u in succ:
        for v in succ[u]:
            e((u, "out"), (v, "in"))
    e("z", (s, "in")); e("z", (t, "out"))
    return adj


def ham_cycle(adj):
    """無向 Hamilton 圈回溯：固定從某點出發，度數 2 的中間點讓搜尋被強迫前進。"""
    N = len(adj)
    start = "z"
    path, seen = [start], {start}

    def dead_end(u):
        """剪枝：還沒走到的點至少要有兩個「還用得到」的鄰居（未走過、目前端點或起點）。"""
        for w in adj:
            if w not in seen and sum(x not in seen or x == u or x == start for x in adj[w]) < 2:
                return True
        return False

    def dfs(u):
        if len(path) == N:
            return start in adj[u]
        if dead_end(u):
            return False
        for v in sorted(adj[u], key=repr):
            if v not in seen:
                seen.add(v); path.append(v)
                if dfs(v):
                    return True
                seen.discard(v); path.pop()
        return False

    return list(path) if dfs(start) else None


def random_cnf(n, m, rng):
    """子句寬度 1~3 的隨機 CNF（歸約對任何 CNF 都成立；短子句讓小實例也會出現 UNSAT）。"""
    out = []
    for _ in range(m):
        vs = rng.sample(range(1, n + 1), rng.randint(1, 3))
        out.append(tuple(v if rng.random() < 0.5 else -v for v in vs))
    return out


if __name__ == "__main__":
    phi = [(1, 2, -3), (-1, -2, 3)]
    succ, s, t = sat_to_dham(3, phi)
    print("有向圖節點", len(succ), "邊", sum(map(len, succ.values())))
    p = ham_path(succ, s, t)
    # 讀回賦值：第 i 排從左端 r[0] 進去就是 x_i = 真
    a = {i: p[p.index(("top", i)) + 1] == ("r", i, 0) for i in range(1, 4)}
    print("讀回賦值", a)
    U = to_undirected(succ, s, t)
    print("無向圖節點", len(U), "邊", sum(map(len, U.values())) // 2,
          "有 Hamilton 圈:", ham_cycle(U) is not None)

    rng = random.Random(3434)
    stats = {"sat": 0, "unsat": 0}
    for trial in range(200):
        n, m = 3, rng.randint(1, 4)
        cl = random_cnf(n, m, rng)
        sat = brute_sat(n, cl) is not None
        succ, s, t = sat_to_dham(n, cl)
        p = ham_path(succ, s, t)
        assert sat == (p is not None), cl
        if p:
            a = {i: p[p.index(("top", i)) + 1] == ("r", i, 0) for i in range(1, n + 1)}
            assert all(any(a[abs(l)] == (l > 0) for l in c) for c in cl)
        if m <= 2:  # 無向版節點數變三倍，只驗小的
            assert sat == (ham_cycle(to_undirected(succ, s, t)) is not None)
        stats["sat" if sat else "unsat"] += 1
    print("200 個隨機實例全部一致", stats)
```

```text
有向圖節點 29 邊 62
讀回賦值 {1: True, 2: True, 3: True}
無向圖節點 88 邊 122 有 Hamilton 圈: True
200 個隨機實例全部一致 {'sat': 187, 'unsat': 13}
```

數字對得上：每顆鑽石 `2 + (3·2+1) = 9` 點，三顆 27 點，加 2 個子句點 = 29。無向版 `3 × 29 + 1 = 88` 點，邊 = `2 × 29`（拆點內部）`+ 62`（原有向邊）`+ 2`（z）= 122。

這裡有兩個誠實的限制要說。第一，這組隨機實例刻意縮小（3 個變數、最多 4 個子句、子句寬 1 到 3）：我第一次用 m = 7 的 3-SAT 實例，單一個有向 Hamilton path 搜尋就跑了 23 秒，因為每一排「要不要繞去 c_j」的選擇在失敗的分支上會相乘。歸約是多項式的，**解歸約後的實例**當然還是指數的，這正是 NP-complete 的意思。第二，寬度 1、2 的子句讓小實例也出現 UNSAT（3 個變數的純 3-SAT 要 8 個子句才會不可滿足）；鑽石構造對任何 CNF 都成立，所以這樣測是合法的。

## 對比與取捨

| 問題 | 判定版難度 | 在哪些圖類變簡單 | 本課相關章 |
| --- | --- | --- | --- |
| Independent Set / Clique | NP-complete | 二分圖（König）、chordal、treewidth 有界 | [Ch 22](./22-bipartite-matching.md)、[Ch 16](./16-chordal-graphs.md)、[Ch 36](./36-treewidth.md) |
| Vertex Cover | NP-complete | 二分圖：`τ = ν`，用匹配算 | [Ch 22](./22-bipartite-matching.md) |
| 3-Coloring | NP-complete | 2-著色是 BFS；chordal 用完美消去序 | [Ch 15](./15-graph-coloring.md)、[Ch 16](./16-chordal-graphs.md) |
| Hamilton cycle | NP-complete | Dirac 條件（`δ ≥ n/2`）保證存在 | [Ch 8](./08-euler-hamilton.md) |
| Euler 迴路 | P（線性） | 全部 | [Ch 8](./08-euler-hamilton.md) |
| 最大匹配 | P | 全部（Blossom） | [Ch 23](./23-general-matching-blossom.md) |
| 圖同構 | 不知道是 P 還是 NP-complete | 平面圖、度數有界 | [Ch 37](./37-graph-isomorphism-wl.md) |

匹配和獨立集長得很像（都是「挑一組互不衝突的東西」），一個在 P、一個 NP-complete。差別在結構：匹配的衝突關係是 line graph，有 augmenting path 這種局部改良的證書；一般圖的獨立集沒有。這也是為什麼 [Ch 25](./25-matroids.md) 的 matroid 能讓貪婪法成功，而獨立集的集合族不是 matroid。

## 踩雷集錦

1. **歸約方向寫反**：想證 B 很難，卻寫了 `B ≤p A`（把 B 翻譯成已知難的 A）。這只證明了「B 不比 A 難」，什麼都沒說。正確方向：**已知難的 A ≤p 你的 B**。口訣：從難的那邊出發，翻譯到你要證的問題。
2. **只證一個方向**：寫完 (⇒) 就收工。(⇐) 才是 gadget 會被作弊的地方：3-著色歸約如果少了「文字點連到 B」那條邊，文字點可以塗成 B，子句 gadget 就能在「全假」時被繞過。我實際把那兩行拿掉重跑：最先炸的是「讀回賦值」那個 assert（解題器找到了著色，但讀回來的賦值不滿足 φ）；再單獨數一次，300 個實例裡的 56 個 UNSAT 實例**全部**變成可 3 著色。只檢查 (⇒) 的測試完全抓不到這個 bug。
3. **把「NP」讀成「非多項式」**：NP 包含 P。說「這題是 NP 所以很慢」是錯的；說「這題是 NP-hard，除非 P = NP 否則沒有多項式演算法」才對。
4. **以為 NP-complete 就是每個實例都難**：NP-complete 是最壞情況的陳述。二分圖的 vertex cover 用 König 定理是多項式（[Ch 22](./22-bipartite-matching.md)）；你手上的 interference graph 可能是 chordal（[Ch 17](./17-register-allocation.md)）；treewidth 小的圖上幾乎所有這些問題都是線性時間（[Ch 36](./36-treewidth.md)）。先看你的實例有什麼結構。
5. **輸入大小算錯**：「背包問題有 `O(nW)` 的 DP，所以在 P」是錯的，W 用 `log W` 位元編碼，`O(nW)` 是輸入長度的指數，這叫偽多項式（pseudo-polynomial）。圖問題比較少踩到，但帶權重的圖（例如帶權 Steiner tree）會。
6. **歸約本身不是多項式**：把 3-SAT 歸約到某問題時枚舉了所有 `2^n` 組賦值當 gadget，大小就爆了，這不是合法歸約。每個建構都要數一下大小：這章分別是 `3m` 點、`3 + 2n + 6m` 點、`n(3m + 3) + m` 點。

## 進階：再往深一層

- **Ladner 定理（1975）**：如果 P ≠ NP，就存在既不在 P、也不是 NP-complete 的 NP 問題（NP-intermediate）。這是存在性結果，構造出來的問題很人工。自然的候選人裡最有名的就是圖同構，[Ch 37](./37-graph-isomorphism-wl.md) 的主題。
- **Parsimonious reduction**：如果歸約還保持「解的個數」，它同時證明了計數版（#SAT → #IS）是 #P-hard。我們的 3-SAT → IS 歸約**不**保持個數（一個賦值可能讓一個子句有好幾個真文字可挑），想想怎麼改。
- **近似難度**：Clique 不只判定版難，連近似到 `n^(1−ε)` 倍都是 NP-hard（PCP 定理之後的結果，Håstad、Zuckerman）；VC 卻有簡單的 2-近似（取極大匹配的所有端點）。歸約有沒有保持「差距」決定了近似難度能不能傳遞，這是 PCP 理論的入口。
- **Fine-grained 與 ETH**：Exponential Time Hypothesis 假設 3-SAT 沒有 `2^(o(n))` 演算法。在 ETH 下，我們的歸約（大小線性於 n + m）可以推出 IS、VC、3-著色都沒有 `2^(o(n))` 演算法。這比「NP-hard」說得更精細，也是 [Ch 36](./36-treewidth.md) 的「`2^tw` 是最佳的」那類下界的來源。

## 動手練習

1. 在 `sat_to_3col` 裡把 `add_edge(adj, lit(i), "B")` 與 `add_edge(adj, lit(-i), "B")` 兩行註解掉，重跑，觀察哪個 assert 先失敗，並解釋那個「作弊著色」長什麼樣子。
2. 寫 3-SAT → Vertex Cover 的**直接**歸約（不經過 IS）：每個變數一條邊 `x − ¬x`，每個子句一個三角形，三角形的點連到對應的文字點，`k = n + 2m`。證明兩個方向，再用 `brute_sat` 對拍。
3. 改寫 `sat_to_dham`，拿掉分隔點（一排 `2m` 點，子句 j 佔第 `2j, 2j+1` 兩點），用同一組隨機實例對拍。如果出現「有 Hamilton path 但原式不可滿足」，把那條路徑印出來，指出它在哪一步違反了「從 c_j 必須回到同一排」的引理；如果一直對拍成功，試著說明分隔點在證明的哪一步才真正被用到（這題我沒有給答案，是開放題）。

## 本章重點整理

- NP 是「yes 實例有短證書、能多項式時間驗證」的判定問題；NP-complete = 在 NP 裡 + 所有 NP 問題都能歸約過來。
- 歸約要交三件事：多項式建構、(⇒) 解的翻譯、(⇐) 從新解讀回舊解；(⇐) 是 gadget 被作弊的地方。
- 3-SAT → IS 用「子句三角形 + 互補邊」；IS ↔ VC 是取補集；IS ↔ Clique 是取補圖；3-著色用調色盤 + OR gadget；Hamilton 用鑽石，再拆點變無向、加點變成圈。
- NP-complete 是最壞情況；實際的圖常常有能被利用的結構，這是 Ch 35–36 的出發點。

## 自我檢核

- [ ] 不看筆記，能寫出 NP 的驗證器定義，並解釋為什麼它對 yes 和 no 不對稱
- [ ] 有人說「我把我的排程問題歸約到 SAT 了，所以它是 NP-complete」，你能指出錯在哪裡嗎？
- [ ] 能在白板上畫出 3-SAT → 3-Coloring 的子句 gadget，並說明「全假時為什麼塗不出來」
- [ ] 能說明 Hamilton 鑽石構造裡分隔點在 (⇐) 方向的作用
- [ ] 想一個你工作上遇到的「找不到快速解」的問題，它是哪個 NP-complete 問題的偽裝？有沒有能利用的結構？

## 延伸閱讀

### 論文

- **[The Complexity of Theorem-Proving Procedures](https://www.cs.umd.edu/~gasarch/COURSES/452/F14/cookpaper.pdf)** — Stephen A. Cook，STOC 1971，pp. 151–158
  - **核心貢獻**：證明任何非決定性多項式時間可接受的集合都能多項式時間歸約到「DNF tautology」問題（它是 SAT 的補問題），NP-completeness 的起點。Cook 用的是允許多次查詢的歸約（今天叫 Cook / Turing reduction），比本章的 Karp reduction 寬鬆。
  - **讀哪裡**：Theorem 1 的證明是本章 tableau 架構的原版；篇幅只有 8 頁，記號比現代教科書舊，對照 Sipser 讀。
  - **和本章的關聯**：本章所有歸約都追溯到這篇。
- **Reducibility Among Combinatorial Problems** — Richard M. Karp，收於 *Complexity of Computer Computations*（Miller、Thatcher 編，Plenum，1972），pp. 85–103
  - **核心貢獻**：用歸約把 21 個組合問題（Clique、Vertex Cover、Chromatic Number、Hamilton Circuit……）證成 NP-complete，讓「NP-complete」從一個邏輯問題變成整個演算法界的工具。
  - **讀哪裡**：歸約樹那張圖與 Clique、Node Cover、Chromatic Number 三段；本章的 IS/VC/Clique 鏈就是它的現代版。
  - **前提**：本章的定義節。

### 書籍

- **《Introduction to the Theory of Computation》** — Michael Sipser（Cengage，第 3 版，2012）
  - **這本書的定位**：NP-completeness 最好讀的教科書處理，證明寫得完整又不冗長。
  - **讀哪幾章**：第 7 章（Time Complexity）。7.4 節是 Cook–Levin 的完整證明；7.5 節（Additional NP-Complete Problems）有 3-SAT → VERTEX-COVER 與本章用的 HAMPATH 鑽石構造，(⇐) 方向的逐格討論就在那裡。
- **《Computers and Intractability: A Guide to the Theory of NP-Completeness》** — Michael R. Garey、David S. Johnson（W. H. Freeman，1979）
  - **這本書的定位**：四十多年仍在用的 NP-complete 問題目錄；附錄有約 300 個問題，每個標註來源與已知的多項式特例。年代久，但目錄部分仍然適用；近似與參數化的結果要另找新資料。
  - **讀哪幾章**：第 3 章（六個基本 NP-complete 問題的證明）；附錄 A1（Graph Theory）當查表用。
- **《Computational Complexity: A Modern Approach》** — Sanjeev Arora、Boaz Barak（Cambridge University Press，2009）
  - **這本書的定位**：研究導向的複雜度教科書，NP 用驗證器定義（和本章一致）。
  - **讀哪幾章**：第 2 章（NP and NP completeness）；想看 PCP 與近似難度，跳到第 11 章附近。作者在 Princeton 放過早期草稿，正式版以出版品為準。

Ch 34 證明了這些問題「很難」。下一章轉身面對現實：既然它們全都能歸約到 SAT，而現代 SAT solver 又很強，那就把圖問題編碼成 CNF 交給 solver，並且量一量編碼方式對速度的影響有多大。

→ [Ch 35 把圖問題歸約到 SAT](./35-reductions-to-sat.md)
