# Ch 33 — weggli：C/C++ 半結構 pattern

> **目標**：把 weggli 練成你掃 C/C++ 大 codebase 的第一道快刀。你已經會 CodeQL/Joern 這種能建 CPG、追跨函式 dataflow 的深工具；weggli 是光譜的另一端——**單函式內、懂 AST 的 grep**，不建資料庫、不追污染、秒級掃完整個 kernel。這章要你搞懂它的定位（該用它 vs 該用深工具）、把語法（`$var`/`_`/`...`/`not:`/`-R`/`-u`）全部真跑一遍看輸出、並認清它的邊界：它找的是「結構可疑點」，不是「已確認漏洞」。
> **環境**：weggli 0.2.4，WSL Ubuntu 22.04

前面 30 章你花了大力氣在「重工具」上：CodeQL 要 build database、寫 QL、跑跨函式 taint；Joern 要載進 CPG、寫 Scala 查詢。它們精，但貴——建庫要幾分鐘到幾小時，一條 global taint query 可能跑幾十分鐘。真實審計裡，你**不會一開始就開這些**。你會先問一個便宜的問題：「這個幾百萬行的 tree 裡，有哪些地方**長得像** bug？」weggli 就是回答這個問題的工具。

## weggli 是什麼：懂 AST 的 grep

weggli 出自 Google Project Zero（研究員 Felix Wilhelm），血統就寫明了用途：**給漏洞獵人在巨大 C/C++ codebase（Linux kernel 是頭號目標）裡快速掃可疑 pattern**。它用 Rust 寫、基於 tree-sitter 解析，所以：

- **懂 C/C++ 語法（AST-aware）**：你寫 `memcpy($d, $s, $n)`，它匹配的是「一個 `memcpy` 呼叫、剛好三個引數」這個 AST 結構，不是一串文字。空白、換行、註解、格式化風格都騙不了它。
- **半結構化（semi-structured）**：你不用寫完整合法的 C。你寫的是**帶洞的程式碼片段**——用 `$var` 當變數佔位、`_` 當萬用、`...` 當「中間隨便幾行」。weggli 幫你把這個片段當 pattern 去 tree 裡對。
- **快**：Rust + 純語法匹配、不建全域資料庫。等下你會看到它 0.3 秒掃完 12 萬行的 libgit2。

一句話定位：**weggli = grep 的精準度升級版，但只在單一函式範圍內做結構匹配、不碰 dataflow**。

### 跟 grep 的根本差別

grep 對的是「字元序列」。weggli 對的是「AST 節點」。差別在真實 code 上會咬人。看這段（`~/audit-lab/weggli-lab/samples.c` 的 `tricky` 函式）：

```c
/* comment trick: this memcpy(x,y,z) is in a comment and split
   across
   lines */
void tricky(char *d, char *s) {
    memcpy(d,
           s,
           strlen(s));     // multi-line + strlen size
}
```

grep 找 `memcpy(`：

```
$ grep -n "memcpy(" samples.c
7:    memcpy(dst, src, n);
12:    memcpy(dst, src, sizeof(dst));
42:/* comment trick: this memcpy(x,y,z) is in a comment and split
46:    memcpy(d,
```

grep **命中了註解裡的假 memcpy（第 42 行）**，而且真正的多行 memcpy 它只看到 `memcpy(` 開頭那行（46），完全不知道引數其實跨三行、size 是 `strlen(s)`。你沒法用一條 grep 正則穩定抓「memcpy 的第三個引數是 strlen」。

weggli 直接對結構：

```
$ weggli '{ memcpy(_, _, strlen(_)); }' samples.c
/home/ypp/audit-lab/weggli-lab/samples.c:45
void tricky(char *d, char *s) {
    memcpy(d,
           s,
           strlen(s));     // multi-line + strlen size
}
```

它**跳過註解、把跨三行的呼叫當一個節點、確認第三個引數是 `strlen(...)`**。這就是 AST-aware 的意義：pattern 對的是語意結構，不是版面。

### 跟 CodeQL/Joern 的根本差別

這條界線更重要，因為你已經會深工具，很容易把 weggli 當成它們的窮人版來用——那會踩雷。核心差別只有一句：

**weggli 只在單一函式內做結構匹配，完全不追跨函式 dataflow。**

- CodeQL/Joern 能回答：「這個 `memcpy` 的 size，**是不是從某個網路 read 一路流過來的**（可能經過三層函式呼叫、幾個賦值）？」——這是 taint，是跨函式 dataflow。
- weggli 能回答：「這個函式裡，有沒有一個 `memcpy`，它的 size 是**這個函式內的某個變數**（不是常數、不是 sizeof）？」——這是結構，不是 flow。它看不到那個變數的值從哪來。

所以 weggli 給你的永遠是**「候選點（candidate）」不是「確認的漏洞」**。它縮小搜索面，深工具或人眼再確認來源。這正是 Ch 35 漏斗的核心：weggli 在漏斗頂端秒級砍面，CodeQL 在底端花時間深查那剩下的少數。

## 語法完整巡覽（全部真跑）

weggli 的 pattern 就是一段「帶洞的 C」，放在 `{ ... }`（函式體語境）或直接是運算式。核心元素：

| 元素 | 意義 |
|---|---|
| `$name` | **具名變數**，綁定一個「單一識別子/簡單運算式節點」。同名要對到同一個東西 |
| `_` | 萬用（wildcard），對任何**單一**子節點，不綁定、不要求一致 |
| `...` | 對「中間任意多個 statement」（只能用在 statement 位置） |
| `not: <pattern>` | negative sub-pattern：函式內**不得**出現這個結構才算命中 |
| `-R '$v=regex'` | 用正則約束某個 `$v` 綁到的**名字** |
| `-u` | unique：要求不同 `$var` 綁到不同的東西 |
| `-A/-B n` | 命中前後多印 n 行 context |
| `-X` | strict/unique 相關的嚴格模式 |

下面每一條都對 `~/audit-lab/weggli-lab/samples.c` 真跑。

### `_` 萬用 vs `$var` 具名：一個關鍵區別

先看「所有 memcpy 呼叫」——用三個 `_`：

```
$ weggli '{ memcpy(_, _, _); }' samples.c
/home/ypp/audit-lab/weggli-lab/samples.c:6
void copy_var(char *dst, char *src, int n) {
    memcpy(dst, src, n);
}
/home/ypp/audit-lab/weggli-lab/samples.c:11
void copy_const(char *dst, char *src) {
    memcpy(dst, src, sizeof(dst));
}
/home/ypp/audit-lab/weggli-lab/samples.c:45
void tricky(char *d, char *s) {
    memcpy(d,
           s,
           strlen(s));     // multi-line + strlen size
}
```

三個都中：`_` 對任何東西。現在把 size 位改成 **`$n`（具名變數）**：

```
$ weggli '{ memcpy(_, _, $n); }' samples.c
/home/ypp/audit-lab/weggli-lab/samples.c:6
void copy_var(char *dst, char *src, int n) {
    memcpy(dst, src, n);
}
```

**只剩一個。** 這是 weggli 最容易被誤解的一點：`$n` 綁的是「一個簡單識別子/變數節點」——`n` 是，但 `sizeof(dst)`（第 12 行）和 `strlen(s)`（第 45 行）是**複合運算式**，綁不上 `$n`。這不是 bug，是特性：**你要「size 是個裸變數」的 memcpy 時，用 `$n` 就自動排掉了 `sizeof(...)` 這種通常安全的常數化 size**。這一步就幫你把「size 是變數所以可能可控」的可疑點篩了出來。

（若你要連 `sizeof`/`strlen` 都一起抓，用 `_`；`_` 對任何子節點，包含複合運算式。選 `$var` 還是 `_`，取決於你要不要「這個位置必須是個裸變數」這個約束。）

### `-R`：正則約束——約束的是「名字」，不是型別

`-R '$v=regex'` 讓你對某個變數綁到的**識別子名字**加正則。抓 `strcpy` 呼叫：

```
$ weggli -R '$fn=^strcpy$' '{ $fn(_, _); }' samples.c
/home/ypp/audit-lab/weggli-lab/samples.c:37
void unsafe_str(char *src) {
    char name[32];
    strcpy(name, src);
}
```

`$fn` 綁「被呼叫的函式名」，`-R` 把它鎖成 `^strcpy$`。你也可以 `-R '$fn=^str(cpy|cat)$'` 一次抓一整族危險函式。

**重要更正一個常見誤解**：`-R` 約束的是**名字**，不是型別。weggli 0.2.4 的 `-R` 沒有「限制變數型別是 int」這種功能——你若寫 `-R '$n=int'` 期待「只留 int 型的 size」，它會去比對**變數名叫不叫 int**，不是型別，幾乎必然 0 命中。這是我實跑踩到的坑：

```
$ weggli -R '$n=int' '{ memcpy(_, _, $n); }' samples.c
（無輸出——因為沒有變數「名字」是 int）
```

`-R` 的殺手用法反而是**否定**——`!=`。「找 memcpy，但第三個引數的名字**不是** `size`」（因為叫 `size` 的通常是刻意算好的長度，比較不可疑）：

```
$ weggli -R 's!=^size$' '{ memcpy(_,_,$s); }' samples.c
/home/ypp/audit-lab/weggli-lab/samples.c:6
void copy_var(char *dst, char *src, int n) {
    memcpy(dst, src, n);
}
```

只留下 size 引數叫 `n`（不叫 `size`）的那個。這招在大 repo 上非常有用——等下你會看到它把 libgit2 的 287 個 memcpy 砍到 99 個。

### `not:`：negative pattern——抓「缺了檢查」的模式

漏洞常常不是「出現了什麼」，而是「**該有的檢查沒有**」。`not:` 讓你要求「函式內不存在某結構」。經典題：**malloc 之後沒有 null check**。

lab 裡有一對對照組：

```c
char *alloc_bad(int n) {
    char *p = malloc(n);
    memset(p, 0, n);       // 用了 p，但前面沒檢查 p == NULL：bug
    return p;
}
char *alloc_good(int n) {
    char *p = malloc(n);
    if (!p) return NULL;   // 有檢查
    memset(p, 0, n);
    return p;
}
```

正確的 weggli 慣用寫法：綁 `$p = malloc(...)`，然後用三個 `not:` 排掉「函式內對 `$p` 做了任何形式的 null 比較」：

```
$ weggli '{ _* $p = malloc(_); not: $p == NULL; not: $p != NULL; not: !$p; }' samples.c
/home/ypp/audit-lab/weggli-lab/samples.c:16
char *alloc_bad(int n) {
    char *p = malloc(n);
    memset(p, 0, n);       // use before null-check: bug
    return p;
}
```

**只有 `alloc_bad` 命中，`alloc_good` 正確排掉。** 這是 weggli 抓「missing check」類 bug 的標準句型，值得背下來。注意細節：

- `_*` 是「型別位」的萬用（`$p` 的型別隨便，`char*`/`void*` 都行）。
- 三個 `not:` 要把常見的 null 檢查寫法都堵上——`p == NULL`、`p != NULL`、`!p`。**漏一種就會漏報**：若你只寫 `not: $p == NULL`，那用 `if (!p)` 檢查的 `alloc_good` 會被誤判成 bad。這是 negative pattern 的通病：你得窮舉「有效的檢查長什麼樣」，少列一種就是假陽。

（我實跑時試過偷懶版 `{ $p = malloc(_); not: if ($p) _; }`——它**漏掉** `if (!p)` 這種寫法，於是 `alloc_good` 沒被排掉，變成兩個都命中的假陽。教訓：negative pattern 要窮舉檢查的變體。）

### 陣列宣告 + 變數 index：固定 buffer 的越界寫

抓「固定大小陣列，用變數當 index 寫入」——經典 OOB write 候選：

```
$ weggli '{ char $b[_]; $b[$i] = _; }' samples.c
/home/ypp/audit-lab/weggli-lab/samples.c:31
void idx_write(int i, char v) {
    char buf[16];
    buf[i] = v;            // OOB if i unchecked
}
```

命中 `idx_write`：宣告了 `char buf[16]`，又用 `buf[i]` 寫（`i` 是參數，未檢查）。**踩雷提醒**：這裡陣列大小位要用 `_`（`char $b[_]`），別寫 `char $b[$size]`——我實跑時 `char $b[$size]`（想把大小綁成變數）會 0 命中，因為那個位置 weggli 綁不上一個具名變數節點。要匹配「有個固定大小陣列」就用 `char $b[_]`，別去綁大小。

### `-u`（unique）：要求不同變數綁不同東西

`-u` 強制不同的 `$var` 必須綁到不同的識別子。用在「來源和目的不能是同一個」這種約束——例如 `memcpy($dst, $src, _)` 若你想排掉 `$dst == $src` 的退化情形，加 `-u` 就會要求 `$dst`、`$src` 綁不同名字。單獨演示意義不大，但在大 repo 上減少「同一變數自我拷貝」這類無趣命中時有用。

## 在真實大 repo 上跑：速度與縮面

上面都是玩具檔。weggli 的真價值在**大 tree 上秒級縮面**。我 clone 了 libgit2 真跑（`/tmp/libgit2`，`src/` 底下 204 個 `.c` 檔、約 12.7 萬行 C）：

**先看「所有 memcpy 呼叫」有幾個、跑多久：**

```
$ cd /tmp/libgit2
$ time weggli '{ memcpy(_,_,_); }' src | grep -c '^/tmp'
287
real    0m0.336s
```

**287 個 memcpy，0.34 秒掃完 12 萬行。** 這速度是 weggli 存在的理由——你不會為了「先看看哪裡有 memcpy」去 build 一個 CodeQL database。

**再用 `-R '!=^size$'` 砍掉 size 引數叫 `size` 的（那些多半是算好的長度）：**

```
$ time weggli -R 's!=^size$' '{ memcpy(_,_,$s); }' src | grep -c '^/tmp'
99
real    0m0.425s
```

**287 → 99。** 一條約束，可疑面砍掉三分之二，還是半秒內。這 99 個才是值得往下看的「size 是奇怪變數」的 memcpy。

**kernel 尺度預期**：Linux kernel 是幾千萬行、幾萬個 memcpy。weggli 對這種規模的實測掃描時間，社群報告是**數秒到十幾秒等級**（它就是為此設計的）——我手邊沒 clone 整個 kernel，這個數字標「預期」，但從 libgit2 的 0.3s/12 萬行外推、加上 weggli 的線性掃描特性，量級是站得住的。真要跑：`git clone --depth 1 linux && time weggli '{ memcpy(_,_,$s); }' linux/`。

這個「幾百萬行 → 秒級 → 幾百個結構候選」的能力，就是 Ch 35 漏斗的第一層。weggli 不告訴你哪個是真 bug，但它把「該人眼/深工具看的地方」從幾百萬行壓到幾十幾百個。

## 對比：weggli 在工具光譜的位置

| 維度 | grep/rg | **weggli** | CodeQL / Joern |
|---|---|---|---|
| 匹配對象 | 字元序列 | **單函式 AST 結構** | 全程式 CPG + dataflow |
| 懂語法？ | 否（會命中註解/字串） | **是（跳註解、看結構）** | 是 |
| 跨函式 dataflow？ | 否 | **否** | 是（taint） |
| 需要 build/database？ | 否 | **否** | 是（分鐘～小時） |
| 速度（12 萬行） | 毫秒 | **~0.3 秒** | 建庫幾分鐘 + 查詢幾秒～幾十分 |
| 產出 | 文字命中 | **結構候選點** | 確認的 flow path |
| 語言 | 任意文字 | **只吃 C/C++** | 多語言 |
| 典型角色 | 最粗第一篩 | **秒級結構縮面** | 深查確認 |

weggli 卡在 rg 和 CodeQL 中間：**比 rg 準（懂結構、不被格式騙），比 CodeQL 快且無需建庫，但看不到 flow**。它是漏斗的「快而粗」那一端偏右一點——比純文字聰明，但不追來源。

## 踩雷集錦

**錯誤直覺：「weggli 能追資料從哪來，size 是不是攻擊者可控它會告訴我。」**
正確認識：weggli **完全不追跨函式 dataflow**。它只知道「這個 memcpy 的 size 是個叫 `n` 的變數」，不知道 `n` 是網路讀來的、還是上層算好的常數。它給你的是「結構候選」，「這個 size 到底可不可控」要嘛人眼往上讀、要嘛丟給 CodeQL/Joern 追 taint。把 weggli 命中當「確認的漏洞」是最大的誤用——它是**縮面工具，不是判定工具**。

**錯誤直覺：「`-R '$n=int'` 可以只留 int 型的 size。」**
正確認識：`-R` 約束的是變數**名字**（正則比對識別子字串），不是**型別**。`$n=int` 是去找「名字叫 int 的變數」，幾乎必然 0 命中（我實跑證實）。weggli 0.2.4 沒有型別約束這種東西。`-R` 的真正戰力在 `!=` 否定名字（如 `s!=^size$` 排掉算好的長度）和 `^str(cpy|cat)$` 這種危險函式族匹配。想按型別過濾，得靠 CodeQL/Joern。

**錯誤直覺：「pattern 寫寬一點多抓總比漏抓好。」**
正確認識：在 kernel 尺度上，`{ memcpy(_,_,_); }` 這種全萬用 pattern 會回你**幾萬個命中**，等於沒篩——你淹死在裡面，跟不用 weggli 一樣。weggli 的價值在**每加一個約束就砍一大批**：size 是變數（`$n` 排掉 sizeof）、size 名字不是 `size`（`-R !=`）、dst 是固定 stack buffer（`char $b[_]`）。你要的是「窄到剩幾十個值得看」，不是「寬到全中」。pattern 設計就是 triage 的前移。

**錯誤直覺：「negative pattern 只要寫一種檢查就能排掉安全的。」**
正確認識：`not:` 要**窮舉該檢查的所有寫法**，否則漏一種就假陽。malloc null check 至少要 `not: $p == NULL; not: $p != NULL; not: !$p` 三種——我實跑時只寫 `not: if ($p) _` 就漏掉 `if (!p)` 寫法，害 `alloc_good` 被誤判。negative pattern 的品質取決於你有沒有把「有效檢查長什麼樣」列全。

**錯誤直覺：「weggli 是通用結構搜尋，什麼語言都能用。」**
正確認識：weggli **只吃 C 和 C++**（tree-sitter 的 C/C++ grammar）。你拿它掃 Go/Rust/Java/JS 會直接沒用。要多語言結構搜尋是下一章 ast-grep 的地盤（tree-sitter 多 grammar）。weggli 專精 C/C++、深度換廣度，這是刻意的取捨——它為 kernel/系統 C 代碼而生。

## 進階延伸

- **weggli 的 pattern 設計即 triage 前移**：真正的高手不是「跑一條 pattern 看結果」，而是**迭代收緊** pattern——先寬看命中量，逐步加約束（`$var`、`-R !=`、`char $b[_]`、`not:`）把幾千砍到幾十。這跟 Ch 12 的批砍同類是一回事，只是搬到 pattern 層。每加一條約束前先想「這砍掉的是哪一類無趣命中」。
- **從 CVE 反推 weggli pattern（variant hunting）**：拿到一個已知 CVE 的 patch，把「修掉的那個危險結構」抽象成 weggli pattern，掃全 repo 找**同一個 bug 的其他實例**。P0 就是這樣用它做規模化 variant analysis 的（接 Ch 43 case study / final project）。
- **weggli 的限制與 CodeQL 的互補**：weggli 快但淺、CodeQL 慢但深，兩者不是替代是接力。標準玩法是 weggli 秒級產候選 → CodeQL 對這批候選點做跨函式 taint 確認來源。這個接力就是 Ch 35 漏斗的骨架。
- **`-X` 與 unique 語意的細節**：weggli 對「同一 pattern 在同一函式多次命中」「不同變數是否必須不同」有 `-u`/`-X` 這類旋鈕，處理起來要對照你要的去重語意。大 repo 上這些旗標影響命中量甚鉅，值得對你的目標 tree 實驗一輪找到訊噪比最好的組合。

## 本章重點整理

- weggli 出自 Project Zero，是 **Rust 寫的、懂 AST 的 C/C++ 半結構 grep**：跳註解、不被格式/換行騙、秒級掃 kernel 尺度，但**只在單函式內做結構匹配、不追跨函式 dataflow**。
- 語法核心：`$var`（綁裸變數，自動排掉 `sizeof`/`strlen` 這種複合運算式）、`_`（萬用，對任何子節點）、`...`（任意多 statement）、`not:`（negative pattern，抓「缺檢查」）、`-R`（**正則約束名字，非型別**，殺手用法是 `!=` 否定）、`-u`（unique）。
- 實跑證據：libgit2 12.7 萬行、`memcpy` 287 個、0.34 秒掃完；加 `-R 's!=^size$'` 砍到 99 個、仍 0.4 秒。這是漏斗第一層的「快而粗縮面」。
- weggli 給的是**結構候選點**不是確認漏洞：它縮面，來源可控性要靠人眼往上讀或丟 CodeQL/Joern 追 taint。
- 最大三個坑：把命中當確認的 bug（它不追 flow）、以為 `-R` 能限型別（它只比名字）、pattern 太寬命中爆炸。negative pattern 要窮舉檢查寫法否則假陽。weggli 只吃 C/C++。

## 自我檢核

- 用 `tricky` 那段多行 + 註解的 memcpy，說明 weggli 跟 grep 對它的行為差在哪、為什麼 grep 會命中註解而 weggli 不會。
- `{ memcpy(_,_,_); }` 回 287 個，`{ memcpy(_,_,$n); }` 少很多——`$n` 相對 `_` 多了什麼約束？為什麼這個約束剛好幫你篩掉通常安全的 size？
- 寫一條 weggli 抓「malloc 後沒做 null check」的 pattern，並解釋為什麼只寫一個 `not:` 會假陽。
- 你想按「size 引數是 int 型別」過濾 memcpy——weggli 能不能做？`-R '$n=int'` 會發生什麼？該用什麼工具做型別過濾？
- 給定一條 weggli 命中「這個 memcpy 的 size 是變數 `len`」，你**還不能**下的判斷是什麼？要確認它是真 bug，下一步該用哪個工具做什麼？

## 延伸閱讀

- **weggli GitHub repo（README + `weggli --help`）**——官方語法權威，`$var`/`_`/`...`/`not:`/`-R`/`-u`/`-X` 的完整定義與範例都在。用法：把本章每條 pattern 對照官方語法表跑一遍，特別注意 `-R` 的名字-正則語意。前提：本章。
- **Project Zero 部落格中用 weggli 做 variant analysis 的文章**——看原作者團隊怎麼把 weggli 用在真實 kernel/驅動漏洞獵捕。用法：學「從一個 bug 抽 pattern 掃全 tree 找變體」的思路，直接接 Ch 43 與 final project。前提：本章 + 一點漏洞背景。
- **tree-sitter 的 C/C++ grammar 文件**——weggli 的解析底層。用法：當你的 pattern 「應該中卻沒中」時，回去看 tree-sitter 把那段 code 解成什麼 AST 節點，就懂為什麼 `char $b[$size]` 綁不上、`char $b[_]` 才行。前提：對 parser/AST 有基本概念（本課 Ch 3）。
- **本課 Ch 22《CodeQL 全域 taint》/ Ch 30《Joern 語意查詢》**——weggli 的互補面。用法：weggli 產候選後，讀這兩章學怎麼對候選點做跨函式來源確認。前提：Part 4/5。這條就是漏斗接力的另一半。

你現在有了漏斗最頂端那把最快的刀。但 weggli 只吃 C/C++——當目標是 Go/Rust/Java，或你需要「結構搜尋 + 批次改寫」時，得換工具。下一章看 weggli 之外的結構搜尋家族：ast-grep 與 comby。

→ [Ch 34 結構搜尋家族](./34-structural-search-family.md)
