# Ch 2 — 型別與物件：整數型別、sizeof、陣列與 struct

> **目標**：建立「型別 = 一段記憶體的解讀方式」的心智模型；搞清楚 C 的整數型別到底保證多大、`<stdint.h>` 解決什麼問題、有號與無號混用時發生什麼事、`char` 為什麼可能是無號的，並能親手印出陣列與 struct 在記憶體裡的位元組排列。

> **對應原文**：[開發工具和規格標準篇](https://hackmd.io/@sysprog/c-standards) 的「為何不探討 C++」（`unsigned char [sizeof(TYPE)]` 與物件表示法）與 [指標篇](https://hackmd.io/@sysprog/c-pointer) 開頭的「回頭看 C 語言規格書」（object 的定義）等節

> **環境**：WSL2 Ubuntu 22.04 x86_64、gcc 11.4.0、clang 14.0.0（跨平台資料以 `clang --target=... -dM -E` 取得）。

## 為什麼需要這個？

C 的前身 B 語言只有一種資料單位：word（機器字組）。在 word 定址的 PDP-7 上這很合理，但搬到位元組定址的 PDP-11 上，處理字元就很彆扭——一個 word 裝兩個字元，要取出其中一個得自己做位移和遮罩。Dennis Ritchie 為此在 B 上加入了**型別**，C 就是這樣誕生的（[Ch 6](./06-history-of-c.md) 細說這段歷史）。

所以 C 的型別系統從一開始就不是為了抽象，而是為了**告訴編譯器：這段記憶體有幾個 bytes、要怎麼解讀**。這也是為什麼 C 的型別規則看起來充滿「實作定義」：語言設計者要讓同一份程式能在 word 大小不同的機器上都編得出有效率的機械碼，所以只規定**最低限度**，剩下交給平台。

不搞懂這一層，你會不斷撞上這些問題：

- `long` 在 Linux 是 8 bytes，在 Windows 是 4 bytes，同一份程式在兩邊行為不同
- `if (strlen(s) - 4 > 0)` 對短字串也成立
- 讀 UTF-8 時 `if (c == 0xE4)` 永遠不成立
- `sizeof(struct)` 比成員大小加總還大

## 先建立直覺

記憶體是一長排編了號的格子，每格 1 byte。**格子本身沒有型別**，只有 0 和 1。型別是你戴上的眼鏡：

```
位址:     0x1000  0x1001  0x1002  0x1003
內容:  │  0x00  │  0x00  │  0xE0  │  0x40  │

戴 int 眼鏡（4 bytes, little-endian）     → 1088421888
戴 float 眼鏡（4 bytes, IEEE 754）       → 7.0
戴 unsigned char 眼鏡，只看 0x1002       → 224
戴 signed char 眼鏡，只看 0x1002（x86）  → -32
```

同一串位元組，眼鏡不同，讀出的「值」完全不同。C 標準的用詞是：

- **物件**（object，N1570 3.15）：「執行環境中的一塊資料儲存區域，其內容可以表示值」。注意這和物件導向的 object 無關，`int x;` 的 `x` 就是一個物件
- **型別**（type）：決定物件佔多少 bytes、這些 bytes 怎麼解讀成值、能做哪些運算
- **物件表示法**（object representation，6.2.6.1p4）：物件那 `sizeof` 個 bytes 的實際內容

jserv 在原文引了一句推文：「In C, everything is a representation (`unsigned char [sizeof(TYPE)]`)」——這句不是玩笑，是 6.2.6.1p4 的白話版：任何（非 bit-field）物件都可以被看成一個 `unsigned char` 陣列。

## sizeof 與整數型別

```c
#include <limits.h>
#include <stdint.h>
#include <stdio.h>

#define SHOW(T) printf("%-20s %2zu bytes\n", #T, sizeof(T))

int main(void)
{
    SHOW(char);
    SHOW(short);
    SHOW(int);
    SHOW(long);
    SHOW(long long);
    SHOW(float);
    SHOW(double);
    SHOW(long double);
    SHOW(void *);
    SHOW(size_t);
    SHOW(int8_t);
    SHOW(int_least16_t);
    SHOW(int_fast16_t);
    SHOW(intptr_t);
    printf("CHAR_BIT = %d\n", CHAR_BIT);
    printf("INT_MIN  = %d, INT_MAX = %d\n", INT_MIN, INT_MAX);
    printf("UINT_MAX = %u\n", UINT_MAX);
    printf("LONG_MAX = %ld\n", LONG_MAX);
    printf("CHAR_MIN = %d, CHAR_MAX = %d\n", CHAR_MIN, CHAR_MAX);
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra sizes.c -o sizes && ./sizes
char                  1 bytes
short                 2 bytes
int                   4 bytes
long                  8 bytes
long long             8 bytes
float                 4 bytes
double                8 bytes
long double          16 bytes
void *                8 bytes
size_t                8 bytes
int8_t                1 bytes
int_least16_t         2 bytes
int_fast16_t          8 bytes
intptr_t              8 bytes
CHAR_BIT = 8
INT_MIN  = -2147483648, INT_MAX = 2147483647
UINT_MAX = 4294967295
LONG_MAX = 9223372036854775807
CHAR_MIN = -128, CHAR_MAX = 127
```

`#T` 是前置處理器的字串化運算子，把巨集參數變成字串常值，[Ch 27](./27-preprocessor-basics.md) 會講。`%zu` 是印 `size_t` 的正確格式，用 `%d` 在 64 位元平台上是型別不符的未定義行為。

這張表是**這台機器**的結果。標準到底保證什麼？

| 型別 | 標準保證的最小範圍（5.2.4.2.1） | 換算的最少位元數 |
|---|---|---|
| `char` | `CHAR_BIT` ≥ 8 | 8 |
| `short` / `int` | ±32767 | 16 |
| `long` | ±2147483647 | 32 |
| `long long` | ±9223372036854775807 | 64 |

再加上一條順序關係（6.2.5p8）：等級（rank）較低的型別，其值域是較高者的子集合。也就是 `char ≤ short ≤ int ≤ long ≤ long long`。**就這樣**。`int` 是 4 bytes、`long` 是 8 bytes 都不是標準保證的。`sizeof(char)` 永遠是 1（6.5.3.4p4），但「1 byte」不一定是 8 bits——`CHAR_BIT` 才是。POSIX 規定 `CHAR_BIT == 8`，某些 DSP 上 `CHAR_BIT` 是 16 或 32。

### 資料模型：同一個 `long`，四種答案

用 clang 的跨平台能力，不必真的有那些機器，就能查到各平台的型別大小（`--target` 指定目標平台，`-dM -E` 倒出預定義巨集）：

```
$ clang --target=<target> -dM -E -x c /dev/null | grep -E "__CHAR_UNSIGNED__|__SIZEOF_..."
```

整理實測結果：

| 目標平台 | 資料模型 | `int` | `long` | 指標 | `long double` | `char` 有號？ |
|---|---|---|---|---|---|---|
| x86_64-linux-gnu | LP64 | 4 | **8** | 8 | 16 | 有號 |
| aarch64-linux-gnu | LP64 | 4 | 8 | 8 | 16 | **無號** |
| i386-linux-gnu | ILP32 | 4 | 4 | 4 | 12 | 有號 |
| armv7-linux-gnueabihf | ILP32 | 4 | 4 | 4 | 8 | **無號** |
| x86_64-windows-msvc | LLP64 | 4 | **4** | 8 | 8 | 有號 |

資料模型的命名是「哪些型別是 64 位元」：LP64 = Long 與 Pointer 是 64；LLP64 = 只有 Long Long 與 Pointer 是 64；ILP32 = Int、Long、Pointer 都是 32。Windows 選 LLP64 的主要理由是相容：大量 Win32 API 與檔案格式把 `long`（`LONG`、`DWORD` 等）寫死成 32 位元。後果是「用 `long` 存指標」的 Unix 程式碼，移植到 64 位元 Windows 就截斷了。

### `<stdint.h>`：把位元數寫進型別名稱

C99 引入 `<stdint.h>`（N1570 7.20）解決「我就是要 32 位元」的需求：

| 型別家族 | 例子 | 保證 | 必備？ |
|---|---|---|---|
| 精確寬度 | `int32_t`、`uint8_t` | 剛好 N 位元、無填充位元、二補數 | **選用**（7.20.1.1p3） |
| 最小寬度 | `int_least16_t` | 至少 N 位元的最小型別 | 必備 |
| 最快寬度 | `int_fast16_t` | 至少 N 位元、「通常最快」的型別 | 必備 |
| 指標大小 | `intptr_t`、`uintptr_t` | 可與 `void *` 互轉不失真 | 選用 |
| 最大 | `intmax_t` | 最大的整數型別 | 必備 |

精確寬度型別是選用的，因為有些機器根本沒有 8 位元的型別（`CHAR_BIT` 為 16 的 DSP）。不過 7.20.1.1p3 也規定：只要實作**有**符合條件的 8/16/32/64 位元型別，就**必須**提供對應的 typedef。實務上主流平台都有。

上面實測 `int_fast16_t` 在 glibc x86-64 是 **8 bytes**。這是 glibc 的選擇（認為 64 位元暫存器運算最快），不是硬體定律；musl 等其他 libc 可能不同。把 `int_fast16_t` 寫進檔案格式或網路協定就會出事。

`<inttypes.h>` 提供對應的 `printf` 格式巨集，例如 `printf("%" PRId64 "\n", x);`——因為 `int64_t` 在 LP64 是 `long`（`%ld`），在 LLP64 是 `long long`（`%lld`），寫死哪個都不可攜。

## 底層機制：有號、無號與轉換規則

### 無號：模運算，永遠不溢位

N1570 6.2.5p9：「涉及無號運算元的計算永遠不會溢位」，結果對 `UINT_MAX + 1` 取模。轉換規則 6.3.1.3p2：把一個值轉成無號型別，若裝不下，就反覆加減「最大值 + 1」直到落在範圍內。

```
unsigned u = -1;
  → -1 + (UINT_MAX + 1) = -1 + 4294967296 = 4294967295 = 0xFFFFFFFF
```

### 有號：裝不下是另一回事

轉成有號型別而值裝不下時，結果是**實作定義**的（6.3.1.3p3，gcc 文件說明它取模）。有號運算本身溢位則是**未定義行為**（6.5p5），Ch 0 的 UBSan 範例就是這個。C23 起規定有號整數一律用二補數表示，但**溢位仍然是未定義行為**——表示法統一不等於溢位有定義，這點常被誤解。[Ch 21](./21-number-systems.md)、[Ch 22](./22-integer-overflow.md) 專門處理。

### 整數提升與一般算術轉換

```
           運算式 a + b
                │
  ① 整數提升（integer promotions，6.3.1.1p2）
     比 int 小的型別（char、short、bit-field）先轉成 int
     （int 裝得下原型別所有值的話；否則轉 unsigned int）
                │
  ② 一般算術轉換（usual arithmetic conversions，6.3.1.8）
     兩邊轉成「共同型別」，大致規則：
       有一邊是浮點 → 轉浮點
       否則 rank 高的贏；
       同 rank 一有號一無號 → 轉無號   ← -1 < 1u 為假的根源
       有號 rank 較高且裝得下無號的所有值 → 轉有號
                │
  ③ 用共同型別運算
```

實測：

```c
#include <stdio.h>
#include <string.h>

int main(void)
{
    unsigned u = -1;                 /* 轉換規則：-1 + (UINT_MAX + 1) */
    printf("unsigned u = -1  -> %u (0x%x)\n", u, u);

    int i = -1;
    unsigned one = 1;
    printf("-1 < 1u        -> %d\n", i < one);
    printf("-1 < (long) 1u -> %d\n", (long) i < (long) one);

    const char *s = "abc";
    if (strlen(s) - 4 > 0)          /* size_t 減法不會變負 */
        printf("strlen(s) - 4 = %zu\n", strlen(s) - 4);

    unsigned char a = 200, b = 100;
    int sum = a + b;                 /* 整數提升：先轉 int 再加 */
    unsigned char wrapped = a + b;   /* 存回 unsigned char：模 256 */
    printf("a + b = %d, stored in uchar = %d\n", sum, wrapped);
    printf("sizeof(a + b) = %zu\n", sizeof(a + b));
    return 0;
}
```

```
$ gcc -Wall -Wextra conv.c -o conv && ./conv
conv.c: In function ‘main’:
conv.c:11:40: warning: comparison of integer expressions of different signedness: ‘int’ and ‘unsigned int’ [-Wsign-compare]
   11 |     printf("-1 < 1u        -> %d\n", i < one);
      |                                        ^
unsigned u = -1  -> 4294967295 (0xffffffff)
-1 < 1u        -> 0
-1 < (long) 1u -> 1
strlen(s) - 4 = 18446744073709551615
a + b = 300, stored in uchar = 44
sizeof(a + b) = 4
```

逐條對照規則：

- `-1 < 1u`：同 rank（都是 int 等級）、一有號一無號 → 轉無號 → `4294967295 < 1` → 假
- 先轉 `long` 再比：在 LP64 上 `long` 裝得下 `unsigned int` 的所有值，兩邊都是有號 → 真。**但在 LLP64（Windows）上 `long` 也是 32 位元，這招無效**
- `strlen` 回傳 `size_t`（無號），`3 - 4` 繞回 `SIZE_MAX`，條件永遠成立。**gcc 對這行一個警告都沒給**
- `a + b`：兩個 `unsigned char` 先提升成 `int`，所以是 300 不是 44，`sizeof(a + b)` 是 4

再開 `-Wconversion -Wsign-conversion`：

```
conv.c:6:18: warning: unsigned conversion from ‘int’ to ‘unsigned int’ changes value from ‘-1’ to ‘4294967295’ [-Wsign-conversion]
conv.c:11:40: warning: comparison of integer expressions of different signedness: ‘int’ and ‘unsigned int’ [-Wsign-compare]
```

`strlen(s) - 4 > 0` 依然沒有警告。工具會幫忙，但不能取代你懂規則。

## `char` 的正負號是實作定義

N1570 6.2.5p15：`char`、`signed char`、`unsigned char` 是**三個不同的型別**；`char` 的值域、表示法與行為由實作選擇等同 `signed char` 或 `unsigned char` 其中之一。上面的表格已經看到：x86 上 `char` 有號，ARM 上無號。

```c
#include <stdio.h>

int main(void)
{
    char c = (char) 0xE4;          /* UTF-8 「中」的第一個位元組 0xE4 */
    int  i = c;
    printf("c = %d, (c > 0) = %d\n", i, c > 0);
    if (c == 0xE4)
        puts("c == 0xE4");
    else
        puts("c != 0xE4 (!)");
    return 0;
}
```

```
$ gcc -Wall -Wextra charsign.c -o cs && ./cs
charsign.c: In function ‘main’:
charsign.c:8:11: warning: comparison is always false due to limited range of data type [-Wtype-limits]
    8 |     if (c == 0xE4)
      |           ^~
c = -28, (c > 0) = 0
c != 0xE4 (!)
$ gcc -Wall -Wextra -funsigned-char charsign.c -o cs && ./cs
c = 228, (c > 0) = 1
c == 0xE4
```

`-funsigned-char` 讓 gcc 模擬 ARM 的選擇，同一份程式結果翻轉。x86 上：`c` 存的位元組是 `0xE4`，以有號解讀是 −28；比較時 `c` 提升成 `int` 的 −28，而 `0xE4` 是 `int` 的 228，永遠不相等。

實務結論：

- 處理**原始位元組**（檔案內容、網路封包、UTF-8）一律用 `unsigned char` 或 `uint8_t`
- `char` 只用來裝**文字**
- `<ctype.h>` 的 `isalpha(c)` 等函式要求引數是 `EOF` 或「可表示為 `unsigned char` 的值」，傳一個負的 `char` 是未定義行為，正確寫法是 `isalpha((unsigned char) c)`

Linux 核心在 6.2 版把整個核心改成以 `-funsigned-char` 編譯，統一所有架構上的 `char` 行為，起因正是一連串只在某些架構出現的 bug。

## 陣列與 struct 在記憶體裡的樣子

```c
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

struct packet {
    char     tag;     /* 1 byte */
    int32_t  len;     /* 4 bytes，要求 4-byte 對齊 */
    char     flag;    /* 1 byte */
};

struct packet_sorted {
    int32_t  len;
    char     tag;
    char     flag;
};

static void dump(const char *name, const void *obj, size_t n)
{
    const unsigned char *p = obj;
    printf("%-8s:", name);
    for (size_t i = 0; i < n; i++)
        printf(" %02x", p[i]);
    putchar('\n');
}

int main(void)
{
    int arr[4] = {1, 2, 0x01020304, -1};
    printf("sizeof arr = %zu, elements = %zu\n", sizeof arr, sizeof arr / sizeof arr[0]);
    for (int i = 0; i < 4; i++)
        printf("&arr[%d] = %p\n", i, (void *) &arr[i]);
    dump("arr", arr, sizeof arr);

    printf("sizeof(struct packet)        = %zu\n", sizeof(struct packet));
    printf("  offsetof tag  = %zu\n", offsetof(struct packet, tag));
    printf("  offsetof len  = %zu\n", offsetof(struct packet, len));
    printf("  offsetof flag = %zu\n", offsetof(struct packet, flag));
    printf("sizeof(struct packet_sorted) = %zu\n", sizeof(struct packet_sorted));
    printf("_Alignof(int32_t) = %zu, _Alignof(struct packet) = %zu\n",
           _Alignof(int32_t), _Alignof(struct packet));

    struct packet pk = {.tag = 'A', .len = 0x11223344, .flag = 1};
    dump("pk", &pk, sizeof pk);
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra -g layout.c -o layout && ./layout
sizeof arr = 16, elements = 4
&arr[0] = 0x7ffdfa4cc160
&arr[1] = 0x7ffdfa4cc164
&arr[2] = 0x7ffdfa4cc168
&arr[3] = 0x7ffdfa4cc16c
arr     : 01 00 00 00 02 00 00 00 04 03 02 01 ff ff ff ff
sizeof(struct packet)        = 12
  offsetof tag  = 0
  offsetof len  = 4
  offsetof flag = 8
sizeof(struct packet_sorted) = 8
_Alignof(int32_t) = 4, _Alignof(struct packet) = 4
pk      : 41 00 00 00 44 33 22 11 01 00 00 00
```

（位址每次執行不同。）`dump` 用 `const unsigned char *` 走訪物件——這是標準明文允許的（6.3.2.3p7：物件指標轉成字元型別指標後指向物件最低位址的位元組，遞增可走訪其餘位元組），下一章會把這個技巧拆開。

**陣列**：元素緊密相鄰、沒有空隙，位址每次差 `sizeof(int)` = 4。`sizeof arr / sizeof arr[0]` 是求元素個數的慣用寫法（只對真正的陣列有效，Ch 3 會示範它在函式參數上失效）。`0x01020304` 存成 `04 03 02 01`：x86-64 是 **little-endian**（最低有效位元組放在最低位址）。`-1` 是 `ff ff ff ff`：二補數。

**struct**：

```
struct packet（12 bytes）
 offset: 0    1    2    3    4    5    6    7    8    9   10   11
       ┌────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┐
       │tag │ pad│ pad│ pad│        len        │flag│ pad│ pad│ pad│
       │ 41 │ 00 │ 00 │ 00 │ 44 │ 33 │ 22 │ 11 │ 01 │ 00 │ 00 │ 00 │
       └────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┘
                             ↑ len 必須從 4 的倍數開始       ↑ 尾端補到 4 的倍數

struct packet_sorted（8 bytes）
       ┌────┬────┬────┬────┬────┬────┬────┬────┐
       │        len        │tag │flag│ pad│ pad│
       └────┴────┴────┴────┴────┴────┴────┴────┘
```

規格書說了什麼：

- 成員位址依宣告順序遞增，第一個成員前**不會**有填充（6.7.2.1p15）——所以 struct 指標轉型後可以指向第一個成員，[Ch 29](./29-oop-in-c.md) 的 C 語言繼承手法就靠這條
- 成員之間與尾端**可能**有未命名的填充（6.7.2.1p15、p17）
- 填充多少由實作依**對齊需求**（alignment requirement）決定：x86-64 SysV ABI 規定 `int32_t` 要放在 4 的倍數位址
- 尾端填充是為了陣列：`struct packet a[2];` 的 `a[1].len` 也要對齊
- 填充位元組的內容是**未指定的**（6.2.6.1p6）。這裡剛好印出 `00`，換個情境可能是殘留的垃圾

把成員依大小遞減排列，同樣的資料從 12 bytes 縮到 8 bytes。對齊為什麼存在、硬體不對齊會怎樣、`_Alignas` 怎麼用，是 [Ch 16](./16-alignment-and-padding.md) 的主題；這裡先記住「`sizeof(struct)` ≥ 成員大小總和」。

## 對比與取捨

| 需求 | 選哪個型別 | 理由 |
|---|---|---|
| 迴圈計數、一般整數運算 | `int` | 平台最自然的大小；溢位是 UB 反而讓編譯器能最佳化 |
| 物件大小、陣列索引、`sizeof` 結果 | `size_t` | 保證裝得下任何物件的大小；但記得它無號 |
| 兩個指標相減 | `ptrdiff_t` | 6.5.6p9 規定的結果型別 |
| 檔案格式、網路協定、暫存器 | `uint8_t`/`uint32_t`… | 位元數固定；仍要處理 endianness |
| 原始位元組 | `unsigned char` | 標準特許存取任何物件的型別 |
| 文字 | `char` | 字串函式的介面型別 |
| 存指標的整數 | `uintptr_t` | `long` 在 LLP64 不夠大 |

無號型別要不要用在「不可能為負」的量上（例如長度）？這在社群裡**沒有共識**。Google C++ 風格指南建議盡量用有號，因為無號的模運算會把減法錯誤藏起來（就像 `strlen(s) - 4`）；另一派認為 `size_t` 是標準函式庫的介面，混用有號反而產生更多比較警告。本課的立場：和標準函式庫交界處用 `size_t`，並在做減法前先比較大小。

## 踩雷集錦

1. **「`int` 是 32 位元」**：標準只保證至少 16 位元。在本課的環境上是 32，但 16 位元的 MCU（例如部分 MSP430、AVR 的 `int`）上 `int` 是 16 位元，`int x = 40000;` 就溢出了。需要確定寬度時用 `<stdint.h>`。
2. **「`char` 就是有號的 byte」**：實作定義。ARM Linux 上它是無號的，`char c = -1; if (c < 0)` 在 ARM 上不成立。處理 byte 一律用 `unsigned char`。
3. **「`unsigned` 比較安全，因為不會溢位」**：它不會「溢位」是因為它定義成模運算——錯誤的減法會安靜地得到一個巨大正數，而不是負數。`for (size_t i = n - 1; i >= 0; i--)` 是無窮迴圈，因為 `i >= 0` 恆真（gcc 的 `-Wextra` 會以 `-Wtype-limits` 提醒）。
4. **「C23 統一二補數，所以有號溢位現在有定義了」**：沒有。C23 只統一**表示法**，`INT_MAX + 1` 仍是未定義行為，編譯器仍可依此最佳化。
5. **「`sizeof` 是函式」**：它是運算子，結果在編譯期決定（除了 VLA），運算元**不會被求值**（6.5.3.4p2）：`sizeof(i++)` 不會讓 `i` 加一。對變數可以不加括號（`sizeof x`），對型別名稱一定要加（`sizeof(int)`）。
6. **「用 `memcmp` 比較兩個 struct 是否相等」**：填充位元組的內容未指定，兩個成員全等的 struct 可能 `memcmp` 不相等。要嘛逐成員比較，要嘛在初始化時先 `memset` 清零（即使如此，對成員賦值時填充位元組可能被改寫，標準不保證）。

## 進階：再往深一層

- **整數轉換等級（rank）**的完整定義在 6.3.1.1p1，是一長串條列規則，包含 `_Bool` 最低、`char`/`signed char`/`unsigned char` 同等級、列舉型別與其底層型別同等級等。值得逐條讀一次，[Ch 4](./04-reading-the-standard.md) 會以這段當作讀規格書的練習。
- **`size_t` 的上限不等於可配置的上限**：`SIZE_MAX` 是 2⁶⁴−1，但 x86-64 Linux 的使用者空間只有 47 位元（四層分頁），任何單一物件大小實際上遠小於 `SIZE_MAX`。glibc 的 `malloc` 也拒絕超過 `PTRDIFF_MAX` 的請求，因為那樣的物件無法用 `ptrdiff_t` 表示兩端指標的差。
- **`_Bool`/`bool`**：C99 的 `_Bool` 只能存 0 或 1，任何非零值轉換後都是 1（6.3.1.2）。C23 把 `bool`、`true`、`false` 變成關鍵字（Ch 0 的 C23 範例）。
- **`_BitInt(N)`**：C23 新增任意位元寬度的整數型別，例如 `_BitInt(24)`，且**不做整數提升**。實測 clang 14（`-std=c2x`）與 gcc 14.4（Docker，`-std=c23`）都接受下面的程式並印出 `sizeof x = 4, u = 0`：`unsigned _BitInt(4)` 的 15 加 1 在 4 位元內繞回 0，`_BitInt(24)` 的儲存空間被補到 4 bytes。gcc 11 不支援。
  ```c
  _BitInt(24) x = 0x7fffff;
  unsigned _BitInt(4) u = 15;
  u = u + 1;
  printf("sizeof x = %zu, u = %d\n", sizeof x, (int) u);
  ```
- **核心怎麼處理這些問題**：Linux 核心有自己的 `u8`/`u16`/`u32`/`s32` 型別（`include/linux/types.h`），並用 `__le32`/`__be32` 搭配 sparse 靜態檢查器標記 endianness，防止忘記轉換。

## 動手練習

1. 不執行，先預測下面三行的輸出，再實際跑：
   ```c
   printf("%d\n", (char) 200);
   printf("%d\n", (unsigned char) -56);
   printf("%d\n", -1 < sizeof(int));
   ```
2. 改寫 `struct packet`，加入一個 `double` 成員放在最前面、中間、最後面，分別預測 `sizeof` 與每個 `offsetof`，再驗證。
3. 寫一個函式 `int widen(const char *p) { return *p; }`，用 `clang --target=aarch64-linux-gnu -S -O2` 和 `clang -S -O2`（x86-64）各編一次，找出 `char` 的正負號在組合語言裡造成什麼差異（提示：把位元組載入暫存器時，x86-64 用 `movsx`（sign-extend）或 `movzx`（zero-extend），AArch64 用 `ldrsb` 或 `ldrb`；也可以直接在 godbolt.org 選 ARM64 gcc 比較）。

## 本章重點整理

- 型別是記憶體的解讀方式：決定大小、表示法與可用運算；任何物件都可以看成 `unsigned char [sizeof]`
- 標準只保證整數型別的**最小**範圍與相對順序；實際大小看資料模型（LP64、LLP64、ILP32）
- 有號/無號混用時，一般算術轉換常把有號轉成無號；無號運算是模運算，有號溢位是 UB
- `char` 的正負號是實作定義；處理位元組用 `unsigned char`
- 陣列元素緊密相鄰；struct 成員依序排列，但之間與尾端可能有填充

## 自我檢核

- [ ] 不看本章，能說出 `int`、`long` 標準保證的最小位元數，以及 Linux x86-64 與 Windows x64 上 `long` 的實際大小和原因
- [ ] 能一步一步推導 `-1 < 1u` 為什麼是假（提到整數提升與一般算術轉換）
- [ ] 能解釋為什麼 `if (c == 0xE4)` 在 x86 上永遠不成立，並說出兩種修法
- [ ] 給你一個 struct 定義，能畫出它的位元組布局、標出填充位置
- [ ] 能說明「C23 統一二補數」改變了什麼、沒有改變什麼

## 延伸閱讀

### 官方文件 / Spec

- **[ISO/IEC 9899 草案 N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**（[網頁版](http://port70.net/~nsz/c/c11/n1570.html)可直接用 `#6.3.1.8` 這種錨點跳轉）
  - **讀哪裡**：5.2.4.2.1（`<limits.h>` 最小值）、6.2.5（型別，p15 是 `char`）、6.2.6.1（物件表示法）、6.3.1.1–6.3.1.8（轉換）、6.7.2.1p15–17（struct 布局）、7.20（`<stdint.h>`）
  - **前提知識**：本章；條文很短，但每個字都有意義，第一次讀慢慢來
- **[System V ABI AMD64 Architecture Processor Supplement](https://gitlab.com/x86-psABIs/x86-64-ABI)**
  - **讀哪裡**：3.1.2「Data Representation」的 Figure 3.1（每個 C 型別的大小與對齊），本章表格的權威來源

### 書籍

- **《Computer Systems: A Programmer's Perspective》（3rd ed.）** — Bryant & O'Hallaron（2015）
  - **讀哪幾章**：2.1（資訊儲存、byte order）、2.2（整數表示法，2.2.5 有號無號轉換）。比本章更多圖解與習題
  - **和本章的關聯**：本章是「規格書視角」，CSAPP 是「位元層級視角」，兩者互補；[Ch 21](./21-number-systems.md) 會再深入
- **《Modern C》** — Jens Gustedt（Manning；初版 2019 以 C17 為準，較新版本更新到 C23，作者網站另有免費 PDF 版）
  - **這本書的定位**：作者是 WG14 委員，從標準出發教 C，對 `size_t`、無號型別的立場鮮明
  - **讀哪幾章**：Level 1 的「Basic values and data」一章，對應本章的整數型別與轉換

### 部落格 / 技術文章

- **[64-Bit Programming Models: Why LP64?](https://unix.org/version2/whatsnew/lp64_wp.html)** — The Open Group
  - **這篇說什麼**：1990 年代 Unix 廠商選擇 LP64 的理由，比較 LP64、ILP64、LLP64 的取捨
  - **為什麼值得讀**：資料模型表格背後的設計決策一手文件；讀完會懂為什麼沒有人選 ILP64

型別讓我們知道「一段記憶體怎麼解讀」，但還沒回答「怎麼指到那段記憶體」。下一章處理多數人學 C 最卡的部分：指標。

→ [Ch 3 指標入門重建：位址、解參考與指標算術](./03-pointer-basics.md)
