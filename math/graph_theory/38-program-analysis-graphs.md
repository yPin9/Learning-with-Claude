# Ch 38 — 程式分析的圖：CPG 與 reachability

> **目標**：能從一段程式親手建出 AST、CFG、def-use 圖，把「使用者輸入會不會流到危險函式」寫成一個圖可達性問題；理解為什麼跨函式時普通 BFS 會誤報，以及 IFDS 怎麼把它變成「括號配對」的可達性（CFL-reachability）。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。本章的程式分析對象是 Python 標準函式庫 `ast` 解析出的小段 Python 程式，`ast.unparse` 需要 Python 3.9 以上。

## 為什麼需要這個？

在靜態分析工具出現之前，找注入型漏洞的方法是人工 grep：搜 `system(`、`execute(`，再一個個往回追參數從哪裡來。這個方法有兩個根本問題：

1. **grep 看不見資料怎麼流**。`q = prefix + user` 隔了五行之後 `run_sql(q)`，字串比對不知道 `q` 和 `user` 有關係。
2. **人工往回追不會停**。參數來自另一個函式的回傳值，那個函式又被三個地方呼叫——人會漏，也會在第三層放棄。

1970–80 年代編譯器社群已經解決了這個問題的「一半」：為了做最佳化，編譯器必須知道「這個變數的值是從哪條指令來的」，於是有了資料流分析（dataflow analysis）、reaching definitions、program dependence graph（PDG，Ferrante、Ottenstein、Warren 1987）。資安社群後來發現：**污點分析（taint analysis）就是在這些圖上問一個可達性問題**。2014 年 Yamaguchi 等人把 AST、CFG、PDG 疊成一張 code property graph（CPG），讓「找漏洞」變成「在一張圖上下查詢」，這就是 Joern 的核心；CodeQL 的 dataflow 函式庫也在做同一類事情。

這一章把這條線用圖論的語言講一次：每一種程式表示是一張什麼圖、taint 是哪一種可達性、跨函式時為什麼要從普通可達性升級成 CFL-reachability。

## 先建立直覺

想像程式是一座水管工廠。

- **AST（抽象語法樹）** 是每台機器的零件圖：`q = "..." + user` 拆成「指派」、「加法」、「常數」、「變數 user」。它是一棵樹，只描述**長相**。
- **CFG（控制流程圖）** 是輸送帶：哪台機器做完，下一台可能是誰。它描述**執行順序**，`if` 讓輸送帶分岔，`while` 讓它繞回去。
- **def-use 圖（資料相依圖）** 是水管：`user` 這桶水在第 1 台機器裝滿，被接到第 5 台和第 8 台去用。它描述**值從哪裡來**。

污點分析問的是：從「水源」（`source()`，使用者輸入）倒進一滴墨水，沿著水管走，不經過「濾水器」（`escape()`，sanitizer），能不能流到「危險出口」（`sink()`，例如 SQL 執行）？這就是**可達性**。

```text
           AST（長相）          CFG（順序）          def-use（值的來源）
          q = ... + user       1 → 2 → 3 ─┬→ 4 ─┐     1(user) ──→ 5(q) ──→ 9 sink(q)
             /     \                      └→ 5 ─┴→ 6   1(user) ──→ 8(safe=escape) ─╳→ 10
          Assign    BinOp                          ↺ 7
                    /   \
               Const   Name(user)
```

最右邊那張圖上，taint 從 1 出發：走 `1 → 5 → 9` 到達 sink，報一個 finding；走 `1 → 8` 碰到 sanitizer，停住。整章的工作就是把這張圖**正確地**建出來，以及在跨函式時**不要多走不該走的路**。

> 如果你對 CFG、可達性和 SCC 還不熟，先回看 [Ch 13 — 有向圖：SCC 與 DAG](./13-directed-graphs-scc-dag.md)；CFG 上的支配關係在 [Ch 14 — Dominator tree 與 Lengauer–Tarjan](./14-dominator-tree.md)。

## 四種程式圖：定義與差異

先把四個名詞釘死。下面的「節點」與「邊」都是圖論意義的，你可以用 [Ch 7](./07-graph-definitions.md) 的語言描述它們。

| 圖 | 節點 | 邊 `u → v` 的意思 | 圖論性質 |
| --- | --- | --- | --- |
| AST | 語法結構（運算式、語句） | `v` 是 `u` 的子結構 | 有根樹 |
| CFG | 基本區塊或語句 | 執行完 `u` 之後**可能**執行 `v` | 有向圖，有環（迴圈），單一入口 |
| PDG | 語句 | 資料相依（`u` 定義的值被 `v` 使用）或控制相依（`u` 的分支結果決定 `v` 會不會執行） | 有向圖，兩種邊 |
| CPG | 以上全部的聯集 | 每條邊帶標籤 `AST` / `CFG` / `REACHING_DEF` / `CDG` | 有向多重圖（multigraph），邊有屬性 |

幾個要分清楚的點：

- **CFG 的邊是「可能」**。`if` 的兩條出邊都在 CFG 裡，CFG 不知道哪條會被走；這叫路徑不敏感（path-insensitive）的根源。
- **控制相依（control dependence）是用後支配（post-dominance）定義的**：`v` 控制相依於分支 `u`，若 `u` 的某個後繼被 `v` 後支配、但 `u` 本身不被 `v` 嚴格後支配。這就是在反向 CFG 上跑 [Ch 14](./14-dominator-tree.md) 的 dominator tree，再算 dominance frontier。本章的程式只建資料相依，控制相依留到動手練習。
- **CPG 不是新的分析**，而是把三張圖疊在同一組節點上，讓一個查詢可以混著走：「沿 AST 找到 `sink` 呼叫，沿 REACHING_DEF 邊往回走到 `source`」。

## 範例一：從 AST 建 CFG 與 def-use 圖

我們分析的對象是這段 Python：

```python
def handler(req):
    user = source(req)
    page = 1
    if user == "admin":
        q = "SELECT * FROM t"
    else:
        q = "SELECT * FROM t WHERE u=" + user
    while page < 3:
        page = page + 1
    safe = escape(user)
    sink(q)
    sink(safe)
    sink(page)
```

`source`、`escape`、`sink` 都是假的函式名稱，只是記號：這段程式不會被執行，只會被 `ast.parse` 解析。

建圖分三步：

1. **CFG**：遞迴走過語句串列，維護「流進這段程式的前驅集合」`preds`。簡單語句接在所有前驅後面；`if` 的條件節點分兩路，兩路的出口聯集成新的 `preds`；`while` 的迴圈體出口接回條件節點（back edge），只有條件節點能離開迴圈。
2. **Reaching definitions**：一個定義 `(v, m)` 表示「語句 m 寫了變數 v」。它抵達語句 n，若存在一條 CFG 路徑 `m → … → n` 中間沒有別的語句再寫 v。用經典的迭代資料流方程式解：`IN[n] = ∪ OUT[p]`（p 是 n 的前驅），`OUT[n] = gen[n] ∪ (IN[n] − kill[n])`。
3. **def-use 邊**：若定義 `(v, m)` 抵達 n 而且 n 讀了 v，加一條邊 `m → n`。

```python
# ch38_cfg_taint.py
"""Ch 38 範例一/二：從 Python AST 建語句級 CFG、reaching definitions、def-use 圖，taint = 可達性。"""
import ast
import networkx as nx

SRC = '''
def handler(req):
    user = source(req)
    page = 1
    if user == "admin":
        q = "SELECT * FROM t"
    else:
        q = "SELECT * FROM t WHERE u=" + user
    while page < 3:
        page = page + 1
    safe = escape(user)
    sink(q)
    sink(safe)
    sink(page)
'''


class CFGBuilder:
    """語句級 CFG：每個簡單語句、每個 if/while 的條件各是一個節點。"""

    def __init__(self):
        self.succ = {}      # node -> set(node)
        self.stmt = {}      # node -> ast 節點
        self.n = 0

    def new(self, node):
        self.n += 1
        self.succ[self.n] = set()
        self.stmt[self.n] = node
        return self.n

    def edge(self, a, b):
        self.succ[a].add(b)

    def build_block(self, stmts, preds):
        """把語句串接起來；preds 是「流進這段程式」的前驅集合，回傳流出的節點集合。"""
        for s in stmts:
            if isinstance(s, ast.If):
                c = self.new(s.test)
                for p in preds:
                    self.edge(p, c)
                out_t = self.build_block(s.body, {c})
                out_f = self.build_block(s.orelse, {c}) if s.orelse else {c}
                preds = out_t | out_f
            elif isinstance(s, ast.While):
                c = self.new(s.test)
                for p in preds:
                    self.edge(p, c)
                out_body = self.build_block(s.body, {c})
                for p in out_body:
                    self.edge(p, c)          # back edge
                preds = {c}                  # 條件為假才離開
            else:
                v = self.new(s)
                for p in preds:
                    self.edge(p, v)
                preds = {v}
        return preds


def defs_uses(node):
    """回傳 (被定義的變數集合, 被使用的變數集合)。"""
    if isinstance(node, ast.Assign):
        d = {t.id for t in node.targets if isinstance(t, ast.Name)}
        u = {x.id for x in ast.walk(node.value) if isinstance(x, ast.Name)}
    else:
        d = set()
        u = {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}
    funcs = {x.func.id for x in ast.walk(node) if isinstance(x, ast.Call)}
    return d, u - funcs


def reaching_definitions(succ, du):
    """經典 iterative dataflow：IN[n] = ∪ OUT[p]，OUT[n] = gen ∪ (IN - kill)。定義 = (var, node)。"""
    pred = {n: set() for n in succ}
    for a in succ:
        for b in succ[a]:
            pred[b].add(a)
    IN = {n: set() for n in succ}
    OUT = {n: set() for n in succ}
    changed, rounds = True, 0
    while changed:
        changed, rounds = False, rounds + 1
        for n in sorted(succ):
            IN[n] = set().union(*(OUT[p] for p in pred[n])) if pred[n] else set()
            d, _ = du[n]
            new = {(v, n) for v in d} | {(v, m) for (v, m) in IN[n] if v not in d}
            if new != OUT[n]:
                OUT[n], changed = new, True
    return IN, rounds


def call_name(node):
    if isinstance(node, ast.Assign):
        node = node.value
    if isinstance(node, ast.Expr):
        node = node.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    return None


def main():
    fn = ast.parse(SRC).body[0]
    b = CFGBuilder()
    b.build_block(fn.body, set())
    du = {n: defs_uses(b.stmt[n]) for n in b.succ}
    print("== CFG（語句級）==")
    for n in sorted(b.succ):
        print(f"{n:2} {ast.unparse(b.stmt[n]):32} -> {sorted(b.succ[n])}")

    IN, rounds = reaching_definitions(b.succ, du)
    print(f"reaching definitions 收斂輪數：{rounds}")

    # def-use 圖：定義點 m 的變數 v 抵達使用點 n
    dug = {n: set() for n in b.succ}
    for n in b.succ:
        _, uses = du[n]
        for (v, m) in IN[n]:
            if v in uses:
                dug[m].add(n)
    print("== def-use 邊 ==")
    for m in sorted(dug):
        if dug[m]:
            print(f"{m:2} -> {sorted(dug[m])}")

    sources = {n for n in b.succ if call_name(b.stmt[n]) == "source"}
    sanit = {n for n in b.succ if call_name(b.stmt[n]) == "escape"}
    sinks = {n for n in b.succ if call_name(b.stmt[n]) == "sink"}

    # taint = 從 source 出發、不穿過 sanitizer 的可達性
    seen, stack = set(sources), list(sources)
    while stack:
        u = stack.pop()
        for w in dug[u]:
            if w not in seen and w not in sanit:
                seen.add(w)
                stack.append(w)
    hits = sorted(seen & sinks)
    print("tainted sinks:", [(n, ast.unparse(b.stmt[n])) for n in hits])

    # 對拍：NetworkX 在「拿掉 sanitizer 節點」的子圖上算 descendants
    G = nx.DiGraph([(a, c) for a in dug for c in dug[a]])
    G.add_nodes_from(dug)
    H = G.subgraph(set(G) - sanit)
    nx_hits = set()
    for s in sources:
        nx_hits |= (nx.descendants(H, s) | {s}) & sinks
    print("networkx 對拍一致：", sorted(nx_hits) == hits)


if __name__ == "__main__":
    main()
```

實跑輸出（繁中 Windows 上輸出導到 pipe 或檔案時會用 cp950 編碼而變亂碼，本課的範例一律先設環境變數 `PYTHONUTF8=1` 再執行）：

```text
== CFG（語句級）==
 1 user = source(req)               -> [2]
 2 page = 1                         -> [3]
 3 user == 'admin'                  -> [4, 5]
 4 q = 'SELECT * FROM t'            -> [6]
 5 q = 'SELECT * FROM t WHERE u=' + user -> [6]
 6 page < 3                         -> [7, 8]
 7 page = page + 1                  -> [6]
 8 safe = escape(user)              -> [9]
 9 sink(q)                          -> [10]
10 sink(safe)                       -> [11]
11 sink(page)                       -> []
reaching definitions 收斂輪數：3
== def-use 邊 ==
 1 -> [3, 5, 8]
 2 -> [6, 7, 11]
 4 -> [9]
 5 -> [9]
 7 -> [6, 7, 11]
 8 -> [10]
tainted sinks: [(9, 'sink(q)')]
networkx 對拍一致： True
```

逐行讀這份輸出：

- **CFG**：節點 3 有兩條出邊（if 分岔），節點 7 → 6 是 while 的 back edge。6 → 8 是條件為假離開迴圈。
- **def-use**：`4 -> [9]` 和 `5 -> [9]` 同時存在，因為 `q` 的兩個定義都能沿某條 CFG 路徑抵達 9。這就是「may」分析：只要**存在**一條路徑就算。`7 -> [6, 7, 11]` 包含自己——迴圈第二輪讀的 `page` 是上一輪寫的，def-use 圖本身也有環。
- **taint**：`1 → 5 → 9` 命中。`sink(safe)` 沒命中，因為唯一的路 `1 → 8 → 10` 穿過 sanitizer 節點 8。`sink(page)` 沒命中，因為 `page` 的定義 2、7 都不可從 1 到達。
- **收斂輪數 3**：第 1 輪沿著節點編號順序（大致是逆後序）把定義一路推下去；第 2 輪把 back edge 7 → 6 帶回來的 `(page, 7)` 傳開；第 3 輪確認沒有變化。迭代次數和節點數無關，而是和 CFG 的「loop connectedness」d（一條無環路徑上最多經過幾條 back edge）有關：對 reaching definitions 這類 rapid 框架，依逆後序掃描最多 d + 2 輪就收斂。這裡 d = 1，正好 3 輪。這是 Kam–Ullman（1976）的經典結果，本章不證明。

**為什麼 sanitizer 是「刪節點」而不是「刪邊」？** 因為 `escape(user)` 的輸出整個是乾淨的，所有從 8 出去的邊都該斷。如果 sanitizer 只清理部分欄位（例如只 escape 了 HTML，但值後來流進 SQL），那就不是刪節點，而是要把事實細分成「對 HTML 乾淨 / 對 SQL 乾淨」——這正是 CodeQL 的 flow state 在做的事。

## 底層機制：taint 為什麼「就是」可達性

把上面的直覺寫成命題，證明它，之後跨函式的部分才知道哪裡會壞。

**設定**。單一函式、沒有指標別名。def-use 圖 `U = (V, E)`，`V` 是語句，`m → n ∈ E` 當且僅當某個 `m` 寫的變數 v 的定義抵達 n、且 n 讀 v。令 `S` 為 source 語句集合、`Z` 為 sanitizer 語句集合。

**定義（被污染）**。語句 n 的值被污染，若存在一個**執行序列**（CFG 上一條從入口出發的路徑）使得 source 產生的值經由一串「寫入 → 讀取」傳到 n，且這串傳遞中沒有經過 sanitizer。

**命題**。在「路徑不敏感」的近似下（假設 CFG 的每條路徑都可能被執行），n 被污染 ⟺ 在 `U − Z` 這張子圖裡，存在從某個 `s ∈ S` 到 n 的有向路徑。

**證明**。

(⇐) 設 `s = x0 → x1 → … → xk = n` 是 `U − Z` 裡的路徑。對每條邊 `x(i) → x(i+1)`，依 def-use 邊的定義，存在變數 v(i) 與一條 CFG 路徑 `P(i)` 從 x(i) 到 x(i+1)，P(i) 內部沒有別的語句寫 v(i)。把 `P(0) P(1) … P(k−1)` 串接起來（共用端點），前面再接一條從入口到 s 的 CFG 路徑（s 可從入口到達，因為我們只建可達語句的 CFG），得到一條完整的執行序列。沿這條序列，x(i) 寫入 v(i) 的值在 P(i) 上沒被覆寫，於是 x(i+1) 讀到的正是 x(i) 寫的值；x(i+1) 的寫入依賴這個讀取。歸納得 n 讀到的值依賴 s 產生的值，且路徑上沒有 Z 中的節點。所以 n 被污染。

(⇒) 設存在一個執行序列把 s 的值傳到 n。取傳遞鏈中每一次「x 寫 v，y 讀到這個 v」的事件：在序列中 x 的寫入和 y 的讀取之間沒有別的語句寫 v（否則 y 讀到的不是 x 寫的值），所以 `(v, x)` 抵達 y，`x → y ∈ E`。把整條鏈的事件依序取出，得到 U 裡從 s 到 n 的路徑；鏈中沒有 sanitizer，所以路徑在 `U − Z` 內。∎

**這個證明在哪裡用到假設？** (⇐) 方向用到「每條 CFG 路徑都可能被執行」。真實程式不是這樣：`if user == "admin"` 為真時才走 4，為假才走 5，但 CFG 不知道。所以可達性給的是**過度近似（over-approximation）**：它不會漏（sound，在沒有別名和反射的前提下），但會誤報。這是一個取捨，不是 bug——要消除它，你得做路徑敏感分析，也就是在每條路徑上解約束，那會把我們帶到 SMT（[Ch 35](./35-reductions-to-sat.md) 的領域），代價是指數級的路徑數。

## 範例二：故意做錯——只看變數名的 taint

很多人第一次寫 taint tracker 會偷懶：「只要語句 A 寫 x、語句 B 讀 x，就連一條邊」，也就是不管順序、不做 reaching definitions。看它怎麼錯：

```python
# ch38_flowinsens.py（與 ch38_cfg_taint.py 放同一個目錄）
"""接在 ch38_cfg_taint.py 後面：flow-insensitive（只看變數名）vs flow-sensitive（reaching defs）。"""
import ast
from ch38_cfg_taint import CFGBuilder, defs_uses, reaching_definitions, call_name

SRC2 = '''
def f():
    x = source()
    x = 1
    sink(x)
'''

fn = ast.parse(SRC2).body[0]
b = CFGBuilder()
b.build_block(fn.body, set())
du = {n: defs_uses(b.stmt[n]) for n in b.succ}

# 版本 A：flow-insensitive —— 任何定義 v 的語句連到任何使用 v 的語句
naive = {n: {m for m in b.succ if du[n][0] & du[m][1]} for n in b.succ}
# 版本 B：reaching definitions
IN, _ = reaching_definitions(b.succ, du)
precise = {n: set() for n in b.succ}
for n in b.succ:
    for (v, m) in IN[n]:
        if v in du[n][1]:
            precise[m].add(n)


def tainted_sinks(g):
    src = [n for n in b.succ if call_name(b.stmt[n]) == "source"]
    seen, st = set(src), list(src)
    while st:
        u = st.pop()
        for w in g[u] - seen:
            seen.add(w)
            st.append(w)
    return sorted(n for n in seen if call_name(b.stmt[n]) == "sink")


print("flow-insensitive def-use:", {k: sorted(v) for k, v in naive.items()})
print("flow-insensitive tainted sinks:", tainted_sinks(naive))
print("reaching-defs def-use:  ", {k: sorted(v) for k, v in precise.items()})
print("reaching-defs tainted sinks:", tainted_sinks(precise))
```

```text
flow-insensitive def-use: {1: [3], 2: [3], 3: []}
flow-insensitive tainted sinks: [3]
reaching-defs def-use:   {1: [], 2: [3], 3: []}
reaching-defs tainted sinks: []
```

`x = 1` 把污染值覆寫了，flow-insensitive 版本看不到這件事，報了一個誤報。差別全在一條邊：`1 → 3` 在變數名圖裡存在，在 reaching-definitions 圖裡不存在，因為 CFG 路徑 1 → 2 → 3 中間的 2 殺掉了 `(x, 1)`。

這不代表 flow-insensitive 一無是處。它便宜（不用解資料流方程式），而且 points-to 分析的 Andersen 式演算法就是 flow-insensitive 的；在大型程式上，工具常常先用便宜的 flow-insensitive 結果篩一輪，再對候選做精確分析。

## 範例三：迷你 CPG——把三張圖疊在一起查詢

Joern 的查詢語言之所以能寫成「找到 sink 呼叫 → 看它的參數 → 沿資料流往回」，是因為 CPG 把 AST、CFG、資料相依放在**同一組節點**上。我們用 `nx.MultiDiGraph` 做一個縮小版：

```python
# ch38_cpg.py（與 ch38_cfg_taint.py 放同一個目錄）
"""接在 ch38_cfg_taint.py 後面：把 AST、CFG、def-use 疊成一張 property graph（迷你 CPG）並下查詢。"""
import ast
from collections import Counter
import networkx as nx
from ch38_cfg_taint import SRC, CFGBuilder, defs_uses, reaching_definitions, call_name

fn = ast.parse(SRC).body[0]
b = CFGBuilder()
b.build_block(fn.body, set())
du = {n: defs_uses(b.stmt[n]) for n in b.succ}
IN, _ = reaching_definitions(b.succ, du)

cpg = nx.MultiDiGraph()
ast_id = {}                                    # id(ast node) -> CPG 節點名
for n, node in b.stmt.items():                 # 語句節點同時是 AST 子樹的根
    ast_id[id(node)] = f"S{n}"
    cpg.add_node(f"S{n}", code=ast.unparse(node), call=call_name(node))
for n, node in b.stmt.items():
    for parent in ast.walk(node):
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, (ast.expr_context, ast.operator, ast.cmpop)):
                continue                       # Load/Add/Eq 是共用單例，不當成樹節點
            cid = ast_id.setdefault(id(child), f"A{len(ast_id)}")
            cpg.add_node(cid, code=type(child).__name__)
            cpg.add_edge(ast_id[id(parent)], cid, label="AST")
for a, ss in b.succ.items():
    for c in ss:
        cpg.add_edge(f"S{a}", f"S{c}", label="CFG")
for n in b.succ:
    for (v, m) in IN[n]:
        if v in du[n][1]:
            cpg.add_edge(f"S{m}", f"S{n}", label="REACHING_DEF", var=v)

print("節點數:", cpg.number_of_nodes())
print("各層邊數:", dict(Counter(lab for _, _, lab in cpg.edges(data="label"))))

# 查詢：「沿 REACHING_DEF 從 source() 走到 sink()，且不經過 escape()」
ddg = nx.DiGraph([(u, v) for u, v, lab in cpg.edges(data="label") if lab == "REACHING_DEF"])
calls = nx.get_node_attributes(cpg, "call")
blocked = {v for v, c in calls.items() if c == "escape"}
ddg.remove_nodes_from(blocked)
for s in [v for v, c in calls.items() if c == "source"]:
    for t in [v for v, c in calls.items() if c == "sink"]:
        if s in ddg and t in ddg and nx.has_path(ddg, s, t):
            path = nx.shortest_path(ddg, s, t)
            print("finding:", " -> ".join(cpg.nodes[x]["code"] for x in path))
```

```text
節點數: 44
各層邊數: {'AST': 33, 'CFG': 12, 'REACHING_DEF': 12}
finding: user = source(req) -> q = 'SELECT * FROM t WHERE u=' + user -> sink(q)
```

驗算一下這些數字：AST 部分是 11 棵子樹（每個語句節點是一棵樹的根），共 44 個節點、33 條 AST 邊，`44 − 11 = 33` 正好是森林的邊數公式 `|E| = |V| − 樹的個數`（[Ch 9](./09-trees-cayley-prufer.md)）。CFG 12 條邊和範例一的輸出逐條對得上，REACHING_DEF 12 條是範例一 def-use 邊的總數（3+3+1+1+3+1）。

第一版我沒跳過 `ast.Load()` 這類節點，得到「49 個節點、57 條 AST 邊」——邊比 `|V| − 樹數` 多，表示 AST 不是森林了。原因是 CPython 的 `ast` 讓所有 `Name` 共用同一個 `Load()` 物件，`id()` 相同，被當成同一個節點而有多個父節點。**用樹的邊數公式驗算，是抓這種建圖 bug 最便宜的方法。**

## 跨函式：為什麼普通可達性會說謊

到這裡都是單一函式。一旦有函式呼叫，最直接的做法是把所有函式的圖接起來：call site 連到被呼叫者的入口，被呼叫者的出口連回**所有** call site 的下一個語句。這張圖叫 **supergraph**。問題是：這張圖上有一些路徑**不可能被執行**——從 call site A 進去，從 call site B 出來。

我們用一個最小例子把它跑出來。程式是：

```text
main:                            id(a):
  m1: x = source()                 i0: (start)
  m2: call id(x)  -> t1            i1: r = a
  m3: (return site) t1 = ret       i2: (exit) return r
  m4: y = 1
  m5: call id(y)  -> t2
  m6: (return site) t2 = ret
  m7: sink(t2)        ← 應該乾淨
  m8: sink(t1)        ← 應該被污染
```

`id` 被呼叫兩次，一次傳污染值，一次傳常數。正確答案：t1 被污染、t2 乾淨。

### IFDS 的 exploded supergraph

Reps、Horwitz、Sagiv 在 POPL 1995 的論文提出 IFDS 框架（Interprocedural, Finite, Distributive, Subset）。它要求：

- 資料流事實是有限集合 D 的子集（這裡 D = 「哪些變數被污染」）；
- 每個語句的 flow function `f: 2^D → 2^D` 滿足**分配律** `f(A ∪ B) = f(A) ∪ f(B)`。

分配律帶來一個關鍵引理：

**引理（函式的圖表示）**。若 `f: 2^D → 2^D` 可分配，則對所有 `A ⊆ D`，`f(A) = f(∅) ∪ ⋃_{d∈A} f({d})`。也就是說，`f` 完全由 `f(∅)` 與 `|D|` 個值 `f({d})` 決定。

**證明**。先注意對任意 B，`f(B) = f(B ∪ ∅) = f(B) ∪ f(∅)`，所以 `f(∅) ⊆ f(B)`。接著對 `|A|` 歸納。`A = ∅` 時兩邊都是 `f(∅)`。設 `A = A' ∪ {d}`、`d ∉ A'`，由分配律 `f(A) = f(A') ∪ f({d})`，代入歸納假設 `f(A') = f(∅) ∪ ⋃_{d'∈A'} f({d'})` 即得。∎

於是 `f` 可以畫成一張 `(D ∪ {0}) × (D ∪ {0})` 的二部圖：從 `0` 連到 `f(∅)` 的每個元素（「無中生有」的事實，例如 `x = source()` 生出 x），從 `d` 連到 `f({d})` 的每個元素。特殊節點 `0`（論文記作 Λ）代表「永遠成立的空事實」。

**函式合成 = 路徑串接**。把語句 n1 的二部圖和 n2 的二部圖首尾接起來，`(f2 ∘ f1)` 的圖表示正是「長度 2 的路徑」：d 經過 f1 到 d'，d' 經過 f2 到 d''。這是關係合成（relation composition），歸納下去：沿 CFG 路徑 `p` 合成所有 flow function，等於在**每個語句複製一份 D ∪ {0} 的 exploded graph** 上沿 p 走。這張圖叫 **exploded supergraph** G#，節點是 `(語句, 事實)`。

**定理（Reps–Horwitz–Sagiv 1995）**。對 IFDS 問題，事實 d 在語句 n 成立（在「所有可實現路徑的 meet」意義下，meet 是聯集）⟺ 在 G# 中存在一條從 `(s_main, 0)` 到 `(n, d)` 的**可實現路徑（realizable path）**。可實現路徑是指：把每條 call 邊標 `(i`、每條對應的 return 邊標 `)i`（i 是 call site 編號），路徑上的括號序列必須是「部分配對」的——每個 `)i` 前面都有一個尚未關閉的 `(i` 跟它配對。

上面的引理和「合成 = 串接」已經完整證明了單一函式內的部分。跨函式的方向，論文的證明是對路徑上的呼叫巢狀深度歸納，加上 tabulation 演算法用 summary edge 記住「從 callee 入口事實 d3 能到 callee 出口事實 d4」——**這裡只給證明架構**，完整證明在論文第 4 節。論文給出的時間上界是 `O(E · D³)`，E 是 supergraph 的邊數、D 是事實個數。

### 實跑：naive BFS vs IFDS tabulation

```python
# ch38_ifds.py
"""Ch 38 範例三：IFDS = exploded supergraph 上的「可實現路徑」可達性。"""
import networkx as nx

ZERO = "0"
# 一般語句的 flow function：fact -> set(fact)
def f_normal(n, d):
    if n == "m1":                       # x = source()：0 生出 x
        if d == ZERO:
            return {ZERO, "x"}
        return set() if d == "x" else {d}   # 舊的 x 被覆寫
    if n == "m4":                       # y = 1：殺掉 y
        return set() if d == "y" else {d}
    if n == "i1":                       # r = a
        if d == "a":
            return {"a", "r"}
        return set() if d == "r" else {d}
    return {d}

normal_succ = {"m1": ["m2"], "m3": ["m4"], "m4": ["m5"], "m6": ["m7"], "m7": ["m8"], "m8": [],
               "i0": ["i1"], "i1": ["i2"]}
calls = {  # call node: (callee start, return site, 實參, 接收變數)
    "m2": ("i0", "m3", "x", "t1"),
    "m5": ("i0", "m6", "y", "t2"),
}
EXIT = {"i0": "i2"}
proc_of = {n: "main" for n in ["m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8"]}
proc_of.update({n: "id" for n in ["i0", "i1", "i2"]})
START = {"main": "m1", "id": "i0"}
CALLER_LOCALS = {"x", "y", "t1", "t2"}

def f_call(c, d):                       # caller 事實 -> callee 起點事實
    _, _, actual, _ = calls[c]
    if d == ZERO:
        return {ZERO}
    return {"a"} if d == actual else set()

def f_ret(c, d):                        # callee 結束事實 -> return site 事實
    _, _, _, recv = calls[c]
    if d == ZERO:
        return {ZERO}
    return {recv} if d == "r" else set()

def f_c2r(c, d):                        # call-to-return：caller 的區域事實繞過呼叫
    _, _, _, recv = calls[c]
    if d == recv:
        return set()
    return {d} if (d == ZERO or d in CALLER_LOCALS) else set()


def exploded_graph():
    """把整個 supergraph × D 展開成一張普通有向圖（不分辨呼叫/返回是否配對）。"""
    D = [ZERO, "x", "y", "t1", "t2", "a", "r"]
    G = nx.DiGraph()
    for n, ss in normal_succ.items():
        for d in D:
            for m in ss:
                for d2 in f_normal(n, d):
                    G.add_edge((n, d), (m, d2), kind="normal")
    for c, (s, ret, _, _) in calls.items():
        for d in D:
            for d2 in f_call(c, d):
                G.add_edge((c, d), (s, d2), kind="call")
            for d2 in f_c2r(c, d):
                G.add_edge((c, d), (ret, d2), kind="c2r")
            for d2 in f_ret(c, d):
                G.add_edge((EXIT[s], d), (ret, d2), kind="ret")
    return G


def ifds_tabulate():
    """Reps–Horwitz–Sagiv tabulation：path edge + summary edge，只走「呼叫與返回配對」的路徑。"""
    path_edges, work = set(), []
    incoming = {}        # (callee start, d3) -> {(call node, d2, caller start, d1)}
    end_summary = {}     # (callee start, d3) -> {exit fact d4}

    def prop(e):
        if e not in path_edges:
            path_edges.add(e)
            work.append(e)

    prop((("m1", ZERO), ("m1", ZERO)))
    while work:
        (sp, d1), (n, d2) = work.pop()
        if n in calls:
            s, ret, _, _ = calls[n]
            for d3 in f_call(n, d2):
                prop(((s, d3), (s, d3)))
                incoming.setdefault((s, d3), set()).add((n, d2, sp, d1))
                for d4 in end_summary.get((s, d3), ()):       # 已經算過的摘要直接套用
                    for d5 in f_ret(n, d4):
                        prop(((sp, d1), (ret, d5)))
            for d3 in f_c2r(n, d2):
                prop(((sp, d1), (ret, d3)))
        elif n in EXIT.values():
            s = START[proc_of[n]]
            end_summary.setdefault((s, d1), set()).add(d2)
            for (c, d4, csp, cd1) in incoming.get((s, d1), ()):  # 只回到「當初呼叫進來」的 call site
                for d5 in f_ret(c, d2):
                    prop(((csp, cd1), (calls[c][1], d5)))
        else:
            for m in normal_succ[n]:
                for d3 in f_normal(n, d2):
                    prop(((sp, d1), (m, d3)))
    return {(n, d) for (_, (n, d)) in path_edges}


G = exploded_graph()
naive = nx.descendants(G, ("m1", ZERO)) | {("m1", ZERO)}
precise = ifds_tabulate()
print("exploded supergraph:", G.number_of_nodes(), "nodes,", G.number_of_edges(), "edges")
for sink, var in [("m7", "t2"), ("m8", "t1")]:
    print(f"sink at {sink} uses {var}: naive reach={(sink, var) in naive}, IFDS={(sink, var) in precise}")
print("naive 多出來的事實:", sorted(naive - precise))

# 把 naive 的錯誤路徑印出來，標上括號：call m2 = "(2"，從 exit 回到 m6 = ")5"
p = nx.shortest_path(G, ("m1", ZERO), ("m7", "t2"))
labels = []
for u, v in zip(p, p[1:]):
    k = G.edges[u, v]["kind"]
    if k == "call":
        labels.append("(" + u[0][1])
    elif k == "ret":
        c = [c for c, val in calls.items() if val[1] == v[0]][0]
        labels.append(")" + c[1])
print("naive path:", " -> ".join(f"{n}:{d}" for n, d in p))
print("括號序列:", " ".join(labels), "（不配對 → 不是可實現路徑）")
```

```text
exploded supergraph: 76 nodes, 64 edges
sink at m7 uses t2: naive reach=True, IFDS=False
sink at m8 uses t1: naive reach=True, IFDS=True
naive 多出來的事實: [('m6', 't2'), ('m7', 't2'), ('m8', 't2')]
naive path: m1:0 -> m2:x -> i0:a -> i1:a -> i2:r -> m6:t2 -> m7:t2
括號序列: (2 )5 （不配對 → 不是可實現路徑）
```

naive BFS 找到的那條路從 call site m2 進 `id`、從 m5 的 return site m6 出來：括號序列 `(2 )5` 不配對，現實中不可能發生。IFDS tabulation 的關鍵在 `incoming`：callee 走到出口時，**只**把結果送回「當初帶著這個入口事實呼叫進來的 call site」。`(i0, a)` 只被 m2 帶進來過（m5 帶進來的只有 `(i0, 0)`，因為 y 在 m4 被殺掉了），所以 `(i2, r)` 只會回到 m3。

### CFL-reachability：把「配對括號」說成文法

把 G# 每條邊標上字母：一般邊標 ε，call 邊標 `(i`，return 邊標 `)i`。「可實現路徑」等於「路徑字串屬於某個上下文無關語言 L」，L 是部分配對括號（Dyck 語言的前綴封閉版本）：

```text
matched   → ε | matched matched | (i matched )i     （對每個 call site i）
realizable → matched | realizable (i matched        （可以有尚未返回的呼叫）
```

這個推廣叫 **CFL-reachability**（Reps 1998 把它整理成統一框架）：給一張邊帶標籤的圖和一個上下文無關文法，問哪些節點對之間存在一條路徑，其標籤字串屬於該文法的語言。普通可達性是文法 `S → ε | S a` 的特例（正規語言），所以 BFS 就夠了；配對括號不是正規語言（有界自動機記不住任意深的巢狀），所以需要更強的演算法。一般 CFL-reachability 用類似 CYK 的動態規劃，時間是節點數的三次方；IFDS 的結構（每個程序只有一個入口、括號只出現在 call/return）讓它可以做到上面的 `O(E · D³)`。

同一套語言還能描述別的分析：field-sensitive 的 points-to 分析把「寫入欄位 f」和「讀取欄位 f」當成另一種括號；如果同時要求 call/return 和 field 兩種括號都配對，問題變成不可判定（Reps，“Undecidability of Context-Sensitive Data-Dependence Analysis”，ACM TOPLAS 22(1)，2000；本章不展開）。這也是為什麼實務工具總是在某一種括號上做近似。

## 對比與取捨

| 方案 | 能回答什麼 | 不能回答什麼 | 代價 | 代表工具 |
| --- | --- | --- | --- | --- |
| grep / 語法樣式比對 | 「有沒有呼叫 `system`」 | 參數是不是來自使用者 | 線性，幾秒 | grep、Semgrep 非 taint 模式 |
| flow-insensitive 變數圖 | 粗略資料相依 | 覆寫、順序 | 接近線性 | Andersen 式 points-to 的前處理 |
| 函式內 reaching-defs + 可達性 | 函式內 taint，處理覆寫 | 跨函式 | 迭代收斂，實務很快 | 編譯器的 def-use chain |
| supergraph 普通可達性 | 跨函式 taint | 會把 A 的呼叫結果送到 B（誤報） | 一次 BFS | 早期簡單工具 |
| IFDS / CFL-reachability | context-sensitive 的跨函式 taint | 非分配的分析（例如常數傳播的一般形式要 IDE 擴充） | `O(E·D³)` | FlowDroid、Heros、WALA |
| 路徑敏感 + SMT | 排除不可行分支 | 規模大時路徑爆炸 | 指數 | 符號執行（KLEE 等） |

**為什麼不全部用路徑敏感？** 因為路徑數是指數級的：10 個串接的 `if` 就有 1024 條路徑。IFDS 選擇的位置很精準：在**呼叫結構**上做到精確（因為誤報多半來自這裡——一個 `sanitize()` 或 `strdup()` 被幾百個地方呼叫，naive 方法會讓所有呼叫者互相污染），在分支條件上接受近似。

**為什麼不是 k-CFA（把最近 k 層 call site 當 context）？** k-CFA 在 k 層以內精確，超過就合併；IFDS 對任意深度的呼叫都精確，代價是要求分配律。這兩條路在 points-to 分析裡仍然並存，沒有一方完勝。

## 踩雷集錦

1. **以為 CFG 的邊代表「會執行」**：CFG 的邊是「可能」。`if (0) { sink(x); }` 在 CFG 上仍然有邊，可達性分析會照報。要排除死碼，先做常數傳播再建圖，或接受這是 over-approximation。
2. **把 sanitizer 建模成「刪邊」而且只刪一條**：`safe = escape(user)` 的輸出整個是乾淨的，正確建模是讓這個節點不往外傳（刪節點或刪它的所有出邊）。只刪 `1 → 8` 這條邊會讓 8 本身沒被污染，結果剛好對；但遇到 `safe = escape(user) + user` 這種一半乾淨的語句，正確做法是在**事實**層級區分，而不是在節點層級。
3. **以為 sound 等於沒有誤報**：sound（不漏報）和 precise（不誤報）是兩件事。本章的可達性在本章假設下是 sound 的，同時會誤報；只要程式有指標別名、反射、`eval`，連 sound 都保證不了，因為 def-use 圖本身就建不完整。
4. **跨函式時把所有 return 接回所有 call site**：這就是範例三的 naive 版本。在真實程式裡，像 `strdup`、`json.loads` 這種被到處呼叫的函式會變成「污染集線器」，一個污染的呼叫讓幾百個 sink 全部報警。看到 taint 工具的誤報一大片都經過同一個 utility 函式，八成是 context-insensitive 造成的。
5. **AST 節點不一定是樹節點**：CPython 的 `ast.Load()`、運算子物件是共用單例，用 `id()` 當節點名會把樹黏成 DAG。範例三的邊數驗算就是在抓這個。

## 進階：再往深一層

- **IDE（Interprocedural Distributive Environment）**：Sagiv、Reps、Horwitz 在 “Precise Interprocedural Dataflow Analysis with Applications to Constant Propagation”（TAPSOFT 1995）把 IFDS 推廣到「事實帶值」的情況（例如線性常數傳播），邊上不只是有/無，而是一個 micro-function。`security/code_auditing` 的 IFDS/IDE 章節有工具層面的介紹。
- **Demand-driven 分析**：IFDS tabulation 是 exhaustive 的（從 main 算出所有事實）。只想回答「這個 sink 會不會被污染」時，可以在反向圖上從 sink 出發做 CFL-reachability，只探索相關部分。
- **Joern 的實際 schema**：真正的 CPG 節點不是語句而是 AST 節點，CFG 邊連在運算式層級，還有 `CALL`、`ARGUMENT`、`CDG`（控制相依）等邊；資料流查詢 `reachableByFlows` 在底層也是一種 reachability 搜尋，再加上語意模型（哪些函式會傳遞污染）。
- **控制相依與隱式資訊流**：`if (secret) x = 1; else x = 0;` 沒有任何資料邊從 `secret` 到 `x`，但 x 洩漏了 secret。把控制相依邊（後支配邊界）加進可達性，就能抓這種隱式流，代價是誤報暴增。這也是大多數 taint 工具預設**不**追隱式流的原因。

## 動手練習

1. 在範例一的程式加入 `if page > 1: q = "x"`（放在 while 之後、`sink(q)` 之前）。先在紙上預測 def-use 邊的變化，再實跑。`sink(q)` 還會被報嗎？為什麼「may」分析在這裡仍然要報？
2. 為範例一加上**控制相依邊**：在反向 CFG（加一個虛擬 exit）上算後支配樹（可以用 [Ch 14](./14-dominator-tree.md) 的演算法或 `nx.immediate_dominators`），再依本章給的定義算出控制相依。驗證：語句 4、5 控制相依於 3；語句 7 控制相依於 6。
3. 把範例三改成 `id` 被呼叫三次，第三次傳 `x`。naive 和 IFDS 的結果各自怎麼變？畫出 naive 多走的那條路徑的括號序列。
4. 在 `f_normal` 裡把 `i1` 改成 `r = a + "suffix"`（flow function 不變）和 `r = len(a)`（你認為 taint 該不該傳？），討論 flow function 的設計是安全判斷，不是圖論判斷。

## 本章重點整理

- AST 是樹、CFG 是帶環有向圖、PDG 加上資料與控制相依、CPG 是三者疊在同一組節點上的帶標籤多重圖。
- 函式內 taint 在路徑不敏感的假設下**等價於** def-use 圖去掉 sanitizer 節點後的可達性；這個等價的證明告訴你誤報從哪裡來。
- reaching definitions 處理覆寫；只看變數名會誤報。
- 跨函式時普通可達性會走不可實現路徑；IFDS 把分配的 flow function 表示成二部圖，把問題變成 exploded supergraph 上的**括號配對可達性**（CFL-reachability），用 summary edge 在多項式時間內解。

## 自我檢核

- [ ] 不看筆記，畫出範例一程式的 CFG，並指出哪條邊是 back edge、哪個節點有兩個出邊。
- [ ] 用自己的話解釋：為什麼「taint = 可達性」的命題只在路徑不敏感的假設下成立？舉一個這個假設造成誤報的三行程式。
- [ ] 如果面試官問「IFDS 為什麼要求 flow function 可分配」，你能用「函式的圖表示」引理回答嗎？
- [ ] 能說出 naive supergraph 可達性的誤報長什麼樣子（括號序列），以及 summary edge 怎麼避免它。
- [ ] 想一個你用過的 SAST 工具的誤報：它比較像是踩雷 1、3 還是 4？

## 延伸閱讀

### 論文

- **[Precise Interprocedural Dataflow Analysis via Graph Reachability](https://research.cs.wisc.edu/wpis/abstracts/popl95.abs.html)** — Thomas Reps、Susan Horwitz、Mooly Sagiv，POPL（1995），pp. 49–61
  - **核心貢獻**：定義 IFDS 問題類別，證明它等價於 exploded supergraph 上的可實現路徑可達性，並給出 tabulation 演算法。
  - **讀哪裡**：第 2 節（問題定義與 exploded supergraph 的圖例，和本章範例三同構）、第 4 節（tabulation 演算法與正確性）。複雜度分析的細節可以先跳過。
  - **前提與關聯**：需要懂資料流分析的 lattice 基本概念；本章 `ifds_tabulate` 就是這篇演算法的縮小版。
- **[Program Analysis via Graph Reachability](https://research.cs.wisc.edu/wpis/abstracts/ist98.abs.html)** — Thomas Reps，Information and Software Technology 40(11–12)（1998），pp. 701–726
  - **核心貢獻**：把 slicing、IFDS、flow-insensitive points-to 統一成 CFL-reachability，是這個框架的綜述。
  - **讀哪裡**：前三節（CFL-reachability 的定義與 Dyck 語言例子），再看 interprocedural slicing 那節。
  - **和本章的關聯**：本章「CFL-reachability」小節的文法就是從這裡來的。
- **[Modeling and Discovering Vulnerabilities with Code Property Graphs](https://mlsec.tu-berlin.de/docs/2014-ieeesp.pdf)** — Fabian Yamaguchi、Nico Golde、Daniel Arp、Konrad Rieck，IEEE S&P（2014），pp. 590–604
  - **核心貢獻**：把 AST、CFG、PDG 合成 CPG，用圖遍歷查詢在 Linux kernel 找到 18 個先前未知的漏洞。
  - **讀哪裡**：第 3 節（三種表示怎麼合併）與第 4 節（遍歷查詢的寫法）；實驗數字可略讀。
  - **和本章的關聯**：範例三的 `MultiDiGraph` 就是這篇 CPG 的玩具版。
- **[The Program Dependence Graph and Its Use in Optimization](https://web.eecs.umich.edu/~mahlke/courses/583w23/reading/ferrante_toplas_87.pdf)** — Jeanne Ferrante、Karl J. Ottenstein、Joe D. Warren，ACM TOPLAS 9(3)（1987），pp. 319–349
  - **核心貢獻**：PDG 的原始定義，用後支配樹定義控制相依。
  - **讀哪裡**：第 2 節的控制相依定義與演算法（動手練習 2 的依據）。
  - **前提**：要先懂 [Ch 14](./14-dominator-tree.md) 的 dominator。

### repo 內延伸

- **[security/code_auditing](../../security/code_auditing/README.md)**：本章的「工具端」。先讀 [程式表示與 CPG](../../security/code_auditing/03-program-representations-cpg.md)、[IFDS/IDE](../../security/code_auditing/05-ifds-ide.md)、[taint 分析理論](../../security/code_auditing/07-taint-analysis-theory.md)，再做它的 [迷你 taint tracker 練習](../../security/code_auditing/practice-a-mini-taint-tracker.md)——你會發現那個練習的核心就是本章的可達性。
- **[compilers/ssa_optimizations](../../compilers/ssa_optimizations/README.md)**：[reaching definitions](../../compilers/ssa_optimizations/09-reaching-definitions.md) 與 [資料流框架](../../compilers/ssa_optimizations/07-dataflow-framework.md) 兩章從編譯器角度講同一組方程式，包含收斂性與迭代順序。

### 書籍

- **[《Static Program Analysis》](https://cs.au.dk/~amoeller/spa/)** — Anders Møller、Michael I. Schwartzbach（Aarhus University 線上講義，2008 年起持續修訂）
  - **定位**：免費、以 lattice 與 fixpoint 為主線的教科書，IFDS 與 context sensitivity 有獨立章節。
  - **讀哪幾章**：dataflow analysis with monotone frameworks、interprocedural analysis 兩章；需要先懂偏序集。

程式分析把「漏洞」變成了圖上的路徑；下一章把同樣的想法搬到網路與帳號權限上：攻擊者在 AD 裡的橫向移動也是一條路徑，而防守方要找的是最該切斷的那幾條邊。

→ [Ch 39 攻擊圖：路徑與最小切斷集](./39-attack-graphs.md)
