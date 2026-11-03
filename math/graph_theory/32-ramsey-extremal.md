# Ch 32 — Ramsey 理論與極值圖論

> **目標**：證明並窮舉驗證 `R(3,3) = 6`、推出 Ramsey 數的遞迴上界與 `R(k,k) < 4^k`、用 z3 確認 `R(3,4) = 9`、用 Paley 圖證明 `R(4,4) > 17`；再證明 Mantel 定理與 Turán 定理（含極值圖唯一性），在所有 ≤ 7 點的圖上驗證，最後理解 Erdős–Stone 定理怎麼用色數決定一般禁用子圖的極值。
>
> **環境**：Python 3.13.12、NetworkX 3.7、z3-solver 5.3.3.0，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2 z3-solver==5.3.3.0`。

## 為什麼需要這個？

Ch 31 問的是「隨機的東西長什麼樣」。這一章問反過來的問題：**一個結構要多大，才能保證無論怎麼安排，都躲不掉某個子結構？**

兩條歷史線在這裡交會：

- **Ramsey 理論**：1930 年 Frank Ramsey 為了數理邏輯的一個判定問題，證明了一個組合引理：夠大的完全圖，邊不管怎麼二著色，總有一個大的單色完全子圖。1935 年 Erdős 與 Szekeres 獨立地重新發現它，並給出上界。這個領域的口號是「完全的無序是不可能的」。
- **極值圖論**：1907 年 Mantel 問：n 個點、沒有三角形的圖最多幾條邊？1941 年 Turán 推廣到「沒有 `K_{r+1}`」，從此「禁止某個子圖時，邊數最多能多少」成為一整個領域。

兩者是同一枚硬幣的兩面：Ramsey 說「夠大就躲不掉」，極值圖論說「邊夠多就躲不掉」，而且精確算出門檻。

系統工程師為什麼要在乎？因為「躲不掉的結構」就是**下界證明**的原料：分散式計算裡「在環上做 3-著色至少要 `Ω(log* n)` 輪」、資料結構的 cell-probe 下界、電路複雜度的下界，都用到 Ramsey 型論證。而 Turán 定理換個說法就是：衝突圖（Ch 15、Ch 17）邊不夠多時，一定有大的獨立集——可以同時排程的任務、可以共用暫存器的變數。

## 先建立直覺

經典的**派對問題**：6 個人的派對，任兩人要嘛互相認識、要嘛互不認識。證明：一定有 3 個人互相認識，或 3 個人互不認識。

```text
     把 6 個人畫成 K6，認識 = 紅邊，不認識 = 藍邊。

     挑任一人 v，他有 5 條邊。5 條邊塗兩色，
     由鴿籠原理，至少 3 條同色——不妨說紅色，接到 a、b、c。

          v
        / | \        若 a、b、c 之間有任何一條紅邊（例如 ab），
       a  b  c       則 v、a、b 是紅三角形。
                     否則 a、b、c 之間全是藍邊，就是藍三角形。
```

這個五行論證就是 `R(3,3) ≤ 6` 的完整證明。整個 Ramsey 理論都是它的推廣：**在一個點的鄰居裡遞迴**。

> 鴿籠原理與極端原理在 [Ch 2 — 證明工具箱](./02-proof-toolbox.md)；Ramsey 數的機率下界在 [Ch 31 — 機率方法](./31-probabilistic-method.md)。

## Ramsey 數：定義與 R(3,3) = 6

**定義**：`R(s, t)` 是最小的 n，使得 `K_n` 的任何紅藍 2-著色中，一定有紅色 `K_s` 或藍色 `K_t`。等價地（把紅邊當成圖 G）：任何 n 點圖 G，要嘛含 `K_s`，要嘛它的補圖含 `K_t`（G 有大小 t 的獨立集）。

**定理 32.1**：`R(3, 3) = 6`。

**證明**：上界 `R(3,3) ≤ 6` 就是派對問題的論證。下界 `R(3,3) > 5` 需要一個 `K_5` 的著色沒有單色三角形：把五邊形 `0-1-2-3-4-0` 塗紅，其餘 5 條邊（五角星 `0-2-4-1-3-0`）塗藍。紅、藍各是一個 5-cycle，5-cycle 沒有三角形。∎

用程式把兩件事都窮舉一遍：`K_5` 有 `2^10 = 1024` 種塗法，`K_6` 有 `2^15 = 32768` 種。

```python
from collections import Counter
from itertools import combinations


def mono_triangles(n, red):
    """red: 紅邊的集合（frozenset 的 pair）。回傳單色三角形個數。"""
    cnt = 0
    for a, b, c in combinations(range(n), 3):
        e = [frozenset(p) in red for p in ((a, b), (a, c), (b, c))]
        cnt += all(e) or not any(e)
    return cnt


for n in (5, 6):
    edges = [frozenset(p) for p in combinations(range(n), 2)]
    dist = Counter()
    for mask in range(1 << len(edges)):                 # 2^C(n,2) 種塗法
        red = {edges[i] for i in range(len(edges)) if mask >> i & 1}
        dist[mono_triangles(n, red)] += 1
    print(f"K{n}: {1 << len(edges)} colorings, #mono triangles -> #colorings: {dict(sorted(dist.items()))}")

# K5 的見證：紅色 = 5-cycle 0-1-2-3-4-0，藍色 = 它的補圖（另一個 5-cycle）
red = {frozenset((i, (i + 1) % 5)) for i in range(5)}
print("pentagon coloring of K5 has", mono_triangles(5, red), "monochromatic triangles")
```

輸出（本機約 0.5 秒）：

```text
K5: 1024 colorings, #mono triangles -> #colorings: {0: 12, 1: 260, 2: 270, 3: 300, 4: 100, 5: 60, 7: 20, 10: 2}
K6: 32768 colorings, #mono triangles -> #colorings: {2: 1760, 3: 5760, 4: 7500, 5: 6264, 6: 5040, 7: 3360, 8: 1530, 9: 720, 10: 432, 11: 160, 12: 90, 13: 120, 16: 30, 20: 2}
pentagon coloring of K5 has 0 monochromatic triangles
```

兩個額外的發現：

- `K_5` 恰好有 **12** 種沒有單色三角形的塗法——正好是 `K_5` 中 5-cycle 的個數（`4!/2 = 12`），每一種都是「紅色是一個 5-cycle」。五邊形著色在標號意義下是唯一的見證。
- `K_6` 的每一種塗法**至少有 2 個**單色三角形，不只 1 個。這是 Goodman（1959）公式的特例：`K_n` 的 2-著色中單色三角形至少有 `C(n,3) − ⌊(n/2)⌊((n−1)/2)²⌋⌋` 個，n = 6 時是 `20 − 18 = 2`。

**窮舉在這裡就走到盡頭了**：`K_7` 有 `2^21` 種塗法還可以，`R(4,4)` 需要看 `K_17` 的 `2^136` 種——宇宙裡的原子數大約 `2^266`，但這種窮舉早就不可行。下一節用 SAT 求解器繞過它。

## Ramsey 數存在嗎？遞迴上界

**定理 32.2（Erdős–Szekeres）**：對所有 s, t ≥ 2，`R(s, t) ≤ R(s−1, t) + R(s, t−1)`。邊界值 `R(s, 2) = s`、`R(2, t) = t`。

**證明**：令 `n = R(s−1, t) + R(s, t−1)`，考慮 `K_n` 的任一 2-著色，挑一個點 v。v 有 n−1 條邊，令 `N_R`、`N_B` 為紅、藍鄰居集合，`|N_R| + |N_B| = n − 1`。由鴿籠，`|N_R| ≥ R(s−1, t)` 或 `|N_B| ≥ R(s, t−1)`（否則 `|N_R| + |N_B| ≤ n − 2`）。

- 若 `|N_R| ≥ R(s−1, t)`：在 `N_R` 內部有紅色 `K_{s−1}`（加上 v 成為紅色 `K_s`）或藍色 `K_t`。
- 若 `|N_B| ≥ R(s, t−1)`：對稱。

邊界：`R(s, 2) = s`，因為 `K_s` 若有任何一條藍邊就是藍色 `K_2`，否則全紅就是紅色 `K_s`；而 `K_{s−1}` 全塗紅是反例。∎

**推論 32.3**：`R(s, t) ≤ C(s+t−2, s−1)`，特別是 `R(k, k) ≤ C(2k−2, k−1) < 4^k`。

**證明**：對 s + t 歸納。`C(s+t−3, s−2) + C(s+t−3, s−1) = C(s+t−2, s−1)`（Pascal 恆等式，Ch 3），邊界 `C(s, s−1) = s` 吻合。`C(2k−2, k−1)` 是 `(1+1)^{2k−2}` 展開的其中一項，所以 `< 4^{k−1} < 4^k`。∎

**加強：奇偶性**。若 `R(s−1, t)` 與 `R(s, t−1)` 都是偶數，上界可以嚴格改進一：`R(s, t) ≤ R(s−1, t) + R(s, t−1) − 1`。

**證明**：令 `a = R(s−1, t)`、`b = R(s, t−1)`，`n = a + b − 1`（奇數），假設某個 `K_n` 的著色沒有紅 `K_s` 也沒有藍 `K_t`。重複上面的論證：每個點的紅鄰居數必須 ≤ a − 1、藍鄰居數 ≤ b − 1，而兩者總和是 `n − 1 = a + b − 2`，所以**每個點**的紅度數恰好是 a − 1，這是奇數。於是紅色子圖有 n（奇數）個點、每個點度數都是奇數，度數總和是奇數——違反 handshake lemma（度數總和 = 2 × 邊數）。∎

套用：`R(3,4) ≤ R(2,4) + R(3,3) = 4 + 6 = 10`，兩者都是偶數，所以 `R(3,4) ≤ 9`。

### 用 z3 確認 R(3,4) = 9

`R(3,4) ≤ 9` 是證明；`R(3,4) > 8` 要一個見證。與其手找，把「`K_8` 沒有紅 `K_3` 也沒有藍 `K_4`」寫成 SAT：每條邊一個布林變數（紅 = True），每個 3 點子集至少一條藍邊、每個 4 點子集至少一條紅邊。

```python
import time
from itertools import combinations

import z3


def ramsey_coloring(n, s, t):
    """找 K_n 的紅藍著色，沒有紅色 K_s 也沒有藍色 K_t；回傳紅邊集合或 None。"""
    red = {e: z3.Bool(f"r_{e[0]}_{e[1]}") for e in combinations(range(n), 2)}
    solver = z3.Solver()
    for S in combinations(range(n), s):                        # 每個 s 子集至少一條藍邊
        solver.add(z3.Or([z3.Not(red[e]) for e in combinations(S, 2)]))
    for T in combinations(range(n), t):                        # 每個 t 子集至少一條紅邊
        solver.add(z3.Or([red[e] for e in combinations(T, 2)]))
    if solver.check() == z3.sat:
        m = solver.model()
        return {e for e, v in red.items() if z3.is_true(m.eval(v))}
    return None


for n in (8, 9):
    t0 = time.perf_counter()
    col = ramsey_coloring(n, 3, 4)
    dt = time.perf_counter() - t0
    if col is None:
        print(f"K{n}: UNSAT -> every coloring has red K3 or blue K4   ({dt:.2f} s)")
    else:
        # 獨立驗證 z3 給的見證
        assert not any(all(e in col for e in combinations(S, 2)) for S in combinations(range(n), 3))
        assert not any(all(e not in col for e in combinations(T, 2)) for T in combinations(range(n), 4))
        degs = [sum(1 for e in col if v in e) for v in range(n)]
        print(f"K{n}: SAT, red edges = {len(col)}, red degrees = {degs}   ({dt:.2f} s)")
```

輸出：

```text
K8: SAT, red edges = 10, red degrees = [3, 2, 3, 2, 3, 2, 3, 2]   (0.02 s)
K9: UNSAT -> every coloring has red K3 or blue K4   (0.10 s)
```

（時間在本機量測，不同機器與 z3 版本會不同。）`K_8` 的見證有 10 條紅邊，構成一張無三角形、補圖無 `K_4` 的圖；`K_9` 是 UNSAT，和奇偶性論證吻合。注意我們沒有盲信 z3 的 SAT 答案：兩行 `assert` 用暴力法獨立驗證了見證。UNSAT 的那一側則是信任 z3 的正確性——要嚴格一點，可以讓求解器輸出 DRAT 證明再用獨立檢查器驗證，這是 SAT 社群處理大型組合證明（例如 Boolean Pythagorean triples）的標準做法。

> SAT 編碼與求解器內部在 [programming/sat_smt](../../programming/sat_smt/README.md) 課程；Ch 35 會系統地把圖問題歸約到 SAT。

### R(4,4) > 17：Paley 圖

**Paley 圖** `P_q`（q 是 ≡ 1 mod 4 的質數）：點是 `Z_q`，x、y 相鄰若 `x − y` 是模 q 的非零平方數。q ≡ 1 mod 4 保證 −1 是平方數，所以「x − y 是平方」與「y − x 是平方」等價，圖是無向的。Ch 30 用 Paley(101) 當過偽隨機圖。

```python
import networkx as nx

q = 17
squares = {(x * x) % q for x in range(1, q)}
P = nx.Graph([(x, y) for x in range(q) for y in range(x + 1, q) if (x - y) % q in squares])
clique = max(len(c) for c in nx.find_cliques(P))
indep = max(len(c) for c in nx.find_cliques(nx.complement(P)))
print(f"Paley(17): {P.number_of_edges()} edges, 8-regular? {set(dict(P.degree()).values()) == {8}}")
print(f"  max clique = {clique}, max independent set = {indep}")
print(f"  self-complementary? {nx.is_isomorphic(P, nx.complement(P))}")
print("  => red = Paley edges, blue = the rest: no monochromatic K4, so R(4,4) > 17")
```

輸出：

```text
Paley(17): 68 edges, 8-regular? True
  max clique = 3, max independent set = 3
  self-complementary? True
  => red = Paley edges, blue = the rest: no monochromatic K4, so R(4,4) > 17
```

配合遞迴上界 `R(4,4) ≤ R(3,4) + R(4,3) = 18`，得到 `R(4,4) = 18`。這是 Greenwood 與 Gleason 1955 年的結果，用的正是這張圖。

### 已知值與差距

| R(s,t) | 已知值或範圍 | 遞迴上界 `C(s+t−2, s−1)` | 機率下界（Ch 31） |
| --- | --- | --- | --- |
| R(3,3) | 6 | 6 | 3 |
| R(3,4) | 9 | 10（奇偶性改進到 9） | — |
| R(3,5) | 14 | 15 | — |
| R(4,4) | 18 | 20 | 6 |
| R(4,5) | 25 | 35 | — |
| R(5,5) | 43 ≤ R(5,5) ≤ 46 | 70 | 11 |

表中的已知值取自 Radziszowski 持續更新的動態綜述〈Small Ramsey Numbers〉（本課撰寫時查閱的是 2026 年的版本）；`R(5,5) ≤ 46` 是 Angeltveit 與 McKay 2024 年的電腦輔助結果（arXiv 預印本）。`R(5,5)` 的確切值至今未知——即使有 SAT 求解器和大量計算資源。

漸近上：`2^{k/2} < R(k,k) < 4^k`。上界的指數 4 從 1935 年起近 90 年沒有改進，直到 Campos、Griffiths、Morris、Sahasrabudhe 證明了 `R(k,k) ≤ (4 − ε)^k`（2023 年預印本，2026 年刊於 Annals of Mathematics）；Gupta、Ndiaye、Norin、Wei 隨後把常數改進到約 3.8（2024 年預印本）。下界的 `√2` 從 1947 年至今沒有改進。

## 極值圖論：Mantel 定理

**定義**：`ex(n, H)` = n 點、不含 H 作為子圖（不必是 induced）的圖，最多有幾條邊。

**定理 32.4（Mantel 1907）**：`ex(n, K_3) = ⌊n²/4⌋`，等號由完全二分圖 `K_{⌊n/2⌋, ⌈n/2⌉}` 達到。

**證明**：設 G 無三角形，m 條邊。對每條邊 uv，u 與 v 沒有共同鄰居（否則形成三角形），所以 `N(u)` 與 `N(v)` 不相交，`deg(u) + deg(v) ≤ n`。對所有邊加總：

```text
Σ_{uv ∈ E} (deg(u) + deg(v)) ≤ m n
```

左邊中，每個點 v 出現 deg(v) 次（每條鄰邊一次），每次貢獻 deg(v)，所以左邊 = `Σ_v deg(v)²`。由 Cauchy–Schwarz，`Σ deg(v)² ≥ (Σ deg(v))² / n = (2m)²/n`。合起來 `4m²/n ≤ mn`，即 `m ≤ n²/4`。m 是整數，所以 `m ≤ ⌊n²/4⌋`。`K_{⌊n/2⌋,⌈n/2⌉}` 無三角形（二分圖無奇環）且有 `⌊n/2⌋⌈n/2⌉ = ⌊n²/4⌋` 條邊。∎

## Turán 定理

**Turán 圖** `T(n, r)`：把 n 個點盡量平均地分成 r 組（每組大小 `⌊n/r⌋` 或 `⌈n/r⌉`），不同組之間全部連邊、同組之間不連。它是完全 r 部圖，所以不含 `K_{r+1}`（任 r+1 個點必有兩個同組）。邊數記為 `t(n, r)`。

**定理 32.5（Turán 1941）**：`ex(n, K_{r+1}) = t(n, r)`，而且 `T(n, r)` 是**唯一**的極值圖。此外 `t(n, r) ≤ (1 − 1/r) n²/2`。

Mantel 是 r = 2 的情況。

### 證明（Zykov 對稱化）

設 G 是 n 點、不含 `K_{r+1}`、邊數最多的圖，m 條邊。**複製點**：「把 x 換成 y 的複製品」是指刪掉 x，再加一個新點 y'，y' 的鄰居恰好是 `N(y)`（y' 與 y 不相鄰）。若 x 與 y 不相鄰，換完之後：

- 仍然不含 `K_{r+1}`：任何團最多包含 y 與 y' 其中之一（兩者不相鄰），而 y' 和 y 鄰居相同，所以含 y' 的團把 y' 換成 y 也是團，原圖就有。
- 邊數變成 `m − deg(x) + deg(y)`（x 不在 `N(y)` 中，所以刪 x 不影響 y' 的鄰居數）。

**步驟 1：「不相鄰」是一個等價關係。** 反身與對稱由定義直接成立（簡單圖沒有自環，每個點與自己不相鄰；無向圖的不相鄰是對稱的）。要證的是遞移性：若 `u ≁ v`、`v ≁ w`，則 `u ≁ w`。假設反過來 `u ~ w`。

- 若 `deg(v) < deg(u)`：把 v 換成 u 的複製品（v ≁ u，可以換），邊數變成 `m − deg(v) + deg(u) > m`，與 m 最大矛盾。所以 `deg(v) ≥ deg(u)`；同理 `deg(v) ≥ deg(w)`。
- 現在把 u 和 w **都**換成 v 的複製品（兩個新點彼此不相鄰、也不和 v 相鄰）。刪掉 u 和 w 共失去 `deg(u) + deg(w) − 1` 條邊（邊 uw 被數了兩次）；兩個複製品各有 `deg(v)` 條邊（u、w 都不在 `N(v)` 中）。新邊數 `= m − deg(u) − deg(w) + 1 + 2 deg(v) ≥ m + 1`，又矛盾。

所以 `u ≁ w`，遞移性成立。

**步驟 2：G 是完全多部圖。** 「不相鄰」的等價類把點分成若干組：同組不相鄰、不同組全部相鄰。因為不含 `K_{r+1}`，組數 ≤ r（從每組各取一點就是一個團）。

**步驟 3：各組大小盡量平均。** 若有兩組大小 `a ≥ b + 2`，從大組移一個點到小組，邊數變化 `= (a − 1) − b > 0`（這個點原本連到小組的 b 個點，移過去之後改連到大組剩下的 a−1 個點，其他不變）。所以最大化時任兩組大小差 ≤ 1；組數不足 r 時，從某個大小 ≥ 2 的組拆一個點出來當新組，嚴格增加邊數（這個點多連到原組其他點）——除非每組大小都是 1，也就是 n ≤ r、G = K_n = T(n, r)。

三步合起來：G 必定是 `T(n, r)`，這同時證明了最大邊數與唯一性。∎

**上界** `t(n, r) ≤ (1 − 1/r) n²/2`：`t(n,r) = (n² − Σ n_i²)/2`，由 Cauchy–Schwarz `Σ n_i² ≥ n²/r`。

**和 Ch 31 的連結**：Turán 定理取補圖就是「平均度數 d 的圖有大小 ≥ n/(d+1) 的獨立集」，這正是 Caro–Wei 在正則圖上的版本。一個是極值論證，一個是機率論證，殊途同歸。

### 在所有 ≤ 7 點的圖上驗證

```python
import networkx as nx


def has_clique(G, r):
    return any(len(c) >= r for c in nx.find_cliques(G)) if G.number_of_edges() else r <= 1


def turan_edges(n, r):
    """Turán 圖 T(n, r)：n 點平均分成 r 份的完全 r 部圖的邊數。"""
    q, s = divmod(n, r)
    sizes = [q + 1] * s + [q] * (r - s)
    return (n * n - sum(x * x for x in sizes)) // 2


atlas = nx.graph_atlas_g()[1:]
for r in (2, 3):                                    # 禁止 K_{r+1}
    print(f"forbid K{r + 1}:")
    for n in range(r, 8):                          # nx.turan_graph 要求 r <= n
        cands = [G for G in atlas if G.number_of_nodes() == n and not has_clique(G, r + 1)]
        ex = max(G.number_of_edges() for G in cands)
        extremal = [G for G in cands if G.number_of_edges() == ex]
        T = nx.turan_graph(n, r)
        unique = len(extremal) == 1 and nx.is_isomorphic(extremal[0], T)
        assert ex == turan_edges(n, r) and unique
        print(f"  n={n}: ex = {ex:2d}  Turan t(n,{r}) = {turan_edges(n, r):2d}  "
              f"#extremal graphs = {len(extremal)} (isomorphic to T({n},{r}): {unique})")
```

輸出：

```text
forbid K3:
  n=2: ex =  1  Turan t(n,2) =  1  #extremal graphs = 1 (isomorphic to T(2,2): True)
  n=3: ex =  2  Turan t(n,2) =  2  #extremal graphs = 1 (isomorphic to T(3,2): True)
  n=4: ex =  4  Turan t(n,2) =  4  #extremal graphs = 1 (isomorphic to T(4,2): True)
  n=5: ex =  6  Turan t(n,2) =  6  #extremal graphs = 1 (isomorphic to T(5,2): True)
  n=6: ex =  9  Turan t(n,2) =  9  #extremal graphs = 1 (isomorphic to T(6,2): True)
  n=7: ex = 12  Turan t(n,2) = 12  #extremal graphs = 1 (isomorphic to T(7,2): True)
forbid K4:
  n=3: ex =  3  Turan t(n,3) =  3  #extremal graphs = 1 (isomorphic to T(3,3): True)
  n=4: ex =  5  Turan t(n,3) =  5  #extremal graphs = 1 (isomorphic to T(4,3): True)
  n=5: ex =  8  Turan t(n,3) =  8  #extremal graphs = 1 (isomorphic to T(5,3): True)
  n=6: ex = 12  Turan t(n,3) = 12  #extremal graphs = 1 (isomorphic to T(6,3): True)
  n=7: ex = 16  Turan t(n,3) = 16  #extremal graphs = 1 (isomorphic to T(7,3): True)
```

atlas 每個同構類只收一張圖，所以「#extremal graphs = 1」驗證的正是「同構意義下唯一」。一開始 `range(2, 8)` 跑 r = 3 時，`nx.turan_graph(2, 3)` 丟出 `NetworkXError: Must satisfy 1 <= r <= n`：NetworkX 不接受「組數比點數多」。數學上 n < r 時 T(n, r) 就是 K_n，我們的 `turan_edges` 也會給出 `C(n,2)`，但對拍時要從 n = r 開始。

## Erdős–Stone 定理：色數決定一切

Turán 處理了禁止完全圖。禁止一般的 H 呢？答案出奇地乾淨：

**定理 32.6（Erdős–Stone 1946；Simonovits 指出推論）**：對任何至少有一條邊的圖 H，

```text
ex(n, H) = (1 − 1/(χ(H) − 1) + o(1)) · n²/2
```

其中 χ(H) 是 H 的色數（Ch 15）。**本章只陳述，不證明**；證明需要 Szemerédi 正則引理或一個較直接的計數論證，見延伸閱讀的 Diestel 第 7 章。

直覺：`T(n, χ(H) − 1)` 是 `(χ(H)−1)`-部圖，它的色數是 `χ(H) − 1`，所以容不下 H；Erdős–Stone 說它在主項上已經是最好的了。

**它對二分圖什麼也沒說**：χ(H) = 2 時，主項是 `(1 − 1)n²/2 = 0`，只得到 `ex(n, H) = o(n²)`。二分 H 的極值問題是另一個世界，至今很多是開放問題。最經典的是 `C_4`：Kővári–Sós–Turán 給出 `ex(n, C_4) = O(n^{3/2})`，而有限射影平面的構造說明這個量級是對的。

```python
import networkx as nx


def contains_cycle_len(G, k):
    """G 是否含長度恰為 k 的圈（子圖，不必是 induced）。n <= 7 用 DFS 暴力。"""
    adj = {u: set(G[u]) for u in G}

    def dfs(start, v, depth, visited):
        if depth == k:
            return start in adj[v]
        return any(dfs(start, w, depth + 1, visited | {w}) for w in adj[v] if w not in visited and w > start)

    return any(dfs(s, s, 1, {s}) for s in adj)


atlas = nx.graph_atlas_g()[1:]
print(f"{'n':>2s} {'ex(n,C4)':>8s} {'ex(n,C5)':>8s} {'n^2/4':>6s} {'C(n,2)':>6s}")
for n in range(4, 8):
    graphs = [G for G in atlas if G.number_of_nodes() == n]
    ex4 = max(G.number_of_edges() for G in graphs if not contains_cycle_len(G, 4))
    ex5 = max(G.number_of_edges() for G in graphs if not contains_cycle_len(G, 5))
    print(f"{n:2d} {ex4:8d} {ex5:8d} {n * n // 4:6d} {n * (n - 1) // 2:6d}")
```

輸出：

```text
 n ex(n,C4) ex(n,C5)  n^2/4 C(n,2)
 4        4        6      4      6
 5        6        7      6     10
 6        7        9      9     15
 7        9       12     12     21
```

（DFS 裡的 `w > start` 讓每個圈只從它最小的點出發，避免重複搜尋。）

- `C_5` 的色數是 3，Erdős–Stone 預測 `ex(n, C_5) ≈ n²/4`。n = 6、7 時恰好等於 `⌊n²/4⌋`（極值圖是完全二分圖，沒有奇環）；n = 4、5 的「異常」是小 n 效應：`K_4` 根本沒有 5 個點，當然沒有 `C_5`；n = 5 時 K4 加一個葉子有 7 條邊也沒有 `C_5`。o(1) 項在小 n 上可以很大，這正是漸近定理的限制。
- `C_4` 是二分的，`ex(n, C_4)` 的成長明顯慢於 `n²/4`，和 `Θ(n^{3/2})` 一致（n = 7 時 `n^{3/2}/2 ≈ 9.3`，實際是 9；這個常數吻合只是巧合般的好看，不要過度解讀小 n 的數值）。

## 對比與取捨

| 問題 | 問的是 | 精確解的狀況 | 主要工具 |
| --- | --- | --- | --- |
| Ramsey `R(s,t)` | 著色後躲不掉的單色團 | 只知道極少數小值；`R(5,5)` 未知 | 鄰域遞迴（上界）、機率法與代數構造（下界）、SAT（小值） |
| Turán `ex(n, K_{r+1})` | 邊多到躲不掉的團 | 完全解出，極值圖唯一 | 對稱化、Cauchy–Schwarz |
| Erdős–Stone `ex(n, H)` | 邊多到躲不掉的一般子圖 | 主項由 χ(H) 決定；二分 H 只知道 `o(n²)` | 正則引理 / 計數 |
| 二分 H 的 `ex(n, H)` | 稀疏的極值 | 大多開放；`C_4`、`K_{2,t}` 等少數知道量級 | 代數構造、計數 |

**為什麼 Ramsey 這麼難、Turán 這麼容易？** Turán 問的是「邊數」這一個參數的極值，對稱化把任何極值圖推到唯一的對稱形狀。Ramsey 問的是**兩種顏色同時**避開結構，紅色要稀疏才能沒有 `K_s`，藍色（補圖）要稀疏才能沒有 `K_t`，兩個要求互相拉扯，極值著色沒有對稱的形狀——Paley 圖之所以有用，正是因為它是自補的偽隨機圖。

## 踩雷集錦

1. **`ex(n, H)` 裡的「不含」是子圖，不是 induced 子圖**：`K_4` 含有 `C_4` 作為子圖（不是 induced）。用 `nx.is_isomorphic` 比 induced 子圖會算錯；本章 `contains_cycle_len` 找的是任意子圖意義下的圈。
2. **以為 R(s,t) 可以靠窮舉算**：`K_n` 有 `2^{C(n,2)}` 種著色，n = 7 就有 200 萬種；`R(4,4)` 要看 `K_17` 的 `2^136` 種。小值需要 SAT、對稱性破除與大量計算；`R(5,5)` 至今算不出來。
3. **相信 SAT 求解器的 UNSAT 卻驗證它的 SAT**：這兩者的可信度不同。SAT 的見證可以獨立檢查（本章兩行 `assert`）；UNSAT 要嘛相信求解器，要嘛要求它輸出可驗證的證明（DRAT）。
4. **Paley 圖的參數**：q 必須是質數（或質數冪，用有限體）而且 `q ≡ 1 (mod 4)`；q = 19 時 −1 不是平方數，`(x − y)` 是平方不代表 `(y − x)` 是平方，程式會建出一張「依點的順序而定」的錯誤無向圖，不會報錯。
5. **把 Erdős–Stone 用在小 n**：它是漸近定理，o(1) 項不受控。表中 n = 5 的 `ex(5, C_5) = 7 > ⌊25/4⌋ = 6` 就是例子。

## 進階：再往深一層

- **Szemerédi 正則引理**：任何夠大的圖都能切成有界個部分，使得幾乎每對部分之間的邊都「像隨機的」（ε-regular）。它是 Erdős–Stone、三角形移除引理、以及 Szemerédi 等差數列定理組合證明的核心工具；代價是常數是塔狀指數，演算法上幾乎沒用。
- **超圖 Ramsey 與 Ramsey 的邏輯起源**：Ramsey 原始的定理是關於 k-元子集的著色，超圖版本的 Ramsey 數成長快得多（塔函數）。Paris–Harrington 定理給出一個 Ramsey 型的有限組合命題，在 Peano 算術中不可證——這是 Gödel 不完備定理的一個「自然」例子。
- **Ramsey 在 CS 下界的用法**：Linial 證明環上的分散式 3-著色需要 `Ω(log* n)` 輪，論證的骨架是：如果演算法太快，就可以從它構造出一個著色，與某個 Ramsey 型的不可能性矛盾。資料結構的 cell-probe 下界與 Yao 的 minimax 原理也常搭配 Ramsey 論證，把任意演算法「縮減」到行為規律的子集合上。

## 動手練習

1. 不看本章，證明 `R(3,5) ≤ 14`（用遞迴上界；想想這裡為什麼用不到奇偶性改進），再用 z3 驗證 `K_13` 存在沒有紅 `K_3`、沒有藍 `K_5` 的著色。（提示：Paley 型的「循環圖」——點是 `Z_13`，差值屬於某個集合時相連——是好的候選。）
2. 用 Zykov 對稱化的同一套論證，證明「n 點、沒有 `K_{r+1}` 的圖，任意點的最小度數 ≤ `(1 − 1/r) n`」。
3. 寫一個程式，對 `nx.gnp_random_graph(n, p, seed=s)` 的 n = 7，比較「實際邊數」與「不含三角形時 Mantel 上界」，數出 1000 個 seed 中有多少張無三角形圖的邊數達到上界的 90% 以上，並解釋為什麼幾乎沒有。
4. 實作 Goodman 公式，在 n = 4..7 上和窮舉的最少單色三角形數對拍（n = 7 要 `2^21` 種塗法，純 Python 可能要跑上數分鐘，可以先跑 n ≤ 6）。

## 本章重點整理

- `R(3,3) = 6`：一個點的五條邊用鴿籠；五邊形是唯一的見證型態；`K_6` 至少有 2 個單色三角形。
- `R(s,t) ≤ R(s−1,t) + R(s,t−1)`，兩者都偶數時再減一；所以 `R(k,k) < 4^k`，`R(3,4) = 9`、`R(4,4) = 18`（Paley(17) 給下界）。
- Mantel：無三角形 ⇒ `m ≤ n²/4`，證明 = 鄰域不交 + Cauchy–Schwarz。
- Turán：無 `K_{r+1}` ⇒ `m ≤ t(n,r)`，唯一極值圖是 Turán 圖；Zykov 對稱化證明「不相鄰」是等價關係。
- Erdős–Stone：`ex(n,H) = (1 − 1/(χ(H)−1) + o(1)) n²/2`；二分 H 只得到 `o(n²)`。

## 自我檢核

- [ ] 不看筆記，五分鐘內寫出 `R(s,t) ≤ R(s−1,t) + R(s,t−1)` 的證明，以及奇偶性改進那一步用到的 handshake lemma。
- [ ] 能解釋 Zykov 對稱化證明中「把 u 和 w 都換成 v 的複製品」時，為什麼邊數只少了 `deg(u) + deg(w) − 1`。
- [ ] 如果有人問「`ex(n, C_4)` 是不是 `n²/4` 量級」，你能用 Erdős–Stone 說明為什麼不是，並說出正確的量級？
- [ ] 能說出用 SAT 求解器「證明」一個 Ramsey 數時，SAT 和 UNSAT 兩種答案各自需要什麼程度的信任。
- [ ] 想一個你工作中的衝突圖（排程、暫存器、鎖）：Turán 定理告訴你，邊數低於多少時一定存在大小 r 的「可同時執行」集合？

## 延伸閱讀

### 綜述與書籍

- **[Small Ramsey Numbers](https://www.combinatorics.org/ojs/index.php/eljc/article/view/DS1)** — Stanisław P. Radziszowski，Electronic Journal of Combinatorics Dynamic Survey DS1（1994 年起持續修訂，本課查閱 2026 年版本）
  - **這份資料的定位**：所有已知小 Ramsey 數與上下界的權威表格，持續更新，每個數字都附原始文獻。
  - **讀哪裡**：開頭兩色古典 Ramsey 數 `R(k,l)` 的表格；本章「已知值」表就是從這裡來的。
  - **前提知識**：本章即可；表格之外的多色與超圖部分可選讀。
- **《Graph Theory》** — Reinhard Diestel（Springer GTM 173）
  - **這本書的定位**：README 列的主參考之一；極值與 Ramsey 兩章寫得精煉而完整。
  - **讀哪幾章**：「Extremal Graph Theory」一章（Turán、Erdős–Stone 與正則引理的證明）、「Ramsey Theory for Graphs」一章（Ramsey 定理與其推廣）。
  - **前提知識**：本章；正則引理那節需要耐心。
- **《Proofs from THE BOOK》** — Martin Aigner、Günter M. Ziegler（Springer）
  - **這本書的定位**：「最漂亮的證明」合集。
  - **讀哪幾章**：「Turán's graph theorem」一章，收錄五個不同的 Turán 證明（包括本章的對稱化與一個機率證明）；章號依版本不同，請以章名查找。
  - **前提知識**：本章。

### 論文

- **[Combinatorial relations and chromatic graphs](https://doi.org/10.4153/CJM-1955-001-4)** — R. E. Greenwood、A. M. Gleason，Canadian Journal of Mathematics 7（1955），1–7
  - **核心貢獻**：確定 `R(3,4) = 9`、`R(3,5) = 14`、`R(4,4) = 18` 與三色的 `R(3,3,3) = 17`，用的是有限體上的平方剩餘構造。
  - **讀哪裡**：全文 7 頁，構造部分與本章的 Paley(17) 直接對應。
  - **和本章的關聯**：「R(4,4) > 17」一節。
- **[On the structure of linear graphs](https://www.ams.org/bull/1946-52-12/S0002-9904-1946-08715-7/)** — P. Erdős、A. H. Stone，Bulletin of the AMS 52（1946），1087–1091
  - **核心貢獻**：Erdős–Stone 定理的原始論文。
  - **讀哪裡**：主定理陳述；證明的原始寫法比現代教科書版本難讀，建議先讀 Diestel 再回來看。
  - **和本章的關聯**：定理 32.6。
- **[An exponential improvement for diagonal Ramsey](https://annals.math.princeton.edu/2026/203-3/p04)** — Marcelo Campos、Simon Griffiths、Robert Morris、Julian Sahasrabudhe，Annals of Mathematics 203(3)（2026）；arXiv:2303.09521（2023）
  - **核心貢獻**：首次把 `R(k,k) ≤ 4^k` 的指數改進為 `(4 − ε)^k`，用的是一個稱為「book algorithm」的精巧鄰域遞迴。
  - **讀哪裡**：引言與證明概覽（sketch）；完整證明很長。
  - **和本章的關聯**：本章遞迴上界的現代後續。

這一章的 Ramsey 與 Turán 都是「確定性」的極值。下一章回到隨機：一張隨機圖隨著邊的機率 p 增加，什麼時候突然出現三角形、巨大連通分量、整體連通？答案是一連串陡峭的**相變**。

→ [Ch 33 隨機圖與相變](./33-random-graphs.md)
