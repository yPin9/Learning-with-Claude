# Ch 33 — Model 與 Proof production

> 目標：SMT solver 最後兩個 output 面向 — **model**（SAT 時給 witness）、**proof**（UNSAT 時給 certificate）。兩者都是 SMT 跟工業應用的 **介面契約**：verifier 不只要 yes/no，要證據。

## SMT Model 的定義

**Model** = 所有變數的具體 assign，讓所有 assertion 為真。

```smt2
(declare-const x Int)
(declare-fun f (Int) Int)
(assert (> x 0))
(assert (= (f x) 5))
(check-sat)  ; sat
(get-model)
```

Z3 output：

```
(
  (define-fun x () Int 1)
  (define-fun f ((!1 Int)) Int
    (ite (= !1 1) 5 0))
)
```

`x = 1`（滿足 `x > 0`），`f` 定義為「input 為 1 時回 5，其他回 0」。滿足 `f(1) = 5`。

## 每個 Theory 的 Model 結構

**EUF**：function 模式
- Union-find class 的代表值
- Uninterpreted function 給 table + default

```
f : ∀x. x == a ? 5 : (x == b ? 3 : 0)
```

**LRA**：Rational number
- Simplex tableau 的 basic / non-basic 解

**LIA**：Integer
- Branch & bound 終止時的 integer assignment

**BV**：Boolean assignment over bits
- Bit-blasting 的 SAT model translate 回 BV

**Array**：const + update chain
- `(store (store (const_array 0) 1 5) 3 7)`

## 多 Theory Model Combination

混合 theory 時，model 要同時滿足所有 theory。範例：

Formula: `x + y = 10 ∧ f(x) = 5 ∧ y > 0`

EUF 要 `f(x) = 5`、LRA 要 `x + y = 10, y > 0`。

**Combined model**：`x = 3, y = 7, f(3) = 5`。兩個 theory 各自滿足，沒衝突。

**真難的情況**：shared variable 的 value 要同時符合兩邊 theory。Nelson–Oppen 的 equality sharing 就是保證這種 consistency。

## Model Validation

**內部 sanity check**：solver 產 model 後，把 model 代回所有 assertion、eval、確認全真。CaDiCaL、Z3 都有 `(set-option :produce-models true)` + 內部 validation。

**Outside validation**：使用者拿 model 自己驗 — Dafny、Verus 這類 verifier 一定要 recheck。因為 SMT solver 有 bug 的時候少，但不是零。

## SMT Proof Production

**UNSAT 的信任問題**：比 SAT 更嚴重。SAT 有 DRAT，SMT 沒統一 proof format。

Z3 有自己的 proof log、cvc5 有 LFSC / Alethe。**沒有 DRAT 的 universal standard**。

### LFSC (Logical Framework with Side Conditions)

cvc4/cvc5 原生格式。proof 是 **dependently-typed λ calculus** term：

```
(%x (Int) (%y (Int) (%H (not (> x y))
  (th_let_pf _
    (rewrite_eq_sym ...)
    ...))))
```

**驗證靠 type-checker**（`lfscc`），確認 proof term well-typed。

### Alethe

Newer proof format (Andreotti et al. 2021)，cvc5 預設輸出。設計目標：**human-readable + machine-checkable**。

```
(assume p1 (= x 5))
(step t1 (= (+ x 1) 6) :rule arith :premises (p1))
(step t2 false :rule contradiction :premises (t1 a3))
```

**每步一 rule + premise**。Isabelle、Coq 有對應 verifier。

### Z3 proof format

二進制 binary、內部用。Z3 有 `(set-option :proof true)` 開 proof log、可轉成其他 format（通過 external tool）。不建議直接用 Z3 proof，**轉成 Alethe 或 LFSC 更通用**。

### SAT-core vs Theory-core

SMT proof 分兩層：

1. **SAT level**：CDCL 的 learned clause → DRAT
2. **Theory level**：theory lemma 的 reason，各 theory 自己的 proof rule
   - EUF: congruence chain
   - LRA: Farkas coefficient
   - LIA: Gomory cut derivation
   - BV: bit-blasting 對應 DRAT

Combined proof = DRAT for Boolean + theory certificates。

## Trusted Kernel

Formal verification 的終極目標：**任何 UNSAT 結論都能被 trusted kernel 機器驗證**。

Trusted kernel 是很小的 proof checker（幾百到幾千行），formally verified 自身正確。通過這個 kernel 的 proof，可信。

SMT 實務：

- **Z3 → Alethe → Isabelle**：Isabelle/HOL 的 verifier 檢查 Alethe proof
- **cvc5 → LFSC → lfscc**：LFSC type checker 檢查
- **CakeML SMT**：formally verified CVC4 subset（研究中）

## Proof 對 workflow 的影響

有 proof 時：

- **Dafny 驗 100 個 method，其中 3 個 SMT solver 有 bug** → proof failed、user 得警告
- **沒 proof** → solver 回 `unsat` 使用者以為對、實際 bug 留下

**工業級 verifier 全部 require proof** — Verus、F\*、lean4/mathlib SMT tactic 都如此。

## Proof 實作成本

對 SMT solver 開發者，proof production 加 **30–50% code**、runtime 慢 **1.5–3×**。所以預設關閉，需要時開啟。

對 theory solver：

- EUF：congruence chain 不太難加（Ch 25 的 proof tree 已是雛形）
- LRA：Farkas coefficient 產生不難
- LIA：Gomory cut 的 chain 要仔細
- BV：每個 bit-blast gate 對應 DRAT clause
- Array：lazy instantiation 記錄哪些 axiom 觸發

## 實作 outline

```cpp
class ProofProducingEUF : public EUF {
    std::vector<ProofStep> proof_log;

public:
    void assert_eq(TermId a, TermId b) override {
        proof_log.push_back({ProofStep::ASSUME, {a, b}});
        EUF::assert_eq(a, b);
    }

    std::vector<ProofStep> get_conflict_proof() {
        auto reasons = EUF::explain_conflict();
        // convert reasons → proof rules
        std::vector<ProofStep> steps;
        for (auto& r : reasons) {
            steps.push_back({ProofStep::CONG, r});
        }
        steps.push_back({ProofStep::CONTRADICTION});
        return steps;
    }
};
```

**每個 check 和 merge 都記錄 proof step**，UNSAT 時把相關 step 組成 proof。

## Model vs Proof 的對偶

| 面向 | Model | Proof |
|---|---|---|
| 對應狀態 | SAT | UNSAT |
| 內容 | 變數 assignment | Inference chain |
| 驗證 | 代回 eval | 規則檢查 |
| 實作難度 | 中（theory 各有做法） | 難（每個 rule 要記錄） |
| 用處 | 使用者看 witness | Formal verifier 信任 |

## 動手練習

1. **Z3 get-model**：寫 5 個不同 logic 的 SAT instance，用 `(get-model)` 看各 theory 的 model 結構。
2. **Proof log 觀察**：Z3 `(set-option :proof true)` + `(check-sat)` 後 dump proof，看 inference rule 名稱。
3. **cvc5 Alethe**：`cvc5 --proof-format=alethe ...`，跑 UNSAT instance，讀 proof。
4. **Model validation**：用 Python 把 Z3 給的 model 代回 SMT、eval 確認滿足所有 assertion。

## 常見誤解

- **「SMT proof 是 DRAT」** — 不是。SMT proof 是 SAT DRAT + theory certificate 的混合，不是單一 format。
- **「Trusted kernel 能驗證所有 solver」** — 只能驗證「有 proof output 的」solver。Z3 沒有 proof mode的 tactic 就不能 check。
- **「Model 都是具體 value」** — 對 EUF 和 function、model 可能是 function definition（不只 value）。
- **「Proof 讓 solver 慢可以忽略」** — 3× slowdown 對 interactive 工具有影響。Verus 用 proof-only-on-demand 策略。

## 自我檢核

- [ ] 懂 SMT model 的結構（每 theory 不同）
- [ ] 知道 combined theory 的 model 怎麼湊
- [ ] 知道 LFSC 和 Alethe 是 SMT 的兩個主流 proof format
- [ ] 理解 trusted kernel 的概念
- [ ] 知道為什麼 proof production 讓 solver 慢 1.5–3×
- [ ] 能用 `(get-model)` 和 proof log 觀察 solver 內部

Part 2 的 theory 講完了。下一個檔案是 **練習 D**（EUF congruence closure 實作）— 把 Ch 25–26 的理論寫成完整可測的 solver。練習 E 把它串進 DPLL(T) 框架。

→ [練習 D — EUF congruence closure](./practice-d-euf.md)
