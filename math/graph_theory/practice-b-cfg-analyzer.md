# 練習 B — CFG 分析器

> **目標**：把 Ch 11–14 學到的東西拼成一個真的能用的命令列工具：讀入文字格式的控制流程圖（CFG），輸出立即支配者、dominator tree、支配邊界、回邊、自然迴圈與迴圈巢狀，並正確處理三種教科書範例常常忽略的情況——不可達的基本塊、迴圈回到入口、不可約（irreducible）的多入口迴圈。完成後你驗證了自己能把一個圖論演算法做到「拿真實輸入也不會壞」的程度。

## 背景與動機

每個編譯器與反編譯器在拿到一個函式之後，做的第一批分析幾乎一模一樣：

1. 從入口做 DFS，丟掉走不到的基本塊（dead code）。
2. 算 dominator tree（Ch 14）。
3. 找回邊與自然迴圈，建立迴圈巢狀樹——LLVM 的 `LoopInfo`、GCC 的 loop tree、Ghidra 與 IDA 的控制流結構化都是這一步。
4. 算支配邊界，準備放 φ（SSA 建構）。

真實世界的 CFG 會給你教科書沒有的麻煩。`goto`、`switch` 的 fall-through、編譯器的 tail duplication、混淆器的 control-flow flattening，都可能產生**不可約迴圈**：一個有兩個入口的迴圈，沒有任何一個點支配整個迴圈。自然迴圈的定義抓不到它，很多工具會靜靜地漏掉它。反編譯器遇到它時通常只能退回 `goto`。這個練習要你的工具**明確地報告**這種情況，而不是假裝它不存在。

和你寫這個工具相關的背景：

> 自然迴圈與迴圈巢狀在 [ssa_optimizations Ch 20 — 迴圈識別](../../compilers/ssa_optimizations/20-loop-identification.md) 有 LLVM 視角的介紹；逆向工程裡怎麼從組語辨認迴圈，見 [reverse_engineering Ch 5 — 辨識控制流](../../security/reverse_engineering/05-recognizing-control-flow.md)。本練習要你把它們背後的圖論親手做一次。

### 需要的定義（複習）

- **回邊（back edge）**：邊 `t → h`，且 `h` 支配 `t`。自環 `h → h` 也算。
- **自然迴圈（natural loop）**：回邊 `t → h` 的自然迴圈是 `{h}` 加上「不經過 `h` 就能走到 `t`」的所有點。同一個 header 的多條回邊，迴圈合併成一個。
- **退回邊（retreating edge）**：對某一次 DFS 而言，指向 DFS 樹祖先（含自己）的邊。回邊一定是退回邊（支配者一定是 DFS 祖先），反之不一定。
- **可約（reducible）**：每條退回邊都是回邊。Hecht 與 Ullman 證明這個性質**和 DFS 的走法無關**，並且等價於「反覆做 T1（刪自環）與 T2（只有一個前驅的點併入前驅）能把圖化簡成一個點」。
- **兩個自然迴圈只會「互斥」「巢狀」或「同 header」**：所以合併同 header 的迴圈之後，迴圈構成一個森林（巢狀樹）。

## 任務規格

### 輸入格式

純文字，一行一個敘述：

```text
entry <名稱>                 # 宣告入口，恰好一次，可以出現在任何位置
<u> -> <v1> <v2> ...         # u 的後繼依序是 v1, v2, ...；可以沒有後繼（u ->）
```

- `#` 之後到行尾是註解；空行忽略。
- 基本塊名稱是不含空白、不含 `#` 的任意字串；`->` 前後的空白可有可無。
- 同一個 `u` 可以出現在多行，後繼依出現順序累加；重複的邊只算一次。
- 只出現在箭頭右邊的名稱也是基本塊（沒有後繼）。
- 允許自環、允許邊指回入口、允許從入口走不到的基本塊。

### 輸出格式

依序輸出下列區段，**每個「列表」都依反後序（reverse postorder, RPO）排列**；RPO 由一次「依輸入順序走後繼」的 DFS 決定：

```text
reachable: <所有可達點，RPO>
unreachable: <不可達點，依名稱第一次出現的順序；沒有就輸出 ->
idom:
  <v>: <idom(v)>              # 每個可達且非入口的點，RPO
domtree:
  <entry>                     # 前序輸出，每深一層多縮排兩格，兄弟依 RPO
    <child> ...
df:
  <v>: <DF(v) 的成員，RPO；空集合輸出 ->
back_edges: <t->h ...，依 (h 的 RPO, t 的 RPO) 排序；沒有就輸出 ->
loops:                        # 沒有迴圈就輸出 "loops: -"
  <header>: <迴圈內所有點，RPO>   # 巢狀迴圈縮排在外層迴圈之下，同層依 header 的 RPO
irreducible: no | yes <u->h ...>   # 是退回邊但不是回邊的邊，排序同 back_edges
```

### 錯誤處理

輸入不合法時，輸出一行 `error: <訊息>` 到 **stderr**，結束碼 1，不輸出任何分析結果。至少要偵測：沒有 `entry`、`entry` 宣告兩次、`entry` 後面沒有名稱或多於一個名稱、既不是 `entry` 也不含 `->` 的行、`->` 左邊沒有名稱或有多個名稱。訊息要包含行號（「沒有 entry」除外）。

### 限制與驗收

- 解答本身**不可以**用 NetworkX（它只能出現在測試裡當對答案的工具）。只用標準函式庫。
- 必須是迭代實作：輸入 10 萬個基本塊、DFS 深度 10 萬的 CFG 不能 `RecursionError`。
- 效能：10 萬個基本塊、約 14 萬條邊的 CFG，CPython 3.13 在一般桌機上 10 秒內完成（參考解答在 AMD Ryzen 7 5700G 上約 2–3 秒）。
- 驗收：下面「測試用例」全數通過；隨機對拍中 idom、DF 與 NetworkX 一致，不可約判定與獨立的 T1/T2 化簡一致。

## 期望輸出範例

輸入（Ch 14 的範例 CFG，加上一個不可達的 `dead`）：

```text
entry entry
entry -> B1
B1 -> B2 B3
B2 -> B4
B3 -> B4
B4 -> B5
B5 -> B1 exit
dead -> B4      # 不可達：沒有任何路徑從 entry 走到 dead
```

輸出：

```text
reachable: entry B1 B3 B2 B4 B5 exit
unreachable: dead
idom:
  B1: entry
  B3: B1
  B2: B1
  B4: B1
  B5: B4
  exit: B5
domtree:
  entry
    B1
      B3
      B2
      B4
        B5
          exit
df:
  entry: -
  B1: B1
  B3: B4
  B2: B4
  B4: B1
  B5: B1
  exit: -
back_edges: B5->B1
loops:
  B1: B1 B3 B2 B4 B5
irreducible: no
```

幾個值得注意的地方：`B3` 排在 `B2` 前面，因為 DFS 先走 `B2`、`B2` 子樹先完成，反後序裡就比較後面；`dead` 只出現在 `unreachable`，它指向 `B4` 的那條邊對任何分析都沒有影響；迴圈 header `B1` 出現在自己的 DF 裡。

邊界輸入：兩個入口的迴圈（`e` 可以直接進 `A`，也可以直接進 `B`）。

```text
entry e
e -> A B
A -> B
B -> A
```

輸出：

```text
reachable: e A B
unreachable: -
idom:
  A: e
  B: e
domtree:
  e
    A
    B
df:
  e: -
  A: B
  B: A
back_edges: -
loops: -
irreducible: yes B->A
```

`A`、`B` 互相支配不了對方，所以 `B -> A` 不是回邊，自然迴圈為空；但它是退回邊，工具必須回報 `irreducible: yes`。

非法輸入（第二行沒有箭頭）：

```text
entry a
a b
```

輸出（stderr，結束碼 1）：

```text
error: line 2: expected 'u -> v ...'
```

## 如果你卡住了

1. **不可達的點先處理**：所有分析都只在可達的點上做。建前驅列表時，只收「來源可達」的邊——否則 `dead -> B4` 會讓 `B4` 多一個沒有 idom 的前驅，CHK 的 `intersect` 會爆掉。
2. **回邊判斷要快**：「`h` 支配 `t`」如果每次都沿 idom 往上爬，在一條 10 萬層的支配鏈上會變成平方時間。對 dominator tree 做一次 DFS 記下進出時間，`a dom b` 就變成區間包含，`O(1)`。
3. **退回邊用 DFS 進出時間判斷**：和第 2 點同一招，用的是 CFG 的 DFS 樹。注意 `u → u` 自環既是退回邊也是回邊。
4. **自然迴圈是反向走訪**：從回邊來源 `t` 沿**前驅**走，碰到 `h` 就停（`h` 先放進集合，就自然不會越過它）。同一個 header 的多條回邊共用同一個集合。
5. **入口在自己的 DF 裡**：若有邊指回入口，Ch 14 踩雷 3 的情況會出現。DF 演算法要把入口也當成匯合點處理。

## 實作步驟建議

### Step 1：解析器

寫 `parse(text) -> (entry, succ)`，`succ` 是 `dict[str, list[str]]`，保留後繼的輸入順序。先把錯誤處理寫完並用「錯誤處理」那幾種輸入測過，再往下做。輸出：能對範例輸入印出 `succ`。

### Step 2：DFS 與可達性

迭代 DFS，產生前序、後序、每個點的進出時間。由後序得到 RPO 與 `reachable`/`unreachable`。輸出：範例的前兩行正確。

### Step 3：idom 與 dominator tree

用 Ch 14 的 CHK（最短）或 LT。印出 `idom` 與縮排的 `domtree`（印樹也要迭代，樹可以很深）。輸出：和 `nx.immediate_dominators` 對拍通過（記得 NetworkX 3.7 的結果不含入口）。

### Step 4：支配邊界

照 Ch 14 的 CHK DF 演算法，處理入口有前驅的情況。輸出：和 `nx.dominance_frontiers` 對拍通過（它的結果包含入口）。

### Step 5：回邊、不可約邊、自然迴圈與巢狀

建 dominator tree 的進出時間做 `O(1)` 支配查詢；掃每條可達的邊分類；對每個 header 做反向走訪；用「嚴格包含自己 header 的最小迴圈」找父迴圈。輸出：`nested` 與 `irreducible` 兩個測試通過。

### Step 6：整合與測試

把輸出格式對齊規格、補上 `main()` 與結束碼，跑完下面所有測試，最後跑 10 萬個基本塊的效能測試。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

```python
"""cfg_analyzer.py：讀 edge list 格式的 CFG，輸出 idom、dominator tree、DF、回邊、自然迴圈、不可約性。"""
import sys


class CFGError(ValueError):
    pass


def parse(text):
    """格式：第一個有效行 `entry X`，其後每行 `u -> v1 v2 ...`（可以沒有後繼）。# 之後是註解。"""
    entry, succ = None, {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "->" not in line and line.split()[0] == "entry":
            parts = line.split()
            if len(parts) != 2:
                raise CFGError(f"line {lineno}: expected 'entry <name>'")
            if entry is not None:
                raise CFGError(f"line {lineno}: duplicate entry")
            entry = parts[1]
            succ.setdefault(entry, [])
            continue
        if "->" not in line:
            raise CFGError(f"line {lineno}: expected 'u -> v ...'")
        left, right = line.split("->", 1)
        u = left.strip()
        if not u or len(u.split()) != 1:
            raise CFGError(f"line {lineno}: bad source block name")
        succ.setdefault(u, [])
        for v in right.split():
            succ.setdefault(v, [])
            if v not in succ[u]:                      # 重複邊只算一次，保留第一次出現的順序
                succ[u].append(v)
    if entry is None:
        raise CFGError("no entry declared")
    return entry, succ


def dfs_orders(succ, r):
    """迭代 DFS（依輸入順序走後繼）。回傳前序、後序，以及每個點的 DFS 區間（判斷祖先用）。"""
    pre, post, tin, tout = [], [], {}, {}
    clock = 0
    tin[r] = clock
    pre.append(r)
    work = [(r, iter(succ[r]))]
    while work:
        v, it = work[-1]
        for w in it:
            if w not in tin:
                clock += 1
                tin[w] = clock
                pre.append(w)
                work.append((w, iter(succ[w])))
                break
        else:
            work.pop()
            clock += 1
            tout[v] = clock
            post.append(v)
    return pre, post, tin, tout


def analyze(entry, succ):
    pre, post, tin, tout = dfs_orders(succ, entry)
    rpo = post[::-1]
    reach = set(rpo)
    rank = {v: i for i, v in enumerate(rpo)}
    pred = {v: [] for v in rpo}
    for u in rpo:
        for w in succ[u]:
            pred[w].append(u)                         # 只收可達的前驅：不可達的邊被忽略

    # --- idom：Cooper–Harvey–Kennedy（Ch 14）---
    num = {v: i for i, v in enumerate(post)}
    idom = {entry: entry}

    def intersect(a, b):
        while a != b:
            while num[a] < num[b]:
                a = idom[a]
            while num[b] < num[a]:
                b = idom[b]
        return a

    changed = True
    while changed:
        changed = False
        for v in rpo[1:]:
            ps = [p for p in pred[v] if p in idom]
            new = ps[0]
            for p in ps[1:]:
                new = intersect(p, new)
            if idom.get(v) != new:
                idom[v], changed = new, True
    parent = {v: (None if v == entry else idom[v]) for v in rpo}
    kids = {v: [] for v in rpo}
    for v in rpo[1:]:
        kids[parent[v]].append(v)
    # dominator tree 的進出時間：a dom b ⇔ b 的區間包在 a 的區間裡，O(1) 查詢
    din, dout, clock, stack = {}, {}, 0, [(entry, False)]
    while stack:
        v, done = stack.pop()
        clock += 1
        if done:
            dout[v] = clock
            continue
        din[v] = clock
        stack.append((v, True))
        stack.extend((c, False) for c in kids[v])

    def dominates(a, b):
        return din[a] <= din[b] and dout[b] <= dout[a]

    # --- 支配邊界（入口也當匯合點，見 Ch 14 踩雷 3）---
    df = {v: set() for v in rpo}
    for y in rpo:
        if y != entry and len(pred[y]) < 2:
            continue
        for p in pred[y]:
            runner = p
            while runner != parent[y]:
                df[runner].add(y)
                runner = parent[runner]

    # --- 回邊、不可約邊 ---
    def is_dfs_ancestor(a, b):                        # a 是 b 的 DFS 祖先（含自己）
        return tin[a] <= tin[b] and tout[b] <= tout[a]

    back, irreducible = [], []
    for u in rpo:
        for h in succ[u]:
            if dominates(h, u):
                back.append((u, h))                   # 回邊：目標支配來源（含自環）
            elif is_dfs_ancestor(h, u):
                irreducible.append((u, h))            # 退回邊但目標不支配來源 → 多入口迴圈

    # --- 自然迴圈：同 header 的回邊合併 ---
    loops = {}
    for t, h in back:
        body = loops.setdefault(h, {h})
        stack = [t] if t not in body else []
        body.add(t)
        while stack:                                  # 從 t 反向走，不越過 h
            x = stack.pop()
            for p in pred[x]:
                if p not in body:
                    body.add(p)
                    stack.append(p)
    # 巢狀：父迴圈 = 嚴格包含自己 header 的最小迴圈
    loop_parent = {}
    for h, body in loops.items():
        outer = [g for g in loops if g != h and h in loops[g]]
        for g in outer:
            if not body <= loops[g]:
                raise AssertionError(f"loops {g} and {h} overlap without nesting")
        loop_parent[h] = min(outer, key=lambda g: len(loops[g])) if outer else None

    return {
        "rpo": rpo, "rank": rank, "unreachable": [v for v in succ if v not in reach],
        "idom": {v: idom[v] for v in rpo[1:]}, "df": df, "back": back,
        "loops": loops, "loop_parent": loop_parent, "irreducible": irreducible,
    }


def render(res):
    rank = res["rank"]
    by = lambda xs: sorted(xs, key=rank.__getitem__)
    edge_key = lambda e: (rank[e[1]], rank[e[0]])
    out = ["reachable: " + " ".join(res["rpo"]),
           "unreachable: " + (" ".join(res["unreachable"]) or "-"), "idom:"]
    out += [f"  {v}: {d}" for v, d in res["idom"].items()]
    out.append("domtree:")
    kids = {v: [] for v in res["rpo"]}
    for v, d in res["idom"].items():
        kids[d].append(v)
    stack = [(res["rpo"][0], 0)]                      # 迭代版前序輸出（樹可以很深）
    while stack:
        v, depth = stack.pop()
        out.append("  " * (depth + 1) + v)
        for c in reversed(by(kids[v])):
            stack.append((c, depth + 1))
    out.append("df:")
    out += [f"  {v}: " + (" ".join(by(res["df"][v])) or "-") for v in res["rpo"]]
    out.append("back_edges: " + (" ".join(f"{u}->{h}" for u, h in sorted(res["back"], key=edge_key)) or "-"))
    out.append("loops:" + ("" if res["loops"] else " -"))
    children = {h: [] for h in res["loops"]}
    roots = []
    for h, p in res["loop_parent"].items():
        (children[p] if p else roots).append(h)
    stack = [(h, 0) for h in reversed(by(roots))]
    while stack:
        h, depth = stack.pop()
        out.append("  " * (depth + 1) + f"{h}: " + " ".join(by(res["loops"][h])))
        for c in reversed(by(children[h])):
            stack.append((c, depth + 1))
    irr = res["irreducible"]
    out.append("irreducible: " + ("yes " + " ".join(f"{u}->{h}" for u, h in sorted(irr, key=edge_key)) if irr else "no"))
    return "\n".join(out)


def main(argv):
    text = open(argv[1], encoding="utf-8").read() if len(argv) > 1 else sys.stdin.read()
    try:
        entry, succ = parse(text)
    except CFGError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(render(analyze(entry, succ)))
    return 0


if __name__ == "__main__" and len(sys.argv) > 1:   # 被 import 或接續測試時不執行
    sys.exit(main(sys.argv))
```

**解答說明**：

- **只看可達的邊**：`pred` 是從 RPO 裡的點出發建的，不可達的來源自動被排除。這一行同時解決了 idom、DF、自然迴圈三處的不可達問題。
- **兩組進出時間**：`tin`/`tout` 是 CFG 的 DFS 樹，用來判斷退回邊；`din`/`dout` 是 dominator tree，用來判斷支配。兩者都把祖先查詢變成區間包含。第一版解答用「沿 idom 往上爬」判斷支配，10 萬個基本塊要 15 秒；換成區間後降到 2–3 秒（同一台機器、多次執行的範圍）。
- **分類邊的順序**：先檢查「目標支配來源」（回邊），再檢查「目標是 DFS 祖先」（退回但非回邊）。因為支配者一定是 DFS 祖先，回邊一定也是退回邊，順序反過來會把所有回邊都報成不可約。
- **迴圈巢狀的斷言**：`analyze` 裡的 `AssertionError` 檢查「兩個不同 header 的迴圈若相交就一定巢狀」。這是一個定理（不同 header 的自然迴圈互斥或巢狀），寫成斷言是把定理當成執行期的自我檢查——如果它被觸發，一定是程式有 bug。
- **為什麼用 CHK 不用 LT**：CHK 約 20 行，在 CFG 型的輸入上和 LT 差不多快（Ch 14 的計時）。若要處理病態輸入，把 `analyze` 裡的 idom 計算換成 Ch 14 的 `idom_lengauer_tarjan` 即可，其他部分不受影響。
- **輸出順序的設計**：全部用 RPO 排序，是因為 RPO 是編譯器裡最常用的區塊順序（資料流分析依 RPO 迭代收斂最快），而且它只依賴輸入，不依賴雜湊或字典順序，輸出可重現。

</details>

## 測試用例

| 輸入 | 預期輸出（關鍵行） | 說明 |
| --- | --- | --- |
| 範例 CFG + `dead` | 見「期望輸出範例」 | 正常路徑：if/else 包在迴圈裡，加一個不可達點 |
| `e -> A B`、`A -> B`、`B -> A` | `back_edges: -`、`loops: -`、`irreducible: yes B->A` | 不可約：兩個入口的迴圈 |
| `e -> A`、`A -> A B` | `back_edges: A->A`、`A: A` | 自環是長度 1 的迴圈 |
| `e -> A`、`A -> e` | `df` 裡 `e: e`、`back_edges: A->e`、迴圈 `e: e A` | 迴圈回到入口；入口在自己的 DF 裡 |
| 兩層巢狀迴圈 `H1 ⊃ H2` | `H1: H1 H2 B C`，下一行縮排 `H2: H2 B` | 迴圈巢狀樹 |
| 只有 `entry only` | `reachable: only`、`loops: -` | 單一點、沒有邊 |
| 入口沒有出邊，另有 `x ⇄ y` | `unreachable: x y`、`irreducible: no` | 不可達的迴圈不算迴圈 |
| 沒有 `entry` | `error: no entry declared` | 非法輸入 |
| `entry` 兩次 | `error: line 2: duplicate entry` | 非法輸入 |
| `a b`（沒有箭頭） | `error: line 2: expected 'u -> v ...'` | 非法輸入 |
| `-> b`（箭頭左邊沒有名稱） | `error: line 2: bad source block name` | 非法輸入 |
| 只有 `entry` | `error: line 1: expected 'entry <name>'` | 非法輸入 |
| 隨機 1500 張圖（seed 2026） | 與 NetworkX、T1/T2 一致 | 隨機對拍 |
| 10 萬個基本塊 | 10 秒內完成 | 效能邊界 |

把上表寫成程式（接在參考解答後面執行）：

```python
# 接續上一段：把這段貼在參考解答後面執行（或另存成 test_cfg.py 並 import 解答）
CASES = {
    "irreducible": "entry e\ne -> A B\nA -> B\nB -> A\n",
    "self_loop": "entry e\ne -> A\nA -> A B\n",
    "loop_to_entry": "entry e\ne -> A\nA -> e\n",
    "nested": "entry e\ne -> H1\nH1 -> H2\nH2 -> B\nB -> H2 C\nC -> H1 X\n",
    "single": "entry only\n",
    "all_dead": "# 入口沒有任何出邊\nentry e\nx -> y\ny -> x\n",
}
EXPECT = {
    "irreducible": ("back_edges: -", "loops: -", "irreducible: yes B->A"),
    "self_loop": ("back_edges: A->A", "  A: A", "irreducible: no"),
    "loop_to_entry": ("  e: e", "back_edges: A->e", "  e: e A"),
    "nested": ("back_edges: C->H1 B->H2", "  H1: H1 H2 B C", "    H2: H2 B"),
    "single": ("reachable: only", "domtree:\n  only", "loops: -"),
    "all_dead": ("unreachable: x y", "back_edges: -", "irreducible: no"),
}
for name, text in CASES.items():
    out = render(analyze(*parse(text)))
    missing = [frag for frag in EXPECT[name] if frag not in out]
    print(f"{name:14s} {'PASS' if not missing else 'FAIL ' + str(missing)}")

for bad in ["e -> A\n", "entry a\nentry b\n", "entry a\na b\n", "entry a\n -> b\n", "entry\n"]:
    try:
        parse(bad)
        print(f"{bad!r:24} FAIL：應該報錯")
    except CFGError as e:
        print(f"{bad!r:24} error: {e}")
```

```text
irreducible    PASS
self_loop      PASS
loop_to_entry  PASS
nested         PASS
single         PASS
all_dead       PASS
'e -> A\n'               error: no entry declared
'entry a\nentry b\n'     error: line 2: duplicate entry
'entry a\na b\n'         error: line 2: expected 'u -> v ...'
'entry a\n -> b\n'       error: line 2: bad source block name
'entry\n'                error: line 1: expected 'entry <name>'
```

隨機對拍與效能測試。這裡用了兩個**和解答完全獨立**的標準答案：NetworkX 的 idom/DF，以及 Hecht–Ullman 的 T1/T2 化簡（它根本不看支配關係，用的是「刪自環 + 合併唯一前驅」）。自然迴圈則直接照定義用 `nx.ancestors` 重算。

```python
# 計時與隨機對拍（接續上一段：貼在測試程式後面執行）：秒數因機器而異
import random
import time
import networkx as nx


def reducible_t1_t2(succ, r):
    """Hecht–Ullman 的 T1/T2 化簡：可約 ⇔ 能化簡成單一點。和 analyze() 完全獨立的判定法。"""
    reach, stack = {r}, [r]
    while stack:
        for w in succ[stack.pop()]:
            if w not in reach:
                reach.add(w)
                stack.append(w)
    S = {u: {v for v in succ[u] if v in reach} for u in reach}
    P = {v: {u for u in reach if v in S[u]} for v in reach}
    changed = True
    while changed:
        changed = False
        for v in list(S):
            if v in S[v]:                              # T1：刪自環
                S[v].discard(v)
                P[v].discard(v)
                changed = True
            if v != r and len(P[v]) == 1:              # T2：唯一前驅 u 吸收 v
                (u,) = P[v]
                for w in S[v]:
                    P[w].discard(v)
                    P[w].add(u)
                    S[u].add(w)
                S[u].discard(v)
                del S[v], P[v]
                changed = True
    return len(S) == 1


rng = random.Random(2026)
n_irr = n_dead = 0
for t in range(1500):
    n = rng.randint(1, 12)
    G = nx.gnp_random_graph(n, rng.choice([0.1, 0.2, 0.35]), seed=rng.randint(0, 10**9), directed=True)
    names = {v: f"b{v}" for v in G}
    text = "entry b0\n" + "".join(f"b{u} -> " + " ".join(names[w] for w in sorted(G[u])) + "\n" for u in G)
    entry, succ = parse(text)
    res = analyze(entry, succ)
    H = nx.relabel_nodes(G, names)
    assert res["idom"] == nx.immediate_dominators(H, "b0")
    assert res["df"] == nx.dominance_frontiers(H, "b0")
    assert (not res["irreducible"]) == reducible_t1_t2(succ, entry)
    # 自然迴圈的定義：h 加上「不經過 h 能走到某個回邊來源 t」的點
    for h, body in res["loops"].items():
        tails = [u for u, hh in res["back"] if hh == h]
        Hh = H.subgraph(set(res["rpo"]) - {h})
        expect = {h} | {x for t in tails if t != h for x in nx.ancestors(Hh, t) | {t}}
        assert body == expect, (text, h)
    n_irr += bool(res["irreducible"])
    n_dead += bool(res["unreachable"])
print(f"1500 張隨機 CFG：idom、DF 與 NetworkX 一致；不可約判定與 T1/T2 化簡一致（不可約 {n_irr} 張、含不可達點 {n_dead} 張）；自然迴圈符合定義")

n = 100_000
rng = random.Random(1)
lines = ["entry b0"]
for i in range(n):
    tgt = [f"b{i + 1}"] if i + 1 < n else []
    if rng.random() < 0.3 and i + 2 < n:
        tgt.append(f"b{rng.randint(i + 2, min(n - 1, i + 20))}")
    if rng.random() < 0.1 and i > 0:
        tgt.append(f"b{rng.randint(max(0, i - 50), i)}")
    lines.append(f"b{i} -> " + " ".join(tgt))
t0 = time.perf_counter()
out = render(analyze(*parse("\n".join(lines))))
print(f"10 萬個基本塊：輸出 {out.count(chr(10)) + 1} 行，耗時 {time.perf_counter() - t0:.1f}s")
```

```text
1500 張隨機 CFG：idom、DF 與 NetworkX 一致；不可約判定與 T1/T2 化簡一致（不可約 366 張、含不可達點 1037 張）；自然迴圈符合定義
10 萬個基本塊：輸出 300890 行，耗時 1.7s
```

（接在前面兩段後面執行，畫面上會先看到測試表的輸出；這裡只列這一段的兩行。AMD Ryzen 7 5700G、Windows 11、Python 3.13.12 實測，耗時因機器而異。）1500 張隨機圖中有 366 張不可約、1037 張含不可達點——比手寫測試多出好幾個數量級的邊界情況。

## 延伸挑戰（加分）

- **Havlak 迴圈巢狀**：自然迴圈對不可約迴圈無能為力。實作 Havlak（1997）的演算法，替不可約迴圈也建出巢狀結構（它會選一個入口當代表 header），並在 `irreducible` 測試上輸出它找到的迴圈。
- **node splitting**：把不可約 CFG 轉成可約的經典作法是複製節點。實作「挑一個多入口迴圈的入口，把迴圈複製一份給另一個入口」，轉換後重新分析，確認 `irreducible: no`，並量測節點數膨脹了多少。
- **post-dominator 與控制相依**：加一個 `--post` 參數，在反向圖上以虛擬出口（連到所有沒有後繼的點）為入口算 post-dominator tree，輸出控制相依關係。
- **吃真實的 CFG**：寫一個轉換器，把 `objdump -d` 的輸出（在 WSL 用 `gcc -O1` 編一個有 `switch` 與 `goto` 的 C 檔）或 LLVM 的 `opt -passes=dot-cfg` 產生的 `.dot` 轉成本練習的格式，找出真實程式裡的不可約迴圈。
- **換成 LT**：把 idom 換成 Ch 14 的 Lengauer–Tarjan，構造一個讓 CHK 跑很多輪的輸入，比較兩者在 10 萬個基本塊上的時間。

## 自我檢核

- [ ] 能解釋為什麼「是退回邊但不是回邊」等價於 CFG 不可約，以及為什麼這個判斷和 DFS 的走法無關（Hecht–Ullman）。
- [ ] 不看解答，說出自然迴圈的反向走訪為什麼不會越過 header，以及為什麼同一個 header 的回邊要合併。
- [ ] 能指出參考解答中哪一行讓不可達的基本塊不影響任何分析。
- [ ] 能說出自己的實作和參考解答的差異（例如用了 LT、遞迴改迭代的方式、輸出排序），並解釋各自的取捨。
- [ ] 如果反編譯器在某個函式上輸出一堆 `goto`，你能用這個工具判斷原因是不是不可約迴圈。

## 延伸閱讀

- **[Characterizations of Reducible Flow Graphs](https://doi.org/10.1145/321832.321835)** — Matthew S. Hecht、Jeffrey D. Ullman，JACM 21(3):367–375（1974）
  - **讀哪裡**：可約性的幾種等價刻畫（T1/T2、退回邊 = 回邊），本練習的 `irreducible` 判定與 T1/T2 對拍都來自這裡。
  - **前提**：本練習的定義複習。
- **[Testing Flow Graph Reducibility](https://doi.org/10.1016/S0022-0000(74)80049-8)** — Robert Endre Tarjan，JCSS 9(3):355–365（1974）
  - **讀哪裡**：近線性時間的可約性測試，用的是 Ch 10 的 union-find 技巧；可以和本練習「掃一遍退回邊」的作法比較。
  - **前提**：Ch 10 的 union-find、Ch 14 的 dominator。
- **[Nesting of Reducible and Irreducible Loops](https://doi.org/10.1145/262004.262005)** — Paul Havlak，ACM TOPLAS 19(4):557–567（1997）
  - **讀哪裡**：延伸挑戰第一題的演算法；它在可約的情況下退化成自然迴圈巢狀。
  - **前提**：本練習的迴圈巢狀樹。
- **[On Loops, Dominators, and Dominance Frontiers](https://doi.org/10.1145/570886.570887)** — G. Ramalingam，ACM TOPLAS 24(5):455–490（2002）
  - **讀哪裡**：比較各種「迴圈」定義（包含不可約迴圈的處理），並說明如何把不可約 CFG 上的支配與 DF 問題化約到可約的情況。
  - **前提**：Ch 14 全章。
- **《Advanced Compiler Design and Implementation》** — Steven S. Muchnick（1997）
  - **讀哪幾章**：§7.4「Loops and Strongly Connected Components」與 §7.5「Reducibility」，是本練習整體的教科書參考，含 node splitting 的說明。

Part 2 的圖都只關心「有沒有路」。Part 3 開始給點塗顏色：相鄰的點不能同色、最少要幾種顏色——這個問題在編譯器裡的名字叫暫存器分配。

→ [Ch 15 著色：貪婪、Brooks、Vizing](./15-graph-coloring.md)
