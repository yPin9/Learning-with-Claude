# Ch 35 — 把圖問題歸約到 SAT

> **目標**：能把 k-coloring、Hamilton cycle、clique 寫成 CNF 並交給 z3 解，解碼回圖上的答案並驗證；理解 at-most-one 與基數約束的幾種編碼、對稱破壞為什麼能把 100 秒變成 5 秒，並用實測數字比較編碼大小與求解時間。
>
> **環境**：Python 3.13.12、NetworkX 3.7、z3-solver 5.3.3.0，Windows 11（AMD Ryzen 7 5700G）。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2 z3-solver==5.3.3.0`。跑範例前設 `PYTHONUTF8=1`。本章所有秒數都是這台機器單次執行的 `Solver.check()` 時間，不含 Python 端建構；重跑會有幾成的浮動，數量級才是重點。

## 為什麼需要這個？

[Ch 34](./34-np-complete-reductions.md) 把歸約當成**證明工具**：從 3-SAT 出發，證明圖問題很難。這章把箭頭反過來，當成**工程工具**：從圖問題出發，翻譯成 SAT，交給 solver。Cook–Levin 保證這永遠做得到（所有 NP 問題都能歸約到 SAT），問題只在於翻譯得好不好。

為什麼值得這樣做，而不是自己寫回溯？Ch 34 我們寫了一個 3-著色回溯器，花了兩個版本才讓它在 131 個點的圖上跑得動。現代 CDCL solver（conflict-driven clause learning）把三十年的工程累積在一起：衝突分析學到的子句、非時序回跳、重啟策略、watched literals。你的回溯器學不到「上次在這裡失敗的原因」，CDCL 會。代價是你得把問題寫成它聽得懂的語言：一堆子句的 AND，每個子句是文字的 OR。

這門課的 SAT 背景由 [programming/sat_smt](../../programming/sat_smt/README.md) 這門課負責：CNF 與 DIMACS 格式在它的 [Ch 8 — SAT 問題與 DIMACS 格式](../../programming/sat_smt/08-sat-problem-dimacs.md)，CDCL 的內部在 [Ch 13 — CDCL 核心](../../programming/sat_smt/13-cdcl-core.md)。這章不重講 solver 怎麼運作，只講**圖問題怎麼編碼**，以及編碼的選擇怎麼影響 solver。

## 先建立直覺

把 CNF 編碼想成寫一份「規則清單」，solver 是一個非常會推理、但完全不懂圖的助理。你得用三種句型把圖說清楚：

```text
句型                         子句長相                  圖問題裡的用途
--------------------------------------------------------------------------------
至少一個 (at-least-one)      (a ∨ b ∨ c)               每個點至少一種顏色
至多一個 (at-most-one, AMO)  (¬a ∨ ¬b)(¬a ∨ ¬c)(¬b ∨ ¬c)  每個點至多一種顏色
衝突 (conflict)              (¬x ∨ ¬y)                 相鄰兩點不能同色
```

幾乎所有圖問題的編碼都是這三種句型的組合，差別在於**變數代表什麼**。同一個問題可以有完全不同的變數設計，大小與速度可以差上千倍。這章的三個問題各展示一個教訓：

```text
k-coloring      → 對稱：k! 種顏色排列都是同一個解，unsat 時 solver 要把它們全部否決
Hamilton cycle  → 大小：n² 個變數、O(n³) 子句；AMO 怎麼寫決定子句數
clique          → 變數設計：「選哪些點」vs「第 i 格放誰」，unsat 時差 300 倍
```

## 核心：一個最小的 CNF 容器與 k-coloring

變數設計：`x(v, c)` = 「點 v 塗顏色 c」，共 `n·k` 個變數。子句：

- 每個點至少一色：`(x(v,0) ∨ … ∨ x(v,k−1))`，n 條
- 每個點至多一色：每對顏色 `(¬x(v,c1) ∨ ¬x(v,c2))`，`n·C(k, 2)` 條
- 相鄰不同色：每條邊、每個顏色 `(¬x(u,c) ∨ ¬x(v,c))`，`m·k` 條

我們自己管變數編號（DIMACS 的正整數），再把 DIMACS 文字交給 z3。z3 的 `Solver.from_string` 吃得下 DIMACS，DIMACS 變數 i 在 z3 的 model 裡叫 `k!i`（我在 z3 5.3.3.0 上實測確認這個命名）。

```python
# cnf_color.py
import itertools
import time
import z3


class CNF:
    """最小的 CNF 容器：變數是正整數，文字是 ±變數（和 DIMACS 一樣）。"""
    def __init__(self):
        self.nvars, self.clauses, self.names = 0, [], {}

    def var(self, name):
        if name not in self.names:
            self.nvars += 1
            self.names[name] = self.nvars
        return self.names[name]

    def add(self, *lits):
        self.clauses.append(lits)

    def dimacs(self):
        lines = [f"p cnf {self.nvars} {len(self.clauses)}"]
        lines += [" ".join(map(str, c)) + " 0" for c in self.clauses]
        return "\n".join(lines) + "\n"

    def solve(self, timeout_ms=None):
        """把 DIMACS 文字餵給 z3；DIMACS 變數 i 在 z3 裡叫 k!i。
        self.t_check 只記 check() 的時間，不含 Python 端建構。"""
        s = z3.Solver()
        if timeout_ms:
            s.set("timeout", timeout_ms)
        s.from_string(self.dimacs())
        t = time.perf_counter()
        r = s.check()
        self.t_check = time.perf_counter() - t
        if r != z3.sat:
            return str(r)
        mdl = s.model()
        val = {d.name(): z3.is_true(mdl[d]) for d in mdl.decls()}
        # 沒出現在 model 裡的變數不影響可滿足性，一律當 False
        return {name: val.get(f"k!{i}", False) for name, i in self.names.items()}


def encode_coloring(adj, k, amo=True, symmetry=None):
    f = CNF()
    x = lambda v, c: f.var(("x", v, c))
    for v in adj:
        f.add(*[x(v, c) for c in range(k)])                 # 至少一色
        if amo:
            for c1, c2 in itertools.combinations(range(k), 2):
                f.add(-x(v, c1), -x(v, c2))                 # 至多一色
    for u in adj:
        for v in adj[u]:
            if repr(u) < repr(v):
                for c in range(k):
                    f.add(-x(u, c), -x(v, c))               # 相鄰不同色
    if symmetry:                                            # 把一個團的顏色固定死
        for c, v in enumerate(symmetry[:k]):
            f.add(x(v, c))
    return f


def decode_coloring(model, adj, k):
    # 沒有 AMO 時某點可能被標了好幾色：挑最小的那個
    return {v: min(c for c in range(k) if model[("x", v, c)]) for v in adj}


if __name__ == "__main__":
    import networkx as nx
    G = nx.petersen_graph()
    adj = {u: set(G[u]) for u in G}
    for k in (2, 3):
        f = encode_coloring(adj, k)
        r = f.solve()
        print(f"Petersen, k={k}: vars={f.nvars} clauses={len(f.clauses)} ->",
              r if isinstance(r, str) else "sat")
        if not isinstance(r, str):
            col = decode_coloring(r, adj, k)
            assert all(col[u] != col[v] for u in adj for v in adj[u])
            print("  著色", col)
    f = encode_coloring(adj, 3, amo=False)
    r = f.solve()
    col = decode_coloring(r, adj, 3)
    multi = sum(1 for v in adj if sum(r[("x", v, c)] for c in range(3)) > 1)
    print("不加 AMO: clauses =", len(f.clauses), "被標多色的點數 =", multi,
          "解碼後合法:", all(col[u] != col[v] for u in adj for v in adj[u]))
    print(encode_coloring({0: {1}, 1: {0}}, 2).dimacs(), end="")
```

```text
Petersen, k=2: vars=20 clauses=50 -> unsat
Petersen, k=3: vars=30 clauses=85 -> sat
  著色 {0: 0, 1: 2, 2: 0, 3: 1, 4: 2, 5: 2, 6: 1, 7: 1, 8: 0, 9: 0}
不加 AMO: clauses = 55 被標多色的點數 = 0 解碼後合法: True
p cnf 4 6
1 2 0
-1 -2 0
3 4 0
-3 -4 0
-1 -3 0
-2 -4 0
```

數一下子句：Petersen 圖 10 點 15 邊，k = 3 時 `10 + 10·3 + 15·3 = 85`，k = 2 時 `10 + 10·1 + 15·2 = 50`。Petersen 有 5-cycle，所以 2-著色 unsat，和 [Ch 15](./15-graph-coloring.md) 的「二分 ⇔ 沒有奇圈」一致。

「不加 AMO」那行值得想清楚。對著色問題，AMO 子句**不是必要的**：如果 solver 給某點標了兩種顏色，兩種都和鄰居不衝突（衝突子句對每個顏色都成立），挑任一種都合法。所以拿掉 AMO 是等價可滿足（equisatisfiable）的編碼，子句從 85 降到 55。這次 z3 碰巧沒有標多色的點，但這**不保證**，所以 `decode_coloring` 必須挑一個，不能假設恰好一個。這是編碼設計的常見手法：**只加為了正確性必要的約束**，其餘留給解碼。反例是 Hamilton cycle：那裡的 AMO 拿掉就錯了（一個點佔兩個位置，路徑就會重複經過它）。

## 底層機制：為什麼對稱會殺死 solver

一張 k-著色的圖，把顏色任意置換，還是合法著色：每個解有 `k!` 個分身。sat 的時候無所謂（找到一個就停）；**unsat 的時候災難**：solver 要證明「沒有解」，等於要把每個分身都否決，學到的衝突子句也常常只對某一種顏色命名有效。

```text
無對稱破壞：solver 在 5! = 120 個「其實等價」的子空間各自撞牆
              ┌────┬────┬────┬─ ... ─┬────┐
              │ σ1 │ σ2 │ σ3 │       │σ120│   每格都要獨立證明 unsat
              └────┴────┴────┴─ ... ─┴────┘
固定一個團的顏色：只剩一個代表
              ┌────┐
              │ σ1 │
              └────┘
```

最常用、也一定正確的破壞法：**找一個團 K（大小 q），把它的點分別固定成顏色 0, 1, …, q−1**。正確性：團上的點兩兩不同色，任何合法著色經過一次顏色置換都能讓團上恰好是 0..q−1，所以固定之後可滿足性不變。下面用 Mycielski 圖（三角形自由但色數任意大，NetworkX 的 `mycielski_graph(n)` 色數是 n）與隨機圖實測：

```python
# sym_color.py（需要同目錄的 cnf_color.py）
import networkx as nx
from cnf_color import encode_coloring


def run(name, G, k):
    adj = {u: set(G[u]) for u in G}
    clique, _ = nx.max_weight_clique(G, weight=None)
    for sb in (False, True):
        f = encode_coloring(adj, k, symmetry=sorted(clique) if sb else None)
        r = f.solve(timeout_ms=300_000)
        print(f"{name:22s} k={k} {'固定團顏色' if sb else '無對稱破壞':6s} "
              f"clauses={len(f.clauses):5d} {r if isinstance(r, str) else 'sat':5s} "
              f"check {f.t_check:8.3f}s")


for n in (4, 5, 6):
    G = nx.convert_node_labels_to_integers(nx.mycielski_graph(n))
    run(f"Mycielski M{n} ({G.number_of_nodes()} 點)", G, n - 1)   # χ(M_n) = n
for seed in (1, 2):
    run(f"G(50, 0.5) seed={seed}", nx.gnp_random_graph(50, 0.5, seed=seed), 8)
```

```text
Mycielski M4 (11 點)    k=3 無對稱破壞  clauses=  104 unsat check    0.001s
Mycielski M4 (11 點)    k=3 固定團顏色  clauses=  106 unsat check    0.000s
Mycielski M5 (23 點)    k=4 無對稱破壞  clauses=  445 unsat check    0.017s
Mycielski M5 (23 點)    k=4 固定團顏色  clauses=  447 unsat check    0.002s
Mycielski M6 (47 點)    k=5 無對稱破壞  clauses= 1697 unsat check   80.463s
Mycielski M6 (47 點)    k=5 固定團顏色  clauses= 1699 unsat check    5.346s
G(50, 0.5) seed=1      k=8 無對稱破壞  clauses= 6146 unsat check   17.283s
G(50, 0.5) seed=1      k=8 固定團顏色  clauses= 6154 unsat check    0.006s
G(50, 0.5) seed=2      k=8 無對稱破壞  clauses= 6298 unsat check    8.376s
G(50, 0.5) seed=2      k=8 固定團顏色  clauses= 6306 unsat check    0.011s
```

這整支跑完約兩分鐘，大部分花在 M6 無對稱破壞那行（另一次執行是 101.8 秒，浮動不小）。兩個觀察：

1. **Mycielski 圖是三角形自由的**，最大團只有一條邊（2 點），我們只固定了 2 個點的顏色，`5!` 的對稱只破掉 `5·4 = 20` 倍，實測快了約 15 倍，量級吻合。
2. **G(50, 0.5) 的最大團有 8 點**（`max_weight_clique` 回報），k = 8 時整個團的顏色全被固定，`8!` 的對稱全破，從 17 秒降到 6 毫秒。這也說明 unsat 的原因多半就在團附近：團吃掉全部 8 色，剩下的點在團的鄰域被推出矛盾。

對稱破壞只加了 2 到 8 個單位子句。**編碼大小幾乎沒變，速度差三個數量級**，所以「子句越少越快」不是可靠的直覺。

## Hamilton cycle：位置編碼與 AMO 的兩種寫法

變數：`p(v, i)` = 「v 是圈上第 i 個點」，`n²` 個變數。約束：

- 每個點恰好一個位置、每個位置恰好一個點（兩組 exactly-one）
- 不相鄰的 u、v 不能排在相鄰位置：對每個非邊 `(u, v)` 的兩個方向、每個 i，`(¬p(u,i) ∨ ¬p(v,i+1))`，位置取 mod n 讓第 n−1 個接回第 0 個
- 對稱破壞：圈可以從任一點開始，固定 `p(V[0], 0)`

AMO 有兩種寫法。**Pairwise**：每對寫一條 `(¬a ∨ ¬b)`，n 個文字 `C(n, 2)` 條子句、不加變數。**Sequential counter**（Sinz 2005）：引入 n−1 個輔助變數 `s[i]`，意思是「前 i+1 個文字裡已經有一個為真」，子句 `3n − 4` 條：

```text
x0 → s0           x_i → s_i         s_{i-1} → s_i        x_i → ¬s_{i-1}      x_{n-1} → ¬s_{n-2}
「x0 真則計數器亮」 「計數器一亮就一路亮到底」   「已經亮了就不能再有新的真」
```

```python
# cnf_ham.py（需要同目錄的 cnf_color.py）
import itertools
import networkx as nx
from cnf_color import CNF


def amo_pairwise(f, lits):
    for a, b in itertools.combinations(lits, 2):
        f.add(-a, -b)


def amo_sequential(f, lits, tag):
    """Sinz 2005 的 sequential counter：s[i] 表示「lits[0..i] 裡已經有一個為真」。"""
    n = len(lits)
    if n <= 1:
        return
    s = [f.var(("s", tag, i)) for i in range(n - 1)]
    f.add(-lits[0], s[0])
    for i in range(1, n - 1):
        f.add(-lits[i], s[i])
        f.add(-s[i - 1], s[i])
        f.add(-lits[i], -s[i - 1])
    f.add(-lits[n - 1], -s[n - 2])


def encode_hc(adj, amo="pairwise"):
    """位置編碼：p(v, i) = 「v 是圈上第 i 個點」。"""
    V = sorted(adj)
    n = len(V)
    f = CNF()
    p = lambda v, i: f.var(("p", v, i))
    for v in V:
        p(v, 0)
    for v in V:
        f.add(*[p(v, i) for i in range(n)])          # 每點至少一個位置
    for i in range(n):
        f.add(*[p(v, i) for v in V])                 # 每個位置至少一個點
    groups = [("v", v, [p(v, i) for i in range(n)]) for v in V] + \
             [("i", i, [p(v, i) for v in V]) for i in range(n)]
    for kind, key, lits in groups:                   # 至多一個
        if amo == "pairwise":
            amo_pairwise(f, lits)
        else:
            amo_sequential(f, lits, (kind, key))
    for u, v in itertools.permutations(V, 2):        # 相鄰位置必須有邊（含首尾相接）
        if v not in adj[u]:
            for i in range(n):
                f.add(-p(u, i), -p(v, (i + 1) % n))
    f.add(p(V[0], 0))                                # 對稱破壞：第 0 個位置固定給 V[0]
    return f


def decode_hc(model, adj):
    n = len(adj)
    order = [None] * n
    for (tag, *rest), val in model.items():
        if tag == "p" and val:
            v, i = rest
            order[i] = v
    return order


if __name__ == "__main__":
    tests = {
        "dodecahedral (20 點, 有 HC)": nx.dodecahedral_graph(),
        "Petersen (10 點, 無 HC)": nx.petersen_graph(),
        "K(5,6) (11 點, 無 HC)": nx.complete_bipartite_graph(5, 6),
        "G(30, 0.15) seed=7": nx.gnp_random_graph(30, 0.15, seed=7),
        "G(30, 0.15) seed=9": nx.gnp_random_graph(30, 0.15, seed=9),
    }
    for name, G in tests.items():
        adj = {u: set(G[u]) for u in G}
        for amo in ("pairwise", "sequential"):
            f = encode_hc(adj, amo)
            r = f.solve()
            dt = f.t_check
            res = r if isinstance(r, str) else "sat"
            print(f"{name:24s} {amo:10s} vars={f.nvars:5d} clauses={len(f.clauses):6d} {res:5s} {dt:.3f}s")
            if res == "sat":
                cyc = decode_hc(r, adj)
                assert sorted(cyc) == sorted(adj)
                assert all(cyc[(i + 1) % len(cyc)] in adj[cyc[i]] for i in range(len(cyc)))
```

```text
dodecahedral (20 點, 有 HC) pairwise   vars=  400 clauses= 14041 sat   0.014s
dodecahedral (20 點, 有 HC) sequential vars= 1160 clauses=  8681 sat   0.011s
Petersen (10 點, 無 HC)    pairwise   vars=  100 clauses=  1521 unsat 0.002s
Petersen (10 點, 無 HC)    sequential vars=  280 clauses=  1141 unsat 0.002s
K(5,6) (11 點, 無 HC)      pairwise   vars=  121 clauses=  1783 unsat 0.003s
K(5,6) (11 點, 無 HC)      sequential vars=  341 clauses=  1211 unsat 0.004s
G(30, 0.15) seed=7       pairwise   vars=  900 clauses= 47581 unsat 0.119s
G(30, 0.15) seed=7       sequential vars= 2640 clauses= 26641 unsat 0.056s
G(30, 0.15) seed=9       pairwise   vars=  900 clauses= 47461 sat   0.231s
G(30, 0.15) seed=9       sequential vars= 2640 clauses= 26521 sat   0.063s
```

子句數可以手算驗證。Pairwise 版：`2n`（兩組 ALO）`+ 2n·C(n,2)`（兩組 AMO）`+ n·(n(n−1) − 2m)`（非邊的兩個方向乘 n 個位置）`+ 1`。dodecahedron 是 20 點 30 邊：`40 + 7600 + 20·320 + 1 = 14041`。Sequential 版每組 AMO 換成 `3n − 4 = 56` 條、加 19 個變數：`40 + 40·56 + 6400 + 1 = 8681`，變數 `400 + 40·19 = 1160`。

Pairwise 的 AMO 是 `O(n³)` 子句，sequential 是 `O(n²)`，但這個編碼的大頭其實是「非邊」那組（也是 `O(n³)`），所以總子句只省了一半左右；30 點的隨機圖上 sequential 快了 2 到 4 倍。換到更大的圖，AMO 的差距才會主導。

Petersen 圖沒有 Hamilton cycle（經典結果，它是最小的「3-正則、2-連通卻沒有 Hamilton 圈」的例子之一），K(5,6) 是兩邊不等大的二分圖：Hamilton 圈在二分圖上必須左右交替，兩邊點數必須相等，所以一定沒有。seed=7 那張隨機圖有度數 1 的點，Hamilton 圈不可能存在，卻還是送進了 solver，見踩雷 3。

## Clique：變數設計的差距

同一個問題，兩種完全不同的變數設計：

- **選點編碼（select）**：每點一個變數 `x_v`。非邊 `(u, v)` 給 `(¬x_u ∨ ¬x_v)`；再加「至少 k 個 x 為真」。後者是基數約束，等價於「至多 n−k 個 ¬x 為真」，用 Sinz 的 at-most-K sequential counter 編（`O(nK)` 個輔助變數）。
- **格子編碼（slots）**：k 個格子，`y(i, v)` = 「第 i 格放 v」，`k·n` 個變數。每格恰放一點，任兩格放的點必須相鄰（a = b 也算不相鄰，所以同一點不會佔兩格）。

格子編碼有 `k!` 種格子排列的對稱，可以加「格子裡的點編號遞增」破掉。對照組是 z3 原生的 `AtLeast` 基數約束：它不經過我們的 CNF，z3 內部怎麼處理基數約束（展開成子句或用專門的推理）依版本與設定而定，這裡不深究。

```python
# cnf_clique.py（需要同目錄的 cnf_color.py）
import itertools
import time
import networkx as nx
import z3
from cnf_color import CNF


def at_most_k(f, lits, K, tag):
    """Sinz (2005) sequential counter：s(i, j) = 「前 i+1 個文字裡至少 j+1 個為真」。"""
    n = len(lits)
    if K >= n:
        return
    if K == 0:
        for l in lits:
            f.add(-l)
        return
    s = lambda i, j: f.var(("cnt", tag, i, j))
    f.add(-lits[0], s(0, 0))
    for j in range(1, K):
        f.add(-s(0, j))
    for i in range(1, n - 1):
        f.add(-lits[i], s(i, 0))
        f.add(-s(i - 1, 0), s(i, 0))
        for j in range(1, K):
            f.add(-lits[i], -s(i - 1, j - 1), s(i, j))
            f.add(-s(i - 1, j), s(i, j))
        f.add(-lits[i], -s(i - 1, K - 1))
    f.add(-lits[n - 1], -s(n - 2, K - 1))


def clique_select(adj, k):
    """編碼 A：每點一個變數「選它」；不相鄰的兩點不能同時選；至少選 k 個。"""
    f = CNF()
    V = sorted(adj)
    x = {v: f.var(("x", v)) for v in V}
    for u, v in itertools.combinations(V, 2):
        if v not in adj[u]:
            f.add(-x[u], -x[v])
    # 至少 k 個為真 ⇔ 至多 n-k 個為假
    at_most_k(f, [-x[v] for v in V], len(V) - k, "atleast")
    return f, lambda m: [v for v in V if m[("x", v)]]


def clique_slots(adj, k, ordered=False):
    """編碼 B：k 個格子，每格恰放一點；任兩格的點必須相鄰（也就互不相同）。"""
    f = CNF()
    V = sorted(adj)
    y = lambda i, v: f.var(("y", i, v))
    for i in range(k):
        f.add(*[y(i, v) for v in V])
        for a, b in itertools.combinations(V, 2):
            f.add(-y(i, a), -y(i, b))
    for i, j in itertools.combinations(range(k), 2):
        for a in V:
            for b in V:
                if b not in adj[a]:          # 包含 a == b：同一點不能佔兩格
                    f.add(-y(i, a), -y(j, b))
    if ordered:                              # 對稱破壞：格子裡的點編號遞增
        for i in range(k - 1):
            for a in V:
                for b in V:
                    if b <= a:
                        f.add(-y(i, a), -y(i + 1, b))
    return f, lambda m: [v for i in range(k) for v in V if m[("y", i, v)]]


def clique_pb(adj, k):
    """對照組：z3 原生的基數約束 AtLeast，不手刻 CNF。"""
    V = sorted(adj)
    x = {v: z3.Bool(f"x{v}") for v in V}
    s = z3.Solver()
    for u, v in itertools.combinations(V, 2):
        if v not in adj[u]:
            s.add(z3.Or(z3.Not(x[u]), z3.Not(x[v])))
    s.add(z3.AtLeast(*[x[v] for v in V], k))
    t = time.perf_counter()
    r = s.check()
    return str(r), time.perf_counter() - t


def is_clique(adj, S):
    return all(b in adj[a] for a, b in itertools.combinations(S, 2))


if __name__ == "__main__":
    G = nx.gnp_random_graph(40, 0.5, seed=35)
    adj = {u: set(G[u]) for u in G}
    _, omega = nx.max_weight_clique(G, weight=None)
    print("NetworkX 的 ω(G) =", omega)
    for k in (omega, omega + 1):
        for name, enc in (("select+counter", clique_select), ("slots", clique_slots),
                          ("slots+ordered", lambda a, k: clique_slots(a, k, ordered=True))):
            f, decode = enc(adj, k)
            r = f.solve()
            res = r if isinstance(r, str) else "sat"
            if res == "sat":
                assert is_clique(adj, decode(r)) and len(set(decode(r))) >= k
            print(f"k={k:2d} {name:15s} vars={f.nvars:5d} clauses={len(f.clauses):6d} "
                  f"{res:5s} check {f.t_check:.3f}s")
        r, dt = clique_pb(adj, k)
        print(f"k={k:2d} {'z3 AtLeast':15s} {'':25s} {r:5s} check {dt:.3f}s")
```

```text
NetworkX 的 ω(G) = 8
k= 8 select+counter  vars= 1288 clauses=  2871 sat   check 0.008s
k= 8 slots           vars=  320 clauses= 27976 sat   check 0.204s
k= 8 slots+ordered   vars=  320 clauses= 33716 sat   check 0.156s
k= 8 z3 AtLeast                                sat   check 0.007s
k= 9 select+counter  vars= 1249 clauses=  2794 unsat check 0.011s
k= 9 slots           vars=  360 clauses= 34965 unsat check 62.610s
k= 9 slots+ordered   vars=  360 clauses= 41525 unsat check 0.205s
k= 9 z3 AtLeast                                unsat check 0.007s
```

對拍的方式有兩層：NetworkX 的精確最大團給出 ω = 8，三種 CNF 編碼都在 k = 8 給 sat、k = 9 給 unsat；sat 時解碼出來的點集再用 `is_clique` 檢查一次。

這張表是本章最重要的數據。格子編碼的變數最少（320 個），但 unsat 時要 62 秒，因為 `9! = 362880` 種格子排列都得否決；加了遞增約束之後子句**變多**了 6500 條，時間卻降到 0.2 秒。選點編碼沒有這種對稱（一個點集只有一種表示），變數多但子句少，和 z3 原生的 `AtLeast` 一樣快。

計數器本身也要驗證，否則一個寫錯的 `at_most_k` 會讓整張表失去意義。對 n ≤ 5、每個 K、每組 0/1 輸入窮舉：

```python
# check_counter.py（需要同目錄的 cnf_color.py、cnf_clique.py）
import itertools
from cnf_color import CNF
from cnf_clique import at_most_k

count = 0
for n in range(1, 6):
    for K in range(0, n + 1):
        for bits in itertools.product([0, 1], repeat=n):
            f = CNF()
            xs = [f.var(("x", i)) for i in range(n)]
            at_most_k(f, xs, K, "t")
            for i, b in enumerate(bits):      # 用單位子句把 x 固定成這組 bits
                f.add(xs[i] if b else -xs[i])
            ok = not isinstance(f.solve(), str)
            assert ok == (sum(bits) <= K), (n, K, bits)
            count += 1
print("at_most_k 窮舉驗證通過", count, "組")
```

```text
at_most_k 窮舉驗證通過 320 組
```

`320 = Σ_{n=1}^{5} (n+1)·2^n`。

## 對比與取捨

| 方案 | 變數 | 子句 | 優點 | 缺點 |
| --- | --- | --- | --- | --- |
| AMO pairwise | 0 額外 | `C(n,2)` | 單位傳播強、好寫 | n 大時子句平方成長 |
| AMO sequential（Sinz） | `n − 1` | `3n − 4` | 線性大小 | 多了輔助變數，傳播要多走幾步 |
| 基數約束：手刻 counter | `O(nK)` | `O(nK)` | 任何 SAT solver 都吃 | K 大時仍然大 |
| 基數約束：z3 `AtLeast` | 0 | 1 條約束 | 最短、本例最快 | 只有支援 PB 的 solver 有；輸出 DIMACS 時要自己展開 |
| 格子編碼（clique） | `kn` | `O(k²n²)` | 變數少、直觀 | `k!` 對稱，一定要破 |
| 自寫回溯（Ch 34） | — | — | 可利用結構（分量分解） | 沒有子句學習，unsat 證明很慢 |

z3 是 SMT solver，它的命題核心是 CDCL，但純 CNF 的效能一般而言比不上專門的 SAT solver（CaDiCaL、Kissat 這類 SAT Competition 常勝軍）。本課環境沒有 python-sat（Windows 上編不起來），所以統一用 z3；DIMACS 是標準格式，`f.dimacs()` 寫成檔案就能直接餵給那些 solver。這點我沒有在本機實測，屬一般經驗。

## 踩雷集錦

1. **把 Python 端建構時間當成 solver 時間**：第一版我用 z3 的 Python API 一條一條 `s.add(Or(...))`，30 點 Hamilton cycle 實例量到「4 秒」。拆開量：建 47461 條 z3 運算式花了 2.8 秒，`check()` 只花 0.23 秒；改成先寫成 DIMACS 再讓 z3 讀進去（當時用的是 `from_file`，本章程式用等價的 `from_string`），建構降到 0.05 秒。benchmark 編碼之前，先確認你量的是 solver。
2. **「子句越少越快」**：格子編碼加了 6500 條對稱破壞子句，時間從 62 秒降到 0.2 秒；Mycielski M6 的團固定只加 2 條，快了 15 倍。決定速度的是搜尋空間的形狀，不是檔案大小。
3. **該在圖上做的前處理丟給 solver**：seed=7 那張圖有度數 1 的點，O(n) 檢查就能判定沒有 Hamilton cycle，卻送了 47581 條子句進去。度數檢查、連通性、割點（[Ch 11](./11-connectivity-tarjan.md)）、二分圖兩邊點數，這些都該先做。SAT 是最後手段，不是第一手段。
4. **解碼時假設「恰好一個」**：拿掉 AMO 的著色編碼是合法的，但 model 可能讓某點同時有兩色；解碼要挑一個。反過來，Hamilton cycle 的 AMO **不能**拿掉，否則一個點可以佔兩個位置。哪些約束可省，要逐條論證，不能憑感覺。
5. **對稱破壞破錯了**：固定「第 0 點塗色 0、第 1 點塗色 1」只有在 0、1 相鄰時才合法；不相鄰的兩點在所有最佳著色裡可能同色，強制它們不同色可能把 sat 變成 unsat。團是安全的，因為團上的點本來就兩兩不同色。

## 進階：再往深一層

- **Lex-leader 對稱破壞**：Crawford 等人（KR 1996）給出一般方法：對問題的對稱群裡每個置換 σ，加一個「解在字典序上不大於它的 σ 像」的約束。完整版大小是群的大小（可能指數大），實務上只加生成元，或用 BreakID 這類工具自動偵測 CNF 的對稱。CNF 的對稱偵測本身就是一個圖同構問題（把子句和文字畫成圖），這是 [Ch 37](./37-graph-isomorphism-wl.md) 的應用之一。
- **Hamilton cycle 的更好編碼**：位置編碼的「非邊」子句是 `O(n³)`，對稀疏大圖很浪費。另一派是「後繼編碼」：每條邊一個變數表示「圈走這條邊」，每點恰好一進一出，再禁止子迴圈（subtour）。禁止子迴圈是難點，可以用遞增的整數順序（transitivity / 位元加法器）、或 CEGAR（先解、發現子迴圈再加子句）。Heule 在 SAT 2021 的 Chinese remainder encoding 是這條線上的結果。
- **增量求解**：找最大團、最小色數時，我們從 k 掃到 k+1 重建整個 CNF。z3 支援 `push/pop` 與 assumption，可以保留學到的子句只改基數界線；這在 [sat_smt](../../programming/sat_smt/README.md) 的進階章有談。
- **什麼時候不要用 SAT**：結構化的圖（樹、treewidth 小、chordal）往往有多項式或 FPT 演算法（下一章）。SAT 擅長的是「沒有明顯結構、但約束局部」的實例。

## 動手練習

1. 把 `sym_color.py` 的對稱破壞改成「只固定團的第一個點為顏色 0」，量 M6 的時間，和「固定整個團」比，看看破掉 k 倍與破掉 `k(k−1)` 倍的差異是否符合預期。
2. 寫 k-coloring 的 **order encoding**：`o(v, c)` = 「v 的顏色 ≥ c」，加 `o(v, c+1) → o(v, c)`。相鄰不同色怎麼寫？量它和本章的 direct encoding 在 M5 上的子句數與時間。
3. 把 `encode_hc` 套到 5×6 棋盤的騎士圖（每格一點、馬步可達就連邊）上找閉合騎士巡邏，解碼後畫出巡邏路線（matplotlib），並確認每一步都是合法的馬步。

## 本章重點整理

- 圖問題編成 CNF 只靠三種句型：至少一個、至多一個、衝突；真正的設計決策是變數代表什麼。
- 對稱是 unsat 實例的頭號殺手：固定一個團的顏色、要求格子遞增，加很少子句就能快上一到三個數量級。
- AMO 與基數約束有 pairwise / sequential counter / 原生 PB 幾種寫法，大小從 `O(n²)` 到 `O(n)`，但總時間要實測。
- 先在圖上做便宜的前處理，再交給 solver；量時間時分開建構與求解。

## 自我檢核

- [ ] 不看筆記，能寫出 k-coloring 的 direct encoding，並說明為什麼 AMO 可以省略
- [ ] 能解釋為什麼對稱破壞對 unsat 實例的影響遠大於 sat 實例
- [ ] 有人說「我的 CNF 比他的小一半，所以我的編碼比較好」，你會怎麼反駁？
- [ ] 能說出 Hamilton cycle 位置編碼裡哪一組子句主導了大小，以及換成後繼編碼要付出什麼代價
- [ ] 想一個你工作上的排程或配置問題，它的自然變數是什麼？有沒有明顯的對稱？

## 延伸閱讀

### 論文

- **[Towards an Optimal CNF Encoding of Boolean Cardinality Constraints](https://www.carstensinz.de/papers/CP-2005.pdf)** — Carsten Sinz，CP 2005（LNCS 3709，pp. 827–831）
  - **核心貢獻**：提出 sequential counter 編碼 at-most-k，子句與輔助變數都是 `O(nk)`，比當時常見的編碼小，並和 parallel counter 等電路式編碼做比較。
  - **讀哪裡**：整篇只有 5 頁；sequential counter 的子句組可以和本章 `at_most_k` 逐條對照（我們的 `s(i, j)` 就是它的計數器變數）。作者說證明放在另一個網頁，正文只有構造。
  - **和本章的關聯**：clique 的選點編碼與 Hamilton cycle 的 sequential AMO 都出自這裡。
- **Symmetry-Breaking Predicates for Search Problems** — James Crawford、Matthew Ginsberg、Eugene Luks、Amitabha Roy，KR 1996（pp. 148–159）
  - **核心貢獻**：把「對稱」形式化為置換群，提出 lex-leader 約束，並把找對稱化成圖自同構問題。
  - **讀哪裡**：前半的定義與 lex-leader 構造；後半的複雜度結果可以先跳過。作者之一 Luks 的網站有放 PDF（ix.cs.uoregon.edu/~luks/symmetry.pdf）。
  - **前提**：本章的對稱破壞節、群的基本定義。
- **[Chinese Remainder Encoding for Hamiltonian Cycles](https://par.nsf.gov/biblio/10302525)** — Marijn J. H. Heule，SAT 2021
  - **核心貢獻**：Hamilton cycle 的緊湊編碼，用中國剩餘定理排除短子迴圈，在 Flinders HCP 挑戰集（1001 張圖）的大圖上勝過其他編碼。
  - **讀哪裡**：前兩節對既有編碼（包含本章的位置編碼）的整理最有用；CRT 構造本身是加分。
  - **前提**：本章 Hamilton 節、二進位加法器的 CNF 編碼概念。

### 書籍

- **《Handbook of Satisfiability》第 2 版** — Armin Biere、Marijn Heule、Hans van Maaren、Toby Walsh 編（IOS Press，2021）
  - **這本書的定位**：SAT 領域的標準參考手冊。
  - **讀哪幾章**：第 2 章 CNF Encodings（Steven Prestwich，pp. 75–100），涵蓋 Tseitin、AMO 與基數約束的各種編碼、編碼與搜尋的互動、常見錯誤；本章是它的圖論小切片。對稱破壞另有專章，有興趣再翻。

### 本 repo 的延伸

- **[programming/sat_smt](../../programming/sat_smt/README.md)**
  - **讀哪裡**：[Ch 4 — Tseitin 轉換](../../programming/sat_smt/04-tseitin-transformation.md)（任意布林式怎麼變 CNF）、[Ch 8 — SAT 問題與 DIMACS 格式](../../programming/sat_smt/08-sat-problem-dimacs.md)（本章 `dimacs()` 的格式依據）、[Ch 13 — CDCL 核心](../../programming/sat_smt/13-cdcl-core.md)（為什麼 solver 能從衝突學習、對稱為什麼讓學到的子句失效）、[Ch 17 — Preprocessing](../../programming/sat_smt/17-preprocessing.md)（solver 自己做的前處理，和踩雷 3 的圖上前處理互補）。

SAT 把一切 NP 問題當黑盒處理，代價是看不見圖的結構。下一章反過來：如果圖「長得像樹」，很多 NP-complete 問題會變成線性時間，而「像樹的程度」可以精確量化，叫 treewidth。

→ [Ch 36 Treewidth 與樹分解](./36-treewidth.md)
