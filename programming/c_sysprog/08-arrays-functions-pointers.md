# Ch 8 — 陣列、函式、指標：derived declarator 與 array decay

> **目標**：理解陣列、函式、指標同屬 derived declarator types 卻是三種不同型別；能用 right-left rule 與 cdecl 讀懂任何 C 宣告；精確說出 array decay 何時發生、何時不發生（三個例外），並能推導多維陣列、`&a` 與 `a`、`&` 與 `*` 的型別與位址。

> **對應原文**：[你所不知道的 C 語言：指標篇](https://hackmd.io/@sysprog/c-pointer) 的「這個講座並非頭腦體操」「英文很重要」「Pointers vs. Arrays」「Address and indirection operators」「Linus Torvalds 親自教你 C 語言」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、gdb 12.1、cdecl 2.5。C23 段落以 Docker `gcc:14`（gcc 14.4）執行。輸出中的位址每次執行不同。

## 為什麼需要這個？

Dennis Ritchie 在回顧 C 的論文裡寫道，C 在同類語言中最有特色的兩個設計是「陣列與指標的關係」以及「宣告語法模仿運算式語法」，而它們「also among its most frequently criticized features, and often serve as stumbling blocks to the beginner」。

設計者本人都承認這兩件事會絆倒初學者，這一章就專門處理這兩件事。你可能遇過這些狀況：

- K&R 說 `char s[]` 和 `char *s` 等價，但你在另一個檔案寫 `extern char *a;` 去接 `char a[10];`，程式當掉了。
- `sizeof(arr) / sizeof(arr[0])` 在 `main` 裡算出 7，搬進函式就變成 2。
- 看到 `void (*signal(int, void (*)(int)))(int);` 完全不知道從哪裡開始讀。
- `int a[3][5]`，`a`、`a[0]`、`&a`、`&a[0]` 的位址印出來都一樣，但 `+1` 之後全都不同。

這些現象背後只有兩套規則：**宣告怎麼組成型別**、**陣列運算式什麼時候變成指標**。把規則學會，上面每一題都能推導出來，不用死背。

## 先建立直覺

### 宣告是「使用方式」的倒影

Ritchie 說 C 的宣告語法是類比推理的產物：對每一種組合型別，本來就有「使用」它的方式（陣列用下標、函式用呼叫、指標用 `*`），於是宣告就寫成「這樣使用之後會得到什麼型別」：

```
int i, *pi, **ppi;
    │   │     └─ **ppi 是 int  → ppi 是「指向 (指向 int 的指標) 的指標」
    │   └─ *pi 是 int          → pi 是「指向 int 的指標」
    └─ i 是 int

int *api[10];     *api[i] 是 int → api 是「10 個 (指向 int 的指標) 的陣列」
int (*pai)[10];   (*pai)[i] 是 int → pai 是「指向 (10 個 int 的陣列) 的指標」
```

讀宣告的訣竅就是：**把宣告當成一個運算式，依運算子優先序「使用」那個名字，最後得到開頭的基本型別**。後置的 `[]`、`()` 比前置的 `*` 優先，所以 `*api[10]` 先下標再解參考，`(*pai)[10]` 先解參考再下標。

### 陣列名稱「平常」會變成指標

```
int x[4];                          運算式中的 x（多數情境）
┌────┬────┬────┬────┐
│ x0 │ x1 │ x2 │ x3 │    ───►     &x[0]，型別 int *，不是 lvalue
└────┴────┴────┴────┘
 ▲ 整個是一個 object，型別 int[4]，sizeof = 16

例外：sizeof x、&x、用字串常量初始化陣列 → 不轉換，x 仍是 int[4]
```

陣列本身是一整塊 object，**不存在任何「指向首元素的指標」被存在某處**；指標是在運算式中提到陣列名稱時「臨時產生」的。這句話是理解整章的鑰匙，下面會從歷史、規格、組合語言三個角度驗證它。

## derived declarator types：三種衍生型別

N1570 **6.2.5 §24**：

> Array, function, and pointer types are collectively called **derived declarator types**. A declarator type derivation from a type T is the construction of a derived declarator type from T by the application of an array-type, a function-type, or a pointer-type derivation to T.

三種衍生可以層層套用，但有三個組合是禁止的：

| 從 T 衍生 | 合法？ | 例子 |
|---|---|---|
| 陣列的陣列 | 合法 | `int m[3][5]` |
| 指標的陣列 | 合法 | `char *argv[]` |
| 函式指標的陣列 | 合法 | `int (*ops[3])(int, int)` |
| 回傳指標的函式 | 合法 | `char *strdup(const char *)` |
| 回傳函式指標的函式 | 合法 | `signal` |
| 指向陣列的指標 | 合法 | `int (*pai)[10]` |
| **函式的陣列** | 不合法 | `int g[3](void)` |
| **回傳陣列的函式** | 不合法 | `int f(void)[3]` |
| **回傳函式的函式** | 不合法 | `int (h(void))(void)` |

```c
int f(void)[3];          /* 函式不能回傳陣列 */
int g[3](void);          /* 不能有「函式的陣列」 */
int (h(void))(void);     /* 函式不能回傳函式 */
```

```
$ gcc -c illegal.c
illegal.c:1:5: error: ‘f’ declared as function returning an array
illegal.c:2:5: error: declaration of ‘g’ as array of functions
illegal.c:3:6: error: ‘h’ declared as function returning a function
```

這三條限制的共通點是：陣列與函式都不是「可以整個搬動的值」（陣列無法指定，函式沒有大小）。凡是想把它們當值傳遞的地方，C 一律改用指標間接表達，這也是指標在 C 裡無所不在的原因之一。

原文用一個 dispatch table 示範「函式指標的陣列」：

```c
int (*routine[])(int) = { handler0, handler1, handler2 };
routine[condition](argv);
```

注意它是「函式指標的陣列」而不是「指向函式陣列的指標」，後者根本不存在。完整可跑的版本在 [Ch 9](./09-void-pointer-and-callbacks.md)。

## 宣告的讀法：right-left rule 與 cdecl

### right-left rule

規則只有三步：

1. 從**識別字**（變數名）開始。
2. 先往**右**讀，遇到 `[N]` 念「N 個……的陣列」，遇到 `(參數)` 念「接受……的函式，回傳」；碰到右括號 `)` 或宣告結尾就轉向。
3. 再往**左**讀，遇到 `*` 念「指向……的指標」，遇到 `const` 念「const 的」；碰到左括號 `(` 就跳出這層括號，回到第 2 步繼續往右。

最後讀到最左邊的基本型別收尾。以原文的 `char *(*fptab[])(int)` 為例：

```
char *(*fptab[])(int)
         ^^^^^            1. fptab
              ^^          2. 往右：[] → fptab 是「……的陣列」
        ^                 3. 碰到 ) 轉向，往左：* → 「指向……的指標」的陣列
       (       )          4. 跳出括號，往右：(int) → 「接受 int 的函式，回傳……」
      ^                   5. 往右沒了，往左：* → 「回傳指向……的指標」
^^^^                      6. char

fptab 是「指向『接受 int、回傳 char 指標』的函式」的指標，所組成的陣列
```

### cdecl：讓工具幫你念

`cdecl` 可以把宣告翻成英文，或把英文翻成宣告。Ubuntu 套件名就叫 `cdecl`（沒有 sudo 權限時可以 `apt-get download cdecl` 再 `dpkg -x` 解開使用）：

```
cdecl> explain char *(*fptab[])(int)
declare fptab as array of pointer to function (int) returning pointer to char
cdecl> explain int (*pai)[10]
declare pai as pointer to array 10 of int
cdecl> explain int *api[10]
declare api as array 10 of pointer to int
cdecl> explain int (*(*x)(void))[3]
declare x as pointer to function (void) returning pointer to array 3 of int
cdecl> explain char * const *q
declare q as pointer to const pointer to char
cdecl> declare a as array of pointer to function returning pointer to function returning pointer to char
char *(*(*a[])())()
cdecl> declare f as function returning array 3 of int
int f()[3]
Warning: Unsupported in C -- 'Function returning array'
	(maybe you mean "function returning pointer")
```

原文的那句話值得記住：**如果你沒辦法用英文解說一個 C 宣告，通常表示你不理解它。** cdecl 的英文就是規格書的用語（array of、pointer to、function returning），用它練習，等於在練規格書的詞彙。

### 經典：signal 的原型

[signal(2)](https://man7.org/linux/man-pages/man2/signal.2.html) 的原始宣告是：

```c
void (*signal(int sig, void (*func)(int)))(int);
```

```
cdecl> explain void (*signal(int, void (*)(int)))(int)
declare signal as function (int, pointer to function (int) returning void)
        returning pointer to function (int) returning void
```

用 right-left rule：`signal` 往右是 `(int, void (*)(int))`，所以是函式；碰到 `)` 往左是 `*`，回傳指標；跳出括號往右是 `(int)`，指向「接受 int 的函式」；往左是 `void`。這種宣告沒有人喜歡讀，所以 man page 和 glibc 都用 typedef 拆開：

```c
typedef void (*sighandler_t)(int);
sighandler_t signal(int signum, sighandler_t handler);
```

**typedef 是馴服複雜宣告的標準工具**：每一層衍生取一個名字，宣告就回到「型別 名字」的簡單形式。

### 經典：`(*(void(*)())0)()`

原文引自《C Traps and Pitfalls》的例子。從內往外拆：

```
void(*)()            型別名稱：指向「回傳 void 的函式」的指標（沒有識別字的宣告）
(void(*)())0         把 0 轉型成這種函式指標
*(void(*)())0        解參考，得到 function designator
(*(void(*)())0)()    呼叫它：呼叫位址 0 的函式
```

用 typedef 改寫就清楚多了：`typedef void (*funcptr)(); (*(funcptr)0)();`。這在沒有作業系統的機器上是「跳到重置向量」的慣用法。看 gcc `-Os` 產生什麼：

```c
int main() {
    typedef void (*funcptr)();
    (* (funcptr) (void *) 0)();
}
```

```
$ gcc -Os -S -o - null0.c
main:
	endbr64
	xorl	%edx, %edx        # edx = 0（目標位址）
	pushq	%rax              # 調整 stack 對齊
	xorl	%eax, %eax        # al = 0：variadic/未原型函式要告訴被呼叫端用了幾個向量暫存器
	call	*%rdx             # 間接呼叫位址 0
	xorl	%eax, %eax
	popq	%rdx
	ret
```

`xorl %eax, %eax` 那行值得注意：因為 `()` 在 C17 以前代表「參數未指定」，x86-64 SysV ABI 對這類呼叫要在 `%al` 放入使用的向量暫存器數目（[Ch 11](./11-process-layout-and-abi.md) 會講 ABI）。至於 `()` 的意義，C23 改了：

> C89 到 C17，宣告裡的 `()` 代表「參數個數與型別未指定」，呼叫時不做檢查；C23 起 `()` 等同 `(void)`。在 Docker `gcc:14` 實測：`int f(); int g(void) { return f(1, 2); }` 用 `-std=c17` 可以編過，用 `-std=c23` 則報 `error: too many arguments to function 'f'`。新程式碼一律寫 `(void)`，兩個版本的意思都一樣。

## Pointers vs. Arrays

### 歷史：為什麼會有 array decay

Ritchie 的論文「Embryonic C」一節交代了這條規則的來歷。在 B 和早期的 C（NB）裡，宣告一個陣列會配置兩樣東西：陣列本身，**加上一個存著陣列起始位址的指標格子**。陣列名稱指的是那個格子。後來他想加入 struct，問題就來了：

```c
struct {
    int  inumber;
    char name[14];
};
```

他希望這個 struct 能精確描述 Unix 目錄項目的 16 個位元組，但「編譯器要把 `name` 的那個指標格子藏在哪裡？」解法就是 C 從無型別的 BCPL 走向有型別的 C 的關鍵一躍：

> It eliminated the materialization of the pointer in storage, and instead caused the creation of the pointer when the array name is mentioned in an expression. The rule, which survives in today's C, is that values of array type are converted, when they appear in expressions, into pointers to the first of the objects making up the array.

所以 array decay 不是隨意的語法糖，是**為了讓陣列不必在記憶體裡存一個指標**而發明的。代價是陣列名稱不再能被指定（B 允許改陣列名稱來「移動」陣列，C 不行），這就是 [Ch 7](./07-pointers-by-the-standard.md) 說陣列不是 modifiable lvalue 的根源。

### 規格：6.3.2.1 §3 與三個例外

> Except when it is the operand of the `sizeof` operator, the `_Alignof` operator, or the unary `&` operator, or is a string literal used to initialize an array, an expression that has type "array of type" is converted to an expression with type "pointer to type" that points to the initial element of the array object and is not an lvalue.

N1570 的清單裡有 `_Alignof`，但 `_Alignof` 只能接型別名稱、不能接運算式，這是條文瑕疵，後續版本已拿掉（N3220 的清單裡沒有）。實際的例外是三個，C23 再加上 `typeof`：

```c
#include <stdio.h>

static void takes_array(int a[10])          /* 參數：被調整成 int *a */
{
    printf("in callee: sizeof a = %zu\n", sizeof a);
    a = NULL;                                /* 合法：a 是普通指標變數 */
}

int main(void)
{
    int x[10] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9};
    printf("%d %d %d %d\n", x[4], *(x + 4), *(4 + x), 4[x]);

    printf("sizeof x       = %zu\n", sizeof x);       /* 例外 1：sizeof */
    printf("sizeof (x + 0) = %zu\n", sizeof (x + 0)); /* x+0 已經 decay */
    printf("x      = %p\n", (void *) x);
    printf("&x     = %p\n", (void *) &x);             /* 例外 2：& */
    printf("x + 1  = %p\n", (void *) (x + 1));
    printf("&x + 1 = %p\n", (void *) (&x + 1));

    char s[] = "hi";                                  /* 例外 3：字串常量初始化陣列 */
    printf("sizeof s = %zu, sizeof \"hi\" = %zu\n", sizeof s, sizeof "hi");
    takes_array(x);
    return 0;
}
```

```
$ gcc -Wall -Wextra -g decay.c -o decay && ./decay
decay.c: In function ‘takes_array’:
decay.c:5:50: warning: ‘sizeof’ on array function parameter ‘a’ will return size of ‘int *’ [-Wsizeof-array-argument]
4 4 4 4
sizeof x       = 40
sizeof (x + 0) = 8
x      = 0x7ffd31ba8ed0
&x     = 0x7ffd31ba8ed0
x + 1  = 0x7ffd31ba8ed4
&x + 1 = 0x7ffd31ba8ef8
sizeof s = 3, sizeof "hi" = 3
in callee: sizeof a = 8
```

逐行對照：

| 運算式 | 有沒有 decay | 型別 | 結果 |
|---|---|---|---|
| `sizeof x` | 否（例外 1） | `int[10]` | 40 |
| `sizeof (x + 0)` | 是，`+` 先讓 `x` decay | `int *` | 8 |
| `x` | 是 | `int *` | `...ed0` |
| `&x` | 否（例外 2） | `int (*)[10]` | `...ed0`，位址值相同 |
| `x + 1` | 是 | `int *` | 前進 4 bytes → `...ed4` |
| `&x + 1` | 否 | `int (*)[10]` | 前進 40 bytes → `...ef8`（0xed0 + 0x28） |
| `char s[] = "hi"` | 否（例外 3） | `char[3]` | 字串常量的內容被**複製**進 `s` |

`x` 和 `&x` 位址值相同、型別不同。原文特別指出，`str == &str` 這樣直接比較在 ISO C 是 constraint violation（`char *` 和 `char (*)[123]` 不相容），要寫成 `(void *) str == (void *) &str`。兩者相等的依據是 6.5.9 §6：指向一個 object 的指標和指向「位於它開頭的子物件」的指標比較相等。

C23 的 `typeof` 也不觸發 decay，在 Docker `gcc:14` 用 `-std=c23` 實測：

```c
int arr[10];
typeof(arr) copy;              /* typeof 保留陣列型別：不 decay */
printf("sizeof(typeof(arr)) = %zu\n", sizeof copy);
```

```
sizeof(typeof(arr)) = 40
```

### `[]` 只是加法加解參考

N1570 **6.5.2.1 §2**：「The definition of the subscript operator `[]` is that `E1[E2]` is identical to `(*((E1)+(E2)))`.」

所以 `x[4]` 是 `*(x + 4)`；加法可交換，`*(4 + x)` 也一樣，於是 `4[x]` 合法。這不是炫技用的冷知識，它證明了**陣列下標根本不是陣列專屬的操作**：`[]` 的運算元裡沒有陣列，只有一個 decay 後的指標和一個整數。對陣列下標，編譯器要的資訊只有兩項：元素大小（縮放用）與首元素位址（decay 的產物）。

這個性質在真實程式碼裡最常見的用法是對字串常量直接下標。glibc 的整數格式化（`printf` 內部）用的就是 digit table：

```c
/* glibc stdio-common/_itoa.c（節錄） */
const char *digits = (upper_case ? _itoa_upper_digits : _itoa_lower_digits);
...
do
    *--buflim = digits[value % base];
while ((value /= base) != 0);
```

自己寫一個可以跑的縮小版：

```c
#include <stdio.h>
static char *utoa_base(unsigned v, unsigned base, char *buflim)
{
    *--buflim = '\0';
    do
        *--buflim = "0123456789abcdef"[v % base];   /* 字串常量直接下標 */
    while ((v /= base) != 0);
    return buflim;
}
int main(void)
{
    char buf[40];
    printf("%s %s %s\n", utoa_base(255, 16, buf + sizeof buf),
           utoa_base(255, 2, buf + 20), utoa_base(0, 10, buf + 10));
    return 0;
}
```

```
$ ./digits
ff 11111111 0
```

`"0123456789abcdef"` 是 `char[17]`，在 `[]` 裡 decay 成指標，`v % base` 當索引，一次查表完成「數值 → 字元」。`do ... while` 而不是 `while`，是為了讓 0 也輸出一個 `'0'`（第三個輸出驗證了這點）。從緩衝區尾端往前寫（`*--buflim`），是因為除法先得到的是最低位。

### 陣列 vs 指標：組合語言裡的差別

「宣告處兩者不可互換」可以在機器碼裡看到：

```c
extern char arr[];
extern char *ptr;
char get_arr(void) { return arr[3]; }
char get_ptr(void) { return ptr[3]; }
```

```
$ gcc -O2 -fno-pic -S -o - access.c
get_arr:
	endbr64
	movzbl	arr+3(%rip), %eax        # 直接讀「arr 的位址 + 3」
	ret
get_ptr:
	endbr64
	movq	ptr(%rip), %rax          # 先從 ptr 這個 object 讀出它存的位址
	movzbl	3(%rax), %eax            # 再讀「那個位址 + 3」
	ret
```

`arr` 這個符號**就是**陣列本身的位址；`ptr` 這個符號是一個 8 bytes 指標 object 的位址，裡面存著另一個位址。同樣寫成 `x[3]`，前者一次存取，後者兩次。（原文提醒，實際是否多一道載入取決於指標是否已在暫存器、ABI 與最佳化，不是固定步數；這裡兩者都是全域變數，差異最明顯。）

### 宣告不匹配：`extern char *a` 接 `char a[10]`

現在可以預測下面這個程式會怎麼死：

```c
/* def.c */
char a[10] = "ABCDEFGHI";
```

```c
/* use.c */
#include <stdio.h>
extern char *a;                 /* 錯誤：定義端是 char a[10] */
int main(void)
{
    printf("a (當成指標的值) = %p\n", (void *) a);
    printf("a[0] = %c\n", a[0]);
    return 0;
}
```

```
$ gcc -Wall -Wextra -g def.c use.c -o use && stdbuf -o0 ./use; echo "exit=$?"
a (當成指標的值) = 0x4847464544434241
Segmentation fault (core dumped)
exit=139
```

`use.c` 以為 `a` 這個符號的位置存著一個指標，於是照 `get_ptr` 的方式先讀 8 bytes 當位址。那 8 bytes 其實是字元 `'A'`…`'H'`（0x41…0x48），little-endian 組起來就是 `0x4847464544434241`，拿它當位址解參考，當然 segfault。連結器沒有抱怨，因為 ELF 符號表只記名字和位址，不記 C 型別；規格上這是 6.2.7 §2 的「同一 object 的宣告型別不相容」，UB。正確寫法是 `extern char a[];`（incomplete array type，[Ch 7](./07-pointers-by-the-standard.md)）：

```
$ gcc -Wall -Wextra -g def.c use_ok.c -o use_ok && ./use_ok
a[0] = A, a = ABCDEFGHI
```

> 印的時候用 `stdbuf -o0` 關掉 stdout 緩衝，是因為輸出接到管線時 stdout 是全緩衝，程式在 flush 之前就 segfault，第一行會消失。這種「當掉前的 printf 不見了」也是除錯時常見的誤導。

### 函式參數：唯一可以互換的地方

K&R 第二版第 99 頁說「As formal parameters in a function definition」，第 100 頁才說 `char s[];` 和 `char *s` 等價。很多讀者只記得第 100 頁，忘了前提。規格依據是 N1570 **6.7.6.3 §7**：參數宣告為「array of type」會被調整（adjusted）成「qualified pointer to type」；§8 則把「function returning type」調整成函式指標。

- `void f(char a[])`、`void f(char a[10])`、`void f(char *a)` 三者完全相同，`[10]` 被丟掉，編譯器不檢查。
- 寫在 `[]` 裡的 qualifier 作用在調整後的指標本身：`char a[const]` 等於 `char *const a`。
- 所以函式內 `sizeof a` 是指標大小，gcc 的 `-Wsizeof-array-argument` 會警告（見上面 `decay.c` 的輸出）。

Ritchie 說這個參數調整規則是「a living fossil, a remnant of NB's way of declaring a pointer」，而且「serves as much to confuse the learner as to alert the reader」。

## sizeof 陷阱與 ARRAY_SIZE

原文引用 Linux 核心的 `ARRAY_SIZE`：

```c
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
```

它只對真正的陣列有效。傳進指標，算出來的是「指標大小 / 元素大小」：

```c
#include <stdio.h>
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
static void f(int *p) { printf("in f: %zu\n", ARRAY_SIZE(p)); }
int main(void)
{
    int v[7];
    printf("in main: %zu\n", ARRAY_SIZE(v));
    f(v);
    return 0;
}
```

```
$ gcc -Wall -Wextra arrsize.c -o arrsize && ./arrsize
arrsize.c:2:34: warning: division ‘sizeof (int *) / sizeof (int)’ does not compute the number of array elements [-Wsizeof-pointer-div]
in main: 7
in f: 2
```

8 / 4 = 2。gcc 8 起有 `-Wsizeof-pointer-div` 會警告，但警告可能被忽略。Linux 核心的真正版本是 `sizeof(arr) / sizeof((arr)[0]) + __must_be_array(arr)`：`__must_be_array` 利用 gcc 的 `__builtin_types_compatible_p` 判斷 `arr` 和 `&(arr)[0]` 的型別是否相同（陣列與指標不同、指標與指標相同），是指標就**直接編譯失敗**（失敗的手段隨核心版本演變：傳統寫法是讓一個 bit-field 的寬度變成負數，較新的版本改用 `_Static_assert` 一類的機制，細節請以你手上的核心原始碼為準）。把執行期的錯誤變成編譯期的錯誤，是核心常用的手法（[Ch 27](./27-preprocessor-basics.md) 會拆解這類巨集）。

## 多維陣列：陣列的陣列

C 沒有真正的「二維陣列」，只有「元素本身是陣列」的一維陣列。`int x[3][5]` 讀作「3 個 (5 個 int 的陣列) 的陣列」：

```
int x[3][5]：一塊連續的 60 bytes

  x[0]                    x[1]                    x[2]
┌──┬──┬──┬──┬──┐┌──┬──┬──┬──┬──┐┌──┬──┬──┬──┬──┐
│00│01│02│03│04││10│11│12│13│14││20│21│22│23│24│
└──┴──┴──┴──┴──┘└──┴──┴──┴──┴──┘└──┴──┴──┴──┴──┘
 ▲ x、x[0]、&x、&x[0]、&x[0][0] 位址值都在這裡，型別各不相同

x        → decay 成 int (*)[5]   x + 1     跨 20 bytes（一列）
x[i]     → int[5]，再 decay 成 int *     x[i] + 1  跨 4 bytes
&x       → int (*)[3][5]         &x + 1    跨 60 bytes（整個陣列）
```

N1570 **6.5.2.1 §3** 說每一個下標剝掉一層維度：n 維陣列在運算式中轉成指向 (n−1) 維子陣列的指標，解參考得到 (n−1) 維子陣列，它又再 decay，直到純量。row-major（最後一個下標變化最快）是從型別規則**推導**出來的，不是編譯器各自的慣例。展開後 `x[i][j]` 就是 `*(*(x + i) + j)`，位址是 `(char *) x + (i * 5 + j) * sizeof(int)`。最外層的 3 不參與計算，所以參數可以寫 `int m[][5]`（等於 `int (*m)[5]`），但不能省略 5。

```c
#include <stdio.h>

static int sum_rows(int (*m)[5], int rows)      /* 等價於 int m[][5] */
{
    int s = 0;
    for (int i = 0; i < rows; i++)
        for (int j = 0; j < 5; j++)
            s += m[i][j];
    return s;
}

int main(void)
{
    int x[3][5];
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 5; j++)
            x[i][j] = i * 10 + j;

    printf("x        = %p\n", (void *) x);
    printf("x + 1    = %p  (跨過一個 int[5] = %zu bytes)\n",
           (void *) (x + 1), sizeof x[0]);
    printf("x[1] + 1 = %p  (跨過一個 int  = %zu bytes)\n",
           (void *) (x[1] + 1), sizeof x[1][0]);
    printf("x[2][3] = %d, *(*(x + 2) + 3) = %d\n", x[2][3], *(*(x + 2) + 3));
    printf("sum = %d\n", sum_rows(x, 3));

    int i = 1, j = 7;                    /* 從執行期來，編譯器無法在編譯期擋 */
    printf("x[1][7] = %d  (看似等於 x[2][2] = %d)\n", x[i][j], x[2][2]);
    return 0;
}
```

```
$ gcc -Wall -Wextra -g multi.c -o multi && ./multi
x        = 0x7ffd8bcc1460
x + 1    = 0x7ffd8bcc1474  (跨過一個 int[5] = 20 bytes)
x[1] + 1 = 0x7ffd8bcc1478  (跨過一個 int  = 4 bytes)
x[2][3] = 23, *(*(x + 2) + 3) = 23
sum = 180
x[1][7] = 22  (看似等於 x[2][2] = 22)
```

最後一行「看起來能用」：`x[1][7]` 算出的位址恰好落在 `x[2][2]`，仍在整個 `x` 之內。但規格說這是 UB。N1570 Annex J.2 明列：「An array subscript is out of range, even if an object is apparently accessible with the given subscript (as in the lvalue expression `a[1][7]` given the declaration `int a[4][5]`)」。理由是 `x[1]` 的型別是 `int[5]`，指標算術只能在這個子陣列（含尾端後一個位置）內進行（6.5.6 §8）。UBSan 抓得到：

```
$ gcc -g -fsanitize=undefined multi.c -o multi_ub && ./multi_ub
...
x[1][7] = 22  (看似等於 x[2][2] = 22)
multi.c:28:63: runtime error: index 7 out of bounds for type 'int [5]'
```

（runtime error 訊息印在最後，是因為 stderr 沒有緩衝、stdout 接管線時有緩衝，兩者的輸出順序被打亂。）

Linus Torvalds 在 LKML 討論 array argument 時說過一段很誠實的話：`int fn(int a[][10])` 等價於 `int fn(int (*a)[10])`，`a + 1` 前進 40 bytes，**不像** `int *`；然後補一句「C multidimensional arrays and the conversions to pointers are really easy to get confused about. Which is why I hope we don't have them」。連他都承認容易搞混，這也是核心程式碼很少用多維陣列的原因。

### 用 gdb 看全域陣列的型別與位址

原文用 gdb 檢查 `calendar` 和 `b` 這兩個陣列。在 x86-64 上重做（`-no-pie` 讓位址固定）：

```c
int a[3];
struct { double v[3]; double length; } b[17];
int calendar[12][31];
int main(void) { return 0; }
```

```
(gdb) p sizeof(calendar)
$1 = 1488
(gdb) p sizeof(b)
$2 = 544
(gdb) p sizeof(b[0])
$3 = 32
(gdb) whatis calendar[0]
type = int [31]
(gdb) whatis &calendar[0]
type = int (*)[31]
(gdb) whatis &calendar
type = int (*)[12][31]
(gdb) p &b
$4 = (struct {...} (*)[17]) 0x404080 <b>
(gdb) p &b + 1
$5 = (struct {...} (*)[17]) 0x4042a0 <calendar>
(gdb) p &b[0] + 1
$6 = (struct {...} *) 0x4040a0 <b+32>
(gdb) p &a
$7 = (int (*)[3]) 0x404060 <a>
```

1488 = 12 × 31 × 4；544 = 17 × 32（3 個 double + 1 個 double = 32 bytes）。`&b + 1` 一次跨 544 = 0x220 bytes，0x404080 + 0x220 = 0x4042a0，恰好是 `calendar` 的起點。`&b + 1` 是合法的 one-past 指標（可計算、不可解參考）；它的數值剛好等於下一個全域變數的位址，是連結器擺放的結果，規格不保證全域變數的相對順序（原文在 aarch64 上就觀察到不同的順序）。

## Address and indirection operators

N1570 **6.5.3.2** 的兩條 constraint：

> 1. The operand of the unary `&` operator shall be either a function designator, the result of a `[]` or unary `*` operator, or an lvalue that designates an object that is not a bit-field and is not declared with the `register` storage-class specifier.
> 2. The operand of the unary `*` operator shall have pointer type.

`&` 是 "address of"，不要念成 and。它能作用的對象有三種：

1. **function designator**（函式名稱），得到函式指標。
2. **`[]` 或 `*` 的結果**，這時兩者互相抵消（§3）：`&*p` 就是 `p`（即使 `p` 是空指標也不算解參考），`&a[5]` 就是 `a + 5`。註腳 102 把這寫得很清楚：「`&*E` is equivalent to `E` (even if E is a null pointer), and `&(E1[E2])` to `((E1)+(E2))`」。
3. **一個定位到 object 的 lvalue**，但不能是 bit-field，也不能是 `register` 變數。

最後一條限制故意踩一次：

```c
struct flags { unsigned ready : 1; unsigned err : 1; };
void g(void)
{
    register int r = 0;
    struct flags fl = {0};
    int *p1 = &r;               /* 錯：register 物件不能取址 */
    unsigned *p2 = &fl.ready;   /* 錯：bit-field 不能取址 */
    (void) p1; (void) p2;
}
```

```
$ gcc -c addr.c
addr.c:6:5: error: address of register variable ‘r’ requested
addr.c:7:20: error: cannot take address of bit-field ‘ready’
```

bit-field 不能取址的理由很實際：位址的最小單位是位元組，一個 1-bit 的欄位沒有自己的位址。`register` 則是程式設計師對編譯器的承諾「我不會取它的位址」，好讓它放心放進暫存器；現代編譯器自己會做暫存器配置，`register` 的實際作用就只剩這條限制。

## 對比與取捨

| 寫法 | 型別 | `sizeof` | `+1` 前進 | 能被指定嗎 |
|---|---|---|---|---|
| `int a[10]`（區域/全域） | `int[10]` | 40 | 不適用（先 decay） | 否 |
| `int a[10]`（參數） | `int *` | 8 | 4 bytes | 是 |
| `int *p` | `int *` | 8 | 4 bytes | 是 |
| `&a` | `int (*)[10]` | 8 | 40 bytes | 否（不是 lvalue） |
| `int m[3][5]` 的 `m` | decay 成 `int (*)[5]` | 60（不 decay 時） | 20 bytes | 否 |
| `int **pp` | `int **` | 8 | 8 bytes | 是 |

最後一列刻意放進來：`int m[3][5]` 不能傳給 `int **` 參數。`m` decay 成 `int (*)[5]`，記憶體裡是 15 個連續的 int；`int **` 期待的是「一排指標，每個指標指向一列」。兩者的記憶體布局完全不同，硬轉型傳進去會把 int 當位址解參考。

## 踩雷集錦

1. **「陣列就是指標常數」**：陣列是一整塊 object，記憶體裡沒有存著任何指標。`sizeof`、`&`、字串初始化三個情境看得出它的本體；`extern char *a` 接 `char a[]` 會讀錯東西然後當掉。
2. **在函式裡用 `sizeof(param)` 算陣列長度**：參數已經被調整成指標。慣用法是多傳一個長度參數，或者傳指向陣列的指標 `int (*a)[10]`（這時 `sizeof *a` 是 40）。打開 `-Wall`，gcc 會用 `-Wsizeof-array-argument` 和 `-Wsizeof-pointer-div` 提醒你。
3. **把二維陣列傳給 `int **`**：布局不同，見上表。要傳二維陣列，用 `int (*)[N]`；要動態的二維結構，要嘛配置一塊連續記憶體自己算索引，要嘛真的配置一排指標。
4. **「`&a` 和 `a` 一樣」**：位址值一樣，型別不一樣，`+1` 的步幅差 N 倍。`memset(&a, 0, sizeof a)` 和 `memset(a, 0, sizeof a)` 結果相同，是因為 `memset` 接 `void *` 把型別資訊丟了，不是因為兩者等價。
5. **用「攤平」的方式走訪多維陣列**：`x[0][i]`，`i` 從 0 跑到 14，在大多數機器上「能用」，但每一個 `i >= 5` 都是 UB。改寫成 `int *p = &x[0][0]; p[i]` 也沒有真正解決問題：`p` 指向的是 `x[0]` 這個 `int[5]` 的元素，委員會與編譯器社群對它能不能合法走到 `x[1]` 仍有爭議。最穩妥是寫雙層迴圈，或一開始就宣告一維陣列 `int flat[15]` 自己算 `i * 5 + j`。

## 進階：再往深一層

- **VLA 與 variably modified type**：C99 允許 `int a[n]`（`n` 是執行期值）。C11 把 VLA 降為選用（實作可定義 `__STDC_NO_VLA__`），C23 把「自動儲存期的 VLA 物件」維持選用、但讓「variably modified type」（例如參數 `int (*m)[cols]`）變成必須支援。後者讓你可以寫 `void f(int rows, int cols, int m[rows][cols])`，傳任意大小的二維陣列而且 `m[i][j]` 照常使用。stack 上的 VLA 大小若來自外部輸入，就是 stack overflow 的資安風險。
- **`static` 寫在陣列參數裡**：`void f(int a[static 10])` 承諾呼叫端至少傳入 10 個元素且不是空指標，編譯器可以據此警告（clang 對傳 NULL 會警告）與最佳化。
- **`char *argv[]` 與 `char **argv`**：`main` 的參數依 6.7.6.3 §7 調整，兩種寫法完全相同。`argv[argc]` 保證是空指標（5.1.2.2.1）；`envp` 第三參數則是 POSIX 慣例而非 ISO C，可攜程式用 `getenv` 或 `environ`。`argv` 和環境字串在 stack 上怎麼擺，見 [Ch 41](./41-c-runtime.md)。
- **面試題**：「`int (*p)[5]` 和 `int *p[5]` 差在哪？`sizeof` 各是多少？」答案：前者是一個指標（8），後者是 5 個指標的陣列（40）。能用 right-left rule 講出推導過程才算過關。

## 動手練習

1. 不用 cdecl，用 right-left rule 讀出下面三個宣告，再用 cdecl 對答案：`int *(*(*fp)(int))[10];`、`char (*(*x[3])())[5];`、`void (*bsd_signal(int, void (*)(int)))(int);`。
2. 寫一個函式 `void fill(int rows, int cols, int m[rows][cols])`，在 `main` 裡分別傳入 `int a[3][4]` 和 `int b[2][7]`，印出 `sizeof m[0]`，觀察它隨 `cols` 變化。
3. 把 `use.c` 改成 `extern char a[];` 但在 `def.c` 裡寫 `char *a = "ABCDEFGHI";`（反過來不匹配），預測會印出什麼，再實際跑一次驗證。
4. 在 gdb 裡對 `int m[3][5]` 分別 `p m`、`p *m`、`p **m`、`p m + 1`、`p *m + 1`，把每個結果的型別寫下來，對照本章的多維陣列圖。

## 本章重點整理

- 陣列、函式、指標是三種 derived declarator types；不能有函式的陣列、回傳陣列或函式的函式，C 一律改用指標間接表達。
- 宣告模仿使用：從識別字出發，先右後左（right-left rule），後置 `[]` `()` 比前置 `*` 優先；複雜宣告用 typedef 拆開，用 cdecl 驗證。
- array decay 的由來是「不在記憶體裡存指標」；它在 `sizeof`、單元 `&`、字串常量初始化陣列（C23 再加 `typeof`）時不發生。
- `E1[E2]` 就是 `*((E1)+(E2))`；陣列與指標只在函式參數處可互換，其他宣告處互換是 UB。
- 多維陣列是陣列的陣列，每層下標剝一層維度；越過子陣列邊界即使仍在整個 object 內也是 UB。

## 自我檢核

- [ ] 不看筆記，畫出 `int x[3][5]` 的記憶體圖，並標出 `x + 1`、`x[1] + 1`、`&x + 1` 分別指到哪裡、各自的型別。
- [ ] 如果有人問你「K&R 不是說 `char s[]` 和 `char *s` 等價嗎」，你會怎麼回答？要提到哪一條規格和哪個例外？
- [ ] 用英文念出 `signal` 的原型，並寫出等價的 typedef 版本。
- [ ] 能用 Ritchie 論文裡的 struct 例子，解釋 array decay 這條規則為什麼會被發明嗎？
- [ ] 回想你寫過的程式碼：有沒有哪裡在函式內用 `sizeof` 算陣列長度、或把二維陣列轉型成 `int **`？

## 延伸閱讀

### 論文

- **[The Development of the C Language](https://www.bell-labs.com/usr/dmr/www/chist.html)** — Dennis Ritchie，HOPL-II（1993）
  - **核心貢獻**：一手說明陣列與指標關係、宣告語法的設計理由。
  - **讀哪裡**：「Embryonic C」（array decay 的發明，含目錄項目 struct 的例子）與「Critique」（宣告語法與參數調整規則的自我批評）。
  - **和本章的關聯**：本章「歷史：為什麼會有 array decay」一節的直接來源。

### 官方文件 / Spec

- **[N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：6.2.5 §20、§24（衍生型別）、6.3.2.1 §3（array decay）、6.5.2.1（下標，§4 有 `int x[3][5]` 的完整推導）、6.5.3.2（`&` 與 `*`，連同註腳 102）、6.7.6（宣告子，6.7.6.3 §7–§8 是參數調整）、Annex J.2 中關於 `a[1][7]` 的條目。
  - **注意**：6.7.6 的文法定義很形式化，先讀 6.7.6.1–6.7.6.3 的 Semantics 段落，文法部分可以跳過。

### 書籍

- **《Expert C Programming: Deep C Secrets》** — Peter van der Linden（Prentice Hall，1994）
  - **讀哪幾章**：第 3 章「Unscrambling Declarations in C」（宣告解讀的經典，含手繪的解碼流程圖）、第 4 章「The Shocking Truth: C Arrays and Pointers Are NOT the Same!」、第 9 章「More about Arrays」、第 10 章「More about Pointers」。
  - **定位**：本章主題最經典的一本書。年代久遠（C89），但這兩個主題的規則自 C89 以來沒變。前提是會基本的 C 語法。
- **《C Traps and Pitfalls》** — Andrew Koenig（Addison-Wesley，1989）；[原始論文版 PDF](http://www.literateprogramming.com/ctraps.pdf)
  - **讀哪裡**：2.1 節「Understanding Declarations」，就是 `(*(void(*)())0)()` 的出處；3.1 節講指標與陣列。

### 部落格 / 技術文章

- **[The Descent to C](https://www.chiark.greenend.org.uk/~sgtatham/cdescent/)** — Simon Tatham（PuTTY 作者）
  - **這篇說什麼**：寫給會高階語言、要下到 C 的程式設計師，其中「Arrays」與「Pointers」兩節用記憶體圖解釋 decay 與「沒有陣列型別的參數」。
  - **讀哪裡**：談陣列與指標的那幾節（目錄裡標題含 arrays、pointers 的段落）。
  - **為什麼值得讀**：作者維護一個大型 C 專案數十年，觀點務實，和本章的規格角度互補。

陣列與函式都會「退化」成指標，但指標本身還有很多面貌：不帶型別的 `void *`、指向指標的指標、把函式當參數傳的 callback，以及加在指標上的 `const`、`volatile`、`restrict`。下一章逐一拆開。

→ [Ch 9 void *、指標的指標、function pointer 與 qualifier](./09-void-pointer-and-callbacks.md)
