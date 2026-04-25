# Ch 28 — LIA：branch & bound、Gomory cuts

> 目標：理解 **LIA (Linear Integer Arithmetic)** 如何解。LIA 比 LRA 難 — NP-complete，因為整數約束把 continuous 問題變 combinatorial。主流方法：**LRA relaxation + branch & bound + Gomory cuts**。Presburger arithmetic 整個 fragment 可決，但 SMT 在 quantifier-free 範圍打。

## LIA 問題

**LIA** 語言跟 LRA 一樣（`+, -, 常數乘, <, ≤, =`），但變數是 **Integer**。

```smt2
(set-logic QF_LIA)
(declare-const x Int)
(declare-const y Int)
(assert (= (+ x y) 10))
(assert (> x 2))
(assert (> y 3))
(check-sat)
```

直覺上「就是 LRA 限制到 Int」，但這個限制讓複雜度從 polynomial 變 **NP-complete**。

## 為什麼 LIA 比 LRA 難

LRA 的解空間是 continuous polytope，Simplex 走 vertex。LIA 要解空間 **在 integer lattice 上**，polytope 可能完全不含 integer 點。

**範例**：

```
2x + 2y = 3  （LRA SAT：x = 1/2, y = 1；LIA UNSAT）
```

LRA 給 fraction 解；LIA 沒 integer 解（左邊偶、右邊奇）。

**Hilbert's 10th problem**（**非線性** integer）：undecidable。**線性** integer (Presburger) 可決，但 3-exponential worst case。LIA 在 SMT 是 NP-complete。

## Branch & Bound：主流策略

基本想法：

```
1. 先用 LRA 解（當 relaxation）
2. 若 LRA UNSAT → LIA 也 UNSAT
3. 若 LRA 的 model 整數 → LIA SAT，done
4. 否則找一個非整數變數 x with LRA value v：
    branch left:  assert x ≤ floor(v),   recurse
    branch right: assert x ≥ ceil(v),    recurse
```

每一次 branch 都是 **兩個子問題、分別解**。

### 範例

LRA sol `x = 1.5, y = 2.3`. 選 `x` branch：

- 左：加 `x ≤ 1`，solve LRA
- 右：加 `x ≥ 2`，solve LRA

若 LRA 解 non-integer 再 branch。遞迴直到：

- 兩邊都 UNSAT → 整個 UNSAT
- 某邊 LRA 給 integer sol → LIA SAT

### 樹的大小

Worst case `O(2^n)` branch 但實務靠 heuristic 砍很多。SMT solver 跟 SAT 的 CDCL 共用 learning / backjump 機制，對 LIA 非常有效。

## Gomory Cuts：加強版

Branch & bound 分支深度大，`Gomory cut` 可以**不 branch、直接縮小可行域**。

給 LRA 最優解、某變數 `x` 非整數 `= v`。我們可以導出一條 **cutting plane** — 當前 tableau 的 linear combination，`x ≥ ceil(v)` 或 `x ≤ floor(v)` 的等價但 stronger form。具體推導（Gomory 1958）：

1. 從 LRA tableau 拿 `x` 所在 row： `x = sum aᵢ xᵢ + k`（`k` 非整）
2. 寫成 `x - floor(k) = sum aᵢ xᵢ + (k - floor(k))`
3. 整數約束要 `sum (fractional parts) aᵢ xᵢ ≥ (fractional k)`
4. 加這條 constraint 到 tableau — **當前 non-integer sol 不再可行，但所有 integer sol 還在**

**Gomory cut 總是切掉當前 non-integer 解、留所有 integer 解**。重複切，直到 tableau 給 integer sol。

### 何時用 cut、何時 branch

- **Cut** 適合「接近 integer」的情況，小幅調整
- **Branch** 適合「明確選方向」的情況
- 實務：兩者混用，Z3 和 cvc5 的 LIA 有 heuristic 調度

## Omega Test (Pugh 1991)

另一個 LIA 演算法，Bill Pugh 為 compiler dependence analysis 設計。

**Omega test** 基於 **variable elimination**：

```
消一個變數 x 的所有出現：
    把所有含 x 的 inequality 兩兩配對（一正一負係數）
    消 x、得到新的 inequality
    檢查 coefficient 是否有 integer solution
```

**細節**：LIA 的 Presburger fragment，Pugh 發現可用 **dark shadow** + **real shadow** 加速分析，某些情況比 branch & bound 快。

Omega 在 polyhedral compilation（循環依賴分析）還是主流，SMT 較少用。

## Presburger Arithmetic 完整可決

**Presburger arithmetic**：LIA + **量詞 ∀, ∃**。Mojżesz Presburger 1929 證明可決（但 3-exponential time worst case）。

現代算法：**Cooper's procedure** 用 quantifier elimination。Z3 有 tactic 處理、但通常先 skolemize 後再走 QF_LIA。

純 QF_LIA solver 大多走 branch & bound。

## Mixed Integer / Real (LIRA)

現實問題常混合 integer 和 real：

```smt2
(declare-const i Int)
(declare-const x Real)
(assert (= x (+ 0.5 i)))
(assert (< x 3))
```

**LIRA**（Linear Integer + Real Arithmetic）solver 用 extended tableau — integer 變數有 integrality constraint，real 沒有。SMT solver 按變數類型分開處理。

## Integration to DPLL(T)

```cpp
class LIASolver : public LRASolver {
public:
    Result check() override {
        Result lra_r = LRASolver::check();
        if (lra_r == UNSAT) return UNSAT;

        while (true) {
            auto m = LRASolver::get_model();
            int x = find_non_int_var(m);
            if (x == -1) return SAT;   // 全整數，done

            // Branch & bound
            LRASolver::push();
            LRASolver::assert_bound(x, "upper", floor(m[x]));
            Result left = LRASolver::check();
            LRASolver::pop();
            if (left == SAT && all_int(LRASolver::get_model())) return SAT;

            LRASolver::push();
            LRASolver::assert_bound(x, "lower", ceil(m[x]));
            Result right = LRASolver::check();
            LRASolver::pop();
            if (right == SAT && all_int(LRASolver::get_model())) return SAT;

            if (left == UNSAT && right == UNSAT) return UNSAT;
            // 選一邊 commit，繼續...
            // 實際實作會跟 DPLL(T) 的 branch 整合
        }
    }
};
```

**實際上 LIA 的 branch 交給 SAT solver**：把 `x ≤ floor(v) ∨ x ≥ ceil(v)` 當 clause 加到 SAT，讓 SAT 做 branching。這樣 LIA 不用自己管 backtracking 堆疊。

### Lazy approach

Z3 的 LIA：

- 每次 LRA check SAT 但 model 非整
- LIA tactic 產 branch lemma，push 到 SAT
- 重新 solve

## UNSAT Explanation

LIA UNSAT 的 explanation 靠 **Farkas-like certificate**：線性組合 hypotheses 得出 `0 < 0` 形式的矛盾（加上 integer constraint 的 cuts）。

```
input:    2x ≥ 3,  2x ≤ 1
Farkas:   linear combo 導出 1 ≤ x ≤ 1.5，但 integer 不可能
LIA UNSAT explain: Gomory cut chain 的壓縮
```

純 Gomory UNSAT cert 不算 minimal，SMT solver 用 heuristic 壓縮後送給 DPLL(T)。

## 難解 LIA 類型

- **Pseudo-Boolean** (`0-1 ILP`)：變數限 `{0, 1}`，SAT solver 能直接處理（每 var 一 bit），效率好
- **Knapsack** / **scheduling**：樹狀 branching，若約束鬆 easy、緊則硬
- **Systems with modular coefficients**：某些 instance 要看 `mod` 才能解，純 LIA tool 硬

**CSP / MIP 領域專門 solver**（Gurobi、CPLEX）比 SMT 的 LIA 快很多，但不整合其他 theory。SMT 強項：可組合 LIA 跟 UF、Array、BV。

## 動手練習

1. **手算 branch & bound**：`x + y = 7, x ≥ 0, y ≥ 0, x ≤ 3.5, y ≤ 3.8`。LRA 給什麼？Branch 哪個變數？tree 畫出來。
2. **Gomory cut 練習**：LRA model `x = 3.7, y = 2.4`。構造一條 Gomory cut 切掉這 point。
3. **比較 Z3 LIA 跟 LRA**：同一個 instance 兩種 logic 各跑、看 time 差。整數約束會讓 solve time 增加 2–10×。
4. **識別 NP-hardness**：把 3-SAT 編碼成 LIA instance。展示 NP reducibility。

## 常見誤解

- **「LIA 跟 LRA 差不多」** — 完全不。NP-complete vs P，數量級差距。
- **「Gomory cut 必閉合」** — 不。某些 instance 需要無窮多 cut，實務 branch。
- **「Presburger 總可以做 QF_LIA」** — 不。QF_LIA 是 Presburger 的 subset，可以 solve 但不直接用 Cooper procedure（太慢）。
- **「Float 能替代 rational」** — 同 LRA 不能。Integer constraint 對誤差更敏感。

## 自我檢核

- [ ] 知道 LIA = NP-complete、LRA = polynomial
- [ ] 說得出 branch & bound 流程
- [ ] 理解 Gomory cut 的基本想法（切 fractional sol）
- [ ] 知道 Omega test 的存在跟使用場景
- [ ] 懂 Presburger arithmetic 的可決性
- [ ] 能估 LIA vs LRA 的 solve time 差距

下一個理論是 **bit-vector** — 固定 width 整數，很多地方跟 LIA 相似但底層完全不同。BV 在 hardware verification 和 binary exploit 分析是主力 theory。

→ [Ch 29 — Bit-vector 理論](./29-bitvector.md)
