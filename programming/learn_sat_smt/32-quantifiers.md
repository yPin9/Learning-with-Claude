# Ch 32 — Quantifiers：E-matching、MBQI

> 目標：處理 SMT 最艱難的部分 — **`∀` / `∃` 量詞**。FOL 整體 undecidable，SMT 用 heuristic：**E-matching**（syntactic pattern-based instantiation）和 **MBQI**（Model-Based Quantifier Instantiation）。兩者都 **incomplete**，但解了工業 verification 大部分的量詞題。

## 量詞的挑戰

量詞一加，FOL 整個變 undecidable（Ch 7 講過）。具體難在：

- `∀x. φ(x)` 的 `x` 可取 infinitely many values
- Solver 無法 enumerate 所有 instantiation
- 需要猜哪些 instantiation 有用

範例：

```smt2
(assert (forall ((x Int)) (>= (* x x) 0)))   ; square is non-negative
(assert (= (* y y) -1))                       ; y² = -1
(check-sat)
```

人類立刻看出矛盾（任何整數平方 ≥ 0，但 y² = -1）。Solver 要會：

1. 把 `y` 代入 `∀x. x² ≥ 0` → `y² ≥ 0`
2. 跟 `y² = -1` 結合 → `-1 ≥ 0` UNSAT

**關鍵**：solver 知道要用 `y` 當 `x` 的 instantiation。但 domain 無限，不能全試。

## E-Matching：Syntactic Pattern

**E-matching** (Detlefs, Nelson, Saxe 2005) 用 **trigger / pattern** 指導 instantiation。

```smt2
(assert (forall ((x Int))
    (! (>= (* x x) 0)
       :pattern ((* x x)))))    ; trigger is (* x x)
```

`:pattern ((* x x))` 告訴 solver：**formula 裡出現 `(* t t)` 形式時，就 instantiate `x = t`**。

SMT tool 看到 `(* y y)` → match pattern `(* x x)` with `x = y` → instantiate `(* y y) ≥ 0` 加進 formula → SAT solver 得到矛盾。

## E-graph 做 matching

E-matching 在 **E-graph** 上進行（EUF 的 congruence closure + term DAG）。

- Term DAG 存所有 term
- E-graph 加 congruence information
- Pattern match 用 **congruence-closure-aware** matching — 跟 union-find 的 class 比對、不只 syntax

```
Pattern:  f(x, g(x))
Target term:  f(a, g(b))
Match if:  a ~ b  (同 class)
```

一般 syntactic match 會 fail，**但 E-matching 考慮 congruence**。這讓 match 捕捉更多 case。

### Matching algorithm

```cpp
// 對 pattern p 搜尋 E-graph 找所有 match
std::vector<Substitution> e_match(Pattern p, EGraph eg) {
    // Pattern 是 term with quantified variables
    // 走過 E-graph 所有相關 class、產 substitution
}
```

Simmons (1998)、Moskal (2008) 的 **compilation-based E-matching** 把 pattern 編成一個 code，執行效率 > 10× naive。

## Trigger 選擇

**每個 `∀` quantifier 可以手動給 `:pattern`** (multi-pattern 也可)。Solver 預設的 **auto-trigger** 從 quantifier body 自動找 trigger，但常找錯。

**寫好的 trigger 對 Z3 效能影響 10×–1000×**。工業使用者（Dafny、Verus、F\*）花大量時間 tune trigger。

### 多 trigger

單 trigger 可能漏、多 trigger 補：

```smt2
:pattern ((f x) (g x))   ; 要求同時出現 (f t) 和 (g t) 才 match
:pattern ((h (f x)))     ; alternative trigger
```

## E-matching 的 Incompleteness

E-matching 永遠找得到 finitely many instantiation。如果 SAT 需要 **infinitely many**（例 `∀x. P(x)` 真的要對每個 Int 驗），E-matching 無法。**這就是 E-matching 的 incompleteness**。

實務絕大多數 case E-matching 找夠 — 因為 formula 裡 term 數量有限、相關 instantiation 也有限。

## MBQI：Model-Based Quantifier Instantiation

**MBQI** (Ge, de Moura 2009) 的不同路線：

1. 先 **假設 quantifier 為真**，solve formula 的 ground part
2. 若 ground sol 存在，**檢查 quantifier 是否真的成立**
3. 不成立 → 從 **counter-model** 抽 instantiation 加到 formula
4. 重複

```
Initial model attempt: x = y = 0
Check: ∀t. f(t) ≠ 0 → solver builds model where f ≡ 0
Counter-example detected: f(0) = 0 contradicts f(a) ≠ 0
Add instantiation: f(0) ≠ 0
Re-solve
```

### MBQI 對某些 fragment complete

**Array property fragment**（Ch 30）、**BV quantified**（有時）MBQI complete。**一般 FOL** 不 complete — counter-model 可能無法有限表達。

Z3 的 `(set-option :smt.mbqi true)` 開 MBQI。**非 E-matching 能處理的題目 MBQI 常搞定**。

## Skolemization 回顧

Ch 7 提過。`∃x. φ(x)` → `φ(sk)`（引入 Skolem 常數）。`∀y. ∃x. φ(x, y)` → `∀y. φ(sk(y), y)`（Skolem 函數）。

SMT solver 一律先 skolemize，所以只處理 `∀`。Skolem functions 變 EUF 的 uninterpreted function。

## Quantifier Elimination

特定 fragment **可以完全消量詞**：

- **Presburger arithmetic**（含 `∀`, `∃` 的 LIA）：Cooper's procedure
- **LRA with quantifiers**：Fourier–Motzkin elimination
- **Algebraically closed fields**：Tarski's theorem

**QE 給出等價 quantifier-free formula**，之後純 QF solver 處理。Z3 的 `qe` tactic 做這件事，適合小 quantifier block。

## Finite Model Finding

某些 instance 保證有 **finite model**（如 bounded integer programming）。這類 instance 可用 **finite model finder**：

- 固定 domain size `k`
- Enumerate model
- 用 SAT 檢查

Paradox (2003)、cvc5 的 **fmf** tactic。對小 domain 的 quantified 問題比 E-matching + MBQI 都好。

## Quantifier 的 incompleteness 管理

SMT 的量詞處理大多 **incomplete**，所以 solver 會回 `unknown` 而非 SAT/UNSAT。實務應對：

- **Timeout**：設上限、超過回 unknown
- **多 tactic**：E-matching + MBQI + finite model 排隊試
- **手 tune**：使用者加 `:pattern`、`:weight`（priority）

Dafny / F\* 等工業 tool 有一整套 **quantifier 管理** code，專門處理 Z3 回 unknown 的情況。

## 範例：完整 Dafny-style verification

```smt2
; forall i, j in [0, n): sorted(a, i) and i < j → a[i] ≤ a[j]
(set-logic AUFLIA)
(declare-fun a (Int) Int)
(declare-const n Int)
(assert (forall ((i Int) (j Int))
    (! (=> (and (<= 0 i) (< i j) (< j n))
           (<= (a i) (a j)))
       :pattern ((a i) (a j)))))
; query: does sorted array contain duplicates?
(assert (exists ((i Int) (j Int))
    (and (<= 0 i) (< i j) (< j n) (= (a i) (a j)))))
(check-sat)
```

複雜 verification problem。Dafny 這類工具產這種 SMT、靠 Z3 的 E-matching 秒答。

## 動手練習

1. **Trigger 試玩**：寫一個 `∀x. P(x)` 量詞 formula、加不加 `:pattern` 看時間/可否 solve。
2. **E-matching vs MBQI**：`(set-option :smt.mbqi true)` 開 MBQI，跑 quantified LIA instance 對比時間。
3. **Incompleteness 範例**：造一個 Z3 會回 `unknown` 的 quantified formula，看 pattern / MBQI 如何影響。
4. **Cooper's procedure**：手推 `∀x. (x > 3 → ∃y. x + y = 10)` 的 QE（Int domain）。

## 常見誤解

- **「Solver 自動找最佳 pattern」** — 錯。Z3 的 auto-trigger 經常挑錯，結果 timeout 或 unknown。
- **「E-matching 完備」** — 不完備。某些 instance E-matching 找不到 instantiation。
- **「MBQI 比 E-matching 好」** — 看 instance。MBQI 某些 fragment 完備，但 overhead 大、小題 E-matching 贏。
- **「量詞一開就變 undecidable」** — 量詞 fragment 有些可決，如 Presburger。

## 自我檢核

- [ ] 懂 E-matching 的概念（trigger + pattern）
- [ ] 寫得出 `:pattern` 語法
- [ ] 懂 MBQI 的概念（counter-model-based）
- [ ] 知道 Skolemization 把 `∃` 消掉
- [ ] 知道 QE 在 Presburger / LRA 可用
- [ ] 理解 quantified SMT 為何常回 `unknown`
- [ ] 能估計 Dafny / F\* 這類 verifier 如何用 SMT 驗證

下一章收尾 SMT 核心：**Model production** 和 **Proof production**。Model 告訴你 SAT 的 witness、Proof 證明 UNSAT 的正確性。兩者都是 SMT 跟工業應用的介面。

→ [Ch 33 — Model 與 Proof production](./33-model-proof.md)
