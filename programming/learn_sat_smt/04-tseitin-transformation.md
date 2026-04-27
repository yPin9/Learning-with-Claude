# Ch 4 — Tseitin 轉換

> 目標：學會把任意 formula **線性地** 轉成 CNF。Ch 3 告訴你天真分配會 `Θ(2^n)` 爆炸，Tseitin 的招是 **引入新變數換掉指數爆炸**。所有現代 SAT encoder（CBMC、Kissat 前端、你之後會寫的練習 A）都用這招。

## 主意：每個子公式配一個新變數

Tseitin 1968 的想法一句話：

> 對 formula `φ` 裡每個子公式 `ψ`，發明一個新變數 `t_ψ`，加一條 clause 斷言 `t_ψ ↔ ψ`。

乍聽多此一舉 — 為什麼要引入新變數？關鍵在於：`↔` 能拆成 **有限幾條小 clause**，而且 **大小跟連接詞數量成正比**。整個公式 `φ` 最後的 CNF size 是 `O(|φ|)`，linear。

代價：新的 CNF 有新變數，跟原公式不 **邏輯等價**，只 **equi-satisfiable**（SAT 性質一致但 model 不同）。對 SAT solver 來說這已經夠。

## 一個小例子先看直覺

轉 `(p ∧ q) ∨ r`。

Step 1：為每個內部節點建新變數。AST 兩個內部節點：

```
         ∨              ← 給它新變數 t₂
        / \
       ∧   r            ← 給左邊 ∧ 新變數 t₁
      / \
     p   q
```

Step 2：對每個新變數寫 `t_i ↔ 子公式`：

```
t₁ ↔ (p ∧ q)
t₂ ↔ (t₁ ∨ r)
```

Step 3：加一條「整個公式為真」的斷言 — 就是頂層 `t₂`：

```
t₂
```

Step 4：把每個 `↔` 展開成 CNF。這就要靠 **clause 模板**。

## Clause 模板：每種連接詞怎麼拆

`t ↔ (a ∧ b)` 展開：

```
t → (a ∧ b)     等價   (¬t ∨ a) ∧ (¬t ∨ b)
(a ∧ b) → t     等價   (¬a ∨ ¬b ∨ t)
```

所以：

```
t ↔ (a ∧ b)  →  (¬t ∨ a) ∧ (¬t ∨ b) ∧ (¬a ∨ ¬b ∨ t)
```

**3 條 clause，總 literal 數 = 7**。背起來。其他運算子照辦（下表是 Tseitin 的核心 cheat sheet）：

| 子公式 | 轉出的 clauses |
|---|---|
| `t ↔ ¬a` | `(¬t ∨ ¬a) ∧ (t ∨ a)` |
| `t ↔ (a ∧ b)` | `(¬t ∨ a) ∧ (¬t ∨ b) ∧ (¬a ∨ ¬b ∨ t)` |
| `t ↔ (a ∨ b)` | `(¬t ∨ a ∨ b) ∧ (¬a ∨ t) ∧ (¬b ∨ t)` |
| `t ↔ (a → b)` | `(¬t ∨ ¬a ∨ b) ∧ (a ∨ t) ∧ (¬b ∨ t)` |
| `t ↔ (a ↔ b)` | `(¬t ∨ ¬a ∨ b) ∧ (¬t ∨ a ∨ ¬b) ∧ (t ∨ ¬a ∨ ¬b) ∧ (t ∨ a ∨ b)` |

**驗證法**：每一條模板都是 tautology，你可以用真值表驗證（5 條 IFF 的展開有 4 條 clause，對應 4 個 `(t, a, b)` valuation 中的一個等式成立的情況）。

## 用上例走到底

`(p ∧ q) ∨ r` 完整 CNF：

```
// t₁ ↔ (p ∧ q)
(¬t₁ ∨ p)
(¬t₁ ∨ q)
(¬p ∨ ¬q ∨ t₁)

// t₂ ↔ (t₁ ∨ r)
(¬t₂ ∨ t₁ ∨ r)
(¬t₁ ∨ t₂)
(¬r ∨ t₂)

// 頂層：t₂ 為真
(t₂)
```

**7 條 clause、3 個新變數**。對一個 3-variable、2-operator 的 formula 來說看起來多，但 **formula 大小 linearly 增加**，比天真分配的指數爆炸好太多。

SAT solver 吃這個 CNF，判得出 SAT/UNSAT 都跟原 `(p ∧ q) ∨ r` 一致。SAT 時它會給你 `t₁, t₂, p, q, r` 的 model，你只看 `p, q, r` 就是原公式的 model。

## Size 分析：為什麼是 linear

原公式有 `n` 個連接詞 → AST 有 `n` 個內部節點 → 引入 `n` 個新變數 → 每個新變數展 ≤ 4 條 clause、每條 ≤ 3 literal。

```
|CNF| ≤ 12 n   （clause 總 literal 數）
```

**Linear in formula size**，勝利。

## 為什麼只是 equi-satisfiable，不是 equivalent

新變數 `t₁, t₂, ...` 在原公式裡沒出現。原 `(p ∧ q) ∨ r` 講的是 3 變數世界，新 CNF 講的是 5 變數世界。兩者的真值表維度不同，不可能 equivalent。

但 **SAT 性質一致**：

- 原公式 SAT（存在 `p, q, r` 值讓它真）⇔ 新 CNF SAT（同一組 `p, q, r` 加上對應的 `t₁, t₂` 就行）
- 原公式 UNSAT ⇔ 新 CNF UNSAT

這就是 **equi-satisfiable**。SAT solver 只在乎這個性質。

## Plaisted–Greenbaum：一邊就夠

Tseitin 要每個子公式 `t ↔ ψ`，展 **雙向**（`t → ψ` 和 `ψ → t`）。但 Plaisted 和 Greenbaum 1986 觀察到：**你只需要一邊**，看你的子公式出現在哪個 polarity。

直覺：頂層 `t₂` 斷言它為真。如果 `t₂` 只在 **positive polarity** 出現（沒被 `¬` 包住），我們只需要確保 `t₂ = ⊤ ⇒ ψ = ⊤`，也就是只要 `t₂ → ψ` 那邊的 clause。反向那邊不需要。

結果：clause 數大約減半，SAT 性質一致。**現代 encoder 幾乎全走 Plaisted–Greenbaum**。

但實作 Tseitin 的初版建議用**對稱版本**（全雙向），正確性明顯、好 debug。練習 A 會實作對稱版，你想挑戰再升級 PG。

## 寫成 C++

接 Ch 2 / Ch 3 的 AST。概念性的虛擬碼：

```cpp
#include <vector>
#include <unordered_map>

using Lit = int;                   // +k = xₖ, -k = ¬xₖ
using Clause = std::vector<Lit>;

struct TseitinEncoder {
    int next_var = 0;
    std::unordered_map<const Formula*, Lit> cache;  // sub-formula → lit
    std::vector<Clause> clauses;

    Lit fresh() { return ++next_var; }

    // 回傳這個 formula 對應的 lit（如果是 atom 直接返回變數編號）
    Lit encode(const Formula& f) {
        if (auto it = cache.find(&f); it != cache.end()) return it->second;

        Lit result = std::visit([&](auto&& node) -> Lit {
            using T = std::decay_t<decltype(node)>;
            if constexpr (std::is_same_v<T, Atom>) {
                return get_or_create_var(node.name);
            }
            if constexpr (std::is_same_v<T, UnaryOp>) {   // ¬
                Lit sub = encode(*node.sub);
                Lit t = fresh();
                clauses.push_back({-t, -sub});
                clauses.push_back({t, sub});
                return t;
            }
            if constexpr (std::is_same_v<T, BinOp>) {
                Lit a = encode(*node.lhs), b = encode(*node.rhs);
                Lit t = fresh();
                emit_template(node.op, t, a, b);   // 按表格 emit clauses
                return t;
            }
            // Const 省略
            return 0;
        }, static_cast<const std::variant<Atom, BinOp, UnaryOp, Const>&>(f));

        cache[&f] = result;
        return result;
    }

    void emit_template(Op op, Lit t, Lit a, Lit b) {
        switch (op) {
            case Op::And:
                clauses.push_back({-t, a});
                clauses.push_back({-t, b});
                clauses.push_back({-a, -b, t});
                break;
            case Op::Or:
                clauses.push_back({-t, a, b});
                clauses.push_back({-a, t});
                clauses.push_back({-b, t});
                break;
            // Implies、Iff 同理
        }
    }

    void encode_top(const Formula& f) {
        Lit top = encode(f);
        clauses.push_back({top});   // 頂層斷言為真
    }
};
```

**兩個實作重點**：

1. **Sub-formula sharing**：用 `cache` 讓相同 sub-formula 共用同一個 `t`。`(p ∧ q) ∨ (p ∧ q)` 只配一次 `t₁`。
2. **頂層一定要加 `{top}`**：忘記這條，CNF 會被 SAT solver 解為「全設 false」— 假陰性。

## 常見誤解

- **「Tseitin 會讓 UNSAT 變 SAT」** — 不會。Equi-satisfiable 保證 SAT 性質一致。但新變數的 **model** 沒意義，別把 `t_i` 的值回報給使用者。
- **「Tseitin 永遠比天真分配好」** — 對大公式是，對非常小的公式（< 10 connectives）天真分配甚至更短，因為 Tseitin 有固定的 `t_i` overhead。實務上 threshold 在那附近切換。
- **「Plaisted–Greenbaum 錯誤率高」** — 完全不會。Polarity 分析嚴謹，正確性有證明。只是要多一輪分析 pass。

## 動手練習

1. **紙筆**：把 `¬(p ∧ q) → r` 用 Tseitin 轉 CNF，寫出所有 clause。最後應該是 `~7` 條 clause 左右（包括 `¬` 的展開）。
2. **心算**：如果 `n = 10` 個 cube 用 `∨` 連起來 —`(p₁ ∧ q₁) ∨ ... ∨ (p₁₀ ∧ q₁₀)`—天真分配給你幾條 clause？Tseitin 給你幾條？把兩個數字寫出來感受一下差距（天真：`2^10 = 1024`；Tseitin：`30 + 21 = ~51`）。
3. **故意做錯**：在 encode 頂層時 **忘記** 加 `{top}`。用 MiniSat 跑一個你本來覺得 UNSAT 的 formula，它會說 SAT 給你怪 model。觀察錯在哪（**全 false** 永遠滿足只有 negative literal 的 clause）。

## 自我檢核

- [ ] 說得出 Tseitin 的核心點子（每個子公式配新變數、展雙向 IFF）
- [ ] 背得住 `t ↔ (a ∧ b)` 和 `t ↔ (a ∨ b)` 的 clause 模板
- [ ] 理解 equi-satisfiable ≠ equivalent
- [ ] 知道 Tseitin 讓 CNF size 從指數降到 linear
- [ ] 知道 Plaisted–Greenbaum 可以再砍一半 clause（細節可以 Ch 20 再看）
- [ ] 寫得出 encoder 的骨架

Part 0 的 **機械面** 到這裡結束 — 你已經能把任何 formula 變成 SAT solver 吃的東西。接下來三章是 **邏輯面**：推論系統、resolution 完備性、一階邏輯預覽。這三章會讓你從「會操作 CNF」變成「懂 CNF 為什麼在邏輯的位置」。

→ [Ch 5 — 推論系統：Hilbert、自然演繹、Sequent](./05-proof-systems.md)
