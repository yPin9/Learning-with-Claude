# Ch 19 — 五色定理與四色定理

> **目標**：完整證明六色與五色定理（Kempe chain 論證），實作一個真正會做 Kempe 交換的平面圖 5-著色並對拍；親手重現 Kempe 1879 年「四色證明」的漏洞（找到一張 9 點圖讓他的雙重交換失敗）；理解 Appel–Haken、Robertson–Sanders–Seymour–Thomas 的證明架構與 Gonthier 的 Coq 形式化在驗證什麼。四色定理本身**只講證明架構，不證**。
>
> **環境**：Python 3.13.12、NetworkX 3.7、NumPy 2.5.4、SciPy 1.18.1，Windows 11。安裝：`pip install networkx==3.7 numpy==2.5.4 scipy==1.18.1 matplotlib==3.11.2`。

## 為什麼需要這個？

1852 年，Francis Guthrie 在幫英格蘭各郡地圖著色時發現四種顏色似乎永遠夠用，問題經由他的弟弟傳到 De Morgan 手上。這個問題看起來像益智遊戲，卻花了 124 年才解決，而且解法本身改變了數學界對「證明」的看法：

- 1879 年 Kempe 發表了一個證明，被接受了 11 年，直到 1890 年 Heawood 指出漏洞。Kempe 的方法（Kempe chain）卻活了下來，成為五色定理與後來所有證明的核心工具。
- 1976 年 Appel 與 Haken 用電腦檢查了上千個構形完成證明——第一個重大定理的證明**無法由人逐行檢查**，引發了「電腦證明算不算證明」的長期爭論。
- 2005 年 Gonthier 在 Coq 證明輔助器裡把整個證明（包括電腦計算部分）形式化，爭論在技術上畫下句點。

對工程師來說，這一章有三個實用收穫：(1) Kempe chain 是一個很通用的**局部修復**技巧，Ch 15 的 Misra–Gries 邊著色、排程衝突修復都在用；(2) 「一個看起來對的證明被接受了 11 年」是最好的「為什麼要對拍、要形式化驗證」的教材；(3) 「可約構形 + discharging」的證明架構，就是「把無限多個情況化約成有限個、再交給電腦窮舉」的典型，和 SAT/SMT 驗證（programming/sat_smt）的精神相同。

## 先建立直覺

**地圖著色 = 平面圖點著色**：每個國家放一個點（首都），相鄰（共用一段邊界，不只一個點）的兩國之間畫一條穿過邊界的路，得到的是平面圖（Ch 18 的對偶圖）。所以「四色夠不夠」等於「每張平面圖的 `χ ≤ 4` 嗎」。

上一章給了兩個工具：平面圖一定有度數 `≤ 5` 的點；而且拿掉一個點還是平面圖。這立刻給出一個歸納框架：

```text
著色 G：
  拔掉一個度數 ≤ 5 的點 v
  遞迴著色 G − v
  把 v 放回去：v 有 ≤ 5 個鄰居
      ├─ 鄰居用的顏色 < 可用顏色數 → 挑剩下的
      └─ 鄰居把顏色用光了 → 需要「騰出」一種顏色 ← 整章的戲都在這裡
```

六色時第二種情況不會發生（5 個鄰居擋不住 6 色）。五色時會發生（5 個鄰居恰好 5 色），要靠 **Kempe chain**：

```text
Kempe chain：只看顏色 1 和 3 的點所構成的子圖，取它的一個連通分量。
把這個分量裡的 1、3 對調，著色仍然合法。

   1 ─ 3 ─ 1 ─ 3        對調後      3 ─ 1 ─ 3 ─ 1
       │                               │
       2  ← 不在分量裡，                2   和分量裡的點本來就不同色，
            顏色不是 1 也不是 3              對調後還是不同
```

> 如果你對 Euler 公式與「一定有度數 ≤ 5 的點」的推論還不熟，先回看 [Ch 18 — 平面圖：Euler 公式與 Kuratowski](./18-planar-graphs.md)；貪婪著色與 smallest-last 順序在 [Ch 15](./15-graph-coloring.md)。

## 六色定理與 Kempe 交換引理

**定理（六色）**：平面圖 `χ ≤ 6`。

**證明**：對點數歸納。拿掉度數 `≤ 5` 的點 v（Ch 18 推論 3），`G − v` 是平面圖，由歸納假設 6 可著色；v 的 `≤ 5` 個鄰居最多佔 5 色，第 6 色給 v。∎

**引理（Kempe 交換）**：G 有合法著色，a、b 是兩種顏色，K 是「顏色為 a 或 b 的點」導出的子圖的一個連通分量。把 K 裡的 a、b 對調，著色仍然合法。

**證明**：檢查每條邊 `xy`。兩端都在 K 裡：原本一個 a 一個 b（K 裡相鄰兩點不同色且都屬於 `{a, b}`），對調後還是一個 a 一個 b。兩端都不在 K：沒變。一端 x 在 K、另一端 y 不在：若 y 的顏色是 a 或 b，y 和 x 相鄰，就該屬於同一個分量 K，矛盾；所以 y 的顏色不是 a 也不是 b，x 對調後的顏色仍在 `{a, b}` 裡，和 y 不同。∎

## 五色定理：完整證明

**定理（Heawood 1890）**：平面圖 `χ ≤ 5`。

**證明**：對點數歸納。點數 `≤ 5` 時每點給不同顏色即可。設 G 是平面圖，固定一個平面嵌入。取度數 `≤ 5` 的點 v，由歸納假設 `G − v` 有 5 著色（嵌入取原嵌入拿掉 v 的部分）。若 v 的鄰居用了 `≤ 4` 種顏色，剩下的那一色給 v，完成。

否則 v 恰有 5 個鄰居，顏色兩兩不同。依它們在嵌入中繞著 v 的**循環順序**命名為 `v1, v2, v3, v4, v5`，顏色分別為 `1, 2, 3, 4, 5`。

**第一步**：看顏色 1、3 的子圖中，含 `v1` 的分量 `K13`。

- 若 `v3 ∉ K13`：對 `K13` 做 Kempe 交換。`v1` 變成 3，`v3` 不在分量裡仍是 3，其他鄰居不受影響（顏色不是 1、3）。現在 v 的鄰居裡沒有顏色 1，把 1 給 v。完成。
- 若 `v3 ∈ K13`：存在一條從 `v1` 到 `v3`、所有點顏色都是 1 或 3 的路徑 P。P 加上邊 `v3–v`、`v–v1` 構成一個圈 C。

**第二步**：C 在嵌入中是一條不自交的閉曲線（Jordan 曲線定理，Ch 18），把平面分成內外兩區。在 v 周圍，邊 `vv1`、`vv3` 屬於 C，而循環順序是 `v1, v2, v3, v4, v5`，所以邊 `vv2` 進入 C 的一側，`vv4`、`vv5` 進入另一側：`v2` 和 `v4` 分處 C 的內外。

任何從 `v2` 到 `v4` 的路徑若不經過 v，都必須穿過 C；平面嵌入中邊不交叉，穿過只能發生在**點**上，也就是路徑上某個點屬於 P，顏色是 1 或 3。所以顏色 2、4 的子圖裡，不存在從 `v2` 到 `v4` 的路徑：`v4 ∉ K24`。

對 `K24`（含 `v2` 的 2-4 分量）做 Kempe 交換：`v2` 變成 4，`v4` 仍是 4，v 的鄰居裡沒有顏色 2 了，把 2 給 v。完成。∎

整個證明的關鍵是第二步的**平面拓撲**：兩條 Kempe chain 不能「交叉」。這是五色能做到、而這套論證推不到四色的地方——四色時鄰居顏色有重複，需要**同時**操作兩條 chain，而兩條 chain 之間的互動，平面性擋不住。

## 實作：會做 Kempe 交換的 5-著色

證明是構造性的，直接翻成演算法：依「反覆拔最小度數點」的順序拔光，再倒過來逐點放回並著色；遇到 5 個鄰居 5 種顏色時做 Kempe 交換。

需要的唯一外部資訊是**循環順序**——證明的第二步靠它判斷 `v2`、`v4` 分處兩側。我們用 `nx.check_planarity` 回傳的嵌入取得每個點鄰居的順時針順序（`neighbors_cw_order`）；拿掉點之後，剩下鄰居的相對順序不變，所以從原嵌入過濾即可。著色演算法本身完全自刻，NetworkX 只提供嵌入與對答案。

```python
import numpy as np
import networkx as nx
from scipy.spatial import Delaunay


def removal_order(adj):
    """反覆拔目前度數最小的點（bucket 版，近線性）；平面圖保證每次度數 ≤ 5。"""
    deg = {v: len(adj[v]) for v in adj}
    buckets = {}
    for v, d in deg.items():
        buckets.setdefault(d, set()).add(v)
    alive, order, d = set(adj), [], 0
    while alive:
        d = max(d - 1, 0)
        while not buckets.get(d):
            d += 1
        v = min(buckets[d])
        buckets[d].discard(v)
        assert d <= 5, "最小度數 > 5：輸入不是平面圖"
        order.append(v)
        alive.discard(v)
        for w in adj[v]:
            if w in alive:
                buckets[deg[w]].discard(w)
                deg[w] -= 1
                buckets.setdefault(deg[w], set()).add(w)
    return order


def kempe_component(adj, col, start, pair):
    """從 start 出發、只走顏色在 pair 裡的已著色點。"""
    seen, stack = {start}, [start]
    while stack:
        u = stack.pop()
        for w in adj[u]:
            if w in col and w not in seen and col[w] in pair:
                seen.add(w)
                stack.append(w)
    return seen


def five_color(G):
    planar, emb = nx.check_planarity(G)
    assert planar, "不是平面圖"
    adj = {u: set(G[u]) for u in G}
    col, swaps = {}, 0
    for v in reversed(removal_order(adj)):
        nbrs = [u for u in emb.neighbors_cw_order(v) if u in col] if adj[v] else []
        used = {col[u] for u in nbrs}
        if len(used) < 5:
            col[v] = min(set(range(5)) - used)
            continue
        v1, v2, v3, v4, v5 = nbrs                          # 5 個鄰居、5 種顏色、循環順序
        for a, b in [(v1, v3), (v2, v4)]:
            comp = kempe_component(adj, col, a, {col[a], col[b]})
            if b not in comp:                              # 證明保證兩組至少一組成立
                ca, cb = col[a], col[b]
                for u in comp:
                    col[u] = cb if col[u] == ca else ca
                col[v] = ca
                swaps += 1
                break
        else:
            raise AssertionError("v1-v3 與 v2-v4 都被 Kempe chain 連通：違反五色定理的證明")
    return col, swaps


def ok(G, col, k):
    return all(col[u] != col[w] for u, w in G.edges()) and set(col.values()) <= set(range(k))


rng = np.random.default_rng(19)
for n in (20, 200, 2000):
    total_swaps = 0
    for _ in range(10):
        T = nx.Graph()
        for a, b, c in Delaunay(rng.random((n, 2))).simplices:
            T.add_edges_from([(int(a), int(b)), (int(b), int(c)), (int(a), int(c))])
        col, sw = five_color(T)
        assert ok(T, col, 5)
        total_swaps += sw
    print(f"Delaunay n={n:4d} ×10：全部合法 5 著色，Kempe 交換共 {total_swaps} 次")

I = nx.icosahedral_graph()                                 # 每點度數 5
col, sw = five_color(I)
print("二十面體：合法", ok(I, col, 5), "用色", len(set(col.values())), "交換", sw)
planar_atlas = [G for G in nx.graph_atlas_g()[1:] if nx.check_planarity(G)[0]]
assert all(ok(G, five_color(G)[0], 5) for G in planar_atlas)
print(f"atlas 中 {len(planar_atlas)} 張平面圖全部合法 5 著色")
```

```text
Delaunay n=  20 ×10：全部合法 5 著色，Kempe 交換共 0 次
Delaunay n= 200 ×10：全部合法 5 著色，Kempe 交換共 0 次
Delaunay n=2000 ×10：全部合法 5 著色，Kempe 交換共 0 次
二十面體：合法 True 用色 5 交換 0
atlas 中 1015 張平面圖全部合法 5 著色
```

全部合法，但真正值得注意的是 **Kempe 交換一次都沒發生**：「拔最小度數點」的順序下，拔的時候度數常常 `≤ 4`，放回時鄰居不到 5 個，交換分支根本走不到。這是「對拍通過」最危險的形式——測試全綠，但最關鍵的程式碼路徑**從沒被執行過**。

要真正測到 Kempe 分支，得**刻意製造**證明裡的那個局面：取一個度數 5 的點 v，把 `G − v` 先 5 著色，再用隨機 Kempe 交換打亂（每次交換都保持合法，由引理保證），直到 v 的 5 個鄰居恰好 5 種顏色，然後單獨跑 Kempe 那一步。

```python
# 接續上一個 block 的定義
import random

R = random.Random(19)
stat = {"v1–v3 不連通": 0, "v1–v3 連通，改用 v2–v4": 0}
for _ in range(100):
    T = nx.Graph()
    for a, b, c in Delaunay(rng.random((40, 2))).simplices:
        T.add_edges_from([(int(a), int(b)), (int(b), int(c)), (int(a), int(c))])
    _, emb = nx.check_planarity(T)
    for v in [u for u in T if T.degree(u) == 5]:
        H = T.copy()
        H.remove_node(v)
        col, _ = five_color(H)
        hadj = {u: set(H[u]) for u in H}
        rot = list(emb.neighbors_cw_order(v))
        for _ in range(60):                                # 隨機 Kempe 交換，打亂著色
            u = R.choice(sorted(H))
            c2 = R.choice([c for c in range(5) if c != col[u]])
            c1 = col[u]
            for w in kempe_component(hadj, col, u, {c1, c2}):
                col[w] = c2 if col[w] == c1 else c1
            if len({col[x] for x in rot}) == 5:
                break
        else:
            continue
        assert ok(H, col, 5)
        v1, v2, v3, v4, v5 = rot
        if v3 not in kempe_component(hadj, col, v1, {col[v1], col[v3]}):
            stat["v1–v3 不連通"] += 1
        else:
            assert v4 not in kempe_component(hadj, col, v2, {col[v2], col[v4]})   # 第二步的拓撲論證
            stat["v1–v3 連通，改用 v2–v4"] += 1
print(stat)
```

```text
{'v1–v3 不連通': 794, 'v1–v3 連通，改用 v2–v4': 72}
```

兩個分支都被大量走到，而且每一次「v1–v3 被連通」時，第二步的 `assert`（v2–v4 一定不連通）都成立——這就是 Jordan 曲線論證在近九百個實例上的實證（不是證明，證明在上一節）。

## Kempe 的四色「證明」錯在哪裡

Kempe 1879 年的論證和五色證明幾乎一樣，差在四色時度數 5 的點：5 個鄰居只有 4 種顏色，必有一色出現兩次。循環順序 `v1..v5`，設顏色為 `1, 2, 3, 4, 2`。可以這樣假設的原因：若鄰居只用了 ≤ 3 色，直接有空色；用了 4 色時恰有一色重複兩次。在三角化（最小反例可以假設是三角化）中，循環順序上相鄰的兩個鄰居彼此相鄰、不可能同色，所以重複的兩點不相鄰，旋轉命名後就是 `v2`、`v5`。

```text
           v1(1)
     v5(2)       v2(2)          目標：讓 v 的鄰居騰出一種顏色
         \   v   /
     v4(4) ───── v3(3)

Kempe 的論證：
  若 v1、v3 不被 1-3 chain 連通 → 交換，騰出 1。完成。
  若 v1、v4 不被 1-4 chain 連通 → 交換，騰出 1。完成。
  否則兩條 chain 都在：
    1-3 chain（v1…v3）+ v 圍成圈，把 v2 和 v4 隔開 → v2 的 2-4 chain 碰不到 v4
    1-4 chain（v1…v4）+ v 圍成圈，把 v5 和 v3 隔開 → v5 的 2-3 chain 碰不到 v3
  Kempe：「同時」交換 v2 的 2-4 chain 與 v5 的 2-3 chain
         → v2 變 4、v5 變 3，騰出 2 給 v。
```

每一步單獨看都對。漏洞在最後一步：**兩次交換不是獨立的**。第一次交換（2-4）改變了圖上的顏色，第二條 chain（2-3）是用**交換前**的顏色算的；而且兩條 chain 可以在某處相鄰——2-4 chain 上原本是 4 的點變成 2，2-3 chain 上原本是 3 的點也變成 2，如果這兩個點相鄰，就撞色了。Heawood 1890 年給出了一張具體的地圖讓這件事發生。

我們不需要 Heawood 的地圖：用隨機 Delaunay 三角化搜尋（seed 固定），在 9 個點的圖上就找到了一個實例。下面把它寫死，逐步重演 Kempe 的論證：

```python
# 接續上一個 block 的定義
E9 = [(0, 2), (0, 3), (0, 4), (0, 7), (1, 3), (1, 4), (1, 5), (1, 6), (1, 8), (2, 5),
      (2, 6), (2, 7), (2, 8), (3, 4), (3, 5), (3, 7), (4, 8), (5, 6), (5, 7), (6, 8)]
G9 = nx.Graph(E9)
v = 3
col = {0: 0, 1: 1, 2: 1, 4: 3, 5: 3, 6: 0, 7: 2, 8: 2}      # G9 − v 的合法 4 著色
H = G9.copy()
H.remove_node(v)
hadj = {u: set(H[u]) for u in H}
_, emb = nx.check_planarity(G9)
rot = list(emb.neighbors_cw_order(v))
s = next(s for s in range(5) if col[rot[(s + 1) % 5]] == col[rot[(s + 4) % 5]])   # 讓重複色落在 v2、v5
v1, v2, v3, v4, v5 = rot[s:] + rot[:s]
print("G9 − v 著色合法:", ok(H, col, 4), " 平面:", nx.check_planarity(G9)[0])
print("v 的鄰居（循環順序）與顏色:", [(x, col[x]) for x in (v1, v2, v3, v4, v5)])
c1, c2, c3, c4 = col[v1], col[v2], col[v3], col[v4]
print("v1–v3 被", (c1, c3), "chain 連通:", v3 in kempe_component(hadj, col, v1, {c1, c3}))
print("v1–v4 被", (c1, c4), "chain 連通:", v4 in kempe_component(hadj, col, v1, {c1, c4}))
K24 = kempe_component(hadj, col, v2, {c2, c4})
K23 = kempe_component(hadj, col, v5, {c2, c3})
print("v2 的 chain:", sorted(K24), "含 v4?", v4 in K24, "｜ v5 的 chain:", sorted(K23), "含 v3?", v3 in K23)
new = dict(col)
for u in K24:
    new[u] = c4 if col[u] == c2 else c2
for u in K23:
    new[u] = c3 if col[u] == c2 else c2
print("同時交換後 v 的鄰居顏色:", [new[x] for x in (v1, v2, v3, v4, v5)])
print("撞色的邊:", [(a, b, new[a]) for a, b in H.edges() if new[a] == new[b]])
```

```text
G9 − v 著色合法: True  平面: True
v 的鄰居（循環順序）與顏色: [(1, 1), (4, 3), (0, 0), (7, 2), (5, 3)]
v1–v3 被 (1, 0) chain 連通: True
v1–v4 被 (1, 2) chain 連通: True
v2 的 chain: [4, 8] 含 v4? False ｜ v5 的 chain: [5, 6] 含 v3? False
同時交換後 v 的鄰居顏色: [1, 2, 0, 2, 0]
撞色的邊: [(6, 8, 3)]
```

逐行對照 Kempe 的論證：兩條 1-x chain 都存在（所以前兩個「完成」都走不到），兩條 2-x chain 也確實碰不到各自的目標（拓撲論證本身沒錯），交換後 v 的鄰居也真的騰出了顏色——但圖的其他地方撞色了。撞色的兩個點一個在 2-4 chain、一個在 2-3 chain，兩條 chain 相鄰。論證**局部**正確、**全域**錯誤，這正是它被數學界接受了 11 年才被發現的原因。

G9 本身當然是 4 可著色的（它是平面圖，四色定理保證；9 個點也可以暴力確認）。失敗的是 Kempe 的**修復程序**，不是四色定理。

## 四色定理：證明架構

**定理（Appel–Haken 1976/1977；Robertson–Sanders–Seymour–Thomas 1997）**：平面圖 `χ ≤ 4`。

**這裡只給證明架構，不證。** 證明的骨架和五色一樣是「最小反例」論證，但「拔一個點」換成「拔一個構形」：

```text
假設 G 是點數最少的、不能 4 著色的平面圖（最小反例）。可以假設它是三角化（每個面都是三角形）。

(1) 不可避免性（unavoidability）：
    給一個有限的構形清單 U。證明：每個三角化平面圖都至少含有 U 中的一個構形。
    工具：discharging（放電法）——
      每個點先放電荷 6 − deg(v)（RSST 版用 10(6 − deg(v))）；
      由 Euler 公式，總電荷是正的常數（Σ(6 − deg) = 6V − 2E = 12，因為三角化 E = 3V − 6）。
      設計一組「把電荷從某些點移到鄰近點」的規則，證明：
      若圖不含 U 的任何構形，移動後每個點電荷都 ≤ 0 → 總和 ≤ 0，矛盾。

(2) 可約性（reducibility）：
    證明 U 中每個構形都不能出現在最小反例裡：
    把構形挖掉、對剩下的較小圖 4 著色（由最小性可以）、
    檢查邊界的「每一種」著色都能（必要時經過 Kempe 交換）延伸回構形內部。
    這是有限但巨量的窮舉 → 電腦檢查。

(1) + (2)：最小反例一定含某個構形，但它不能含任何構形 → 最小反例不存在。
```

度數 `≤ 4` 的點就是最簡單的可約構形（Kempe 的論證對度數 4 是對的）；度數 5 的點**不是**單獨可約的——這正是 Kempe 錯的地方。所以不可避免集合必須用更大的構形取代「度數 5 的點」。

| | Appel–Haken（1977） | Robertson–Sanders–Seymour–Thomas（1997） |
| --- | --- | --- |
| 不可避免集合大小 | 1977 年論文約 1,900 多個構形，Appel–Haken 1989 年的專書縮減到 1,400 多個（各來源數字略有出入） | 633 個 |
| discharging 規則 | 數百條（RSST 論文的說法是 300 條以上） | 32 條 |
| 人工檢查部分 | 不可避免性的證明有大量手工檢查，難以複驗 | 不可避免性也交給電腦檢查 |
| 演算法副產品 | — | `O(n²)` 時間的平面圖 4 著色演算法 |

**Gonthier 的 Coq 形式化（2004–2005 完成，2008 年發表於 Notices of the AMS）**：Georges Gonthier（與 Benjamin Werner 合作）在 Coq 證明輔助器中，從平面圖的組合定義（用 hypermap 表示，避開實數拓撲）開始，把 RSST 證明的**全部**內容——包括 633 個構形的可約性計算——寫成機器檢查的證明。它驗證的是：「如果你相信 Coq 的核心（一個小的型別檢查器），你就不需要相信任何其他程式或任何人的手工檢查」。這把爭議從「電腦程式有沒有 bug」縮小到「證明輔助器的可信核心有沒有 bug」。

## 對比與取捨

| 結果 | 證明長度 | 可人工檢查？ | 演算法 |
| --- | --- | --- | --- |
| 六色 | 三行 | 是 | 線性時間（smallest-last 貪婪） |
| 五色 | 一頁 | 是 | 本章實作；文獻有線性時間版本 |
| 四色（Appel–Haken） | 數百頁 + 電腦 | 只有部分 | 二次方時間以上 |
| 四色（RSST + Gonthier） | 電腦 + 形式化 | 由證明輔助器檢查 | RSST：`O(n²)` |
| 平面圖 3 著色判定 | — | — | NP-complete（Garey–Johnson–Stockmeyer 1976） |

實務上的提醒：**四色定理是存在性保證，不是好用的演算法**。真的要把地圖或平面圖著成 4 色，常見做法是 DSATUR 之類的啟發式加回溯，小圖上很快；要保證 5 色、近線性時間，本章的 Kempe 演算法就夠了。而「能不能 3 色」在平面圖上仍是 NP-complete，平面性並沒有讓著色問題全面變簡單。

## 踩雷集錦

1. **「五色的證明改一下就是四色」**：這正是 Kempe 的錯。四色時度數 5 的點需要**同時**操作兩條 chain，而平面拓撲只保證每條 chain 單獨碰不到目標，不保證兩條 chain 互不干擾。本章的 9 點反例把這件事具體化了。
2. **把 Kempe 交換套在整個二色子圖上**：交換必須只在**一個連通分量**裡做。若把整張圖的 1、3 全部對調，v3 也會被換掉，什麼都沒騰出來；引理的證明也只對單一分量成立。
3. **忘了循環順序**：五色證明第二步依賴 `v1..v5` 是繞著 v 的順序，所以 `v1-v3` 與 `v2-v4` 是「交錯」的兩對。若隨便取順序（例如把 `v1, v2` 配對），chain 的分隔論證不成立。程式裡用 `neighbors_cw_order` 就是為了這個。
4. **測試全綠但關鍵路徑沒跑到**：本章第一個實驗裡 Kempe 交換 0 次。如果只看「全部合法」就收工，交換分支裡的 bug 永遠不會被發現。要刻意構造會走到那條路徑的輸入，或量測覆蓋率。
5. **「四色定理說地圖 4 色就夠」的邊界條件**：國家必須是連通的一塊（飛地會破壞對偶圖的平面性），「相鄰」必須是共用一段邊界而不是只共用一點。放寬這些條件，需要的顏色數可以任意多。

## 進階：再往深一層

- **List coloring**：Thomassen（1994）證明每個平面圖都是 5-choosable（每點從自己的 5 色清單挑，總能合法著色），證明只有半頁，而且比五色定理更強；Voigt（1993）給出不是 4-choosable 的平面圖，所以四色定理沒有清單版本。
- **Hadwiger 猜想**：四色定理等價於「沒有 K5 minor 的圖 4 可著色」（Wagner 定理 + Wagner 1937 的結構定理）；推廣成「沒有 `K_t` minor 則 `t − 1` 可著色」，t = 6 由 Robertson–Seymour–Thomas 1993 化約到四色定理，t ≥ 7 仍未解。
- **Heawood 公式與曲面**：Heawood 在指出 Kempe 錯誤的同一篇論文裡，給了虧格 g 曲面的上界 `⌊(7 + √(1 + 48g)) / 2⌋`；Ringel–Youngs（1968）證明它對 g ≥ 1 是緊的。諷刺的是，平面（g = 0）代入得到 4，卻是最難證的情況。
- **形式化驗證的工程**：Gonthier 的工作催生了 SSReflect（Coq 的證明語言擴充）與 Mathematical Components 函式庫，後來用在 Feit–Thompson 奇數階定理的形式化。對想學證明輔助器的工程師，這是最好的大型範例。

## 動手練習

1. 把 `five_color` 改成「鄰居用了 < 5 色時隨機挑一個空色」（固定 seed），在 Delaunay 圖上統計 Kempe 交換次數，看它是否仍然很少，解釋原因。
2. 在 G9 上照**循序**的方式做 Kempe 的兩次交換（先換 2-4，再用新顏色重算 2-3 chain 並交換），看結果是否合法、v 是否騰出顏色。
3. 用 Ch 15 的回溯 `chromatic_number` 驗證 G9 的 `χ = 4`，並找出 G 的一個 4 著色。
4. 寫一個「6 色版」著色（smallest-last 貪婪，不做交換），在 2000 點 Delaunay 圖上比較用色數，和 5 色版、`nx.greedy_color(..., "DSATUR")` 對照。

## 本章重點整理

- 平面圖必有度數 `≤ 5` 的點 → 六色定理三行證完。
- Kempe 交換：在二色子圖的**一個連通分量**裡對調兩色，著色仍合法。
- 五色定理：度數 5 且鄰居 5 色時，交錯的兩對鄰居 `v1-v3`、`v2-v4` 不可能同時被 Kempe chain 連通——Jordan 曲線把它們隔開。
- Kempe 的四色論證錯在「同時」做兩次交換：兩條 chain 可以相鄰，撞色；本章 9 點圖重現了這個漏洞。
- 四色定理的證明是「不可避免集合（discharging）+ 每個構形可約（電腦窮舉）」，Gonthier 在 Coq 中完整形式化；本課不證。

## 自我檢核

- [ ] 不看筆記，寫出五色定理的完整證明，並指出 Jordan 曲線定理和循環順序各用在哪一步。
- [ ] 用自己的話解釋 Kempe 四色論證的漏洞；如果有人說「那循序做兩次交換不就好了」，你會怎麼回應？
- [ ] 解釋為什麼本章第一個實驗「全部合法」卻不足以證明 Kempe 交換的程式碼是對的，以及你會怎麼設計測試。
- [ ] 說出「不可避免性」和「可約性」各自在證明什麼，以及 discharging 的初始電荷為什麼加總是正的。
- [ ] Gonthier 的形式化消除了什麼疑慮、沒有消除什麼疑慮？

## 延伸閱讀

### 論文

- **Map-colour theorem** — P. J. Heawood，Quarterly Journal of Pure and Applied Mathematics 24（1890）
  - **核心貢獻**：指出 Kempe 證明的漏洞，用 Kempe chain 證明五色定理，並給出高虧格曲面的著色上界。
  - **讀哪裡**：歷史意義為主；論證內容已被本章與教科書完整轉述。
- **[Every planar map is four colorable. Part I: Discharging / Part II: Reducibility](https://projecteuclid.org/euclid.ijm/1256049023)** — K. Appel、W. Haken（Part II 與 J. Koch 合著），Illinois Journal of Mathematics 21(3)（1977）
  - **核心貢獻**：第一個四色定理證明；Part I 是 discharging，Part II 是可約性的電腦檢查。
  - **讀哪裡**：Part I 的引言與 discharging 的整體思路即可，細節極長。
- **[The Four-Colour Theorem](https://thomas.math.gatech.edu/PAP/fc.pdf)** — N. Robertson、D. Sanders、P. Seymour、R. Thomas，Journal of Combinatorial Theory Series B 70（1997）
  - **核心貢獻**：633 個構形、32 條 discharging 規則的簡化證明，全部檢查交給電腦，並得到 `O(n²)` 的 4 著色演算法。
  - **讀哪裡**：第 1–3 節（架構、構形與 discharging 的定義）；作者的 [Four Color Theorem 說明頁](https://thomas.math.gatech.edu/FC/fourcolor.html) 是更友善的入口。
- **Formal Proof—The Four-Color Theorem** — G. Gonthier，Notices of the AMS 55(11)（2008）
  - **核心貢獻**：說明如何在 Coq 中形式化平面圖（hypermap）、可約性計算與整個 RSST 證明。
  - **讀哪裡**：全文約 12 頁，寫給一般數學讀者；「hypermap」與「reflection」兩節是技術核心。
  - **前提**：本章的證明架構；不需要先會 Coq。

### 書籍與文章

- **[The four colour theorem（MacTutor History of Mathematics）](https://mathshistory.st-andrews.ac.uk/HistTopics/The_four_colour_theorem/)** — J. J. O'Connor、E. F. Robertson
  - **這篇說什麼**：從 Guthrie 到 Appel–Haken 的完整歷史，包括 Kempe、Tait、Heawood 的往來。
  - **為什麼值得讀**：引用原始文獻的段落，適合核對本章的歷史敘述。
- **《Graph Theory》** — Reinhard Diestel（Springer GTM 173，第 5 版，2017）
  - **讀哪幾章**：5.1 Colouring maps and planar graphs（五色定理）、5.4 List colouring（Thomassen 的 5-choosability 證明）。
- **《Four Colors Suffice: How the Map Problem Was Solved》** — Robin Wilson（Princeton University Press，2002；修訂版 2014）
  - **這本書的定位**：寫給一般讀者的四色問題史，對 discharging 與可約性有不用公式的清楚說明。

Part 3 到這裡結束。下一個練習把 Ch 15–17 拼起來：從一段線性 IR 出發，寫一個會 spill 的完整暫存器分配器。

→ [練習 C：Toy register allocator](./practice-c-register-allocator.md)
