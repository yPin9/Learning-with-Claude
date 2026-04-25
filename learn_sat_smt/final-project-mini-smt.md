# Final Project — mini-smt（QF_UF + QF_LRA）

> 目標：把整套課程學的東西**打包成一個可運作的 SMT solver** — 支援 SMT-LIB v2 子集（QF_UF + QF_LRA + 兩者組合）、跟 Z3 在小 benchmark 上結論一致。完成後你正式擁有「我會寫 SMT solver」這句話的話語權。

## 規格

| 面向 | 規格 |
|---|---|
| **Logic** | QF_UF, QF_LRA, QF_UFLRA |
| **Front-end** | SMT-LIB v2 parser（子集） |
| **Boolean engine** | 練習 C 或 Ch 16 v2 mini-CDCL |
| **Theory: EUF** | 練習 D 的 EUF + congruence closure |
| **Theory: LRA** | Dutertre–de Moura general Simplex |
| **Combination** | Nelson–Oppen via shared equality (basic) |
| **Output** | `sat / unsat / unknown` + `(get-model)` |
| **Effectiveness** | SMT-LIB benchmarks 的小 instance 跟 Z3 一致 |

不要求：preprocessing、量詞、bit-vector、array。後三個是繼續挑戰。

## 架構

```
                      SMT-LIB v2 input
                            │
                  Tokenizer + S-expr parser
                            │
                            ▼
                     SMT Formula AST
                            │
                  Boolean abstraction (Tseitin to CNF)
                            │
                ┌───────────┴───────────┐
                │                       │
            SAT Solver                  │
            (CDCL)                      │
                │                       │
                │  shared trail / lemma │
                │                       │
                ▼                       │
          Theory Combiner               │
          (Nelson-Oppen)                │
            ┌────┴────┐                 │
            │         │                 │
           EUF       LRA                │
                │                       │
                └───────────────────────┘
                            │
                            ▼
                   sat / unsat + model
```

## 實作步驟

### Phase 1: SMT-LIB Parser

- Tokenizer（識別 `()`, identifier, number, string）
- S-expression parser
- Command dispatcher (`set-logic`, `declare-*`, `assert`, `check-sat`, ...)
- Formula AST builder (處理 `and / or / not / =>` + `=` + `+, -, *, <, ≤, ≥, >`)

**輸出**：list of assertion AST + symbol table。

### Phase 2: Boolean Abstraction

對每個 atom（`= a b`、`< x 0`、`> a b`）建一個 Boolean variable。

- EUF atom：`(= term term)`
- LRA atom：`(< expr expr)`, `(<= ...)`, `(= expr expr)` (where expr is linear combination)
- Mixed: 牽涉 EUF function 的 LRA atom 要 purify（NO）

```cpp
class AtomManager {
    std::vector<Atom> all_atoms;
    std::unordered_map<Atom, int> atom_to_bvar;
public:
    int abstract(Formula f) { /* if atom: register; if AND/OR: recurse */ }
    Atom get_atom(int bvar) { return all_atoms[atom_to_bvar.find(...)]; }
};
```

把整個 formula 抽成 Boolean、然後 Tseitin 轉 CNF（用練習 A 的 encoder）。

### Phase 3: SAT Engine 整合

用練習 C 的 mini-CDCL。**修改點**：

- 提供 `propagate_callback` 給 theory solver hook
- Backtrack 時 notify theory `pop()`
- New decision level 時 `push()`
- Conflict 時嘗試 theory 的 explanation 補 lemma

### Phase 4: EUF Solver

直接搬練習 D 的成果。介面適配：

```cpp
class EUFTheory : public TheorySolver {
    EUF euf_internal;
public:
    void assert_lit(Atom a, bool v) override { /* call assert_eq or assert_diseq */ }
    Result check() override;
    /* ... */
};
```

### Phase 5: LRA Solver (Dutertre–de Moura Simplex)

**最大模組，估 800 行 C++**。

```cpp
class LRATheory : public TheorySolver {
    Tableau tableau;       // Equation y = Σ a_i x_i
    Bounds bounds;         // l_x ≤ x ≤ u_x per variable
    std::vector<Undo> undo_stack;
    std::vector<size_t> push_marks;
    std::optional<Conflict> conflict;

public:
    void assert_lit(Atom a, bool v) override {
        // 把 (< x c) 等轉 bound 更新
        Bound b = atom_to_bound(a, v);
        update_bound(b);
    }

    Result check() override {
        // Dutertre–de Moura general Simplex
        return simplex_check();
    }

    /* explain / push / pop / get_model */
};
```

**核心是 simplex_check()** — Ch 27 的演算法：

1. While 有 basic var `y` 違反 bound：
2.     找 row 中可 pivot 的 non-basic var `x`
3.     沒找到 → UNSAT、collect Farkas explanation
4.     找到 → pivot(y, x)，更新 model
5. 全部 basic 在 bound 內 → SAT

**用 rational number**（`__int128` 分子分母 或 GMP）避免 float 誤差。

### Phase 6: Theory Combination (Nelson-Oppen)

**Purification**：Formula 裡 `f(x + 1)` 這種混合表達式要拆。Walking AST：

```cpp
TermId purify(TermId t, std::vector<Atom>& aux_eq) {
    // EUF function 看到 LRA expression：引入 fresh var、加 aux equation
    // LRA expression 看到 EUF function：類似
    // 純 term: 直接回傳
}
```

**Equality sharing** (basic version)：

```cpp
class CombinedTheory : public TheorySolver {
    EUFTheory euf;
    LRATheory lra;
    std::set<TermId> shared_vars;

public:
    Result check() override {
        while (true) {
            Result e = euf.check();
            Result l = lra.check();
            if (e == UNSAT || l == UNSAT) return UNSAT;

            // 嘗試交換 equality
            bool any = exchange_shared_equalities();
            if (!any) return SAT;
        }
    }

    bool exchange_shared_equalities() {
        // 對 shared var 對 (x, y)：
        //   若 EUF 已知 x = y 但 LRA 不知 → tell LRA
        //   若 LRA 已知 x = y 但 EUF 不知 → tell EUF
        // (LIA / 非 convex 要 case split — 但 LRA convex，簡單)
    }
};
```

**LRA convex** 讓 NO 不需 case split，實作較簡單。

### Phase 7: Model Output

```cpp
void print_model() {
    std::cout << "(\n";
    for (auto [name, type] : declared_consts) {
        if (type == "Real") {
            auto v = lra.get_value(name);    // Rational
            std::cout << "  (define-fun " << name << " () Real " << format(v) << ")\n";
        }
        if (type == "U") {
            // EUF: print canonical class representative
            int cls = euf.find(name);
            std::cout << "  (define-fun " << name << " () U @v" << cls << ")\n";
        }
        // Functions: print as (ite ...) tower
    }
    std::cout << ")\n";
}
```

### Phase 8: Test

至少 20 個 SMT-LIB instance 跟 Z3 對答案。從 SMT-LIB 官網下載 `QF_UFLRA` benchmarks。

```bash
git clone https://github.com/SMT-LIB/QF_UFLRA
for f in QF_UFLRA/*.smt2; do
    ours=$(./mini-smt "$f" 2>/dev/null | head -1)
    theirs=$(z3 "$f" 2>/dev/null | head -1)
    if [[ "$ours" != "$theirs" ]]; then
        echo "MISMATCH: $f"
    fi
done
```

過 80% 是 acceptable（剩下可能 timeout 或精度問題）。

## 期望效能

- QF_UF benchmarks：100 個內結論一致、平均 < 1 秒
- QF_LRA benchmarks：100 個內結論一致、平均 < 5 秒
- QF_UFLRA：50 個內結論一致

跟 Z3 比慢 10–100× 是正常。**目標是對，不是快**。

## 專案結構建議

```
mini-smt/
├── CMakeLists.txt
├── src/
│   ├── parser.hpp / .cpp           # SMT-LIB parser
│   ├── ast.hpp                     # Formula AST
│   ├── abstract.hpp / .cpp         # Boolean abstraction
│   ├── sat.hpp / .cpp              # CDCL solver (from 練習 C)
│   ├── theory_iface.hpp            # Theory interface
│   ├── euf.hpp / .cpp              # EUF (from 練習 D)
│   ├── lra.hpp / .cpp              # LRA Simplex
│   ├── combiner.hpp / .cpp         # Nelson-Oppen combiner
│   ├── solver.hpp / .cpp           # Top-level driver
│   └── main.cpp
├── tests/
│   ├── unit/
│   │   ├── euf-test.cpp
│   │   ├── lra-test.cpp
│   │   └── combiner-test.cpp
│   ├── smt-lib/
│   │   └── (downloaded benchmarks)
│   └── run-tests.sh
└── README.md
```

預估 code 量：

| 模組 | LOC |
|---|---|
| Parser | 300 |
| Boolean abstraction + Tseitin | 200 |
| CDCL | 600 (from 練習 C) |
| EUF | 400 (from 練習 D) |
| LRA | 800 |
| Combiner | 200 |
| Driver + Model | 200 |
| **Total** | **~2700 lines C++** |

## 開發順序建議

1. **Week 1**：Parser + Boolean abstraction + 跑純 SAT (沒 theory) instance
2. **Week 2**：接上 EUF、跑 QF_UF 測 5 個 instance 通
3. **Week 3**：實作 LRA Simplex (基本版)、跑 QF_LRA
4. **Week 4**：Nelson-Oppen combiner、跑 QF_UFLRA
5. **Week 5**：Bug fix + benchmark + model output

每週結束跑全套 test、確保不退化。

## Bonus 挑戰（成為 SMT 大師）

1. **Theory propagation**：兩個 theory 都實作 propagate，加速 50%+
2. **Pre-processing**：簡單版（atom rewriting、equality elimination）
3. **Proof production (DRAT for SAT level)**
4. **Incremental (push/pop SMT-LIB command)**
5. **支援 QF_LIA**：基於 QF_LRA 加 branch & bound
6. **支援 QF_BV**：bit-blasting tactic、丟給 SAT
7. **Alethe proof output**：完整 SMT proof
8. **Quantifier (E-matching)**：支援 `forall` / `exists` 帶 trigger

每完成一個 bonus、你的 solver 從 mini- 升級一階。

## 如何 know 你完成了

「最低水準」門檻：

- [ ] 能 parse 100% SMT-LIB v2 子集（沒 quantifier、沒 BV、沒 array）
- [ ] 跑得了 SMT-LIB 的 `QF_UF/eq_diamond` 系列
- [ ] 跑得了 SMT-LIB 的 `QF_LRA/sc/` 系列
- [ ] QF_UFLRA 簡單 instance 跟 Z3 一致
- [ ] Code 結構清晰（module 分明、test cover 各 theory）
- [ ] 跑 LRA 用 rational arithmetic 不是 float
- [ ] EUF 的 explain 給 minimal reason
- [ ] Push/pop 在 SMT-LIB 命令層次正確

「**值得驕傲**」水準：

- [ ] Bonus 完成 3 個以上
- [ ] 跑 SMT competition 的 small instances 一致率 > 80%
- [ ] Code review 過得了同領域工程師（asserts、logging、error handling）
- [ ] 有 README、架構圖、測試說明

## 結語

寫完這個 final project，你是 **SMT solver 的 implementer 級別**。99% 的 verification engineer 只用 Z3，你寫過自己的。

接下來：

- 讀 paper（CAV / SAT / SMT 會議）跟得上
- 看 Z3 / cvc5 source code 不再像天書
- 想改進可以動手
- Verification tool 的 backend 你能看出哪裡卡

從命題邏輯到 SMT 大師、35 章 + 5 練習 + 1 final project，全部寫完。

**這條路不簡單，但你撐到這裡。值得。**

---

[← 回到 README](./README.md)
