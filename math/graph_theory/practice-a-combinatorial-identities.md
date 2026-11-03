# 練習 A — 組合恆等式的暴力驗證器

> **目標**：把 Ch 2–6 學到的東西拼起來，寫一個 `identcheck` 工具：輸入一個計數公式和一個暴力列舉器，在一串參數上用精確整數比對兩者，找出**最小**反例，並且能抓到浮點誤差、非整數結果、例外與超出預算的情況。完成後，你在後面 35 章寫的每個計數公式都能丟進去自動驗證。

## 背景與動機

Part 1 的每一章都在做同一件事：寫一個公式，再寫一段暴力程式去數，兩邊對答案。你可能已經注意到這些程式長得很像，而且犯過的錯也很像：

- Ch 3 的 `factorial(n) / ...` 在 n = 100 安靜地錯了 13 位數。
- Ch 4 的 `round(n!/e)` 在 n = 0 給出 0，真值是 1。
- Ch 5 的 Binet 公式在 `F(71)` 開始取整出錯。
- Ch 6 的 `2^C(n,2) / n!` 在 n = 3 根本不是整數。

這些錯誤有共同點：**它們都在邊界或在大 n 時才出現**，而且如果你只看「前幾項對不對」，大多會漏掉。這個練習把「對答案」這件事做成一個工具，讓它系統地掃參數、用精確算術比較、把第一個出錯的參數回報給你。

真實世界的對應物是 property-based testing（例如 Python 的 Hypothesis、Haskell 的 QuickCheck）：給一個「對所有輸入都應該成立」的性質，工具自動產生輸入去找反例。我們的版本更簡單——參數由小到大窮舉——但這正好符合計數公式的特性：最小反例幾乎總在很小的 n 上。

## 任務規格

寫一個 Python 模組 `identcheck.py`（只用標準函式庫），包含以下元件。

### 1. 列舉器

| 函式 | 產生什麼 | `n = 0` 時 |
| --- | --- | --- |
| `subsets(n)` | `{0..n−1}` 的所有子集（`frozenset`） | 恰一個：空集合 |
| `perms(n)` | `range(n)` 的所有排列（tuple） | 恰一個：空排列 |
| `functions(k, n)` | `[k] → [n]` 的所有函數，長度 k 的 tuple | `functions(0, n)` 恰一個；`functions(k, 0)` 在 k ≥ 1 時為空 |
| `labeled_graphs(n)` | 頂點 `0..n−1` 上的所有簡單圖，鄰接字典 `dict[int, set[int]]` | 恰一張：空字典 |
| `set_partitions(n)` | `{0..n−1}` 的所有集合分割（list of lists） | 恰一個：空分割 `[]` |
| `count(it, pred=None)` | 計數，可帶過濾條件 | — |

`labeled_graphs` 必須遵守 [Ch 0](./00-environment-setup.md) 的鄰接字典約定：孤立點要出現在字典裡，`v in adj[u]` 若且唯若 `u in adj[v]`。

### 2. 宣稱（Claim）與結果（Result）

```text
Claim(name, lhs, rhs, params, cost=None)
  name   : 唯一的名稱
  lhs    : 可呼叫物件，通常是公式
  rhs    : 可呼叫物件，通常是暴力；也可以是另一個公式
  params : 參數 tuple 的 list，由「小」到「大」排列，例如 [(0,), (1,), ...] 或 [(n, k), ...]
  cost   : 可選，params -> 估計的暴力列舉量（整數）

Result(name, status, checked, skipped, seconds, detail, witness)
```

`check(claim, budget=2_000_000)` 依序對每組參數 p 呼叫 `lhs(*p)` 與 `rhs(*p)`，規則如下：

| 情況 | status | witness | 是否停止 |
| --- | --- | --- | --- |
| `cost(*p) > budget` | 這組計入 `skipped`，不呼叫 lhs/rhs | — | 否，繼續下一組 |
| 任一邊丟出例外 | `ERROR`，detail 含例外型別與訊息 | p | 是 |
| 任一邊回傳 `float`、`bool`、非整數的 `Fraction`、或其他型別 | `NONINT`，detail 說明哪一邊、什麼值 | p | 是 |
| 兩邊都是精確整數但不相等 | `FAIL`，detail 為 `lhs = ..., rhs = ...` | p | 是 |
| 全部相等 | `PASS` | None | — |
| 沒有任何一組被實際檢查（全部略過或 params 為空） | `SKIP` | None | — |

精確整數的定義：`int`（但不是 `bool`），或分母為 1 的 `fractions.Fraction`。`float` 一律拒絕，**即使它的值剛好是整數**（例如 `1.0`）——這是刻意的，因為 float 公式在小 n 對、大 n 錯，允許它就等於把 Ch 3 和 Ch 5 的坑留給使用者。

### 3. Registry 與報表

- `Registry.add(claim)`：名稱重複時丟 `ValueError`；任何 params 元素不是「非負整數組成的 tuple」時丟 `ValueError`。
- `Registry.run(budget)`：依加入順序執行所有宣稱，回傳 `list[Result]`。
- `fmt(result)` 產生一行報表：

```text
[PASS  ] <name 靠左對齊 28 字>  <checked> 組通過[ 略過 <skipped>] (<seconds>s)
[FAIL  ] <name>               在 <witness>: <detail>
```

- `sequence_claim(name, f, known, offset=0)`：建立一個把 `f(n)` 和已知數列 `known`（例如抄自 OEIS，`known[0]` 對應 `n = offset`）逐項比對的宣稱。

### 4. 內建宣稱

`builtin()` 回傳一個 Registry，至少包含下表的宣稱，其中 `WRONG` 開頭的必須被驗證器抓到（FAIL 或 NONINT）：

| 名稱 | lhs（公式） | rhs（暴力或另一公式） | 參數範圍 | 出處 |
| --- | --- | --- | --- | --- |
| binomial C(n,k) | `comb(n, k)` | 數 `combinations` | 0 ≤ n ≤ 7，0 ≤ k ≤ n+1 | Ch 3 |
| pascal | `C(n,k)` | `C(n−1,k−1) + C(n−1,k)` | 1 ≤ k ≤ n ≤ 14 | Ch 3 |
| committee-chair | `n·2^(n−1)` | 子集大小總和 | 0 ≤ n ≤ 11 | Ch 3 |
| derangements | 排容和式 | 數無不動點排列 | 0 ≤ n ≤ 9 | Ch 4 |
| surjections | `n!·S(k,n)` | 數滿射 | 0 ≤ k ≤ 6，0 ≤ n ≤ 4 | Ch 4 |
| Bell | `Σ_k S(n,k)` | 數集合分割 | 0 ≤ n ≤ 9 | Ch 4 |
| perfect matchings | `(2m−1)!!` | 數每塊大小 2 的分割 | 0 ≤ m ≤ 4 | Ch 3 |
| handshake | `0` | 違反握手定理（度數和 ≠ 2 倍邊數）的標號圖數 | 0 ≤ n ≤ 6 | Ch 0、2 |
| connected labeled | `sequence_claim` 對 A001187 | — | 0 ≤ n ≤ 6 | Ch 5 |
| unlabeled | Burnside（列舉 `S_n`） | 暴力標準形 | 0 ≤ n ≤ 5 | Ch 6 |
| necklaces | `(1/n)Σ q^gcd(n,k)` | 暴力標準形 | 1 ≤ n ≤ 8，1 ≤ q ≤ 3 | Ch 6 |
| Ham cycles K_n | `(n−1)!/2` | 數邊集合 | 3 ≤ n ≤ 7 | Ch 3 |
| WRONG round(n!/e) | `round(n!/e)` | 數錯排 | 0 ≤ n ≤ 8 | Ch 4 |
| WRONG 2^C(n,2)/n! | 天真除法 | 暴力標準形 | 0 ≤ n ≤ 5 | Ch 6 |
| WRONG Ham cycles | `(n−1)!/2` | 數邊集合 | **1** ≤ n ≤ 7 | Ch 3 |
| WRONG Binet float | float Binet | 整數 Fibonacci | 0 ≤ n < 200 | Ch 5 |
| WRONG float formula | `n!/(n−1)!` 用 `/` | `n` | 1 ≤ n ≤ 4 | Ch 3 |

`python identcheck.py` 執行全部內建宣稱並印出報表。

### 限制

- 只能用 Python 標準函式庫（`itertools`、`math`、`fractions`、`dataclasses`、`time`）。NetworkX 不准用：驗證器本身必須獨立於被驗證的對象。
- 參數必須由小到大檢查，第一個不一致就停，保證回報的是**參數序上的最小反例**。
- 全部內建宣稱在一般筆電上應在 5 秒內跑完（參考解答在 Ryzen 7 5700G 上約 0.8 秒）。

## 期望輸出範例

```text
輸入：python identcheck.py
輸出：
[PASS  ] binomial C(n,k)               44 組通過 (0.00s)
[PASS  ] pascal                       105 組通過 (0.00s)
...
[PASS  ] Ham cycles K_n (n>=3)          5 組通過 (0.02s)
[FAIL  ] WRONG round(n!/e)            在 (0,): lhs = 0, rhs = 1
[FAIL  ] WRONG 2^C(n,2)/n!            在 (2,): lhs = 1, rhs = 2
[NONINT] WRONG Ham cycles (n-1)!/2    在 (1,): lhs = 1/2（非整數）
[FAIL  ] WRONG Binet float            在 (71,): lhs = 308061521170130, rhs = 308061521170129
[NONINT] WRONG float formula          在 (1,): lhs 回傳 float 1.0，請改用整數或 Fraction
```

```text
邊界輸入：check(Claim("boom", lambda n: 1 // n, lambda n: 0, [(0,)]))
輸出：status = ERROR，detail = "lhs(0,) 丟出 ZeroDivisionError: integer division or modulo by zero"
```

```text
預算輸入：derangements 的 cost = n!，用 budget=1000 執行
輸出：[PASS  ] derangements                   7 組通過 略過 3 (0.00s)
      （n = 7, 8, 9 的 n! 超過 1000，被略過；仍然 PASS，因為檢查過的都對）
```

秒數欄位依機器而異，其他欄位應完全一致。

## 如果你卡住了

1. **`set_partitions` 不知道怎麼寫**：想像依序處理元素 0、1、2……，每個元素要嘛加入某個已經存在的塊，要嘛自己開一個新塊。這個遞迴保證每個分割恰好產生一次。為什麼？想想「塊按最小元素排序」這個標準形。
2. **`exact()` 的型別判斷順序不對**：`bool` 是 `int` 的子類別，`isinstance(True, int)` 是 `True`。要先擋 bool 再接受 int。
3. **WRONG Binet 沒被抓到**：檢查你的 params 有沒有掃到 71 以上，以及 `round()` 回傳的是 int（`round(float)` 回傳 int，所以它會通過 `exact()`，靠的是數值不相等被抓到）。
4. **handshake 宣稱寫成「公式 vs 暴力」很彆扭**：把它改寫成「違反定理的圖有幾張」，lhs 是 0，rhs 是暴力數出的違反數。這是把「對所有物件成立」的定理轉成計數宣稱的通用技巧。
5. **最小反例的意義**：如果你的 params 是 `[(n, k) for n ... for k ...]`，「最小」是指這個 list 的順序。順序要自己設計成「越前面越簡單」。

## 實作步驟建議

### Step 1：列舉器

先寫 `subsets`、`perms`、`functions`、`labeled_graphs`、`set_partitions`、`count`。用已知的數量自我檢查：`count(subsets(5)) == 32`、`count(set_partitions(5)) == 52`（Bell 數）、`count(labeled_graphs(4)) == 64`，以及每個列舉器在 n = 0 的行為符合規格表。

### Step 2：精確比較

寫 `exact(value, side)`，回傳 `(整數, None)` 或 `(None, 原因字串)`。對 `int`、`bool`、`float`、`Fraction(3, 1)`、`Fraction(1, 2)`、`"3"` 各測一次。

### Step 3：`check` 的主迴圈

先只處理 PASS 和 FAIL。確認 `Claim("bad", lambda n: n * n, lambda n: 2 ** n, ...)` 回報的 witness 是 `(0,)`（`0² = 0 ≠ 1`），不是 `(3,)`。

### Step 4：失敗模式

加上 ERROR、NONINT、預算 SKIP、全部略過時的 SKIP。每種寫一個小測試。注意例外要被**捕捉並回報**，不能讓一個壞宣稱中斷整份報表。

### Step 5：Registry、報表與內建宣稱

實作 `Registry.add` 的驗證、`run`、`fmt`、`sequence_claim`，再把上面表格的宣稱逐一加入。每加一個就跑一次，確認 PASS 的真的 PASS、WRONG 的真的被抓到。

### Step 6：整合與測試

寫一份 pytest 測試檔（下面「測試用例」一節有完整清單），`python -m pytest -q` 全部通過，`python identcheck.py` 輸出和期望範例一致。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作</summary>

`identcheck.py`：

```python
"""identcheck：組合恆等式 / 計數公式的暴力驗證器（練習 A 參考解答）。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import combinations, permutations, product
from math import comb, factorial, e, gcd
from typing import Callable, Iterable, Iterator

# ---------------------------------------------------------------- 列舉器


def subsets(n: int) -> Iterator[frozenset]:
    """{0..n-1} 的所有子集。"""
    for k in range(n + 1):
        for c in combinations(range(n), k):
            yield frozenset(c)


def perms(n: int) -> Iterator[tuple]:
    return permutations(range(n))


def functions(k: int, n: int) -> Iterator[tuple]:
    """[k] -> [n] 的所有函數，以長度 k 的 tuple 表示。"""
    return product(range(n), repeat=k)


def labeled_graphs(n: int) -> Iterator[dict]:
    """頂點 0..n-1 上的所有簡單圖，鄰接字典 dict[int, set[int]]。"""
    pairs = list(combinations(range(n), 2))
    for mask in range(1 << len(pairs)):
        adj = {v: set() for v in range(n)}
        for i, (u, v) in enumerate(pairs):
            if mask >> i & 1:
                adj[u].add(v)
                adj[v].add(u)
        yield adj


def set_partitions(n: int) -> Iterator[list]:
    """{0..n-1} 的所有集合分割（每塊是 list）。用「元素 i 加入既有塊或開新塊」遞迴。"""
    def rec(i, blocks):
        if i == n:
            yield [b[:] for b in blocks]
            return
        for b in blocks:
            b.append(i)
            yield from rec(i + 1, blocks)
            b.pop()
        blocks.append([i])
        yield from rec(i + 1, blocks)
        blocks.pop()
    yield from rec(0, [])


def count(it: Iterable, pred: Callable | None = None) -> int:
    if pred is None:
        return sum(1 for _ in it)
    return sum(1 for x in it if pred(x))


def is_connected(adj: dict) -> bool:
    if not adj:
        return True          # 約定：空圖連通（和 A001187 的 a(0)=1 一致）
    start = next(iter(adj))
    seen, stack = {start}, [start]
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return len(seen) == len(adj)


# ---------------------------------------------------------------- 宣稱與結果


@dataclass
class Claim:
    name: str
    lhs: Callable                     # 通常是公式
    rhs: Callable                     # 通常是暴力；也可以是另一個公式
    params: list                      # 依「由小到大」排好的參數 tuple
    cost: Callable | None = None      # params -> 估計的暴力列舉量；None 表示不估
    note: str = ""


@dataclass
class Result:
    name: str
    status: str                       # PASS / FAIL / NONINT / ERROR / SKIP
    checked: int = 0
    skipped: int = 0
    seconds: float = 0.0
    detail: str = ""
    witness: tuple | None = None


def exact(value, side: str):
    """把結果轉成精確整數；非整數回傳 (None, 原因)。"""
    if isinstance(value, bool):
        return None, f"{side} 回傳 bool"
    if isinstance(value, int):
        return value, None
    if isinstance(value, Fraction):
        if value.denominator == 1:
            return int(value), None
        return None, f"{side} = {value}（非整數）"
    if isinstance(value, float):
        return None, f"{side} 回傳 float {value!r}，請改用整數或 Fraction"
    return None, f"{side} 回傳 {type(value).__name__}"


def check(claim: Claim, budget: int = 2_000_000) -> Result:
    t0 = time.perf_counter()
    res = Result(claim.name, "PASS")
    for p in claim.params:
        if claim.cost is not None and claim.cost(*p) > budget:
            res.skipped += 1
            continue
        values = []
        for side, f in (("lhs", claim.lhs), ("rhs", claim.rhs)):
            try:
                raw = f(*p)
            except Exception as exc:                       # noqa: BLE001 — 回報而非中止
                res.status, res.witness = "ERROR", p
                res.detail = f"{side}{p} 丟出 {type(exc).__name__}: {exc}"
                res.seconds = time.perf_counter() - t0
                return res
            v, why = exact(raw, side)
            if why:
                res.status, res.witness, res.detail = "NONINT", p, why
                res.seconds = time.perf_counter() - t0
                return res
            values.append(v)
        res.checked += 1
        if values[0] != values[1]:
            res.status, res.witness = "FAIL", p
            res.detail = f"lhs = {values[0]}, rhs = {values[1]}"
            break
    if res.status == "PASS" and res.checked == 0:
        res.status = "SKIP"
    res.seconds = time.perf_counter() - t0
    return res


def fmt(r: Result) -> str:
    head = f"[{r.status:<6}] {r.name:<28}"
    if r.status in ("PASS", "SKIP"):
        extra = f" 略過 {r.skipped}" if r.skipped else ""
        return f"{head} {r.checked:>3} 組通過{extra} ({r.seconds:.2f}s)"
    return f"{head} 在 {r.witness}: {r.detail}"


class Registry:
    def __init__(self):
        self.claims: dict[str, Claim] = {}

    def add(self, claim: Claim):
        if claim.name in self.claims:
            raise ValueError(f"重複的宣稱名稱: {claim.name}")
        for p in claim.params:
            if not isinstance(p, tuple) or any(not isinstance(x, int) or x < 0 for x in p):
                raise ValueError(f"{claim.name}: 參數必須是非負整數 tuple，收到 {p!r}")
        self.claims[claim.name] = claim

    def run(self, budget: int = 2_000_000) -> list[Result]:
        return [check(c, budget) for c in self.claims.values()]


def sequence_claim(name: str, f: Callable[[int], int], known: list[int], offset: int = 0) -> Claim:
    """把公式和一段已知數列（例如抄自 OEIS）對照。"""
    return Claim(name, f, lambda n: known[n - offset],
                 [(n,) for n in range(offset, offset + len(known))])


# ---------------------------------------------------------------- 內建宣稱


def dfact(m):
    r = 1
    while m > 1:
        r *= m
        m -= 2
    return r


def stirling2(k, n):
    return Fraction(sum((-1) ** j * comb(n, j) * (n - j) ** k for j in range(n + 1)), factorial(n))


def fib_int(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def unlabeled_burnside(n):
    pairs = list(combinations(range(n), 2))

    def cyc(s):
        seen, c = set(), 0
        for p in pairs:
            if p in seen:
                continue
            c += 1
            q = p
            while q not in seen:
                seen.add(q)
                a, b = s[q[0]], s[q[1]]
                q = (a, b) if a < b else (b, a)
        return c
    return Fraction(sum(2 ** cyc(s) for s in permutations(range(n))), factorial(n))


def unlabeled_brute(n):
    pairs = list(combinations(range(n), 2))
    ps = list(permutations(range(n)))
    seen = set()
    for mask in range(1 << len(pairs)):
        E = [p for i, p in enumerate(pairs) if mask >> i & 1]
        seen.add(min(tuple(sorted(tuple(sorted((s[u], s[v]))) for u, v in E)) for s in ps))
    return len(seen)


def builtin() -> Registry:
    R = Registry()
    nk = [(n, k) for n in range(0, 8) for k in range(0, n + 2)]
    R.add(Claim("binomial C(n,k)", comb, lambda n, k: count(combinations(range(n), k)), nk))
    R.add(Claim("pascal", lambda n, k: comb(n, k),
                lambda n, k: comb(n - 1, k - 1) + comb(n - 1, k),
                [(n, k) for n in range(1, 15) for k in range(1, n + 1)]))
    R.add(Claim("committee-chair", lambda n: n * 2 ** (n - 1) if n else 0,
                lambda n: sum(len(s) for s in subsets(n)), [(n,) for n in range(0, 12)]))
    R.add(Claim("derangements", lambda n: sum((-1) ** k * comb(n, k) * factorial(n - k) for k in range(n + 1)),
                lambda n: count(perms(n), lambda p: all(p[i] != i for i in range(n))),
                [(n,) for n in range(0, 10)], cost=lambda n: factorial(n)))
    R.add(Claim("surjections = n!·S(k,n)", lambda k, n: factorial(n) * stirling2(k, n),
                lambda k, n: count(functions(k, n), lambda f: len(set(f)) == n),
                [(k, n) for k in range(0, 7) for n in range(0, 5)], cost=lambda k, n: n ** k))
    R.add(Claim("Bell = Σ S(n,k)", lambda n: sum(stirling2(n, k) for k in range(n + 1)),
                lambda n: count(set_partitions(n)), [(n,) for n in range(0, 10)]))
    R.add(Claim("perfect matchings K_2m", lambda m: dfact(2 * m - 1),
                lambda m: count(set_partitions(2 * m), lambda P: all(len(b) == 2 for b in P)),
                [(m,) for m in range(0, 5)]))
    R.add(Claim("handshake (labeled n<=6)", lambda n: 0,
                lambda n: count(labeled_graphs(n),
                                lambda a: sum(map(len, a.values())) != 2 * sum(1 for u in a for v in a[u] if u < v)),
                [(n,) for n in range(0, 7)], cost=lambda n: 2 ** comb(n, 2)))
    R.add(sequence_claim("connected labeled (A001187)",
                         lambda n: count(labeled_graphs(n), is_connected),
                         [1, 1, 1, 4, 38, 728, 26704]))
    R.add(Claim("unlabeled: Burnside vs canon", unlabeled_burnside, unlabeled_brute,
                [(n,) for n in range(0, 6)]))
    R.add(Claim("necklaces C_n, q colors", lambda n, q: Fraction(sum(q ** gcd(n, k) for k in range(n)), n),
                lambda n, q: len({min(s[i:] + s[:i] for i in range(n)) for s in product(range(q), repeat=n)}),
                [(n, q) for n in range(1, 9) for q in range(1, 4)]))
    R.add(Claim("Ham cycles K_n (n>=3)", lambda n: factorial(n - 1) // 2,
                lambda n: len({frozenset(frozenset((p[i], p[(i + 1) % n])) for i in range(n))
                               for p in permutations(range(n))}),
                [(n,) for n in range(3, 8)]))
    # ---- 以下是故意寫錯的宣稱，用來確認驗證器抓得到 ----
    R.add(Claim("WRONG round(n!/e)", lambda n: round(factorial(n) / e),
                lambda n: count(perms(n), lambda p: all(p[i] != i for i in range(n))),
                [(n,) for n in range(0, 9)]))
    R.add(Claim("WRONG 2^C(n,2)/n!", lambda n: Fraction(2 ** comb(n, 2), factorial(n)),
                unlabeled_brute, [(n,) for n in range(0, 6)]))
    R.add(Claim("WRONG Ham cycles (n-1)!/2", lambda n: Fraction(factorial(n - 1), 2),
                lambda n: len({frozenset(frozenset((p[i], p[(i + 1) % n])) for i in range(n))
                               for p in permutations(range(n))}),
                [(n,) for n in range(1, 8)]))
    R.add(Claim("WRONG Binet float", lambda n: round(((1 + 5 ** 0.5) / 2) ** n / 5 ** 0.5),
                fib_int, [(n,) for n in range(0, 200)]))
    R.add(Claim("WRONG float formula", lambda n: factorial(n) / factorial(n - 1), lambda n: n,
                [(n,) for n in range(1, 5)]))
    return R


if __name__ == "__main__":
    for r in builtin().run():
        print(fmt(r))
```

`test_identcheck.py`：

```python
from fractions import Fraction
import pytest
from identcheck import (Claim, Registry, check, builtin, set_partitions, labeled_graphs,
                        subsets, count, sequence_claim)


def test_enumerators_sizes():
    assert count(subsets(5)) == 32
    assert count(set_partitions(5)) == 52          # Bell(5)
    assert count(labeled_graphs(4)) == 64
    assert count(set_partitions(0)) == 1           # 空集合恰有一個分割


def test_pass_and_fail():
    ok = check(Claim("ok", lambda n: 2 ** n, lambda n: count(subsets(n)), [(n,) for n in range(8)]))
    assert ok.status == "PASS" and ok.checked == 8
    bad = check(Claim("bad", lambda n: n * n, lambda n: count(subsets(n)), [(n,) for n in range(8)]))
    assert bad.status == "FAIL" and bad.witness == (0,)    # 0^2 = 0 ≠ 1：最小反例


def test_nonint_and_error():
    r = check(Claim("frac", lambda n: Fraction(n, 2), lambda n: 0, [(1,)]))
    assert r.status == "NONINT"
    r = check(Claim("float", lambda n: 1.0, lambda n: 1, [(1,)]))
    assert r.status == "NONINT"
    r = check(Claim("boom", lambda n: 1 // n, lambda n: 0, [(0,)]))
    assert r.status == "ERROR" and "ZeroDivisionError" in r.detail


def test_budget_skip():
    c = Claim("big", lambda n: 2 ** n, lambda n: count(subsets(n)),
              [(n,) for n in range(30)], cost=lambda n: 2 ** n)
    r = check(c, budget=1000)
    assert r.status == "PASS" and r.checked == 10 and r.skipped == 20
    r = check(Claim("all-skip", lambda n: 1, lambda n: 1, [(40,)], cost=lambda n: 2 ** n), budget=10)
    assert r.status == "SKIP"


def test_registry_validation():
    R = Registry()
    R.add(Claim("a", lambda n: 1, lambda n: 1, [(1,)]))
    with pytest.raises(ValueError):
        R.add(Claim("a", lambda n: 1, lambda n: 1, [(1,)]))
    with pytest.raises(ValueError):
        R.add(Claim("neg", lambda n: 1, lambda n: 1, [(-1,)]))
    with pytest.raises(ValueError):
        R.add(Claim("notuple", lambda n: 1, lambda n: 1, [3]))


def test_sequence_claim_offset():
    c = sequence_claim("catalan", lambda n: [1, 1, 2, 5, 14][n], [1, 2, 5, 14], offset=1)
    assert check(c).status == "PASS"


def test_builtin_expected_statuses():
    got = {r.name: r for r in builtin().run()}
    for name, r in got.items():
        if name.startswith("WRONG"):
            assert r.status in ("FAIL", "NONINT"), name
        else:
            assert r.status == "PASS", (name, r.detail)
    assert got["WRONG Binet float"].witness == (71,)
```

執行（PowerShell，`PYTHONUTF8=1` 的原因見 Ch 0）：

```powershell
$env:PYTHONUTF8 = "1"
python -m pytest -q test_identcheck.py
python identcheck.py
```

pytest 輸出：

```text
.......                                                                  [100%]
7 passed in 0.76s
```

**解答說明**：

- **左右兩邊對稱處理**：`check` 不假設哪邊是公式、哪邊是暴力，`lhs`、`rhs` 都走同一套例外捕捉與 `exact()` 檢查。這讓「公式 vs 公式」（pascal）和「公式 vs 暴力」用同一個介面。
- **`exact()` 拒絕所有 float**：`WRONG float formula` 的 `n!/(n−1)!` 在 n = 1..4 的值都是精確的 `1.0, 2.0, 3.0, 4.0`，數值上全對，卻在 n = 1 就被擋下。這是刻意的設計：只看數值的驗證器會讓它通過，然後它在大 n 時出錯。寧可多擋，逼使用者改用 `//` 或 `Fraction`。
- **NONINT 是比 FAIL 更早的訊號**：`(n−1)!/2` 在 n = 1 給 `1/2`，在 n = 2 也給 `1/2`——公式在 n < 3 沒有意義，暴力數出 1 和 1（n = 1 的「圈」是一個點、n = 2 是一條邊，`frozenset` 把它們收成一個）。驗證器在 n = 1 就說「這不是整數」，比等到數值不符更直接地指出「適用範圍錯了」。
- **`2^C(n,2)/n!` 在 n = 2 是 FAIL 而不是 NONINT**：`2/2 = 1` 剛好是整數，但真值是 2（空圖和一條邊）。n = 3 才會出現非整數 `8/6`。參數由小到大掃描保證我們先看到 n = 2 這個更小的反例。
- **預算機制只略過、不判失敗**：`cost` 是使用者給的估計，超過預算的參數不檢查但也不算錯，最後的 PASS 意思是「檢查過的都對」。報表裡印出略過數，避免使用者以為全部都驗過了。
- **`set_partitions` 的正確性**：每個分割有唯一的「塊按最小元素排序、元素依序加入」的建構順序，遞迴恰好走過每一種，所以不重複也不遺漏。`count(set_partitions(n))` 得到 Bell 數 `1, 1, 2, 5, 15, 52, ...`，和 `Σ_k S(n, k)` 對上，這個宣稱本身就在驗證列舉器。
- **驗證器不用 NetworkX**：`is_connected` 自己寫 DFS。如果用 `nx.is_connected`，那「連通標號圖」這個宣稱驗證的其實是 NetworkX，不是你的公式；Part 2 之後你自刻演算法時，驗證器還要拿來對拍 NetworkX，兩者必須獨立。

</details>

## 測試用例

| 輸入 | 預期輸出 | 說明 |
| --- | --- | --- |
| `count(subsets(5))`、`count(set_partitions(5))`、`count(labeled_graphs(4))` | 32、52、64 | 列舉器大小 |
| `count(set_partitions(0))` | 1 | 空集合恰有一個分割（空分割） |
| `Claim("ok", 2**n, count(subsets(n)), n=0..7)` | PASS，checked = 8 | 正常路徑 |
| `Claim("bad", n*n, count(subsets(n)), n=0..7)` | FAIL，witness = `(0,)` | 最小反例在邊界 |
| lhs 回傳 `Fraction(1, 2)` | NONINT | 非整數分數 |
| lhs 回傳 `1.0` | NONINT | float 一律拒絕，即使值是整數 |
| lhs 在 `(0,)` 做 `1 // n` | ERROR，detail 含 `ZeroDivisionError` | 例外被捕捉，不中斷 |
| `cost = 2**n`，n = 0..29，`budget = 1000` | PASS，checked = 10，skipped = 20 | 預算邊界：`2^9 = 512 ≤ 1000 < 1024` |
| 唯一參數 `(40,)` 被略過 | SKIP | 沒有任何一組實際被檢查 |
| `Registry.add` 兩個同名宣稱 | `ValueError` | 非法輸入 |
| params 含 `(-1,)` 或非 tuple 的 `3` | `ValueError` | 非法輸入 |
| `sequence_claim(..., offset=1)` | PASS | offset 對齊 |
| `builtin().run()` | 非 WRONG 全 PASS；WRONG 全為 FAIL 或 NONINT；Binet 的 witness 為 `(71,)` | 整合測試 |
| `builtin().run(budget=1000)` | derangements 略過 3、surjections 略過 2、handshake 略過 2，仍 PASS | 效能邊界 |

## 延伸挑戰（加分）

- **Gray code 列舉標號圖**：把 `labeled_graphs` 改成 Gray code 順序，每一步只加或刪一條邊，並增量維護度數序列。量測 n = 7 時握手定理驗證從 Ch 0 的 11.9 秒降到多少。
- **自動收縮反例**：目前回報的是參數最小的反例。對「圖上的性質」（例如 handshake 那種寫法），再加一層：找到違反的圖之後，嘗試逐一刪邊、刪點，只要仍然違反就繼續刪，最後回報一張**極小**的反例圖。這是 Hypothesis 的 shrinking 的簡化版。
- **離線 OEIS 指紋**：加一個 `fingerprint(f, n_max)`，算出前 n_max 項，和一個你手動整理的小型「數列 → A-number」字典比對（例如本 Part 出現過的 A000088、A000166、A001187、A006129、A000031），讓工具告訴你「你的公式看起來是 A001187」。
- **平行化**：用 `concurrent.futures.ProcessPoolExecutor` 把不同宣稱分給不同 process，並說明為什麼在 CPython 上用 thread 不會變快。
- **接上 Part 2**：Ch 9 會證明 Cayley 公式 `n^(n−2)`、Ch 28 會證明矩陣樹定理。把「標號樹數 = `n^(n−2)`」和「`K_n` 的 Laplacian 去掉一行一列的行列式 = `n^(n−2)`」加進內建宣稱（行列式用 `Fraction` 做高斯消去，保持精確）。

## 自我檢核

- [ ] 能解釋為什麼驗證器要拒絕所有 float，即使某個 float 剛好等於正確的整數。
- [ ] 能說出「參數由小到大、第一個不一致就停」和「隨機抽樣參數」各自的優缺點，以及為什麼計數公式適合前者。
- [ ] 不看解答，說出 `WRONG 2^C(n,2)/n!` 為什麼在 n = 2 是 FAIL、在 n = 3 會是 NONINT，以及這反映了 Ch 6 的哪個概念。
- [ ] 能把「對所有圖成立的定理」（例如定理 2.6：任意圖有兩點同度數）改寫成 identcheck 的宣稱，並說明 lhs、rhs 各是什麼。
- [ ] 能說出自己的實作和參考解答的差異，並解釋各自取捨。

工具準備好了。Part 2 開始處理圖本身的結構：嚴格的定義、表示法，以及「兩張圖什麼時候算同一張」——同構這個在 Ch 6 已經偷偷用了一整章的概念。

→ [Ch 7 圖的定義、表示與同構](./07-graph-definitions.md)
