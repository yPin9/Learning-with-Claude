# Ch 8 — SAT 問題與 DIMACS 格式

> 目標：精確定義 SAT 問題的各種變體（k-SAT、Horn-SAT...）、講清楚為什麼 2-SAT 容易而 3-SAT 困難、徹底搞懂 **DIMACS 格式**（接下來每個 solver 都吃這個輸入），寫一個 baseline brute-force solver 當 sanity check。

## SAT 問題的精確定義

**SAT 問題（布林可滿足性問題）**：給一個 CNF 公式 `φ`，問存不存在一組 valuation 讓 `φ = ⊤`。

輸入：CNF  
輸出：`SAT` + model（如果 SAT）；`UNSAT`（如果 UNSAT）

**不是 CNF 就不是 SAT 問題**（嚴格意義上）。你如果拿一般 formula，要先 Tseitin 轉 CNF。SAT solver 只認 CNF。

## k-SAT：clause 最大長度

**k-SAT** = CNF 中每條 clause 至多 `k` 個 literal。

| 變體 | 複雜度 | 備註 |
|---|---|---|
| 1-SAT | O(n) trivial | 每條 clause 就一個 literal，直接 assign |
| 2-SAT | O(n+m) 線性 | Aspvall–Plass–Tarjan 1979：用 SCC |
| 3-SAT | NP-complete | Cook–Levin 1971 |
| k-SAT (k ≥ 3) | NP-complete | 都能 reduce 到 3-SAT |

**2-SAT 為什麼線性**？把每條 clause `(a ∨ b)` 看成兩個蘊含：`¬a → b` 和 `¬b → a`，建 **implication graph**。UNSAT 當且僅當某個變數 `x` 和 `¬x` 在同一 SCC。漂亮。

**3-SAT 為什麼是 NP-complete 的核心**？Cook 1971 直接用它。任何 NP 問題能多項式 reduce 到 3-SAT。所以寫 SAT solver 基本等於寫 **通用 NP-complete solver**。

## 其他常見 fragment

| 名稱 | 定義 | 性質 |
|---|---|---|
| Horn-SAT | 每條 clause 最多一個正 literal | 線性（Unit propagation 全做完就行） |
| Dual-Horn | 每條 clause 最多一個負 literal | 對稱 |
| 2-SAT | 前面講過 | 線性 |
| XOR-SAT | 每條 clause 是 XOR 不是 OR | 多項式（高斯消去） |
| Nested SAT | 特殊結構 | 多項式 |

**但「真實世界」的 instance 很少在這些好處理的 fragment 裡**。CBMC 產的、硬體驗證產的、planning 產的，都是一般 3-SAT（經常 4-SAT、5-SAT）。這就是為什麼 CDCL 要通吃。

## DIMACS CNF 格式

SAT 圈的事實標準（DIMACS 是 1993 年第一次 SAT challenge 時定的）。Ch 0 已經看過，這章講透：

```
c Optional comment lines (starts with 'c')
c Another comment
p cnf <num_variables> <num_clauses>
<literal> <literal> ... 0
<literal> <literal> ... 0
...
```

規則：

- **`c` 開頭是 comment**，任何位置都可
- **`p cnf N M`** 是 header 必須正好一行，`N` 個變數、`M` 條 clause
- 變數編號從 **1 開始**（0 保留作 terminator）
- **正數** `k` = `xₖ`、**負數** `-k` = `¬xₖ`
- **每條 clause 以 `0` 結尾**
- clause 可以跨行（空白分隔）
- 末尾 newline 非必要但建議

範例（`(x₁ ∨ ¬x₂ ∨ x₃) ∧ (¬x₁ ∨ x₂) ∧ (¬x₃)`）：

```
c my formula
p cnf 3 3
1 -2 3 0
-1 2 0
-3 0
```

就這麼簡單。

### 常見陷阱

- **`N` 宣告過大**：多出來的變數會被當 free（可真可假）。很多 encoder 會刻意這樣做 — 報一個寬鬆的 `N`，再只用其中一部分。
- **`N` 宣告過小**：超出的 literal 未定義行為，solver 通常直接 error。
- **空 clause**：寫成一行只有 `0`。這讓整個 instance 立刻 UNSAT（空 clause 永遠不能 satisfy）。
- **重複 literal**：`(p ∨ p ∨ q)` 等價於 `(p ∨ q)`。solver 通常會自動去重。
- **Tautology clause**：`(p ∨ ¬p ∨ q)` 永遠為真，可直接刪掉。Ch 17 preprocessing 會講。

### Model 輸出格式（標準）

SAT solver 說 SAT 時的 model 格式：

```
s SATISFIABLE
v 1 -2 3 -4 0
```

- `s` 行是 status（`s SATISFIABLE`、`s UNSATISFIABLE`、`s UNKNOWN`）
- `v` 行是 model，每個變數一個 literal，正代表 true、負代表 false，`0` 結尾

你後面寫自己的 solver 要按這格式輸出。

## 一個 C++ DIMACS Parser

```cpp
#include <vector>
#include <iostream>
#include <sstream>
#include <string>

using Lit = int;
using Clause = std::vector<Lit>;

struct CNF {
    int num_vars = 0;
    std::vector<Clause> clauses;
};

CNF parse_dimacs(std::istream& in) {
    CNF cnf;
    std::string line;
    bool header_seen = false;
    while (std::getline(in, line)) {
        if (line.empty() || line[0] == 'c') continue;
        std::istringstream iss(line);
        if (!header_seen && line[0] == 'p') {
            std::string p, fmt; int n, m;
            iss >> p >> fmt >> n >> m;
            cnf.num_vars = n;
            cnf.clauses.reserve(m);
            header_seen = true;
            continue;
        }
        // 一條 clause，可能跨行（這個簡化版不跨行）
        Clause c;
        Lit l;
        while (iss >> l) {
            if (l == 0) break;
            c.push_back(l);
        }
        if (!c.empty()) cnf.clauses.push_back(std::move(c));
    }
    return cnf;
}
```

**不處理跨行 clause 的簡化版**。Rigorous 版要把整個 stream tokenize 後再按 `0` 切。SAT competition 的 benchmark 幾乎都一行一條 clause，簡化版夠用。

## Baseline Brute-Force Solver

最笨的 SAT 演算法：**枚舉所有 `2^n` valuation**。

```cpp
#include <bit>

bool eval_clause(const Clause& c, uint64_t assignment) {
    // assignment 的第 i 位代表變數 i+1 的真值
    for (Lit l : c) {
        int v = std::abs(l);
        bool val = (assignment >> (v - 1)) & 1;
        bool lit_val = (l > 0) ? val : !val;
        if (lit_val) return true;
    }
    return false;
}

bool brute_force(const CNF& cnf, uint64_t& model_out) {
    if (cnf.num_vars >= 64) throw std::runtime_error("brute force: too many vars");
    uint64_t total = 1ULL << cnf.num_vars;
    for (uint64_t a = 0; a < total; a++) {
        bool all_sat = true;
        for (const auto& cl : cnf.clauses) {
            if (!eval_clause(cl, a)) { all_sat = false; break; }
        }
        if (all_sat) { model_out = a; return true; }
    }
    return false;
}
```

**這東西到 30 個變數已經跑不動**。但它有個無可替代的用途：**當你寫 DPLL/CDCL 時的 sanity check**。小題兩邊都跑，結果不一致你的 solver 有 bug。**永遠留著它**。

## 測試自己的 parser + brute force

把 Ch 0 的 `hello.cnf` 丟進去：

```cpp
int main() {
    auto cnf = parse_dimacs(std::cin);
    std::cerr << "Read " << cnf.num_vars << " vars, " << cnf.clauses.size() << " clauses\n";
    uint64_t model;
    if (brute_force(cnf, model)) {
        std::cout << "s SATISFIABLE\nv ";
        for (int i = 1; i <= cnf.num_vars; i++) {
            bool val = (model >> (i-1)) & 1;
            std::cout << (val ? i : -i) << " ";
        }
        std::cout << "0\n";
    } else {
        std::cout << "s UNSATISFIABLE\n";
    }
}
```

編譯跑：

```bash
g++ -std=c++20 -O2 brute.cpp -o brute
./brute < hello.cnf   # 應該跟 minisat 結果一致
```

兩邊一致，你的 parser 正確。

## 動手練習

1. **手寫 DIMACS**：把 `(p → q) ∧ (q → r) ∧ p ∧ ¬r` 寫成 DIMACS。先手動展 CNF（`¬p ∨ q`、`¬q ∨ r`），再編 `p = 1, q = 2, r = 3`。丟 brute force 應該 UNSAT。
2. **壓力測試**：產生一個 20 變數、80 random clause 的 DIMACS，跑 brute force，記錄時間。再跑 MiniSat，記錄時間。差幾倍？（通常 MiniSat 毫秒、brute 幾秒）
3. **故意做錯**：把 DIMACS 的 `0` 結尾忘掉一條，看 parser 怎麼反應。修 parser 讓它會報錯而不是 silently 接受。

## 常見誤解

- **「DIMACS 變數從 0 開始」** — 錯。0 是 terminator，變數從 1 開始。
- **「每條 clause 必須一行」** — 不是標準規定。理論上 clause 可跨行，但幾乎所有 benchmark 都一行一條，你的 parser 簡化處理沒問題。
- **「brute force 沒用」** — 錯。它是你後面幾千行 CDCL code 的正確性參照，小 instance 一定要跟它對。

## 自我檢核

- [ ] 寫得出 k-SAT 的定義，知道 2-SAT 線性、3-SAT NP-complete
- [ ] 徹底熟悉 DIMACS 格式（`p cnf N M`、`0` 結尾、正負 literal）
- [ ] 寫得出能吃 DIMACS 的 parser
- [ ] 寫得出 brute force solver 當 sanity 參照
- [ ] 知道 SAT solver 的標準輸出格式（`s` / `v` 行）

下一章看 SAT 的第一個非 trivial 演算法 — **Davis–Putnam 1960**。它不是你想用的演算法（會爆），但你得知道它為什麼爆、DPLL 怎麼從它演化出來。這是 SAT solver 家族的**起點**。

→ [Ch 9 — Davis–Putnam 演算法](./09-davis-putnam.md)
