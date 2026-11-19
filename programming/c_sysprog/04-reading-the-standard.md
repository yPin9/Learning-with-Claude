# Ch 4 — 讀規格書：ISO/IEC 9899 與 C89 到 C23 的演進

> **目標**：能自己取得並翻查 C 語言規格書，分得清 constraint 與 semantics、規範與非規範內容，遇到「這樣寫行不行」的疑問時，用條款編號而不是用「我的編譯器跑得過」來回答；同時理解 C89 到 C23 每次改版在取捨什麼。

> **對應原文**：[你所不知道的 C 語言：開發工具和規格標準篇](https://hackmd.io/@sysprog/c-standards) 的「C 語言標準的演進」「讀規格書可大幅省去臆測」「ISO/IEC 9899 (簡稱 C99)」「規格改版的考量與取捨」「C23」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14；C23 範例以 Docker `gcc:14` 映像（GCC 14.4.0、glibc 2.41）執行。規格書引用以 C11 草案 N1570 的條款編號為主，C23 新特徵用 N3220。

## 為什麼需要這個？

網路上流傳一則笑話："Programming in C: if it doesn't work, just add a star. Or multiple stars. Or ampersands."——寫 C 卡住時，很多人的策略是改一改、編一編、跑跑看，直到「好像對了」。這個策略的問題在於，C 的編譯器會接受大量「規格書沒有保證」的程式碼，而且當下跑出你想要的結果。先看一個每個初學者都寫過的例子：

```c
#include <stdio.h>

void main(void)
{
    printf("hello\n");
}
```

```
$ gcc -Wall -Wextra -pedantic -std=c11 voidmain.c -o voidmain
voidmain.c:3:6: warning: return type of ‘main’ is not ‘int’ [-Wmain]
    3 | void main(void)
      |      ^~~~
$ clang -Wall -Wextra -std=c11 voidmain.c -o voidmain_c
voidmain.c:3:1: error: 'main' must return 'int'
void main(void)
^~~~
int
1 error generated.
```

同一份程式碼，gcc 給警告後照樣產生執行檔，clang 直接拒絕。誰對？如果你的判斷依據是「我用的編譯器怎麼說」，這題你永遠答不出來，因為兩個編譯器都沒有違反規格書——規格書在這裡允許實作自行決定（下面會看到條文）。能回答這個問題的只有規格書本身。

這一章要建立的習慣是：**疑問先查條文，再用編譯器驗證，最後才相信輸出**。順序反過來，你學到的是某個編譯器某個版本的脾氣，不是 C。

## 先建立直覺

把 C 標準想成一份**三方契約**：

```
         ┌────────────────────────────────────┐
         │  ISO/IEC 9899（C 標準 / 規格書）    │
         │  「符合規格的程式」在「符合規格的   │
         │   實作」上會有什麼行為              │
         └──────────┬───────────────┬─────────┘
                    │               │
       對程式設計者 │               │ 對實作者
     「你只要不碰   │               │「你必須讓這些
       UB，我保證…」│               │  程式照條文跑」
                    ▼               ▼
           ┌──────────────┐  ┌──────────────────┐
           │ 你的 .c 檔   │  │ gcc / clang /    │
           │              │  │ MSVC + libc      │
           └──────────────┘  └──────────────────┘
```

契約不是每一條都寫死。標準刻意替某些行為留下不同程度的彈性，這是讀規格書最先要分清楚的光譜：

```
 完全規定 ◄──────────────────────────────────────────────────► 完全不管
 
 well-defined   implementation-   unspecified       undefined
 （規定好了）   defined           （幾種可能之一， （標準不施加
                （實作選，且必須   不必寫文件）     任何要求）
                 寫進文件）
 
 例：1 + 1      例：-1 >> 1       例：函式引數的    例：有號整數溢位、
                    char 有無號       求值順序          解參考 NULL
```

光譜上越往右，同一份程式碼換個編譯器、換個最佳化等級、甚至換個日子，結果就越可能不同。「我跑過了沒問題」只對最左邊那格有意義。

> 這張光譜的每個名詞在 N1570 第 3 章都有正式定義，下面會逐一讀原文。未定義行為（undefined behavior，UB）的完整分類與編譯器怎麼利用它，留到 [Ch 34 — 未定義行為](./34-undefined-behavior.md)；本章只要求你能從條文辨認出它。

## 規格書怎麼取得

ISO 的正式文件要付費（數百瑞士法郎），但標準委員會 WG14 會公開每一版定稿前後的**草案**，和正式版只差在編輯層面，日常查閱完全夠用：

| 通稱 | 正式名稱 | `__STDC_VERSION__` | 可免費取得的草案 |
|---|---|---|---|
| K&R C | 《The C Programming Language》第一版（1978） | 無 | — |
| C89 / C90 | ANSI X3.159-1989 / ISO/IEC 9899:1990 | 無 | — |
| C95 | ISO/IEC 9899:1990/AMD1:1995 | `199409L` | — |
| C99 | ISO/IEC 9899:1999 | `199901L` | [N1256](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1256.pdf)（含 TC1–TC3） |
| C11 | ISO/IEC 9899:2011 | `201112L` | [N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)、[HTML 版](http://port70.net/~nsz/c/c11/n1570.html) |
| C17（又稱 C18） | ISO/IEC 9899:2018 | `201710L` | [N2176](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n2176.pdf) |
| C23 | ISO/IEC 9899:2024 | `202311L` | [N3220](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n3220.pdf) |
| C2y | 開發中 | — | [WG14 文件清單](https://www.open-std.org/jtc1/sc22/wg14/www/wg14_document_log)中最新的 Working Draft |

幾個容易混淆的地方：

- **C17 沒有新特徵**，只修正 C11 的缺陷報告（defect report，DR），所以本課以 N1570 為主要引用來源，條款編號在 C17 幾乎一樣。
- **C23 的正式名稱寫 2024**：技術作業在 2023 年完成，ISO 到 2024 年 10 月才出版，`__STDC_VERSION__` 則記為 `202311L`。
- **N3220 不是 C23 定稿**，而是 C23 之後第一份 C2y 工作草案，內容與 C23 實質相同，所以引用 C23 時常以它為準。
- **N 編號**是 WG14 的文件流水號，不只規格草案，提案、會議紀錄也都有 N 編號（例如本章後面引用的 N2611 是 C23 的章程）。

我的建議：**下載 N1570 的 PDF 放在手邊，再把 HTML 版加入書籤**。HTML 版每個條款都有錨點，可以直接把 `n1570.html#6.5.2.2` 這種連結貼給別人；PDF 則適合全文搜尋與列印。

## 規格書的骨架

第一次打開 N1570 會被它近 700 頁的篇幅嚇到。其實它的結構非常規律，搞懂骨架之後，日常查閱集中在 3、5、6、7 四章：

```
N1570
├── Foreword / Introduction   ← 非規範（只供參考）
├── 1  Scope                  ← 標準管什麼、不管什麼
├── 2  Normative references
├── 3  Terms, definitions, and symbols   ← 術語字典（object、UB…）
├── 4  Conformance            ← 「shall」違反了會怎樣
├── 5  Environment            ← 翻譯階段、main、執行環境、各種極限
│     5.1.1.2 Translation phases
│     5.1.2.2.1 Program startup（main 的形式）
│     5.2.4.1 Translation limits
├── 6  Language               ← 語言本體
│     6.2 Concepts（scope、linkage、storage duration、型別、表示法）
│     6.3 Conversions
│     6.4 Lexical elements
│     6.5 Expressions        ← 6.5.2.1 陣列下標、6.5.3.2 & 與 *…
│     6.7 Declarations
│     6.8 Statements
│     6.10 Preprocessing directives
├── 7  Library                ← 標準函式庫（7.21 stdio、7.22 stdlib…）
└── Annex A–L                 ← 有的規範、有的只供參考（見下）
       Annex J  Portability issues：UB/未指定/實作定義行為的總清單
```

**條款編號的讀法**：本課寫「6.5.2.2 §2」表示第 6.5.2.2 節第 2 段。N1570 每一段左邊都有小小的段落號，引用時連段落號一起給，別人才能精確定位。

**每個語言條款的內部結構**也一致。以函式呼叫 6.5.2.2 為例，原文長這樣（節錄自 N1570）：

```
6.5.2.2 Function calls
Constraints
1  The expression that denotes the called function shall have type pointer to
   function returning void or returning a complete object type other than an
   array type.
2  If the expression that denotes the called function has a type that includes
   a prototype, the number of arguments shall agree with the number of
   parameters. ...
Semantics
3  A postfix expression followed by parentheses () containing a possibly empty,
   comma-separated list of expressions is a function call. ...
...
6  If the expression that denotes the called function has a type that does not
   include a prototype, ... If the number of arguments does not equal the
   number of parameters, the behavior is undefined. ...
```

`Syntax`（文法）、`Constraints`（限制）、`Semantics`（語意）三個小標題反覆出現；有時還有 `Forward references`、`EXAMPLE`、`NOTE` 與腳註。這幾個小標題的法律效力完全不同，下面兩節就是本章的核心。

## 術語定義：第 3 章是字典

第 3 章開頭有一句很重要的話：

> Terms explicitly defined in this International Standard are not to be presumed to refer implicitly to similar terms defined elsewhere.

意思是：規格書裡的 object、byte、behavior 都是**術語**，不要用你在別處學到的意思去理解它。最經典的例子是 object（N1570 3.15）：

> **object**: region of data storage in the execution environment, the contents of which can represent values
>
> NOTE When referenced, an object may be interpreted as having a particular type; see 6.3.2.1.

這裡的 object 和物件導向的 object 毫無關係：它指的是「執行時期的一塊資料儲存區，其內容可以表示值」。一個 `int` 變數是 object，`malloc` 回來的一塊記憶體也是 object。規格書裡 object 出現的次數比 pointer 還多，許多教材急著談指標卻略過 object，這是指標學不好的根源之一——[Ch 7 — 規格書裡的指標](./07-pointers-by-the-standard.md)會從這個定義出發重建指標。

光譜上的四種行為也都在第 3 章（N1570 3.4.1–3.4.4，引文為原文節錄）：

| 術語 | 條款 | 原文定義（節錄） | 標準自帶的例子 |
|---|---|---|---|
| implementation-defined behavior | 3.4.1 | unspecified behavior where each implementation documents how the choice is made | 有號整數右移時高位元怎麼補 |
| locale-specific behavior | 3.4.2 | behavior that depends on local conventions of nationality, culture, and language that each implementation documents | `islower` 對 26 個拉丁小寫字母以外的字元是否回傳真 |
| undefined behavior | 3.4.3 | behavior, upon use of a nonportable or erroneous program construct or of erroneous data, for which this International Standard imposes no requirements | 整數溢位 |
| unspecified behavior | 3.4.4 | use of an unspecified value, or other behavior where this International Standard provides two or more possibilities and imposes no further requirements on which is chosen in any instance | 函式引數的求值順序 |

注意 3.4.3 的 NOTE：UB 的可能結果「從完全忽略、產生無法預測的結果，到以文件記載的方式運作，到終止編譯或執行」都算。換句話說，**UB 不是「會當掉」，是「標準不再對你做任何承諾」**，包括它可能看起來完全正常。

其他值得先讀的定義：3.6 byte（「可定址的資料儲存單位」，位元數由實作定義，不一定是 8）、3.8 constraint、3.14 memory location。讀第 3 章花不到半小時，卻是讀懂其餘六百頁的前提。

## Constraints vs Semantics：同一個錯，兩種下場

這是讀規格書最實用、也最常被忽略的區分。第 4 章（Conformance）第 2 段：

> If a ''shall'' or ''shall not'' requirement that appears outside of a constraint or runtime-constraint is violated, the behavior is undefined.

再配上 5.1.1.3 §1（Diagnostics）：

> A conforming implementation shall produce at least one diagnostic message (identified in an implementation-defined manner) if a preprocessing translation unit or translation unit contains a violation of any syntax rule or constraint, ... Diagnostic messages need not be produced in other circumstances.

把這兩段合起來：

```
            程式違反了某條 "shall"
                     │
         ┌───────────┴────────────┐
   寫在 Constraints 底下      寫在 Semantics 或其他地方
         │                          │
  編譯器「必須」至少給一則      未定義行為：
  診斷訊息（警告或錯誤皆可）    編譯器不必說任何話
```

**同一種錯誤**放在不同小標題底下，編譯器的義務就完全不同。用 6.5.2.2 實際驗證：呼叫函式時多傳一個引數。

```c
#include <stdio.h>

int add();                 /* C17: 參數資訊未提供；C23: 等同 int add(void) */

int main(void)
{
    printf("%d\n", add(1, 2, 3));   /* 多傳一個引數 */
    return 0;
}

int add(int a, int b) { return a + b; }
```

`int add();` 這種空括號宣告在 C17 之前**不是原型（prototype）**：6.7.6.3 §14 說，不屬於函式定義的宣告中，空的參數列表示「沒有提供參數個數與型別的資訊」。所以這個呼叫落在 6.5.2.2 §6（Semantics）：引數個數不等於參數個數，行為未定義。

```
$ gcc -std=c17 -Wall -Wextra emptyparen.c -o ep && ./ep
3
$ clang -std=c17 -Wall emptyparen.c -o epc && ./epc
3
```

開了 `-Wall -Wextra`，兩個編譯器**一聲不吭**，而且還印出看似合理的 3。這完全符合規格書：Semantics 的違反不要求診斷。現在把宣告改成原型 `int add(int a, int b);`，同樣的呼叫就落在 6.5.2.2 §2（Constraints）：

```
$ gcc -std=c17 -Wall proto.c -o proto
proto.c: In function ‘main’:
proto.c:7:20: error: too many arguments to function ‘add’
    7 |     printf("%d\n", add(1, 2, 3));   /* 多傳一個引數 */
      |                    ^~~
proto.c:3:5: note: declared here
    3 | int add(int a, int b);
      |     ^~~
$ clang -std=c17 proto.c -o protoc
proto.c:7:30: error: too many arguments to function call, expected 2, have 3
```

這就是原型存在的意義：它把一整類執行期才會爆的 UB，提升成編譯器**必須**抓的 constraint violation。gcc 其實有選項能在第一種情況提醒你：

```
$ gcc -std=c17 -Wall -Wstrict-prototypes emptyparen.c -o ep3
emptyparen.c:3:1: warning: function declaration isn’t a prototype [-Wstrict-prototypes]
```

而 C23 乾脆把 `()` 改成等同 `(void)`（見本章後面的 C23 一節），同一份程式碼在 C23 下變成編譯錯誤。

> 小結一句話：**看到 "shall" 先看它在哪個小標題底下。** Constraints 底下的，編譯器會幫你抓（至少要說一聲）；其他地方的，違反了就是 UB，沒人有義務提醒你。

## 規範與非規範：腳註、NOTE、EXAMPLE 不是法條

N1570 的 Foreword 第 8 段寫得很清楚：

> Annexes D, F, G, K, and L form a normative part of this standard; annexes A, B, C, E, H, I, J, the bibliography, and the index are for information only. In accordance with Part 2 of the ISO/IEC Directives, this foreword, the introduction, notes, footnotes, and examples are also for information only.

所以：

| 內容 | 效力 | 怎麼用 |
|---|---|---|
| 條文本體（Syntax / Constraints / Semantics） | **規範（normative）** | 爭論時的唯一依據 |
| 腳註（footnote）、NOTE、EXAMPLE | 非規範（informative） | 幫你理解條文的意圖，但不能拿來推翻條文 |
| Annex J（Portability issues） | 非規範 | UB／未指定／實作定義行為的**索引**，從這裡反查回條文 |
| Annex F（IEC 60559 浮點）、Annex K（邊界檢查函式） | 規範 | 但它們本身是條件式特徵，實作可以宣告不支援 |

腳註非規範，不代表它沒用。例如 6.5.5 §6 規定整數除法「捨去小數部分」，腳註 105 補一句 "This is often called ''truncation toward zero''"——條文已經講了，腳註幫你對上大家熟悉的說法。又例如 6.2.5 §28 規定 `void *` 和 `char *` 的表示法與對齊要求相同，腳註 48 解釋意圖是讓兩者當函式參數、回傳值與 union 成員時可以互換。讀腳註能知道委員會「為什麼這樣寫」，但引用時要引條文本體。

Annex J 特別值得一提：J.2 把散落全書的 UB 收成一張清單，每項後面標著出處條款。想知道「這樣寫是不是 UB」時，在 J.2 搜尋關鍵字，再跳回條文確認，是最快的路。

## 示範：臆測 vs 查規格

下面四個例子，每一個都是「直覺答案」與「規格書答案」不同、或直覺答案只在某些條件下成立的情況。

### 例一：`main` 能不能是 `void`？

N1570 5.1.2.2.1 §1：

> It shall be defined with a return type of int and with no parameters: `int main(void) { /* ... */ }` or with two parameters ...: `int main(int argc, char *argv[]) { /* ... */ }` or equivalent; or in some other implementation-defined manner.

關鍵在最後那句 "or in some other implementation-defined manner"：實作**可以**接受其他形式，但必須寫進文件。所以 gcc 接受 `void main` 並給警告、clang 拒絕，兩者都合規；但 `void main` 不可攜，而且 5.1.2.2.3 說回傳型別與 `int` 不相容時，交給宿主環境的結束狀態是 unspecified。C++ 更嚴格，Stroustrup 在 FAQ 說得斬釘截鐵："The definition `void main() { /* ... */ }` is not and never has been C++, nor has it even been C."

這些規定只適用於 **hosted 環境**（有作業系統）。在 **freestanding 環境**（作業系統核心、無 OS 的韌體）中，進入點的名稱與型別都由實作定義——Linux 核心就沒有 `main`。

### 例二：`main` 忘了寫 `return` 會怎樣？

同一條 5.1.2.2.3 §1 後半：

> reaching the } that terminates the main function returns a value of 0.

這句話是 **C99 才加的**。實測：

```c
#include <stdio.h>

int main(void)
{
    printf("hello, world\n");
}
```

```
$ gcc -std=c89 -O0 noreturn.c -o nr_c89 && ./nr_c89; echo "exit=$?"
hello, world
exit=13
$ gcc -std=c99 -O0 noreturn.c -o nr_c99 && ./nr_c99; echo "exit=$?"
hello, world
exit=0
```

C89 模式下結束碼是 13——剛好是 `printf` 回傳的字元數（`"hello, world\n"` 共 13 個字元），因為 `printf` 的回傳值還留在 `eax` 暫存器裡，`main` 結束時沒人覆蓋它。這是「實作恰好如此」，不是保證。同一份程式碼，標準版本不同，可觀察行為就不同；shell script 若依結束碼判斷成敗，C89 版會被當成失敗。

### 例三：浮點數轉 16 位元整數，`32768.0` 會變成什麼？

1996 年 Ariane 5 首航爆炸的直接原因，是一個 64 位元浮點數轉成 16 位元有號整數時超出範圍。原文用 C 重現時得到 `-32768`，但這是 C 的保證嗎？N1570 6.3.1.4 §1：

> When a finite value of real floating type is converted to an integer type other than _Bool, the fractional part is discarded (i.e., the value is truncated toward zero). If the value of the integral part cannot be represented by the integer type, the behavior is undefined.

UB。實測看看「標準不施加任何要求」長什麼樣子：

```c
#include <stdint.h>
#include <stdio.h>

int16_t to16(double d) { return (int16_t) d; }

int main(void)
{
    double in[] = {32767.0, 32768.0, -32769.0, 1e10};
    for (int i = 0; i < 4; i++)
        printf("%-10g -> %d\n", in[i], to16(in[i]));
    return 0;
}
```

```
$ gcc -O0 to16.c -o to16 && ./to16          # gcc -O2 結果相同
32767      -> 32767
32768      -> -32768
-32769     -> 32767
1e+10      -> 0
$ clang -O2 to16.c -o to16c && ./to16c
32767      -> 32767
32768      -> -1614143437
-32769     -> -1614143437
1e+10      -> -1614143437
```

clang `-O2` 印出的值**甚至不在 `int16_t` 的範圍內**——一個 `int16_t` 函式的回傳值竟然是 -1614143437。這不是 clang 的 bug：輸入一旦觸發 UB，編譯器就不必維持「`int16_t` 的值必在 -32768..32767」這個不變量。gcc 的結果之所以「看起來合理」，可以從組合語言看出來：

```
$ gcc -O0 -S -o - to16.c     # 節錄 to16
	movsd	%xmm0, -8(%rbp)
	movsd	-8(%rbp), %xmm0
	cvttsd2sil	%xmm0, %eax
	popq	%rbp
	ret
```

`cvttsd2si` 是 x86-64 的「截斷式浮點轉 32 位元整數」指令；`32768.0` 在 32 位元內放得下，得到 32768（`0x00008000`），取低 16 位元就是 `-32768`。`1e10` 超出 32 位元範圍，Intel 規定此時回傳「integer indefinite」`0x80000000`，低 16 位元是 0。原文另外指出，arm64 的 `fcvtzs` 會飽和為 `INT32_MAX`，所以同一份程式在 arm64 上 `1e10` 得到 `-1`（本機是 x86-64，arm64 部分**未實測**，引自原文）。

**你看到的 -32768 是 x86 指令集的性質，不是 C 語言的性質。** 要在執行期抓到這類問題，用 sanitizer：

```
$ clang -O0 -g -fsanitize=float-cast-overflow to16.c -o to16s && ./to16s
to16.c:4:33: runtime error: 32768 is outside the range of representable values of type 'short'
SUMMARY: UndefinedBehaviorSanitizer: undefined-behavior to16.c:4:33 in
```

（gcc 11 的 `-fsanitize=float-cast-overflow` 也會回報，訊息是 `type 'short int'`；同一個位置只回報第一次。）Ariane 5 的飛行軟體用 Ada 撰寫，溢位時會引發 Operand Error 例外，問題是例外沒被處理；同樣的轉換在 C 裡連例外都沒有，錯誤的數字會悄悄流進後續計算。

### 例四：四個「大家都知道」的小題目

```c
#include <stdio.h>

int main(void)
{
    char c = (char) 200;
    printf("sizeof('a')      = %zu\n", sizeof('a'));
    printf("-7 / 2, -7 %% 2   = %d, %d\n", -7 / 2, -7 % 2);
    printf("-1 >> 1          = %d\n", -1 >> 1);
    printf("(char)200 < 0    = %d\n", c < 0);
    printf("sizeof(long)     = %zu\n", sizeof(long));
    return 0;
}
```

```
$ gcc -Wall -Wextra guess.c -o guess && ./guess
sizeof('a')      = 4
-7 / 2, -7 % 2   = -3, -1
-1 >> 1          = -1
(char)200 < 0    = 1
sizeof(long)     = 8
$ gcc -funsigned-char guess.c -o guess2 && ./guess2 | grep char
(char)200 < 0    = 0
$ g++ -x c++ guess.c -o guesspp && ./guesspp | head -1
sizeof('a')      = 1
```

| 題目 | 本機結果 | 規格書怎麼說 | 可攜嗎？ |
|---|---|---|---|
| `sizeof('a')` | 4 | 6.4.4.4 §10："An integer character constant has type int." C++ 裡是 `char`，所以 g++ 印 1 | C 裡恆等於 `sizeof(int)`，可攜 |
| `-7 / 2` | -3 | 6.5.5 §6：捨去小數部分（向零截斷）；C89 時代負數除法的捨入方向由實作定義，C99 才定死 | C99 起可攜 |
| `-1 >> 1` | -1 | 6.5.7 §5："If E1 has a signed type and a negative value, the resulting value is implementation-defined." | **不可攜**，gcc 文件寫明是算術右移 |
| `char` 有沒有號 | 有號 | 6.2.5 §15：`char` 與 `signed char` 或 `unsigned char` 之一相同，由實作決定 | **不可攜**；x86-64 Linux 有號，arm64 Linux 無號 |
| `sizeof(long)` | 8 | 只規定最小範圍（5.2.4.2.1），寬度由實作決定 | **不可攜**；64 位元 Windows 是 4 |

`-funsigned-char` 這一列特別有教育意義：**同一個編譯器、同一個平台，換一個旗標，`char` 的語意就變了**，而這完全合乎規格。寫 `char c = getchar(); if (c == EOF)` 這種程式碼的人，在 arm64 上會發現迴圈永遠不結束——[Ch 35](./35-stream-io-and-signals.md) 會回來談。

## 查規格的工作流程

把上面的例子歸納成一套可重複的步驟：

```
 疑問：「這樣寫合法嗎？結果保證是多少？」
   │
   ├─ 1. 找到語法構造所屬的條款
   │     運算子 → 6.5.x；宣告 → 6.7.x；函式庫 → 7.x
   │     不知道在哪：在 PDF 搜尋關鍵字，或先查 cppreference 頁尾的
   │     "References" 段落，它列出 C11/C17/C23 的條款編號
   │
   ├─ 2. 讀 Constraints：有違反嗎？→ 編譯器應該要說話
   │
   ├─ 3. 讀 Semantics：找 "undefined" "unspecified"
   │     "implementation-defined" 這幾個字
   │
   ├─ 4. 遇到 implementation-defined → 去查編譯器文件
   │     （GCC 手冊第 4 章 "C Implementation-Defined Behavior"）
   │
   └─ 5. 用多個編譯器、多個 -std、-O0/-O2、sanitizer 驗證
         「結果不同」本身就是證據
```

實務技巧：N1570 的 HTML 版可以下載成純文字後用 `grep` 查，比在 PDF 裡翻快得多。本章的條文就是這樣核對的：

```
$ curl -sSL -o n1570.html http://port70.net/~nsz/c/c11/n1570.html
$ sed -e 's/<[^>]*>//g' n1570.html > n1570.txt
$ grep -n "An integer character constant has type int" n1570.txt
4212: An integer character constant has type int. The value of an integer character constant
```

## 一切都是表示法：object representation

原文引用一則推文把各語言的世界觀濃縮成一句：「In C, everything is a representation (`unsigned char [sizeof(TYPE)]`)」。這句不是玩笑，而是 N1570 6.2.6.1 §4 的白話版：

> Values stored in non-bit-field objects of any other object type consist of n × CHAR_BIT bits, where n is the size of an object of that type, in bytes. The value may be copied into an object of type unsigned char [n] (e.g., by memcpy); the resulting set of bytes is called the object representation of the value.

除了 bit-field，任何 object 都佔 `n` 個連續 byte，`n` 就是 `sizeof`；把這些 byte 原封不動抄進 `unsigned char[n]`，得到的序列叫 **object representation**。為什麼是 `unsigned char`？6.2.6.1 §3 與 6.2.6.2 §1 保證它是純二進位、沒有 padding bit，任何位元樣式讀出來都是合法值；6.5 §7 的 aliasing 規則也允許用字元型別存取任何 object。`memcpy`、`memcmp`、`fwrite` 能處理任意型別，依據就在這裡。

```c
#include <math.h>
#include <stdio.h>
#include <string.h>

static void dump(const char *name, const void *obj, size_t n)
{
    const unsigned char *p = obj;
    printf("%-6s:", name);
    for (size_t i = 0; i < n; i++)
        printf(" %02x", p[i]);
    printf("\n");
}

int main(void)
{
    int i = 7;
    float f = 7.0f;
    dump("int", &i, sizeof i);
    dump("float", &f, sizeof f);

    double pz = 0.0, nz = -0.0;
    dump("+0.0", &pz, sizeof pz);
    dump("-0.0", &nz, sizeof nz);
    printf("+0.0 == -0.0: %d, memcmp: %d\n", pz == nz,
           memcmp(&pz, &nz, sizeof pz) == 0);

    double x = NAN;
    printf("NaN == NaN: %d, memcmp: %d\n", x == x,
           memcmp(&x, &x, sizeof x) == 0);
    return 0;
}
```

```
int   : 07 00 00 00
float : 00 00 e0 40
+0.0  : 00 00 00 00 00 00 00 00
-0.0  : 00 00 00 00 00 00 00 80
+0.0 == -0.0: 1, memcmp: 0
NaN == NaN: 0, memcmp: 1
```

三個觀察：

1. `(int) 7` 和 `(float) 7.0` 的位元樣式毫無相似之處：`0x00000007` 對上 `0x40E00000`（因為 x86-64 是 little-endian，低位元組先存）。「它們都是 7」是**值**的層次，不是**表示法**的層次。
2. 6.2.6.1 §4 末句："Two values (other than NaNs) with the same object representation compare equal, but values that compare equal may have different object representations." `+0.0 == -0.0` 成立但 byte 不同；NaN 正好相反。
3. 推論：**不能用 `memcmp` 比較兩個 struct 是否相等**，因為 6.2.6.1 §6 規定 padding byte 的內容未指定。這會在 [Ch 16 — alignment 與 padding](./16-alignment-and-padding.md) 回來。

## C89 到 C23：每一版改了什麼

先看你的編譯器到底在用哪一版。`__STDC_VERSION__` 是標準規定的巨集（6.10.8.1）：

```c
#include <stdio.h>

int main(void)
{
#ifdef __STDC_VERSION__
    printf("__STDC_VERSION__ = %ldL\n", (long) __STDC_VERSION__);
#else
    printf("__STDC_VERSION__ undefined (C89/C90)\n");
#endif
    return 0;
}
```

```
$ for s in c89 c99 c11 c17 c2x gnu17; do printf "%-6s " $s; gcc -std=$s ver.c -o ver && ./ver; done
c89    __STDC_VERSION__ undefined (C89/C90)
c99    __STDC_VERSION__ = 199901L
c11    __STDC_VERSION__ = 201112L
c17    __STDC_VERSION__ = 201710L
c2x    __STDC_VERSION__ = 202000L
gnu17  __STDC_VERSION__ = 201710L
```

注意 `c2x` 的 `202000L`：gcc 11 時 C23 還沒定稿，它用一個暫定值。實測 GCC 14 的 `-std=c23` 也還是印 `202000`；原文說 GCC 15 起才是正式的 `202311`（本機沒有 GCC 15，**未實測**）。所以**不要用 `__STDC_VERSION__ >= 202311L` 判斷 C23 特徵是否可用**，用 `__has_include` 或功能專屬的巨集更可靠。另外，gcc 11 與 clang 14 不加 `-std` 時預設是 `gnu17`（C17 加 GNU 擴充）。

各版重點（條列的是對日常寫程式影響最大的項目，不是完整清單）：

| 版本 | 語言面 | 函式庫面 | 移除／過時 |
|---|---|---|---|
| C89/C90 | 函式原型、`const`、`volatile`、`void *` | 標準化 stdio/stdlib/string 等 | K&R 式函式定義列為過時 |
| C99 | `//` 註解、宣告與敘述混用、`for (int i…)`、`long long`、`_Bool`、`inline`、`restrict`、VLA、designated initializer、compound literal、`main` 結尾隱含 `return 0` | `<stdint.h>`、`<stdbool.h>`、`snprintf`、`%a` | 移除 implicit int、隱式函式宣告 |
| C11 | `_Generic`、`_Static_assert`、`_Alignas`、`_Noreturn`、匿名 struct/union、`_Atomic` | `<threads.h>`、`<stdatomic.h>`、`<uchar.h>`、Annex K | 移除 `gets`；VLA、複數改為條件式特徵 |
| C17 | 無新特徵，只修 DR | — | — |
| C23 | `nullptr`、`constexpr`、`auto` 型別推導、`typeof`、`[[attribute]]`、`bool`/`true`/`false`/`static_assert` 升格為關鍵字、`0b` 二進位常數、`'` 數字分隔、`= {}`、`_BitInt(N)`、列舉指定底層型別、`#embed` | `<stdckdint.h>`、`<stdbit.h>`、`memset_explicit`、`strdup`、`unreachable()` | 移除 trigraph、K&R 式函式定義；`()` 等同 `(void)`；強制二補數 |

### 舊規格的痕跡：trigraph 與六個字元

trigraph（三字元序列）是為了讓沒有 `#`、`[`、`|` 等字元的鍵盤與字元集也能寫 C：`??=` 代表 `#`、`??!` 代表 `|`。它在 C23 被移除，但 gcc 11 在嚴格 ISO 模式下仍會處理它：

```c
#include <stdio.h>

int main(void)
{
    printf("What??!\n");
    return 0;
}
```

```
$ gcc -std=c11 tri.c -o tri && ./tri
What|
$ gcc -std=gnu11 -Wall tri.c -o tri && ./tri
tri.c:5:17: warning: trigraph ??! ignored, use -trigraphs to enable [-Wtrigraphs]
What??!
```

一個字串常值的內容，竟然隨 `-std=c11` 與 `-std=gnu11` 而不同。這就是原文說的「規格不能只看新的，過往也要熟悉」。

另一個痕跡藏在函式名稱裡。`strcpy`、`strlen` 為什麼這麼短？C89 的 translation limits 只保證**外部識別字的前 6 個字元**有意義，而且可以不分大小寫，因為當年許多連結器只認得這麼多。C99 才把下限提高到 31 個字元（N1570 5.2.4.1："31 significant initial characters in an external identifier"）。Stroustrup 在《The Design and Evolution of C++》3.3.3 節提到，C++ 必須把類別名稱與參數型別編進外部名稱（name mangling），在只保證 6 個大寫字元的連結器上幾乎不可能實作。

## 改版的取捨：委員會在想什麼

WG14 為 C23 擬定的章程 [N2611](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n2611.htm) 列出 15 條歷次累積下來的原則，並直言原則之間有衝突，偏離越多需要越多理由。歸納成四條主軸：

**1. 相容性：既有程式碼比既有編譯器重要。** 自 C89 沿用至今的原則是 "Existing code is important, existing implementations are not."——委員會寧可要求所有編譯器改，也不願讓程式設計者改程式。並列的原則是避免 **quiet change**：讓合法程式在沒有任何診斷的情況下改變語意。新關鍵字的拼法是最好的例證：`_Bool`、`_Complex`、`_Static_assert` 都用「底線加大寫字母」開頭，因為這類名稱本來就保留給實作（7.1.3），不可能撞到使用者的變數名；好讀的 `bool` 交給 `<stdbool.h>` 用巨集提供。一直到 C23，`bool` 才升格為真正的關鍵字。移除特徵更需要耐心：K&R 式函式定義在 C89 就列為過時，到 C23 才真正移除，前後超過三十年。

**2. 只標準化既有實務。** C11 的章程甚至寫「不發明，無一例外」。C23 的 `typeof`、`__has_include`、`[[...]]` attribute、二進位常數，都是 GCC、Clang 或 C++ 行之有年的作法。反面教材是 C11 的 **Annex K**（`strcpy_s` 等邊界檢查函式）：它源自 Microsoft 推動的技術報告，納入標準後幾乎沒有主流 libc 實作，2015 年的 [N1967](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1967.htm) 回顧後建議移除——即便如此，它至今仍在 C23 裡。

**3. 標準是雙方都要能履行的契約。** 不應強迫小型嵌入式平台的廠商實作用不到的大特徵。所以 C11 把 C99 必備的 VLA 與複數改成條件式特徵，用 `__STDC_NO_VLA__`、`__STDC_NO_COMPLEX__`、`__STDC_NO_THREADS__`、`__STDC_NO_ATOMICS__` 宣告不支援。MSVC 直到 2020 年才正式支援 C11/C17，而且至今不支援 VLA。ABI 也會綁住設計：[N2465](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n2465.pdf) 指出各平台已把 `intmax_t` 的寬度寫進 ABI，加寬它會讓 `printf` 家族與既有二進位不相容，所以 C23 選擇讓 `_BitInt(N)` 超出 `intmax_t` 的範圍，而不是加寬 `intmax_t`。

**4. 效能與安全的拉鋸。** "Trust the programmer" 是 C 的傳統精神，但 C11 章程已承認，對資安與功能安全社群而言這個目標已經過時。結果往往是折衷：C11 移除 `gets`（極少數直接刪掉標準函式的案例）；C23 強制有號整數用二補數表示，但提案 [N2412](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n2412.pdf) 明言對「溢位改成環繞」沒有共識，所以**有號溢位仍是 UB**，編譯器仍可據此最佳化迴圈；要檢查溢位就用 C23 的 `<stdckdint.h>`。

這些取捨的代價見於真實事故。波音 787 的發電機控制單元連續供電 248 天後全數進入 fail-safe 模式，Phil Koopman 推算是每 1/100 秒遞增的 32 位元有號計數器溢位（2³¹ / 100 / 86400 ≈ 248.5 天）。汽車等安全關鍵領域因此另訂 MISRA C 這類子集，以禁止或限制涉及 UB 與實作定義行為的寫法，補上標準不提供的保證。

## C23 實際長什麼樣子

gcc 11 不支援大部分 C23 特徵，以下用 Docker 的 `gcc:14` 映像執行（`docker run --rm -v "$PWD:/w" gcc:14 ...`）：

```c
#include <stdbit.h>
#include <stdckdint.h>
#include <stddef.h>
#include <stdio.h>

#define container_of(ptr, type, member) \
    ((type *) ((char *) (ptr) - offsetof(type, member)))

struct node {
    int key;
    struct node *next;
};

enum color : unsigned char { RED, GREEN, BLUE };

[[nodiscard]] static int add(int a, int b, bool *overflow)
{
    int r;
    *overflow = ckd_add(&r, a, b);
    return r;
}

int main(void)
{
    constexpr int N = 4;
    int buf[N] = {};             /* 空大括號初始化 */
    auto mask = 0b1111'0000u;    /* 二進位常數與數字分隔符號 */
    typeof(mask) copy = mask;

    struct node n = {.key = 42};
    struct node *p = container_of(&n.key, struct node, key);
    static_assert(sizeof(enum color) == 1);

    bool ovf;
    int s = add(2147483647, 1, &ovf);
    printf("%d %d %#b %u %d\n", buf[0], p->key, copy,
           stdc_count_ones_ui(copy), ovf);
    printf("s = %d, __STDC_VERSION__ = %ld\n", s, __STDC_VERSION__);
}
```

```
$ gcc -std=c23 -Wall -Wextra -pedantic c23.c -o c23 && ./c23
0 42 0b11110000 4 1
s = -2147483648, __STDC_VERSION__ = 202000
```

逐項對照：`bool` 不必引入 `<stdbool.h>`；`static_assert` 可省略訊息；`ckd_add` 回傳 `true` 表示溢位，此時 `r` 存的是以二補數環繞後的值，**整個過程沒有觸發 UB**；`%#b` 是 C23 的 `printf` 新格式（需要 glibc 2.35 以上，這個映像是 2.41）；`stdc_count_ones_ui` 來自 `<stdbit.h>`（glibc 2.39 以上）。把 `int s = add(...)` 改成只寫 `add(...);`，`[[nodiscard]]` 就會發威：

```
c23b.c:34:5: warning: ignoring return value of 'add', declared with attribute 'nodiscard' [-Wunused-result]
```

前面的 `emptyparen.c` 在 C23 下則直接編譯失敗，因為 `int add();` 現在等於 `int add(void);`：

```
$ gcc -std=c23 -Wall emptyparen.c -o ep
emptyparen.c:7:20: error: too many arguments to function 'add'
emptyparen.c:11:5: error: conflicting types for 'add'; have 'int(int,  int)'
emptyparen.c:3:5: note: previous declaration of 'add' with type 'int(void)'
```

這是 C23 少數「刻意打破舊程式」的改動之一：委員會判斷，空括號宣告在實務上幾乎總是「忘了寫 `void`」，把它變成原型能抓到的錯誤遠多於它弄壞的程式。

## 下一版：C2y 的方向

WG14 已著手下一版，暫稱 C2y。依原文整理（截至本課撰寫時的狀態，可能仍會變動）：已表決進入工作草案的包含 `_Countof` 運算子與 `<stdcountof.h>` 的 `countof` 巨集（取代 `sizeof(a) / sizeof(a[0])`）、具名迴圈（named loops，讓 `break`/`continue` 指定目標）、類似 C++17 的 `if` 敘述內宣告；`defer` 則以技術規格（Technical Specification，TS）的形式先行發展。

具名迴圈要解決的問題可以從一場真實事故看出來。1990 年 1 月 15 日，AT&T 長途電話網癱瘓約 9 小時，根源是一處 `break`：N1570 6.8.6.3 §2 規定 "A break statement terminates execution of the smallest enclosing switch or iteration statement."——`if` 不在其列。工程師在 `switch` 裡的 `if` 內寫 `break`，以為會跳出 `if`，實際上跳出整個 `switch`，略過了設定指標的程式碼。條文只有一句話，讀懂它的代價卻是 9 小時的全國斷線。

## 語言設計者的評論，以及本課為何不談 C++

Dennis Ritchie 在 HOPL-II（1993）的論文〈The Development of the C Language〉這樣總結：

> C is quirky, flawed, and an enormous success. Although accidents of history surely helped, it evidently satisfied a need for a system implementation language efficient enough to displace assembly language, yet sufficiently abstract and fluent to describe algorithms and interactions in a wide variety of environments.

「彆扭、有缺陷、卻異常成功」——這三個詞的來源，就是 [Ch 6](./06-history-of-c.md) 要講的歷史。順帶一提，K&R 的 "K" 是 Brian Kernighan，不是 Ken Thompson。

本課不談 C++，理由有三：

1. **C++ 已是另一個語言**。本章的例四就有一題答案不同：`sizeof('a')` 在 C 是 4，在 C++ 是 1。Stroustrup 自己在〈Learning Standard C++ as a New Language〉也主張別把 C++ 當成「較好的 C」來學。
2. **C++ 不保證抽象的執行期成本一目了然**，而本課的目標正是看穿每一行 C 變成什麼機械碼。
3. **C++ 改版節奏快得多**（C++11 起三年一版），兩邊的規格書各自有一千頁以上；混著讀只會讓兩邊都讀不清。

## 對比與取捨：遇到疑問該查哪裡

| 資料來源 | 優點 | 缺點 | 適合 |
|---|---|---|---|
| 規格書草案（N1570、N3220） | 唯一權威；條文精確 | 難讀，沒有範例與動機說明 | 最終仲裁、寫教材、code review 爭論 |
| [cppreference](https://en.cppreference.com/w/c) | 好讀，每頁附條款編號與版本差異 | 是整理不是原文，偶有簡化 | 日常查詢的第一站，再跳回條文 |
| 編譯器手冊（GCC 第 4 章） | 唯一能回答「實作定義行為選了什麼」的地方 | 只對那個編譯器有效 | implementation-defined 的細節 |
| C99 Rationale | 解釋「為什麼這樣規定」 | 只到 C99 | 理解設計動機 |
| K&R 第二版 | 語言設計者親筆，第 5 章仍經典 | 停在 C89 | 建立基本心智模型 |
| Stack Overflow / 部落格 | 有具體案例 | 品質參差，常把「我的編譯器這樣」當規則 | 找線索，不當證據 |
| 「我跑過了」 | 快 | 只證明某編譯器某版本某旗標的結果 | 驗證，不是依據 |

## 踩雷集錦

1. **「編譯器沒警告就是合法」**：上面的 `emptyparen.c` 在 `-Wall -Wextra` 下零警告，卻是 UB。只有 constraint violation 才保證有診斷；Semantics 裡的 UB 編譯器沒有義務說任何話。
2. **「跑出來的值就是答案」**：`to16(32768.0)` 在 gcc 得到 -32768，在 clang `-O2` 得到一個根本不是 16 位元的數字。UB 的輸出不是「某個值」，是「沒有承諾」。
3. **「腳註也是規定」**：腳註、NOTE、EXAMPLE 依 Foreword §8 都是非規範內容。拿腳註跟人爭「標準規定 X」，對方翻出條文本體你就輸了；反過來，腳註是理解委員會意圖的好材料。
4. **「C 就是我的 gcc」**：`char` 的有無號、`long` 的寬度、`-1 >> 1` 的結果都是實作定義，換到 arm64 或 Windows 就不一樣，連 `-funsigned-char` 一個旗標都能改變語意。
5. **「C23 已經能用了」**：標準定稿不等於工具鏈就緒。gcc 11 的 `-std=c2x` 只支援部分特徵，GCC 14 的 `__STDC_VERSION__` 也還是暫定值；`<stdbit.h>` 需要 glibc 2.39。用 `-std=` 明確指定版本，用 `__has_include` 檢查實際能力。

## 進階：再往深一層

- **缺陷報告（DR）**：規格書也會錯。WG14 的 DR 清單記錄了歷年對條文的修正與解釋，C17 整版就是 DR 的合集。讀到條文自相矛盾時，先查有沒有對應的 DR。
- **讀提案（N paper）**：想知道某個 C23 特徵為什麼長這樣，去 [WG14 文件清單](https://www.open-std.org/jtc1/sc22/wg14/www/wg14_document_log) 找原始提案。例如二進位常數是 N2549、二補數是 N2412。提案通常有「動機」「既有實作」「設計選項」三段，正好回答本章一直在問的「為什麼不選 B」。
- **把規格書當測試規格**：GCC 與 Clang 的測試套件裡有大量以條款編號命名的測試。讀 clang 原始碼時搜尋 `C11 6.5.2.2` 這類字串，能找到實作該條文的位置。
- **面試常見題**：「`i = i++ + 1;` 的結果是多少？」標準答案不是數字，是「6.5 §2 規定這是 UB，所以問題本身沒有答案」。能說出條款編號，是這類題目的加分點。

## 動手練習

1. 下載 N1570，找出下列三件事各自在哪個條款、是 constraint 還是 semantics、違反時的後果：(a) 對同一個 object 在兩個序列點之間修改兩次；(b) 對 `void *` 做指標算術；(c) `switch` 裡出現兩個相同值的 `case`。用 gcc 與 clang 各編一次，對照編譯器是否給診斷。
2. 在 Annex J.2 搜尋 "shift"，列出所有與位移有關的 UB，回到 6.5.7 讀原文，各寫一行會觸發它的程式，用 `-fsanitize=undefined` 跑。
3. 寫一個程式印出 `CHAR_BIT`、`CHAR_MIN`、`sizeof(long)`、`sizeof(void *)`、`-1 >> 1`、`(-7) % 3`，在 gcc 加與不加 `-funsigned-char` 各跑一次；把結果填進一張表，每格註明「標準保證」或「實作定義」與條款編號。
4. 把本章的 `noreturn.c` 用 `-std=c89 -O2` 再編一次，結束碼還是 13 嗎？用 `gcc -S` 看組合語言解釋原因。

## 本章重點整理

- 規格書是程式設計者與編譯器實作者之間的契約；免費草案（N1570、N3220）足夠日常使用。
- 第 3 章是字典：object、byte、四種行為（implementation-defined / locale-specific / undefined / unspecified）都是有精確定義的術語。
- 違反 Constraints → 編譯器必須診斷；違反其他地方的 "shall" → UB，沒人需要提醒你。
- 腳註、NOTE、EXAMPLE、Annex J 是非規範內容，用來理解與索引，不能拿來推翻條文。
- 改版的取捨圍繞四條主軸：相容性、只收既有實務、雙方可履行、效能與安全；每次改版都在它們之間找平衡。

## 自我檢核

- [ ] 不看筆記，能說出 implementation-defined 與 unspecified 的差別，並各舉一個例子嗎？
- [ ] 有人說「這樣寫 gcc 有警告但能跑，所以沒問題」，你能用 constraints vs semantics 的框架反駁或支持他嗎？
- [ ] 能解釋為什麼 `to16(32768.0)` 在 x86-64 的 gcc 上得到 -32768，以及為什麼這不能當作 C 的保證嗎？
- [ ] 面試官問「`void main` 合法嗎？」，你能在 30 秒內給出包含 hosted/freestanding 與 implementation-defined 的完整答案嗎？
- [ ] 說得出 C23 的 `()` 改成 `(void)` 會讓哪一類舊程式碼編不過、為什麼委員會仍然這樣改嗎？
- [ ] 想一個你寫過、依賴 `char` 有號或 `long` 為 8 byte 的程式碼，它在哪個平台上會壞？

## 延伸閱讀

### 官方文件 / Spec

- **[N1570：ISO/IEC 9899:201x Committee Draft](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**（[HTML 版](http://port70.net/~nsz/c/c11/n1570.html)）
  - **讀哪裡**：第 3 章全部（半小時）、第 4 章（一頁）、5.1.2.2 的 `main`、6.2.6 表示法、Annex J.2 的 UB 清單；之後依需要查閱，不必通讀
  - **能學到什麼**：本章所有條文的原文與上下文
  - **前提**：會基本 C 語法；英文閱讀能力，條文用字有限，讀幾段就會習慣
- **[N2611：C23 Charter](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n2611.htm)** — WG14
  - **讀哪裡**：全文不長，15 條原則逐條讀
  - **能學到什麼**：委員會的決策框架，本章「改版的取捨」一節的一手來源
  - **前提**：讀完本章即可
- **[Rationale for International Standard — Programming Languages — C (C99, V5.10)](https://www.open-std.org/jtc1/sc22/wg14/www/C99RationaleV5.10.pdf)**
  - **讀哪裡**：0 Introduction 的 "Spirit of C" 段落；6.7.5.3（原型）；搜尋 "QUIET CHANGE" 看委員會自己標出的語意變動
  - **能學到什麼**：條文背後的動機，規格書本體刻意不寫的「為什麼」
  - **前提**：手邊有 N1256 或 N1570 對照
- **[GCC Manual, Chapter 4: C Implementation-Defined Behavior](https://gcc.gnu.org/onlinedocs/gcc/C-Implementation.html)**
  - **讀哪裡**：4.5 Integers（右移、轉換）與 4.4 Characters（`char` 的有無號）
  - **能學到什麼**：規格書說「實作定義」時，gcc 實際選了什麼，並且是寫進文件的承諾
  - **前提**：知道 implementation-defined 的定義（本章）

### 部落格 / 技術文章

- **[C23 is Finished: Here is What is on the Menu](https://thephd.dev/c23-is-coming-here-is-what-is-on-the-menu)** — JeanHeyd Meneide（2022）
  - **這篇說什麼**：C23 主要特徵的導覽，作者是 WG14 成員、多項 C23 提案（含 `#embed`）的作者
  - **讀哪裡**：整篇；每個特徵都附提案編號，可以順著讀原始提案
  - **為什麼值得讀**：從提案者角度說明特徵的取捨與委員會內的爭論，這是規格書本體看不到的
- **[A cheatsheet of modern C language and library features](https://github.com/AnthonyCalandra/modern-c-features)** — Anthony Calandra
  - **這篇說什麼**：C99 到 C23 各特徵一行一例的速查表
  - **讀哪裡**：當作 C89→C23 表格的補充，看每個特徵的最小範例
  - **為什麼值得讀**：比規格書快 100 倍找到「這個特徵長什麼樣」，再回條文查細節

### 書籍

- **《Modern C》** — Jens Gustedt（Manning；新版已涵蓋 C23）
  - **這本書的定位**：作者是 WG14 成員，全書以現代標準寫 C，並大量引用規格書概念
  - **讀哪幾章**：Level 1 的前幾章建立「object、值、表示法」的觀念，與本章及 Ch 7 互補
- **《The C Programming Language》（2nd ed.）** — Kernighan & Ritchie（1988）
  - **這本書的定位**：附錄 A 是 C89 的參考手冊，篇幅只有規格書的零頭，是讀規格書前的好暖身
  - **讀哪幾章**：附錄 A；搭配本章的版本表看哪些東西 C99 之後改了

有了查規格的能力，下一步是查「程式實際上做了什麼」：規格書告訴你承諾，除錯器讓你看見現實。

→ [Ch 5 GDB 實戰：不靠 printf 觀察程式狀態](./05-gdb-in-practice.md)
