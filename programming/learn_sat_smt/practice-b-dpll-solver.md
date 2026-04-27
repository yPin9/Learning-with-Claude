# 練習 B — 完整 DPLL solver

> 目標：把 Ch 10–11 的 DPLL 寫成 **正式完整** 的 solver — 加 branching heuristic (DLIS)、正確處理 pure literal、能輸出 SAT model、能跑 SAT competition 的 `uf` 系列 benchmark 並跟 MiniSat 對答案。**這是你讓自己說「我會寫 SAT solver」的基礎門檻**。

## 任務規格

| 項目 | 規格 |
|---|---|
| 輸入 | DIMACS CNF 從 file 或 stdin |
| 輸出 | SAT competition 標準格式 (`s` + `v` 行) |
| Exit code | `10` SAT / `20` UNSAT / `0` UNKNOWN |
| 演算法 | DPLL + unit propagation + pure literal + DLIS branching |
| 不需要 | CDCL、watched literals、VSIDS、restart — 那是練習 C |
| 效能目標 | 100 變數 random 3-SAT 10 秒內解完 |
| 必要驗證 | 在至少 20 個不同 instance 上跟 MiniSat 結論一致 |

## 跟 Ch 11 v1 的差別

Ch 11 的 v1 是教學版、省了幾個東西：

1. Ch 11 用「第一個未指派變數」做 branching — 太笨，**這個練習加 DLIS**
2. Ch 11 沒做 pure literal — **這個練習要做**
3. Ch 11 沒處理 edge case（空 CNF、已含空 clause）— **練習要處理**
4. Ch 11 用 full assignment backup — 可用 undo stack 替代

## 實作步驟建議

### Step 1：骨架

複製 `sat-v1/` 一份變 `sat-dpll/`。CMakeLists、parser 照用。重寫 `solver.hpp`。

### Step 2：Decision Heuristic — DLIS

**DLIS (Dynamic Largest Individual Sum)**：每次 branching 前統計所有 unresolved clause 裡各 literal 的出現次數，挑出現最多的 literal。

```cpp
Lit pick_dlis() const {
    std::unordered_map<Lit, int> count;
    for (const auto& c : cnf_.clauses) {
        bool sat = false;
        for (Lit l : c) if (lit_val(l, assignment_) == Value::True) { sat = true; break; }
        if (sat) continue;
        for (Lit l : c) if (lit_val(l, assignment_) == Value::Unassigned) count[l]++;
    }
    Lit best = 0; int best_count = -1;
    for (auto [l, c] : count) if (c > best_count) { best_count = c; best = l; }
    return best;
}
```

**每次 branching 跑 O(literals)**，小題可接受。這是 DLIS 的 overhead，也是 1990s 研究改 VSIDS 的動機。

### Step 3：Pure Literal

```cpp
bool pure_literal_elimination() {
    bool any = false;
    while (true) {
        std::unordered_map<int, int> pos_seen, neg_seen;
        for (const auto& c : cnf_.clauses) {
            bool sat = false;
            for (Lit l : c) if (lit_val(l, assignment_) == Value::True) { sat = true; break; }
            if (sat) continue;
            for (Lit l : c) {
                if (lit_val(l, assignment_) == Value::Unassigned) {
                    if (l > 0) pos_seen[l]++;
                    else neg_seen[-l]++;
                }
            }
        }
        bool changed = false;
        for (int v = 1; v <= cnf_.num_vars; v++) {
            if (assignment_[v] != Value::Unassigned) continue;
            if (pos_seen.count(v) && !neg_seen.count(v)) {
                assignment_[v] = Value::True;
                changed = true; any = true;
            } else if (!pos_seen.count(v) && neg_seen.count(v)) {
                assignment_[v] = Value::False;
                changed = true; any = true;
            }
        }
        if (!changed) break;
    }
    return any;
}
```

在 `dpll()` 裡每次 unit prop 完做一輪 pure literal。

### Step 4：主循環整合

```cpp
bool dpll() {
    Assignment backup = assignment_;
    if (!unit_propagate()) { assignment_ = backup; return false; }
    pure_literal_elimination();

    if (all_satisfied()) return true;
    // 或檢查是否有 clause 已 falsified
    for (const auto& c : cnf_.clauses) {
        bool sat = false, unassigned = false;
        for (Lit l : c) {
            Value v = lit_val(l, assignment_);
            if (v == Value::True) { sat = true; break; }
            if (v == Value::Unassigned) { unassigned = true; }
        }
        if (!sat && !unassigned) { assignment_ = backup; return false; }
    }

    Lit l = pick_dlis();
    if (l == 0) return all_satisfied();
    decisions_++;
    int v = var_of(l);
    Value first = sign_of(l) ? Value::True : Value::False;
    Value second = sign_of(l) ? Value::False : Value::True;

    assignment_[v] = first;
    if (dpll()) return true;
    assignment_ = backup;
    assignment_[v] = second;
    if (dpll()) return true;
    assignment_ = backup;
    return false;
}
```

### Step 5：Edge case

`add_clause` 要正確處理：

- **空 CNF**（0 clause）→ 立即 SAT
- **含空 clause** → 立即 UNSAT
- **含 unit clause** → level-0 assignment

### Step 6：測試 benchmark

下載 SAT competition 的 `uf50-218` 和 `uf100-430` benchmark（或 Tseitin 自製）：

```bash
wget https://www.cs.ubc.ca/~hoos/SATLIB/Benchmarks/SAT/RND3SAT/uf50-218.tar.gz
tar xzf uf50-218.tar.gz
```

跑所有 100 題：

```bash
for f in uf50-*/*.cnf; do
    ours=$(./sat-dpll "$f" 2>/dev/null | head -1 | awk '{print $2}')
    theirs=$(minisat "$f" 2>/dev/null | tail -1)
    if [[ "$ours" != "$theirs" ]]; then echo "MISMATCH $f"; fi
done
```

全對才算過。

## 期望輸出範例

輸入 `uf50-01.cnf`：

```
p cnf 50 218
1 -2 3 0
... 218 clauses ...
```

輸出：

```
c variables: 50, clauses: 218
c decisions: 1823, propagations: 5429, time: 0.6s
s SATISFIABLE
v 1 2 -3 4 ... -50 0
```

不同種子答案可能不同。SAT 時 model 能讓所有 clause 為 ⊤ 即可。

## 完整參考解答

**請自己寫一遍**。不先掙扎，看也沒用。

<details>
<summary>點開參考實作骨架</summary>

```cpp
// sat-dpll/src/solver.hpp (~200 lines)
#pragma once
#include "types.hpp"
#include <unordered_map>
#include <optional>

class DpllSolver {
public:
    explicit DpllSolver(CNF cnf) : cnf_(std::move(cnf)) {
        assignment_.assign(cnf_.num_vars + 1, Value::Unassigned);
    }
    bool solve() {
        // level-0 preprocess
        for (const auto& c : cnf_.clauses) if (c.empty()) return false;
        if (cnf_.clauses.empty()) return true;
        return dpll();
    }
    const Assignment& model() const { return assignment_; }
    uint64_t decisions() const { return decisions_; }
    uint64_t propagations() const { return propagations_; }

private:
    CNF cnf_;
    Assignment assignment_;
    uint64_t decisions_ = 0, propagations_ = 0;

    Value lit_val(Lit l) const {
        Value v = assignment_[var_of(l)];
        if (v == Value::Unassigned) return v;
        bool truth = (v == Value::True);
        return (sign_of(l) == truth) ? Value::True : Value::False;
    }

    bool unit_propagate() {
        bool changed = true;
        while (changed) {
            changed = false;
            for (const auto& c : cnf_.clauses) {
                int unassigned = 0; Lit last = 0; bool sat = false;
                for (Lit l : c) {
                    Value v = lit_val(l);
                    if (v == Value::True) { sat = true; break; }
                    if (v == Value::Unassigned) { unassigned++; last = l; }
                }
                if (sat) continue;
                if (unassigned == 0) return false;
                if (unassigned == 1) {
                    assignment_[var_of(last)] = sign_of(last) ? Value::True : Value::False;
                    propagations_++; changed = true;
                }
            }
        }
        return true;
    }

    void pure_literal() {
        while (true) {
            std::unordered_map<int, int> pos, neg;
            for (const auto& c : cnf_.clauses) {
                bool sat = false;
                for (Lit l : c) if (lit_val(l) == Value::True) { sat = true; break; }
                if (sat) continue;
                for (Lit l : c) {
                    if (lit_val(l) == Value::Unassigned) {
                        if (l > 0) pos[l]++; else neg[-l]++;
                    }
                }
            }
            bool any = false;
            for (int v = 1; v <= cnf_.num_vars; v++) {
                if (assignment_[v] != Value::Unassigned) continue;
                if (pos.count(v) && !neg.count(v)) { assignment_[v] = Value::True; any = true; }
                else if (!pos.count(v) && neg.count(v)) { assignment_[v] = Value::False; any = true; }
            }
            if (!any) break;
        }
    }

    bool all_satisfied() const {
        for (const auto& c : cnf_.clauses) {
            bool sat = false;
            for (Lit l : c) if (lit_val(l) == Value::True) { sat = true; break; }
            if (!sat) return false;
        }
        return true;
    }

    Lit pick_dlis() const {
        std::unordered_map<Lit, int> count;
        for (const auto& c : cnf_.clauses) {
            bool sat = false;
            for (Lit l : c) if (lit_val(l) == Value::True) { sat = true; break; }
            if (sat) continue;
            for (Lit l : c) if (lit_val(l) == Value::Unassigned) count[l]++;
        }
        Lit best = 0; int best_count = -1;
        for (auto [l, c] : count) if (c > best_count) { best_count = c; best = l; }
        return best;
    }

    bool dpll() {
        Assignment backup = assignment_;
        if (!unit_propagate()) { assignment_ = backup; return false; }
        pure_literal();
        // check conflict
        for (const auto& c : cnf_.clauses) {
            bool sat = false, unassigned = false;
            for (Lit l : c) {
                Value v = lit_val(l);
                if (v == Value::True) { sat = true; break; }
                if (v == Value::Unassigned) unassigned = true;
            }
            if (!sat && !unassigned) { assignment_ = backup; return false; }
        }
        if (all_satisfied()) return true;

        Lit l = pick_dlis();
        if (l == 0) return all_satisfied();
        decisions_++;
        int v = var_of(l);
        Value first = sign_of(l) ? Value::True : Value::False;
        Value second = sign_of(l) ? Value::False : Value::True;
        assignment_[v] = first;
        if (dpll()) return true;
        assignment_ = backup;
        assignment_[v] = second;
        if (dpll()) return true;
        assignment_ = backup;
        return false;
    }
};
```

</details>

## 測試用例

底下六題必過：

1. `p cnf 1 1\n1 0` → SAT
2. `p cnf 1 2\n1 0\n-1 0` → UNSAT
3. `p cnf 3 2\n1 2 3 0\n-1 -2 -3 0` → SAT
4. Pigeonhole 3-2（3 隻鴿子 2 洞，6 變數、9 clause） → UNSAT
5. `uf20-01.cnf` 到 `uf20-10.cnf` → 跟 MiniSat 全一致
6. `uf50-01.cnf` → SAT within 10 seconds

## Bonus 挑戰

寫完基礎版，有餘力試：

1. **MOMS 替代 DLIS**：`Maximum Occurrences in Minimum Size` — 只算**最短 unresolved clause** 中的 literal 頻率，加速 `f(x) = f(¬x) · 2^k + f(x) + f(¬x)`。比 DLIS 稍快。
2. **Chronological stats**：印 branching tree 深度分布。驗證「難題」確實需要深 backtrack。
3. **Undo stack 取代 backup**：不存整個 assignment copy，只記「這一 level 改了哪些變數」，return 時反向 undo。空間 O(n) 而非 O(n²)。
4. **IsSatisfiable flag**：提前 short-circuit — 某個 clause 已 sat 就不再看 — 加速 unit_propagate 約 30%。

## 自我檢核

- [ ] `sat-dpll` 編譯、跑 DIMACS、輸出 SAT competition 格式
- [ ] 跟 MiniSat 在 20+ 個 `uf` benchmark 結論一致
- [ ] 100 變數 random 3-SAT 10 秒內解完
- [ ] DLIS 正確 — 每次 branching 前統計 unresolved clauses
- [ ] Pure literal elimination 有做、edge case 正確
- [ ] decisions / propagations / time 印出合理
- [ ] 讀 MiniSat 0.4 的 `search()` 能對照出哪些 feature 你還沒有

**這個練習完成 = 你能寫 DPLL**。下一個練習 C 把它升級到 CDCL + watched literals，那是進入工業級 solver 的門檻。

→ [練習 C — CDCL + two watched literals](./practice-c-cdcl-solver.md)
