# 練習 D — Flow 函式庫與問題建模

> **目標**：把 Ch 20–26 拼起來：自己寫一個小型 flow 函式庫（Dinic 最大流 + successive shortest path 最小費用流），再把排班、選課配對、project selection、邊不相交路徑、影像二值分割五個問題建模成 flow，全部用暴力窮舉或 NetworkX/SciPy 對答案。完成後你驗證了兩件事：演算法寫得對，以及你會把「看起來不像圖」的問題變成圖。
>
> **環境**：Python 3.13.12、NetworkX 3.7、SciPy 1.18.1、pytest 9.2.0，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2 pytest==9.2.0`。

## 背景與動機

Part 4 的定理都長一個樣子：「最多能做幾件事」等於「最少要付多少代價才能阻止」。Ch 20 的 max-flow min-cut、Ch 22 的 König、Ch 26 的 LP 強對偶都是。實務上真正難的不是寫 Dinic，而是**建模**：拿到一個排班需求、一份選課志願、一張專案依賴表，看出它是一個 flow 問題，並且證明「flow 的最佳解」對應「原問題的最佳解」。

這五個題目都是真實場景的縮小版：

| 題目 | 真實情境 | 用到的章節 |
| --- | --- | --- |
| 排班 | 值班表、on-call 輪值 | Ch 20 整數性、Ch 22 二分匹配的推廣 |
| 選課配對 | 志願分發、資源分配 | Ch 24 最小費用流 |
| Project selection | 「哪些資安專案值得做」：有些專案有收益，有些是前置成本 | Ch 20 最小割、Ch 26 closure |
| 邊不相交路徑 | 網路備援路由、攻擊路徑的獨立性 | Ch 12、Ch 20 的 Menger |
| 影像二值分割 | graph cut 分割、去雜訊 | Ch 20 最小割、Ch 21 的格子圖實測 |

Project selection 的形狀和 [Ch 39 攻擊圖](./39-attack-graphs.md) 的最小切斷集是同一類：都是「選一個集合，代價由跨越邊界的邊決定」。

## 任務規格

### Part 1：函式庫 `flowlib.py`

| API | 行為 |
| --- | --- |
| `MaxFlow(n)` | `n` 個點（編號 `0..n-1`）的空網路；`n < 1` 丟 `ValueError` |
| `MaxFlow.add_edge(u, v, cap) -> int` | 加一條有向邊，回傳邊編號；點超出範圍或 `cap < 0` 丟 `ValueError`；允許重邊與反平行邊 |
| `MaxFlow.max_flow(s, t) -> int` | 回傳最大流值；`s == t` 丟 `ValueError`；**必須是 Dinic**（level graph + blocking flow + current-arc） |
| `MaxFlow.flow_on(eid) -> int` | `max_flow` 之後，該邊上的流量 |
| `MaxFlow.min_cut_side(s) -> set` | `max_flow` 之後，殘餘圖中 `s` 可達的點集 |
| `MinCostFlow(n)`、`add_edge(u, v, cap, cost) -> int`、`flow_on(eid)` | 同上，邊多一個 `cost`（可以是負數） |
| `MinCostFlow.solve(s, t, max_flow=inf) -> (flow, cost)` | 送出不超過 `max_flow` 的流，在「流量最大」的前提下費用最小；**必須用 successive shortest path + potentials（Dijkstra）**；從 `s` 可達的負費用環丟 `ValueError("negative-cost cycle ...")` |

**禁止**：在 `flowlib.py` 裡 `import networkx` 或 `scipy`。它們只能出現在測試裡當對答案的工具。

### Part 2：模型 `models.py`

| 函式 | 輸入 | 輸出 |
| --- | --- | --- |
| `schedule_shifts(workers, shifts, max_shifts)` | `workers: {人名: set(可上的班)}`；`shifts: {班: (日期, 需要人數)}`；每人總班數 ≤ `max_shifts`，**每人每天最多一班** | `{班: sorted(人名)}`；不可能排滿時回傳 `None` |
| `allocate_courses(prefs, capacity, per_student)` | `prefs: {學生: [課程, 依志願排序]}`，排第 `r` 名（從 0 起）的費用是 `r`；`capacity: {課程: 名額}`；每人最多 `per_student` 門 | `({學生: sorted(課程)}, 總費用)`；先最大化總錄取門數，再最小化費用 |
| `project_selection(profit, requires)` | `profit: {專案: 利潤}`（可為負）；`requires: [(a, b)]` 表示選 `a` 就必須選 `b` | `(最大總利潤, 選中的專案集合)`；什麼都不選的利潤是 0 |
| `edge_disjoint_paths(succ, s, t)` | 有向圖 `succ: {u: set(v)}` | 數量最多的一組邊不相交 `s→t` 路徑，每條是點的 list |
| `segment(image, fg_mean, bg_mean, smooth)` | 灰階二維 list；像素標前景成本 `abs(I − fg_mean)`、標背景成本 `abs(I − bg_mean)`；上下左右相鄰且標籤不同付 `smooth` | `(最小總能量, 標籤矩陣)`，1 = 前景 |

### 效能要求

- `segment` 在 100×100 影像上 < 10 秒。
- `MinCostFlow` 解 100×100 的指派問題（費用 0–1000）< 10 秒，且總費用等於 `scipy.optimize.linear_sum_assignment`。

門檻刻意放寬：參考解答在 AMD Ryzen 7 5700G 上分別是 0.41 秒與 0.35 秒（單次量測）。如果你超過門檻，通常是少了 current-arc 或用了 Bellman–Ford 取代 Dijkstra。

## 驗收測試

把下面存成 `test_flowlib.py`，和你的 `flowlib.py`、`models.py` 放同一個目錄，執行 `python -m pytest -q test_flowlib.py`。測試本身就是規格：每個模型都用暴力窮舉或 NetworkX/SciPy 對答案。

```python
import itertools
import random

import networkx as nx
import pytest

from flowlib import MaxFlow, MinCostFlow
from models import (allocate_courses, edge_disjoint_paths, project_selection,
                    schedule_shifts, segment)


# ---------- 函式庫本身 ----------

def random_digraph(rng, n, m, maxcap, maxcost=None):
    G = nx.DiGraph()
    G.add_nodes_from(range(n))
    for _ in range(m):
        u, v = rng.randrange(n), rng.randrange(n)
        if u != v and not G.has_edge(u, v) and not G.has_edge(v, u):
            G.add_edge(u, v, capacity=rng.randint(1, maxcap),
                       weight=rng.randint(0, maxcost) if maxcost else 0)
    return G


def test_maxflow_matches_networkx():
    rng = random.Random(1)
    for _ in range(300):
        n = rng.randint(2, 25)
        G = random_digraph(rng, n, 3 * n, 30)
        g = MaxFlow(n)
        for u, v, d in G.edges(data=True):
            g.add_edge(u, v, d["capacity"])
        assert g.max_flow(0, n - 1) == nx.maximum_flow_value(G, 0, n - 1)


def test_min_cut_side_is_a_certificate():
    rng = random.Random(2)
    for _ in range(100):
        n = rng.randint(2, 15)
        G = random_digraph(rng, n, 3 * n, 30)
        g = MaxFlow(n)
        for u, v, d in G.edges(data=True):
            g.add_edge(u, v, d["capacity"])
        f = g.max_flow(0, n - 1)
        S = g.min_cut_side(0)
        assert n - 1 not in S
        assert f == sum(d["capacity"] for u, v, d in G.edges(data=True) if u in S and v not in S)


def test_mincost_matches_networkx():
    rng = random.Random(3)
    for _ in range(200):
        n = rng.randint(2, 15)
        G = random_digraph(rng, n, 3 * n, 10, maxcost=20)
        g = MinCostFlow(n)
        for u, v, d in G.edges(data=True):
            g.add_edge(u, v, d["capacity"], d["weight"])
        flow, cost = g.solve(0, n - 1)
        nx_flow = nx.max_flow_min_cost(G, 0, n - 1)
        assert flow == sum(nx_flow[0].values()) - sum(nx_flow[u].get(0, 0) for u in G)
        assert cost == nx.cost_of_flow(G, nx_flow)


def test_mincost_negative_edges_and_limit():
    g = MinCostFlow(4)
    g.add_edge(0, 1, 2, 5)
    g.add_edge(0, 2, 2, 1)
    g.add_edge(2, 1, 1, -3)        # 負費用邊（沒有負環）
    g.add_edge(1, 3, 3, 0)
    g.add_edge(2, 3, 2, 4)
    assert g.solve(0, 3, max_flow=1) == (1, -2)        # 0→2→1→3：1 - 3 + 0
    g2 = MinCostFlow(3)
    g2.add_edge(0, 1, 1, 1)
    g2.add_edge(1, 2, 1, -5)
    g2.add_edge(2, 1, 1, 1)        # 1→2→1 是負環
    with pytest.raises(ValueError, match="negative-cost cycle"):
        g2.solve(0, 2)


@pytest.mark.parametrize("bad", [lambda: MaxFlow(0), lambda: MaxFlow(2).add_edge(0, 1, -1),
                                 lambda: MaxFlow(2).add_edge(0, 5, 1), lambda: MaxFlow(2).max_flow(1, 1),
                                 lambda: MinCostFlow(2).solve(0, 0)])
def test_invalid_input(bad):
    with pytest.raises(ValueError):
        bad()


# ---------- 五個模型 ----------

def test_schedule_feasible_and_rules():
    workers = {"amy": {"mon-am", "mon-pm", "tue-am"}, "bob": {"mon-am", "tue-am", "tue-pm"},
               "cat": {"mon-pm", "tue-pm"}, "dan": {"mon-am", "mon-pm", "tue-am", "tue-pm"}}
    shifts = {"mon-am": ("mon", 2), "mon-pm": ("mon", 1), "tue-am": ("tue", 1), "tue-pm": ("tue", 2)}
    roster = schedule_shifts(workers, shifts, max_shifts=2)
    assert roster is not None
    for k, (day, need) in shifts.items():
        assert len(roster[k]) == need
        assert all(k in workers[w] for w in roster[k])
    for w in workers:
        mine = [k for k in roster if w in roster[k]]
        assert len(mine) <= 2
        assert len({shifts[k][0] for k in mine}) == len(mine)     # 每天最多一班


def test_schedule_infeasible():
    workers = {"amy": {"mon-am", "mon-pm"}, "bob": {"mon-am"}}
    shifts = {"mon-am": ("mon", 1), "mon-pm": ("mon", 1), "tue-am": ("tue", 1)}
    assert schedule_shifts(workers, shifts, max_shifts=3) is None   # 沒人能上 tue-am
    shifts2 = {"mon-am": ("mon", 1), "mon-pm": ("mon", 1)}
    workers2 = {"amy": {"mon-am", "mon-pm"}}
    assert schedule_shifts(workers2, shifts2, max_shifts=2) is None  # 同一天只能一班


def test_allocate_courses_bruteforce():
    rng = random.Random(4)
    courses = ["algo", "crypto", "os", "pl"]
    for _ in range(30):
        students = [f"s{i}" for i in range(rng.randint(1, 4))]
        prefs = {x: rng.sample(courses, rng.randint(1, 4)) for x in students}
        cap = {c: rng.randint(0, 2) for c in courses}
        result, cost = allocate_courses(prefs, cap, per_student=2)
        # 暴力：每人選一個大小 ≤ 2 的子集，先比總門數（大者優），再比費用（小者優）
        options = [[sub for k in range(3) for sub in itertools.combinations(prefs[x], k)]
                   for x in students]
        best = None
        for combo in itertools.product(*options):
            load = {c: 0 for c in courses}
            for sub in combo:
                for c in sub:
                    load[c] += 1
            if any(load[c] > cap[c] for c in courses):
                continue
            key = (-sum(map(len, combo)),
                   sum(prefs[x].index(c) for x, sub in zip(students, combo) for c in sub))
            best = key if best is None or key < best else best
        assert (-sum(map(len, result.values())), cost) == best


def test_project_selection_bruteforce():
    rng = random.Random(5)
    for _ in range(100):
        k = rng.randint(1, 8)
        profit = {f"p{i}": rng.randint(-10, 10) for i in range(k)}
        names = sorted(profit)
        req = [(a, b) for a in names for b in names if a != b and rng.random() < 0.2]
        best = 0
        for mask in range(1 << k):
            chosen = {names[i] for i in range(k) if mask >> i & 1}
            if all(b in chosen for a, b in req if a in chosen):
                best = max(best, sum(profit[p] for p in chosen))
        value, chosen = project_selection(profit, req)
        assert value == best
        assert all(b in chosen for a, b in req if a in chosen)
        assert sum(profit[p] for p in chosen) == value


def test_edge_disjoint_paths():
    rng = random.Random(6)
    for _ in range(100):
        n = rng.randint(2, 10)
        G = nx.gnp_random_graph(n, 0.35, seed=rng.randrange(10**6), directed=True)
        succ = {u: set(G[u]) for u in G}
        paths = edge_disjoint_paths(succ, 0, n - 1)
        assert len(paths) == nx.edge_connectivity(G, 0, n - 1)
        used = [e for p in paths for e in zip(p, p[1:])]
        assert len(used) == len(set(used))                       # 邊不相交
        assert all(p[0] == 0 and p[-1] == n - 1 and G.has_edge(a, b)
                   for p in paths for a, b in zip(p, p[1:]))


def test_segment_bruteforce():
    rng = random.Random(7)
    for _ in range(30):
        h, w = rng.randint(1, 3), rng.randint(1, 3)
        img = [[rng.randint(0, 255) for _ in range(w)] for _ in range(h)]
        smooth = rng.randint(0, 120)
        energy, labels = segment(img, fg_mean=200, bg_mean=50, smooth=smooth)

        def E(lab):
            data = sum(abs(img[r][c] - (200 if lab[r][c] else 50)) for r in range(h) for c in range(w))
            pair = sum(smooth for r in range(h) for c in range(w)
                       for rr, cc in ((r, c + 1), (r + 1, c))
                       if rr < h and cc < w and lab[r][c] != lab[rr][cc])
            return data + pair

        best = min(E([list(bits[r * w:(r + 1) * w]) for r in range(h)])
                   for bits in itertools.product((0, 1), repeat=h * w))
        assert energy == best == E(labels)


# ---------- 效能邊界（門檻很寬，參考解在 Ryzen 7 5700G 上都在 0.5 秒內） ----------

def test_performance_segment_100x100():
    import time
    rng = random.Random(8)
    img = [[rng.randint(0, 255) for _ in range(100)] for _ in range(100)]
    t0 = time.perf_counter()
    energy, labels = segment(img, fg_mean=200, bg_mean=50, smooth=40)
    assert time.perf_counter() - t0 < 10
    assert len(labels) == 100 and all(len(row) == 100 for row in labels)


def test_performance_assignment_100x100():
    import time
    from scipy.optimize import linear_sum_assignment
    rng = random.Random(9)
    n = 100
    C = [[rng.randint(0, 1000) for _ in range(n)] for _ in range(n)]
    g = MinCostFlow(2 * n + 2)
    for i in range(n):
        g.add_edge(2 * n, i, 1, 0)
        g.add_edge(n + i, 2 * n + 1, 1, 0)
        for j in range(n):
            g.add_edge(i, n + j, 1, C[i][j])
    t0 = time.perf_counter()
    flow, cost = g.solve(2 * n, 2 * n + 1)
    assert time.perf_counter() - t0 < 10
    rows, cols = linear_sum_assignment(C)
    assert (flow, cost) == (n, sum(C[i][j] for i, j in zip(rows, cols)))
```

參考解答的結果：

```text
.................                                                        [100%]
17 passed in 1.55s
```

（秒數會隨機器變動。）

## 期望輸出範例

用參考解答跑下面這個 `demo.py`：

```python
from models import allocate_courses, edge_disjoint_paths, project_selection, schedule_shifts, segment

workers = {"amy": {"mon-am", "mon-pm", "tue-am"}, "bob": {"mon-am", "tue-am", "tue-pm"},
           "cat": {"mon-pm", "tue-pm"}, "dan": {"mon-am", "mon-pm", "tue-am", "tue-pm"}}
shifts = {"mon-am": ("mon", 2), "mon-pm": ("mon", 1), "tue-am": ("tue", 1), "tue-pm": ("tue", 2)}
print("1) roster:", schedule_shifts(workers, shifts, max_shifts=2))
print("   roster (max_shifts=1):", schedule_shifts(workers, shifts, max_shifts=1))

prefs = {"ann": ["crypto", "os", "algo"], "ben": ["crypto", "algo"], "cy": ["crypto", "os"]}
print("2) courses:", allocate_courses(prefs, {"crypto": 2, "os": 1, "algo": 2}, per_student=2))

profit = {"auth-rewrite": 12, "sso": 5, "ldap-migration": -9, "audit-log": -7, "pentest": 1}
requires = [("auth-rewrite", "ldap-migration"), ("sso", "ldap-migration"), ("sso", "audit-log"),
            ("pentest", "audit-log")]
value, chosen = project_selection(profit, requires)
print("3) projects:", value, sorted(chosen))

succ = {"s": {"a", "b", "c"}, "a": {"d"}, "b": {"d", "e"}, "c": {"e"}, "d": {"t"}, "e": {"t"}}
print("4) paths:", sorted(edge_disjoint_paths(succ, "s", "t")))

img = [[200, 190, 60],
       [210, 120, 40],
       [ 70,  50, 30]]
for lam in (0, 40, 200):
    e, lab = segment(img, fg_mean=200, bg_mean=50, smooth=lam)
    print(f"5) smooth={lam:<3} energy={e:<4} labels={lab}")
```

```text
1) roster: {'mon-am': ['amy', 'bob'], 'mon-pm': ['cat'], 'tue-am': ['amy'], 'tue-pm': ['bob', 'cat']}
   roster (max_shifts=1): None
2) courses: ({'ann': ['algo', 'os'], 'ben': ['algo', 'crypto'], 'cy': ['crypto']}, 4)
3) projects: 3 ['auth-rewrite', 'ldap-migration']
4) paths: [['s', 'a', 'd', 't'], ['s', 'b', 'e', 't']]
5) smooth=0   energy=150  labels=[[1, 1, 0], [1, 0, 0], [0, 0, 0]]
5) smooth=40  energy=310  labels=[[1, 1, 0], [1, 0, 0], [0, 0, 0]]
5) smooth=200 energy=580  labels=[[0, 0, 0], [0, 0, 0], [0, 0, 0]]
```

幾個值得自己先驗算的地方：

- **排班 `max_shifts=1` 是 `None`**：四個班總共要 6 人次，四個人每人最多一班只有 4 人次。最大流 4 < 6，所以不可行。這是 Ch 20 弱對偶的直接應用：割 `{s}` 的容量就是 4。
- **選課的平手**：`ann` 拿到 `algo`、`os`（排名 2 + 1），`ben` 拿 `algo`、`crypto`（1 + 0），`cy` 拿 `crypto`（0），總費用 4。另一組解（`ann` 拿 `crypto`、`algo`，`cy` 拿 `os`）費用也是 4。你的實作輸出可以不同，測試只檢查「門數與費用」這兩個數字。
- **`sso` 利潤是正的卻沒被選**：選 `sso` 要連帶付 `audit-log` 的 7（`ldap-migration` 已經因為 `auth-rewrite` 付過了），淨收益 5 − 7 = −2；加上 `pentest`（同樣依賴 `audit-log`）也只有 5 + 1 − 7 = −1。最佳是只做 `auth-rewrite` + `ldap-migration`，利潤 12 − 9 = 3。
- **分割的 `smooth=200`**：平滑懲罰太大時，全部標成同一類最便宜——這就是 graph cut 的參數要調的原因。

**輸出要可重現**：參考解答第一版在 `schedule_shifts` 與 `edge_disjoint_paths` 裡直接走訪 `set`，結果每次執行排班表和路徑都可能不同——Python 對字串的雜湊每個行程隨機（`PYTHONHASHSEED`），`set` 的走訪順序跟著變。答案都合法，但無法對照。修正是加邊前先 `sorted(...)`；上面的輸出在 `PYTHONHASHSEED=1..4` 下完全相同。

邊界輸入：

```text
MaxFlow(2).max_flow(1, 1)            → ValueError: source and sink must differ
MaxFlow(2).add_edge(0, 1, -1)        → ValueError: negative capacity on 0->1: -1
MinCostFlow 有 1→2→1 的負費用環       → ValueError: negative-cost cycle reachable from source
project_selection 全部利潤為負        → (0, set())
edge_disjoint_paths 中 t 不可達       → []
```

## 如果你卡住了

1. **最小費用流的 Dijkstra 錯了**：殘餘圖一定有負費用的反向邊，Dijkstra 不能直接用。先想 Ch 24 的 potentials：reduced cost `c(u,v) + π(u) − π(v)` 為什麼在每次增廣後仍然 ≥ 0？初始的 `π` 要怎麼來（輸入本身可能有負費用邊）？
2. **Project selection 的方向搞反**：先決定「`S` 側 = 選、`T` 側 = 不選」，再問：選了 `a` 卻沒選 `b` 時，哪一條邊會跨過割？它的容量必須是無限大，所以它應該從 `a` 指向 `b`。利潤為正的專案「不選」要付出損失，利潤為負的「選了」要付出成本——各自對應哪一條會被割到的邊？
3. **排班「每天最多一班」放不進去**：直接從人連到班，無法限制同一天的班數。在人和班之間插一層「（人, 日期）」節點，容量 1。這和 Ch 20 的拆點是同一個技巧。
4. **邊不相交路徑抽不出來**：`max_flow` 給的是每條邊的流量，不是路徑。從 `s` 沿著有流量的邊走、走過就消耗，就是 Ch 20 Menger 證明裡的 flow decomposition。走回已經到過的點時，那一段是一個環，丟掉它。
5. **分割的能量對不上暴力解**：檢查 `s→p` 和 `p→t` 哪一條代表「標前景要付的成本」。如果 `S` 側是前景，那麼 `p` 標前景時被割到的是 `p→t`，所以 `p→t` 的容量應該是前景成本。

## 實作步驟建議

### Step 1：Dinic

從 [Ch 21](./21-dinic-push-relabel.md) 的 `dinic.py` 出發，改成本題的 API（`add_edge` 回傳編號、`flow_on`、`min_cut_side`、輸入檢查）。子目標：`test_maxflow_matches_networkx`、`test_min_cut_side_is_a_certificate`、`test_invalid_input` 的 `MaxFlow` 部分通過。

### Step 2：最小費用流

邊陣列多存一個 `cost`，反向邊費用取負。先用 Bellman–Ford 從 `s` 算一次距離當初始 potentials（同時偵測負環），之後每輪用 Dijkstra 在 reduced cost 上找最短路、更新 potentials、沿路推瓶頸量。子目標：`test_mincost_matches_networkx`、`test_mincost_negative_edges_and_limit` 通過。

### Step 3：排班與選課

兩個都是「左邊一群、右邊一群、中間有容量」的二分結構。排班用最大流判斷可行性並讀出每條「人→班」邊的流量；選課在「學生→課程」的邊上放志願排名當費用。子目標：兩個排班測試、`test_allocate_courses_bruteforce` 通過。

### Step 4：Project selection 與邊不相交路徑

Project selection 的答案是「正利潤總和 − 最小割」，被選的專案是 `min_cut_side` 裡的那些。寫完之後在紙上證明：任何滿足依賴的選法都對應一個有限容量的割，反之亦然，而且割容量 = 正利潤總和 − 該選法的利潤。子目標：`test_project_selection_bruteforce`、`test_edge_disjoint_paths` 通過。

### Step 5：影像分割

每個像素一個點，連 `s`、連 `t`、和右邊與下面的鄰居各連一對雙向邊。子目標：`test_segment_bruteforce` 在所有 3×3 以內的影像上和 `2^9` 種標記的窮舉一致。

### Step 6：整合與效能

跑完整的 `pytest`，包括兩個效能測試；跑 `demo.py` 對照上面的期望輸出。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作：flowlib.py</summary>

```python
"""flowlib.py：Dinic 最大流 + 最小費用流（successive shortest path + potentials）。"""
from collections import deque
import heapq

INF = float("inf")


class MaxFlow:
    def __init__(self, n):
        if n < 1:
            raise ValueError("n must be >= 1")
        self.n = n
        self.adj = [[] for _ in range(n)]
        self.to, self.cap, self.orig = [], [], []

    def add_edge(self, u, v, cap):
        """加一條 u→v、容量 cap 的邊，回傳邊編號（之後可用 flow_on 查流量）。"""
        if not (0 <= u < self.n and 0 <= v < self.n):
            raise ValueError(f"node out of range: {u}->{v}")
        if cap < 0:
            raise ValueError(f"negative capacity on {u}->{v}: {cap}")
        eid = len(self.to)
        self.adj[u].append(eid); self.to.append(v); self.cap.append(cap); self.orig.append(cap)
        self.adj[v].append(eid + 1); self.to.append(u); self.cap.append(0); self.orig.append(0)
        return eid

    def flow_on(self, eid):
        return self.orig[eid] - self.cap[eid]

    def _bfs(self, s, t):
        level = [-1] * self.n
        level[s] = 0
        q = deque([s])
        while q:
            u = q.popleft()
            for e in self.adj[u]:
                if self.cap[e] > 0 and level[self.to[e]] < 0:
                    level[self.to[e]] = level[u] + 1
                    q.append(self.to[e])
        return level

    def max_flow(self, s, t):
        if s == t:
            raise ValueError("source and sink must differ")
        total = 0
        while (level := self._bfs(s, t))[t] >= 0:
            it = [0] * self.n
            while True:
                path, u = [], s
                while u != t:
                    adj_u, i = self.adj[u], it[u]
                    while i < len(adj_u) and not (self.cap[adj_u[i]] > 0
                                                  and level[self.to[adj_u[i]]] == level[u] + 1):
                        i += 1
                    it[u] = i
                    if i == len(adj_u):
                        level[u] = -1
                        if not path:
                            break
                        u = self.to[path.pop() ^ 1]
                        it[u] += 1
                        continue
                    path.append(adj_u[i])
                    u = self.to[adj_u[i]]
                if u != t:
                    break
                b = min(self.cap[e] for e in path)
                for e in path:
                    self.cap[e] -= b
                    self.cap[e ^ 1] += b
                total += b
        return total

    def min_cut_side(self, s):
        """max_flow 之後呼叫：殘餘圖中 s 可達的點集（最小割的 S 側）。"""
        seen, stack = {s}, [s]
        while stack:
            u = stack.pop()
            for e in self.adj[u]:
                if self.cap[e] > 0 and self.to[e] not in seen:
                    seen.add(self.to[e])
                    stack.append(self.to[e])
        return seen


class MinCostFlow:
    def __init__(self, n):
        if n < 1:
            raise ValueError("n must be >= 1")
        self.n = n
        self.adj = [[] for _ in range(n)]
        self.to, self.cap, self.cost, self.orig = [], [], [], []

    def add_edge(self, u, v, cap, cost):
        if not (0 <= u < self.n and 0 <= v < self.n):
            raise ValueError(f"node out of range: {u}->{v}")
        if cap < 0:
            raise ValueError(f"negative capacity on {u}->{v}: {cap}")
        eid = len(self.to)
        for a, b, c, w in ((u, v, cap, cost), (v, u, 0, -cost)):
            self.adj[a].append(len(self.to))
            self.to.append(b); self.cap.append(c); self.cost.append(w); self.orig.append(c)
        return eid

    def flow_on(self, eid):
        return self.orig[eid] - self.cap[eid]

    def _bellman_ford(self, s):
        """初始 potential：允許負費用邊；偵測到負環就丟例外。"""
        dist = [INF] * self.n
        dist[s] = 0
        for rnd in range(self.n):
            changed = False
            for u in range(self.n):
                if dist[u] == INF:
                    continue
                for e in self.adj[u]:
                    if self.cap[e] > 0 and dist[u] + self.cost[e] < dist[self.to[e]]:
                        dist[self.to[e]] = dist[u] + self.cost[e]
                        changed = True
            if not changed:
                return dist
        raise ValueError("negative-cost cycle reachable from source")

    def solve(self, s, t, max_flow=INF):
        """沿最短（reduced cost）路徑增廣，直到送滿 max_flow 或沒有路。回傳 (flow, cost)。"""
        if s == t:
            raise ValueError("source and sink must differ")
        pot = [0 if d == INF else d for d in self._bellman_ford(s)]
        flow = cost = 0
        while flow < max_flow:
            dist = [INF] * self.n
            dist[s] = 0
            pe = [-1] * self.n
            pq = [(0, s)]
            while pq:                                  # Dijkstra on reduced costs
                d, u = heapq.heappop(pq)
                if d > dist[u]:
                    continue
                for e in self.adj[u]:
                    v = self.to[e]
                    nd = d + self.cost[e] + pot[u] - pot[v]
                    if self.cap[e] > 0 and nd < dist[v]:
                        dist[v], pe[v] = nd, e
                        heapq.heappush(pq, (nd, v))
            if dist[t] == INF:
                break
            for v in range(self.n):
                if dist[v] < INF:
                    pot[v] += dist[v]
            b, v = max_flow - flow, t
            while v != s:
                b = min(b, self.cap[pe[v]])
                v = self.to[pe[v] ^ 1]
            v = t
            while v != s:
                self.cap[pe[v]] -= b
                self.cap[pe[v] ^ 1] += b
                v = self.to[pe[v] ^ 1]
            flow += b
            cost += b * (pot[t] - pot[s])
        return flow, cost
```

**解答說明**：

- `MaxFlow.max_flow` 就是 Ch 21 的迭代式 Dinic，壓縮了內層迴圈。`orig` 陣列記住每條邊的原始容量，`flow_on` 用 `orig − cap` 算流量；因為每條原始邊有自己的編號，重邊與反平行邊不會互相污染（Ch 20 踩雷第 1 條）。
- `MinCostFlow.solve` 每輪 Dijkstra 之後把 `pot[v] += dist[v]`。這保證所有殘餘邊的 reduced cost 仍 ≥ 0（Ch 24 的引理）。從 `s` 不可達的點不更新 potential 是安全的：新出現的殘餘邊只會連接可達的點，不可達的點之後也一直不可達。
- 每輪的實際路徑費用是 `pot[t] − pot[s]`：Dijkstra 算的是 reduced 距離，加回 potential 的差就是原始費用。`pot[s]` 永遠是 0。
- 回傳的是「最小費用的最大流」（或流量上限為 `max_flow` 時的最小費用流）。SSP 的正確性靠的是 Ch 24 的「沒有負環 ⇔ 最小費用」：每一步都沿最短路增廣，殘餘圖始終沒有負環。

</details>

<details>
<summary>點開參考實作：models.py</summary>

```python
"""models.py：把五個問題建模成 flow。需要同目錄的 flowlib.py。"""
from flowlib import MaxFlow, MinCostFlow

BIG = 10**12      # 「無限大」容量：大於任何可能的割


def schedule_shifts(workers, shifts, max_shifts):
    """排班。
    workers: {name: set(可上的 shift id)}；shifts: {shift_id: (day, 需要人數)}；
    max_shifts: 每人總班數上限。每人每天最多一班。
    回傳 {shift_id: sorted(人名)}；排不滿就回傳 None。
    """
    W, S = sorted(workers), sorted(shifts)
    days = sorted({shifts[k][0] for k in S})
    wid = {w: i for i, w in enumerate(W)}
    wd = {(w, d): len(W) + i * len(days) + j for i, w in enumerate(W) for j, d in enumerate(days)}
    sid = {k: len(W) + len(wd) + i for i, k in enumerate(S)}
    src, snk = len(W) + len(wd) + len(S), len(W) + len(wd) + len(S) + 1
    g = MaxFlow(snk + 1)
    for w in W:
        g.add_edge(src, wid[w], max_shifts)
        for d in days:
            g.add_edge(wid[w], wd[w, d], 1)                 # 每天最多一班
    assign = {}
    for w in W:
        for k in sorted(workers[w]):          # 排序：讓輸出不受 set 的雜湊順序影響
            if k in shifts:
                assign[g.add_edge(wd[w, shifts[k][0]], sid[k], 1)] = (w, k)
    need = 0
    for k in S:
        g.add_edge(sid[k], snk, shifts[k][1])
        need += shifts[k][1]
    if g.max_flow(src, snk) < need:
        return None
    roster = {k: [] for k in S}
    for eid, (w, k) in assign.items():
        if g.flow_on(eid):
            roster[k].append(w)
    return {k: sorted(v) for k, v in roster.items()}


def allocate_courses(prefs, capacity, per_student):
    """選課配對（最小費用）。
    prefs: {student: [課程, 依偏好排序]}，排第 r 名（從 0 起）的費用是 r；
    capacity: {course: 名額}；per_student: 每人最多修幾門。
    目標：先最大化總選上門數，再最小化偏好排名總和。回傳 ({student: sorted(courses)}, 總費用)。
    """
    S, C = sorted(prefs), sorted(capacity)
    sid = {x: i for i, x in enumerate(S)}
    cid = {c: len(S) + i for i, c in enumerate(C)}
    src, snk = len(S) + len(C), len(S) + len(C) + 1
    g = MinCostFlow(snk + 1)
    pick = {}
    for x in S:
        g.add_edge(src, sid[x], per_student, 0)
        for rank, c in enumerate(prefs[x]):
            pick[g.add_edge(sid[x], cid[c], 1, rank)] = (x, c)
    for c in C:
        g.add_edge(cid[c], snk, capacity[c], 0)
    flow, cost = g.solve(src, snk)
    result = {x: [] for x in S}
    for eid, (x, c) in pick.items():
        if g.flow_on(eid):
            result[x].append(c)
    return {x: sorted(v) for x, v in result.items()}, cost


def project_selection(profit, requires):
    """Project selection / maximum-weight closure。
    profit: {project: 利潤（可為負）}；requires: [(a, b)] 表示選 a 就必須選 b。
    回傳 (最大總利潤, 選中的專案集合)。
    """
    P = sorted(profit)
    pid = {p: i for i, p in enumerate(P)}
    src, snk = len(P), len(P) + 1
    g = MaxFlow(len(P) + 2)
    positive = 0
    for p in P:
        if profit[p] > 0:
            g.add_edge(src, pid[p], profit[p])
            positive += profit[p]
        elif profit[p] < 0:
            g.add_edge(pid[p], snk, -profit[p])
    for a, b in requires:
        g.add_edge(pid[a], pid[b], BIG)          # 選 a 不選 b 的割容量無限大
    cut = g.max_flow(src, snk)
    side = g.min_cut_side(src)
    return positive - cut, {p for p in P if pid[p] in side}


def edge_disjoint_paths(succ, s, t):
    """有向圖 succ: {u: set(v)}，回傳一組數量最多的邊不相交 s→t 路徑。"""
    nodes = sorted(set(succ) | {v for u in succ for v in succ[u]} | {s, t}, key=repr)
    idx = {v: i for i, v in enumerate(nodes)}
    g = MaxFlow(len(nodes))
    eids = {}
    for u in sorted(succ, key=repr):          # 排序：讓輸出不受 set 的雜湊順序影響
        for v in sorted(succ[u], key=repr):
            eids[g.add_edge(idx[u], idx[v], 1)] = (u, v)
    k = g.max_flow(idx[s], idx[t])
    used = {}
    for eid, (u, v) in eids.items():
        if g.flow_on(eid):
            used.setdefault(u, []).append(v)
    paths = []
    for _ in range(k):                         # flow decomposition
        path, u, seen = [s], s, {s: 0}
        while u != t:
            v = used[u].pop()
            if v in seen:                      # 走出環：把環剪掉（環上的流量本來就可以丟掉）
                path = path[:seen[v] + 1]
                seen = {x: i for i, x in enumerate(path)}
            else:
                seen[v] = len(path)
                path.append(v)
            u = v
        paths.append(path)
    return paths


def segment(image, fg_mean, bg_mean, smooth):
    """二值影像分割（graph cut）。
    image: 灰階二維 list；像素 p 標前景的成本 |I_p - fg_mean|，標背景的成本 |I_p - bg_mean|；
    相鄰像素標籤不同要付 smooth。回傳 (最小能量, 標籤矩陣 1=前景)。
    """
    h, w = len(image), len(image[0])
    src, snk = h * w, h * w + 1
    g = MaxFlow(h * w + 2)
    for r in range(h):
        for c in range(w):
            p = r * w + c
            g.add_edge(src, p, abs(image[r][c] - bg_mean))   # 切掉 = p 標背景，付背景成本
            g.add_edge(p, snk, abs(image[r][c] - fg_mean))   # 切掉 = p 標前景，付前景成本
            for q in ((p + 1) if c + 1 < w else None, (p + w) if r + 1 < h else None):
                if q is not None:
                    g.add_edge(p, q, smooth)
                    g.add_edge(q, p, smooth)
    energy = g.max_flow(src, snk)
    side = g.min_cut_side(src)
    return energy, [[1 if r * w + c in side else 0 for c in range(w)] for r in range(h)]
```

**解答說明**：

- **排班**：三層結構 `s → 人（容量 max_shifts）→ (人, 日期)（容量 1）→ 班（容量 1）→ t（容量 = 需要人數）`。整數性定理保證最大流可以取整數，每條「(人, 日)→班」邊的流量是 0 或 1，直接讀成排班表。
- **選課**：`s → 學生（容量 per_student，費用 0）→ 課程（容量 1，費用 = 志願排名）→ t（容量 = 名額）`。SSP 先保證流量最大（錄取門數最多），在這個前提下費用最小。
- **Project selection**：`s → p` 容量 `profit[p]`（利潤為正），`p → t` 容量 `−profit[p]`（利潤為負），依賴 `a → b` 容量無限大。割 `(S, T)` 有限 ⇔ `S` 對依賴封閉；有限割的容量 = 沒選的正利潤 + 選了的負利潤的絕對值 = 正利潤總和 − 選中集合的利潤。所以最大利潤 = 正利潤總和 − 最小割。這個構造一般歸功於 Picard（1976）與 Rhys（1970）；Kleinberg–Tardos 7.11 有完整推導。
- **邊不相交路徑**：最大流 + flow decomposition。被剪掉的環本來就不貢獻流值，丟掉不影響路徑數。
- **分割**：`S` 側是前景。`p` 標前景時被割的是 `p→t`，所以 `p→t` 容量是前景成本；標背景時被割的是 `s→p`，容量是背景成本；相鄰兩點標籤不同時，雙向邊恰好有一條從 `S` 指到 `T`，付一次 `smooth`。能量函數「資料項 + 相鄰不同就付固定懲罰」能精確化成最小割；更一般的能量需要 submodular 條件（Kolmogorov–Zabih 2004）。

</details>

## 測試用例

| 輸入 | 預期輸出 | 說明 |
| --- | --- | --- |
| 300 張隨機有向圖（`n ≤ 25`） | `max_flow` 等於 `nx.maximum_flow_value` | 正常路徑 |
| `max_flow` 後的 `min_cut_side` | 割容量等於流值，且不含 `t` | 最小割證書 |
| 200 張隨機圖（費用 0–20） | `(flow, cost)` 等於 `nx.max_flow_min_cost` | 最小費用流 |
| 含負費用邊、`max_flow=1` | `(1, -2)` | 負邊 + 流量上限 |
| 含負費用環 | `ValueError` | 非法輸入 |
| `MaxFlow(0)`、負容量、點超出範圍、`s == t` | `ValueError` | 非法輸入 |
| 排班：某班沒人能上 / 同日兩班只有一人 | `None` | 不可行 |
| 選課：30 組隨機志願 | 門數與費用等於窮舉 | 暴力對拍 |
| Project selection：100 組隨機依賴 | 利潤等於 `2^k` 窮舉，且輸出集合滿足依賴 | 暴力對拍 |
| 邊不相交路徑：100 張隨機有向圖 | 路徑數等於 `nx.edge_connectivity(G, s, t)`，且邊不重複 | Menger |
| 分割：30 張 ≤ 3×3 影像 | 能量等於 `2^(h·w)` 窮舉的最小值 | 暴力對拍 |
| 100×100 分割、100×100 指派 | < 10 秒；指派費用等於 `linear_sum_assignment` | 效能邊界 |

## 延伸挑戰（加分）

- **下界（lower bounds）**：排班改成「每人至少上 1 班」。這需要帶下界的可行流：把下界轉成點的需求（供給 / 需求），加一條 `t → s` 的無限大邊，再用超級源點與超級匯點化成一個普通最大流；超級源點的出邊全部飽和 ⇔ 可行。實作 `MaxFlow` 的 `add_edge(u, v, low, high)`。
- **多標籤分割**：把 `segment` 推廣到 3 個以上的標籤，實作 α-expansion（每一步解一個二值 graph cut）。它不保證全域最佳，但有近似比保證（Boykov–Veksler–Zabih 2001）。
- **Push-relabel 版本**：在 `flowlib` 裡加一個 push-relabel 實作，用 Ch 21 的 `bench.py` 形狀（格子圖、單位二分圖）比較它和你的 Dinic。
- **從 min-cost flow 解 Ch 24 的指派**：用 `MinCostFlow` 實作 `assign(cost_matrix)`，和你在 Ch 24 寫的匈牙利演算法、`linear_sum_assignment` 三方對拍，並量測 `n = 50, 100, 200` 的時間。
- **攻擊圖 chokepoint**：把 `edge_disjoint_paths` 改成點版本（Ch 20 的拆點），在一張「主機 → 主機」的橫向移動圖上找最少要修補的主機。這是 [練習 F](./practice-f-attack-graph.md) 的預習。

## 自我檢核

- [ ] 不看解答，能為 project selection 證明「有限割 ⇔ 合法選法」以及割容量的公式
- [ ] 能解釋為什麼最小費用流的 Dijkstra 需要 potentials，以及初始 potentials 從哪裡來
- [ ] 遇到一個新的「分配 / 選擇」問題時，能說出它該用最大流、最小割還是最小費用流，以及判斷依據
- [ ] 能指出排班模型裡「(人, 日期)」那一層對應 Ch 20 的哪個技巧
- [ ] 能說出自己的實作和參考解答的差異，並解釋各自取捨

Part 4 到這裡結束：flow、匹配、matroid、LP 對偶都是組合結構上的最佳化。Part 5 換一副眼鏡，把圖寫成矩陣，看特徵值能告訴我們什麼。

→ [Ch 27 譜圖論入門](./27-spectral-graph-theory.md)
