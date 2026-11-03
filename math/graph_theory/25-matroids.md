# Ch 25 — Matroid：貪婪法為什麼有效

> **目標**：用三條公理定義 matroid，證明「貪婪法對所有權重都最優」若且唯若「結構是 matroid」（兩個方向都證），用它重新理解 Ch 10 的 Kruskal，並看到 matching 為什麼不是 matroid、卻是兩個 matroid 的交集。
>
> **環境**：Python 3.13.12、NetworkX 3.7、NumPy 2.5.4，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

你在 [Ch 10 最小生成樹與 cut/cycle property](./10-minimum-spanning-trees.md) 證過 Kruskal 是對的：邊照權重排序，不成圈就加。證明用了 cut property，而 cut property 的證明用到「樹」「割」這些圖論專屬的概念。

但「排序後能加就加」這個模式到處都是：從一堆向量裡挑權重最大的一組線性獨立基底；每個部門最多錄取 k 人、要總分最高；從 n 個候選裡挑最重的 k 個。它們有時對、有時錯——在一般圖上挑最重的 matching，貪婪就會錯（下面會實測）。每碰到一個新問題就重新證一次，或者更糟，憑直覺套貪婪然後在某個輸入上爆掉，這是工程師最常犯的演算法錯誤之一。

1935 年 Hassler Whitney 在〈On the abstract properties of linear dependence〉裡發現：線性代數的「線性獨立」和圖論的「沒有圈」滿足同一組公理。他把這組公理叫做 **matroid**（字面意思是「像矩陣的東西」）。後來 Rado（1957）與 Edmonds（1971）證明了一個漂亮的刻畫：**貪婪法對所有權重都給出最優解，若且唯若底下的結構是 matroid**。這個定理一般稱為 Rado–Edmonds 定理。

所以這章給你的不是又一個演算法，而是一把判斷尺：碰到「挑一組東西、每個有權重、有某種相容性限制」的問題，檢查限制是不是 matroid——是，貪婪必對；不是，貪婪可能錯，去找別的方法（通常是 flow、matching 或 matroid intersection）。

## 先建立直覺

想像你在組一支資安團隊，每個候選人有一個能力分數，限制是「團隊裡任兩人的專長不能重複」（每種專長最多一人）。貪婪：分數最高的先錄，專長撞了就跳過。你會隱約覺得這樣是對的——因為不管先錄誰，**最後能錄的總人數都一樣**（等於專長種類數），而且被擋掉的人永遠是被一個分數更高、同專長的人擋掉的。

這兩個感覺正是 matroid 的本質：

```text
獨立集族 I（「合法的選法」）要滿足：

  (I1) 空集合合法                                  ∅ ∈ I
  (I2) 合法選法的子集也合法（遺傳性 hereditary）     Y ∈ I, X ⊆ Y  ⇒  X ∈ I
  (I3) 小的合法選法可以從大的那裡借一個元素擴充     X, Y ∈ I, |X| < |Y|
       （交換公理 exchange axiom）                   ⇒ ∃ e ∈ Y \ X, X ∪ {e} ∈ I

  只滿足 (I1)(I2) 的叫 independence system；三條都滿足才是 matroid。
```

(I3) 是關鍵：它保證「卡住」只會發生在真的到頂的時候。如果你手上的 X 比別人的 Y 小，你一定還能從 Y 那裡撿一個元素而不違規——所以「貪婪地加到加不下去」不會停在一個比別人小的地方。

## 四個標準例子與它們為什麼是 matroid

ground set E 是有限集合，I 是 E 的一些子集。

| 名稱 | ground set | 獨立的意思 | 典型問題 |
| --- | --- | --- | --- |
| uniform `U(k, n)` | n 個元素 | 大小 ≤ k | 挑最重的 k 個 |
| partition | 被分成若干塊的元素 | 每塊 `B_i` 最多取 `k_i` 個 | 每個部門最多錄 k 人 |
| linear | 矩陣的行（column） | 這些行線性獨立 | 最大權重基底 |
| graphic `M(G)` | 圖的邊 | 這些邊沒有圈（是森林） | 最小/最大生成樹 |

(I1)(I2) 在四個例子都可以直接從定義驗證（子集不會讓「大小」「每塊個數」變多，線性獨立集的子集仍線性獨立，森林的子圖仍是森林）。逐一證 (I3)：

- **uniform**：`|X| < |Y| ≤ k`，Y \ X 非空，任取一個加入 X，大小 `≤ |X| + 1 ≤ k`。
- **partition**：`|X| < |Y|` 而兩者都是各塊個數的總和，所以存在某塊 `B_i` 使 `|Y ∩ B_i| > |X ∩ B_i|`。取 `e ∈ (Y \ X) ∩ B_i`，加入後 X 在 `B_i` 的個數 `≤ |Y ∩ B_i| ≤ k_i`，其他塊不變。注意塊必須**互斥**；若兩塊重疊（例如 `B_1 = {1,2}`、`B_2 = {2,3}`、`k = 1`），`X = {2}` 與 `Y = {1,3}` 都合法，但 X 無法從 Y 借任何元素，(I3) 失敗。
- **linear**：X 的行張出 `|X|` 維子空間，Y 的行張出 `|Y|` 維。若 Y 的每一行都落在 span(X) 裡，則 span(Y) ⊆ span(X)，維度 `|Y| ≤ |X|`，矛盾。所以某個 `y ∈ Y` 不在 span(X)，加入後仍線性獨立（而 y 不在 span(X) 也就意味著 y 不在 X 裡）。
- **graphic**：n 個點上有 k 條邊的森林恰有 `n − k` 個連通分量（每加一條不成圈的邊，分量數減一）。`|X| < |Y|` 所以 Y 的分量數 `n − |Y|` 少於 X 的 `n − |X|`。若 Y 的每條邊兩端都在 X 的同一個分量裡，則 Y 的每個分量都被包含在 X 的某個分量內，Y 的分量數至少和 X 一樣多，矛盾。所以存在 `e ∈ Y` 連接 X 的兩個不同分量；e 不在 X 裡（X 的邊不跨分量），加入 X 不會成圈。

從 (I3) 立刻得到一個重要推論：**所有極大獨立集（稱為 base，基底）大小相同**。若基底 A、B 滿足 `|A| < |B|`，(I3) 給出 `e ∈ B \ A` 使 `A + e` 獨立，A 就不是極大的。這個共同大小叫 matroid 的 **rank**。graphic matroid 的基底是生成森林；連通圖的 rank 就是 `n − 1`，這正是 [Ch 9](./09-trees-cayley-prufer.md) 樹的等價定義之一。

## 一個 greedy，四種 matroid

演算法只依賴一個「獨立性 oracle」：給它一個集合，回答是否獨立。

```python
"""通用貪婪 + 四種 matroid 的獨立性 oracle，並用 Kruskal 對拍 NetworkX。"""
import random

import networkx as nx
import numpy as np


def greedy(E, w, indep):
    """依權重由大到小掃，能加就加。indep(S) 是獨立性 oracle。"""
    S = []
    for e in sorted(E, key=lambda e: -w[e]):
        if indep(S + [e]):
            S.append(e)
    return S


def graphic(S):
    """S 是邊 (u, v) 的清單；沒有圈 = 獨立。用 union-find 判斷。"""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for u, v in S:
        ru, rv = find(u), find(v)
        if ru == rv:
            return False
        parent[ru] = rv
    return True


def uniform(k):
    return lambda S: len(S) <= k


def partition(block, cap):
    """block[e] = e 所在的塊；每塊最多 cap[塊] 個。"""
    def indep(S):
        cnt = {}
        for e in S:
            cnt[block[e]] = cnt.get(block[e], 0) + 1
        return all(c <= cap[b] for b, c in cnt.items())
    return indep


def linear(M):
    """M 的行（column）是 ground set；線性獨立 = 獨立。"""
    return lambda S: np.linalg.matrix_rank(M[:, S]) == len(S) if S else True


# 1) graphic matroid：貪婪 = Kruskal。最小生成樹 = 對 C - w 做最大權重基底
rng = random.Random(25)
for trial in range(200):
    G = nx.gnm_random_graph(rng.randint(2, 12), rng.randint(1, 30), seed=trial)
    for u, v in G.edges:
        G[u][v]["weight"] = rng.randint(1, 20)
    E = list(G.edges)
    w = {e: 100 - G.edges[e]["weight"] for e in E}       # 翻轉成「越輕越好」
    ours = sum(G.edges[e]["weight"] for e in greedy(E, w, graphic))
    ref = nx.minimum_spanning_tree(G).size(weight="weight")
    assert ours == ref, (trial, ours, ref)
print("graphic: 200 random graphs, greedy == nx.minimum_spanning_tree")

# 2) uniform U_{2,5}：挑最重的 2 個
E = list("abcde")
w = dict(zip(E, [5, 9, 1, 7, 3]))
print("uniform U(2,5):", greedy(E, w, uniform(2)))

# 3) partition：每個部門最多錄取 cap 人
block = {"amy": "sec", "bob": "sec", "cat": "sec", "dan": "net", "eve": "net"}
score = {"amy": 8, "bob": 9, "cat": 7, "dan": 6, "eve": 5}
print("partition:", greedy(list(block), score, partition(block, {"sec": 2, "net": 1})))

# 4) linear：R^3 裡的 5 個向量，挑權重最大的一組基底
M = np.array([[1, 0, 1, 2, 0],
              [0, 1, 1, 2, 0],
              [0, 0, 0, 0, 1]])
w = {0: 3, 1: 1, 2: 4, 3: 5, 4: 2}
print("linear basis (column ids):", greedy(list(range(5)), w, linear(M)))
```

```text
graphic: 200 random graphs, greedy == nx.minimum_spanning_tree
uniform U(2,5): ['b', 'd']
partition: ['bob', 'amy', 'dan']
linear basis (column ids): [3, 0, 4]
```

幾個值得看的細節：

- **最小生成樹怎麼變成「最大」權重問題？** 因為所有基底大小相同（rank），把權重換成 `C − w`（C 取比所有權重大的常數，這裡 100），每個基底的新權重 = `rank·C − 舊權重`，最大化新權重就等於最小化舊權重。而且所有新權重為正，貪婪會一路加到成為基底。`gnm_random_graph` 產生的圖常常不連通，這時兩邊算的都是**最小生成森林**——NetworkX 的 `minimum_spanning_tree` 在不連通圖上回傳的就是森林，這是 3.7 docstring 寫明的行為（"Otherwise, a spanning forest is found."）。
- **linear 例子裡第 2 行被跳過**：它權重 4 排第二，但 `(1,1,0)` 與已選的第 3 行 `(2,2,0)` 平行，加入就線性相依。貪婪接著選了權重較低的第 0 行和第 4 行。最後得到 `R^3` 的一組基底，大小 3 = rank。
- **oracle 成本**：這個 `graphic` 每次從頭重建 union-find，總共 `O(m^2 α)`。真正的 Kruskal 把 union-find 留著增量更新，降到 `O(m log m)`（排序主導）。matroid 的抽象版本告訴你**對不對**，具體實作決定**多快**。

## 底層機制：貪婪最優 ⟺ matroid

**定理（Rado–Edmonds）**：設 (E, I) 是 independence system（滿足 I1、I2）。對所有非負權重 `w: E → R≥0`，貪婪法都回傳權重最大的獨立集，若且唯若 (E, I) 滿足 (I3)，即它是 matroid。

這裡的貪婪法：把元素依權重由大到小排序（同權重任意順序），依序檢查，若加入後仍獨立就加入。

**證明 (⇐)：matroid 上貪婪最優。**

設貪婪依序選出 `g_1, g_2, …, g_k`，則 `w(g_1) ≥ w(g_2) ≥ … ≥ w(g_k)`。貪婪掃完所有元素，結果 G 是極大獨立集，即基底，`k = rank`。

取任一最優獨立集；因為權重非負，把它用 (I3) 擴充成基底不會讓權重變小，所以可以假設最優解 O 是基底，`|O| = k`。把 O 也排序：`O = {o_1, …, o_k}`，`w(o_1) ≥ … ≥ w(o_k)`。

**宣稱**：對每個 i，`w(g_i) ≥ w(o_i)`。

反設存在 i 使 `w(g_i) < w(o_i)`，取最小的這種 i。令 `A = {g_1, …, g_{i−1}}`、`B = {o_1, …, o_i}`。兩者都獨立（基底的子集，用 I2），`|A| = i − 1 < i = |B|`。由 (I3)，存在 `o_j ∈ B \ A` 使 `A ∪ {o_j}` 獨立。注意 `w(o_j) ≥ w(o_i) > w(g_i)`。

考慮貪婪掃描到 `o_j` 的那一刻。因為 `w(o_j) > w(g_i)`，`o_j` 排在 `g_i` 之前被檢查，那時貪婪手上的集合 A' 是 A 的一個前綴，`A' ⊆ A`。`A' ∪ {o_j} ⊆ A ∪ {o_j}` 是獨立集的子集，由 (I2) 獨立，所以貪婪**會**選 `o_j`。被選的元素權重 `> w(g_i)`，必然是 `g_1, …, g_{i−1}` 之一，也就是 `o_j ∈ A`——和 `o_j ∈ B \ A` 矛盾。

宣稱成立，加總得 `w(G) = Σ w(g_i) ≥ Σ w(o_i) = w(O)`，G 最優。∎

注意證明裡兩個公理各用在哪：(I3) 製造出一個「該被選卻沒被選」的元素，(I2) 保證貪婪當時確實可以選它。

**證明 (⇒)：不是 matroid，就存在讓貪婪失敗的權重。**

設 (I3) 不成立：存在獨立集 X、Y，`|X| < |Y|`，但對所有 `e ∈ Y \ X`，`X ∪ {e}` 都不獨立。令 `p = |X|`，構造權重

```text
w(e) = p + 2    若 e ∈ X
w(e) = p + 1    若 e ∈ Y \ X
w(e) = 0        其他
```

貪婪先掃 X 的元素（權重最高）。X 的每個前綴都是 X 的子集，由 (I2) 獨立，所以 X 全部被選。接著掃 `Y \ X` 的元素 e：貪婪手上的集合 S 包含 X，若 `S ∪ {e}` 獨立，則其子集 `X ∪ {e}` 也獨立（I2），與假設矛盾。所以 `Y \ X` 一個都選不到；剩下的元素權重 0，不影響總和。貪婪的權重是 `p(p + 2)`。

另一方面，Y 本身獨立，權重 `≥ |Y \ X|·(p + 1) + |X ∩ Y|·(p + 2) ≥ |Y|·(p + 1) ≥ (p + 1)^2 = p^2 + 2p + 1 > p(p + 2)`。貪婪不是最優。∎

（這裡用整數權重 `p + 2`、`p + 1` 是為了下面的暴力驗證能只用小整數；等價的寫法是 X 給 `1 + ε`、Y \ X 給 1，`ε < 1/p`。）

## 暴力驗證：所有 4 元素的 independence system

證明寫完，用程式確認沒有漏洞。4 個元素的子集族有 `2^16 = 65536` 種，篩出滿足 (I1)(I2) 的，逐一檢查「是否 matroid」與「是否對所有權重 `{0,…,5}^4` 貪婪都最優」。權重上限取 5，是因為 (⇒) 的構造在 `p = |X| ≤ 3` 時用到的最大權重是 `p + 2 = 5`。

```python
"""在 4 個元素的 ground set 上窮舉所有 independence system，
驗證：greedy 對所有權重都最優  <=>  滿足 exchange axiom（是 matroid）。"""
from itertools import product

N = 4
SUBSETS = list(range(1 << N))          # 用 bitmask 表示子集


def popcount(x):
    return bin(x).count("1")


def is_down_closed(fam):
    # 只要檢查「拿掉一個元素」：遞移下去就涵蓋所有子集
    return all((S & ~(1 << e)) in fam for S in fam for e in range(N) if S >> e & 1)


def exchange_ok(fam):
    for X in fam:
        for Y in fam:
            if popcount(Y) > popcount(X):
                if not any((Y >> e & 1) and not (X >> e & 1) and (X | 1 << e) in fam
                           for e in range(N)):
                    return False
    return True


def greedy_value(fam, w):
    S = 0
    for e in sorted(range(N), key=lambda e: -w[e]):
        if (S | 1 << e) in fam:
            S |= 1 << e
    return sum(w[e] for e in range(N) if S >> e & 1)


def best_value(fam, w):
    return max(sum(w[e] for e in range(N) if S >> e & 1) for S in fam)


weights = list(product(range(6), repeat=N))        # 每個元素權重 0..5，共 6^4 組
systems = matroids = agree = 0
for mask in range(1 << len(SUBSETS)):
    fam = {S for S in SUBSETS if mask >> S & 1}
    if 0 not in fam or not is_down_closed(fam):
        continue
    systems += 1
    is_matroid = exchange_ok(fam)
    greedy_always = all(greedy_value(fam, w) == best_value(fam, w) for w in weights)
    matroids += is_matroid
    agree += is_matroid == greedy_always
print(f"independence systems on {N} elements: {systems}")
print(f"matroids among them:                 {matroids}")
print(f"systems where (matroid <=> greedy always optimal) holds: {agree}")
```

```text
independence systems on 4 elements: 167
matroids among them:                 68
systems where (matroid <=> greedy always optimal) holds: 167
```

167 個系統全部符合定理。68 這個數字可以獨立查證：OEIS A058673「Number of matroids on n labeled points」的 n = 4 項正是 68（n = 3 是 16，把 `N` 改成 3 跑會得到 19 個系統、16 個 matroid）。程式數出和文獻一致的數字，是 `exchange_ok` 寫對的旁證。

## 不是 matroid 的時候：matching 與 matroid intersection

圖的 matching（沒有共用端點的邊集）滿足 (I1)(I2)，但不滿足 (I3)。最小的反例是路徑 a–b–c–d：

```python
"""matching 不是 matroid；但二分圖 matching = 兩個 partition matroid 的交集。"""
from itertools import combinations

import networkx as nx

# 1) 路徑 a-b-c-d 上的 matching：貪婪失敗
w = {("a", "b"): 2, ("b", "c"): 3, ("c", "d"): 2}


def is_matching(S):
    ends = [x for e in S for x in e]
    return len(ends) == len(set(ends))


S = []
for e in sorted(w, key=lambda e: -w[e]):
    if is_matching(S + [e]):
        S.append(e)
G = nx.Graph()
for (u, v), x in w.items():
    G.add_edge(u, v, weight=x)
opt = nx.max_weight_matching(G)
print("greedy matching:", S, "weight", sum(w[e] for e in S))
print("optimal       :", sorted(tuple(sorted(e)) for e in opt),
      "weight", sum(G[u][v]["weight"] for u, v in opt))
X, Y = [("b", "c")], [("a", "b"), ("c", "d")]
print("exchange X={bc}, Y={ab,cd}:", [e for e in Y if is_matching(X + [e])])

# 2) 二分圖：左點 L、右點 R；M1 限制每個左點最多 1 條邊，M2 限制每個右點
E = [("L1", "R1"), ("L1", "R2"), ("L2", "R1"), ("L3", "R1"), ("L3", "R3"), ("L4", "R3")]


def rank_partition(A, side):
    return len({e[side] for e in A})        # partition matroid（容量 1）的 rank


best = max(k for k in range(len(E) + 1) for S in combinations(E, k)
           if rank_partition(S, 0) == k and rank_partition(S, 1) == k)
minmax = min(rank_partition(A, 0) + rank_partition(set(E) - set(A), 1)
             for k in range(len(E) + 1) for A in combinations(E, k))
B = nx.Graph(E)
hk = nx.bipartite.hopcroft_karp_matching(B, top_nodes=[u for u, _ in E])
print("max common independent set:", best)
print("min_A r1(A) + r2(E-A)      :", minmax)
print("hopcroft_karp matching size:", len(hk) // 2)
```

```text
greedy matching: [('b', 'c')] weight 3
optimal       : [('a', 'b'), ('c', 'd')] weight 4
exchange X={bc}, Y={ab,cd}: []
max common independent set: 3
min_A r1(A) + r2(E-A)      : 3
hopcroft_karp matching size: 3
```

第三行就是 (I3) 失敗的現場：`X = {bc}` 比 `Y = {ab, cd}` 小，卻一條都借不到。而貪婪的失敗正是 (⇒) 證明描述的機制——先吃下 X，然後被 X 擋住整個 Y。

但二分圖的 matching 有一個救贖：**它是兩個 matroid 的交集**。令 M1 是「每個左點最多一條邊」的 partition matroid（塊 = 以同一左點為端點的邊），M2 是「每個右點最多一條邊」的 partition matroid。一個邊集是 matching ⟺ 它同時在 M1 和 M2 裡獨立。兩個 matroid 的交集通常不是 matroid（上面的 matching 就是例子），所以貪婪不保證對，但 Edmonds（1970）證明了 **matroid intersection 定理**：

```text
max { |I| : I ∈ I1 ∩ I2 }  =  min over A ⊆ E of  r1(A) + r2(E \ A)
```

其中 `r_i(A)` 是 A 在 `M_i` 裡最大獨立子集的大小。上面的暴力驗證三個數都是 3。把 M1、M2 的 rank 函數代進去，右式變成「選一組左點與右點蓋住所有邊的最小代價」——這就是 [Ch 22](./22-bipartite-matching.md) 的 König 定理。matroid intersection 有多項式時間演算法（增廣路式的，結構和二分圖匹配很像），這裡不展開，**只給定理敘述，沒有證明**。

一個邊界要記住：**三個** matroid 的交集就是 NP-hard 的。有向圖的 Hamilton 路徑可以寫成 graphic matroid（忽略方向後無圈）與兩個 partition matroid（每點出度 ≤ 1、入度 ≤ 1）的交集——所以「兩個」是一條真正的分水嶺，呼應 [Ch 8](./08-euler-hamilton.md) 的容易與困難。

## 和 Ch 10 cut/cycle property 的對應

| Ch 10 的圖論語言 | matroid 語言 |
| --- | --- |
| 森林 | 獨立集 |
| 生成樹（連通圖） | 基底（base） |
| 圈 | circuit（極小相依集） |
| `n − 1`（樹的邊數） | rank |
| cycle property：圈上最重的邊不在某棵 MST | circuit 上權重最小的元素不在某個最大權重基底 |
| Kruskal | matroid greedy |

cut property 和 cycle property 是這個定理在 graphic matroid 上的兩個投影。matroid 版的證明裡沒有出現「割」或「樹」，只用了 (I2)(I3)，所以它同時證明了線性基底、partition 選拔、uniform 選拔的貪婪都對。另一方面 Prim 演算法**不是**直接的 matroid greedy（它要求選的邊維持連通，元素的考慮順序依賴已選集合）；它的正確性要靠 cut property 另外證明。

## 對比與取捨

| 結構 | 貪婪最優？ | 正確做法 | 複雜度（大致） |
| --- | --- | --- | --- |
| 單一 matroid | 是（本章定理） | 排序 + oracle | `O(n log n + n·T_oracle)` |
| 兩個 matroid 的交集（二分圖匹配、arborescence 等） | 否 | matroid intersection、或特化成 flow / Hopcroft–Karp | 多項式 |
| 一般圖 matching | 否 | Blossom（Ch 23） | 多項式 |
| 三個 matroid 的交集 | 否 | NP-hard，用 ILP/SAT 或近似 | 指數（最壞） |
| independence system 但非 matroid | 否 | 視問題而定；貪婪有時有近似保證 | — |

## 踩雷集錦

1. **「每一步局部最優」就以為全域最優**：matching 的例子裡每一步都選了當下最重的邊，結果差了 25%。貪婪對不對是結構的性質，不是演算法本身的性質。檢查方法：找兩個大小不同的極大可行解——如果存在，(I3) 一定失敗，貪婪一定有反例。
2. **以為「最小權重」直接套就好**：在 matroid 上找最小權重**獨立集**，非負權重下答案是空集合。你真正要的是最小權重**基底**，做法是權重翻轉成 `C − w`，或者升序排列、強制加到基底大小為止。
3. **partition matroid 的塊必須互斥**：塊重疊（例如「每個專長最多一人」但一個人有兩個專長）就不再是 matroid，上面 `B_1 = {1,2}`、`B_2 = {2,3}` 是反例。那其實是 bipartite matching / matroid intersection 問題。
4. **以為 matroid 的交集還是 matroid**：二分圖 matching 是兩個 partition matroid 的交集，卻不是 matroid。交集要用專門的演算法；三個以上就是 NP-hard。
5. **把 linear matroid 的 rank 交給浮點數**：`np.linalg.matrix_rank` 用 SVD 加容忍值判斷，接近相依的向量可能被誤判。需要精確答案時（例如 GF(2) 上的向量），用有理數或模運算做高斯消去。

## 進階：再往深一層

- **其他公理系統**：matroid 可以等價地用 base、circuit、rank 函數（submodular：`r(A) + r(B) ≥ r(A ∪ B) + r(A ∩ B)`）來定義。rank 函數的 submodularity 是 submodular optimization 這整個領域的起點。
- **matroid polytope**：獨立集的指示向量的凸包，可以用 `x(A) ≤ r(A)` 這組不等式完整描述（Edmonds）。這是下一章「LP 的最優頂點自動是整數」的又一個例子，貪婪法則是這個 LP 的最優解構造法。
- **對偶 matroid**：把基底換成基底的補集也是 matroid。graphic matroid 的對偶和平面圖的對偶圖對應，連到 [Ch 18](./18-planar-graphs.md)。
- **工程應用**：GF(2) 上的 linear matroid 就是 XOR 線性基底，用來在一組位元向量裡挑「互相不可由 XOR 組合出來」的最大權重子集；排程問題「單位工時、有截止時間、最大化準時完成的權重」也是 matroid（transversal matroid 的特例），貪婪可解。

## 動手練習

1. 給 `matroid_greedy.py` 加一個 `transversal` oracle：E 是工作，每個工作可以分給某些工人，「獨立」= 存在一個 matching 把這些工作全部分出去（用 `nx.bipartite.hopcroft_karp_matching` 判斷）。在小例子上用 `brute_theorem.py` 的 `exchange_ok` 思路驗證它滿足 (I3)。
2. 把 `brute_theorem.py` 的權重範圍改成 `range(5)`（0..4），看是否仍然 167 個全部一致。如果不一致，找出是哪個系統、哪組 X、Y，並解釋為什麼 (⇒) 的構造需要權重 5。
3. 改寫 `graphic` 成真正的增量 Kruskal（union-find 保留在外面），用 `time.perf_counter` 在 `gnm_random_graph(2000, 20000, seed=1)` 上比較兩個版本的時間，並記下你的機器與條件。

## 本章重點整理

- matroid = independence system + 交換公理；交換公理推出所有基底等大。
- uniform、partition（塊互斥）、linear、graphic 都是 matroid，證明各只要幾行；Kruskal 是 graphic matroid 上的通用貪婪。
- Rado–Edmonds：貪婪對所有非負權重最優 ⟺ matroid。(⇐) 用 (I3) 造出「該被選的元素」再用 (I2) 推出矛盾；(⇒) 用 X 與 Y \ X 的兩級權重構造反例。
- matching 不是 matroid；二分圖 matching 是兩個 matroid 的交集，有 min-max 定理與多項式演算法；三個 matroid 的交集是 NP-hard。

## 自我檢核

- [ ] 不看筆記，從三條公理推出「所有基底大小相同」。
- [ ] 能在白板上寫出 (⇐) 方向的證明，並指出 (I2) 用在哪一步？如果拿掉 (I2) 只留 (I3)，證明哪裡會斷？
- [ ] 有人在 code review 裡寫了一個「依分數排序、能加就加」的選拔程式，你會問他哪個問題來判斷它是否正確？
- [ ] 能說出一個你工作中遇到、看起來能用貪婪但其實是兩個 matroid 交集的問題嗎？
- [ ] 為什麼 graphic matroid 的交換公理證明要數連通分量，而不是數邊？

## 延伸閱讀

### 論文

- **On the Abstract Properties of Linear Dependence** — Hassler Whitney，American Journal of Mathematics 57(3):509–533（1935），[JSTOR 2371182](https://www.jstor.org/stable/2371182)
  - **核心貢獻**：matroid 理論的起點，把向量的線性獨立與圖的無圈性抽象成同一組公理，並給出 base、circuit、rank 等多種等價定義。
  - **讀哪裡**：前幾節的公理與等價定義；後面關於圖的部分可以對照本章的 graphic matroid。
  - **和本章的關聯**：本章的 (I1)–(I3) 與 rank 都來自這篇。前提：線性代數的 span 與維度。
- **[Matroids and the Greedy Algorithm](https://doi.org/10.1007/BF01584082)** — Jack Edmonds，Mathematical Programming 1:127–136（1971）
  - **核心貢獻**：從最佳化角度證明 matroid 正是貪婪法有效的結構，並給出 matroid polytope 的描述。
  - **讀哪裡**：開頭的貪婪定理與證明；polytope 部分等讀完 Ch 26 再回來。
  - **和本章的關聯**：本章主定理的經典出處之一。前提：本章的證明。
- **[Submodular Functions, Matroids, and Certain Polyhedra](https://doi.org/10.1007/3-540-36478-1_2)** — Jack Edmonds，原為 1970 年會議論文，2003 年重刊於 LNCS 2570《Combinatorial Optimization — Eureka, You Shrink!》
  - **核心貢獻**：matroid intersection 的 min-max 公式與 polymatroid；Schrijver 的書指出 Edmonds 在這篇給了基於 LP 對偶與全單模性的證明。
  - **讀哪裡**：matroid intersection 定理那一節。
  - **和本章的關聯**：本章 intersection 預告的原始出處；也是 Ch 26 全單模性的一個應用。

### 講義

- **[Lecture notes on matroids](https://math.mit.edu/~goemans/18433S13/matroid-notes.pdf)** — Michel X. Goemans，MIT 18.433 Combinatorial Optimization（2013）
  - **這份說什麼**：定義、uniform/partition 等例子（含塊重疊時不是 matroid 的反例）、§4.2 Matroid Optimization 的貪婪證明、§4.4 matroid polytope 的三種證明。
  - **讀哪裡**：§4.1–4.3 對應本章；§4.4 是 Ch 26 的好橋樑。
  - **為什麼值得讀**：作者是組合最佳化的一線研究者，講義篇幅短、證明完整。同一課的 [matroid intersection 講義](https://math.mit.edu/~goemans/18433S11/matroid-intersect-notes.pdf) 補上本章沒證的 intersection 定理。

### 書籍

- **《Matroid Theory》** — James Oxley（Oxford University Press，第 2 版，2011，Oxford Graduate Texts in Mathematics 21）
  - **這本書的定位**：matroid 理論的標準研究所教科書，偏結構與代數，不偏演算法。
  - **讀哪幾章**：第 1 章（基本定義與例子，含各種等價公理）；第 2 章對偶。作者維護一份到 2020 年的勘誤表。
- **《Combinatorial Optimization: Theory and Algorithms》** — Bernhard Korte、Jens Vygen（Springer，第 6 版，2018）
  - **這本書的定位**：演算法導向；第 13 章「Matroids」涵蓋貪婪、matroid intersection 演算法與 matroid partition，第 14 章是推廣。
  - **讀哪幾章**：第 13 章；前提是本章與 Ch 22。

matroid intersection 的 min-max 公式、匈牙利法的對偶證書、max-flow min-cut——它們長得太像了，不可能是巧合。下一章用線性規劃的對偶性把它們一次統一，並解釋為什麼這些 LP 的最優解自動是整數。

→ [Ch 26 LP 對偶與全單模矩陣](./26-lp-duality.md)
