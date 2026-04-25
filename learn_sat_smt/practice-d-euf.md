# 練習 D — EUF congruence closure

> 目標：把 Ch 25–26 的 EUF + congruence closure 寫成 **獨立可測的 theory solver**。介面符合 Ch 24 的 contract（assert / check / explain / push / pop）。完成後它就是 final project 的 EUF 模組。

## 任務規格

| 項目 | 規格 |
|---|---|
| 介面 | `assert_eq(a, b)` / `assert_diseq(a, b)` / `check()` / `explain_conflict()` / `push()` / `pop()` |
| Term 類型 | `Const, Var, App` 三種 |
| 演算法 | Union-find (no path compression) + congruence closure |
| Explain | 真實 proof path，congruence edge 遞迴展開 argument 等式 |
| 效能 | 1000 個 term 的 instance 秒內完成 |
| 必過測試 | 底下 6 組 test case 全 pass |

## 實作步驟建議

### Step 1: Term DAG 與 hash-cons

```cpp
struct Term { TermKind kind; std::string name; std::vector<TermId> args; };
class TermManager { /* hash-cons creation */ };
```

每個 unique term（按 kind + name + args）只創建一次。**hash-cons 對 union-find 的 ID 一致性很重要**。

### Step 2: Union-Find with undo

```cpp
class UnionFindUndo {
    std::vector<int> parent;
    std::vector<int> rank_;
    std::vector<std::pair<int, int>> undo_stack;   // {child_id, old_parent}
public:
    int find(int x);                  // 不做 path compression
    bool union_(int a, int b);        // 做 union by rank、push undo entry
    int snapshot();                    // 回 undo_stack 的 size
    void restore(int mark);           // pop 到 mark
};
```

**為什麼不做 path compression**：path compression 之後 undo 困難。Worst case `find` 從 O(α(n)) 變 O(log n)，可接受。

### Step 3: Use list 與 congruence

```cpp
std::vector<std::vector<TermId>> uses;   // uses[class_repr] = 用到這個 class 的 App term

void register_term(TermId t) {
    if (term is App) {
        for (TermId arg : args) uses[find(arg)].push_back(t);
    }
}

void merge(TermId a, TermId b, /* reason */) {
    // union-find merge
    // 處理 use list (大 class 吸收小 class)
    // 對新 class 的 use list 做 congruence check
    check_congruence_for(merged_class);
}
```

### Step 4: Congruence check via signature

```cpp
void check_congruence_for(int class_id) {
    std::unordered_map<std::string, TermId> sig_to_term;
    for (TermId t : uses[class_id]) {
        std::string sig = compute_sig(t);   // f|find(a1)|find(a2)|...
        if (sig_to_term.count(sig)) {
            TermId other = sig_to_term[sig];
            if (find(t) != find(other)) {
                // 觸發新 congruence、recursive merge
                merge(t, other, /* congruence reason */);
            }
        } else {
            sig_to_term[sig] = t;
        }
    }
}
```

### Step 5: Disequality 檢查

```cpp
std::vector<std::pair<TermId, TermId>> diseqs;

void assert_diseq(TermId a, TermId b) {
    if (find(a) == find(b)) { conflict_ = {a, b}; return; }
    diseqs.push_back({a, b});
}

// 每次 merge 後 check
void check_diseqs_after_merge() {
    for (auto [u, v] : diseqs) {
        if (find(u) == find(v)) { conflict_ = {u, v}; return; }
    }
}
```

優化：merge 兩個 class 時只 check 跟那兩個 class 相關的 diseq。

### Step 6: Explain (proof tree)

每次 merge 在 proof tree 上加一條邊，labelled with reason。Explain `a ~ b` 時找 path。

```cpp
struct ProofEdge {
    TermId other;
    enum { Direct, Congruence } kind;
    std::pair<TermId, TermId> direct_terms;   // for Direct
    std::pair<TermId, TermId> congr_terms;    // for Congruence (兩個 App term)
};
std::vector<std::vector<ProofEdge>> proof_edges;

// Find path from a to b in proof forest
std::vector<TermId> find_path(TermId a, TermId b) { /* BFS */ }

// Traverse path, collect reasons; congruence edges recursively
std::vector<std::pair<TermId, TermId>> explain(TermId a, TermId b) {
    auto path = find_path(a, b);
    std::vector<std::pair<TermId, TermId>> reasons;
    for (each edge along path) {
        if (Direct) reasons.push_back(edge.direct_terms);
        if (Congruence) {
            // 兩個 App term 的對應 argument equality
            for (each arg pair) reasons.append(explain(arg_a, arg_b));
        }
    }
    return reasons;
}
```

### Step 7: Push / pop

```cpp
void push() { undo_marks.push_back(uf.snapshot()); /* 記其他狀態 */ }
void pop() {
    int mark = undo_marks.back(); undo_marks.pop_back();
    uf.restore(mark);
    // 還原 diseqs / use list / proof_edges
    conflict_.reset();
}
```

## 必過 6 組測試

```cpp
// Test 1: 簡單等式
{a, b, c}; assert_eq(a, b); assert_eq(b, c);
assert(check() == SAT);
assert(find(a) == find(c));

// Test 2: 等式矛盾
assert_eq(a, b); assert_diseq(a, b);
assert(check() == UNSAT);

// Test 3: Congruence
{a, b, fa = f(a), fb = f(b)};
assert_eq(a, b); assert_diseq(fa, fb);
assert(check() == UNSAT);

// Test 4: 多 argument congruence
{a, b, c, d, fac = f(a, c), fbd = f(b, d)};
assert_eq(a, b); assert_eq(c, d); assert_diseq(fac, fbd);
assert(check() == UNSAT);

// Test 5: Push/pop
push(); assert_eq(a, b); assert(find(a) == find(b)); pop();
assert(find(a) != find(b));   // 還原成功

// Test 6: Explain minimal
assert_eq(a, b); assert_eq(b, c); assert_eq(c, d); assert_diseq(a, d);
auto exp = explain_conflict();
assert(exp.size() == 4);   // {a=b, b=c, c=d, a≠d}
```

## Bonus 挑戰

1. **Theory propagation**：實作 `propagate()` — 對 atom set 推 atom 真值。
2. **Predicate handling**：把 predicate `P(x)` 轉成 `P(x) = true_term`。讓 `P(a) ∧ ¬P(b) ∧ a = b` 能被 detect。
3. **Nieuwenhuis–Oliveras explain**：O(n log n) explain，比 BFS 快。
4. **Z3 對照**：寫 5–10 個 QF_UF instance、丟 Z3 + 你的 solver、答案要一致。

## 完整參考實作骨架

寫完再看。

<details>
<summary>骨架（~250 行 C++）</summary>

請參考 Ch 26 的完整 code，整理成模組化形式：

- `term.hpp` — TermManager + hash-cons
- `unionfind.hpp` — UF with undo
- `euf.hpp` — main solver
- `tests/euf-test.cpp` — 6 個 test case
- `CMakeLists.txt`

Ch 26 已給出主要 code，這個練習是把它**從 Ch 26 教學版升級到模組化、測試覆蓋完整**的版本。

</details>

## Debug 工具

1. **Print union-find**：`dump_classes()` 印每個 class 的成員。Merge 後 sanity check。
2. **Print proof forest**：把 proof_edges dump 成 DOT format、用 graphviz 畫。
3. **Diff with Z3**：對同一個 instance 比對，不一致就有 bug。

## 自我檢核

- [ ] 6 組必過測試全 pass
- [ ] Use list 正確維護（merge 時搬移、undo 時還原）
- [ ] Disequality check 在 merge 後立刻做
- [ ] Push/pop 100 次後狀態還原
- [ ] Explain 給出 minimal reason set
- [ ] 跟 Z3 對 5+ 個 instance 結論一致

完成後你的 EUF solver 達 final project 標準。下一個練習把它接到 SAT solver、做出 mini-SMT for QF_UF。

→ [練習 E — DPLL(T) 骨架串 EUF](./practice-e-dpll-t-skeleton.md)
