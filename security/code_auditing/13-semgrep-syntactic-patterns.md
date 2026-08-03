# Ch 13 — Semgrep 語法模式

> **目標**：進 Part 3 工具實戰。這章把 Semgrep 當「懂 AST 的 grep」拆開講——為什麼它的 pattern 長得像原始碼、為什麼它不會被空白/註解/換行騙、它跟 `grep` 的根本差別在哪。然後逐一真跑核心語法：**pattern**、**metavariable（`$X`，元變數）**、**ellipsis（`...`，省略號）**、`pattern-either`／`pattern-not`／`pattern-inside`／`metavariable-regex`／`metavariable-pattern`，最後把 rule 的 YAML 骨架講清楚。這章只做 **syntactic（語法/結構）匹配**——不追資料流。資料流是 Ch 14 taint mode 的事，這章先把「怎麼精確描述一段程式碼長什麼樣」練到位。
>
> **環境**：Semgrep 1.172.0，WSL Ubuntu 22.04

Semgrep 的定位一句話講完：**pattern 用你要找的那個語言的原始碼語法寫，Semgrep 把 pattern 和目標檔各自 parse 成 AST，再在 AST 上做結構比對**。走的是 **tree-sitter** parser（各語言各一套 grammar），所以它**不需要 build**——不編譯、不連結、不跑，丟原始碼就能掃。跨語言是免費的：換 `languages` 欄位，同一套匹配引擎換一套 grammar。

## 跟 grep 的根本差別：AST 不被格式騙

先立這章的核心命題，因為它決定你什麼時候該用 Semgrep 而不是 `grep`。

`grep` 比對的是**字元序列**。它不知道什麼是函式呼叫、什麼是註解、什麼是字串常數。於是這些對 `grep memcpy` 全都出問題：

```
memcpy(buf, data, 128);          // grep 命中（對）
// old: memcpy(a, b, n)          // grep 命中（錯，這是註解）
char *s = "call memcpy manually";// grep 命中（錯，這是字串內容）
mem
cpy(buf, ...)                    // grep 漏（斷行，但 C 允許）
memcpy   (  buf ,  data , 128 ); // 多空白，grep 若用 memcpy\( 就漏
```

Semgrep 的 `pattern: memcpy(...)` 走 AST：**它只匹配 AST 上真正是「呼叫 `memcpy`」的節點**。註解在 parse 階段就被丟掉、字串常數是另一種節點、空白與換行不影響 AST 結構。所以上面五行裡它只命中該命中的那些，且不受排版影響。

```
grep：            比對字元流 → 被註解/字串/空白/換行騙
Semgrep pattern： 比對 AST 節點 → 只認「結構上真的是這個構造」的地方
```

**這是分水嶺**：只要你要找的是「某種程式碼構造」（某個 API 這樣被呼叫、某個賦值後面接某個操作），Semgrep 對；只要你找的是純文字（某個字串常數、某個註解裡的 TODO），`grep` 更快更直接。別拿 Semgrep 當文字搜尋、也別拿 `grep` 當結構搜尋。

## 核心語法：一次一個真跑

以下全部對這個自建檔跑（`~/audit-lab/ch13-15/net.c`）：

```c
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

void copy_fixed(int fd) {
    char buf[64];
    char *data = malloc(128);
    read(fd, data, 128);
    memcpy(buf, data, 128);      // 命中：memcpy(...)
    free(data);
}

void copy_via_var(int fd, size_t n) {
    char *p = malloc(n);         // p = malloc(...)
    read(fd, p, n);
    char dst[32];
    memcpy(dst, p, n);
}

void safe_one(void) {
    char a[8], b[8];
    memcpy(a, b, sizeof(a));     // 也是 memcpy(...)，靜態安全
}
```

### pattern + ellipsis（`...`）

`...` 是 **ellipsis**，意思是「這裡有零到多個我不在乎的東西」——可以是引數、語句、字元。**它不是 regex**（這是頭號誤解，見踩雷）。`memcpy(...)` = 「呼叫 `memcpy`，引數不管幾個、是什麼」。

```yaml
rules:
  - id: find-memcpy
    languages: [c]
    severity: INFO
    message: "memcpy call"
    pattern: memcpy(...)
```

真跑 `semgrep --quiet --config r-memcpy.yml net.c`，照貼輸出：

```
┌─────────────────┐
│ 3 Code Findings │
└─────────────────┘
    net.c
     ❱ find-memcpy
            9┆ memcpy(buf, data, 128);
           17┆ memcpy(dst, p, n);
           22┆ memcpy(a, b, sizeof(a));
```

三個 `memcpy` 全中，包含最後那個靜態安全的（`sizeof`）。**syntactic pattern 不判斷安全性，它只認結構**——要排除安全形，得靠 `pattern-not`（下面）。

### metavariable（`$X`）：跨處綁定同一個東西

metavariable（元變數）以 `$` 開頭、大寫慣例。它像 regex 的捕獲群組，但**綁的是 AST 子樹，且同名必須是同一個東西**。這是它比 `grep` 強的第二層：能表達「這裡和那裡是同一個變數」。

```yaml
rules:
  - id: malloc-then-memcpy-src
    languages: [c]
    severity: WARNING
    message: "buffer $X from malloc flows into memcpy source"
    pattern: |
      $X = malloc(...);
      ...
      memcpy($DST, $X, $N);
```

讀法：某個 `$X` 由 `malloc` 賦值，**中間隔著任意語句**（`...` 在語句位置 = 任意多行），然後 `$X` 出現在 `memcpy` 的來源位置。`$X` 兩處必須綁到同一個變數。真跑輸出：

```
┌─────────────────┐
│ 2 Code Findings │
└─────────────────┘
    net.c
    ❯❱ malloc-then-memcpy-src
          buffer data from malloc flows into memcpy source
            7┆ char *data = malloc(128);
            8┆ read(fd, data, 128);
            9┆ memcpy(buf, data, 128);
    ❯❱ malloc-then-memcpy-src
          buffer p from malloc flows into memcpy source
           14┆ char *p = malloc(n);
           15┆ read(fd, p, n);
           17┆ memcpy(dst, p, n);
```

注意 message 裡 `$X` 被填成實際變數名（`data`、`p`）——metavariable 能帶進 message，triage 時直接看得到綁了什麼。`safe_one` 沒中，因為它的 `a`/`b` 不是 `malloc` 來的。**這已經是「多語句、跨變數綁定」的結構匹配，`grep` 做不到**。但請注意：這條 rule 用 `...` 表示「中間有任意語句」，它**不驗證資料真的流過去**——中間就算把 `$X` 換掉，只要文字上還能對上模式仍會命中。真正的資料流是 Ch 14。

### pattern-either：多形合一

`pattern-either` = OR。抓「read 家族」的兩種形（`read` 三參數、`recv` 四參數以上）：

```yaml
rules:
  - id: read-family-into-heap
    languages: [c]
    severity: INFO
    message: "read/recv into $BUF"
    patterns:
      - pattern-either:
          - pattern: read($FD, $BUF, $N)
          - pattern: recv($FD, $BUF, $N, ...)
```

真跑輸出（`recv` 在此檔沒出現，只中 `read`）：

```
┌─────────────────┐
│ 2 Code Findings │
└─────────────────┘
    net.c
     ❱ read-family-into-heap
            8┆ read(fd, data, 128);
           15┆ read(fd, p, n);
```

### pattern-not：減去不想要的形

`pattern` 找到集合後，`pattern-not` 從裡面**扣掉**符合另一個形的。經典用法：抓所有 `memcpy` 但排除「size 是 `sizeof(...)`」的靜態安全形。

```yaml
rules:
  - id: memcpy-nonconst-size
    languages: [c]
    severity: WARNING
    message: "memcpy with non-sizeof size $N"
    patterns:
      - pattern: memcpy($DST, $SRC, $N)
      - pattern-not: memcpy($DST, $SRC, sizeof(...))
```

真跑輸出——從 3 個 `memcpy` 扣掉 `sizeof` 那個，剩 2 個：

```
┌─────────────────┐
│ 2 Code Findings │
└─────────────────┘
    net.c
    ❯❱ memcpy-nonconst-size
          memcpy with non-sizeof size 128
            9┆ memcpy(buf, data, 128);
    ❯❱ memcpy-nonconst-size
          memcpy with non-sizeof size n
           17┆ memcpy(dst, p, n);
```

`safe_one` 的 `memcpy(a, b, sizeof(a))` 被 `pattern-not` 切掉了。**`pattern-not` 是降誤報的第一把刀**——先寬抓，再減去已知安全形（Ch 15 會把這套系統化）。

### metavariable-regex：對綁定值再套文字條件

前面的 metavariable 綁的是 AST 子樹；`metavariable-regex` 讓你**對綁定到的那個 token 的文字再加 regex 條件**。例如「$F 是個呼叫，但函式名必須是 read/recv/fread 之一」：

```yaml
rules:
  - id: source-call-regex
    languages: [c]
    severity: INFO
    message: "input source $F(...)"
    patterns:
      - pattern: $F($FD, $BUF, ...)
      - metavariable-regex:
          metavariable: $F
          regex: ^(read|recv|fread)$
```

真跑輸出——`$F($FD, $BUF, ...)` 本來會匹配一堆三參數呼叫，regex 把 `$F` 收窄到只剩 `read`：

```
┌─────────────────┐
│ 2 Code Findings │
└─────────────────┘
    net.c
     ❱ source-call-regex
            8┆ read(fd, data, 128);
           15┆ read(fd, p, n);
```

**這是 AST 結構 + 文字條件的混合**：結構決定「這是個三參數呼叫」，regex 決定「函式名要對」。`metavariable-pattern`（沒在這裡跑）更進一步——對綁定的子樹再套一整條 Semgrep pattern，用於「$X 必須本身是某種構造」的巢狀條件。

### pattern-inside：限定命中要在某個範圍內

`pattern-inside` 不減也不加命中，它加一個**上下文條件**：命中必須發生在某個更大構造內部（某個函式、某個迴圈、某個 `if`）。例如「只找出現在 `while` 迴圈裡的 `memcpy`」——把外層 `pattern-inside: while (...) { ... }` 和內層 `pattern: memcpy(...)` 用 `patterns:` 綁一起即可。它常用來把「危險 API」限定在「危險上下文」（迴圈裡的拷貝、無鎖區段裡的存取），大幅壓低誤報。

## rule YAML 骨架

上面每條 rule 的最小結構固定就這幾欄，記熟：

```yaml
rules:
  - id: my-rule-id          # 唯一識別，triage/抑制/測試都靠它
    languages: [c]          # 決定用哪套 tree-sitter grammar，不填不會掃
    severity: WARNING       # INFO / WARNING / ERROR
    message: "..."          # 命中時印的話，可內嵌 $X 綁定值
    pattern: memcpy(...)    # 單一 pattern；多條件時改用 patterns: 底下組合
```

多條件時 `pattern:` 換成 `patterns:`（AND 語意，底下列 `pattern`／`pattern-not`／`pattern-inside`／`metavariable-*`）或 `pattern-either`（OR）。`languages` **漏填或填錯，Semgrep 選不到 grammar，掃 0 個檔卻不報錯**（踩雷常客）。

## 「pattern 太寬」會怎樣：命中爆炸

把 pattern 寫成 `$F(...)`（任意函式的任意呼叫），對 `net.c` 跑，用 JSON 數命中數：

```
8 findings
```

一個 23 行的小檔，`$F(...)` 命中 8 處（每個函式呼叫都算）。**pattern 越寬命中越爆**——真實專案這種 rule 會吐幾萬條，triage 成本壓垮你。寫 pattern 的紀律是**從能表達的最窄結構起手**：要 `memcpy` 就寫 `memcpy(...)` 別寫 `$F(...)`；要特定 size 就 `metavariable-comparison`（Ch 15）；要特定上下文就 `pattern-inside`。寬 pattern 不是「保險」，是「自找 triage 地獄」。

## 對比演進：從 grep 到 syntactic pattern 到 dataflow

把這章放進工具譜系（呼應 Ch 8 的精度表）：

| 手段 | 看到的東西 | 抓得到 | 抓不到 |
|---|---|---|---|
| `grep` | 字元流 | 純文字特徵 | 被註解/字串/空白騙；不懂結構 |
| **Semgrep syntactic（本章）** | 單檔 AST | 程式碼構造、跨處變數同名綁定 | **資料是否真的流過去**（`...` 只是「中間有東西」） |
| Semgrep taint（Ch 14） | AST + 輕量 dataflow | source→sink 的真流動、sanitizer 切斷 | 深度 inter-proc、精確 alias（Ch 8） |

**本章是第二格**：比 grep 懂結構，但還不追流。`$X = malloc(...); ... memcpy($DST, $X, ...)` 這條看起來像「追資料流」，其實只是「文字上 `$X` 先被 malloc 賦值、後面又出現在 memcpy」——中間若 `$X` 被重新賦值成別的東西，它照樣命中（誤報）。要判斷「這個值真的是那個 malloc 來的、中途沒被換掉」，得升級到 taint mode。Ch 14 就補這一格。

## 踩雷集錦

**錯誤直覺：「`...` 是 regex 的 `.*`。」**
正確認識：`...` 是 **ellipsis**，是 AST 層的「零到多個節點」，不是文字層的萬用字元。它在引數位置 = 任意多個引數，在語句位置 = 任意多條語句，在函式體 = 任意內容。它**不能**寫成 `mem...py` 去匹配 `memcpy`——那不是它的語意。要對 token 文字做萬用比對，用 `metavariable-regex`。把 `...` 當 `.*` 是新手最普遍的翻車點。

**錯誤直覺：「同名 metavariable 只是變數名，隨便取。」**
正確認識：**同一條 pattern 裡同名 metavariable 必須綁到同一個 AST 子樹**。`memcpy($X, $X, $N)` 只命中「來源和目的是同一個東西」的 `memcpy`（多半是 bug）；`memcpy($X, $Y, $N)` 才是來源目的可不同。想「這裡和那裡是同一個變數」就同名，想「可以不同」就換名。取名不是裝飾，是約束。

**錯誤直覺：「pattern 寫寬一點比較保險，免得漏。」**
正確認識：寬 pattern 換來的是命中爆炸與 triage 地獄——`$F(...)` 在 23 行檔就吐 8 條，真實 codebase 幾萬條。**寬不等於覆蓋率高，只等於雜訊高**：真陽性淹在假陽性裡，等於沒抓到。紀律是從最窄的可表達結構起手，需要放寬再用 `pattern-either` 精準加形，不要一把 `$F(...)` 梭哈。

**錯誤直覺：「rule 沒 `languages` 也能掃。」**
正確認識：`languages` 決定選哪套 tree-sitter grammar。**漏填或填錯（例如 C 檔卻寫 `languages: [cpp]` 而 pattern 有 C 專屬構造），Semgrep 可能掃 0 個檔卻「成功」退出、不報錯**。你以為掃乾淨了，其實引擎根本沒認得你的檔。每條 rule 第一件事確認 `languages` 對，且跑完看「Scanning N files」的 N 不是 0。

**錯誤直覺：「syntactic pattern 命中 `malloc(...); ...; memcpy($X,...)` 就代表資料真的從 malloc 流到 memcpy。」**
正確認識：`...` 只保證「中間有任意語句」，**不保證中間沒把 `$X` 換掉、也不保證這條路徑真的可達**。這是 syntactic 匹配的本質天花板——它認的是「程式碼長這樣」不是「資料這樣流」。想要「值真的從 A 流到 B、中途沒被淨化」，那是 taint 語意，必須用 Ch 14 的 `mode: taint`。把 syntactic 命中當資料流結論，是本章最容易溢出解讀的地方。

## 進階延伸

- **Semgrep Playground（線上 pattern 實驗場）**：貼一段 code、即時看你的 pattern 命中哪裡、AST 長怎樣。寫複雜 pattern 前先在這裡試，比在 CLI 反覆改 YAML 快十倍。前提：本章的 pattern／metavariable／ellipsis 概念。
- **`metavariable-pattern`（巢狀 pattern）**：對某個 metavariable 綁到的子樹再套一整條 Semgrep pattern，例如「$X 必須本身是個 `malloc` 呼叫」。用於單靠 `metavariable-regex` 的文字條件表達不了的結構條件。讀官方 pattern syntax 文件的 metavariable-pattern 段。前提：本章 `metavariable-regex`。
- **`generic` mode 與 `spacegrep`**：對 Semgrep 沒有 tree-sitter grammar 的語言/設定檔（Dockerfile 片段、自訂 DSL、log 格式），Semgrep 提供不 parse AST、走「近似結構」的 generic mode。理解它為什麼比純 grep 好又比 AST 弱，能補上「沒 grammar 的語言怎麼辦」這塊。前提：本章 grep vs AST 的分野。

## 本章重點整理

- Semgrep = **懂 AST 的 grep**：pattern 用目標語言原始碼語法寫，走 tree-sitter parse、**不需 build**、跨語言。與 grep 的根本差別是**認結構不認字元**，不被註解/字串/空白/換行騙。
- 核心語法：**pattern**（要找的構造）、**metavariable `$X`**（綁 AST 子樹，同名必同物）、**ellipsis `...`**（零到多個節點，**不是 regex**）、`pattern-either`（OR）、`pattern-not`（減去不要的形）、`pattern-inside`（限定上下文）、`metavariable-regex`／`metavariable-pattern`（對綁定值再加文字/結構條件）。
- rule YAML 骨架：`id` / `languages`（不填選不到 grammar，掃 0 檔還不報錯）/ `severity` / `message`（可嵌 `$X`）/ `pattern` 或 `patterns:`。
- **本章只做 syntactic 匹配**：`malloc(...); ...; memcpy($X,...)` 看似追流，其實只是「文字上先後出現」——不驗證值真的流過去、中途沒被換掉。真資料流是 Ch 14 taint mode。
- 降誤報第一把刀是 **`pattern-not` 減去已知安全形**、以及**從最窄結構起手**別寫 `$F(...)` 這種寬 pattern（23 行檔就吐 8 條）。

## 自我檢核

- 用你自己的話說出 Semgrep pattern 和 `grep` 的根本差別。給一個 `grep memcpy` 會誤報、Semgrep `memcpy(...)` 不會誤報的具體例子。
- `...` 和 regex 的 `.*` 差在哪？`memcpy($X, $X, $N)` 和 `memcpy($X, $Y, $N)` 命中的東西差在哪？
- 你要抓「所有 `memcpy` 但排除 size 是 `sizeof(...)` 的安全形」。寫出用到 `patterns:` + `pattern` + `pattern-not` 的 rule 骨架。
- 一條 rule 掃真實 codebase 吐了三萬條命中。從本章角度，最可能的兩個原因是什麼？各怎麼收窄？
- 為什麼 `$X = malloc(...); ...; memcpy($DST, $X, ...)` 命中，**不能**直接下結論「這個 buffer 的內容真的從 malloc 流進了 memcpy」？要證明資料流得換什麼？

## 延伸閱讀

- **Semgrep 官方 *Pattern syntax* 文件**——本章每個語法元素（ellipsis、metavariable、`pattern-either/not/inside`、`metavariable-regex/pattern/comparison`）的權威定義與邊界案例。當作查手冊反覆回來翻。優先讀 ellipsis 與 metavariable 兩段。前提：本章。
- **Semgrep 官方 registry（`semgrep --config auto` 背後的規則庫）**——上千條真實維護中的規則，是學「業界怎麼把一個漏洞類寫成精準 pattern」的活教材。挑幾條 C/Python 的 CWE 規則讀它怎麼用 `pattern-not` 收誤報。前提：本章 rule 骨架。
- **tree-sitter 專案文件**——Semgrep 底層 parser。讀「grammar 怎麼把原始碼變 AST」能徹底理解「為什麼 pattern 不被排版騙」以及「為什麼沒 grammar 的語言只能 generic mode」。想打通底層機制讀這個。前提：本章 grep vs AST 分野。

syntactic pattern 練到這裡，你能精確描述「程式碼長什麼樣」，但還答不了審計最核心的問題——「攻擊者控制的資料，會不會流到危險操作」。那需要在 AST 上疊資料流。下一章把 Ch 7 的 source/sink/sanitizer 理論落成 Semgrep 的 `mode: taint`。

→ [Ch 14 Semgrep taint mode](./14-semgrep-taint-mode.md)
