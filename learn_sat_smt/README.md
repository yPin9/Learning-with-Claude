# SAT / SMT 學習筆記：從命題邏輯到自刻 mini-SMT Solver

> 給對邏輯、編譯器、形式化驗證有興趣，想真的把 solver 刻出來、而不只是會用 Z3 的工程師。

這系列從命題邏輯的語法與語義出發，用 C++20 寫出兩版 SAT solver（乾淨 DPLL → mini-CDCL），再把 SMT 的五個核心理論（EUF、LRA、LIA、Bit-vector、Array）拆開來看，最後端出一個支援 **QF_UF + QF_LRA** 的 mini-SMT solver，吃 SMT-LIB v2 子集。全程不靠 MiniSat/Z3 當黑盒。

## 為什麼學這個？

- **SAT/SMT 是現代形式化方法的引擎**：程式驗證（CBMC、SeaHorn）、symbolic execution（KLEE、angr）、scheduler、planner、optimizing compiler，底下都是 SMT。看不懂 solver，看不懂現代 verification。
- **CDCL 是演算法工程的教科書級案例**：watched literals、VSIDS、restart，示範了「理論正確」跟「工程高效」中間隔了多少層 heuristic，讀完會對「工程」兩個字重新定義。
- **SMT 把邏輯變成可計算**：Nelson–Oppen theory combination 是 CS 最優雅的結果之一，值得親手刻一次。

## 課程地圖

### Part 0 — 邏輯基礎
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 為什麼學 SAT/SMT：全景圖](./01-overview.md)
- [Ch 2 命題邏輯的語法與語義](./02-propositional-syntax-semantics.md)
- [Ch 3 邏輯等價與標準式（NNF / CNF / DNF）](./03-normal-forms.md)
- [Ch 4 Tseitin 轉換](./04-tseitin-transformation.md)
- [Ch 5 推論系統：Hilbert、自然演繹、Sequent](./05-proof-systems.md)
- [Ch 6 Resolution 與 refutation 完備性](./06-resolution.md)
- [Ch 7 一階邏輯預覽](./07-first-order-preview.md)
- [練習 A：Tseitin CNF 轉換器](./practice-a-tseitin.md)

### Part 1 — SAT Solver 從零到 CDCL
- [Ch 8 SAT 問題與 DIMACS 格式](./08-sat-problem-dimacs.md)
- [Ch 9 Davis–Putnam 演算法](./09-davis-putnam.md)
- [Ch 10 DPLL 演算法](./10-dpll.md)
- [Ch 11 實作 DPLL（v1）](./11-implement-dpll.md)
- [Ch 12 Two Watched Literals](./12-watched-literals.md)
- [Ch 13 CDCL 核心：implication graph、conflict analysis](./13-cdcl-core.md)
- [Ch 14 Backjumping 與 1UIP](./14-backjumping-1uip.md)
- [Ch 15 VSIDS、phase saving、Luby restart](./15-heuristics-restart.md)
- [Ch 16 實作 CDCL（v2）](./16-implement-cdcl.md)
- [Ch 17 Preprocessing](./17-preprocessing.md)
- [Ch 18 Inprocessing 與 vivification](./18-inprocessing.md)
- [Ch 19 Local search：WalkSAT、ProbSAT](./19-local-search.md)
- [Ch 20 DRAT proof 與 UNSAT 驗證](./20-drat-proof.md)
- [Ch 21 並行 SAT：portfolio、clause sharing](./21-parallel-sat.md)
- [練習 B：完整 DPLL solver](./practice-b-dpll-solver.md)
- [練習 C：CDCL + watched literals](./practice-c-cdcl-solver.md)

### Part 2 — SMT Solver 核心
- [Ch 22 SMT 全貌與 SMT-LIB v2](./22-smt-overview.md)
- [Ch 23 DPLL(T) 架構](./23-dpll-t-architecture.md)
- [Ch 24 Theory solver 介面](./24-theory-solver-interface.md)
- [Ch 25 EUF 與 congruence closure](./25-euf-congruence-closure.md)
- [Ch 26 實作 EUF theory solver](./26-implement-euf.md)
- [Ch 27 LRA：Simplex for SMT](./27-lra-simplex.md)
- [Ch 28 LIA：branch & bound、Gomory cuts](./28-lia-branch-bound.md)
- [Ch 29 Bit-vector 理論](./29-bitvector.md)
- [Ch 30 Array 理論](./30-arrays.md)
- [Ch 31 Theory combination：Nelson–Oppen](./31-theory-combination.md)
- [Ch 32 Quantifiers：E-matching、MBQI](./32-quantifiers.md)
- [Ch 33 Model 與 Proof production](./33-model-proof.md)
- [練習 D：EUF congruence closure](./practice-d-euf.md)
- [練習 E：DPLL(T) 骨架串 EUF](./practice-e-dpll-t-skeleton.md)

### Part 3 — 實戰與前沿
- [Ch 34 應用：verification、symbolic execution](./34-applications.md)
- [Ch 35 前沿：MCSat、NRA、CAD](./35-frontier.md)
- [Final Project：mini-smt（QF_UF + QF_LRA）](./final-project-mini-smt.md)

## 學習方式建議

1. **別只讀公式，動手抄一遍**：Part 0 的真值表、Resolution、Tseitin 用紙筆各做三題，再拿 solver 對答案。
2. **Ch 11 跟 Ch 16 的 code 一定要自己打一次**：複製貼上不算學過，CDCL 的 bug 只有親手踩過才會懂為什麼要 learn 那條 clause。
3. **跟現有 solver 對照**：每寫完一版 solver 就拿 MiniSat / CaDiCaL 跑同樣的 benchmark，看差距在哪。差 100 倍正常，差 10000 倍表示你演算法有錯。
4. **讀一手論文**：Ch 12 讀 Moskewicz 2001（Chaff），Ch 27 讀 Dutertre–de Moura 2006（Simplex for SMT）。我會寫摘要，但原文的細節值得看。

## 參考資料

- *Handbook of Satisfiability*, 2nd ed. — Biere, Heule, van Maaren, Walsh（IOS Press, 2021）— SAT 最全的手冊
- *Decision Procedures: An Algorithmic Point of View*, 2nd ed. — Kroening, Strichman（Springer, 2016）— SMT 入門首選
- *The Calculus of Computation* — Bradley, Manna（Springer, 2007）— 邏輯與驗證教科書
- *SAT/SMT by Example* — Dennis Yurichev（免費 PDF）— 實戰案例為主
- Z3 tutorial：<https://microsoft.github.io/z3guide/>
- SAT Competition：<http://satcompetition.org/>
- SMT-LIB 標準：<https://smt-lib.org/>
