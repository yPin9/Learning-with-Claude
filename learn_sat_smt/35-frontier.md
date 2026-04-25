# Ch 35 — 前沿：MCSat、NRA、CAD

> 目標：看 SMT 研究的當代前沿。**MCSat** 是新一代架構、跟 DPLL(T) 競爭中。**NRA**（非線性實數）用 cylindrical algebraic decomposition 解，是 SMT 最複雜的演算法之一。最後一瞥：incremental SAT、量子 SAT、ML-guided SAT 的萌芽。

## MCSat: Model Constructing SAT

**de Moura, Jovanović 2013**。和 DPLL(T) 對立的新架構。

### DPLL(T) 的瓶頸

DPLL(T) 把 Boolean 跟 theory 分層：

- SAT 找 Boolean assignment
- Theory check consistency
- 不一致就學 lemma 回 SAT

**問題**：theory model 是 SAT 的 by-product，不直接構造。對 NRA 這種需要 explicit model construction 的 theory 不友好。

### MCSat 的招

**Boolean variable 跟 theory variable 同等對待**。Solver assign value（不只 T/F）給每個 variable。每個 step：

- Pick a variable
- Compute compatible value (Boolean: T/F; Real: 任何 satisfying current constraints)
- Propagate
- Conflict 時 explain 並 backjump

**統一的 search**：不再「SAT layer 跟 theory layer」分開。conflict analysis 用 **Algebraic Datatypes** 概念。

### Yices 2 走 MCSat

Yices 2 (SRI) 的 NRA / LRA tactic 用 MCSat。對某些 NRA instance 比 Z3 (DPLL(T)) 快幾個數量級。Z3 也有 MCSat tactic (`smt.mcsat`)，但 default 還是 DPLL(T)。

### MCSat 仍在發展

- Theory plugin 還少（NRA、LRA、UF 有；BV 沒）
- Implementation 比 DPLL(T) 複雜
- 但長期看 MCSat 可能取代 DPLL(T) 為主架構

## NRA: 非線性實數算術

**NRA** = LRA + 乘法（含 `x * y`、`x²`）。例：

```smt2
(set-logic QF_NRA)
(declare-const x Real)
(declare-const y Real)
(assert (= (+ (* x x) (* y y)) 1))   ; 單位圓上
(assert (> (+ x y) 1.5))
(check-sat)
```

**Tarski 1948** 證明 NRA decidable，演算法：**Cylindrical Algebraic Decomposition (CAD)**.

## CAD：Cylindrical Algebraic Decomposition

**Collins 1975**。把 R^n 切成 finite cells，每個 cell 內所有 polynomial 的 sign 不變。Solver 對每個 cell 試一個 sample point。

直覺（n = 2）：

- 給多項式 `p_1(x, y), p_2(x, y), ...`
- 投影到 x 軸：得 univariate polynomials in x
- 找 x 軸的 critical points：根 + intersection
- 每兩個 critical points 之間是一個 **cell**
- 每個 cell 對應一個 y-範圍的「stack」of cells
- 在 stack 中按 y-roots 切

最終 R² 切成 ~n^4 cell，每個 cell 用 sample point check satisfiability。

### CAD 複雜度

**Doubly exponential** in 變數數量。對 5+ 變數已經痛苦。但 NRA SMT problem 通常變數少（< 10），practically tractable。

### CAD 在 Z3

Z3 的 `smt.arith.solver=2` 或 `qfnra-nlsat` tactic 開 NRA support。底層用 **NLSat** (Jovanović–de Moura 2012) — 結合 MCSat 跟 CAD 的混合。

實作：~5000 行 C++、極複雜的 algebraic geometry code。一般 SMT user 不會碰、但 Z3 內部就靠它。

## NIA：非線性整數

**NIA = NRA + integer constraint**。**Hilbert's 10th problem**：NIA undecidable。

但 **bounded NIA**（變數有上下界）可決。SMT solver 用 NRA + bound + branch & bound。Z3 的 NIA tactic 是 incomplete heuristic，timeout 後回 `unknown`。

## Real Algebraic Geometry

NRA 解決需要 real algebraic geometry 的工具：

- **Resultant**：消去變數的 polynomial
- **Subresultant chain**：root 的精確 sign 分析
- **Real root isolation**：給 polynomial 找根的精確 interval
- **Sturm sequences**：count real roots 在區間內

這些都有 algorithm + library（**MathSAT**、**RealCAD**、**redlog**）。SMT solver 內部用。

## Incremental SMT

**Incremental SAT**：solver 保留 state、加新 constraint、push/pop、不重算。

實務：
- DSL 漸進式 typecheck
- Symbolic execution 一個個 path 加 constraint
- IDE 即時驗證

Z3 的 incremental mode 用 push/pop 維護 trail。**Theory solver 也要 incremental** — 練習 D 的 EUF 已是 incremental。

## ML for SAT

最近研究：**ML 學 branching heuristic / restart policy**。

- **Liang et al. 2016 LRB**：用 reinforcement learning 學 VSIDS 替代
- **Selsam et al. 2019 NeuroSAT**：GNN 直接預測 SAT/UNSAT（toy size only）
- **NN-guided search**: 用 NN 給 branch suggestion，CDCL 採納

這些目前**沒打敗 hand-tuned heuristic**，但研究持續。SAT competition 偶有 ML-augmented entries。

## 量子 SAT

**Grover's algorithm** 在 SAT 上有 `O(√(2^n))` quantum speedup。對 NP-complete 來說 polynomial speedup（不是 P）。

**現狀**：純理論興趣。實際 quantum hardware 還無法處理 100+ var SAT。但有研究探索 quantum annealing (D-Wave) 解 SAT-like 問題。

## Parallel SAT 進化

過去：portfolio + clause sharing。
現在：**Mallob (cluster-scale)**, **CryptoMiniSat distributed**, **Painless framework**.

Cube-and-conquer + distributed 可以解 **10⁹ variable** scale 的 SAT — Pythagorean Triples Problem (Heule 2016) 是里程碑。

## SAT/SMT 跟 LLM 的交集

LLM 寫 verification spec / synthesize SMT formula：

- GPT-4 直接寫 Z3 input 解 puzzle
- LLM 給 program proof obligation、tool 翻譯成 SMT
- LLM-aided trigger selection (E-matching)

**Combination：** LLM 做 high-level reasoning、SMT 做 low-level verification。**Mixing scale**，邊界鬆動中。

## Open Problems

- **CDCL 對 PHP-style 永遠 exponential**：能否新演算法繞過？
- **NRA 的 doubly-exponential**：有 better algorithm 嗎？
- **量詞處理**：能否做到 95% complete instead of 80%?
- **Proof-carrying**：solver 全部加 proof production 而 < 2× slowdown？
- **ML / classical 結合**：ML 真能 outperform hand-tuned 嗎？

每年 SAT/SMT competition、研討會（CAV、SAT、SMT）有新 paper。**這個領域不是 stable，仍在快速演化**。

## 動手練習

1. **NRA 試駕**：用 Z3 解一個 NRA instance（含 `x*x`），看時間。對比改成 LRA-only 的 instance。
2. **MCSat tactic**：`(set-option :smt.mcsat true)`，跑 quantifier-free arithmetic instance、對比 default。
3. **Pythagorean Triples 縮版**：找對應 paper、看 cube-and-conquer 怎麼用，理解 200 TB proof 的尺度。
4. **Z3 vs Yices on NRA**：同一個 NRA instance 兩 solver 比，看 MCSat 跟 DPLL(T) 表現差異。

## 常見誤解

- **「MCSat 已經取代 DPLL(T)」** — 沒有。DPLL(T) 仍是主流，MCSat 在某些 fragment 強。
- **「NRA 已解決」** — 解決 in theory（CAD），但效能仍研究中。
- **「ML 即將超越 hand-tuned solver」** — 短期內不會。Hand-tuning 30 年累積的 heuristic 很難取代。
- **「量子計算會打破 SAT 困難」** — Grover 只 polynomial speedup，不解 NP = P。

## 自我檢核

- [ ] 知道 MCSat 跟 DPLL(T) 的架構差異
- [ ] 懂 CAD 的基本想法（cell decomposition）
- [ ] 知道 NRA decidable 但 doubly exponential
- [ ] 理解 NIA undecidable 的根因（Hilbert's 10th）
- [ ] 聽過 Pythagorean Triples Problem 的 SAT 解
- [ ] 知道 ML for SAT 是研究領域、現在沒成熟產品

整門課的章節到此結束。最後一個檔案是 **final project** — 把所學打包做出 mini-SMT solver，支援 QF_UF + QF_LRA。完成後你可以正式說「我會寫 SMT solver」。

→ [Final Project — mini-smt](./final-project-mini-smt.md)
