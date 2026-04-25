# Ch 2 — 命題邏輯的語法與語義

> 目標：把命題邏輯的 syntax（formula 的結構）跟 semantics（formula 的意義）分清楚。這章看起來基礎，但**SAT solver 的每一行 code 都在操作這章的 object**，術語馬虎一次、後面會連鎖混亂。

## 語法 vs 語義的分野

這兩個字你大概有模糊印象，我們把它釘死：

- **語法（syntax）**：formula 長什麼樣子 — 是 symbol 的排列。`p ∧ q` 是一個由 `p`、`∧`、`q` 三個 symbol 組成的字串。電腦眼中的 formula 就只是個 tree。
- **語義（semantics）**：formula 是什麼意思 — 在某個「世界」裡，這個 formula 是真是假。

你寫 parser 在處理 syntax，你寫 evaluator 在處理 semantics。SAT solver 的任務是：**給一個 syntactic object（formula），問它的 semantics 上有沒有一種「世界」讓它為真**。

語法跟語義要分乾淨 — **形式邏輯的一切嚴謹都從這裡開始**。

## 語法：formula 的 BNF

命題邏輯的 formula 長這樣（Backus–Naur Form）：

```
formula ::= atom                 # 原子命題 (proposition)
          | ¬ formula            # 否定
          | formula ∧ formula    # 合取 (AND)
          | formula ∨ formula    # 析取 (OR)
          | formula → formula    # 蘊含 (IMPLIES)
          | formula ↔ formula    # 雙條件 (IFF)
          | ⊤                    # 永真 (top, true)
          | ⊥                    # 永假 (bottom, false)
          | ( formula )

atom    ::= p | q | r | ... | p1 | p2 | ...
```

講人話：一個 formula 要嘛是 atom（原子命題），要嘛由更小的 formula 組成。這是一個遞迴定義。

**術語對照**：

| 名字 | 符號 | 其他念法 |
|---|---|---|
| 否定 | `¬p`, `~p`, `!p` | NOT |
| 合取 | `p ∧ q`, `p & q`, `p · q` | AND |
| 析取 | `p ∨ q`, `p \| q`, `p + q` | OR |
| 蘊含 | `p → q`, `p ⊃ q`, `p ⇒ q` | IMPLIES, if-then |
| 雙條件 | `p ↔ q`, `p ≡ q`, `p ⇔ q` | IFF, XNOR |
| 異或 | `p ⊕ q` | XOR |
| 永真 / 永假 | `⊤` / `⊥`, `1` / `0`, `T` / `F` | top / bottom |

不同書、不同論文會用不同 notation。你要**全部認得**，這樣讀論文才不會絆腳。本系列主用 `¬ ∧ ∨ → ↔`。

## 優先級與括號

標準優先級（從高到低）：

```
1. ¬           (最緊)
2. ∧
3. ∨
4. →   (右結合！)
5. ↔
```

所以 `¬p ∧ q → r` 不需要括號就能解讀為 `((¬p) ∧ q) → r`。`p → q → r` 因為 `→` 右結合，解讀為 `p → (q → r)`。

**但在教材和正式 paper 裡**，人們會加一堆括號預防歧義。你自己寫 SAT benchmark 時也建議多加括號，debug 時感謝自己。

## Formula 作為 AST

電腦裡 formula 就是一棵樹。`(p ∧ q) → (¬r)` 的 AST：

```
          →
         / \
        ∧   ¬
       / \   \
      p   q   r
```

用 C++20 把這棵樹寫出來：

```cpp
#include <memory>
#include <string>
#include <variant>

enum class Op { And, Or, Not, Implies, Iff };

struct Formula;
using FPtr = std::shared_ptr<Formula>;

struct Atom   { std::string name; };
struct BinOp  { Op op; FPtr lhs, rhs; };
struct UnaryOp{ Op op; FPtr sub; };
struct Const  { bool value; };  // ⊤ / ⊥

struct Formula : std::variant<Atom, BinOp, UnaryOp, Const> {
    using variant::variant;
};

// 方便構造
FPtr var(std::string n) { return std::make_shared<Formula>(Atom{std::move(n)}); }
FPtr neg(FPtr f)        { return std::make_shared<Formula>(UnaryOp{Op::Not, f}); }
FPtr fand(FPtr a, FPtr b){ return std::make_shared<Formula>(BinOp{Op::And, a, b}); }
FPtr forr(FPtr a, FPtr b){ return std::make_shared<Formula>(BinOp{Op::Or, a, b}); }
FPtr imp(FPtr a, FPtr b) { return std::make_shared<Formula>(BinOp{Op::Implies, a, b}); }
FPtr iff(FPtr a, FPtr b) { return std::make_shared<Formula>(BinOp{Op::Iff, a, b}); }
```

建剛剛那棵樹：

```cpp
auto p = var("p"), q = var("q"), r = var("r");
auto f = imp(fand(p, q), neg(r));  // (p ∧ q) → ¬r
```

這種 AST 結構會貫穿你整個 solver 的 frontend。Part 0 都會用它。

## 語義：valuation 與 evaluation

Formula 本身沒有真假。要問它真假，要先指定每個 atom 是 true 還是 false — 這個指派叫 **valuation**（或 interpretation、assignment、model，根據作者品味）。

```
valuation v : Atoms → {⊤, ⊥}
```

給一個 valuation，formula 的真值由結構遞迴決定：

| Formula | 值 |
|---|---|
| atom `p` | `v(p)` |
| `¬φ` | NOT `[[φ]]_v` |
| `φ ∧ ψ` | `[[φ]]_v` AND `[[ψ]]_v` |
| `φ ∨ ψ` | `[[φ]]_v` OR `[[ψ]]_v` |
| `φ → ψ` | IF `[[φ]]_v` THEN `[[ψ]]_v`（等價 `¬φ ∨ ψ`） |
| `φ ↔ ψ` | `[[φ]]_v` = `[[ψ]]_v` |

`[[φ]]_v` 讀作「φ 在 valuation `v` 下的值」。這個雙中括號 `[[·]]` 是 semantic bracket，整個邏輯書都這樣寫。

**寫成 C++ evaluator**：

```cpp
#include <unordered_map>
using Valuation = std::unordered_map<std::string, bool>;

bool eval(const Formula& f, const Valuation& v) {
    return std::visit([&](auto&& node) -> bool {
        using T = std::decay_t<decltype(node)>;
        if constexpr (std::is_same_v<T, Atom>)   return v.at(node.name);
        if constexpr (std::is_same_v<T, Const>)  return node.value;
        if constexpr (std::is_same_v<T, UnaryOp>) return !eval(*node.sub, v);
        if constexpr (std::is_same_v<T, BinOp>) {
            bool l = eval(*node.lhs, v), r = eval(*node.rhs, v);
            switch (node.op) {
                case Op::And:     return l && r;
                case Op::Or:      return l || r;
                case Op::Implies: return !l || r;
                case Op::Iff:     return l == r;
                default:          return false;
            }
        }
    }, static_cast<const std::variant<Atom, BinOp, UnaryOp, Const>&>(f));
}
```

示範：

```cpp
Valuation v = {{"p", true}, {"q", true}, {"r", false}};
bool result = eval(*f, v);  // (p ∧ q) → ¬r = (⊤ ∧ ⊤) → ⊤ = ⊤
```

## 關鍵四組術語

有了 valuation，我們可以分類 formula 的「命運」：

| 術語 | 定義 | 直覺 |
|---|---|---|
| **satisfiable** | 存在一個 valuation 讓 formula 為 ⊤ | 有得救 |
| **valid (tautology)** | 所有 valuation 都讓它為 ⊤ | 永遠對 |
| **contingent** | 某些 valuation 為 ⊤、某些為 ⊥ | 不定 |
| **unsatisfiable (contradiction)** | 所有 valuation 都讓它為 ⊥ | 無解 |

**關鍵對偶關係**（記一輩子）：

```
φ valid  ⟺  ¬φ unsatisfiable
φ SAT    ⟺  ¬φ not valid
```

所以「證明 φ 是定理（valid）」就是「證明 ¬φ unsatisfiable」—— 這就是 **refutation**，Ch 6 的主角。所有 SAT solver 都是 UNSAT 檢查器（只是順便告訴你 model）。

## 真值表

對 `n` 個變數的 formula，枚舉所有 `2^n` 種 valuation，就是真值表。以 `p → q`：

| p | q | p → q |
|---|---|---|
| ⊥ | ⊥ | ⊤ |
| ⊥ | ⊤ | ⊤ |
| ⊤ | ⊥ | ⊥ |
| ⊤ | ⊤ | ⊤ |

**讀出：**`p → q` contingent，不是 tautology 也不是 contradiction。

**最 naive 的 SAT 演算法就是跑真值表** — `2^n` 行、找一行為 ⊤。對 `n = 30` 已經 10 億行，對 `n = 1000` 宇宙熱寂都算不完。所以我們需要 CDCL。

## 初學者最常搞錯的事

**蘊含（`→`）不是因果**。`p → q` 只是「不可能 p 真而 q 假」。所以：

- `⊥ → 任何東西 = ⊤`（**空洞真 / vacuously true**）
- `任何東西 → ⊤ = ⊤`

讀出「如果月亮是方的，那我是超人」在邏輯上是 **真** 的句子 — 因為前提永遠為假。這不是語病，是 `→` 的精確語義。你會在 Ch 6 看到這個性質被 resolution 拿來用。

另一個誤解：**「SAT」講的是 formula 的，不是 clause 的**。我們常說「這條 clause 是 satisfied」指的是「在當前 partial assignment 下它為 ⊤」，這是習慣用法，但源頭定義只作用在 formula 上。

## 動手練習

1. **手畫 AST**：把 `(p ∨ ¬q) → (r ∧ (q ↔ p))` 的 AST 畫出來，注意括號跟優先級。
2. **真值表三題**：
   - `(p → q) → ((q → r) → (p → r))` — 是 tautology 嗎？
   - `p ∧ ¬p` — 是什麼？
   - `(p ↔ q) ↔ ((p ∧ q) ∨ (¬p ∧ ¬q))` — 是什麼？
3. **用上面的 C++ 碼實作 `eval`**，跑前一題驗證你手算的結果。

（答案：tautology、unsatisfiable、tautology。第三題的結論 = `p ↔ q` 的定義展開，很重要，Ch 3 會再見。）

## 自我檢核

- [ ] 分得清楚 syntax / semantics — 一個是結構、一個是意義
- [ ] 會用 BNF 寫 formula 的語法
- [ ] 會用 C++ 把 formula 建成 AST 並 evaluate
- [ ] 會畫真值表
- [ ] 會用「satisfiable / valid / unsatisfiable」精確描述 formula
- [ ] 記得 `φ valid ⟺ ¬φ unsat` 的對偶關係
- [ ] 知道 `⊥ → anything = ⊤`

語法跟語義都有了，下一章我們處理一個工程問題：formula 有千百種長法，SAT solver 只吃一種形狀 — CNF。

→ [Ch 3 — 邏輯等價與標準式（NNF / CNF / DNF）](./03-normal-forms.md)
