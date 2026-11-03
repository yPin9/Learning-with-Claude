# Ch 2 — 證明工具箱：歸納、反證、鴿籠、極端原理

> **目標**：學會六種最常用的證明技巧（直接證明與雙重計數、歸納、強歸納、反證、鴿籠、極端原理、雙射），能用它們寫出完整、可檢查的圖論證明，並認得最常見的幾種錯誤證明。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。每個定理都附一段在小圖上窮舉驗證的程式。

## 為什麼需要這個？

工程師對「證明」的印象多半來自考試：一堆符號，最後寫個 QED。所以常見的態度是「程式跑得過就好，證明留給數學家」。

但你其實天天在依賴證明，只是別人幫你做了。Dijkstra 為什麼在負權邊上會錯？貪婪著色為什麼有時用太多顏色？LLVM 為什麼可以只看 dominance frontier 就決定 φ 放哪？這些問題的答案都是一個證明，或一個證明在哪一步需要的假設。當你自己設計演算法——例如一個新的 taint 傳播規則、一個攻擊圖的剪枝——沒有人會幫你證明，測試也只能抓到你想得到的情況。

這一章的目標不是讓你變成數學家，而是讓你能做三件事：

1. 讀懂後面 40 章的證明，知道每一步用的是哪種技巧。
2. 自己寫出「別人能逐行檢查」的論證。
3. 看到一個錯的證明時，能指出錯在哪一步。

## 先建立直覺

把證明想成一段**沒有執行環境的程式**：

```text
程式                         證明
---------------------------  ---------------------------------
輸入型別、前置條件            假設（hypothesis）
回傳值的保證                  結論（conclusion）
每一行都要能被編譯器檢查      每一步都要能被讀者檢查
迴圈不變式 + 終止              歸納法：基底 + 歸納步驟
assert 失敗就丟例外            反證法：推出矛盾就否定假設
測試只能證明有 bug            窮舉小例子只能找反例
```

最後一行是本課的核心工作流：先用程式窮舉找反例，找不到再寫證明。證明寫完，再回頭看程式驗證的範圍是否覆蓋了證明裡的每個分支。

> 如果你還沒跑過 [Ch 0](./00-environment-setup.md) 的窮舉骨架，先回去跑一次；本章每個定理都沿用它。

## 邏輯的最小語彙

證明會反覆用到下面幾種敘述的變形。弄錯它們是錯誤證明的頭號來源。

| 原敘述 `P ⇒ Q` | 名稱 | 和原敘述等價嗎 |
| --- | --- | --- |
| `¬Q ⇒ ¬P` | 逆否命題（contrapositive） | **等價** |
| `Q ⇒ P` | 逆命題（converse） | 不等價 |
| `¬P ⇒ ¬Q` | 否命題（inverse） | 不等價 |

用 Euler 的結果舉例。原敘述「若圖有一筆畫，則奇點數 ≤ 2」為真。逆否命題「若奇點數 > 2，則沒有一筆畫」也為真——Ch 1 就是用這個形式判定七橋。逆命題「若奇點數 ≤ 2，則有一筆畫」**不成立**：兩個分開的三角形沒有奇點，卻不能一筆畫。

量詞的否定要翻轉：

```text
¬(對所有圖 G，P(G))   ≡   存在某張圖 G，¬P(G)     ← 推翻「對所有」：一個反例就夠
¬(存在頂點 v，P(v))   ≡   對所有頂點 v，¬P(v)     ← 推翻「存在」要檢查全部
```

這就是為什麼窮舉找反例能**推翻**全稱命題，卻不能**證明**它。

## 技巧一：直接證明與雙重計數

**雙重計數（double counting）**：用兩種方法數同一個集合，兩個答案必然相等。

**定理 2.1（握手定理）**：任意圖 G = (V, E) 中，`Σ_{v∈V} deg(v) = 2|E|`。

**證明**：令 `S = {(v, e) : v ∈ V, e ∈ E, v 是 e 的端點}`，數 `|S|` 兩次。

- 按頂點分組：固定頂點 v，滿足 `(v, e) ∈ S` 的 e 恰好是碰到 v 的邊，共 `deg(v)` 條。所以 `|S| = Σ_v deg(v)`。
- 按邊分組：固定邊 e，它恰有兩個端點（簡單圖沒有自環），所以每條 e 貢獻 2 個元素，`|S| = 2|E|`。

兩者相等，證畢。

**推論 2.2**：任意圖的奇數度頂點個數為偶數。

**證明**：把頂點分成奇數度集合 `V_odd` 與偶數度集合 `V_even`。由定理 2.1，`Σ_{V_odd} deg(v) + Σ_{V_even} deg(v) = 2|E|` 是偶數。第二項是偶數的和，為偶數，所以第一項也是偶數。第一項是 `|V_odd|` 個奇數的和；k 個奇數的和與 k 同奇偶，因此 `|V_odd|` 是偶數。證畢。

注意兩個細節。第一，「簡單圖沒有自環」在證明裡被用到了：如果允許自環，按 NetworkX 的慣例自環讓度數加 2（Ch 0 坑 5），定理依然成立，但「每條邊恰有兩個端點」那句要改寫成「每條邊貢獻 2 到度數總和」。寫證明時，每個假設在哪裡被用到都應該看得出來。第二，推論的最後一步「k 個奇數的和與 k 同奇偶」本身也是需要理由的小事實；在正式證明裡可以寫一句「因為每個奇數 ≡ 1 (mod 2)」帶過，但不能跳過不提。

## 技巧二：數學歸納法

歸納法證明「對所有 n ≥ n0，P(n) 成立」：

```text
基底：     證明 P(n0)
歸納步驟：  假設 P(n) 成立（歸納假設），證明 P(n+1)
```

它的正確性本身可以用反證說明：若 P 對某些 n 不成立，取最小的那個 `n*`。`n* ≠ n0`（基底成立），所以 `P(n* − 1)` 成立，由歸納步驟 `P(n*)` 成立，矛盾。這個「取最小反例」的論證就是後面的極端原理。

### 圖論歸納的正確姿勢：拆，不要蓋

先定義：**樹（tree）** 是連通且沒有圈的圖；**葉（leaf）** 是度數為 1 的頂點。

**引理 2.3**：每棵至少有 2 個頂點的樹，至少有兩片葉子。

**證明**（極端原理，見技巧五）：樹 T 至少 2 個頂點且連通，所以至少有一條邊。在 T 的所有路徑中取一條**最長的** `P = v0 v1 ... vk`（T 是有限圖，路徑有限多條，最長的存在），`k ≥ 1`。假設 v0 除了 v1 之外還有鄰居 w：

- 若 w 在 P 上，`w = vi`，`i ≥ 2`，則 `v0 v1 ... vi v0` 是一個圈，和「T 無圈」矛盾。
- 若 w 不在 P 上，`w v0 v1 ... vk` 是一條比 P 長的路徑，和「P 最長」矛盾。

所以 `deg(v0) = 1`。同理 `deg(vk) = 1`，且 `v0 ≠ vk`（路徑的頂點互不相同）。證畢。

**定理 2.4**：n 個頂點的樹恰有 n − 1 條邊。

**證明**：對 n 歸納。

- **基底** n = 1：唯一的單點圖有 0 條邊。
- **歸納步驟**：假設所有 n 個頂點的樹都有 n − 1 條邊。令 T 是任一棵 n + 1 個頂點的樹（`n + 1 ≥ 2`）。由引理 2.3，T 有一片葉子 v。令 `T' = T − v`（刪掉 v 和它唯一的那條邊）。
  - T' 無圈：T' 的圈也會是 T 的圈。
  - T' 連通：對 T' 中任兩點 x、y，T 中有一條 x 到 y 的路徑。這條路徑的內部頂點度數至少 2，而 `deg(v) = 1` 且 v 不是 x 或 y，所以 v 不在這條路徑上，它整條都在 T' 裡。
  - 所以 T' 是 n 個頂點的樹，由歸納假設有 n − 1 條邊，T 有 `(n − 1) + 1 = n` 條邊。

證畢。

結構上注意：歸納步驟從**任意一棵** n + 1 點的樹出發，往下**拆**成 n 點的樹。反方向——「拿一棵 n 點的樹，加一個點變成 n + 1 點的樹」——是常見的錯誤寫法，後面的「錯誤證明」一節會用程式抓出它的漏洞。

窮舉驗證（n ≤ 6 的所有標號圖，判斷樹的方法刻意**不用**邊數，避免偷用結論）：

```python
from itertools import combinations


def all_labeled_graphs(n):
    pairs = list(combinations(range(n), 2))
    for mask in range(1 << len(pairs)):
        adj = {v: set() for v in range(n)}
        for i, (u, v) in enumerate(pairs):
            if mask >> i & 1:
                adj[u].add(v)
                adj[v].add(u)
        yield adj


def is_tree(adj):
    """連通（DFS）且無圈（union-find），不使用邊數。"""
    n = len(adj)
    seen, stack = {0}, [0]
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    if len(seen) != n:
        return False
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for u in adj:
        for v in adj[u]:
            if u < v:
                ru, rv = find(u), find(v)
                if ru == rv:
                    return False
                parent[ru] = rv
    return True


for n in range(1, 7):
    trees = [a for a in all_labeled_graphs(n) if is_tree(a)]
    edge_counts = {sum(len(s) for s in a.values()) // 2 for a in trees}
    min_leaves = min(sum(1 for s in a.values() if len(s) == 1) for a in trees)
    print(f"n={n}: 標號樹 {len(trees):>4} 棵, 邊數集合 {edge_counts}, 最少葉子數 {min_leaves}")
```

輸出：

```text
n=1: 標號樹    1 棵, 邊數集合 {0}, 最少葉子數 0
n=2: 標號樹    1 棵, 邊數集合 {1}, 最少葉子數 2
n=3: 標號樹    3 棵, 邊數集合 {2}, 最少葉子數 2
n=4: 標號樹   16 棵, 邊數集合 {3}, 最少葉子數 2
n=5: 標號樹  125 棵, 邊數集合 {4}, 最少葉子數 2
n=6: 標號樹 1296 棵, 邊數集合 {5}, 最少葉子數 2
```

邊數集合永遠只有 `{n − 1}` 一個元素，最少葉子數在 n ≥ 2 時是 2（路徑就是只有兩片葉子的樹，下界是緊的）。n = 1 時葉子數為 0，正好說明引理 2.3 為什麼要求 n ≥ 2。樹的數量 `1, 1, 3, 16, 125, 1296` 是 `n^(n−2)`，這是 Cayley 公式，[Ch 9](./09-trees-cayley-prufer.md) 會證明。

### 歸納即演算法：tournament 的 Hamilton 路徑

**tournament** 是每對頂點之間恰有一條有向邊的有向圖（想像循環賽，每場比賽有輸贏、沒有和局）。

**定理 2.5（Rédei，1934）**：每個 tournament 都有一條 Hamilton 路徑（經過每個頂點恰一次的有向路徑）。

**證明**：對頂點數 n 歸納。n = 1 時單點本身是路徑。假設 n 點都成立；給定 n + 1 點的 tournament，任取一點 v，其餘 n 點構成的子 tournament 由歸納假設有 Hamilton 路徑 `p1 → p2 → ... → pn`。

- 若 `v → p1`，`v → p1 → ... → pn` 即所求。
- 否則 `p1 → v`。若 `pn → v`，`p1 → ... → pn → v` 即所求。
- 否則 `p1 → v` 且 `v → pn`。考慮最小的 i 使得 `v → p_{i+1}`（i + 1 = n 時成立，所以這樣的 i 存在，且 i ≥ 1）。由 i 的最小性，`v → p_i` 不成立，也就是 `p_i → v`（i = 1 時直接由 `p1 → v` 得到）。於是 `p1 → ... → p_i → v → p_{i+1} → ... → pn` 即所求。

證畢。

這個證明是**構造性**的：它不只說路徑存在，還告訴你怎麼一次插入一個點把路徑建出來。直接把證明翻成程式：

```python
"""Rédei 定理：把歸納證明翻成插入法，窮舉 n<=5 並隨機測 n<=60。"""
from itertools import combinations
import random


def insert_path(nodes, beats):
    path = []
    for v in nodes:
        if not path or beats(v, path[0]):
            path.insert(0, v)
            continue
        if beats(path[-1], v):
            path.append(v)
            continue
        # 此時 path[0] -> v 且 v -> path[-1]：證明保證存在 i 使 path[i] -> v -> path[i+1]
        for i in range(len(path) - 1):
            if beats(path[i], v) and beats(v, path[i + 1]):
                path.insert(i + 1, v)
                break
        else:
            raise AssertionError("證明裡的引理失效")
    return path


def is_ham_path(path, beats, n):
    return len(path) == n and all(beats(path[i], path[i + 1]) for i in range(n - 1))


for n in range(1, 6):
    pairs = list(combinations(range(n), 2))
    total = 0
    for mask in range(1 << len(pairs)):
        win = {(u, v) if mask >> i & 1 else (v, u) for i, (u, v) in enumerate(pairs)}
        beats = lambda a, b, w=win: (a, b) in w
        assert is_ham_path(insert_path(range(n), beats), beats, n)
        total += 1
    print(f"n={n}: {total:>5} 個 tournament 都有 Hamilton 路徑")

SEED = 7
rng = random.Random(SEED)
for _ in range(300):
    n = rng.randint(1, 60)
    win = {(u, v) if rng.random() < 0.5 else (v, u) for u, v in combinations(range(n), 2)}
    beats = lambda a, b, w=win: (a, b) in w
    assert is_ham_path(insert_path(range(n), beats), beats, n)
print(f"seed={SEED}: 300 個隨機 tournament（n<=60），插入法全部給出 Hamilton 路徑")
```

輸出：

```text
n=1:     1 個 tournament 都有 Hamilton 路徑
n=2:     2 個 tournament 都有 Hamilton 路徑
n=3:     8 個 tournament 都有 Hamilton 路徑
n=4:    64 個 tournament 都有 Hamilton 路徑
n=5:  1024 個 tournament 都有 Hamilton 路徑
seed=7: 300 個隨機 tournament（n<=60），插入法全部給出 Hamilton 路徑
```

`raise AssertionError("證明裡的引理失效")` 那行是刻意放的：如果證明的第三種情況推理有誤，程式會在這裡爆掉。它從沒觸發，是證明和程式互相背書的一個小例子。一般圖的 Hamilton 路徑是 NP-complete（Ch 34），tournament 這個特例卻有 O(n²) 的構造法——結構決定難度。

## 技巧三：強歸納

**強歸納（strong induction）**：歸納步驟可以假設 P(k) 對**所有** `n0 ≤ k ≤ n` 都成立，再證 P(n+1)。它和普通歸納等價（把「P(n0), ..., P(n) 都成立」當成新的命題 Q(n) 做普通歸納），但當你把問題拆成兩塊大小不定的子問題時，寫起來自然得多。

**定理 2.4 的第二個證明**：對 n 強歸納。n = 1 成立。設 n ≥ 2，且所有頂點數小於 n 的樹都滿足定理。令 T 是 n 點的樹，任取一條邊 `e = uv`（n ≥ 2 且連通，邊存在）。

1. **T − e 不連通**：若 T − e 中有 u 到 v 的路徑，加上 e 就是 T 的一個圈，矛盾。
2. **T − e 恰有兩個連通元件，分別含 u 與 v**：任取頂點 x，取 T 中一條 x 到 u 的路徑 Q。若 Q 不經過 e，x 和 u 在同一元件。若 Q 經過 e，因為 Q 終點是 u，e 必是 Q 的最後一條邊（路徑不重複頂點，u 只出現在結尾），所以 Q 去掉最後一條邊是 x 到 v 且不經過 e 的路徑，x 和 v 在同一元件。
3. 兩個元件 T1、T2 各自連通（元件的定義）且無圈（T 的子圖），所以是樹，頂點數 a、b 滿足 `a + b = n`、`a, b ≥ 1`，因此 `a, b < n`。由強歸納假設，邊數分別是 a − 1、b − 1。

T 的邊數 `= (a − 1) + (b − 1) + 1 = n − 1`。證畢。

為什麼這裡需要強歸納？因為 a 可能是 1，也可能是 n − 1，我們不知道是哪個，所以需要「所有比 n 小的情況都成立」。這個「切成兩半、遞迴、合併」的結構和 divide-and-conquer 演算法一樣，正確性證明也一樣用強歸納。

## 技巧四：反證法與鴿籠原理

**反證法（proof by contradiction）**：假設結論不成立，推出矛盾。

一個最短的例子：**不存在 7 個頂點的 3-正則圖**（每點度數都是 3）。假設存在，度數總和是 `7 × 3 = 21`，是奇數，和握手定理（總和 `= 2|E|`）矛盾。

**鴿籠原理（pigeonhole principle）**：把 n + 1 個物件放進 n 個盒子，某個盒子至少有 2 個。

**證明**：若每個盒子至多 1 個，物件總數至多 n，矛盾。證畢。

一般化：N 個物件放進 k 個盒子，某個盒子至少有 `⌈N/k⌉` 個（否則總數至多 `k(⌈N/k⌉ − 1) < N`）。

鴿籠原理的難處從來不在原理本身，而在**怎麼選盒子**。

**定理 2.6**：任意至少 2 個頂點的簡單圖，必有兩個頂點度數相同。

**證明**：設 G 有 n ≥ 2 個頂點。簡單圖中每點度數在 `{0, 1, ..., n − 1}` 之中，共 n 個值。n 個頂點放進 n 個盒子，鴿籠原理還用不上——要再擠掉一個盒子。

度數 0 和度數 n − 1 不能同時出現：若 u 的度數是 n − 1，u 和所有其他點相鄰，那就沒有點的度數是 0。所以實際出現的度數最多只落在 `{0, ..., n − 2}` 或 `{1, ..., n − 1}` 其中一組，都只有 n − 1 個值。n 個頂點放進 n − 1 個盒子，必有兩點同度數。證畢。

```python
"""定理 2.6：在 atlas 上驗證，並展示多重圖為什麼不適用。"""
import networkx as nx

checked = 0
for G in nx.graph_atlas_g():
    n = G.number_of_nodes()
    if n < 2:
        continue
    degs = [d for _, d in G.degree()]
    assert len(set(degs)) < n, list(G.edges())
    checked += 1
print(f"atlas 中 {checked} 張 n>=2 的圖，每張都有兩點同度數")

M = nx.MultiGraph([("a", "b"), ("a", "b"), ("b", "c")])
print("多重圖反例的度數:", dict(M.degree()))
```

輸出：

```text
atlas 中 1251 張 n>=2 的圖，每張都有兩點同度數
多重圖反例的度數: {'a': 2, 'b': 3, 'c': 1}
```

多重圖的反例說明了證明用到哪個假設：「度數 ≤ n − 1」只在簡單圖成立。有平行邊時 b 的度數可以是 3 > n − 1 = 2，盒子變多，鴿籠就失效。

### 鴿籠加分類討論：六人必有三人互識或互不識

**定理 2.7**：任意 6 個頂點的圖 G，要嘛 G 有三角形，要嘛 G 的補圖有三角形（也就是有 3 個兩兩不相鄰的點）。

**證明**：任取頂點 v。其他 5 點分成「和 v 相鄰」與「和 v 不相鄰」兩個盒子，由一般化鴿籠，某個盒子至少 `⌈5/2⌉ = 3` 個點。

- 情況 1：至少 3 點 x、y、z 和 v 相鄰。若 x、y、z 中任兩點相鄰，例如 xy，則 vxy 是三角形。否則 x、y、z 兩兩不相鄰，就是補圖的三角形。
- 情況 2：至少 3 點和 v 不相鄰。對補圖套用情況 1 的論證（在補圖中這 3 點和 v 相鄰），結論對稱。

證畢。

這說明 Ramsey 數 `R(3,3) ≤ 6`。它在 5 點時不成立，窮舉會給出反例：

```python
"""R(3,3)：窮舉 n=5 與 n=6 的所有標號圖。"""
from itertools import combinations


def has_triangle_or_independent3(n, edges):
    for a, b, c in combinations(range(n), 3):
        k = ((a, b) in edges) + ((a, c) in edges) + ((b, c) in edges)
        if k == 0 or k == 3:
            return True
    return False


for n in (5, 6):
    pairs = list(combinations(range(n), 2))
    bad = None
    for mask in range(1 << len(pairs)):
        edges = {p for i, p in enumerate(pairs) if mask >> i & 1}
        if not has_triangle_or_independent3(n, edges):
            bad = sorted(edges)
            break
    print(f"n={n}: 反例 = {bad}")
```

輸出：

```text
n=5: 反例 = [(0, 3), (0, 4), (1, 2), (1, 4), (2, 3)]
n=6: 反例 = None
```

n = 5 的反例是 5-圈 `0-3-2-1-4-0`：它沒有三角形，補圖也是一個 5-圈，同樣沒有三角形。所以 `R(3,3) = 6`。n = 6 的窮舉跑過了全部 `2^15 = 32768` 張圖，這對 n = 6 本身是完整證明；定理 2.7 的證明則說明「為什麼」。[Ch 32](./32-ramsey-extremal.md) 會把這推廣到一般的 Ramsey 數。

## 技巧五：極端原理

**極端原理（extremal principle）**：考慮某個量最大或最小的物件——最長的路徑、度數最小的頂點、邊數最多的切割——然後證明它必須滿足你要的性質，否則可以找到更極端的物件，矛盾。

引理 2.3（最長路徑的端點是葉子）已經是一個例子。再看兩個。

**定理 2.8**：若圖 G 的最小度數 `δ ≥ 2`，則 G 有長度至少 `δ + 1` 的圈。（推論：每個頂點度數都 ≥ 2 的有限圖一定有圈。）

**證明**：取 G 中一條最長路徑 `P = v0 v1 ... vk`。v0 的每個鄰居都在 P 上，否則可以把 P 往前延長，矛盾。v0 至少有 δ 個鄰居，全部在 `{v1, ..., vk}` 中，所以其中下標最大的鄰居 `vi` 滿足 `i ≥ δ`。`v0 v1 ... vi v0` 是一個圈，長度 `i + 1 ≥ δ + 1`。證畢。

同一個論證也給出：最長路徑長度 `k ≥ i ≥ δ`。

**定理 2.9**：每個圖 G 都有一個二分子圖，邊數至少 `|E| / 2`。

**證明**：在所有把 V 分成兩份 `(A, B)` 的方式中，取跨越兩邊的邊數 `c(A, B)` **最大**的一種。對任一點 `v ∈ A`，若 v 在 A 內的鄰居比在 B 內的多，把 v 移到 B 會讓 `c` 增加，和最大性矛盾。所以每個 v 至少有一半的鄰居在另一邊，也就是 v 碰到的跨越邊至少 `deg(v) / 2` 條。對所有頂點加總，每條跨越邊被數兩次：

```text
2 · c(A, B) = Σ_v (v 碰到的跨越邊數) ≥ Σ_v deg(v) / 2 = |E|
```

所以 `c(A, B) ≥ |E| / 2`，跨越邊構成的子圖是二分圖。證畢。

```python
"""定理 2.8 與 2.9：在 atlas 上暴力驗證。"""
from itertools import product
import networkx as nx


def to_adj(G):
    return {u: set(G[u]) for u in G}


def longest_path_len(adj):
    best = 0

    def dfs(u, seen, length):
        nonlocal best
        best = max(best, length)
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                dfs(v, seen, length + 1)
                seen.remove(v)
    for s in adj:
        dfs(s, {s}, 0)
    return best


def longest_cycle_len(adj):
    best = 0

    def dfs(start, u, seen, length):
        nonlocal best
        for v in adj[u]:
            if v == start and length >= 3:
                best = max(best, length)
            elif v not in seen and v > start:   # 只從圈上最小的點出發，避免重複
                seen.add(v)
                dfs(start, v, seen, length + 1)
                seen.remove(v)
    for s in adj:
        dfs(s, s, {s}, 1)
    return best


checked = 0
worst = 1.0
for G in nx.graph_atlas_g():
    if G.number_of_nodes() == 0:
        continue
    adj = to_adj(G)
    delta = min(len(s) for s in adj.values())
    if delta >= 1:
        assert longest_path_len(adj) >= delta
        if delta >= 2:
            assert longest_cycle_len(adj) >= delta + 1
        checked += 1
    m = G.number_of_edges()
    if m:
        nodes = list(G)
        best = 0
        for side in product((0, 1), repeat=len(nodes)):
            S = {v for v, s in zip(nodes, side) if s}
            best = max(best, sum(1 for u, v in G.edges() if (u in S) != (v in S)))
        assert 2 * best >= m
        worst = min(worst, best / m)
print(f"{checked} 張 δ>=1 的圖：最長路徑 >= δ；δ>=2 時最長圈 >= δ+1，全部成立")
print(f"所有有邊的圖：max cut >= |E|/2 成立，最小比例 = {worst:.4f}")
```

輸出：

```text
1043 張 δ>=1 的圖：最長路徑 >= δ；δ>=2 時最長圈 >= δ+1，全部成立
所有有邊的圖：max cut >= |E|/2 成立，最小比例 = 0.5714
```

最小比例 `0.5714 = 12/21` 來自 K7（7 點完全圖）：最好的切法是 3 對 4，切到 12 條、總共 21 條。n 越大，完全圖的比例越接近 1/2，所以 1/2 這個常數不能再改進。

極端原理的證明也常常是演算法：定理 2.9 的證明暗示一個 **local search**——從任意切法開始，只要有點「站錯邊」就把它移過去。每次移動讓 c 至少加 1，c 不超過 |E|，所以最多 |E| 步就停，停下來時已滿足 `c ≥ |E| / 2`。[Ch 31](./31-probabilistic-method.md) 會用機率方法給同一個結論第三種證明。

## 技巧六：雙射

**雙射（bijection）**：要證明兩個集合一樣大，找一個一對一且映成的函數。寫雙射證明要檢查三件事：函數**定義良好**（輸出真的落在目標集合）、**單射**、**滿射**；或者直接給出反函數。

**例 1**：n 個標號頂點上的簡單圖共有 `2^C(n,2)` 張。雙射：圖 ↔ 長度 `C(n,2)` 的 0/1 字串，每個位元代表一對頂點有沒有邊。這正是 Ch 0 窮舉器裡的 `mask`。

**例 2（補圖）**：n 點上有 m 條邊的圖，和有 `C(n,2) − m` 條邊的圖一樣多。雙射：G ↦ G 的補圖（有邊變沒邊、沒邊變有邊）。取兩次補圖回到原圖，所以它是自己的反函數，必為雙射。

**例 3（對合）**：n ≥ 2 時，邊數為偶數的標號圖和邊數為奇數的一樣多。雙射：「切換頂點 0 和 1 之間的邊」。它改變邊數的奇偶，而且做兩次回到原狀，所以是偶數邊圖和奇數邊圖之間的雙射。注意 n ≥ 2 是必要的：n = 1 時沒有 `{0, 1}` 這對頂點可以切換，唯一的空圖邊數是 0（偶數），兩邊不相等。

```python
"""例 2 與例 3 的窮舉驗證。"""
from itertools import combinations

for n in range(2, 7):
    pairs = list(combinations(range(n), 2))
    N = len(pairs)
    by_m = {}
    for mask in range(1 << N):
        m = bin(mask).count("1")
        by_m[m] = by_m.get(m, 0) + 1
    sym = all(by_m[m] == by_m[N - m] for m in by_m)
    even = sum(c for m, c in by_m.items() if m % 2 == 0)
    odd = sum(c for m, c in by_m.items() if m % 2 == 1)
    print(f"n={n}: 依邊數分布對稱={sym}, 偶數邊 {even} 張 / 奇數邊 {odd} 張")
```

輸出：

```text
n=2: 依邊數分布對稱=True, 偶數邊 1 張 / 奇數邊 1 張
n=3: 依邊數分布對稱=True, 偶數邊 4 張 / 奇數邊 4 張
n=4: 依邊數分布對稱=True, 偶數邊 32 張 / 奇數邊 32 張
n=5: 依邊數分布對稱=True, 偶數邊 512 張 / 奇數邊 512 張
n=6: 依邊數分布對稱=True, 偶數邊 16384 張 / 奇數邊 16384 張
```

雙射證明的價值是它通常**解釋了原因**，而不只是確認數字相等。[Ch 3](./03-counting-basics.md) 會大量使用它，[Ch 9](./09-trees-cayley-prufer.md) 的 Prüfer 編碼是全課最漂亮的雙射。

## 怎麼寫一個證明

把下面這份清單當成 code review checklist：

1. **先寫出精確的敘述**。「樹的邊數是 n − 1」不夠，要寫「每個有 n ≥ 1 個頂點的樹恰有 n − 1 條邊」。量詞（每個、存在）、範圍（n ≥ 1）、物件（簡單圖還是多重圖）都要明確。
2. **說明你用哪種技巧**。第一句話寫「對 n 歸納」「假設不然」「取一條最長路徑」，讀者就知道接下來的結構。
3. **給每個物件名字**。「取一條最長路徑 `P = v0 ... vk`」比「考慮最長的路徑」好，後面才能說「v0 的鄰居 w」。
4. **標出假設在哪裡用到**。如果某個假設從頭到尾沒用到，要嘛定理可以加強，要嘛你的證明有洞。定理 2.6 的「簡單圖」用在度數 ≤ n − 1；引理 2.3 的「無圈」用在排除 w 在 P 上的情況。
5. **分情況時確認情況窮盡**。定理 2.5 的三種情況（`v → p1`；`p1 → v` 且 `pn → v`；`p1 → v` 且 `v → pn`）涵蓋了所有可能，因為 tournament 中每對點恰有一個方向。
6. **「不失一般性」要說得出為什麼**。定理 2.7 的情況 2 能說「對稱」，是因為把圖換成補圖後情況 2 就變成情況 1。
7. **最後回頭檢查邊界**。n = 1、空圖、不連通的圖，證明的每一步在這些情況下還成立嗎？

## 踩雷集錦：常見的錯誤證明

1. **「蓋房子」式歸納（build-up error）**：錯誤命題「每個頂點度數 ≥ 1 的圖都連通」，錯誤證明：「n = 2 時兩點有邊，連通。假設 n 點成立，取一個 n 點的連通圖，加一個新點，新點度數 ≥ 1 所以連到某個舊點，新圖連通。」錯在哪？歸納步驟只處理了「由某個 n 點的好圖加一點得到」的 n + 1 點圖，**並不是所有** n + 1 點且每點度數 ≥ 1 的圖都長這樣。程式一下就找到反例：

   ```python
   import networkx as nx

   for G in nx.graph_atlas_g():
       n = G.number_of_nodes()
       if n >= 1 and min(d for _, d in G.degree()) >= 1 and not nx.is_connected(G):
           print("「每點度數 >= 1 則連通」的最小反例：n =", n, "edges =", list(G.edges()))
           break
   ```

   輸出：

   ```text
   「每點度數 >= 1 則連通」的最小反例：n = 4 edges = [(0, 1), (2, 3)]
   ```

   兩條不相交的邊，每點度數都是 1，但不連通。把它刪掉一點得到的 3 點圖有一個孤立點，不滿足假設——所以它不是從任何「n = 3 的好圖」蓋出來的。糾正：歸納步驟要**從任意的 n + 1 點物件出發，往下拆**，並確認拆完的東西仍滿足歸納假設的前提（定理 2.4 檢查了 T − v 仍是樹）。

2. **基底和步驟接不上（「所有馬同色」）**：「任意 n 匹馬同色。n = 1 成立。n + 1 匹馬時，前 n 匹同色、後 n 匹同色，兩組有重疊，所以全部同色。」n = 1 → 2 時前 1 匹和後 1 匹**沒有重疊**，步驟失效。糾正：寫完歸納步驟後，代入最小的那個 n 手動跑一次，確認每一句話在那裡都成立。

3. **偷用結論（循環論證）**：證明「樹有 n − 1 條邊」時寫「因為 T − v 有 n − 2 條邊……」而沒說明 T − v 是樹；或者驗證程式用 `sum(deg) // 2` 算邊數去驗證握手定理（Ch 0 動手練習 1）。糾正：每一步問自己「這個事實是從哪裡來的？」，答案不能是「從我要證的定理」。

4. **混淆逆命題**：證了「樹是連通且無圈的」就宣稱「連通且有 n − 1 條邊的圖是樹」。後者其實也成立，但需要**另外**證明（Ch 9 會證）。糾正：寫下 `P ⇒ Q` 和 `Q ⇒ P` 兩個方向，分開處理。

5. **鴿籠選錯盒子**：證定理 2.6 時寫「度數有 n 種可能、頂點有 n 個，所以有兩個相同」——n 個物件放 n 個盒子推不出任何東西。糾正：數清楚物件數和盒子數，物件必須**嚴格多於**盒子；不夠就像定理 2.6 那樣找額外限制壓縮盒子數。

6. **把「窮舉 n ≤ 7 都對」當證明**：窮舉只證明了 n ≤ 7 的情況。有些圖論猜想的反例非常大——例如 Tait 猜想（每個 3-連通、3-正則的平面圖都有 Hamilton 圈），Tutte 在 1946 年給出的第一個反例有 46 個頂點，後來 Holton 與 McKay 證明最小的反例也有 38 個頂點，遠超出任何暴力窮舉的範圍。糾正：窮舉是用來殺猜想和檢查證明的，不是用來取代證明的。

## 對比與取捨

| 技巧 | 什麼時候想到它 | 典型的圖論用法 | 常見陷阱 |
| --- | --- | --- | --- |
| 雙重計數 | 有兩種自然的分組方式 | 握手定理、算邊數 | 兩種計數其實數的不是同一個集合 |
| 歸納 | 問題對 n 有自然的遞迴結構 | 刪一片葉子、刪一條邊 | 蓋房子式步驟、基底接不上 |
| 強歸納 | 拆出的子問題大小不定 | 切一條邊成兩棵樹、分治 | 忘了確認每個子問題都嚴格變小 |
| 反證 | 結論是「不存在」或「必然」 | 不存在 7 點 3-正則圖 | 推出的「矛盾」其實不是矛盾 |
| 鴿籠 | 物件比類別多 | 兩點同度數、Ramsey | 盒子沒選好 |
| 極端原理 | 需要一個「好」的出發點 | 最長路徑、最大切割 | 沒確認極端物件存在（無限圖可能沒有最長路徑） |
| 雙射 | 兩個集合看起來一樣大 | 補圖、Prüfer 編碼 | 沒驗證映成或定義良好 |

反證和逆否常被混用。證明 `P ⇒ Q` 的逆否寫法是「假設 ¬Q，推出 ¬P」；反證寫法是「假設 P 且 ¬Q，推出任何矛盾」。後者更有彈性，但也更容易寫出一長串推論後忘了矛盾到底在哪。能用逆否就用逆否，結構比較清楚。

## 進階：再往深一層

**良序原理（well-ordering principle）**：每個非空的自然數集合都有最小元素。歸納法、強歸納、「取最小反例」三者在自然數上等價。極端原理在有限圖上靠的就是它（或「有限集合有最大元素」）。在**無限圖**上，最長路徑可能不存在，引理 2.3 這類論證要改用 Zorn 引理或 König 引理，Diestel 第 8 章處理這些情況。

**證明的形式化**：Lean、Coq、Isabelle 這類 proof assistant 讓電腦逐步檢查證明。四色定理在 2005 年由 Gonthier 等人在 Coq 中完整形式驗證過（Ch 19 會談）。對工程師來說，同一套想法出現在 Dafny、Why3 這類程式驗證工具裡：迴圈不變式就是歸納假設。

**面試常見問法**：「證明 Kruskal 演算法正確」「證明 BFS 給出最短路徑」——標準答案都是本章的技巧：前者是 cut property 加歸納（Ch 10），後者是對距離做歸納。

## 動手練習

1. 用極端原理證明：每個連通圖都有生成樹（提示：取**邊數最少**的連通生成子圖，證明它無圈）。然後用窮舉檢查你的證明在 n ≤ 6 的連通圖上的每個步驟。
2. 修改 `insert_path`，把第三種情況改成「插在第一個 `beats(v, path[i])` 的位置之前」但不檢查 `beats(path[i-1], v)`，跑窮舉看哪個 n 開始失敗，並解釋證明的哪一句被你拿掉了。
3. 找出這個「證明」的錯誤：「命題：每個 n 點、n 條邊的圖都恰有一個圈。對 n 歸納，n = 3 的三角形成立。加一個新點和一條新邊，新邊不會造成新的圈，所以仍恰有一個圈。」然後用 atlas 找最小反例。
4. 把定理 2.9 的證明寫成 local search 程式，在 `nx.gnp_random_graph(40, 0.3, seed=1)` 上執行，印出移動次數與最終切割大小，確認 `c ≥ |E| / 2`。

## 本章重點整理

- 證明是沒有執行環境的程式：每一步都要能被檢查，每個假設都要用到。
- 雙重計數、歸納、強歸納、反證、鴿籠、極端原理、雙射，是後面 40 章反覆出現的七把工具。
- 圖論歸納要「從任意大物件往下拆」，不要「從小物件往上蓋」。
- 很多證明是構造性的，可以直接翻成演算法（tournament 插入法、max cut local search）。
- 窮舉能推翻全稱命題，不能證明它；但它能檢查你的證明在小情況下有沒有寫錯。

## 自我檢核

- [ ] 不看書，完整寫出「n 點的樹有 n − 1 條邊」的歸納證明，包含「T − v 仍然連通」那一步的理由。
- [ ] 解釋為什麼「蓋房子」式歸納是錯的，並舉出本章以外的一個例子。
- [ ] 不翻書，說出定理 2.6 的證明裡「壓縮盒子數」的關鍵觀察。
- [ ] 如果有人在 code review 時說「我的 taint 傳播規則在所有測試都對，所以它是 sound 的」，你會要求他補什麼？用本章的哪個技巧？
- [ ] 選一個你熟悉的演算法（例如 BFS 求最短距離），說出它的正確性證明用的是哪種技巧，歸納的對象是什麼。

## 延伸閱讀

### 書籍

- **《How to Prove It: A Structured Approach》** — Daniel J. Velleman（Cambridge University Press，第 3 版，2019）
  - **這本書的定位**：專門教「怎麼寫證明」的入門書，從邏輯、量詞一路講到歸納法，大量練習。
  - **讀哪幾章**：Chapter 3（Proofs）與 Chapter 6（Mathematical Induction）。如果本章的邏輯語彙表讓你覺得吃力，先讀 Chapter 1–2。
  - **前提知識**：無，給從沒寫過證明的讀者。
- **[Mathematics for Computer Science](https://ocw.mit.edu/courses/6-042j-mathematics-for-computer-science-spring-2015/pages/readings/)** — Eric Lehman、F. Thomson Leighton、Albert R. Meyer（MIT 6.042J 教科書；OCW Spring 2015 課程頁的 Readings 提供全書 PDF）
  - **這本書的定位**：MIT 給 CS 學生的離散數學教材，免費 PDF，例子全部偏 CS。
  - **讀哪幾章**：Unit 1（Proofs）裡的「What is a Proof?」「The Well Ordering Principle」「Induction」三章，以及 Unit 2 的「Simple Graphs」一章。版本間章節編號會變，以章名查找。
  - **前提知識**：無。
- **《Proofs from THE BOOK》** — Martin Aigner、Günter M. Ziegler（Springer，第 6 版，2018）
  - **這本書的定位**：收集「最漂亮的證明」，示範一個好證明可以多短、多清楚。
  - **讀哪幾章**：組合與圖論部分，例如 Cayley 公式的幾種證明、鴿籠與雙重計數一章。讀完 Part 2 後回來看效果最好。
  - **前提知識**：本章內容；部分章節需要線性代數。
- **《Introduction to Graph Theory》** — Douglas B. West（Prentice Hall，第 2 版，2001）
  - **讀哪幾章**：Section 1.3（Vertex Degrees and Counting）有握手定理、build-up error 的討論與 extremal 論證；Section 2.1 是樹的等價定義（本章定理 2.4 的完整脈絡）。
  - **為什麼值得讀**：West 特別強調「歸納步驟要從任意大物件出發」，並給了多個 build-up 錯誤的例子，和本章踩雷 1 直接對應。

有了證明工具，下一章把其中的雙重計數與雙射延伸成一整套計數技術——排列組合、二項式係數，以及怎麼讓程式替每個公式背書。

→ [Ch 3 計數基礎：排列組合、二項式、雙重計數](./03-counting-basics.md)
