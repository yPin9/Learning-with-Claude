# Ch 23 — DPLL(T) 架構

> 目標：理解 **DPLL(T)** — 現代 SMT solver 的核心架構。SAT solver 和 theory solver **輪流接手**：SAT 找 Boolean model、theory 檢查語意、有矛盾就產生 theory lemma 丟回 SAT。這章讀懂，Part 2 剩下所有理論章只是填不同的 theory solver。

## 從 Eager 到 Lazy

Ch 22 提過兩條路。**Eager**（一次轉成 SAT）對小 theory 可行（QF_BV 的 bit-blasting），但對有無限 domain 的 theory（Int、Real、UF）會爆。

**Lazy** 架構：SAT solver 不看 theory 細節，只看「atom 是 true 還是 false」。需要時才問 theory solver。

## DPLL(T) 的主角

三個模組：

1. **Boolean Abstraction**：SMT formula 抽象化成 Boolean CNF（每個 theory atom 變成一個 Boolean 變數）
2. **SAT Solver (CDCL)**：Part 1 學的，找 Boolean model
3. **Theory Solver**：在給定 theory-atom 真值下，檢查是否 theory-consistent

### Boolean Abstraction 怎麼做

對 SMT formula 每個 atom（theory-specific），**替換成新 Boolean 變數**：

```
Original:
(x > 0) ∧ ((y < 5) ∨ (f(x) = f(y)))

Boolean abstraction:
b1 ∧ (b2 ∨ b3)

where:
b1 ↔ (x > 0)
b2 ↔ (y < 5)
b3 ↔ (f(x) = f(y))
```

SAT solver 看到的是 `b1 ∧ (b2 ∨ b3)`，不管 `b1, b2, b3` 實際語意。

## DPLL(T) 主循環

```
DPLL(T):
    while true:
        # Phase 1: SAT solver 找 Boolean model
        bool_model ← SAT.solve(boolean_abstraction)
        if bool_model == UNSAT:
            return UNSAT        # Boolean 不行 ⇒ 整個 UNSAT

        # Phase 2: 把 Boolean model 翻回 theory atoms
        theory_constraints ← [lit.theory_atom for lit in bool_model if lit.is_theory]
        result ← T.check(theory_constraints)

        if result == SAT:
            return SAT with theory model

        # Phase 3: theory conflict → 學一條 theory lemma
        lemma ← T.explain()       # 不一致的最小原因
        SAT.add_clause(lemma)
        # 回到 phase 1
```

**兩個 solver 輪流**，直到 SAT 或 UNSAT。**Theory lemma** 是整合的關鍵 — 它把 theory 的語意推理「翻譯」成 SAT solver 看得懂的 clause。

## 範例：從頭走一次

```
SMT formula:
(x > 0) ∧ (x < 0)

Boolean abstraction:
b1 ∧ b2
where b1 ↔ (x > 0), b2 ↔ (x < 0)
```

### Round 1

SAT solver 找到 `b1 = ⊤, b2 = ⊤` satisfy `b1 ∧ b2`.

Translate back: `(x > 0) ∧ (x < 0)`.

Theory solver (LRA): **UNSAT** (x 不可能同正同負).

Theory explanation: 這兩個 atom 本身就矛盾 → lemma = `¬b1 ∨ ¬b2` (i.e., `¬(x > 0) ∨ ¬(x < 0)`).

Add lemma to SAT.

### Round 2

SAT has `b1 ∧ b2 ∧ (¬b1 ∨ ¬b2)` → UNSAT.

Return UNSAT.

## Interface 細節

Theory solver 要實作這個介面（Ch 24 完整講，這裡先 overview）：

```cpp
class TheorySolver {
public:
    // 通知：atom 被 assigned 為 True/False
    void assert(TheoryAtom atom, bool value);

    // 一致性檢查
    enum Result { SAT, UNSAT };
    Result check();

    // 若 check() 回 UNSAT：給 conflict clause
    std::vector<TheoryAtom> explain_conflict();

    // Theory propagation（選配）：可以推出新 atom 為 T/F 嗎？
    std::vector<std::pair<TheoryAtom, bool>> propagate();

    // Backtrack 支援
    void push();
    void pop();
};
```

## Lazy vs Eager Theory Check

### Eager

SAT solver 每次 assign 完所有 Boolean 變數就 check theory consistency。實作簡單、檢查少次。

### Lazy (true DPLL(T))

SAT solver **每一步** propagate 後就問 theory：「目前 assignment consistent 嗎？」 Theory solver 甚至可以倒推 Boolean assignment（**theory propagation**）。

```
SAT assigns b1 = T, propagate:
    b2 becomes unit → decide
    ... later ...
    SAT checks with theory: "consistent so far?"
    Theory replies: "hmm, given (x > 0) ∧ (x < 5), we can further imply x < 3"
    → theory propagates (x < 3), back to SAT
```

**Lazy 是現代 SMT solver 主流**。Theory 和 SAT 的交互可細緻到每條 propagation。

## Theory Propagation

Theory solver 不只被動回答「consistent 嗎」，還**主動推導**：

```
Current theory assignment: (x > 5) ∧ (x > 10)
Theory deduces: (x > 20) is inconsistent  → push to SAT as propagated fact
                (x > 7) is implied         → propagate True
```

這讓 SMT solver 的 **unit propagation 包含 theory reasoning**，避免不必要的 Boolean branching。

## 如何建 Boolean Abstraction

遞迴下降：

```cpp
// abstraction: theory_atom → Boolean var index
Map<Atom, int> atom_to_var;

Lit abstract(Formula f) {
    if (f is theory atom) {
        if (atom_to_var has f) return atom_to_var[f];
        int v = new_boolean_var();
        atom_to_var[f] = v;
        var_to_atom[v] = f;
        return v;
    }
    if (f == (a AND b)) return and_clause(abstract(a), abstract(b));
    if (f == (a OR b))  return or_clause(abstract(a), abstract(b));
    ...
}
```

SAT solver 處理抽象出來的 Boolean formula（Tseitin 化成 CNF，Ch 4），theory 只在 check 時看 atom。

## Conflict Learning

Theory explanation (lemma) 加到 SAT solver。**Lemma 是 SAT clause**，會進 VSIDS / watched literals / clause deletion 機制。

```
Theory says UNSAT because of:
    (x > 0) ∧ (x + y = 0) ∧ (y > 0)   ← 三個 assertion 不能同時成立

Theory lemma (as Boolean):
    ¬b1 ∨ ¬b2 ∨ ¬b3

This is what SAT solver sees and learns.
```

好的 theory solver 給 **minimal explanation** — 不一致所需的最少 atom 集合。Ch 25 EUF 的 congruence closure 就有專門的 explanation 機制。

## 不同 Theory 的 DPLL(T) 適配度

| Theory | DPLL(T) 匹配度 | 備註 |
|---|---|---|
| EUF | ★★★★★ | Perfect — congruence closure 天生支援 explain / propagate |
| LRA | ★★★★★ | Dutertre-de Moura Simplex 專門設計 |
| LIA | ★★★★ | LRA + branch-and-bound，稍慢 |
| QF_BV | ★★★ | 可以 DPLL(T) 但 bit-blast 常勝 |
| Arrays | ★★★★ | Lazy axiom instantiation |
| Quantifiers | ★★ | E-matching 是 heuristic，incomplete |
| NRA | ★★ | CAD 是 heavy 算法、不天生 lazy-friendly |

理論的 lazy-friendliness 取決於：

- 能否快做 consistency check
- 能否快生 explanation
- Backtracking 成本

## 實作：minimal DPLL(T)

骨架（pseudo-code）：

```cpp
class SMTSolver {
    SATSolver sat;
    TheorySolver theory;
    Map<int, Atom> var_to_atom;

public:
    bool solve(Formula smt_formula) {
        CNF cnf = boolean_abstract(smt_formula);
        sat.init(cnf);

        while (true) {
            auto bool_model = sat.solve();
            if (!bool_model) return false;  // SAT solver 說 UNSAT

            // Pass Boolean assignment to theory
            theory.reset();
            for (Lit l : bool_model.assignment) {
                int v = var_of(l);
                if (var_to_atom.count(v)) {
                    bool truth = sign_of(l);
                    theory.assert(var_to_atom[v], truth);
                }
            }

            auto theory_result = theory.check();
            if (theory_result == SAT) return true;

            // Theory conflict → add lemma
            auto explanation = theory.explain_conflict();
            Clause lemma;
            for (auto [atom, val] : explanation) {
                Lit l = atom_to_lit(atom, !val);  // negate
                lemma.push_back(l);
            }
            sat.add_clause(lemma);
        }
    }
};
```

**實際上更精緻** — lazy theory propagation、theory-aware restart、正確 push/pop。Ch 24 細拆。

## Pure SAT vs DPLL(T) 效能

| 操作 | Pure SAT | DPLL(T) |
|---|---|---|
| Unit propagation | 線性 | 線性 + theory propagation |
| Conflict analysis | Resolution 步 | Resolution + theory explanation |
| Clause learning | 標準 | 包含 theory lemma |
| Preprocessing | Ch 17 | + theory-specific（e.g., LRA 的 equality substitution） |
| Complexity | NP-complete | NP-complete + theory cost |

**額外成本來自 theory**，但整體仍然 exponential worst case。

## 動手練習

1. **Boolean abstraction 手做**：將 `(x > 0 ∧ y < 5) ∨ (f(x) = f(y) ∧ x < 10)` 抽象化，寫出 atom-to-var map、Boolean CNF。
2. **模擬 DPLL(T) 輪次**：用第一個例子 `(x > 0) ∧ (x < 0)`，用 paper 算一次 SAT + theory + lemma 三個 phase。
3. **讀 Z3 trace**：`z3 -trace test.smt2` 印詳細內部流程，觀察 SAT conflict 和 theory conflict 交替。
4. **MiniSMT 專案起步**：建 `learn_sat_smt/mini-smt/`，寫 `SATSolver` + `TheorySolver` interface，留空實作 — Ch 24 後就有內容填。

## 常見誤解

- **「Theory solver 就是個 decision procedure」** — 半對。除了判 SAT/UNSAT，它還要能 **explain**、**propagate**、**backtrack**。介面比 decision procedure 複雜。
- **「SAT solver 要改很多」** — 不多。CDCL 只需要加 hook 給 theory。MiniSat 的 `analyzeFinal()` 和 `conflict` callback 就能改成 DPLL(T)。
- **「DPLL(T) 比 eager 永遠好」** — 不一定。QF_BV 的 bit-blasting 在 hardware verif 常贏 lazy。取決於 theory 和 instance。

## 自我檢核

- [ ] 懂 Boolean abstraction 是什麼
- [ ] 寫得出 DPLL(T) 的主循環偽代碼
- [ ] 說得出 theory solver 的四個核心介面（assert / check / explain / propagate）
- [ ] 理解 theory lemma 如何學回 SAT solver
- [ ] 知道 lazy vs eager 的 trade-off
- [ ] 能列舉不同 theory 對 DPLL(T) 的適配度

下一章細拆 theory solver 的四個介面需求。之後每個理論章（EUF / LRA / LIA / BV / Array）都會對應實作這個介面 — **DPLL(T) 是骨架、每個理論是填進去的模組**。

→ [Ch 24 — Theory solver 介面](./24-theory-solver-interface.md)
