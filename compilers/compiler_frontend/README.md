# 編譯器前端學習筆記：從 lex/yacc 到 MiniC

> 給已經會 C 語言、想動手寫一個小型語言前端的工程師。

這是一系列循序漸進的教學文章，以 **flex + bison**（lex/yacc 的 GNU 版本）為工具，從詞法分析、語法分析，一路寫到 AST 與簡單解釋器。

## 為什麼學這個？

- **理解你天天在用的東西**：你寫的每一行程式碼都被某個 lexer/parser 吃過。
- **寫 DSL 的能力**：設定檔、範本語言、查詢語言，本質都是一個小型編譯器前端。
- **讀懂錯誤訊息**：當 gcc 對你大叫「shift/reduce conflict」或「expected ')' before token」，你會知道它在說什麼。

## 課程地圖

### Part 1 — 基礎與環境
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 編譯器前端全景](./01-frontend-overview.md)

### Part 2 — Lex（詞法分析）
- [Ch 2 正則表達式與 DFA 直覺](./02-regex-and-dfa.md)
- [Ch 3 flex 基本語法](./03-flex-basics.md)
- [Ch 4 常見詞法模式](./04-flex-common-patterns.md)
- [Ch 5 flex 與 C 互動](./05-flex-c-interaction.md)
- [練習 A：C 子集 tokenizer](./practice-a-c-tokenizer.md)

### Part 3 — Yacc/Bison（語法分析）
- [Ch 6 文法基礎與 BNF](./06-grammar-basics.md)
- [Ch 7 LR/LALR 直覺](./07-lr-lalr-intuition.md)
- [Ch 8 bison 基本語法](./08-bison-basics.md)
- [Ch 9 解決文法衝突](./09-resolving-conflicts.md)
- [Ch 10 lex + yacc 整合](./10-lex-yacc-integration.md)
- [練習 B：表達式計算器](./practice-b-calculator.md)

### Part 4 — AST 與語義
- [Ch 11 設計 AST](./11-ast-design.md)
- [Ch 12 在動作中建構 AST](./12-building-ast.md)
- [Ch 13 符號表與作用域](./13-symbol-table.md)
- [Ch 14 型別檢查](./14-type-checking.md)
- [練習 C：AST 解釋器](./practice-c-ast-interpreter.md)

### Part 5 — 整合專案
- [Final Project：MiniC 前端](./final-project-minic.md)

## 學習方式建議

1. **每章親手敲過**：別只讀，所有範例打開編輯器跑一次。
2. **故意改壞**：把分號刪掉、把 `+` 改成 `++`，看 flex/bison 怎麼罵你。
3. **看輸出**：`bison -v` 會生成 `.output` 檔，裡面有狀態機、衝突報告，那是你最好的老師。

## 參考資料

- 《flex & bison》— John Levine, O'Reilly（最直接對口）
- 《Compilers: Principles, Techniques, and Tools》（龍書）— 第 3、4 章理論補充
- bison 官方 manual：<https://www.gnu.org/software/bison/manual/>
- flex 官方 manual：<https://westes.github.io/flex/manual/>
