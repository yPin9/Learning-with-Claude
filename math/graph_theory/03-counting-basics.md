# Ch 3 — 計數基礎：排列組合、二項式、雙重計數

> **目標**：掌握加法與乘法原理、排列、組合、重複組合、二項式定理，能用「組合證明」（雙重計數與雙射）推導恆等式，並把每個公式都和程式暴力計數對上。
>
> **環境**：Python 3.13.12、NetworkX 3.7、NumPy 2.5.4，Windows 11。

## 為什麼需要這個？

計數在系統與資安工作裡比你想像的常見：

- 暴力破解一個 8 字元、62 種字元的密碼要試幾次？
- 一個有 n 個基本塊的函式，最多有幾條無環路徑？路徑爆炸是 symbolic execution 的頭號敵人。
- 一個 fuzzer 要覆蓋所有「兩個 feature 的組合」，最少要幾個測試？
- 在 n 個節點的網路裡，可能的三角形有幾個？要不要全部列舉？

很多人處理這類問題的方式是「寫個迴圈數一數」。這在小規模可以，但它不會告訴你**數量怎麼隨 n 成長**，也不會在 n = 50 時給你答案。反過來，只背公式的人常犯另一種錯：搞不清楚「有沒有順序」「能不能重複」，套錯公式卻不自知。

本章的做法是兩邊都要：每個公式都有證明，也都有一支暴力程式替它背書。公式錯了，程式會抓到；程式太慢，公式接手。

## 先建立直覺

幾乎所有基礎計數問題都可以歸結成「從 n 個東西裡選 k 個」，然後問兩個問題：

```text
                 可以重複選？
                  否            是
            ┌──────────────┬──────────────────┐
  有順序？是 │ 排列 P(n,k)   │ n^k              │
            │ n!/(n-k)!     │ （字串、函數）     │
            ├──────────────┼──────────────────┤
         否 │ 組合 C(n,k)   │ 重複組合          │
            │ n!/(k!(n-k)!) │ C(n+k-1, k)       │
            └──────────────┴──────────────────┘
```

圖論的例子：

- 有順序、不重複：一條有方向的路徑 `v0 → v1 → ... → vk`，頂點互不相同。
- 無順序、不重複：一個頂點子集，例如一個團（clique）或一條邊 `{u, v}`。
- 有順序、可重複：一條「walk」（可以重複走同一點的走法）。
- 無順序、可重複：一個度數的多重集合，例如「三個點度數 2、兩個點度數 1」。

拿到一個計數問題，第一件事是把它放進這張表的某一格。放錯格子是計數錯誤的最大來源。

> 本章大量使用 [Ch 2](./02-proof-toolbox.md) 的雙重計數與雙射；如果還不熟，先回看「技巧一」與「技巧六」。

## 兩條基本原理

**加法原理（sum rule）**：若 A、B 不相交，`|A ∪ B| = |A| + |B|`。

**乘法原理（product rule）**：若一個選擇分成兩步，第一步有 a 種做法，**不論第一步選了什麼**，第二步都有 b 種做法，則總共 `a × b` 種。

「不論第一步選了什麼」這句是乘法原理最常被忽略的條件。數「頂點互不相同的有向路徑 `v0 → v1`」時，選了 v0 之後 v1 有 n − 1 種，這個數字不依賴 v0 是誰，所以總數是 `n(n − 1)`。但如果要數「在某張特定圖 G 裡的有向邊」，選了 v0 之後 v1 的選擇數是 `deg(v0)`，隨 v0 改變，乘法原理不適用，要改成 `Σ deg(v0)`。

**除法原理（division rule）**：若一個集合 A 的每個元素恰好對應到 B 中 k 個元素（一個「k 對 1」的映射 B → A），則 `|A| = |B| / k`。這是雙重計數的特例，下面數 Hamilton 圈時會用到。

## 排列、組合、重複組合

**排列**：從 n 個相異物件中依序選 k 個，`P(n, k) = n(n − 1)...(n − k + 1) = n! / (n − k)!`。證明：乘法原理，第 i 步剩 `n − i + 1` 種。

**組合**：從 n 個相異物件中選 k 個的子集數 `C(n, k)`。證明（除法原理）：每個 k 元子集恰好對應 `k!` 個有序排列，所以 `C(n, k) = P(n, k) / k! = n! / (k! (n − k)!)`。

**重複組合**：從 n 種物件中可重複地選 k 個（不計順序），數量是 `C(n + k − 1, k)`。

**證明（隔板法，stars and bars）**：一個選法由「第 i 種選了 `x_i` 個」決定，`x_1 + ... + x_n = k`，`x_i ≥ 0`。把它寫成 k 顆星和 n − 1 根隔板排成一列：

```text
n = 4 種，k = 5 個，(x1, x2, x3, x4) = (2, 0, 1, 2)

    ★ ★ | | ★ | ★ ★
```

這個對應是雙射：每個解給出唯一一個排列，每個排列（讀隔板之間有幾顆星）給出唯一一個解。排列總長 `k + n − 1`，選 k 個位置放星，共 `C(n + k − 1, k)` 種。證畢。

把公式和 itertools 對答案，順便看邊界情況：

```python
"""排列、組合、重複組合：公式 vs itertools 暴力。"""
from itertools import permutations, combinations, combinations_with_replacement, product
from math import comb, perm

print(f"{'n':>2} {'k':>2} | {'P(n,k)':>7} {'perm':>7} | {'C(n,k)':>6} {'comb':>6} | {'重複組合':>6} {'公式':>6}")
for n, k in [(5, 0), (5, 2), (5, 5), (6, 3), (7, 4), (3, 5)]:
    p_brute = sum(1 for _ in permutations(range(n), k))
    c_brute = sum(1 for _ in combinations(range(n), k))
    m_brute = sum(1 for _ in combinations_with_replacement(range(n), k))
    m_formula = comb(n + k - 1, k)
    print(f"{n:>2} {k:>2} | {p_brute:>7} {perm(n, k):>7} | {c_brute:>6} {comb(n, k):>6} | {m_brute:>6} {m_formula:>6}")
    assert p_brute == perm(n, k) and c_brute == comb(n, k) and m_brute == m_formula

# 函數計數：[k] -> [n] 的函數有 n^k 個，單射有 P(n,k) 個
for n, k in [(3, 2), (4, 4), (2, 3)]:
    funcs = list(product(range(n), repeat=k))
    inj = sum(1 for f in funcs if len(set(f)) == k)
    print(f"[{k}]->[{n}]: 函數 {len(funcs)} = {n}^{k}, 單射 {inj} = P({n},{k}) = {perm(n, k)}")
```

輸出：

```text
 n  k |  P(n,k)    perm | C(n,k)   comb |   重複組合     公式
 5  0 |       1       1 |      1      1 |      1      1
 5  2 |      20      20 |     10     10 |     15     15
 5  5 |     120     120 |      1      1 |    126    126
 6  3 |     120     120 |     20     20 |     56     56
 7  4 |     840     840 |     35     35 |    210    210
 3  5 |       0       0 |      0      0 |     21     21
[2]->[3]: 函數 9 = 3^2, 單射 6 = P(3,2) = 6
[4]->[4]: 函數 256 = 4^4, 單射 24 = P(4,4) = 24
[3]->[2]: 函數 8 = 2^3, 單射 0 = P(2,3) = 0
```

幾個邊界值值得停下來看：

- `k = 0`：選 0 個的方法有 1 種（空選法），不是 0 種。`0! = 1` 的定義正是為了讓這些公式在邊界上一致。
- `k > n`（最後一列 `n = 3, k = 5`）：不重複的選法是 0，`math.comb` 和 `math.perm` 都正確回傳 0；但重複組合可以有 21 種，因為可以重複選。
- 函數 `[3] → [2]` 不可能是單射，這就是鴿籠原理（Ch 2）的計數版本。

## 在完全圖裡數東西

**完全圖（complete graph）** `K_n` 是 n 個點兩兩相鄰的圖。它是計數練習的好場地，因為「圖裡有沒有這條邊」永遠是「有」，問題只剩純計數。

| 要數的東西 | 公式 | 理由 |
| --- | --- | --- |
| 邊 | `C(n, 2)` | 每條邊是一個 2 元子集 |
| 三角形 | `C(n, 3)` | 每個 3 元子集恰好構成一個三角形 |
| 有 2 條邊的路徑 `a − b − c` | `n(n − 1)(n − 2) / 2` | 有序三元組 `P(n, 3)`，每條路徑被正反數兩次 |
| Hamilton 圈 | `(n − 1)! / 2`（n ≥ 3） | 見下 |
| 完美匹配（n 為偶數） | `(n − 1)!! = (n − 1)(n − 3)...3 · 1` | 見下 |

**Hamilton 圈的計數（除法原理）**：把 n 個點排成一列 `p0 p1 ... p(n−1)`，接上 `p(n−1) p0`，就得到一個 Hamilton 圈。共 `n!` 個排列。同一個圈會被幾個排列產生？起點可以是圈上任一點（n 種），方向可以順或逆（2 種），所以每個圈恰對應 `2n` 個排列（n ≥ 3 時這 2n 個排列互不相同）。由除法原理，圈數 `= n! / (2n) = (n − 1)! / 2`。

**完美匹配的計數（遞推）**：令 `M(n)` 是 `K_n` 的完美匹配數，n 為偶數。頂點 0 必須和某個點配對，有 n − 1 種選擇；配對後剩下 n − 2 個點形成 `K_(n−2)`，有 `M(n − 2)` 種完成方式。所以 `M(n) = (n − 1) M(n − 2)`，`M(0) = 1`，展開得 `(n − 1)!!`。

```python
"""K_n 裡的計數：三角形、長度 2 的路徑、Hamilton 圈、完美匹配。"""
from itertools import combinations, permutations
from math import comb, factorial


def count_paths(n, k):
    """K_n 中有 k 條邊的路徑數（路徑視為無方向：v0..vk 與 vk..v0 同一條）。"""
    seqs = sum(1 for _ in permutations(range(n), k + 1))
    return seqs // 2


def count_ham_cycles(n):
    seen = set()
    for p in permutations(range(n)):
        edges = frozenset(frozenset((p[i], p[(i + 1) % n])) for i in range(n))
        seen.add(edges)
    return len(seen)


def count_perfect_matchings(n):
    seen = set()
    for p in permutations(range(n)):
        seen.add(frozenset(frozenset(p[i:i + 2]) for i in range(0, n, 2)))
    return len(seen)


def double_factorial(m):
    r = 1
    while m > 1:
        r *= m
        m -= 2
    return r


for n in range(3, 8):
    tri = sum(1 for _ in combinations(range(n), 3))
    print(f"K_{n}: 三角形 {tri} = C({n},3) = {comb(n, 3)};"
          f" 2-邊路徑 {count_paths(n, 2)} = n(n-1)(n-2)/2 = {n * (n - 1) * (n - 2) // 2};"
          f" Ham 圈 {count_ham_cycles(n)} = (n-1)!/2 = {factorial(n - 1) // 2}")
for n in (2, 4, 6, 8):
    print(f"K_{n} 完美匹配: {count_perfect_matchings(n)} = ({n}-1)!! = {double_factorial(n - 1)}")
```

輸出：

```text
K_3: 三角形 1 = C(3,3) = 1; 2-邊路徑 3 = n(n-1)(n-2)/2 = 3; Ham 圈 1 = (n-1)!/2 = 1
K_4: 三角形 4 = C(4,3) = 4; 2-邊路徑 12 = n(n-1)(n-2)/2 = 12; Ham 圈 3 = (n-1)!/2 = 3
K_5: 三角形 10 = C(5,3) = 10; 2-邊路徑 30 = n(n-1)(n-2)/2 = 30; Ham 圈 12 = (n-1)!/2 = 12
K_6: 三角形 20 = C(6,3) = 20; 2-邊路徑 60 = n(n-1)(n-2)/2 = 60; Ham 圈 60 = (n-1)!/2 = 60
K_7: 三角形 35 = C(7,3) = 35; 2-邊路徑 105 = n(n-1)(n-2)/2 = 105; Ham 圈 360 = (n-1)!/2 = 360
K_2 完美匹配: 1 = (2-1)!! = 1
K_4 完美匹配: 3 = (4-1)!! = 3
K_6 完美匹配: 15 = (6-1)!! = 15
K_8 完美匹配: 105 = (8-1)!! = 105
```

暴力版的關鍵設計：Hamilton 圈和完美匹配都用 `frozenset` 的 `frozenset` 表示，也就是「邊的集合」。這樣同一個圈不論從哪個起點、哪個方向生成，都會變成同一個 key，`set` 自動去重。如果改用 tuple 存，就會數到 `n!` 而不是 `(n − 1)! / 2`——暴力程式本身也要想清楚「什麼算同一個」。

## 二項式定理與組合證明

**二項式定理**：`(x + y)^n = Σ_{k=0}^{n} C(n, k) x^k y^(n−k)`。

**組合證明**：把 `(x + y)^n` 展開成 n 個因子 `(x + y)(x + y)...(x + y)` 的乘積，每一項來自「每個因子選 x 或選 y」。`x^k y^(n−k)` 這一項的係數，等於「在 n 個因子中選哪 k 個貢獻 x」的方法數，也就是 `C(n, k)`。證畢。

代入 `x = y = 1` 得 `Σ C(n, k) = 2^n`：n 元集合的子集總數，按大小分組。代入 `x = 1, y = −1` 得 `Σ (−1)^k C(n, k) = 0`（n ≥ 1）：偶數大小子集和奇數大小子集一樣多——Ch 2 例 3 的對合證明的是同一件事。

組合證明（combinatorial proof）的意思是：**證明一個等式的兩邊都在數同一個集合**。它比代數推導更能解釋「為什麼」。下面四個恆等式都用這種方式證：

**Pascal 恆等式** `C(n, k) = C(n − 1, k − 1) + C(n − 1, k)`：數 `{1, ..., n}` 的 k 元子集，按「含不含元素 n」分成兩類。含 n 的：剩下 k − 1 個從前 n − 1 個選；不含 n 的：k 個全從前 n − 1 個選。

**委員會與主席** `Σ_k k · C(n, k) = n · 2^(n−1)`：數「(委員會, 主席)」配對，主席必須是委員。左邊先選 k 人委員會再從中選主席；右邊先從 n 人選主席，其餘 n − 1 人每人決定進不進委員會。

**Vandermonde 恆等式** `C(n + m, r) = Σ_j C(n, j) C(m, r − j)`：從 n 個男生和 m 個女生中選 r 人，按「選了 j 個男生」分組。

**Hockey stick** `Σ_{i=r}^{n} C(i, r) = C(n + 1, r + 1)`：數 `{0, 1, ..., n}` 的 r + 1 元子集，按「最大元素是 i」分組；最大元素是 i 時，其餘 r 個從 `{0, ..., i − 1}` 中選，有 `C(i, r)` 種。

```python
"""二項式恆等式：暴力驗證 Pascal、Vandermonde、hockey stick、Σk·C(n,k)。"""
from itertools import combinations
from math import comb

N = 12
for n in range(1, N + 1):
    for k in range(1, n + 1):
        assert comb(n, k) == comb(n - 1, k - 1) + comb(n - 1, k)          # Pascal
    assert sum(comb(n, k) for k in range(n + 1)) == 2 ** n               # 子集總數
    assert sum(k * comb(n, k) for k in range(n + 1)) == n * 2 ** (n - 1)  # 委員會 + 主席
    for m in range(0, N + 1):
        for r in range(0, n + m + 1):
            assert comb(n + m, r) == sum(comb(n, j) * comb(m, r - j) for j in range(r + 1))  # Vandermonde
    for r in range(0, n + 1):
        assert sum(comb(i, r) for i in range(r, n + 1)) == comb(n + 1, r + 1)  # hockey stick
print(f"Pascal / 2^n / n·2^(n-1) / Vandermonde / hockey stick：n,m <= {N} 全部成立")

# 委員會 + 主席的雙重計數：直接數 (委員會, 主席) 配對
for n in range(1, 8):
    pairs = sum(len(c) for k in range(n + 1) for c in combinations(range(n), k))
    pairs2 = n * 2 ** (n - 1)
    print(f"n={n}: (委員會, 主席) 配對 {pairs:>3} = n·2^(n-1) = {pairs2}")
```

輸出：

```text
Pascal / 2^n / n·2^(n-1) / Vandermonde / hockey stick：n,m <= 12 全部成立
n=1: (委員會, 主席) 配對   1 = n·2^(n-1) = 1
n=2: (委員會, 主席) 配對   4 = n·2^(n-1) = 4
n=3: (委員會, 主席) 配對  12 = n·2^(n-1) = 12
n=4: (委員會, 主席) 配對  32 = n·2^(n-1) = 32
n=5: (委員會, 主席) 配對  80 = n·2^(n-1) = 80
n=6: (委員會, 主席) 配對 192 = n·2^(n-1) = 192
n=7: (委員會, 主席) 配對 448 = n·2^(n-1) = 448
```

注意這段程式分兩層。上半段用 `math.comb` 驗證**公式之間**的關係，這只能確認代數沒寫錯；下半段直接列舉 (委員會, 主席) 配對，才是真正獨立地數那個集合。兩層的強度不同：如果 `math.comb` 本身有 bug，上半段抓不到。練習 A 會把「獨立暴力計數」這件事做得更系統。

## 雙重計數在圖上

回到任意圖 G（不只是完全圖）。

**例 1：長度 2 的路徑**。一條長度 2 的路徑 `x − v − y`（無方向）由中間點 v 和 v 的兩個鄰居決定。按中間點分組：

```text
#（長度 2 的路徑）= Σ_v C(deg(v), 2)
```

這條公式在網路分析裡叫「cherries」或「wedges」計數，是算 clustering coefficient 的分母。

**例 2：三角形與鄰接矩陣**。令 A 是 G 的鄰接矩陣，`A[i][j] = 1` 若 i、j 相鄰。`A^3[i][i]` 等於從 i 出發、走 3 步回到 i 的 walk 數（矩陣乘法的定義：`A^3[i][i] = Σ_{j,k} A[i][j] A[j][k] A[k][i]`）。簡單圖中長度 3 的閉 walk `i → j → k → i` 一定是三角形：沒有自環，所以 `j ≠ i`、`k ≠ j`、`i ≠ k`，三個點兩兩相異且兩兩相鄰。每個三角形 `{a, b, c}` 產生幾個閉 walk？起點 3 種、方向 2 種，共 6 個。所以：

```text
#（三角形）= trace(A^3) / 6
```

```python
"""雙重計數在圖上：Σ C(deg v, 2) = 長度 2 的路徑數；三角形數 = trace(A^3)/6。"""
from itertools import combinations
import networkx as nx
import numpy as np

SEED = 11
for n, p in [(8, 0.3), (12, 0.5), (20, 0.2)]:
    G = nx.gnp_random_graph(n, p, seed=SEED)
    adj = {u: set(G[u]) for u in G}
    cherries_formula = sum(len(s) * (len(s) - 1) // 2 for s in adj.values())
    cherries_brute = sum(1 for a, b, c in combinations(adj, 3)
                         for mid, x, y in ((a, b, c), (b, a, c), (c, a, b))
                         if x in adj[mid] and y in adj[mid])
    tri_brute = sum(1 for a, b, c in combinations(adj, 3)
                    if b in adj[a] and c in adj[a] and c in adj[b])
    A = nx.to_numpy_array(G, dtype=np.int64)
    tri_trace = int(np.trace(A @ A @ A)) // 6
    tri_nx = sum(nx.triangles(G).values()) // 3
    print(f"G({n},{p}) seed={SEED}: 2-路徑 {cherries_brute} = ΣC(deg,2) {cherries_formula};"
          f" 三角形 暴力 {tri_brute} / trace(A^3)/6 {tri_trace} / nx {tri_nx}")
```

輸出：

```text
G(8,0.3) seed=11: 2-路徑 22 = ΣC(deg,2) 22; 三角形 暴力 1 / trace(A^3)/6 1 / nx 1
G(12,0.5) seed=11: 2-路徑 169 = ΣC(deg,2) 169; 三角形 暴力 25 / trace(A^3)/6 25 / nx 25
G(20,0.2) seed=11: 2-路徑 162 = ΣC(deg,2) 162; 三角形 暴力 14 / trace(A^3)/6 14 / nx 14
```

`nx.triangles(G)` 回傳每個點參與的三角形數，總和除以 3 才是三角形總數——又是一次雙重計數（每個三角形被它的 3 個頂點各數一次）。暴力版的 `cherries_brute` 對每個 3 元子集檢查三個可能的中間點，它和公式是完全獨立的兩種數法。

`dtype=np.int64` 不是裝飾：`to_numpy_array` 預設回傳 float64，矩陣次方在大圖上會累積成很大的數，float 會悄悄失去精度。計數時一律用整數型別。

## 對比與取捨

| 方法 | 優點 | 缺點 | 適用情境 |
| --- | --- | --- | --- |
| 暴力列舉（itertools） | 不會套錯公式；「什麼算同一個」寫得很明確 | 指數時間 | 驗證公式、n ≤ 10 左右 |
| 封閉公式（`math.comb`） | O(1) 或多項式時間；顯示成長速度 | 套錯格子就全錯 | 已經證明過的計數 |
| 遞推（例如完美匹配） | 好推導、可程式化 | 未必有封閉形式 | 公式推不出來時（Ch 5） |
| 代數（矩陣 trace） | 一次算整張圖 | 只能數特定結構 | 三角形、walk 計數（Part 5） |

公式和暴力不是二選一，而是互相檢查。工程上的建議：任何在程式裡用到的計數公式，旁邊放一個小 n 的暴力測試。

## 踩雷集錦

1. **有序與無序搞混**：數「K_n 的三角形」寫成 `P(n, 3) = n(n − 1)(n − 2)`，多算了 6 倍。糾正：先問「`(a, b, c)` 和 `(b, a, c)` 算不算同一個」，算同一個就要除掉重複，或者一開始就用 `combinations`。
2. **除法原理的 k 不是常數**：數 Hamilton 圈時除以 2n 之所以對，是因為**每個**圈都恰好對應 2n 個排列。n = 2 時「圈」`0 → 1 → 0` 的兩個方向是同一個排列，k 不是 4，公式失效；這就是為什麼 Hamilton 圈公式要求 n ≥ 3。糾正：用除法原理前確認每個元素的重數都一樣。
3. **浮點數算組合數**：`factorial(n) / (factorial(k) * factorial(n - k))` 在 n 稍大時會出錯，而且是**安靜地**出錯：

   ```python
   from math import comb, factorial

   for n in (20, 60, 100):
       exact = comb(n, n // 2)
       via_float = int(factorial(n) / (factorial(n // 2) ** 2))
       print(f"C({n},{n // 2}): comb = {exact}")
       print(f"{'':>9}float = {via_float}  差 {via_float - exact}")
   try:
       factorial(200) / factorial(10)
   except OverflowError as e:
       print("OverflowError:", e)
   print("comb(3, 5) =", comb(3, 5))
   try:
       comb(-1, 2)
   except ValueError as e:
       print("ValueError:", e)
   ```

   輸出：

   ```text
   C(20,10): comb = 184756
            float = 184756  差 0
   C(60,30): comb = 118264581564861424
            float = 118264581564861424  差 0
   C(100,50): comb = 100891344545564193334812497256
            float = 100891344545564202071714955264  差 8736902458008
   OverflowError: integer division result too large for a float
   comb(3, 5) = 0
   ValueError: n must be a non-negative integer
   ```

   `/` 是浮點除法，float64 只有 53 位元的尾數，大約 15–16 位十進位有效數字。`C(100, 50)` 有 30 位數，後半段全是捨入誤差；`C(60, 30)` 在這裡剛好沒錯，不代表下一個 n 也不會錯。糾正：用 `math.comb`、`math.perm`，或整數除法 `//`。注意 `comb(n, k)` 在 `k > n` 時回傳 0（合理），但 `n < 0` 時丟 `ValueError`；Ch 4 和 Ch 5 的公式偶爾會出現負的參數，要自己處理。

4. **以為乘法原理永遠可用**：數「圖 G 中的有向邊」寫成 `n × (每點的鄰居數)`，但每點鄰居數不一樣。糾正：第二步的選擇數依賴第一步時，改用加法原理 `Σ_v deg(v)`。
5. **標號 vs 無標號**：「4 個點上有幾張圖」，標號的答案是 `2^6 = 64`，無標號是 11（Ch 0 的 atlas）。兩者差的不是一個固定倍數，因為不同的圖有不同的對稱性。糾正：問題沒說清楚時先問「頂點有沒有名字」；無標號計數要等到 [Ch 6](./06-burnside-polya.md) 的 Burnside 引理。

## 進階：再往深一層

**Twelvefold way**：Stanley 把「把 k 個球放進 n 個盒子」按「球可不可區分、盒子可不可區分、每盒限制（任意、至多 1、至少 1）」分成 12 種情況，整理成一張表。本章處理了其中「球可區分、盒子可區分」的幾格（`n^k`、`P(n, k)`）和「球不可區分、盒子可區分」的幾格（`C(n, k)`、`C(n + k − 1, k)`）。「每盒至少 1」需要 [Ch 4](./04-inclusion-exclusion.md) 的排容原理（得到 Stirling 數），「盒子不可區分」會引出整數分割，這是 Ch 5 生成函數的經典應用。

**路徑爆炸的量級**：一個由 k 個連續 if-else 組成的函式，CFG 有 `2^k` 條 entry 到 exit 的路徑，因為每個分支點獨立選兩邊之一（乘法原理）。k = 30 就超過十億條。這是 symbolic execution 必須做 path merging 或 state summarization 的計數理由。

**計數與複雜度**：「數 G 中有幾個完美匹配」對一般圖是 #P-complete（Valiant 1979），即使「判斷有沒有完美匹配」是多項式時間（Ch 23）。計數往往比判定難得多，這也是為什麼本章只在完全圖這種高度對稱的情況下有封閉公式。

## 動手練習

1. 一個 CFG 由 k 個連續的 if-else 菱形串接而成，寫程式產生它的 `succ` 字典，用 DFS 數 entry 到 exit 的路徑數，驗證是 `2^k`。再加一條從 exit 回到 entry 的回邊，「路徑數」還有意義嗎？
2. 證明並用程式驗證：`K_n` 中長度為 k 的路徑（k 條邊、頂點互異、無方向）共 `P(n, k + 1) / 2` 條（k ≥ 1）。為什麼 k = 0 時不能除以 2？
3. 用組合證明推導 `C(2n, n) = Σ_k C(n, k)^2`，然後用暴力列舉 `{0, ..., 2n−1}` 的 n 元子集驗證到 n = 8。
4. 寫一個函數，輸入一張圖，用 `Σ C(deg, 2)` 和三角形數算全域 clustering coefficient `3 × 三角形數 / 2-路徑數`，和 `nx.transitivity(G)` 對拍。

## 本章重點整理

- 計數第一步：判斷「有沒有順序」「能不能重複」，放進 2×2 的表格。
- 乘法原理要求第二步的選擇數不依賴第一步；除法原理要求每個元素的重數相同。
- 組合證明＝證明兩邊數的是同一個集合；它解釋原因，而不只是確認數值。
- 圖上的雙重計數：`Σ C(deg, 2)` 數 2-路徑，`trace(A^3) / 6` 數三角形。
- 計數一律用整數運算，`math.comb` 而非浮點除法。

## 自我檢核

- [ ] 不看書，用隔板法推導重複組合公式，並說清楚為什麼那是一個雙射。
- [ ] 解釋為什麼 Hamilton 圈的公式是 `(n − 1)! / 2`，以及 n = 2 時哪裡失效。
- [ ] 如果同事的程式用 `factorial(n) / factorial(k) / factorial(n-k)` 算組合數，你會怎麼說服他改？給出具體會出錯的 n。
- [ ] 想一個你工作中遇到的「數一數有幾種」的問題（測試組合、路徑、設定組合），把它放進本章的表格，並寫出公式。

## 延伸閱讀

### 書籍

- **《Concrete Mathematics: A Foundation for Computer Science》** — Ronald L. Graham、Donald E. Knuth、Oren Patashnik（Addison-Wesley，第 2 版，1994）
  - **這本書的定位**：Part 1 的主要參考書。Knuth 的風格：大量恆等式、每個都有推導，習題分級。
  - **讀哪幾章**：Chapter 5（Binomial Coefficients）。5.1 節的基本恆等式表是本章恆等式的完整版；5.2 節的「basic practice」示範怎麼拆解看起來嚇人的和式。
  - **前提知識**：本章內容與基本代數。年代雖久，二項式係數的內容不會過時。
- **《Proofs that Really Count: The Art of Combinatorial Proof》** — Arthur T. Benjamin、Jennifer J. Quinn（MAA，2003）
  - **這本書的定位**：整本書只做一件事：用組合證明（雙重計數、雙射）證明恆等式。
  - **讀哪幾章**：Chapter 5（Binomial Identities）直接對應本章；Chapter 1（Fibonacci Identities）是 Ch 5 遞迴關係的好預習。
  - **前提知識**：本章內容即可，非常適合初學者。
- **《Enumerative Combinatorics, Volume 1》** — Richard P. Stanley（Cambridge University Press，第 2 版，2012）
  - **這本書的定位**：計數組合學的標準研究所教科書。
  - **讀哪幾章**：Section 1.9（The Twelvefold Way）。讀完本章與 Ch 4 後再看，那張表會變得很好懂。其餘章節難度很高，當參考書用。
  - **前提知識**：本章與 Ch 4–5。

### 課程講義

- **[Mathematics for Computer Science](https://ocw.mit.edu/courses/6-042j-mathematics-for-computer-science-spring-2015/pages/readings/)** — Lehman、Leighton、Meyer（MIT 6.042J）
  - **讀哪裡**：Unit 3（Counting）的「Cardinality Rules」一章：sum rule、product rule、division rule、bookkeeper rule、組合證明，例子全部偏 CS。
  - **為什麼值得讀**：它把「division rule」講得特別清楚，正好補本章 Hamilton 圈計數的那一步。
  - **前提知識**：本章內容。

數得出「有幾個」之後，下一個問題是「有幾個**不**滿足某些條件」——錯排、滿射、避開某些邊的著色。這需要一個專門處理重疊的工具。

→ [Ch 4 排容原理與錯排](./04-inclusion-exclusion.md)
