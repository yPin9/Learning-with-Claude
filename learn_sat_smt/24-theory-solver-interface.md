# Ch 24 — Theory solver 介面

> 目標：把 Ch 23 的 theory solver 介面精確化 — **assert / check / explain / propagate / push / pop** 這六個方法。這章是 spec，接下來每個理論（EUF、LRA、LIA、BV、Array）都會實作這個介面。讀完你有一份 API contract，往後看論文都能按這個框架讀。

## 介面全貌

```cpp
class TheorySolver {
public:
    // 1. Assert: 通知 solver 一個 atom 被指派真假
    void assert_lit(TheoryAtom atom, bool value);

    // 2. Check: 當前 assertion set 是否 theory-consistent
    enum class Result { SAT, UNSAT };
    Result check();

    // 3. Explain: 產生不一致的最小原因
    std::vector<TheoryAtom> explain_conflict();

    // 4. Propagate: 推出新 atom 必為某值
    struct Propagated { TheoryAtom atom; bool value; };
    std::vector<Propagated> propagate();

    // 5. Push: 備份當前狀態（decision level 加一）
    void push();

    // 6. Pop: 還原到前一個 push 點
    void pop();

    // 7. Model: 給出 SAT 時的 theory model (optional)
    TheoryModel get_model();
};
```

每個方法都有 spec，搞錯一個就是 soundness bug。

## 1. Assert

**Spec**：Solver 記錄「atom = value」這個 assertion，加入當前 context。**不需要立刻檢查 consistency**（留給 `check()`）。

```cpp
// 範例：LRA solver
theory.assert_lit(GreaterThan{x, 0}, true);   // x > 0
theory.assert_lit(LessThan{x, 5}, true);      // x < 5
theory.assert_lit(Equal{x, 3}, false);        // ¬(x = 3) ⇒ x ≠ 3
```

**Assert 應該 O(1) 或 O(log n)**，不要做重計算。把 assertion 塞進 list 就好，真正做事交給 `check()`。

## 2. Check

**Spec**：回傳當前 assertions 是否 theory-consistent。

```cpp
Result r = theory.check();
```

**實作複雜度依 theory**：

- EUF: O(n log n)（congruence closure）
- LRA: O(n³) worst（Simplex），實務 O(n²)
- LIA: NP-complete
- BV: NP-complete (SAT-like)

**Incremental check**：好 solver 的 `check()` 不每次從頭做。維護 state（union-find、tableau），只處理新 assertion。

## 3. Explain (Conflict Generation)

**Spec**：當 `check()` 回 UNSAT，`explain_conflict()` 回一組 atom 使：

1. 它們**都在當前 assertion set 中**
2. 單獨看這組 atom 也 UNSAT
3. **最好是 minimal**（無法再刪任何一個）

```cpp
// Assertions: x > 0, x < 0, y > 5
// check() = UNSAT（x > 0 vs x < 0）
// explain_conflict() = { x > 0, x < 0 }   // minimal
// 回傳 y > 5 也 sound 但 unnecessarily large
```

**Minimality 影響 solver 效能**。Smaller lemma → SAT solver 學更 focused 的 clause → 更快收斂。

### Explain 作為 resolution chain

高品質的 theory explain 等同於 **在 theory 上跑 resolution**：

```
Assertion: a = b, b = c, a ≠ c
Explain should say: a = b and b = c imply a = c, contradicting a ≠ c
Minimal set: {a=b, b=c, a≠c}
```

EUF (Ch 25) 的 congruence closure 能跟 union-find 共享 data structure、O(n log n) 生 explanation。

## 4. Propagate

**Spec**：根據當前 assertions 推出其他 **已知 atom** 的真值。

```cpp
auto props = theory.propagate();
// props 可能回傳 { (x > 3, true), (f(x) = f(y), false) }
// 條件：這些 atom 已在 Boolean abstraction 裡
```

### 重要：只推「已知 atom」

Theory propagation 不是生新 atom，是**決定已知 atom 的真值**。SMT solver 不會動態加新 atom（那會無限）。

範例：

```
SMT formula includes atoms: (x > 0), (x > 3), (x > 10)
Assert: (x > 10) = true
Propagate: (x > 3) = true, (x > 0) = true    ← 推出已知 atom
```

不會推 (x > 5)，因為那不是 formula 裡出現的 atom。

### Propagate 不是必須

Theory solver 可以選擇不 propagate（簡單版），SAT solver 會用 branching 撞出等價效果。**Propagate 是加速，不是 correctness**。

## 5. Push / Pop

**Spec**：SAT solver 在每個 decision level 呼叫 `push()`，backtrack 時呼叫 `pop()`。Theory 要能還原到之前的狀態。

```cpp
theory.push();           // 存 snapshot
theory.assert_lit(...);
theory.assert_lit(...);
auto r = theory.check();
if (r == UNSAT) {
    theory.pop();        // 還原到 push 前
}
```

### 實作技巧：Trail / Undo Stack

不要真的複製整個 state（太貴）。存 **變更記錄**：

```cpp
struct Undo {
    enum Kind { UnionFind, Equality, Bound, ... };
    Kind kind;
    /* fields */;
};
std::vector<Undo> undo_stack;
std::vector<size_t> push_marks;   // push_marks[level] = level 開始時的 stack size

void push() { push_marks.push_back(undo_stack.size()); }
void pop() {
    size_t target = push_marks.back(); push_marks.pop_back();
    while (undo_stack.size() > target) {
        undo(undo_stack.back());
        undo_stack.pop_back();
    }
}
```

這個 undo trail 是每個 theory solver 必有的資料結構。

## 6. Model Generation

**Spec**：當 `check() == SAT`，回一個具體的 theory model — 所有 theory variable 的具體值（Int、Real、BV 等）。

```cpp
// LRA check() returns SAT with:
TheoryModel m = theory.get_model();
// m[x] = 3/2  (Real)
// m[y] = -1/2
```

Model 要滿足所有 assertions（包括 negation — `x ≠ 3` 要真的給一個 `x != 3` 的值）。

### Model construction 跟 solver 相依

- **EUF**：Union-find class + representative，model 給 representative 的值
- **LRA**：Simplex tableau 的 basic/non-basic 解
- **BV**：SAT 的 Boolean model translate
- **Arrays**：lambda 形式的 array 值

Ch 33 完整講 model production。

## 整合到 DPLL(T)

Main loop:

```cpp
while (true) {
    Lit l = pick_branch();          // SAT decision
    sat.enqueue(l);

    // Propagate Boolean
    sat_conflict = sat.propagate();
    if (sat_conflict) { learn + backjump; continue; }

    // Update theory with newly assigned atoms
    for (new_assigned in trail) {
        if (is_theory_atom(new_assigned)) {
            theory.assert_lit(atom_of(new_assigned), sign_of(new_assigned));
        }
    }

    // Check theory
    auto r = theory.check();
    if (r == UNSAT) {
        auto explanation = theory.explain_conflict();
        Clause lemma = negate_all(explanation);
        sat.add_clause(lemma);
        // backjump to the level where lemma is unit
        continue;
    }

    // Theory propagation
    for (auto [atom, value] : theory.propagate()) {
        Lit l = atom_to_lit(atom, value);
        sat.enqueue(l);
    }

    // All SAT variables assigned + theory consistent → SAT
    if (sat.all_assigned()) return SAT;
}
```

Push/pop 插在 SAT 的 `new_decision_level` 和 `backtrack` 裡。

## 先實作哪些介面？

教學順序：

1. **assert + check**：minimal 能 work。Correctness 第一。
2. **push + pop**：incremental，大部分 instance 必要
3. **explain**：沒這個會學 weak lemma，但至少 sound
4. **propagate**：最後加，純加速

Ch 25 EUF 會全部跑一遍。

## Theory solver 的寫作規律

```cpp
class EUFSolver : public TheorySolver {
    UnionFind uf;
    std::vector<std::pair<Term, Term>> equalities;
    std::vector<std::pair<Term, Term>> disequalities;
    std::vector<Undo> undo_stack;
    std::vector<size_t> push_marks;

public:
    void assert_lit(TheoryAtom atom, bool value) override {
        // Record atom in equalities/disequalities
        // Push undo entry
    }

    Result check() override {
        // Run congruence closure
        // Check disequalities against union-find
    }

    std::vector<TheoryAtom> explain_conflict() override {
        // Trace equality chain from union-find
    }

    void push() override { push_marks.push_back(undo_stack.size()); }
    void pop() override { /* undo 到 push_mark */ }
    // propagate() 見 Ch 25
};
```

**這個 template 套到每個 theory**。只有 `check()` 和 `explain_conflict()` 是 theory-specific。

## Soundness vs Completeness

**Soundness**：theory solver 永遠回正確答案（check 說 UNSAT 就真 UNSAT）。**必須**。
**Completeness**：能回 `SAT` 或 `UNSAT`，不會回 `unknown`。**對可決 theory 必須**。

**Incomplete 狀況**：

- Quantifiers：E-matching 是 incomplete
- NRA：有些 solver 用 heuristic、遇難題回 unknown
- NIA：undecidable，必 incomplete

## 動手練習

1. **Skeleton in C++**：建 `mini-smt/theory_interface.hpp`，寫出 pure virtual interface。
2. **Dummy theory**：寫一個 **永遠回 SAT** 的 dummy theory solver。把它嵌到 Ch 11 v1 的 DPLL loop 裡。你應該能 solve 純 Boolean instance，因為 theory 啥都不做。
3. **Mini equality theory**：寫個超簡化 theory：只處理 `=` 和 `≠`，用 union-find。不支援 function application（下章 EUF 才支援）。跑 `x = y ∧ y = z ∧ x ≠ z` 測試。

## 常見誤解

- **「Theory solver 要 complete 才能用」** — 不完全。Incomplete theory 加 CDCL 常 work（SAT solver 用 branching 補）。但容易炸。
- **「Explain 不重要，只要 check 對就好」** — 錯大錯。沒好 explain solver 學的 lemma 含整個 assignment、超大、效率爆差。
- **「Theory solver 要自己處理量詞」** — 看架構。E-matching 在 quantifier handler 層做（Ch 32），純 theory solver 不碰量詞。
- **「Push/Pop 是 optional」** — 對 incremental SAT，必要。沒 push/pop 每次 SAT backtrack 都得從頭重算。

## 自我檢核

- [ ] 記得六個介面方法：assert, check, explain, propagate, push, pop, get_model
- [ ] 懂 explain 的 minimal 要求
- [ ] 懂 theory propagation 只推「已知 atom」
- [ ] 知道 push/pop 要用 undo trail 實作
- [ ] 明白 model 的要求（滿足 所有 assertion，包括 negation）
- [ ] 能識別哪些 theory 是 complete / incomplete

Interface 定義清楚了。下一章進入第一個真實的 theory solver — **EUF**（Equality + Uninterpreted Functions），congruence closure 是它的核心演算法。EUF 是 SMT 最重要的 theory、也是最優雅的之一。

→ [Ch 25 — EUF 與 congruence closure](./25-euf-congruence-closure.md)
