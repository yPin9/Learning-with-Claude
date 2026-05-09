# SSA + 中端優化：從理論到 LLVM Pass 實作

> 給已懂編譯器前後端、想深入優化引擎的工程師。

前端把程式變成 AST，後端把 IR 變成機器碼——**中端才是讓程式跑快的地方**。這門課從 SSA 的理論根基出發，推導每個經典優化算法的數學正確性，最後用 LLVM C++ API 實作真實的 pass，並用 Alive2 驗證你的優化不會改變程式語意。

## 為什麼學這個？

- **SSA 是現代優化的基石**：LLVM、GCC、V8、HotSpot JIT 全都建在 SSA 上，不懂 SSA 就看不懂這些系統的核心。
- **算法有嚴格推導**：DCE、GVN、LICM 不是「聽起來合理的技巧」，它們有完整的正確性證明——知道為什麼才能知道邊界在哪。
- **LLVM 實作**：每個優化都有對應的 LLVM pass 可以讀、可以改，理論直接對應到真實代碼。
- **Alive2 驗證**：優化的最大風險是「看起來對但其實改變了語意」，Alive2 讓你用形式化方法驗證。

## 先修條件

- 熟悉 LLVM IR 基本語法（`compilers/compiler_backend` 前幾章）
- 能看 C++ 代碼（不需要是 C++ 高手，能讀能改就行）
- 大學程度的離散數學（集合、關係、偏序集）

## 課程地圖

### Part 1 — SSA 理論基礎
- [Ch 0 環境設置](./00-environment-setup.md)
- [Ch 1 為什麼需要 SSA](./01-why-ssa.md)
- [Ch 2 支配關係（Dominance）](./02-dominance.md)
- [Ch 3 支配樹算法：Lengauer-Tarjan](./03-dominator-tree-algorithm.md)
- [Ch 4 支配邊界（Dominance Frontier）](./04-dominance-frontier.md)
- [Ch 5 φ-node 插入：Cytron 算法](./05-phi-insertion.md)
- [Ch 6 SSA 解構：Out-of-SSA](./06-ssa-deconstruction.md)

### Part 2 — 資料流分析
- [Ch 7 資料流分析框架：格論與 Worklist](./07-dataflow-framework.md)
- [Ch 8 活躍變數分析（Liveness Analysis）](./08-liveness-analysis.md)
- [Ch 9 到達定義（Reaching Definitions）](./09-reaching-definitions.md)
- [Ch 10 稀疏條件常數傳播（SCCP）](./10-sccp.md)
- [Ch 11 別名分析基礎（Alias Analysis）](./11-alias-analysis.md)
- [Ch 12 MemorySSA](./12-memory-ssa.md)

### Part 3 — 經典純量優化
- [Ch 13 常數折疊（Constant Folding）與 Peephole](./13-constant-folding.md)
- [Ch 14 死碼消除（DCE 與 ADCE）](./14-dce.md)
- [Ch 15 全局值編號（GVN）](./15-gvn.md)
- [Ch 16 複製傳播（Copy Propagation）](./16-copy-propagation.md)
- [Ch 17 強度削減（Strength Reduction）](./17-strength-reduction.md)
- [Ch 18 InstCombine：LLVM 的 Peephole 引擎](./18-instcombine.md)
- [Ch 19 New PassManager 架構](./19-new-pass-manager.md)
- [練習 A：實作 DCE + Constant Folding Pass](./practice-a-dce-pass.md)

### Part 4 — Loop 優化
- [Ch 20 迴圈識別：Natural Loop 與 Loop Nesting Forest](./20-loop-identification.md)
- [Ch 21 Loop-Closed SSA Form](./21-loop-closed-ssa.md)
- [Ch 22 LICM：Loop-Invariant Code Motion](./22-licm.md)
- [Ch 23 Scalar Evolution（SCEV）](./23-scev.md)
- [Ch 24 迴圈展開（Loop Unrolling）](./24-loop-unrolling.md)
- [Ch 25 向量化基礎：SLP 與迴圈向量化](./25-vectorization.md)
- [Ch 26 Loop Pass Pipeline：優化順序問題](./26-loop-pipeline.md)
- [練習 B：用 opt 觀察 Loop Pass Pipeline](./practice-b-loop-analysis.md)

### Part 5 — 過程間優化
- [Ch 27 Call Graph 與 SCC](./27-call-graph.md)
- [Ch 28 函式內聯（Inlining）](./28-inlining.md)
- [Ch 29 過程間常數傳播（IPCP）與函式特化](./29-ipcp.md)
- [Ch 30 LTO：連結時優化](./30-lto.md)

### Part 6 — 正確性與測試
- [Ch 31 Undefined Behavior 在優化中的角色](./31-ub-and-optimization.md)
- [Ch 32 Alive2、lit + FileCheck、CSmith](./32-testing-alive2.md)
- [練習 C：用 Alive2 驗證 Peephole 規則](./practice-c-alive2.md)

### Final Project
- [從零實作 Mini Optimizer](./final-project-mini-optimizer.md)

## 學習方式建議

1. **推導不要跳**：Lengauer-Tarjan 和 SCCP 的推導看起來痛苦，但跳過之後 LLVM 源碼會是天書。花時間走一遍算法是值得的。
2. **邊讀邊跑 LLVM**：每章都有 `opt` 指令可以在真實 IR 上觀察效果，不要只讀不動手。
3. **先理解再看 LLVM 源碼**：LLVM 的實作加了很多工程細節，先懂理論再看源碼會快很多。

## 參考資料

- 《Engineering a Compiler》— Cooper & Torczon（Chapter 8–10 是 SSA 和優化的聖經）
- 《SSA-based Compiler Design》— Braun et al.（免費 PDF，最完整的 SSA 教材）
- LLVM Passes 源碼：`llvm/lib/Transforms/`
- Alive2：`alive2.llvm.org`（線上驗證 IR 等價性）
