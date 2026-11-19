# Ch 6 — C 的起源與演化：從 BCPL、B 到 PDP-11

> **目標**：能說出 C 那些「怪異」特徵——陣列在運算式中變成指標、宣告要由內往外讀、`&` 的優先順序低於 `==`、函式參數 `int a[]` 其實是指標、字串以 `'\0'` 結尾——各自來自哪條歷史路線、當時在取捨什麼；並能分辨網路上流傳的 C 歷史說法哪些與一手資料不符。

> **對應原文**：[你所不知道的 C 語言：起源與演化篇](https://hackmd.io/@sysprog/c-history) 的「直線敘事與它的缺口」「路線四：機器從 word 變成位元組」「匯流點：從 NB 到 C 的關鍵決定」「怪異之處各自來自哪條路線」「以實作優先的取捨」「流傳甚廣的誤解與一手資料」「Plan 9 的 C 編譯器」等節

> **環境**：程式範例以 gcc 11.4、clang 14（WSL Ubuntu 22.04 x86_64）執行；涉及 C23 的部分以 Docker `gcc:14`（GCC 14.4.0）執行。

> **本章結構說明**：這是歷史章，沒有「怎麼用才不會出錯」的操作面，所以模板中的「踩雷集錦」改為「流傳甚廣的誤解」——歷史章最常見的錯誤，是把不準確的傳說當成事實。本章的史實以 Dennis Ritchie 的〈The Development of the C Language〉（HOPL-II，1993）為主要一手資料，輔以 Thompson 的 B 手冊、Kernighan 的 B 教學、早期編譯器原始碼與兩位當事人的訪談；凡是推論而非一手記載的地方，會明確標出。

## 為什麼需要這個？

先看三個每個 C 程式設計者都會撞上的怪現象：

```c
#include <stdio.h>

#define READY 0x4

int main(void)
{
    unsigned status = 0x6;           /* 二進位 110：READY 位元是 1 */
    if (status & READY == READY)
        printf("A: ready\n");
    else
        printf("A: not ready\n");
    if ((status & READY) == READY)
        printf("B: ready\n");
    int x = 5;
    x =+ 1;                          /* 1976 年以前的 B/C：x += 1 */
    printf("x = %d\n", x);
    printf("010 = %d\n", 010);
    return 0;
}
```

```
$ gcc prec.c -o prec && ./prec
A: not ready
B: ready
x = 1
010 = 8
```

不開警告，gcc 一聲不吭，三行都和直覺不符：

1. `status & READY == READY` 被解析成 `status & (READY == READY)`，也就是 `6 & 1` = 0，因為 `&` 的優先順序**低於** `==`。
2. `x =+ 1` 是 `x = +1`，不是 `x += 1`。
3. `010` 是八進位的 8，不是十。

這些不是設計失誤的隨機堆積，每一個都有明確的歷史來源。clang 的警告訊息甚至直接洩漏了其中一段歷史：

```
$ clang -Wall prec.c -o precc
prec.c:8:16: warning: & has lower precedence than ==; == will be evaluated first [-Wparentheses]
    if (status & READY == READY)
               ^~~~~~~~~~~~~~~~
prec.c:15:7: warning: use of unary operator that may be intended as compound assignment (+=)
    x =+ 1;                          /* 1976 年以前的 B/C：x += 1 */
      ^~
```

為什麼 2020 年代的編譯器還要特別警告 `=+`？因為早期的 C 真的把 `=+` 當成複合指定運算子。懂了這些歷史，C 的規則就不再是一堆要死背的例外，而是一連串在特定機器、特定資源限制下做出的合理取捨。這也是 [Ch 7](./07-pointers-by-the-standard.md) 起讀指標規則時最好用的背景知識。

## 先建立直覺

很多文章把 C 的起源講成一條直線：

```
 CPL ──▶ BCPL ──▶ B ──▶ C        （常見的直線敘事）
```

這條線不算錯，但只交代了「語言血統」一條路線，而且 Ritchie 的論文根本沒有提到 CPL。實際上 C 的各項特徵來自至少五條路線，在 1971 至 1973 年間匯流：

```
  路線一：語言血統          路線二：資料模型          路線三：編譯技術
  ALGOL 60 / ALGOL 68       CPL → BCPL → B            TMG（McIlroy, 1969）
  （型別組合、union、cast） （只有 word 一種型別）     threaded code、單趟編譯
           │                        │                         │
           └────────────┬───────────┴─────────────┬───────────┘
                        ▼                         ▼
  路線四：機器                              路線五：Unix 的處境
  PDP-7（18 位元 word 定址）               退出 Multics，想用高階語言
     → PDP-11（位元組定址、浮點硬體）       寫作業系統；資源極少
                        │                         │
                        └──────────┬──────────────┘
                                   ▼
                    B ─(加 char、產生機器碼)─▶ NB ─(陣列規則、struct)─▶ C
                                                     （1971–1973）
                                   │
                                   ▼
             1973 年後：前置處理器、可攜性、lint、K&R、ANSI/ISO 標準、C++ 的回饋
```

本章就順著這五條路線走，最後在「匯流點」看它們如何決定陣列、指標、型別與宣告語法。

## 路線一：ALGOL 與程序式語言的家族

ALGOL（Algorithmic Language）60 由十三位代表於 1960 年 1 月在巴黎開會寫成，修訂版報告 1963 年刊於 CACM。它首次以 **BNF**（Backus Naur Form）這種形式記法定義語法，並有巢狀區塊、程序與 `if ... then ... else`。值得注意的是，**ALGOL 60 報告本文沒有定義輸入輸出**——這個傳統一路傳到 C：C 語言本身沒有 I/O 敘述，`printf` 只是一個程式庫函式。

Ritchie 把 BCPL、B、C 歸為以 Fortran 與 ALGOL 60 為代表的「傳統程序式家族」：偏向系統程式設計、規模小而容易描述、可由簡單的編譯器翻譯。它們「貼近機器」，是因為提供的抽象容易對應到真實電腦的資料型別與運算；但抽象層次又夠高，小心使用時可以在不同機器間移植。

ALGOL 68 則以另一種方式影響 C。Ritchie 說他從 ALGOL 68 取得的中心概念，是**以基本型別為基礎，組合出陣列、指標與函式的型別結構**；`union` 與 cast 稍後才產生影響。他也補了一句：C 的結果未必是 ALGOL 擁護者會認可的樣子。

## 路線二：CPL 與 BCPL 的 word 模型

CPL（Combined Programming Language）是 1960 年代 Cambridge 與倫敦大學合作、由 Christopher Strachey 主導的語言，屬 ALGOL 傳統中的野心之作，卻因為過於龐大複雜而終止。Martin Richards 取其簡單子集做成 BCPL（Basic CPL），1967 年初在 MIT 完整實作。Bell Labs 的人熟悉 BCPL，是因為 MIT 的 CTSS 系統也用於 Multics 的開發。

BCPL 與 B 的資料模型是 C 的出發點，四個特徵要記住：

- **只有一種型別：word（或稱 cell）**，一個固定長度的位元樣式。記憶體是 cell 的線性陣列，`+` 就是機器的整數加法；cell 代表整數、字元還是位址，取決於你對它做什麼運算。
- **指標是 cell 的索引**。`p` 是某個 cell 的位址，`p+1` 就是**下一個 cell**。B 的 `auto V[10]` 會配置一個名為 `V` 的 cell，另外留出連續 10 個 cell，再把第一個的索引放進 `V`；`V[i]` 就定義成 `*(V+i)`。
- **字串是字元向量**。BCPL 的字串第一個 byte 記錄長度；B 改用特殊字元 `*e` 結尾，理由包括長度欄位會限制字串長度、維護計數不如結尾字元方便。C 的 `'\0'` 結尾字串就是從這裡來的。
- **分開編譯**：BCPL 用 global vector（程式設計師手動替每個外部名稱指定數字偏移量）；後來的 B 與全部的 C 改用一般的連結器依名稱解析（[Ch 39](./39-static-linking.md)）。

Kernighan 的 B 教學有一個 Hello World，最能說明 word 模型：

```
main( ) {
    extrn a, b, c;
    putchar(a); putchar(b); putchar(c); putchar('!*n');
}
a 'hell';
b 'o, w';
c 'orld';
```

`'hell'` 是一個**字元常數**，把 4 個字元塞進 Honeywell 6070 的一個 36 位元 word（每字元 9 位元）。`a`、`b`、`c` 各是一個 word 大小的外部變數，`putchar` 會把整個 word 裡的字元依序印出。`*n` 是 B 的換行跳脫字元，C 改成 `\n`。

## 路線三：編譯技術——TMG、bootstrapping 與 threaded code

1969 年，Doug McIlroy 在 PDP-7 上的 Unix 實作了 TMG，一個寫 top-down、recursive-descent（遞迴下降）編譯器的工具。Ken Thompson 受到刺激，認為 Unix 需要一個系統程式語言。依 Ritchie 的記述，Thompson 先試 Fortran，很快放棄；他在 2019 年與 Kernighan 的訪談中補充：他先用 TMG 寫 Fortran 編譯器，結果約是目標大小的 10 倍，於是一路刪減，刪到能塞進 PDP-7 時，就叫它 B。

TMG 版的 B 能跑之後，Thompson 用 B 重寫 B（**bootstrapping，自舉**）：每加一個新功能，編譯器剛好還塞得進記憶體；每次用新功能改寫自己，編譯器又變小一點。記憶體壓力也左右了語法：BCPL 編譯器先把整個程式的剖析樹放在記憶體裡再輸出，B 的編譯器沒有這個餘裕，**必須邊讀邊輸出**（單趟編譯），語法因此重新設計，這個設計延續到 C——這也是為什麼 C 要求「先宣告後使用」。

PDP-7 上的 B 編譯器不產生機器指令，而是輸出 **threaded code**：

```
 B 原始碼：  x = a + b;
 
 編譯結果（一串位址）：            執行時期的小片段：
 ┌──────────────┐                  push_a:  把 a 推上堆疊；跳回派發器
 │ &push_a      │ ─────────────▶   push_b:  把 b 推上堆疊；跳回派發器
 │ &push_b      │                  add:     彈出兩個、相加、推回；跳回
 │ &add         │                  store_x: 彈出、存到 x；跳回
 │ &store_x     │
 └──────────────┘
        ▲
   派發器：取下一個位址 → 間接跳躍過去 → 片段做完再回來取下一個
```

（這是對 threaded code 技術的通用描述，用來說明概念，不是 B 實際的片段名稱。）

Ritchie 明說的利弊有兩點：**移植容易**（換機器只要重寫那些小片段），**速度遠比組合語言慢**，所以團隊不考慮用 B 改寫作業系統。從結構可以推知慢的原因：每個運算都多一次取位址與間接跳躍，中間值經由堆疊傳遞，編譯器沒機會替運算安排暫存器。這個「慢」是 B 必須演化成 C 的理由之一。threaded code 的概念至今仍活在許多直譯器裡，[Ch 31](./31-goto-and-control-flow.md) 的 computed goto 與 [Ch 38](./38-compiler-construction.md) 的直譯器會再遇到它。

## 路線四：機器從 word 變成位元組

這是理解 C 的指標與陣列**最關鍵的一段**。

Unix 起始於 PDP-7，一台只有 **8K 個 18 位元 word** 的機器，而且是 **word 定址**：每個位址指向一整個 word，位址 + 1 就是下一個 word。B 的單一 cell 型別正好等於硬體的 word，指標是 cell 索引也正好等於硬體位址，一切很自然。

1970 年，Bell Labs 取得 PDP-11（Ritchie 記得磁碟比處理器晚三個月才到）。PDP-11 是 16 位元、**位元組定址**：每個位址指向一個 byte，一個 16 位元 word 佔兩個位址。

```
 word 定址（PDP-7、B 的模型）          位元組定址（PDP-11）
 
 位址   內容（一個 word）              位址   內容（一個 byte）
 ┌───┬──────────────┐                 ┌───┬──────┐
 │ 0 │ 'he'         │                 │ 0 │ 'h'  │  ┐ word 0
 │ 1 │ 'll'         │                 │ 1 │ 'e'  │  ┘
 │ 2 │ 'o\0'        │                 │ 2 │ 'l'  │  ┐ word 1
 └───┴──────────────┘                 │ 3 │ 'l'  │  ┘
  p+1 = 下一個 word                    │ 4 │ 'o'  │
  無法直接指向 'e'                     └───┴──────┘
                                       p+1 = 下一個 byte
                                       可以直接指向任何字元
```

把 B 搬到 PDP-11，Ritchie 列出三個問題：

1. **字元處理笨拙**：B 的指標指向 word，沒辦法指向一個字元。B 的程式庫有 `char(s, i)` 與 `lchar(s, i, c)` 兩個函式，用來取出與存入字串的第 i 個字元，但每次都要先算出第幾個 word、再挑出半邊。
2. **浮點數放不下**：廠商承諾 PDP-11 會有浮點硬體。BCPL 的作法是定義特殊的浮點運算子，前提是一個 word 放得下一個浮點數，16 位元的 PDP-11 不成立。
3. **指標要換算**：B 的指標是 word 索引，但 PDP-11 硬體要的是 byte 位址，每次解參考都要在執行期把索引乘以 2。

用一段 C 模擬 B 在 PDP-11 上的處境，問題一和三就很具體：

```c
/* 用 C 模擬 B 在 PDP-11 上的處境：記憶體是 16 位元 word 的陣列，
 * 「指標」是 word 索引；字元得靠函式拆裝。 */
#include <stdint.h>
#include <stdio.h>

static uint16_t mem[16];                 /* 32 bytes 的 word 記憶體 */

/* B 程式庫 char(s, i)：取出字串 s 的第 i 個字元（每 word 放 2 個字元） */
static int b_char(int s, int i)
{
    uint16_t w = mem[s + i / 2];        /* 先算出第幾個 word */
    return (i % 2 == 0) ? (w & 0xff) : (w >> 8);   /* 再挑出半邊 */
}

/* B 程式庫 lchar(s, i, c)：把字元 c 存到第 i 個位置 */
static void b_lchar(int s, int i, int c)
{
    uint16_t *w = &mem[s + i / 2];
    if (i % 2 == 0)
        *w = (uint16_t) ((*w & 0xff00) | (c & 0xff));
    else
        *w = (uint16_t) ((*w & 0x00ff) | ((c & 0xff) << 8));
}

int main(void)
{
    int s = 3;                           /* B 的「指標」：word 索引 3 */
    const char *msg = "hello";
    for (int i = 0; msg[i]; i++)
        b_lchar(s, i, msg[i]);
    b_lchar(s, 5, 0);

    printf("B 指標 s = %d, s+1 = %d (差 1 個 word = 2 bytes)\n", s, s + 1);
    printf("硬體位元組位址 = %d, %d (每次參照都要乘 2)\n", s * 2, (s + 1) * 2);
    printf("char(s, 1) = '%c'\n", b_char(s, 1));
    printf("mem[3..5] = %04x %04x %04x\n", mem[3], mem[4], mem[5]);

    /* C 的作法：型別告訴編譯器每一步要走幾個 byte */
    char *cp = (char *) &mem[3];
    printf("C: cp[1] = '%c', cp+1 - cp = %td byte\n", cp[1], (cp + 1) - cp);
    return 0;
}
```

```
$ gcc -Wall -Wextra wordmem.c -o wordmem && ./wordmem
B 指標 s = 3, s+1 = 4 (差 1 個 word = 2 bytes)
硬體位元組位址 = 6, 8 (每次參照都要乘 2)
char(s, 1) = 'e'
mem[3..5] = 6568 6c6c 006f
C: cp[1] = 'e', cp+1 - cp = 1 byte
```

`mem[3] = 0x6568`：`'h'`（0x68）在低位元組、`'e'`（0x65）在高位元組，B 想拿第 1 個字元，要先 `i / 2` 找 word、再 `>> 8` 取高半邊。C 的解法是**讓指標帶型別**：`char *` 加 1 走 1 byte、`int *` 加 1 走 `sizeof(int)` byte，換算由編譯器在編譯時做完，硬體位址直接存在指標裡。這就是今天 C 指標算術「依型別縮放」的起源，也是 [Ch 3](./03-pointer-basics.md) 那條規則背後的原因。

除了語言本身，threaded code 的速度也限制了 B（路線三）。1971 年 Ritchie 替 B 加入 `char` 型別，並重寫編譯器**直接產生 PDP-11 機器指令**，稱這個稍加延伸的語言為 **NB**（New B）。他明言：從 B 到 C 的轉變，與一個能產生「足以和組合語言競爭的快速小型程式」的編譯器同時誕生。型別安全與介面檢查在當時並不重要，是後來才變得重要。

## 路線五：Multics 之後的 Unix

Multics 是 MIT、General Electric 與 Bell Labs 的合作計畫。到 1969 年，Bell Labs 的管理階層與研究人員都認為它的承諾來得太遲、太昂貴，於是退出。Thompson 想依自己的設計建立運算環境，移植 Multics 的若干概念（行程、樹狀檔案系統、以使用者程式實作的命令直譯器、簡單的文字檔表示）；他們不喜歡 Multics 使用的 PL/I，卻懷念 BCPL 這類高階語言的易寫易懂。原始的 Unix 以組合語言寫成，一開始甚至在 GE-635 上組譯，再用紙帶搬到 PDP-7 測試。

Kernighan 在 2026 年的訪談中描述了這個環境：AT&T 是受管制的公共壟斷，營收近乎有保證，能撥出一部分資助長期研究；研究人員沒有指定專案與期限，每年只交一頁成果摘要。這和 Ritchie 所述 Thompson 想「依自己的設計、用手邊任何手段」建立環境的處境相符。

Unix 的工具文化也是 C 得以通行的條件。yacc、lex、awk、grep 都出自 Bell Labs 的同一群人；Ritchie 提到 yacc 在 1973 年已由 Alan Snyder 改寫為 C，Steve Johnson 從加拿大回到 Bell Labs 時，發現自己帶去的 B 已在原處演化成 C。

## 匯流點：從 NB 到 C 的關鍵決定

NB 只提供 `int`、`char`、它們的陣列與指標，**陣列語意仍同 B**：宣告陣列時會建立一個 cell，存放指向首元素的位址。這在 Ritchie 加入結構（struct）時撞牆了。他舉早期 Unix 目錄項為例，希望結構的記憶體配置恰好對應從磁碟讀進來的位元：

```c
struct {
    int inumber;
    char name[14];
};
```

在 PDP-11 上，`int` 是 2 byte，整個結構正好 16 byte，和磁碟上一筆目錄項一樣大，讀進來就能直接用。但依 B 的語意，`name` 需要一個**存放陣列位址的指標 cell**：

```
 B/NB 的陣列語意                        C 的陣列語意
 
 struct（想要 16 bytes）               struct（16 bytes）
 ┌──────────┐                          ┌──────────┐
 │ inumber  │ 2 bytes                  │ inumber  │ 2 bytes
 ├──────────┤                          ├──────────┤
 │ name ────┼──┐ 指標 cell：藏在哪？   │ name[0]  │
 ├──────────┤  │                       │   ...    │ 14 bytes，陣列本體
 │ ??? 14   │◀─┘                       │ name[13] │ 就是 struct 的一部分
 │ bytes    │                          └──────────┘
 └──────────┘                          「name」不佔任何空間；在運算式中
 多出一個指標，和磁碟格式對不上；      用到它時，編譯器才算出 &name[0]
 結構內含陣列、陣列又含結構時，
 每一層的指標都要逐一初始化
```

Ritchie 的解法，用他自己的話說是「從無型別的 BCPL 到有型別的 C 的演化鏈中**關鍵的一躍**」：**不在儲存空間中實體化指標，改為在運算式提到陣列名稱時才產生指標**。這條規則至今仍是 C11 6.3.2.1 §3，例外包括 `sizeof`、`_Alignof`、一元 `&` 的運算元，以及用字串常值初始化字元陣列。實際驗證：

```c
#include <stdio.h>

struct direct {                 /* 早期 Unix (V6/V7) 的目錄項 */
    short inumber;              /* PDP-11 的 int 是 16 位元，這裡用 short 模擬 */
    char name[14];
};

static void f(int a[])          /* 「活化石」：其實是 int *a */
{
    printf("in f:   sizeof a = %zu\n", sizeof a);
}

int main(void)
{
    int arr[10];
    int *p = arr;               /* arr 在運算式中轉成 &arr[0] */
    printf("in main: sizeof arr = %zu, sizeof p = %zu\n", sizeof arr, sizeof p);
    printf("arr = %p, &arr[0] = %p, &arr = %p\n", (void *) arr, (void *) &arr[0], (void *) &arr);
    printf("arr + 1 = %p, &arr + 1 = %p\n", (void *) (arr + 1), (void *) (&arr + 1));
    f(arr);
    printf("sizeof(struct direct) = %zu\n", sizeof(struct direct));
    /* arr = p;  若取消註解：陣列名稱不能被指定 */
    return 0;
}
```

```
$ gcc -Wall -Wextra decay.c -o decay && ./decay
decay.c:10:47: warning: ‘sizeof’ on array function parameter ‘a’ will return size of ‘int *’ [-Wsizeof-array-argument]
in main: sizeof arr = 40, sizeof p = 8
arr = 0x7ffd408e0f70, &arr[0] = 0x7ffd408e0f70, &arr = 0x7ffd408e0f70
arr + 1 = 0x7ffd408e0f74, &arr + 1 = 0x7ffd408e0f98
in f:   sizeof a = 8
sizeof(struct direct) = 16
```

（位址每次執行不同。）逐行對照歷史：

- `sizeof arr = 40`：`sizeof` 是例外，陣列不轉換，所以是整個陣列的大小——證明 `arr` 沒有對應的指標 cell，它**就是**那 40 byte。
- `arr`、`&arr[0]`、`&arr` 印出同一個位址，但 `arr + 1` 走 4 byte、`&arr + 1` 走 40 byte：型別不同（`int *` 對上 `int (*)[10]`），縮放量就不同。
- `sizeof(struct direct) = 16`：陣列本體直接嵌在結構裡，和磁碟格式對得上，這正是 Ritchie 要的。
- 若取消 `arr = p;` 的註解，gcc 回報 `error: assignment to expression with array type`。Ritchie 說這個發明讓大部分既有的 B 程式照常運作，**只有少數對陣列名稱指定新值的程式需要修補**——因為在 B 裡，陣列名稱是一個真的 cell，可以被指定；在 C 裡它不是。

### 宣告模仿使用

第二個決定是型別語法。給定任何型別，都應該能描述由它組成的陣列、傳回它的函式、指向它的指標。對每一種組合，運算式裡本來就有「使用」它的方式（取下標、呼叫、解參考），所以 Ritchie 讓**宣告的語法模仿運算式的語法**：

```c
int i, *pi, **ppi;          /* *pi 是 int、**ppi 是 int */
int f(), *g(), (*h)();      /* f() 是 int、*g() 是 int、(*h)() 是 int */
int *api[10], (*pai)[10];   /* *api[i] 是 int、(*pai)[i] 是 int */
```

讀法是「這個運算式的結果是 `int`」：`*api[10]` 因為 `[]` 優先於 `*`，所以 `api` 是陣列、元素是指標；`(*pai)[10]` 用括號讓 `*` 先作用，所以 `pai` 是指標、指向陣列。這就是 C 宣告要「由內往外讀」的原因。Ritchie 轉述 Ravi Sethi 的觀察：如果當初解參考用後置運算子，許多巢狀宣告會簡單得多，但為時已晚。[Ch 8](./08-arrays-functions-pointers.md) 會從規格書的 derived declarator 角度完整解析這套語法。

### 活化石：函式參數 `int a[]`

Ritchie 公開的兩份最早期編譯器原始碼（年代在 1972 至 1973 年之間）留下了過渡期的痕跡：

- **`last1120c`**：完全不認得 `struct`。
- **`prestruct-c`**：已實作結構，但還沒有今日的複合宣告子，**指標仍寫成 `int ip[];`**。

這個寫法留存為現今函式參數宣告中的 `int a[]`——Ritchie 稱之為「活化石」。上面 `decay.c` 的 `f(int a[])` 裡 `sizeof a` 是 8，gcc 還特地警告 `-Wsizeof-array-argument`：參數 `a` 的型別早已被調整成 `int *`（C11 6.7.6.3 §7），寫成陣列只是保留舊拼法。

### 命名為 C，以及 Unix 的改寫

型別系統、語法與編譯器完成後，Ritchie 覺得 NB 不夠有辨識度，沿用單一字母，命名為 C。Thompson 在 2019 年的訪談中說，他用 C 重寫核心失敗了三次，「身為自負的人，我把責任推給語言」，每次 Ritchie 補強語言，直到加入結構才成功。Ritchie 的論文給出更精確的時序：Thompson 在 1972 年曾以**尚無結構**的早期 C 嘗試，放棄；C 在 1973 年初基本成形，**同年夏天**核心才改用 C 在 PDP-11 上重寫。

### `auto` 的半世紀

B 的函式內，名稱要用 `auto`（堆疊上的區域變數）或 `extrn`（外部名稱）宣告。C 加入型別後，`int i;` 這種只標型別的宣告在函式內自動是 automatic，`auto` 就變得多餘——但作為關鍵字留了下來。C23 又給了它第二個用途：型別推導。同一行程式碼在不同標準下的命運：

```c
#include <stdio.h>

int main(void)
{
    auto x = 1.5;
    printf("%d %g\n", (int) sizeof x, (double) x);
    return 0;
}
```

```
$ gcc -std=c89 -Wall auto.c -o a && ./a          # Docker gcc:14
auto.c:5:10: warning: type defaults to 'int' in declaration of 'x' [-Wimplicit-int]
4 1
$ gcc -std=c99 -Wall auto.c -o a
auto.c:5:10: error: type defaults to 'int' in declaration of 'x' [-Wimplicit-int]
$ gcc -std=c23 auto.c -o a && ./a
8 1.5
```

| 標準 | `auto x = 1.5;` 的意思 | 依據 |
|---|---|---|
| C89 | `auto int x = 1.5;`（implicit int），值截斷為 1 | 儲存類別指定詞 + implicit int |
| C99–C17 | 編譯錯誤 | C99 移除 implicit int（C11 6.7.2 §2：每個宣告至少要有一個型別指定詞） |
| C23 | 推導為 `double` | N3220 6.7.10；此處 `auto` 仍是儲存類別指定詞，推導宣告必須帶有它 |

一個 1969 年的關鍵字，在 2024 年的標準裡換上新意義——這正是 [Ch 4](./04-reading-the-standard.md) 說的「既有程式碼比既有編譯器重要」：委員會選擇重用一個幾乎沒人寫的保留字，而不是新增一個可能撞到使用者變數名的關鍵字。

## 匯流之後：運算子、前置處理器與可攜性

### `&&`、`||` 與 `&` 的優先順序

BCPL 與 B 的 `&` 與 `|` 在條件式中是短路求值的邏輯運算，在其他地方是位元運算，兩種語意重疊、難以說明。Alan Snyder 建議後，Ritchie 引入 `&&` 與 `||`。但**為了降低從 B 轉換的痛苦，`&` 與 `==` 的相對優先順序維持不變**——在 B 裡，`if (a & mask == b)` 中的 `&` 是邏輯 AND，`==` 先算是合理的。Ritchie 事後承認應該調整：今天測試遮罩必須寫成 `if ((a & mask) == b)`，內層括號必要卻容易忘。本章開頭的 `prec.c` 就是這個決定留下的代價，gcc 要開 `-Wall`（`-Wparentheses`）才會提醒：

```
$ gcc -Wall -Wextra prec.c -o prec
prec.c:8:16: warning: suggest parentheses around comparison in operand of ‘&’ [-Wparentheses]
    8 |     if (status & READY == READY)
      |                ^
```

### `=+` 變成 `+=`

廣義指定運算子的記號來自 ALGOL 68，經 McIlroy 帶進 B，當時拼成 `=+`、`=-`。Ritchie 稱這是個錯誤，是 B 的詞法分析器「誘人地容易處理」這種寫法所導致，1976 年改成今天的 `+=`。常被提到的副作用是 `x=-1` 的歧義（`x =- 1` 還是 `x = -1`？），不過這個理由不在 Ritchie 的論文裡，是後人的說明。半世紀後，clang 仍為 `x =+ 1` 發出 "may be intended as compound assignment (+=)" 的警告。

### 八進位常數

Thompson 的 B 手冊規定以 `0` 開頭的數字是八進位；Kernighan 的 B 教學說明理由：B 常用於系統程式設計與位元操作，八進位很重要（補充觀察：PDP-7 的 18 位元 word 正好是 6 個八進位數字，這點不是手冊給的理由）。C 沿用至今，`010 == 8`；二進位常數 `0b` 要到 C23 才加入。

### 前置處理器：後來的外掛

1972 至 1973 年最重要的變動是前置處理器，部分出自 Snyder 的敦促，也因為 BCPL 與 PL/I 的檔案引入機制很有用。最初只有 `#include` 與無參數的 `#define`，之後 Mike Lesk 與 John Reiser 加入帶引數的巨集與條件編譯。有好幾年，它被視為語言的**選用附件**，必須在原始檔開頭放特殊標記才會啟用——這解釋了它的語法與語言其他部分整合得很差（[Ch 27](./27-preprocessor-basics.md)）。

### 可攜性：unsigned、cast、lint

1977 至 1979 年，把 Unix 移植到 Interdata 8/32 的經驗催生另一波語言變動。當時的 C 仍帶有無型別時代的痕跡：指標與整數幾乎不分，整數與指標互相指定被容忍。因此加入 `unsigned`（讓無號運算不必借用指標）、借用 ALGOL 68 的名稱發明 **cast**；Steve Johnson 改造可重新定位目標的編譯器 pcc，做出 **`lint`**，標出合法但可疑的構造，並檢查分開編譯的檔案之間的介面是否一致。

為什麼需要 lint？因為早期 C 根本不檢查函式呼叫的引數。呼叫一個沒宣告過的函式，編譯器會假設它回傳 `int`：

```c
/* imp_main.c */
#include <stdio.h>

int main(void)
{
    double r = half(3.0);       /* 呼叫前沒有宣告 */
    printf("half(3.0) = %f\n", r);
    return 0;
}
```

```c
/* half.c */
double half(double x) { return x / 2; }
```

```
$ gcc -std=c89 imp_main.c half.c -o imp && ./imp
half(3.0) = 0.000000
```

C89 模式下**沒有任何警告**，結果是錯的（本機印出 0，實際值取決於暫存器內容）：`main` 以為 `half` 回傳 `int`，去 `eax` 拿結果再轉成 `double`；`half` 卻把 `double` 放在 `xmm0`。兩個檔案分開編譯，編譯器看不到彼此。C89 3.3.2.2 規定未宣告的函式視同 `extern int identifier();`，C99 移除了這條：

```
$ gcc -std=c99 imp_main.c half.c -o imp2 && ./imp2    # gcc 11：只是警告
imp_main.c:5:16: warning: implicit declaration of function ‘half’ [-Wimplicit-function-declaration]
half(3.0) = 0.000000
$ gcc -std=c99 imp_main.c half.c -o imp               # Docker gcc:14：錯誤
imp_main.c:5:16: error: implicit declaration of function 'half' [-Wimplicit-function-declaration]
```

gcc 11 在 C99 模式下仍只給警告並產生錯誤的程式，GCC 14 起才預設把它當成錯誤。從 1973 年的 `last1120c`（它已把未宣告、後接 `(` 的識別字標為 `extern`）到 2024 年的 GCC 14，這個「無型別的出身」花了五十年才在預設設定中被關掉。

### 從 K&R 到 ANSI/ISO

K&R 第一版（1978）描述了大部分型別規則，Kernighan 寫說明文字，Ritchie 寫附錄的參考手冊；在標準出現前，它就是語言的定義。到 1982 年，它已不能描述使用中的語言（缺 `void`、`enum`），在 McIlroy 的促請下，ANSI 於 1983 年成立 X3J11 委員會，報告 1989 年底完成，隨後被 ISO 採為 ISO/IEC 9899:1990。Ritchie 認為委員會唯一真正重要的語言變更，是**把參數型別納入函式的型別簽章**（函式原型）——正是它終結了上面 `half` 那種錯誤。

## C with Classes 與 C 的標準化

C++ 源於同一個 Bell Labs 研究中心。Bjarne Stroustrup 1979 年在 Murray Hill 開始做 C with Classes，1983 年改名 C++。影響並非單向，C 標準有幾項特徵來自 C++：

| 項目 | C++ 一側（Stroustrup，〈A History of C++〉） | C 標準一側 |
|---|---|---|
| 函式原型 | C with Classes 第一個實作就有靜態型別檢查，語法與規則「後來被 ANSI C 採用」 | Ritchie：語法借自 C++；C99 Rationale：ALGOL 系語言早有先例，具體形式大體基於 C++ |
| `const` | 最初提出 readonly/writeonly，與 Ritchie 討論後演變；Bell Labs 內部標準小組決議改名 `const` 引入 C，但當時沒有執行 | C99 Rationale：語法與語意改自 C++；`volatile` 與 `restrict` 則是委員會的發明 |
| `inline` | 為了避免函式呼叫成本讓人把資料成員設成公開 | C99 Rationale：C99 的 `inline` 改自 C++ |

但範圍有限。C99 Rationale 寫道：「委員會樂於讓 C++ 成為龐大而野心勃勃的語言……但無意讓 C 變成 C++」；Stroustrup 的原則則是 "As close to C, but no closer"。另外，`//` 註解常被說成 C++ 的發明，其實 **BCPL 就用 `//`**，B 與 C 改用 PL/I 風格的 `/* */`，Ritchie 說 C++ 是「重新啟用」BCPL 的慣例，C99 再把它收回 C。

## 怪異之處各自來自哪條路線

Ritchie 在論文的檢討一節說，C 最有特色的兩個概念——陣列與指標的關係、宣告語法模仿運算式——也最常受批評。把本章出現過的怪異特徵整理成一張表：

| 特徵 | 來源路線 | 當時的考量 | 本課哪裡再談 |
|---|---|---|---|
| 陣列名稱在運算式中轉成指標 | 資料模型（路線二）遇上 struct | 結構內沒地方放指標 cell；大部分 B 程式照常運作 | [Ch 8](./08-arrays-functions-pointers.md) |
| 宣告模仿運算式，由內往外讀 | ALGOL 68 的型別組合 + B 的前置 `*` | 宣告與使用對稱；若用後置 `*` 會較簡單，但為時已晚 | [Ch 8](./08-arrays-functions-pointers.md) |
| 參數 `int a[]` 實為指標 | NB 與早期 C 宣告指標的方式 | 相容舊程式；「活化石」 | [Ch 8](./08-arrays-functions-pointers.md) |
| 指標算術依型別縮放 | PDP-11 位元組定址（路線四） | 換算移到編譯期，指標直接存硬體位址 | [Ch 3](./03-pointer-basics.md) |
| 呼叫未宣告函式視為回傳 `int` | 無型別的出身 | 單趟編譯、不做跨檔案檢查；C99 移除 | [Ch 9](./09-void-pointer-and-callbacks.md) |
| `&` 優先順序低於 `==` | B 的語法演變 | 減輕從 B 轉換的痛苦 | [Ch 23](./23-bitwise-basics.md) |
| `=` 指定、`/* */` 註解 | BCPL→B 的語法變動、PL/I | `=` 取代 BCPL 的 `:=`，Ritchie 稱至今仍有爭議 | — |
| `+=`（原為 `=+`） | ALGOL 68 記號，經 McIlroy 帶入 B | 原拼法因詞法分析器好處理，1976 年修正 | — |
| `'\0'` 結尾字串 | B 的 `*e` 結尾字元 | 字串完全落在陣列的一般規則內；代價是要搜尋結尾、儲存管理由使用者負責 | [Ch 10](./10-strings-and-null.md) |
| 語言本身沒有 I/O | ALGOL 60、BCPL、B 共通 | 交給程式庫 | [Ch 35](./35-stream-io-and-signals.md) |
| 前置處理器像外掛 | Snyder 的建議、BCPL 與 PL/I 的檔案引入 | 原本是選用附件 | [Ch 27](./27-preprocessor-basics.md) |
| `break` 兼用於 `switch` | BCPL→B 的分歧演化 | 他們學到的 BCPL 版本還沒有 `endcase`，並非刻意設計 | [Ch 31](./31-goto-and-control-flow.md) |
| `++` 與 `--` | PDP-7 | Ritchie：PDP-7 的自動遞增記憶體位置或許給了靈感；較強的動機大概是 `++x` 的翻譯結果比 `x=x+1` 小 | — |
| 沒有執行期檢查 | 機器與 Unix 的處境（路線四、五） | Kernighan：當時記憶體很小，檢查要佔程式碼空間；目標是寫作業系統 | [Ch 34](./34-undefined-behavior.md) |

Ritchie 對這些特徵的評價並不一致：他認為宣告語法仍是「有用的統一原則」，陣列的處理則在實務上較可疑，但也有真正的優點。

## 以實作優先的取捨：New Jersey 與 MIT 風格

上表的共同取向是：**先讓編譯器與系統在手邊的機器上跑起來，其餘從簡**。Richard P. Gabriel 為這種取向取了名字。他經營 Lisp 公司 Lucid 時，被問到為什麼人們認為 C 與 Unix 勝過 Lisp，半開玩笑地答「越糟越好」（worse is better）；1990 年的演講稿〈Lisp: Good News, Bad News, How to Win Big〉中論述這個觀點的段落後來被廣為傳閱。他以四項準則對比兩種風格：

| 準則 | MIT 風格（"the right thing"） | New Jersey 風格（"worse is better"） |
|---|---|---|
| 簡單性 | 實作與介面都要簡單，**介面**簡單比實作簡單重要 | 實作與介面都要簡單，**實作**簡單比介面簡單重要，且是最重要的考量 |
| 正確性 | 所有可觀察的面向都必須正確 | 必須正確，但簡單略勝於正確 |
| 一致性 | 不容許不一致 | 可以為簡單犧牲；寧可捨棄罕見情況也不增加實作複雜度 |
| 完整性 | 涵蓋所有合理預期的情況 | 可為其他性質讓步，威脅實作簡單時必須犧牲 |

Gabriel 的經典例子是**被 signal 中斷的系統呼叫**：MIT 風格的系統會還原使用者程式的狀態、讓它自動重新進入系統呼叫；Unix 讓系統呼叫直接結束、回傳錯誤碼（`EINTR`），由使用者程式自己檢查、重試。實作簡單了，代價移到每一個呼叫者身上——今天 Linux 程式碼中處處可見的 `if (errno == EINTR) continue;`，就是這個取捨的後果（[Ch 35](./35-stream-io-and-signals.md)）。C 標準的 `<signal.h>` 也延續同樣的風格：`signal` 的介面小到可以背下來，C11 7.14 只定義六個 signal，handler 能做的事、多執行緒下的行為幾乎全部是 UB 或實作定義。

C 的特徵可以逐項對上這套準則：單趟編譯與不允許巢狀函式（簡單性）、早期編譯器不檢查整數與指標互相指定（正確性讓位）、`int a[]` 與 `&` 的優先順序為相容而保留（一致性讓位）、沒有字串型別與 I/O（完整性讓位）。Gabriel 另有一個演化論證：約一半的電腦比中位數更小更慢，Unix 與 C 在這些機器上照常運作，實作簡單讓它們容易移植，於是「像病毒一樣」擴散，之後再逐步改進。

但有三點要澄清，否則會把事後的標籤當成設計宣言：

1. **Ritchie 從沒用過這組詞彙**。他給的理由是資源：實作 Fortran、PL/I 或 ALGOL 68 這類主流語言的計畫「對我們的資源而言大得無望」；他稱 C 的取向「節制而務實」（parsimonious, pragmatic），是「用來建造更大工具的工具」。
2. **Gabriel 自己的立場搖擺不定**。他後來以筆名寫〈Worse Is Better Is Worse〉反駁自己，又寫〈Is Worse Really Better?〉回應，2000 年還先後寫下反對與支持兩份意見書，說仍無法決定。
3. **取捨的前提會過時**。Kernighan 說的記憶體限制早已不存在，原本省略的檢查以工具的形式回來：sanitizer、`_FORTIFY_SOURCE`、Fil-C 這類記憶體安全實作。這恰好符合 Gabriel 的演化論證：先擴散，再改進。[Ch 5](./05-gdb-in-practice.md) 用過的 ASan 就是其中之一。

## 為什麼 C 能取代組合語言

Ritchie 的總結（[Ch 4](./04-reading-the-standard.md) 引用過全文）是：C 彆扭、有缺陷，卻異常成功；它滿足了對系統實作語言的需求——效率足以取代組合語言，又夠抽象、夠流暢，能在多樣環境中描述演算法。

Kernighan 在 2026 年的訪談中給出三點補充：

- **效率與表達力的平衡**：他回憶第一部跑 C 的機器約有 24K byte 記憶體、磁碟約 0.5 MB；C 簡單到看得出它如何翻譯成 PDP-11 指令，編譯器不難寫，卻足以有效表達要表達的事。
- **受限的資源決定刪去什麼**：沒有餘裕做執行期檢查、沒有複雜的執行期支援。
- **Unix 與 C 互相支撐**：Unix 比當時的商用作業系統好用，人們用 C 寫工具，又讓更多人用 Unix。

他也提醒組合語言被取代有更早的先例：John Backus 的團隊在 1950 年代中期開發 Fortran 時，擔心程式設計師不接受編譯器產生的程式不如手寫組合語言，所以 Fortran 刻意著重翻譯的效率而非優雅。C 走的是同一條路。

## 流傳甚廣的誤解

（本節取代一般章節的「踩雷集錦」，原因見章首說明。）

| 流傳的說法 | 一手資料怎麼說 |
|---|---|
| C 是從 CPL 或 BCPL 刪減而成 | Thompson 把 BCPL 壓縮成 B（1969–1970），Ritchie 替 B **加入型別**才成為 C（1971–1973）。從 B 到 C 是增加，不是繼續刪減 |
| Unix 一開始就用 C 寫成，在 PDP-11 上誕生 | 1969 年的 Unix 在 PDP-7 上以組合語言寫成；Bell Labs 1970 年才取得 PDP-11；核心 1973 年夏天才改用 C 重寫 |
| `++` 與 `--` 是為了 PDP-11 的自動遞增定址模式 | B 發展時 PDP-11 還不存在，Ritchie 說這「在歷史上不可能」 |
| C 是「高階組合語言」 | Ritchie：抽象貼近機器但夠高階，小心使用時可移植；「花俏的組合語言」是 Gabriel 轉述別人的說法，不是設計者的定位 |
| K&R C 就是 ANSI C | K&R 第一版到 1982 年已不能描述使用中的語言，正是它催生 X3J11 |
| ANSI C 與 ISO C 是兩種語言 | X3J11 的報告被 ISO 採為 ISO/IEC 9899:1990；之後各版都是 9899 的版次 |
| ANSI/ISO 把 C 改得面目全非 | C99 Rationale：C89 絕大部分與 K&R 第一版附錄 A 相同；Ritchie：核心語言「幾乎毫髮無傷」，唯一重要的變更是函式原型 |
| 函式原型是委員會憑空發明的 | 語法借自 C++，ALGOL 系語言早有先例 |
| C++ 是 C 的超集 | Stroustrup：C++ 不是 ANSI C 的嚴格超集。[Ch 4](./04-reading-the-standard.md) 的 `sizeof('a')` 在兩者中就不同 |
| `//` 註解是 C++ 發明的 | BCPL 已用 `//`；C++ 是重新啟用，C99 再收回 C |
| Plan 9 的 C 編譯器就是標準 ANSI C | Thompson：「帶有若干限制與擴充的 ANSI C」，沒有 `#if`；要嚴格 ANSI C 須用 `pcc` |

一個讀歷史的通則：**二手報導與漫畫常把時序與人物弄混**。原文指出，2020 年 Ars Technica 的長文與 2026 年的 F/OSS Comics 漫畫在 PDP-11 取得年代、`++` 的動機、誰用 B 嘗試重寫 Unix 等處都與 Ritchie 的論文不同。遇到衝突，以當事人的一手文件為準，這和 [Ch 4](./04-reading-the-standard.md) 說的「以條文為準、不以編譯器輸出為準」是同一種功夫。

## Plan 9：Bell Labs 自己的另一條路

Ritchie 描述的 C 隨標準化繼續演變；Bell Labs 後來的 Plan 9 作業系統則選了另一條路。Plan 9 的 C 編譯器是全新的程式，設計文件的作者是 Ken Thompson，他說它實作「帶有若干限制與擴充的 ANSI C」；Rob Pike 的使用手冊說它接受「ANSI C 的方言」，要嚴格 ANSI C 與 POSIX 時另用 `pcc`，並稱後者「說明委員會設計的環境有多笨拙」。

| 項目 | Plan 9 C | ISO C |
|---|---|---|
| 函式宣告 | 必須用原型，舊式宣告要加不鼓勵的 `-B` | C89 接受舊式，C23 才移除 |
| 前置處理器 | 整合進編譯器，**不支援 `#if` 與 `##`**；Thompson 認為 `#if` 不必要且常被濫用 | 標準的一部分（C11 6.10.1、6.10.3.3） |
| 標頭檔 | 標頭不引入其他標頭、不設重複引入保護，原始檔自行排序；每個程式以 `#include <u.h>` 起頭 | 慣用 `#ifndef` 保護 |
| `main` | 不回傳整數，以 `exits(string)` 結束 | hosted 環境回傳 `int` |
| `extern register` | 配置全域暫存器，Plan 9 核心用它保存「目前行程」與「每部機器的資料」 | 無此用法 |

Plan 9 的若干擴充後來以加上等號的形式進了 C99：structure display 對應 compound literal、`['%'] percent` 與 `.y 100` 對應 designated initializer（C99 寫成 `['%'] = ...`、`.y = 100`）。匿名結構成員也影響了 C11，但 Plan 9 的版本更寬鬆，GCC 用 `-fplan9-extensions` 提供相近的擴充：

```c
#include <stdio.h>

typedef struct Lock { int held; } Lock;

typedef struct Node {
    Lock;                       /* 匿名成員：Plan 9 風格，帶 typedef 名稱 */
    int key;
} Node;

static void lock(Lock *l) { l->held = 1; }

int main(void)
{
    Node n = { .key = 7 };
    lock(&n);                   /* Node * 自動轉成指向內嵌 Lock 的指標 */
    printf("held = %d, n.Lock.held = %d, key = %d\n", n.held, n.Lock.held, n.key);
    return 0;
}
```

```
$ gcc -std=c11 -Wall plan9.c -o p9 2>&1 | grep -E "error|warning"
plan9.c:6:9: warning: declaration does not declare anything
plan9.c:15:10: warning: passing argument 1 of ‘lock’ from incompatible pointer type [-Wincompatible-pointer-types]
plan9.c:16:56: error: ‘Node’ has no member named ‘held’
plan9.c:16:64: error: ‘Node’ has no member named ‘Lock’
$ gcc -std=gnu11 -Wall -fplan9-extensions plan9.c -o p9 && ./p9
held = 1, n.Lock.held = 1, key = 7
```

標準 C11 的匿名結構要求「沒有 tag」（6.7.2.1 §13），以 typedef 名稱宣告的 `Lock;` 不算，所以被當成什麼都沒宣告。Plan 9 的版本則同時給了「內嵌」（`n.held`）、「具名存取」（`n.Lock`）和「指標自動轉換」（`lock(&n)`）三件事——這是一種 C 語言層級的繼承，[Ch 29](./29-oop-in-c.md) 會看到 Linux 核心用 `container_of` 達成類似效果。

### Plan 9 到 Go

Plan 9 的部分開發者後來到 Google 設計 Go。Robert Griesemer、Rob Pike 與 Ken Thompson 在 2007 年 9 月於白板上草擬新語言的目標，Go 在 2009 年 11 月開源。Thompson 一個人先後參與了 B、Unix、Plan 9 C 編譯器與 Go。傳承有三層：

- **工具鏈**：Go 早期的 gc 工具鏈衍生自 Plan 9 的編譯器工具鏈，`6g`、`8g` 這類名稱沿用 Plan 9 以數字代表架構的命名（6 是 AMD64、8 是 386）；Go 1.5 起編譯器改以 Go 寫成。
- **依賴管理**：Pike 在〈Go at Google〉說明，Plan 9 禁止標頭檔再 `#include`，每個標頭只讀一次，編譯快得多；Go 的 import 延續這個想法。
- **設計取向**：組合而非型別階層；語法像 C，讓 C 家族的程式設計師容易上手。

## 對比與取捨：同一時期的其他路線

C 沒有取代同時代的語言，也沒有囊括它們的完整設計：

| 語言 | 它的取捨 | C 取了什麼、沒取什麼 |
|---|---|---|
| ALGOL 68 | 完整而嚴謹的型別系統，flexible array 需要複雜的編譯器支援 | 取型別組合、union、cast；不取 flexible array |
| CPL | 野心太大而終止 | 經 BCPL、B 取其簡單子集 |
| PL/I | Multics 的實作語言，Ritchie 稱不合口味 | 取註解記號、檔案引入、結構指標（不綁型別，後來被視為缺陷） |
| Pascal | 原版只有固定大小的陣列與字串，Kernighan 1981 年批評它「有所局限」 | C 以統一的指標機制處理陣列與字串 |
| Fortran | 刻意放棄優雅，著重翻譯效率 | 取以關鍵字起頭的宣告寫法；同樣以效率說服使用者 |
| Lisp | Gabriel 眼中 MIT 風格的代表 | 另一種優先序：介面簡單、正確、一致、完整 |

Ritchie 的一句話總結了這張表：「這些語言都影響我們的工作，但自己動手比較有趣。」

## 進階：再往深一層

- **讀最早的 C 編譯器**：Ritchie 的〈Primeval C〉頁面公開了 `last1120c` 與 `prestruct-c` 的原始碼。打開 `c00.c`，你會看到 `extrn` 已改拼 `extern`、宣告還有不帶型別的 `auto i, hl;`、指定運算子還是 `=+`——C 正在從 B 長出來的現場。Warren Toomey 曾在 PDP-11 第一、二版 Unix 的模擬器上讓 `last1120c` 編譯它自己。
- **跑 B**：[pdp7-unix](https://github.com/DoctorWkt/pdp7-unix) 專案重建了 PDP-7 版 Unix 與 B 編譯器，可以在模擬器上親手體驗 word 定址的 `p+1`。
- **重現 threaded code**：用 C 寫一個 threaded code 直譯器（用 GCC 的 labels-as-values 擴充），和 switch 式直譯器比較效能，銜接 [Ch 31](./31-goto-and-control-flow.md) 與 [Ch 38](./38-compiler-construction.md)。
- **面試題**：「為什麼 C 的陣列不能直接指定、不能當函式回傳值？」——好的答案會從 NB 的指標 cell 講起：陣列名稱不是一個存著位址的變數，而是在運算式中才產生的值，所以沒有東西可以被指定。

## 動手練習

1. 不看本章，在紙上畫出 B 與 C 對 `struct { int inumber; char name[14]; }` 的記憶體配置差異，並說明為什麼 B 的語意讓 `name` 無處安放。畫完後用 `sizeof` 與 `offsetof` 驗證 C 的配置。
2. 修改 `wordmem.c`：把 word 改成 PDP-7 的 18 位元（用 `uint32_t` 存、只用低 18 位元，每 word 放兩個 9 位元字元），重寫 `b_char` 與 `b_lchar`。
3. 收集三個 `&` 與 `==` 優先順序造成的錯誤寫法（可以搜尋開源專案的 commit 紀錄中 "precedence" 或 "parentheses"），用 `gcc -Wall` 與 `clang -Wall` 各編一次，比較兩者的警告訊息。
4. 用 `cdecl`（`sudo apt install cdecl`）解釋 `int *(*(*x)[5])(void)`，再依「宣告模仿使用」的原則，寫出要對 `x` 做哪些運算才能得到一個 `int`。

## 本章重點整理

- C 的特徵來自五條路線的匯流：ALGOL 的型別組合、BCPL/B 的 word 資料模型、TMG 與單趟編譯的技術限制、PDP-7 到 PDP-11 的定址轉變、Unix 的資源處境。
- PDP-11 的位元組定址是關鍵：它讓 B 的「指標是 word 索引」失效，迫使 C 引入 `char` 型別與依型別縮放的指標。
- 加入 struct 時，陣列無法再帶一個指標 cell，Ritchie 改成「運算式中才把陣列轉成指標」，這條規則就是今天的 array decay。
- `&` 的優先順序、`int a[]`、隱式函式宣告、`'\0'` 結尾字串，都是為了相容既有 B 程式或節省資源而保留的取捨；後世以 lint、警告、sanitizer 等工具補回省略的檢查。
- 讀歷史要以一手資料為準：C 不是從 BCPL 刪減而來，Unix 不是一開始就用 C 寫，`++` 也不是為了 PDP-11。

## 自我檢核

- [ ] 不看筆記，能用 word 定址與位元組定址的差異，解釋為什麼 B 不適合 PDP-11、C 為什麼要讓指標帶型別嗎？
- [ ] 有人說「C 的陣列就是指標」，你能用 NB 到 C 的那一躍，以及 `sizeof arr` 與 `&arr + 1` 的實驗反駁他嗎？
- [ ] 面試官問「為什麼 `if (x & MASK == MASK)` 是錯的，而 C 為什麼要設計成這樣？」，你能同時回答語法層面與歷史層面嗎？
- [ ] 能說出 New Jersey 風格的四項準則，並舉一個 C 或 Unix 的具體例子嗎？也能說出為什麼這個標籤不該被當成 Ritchie 的設計宣言嗎？
- [ ] 讀到一篇講 C 歷史的文章，你會檢查哪些容易出錯的細節？

## 延伸閱讀

### 論文

- **[The Development of the C Language](https://www.nokia.com/bell-labs/about/dennis-m-ritchie/chist.html)** — Dennis M. Ritchie，HOPL-II（1993）
  - **核心貢獻**：C 的設計者親述從 BCPL、B、NB 到 C 的每個決定，以及他自己對 C 的批評
  - **讀哪裡**：全文約 20 頁，建議通讀；最重要的是 "The Problems of B"、"Embryonic C" 與 "Critique" 三節，本章的匯流點與怪異特徵表即出自這裡
  - **和本章的關聯**：本章的主要一手資料；讀完你會發現本章只轉述了一半的細節
- **[A History of C++: 1979–1991](https://www.stroustrup.com/hopl2.pdf)** — Bjarne Stroustrup，HOPL-II（1993）
  - **核心貢獻**：C++ 的設計歷史，與 Ritchie 的論文同場發表
  - **讀哪裡**：第 2 節 C with Classes（函式原型、`const`、`inline` 的來歷），其餘可跳過
  - **和本章的關聯**：「C with Classes 與 C 的標準化」一節的 C++ 一側出處

### 部落格 / 技術文章

- **[Worse Is Better](https://www.dreamsongs.com/WorseIsBetter.html)** — Richard P. Gabriel
  - **這篇說什麼**：「越糟越好」一詞的來龍去脈，並連結到原始演講稿與他後來的反駁文章
  - **讀哪裡**：本頁的回顧，再點進〈Lisp: Good News, Bad News, How to Win Big〉的 "The Rise of Worse is Better" 一節
  - **為什麼值得讀**：軟體設計哲學最常被引用的文章之一；讀原文能看到作者自己的保留與矛盾，而不只是二手轉述的口號
- **[Primeval C: two very early compilers](https://www.nokia.com/bell-labs/about/dennis-m-ritchie/primevalC.html)** — Dennis M. Ritchie
  - **這篇說什麼**：`last1120c` 與 `prestruct-c` 的介紹與原始碼下載
  - **讀哪裡**：頁面說明，然後打開 `c00.c` 的前 100 行
  - **為什麼值得讀**：唯一能親眼看到「B 正在變成 C」的一手材料；前提是能讀懂基本的 C 與一點 B 的語法

### 官方文件 / Spec

- **[Users' Reference to B](https://www.nokia.com/bell-labs/about/dennis-m-ritchie/kbman.html)** — Ken Thompson（1972）與 **[A Tutorial Introduction to the Language B](https://www.nokia.com/bell-labs/about/dennis-m-ritchie/btut.html)** — Brian Kernighan
  - **讀哪裡**：Thompson 手冊的 4.0 Expression Evaluation 與 6.0 Declarations、7.2 Vector Definitions；Kernighan 教學的前幾節與 External Variables
  - **注意**：B 的 `*` 跳脫字元、`=+` 運算子都和 C 不同，對照著讀最能看出 C 改了什麼
- **[How to Use the Plan 9 C Compiler](https://doc.cat-v.org/plan_9/4th_edition/papers/comp)** — Rob Pike
  - **讀哪裡**：Introduction、Header files、Extensions 三節
  - **注意**：搭配本章 Plan 9 一節的表格；GCC 對應選項見 [Unnamed Fields](https://gcc.gnu.org/onlinedocs/gcc/Unnamed-Fields.html)

### 書籍

- **《The C Programming Language》（2nd ed.）** — Kernighan & Ritchie（Prentice Hall，1988）
  - **這本書的定位**：語言設計者寫的原典；第一版（1978）就是 ANSI 之前事實上的語言定義
  - **讀哪幾章**：第 5 章（指標與陣列）讀起來會和本章的匯流點一節互相印證；附錄 C 列出第二版相對第一版的改動
- **《UNIX: A History and a Memoir》** — Brian Kernighan（Kindle Direct Publishing，2019）
  - **這本書的定位**：當事人寫的 Bell Labs 與 Unix 歷史，篇幅輕鬆
  - **讀哪幾章**：講 Unix 誕生與 C 的章節，補充本章路線五的人與環境

歷史告訴我們陣列、指標與型別為什麼長成這樣；接下來要回到規格書，把這些概念的精確定義一條一條讀清楚。

→ [Ch 7 規格書裡的指標：object、type、incomplete type 與 lvalue](./07-pointers-by-the-standard.md)
