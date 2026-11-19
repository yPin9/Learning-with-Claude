# Ch 27 — 前置處理器 I：巨集陷阱、_Generic 與 statement expression

> **目標**：能說清楚前置處理器在翻譯階段（translation phases）中的位置、巨集展開的精確規則（引數預先展開、`#` 與 `##`、重新掃描與 painted blue），並能親手寫出沒有括號陷阱、不重複求值、拒絕指標的巨集；最後用 `_Generic` 做出依型別分派的「C 版 template」。

> **對應原文**：[你所不知道的 C 語言：前置處理器應用篇](https://hackmd.io/@sysprog/c-preprocessor) 的「不要小看前置處理器」「前置處理器是後來才納入 C 語言的特徵」「_Generic」「避免重複求值」「不夠謹慎的 ARRAY_SIZE 巨集」「do { ... } while (0) 巨集」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14；C23 段落以 Docker `gcc:15`（GCC 15.3）執行，章節內標明。

## 為什麼需要這個？

很多人把巨集當成「比較快的函式」，寫出 `#define SQUARE(x) x*x` 之後，某天 `SQUARE(a+1)` 算出 7 而不是 16，花一個下午才找到原因。問題的根源只有一句話：**前置處理器不懂 C，它只懂語彙單元（token）**。它不知道什麼是運算子優先序、什麼是型別、什麼是副作用；它照著一套和 C 語法無關的規則，把一串 token 換成另一串 token，然後把結果交給真正的編譯器。

這套規則不是後來才補上的瑕疵，而是歷史造成的。C 語言最早（1972 年前後的 last1120c 編譯器）根本沒有 `#include` 與 `#define`，Dennis Ritchie 在〈Primeval C〉中直說 "the preprocessor didn't exist at the time"。依他在〈The Development of the C Language〉的記述，前置處理器在 1972–73 年間加入，部分是受 Alan Snyder 的催促，部分是參考 BCPL 與 PL/I 的檔案引入機制；最初只有 `#include` 與無參數的 `#define`，之後 Mike Lesk 與 John Reiser 才加上帶參數的巨集與條件編譯。Ritchie 也承認，前置處理器起初被當成語言之外的「選用附件」（an optional adjunct），有好幾年檔案開頭沒有特殊標記它甚至不會啟動。這解釋了為什麼它的語法和 C 本體格格不入：它本來就是另一個工具。

懂這一層之後，巨集的各種陷阱都會變成可以推導的結果，而不是要死背的禁忌清單。這章把規則講清楚，下一章（Ch 28）再看這套規則被推到極限時能做出什麼。

## 先建立直覺

把編譯想成一條產線。前置處理器站在最前面，手上拿的不是 C 程式，而是一長串「積木」（preprocessing token）。它的工作只有三件：

```
原始檔 (.c)
   │  phase 1-3：字元對應、反斜線接行、切成 pp-token、註解變一個空白
   ▼
┌──────────────────────────────────────────┐
│  phase 4：前置處理器                       │
│   ・執行 #include / #if / #define ...      │
│   ・看到巨集名稱 → 換成替換清單 → 再掃一次  │
│   ・刪掉所有指引                           │
└──────────────────────────────────────────┘
   │  一串「沒有巨集、沒有指引」的 pp-token（gcc -E 的輸出）
   ▼
phase 5-6：字元常數轉碼、相鄰字串字面值串接
   ▼
phase 7：pp-token 轉成 token，真正的語法與語意分析（編譯器本體）
   ▼
phase 8：連結
```

關鍵心智模型：**巨集展開發生在「語法分析」之前**。所以巨集看不到括號的語意、看不到型別、看不到變數是否已宣告。`SQUARE(a+1)` 變成 `a+1*a+1` 時，編譯器才第一次看到它，並且忠實地依優先序算出 `a + a + 1`。

> 如果你對編譯流程的四個階段（前置處理、編譯、組譯、連結）還不熟，先回看 [Ch 1 — 一支 C 程式的一生](./01-life-of-a-c-program.md)。

## 翻譯階段：規格書怎麼說

C11 草案 N1570 的 5.1.1.2 把翻譯定義成 8 個階段（C23 的 N3220 同條），並在腳註 6 強調 "Implementations shall behave as if these separate phases occur"：實作可以把階段合併，但結果必須和逐階段執行一樣。和前置處理器有關的是前 4 個：

| 階段 | 做什麼 | 你會在哪裡踩到 |
|---|---|---|
| 1 | 實體字元對應到來源字元集；trigraph（`??=` 等三字元序列）換成單一字元 | 字串裡寫 `"??!"`，在 `-trigraphs` 下變成 `"|"` |
| 2 | **反斜線緊接換行**的組合被刪除，實體行接成邏輯行 | `//` 註解行尾的 `\` 會把下一行也吃進註解 |
| 3 | 切成 pp-token 與空白；**每個註解換成一個空白** | `int/**/main` 是 `int main`，不是 `intmain` |
| 4 | 執行指引、展開巨集；`#include` 的檔案遞迴跑 1–4 階段 | 本章的全部內容 |

第 2、3 階段的效果可以實驗。下面這支程式故意在 `//` 註解結尾放一個反斜線：

```c
#include <stdio.h>
#define GREETING "hello, " \
                 "world"
/* comment becomes one space */int/**/main(void)
{
    // line splice: this comment continues \
    printf("you will never see me\n");
    printf("%s\n", GREETING);
    return 0;
}
```

```
$ gcc -Wall -Wextra phases.c -o phases
phases.c: In function ‘main’:
phases.c:6:5: warning: multi-line comment [-Wcomment]
    6 |     // line splice: this comment continues \
      |     ^
$ ./phases
hello, world
```

第一個 `printf` 消失了：第 2 階段先把兩行接起來，第 3 階段才辨認註解，於是整行 `printf` 都在註解裡。`-Wall` 會以 `-Wcomment` 提醒你，這也是「一定要開 `-Wall`」的理由之一。

用 `gcc -E -P` 看第 4 階段的輸出（`-P` 省略行號標記）：

```
$ gcc -E -P phases.c | tail -5
                               int main(void)
{
    printf("%s\n", "hello, " "world");
    return 0;
}
```

注意兩件事。第一，`GREETING` 展開後仍是**兩個**字串字面值 `"hello, " "world"`，相鄰字串的串接發生在第 6 階段，前置處理器不負責。第二，`int/**/main` 中的註解變成了空白。這份輸出有 748 行（`gcc -E phases.c | wc -l`），幾乎都來自 `<stdio.h>`，這就是 `#include` 的本質：**純文字貼上**。

去掉 `-P` 會看到 `# 1 "phases.c"` 這類行號標記（linemarker），格式是 `# 行號 "檔名" 旗標`。它讓後續階段的錯誤訊息能指回原始檔的正確行號；旗標 `1` 表示進入新檔案、`2` 表示回到上層檔案、`3` 表示系統標頭（警告會被壓抑）。

### 前置處理器還是獨立程式嗎？

傳統上 `cpp` 是獨立工具，可以拿來處理非 C 的文字（舊的 imake 就這樣做）。現代 GCC 已經把它整合進編譯器本體。以 `gcc -v -E` 觀察 driver 實際呼叫的程式：

```
$ gcc -v -E phases.c -o /dev/null 2>&1 | grep cc1
 /usr/lib/gcc/x86_64-linux-gnu/11/cc1 -E -quiet -v -imultiarch x86_64-linux-gnu phases.c -o /dev/null ...
```

執行的是 `cc1 -E`，也就是編譯器本體在第 4 階段後停下。一般編譯時，前置處理與編譯在同一個 `cc1` 行程內完成，不會產生中間檔，除非你加 `-save-temps`。

### 巨集名稱必須是識別字

原文舉了一個整人手法：把分號換成外觀相同的希臘問號 U+037E。想用 `-D` 把它「定義回」分號是不可能的，因為巨集名稱必須是識別字（identifier），而 `;` 是標點：

```
$ gcc -D";"=";" -E phases.c -o /dev/null
<command-line>: error: macro names must be identifiers
```

這是本章第一個要內化的原則：**前置處理器的操作單位是 pp-token**，名稱、數字、字串、標點各是一種 token，巨集只能以識別字命名。

## 巨集展開的精確規則

物件式巨集（object-like，`#define N 10`）與函式式巨集（function-like，`#define F(x) ...`）的差別在名稱後是否**緊接** `(`。`#define F (x)` 是物件式巨集，替換清單是 `(x)`。

函式式巨集被呼叫時，N1570 6.10.3.1 到 6.10.3.4 規定了這個順序：

```
F(arg1, arg2) 被辨識
   │
   ▼
① 切出引數（只認最外層的小括號與逗號；大括號、中括號不算！）
   │
   ▼
② 對每個參數出現處：
     ├─ 前面是 #，或前後是 ##  → 用「原封不動」的引數 token
     └─ 否則                   → 先把引數「完整展開」再代入  (6.10.3.1 §1)
   │
   ▼
③ 執行 #（字串化）與 ##（串接）              (6.10.3.2, 6.10.3.3)
   │
   ▼
④ 把結果連同檔案後續 token 重新掃描           (6.10.3.4 §1)
   掃描時遇到「正在展開中的巨集名稱」→ 不展開，並且永遠不再展開
   （社群稱為 painted blue）                   (6.10.3.4 §2)
```

### `#`：字串化

`#param` 把引數的 token 序列變成字串字面值。6.10.3.2 §2 規定：引數內部的空白一律變成一個空格、首尾空白刪除，字串與字元常數中的 `"` 和 `\` 前面會補上 `\`。

```c
#include <stdio.h>

#define STR(x)      #x
#define XSTR(x)     STR(x)
#define CAT(a, b)   a##b
#define XCAT(a, b)  CAT(a, b)
#define VERSION     3
#define CHECK(expr) printf("%-22s -> %s\n", #expr, (expr) ? "ok" : "FAIL")

int var3 = 33;

int main(void)
{
    puts(STR(VERSION));          /* "VERSION" */
    puts(XSTR(VERSION));         /* "3" */
    puts(STR(  a   +    "b\n" )); /* whitespace normalized, quote escaped */
    printf("%d\n", XCAT(var, VERSION)); /* var3 */
    CHECK(1 + 1 == 2);
    CHECK(sizeof(int) == 8);
    return 0;
}
```

```
$ gcc -Wall -Wextra hash.c -o hash && ./hash
VERSION
3
a + "b\n"
33
1 + 1 == 2             -> ok
sizeof(int) == 8       -> FAIL
```

`STR(VERSION)` 得到 `"VERSION"`，因為參數 `x` 前面有 `#`，引數**不先展開**（規則 ②）。多包一層的 `XSTR(VERSION)` 中，`XSTR` 的替換清單 `STR(x)` 裡 `x` 不與 `#` 相鄰，所以 `VERSION` 先展開成 `3`，再交給 `STR`。這個「兩層間接」是整個前置處理器技巧的基本功，Ch 28 的 `IS_ENABLED`、Zephyr 的 devicetree 巨集都靠它。

`CHECK` 示範 `#` 最常見的實際用途：把條件式原文印出來。標準函式庫的 `assert` 失敗時能印出 `Assertion 'p != NULL' failed`，靠的就是同一招。

### `##`：語彙單元串接

`a##b` 把左右兩個 token 黏成一個新 token。`gcc -E -P` 的結果：

```
    printf("%d\n", var3);
```

串接結果必須是**合法的 pp-token**，否則是未定義行為（6.10.3.3 §3），GCC 直接報錯：

```
$ cat bad.c
#define CAT(a,b) a##b
CAT(var, VERSION)
CAT(x, +)
$ gcc -E -P bad.c
bad.c:3:5: error: pasting "x" and "+" does not give a valid preprocessing token
varVERSION
x +
```

第二行同時示範了 `##` 抑制引數展開：`CAT(var, VERSION)` 得到 `varVERSION`，不是 `var3`。要先展開就得經過 `XCAT`。

`##` 最典型的用途是**產生程式碼**。原文舉 raytracing 專案的 `DECLARE_OBJECT(name)`，一次為 `light`、`sphere` 等型別各產生串列節點型別與 `append_light()`、`delete_light_list()` 宣告。這是 C 模擬泛型最原始的手法，Ch 28 會把它發展成完整的 ADT。

### 重新掃描與 painted blue

規則 ④ 防止巨集無窮遞迴。下面這份輸入取材自 GCC 手冊「Self-Referential Macros」與「Argument Prescan」兩節：

```c
#define x (4 + y)
#define y (2 * x)
#define foo foo + 1
#define max(a, b) ((a) > (b) ? (a) : (b))
#define EMPTY
#define LPAREN (
A: x
B: y
C: foo
D: max(1, 2) | max | (max)(1, 2)
E: max EMPTY (1, 2)
F: max LPAREN 1, 2)
```

```
$ gcc -E -P blue.c
A: (4 + (2 * x))
B: (2 * (4 + y))
C: foo + 1
D: ((1) > (2) ? (1) : (2)) | max | (max)(1, 2)
E: max (1, 2)
F: max ( 1, 2)
```

逐行解讀：

- **A**：`x` → `(4 + y)` → 重掃時 `y` → `(2 * x)`；此時 `x` 仍在展開中，不再展開。
- **C**：`foo` 自我參照只展開一層。原文 `cbrt` 範例的巨集與函式同名卻不會無窮展開，就是這條規則。
- **D**：函式式巨集名稱後面沒有 `(` 時不展開。`(max)(1, 2)` 這種寫法刻意繞過同名巨集、呼叫真正的函式，Tony Finch 的 parametric constness 技巧與 glibc 都用它。
- **E**：`max EMPTY (1, 2)` 掃到 `max` 時下一個 token 是 `EMPTY` 不是 `(`，於是 `max` 被放過；等 `EMPTY` 展開成空，掃描已經走過 `max` 了。
- **F**：`LPAREN` 展開出的 `(` 同樣來不及讓 `max` 被辨識。

E 與 F 看起來是怪癖，其實是 Ch 28「前置處理器元程式設計」的核心：刻意讓「名稱」與「括號」在本輪掃描中不相鄰，延到下一輪再組合，就能繞過 painted blue。

> **標準的灰色地帶**：N1570 6.10.3.4 §4 的 EXAMPLE 承認，`#define f(a) a*g` 與 `#define g(a) f(a)` 之下的 `f(2)(9)` 可展開成 `2*f(9)` 或 `2*9*g`，屬於未指定行為。實務上不要寫出依賴這種邊界的巨集。

## 陷阱一：括號

巨集參數代入時是 token 原樣貼上，所以參數與整體都要加括號：

```c
#include <stdio.h>

#define SQUARE_BAD(x)  x * x
#define SQUARE_MID(x)  (x) * (x)
#define SQUARE(x)      ((x) * (x))
#define NEG_BAD(x)     -x

int main(void)
{
    int a = 3;
    printf("SQUARE_BAD(a + 1) = %d\n", SQUARE_BAD(a + 1));   /* a + 1 * a + 1 */
    printf("100 / SQUARE_MID(a) = %d\n", 100 / SQUARE_MID(a)); /* 100 / (a) * (a) */
    printf("100 / SQUARE(a) = %d\n", 100 / SQUARE(a));
    printf("NEG_BAD(-a) = %d\n", NEG_BAD(-a));
    return 0;
}
```

```
$ ./paren
SQUARE_BAD(a + 1) = 7
100 / SQUARE_MID(a) = 99
100 / SQUARE(a) = 11
NEG_BAD(-a) = 3
```

- `SQUARE_BAD(a + 1)` 展開為 `a + 1 * a + 1` = 3 + 3 + 1 = 7。**參數**沒加括號。
- `100 / SQUARE_MID(a)` 展開為 `100 / (a) * (a)` = (100 / 3) * 3 = 33 * 3 = 99。**整體**沒加括號，`/` 與 `*` 同優先序、由左往右結合。
- `NEG_BAD(-a)` 很多人預期會變成 `--a`（前置遞減）並且改到 `a`。實際輸出 3，`gcc -E` 顯示展開結果是 `- -a`：兩個 `-` 是兩個獨立的 token，前置處理器**不會把它們重新黏成 `--`**。GCC 在 `-E` 輸出時還會主動插入空白，避免文字重新切割時改變語意。這再次證明巨集是 token 替換，不是文字替換。

規則：**每個參數出現處加括號，整個替換清單外層再加一層括號**。例外是參數被當成型別或成員名稱使用時（例如 `offsetof(type, member)` 的 `member`），加括號反而會變成語法錯誤，這時只能靠文件約束。

## 陷阱二：重複求值

括號解決不了這個問題：參數在替換清單中出現兩次，引數就被求值兩次。

```c
#include <stdio.h>

#define MAX_BAD(a, b) ((a) > (b) ? (a) : (b))

#define MAX(a, b) ({                 \
    __typeof__(a) _max_a = (a);      \
    __typeof__(b) _max_b = (b);      \
    _max_a > _max_b ? _max_a : _max_b; \
})

/* naive local name: collides with caller's variable */
#define DOUBLE_SHADOW(a) ({ __typeof__(a) _x = (a); _x + _x; })

static int calls;
static int next(void) { return ++calls * 10; }

int main(void)
{
    int i = 5, j = 3;
    int m = MAX_BAD(i++, j);
    printf("MAX_BAD: m=%d i=%d\n", m, i);

    i = 5;
    m = MAX(i++, j);
    printf("MAX    : m=%d i=%d\n", m, i);

    calls = 0;
    m = MAX_BAD(next(), 5);
    printf("MAX_BAD(next(), 5) = %d, next() called %d times\n", m, calls);
    calls = 0;
    m = MAX(next(), 5);
    printf("MAX(next(), 5)     = %d, next() called %d times\n", m, calls);

    int _x = 21;
    printf("DOUBLE_SHADOW(_x) = %d\n", DOUBLE_SHADOW(_x));
    return 0;
}
```

```
$ gcc -Wall -Wextra twice.c -o twice && ./twice
MAX_BAD: m=6 i=7
MAX    : m=5 i=6
MAX_BAD(next(), 5) = 20, next() called 2 times
MAX(next(), 5)     = 10, next() called 1 times
DOUBLE_SHADOW(_x) = 47010
```

`MAX_BAD(i++, j)` 中，`i++` 在比較時跑一次、在選中的分支又跑一次，`i` 從 5 變成 7，回傳值是 6，兩個數字都不是任何人想要的。`next()` 也被呼叫兩次，回傳第二次的 20。如果 `next()` 是讀硬體暫存器或從佇列取資料，這就是一個難以重現的 bug。

### statement expression 與 `__typeof__`

`MAX` 用了兩個 GNU 擴充（GCC 與 Clang 都支援）：

- **statement expression**：`({ ... })` 把一個複合敘述變成運算式，其值為最後一個運算式敘述的值（GCC 手冊「Statement Exprs」）。
- **`__typeof__(e)`**：取得運算式 `e` 的型別，且**不求值** `e`（GCC 手冊「Typeof」）。

引數先存進區域變數，之後只用變數，所以只求值一次。代價是可攜性：

```
$ gcc -std=c11 -pedantic -c twice.c
twice.c:5:19: warning: ISO C forbids braced-groups within expressions [-Wpedantic]
```

### 衛生（hygiene）問題

`DOUBLE_SHADOW(_x)` 印出 47010 這種垃圾值（每次執行不同；`-O2` 下印出 0）。展開後是：

```c
({ __typeof__(_x) _x = (_x); _x + _x; })
```

內層宣告的 `_x` 從 `=` 之前就開始遮蔽外層的 `_x`（區塊作用域從宣告子結束處開始，N1570 6.2.1 §7），於是 `_x` 用**未初始化的自己**初始化自己。gcc 11 即使開 `-Wall -Wextra` 也**沒有警告**；clang 14 則會說：

```
twice.c:35:54: warning: variable '_x' is uninitialized when used within its own initialization [-Wuninitialized]
```

C 的前置處理器不提供巨集衛生（macro hygiene）：巨集內外的名稱可能互相干擾。原文的 `DOUBLE` 刻意把變數取名為 `_x_in_DOUBLE` 降低撞名機率；Linux 核心則用 `__COUNTER__`（GCC/Clang 擴充，每次展開遞增的整數）產生保證不重複的名字。下面是 `include/linux/compiler.h` 與 `minmax.h` 手法的精簡版：

```c
#include <stdio.h>

#define ___PASTE(a, b) a##b
#define __PASTE(a, b)  ___PASTE(a, b)
#define __UNIQUE_ID(prefix) __PASTE(__PASTE(__UNIQUE_ID_, prefix), __COUNTER__)

#define __cmp(x, y, op) ((x) op (y) ? (x) : (y))
#define __cmp_once(x, y, ux, uy, op) ({ \
    __typeof__(x) ux = (x);             \
    __typeof__(y) uy = (y);             \
    __cmp(ux, uy, op); })
#define my_max(x, y) __cmp_once(x, y, __UNIQUE_ID(x_), __UNIQUE_ID(y_), >)

int main(void)
{
    int _x = 4, x_ = 9;
    printf("%d\n", my_max(_x, x_));
    printf("%d\n", my_max(my_max(1, 7), 3));   /* nested: names do not clash */
    return 0;
}
```

```
$ ./uniq
9
7
$ gcc -E -P uniq.c | grep -m1 UNIQUE
    printf("%d\n", ({ __typeof__(_x) __UNIQUE_ID_x_0 = (_x); __typeof__(x_) __UNIQUE_ID_y_1 = (x_); ...
```

唯一名稱要在 `my_max` 這一層產生，再當成參數往下傳，這樣同一次展開中的多處引用才會拿到同一個名字。巢狀呼叫時內層拿到 `_2`、`_3`，外層拿到 `_4`、`_5`，彼此不衝突。真正的 Linux `min`/`max` 還額外檢查兩個引數的型別與正負號，詳見原文延伸連結〈Linux 核心原始程式碼巨集: max, min〉。

### 其他避免重複求值的方式

| 方案 | 可攜性 | 型別泛用 | 備註 |
|---|---|---|---|
| `static inline` 函式 | ISO C99 | 否（每種型別一個） | 首選；開最佳化後一樣行內展開，還有型別檢查 |
| statement expression + `__typeof__` | GCC/Clang | 是 | Linux 核心大量使用 |
| C23 `typeof` + statement expression | `typeof` 是標準，`({})` 仍是擴充 | 是 | 見本章「進階」 |
| Apple Blocks `^(T x){...}(a)` | 僅 Clang `-fblocks` | 是 | 需連結 BlocksRuntime；Ubuntu 上未裝時連結失敗：`undefined reference to '_NSConcreteGlobalBlock'` |
| `_Generic` 分派到多個 inline 函式 | ISO C11 | 是（列舉型別） | 見本章後半 |

## 陷阱三：不夠謹慎的 `ARRAY_SIZE`

教科書版 `sizeof(a) / sizeof((a)[0])` 對陣列有效，傳入指標時卻默默算出「指標大小 ÷ 元素大小」。最隱蔽的情境是函式參數：參數宣告成 `int a[5]`，型別依 N1570 6.7.6.3 §7 調整為 `int *`。

> 陣列在運算式中退化為指標的規則，見 [Ch 8 — 陣列、函式、指標](./08-arrays-functions-pointers.md)。

Linux 核心的 `ARRAY_SIZE`（`include/linux/array_size.h`）多加一項 `__must_be_array(arr)`：是陣列時值為 0，不是陣列時編譯失敗。下面把它和教科書版放在一起：

```c
#include <stdio.h>

#define NAIVE_ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))

#define __same_type(a, b) __builtin_types_compatible_p(__typeof__(a), __typeof__(b))
#define BUILD_BUG_ON_ZERO_MSG(e, msg) ((int)sizeof(struct { _Static_assert(!(e), msg); }))
#define __is_array(a)       (!__same_type((a), &(a)[0]))
#define __must_be_array(a)  BUILD_BUG_ON_ZERO_MSG(!__is_array(a), "must be array")
#define ARRAY_SIZE(arr) (sizeof(arr) / sizeof((arr)[0]) + __must_be_array(arr))

static size_t h(int a[5]) { return NAIVE_ARRAY_SIZE(a); }
static size_t g(int (*a)[5]) { return ARRAY_SIZE(*a); }

int main(void)
{
    int a[5];
    int *p = a;
    static char table[ARRAY_SIZE(a) * 2];   /* still an integer constant expression */
    printf("%zu %zu %zu %zu\n", NAIVE_ARRAY_SIZE(a), h(a), g(&a), sizeof table);
    printf("%zu\n", NAIVE_ARRAY_SIZE(p));
#ifdef BREAK
    printf("%zu\n", ARRAY_SIZE(p));
#endif
    return 0;
}
```

```
$ gcc -std=gnu11 -Wall -Wextra asize.c -o asize
asize.c: In function ‘h’:
asize.c:3:36: warning: ‘sizeof’ on array function parameter ‘a’ will return size of ‘int *’ [-Wsizeof-array-argument]
...
asize.c:3:40: warning: division ‘sizeof (int *) / sizeof (int)’ does not compute the number of array elements [-Wsizeof-pointer-div]
...
$ ./asize
5 2 5 10
2
```

`h(a)` 得到 2：x86-64 上 `sizeof(int *)` 是 8、`sizeof(int)` 是 4。gcc 11 會警告，但**只是警告**，在一個充滿警告的專案裡很容易被淹沒。`g(&a)` 得到 5 是正確的：`a` 是指向陣列的指標，`*a` 的型別是 `int[5]`。

打開 `-DBREAK`，對指標使用 Linux 版：

```
$ gcc -std=gnu11 -DBREAK asize.c -o /dev/null
asize.c:6:61: error: static assertion failed: "must be array"
$ clang -std=gnu11 -DBREAK asize.c -o /dev/null
asize.c:22:21: error: static_assert failed due to requirement '!(!(!__builtin_types_compatible_p(int *, int *)))' "must be array"
```

### 底層機制：它怎麼判斷「是不是陣列」？

```
a 是 int[5]                         a 是 int *
  typeof(a)       = int[5]            typeof(a)       = int *
  typeof(&(a)[0]) = int *             typeof(&(a)[0]) = int *
  相容？ 否 → __is_array = 1          相容？ 是 → __is_array = 0
  _Static_assert(!(!1)) 通過          _Static_assert(!(!0)) 失敗 → 編譯錯誤
  sizeof(struct{...}) == 0  → +0
```

- `__builtin_types_compatible_p(T1, T2)`：GCC 內建函式，兩型別相容時為 1（忽略頂層限定詞）。它是**整數常數運算式**，所以能放進 `_Static_assert`。
- `struct { _Static_assert(...); }`：C11 允許 `_Static_assert` 出現在結構宣告中。這個結構沒有任何成員，在 GNU C 中大小為 0，所以整個 `__must_be_array` 是 `(int)0`，加到結果上不改變數值。沒有具名成員的結構在 ISO C 屬未定義行為（N1570 6.7.2.1 §8），這是 GNU 擴充。
- 結果仍是整數常數運算式，所以 `static char table[ARRAY_SIZE(a) * 2]` 可以編譯（大小 10）。傳入 VLA 時 `sizeof` 改在執行時期求值，就不是常數了。

舊版核心寫成 `BUILD_BUG_ON_ZERO(e)`，用負寬度的 bit-field 觸發錯誤，錯誤訊息是難懂的 `negative width in bit-field`；改成 `_Static_assert` 後訊息可讀得多。

### C2y 的 `_Countof`

C2y 草案在語言層級加入 `_Countof` 運算子與 `<stdcountof.h>` 的 `countof`，對指標使用屬於限制違反（constraint violation），編譯器必須診斷。提案者是 Linux man-pages 維護者 Alejandro Colomar（N3369，原名 `_Lengthof`），社群投票後改名（N3469）。**本段未實測，為理論預期行為**：我們手上的 GCC 15.3 對 `_Countof(a)` 回報 `implicit declaration of function '_Countof'`，依原文需 GCC 16 或 Clang 21 才支援。

## 陷阱四：多敘述巨集與 `do { ... } while (0)`

一個巨集要放好幾個敘述時，有三種寫法：

```c
#define RESET_BOTH(a, b) a = 0; b = 0                       /* 裸寫 */
#define SWAP_BAD(a, b)  { int t = a; a = b; b = t; }        /* 大括號 */
#define SWAP(a, b)      do { int t = a; a = b; b = t; } while (0)
```

裸寫的版本放在沒有大括號的 `if` 後面：

```c
    int x = 1, y = 2;
    if (x > 100)
        RESET_BOTH(x, y);
    printf("x=%d y=%d\n", x, y);
```

```
$ gcc -Wall bare.c -o bare
bare.c:8:20: warning: macro expands to multiple statements [-Wmultistatement-macros]
bare.c:7:5: note: some parts of macro expansion are not guarded by this ‘if’ clause
$ ./bare
x=1 y=0
```

條件不成立，`y` 卻被清成 0：只有 `a = 0;` 屬於 `if`。gcc 的 `-Wmultistatement-macros`（含於 `-Wall`）會抓到；clang 14 在 `-Wall` 下對這段**沒有警告**。

大括號版本修好了這個問題，卻在 `if`/`else` 之間出錯：

```c
    if (x < y)
        SWAP_BAD(x, y);   /* 展開為 { ... }; 多出的分號結束了 if */
    else
        puts("no swap");
```

```
$ gcc -DBAD swap.c
swap.c:13:5: error: ‘else’ without a previous ‘if’
```

`{ ... };` 是一個複合敘述加一個空敘述，`if` 在分號處就結束了。`do { ... } while (0)` 讓整個巨集成為**單一敘述**，而且**必須**以呼叫端的分號結尾，行為和函式呼叫一模一樣；迴圈只跑一次，最佳化後不留痕跡。Linux 核心的多敘述巨集幾乎都這樣寫。

> `do { } while (0)` 也有例外。Ch 31 會看到在直譯器的 `switch` 裡，`do { ...; continue; } while (0)` 的 `continue` 會被這個偽迴圈捕獲，程式安靜地算錯。巨集中出現 `break`/`continue` 時要特別小心。

## `_Generic`：C11 的型別分派

C 沒有函式多載，`sqrt` 要分 `sqrtf`/`sqrt`/`sqrtl`。C99 的 `<tgmath.h>` 提供依型別自動挑選的巨集，但當時只能靠編譯器魔法實作。C11 引入**泛型選擇**（generic selection，N1570 6.5.1.1；C23 為 N3220 6.5.2.1），讓任何人都能寫出這種巨集：

```
_Generic( 控制運算式 ,  型別1: 結果1,  型別2: 結果2,  default: 結果N )
            │
            └─ 先做 lvalue conversion（去掉限定詞、陣列→指標、函式→指標），
               取其型別，挑出相容的那一項；控制運算式本身「不求值」
```

整個 `_Generic` 是在**編譯時期**決定的運算式，沒選中的分支不會產生程式碼。

```c
#include <math.h>
#include <stdio.h>

#define cbrt(X) _Generic((X), long double: cbrtl, default: cbrt, float: cbrtf)(X)

#define TYPE_NAME(x) _Generic((x),          \
    _Bool: "_Bool", char: "char",            \
    signed char: "signed char", unsigned char: "unsigned char", \
    short: "short", unsigned short: "unsigned short", \
    int: "int", unsigned: "unsigned int",    \
    long: "long", unsigned long: "unsigned long", \
    long long: "long long", unsigned long long: "unsigned long long", \
    float: "float", double: "double", long double: "long double", \
    char *: "char *", const char *: "const char *", \
    default: "other")

int main(void)
{
    double x = 8.0;
    const float y = 3.375f;
    printf("cbrt(8.0)   = %f\n", cbrt(x));
    printf("cbrt(3.375f)= %f  [%s]\n", cbrt(y), TYPE_NAME(cbrt(y)));

    char c = 'a';
    short s = 1;
    int arr[3];
    const char *msg = "hi";
    printf("'a'      : %s\n", TYPE_NAME('a'));
    printf("c        : %s\n", TYPE_NAME(c));
    printf("c + 1    : %s\n", TYPE_NAME(c + 1));
    printf("s * s    : %s\n", TYPE_NAME(s * s));
    printf("1u - 2   : %s\n", TYPE_NAME(1u - 2));
    printf("sizeof 1 : %s\n", TYPE_NAME(sizeof 1));
    printf("arr      : %s\n", TYPE_NAME(arr));
    printf("\"lit\"    : %s\n", TYPE_NAME("lit"));
    printf("msg      : %s\n", TYPE_NAME(msg));
    printf("y (const float): %s\n", TYPE_NAME(y));
    printf("1.0f*2   : %s\n", TYPE_NAME(1.0f * 2));
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra generic.c -o generic -lm && ./generic
cbrt(8.0)   = 2.000000
cbrt(3.375f)= 1.500000  [float]
'a'      : int
c        : char
c + 1    : int
s * s    : int
1u - 2   : unsigned int
sizeof 1 : unsigned long
arr      : other
"lit"    : char *
msg      : const char *
y (const float): float
1.0f*2   : float
```

clang 14 的輸出完全相同。`TYPE_NAME` 是一個**觀察型別的顯微鏡**，這張輸出可以拿來複習好幾個 C 的規則：

- `'a'` 是 `int`：C 的字元常數型別是 `int`（N1570 6.4.4.4 §10），C++ 才是 `char`。
- `c + 1`、`s * s` 是 `int`：整數提升（integer promotion），見 [Ch 2](./02-types-and-objects.md)。
- `1u - 2` 是 `unsigned int`：一般算術轉換讓 `2` 變成無號，結果是 4294967295，這是 [Ch 22](./22-integer-overflow.md) 的經典陷阱。
- `sizeof 1` 是 `unsigned long`：x86-64 Linux 上 `size_t` 的實際型別。
- `arr` 選到 `default`：陣列經 lvalue conversion 退化成 `int *`，而清單裡沒有 `int *`。
- `"lit"` 是 `char *`：C 的字串字面值型別是 `char[N]`，退化後是 `char *`，**不是** `const char *`（寫入它仍是未定義行為）。
- `y` 是 `const float` 卻選中 `float`：lvalue conversion 去掉限定詞。

### 為什麼 `cbrt` 這樣寫不會無窮遞迴？

巨集名稱與函式同名，`default: cbrt` 裡的 `cbrt` 在重新掃描時是正在展開的巨集名稱，依 6.10.3.4 §2 不再展開，留下來的就是 `<math.h>` 宣告的函式名稱。

### 限定詞的歷史坑

C11 原文沒有寫清楚控制運算式要不要先做 lvalue conversion，早期編譯器各做各的：原文提到 Clang 3.8 之前讓 `const float` 的關聯被選中。後來的缺陷報告與 C17 補上說明，C23 6.5.2.1 寫明 "as if it had undergone an lvalue conversion"，腳註補充 "An lvalue conversion drops type qualifiers"。所以**關聯型別不要加 `const`/`volatile`**，加了也永遠選不到：

```c
#define QUAL(X) _Generic((X), const int: "const int", int: "int")
const int ci = 3;
printf("%s\n", QUAL(ci));   /* 印出 int */
```

我們的 gcc 11 與 clang 14 都印 `int`，而且都**沒有**警告；原文指出較新的 Clang 會警告 "association of type 'const int' will never be selected because it is qualified"。

### C 與 C++ 的差異：`'a'`

```c
static void funci(int x) { printf("func value = %d\n", x); }
static void funcc(char c) { printf("func char = %c\n", c); }
static void funcdef(double v) { printf("Def func's value = %lf\n", v); }
#define func(X) _Generic((X), int: funci, char: funcc, default: funcdef)(X)

    func(1);
    func('a');
    func((char) 'a');
    func(1.3);
```

```
func value = 1
func value = 97
func char = a
Def func's value = 1.300000
```

用 C++ template 特化寫出同樣的東西，`func('a')` 會印 `func char = a`。差別就是字元常數的型別。

### 沒有相符項目時

```c
#define ONLY_INT(X) _Generic((X), int: (X))
printf("%d\n", ONLY_INT(1.5));
```

```
gcc  : error: ‘_Generic’ selector of type ‘double’ is not compatible with any association
clang: error: controlling expression type 'double' not compatible with any generic association type
```

這是 `_Generic` 拿來做**編譯期型別檢查**的基礎：不寫 `default`，錯誤型別就直接編譯失敗。Tony Finch 用這招讓 `strchr` 保留引數的 `const`（傳入 `const char *` 就回傳 `const char *`），C23 依 WG14 N3020 把這個想法標準化：`strchr`、`strstr`、`memchr`、`bsearch` 等改為保留限定詞的泛型函式（N3220 7.26.5.3 寫作 `QChar *strchr(QChar *s, int c)`）。

### `_Generic` 的真實用途

- **`<tgmath.h>`**：標準的泛型數學。glibc 在 GCC 8 以上其實用 `__builtin_tgmath`，舊 GCC 則用 `sizeof` 與 `__builtin_classify_type` 拼出的條件運算式，巢狀呼叫時展開大小呈指數成長（glibc 原始碼註解稱 "exponential blowup"）；FreeBSD 的版本則以 `_Generic` 為主。
- **dbg.h**：仿 Rust `dbg!`，`dbg(expr)` 依型別挑選 `static inline` 印出函式，印出檔名、行號、運算式原文與值，並回傳原值。
- **vastringify**：每個引數各自經過 `_Generic` 選格式化函式，完全不用 `...`，格式字串不再承載型別資訊，傳錯型別也不會讀錯記憶體。

`_Generic` 的限制也要知道：它只看**一個**運算式的型別（多參數要巢狀）、不能列舉「所有指標型別」、`char`/`signed char`/`unsigned char` 是三個不同型別、`size_t` 與 `unsigned long` 可能是同一型別（同時列出會因重複而編譯錯誤）。

## 對比與取捨

| 需求 | 巨集 | `static inline` 函式 | `_Generic` + inline 函式 |
|---|---|---|---|
| 型別檢查 | 無 | 完整 | 完整（每個分支） |
| 只求值一次 | 需 GNU 擴充 | 天生 | 天生（控制運算式不求值，選中的函式參數求值一次） |
| 型別泛用 | 天生 | 否 | 是，但要逐一列舉 |
| 能接收型別或成員名稱 | 能 | 不能 | 不能 |
| 能產生宣告、`for` 迴圈開頭等語法片段 | 能 | 不能 | 不能 |
| 除錯 | 不能設中斷點，只能看 `gcc -E` | 正常 | 正常 |
| 錯誤訊息 | 常指向展開後的位置 | 正常 | 尚可 |

判斷準則：**能用函式就用函式**。巨集留給函式做不到的事：拿型別或成員名稱當參數（`container_of`、`offsetof`）、產生程式碼（`DECLARE_OBJECT`）、捕捉呼叫點資訊（`__FILE__`、`__LINE__`、`#expr`）、產生語法結構（`list_for_each_entry`）。原文也實測過：Linux 的 `list_add` 是 `static __always_inline` 函式，不是巨集，「巨集比較快」在開最佳化後通常不成立。

## 踩雷集錦

1. **以為巨集是文字替換**：其實是 token 替換。`NEG_BAD(-a)` 不會變成 `--a`；`CAT(x, +)` 會報錯而不是產生 `x+`。思考巨集時，先把程式碼切成 token。
2. **以為加了參數括號就安全**：整個替換清單也要括號（`100 / SQUARE_MID(a)` = 99）。而且括號只解決優先序，解決不了重複求值（`MAX_BAD(i++, j)`）。
3. **以為引數裡的逗號一定被保護**：前置處理器只認**小括號**。`FOO({ int a, b; })` 會被切成兩個引數 `{ int a` 與 ` b; }`；`FOO(map<int, int>)` 這類寫法在 C 裡較少見，但 compound literal `FOO((struct p){1, 2})` 要外加一層小括號才安全。
4. **以為 `#`/`##` 會先展開引數**：不會。`STR(VERSION)` 得到 `"VERSION"`，要多一層 `XSTR`。這是前置處理器最常見的「為什麼不動」。
5. **在 statement expression 裡用常見名稱**：`_x`、`tmp`、`__a` 都可能和呼叫端撞名，造成自我初始化。用 `__COUNTER__` 產生唯一名稱，或至少用帶巨集名稱的長名稱。另外，以底線加大寫字母或雙底線開頭的識別字保留給實作（N1570 7.1.3），自己的程式碼裡應避免，本章為了貼近核心原始碼才沿用。
6. **在 `_Generic` 關聯中寫 `const T`**：永遠選不到；列出 `size_t` 又列出 `unsigned long` 會因重複關聯而編譯失敗（在 x86-64 Linux 上）。

## 進階：再往深一層

### C23 的 `typeof` 與 `typeof_unqual`

C23 把 `typeof` 收進標準（N3220 6.7.3.6 "Typeof specifiers"），並新增去掉限定詞的 `typeof_unqual`，以及型別推導 `auto`（6.7.10）。以 Docker `gcc:15` 實測：

```c
#include <stdio.h>

#define SWAP(a, b) do { typeof(a) _tmp = (a); (a) = (b); (b) = _tmp; } while (0)

int main(void)
{
    const int ci = 10;
    typeof(ci) a = 1;               /* const int */
    typeof_unqual(ci) b = 2;        /* int */
    b = 5;
    double x = 1.5, y = 2.5;
    SWAP(x, y);
    auto z = x * 2;                 /* C23: type inferred = double */
    printf("%d %d %.1f %.1f %.1f %zu\n", a, b, x, y, z, sizeof z);
    return 0;
}
```

```
$ gcc -std=c23 -Wall -Wextra -pedantic typeof23.c -o t23 && ./t23
1 5 2.5 1.5 5.0 8
$ gcc -std=c11 -pedantic -c typeof23.c
typeof23.c:8:5: error: implicit declaration of function 'typeof' [-Wimplicit-function-declaration]
```

`-std=c11` 下 `typeof` 不是關鍵字（GNU 模式才是），所以 Linux 核心這類程式碼一律寫 `__typeof__`，它在任何模式都可用。注意 `typeof(ci)` 保留 `const`，若 `SWAP` 的暫存變數用 `typeof` 宣告一個 `const` 物件再賦值就會出錯，這正是 `typeof_unqual` 存在的理由。statement expression 在 C23 仍不是標準。

### 一個巨集的生命週期練習

試著不跑程式，手動推導 `XSTR(XCAT(var, VERSION))` 的每一步，再用 `gcc -E -P` 驗證。答案是 `"var3"`：`XSTR` 的參數不與 `#` 相鄰，所以引數 `XCAT(var, VERSION)` 先被完整展開成 `var3`，再交給 `STR`。

### 面試常見問題

- 「`#define` 和 `const` 有什麼差別？」：`#define` 在第 4 階段就消失，沒有型別、沒有作用域、除錯器看不到；`const` 變數是物件，有型別、有位址。但 C 的 `const int` 不是整數常數運算式，不能當陣列大小（會變成 VLA）或 `case` 標籤，這時用 `enum` 常數更好。
- 「寫一個安全的 `MIN` 巨集」：回答時依序講括號、重複求值、statement expression + `__typeof__`、命名衝突，最後提 `static inline` 或 `_Generic` 替代方案，就是完整答案。

## 動手練習

1. 把 `SQUARE` 改成 `#define SQUARE(x) ((x) * (x))`，呼叫 `SQUARE(i++)`。用 `gcc -Wall` 編譯，觀察 `-Wsequence-point` 的警告，並解釋為什麼這是未定義行為而不只是「算兩次」。
2. 寫 `#define STATIC_ASSERT_SAME_TYPE(a, b)`，在 `a` 與 `b` 型別不同時編譯失敗；分別用 `__builtin_types_compatible_p` 與 `_Generic` 各實作一次。
3. 用 `TYPE_NAME` 觀察 `-1 < 1u`、`(char)200 + 0`、`'\377'`、`3 / 2.0f` 的型別，對照 Ch 2 與 Ch 22 預測結果。
4. 讓 `ARRAY_SIZE` 在純 ISO C11（不用 `typeof`、`__builtin_types_compatible_p`）下拒絕指標。提示：`_Generic(&(a), ...)` 能區分 `int (*)[N]` 與 `int **` 嗎？想想 N 未知時的困難。

## 本章重點整理

- 前置處理器是翻譯第 4 階段，在語法分析之前；它只處理 pp-token，不懂優先序、型別與副作用。
- 函式式巨集展開順序：切引數 → 引數預先完整展開（與 `#`/`##` 相鄰者除外）→ 字串化與串接 → 重新掃描；正在展開的巨集名稱不再展開（painted blue）。
- 四大陷阱與解法：括號（參數與整體都加）、重複求值（statement expression + `__typeof__` + 唯一名稱）、`ARRAY_SIZE` 接受指標（`__must_be_array`／C2y `_Countof`）、多敘述（`do { } while (0)`）。
- `_Generic` 在編譯時期依控制運算式經 lvalue conversion 後的型別挑選結果；控制運算式不求值、限定詞會被去掉。
- 能用 `static inline` 就不要用巨集；巨集的正當用途是函式做不到的事。

## 自我檢核

- [ ] 不看筆記，能說出 C 的 8 個翻譯階段中前 4 個各做什麼，並解釋為什麼 `//` 註解行尾的 `\` 會吃掉下一行
- [ ] 能解釋為什麼 `STR(VERSION)` 與 `XSTR(VERSION)` 結果不同，並引用 6.10.3.1 的規則
- [ ] 能手動推導 `#define x (4 + y)`、`#define y (2 * x)` 下 `x` 的展開結果，並說明停止的原因
- [ ] 如果面試官要你寫 `MAX`，能一路講到命名衝突，並寫出 `__COUNTER__` 版本
- [ ] 能說明 `__must_be_array` 為什麼值是 0 且仍是整數常數運算式
- [ ] 能舉出三個 `_Generic` 會讓人意外的選擇結果（`'a'`、`const float`、陣列）並解釋原因
- [ ] 知道在什麼情況下**不**應該寫成巨集

## 延伸閱讀

### 官方文件 / Spec

- **[ISO/IEC 9899 草案 N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：5.1.1.2「Translation phases」（兩頁，本章開頭的表格就是它）；6.10.3.1–6.10.3.4（巨集展開的全部規則，四頁，含 `hash_hash` 這個經典 EXAMPLE）；6.5.1.1「Generic selection」
  - **前提知識**：知道什麼是 token；讀過 Ch 4 的「怎麼讀規格書」更好
  - **為什麼值得讀**：本章所有「為什麼」最後都回到這幾段，篇幅短、密度高

- **[The C Preprocessor（GCC cpp 手冊）](https://gcc.gnu.org/onlinedocs/cpp/)** — GNU
  - **讀哪裡**：3.10「Macro Pitfalls」整節（Operator Precedence Problems、Swallowing the Semicolon、Duplication of Side Effects、Self-Referential Macros、Argument Prescan），恰好對應本章四大陷阱
  - **能學到什麼**：每個陷阱都有 GCC 維護者寫的反例，Argument Prescan 一節講清楚為什麼需要兩層間接
  - **前提知識**：無

- **[GCC 手冊：Statement Exprs](https://gcc.gnu.org/onlinedocs/gcc/Statement-Exprs.html) 與 [Typeof](https://gcc.gnu.org/onlinedocs/gcc/Typeof.html)**
  - **讀哪裡**：兩頁都很短，Statement Exprs 末段說明 statement expression 中的 `break`/`goto` 與暫存物件生命週期的限制
  - **和本章的關聯**：`MAX` 與 Linux `min/max` 依賴這兩項擴充

### 部落格 / 技術文章

- **[The Development of the C Language](https://www.bell-labs.com/usr/dmr/www/chist.html)** — Dennis M. Ritchie（HOPL-II，1993）
  - **讀哪裡**：「Portability」之前提到 preprocessor 的兩段（搜尋 "preprocessor"）
  - **能學到什麼**：前置處理器為何被當成「選用附件」，以及為什麼它的語法和 C 不一致的一手說法
  - **前提知識**：無；[Ch 6](./06-history-of-c.md) 有完整導讀

- **[C11 - Generic Selections](http://www.robertgamble.net/2012/01/c11-generic-selections.html)** — Robert Gamble（2012）
  - **這篇說什麼**：C11 剛發布時對 `_Generic` 最完整的介紹，涵蓋 `default`、型別相容、在巨集中多層組合
  - **為什麼值得讀**：範例比規格書友善，可當 `_Generic` 的第二份教材
  - **前提知識**：本章 `_Generic` 小節

- **[Experimenting with _Generic() for parametric constness in C11](https://fanf.livejournal.com/144696.html)** — Tony Finch（2016）
  - **讀哪裡**：整篇；重點在 `(strchr)(...)` 繞過巨集，與用 `_Generic` 讓錯誤型別變成編譯錯誤
  - **和本章的關聯**：painted blue 規則與 `_Generic` 編譯期檢查的實戰應用，也是 C23 N3020 的前身

### 書籍

- **《Modern C》C23 版** — Jens Gustedt（Manning / INRIA 免費版，2024）
  - **讀哪裡**：16 章「Function-like macros」與 18 章 type-generic programming（章節號依版本可能略有差異，以目錄中 "macro" 與 "type-generic" 為準）
  - **這本書的定位**：WG14 成員寫的現代 C 教科書，把 `_Generic`、巨集與 C23 新特徵放在一起講

> 和 `programming/c_interview/` 的關係：面試導向的陷阱速查見 [../c_interview/19-preprocessor-traps.md](../c_interview/19-preprocessor-traps.md)，本章則把每個陷阱推回規格條款與展開規則。

這章把巨集的規則講完了。下一章把這些規則推到極限：X macro、可變參數巨集、C23 的 `#embed` 與 `__VA_OPT__`，以及前置處理器裡的「遞迴」，看 Linux 核心、CPython、BSD 與 Zephyr 怎麼用它們產生整套程式碼。

→ [Ch 28 前置處理器 II：X macro、元程式設計與 C23 新特徵](./28-preprocessor-advanced.md)
