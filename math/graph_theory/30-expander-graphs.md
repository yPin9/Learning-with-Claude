# Ch 30 — Expander graph

> **目標**：理解 expander 的兩種定義（邊擴張與譜擴張）怎麼互相轉換，完整證明 expander mixing lemma 與「expander 上的隨機遊走幾乎像獨立取樣」，實測隨機正則圖的第二特徵值貼著 Ramanujan 界 `2√(d−1)`，並知道 expander 在錯誤更正碼、去隨機化、資料中心網路與密碼學裡各扮演什麼角色。
>
> **環境**：Python 3.13.12、NetworkX 3.7、NumPy 2.5.4、SciPy 1.18.1，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

設計一個網路，有兩個互相衝突的目標：

- **便宜**：每個節點只接固定幾條線（度數 d 是常數，例如交換器的埠數）。
- **強健**：任意切掉一部分節點或連線，網路仍然連通、而且沒有瓶頸；資料從任一點出發，很快就能擴散到全網。

完全圖最強健，但度數是 n−1，太貴；圈最便宜，但切兩條線就斷成兩半，擴散要 `Θ(n²)` 步（Ch 29 的 C64）。**Expander** 是這兩個極端之間的甜蜜點：度數固定，卻像完全圖一樣「沒有任何窄頸」。

歷史上，這個需求最早來自 1970 年代的電話交換網路與 superconcentrator 的研究。Pinsker 用機率論證證明了「隨機的稀疏圖幾乎都是 expander」——存在性很容易，但沒有人知道怎麼**明確構造**一個。Margulis 在 1973 年用群表示論給出第一個明確構造；1980 年代 Alon、Milman 把擴張性和特徵值連起來（Ch 28 的 Cheeger 不等式），1988 年 Lubotzky–Phillips–Sarnak 與 Margulis 各自構造出譜上最優的 **Ramanujan 圖**。之後 expander 變成理論計算機科學的萬用零件：錯誤更正碼、去隨機化、PCP 定理、log-space 演算法、密碼雜湊——Hoory、Linial、Wigderson 2006 年那篇 100 多頁的綜述，標題就叫〈Expander graphs and their applications〉。

## 先建立直覺

想像一張度數 d 的圖，任選一群點 S（不超過一半）。「好的擴張」是說：S 往外連的邊很多，跨出去的邊數至少是 S 大小的常數倍。

```text
   壞的擴張（圈）                         好的擴張（expander）
   S = 連續的一段                         S = 任意一群點

   ●─●─●─●─●─○─○─○─○─○─○                    ┌───────┐ ═══════ ○
   └── S ──┘                                │  S    │ ═══════ ○
   只有 2 條邊跨出去，不管 S 多大           │ ●●●●  │ ═══════ ○
                                            └───────┘ 跨出去的邊 ≥ c·|S|
```

expander 的另一個面貌是「**看起來像隨機圖**」：任選兩群點 S 與 T，它們之間的邊數，大約就是隨機圖該有的 `d|S||T|/n`，誤差被第二特徵值控制。這就是 expander mixing lemma，它把「擴張」從一個切割性質變成一個**偽隨機**性質——這正是 expander 能拿來去隨機化的原因。

> 擴張性和 λ_2 的關係（Cheeger 不等式）在 [Ch 28 — Laplacian、矩陣樹定理與 Cheeger 不等式](./28-laplacian-cheeger.md)；隨機遊走的 mixing time 與 spectral gap 在 [Ch 29 — 隨機遊走與 PageRank](./29-random-walks-pagerank.md)。

## 兩種定義

本章只談 d-正則圖。鄰接矩陣特徵值 `d = λ_1 ≥ λ_2 ≥ ... ≥ λ_n ≥ −d`（Ch 27）。

**邊擴張**：`h(G) = min_{0 < |S| ≤ n/2} |E(S, S̄)| / |S|`（Ch 28）。

**譜擴張**：令 `λ(G) = max(|λ_2|, |λ_n|)`，也就是除了 `λ_1 = d` 之外的最大特徵值絕對值。一張 n 點 d-正則、`λ(G) ≤ λ` 的圖叫做 **(n, d, λ)-圖**。λ 越小越好；`d − λ_2` 叫做（單邊）spectral gap。

兩者由 Cheeger 不等式綁在一起（Ch 28 定理 28.5，`L = dI − A`，所以 Laplacian 的 λ_2 是 `d − λ_2(A)`）：

```text
(d − λ_2) / 2  ≤  h(G)  ≤  √(2d (d − λ_2))
```

**「expander」嚴格說是一個圖族的性質**：一族 d-正則圖 `G_1, G_2, ...`，點數趨近無限，而 `h(G_i) ≥ ε > 0`（或等價地 `λ_2(G_i) ≤ d − ε'`）對所有 i 成立。單獨一張有限圖永遠有正的 h，說它「是 expander」沒有意義；我們說它是某個 (n, d, λ)-圖。

**為什麼要兩側的 λ？** 隨機遊走的收斂（Ch 29）與 mixing lemma 都需要 `|λ_n|` 也小；二分圖 `λ_n = −d`，兩側定義下永遠不是好 expander，但單邊 gap 可以很大。下表會看到超立方體的例子。

```python
import networkx as nx
import numpy as np


def paley(q):
    """Paley graph：頂點 Z_q，x~y 若 x-y 是非零平方數。q 必須是 ≡ 1 (mod 4) 的質數。"""
    squares = {(x * x) % q for x in range(1, q)}
    G = nx.Graph()
    G.add_nodes_from(range(q))
    G.add_edges_from((x, y) for x in range(q) for y in range(x + 1, q) if (x - y) % q in squares)
    return G


def two_sided_lambda(G):
    lam = np.sort(np.linalg.eigvalsh(nx.to_numpy_array(G)))
    return lam[-1], lam[-2], lam[0], max(abs(lam[-2]), abs(lam[0]))


cases = {
    "cycle C101": nx.cycle_graph(101),
    "Petersen": nx.petersen_graph(),
    "hypercube Q7": nx.hypercube_graph(7),
    "Paley(101)": paley(101),
    "rand 3-reg n=100": nx.random_regular_graph(3, 100, seed=0),
    "rand 7-reg n=128": nx.random_regular_graph(7, 128, seed=0),
}
print(f"{'graph':17s} {'n':>4s} {'d':>3s} {'lam2':>7s} {'lam_min':>8s} {'lambda':>7s} {'2sqrt(d-1)':>10s} {'Ramanujan':>9s}")
for name, G in cases.items():
    degs = {d for _, d in G.degree()}
    assert len(degs) == 1
    d = degs.pop()
    l1, l2, ln, lam = two_sided_lambda(G)
    rb = 2 * np.sqrt(d - 1)
    print(f"{name:17s} {G.number_of_nodes():4d} {d:3d} {l2:7.3f} {ln:8.3f} {lam:7.3f} {rb:10.3f} {str(lam <= rb + 1e-9):>9s}")
```

輸出：

```text
graph                n   d    lam2  lam_min  lambda 2sqrt(d-1) Ramanujan
cycle C101         101   2   1.996   -1.999   1.999      2.000      True
Petersen            10   3   1.000   -2.000   2.000      2.828      True
hypercube Q7       128   7   5.000   -7.000   7.000      4.899     False
Paley(101)         101  50   4.525   -5.525   5.525     14.000      True
rand 3-reg n=100   100   3   2.826   -2.800   2.826      2.828      True
rand 7-reg n=128   128   7   4.648   -4.839   4.839      4.899      True
```

逐列讀：

- **圈「是 Ramanujan」是個陷阱**：d = 2 時 `2√(d−1) = 2 = d`，界是平凡的，任何 2-正則圖都滿足。λ = 1.999 ≈ d 才是重點：圈的 spectral gap 趨近 0，是最差的 expander。Ramanujan 的定義只對 d ≥ 3 有意義。
- **超立方體**：二分圖，`λ_min = −7 = −d`，兩側 λ 等於 d；單邊 gap `d − λ_2 = 2`，不錯但不隨 d 成長。而且 Q_d 的度數 `d = log₂ n` 會長，不是固定度數的族。
- **Paley 圖**是明確構造（只用到模 q 的平方數），特徵值是 `(−1 ± √q)/2`，`λ = 5.525` 遠小於 `2√49 = 14`。它是稠密圖（`d = (q−1)/2`），不是我們最想要的稀疏 expander，但它是「偽隨機圖」的經典例子，Ch 32 會用它證明 Ramsey 下界。
- **隨機正則圖**：d = 3 與 d = 7 的 λ 都貼著 `2√(d−1)`，差距在小數第二位。這不是巧合，下一節解釋。

## Expander mixing lemma

對點集 S、T，令 `e(S, T) = #{(s, t) : s ∈ S, t ∈ T, st ∈ E}`（**有序對**；S∩T 裡的邊會被算兩次）。在「隨機」的世界裡，每對點以 `d/n` 的密度相連，預期 `e(S, T) ≈ d|S||T|/n`。

**定理 30.1（Expander mixing lemma）**：對 (n, d, λ)-圖與任意 S, T ⊆ V，

```text
| e(S, T) − d|S||T|/n |  ≤  λ √(|S| |T|)
```

**證明**：令 `1_S`、`1_T` 為指示向量，則 `e(S, T) = 1_S^T A 1_T`。把它們分解成「平均部分」與「與 1 正交的部分」：

```text
1_S = (|S|/n) 1 + s,    s ⟂ 1
1_T = (|T|/n) 1 + t,    t ⟂ 1
```

展開 `1_S^T A 1_T`，四項分別是：

- `(|S|/n)(|T|/n) 1^T A 1 = (|S||T|/n²) · dn = d|S||T|/n`（`A1 = d1`，`1^T 1 = n`）。
- `(|S|/n) 1^T A t = (|S|/n) d 1^T t = 0`（A 對稱，`1^T A = d 1^T`，而 `t ⟂ 1`）。
- `s^T A 1 (|T|/n) = d (s^T 1)(|T|/n) = 0`，同理。
- `s^T A t`：剩下的誤差項。

因為 A 把 `1^⟂`（與 1 正交的子空間）送回自己（Ch 27 譜定理證明的第 3 步），而且在 `1^⟂` 上 A 的特徵值是 `λ_2, ..., λ_n`，絕對值都 ≤ λ，所以 `‖A t‖ ≤ λ ‖t‖`。由 Cauchy–Schwarz，`|s^T A t| ≤ ‖s‖ ‖A t‖ ≤ λ ‖s‖ ‖t‖`。最後 `‖s‖^2 = ‖1_S‖^2 − (|S|/n)^2 n = |S|(1 − |S|/n) ≤ |S|`，`‖t‖^2 ≤ |T|` 同理。∎

證明裡其實得到了更緊的 `λ √(|S||T|(1 − |S|/n)(1 − |T|/n))`。

**推論（獨立集上界）**：若 S 是獨立集，取 T = S，`e(S, S) = 0`，得 `d|S|²/n ≤ λ|S|`，所以 `α(G) ≤ λn/d`。好的 expander 沒有大的獨立集，因此色數 `χ ≥ n/α ≥ d/λ`——這是 Ch 27 Hoffman 界的近親。

### 實測：隨機集合不夠「壞」，要用特徵向量挑

用隨機的 S、T 測 mixing lemma 會讓人誤以為誤差很小——隨機集合本來就近似隨機。要逼近界，得挑「最不隨機」的集合：取 λ_2 的特徵向量，S = 它為正的那些點。

```python
import random

import networkx as nx
import numpy as np


def paley(q):
    squares = {(x * x) % q for x in range(1, q)}
    return nx.Graph([(x, y) for x in range(q) for y in range(x + 1, q) if (x - y) % q in squares])


def e(A, S, T):
    """有序對 (s, t)，s∈S、t∈T 且相鄰的個數；S∩T 內的邊算兩次。"""
    return int(A[np.ix_(S, T)].sum())


def check_eml(G, name, trials=2000, seed=0):
    nodes = sorted(G)
    n = len(nodes)
    A = nx.to_numpy_array(G, nodelist=nodes)
    d = int(A[0].sum())
    ev, vec = np.linalg.eigh(A)
    lam = max(abs(ev[-2]), abs(ev[0]))

    def ratio(S, T):
        return abs(e(A, S, T) - d * len(S) * len(T) / n) / np.sqrt(len(S) * len(T))

    rng = random.Random(seed)
    worst_random = max(ratio(rng.sample(range(n), rng.randint(1, n - 1)),
                             rng.sample(range(n), rng.randint(1, n - 1))) for _ in range(trials))
    # 用第二特徵向量挑「最不隨機」的集合：S = T = {v : x_v > 0}
    S = [v for v in range(n) if vec[v, -2] > 0]
    expected, actual = d * len(S) ** 2 / n, e(A, S, S)
    r_eig = ratio(S, S)
    assert worst_random <= lam + 1e-9 and r_eig <= lam + 1e-9
    print(f"{name:17s} d={d:3d} lambda={lam:6.2f} | random: {worst_random:5.2f} | eigvec S=T: {r_eig:5.2f}"
          f"  e(S,S)={actual:6d} vs d|S|^2/n={expected:8.1f} ({(actual - expected) / expected:+.1%})")


check_eml(nx.cycle_graph(400), "cycle C400")
check_eml(nx.random_regular_graph(4, 400, seed=3), "rand 4-reg n=400")
check_eml(nx.random_regular_graph(40, 400, seed=3), "rand 40-reg n=400")
check_eml(paley(401), "Paley(401)")
```

輸出（本機約 3 秒）：

```text
cycle C400        d=  2 lambda=  2.00 | random:  0.18 | eigvec S=T:  0.99  e(S,S)=   396 vs d|S|^2/n=   198.0 (+100.0%)
rand 4-reg n=400  d=  4 lambda=  3.42 | random:  0.36 | eigvec S=T:  1.25  e(S,S)=   656 vs d|S|^2/n=   404.0 (+62.4%)
rand 40-reg n=400 d= 40 lambda= 11.71 | random:  0.68 | eigvec S=T:  4.04  e(S,S)=  4590 vs d|S|^2/n=  3802.5 (+20.7%)
Paley(401)        d=200 lambda= 10.51 | random:  1.55 | eigvec S=T:  1.78  e(S,S)= 24310 vs d|S|^2/n= 23920.7 (+1.6%)
```

最後一欄是重點：mixing lemma 保證的是**相對誤差** `≤ λ√(|S||T|) / (d|S||T|/n)`，對 `|S| = |T| = n/2` 就是 `2λ/d`。

- 圈：`2λ/d = 2`，界毫無資訊，而真的有一個集合（半圈）內部邊數是隨機預期的兩倍。
- 4-正則：`2λ/d ≈ 1.7`，界仍然沒用；特徵向量挑的集合偏差 +62%。
- 40-正則：`2λ/d ≈ 0.59`，最壞偏差 +21%。
- Paley(401)：`2λ/d ≈ 0.105`，最壞偏差只有 +1.6%——**λ/d 越小，圖越像隨機圖**。

這說明了 expander 的真正參數是 `λ/d`（正規化後的第二特徵值），而不是 λ 本身。

## 最好能多好：Alon–Boppana 與 Ramanujan 圖

λ 能不能任意小？不能。

**Alon–Boppana 界**（本章只給證明架構）：任何一族 d-正則圖，`λ_2 ≥ 2√(d−1) − o(1)`（n → ∞）。

**架構**：無窮 d-正則樹的「譜半徑」是 `2√(d−1)`——在樹上從一點出發走 2k 步回到原點的 walk 數，大約是 `(2√(d−1))^{2k}`。任何 d-正則圖的 universal cover 都是這棵樹，所以圖上的閉 walk 至少和樹上一樣多；而 `trace(A^{2k}) = Σ λ_i^{2k}` 由 Ch 27 定理 27.1 等於閉 walk 總數。扣掉 `λ_1 = d` 的貢獻後，剩下的特徵值必須夠大才湊得出這麼多閉 walk。完整版要小心挑 k 與一個測試向量，見 HLW 綜述中講譜極值問題的章節。

**Ramanujan 圖**：d-正則（d ≥ 3）且 `λ(G) ≤ 2√(d−1)` 的圖。由 Alon–Boppana，這是漸近最優的譜擴張。

| 結果 | 年份 | 內容 | 本章狀態 |
| --- | --- | --- | --- |
| Lubotzky–Phillips–Sarnak；Margulis | 1988 | d = p+1（p 為質數）時，明確構造無窮多個 Ramanujan 圖；用到數論中 Ramanujan 猜想（Deligne 定理），名字由此而來 | 只陳述 |
| Friedman | 2008 | 固定 d ≥ 3，隨機 d-正則圖以趨近 1 的機率滿足 `λ ≤ 2√(d−1) + ε` | 只陳述，下面實測 |
| Marcus–Spielman–Srivastava | 2015 | 對**所有** d ≥ 3，存在無窮多個 d-正則**二分** Ramanujan 圖（interlacing families 方法） | 只陳述 |
| Huang–McKenzie–Yau | 2024 預印本 | 隨機 d-正則圖的 λ_2 漲落服從 Tracy–Widom 分佈；推論約 69% 的隨機 d-正則圖是 Ramanujan | 只陳述；截至本課撰寫時為 arXiv 預印本 |

實測 Friedman 定理：n = 2000，每個 d 跑 5 個 seed：

```python
import networkx as nx
import numpy as np

n = 2000
print(f"{'d':>3s} {'2sqrt(d-1)':>10s} {'lambda (5 seeds)':>38s} {'#Ramanujan':>10s} {'gap d-lam2':>10s}")
for d in (3, 4, 6, 10, 20):
    lams, gaps = [], []
    for seed in range(5):
        G = nx.random_regular_graph(d, n, seed=seed)
        ev = np.sort(np.linalg.eigvalsh(nx.to_numpy_array(G)))
        lams.append(max(abs(ev[-2]), abs(ev[0])))
        gaps.append(d - ev[-2])
    rb = 2 * np.sqrt(d - 1)
    print(f"{d:3d} {rb:10.4f} {' '.join(f'{x:7.4f}' for x in lams):>38s} "
          f"{sum(x <= rb for x in lams):>8d}/5 {np.mean(gaps):10.3f}")
```

輸出（本機約 14 秒，主要花在 25 次 2000×2000 稠密特徵值分解）：

```text
  d 2sqrt(d-1)                       lambda (5 seeds) #Ramanujan gap d-lam2
  3     2.8284  2.8285  2.8267  2.8315  2.8283  2.8247        3/5      0.173
  4     3.4641  3.4593  3.4631  3.4562  3.4632  3.4555        5/5      0.545
  6     4.4721  4.4560  4.4455  4.4527  4.4738  4.4719        4/5      1.545
 10     6.0000  5.9785  5.9578  5.9770  6.0226  5.9717        4/5      4.039
 20     8.7178  8.7055  8.6423  8.6951  8.6544  8.6489        5/5     11.344
```

λ 落在 `2√(d−1)` 上下千分之幾的範圍內，有時略超過（d = 3 的 seed 2：2.8315 > 2.8284）——正是 Friedman 定理的 `+ ε` 與 Huang–McKenzie–Yau 說的「不是 100% Ramanujan」。25 次裡 21 次 Ramanujan，這個比例是 n = 2000 的有限樣本，不要拿來和 69% 直接比較。

**這說明了一件反直覺的事**：隨機構造幾乎是最好的，明確構造反而難。這是去隨機化的核心張力——我們知道好東西大量存在，卻很難寫出一個。

## 應用一：用更少的隨機位元重複實驗

設一個隨機演算法（例如質數測試）每次用 r 個隨機位元，失敗機率 ≤ 1/2。重複 t+1 次取多數或取「任一次成功」，失敗機率降到 `2^{−(t+1)}`，但要 `(t+1) r` 個隨機位元。

**用 expander 省位元**：把 `{0,1}^r` 的 `n = 2^r` 個種子當成一張 (n, d, λ)-圖的點。只抽**一個**均勻起點（r 位元），然後隨機遊走 t 步（每步 `log₂ d` 位元），把走過的 t+1 個點當成 t+1 個種子。總共 `r + t log₂ d` 位元。

**定理 30.2（expander 上的遊走很少一直待在小集合裡）**：令 B ⊆ V，`|B| = βn`，`α = λ/d`。從均勻起點走 t 步，t+1 個點**全部**落在 B 的機率 ≤ `β (β + α)^t`。

**證明**：令 `P = A/d`，`Π_B` 為「只保留 B 上的座標」的對角投影。起點均勻落在 B 的質量是 `1_B / n`；每走一步乘一次 P，再用 `Π_B` 丟掉落在 B 外的質量。所以所求機率是 `(1/n) 1_B^T (Π_B P Π_B)^t 1_B`（P 對稱，左乘右乘一樣）。只要證 `‖Π_B P Π_B‖ ≤ β + α`（運算子 2-範數），則機率 ≤ `(1/n) ‖1_B‖^2 (β + α)^t = β (β + α)^t`。

取任意支撐在 B 上的向量 x，分解 `x = x̄ + x⊥`，`x̄ = (⟨x, 1⟩/n) 1`，`x⊥ ⟂ 1`。

- `P x̄ = x̄`。`‖Π_B x̄‖ = (|⟨x, 1⟩|/n) √(βn)`。因為 x 支撐在 B 上，`⟨x, 1⟩ = ⟨x, 1_B⟩ ≤ ‖x‖ √(βn)`（Cauchy–Schwarz），所以 `‖Π_B x̄‖ ≤ β ‖x‖`。
- `‖Π_B P x⊥‖ ≤ ‖P x⊥‖ ≤ α ‖x⊥‖ ≤ α ‖x‖`（P 在 `1^⟂` 上的範數是 λ/d）。

三角不等式：`‖Π_B P x‖ ≤ (β + α) ‖x‖`。∎

把 B 設為「讓演算法失敗的種子」（β ≤ 1/2），只要 `β + α < 1`，失敗機率就隨 t 指數下降——和獨立取樣同樣是指數，只是底數從 β 變成 β + α。實測：

```python
import math
import random

import networkx as nx
import numpy as np

n, d = 1024, 32
G = nx.random_regular_graph(d, n, seed=7)
A = nx.to_numpy_array(G, nodelist=range(n))
ev, vec = np.linalg.eigh(A)
alpha = max(abs(ev[-2]), abs(ev[0])) / d          # 正規化後的 lambda
P = A / d


def stay_prob(B, t):
    """精確計算：均勻起點、走 t 步，t+1 個點全落在 B 的機率。"""
    mask = np.zeros(n)
    mask[B] = 1
    x = mask / n                                   # 起點在 B
    for _ in range(t):
        x = (x @ P) * mask                         # 走一步，再只保留落在 B 的質量
    return x.sum()


rng = random.Random(1)
B_rand = rng.sample(range(n), n // 2)
B_eig = [v for v in range(n) if vec[v, -2] > 0][: n // 2]   # 用特徵向量挑「黏」的集合
beta = 0.5
print(f"d={d}, lambda/d = {alpha:.3f}, beta = {beta}")
print(f"{'t':>3s} {'indep beta^(t+1)':>17s} {'walk, random B':>15s} {'walk, eigvec B':>15s}"
      f" {'beta*(beta+a)^t':>16s} {'bits indep':>10s} {'bits walk':>9s}")
for t in (1, 2, 5, 10, 20):
    pr, pe = stay_prob(B_rand, t), stay_prob(B_eig, t)
    bound = beta * (beta + alpha) ** t
    assert pr <= bound + 1e-12 and pe <= bound + 1e-12
    print(f"{t:3d} {beta ** (t + 1):17.2e} {pr:15.2e} {pe:15.2e} {bound:16.2e}"
          f" {(t + 1) * math.log2(n):10.0f} {math.log2(n) + t * math.log2(d):9.0f}")
```

輸出：

```text
d=32, lambda/d = 0.340, beta = 0.5
  t  indep beta^(t+1)  walk, random B  walk, eigvec B  beta*(beta+a)^t bits indep bits walk
  1          2.50e-01        2.52e-01        3.02e-01         4.20e-01         20        15
  2          1.25e-01        1.30e-01        1.86e-01         3.53e-01         30        20
  5          1.56e-02        1.82e-02        4.36e-02         2.09e-01         60        35
 10          4.88e-04        6.88e-04        3.89e-03         8.74e-02        110        60
 20          4.77e-07        9.81e-07        3.10e-05         1.53e-02        210       110
```

讀法：

- 對隨機的 B，walk 幾乎和獨立取樣一樣好（`9.8e-7` vs `4.8e-7`），位元數卻只要一半多一點（110 vs 210）。
- 對「黏」的 B（特徵向量挑的、內部邊特別多的集合），walk 比獨立取樣差很多，但仍然指數下降，而且全程在定理 30.2 的界之內。
- 這裡 `r = log₂ 1024 = 10`、`log₂ d = 5`，省得不多；實務上 r 可能是幾百位元而 d 是常數，節省就是 `t·r` 對 `r + O(t)` 的量級差。

這個想法出自 Ajtai–Komlós–Szemerédi（1987），Impagliazzo–Zuckerman 與 Cohen–Wigderson（1989）各自把它用在放大成功機率上。**它不是一個「讓程式變快」的工程技巧**——它省的是隨機位元這種計算複雜度理論裡的資源。

**同一條路的頂點**：Reingold（2005 年 STOC，2008 年 JACM）證明無向圖的 s–t 連通性可以在 `O(log n)` 空間內**確定性**地判定（SL = L）。核心是用 zig-zag 乘積把輸入圖逐步「變成」一張常數度數的 expander，而 expander 的直徑是 `O(log n)`，可以列舉所有短路徑。這裡只陳述，不展開。

## 應用二：錯誤更正碼

LDPC（low-density parity-check）碼可以畫成一張二分圖（**Tanner 圖**）：左邊 N 個位元、右邊 M 個奇偶檢查，每個檢查連到少數幾個位元。合法碼字是「所有檢查都滿足」的位元串。

Sipser–Spielman（1996）證明：若這張二分圖是好的**頂點 expander**——左邊任意不太大的集合 S，它的鄰居集合大小至少是 `(3/4) d |S|`——那麼：

1. 碼的最小距離與 N 成正比（常數比例的錯誤可以被更正）；
2. 一個極簡單的**翻轉解碼**就能在線性時間內更正它們：反覆找一個「相連的不滿足檢查比滿足的多」的位元，把它翻過來。

為什麼擴張性讓翻轉解碼有效？（這裡只給證明架構）錯誤位元集合 S 夠小時，擴張性保證 S 有很多**唯一鄰居**（只連到 S 中一個位元的檢查），這些檢查一定不滿足，所以必定存在一個錯誤位元「不滿足的檢查佔多數」；每次翻轉嚴格減少不滿足的檢查數，所以總步數是線性的。完整證明在 Spielman 書稿 Chapter 29「Expander Codes」。

工程上的對應：Wi-Fi、5G、DVB-S2 的 LDPC 碼並不直接用明確的 expander 構造，而是用隨機或代數方法設計、再配合 belief propagation 解碼；expander 理論提供的是「為什麼這類稀疏碼能好」的保證。

## 應用三：網路拓撲

資料中心的交換器網路，傳統上用 fat-tree（Clos 網路）這類結構化拓撲。Expander 的論點是：給定每台交換器的埠數 d，expander 讓任意兩群機器之間都有充足的頻寬（mixing lemma），而且沒有單一瓶頸（Cheeger）。

- **Jellyfish**（Singla 等，NSDI 2012）：把 ToR 交換器之間接成一張**隨機正則圖**。論文報告在相同設備下，它能以全容量支援比 fat-tree 更多的伺服器，而且優勢隨規模成長；也更容易增量擴充（加一台交換器，只要拆掉幾條隨機邊重接）。代價是路由與布線變複雜。
- **Xpander**（Valadarsky、Shahaf、Dinitz、Schapira，CoNEXT 2016）：改用**確定性**的 expander 構造（2-lift 一類的方法），保留 expander 的效能，又讓布線有規律可循。

這兩篇的效能數字都來自論文作者的模擬與測試床，**本章沒有實測**；也有後續研究指出 expander 拓撲在某些流量模式下的吞吐量限制。分散式系統裡的 gossip 協定與 P2P overlay 也依賴類似的性質：隨機選鄰居形成的 overlay 是 expander，訊息在 `O(log n)` 輪內傳遍全網。

> 分散式系統的 overlay 與 gossip 在 [systems/distributed_systems](../../systems/distributed_systems/README.md) 課程。

## 應用四：密碼雜湊（以及它怎麼壞掉）

Charles–Goren–Lauter（Journal of Cryptology，2009）提出用 Ramanujan 圖上的隨機遊走做雜湊：把訊息位元當成「每一步走哪條邊」的指令，終點就是雜湊值。Ramanujan 性質保證輸出分佈接近均勻；抗碰撞性則歸結到圖背後的數學難題——用 LPS 圖時是群論上的一個表示問題，用 Pizer 圖（超奇異橢圓曲線的同源圖）時是計算同源的困難度。

這是一個「expander 給你均勻性、但不給你安全性」的好例子：LPS 版本後來被找到有效的碰撞攻擊（Tillich–Zémor，2008 年左右），因為 LPS 圖的代數結構太規則；同源圖這條路則演變成後量子密碼的一支（同源密碼）。

> 雜湊函數的安全定義與後量子密碼在 [security/cryptography](../../security/cryptography/README.md) 課程。

## 對比與取捨

| 構造 | 度數 | λ 品質 | 優點 | 缺點 |
| --- | --- | --- | --- | --- |
| 隨機 d-正則圖 | 任意常數 | 幾乎 Ramanujan（Friedman） | 實作最簡單；`nx.random_regular_graph` | 不是明確構造：要存整張圖、無法「算出第 i 個點的鄰居」 |
| LPS / Margulis Ramanujan 圖 | p+1 | Ramanujan | 明確、可局部計算鄰居 | 只有特定度數；需要數論 |
| Zig-zag 乘積（Reingold–Vadhan–Wigderson） | 常數 | 不到 Ramanujan | 組合式、可遞迴構造；SL = L 的關鍵 | 常數不漂亮 |
| Paley 圖 | `(q−1)/2` | 非常好（`≈ √q / 2`） | 一行定義 | 稠密，不是常數度數 |
| 超立方體 | `log₂ n` | 單邊 gap 2 | 結構清楚、路由簡單 | 二分；度數隨 n 成長 |

**為什麼不直接用完全圖？** 完全圖的 `λ/d = 1/(n−1)` 是完美的，但成本是 `Θ(n²)` 條邊。Expander 的意義是用 `Θ(n)` 條邊拿到「常數品質」的偽隨機性。

## 踩雷集錦

1. **以為單一一張圖「是 expander」**：expander 是圖族的漸近性質。對有限圖，請說它的 λ 或 h 是多少。
2. **把 d = 2 的 Ramanujan 判斷當真**：`2√(d−1) = d` 時界是平凡的；圈是最差的 expander。
3. **二分圖的兩側 λ**：二分圖 `λ_n = −d`，兩側定義下 `λ = d`；如果你的應用（例如 Tanner 圖）天生是二分的，要用單邊 gap 或專門的二分版定義（MSS 證明的就是二分 Ramanujan 圖）。
4. **用隨機集合測 mixing lemma**：隨機的 S、T 本來就近似隨機，看起來誤差很小，什麼都沒驗證到。要用特徵向量或暴力搜尋挑對抗性集合。
5. **`nx.random_regular_graph` 的邊界**：`n * d` 必須是偶數，否則丟出 `NetworkXError: n * d must be even`；d = 2 時圖常常不連通（我們測 n = 100 的 100 個 seed，只有 16 個連通），d ≥ 3 時在測試的 100 個 seed 全部連通——但這是機率性質，用之前仍要 `assert nx.is_connected(G)`。

## 進階：再往深一層

**頂點擴張與 unique-neighbor 擴張**：錯誤更正碼需要的是「|N(S)| ≥ (1−ε) d |S|」這種接近最大可能的頂點擴張，譜方法最多只能證到 `d/2` 左右的擴張率。Capalbo–Reingold–Vadhan–Wigderson 的 lossless expander 構造突破了這個限制，見 HLW 綜述的相關章節。

**2-lift 與 interlacing families**：Bilu–Linial（2006）提出：把一張圖的每個點複製成兩份、每條邊選擇「直接接」或「交叉接」（2-lift），新圖的特徵值是原圖特徵值加上一個「帶號鄰接矩陣」的特徵值；他們猜測總存在一種選法讓帶號矩陣的特徵值 ≤ `2√(d−1)`。Marcus–Spielman–Srivastava 證明了二分圖的版本，從而得到所有度數的二分 Ramanujan 圖。Spielman 書稿 Chapter 44「Bipartite Ramanujan Graphs」是最容易入門的講法。

**高維 expander 與局部可測碼**：近年把 expander 的概念推廣到單純複形（high-dimensional expanders），已被用來構造具常數速率、常數距離、常數查詢的局部可測碼——這是 2020 年代理論計算機科學的熱點之一。

## 動手練習

1. 用 mixing lemma 的推論 `α(G) ≤ λn/d`，對 Paley(101) 算出獨立集上界，再用 `nx.max_weight_clique(nx.complement(G), weight=None)` 求真正的最大獨立集，比較兩者。
2. 實作 Bilu–Linial 的隨機 2-lift：從 `K_4` 出發反覆 lift 6 次（每次隨機選交叉與否，seed 固定），畫出 λ 隨點數變化的曲線，和 `2√2` 比較。
3. 修改定理 30.2 的實驗，把 d 改成 4（`λ/d ≈ 0.87`），看 `β + α > 1` 時界變得沒用，但實際機率是否仍然指數下降。
4. 證明 mixing lemma 的更緊版本 `λ √(|S||T|(1 − |S|/n)(1 − |T|/n))`，並用它重新推導 Ch 28 Cheeger 不等式的容易方向（提示：T = S̄）。

## 本章重點整理

- Expander：度數固定、λ/d 有界小於 1 的圖族；邊擴張與譜擴張由 Cheeger 不等式互換。
- Expander mixing lemma：`|e(S,T) − d|S||T|/n| ≤ λ√(|S||T|)`；證明 = 把指示向量拆成平均部分與正交部分。真正的參數是 λ/d。
- Alon–Boppana：λ_2 ≥ 2√(d−1) − o(1)；達到它的是 Ramanujan 圖。隨機正則圖幾乎都是（Friedman），實測貼著界。
- Expander 上的遊走：t 步全落在密度 β 的集合裡的機率 ≤ `β(β + λ/d)^t`；用 `r + O(t)` 位元得到接近 `t` 次獨立取樣的效果。
- 應用：線性時間解碼的 expander codes、去隨機化與 SL = L、資料中心拓撲、雜湊；每一個都是「偽隨機性」的不同用法。

## 自我檢核

- [ ] 不看筆記，寫出 mixing lemma 證明中四項展開，並說出哪兩項為什麼是 0。
- [ ] 同事說「我測了 1000 組隨機子集，偏差都很小，所以這張圖是好 expander」——你會怎麼反駁？
- [ ] 能解釋為什麼 Paley(401) 的最壞偏差只有 1.6%，而 4-正則隨機圖有 62%，用 λ/d 一句話說清楚。
- [ ] 能說出用 expander walk 省隨機位元時，省下的是什麼、沒省下的是什麼。
- [ ] 如果你要設計一個 1000 台交換器、每台 32 埠的網路，你會考慮 Jellyfish 嗎？列出兩個支持與兩個反對的理由。

## 延伸閱讀

### 綜述與書籍

- **[Expander graphs and their applications](https://www.doi.org/10.1090/S0273-0979-06-01126-8)** — Shlomo Hoory、Nathan Linial、Avi Wigderson，Bulletin of the AMS 43(4)（2006），439–561
  - **核心貢獻**：expander 的標準綜述，從定義、構造（代數、zig-zag、lift）到應用（碼、去隨機化、度量嵌入）一應俱全。
  - **讀哪裡**：開頭講動機的第一節（三個應用的故事）、講擴張與特徵值（含 mixing lemma）的章節、講 expander 上隨機遊走的章節（本章定理 30.2 的脈絡），以及講譜的極值問題（Alon–Boppana）的章節；後面的度量嵌入與群論構造可以先跳過。
  - **前提知識**：Ch 27–29。
- **[Spectral and Algebraic Graph Theory](https://www.cs.yale.edu/homes/spielman/sagt/)** — Daniel A. Spielman（Yale，書稿 2025-04-02 版）
  - **讀哪幾章**：Part V「Expander Graphs」：Chapter 27「Properties of Expander Graphs」（mixing lemma 與偽隨機性）、Chapter 29「Expander Codes」（Sipser–Spielman 解碼的完整證明）、Chapter 30「A simple construction of expander graphs」、Chapter 31「PSRGs via Random Walks on Graphs」（本章應用一的完整版）；Chapter 43–44（Ramanujan 圖與 interlacing families）。
  - **前提知識**：Ch 27–29；Part VII 需要多項式根的一些分析。

### 論文

- **[Eigenvalues and expanders](https://doi.org/10.1007/BF02579166)** — Noga Alon，Combinatorica 6(2)（1986），83–96
  - **核心貢獻**：證明擴張性與 spectral gap 的雙向關係，並記錄了 Alon–Boppana 界。
  - **讀哪裡**：開頭的主定理陳述與擴張性 ⇔ 特徵值的論證；後面的構造細節可略。
  - **和本章的關聯**：「兩種定義」一節的理論基礎。
- **[A proof of Alon's second eigenvalue conjecture and related problems](https://www.arxiv.org/abs/cs/0405020)** — Joel Friedman，Memoirs of the AMS 195, no. 910（2008）
  - **核心貢獻**：證明隨機 d-正則圖幾乎都是「幾乎 Ramanujan」。
  - **讀哪裡**：只讀摘要與第 1 章的陳述；證明超過 100 頁，是重型的 trace method。
  - **和本章的關聯**：本章 n = 2000 實驗所驗證的定理。
- **[Interlacing families I: Bipartite Ramanujan graphs of all degrees](https://annals.math.princeton.edu/2015/182-1/p07)** — Adam Marcus、Daniel A. Spielman、Nikhil Srivastava，Annals of Mathematics 182(1)（2015），307–325
  - **核心貢獻**：用 interlacing families 證明 Bilu–Linial 猜想的二分版，得到所有度數的二分 Ramanujan 圖。
  - **讀哪裡**：引言的問題陳述與證明策略概覽；同一套 interlacing families 方法後來也用在 Kadison–Singer 問題上。
  - **和本章的關聯**：Ramanujan 表格第三列。
- **[Expander codes](https://doi.org/10.1109/18.556667)** — Michael Sipser、Daniel A. Spielman，IEEE Transactions on Information Theory 42(6)（1996），1710–1722
  - **核心貢獻**：以頂點 expander 構造的 LDPC 碼，配合線性時間的翻轉解碼。
  - **讀哪裡**：碼的構造與翻轉解碼（sequential/parallel decoding）的分析部分；資訊理論背景不熟的話，先讀 Spielman 書稿 Chapter 28「A brief introduction to Coding Theory」。
  - **和本章的關聯**：應用二。
- **[Undirected connectivity in log-space](https://doi.org/10.1145/1391289.1391291)** — Omer Reingold，Journal of the ACM 55(4)（2008）
  - **核心貢獻**：SL = L；用 zig-zag 乘積把任意圖轉成 expander，在對數空間內確定性判斷連通性。
  - **讀哪裡**：引言的概覽與主演算法的描述；需要先熟悉 zig-zag 乘積（Reingold–Vadhan–Wigderson）。
  - **和本章的關聯**：應用一的最終形態。
- **[Jellyfish: Networking Data Centers Randomly](https://www.usenix.org/conference/nsdi12/technical-sessions/presentation/singla)** — Ankit Singla、Chi-Yao Hong、Lucian Popa、P. Brighten Godfrey，NSDI 2012
  - **核心貢獻**：以隨機正則圖連接 ToR 交換器的資料中心拓撲，並處理路由與布線問題。
  - **讀哪裡**：與 fat-tree 的容量比較，以及處理路由與布線的段落；理論背景就是本章的隨機正則圖。USENIX 頁面有免費 PDF 與投影片。
  - **和本章的關聯**：應用三；同主題的確定性版本是 Valadarsky 等人的〈Xpander: Towards Optimal-Performance Datacenters〉（CoNEXT 2016）。
- **[Cryptographic Hash Functions from Expander Graphs](https://eprint.iacr.org/2006/021)** — Denis X. Charles、Eyal Z. Goren、Kristin E. Lauter，Journal of Cryptology（2009）；連結是 IACR ePrint 2006/021 版本
  - **核心貢獻**：以 LPS 與 Pizer Ramanujan 圖上的遊走構造可證明抗碰撞的雜湊。
  - **讀哪裡**：雜湊構造與安全性歸約的部分；LPS 版本後來的碰撞攻擊要另外查 Tillich–Zémor 的論文。
  - **和本章的關聯**：應用四。

Expander 告訴我們：隨機構造幾乎總是最好的，而明確構造很難。下一章把這句話變成一個證明技巧——要證明某個好東西存在，就證明「隨機挑一個，它是好東西的機率大於 0」。

→ [Ch 31 機率方法](./31-probabilistic-method.md)
