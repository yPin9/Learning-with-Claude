# Ch 0 — 環境搭建

> **目標**：建好一個釘死版本的 Python 環境，並學會全課反覆使用的兩個工作流——「自刻演算法 + NetworkX 對拍」與「小圖暴力窮舉驗證定理」。讀完這章，你寫下的每個定理都能在幾秒內被電腦檢查一遍。
>
> **環境**：Python 3.13.12、NetworkX 3.7、NumPy 2.5.4、SciPy 1.18.1、matplotlib 3.11.2，Windows 11（AMD Ryzen 7 5700G）。Linux / macOS 指令另外列出；本章所有輸出都在上述 Windows 環境實跑。

## 為什麼需要這個？

圖論課最常見的失敗模式有兩種。第一種是「只讀證明」：讀起來每一步都通，合上書卻不知道定理在說什麼，更不知道哪個條件拿掉就會壞。第二種是「只寫程式」：BFS 寫得很熟，但說不出它為什麼對，碰到變形題就只能猜。

這門課的解法是讓證明和程式互相檢查：

- **程式檢查證明**：每個定理都能在小圖上窮舉驗證。如果你的「證明」推出的結論在 n = 5 的某張圖上不成立，證明一定有洞。
- **證明檢查程式**：每個自刻演算法都要和 NetworkX 對拍。對拍通過不代表正確（測試只能證明有 bug，不能證明沒 bug），所以還要有證明。

在電腦普及以前，數學家驗證小例子靠手算，例如 Read 與 Wilson 的《An Atlas of Graphs》把所有 7 點以內的圖一張張畫出來。今天這本圖鑑就躺在 NetworkX 裡，一行就能拿到，這是我們這一代學圖論最大的優勢。

## 先建立直覺

把這門課的每一章想成一個三角形：

```text
                 定理 / 演算法的「宣稱」
                        /\
                       /  \
        證明（為什麼對）     程式（在小圖上真的對嗎）
                     /      \
                    /________\
                  NetworkX 參考答案 / 窮舉結果
```

證明給你「對所有圖都成立」的保證，窮舉給你「在所有小圖上確實成立」的證據，NetworkX 給你「別人寫的、被很多人用過的版本」當對照。三邊任何一邊和另外兩邊對不上，就是你該停下來想的地方。

## 建 venv 與釘版本安裝

不要用系統 Python 直接 `pip install`。版本不同時，`nx.graph_atlas_g()` 的回傳型別、`spring_layout` 的座標、甚至某些函式名稱都會變，你會在「為什麼我的輸出和書上不一樣」這種問題上浪費時間。

Windows（PowerShell）：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2
```

Linux / macOS：

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2
```

Part 6 的 SAT 歸約與 Part 7 的 binary 分析章節會另外用到：

```bash
pip install z3-solver==5.3.3.0 capstone==5.0.9 pyelftools==0.33
```

**為什麼要 SciPy？** NetworkX 本身是純 Python，但 `laplacian_matrix`、`adjacency_matrix` 這類函式回傳 SciPy sparse 矩陣，Part 5 的譜方法會大量使用。沒裝 SciPy 時，呼叫這些函式會丟 `ImportError`，不會在 import networkx 時就提醒你。

**為什麼不裝最新版就好？** 可以，但請把書裡的輸出當作「在 3.7 上的結果」看。尤其 layout 演算法的座標、隨機圖產生器在同一個 seed 下的結果，都不保證跨版本穩定。

### 驗證安裝

```python
import sys
import networkx as nx
import numpy as np
import scipy
import matplotlib

print("Python    ", sys.version.split()[0])
print("NetworkX  ", nx.__version__)
print("NumPy     ", np.__version__)
print("SciPy     ", scipy.__version__)
print("matplotlib", matplotlib.__version__)

G = nx.petersen_graph()
print("Petersen:", G.number_of_nodes(), "nodes,", G.number_of_edges(), "edges")
print("degrees:", sorted({d for _, d in G.degree()}))
L = nx.laplacian_matrix(G).toarray()
print("Laplacian type:", type(L).__name__, L.shape)
```

輸出：

```text
Python     3.13.12
NetworkX   3.7
NumPy      2.5.4
SciPy      1.18.1
matplotlib 3.11.2
Petersen: 10 nodes, 15 edges
degrees: [3]
Laplacian type: ndarray (10, 10)
```

最後一行確認 SciPy 這條路是通的。Petersen 圖（10 點、每點度數 3）會在這門課出現很多次，它是圖論裡最常見的反例來源。

### Windows 的中文輸出亂碼

把下一節的腳本輸出導到檔案或管線（例如 `python x.py > out.txt`，或在 Git Bash 裡跑）時，你可能看到：

```text
n=1:      1 �Ӽи��ϥ����ŦX����w�z
```

原因：Python 在 Windows 上寫到**主控台**時用 UTF-8，但寫到**檔案或管線**時預設用系統的 ANSI code page，繁中 Windows 是 cp950。接收端用 UTF-8 解讀，就變亂碼。修法是開 Python 的 UTF-8 模式：

```powershell
$env:PYTHONUTF8 = "1"      # 這個 PowerShell session 內有效
python -X utf8 script.py   # 或單次指定
```

本課所有輸出都在 `PYTHONUTF8=1` 下取得。Linux / macOS 的 locale 通常已是 UTF-8，不受影響。

## 工作流一：小圖暴力窮舉驗證定理

先拿圖論第一個定理練手。

**握手定理（Handshaking Lemma）**：任意簡單無向圖中，所有頂點度數總和等於邊數的兩倍，`Σ deg(v) = 2|E|`；因此奇數度頂點的個數必為偶數。

證明在 [Ch 2](./02-proof-toolbox.md) 會寫完整，這裡先讓電腦說服你。

### 標號圖：用 itertools 窮舉

頂點固定為 `0..n-1` 時，每一對頂點 `{u, v}` 要嘛有邊要嘛沒有。共有 `C(n,2)` 對，所以有 `2^C(n,2)` 張**標號圖（labeled graph）**。用一個整數 `mask` 的每個 bit 代表一對頂點，就能全部列出來：

```python
"""暴力驗證握手定理：所有 n <= 6 的標號圖（itertools 窮舉）。"""
from itertools import combinations


def all_labeled_graphs(n):
    """產生頂點 0..n-1 上的所有簡單無向圖，以鄰接字典表示。"""
    pairs = list(combinations(range(n), 2))
    for mask in range(1 << len(pairs)):
        adj = {v: set() for v in range(n)}
        for i, (u, v) in enumerate(pairs):
            if mask >> i & 1:
                adj[u].add(v)
                adj[v].add(u)
        yield adj


def check_handshake(adj):
    deg_sum = sum(len(nbrs) for nbrs in adj.values())
    m = sum(1 for u in adj for v in adj[u] if u < v)
    odd = sum(1 for nbrs in adj.values() if len(nbrs) % 2 == 1)
    return deg_sum == 2 * m and odd % 2 == 0


for n in range(1, 7):
    total = 0
    for adj in all_labeled_graphs(n):
        total += 1
        assert check_handshake(adj), adj
    print(f"n={n}: {total:>6} 個標號圖全部符合握手定理")
```

輸出：

```text
n=1:      1 個標號圖全部符合握手定理
n=2:      2 個標號圖全部符合握手定理
n=3:      8 個標號圖全部符合握手定理
n=4:     64 個標號圖全部符合握手定理
n=5:   1024 個標號圖全部符合握手定理
n=6:  32768 個標號圖全部符合握手定理
```

注意 `m` 的算法：我們**沒有**用 `sum(deg) // 2` 算邊數，因為那等於假設了要驗證的定理。數邊時用 `u < v` 讓每條邊只被數一次，這是獨立於度數的計數方式。驗證程式不能偷用待證結論，這條原則全課適用。

窮舉的代價成長得非常快。同一支程式跑 n = 7（`2^21 = 2,097,152` 張圖），在上面那台 Ryzen 7 5700G 上單執行緒花了 11.9 秒；n = 8 是 `2^28`，約 2.7 億張，純 Python 不實際。

### 無標號圖：用 graph atlas

很多定理只關心圖的「形狀」，不關心頂點叫什麼名字。兩張圖只差在頂點重新命名時叫**同構（isomorphic）**，同構類別叫**無標號圖（unlabeled graph）**。n = 7 的標號圖有兩百多萬張，但無標號圖只有 1044 種。NetworkX 內建了《An Atlas of Graphs》裡所有 0 到 7 點的圖：

```python
"""用 NetworkX 的 graph atlas（所有 0..7 點無標號圖）驗證握手定理。"""
from collections import Counter
import networkx as nx

count = Counter()
for G in nx.graph_atlas_g():
    n = G.number_of_nodes()
    count[n] += 1
    deg_sum = sum(d for _, d in G.degree())
    assert deg_sum == 2 * G.number_of_edges()
    odd = sum(1 for _, d in G.degree() if d % 2)
    assert odd % 2 == 0

print("每個點數的無標號圖數量：", dict(sorted(count.items())))
print("總數：", sum(count.values()))
```

輸出：

```text
每個點數的無標號圖數量： {0: 1, 1: 1, 2: 2, 3: 4, 4: 11, 5: 34, 6: 156, 7: 1044}
總數： 1253
```

`1, 1, 2, 4, 11, 34, 156, 1044` 是 OEIS 的 A000088 數列。[Ch 6](./06-burnside-polya.md) 會用 Burnside 引理**不靠窮舉**把這串數字算出來，再回頭和這裡對答案。

函式名稱要注意：NetworkX 3.7 提供的是 `nx.graph_atlas_g()`（回傳 1253 張圖的 list）與 `nx.graph_atlas(i)`（回傳第 i 張）。網路上有些範例寫 `graph_atlas_all`，在 3.7 會得到：

```text
AttributeError: module 'networkx' has no attribute 'graph_atlas_all'
```

**標號 vs 無標號，用哪個？** 驗證「對所有圖成立」的定理時兩者等價，因為同構的圖滿足一樣的圖論性質；atlas 快得多，且能到 n = 7。但做**計數**（例如「n 點上有幾張連通的標號圖」）時一定要用標號窮舉，因為一個無標號圖對應的標號圖數量不一樣多。這個差別是 Ch 6 的核心。

### 窮舉找反例

窮舉真正的用途不是確認你已經相信的定理，而是**殺死錯的猜想**。下面是一個通用骨架：給「假設」和「結論」兩個述詞，在 atlas 裡找第一張滿足假設但結論不成立的圖。

```python
"""通用的「小圖窮舉找反例」骨架，拿一個錯的猜想來試。"""
from itertools import permutations
import networkx as nx


def to_adj(G):
    return {u: set(G[u]) for u in G}


def has_hamilton_cycle(adj):
    nodes = list(adj)
    n = len(nodes)
    if n < 3:
        return False
    first = nodes[0]
    for perm in permutations(nodes[1:]):
        cyc = (first,) + perm
        if all(cyc[(i + 1) % n] in adj[cyc[i]] for i in range(n)):
            return True
    return False


def find_counterexample(hypothesis, conclusion, graphs):
    for idx, G in enumerate(graphs):
        adj = to_adj(G)
        if hypothesis(adj) and not conclusion(adj):
            return idx, adj
    return None


def connected_min_deg_2(adj):
    if len(adj) < 3:
        return False
    G = nx.Graph(adj)  # 這裡只拿 NetworkX 判連通，Ch 0 的重點不是連通演算法
    G.add_nodes_from(adj)
    return nx.is_connected(G) and min(len(s) for s in adj.values()) >= 2


atlas = nx.graph_atlas_g()
hit = find_counterexample(connected_min_deg_2, has_hamilton_cycle, atlas)
idx, adj = hit
print("猜想「連通且最小度數 >= 2 必有 Hamilton cycle」的第一個反例：")
print("atlas index =", idx)
print({u: sorted(v) for u, v in adj.items()})


def dirac(adj):
    n = len(adj)
    return n >= 3 and all(len(s) >= n / 2 for s in adj.values())


print("Dirac 條件（每點度數 >= n/2）在 n <= 7 的反例：",
      find_counterexample(dirac, has_hamilton_cycle, atlas))
```

輸出：

```text
猜想「連通且最小度數 >= 2 必有 Hamilton cycle」的第一個反例：
atlas index = 42
{0: [1, 4], 1: [0, 4], 2: [3, 4], 3: [2, 4], 4: [0, 1, 2, 3]}
Dirac 條件（每點度數 >= n/2）在 n <= 7 的反例： None
```

反例是「蝴蝶結」：兩個三角形共用頂點 4。Hamilton cycle 要經過 4 兩次才能從左邊三角形走到右邊，所以不存在。

```text
  0       2
  | \   / |
  |   4   |
  | /   \ |
  1       3
```

第二行說 Dirac 定理（[Ch 8](./08-euler-hamilton.md) 會證）在 n ≤ 7 找不到反例。**這不是證明**：窮舉只覆蓋有限張圖。它的價值是不對稱的——找到反例就確定猜想錯了；找不到只代表「值得去證明」。

## 工作流二：自刻 + NetworkX 對拍

### 鄰接字典約定

全課自刻演算法一律用這三種表示：

| 圖的種類 | 型別 | 例子 |
| --- | --- | --- |
| 無向圖 | `adj: dict[node, set[node]]` | `{0: {1, 2}, 1: {0}, 2: {0}}` |
| 有向圖 | `succ: dict[node, set[node]]` | `{"entry": {"b1"}, "b1": set()}` |
| 加權圖 | `dict[node, dict[node, weight]]` | `{0: {1: 3.5}, 1: {0: 3.5}}` |

為什麼不用鄰接矩陣？矩陣要 `n^2` 空間，而這門課碰到的圖（CFG、呼叫圖、攻擊圖）大多稀疏，邊數接近 n 而非 `n^2`。為什麼用 `set` 而不是 `list`？`v in adj[u]` 平均 O(1)，而且 set 天然擋掉重邊。為什麼不直接用 NetworkX 的 `G[u]`？因為自刻的重點是看清楚資料結構，而 `G[u]` 是一個 view 物件（下面會講它的坑），每次存取都多一層間接。

**無向圖的不變式**：`v in adj[u]` 若且唯若 `u in adj[v]`。孤立點必須以 `v: set()` 出現，不能省略，否則 `len(adj)` 就不是頂點數。

### 轉換與對拍範例

```python
"""自刻 BFS 距離 vs NetworkX，在固定 seed 的隨機圖上對拍。"""
from collections import deque
import random
import networkx as nx


def to_adj(G):
    """NetworkX 無向圖 -> 鄰接字典 dict[node, set[node]]。"""
    return {u: set(G[u]) for u in G}


def from_adj(adj):
    """鄰接字典 -> NetworkX Graph（孤立點也要保留）。"""
    G = nx.Graph()
    G.add_nodes_from(adj)
    G.add_edges_from((u, v) for u in adj for v in adj[u])
    return G


def bfs_dist(adj, s):
    dist = {s: 0}
    q = deque([s])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return dist


SEED = 2026
rng = random.Random(SEED)
trials = 0
for t in range(200):
    n = rng.randint(1, 30)
    p = rng.random()
    G = nx.gnp_random_graph(n, p, seed=rng.randint(0, 10**9))
    adj = to_adj(G)
    assert nx.utils.graphs_equal(from_adj(adj), G)
    for s in G:
        mine = bfs_dist(adj, s)
        ref = nx.single_source_shortest_path_length(G, s)
        assert mine == ref, (t, s)
        trials += 1
print(f"seed={SEED}: 200 張隨機圖、{trials} 個起點，自刻 BFS 與 NetworkX 完全一致")
```

輸出：

```text
seed=2026: 200 張隨機圖、3200 個起點，自刻 BFS 與 NetworkX 完全一致
```

這段的幾個設計決策，全課沿用：

- **一個主 seed 派生所有子 seed**。`rng = random.Random(SEED)` 產生每張圖的 seed，這樣只要回報 `seed=2026, t=137` 就能重現失敗案例。不要用全域 `random.seed()`，別的函式庫也可能動到它。
- **n 和 p 都隨機**。只測 `p = 0.5` 會漏掉空圖、完全圖、極稀疏圖這些邊界；`n = 1` 也在範圍內。
- **assert 帶上下文**。`assert mine == ref, (t, s)` 失敗時直接告訴你是第幾張圖、哪個起點。
- **轉換本身也要對拍**。`graphs_equal(from_adj(adj), G)` 確認來回轉換沒丟東西（孤立點最常丟）。

`from_adj` 會把每條無向邊加兩次（`u→v` 與 `v→u`），對 `nx.Graph` 無害，因為重複的邊會合併。換成 `MultiGraph` 就會變成兩條邊，下一節的坑 4。

## 用 matplotlib 畫圖

看不懂結構時，先畫出來。

```python
import matplotlib
matplotlib.use("Agg")  # 沒有螢幕也能畫；互動式環境可拿掉這行改用 plt.show()
import matplotlib.pyplot as plt
import networkx as nx

G = nx.petersen_graph()
fig, axes = plt.subplots(1, 2, figsize=(8, 4))

pos = nx.spring_layout(G, seed=42)
nx.draw(G, pos, ax=axes[0], with_labels=True, node_color="lightsteelblue")
axes[0].set_title("spring_layout(seed=42)")

shells = [range(5, 10), range(5)]
pos2 = nx.shell_layout(G, nlist=shells)
nx.draw(G, pos2, ax=axes[1], with_labels=True, node_color="wheat")
axes[1].set_title("shell_layout")

fig.tight_layout()
fig.savefig("petersen.png", dpi=120)
print("saved petersen.png")
```

執行後當前目錄會出現 `petersen.png`。左圖是力導向排版，Petersen 圖會糊成一團、邊交錯很多，看不出結構；右圖把 0–4 放外圈、5–9 放內圈，你會看到教科書上的經典樣子：外圈五邊形、內圈五角星、五條輻條相連。同一張圖，排版決定你看不看得懂。

兩個要記得的點：

- `spring_layout` 是隨機初始化的力導向演算法，**不給 seed 每次畫出來都不一樣**，寫筆記或比較兩張圖時一定要固定 seed。
- 排版是給人看的，不是圖的性質。兩張畫得很不一樣的圖可能同構，畫得很像的也可能不同構（Ch 7 會遇到）。

## 踩雷集錦：NetworkX 常見坑

先跑這支，每個坑都有真實輸出：

```python
import networkx as nx

print("--- 坑 1：節點標籤型別 ---")
G = nx.Graph()
G.add_edge(1, 2)
G.add_edge("2", 3)
print(sorted(G.nodes(), key=str), G.number_of_nodes())

print("--- 坑 2：Graph vs DiGraph ---")
D = nx.DiGraph([("a", "b"), ("b", "c")])
print("neighbors(b):", list(D.neighbors("b")))
print("predecessors(b):", list(D.predecessors("b")))
print("degree(b):", D.degree("b"), "in:", D.in_degree("b"), "out:", D.out_degree("b"))
U = nx.Graph(D)
print("轉無向後 neighbors(b):", sorted(U.neighbors("b")))

print("--- 坑 3：view 物件是活的 ---")
G = nx.path_graph(4)
nodes_view = G.nodes
edges_view = G.edges
print("before:", list(nodes_view), list(edges_view))
G.add_edge(3, 4)
print("after :", list(nodes_view), list(edges_view))
try:
    for u in G.nodes:
        if u % 2 == 0:
            G.remove_node(u)
except RuntimeError as e:
    print("RuntimeError:", e)

print("--- 坑 4：重邊與自環 ---")
G = nx.Graph()
G.add_edge(0, 1)
G.add_edge(0, 1)
print("Graph 加兩次同一條邊 -> edges =", G.number_of_edges())
M = nx.MultiGraph()
k1 = M.add_edge(0, 1)
k2 = M.add_edge(0, 1)
print("MultiGraph keys:", k1, k2, "edges =", M.number_of_edges(), "deg(0) =", M.degree(0))
print("nx.Graph(M) edges =", nx.Graph(M).number_of_edges())
S = nx.Graph([(0, 0), (0, 1)])
print("自環：deg(0) =", S.degree(0), " set(S[0]) =", set(S[0]))
```

輸出：

```text
--- 坑 1：節點標籤型別 ---
[1, 2, '2', 3] 4
--- 坑 2：Graph vs DiGraph ---
neighbors(b): ['c']
predecessors(b): ['a']
degree(b): 2 in: 1 out: 1
轉無向後 neighbors(b): ['a', 'c']
--- 坑 3：view 物件是活的 ---
before: [0, 1, 2, 3] [(0, 1), (1, 2), (2, 3)]
after : [0, 1, 2, 3, 4] [(0, 1), (1, 2), (2, 3), (3, 4)]
RuntimeError: dictionary changed size during iteration
--- 坑 4：重邊與自環 ---
Graph 加兩次同一條邊 -> edges = 1
MultiGraph keys: 0 1 edges = 2 deg(0) = 2
nx.Graph(M) edges = 1
自環：deg(0) = 3  set(S[0]) = {0, 1}
```

1. **節點標籤是任意 hashable，`2` 和 `"2"` 是兩個不同節點**：你以為建了一條路徑 1–2–3，實際上得到兩個分離的元件、4 個節點。從檔案讀邊表時最常發生（`split()` 出來是字串）。糾正：讀檔後統一轉型，或在建圖後檢查 `number_of_nodes()` 是不是你預期的值。
2. **`DiGraph.neighbors` 只回傳後繼（successors）**：在 CFG 上想找「誰會跳到這個 block」要用 `predecessors`。`degree` 在有向圖是入度加出度。`nx.Graph(D)` 會丟掉方向，而且若原本同時有 `a→b` 與 `b→a`，轉完只剩一條邊。糾正：寫有向圖演算法時明確選 `successors` / `predecessors`，自刻時用 `succ` 和另建的 `pred` 兩個字典。
3. **`G.nodes`、`G.edges`、`G[u]`、`G.degree` 都是 view，不是快照**：它們反映圖的**當下**狀態，所以加邊後 `edges_view` 也跟著變；邊迭代邊刪點會丟 `RuntimeError`。糾正：需要快照就 `list(G.nodes)`；要刪點先收集再刪，`G.remove_nodes_from([u for u in G if u % 2 == 0])`。我們的 `to_adj` 用 `set(G[u])` 複製一份，也是為了切斷和原圖的連動。
4. **`Graph` 會吞掉重邊，`MultiGraph` 不會**：`Graph` 上第二次 `add_edge(0, 1)` 只是更新屬性。`MultiGraph` 每條平行邊有自己的 key（0、1、…），度數也算兩次。把 `MultiGraph` 轉成 `Graph` 會靜默合併平行邊。柯尼斯堡七橋（[Ch 1](./01-graph-theory-overview.md)）就是一個 multigraph，用 `Graph` 建會只剩五條邊，答案完全錯。
5. **自環讓度數加 2，而且鄰接集合包含自己**：`deg(0) = 3`（自環算 2、加上邊 0–1）。我們的鄰接字典約定是**簡單圖**，`len(adj[u])` 在有自環時會少算 1。糾正：自刻演算法開頭用 `assert all(u not in adj[u] for u in adj)` 擋掉自環；需要自環或重邊的章節會明說改用什麼表示。

## 對比與取捨

| 驗證方式 | 能給你什麼 | 不能給你什麼 | 適用情境 |
| --- | --- | --- | --- |
| 標號窮舉（itertools） | 精確計數、所有標號情況 | n ≥ 8 不可行 | 計數公式、標號相關的性質 |
| graph atlas | 所有 n ≤ 7 的形狀，1253 張 | 標號資訊、n = 8 以上 | 「對所有圖成立」的定理、找反例 |
| 固定 seed 隨機圖 | 中大型圖、各種密度 | 完備性；可能漏掉稀有結構 | 演算法對拍、效能觀察 |
| 和 NetworkX 對拍 | 獨立實作的參考答案 | 證明；兩邊可能犯同樣的錯 | 每個自刻演算法 |
| 數學證明 | 對所有圖的保證 | 抓不到你證明裡的筆誤 | 每個核心定理 |

最後一欄不是開玩笑。證明寫錯一個下標、窮舉程式偷用了待證結論、對拍時兩邊都用同一個有 bug 的 helper——每一種單獨都會騙你，組合起來才可靠。

## 進階：再往深一層

**n = 8 以上的無標號圖**：n = 8 有 12346 種無標號圖，atlas 沒有。標準工具是 Brendan McKay 的 nauty 套件裡的 `geng`，它用 canonical augmentation 不重複地產生每個同構類別一次，輸出 graph6 格式，NetworkX 可用 `nx.read_graph6` 讀入。本課環境沒有安裝 nauty，**這段未實測**；需要的話在 WSL 用套件管理器安裝 nauty 後試 `geng 8 > g8.g6`。

**更聰明的標號窮舉**：上面的 `all_labeled_graphs` 每張圖都從頭建字典。用 Gray code 順序（相鄰兩個 mask 只差一個 bit）可以每步只加或刪一條邊，增量維護度數。這是練習 A 的延伸挑戰之一。

**property-based testing**：手寫隨機圖產生器之外，Python 的 Hypothesis 函式庫可以自動產生輸入並在失敗時把反例「縮小」到最簡形式。本課不依賴它，但如果你的工作程式碼要測圖演算法，它比固定 seed 的迴圈更能挖出邊界情況。

**本課的腳本習慣**：每個 code block 都可以單獨存檔執行。你可以自己把 `to_adj`、`from_adj`、`all_labeled_graphs` 收進一個 `gtutil.py`，但書裡的範例會把用到的小函式直接寫在腳本裡，讓每段都能複製即跑。

## 動手練習

1. 把握手定理驗證程式的 `m` 改成 `deg_sum / 2`，第一個條件 `deg_sum == 2 * m` 就永遠成立。解釋為什麼這樣的「驗證」對 `Σ deg(v) = 2|E|` 沒有任何檢查力。
2. 用 `find_counterexample` 測試猜想「每個連通圖的邊數至少是 n - 1」與「邊數至少 n - 1 的圖一定連通」。哪個是真的？假的那個，反例最少幾個點？
3. 在對拍腳本的 `bfs_dist` 裡故意把 `if v not in dist` 改成 `if v not in q`，跑跑看在第幾張圖、哪個起點失敗，然後解釋原因。
4. 用 `nx.MultiGraph` 建一張有 3 條平行邊的圖，分別用 `to_adj` 與 `nx.Graph(M)` 轉換，印出度數，看資訊在哪裡丟掉。

## 本章重點整理

- 每個定理在小圖上窮舉驗證：n ≤ 6 用 itertools 標號窮舉，n ≤ 7 用 `nx.graph_atlas_g()`。
- 窮舉找到反例就推翻猜想；找不到只代表值得證明，不是證明。
- 自刻演算法用鄰接字典 `dict[node, set[node]]`，和 NetworkX 在固定 seed 的隨機圖上對拍。
- 驗證程式不能偷用待證結論；轉換函式本身也要對拍。
- NetworkX 的坑：標籤型別、有向圖的 neighbors、活的 view、`Graph` 吞重邊、自環度數加 2。

## 自我檢核

- [ ] 不看筆記，寫出「把 `C(n,2)` 個 bit 對應到邊」的標號圖窮舉器，並說出 n = 7 為什麼還跑得動、n = 8 為什麼不行。
- [ ] 解釋標號圖與無標號圖的差別，並說出什麼時候一定要用標號窮舉。
- [ ] 如果同事說「我的演算法在 1000 張隨機圖上都和 NetworkX 一樣，所以是對的」，你會怎麼回應？
- [ ] 不翻書，列出至少三個 NetworkX 的坑，並說出各自在 CFG 或攻擊圖這類真實資料上會造成什麼錯誤結果。

## 延伸閱讀

### 官方文件

- **[NetworkX Tutorial](https://networkx.org/documentation/stable/tutorial.html)**
  - **讀哪裡**：「Adding attributes to graphs, nodes, and edges」之前的全部，以及「Directed graphs」「Multigraphs」兩節。
  - **能學到什麼**：本章的坑 2–4 在官方文件裡的說法；`G.adj`、`G[u]`、`G.edges` 這些 view 各自回傳什麼。
  - **前提知識**：Python dict 操作。
- **[NetworkX 參考：Atlas](https://networkx.org/documentation/stable/reference/generated/networkx.generators.atlas.graph_atlas_g.html)**
  - **讀哪裡**：`graph_atlas_g` 的 Notes 一節。
  - **能學到什麼**：atlas 的排序規則（點數、邊數、度數序列、自同構數）與三個例外，讓你知道 `atlas[42]` 為什麼是蝴蝶結。
  - **前提知識**：本章的無標號圖概念。

### 書籍

- **《An Atlas of Graphs》** — Ronald C. Read、Robin J. Wilson（Oxford University Press，1998）
  - **這本書的定位**：`graph_atlas_g()` 的資料來源，把 7 點以內的所有圖編號畫出，另有樹、正則圖等分類圖鑑。
  - **讀哪裡**：翻一翻 4–5 點的圖，對照 atlas 的索引，建立「小圖長什麼樣」的直覺。不需要讀完。
  - **前提知識**：無。

### 資料庫

- **[OEIS A000088：n 個頂點的無標號簡單圖數量](https://oeis.org/A000088)**
  - **讀哪裡**：DATA 列和 FORMULA 段的前幾條。
  - **能學到什麼**：本章 atlas 計數 `1, 1, 2, 4, 11, 34, 156, 1044` 的出處，以及延續下去的 12346、274668……。OEIS 是整個 Part 1 對答案的參考資料庫，計數結果只要對上 OEIS 的數列，就能大致排除程式錯誤。
  - **前提知識**：無。

環境好了、兩個工作流也跑過一遍，下一章回到圖論的起點：一個 1736 年的散步問題，以及它怎麼長成你每天在用的 dominator tree 與攻擊圖。

→ [Ch 1 圖論全貌：從柯尼斯堡七橋出發](./01-graph-theory-overview.md)
