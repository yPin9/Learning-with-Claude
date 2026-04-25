# Ch 3 — 邏輯等價與標準式（NNF / CNF / DNF）

> 目標：理解邏輯等價的代數定律；把 formula 轉成三種標準式 — NNF、CNF、DNF；看清楚為什麼 SAT solver 只吃 CNF、又為什麼天真轉 CNF 會 **爆炸**。Ch 4 的 Tseitin 就是解這個爆炸。

## 等價（`≡`）與 equi-satisfiability

兩個 formula 的「相等」有兩種強度：

- **邏輯等價（equivalent，`≡`）**：在 **每一個** valuation 下值都一樣。
- **等可滿足（equi-satisfiable）**：一個 SAT 當且僅當另一個 SAT。不要求 model 一樣。

等價的條件嚴格，equi-satisfiable 鬆。`p ∧ q` 和 `p ∧ q ∧ (r ∨ ¬r)` **等價**（後面那塊恆真）。`p ∧ q` 和 `p ∧ q ∧ s`（`s` 是新變數）**不等價**但 **equi-satisfiable**（兩者要嘛都 SAT、要嘛都 UNSAT，因為 `s` 可以自由選）。

**這個區別 Ch 4 會變重要** — Tseitin 轉換不保證 equivalent，但保證 equi-satisfiable，這就夠 SAT solver 用了。

## 經典等價定律

底下每條都是 tautology `φ ↔ ψ`，背起來。Ch 6 resolution、Ch 10 DPLL 會每章用到：

| 名字 | 定律 |
|---|---|
| 雙重否定 | `¬¬φ ≡ φ` |
| De Morgan | `¬(φ ∧ ψ) ≡ ¬φ ∨ ¬ψ` |
|  | `¬(φ ∨ ψ) ≡ ¬φ ∧ ¬ψ` |
| 交換律 | `φ ∧ ψ ≡ ψ ∧ φ`；`φ ∨ ψ ≡ ψ ∨ φ` |
| 結合律 | `(φ ∧ ψ) ∧ χ ≡ φ ∧ (ψ ∧ χ)` |
| 分配律 | `φ ∧ (ψ ∨ χ) ≡ (φ ∧ ψ) ∨ (φ ∧ χ)` |
|  | `φ ∨ (ψ ∧ χ) ≡ (φ ∨ ψ) ∧ (φ ∨ χ)` |
| 吸收律 | `φ ∧ (φ ∨ ψ) ≡ φ`；`φ ∨ (φ ∧ ψ) ≡ φ` |
| 冪等 | `φ ∧ φ ≡ φ`；`φ ∨ φ ≡ φ` |
| 蘊含的定義 | `φ → ψ ≡ ¬φ ∨ ψ` |
| 雙條件的定義 | `φ ↔ ψ ≡ (φ → ψ) ∧ (ψ → φ) ≡ (¬φ ∨ ψ) ∧ (φ ∨ ¬ψ)` |

**不會證的**就用真值表跑一次。這不是榮耀，是投資。

## 三種標準式

定義（請嚴格記）：

```
literal   ::= atom | ¬atom              # 只有原子或原子的否定
clause    ::= literal ∨ literal ∨ ...   # literal 的 OR
cube      ::= literal ∧ literal ∧ ...   # literal 的 AND

NNF (Negation Normal Form):
  ¬ 只出現在 atom 前面（用 De Morgan 把 ¬ 推到 leaf）

CNF (Conjunctive Normal Form):
  clause₁ ∧ clause₂ ∧ ... ∧ clauseₙ      # clauses 的 AND

DNF (Disjunctive Normal Form):
  cube₁ ∨ cube₂ ∨ ... ∨ cubeₙ            # cubes 的 OR
```

記憶技巧：
- **NNF** 是 intermediate form，只要求「`¬` 在最裡面」，沒規定結構層次。
- **CNF = AND of ORs**（一堆 clauses 做 AND）
- **DNF = OR of ANDs**（一堆 cubes 做 OR）

SAT solver 吃 **CNF**。DIMACS 格式（Ch 0 看過）就是一條 clause 一行。

## 為什麼 SAT 吃 CNF 不吃 DNF

**DNF 上判 SAT 是 trivial**：檢查 `cube₁ ∨ cube₂ ∨ ...` 有沒有哪一個 `cubeᵢ` 不包含矛盾（也就是同時含 `p` 和 `¬p`），有就 SAT。線性時間。

**CNF 上判 SAT 是 NP-complete**：你得找一組 valuation 讓 **每一條 clause** 都至少有一個 literal 為 ⊤。

那為什麼不用 DNF？**因為把公式轉 DNF 會爆**。一般公式轉 DNF 的 size 可能是原公式的 `Θ(2^n)`。例如：

```
(p₁ ∨ q₁) ∧ (p₂ ∨ q₂) ∧ ... ∧ (pₙ ∨ qₙ)
```

這是一個 CNF，size `O(n)`。轉 DNF 要把 AND 分配進去，得到所有 `pᵢ / qᵢ` 的組合，共 `2^n` 個 cube。

**所以結論是：**

- 表示 formula **容易** 用 CNF（人天生傾向 AND 一堆約束）
- 判 SAT 在 CNF 上 **本質難**（但 CDCL 實務上很快）
- DNF 判 SAT trivial **但沒人有 DNF**（把 formula 轉 DNF 會爆）

天下沒有白吃的午餐被完美演繹。

## 把任意 formula 轉成 NNF

Step by step：

1. 把 `→` 和 `↔` 用定義消掉：`φ → ψ` 變 `¬φ ∨ ψ`，`φ ↔ ψ` 變 `(¬φ ∨ ψ) ∧ (φ ∨ ¬ψ)`
2. 把 `¬` 往裡推：遇到 `¬(φ ∧ ψ)` 變 `¬φ ∨ ¬ψ`，遇到 `¬(φ ∨ ψ)` 變 `¬φ ∧ ¬ψ`，遇到 `¬¬φ` 變 `φ`
3. 重複到 `¬` 只貼在 atom 上

範例：`¬(p → (q ∧ ¬r))`

```
¬(p → (q ∧ ¬r))
≡ ¬(¬p ∨ (q ∧ ¬r))       [消 →]
≡ ¬¬p ∧ ¬(q ∧ ¬r)        [De Morgan]
≡ p ∧ (¬q ∨ ¬¬r)         [雙重否定、De Morgan]
≡ p ∧ (¬q ∨ r)           [雙重否定]
```

這是 NNF。它已經接近 CNF 了（只差沒有明確的 top-level `∧` 結構要求）。

## NNF → CNF（天真法）

遇到 `φ ∨ (ψ ∧ χ)` 就分配成 `(φ ∨ ψ) ∧ (φ ∨ χ)`。

範例：`(p ∧ q) ∨ (r ∧ s)`

```
(p ∧ q) ∨ (r ∧ s)
≡ ((p ∧ q) ∨ r) ∧ ((p ∧ q) ∨ s)          [對 r∧s 分配]
≡ (p ∨ r) ∧ (q ∨ r) ∧ (p ∨ s) ∧ (q ∨ s)  [再對每邊分配]
```

從 4 個 literal 變成 4 條 clause、8 個 literal instance。**小題目**，可接受。

**但一般情況會爆**：前面提過的「`n` 個 `(pᵢ ∨ qᵢ)` AND 起來」轉 DNF 要 `2^n`，對偶地，「`n` 個 `(pᵢ ∧ qᵢ)` OR 起來」轉 CNF 也要 `2^n`。

```cpp
// 天真 to_cnf 的 pseudo-code，會爆
FPtr to_cnf_naive(FPtr f) {
    f = eliminate_iff_and_impl(f);
    f = push_negation_inward(f);          // NNF
    return distribute_or_over_and(f);     // 指數爆
}
```

**不要這樣做**，Ch 4 會教正確的方法（Tseitin）。

## NNF → DNF（對偶，也會爆）

和 CNF 對稱，這裡略。

## 兩個特殊 formula：`⊤` 和 `⊥` 的 CNF

習慣上：

- `⊤` 的 CNF 是 **空的 clause 集合**（沒有 clause，空 conjunction 為真）
- `⊥` 的 CNF 是 **只有一個空 clause** 的集合（空 disjunction 為假）

DIMACS 裡空 clause 寫成單獨一個 `0`，這代表永假。CDCL 的收斂條件就是：**learned clause 空 ⇒ UNSAT**。Ch 13 細講。

## 實務：用 C++ 把 formula 轉 NNF

接 Ch 2 的 AST：

```cpp
// 假設已經用 eliminate_iff_and_impl 把 → 和 ↔ 消掉
FPtr to_nnf(FPtr f, bool negate = false) {
    return std::visit([&](auto&& node) -> FPtr {
        using T = std::decay_t<decltype(node)>;
        if constexpr (std::is_same_v<T, Atom>) {
            return negate ? neg(std::make_shared<Formula>(node))
                          : std::make_shared<Formula>(node);
        }
        if constexpr (std::is_same_v<T, Const>) {
            return std::make_shared<Formula>(Const{negate ? !node.value : node.value});
        }
        if constexpr (std::is_same_v<T, UnaryOp>) {
            // Op::Not — 切換 negate flag 往下傳
            return to_nnf(node.sub, !negate);
        }
        if constexpr (std::is_same_v<T, BinOp>) {
            auto l = to_nnf(node.lhs, negate);
            auto r = to_nnf(node.rhs, negate);
            // De Morgan：negate 時把 ∧ 換 ∨、∨ 換 ∧
            Op op = node.op;
            if (negate) op = (op == Op::And) ? Op::Or : Op::And;
            return std::make_shared<Formula>(BinOp{op, l, r});
        }
    }, static_cast<const std::variant<Atom, BinOp, UnaryOp, Const>&>(*f));
}
```

這個做法很漂亮：**用一個 `negate` flag 順著 AST 往下傳，遇到 `¬` 就反轉 flag，遇到 `∧ / ∨` 就在 flag 為真時交換**。不用真的修改 AST 節點。

## 手算練習

把這題轉 NNF 和 CNF（用天真分配）：

```
(p → q) ∧ (¬p → r)
```

**NNF**：

```
(p → q) ∧ (¬p → r)
≡ (¬p ∨ q) ∧ (¬¬p ∨ r)    [消 →]
≡ (¬p ∨ q) ∧ (p ∨ r)       [雙重否定]
```

已經是 NNF，而且碰巧已經是 **CNF**！兩條 clause。

再換一題，這題會爆：

```
(p₁ ∧ q₁) ∨ (p₂ ∧ q₂) ∨ (p₃ ∧ q₃)
```

轉 CNF 要 `2^3 = 8` 條 clause：`(p₁ ∨ p₂ ∨ p₃)`、`(p₁ ∨ p₂ ∨ q₃)`、`(p₁ ∨ q₂ ∨ p₃)` ... 自己把 8 條寫出來。**這就是爆炸的樣子**，如果有 30 個 cube，你得寫 10 億條 clause。

## 動手練習

1. 把 `(a → b) ↔ (¬b → ¬a)` 轉 NNF。（提示：先展開 `↔`，再處理 `→`。結果應該是 tautology 的 NNF 形式，你會看到兩個互相涵蓋的部分。）
2. 把 `(p ∨ q) ∧ (¬p ∨ r) ∧ (¬q ∨ ¬r)` 的真值表畫出來，找 SAT assignment。
3. **故意做錯**：把 `(p₁ ∧ q₁) ∨ (p₂ ∧ q₂) ∨ (p₃ ∧ q₃)` 天真轉 CNF，然後把 `n = 3` 改成 `n = 10`，估算 clause 數量。估完你就會懂為什麼要 Tseitin。

## 常見誤解

- **「NNF 就是 CNF」** — 錯。NNF 只要求 `¬` 在 leaf。`(p ∧ (q ∨ r)) ∨ s` 是 NNF，不是 CNF。
- **「轉 CNF 時用分配律永遠對」** — 對是對，但會爆。小題可以用（教學 / debug），工業規模要用 Tseitin。
- **「CNF 比 DNF 強」** — 錯。兩者同樣能表達所有 formula。區別只在 **誰判 SAT 快**：在 CNF 上 NP-complete、在 DNF 上 linear — 但轉 DNF 會爆。

## 自我檢核

- [ ] 說得出 equivalent 和 equi-satisfiable 的差別
- [ ] 背得出 De Morgan、分配律、蘊含的定義展開
- [ ] 會把小 formula 手動轉 NNF
- [ ] 會把小 formula 手動轉 CNF（天真分配）
- [ ] 知道天真 CNF 可能 `Θ(2^n)` 爆炸
- [ ] 記住 `⊤` = 空 clause 集合、`⊥` = {空 clause}

下一章我們解這個爆炸問題 — Tseitin 轉換：線性 size、換來 equi-satisfiable（而非 equivalent），這個取捨 **所有現代 SAT encoder 都在用**。

→ [Ch 4 — Tseitin 轉換](./04-tseitin-transformation.md)
