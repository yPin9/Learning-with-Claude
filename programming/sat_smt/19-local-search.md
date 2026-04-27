# Ch 19 — Local search：WalkSAT、ProbSAT

> 目標：換一個完全不同的 SAT 解法家族 — **local search**（隨機搜索）。它不用 CDCL 的 unit propagation、不 learn clause、也不保證完備，但**對某些 instance 比 CDCL 快 10–100 倍**，而且 code 極簡。現代 SAT portfolio（Ch 21）會同時跑 CDCL 和 local search。

## 兩個 SAT 世界

| 維度 | CDCL (Ch 10–18) | Local Search |
|---|---|---|
| 找 SAT 的方法 | 系統性搜索 + backtrack | 從一個 total assignment 出發、局部翻變數 |
| 找 UNSAT 的方法 | 可以（refutation proof） | **不行**（沒停止條件） |
| 完備性 | 完備 | **不完備**（可能找不到 SAT 即使有） |
| 適合的 instance | 結構化（工業、硬體） | 隨機、相對均勻 |
| 記憶體 | 大（learned clause） | 極小（只存當前 assignment） |
| Code 量 | 數千行 | ~50 行 |

**關鍵**：local search 只能證 SAT，不能證 UNSAT。但對 random 3-SAT、planning、SAT-based combinatorial optimization，它極其有效。

## WalkSAT：1994 的經典

Selman, Kautz, Cohen 1994。基本演算法：

```
WalkSAT(CNF φ, max_flips, max_tries, noise_p):
    for try = 1 to max_tries:
        A ← random total assignment
        for flip = 1 to max_flips:
            if A satisfies φ: return SAT with A
            pick a random unsatisfied clause C
            with probability p:
                flip a random variable in C
            else:
                flip the variable in C that maximizes satisfied clauses
    return UNKNOWN
```

**50 行 C++ 就寫完**。核心四件事：

1. **隨機 assignment 起始**
2. **每輪挑一個 unsatisfied clause**
3. **70% 機率貪心 flip**（choose variable that maximizes #satisfied）
4. **30% 機率隨機 flip**（避免 local minima）

## 寫成 C++

```cpp
#include <vector>
#include <random>

struct WalkSAT {
    const CNF& cnf;
    std::vector<bool> assign;      // 1-indexed
    std::vector<int>  unsat_clauses;  // index 進 cnf.clauses
    std::mt19937 rng;

    WalkSAT(const CNF& c, uint64_t seed) : cnf(c), assign(c.num_vars + 1), rng(seed) {}

    bool clause_satisfied(int ci) const {
        for (Lit l : cnf.clauses[ci]) {
            bool val = assign[var_of(l)];
            if ((l > 0) == val) return true;
        }
        return false;
    }

    void init_assignment() {
        std::uniform_int_distribution<int> bit(0, 1);
        for (int v = 1; v <= cnf.num_vars; v++) assign[v] = bit(rng);
        unsat_clauses.clear();
        for (size_t i = 0; i < cnf.clauses.size(); i++)
            if (!clause_satisfied(i)) unsat_clauses.push_back(i);
    }

    // 翻變數 v 後，unsat_clauses 更新 + 回傳「#clauses that become unsat minus #that become sat」
    int flip_variable(int v) {
        assign[v] = !assign[v];
        // 這裡簡化：重新掃整個 CNF。實務上維護 per-variable 的 make/break counts。
        std::vector<int> new_unsat;
        for (size_t i = 0; i < cnf.clauses.size(); i++)
            if (!clause_satisfied(i)) new_unsat.push_back(i);
        int delta = (int)new_unsat.size() - (int)unsat_clauses.size();
        unsat_clauses = std::move(new_unsat);
        return delta;
    }

    bool solve(int max_flips, int max_tries, double noise_p) {
        std::uniform_real_distribution<double> uni(0, 1);
        for (int t = 0; t < max_tries; t++) {
            init_assignment();
            for (int f = 0; f < max_flips; f++) {
                if (unsat_clauses.empty()) return true;
                int ci = unsat_clauses[std::uniform_int_distribution<size_t>(0, unsat_clauses.size()-1)(rng)];
                const auto& clause = cnf.clauses[ci];
                int var_to_flip;
                if (uni(rng) < noise_p) {
                    // random walk
                    Lit l = clause[std::uniform_int_distribution<size_t>(0, clause.size()-1)(rng)];
                    var_to_flip = var_of(l);
                } else {
                    // greedy：flip the var that minimizes break count
                    int best_v = 0, best_delta = INT_MAX;
                    for (Lit l : clause) {
                        int v = var_of(l);
                        // simulate flip
                        assign[v] = !assign[v];
                        int delta = 0;
                        for (size_t i = 0; i < cnf.clauses.size(); i++)
                            if (!clause_satisfied(i)) delta++;
                        assign[v] = !assign[v];  // 還原
                        if (delta < best_delta) { best_delta = delta; best_v = v; }
                    }
                    var_to_flip = best_v;
                }
                flip_variable(var_to_flip);
            }
        }
        return false;
    }
};
```

這個版本 **效能差**（每次 flip 全掃 CNF），但**邏輯對**。工業級的 WalkSAT（ubcsat、probSAT）用 **incremental update** 把 flip 變 O(clause 相關長度)。

## 提升版：ProbSAT

Balint & Schöning 2012。**沒有固定的 greedy / random 切換**，而是根據「break count」算機率：

```
對 unsatisfied clause C，對每個 variable v in C：
    break_count(v) = 翻 v 後會變 unsat 的 clause 數
    weight(v) = 1 / (break_count(v) + ε)^exp
pick v ∝ weight(v)     // 按 weight 加權抽樣
```

`exp` 是 exponent，通常 2.0–2.5。Break 少的變數被選機率高（貪心），但 break 高的還是有機會（噪音）。**平滑版的 WalkSAT**。

ProbSAT 在 SAT Competition 的 random instance track 連拿數年冠軍。

## Local Search 的致命弱點

### 1. UNSAT 判不了

Local search 在 UNSAT instance 上會無限翻、永遠找不到解。標準做法：**設 budget**（max_flips / 時間上限），超過回 UNKNOWN。**不要期待它證 UNSAT**。

### 2. Structured instance 爛

硬體 verification、CBMC 產的 instance 有**深 dependency chains**。Local search 碰到這種要翻對幾百個變數才一步 improvement、機率極低。CDCL 的 unit propagation 一路推過去就完事。

### 3. 參數敏感

`noise_p`、`exp`、`max_flips` 等參數對 solve time 影響大。工業級 local search solver 會 **auto-tune** 這些。

## Local Search 為何在 random 強

Random 3-SAT 的 solution space 是**相對均勻的塊狀**，從任一起點往滿足方向走有高機率走到解。CDCL 的 unit propagation 在 random 上推不出多少（random 沒結構），學的 clause 品質差。**各取所需**。

## Hybrid：CDCL + Local Search

**Portfolio** approach（Ch 21）：同時跑多個 solver、誰先找到解就停：

```
Thread 1: CaDiCaL (CDCL)
Thread 2: Kissat with aggressive inprocessing
Thread 3: YalSAT (ProbSAT variant)
Thread 4: ...
```

**SAT Competition 的 Parallel Track** 都是這樣。現代 portfolio（CryptoMiniSat）裡 local search 常是第 3、4 個 thread。

另一個結合：**Restart 時用 local search 暖身**。CDCL 在 restart 後，先跑幾千次 local-search flip、找到一個「接近 SAT」的 assignment、再從那開始 CDCL。

## SLS 家族的其他成員

| 演算法 | 特色 |
|---|---|
| GSAT (1992) | Greedy only, no noise. 容易卡 local minima |
| WalkSAT (1994) | 加 noise |
| Novelty / R-Novelty | 加 memory: avoid recently flipped vars |
| AdaptNovelty+ | 自適應 noise |
| ProbSAT (2012) | 機率加權 |
| YalSAT (2014) | ProbSAT 工業版 |
| CCAnr (2019) | Configuration Checking + noise-based |

SLS (Stochastic Local Search) 是獨立的領域，有專門的書（Hoos & Stützle 2004）。

## Weighted MAX-SAT

Local search 天然適合 **MAX-SAT** — 不要求所有 clause sat、只要滿足最多 clause。WalkSAT 的 greedy 直接拿來用，把「minimize break count」換成「maximize soft-weight-sum」。

工業應用：combinatorial optimization、portfolio optimization、scheduling。

## 動手練習

1. **寫一個簡化版 WalkSAT**：上面 50 行的 baseline 版，跑 `uf100-0XX.cnf`。你會看到 random 3-SAT 幾乎瞬解。
2. **對比 CDCL v2**：同一批 random 3-SAT instance，分別跑 v2 和你的 WalkSAT。SAT instance 比速度、UNSAT instance 看 WalkSAT 超時（永遠回不來）。
3. **調 noise_p**：試 0.1、0.3、0.5、0.7、0.9，看 solve time 曲線。通常 0.3–0.5 是 sweet spot。
4. **極端 case**：寫一個 100 變數的 pigeonhole UNSAT instance（101 鴿子 100 洞），WalkSAT 會用多久承認失敗？（答案：永遠）

## 常見誤解

- **「Local search 比 CDCL 差」** — 不對，兩者互補。**Random instance local search 常贏**。
- **「Local search 沒學術價值」** — 錯。SAT Competition 有 random track 專門跑 local search。
- **「Local search 不完備所以沒實務用」** — 錯。對「只要找 SAT」的應用（planning、scheduling、crypto）極其有效。UNSAT 判定丟給 CDCL 就好。
- **「WalkSAT 是最好的 local search」** — 2012 後被 ProbSAT 打敗。現代主流是 ProbSAT 變體。

## 自我檢核

- [ ] 寫得出 WalkSAT 的核心循環
- [ ] 懂 ProbSAT 的加權抽樣
- [ ] 說得出 local search 不能做 UNSAT 的原因
- [ ] 懂 CDCL vs local search 的適合場景對比
- [ ] 知道 portfolio 為什麼同時跑兩家

下一章回到 CDCL 的世界，做一件 **非常重要但容易忽略** 的事 — **產生 UNSAT 的證明**。SAT solver 說 UNSAT，憑什麼信它？DRAT proof 給出機器可驗證的答案，Ch 20 細講。

→ [Ch 20 — DRAT proof 與 UNSAT 驗證](./20-drat-proof.md)
