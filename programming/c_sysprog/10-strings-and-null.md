# Ch 10 — 字串、null pointer、nullptr 與記憶體安全

> **目標**：搞清楚字串常量放在哪裡、為什麼不能改，分辨 `char *p = "..."` 與 `char a[] = "..."` 在記憶體與組合語言層面的差別；理解字串函式為何不檢查 null pointer；精確區分 null pointer constant、null pointer 與 `NULL` 巨集，知道 C23 `nullptr` 解決了什麼、沒解決什麼；最後看清指標自由的代價，以及 Fil-C 這類記憶體安全方案的思路。

> **對應原文**：[你所不知道的 C 語言：指標篇](https://hackmd.io/@sysprog/c-pointer) 的「重新探討字串」「Learn C The Hard Way」「Linus Torvalds 親自教你 C 語言」「Null pointer?」「C23 的 `nullptr`」「指標的代價：記憶體安全與 Fil-C」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、glibc 2.35。C23 段落以 Docker `gcc:14`（gcc 14.4）執行。輸出中的位址每次執行不同。

## 為什麼需要這個？

C 程式裡最常見的指標是 `char *`，最常見的指標值是 `NULL`。這兩者也是 C 最出名的兩個設計爭議：

- NUL 結尾的字串讓 `strlen` 只能從頭數到尾，讓 `gets` 成為史上最惡名昭彰的函式（C11 已把它從標準中刪除），也讓一整個世代的 buffer overflow 成為可能。FreeBSD 開發者 Poul-Henning Kamp 在 ACM Queue 上把它稱為〈The Most Expensive One-byte Mistake〉。
- null reference 的發明者 Tony Hoare 在 2009 年的演講中稱它為「billion-dollar mistake」。

這兩個設計都不會消失：數十年的 C 程式碼、POSIX API、系統呼叫介面全建立在它們之上。所以這一章的目標不是批評，而是**精確地知道規則**：哪些寫法是定義良好的，哪些是 UB，編譯器又會怎麼利用那些 UB。

## 先建立直覺

```
C 字串 = 一段 char 陣列 + 「遇到 '\0' 就結束」的約定

   p ─────┐
          ▼
        ┌───┬───┬───┬───┬───┬────┐
        │ h │ e │ l │ l │ o │ \0 │      長度沒有存在任何地方
        └───┴───┴───┴───┴───┴────┘      strlen 要一路數到 \0
          0x402008（.rodata，唯讀）

null pointer = 「保證不指向任何 object 或函式」的指標值

   q ──► ╳        不能解參考；可以比較；可以傳給 free
```

C 語言本身沒有字串型別。「字串」是標準函式庫與字串常量語法共同遵守的一個**約定**：一個 `char` 陣列，內容以值為 0 的位元組結尾。指向字串的指標只記得起點，長度要靠走到 `'\0'` 才知道。這個約定的所有優點（簡單、零額外空間）和缺點（`O(n)` 求長度、忘了 `'\0'` 就越界）都從這裡來。

## 重新探討字串

### 兩種字串

從 C99 起，C 有兩種字串：

1. **byte string**：以 `char` 為單位，`char` 至少 8 bits。`<string.h>` 的 `strlen`、`strcpy` 處理的就是它。
2. **wide string**：以 `wchar_t` 為單位，`L"..."` 字面寫法，`<wchar.h>` 的 `wcslen` 等函式處理。`wchar_t` 的寬度由實作決定（Linux 上是 32 bits，Windows 上是 16 bits）。

今天的 Linux 程式多半把 UTF-8 放在 byte string 裡處理，而 UTF-8 的發明者正是 Unix 的 Rob Pike 與 Ken Thompson。原文引用 Wikipedia 的提醒：原始碼中的非 ASCII 字串常量（例如 `"φωωβαρ"`）怎麼被編碼，是 implementation-defined 的，可能要求原始檔用同一種編碼。C11 加了 `u8"..."`、`u"..."`、`U"..."`；`u8` 字串的元素型別在 C11/C17 是 `char`，C23 改成 `char8_t`。

### 字串常量住在哪裡

N1570 **6.4.5 §6**：字串常量的多位元組字元序列會被用來初始化「an array of static storage duration and length just sufficient to contain the sequence」。所以 `"hello"` 是一個型別為 `char[6]`、static storage duration 的陣列 object（[Ch 7](./07-pointers-by-the-standard.md) 說它是無名的 lvalue）。

**6.4.5 §7** 接著說：

> It is unspecified whether these arrays are distinct provided their elements have the appropriate values. If the program attempts to modify such an array, the behavior is undefined.

兩句話各有後果。用一支程式驗證：

```c
#include <stdio.h>

int main(void)
{
    char *p = "hello";          /* p 指向 static storage 中的字串常量 */
    char  a[] = "hello";        /* a 是 6 個 char 的區域陣列，內容是複製來的 */
    const char *q = "hello";    /* 同內容的另一個字串常量 */

    printf("sizeof p = %zu, sizeof a = %zu\n", sizeof p, sizeof a);
    printf("p = %p\nq = %p\na = %p\n", (void *) p, (void *) q, (void *) a);
    printf("p == q ? %s\n", p == q ? "yes (被合併)" : "no");

    a[0] = 'J';                 /* OK：改的是自己的副本 */
    printf("a = %s\n", a);
    fflush(stdout);
    p[0] = 'J';                 /* UB：寫入字串常量 */
    printf("p = %s\n", p);
    return 0;
}
```

```
$ gcc -Wall -Wextra -g -no-pie lit.c -o lit && ./lit; echo "exit=$?"
sizeof p = 8, sizeof a = 6
p = 0x402008
q = 0x402008
a = 0x7ffdb41ec2e2
p == q ? yes (被合併)
a = Jello
Segmentation fault (core dumped)
exit=139
```

- **`p == q`**：兩個 `"hello"` 被合併成同一份。這就是「unspecified whether these arrays are distinct」：規格允許合併也允許不合併，程式不能依賴任一種。
- **`p[0] = 'J'` 當掉**：gcc 把字串常量放在 `.rodata` 區段，載入時映射成唯讀頁面，寫入觸發 page fault，核心送出 SIGSEGV。

確認字串真的在 `.rodata`：

```
$ objdump -s -j .rodata lit
Contents of section .rodata:
 402000 01000200 00000000 68656c6c 6f000000  ........hello...
 402010 73697a65 6f662070 203d2025 7a752c20  sizeof p = %zu,
$ readelf -S -W lit | grep -E "Name|\.rodata"
  [Nr] Name              Type            Address          Off    Size   ES Flg Lk Inf Al
  [17] .rodata           PROGBITS        0000000000402000 002000 000076 00   A  0   0  8
```

`68 65 6c 6c 6f 00` 就是 `hello\0`，位址 0x402008 與程式印出的 `p` 吻合。Flg 欄只有 `A`（allocate），沒有 `W`（write），所以載入後是唯讀的。

注意：在 C 裡，字串常量的型別是 `char[N]`，**不是** `const char[N]`（C++ 才是 const），所以 `char *p = "hello";` 合法、不會警告，寫入它也不會有編譯錯誤。這是為了相容 `const` 出現之前的大量舊程式碼。想讓編譯器幫忙抓，加 `-Wwrite-strings`，它讓字串常量的型別在警告檢查時被當成 `const char[N]`：

```
$ gcc -Wall -Wwrite-strings -c lit.c
lit.c:5:15: warning: initialization discards ‘const’ qualifier from pointer target type [-Wdiscarded-qualifiers]
    5 |     char *p = "hello";          /* p 指向 static storage 中的字串常量 */
```

好習慣是指向字串常量的指標一律宣告成 `const char *`。

### `char *p` vs `char a[]`：組合語言裡的差別

兩種寫法看起來只差一個符號，編譯器做的事完全不同：

```c
void use(char *);
void f(void) { char a[] = "hello"; use(a); }
void g(void) { char *p = "hello"; use(p); }
```

```
$ gcc -O0 -fno-stack-protector -S -o - init.c
f:
	...
	movl	$1819043176, -6(%rbp)    # 0x6c6c6568 = "hell"（little-endian）
	movw	$111, -2(%rbp)           # 0x006f     = "o\0"
	leaq	-6(%rbp), %rax           # a 的位址（在 stack 上）
	movq	%rax, %rdi
	call	use@PLT
	...
g:
	...
	leaq	.LC0(%rip), %rax         # .rodata 中 "hello" 的位址
	movq	%rax, -8(%rbp)           # 存進 p 這個 8 bytes 的指標變數
	movq	-8(%rbp), %rax
	movq	%rax, %rdi
	call	use@PLT
```

`f` 的字串根本沒有從 `.rodata` 讀：編譯器把 6 個位元組拆成一個 4-byte 立即值和一個 2-byte 立即值，**直接寫進 stack 上的陣列**。這就是 [Ch 8](./08-arrays-functions-pointers.md) 說的 array decay 第三個例外：「用字串常量初始化陣列」時，字串常量不會變成指標，它的內容被複製進陣列。`g` 則只是把 `.rodata` 裡那份的位址存進指標。

| | `char *p = "hello";` | `char a[] = "hello";`（函式內） |
|---|---|---|
| 配置了什麼 | 一個 8 bytes 的指標變數 | 一個 6 bytes 的 `char` 陣列 |
| 字串內容在哪 | `.rodata`（static storage，唯讀） | stack（automatic storage，可寫） |
| `sizeof` | 8 | 6 |
| 能不能改內容 | 不能（UB，通常 SIGSEGV） | 能 |
| 能不能讓它指向別處 | 能（`p = other;`） | 不能（陣列不是 modifiable lvalue） |
| 能不能 `return` 給呼叫端 | 能，字串常量活到程式結束 | 不能，函式返回後陣列就死了 |

最後一列直接驗證：

```c
#include <stdio.h>
static char *greet_bad(void)  { char buf[] = "hi there"; return buf; }
static const char *greet_lit(void) { return "hi there"; }   /* static storage：安全 */
static char *greet_static(void) { static char buf[] = "hi there"; return buf; }
int main(void)
{
    printf("[%s]\n", greet_lit());
    printf("[%s]\n", greet_static());
    char *p = greet_bad();
    printf("p = %p\n", (void *) p);
    return 0;
}
```

```
$ gcc -Wall -Wextra retarr.c -o retarr && ./retarr
retarr.c:2:65: warning: function returns address of local variable [-Wreturn-local-addr]
[hi there]
[hi there]
p = (nil)
```

和 [Ch 7](./07-pointers-by-the-standard.md) 的 `dangling_local` 一樣，gcc 把回傳區域陣列位址改成回傳 0。`greet_static` 的 `static` 陣列是 static storage duration，活到程式結束，可以安全回傳（代價是不可重入：每次呼叫都共用同一塊）。

### 字串函式不收 null pointer

`<string.h>` 的函式能不能接受 `NULL`？`strlen(NULL)` 該回傳 0 嗎？規格的答案是：**不能，傳了就是 UB**。依據有兩條：

- N1570 **7.24.1 §1**：「in all cases a `char *` or `void *` argument points to the initial (lowest addressed) character of the array. If an array is accessed beyond the end of an object, the behavior is undefined.」null pointer 不指向任何陣列。
- N1570 **7.1.4 §1**（適用於所有標準函式庫函式）：如果引數是無效值，例如「a pointer outside the address space of the program, or a null pointer」，行為未定義，除非個別函式另有說明。

既然是 UB，函式庫就沒有義務檢查。glibc 的 `strlen` 不檢查，代價由呼叫端承擔：

```c
#include <stdio.h>
#include <string.h>
int main(void)
{
    const char *s = NULL;
    printf("about to strlen(NULL)\n");
    fflush(stdout);
    printf("%zu\n", strlen(s));
    return 0;
}
```

```
$ gcc -O0 -Wall -Wextra nul.c -o nul && ./nul; echo "exit=$?"
about to strlen(NULL)
Segmentation fault (core dumped)
exit=139
```

當 null 是一個編譯期就看得出來的常數時，gcc 會警告，因為 glibc 的標頭檔替 `strlen` 加了 `nonnull` 屬性：

```
$ gcc -O0 -Wall -c nul2.c        # 內容：size_t f(void) { return strlen(NULL); }
nul2.c:3:25: warning: argument 1 null where non-null expected [-Wnonnull]
/usr/include/string.h:407:15: note: in a call to function ‘strlen’ declared ‘nonnull’
```

`nonnull` 屬性還有另一面：編譯器可以據此**假設**引數不是 null，刪掉呼叫之後的 null 檢查。這和後面「null 檢查被刪除」一節是同一類現象。

另一個例外值得記住：`free(NULL)` 是明確定義為「什麼都不做」的（7.22.3.3），所以 `free` 之前不必檢查。

### 字串操作的經典錯誤

原文用「把 `s` 和 `t` 串接起來」這個任務，示範一連串常見錯誤。第一種，目的地根本沒有空間：

```c
#include <string.h>
void f(const char *s) { char *r; strcpy(r, s); }
```

```
$ gcc -O2 -Wall -c uninit.c
uninit.c:2:34: warning: ‘r’ is used uninitialized [-Wuninitialized]
```

`r` 是一個沒初始化的指標，`strcpy` 會寫到一個隨機位址。第二種，用 VLA `char r[strlen(s) + strlen(t)]`：少了 `'\0'` 的一個位元組，兩個字串都空時長度為 0（C 不允許長度 0 的 VLA），而且大小來自外部輸入時會造成 stack overflow。第三種，用 `malloc` 但一樣少算 1：

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char *concat_bad(const char *s, const char *t)
{
    char *r = malloc(strlen(s) + strlen(t));        /* 少了 '\0' 的 1 byte */
    if (!r) return NULL;
    strcpy(r, s);
    strcat(r, t);
    return r;
}

static char *concat(const char *s, const char *t)
{
    size_t ls = strlen(s), lt = strlen(t);
    char *r = malloc(ls + lt + 1);
    if (!r) return NULL;
    memcpy(r, s, ls);
    memcpy(r + ls, t, lt + 1);                      /* 連同 '\0' 一起複製 */
    return r;
}

int main(int argc, char **argv)
{
    char *r = (argc > 1) ? concat_bad("hello, ", "world") : concat("hello, ", "world");
    printf("%s\n", r);
    free(r);
    return 0;
}
```

```
$ gcc -g -fsanitize=address concat.c -o concat && ./concat
hello, world
$ ./concat bad
==13660==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x50200000001c ...
WRITE of size 6 at 0x50200000001c thread T0
    #0 0x7164dc63b018 in __interceptor_strcat ...
    #1 0x5e2c92abc2fb in concat_bad /home/ypp/c_sysprog_lab/p2a/ch10/concat.c:10
0x50200000001c is located 0 bytes to the right of 12-byte region [0x502000000010,0x50200000001c)
```

`"hello, "` 7 個字 + `"world"` 5 個字 = 12，`malloc(12)` 放不下結尾的 `'\0'`。沒開 ASan 時，這個程式通常「正常」印出結果：多寫的 1 byte 落在 malloc 配置單位的 padding 裡（[Ch 17](./17-malloc-internals.md)），一般執行看不出問題。這正是 off-by-one 危險的地方。

正確版本有三個要點：`+ 1` 給 `'\0'`；檢查 `malloc` 回傳值；長度已知時用 `memcpy` 而不是 `strcpy`／`strcat`（`strcat` 每次都要從頭找 `'\0'`，串接 n 個字串會變成 O(n²)）。`strdup(s)` 就是「`malloc(strlen(s) + 1)` 再複製」的包裝，回傳的指標要記得 `free`，而且它也可能回傳 `NULL`。

### NUL 結尾的代價

原文引用 Poul-Henning Kamp 的文章與 Zed Shaw《Learn C The Hard Way》Exercise 36「Why C Strings Were A Horrible Idea」，把代價分成三類：

| 代價 | 說明 |
|---|---|
| 效能 | 取得長度是 O(n)。Pascal 式「長度前綴」字串是 O(1)；Kamp 舉例說 Visual BASIC 取字串長度比 C 快。 |
| 編譯器開發 | 不知道長度就難以最佳化；編譯器要特別辨認「長度可推知」的情況（例如複製字串常量），把 `strcpy` 換成有平台最佳化的 `memcpy`，這類特例處理本身就是維護成本。 |
| 安全 | 無法在不冒著越界風險的情況下驗證一個字串（Zed Shaw：「it is not possible to validate a C string without possibly looping infinitely」）。`gets` 無法限制長度，C11 已將它刪除。 |

替代方案在實務上已經很普及：`strnlen`、`snprintf`（回傳「需要的長度」，可據此判斷是否截斷）、BSD 的 `strlcpy`／`strlcat`（glibc 2.38 起也提供）、以及許多專案自帶的「長度 + 指標」字串型別（例如 Redis 的 SDS、Linux 核心的 `seq_buf`）。

## Linus Torvalds 的 C 教學：指標的指標與品味

原文在這裡收錄了 Linus 的幾段 C 教學。陣列參數那段已在 [Ch 8](./08-arrays-functions-pointers.md) 談過；另一段更有名的，是他在 TED 訪談裡用「從單向鏈結串列刪除節點」說明什麼叫 good taste。直覺寫法要替「刪的是開頭節點」寫特例：

```c
node *prev = NULL, *walk = *headp;
for (; walk; prev = walk, walk = walk->next)
    if (walk == target) {
        if (prev) prev->next = walk->next;   /* 中段 */
        else      *headp = walk->next;       /* 開頭節點特例 */
        return;
    }
```

改用 [Ch 9](./09-void-pointer-and-callbacks.md) 的指標的指標，讓 `pp` 指向「指向目前節點的那個欄位」，特例就消失了：

```c
node **pp = headp;                 /* pp 指向「指向目前節點的那個欄位」 */
while (*pp && *pp != target)
    pp = &(*pp)->next;
if (*pp)
    *pp = target->next;            /* 開頭與中段是同一道寫入 */
```

```
rm head : 2 3
rm tail : 2
rm none : 2
rm last :
```

（完整測試程式在 ASan 下跑過：刪開頭、刪尾端、刪不存在的節點、刪到空串列都正確。）

```
pp 一開始指向 head 變數本身，之後指向前一個節點的 next 欄位：

  head ──► [1|next] ──► [2|next] ──► [3|NULL]
   ▲            ▲
   pp(初始)     pp(第二步)

*pp = target->next 對 head 和對某個 next 欄位是同一個動作
```

`pp` 不在乎它指的是 `head` 還是某個節點的 `next`，兩者都是「一個 `node *` 型別的 object」，所以寫入的方式完全相同。用資料結構消化掉特例，就是 Linus 說的品味。這個手法、它在 Linux 核心中的實例，以及 `struct list_head` 的雙向版本，在 [Ch 18](./18-linked-list-pointer-to-pointer.md) 與 [Ch 19](./19-linux-list-head.md) 完整展開。

## Null pointer

順帶一提發音：null 念 /nʌl/。

### null pointer constant、null pointer、NULL

原文的第一個小實驗：

```c
int main() { return (NULL == 0); }
```

沒有 `#include <stddef.h>` 時，gcc 報 `'NULL' undeclared`：`NULL` 是標準函式庫的巨集，不是關鍵字。加上 include 後，回傳值永遠是 1。為什麼？要分清三個概念，依據是 N1570 **6.3.2.3 §3–§4**：

> An integer constant expression with the value 0, or such an expression cast to type `void *`, is called a **null pointer constant**. If a null pointer constant is converted to a pointer type, the resulting pointer, called a **null pointer**, is guaranteed to compare unequal to a pointer to any object or function.
>
> Conversion of a null pointer to another pointer type yields a null pointer of that type. Any two null pointers shall compare equal.

| 概念 | 是什麼 | 例子 |
|---|---|---|
| null pointer constant | **原始碼層面**的一種運算式：值為 0 的整數常數運算式，或它轉型成 `void *` | `0`、`0L`、`'\0'`、`(void *) 0`、`(1 - 1)` |
| null pointer | **執行期**的一種指標值：保證和任何 object、函式的指標都不相等 | `int *p = 0;` 之後 `p` 的值 |
| `NULL` | `<stddef.h>` 等標頭定義的巨集，展開成某個 null pointer constant | gcc 的 C：`((void *)0)` |

`NULL == 0` 裡，兩邊都是 null pointer constant，比較時都代表 null pointer，所以相等。這**和 null pointer 的位元表示是不是全 0 無關**：規格不要求 null pointer 的位元是全 0（確實有古老平台不是），編譯器負責在「把常數 0 轉成指標」時產生該平台的 null 表示法。所以 `memset(&p, 0, sizeof p)` 不保證讓 `p` 成為 null pointer，`int *p = 0;` 才保證（在所有主流平台上兩者結果相同）。

看 gcc 實際怎麼定義 `NULL`，以及它在 `_Generic` 裡的型別：

```
$ echo "#include <stddef.h>" | gcc -E -dM - | grep -w NULL
#define NULL ((void *)0)
$ grep -n -A6 "define NULL" /usr/lib/gcc/x86_64-linux-gnu/11/include/stddef.h
392:#define NULL __null
393-#else   /* G++ */
394-#ifndef __cplusplus
395:#define NULL ((void *)0)
396-#else   /* C++ */
397:#define NULL 0
```

```c
#include <stdio.h>
#include <stddef.h>
#define TYPE(x) _Generic((x), void *: "void *", int: "int", long: "long", default: "other")
int main(void)
{
    printf("NULL == 0 -> %d\n", NULL == 0);
    printf("type of NULL: %s, type of 0: %s\n", TYPE(NULL), TYPE(0));
    printf("sizeof NULL = %zu, sizeof 0 = %zu\n", sizeof NULL, sizeof 0);
    int *a = NULL; char *b = 0; void *c = (void *) 0;
    printf("a == (int *) b ? %d, a == c ? %d\n", a == (int *) b, a == c);
    if (!a) puts("!a is true (pointer is a scalar)");
    return 0;
}
```

```
$ gcc -Wall -Wextra nulldef.c -o nulldef && ./nulldef
NULL == 0 -> 1
type of NULL: void *, type of 0: int
sizeof NULL = 8, sizeof 0 = 4
a == (int *) b ? 1, a == c ? 1
!a is true (pointer is a scalar)
```

在 gcc + glibc 的 C 模式下 `NULL` 是 `void *`，但**規格允許 `NULL` 展開成 `0`**，在 C++ 模式下 g++ 用的是內建的 `__null`。程式不能依賴 `NULL` 的型別，這就是 C23 引入 `nullptr` 的原因。最後一行 `if (!a)` 合法，是因為指標屬於 scalar type（[Ch 7](./07-pointers-by-the-standard.md)），條件運算式只要求 scalar；`assert(p)` 也一樣。

### 兩個 null pointer 能不能比較

能，而且一定相等（6.3.2.3 §4 與 6.5.9 §6）。容易混淆的是另一條：**relational** 運算子（`<`、`>`、`<=`、`>=`）依 6.5.8 §5，只在兩個指標指向同一個 object、同一個陣列（含尾端後一位置）或同一個 aggregate 的成員時才有定義，其他情況是 UB。所以 `p == NULL` 永遠有定義，`p < NULL` 則沒有。原文特別提醒：不能拿 6.5.8 的「排序未定義」去推論 `==` 的結果，兩者是不同的條款。

### variadic 函式與 NULL

`NULL` 的型別不固定，在 variadic 函式裡會出事。`execl` 用一個 null pointer 當作引數列的結尾（sentinel）：

```c
#include <unistd.h>
#include <stddef.h>
void run(void)
{
    execl("/bin/echo", "echo", "hi", 0);        /* 0 是 int，不是指標 */
    execl("/bin/echo", "echo", "hi", NULL);     /* glibc C 的 NULL 是 ((void *)0) */
    execl("/bin/echo", "echo", "hi", (char *) 0);  /* 最可攜的寫法 */
}
```

```
$ gcc -Wall -c sentinel.c
sentinel.c:5:5: warning: missing sentinel in function call [-Wformat=]
```

`...` 的引數不會被轉成指標型別，`0` 以 `int` 傳遞（4 bytes），而 `execl` 內部用 `va_arg(ap, char *)` 讀 8 bytes，這是 UB（7.16.1.1：型別不相符）。在 x86-64 上，前 6 個整數引數走暫存器，`int` 寫進 32 位元暫存器時高位會被清零，所以常常「剛好能動」；放到 stack 上的第 7 個以後的引數就不一定了。gcc 的 `sentinel` 屬性能抓到 `0`，但抓不到「`NULL` 被定義成 `0` 的平台」上的 `NULL`。`(char *) 0` 是唯一在所有平台都正確的寫法。

### null 解參考與編譯器：被刪掉的檢查

解參考 null pointer 是 UB（6.5.3.2 §4 的註腳 102 把 null pointer 列為無效值）。編譯器據此推論：**如果一個指標已經被解參考過，它就不可能是 null**。

```c
struct tun { int flags; };
int poll(struct tun *tun)
{
    int flags = tun->flags;     /* 先解參考 */
    if (!tun)                   /* 再檢查：編譯器推論 tun 不可能是 NULL */
        return -1;
    return flags;
}
```

```
-- -O2
poll:
	endbr64
	movl	(%rdi), %eax
	ret
-- -O2 -fno-delete-null-pointer-checks
poll:
	endbr64
	testq	%rdi, %rdi
	movl	$-1, %eax
	cmovne	(%rdi), %eax
	ret
```

`-O2` 下 `if (!tun)` 整個消失。這不是虛構的例子：2009 年 Linux 核心的 tun 驅動程式就有幾乎一樣的程式碼（CVE-2009-1897），gcc 刪掉了 null 檢查；而在當時，攻擊者可以透過 `mmap` 把位址 0 映射成自己控制的資料，於是 null 解參考變成可利用的漏洞。此後 Linux 核心以 `-fno-delete-null-pointer-checks` 編譯，`vm.mmap_min_addr` 也禁止一般行程映射低位址。

看第二段組合語言要小心：`cmovne (%rdi), %eax` 的記憶體運算元在 x86 上**不論條件是否成立都會被讀取**。這裡沒有問題，是因為原始碼本來就先解參考了；這個旗標只是保留了檢查，真正的修正是**先檢查、再解參考**。

> 編譯器利用 UB 做推論的完整討論在 [Ch 34](./34-undefined-behavior.md)。

### offsetof 與 container_of

傳統的 `offsetof` 實作是這樣：

```c
#define offsetof(st, m) ((size_t)&(((st *)0)->m))
```

對 null pointer「取成員再取址」，在很多編譯器上算得出正確的偏移量，但依規格是 UB。原文仔細區分了一個特例：6.5.3.2 §3 規定 `&*p` 中的 `&` 和 `*` 互相抵消、都不求值，所以 `&*p` 即使 `p` 是 null 也合法；但 `&((st *)0)->m` 的 `&` 作用在**成員運算式**上，不是作用在 `*p` 上，沒有抵消可言，而 `*(st *)0` 這個 lvalue 並沒有定位到任何 object（6.3.2.1 §1）。所以 gcc 改用內建函式：

```
$ echo "#include <stddef.h>" | gcc -E -dM - | grep offsetof
#define offsetof(TYPE,MEMBER) __builtin_offsetof (TYPE, MEMBER)
```

Linux 核心在 `offsetof` 之上做了反向操作 `container_of`：給定成員的位址，找回外層 struct 的起點：

```c
#include <stdio.h>
#include <stddef.h>

#define my_offsetof(st, m) ((size_t) &(((st *) 0)->m))   /* 土炮版：對 null 取成員，UB */
#define container_of(ptr, type, member) ({                  \
    const typeof(((type *) 0)->member) *__mptr = (ptr);     \
    (type *) ((char *) __mptr - offsetof(type, member)); })

struct list_head { struct list_head *next, *prev; };
struct task {
    int pid;
    char comm[16];
    struct list_head node;      /* 嵌入的串列節點 */
};

int main(void)
{
    printf("offsetof(task, node)    = %zu\n", offsetof(struct task, node));
    printf("my_offsetof(task, node) = %zu\n", my_offsetof(struct task, node));
    struct task t = { .pid = 42, .comm = "kworker" };
    struct list_head *lh = &t.node;                      /* 只拿到成員指標 */
    struct task *back = container_of(lh, struct task, node);
    printf("back == &t ? %d, pid = %d, comm = %s\n", back == &t, back->pid, back->comm);
    return 0;
}
```

```
$ gcc -Wall -Wextra offs.c -o offs && ./offs
offsetof(task, node)    = 24
my_offsetof(task, node) = 24
back == &t ? 1, pid = 42, comm = kworker
```

24 = `pid` 4 bytes + `comm` 16 bytes + 4 bytes padding（讓 `node` 對齊到 8）。土炮版在這裡算出相同的值，加上 `-fsanitize=undefined` 也沒有報錯：gcc 在編譯期就把整個運算式折成常數 24，UBSan 沒有機會在執行期檢查。**「工具沒報錯」不等於「沒有 UB」**。`container_of` 裡的 `((type *) 0)->member` 只出現在 `typeof` 裡，不會被求值，那是安全的用法。`typeof` 在 C23 成為標準關鍵字，`({ ... })` statement expression 仍是 GNU 擴充。

> `container_of` 怎麼撐起整個 Linux 核心的鏈結串列與物件模型，見 [Ch 19](./19-linux-list-head.md)。

### C23 的 `nullptr`

`NULL` 的問題在於它只是一個巨集，展開成 `0`、`0L` 或 `((void *)0)` 都合法，型別不固定。C23 引入關鍵字 `nullptr`（N3220 6.4.4.6），型別是 `nullptr_t`（定義在 `<stddef.h>`，N3220 7.21.2）。以下在 Docker `gcc:14` 用 `-std=c23` 實測：

```c
#include <stdio.h>
#include <stddef.h>
#include <stdarg.h>

#define TYPE(x) _Generic((x), nullptr_t: "nullptr_t", void *: "void *", int: "int", default: "other")

static void print_all(const char *first, ...)
{
    va_list ap;
    va_start(ap, first);
    for (const char *s = first; s != nullptr; s = va_arg(ap, const char *))
        printf("%s ", s);
    va_end(ap);
    printf("\n");
}

int main(void)
{
    int *p = nullptr;
    bool b = nullptr;              /* 轉成 false */
    printf("TYPE(nullptr) = %s, TYPE(NULL) = %s\n", TYPE(nullptr), TYPE(NULL));
    printf("sizeof(nullptr_t) = %zu, alignof = %zu\n", sizeof(nullptr_t), alignof(nullptr_t));
    printf("p == nullptr ? %d, b = %d\n", p == nullptr, b);
    print_all("alpha", "beta", "gamma", nullptr);
    return 0;
}
```

```
$ gcc -std=c23 -Wall -Wextra np.c -o np && ./np       # gcc 14.4（Docker gcc:14）
TYPE(nullptr) = nullptr_t, TYPE(NULL) = void *
sizeof(nullptr_t) = 8, alignof = 8
p == nullptr ? 1, b = 0
alpha beta gamma
```

`nullptr` 可以轉成任何指標型別或 `bool`，但**不能轉成整數**：

```
$ gcc -std=c23 -c np_bad.c       # 內容：int i = nullptr;
np_bad.c:2:23: error: incompatible types when initializing type 'int' using type 'nullptr_t'
```

要留意 `nullptr` 解決的範圍：

- 它消除了 `NULL` 的**型別歧義**，不改變 null pointer 的**位元表示**（仍不保證全 0）。
- variadic 情境下，`nullptr` 以 `nullptr_t` 傳遞，不會自動變成接收端想要的指標型別。N3220 7.16.1.1 明確允許以 `va_arg` 取成 `void *` 或字元指標型別（上面的 `const char *` 就屬此類）；其他指標型別（例如 `int *`）不在保證內，可攜寫法仍是 `(int *) nullptr`。
- C23 也把 null pointer constant 的定義擴充成三種：值為 0 的整數常數運算式、它轉型成 `void *`、以及 `nullptr`。`NULL` 仍保留以維持相容。

跨語言比較：

| 語言 | 空值 | 型別 | 解參考空值 |
|---|---|---|---|
| C23 | `nullptr`（舊有 `NULL`） | `nullptr_t` | UB，編譯期通常不擋 |
| C++11 | `nullptr`（舊有 `NULL`／`0`） | `std::nullptr_t` | UB |
| Java | `null` | null type，可指派給任何參照型別 | 定義良好：拋出 `NullPointerException` |
| Go | `nil` | 依上下文 | 定義良好：runtime panic（[Ch 9](./09-void-pointer-and-callbacks.md) 實測過） |
| Rust | 參照不可為空；缺值用 `Option<T>` | — | 安全 Rust 中無法發生（raw pointer 仍可為空） |

C++ 在 2011 年先引入 `nullptr`，C23（ISO/IEC 9899:2024）跟進，連型別名稱都一樣。關鍵差異在最後一欄：C/C++ 把空值解參考留給 UB，換取零執行期成本；Java、Go 保證檢查，換取定義良好的錯誤。

## 指標的代價：記憶體安全與 Fil-C

這四章反覆強調：指標就是位址，可以任意轉型、位移、存進整數再還原。這份自由的另一面就是 C 長年的漏洞來源：越界存取、懸掛指標、use-after-free、double free。Olve Maudal 的投影片〈Insecure coding in C (and C++)〉收集了大量這類危險且未定義的程式碼。

傳統的對策有三條路：

| 路線 | 做法 | 限制 |
|---|---|---|
| 限縮語言子集 | MISRA C 等規範禁止或約束多種指標用法，用靜態分析與人工審查執行 | 靠紀律，無法擋住所有錯誤 |
| 換語言 | Rust 以所有權模型在編譯期排除大部分記憶體錯誤 | 既有的大型 C/C++ 程式碼改寫成本極高 |
| 執行期檢查 | ASan（開發期）、硬體方案（如 Arm MTE、CHERI）、或安全的 C 實作 | 效能與相容性成本 |

[Fil-C](https://fil-c.org/)（Filip Pizlo 發起）走的是第三條路裡最激進的一種：一個基於 Clang 的 C/C++ 實作，目標是讓既有程式「零修改或少量修改」重新編譯後就取得記憶體安全。依原文與官網的描述，兩個核心機制是：

- **InvisiCaps（invisible capabilities）**：每個指標在 C 看得見的位址之外，另外帶著一份看不見的 capability，記錄它所屬配置的邊界等資訊。每次存取都依 capability 檢查是否越界；憑空捏造的指標沒有 capability，存取會被攔下。
- **FUGC（Fil's Unbelievable Garbage Collector）**：並行運作、不需要全域 stop-the-world 的垃圾收集器。`free()` 立即把物件標記為已釋放，之後的存取會觸發 trap；等 GC 確認沒有人還指著它，才真正回收。這把 use-after-free 從「讀到別人的資料」變成「確定的錯誤」。

Fil-C 的邊界剛好呼應這幾章的主題：把指標轉成 `uintptr_t` 做位元運算再轉回來（例如在低位元藏旗標，只要還原後指標本體不變）仍可運作；真正越界、使用已釋放的物件、或像 [Ch 7](./07-pointers-by-the-standard.md) 的 `*(int32_t * const) 0x67a9` 那樣憑空構造指標，才是它要擋的。它保障的是空間與時間上的記憶體安全，**不**處理 strict aliasing 這類型別層面的 UB。

代價是效能與相容性：原文引述的額外開銷約為原生 C 的 1.5 到 4 倍時間（依工作負載而異；Fil-C 官網首頁沒有列出數字，本課也**未實測**）；Fil-C 刻意不與傳統 ABI 相容，避免和未檢查的程式碼混用而破壞安全假設。官網列出可以良好運作的程式包括 CPython、OpenSSH、GNU Emacs 與 Wayland，還有一套以 Fil-C 建構的 Linux 使用者空間 Pizlix。

> **本節未實測**：Fil-C 需要另外安裝其工具鏈，本課環境沒有安裝。上面的機制描述來自原文與 [fil-c.org](https://fil-c.org/)，行為細節請以官方文件為準。

指標給了 C 直面硬體的自由，記憶體安全是在保留這份自由與防堵它的代價之間持續拉鋸。本課後面的 [Ch 14](./14-function-call-security.md)（buffer overflow 與 ROP）、[Ch 17](./17-malloc-internals.md)（heap 的內部結構）、[Ch 34](./34-undefined-behavior.md)（UB）會從攻擊與防禦兩面繼續這個主題。

## 對比與取捨

| 寫法 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| `const char *s = "..."` | 零複製，活到程式結束，可安全回傳 | 不能改 | 訊息字串、查表 |
| `char s[] = "..."`（函式內） | 可改，每次呼叫一份新的 | 每次進入函式都要複製；不能回傳 | 需要就地修改的暫存字串（如 `strtok` 的輸入） |
| `static char s[] = "..."` | 可改、可回傳 | 所有呼叫共用，不可重入、不是 thread-safe | 舊式 API（`asctime`、`strerror` 的某些實作） |
| `malloc` + 複製（`strdup`） | 大小執行期決定，可回傳 | 要檢查失敗、要記得 `free` | 長度不定的字串 |
| `NULL` | 相容所有 C 版本 | 型別由實作決定 | C17 以前的程式碼 |
| `nullptr` | 型別明確，不能誤當整數 | 需要 C23 編譯器（gcc 13+、clang 16+ 一帶） | 新程式碼 |

## 踩雷集錦

1. **「字串常量是 `const char *`」**：在 C 裡它是 `char[N]`，所以 `char *p = "hi"; p[0] = 'H';` 編得過也不警告，執行時才 SIGSEGV（或在沒有唯讀保護的平台上安靜地改壞所有共用這個字串常量的地方）。加 `-Wwrite-strings`，並一律寫 `const char *`。
2. **「`strlen(NULL)` 應該回傳 0」**：規格說是 UB，glibc 直接 segfault。函式庫不替你檢查，是因為檢查成本由所有正確的呼叫者分攤，規格選擇把責任放在呼叫端。
3. **忘了 `'\0'` 的那 1 byte**：`malloc(strlen(s))` 是最經典的 off-by-one，一般執行常常看不出來。開發期一律跑 ASan。
4. **「null pointer 的位元就是全 0」**：在所有主流平台上是，但規格不保證。`calloc` 出來的指標欄位、`memset` 清零的 struct 裡的指標，嚴格說不保證是 null。主流平台上這不會出事，但要知道它是平台假設，不是語言保證。
5. **先解參考再檢查 null**：編譯器會刪掉後面的檢查（CVE-2009-1897）。檢查一定要寫在第一次解參考之前。
6. **variadic 函式的 sentinel 寫 `0` 或 `NULL`**：用 `(char *) 0` 或（C23）明確轉型的 `nullptr`。

## 進階：再往深一層

- **字串常量合併與 `-fmerge-constants`**：gcc 會在同一個編譯單元內合併相同的字串常量，並把字串常量放進帶 `SHF_MERGE | SHF_STRINGS` 旗標的區段，讓連結器有機會跨檔案合併（GNU ld 在最佳化選項下還可能讓 `"lo"` 共用 `"hello"` 的尾端；實際行為請用練習 4 的方法在你的工具鏈上確認）。這是為什麼規格說「unspecified whether these arrays are distinct」。
- **`nonnull` 與 `returns_nonnull` 屬性**：glibc 標頭大量使用 `__attribute__((nonnull))`。它同時是警告來源和最佳化依據，後者可能刪掉你防禦性的 null 檢查，和本章 `poll` 的例子同源。
- **C23 的其他相關變化**：`u8` 字串改用 `char8_t`；`memset_explicit`（不會被最佳化掉的清零，用於清除密碼）；`strdup`／`strndup` 正式進入標準。
- **硬體記憶體安全**：Arm MTE 在指標高位元放 tag、記憶體旁放對應 tag，存取時比對；CHERI 把指標換成帶邊界與權限的 128-bit capability。兩者都和 Fil-C 的 InvisiCaps 有相似的思路，差別在由硬體還是編譯器執行檢查。
- **面試題**：「`char *s = "abc"; char t[] = "abc";` 的 `sizeof`、能否修改、存放位置、能否回傳」是嵌入式與系統職缺的高頻題，本章的對照表就是標準答案。

## 動手練習

1. 把 `lit.c` 的 `p` 改成 `char *p = (char[]){ "hello" };`（compound literal），再跑一次。還會 segfault 嗎？用 [Ch 7](./07-pointers-by-the-standard.md) 的 storage duration 解釋。
2. 寫一個 `size_t safe_strlen(const char *s, size_t max)`，對 `NULL` 回傳 0、最多數到 `max`，再和 glibc 的 `strnlen` 比較行為差異。
3. 寫一個 variadic 函式 `int sum_until_null(int *first, ...)`，以 `nullptr` 當結尾，用 Docker `gcc:14` 試試直接傳 `nullptr` 與傳 `(int *) nullptr` 的差別，並對照 N3220 7.16.1.1 說明哪一種是可攜的。
4. 用 `objdump -s -j .rodata` 檢查兩個檔案各自定義 `"hello world"` 後連結的執行檔，觀察字串是否被跨檔案合併；再加 `-fno-merge-constants` 比較。

## 本章重點整理

- 字串常量是 static storage duration 的 `char[N]` 陣列，gcc 放在唯讀的 `.rodata`；修改它是 UB，相同內容可能被合併。
- `char *p = "..."` 是指向唯讀資料的指標；`char a[] = "..."` 是把內容複製進自己的陣列，函式內的版本不能回傳。
- 標準字串函式不接受 null pointer（7.1.4、7.24.1），glibc 不檢查；`free(NULL)` 是唯一的常見例外。
- null pointer constant 是原始碼層面的 0 或 `(void *)0`，null pointer 是執行期的值，`NULL` 是型別不固定的巨集；null 的位元表示不保證是全 0。C23 `nullptr` 給了 null 一個專屬型別，但不改變表示法，也不豁免 variadic 的型別規則。
- 解參考 null 是 UB，編譯器會據此刪掉之後的 null 檢查。指標的自由換來記憶體安全問題，Fil-C 等方案用執行期 capability 與 GC 把它收回來，代價是效能與 ABI 相容性。

## 自我檢核

- [ ] 不看筆記，畫出 `char *p = "hi";` 與 `char a[] = "hi";` 在記憶體中的樣子，並說出各自的 `sizeof`、可否修改、可否回傳。
- [ ] 如果有人說「`NULL` 就是 0，所以 `memset` 清零的指標就是 null」，你會怎麼糾正？要引用哪一條？
- [ ] 能解釋為什麼 `int flags = tun->flags; if (!tun) return -1;` 的檢查會被刪掉，以及為什麼加了 `-fno-delete-null-pointer-checks` 仍然不是真正的修正嗎？
- [ ] 能說出 `nullptr` 解決了 `NULL` 的哪個問題、哪兩個問題它沒有解決嗎？
- [ ] 想一個你寫過的字串處理函式：它有沒有可能少算 `'\0'`？如果呼叫端傳 `NULL`，它會怎樣？

## 延伸閱讀

### 官方文件 / Spec

- **[N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：6.3.2.3 §3–§4（null pointer constant 與 null pointer，連同註腳 66）、6.4.5 §6–§7（字串常量）、6.5.8–6.5.9（relational 與 equality 比較指標的規則）、7.1.4 §1（函式庫引數的無效值）、7.19（`NULL` 與 `offsetof`）、7.24.1（字串函式約定）。
- **[N3220](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n3220.pdf)**（C23 之後的工作草案）
  - **讀哪裡**：6.4.4.6（`nullptr` 等預定義常數）、6.3.2.4（`nullptr_t` 的轉換）、7.21.2（`nullptr_t` 型別）、7.16.1.1（`va_arg` 對 `nullptr_t` 的特別規定）。
  - **注意**：N3220 是 C23 定稿之後的工作草案，內容與 ISO/IEC 9899:2024 幾乎相同，但不是正式標準本身。

### 部落格 / 技術文章

- **[The Most Expensive One-byte Mistake](https://queue.acm.org/detail.cfm?id=2010365)** — Poul-Henning Kamp（ACM Queue，2011）
  - **這篇說什麼**：回顧 NUL 結尾字串這個決定，從效能、編譯器、安全三個角度估算它的代價。
  - **讀哪裡**：整篇（短）。
  - **為什麼值得讀**：作者是 FreeBSD 核心開發者，寫過 Varnish；這篇是本章「NUL 結尾的代價」一節的來源。
- **[Null References: The Billion Dollar Mistake](https://www.infoq.com/presentations/Null-References-The-Billion-Dollar-Mistake-Tony-Hoare/)** — Tony Hoare（QCon London，2009）
  - **這篇說什麼**：null reference 的發明者親自解釋當初為什麼加入它、為什麼後悔。
  - **讀哪裡**：演講前 15 分鐘就講完核心論點。
  - **為什麼值得讀**：一手史料；聽完再看本章的跨語言比較表，會更理解各語言的選擇。
- **[Fil-C 官方網站](https://fil-c.org/)** — Filip Pizlo
  - **讀哪裡**：首頁的概覽，以及 InvisiCaps、FUGC 的說明頁。
  - **能學到什麼**：一個完整的記憶體安全 C 實作怎麼設計指標表示法與 GC。前提是讀完本章與 [Ch 17](./17-malloc-internals.md) 會更容易理解。
- **[Insecure coding in C (and C++)](https://www.slideshare.net/slideshow/insecure-coding-in-c-and-c/35699341)** — Olve Maudal
  - **這篇說什麼**：一系列看起來無害、實際上危險或未定義的 C 程式碼，逐張投影片拆解。
  - **讀哪裡**：整份；和本章、[Ch 34](./34-undefined-behavior.md) 搭配讀。

### 書籍

- **《Secure Coding in C and C++》2nd ed.** — Robert Seacord（Addison-Wesley，2013）
  - **讀哪幾章**：第 2 章（Strings）完整涵蓋字串相關的漏洞類型與緩解；第 4 章（Dynamic Memory Management）銜接 [Ch 17](./17-malloc-internals.md)。
  - **定位**：CERT C 安全編碼標準的作者所寫，資安角度的 C 參考書。2013 年出版，C11 Annex K（`strcpy_s` 等）的部分在實務上採用不多，閱讀時注意。

Part 2 的前半到這裡告一段落：我們從規格書的 object 一路走到字串與 null。這些指標最後都指向行程位址空間裡的某個地方，`.rodata`、stack、heap 分別在哪裡、函式呼叫時 stack 上又發生了什麼，是下一章的主題。

→ [Ch 11 行程記憶體布局、x86-64 SysV ABI 與 stack frame](./11-process-layout-and-abi.md)
