# Ch 9 — 解決文法衝突

> 目標：學會看懂 bison 的衝突警告，並用五種常見技巧化解。

## 衝突的兩種類型（複習）

- **shift/reduce**：同一個狀態下可以 shift 也可以 reduce
- **reduce/reduce**：可以用兩條不同規則 reduce

bison 遇到衝突時**仍會產生 parser**，預設：
- shift/reduce → 選 shift
- reduce/reduce → 選**先寫**的規則

但預設未必是你要的。**所有衝突都應該明確處理，不該放任**。

## 技巧 1：優先級與結合性

最常用。二義文法配 `%left/%right/%nonassoc` 就能解決絕大部分運算子衝突。

```bison
%left '+' '-'
%left '*' '/'
%right '^'

%%
expr : expr '+' expr
     | expr '-' expr
     | expr '*' expr
     | expr '/' expr
     | expr '^' expr
     | NUMBER
     ;
```

### bison 如何用這些資訊？

遇到 shift/reduce 衝突時，比較：

1. 堆疊頂端規則的運算子優先級（以規則中最後一個有優先級的 token 為準）
2. 下一個要 shift 的 token 的優先級

規則：
- 下一個優先級**高** → shift
- 下一個優先級**低** → reduce
- 相等 → 看結合性：`%left` 就 reduce，`%right` 就 shift，`%nonassoc` 報錯

範例：堆疊 `1 + 2`，下一個是 `*`。
- `+` 優先級低、`*` 高 → shift，繼續組 `2 * ?`
- 結果：`1 + (2 * ?)`

堆疊 `1 + 2`，下一個是 `+`。
- 同優先級、`%left` → reduce
- 結果：`(1 + 2) + ?`

## 技巧 2：%prec

有時同一個 token 在不同位置有不同優先級。經典例子是**一元減號**：

```bison
expr : expr '-' expr       /* 二元減 */
     | '-' expr            /* 一元減 */
     | NUMBER
     ;
```

`-3 * 2` 該怎麼解析？如果沒處理，它會走二元減規則嘗試 reduce `expr '-' expr`，發現前面沒 expr 就報錯。

解法：

```bison
%left  '+' '-'
%left  '*' '/'
%right UMINUS           /* 虛擬 token，只用來指定優先級 */

%%
expr : expr '+' expr
     | expr '-' expr
     | expr '*' expr
     | expr '/' expr
     | '-' expr   %prec UMINUS    /* 明確指定此規則用 UMINUS 的優先級 */
     | NUMBER
     ;
```

`%prec UMINUS` 告訴 bison：「這條規則的優先級以 UMINUS 為準」。

`UMINUS` 不會真的被 lexer 回傳，它只是一個優先級標籤。放最下面，所以優先級最高。

## 技巧 3：改寫文法消除二義性

前面提過，用階層結構表達優先級：

```bison
expr  : expr '+' term  | term ;
term  : term '*' factor | factor ;
factor : NUMBER | '(' expr ')' ;
```

這種文法沒有衝突、不需要 `%left` 宣告。缺點是：
- 規則多，醜
- 每一層都要寫 action，繁瑣

實務上小型語言用 `%left`，大型語言有時會混用。

## 技巧 4：dangling else

bison 對下面這個文法會警告：

```bison
stmt : IF '(' expr ')' stmt
     | IF '(' expr ')' stmt ELSE stmt
     | ...
     ;
```

衝突原因：堆疊 `IF ( expr ) IF ( expr ) stmt`，下一個是 `ELSE`。
- shift → ELSE 綁內層
- reduce → ELSE 綁外層

bison 預設 shift，剛好是 C/Java 的行為（綁最近的 if），所以**這個警告是可接受的**。

但你應該明確表達「我知道有這個衝突且選 shift」。最清晰的做法：

```bison
%nonassoc IFX
%nonassoc ELSE

%%
stmt : IF '(' expr ')' stmt               %prec IFX
     | IF '(' expr ')' stmt ELSE stmt
     ;
```

原理：`IFX` 優先級低於 `ELSE`，所以面對 `ELSE` 時 shift 優於 reduce。

另一個做法是重寫文法區分「匹配 else 的 stmt」和「未匹配的 stmt」：

```bison
stmt  : matched | unmatched ;

matched   : IF '(' expr ')' matched ELSE matched
          | other_stmt
          ;

unmatched : IF '(' expr ')' stmt
          | IF '(' expr ')' matched ELSE unmatched
          ;
```

這個版本完全沒有衝突，但規則爆炸。多數人選前者。

## 技巧 5：reduce/reduce 衝突

這種衝突幾乎都是文法設計問題，得改文法。常見原因：

### 原因 A：重複定義

```bison
name : IDENT ;
type : IDENT ;
decl : name
     | type
     ;
```

看到 `IDENT`，reduce 成 `name` 還是 `type`？parser 不知道。
解法：合併或重新設計。

### 原因 B：上下文相關

C 的 `T * x;` 到底是宣告 `x` 為指向 `T` 的指標、還是表達式 `T * x`？這無法純靠文法判斷，需要**符號表**知道 `T` 是不是 typedef 的型別。

經典解法：lexer 查符號表，遇到已知 typedef 名稱時回傳 `TYPENAME`，否則回傳 `IDENT`。這叫 **lexer hack**，是 C 編譯器的傳統藝能。

## 看懂 bison 衝突訊息的步驟

### Step 1：跑 bison -v

```bash
bison -v grammar.y
```

### Step 2：看終端警告

```
grammar.y: warning: 1 shift/reduce conflict [-Wconflicts-sr]
```

### Step 3：打開 .output 檔

搜尋 `conflict`：

```
State 15 conflicts: 1 shift/reduce

State 15

    4 expr: expr '+' expr .
    5     | expr . '+' expr

    '+'  shift, and go to state 9
    '+'  [reduce using rule 4 (expr)]
```

這告訴你：
- 狀態 15 有衝突
- 堆疊最後是 `expr '+' expr`
- 下一個是 `'+'`
- 兩個選項：shift 或 reduce by rule 4
- bison 選了 shift，忽略了 reduce

### Step 4：決定修法

在此例中加 `%left '+'` 就能告訴 bison「`+` 左結合」，自然就會選 reduce，衝突消失。

## 錯誤恢復：error token

bison 支援一個特殊 token `error`，用來做錯誤恢復。當語法錯誤發生時，bison 會試著跳過 token 直到堆疊頂端符合含有 `error` 的規則。

```bison
stmt : expr ';'
     | IF '(' expr ')' stmt
     | error ';'           /* 遇錯就丟 token 直到看到 ';' */
     ;
```

這樣一個語法錯誤的語句不會讓整個編譯放棄，parser 會吞到下一個 `;` 繼續下去。

有了這個，你可以一次報多個語法錯誤，而不是每改一個錯誤再編譯一次。

## 開發流程建議

1. 寫文法時，每加一條規則就跑 `bison -v` 確認沒有新衝突
2. 有衝突優先用 `%left/%right/%nonassoc` 解
3. 處理 dangling else 用 `%prec IFX` 模式
4. reduce/reduce 衝突幾乎都要改文法
5. 保留最終一個 `error` 規則做恢復

## 動手練習

1. 寫一個二義運算子文法，故意不加 `%left`，看 bison 報幾個衝突。
2. 加上 `%left '+' '-'`、`%left '*' '/'`，驗證衝突消失。
3. 加入一元減號，故意不加 `%prec UMINUS`，看輸入 `-3 * 2` 會怎樣。然後加上解決。
4. 實作 dangling else，比較兩種解法的 `.output` 狀態數。
5. 加入 `error ';'` 規則，輸入一段有多個語法錯誤的 code，看它能不能都抓到。

## 自我檢核

- [ ] 我能用 `%left/%right` 解決運算子衝突
- [ ] 我能用 `%prec UMINUS` 處理一元運算子
- [ ] 我知道 dangling else 的兩種解法
- [ ] 我能打開 `.output` 找到具體衝突狀態
- [ ] 我知道 reduce/reduce 衝突通常要改文法

→ [Ch 10 lex + yacc 整合](./10-lex-yacc-integration.md)
