# Ch 41 — 大圖工程：CSR、cache 與平行 BFS

> **目標**：知道一張百萬節點、千萬條邊的圖在記憶體裡該長什麼樣子；能用 numpy 建 CSR、用 C 寫 CSR 上的 BFS 與 direction-optimizing BFS，並用自己實測的數字解釋「同樣 `O(V + E)` 的演算法為什麼會差上百倍」。
>
> **環境**：Python 端是 Windows 11 上的 Python 3.13.12、NumPy 2.5.4、NetworkX 3.7；C 端是同一台機器的 WSL2 Ubuntu 22.04、gcc 11.4.0（`-O3 -march=native -fopenmp`）。機器：AMD Ryzen 7 5700G（8 核 16 執行緒；`lscpu` 回報 L1d 32 KiB × 8、L2 512 KiB × 8、L3 16 MiB），Windows 端 44 GB RAM。**本章所有時間都是這台機器的實測，而且量測時機器上同時有其他工作在跑，同一個指令重跑的時間差 ±20% 是常態**；請只看數量級與相對比較。

## 為什麼需要這個？

到上一章為止，本課所有演算法都用 `dict[node, set[node]]` 表示圖。這在幾千個節點時完美：好寫、好讀、和數學定義一一對應。但 Ch 39 的 AD 圖在真實企業裡有幾十萬個主體、上千萬條關係；Ch 38 的 CPG 在一個中型 C 專案上就有上千萬個節點；網路流量圖、社群圖更大。

漸近複雜度在這裡不再是故事的全部。BFS 永遠是 `O(V + E)`，但 [Ch 21](./21-dinic-push-relabel.md) 已經提醒過：真正決定能不能跑完的是**常數因子**——每條邊要花幾奈秒。而那個常數，現代 CPU 上幾乎完全由**記憶體存取模式**決定：一次 L1 cache hit 大約幾個 cycle，一次 DRAM 存取上百個 cycle（這是常被引用的數量級，確切數字依微架構而異）。

圖演算法是 cache 的天敵：BFS 的下一步要看哪個節點，取決於資料本身，硬體 prefetcher 猜不到。圖計算社群從 2000 年代後期開始系統性地處理這件事：Graph500 基準（2010 年起）用 BFS 衡量超級電腦、GPU BFS（Merrill 等，PPoPP 2012）、direction-optimizing BFS（Beamer 等，SC 2012）、Ligra（Shun、Blelloch，PPoPP 2013）。這一章把其中三個想法親手做一次：**緊湊的資料表示（CSR）**、**記憶體局部性**、**少做工作（direction-optimizing）**，最後碰一下平行化。

## 先建立直覺

想像一間圖書館，要你把每本書的「參考文獻」全部走一遍。

- **鄰接字典**像每本書的參考文獻各自寫在一張便條紙上，便條紙散落在圖書館各處，每張紙還附了一個信封（Python 物件的標頭）。要讀一本書的參考文獻，你得先走到它的便條紙那裡。
- **CSR（Compressed Sparse Row）** 像把所有參考文獻依書號順序抄在**一本**連續的冊子裡，另外有一張目錄告訴你「第 u 本書的參考文獻從冊子第幾行開始」。讀第 u 本書的參考文獻，就是從某一行連續讀到下一本書開始的那一行。

```text
圖：0—1, 0—2, 1—2, 2—3

offsets: [0, 2, 4, 7, 8]          ← 長度 n+1；u 的鄰居在 adj[offsets[u] : offsets[u+1]]
adj:     [1, 2, 0, 2, 0, 1, 3, 2] ← 長度 m（無向圖每條邊存兩次）
          └0┘  └1┘  └──2──┘  3
```

CSR 讀「一個點的所有鄰居」是一段連續記憶體——cache line 一次帶進 16 個 `int32`。但 BFS 接著要查**每個鄰居的 dist**，那些鄰居的編號可能散落在整個 `dist` 陣列裡，這才是真正的 cache miss 來源。所以節點**怎麼編號**會影響速度，這是本章的第二個實驗。

> 本章反覆用到 BFS 的層次性質（滿足 `dist(w) = dist(u) + 1` 的邊構成層次 DAG），它在 [Ch 39](./39-attack-graphs.md) 的「最短路徑計數」證明裡用過；圖的表示法（鄰接矩陣、鄰接串列）的基本定義見 [Ch 7](./07-graph-definitions.md)。

## CSR：定義與用 numpy 建構

給定 n 個節點、m 條（有向）邊，CSR 是兩個陣列：

- `offsets`：長度 `n + 1`，`offsets[0] = 0`，`offsets[u+1] − offsets[u] = deg(u)`；
- `adj`：長度 m，把所有邊依起點排序後的終點序列。

建構的關鍵觀察是 `offsets` 就是度數的**前綴和**：`offsets[u] = Σ_{v<u} deg(v)`。所以只要把邊依起點排序，用 `bincount` 算度數、`cumsum` 算前綴和，全程向量化：

```python
# gen_graphs.py
"""Ch 41：用 numpy 產生測試圖並存成 CSR 二進位檔。
格式：int64 n, int64 m, int64 offsets[n+1], int32 adj[m]（無向圖，兩個方向都存）。"""
import sys
import time
import numpy as np

SEED = 20241104


def to_csr(n, src, dst):
    """邊列表 → CSR：補上反向邊、去掉自環與重邊、依 (u, v) 排序，offsets 用 bincount + cumsum。"""
    u = np.concatenate([src, dst]).astype(np.int64)
    v = np.concatenate([dst, src]).astype(np.int64)
    keep = u != v
    key = np.unique(u[keep] * n + v[keep])          # 排序 + 去重一次完成
    u, v = key // n, key % n
    offsets = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(np.bincount(u, minlength=n), out=offsets[1:])
    return offsets, v.astype(np.int32)


def rmat(scale, edgefactor, rng, a=0.57, b=0.19, c=0.19):
    """R-MAT（Graph500 的參數）：每條邊逐位元決定落在鄰接矩陣的哪個象限。"""
    n, m = 1 << scale, edgefactor << scale
    src = np.zeros(m, dtype=np.int64)
    dst = np.zeros(m, dtype=np.int64)
    for bit in range(scale):
        r = rng.random(m)
        down = r >= a + b                              # 下半部（c 或 d 象限）
        right = ((r >= a) & (r < a + b)) | (r >= a + b + c)
        src |= down.astype(np.int64) << bit
        dst |= right.astype(np.int64) << bit
    perm = rng.permutation(n)                          # 打亂編號，避免高度數點集中在 0 附近
    return n, perm[src], perm[dst]


def save(path, offsets, adj):
    with open(path, "wb") as f:
        np.array([len(offsets) - 1, len(adj)], dtype=np.int64).tofile(f)
        offsets.tofile(f)
        adj.tofile(f)
    print(f"{path}: n={len(offsets) - 1} m={len(adj)} ({(offsets.nbytes + adj.nbytes) / 2**20:.1f} MiB)")


if __name__ == "__main__":
    rng = np.random.default_rng(SEED)
    t0 = time.perf_counter()
    n, s, d = rmat(20, 8, rng)
    save("rmat20.csr", *to_csr(n, s, d))
    n = 1 << 20
    s, d = rng.integers(0, n, 8 * n), rng.integers(0, n, 8 * n)
    save("unif20.csr", *to_csr(n, s, d))
    # 1024 x 1024 格子：row-major 編號（鄰居在記憶體中很近）vs 隨機重新編號
    k = 1024
    ids = np.arange(k * k).reshape(k, k)
    s = np.concatenate([ids[:, :-1].ravel(), ids[:-1, :].ravel()])
    d = np.concatenate([ids[:, 1:].ravel(), ids[1:, :].ravel()])
    save("grid_ordered.csr", *to_csr(k * k, s, d))
    p = rng.permutation(k * k)
    j = int(np.flatnonzero(p == 0)[0])
    p[j], p[0] = p[0], 0                               # 讓角落的 0 號點保持編號 0，兩張圖從同一個點出發
    save("grid_perm.csr", *to_csr(k * k, p[s], p[d]))
    print(f"產生時間 {time.perf_counter() - t0:.1f}s")
```

```text
rmat20.csr: n=1048576 m=16083726 (69.4 MiB)
unif20.csr: n=1048576 m=16777058 (72.0 MiB)
grid_ordered.csr: n=1048576 m=4190208 (24.0 MiB)
grid_perm.csr: n=1048576 m=4190208 (24.0 MiB)
產生時間 42.0s
```

四張測試圖各自代表一種情況：

| 圖 | 結構 | 直徑 | 用來看什麼 |
| --- | --- | --- | --- |
| `rmat20` | R-MAT，Graph500 參數 `(a, b, c) = (0.57, 0.19, 0.19)`，2²⁰ 點、每點平均 8 條無向邊；度數分布重尾 | 小（BFS 6 層） | 社群圖、AD 圖這類「少數超級節點」的圖 |
| `unif20` | 每條邊兩端均勻隨機 | 小（7 層） | 沒有結構的對照組 |
| `grid_ordered` | 1024 × 1024 格子，row-major 編號 | 大（2046 層） | 局部性最好的情況 |
| `grid_perm` | 同一個格子，節點隨機重新編號 | 同上 | 局部性最差的情況 |

`2^20 = 1048576` 是為了讓 `dist` 陣列（每點 4 bytes，共 4 MiB）比 L2（512 KiB）大很多、又放得進 L3（16 MiB）；`adj` 陣列（61–64 MiB）則遠大於 L3，必然要從 DRAM 讀。R-MAT 的 `m` 是 16083726 而不是 `2 × 8 × 2^20 = 16777216`，因為 R-MAT 會產生大量重邊與自環，`to_csr` 把它們去掉了。

## 範例一：同一張圖，四種 Python BFS

```python
# bfs_py.py
"""Ch 41：同一張圖、四種 Python BFS 實作的時間與記憶體。用法：python bfs_py.py rmat20.csr"""
import sys
import time
import tracemalloc
from collections import deque
import numpy as np
import networkx as nx


def load_csr(path):
    with open(path, "rb") as f:
        n, m = np.fromfile(f, dtype=np.int64, count=2)
        offsets = np.fromfile(f, dtype=np.int64, count=n + 1)
        adj = np.fromfile(f, dtype=np.int32, count=m)
    return offsets, adj


def csr_to_dict(offsets, adj):
    o, a = offsets.tolist(), adj.tolist()
    return {u: set(a[o[u]:o[u + 1]]) for u in range(len(o) - 1)}


def bfs_dict(g, s):                      # 1. 鄰接字典（本課的標準表示）
    dist = {s: 0}
    q = deque([s])
    while q:
        u = q.popleft()
        du = dist[u] + 1
        for w in g[u]:
            if w not in dist:
                dist[w] = du
                q.append(w)
    return dist


def bfs_csr_scalar(offsets, adj, s):     # 2. CSR，但在 Python 迴圈裡逐個索引 numpy 陣列
    dist = np.full(len(offsets) - 1, -1, dtype=np.int32)
    dist[s] = 0
    q = deque([s])
    while q:
        u = q.popleft()
        for i in range(offsets[u], offsets[u + 1]):
            w = adj[i]
            if dist[w] < 0:
                dist[w] = dist[u] + 1
                q.append(w)
    return dist


def bfs_csr_lists(o, a, s):              # 3. CSR 轉成 Python list 再做
    dist = [-1] * (len(o) - 1)
    dist[s] = 0
    q = deque([s])
    while q:
        u = q.popleft()
        du = dist[u] + 1
        for i in range(o[u], o[u + 1]):
            w = a[i]
            if dist[w] < 0:
                dist[w] = du
                q.append(w)
    return dist


def bfs_numpy(offsets, adj, s):          # 4. 層同步 + 向量化：一次處理整個 frontier
    n = len(offsets) - 1
    dist = np.full(n, -1, dtype=np.int32)
    dist[s] = 0
    frontier, level = np.array([s], dtype=np.int64), 0
    deg = np.diff(offsets)
    while frontier.size:
        starts, cnt = offsets[frontier], deg[frontier]
        # 把每個 frontier 點的鄰居區間 [start, start+cnt) 攤平成一個索引陣列
        idx = np.repeat(starts - np.cumsum(cnt) + cnt, cnt) + np.arange(cnt.sum())
        nbr = adj[idx]
        nbr = np.unique(nbr[dist[nbr] < 0])
        level += 1
        dist[nbr] = level
        frontier = nbr.astype(np.int64)
    return dist


def summary(d):
    d = np.asarray(list(d.values()) if isinstance(d, dict) else d)
    d = d[d >= 0]
    return int(d.size), int(d.max()), int(d.sum())


if __name__ == "__main__":
    path = sys.argv[1]
    offsets, adj = load_csr(path)
    n, m = len(offsets) - 1, len(adj)
    s = int(np.argmax(np.diff(offsets) > 0))        # 第一個非孤立點當起點，固定可重現
    print(f"{path}: n={n} m={m} source={s}")
    print(f"CSR 記憶體: offsets {offsets.nbytes / 2**20:.1f} MiB + adj {adj.nbytes / 2**20:.1f} MiB")

    tracemalloc.start()
    g = csr_to_dict(offsets, adj)
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"dict-of-sets 記憶體（tracemalloc）: {cur / 2**20:.0f} MiB")
    del g
    t0 = time.perf_counter(); g = csr_to_dict(offsets, adj); t1 = time.perf_counter()
    print(f"建 dict-of-sets: {t1 - t0:.2f}s")

    o, a = offsets.tolist(), adj.tolist()
    reached = bfs_numpy(offsets, adj, s) >= 0
    mr = int(np.diff(offsets)[reached].sum())     # 實際走過的（有向）邊數，TEPS 的分子
    print(f"可達 {int(reached.sum())} 點、{mr} 條邊")
    res = {}
    for name, f in [("dict-of-sets", lambda: bfs_dict(g, s)),
                    ("CSR numpy 逐個索引", lambda: bfs_csr_scalar(offsets, adj, s)),
                    ("CSR Python list", lambda: bfs_csr_lists(o, a, s)),
                    ("CSR numpy 向量化", lambda: bfs_numpy(offsets, adj, s))]:
        t0 = time.perf_counter(); d = f(); dt = time.perf_counter() - t0
        res[name] = summary(d)
        print(f"  {name:20} {dt:7.2f}s  {mr / dt / 1e6:6.2f} MTEPS  (reached, maxdist, sumdist)={res[name]}")
    print("四種結果一致:", len(set(res.values())) == 1)

    # 小圖上與 NetworkX 對拍
    k = 2000
    sub = {u: {w for w in g[u] if w < k} for u in range(k)}
    G = nx.Graph([(u, w) for u in sub for w in sub[u]])
    G.add_nodes_from(range(k))
    src = next(u for u in range(k) if sub[u])
    print("小圖 (前 2000 點導出子圖) 與 NetworkX 一致:", bfs_dict(sub, src) == nx.single_source_shortest_path_length(G, src))
```

**TEPS**（traversed edges per second）是 Graph500 的指標：BFS 走過的邊數除以時間。分子用「可達部分的邊數」而不是整張圖的 m。R-MAT 有將近一半的點走不到（可達 545915 / 1048576），但走不到的幾乎都是沒有邊的孤立點，所以這張圖上兩種算法只差 0.006%；在有多個大連通分量的圖上就會差很多。

實測（`PYTHONUTF8=1 python bfs_py.py rmat20.csr`，以及 `unif20.csr`）：

```text
rmat20.csr: n=1048576 m=16083726 source=2
CSR 記憶體: offsets 8.0 MiB + adj 61.4 MiB
dict-of-sets 記憶體（tracemalloc）: 1592 MiB
建 dict-of-sets: 5.15s
可達 545915 點、16082744 條邊
  dict-of-sets            5.56s    2.89 MTEPS  (reached, maxdist, sumdist)=(545915, 6, 1716997)
  CSR numpy 逐個索引          4.86s    3.31 MTEPS  (reached, maxdist, sumdist)=(545915, 6, 1716997)
  CSR Python list         2.21s    7.27 MTEPS  (reached, maxdist, sumdist)=(545915, 6, 1716997)
  CSR numpy 向量化           1.07s   15.07 MTEPS  (reached, maxdist, sumdist)=(545915, 6, 1716997)
四種結果一致: True
小圖 (前 2000 點導出子圖) 與 NetworkX 一致: True
unif20.csr: n=1048576 m=16777058 source=0
CSR 記憶體: offsets 8.0 MiB + adj 64.0 MiB
dict-of-sets 記憶體（tracemalloc）: 1707 MiB
建 dict-of-sets: 4.05s
可達 1048576 點、16777058 條邊
  dict-of-sets            9.75s    1.72 MTEPS  (reached, maxdist, sumdist)=(1048576, 7, 5565971)
  CSR numpy 逐個索引          6.36s    2.64 MTEPS  (reached, maxdist, sumdist)=(1048576, 7, 5565971)
  CSR Python list         3.50s    4.80 MTEPS  (reached, maxdist, sumdist)=(1048576, 7, 5565971)
  CSR numpy 向量化           1.03s   16.27 MTEPS  (reached, maxdist, sumdist)=(1048576, 7, 5565971)
四種結果一致: True
小圖 (前 2000 點導出子圖) 與 NetworkX 一致: True
```

四個觀察：

1. **記憶體：1592 MiB vs 69.4 MiB，23 倍**。dict-of-sets 的每個鄰居是一個 Python `int` 物件（CPython 對小整數有快取，但大於 256 的數各自是物件，64 位元上約 28 bytes）加上 set 的雜湊槽；CSR 每個鄰居 4 bytes。對千萬條邊的圖，這是「放得進記憶體」和「放不進」的差別。
2. **「CSR numpy 逐個索引」幾乎沒比字典快**。資料結構換了，但每次 `adj[i]`、`dist[w]` 都要把 C 層的數字包成 Python 物件，直譯器開銷蓋過了記憶體佈局的好處。這是常見的失望時刻：**numpy 只有在整批運算時才快**。
3. **轉成 list 快 2–3 倍**：list 索引比 numpy 純量索引便宜，但仍是直譯器在逐條邊執行。
4. **向量化層同步 BFS 最快**：每一層只呼叫幾個 numpy 函式（`repeat`、`unique`、遮罩索引），迴圈在 C 裡跑。它在低直徑圖上表現好，因為層數少（6、7 層）；在 2046 層的格子圖上優勢會縮小（下面的表）。

`idx = np.repeat(starts - np.cumsum(cnt) + cnt, cnt) + np.arange(cnt.sum())` 這行值得拆開：`np.arange(cnt.sum())` 給每個要讀的鄰居一個全域編號 0, 1, 2, …；對第 k 個 frontier 點，它的鄰居佔用全域編號 `[C_k, C_k + cnt_k)`，其中 `C_k = cumsum(cnt)[k] − cnt[k]`；所以鄰居在 `adj` 裡的位置是 `starts[k] + (全域編號 − C_k)`。`np.repeat` 把每個點的 `starts[k] − C_k` 複製 `cnt_k` 次，再加上全域編號即得。

## 範例二：C 版 CSR BFS 與 direction-optimizing

### top-down 與 bottom-up

傳統 BFS 是 **top-down**：frontier（這一層的點）裡的每個點，把它所有未拜訪的鄰居標成下一層。

Beamer、Asanović、Patterson（SC 2012）的觀察是：在低直徑圖上，BFS 中間有一兩層的 frontier 非常大（R-MAT 的第 3 層一次拜訪了 42 萬個點），這時 top-down 會檢查大量「鄰居早就被拜訪過」的邊。反過來做——**bottom-up**：每個**未拜訪**的點 v 去檢查「我有沒有鄰居在 frontier 裡」，找到一個就停——可以省下大部分的邊檢查。

**命題（bottom-up 的正確性）**。設 `F_d` 是距離恰為 d 的點集合，所有 `dist ≤ d` 的點都已標記。則一個點 v 的距離為 `d + 1` ⟺ v 未被標記且存在鄰居 `u ∈ F_d`。

**證明**。(⇒) `dist(v) = d + 1`，則 v 未被標記（只有 `dist ≤ d` 的被標了）；取一條 s 到 v 的最短路徑，倒數第二個點 u 滿足 `dist(u) = d`，即 `u ∈ F_d`，且 u 是 v 的鄰居。(⇐) v 未被標記，所以 `dist(v) ≥ d + 1`；v 有鄰居 `u ∈ F_d`，所以 `dist(v) ≤ dist(u) + 1 = d + 1`。∎

所以 bottom-up 每一層計算的結果與 top-down 完全相同，而且**每個 v 只要找到一個 frontier 鄰居就可以停**——這是省工的來源。兩個方向各有適用時機：frontier 小時 top-down 便宜（只看 frontier 的邊），frontier 大時 bottom-up 便宜（未拜訪的點少，而且大多很快找到父節點）。

切換規則用兩個量：frontier 的出邊數 `m_f`、尚未探索的邊數 `m_u`。本章的參數取自 GAP Benchmark Suite（Beamer 維護的參考實作）原始碼的預設值：

```text
top-down → bottom-up：m_f > m_u / α        （α = 15）
bottom-up → top-down：frontier 開始縮小且 n_f ≤ n / β   （β = 18）
```

SC 2012 原論文用的是自己調校過的 α、β；這兩個數字是啟發式參數，不是推導出來的常數，不同圖上的最佳值不同。

```c
/* bfs.c —— Ch 41：CSR 上的 BFS —— top-down、direction-optimizing（序列與 OpenMP 平行）。
 * 編譯：gcc -O3 -march=native -fopenmp -o bfs bfs.c
 * 用法：./bfs graph.csr [來源數]
 * CSR 檔格式：int64 n, int64 m, int64 offsets[n+1], int32 adj[m]（無向圖兩個方向都存）。 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <omp.h>

typedef struct { int64_t n, m; int64_t *off; int32_t *adj; } CSR;

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static CSR load(const char *path) {
    CSR g;
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(1); }
    if (fread(&g.n, 8, 1, f) != 1 || fread(&g.m, 8, 1, f) != 1) exit(1);
    g.off = malloc((g.n + 1) * sizeof(int64_t));
    g.adj = malloc(g.m * sizeof(int32_t));
    if (fread(g.off, 8, g.n + 1, f) != (size_t)(g.n + 1) || fread(g.adj, 4, g.m, f) != (size_t)g.m) exit(1);
    fclose(f);
    return g;
}

static int64_t examined;   /* 檢查過的邊數（只在序列版統計） */
static int trace;         /* 環境變數 TRACE=1：印出序列版每一層的方向與大小 */

/* 1. 經典 top-down：佇列 + dist 陣列。 */
static void bfs_topdown(const CSR *g, int32_t s, int32_t *dist) {
    int32_t *q = malloc(g->n * sizeof(int32_t));
    int64_t head = 0, tail = 0;
    for (int64_t i = 0; i < g->n; i++) dist[i] = -1;
    dist[s] = 0; q[tail++] = s;
    while (head < tail) {
        int32_t u = q[head++];
        for (int64_t i = g->off[u]; i < g->off[u + 1]; i++) {
            int32_t w = g->adj[i];
            examined++;
            if (dist[w] < 0) { dist[w] = dist[u] + 1; q[tail++] = w; }
        }
    }
    free(q);
}

/* 2. direction-optimizing（Beamer 等 SC 2012）。切換規則與參數取自 GAP Benchmark Suite 的 bfs.cc：
 *    top-down → bottom-up：frontier 的出邊數 m_f > 尚未探索的邊數 m_u / ALPHA
 *    bottom-up → top-down：frontier 開始縮小，且 n_f <= n / BETA                */
#define ALPHA 15
#define BETA 18

static void bfs_do(const CSR *g, int32_t s, int32_t *dist, int par) {
    int64_t n = g->n;
    int32_t *front = malloc(n * sizeof(int32_t)), *next = malloc(n * sizeof(int32_t));
    uint8_t *inF = calloc(n, 1), *inN = calloc(n, 1);
    int64_t nf = 1, mu = g->m, level = 0, ex = 0;
    #pragma omp parallel for if (par)
    for (int64_t i = 0; i < n; i++) dist[i] = -1;
    dist[s] = 0; front[0] = s;
    int64_t mf = g->off[s + 1] - g->off[s];
    while (nf > 0) {
        if (mf > mu / ALPHA) {
            /* ---- bottom-up：每個未拜訪的點問「我有沒有鄰居在 frontier 裡？」 ---- */
            memset(inF, 0, n);
            for (int64_t i = 0; i < nf; i++) inF[front[i]] = 1;
            int64_t prev = nf;
            do {
                int64_t cnt = 0;
                level++;
                #pragma omp parallel for if (par) reduction(+ : cnt, ex) schedule(dynamic, 1024)
                for (int64_t v = 0; v < n; v++) {
                    inN[v] = 0;
                    if (dist[v] >= 0) continue;
                    for (int64_t i = g->off[v]; i < g->off[v + 1]; i++) {
                        ex++;
                        if (inF[g->adj[i]]) { dist[v] = level; inN[v] = 1; cnt++; break; }  /* 找到一個就停 */
                    }
                }
                uint8_t *t = inF; inF = inN; inN = t;
                if (trace && !par) printf("    level %2ld  bottom-up  新拜訪 %ld 點\n", (long)level, (long)cnt);
                prev = nf; nf = cnt;
            } while (nf >= prev || nf > n / BETA);
            /* 把 bitmap 轉回 frontier 陣列，回到 top-down */
            int64_t k = 0;
            for (int64_t v = 0; v < n; v++) if (inF[v]) front[k++] = (int32_t)v;
            nf = k;
            mf = 0;
            for (int64_t i = 0; i < nf; i++) mf += g->off[front[i] + 1] - g->off[front[i]];
            continue;
        }
        /* ---- top-down：frontier 裡的點往外推 ---- */
        int64_t nn = 0, newmf = 0;
        level++;
        #pragma omp parallel if (par) reduction(+ : newmf, ex)
        {
            /* 每個執行緒先寫進自己的緩衝區，最後一次性搬到 next：避免每發現一個點就搶同一個計數器 */
            int64_t cap = 1024, cnt = 0;
            int32_t *buf = malloc(cap * sizeof(int32_t));
            #pragma omp for schedule(dynamic, 64) nowait
            for (int64_t i = 0; i < nf; i++) {
                int32_t u = front[i];
                for (int64_t j = g->off[u]; j < g->off[u + 1]; j++) {
                    int32_t w = g->adj[j];
                    ex++;
                    /* 平行時用 compare-and-swap 確保每個點只被一個執行緒認領 */
                    if (dist[w] < 0 && __sync_bool_compare_and_swap(&dist[w], -1, (int32_t)level)) {
                        if (cnt == cap) buf = realloc(buf, (cap *= 2) * sizeof(int32_t));
                        buf[cnt++] = w;
                        newmf += g->off[w + 1] - g->off[w];
                    }
                }
            }
            int64_t pos = __atomic_fetch_add(&nn, cnt, __ATOMIC_RELAXED);
            memcpy(next + pos, buf, cnt * sizeof(int32_t));
            free(buf);
        }
        if (trace && !par)
            printf("    level %2ld  top-down   n_f=%ld m_f=%ld m_u/ALPHA=%ld → 新拜訪 %ld 點\n",
                   (long)level, (long)nf, (long)mf, (long)(mu / ALPHA), (long)nn);
        mu -= mf;
        int32_t *t = front; front = next; next = t;
        nf = nn; mf = newmf;
    }
    if (!par) examined += ex;
    free(front); free(next); free(inF); free(inN);
}

static void summary(const CSR *g, const int32_t *d, int64_t *reached, int64_t *maxd, int64_t *sum, int64_t *edges) {
    *reached = *maxd = *sum = *edges = 0;
    for (int64_t v = 0; v < g->n; v++)
        if (d[v] >= 0) {
            (*reached)++; *sum += d[v]; if (d[v] > *maxd) *maxd = d[v];
            *edges += g->off[v + 1] - g->off[v];
        }
}

int main(int argc, char **argv) {
    CSR g = load(argv[1]);
    int nsrc = argc > 2 ? atoi(argv[2]) : 8;
    trace = getenv("TRACE") != NULL;
    int32_t *d1 = malloc(g.n * 4), *d2 = malloc(g.n * 4), *d3 = malloc(g.n * 4);
    printf("%s: n=%ld m=%ld threads=%d\n", argv[1], (long)g.n, (long)g.m, omp_get_max_threads());
    /* 來源：第一個非孤立點，之後用固定 seed 的 LCG 挑非孤立點 */
    uint64_t seed = 20241104;
    int32_t s = 0;
    while (g.off[s + 1] == g.off[s]) s++;
    double t_td = 0, t_do = 0, t_par = 0;
    int64_t ex_td = 0, ex_do = 0, teps_edges = 0;
    for (int k = 0; k < nsrc; k++) {
        if (k > 0) do { seed = seed * 6364136223846793005ULL + 1442695040888963407ULL; s = (int32_t)((seed >> 33) % g.n); }
                   while (g.off[s + 1] == g.off[s]);
        double t0 = now(); examined = 0; bfs_topdown(&g, s, d1); double t1 = now(); ex_td += examined;
        examined = 0; bfs_do(&g, s, d2, 0); double t2 = now(); ex_do += examined;
        bfs_do(&g, s, d3, 1); double t3 = now();
        if (memcmp(d1, d2, g.n * 4) || memcmp(d1, d3, g.n * 4)) { printf("MISMATCH at source %d\n", s); return 1; }
        int64_t r, mx, sm, e;
        summary(&g, d1, &r, &mx, &sm, &e);
        if (k == 0) printf("source=%d (reached, maxdist, sumdist)=(%ld, %ld, %ld)\n", s, (long)r, (long)mx, (long)sm);
        t_td += t1 - t0; t_do += t2 - t1; t_par += t3 - t2; teps_edges += e;
    }
    printf("%d sources, 三種結果逐點相同\n", nsrc);
    printf("  top-down 序列        %8.1f ms/BFS  %7.1f MTEPS  檢查邊數/可達邊數 = %.3f\n",
           1e3 * t_td / nsrc, teps_edges / t_td / 1e6, (double)ex_td / teps_edges);
    printf("  direction-opt 序列   %8.1f ms/BFS  %7.1f MTEPS  檢查邊數/可達邊數 = %.3f\n",
           1e3 * t_do / nsrc, teps_edges / t_do / 1e6, (double)ex_do / teps_edges);
    printf("  direction-opt 平行   %8.1f ms/BFS  %7.1f MTEPS\n", 1e3 * t_par / nsrc, teps_edges / t_par / 1e6);
    return 0;
}
```

這個程式的 LCG 常數 `6364136223846793005` 與 `1442695040888963407` 是 Knuth 在 MMIX 用的 64 位元線性同餘產生器參數，只用來可重現地挑 BFS 起點，不影響結果的正確性。每個起點三種 BFS 都跑，`memcmp` 逐點比對 dist 陣列；第一個起點與 Python 版相同，`(reached, maxdist, sumdist)` 兩邊一致，等於拿 C 版和 NetworkX 間接對拍過。

先看 direction-optimizing 每一層在做什麼（`TRACE=1`，單一起點）：

```text
$ TRACE=1 OMP_NUM_THREADS=8 ./bfs rmat20.csr 1
rmat20.csr: n=1048576 m=16083726 threads=8
    level  1  top-down   n_f=1 m_f=8 m_u/ALPHA=1072248 → 新拜訪 8 點
    level  2  top-down   n_f=8 m_f=21505 m_u/ALPHA=1072247 → 新拜訪 19209 點
    level  3  bottom-up  新拜訪 429158 點
    level  4  bottom-up  新拜訪 96603 點
    level  5  bottom-up  新拜訪 931 點
    level  6  top-down   n_f=931 m_f=944 m_u/ALPHA=1070814 → 新拜訪 5 點
    level  7  top-down   n_f=5 m_f=5 m_u/ALPHA=1070751 → 新拜訪 0 點
```

第 2 層的 frontier 只有 8 個點，卻帶出了 19209 個新點——這 8 個點裡有 R-MAT 的超級節點。新 frontier 的出邊數遠超過 `m_u / 15`，第 3 層切到 bottom-up，一層拜訪了 42 萬個點（可達部分的 79%）。第 5 層 frontier 縮到 931 個點，切回 top-down 收尾。

在 WSL 裡跑完整的 8 個起點（`OMP_NUM_THREADS=8`）：

```text
rmat20.csr: n=1048576 m=16083726 threads=8
source=2 (reached, maxdist, sumdist)=(545915, 6, 1716997)
8 sources, 三種結果逐點相同
  top-down 序列           107.0 ms/BFS    150.3 MTEPS  檢查邊數/可達邊數 = 1.000
  direction-opt 序列       24.1 ms/BFS    666.1 MTEPS  檢查邊數/可達邊數 = 0.057
  direction-opt 平行        7.5 ms/BFS   2150.5 MTEPS
unif20.csr: n=1048576 m=16777058 threads=8
source=0 (reached, maxdist, sumdist)=(1048576, 7, 5565971)
8 sources, 三種結果逐點相同
  top-down 序列           239.0 ms/BFS     70.2 MTEPS  檢查邊數/可達邊數 = 1.000
  direction-opt 序列       38.5 ms/BFS    435.4 MTEPS  檢查邊數/可達邊數 = 0.282
  direction-opt 平行       10.0 ms/BFS   1670.2 MTEPS
grid_ordered.csr: n=1048576 m=4190208 threads=8
source=0 (reached, maxdist, sumdist)=(1048576, 2046, 1072693248)
8 sources, 三種結果逐點相同
  top-down 序列            21.7 ms/BFS    192.7 MTEPS  檢查邊數/可達邊數 = 1.000
  direction-opt 序列       32.5 ms/BFS    128.9 MTEPS  檢查邊數/可達邊數 = 1.001
  direction-opt 平行       17.7 ms/BFS    237.2 MTEPS
grid_perm.csr: n=1048576 m=4190208 threads=8
source=0 (reached, maxdist, sumdist)=(1048576, 2046, 1072693248)
8 sources, 三種結果逐點相同
  top-down 序列            78.9 ms/BFS     53.1 MTEPS  檢查邊數/可達邊數 = 1.000
  direction-opt 序列      108.7 ms/BFS     38.5 MTEPS  檢查邊數/可達邊數 = 1.001
  direction-opt 平行       29.1 ms/BFS    143.8 MTEPS
```

### 解讀一：少做工作

R-MAT 上 direction-optimizing 只檢查了 top-down 5.7% 的邊，序列時間從 107 ms 降到 24 ms（約 4.4 倍）。時間沒有降到 5.7%，因為 bottom-up 每層要掃過所有 n 個點（即使大多數已拜訪，`dist[v] >= 0` 就跳過），這是 `O(n)` 的固定成本。均勻隨機圖省得少一些（28.2%），因為沒有超級節點，frontier 爆發得沒那麼集中。

格子圖上 direction-optimizing **反而變慢**（21.7 → 32.5 ms）：格子的每一層 frontier 都很小（最多約 1000 個點），切換條件幾乎不會觸發，但每層多了維護 `m_f` 的成本。**最佳化是有前提的**：它針對的是低直徑、度數重尾的圖。

### 解讀二：局部性

`grid_ordered` 和 `grid_perm` 是**同一張圖**，只差節點編號；同一個起點、同樣 2046 層、同樣檢查 4190208 條邊。top-down 序列時間是 21.7 ms vs 78.9 ms，**3.6 倍**，全部來自記憶體存取模式：

- row-major 編號下，點 u 的鄰居是 `u ± 1` 與 `u ± 1024`，`dist[w]` 的存取落在 u 附近幾 KB 內，幾乎都是 cache hit；BFS 佇列裡的點也大致依編號順序出現，`offsets[u]` 和 `adj[...]` 的讀取接近循序。
- 隨機編號下，每個 `dist[w]` 是 4 MiB 陣列中的隨機位置，超出 L2（512 KiB），每次都可能 miss 到 L3；`offsets[u]`（8 MiB）與 `adj` 同理。

Python 版也看得到同樣的效應（同機器、Windows 端，單次執行）：

```text
grid_ordered.csr  dict-of-sets  0.45s    CSR numpy 向量化  0.28s
grid_perm.csr     dict-of-sets  1.76s    CSR numpy 向量化  0.42s
```

這就是為什麼大型圖系統會**重新編號**節點（例如依 BFS 順序、依度數、或用 Reverse Cuthill–McKee 這類頻寬縮減排序）：演算法完全不變，只是讓「圖上相鄰」盡量變成「記憶體上相鄰」。

### 解讀三：Python vs C

同一張 R-MAT、同一個起點（source = 2），單一起點的 C 執行（`./bfs rmat20.csr 1`）是 top-down 94.5 ms、direction-optimizing 序列 30.0 ms、平行 6.8 ms。和 Python 對照：

| 實作 | 時間 | 相對 dict-of-sets |
| --- | --- | --- |
| Python dict-of-sets | 5.56 s | 1× |
| Python CSR 向量化（numpy） | 1.07 s | 約 5× |
| C top-down（CSR） | 94.5 ms | 約 59× |
| C direction-optimizing 序列 | 30.0 ms | 約 185× |
| C direction-optimizing 8 執行緒 | 6.8 ms | 約 820× |

注意這個對照的條件：Python 跑在 Windows 原生、C 跑在 WSL2，同一顆 CPU；Python 的數字是單次執行，C 的是單一起點單次執行；機器同時有其他負載。倍數的數量級是可靠的，小數點不是。

## 平行 BFS：概念與實測

平行 BFS 最常見的形式是 **level-synchronous**：一層一層做，每一層內部平行，層與層之間設 barrier。兩個方向的平行難度不同：

- **top-down**：多個執行緒可能同時發現同一個 w。用 compare-and-swap（`__sync_bool_compare_and_swap(&dist[w], -1, level)`）讓只有一個執行緒「認領」w；新 frontier 的寫入用每執行緒的本地緩衝區，最後一次 `fetch_add` 預留位置再 `memcpy`。第一版程式每發現一個點就對共用計數器做一次 `fetch_add`，在 16 執行緒下 R-MAT 的平行版反而比序列慢（實測 35.9 ms vs 序列 25.6 ms）——所有核心在搶同一條 cache line。
- **bottom-up**：每個 v 只寫自己的 `dist[v]` 和 `inN[v]`，**完全不需要原子操作**。這是 bottom-up 除了省工以外的第二個優點。

執行緒數的擴展（`unif20`，8 個起點平均，direction-optimizing 平行版）：

```text
T=1   direction-opt 平行       38.8 ms/BFS    432.0 MTEPS
T=2   direction-opt 平行       19.4 ms/BFS    863.3 MTEPS
T=4   direction-opt 平行       12.1 ms/BFS   1381.0 MTEPS
T=8   direction-opt 平行        9.0 ms/BFS   1857.0 MTEPS
T=16  direction-opt 平行       10.9 ms/BFS   1544.0 MTEPS
```

1 → 2 執行緒幾乎線性；到 8 執行緒（8 個實體核心）是 4.3 倍；16 執行緒（用上 SMT）反而退步。另一次 R-MAT 的量測裡，16 執行緒三次重跑分別是 15.2、15.0、7.8 ms——在 WSL2 上、機器有其他負載時，SMT 執行緒的表現很不穩定。BFS 是記憶體頻寬與延遲受限的工作，SMT 能藏一些延遲，但兩個邏輯核心共用 L1/L2，在這個工作上沒有幫助。

格子圖的平行加速很小（21.7 → 17.7 ms），原因是 2046 層每一層都要 fork/join 一次，而每層只有約 1000 個點的工作量，同步成本蓋過了平行的好處。**高直徑圖是 level-synchronous 平行 BFS 的天敵**；這類圖需要減少同步次數的策略（例如放寬逐層 barrier 的非同步 BFS），或乾脆不平行。

GPU 上的 BFS 走得更遠：Merrill、Garland、Grimshaw（PPoPP 2012）用 prefix sum 分配每條邊的工作，讓上千個執行緒均勻分攤高度數節點的鄰居列表。概念和本章的 `np.repeat` 那行相同——把「每個點的鄰居區間」攤平成「每條邊一個工作項」。

## 對比與取捨

| 表示法 | 記憶體（本章 R-MAT） | 加邊/刪邊 | 讀一個點的鄰居 | 適合 |
| --- | --- | --- | --- | --- |
| `dict[node, set]` | 1592 MiB | `O(1)` | 雜湊表走訪，散落各處 | 原型、動態圖、小圖、證明對拍 |
| 鄰接矩陣 | `n² / 8` bytes（位元版）= 128 GiB | `O(1)` | `O(n)` | 稠密小圖 |
| CSR | 69.4 MiB | 要重建，`O(m)` | 連續一段 | 靜態大圖、BFS/PageRank/SpMV |
| 邊列表（COO） | 約 123 MiB（兩個 int32 陣列） | 附加 `O(1)` | 要先排序 | 串流輸入、建 CSR 的中間格式 |

| BFS 變體 | 最適合 | 不適合 |
| --- | --- | --- |
| top-down | 高直徑、frontier 一直很小的圖 | 低直徑重尾圖的中間層 |
| bottom-up | frontier 佔了很大比例時 | frontier 小時（每層 `O(n)` 固定成本） |
| direction-optimizing | 社群圖、R-MAT、AD 圖這類低直徑圖 | 格子、道路網路 |
| level-synchronous 平行 | 層數少、每層工作量大 | 層數上千的圖 |

## 踩雷集錦

1. **以為換成 numpy 就會變快**：`bfs_csr_scalar` 用了 CSR 和 numpy 陣列，卻和字典版差不多慢。numpy 的速度來自**整批**運算；在 Python 迴圈裡逐個 `adj[i]` 索引，每次都付一次物件轉換成本。要嘛整層向量化，要嘛換到 C。
2. **TEPS 的分子算錯**：用整張圖的 m 除以時間，等於把沒走到的連通分量也算成「走過」。本章的 R-MAT 剛好不受影響（走不到的都是孤立點），但只要圖有好幾個大分量，TEPS 就會被灌水；正確做法是只數可達部分的邊。比較不同論文的 TEPS 前，先確認它們的定義（Graph500 的規則對此有明確規定）。
3. **把 direction-optimizing 當成無條件的加速**：格子圖上它比 top-down 慢 50%。任何最佳化都要在**你的**圖上量過。
4. **平行版搶同一個計數器**：每發現一個點就 `fetch_add` 共用計數器，16 執行緒下比序列還慢。原子操作本身不貴，貴的是 cache line 在核心之間來回搬（contention）；用本地緩衝區批次提交。
5. **忘記編號會影響速度**：同一張格子圖，隨機編號比 row-major 慢 3.6 倍。從資料庫或雜湊表撈出來的節點 ID 通常是隨機的；建 CSR 前重新編號是便宜又有效的最佳化。
6. **在 WSL2 或有負載的機器上只跑一次就下結論**：本章同一個指令重跑差 ±20%、SMT 執行緒甚至差兩倍。至少跑多個起點、多次，報告時寫清楚機器與條件。

## 進階：再往深一層

- **量測方法**：本章只用了 wall-clock 時間。要確認「慢是因為 cache miss」，用 `perf stat -e cache-misses,LLC-load-misses` 這類硬體計數器（WSL2 上不一定可用）。[systems/perf_bench](../../systems/perf_bench/README.md) 的 [performance events](../../systems/perf_bench/06-performance-events.md) 與 [perf 工具](../../systems/perf_bench/07-perf-tool.md) 兩章教你怎麼做，[統計基礎](../../systems/perf_bench/04-statistical-basics.md) 教你怎麼處理 ±20% 的雜訊。
- **壓縮**：`adj` 依起點排序後，同一個點的鄰居編號也可以排序，存差值再用變長編碼（Ligra+ 的做法），記憶體再降一半以上，代價是解碼成本。
- **GPU**：[parallel/gpu_cuda](../../parallel/gpu_cuda/README.md) 的 [memory coalescing](../../parallel/gpu_cuda/18-memory-coalescing.md) 與 [warp divergence](../../parallel/gpu_cuda/21-warp-divergence.md) 正好是 GPU BFS 的兩大難題：鄰居列表長短不一造成 divergence，`dist[w]` 的隨機存取無法 coalesce。
- **線性代數觀點**：一層 top-down BFS 等於稀疏矩陣乘向量（`A^T x`，在布林半環上），CSR 就是稀疏矩陣的標準格式。GraphBLAS 把這個觀點標準化，PageRank（[Ch 29](./29-random-walks-pagerank.md)）在同一套格式上就是反覆的 SpMV。
- **分散式**：圖大到單機放不下時要切分；Ch 39、Ch 20 的割在這裡變成「切分時讓跨機器的邊最少」，那是圖切分（graph partitioning）問題，本身是 NP-hard，實務用多層次啟發式（METIS 一類）。

## 動手練習

1. 在 `gen_graphs.py` 加一個「依 BFS 順序重新編號」的函式：從某個點做 BFS，依拜訪順序給新編號。把 `grid_perm` 重新編號後跑 `./bfs`，時間能回到接近 `grid_ordered` 嗎？
2. 把 `bfs.c` 的 `ALPHA` 改成 2、5、50、500，在 `rmat20` 上畫出時間對 α 的曲線（用 matplotlib，x 軸取 log）。你會看到一個 U 形：太小太早切換、太大不切換。
3. 用 `bfs_numpy` 的寫法實作一個「向量化 bottom-up」步驟：對所有未拜訪點，判斷是否有鄰居在 frontier 裡（提示：`np.add.reduceat` 或 `np.maximum.reduceat` 搭配 `offsets`）。Python 版的 direction-optimizing 能比 `bfs_numpy` 快嗎？
4. 估算：一台 64 GB 記憶體的機器，用 CSR（`int64` offsets + `int32` adj）最多能放多大的圖？如果用 dict-of-sets 呢？用本章實測的「每條邊 bytes 數」推算。

## 本章重點整理

- CSR 用 `offsets`（度數前綴和）加 `adj` 兩個陣列表示靜態圖，記憶體是本章 dict-of-sets 的 1/23；用 numpy 的 `unique`、`bincount`、`cumsum` 向量化建構。
- 同樣 `O(V + E)` 的 BFS，常數因子由直譯器開銷與記憶體存取模式決定：本章實測 Python 字典到 C 平行版差約 800 倍，同一張格子圖只換編號就差 3.6 倍。
- direction-optimizing BFS 在低直徑重尾圖上省下大部分邊檢查（R-MAT 只檢查 5.7%），正確性來自「距離 d+1 ⟺ 未拜訪且有鄰居在第 d 層」；在高直徑圖上反而變慢。
- 平行 BFS：top-down 需要 CAS 與批次提交，bottom-up 天生無競爭；高直徑圖的每層同步成本會吃掉平行的好處。

## 自我檢核

- [ ] 不看筆記，從一個邊列表手算出 CSR 的 `offsets` 和 `adj`，並說明 `offsets` 為什麼是度數的前綴和。
- [ ] 用自己的話證明 bottom-up 步驟算出的距離和 top-down 一樣，並指出「找到一個鄰居就停」為什麼不影響正確性。
- [ ] 如果同事說「我把 dict 換成 numpy 陣列了，但沒變快」，你會怎麼解釋？
- [ ] 解釋 `grid_ordered` 和 `grid_perm` 為什麼差 3.6 倍，並說出兩種讓隨機編號的圖變快的方法。
- [ ] 想一個你工作上的圖（AD、CPG、網路拓樸）：它是低直徑還是高直徑？direction-optimizing 值得用嗎？

## 延伸閱讀

### 論文

- **[Direction-Optimizing Breadth-First Search](https://sc12.supercomputing.org/schedule/event_detail.php-evid=pap525.html)** — Scott Beamer、Krste Asanović、David Patterson，SC（2012）
  - **核心貢獻**：提出 bottom-up 步驟與 top-down/bottom-up 的切換啟發式，在低直徑圖上大幅減少檢查的邊數。期刊延伸版發表於 Scientific Programming 21（2013），pp. 137–148。
  - **讀哪裡**：bottom-up 的描述與「為什麼 top-down 在中間層浪費」的分析、切換啟發式那節；硬體平台的實驗可略讀。
  - **和本章的關聯**：`bfs_do` 就是這篇演算法的實作。
- **[The GAP Benchmark Suite](https://arxiv.org/abs/1508.03619)** — Scott Beamer、Krste Asanović、David Patterson，arXiv 1508.03619（2015，2017 修訂）
  - **核心貢獻**：定義 BFS、SSSP、PageRank 等六個 kernel、五張輸入圖與量測規則，附參考實作。
  - **讀哪裡**：量測方法那節（為什麼要固定起點數、如何計時），以及參考實作的原始碼 `src/bfs.cc`——本章的 α = 15、β = 18 就取自那裡。
- **[Scalable GPU Graph Traversal](https://research.nvidia.com/publication/2012-02_scalable-gpu-graph-traversal)** — Duane Merrill、Michael Garland、Andrew Grimshaw，PPoPP（2012）
  - **核心貢獻**：用 prefix sum 做細粒度工作分配，讓 GPU BFS 達到 `O(V + E)` 的工作量與很高的 TEPS。
  - **讀哪裡**：鄰居收集（neighbor gathering）策略的討論；前提是懂 GPU 的 warp 與 shared memory。
- **[Ligra: A Lightweight Graph Processing Framework for Shared Memory](https://jshun.csail.mit.edu/ligra.pdf)** — Julian Shun、Guy E. Blelloch，PPoPP（2013），pp. 135–146
  - **核心貢獻**：把 direction-optimizing 抽象成 `edgeMap` 的自動稀疏/稠密切換，讓 BFS、PageRank、連通分量等演算法共用同一套框架。
  - **讀哪裡**：`edgeMap` 的定義與 sparse/dense 切換那節，看它怎麼把本章的 α 規則一般化。

### repo 內延伸

- **[systems/perf_bench](../../systems/perf_bench/README.md)**：本章的數字怎麼量才可信；特別是 [微基準 vs 巨觀基準](../../systems/perf_bench/01-micro-vs-macro.md) 與 [microarchitecture 入門](../../systems/perf_bench/05-microarch-primer.md)。
- **[parallel/gpu_cuda](../../parallel/gpu_cuda/README.md)**：[記憶體階層](../../parallel/gpu_cuda/09-memory-hierarchy.md) 與 [atomics 與 reduction](../../parallel/gpu_cuda/22-atomics-reduction.md) 是把本章平行 BFS 搬到 GPU 前必讀的兩章。

### 書籍

- **《Computer Systems: A Programmer's Perspective》** — Randal E. Bryant、David R. O'Hallaron（Pearson，第 3 版，2015）
  - **讀哪幾章**：第 6 章（記憶體階層、locality、cache 對程式效能的影響）。本章「局部性」那節的硬體背景都在這裡。

Part 7 的四章把圖論接回了程式分析、攻擊路徑、binary 比對與工程實作。接下來的練習把 Ch 39 的割分析做完整：在預算限制下，切哪幾條邊最划算。

→ [練習 F：攻擊圖的 chokepoint 分析](./practice-f-attack-graph.md)
