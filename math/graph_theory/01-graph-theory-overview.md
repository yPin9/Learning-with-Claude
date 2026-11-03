# Ch 1 — 圖論全貌：從柯尼斯堡七橋出發

> **目標**：從 Euler 的七橋問題看懂「把問題抽象成圖」這個動作，知道圖論能回答哪幾類問題、這門課的 42 章怎麼串起來，以及每個概念在這個 repo 的哪門課會再遇到。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。本章的程式只是預告，演算法本身在後面章節自刻。

## 為什麼需要這個？

你大概已經在用圖，只是不叫它圖論：

- 編譯器課講 SSA 時，你算過 dominator tree，也畫過 CFG。
- 資安課裡，BloodHound 幫你找「從低權限帳號到 Domain Admin 的最短路徑」。
- 逆向時，BinDiff 這類工具用函式的 CFG 結構比對兩個版本的 binary。

這些工具各自教了「怎麼用」，但沒有人告訴你它們底下共用同一套數學。這帶來兩個實際問題。第一，遇到新場景時你不會建模：你不知道「找到最少要修補幾台機器才能切斷所有攻擊路徑」其實是一個最小割問題，有多項式時間的精確解。第二，你不知道哪些問題沒有好解：「找一條經過每個基本塊恰一次的執行路徑」是 Hamilton 路徑，NP-complete，不要浪費時間找快速精確演算法。

圖論的起點正好示範了這兩件事：一個看似要窮舉的問題，被一個結構性的觀察一刀解決。

## 先建立直覺

1736 年的柯尼斯堡（今俄羅斯加里寧格勒），普列戈利亞河把城市分成四塊陸地，七座橋連接它們。居民問：能不能散步一次，每座橋**恰好**走過一次？

```text
           北岸 B
     ======================
       |橋1    |橋2       \橋6
    ~~~~~~~~~~~~~~~~~~~~   \
    ~~   島 A        ~~ 橋5 ===  東 D
    ~~~~~~~~~~~~~~~~~~~~   /
       |橋3    |橋4       /橋7
     ======================
           南岸 C
```

Euler 的第一個動作不是去走，而是**丟掉不重要的資訊**：陸地的大小、形狀、橋的長度、你在陸地上怎麼走，都和答案無關。只剩下「哪塊陸地和哪塊陸地之間有幾座橋」：

```text
        B
       /||\
      / || \           A: 5 座橋
     (  ||  )          B: 3 座橋
      \ || /  \        C: 3 座橋
        A ---- D       D: 3 座橋
      / || \  /
     (  ||  )
      \ || /
        C
```

這就是一張**圖（graph）**：點（vertex，又叫節點 node）代表陸地，邊（edge）代表橋。因為 A 和 B 之間有兩座橋，同一對點之間有多條邊，這種圖叫**多重圖（multigraph）**。

> 如果你對 NetworkX 的 `Graph` 和 `MultiGraph` 差別還不熟，先回看 [Ch 0](./00-environment-setup.md) 的「踩雷集錦」坑 4。

## Euler 的論證

先讓電腦把所有可能的路線試一遍，再看 Euler 怎麼不試就知道答案。

```python
"""柯尼斯堡七橋：暴力搜尋所有「每座橋恰走一次」的路線，再對照 Euler 的度數判準。"""
import networkx as nx

# A = Kneiphof 島, B = 北岸, C = 南岸, D = 東邊陸地
BRIDGES = [("A", "B"), ("A", "B"), ("A", "C"), ("A", "C"),
           ("A", "D"), ("B", "D"), ("C", "D")]


def count_trails(bridges):
    """DFS 列舉所有使用每座橋恰一次的走法（可從任意陸地出發）。"""
    used = [False] * len(bridges)
    found = []
    start_of = [None]

    def dfs(at, path):
        if len(path) == len(bridges):
            found.append((start_of[0], path[:]))
            return
        for i, (u, v) in enumerate(bridges):
            if not used[i] and at in (u, v):
                used[i] = True
                path.append(i)
                dfs(v if at == u else u, path)
                path.pop()
                used[i] = False

    for start in sorted({x for e in bridges for x in e}):
        start_of[0] = start
        dfs(start, [])
    return found


def degrees(bridges):
    deg = {}
    for u, v in bridges:
        deg[u] = deg.get(u, 0) + 1
        deg[v] = deg.get(v, 0) + 1
    return dict(sorted(deg.items()))


print("七橋度數:", degrees(BRIDGES))
print("七橋的一筆畫走法數:", len(count_trails(BRIDGES)))

M = nx.MultiGraph(BRIDGES)
print("NetworkX MultiGraph: edges =", M.number_of_edges(),
      " has_eulerian_path =", nx.has_eulerian_path(M))
print("誤用 nx.Graph: edges =", nx.Graph(BRIDGES).number_of_edges(),
      " has_eulerian_path =", nx.has_eulerian_path(nx.Graph(BRIDGES)))

EIGHT = BRIDGES + [("B", "C")]
print("加一座 B-C 橋後度數:", degrees(EIGHT))
trails = count_trails(EIGHT)
print("八橋的一筆畫走法數:", len(trails))
start, order = trails[0]
walk = [start]
for i in order:
    u, v = EIGHT[i]
    walk.append(v if walk[-1] == u else u)
print("其中一條:", "-".join(walk))
print("起點分布:", sorted({s for s, _ in trails}))
print("NetworkX:", nx.has_eulerian_path(nx.MultiGraph(EIGHT)),
      " is_eulerian:", nx.is_eulerian(nx.MultiGraph(EIGHT)))
```

輸出：

```text
七橋度數: {'A': 5, 'B': 3, 'C': 3, 'D': 3}
七橋的一筆畫走法數: 0
NetworkX MultiGraph: edges = 7  has_eulerian_path = False
誤用 nx.Graph: edges = 5  has_eulerian_path = True
加一座 B-C 橋後度數: {'A': 5, 'B': 4, 'C': 4, 'D': 3}
八橋的一筆畫走法數: 416
其中一條: A-B-A-C-A-D-B-C-D
起點分布: ['A', 'D']
NetworkX: True  is_eulerian: False
```

暴力搜尋說：七橋沒有任何走法。加一座 B–C 橋之後有 416 種（同一對陸地間的兩座橋算不同的橋，正向反向算不同走法），而且**只能**從 A 或 D 出發。為什麼偏偏是 A 和 D？

### 度數論證

一個點的**度數（degree）**是碰到它的邊數。假設存在一條走法，每座橋恰走一次，起點 s、終點 t。看任何一個**不是**起點也不是終點的陸地 v：每次走進 v 用掉一座橋，接著一定要走出去，又用掉一座橋。進出成對，所以碰到 v 的橋數是偶數。起點多一次「走出去」沒有配對的「走進來」，終點多一次「走進來」，所以：

- 若 s ≠ t：s 和 t 的度數是奇數，其他點都是偶數。
- 若 s = t（走回原點）：所有點的度數都是偶數。

不論哪種情況，**度數為奇數的點最多兩個**。七橋有四個奇點（A、B、C、D 度數 5、3、3、3），所以不存在這樣的走法，連一條都不用試。

加了 B–C 橋後奇點剩 A 和 D，論證告訴我們起終點只能是 A 和 D——和暴力搜尋的「起點分布」完全吻合。

這個論證只證明了**必要條件**：奇點超過兩個就一定不行。反方向——「連通且奇點最多兩個就一定可以」——Euler 在 1736 年的論文裡宣稱了，但沒有給出嚴格證明。第一個完整證明來自 Carl Hierholzer，他在 1871 年過世，證明由同事整理後於 1873 年發表在 *Mathematische Annalen*。我們會在 [Ch 8](./08-euler-hamilton.md) 把兩個方向都證完，並實作 Hierholzer 演算法。

### 一個失敗的抽象

注意輸出的第四行：用 `nx.Graph` 建七橋，NetworkX 回答「有一筆畫」。因為 `Graph` 把 A–B 的兩座橋合併成一條、A–C 也合併，剩下 5 條邊，度數變成 A:3、B:2、C:2、D:3，奇點只剩兩個。

抽象化是丟掉資訊，但**丟錯了資訊答案就錯**。橋的數量正是這個問題的核心，用簡單圖建模就把問題換成另一個問題。這種錯誤在真實系統裡一樣常見：兩個基本塊之間的 true 分支和 false 分支同時跳到同一個目標時，CFG 上會有兩條平行邊，用簡單圖存會讓某些分析（例如計算 edge profile）出錯。

## 圖論能回答哪些問題

七橋是一個**存在性**問題（有沒有一筆畫）。圖論的問題大致可以分成五類，這門課每一章都落在其中一兩類：

| 問題類型 | 典型提問 | 例子 | 本課章節 |
| --- | --- | --- | --- |
| 存在性 | 有沒有某種結構？ | 有沒有 Euler 迴路、完美匹配、平面嵌入 | Ch 8、18、22、23 |
| 計數 | 有幾個？ | n 個點有幾棵標號樹、幾張無標號圖 | Ch 3–6、9、28 |
| 最佳化 | 最大／最小是多少？ | 最小生成樹、最大流、最少顏色數 | Ch 10、15、20–26 |
| 結構刻畫 | 什麼條件等價於某性質？ | 二分圖 ⟺ 沒有奇圈；平面圖 ⟺ 不含 K5、K3,3 的細分 | Ch 7、12、16、18 |
| 計算複雜度 | 能不能有效率地算？ | Euler 路徑線性時間，Hamilton 路徑 NP-complete | Ch 8、34–37 |

第五類值得多說一句。Euler 路徑（每條**邊**走一次）和 Hamilton 路徑（每個**點**走一次）聽起來幾乎一樣，前者有 O(|V| + |E|) 的演算法，後者至今沒有已知的多項式演算法，並且是 NP-complete。這種「看起來差一個字、難度天差地遠」的現象貫穿全課；學會分辨它，比背任何一個演算法都有用。

## 歷史演進：從散步問題到系統工具

圖論沒有一開始就是一門學科，而是好幾條獨立的線在二十世紀匯流：

```text
1736  Euler：七橋問題（論文 1741 年刊出）           存在性、度數
1847  Kirchhoff：電路網路與生成樹                   → Ch 28 矩陣樹定理
1852  Guthrie 提出四色猜想                          → Ch 19
1857  Cayley：開始研究樹的計數（後來用於化學異構物） → Ch 9、Ch 6
1873  Hierholzer：Euler 迴路充分性的證明             → Ch 8
1936  König：第一本圖論教科書                        匹配、二分圖 → Ch 22
1956  Ford–Fulkerson：最大流最小割                   → Ch 20
1965  Edmonds：Blossom 演算法、「多項式時間＝有效率」  → Ch 23
1971  Cook：NP-completeness                           → Ch 34
1979  Lengauer–Tarjan：近線性 dominator 演算法        → Ch 14
1982  Chaitin：暫存器分配＝圖著色                      → Ch 17
2014  Yamaguchi 等：Code Property Graph 找漏洞         → Ch 38
```

讀這條時間線時注意兩件事。一是前半段幾乎都是數學家為了別的問題（電路、化學、地圖）發明工具；二是後半段電腦出現後，「能不能有效率地算」才變成核心問題。這門課的編排也是這個順序：先學結構與證明，再學演算法，最後學複雜度與應用。

## 全課地圖

```text
Part 1 證明與計數地基（Ch 0–6）
   證明技巧、計數、生成函數、Burnside
        │  所有後續證明都用這裡的工具
        ▼
Part 2 圖的結構（Ch 7–14）────────────────┐
   定義、Euler/Hamilton、樹、MST、連通度、   │
   Menger、SCC/DAG、dominator tree          │
        │                                   │
   ┌────┴─────────┐                         │
   ▼              ▼                         │
Part 3 著色與平面  Part 4 Flow/Matching/     │
(Ch 15–19)        Matroid（Ch 20–26）        │
 register alloc    最大流最小割、LP 對偶     │
   │              │                         │
   └────┬─────────┘                         │
        ▼                                   │
Part 5 代數、機率、極值（Ch 27–33）          │
   譜方法、隨機遊走、機率方法、Ramsey         │
        │                                   │
        ▼                                   │
Part 6 計算複雜度（Ch 34–37）               │
   NP-complete、SAT 歸約、treewidth、WL      │
        │                                   │
        ▼                                   ▼
Part 7 系統與資安應用（Ch 38–41） + Final Project graphlab
   CPG、攻擊圖、binary 相似度、大圖工程
```

每個 Part 的角色：

- **Part 1** 是工具箱。它看起來不像圖論，但後面每一個「證明某演算法正確」都會用到歸納、反證、雙重計數。
- **Part 2** 是核心。樹、連通度、有向圖、dominator 是系統程式最常碰到的結構。
- **Part 3** 回答「什麼時候能用 k 種顏色著色」，直接對應暫存器分配。
- **Part 4** 是組合最佳化的主幹：flow、matching、matroid、LP 對偶，回答「為什麼貪婪法有時對有時錯」。
- **Part 5** 用線性代數和機率看圖，解釋 PageRank、expander、隨機圖的相變。
- **Part 6** 告訴你哪些問題沒有快速精確解，以及怎麼繞過去（歸約到 SAT、利用 treewidth）。
- **Part 7** 把前面全部接回真實工具。

## 這些概念在 repo 其他課的哪裡出現

這門課最大的價值是「補齊你在別門課用過但沒證過的東西」。下表列出主要的交會點，每一列在對應章節都會再展開：

| 圖論概念 | 本課章節 | 在哪門課用過 | 那邊怎麼用 |
| --- | --- | --- | --- |
| Dominator、dominator tree | Ch 14 | [SSA 最佳化 Ch 2 支配關係](../../compilers/ssa_optimizations/02-dominance.md)、[Ch 3 dominator tree 演算法](../../compilers/ssa_optimizations/03-dominator-tree-algorithm.md) | φ 函式插在 dominance frontier；LICM 需要知道哪個 block 支配迴圈出口 |
| SCC、DAG、拓撲排序 | Ch 13 | [SSA 最佳化 Ch 27 Call Graph 與 SCC](../../compilers/ssa_optimizations/27-call-graph.md) | 依 SCC 的逆拓撲序處理函式，做 inlining 與跨程序分析 |
| 圖著色、chordal graph | Ch 15–17 | [編譯器後端 Ch 12 Register allocator](../../compilers/compiler_backend/12-register-allocator.md) | interference graph 著色；SSA 形式下的 interference graph 是 chordal |
| 圖可達性 | Ch 13、38 | [程式碼審計 Ch 5 IFDS/IDE](../../security/code_auditing/05-ifds-ide.md) | 把 taint 分析化成 exploded supergraph 上的可達性 |
| CPG（AST + CFG + PDG） | Ch 38 | [程式碼審計 Ch 3 程式表示：CPG](../../security/code_auditing/03-program-representations-cpg.md) | Joern、CodeQL 在合成圖上查詢 source 到 sink 的路徑 |
| 最短路徑、最小割 | Ch 20、39 | [OSCP Ch 33 BloodHound](../../security/oscp_prep/33-bloodhound.md) | 「Find Shortest Paths to Domain Admins」；防守方要的是切斷所有路徑的最小修補集合 |
| 圖相似度、同構 | Ch 37、40 | [逆向工程 Ch 28 二進位相似度](../../security/reverse_engineering/28-binary-similarity-fingerprinting.md) | 以 CFG 與 call graph 結構比對函式，做 patch diff 與函式指紋 |
| NP-complete 歸約到 SAT | Ch 34–35 | [SAT/SMT 課](../../programming/sat_smt/README.md) | 把著色、Hamilton 路徑編碼成 CNF 交給 solver |
| 偏序、happens-before DAG | Ch 13 | [分散式系統 Ch 6 Vector Clock](../../systems/distributed_systems/06-vector-clocks.md) | 事件之間的因果關係是一個 DAG，vector clock 判斷兩事件可不可比 |
| CSR、平行 BFS | Ch 41 | [GPU/CUDA 課](../../parallel/gpu_cuda/README.md) | 稀疏圖的記憶體布局決定 cache 與 coalescing 行為 |

### 三個場景的預告

下面這段用 NetworkX 現成函式跑三個場景，讓你先看到「同一個資料結構、三種問題」。這裡**不**解釋演算法，對應章節會自刻並證明。

```python
"""全課預告：同一個 NetworkX，三種系統場景。演算法本身在後面章節自刻。"""
import networkx as nx

# 1) 編譯器：CFG 的 dominator（Ch 14）
cfg = nx.DiGraph([("entry", "cond"), ("cond", "then"), ("cond", "else"),
                  ("then", "join"), ("else", "join"), ("join", "cond"),
                  ("join", "exit")])
idom = nx.immediate_dominators(cfg, "entry")
print("idom:", {b: idom[b] for b in sorted(idom)})

# 2) 暫存器分配：interference graph 著色（Ch 15, 17）
interf = nx.Graph([("a", "b"), ("a", "c"), ("b", "c"), ("c", "d"), ("d", "e")])
color = nx.greedy_color(interf, strategy="largest_first")
print("colors used:", max(color.values()) + 1, color)

# 3) 資安：攻擊圖的最短路徑與最小切斷集（Ch 20, 39）
atk = nx.DiGraph([("phish", "ws01"), ("ws01", "svc_sql"), ("ws01", "helpdesk"),
                  ("helpdesk", "srv02"), ("svc_sql", "srv02"),
                  ("srv02", "DA"), ("helpdesk", "DA")])
print("shortest:", nx.shortest_path(atk, "phish", "DA"))
print("min cut nodes:", sorted(nx.minimum_node_cut(atk, "phish", "DA")))
```

輸出：

```text
idom: {'cond': 'entry', 'else': 'cond', 'exit': 'join', 'join': 'cond', 'then': 'cond'}
colors used: 3 {'c': 0, 'a': 1, 'b': 2, 'd': 1, 'e': 0}
shortest: ['phish', 'ws01', 'helpdesk', 'DA']
min cut nodes: ['ws01']
```

逐行讀：

- **dominator**：`join` 的 immediate dominator 是 `cond` 而不是 `then` 或 `else`，因為從 `entry` 到 `join` 有兩條路，只有 `cond` 是必經之地。迴圈的回邊 `join → cond` 不改變這件事。NetworkX 3.7 的回傳字典**不包含起點本身**（文件明寫 "except for `start` itself"）；就我所知較舊版本會放 `entry: entry`，跨版本時以實測為準。
- **著色**：a、b、c 兩兩相連（三角形），至少要 3 種顏色，貪婪法剛好用了 3 種。但貪婪法不保證最少，Ch 15 會給它失敗的例子。
- **攻擊圖**：攻擊者的最短路徑經過 `helpdesk`，但防守方真正要的是「拿掉哪些節點能切斷**所有**路徑」——答案是 `ws01` 一台。只修 `helpdesk` 不夠，攻擊者會改走 `svc_sql → srv02 → DA`。從最短路徑思維換到最小割思維，是 Ch 39 的主題。

## 對比：圖論課 vs 刷題的圖

| 面向 | 刷題（leetcode_patterns） | 這門課 |
| --- | --- | --- |
| 問題 | 給定題目，套 BFS/DFS/Dijkstra 模板 | 先問「這張圖有什麼結構」，再決定方法 |
| 正確性 | 跑過測資就算對 | 要能證明對，並說出失效條件 |
| 困難問題 | 很少碰 | 明確知道哪些是 NP-hard，如何近似或歸約 |
| 計數 | 多半是 DP 加取模 | 生成函數、Burnside，給出封閉公式並和窮舉對答案 |
| 應用 | 面試 | 編譯器、程式分析、攻擊圖、binary 比對 |

這不是說刷題沒用。如果你對 BFS、拓撲排序、union-find 的實作還不熟，[algorithms/leetcode_patterns](../../algorithms/leetcode_patterns/README.md) 的圖論章節是更好的起點；這門課假設你已經會寫它們，重點放在「為什麼對」和「還能用在哪」。

## 踩雷集錦

1. **以為圖就是「點加邊」的資料結構**：圖論真正研究的是**結構性質**——連通、可著色、可平面嵌入。資料結構只是存放方式。同一張 CFG 可以存成鄰接字典或矩陣，dominator 關係不會變。
2. **建模時用錯圖的種類**：七橋用簡單圖建模會得到「可以一筆畫」的錯誤答案。建模前先問：方向重不重要？平行邊有沒有意義？自環呢？權重呢？每一個答錯，後面的演算法再正確都沒用。
3. **把 Euler 和 Hamilton 混為一談**：「每條邊一次」可以線性時間判定，「每個點一次」是 NP-complete。看到「遍歷所有 X 恰一次」先確認 X 是邊還是點。
4. **把窮舉沒找到反例當成證明**：七橋的暴力搜尋說「0 種走法」，對這一張圖是證明（窮舉了所有可能）；但「奇點超過兩個的圖都沒有一筆畫」是對無限多張圖的宣稱，需要 Euler 的度數論證。有限窮舉只對有限情況是證明。
5. **以為度數條件是充要的**：「奇點 ≤ 2」是必要條件，不是充分條件。兩個分開的三角形，每個點度數都是 2，卻沒有一筆畫，因為圖不連通。Ch 8 的完整定理需要加上連通性（嚴格說是「所有有邊的點在同一個連通元件」）。

## 進階：再往深一層

**中國郵差問題（Chinese Postman Problem）**：如果圖沒有 Euler 迴路，最少要重複走哪些邊才能回到原點並走過每條邊？中國數學家管梅谷（Mei-Ko Kwan）在 1960 年以中文發表這個問題（英譯 1962 年），Edmonds 與 Johnson 在 1973 年用最小權完美匹配給出多項式時間解。它把 Ch 1 的存在性問題變成最佳化問題，並用到 Ch 23 的一般圖匹配。真實應用是郵差、掃街車、以及測試時「覆蓋每條 CFG 邊」的測試序列產生。

**建模是最難的一步**：教科書的問題都已經是圖，真實問題不是。攻擊圖要決定「節點是主機、帳號、還是（主機, 權限）組合」，不同選擇會讓同一個問題從多項式時間變成 NP-hard。Ch 39 會專門處理這件事。

**圖論的兩種風格**：一種是結構圖論（Diestel 的書偏這邊），關心「什麼條件下某結構存在」；另一種是演算法圖論，關心「怎麼有效率地找到它」。兩者交會的地方最有意思，例如 Menger 定理（結構）和最大流演算法（演算法）其實是同一件事的兩面，Ch 12 和 Ch 20 會讓你看到。

## 動手練習

1. 改 `BRIDGES`，拆掉其中一座橋（任選），讓七橋變成可以一筆畫的六橋。先用度數論證預測起終點，再跑 `count_trails` 驗證。
2. 想像城市再蓋兩座橋，使得可以**走回出發點**（Euler 迴路）。最少要加幾座？加在哪？用程式確認。
3. 用 `nx.Graph` 而不是 `MultiGraph` 建七橋，畫出度數，說明這個錯誤模型對應的是哪個真實城市（提示：每對陸地之間最多一座橋）。
4. 在預告程式的攻擊圖上加一條邊 `phish → svc_sql`，再跑一次 `minimum_node_cut`。結果變成什麼？為什麼只修 `ws01` 不夠了？

## 本章重點整理

- 建模的本質是丟掉無關資訊；丟錯了（例如把多重圖當簡單圖）答案就錯。
- Euler 的度數論證：一筆畫要求奇點最多兩個。這是必要條件；充分性要等到 Hierholzer 才證完。
- 圖論問題分五類：存在、計數、最佳化、結構刻畫、計算複雜度。
- Euler 路徑容易、Hamilton 路徑困難：一字之差，難度天差地遠。
- 這門課的概念在 SSA、register allocation、CPG、BloodHound、binary diff 裡都已經出現過，這裡補上它們的證明與演算法。

## 自我檢核

- [ ] 不看書，用「進出成對」的論證向同事解釋為什麼七橋不能一筆畫。
- [ ] 說出為什麼用 `nx.Graph` 建七橋會得到錯的答案，並舉一個真實系統裡同樣犯錯的建模場景。
- [ ] 有人說「BloodHound 已經給了最短攻擊路徑，修掉路徑上的一台就安全了」，你會用哪個圖論概念反駁？
- [ ] 想一個你最近工作上碰到的問題，把它分類到「存在、計數、最佳化、結構、複雜度」其中一類，並說出點和邊分別代表什麼。

## 延伸閱讀

### 原始文獻

- **[Solutio problematis ad geometriam situs pertinentis（Euler Archive E53）](https://scholarlycommons.pacific.edu/euler-works/53/)** — Leonhard Euler，*Commentarii academiae scientiarum Petropolitanae* 8，pp. 128–140（1741 年刊出；Euler Archive 記為 1735 年寫成）
  - **核心貢獻**：圖論的起點論文。Euler 把七橋抽象成區域與橋的計數，提出奇點數的判準。
  - **讀哪裡**：原文是拉丁文；Euler Archive 頁面除了原文掃描，另附一個英譯 PDF（E053tr.pdf）。看第 1–9 段（問題敘述與「用字母記錄路線」的想法）和最後的結論段。
  - **和本章的關聯**：你會發現 Euler 並沒有用「圖」或「度數」這些詞，他記錄的是「路線中某個字母出現幾次」。現代的圖抽象是後人整理出來的。
- **[The Truth about Königsberg](https://www.maa.org/node/113964)** — Brian Hopkins、Robin J. Wilson，*The College Mathematics Journal* 35(3)（2004）
  - **核心貢獻**：考據 Euler 論文實際證明了什麼、沒證明什麼，澄清「Euler 發明了圖論」這個說法的幾個誤解。
  - **讀哪裡**：全文約 10 頁，可讀完。重點是 Euler 對充分性的處理那一段。
  - **為什麼值得讀**：本章「Euler 只證了必要條件」這個說法的來源之一；這篇得過 MAA 的 Pólya 獎。

### 書籍

- **《Graph Theory 1736–1936》** — Norman L. Biggs、E. Keith Lloyd、Robin J. Wilson（Clarendon Press，Oxford，1976）
  - **這本書的定位**：圖論前兩百年的原始文獻選集，每篇附英文翻譯與評註。
  - **讀哪幾章**：第 1 章（Euler 與 Hierholzer 的論文英譯）。之後想知道某個定理的出處時再翻對應章節；年代久，但它收錄的是原始文獻，不會過時。
  - **前提知識**：無。
- **《Introduction to Graph Theory》** — Douglas B. West（Prentice Hall，第 2 版，2001）
  - **這本書的定位**：本課主教科書。
  - **讀哪幾章**：Section 1.1（What is a Graph?）以 Königsberg 開場並建立術語；Section 1.2 有 Euler 迴路定理的完整證明。
  - **前提知識**：無，Section 1.1 從零開始。
- **《Graph Theory》** — Reinhard Diestel（Springer GTM 173）
  - **這本書的定位**：比 West 精煉、偏結構圖論；作者網站提供電子版可線上閱讀。
  - **讀哪幾章**：Chapter 1 讀一遍當術語表；Section 1.8 有 Euler 迴路定理的簡潔證明。
  - **前提知識**：習慣簡潔的數學寫法；如果讀不動，先讀 West。

### 論文

- **[Euler, Mei-Ko Kwan, Königsberg, and a Chinese Postman](https://opus4.kobv.de/opus4-zib/frontdoor/index/index/docId/4331)** — Martin Grötschel、Ya-xiang Yuan，*Documenta Mathematica* Extra Volume《Optimization Stories》（ISMP 2012），pp. 43–50
  - **核心貢獻**：一篇 8 頁的短文，從七橋講到 Euler 迴路與它的最佳化版本——管梅谷的中國郵差問題。
  - **讀哪裡**：全文。
  - **前提知識**：本章內容即可。
  - **和本章的關聯**：本章「進階」一節中國郵差問題與 Hierholzer 1873 年論文的來龍去脈。

Euler 的論證看起來簡單，但「進出成對」背後是一個需要嚴格寫出來的證明。下一章把這類論證的工具一次備齊：歸納、反證、鴿籠、極端原理、雙射。

→ [Ch 2 證明工具箱：歸納、反證、鴿籠、極端原理](./02-proof-toolbox.md)
