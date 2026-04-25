# 練習 E — DPLL(T) 骨架串 EUF

> 目標：把練習 D 的 EUF solver **接到** SAT solver 上、組成最小可跑 mini-SMT for QF_UF。讀 SMT-LIB 子集、output `sat / unsat`、(get-model) 給 EUF model。完成後你會看到 SMT 的全貌如何在你手上跑起來。

## 任務規格

| 項目 | 規格 |
|---|---|
| 輸入 | SMT-LIB v2 子集 (QF_UF only) |
| Logic | `QF_UF` |
| 支援 syntax | `set-logic / declare-sort / declare-fun / declare-const / assert / check-sat / get-model / exit` |
| 支援 atom | `=` 和 `not =`，no other predicate |
| 支援 logic ops | `and / or / not / =>` |
| SAT engine | 練習 C 的 mini-CDCL（或 Ch 16 v2） |
| Theory engine | 練習 D 的 EUF |
| Output | SMT-LIB 標準格式 (`sat`, `unsat`, model) |

## 架構圖

```
        SMT-LIB input
              │
              ▼
       SMT-LIB parser
              │
              ▼
        AST (Formula)
              │
   Boolean abstraction
              │
   ┌──────────┴──────────┐
   │                     │
SAT solver         EUF solver
(Boolean CNF)      (atom map)
   │                     │
   └──────────┬──────────┘
              │
        DPLL(T) loop
              │
              ▼
         sat / unsat
```

## 實作步驟

### Step 1: SMT-LIB Parser

子集，**不要**寫完整 parser。Token 化 + S-expression parser：

```cpp
struct SExpr {
    enum { Atom, List } kind;
    std::string atom;        // "=", "x", "and", ...
    std::vector<SExpr> children;
};

SExpr parse_sexpr(std::istream& in);    // 標準 Lisp parser
```

然後 SMT-LIB 命令逐個 dispatch：

```cpp
void execute(SExpr cmd) {
    if (cmd.children[0].atom == "set-logic") { /* check QF_UF */ }
    if (cmd.children[0].atom == "declare-fun") { /* register fn */ }
    if (cmd.children[0].atom == "assert") { add_assertion(parse_formula(cmd.children[1])); }
    if (cmd.children[0].atom == "check-sat") { /* solve */ }
    if (cmd.children[0].atom == "get-model") { /* dump */ }
}
```

### Step 2: Formula AST

```cpp
struct Formula {
    enum { Atom, And, Or, Not, Implies, Iff } kind;
    // For Atom: TermId left, right; bool is_eq;  (eq 或 diseq 的 base atom)
    // For others: children
};
```

### Step 3: Boolean Abstraction

每個 atomic equality `(= a b)` 配一個 Boolean variable：

```cpp
std::unordered_map<std::pair<TermId, TermId>, int> atom_to_bvar;
std::unordered_map<int, std::pair<TermId, TermId>> bvar_to_atom;

int abstract_atom(TermId a, TermId b) {
    auto key = std::min(a, b) < a ? std::pair{b, a} : std::pair{a, b};   // canonical
    if (atom_to_bvar.count(key)) return atom_to_bvar[key];
    int v = sat.new_var();
    atom_to_bvar[key] = v;
    bvar_to_atom[v] = key;
    return v;
}

// 把 Formula 遞迴轉成 Boolean structure，
// 然後 Tseitin 轉 CNF
```

### Step 4: DPLL(T) Loop

```cpp
class SMTSolver {
    SatSolver sat;
    EUF euf;
    std::unordered_map<int, std::pair<TermId, TermId>> bvar_to_atom;

public:
    Result solve() {
        while (true) {
            auto bool_result = sat.solve_with_callback([&](TrailEvent e) -> bool {
                // SAT 每 propagate 一個 lit，更新 EUF
                if (bvar_to_atom.count(e.var)) {
                    auto [a, b] = bvar_to_atom[e.var];
                    if (e.value) euf.assert_eq(a, b);
                    else euf.assert_diseq(a, b);
                }
                if (e.kind == NEW_LEVEL) euf.push();
                if (e.kind == BACKTRACK) euf.pop();
                if (e.kind == CHECK) {
                    auto r = euf.check();
                    if (r == EUF::UNSAT) {
                        auto reason = euf.explain_conflict();
                        // 把 reason 翻譯成 SAT lemma
                        Clause lemma;
                        for (auto [a, b] : reason) {
                            int v = atom_to_bvar[{a, b}];
                            lemma.push_back(-v);   // negate
                        }
                        sat.add_clause(lemma);
                        return false;   // SAT 應 backtrack
                    }
                }
                return true;
            });
            if (bool_result == SAT) return SAT;
            if (bool_result == UNSAT) return UNSAT;
        }
    }
};
```

實際上 SAT solver 不用 callback、用 **explicit step API**：

```cpp
sat.start();
while (true) {
    auto step = sat.step();   // returns: PROPAGATED, DECISION, CONFLICT, SAT, UNSAT
    if (step.kind == PROPAGATED || step.kind == DECISION) {
        // notify EUF
        euf.assert_lit(...);
        // periodic check
        if (euf.check() == UNSAT) {
            sat.add_clause(translate_explanation());
            sat.backtrack();
        }
    }
    if (step.kind == SAT) return SAT;
    if (step.kind == CONFLICT && all level 0) return UNSAT;
}
```

### Step 5: Model Output

SAT 時，從 EUF 取 representative：

```cpp
void print_model() {
    std::cout << "(\n";
    for (auto [name, term_id] : declared_consts) {
        TermId repr = euf.find(term_id);
        // 給每個 class 分配一個獨特的 fresh value
        std::cout << "  (define-fun " << name << " () U " << model_value(repr) << ")\n";
    }
    // For uninterpreted functions, output (define-fun ... (ite ...))
    std::cout << ")\n";
}
```

`U` 是 declared sort name。實作上要對每個 class 給 unique value（`@val_0, @val_1, ...`）。

## 測試 SMT-LIB instance

### Test 1: 簡單 SAT

```smt2
(set-logic QF_UF)
(declare-sort U 0)
(declare-fun a () U)
(declare-fun b () U)
(assert (= a b))
(check-sat)
```

Output: `sat`

### Test 2: 簡單 UNSAT

```smt2
(set-logic QF_UF)
(declare-sort U 0)
(declare-fun a () U)
(declare-fun b () U)
(assert (= a b))
(assert (not (= a b)))
(check-sat)
```

Output: `unsat`

### Test 3: Congruence

```smt2
(set-logic QF_UF)
(declare-sort U 0)
(declare-fun a () U)
(declare-fun b () U)
(declare-fun f (U) U)
(assert (= a b))
(assert (not (= (f a) (f b))))
(check-sat)
```

Output: `unsat`

### Test 4: Disjunction (SAT 處理 Boolean 結構)

```smt2
(set-logic QF_UF)
(declare-sort U 0)
(declare-fun a () U)
(declare-fun b () U)
(declare-fun c () U)
(assert (or (= a b) (= b c)))
(assert (not (= a c)))
(check-sat)
```

Output: `sat` (e.g., `a = b ∧ b ≠ c` 滿足)

### Test 5: SMT-LIB benchmark

下載 SMT-LIB 的 `QF_UF/eq_diamond/eq_diamond1.smt2` 等：

```bash
git clone https://github.com/SMT-LIB/QF_UF
./mini-smt QF_UF/eq_diamond/eq_diamond1.smt2
```

跟 Z3 對 5–10 個 instance、結論一致。

## Bonus 挑戰

1. **Theory propagation**：EUF 推出 atom 真值時 push 到 SAT，省去 branching。
2. **Symbol table**：支援 `let` binding。
3. **Print model in SMT-LIB format**：完整 `define-fun` 含 ite tower for functions。
4. **Pre-processing**：對 atom 做 simplification（`a = a` 直接是 true）。
5. **Push/pop 命令**：支援 SMT-LIB 的 `(push)` / `(pop)`，做 incremental SMT。

## 自我檢核

- [ ] SMT-LIB parser 能 parse 5 個 test instance
- [ ] DPLL(T) 主循環正確（SAT 通知 EUF、conflict 翻譯成 lemma 回傳）
- [ ] Push/pop 在 SAT 跟 EUF 間同步
- [ ] 5 個 test SMT-LIB instance 結論跟 Z3 一致
- [ ] SAT 時印 SMT-LIB 格式的 model
- [ ] 至少能跑 SMT-LIB 的 `eq_diamond1.smt2` 等小 benchmark

這個練習做完，你已經有一個 **mini-SMT solver for QF_UF**。Final project 把它擴展支援 QF_LRA、加入 LRA solver 和 Nelson–Oppen 組合。

→ [Ch 34 — 應用：verification、symbolic execution](./34-applications.md)
