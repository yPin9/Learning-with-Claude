# Ch 22 — SMT 全貌與 SMT-LIB v2

> 目標：精確理解 SMT **和 SAT 的差別**、有哪些 theory、SMT-LIB v2 這個業界標準 format 怎麼讀寫、跟 Z3 / cvc5 互動。Part 2 的第一章，所有後續章節都會用這章建立的字彙。

## 一句話分 SAT 和 SMT

**SAT**：變數只是 true/false，沒意義。
**SMT**：變數有**語意**（整數、實數、bit-vector、array、function）；約束用 **theory-specific operator**（`+ < = f(x)`）。

```
SAT:    (x1 ∨ ¬x2 ∨ x3) ∧ (x2 ∨ x4)              ← 純邏輯

SMT:    (x + y > 10) ∧ (x < 5) ∧ (f(x) = f(y))   ← x, y, f 有整數 / function 語意
```

SMT 的求解器叫 **SMT solver**（Z3、cvc5、Yices、MathSAT）。

## SMT-LIB v2 格式

**SMT-LIB** 是 SMT 的 DIMACS — 業界標準輸入 format、統一 benchmark 格式、讓所有 solver 可比較。Current version: **v2.6**（2017）。

### 基本結構

```smt2
(set-logic QF_LIA)                  ; 宣告 logic
(declare-const x Int)               ; 宣告常數 x 是 Int
(declare-const y Int)
(assert (> x 0))                    ; 斷言 x > 0
(assert (< y 0))
(assert (= (+ x y) 10))
(check-sat)                         ; 求解
(get-model)                         ; 若 sat，取 model
(exit)
```

**S-expression 語法** — Lisp 家族。每個 operator 前綴，括號圍起來。

### set-logic

宣告你用的 fragment。**要嚴格** — 放寬 logic 會讓 solver 用更慢的 tactic。

常見 logic：

| Logic | 內容 |
|---|---|
| `QF_UF` | Uninterpreted Function + `=`，無量詞 |
| `QF_LIA` | Linear Integer Arithmetic |
| `QF_LRA` | Linear Real Arithmetic |
| `QF_NIA` | Nonlinear Integer — **undecidable!** |
| `QF_NRA` | Nonlinear Real — decidable (CAD) |
| `QF_BV` | Bit-vector，固定寬度 |
| `QF_ABV` | Array + BV |
| `QF_AUFLIA` | Array + UF + LIA，無量詞 |
| `UFLIA` | UF + LIA 加量詞 |
| `ALL` | 任何東西，solver 自己決定 tactic |

`QF_` 前綴 = quantifier-free，SMT 最常見。

### 宣告類型

```smt2
(declare-const x Int)                         ; 常數 x: Int
(declare-fun f (Int) Int)                     ; 函數 f: Int → Int
(declare-fun P (Int Int) Bool)                ; predicate P: Int × Int → Bool
(declare-fun a () (Array Int Int))            ; array a: Int → Int
```

`declare-const` 是 `declare-fun` 的簡寫（零參數）。

### 常用 operators

**Arithmetic** (`Int`, `Real`)：

```smt2
(+ x y z)          ; variadic
(- x y)            ; 二元減
(- x)              ; 一元負
(* x 2)            ; 乘常數
(< x y) (<= x y) (> x y) (>= x y)
(= x y)
```

**Logic**：

```smt2
(and a b c)
(or a b c)
(not a)
(=> a b)           ; implies
(= a b)            ; Bool 的 = 就是 iff
(ite c a b)        ; if-then-else
(xor a b)
```

**Array**：

```smt2
(select a i)       ; a[i]
(store a i v)      ; a' = a, a'[i] = v
```

**Bit-vector** (`BitVec n`)：

```smt2
(bvand x y)
(bvor x y)
(bvshl x y)
(bvadd x y)
(bvule x y)        ; unsigned less-or-equal
(bvslt x y)        ; signed less-than
((_ extract 7 0) x)  ; 取 bit 0-7
((_ zero_extend 32) x)
```

**Quantifier**：

```smt2
(forall ((x Int)) (>= (* x x) 0))
(exists ((x Int)) (> x 0))
```

### 互動式

```smt2
(assert (> x 0))
(check-sat)                   ; sat
(get-value (x))               ; ((x 1))

(push 1)                      ; 存 snapshot
(assert (< x 0))
(check-sat)                   ; unsat
(pop 1)                       ; 復原

(check-sat)                   ; sat (原來的 assertion 還在)
```

`push`/`pop` 對 solver 很友好 — incremental SAT 底層支援，不重算整個 instance。

### Let binding

```smt2
(assert (let ((t (+ x y))) (= (* t t) 4)))
```

讓公式裡引入 abbreviation。不是 `declare`，只在該 assertion 範圍內有效。SMT 產業用 tool（CBMC）的 output 大量用 `let`、避免公式爆炸。

## Hello SMT 實戰

Ch 0 跑過一次，現在逐段看：

```smt2
(set-logic QF_LRA)
(declare-const x Real)
(declare-const y Real)
(assert (> x 0))
(assert (< y 0))
(assert (= (+ x y) 1))
(check-sat)
(get-model)
```

跑 `z3 hello.smt2`：

```
sat
(
  (define-fun y () Real
    (- (/ 1.0 2.0)))
  (define-fun x () Real
    (/ 3.0 2.0))
)
```

**Z3 還給你有理數、不是浮點**。QF_LRA solver 用精確算術，沒有 floating-point 誤差。

## SMT 和 SAT 的關係圖

```
     SMT formula
        │
        │ abstract (每個 atom 視為 Boolean var)
        ▼
   Boolean skeleton (CNF)
        │
        │ SAT solver 找 Boolean model
        ▼
   boolean assignment
        │
        │ theory solver 檢查語意 consistency
        ▼
    SAT? 給 model
    UNSAT? 解釋 → 新 clause 回 Boolean level
```

**核心思想**：SAT solver 負責 Boolean combinatorics，theory solver 負責語意。兩者來回對話，直到收斂。這就是 **DPLL(T) 架構**（Ch 23）。

## SMT 背後的實作光譜

兩個極端：

### Eager approach

把整個 SMT formula **一次性轉成 SAT**（或 QBF），丟 SAT solver。例：

- QF_BV 可以用 **bit-blasting** 轉 SAT（每個 bit 一個 Boolean 變數）
- QF_UF 可以 **Ackermann-reduce** 成 QF_BV 或 pure SAT

**優點**：重用整個 SAT solver 基礎建設（CDCL + preprocessing）。
**缺點**：某些 theory 的 eager encoding 超大、sometimes 無法 scale。

Boolector (後成為 cvc5 的一部分) 早期用 eager BV。

### Lazy approach (DPLL(T))

SAT solver 跟 theory solver 合作，theory 查詢**只在需要時**。

**優點**：適用於更多 theory、效率更好、能給 theory solver 最大彈性。
**缺點**：實作複雜、theory solver 要支援 `pushpop / backtrack / explain` 等介面。

**現代 SMT solver 都走 lazy**（Z3、cvc5），eager 只在特定 theory 當 tactic 用。

## SMT-LIB 2 的實際生態

- **SMT-LIB benchmark**：按 logic 分類的公開題庫，`https://clc-gitlab.cs.uiowa.edu:2443/SMT-LIB-benchmarks`，幾百 GB
- **SMT Competition**：每年舉辦，按 logic 分類評比
- **SMT-Solvers**：Z3（微軟）、cvc5（Stanford/Iowa）、Yices（SRI）、MathSAT（Trento）、Bitwuzla（BV 專門）
- **工業 user**：CBMC, KLEE, Dafny, F\*, Rust-analyzer, JPF, Spark-Ada, ...

## 從 Z3 命令列到 API

Z3 有 Python、C++、C、Java、.NET 各種 API。**研究和 prototyping 用 Python**：

```python
from z3 import *
x, y = Ints('x y')
s = Solver()
s.add(x > 0, y < 0, x + y == 10)
print(s.check())   # sat
print(s.model())   # [x = 11, y = -1]
```

Python 版 syntax 比 SMT-LIB 順，但產 `*.smt2` 仍有用（portable、可給其他 solver）。

## 動手練習

1. **SMT-LIB 讀寫**：寫底下三題的 SMT-LIB：
   - 找兩整數 `x, y`，`x = 3y + 1` 且 `0 < x < 100`
   - 判斷 `(a → b) ∧ (b → c) ∧ ¬(a → c)` valid（提示：assert negation）
   - Array: 宣告 `a: Int→Int`, 斷 `a[0] = 1 ∧ a[1] = 2 ∧ a[0] ≠ a[1]`
2. **Logic 選擇影響**：第一題改用 `QF_LIA` 和 `QF_LRA` 分別跑，比 solve time。Integer 理論通常比 real 慢。
3. **push/pop 互動**：用 `push/assert/check-sat/pop` 探索約束空間。

## 常見誤解

- **「SMT 只是 SAT 加幾個 operator」** — 錯。SMT 的 theory solver 是獨立系統，CDCL 加入 theory 整個改架構。
- **「SMT-LIB v1 還在用」** — 幾乎沒人用了。v2 是事實標準。
- **「所有 SMT solver 都支援所有 logic」** — 錯。`QF_NRA`、`QF_BV` 有 solver 強、`ALL` 效能通常差。選 solver 看 logic。
- **「QF_NIA 可以解」** — 不完全。Z3 的 NIA tactic 是 incomplete（可能回 `unknown`）。純 NIA 理論上 undecidable（Hilbert 10th problem）。

## 自我檢核

- [ ] 說得出 SAT 跟 SMT 的差（語意 vs 純邏輯）
- [ ] 讀得寫得 SMT-LIB v2 的 `set-logic / declare-const / assert / check-sat / get-model`
- [ ] 認得 QF_UF, QF_LRA, QF_LIA, QF_BV 的涵義
- [ ] 懂 eager vs lazy 兩種 SMT 架構
- [ ] 能用 Z3 Python API 解小題
- [ ] 知道 SMT solver 背後是 SAT solver + theory solver 合作

下一章進入核心 — **DPLL(T) 架構**。理解這一章之後，Part 2 後續所有理論章節（EUF、LRA、LIA、BV、Array、Theory Combination）都是在這個框架裡填東西。

→ [Ch 23 — DPLL(T) 架構](./23-dpll-t-architecture.md)
