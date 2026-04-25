# Ch 11 — 實作 SAT solver v1：乾淨的 DPLL

> 目標：把 Ch 10 的 DPLL 骨架寫成 **一個完整的 C++ 專案**：CMake build、能吃 DIMACS、輸出標準 SAT format、能跟 MiniSat 在小 benchmark 上對答案。這是你的第一個 solver — **不是最快、但是對的**。v2 在 Ch 16，兩版差距會讓你體會 CDCL 的威力。

## 專案結構

```
sat-v1/
├── CMakeLists.txt
├── src/
│   ├── types.hpp       # Lit, Clause, CNF, Assignment
│   ├── parser.hpp      # DIMACS parser
│   ├── solver.hpp      # DPLL solver
│   └── main.cpp        # 入口
└── tests/
    └── smoke.sh        # 對 MiniSat 的 smoke test
```

建目錄：

```bash
mkdir -p ~/sat-smt/sat-v1/{src,tests}
cd ~/sat-smt/sat-v1
```

## CMakeLists.txt

```cmake
cmake_minimum_required(VERSION 3.22)
project(sat-v1 CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if (NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

add_compile_options(-Wall -Wextra -Wpedantic)

add_executable(sat-v1 src/main.cpp)
target_include_directories(sat-v1 PRIVATE src)
```

簡單乾淨。Release build + warnings 全開，之後加 `-fsanitize=undefined,address` debug 用。

## src/types.hpp

基本型別，全部放 header 方便被其他 module 用：

```cpp
#pragma once
#include <vector>
#include <cstdint>

using Lit = int32_t;              // +k = x_k；-k = ¬x_k；變數從 1 開始
using Clause = std::vector<Lit>;

struct CNF {
    int num_vars = 0;
    std::vector<Clause> clauses;
};

enum class Value : uint8_t { Unassigned = 0, True = 1, False = 2 };

inline int var_of(Lit l) { return l > 0 ? l : -l; }
inline bool sign_of(Lit l) { return l > 0; }  // true 代表正 literal

// 1-indexed，assignment[0] 不用
using Assignment = std::vector<Value>;

// 在當前 assignment 下 literal 的真值
inline Value lit_value(Lit l, const Assignment& a) {
    Value v = a[var_of(l)];
    if (v == Value::Unassigned) return v;
    bool truth = (v == Value::True);
    bool lit_true = (sign_of(l) == truth);
    return lit_true ? Value::True : Value::False;
}
```

**為什麼 Lit 用 int32_t**：MiniSat 用的編碼；`std::abs(int32_t)` 在 clang/gcc 都是單條指令；空間跟 `int` 一樣但符號明確。

## src/parser.hpp

```cpp
#pragma once
#include "types.hpp"
#include <istream>
#include <sstream>
#include <string>
#include <stdexcept>

inline CNF parse_dimacs(std::istream& in) {
    CNF cnf;
    std::string line;
    bool header_seen = false;
    Clause current;

    while (std::getline(in, line)) {
        if (line.empty() || line[0] == 'c' || line[0] == '%') continue;
        if (!header_seen && line[0] == 'p') {
            std::istringstream iss(line);
            std::string p, fmt; int n, m;
            iss >> p >> fmt >> n >> m;
            if (fmt != "cnf") throw std::runtime_error("only 'p cnf' supported");
            cnf.num_vars = n;
            cnf.clauses.reserve(m);
            header_seen = true;
            continue;
        }
        std::istringstream iss(line);
        Lit l;
        while (iss >> l) {
            if (l == 0) {
                cnf.clauses.push_back(std::move(current));
                current.clear();
            } else {
                current.push_back(l);
            }
        }
    }
    if (!current.empty()) cnf.clauses.push_back(std::move(current));
    if (!header_seen) throw std::runtime_error("missing 'p cnf' header");
    return cnf;
}
```

**處理跨行 clause**：不斷 accumulate literal 到 `current`，碰 `0` 才 flush 一條。SAT competition 某些 benchmark 會這樣切。

## src/solver.hpp — DPLL 本體

```cpp
#pragma once
#include "types.hpp"
#include <optional>

class DpllSolver {
public:
    explicit DpllSolver(CNF cnf) : cnf_(std::move(cnf)) {
        assignment_.assign(cnf_.num_vars + 1, Value::Unassigned);
    }

    bool solve() { return dpll(); }
    const Assignment& model() const { return assignment_; }
    uint64_t decisions() const { return decisions_; }
    uint64_t propagations() const { return propagations_; }

private:
    CNF cnf_;
    Assignment assignment_;
    uint64_t decisions_ = 0;
    uint64_t propagations_ = 0;

    // 檢查 clauses 狀態，並做 unit propagation
    // 回傳 false 代表 conflict
    bool unit_propagate() {
        bool changed = true;
        while (changed) {
            changed = false;
            for (const auto& c : cnf_.clauses) {
                int unassigned_count = 0;
                Lit last_unassigned = 0;
                bool satisfied = false;
                for (Lit l : c) {
                    Value v = lit_value(l, assignment_);
                    if (v == Value::True) { satisfied = true; break; }
                    if (v == Value::Unassigned) {
                        unassigned_count++;
                        last_unassigned = l;
                    }
                }
                if (satisfied) continue;
                if (unassigned_count == 0) return false;  // conflict
                if (unassigned_count == 1) {
                    int v = var_of(last_unassigned);
                    assignment_[v] = sign_of(last_unassigned) ? Value::True : Value::False;
                    propagations_++;
                    changed = true;
                }
            }
        }
        return true;
    }

    // 所有 clause 都 satisfied？
    bool all_satisfied() const {
        for (const auto& c : cnf_.clauses) {
            bool sat = false;
            for (Lit l : c) if (lit_value(l, assignment_) == Value::True) { sat = true; break; }
            if (!sat) return false;
        }
        return true;
    }

    // 挑第一個未指派變數（最笨）
    int pick_variable() const {
        for (int v = 1; v <= cnf_.num_vars; v++)
            if (assignment_[v] == Value::Unassigned) return v;
        return 0;
    }

    bool dpll() {
        Assignment backup = assignment_;
        if (!unit_propagate()) { assignment_ = backup; return false; }
        if (all_satisfied()) return true;

        int x = pick_variable();
        if (x == 0) return all_satisfied();

        decisions_++;
        assignment_[x] = Value::True;
        if (dpll()) return true;

        assignment_ = backup;
        assignment_[x] = Value::False;
        if (dpll()) return true;

        assignment_ = backup;
        return false;
    }
};
```

**注意 backup / restore**：每進 `dpll` 先存一份 assignment，conflict 或分支失敗時還原。**效率很差**（複製整個 vector），v2 會改 undo stack。但現在這樣好懂、好 debug。

## src/main.cpp

```cpp
#include "parser.hpp"
#include "solver.hpp"
#include <iostream>
#include <fstream>
#include <chrono>

int main(int argc, char** argv) {
    try {
        CNF cnf;
        if (argc > 1) {
            std::ifstream f(argv[1]);
            if (!f) { std::cerr << "cannot open: " << argv[1] << "\n"; return 1; }
            cnf = parse_dimacs(f);
        } else {
            cnf = parse_dimacs(std::cin);
        }

        std::cerr << "c variables: " << cnf.num_vars
                  << ", clauses: " << cnf.clauses.size() << "\n";

        auto start = std::chrono::steady_clock::now();
        DpllSolver solver(std::move(cnf));
        bool sat = solver.solve();
        auto dur = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();

        std::cerr << "c decisions: " << solver.decisions()
                  << ", propagations: " << solver.propagations()
                  << ", time: " << dur << "s\n";

        if (sat) {
            std::cout << "s SATISFIABLE\nv";
            const auto& a = solver.model();
            for (size_t i = 1; i < a.size(); i++) {
                int val = (a[i] == Value::True) ? (int)i : -(int)i;
                std::cout << ' ' << val;
            }
            std::cout << " 0\n";
            return 10;   // SAT competition 慣例
        } else {
            std::cout << "s UNSATISFIABLE\n";
            return 20;
        }
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return 1;
    }
}
```

**Exit code 10 / 20**：SAT competition 慣例 — `10` = SAT、`20` = UNSAT、`0` = unknown。寫 bash 腳本跑 benchmark 會依賴這個。

## Build + 跑

```bash
cd ~/sat-smt/sat-v1
cmake -B build -G Ninja
cmake --build build

# 試 Ch 0 的 hello.cnf
./build/sat-v1 ~/sat-smt/hello/hello.cnf
# s UNSATISFIABLE
```

**完工**。~120 行 C++，你的第一個 solver。

## 對 MiniSat 做 smoke test

`tests/smoke.sh`：

```bash
#!/usr/bin/env bash
# 拿幾個小 CNF 比 v1 跟 minisat 的答案

SAT_V1="../build/sat-v1"
FAIL=0

for cnf in test-cases/*.cnf; do
    ours=$($SAT_V1 "$cnf" 2>/dev/null | head -1 | awk '{print $2}')
    theirs=$(minisat "$cnf" 2>/dev/null | tail -1)
    if [[ "$ours" == "SATISFIABLE" && "$theirs" == "SATISFIABLE" ]] ||
       [[ "$ours" == "UNSATISFIABLE" && "$theirs" == "UNSATISFIABLE" ]]; then
        echo "PASS  $cnf  ($ours)"
    else
        echo "FAIL  $cnf  ours=$ours theirs=$theirs"
        FAIL=1
    fi
done
exit $FAIL
```

手寫幾個 test case 丟 `test-cases/`：

- `sat1.cnf`：Ch 8 的 `(p → q) ∧ (q → r) ∧ p ∧ ¬r` → UNSAT
- `sat2.cnf`：單條 `(p ∨ q)` → SAT
- `hello.cnf`：Ch 0 的 → UNSAT
- `pigeon-3-2.cnf`：3 隻鴿子 2 個洞（你自己產，6 變數）→ UNSAT

跑：

```bash
chmod +x tests/smoke.sh
cd tests && ./smoke.sh
```

全 PASS 才能往下走。**這個 smoke test 從 v1 到 v2 都用**，你會改 solver 幾十次，每次改完靠它證明沒退化。

## 效能基準（不要嚇到）

拿 SAT competition 的 benchmark `uf50-01.cnf`（50 變數、218 clause、uniform random 3-SAT）跑：

```bash
time ./build/sat-v1 uf50-01.cnf
# s SATISFIABLE
# c decisions: 4823, propagations: 18291, time: 0.12s

time minisat uf50-01.cnf
# s SATISFIABLE
# time: 0.002s
```

**v1 比 MiniSat 慢 60 倍**，50 變數還能看。到 100 變數差距會拉到 10000 倍，200 變數 v1 直接跑不完。

這不是 bug，是 **DPLL 沒有 CDCL 的必然結果**：每遇衝突就從頭 backtrack，學不到東西。Ch 12–16 會把差距補回來。

## 三個立刻想改但先忍住的地方

你寫完 v1 應該會很想改這些，**請忍住**，讓 v1 保持可讀。這些是 Ch 12–16 的內容：

1. **Unit propagation 的 O(N×M)**：每輪都掃全部 clause 找 unit，大 instance 卡死。**Two watched literals**（Ch 12）把它改成 amortized O(1)。
2. **Chronological backtracking 的愚蠢**：衝突後退一步，可能下次又遇到同樣衝突。**CDCL backjumping**（Ch 14）跳到真正相關的 level。
3. **Branching 用 `pick_variable` 撿第一個未指派**：極度浪費。**VSIDS**（Ch 15）按「最近參與衝突的程度」排序，大幅加速。

改這三件事前，**先把 v1 所有 test 跑過**。

## 動手練習

1. **全部打完跑起來**：不要複製貼上，自己手敲一遍 solver.hpp。每個 variable 型別的意義要在腦袋裡過一遍。
2. **改 branching heuristic**：把 `pick_variable` 改成「選出現次數最多的未指派變數」，跑 `uf50-01.cnf` 看 decisions 數怎麼變。應該降 30% 以上。
3. **故意做錯**：把 `dpll()` 裡 `assignment_ = backup;` 的第一次（unit_propagate 失敗後）拿掉，跑你的 UNSAT test，觀察錯誤 model 如何出現。**這是初學者最常踩的坑**，踩過一次就記得了。
4. **測量 propagation 深度**：在 `unit_propagate` 每輪印 `changed` 迭代次數，你會看到某些 instance 一次 decision 之後 propagate 幾百次 unit。這是 real-world instance 「unit 鏈很長」的證據。

## 常見誤解

- **「v1 能解 100 變數就算成功」** — 取決於 instance。Random 3-SAT 能；結構化的（pigeon-hole、graph colouring）可能 50 變數就爆。
- **「backup / restore 太慢改用 undo stack 就好」** — 對，v2 會改。但 v1 保留天真 backup 讓你讀得懂演算法本身。先求對，再求快。
- **「unit propagation 就是 unit resolution」** — 精確來說是 unit resolution 的反覆應用直到 fixpoint。SAT paper 裡叫 **Boolean Constraint Propagation (BCP)**，同義詞。

## 自我檢核

- [ ] 有一個可編譯、可跑 DIMACS 的 `sat-v1`
- [ ] 輸出 `s SATISFIABLE` / `s UNSATISFIABLE` 符合 SAT 標準
- [ ] Smoke test 跟 MiniSat 在小 instance 上一致
- [ ] 能印出 decisions / propagations / time 三個指標
- [ ] 讀 `solver.hpp` 能逐行解釋每個動作
- [ ] 知道 v1 會在何種規模被擊垮（~100 變數的結構化 instance）

下一章開始補破網。第一件事：**unit propagation 的效率問題**。Moskewicz 2001 的 two watched literals 是現代 SAT solver 的地基之一，Ch 12 把它拆到底。

→ [Ch 12 — Two Watched Literals](./12-watched-literals.md)
