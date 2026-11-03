# Ch 18 — 平面圖：Euler 公式與 Kuratowski

> **目標**：完整證明 Euler 公式 `V − E + F = 2` 與推論 `E ≤ 3V − 6`，用它證明 K5、K3,3 不是平面圖；理解 Kuratowski / Wagner 定理在說什麼（證明只給架構）；自刻「窮舉 rotation system」的平面性判定，在 6 點以內的所有連通圖上和 `nx.check_planarity` 對拍，並親手從 NetworkX 的反例裡認出 K5 或 K3,3。
>
> **環境**：Python 3.13.12、NetworkX 3.7、SciPy 1.18.1，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

「三間房子各自要接水、電、瓦斯三條管線，管線不能交叉，做得到嗎？」這個 1910 年代就在流傳的謎題，問的正是 K3,3 能不能畫在平面上不交叉。答案是不能，但要**證明**不能，需要一個不依賴「我試了很多種畫法都失敗」的論證。

平面性在工程上一點都不抽象：

- **電路佈線**：單層 PCB 或晶片的一層金屬上，導線不能交叉；一張電路圖能不能單層佈完，就是平面性判定。
- **地圖與著色**：地圖上相鄰國家連邊得到的圖一定是平面圖，「地圖要幾色」是 Ch 19 的四色定理。
- **演算法加速**：平面圖有 `O(√n)` 大小的分隔集（Lipton–Tarjan），很多 NP-hard 問題在平面圖上有次指數或近似演算法；平面圖最短路徑、最大流也有專用的快速演算法。
- **圖的繪製**：控制流程圖、相依圖要畫得好讀，第一步就是判斷能不能無交叉地畫。

這一章的主線是：**一個拓撲事實（Euler 公式）推出一個計數不等式（`E ≤ 3V − 6`），計數不等式推出具體的不可能性（K5、K3,3）**。最後 Kuratowski 說：這兩個就是全部的障礙。

## 先建立直覺

把圖畫在平面上、邊不交叉，得到一個**平面嵌入**（plane embedding）；能這樣畫的圖叫**平面圖**（planar graph）。邊把平面切成好幾塊區域，每塊叫一個**面**（face），最外面那塊無界的也算一個面。

```text
   立方體的平面畫法（V=8, E=12）

     a ─────────── b
     │ ╲         ╱ │
     │  e ───── f  │       面：中間的 efgh、四個梯形、外面那一塊
     │  │       │  │       F = 1 + 4 + 1 = 6
     │  h ───── g  │       V − E + F = 8 − 12 + 6 = 2
     │ ╱         ╲ │
     d ─────────── c
```

換一種畫法，面的形狀會變，但 `V − E + F` 永遠是 2。直覺上：加一條連到新點的邊（V+1、E+1），面數不變；在已有的兩點之間加一條邊（E+1），它會把一個面切成兩個（F+1）。兩種操作都讓 `V − E + F` 不變，而起點（一個孤立點）是 `1 − 0 + 1 = 2`。

> 圖的連通、樹、圈的基本性質在 [Ch 7 — 圖的定義、表示與同構](./07-graph-definitions.md) 與 [Ch 9 — 樹](./09-trees-cayley-prufer.md)。

## Euler 公式

**定理（Euler 公式）**：G 是連通的平面圖，任取一個平面嵌入，`V − E + F = 2`。

**證明**：對邊數 E 歸納。這個證明借用一個拓撲事實：**Jordan 曲線定理**——平面上一條不自交的閉曲線把平面分成恰好兩個區域（內與外）。它的證明屬於拓撲學，本課不證，直接使用。

- **G 是樹**：`E = V − 1`（Ch 9）。樹沒有圈，所以嵌入中沒有閉曲線把平面切開，只有一個面，`F = 1`。`V − (V − 1) + 1 = 2`。
- **G 不是樹**：G 有一個圈 C，取 C 上一條邊 e。C 在嵌入中是一條閉曲線，由 Jordan 曲線定理，e 的兩側分屬 C 的內部與外部，也就是 e 恰好是兩個**不同**的面的邊界。拿掉 e：G − e 仍連通（e 在圈上，不是橋），而 e 兩側的兩個面合併成一個，其他面不受影響。所以 G − e 有 `E − 1` 條邊、`F − 1` 個面、V 個點。由歸納假設 `V − (E − 1) + (F − 1) = 2`，整理得 `V − E + F = 2`。∎

不連通的情況：有 c 個連通分量時，`V − E + F = 1 + c`（每多一個分量，外面那個面被共用，少算一次）。

### 推論：平面圖很稀疏

**定義**：一個面的**長度**是沿著它的邊界走一圈經過的邊數，一條邊如果兩側是同一個面（例如樹的邊），算兩次。所以 `Σ 面長 = 2E`——每條邊恰好貢獻兩次（兩側各一次），這和握手定理 `Σ deg = 2E` 是同一種雙重計數（Ch 3）。

**推論 1**：G 是簡單平面圖、`V ≥ 3`，則 `E ≤ 3V − 6`。

**證明**：先假設 G 連通。簡單圖沒有自環、沒有重邊，`V ≥ 3` 時每個面的長度 `≥ 3`（長度 1 要自環、長度 2 要重邊或「只有一條邊的圖」，後者 `V = 2` 被排除）。所以 `3F ≤ Σ 面長 = 2E`，`F ≤ 2E/3`。代入 Euler 公式：`2 = V − E + F ≤ V − E + 2E/3`，整理得 `E ≤ 3V − 6`。不連通時，在分量之間加邊連起來不破壞平面性、只讓 E 變大，連起來的圖滿足不等式，原圖也滿足。∎

**推論 2**：G 是簡單平面圖、`V ≥ 3`、沒有三角形，則 `E ≤ 2V − 4`。（同樣論證，面長 `≥ 4`：`4F ≤ 2E`。）

**推論 3**：每個簡單平面圖都有一個度數 `≤ 5` 的點。（若全部 `≥ 6`，`2E = Σ deg ≥ 6V`，`E ≥ 3V > 3V − 6`，矛盾。）

推論 3 是 Ch 19 五色定理的起點：歸納時永遠拔得到一個度數 `≤ 5` 的點。它也讓 Ch 15 的 smallest-last 貪婪法在平面圖上保證 `≤ 6` 色。

### K5 與 K3,3 不是平面圖

- **K5**：`V = 5`、`E = 10`，但 `3V − 6 = 9`。違反推論 1。
- **K3,3**：二分圖沒有奇圈，所以沒有三角形；`V = 6`、`E = 9`，但 `2V − 4 = 8`。違反推論 2。

K3,3 剛好落在推論 1 的範圍內（`9 ≤ 12`），只用 `3V − 6` 證不出來——要用「沒有三角形」換到更緊的 `2V − 4`。這是一個「選對不變量」的好例子。

注意方向：這些不等式是**必要條件**。`E ≤ 3V − 6` 成立的圖不一定是平面圖（K3,3 就是），它只能用來快速排除。

## 底層機制：把「嵌入」變成組合物件

「畫在平面上」是幾何概念，程式要怎麼處理？關鍵是 **rotation system**：對每個點，記下它的鄰居**繞著它的順時針順序**。一個嵌入完全由這組順序決定（在拓撲等價的意義下），而面可以純組合地追蹤出來：

```text
面追蹤規則：站在有向邊 (a → b) 上，到了 b 之後，
            轉向「b 的循環順序中，a 的下一個鄰居」c，走 (b → c)。
            重複直到回到起點，走過的有向邊就是一個面。

            每條無向邊對應兩條有向邊，每條有向邊恰屬一個面
            → 面數 = 有向邊被分成的軌道數
```

任何 rotation system 都對應某個可定向曲面上的嵌入（Heffter–Edmonds 原理），對連通圖滿足 `V − E + F = 2 − 2g`，g 是曲面的虧格（genus，甜甜圈 g = 1）。所以：

> **連通圖是平面圖 ⟺ 存在一個 rotation system 讓追蹤出的面數 `F = E − V + 2`。**

這給了一個**指數時間但完全自刻**的平面性判定：窮舉每個點的循環順序（度數 d 的點有 `(d − 1)!` 種），追蹤面數。拿它在 6 點以內的全部 143 張連通圖上和 NetworkX 對拍：

```python
import time
from itertools import permutations, product
import networkx as nx


def count_faces(adj, rot):
    """rot[v] = v 的鄰居循環順序。依面追蹤規則數有向邊的軌道數。"""
    nxt = {v: {r[i]: r[(i + 1) % len(r)] for i in range(len(r))} for v, r in rot.items()}
    seen, faces = set(), 0
    for u in adj:
        for v in adj[u]:
            if (u, v) in seen:
                continue
            faces += 1
            d = (u, v)
            while d not in seen:
                seen.add(d)
                a, b = d
                d = (b, nxt[b][a])
    return faces


def planar_bruteforce(adj, use_bound=True):
    """連通圖的平面性：窮舉 rotation system。回傳 (是否平面, 試了幾組)。"""
    V = len(adj)
    E = sum(len(s) for s in adj.values()) // 2
    if E == 0:
        return True, 0                                   # 單點：一個面
    if use_bound and V >= 3 and E > 3 * V - 6:
        return False, 0                                  # 推論 1 直接排除
    choices = []
    for v in adj:
        nb = sorted(adj[v])
        choices.append([[nb[0], *p] for p in permutations(nb[1:])])   # 固定第一個，避免重複循環
    tried = 0
    for combo in product(*choices):
        tried += 1
        if V - E + count_faces(adj, dict(zip(adj, combo))) == 2:
            return True, tried
    return False, tried


t = time.perf_counter()
result = {True: 0, False: 0}
for G in nx.graph_atlas_g()[1:]:
    if G.number_of_nodes() > 6 or not nx.is_connected(G):
        continue
    ok, _ = planar_bruteforce({u: set(G[u]) for u in G})
    assert ok == nx.check_planarity(G)[0]
    result[ok] += 1
print(f"6 點以內連通圖：平面 {result[True]} 張、非平面 {result[False]} 張，與 nx 全部一致")

for name, G in [("K5", nx.complete_graph(5)), ("K3,3", nx.complete_bipartite_graph(3, 3))]:
    adj = {u: set(G[u]) for u in G}
    ok, tried = planar_bruteforce(adj, use_bound=False)          # 不用推論 1，硬窮舉
    best = max(count_faces(adj, dict(zip(adj, combo))) for combo in product(
        *[[[sorted(adj[v])[0], *p] for p in permutations(sorted(adj[v])[1:])] for v in adj]))
    V, E = len(adj), G.number_of_edges()
    print(f"{name}: 窮舉 {tried} 組 rotation system，全部失敗={not ok}；"
          f"最多 {best} 個面，平面需要 E − V + 2 = {E - V + 2} 個")
```

```text
6 點以內連通圖：平面 129 張、非平面 14 張，與 nx 全部一致
K5: 窮舉 7776 組 rotation system，全部失敗=True；最多 5 個面，平面需要 E − V + 2 = 7 個
K3,3: 窮舉 64 組 rotation system，全部失敗=True；最多 3 個面，平面需要 E − V + 2 = 5 個
```

K3,3 的 64 組、K5 的 7776 組 rotation system 全部試過，沒有一組湊得出平面需要的面數。這是「不是平面圖」的**窮舉證明**，和推論 1、2 的計數證明互相印證。代價是指數時間：度數一高就爆炸。

## 用 NetworkX 對拍：嵌入、面、Kuratowski 子圖

實務上的平面性判定是線性時間的。NetworkX 的 `check_planarity` 實作 Left-Right 平面性測試（de Fraysseix–Rosenstiehl 的方法，依 Brandes 的整理實作），平面時回傳一個 `PlanarEmbedding`（就是 rotation system），不是平面時加 `counterexample=True` 可以拿到一個 **Kuratowski 子圖**作為證據。

```python
# 接續上一個 block 的定義
import numpy as np
from scipy.spatial import Delaunay


def smooth(H):
    """反覆把度數 2 的點「抹平」（兩條邊接成一條），還原出被細分的骨架。"""
    H = nx.Graph(H)
    changed = True
    while changed:
        changed = False
        for v in list(H):
            if H.degree(v) == 2:
                a, b = H[v]
                if not H.has_edge(a, b):
                    H.remove_node(v)
                    H.add_edge(a, b)
                    changed = True
                    break
    return H


def kuratowski_type(G):
    planar, K = nx.check_planarity(G, counterexample=True)
    if planar:
        return "平面"
    S = smooth(K)
    for name, T in [("K5", nx.complete_graph(5)), ("K3,3", nx.complete_bipartite_graph(3, 3))]:
        if nx.is_isomorphic(S, T):
            return f"{name} 的細分（子圖 {K.number_of_nodes()} 點 {K.number_of_edges()} 邊）"
    return "未辨識"


P = nx.petersen_graph()
for name, G in [("K5", nx.complete_graph(5)), ("K3,3", nx.complete_bipartite_graph(3, 3)),
                ("Petersen", P), ("立方體 Q3", nx.hypercube_graph(3))]:
    print(f"{name:10s} → {kuratowski_type(G)}")

# Petersen 沒有 K5 細分（3-正則圖不可能有度數 4 的分支點），但有 K5 minor：縮掉 5 條輻條
M = P.copy()
for i in range(5):
    M = nx.contracted_nodes(M, i, i + 5, self_loops=False)
print("Petersen 縮掉輻條後與 K5 同構:", nx.is_isomorphic(nx.Graph(M), nx.complete_graph(5)))

# Euler 公式對拍：隨機點的 Delaunay 三角化一定是平面圖
rng = np.random.default_rng(18)
for n in (10, 100, 1000):
    pts = rng.random((n, 2))
    T = nx.Graph()
    for a, b, c in Delaunay(pts).simplices:
        T.add_edges_from([(a, b), (b, c), (a, c)])
    planar, emb = nx.check_planarity(T)
    rot = {v: list(emb.neighbors_cw_order(v)) for v in emb}
    V, E = T.number_of_nodes(), T.number_of_edges()
    F = count_faces({u: set(T[u]) for u in T}, rot)
    print(f"Delaunay n={n:4d}: 平面={planar} V={V} E={E} F={F} V−E+F={V - E + F} "
          f"3V−6={3 * V - 6} 最小度數={min(d for _, d in T.degree())}")
```

```text
K5         → K5 的細分（子圖 5 點 10 邊）
K3,3       → K3,3 的細分（子圖 6 點 9 邊）
Petersen   → K3,3 的細分（子圖 9 點 12 邊）
立方體 Q3     → 平面
Petersen 縮掉輻條後與 K5 同構: True
Delaunay n=  10: 平面=True V=10 E=22 F=14 V−E+F=2 3V−6=24 最小度數=3
Delaunay n= 100: 平面=True V=100 E=284 F=186 V−E+F=2 3V−6=294 最小度數=2
Delaunay n=1000: 平面=True V=1000 E=2982 F=1984 V−E+F=2 3V−6=2994 最小度數=3
```

幾個值得看的地方：

- Petersen 圖不是平面圖，NetworkX 給的證據是 K3,3 的細分——它是 3-正則圖，每個點度數 3，不可能當 K5 細分裡度數 4 的分支點。但把 5 條輻條縮起來，得到的正是 K5：它有 K5 **minor**。這正是下一節兩個定理的差別。
- 立方體是平面圖（本章開頭的畫法）。
- Delaunay 三角化的 E 都略小於 `3V − 6`：差額來自凸包外圍不是三角形的那個外部面。每個內面都是三角形時，`E = 3V − 3 − h`（h 是凸包上的點數）。最小度數都 `≤ 5`，符合推論 3。

## Kuratowski 與 Wagner：障礙只有兩個

**細分**（subdivision）：在邊上插入新的度數 2 點（把一條邊換成一條路徑）。細分不改變平面性——畫法完全一樣，只是邊上多了點。所以含有 K5 或 K3,3 細分的圖一定不是平面圖。驚人的是反過來也成立：

**定理（Kuratowski 1930）**：G 是平面圖 ⟺ G 不含 K5 或 K3,3 的細分作為子圖。

**Minor**：從 G 經過刪點、刪邊、**縮邊**（把一條邊的兩端合成一點）得到的圖。縮邊也不破壞平面性（把一條邊在畫面上縮短到消失）。

**定理（Wagner 1937）**：G 是平面圖 ⟺ G 沒有 K5 或 K3,3 minor。

兩個定理等價但不完全相同：細分 ⟹ minor（把細分點縮回去），反方向不成立（Petersen 有 K5 minor 但沒有 K5 細分）；不過「有 K5 minor」⟹「有 K5 或 K3,3 的細分」，所以兩個刻畫的平面圖類相同。

**這裡只給證明架構**（必要性已經在上面證完；充分性是困難方向）。Thomassen（1981）的證明路線：

```text
1. 取一個「不含 K5、K3,3 細分卻不是平面」的最小反例 G。
2. 證明 G 一定是 3-連通的：若 G 有 1 點割或 2 點割，
   把兩邊分開各自嵌入（它們更小，由最小性是平面的），再黏回去，
   2 點割時要補一條「虛擬邊」並證明這不會產生 Kuratowski 細分。
3. 3-連通圖（點數 ≥ 5）一定有一條邊 e，縮掉後仍 3-連通（Thomassen 的引理）。
4. G/e 更小、也不含 Kuratowski 細分（需要論證：若 G/e 含，則 G 也含），
   由最小性它是平面的，取它的嵌入。
5. 在 G/e 的嵌入裡，把被縮的點「拆回」e 的兩端 x、y。
   移除縮點後，它的鄰居在一個面的邊界圈 C 上；看 x 和 y 的鄰居在 C 上怎麼交錯：
   - 不交錯 → 可以把 x、y 都放進這個面，得到 G 的嵌入，矛盾；
   - 交錯 → 分三種情況，各自直接找出 K3,3 或 K5 的細分，矛盾。
```

第 5 步的分情況就是證明的全部技術含量，大約兩三頁。完整證明見延伸閱讀 Diestel 第 4.4 節或 West 第 6.2 節。

## 對比與取捨

| 方法 | 時間 | 給你什麼 | 何時用 |
| --- | --- | --- | --- |
| 推論 1、2 的計數 | `O(1)`（知道 V、E） | 只能**排除**，不能確認 | 先跑一次濾掉明顯稠密的圖 |
| 窮舉 rotation system | `O(Π (d_v − 1)! · E)` | 嵌入或窮舉證明 | 教學、n ≤ 6 的對拍 |
| Hopcroft–Tarjan（1974） | `O(V)` | 第一個線性時間判定 | 歷史意義；實作極複雜 |
| Boyer–Myrvold（2004） | `O(V)` | 嵌入或 Kuratowski 子圖 | 很多函式庫（例如 Boost）的選擇 |
| Left-Right（`nx.check_planarity`） | `O(V)` | 嵌入或 Kuratowski 子圖 | Python 對答案、日常使用 |

為什麼窮舉法不能優化成實用版本？它把「選嵌入」和「驗證」分開，選擇空間是乘積級的。線性時間演算法的關鍵是**邊做 DFS 邊決定每條回邊放左邊還是右邊**，把嵌入的選擇壓成一個 2-SAT 式的相容性約束——這也是 Left-Right 這個名字的由來。

## 踩雷集錦

1. **「畫出來有交叉，所以不是平面圖」**：交叉可能只是畫法不好。K4 用正方形加兩條對角線畫會交叉，但把一條對角線繞到外面就沒有交叉。平面性是**存在**某種畫法，要證明不是平面圖必須用 Euler、Kuratowski 或窮舉。
2. **把 `E ≤ 3V − 6` 當充分條件**：K3,3 有 6 點 9 邊，`9 ≤ 12`，但它不是平面圖。不等式只能排除。
3. **忘了 `V ≥ 3` 與「簡單圖」的前提**：K2 有 `E = 1 > 3·2 − 6 = 0`，但它當然是平面圖——面長 `≥ 3` 的論證在 V = 2 時不成立。有重邊的多重圖可以有任意多條邊而仍是平面的。
4. **以為面數和畫法有關**：同一張連通平面圖，不同嵌入的**每個面長什麼樣**會變，但面數由 Euler 公式固定為 `E − V + 2`。3-連通平面圖更強：嵌入在本質上唯一（Whitney 定理）。
5. **混淆 subdivision 與 minor**：Petersen 圖沒有 K5 細分，有 K5 minor。若你寫的程式只搜尋 K5 細分，會「找不到 K5」，但結論「是平面」是錯的——它有 K3,3 細分。兩個定理各自都要同時檢查 K5 和 K3,3。

## 進階：再往深一層

- **Graph minor 理論**：Wagner 定理是 Robertson–Seymour 定理的起點：任何在取 minor 下封閉的圖類，都由**有限個**禁止 minor 刻畫。例如可嵌入在環面上的圖類，目前已知的禁止 minor 至少有上萬個（完整清單仍未確定）。Robertson–Seymour 的證明橫跨二十多篇論文，本課不證（Ch 36 會在 treewidth 的脈絡下再提）。
- **平面分隔定理**（Lipton–Tarjan 1979）：n 點平面圖可以拿掉 `O(√n)` 個點，把它分成兩半，每半 `≤ 2n/3`。這是平面圖上分治演算法的基礎，也解釋了為什麼網格（平面圖）上的稀疏線性系統比一般稀疏系統好解。
- **虧格與 Heawood 公式**：在虧格 g 的曲面上，Euler 公式變成 `V − E + F = 2 − 2g`，類似推論給出 `χ ≤ ⌊(7 + √(1 + 48g)) / 2⌋`（g ≥ 1），由 Ringel–Youngs（1968）證明是緊的。Ch 19 會講到 Heawood。
- **平面圖的對偶**：每個面一個點、相鄰的面連邊，得到對偶圖。地圖著色（面著色）等於對偶圖的點著色，這是 Ch 19 把「地圖四色」轉成「平面圖點著色」的方式。

## 動手練習

1. 用 `planar_bruteforce` 的 `count_faces`，對 K4 找出所有 rotation system 的面數分布，解釋為什麼最多 4 個面、最少幾個。
2. 證明推論 2 的「沒有三角形」可以換成「最短圈長度 `≥ g`」，得到 `E ≤ g(V − 2)/(g − 2)`；用 Petersen（最短圈 5）驗證它不是平面圖。
3. 拿 `smooth` 處理一個 K3,3 每條邊都插兩個點的細分，確認它還原成 K3,3。
4. 改寫 Delaunay 的實驗，數出外部面的長度 h，驗證 `E = 3V − 3 − h`。

## 本章重點整理

- Euler 公式 `V − E + F = 2`（連通平面圖），證明用對邊歸納 + Jordan 曲線定理。
- 雙重計數 `Σ 面長 = 2E` 加上面長下界，得到 `E ≤ 3V − 6`、無三角形時 `E ≤ 2V − 4`、一定有度數 `≤ 5` 的點。
- K5 違反第一個不等式，K3,3 違反第二個；兩者是唯二的「最小」障礙（Kuratowski：細分；Wagner：minor）。
- rotation system 把嵌入變成組合物件：面 = 有向邊的軌道，平面 ⟺ 存在 rotation system 使 `F = E − V + 2`。
- 實用判定是線性時間（Hopcroft–Tarjan、Boyer–Myrvold、Left-Right），並能輸出嵌入或 Kuratowski 子圖作為證據。

## 自我檢核

- [ ] 不看筆記，寫出 Euler 公式的歸納證明，並指出 Jordan 曲線定理用在哪一步、如果拿掉的邊不在圈上會壞在哪裡。
- [ ] 解釋為什麼 K3,3 用 `E ≤ 3V − 6` 證不出非平面，要換成什麼不等式、依據是什麼。
- [ ] 如果有人給你一張 1000 個點、3000 條邊的簡單圖並說它是平面圖，你能立刻說什麼？換成 2000 條邊，你又能說什麼、不能說什麼？
- [ ] 用自己的話解釋 subdivision 和 minor 的差別，並說出 Petersen 圖在兩者上的表現。
- [ ] 面追蹤規則「到了 b，轉向 a 在 b 的循環順序中的下一個」為什麼能保證每條有向邊恰屬一個面？

## 延伸閱讀

### 論文

- **[Sur le problème des courbes gauches en topologie](https://impan.pl/en/publishing-house/journals-and-series/fundamenta-mathematicae/all/15/0/92829/sur-le-probleme-des-courbes-gauches-en-topologie)** — K. Kuratowski，Fundamenta Mathematicae 15（1930）
  - **核心貢獻**：Kuratowski 定理的原始論文（法文）。
  - **讀哪裡**：主要是歷史意義；要學證明請讀下面 Thomassen 或教科書版本。
- **Kuratowski's theorem** — C. Thomassen，Journal of Graph Theory 5(3)（1981）
  - **核心貢獻**：用「3-連通圖有可縮邊」的歸納給出簡潔證明，是現代教科書證明的來源，本章的證明架構就是它。
  - **讀哪裡**：3-連通的約化與最後的分情況討論。
  - **前提**：Ch 11–12 的連通度與 Menger 定理。
- **[The Left-Right Planarity Test](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.planarity.check_planarity.html)** — U. Brandes（2009 手稿）；連結是 NetworkX 文件頁，頁面的 References 列出原文
  - **這篇說什麼**：把 de Fraysseix–Rosenstiehl 的 Left-Right 判定整理成可實作的形式，`nx.check_planarity` 依此實作。
  - **讀哪裡**：DFS 定向與 LR 約束的段落；NetworkX 原始碼 `networkx/algorithms/planarity.py` 可以對照讀。
  - **前提**：Ch 11 的 DFS 樹與回邊。

### 書籍

- **《Graph Theory》** — Reinhard Diestel（Springer GTM 173，第 5 版，2017）
  - **讀哪幾章**：第 4 章 Planar Graphs；4.2 Plane graphs（Euler 與面的嚴謹處理）、4.4 Planar graphs: Kuratowski's theorem（完整證明）。
  - **定位**：本章跳過的拓撲細節和 Kuratowski 的完整證明都在這裡。
- **《Introduction to Graph Theory》** — Douglas B. West（Prentice Hall，第 2 版，2001）
  - **讀哪幾章**：6.1 Embeddings and Euler's Formula、6.2 Characterization of Planar Graphs、6.3 Parameters of Planarity（虧格、交叉數）。
- **《Graphs on Surfaces》** — Bojan Mohar、Carsten Thomassen（Johns Hopkins University Press，2001）
  - **讀哪裡**：講組合嵌入（rotation system）與曲面上 Euler 公式的章節。
  - **定位**：本章「rotation system 窮舉」背後的完整理論；讀完 Ch 19 再看。

平面圖很稀疏、一定有低度數點——下一章把這兩個事實變成著色定理：五色有一頁長的證明，四色至今只有電腦輔助的證明。

→ [Ch 19 五色定理與四色定理](./19-five-and-four-color.md)
