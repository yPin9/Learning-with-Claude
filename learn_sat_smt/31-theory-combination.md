# Ch 31 — Theory combination：Nelson–Oppen

> 目標：解決 SMT 最常見情境 — **多個 theory 的 atom 混在一個 formula**：`(x + y = z) ∧ (f(x) = f(z))`，LRA + EUF 都要參與。**Nelson–Oppen 1979** 給出優雅的 combination 演算法：兩個 theory solver 只交換 **equality 和 disequality**，合起來就是 combined theory solver。這是 SMT 最漂亮的結果之一。

## 混合 theory 的範例

```smt2
(declare-const x Int)
(declare-const y Int)
(declare-const z Int)
(declare-fun f (Int) Int)
(assert (= (+ x y) z))           ; LIA atom
(assert (= (f x) (f z)))         ; EUF atom
(assert (not (= (f y) (f 0))))   ; EUF atom
(check-sat)
```

**LIA** 處理 `+`、`=`。**EUF** 處理 `f`。它們怎麼合作？

## Nelson–Oppen 的主意

兩個 theory solver `T₁`, `T₂`，**一個 combined solver** 檢查 `T₁ ∪ T₂` 的 consistency。

**基本原則**：

1. **Purify**：把 formula 拆成 `φ₁ ∧ φ₂`，其中 `φᵢ` 只含 `Tᵢ` 的 term
2. **Share equalities**：兩個 solver 交換「共享變數的 equality」資訊
3. 若某邊推出 `x = y`（x, y 是共享變數），告訴另一邊
4. 兩邊各自都 consistent → combined consistent

## Step 1: Purification

Formula:

```
(x + y = z) ∧ (f(x) = f(z))
```

有 `+` (LIA) 和 `f` (EUF) 混合。**Purify 成**：

```
(x + y = z)              ← pure LIA
(f(x) = f(z))            ← pure EUF
```

運氣好，這題已經 pure。若更複雜：

```
f(x + y) = 5
```

x + y 是 LIA、f 是 EUF、5 是 LIA 常數。引入新變數 `a = x + y`，重寫：

```
pure LIA: a = x + y
pure EUF: f(a) = 5       ← 這裡 5 當 uninterpreted constant
        加 b: EUF atom f(a) = b, LIA atom b = 5
pure LIA: b = 5
```

**共享變數** = 出現在兩個 pure formula 裡的：{a, b}。這些是 equality 要交換的對象。

## Step 2: Share Equalities

兩個 solver `T_LIA`, `T_EUF` 各自 assert 自己的 literal，然後互相問：**哪些共享變數 equality 你能推出來？**

```
T_LIA given: a = x + y, b = 5, x = 3, y = 7
T_LIA deduces: a = 10, a = b (if b = 10 from other sources)

T_EUF given: f(a) = b
T_EUF deduces: nothing about equality of a, b unless other stuff
```

當 `T_LIA` 推 `a = b`，通知 `T_EUF`。`T_EUF` 更新：`f(a) = b` ∧ `a = b` ⇒ `f(a) = a`... 繼續推。

反之若 `T_EUF` 推出 `f(a) = f(b)` ⇒ `a, b congruent` ⇒ 某種等式。

**重複這個過程直到 saturation**（沒新 equality 傳播）。

## Step 3: Check Consistency

各 solver 在自己的 atom + 共享 equality 上 check。任一 UNSAT → combined UNSAT。都 SAT + saturation → combined SAT。

## 例子：完整跑一次

Formula:

```
(a = x + y) ∧ (b = 5) ∧ (a = b) ∧ (f(a) = f(b)) ∧ (f(x) ≠ f(y + 5))
```

Purify：

- LIA: `a = x + y, b = 5, a = b`
- EUF: `f(a) = f(b), f(x) ≠ f(c)`（引入 `c = y + 5`）
- LIA: `c = y + 5`

Shared: {a, b, c}（出現在兩個 solver）。

Round 1:

- LIA asserts: `a = b, b = 5` → deduces `a = 5`
- EUF asserts: `f(a) = f(b), f(x) ≠ f(c)` → no shared equality deducible yet

Round 2:

- LIA tells EUF: `a = b`
- EUF updates: `f(a) = f(b)` + `a = b` ⇒ trivially satisfied
- EUF consists check: no conflict yet

Round 3:

- LIA with `a = x + y, a = 5` ⇒ `x + y = 5`
- LIA with `c = y + 5, x + y = 5` ⇒ `c = x + 5`... but not direct equality

Hmm, 這例子 NOP 可能無法直接處理（需要 non-convex handling）。這點下一節講。

## Convex vs Non-Convex Theory

**Convex theory**：若從 hypothesis 推出 `x₁ = y₁ ∨ x₂ = y₂ ∨ ...`，則至少一個**單獨** disjunct 也能推出。

- **LRA convex**（proof: 幾何凸性）
- **EUF convex**
- **LIA 不 convex**！例：`0 ≤ x ≤ 1 ⇒ x = 0 ∨ x = 1`，但單獨 `x = 0` 推不出來
- **BV 不 convex**
- **Array 不 convex**

NO 原版只適用 **convex theory**。對非 convex，需要 **case split**：推出 disjunction 時，逐個試：

```
T deduces (x = y) ∨ (x = z):
    case 1: assume x = y, recurse
    case 2: assume x = z, recurse
```

**這讓 NO 在 LIA / BV / Array 環境變 exponential**。

## Stably Infinite

NO 還有個技術條件：**stably infinite**。

**Stably infinite theory**：若 theory 有 SAT model，則**有任意大 (infinite)** 的 model。

- EUF stably infinite（uninterpreted sort domain 可無限）
- LRA stably infinite（Real 無限）
- LIA stably infinite（Int 無限）
- **BV not stably infinite**（寬度 n 只有 2^n 值）
- **Array with fixed domain not stably infinite**

**兩個 stably infinite theory 才能用 NO 直接組合**。BV + LIA 需要 **拓展版 NO** 或 alternative approach。

## 實作架構

```cpp
class CombinedTheory : public TheorySolver {
    LIASolver lia;
    EUFSolver euf;
    std::set<TermId> shared_vars;

public:
    void assert_lit(Atom a, bool v) override {
        if (a.is_lia()) lia.assert_lit(a, v);
        else if (a.is_euf()) euf.assert_lit(a, v);
    }

    Result check() override {
        while (true) {
            Result lr = lia.check();
            Result er = euf.check();
            if (lr == UNSAT || er == UNSAT) return UNSAT;

            // 交換 equality
            auto new_eqs_from_lia = lia.new_shared_equalities(shared_vars);
            auto new_eqs_from_euf = euf.new_shared_equalities(shared_vars);

            bool any = false;
            for (auto [x, y] : new_eqs_from_lia) {
                euf.assert_lit(Atom::eq(x, y), true);
                any = true;
            }
            for (auto [x, y] : new_eqs_from_euf) {
                lia.assert_lit(Atom::eq(x, y), true);
                any = true;
            }
            if (!any) break;   // saturation
        }
        return SAT;
    }
};
```

**Non-convex 要加 case split**，實作更複雜。

## Delayed Theory Combination (DTC)

Bozzano et al. 2005 改進：**不主動交換 equality**，讓 SAT solver 處理。

- 對每對 shared var `(x, y)`，在 formula 加 atom `EQ(x, y)` — SAT solver decide
- SAT 的 decision 自動 propagate 到兩個 theory
- 不需要 theory 主動 enumerate equality

**效果**：theory solver 簡化、SAT solver 多變數。現代 SMT（Z3、cvc5）用 DTC 為主、pure NO 為輔。

## 多 Theory（> 2）

NO 自然擴展到 `k` 個 theory：每對 theory 交換 equality。**實作很複雜**，實務用 DTC 走 SAT 統一協調。

## 動手練習

1. **Purify 練習**：`g(x + 1) > 5` purify 成 pure EUF + pure LIA。
2. **Non-convex 碰壁**：寫一個 LIA-only instance：`0 ≤ x ≤ 1 ∧ f(x) ≠ f(0) ∧ f(x) ≠ f(1)`。NO-pure 可能推不出 UNSAT，需 case split。
3. **NO + BV**：查 Z3 的 `QF_UFBV` tactic，觀察它怎麼繞過 stably infinite 問題。
4. **比較 EUF + LIA 跟純 LIA**：同一個 instance 用 EUF + LIA 編碼 vs 用 Int-array 編碼，看 solve time 差距。

## 常見誤解

- **「NO 可以組合任意 theory」** — 不行。**Stably infinite + convex** 是條件。
- **「DTC 取代 NO」** — 不取代，是實作技巧。NO 提供正確性證明。
- **「EUF + LIA 組合後更強」** — 是，但新 theory 仍 NP-complete（NP × NP = NP）。
- **「所有 solver 都用 NO」** — Z3 用 DTC、有的 solver 用 hybrid。

## 自我檢核

- [ ] 懂 Nelson–Oppen 的核心主意（purify + share equality）
- [ ] 說得出 convex / stably infinite 的定義
- [ ] 知道哪些 theory convex / stably infinite
- [ ] 懂 NO 在 non-convex 要 case split
- [ ] 知道 DTC 是實作替代
- [ ] 能描述 purification 如何拆 formula

下一章處理 SMT 最難的部分 — **量詞**。純 quantifier-free 的 SMT 可決且有效，加 `∀` `∃` 後整體 undecidable，但有 **E-matching** 和 **MBQI** 兩大 heuristic 能處理大量實務 instance。

→ [Ch 32 — Quantifiers：E-matching、MBQI](./32-quantifiers.md)
