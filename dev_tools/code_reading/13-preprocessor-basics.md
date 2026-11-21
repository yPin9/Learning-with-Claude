# Ch 13 — 前置處理器基礎：巨集、條件編譯、展開來看

> **目標**：能分辨 object-like 與 function-like macro 並預測它們展開後的樣子；看到 `#ifdef` 叢林時知道怎麼畫出骨架、怎麼把它「投影」成某個設定下真正被編譯的程式碼；遇到看不懂的巨集時，第一反應是 `gcc -E` 展開來看，而不是在腦中模擬。
> **鴻溝**：本章主要練**慣用法鴻溝**（巨集陷阱、`do { } while (0)`）與**間接鴻溝**（條件編譯讓「你看到的程式碼」不等於「被編譯的程式碼」）。

> **環境**：WSL Ubuntu 22.04、x86_64、gcc 11.4、clang 14；`unifdef` 2.5 從 Linux v6.18 tree 的 `scripts/unifdef.c` 編出來放在 `~/.local/bin`。語料是 Lua 5.4.9、Redis 7.2.17、Linux v6.18 的 `fs/pipe.c`。

## 為什麼需要這個？

C 的前置處理器（preprocessor，cpp）是一個在編譯器之前執行的**文字替換器**。它不懂 C 的型別、不懂作用域、不懂運算子優先順序——它只認得 token。這是它強大的原因，也是讀碼時最大的陷阱來源之一：**你在編輯器裡看到的原始碼，不是編譯器看到的原始碼。**

Spinellis 原書第 5.6 節專門談前置處理器，指出條件編譯是可攜性程式碼的主要手段，也是讓程式碼難讀的主因之一。2003 年的例子是 NetBSD 跨十幾種 CPU 的 `#ifdef`；今天的 Linux kernel 有上萬個 Kconfig 選項，同一個 `.c` 檔在不同 `.config` 下可能編出完全不同的函式內容。我們課程 build 的 kernel 設定裡 `CONFIG_WATCH_QUEUE` 沒開，所以 `fs/pipe.c` 讀檔主迴圈裡有二十多行程式碼**根本不存在**——如果你花時間去理解那一段，就是在讀一段不會執行的程式。

歷史上，前置處理器原本是 C 之外的獨立工具（早期 Unix 的 `/lib/cpp`），後來才被吸收進標準。它承擔了 C 語言當年沒有的功能：常數（後來有 `const`/`enum`）、inline 函式（C99 才有 `inline`）、泛型（C11 才有 `_Generic`）、模組（C 至今沒有，靠 `#include`）。很多今天看起來古怪的巨集寫法，都是在補語言當年缺的那一塊。kernel coding style 第 12 節（v6.18 `Documentation/process/coding-style.rst` 第 795 行起）說得直接：「Generally, inline functions are preferable to macros resembling functions.」

## 先建立直覺

把前置處理器想成**一台在你送出文件之前做「尋找並取代」的影印機**：

```
你寫的 .c 檔 ──► [ 前置處理器 ] ──► 一個巨大的 .i 檔 ──► [ 編譯器 ] ──► .o
                  │                       ▲
                  ├ #include：把檔案整個貼進來
                  ├ #define：記住「看到 X 就換成 Y」
                  ├ #if/#ifdef：決定哪些行要留、哪些行整段刪掉
                  └ 換完之後，#開頭的指令全部消失
                                      你讀的是左邊；編譯器讀的是這裡
```

三個關鍵直覺：

1. **它是 token 層級的取代，不是值層級的計算**。`#define SQUARE(x) x * x` 遇到 `SQUARE(1 + 2)` 會變成 `1 + 2 * 1 + 2`，不是 `3 * 3`。
2. **條件編譯在編譯之前就把程式碼刪掉了**。被 `#if 0` 包住的程式碼，編譯器連看都沒看到，裡面有語法錯誤也不會報錯。
3. **最可靠的讀法是讓工具替你展開**。`gcc -E` 輸出的就是右邊那個 `.i` 檔。

> 如果你對 `do { } while (0)` 為什麼存在還有疑問，先回看 [Ch 8 — 控制流慣用法](./08-control-flow-idioms.md)；本章會從巨集展開的角度再看一次。

## object-like 與 function-like macro

```c
#define BUFSIZE 1024              /* object-like：名字後面沒有括號 */
#define SQUARE(x) ((x) * (x))     /* function-like：名字後面「緊貼」左括號 */
#define ODD (x) ((x) & 1)         /* 注意空格！這是 object-like，展開成 "(x) ((x) & 1)" */
```

`#define` 的名字和 `(` 之間**有沒有空格**決定了它是哪一種。第三行是經典的手誤，讀碼時看到奇怪的展開結果，先檢查這個空格。

function-like macro 只有在名字後面**緊跟著 `(`** 時才展開。這個規則被用來做一個常見技巧：同名的巨集與函式並存，`foo(x)` 走巨集，`(foo)(x)` 走真正的函式。Lua 的 `lua.h` 就這樣宣告 API：

```c
/* lua-5.4.9/lua.h:163 */
LUA_API lua_State *(lua_newstate) (lua_Alloc f, void *ud);
```

函式名被括號包起來，是為了防止它被某個同名巨集展開。讀到這種寫法不用驚訝，它就是一般的函式宣告。

## 巨集陷阱：故意讓它出錯

以下程式碼收集了四個最常見的巨集錯誤（`scratch/13/pitfalls.c`）：

```c
#include <stdio.h>

#define SQUARE_BAD(x)   x * x
#define SQUARE(x)       ((x) * (x))
#define MAX(a, b)       ((a) > (b) ? (a) : (b))
#define BUFSIZE_BAD     1024 + 1
#define BUFSIZE         (1024 + 1)

static int calls = 0;
static int next_id(void) { return ++calls * 10; }

int main(void) {
    printf("SQUARE_BAD(1+2) = %d\n", SQUARE_BAD(1 + 2));
    printf("SQUARE(1+2)     = %d\n", SQUARE(1 + 2));
    printf("2*BUFSIZE_BAD   = %d\n", 2 * BUFSIZE_BAD);
    printf("2*BUFSIZE       = %d\n", 2 * BUFSIZE);

    int i = 5, j = 3;
    int m = MAX(i++, j);
    printf("MAX(i++, j) = %d, i 變成 %d（原本 5）\n", m, i);

    calls = 0;
    int r = MAX(next_id(), 5);
    printf("MAX(next_id(), 5) = %d, next_id 被呼叫 %d 次\n", r, calls);
    return 0;
}
```

```
$ gcc -Wall -o pitfalls pitfalls.c && ./pitfalls
SQUARE_BAD(1+2) = 5
SQUARE(1+2)     = 9
2*BUFSIZE_BAD   = 2049
2*BUFSIZE       = 2050
MAX(i++, j) = 6, i 變成 7（原本 5）
MAX(next_id(), 5) = 20, next_id 被呼叫 2 次
```

gcc 開了 `-Wall` 一個警告都沒有。逐條拆：

1. **參數沒加括號**：`SQUARE_BAD(1 + 2)` 展開成 `1 + 2 * 1 + 2` = 5。規則：**巨集本體裡每個參數都要加括號**。
2. **整個本體沒加括號**：`2 * BUFSIZE_BAD` 展開成 `2 * 1024 + 1` = 2049。規則：**運算式巨集的整個本體要加括號**。kernel coding style 第 12 節的第 4 條「forgetting about precedence」講的就是這兩件事。
3. **參數被求值兩次**：`MAX(i++, j)` 展開成 `((i++) > (j) ? (i++) : (j))`。`5 > 3` 成立，於是第二個 `i++` 也執行：回傳 6、`i` 變成 7。括號救不了這個問題。
4. **副作用被重複**：`MAX(next_id(), 5)` 的第一次呼叫回傳 10，`10 > 5` 成立，再呼叫一次回傳 20。函式被呼叫了兩次，回傳值也不是你以為的那一個。

第 3、4 條是 function-like macro 的本質缺陷。kernel 的解法是 GNU C 的 statement expression 加 `typeof`，把參數先存進區域變數（`include/linux/minmax.h` 的 `max()` 就是這樣，[Ch 15](./15-kernel-c-gnu-extensions.md) 拆解）；更通用的解法是改用 `static inline` 函式。讀碼時的習慣：**看到全大寫的「函式呼叫」，參數裡如果有 `++`、`--` 或函式呼叫，停下來想一下它會被求值幾次。**

### 多敘述巨集與 `do { } while (0)`

```c
/* scratch/13/swap_bad.c 的三個版本 */
#define SWAP_BAD(a, b)   int t = a; a = b; b = t
#define SWAP_BRACE(a, b) { int t = a; a = b; b = t; }
#define SWAP(a, b) do { int t = (a); (a) = (b); (b) = t; } while (0)

    if (x < y)
        SWAP_xxx(x, y);
    else
        puts("no swap");
```

用 `SWAP_BRACE` 編譯：

```
swap_bad.c: In function ‘main’:
swap_bad.c:9:5: error: ‘else’ without a previous ‘if’
    9 |     else
      |     ^~~~
```

`{ ... };` 後面那個分號變成一個空敘述，`if` 的本體在 `}` 就結束了，`else` 找不到它的 `if`。用 `SWAP_BAD`：

```
swap_bad2.c:2:25: error: expected expression before ‘int’
    2 | #define SWAP_BAD(a, b)  int t = a; a = b; b = t
      |                         ^~~
swap_bad2.c:8:9: note: in expansion of macro ‘SWAP_BAD’
    8 |         SWAP_BAD(x, y);
```

C 不允許宣告直接當 `if` 的本體。注意 gcc 的 `note: in expansion of macro` ——**錯誤訊息指向巨集定義那一行，真正的呼叫點在 note 裡**。讀編譯錯誤時記得往下看 note。

`do { ... } while (0)` 版本吃掉呼叫端的分號、形成單一敘述，`if/else` 正常運作，輸出 `2 1`。這就是 kernel 和 Redis 裡所有多敘述巨集都包成這樣的原因，例如 Redis 的：

```c
/* redis-7.2.17/src/server.h:3734 */
#define serverLog(level, ...) do {\
        if (((level)&0xff) < server.verbosity) break;\
        _serverLog(level, __VA_ARGS__);\
    } while(0)
```

這裡還順便利用了 `do-while` 的另一個好處：可以用 `break` 提早離開巨集。`__VA_ARGS__` 是 [Ch 14](./14-macro-metaprogramming.md) 的主題。

## 展開來看：`gcc -E` 與它的夥伴

遇到看不懂的巨集，**不要在腦中展開**。讓編譯器做：

```c
/* scratch/13/tiny.c */
#include "tiny.h"
#define GREETING "hi"
int main(void) { return SQUARE(GREETING[0]); }
/* scratch/13/tiny.h */
#define SQUARE(x) ((x) * (x))
```

```
$ gcc -E tiny.c
# 0 "tiny.c"
# 0 "<built-in>"
# 0 "<command-line>"
# 1 "/usr/include/stdc-predef.h" 1 3 4
# 0 "<command-line>" 2
# 1 "tiny.c"
# 1 "tiny.h" 1
# 2 "tiny.c" 2

int main(void) { return (("hi"[0]) * ("hi"[0])); }
```

`# 行號 "檔名" 旗標` 是 **line marker**，告訴編譯器「接下來的內容來自哪個檔案的第幾行」，編譯錯誤才能指回原始檔。旗標 `1` 表示進入一個新檔、`2` 表示回到上一層、`3` 表示系統標頭。讀一個幾千行的 `.i` 檔時，line marker 就是你的 GPS：搜尋你關心的檔名，就能跳到那個檔案貢獻的部分。

不想看 line marker 時加 `-P`：

```
$ gcc -E -P tiny.c
int main(void) { return (("hi"[0]) * ("hi"[0])); }
```

幾個搭配旗標，讀碼時都用得上：

| 旗標 | 作用 | 什麼時候用 |
|---|---|---|
| `-E` | 只做前置處理，輸出到 stdout | 看某個巨集展開成什麼 |
| `-E -P` | 同上，去掉 line marker | 輸出要給人讀或丟進 diff |
| `-dM -E` | 不輸出程式碼，只列出**最後所有**生效的 `#define` | 問「在這個設定下，X 被定義成什麼？」 |
| `-dD -E` | 輸出程式碼，同時保留 `#define` 指令 | 想看巨集在哪裡被定義 |
| `-H` | 印出 include 樹（到 stderr） | 問「這個標頭是誰拉進來的？」 |
| `-D X` / `-U X` | 定義 / 取消定義巨集 | 模擬某個 build 設定 |

真實檔案展開後通常很大。Redis 的 `zmalloc.c` 原本 851 行，`gcc -E -DUSE_JEMALLOC ...` 之後變成 7383 行——多出來的全是標頭。這時用 line marker 定位、或用 `-P` 加 `grep -n` 找你要的函式名。

### `-dM`：編譯器預先定義了什麼？

```
$ gcc -dM -E - </dev/null | wc -l
367
$ gcc -dM -E - </dev/null | grep -E "__x86_64__|__LP64__|__GNUC__ |__GNUC_MINOR__|__linux__|__SIZEOF_POINTER__|__BYTE_ORDER__ |__STDC_VERSION__|__OPTIMIZE__"
#define __GNUC__ 11
#define __SIZEOF_POINTER__ 8
#define __LP64__ 1
#define __x86_64__ 1
#define __STDC_VERSION__ 201710L
#define __linux__ 1
#define __BYTE_ORDER__ __ORDER_LITTLE_ENDIAN__
#define __GNUC_MINOR__ 4
```

gcc 11.4 什麼都不 include 就預先定義了 367 個巨集。`__STDC_VERSION__ 201710L` 告訴你預設是 C17（gnu17）。`__OPTIMIZE__` 沒出現，加上 `-O2` 才有：

```
$ gcc -O2 -dM -E - </dev/null | grep -E "__OPTIMIZE__"
#define __OPTIMIZE__ 1
```

這代表**優化等級可以改變前置處理的結果**——有些標頭（例如 glibc 的 `FORTIFY_SOURCE` 相關部分）會看 `__OPTIMIZE__` 決定要不要啟用某些 inline 版本。再看 clang：

```
$ clang -dM -E - </dev/null | grep -E "__clang_major__|__GNUC__ "
#define __GNUC__ 4
#define __clang_major__ 14
```

clang 14 自稱 `__GNUC__ 4`，為了相容大量「`#ifdef __GNUC__` 才用 GNU 擴充」的程式碼。讀到 `#if __GNUC__ >= 5` 這種判斷時要知道：它在 clang 上的結果可能和你預期不同。

## include guard

每個標頭檔都要防止被重複 include。Lua 的寫法：

```c
/* lua-5.4.9/lobject.h:8 */
#ifndef lobject_h
#define lobject_h
...
#endif
```

拿掉 guard、include 兩次會怎樣？

```c
/* scratch/13/noguard.h */
#define SQUARE(x) ((x) * (x))
struct point { int x, y; };
/* scratch/13/twice.c */
#include "noguard.h"
#include "noguard.h"
int main(void){ struct point p = {1,2}; return SQUARE(p.x); }
```

```
$ gcc -c twice.c
In file included from twice.c:2:
noguard.h:2:8: error: redefinition of ‘struct point’
    2 | struct point { int x, y; };
      |        ^~~~~
In file included from twice.c:1:
noguard.h:2:8: note: originally defined here
```

注意 `SQUARE` 被定義兩次**沒有**報錯：C 標準允許完全相同的巨集重複定義（token 序列一字不差），只有不同的定義才會警告。struct 重複定義則是錯誤。

讀碼時 include guard 有兩個小用途：

- guard 的名字常常反映檔案的歷史路徑或專案慣例（Lua 用 `lobject_h`、Redis 用 `__ZMALLOC_H`、kernel 用 `_LINUX_FS_H`），看到 guard 名和檔名對不上，可能是檔案被搬過家。
- guard 的 `#endif` 在檔案最後，而 kernel coding style 第 21 節要求非小型的 `#if` 區塊在 `#endif` 後面加註解標明條件（`#endif /* CONFIG_SOMETHING */`）。Redis 的 `zmalloc.h:167` 就寫了 `#endif /* __ZMALLOC_H */`。

`#pragma once` 是非標準但被 gcc/clang/MSVC 都支援的替代寫法。kernel 與 Lua、Redis 都沒有用它，仍是傳統 guard。

## 底層機制：讀 `#ifdef` 叢林的三個步驟

條件編譯的問題是：同一個檔案在不同設定下是不同的程式。讀它的方法不是硬記所有分支，而是三步：**看骨架 → 決定設定 → 投影**。

### 步驟一：看骨架

先只看條件指令本身，把縮排畫出來。這支 awk 腳本把 `#if/#else/#endif` 依巢狀深度加上 `|`：

```awk
# /tmp/ifdepth.awk
/^[ \t]*#[ \t]*(if|ifdef|ifndef)/ { printf "%4d %s%s\n", NR, substr("||||||||||", 1, d), $0; d++; next }
/^[ \t]*#[ \t]*(else|elif)/       { printf "%4d %s%s\n", NR, substr("||||||||||", 1, d-1), $0; next }
/^[ \t]*#[ \t]*endif/             { d--; printf "%4d %s%s\n", NR, substr("||||||||||", 1, d), $0; next }
```

對 Redis 7.2.17 的 `zmalloc.h`：

```
$ awk -f /tmp/ifdepth.awk zmalloc.h
  31 #ifndef __ZMALLOC_H
  38 |#if defined(USE_TCMALLOC)
  41 ||#if (TC_VERSION_MAJOR == 1 && TC_VERSION_MINOR >= 6) || (TC_VERSION_MAJOR > 1)
  44 ||#else
  46 ||#endif
  48 |#elif defined(USE_JEMALLOC)
  51 ||#if (JEMALLOC_VERSION_MAJOR == 2 && JEMALLOC_VERSION_MINOR >= 1) || (JEMALLOC_VERSION_MAJOR > 2)
  54 ||#else
  56 ||#endif
  58 |#elif defined(__APPLE__)
  62 |#endif
  72 |#ifndef ZMALLOC_LIB
  75 ||#if !defined(NO_MALLOC_USABLE_SIZE) && \
  81 |||#ifdef __FreeBSD__
  83 |||#else
  84 ||||#ifndef _GNU_SOURCE
  86 ||||#endif
  88 |||#endif
  93 ||#endif
  94 |#endif
  99 |#if defined(USE_JEMALLOC) && defined(JEMALLOC_FRAG_HINT)
 101 |#endif
 134 |#ifdef HAVE_DEFRAG
 137 |#endif
 139 |#ifndef HAVE_MALLOC_SIZE
 142 |#else
 159 |#endif
 163 |#ifdef REDIS_TEST
 165 |#endif
 167 #endif /* __ZMALLOC_H */
```

一眼看出結構：第 38–62 行是「選配置器」的三選一（tcmalloc / jemalloc / Apple），每個分支內還有版本檢查；第 72–94 行是「前面都沒選到時，退回 libc 並嘗試啟用 `malloc_usable_size`」；後面是依結果決定的功能開關。`unifdef -s` 則列出檔案裡所有出現在條件式中的巨集，等於這個檔案的「設定旋鈕清單」：

```
$ unifdef -s zmalloc.h | tr '\n' ' '
__ZMALLOC_H USE_TCMALLOC TC_VERSION_MAJOR TC_VERSION_MINOR TC_VERSION_MAJOR USE_JEMALLOC JEMALLOC_VERSION_MAJOR JEMALLOC_VERSION_MINOR JEMALLOC_VERSION_MAJOR __APPLE__ ZMALLOC_LIB NO_MALLOC_USABLE_SIZE __FreeBSD__ _GNU_SOURCE USE_JEMALLOC JEMALLOC_FRAG_HINT HAVE_DEFRAG HAVE_MALLOC_SIZE REDIS_TEST
```

### 步驟二：決定你關心的設定

不要讀「所有可能」，讀「你的 binary 實際用的那一個」。問 build 系統：

```
$ cd redis/src && make -n -B zmalloc.o | grep -o "cc .*zmalloc.c" | tr ' ' '\n' | grep -E "^-D|^-I|^-std|^-O"
-DREDIS_STATIC= -std=gnu11 -O0 -I../deps/hiredis -I../deps/linenoise -I../deps/lua/src -I../deps/hdr_histogram -I../deps/fpconv -DUSE_JEMALLOC -I../deps/jemalloc/include
```

`make -n` 只印出會執行的指令而不執行，`-B` 強制把目標當成過期。答案：`-DUSE_JEMALLOC`。（`-O0` 是我們 build 時自己指定的 `OPTIMIZATION=-O0`。）對 kernel 而言，對應的問題是「`.config` 裡這個選項是什麼」，加上 `compile_commands.json`——Ch 36 與 Ch 49 處理。

### 步驟三：投影

**投影**的意思是：給定一組巨集的值，把條件編譯「解掉」，只留下會被編譯的那些行。有兩個工具。

**工具一：`gcc -dM -E` 問結果**。不看程式碼，只問「在這個設定下，關鍵巨集最後是什麼」：

```
$ echo '#include "zmalloc.h"' > zm.c
$ gcc -dM -E -I$R -DUSE_JEMALLOC -I$J zm.c | grep -E "define (ZMALLOC_LIB|HAVE_MALLOC_SIZE|zmalloc_size|HAVE_DEFRAG)\b" | sort
#define HAVE_DEFRAG
#define HAVE_MALLOC_SIZE 1
#define ZMALLOC_LIB ("jemalloc-" __xstr(JEMALLOC_VERSION_MAJOR) "." __xstr(JEMALLOC_VERSION_MINOR) "." __xstr(JEMALLOC_VERSION_BUGFIX))
#define zmalloc_size(p) je_malloc_usable_size(p)
$ gcc -dM -E -I$R zm.c | grep -E "define (ZMALLOC_LIB|HAVE_MALLOC_SIZE|zmalloc_size|HAVE_DEFRAG)\b" | sort
#define ZMALLOC_LIB "libc"
```

第二個結果是**錯的投影**，而且錯得很有教育意義。不加 `USE_JEMALLOC` 時，glibc 平台理應定義 `HAVE_MALLOC_SIZE`（第 75 行的條件包含 `defined(__GLIBC__)`），為什麼沒有？因為 `__GLIBC__` **不是編譯器預先定義的**（`gcc -dM -E - </dev/null | grep -c __GLIBC__` 輸出 0），它來自 glibc 的 `<features.h>`。我們的 `zm.c` 直接 include `zmalloc.h`，在它之前沒有任何 glibc 標頭，所以 `__GLIBC__` 還沒被定義。真正的 `zmalloc.c` 在 include `zmalloc.h` 之前已經 include 了 `<stdio.h>`、`<stdlib.h>`。修正投影環境：

```
$ printf '#include <stdlib.h>\n#include "zmalloc.h"\n' > zm2.c
$ gcc -dM -E -I$R zm2.c | grep -E "define (ZMALLOC_LIB|HAVE_MALLOC_SIZE|zmalloc_size|__GLIBC__)\b" | sort
#define HAVE_MALLOC_SIZE 1
#define ZMALLOC_LIB "libc"
#define __GLIBC__ 2
#define zmalloc_size(p) malloc_usable_size(p)
$ gcc -dM -E -I$R -DNO_MALLOC_USABLE_SIZE zm2.c | grep -E "..." | sort
#define ZMALLOC_LIB "libc"
#define __GLIBC__ 2
```

教訓：**條件編譯的結果取決於 include 順序**。投影時要重現真實編譯單元的上下文，最可靠的做法是直接對真正的 `.c` 檔、用真正的編譯旗標跑 `gcc -E`。`gcc -H` 可以印出 include 樹確認順序；`zm2.c` 光是 include `<stdlib.h>` 就拉進了 66 個標頭。

**工具二：`unifdef` 投影程式碼**。`gcc -E` 的問題是它把所有巨集也一起展開、把標頭全部貼進來，輸出不像原始碼了。`unifdef` 只解掉你指定的條件，其他一切保持原樣。Linux tree 自帶一份（`scripts/unifdef.c`，`headers_install` 用它剝掉 `__KERNEL__` 區塊），編譯它不需要 root：

```
$ gcc -O2 -o ~/.local/bin/unifdef ~/code_reading/linux/scripts/unifdef.c
$ unifdef -V | head -1
Version: unifdef-2.5
```

把 `zmalloc.c` 的 `zfree`（第 365–381 行）分別投影到兩種設定：

```
$ sed -n '365,381p' zmalloc.c > zfree.c
$ unifdef -DHAVE_MALLOC_SIZE zfree.c
void zfree(void *ptr) {

    if (ptr == NULL) return;
    update_zmalloc_stat_free(zmalloc_size(ptr));
    free(ptr);
}
$ unifdef -UHAVE_MALLOC_SIZE zfree.c
void zfree(void *ptr) {
    void *realptr;
    size_t oldsize;

    if (ptr == NULL) return;
    realptr = (char*)ptr-PREFIX_SIZE;
    oldsize = *((size_t*)realptr);
    update_zmalloc_stat_free(oldsize+PREFIX_SIZE);
    free(realptr);
}
```

原本 17 行、交錯三組 `#ifdef` 的函式，變成兩個各自清楚的版本。`PREFIX_SIZE`、`free` 這些巨集**沒有被展開**，你看到的仍然是原始碼的樣子。`unifdef` 的回傳值 1 表示「輸出和輸入不同」，0 表示沒變、2 表示錯誤，可以拿來在腳本裡判斷某個檔案是否受某個選項影響。

### 在 kernel 上投影

`unifdef` 不需要 build、不需要 `.config`，直接作用在原始碼上。`fs/pipe.c`（v6.18）裡的條件編譯：

```
$ grep -nE '^\s*#\s*(if|ifdef|ifndef|else|elif|endif)' fs/pipe.c
80:#ifdef CONFIG_PROVE_LOCKING
86:#endif
251:#ifdef CONFIG_WATCH_QUEUE
254:#endif
297:#ifdef CONFIG_WATCH_QUEUE
319:#endif
...
1483:#ifdef CONFIG_SYSCTL
1534:#endif
```

`anon_pipe_read()`（第 269 行起）的主迴圈裡有一段：

```c
/* fs/pipe.c:292（v6.18） */
	for (;;) {
		/* Read ->head with a barrier vs post_one_notification() */
		unsigned int head = smp_load_acquire(&pipe->head);
		unsigned int tail = pipe->tail;

#ifdef CONFIG_WATCH_QUEUE
		if (pipe->note_loss) {
			struct watch_notification n;
			...
			pipe->note_loss = false;
		}
#endif

		if (!pipe_empty(head, tail)) {
```

本課程 build 用的 `.config`（`~/code_reading/build/.config` 第 57 行）寫著 `# CONFIG_WATCH_QUEUE is not set`；`init/Kconfig:486` 也顯示它 `default n`。投影：

```
$ unifdef -UCONFIG_WATCH_QUEUE fs/pipe.c > /tmp/pipe_nowq.c; echo "rc=$?"
rc=1
$ wc -l fs/pipe.c /tmp/pipe_nowq.c
 1553 fs/pipe.c
 1505 /tmp/pipe_nowq.c
```

48 行消失，包括讀檔主迴圈裡那整段 `note_loss` 處理。讀 pipe 的讀取路徑時，先投影再讀，你面對的是一個更短、更接近實際執行的函式。**注意它的限制**：kernel 的 `CONFIG_X` 也可能透過 `IS_ENABLED()` 出現在一般 C 的 `if` 裡（見下一節），`unifdef` 只處理前置處理器指令，那些不會被投影掉。

## 條件編譯的風格：kernel 怎麼說

kernel coding style 第 21 節「Conditional Compilation」（v6.18 第 1159 行起）給了三條規則，讀 kernel 時知道這些規則，就知道「不尋常的寫法」在哪裡：

1. **盡量不要在 `.c` 檔裡用 `#if`**。把條件放在標頭檔，提供一個 no-op 的 stub 版本，`.c` 檔無條件呼叫。編譯器會把空 stub 的呼叫最佳化掉。
2. **優先整個函式編譯掉，而不是函式的一部分**。`fs/pipe.c` 第 297 行那段函式內 `#ifdef`，在 kernel 裡反而算「不得已」的寫法。
3. **能用 `IS_ENABLED(CONFIG_X)` 就不要用 `#ifdef`**：

```c
/* kernel/fork.c:439（v6.18） */
	if (IS_ENABLED(CONFIG_VMAP_STACK)) {
```

   `IS_ENABLED` 展開成常數 0 或 1，編譯器做 constant folding 把死碼刪掉，執行期沒有成本；好處是被關掉的那段程式碼**仍然會被編譯器做語法與型別檢查**，不會像 `#ifdef` 裡的程式碼那樣悄悄爛掉。代價是被引用的符號在任何設定下都必須存在。

`IS_ENABLED` 本身是用一個很巧妙的巨集技巧做出來的（`include/linux/kconfig.h:41–73`），利用 `##` 拼接與「把第二個參數取出來」的手法判斷一個巨集是否被定義為 1。這個技巧在 [Ch 14](./14-macro-metaprogramming.md) 拆解；Kconfig、Kbuild 與 `#ifdef` 在 kernel 裡的完整故事在 [Ch 36](./36-kconfig-kbuild-ifdef.md)。

## 在 Lua、Redis 找條件編譯

條件編譯密度可以粗略量一下：

```
lua-5.4.9: 215 #if-lines / 31708 lines
redis-7.2.17/src: 490 #if-lines / 149352 lines
kernel/sched/core.c: 137 / 10930
fs/pipe.c: 8 / 1553
```

Lua 把幾乎所有平台相依的設定集中在一個檔案 `luaconf.h`，這是很好的設計：其他檔案只看「功能巨集」（`LUA_USE_POSIX`），不看「平台巨集」（`__linux__`）。

```c
/* lua-5.4.9/luaconf.h:61 */
#if defined(LUA_USE_LINUX)
#define LUA_USE_POSIX
#define LUA_USE_DLOPEN		/* needs an extra library: -ldl */
#endif
```

投影三種平台設定：

```
$ echo '#include "luaconf.h"' > lc.c
$ for cfg in "" "-DLUA_USE_LINUX" "-DLUA_USE_WINDOWS"; do echo "== $cfg"; \
    gcc -dM -E -I$L $cfg lc.c | grep -E "define (LUA_USE_[A-Z0-9]+|LUA_DL_[A-Z]+|LUA_PATH_SEP|LUA_DIRSEP|LUA_INTEGER) " | sort; done
==
#define LUA_DIRSEP "/"
#define LUA_INTEGER long long
#define LUA_PATH_SEP ";"
== -DLUA_USE_LINUX
#define LUA_DIRSEP "/"
#define LUA_INTEGER long long
#define LUA_PATH_SEP ";"
#define LUA_USE_DLOPEN
#define LUA_USE_LINUX 1
#define LUA_USE_POSIX
== -DLUA_USE_WINDOWS
#define LUA_DIRSEP "/"
#define LUA_DL_DLL
#define LUA_INTEGER long long
#define LUA_PATH_SEP ";"
#define LUA_USE_C89
#define LUA_USE_WINDOWS 1
```

`-DLUA_USE_LINUX` 是 makefile 預設傳的（`makefile:73` 的 `MYCFLAGS`），它展開成 `LUA_USE_POSIX` 與 `LUA_USE_DLOPEN` 兩個功能開關。`-DLUA_USE_WINDOWS` 打開 `LUA_USE_C89`（「broadly, Windows is C89」，`luaconf.h:57`）與 `LUA_DL_DLL`。有趣的是 `LUA_DIRSEP` 在第三組仍是 `"/"`：它看的是編譯器定義的 `_WIN32`，不是 `LUA_USE_WINDOWS`。在 Linux 上手動 `-D` 一個平台巨集，**不等於**真的在那個平台上編譯——投影結果只對你模擬到的那部分有效。

最後一種常見的條件編譯：用 `#error` 在編譯期拒絕不支援的設定。

```c
/* redis-7.2.17/src/zmalloc.h:51 */
#if (JEMALLOC_VERSION_MAJOR == 2 && JEMALLOC_VERSION_MINOR >= 1) || (JEMALLOC_VERSION_MAJOR > 2)
#define HAVE_MALLOC_SIZE 1
#define zmalloc_size(p) je_malloc_usable_size(p)
#else
#error "Newer version of jemalloc required"
#endif
```

用一個空的假 `jemalloc/jemalloc.h` 加上 `-DJEMALLOC_VERSION_MAJOR=2 -DJEMALLOC_VERSION_MINOR=0`（寫在 `oldje.c` 裡）觸發它：

```
In file included from oldje.c:4:
/home/ypp/code_reading/redis-7.2.17/src/zmalloc.h:55:2: error: #error "Newer version of jemalloc required"
   55 | #error "Newer version of jemalloc required"
```

讀到 `#error`，它就是作者寫給你的文件：「這條路徑不支援」。

## 對比與取捨

| 需求 | 前置處理器做法 | 語言做法 | 讀碼時的差異 |
|---|---|---|---|
| 常數 | `#define N 16` | `enum { N = 16 };`、`static const int` | enum 在 debugger 裡看得到名字，巨集看不到 |
| 小函式 | function-like macro | `static inline` | inline 只求值一次、有型別檢查、能設中斷點 |
| 平台差異 | `#ifdef` | 分檔案實作（`foo_linux.c`/`foo_bsd.c`）+ build 系統選檔 | 分檔案讓每個檔案只有一種意義 |
| 功能開關 | `#ifdef CONFIG_X` | `if (IS_ENABLED(CONFIG_X))` | 後者讓關掉的程式碼仍被型別檢查 |
| 只在某些設定存在的欄位 | struct 裡 `#ifdef` | 無好替代 | 欄位存在與否隨設定變，`pahole` 結果也跟著變 |

## 踩雷集錦

1. **「加了括號就安全」**：括號解決優先順序，解決不了多次求值。`MAX(i++, j)` 的每個參數都有括號，`i` 還是多加了一次。要解決多次求值，用 `static inline` 或 GNU statement expression。
2. **「`#ifdef X` 和 `#if X` 一樣」**：不一樣。`#define X 0` 時，`#ifdef X` 成立、`#if X` 不成立。`#if` 遇到未定義的識別字當成 0（`-Wundef` 會警告）。讀到 `#if` 時要問「這個巨集定義成什麼值」，讀到 `#ifdef` 時只問「有沒有定義」。
3. **「用 `-D` 模擬平台，投影結果就是那個平台的程式碼」**：Lua 的 `LUA_DIRSEP` 和 Redis 的 `__GLIBC__` 都說明了不是。平台巨集可能來自編譯器內建（`_WIN32`、`__linux__`）或系統標頭（`__GLIBC__`），手動 `-D` 只模擬了你想到的那一個。
4. **「IDE 的跳到定義會告訴我答案」**：IDE 用它自己猜的那組巨集做索引。`zmalloc.c` 裡的 `free` 在 jemalloc build 下是 `je_free`，IDE 很可能跳到 glibc。沒有 `compile_commands.json` 的 IDE 對條件編譯的判斷常常是錯的（Ch 49）。
5. **「`#if 0` 裡的程式碼是被註解掉的舊程式碼」**：不一定。Lua 的 `ljumptab.h` 用 `#if 0 ... #endif` 包住一段**說明文字**（如何用 sed 重新產生那張表），因為那段文字含有 `*/`，不能放在 C 註解裡。Lua 的 `lvm.c` 主迴圈裡也有 `#if 0` 包住的除錯 printf，是作者留給自己的開關。

## 進階：再往深一層

- **`cpp -fdirectives-only`**：只處理指令、不展開巨集，是介於 `unifdef` 和完整 `gcc -E` 之間的選擇。對「我想看條件編譯解掉後的樣子，但不要巨集展開」很有用，不過它仍會把 `#include` 的內容貼進來。
- **coan**：比 unifdef 更強的投影工具，能處理 `#if` 中的運算式並報告「哪些巨集組合下這行會被編譯」。不在本環境中，需要時從原始碼裝到 `~/.local`。
- **`-Wundef`**：讓 `#if FOO` 在 `FOO` 未定義時發出警告，kernel 在 `scripts/Makefile.extrawarn:12` 把它加進 `KBUILD_CFLAGS`，預設就開。所以 kernel 的 `#if X` 若 `X` 未定義，build 時會有警告，維護者通常會修掉；但警告不是錯誤，不能百分之百保證。
- **用 compile_commands.json 投影任何檔案**：拿到某個 `.c` 檔的真實編譯指令，把 `-c ... -o x.o` 換成 `-E -P`，就得到那個檔案在你的設定下的完整前置處理結果。kernel 有更方便的 `make fs/pipe.i`，會用正確的旗標產生 `.i` 檔。本章撰寫時 build 目錄還在建置中，這一招在 [Ch 50](./50-compiler-as-reading-tool.md) 實際示範。

## 動手練習

1. 對 Lua 的 `lvm.c` 跑 `gcc -E -P -I. -DLUA_USE_LINUX lvm.c > lvm.i`，在 `lvm.i` 裡找 `luaV_execute`，看 `vmcase(OP_MOVE)` 變成了什麼。再加上 `-DLUA_USE_JUMPTABLE=0` 重做一次，比較兩者。（練習 B 第 1 題會完整拆解。）
2. 用 `unifdef -s` 列出 `redis-7.2.17/src/server.h` 的所有條件巨集。哪幾個是平台相關的？哪幾個是功能開關？
3. 用 `/tmp/ifdepth.awk` 看 `lua-5.4.9/luaconf.h` 的骨架，找出最深的一層巢狀在哪裡、在處理什麼問題。
4. 寫一個 `#define CUBE(x) x*x*x`，用 `gcc -E` 展開 `CUBE(a+1)` 和 `CUBE(i++)`，在跑程式之前先預測結果，再跑程式驗證。

## 本章重點整理

- 前置處理器是 token 層級的文字取代，不懂型別與優先順序；function-like macro 的參數要加括號、本體要加括號、多敘述要包 `do { } while (0)`，但多次求值無法靠括號解決。
- `gcc -E` 是讀巨集的第一工具；`-P` 去 line marker、`-dM` 列出最終巨集、`-H` 印 include 樹。
- 讀 `#ifdef` 叢林三步：看骨架（awk 或 `unifdef -s`）→ 決定實際設定（`make -n`、`.config`）→ 投影（`unifdef -D/-U` 保留原始碼樣貌，`gcc -dM -E` 問最終結果）。
- 投影結果取決於 include 順序與編譯器內建巨集；最可靠的投影是用真實的編譯指令。
- kernel 的風格偏好 `IS_ENABLED()` 和標頭檔 stub，`.c` 檔裡的函式內 `#ifdef` 是少數例外。

## 自我檢核

- [ ] 不看本章，寫出 `MAX(a, b)` 的三個問題版本，並說出每一個錯在哪裡、哪一個靠括號修不好。
- [ ] 為什麼 `do { } while (0)` 能解決 `{ }` 解決不了的 `if/else` 問題？用你自己的話解釋那個分號去了哪裡。
- [ ] 有人給你一個 3000 行、充滿 `#ifdef` 的檔案，請你解釋某個函式在 production build 下的行為。列出你的步驟與每一步用的工具。
- [ ] 我們第一次投影 `zmalloc.h` 的 libc 設定時得到了錯誤結果。原因是什麼？這個教訓怎麼套用到 kernel 的標頭檔？
- [ ] `#ifdef CONFIG_X` 和 `if (IS_ENABLED(CONFIG_X))` 在執行期效能相同，那 kernel 為什麼偏好後者？什麼情況下必須用前者？
- [ ] `unifdef` 和 `gcc -E -P -D...` 都能投影，各自的輸出有什麼不同？什麼時候選哪一個？

## 延伸閱讀

### 官方文件

- **The C Preprocessor（GCC 手冊）** — gcc.gnu.org/onlinedocs/cpp/
  - **讀哪裡**：「Macros」一章的「Macro Pitfalls」小節（運算子優先順序、分號吞噬、重複副作用都有完整例子）；「Preprocessor Output」一節解釋 line marker 的旗標意義；「Invocation」一節列出 `-dM`、`-dD`、`-H`、`-fdirectives-only`。
  - **前提**：無。
  - **為什麼值得讀**：kernel coding style 第 12 節最後一句就說「The cpp manual deals with macros exhaustively」。本章的陷阱分類直接對應這份手冊。
- **Linux kernel coding style 第 12 節「Macros, Enums and RTL」與第 21 節「Conditional Compilation」** — v6.18 `Documentation/process/coding-style.rst` 第 795 行與第 1159 行
  - **讀哪裡**：第 12 節列的五種「Things to avoid when using macros」；第 21 節整節（stub、`IS_ENABLED`、`#endif` 註解）。
  - **和本章的關聯**：知道 kernel 的規則，才知道哪些寫法是「慣例」、哪些是「例外」，例外通常有它的歷史理由。

### 工具

- **unifdef** — Tony Finch，dotat.at/prog/unifdef（`unifdef -V` 輸出的 URL）；Linux tree 的 `scripts/unifdef.c` 是同一份程式
  - **讀哪裡**：man page 的 `-D`、`-U`、`-s`、`-k`（處理 `#if 0` 這類常數條件）四個選項，以及回傳值的定義。
  - **和本章的關聯**：本章所有程式碼投影都用它；比 `gcc -E` 更適合「給人讀」。

### 論文

- **An Empirical Analysis of C Preprocessor Use** — Michael D. Ernst, Greg J. Badros, David Notkin，IEEE Transactions on Software Engineering 28(12)（2002）
  - **核心貢獻**：分析 26 個 C 套件中前置處理器的實際用法，統計巨集的種類、條件編譯的密度，並分類出「難以用語言特性取代」的用法。
  - **讀哪裡**：第 3 節的巨集分類與第 4 節的條件編譯分析。
  - **和本章的關聯**：本章「object-like / function-like」「平台 / 功能開關」的分類有實證基礎；也解釋了為什麼前置處理器讓程式分析工具（以及讀者）這麼痛苦。

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：第 5.6 節「Preprocessor」（巨集、條件編譯的讀法），以及第 9 章談到 build 設定如何影響原始碼的部分。

巨集不只能取代常數和小函式；當 `#` 與 `##` 登場，巨集開始**產生程式碼**——那是讀碼時「grep 找不到定義」的頭號原因。

→ [Ch 14 巨集元程式：X-macro、token pasting 與巨集生成的程式碼](./14-macro-metaprogramming.md)
