# Ch 34 — 應用：verification、symbolic execution

> 目標：看 SAT/SMT 在工業界做什麼。Part 0–2 學了演算法跟實作，這章把視角拉開：哪些 tool 把 SMT 當 backend、它們怎麼把 problem encode 成 SMT、為什麼這些 tool 要存在。**讀完你會理解 SMT 為什麼是 verification 領域的 GPT-4**。

## 主要應用領域

```
       Program Verification  ──────► CBMC, SeaHorn, Dafny, Verus, F*
                │
       Symbolic Execution ───────────► KLEE, angr, SPF, Mayhem
                │
       Hardware Verification  ───────► Yosys, Boolector + flow
                │
       Model Checking ──────────────► NuXMV, IC3, BMC tools
                │
       Type System    ──────────────► Liquid Haskell, refinement types
                │
       Synthesis      ──────────────► Sketch, Rosette
                │
       Crypto / Constraints  ────────► CryptoMiniSat, custom solver
                │
       Constraint Solving (planning) ──► UNF MaxSAT, OR-Tools
```

## 1. Bounded Model Checking (BMC)

**最早 (1999) 的 SMT 殺手應用**。給程式 + assertion + 深度 N，問「N 步內 assertion 可能違反嗎？」

工具：**CBMC**（Clarke et al.）— 抓 C/C++ 的 undefined behavior、null deref、buffer overflow。

```c
int x, y;
__CPROVER_assume(0 <= x && x <= 10);
y = x * x;
__CPROVER_assert(y >= 0, "y is non-negative");
```

CBMC 把這轉成 QF_BV：

```
∧ (0 ≤ x) ∧ (x ≤ 10)
∧ (y = bvmul(x, x))
∧ ¬(bvsge(y, 0))     // assertion 的 negation
```

丟 SMT solver。SAT → 找到反例（counter-example）；UNSAT → 此深度內 safe。

**業界使用**：Linux kernel 用 CBMC verify locking、Amazon AWS 對 IAM policy 跑 CBMC。

## 2. Symbolic Execution

**動態執行 + symbolic constraints**。每條 path 收集 path constraint，丟 SMT solver。SAT → 該 path 可達；UNSAT → 不可達。

工具：**KLEE** (LLVM-based)、**angr** (binary)、**Mayhem** (DARPA Cyber Grand Challenge winner)。

範例：

```c
int x = symbolic_input();
if (x > 5) {
    if (x < 10) {
        assert(x != 7);
    }
}
```

Path 1: `x > 5 ∧ x < 10 ∧ x = 7` → SAT (反例 `x=7`)
Path 2: `x > 5 ∧ x < 10 ∧ x ≠ 7` → SAT (其他 path)

**KLEE 用 STP / Z3 / Boolector** 做 constraint solving。不同 instance 換不同 solver 是常見策略。

## 3. Deductive Verification

**Dafny / F\* / Verus / Lean 4**：使用者寫 program + spec（precondition、postcondition、loop invariant）、tool 用 SMT 驗證。

範例（Dafny）：

```dafny
method abs(x: int) returns (y: int)
    ensures y >= 0
    ensures y == x || y == -x
{
    if x < 0 { y := -x; }
    else { y := x; }
}
```

Dafny 把 `ensures` 跟 program 一起編成 SMT，丟 Z3 驗。複雜 spec 用 quantified SMT，需要好 trigger。

**Verus**（Rust verification）跟 F\* 也用 Z3 後端。Microsoft Research 推動數十年。

## 4. Type Systems / Refinement Types

**Liquid Haskell**, **Liquid Java**：types 用 logical predicate refine：

```haskell
{-@ inc :: x:Int -> {y:Int | y > x} @-}
inc :: Int -> Int
inc x = x + 1
```

Type check 時，verifier 產 `{y > x ∧ y = x + 1}` 等 condition、丟 SMT。SAT 表類型成立。

## 5. Synthesis (Program Synthesis)

**從 spec 自動生成 program**。Sketch (UC Berkeley) 用 SMT：

```
sketch:
    int magic(int x) {
        return (x ?? 1) + (x ?? 2);
        // ?? 是 hole，由 synthesis 填
    }
spec: magic(5) == 10 && magic(3) == 6
```

SMT solver 找 hole 值滿足所有 example。`?? 1 = 1, ?? 2 = 1` 給 `2x` 函數。

**Rosette** 是 Racket-based、interactive synthesis。**SMT-driven program synthesis** 整個領域 (Solar-Lezama 2008 thesis 開創)。

## 6. Hardware Verification

**Yosys + SBY + Boolector** 組合：Verilog → SMT → check property。專屬 BV theory 的 strength。

例：cache coherence protocol、處理器 pipeline correctness、protocol equivalence checking。

商業 EDA tool（Cadence、Synopsys）用 SAT/SMT 內核做 formal verification。

## 7. Model Checking

**NuXMV**, **IC3 / PDR algorithm**：infinite-state model checking 用 SMT 做 state exploration。

`IC3 (Bradley 2011)` 是 model checking 的 game changer，用 SMT 反復學 inductive invariant。整個 hardware verif 領域都用。

## 8. Cryptanalysis

SAT 用來找 hash collision。**MD5 collision** by Wang et al. 2005、**SHA-1 collision** (Google 2017) — 部分工作用 SAT。

CryptoMiniSat 的特殊功能：XOR clause + Gauss elimination，crypto problem 友好。

## 9. Combinatorial / Constraint Problems

**Sudoku, scheduling, packing, planning, coloring**：把問題 encode 成 SAT/MaxSAT，跑 solver。

Encode 套路：

- Sudoku：每格 9 Boolean (one-hot)、加 constraint
- N-queens：每 row 一個 BV variable、表示 queen 列位置
- Graph coloring：每 vertex k Boolean、加邊不同色 constraint

## 10. AI Planning

**Planner**：goal + actions + state → action sequence。Encode 成 SAT (Kautz–Selman 1992)：

- 每 step 每 action 一 Boolean
- 加 frame axiom、precondition、effect

**SATPlan** 是經典 planner。現代 planning competition mix-use SAT。

## 為什麼 SMT 取代客製 solver

10 年前：每個 verification tool 自己寫 BV solver / linear arith checker。

現在：**全部丟 Z3**。原因：

- Z3 / cvc5 都通用、強大
- SMT-LIB 統一格式
- 這些 solver 投入幾百人年、單獨團隊難超越

**結果**：verification tool 變薄、業界生產力大增。代價：solver 是 single point of failure，bug 會傳到所有 client。

## SMT 做不到的

- **Higher-order logic**：lambda、function-as-arg、polymorphism — Lean / Coq / Isabelle 的範圍
- **Topological / geometric reasoning**：純 SMT 弱
- **Probabilistic reasoning**：Z3 不會推 P(A) 之類
- **Big-step inductive proof**：integer 上的 induction Z3 不直接支援

這些要 **interactive theorem prover**（Lean、Coq）+ SMT 配合。Lean 4 的 `omega` tactic 用內建 SMT-like 算法。

## 動手練習

1. **CBMC 玩一次**：寫個含 buffer overflow 的 C、跑 `cbmc --bounds-check file.c`，看 counter-example。
2. **KLEE 試駕**：跟著 KLEE tutorial 跑 `coreutils`，看 symbolic execution 找的 bug。
3. **Dafny 寫 spec**：寫個簡單 sort 函數 + `ensures sorted`，看 Dafny 如何依靠 Z3 驗證。
4. **Sudoku SAT encoding**：手動寫一個 4x4 sudoku 的 SAT，丟 MiniSat 解。

## 常見誤解

- **「SMT 可以解任何 verification 題」** — 不行。SMT 邊界（無 higher-order、量詞 incomplete）對某些 spec 處理不來。
- **「verifier 寫好就一勞永逸」** — verifier 自己有 bug，client tool 不能 100% trust。需要 proof checking + manual audit。
- **「客戶不會看 SMT」** — Linux kernel maintainers、AWS engineers 直接讀 CBMC output 的 counter-example、debug 自己的 code。

## 自我檢核

- [ ] 列得出 5+ 個用 SMT 的工業 tool
- [ ] 知道 BMC 跟 symbolic execution 怎麼編碼成 SMT
- [ ] 懂 deductive verification (Dafny / F\*) 跟 BMC 的差別
- [ ] 知道 IC3/PDR 對 model checking 的意義
- [ ] 理解 SMT 跟 ITP (Lean / Coq) 的分工

最後一章看 SMT 的 **前沿研究** — MCSat 架構、NRA + CAD、新興 solver 技術。

→ [Ch 35 — 前沿：MCSat、NRA、CAD](./35-frontier.md)
