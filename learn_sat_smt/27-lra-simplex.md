# Ch 27 — LRA：Simplex for SMT

> 目標：理解 **LRA (Linear Real Arithmetic)** 如何解。核心演算法是 **Dutertre–de Moura 2006** 的 SMT-friendly Simplex — 跟教科書的 LP Simplex 有關鍵差異。這章是 Part 2 最「數值」的一章，準備好紙筆跟著跑。

## LRA 問題

**LRA** 的語言：

- 變數：Real 值 `x, y, z`
- 運算：`+, -, 常數乘法, <, ≤, >, ≥, =`
- **線性**：`3x + 2y ≤ 5`，不能 `x * y` 或 `x²`

可決，而且 polynomial time（Khachiyan 1979 ellipsoid、Karmarkar 1984 interior point）。實務最快：**Simplex**（George Dantzig 1947），worst-case exponential 但 average polynomial。

### 範例

Assertions：

```
3x + 2y ≤ 6
x - y ≥ -1
x ≥ 0
y ≥ 0
```

求是否有 `(x, y) ∈ R²` 同時滿足。答案：SAT，`x = 0, y = 0`。

## 為什麼 LP Simplex 不直接套

教科書的 LP Simplex：**optimize objective**（maximize/minimize）under constraints。SMT 不需要 optimize、**只問 feasibility（有解嗎）**。差別：

- LP：找 `maximize c^T x s.t. Ax ≤ b`
- SMT: 找 `∃x: Ax ≤ b`

LP Simplex 有 **objective function**，pivot 規則優化 objective。SMT 版本沒 objective、規則不同。

更大差別：**Incremental**。SMT solver backtrack 時 pop 某些 assertion，然後 add 新的。LP Simplex 沒這需求。

## Dutertre–de Moura 2006 的關鍵想法

他們在 *Yices* solver 開發時設計了 **SMT-friendly general Simplex**（GS）：

1. **用 `≤` 和 `≥` 形式處理**，不用 slack variable
2. **Bound 存在變數上**：每變數有 lower bound `l_x`、upper bound `u_x`
3. **Tableau** 表達 equation `y = ∑ aᵢxᵢ`（`y` 是 basic、`xᵢ` 是 non-basic）
4. **Assertion 只更新 bound**，很快
5. **Pivot** 只在必要時（bound violation）

**結果**：Assert O(1)、check 可 incremental、跟 DPLL(T) 完美搭。Z3 和 cvc5 的 LRA 都基於這個架構。

## Tableau 結構

所有 constraint 改寫成 **equations with bounds**：

```
原 constraint:    3x + 2y ≤ 6
改寫:             s = 3x + 2y, s ≤ 6    ← 引入新變數 s
```

`s` 是 **basic**（defined by equation），`x, y` 是 **non-basic**（可以自由設）。每個 basic 在 tableau 裡有一行：

```
basic     non-basic coef
─────────────────────────
s         3x + 2y
s'        x - y          ← 處理 x - y ≥ -1
```

Bound table（每變數）：

```
var    lower    upper
x      0        +∞
y      0        +∞
s      -∞       6
s'     -1       +∞
```

## 基本不變條件

Tableau 維持以下 **model**：每個變數有當前值 `v(x)`、滿足：

1. **Equation invariant**：每個 basic 變數 `y` 的 `v(y) = Σ aᵢ v(xᵢ)`
2. **Bound invariant (for non-basic)**：每個 non-basic `xᵢ` 的 `l_{xᵢ} ≤ v(xᵢ) ≤ u_{xᵢ}`
3. **Bound check for basic**：basic 變數的 `v(y)` 可能暫時違反 bound → pivot 調整

Initial state: 所有變數 `v = 0`（若 0 在每個 non-basic 的 bound 內）。

## 演算法流程

```
assert_bound(x, kind, val):
    if kind == "lower" and val > l_x: l_x = val
    if kind == "upper" and val < u_x: u_x = val
    # x 的新值滿足 bound？
    if x is non-basic and v(x) not in [l_x, u_x]:
        adjust v(x) 到 [l_x, u_x]，更新所有 basic 的 v

check():
    while 有 basic y with v(y) < l_y or v(y) > u_y:
        find a non-basic x in y's row that can help：
            若 v(y) < l_y，要增大 v(y)：
                pivot x 的 coef 正 → 可增 x（if x < u_x）
                pivot x 的 coef 負 → 可減 x (if x > l_x)
            若 v(y) > u_y，反之
        找不到 → UNSAT
        找到 → pivot(y, x)，重算 v
    return SAT
```

**Pivot**：把 basic `y` 和 non-basic `x` 角色對調。`y` 變 non-basic、`x` 變 basic。Tableau 對應 row 操作（linear algebra 基本功）。

## 範例：跑一次

Tableau:

```
s  = 3x + 2y
s' = x - y
```

Bounds: `x ∈ [0, ∞), y ∈ [0, ∞), s ∈ (-∞, 6], s' ∈ [-1, ∞)`.

Initial model: `x = 0, y = 0, s = 0, s' = 0`. 檢查：

- `s = 0 ≤ 6` ✓
- `s' = 0 ≥ -1` ✓
- `x ≥ 0` ✓
- `y ≥ 0` ✓

**SAT，model 是 (0, 0)**。 check() 無需 pivot。

### 若 assert 加 `x + y ≥ 3`

New equation: `s'' = x + y, s'' ≥ 3`. Add to tableau:

```
s  = 3x + 2y
s' = x - y
s''= x + y
```

Bounds: `s'' ≥ 3`.

Initial `v(s'') = v(x) + v(y) = 0 + 0 = 0`. 違反 `s'' ≥ 3`.

Pivot: `s''` 要增大到 3。Row 裡 non-basic 有 x, y：

- x coef 1 > 0，`v(x) = 0, u_x = ∞`，可增
- y coef 1 > 0，可增

選 x，pivot(s'', x)：tableau 改寫，x 成 basic、s'' 非 basic：

```
x  = s'' - y     ← from s'' = x + y 解出 x
然後把 x 代回其他 row：
s  = 3(s'' - y) + 2y = 3s'' - y
s' = (s'' - y) - y = s'' - 2y
```

新值：`v(s'') = 3`（新 non-basic 值設到 lower bound），`v(y) = 0`（非 basic 不變），`v(x) = 3 - 0 = 3`。

檢查其他：`s = 3*3 - 0 = 9 > 6` → 違反 `s ≤ 6`。再 pivot... 最終可能發現 `UNSAT`（某個 row 沒 non-basic 能 balance）。

**實務 code**: 這些 row 操作用 rational number 做（Z3 和 cvc5 都 unbounded precision rational）。浮點會有誤差、不能保證 correctness。

## Strict inequality 處理

`x < 5` 不能直接存成 `u_x = 5`（那是 `x ≤ 5`）。要區分 strict。

Dutertre–de Moura 用 **delta-trick**：

```
x < 5   ↔   x ≤ 5 - δ
```

`δ` 是 **infinitesimal positive**。具體實作用 pair `(value, delta_coef)`：

```
Rational v;   // 實部
Rational d;   // δ 係數
// 真值 = v + d·δ
```

兩個 `(v, d)` 比大小：先比 `v`，再比 `d`。

延伸性的 pivot rule 也擴展成兩個分量。**實作複雜度加一倍，但對應語義精確**。

## UNSAT 檢測

Check 的 main loop 裡，當某個 basic `y` 違反 bound（假設 `v(y) < l_y`）要增大：

```
for each non-basic x in y's row:
    coef = coef of x in y's row
    if coef > 0 and v(x) < u_x: can increase → pivot
    if coef < 0 and v(x) > l_x: can decrease → pivot
找不到可 pivot 的 x → UNSAT
```

**UNSAT 的 explanation**：row 的每個 non-basic 都「被上下 bound 卡死」，加 `y` 的 lower bound 違反，**這些 bound 的集合就是 minimal explanation**。

```
Explanation = {l_y} ∪ {u_x for x with coef > 0} ∪ {l_x for x with coef < 0}
```

這組 bound 在當前 tableau 線性組合矛盾，Farkas 定理保證 minimal。

## Pivot Rule

哪個 non-basic 優先 pivot？Dutertre 用 **Bland's rule**（按變數 id 最小的）防止 cycling。工業 solver 可用 **largest coefficient rule** 加速，但 Bland 保守正確。

## 整合到 DPLL(T)

```cpp
class LRASolver : public TheorySolver {
    Tableau T;
    BoundStore B;
    std::vector<Undo> undos;

public:
    void assert_lit(Atom a, bool value) override {
        // a 是 (expr ≤ c) 或 (expr < c) 等
        // 轉成 bound 更新
        Bound b = atom_to_bound(a, value);
        B.update(b);
        undos.push_back({B.snapshot()});
    }
    Result check() override { return simplex_check(); }
    /* explain / propagate / push / pop ... */
};
```

## Theory Propagation

Tableau 在 pivot 過程中可以主動推 atom：

- Non-basic `x` 的 bound 固定 → 若 `x` 參與其他 atom（e.g., `x ≤ 10`），可決定它
- Strict/non-strict：若 `x > 3` 且 `x ≤ 3` → UNSAT propagated

LRA solver 的 propagate 有 reach / bound detection 專門邏輯。CaDiCaL 的 LRA 實作這部分有幾千行。

## 動手練習

1. **手算 Simplex**：constraints `x ≥ 0, y ≥ 0, x + y ≤ 4, 2x + y ≥ 3`。Tableau、初始 model、pivot、sol。
2. **Strict vs non-strict**：`x > 0` 和 `x ≥ 0` 的 model 差異。自己推導 `δ` 如何運算。
3. **比對 Z3**：寫幾個 LRA instance 丟 Z3、跟你手算對答案。
4. **Python 版 prototype**：用 `fractions.Fraction` 寫 Python 的 simplified Simplex。200 行內可做出 10 變數 tableau。

## 常見誤解

- **「Simplex 是 LP 才用」** — 錯。SMT 用 *general* Simplex (Dutertre–de Moura)，跟 LP Simplex 是近親但不同演算法。
- **「浮點可以頂替」** — 不行。LRA 要精確 rational，浮點誤差會產生假的 UNSAT/SAT。
- **「Pivot 很慢」** — Amortized 很快。Assert / check 平均 O(n) 對 small instance。
- **「LRA 可以做 LIA」** — 不可以直接。LIA 的整數約束要 branch-and-bound（Ch 28）。

## 自我檢核

- [ ] 懂 LRA 的 fragment（linear real + `+, -, *常數, <, ≤, >, ≥, =`）
- [ ] 說得出 Dutertre–de Moura general Simplex 跟 LP Simplex 的差別
- [ ] 讀得懂 tableau 的 basic / non-basic 表示
- [ ] 懂 pivot 的目的（換 basic/non-basic 角色）
- [ ] 知道 strict inequality 用 delta-trick
- [ ] UNSAT 時能寫出 Farkas-style explanation
- [ ] 知道為什麼要用 rational 而非 float

下一章延伸到 **LIA (Linear Integer Arithmetic)** — 整數版，基於 LRA + 整數約束。由 NP-complete，工業題更常見（陣列 index、counter 等）。

→ [Ch 28 — LIA：branch & bound、Gomory cuts](./28-lia-branch-bound.md)
