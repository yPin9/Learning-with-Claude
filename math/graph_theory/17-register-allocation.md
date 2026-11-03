# Ch 17 — Register allocation：Chaitin–Briggs

> **目標**：從 liveness 資料流一路做到 interference graph、Chaitin 的 simplify/spill、Briggs 的 optimistic coloring、Briggs/George 兩種保守 coalescing，在一段真的小 IR 上跑完整流程（含 spill 改寫後重跑），並能用 Ch 15–16 的定理解釋每一步為什麼成立、什麼時候失效。
>
> **環境**：Python 3.13.12、NetworkX 3.7，Windows 11。本章程式只用標準函式庫。

## 為什麼需要這個？

CPU 只有少數幾個暫存器（x86-64 有 16 個通用暫存器，RISC-V 有 32 個，其中幾個保留給 sp、ra 等用途），程式裡的變數和編譯器產生的暫時值卻可能有上百個。暫存器分配（register allocation）決定**哪個值放哪個暫存器、放不下的放記憶體（spill）**。放錯的代價很具體：一次 L1 cache 命中的 load 是幾個 cycle，放在迴圈裡就乘上迭代次數。

1970 年代以前，編譯器多半用區域性的作法：一個基本區塊（basic block）內部分配，跨區塊的值一律寫回記憶體。1981 年 IBM 的 Chaitin 等人在 PL.8 編譯器裡把整個函式的分配建模成**圖著色**：

```text
程式 ──liveness──▶ 每個點誰活著 ──建圖──▶ interference graph ──k 著色──▶ 暫存器
                                                       │
                                                 著不了色 → 挑變數 spill → 改寫程式 → 重來
```

這個模型的優雅在於它把一個工程問題變成了一個圖論問題，而圖論的定理直接告訴我們哪些啟發式有保證、哪些沒有。Chaitin 等人也證明了它的難度：對任何一張圖，都能寫出一段程式以它為 interference graph，所以一般情況下「k 個暫存器夠不夠」是 NP-complete。

> 這一章在 [compilers/compiler_backend 的 Ch 12 — Register allocator](../../compilers/compiler_backend/12-register-allocator.md) 有工程版對照：LLVM 的 greedy allocator 不再是純 Chaitin–Briggs，但 interference、spill cost、coalescing、split 這些概念全部沿用。

## 先建立直覺

一個變數從被定義到最後一次被使用之間是「活著」的，這段範圍叫**活躍範圍**（live range）。兩個活躍範圍重疊的變數不能共用暫存器。

```text
指令              活著的變數（指令之後）
i = 0             i
s = 0             i s
n = 10            i s n
L: if i < n       i s n
   t = i * i      i s n t       ← 4 個同時活著
   s2 = s + t     i n s2
   s = s2         i n s
   i2 = i + 1     n s i2
   i = i2         n s i
   goto L
```

把每個變數當成點，**同時活著**就連邊，得到 interference graph。暫存器數 k 就是顏色數。Chaitin 的關鍵觀察是一個再樸素不過的引理：

> **引理（simplify）**：若 `deg(v) < k`，且 `G - v` 可以 k 著色，則 G 可以 k 著色。v 的鄰居最多用掉 `k-1` 色，總會剩一色給它。

所以可以反覆拔掉度數 `< k` 的點（simplify），拔到空就保證可以著色；拔到卡住（所有點度數 `≥ k`）時才需要 spill。這正是 Ch 15 smallest-last 順序與 Brooks 證明裡「讓每個點著色時被擋的顏色少於 k」的同一個想法。

> 如果你對貪婪著色的排序和 `χ` vs `ω` 還不熟，先回看 [Ch 15 — 著色：貪婪、Brooks、Vizing](./15-graph-coloring.md)；SSA 上為什麼可以做到最佳，見 [Ch 16 — Chordal graph 與完美消去序](./16-chordal-graphs.md)。

## Liveness：資料流方程

變數 x 在程式點 p **活著**：存在一條從 p 出發、到達 x 的某個使用點、途中沒有重新定義 x 的路徑。這是「往未來看」的性質，所以用**反向**資料流計算。對每個基本區塊 B：

```text
use[B] = B 裡「被定義之前就被使用」的變數
def[B] = B 裡被定義的變數
out[B] = ∪ in[S]，S 是 B 的後繼
in[B]  = use[B] ∪ (out[B] − def[B])
```

所有集合從空集合開始，反覆套用直到不再變化（不動點）。集合只會變大、而且有上限（全部變數），所以一定會停。迭代順序不影響答案、只影響輪數；反向走訪（逆後序）通常最快。完整的資料流框架理論在 [ssa_optimizations Ch 7](../../compilers/ssa_optimizations/07-dataflow-framework.md) 與 [Ch 8 — Liveness](../../compilers/ssa_optimizations/08-liveness-analysis.md)。

我們的小 IR：每個區塊是 `(指令清單, 後繼清單)`，指令只有五種形式——`x = 常數`、`x = y`（move）、`x = y op z`、`if x < y`、`print x`。

```python
import re

LOOP = {
    "B0": (["i = 0", "s = 0", "n = 10"], ["B1"]),
    "B1": (["if i < n"], ["B2", "B3"]),
    "B2": (["t = i * i", "s2 = s + t", "s = s2", "i2 = i + 1", "i = i2"], ["B1"]),
    "B3": (["print s"], []),
}
VAR = r"[a-z]\w*"


def parse(ins):
    """回傳 (defs, uses, move)；move 是 (dst, src) 或 None。"""
    if m := re.fullmatch(rf"({VAR}) = ({VAR})", ins):
        return {m[1]}, {m[2]}, (m[1], m[2])
    if m := re.fullmatch(rf"({VAR}) = (\w+) [-+*<] (\w+)", ins):
        return {m[1]}, {x for x in (m[2], m[3]) if re.fullmatch(VAR, x)}, None
    if m := re.fullmatch(rf"({VAR}) = (\d+)", ins):
        return {m[1]}, set(), None
    if m := re.fullmatch(rf"({VAR}) = load @({VAR})", ins):        # spill 改寫才會出現
        return {m[1]}, set(), None
    if m := re.fullmatch(rf"store @({VAR}), ({VAR})", ins):
        return set(), {m[2]}, None
    if m := re.fullmatch(rf"(?:print|if) ({VAR})(?: < ({VAR}))?", ins):
        return set(), {x for x in m.groups() if x}, None
    raise ValueError(f"看不懂的指令: {ins!r}")


def liveness(prog):
    use, defs = {}, {}
    for b, (code, _) in prog.items():
        u, d = set(), set()
        for ins in code:
            di, ui, _ = parse(ins)
            u |= ui - d
            d |= di
        use[b], defs[b] = u, d
    live_in = {b: set() for b in prog}
    live_out = {b: set() for b in prog}
    rounds, changed = 0, True
    while changed:
        changed, rounds = False, rounds + 1
        for b in reversed(list(prog)):
            out = set().union(*(live_in[s] for s in prog[b][1]))
            inn = use[b] | (out - defs[b])
            if (out, inn) != (live_out[b], live_in[b]):
                live_out[b], live_in[b], changed = out, inn, True
    return live_in, live_out, rounds


def build(prog):
    """在每個區塊裡從尾往頭走，維護 live 集合，逐指令加干擾邊。"""
    _, live_out, _ = liveness(prog)
    adj, moves, maxlive = {}, [], 0
    for b, (code, _) in prog.items():
        live = set(live_out[b])
        for ins in reversed(code):
            d, u, mv = parse(ins)
            for x in d | u:
                adj.setdefault(x, set())
            others = live - ({mv[1]} if mv else set())   # move 例外：x = y 的 x 不和 y 連邊
            for x in d:
                for y in others - {x}:
                    adj[x].add(y)
                    adj.setdefault(y, set()).add(x)
            if mv:
                moves.append(mv)
            maxlive = max(maxlive, len(live | d))
            live = (live - d) | u
    return adj, moves, maxlive


li, lo, rounds = liveness(LOOP)
print(f"不動點輪數: {rounds}")
for b in LOOP:
    print(f"  {b}: in={sorted(li[b])} out={sorted(lo[b])}")
adj, moves, maxlive = build(LOOP)
print("interference:", {v: sorted(n) for v, n in sorted(adj.items())})
print("moves:", moves, " maxlive:", maxlive)
```

```text
不動點輪數: 3
  B0: in=[] out=['i', 'n', 's']
  B1: in=['i', 'n', 's'] out=['i', 'n', 's']
  B2: in=['i', 'n', 's'] out=['i', 'n', 's']
  B3: in=['s'] out=[]
interference: {'i': ['n', 's', 's2', 't'], 'i2': ['n', 's'], 'n': ['i', 'i2', 's', 's2', 't'], 's': ['i', 'i2', 'n', 't'], 's2': ['i', 'n'], 't': ['i', 'n', 's']}
moves: [('i', 'i2'), ('s', 's2')]  maxlive: 4
```

兩個設計決策值得停下來看：

1. **干擾邊只在「定義點」加**：定義 x 的那一刻，x 和當時所有活著的變數連邊。為什麼不是「任兩個同時活著的變數都連」？兩者在大多數程式上等價（一個變數活著一定是從它的定義延續過來的），但「定義點加邊」可以逐指令一次掃完，而且自然處理了「定義了但沒用到」的死定義——它也得佔一個暫存器一瞬間，不能和活著的值撞。
2. **move 例外**：`s = s2` 之後 s2 若不再用，s 和 s2 其實可以共用暫存器，那條 move 就能刪掉。若照字面加邊，s–s2 會干擾，coalescing 就永遠做不了。所以 Chaitin 規定 `x = y` 的 x **不**和 y 連邊（除非 y 之後另有干擾）。上面輸出裡 s 和 s2 不相鄰、i 和 i2 不相鄰，就是這條規則的結果。

## 底層機制：Chaitin 的 simplify / spill 與 Briggs 的樂觀著色

```text
         ┌───────────── simplify：拔 deg < k 的點，推進堆疊 ──────────────┐
         │                                                              │
  圖 ────┤ 卡住（全部 deg ≥ k）                                          ├──▶ 空圖
         │   Chaitin：挑 cost/deg 最小的點，標記 spill，從圖拔掉        │
         │   Briggs ：同樣挑，但「先推進堆疊」，標記 potential spill    │
         └──────────────────────────────────────────────────────────────┘
  select：依堆疊反序彈出，每點挑鄰居沒用的最小色
          Briggs 的 potential spill 若碰巧有色可用 → 不必 spill
          沒有色可用 → actual spill
```

**spill cost** 估計「把這個變數放記憶體要多付多少」：每次定義或使用都多一次記憶體存取，迴圈裡的乘上 `10^迴圈深度`（這個 10 是 Chaitin 以來的慣例估計，代表「迴圈大約跑 10 次」，不是測量值）。再除以度數：度數高的點拔掉後最能讓圖變好著色。

**Briggs 為什麼比較好**：Chaitin 卡住時立刻判死刑，但度數 `≥ k` 不代表著不了色——鄰居可能共用顏色。最經典的例子是 C4 配 k = 2：每個點度數 2，Chaitin 卡在第一步，但 C4 是二分圖，2 色就夠。下面的程式就造出這種圖：兩條分支各自用兩個暫時值，活躍範圍在合流處接成一個 4-圈（Ch 16 的非 chordal 例子）。

```python
# 接續上一個 block 的定義
DIAMOND = {
    "B0": (["x = 0", "y = 1", "if x < y"], ["B1", "B2"]),
    "B1": (["a = 1", "b = 2", "print a", "c = 3", "print b"], ["B3"]),
    "B2": (["a = 4", "d = 5", "print a", "c = 6", "print d"], ["B3"]),
    "B3": (["print c"], []),
}
LOOP_DEPTH = {"B1": 1, "B2": 1}         # LOOP 的 B1、B2 在迴圈裡；DIAMOND 沒有迴圈


def spill_cost(prog, depth):
    cost = {}
    for b, (code, _) in prog.items():
        for ins in code:
            d, u, _ = parse(ins)
            for x in d | u:
                cost[x] = cost.get(x, 0) + 10 ** depth.get(b, 0)
    return cost


def color(adj, k, cost, optimistic):
    work = {v: set(n) for v, n in adj.items()}
    stack, spilled, log = [], set(), []
    while work:
        low = sorted(v for v in work if len(work[v]) < k)
        if low:
            v = low[0]
            log.append(f"simplify {v}")
        else:
            v = min(work, key=lambda x: (cost[x] / len(work[x]), x))
            log.append(("potential spill " if optimistic else "spill ") + v)
            if not optimistic:
                spilled.add(v)
        for n in work.pop(v):
            work[n].discard(v)
        if v not in spilled:
            stack.append(v)
    reg = {}
    while stack:
        v = stack.pop()
        used = {reg[n] for n in adj[v] if n in reg}
        free = [c for c in range(k) if c not in used]
        if free:
            reg[v] = free[0]
        else:
            spilled.add(v)
            log.append(f"select {v}: 沒有色 → actual spill")
    return reg, spilled, log


dadj, _, dmax = build(DIAMOND)
print("DIAMOND interference:", {v: sorted(n) for v, n in sorted(dadj.items())}, "maxlive", dmax)
dcost = spill_cost(DIAMOND, {})
for name, opt in [("Chaitin", False), ("Briggs ", True)]:
    reg, spilled, log = color(dadj, 2, dcost, opt)
    print(f"{name} k=2: spill={sorted(spilled)} reg={dict(sorted(reg.items()))}")
    print("         ", " → ".join(log))

lcost = spill_cost(LOOP, LOOP_DEPTH)
print("LOOP spill cost:", lcost)
for k in (4, 3):
    reg, spilled, log = color(adj, k, lcost, True)
    print(f"Briggs LOOP k={k}: spill={sorted(spilled)} reg={dict(sorted(reg.items()))}")
    print("         ", " → ".join(log))
```

```text
DIAMOND interference: {'a': ['b', 'd'], 'b': ['a', 'c'], 'c': ['b', 'd'], 'd': ['a', 'c'], 'x': ['y'], 'y': ['x']} maxlive 2
Chaitin k=2: spill=['b'] reg={'a': 1, 'c': 1, 'd': 0, 'x': 1, 'y': 0}
          simplify x → simplify y → spill b → simplify a → simplify c → simplify d
Briggs  k=2: spill=[] reg={'a': 1, 'b': 0, 'c': 1, 'd': 0, 'x': 1, 'y': 0}
          simplify x → simplify y → potential spill b → simplify a → simplify c → simplify d
LOOP spill cost: {'i': 41, 's': 22, 'n': 11, 't': 20, 's2': 20, 'i2': 20}
Briggs LOOP k=4: spill=[] reg={'i': 2, 'i2': 0, 'n': 1, 's': 3, 's2': 0, 't': 0}
          simplify i2 → simplify s → simplify i → simplify n → simplify s2 → simplify t
Briggs LOOP k=3: spill=['n'] reg={'i': 2, 'i2': 0, 's': 1, 's2': 0, 't': 0}
          simplify i2 → simplify s2 → potential spill n → simplify i → simplify s → simplify t → select n: 沒有色 → actual spill
```

讀輸出：

- DIAMOND 上 Chaitin 一開始就卡住（a、b、c、d 度數都是 2），把 cost/度數最小的那個變數 spill 掉；Briggs 把它當 potential spill 推進堆疊，select 時發現鄰居只用了一種顏色，照樣上色，**零 spill**。
- LOOP 在 k = 4 時全部 simplify 成功（maxlive = 4，夠用）。k = 3 時卡住，n 被選中：它在迴圈裡只被用一次、定義在迴圈外，cost 最低；而且它和其他每個變數都干擾，度數最高。Briggs 樂觀地推進堆疊，但 select 時鄰居真的用滿了 3 色，變成 actual spill。

### Spill 之後：改寫程式、重建圖、重來

spill 不是「把 n 標記成在記憶體」就結束了。指令需要運算元在暫存器裡，所以要**改寫程式**：每次定義 n 之後插入 `store`，每次使用之前插入 `load` 到一個**新的、活躍範圍很短的**暫時變數。改寫後重算 liveness、重建圖、重新著色；新的暫時變數活躍範圍只有一兩條指令，幾乎不和誰干擾。

```python
# 接續上一個 block 的定義
def rewrite(prog, spilled):
    new, fresh = {}, [0]
    def tmp(v):
        fresh[0] += 1
        return f"{v}_{fresh[0]}"
    for b, (code, succ) in prog.items():
        out = []
        for ins in code:
            d, u, _ = parse(ins)
            lhs, rhs = ins.split(" = ", 1) if d else (None, ins)
            for v in sorted(u & spilled):              # 使用前：load 到新暫時變數
                t = tmp(v)
                out.append(f"{t} = load @{v}")
                rhs = re.sub(rf"\b{v}\b", t, rhs)
            store = None
            if lhs in spilled:                         # 定義後：store 回 spill slot
                lhs = tmp(lhs)
                store = f"store @{ins.split(' = ')[0]}, {lhs}"
            out.append(f"{lhs} = {rhs}" if lhs else rhs)
            if store:
                out.append(store)
        new[b] = (out, succ)
    return new


LOOP2 = rewrite(LOOP, {"n"})
for b, (code, _) in LOOP2.items():
    print(f"{b}: {code}")
adj2, moves2, maxlive2 = build(LOOP2)
reg, spilled, log = color(adj2, 3, spill_cost(LOOP2, LOOP_DEPTH), True)
print("重建後 maxlive:", maxlive2, " spill:", sorted(spilled))
print("暫存器:", {v: f"r{c}" for v, c in sorted(reg.items())})
```

```text
B0: ['i = 0', 's = 0', 'n_1 = 10', 'store @n, n_1']
B1: ['n_2 = load @n', 'if i < n_2']
B2: ['t = i * i', 's2 = s + t', 's = s2', 'i2 = i + 1', 'i = i2']
B3: ['print s']
重建後 maxlive: 3  spill: []
暫存器: {'i': 'r1', 'i2': 'r0', 'n_1': 'r0', 'n_2': 'r0', 's': 'r2', 's2': 'r0', 't': 'r0'}
```

改寫後原本的 n 拆成兩個短命的暫時變數：`n_1` 只活在定義到 `store` 之間，`n_2` 只活在 `load` 到 `if i < n_2` 之間，maxlive 降到 3，k = 3 一次著完。代價是迴圈每一圈多一次 load——這正是 spill cost 想估計的東西。真實的分配器會先考慮更便宜的替代：n 是常數 10，可以**重新計算**（rematerialization，Briggs 1994 的另一個貢獻），連 load 都省掉。

## Coalescing：把 move 刪掉

`s = s2`、`i = i2` 這種 move 在 SSA 消除（φ → copy）後大量出現。若 s 和 s2 分到同一個暫存器，move 就是 `mov r1, r1`，可以刪掉。做法是把兩個點**合併**（coalesce）成一個點，鄰居取聯集。只有在兩點**不干擾**時才能合併——這就是 move 例外存在的理由。

問題是合併會讓度數變高，可能把一張 k 可著色的圖變成不可著色。Chaitin 原本的 aggressive coalescing 能合就合，Briggs 和 George 提出兩種**保守**（conservative）測試，保證合併不會讓「simplify 能不能拔光」變差：

| 測試 | 條件（合併 a、b 成 ab） | 為什麼安全 |
| --- | --- | --- |
| **Briggs** | ab 的鄰居中，度數 `≥ k` 的少於 k 個 | simplify 時度數 `< k` 的鄰居都會先被拔掉，之後 ab 的度數 `< k`，ab 也能被拔掉 |
| **George** | a 的每個鄰居 t：要嘛已經和 b 干擾，要嘛 `deg(t) < k` | 前者合併後不增加 t 的度數；後者 t 本來就會被拔掉。所以任何能拔光原圖的順序，拔掉 a 之後仍能拔光 |

兩個測試都是**充分不必要**：拒絕了不代表合併一定有害，只是證明不了安全。George 測試只看 a 的鄰居，適合 a 是度數很高的預著色節點（physical register）的情況；Briggs 測試看合併後的鄰居。George–Appel 的 iterated register coalescing（IRC）把 simplify 和保守 coalescing 交錯執行：先拔低度數的非 move 節點，讓更多 move 通過測試。

```python
# 接續上一個 block 的定義
def merged(adj, a, b):
    g = {v: set(n) for v, n in adj.items() if v not in (a, b)}
    ab = f"{a}+{b}"
    g[ab] = (adj[a] | adj[b]) - {a, b}
    for v in g:
        if a in g[v] or b in g[v]:
            g[v] = (g[v] - {a, b}) | {ab}
    return g, ab


def briggs_ok(adj, a, b, k):
    g, ab = merged(adj, a, b)
    return sum(len(g[t]) >= k for t in g[ab]) < k


def george_ok(adj, a, b, k):
    return all(t in adj[b] or len(adj[t]) < k for t in adj[a])


def coalesce(adj, moves, k, test):
    g = {v: set(n) for v, n in adj.items()}
    alias = {}
    def find(v):
        while v in alias:
            v = alias[v]
        return v
    kept = []
    for dst, src in moves:
        a, b = find(dst), find(src)
        if a == b:
            continue
        if b not in g[a] and test(g, a, b, k):
            g, ab = merged(g, a, b)
            alias[a] = alias[b] = ab
        else:
            kept.append((dst, src))
    return g, kept


for k in (4, 3):
    for name, test in [("Briggs", briggs_ok), ("George", george_ok)]:
        g, kept = coalesce(adj, moves, k, test)
        reg, spilled, _ = color(g, k, {v: 1 for v in g}, True)
        extra = f" spill={sorted(spilled)}" if k == 4 else ""
        print(f"k={k} {name}: 留下的 move={kept} 合併後節點={sorted(g)}{extra}")

# aggressive coalescing 的反例：路徑 a - x - y - b，move a = b，k = 2
P = {"a": {"x"}, "x": {"a", "y"}, "y": {"x", "b"}, "b": {"y"}}
g, ab = merged(P, "a", "b")
print("合併前 2 色可著:", not color(P, 2, {v: 1 for v in P}, True)[1],
      " 硬合併後:", {v: sorted(n) for v, n in sorted(g.items())},
      " spill:", sorted(color(g, 2, {v: 1 for v in g}, True)[1]))
print("Briggs 允許?", briggs_ok(P, "a", "b", 2), " George 允許?", george_ok(P, "a", "b", 2))
```

```text
k=4 Briggs: 留下的 move=[] 合併後節點=['i+i2', 'n', 's+s2', 't'] spill=[]
k=4 George: 留下的 move=[] 合併後節點=['i+i2', 'n', 's+s2', 't'] spill=[]
k=3 Briggs: 留下的 move=[('i', 'i2'), ('s', 's2')] 合併後節點=['i', 'i2', 'n', 's', 's2', 't']
k=3 George: 留下的 move=[('i', 'i2'), ('s', 's2')] 合併後節點=['i', 'i2', 'n', 's', 's2', 't']
合併前 2 色可著: True  硬合併後: {'a+b': ['x', 'y'], 'x': ['a+b', 'y'], 'y': ['a+b', 'x']}  spill: ['a+b']
Briggs 允許? False  George 允許? False
```

k = 4 時兩個 move 都通過測試、被刪掉，而且著色仍然零 spill。k = 3 時兩個測試都拒絕：圖本來就要 spill（maxlive = 4），合併後的節點鄰居幾乎全是高度數點，保守測試證明不了安全，寧可留著 move。IRC 的做法是先處理 spill、讓圖變稀疏後再回頭試這些 move。最後那個反例說明保守測試在防什麼：路徑 `a-x-y-b` 是二分圖，但硬把 a、b 合併會變成三角形 `ab-x-y`，k = 2 就必須 spill；兩個測試都正確地拒絕了它。

## 對比與取捨

| 分配器 | 圖模型 | 時間 | 品質 | 代表 |
| --- | --- | --- | --- | --- |
| 區域分配 | 無 | 線性 | 跨區塊一律記憶體 | 早期編譯器、`-O0` |
| Chaitin（1981/82） | interference graph | 近 `O(n²)`，加上 spill 重來的輪數 | 卡住就 spill，偏悲觀 | IBM PL.8 |
| Chaitin–Briggs（1994） | 同上 + optimistic | 同上 | 少很多不必要的 spill | 教科書標準 |
| IRC（George–Appel 1996） | 同上 + 交錯保守 coalescing | 同上 | move 刪得多 | 《Modern Compiler Implementation》 |
| Linear scan（Poletto–Sarkar 1999） | 活躍區間 | `O(n log n)` | 略差，編譯快 | JIT（HotSpot C1、V8 等的變體） |
| SSA 上著色（Hack 2006） | chordal graph | 多項式，著色最佳 | 需處理 φ 的平行複製 | libFirm |
| LLVM greedy | 活躍區間 + 干擾查詢 + split | 依啟發式 | 生產級 | LLVM `-O2` |

**為什麼生產編譯器不全用 Chaitin–Briggs？** 三個原因：(1) 圖本身很大，建圖的時間和記憶體在大函式上很可觀；(2) spill 是「整個變數全部進記憶體」，太粗——LLVM greedy 偏好**切割活躍範圍**（live range splitting），只在壓力高的那段放記憶體；(3) 真實 ISA 有暫存器類別、固定暫存器（呼叫慣例、`div` 的輸出）、別名暫存器（`eax`/`ax`），在純圖著色模型裡要用預著色節點硬塞。

## 踩雷集錦

1. **「maxlive ≤ k 就一定不用 spill」**：在非 SSA 的圖上不成立。Ch 16 的 C5 迴圈 maxlive = 2 卻要 3 色。只有在 chordal（例如 SSA）的圖上 `χ = maxlive`。
2. **「度數 ≥ k 就一定要 spill」**：這是 Chaitin 的悲觀。DIAMOND 上四個點度數都是 k，但 2 色著得完。Briggs 的樂觀著色就是在修這個。
3. **忘了 move 例外，或例外用錯方向**：`x = y` 時只排除 x–y 這一條邊。如果之後 y 還被用（y 仍活著）且 x 被重新定義，干擾要在那個定義點另外加——我們的「定義點加邊」規則會自動處理，因為例外只套用在 move 指令本身。
4. **aggressive coalescing**：合併兩個不干擾的點看似永遠划算（少一條 move），但會讓度數暴增，把可著色的圖變得要 spill。spill 一個變數的成本遠高於留一條 move，所以保守測試寧可錯殺。
5. **spill 完不重跑**：spill 改寫會產生新的暫時變數與新的干擾，原圖已經不是現在程式的圖。Chaitin 的流程是「改寫 → 整個重來」，通常一兩輪就收斂；只標記不重跑，最後產生的程式可能用到超過 k 個暫存器。

## 進階：再往深一層

- **預著色節點與呼叫慣例**：把實體暫存器也當成圖上的點（彼此兩兩干擾），跨呼叫活著的變數和所有 caller-saved 暫存器干擾，就會被逼進 callee-saved 暫存器。這是 IRC 處理呼叫的方式，也是 George 測試適合預著色節點的原因。
- **Spill 決策的難度**：給定 k，最少 spill 成本的選擇即使在 SSA（chordal）上也是 NP-hard（Bouchez–Darte–Rastello 研究了 spill everywhere 等多種變體）；coalescing 的多種版本也都 NP-complete（同一組作者 CGO 2007）。啟發式不是偷懶，是必然。
- **Live range splitting**：與其整個 spill，不如在壓力高的區域把活躍範圍切成兩段，各自分配。這等價於在圖上插入 move，再交給 coalescing 決定要不要合回去。LLVM greedy 的核心就是這個（見 compiler_backend Ch 12）。
- **形式化的 lower bound**：線上評估分配器品質常用整數線性規劃（ILP）求最佳解當基準，例如 Appel–George 的 optimal spilling。這也是 Ch 26 LP 對偶的應用場景之一。

## 動手練習

1. 把 DIAMOND 的 k 改成 1，預測 Chaitin 與 Briggs 的 spill 集合，再實跑驗證。
2. 移除 `build` 裡的 move 例外（`others = live`），重跑 coalescing，觀察為什麼所有 move 都被留下。
3. 實作 rematerialization：spill 一個「定義是常數」的變數時，在使用前插入 `n_1 = 10` 而不是 load，比較改寫後的程式。
4. 在 LOOP 裡多加一個跨迴圈活著的變數，讓 maxlive 變成 5，觀察 k = 3 時需要幾輪「spill → 改寫 → 重來」才收斂。

## 本章重點整理

- liveness 是反向資料流：`in = use ∪ (out − def)`，`out = ∪ in(succ)`，迭代到不動點。
- interference 在定義點加邊；move 例外讓 move 兩端可以合併。
- simplify 的正確性來自「度數 `< k` 的點一定著得上色」；卡住時 Chaitin 直接 spill，Briggs 樂觀推進堆疊、到 select 才判定。
- spill 要改寫程式並整個重來；新暫時變數的活躍範圍很短。
- 保守 coalescing：Briggs 看合併後高度數鄰居數 `< k`，George 看 a 的每個鄰居「已和 b 干擾或度數 `< k`」；兩者都保證不讓 simplify 變差。

## 自我檢核

- [ ] 不看筆記，寫出 liveness 的兩條方程，並解釋為什麼迭代一定會停。
- [ ] 用一張你自己畫的圖說明 Chaitin 會 spill、Briggs 不會的情況，並說明 Briggs 的 select 階段什麼時候還是會 actual spill。
- [ ] 證明 George 測試的安全性：為什麼「a 的鄰居都已和 b 干擾或度數 `< k`」能保證合併後 simplify 仍拔得光？
- [ ] 同事說「我們乾脆把所有 move 都合併，反正少一條指令」，你會用哪個最小反例說服他？
- [ ] 解釋為什麼 SSA 上著色是最佳的，但生產編譯器仍需要 coalescing 和 spill 啟發式。

## 延伸閱讀

### 論文

- **[Register Allocation & Spilling via Graph Coloring](https://web.eecs.umich.edu/~mahlke/courses/583f18/reading/chaitin82.pdf)** — G. J. Chaitin，SIGPLAN Symposium on Compiler Construction，SIGPLAN Notices 17(6)（1982）
  - **核心貢獻**：把 spill 決策整合進著色流程（simplify 卡住就挑 spill、改寫、重來），是本章 Chaitin 演算法的原型。
  - **讀哪裡**：全文八頁；spill cost 與「卡住時挑誰」的段落最重要。
  - **前提**：本章 liveness 與 interference 的定義。
- **[Improvements to Graph Coloring Register Allocation](https://www.rw.cdl.uni-saarland.de/people/phlucas/private/Lehre/reg.pdf)** — P. Briggs、K. D. Cooper、L. Torczon，ACM TOPLAS 16(3)（1994）
  - **核心貢獻**：optimistic coloring（延後 spill 決策到 select）與 rematerialization；附 FORTRAN 程式的實測。
  - **讀哪裡**：optimistic coloring 一節與實驗結果；rematerialization 可第二輪再讀。
  - **和本章的關聯**：本章 `color(..., optimistic=True)` 與 Briggs coalescing 測試的出處。
- **[Iterated Register Coalescing](https://www.cs.purdue.edu/homes/hosking/502/george.pdf)** — L. George、A. W. Appel，ACM TOPLAS 18(3)（1996）
  - **核心貢獻**：把 simplify、保守 coalescing、freeze、spill 交錯成一個 worklist 演算法，提出 George 測試處理預著色節點。
  - **讀哪裡**：第 3–4 節的演算法與 worklist 狀態圖；附錄的虛擬碼可以直接拿來實作。
- **[Linear Scan Register Allocation](https://www.cse.iitm.ac.in/~krishna/cs3300/linearscan.pdf)** — M. Poletto、V. Sarkar，ACM TOPLAS 21(5)（1999）
  - **這篇說什麼**：放棄 interference graph，改在活躍區間上單次掃描；編譯速度快很多，品質略差。
  - **和本章的關聯**：理解「為什麼 JIT 不用圖著色」；區間圖是 chordal（Ch 16）。
- **[On the Complexity of Register Coalescing](https://hal-lara.archives-ouvertes.fr/hal-02102282)** — F. Bouchez、A. Darte、F. Rastello，CGO 2007（技術報告版 LIP RR2006-15）
  - **核心貢獻**：系統化證明 aggressive、conservative、optimistic 等 coalescing 變體的 NP-completeness，包括在 chordal 圖上。
  - **讀哪裡**：引言與結果總表；證明細節選讀。

### 書籍

- **《Modern Compiler Implementation in ML / Java / C》** — Andrew W. Appel（Cambridge University Press，1998；Java 版第 2 版 2002 與 Jens Palsberg 合著）
  - **讀哪幾章**：第 10 章 Liveness Analysis、第 11 章 Register Allocation（IRC 的完整虛擬碼）。
  - **定位**：練習 C 的 toy allocator 可以照這兩章擴充成 IRC。
- **《Engineering a Compiler》** — Keith D. Cooper、Linda Torczon（Morgan Kaufmann，第 2 版 2011）
  - **讀哪幾章**：Register Allocation 一章（第 2 版第 13 章），對 Chaitin vs Briggs、spill cost、coalescing 的工程細節寫得最完整。

演算法已經齊了，接下來換到 Part 3 的另一個主題：平面圖。它和著色的交會點是「平面圖要幾色」——那會在 Ch 19 收束。

→ [Ch 18 平面圖：Euler 公式與 Kuratowski](./18-planar-graphs.md)
