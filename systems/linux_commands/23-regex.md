# Ch 23 — 正規表示式

> **目標**：徹底理解正則表達式（regular expression, regex）——它是什麼（一種描述「文字模式」的語言）、底層怎麼運作（NFA/DFA 狀態機）、BRE/ERE/PCRE 三種方言的差異、貪婪/惰性匹配、以及為什麼有些 regex 會讓 CPU 100%（catastrophic backtracking）。這是 Part 6 的地基——grep/sed/awk 全建在 regex 上。

> **環境**：bash 5.x，GNU grep/sed（BRE/ERE）。PCRE 用 `grep -P` 或 Perl/Python。

## 為什麼 regex 是文字處理的地基？

接下來四章（grep/sed/awk/text utils）全部依賴一個共同基礎——正則表達式。grep 用 regex 找行、sed 用 regex 匹配替換、awk 用 regex 選欄位。不懂 regex，這些工具你只會用最皮毛。

regex 是「用一個模式描述一整類字串」的語言。`[0-9]+` 描述「一個或多個數字」、`^Error` 描述「以 Error 開頭的行」。它讓你不用寫程式就能表達複雜的文字匹配規則。理解它的底層（狀態機）還能解釋為什麼某些 regex 快、某些慢到當機。這章把 regex 從「背符號」變成「理解它是個狀態機」。

## 先建立直覺：regex 是一台「模式比對機器」

```
regex 是「描述一類字串的模式」：

  不是描述「一個」字串，而是「一整類」：
  pattern: [0-9]+
  匹配：  "5", "42", "1000", "999999"...（所有純數字串）
        │
  把 regex 想成一台「比對機器」：
  pattern "ab*c" 變成一台機器：
       a      b（可重複）    c
    ─▶(1)──▶(2)──┐  ──▶(3)──▶(接受)
                 └─┘
                 b 可以走 0 次或多次
        │
  餵字串進去，看機器能不能走到「接受」狀態：
    "ac"    → a, (b 跳過), c → 接受 ✓
    "abc"   → a, b, c → 接受 ✓
    "abbbc" → a, b,b,b, c → 接受 ✓
    "axc"   → a, x?(卡住) → 拒絕 ✗
        │
  → regex 編譯成一台狀態機，字串「走」過機器，看能否到達接受狀態
```

關鍵心智：regex 是描述「一整類字串」的模式，不是單一字串。它底層編譯成一台**狀態機**（state machine）——字串餵進去「走」過狀態，能走到接受狀態就匹配。理解這個狀態機模型，後面的貪婪匹配、回溯、catastrophic backtracking 全都說得通。

## regex 的基本元件

```
regex 的構成元件（建立詞彙表）：

  字面字元：     a b c 1 2   匹配自己
  . （點）：     任意一個字元（除換行）
  字元類 []：    [abc] = a 或 b 或 c；[0-9] = 數字；[^abc] = 非 abc
  量詞：
    *           前面的東西 0 次或多次
    +           1 次或多次（ERE）
    ?           0 次或 1 次（ERE）
    {n,m}       n 到 m 次
  錨點：
    ^           行首
    $           行尾
    \b          詞邊界（PCRE/GNU）
  分組與選擇：
    (...)       分組（ERE）
    |           或（ERE）：cat|dog = cat 或 dog
  跳脫：
    \.          字面的點（不是「任意字元」）
```

```bash
# 用 grep -E（ERE）建立感覺
echo -e "cat\nbat\nrat\ndog" | grep -E '[cb]at'      # cat, bat（[cb] = c 或 b）
echo -e "5\n42\nabc\n100" | grep -E '^[0-9]+$'       # 5, 42, 100（純數字行）
echo "phone: 0912-345-678" | grep -Eo '[0-9]{4}'     # 0912（連續 4 個數字）
echo -e "error\nERROR\nErRoR" | grep -Ei 'error'     # 全部（-i 忽略大小寫）
echo "a.b.c" | grep -Eo 'a\.b'                       # a.b（\. = 字面點）
echo "a.b.c" | grep -Eo 'a.b'                        # a.b（. = 任意字元，這裡也匹配）
```

## 底層機制：NFA、回溯、DFA

regex 引擎有兩大流派，理解它解釋了所有效能問題：

```
兩種 regex 引擎實作：

  NFA（非確定有限自動機）+ 回溯（backtracking）：
    用於：PCRE、Perl、Python、Java、JavaScript、grep -P
    特性：支援反向引用(\1)、lookahead 等強大功能
    機制：嘗試一條路，走不通就「回溯」試另一條
    問題：某些 pattern 會「災難性回溯」（指數爆炸，CPU 100%）
        │
  DFA（確定有限自動機）：
    用於：grep（預設）、awk、egrep（傳統）
    特性：功能較少（不支援反向引用）
    機制：一次掃過字串，每個字元只看一次（無回溯）
    優勢：保證線性時間 O(n)，不會災難性回溯
        │
  → 這就是為什麼 grep 永遠很快（DFA，線性）
    而 grep -P / Python re 偶爾會卡死（NFA 回溯爆炸）
```

```
回溯的例子（NFA）：pattern "a*a" 匹配 "aaa"

  a* 貪婪：先吃掉所有 a → "aaa"
  然後要匹配最後的 a → 沒字元了！
  回溯：a* 吐回一個 a → "aa"，剩 "a" 給最後的 a → 匹配 ✓
        │
  回溯 = 「試錯」：貪婪地吃，不行就吐回來重試
  大部分情況很快，但巢狀量詞會讓回溯次數指數爆炸
```

> **NFA（回溯）vs DFA 是 regex 最重要的底層區別**。`grep` 預設用 **DFA**——把 regex 編譯成一台確定狀態機，掃過字串時每個字元只看一次，**保證 O(n) 線性時間**，永遠不會卡死（代價：不支援反向引用 `\1`、lookahead）。`grep -P`、Perl、Python `re`、JavaScript 用 **NFA + 回溯**——功能強大（反向引用、lookahead）但某些 pattern 會「災難性回溯」（catastrophic backtracking），時間指數爆炸，CPU 100%。這是 Russ Cox 經典文章的核心：**正則的功能和效能保證是個取捨**。知道你用的工具是哪種引擎，你才能預測它的行為——grep 處理 GB 級 log 不會慢，但一個寫壞的 Python regex 能讓伺服器掛掉（ReDoS 攻擊）。

## 三種方言：BRE、ERE、PCRE

regex 不是一種語言，是好幾種「方言」，這是新手最大的困惑源：

```
三種主要 regex 方言（同樣概念，不同語法）：

  BRE（Basic RE）—— grep、sed 預設：
    + ? { } ( ) | 要加反斜線才有特殊意義：\+ \? \{ \} \( \) \|
    （沒加反斜線時是「字面字元」！）
    例：grep 'a\+' 才是「一個或多個 a」；grep 'a+' 是「a 後面跟字面 +」
        │
  ERE（Extended RE）—— grep -E、egrep、awk：
    + ? { } ( ) | 直接有特殊意義（不用反斜線）
    例：grep -E 'a+' 就是「一個或多個 a」
    → 比較直覺，建議優先用 -E
        │
  PCRE（Perl Compatible RE）—— grep -P、Perl、Python：
    ERE + 更多：\d（數字）\w（詞字元）\b（詞邊界）
    lookahead (?=...)、反向引用、非貪婪 *?
    → 最強大，但用 NFA 回溯（可能慢）
```

```bash
# 同一個「一個或多個數字」，三種方言
echo "abc123" | grep    '[0-9][0-9]*'      # BRE（沒有 +，用 [0-9][0-9]*）
echo "abc123" | grep    '[0-9]\+'          # BRE（\+ 才是 +）
echo "abc123" | grep -E '[0-9]+'           # ERE（+ 直接用）
echo "abc123" | grep -P '\d+'              # PCRE（\d = 數字）

# BRE 的陷阱：+ 是字面字元
echo "a+b" | grep 'a+b'                     # 匹配！（BRE 裡 + 是字面 +）
echo "aaab" | grep 'a+b'                    # 不匹配（BRE 裡 a+ = 字面 "a+"）
echo "aaab" | grep -E 'a+b'                 # 匹配（ERE 裡 a+ = 多個 a）
```

> **方言差異是 regex 最大的踩雷源**。同一個 `a+`，在 BRE（grep 預設、sed）裡是「字面的 a 加號」，在 ERE（grep -E、awk）裡是「一個或多個 a」。這就是為什麼你複製的 regex 在不同工具裡行為不同。**建議：能用 ERE 就用 `-E`**（`grep -E`、`sed -E`），語法直覺、和大多數語言一致。需要 `\d`、lookahead、非貪婪時才用 PCRE（`grep -P`）。寫 regex 前先確認「這個工具用哪種方言」——這是省下大量 debug 時間的習慣。

## 貪婪 vs 惰性匹配

量詞預設「貪婪」（吃越多越好），這常造成意外：

```bash
# 貪婪：* + 預設盡量多吃
echo '<a><b>' | grep -Po '<.*>'      # <a><b>（貪婪：. 吃到最後一個 >）
echo '<a><b>' | grep -Po '<.*?>'     # <a>（惰性 *?：盡量少吃，PCRE）
#   .*  貪婪：從第一個 < 吃到最後一個 >
#   .*? 惰性：從第一個 < 吃到「下一個」>

# 經典陷阱：想抓 HTML 標籤卻抓了一整行
echo '<b>bold</b> text <i>italic</i>' | grep -Po '<.*>'
# <b>bold</b> text <i>italic</i>     ← 貪婪吃了全部！
echo '<b>bold</b> text <i>italic</i>' | grep -Po '<.*?>'
# <b> / </b> / <i> / </i>            ← 惰性，每個標籤分開
```

```
貪婪 vs 惰性：

  pattern <.*>  對 "<a><b>"
  貪婪 .*：盡量多吃 → 吃到最後一個 > → 匹配整個 "<a><b>"
  惰性 .*?：盡量少吃 → 吃到第一個 > → 匹配 "<a>"
        │
  貪婪是預設（* + {n,} 都貪婪）
  加 ? 變惰性（*? +? {n,}?）—— 只有 PCRE/Perl 系支援
        │
  → DFA 引擎（grep 預設）沒有貪婪/惰性概念（它找最長匹配）
    貪婪/惰性是 NFA 回溯引擎的概念
```

> **貪婪是預設，是「抓太多」bug 的頭號原因**。`.*` 盡量多吃——`<.*>` 對 `<a> text <b>` 會匹配整個 `<a> text <b>`（從第一個 `<` 到最後一個 `>`），不是你想的單個標籤。惰性 `.*?`（PCRE）盡量少吃，匹配到第一個 `>` 就停。記法：**`*` 貪心、`*?` 知足**。注意貪婪/惰性是 **NFA 回溯引擎**（PCRE）的概念——DFA 引擎（grep 預設）找「最長匹配」沒有惰性選項。需要惰性時用 `grep -P`。另外，能用更精確的字元類就別用 `.*`——`<[^>]*>`（吃非 `>` 字元）比 `<.*?>` 更明確也更快。

## 故意弄壞：catastrophic backtracking

regex 能讓 CPU 100%，這是真實的安全問題（ReDoS）：

```bash
# 災難性回溯：巢狀量詞 + 不匹配的輸入 → 指數爆炸
# pattern (a+)+$ 對 "aaaaaaaaaaaaaaaaaaaaX"（結尾 X 讓它匹配失敗）
# 用 Python 演示（grep -P 也會中招，grep 預設 DFA 不會）

time echo "aaaaaaaaaaaaaaaaaaaaaaaaaX" | grep -P '(a+)+$'
# 沒輸出（不匹配），但花很久！（回溯指數爆炸）
# (a+)+ 對 N 個 a 有指數多種「怎麼分組」的方式，X 讓全部都要試

# 對比：grep 預設（DFA）不會爆
time echo "aaaaaaaaaaaaaaaaaaaaaaaaaX" | grep -E '(a+)+$'
# 瞬間完成（DFA 線性，不回溯）

# 為什麼爆：(a+)+ 對 "aaaa" 有多種拆法
#   (aaaa) / (aaa)(a) / (aa)(aa) / (a)(aaa) / (a)(a)(aa) ...
#   結尾 X 不匹配 → 引擎回溯試「每一種拆法」→ 2^N 種 → 爆炸
```

> **catastrophic backtracking 是真實的攻擊面（ReDoS）**。`(a+)+$` 這種「巢狀量詞」對 NFA 回溯引擎是毒藥——對 N 個 a，`(a+)+` 有指數多種「怎麼分組」的可能，當結尾不匹配（X）時，引擎要回溯試遍所有可能（2^N 次），CPU 卡死。這在生產系統是真實漏洞：使用者輸入一個惡意字串，讓你的 regex（如表單驗證、log 解析）跑指數時間，伺服器掛掉（Cloudflare 2019 年就因此全球當機）。防範：避免巢狀量詞 `(a+)+`、`(a*)*`；用 DFA 引擎（grep 預設、RE2）；或設 regex 超時。`grep`（DFA）天生免疫——這是 DFA「功能少但保證線性」取捨的價值體現。

## 進階：常用 regex 模式速查

實務中反覆出現的 pattern，值得記住：

```bash
# IP 位址（粗略）
grep -E '([0-9]{1,3}\.){3}[0-9]{1,3}' access.log

# email（粗略，完整 email regex 極複雜）
grep -Eo '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' file

# 抓括號內的內容（惰性，PCRE）
grep -Po '\(\K[^)]*'        # \K：丟掉前面已匹配的（lookbehind 替代）

# 整行匹配（錨點）
grep -E '^[0-9]+$'          # 整行都是數字

# 詞邊界（避免部分匹配）
grep -P '\bcat\b' file      # 匹配單詞 cat，不匹配 category/concat

# 量詞精確控制
grep -E '^.{1,10}$'         # 1 到 10 個字元的行
grep -E '[0-9]{4}-[0-9]{2}-[0-9]{2}'   # 日期 YYYY-MM-DD

# 反向引用（PCRE，找重複的詞）
grep -P '\b(\w+)\s+\1\b' file    # 找連續重複的詞（the the）
```

> **`\b`（詞邊界）和 `\K` 是 PCRE 的實用利器**。`\bcat\b` 只匹配獨立的單詞 `cat`，不匹配 `category` 或 `concatenate`——詞邊界避免「部分匹配」的常見錯誤。`\K`（PCRE）丟棄前面已匹配的部分，是「lookbehind」的輕量替代——`\(\K[^)]*` 抓括號內容但不含括號本身。這些都需要 `grep -P`（PCRE）。實務中與其每次從頭寫 regex，不如記住這些常用模式（IP、email、日期、詞邊界）的骨架，再微調。但記住：**email/URL 的「完整正確」regex 極其複雜**（RFC 5322 的 email regex 有數百字元），實務上用「夠好」的近似就行，別追求完美。

## 動手練習

1. 三方言對比：對 `aaab` 跑 `grep 'a+b'`、`grep 'a\+b'`、`grep -E 'a+b'`，理解 BRE/ERE 的 `+` 差異

2. 貪婪陷阱：`echo '<a><b>' | grep -Po '<.*>'` vs `grep -Po '<.*?>'`，看貪婪和惰性的差別

3. 體驗回溯爆炸：跑「故意弄壞」的 `grep -P '(a+)+$'`（慢）vs `grep -E '(a+)+$'`（快），感受 NFA vs DFA

4. 用 [regex101.com](https://regex101.com)（線上 regex 測試器，能視覺化匹配和回溯）測試你的 pattern

## 本章重點整理

- regex 是描述「一整類字串」的模式語言，底層編譯成狀態機（字串「走」過狀態看能否到接受狀態）
- 兩種引擎：DFA（grep 預設，O(n) 線性，不回溯，功能少）vs NFA+回溯（PCRE，功能強，可能災難性回溯）
- 三種方言：BRE（grep/sed 預設，+?{}() 要跳脫）、ERE（grep -E/awk，直接用）、PCRE（grep -P，\d \b lookahead）——建議優先 ERE
- 貪婪（`*` 預設，盡量多吃）vs 惰性（`*?`，盡量少吃，PCRE）——「抓太多」bug 的主因
- catastrophic backtracking（巢狀量詞 `(a+)+`）是真實 ReDoS 攻擊面；DFA 引擎免疫

## 自我檢核

- [ ] 能解釋 regex 底層是狀態機，以及 DFA 和 NFA 的差別
- [ ] 知道 BRE/ERE/PCRE 的差異，能說出為什麼同個 regex 在 grep 和 grep -E 行為不同
- [ ] 理解貪婪 vs 惰性，能 debug「regex 抓太多」的問題
- [ ] 知道 catastrophic backtracking 是什麼、為什麼危險、grep 為什麼免疫
- [ ] 能寫出常見模式（IP、日期、詞邊界）而不用每次查

## 延伸閱讀

### 必讀文章

- **[Regular Expression Matching Can Be Simple And Fast](https://swtch.com/~rsc/regexp/regexp1.html)** — Russ Cox（2007）
  - **核心貢獻**：解釋為什麼 Perl/Python 的 NFA 回溯會指數爆炸，而 DFA（Thompson NFA）保證線性。這是理解 regex 效能的奠基之作
  - **讀哪裡**：整篇（有圖解 NFA/DFA 建構）；前半最關鍵
  - **和本章的關聯**：本章「DFA vs NFA」「災難性回溯」的完整理論，作者是 RE2（Go 的 regex 引擎）作者

### 書籍

- **《Mastering Regular Expressions》— Ch 4-6** — Jeffrey Friedl（O'Reilly, 3rd ed）
  - **讀哪幾章**：Ch 4（regex 引擎機制）、Ch 5（實戰技巧）、Ch 6（效能調校與回溯）
  - **這本書的定位**：regex 的權威巨著，把引擎內部、貪婪/惰性、回溯講到極致
  - **前提**：本章 + 用過 regex

### 工具 / 互動資源

- **[regex101.com](https://regex101.com)** — 線上 regex 測試器
  - **讀哪裡**：貼上 pattern 和測試字串，右側 "Regex Debugger" 能逐步看回溯
  - **為什麼值得讀**：視覺化匹配過程和回溯步數，是學 regex 最好的互動工具；能切換 PCRE/ERE 等方言

- **[Linux grep/sed regex quick reference](https://www.gnu.org/software/grep/manual/grep.html#Regular-Expressions)** — GNU grep manual
  - **讀哪裡**：Regular Expressions 整章，特別是 BRE/ERE 差異表
  - **為什麼值得讀**：權威定義 grep 支援的 BRE/ERE 語法，本章方言差異的官方來源

→ [Ch 24 grep](./24-grep.md)
