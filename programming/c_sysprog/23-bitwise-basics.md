# Ch 23 — bitwise 基礎：位移語意與位元操作

> **目標**：能精確說出 C 位元運算子作用在「什麼」上（值的位元表示，不是記憶體位元組）；避開有號與無號混用的比較陷阱；記住位移的三種未定義行為與一種實作定義行為；熟練設定、清除、切換、測試位元與「以遮罩取代分支」；並用位元的眼光重新看懂 ASCII 表與 Ctrl 鍵。

> **對應原文**：[你所不知道的 C 語言：bitwise 操作篇](https://hackmd.io/@sysprog/c-bitwise) 的「概況」「整數的位元表示」「有號與無號混用的比較」「位移運算的語意」「設定、清除、切換與測試位元」「以遮罩取代分支」「ASCII 的編碼與控制鍵」等節，以及[數值系統篇](https://hackmd.io/@sysprog/c-numerics)的「運用 bit-wise operator」

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14。組合語言為 AT&T 語法、`-O2`，刪去 `endbr64` 等與主題無關的指示詞。

> 本章大量使用 [Ch 21](./21-number-systems.md) 的二補數與整數提升、[Ch 22](./22-integer-overflow.md) 的「有號溢位是 UB」。

## 為什麼需要這個？

系統軟體面對的資料，大多由硬體或外部格式規定到**位元**：分頁表項（page table entry）的 Present/RW/User 旗標、處理器旗標暫存器、指令編碼、網路封包標頭、壓縮後的位元串流、RGB565 畫素。C 有位元欄位（bit-field）語法，看起來能直接描述這些格式，但 C11 6.7.2.1 §11 把關鍵細節都交給實作：用多大的儲存單元、欄位放不下時跨不跨單元、在單元內從高位還是低位排起，全由實作定義，單元對齊則未指定。**位元欄位無法保證對上硬體或檔案格式。**

所以 Linux 核心、QEMU、FFmpeg 一律用「整數 + 遮罩 + 位移」：位元的位置完全由程式決定，對原生寬度的整數一道 AND 或位移通常就是一道指令，也多半不需要分支。原文開頭舉的三個例子值得記住：

- **Linux 紅黑樹**：`struct rb_node` 以 `aligned(sizeof(long))` 對齊，位址最低兩個位元必為 0，於是 `__rb_parent_color` 把**親代指標與顏色塞在同一個字組**：`& ~3` 取指標、`& 1` 取顏色，每個節點省一個欄位。
- **Linux RT 排程器**：100 個優先權各一條佇列，另以 101 位元的位元圖記錄哪些佇列非空；`sched_find_first_bit` 找最低位元的 1 就是最高優先權，成本與就緒工作數無關（原理在 [Ch 24](./24-bitwise-tricks.md) 的 ctz）。
- **FFmpeg `get_bits`**：以位元為單位記錄讀取位置 `index`，從第 `index >> 3` 個位元組讀起，左移 `index & 7` 丟掉已讀部分，再右移 `32 - n` 取出最高 n 位元。

這一章把這些操作背後的規則一次釘牢；技巧的大集合留到 [Ch 24](./24-bitwise-tricks.md)。

## 先建立直覺

把一個 `uint32_t` 想成 32 個並排的開關，編號 31（左、最高位）到 0（右、最低位）：

```
 位元編號   31 30 29 28 ...  7  6  5  4  3  2  1  0
           ┌──┬──┬──┬──┬───┬──┬──┬──┬──┬──┬──┬──┬──┐
    x =    │ 0│ 0│ 0│ 0│...│ 0│ 1│ 1│ 0│ 1│ 0│ 0│ 0│   = 0x68
           └──┴──┴──┴──┴───┴──┴──┴──┴──┴──┴──┴──┴──┘
  遮罩 1<<5 只有第 5 個開關是 1：
           x | (1<<5)   打開第 5 個（不管原來是什麼）
           x & ~(1<<5)  關掉第 5 個
           x ^ (1<<5)   切換第 5 個
           (x >> 5) & 1 讀第 5 個
```

三個運算子各管一件事：**`|` 只能把 0 變 1，`&` 只能把 1 變 0，`^` 只會翻轉**。遮罩上是 0 的位置，三者都不動。記住這三句，所有位元操作都是它們的組合。

這個模型有一個重要邊界：**位元編號指的是「值」的位元，不是記憶體裡的位元組順序**。位移與遮罩作用在數值上，與 endianness 無關——下一節會實測。

## 整數的位元表示

C11 6.2.6.2 §1 把無號整數的物件表示分成**值位元（value bits）** 與**填充位元（padding bits）**，有號整數另有一個**符號位元**（§2）。C11 允許三種符號表示（sign-magnitude、二補數、一補數）；C23 只留二補數。GCC 手冊寫明「GCC supports only two's complement integer types, and all bit patterns are ordinary values.」

精確寬度型別把彈性收得更緊：C11 7.20.1.1 規定 `intN_t` 寬度為 N、**沒有填充位元**、採二補數；`uintN_t` 寬度為 N、沒有填充位元。所以本章與下一章的範例幾乎都用 `uint32_t` 這類型別——位元的位置與數量都有保證。

C11 6.5 §4 直接說明位元運算子「yield values that depend on the internal representations of integers, and have implementation-defined and undefined aspects for signed types.」**可攜的位元運算一律用無號型別**，這是本章最重要的一條守則。

```c
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static void print_bits(const char *tag, uint32_t v, int w)
{
    printf("%-12s", tag);
    for (int i = w - 1; i >= 0; i--) {
        putchar('0' + ((v >> i) & 1));
        if (i && i % 4 == 0) putchar(' ');
    }
    putchar('\n');
}

int main(void)
{
    int32_t a = 6, b = -6, c = INT32_MIN, d = -1;
    print_bits("6", (uint32_t) a, 32);
    print_bits("-6", (uint32_t) b, 32);
    print_bits("INT32_MIN", (uint32_t) c, 32);
    print_bits("-1", (uint32_t) d, 32);

    /* 位元表示 vs 記憶體位元組順序 */
    uint32_t x = 0x12345678;
    unsigned char bytes[4];
    memcpy(bytes, &x, 4);
    printf("0x12345678 in memory: %02x %02x %02x %02x\n", bytes[0], bytes[1], bytes[2], bytes[3]);
    printf("x & 0xFF = 0x%02x, x >> 24 = 0x%02x\n", x & 0xFF, x >> 24);
    return 0;
}
```

```
6           0000 0000 0000 0000 0000 0000 0000 0110
-6          1111 1111 1111 1111 1111 1111 1111 1010
INT32_MIN   1000 0000 0000 0000 0000 0000 0000 0000
-1          1111 1111 1111 1111 1111 1111 1111 1111
0x12345678 in memory: 78 56 34 12
x & 0xFF = 0x78, x >> 24 = 0x12
```

x86-64 是 little-endian，記憶體裡最低位址放的是最低位元組 `78`；但 `x & 0xFF` 永遠是最低位元組、`x >> 24` 永遠是最高位元組，換到 big-endian 機器也一樣。**要依位元組順序存取，用 `unsigned char *`；要依數值位置存取，用位移與遮罩。** 兩者混用是可攜性 bug 的大宗（[Ch 24](./24-bitwise-tricks.md) 的 RGBA 灰階案例會實際碰到）。

## 有號與無號混用的比較

[Ch 21](./21-number-systems.md) 預告過一般算術轉換：兩個運算元一方無號、且其等級不低於另一方時，**有號的一方轉成無號**。這條規則對比較運算子同樣適用，`-1 < 0U` 比的是 4294967295 與 0。CS:APP 3e Figure 2.19 的表格，以 gcc 11 與 clang 14 逐列驗證（兩者輸出完全相同）：

```c
#include <stdio.h>

#define SHOW(e) printf("%-34s = %d\n", #e, (e))

int main(void)
{
    SHOW(0 == 0U);
    SHOW(-1 < 0);
    SHOW(-1 < 0U);
    SHOW(2147483647 > -2147483647 - 1);
    SHOW(2147483647U > -2147483647 - 1);
    SHOW(2147483647 > (int) 2147483648U);
    SHOW(-1 > -2);
    SHOW((unsigned) -1 > -2);
    SHOW(2147483647 < 2147483648U);
    printf("sizeof(-2147483648) = %zu\n", sizeof(-2147483648));
    printf("sizeof(0x80000000)  = %zu\n", sizeof(0x80000000));
    return 0;
}
```

```
0 == 0U                            = 1
-1 < 0                             = 1
-1 < 0U                            = 0
2147483647 > -2147483647 - 1       = 1
2147483647U > -2147483647 - 1      = 0
2147483647 > (int) 2147483648U     = 1
-1 > -2                            = 1
(unsigned) -1 > -2                 = 1
2147483647 < 2147483648U           = 1
sizeof(-2147483648) = 8
sizeof(0x80000000)  = 4
```

幾列值得逐一想：

- `2147483647U > -2147483647 - 1` 為 0：右邊的 INT_MIN 轉成無號是 2147483648，比左邊大。
- `(unsigned) -1 > -2` 為 1：兩邊都變無號，4294967295 > 4294967294。
- 為什麼把 −2³¹ 寫成 `-2147483647 - 1`？因為 `-2147483648` 是一元負號作用在常數 `2147483648` 上，後者超出 `int`，C99 起改用 `long`——最後兩行 `sizeof` 證實了：`-2147483648` 是 8 位元組的 `long`，十六進位的 `0x80000000` 則是 4 位元組的 `unsigned int`（十六進位常數會先試無號型別）。CS:APP 的補充說明〈Writing TMin in C〉列出 C90 與 C99 的完整規則。`<limits.h>` 把 `INT_MIN` 定義成 `(-INT_MAX - 1)` 就是這個原因。

處理器本身不知道變數的型別：轉換之後，編譯器選擇無號比較（x86 的 `setb`/`ja`）或有號比較（`setl`/`jg`）。**比較的語意在編譯時期就決定了**。

### 藏在迴圈條件裡

```c
#include <stdio.h>

int main(void)
{
    int n = 10;
    for (int i = n - 1; i - sizeof(char) >= 0; i--)
        printf("i: %d\n", i);
    return 0;
}
```

`sizeof` 的結果是無號的 `size_t`（C11 6.5.3.4 §5），`i - sizeof(char)` 因此是 `size_t`，`>= 0` 恆為真，迴圈不會結束；`i` 遞減到 `INT_MIN` 後再 `i--` 就是有號溢位。編譯器的反應：

```
$ gcc -Wall -Wextra -c sizeof_loop.c
sizeof_loop.c: In function ‘main’:
sizeof_loop.c:6:42: warning: comparison of unsigned expression in ‘>= 0’ is always true [-Wtype-limits]
    6 |     for (int i = n - 1; i - sizeof(char) >= 0; i--)
      |                                          ^~
$ clang -Wall -Wextra -c sizeof_loop.c && echo "(no warning)"
(no warning)
$ clang -Wall -Wextra -Wtype-limits -c sizeof_loop.c
sizeof_loop.c:6:42: warning: result of comparison of unsigned expression >= 0 is always true [-Wtautological-unsigned-zero-compare]
```

gcc 的 `-Wextra` 含 `-Wtype-limits`；clang 14 要另外開。**兩個編譯器都跑一次**是值得養成的習慣。

### 負的「錯誤碼」被當成很大的長度

```c
#include <stdio.h>
#include <string.h>

int main(void)
{
    const char *s = "hi";
    int limit = -1;                 /* 例如：解析失敗回傳 -1 */
    if (strlen(s) > limit)
        printf("too long\n");
    else
        printf("ok\n");
    return 0;
}
```

```
len_cmp.c:8:19: warning: comparison of integer expressions of different signedness: ‘size_t’ {aka ‘long unsigned int’} and ‘int’ [-Wsign-compare]
    8 |     if (strlen(s) > limit)
      |                   ^
ok
```

`limit` 被轉成 `SIZE_MAX`，任何長度都「不超過上限」，錯誤碼變成了「無限大的許可」。這和 [Ch 22](./22-integer-overflow.md) 的 FreeBSD 漏洞是同一個模式，只是方向相反。`-Wsign-compare` 在 gcc 由 `-Wextra` 開啟（C 模式），clang 也會在 `-Wextra` 下報。

### 寬度改變：符號擴充與零擴充

[Ch 21](./21-number-systems.md) 證明過：有號值變寬時複製符號位元（sign extension）保持值不變，無號值補 0（zero extension）。gcc `-O2` 的對應指令：

```c
long sext(int x) { return x; }
unsigned long zext(unsigned x) { return x; }
int from_s8(signed char c) { return c; }
int from_u8(unsigned char c) { return c; }
```

```
sext:    movslq %edi, %rax      ← 32→64 符號擴充
zext:    movl   %edi, %eax      ← 寫 32 位元暫存器會自動把高 32 位元清 0
from_s8: movsbl %dil, %eax      ← 8→32 符號擴充（movsx）
from_u8: movzbl %dil, %eax      ← 8→32 零擴充（movzx）
```

`zext` 只有一道 `movl`：x86-64 規定寫入 32 位元暫存器時高 32 位元自動清零，零擴充是免費的。

## 位移運算的語意

位移的規定集中在 C11 6.5.7（C23 6.5.8）。§3：先對**兩個運算元各自**做整數提升（不是一般算術轉換！），結果型別是提升後的左運算元型別，以及：

> If the value of the right operand is negative or is greater than or equal to the width of the promoted left operand, the behavior is undefined.

§4、§5 規定值：

> The result of E1 << E2 is E1 left-shifted E2 bit positions; vacated bits are filled with zeros. If E1 has an unsigned type, the value of the result is E1 × 2^E2, reduced modulo one more than the maximum value representable in the result type. If E1 has a signed type and nonnegative value, and E1 × 2^E2 is representable in the result type, then that is the resulting value; otherwise, the behavior is undefined.
>
> The result of E1 >> E2 is E1 right-shifted E2 bit positions. If E1 has an unsigned type or if E1 has a signed type and a nonnegative value, the value of the result is the integral part of the quotient of E1 / 2^E2. If E1 has a signed type and a negative value, the resulting value is implementation-defined.

歸納成一張表（C23 統一二補數之後**這張表沒有變**）：

| 情況 | 行為 |
|---|---|
| 位移量為負 | **未定義** |
| 位移量 ≥ 提升後左運算元的寬度（例如 `uint32_t` 移 32） | **未定義** |
| 有號左移，結果放不下（含左運算元為負，例如 `1 << 31`、`-1 << 1`） | **未定義** |
| 負數右移（`-3 >> 1`） | **實作定義**（GCC/Clang：算術右移） |
| 無號左移 | 取模 2^N，有定義 |
| 無號右移、非負有號右移 | 商的整數部分，有定義 |

### 邏輯右移與算術右移

硬體有兩種右移：**邏輯右移（logical shift）** 左側補 0，**算術右移（arithmetic shift）** 左側複製符號位元。C 以運算元型別決定用哪一種：

```
x = 0xA2        1 0 1 0 0 0 1 0
邏輯右移 2     [0 0]1 0 1 0 0 0    ← 補 0     → 0x28
算術右移 2     [1 1]1 0 1 0 0 0    ← 複製符號 → 0xE8
```

```c
#include <stdint.h>
#include <stdio.h>

int main(void)
{
    uint8_t u = 0xA2;          /* 1010 0010 */
    int8_t s = (int8_t) 0xA2;  /* 轉換結果由實作定義，GCC 與 Clang 皆得 -94 */

    printf("s = %d\n", s);
    printf("logical:    %02X\n", (unsigned) (uint8_t) (u >> 2));
    printf("arithmetic: %02X\n", (unsigned) (uint8_t) (s >> 2));
    printf("-3 >> 1 = %d, -3 / 2 = %d\n", -3 >> 1, -3 / 2);
    printf("-3 %% 2 = %d, -3 & 1 = %d\n", -3 % 2, -3 & 1);
    return 0;
}
```

```
s = -94
logical:    28
arithmetic: E8
-3 >> 1 = -2, -3 / 2 = -1
-3 % 2 = -1, -3 & 1 = 1
```

`u` 與 `s` 都先提升為 `int`（162 與 −94），右移後再轉回 `uint8_t` 取最低 8 位元。後兩行揭露一個常被忽略的差異：**右移向負無限大取整，除法向零截斷**（C11 6.5.5 §6）。所以 `-3 >> 1 == -2` 而 `-3 / 2 == -1`；`%` 的正負號跟被除數走，`-3 % 2 == -1`，但 `-3 & 1 == 1`。

這直接影響編譯器能產生的程式碼：

```c
int sdiv2(int x) { return x / 2; }
int sshr1(int x) { return x >> 1; }
unsigned udiv2(unsigned x) { return x / 2; }
int is_odd_and(int x) { return x & 1; }
int is_odd_mod(int x) { return x % 2 != 0; }
int is_odd_bad(int x) { return x % 2 == 1; }
```

```
sdiv2:                       sshr1:               udiv2:
  movl %edi, %eax              movl %edi, %eax      movl %edi, %eax
  shrl $31, %eax  ← 取符號位   sarl %eax            shrl %eax
  addl %edi, %eax ← 負數加 1   ret                  ret
  sarl %eax
  ret
is_odd_and:           is_odd_mod:           is_odd_bad:
  movl %edi, %eax       movl %edi, %eax       movl  %edi, %eax
  andl $1, %eax         andl $1, %eax         shrl  $31, %eax
  ret                   ret                   addl  %eax, %edi
                                              andl  $1, %edi
                                              subl  %eax, %edi
                                              xorl  %eax, %eax
                                              cmpl  $1, %edi
                                              sete  %al
                                              ret
```

有號的 `x / 2` 要先把負數加 1 再算術右移，才符合「向零截斷」；無號的 `/ 2` 與有號的 `>> 1` 都只要一道位移。`x % 2 != 0` 與 `x & 1` 等價，都編成一道 `and`；但 `x % 2 == 1` **對負的奇數為假**（−3 % 2 是 −1），gcc 只好照字面算出帶正負號的餘數再比較，8 道指令換來一個錯誤的奇偶判斷。

### 三種 UB 的實際樣貌

```c
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    unsigned n = argc > 1 ? (unsigned) atoi(argv[1]) : 32;
    unsigned x = 1;
    printf("x << n (runtime n=%u) = %u\n", n, x << n);
    printf("1u << 32 (constant)  = %u\n", 1u << 32);
    printf("1 << 31              = %d\n", 1 << 31);
    printf("-1 << 1              = %d\n", -1 << 1);
    return 0;
}
```

| 組態 | `x << n`（n=32 執行時期） | `1u << 32`（常數） |
|---|---|---|
| gcc -O0 | 1 | 0 |
| gcc -O2 | 1 | 0 |
| clang -O0 | 1 | **114597918** |
| clang -O2 | 1 | **651849758** |

（gcc 會警告 `left shift count >= width of type [-Wshift-count-overflow]`。）

- 執行時期移 32 位元得到 1：x86 的 `shl` 只取位移量的低 5 位元（32 位元運算），32 & 31 = 0，等於沒移。arm64 的 `lslv` 取位移量除以暫存器寬度的餘數，結果也常是 x。
- 常數 `1u << 32`：gcc 決定給 0；clang 14 直接把這個值當成「未定義」，`printf` 印出的是**暫存器裡剛好留下的垃圾**，`-O0` 與 `-O2` 還不一樣。這是 UB 最真實的樣子——不是「某個合理值」，而是「沒有值」。
- `1 << 31` 與 `-1 << 1` 在這裡都印出「看起來合理」的數。GCC 手冊說它「does not use the latitude given in C99 and later to treat certain aspects of signed '<<' as undefined」——這是 **GCC 的承諾，不是 C 的保證**，Clang 沒有同樣的承諾。

UBSan 的 `-fsanitize=shift` 四個都抓得到（gcc 與 clang 訊息相同）：

```
shiftub.c:8:49: runtime error: shift exponent 32 is too large for 32-bit type 'unsigned int'
shiftub.c:9:46: runtime error: shift exponent 32 is too large for 32-bit type 'unsigned int'
shiftub.c:10:45: runtime error: left shift of 1 by 31 places cannot be represented in type 'int'
shiftub.c:11:46: runtime error: left shift of negative value -1
```

（加了 UBSan 後 clang 的常數 `1u << 32` 也變成印 0——sanitizer 改變了程式，再次提醒不要拿 sanitizer 版本推論正式版本的輸出。）

### 用算術右移取符號遮罩

有號整數算術右移「寬度減 1」位後，只剩符號位元的複製：負數變全 1（−1），非負數變 0。這是許多無分支技巧的起點：

```c
/* 負數右移的結果由實作定義；GCC 與 Clang 採算術位移，mask 因而是 0 或 -1 */
static int abs_branchless(int n)
{
    int mask = n >> (sizeof(int) * CHAR_BIT - 1);
    return (n ^ mask) - mask;
}
```

n 非負時 mask 為 0，`(n ^ 0) - 0 = n`；n 為負時 mask 為 −1，`n ^ -1` 就是 `~n`，減 −1 即加 1，得 `~n + 1 = -n`（[Ch 21](./21-number-systems.md)）。`n == INT_MIN` 時 `~n + 1` 溢位，與 `abs(INT_MIN)` 同樣是 UB。位移量寫成 `sizeof(int) * CHAR_BIT - 1` 而非 31，不寫死寬度，但仍假設 `int` 沒有填充位元。這個寫法曾是 Sun 的專利 US 6,073,150（1997 申請，2017 年期滿）。

## 設定、清除、切換與測試位元

遮罩常數寫成 `UINT32_C(1)` 而非 `1`：後者是 `int`，`1 << 31` 是有號左移溢位。

```c
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

static uint32_t bit_set(uint32_t x, unsigned n) { return x | (UINT32_C(1) << n); }
static uint32_t bit_clear(uint32_t x, unsigned n) { return x & ~(UINT32_C(1) << n); }
static uint32_t bit_toggle(uint32_t x, unsigned n) { return x ^ (UINT32_C(1) << n); }
static bool bit_test(uint32_t x, unsigned n) { return (x >> n) & 1; }

/* 依 b 設定或清除第 n 個位元，不需要分支 */
static uint32_t bit_assign(uint32_t x, unsigned n, bool b)
{
    return (x & ~(UINT32_C(1) << n)) | ((uint32_t) b << n);
}

static void print8(const char *tag, uint32_t x)
{
    printf("%-20s", tag);
    for (int i = 7; i >= 0; i--)
        printf(" %d", bit_test(x, (unsigned) i));
    printf("\n");
}

int main(void)
{
    print8("a", 0x80);
    print8("bit_set(a, 1)", bit_set(0x80, 1));
    print8("bit_clear(b, 3)", bit_clear(0xFF, 3));
    print8("bit_toggle(c, 5)", bit_toggle(0x9B, 5));
    print8("bit_assign(d, 6, 1)", bit_assign(0x10, 6, true));
    print8("bit_assign(d, 4, 0)", bit_assign(0x10, 4, false));
    return 0;
}
```

```
a                    1 0 0 0 0 0 0 0
bit_set(a, 1)        1 0 0 0 0 0 1 0
bit_clear(b, 3)      1 1 1 1 0 1 1 1
bit_toggle(c, 5)     1 0 1 1 1 0 1 1
bit_assign(d, 6, 1)  0 1 0 1 0 0 0 0
bit_assign(d, 4, 0)  0 0 0 0 0 0 0 0
```

這些函式假設 n 在 0..31。`bit_assign` 先清除再 OR 上 `b << n`，沒有 `if`。

### 為什麼不寫成巨集

網路上常見 `#define CLEARBIT(a, pos) (a &= ~(1 << pos))`。它有三個問題，第一個最容易咬人：

```c
#include <stdio.h>

#define CLEARBIT(a, pos) (a &= ~(1 << pos))
#define CLEARBIT_OK(a, pos) ((a) &= ~(1u << (pos)))

int main(void)
{
    unsigned x = 0xFF, y = 0xFF;
    unsigned i = 4;
    CLEARBIT(x, i & 7);       /* 想清第 4 位元 */
    CLEARBIT_OK(y, i & 7);
    printf("CLEARBIT:    0x%02X\n", x);
    printf("CLEARBIT_OK: 0x%02X\n", y);
    return 0;
}
```

```
CLEARBIT:    0xFF
CLEARBIT_OK: 0xEF
```

`CLEARBIT(x, i & 7)` 展開成 `~(1 << i & 7)`，`<<` 優先於 `&`，等於 `~((1 << 4) & 7) = ~0`，什麼也沒清。（`CLEARBIT(x, i + 1)` 反而碰巧正確，因為 `+` 優先於 `<<`——這種「有時對」的 bug 最難找。）另外兩個問題：`1` 是 `int`，位置 31 是 UB；巨集就地修改引數，不能用在只想取值的運算式。用 `static inline` 函式，型別有檢查、`-O2` 下內嵌、沒有成本。巨集的陷阱在 [Ch 27](./27-preprocessor-basics.md) 會系統整理。

### 整數當集合

第 i 個位元表示元素 i 是否在集合中：`|` 聯集、`&` 交集、`& ~` 差集、`^` 對稱差。Linux 的 `cpumask`、`select()` 的 `fd_set`、檔案權限位元都這樣表示。對稱差的定義 `(¬x ∧ y) ∨ (x ∧ ¬y)` 在 C 裡寫成 `(x ^ y) == ((~x & y) | (x & ~y))`，括號不能省：**`==` 的優先序高於 `^`、`&`、`|`**。

### 2 的冪：優先序陷阱的經典

`x & (x - 1)` 清除最低位元的 1（[Ch 24](./24-bitwise-tricks.md) 詳述），結果為 0 代表原本只有一個 1：

```c
static bool is_pow2_bad(uint32_t x) { return x & (x - 1) == 0; }
static bool is_pow2(uint32_t x) { return x && !(x & (x - 1)); }
```

```
pow2.c:5:58: warning: suggest parentheses around comparison in operand of ‘&’ [-Wparentheses]
    5 | static bool is_pow2_bad(uint32_t x) { return x & (x - 1) == 0; }
         0: bad=0 good=0
         1: bad=1 good=1
         2: bad=0 good=1
         3: bad=0 good=0
         4: bad=0 good=1
         6: bad=0 good=0
         8: bad=0 good=1
2147483648: bad=0 good=1
```

錯誤版本解析成 `x & ((x - 1) == 0)`，只有 x == 1 時為真。clang 的警告更直白：「& has lower precedence than ==; == will be evaluated first」。另外 x == 0 要排除；參數要無號，因為有號的 `x - 1` 在 `INT_MIN` 時溢位。C23 `<stdbit.h>` 提供 `stdc_has_single_bit` 做同樣的判斷。

### 把資訊藏在指標的低位元

回到開頭的紅黑樹。下面是一個獨立可編譯的模型（不是核心原始碼）：

```c
#include <stdint.h>
#include <stdio.h>

#define RB_RED   0
#define RB_BLACK 1

struct rb_node {
    uintptr_t parent_color;          /* 親代節點位址 | 顏色 */
    struct rb_node *left, *right;
} __attribute__((aligned(sizeof(long))));

static struct rb_node *rb_parent(const struct rb_node *n)
{
    return (struct rb_node *) (n->parent_color & ~(uintptr_t) 3);
}
static int rb_color(const struct rb_node *n) { return n->parent_color & 1; }
static void rb_set_parent_color(struct rb_node *n, struct rb_node *p, int color)
{
    n->parent_color = (uintptr_t) p | (uintptr_t) color;
}

int main(void)
{
    static struct rb_node root, child;
    rb_set_parent_color(&child, &root, RB_BLACK);
    printf("&root          = %p (low 2 bits = %lu)\n", (void *) &root, (unsigned long) ((uintptr_t) &root & 3));
    printf("parent_color   = 0x%lx\n", (unsigned long) child.parent_color);
    printf("rb_parent      = %p, color = %s\n", (void *) rb_parent(&child),
           rb_color(&child) == RB_BLACK ? "black" : "red");
    printf("sizeof(struct rb_node) = %zu\n", sizeof(struct rb_node));
    return 0;
}
```

```
&root          = 0x5ee91a7af040 (low 2 bits = 0)
parent_color   = 0x5ee91a7af041
rb_parent      = 0x5ee91a7af040, color = black
sizeof(struct rb_node) = 24
```

（位址每次執行不同。）對齊保證低位元為 0（[Ch 16](./16-alignment-and-padding.md)），這兩個位元就可以拿來放旗標。指標轉整數的結果在 C11 6.3.2.3 §6 是實作定義，這個手法以平坦定址空間為前提；核心可以這樣做，可攜程式碼要三思。

## 以遮罩取代分支

`bit_assign` 用遮罩取代 `if`。同一手法可以推廣成一組不經分支的選擇運算，共同骨架是：

```
a ^ ((a ^ b) & mask)
  mask 為 1 的位元：a ^ a ^ b = b
  mask 為 0 的位元：a ^ 0     = a
```

只要造出適當的 mask，就能完成合併、條件設定、條件取負、條件交換。Bit Twiddling Hacks 與 Hacker's Delight 的原始版本多以 `int` 撰寫，部分依賴有號右移為算術位移、甚至依賴有號溢位回繞；原文把它們改寫成 `uint32_t`，有號結果以無號運算求得後再轉型：

```c
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

/* 二數異號時，XOR 的符號位元為 1 */
static bool opposite_signs(int32_t x, int32_t y) { return (x ^ y) < 0; }

/* mask 為 1 的位元取自 b，為 0 的位元取自 a */
static uint32_t merge(uint32_t a, uint32_t b, uint32_t mask) { return a ^ ((a ^ b) & mask); }

/* f 為真時設定 mask 標示的位元，否則清除 */
static uint32_t set_or_clear(uint32_t x, uint32_t mask, bool f)
{
    return x ^ ((-(uint32_t) f ^ x) & mask);
}

/* f 為真時取負；以無號運算避免 INT32_MIN 取負的溢位 */
static int32_t negate_if(int32_t x, bool f)
{
    return (int32_t) (((uint32_t) x ^ -(uint32_t) f) + f);
}

/* f 為真時交換 *x 與 *y；x 與 y 指向同一物件時 d 為 0，值不變 */
static void swap_if(uint32_t *x, uint32_t *y, bool f)
{
    uint32_t d = (*x ^ *y) & -(uint32_t) f;
    *x ^= d;
    *y ^= d;
}

/* 把 x 的低 b 位元 (1 <= b <= 32) 視為 b 位元的二補數，擴充為 32 位元 */
static int32_t sign_extend(uint32_t x, unsigned b)
{
    uint32_t m = UINT32_C(1) << (b - 1);
    x &= m | (m - 1);
    return (int32_t) ((x ^ m) - m);
}

/* 以 -(a < b) 當遮罩的 min/max：不需要 a - b 不溢位 */
static int32_t min_bl(int32_t a, int32_t b) { return b ^ ((a ^ b) & -(int32_t) (a < b)); }
static int32_t max_bl(int32_t a, int32_t b) { return a ^ ((a ^ b) & -(int32_t) (a < b)); }

static int abs_branchless(int n)   /* 見上一節 */
{
    int mask = n >> (sizeof(int) * CHAR_BIT - 1);
    return (n ^ mask) - mask;
}

static uint64_t s = 88172645463325252u;
static uint64_t rnd(void) { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return s; }

int main(void)
{
    printf("%d %d\n", opposite_signs(-3, 5), opposite_signs(-3, -5));
    printf("%08X\n", (unsigned) merge(0x12345678, 0xABCDEF01, 0xFFFF0000));
    printf("%08X %08X\n", (unsigned) set_or_clear(0xF0F0F0F0, 0x0000FFFF, true),
           (unsigned) set_or_clear(0xF0F0F0F0, 0x0000FFFF, false));
    printf("%d %d %d\n", negate_if(42, true), negate_if(42, false), negate_if(INT32_MIN, true));
    uint32_t p = 1, q = 2;
    swap_if(&p, &q, true);
    printf("%u %u\n", (unsigned) p, (unsigned) q);
    printf("%d %d %d\n", sign_extend(0x1F, 5), sign_extend(0x0F, 5), sign_extend(0x80, 8));
    printf("%d %d %d\n", abs_branchless(-42), abs_branchless(INT_MAX), abs_branchless(INT_MIN + 1));
    printf("%d %d\n", min_bl(INT32_MIN, INT32_MAX), max_bl(INT32_MIN, INT32_MAX));

    unsigned long bad = 0;
    for (long k = 0; k < 10000000; k++) {   /* 與直觀寫法逐一比對 */
        uint32_t a = (uint32_t) rnd(), b = (uint32_t) rnd(), m = (uint32_t) rnd();
        bool f = rnd() & 1;
        unsigned w = 1 + (unsigned) (rnd() % 32);
        bad += merge(a, b, m) != ((a & ~m) | (b & m));
        bad += set_or_clear(a, m, f) != (f ? (a | m) : (a & ~m));
        bad += negate_if((int32_t) a, f) != (int32_t) (f ? -(int64_t) (int32_t) a : (int32_t) a);
        uint32_t sa = a, sb = b;
        swap_if(&sa, &sb, f);
        bad += f ? (sa != b || sb != a) : (sa != a || sb != b);
        swap_if(&sa, &sa, true);
        bad += sa != (f ? b : a);
        bad += opposite_signs((int32_t) a, (int32_t) b) != (((int32_t) a < 0) != ((int32_t) b < 0));
        int64_t low = a & (uint32_t) ((UINT64_C(1) << w) - 1);
        int64_t want = (low >> (w - 1)) ? low - (INT64_C(1) << w) : low;
        bad += sign_extend(a, w) != want;
        int32_t x = (int32_t) a, y = (int32_t) b;
        bad += min_bl(x, y) != (x < y ? x : y);
        bad += max_bl(x, y) != (x < y ? y : x);
        if (x != INT_MIN)
            bad += abs_branchless(x) != (x < 0 ? -x : x);
    }
    printf("mismatches: %lu\n", bad);
    return 0;
}
```

先印幾組範例，再以 xorshift 產生 10⁷ 組隨機輸入，逐一與直觀寫法（`if` 或 `?:`）比對：

```
1 0
ABCD5678
F0F0FFFF F0F00000
-42 42 -2147483648
2 1
-1 15 -128
42 2147483647 2147483647
-2147483648 2147483647
mismatches: 0
```

各函式的推導：

- **`opposite_signs`**：二補數負數的最高位元是 1，恰有一數為負時 XOR 的最高位元為 1，結果為負。
- **`set_or_clear`**：骨架中的 b 換成 `-(uint32_t)f`——f 為真時是全 1（設定），為假時是 0（清除）。`-(uint32_t)f` 是「把 bool 變成全 0 或全 1 遮罩」的標準寫法，在無號中進行，沒有溢位顧慮。
- **`negate_if`**：f 為真時 `x ^ 全1 + 1 = ~x + 1 = -x`。以無號運算求值，`INT32_MIN` 取負得到 `INT32_MIN`（第四行第三個數）而非 UB。
- **`swap_if`**：d 在 f 為真時是 `*x ^ *y`，兩邊各 XOR d 就互換；x 與 y 指向同一物件時 d 為 0，值不變。對照 [Ch 24](./24-bitwise-tricks.md) 的 XOR swap：那個寫法在同一物件時會歸零。X25519 的常數時間 `cswap`（RFC 7748）就是這個骨架。
- **`sign_extend`**：設 b 位元欄位的最高位元為 t、其餘為 r，值為 `t·2^{b−1} + r`。`x ^ m` 把最高位元翻成 1−t，再減 `m = 2^{b−1}`，得 `r − t·2^{b−1}`，恰為二補數解讀。`0x1F` 的 5 位元解讀為 −1，`0x0F` 為 15。

### 寫錯的符號擴充：隨最佳化等級改變答案

另一種流傳的符號擴充寫法用乘法把欄位推到最高位元，再用除法拉回：

```c
#include <stdio.h>
#define M(B) (1U << (32 - B))
static int const multipliers[] = {0, M(1), M(2), M(3), M(4)};
static int const divisors[] = {1, ~M(1), M(2), M(3), M(4)};
#undef M
int main(void)
{
    unsigned b = 2;
    volatile int x = 2;
    int r = (x * multipliers[b]) / divisors[b];
    printf("%d\n", r);
    return 0;
}
```

```
gcc -O0: -2
clang -O0: -2
gcc -O2: 2
clang -O2: 2
$ gcc -O0 -fsanitize=undefined sxmul.c -o sx && ./sx
-2
sxmul.c:10:16: runtime error: signed integer overflow: 1073741824 * 2 cannot be represented in type 'int'
```

2 位元欄位 `10` 應該是 −2。`-O0` 印對了，`-O2` 印錯了：`x * multipliers[b]` 在欄位最高位元為 1 時**本來就要越過 `INT_MAX`**，這是有號溢位，編譯器在 `-O2` 依「不會溢位」把乘除抵銷了（[Ch 22](./22-integer-overflow.md) 的 `x*2/2` 同一回事）。**答案隨最佳化等級改變，是 UB 最典型的症狀**。

### 手寫無分支不一定比較好

```c
int32_t min_bl(int32_t a, int32_t b) { return b ^ ((a ^ b) & -(int32_t) (a < b)); }
int32_t min_if(int32_t a, int32_t b) { return a < b ? a : b; }
```

```
min_bl:                      min_if:
  xorl  %eax, %eax             cmpl   %edi, %esi
  cmpl  %esi, %edi             movl   %edi, %eax
  setl  %al                    cmovle %esi, %eax
  xorl  %esi, %edi             ret
  negl  %eax
  andl  %edi, %eax
  xorl  %esi, %eax
  ret
```

gcc 把 `?:` 編成條件搬移 `cmov`——本來就沒有分支，而且只有 3 道指令。手寫的遮罩版本 7 道。**無分支技巧的價值在於：編譯器不保證產生 `cmov` 的場合（例如需要常數時間的密碼學程式碼）、SIMD 程式碼、或沒有條件搬移的架構**，而不是一般程式的效能。動手前先看組合語言。

## ASCII 的編碼與控制鍵

RFC 20 收錄的 1968 年版 ASCII 把 128 個字元分成四段，每段 32 個：

```
  0x00–0x1F  控制字元          000 xxxxx
  0x20–0x3F  空白、數字、標點  001 xxxxx / 011 xxxxx
  0x40–0x5F  @、大寫字母、[\]^_ 100 xxxxx
  0x60–0x7F  `、小寫字母、{|}~DEL 110 xxxxx / 111 xxxxx

  'A' = 0x41 = 100 0001
  'a' = 0x61 = 110 0001     ← 只差第 5 個位元（0x20）
  ' ' = 0x20 = 010 0000     ← 空白恰好就是那個位元
  '_' = 0x5F = 101 1111     ← 除了 0x20 以外的低 7 位元全是 1
```

這不是巧合，而是**刻意的設計**：大小寫只差一個位元，讓機械式電傳打字機的 Shift 鍵可以用翻轉一個位元實作。所以：

```c
#include <stdio.h>

/* ASCII 字母不分大小寫地落在 0x61 至 0x7A：先設定第 5 個位元再比較範圍 */
static int is_ascii_letter(int c)
{
    return (unsigned) (c | 0x20) - 'a' < 26;
}

int main(void)
{
    printf("'a'|' '=%c 'A'|' '=%c 'a'&'_'=%c 'A'&'_'=%c 'a'^' '=%c 'A'^' '=%c\n",
           'a' | ' ', 'A' | ' ', 'a' & '_', 'A' & '_', 'a' ^ ' ', 'A' ^ ' ');
    printf("'@'|' '=%c  '['^' '=%c  '1'&'_'=0x%02X\n", '@' | ' ', '[' ^ ' ', '1' & '_');

    for (const char *p = "Hello, World"; *p; p++)
        putchar(is_ascii_letter(*p) ? *p ^ 0x20 : *p);
    putchar('\n');

    for (const char *p = "Hello, World"; *p; p++)  /* 錯誤示範：不檢查就切換 */
        printf("%02X ", (unsigned char) (*p ^ 0x20));
    putchar('\n');

    for (const char *p = "@GHJKLM[?"; *p; p++)
        printf("Ctrl-%c = 0x%02X\n", *p, *p & 0x1F);
    printf("'7' - '0' = %d, '7' & 0x0F = %d\n", '7' - '0', '7' & 0x0F);
    return 0;
}
```

```
'a'|' '=a 'A'|' '=a 'a'&'_'=A 'A'&'_'=A 'a'^' '=A 'A'^' '=a
'@'|' '=`  '['^' '={  '1'&'_'=0x11
hELLO, wORLD
68 45 4C 4C 4F 0C 00 77 4F 52 4C 44
Ctrl-@ = 0x00
Ctrl-G = 0x07
Ctrl-H = 0x08
Ctrl-J = 0x0A
Ctrl-K = 0x0B
Ctrl-L = 0x0C
Ctrl-M = 0x0D
Ctrl-[ = 0x1B
Ctrl-? = 0x1F
'7' - '0' = 7, '7' & 0x0F = 7
```

- `| ' '` 轉小寫、`& '_'` 轉大寫、`^ ' '` 切換——**只對字母有意義**：`'@' | ' '` 變成反引號、`'[' ^ ' '` 變成 `{`、`'1' & '_'` 變成控制字元 0x11。
- 第四行是「不檢查就切換」的下場：逗號 `0x2C` 變成 form feed `0x0C`，空白 `0x20` 變成 **NUL `0x00`**——字串被截斷了。
- `is_ascii_letter`：`| 0x20` 把大寫變小寫，轉無號後減 `'a'`，小於 `'a'` 的字元減完回繞成很大的無號數，**一次比較同時檢查上下界**。先轉無號也讓減法不會有號溢位。這個「範圍檢查化為一次無號比較」的手法，在 [Ch 32](./32-tricks-syntax-and-resources.md) 會再看到。
- 數字 `'0'`–`'9'` 是 `0x30`–`0x39`，低 4 位元就是數值，所以 `'7' & 0x0F == 7`。但可攜的寫法是 `c - '0'`：C11 5.2.1 §3 **只保證數字字元連續**，字母沒有這個保證。EBCDIC 的 `'I'` 是 `0xC9`、`'J'` 是 `0xD1`，中間有空洞。本節的技巧全部以 ASCII 為前提；要可攜，用 `<ctype.h>` 的 `toupper`/`tolower`。

### Ctrl 鍵其實是在清位元

終端機的 Ctrl 鍵讓 `0x40`–`0x5F` 那一段的字元只保留低 5 位元：`'H'`（100 1000）→ `000 1000` = backspace，`'J'` → line feed，`'M'` → carriage return，`'['` → `0x1B` = ESC（所以在 vim 裡 Ctrl-[ 等於 Esc）。更精確地說，終端機做的是**翻轉第 6 個位元（`^ 0x40`）**：對大寫字母與 `@[\]^_` 而言它等於 `& 0x1F`，但對 `'?'`（0x3F）而言，`^ 0x40` 得到 `0x7F`（DEL）——這就是 Ctrl-? 在終端機上送出 DEL 的原因；上面程式用 `& 0x1F` 算出的 `0x1F` 並不是終端機的實際行為。

這解釋了一個 Unix 古老謎題：在某些終端機上按 Backspace 會顯示 `^H` 而不是刪除。tty 的 ERASE 字元（termios 的 `VERASE`）若設為 DEL（0x7F），而 Backspace 鍵送出 0x08（Ctrl-H），canonical 模式的 line discipline 不認得它，就以 `^H` 回顯；反之則顯示 `^?`。

再往前追：Lear Siegler ADM-3A 終端機沒有獨立方向鍵，使用手冊列出 Ctrl-H 左、Ctrl-J 下、Ctrl-K 上、Ctrl-L 右，H/J/K/L 鍵帽上就印著箭頭。Bill Joy 在柏克萊用 ADM-3A 寫 `vi`，於是 `h j k l` 成了 vi 與 Vim 的方向鍵。**一個位元的設計，一路影響到今天的編輯器肌肉記憶。**

## 對比與取捨

| 需求 | 位元欄位 `struct { unsigned a:3; }` | 遮罩 + 位移 | `unsigned char *` 逐位元組 |
|---|---|---|---|
| 位元位置可控 | 否（實作定義） | 是 | 是（但與 endianness 綁定） |
| 可讀性 | 最好 | 中，靠命名常數補 | 差 |
| 可攜到硬體／檔案格式 | 不行 | 可以 | 要處理位元組順序 |
| 原子操作 | 不行（不能取欄位位址） | 可以（`atomic_fetch_or`） | 可以 |
| 適用 | 只在本程式內使用的旗標打包 | 暫存器、封包、檔案格式、核心 | 序列化 |

| 寫法 | 何時用 | 何時不要用 |
|---|---|---|
| `if` / `?:` | 預設；編譯器常產生 `cmov` | 需要常數時間（密碼學） |
| 遮罩無分支 | 常數時間、SIMD、分支難預測的熱迴圈 | 可讀性重要、編譯器已產生 `cmov` |

## 踩雷集錦

1. **「位元運算跟 endianness 有關」**：不對。`x & 0xFF`、`x >> 24` 作用在值上，任何平台都一樣。只有透過 `unsigned char *` 或 `memcpy` 看記憶體時才與位元組順序有關。
2. **「`1 << 31` 就是 `0x80000000`」**：`1` 是 `int`，`1 << 31` 是有號左移溢位，UB。寫 `1u << 31` 或 `UINT32_C(1) << 31`。64 位元時 `1 << 40` 更糟——`int` 只有 32 位元，要寫 `1ull << 40`。
3. **「移 32 位元會得到 0」**：位移量 ≥ 寬度是 UB。x86 執行時期得到原值（只取低 5 位元），常數時 clang 可能給垃圾。需要「移 0 到 32 位元」時要特判，或拆成兩次位移。
4. **「`x % 2 == 1` 判斷奇數」**：負的奇數得 −1，判斷失敗。用 `x & 1` 或 `x % 2 != 0`。
5. **「`-3 >> 1` 等於 `-3 / 2`」**：前者 −2（向負無限大），後者 −1（向零）。把除以 2 的冪手動改成位移，對負數就是錯的——讓編譯器去換。
6. **「`x & mask == 0` 檢查位元」**：`==` 優先於 `&`。所有位元運算與比較混用時一律加括號，`-Wparentheses`（`-Wall` 內含）會幫你。
7. **「`'a' ^ 0x20` 可以轉換所有字元的大小寫」**：只對字母；空白會變 NUL。先檢查是不是字母，或用 `toupper`。

## 進階：再往深一層

**QEMU 的惰性旗標計算。** QEMU 以 TCG 模擬 x86 時，不在每道指令後計算 EFLAGS，而是只記錄一個運算元（`CC_SRC`）、結果（`CC_DST`）與運算種類（`CC_OP`），真正需要時才算。`ADD_COUT_VEC` 用 `((r ^ a) & (a ^ b)) ^ b` 一次算出加法 a + b = r 在**每個位元**的進位：每個位元的進位是 a、b 與進位輸入的多數決；a 與 b 不同時進位輸入等於 ¬r，相同時進位與進位輸入無關。之後把進位向量左旋 1 位，讓最高位的進位落在 CF、位元 3 的進位落在 AF。這是本章「遮罩」與下一章「旋轉」的漂亮結合，值得對照 `target/i386/cpu.h` 原始碼讀一遍。

**FFmpeg 的 `av_clip_uint8_c`**：不比較大小，以 `a & ~0xFF` 判斷是否超出 0..255，超出時用 `(~a) >> 31` 得到 0（a 為負）或 −1（a 大於 255），轉 `uint8_t` 即 0 或 255。它依賴 32 位元 `int` 與算術右移（實作定義）——在 FFmpeg 支援的平台上成立，換平台要重新確認。

**C23 的 `<stdbit.h>`** 標準化了一批位元函式（`stdc_has_single_bit`、`stdc_leading_zeros`、`stdc_count_ones`…），型別泛型且對 0 有定義，[Ch 24](./24-bitwise-tricks.md) 會實測。

**常見面試題**：「不用比較運算子判斷兩個 `int` 是否異號」（`(x ^ y) < 0`）、「判斷 2 的冪」、「清除最低位元的 1」、「不用暫存變數交換」（注意同一物件）、「`unsigned` 與 `int` 比較的結果」。本章的每一個都要能說出**它在什麼條件下會錯**，這才是面試官真正想聽的。延伸到 [`../c_interview/26-branchless.md`](../c_interview/26-branchless.md) 有更多無分支題型。

## 動手練習

1. 寫 `uint32_t extract_bits(uint32_t x, unsigned lo, unsigned len)` 取出第 lo 起的 len 個位元，要求 len 可以是 32（提示：遮罩 `(1u << len) - 1` 在 len == 32 時是 UB，想辦法繞過）。用 10⁶ 組隨機輸入對照逐位元的版本。
2. 把 `sizeof_loop.c` 修好兩種寫法：一種保持 `int`、一種改成 `size_t` 倒數迴圈（提示：`for (size_t i = n; i-- > 0; )`）。
3. 對 `CLEARBIT(x, i & 7)` 執行 `gcc -E`，親眼看展開結果，再把 `i & 7` 換成 `i | 1`、`i == 4 ? 4 : 0`，預測每個結果再驗證。
4. 寫一個不用 `if` 的 `clamp_u8(int v)`，把任意 `int` 夾到 0..255（參考進階的 `av_clip_uint8_c`），窮舉 −70000..70000 驗證；再用 `-O2 -S` 比較它和 `v < 0 ? 0 : v > 255 ? 255 : v` 的組合語言。
5. 在終端機執行 `stty -a`，找出 `erase` 的設定；用 `cat -v` 按 Backspace、Ctrl-H、Ctrl-?，對照本章 Ctrl 鍵的說明。

## 本章重點整理

- 位元運算作用在值的位元表示上，與記憶體位元組順序無關；可攜的位元運算一律用無號型別與精確寬度型別。
- 有號與無號混用時，有號的一方被轉成無號：`-1 < 0U` 為假；`strlen(s) > -1` 恆為假。
- 位移有三種 UB（量為負、量 ≥ 寬度、有號左移放不下）與一種實作定義（負數右移，GCC/Clang 為算術右移）；C23 沒有改變這些規則。
- 右移向負無限大取整、除法向零截斷，所以有號 `/2` 比 `>>1` 多三道指令，`x % 2 == 1` 對負數錯。
- `|` 設定、`& ~` 清除、`^` 切換、`(x >> n) & 1` 測試；遮罩用 `UINT32_C(1) << n`。
- `a ^ ((a ^ b) & mask)` 是無分支選擇的骨架；`-(uint32_t)f` 把 bool 變全 0 / 全 1。但編譯器常已用 `cmov`，先看組合語言。
- ASCII 大小寫差 0x20、Ctrl 鍵翻轉 0x40——這是刻意的位元設計。

## 自我檢核

- [ ] 不看筆記，能列出位移的三種未定義行為與一種實作定義行為嗎？
- [ ] 有人說「我在 big-endian 機器上 `x & 0xFF` 會拿到最高位元組」，你能說明他錯在哪裡嗎？
- [ ] 能不跑程式就說出 `2147483647U > -2147483647 - 1` 的結果並解釋嗎？為什麼要寫成 `-2147483647 - 1`？
- [ ] 能解釋為什麼 gcc 對有號 `x / 2` 產生四道指令，而 `x >> 1` 只要一道嗎？
- [ ] 能寫出 `negate_if` 並說明為什麼它對 `INT32_MIN` 不是 UB 嗎？
- [ ] 能說出至少兩個「手寫無分支程式碼不划算」的情況，以及一個「非寫不可」的情況嗎？
- [ ] 能解釋 vi 為什麼用 h/j/k/l 移動游標，並把它連回 ASCII 的位元結構嗎？

## 延伸閱讀

### 官方文件 / Spec

- **[ISO/IEC 9899 草案 N1570（C11）](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：6.5.7（位移，全文只有五段，值得逐字讀）、6.3.1.8（一般算術轉換）、6.2.6.2（整數表示）、6.7.2.1 §11（位元欄位的實作定義範圍）。
  - **注意**：C23 的位移在 [N3220](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n3220.pdf) 6.5.8，文字幾乎不變。

- **[RFC 20 — ASCII format for Network Interchange](https://www.rfc-editor.org/rfc/rfc20)**
  - **讀哪裡**：第 2 節的編碼表（以欄列排列，一眼就能看出四段 × 32 的結構）。
  - **前提知識**：無；配合 Linux 的 `man ascii` 一起看。

### 部落格 / 技術文章

- **[Bit Twiddling Hacks](https://graphics.stanford.edu/~seander/bithacks.html)** — Sean Eron Anderson（Stanford）
  - **這篇說什麼**：位元技巧的經典彙整，每條附正確性說明與貢獻者。
  - **讀哪裡**：「Detect if two integers have opposite signs」「Conditionally set or clear bits without branching」「Conditionally negate a value」「Sign extending」幾節，就是本章「以遮罩取代分支」的出處。
  - **注意**：原文多用 `int`，依賴實作定義行為，使用前要像本章一樣改寫成無號。

- **[Writing TMin in C](https://csapp.cs.cmu.edu/3e/waside/waside-tmin.pdf)** — Bryant & O'Hallaron（CS:APP Web Aside）
  - **這篇說什麼**：為什麼 `-2147483648` 不是 `int`，C90 與 C99 選擇常數型別的規則表。
  - **讀哪裡**：Figure 1 與 Figure 2，三頁讀完。
  - **為什麼值得讀**：本章比較表最後兩行 `sizeof` 的完整解釋。

- **[解讀計算機編碼](https://hackmd.io/@sysprog/binary-representation)** — 黃敬群（jserv）
  - **讀哪裡**：「不需要分支的設計」「常數時間實作」「需要區分正負號的指令」三節。
  - **和本章的關聯**：補充無分支 `min`/`max` 的減法版本、以及各指令集區分有號無號的完整對照表。

### 書籍

- **《Hacker's Delight》2nd ed.** — Henry S. Warren（Addison-Wesley，2012）
  - **這本書的定位**：位元技巧的「那本書」，每個技巧都有證明。
  - **讀哪幾章**：Chapter 2「Basics」（2-1 到 2-9：最低位元操作、De Morgan、絕對值、符號擴充）與 10-1 節（有號除以 2 的冪）。
  - **前提知識**：本章與 [Ch 21](./21-number-systems.md)。

- **《Computer Systems: A Programmer's Perspective》3rd ed.** — Bryant & O'Hallaron（2015）
  - **讀哪幾章**：2.1.6–2.1.9（位元運算、位移）、2.2.4–2.2.6（有號無號轉換、符號擴充）；Figure 2.19 是本章比較表的出處。

基礎的開關會用了，下一章把它們組合成真正的「技巧」：怎麼一道運算找出最低位元的 1、怎麼數 1 的個數、怎麼不用迴圈反轉位元，以及編譯器何時認得出你的技巧、換成一道硬體指令。

→ [Ch 24 bitwise 技巧：clz、popcount、無分支與 Morton 碼](./24-bitwise-tricks.md)
