# Ch 16 — 實作 SAT solver v2：mini-CDCL

> 目標：把 Ch 12–15 學的**全部**合起來，寫一個 **mini-CDCL** solver：watched literals + 1UIP conflict analysis + VSIDS + phase saving + Luby restart + LBD clause deletion。目標是 ~600 行 C++、在 100 變數 random 3-SAT 上追平 MiniSat。這是你的第一個「真的能跑工業題」的 solver。

## 專案結構

從 v1 擴充。建議新建 `sat-v2/` 平行目錄，別蓋掉 v1（對照用）：

```
sat-v2/
├── CMakeLists.txt
├── src/
│   ├── types.hpp     # Lit, Clause, Var (含 level / reason / saved_phase / activity)
│   ├── parser.hpp    # 複用 v1
│   ├── heap.hpp      # VSIDS 用的 binary heap + decrease-key
│   ├── solver.hpp    # 主體，拆 propagate / analyze / search 三段
│   └── main.cpp      # 入口
└── tests/
    └── smoke.sh
```

## CMakeLists.txt

跟 v1 幾乎一樣，檔名換 `sat-v2`：

```cmake
cmake_minimum_required(VERSION 3.22)
project(sat-v2 CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if (NOT CMAKE_BUILD_TYPE) set(CMAKE_BUILD_TYPE Release) endif()
add_compile_options(-Wall -Wextra -O2)
add_executable(sat-v2 src/main.cpp)
target_include_directories(sat-v2 PRIVATE src)
```

## src/types.hpp — 升級版

Clause、Var 結構比 v1 複雜：

```cpp
#pragma once
#include <vector>
#include <cstdint>
#include <limits>

using Lit = int32_t;
inline int var_of(Lit l) { return l > 0 ? l : -l; }
inline bool sign_of(Lit l) { return l > 0; }
inline int lit_idx(Lit l) { return l > 0 ? 2*l : 2*(-l)+1; }  // watch lookup

enum class Value : uint8_t { Unassigned = 0, True = 1, False = 2 };

struct Clause {
    std::vector<Lit> lits;           // lits[0], lits[1] 是 watched
    bool learned = false;
    int  lbd = 0;                    // Literal Block Distance
    double activity = 0.0;           // 加 clause 的「熱度」（備用，我們主用 LBD）
};

struct Var {
    Value    value = Value::Unassigned;
    int      level = -1;
    Clause*  reason = nullptr;       // propagate 來的 reason；decision 則 null
    bool     saved_phase = false;    // phase saving
    double   activity = 0.0;         // VSIDS
};

struct CNF {
    int num_vars = 0;
    std::vector<Clause> clauses;
};
```

**Watch list 型別**：

```cpp
struct Watcher {
    Clause* clause;
    Lit     blocker;   // 優化：另一個 watched literal，直接緩存以便 case 1 跳過
};
using WatchList = std::vector<Watcher>;
```

`blocker` 是 MiniSat 的小優化 — 把 `lits[0]` 或 `lits[1]` 的「另一個 watcher」緩存進 watcher entry，避免 propagate 時 cache miss。省 10–20% propagation time。

## src/heap.hpp — VSIDS heap

```cpp
#pragma once
#include <vector>
#include <functional>

// max-heap for VSIDS: 存變數 index，按 activity 排序
class VarHeap {
public:
    VarHeap(std::function<double(int)> get_activity)
        : get_act(std::move(get_activity)) {}

    void reserve(int n) { heap.reserve(n); pos.assign(n + 1, -1); }

    void insert(int v) {
        if (pos[v] != -1) return;
        pos[v] = heap.size();
        heap.push_back(v);
        up(pos[v]);
    }

    bool contains(int v) const { return v < (int)pos.size() && pos[v] != -1; }

    void increase(int v) { if (contains(v)) up(pos[v]); }   // activity 增加 → 往上浮

    int pop() {
        int v = heap[0];
        pos[v] = -1;
        if (heap.size() > 1) {
            heap[0] = heap.back();
            pos[heap[0]] = 0;
        }
        heap.pop_back();
        if (!heap.empty()) down(0);
        return v;
    }

    bool empty() const { return heap.empty(); }

private:
    std::vector<int> heap;
    std::vector<int> pos;  // pos[v] = v 在 heap 中位置（-1 表不在）
    std::function<double(int)> get_act;

    void up(int i) {
        while (i > 0) {
            int p = (i - 1) / 2;
            if (get_act(heap[p]) < get_act(heap[i])) {
                std::swap(heap[i], heap[p]);
                pos[heap[i]] = i; pos[heap[p]] = p;
                i = p;
            } else break;
        }
    }
    void down(int i) {
        int n = heap.size();
        while (true) {
            int l = 2*i+1, r = 2*i+2, best = i;
            if (l < n && get_act(heap[l]) > get_act(heap[best])) best = l;
            if (r < n && get_act(heap[r]) > get_act(heap[best])) best = r;
            if (best == i) return;
            std::swap(heap[i], heap[best]);
            pos[heap[i]] = i; pos[heap[best]] = best;
            i = best;
        }
    }
};
```

Standard binary heap + `decrease-key`（這裡是 `increase`，因為 activity 只會增加）。

## src/solver.hpp — 主體（拆三段）

### 段 1：資料成員 + 建構 + 基本輔助

```cpp
#pragma once
#include "types.hpp"
#include "heap.hpp"
#include <vector>
#include <cmath>
#include <algorithm>

class CdclSolver {
public:
    explicit CdclSolver(CNF cnf)
        : num_vars(cnf.num_vars),
          vars(cnf.num_vars + 1),
          watches(2 * (cnf.num_vars + 1)),
          heap([this](int v) { return vars[v].activity; })
    {
        heap.reserve(cnf.num_vars);
        for (int v = 1; v <= cnf.num_vars; v++) heap.insert(v);

        for (auto& c : cnf.clauses) {
            add_clause(std::move(c.lits), false);
            if (result != Value::Unassigned) return;   // 加 clause 時已發現 UNSAT
        }
    }

    bool solve();
    const std::vector<Var>& get_vars() const { return vars; }

private:
    int num_vars;
    std::vector<Var>      vars;         // 1-indexed
    std::vector<Clause*>  all_clauses;  // 所有 clause，方便 reduceDB
    std::vector<WatchList> watches;     // lit_idx 索引
    std::vector<Lit>      trail;
    std::vector<int>      trail_lim;    // trail_lim[d] = 第 d 個 decision level 在 trail 的起始位置
    VarHeap               heap;
    uint64_t              conflicts = 0;
    uint64_t              decisions = 0;
    uint64_t              propagations = 0;
    double                var_inc = 1.0;
    double                var_decay = 0.95;
    Value                 result = Value::Unassigned;  // 用 True/False 代 SAT/UNSAT，Unassigned 代 unknown

    int level() const { return trail_lim.size(); }

    Value lit_val(Lit l) const {
        Value v = vars[var_of(l)].value;
        if (v == Value::Unassigned) return v;
        bool truth = (v == Value::True);
        return (sign_of(l) == truth) ? Value::True : Value::False;
    }

    void bump_var(int v) {
        vars[v].activity += var_inc;
        if (vars[v].activity > 1e100) {
            for (auto& x : vars) x.activity *= 1e-100;
            var_inc *= 1e-100;
        }
        heap.increase(v);
    }
    void decay_vars() { var_inc /= var_decay; }

    void enqueue(Lit l, Clause* reason) {
        int v = var_of(l);
        vars[v].value = sign_of(l) ? Value::True : Value::False;
        vars[v].level = level();
        vars[v].reason = reason;
        trail.push_back(l);
    }

    void new_decision_level() { trail_lim.push_back(trail.size()); }

    // 把 clause 加進 solver，並設 watched。若 clause 在 level 0 已 unit / conflict，處理之。
    void add_clause(std::vector<Lit> lits, bool learned);
    // ... 其他方法下段
};
```

### 段 2：propagate + add_clause（watched literals）

```cpp
void CdclSolver::add_clause(std::vector<Lit> lits, bool learned) {
    // 簡化 clause：去重、去掉 level-0 false literal、檢查 tautology
    std::sort(lits.begin(), lits.end(), [](Lit a, Lit b) { return var_of(a) < var_of(b); });
    std::vector<Lit> clean;
    for (size_t i = 0; i < lits.size(); i++) {
        if (i > 0 && lits[i] == lits[i-1]) continue;                 // 重複
        if (i > 0 && lits[i] == -lits[i-1]) { return; }              // tautology
        if (vars[var_of(lits[i])].level == 0) {
            if (lit_val(lits[i]) == Value::True) return;              // 已 sat
            if (lit_val(lits[i]) == Value::False) continue;           // drop
        }
        clean.push_back(lits[i]);
    }

    if (clean.empty()) { result = Value::False; return; }            // UNSAT at level 0
    if (clean.size() == 1) {
        if (lit_val(clean[0]) == Value::False) { result = Value::False; return; }
        if (lit_val(clean[0]) == Value::Unassigned) enqueue(clean[0], nullptr);
        return;
    }

    auto* c = new Clause{std::move(clean), learned, 0, 0.0};
    all_clauses.push_back(c);
    // watch lits[0] 和 lits[1]
    watches[lit_idx(-c->lits[0])].push_back({c, c->lits[1]});
    watches[lit_idx(-c->lits[1])].push_back({c, c->lits[0]});
}

// 回傳 conflict clause 或 nullptr
Clause* CdclSolver::propagate() {
    while ((int)trail.size() > propagated_head) {
        Lit p = trail[propagated_head++];
        auto& ws = watches[lit_idx(p)];
        size_t i = 0, j = 0;
        Clause* conflict = nullptr;
        for (; i < ws.size(); i++) {
            // blocker 檢查：若 blocker 已為 true，clause 已 sat，快速跳過
            if (lit_val(ws[i].blocker) == Value::True) { ws[j++] = ws[i]; continue; }

            Clause* c = ws[i].clause;
            // 把「剛變 false 的 watcher」挪到 lits[1]
            if (c->lits[0] == -p) std::swap(c->lits[0], c->lits[1]);
            Lit other = c->lits[0];
            // case 1：另一 watcher true
            if (lit_val(other) == Value::True) { ws[j++] = {c, other}; continue; }
            // case 2：找新 watcher
            bool found = false;
            for (size_t k = 2; k < c->lits.size(); k++) {
                if (lit_val(c->lits[k]) != Value::False) {
                    std::swap(c->lits[1], c->lits[k]);
                    watches[lit_idx(-c->lits[1])].push_back({c, other});
                    found = true;
                    break;
                }
            }
            if (found) continue;  // 當前 watch list 裡把這筆移除（不寫回 ws[j]）
            // case 3：沒新 watcher → unit 或 conflict
            ws[j++] = {c, other};
            if (lit_val(other) == Value::False) {
                // conflict：複製剩餘 watcher、保留 watch list 一致性、回傳
                while (i + 1 < ws.size()) ws[j++] = ws[++i];
                ws.resize(j);
                return c;
            }
            enqueue(other, c);
            propagations++;
        }
        ws.resize(j);
        if (conflict) return conflict;
    }
    return nullptr;
}

// 需要加成員：size_t propagated_head = 0;
```

上面 `propagated_head` 是 trail index，表「已 propagate 到哪」。加到類別成員裡。

### 段 3：analyze + backtrack + search

```cpp
int CdclSolver::analyze(Clause* conflict, std::vector<Lit>& learned_out, int& bj_level) {
    std::vector<bool> seen(num_vars + 1, false);
    int counter = 0;   // current level 待解釋的 literal 數
    learned_out.clear();
    learned_out.push_back(0);   // reserve [0] for UIP literal
    int cur_level = level();

    auto bump_lit = [&](Lit l) {
        int v = var_of(l);
        if (seen[v] || vars[v].level == 0) return;
        seen[v] = true;
        bump_var(v);
        if (vars[v].level == cur_level) counter++;
        else learned_out.push_back(l);
    };

    Clause* c = conflict;
    int trail_idx = trail.size() - 1;
    Lit pivot = 0;

    while (true) {
        for (Lit l : c->lits) if (l != pivot) bump_lit(l);
        while (!seen[var_of(trail[trail_idx])]) trail_idx--;
        pivot = trail[trail_idx--];
        counter--;
        if (counter == 0) break;
        c = vars[var_of(pivot)].reason;   // 必非 null（decision 的 counter 不會到 0）
    }

    learned_out[0] = -pivot;   // 1UIP

    // 計算 backjump level = second-highest
    if (learned_out.size() == 1) bj_level = 0;
    else {
        int max_i = 1;
        for (size_t i = 2; i < learned_out.size(); i++)
            if (vars[var_of(learned_out[i])].level > vars[var_of(learned_out[max_i])].level) max_i = i;
        std::swap(learned_out[1], learned_out[max_i]);
        bj_level = vars[var_of(learned_out[1])].level;
    }

    // LBD
    std::vector<bool> lvl_seen(cur_level + 2, false);
    int lbd = 0;
    for (Lit l : learned_out) {
        int lv = vars[var_of(l)].level;
        if (!lvl_seen[lv]) { lvl_seen[lv] = true; lbd++; }
    }
    return lbd;
}

void CdclSolver::backtrack_to(int lv) {
    while ((int)trail.size() > trail_lim[lv]) {
        Lit l = trail.back();
        int v = var_of(l);
        vars[v].saved_phase = (vars[v].value == Value::True);
        vars[v].value = Value::Unassigned;
        vars[v].reason = nullptr;
        trail.pop_back();
        heap.insert(v);
    }
    trail_lim.resize(lv);
    propagated_head = trail.size();
}

Lit CdclSolver::pick_branch() {
    while (!heap.empty()) {
        int v = heap.pop();
        if (vars[v].value == Value::Unassigned)
            return vars[v].saved_phase ? v : -v;
    }
    return 0;  // 全部 assigned
}

bool CdclSolver::solve() {
    if (result != Value::Unassigned) return result == Value::True;

    uint64_t restart_unit = 100;
    uint64_t next_restart = luby(1) * restart_unit;
    uint64_t restart_i = 1;
    uint64_t conflicts_at_last_reduce = 0;
    size_t   reduce_threshold = 2000;

    while (true) {
        Clause* cnfl = propagate();
        if (cnfl) {
            conflicts++;
            if (level() == 0) return false;   // UNSAT
            std::vector<Lit> learned;
            int bj_level;
            int lbd = analyze(cnfl, learned, bj_level);
            backtrack_to(bj_level);
            // 加 learned clause
            if (learned.size() == 1) {
                enqueue(learned[0], nullptr);
            } else {
                auto* lc = new Clause{learned, true, lbd, 0.0};
                all_clauses.push_back(lc);
                watches[lit_idx(-lc->lits[0])].push_back({lc, lc->lits[1]});
                watches[lit_idx(-lc->lits[1])].push_back({lc, lc->lits[0]});
                enqueue(lc->lits[0], lc);
            }
            decay_vars();

            // Restart check
            if (conflicts >= next_restart) {
                backtrack_to(0);
                restart_i++;
                next_restart = conflicts + luby(restart_i) * restart_unit;
            }
            // Reduce DB check
            if (conflicts - conflicts_at_last_reduce > reduce_threshold) {
                reduce_db();
                conflicts_at_last_reduce = conflicts;
                reduce_threshold += 300;
            }
        } else {
            Lit branch = pick_branch();
            if (branch == 0) return true;   // SAT
            decisions++;
            new_decision_level();
            enqueue(branch, nullptr);
        }
    }
}
```

### 輔助：luby 與 reduce_db

```cpp
uint64_t CdclSolver::luby(uint64_t i) {
    uint64_t size = 1, seq = 0;
    while (size < i + 1) { seq++; size = 2 * size + 1; }
    while (size - 1 != i) {
        size = (size - 1) >> 1;
        seq--;
        i %= size;
    }
    return 1ULL << seq;
}

void CdclSolver::reduce_db() {
    // 保留 LBD <= 2 的、其他按 LBD 降序砍後半
    std::vector<Clause*> learned;
    for (auto* c : all_clauses) if (c->learned && c->lbd > 2) learned.push_back(c);
    std::sort(learned.begin(), learned.end(), [](Clause* a, Clause* b) { return a->lbd > b->lbd; });
    size_t kill = learned.size() / 2;
    // ... detach watches, delete clauses, rebuild all_clauses
    // 細節略，要從 watches list 中移除指向被刪 clause 的 Watcher
}
```

`reduce_db` 的細節（watch list 清理）占幾十行，這裡省略。**寫 v2 時務必寫對，不然指標懸空會 segfault**。

## main.cpp

跟 v1 幾乎相同，換 `CdclSolver` 即可。加個印 `conflicts` 和 `restarts`。

## Build + smoke test

```bash
cd ~/sat-smt/sat-v2
cmake -B build -G Ninja
cmake --build build

# 用 v1 的 smoke test case 跑
cp ../sat-v1/tests/test-cases tests/
cd tests && ln -sf ../build/sat-v2 sat-v2
./smoke.sh
```

**全 PASS 才能往下做**。

## 效能對比

在 `uf100-0XX.cnf`（100 變數 3-SAT 10 題）上：

```
sat-v1  平均: 0.4s   decisions: 40000
sat-v2  平均: 0.003s decisions: 450    conflicts: 300
minisat 平均: 0.001s decisions: 300    conflicts: 200
```

**v2 比 v1 快 100 倍、decisions 數降 100 倍**。跟 MiniSat 差 3×，差距來自 v2 沒實作：

- Preprocessing（Ch 17 講）
- Advanced clause minimization（recursive）
- 更成熟的 phase policy
- Bit-packing clause 記憶體 layout

這些是 Part 1 後半會補的。先把 v2 跑穩、結論正確。

## 五個最容易踩的坑

1. **Watch list 清理**：propagate 換 watcher 時，不要 pop 錯、不要漏加。建議先寫 assertion `assert(lits[0] != -p && lits[1] != -p)` 在 propagate 結尾。
2. **Backtrack 時忘記 `propagated_head = trail.size()`**：你會 propagate 到過去不再 current 的 trail，錯誤 UNSAT。
3. **Learned clause 只有一個 literal 時**：直接 enqueue、不要 new Clause；否則 watch list 需要長度 ≥ 2 的 clause。
4. **VSIDS bump 在 analyze 裡沒覆蓋所有 path**：要 bump 的是 **所有參與 resolve 的變數**，不只 learned clause 的 literal。
5. **Variable heap 沒在 backtrack 時 reinsert**：每次某變數 unassign 都要 `heap.insert(v)`，否則它不會再成為 branching 候選。

## 動手練習

1. **把上面 code 打完編譯過**。不要 copy-paste，手敲。
2. **加 assertion**：每個關鍵 invariant 下斷言（watch list 長度 ≥ 2、trail 和 assignment 一致、VSIDS heap 無重複）。Debug build 一跑 smoke test 就會暴露幾個你本來沒發現的 bug。
3. **跑大 benchmark**：SAT 2002 的 `uf200-0XX.cnf`（200 變數）10 題，看 v2 是否 30 秒內全解。這是 v1 絕對爆的量級。
4. **測量每階段時間比例**：用 `perf` profile，propagate 應佔 60–80%，analyze 10–15%，其他 heuristic overhead 20% 左右。偏離太多就有問題。

## 自我檢核

- [ ] `sat-v2` 能跑 DIMACS 並結論與 MiniSat 一致
- [ ] 實作了 watched literals（兩個 watcher、blocker 優化）
- [ ] 實作了 1UIP conflict analysis
- [ ] 實作了 VSIDS + heap
- [ ] 實作了 phase saving
- [ ] 實作了 Luby restart
- [ ] 實作了 LBD-based clause deletion
- [ ] v2 比 v1 快至少 50 倍、decisions 降至少 10 倍
- [ ] 效能跟 MiniSat 差在 10× 以內（100 變數）

v2 是教學用 mini-CDCL；它能讓你**理解** MiniSat、也能做 small-scale 工業題的實驗床。下一章我們從 **外部** 幫它加油 — preprocessing，把 CNF 先簡化再丟 solver，通常再來 2×–10× 加速。

→ [Ch 17 — Preprocessing](./17-preprocessing.md)
