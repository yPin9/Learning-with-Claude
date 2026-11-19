# Ch 3 — 指標入門重建：位址、解參考與指標算術

> **目標**：從零重建指標的心智模型：位址是什麼、`&` 和 `*` 各做什麼、指標的「型別」到底決定了什麼、指標算術為什麼以元素為單位；能用 GDB 的 `x` 指令直接看記憶體驗證自己的理解，並認得五種最常見的指標崩潰在 ASan 下長什麼樣子。

> **對應原文**：[指標篇](https://hackmd.io/@sysprog/c-pointer) 開頭的「即便你對指標毫無掌握，你還是能寫程式」「先羅列你已經知道的部分」「回頭看 C 語言規格書」等節，以及 [開發工具和規格標準篇](https://hackmd.io/@sysprog/c-standards) 的「不要急著印出位址，善用 GDB」

> **環境**：WSL2 Ubuntu 22.04 x86_64、gcc 11.4.0、gdb 12.1。本章所有位址每次執行不同（ASLR），GDB 底下則固定（GDB 預設關閉 ASLR）。

## 為什麼需要這個？

jserv 在指標篇開頭引了一個觀察：K&R 那本書直到第 5 章、約全書一半才談指標，代表「不懂指標也能掌握多數 C 語言功能」。那為什麼大家都說指標是 C 的核心？

因為只要你想做下面任何一件事，就躲不掉：

- **讓函式修改呼叫者的變數**：C 的函式參數一律**傳值**（pass by value），函式拿到的是副本
- **處理陣列與字串**：陣列傳進函式時只剩一個指標；`char *` 就是 C 的字串
- **動態配置記憶體**：`malloc` 回傳的只有一個位址
- **建構資料結構**：鏈結串列、樹、圖，節點之間靠指標相連
- **和硬體對話**：裝置暫存器在固定位址，要用指標去讀寫（[Ch 42](./42-embedded-quiz-part1.md)）

其他語言把這些藏在「參考」（reference）後面，C 把它直接交給你。好處是你知道每個操作的成本；壞處是錯了沒有人攔你——這章後半的崩潰示範就是代價。

指標篇的副標題是「指標扮演『記憶體』和『物件』之間的橋樑」。[Ch 2](./02-types-and-objects.md) 講了物件（一塊有型別的記憶體），這章就是那座橋。

## 先建立直覺

記憶體是一條很長的街，每間房子（1 byte）有門牌號碼（位址）。

```
門牌（位址）:  ...  0x...a7c  0x...a7d  0x...a7e  0x...a7f  0x...a80 ... 0x...a87  ...
              ────┬─────────┬─────────┬─────────┬─────────┬───── ─ ─────────┬────
 房子內容:        │   0a    │   00    │   00    │   00    │  7c 3a 84 87 fd 7f 00 00 │
              ────┴─────────┴─────────┴─────────┴─────────┴───── ─ ─────────┴────
                  └────────── int x = 10 ────────┘         └─── int *p = &x ───────┘
                                                              p 的內容 = x 的門牌號碼
```

- `x` 是一個佔 4 間房子的 `int` 物件，裡面住著 10
- `p` 也是一個物件（佔 8 間房子），**裡面住的是一個門牌號碼**——`x` 的位址
- `&x`：「`x` 住哪？」→ 得到門牌號碼
- `*p`：「照 `p` 裡寫的門牌找過去，看那裡住什麼」→ 得到 10

指標**就是一個存放位址的變數**，沒有更神秘的東西。卡住的人通常是把「指標本身」和「指標指向的東西」混在一起。這張圖的關鍵是：`p` 有自己的位址（`&p`）、自己的內容（`x` 的位址），以及它指向的內容（`*p`）。三樣東西，三個不同的值。

這個門牌比喻有一處要小心：真實的程式看到的是**虛擬位址**，不是實體記憶體的位址，由 CPU 的 MMU 和作業系統翻譯（[Ch 15](./15-memory-hierarchy.md)）。對寫 C 的人來說，把虛擬位址當門牌號碼完全成立；只是別以為兩支程式裡相同的位址指的是同一塊實體記憶體。

## `&`、`*` 與指標的三個值

```c
#include <stdio.h>

int main(void)
{
    int x = 10;
    int *p = &x;          /* p 存的是 x 的位址 */

    printf("x  = %d\n", x);
    printf("&x = %p\n", (void *) &x);
    printf("p  = %p\n", (void *) p);
    printf("*p = %d\n", *p);
    printf("&p = %p (p 自己也有位址)\n", (void *) &p);

    *p = 20;              /* 透過 p 改 x */
    printf("after *p = 20: x = %d\n", x);

    printf("sizeof x = %zu, sizeof p = %zu, sizeof *p = %zu\n",
           sizeof x, sizeof p, sizeof *p);
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra -g basic.c -o basic && ./basic
x  = 10
&x = 0x7ffd87843a7c
p  = 0x7ffd87843a7c
*p = 10
&p = 0x7ffd87843a80 (p 自己也有位址)
after *p = 20: x = 20
sizeof x = 4, sizeof p = 8, sizeof *p = 4
```

再跑一次，`&x` 變成 `0x7ffdb05376fc`：位址每次不同，是 ASLR（Address Space Layout Randomization，位址空間配置隨機化）讓 stack 的起點每次隨機。所以**不要把位址的具體數值當成程式邏輯的一部分**。

幾個細節：

- **`%p` 要求 `void *`**（N1570 7.21.6.1p8），所以每個指標都轉型 `(void *)`。在 x86-64 上不轉也印得出來，但標準只對 `void *` 定義了 `%p`
- **`sizeof p` 是 8**：在 64 位元平台上所有物件指標都是 8 bytes，不管指向什麼
- **`sizeof *p` 是 4**：`*p` 的型別是 `int`。`sizeof` 不會求值運算元，所以就算 `p` 是 NULL，`sizeof *p` 也安全

`&` 的正式名稱是 address-of 運算子，`*` 是 indirection（間接）運算子，定義在 N1570 6.5.3.2。jserv 特別提醒：在指標語境下 `&` 要念 "address of"，別念成 "and"。

### 宣告語法：`int *p` 的讀法

```c
int *p, q;      /* q 是 int，不是 int * */
```

```
multi.c:6:7: warning: assignment to ‘int’ from ‘int *’ makes integer from pointer without a cast [-Wint-conversion]
    6 |     q = &x;
```

C 的宣告語法是「**宣告長得像使用**」（declaration mirrors use）：`int *p` 讀作「`*p` 是一個 `int`」，因此 `p` 是指向 `int` 的指標。`*` 黏在變數名稱上，而不是黏在型別上，所以 `int *p, q;` 裡只有 `p` 是指標。這個設計的來源與 `int (*fp)(void)` 這類複雜宣告的讀法，留到 [Ch 6](./06-history-of-c.md) 與 [Ch 8](./08-arrays-functions-pointers.md)。本課的程式碼風格是 `int *p`（星號靠變數），和 Linux 核心一致，正是為了提醒這件事。

同一個 `*` 符號有兩種完全不同的角色：

| 出現位置 | 意義 | 例子 |
|---|---|---|
| 宣告中 | 「這是指標型別」 | `int *p;` |
| 運算式中（一元） | 解參考：「去那個位址取東西」 | `*p = 20;` |
| 運算式中（二元） | 乘法 | `a * b` |

## 底層機制：指標的「型別」決定了什麼？

所有指標都是 8 bytes 的位址，那 `int *` 和 `char *` 差在哪？差在**編譯器怎麼使用它**：

1. **解參考時讀幾個 bytes、怎麼解讀**（[Ch 2](./02-types-and-objects.md) 的「眼鏡」）
2. **指標算術一步走多遠**

```c
#include <stdint.h>
#include <stdio.h>

int main(void)
{
    uint32_t v = 0x11223344;
    uint32_t      *p32 = &v;
    uint16_t      *p16 = (uint16_t *) &v;      /* 只做算術，不解參考 */
    unsigned char *p8  = (unsigned char *) &v; /* 字元型別可合法讀任何物件 */

    printf("p32 = %p, p16 = %p, p8 = %p\n", (void *) p32, (void *) p16, (void *) p8);
    printf("*p32 = 0x%08x\n", *p32);
    printf("p8[0..3] = %02x %02x %02x %02x\n", p8[0], p8[1], p8[2], p8[3]);

    printf("p32 + 1 -> +%td bytes\n", (char *) (p32 + 1) - (char *) p32);
    printf("p16 + 1 -> +%td bytes\n", (char *) (p16 + 1) - (char *) p16);
    printf("p8  + 1 -> +%td bytes\n", (char *) (p8 + 1) - (char *) p8);
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra -g types.c -o types && ./types
p32 = 0x7ffeb45d064c, p16 = 0x7ffeb45d064c, p8 = 0x7ffeb45d064c
*p32 = 0x11223344
p8[0..3] = 44 33 22 11
p32 + 1 -> +4 bytes
p16 + 1 -> +2 bytes
p8  + 1 -> +1 bytes
```

三個指標的**值完全相同**，差別全在型別：

```
位址:       ...64c   ...64d   ...64e   ...64f   ...650
          ┌────────┬────────┬────────┬────────┬────────
內容:     │   44   │   33   │   22   │   11   │  ...
          └────────┴────────┴────────┴────────┴────────
p8        ↑ p8+1   ↑
p16       ↑                 ↑ p16+1
p32       ↑                                   ↑ p32+1
*p32 讀 4 bytes → 0x11223344
*p8  讀 1 byte  → 0x44（little-endian：最低位元組在最低位址）
```

為什麼 `p16` 只做算術、不解參考？用 `uint16_t *` 去讀一個 `uint32_t` 物件，違反 N1570 6.5p7 的**有效型別規則**（effective type rule，俗稱 strict aliasing）：一個物件只能透過相容的型別或字元型別存取。違反是未定義行為，`-O2` 下編譯器可能假設 `*p16` 和 `*p32` 不會指到同一處而重排讀寫。**字元型別是例外**，所以 `p8` 合法。要可攜地把位元組重新解讀成別的型別，用 `memcpy`。這條規則在 [Ch 7](./07-pointers-by-the-standard.md) 與 [Ch 34](./34-undefined-behavior.md) 會完整展開。

`%td` 是印 `ptrdiff_t` 的格式。為什麼要先轉成 `char *` 再相減？因為指標相減的結果是「相差幾個元素」，不是幾個 bytes；轉成 `char *`（元素大小 1）才會得到 bytes 數。

### 指標算術的規則

N1570 6.5.6p8–9 把指標算術定義在**陣列**上：

- `p + n`：`p` 指向陣列某元素時，結果指向往後第 `n` 個元素。機械碼層面就是「位址 + n × sizeof(*p)」
- 結果只能落在**陣列內**或**最後一個元素的下一個位置**（one past the end），超出就是未定義行為——**連算出來都不行，不必等到解參考**
- one-past-the-end 可以拿來比較，但不能解參考
- `q - p`：兩個指向**同一陣列**的指標相減，得到 `ptrdiff_t` 型別的元素個數差
- 單一物件視為長度 1 的陣列，所以 `&v + 1` 合法（但不能解參考）

為什麼規格要限制在同一陣列？因為 C 要能在**分段記憶體**（segmented memory，如 8086 的 segment:offset）上實作。在那種機器上，兩個不同物件可能在不同段，相減或比較根本沒有意義。現代平面位址空間上這條限制看起來多餘，但編譯器的最佳化仍會依賴它（[Ch 34](./34-undefined-behavior.md)）。

## 陣列與指標：`a[i]` 就是 `*(a + i)`

```c
#include <stdio.h>

static int sum(const int *p, size_t n)   /* 陣列傳進來就只剩指標 */
{
    int s = 0;
    for (const int *end = p + n; p < end; p++)
        s += *p;
    return s;
}

int main(void)
{
    int a[5] = {10, 20, 30, 40, 50};
    int *p = a;                     /* 陣列名稱轉成 &a[0] */

    printf("a[2] = %d, *(a + 2) = %d, *(p + 2) = %d, p[2] = %d, 2[a] = %d\n",
           a[2], *(a + 2), *(p + 2), p[2], 2[a]);

    int *q = &a[4];
    printf("q - p = %td (元素個數，不是 bytes)\n", q - p);

    printf("sum = %d\n", sum(a, sizeof a / sizeof a[0]));

    p++;                            /* 指標可以移動 */
    printf("after p++: *p = %d\n", *p);
    /* a++;  陣列名稱不是變數，這行編不過 */
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra -g walk.c -o walk && ./walk
a[2] = 30, *(a + 2) = 30, *(p + 2) = 30, p[2] = 30, 2[a] = 30
q - p = 4 (元素個數，不是 bytes)
sum = 150
after p++: *p = 20
```

把 `a++` 的註解拿掉：

```
walk_bad.c: In function ‘main’:
walk_bad.c:26:6: error: lvalue required as increment operand
   26 |     a++; /*  陣列名稱不是變數，這行編不過 */
      |      ^~
```

兩條規則解釋了所有現象：

1. **下標就是指標算術**（6.5.2.1p2）：`E1[E2]` 定義為 `(*((E1)+(E2)))`。加法可交換，所以 `2[a]` 等於 `*(2 + a)` 等於 `a[2]`。這是語言定義的副作用，**不要在真實程式這樣寫**，但它證明了下標沒有任何魔法
2. **陣列會轉換成指標**（6.3.2.1p3）：陣列型別的運算式，除了當 `sizeof`、一元 `&` 的運算元，或用來初始化字元陣列的字串常值之外，會被轉換成「指向第一個元素的指標」。這個轉換俗稱 **array decay**（陣列退化）

所以 `int *p = a;` 裡的 `a` 退化成 `&a[0]`。但 `a` **本身不是指標**：它沒有一個存放位址的儲存空間，轉換出來的指標是個**值**而不是**物件**，所以不能 `a++`（錯誤訊息說它不是 lvalue，即「可以出現在賦值左邊、代表一個物件」的運算式；[Ch 7](./07-pointers-by-the-standard.md) 會精確定義）。

`sum` 的寫法 `for (const int *end = p + n; p < end; p++)` 是 C 程式碼中很常見的「指標走訪」慣用法：`end` 是 one-past-the-end，合法可比較。`const int *` 表示「透過這個指標不能修改所指的 `int`」，函式只讀不寫時應該這樣宣告。

## 用 GDB 直接看記憶體

`printf` 位址只能看到一個數字。GDB 讓你停在任意一行，用多種「眼鏡」看同一塊記憶體：

```
$ gdb -q ./walk
(gdb) break 21
Breakpoint 1 at 0x1264: file walk.c, line 22.
(gdb) run
Breakpoint 1, main () at walk.c:22
22	    printf("sum = %d\n", sum(a, sizeof a / sizeof a[0]));
(gdb) print a
$1 = {10, 20, 30, 40, 50}
(gdb) print p
$2 = (int *) 0x7fffffffe120
(gdb) print *p
$3 = 10
(gdb) print &a[4]
$4 = (int *) 0x7fffffffe130
(gdb) print q - p
$5 = 4
(gdb) x/5dw a
0x7fffffffe120:	10	20	30	40
0x7fffffffe130:	50
(gdb) x/20xb a
0x7fffffffe120:	0x0a	0x00	0x00	0x00	0x14	0x00	0x00	0x00
0x7fffffffe128:	0x1e	0x00	0x00	0x00	0x28	0x00	0x00	0x00
0x7fffffffe130:	0x32	0x00	0x00	0x00
(gdb) x/2gx a
0x7fffffffe120:	0x000000140000000a	0x000000280000001e
(gdb) ptype p
type = int *
(gdb) ptype a
type = int [5]
(gdb) print *a@5
$7 = {10, 20, 30, 40, 50}
```

（第 21 行是空行，GDB 自動把中斷點移到下一個有程式碼的第 22 行。）

`x`（examine）指令的格式是 `x/<個數><格式><單位> <位址>`：

| 格式字母 | 意義 | 單位字母 | 大小 |
|---|---|---|---|
| `x` | 十六進位 | `b` | byte（1） |
| `d` | 有號十進位 | `h` | halfword（2） |
| `u` | 無號十進位 | `w` | word（4） |
| `c` | 字元 | `g` | giant（8） |
| `s` | 字串 | | |
| `i` | 指令（反組譯） | | |

同一塊記憶體，三種看法：`x/5dw` 以 4 bytes 一組的十進位看是 `10 20 30 40 50`；`x/20xb` 一個 byte 一個 byte 看，看得到 little-endian 的排列；`x/2gx` 以 8 bytes 一組看，兩個 `int` 被併成一個數字，`0x000000140000000a` 的低半部是 `a[0]` = 0x0a，高半部是 `a[1]` = 0x14。這就是 Ch 2 的「眼鏡」在除錯器裡的樣子。

注意 GDB 的 `w` 是 4 bytes、`g` 是 8 bytes——和 x86 文件裡 "word" 指 2 bytes 的用法不同，別混淆。

`ptype` 揭露了 `a` 和 `p` 的根本差異：`int [5]` 對 `int *`。`*a@5` 是 GDB 專屬語法（「從 `*a` 開始連續 5 個」），對動態配置的陣列特別有用，因為 `print p` 只會印出一個位址。

### 驗證「傳值」：swap

```c
#include <stdio.h>

static void swap_wrong(int a, int b)   /* 拿到的是副本 */
{
    int t = a;
    a = b;
    b = t;
}

static void swap(int *a, int *b)       /* 拿到的是位址 */
{
    int t = *a;
    *a = *b;
    *b = t;
}

int main(void)
{
    int x = 1, y = 2;
    swap_wrong(x, y);
    printf("after swap_wrong: x = %d, y = %d\n", x, y);
    swap(&x, &y);
    printf("after swap:       x = %d, y = %d\n", x, y);
    return 0;
}
```

```
after swap_wrong: x = 1, y = 2
after swap:       x = 2, y = 1
```

在 GDB 裡停在 `swap`，用 `up` 切到呼叫者 `main` 的框架：

```
(gdb) break swap
(gdb) run
Breakpoint 1, swap (a=0x7fffffffe0e0, b=0x7fffffffe0e4) at swap.c:12
(gdb) print *a
$2 = 1
(gdb) up
#1  0x0000555555555224 in main () at swap.c:22
(gdb) print &x
$3 = (int *) 0x7fffffffe0e0
(gdb) print &y
$4 = (int *) 0x7fffffffe0e4
(gdb) x/2dw &x
0x7fffffffe0e0:	1	2
```

`swap` 的參數 `a` 的值 `0x7fffffffe0e0` 正是 `main` 裡 `&x`。C 依然是傳值——只是傳的值剛好是位址。`swap` 透過位址回頭改到 `main` 的 `x`。`swap_wrong` 改的是自己的副本，函式返回副本就消失了。

## 崩潰博物館：五種指標錯誤與 ASan

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int *dangling_stack(void)
{
    int local = 123;
    int *p = &local;
    return p;                 /* 回傳區域變數的位址：函式返回後 local 已死 */
}

int main(int argc, char *argv[])
{
    int which = argc > 1 ? atoi(argv[1]) : 0;

    if (which == 1) {         /* null pointer dereference */
        int *p = NULL;
        printf("%d\n", *p);
    } else if (which == 2) {  /* heap use-after-free */
        int *p = malloc(sizeof *p);
        *p = 42;
        free(p);
        printf("%d\n", *p);
    } else if (which == 3) {  /* stack buffer overflow */
        char buf[8];
        strcpy(buf, argc > 2 ? argv[2] : "0123456789");
        printf("%s\n", buf);
    } else if (which == 4) {  /* use after return */
        int *p = dangling_stack();
        printf("%d\n", *p);
    } else if (which == 5) {  /* double free */
        int *p = malloc(16);
        free(p);
        free(p);
    }
    return 0;
}
```

### 不開 sanitizer：五種錯誤，五種反應

```
$ gcc -std=c11 -Wall -Wextra -g -O0 crash.c -o crash
$ ./crash 1
Segmentation fault (core dumped)          exit=139
$ ./crash 2
-804239015                                exit=0
$ ./crash 3
*** stack smashing detected ***: terminated
Aborted (core dumped)                     exit=134
$ ./crash 4
123                                       exit=0
$ ./crash 5
free(): double free detected in tcache 2
Aborted (core dumped)                     exit=134
```

（exit 值是本書加在右側的；139 = 128 + 11（SIGSEGV），134 = 128 + 6（SIGABRT），這是 shell 回報「被訊號殺死」的慣例。）

這張表是本章最重要的一課：

| # | 錯誤 | 不開 sanitizer 的表現 | 誰抓到的 |
|---|---|---|---|
| 1 | NULL 解參考 | 立刻 segfault | 硬體 + 核心：位址 0 所在的頁面沒有映射 |
| 2 | use-after-free | **印出垃圾值，正常結束** | 沒人 |
| 3 | stack 越界寫 | 函式返回時中止 | gcc 的 stack canary（`-fstack-protector-strong`，Ubuntu 預設開） |
| 4 | use-after-return | **印出 123，看起來完全正確** | 沒人 |
| 5 | double free | 中止 | glibc `free` 的 tcache 檢查 |

最危險的是 2 和 4：**錯誤的程式印出了「合理」的結果**。#4 印出 123 是因為 `local` 那塊 stack 還沒被別的函式覆蓋——換一個呼叫順序、換個最佳化等級，就是別的值。#2 印出的 `-804239015` 是 glibc 在已釋放區塊裡寫入的 tcache 管理資料（[Ch 17](./17-malloc-internals.md)），被當成 `int` 讀出來。#3 的 `printf` 根本沒印出東西，因為 `abort` 不會清空 stdout 的緩衝區。

NULL 解參考為什麼一定 segfault？其實**不一定**。在 Linux 使用者空間，核心不允許映射最低的位址範圍（`vm.mmap_min_addr`，本書的 WSL2 環境 `cat /proc/sys/vm/mmap_min_addr` 得到 65536），所以讀位址 0 會觸發頁錯誤。但在標準眼中，解參考 null pointer 是未定義行為（6.5.3.2p4），編譯器可以假設它不發生並據此最佳化；在沒有 MMU 的嵌入式系統上，位址 0 可能是合法記憶體（例如中斷向量表）。[Ch 10](./10-strings-and-null.md) 與 [Ch 34](./34-undefined-behavior.md) 會看到 Linux 核心因此出過的真實漏洞。

### 開 ASan：每一種都有名字和行號

```
$ gcc -std=c11 -Wall -Wextra -g -O0 -fsanitize=address,undefined -fno-omit-frame-pointer crash.c -o crash_asan
```

**#1 NULL 解參考**（UBSan 先報，再由 ASan 攔下 SEGV）：

```
crash.c:18:9: runtime error: load of null pointer of type 'int'
==7093==ERROR: AddressSanitizer: SEGV on unknown address 0x000000000000 (pc 0x5fa361004655 bp 0x7ffeb556ded0 sp 0x7ffeb556de00 T0)
==7093==The signal is caused by a READ memory access.
    #0 0x5fa361004655 in main /home/ypp/c_sysprog_lab/p0/ptr/crash.c:18
```

**#2 use-after-free**：同時告訴你在哪裡用、在哪裡釋放、在哪裡配置

```
==7097==ERROR: AddressSanitizer: heap-use-after-free on address 0x502000000010 at pc 0x588af393377c bp 0x7ffea076f670 sp 0x7ffea076f660
READ of size 4 at 0x502000000010 thread T0
    #0 0x588af393377b in main /home/ypp/c_sysprog_lab/p0/ptr/crash.c:23
0x502000000010 is located 0 bytes inside of 4-byte region [0x502000000010,0x502000000014)
freed by thread T0 here:
    #0 0x73a5510b4537 in __interceptor_free ../../../../src/libsanitizer/asan/asan_malloc_linux.cpp:127
    #1 0x588af3933713 in main /home/ypp/c_sysprog_lab/p0/ptr/crash.c:22
previously allocated by thread T0 here:
    #0 0x73a5510b4887 in __interceptor_malloc ../../../../src/libsanitizer/asan/asan_malloc_linux.cpp:145
    #1 0x588af3933688 in main /home/ypp/c_sysprog_lab/p0/ptr/crash.c:20
```

ASan 怎麼知道已釋放？被 `free` 的區塊在 shadow memory 標成 `fd`（freed heap region），而且 ASan **不會立刻把它還給配置器重用**，而是放進一個隔離區（quarantine）一段時間，讓之後的存取能被抓到（Ch 0 介紹過 shadow memory）。

**#3 stack 越界**：

```
==7100==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7ffec0506538 at pc 0x7bee202544bf bp 0x7ffec05064c0 sp 0x7ffec0505c68
WRITE of size 11 at 0x7ffec0506538 thread T0
    #0 0x7bee202544be in __interceptor_strcpy ../../../../src/libsanitizer/asan/asan_interceptors.cpp:440
    #1 0x59653f8f4870 in main /home/ypp/c_sysprog_lab/p0/ptr/crash.c:26
Address 0x7ffec0506538 is located in stack of thread T0 at offset 40 in frame
    #0 0x59653f8f448d in main /home/ypp/c_sysprog_lab/p0/ptr/crash.c:13
```

`WRITE of size 11`：`"0123456789"` 是 10 個字元加結尾的 `'\0'`，寫進 8 bytes 的 `buf`。`strcpy` 不知道目的地多大，這正是 [Ch 14](./14-function-call-security.md) buffer overflow 的起點。

**#4 use-after-return**：預設**抓不到**！

```
$ ./crash_asan 4
123
$ ASAN_OPTIONS=detect_stack_use_after_return=1 ./crash_asan 4
==7110==ERROR: AddressSanitizer: stack-use-after-return on address 0x7742a6500060 at pc 0x6069c3690903 bp 0x7fff0dfd70e0 sp 0x7fff0dfd70d0
READ of size 4 at 0x7742a6500060 thread T0
    #0 0x6069c3690902 in main /home/ypp/c_sysprog_lab/p0/ptr/crash.c:30
Address 0x7742a6500060 is located in stack of thread T0 at offset 32 in frame
    #0 0x6069c3690378 in dangling_stack /home/ypp/c_sysprog_lab/p0/ptr/crash.c:6
```

偵測 use-after-return 需要把區域變數搬到 heap 上的「假 stack」，成本較高，所以在 gcc 11 的 ASan 下預設關閉，要用執行期選項 `ASAN_OPTIONS=detect_stack_use_after_return=1` 打開。較新的 clang/gcc 對這個預設值有調整，換版本時請以該版本的 sanitizer 文件為準，並實際用上面的程式驗證一次。

**#5 double free**：

```
==7104==ERROR: AddressSanitizer: attempting double-free on 0x502000000010 in thread T0:
    #0 0x75e18f0b4537 in __interceptor_free ../../../../src/libsanitizer/asan/asan_malloc_linux.cpp:127
    #1 0x57824441095b in main /home/ypp/c_sysprog_lab/p0/ptr/crash.c:34
```

### 不開 ASan 時：用 GDB 找 segfault

```
$ gdb -q -batch -ex "run 1" -ex bt -ex "print p" ./crash
Program received signal SIGSEGV, Segmentation fault.
0x00005555555552a4 in main (argc=2, argv=0x7fffffffe238) at crash.c:18
18	        printf("%d\n", *p);
#0  0x00005555555552a4 in main (argc=2, argv=0x7fffffffe238) at crash.c:18
$1 = (int *) 0x0
```

GDB 停在出事的那一行，`print p` 直接看到 `0x0`。這只對「立刻崩潰」的錯誤有效；#2、#4 根本不會崩潰，GDB 無從停起——這就是 sanitizer 存在的理由。

## 對比與取捨

| 偵測方式 | NULL 解參考 | UAF | stack 越界 | use-after-return | double free |
|---|---|---|---|---|---|
| 什麼都不開 | segfault | **沉默** | canary（返回時） | **沉默** | glibc 檢查（不保證） |
| `-Wall -Wextra`（gcc 11） | 本例沒有警告 | 本例沒有警告（gcc 12 起有 `-Wuse-after-free`） | 本例沒有警告 | 只有直接 `return &local` | 本例沒有警告 |
| ASan | 有 | 有 | 有（當下） | 需開選項 | 有 |
| Valgrind | 有 | 有 | **沒有**（stack 沒有 redzone） | 沒有 | 有 |

編譯器警告只抓得到「看得出來」的情況。直接寫 `return &local;`：

```
ret.c: In function ‘bad’:
ret.c:6:12: warning: function returns address of local variable [-Wreturn-local-addr]
    6 |     return &local;
      |            ^~~~~~
p = (nil)
```

注意輸出是 `p = (nil)`：gcc 不只警告，還**直接把回傳值換成 NULL**——既然回傳的位址不能合法使用，gcc 選擇讓它在被解參考時立刻崩潰，而不是安靜地讀到殘留值。但只要中間多一個變數（`int *p = &local; return p;`，`crash.c` 的寫法），gcc 11 就看不出來了。

## 踩雷集錦

1. **「陣列就是指標」**：不是。`sizeof` 揭穿一切：
   ```c
   static void f(int arr[10]) { printf("in f: sizeof arr = %zu\n", sizeof arr); }
   int a[10]; printf("in main: sizeof a = %zu\n", sizeof a); f(a);
   ```
   ```
   sizeofparam.c:5:47: warning: ‘sizeof’ on array function parameter ‘arr’ will return size of ‘int *’ [-Wsizeof-array-argument]
   in main: sizeof a = 40
   in f: sizeof arr = 8
   ```
   函式參數寫成 `int arr[10]`，編譯器會把它**調整**成 `int *arr`（6.7.6.3p7），那個 `10` 被忽略。所以 C 的函式要另外傳長度。陣列和指標的完整關係是 [Ch 8](./08-arrays-functions-pointers.md) 的主題。
2. **`*p++` 到底遞增誰**：後置 `++` 的優先順序高於一元 `*`，所以 `*p++` 是 `*(p++)`：取 `p` 指的值，然後**指標**前進。實測：
   ```c
   int a[3] = {10, 20, 30}; int *p = a;
   int x = *p++;     /* x = 10，p 指向 a[1] */
   int y = (*p)++;   /* y = 20，a[1] 變成 21 */
   int z = *++p;     /* p 先前進到 a[2]，z = 30 */
   ```
   ```
   x = 10, *p = 20
   y = 20, a[1] = 21
   z = 30
   ```
3. **「`free(p)` 之後 `p` 就變成 NULL」**：不會。`free` 拿到的是 `p` 的副本（傳值！），改不到 `p` 本身。`p` 仍存著舊位址，成了**懸空指標**（dangling pointer）。依 6.2.4p2，物件生命週期結束後，指向它的指標值變成**不確定**（indeterminate）——連讀取 `p` 本身的值嚴格說都有問題。慣用法是 `free(p); p = NULL;`，讓後續誤用變成可預測的 NULL 崩潰。
4. **「未初始化的指標是 NULL」**：區域指標變數未初始化時值是不確定的，可能指向任何地方。寫進去可能悄悄破壞別的資料。宣告時就初始化：`int *p = NULL;`。只有 static 儲存期的指標才保證初始化為 null（6.7.9p10）。
5. **「把指標加到陣列外再減回來沒關係」**：`p + 100 - 99` 在 `p + 100` 超出陣列時就已經是未定義行為（6.5.6p8），即使最後結果落回陣列內。這不是學究：編譯器會利用「指標算術不越界」推論迴圈次數。

## 進階：再往深一層

- **指標與整數的轉換**：`(uintptr_t) p` 把指標轉成整數是實作定義的（6.3.2.3p5–6）。在 x86-64 上就是位址數值本身，但標準不保證；CHERI 這類硬體上，指標帶有額外的權限位元，不是單純的整數。[Ch 33](./33-tricks-pointers-and-performance.md) 談利用指標低位元存放標記的技巧，就是建立在「對齊的指標低位元恆為 0」這個實作性質上。
- **pointer provenance（指標來源）**：兩個數值相同的指標，可能因為「來自不同物件」而在最佳化器眼中不等價。WG14 有一份技術規格草案（TS 6010）試圖把這件事寫進標準。這是現代 C 語意最前沿的議題之一，[Ch 7](./07-pointers-by-the-standard.md) 會簡介。
- **ASan 的原理細節**：位址到 shadow 的換算是 `(addr >> 3) + 0x7fff8000`（x86-64 Linux 的預設偏移）。在 GDB 裡停在 ASan 程式中，可以用 `x/xb` 直接看 shadow byte。
- **Fil-C**：jserv 指標篇提到的一個記憶體安全 C 實作，透過在執行期追蹤每個指標的邊界（capability），讓本章五種錯誤全部變成確定的錯誤中止，代價是效能與相容性。[Ch 10](./10-strings-and-null.md) 介紹。

## 動手練習

1. 在 `walk.c` 的 `sum` 裡設中斷點，用 GDB 的 `print p`、`print end - p`、`x/4dw p` 觀察每一輪迴圈 `p` 的變化。（提示：`display *p` 讓 GDB 每次停下都印出。）
2. 把 `crash.c` 的 case 3 換成 `./crash_asan 3 0123456`（7 個字元 + `'\0'` 剛好 8 bytes）和 `./crash_asan 3 01234567`，哪個會報錯？差一個 byte 的 off-by-one 是最常見的字串錯誤。
3. 寫一個 `void reverse(int *a, size_t n)`，只用指標（兩個指標從兩端往中間走），不用下標。用 ASan 編譯並測試 `n = 0`、`n = 1`、`n = 2`、`n = 5`。`n = 0` 時你的 `a + n - 1` 合法嗎？
4. 在 `types.c` 加上 `printf("*p16 = 0x%04x\n", *p16);`，分別用 `-O0`、`-O2` 編譯，再加 `-fstrict-aliasing -Wstrict-aliasing=1` 看 gcc 是否警告。結果相同不代表合法，說說為什麼。

## 本章重點整理

- 指標是存放位址的物件；`&` 取位址，`*` 照位址取物件；一個指標有自己的位址、自己的值、所指的值三樣東西
- 指標型別決定解參考讀幾個 bytes、怎麼解讀，以及 `p + 1` 走幾個 bytes
- `a[i]` 定義為 `*(a + i)`；陣列在多數運算式中退化成指向首元素的指標，但陣列本身不是指標（`sizeof`、`&`、不能 `++`）
- 指標算術只在同一陣列內（含 one-past-the-end）有定義
- 記憶體錯誤常常**不會崩潰**；ASan 能把 UAF、越界、double free 變成帶行號的報告，use-after-return 要額外開選項

## 自我檢核

- [ ] 不看本章，畫出 `int x = 10; int *p = &x;` 的記憶體圖，標出 `x`、`&x`、`p`、`&p`、`*p` 各是什麼
- [ ] 能解釋為什麼 `int *`、`char *` 都是 8 bytes，卻不能互相取代
- [ ] 能用規格書的定義說明 `2[a]` 為什麼合法
- [ ] 有人說「C 有傳參考」，你能用 GDB 的 swap 實驗反駁並解釋真正發生的事
- [ ] 說出五種崩潰中哪兩種在不開 sanitizer 時最危險、為什麼
- [ ] 能在 GDB 中用 `x` 指令以三種不同單位查看同一個陣列，並解釋輸出的差異

## 延伸閱讀

### 書籍

- **《The C Programming Language》（2nd ed.）** — Kernighan & Ritchie（Prentice Hall，1988）
  - **讀哪幾章**：第 5 章「Pointers and Arrays」5.1–5.5。語言設計者親自解釋指標與陣列的關係，篇幅短而精確
  - **注意**：書以 C89 為準，有些寫法（如隱式 `int`）已過時，但第 5 章的概念完全適用
- **《Expert C Programming: Deep C Secrets》** — Peter van der Linden（Prentice Hall，1994）
  - **這本書的定位**：專講 C 的陰暗角落，第 4 章「The Shocking Truth: C Arrays and Pointers Are NOT the Same!」與第 9 章「More about Arrays」是「陣列不是指標」的經典說明
  - **讀哪幾章**：第 4 章現在就能讀；第 3 章「Unscrambling Declarations in C」留到 Ch 8

### 部落格 / 技術文章

- **[Everything you need to know about pointers in C](https://boredzo.org/pointers/)** — Peter Hosey
  - **這篇說什麼**：jserv 指標篇「先羅列你已經知道的部分」推薦的入門文，用大量小例子講宣告、解參考、陣列、指標算術
  - **讀哪裡**：整篇，約半小時；可以當本章的另一個角度複習
- **[A Guide to Undefined Behavior in C and C++, Part 1](https://blog.regehr.org/archives/213)** — John Regehr（2010）
  - **這篇說什麼**：猶他大學教授、Csmith 作者解釋 UB 的本質，以及為什麼「看起來正常運作」不代表程式正確
  - **為什麼值得讀**：本章「崩潰博物館」中 #2、#4 的沉默錯誤，背後原理就是這篇；Part 2、3 留到 Ch 34

### 官方文件 / Spec

- **[ISO/IEC 9899 草案 N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：6.5.3.2（`&` 與 `*`）、6.5.6p8–9（指標算術與 one-past-the-end）、6.5.2.1p2（下標的定義）、6.3.2.1p3（陣列轉指標）、6.2.4p2（生命週期與不確定的指標值）
  - **前提知識**：本章；這幾段加起來不到兩頁，下一章會教你怎麼讀
- **[GDB 手冊：Examining Memory](https://sourceware.org/gdb/current/onlinedocs/gdb.html/Memory.html)**
  - **讀哪裡**：`x` 指令的完整格式與單位說明；`display` 與 `print` 的格式化另見同章的「Output Formats」

本章引用了一長串規格書條款，但還沒教你怎麼讀它。下一章打開 ISO/IEC 9899 本身：它怎麼組織、條款編號怎麼查、C89 到 C23 各改了什麼。

→ [Ch 4 讀規格書：ISO/IEC 9899 與 C89 到 C23 的演進](./04-reading-the-standard.md)
