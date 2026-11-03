# Ch 39 — 攻擊圖：路徑與最小切斷集

> **目標**：能把一份 AD 權限關係資料建成有向圖，算出到 Domain Admins 的最短攻擊路徑與路徑數，並用 max-flow/min-cut 回答防守方真正關心的問題：「預算只夠修三個地方，修哪三個能切斷所有路徑？」
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。本章所有 AD 資料都是**自己編的合成資料**（網域 `CORP.EXAMPLE`、帳號 `eve`、`carol` 等皆虛構），不連任何真實網域，也不使用任何蒐集工具。

## 為什麼需要這個？

2000 年前後的滲透測試報告長這樣：一份弱點清單，每個弱點有 CVSS 分數，依分數排序。防守方照著修，修了一百個高分弱點，紅隊下一次還是進得來——因為攻擊者不是利用「一個」弱點，而是**串起**一連串看起來都不嚴重的條件：某台工作站讓所有人都是本機管理員（中風險）、某個伺服器管理員在那台工作站登入過（不是弱點，是日常）、那個伺服器上有 Domain Admin 的登入 session（也是日常）。

紅隊早就用「攻擊圖」的方式思考，但都是手畫。Sheyner 等人在 IEEE S&P 2002 提出自動產生攻擊圖：把網路狀態建模成有限狀態機，用 model checker 列出所有能達成攻擊目標的狀態序列。問題是狀態數隨主機數指數成長。同一年 Ammann 等人（CCS 2002）提出**單調性假設**（attacker 取得的權限不會失去），讓圖的大小變成多項式；MulVAL（USENIX Security 2005）用 Datalog 規則推出「邏輯攻擊圖」，在數千台機器的網路上幾秒內算完。

2016 年 BloodHound 把這套思路帶到 Active Directory：節點是使用者、群組、電腦，邊是「控制了 u 就能取得 v」的關係（`MemberOf`、`AdminTo`、`HasSession`……）。今天藍隊用它做的事，正是本章的兩個問題：**最短路徑**（攻擊者最可能走哪條）和**最小切斷集**（修哪裡 CP 值最高）。這兩個問題你在 [Ch 12 Menger 定理](./12-menger-theorem.md) 和 [Ch 20 最大流最小割](./20-max-flow-min-cut.md) 已經證明過了；這章是它們在資安上的落地。

## 先建立直覺

把 AD 想成一棟大樓的鑰匙系統：

- 你是實習生 eve，手上只有大門卡（`Domain Users` 成員）。
- 大門卡居然能開 3 號辦公室（`Domain Users AdminTo WS03`——某人設定錯了）。
- 3 號辦公室的抽屜裡有 carol 留下的鑰匙圈（`WS03 HasSession carol`：carol 曾經在 WS03 登入，憑證可能還在記憶體）。
- carol 的鑰匙圈能開伺服器機房（`carol MemberOf ServerAdmins`，`ServerAdmins AdminTo SRV01`）。
- 機房裡有總經理的萬能鑰匙（`SRV01 HasSession da_admin`）。

```text
eve ─MemberOf→ DomainUsers ─AdminTo→ WS03 ─HasSession→ carol ─MemberOf→ ServerAdmins
                                                                          │AdminTo
                                                         ┌────────────────┴───────┐
                                                         ▼                        ▼
                                                       SRV01                    SRV02
                                                         └─HasSession→ da_admin ←─┘
                                                                          │MemberOf
                                                                          ▼
                                                                     DomainAdmins
```

**邊的方向是「控制流向」**：`u → v` 表示控制了 u 的人可以取得 v 的權限。`MemberOf` 從使用者指向群組（使用者繼承群組的權限），`HasSession` 從電腦指向使用者（控制電腦的人可能拿到登入者的憑證）。BloodHound 的官方文件把 `HasSession` 定義為 Computer → User，方向和本章一致。

有了這個方向，「eve 能不能變成 DA」就是有向圖的可達性；「最少幾步」是 BFS；「修哪裡」是割。

## 攻擊圖的三種模型

| 模型 | 節點 | 邊 | 大小 | 代表 |
| --- | --- | --- | --- | --- |
| 狀態攻擊圖 | 整個網路的狀態（每台機器、每個權限的布林向量） | 一個原子攻擊讓狀態轉移 | 指數級 | Sheyner 等，IEEE S&P 2002 |
| 邏輯/相依攻擊圖 | 事實（「攻擊者在 H 上有 root」）與規則實例 | 前提 → 結論 | 多項式（在單調性假設下） | MulVAL，USENIX Security 2005 |
| 權限關係圖 | 主體（使用者、群組、電腦） | 「控制 u ⇒ 取得 v」 | 線性於關係數 | BloodHound |

這三者的差別在**一條邊代表什麼**：

- 狀態攻擊圖裡，同一個原子攻擊（例如「利用 SRV01 的某個漏洞」）在成千上萬個狀態轉移上都會出現。防守方「修掉這個漏洞」等於**一次刪掉所有標著這個攻擊的邊**。Jha、Sheyner、Wing 在 CSFW 2002 證明了「找最少的防禦措施使攻擊目標不可達」和最小 hitting set 問題多項式等價，所以是 NP-hard，他們給的是有近似保證的貪婪演算法。
- 權限關係圖裡，每條邊大致對應一個獨立的設定（一筆群組成員、一個本機管理員授權、一個登入 session）。修一個設定 ≈ 刪一條邊，於是「最少修幾個」變成**最小邊割**，用 max-flow 在多項式時間解。

**單調性假設**是讓後兩種模型變小的關鍵：攻擊者拿到的權限不會因為後續動作而失去，所以「已經控制了哪些節點」只會變大，可達性就夠了，不需要記錄順序與狀態。這個假設在 AD 上大致成立，但不是永遠：改了某人的密碼（`ForceChangePassword`）會讓那個人的 session 失效、也會驚動他本人。這是模型的限制，報告裡要寫出來。

## 範例一：最短攻擊路徑與路徑計數

合成資料 `ad_lab.json`（19 個節點、32 條邊）：

```json
{
  "domain": "CORP.EXAMPLE",
  "nodes": [
    {"id": "eve",         "kind": "User"},
    {"id": "alice",       "kind": "User"},
    {"id": "bob",         "kind": "User"},
    {"id": "carol",       "kind": "User"},
    {"id": "dave",        "kind": "User"},
    {"id": "svc_backup",  "kind": "User"},
    {"id": "da_admin",    "kind": "User"},
    {"id": "DomainUsers", "kind": "Group"},
    {"id": "Helpdesk",    "kind": "Group"},
    {"id": "IT_Admins",   "kind": "Group"},
    {"id": "ServerAdmins","kind": "Group"},
    {"id": "BackupOps",   "kind": "Group"},
    {"id": "DomainAdmins","kind": "Group"},
    {"id": "WS01",        "kind": "Computer"},
    {"id": "WS02",        "kind": "Computer"},
    {"id": "WS03",        "kind": "Computer"},
    {"id": "SRV01",       "kind": "Computer"},
    {"id": "SRV02",       "kind": "Computer"},
    {"id": "DC01",        "kind": "Computer"}
  ],
  "edges": [
    ["eve", "MemberOf", "DomainUsers"],
    ["alice", "MemberOf", "DomainUsers"],
    ["bob", "MemberOf", "DomainUsers"],
    ["carol", "MemberOf", "DomainUsers"],
    ["dave", "MemberOf", "DomainUsers"],
    ["svc_backup", "MemberOf", "DomainUsers"],
    ["da_admin", "MemberOf", "DomainUsers"],
    ["alice", "MemberOf", "Helpdesk"],
    ["bob", "MemberOf", "IT_Admins"],
    ["carol", "MemberOf", "ServerAdmins"],
    ["dave", "MemberOf", "Helpdesk"],
    ["svc_backup", "MemberOf", "BackupOps"],
    ["da_admin", "MemberOf", "DomainAdmins"],
    ["Helpdesk", "AdminTo", "WS01"],
    ["Helpdesk", "AdminTo", "WS02"],
    ["DomainUsers", "AdminTo", "WS03"],
    ["IT_Admins", "AdminTo", "SRV01"],
    ["IT_Admins", "AdminTo", "SRV02"],
    ["ServerAdmins", "AdminTo", "SRV01"],
    ["ServerAdmins", "AdminTo", "SRV02"],
    ["BackupOps", "AdminTo", "DC01"],
    ["DomainAdmins", "AdminTo", "DC01"],
    ["WS01", "HasSession", "bob"],
    ["WS02", "HasSession", "alice"],
    ["WS03", "HasSession", "carol"],
    ["WS03", "HasSession", "dave"],
    ["SRV01", "HasSession", "da_admin"],
    ["SRV02", "HasSession", "svc_backup"],
    ["SRV02", "HasSession", "da_admin"],
    ["DC01", "HasSession", "da_admin"],
    ["Helpdesk", "ForceChangePassword", "bob"],
    ["IT_Admins", "GenericAll", "ServerAdmins"]
  ]
}
```

BFS 本身你寫過很多次。這裡要多做一件事：**同時數最短路徑有幾條**。在 BFS 樹上，令 `σ(v)` 為從 s 到 v 的最短路徑數，則

```text
σ(s) = 1
σ(w) = Σ σ(u)，對所有滿足 (u, w) ∈ E 且 dist(u) = dist(w) − 1 的 u
```

**命題**。上式算出的 σ(w) 等於 s 到 w 的最短路徑數。

**證明**。對 `dist(w)` 歸納。`dist = 0` 只有 s 本身，σ(s) = 1 正確。設 `dist(w) = k ≥ 1`。任一條 s 到 w 的最短路徑 P，最後一條邊是某個 `(u, w)`，P 去掉最後一條邊是 s 到 u、長度 k − 1 的路徑；因為 `dist(u) ≥ dist(w) − 1 = k − 1`（否則 w 會更近）且這條路長度 k − 1，所以 `dist(u) = k − 1`，這段是 s 到 u 的最短路徑。反過來，對任何 `dist(u) = k − 1` 且 `(u, w) ∈ E` 的 u，s 到 u 的每條最短路徑接上 `(u, w)` 都是長度 k 的 s–w 路徑，也就是最短路徑。這給出一個雙射：「s 到 w 的最短路徑」↔「（前驅 u，s 到 u 的最短路徑）的配對」。由歸納假設，後者的個數是 `Σ σ(u)`。∎

實作上要注意：BFS 處理 u 時，w 可能是第一次看到（設 dist 並把 σ 清零），也可能是同一層另一個前驅已經看過；只要 `dist(w) == dist(u) + 1` 就累加。因為 BFS 一層一層處理，u 被 pop 時 σ(u) 已經是最終值。

```python
# ch39_paths.py
"""Ch 39 範例一：載入合成 AD 圖，求最短攻擊路徑、最短路徑數、所有簡單路徑數。"""
import json
import sys
from collections import deque
import networkx as nx

PATH = sys.argv[1] if len(sys.argv) > 1 else "ad_lab.json"


def load(path):
    data = json.load(open(path, encoding="utf-8"))
    kind = {n["id"]: n["kind"] for n in data["nodes"]}
    succ = {v: set() for v in kind}
    label = {}
    for u, rel, v in data["edges"]:
        succ[u].add(v)
        label[(u, v)] = rel
    return kind, succ, label


def bfs_count(succ, s):
    """BFS 同時算距離 dist 與最短路徑數 sigma（σ(v) = Σ σ(u)，u 是 v 在上一層的前驅）。"""
    dist, sigma = {s: 0}, {s: 1}
    q = deque([s])
    while q:
        u = q.popleft()
        for w in succ[u]:
            if w not in dist:
                dist[w] = dist[u] + 1
                sigma[w] = 0
                q.append(w)
            if dist[w] == dist[u] + 1:
                sigma[w] += sigma[u]
    return dist, sigma


def shortest_paths(succ, dist, s, t):
    """沿 BFS DAG 從 t 往回列舉所有最短路徑。"""
    pred = {v: [u for u in succ if v in succ[u] and u in dist and dist[u] == dist[v] - 1] for v in dist}
    out = []

    def back(v, acc):
        if v == s:
            out.append([s] + acc[::-1])
            return
        for u in sorted(pred[v]):
            back(u, acc + [v])
    back(t, [])
    return out


def count_simple_paths(succ, s, t):
    """DFS 列舉簡單路徑（不重複節點）並計數；最壞指數時間。"""
    cnt, on = 0, {s}

    def dfs(u):
        nonlocal cnt
        if u == t:
            cnt += 1
            return
        for w in succ[u]:
            if w not in on:
                on.add(w)
                dfs(w)
                on.remove(w)
    dfs(s)
    return cnt


if __name__ == "__main__":
    kind, succ, label = load(PATH)
    G = nx.DiGraph([(u, v) for u in succ for v in succ[u]])
    print(f"nodes={len(succ)} edges={sum(map(len, succ.values()))}")
    s, t = "eve", "DomainAdmins"
    dist, sigma = bfs_count(succ, s)
    print(f"dist({s}->{t}) = {dist[t]}, 最短路徑數 = {sigma[t]}")
    for p in shortest_paths(succ, dist, s, t):
        print("  " + " ".join(f"{a} -[{label[(a, b)]}]->" for a, b in zip(p, p[1:])) + f" {t}")
    nxp = sorted(nx.all_shortest_paths(G, s, t))
    print("networkx 最短路徑數:", len(nxp), "一致:", nxp == sorted(shortest_paths(succ, dist, s, t)))
    n_simple = count_simple_paths(succ, s, t)
    print("簡單路徑總數:", n_simple, "networkx:", sum(1 for _ in nx.all_simple_paths(G, s, t)))
    # 每個使用者到 DA 的最短距離：防守方要先看「誰離 DA 最近」
    print("各使用者到 DomainAdmins 的距離 / 最短路徑數：")
    for u in sorted(v for v in kind if kind[v] == "User"):
        d, sg = bfs_count(succ, u)
        print(f"  {u:10} dist={d.get(t, '∞')} paths={sg.get(t, 0)}")
```

```text
nodes=19 edges=32
dist(eve->DomainAdmins) = 7, 最短路徑數 = 2
  eve -[MemberOf]-> DomainUsers -[AdminTo]-> WS03 -[HasSession]-> carol -[MemberOf]-> ServerAdmins -[AdminTo]-> SRV01 -[HasSession]-> da_admin -[MemberOf]-> DomainAdmins
  eve -[MemberOf]-> DomainUsers -[AdminTo]-> WS03 -[HasSession]-> carol -[MemberOf]-> ServerAdmins -[AdminTo]-> SRV02 -[HasSession]-> da_admin -[MemberOf]-> DomainAdmins
networkx 最短路徑數: 2 一致: True
簡單路徑總數: 15 networkx: 15
各使用者到 DomainAdmins 的距離 / 最短路徑數：
  alice      dist=6 paths=2
  bob        dist=4 paths=2
  carol      dist=4 paths=2
  da_admin   dist=1 paths=1
  dave       dist=6 paths=2
  eve        dist=7 paths=2
  svc_backup dist=4 paths=1
```

讀這份輸出時，防守方應該注意三件事：

1. **最短路徑 7 跳，但前 4 跳完全相同**。兩條最短路徑都經過 `DomainUsers → WS03 → carol → ServerAdmins`，差別只在最後走 SRV01 還是 SRV02。共同前綴上的任何一條邊都是「單點故障」——下一節的割會證實這件事。
2. **簡單路徑 15 條，最短路徑只有 2 條**。報告裡寫「eve 有 15 條路到 DA」聽起來嚇人，但那些長路徑大多是繞道（例如經過 `IT_Admins GenericAll ServerAdmins`）。兩個數字回答不同問題：最短路徑數衡量「攻擊者最省力的選擇有多少」，簡單路徑數衡量「整體暴露面」。
3. **每個一般帳號都能到 DA**。這比 eve 那條路更嚴重：alice、dave 是 helpdesk，bob 是 IT，任何一個被釣魚都能走到底。

## 底層機制：簡單路徑計數為什麼是難的

最短路徑數可以在 `O(V + E)` 時間內算出（上面的 σ），簡單路徑數卻沒有這種好運：Valiant（SIAM J. Computing，1979）證明了計算有向或無向圖中 s–t 簡單路徑數是 **#P-complete**。#P 是「數 NP 問題的見證有幾個」的複雜度類別；#P-complete 意味著如果它有多項式演算法，所有 #P 問題都有，包括計算布林公式的滿足賦值個數——普遍相信不可能。本章不證明這個結果。

直覺上的差別在於：最短路徑有**最佳子結構**——最短路徑的前綴也是最短路徑，所以可以在 DAG（BFS 層次圖）上做動態規劃。簡單路徑沒有：一條 s–w 的簡單路徑能不能接上 `(w, x)`，取決於它**之前有沒有經過 x**，這是整條路徑的全域資訊，沒辦法壓縮成每個節點一個數字。

實測爆炸速度。下面這張圖是 k 個串接的「菱形」，每個菱形有 a、b 兩條路線，外加一條橫向邊 `b → a`，所以每過一個菱形有 3 種走法：

```python
# ch39_extra.py（與 ch39_paths.py 同目錄）
"""Ch 39 範例三：簡單路徑數的爆炸 vs 最短路徑數的多項式計數；以及機率加權的「最可能路徑」。"""
import math
import time
import networkx as nx
from ch39_paths import bfs_count, count_simple_paths

# 一串 k 個「菱形」：每層有兩條平行路線，再加一條回頭邊讓圖有環
for k in [4, 8, 12, 16]:
    succ = {}
    def add(u, v):
        succ.setdefault(u, set()).add(v)
        succ.setdefault(v, set())
    for i in range(k):
        add(f"h{i}", f"a{i}"); add(f"h{i}", f"b{i}")
        add(f"a{i}", f"h{i+1}"); add(f"b{i}", f"h{i+1}")
        add(f"b{i}", f"a{i}")                     # 橫向邊：a、b 之間也能走
    t0 = time.perf_counter(); n_simple = count_simple_paths(succ, "h0", f"h{k}"); t1 = time.perf_counter()
    dist, sigma = bfs_count(succ, "h0"); t2 = time.perf_counter()
    print(f"k={k:2}: 簡單路徑 {n_simple:>9} 條 ({t1-t0:.3f}s)，最短路徑 {sigma[f'h{k}']:>6} 條 ({t2-t1:.5f}s)")

# 機率加權：邊上標「攻擊成功機率」p，最可能路徑 = 最小化 Σ -log p 的最短路
P = {("eve", "WS03"): 0.9, ("WS03", "carol"): 0.5, ("carol", "SRV01"): 0.9,
     ("SRV01", "DA"): 0.3, ("eve", "WS01"): 0.2, ("WS01", "bob"): 0.9, ("bob", "SRV01"): 0.9,
     ("carol", "SRV02"): 0.9, ("SRV02", "DA"): 0.8}
G = nx.DiGraph()
for (u, v), p in P.items():
    G.add_edge(u, v, w=-math.log(p))
path = nx.dijkstra_path(G, "eve", "DA", weight="w")
prob = math.prod(P[(a, b)] for a, b in zip(path, path[1:]))
print("最可能路徑:", " -> ".join(path), f"成功機率 {prob:.3f}")
hop = nx.shortest_path(G, "eve", "DA")
print("最少跳數路徑:", " -> ".join(hop), f"成功機率 {math.prod(P[(a, b)] for a, b in zip(hop, hop[1:])):.3f}")
```

實測（AMD Ryzen 7 5700G、Windows 11、Python 3.13.12，單次執行）：

```text
k= 4: 簡單路徑        81 條 (0.000s)，最短路徑     16 條 (0.00002s)
k= 8: 簡單路徑      6561 條 (0.003s)，最短路徑    256 條 (0.00002s)
k=12: 簡單路徑    531441 條 (0.259s)，最短路徑   4096 條 (0.00004s)
k=16: 簡單路徑  43046721 條 (32.391s)，最短路徑  65536 條 (0.00006s)
最可能路徑: eve -> WS03 -> carol -> SRV02 -> DA 成功機率 0.324
最少跳數路徑: eve -> WS03 -> carol -> SRV01 -> DA 成功機率 0.121
```

簡單路徑數是 `3^k`（`3^16 = 43046721`），列舉時間跟著指數成長；最短路徑數 `2^k` 也是指數大的**數字**，但 σ 的計算只做一次 BFS，時間是線性的——數字大不代表算得慢。這張圖只有 `5k` 條邊，k = 16 時才 80 條邊，DFS 就要半分鐘；真實 AD 動輒十萬條邊，「列出所有路徑」在實務上不可行，報告應該給最短路徑和割。

後兩行是另一種建模：把每條邊標上「攻擊成功機率」p（這些數字是為了示範編的），因為 `−log` 把乘積變成和，`Σ −log p` 最小的路徑就是成功機率最高的路徑，可以用 Dijkstra（權重非負，因為 `p ≤ 1`）。最可能的路徑和最少跳數的路徑不一樣——SRV01 上的 DA session 不常出現（p = 0.3），SRV02 上的常出現（p = 0.8）。這個模型假設各步驟獨立，現實中通常不成立（同一個攻擊者的技能讓各步驟正相關），所以只能拿來排序，不要把 0.324 當成真實機率。

## 範例二：最小割——修哪裡最划算

把問題翻譯成圖論：

- 攻擊者可能的起點集合 A（被釣魚的帳號），目標 t = `DomainAdmins`。
- 加一個超級源點 `S*`，連到 A 的每個點，容量 ∞。多源問題就變成單源。
- 每條「可以修」的邊容量 1（代表修補成本），「不能修」的邊容量 ∞。
- 由 max-flow min-cut 定理（[Ch 20](./20-max-flow-min-cut.md)），`S*` 到 t 的最大流值 = 最小割容量 = 切斷所有攻擊路徑所需的最少修補數；最小割可以從最後一次 BFS 的殘餘圖讀出：從 `S*` 可達的點是 S 側，S 側指向 T 側的原始邊就是割。

**為什麼不是去掉「最多路徑經過」的那條邊？** 因為貪婪不保證最優，而且「路徑數」本身難算（上一節）。割是全域最優的，而且多項式時間可解。

想刪的是**節點**（例如「把這台伺服器重灌並收緊權限」）時，用 node splitting：每個節點 v 拆成 `v_in → v_out`，容量是刪 v 的成本，原本的邊 `u → v` 變成 `u_out → v_in`、容量 ∞。

**命題（node splitting 的正確性）**。在拆點後的圖 G' 中，`S*` 到 `t_in` 的有限容量割與原圖中「切斷所有 A–t 路徑的節點集合」一一對應（容量 = 節點成本總和）。

**證明**。(1) 設 X 是原圖中一個切斷所有 A–t 路徑的節點集合（不含 A 與 t）。令 G' 的割為 `{(v_in, v_out) : v ∈ X}`。任一條 G' 中 `S*` 到 `t_in` 的路徑對應原圖一條 A–t 路徑（把 `v_in → v_out` 縮回 v），這條路徑經過某個 `x ∈ X`，所以 G' 路徑經過 `(x_in, x_out)`，被切斷。容量是 X 的成本總和。(2) 反過來，設 C 是 G' 中一個有限容量的割。有限容量意味著 C 只含 `(v_in, v_out)` 型的邊（其他邊都是 ∞）。令 `X = {v : (v_in, v_out) ∈ C}`。若原圖有一條 A–t 路徑避開 X，把它展開成 G' 的路徑，這條路徑上的拆點邊都不在 C、其他邊也不在 C，與 C 是割矛盾。所以 X 切斷所有路徑，且成本等於 C 的容量。兩個方向合起來，最小有限割的容量 = 最小點割的成本。∎

```python
# ch39_cut.py（與 ch39_paths.py、ad_lab.json 同目錄）
"""Ch 39 範例二：用 max-flow / min-cut 找「切斷所有攻擊路徑」的最少修補點。
接在 ch39_paths.py 後面（同目錄，import 它的 load）。"""
from collections import deque
from itertools import combinations
import networkx as nx
from ch39_paths import load

INF = float("inf")


def max_flow_min_cut(cap, s, t):
    """Edmonds–Karp（Ch 20）。cap: dict[u][v] = 容量。回傳 (流量值, 割的 S 側節點集合)。"""
    res = {u: dict(vs) for u, vs in cap.items()}
    for u in cap:
        for v in cap[u]:
            res.setdefault(v, {}).setdefault(u, 0)
    flow = 0
    while True:
        par = {s: None}
        q = deque([s])
        while q and t not in par:
            u = q.popleft()
            for v, c in res[u].items():
                if c > 0 and v not in par:
                    par[v] = u
                    q.append(v)
        if t not in par:
            return flow, set(par)            # 殘餘圖中 s 可達的點 = 最小割的 S 側
        b, v = INF, t
        while par[v] is not None:
            b = min(b, res[par[v]][v])
            v = par[v]
        if b == INF:
            return INF, set(par)
        v = t
        while par[v] is not None:
            res[par[v]][v] -= b
            res[v][par[v]] += b
            v = par[v]
        flow += b


kind, succ, label = load("ad_lab.json")
T = "DomainAdmins"
starts = [v for v in kind if kind[v] == "User" and v != "da_admin"]   # 假設任何一般帳號都可能被釣魚


def edge_cut(starts, cost):
    cap = {"S*": {u: INF for u in starts}}
    for u in succ:
        cap.setdefault(u, {})
        for v in succ[u]:
            cap[u][v] = cost(u, v)
    val, S = max_flow_min_cut(cap, "S*", T)
    cut = sorted((u, v) for u in S for v in cap.get(u, {}) if v not in S and cap[u][v] > 0)
    G = nx.DiGraph()
    for u in cap:
        for v, c in cap[u].items():
            G.add_edge(u, v, capacity=c if c != INF else 10**9)
    assert nx.minimum_cut(G, "S*", T)[0] == val     # 與 NetworkX 對拍
    return val, cut


def cost_a(u, v):          # 情境 A/B：除了「踢出 Domain Users」與 DA 成員關係，什麼都能改
    if label[(u, v)] == "MemberOf" and v in ("DomainUsers", "DomainAdmins"):
        return INF
    return 1


def cost_c(u, v):          # 情境 C：組織要求所有群組成員關係都不能動，只能改權限與登入習慣
    return INF if label[(u, v)] == "MemberOf" else 1


for name, st, cf in [("A 起點只有 eve", ["eve"], cost_a),
                     ("B 任何一般帳號都可能被釣魚", starts, cost_a),
                     ("C 同 B，但群組成員不能動", starts, cost_c)]:
    val, cut = edge_cut(st, cf)
    print(f"情境 {name}：最小邊割 = {val}")
    for u, v in cut:
        print(f"    移除 {u} -[{label[(u, v)]}]-> {v}")

# ---- 最小點割（node splitting）----
PROTECT = set(starts) | {T, "DomainUsers", "da_admin"}   # 這些點不能「刪」
cap2 = {"S*": {u + "_in": INF for u in starts}}
for v in succ:
    cap2[v + "_in"] = {v + "_out": INF if v in PROTECT else 1}
    cap2.setdefault(v + "_out", {})
    for w in succ[v]:
        cap2[v + "_out"][w + "_in"] = INF
val2, S2 = max_flow_min_cut(cap2, "S*", T + "_in")
nodes = sorted(v for v in succ if v + "_in" in S2 and v + "_out" not in S2)
print("最小點割大小:", val2, "→ 修補這些節點:", nodes)

# 驗證：刪掉割之後，沒有任何起點能到 DA
H = nx.DiGraph([(u, v) for u in succ for v in succ[u]])
H.remove_nodes_from(nodes)
print("刪點後仍可達 DA 的帳號:", [u for u in starts if u in H and nx.has_path(H, u, T)])

# 暴力驗證最小性：所有大小 < val2 的可刪點集合都切不斷
full = nx.DiGraph([(a, b) for a in succ for b in succ[a]])
removable = [v for v in succ if v not in PROTECT]
ok = True
for k in range(int(val2)):
    for R in combinations(removable, k):
        H2 = nx.restricted_view(full, R, [])
        if not any(nx.has_path(H2, u, T) for u in starts):
            ok = False
print(f"暴力檢查 {len(removable)} 個可刪點的所有大小 < {int(val2)} 的子集，全都切不斷：", ok)
```

```text
情境 A 起點只有 eve：最小邊割 = 1
    移除 DomainUsers -[AdminTo]-> WS03
情境 B 任何一般帳號都可能被釣魚：最小邊割 = 3
    移除 bob -[MemberOf]-> IT_Admins
    移除 carol -[MemberOf]-> ServerAdmins
    移除 svc_backup -[MemberOf]-> BackupOps
情境 C 同 B，但群組成員不能動：最小邊割 = 3
    移除 BackupOps -[AdminTo]-> DC01
    移除 SRV01 -[HasSession]-> da_admin
    移除 SRV02 -[HasSession]-> da_admin
最小點割大小: 3 → 修補這些節點: ['BackupOps', 'IT_Admins', 'ServerAdmins']
刪點後仍可達 DA 的帳號: []
暴力檢查 10 個可刪點的所有大小 < 3 的子集，全都切不斷： True
```

三個情境給出三種完全不同的修補建議，這正是割分析的價值：

- **情境 A**：如果只擔心 eve，修一條邊就夠——拿掉「所有人都是 WS03 本機管理員」這個錯誤設定。這和範例一觀察到的「共同前綴」一致。
- **情境 B**：一旦任何帳號都可能被拿下，WS03 那條邊就不夠了（例如 bob 被釣魚，走 `bob → IT_Admins → SRV01` 根本不經過 WS03）。割移到了「特權群組的成員關係」。實務上這等於說：日常帳號不該直接是伺服器管理群組成員，要另開管理帳號。
- **情境 C**：如果組織說群組成員不能動，割就移到「DA 不要登入成員伺服器」（兩條 HasSession）以及「BackupOps 不該是 DC 的本機管理員」。這和 Microsoft 過去推廣的 AD 分層管理模型（tier model）的核心建議一致——高權限帳號只登入同層級的機器——而我們是從 max-flow 自己推出來的。

注意情境 A 用 `DomainUsers -[AdminTo]-> WS03` 而不是 `eve -[MemberOf]-> DomainUsers`，因為後者容量是 ∞（`cost_a` 規定不能把人踢出 Domain Users）。**容量 ∞ 是把業務限制編碼進圖的方法**；忘了設，割會給出「把所有人移出 Domain Users」這種技術上正確、業務上荒謬的答案。

最後一行是對最小性的獨立驗證：可刪的 10 個節點中，所有大小 0、1、2 的子集（共 `1 + 10 + 45 = 56` 個）都切不斷，所以 3 確實是最小的。這個暴力法在 10 個點上可行，在一萬個點上不可行——它是用來驗證 max-flow 程式寫對，不是拿來用的。

## 對比與取捨

| 問題 | 方法 | 複雜度 | 回答什麼 | 限制 |
| --- | --- | --- | --- | --- |
| 某帳號能不能到 DA | BFS / DFS 可達性 | `O(V + E)` | 是否暴露 | 沒有優先順序 |
| 最省力的路徑 | BFS（無權）/ Dijkstra（權重） | `O(V + E)` / `O(E log V)` | 攻擊者最可能怎麼走 | 權重是估的 |
| 有幾條最省力的路徑 | BFS + σ 動態規劃 | `O(V + E)` | 單點故障在哪 | 只看最短的 |
| 總共有幾條路 | 列舉簡單路徑 | 指數；計數問題 #P-complete | 整體暴露面 | 大圖上不可行 |
| 最少修幾個地方 | max-flow / min-cut | 多項式（Edmonds–Karp `O(V E²)`） | 修補優先順序 | 假設修補互相獨立 |
| 一個措施刪很多邊 | 最小 hitting set | NP-hard | 「哪幾個 GPO 改動」 | 只能近似 |

## 踩雷集錦

1. **把邊方向畫反**：直覺上「carol 在 WS03 上有 session」寫成 `carol → WS03`，但攻擊的方向是「控制 WS03 的人拿到 carol 的憑證」，所以是 `WS03 → carol`。方向錯了，BFS 會回答「DA 能不能走到 eve」這種沒人關心的問題。每種邊型都要先寫下「控制 u ⇒ 取得 v」再決定方向。
2. **以為路徑數多 = 風險高**：簡單路徑數會被長繞道灌水，而且它是 #P-complete 的計數問題，大圖上算不出來。比較風險用最短距離和最短路徑數，修補用割。
3. **忘記把不可修的東西設成 ∞**：最小割會「誠實地」選最便宜的割，包括「刪掉 Domain Users 群組」或「把 DA 帳號刪掉」。所有業務限制都要變成容量，否則答案技術上正確、實務上不能用。
4. **把 session 當成永久事實**：`HasSession` 是蒐集當下的快照，下班後 session 可能消失，明天又出現。BloodHound 文件明確說 session 只代表憑證「可能」存在。割建議「移除 SRV01 → da_admin」的真正意思是改變管理習慣（DA 不登入成員伺服器），而不是登出一次。
5. **在有環的圖上做「路徑 DP」**：AD 圖有環（`DomainAdmins → DC01 → da_admin → DomainAdmins`）。σ 的動態規劃之所以成立，是因為它只在 BFS 層次 DAG 上進行；如果你把它改寫成「σ(v) = 所有前驅 σ 的和」而不限制 `dist(u) = dist(v) − 1`，在有環圖上會無窮遞迴或算錯。

## 進階：再往深一層

- **加權割與預算**：把每條邊的容量設成修補工時，最小割就是「最少工時」。如果預算固定為 k、割大於 k，問題變成「在 k 次修補內切斷最多路徑」——這不再是 max-flow 能直接解的問題，[練習 F](./practice-f-attack-graph.md) 會比較 min-cut 與貪婪法。
- **和 project selection 是同一種建模**：[練習 D](./practice-d-flow-modeling.md) 的 project selection 用「源點側 = 選、匯點側 = 不選、∞ 邊表示依賴」把組合選擇變成最小割；本章的容量 ∞（業務限制）和它是同一個技巧。如果修補措施之間有依賴（「要先導入 LAPS 才能收回本機管理員權限」），就可以照 project selection 的方式加 ∞ 邊。
- **Hitting set 版本**：一個措施（例如「停用 NTLM」、「一個 GPO 改動」）同時刪除多條邊時，就回到 Jha–Sheyner–Wing 的 NP-hard 問題。實務作法是貪婪：每次選「切斷最多剩餘最短路徑」的措施，它有 `O(log n)` 的近似比（set cover 的經典結果）。
- **多目標**：同時保護 `DomainAdmins`、`EnterpriseAdmins`、備份伺服器時，加一個超級匯點 `T*`，所有目標連到它、容量 ∞。
- **與偵測結合**：最小割給的是「修哪裡」，同一份圖也能回答「偵測要放哪裡」：在割的位置佈監控，攻擊者必經之地。這和 `blue_team_dfir` 的偵測工程是同一件事的兩面。

## 動手練習

1. 在 `ad_lab.json` 加一條 `["Helpdesk", "AdminTo", "SRV02"]`，先預測情境 A、B、C 的最小割會不會變，再實跑驗證。
2. 把 `count_simple_paths` 改成「回傳長度 ≤ L 的簡單路徑數」，畫出 L = 4…10 時 eve 到 DA 的路徑數。哪個 L 開始出現繞道？
3. 證明或否證：在情境 B 中，「max-flow 值 = 3」意味著存在 3 條從某些一般帳號到 DA、兩兩沒有共用可修邊的路徑。（提示：Menger 定理的邊版本，[Ch 12](./12-menger-theorem.md)。）用 `nx.maximum_flow` 回傳的 flow dict 做流分解（每次沿流量為正的邊從 `S*` 走到 DA，扣掉 1 單位）把它們找出來；注意 `nx.edge_disjoint_paths` 不適用，因為容量 ∞ 的邊允許被多條路徑共用。
4. 用 matplotlib 畫出 `ad_lab.json`：`nx.draw_networkx(G, pos=nx.multipartite_layout(...))`，以節點種類分層（User、Group、Computer），並用紅色標出情境 C 的割邊。你會看到割邊集中在「Computer → da_admin」那一層。

## 本章重點整理

- 攻擊圖的三種模型差在「一條邊代表什麼」：狀態轉移（指數大）、邏輯推論（多項式，靠單調性）、權限關係（BloodHound）。
- 最短攻擊路徑是 BFS；最短路徑數可以用 σ 動態規劃線性時間算出；簡單路徑計數是 #P-complete。
- 「修哪裡」是最小割：超級源點處理多起點、容量 ∞ 編碼業務限制、node splitting 處理刪點；結果可以用暴力法在小圖上驗證最小性。
- 當一個措施刪很多邊，問題變成 hitting set，是 NP-hard。

## 自我檢核

- [ ] 不看筆記，說明 `HasSession` 為什麼是 Computer → User，以及畫反會讓分析回答什麼錯誤的問題。
- [ ] 用自己的話證明 σ 動態規劃的正確性，並指出它在哪一步用到「只在 BFS 層次圖上累加」。
- [ ] 如果主管問「為什麼三個情境的修補建議都不一樣」，你能用起點集合與容量設定解釋嗎？
- [ ] 解釋為什麼「刪一條邊 = 一個修補」時問題是多項式的，而「一個 GPO 改動刪很多邊」時是 NP-hard。
- [ ] 想想你們公司的環境：哪些關係在割分析裡應該設成容量 ∞？

## 延伸閱讀

### 論文

- **[Automated Generation and Analysis of Attack Graphs](https://www.cs.cmu.edu/afs/cs.cmu.edu/project/calder/www/sp02.html)** — Oleg Sheyner、Joshua Haines、Somesh Jha、Richard Lippmann、Jeannette M. Wing，IEEE S&P（2002），pp. 254–265
  - **核心貢獻**：第一個用 symbolic model checking 自動產生攻擊圖的系統，並提出兩種分析找出最值得防禦的攻擊。
  - **讀哪裡**：攻擊模型與攻擊圖的定義章節，以及範例網路那節；model checking 的實作細節可以略過。
  - **和本章的關聯**：本章「狀態攻擊圖」那一列；看完你會理解為什麼狀態數會爆炸。
- **[Two Formal Analyses of Attack Graphs](https://pages.cs.wisc.edu/~jha/jha-papers/security/CSFW_2002_1.html)** — Somesh Jha、Oleg Sheyner、Jeannette M. Wing，IEEE CSFW（2002）
  - **核心貢獻**：證明「最少防禦措施」問題與最小 hitting set 多項式等價，給出有保證的貪婪演算法；另外做了機率可靠度分析。
  - **讀哪裡**：minimization analysis 那節。
  - **前提**：知道 set cover 與 NP-hardness（[Ch 34](./34-np-complete-reductions.md)）。
- **[MulVAL: A Logic-based Network Security Analyzer](https://www.usenix.org/legacy/events/sec05/tech/ou.html)** — Xinming Ou、Sudhakar Govindavajhala、Andrew W. Appel，USENIX Security（2005），pp. 113–128
  - **核心貢獻**：用 Datalog 規則描述漏洞與網路設定，推導出多項式大小的邏輯攻擊圖，可處理數千台主機。
  - **讀哪裡**：規則系統與 interaction rules 的章節。
  - **和本章的關聯**：本章權限圖的邊，在 MulVAL 裡是由規則推導出來的事實。
- **[Scalable, Graph-Based Network Vulnerability Analysis](https://www.engineering.iastate.edu/~daji/seminar/papers/AWK02.ACMCCS.pdf)** — Paul Ammann、Duminda Wijesekera、Saket Kaushik，ACM CCS（2002），pp. 217–224
  - **核心貢獻**：提出單調性假設，換取多項式大小的攻擊圖表示。
  - **讀哪裡**：monotonicity 的定義與它放棄了什麼的討論。

### 官方文件

- **[BloodHound CE 文件：Edges](https://bloodhound.specterops.io/resources/edges/has-session)**（SpecterOps）
  - **讀哪裡**：`HasSession`、`MemberOf`、`AdminTo`、`GenericAll`、`ForceChangePassword` 各頁的「Abuse Info」與「Opsec」段落。
  - **能學到什麼**：每種邊的精確語意與方向、session 只代表憑證「可能」存在的警告；本章的邊型都取自這裡。

### repo 內延伸

- **[security/blue_team_dfir](../../security/blue_team_dfir/README.md)**：本章的割告訴你「修哪裡、監控哪裡」，那門課教你怎麼監控。特別對照 [憑證與橫向移動的鑑識](../../security/blue_team_dfir/20-credential-lateral-movement-forensics.md)——HasSession 邊在真實事件中留下的痕跡就在那裡。

### 書籍

- **《Introduction to Graph Theory》** — Douglas B. West（Prentice Hall，第 2 版，2001）
  - **讀哪幾章**：4.2 節（Menger 定理的各種版本）與 4.3 節（network flow）。本章所有割的論證都建立在這兩節。

攻擊圖處理的是「權限之間」的關係；下一章換一個角度看圖：同一份 C 程式用不同最佳化等級編譯，兩張 CFG 長得不一樣，我們怎麼判斷它們其實是同一個函式？

→ [Ch 40 Binary 相似度的圖比對](./40-binary-similarity.md)
