# 練習 A — Tseitin CNF 轉換器

> 目標：把 Ch 2 的 AST、Ch 4 的 Tseitin 串起來，寫一個 CLI 工具：吃 infix formula、輸出 DIMACS CNF。**做完你能把自己手寫的邏輯題丟給 MiniSat**。

## 任務規格

| 項目 | 規格 |
|---|---|
| 輸入 | stdin，一行 infix formula |
| 輸出 | stdout，DIMACS CNF |
| 原子命題 | `[a-z][a-zA-Z0-9_]*` |
| 運算子 | `~`（NOT）、`&`（AND）、`\|`（OR）、`->`（IMPLIES）、`<->`（IFF） |
| 優先級（高到低） | `~` > `&` > `\|` > `->` (右結合) > `<->` |
| 括號 | `(`、`)` |
| 常數 | 不支援（自己挑戰加 `T`、`F`） |
| 編碼 | Tseitin 對稱版（不是 Plaisted–Greenbaum） |

## 期望輸出範例

**輸入**：

```
(p & q) | ~r
```

**輸出**：

```
c Variable mapping:
c   1 = p
c   2 = q
c   3 = r
c   4 = t1 (Tseitin for: p & q)
c   5 = t2 (Tseitin for: t1 | ~r)
c   6 = t3 (Tseitin for: ~r)
p cnf 6 10
-4 1 0
-4 2 0
-1 -2 4 0
-6 -3 0
6 3 0
-5 4 6 0
-4 5 0
-6 5 0
5 0
```

**最後一行 `5 0`** 是頂層斷言 `t₂` 為真（就是整個 formula 為真）。

丟給 MiniSat：

```bash
./tseitin < input.txt | minisat /dev/stdin /dev/stdout | tail -3
```

應該看到 `SATISFIABLE` 加一組 model。

## 實作步驟建議

### Step 1：Tokenizer

把輸入字串切成 token：

```
Token 類型：IDENT, LPAREN, RPAREN, NOT, AND, OR, IMPLIES, IFF, END
```

多字元 operator（`->`、`<->`）要小心：看到 `-` 再看下一字元是不是 `>`。看到 `<` 要看下兩個是不是 `->`。

### Step 2：Recursive Descent Parser

每個優先級一個 function：

```
parse_iff       ← 最低優先級，<->
  parse_impl    ← -> (右結合)
    parse_or    ← |
      parse_and ← &
        parse_not ← ~
          parse_atom ← 變數或 (formula)
```

右結合的 `->`：parse 完 `a`，若看到 `->`，右邊**再遞迴 parse `impl` 本身**（不是 `or`），這樣 `a -> b -> c` parse 成 `a -> (b -> c)`。

### Step 3：用 Ch 2 的 AST

直接抄 Ch 2 那個 `Formula = variant<Atom, BinOp, UnaryOp, Const>` 結構。

### Step 4：Tseitin Encoder

主體：

- 維護 `next_var` 計數器
- 對每個 atom 分配變數（先來先到）
- 對每個 BinOp / UnaryOp 節點分配新輔助變數
- 用 `cache` 避免同一個 sub-formula 重複 encode（子樹 sharing）
- 對每個 op 按 Ch 4 的 clause 模板輸出

### Step 5：輸出 DIMACS

先掃一遍 AST 算 `next_var` 和 clause 數（或者 encode 完再數），再打 `p cnf N M` header，然後所有 clause。

**別忘了最後加一條頂層 `{top_lit}` clause**。忘記這條、所有 UNSAT 都會假陽性變 SAT。

### Step 6：測試

底下幾個 input 跑：

- `p` — SAT，model `p = T`
- `p & ~p` — UNSAT
- `(p -> q) & p & ~q` — UNSAT
- `(p <-> q) & (q <-> ~p)` — UNSAT
- `(p | q | r) & (~p | q) & (~q | r) & ~r` — UNSAT（Ch 6 的例子）

每題都對照 MiniSat 的輸出，看 SAT/UNSAT 一致。

## 完整參考解答

**寫完再看！** 不先自己掙扎過，看了這份也記不住。

<details>
<summary>點開參考實作（~200 行 C++20）</summary>

```cpp
// tseitin.cpp
// 編譯：g++ -std=c++20 tseitin.cpp -o tseitin
#include <iostream>
#include <string>
#include <vector>
#include <variant>
#include <memory>
#include <unordered_map>
#include <sstream>
#include <cctype>

// ---------- AST ----------
enum class Op { Not, And, Or, Implies, Iff };
struct Formula;
using FPtr = std::shared_ptr<Formula>;
struct Atom   { std::string name; };
struct BinOp  { Op op; FPtr lhs, rhs; };
struct UnaryOp{ Op op; FPtr sub; };
struct Formula : std::variant<Atom, BinOp, UnaryOp> { using variant::variant; };

FPtr mkAtom(std::string n){ return std::make_shared<Formula>(Atom{std::move(n)}); }
FPtr mkUn(Op o, FPtr s){ return std::make_shared<Formula>(UnaryOp{o, s}); }
FPtr mkBin(Op o, FPtr a, FPtr b){ return std::make_shared<Formula>(BinOp{o, a, b}); }

// ---------- Tokenizer ----------
enum class Tk { Ident, LParen, RParen, Not, And, Or, Implies, Iff, End };
struct Token { Tk tk; std::string text; };

struct Lexer {
    std::string src; size_t pos = 0;
    std::vector<Token> tokens;
    explicit Lexer(std::string s): src(std::move(s)) { tokenize(); }
    void tokenize() {
        while (pos < src.size()) {
            char c = src[pos];
            if (std::isspace(c)) { pos++; continue; }
            if (c == '(') { tokens.push_back({Tk::LParen, "("}); pos++; }
            else if (c == ')') { tokens.push_back({Tk::RParen, ")"}); pos++; }
            else if (c == '~') { tokens.push_back({Tk::Not, "~"}); pos++; }
            else if (c == '&') { tokens.push_back({Tk::And, "&"}); pos++; }
            else if (c == '|') { tokens.push_back({Tk::Or, "|"}); pos++; }
            else if (c == '-' && pos+1 < src.size() && src[pos+1] == '>') {
                tokens.push_back({Tk::Implies, "->"}); pos += 2;
            }
            else if (c == '<' && src.substr(pos,3) == "<->") {
                tokens.push_back({Tk::Iff, "<->"}); pos += 3;
            }
            else if (std::isalpha(c) || c == '_') {
                size_t start = pos;
                while (pos < src.size() && (std::isalnum(src[pos]) || src[pos]=='_')) pos++;
                tokens.push_back({Tk::Ident, src.substr(start, pos-start)});
            }
            else { throw std::runtime_error("unexpected char: " + std::string(1, c)); }
        }
        tokens.push_back({Tk::End, ""});
    }
};

// ---------- Parser（遞迴下降） ----------
struct Parser {
    std::vector<Token>& tks; size_t i = 0;
    explicit Parser(std::vector<Token>& t): tks(t) {}
    Token& peek() { return tks[i]; }
    Token& eat(Tk expected) {
        if (peek().tk != expected) throw std::runtime_error("parse error at '" + peek().text + "'");
        return tks[i++];
    }
    FPtr parse() { auto f = parseIff(); eat(Tk::End); return f; }
    FPtr parseIff() {
        auto l = parseImpl();
        while (peek().tk == Tk::Iff) { eat(Tk::Iff); auto r = parseImpl(); l = mkBin(Op::Iff, l, r); }
        return l;
    }
    FPtr parseImpl() {
        auto l = parseOr();
        if (peek().tk == Tk::Implies) { eat(Tk::Implies); auto r = parseImpl(); return mkBin(Op::Implies, l, r); }
        return l;
    }
    FPtr parseOr() {
        auto l = parseAnd();
        while (peek().tk == Tk::Or) { eat(Tk::Or); auto r = parseAnd(); l = mkBin(Op::Or, l, r); }
        return l;
    }
    FPtr parseAnd() {
        auto l = parseNot();
        while (peek().tk == Tk::And) { eat(Tk::And); auto r = parseNot(); l = mkBin(Op::And, l, r); }
        return l;
    }
    FPtr parseNot() {
        if (peek().tk == Tk::Not) { eat(Tk::Not); return mkUn(Op::Not, parseNot()); }
        return parseAtom();
    }
    FPtr parseAtom() {
        if (peek().tk == Tk::LParen) { eat(Tk::LParen); auto f = parseIff(); eat(Tk::RParen); return f; }
        if (peek().tk == Tk::Ident) { return mkAtom(eat(Tk::Ident).text); }
        throw std::runtime_error("expected atom, got '" + peek().text + "'");
    }
};

// ---------- Tseitin Encoder ----------
using Lit = int;
using Clause = std::vector<Lit>;
struct Encoder {
    int next = 0;
    std::unordered_map<std::string, int> atomVar;     // atom name → var
    std::unordered_map<const Formula*, Lit> cache;    // sub-formula → lit
    std::vector<Clause> clauses;
    std::vector<std::string> varDesc;                 // 1-indexed，varDesc[i] = 變數 i 的描述

    int newVar(std::string desc) {
        varDesc.push_back(std::move(desc));
        return ++next;
    }

    std::string showFormula(const Formula& f) {
        return std::visit([&](auto&& n) -> std::string {
            using T = std::decay_t<decltype(n)>;
            if constexpr (std::is_same_v<T, Atom>) return n.name;
            if constexpr (std::is_same_v<T, UnaryOp>) return "~" + showFormula(*n.sub);
            if constexpr (std::is_same_v<T, BinOp>) {
                const char* op = n.op==Op::And? "&" : n.op==Op::Or? "|" : n.op==Op::Implies? "->" : "<->";
                return "(" + showFormula(*n.lhs) + op + showFormula(*n.rhs) + ")";
            }
            return "";
        }, static_cast<const std::variant<Atom, BinOp, UnaryOp>&>(f));
    }

    Lit encode(const Formula& f) {
        if (auto it = cache.find(&f); it != cache.end()) return it->second;
        Lit result = std::visit([&](auto&& n) -> Lit {
            using T = std::decay_t<decltype(n)>;
            if constexpr (std::is_same_v<T, Atom>) {
                if (auto it = atomVar.find(n.name); it != atomVar.end()) return it->second;
                int v = newVar(n.name);
                atomVar[n.name] = v;
                return v;
            }
            if constexpr (std::is_same_v<T, UnaryOp>) {
                Lit sub = encode(*n.sub);
                Lit t = newVar("t" + std::to_string(next+1) + " (Tseitin for: " + showFormula(f) + ")");
                clauses.push_back({-t, -sub});
                clauses.push_back({t, sub});
                return t;
            }
            if constexpr (std::is_same_v<T, BinOp>) {
                Lit a = encode(*n.lhs), b = encode(*n.rhs);
                Lit t = newVar("t" + std::to_string(next+1) + " (Tseitin for: " + showFormula(f) + ")");
                switch (n.op) {
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
                    case Op::Implies:  // t <-> (a -> b) = t <-> (~a | b)
                        clauses.push_back({-t, -a, b});
                        clauses.push_back({a, t});
                        clauses.push_back({-b, t});
                        break;
                    case Op::Iff:
                        clauses.push_back({-t, -a, b});
                        clauses.push_back({-t, a, -b});
                        clauses.push_back({t, -a, -b});
                        clauses.push_back({t, a, b});
                        break;
                    default: break;
                }
                return t;
            }
            return 0;
        }, static_cast<const std::variant<Atom, BinOp, UnaryOp>&>(f));
        cache[&f] = result;
        return result;
    }

    void encodeTop(const Formula& f) {
        Lit top = encode(f);
        clauses.push_back({top});
    }
};

int main() {
    std::string line;
    if (!std::getline(std::cin, line)) return 0;

    Lexer lex(line);
    Parser par(lex.tokens);
    FPtr ast = par.parse();

    Encoder enc;
    enc.encodeTop(*ast);

    // 輸出 DIMACS
    std::cout << "c Variable mapping:\n";
    for (int i = 1; i <= enc.next; i++) std::cout << "c   " << i << " = " << enc.varDesc[i-1] << "\n";
    std::cout << "p cnf " << enc.next << " " << enc.clauses.size() << "\n";
    for (auto& cl : enc.clauses) {
        for (Lit l : cl) std::cout << l << " ";
        std::cout << "0\n";
    }
    return 0;
}
```

編譯跑：

```bash
g++ -std=c++20 tseitin.cpp -o tseitin
echo "(p & q) | ~r" | ./tseitin
```

</details>

## 測試用例

| 輸入 | 期望 | 說明 |
|---|---|---|
| `p` | SAT, `p = T` | 最小 case |
| `p & ~p` | UNSAT | 矛盾 |
| `(p -> q) & p & ~q` | UNSAT | Modus ponens 的反向 |
| `(p <-> q) & (q <-> ~p)` | UNSAT | `p ↔ q ↔ ¬p` 繞回自身 |
| `a -> b -> c` | SAT | 右結合測試，很多 model（如 `a=F`） |
| `(p \| q) & (~p \| q) & (p \| ~q) & (~p \| ~q)` | UNSAT | 4 條 clause 覆蓋 2 變數全部 assignment |

## Bonus 挑戰

寫完基礎版、有餘力試這些：

1. **加 `T`、`F` 常數**：parse 時當特殊 identifier，encoder 用單一 clause `(top_lit)` 或 `(-top_lit)`。
2. **Plaisted–Greenbaum 優化**：先跑一遍 polarity 分析（每個子公式出現在正極性、負極性、或兩者），encode 時只出需要那邊的 clause。clause 數砍 ~30%。
3. **輸出 SMT-LIB v2**：除了 DIMACS，加個 flag 輸出 `(declare-const p Bool) (assert ...) (check-sat)`，Part 2 會用到。
4. **Pretty-print AST**：debug 用。

## 自我檢核

- [ ] 手寫的 formula 能被 tokenize + parse 正確
- [ ] AST 的結構跟 Ch 2 教的一致
- [ ] Tseitin encoder 用了 cache 避免重複 sub-formula
- [ ] 頂層 literal 有 assert 為真（加了 `{top}` clause）
- [ ] 轉出的 DIMACS 能被 MiniSat 正確判 SAT/UNSAT
- [ ] 會 debug：輸出 SAT 時，把 model 裡只屬於原 atom 的那部分抽出來，手算驗證原公式真值

做完這個練習，你已經有兩個能力：**(1) 把人類描述的邏輯約束機械化** + **(2) 丟進業界 SAT solver**。接下來 Part 1 我們換個視角 — **從頭刻 SAT solver 本身**，不再把 MiniSat 當黑盒。

→ [Ch 8 — SAT 問題與 DIMACS 格式](./08-sat-problem-dimacs.md)
