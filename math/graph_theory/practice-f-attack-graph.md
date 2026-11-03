# 練習 F — 攻擊圖的 chokepoint 分析

> **目標**：把 [Ch 39](./39-attack-graphs.md) 的路徑計數與最小割、[Ch 20](./20-max-flow-min-cut.md) 的 max-flow、[Ch 12](./12-menger-theorem.md) 的 Menger 定理拼成一個可以交給藍隊用的工具：讀入合成的 AD 關係 JSON，列出到 Domain Admins 的所有最短攻擊路徑，並在「只能修 k 個地方」的預算下選出最划算的修補組合，用暴力窮舉證明你的選擇在小規模下是最佳的，或量出它離最佳差多少。
>
> **環境**：Python 3.13.12、NetworkX 3.7（只用來對答案）、pytest 9.2.0，Windows 11。安裝：`pip install networkx==3.7 pytest==9.2.0`。執行範例前設 `PYTHONUTF8=1`。所有 AD 資料都是合成的，網域與帳號名稱皆虛構。

## 背景與動機

Ch 39 的最小割回答了「最少要修幾個地方才能**完全**切斷」。真實的修補工作有兩個 Ch 39 沒處理的麻煩：

1. **預算不夠**。最小割是 4，但這一季只排得出 2 個變更窗口。這時要問的是「修哪 2 個能保護最多帳號」，而這不再是 max-flow 能直接回答的問題。
2. **直覺的貪婪會騙人**。最常見的做法是「找被最多攻擊路徑經過的那條邊先修」（BloodHound 的使用者常這樣排優先序）。這個練習會讓你親手證明：在一份 82 個節點的合成 AD 上，這種貪婪用完預算之後**一個帳號都沒保護到**，而另一種以「割」為單位的貪婪在同樣預算下保護了 32 個。

這是防守方每季都要做的決策，你不是在做假題目。

## 任務規格

### 輸入格式

一個 JSON 檔：

```json
{
  "target": "DomainAdmins",
  "tier0": ["da_alice", "da_bob", "DC01", "DC02"],
  "starts": "non_tier0_users",
  "nodes": [{"id": "eve", "kind": "User"}, {"id": "WS01", "kind": "Computer"}, {"id": "DomainAdmins", "kind": "Group"}],
  "edges": [["eve", "AdminTo", "WS01"]]
}
```

| 欄位 | 規則 |
| --- | --- |
| `nodes[].kind` | `User`、`Group`、`Computer` 三者之一；`id` 不可重複 |
| `edges[]` | `[src, rel, dst]`，`rel ∈ {MemberOf, AdminTo, HasSession, GenericAll, ForceChangePassword, AddMember}`；方向是「控制 src ⇒ 取得 dst」（Ch 39 的約定） |
| `target` | 攻擊目標節點，必須存在 |
| `tier0` | 最高權限的節點清單；`target` 自動算在內 |
| `starts` | 攻擊者可能的起點：字串 `"non_tier0_users"`（所有非 tier0 的 User），或明確的節點清單 |

非法輸入（未知節點種類、重複節點、邊引用不存在的節點、未知邊型、自環、target 不存在）一律丟 `ValueError`。

### 可修與不可修

一條邊**不可修**，若它是 `MemberOf` 且（終點是 `DomainUsers`，或兩端都在 tier0）。其他邊都可修，修一條的成本是 1。理由同 Ch 39：不能把人踢出 Domain Users，DA 帳號屬於 DA 群組是定義，不是錯誤設定。

### 要實作的函式

| 函式 | 回傳 | 說明 |
| --- | --- | --- |
| `shortest_attack_paths(g, removed=∅)` | `(d, σ, paths)` | 把所有起點視為一個超級源點：d = 到 target 的最短距離，σ = 最短路徑數，paths = **全部**最短路徑（每條從某個起點開始）；不可達回傳 `(None, 0, [])` |
| `evaluate(g, removed)` | `(alive, total)` | 刪掉 `removed` 後，仍能到達 target 的起點數，以及這些起點各自的最短路徑數總和。評分以 tuple 字典序比較，越小越好 |
| `min_edge_cut(g, starts=None)` | `(val, edges)` | 超級源點連到起點、可修邊容量 1、不可修邊容量 ∞ 的最小割；割不斷時 `val = inf` |
| `greedy_paths(g, k)` | 邊清單 | 貪婪 A：每步刪「被最多條最短攻擊路徑經過」的可修邊 |
| `budget_cut(g, k)` | `(method, edges)` | 你的正式答案：最小割 ≤ k 時回傳最小割；否則回傳你能找到最好的 ≤ k 條邊 |
| `brute_force(g, k)` | `(score, edges)` | 窮舉所有 ≤ k 條可修邊的組合，回傳 evaluate 最小者（驗證用） |

### 驗收標準

- 所有測試用例通過（下面的測試表與 pytest 檔）。
- `min_edge_cut` 的值與 `nx.minimum_cut_value` 在 30 張隨機小圖上一致，而且「少一條邊就切不斷」由 `brute_force` 驗證。
- 在 `ad_practice.json` 上，`budget_cut(g, 2)` 的評分不差於 `brute_force(g, 2)`。
- **禁止**：`budget_cut` 不能呼叫 `brute_force`（窮舉在真實規模上不可行，只能拿來驗證）。

### 效能要求

`ad_practice.json`（82 點、154 邊、41 個起點）上，`budget_cut(g, 3)` 在一般筆電上幾秒內完成；`brute_force(g, 3)` 允許跑到數十秒（參考解答實測約 20 秒）。

## 資料：產生 `ad_practice.json`

```python
# make_ad.py
"""練習 F：產生合成 AD 資料 ad_practice.json（seed 固定，所有名稱皆虛構）。"""
import json
import random

rng = random.Random(42)
nodes, edges = [], []


def node(i, k):
    nodes.append({"id": i, "kind": k})
    return i


def edge(u, r, v):
    if [u, r, v] not in edges:
        edges.append([u, r, v])


groups = {g: node(g, "Group") for g in
          ["DomainUsers", "Helpdesk", "IT_Admins", "ServerAdmins", "BackupOps", "SQLAdmins", "DomainAdmins"]}
staff = [node(f"user{i:02d}", "User") for i in range(30)]
helpdesk = [node(f"hd{i}", "User") for i in range(4)]
itadm = [node(f"it{i}", "User") for i in range(3)]
srvadm = [node(f"srvadm{i}", "User") for i in range(2)]
svc = [node("svc_backup", "User"), node("svc_sql", "User")]
das = [node("da_alice", "User"), node("da_bob", "User")]
ws = [node(f"WS{i:02d}", "Computer") for i in range(24)]
srv = [node(f"SRV{i:02d}", "Computer") for i in range(6)]
dcs = [node("DC01", "Computer"), node("DC02", "Computer")]

for u in staff + helpdesk + itadm + srvadm + svc + das:
    edge(u, "MemberOf", "DomainUsers")
for u in helpdesk:
    edge(u, "MemberOf", "Helpdesk")
for u in itadm:
    edge(u, "MemberOf", "IT_Admins")
for u in srvadm:
    edge(u, "MemberOf", "ServerAdmins")
edge("svc_backup", "MemberOf", "BackupOps")
edge("svc_sql", "MemberOf", "SQLAdmins")
for u in das:
    edge(u, "MemberOf", "DomainAdmins")
edge("IT_Admins", "MemberOf", "ServerAdmins")            # 巢狀群組

for w in ws:
    edge("Helpdesk", "AdminTo", w)
for w in rng.sample(ws, 3):
    edge("DomainUsers", "AdminTo", w)                   # 錯誤設定：所有人都是這幾台的本機管理員
for s in srv:
    edge("ServerAdmins", "AdminTo", s)
edge("SQLAdmins", "AdminTo", "SRV04")
edge("SQLAdmins", "AdminTo", "SRV05")
edge("BackupOps", "AdminTo", "DC01")
for d in dcs:
    edge("DomainAdmins", "AdminTo", d)
edge("Helpdesk", "ForceChangePassword", "srvadm1")        # 過寬的委派
edge("IT_Admins", "GenericAll", "SQLAdmins")

# session：一般使用者各自登入自己的工作站；管理員偶爾登入工作站或伺服器
for i, u in enumerate(staff):
    edge(ws[i % len(ws)], "HasSession", u)
for u in helpdesk + itadm:
    for w in rng.sample(ws, 2):
        edge(w, "HasSession", u)
for u in srvadm:
    edge(rng.choice(ws), "HasSession", u)
    edge(rng.choice(srv), "HasSession", u)
edge("SRV01", "HasSession", "svc_backup")
edge("SRV04", "HasSession", "svc_sql")
edge("SRV02", "HasSession", "da_alice")                  # DA 登入成員伺服器
edge("SRV05", "HasSession", "da_bob")
edge(rng.choice(ws), "HasSession", "da_bob")             # DA 甚至登入過工作站
for d in dcs:
    for u in das:
        edge(d, "HasSession", u)

data = {"domain": "CORP.EXAMPLE", "target": "DomainAdmins", "tier0": das + dcs,
        "starts": "non_tier0_users", "nodes": nodes, "edges": edges}
json.dump(data, open("ad_practice.json", "w", encoding="utf-8"), indent=1)
print(f"nodes={len(nodes)} edges={len(edges)}")
```

```text
nodes=82 edges=154
```

`random.Random(42)` 固定了哪三台工作站被錯誤設定、管理員登入了哪些機器；換 seed 會得到不同的圖，下面的期望輸出就不再適用。

另外準備一個 13 個節點的手工小圖 `trap.json`，它是專門設計來讓「路徑計數貪婪」失敗的：

```json
{
  "target": "DomainAdmins",
  "tier0": ["da_admin"],
  "starts": ["eve"],
  "nodes": [
    {"id": "eve", "kind": "User"}, {"id": "bob", "kind": "User"}, {"id": "carol", "kind": "User"},
    {"id": "da_admin", "kind": "User"},
    {"id": "DomainUsers", "kind": "Group"}, {"id": "Helpdesk", "kind": "Group"},
    {"id": "ServerOps", "kind": "Group"}, {"id": "BackupOps", "kind": "Group"},
    {"id": "DomainAdmins", "kind": "Group"},
    {"id": "WS1", "kind": "Computer"}, {"id": "WS2", "kind": "Computer"},
    {"id": "SRV1", "kind": "Computer"}, {"id": "SRV2", "kind": "Computer"}
  ],
  "edges": [
    ["eve", "MemberOf", "Helpdesk"],
    ["eve", "MemberOf", "DomainUsers"],
    ["Helpdesk", "AdminTo", "SRV1"],
    ["Helpdesk", "AdminTo", "SRV2"],
    ["eve", "AdminTo", "WS1"],
    ["WS1", "HasSession", "bob"],
    ["bob", "MemberOf", "ServerOps"],
    ["ServerOps", "AdminTo", "SRV1"],
    ["DomainUsers", "AdminTo", "WS2"],
    ["WS2", "HasSession", "carol"],
    ["carol", "MemberOf", "BackupOps"],
    ["BackupOps", "AdminTo", "SRV2"],
    ["SRV1", "HasSession", "da_admin"],
    ["SRV2", "HasSession", "da_admin"],
    ["da_admin", "MemberOf", "DomainAdmins"]
  ]
}
```

```text
eve ─→ Helpdesk ─┬→ SRV1 ─┐            最短路徑（長度 4）都經過 eve→Helpdesk
                 └→ SRV2 ─┼→ da_admin → DomainAdmins
eve → WS1 → bob → ServerOps → SRV1     繞道一（長度 6）
eve → DomainUsers → WS2 → carol → BackupOps → SRV2   繞道二（長度 7）
```

## 期望輸出範例

```text
輸入：python chokepoint.py trap.json --k 2 --brute
輸出：
nodes=13 edges=15 starts=1 target=DomainAdmins
最短攻擊距離=4 最短路徑數=2
  eve -> Helpdesk -> SRV1 -> da_admin -> DomainAdmins
  eve -> Helpdesk -> SRV2 -> da_admin -> DomainAdmins
切斷前 (可達起點數, 最短路徑總數) = (1, 2)
最小邊割 = 2: [('SRV1', 'da_admin'), ('SRV2', 'da_admin')]
預算 k=2 採用 min-cut: [('SRV1', 'da_admin'), ('SRV2', 'da_admin')] → (0, 0)
貪婪A 超級源點 k=2: [('SRV1', 'da_admin'), ('eve', 'Helpdesk')] → (1, 1)
貪婪A 逐起點 k=2: [('SRV1', 'da_admin'), ('eve', 'Helpdesk')] → (1, 1)
貪婪B 一步前瞻 k=2: [('BackupOps', 'SRV2'), ('Helpdesk', 'SRV1')] → (1, 1)
貪婪C 以割為單位 k=2: [('SRV1', 'da_admin'), ('SRV2', 'da_admin')] → (0, 0)
窮舉最佳 k=2: [('SRV1', 'da_admin'), ('SRV2', 'da_admin')] → (0, 0)（0.0s）
```

```text
輸入：python chokepoint.py ad_practice.json --k 2 --brute
輸出：
nodes=82 edges=154 starts=41 target=DomainAdmins
最短攻擊距離=4 最短路徑數=11
  svc_backup -> BackupOps -> DC01 -> da_alice -> DomainAdmins
  srvadm0 -> ServerAdmins -> SRV02 -> da_alice -> DomainAdmins
  srvadm1 -> ServerAdmins -> SRV02 -> da_alice -> DomainAdmins
  svc_backup -> BackupOps -> DC01 -> da_bob -> DomainAdmins
  svc_sql -> SQLAdmins -> SRV05 -> da_bob -> DomainAdmins
  srvadm0 -> ServerAdmins -> SRV05 -> da_bob -> DomainAdmins
  srvadm1 -> ServerAdmins -> SRV05 -> da_bob -> DomainAdmins
  hd0 -> Helpdesk -> WS19 -> da_bob -> DomainAdmins
  hd1 -> Helpdesk -> WS19 -> da_bob -> DomainAdmins
  hd2 -> Helpdesk -> WS19 -> da_bob -> DomainAdmins
  hd3 -> Helpdesk -> WS19 -> da_bob -> DomainAdmins
切斷前 (可達起點數, 最短路徑總數) = (41, 50)
最小邊割 = 4: [('Helpdesk', 'WS19'), ('SRV05', 'da_bob'), ('ServerAdmins', 'SRV02'), ('svc_backup', 'BackupOps')]
預算 k=2 採用 cut-greedy: [('hd2', 'Helpdesk'), ('it2', 'IT_Admins')] → (9, 16)
貪婪A 超級源點 k=2: [('Helpdesk', 'WS19'), ('SRV05', 'da_bob')] → (41, 42)
貪婪A 逐起點 k=2: [('Helpdesk', 'WS19'), ('it2', 'IT_Admins')] → (41, 83)
貪婪B 一步前瞻 k=2: [('BackupOps', 'DC01'), ('SRV05', 'da_bob')] → (41, 42)
貪婪C 以割為單位 k=2: [('hd2', 'Helpdesk'), ('it2', 'IT_Admins')] → (9, 16)
窮舉最佳 k=2: [('hd2', 'Helpdesk'), ('it2', 'IT_Admins')] → (9, 16)（0.4s）
```

```text
邊界輸入：target 不可達（只有一條 eve -AdminTo-> WS1）
輸出：shortest_attack_paths → (None, 0, [])；min_edge_cut → (0, [])；budget_cut(g, 3) → ("min-cut", [])

邊界輸入：起點本身就是 DA 成員（只有不可修的 tier0 MemberOf 邊）
輸出：min_edge_cut → (inf, [])；budget_cut 不得回傳 "min-cut"，評分維持 (1, 1)

非法輸入：邊型 "Pwns"、邊引用不存在的節點、自環
輸出：ValueError
```

讀 `ad_practice.json` 的結果時注意三件事：

- **全域最短路徑只來自 11 個管理類帳號**。30 個一般員工的路徑比較長（要先經過被錯誤設定的工作站），所以不在「最短攻擊路徑」清單裡——但他們也能到 DA。只看全域最短路徑的工具會系統性地忽略他們。
- **三種路徑/評分導向的貪婪在 k = 2 時一個帳號都沒保護到**（`alive` 仍是 41），以割為單位的貪婪 C 保護了 32 個，而且和窮舉最佳一致。
- **刪邊可能讓最短路徑總數變多**：貪婪 A（逐起點）刪了兩條邊之後，總數從 50 升到 83。最短路徑被切掉後，攻擊者改走較長的路，而較長的路可能有更多條。「路徑數」不是一個會單調下降的指標。

## 如果你卡住了

1. **「所有最短路徑」怎麼列**：不要列舉所有簡單路徑（Ch 39 說過那是指數級）。先做多源 BFS 得到 dist，最短路徑只會走 `dist(v) = dist(u) + 1` 的邊；從 target 沿這些邊**往回**走到 dist = 0 的點。
2. **一條邊被幾條最短路徑經過**：對單一起點 s，邊 `(u, v)` 在某條 s–t 最短路徑上 ⟺ `d_s(u) + 1 + d_t(v) = d_s(t)`，其中 `d_t` 是在反向圖上從 t 做 BFS 的距離。條數是 `σ_s(u) · σ_t(v)`。先想想為什麼是乘法。
3. **預算不夠時最小割幫不上忙嗎**：最小割對「全部起點」太大，但對「一部分起點」可能很小。如果你能保護一群共用同一個瓶頸的帳號，那群帳號的最小割可能只有 2。換個單位思考：貪婪地挑「要保護誰」，而不是「要刪哪條邊」。
4. **評分怎麼比較**：Python 的 tuple 比較就是字典序，`(9, 16) < (41, 42)`，先比可達起點數，再比路徑總數。
5. **min cut 回傳 inf**：只要有一條從起點到 target 的路徑全由不可修邊組成，就割不斷。`budget_cut` 這時不能回傳「min-cut」，要退回到別的方法並誠實地回報評分沒變。

## 實作步驟建議

### Step 1：載入與驗證

寫 `AD` 類別：檢查節點種類、重複、邊型、未知端點、自環；建 `succ`（鄰接字典）與 `label[(u, v)]`；解析 `starts`；寫 `removable(u, v)`。子目標：非法輸入測試全部通過。

### Step 2：多源 BFS 與 σ

寫 `bfs_sigma(succ, sources)`：多源 BFS，同時算 dist 與 σ（Ch 39 的命題，把多個源點的 σ 都初始化為 1、dist 為 0）。在反向圖上從 target 做同一件事，就得到「每個點到 target 的距離與最短路徑數」。子目標：`shortest_attack_paths` 在鏈狀圖與菱形圖上正確，`evaluate` 能算出每個起點是否可達。

### Step 3：邊的路徑經過數

寫 `edge_path_counts`，用卡點 2 的公式。用恆等式自我檢查：所有邊都可修時，`Σ_e count(e) = σ · d`（每條最短路徑恰好貢獻 d 條邊）。子目標：`test_path_count_identity` 通過。

### Step 4：最小割

沿用 Ch 39 的 Edmonds–Karp，加上 `starts` 參數讓你可以對任意起點子集算割。子目標：30 張隨機圖上與 NetworkX 一致、少一條邊就切不斷。

### Step 5：預算版本與比較

先寫貪婪 A，確認它在 `trap.json` 上失敗；再設計你自己的 `budget_cut`，在 `ad_practice.json` 上和 `brute_force` 比較 k = 1、2、3。子目標：k = 2 時與窮舉最佳一致，並且能說明 k = 3 時你的方法離最佳差多少。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<!-- markdownlint-disable MD033 -->

<details>
<summary>點開參考實作</summary>

```python
# chokepoint.py
"""練習 F 參考解答：AD 攻擊圖的最短路徑與預算限制下的 chokepoint 選擇。"""
import argparse
import json
import time
from collections import deque
from itertools import combinations

INF = float("inf")
EDGE_TYPES = {"MemberOf", "AdminTo", "HasSession", "GenericAll", "ForceChangePassword", "AddMember"}


class AD:
    def __init__(self, data):
        self.kind = {}
        for nd in data["nodes"]:
            if nd["kind"] not in ("User", "Group", "Computer"):
                raise ValueError(f"unknown node kind: {nd['kind']}")
            if nd["id"] in self.kind:
                raise ValueError(f"duplicate node: {nd['id']}")
            self.kind[nd["id"]] = nd["kind"]
        self.target = data["target"]
        self.tier0 = set(data.get("tier0", [])) | {self.target}
        self.succ = {v: set() for v in self.kind}
        self.label = {}
        for e in data["edges"]:
            u, rel, v = e
            if u not in self.kind or v not in self.kind:
                raise ValueError(f"edge references unknown node: {e}")
            if rel not in EDGE_TYPES:
                raise ValueError(f"unknown edge type: {rel}")
            if u == v:
                raise ValueError(f"self loop: {e}")
            self.succ[u].add(v)
            self.label[(u, v)] = rel
        if self.target not in self.kind:
            raise ValueError("target not in nodes")
        starts = data.get("starts", "non_tier0_users")
        if starts == "non_tier0_users":
            self.starts = sorted(v for v, k in self.kind.items() if k == "User" and v not in self.tier0)
        else:
            self.starts = sorted(starts)

    def removable(self, u, v):
        """不能修的邊：踢出 DomainUsers、tier0 成員關係（那是「定義上的 DA」，不是錯誤設定）。"""
        return not (self.label[(u, v)] == "MemberOf" and (v == "DomainUsers" or (u in self.tier0 and v in self.tier0)))


def _succ_without(g, removed):
    return {u: {v for v in ws if (u, v) not in removed} for u, ws in g.succ.items()}


def bfs_sigma(succ, sources):
    """多源 BFS：dist 與最短路徑數 σ（所有源點視為從同一個超級源點出發、距離 0）。"""
    dist = {s: 0 for s in sources}
    sigma = {s: 1 for s in sources}
    q = deque(sources)
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


def reverse(succ):
    pred = {v: set() for v in succ}
    for u, ws in succ.items():
        for w in ws:
            pred[w].add(u)
    return pred


def shortest_attack_paths(g, removed=frozenset(), limit=None):
    """回傳 (距離, 最短路徑總數, 路徑清單)。距離是任一起點到 target 的最短距離；不可達回傳 (None, 0, [])。"""
    succ = _succ_without(g, removed)
    dist, sigma = bfs_sigma(succ, g.starts)
    t = g.target
    if t not in dist:
        return None, 0, []
    pred = reverse(succ)
    out = []

    def back(v, acc):
        if limit is not None and len(out) >= limit:
            return
        if dist[v] == 0:
            out.append([v] + acc[::-1])
            return
        for u in sorted(pred[v]):
            if u in dist and dist[u] == dist[v] - 1:
                back(u, acc + [v])
    back(t, [])
    return dist[t], sigma[t], out


def evaluate(g, removed):
    """(仍可到達 target 的起點數, 各起點最短路徑數總和)。"""
    succ = _succ_without(g, set(removed))
    pred = reverse(succ)
    dist_t, sigma_t = bfs_sigma(pred, [g.target])        # 反向 BFS：誰能到 target、各有幾條最短路徑
    alive = [s for s in g.starts if s in dist_t]
    return len(alive), sum(sigma_t[s] for s in alive)


def edge_path_counts(g, removed, per_start=True):
    """每條可修邊被幾條最短攻擊路徑經過。
    對起點 s：邊 (u, v) 在某條 s→t 最短路徑上 ⟺ d_s(u) + 1 + d_t(v) = d_s(t)，條數 = σ_s(u)·σ_t(v)。
    per_start=True：對每個起點各自計算再加總（和 evaluate 的「最短路徑總數」一致）。
    per_start=False：把所有起點併成一個超級源點，只看「全域最近」的那些路徑（練習裡的反面教材）。"""
    succ = _succ_without(g, removed)
    dt, st = bfs_sigma(reverse(succ), [g.target])
    groups = [[s] for s in g.starts] if per_start else [g.starts]
    cnt = {}
    for srcs in groups:
        ds, ss = bfs_sigma(succ, srcs)
        if g.target not in ds:
            continue
        d = ds[g.target]
        for u in ds:
            for v in succ[u]:
                if v in dt and ds[u] + 1 + dt[v] == d and g.removable(u, v):
                    cnt[(u, v)] = cnt.get((u, v), 0) + ss[u] * st[v]
    return cnt


def max_flow_min_cut(cap, s, t):
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
            return flow, set(par)
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


def min_edge_cut(g, starts=None):
    """超級源點 → 起點（∞），可修邊容量 1，不可修邊 ∞。回傳 (割大小或 INF, 邊清單)。"""
    starts = g.starts if starts is None else starts
    cap = {"__S__": {s: INF for s in starts}}
    for u in g.succ:
        cap.setdefault(u, {})
        for v in g.succ[u]:
            cap[u][v] = 1 if g.removable(u, v) else INF
    if not starts:
        return 0, []
    val, S = max_flow_min_cut(cap, "__S__", g.target)
    if val == INF:
        return INF, []
    cut = sorted((u, v) for u in S if u != "__S__" for v in g.succ[u] if v not in S)
    return val, cut


def greedy_paths(g, k, per_start=True):
    """貪婪 A：每一步刪掉「被最多條最短攻擊路徑經過」的可修邊（同分取字典序最小）。"""
    removed = set()
    for _ in range(k):
        cnt = edge_path_counts(g, removed, per_start)
        if not cnt:
            break
        removed.add(min(cnt, key=lambda e: (-cnt[e], e)))
    return sorted(removed)


def greedy_eval(g, k):
    """貪婪 B：每一步試刪每條可修邊，挑讓 evaluate 降最多的那條（一步前瞻）。"""
    removed = []
    cand = sorted(e for e in g.label if g.removable(*e))
    for _ in range(k):
        rest = [e for e in cand if e not in removed]
        if not rest:
            break
        best = min((evaluate(g, removed + [e]), e) for e in rest)
        removed.append(best[1])
        if best[0][0] == 0:
            break
    return sorted(removed)


def cut_greedy(g, k):
    """貪婪 C（以割為單位）：逐步擴大「要保護的起點集合」P，每次加入讓 min-cut(P) 最小的起點，
    直到割超過預算；回傳最後一個 ≤ k 的割。每一步的割都是 P 的最佳切法，所以不會出現「刪了邊卻沒切斷任何人」。"""
    P, best_cut = [], []
    alive = [s for s in g.starts]
    while True:
        cands = []
        for s in alive:
            if s in P:
                continue
            val, cut = min_edge_cut(g, P + [s])
            if val <= k:
                cands.append((val, s, cut))
        if not cands:
            return sorted(best_cut)
        val, s, cut = min(cands)
        P.append(s)
        best_cut = cut
        # 這個割順便切斷的其他起點也算進 P
        succ = _succ_without(g, set(cut))
        reach_t, _ = bfs_sigma(reverse(succ), [g.target])
        P = sorted(set(P) | {x for x in g.starts if x not in reach_t})


def brute_force(g, k):
    """窮舉所有 ≤ k 條可修邊的組合，回傳最佳 (評分, 邊集合)。只能在小圖上用。"""
    cand = sorted(e for e in g.label if g.removable(*e))
    best = (evaluate(g, []), [])
    for r in range(1, k + 1):
        for R in combinations(cand, r):
            sc = evaluate(g, R)
            if sc < best[0]:
                best = (sc, list(R))
    return best


def budget_cut(g, k):
    """預算 k：若最小割 ≤ k 就用最小割（保證全部切斷）；否則用以割為單位的貪婪 C。"""
    val, cut = min_edge_cut(g)
    if val <= k:
        return "min-cut", cut
    C, B = cut_greedy(g, k), greedy_eval(g, k)
    return ("cut-greedy", C) if evaluate(g, C) <= evaluate(g, B) else ("eval-greedy", B)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--brute", action="store_true")
    a = ap.parse_args()
    g = AD(json.load(open(a.path, encoding="utf-8")))
    nedges = sum(map(len, g.succ.values()))
    print(f"nodes={len(g.kind)} edges={nedges} starts={len(g.starts)} target={g.target}")
    d, sig, paths = shortest_attack_paths(g)
    print(f"最短攻擊距離={d} 最短路徑數={sig}")
    for p in paths:
        print("  " + " -> ".join(p))
    print("切斷前 (可達起點數, 最短路徑總數) =", evaluate(g, []))
    val, cut = min_edge_cut(g)
    print(f"最小邊割 = {val}: {cut}")
    method, R = budget_cut(g, a.k)
    print(f"預算 k={a.k} 採用 {method}: {R} → {evaluate(g, R)}")
    for name, R in [("貪婪A 超級源點", greedy_paths(g, a.k, per_start=False)),
                    ("貪婪A 逐起點", greedy_paths(g, a.k)),
                    ("貪婪B 一步前瞻", greedy_eval(g, a.k)),
                    ("貪婪C 以割為單位", cut_greedy(g, a.k))]:
        print(f"{name} k={a.k}: {R} → {evaluate(g, R)}")
    if a.brute:
        t0 = time.perf_counter()
        sc, B = brute_force(g, a.k)
        print(f"窮舉最佳 k={a.k}: {B} → {sc}（{time.perf_counter() - t0:.1f}s）")


if __name__ == "__main__":
    main()
```

**解答說明**：

1. **路徑經過數的公式為什麼是乘法**。固定起點 s 與邊 `(u, v)`，且 `d_s(u) + 1 + d_t(v) = d_s(t)`。任取一條 s–u 最短路徑 P 與一條 v–t 最短路徑 Q，`P + (u, v) + Q` 的長度是 `d_s(u) + 1 + d_t(v) = d_s(t)`，所以是 s–t 最短路徑。反過來，任何經過 `(u, v)` 的 s–t 最短路徑，切開後前段是 s–u 的路徑、長度至少 `d_s(u)`，後段是 v–t 的路徑、長度至少 `d_t(v)`，兩段加 1 等於 `d_s(t)`，所以兩段都必須是最短的。這是「（前段, 後段）配對」與「經過 (u, v) 的最短路徑」之間的雙射，條數是 `σ_s(u) · σ_t(v)`。若等式不成立，沒有任何最短路徑經過這條邊。∎ 恆等式 `Σ_e count(e) = σ · d` 是雙重計數（[Ch 3](./03-counting-basics.md)）：把（最短路徑, 路徑上的邊）配對分別依路徑和依邊來數。
2. **為什麼貪婪 A 會失敗**。`trap.json` 上，`eve → Helpdesk` 被兩條最短路徑經過，貪婪 A 第一步就刪它——但刪了之後攻擊者改走長度 6 的繞道，而那條繞道和長度 7 的另一條繞道各自需要一刀。最小割 `{SRV1 → da_admin, SRV2 → da_admin}` 在「所有路徑匯集處」下刀，兩刀切斷全部。路徑計數只看**目前的**最短路徑，看不到刪邊之後才浮現的繞道；割看的是**所有**路徑（由 max-flow min-cut 定理保證）。
3. **為什麼貪婪 C 在 ad_practice 上好**。30 個一般員工都經過被錯誤設定的工作站，那些工作站上有 hd2 與 it2 的 session。對「員工 + hd2 + it2」這群起點，最小割只有 2（`hd2 → Helpdesk`、`it2 → IT_Admins`）。貪婪 C 每一步都在問「再多保護一個人，割要多大」，所以它會先吃下這整群共用瓶頸的帳號。它不是最佳演算法：k = 3 時它的評分是 `(8, 15)`，窮舉最佳是 `(7, 10)`，差一個帳號（見下面的測試表）。
4. **預算版本為什麼沒有多項式最佳解**。「用 k 條邊切斷最多起點」包含了 partial vertex/set cover 類型的問題作為特例，一般預期是 NP-hard；本練習不證明這點，只用窮舉在小規模上量出啟發式的差距。這和 Ch 39 提到的 hitting set 結論一致：只要目標不再是「全部切斷」，max-flow 的保證就沒了。
5. **複雜度**。`min_edge_cut` 是 Edmonds–Karp，單位容量時增廣次數不超過割值；`cut_greedy` 每一步對每個候選起點算一次割，最多 `|starts|` 步，所以是 `O(|starts|²)` 次 max-flow——在 41 個起點上可以接受，在一萬個起點上要先把「共用同一組前驅的起點」合併成等價類。

</details>

## 測試用例

| 輸入 | 預期輸出 | 說明 |
| --- | --- | --- |
| 鏈 `eve → WS1 → adm → DomainAdmins` | `(d, σ) = (3, 1)`，最小割 1，割後 `(0, 0)` | 最小可工作例子 |
| 菱形（WS1、WS2 兩條平行路線） | `(3, 2)`、兩條路徑、最小割 2 | 多條最短路徑 |
| target 不可達 | `(None, 0, [])`、割 0、`("min-cut", [])` | 邊界：不需修補 |
| 起點是 tier0 成員 | 割 = `inf`，`budget_cut` 不回傳 min-cut，評分 `(1, 1)` | 邊界：割不斷 |
| 邊型 `Pwns`、未知節點、自環 | `ValueError` | 非法輸入 |
| 菱形 + 第三條路線，全部可修 | `Σ count = σ · d` | 公式自我檢查 |
| `trap.json`，k = 2 | 貪婪 A `(1, 1)`；`budget_cut` `(0, 0)` | 貪婪陷阱 |
| 30 張隨機小圖 | 割值 = `nx.minimum_cut_value`；`brute_force(val − 1)` 仍有人可達 | 正確性與最小性 |
| `ad_practice.json`，k = 1 | `budget_cut` 與窮舉都是 `(41, 43)` | 預算太小，沒有人能被完整保護 |
| `ad_practice.json`，k = 2 | `budget_cut` `(9, 16)` = 窮舉 `(9, 16)` | 貪婪 A/B 都是 `alive = 41` |
| `ad_practice.json`，k = 3 | `budget_cut` `(8, 15)`；窮舉 `(7, 10)`（約 20 秒） | 啟發式不是最佳，差一個帳號 |
| `ad_practice.json`，k = 4 | `budget_cut` 採用 min-cut，`(0, 0)` | 預算達到最小割 |

<details>
<summary>點開 pytest 測試檔</summary>

```python
# test_chokepoint.py
"""練習 F 測試：pytest test_chokepoint.py（與 chokepoint.py、trap.json 同目錄）"""
import json
import random
import pytest
import networkx as nx
from chokepoint import (AD, shortest_attack_paths, evaluate, edge_path_counts, min_edge_cut,
                        greedy_paths, brute_force, budget_cut, INF)


def mk(edges, starts, tier0=(), extra_nodes=()):
    names = {x for e in edges for x in (e[0], e[2])} | set(extra_nodes) | set(starts) | {"DomainAdmins"}
    kind = lambda x: "Group" if x[0].isupper() and not x.startswith(("WS", "SRV")) else (
        "Computer" if x.startswith(("WS", "SRV")) else "User")
    return AD({"target": "DomainAdmins", "tier0": list(tier0), "starts": list(starts),
               "nodes": [{"id": x, "kind": kind(x)} for x in sorted(names)],
               "edges": [list(e) for e in edges]})


CHAIN = [("eve", "AdminTo", "WS1"), ("WS1", "HasSession", "adm"), ("adm", "MemberOf", "DomainAdmins")]
DIAMOND = [("eve", "AdminTo", "WS1"), ("eve", "AdminTo", "WS2"), ("WS1", "HasSession", "adm"),
           ("WS2", "HasSession", "adm"), ("adm", "MemberOf", "DomainAdmins")]


def test_chain():
    g = mk(CHAIN, ["eve"], tier0=["adm"])
    assert shortest_attack_paths(g)[:2] == (3, 1)
    val, cut = min_edge_cut(g)
    assert val == 1 and len(cut) == 1
    assert evaluate(g, cut) == (0, 0)


def test_diamond_two_paths_and_cut_two():
    g = mk(DIAMOND, ["eve"], tier0=["adm"])
    d, sig, paths = shortest_attack_paths(g)
    assert (d, sig, len(paths)) == (3, 2, 2)
    assert min_edge_cut(g)[0] == 2


def test_unreachable_target():
    g = mk([("eve", "AdminTo", "WS1")], ["eve"], extra_nodes=["DomainAdmins"])
    assert shortest_attack_paths(g) == (None, 0, [])
    assert min_edge_cut(g)[0] == 0
    assert budget_cut(g, 3) == ("min-cut", [])


def test_only_fixed_edges_cannot_be_cut():
    # 起點本身就是 DA 成員（tier0 之間的 MemberOf 不可修）→ 割是無限大，budget_cut 退回貪婪
    g = mk([("adm", "MemberOf", "DomainAdmins")], ["adm"], tier0=["adm"])
    assert min_edge_cut(g)[0] == INF
    method, R = budget_cut(g, 2)
    assert method != "min-cut" and evaluate(g, R) == (1, 1)


def test_invalid_inputs():
    with pytest.raises(ValueError):
        mk([("eve", "Pwns", "WS1")], ["eve"])
    with pytest.raises(ValueError):
        AD({"target": "DomainAdmins", "nodes": [{"id": "DomainAdmins", "kind": "Group"}],
            "edges": [["ghost", "MemberOf", "DomainAdmins"]]})
    with pytest.raises(ValueError):
        mk([("eve", "AdminTo", "eve")], ["eve"])


def test_path_count_identity():
    # 每條最短路徑恰有 d 條邊 ⇒ 所有邊的經過次數總和 = σ · d（全部邊都可修時）
    g = mk(DIAMOND + [("eve", "AdminTo", "WS3"), ("WS3", "HasSession", "adm")], ["eve"])
    d, sig, _ = shortest_attack_paths(g)
    assert sum(edge_path_counts(g, set()).values()) == sig * d


def test_greedy_trap():
    g = AD(json.load(open("trap.json", encoding="utf-8")))
    assert min_edge_cut(g)[0] == 2
    assert evaluate(g, greedy_paths(g, 2)) == (1, 1)        # 貪婪 A 用完預算仍沒切斷
    assert evaluate(g, budget_cut(g, 2)[1]) == (0, 0)       # 最小割切斷


@pytest.mark.parametrize("seed", range(30))
def test_min_cut_matches_bruteforce_and_networkx(seed):
    rng = random.Random(seed)
    users = ["u0", "u1", "u2"]
    comps = ["WS0", "WS1", "WS2", "SRV0"]
    E = {("adm", "MemberOf", "DomainAdmins")}
    for c in comps:
        for u in users:
            if rng.random() < 0.4:
                E.add((u, "AdminTo", c))
        if rng.random() < 0.6:
            E.add((c, "HasSession", "adm"))
        for u in users:
            if rng.random() < 0.2:
                E.add((c, "HasSession", u))
    g = mk(sorted(E), users, tier0=["adm"])
    val, cut = min_edge_cut(g)
    G = nx.DiGraph()
    for s in g.starts:
        G.add_edge("S*", s, capacity=10**9)
    for (u, v) in g.label:
        G.add_edge(u, v, capacity=1 if g.removable(u, v) else 10**9)
    assert nx.minimum_cut_value(G, "S*", "DomainAdmins") == val
    if val == 0:
        return
    assert evaluate(g, cut) == (0, 0)
    best_less, _ = brute_force(g, val - 1)                  # 少一條邊一定切不斷（最小性）
    assert best_less[0] > 0
```

```text
$ python -m pytest -q test_chokepoint.py
.....................................                                    [100%]
37 passed in 0.22s
```

</details>

## 延伸挑戰（加分）

- **點割版本**：用 Ch 39 的 node splitting 改寫成「預算 k 台機器可以重灌並收緊權限」。哪些節點同時出現在邊割與點割的答案裡？
- **加權**：讓每條邊帶修補成本（改群組成員 1、收回本機管理員 2、改 DA 登入習慣 5），把 `budget_cut` 改成成本預算。貪婪 C 要怎麼改？
- **修補之間有依賴**：「要先導入本機管理員密碼管理，才能收回 Helpdesk 的 AdminTo」。這正是 [練習 D](./practice-d-flow-modeling.md) 的 project selection 模型：把「修補 A 依賴 B」寫成 ∞ 邊，在一張流網路上同時處理「保護價值」與「修補成本」。試著把本練習的「保護一群起點」改寫成 project selection（起點的保護價值是收益，修補是成本），用一次 min-cut 求出最佳的「保護誰、修哪些」。
- **把 `brute_force` 換成精確整數規劃**：用 z3 的 `Optimize`（[Ch 35](./35-reductions-to-sat.md)）把「選 ≤ k 條邊、最大化被切斷的起點數」寫成約束，k = 3 時應該重現窮舉的 `(7, 10)` 中的 7，看它比 20 秒的窮舉快多少。
- **規模**：把 `make_ad.py` 放大到 3000 個使用者。`cut_greedy` 的 `O(|starts|²)` 次 max-flow 會變成瓶頸；把「後繼集合完全相同」的起點合併成一類再算，量測加速。

## 自我檢核

- [ ] 不看筆記，證明「經過邊 (u, v) 的 s–t 最短路徑數 = σ_s(u)·σ_t(v)」，並說出等式條件 `d_s(u) + 1 + d_t(v) = d_s(t)` 缺了會怎樣。
- [ ] 用 `trap.json` 向同事解釋：為什麼「先修被最多攻擊路徑經過的地方」這個直覺會失敗。
- [ ] 說明為什麼預算版本沒有 max-flow 式的最佳性保證，而「預算 ≥ 最小割」時有。
- [ ] 為什麼刪邊之後最短路徑總數可能**增加**？這對你設計報告指標有什麼影響？
- [ ] 能說出自己的 `budget_cut` 和參考解答的差異，以及在 k = 3 時各自離窮舉最佳差多少。

練習 F 把 Part 7 的攻擊圖收尾了。最後的 Final Project 回到 binary：把全課的圖演算法串成一條從 ELF 到 chokepoint 的分析工具鏈。

→ [Final Project：graphlab 分析工具鏈](./final-project-graphlab.md)
