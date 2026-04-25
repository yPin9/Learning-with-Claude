# Ch 7 — 一階邏輯預覽

> 目標：看懂一階邏輯（FOL）的語法、語義、量詞，為什麼整體不可決，以及為什麼 SMT 還是能用它 — 因為 SMT solver 只打某幾個**可決的 fragment**。這章不深，目的是把 Part 2 會用到的字彙先鋪好。

## 命題邏輯的天花板

命題邏輯的能力卡在一件事：**變數只代表 true/false，沒辦法談對象**。想寫：

> 任何大於 10 的整數都大於 5

命題邏輯寫不出來。你需要：

- **對象（object）**：整數 `5, 10, 100, ...`
- **函數（function）**：加法 `+`、乘法 `*`
- **謂詞（predicate）**：`>`、`=`
- **量詞（quantifier）**：「任何」`∀`、「存在」`∃`

加上這四樣，你有了 **一階邏輯（First-Order Logic, FOL）**。

## 語法：term 和 formula

FOL 把符號分兩種：

- **Term**：代表對象。常數 `0, 1, a`、變數 `x, y`、函數結果 `f(x)`、`x + y`。
- **Formula**：斷言真假的東西。`x > 0`、`f(x) = y`、`∀x. x > 0`。

BNF：

```
term    ::= constant | variable | f(term, term, ..., term)

formula ::= P(term, ..., term)          # atomic formula
          | ¬ formula
          | formula ∧ formula | formula ∨ formula
          | formula → formula | formula ↔ formula
          | ∀ variable. formula
          | ∃ variable. formula
```

`P` 是 predicate symbol、`f` 是 function symbol。常見的 `=` 是特殊的 predicate，處理時有一套特殊規則（Ch 25 會花整章講）。

**注意 term 和 formula 是兩個語法類別**。`x + 1` 是 term（不能獨立斷真假）；`x + 1 > 0` 是 formula（可真可假）。命題邏輯沒有這個二分。

## 範例：把自然語描述寫成 FOL

> 所有人都會死。蘇格拉底是人。所以蘇格拉底會死。

形式化：

```
∀x. Human(x) → Mortal(x)
Human(Socrates)
───────────────────────── 期望推出
Mortal(Socrates)
```

`Human`、`Mortal` 是 unary predicate，`Socrates` 是 constant。這是 FOL 教科書前三頁的例子。

更數學化的：

```
任何大於 10 的整數都大於 5
     ≡ ∀x. (x > 10 → x > 5)      （整數域）

存在一個數，它平方等於 2
     ≡ ∃x. (x · x = 2)           （實數域；整數域是 false）
```

**同一個 formula，在不同的「域」上真值不同** — 這是 FOL 的精華，下一節講。

## 語義：Model / Interpretation

命題邏輯的 valuation 只要指派 atom。FOL 麻煩很多，要給 **一整個 model**：

```
M = (D, I, v)
```

- `D`：**domain**，你的對象集合（`Z`、`R`、`{蘇格拉底, ...}`）
- `I`：**interpretation**，把 symbol 映射到具體東西：
  - 每個 constant `c` 映射到 `D` 的一個元素
  - 每個 function `f` 映射到 `D^n → D` 的函數
  - 每個 predicate `P` 映射到 `D^n` 的子集（`P(x,y)` 成立 ⇔ `(x,y) ∈ I(P)`）
- `v`：**variable assignment**（因為量詞內部會介紹 free variable）

**Formula `φ` 在 model `M` 下為真** 記作 `M ⊨ φ`。遞迴定義（精簡版）：

- `M ⊨ P(t₁,...,tₙ)` 當 `(eval(t₁),...,eval(tₙ)) ∈ I(P)`
- `M ⊨ ¬φ` 當 不 `M ⊨ φ`
- `M ⊨ ∀x. φ` 當 **對每個 `d ∈ D`**，令 `v[x ↦ d]` 都有 `M ⊨ φ`
- `M ⊨ ∃x. φ` 當 **存在某個 `d ∈ D`** 讓 `M ⊨ φ`（with `v[x ↦ d]`）

**∀ 要 quantify 過整個 domain**。如果 `D` 是無窮的（像 `Z`），你沒辦法機械檢查所有情況。這是一階邏輯難的根源。

## FOL 是不可決的

**Church–Turing 定理（1936）**：一階邏輯的 validity 問題 **undecidable** — 沒有演算法能對任意 FOL formula 決定它是否永遠成立。

證明概要：把 Turing machine 的 halting 編碼成 FOL 公式的 validity。FOL 能表達 halting，halting 不可決，FOL validity 就不可決。

但 FOL 只是 **半可決（semi-decidable）**：如果 `φ` valid，有演算法能在有限步內證（resolution for FOL 就是）；不 valid 的話可能永遠找不到答案。這是完備 + 不可決的共存。

**所以為什麼 SMT 還能 work？** 因為 SMT 不打整個 FOL，它只打 FOL 的幾個 **可決 fragment**。

## SMT 的 fragment 哲學

「Fragment」= FOL 的一個子集（syntactically restricted），在那個子集上 validity 可決。

SMT-LIB 裡每個 `logic` 就是一個 fragment：

| Logic | 意思 | 可決性 |
|---|---|---|
| QF_UF | 無量詞 + uninterpreted function | 可決（NP-complete） |
| QF_LRA | 無量詞 + 線性實數算術 | 可決（多項式） |
| QF_LIA | 無量詞 + 線性整數算術 | 可決（NP-complete） |
| QF_NRA | 無量詞 + 非線性實數 | 可決（指數，CAD） |
| QF_NIA | 無量詞 + 非線性整數 | **不可決**（希爾伯特第十問題） |
| QF_BV | 無量詞 + bit-vector（固定寬度） | 可決（NP-complete） |
| QF_AUFLIA | UF + array + LIA（無量詞） | 可決 |
| UFLIA | 加回量詞的 UFLIA | 不可決一般，有半可決 heuristic |

`QF_` 開頭代表 **quantifier-free**。移掉量詞是最常見的「拿回可決」手段。

**關鍵印象**：SMT solver 不會一次打整個 FOL，它每次只吃你 `(set-logic ...)` 宣告的 fragment。Ch 22 你會實際在 SMT-LIB 裡用這些 logic。

## Theory 是什麼

先區分兩種 function / predicate symbol：

- **Uninterpreted（未解讀）**：只有名字沒有語意。`f(x) = f(y)` 成立當且僅當 `x = y`（congruence），其他一概不推。
- **Interpreted（已解讀）**：名字背後有固定語意。`+` 永遠是加法、`>` 永遠是大小關係、`0 < 1` 永遠為真。

**Theory** = 一組固定解讀的 symbol 和它們遵守的 axiom。

| Theory | 解讀的 symbol | 常見 axiom |
|---|---|---|
| LRA（線性實數） | `+, -, *常數, <, =` | 實數域的公理 |
| LIA（線性整數） | 同上但 domain 是 `Z` | Presburger axioms |
| UF（未解讀函數） | 無 | Reflexivity、symmetry、transitivity、congruence |
| BV（bit-vector） | bitwise ops、`<<`、`+`、... 的 mod 2^n 版本 | Modular arithmetic |
| Array | `read`、`write` | McCarthy's axioms (`read(write(a,i,v),i)=v` 等) |

**SMT solver 的角色**：

```
SAT solver 管命題結構 + Theory solver 管該理論的語義
            └────────────────┬────────────────┘
                        DPLL(T)
```

Ch 23 你會看到這個架構怎麼組起來。

## 幾個會用到的工具概念

### Herbrand universe / Herbrand's theorem

給一個 FOL formula，**Herbrand universe** 是用它裡面的常數和 function symbol 能構造的所有 ground term（沒有變數的 term）的集合。

例：formula 有 constant `a` 和 unary function `f`。Herbrand universe：

```
{ a, f(a), f(f(a)), f(f(f(a))), ... }
```

**Herbrand's theorem**：一個**不含量詞**（或 Skolemized）的 FOL formula `∃x. φ(x)` satisfiable ⇔ 存在有限個 ground substitution `σ₁, ..., σₙ` 使 `φ(t₁) ∨ ... ∨ φ(tₙ)` 在命題意義下 SAT。

白話：**FOL 的存在量詞可以 reduce 成命題 SAT 的有限 disjunction**。Ch 32 E-matching 直接用這個性質。

### Skolemization

把 `∃x. φ(x)` 變成 `φ(sk())`（引入新 constant `sk`，skolem constant）。`∃x. ∀y. φ(x,y)` 變成 `∀y. φ(c, y)`（新 constant）。`∀y. ∃x. φ(x,y)` 更微妙，要引入 skolem function `f(y)` 取代 `x`。

結果：所有 `∃` 被消掉，只剩 `∀`（或沒量詞）。**Equi-satisfiable** 不等價（你引入了新 symbol），跟 Tseitin 精神一致。

### Prenex Normal Form

所有 `∀、∃` 都拉到最外層：`∀x. ∃y. ...`。標準化用。

## 為什麼 SAT/SMT 研究會這樣發展

一句話總結：

> FOL 太強會不可決，命題邏輯太弱講不出有用的東西。SMT 剛好在中間 — 留住「有語意的對象」（整數、實數），但限制在 quantifier-free 的 fragment，讓 solver 還能跑。

這也解釋了 SMT 論文為什麼一直在兩個方向推進：

1. **加理論**（支持更多 function/predicate 的語義）
2. **加量詞**（用 E-matching、MBQI 等啟發式處理 `∀/∃`，雖然理論上不可決）

Part 2 你會分別碰到這兩條路。

## 動手練習

1. **形式化**：把「每個素數都大於等於 2」寫成 FOL。`Prime(x)` 是 unary predicate。
2. **Herbrand universe**：formula 用到 constant `0` 和 function `succ`，寫出 universe 前 5 個 element。（`0, succ(0), succ(succ(0)), ...`）
3. **Skolemize**：把 `∀x. ∃y. loves(x, y)` skolemize。（引入 `f`，變 `∀x. loves(x, f(x))`。`f` 讀作「函數：給你一個人，傳回他愛的人」。）
4. **判 logic**：底下 formula 在哪個 SMT-LIB logic 下？
   - `(x > 0) ∧ (x + y = 10)` → QF_LIA 或 QF_LRA（看 domain 宣告）
   - `f(x) = x ∧ f(f(x)) = a` → QF_UF
   - `∀i. a[i] = 0 → 某東西` → 含量詞 + array，AUFLIA

## 常見誤解

- **「FOL validity 可以 reduce 成命題 SAT」** — 半對。透過 Herbrand 只能在 formula 剛好對應有限個 substitution 時成立，general case 是半可決，不能保證有限。
- **「Uninterpreted function 沒用」** — 大錯。EUF 是 SMT 的核心理論（Ch 25），能形式化「某 function 可被 abstract」的所有邏輯，超有用。
- **「加了量詞就回不去」** — 不一定。有些 fragment 有量詞仍然可決（Presburger arithmetic 整個、BSR、array property fragment）。SMT solver 會為這些 fragment 開特殊 tactic。

## 自我檢核

- [ ] 分得清 term（對象）和 formula（真假斷言）
- [ ] 寫得出 FOL 的 BNF
- [ ] 知道 `M = (D, I, v)` 是什麼
- [ ] 說得出 FOL 整體 undecidable（Church–Turing），但半可決
- [ ] 能列出 5 個以上的 SMT theory 名字
- [ ] 理解 QF_ 前綴的意義（quantifier-free）
- [ ] 知道 Skolemization、Herbrand universe 大意

Part 0 結束。下一個檔案是 **練習 A** — 你要把 Ch 2 的 AST、Ch 4 的 Tseitin 全部串起來，做出一個 **能吃 infix formula、輸出 DIMACS** 的轉換器。做完你就有了整門課第一個自己的工具。

→ [練習 A — Tseitin CNF 轉換器](./practice-a-tseitin.md)
