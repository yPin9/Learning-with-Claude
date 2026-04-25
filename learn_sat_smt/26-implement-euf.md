# Ch 26 — 實作 EUF theory solver

> 目標：把 Ch 25 的 congruence closure 寫成 C++：term DAG + union-find with undo + merge + congruence propagation + explain + theory interface。這是你的第一個 theory solver，可以直接嵌到 DPLL(T) 框架跑 QF_UF。

## 專案結構

```
mini-smt/
├── src/
│   ├── theory.hpp           # Ch 24 的 interface
│   ├── term.hpp             # Term DAG 結構
│   ├── euf.hpp              # EUF solver 本體
│   └── ...
└── tests/
    └── euf-tests.cpp
```

## src/term.hpp — Term DAG

```cpp
#pragma once
#include <vector>
#include <string>
#include <unordered_map>
#include <memory>

using TermId = int;   // term 的 DAG 編號

enum class TermKind { Const, Var, App };   // constant / variable / function application

struct Term {
    TermKind kind;
    std::string name;            // for Const / Var / function symbol
    std::vector<TermId> args;    // 只有 App 用
    // DAG 分析資訊
    TermId id;
};

// Term manager：hash-cons 保證相同 term 只有一份
class TermManager {
public:
    TermId mk_const(std::string n) { return intern({TermKind::Const, std::move(n), {}, -1}); }
    TermId mk_var(std::string n) { return intern({TermKind::Var, std::move(n), {}, -1}); }
    TermId mk_app(std::string fn, std::vector<TermId> args) {
        return intern({TermKind::App, std::move(fn), std::move(args), -1});
    }
    const Term& get(TermId id) const { return terms[id]; }
    size_t size() const { return terms.size(); }

private:
    std::vector<Term> terms;
    std::unordered_map<std::string, TermId> name_to_id;   // hash-cons

    std::string serialize(const Term& t) const {
        std::string s = (int)t.kind + std::string("|") + t.name;
        for (TermId a : t.args) s += "|" + std::to_string(a);
        return s;
    }

    TermId intern(Term t) {
        std::string key = serialize(t);
        auto it = name_to_id.find(key);
        if (it != name_to_id.end()) return it->second;
        t.id = terms.size();
        name_to_id[key] = t.id;
        terms.push_back(std::move(t));
        return terms.back().id;
    }
};
```

**Hash-cons** 是 term DAG 的標準做法 — 保證結構相等的 term 共用 id，union-find 操作才能 O(α(n))。

## src/euf.hpp — EUF solver (骨架)

```cpp
#pragma once
#include "term.hpp"
#include <vector>
#include <optional>
#include <cstdint>

class EUF {
public:
    enum class Result { SAT, UNSAT };

    EUF(TermManager& tm) : tm(tm) {}

    void assert_eq(TermId a, TermId b);
    void assert_diseq(TermId a, TermId b);
    Result check();
    std::vector<std::pair<TermId, TermId>> explain_conflict();
    void push();
    void pop();

private:
    TermManager& tm;

    // 每個 term 的 representative
    std::vector<TermId> parent;   // parent[id] = 父節點，root 的 parent = 自己
    std::vector<int>    rank_;    // 用於 union-by-rank

    // Use lists：如果 App term 的 arg 變了，可能觸發 congruence
    std::vector<std::vector<TermId>> uses;   // uses[class_id] = 以這個 class 為 arg 的 App term id

    // 已 assert 的 diseq
    std::vector<std::pair<TermId, TermId>> diseqs;

    // Proof edges：記 merge 原因（為 explain 用）
    struct ProofEdge {
        TermId other;           // 對方節點
        std::pair<TermId, TermId> reason;   // 直接原因 (pair of terms that 被 asserted 等於)
                                            // 若是 congruence，reason 等於被 merge 的 f(...)
        bool is_congruence;
    };
    std::vector<std::vector<ProofEdge>> proof_edges;   // proof_edges[id] = 這個 id 的 proof edges

    // Undo trail
    struct Undo {
        enum Kind { Merge, Diseq, UseList };
        Kind kind;
        int a, b;  // 依 kind 解釋
    };
    std::vector<Undo> undo_stack;
    std::vector<size_t> push_marks;

    // 衝突記錄（check 發現後放這）
    std::optional<std::pair<TermId, TermId>> conflict;

    // === core ===
    TermId find(TermId x) {
        while (parent[x] != x) x = parent[x];
        return x;     // 注意：沒做 path compression（為了 undo-friendly）
    }

    void grow_to(TermId id) {
        while ((int)parent.size() <= id) {
            parent.push_back(parent.size());
            rank_.push_back(0);
            uses.emplace_back();
            proof_edges.emplace_back();
        }
    }

    bool merge(TermId a, TermId b,
               std::pair<TermId, TermId> direct_reason,
               bool is_congruence);

    void check_congruence_for(TermId class_id);
    bool is_congruent(TermId t1, TermId t2);
};
```

**注意**：沒做 path compression。因為 `pop()` 要 undo，compression 破壞結構。代價：find 退化到 O(log n)；換來 push/pop O(undo size)。實務上是好 trade。

## src/euf.cpp — 主體

### assert_eq

```cpp
void EUF::assert_eq(TermId a, TermId b) {
    grow_to(std::max(a, b));
    // 初始化 uses：對新 App term 的 arg 註冊
    // （簡化：假設已提前 build use list）
    if (!merge(a, b, {a, b}, /*is_congruence=*/false)) {
        // 真正的 conflict check 在 check() 裡，這裡只標記
    }
}

void EUF::assert_diseq(TermId a, TermId b) {
    grow_to(std::max(a, b));
    if (find(a) == find(b)) {
        conflict = {a, b};
        return;
    }
    diseqs.push_back({a, b});
    undo_stack.push_back({Undo::Diseq, (int)diseqs.size() - 1, 0});
}
```

### merge (核心)

```cpp
bool EUF::merge(TermId a, TermId b,
                std::pair<TermId, TermId> reason,
                bool is_congruence) {
    TermId ra = find(a), rb = find(b);
    if (ra == rb) return true;

    // Union by rank
    if (rank_[ra] < rank_[rb]) std::swap(ra, rb);
    // rb 成為 ra 的 child
    undo_stack.push_back({Undo::Merge, rb, parent[rb]});
    parent[rb] = ra;
    if (rank_[ra] == rank_[rb]) rank_[ra]++;

    // Record proof edge（兩個方向都記）
    proof_edges[a].push_back({b, reason, is_congruence});
    proof_edges[b].push_back({a, reason, is_congruence});

    // 合併 use list (rb 的 use 搬到 ra)
    for (TermId t : uses[rb]) uses[ra].push_back(t);
    undo_stack.push_back({Undo::UseList, ra, (int)uses[ra].size() - (int)uses[rb].size()});

    // 檢查 disequality violation
    for (auto [u, v] : diseqs) {
        if (find(u) == find(v)) {
            conflict = {u, v};
            return false;
        }
    }

    // 檢查 congruence: 對 ra 的 use list 看有沒有 newly congruent
    check_congruence_for(ra);

    return !conflict.has_value();
}

void EUF::check_congruence_for(TermId class_id) {
    // 用 hash signature 找 congruent pair
    std::unordered_map<std::string, TermId> sig_to_term;
    for (TermId t : uses[class_id]) {
        const Term& term = tm.get(t);
        if (term.kind != TermKind::App) continue;
        std::string sig = term.name;
        for (TermId a : term.args) sig += "|" + std::to_string(find(a));
        if (sig_to_term.count(sig)) {
            TermId other = sig_to_term[sig];
            if (find(t) != find(other)) {
                // 新 congruence！
                merge(t, other, {t, other}, /*is_congruence=*/true);
            }
        } else {
            sig_to_term[sig] = t;
        }
    }
}

bool EUF::is_congruent(TermId t1, TermId t2) {
    const Term& a = tm.get(t1);
    const Term& b = tm.get(t2);
    if (a.kind != TermKind::App || b.kind != TermKind::App) return false;
    if (a.name != b.name || a.args.size() != b.args.size()) return false;
    for (size_t i = 0; i < a.args.size(); i++)
        if (find(a.args[i]) != find(b.args[i])) return false;
    return true;
}
```

### check

```cpp
EUF::Result EUF::check() {
    return conflict.has_value() ? Result::UNSAT : Result::SAT;
}
```

Conflict 早在 `assert_*` / `merge` 時就設了，這裡純 lookup。

### explain_conflict

```cpp
std::vector<std::pair<TermId, TermId>> EUF::explain_conflict() {
    if (!conflict) return {};
    auto [u, v] = *conflict;
    // 找 proof path u → v
    std::vector<std::pair<TermId, TermId>> reasons;
    explain_path(u, v, reasons);
    // Add 這個 disequality 本身
    reasons.push_back({u, v});   // 但要 mark 是 disequality
    return reasons;
}

void EUF::explain_path(TermId a, TermId b, std::vector<std::pair<TermId, TermId>>& out) {
    // BFS 找 proof tree 上 a 到 b 的 path
    std::vector<int> dist(parent.size(), -1);
    std::vector<int> prev(parent.size(), -1);
    std::queue<int> q;
    q.push(a); dist[a] = 0;
    while (!q.empty()) {
        int x = q.front(); q.pop();
        if (x == b) break;
        for (auto& e : proof_edges[x]) {
            if (dist[e.other] == -1) {
                dist[e.other] = dist[x] + 1;
                prev[e.other] = x;
                q.push(e.other);
            }
        }
    }
    // 回溯 path，對每個 edge 收集 reason
    int cur = b;
    while (prev[cur] != -1) {
        int p = prev[cur];
        for (auto& e : proof_edges[p]) {
            if (e.other == cur) {
                if (!e.is_congruence) {
                    out.push_back(e.reason);
                } else {
                    // Congruence: recursively explain each argument pair
                    const Term& t1 = tm.get(e.reason.first);
                    const Term& t2 = tm.get(e.reason.second);
                    for (size_t i = 0; i < t1.args.size(); i++) {
                        explain_path(t1.args[i], t2.args[i], out);
                    }
                }
                break;
            }
        }
        cur = p;
    }
}
```

### push / pop

```cpp
void EUF::push() {
    push_marks.push_back(undo_stack.size());
}

void EUF::pop() {
    size_t target = push_marks.back();
    push_marks.pop_back();
    while (undo_stack.size() > target) {
        auto& u = undo_stack.back();
        switch (u.kind) {
            case Undo::Merge:
                parent[u.a] = u.b;  // 還原 parent
                break;
            case Undo::Diseq:
                diseqs.pop_back();
                break;
            case Undo::UseList:
                uses[u.a].resize(u.b);
                break;
        }
        undo_stack.pop_back();
    }
    conflict.reset();
}
```

## 測試（tests/euf-tests.cpp）

```cpp
#include "term.hpp"
#include "euf.hpp"
#include <cassert>
#include <iostream>

int main() {
    TermManager tm;
    EUF euf(tm);

    // Test 1: a = b, b = c, a = c 應該 SAT
    {
        auto a = tm.mk_const("a"), b = tm.mk_const("b"), c = tm.mk_const("c");
        euf.assert_eq(a, b);
        euf.assert_eq(b, c);
        assert(euf.check() == EUF::Result::SAT);
    }

    // Test 2: a = b, a ≠ b 應該 UNSAT
    {
        EUF euf2(tm);
        auto a = tm.mk_const("a"), b = tm.mk_const("b");
        euf2.assert_eq(a, b);
        euf2.assert_diseq(a, b);
        assert(euf2.check() == EUF::Result::UNSAT);
    }

    // Test 3: congruence
    // a = b, f(a) ≠ f(b) → UNSAT
    {
        EUF euf3(tm);
        auto a = tm.mk_const("a"), b = tm.mk_const("b");
        auto fa = tm.mk_app("f", {a});
        auto fb = tm.mk_app("f", {b});
        // 先把 fa, fb 的 use list 註冊（略）
        euf3.assert_eq(a, b);
        euf3.assert_diseq(fa, fb);
        assert(euf3.check() == EUF::Result::UNSAT);
    }

    std::cout << "All EUF tests passed\n";
}
```

**實際上 use list 註冊**要在 term 建的時候做。簡化版可以在 `assert_eq` 時掃 TermManager 所有 App term、填 use list。Production 版要跟 term creation 同步。

## Theory Interface 包裝

為了接 Ch 23 的 DPLL(T) 框架：

```cpp
class EUFTheoryAdapter : public TheorySolver {
    EUF euf;
public:
    void assert_lit(Atom a, bool value) override {
        if (a.is_equality()) {
            if (value) euf.assert_eq(a.lhs, a.rhs);
            else euf.assert_diseq(a.lhs, a.rhs);
        }
    }
    Result check() override {
        return euf.check() == EUF::Result::SAT ? SAT : UNSAT;
    }
    std::vector<Atom> explain_conflict() override {
        auto reasons = euf.explain_conflict();
        std::vector<Atom> result;
        for (auto [a, b] : reasons) result.push_back(Atom::eq(a, b));
        return result;
    }
    void push() override { euf.push(); }
    void pop() override { euf.pop(); }
};
```

## 常見踩坑

1. **Use list 沒建**：建 App term 時一定要在每個 arg 的 class 加 use entry。忘了就不會觸發 congruence。
2. **Disequality 比對錯**：`a ≠ b` 中 `a`, `b` 可能 merge 到同 class，要用 `find`、不能用 raw id。
3. **Pop 時忘復原 conflict flag**：`conflict.reset()` 一定要在 pop 後做。
4. **BFS 找 proof path 爆炸**：proof tree 大時 BFS 慢，production 用 union-find on proof forest（Nieuwenhuis-Oliveras 2005）。
5. **Memory leak**：undo_stack 不清理會吃記憶體。每 level pop 後清才對。

## 動手練習

1. **打完 code，跑基本測試**：三個測試至少過。
2. **加 congruence 測試**：`f(a, b) = c ∧ a = d ∧ b = e ∧ f(d, e) ≠ c` → UNSAT。
3. **測 pop 正確性**：`push, assert_eq(a, b), check, pop, check` — pop 後 `a = b` 應該失效，`find(a) != find(b)` 再次成立。
4. **整合 Z3**：寫 `.smt2` QF_UF instance 丟 Z3，用你的 solver 比對答案。

## 自我檢核

- [ ] Term DAG + hash-cons 寫得出
- [ ] Union-find with undo 正確
- [ ] Congruence propagation 在 merge 後觸發
- [ ] Disequality check 沒漏
- [ ] Explain 能找到 proof path（congruence edge 遞迴展開）
- [ ] Push / pop 正確還原
- [ ] 基本 QF_UF instance 結論與 Z3 一致
- [ ] 效能：100 變數 QF_UF instance 秒內解完

EUF 寫完，你有第一個實用的 theory solver。下一章跳到 **LRA** — 線性實數算術、Simplex 算法、連續系統的 theory。跟 EUF 完全不同 — LRA 處理的是數值，要 pivot / bound / infeasibility。

→ [Ch 27 — LRA：Simplex for SMT](./27-lra-simplex.md)
