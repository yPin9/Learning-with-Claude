# 練習 C — Toy register allocator

> **目標**：把 Ch 15–17 拼起來，寫一個完整的 toy register allocator：輸入一段 three-address 線性 IR 與暫存器數 k，算逐指令的 liveness、建 interference graph、用 Chaitin–Briggs 做 k 著色，著不了色就 spill、改寫程式、重來，最後輸出只用 `r0 .. r(k-1)` 的程式，並用直譯器證明改寫前後行為相同。
>
> **環境**：Python 3.13.12，Windows 11。只用標準函式庫（`re`、`heapq`、`dataclasses`）。

## 背景與動機

Ch 17 的分配器跑在手寫的區塊字典上，而且 spill 改寫只示範了一個變數。真正的後端要面對的是：從文字 IR 解析、label 與跳躍組成的控制流、同一條指令既用又定義同一個變數（`i = i + 1`）、spill 之後新的暫時變數也要分配、暫時變數本身不能再被 spill（否則永遠不收斂），以及「怎麼知道我的分配器沒有把程式改壞」。

這個練習是一個縮小版的編譯器後端暫存器分配階段：輸入相當於 instruction selection 之後、還在用虛擬暫存器的 machine IR，輸出是實體暫存器的版本。驗收標準不是「看起來對」，而是**差分測試**：原程式與分配後的程式在同一個直譯器上跑出完全相同的輸出。

> 需要複習的地方：liveness 方程與 move 例外在 [Ch 17 — Register allocation：Chaitin–Briggs](./17-register-allocation.md)；為什麼 maxlive ≤ k 仍可能要 spill，見 [Ch 16 — Chordal graph 與完美消去序](./16-chordal-graphs.md) 的 C5 迴圈；貪婪著色與 `χ` vs `ω` 在 [Ch 15](./15-graph-coloring.md)。工程版對照：[compiler_backend Ch 12 — Register allocator](../../compilers/compiler_backend/12-register-allocator.md)。

## 任務規格

### 輸入語言

一行一條指令，`#` 之後是註解，空行忽略。變數與 label 名稱是 `[a-z_][a-z0-9_]*`，整數可為負。

| 形式 | 意義 | defs | uses |
| --- | --- | --- | --- |
| `L:` | label | — | — |
| `x = 5` / `x = y` | 常數 / 複製（後者是 **move**） | x | — / y |
| `x = a op b`，`op ∈ + - * / <` | 二元運算；`<` 回傳 0/1，`/` 向零取整 | x | a、b 中的變數 |
| `if a < b goto L` | 條件跳躍，不成立就往下 | — | a、b 中的變數 |
| `goto L` | 無條件跳躍 | — | — |
| `print a` | 輸出 | — | a |
| `ret a` | 輸出並結束 | — | a |
| `x = load [s]` / `store [s], a` | spill slot 存取（輸入可以沒有，輸出會有） | x / — | — / a |

### 要實作的 API

```python
# norun：介面規格，不是可執行程式
def allocate(src: str, k: int) -> Result: ...
#   Result.code          list[str]：分配後的程式，只能出現 r0..r(k-1) 與 spill slot
#   Result.reg           dict[str, int]：最後一輪每個（含暫時）變數的暫存器編號
#   Result.spilled       set[str]：被 spill 的原始變數（也就是 spill slot 的名字）
#   Result.rounds        int：spill → 改寫 → 重來的輪數（第一次就成功是 1）
#   Result.removed_moves int：分配後變成 rX = rX 而被刪掉的 move 數
def run(src: str) -> list[int]: ...
#   直譯器：執行程式，回傳 print / ret 的輸出序列；原程式與分配後程式都要能跑
```

### 驗收標準

1. 對每個合法輸入與每個 `k ≥ 2`：`run(src) == run("\n".join(allocate(src, k).code))`。
2. 輸出程式裡的暫存器名稱全部是 `r0 .. r(k-1)`。
3. interference graph 能被 simplify 直接拔光（或 Briggs 樂觀著色成功）時，不得 spill（`rounds == 1`）。
4. 非法輸入丟 `ValueError`，訊息指出原因（與行號，若適用）：
   - `k < 2`
   - 語法錯誤、把關鍵字（`if goto print ret load store`）當變數
   - 跳到未定義的 label、重複的 label
   - 某個變數在入口處就活著（存在一條路徑使用它卻沒先定義）
5. 效能：60 個變數、約 1000 條運算、`k = 6` 的隨機程式，分配應在 1 秒內完成。參考解答在作者機器（Windows 11、Python 3.13.12）上約 0.2 秒；這個數字只代表量級。

**限制**：不准用 NetworkX 或任何圖論套件，圖與著色全部自刻。

## 期望輸出範例

Ch 16 的 C5 迴圈，maxlive 只有 2，但 interference graph 是 5-圈，`k = 2` 必須 spill：

```text
輸入（k = 2）：
a = 0
b = 1
loop:
c = a + 1
d = b + 1
e = c + 1
a = d + 1
b = e + 1
if b < 100 goto loop
print a
print b

輸出（參考解答）：
r1 = 0
r0 = 1
loop:
r1 = r1 + 1
store [c], r1
r1 = r0 + 1
r0 = load [c]
r0 = r0 + 1
r1 = r1 + 1
r0 = r0 + 1
if r0 < 100 goto loop
print r1
print r0
# rounds=2 spilled=['c']；原程式與輸出程式都印出 [100, 101]
```

你的暫存器編號與 spill 對象可以不同（取決於平手規則與 spill cost），但必須通過驗收標準 1、2。

```text
邊界輸入：任意程式，k = 1
輸出：ValueError: k 至少要 2：x = a + b 在 a、b 都 spill 時需要兩個暫存器

邊界輸入：a = b + 1 / print a
輸出：ValueError: 使用前未定義（入口處活著）: ['b']
```

## 如果你卡住了

1. **liveness 的粒度**：Ch 17 是區塊層級再往回掃；這裡直接做**逐指令**的資料流最省事：每條指令的後繼是「下一條」「跳躍目標」或兩者都有（`if`），`ret` 與 `goto` 不會落到下一條。先把 `successors` 寫對、印出來看。
2. **`i = i + 1` 被 spill 時**：use 和 def 要用**兩個不同**的暫時變數：`i_1 = load [i]`、`i_2 = i_1 + 1`、`store [i], i_2`。若共用同一個名字，load 進來的值會被誤認為一路活到下一次定義，活躍範圍被拉長，最後在 k = 2 時出現「暫時變數著不了色」。參考解答的第一版就在這裡出錯過。
3. **暫時變數不能再 spill**：spill 一個 load 進來的暫時變數，只會產生另一個一模一樣的暫時變數，永遠不收斂。選 spill 候選時排除它們；如果 select 階段某個暫時變數還是著不了色，改 spill 擋住它的那些**原始**變數。
4. **為什麼 k ≥ 2 一定收斂？** 最壞情況所有原始變數都被 spill，每條指令附近只剩自己的暫時變數：`x = a + b` 需要 a、b 的暫時變數同時活著（2 個），結果的暫時變數可以重用其中一個的暫存器。想清楚這點，就知道 k = 1 為什麼要拒絕。
5. **怎麼確定沒改壞**：寫好 `run` 之後，每個測試都比較 `run(src)` 與 `run(分配後)`，再加一個隨機程式產生器大量對拍。隨機程式別用乘法：迴圈裡反覆相乘會讓整數長成上萬位數，直譯器慢到像當掉——這不是分配器的 bug，但會浪費你一個下午。

## 實作步驟建議

### Step 1：解析器與直譯器

用一張「正規表示式 → 指令種類」的表解析每一行，產生 `Ins(kind, args, line)`，並為每種指令寫 `defs()`、`uses()`、`is_move()`、`text()`。同時寫 `run`。**先寫直譯器**：之後每一步都靠它驗證。子目標：C5 迴圈的 `run` 輸出 `[100, 101]`。

### Step 2：控制流與逐指令 liveness

`successors(prog, labels)` 回傳每條指令的後繼索引；`liveness` 迭代 `in = use ∪ (out − def)`、`out = ∪ in(succ)` 到不動點。子目標：對 Ch 17 的 sum 迴圈印出每條指令的 live-out，手動核對迴圈內 `i`、`s`、`n` 一直活著；並用 `live_in[0]` 非空偵測「使用前未定義」。

### Step 3：interference graph

每條定義 x 的指令，x 和該指令的 live-out（扣掉自己；move 時再扣掉來源）連邊。子目標：C5 迴圈得到恰好 5 條邊的圈；sum 迴圈的 `s`–`s2`、`i`–`i2` 不相鄰。

### Step 4：Chaitin–Briggs 著色

simplify 用 worklist（度數 `< k` 的點），卡住時挑 `cost / degree` 最小的可 spill 點樂觀推進堆疊；select 依堆疊反序挑最小可用色，沒有就記為 actual spill。spill cost 用 `Σ 10^迴圈深度`，迴圈深度可以用「被幾個回邊區間包住」粗估（回邊 = 跳到自己前面的跳躍）。子目標：Ch 17 的 DIAMOND 在 k = 2 零 spill。

### Step 5：spill 改寫與重來

對每個 spilled 變數，在每次 use 前插入 `t = load [v]`、每次 def 後插入 `store [v], t`，t 是新的、不可 spill 的名字。重新算 liveness、建圖、著色，直到沒有 spill。子目標：C5 迴圈 k = 2 在第 2 輪成功。

### Step 6：輸出、刪 move、差分測試

把每個變數換成 `r{色}`，刪掉變成 `rX = rX` 的 move。跑「測試用例」一節的套件與隨機對拍。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<!-- markdownlint-disable MD033 -->
<details>
<summary>點開參考實作（regalloc.py）</summary>

```python
"""Toy register allocator：liveness → interference → Chaitin–Briggs → spill 改寫 → 重來。"""
import heapq
import re
from dataclasses import dataclass

NAME = r"[a-z_][a-z0-9_]*"
ATOM = rf"(?:{NAME}|-?\d+)"
KEYWORDS = {"if", "goto", "print", "ret", "load", "store"}
PATTERNS = [
    ("label", re.compile(rf"({NAME}):")),
    ("binop", re.compile(rf"({NAME}) = ({ATOM}) ([-+*/<]) ({ATOM})")),
    ("load",  re.compile(rf"({NAME}) = load \[({NAME})\]")),
    ("copy",  re.compile(rf"({NAME}) = ({ATOM})")),
    ("store", re.compile(rf"store \[({NAME})\], ({ATOM})")),
    ("if",    re.compile(rf"if ({ATOM}) < ({ATOM}) goto ({NAME})")),
    ("goto",  re.compile(rf"goto ({NAME})")),
    ("print", re.compile(rf"print ({ATOM})")),
    ("ret",   re.compile(rf"ret ({ATOM})")),
]


@dataclass
class Ins:
    kind: str
    args: tuple
    line: int

    def defs(self):
        return {self.args[0]} if self.kind in ("binop", "load", "copy") else set()

    def uses(self):
        if self.kind == "binop":
            cand = self.args[1:2] + self.args[3:4]
        elif self.kind in ("copy",):
            cand = self.args[1:2]
        elif self.kind == "store":
            cand = self.args[1:2]
        elif self.kind == "if":
            cand = self.args[:2]
        elif self.kind in ("print", "ret"):
            cand = self.args[:1]
        else:
            cand = ()
        return {a for a in cand if re.fullmatch(NAME, a)}

    def is_move(self):
        return self.kind == "copy" and re.fullmatch(NAME, self.args[1]) is not None

    def text(self, ren=lambda v: v):
        r = lambda a: ren(a) if re.fullmatch(NAME, a) else a
        a = self.args
        return {
            "label": lambda: f"{a[0]}:",
            "binop": lambda: f"{r(a[0])} = {r(a[1])} {a[2]} {r(a[3])}",
            "load":  lambda: f"{r(a[0])} = load [{a[1]}]",
            "copy":  lambda: f"{r(a[0])} = {r(a[1])}",
            "store": lambda: f"store [{a[0]}], {r(a[1])}",
            "if":    lambda: f"if {r(a[0])} < {r(a[1])} goto {a[2]}",
            "goto":  lambda: f"goto {a[0]}",
            "print": lambda: f"print {r(a[0])}",
            "ret":   lambda: f"ret {r(a[0])}",
        }[self.kind]()


def parse(src):
    prog = []
    for n, raw in enumerate(src.splitlines(), 1):
        line = raw.split("#")[0].strip()
        if not line:
            continue
        for kind, pat in PATTERNS:
            m = pat.fullmatch(line)
            if m:
                ins = Ins(kind, m.groups(), n)
                bad = {x for x in ins.defs() | ins.uses() if x in KEYWORDS}
                if bad:
                    raise ValueError(f"line {n}: 關鍵字不能當變數: {sorted(bad)}")
                prog.append(ins)
                break
        else:
            raise ValueError(f"line {n}: 語法錯誤: {raw.strip()!r}")
    labels = {}
    for i, ins in enumerate(prog):
        if ins.kind == "label":
            if ins.args[0] in labels:
                raise ValueError(f"line {ins.line}: 重複的 label {ins.args[0]}")
            labels[ins.args[0]] = i
    for ins in prog:
        target = ins.args[-1] if ins.kind in ("if", "goto") else None
        if target is not None and target not in labels:
            raise ValueError(f"line {ins.line}: 未定義的 label {target}")
    return prog, labels


def successors(prog, labels):
    succ = []
    for i, ins in enumerate(prog):
        nxt = [i + 1] if i + 1 < len(prog) else []
        if ins.kind == "goto":
            succ.append([labels[ins.args[0]]])
        elif ins.kind == "if":
            succ.append(sorted(set(nxt + [labels[ins.args[2]]])))
        elif ins.kind == "ret":
            succ.append([])
        else:
            succ.append(nxt)
    return succ


def liveness(prog, succ):
    """逐指令的反向資料流：in = use ∪ (out − def)。"""
    lin = [set() for _ in prog]
    lout = [set() for _ in prog]
    changed = True
    while changed:
        changed = False
        for i in reversed(range(len(prog))):
            out = set().union(*(lin[s] for s in succ[i])) if succ[i] else set()
            inn = prog[i].uses() | (out - prog[i].defs())
            if out != lout[i] or inn != lin[i]:
                lout[i], lin[i], changed = out, inn, True
    return lin, lout


def interference(prog, lout):
    adj = {}
    for ins in prog:
        for v in ins.defs() | ins.uses():
            adj.setdefault(v, set())
    for i, ins in enumerate(prog):
        skip = set(ins.args[1:2]) if ins.is_move() else set()   # move 例外
        for d in ins.defs():
            for v in lout[i] - skip - {d}:
                adj[d].add(v)
                adj[v].add(d)
    return adj


def spill_costs(prog, labels, succ):
    """cost = Σ 10^depth；depth 用「被幾個回邊區間包住」估計（回邊：跳到前面的 label）。"""
    depth = [0] * len(prog)
    for i, ins in enumerate(prog):
        for s in succ[i]:
            if s <= i:                                         # 回邊 s..i 是一個迴圈
                for j in range(s, i + 1):
                    depth[j] += 1
    cost = {}
    for i, ins in enumerate(prog):
        for v in ins.defs() | ins.uses():
            cost[v] = cost.get(v, 0) + 10 ** depth[i]
    return cost


def color(adj, k, cost, unspillable):
    work = {v: set(n) for v, n in adj.items()}
    low = [v for v in work if len(work[v]) < k]     # simplify worklist（heap：每次取名字最小的，結果可重現）
    heapq.heapify(low)
    stack = []
    while work:
        while low and (low[0] not in work):         # 惰性刪除：已拔掉的點留在 heap 裡，取到時丟掉
            heapq.heappop(low)
        if low:
            v = heapq.heappop(low)
        else:
            cands = [v for v in work if v not in unspillable] or list(work)
            v = min(cands, key=lambda x: (cost.get(x, 0) / len(work[x]), x))   # Briggs：先推，select 再說
        for n in work.pop(v):
            work[n].discard(v)
            if len(work[n]) == k - 1:               # 剛好降到 k-1：變成可以 simplify
                heapq.heappush(low, n)
        stack.append(v)
    reg, spilled = {}, set()
    while stack:
        v = stack.pop()
        used = {reg[n] for n in adj[v] if n in reg}
        free = [c for c in range(k) if c not in used]
        if free:
            reg[v] = free[0]
        else:
            spilled.add(v)
    return reg, spilled


def rewrite(prog, spilled, fresh, unspillable):
    out = []
    for ins in prog:
        use_ren, def_ren = {}, {}           # 分開記：x = x + 1 的 use 和 def 要用不同的暫時變數
        pre, post = [], []
        for v in sorted(ins.uses() & spilled):
            use_ren[v] = t = f"{v}_{next(fresh)}"
            unspillable.add(t)
            pre.append(Ins("load", (t, v), ins.line))
        for v in sorted(ins.defs() & spilled):
            def_ren[v] = t = f"{v}_{next(fresh)}"
            unspillable.add(t)
            post.append(Ins("store", (v, t), ins.line))
        a = list(ins.args)
        U = lambda x: use_ren.get(x, x)
        if ins.kind == "binop":             # 依位置改名，不會誤改 label 或 spill slot 名稱
            a = [def_ren.get(a[0], a[0]), U(a[1]), a[2], U(a[3])]
        elif ins.kind == "copy":
            a = [def_ren.get(a[0], a[0]), U(a[1])]
        elif ins.kind == "load":
            a = [def_ren.get(a[0], a[0]), a[1]]
        elif ins.kind == "store":
            a = [a[0], U(a[1])]
        elif ins.kind == "if":
            a = [U(a[0]), U(a[1]), a[2]]
        elif ins.kind in ("print", "ret"):
            a = [U(a[0])]
        out += pre + [Ins(ins.kind, tuple(a), ins.line)] + post
    return out


@dataclass
class Result:
    code: list
    reg: dict
    spilled: set
    rounds: int
    removed_moves: int


def allocate(src, k, max_rounds=20):
    if k < 2:
        raise ValueError("k 至少要 2：x = a + b 在 a、b 都 spill 時需要兩個暫存器")
    prog, labels = parse(src)
    succ = successors(prog, labels)
    lin, _ = liveness(prog, succ)
    if prog and lin[0]:
        raise ValueError(f"使用前未定義（入口處活著）: {sorted(lin[0])}")
    all_spilled, unspillable = set(), set()
    fresh = iter(range(1, 10**9))
    for rounds in range(1, max_rounds + 1):
        labels = {ins.args[0]: i for i, ins in enumerate(prog) if ins.kind == "label"}
        succ = successors(prog, labels)
        _, lout = liveness(prog, succ)
        adj = interference(prog, lout)
        reg, spilled = color(adj, k, spill_costs(prog, labels, succ), unspillable)
        if not spilled:
            break
        stuck = spilled & unspillable
        if stuck:
            # 暫時變數不能再 spill（會無限循環）：改 spill 擋住它的那些原始變數
            spilled = (spilled - unspillable) | {n for t in stuck for n in adj[t] if n not in unspillable}
            if not spilled:
                raise RuntimeError(f"無法著色的暫時變數: {sorted(stuck)}")
        all_spilled |= spilled
        prog = rewrite(prog, spilled, fresh, unspillable)
    else:
        raise RuntimeError("spill 沒有收斂")
    code, removed = [], 0
    for ins in prog:
        if ins.is_move() and reg[ins.args[0]] == reg[ins.args[1]]:
            removed += 1                                         # r1 = r1：刪掉
            continue
        code.append(ins.text(lambda v: f"r{reg[v]}"))
    return Result(code, reg, all_spilled, rounds, removed)


def run(src, max_steps=100_000):
    """直譯器：原始程式與分配後的程式都能跑，回傳 print/ret 的輸出序列。"""
    prog, labels = parse(src)
    env, mem, out, pc, steps = {}, {}, [], 0, 0
    val = lambda a: env[a] if re.fullmatch(NAME, a) else int(a)
    ops = {"+": lambda x, y: x + y, "-": lambda x, y: x - y, "*": lambda x, y: x * y,
           "/": lambda x, y: int(x / y), "<": lambda x, y: int(x < y)}
    while pc < len(prog):
        steps += 1
        if steps > max_steps:
            raise RuntimeError("超過步數上限")
        ins, a = prog[pc], prog[pc].args
        pc += 1
        if ins.kind == "binop":
            env[a[0]] = ops[a[2]](val(a[1]), val(a[3]))
        elif ins.kind == "copy":
            env[a[0]] = val(a[1])
        elif ins.kind == "load":
            env[a[0]] = mem[a[1]]
        elif ins.kind == "store":
            mem[a[0]] = val(a[1])
        elif ins.kind == "if":
            if val(a[0]) < val(a[1]):
                pc = labels[a[2]]
        elif ins.kind == "goto":
            pc = labels[a[0]]
        elif ins.kind == "print":
            out.append(val(a[0]))
        elif ins.kind == "ret":
            out.append(val(a[0]))
            break
    return out
```

**解答說明**：

- **逐指令而非逐區塊**：程式是線性 IR，基本區塊要另外切；逐指令的資料流多花一點時間，但程式碼短很多，也不會有「區塊內 use/def 順序」的 bug。
- **`use_ren` 與 `def_ren` 分開**：處理 `i = i + 1` 這種既用又定義的指令；改名依指令種類**依位置**進行，避免把 label 或 spill slot 名稱誤改。
- **暫時變數的安全網**：選 spill 候選時排除暫時變數；萬一 select 階段暫時變數仍著不了色，就改 spill 它的原始變數鄰居。每輪至少多 spill 一個原始變數，而原始變數有限，所以一定收斂（`max_rounds` 只是防呆）。
- **simplify worklist 用 heap**：第一版每一步都把全部節點 `sorted` 一次找度數 `< k` 的點，是平方時間，60 變數 / 1000 指令的程式 profile 出來八成時間花在這裡；改成「度數剛降到 k−1 時推進 heap」後是 `O((V + E) log V)`。平手時取名字最小的點，結果可重現。
- **沒做 coalescing**：move 只在兩端碰巧同色時刪掉（`removed_moves`）。保守 coalescing 留給延伸挑戰。

</details>
<!-- markdownlint-enable MD033 -->

## 測試用例

| 輸入 | k | 預期 | 說明 |
| --- | --- | --- | --- |
| `straight`（4 條直線運算） | 2–4 | 不 spill，輸出 `[6]` | 正常路徑，maxlive 2 |
| `pressure3`（a、b、c 同時活著） | 2 | 需要 spill，輸出仍是 `[6]` | maxlive 3 > k |
| `pressure3` | 3 | 不 spill | 剛好夠 |
| `diamond`（Ch 17 的 C4） | 2 | **不** spill | Chaitin 會 spill，Briggs 不會 |
| `c5loop`（Ch 16 的 C5） | 2 | 需要 spill | maxlive 2 但 `χ = 3` |
| `c5loop` | 3 | 不 spill | |
| `sum`（含兩條 move） | 4 | 不 spill，move 被刪掉或保留皆可 | 迴圈、spill cost |
| `sum` | 2 | 多輪 spill，輸出 `[285]` | 收斂性 |
| `self_update`（`i = i + 1`、`j = j + i`，三個變數跨迴圈活著） | 2 | 需要 spill，輸出 `[45, 9]` | 卡點 2 的情況 |
| `a = b + 1` | 3 | `ValueError`（使用前未定義） | 非法輸入 |
| `goto nowhere` | 3 | `ValueError`（未定義 label） | 非法輸入 |
| 任意 | 1 | `ValueError` | k 太小 |
| `a = = 1`、`if = 1`、重複 label | 3 | `ValueError` | 語法 / 關鍵字 / label |
| 200 個隨機程式 × k ∈ {2, 3, 5} | — | 全部輸出相同、暫存器編號 `< k` | 差分測試 |
| 60 變數、約 1000 運算 | 6 | 1 秒內 | 效能邊界 |

把下面的測試套件接在你的實作後面跑（它也是參考解答的實際輸出）：

```python
# 接續上一個 block 的定義
import random
import time

CASES = {
    "straight": "a = 1\nb = 2\nc = a + b\nd = c * 2\nprint d",
    "pressure3": "a = 1\nb = 2\nc = 3\nd = a + b\ne = d + c\nprint e",
    "diamond": "x = 0\nif x < 1 goto right\na = 1\nb = 2\nprint a\nc = 3\nprint b\ngoto join\n"
               "right:\na = 4\nd = 5\nprint a\nc = 6\nprint d\njoin:\nprint c",
    "c5loop": "a = 0\nb = 1\nloop:\nc = a + 1\nd = b + 1\ne = c + 1\na = d + 1\nb = e + 1\n"
              "if b < 100 goto loop\nprint a\nprint b",
    "sum": "i = 0\ns = 0\nn = 10\nloop:\nt = i * i\ns2 = s + t\ns = s2\ni2 = i + 1\ni = i2\n"
           "if i < n goto loop\nret s",
    "self_update": "i = 0\nj = 0\nm = 9\ntop:\ni = i + 1\nj = j + i\nif i < m goto top\nprint j\nret i",
}
for name, src in CASES.items():
    for k in (2, 3, 4):
        r = allocate(src, k)
        same = run(src) == run("\n".join(r.code))
        print(f"{name:11s} k={k} rounds={r.rounds} spilled={sorted(r.spilled)} "
              f"removed_moves={r.removed_moves} 輸出相同={same} {run(src)}")

for bad_src, k in [("a = b + 1\nprint a", 3), ("goto nowhere", 3), ("a = 1\nprint a", 1),
                   ("a = = 1", 3), ("if = 1", 3), ("top:\ntop:\nret 0", 3)]:
    try:
        allocate(bad_src, k)
        print("沒有報錯（錯！）:", bad_src)
    except ValueError as e:
        print("ValueError:", e)

rng = random.Random(7)


def gen(nv, ni):
    """隨機程式：nv 個變數、ni 條運算、外層迴圈 3 圈；只用 + -，避免整數爆炸。"""
    vs = [f"v{i}" for i in range(nv)]
    lines = [f"{v} = {rng.randint(0, 9)}" for v in vs] + ["i = 0", "top:"]
    for _ in range(ni):
        d, a, b = rng.choice(vs), rng.choice(vs), rng.choice(vs)
        lines.append(f"{d} = {a} {rng.choice('+-')} {b}")
        if rng.random() < 0.1:
            lines.append(f"{d} = {d} - {a}")
    lines += ["i = i + 1", "if i < 3 goto top"] + [f"print {v}" for v in vs]
    return "\n".join(lines)


failures = 0
for seed in range(200):
    rng.seed(seed)
    src = gen(rng.randint(2, 12), rng.randint(1, 40))
    for k in (2, 3, 5):
        r = allocate(src, k)
        failures += run(src) != run("\n".join(r.code))
        failures += any(int(x[1:]) >= k for line in r.code for x in re.findall(r"\br\d+\b", line))
print("隨機對拍 200 個程式 × k∈{2,3,5}：失敗次數 =", failures)

rng.seed(1)
big = gen(60, 1000)
t = time.perf_counter()
r = allocate(big, 6)
elapsed = time.perf_counter() - t
print(f"效能：{len(big.splitlines())} 行、k=6 → rounds={r.rounds}，spill {len(r.spilled)} 個，"
      f"輸出相同={run(big) == run(chr(10).join(r.code))}，1 秒內={elapsed < 1}")
```

```text
straight    k=2 rounds=1 spilled=[] removed_moves=0 輸出相同=True [6]
straight    k=3 rounds=1 spilled=[] removed_moves=0 輸出相同=True [6]
straight    k=4 rounds=1 spilled=[] removed_moves=0 輸出相同=True [6]
pressure3   k=2 rounds=4 spilled=['a', 'b', 'c'] removed_moves=0 輸出相同=True [6]
pressure3   k=3 rounds=1 spilled=[] removed_moves=0 輸出相同=True [6]
pressure3   k=4 rounds=1 spilled=[] removed_moves=0 輸出相同=True [6]
diamond     k=2 rounds=1 spilled=[] removed_moves=0 輸出相同=True [4, 5, 6]
diamond     k=3 rounds=1 spilled=[] removed_moves=0 輸出相同=True [4, 5, 6]
diamond     k=4 rounds=1 spilled=[] removed_moves=0 輸出相同=True [4, 5, 6]
c5loop      k=2 rounds=2 spilled=['c'] removed_moves=0 輸出相同=True [100, 101]
c5loop      k=3 rounds=1 spilled=[] removed_moves=0 輸出相同=True [100, 101]
c5loop      k=4 rounds=1 spilled=[] removed_moves=0 輸出相同=True [100, 101]
sum         k=2 rounds=4 spilled=['i', 'n', 's', 't'] removed_moves=2 輸出相同=True [285]
sum         k=3 rounds=2 spilled=['n'] removed_moves=0 輸出相同=True [285]
sum         k=4 rounds=1 spilled=[] removed_moves=0 輸出相同=True [285]
self_update k=2 rounds=3 spilled=['j', 'm'] removed_moves=0 輸出相同=True [45, 9]
self_update k=3 rounds=1 spilled=[] removed_moves=0 輸出相同=True [45, 9]
self_update k=4 rounds=1 spilled=[] removed_moves=0 輸出相同=True [45, 9]
ValueError: 使用前未定義（入口處活著）: ['b']
ValueError: line 1: 未定義的 label nowhere
ValueError: k 至少要 2：x = a + b 在 a、b 都 spill 時需要兩個暫存器
ValueError: line 1: 語法錯誤: 'a = = 1'
ValueError: line 1: 關鍵字不能當變數: ['if']
ValueError: line 2: 重複的 label top
隨機對拍 200 個程式 × k∈{2,3,5}：失敗次數 = 0
效能：1244 行、k=6 → rounds=3，spill 57 個，輸出相同=True，1 秒內=True
```

讀輸出時注意三件事：`diamond` 在 k = 2 零 spill（Briggs 的樂觀著色）；`c5loop` 在 k = 2 需要第 2 輪（maxlive 2 不夠，因為圖不是 chordal）；`self_update` 在 k = 2 必須 spill 一個「既用又定義」的變數，輸出仍正確（卡點 2 處理正確）。效能那行刻意只印「是否在 1 秒內」而不印秒數，讓輸出可重現。

## 延伸挑戰（加分）

- **保守 coalescing**：實作 Ch 17 的 Briggs 與 George 測試，在 simplify 之前把可合併的 move 合併。比較 `sum` 在 k = 4 時刪掉的 move 數，以及在隨機程式上 spill 數有沒有變多。
- **Iterated register coalescing**：照 George–Appel 1996 的 worklist（simplify / coalesce / freeze / spill 四個 worklist）重寫 `color`，讓 coalescing 和 simplify 交錯。
- **Rematerialization**：被 spill 的變數若定義是常數（`n = 10`），不要 store/load，在每次使用前重新產生常數。量一下 `sum` 在 k = 3 時 load 數的變化。
- **Live range splitting**：與其整個變數 spill，只在迴圈外 spill（迴圈內留在暫存器）。這是 LLVM greedy allocator 的核心想法，見 compiler_backend Ch 12。
- **先轉 SSA 再著色**：把輸入轉成 SSA（Ch 14 的支配樹 + φ 插入），照 Ch 16 的定義點支配樹前序著色，驗證「maxlive ≤ k 就不需要 spill」，再實作 φ 的平行複製消除（含 swap）。
- **呼叫慣例**：加入 `call f` 指令，規定 r0、r1 是 caller-saved（call 會破壞），用預著色節點讓跨 call 活著的變數被逼進其他暫存器。

## 自我檢核

- [ ] 不看解答，說明為什麼 `k = 1` 要拒絕、而 `k = 2` 保證收斂。
- [ ] 解釋 `i = i + 1` 的 use 與 def 為什麼必須用不同的暫時變數，否則會在哪一步、以什麼症狀失敗。
- [ ] 如果你的 `run` 和分配後的 `run` 輸出不同，你會依序檢查哪幾個地方？
- [ ] 說出 `c5loop` 在 k = 2 需要 spill 的圖論原因，以及把它轉成 SSA 後為什麼 2 個暫存器就夠（代價是什麼）。
- [ ] 能說出自己的實作和參考解答的差異（spill 對象、輪數、刪掉的 move 數），並解釋各自取捨。

Part 3 到這裡結束。Part 4 換一個完全不同的問題：圖上的「流」，從最大流最小割開始。

→ [Ch 20 最大流最小割與 Edmonds–Karp](./20-max-flow-min-cut.md)
