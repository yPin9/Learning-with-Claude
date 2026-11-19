# Ch 24 — bitwise 技巧：clz、popcount、無分支與 Morton 碼

> **目標**：掌握一組經典位元技巧——最低位元的 1、ctz/clz、popcount 與同位、位元旋轉、位元反轉、Morton 碼、以乘法平行處理位元、畫素運算、XOR 加解密——每一個都能推導、能寫出窮舉或隨機驗證、知道編譯器何時會把它換成一道硬體指令，以及它在什麼條件下會錯。

> **對應原文**：[你所不知道的 C 語言：bitwise 操作篇](https://hackmd.io/@sysprog/c-bitwise) 的「最低位元的 1 與 trailing zeros」「以乘法與取餘數平行處理位元」「計算 1 的個數與同位」「位元旋轉」「位元反轉」「位元交錯」「影像畫素的位元操作」等節，以及[數值系統篇](https://hackmd.io/@sysprog/c-numerics)的「Count Leading Zero」「省去迴圈」「數值語意與編譯器最佳化」「加解密的應用」等節

> **環境**：WSL Ubuntu 22.04 x86_64（AMD Ryzen 7 5700G，Zen 3，支援 BMI1/BMI2/LZCNT/POPCNT），gcc 11.4、clang 14。C23 `<stdbit.h>` 段落在 Docker `gcc:14` 執行。窮舉 2³² 個輸入的驗證程式以 `-O2` 執行約 10 秒到 100 秒，本章會標出實測時間。

> 本章所有技巧都以 [Ch 23](./23-bitwise-basics.md) 的「無號型別、`UINT32_C(1) << n`、位移量 < 寬度」三條守則為前提。

## 為什麼需要這個？

位元技巧不是炫技。Linux 的 RT 排程器靠「找最低位元的 1」在常數時間挑出下一個工作；記憶體配置器靠 clz 把大小對應到 size class；資料庫與網路程式的 bitmap index、Bloom filter 每秒做數億次 popcount；SHA-256、ChaCha20、SipHash 的每一輪都是旋轉；FFT 開頭要做位元反轉重排；空間索引與 GPU 紋理用 Morton 碼讓二維資料在記憶體中保持局部性。

歷史上這些技巧先是軟體寫法（Hacker's Delight、Bit Twiddling Hacks 收錄的都是 1960–2000 年代的手法），後來處理器紛紛加了專用指令：x86 的 `bsf`/`bsr`（80386）、`popcnt`（SSE4.2 時代）、`lzcnt`/`tzcnt`（ABM/BMI1）、`pdep`/`pext`（BMI2），arm 的 `clz`、`rbit`、`cnt`。今天的問題變成：**什麼時候寫軟體版本、什麼時候用 `__builtin_*`、什麼時候編譯器會自動認出你的寫法？** 而且每個內建函式都有自己的陷阱（`__builtin_clz(0)` 是未定義）。這一章逐一回答。

## 先建立直覺

大部分位元技巧都建立在三個觀念上：

```
1. 借位與進位只往一個方向傳
   x     = ...a 1 0 0 0        最低位元的 1 之下有 k 個 0
   x - 1 = ...a 0 1 1 1        借位把那個 1 變 0、下面的 0 全變 1
   -x    = ...ā 1 0 0 0        ~x + 1：進位穿過 k 個 1，停在原位置

2. 暫存器是一排並列的小欄位（SWAR：SIMD within a register）
   [ 2 ][ 1 ][ 1 ][ 2 ]   一道加法同時加四個欄位，只要欄位之間不會進位

3. 乘法 = 很多個「位移後相加」
   v × 0x01010101 = v + (v<<8) + (v<<16) + (v<<24)   → 複製，或把各位元組加到最高位元組
```

記住這三張圖，後面每個技巧都是它們的變形。

## 最低位元的 1

```c
#include <stdint.h>
#include <stdio.h>

static void b8(uint8_t v) { for (int i = 7; i >= 0; i--) putchar('0' + ((v >> i) & 1)); }

int main(void)
{
    uint8_t x = 0x68;  /* 0110 1000 */
    struct { const char *e; uint8_t v; } t[] = {
        {"x", x}, {"x - 1", (uint8_t) (x - 1)}, {"x & (x - 1)", (uint8_t) (x & (x - 1))},
        {"~x", (uint8_t) ~x}, {"-x", (uint8_t) -x}, {"x & -x", (uint8_t) (x & -x)},
        {"x ^ (x - 1)", (uint8_t) (x ^ (x - 1))}, {"x | (x - 1)", (uint8_t) (x | (x - 1))},
        {"~x & (x - 1)", (uint8_t) (~x & (x - 1))},
    };
    for (size_t i = 0; i < sizeof t / sizeof t[0]; i++) {
        printf("%-14s ", t[i].e);
        b8(t[i].v);
        printf("  (0x%02X)\n", t[i].v);
    }
    return 0;
}
```

```
x              01101000  (0x68)
x - 1          01100111  (0x67)
x & (x - 1)    01100000  (0x60)   清除最低位元的 1
~x             10010111  (0x97)
-x             10011000  (0x98)
x & -x         00001000  (0x08)   只留下最低位元的 1
x ^ (x - 1)    00001111  (0x0F)   最低位元的 1 及其下全為 1
x | (x - 1)    01101111  (0x6F)   把最低位元的 1 之下填滿
~x & (x - 1)   00000111  (0x07)   只留最低位元的 1 之下的 0（變成 1）
```

（右欄註解是我們加的。）`x & -x` 的推導：`-x = ~x + 1`，`~x` 在最低位元的 1 以下是 k 個 1、那個位置是 0；加 1 的進位穿過 k 個 1，停在原位置把它變成 1，更高的位元維持反轉。於是 `x & -x` 只有那一位相同。以 `uint32_t` 計算時 `-x` 是模 2³² 運算，沒有溢位顧慮。這張表在 Hacker's Delight 2-1 節有完整版本。

**`~x & (x - 1)` 的值 + 1 就是 2^ctz**，`popcount(~x & (x - 1))` 就是 ctz——各種位元掃描之間都能互相換算。

## ctz：數尾端的 0

**trailing zeros（ctz）**：最低位元的 1 之下有幾個 0，等於最低位元的 1 的位置。`x & -x` 把問題化成「2 的冪是 2 的幾次方」，以下三種寫法各自回答：

```c
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* 0x077CB531 的二進位 0000 0111 0111 1100 1011 0101 0011 0001
 * 左移 0 至 31 位元後，最高 5 位元恰好走過 0 至 31 各一次 */
#define DEBRUIJN32 0x077CB531u

static unsigned char ctz_pos[32];

static void init_ctz(void)
{
    for (unsigned i = 0; i < 32; i++)
        ctz_pos[(uint32_t) (DEBRUIJN32 << i) >> 27] = (unsigned char) i;
}

/* v 不可為 0：v & -v 只留最低位元的 1，乘法相當於左移 */
static unsigned ctz_debruijn(uint32_t v)
{
    return ctz_pos[(uint32_t) ((v & -v) * DEBRUIJN32) >> 27];
}

/* 逐一決定答案的 5 個位元；v 為 0 時得 32 */
static unsigned ctz_bits(uint32_t v)
{
    unsigned c = 32;
    v &= -v;
    if (v) c--;
    if (v & 0x0000FFFF) c -= 16;
    if (v & 0x00FF00FF) c -= 8;
    if (v & 0x0F0F0F0F) c -= 4;
    if (v & 0x33333333) c -= 2;
    if (v & 0x55555555) c -= 1;
    return c;
}

/* v 不可為 0：2 的冪轉為 float 是精確的，指數欄位即位置 */
static unsigned ctz_float(uint32_t v)
{
    float f = (float) (v & -v);
    uint32_t u;
    memcpy(&u, &f, sizeof(u));
    return (u >> 23) - 127;
}

int main(void)
{
    init_ctz();
    unsigned seen = 0;
    for (unsigned i = 0; i < 32; i++)
        seen |= 1u << ((uint32_t) (DEBRUIJN32 << i) >> 27);
    printf("distinct windows: %s\n", seen == 0xFFFFFFFF ? "yes" : "no");
    printf("table:");
    for (int i = 0; i < 32; i++) printf(" %u", ctz_pos[i]);
    printf("\n%u %u %u %u\n", ctz_debruijn(0x68), ctz_bits(0x68), ctz_float(0x68), ctz_bits(0));

    unsigned long long bad = 0;
    for (uint32_t v = 1; v != 0; v++) {     /* 窮舉 1 至 2^32 - 1 */
        unsigned want = (unsigned) __builtin_ctz(v);
        bad += (ctz_debruijn(v) != want) + (ctz_bits(v) != want) + (ctz_float(v) != want);
    }
    printf("mismatches: %llu\n", bad);
    return 0;
}
```

```
distinct windows: yes
table: 0 1 28 2 29 14 24 3 30 22 20 15 25 17 4 8 31 27 13 23 21 19 16 7 26 12 18 6 11 5 10 9
3 3 3 32
mismatches: 0
```

（窮舉 2³² − 1 個輸入，約 11.8 秒。）

- **`ctz_debruijn`**：`(v & -v) * 0x077CB531` 等於把常數左移 ctz 位元。這個常數是 32 位元的 **de Bruijn 序列**：從左往右讀，任何連續 5 個位元（右側不足補 0）構成的 32 個「窗格」互不相同——程式開頭的 `seen` 檢查就是在驗證這件事。取最高 5 位元得到 0..31 的唯一編號，再查 32 項表格換回位置。表格由 `init_ctz` 依同一性質生成，不手抄 32 個數字。
- **`ctz_bits`**：`v & -v` 之後只剩一個位元；`0x0000FFFF` 問它在不在低 16 位元，`0x00FF00FF` 問它在 16 位元半部中是否落在低 8 位元，依此類推——五個檢查各決定答案的一個位元。這是二分搜尋，每一步把答案的一個位元定下來。
- **`ctz_float`**：2^k（k ≤ 31）轉成 IEEE 754 單精度是精確的，指數欄位（位元 30..23）存 k + 127。用 `memcpy` 取出位元而非 `*(uint32_t *)&f`，後者違反 C11 6.5 §7 的別名規則（[Ch 25](./25-ieee754.md) 會詳談 binary32 的欄位）。

### 硬體指令與 `__builtin_ctz(0)`

```c
int lz(unsigned x) { return __builtin_clz(x); }
int lg(unsigned x) { return 31 - __builtin_clz(x); }
int tz(unsigned x) { return __builtin_ctz(x); }
int pc(unsigned x) { return __builtin_popcount(x); }
```

| 函式 | `gcc -O2`（預設 x86-64 baseline） | `gcc -O2 -march=native`（Zen 3） |
|---|---|---|
| `lz` | `bsrl %edi,%eax` ; `xorl $31,%eax` | `lzcntl %edi,%eax` |
| `tz` | `xorl %eax,%eax` ; `rep bsfl %edi,%eax` | `tzcntl %edi,%eax` |
| `pc` | `call __popcountdi2@PLT` | `popcntl %edi,%eax` |

三件事值得注意：

1. **預設的 x86-64 baseline 沒有 `popcnt`**，gcc 只好呼叫 libgcc 的軟體版本 `__popcountdi2`。要硬體指令得加 `-mpopcnt` 或 `-march=x86-64-v2` 以上。效能敏感的程式要注意編譯旗標。
2. `rep bsf` 是刻意的編碼：在支援 BMI1 的 CPU 上這組位元組會被解碼成 `tzcnt`，在舊 CPU 上 `rep` 前綴被忽略、執行 `bsf`，兩者對非零輸入結果相同。
3. `bsr`/`bsf` 在**輸入為 0 時不定義目的暫存器**（AMD 文件說保持不變，Intel 說未定義），`lzcnt`/`tzcnt` 則回傳 32。這就是 GCC 手冊說「If x is 0, the result is undefined」的硬體根源：

```c
#include <stdio.h>
#include <stdlib.h>

__attribute__((noinline)) int lz(unsigned x) { return __builtin_clz(x); }

int main(int argc, char **argv)
{
    unsigned x = argc > 1 ? (unsigned) atoi(argv[1]) : 0;
    printf("__builtin_clz(%u) = %d\n", x, lz(x));
    return 0;
}
```

```
gcc   -O2          : __builtin_clz(0) = 541364383
gcc   -O2 -mlzcnt  : __builtin_clz(0) = 32
clang -O2          : __builtin_clz(0) = 1202848127
clang -O2 -mlzcnt  : __builtin_clz(0) = 32
```

同一份原始碼，依 `-march` 不同得到垃圾或 32。**呼叫 `__builtin_clz`/`__builtin_ctz` 前一定要排除 0**。GCC 14 與 Clang 19 起提供 `__builtin_clzg(x, fallback)`，輸入為 0 時回傳第二個參數；C23 的 `<stdbit.h>` 則直接定義 0 的結果（下文）。

## clz 與對數

對正整數 N，⌊log₂N⌋ 等於最高位元的 1 的位置，也就是 `31 - clz(N)`（32 位元）。**計算對數化為計算前導零**——配置器的 size class、雜湊表的容量取整、浮點數的正規化都用這個。

```c
#include <stdint.h>
#include <stdio.h>
#include <string.h>

int clz_iter(uint32_t x)            /* 逐次折半 */
{
    int n = 32, c = 16;
    do {
        uint32_t y = x >> c;
        if (y) { n -= c; x = y; }
        c >>= 1;
    } while (c);
    return n - (int) x;             /* 結束時 x 為 0（輸入為 0）或 1 */
}

int clz_bsearch(uint32_t x)         /* 以比較做二分搜尋 */
{
    if (x == 0) return 32;
    int n = 0;
    if (x <= 0x0000FFFF) { n += 16; x <<= 16; }
    if (x <= 0x00FFFFFF) { n += 8; x <<= 8; }
    if (x <= 0x0FFFFFFF) { n += 4; x <<= 4; }
    if (x <= 0x3FFFFFFF) { n += 2; x <<= 2; }
    if (x <= 0x7FFFFFFF) { n += 1; }
    return n;
}

/* 先把最高位的 1 往下擴散成全 1，再做 popcount：clz = 32 - popcount */
int clz_smear(uint32_t x)
{
    x |= x >> 1; x |= x >> 2; x |= x >> 4; x |= x >> 8; x |= x >> 16;
    x = x - ((x >> 1) & 0x55555555);
    x = (x & 0x33333333) + ((x >> 2) & 0x33333333);
    x = (x + (x >> 4)) & 0x0F0F0F0F;
    return 32 - (int) ((x * 0x01010101) >> 24);
}

static const int tab32[32] = {
     0,  9,  1, 10, 13, 21,  2, 29, 11, 14, 16, 18, 22, 25,  3, 30,
     8, 12, 20, 28, 15, 17, 24,  7, 19, 27, 23,  6, 26,  5,  4, 31,
};

int log2_32(uint32_t value)         /* value > 0：擴散成全 1 後以 de Bruijn 乘法查表 */
{
    value |= value >> 1; value |= value >> 2; value |= value >> 4;
    value |= value >> 8; value |= value >> 16;
    return tab32[(uint32_t) (value * 0x07C4ACDD) >> 27];
}

/* v 不可為 0：uint32_t 轉為 double 是精確的，指數欄位即最高位元的 1 的位置 */
static unsigned ilog2_double(uint32_t v)
{
    double d = (double) v;
    uint64_t u;
    memcpy(&u, &d, sizeof(u));
    return (unsigned) (u >> 52) - 1023;
}

static unsigned ilog2_float(uint32_t v)   /* 錯誤示範：float 只有 24 位元有效位數 */
{
    float f = (float) v;
    uint32_t u;
    memcpy(&u, &f, sizeof(u));
    return (u >> 23) - 127;
}

static const uint32_t pow10[] = {
    1, 10, 100, 1000, 10000, 100000, 1000000, 10000000, 100000000, 1000000000,
};

unsigned ilog10_clz(uint32_t n)     /* n > 0 */
{
    unsigned t = (32 - (unsigned) __builtin_clz(n)) * 1233 >> 12; /* 1233 / 4096 ≈ log10(2) */
    return t - (n < pow10[t]);
}

unsigned ilog10_scan(uint32_t n)
{
    unsigned i = 0;
    while (i < 9 && n >= pow10[i + 1]) i++;
    return i;
}

int main(void)
{
    printf("clz(0): iter=%d bsearch=%d smear=%d\n", clz_iter(0), clz_bsearch(0), clz_smear(0));
    printf("log2(1000): clz=%d debruijn=%d double=%u\n", 31 - __builtin_clz(1000u), log2_32(1000), ilog2_double(1000));
    printf("ilog2_float(0x01FFFFFF) = %u, ilog2_double = %u\n", ilog2_float(0x01FFFFFF), ilog2_double(0x01FFFFFF));
    unsigned long long bad = 0;
    uint32_t first_float_bad = 0;
    for (uint32_t v = 1; v != 0; v++) {
        int want = __builtin_clz(v);
        bad += (clz_iter(v) != want) + (clz_bsearch(v) != want) + (clz_smear(v) != want) +
               (log2_32(v) != 31 - want) + (ilog2_double(v) != 31u - (unsigned) want) +
               (ilog10_clz(v) != ilog10_scan(v));
        if (!first_float_bad && ilog2_float(v) != 31u - (unsigned) want)
            first_float_bad = v;
    }
    printf("mismatches: %llu, first float failure: 0x%08X\n", bad, first_float_bad);
    return 0;
}
```

```
clz(0): iter=32 bsearch=32 smear=32
log2(1000): clz=9 debruijn=9 double=9
ilog2_float(0x01FFFFFF) = 25, ilog2_double = 24
mismatches: 0, first float failure: 0x01FFFFFF
```

（窮舉約 61 秒。）

- **`clz_iter`**：依序嘗試右移 16、8、4、2、1 位，高半部不為 0 就保留高半部並把 n 減去移位量。結束時 x 只剩最高位的 1（或輸入為 0 時維持 0），`return n - x` 一次處理兩種情況，對所有輸入都正確。
- **`clz_smear`**：`x |= x >> 1` 等五步把最高位的 1 以下全部填滿（「擴散」），1 的個數就是 32 − clz。這把 clz 化成 popcount（下一節）。
- **`log2_32`**：同樣先擴散成 `0…01…1`，這種值只有 32 種，乘上另一個 de Bruijn 常數 `0x07C4ACDD` 後取最高 5 位元查表。
- **`ilog2_float` 的失敗**：binary32 只有 24 位元有效位數，`0x01FFFFFF`（2²⁵ − 1，25 個有效位元）恰在 2²⁵ − 2 與 2²⁵ 的正中間，在「捨入到最近、同距取偶數」的預設模式下變成 2²⁵，於是得到 25。窮舉證實這是第一個失敗的輸入。binary64 有 53 位元有效位數，任何 `uint32_t` 都放得下，所以 `ilog2_double` 正確。**用浮點做位元技巧，一定要算清楚有效位數**。
- **`ilog10_clz`**：⌊log₁₀N⌋ 只可能是 0..9。先以 clz 得到位元數 `32 - clz`，乘 `1233/4096 ≈ 0.30103 ≈ log₁₀2` 估計位數（1233/4096 = 0.301025…，誤差 5×10⁻⁶），估計值最多大 1，再與表中一項比較修正。只用一次比較，而非最多 9 次。它假設 `unsigned int` 是 32 位元（`__builtin_clz` 的參數型別是 `unsigned int`）。

### C23 的 `<stdbit.h>`

C23 7.18 把這組運算標準化，型別泛型且對 0 有定義（以下在 Docker `gcc:14`、`-std=c23` 執行）：

```c
#include <stdbit.h>
#include <stdint.h>
#include <stdio.h>
#include <strings.h>

int main(void)
{
    static const unsigned v[] = {0, 1, 128, 129, 0x40000000u};
    for (unsigned i = 0; i < sizeof v / sizeof v[0]; i++)
        printf("%#10x: ffs %2d, first_trailing_one %2u, leading_zeros %2u, bit_width %2u, count_ones %2u\n",
               v[i], ffs((int) v[i]), stdc_first_trailing_one(v[i]),
               stdc_leading_zeros(v[i]), stdc_bit_width(v[i]), stdc_count_ones(v[i]));
    printf("leading_zeros((uint8_t) 1) = %u\n", stdc_leading_zeros((uint8_t) 1));
    printf("__builtin_clzg(0u, 32) = %d, __builtin_ctzg(0u, -1) = %d\n",
           __builtin_clzg(0u, 32), __builtin_ctzg(0u, -1));
    return 0;
}
```

```
         0: ffs  0, first_trailing_one  0, leading_zeros 32, bit_width  0, count_ones  0
       0x1: ffs  1, first_trailing_one  1, leading_zeros 31, bit_width  1, count_ones  1
      0x80: ffs  8, first_trailing_one  8, leading_zeros 24, bit_width  8, count_ones  1
      0x81: ffs  1, first_trailing_one  1, leading_zeros 24, bit_width  8, count_ones  2
0x40000000: ffs 31, first_trailing_one 31, leading_zeros  1, bit_width 31, count_ones  1
leading_zeros((uint8_t) 1) = 7
__builtin_clzg(0u, 32) = 32, __builtin_ctzg(0u, -1) = -1
```

- `stdc_leading_zeros(0)` 是型別寬度 32；**依引數型別決定寬度**，`(uint8_t)1` 得 7 而不是 31。
- `stdc_bit_width(x)` 是 `1 + ⌊log₂x⌋`（x = 0 時為 0），所以 ⌊log₂x⌋ 可寫成 `stdc_bit_width(x) - 1`，仍要自己處理 x == 0。
- POSIX 的 `ffs` 與 C23 的 `stdc_first_trailing_one` 同語意（最低位 1 的位置，從 1 起算，0 得 0），差別在 `ffs` 只收 `int`。

gcc 11 / glibc 2.35 沒有 `<stdbit.h>`。

## popcount 與同位

**population count（popcount）** 數字組中 1 的個數；**同位（parity）** 是它的奇偶。六種寫法，全部窮舉驗證：

```c
#include <stdint.h>
#include <stdio.h>

/* 每次清除最低位元的 1，迴圈次數等於 1 的個數 */
static unsigned pop_clear(uint32_t v)
{
    unsigned c = 0;
    for (; v; v &= v - 1)
        c++;
    return c;
}

/* 256 項的表格，以巨集依最高二個位元遞迴展開 */
static const unsigned char pop_table8[256] = {
#define B2(n) n, n + 1, n + 1, n + 2
#define B4(n) B2(n), B2(n + 1), B2(n + 1), B2(n + 2)
#define B6(n) B4(n), B4(n + 1), B4(n + 1), B4(n + 2)
    B6(0), B6(1), B6(1), B6(2)
#undef B2
#undef B4
#undef B6
};

static unsigned pop_table(uint32_t v)
{
    return pop_table8[v & 0xff] + pop_table8[(v >> 8) & 0xff] +
           pop_table8[(v >> 16) & 0xff] + pop_table8[v >> 24];
}

/* 在暫存器內平行加總：2、4、8 位元一組，最後以乘法加總四個位元組 */
static unsigned pop_swar(uint32_t v)
{
    v = v - ((v >> 1) & 0x55555555);
    v = (v & 0x33333333) + ((v >> 2) & 0x33333333);
    v = (v + (v >> 4)) & 0x0F0F0F0F;
    return (v * 0x01010101) >> 24;
}

/* 以 64 位元乘法複製 12 位元的片段，再以 mod 31 加總 */
static unsigned pop_mod(uint32_t v)
{
    const uint64_t rep = 0x1001001001001, pick = 0x84210842108421;
    return (unsigned) (((v & 0xfff) * rep & pick) % 0x1f +
                       (((v >> 12) & 0xfff) * rep & pick) % 0x1f +
                       ((v >> 24) * rep & pick) % 0x1f);
}

/* 對折 XOR 到 4 位元，再以 0x6996 查 16 項的同位表 */
static unsigned parity_fold(uint32_t v)
{
    v ^= v >> 16;
    v ^= v >> 8;
    v ^= v >> 4;
    return (0x6996 >> (v & 0xf)) & 1;
}

/* 先得到每 4 位元的同位，再以乘法加總到第 28 位元 */
static unsigned parity_mul(uint32_t v)
{
    v ^= v >> 1;
    v ^= v >> 2;
    v = (v & 0x11111111) * 0x11111111;
    return (v >> 28) & 1;
}

static void show_swar(uint32_t v)   /* 印出 SWAR 每一步的中間值 */
{
    uint32_t a = v - ((v >> 1) & 0x55555555);
    uint32_t b = (a & 0x33333333) + ((a >> 2) & 0x33333333);
    uint32_t c = (b + (b >> 4)) & 0x0F0F0F0F;
    printf("v      = 0x%08X\nstep 1 = 0x%08X (每 2 位元)\nstep 2 = 0x%08X (每 4 位元)\n"
           "step 3 = 0x%08X (每 8 位元)\n*0x01010101 = 0x%08X -> >>24 = %u\n",
           v, a, b, c, c * 0x01010101u, (c * 0x01010101u) >> 24);
}

int main(void)
{
    printf("%u %u %u %u\n", pop_clear(0xF00F0001), pop_table(0xF00F0001),
           pop_swar(0xF00F0001), pop_mod(0xF00F0001));
    printf("%u %u\n", parity_fold(0xF00F0001), parity_mul(0xF00F0001));
    show_swar(0xDB);
    show_swar(0xF00F0001);

    unsigned long long bad = 0;
    uint32_t v = 0;
    do {    /* 窮舉 2^32 個輸入，以編譯器內建函式為參考 */
        unsigned p = (unsigned) __builtin_popcount(v);
        bad += (pop_clear(v) != p) + (pop_table(v) != p) + (pop_swar(v) != p) +
               (pop_mod(v) != p) + (parity_fold(v) != (p & 1)) +
               (parity_mul(v) != (p & 1));
    } while (++v != 0);
    printf("mismatches: %llu\n", bad);
    return 0;
}
```

```
9 9 9 9
1 1
v      = 0x000000DB
step 1 = 0x00000096 (每 2 位元)
step 2 = 0x00000033 (每 4 位元)
step 3 = 0x00000006 (每 8 位元)
*0x01010101 = 0x06060606 -> >>24 = 6
v      = 0xF00F0001
step 1 = 0xA00A0001 (每 2 位元)
step 2 = 0x40040001 (每 4 位元)
step 3 = 0x04040001 (每 8 位元)
*0x01010101 = 0x09050101 -> >>24 = 9
mismatches: 0
```

（窮舉約 60 秒。）

### SWAR 推導

以 `0xDB = 1101 1011` 為例，`show_swar` 印出的每一步：

```
v      = 11 01 10 11          四個 2 位元欄位
step 1 = 10 01 01 10  (0x96)  每 2 位元的 1 的個數：2 1 1 2
step 2 = 0011 0011    (0x33)  每 4 位元：3 3
step 3 = 00000110     (0x06)  每 8 位元：6
```

- **第一步** `v - ((v >> 1) & 0x55555555)`：對每個 2 位元欄位 `ab`（值 2a + b），`(v >> 1) & 0x55…` 取出 a，相減得 2a + b − a = a + b，正是這個欄位的 1 的個數。比「`(v & 0x55…) + ((v >> 1) & 0x55…)`」少一道 AND。欄位之間不會借位，因為 2a + b ≥ a。
- **第二步**：`0x33333333 = 0011 0011…`，取出每個 4 位元欄位的低 2 位元與高 2 位元相加。結果最多 4，放得進 4 位元（3 位元就夠）。
- **第三步** `(v + (v >> 4)) & 0x0F0F0F0F`：先加再遮罩。每個 4 位元欄位最多 4，相加最多 8，放得進 4 位元，**不會溢出到隔壁**，所以可以省掉一次遮罩。
- **最後一步** `v * 0x01010101 >> 24`：乘法把四個位元組加總到最高位元組（直式乘法中對齊在同一欄的四項）。看 `0xF00F0001` 的例子：`0x04040001 × 0x01010101 = 0x09050101`，最高位元組 9 = 4 + 4 + 0 + 1。總和最多 32，一個位元組放得下，不會進位到更高。

**SWAR（SIMD within a register）** 的精髓就在這裡：把暫存器當成多個窄欄位，只要保證欄位之間不會進位或借位，一道加法就同時算了很多個欄位。

其他寫法：

- **`pop_clear`**：Peter Wegner 1960 年在 CACM 發表，K&R 第 2 版習題 2-9。迴圈次數等於 1 的個數，1 稀疏時快。
- **`pop_table`**：表格用巨集生成，`B2(n)` 展開成 `n, n+1, n+1, n+2`，對應最低兩位元為 00/01/10/11 時多出的 1，`B4`、`B6` 處理更高的位元對。
- **`pop_mod`**：`rep` 把 12 位元片段複製 5 份（位元 0、12、24、36、48），`pick` 每隔 5 位元挑一個位元；因為 5 與 12 互質，第 5i 個位元落在第 ⌊5i/12⌋ 份的第 5i mod 12 位，i = 0..11 恰好挑到片段的每個位元一次，且各自位於以 32 為基數的不同「位數」上。由「2⁵ ≡ 1 (mod 31)」，`% 31` 就把這些位數加總（下一節詳述）。
- **`parity_fold`**：`v ^= v >> 16` 之後，低 16 位元的同位等於原本 32 位元的同位；對折到 4 位元後，`0x6996 = 0110 1001 1001 0110` 正是 0..15 的同位表，右移取最低位元就是查表。

`__builtin_popcount` 在支援 `popcnt` 的旗標下是一道指令；原文記錄在 arm64 上 GCC 14 與 Clang 20 會把 `pop_clear` 的迴圈與 `pop_swar` **自動辨認**成 NEON 的 `cnt` + `addv`。C23 的 `stdc_count_ones` 是標準化版本。

## 以乘法與取餘數平行處理位元

popcount 的最後一步用到乘法。把乘法當成「很多個位移後相加」，可以得到三個性質：

- **複製**：v < 2^k 時，`v × (1 + 2^k + 2^{2k} + …)` 的部分積互不重疊，乘積就是把 v 每隔 k 位元寫一份。十進位的 987 × 1001001 = 987987987 是同一回事。
- **加總**：x 由四個位元組組成時，`x × 0x01010101` 的最高位元組是四個位元組的和（只要和小於 256）。
- **位數和（去九法的二進位版）**：2^k ≡ 1 (mod 2^k − 1)，所以 n 以 2^k 為基數的各位數之和 ≡ n (mod 2^k − 1)。位數和小於 2^k − 1 時，`n % (2^k − 1)` 就精確等於位數和。

```c
#include <stdint.h>
#include <stdio.h>

/* n mod (2^s - 1)，1 <= s <= 31：反覆加總以 2^s 為基數的各位數 */
static uint32_t mod_mersenne(uint32_t n, unsigned s)
{
    const uint32_t d = (UINT32_C(1) << s) - 1;
    uint32_t m = n;
    while (m > d) {
        uint32_t sum = 0;
        for (; m; m >>= s)
            sum += m & d;
        m = sum;
    }
    return m == d ? 0 : m;
}

static uint64_t s64 = 88172645463325252u;
static uint64_t rnd(void) { s64 ^= s64 << 13; s64 ^= s64 >> 7; s64 ^= s64 << 17; return s64; }

int main(void)
{
    uint32_t b = 0xAB;
    printf("replicate 0x%02X -> 0x%08X\n", b, b * UINT32_C(0x01010101));
    uint32_t x = 0x01020304;
    printf("byte sum of 0x%08X = %u\n", x, (x * UINT32_C(0x01010101)) >> 24);
    printf("987 * 1001001 = %u\n", 987u * 1001001u);
    printf("%u %u\n", (unsigned) mod_mersenne(9487, 4), 9487u % 15);

    unsigned long bad = 0;
    for (unsigned s = 1; s <= 31; s++) {
        uint32_t d = (UINT32_C(1) << s) - 1;
        for (long k = 0; k < 1000000; k++) {
            uint32_t n = k < 4 ? (uint32_t[]){0, d, d + 1, UINT32_MAX}[k] : (uint32_t) rnd();
            bad += mod_mersenne(n, s) != n % d;
        }
    }
    printf("mismatches: %lu\n", bad);
    return 0;
}
```

```
replicate 0xAB -> 0xABABABAB
byte sum of 0x01020304 = 10
987 * 1001001 = 987987987
7 7
mismatches: 0
```

9487 = 0x250F，十六進位位數和 2 + 5 + 0 + 15 = 22 = 0x16，再加一次得 1 + 6 = 7 = 9487 mod 15。位數和恰等於 d 時代表餘數為 0，所以最後要把 d 換成 0。對 s = 1..31 各 10⁶ 個輸入（含 0、d、d + 1、`UINT32_MAX` 四個邊界）都與 `%` 相同。

注意 `UINT32_C(0x01010101)`：若寫成 `b * 0x01010101` 而 b 是 `uint8_t`，兩邊都提升成 `int`，b > 127 時乘積超過 `INT_MAX`，成為有號溢位（[Ch 21](./21-number-systems.md) 的整數提升陷阱）。

### 同一招找零位元組：`strlen` 的祕密

Alan Mycroft 1987 年在 comp.lang.c 貼出的巨集，收錄於 Bit Twiddling Hacks 與 Hacker's Delight 第 6 章：

```c
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define ONES ((unsigned long) -1 / 0xFF) /* 0x0101...01 */
#define HIGHS (ONES * 0x80)              /* 0x8080...80 */
#define HASZERO(x) (((x) - ONES) & ~(x) & HIGHS)

int main(void)
{
    printf("ONES = 0x%016lx, HIGHS = 0x%016lx\n", ONES, HIGHS);
    unsigned long w1, w2, w3;
    memcpy(&w1, "ABCDEFGH", 8);
    memcpy(&w2, "ABC\0EFGH", 8);
    unsigned char b3[8] = {0x00, 0x01, 0x41, 0x41, 0x41, 0x41, 0x41, 0x41};
    memcpy(&w3, b3, 8);
    printf("\"ABCDEFGH\"      -> 0x%016lx\n", HASZERO(w1));
    printf("\"ABC\\0EFGH\"     -> 0x%016lx (first zero at byte %d)\n", HASZERO(w2), __builtin_ctzl(HASZERO(w2)) / 8);
    printf("00 01 41 .. 41 -> 0x%016lx (byte 1 falsely flagged)\n", HASZERO(w3));
    return 0;
}
```

```
ONES = 0x0101010101010101, HIGHS = 0x8080808080808080
"ABCDEFGH"      -> 0x0000000000000000
"ABC\0EFGH"     ->0x0000000080000000 (first zero at byte 3)
00 01 41 .. 41 -> 0x0000000000008080 (byte 1 falsely flagged)
```

單一位元組看 `(x − 1) & ~x & 0x80`：`x − 1` 的最高位元為 1 若且唯若 x == 0（借位得 0xFF）或 x ≥ 0x81；`~x` 的最高位元為 1 若且唯若 x < 0x80。兩者同時成立只有 x == 0。擴到整個字組時，借位會越過零位元組往高位傳，**零位元組上方、值為 0x01 的位元組也會被標記**（第三行的誤判）。但最低位的標記一定正確，所以它適合回答「有沒有零位元組」，並在 little-endian 下用 ctz 找出第一個（第二行）。glibc 稱這個運算為 `find_zero_low`，註解寫明「other bytes within the word are indeterminate」。

`strlen` 一次檢查 8 個位元組就是靠它：newlib、glibc 的通用版本、FreeBSD 都這樣做。在 C 層級，一次讀 8 個位元組可能讀過字串物件的結尾，是 UB（AddressSanitizer 會報 heap-buffer-overflow）；對齊的字組不跨分頁所以實務上不會出錯，C 程式庫因此用組合語言或特殊處理實作。

## 位元旋轉

位移把移出的位元丟掉；**位元旋轉（rotation，circular shift）** 把它們從另一側移回。C 沒有旋轉運算子，C23 `<stdbit.h>` 也沒有，要用兩次位移加一次 OR：

```
v = 0xB1         1 0 1 | 1 0 0 0 1
v << 3           1 0 0 0 1 | 0 0 0     低 5 位元移到高位
v >> 5           0 0 0 0 0 | 1 0 1     高 3 位元移到低位
OR = 0x8D        1 0 0 0 1   1 0 1     向左旋轉 3 位元
```

直觀寫法 `(v << c) | (v >> (w - c))` 在 c = 0 時右移 w 位元——[Ch 23](./23-bitwise-basics.md) 說過這是 UB。在 w 為 2 的冪的前提下，先 `c &= w - 1`，再把右移量寫成 `-c & (w - 1)`：

```c
#include <stdint.h>
#include <stdio.h>

#define DECLARE_ROTATE_T(bits, type)                       \
    static inline type rotl##bits(type v, unsigned c)      \
    {                                                      \
        const unsigned mask = (bits) - 1;                  \
        c &= mask;                                         \
        return (type) ((v << c) | (v >> (-c & mask)));     \
    }                                                      \
    static inline type rotr##bits(type v, unsigned c)      \
    {                                                      \
        const unsigned mask = (bits) - 1;                  \
        c &= mask;                                         \
        return (type) ((v >> c) | (v << (-c & mask)));     \
    }

#define DECLARE_ROTATE(bits) DECLARE_ROTATE_T(bits, uint##bits##_t)

DECLARE_ROTATE(8)
DECLARE_ROTATE(16)
DECLARE_ROTATE(32)
DECLARE_ROTATE(64)

static uint32_t rotl32_ref(uint32_t v, unsigned c)   /* 逐位元搬移的參考版本 */
{
    uint32_t r = 0;
    c %= 32;
    for (unsigned i = 0; i < 32; i++)
        r |= ((v >> i) & 1u) << ((i + c) % 32);
    return r;
}

static uint64_t s = 88172645463325252u;
static uint64_t rnd(void) { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return s; }

int main(void)
{
    printf("%02X %02X\n", (unsigned) rotl8(0x17, 1), (unsigned) rotr8(0x17, 1));
    printf("%04X %04X\n", (unsigned) rotl16(0x8001, 20), (unsigned) rotr16(0x8001, 4));
    printf("%08lX %08lX\n", (unsigned long) rotl32(0x12345678, 8),
           (unsigned long) rotr32(0x12345678, 32));
    printf("%016llX %016llX\n", (unsigned long long) rotl64(1, 63),
           (unsigned long long) rotr64(1, 1));

    unsigned long bad = 0;
    for (long k = 0; k < 10000000; k++) {
        uint32_t v = (uint32_t) rnd();
        unsigned c = (unsigned) rnd() % 100;            /* 含 0 與 >= 32 */
        bad += rotl32(v, c) != rotl32_ref(v, c);
        bad += rotr32(rotl32(v, c), c) != v;
        uint8_t b = (uint8_t) v;
        bad += rotr8(rotl8(b, c), c) != b;
    }
    printf("mismatches: %lu\n", bad);
    return 0;
}
```

```
2E 8B
0018 1800
34567812 12345678
8000000000000000 8000000000000000
mismatches: 0
```

`rotl16(0x8001, 20)` 的位移量先化簡為 20 mod 16 = 4；`rotr32(…, 32)` 化簡為 0，原值不變，都沒有 UB。輔助巨集叫 `DECLARE_ROTATE_T` 而不是 `__DECLARE_ROTATE`：雙底線開頭的識別字保留給實作（C11 7.1.3 §1）。

**為什麼 `-c & (w - 1)` 是對的**：設 `unsigned` 寬度 N，w = 2^k，c 已化簡到 [0, w−1]。無號的 −c 等於 2^N − c（c ≠ 0）或 0；w 整除 2^N，取低 k 位元就是取 mod w：

```
(-c) & (w - 1) = (2^N - c) mod w = { w - c,  1 ≤ c ≤ w-1
                                   { 0,      c = 0
```

c ≠ 0 時與直觀寫法相同；c = 0 時右移量是 0 而非 w，`v >> 0 = v`，`(v << 0) | v = v`。例：w = 8、c = 3，32 位元的 −3 是 `0xFFFFFFFD`，AND 7 得 5 = 8 − 3。

### Linux 核心的修正歷程與 UBSan 重現

Linux v4.4 之前，`rol32` 寫成 `(word << shift) | (word >> (32 - shift))`。Sasha Levin 的〈bitops.h: correctly handle rol32 with 0 byte shift〉把右移量改成 `(-shift) & 31`，說明寫到：直接檢查 `shift == 0` 會讓 GCC 不再把它辨認成旋轉指令，這個代價無法接受。v5.2 Rasmus Villemoes 的〈include/linux/bitops.h: sanitize rotate primitives〉起因於 UBSan 回報 `ror32` 確實收到位移量 0，把全部八個函式改成兩側都取遮罩的現行寫法：

```c
static inline __u32 rol32(__u32 word, unsigned int shift)
{
    return (word << (shift & 31)) | (word >> ((-shift) & 31));
}
```

提交說明還分析了窄型別：`rol16` 的舊寫法在 `word` 的第 15 位元為 1 且 shift 為 16 時，`word` 提升成 `int` 後左移 16 位元超出 `int` 範圍。用 UBSan 跑舊寫法：

```c
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

/* Linux v5.2 之前的 ror32 與 rol16 */
static uint32_t ror32_old(uint32_t word, unsigned int shift)
{
    return (word >> shift) | (word << (32 - shift));
}

static uint16_t rol16_old(uint16_t word, unsigned int shift)
{
    return (uint16_t) ((word << shift) | (word >> (16 - shift)));
}

int main(int argc, char **argv)
{
    unsigned shift = argc > 1 ? (unsigned) strtoul(argv[1], NULL, 0) : 0;
    printf("%08X\n", (unsigned) ror32_old(0x12345678, shift));
    printf("%04X\n", (unsigned) rol16_old(0x8001, 16 + shift));
    return 0;
}
```

```
$ gcc -std=c11 -O0 -fsanitize=undefined rot_ub.c -o ru && ./ru
rot_ub.c:8:36: runtime error: shift exponent 32 is too large for 32-bit type 'unsigned int'
rot_ub.c:13:30: runtime error: left shift of 32769 by 16 places cannot be represented in type 'int'
12345678
8001
```

程式仍印出「看似正確」的值：x86 的 `shl` 只取位移量低 5 位元，`word << 32` 實際是 `word << 0`；`0x8001 << 16` 在硬體上得到 `0x80010000`，OR 後截成 `uint16_t` 是 `0x8001`。這是**這次的硬體與編譯器行為，不是 C 的保證**。

### 編譯器把位移組合辨認成 `rol`

```c
#include <stdint.h>

uint32_t rotl32_naive(uint32_t x, unsigned n)  { return (x << n) | (x >> (32 - n)); }
uint32_t rotl32_masked(uint32_t x, unsigned n) { return (x << (n & 31)) | (x >> (-n & 31)); }
uint32_t rotl32_branch(uint32_t x, unsigned n)
{
    n &= 31;
    if (n == 0)
        return x;
    return (x << n) | (x >> (32 - n));
}
uint8_t rotl8_masked(uint8_t x, unsigned n) { return (uint8_t) ((x << (n & 7)) | (x >> (-n & 7))); }
```

`objdump -d` 挑出關鍵指令：

| 函式 | gcc -O0 | gcc -O2 | clang -O0 | clang -O2 |
|---|---|---|---|---|
| `rotl32_naive` | `rol` | `rol` | shl/shr/or | `rol` |
| `rotl32_masked` | and/shl/neg/shr/or | `rol` | and/shl/shr/or | `rol` |
| `rotl32_branch` | cmp/jne + `rol` | `rol`（分支消失） | cmp/jne + shl/shr/or | `rol` |
| `rotl8_masked` | and/shl/neg/or | `and $7` + `rol %cl,%al` | and/shl/or | `rol %cl,%al` |

- `-O2` 下四種寫法全部變成一道 `rol`，8 位元版本直接用 8 位元暫存器 `%al`。直觀寫法與遮罩寫法**機械碼相同，差別只在原始碼有沒有 UB**——編譯器依據的是 C 原始碼，不是最後選的指令。
- gcc 在 `-O0` 就把直觀寫法認成旋轉（辨認在前端進行），遮罩寫法則要到最佳化階段。
- 有意思的是 `rotl32_branch`：Linux v4.4 當年說「檢查 0 會讓 GCC 認不出旋轉」，**gcc 11 已經認得出來**，分支被消掉。GCC Bug 57157 記錄了這段演進：GNU Nettle 的作者 Niels Möller 2013 年回報 GCC 4.6 認不出可攜的 `-n & 31` 寫法，Jakub Jelinek 在 forwprop 階段加入 `simplify_rotate`，隨 GCC 4.9 發布。編譯器的能力會變，**當年的「效能理由」要定期重新驗證**。

旋轉在密碼學與雜湊中無所不在：Linux 的 SHA-256 以 `ror32` 定義 Σ₀ = `ror32(x,2) ^ ror32(x,13) ^ ror32(x,22)`；BLAKE2s 的 G 函式旋轉 16、12、8、7；SipHash（核心用來產生 TCP 初始序號）的 SipRound 以 `rol64` 旋轉 13、32、16、21、17、32 位元。`lib/xxhash.c` 用的是直觀寫法 `(x << r) | (x >> (32 - r))`——全檔呼叫點都傳 1..31 的常數，不會觸及 0。**直觀寫法只在位移量確定落在 [1, w−1] 時可用**。

## 位元反轉

**位元反轉（bit reversal）** 把第 i 個位元移到第 w − 1 − i 個位置。逐位元搬移要 w 次迭代；分治法交換相鄰區塊只要 log₂w 步：

```
8 位元 abcdefgh 的後三步：
原始         a b c d | e f g h
交換 nibble  e f g h | a b c d    (x & 0xF0) >> 4 | (x & 0x0F) << 4
交換位元對   g h|e f | c d|a b    (x & 0xCC) >> 2 | (x & 0x33) << 2
交換相鄰位元 h g f e   d c b a    (x & 0xAA) >> 1 | (x & 0x55) << 1
```

32 位元版本前面多兩步（交換 16 位元半部、交換位元組）。遮罩 `0xc = 1100`、`0xa = 1010`。另外三種**單一位元組**的寫法分別用查表、乘法加取餘數、純乘法：

```c
#include <stdint.h>
#include <stdio.h>

uint32_t reverse32(uint32_t n)
{
    n = ((n & 0xffff0000) >> 16) | ((n & 0x0000ffff) << 16); /* 交換二個 16 位元半部 */
    n = ((n & 0xff00ff00) >> 8) | ((n & 0x00ff00ff) << 8);   /* 交換相鄰位元組 */
    n = ((n & 0xf0f0f0f0) >> 4) | ((n & 0x0f0f0f0f) << 4);   /* 交換相鄰 nibble */
    n = ((n & 0xcccccccc) >> 2) | ((n & 0x33333333) << 2);   /* 交換相鄰位元對 */
    n = ((n & 0xaaaaaaaa) >> 1) | ((n & 0x55555555) << 1);   /* 交換奇偶位元 */
    return n;
}

uint32_t reverse32_loop(uint32_t x)
{
    uint32_t r = 0;
    for (int i = 0; i < 32; i++)
        r |= ((x >> i) & 1) << (31 - i);
    return r;
}

/* 256 項的反轉表，以巨集每次決定 2 個位元的位置 */
static const unsigned char rev_table[256] = {
#define R2(n) n, n + 2 * 64, n + 1 * 64, n + 3 * 64
#define R4(n) R2(n), R2(n + 2 * 16), R2(n + 1 * 16), R2(n + 3 * 16)
#define R6(n) R4(n), R4(n + 2 * 4), R4(n + 1 * 4), R4(n + 3 * 4)
    R6(0), R6(2), R6(1), R6(3)
#undef R2
#undef R4
#undef R6
};

/* 複製 5 份、挑出 8 個位元，再以 mod 1023 加總 10 位元一組的「位數」 */
static uint8_t rev_mod(uint8_t b)
{
    return (uint8_t) ((b * UINT64_C(0x0202020202) & UINT64_C(0x010884422010)) % 1023);
}

/* 同樣先複製再挑選，最後以乘法把各位元組加到第 32 至 39 位元 */
static uint8_t rev_mul64(uint8_t b)
{
    return (uint8_t) (((b * UINT64_C(0x80200802)) & UINT64_C(0x0884422110)) *
                      UINT64_C(0x0101010101) >> 32);
}

/* 查表版 32 位元反轉：每個位元組各自反轉，再把位元組順序對調 */
static uint32_t reverse32_table(uint32_t x)
{
    return (uint32_t) rev_table[x & 0xff] << 24 | (uint32_t) rev_table[(x >> 8) & 0xff] << 16 |
           (uint32_t) rev_table[(x >> 16) & 0xff] << 8 | rev_table[x >> 24];
}

int main(void)
{
    printf("%08X %08X\n", (unsigned) reverse32(0x12345678), (unsigned) reverse32_table(0x12345678));
    printf("%02X %02X %02X\n", rev_table[0x2C], rev_mod(0x2C), rev_mul64(0x2C));

    unsigned bad8 = 0;
    for (unsigned b = 0; b < 256; b++) {
        unsigned want = 0;
        for (int i = 0; i < 8; i++)
            want |= ((b >> i) & 1) << (7 - i);
        bad8 += (rev_table[b] != want) + (rev_mod((uint8_t) b) != want) + (rev_mul64((uint8_t) b) != want);
    }
    unsigned long long bad = 0;
    uint32_t x = 0;
    do {
        uint32_t r = reverse32(x);
        bad += (r != reverse32_loop(x)) + (reverse32(r) != x) + (reverse32_table(x) != r);
    } while (++x != 0);
    printf("8-bit mismatches: %u, 32-bit mismatches: %llu\n", bad8, bad);

    /* FFT 重排順序 / Redis SCAN 游標順序：n = 8 時把 3 位元索引反轉 */
    printf("bit-reversed order (n=8):");
    for (uint32_t i = 0; i < 8; i++)
        printf(" %u", reverse32(i) >> 29);
    printf("\n");
    return 0;
}
```

```
1E6A2C48 1E6A2C48
34 34 34
8-bit mismatches: 0, 32-bit mismatches: 0
bit-reversed order (n=8): 0 4 2 6 1 5 3 7
```

（窮舉約 95 秒。）三項檢查：分治版本與迴圈版本一致、查表版本一致、**反轉兩次回到原值**（位元反轉是自身的反函數，involution）。迴圈版本若寫成常見的 `if (x & (1 << i)) r |= 1 << (31 - i);`，`1 << 31` 是有號溢位 UB；這裡的 `(x >> i) & 1` 是 `uint32_t`，沒問題。

- **`rev_mod`**：`0x0202020202` 以 8 位元間隔把 b 複製 5 份；遮罩 `0x010884422010` 把 b 的第 t 個位元挑到某個 10 位元「位數」的第 7 − t 個位置。2¹⁰ ≡ 1 (mod 1023)，`% 1023` 加總位數，得到的正是反轉後的位元組——和 `pop_mod` 同一個原理。
- **`rev_mul64`**：改以 10 位元間隔複製，遮罩把第 t 個位元挑到某個位元組的第 7 − t 個位置，再乘 `0x0101010101` 把五個位元組加總到位元 32..39。用乘法取代取餘數。

編譯器能不能認出 `reverse32`？x86-64 沒有通用暫存器的位元反轉指令。gcc 11 與 clang 14 都把前兩步（交換半部 + 交換位元組）合併成一道 **`bswap`**，後三步照字面產生（clang 共 18 道指令）。原文記錄 arm64 上 Clang 20 把五步整個編成一道 `rbit`，GCC 則只合併成 `rev`；GCC 的 `__builtin_bitreverse32` 直到 2026 年才進入 GCC 17 開發分支，Clang 早就有。

### 應用：位元順序與「從高位開始遞增」

- **Ethernet CRC**：Ethernet 先送位元組的最低位元，CRC-32 因此採「反射」形式計算。Linux 的 `ether_crc` 定義為 `bitrev32(crc32_le(~0, data, length))`，把結果轉回一般位元順序供網路卡的雜湊表使用。
- **FFT**：radix-2 Cooley-Tukey 每一層依索引最低位元分偶奇，最底層的順序就是索引的位元反轉——上面輸出的 0 4 2 6 1 5 3 7。原地實作在計算前以 `i < rev(i)` 時交換的方式重排。
- **Redis `SCAN`**：游標從高位元開始遞增（反轉、加 1、再反轉），序列同樣是 0 4 2 6 1 5 3 7。好處是雜湊表在兩次呼叫之間擴大或縮小時，已經走過的低位元樣式不必重走、也不會漏掉元素，代價是部分元素可能回傳不只一次。`src/dict.c` 的 `rev` 用迴圈產生分治遮罩（`mask ^= mask << s`）。

## 位元交錯：Morton 碼

位元反轉用「位移 + 遮罩」分治搬移位元；同樣的結構也能把位元**打散**。把兩個 16 位元整數交錯成一個 32 位元整數——x 放偶數位、y 放奇數位——得到 **Morton 碼**，依它排序的平面點構成 Z 字形走訪（Z-order）：

```
0000 0000 0000 0000 abcd efgh ijkl mnop    x
0000 0000 abcd efgh 0000 0000 ijkl mnop    (x | x << 8) & 0x00FF00FF
0000 abcd 0000 efgh 0000 ijkl 0000 mnop    (x | x << 4) & 0x0F0F0F0F
00ab 00cd 00ef 00gh 00ij 00kl 00mn 00op    (x | x << 2) & 0x33333333
0a0b 0c0d 0e0f 0g0h 0i0j 0k0l 0m0n 0o0p    (x | x << 1) & 0x55555555
```

x86 的 BMI2 有 `pdep`（parallel deposit）指令，`_pdep_u32(x, 0x55555555)` 一道指令完成 `spread`；`pext` 做反方向。我們一併驗證：

```c
#include <stdint.h>
#include <stdio.h>
#include <immintrin.h>

static uint32_t spread(uint32_t x)      /* 第 i 個位元搬到第 2i 個 */
{
    x &= 0x0000FFFF;
    x = (x | (x << 8)) & 0x00FF00FF;
    x = (x | (x << 4)) & 0x0F0F0F0F;
    x = (x | (x << 2)) & 0x33333333;
    x = (x | (x << 1)) & 0x55555555;
    return x;
}

static uint32_t compact(uint32_t x)     /* spread 的反向：取出偶數位元並收攏 */
{
    x &= 0x55555555;
    x = (x | (x >> 1)) & 0x33333333;
    x = (x | (x >> 2)) & 0x0F0F0F0F;
    x = (x | (x >> 4)) & 0x00FF00FF;
    x = (x | (x >> 8)) & 0x0000FFFF;
    return x;
}

static uint32_t morton(uint16_t x, uint16_t y) { return spread(x) | (spread(y) << 1); }

int main(void)
{
    printf("%08X\n", (unsigned) morton(0xFFFF, 0));
    for (uint16_t y = 0; y < 4; y++) {
        for (uint16_t x = 0; x < 4; x++)
            printf("%3u", (unsigned) morton(x, y));
        printf("\n");
    }
    unsigned long bad = 0;
    for (uint32_t x = 0; x < 65536; x++) {
        uint32_t want = 0;
        for (int i = 0; i < 16; i++)
            want |= ((x >> i) & 1) << (2 * i);
        bad += (spread(x) != want) + (compact(want) != x) +
               (compact(morton((uint16_t) x, (uint16_t) ~x) >> 1) != (uint16_t) ~x) +
               (_pdep_u32(x, 0x55555555) != want) + (_pext_u32(want, 0x55555555) != x);
    }
    printf("mismatches: %lu\n", bad);
    return 0;
}
```

```
$ gcc -O2 -mbmi2 -Wall morton.c -o mo && ./mo
55555555
  0  1  4  5
  2  3  6  7
  8  9 12 13
 10 11 14 15
mismatches: 0
```

窮舉全部 65536 個 16 位元輸入。4×4 方格是 `morton(x, y)` 的值：0..3 填滿左上角的 2×2 區塊，4..7 填右上角，依此遞迴——**任一個 2^k × 2^k 的對齊區塊在 Morton 碼上都是連續的一段**。依 Morton 碼排列二維資料，平面上相鄰的點在記憶體中也大多相鄰，用於空間索引（四元樹、GeoHash 的本質）、GPU 紋理的 swizzle 儲存與分塊影像。`pdep` 在 Intel Haswell 之後很快；在 Zen 2 以前的 AMD 處理器上是微碼實作、慢上許多（Zen 3 起改為硬體實作）——用之前要看目標 CPU。

## 影像畫素的位元操作

嵌入式 framebuffer 常用 16 位元的 **RGB565**：紅 5 位元、綠 6 位元（人眼對綠最敏感）、藍 5 位元。

```c
#include <stdint.h>
#include <stdio.h>

/* RGB565：R 佔 5 位元、G 佔 6 位元、B 佔 5 位元 */
static uint16_t blend(uint16_t fg, uint16_t bg, uint8_t alpha)
{
    unsigned fg_r = fg >> 11, fg_g = (fg >> 5) & 0x3F, fg_b = fg & 0x1F;
    unsigned bg_r = bg >> 11, bg_g = (bg >> 5) & 0x3F, bg_b = bg & 0x1F;
    unsigned out_r = (fg_r * alpha + bg_r * (255 - alpha)) / 255;
    unsigned out_g = (fg_g * alpha + bg_g * (255 - alpha)) / 255;
    unsigned out_b = (fg_b * alpha + bg_b * (255 - alpha)) / 255;
    return (uint16_t) ((out_r << 11) | (out_g << 5) | out_b);
}

/* RGB888 -> RGB565：截掉低位元；反方向以「位元複製」補滿低位元 */
static uint16_t pack565(unsigned r, unsigned g, unsigned b)
{
    return (uint16_t) (((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3));
}
static void unpack565(uint16_t p, unsigned *r, unsigned *g, unsigned *b)
{
    unsigned r5 = p >> 11, g6 = (p >> 5) & 0x3F, b5 = p & 0x1F;
    *r = (r5 << 3) | (r5 >> 2);
    *g = (g6 << 2) | (g6 >> 4);
    *b = (b5 << 3) | (b5 >> 2);
}

int main(void)
{
    printf("blend(red, blue, 128) = %04X\n", (unsigned) blend(0xF800, 0x001F, 128));
    unsigned r, g, b;
    uint16_t white = pack565(255, 255, 255);
    unpack565(white, &r, &g, &b);
    printf("white: 565=%04X -> back to (%u,%u,%u)\n", white, r, g, b);
    unpack565(pack565(200, 100, 50), &r, &g, &b);
    printf("(200,100,50) -> 565 -> (%u,%u,%u)\n", r, g, b);

    for (unsigned x = 0; x < 1u << 20; x++)
        if (((x + 1 + (x >> 8)) >> 8) != x / 255) {
            printf("div255 trick: first mismatch at x = %u\n", x);
            break;
        }
    /* 四捨五入版本：(t + (t >> 8)) >> 8，t = x + 128 */
    unsigned bad = 0;
    for (unsigned x = 0; x <= 65025; x++) {
        unsigned t = x + 128;
        bad += ((t + (t >> 8)) >> 8) != (x + 127) / 255;
    }
    printf("rounding div255 mismatches in [0, 65025]: %u\n", bad);
    return 0;
}
```

```
blend(red, blue, 128) = 780F
white: 565=FFFF -> back to (255,255,255)
(200,100,50) -> 565 -> (206,101,49)
div255 trick: first mismatch at x = 65535
rounding div255 mismatches in [0, 65025]: 0
```

- `blend(red, blue, 128)`：紅色分量 31·128/255 與藍色 31·127/255 都捨去為 15，得 `(15 << 11) | 15 = 0x780F`。
- **解包時的「位元複製」**：5 位元擴成 8 位元若只左移 3，最大值 31 會變成 248 而不是 255（白色變灰）。`(r5 << 3) | (r5 >> 2)` 把高位元複製到空出的低位元，0 對應 0、31 對應 255，線性又不需要乘除。
- **除以 255 的近似**：`(x + 1 + (x >> 8)) >> 8`。原理是 1/255 = (1/256)·1/(1 − 2⁻⁸) = (1/256)(1 + 2⁻⁸ + 2⁻¹⁶ + …)，`x >> 8` 近似第二項，`+ 1` 補償捨去。窮舉顯示它對 0..65534 都等於 ⌊x/255⌋，而兩個 8 位元值的加權和最大 255·255 = 65025，在範圍內。四捨五入版本對 0..65025 全部正確——反覆疊加的影像合成用無條件捨去會累積成整體偏暗，所以常用這個版本。GCC/Clang 對常數除法本來就會換成乘以倒數；這個寫法的價值在於只用加法與位移，適合沒有快速乘法器的處理器或 SIMD 一次處理多個 16 位元分量。

### 案例：RGBA 轉灰階的四種「最佳化」

原文以 BT.601 的亮度公式 `0.299R + 0.587G + 0.114B` 把 32 位元 RGBA（位元 31..24 為 alpha）轉成灰階，比較五個版本：原始 `double`（`float`）、三張 256 項表格（`lut`）、以 `uint8_t *` 直接讀寫位元組（`byte`，依賴 little-endian）、兩者合併、以及**整數權重**（`fixed`）。整數權重把權重乘 256 取整數，右移 8 位元完成除法；常見的 77/151/28 其實不是四捨五入的結果（0.587 × 256 = 150.27、0.114 × 256 = 29.18），四捨五入得 77/150/29，總和同樣 256。窮舉全部 2²⁴ 種 RGB：

```c
#include <stdio.h>
#include <stdlib.h>

/* 以 2^24 種 RGB 組合比較整數權重與 double 版本 (無條件捨去) 的差異 */
int main(void)
{
    static const unsigned w[][3] = {{77, 150, 29}, {77, 151, 28}};
    for (int k = 0; k < 2; k++) {
        long mismatch = 0;
        int max_diff = 0;
        for (unsigned c = 0; c < 1u << 24; c++) {
            unsigned r = c >> 16, g = (c >> 8) & 0xff, b = c & 0xff;
            volatile double wr = r * 0.299, wg = g * 0.587, wb = b * 0.114;
            int ref = (int) (wr + wg + wb);
            int fix = (int) ((w[k][0] * r + w[k][1] * g + w[k][2] * b) >> 8);
            int d = abs(fix - ref);
            mismatch += d != 0;
            if (d > max_diff)
                max_diff = d;
        }
        printf("%u/%u/%u: %ld mismatches (%.2f%%), max |diff| %d\n", w[k][0],
               w[k][1], w[k][2], mismatch, 100.0 * mismatch / (1 << 24), max_diff);
    }
    return 0;
}
```

```
77/150/29: 2243405 mismatches (13.37%), max |diff| 1
77/151/28: 5834498 mismatches (34.78%), max |diff| 2
```

（`volatile double` 讓三個乘積各自捨入後才相加，避免編譯器合併成 FMA。）

我們把原文的 `rgba.c`（1920×1080 的 xorshift 測試影像、每版本 21 次取中位數）在 Ryzen 7 5700G 上重跑，`taskset -c 2` 固定核心（WSL，單次量測，數字有雜訊）：

| 版本（ms） | gcc -O0 | gcc -O2 | gcc -O3 | clang -O0 | clang -O2 | clang -O3 |
|---|---|---|---|---|---|---|
| float | 10.32 | 3.06 | 1.98 | 10.58 | 2.20 | 2.32 |
| lut | 9.94 | 2.50 | 2.48 | 10.83 | 2.11 | 2.13 |
| byte | **6.99** | 3.61 | 3.14 | 8.37 | 3.19 | 3.04 |
| lut+byte | 8.19 | 1.90 | 1.79 | **5.00** | 2.07 | 1.83 |
| fixed | 8.42 | 2.98 | **1.16** | 11.14 | **0.96** | **1.09** |

每個版本與 `float` 的差異畫素數：`lut`、`byte`、`lut+byte` 都是 0，`fixed` 是 276962（13.36%，與窮舉比例一致）。

讀這張表的三個教訓：

1. **`-O0` 的排名毫無意義**：`byte` 在 `-O0` 最快，到了 `-O2` 反而是最慢的。`-O0` 保留所有區域變數的堆疊讀寫，省掉位移與遮罩的「指令數」佔優勢；最佳化之後，這些運算本來就只是一兩道指令。
2. **結果依平台而異**：原文在 arm64（ThunderX2）上量到 GCC `-O2` 時 `byte` 比 `float` 快（9.0 vs 11.8 ms）；我們在 x86-64 上 `byte` 比 `float` 慢（3.61 vs 3.06 ms）。位元組讀寫讓記憶體存取從 2 道增加到 6 道，在不同微架構上代價不同。
3. **整數權重在能向量化時勝出**：clang `-O2`、gcc `-O3` 向量化 `fixed` 後最快（約 1 ms）。一個 128 位元向量暫存器只放 2 個 `double`，卻放 4 個 32 位元整數。查表版本需要依索引逐一載入，難以向量化。

構想 1「以 RGB 直接查一張 2²⁴ 項的表」原文也分析了：一項 1 位元組也要 16 MiB，隨機畫素讓查表大多落在 L1/L2 之外；而原文提出的寫法 `table[rgb & 0x00ffffff] + rgb & 0xff000000` 還有優先序 bug——`+` 高於 `&`。

## 數值語意與編譯器最佳化

[Ch 21](./21-number-systems.md) 整理了哪些代數性質對哪個型別成立，編譯器的改寫正以這些性質為依據。

```c
int mul9(int x) { return x * 9; }
int mul45(int x) { return x * 45; }
unsigned div7(unsigned x) { return x / 7; }
```

```
mul9  (gcc/clang):  leal (%rdi,%rdi,8), %eax           ; 9x = x + 8x
mul45 (gcc):        imull $45, %edi, %eax
mul45 (clang):      leal (%rdi,%rdi,8), %eax ; leal (%rax,%rax,4), %eax   ; 45x = 5·(9x)
div7  (gcc):        imulq $613566757 ... shrq $32 ... subl ... shrl ... addl ... shrl $2
```

乘以常數用分配律與結合律拆成 `lea`（[Ch 21](./21-number-systems.md) 說過這些定律在模 2^w 的環中成立）；拆不拆由各編譯器的成本模型決定。除以 7 換成乘以 magic number 613566757（= ⌈2³⁵/7⌉ − 2³²，再以「減、右移、加、右移」修正），推導見 Granlund & Montgomery 1994 與 Hacker's Delight 第 10 章。

### 結合律決定能不能向量化

```c
int sum_i(const int *a, long n)     { int s = 0; for (long i = 0; i < n; i++) s += a[i]; return s; }
float sum_f(const float *a, long n) { float s = 0; for (long i = 0; i < n; i++) s += a[i]; return s; }
float sum_kahan(const float *a, long n)
{
    float s = 0, c = 0;                 /* c：先前加法遺失的低位 */
    for (long i = 0; i < n; i++) {
        float y = a[i] - c;
        float t = s + y;
        c = (t - s) - y;
        s = t;
    }
    return s;
}
```

把陣列加總改成 4 個通道同時累加，等於重新分組，需要結合律。數組合語言中的向量指令（`paddd` 為整數、`addps` 為 float）與 Kahan 迴圈裡的減法 `subss`：

| 組態 | `sum_i` 向量化 | `sum_f` 向量化 | Kahan 的減法 |
|---|---|---|---|
| gcc -O2 | 否 | 否 | 保留 |
| gcc -O3 | 是 | 否 | 保留 |
| gcc -O3 -ffast-math | 是 | 是 | **全部刪除** |
| clang -O2 | 是 | 否 | 保留 |
| clang -O2 -ffast-math | 是 | 是 | **全部刪除** |

- 整數加法在模 2³² 下可結合，clang `-O2`、gcc `-O3` 都向量化（gcc 的 `-O2` 採 very-cheap 成本模型，迴圈次數未知時不向量化）。
- 浮點加法不可結合（[Ch 25](./25-ieee754.md)），C11 5.1.2.3 §14 EXAMPLE 5 禁止編譯器重排，所以 `sum_f` 預設不向量化；`-ffast-math`（其中的 `-fassociative-math`）允許重排後才向量化。
- **Kahan 加總的正確性完全建立在結合律不成立之上**：代數上 `(t - s) - y` 恆為 0。`-ffast-math` 依代數把它化簡掉，Kahan 退化成普通加總：

`kahan.c` 把上面的 `sum_f`、`sum_kahan` 接上一個 `main`，以 xorshift32 產生 2²⁴ 個 [0, 1) 的 `float`，同時用 `double` 累加當參考值：

```c
int main(void)
{
    long n = 1L << 24;
    float *a = malloc(n * sizeof *a);
    double ref = 0;
    unsigned s = 2463534242u;
    for (long i = 0; i < n; i++) {
        s ^= s << 13; s ^= s >> 17; s ^= s << 5;
        a[i] = (s >> 8) * (1.0f / 16777216.0f);   /* 高 24 位元 → [0, 1) */
        ref += a[i];
    }
    printf("double ref %.1f  seq %.1f  kahan %.1f\n", ref, sum_f(a, n), sum_kahan(a, n));
    free(a);
    return 0;
}
```

```
$ gcc -O2 kahan.c -o k && ./k
double ref 8388891.0  seq 8388612.0  kahan 8388891.0
$ gcc -O3 -ffast-math kahan.c -o kf && ./kf
double ref 8388891.0  seq 8388773.0  kahan 8388773.0
```

`-O2` 時 Kahan 與 `double` 參考值完全相同；`-ffast-math` 之後 Kahan 的結果與（向量化的）普通加總相同——補償消失了。**含 Kahan 這類演算法的轉譯單元不能用 `-ffast-math` 編譯**。[Ch 26](./26-floating-point-errors.md) 會完整討論 Kahan 與 pairwise 加總。

### 加法本身也能只用位元運算

反過來，加法可以完全用位元運算實作——這就是硬體加法器：

```c
static unsigned add_iter(unsigned a, unsigned b)
{
    while (b) {
        unsigned carry = (a & b) << 1;   /* 進位：兩邊都是 1 的位置，往左一格 */
        a ^= b;                          /* 不進位的和 */
        b = carry;
    }
    return a;
}
```

```
add_iter(0xFFFFFFFF, 1) = 0, add_iter(1234, 4321) = 5555
mismatches: 0          （10⁶ 組隨機輸入與 + 比對）
```

每一輪保持 `a + b` 不變（[Ch 21](./21-number-systems.md) 的 `x + y = (x ^ y) + 2(x & y)`），而 b 尾端的 0 至少多一個，32 輪內歸零。參數必須無號：用 `int` 時 `(a & b) << 1` 會觸發有號左移 UB。

## XOR 與加解密

### XOR 的代數性質

[Ch 21](./21-number-systems.md)「進階」說過，`({0,1}^n, ⊕)` 是每個元素以自己為反元素的阿貝爾群。這給了加密最需要的三個性質：

- **加密與解密是同一個運算**：`(m ⊕ k) ⊕ k = m`。
- **固定 k 時 `x ↦ x ⊕ k` 是一對一對應**，反函數是自己，不遺失資訊。
- **沒有進位**：結果的第 i 位元只取決於兩運算元的第 i 位元。

AND 與 OR 則不可逆：`x ∧ 0 = 0` 對 x = 0 與 1 都成立，資訊一旦遺失就無從解密。更重要的是分布：

```c
#include <stdint.h>
#include <stdio.h>

static uint64_t s = 88172645463325252u;
static uint64_t rnd(void) { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return s; }

int main(void)
{
    /* 明文：偏斜的分布，位元為 1 的機率 1/8；金鑰：均勻亂數 */
    long n = 10000000, ones_p = 0, ones_and = 0, ones_or = 0, ones_xor = 0;
    for (long i = 0; i < n; i++) {
        unsigned p = (rnd() & 7) == 0, k = rnd() & 1;
        ones_p += p; ones_and += p & k; ones_or += p | k; ones_xor += p ^ k;
    }
    printf("P(bit=1): plain %.3f  AND %.3f  OR %.3f  XOR %.3f\n",
           (double) ones_p / n, (double) ones_and / n, (double) ones_or / n, (double) ones_xor / n);
    return 0;
}
```

```
P(bit=1): plain 0.125  AND 0.062  OR 0.563  XOR 0.500
```

明文 1 的機率 1/8；AND 之後 1/16、OR 之後 9/16，**都還看得出明文的偏斜**；XOR 之後恰好 1/2。證明：X 均勻且與 Y 獨立時，`P(X ⊕ Y = z) = Σ_y P(Y = y)·P(X = z ⊕ y) = 2⁻ⁿ`——關鍵是對每個 y 恰有一個 x 使結果為 z。這就是 **one-time pad** 的安全性來源：Shannon 1949 年的〈Communication Theory of Secrecy Systems〉證明它達成完美保密（截獲密文後明文的後驗分布等於先驗分布），並證明任何完美保密系統的金鑰至少要和訊息一樣長——這也是 one-time pad 難以普及的根本原因。

### 只有 XOR 不夠：two-time pad 與可塑性

```c
#include <stdio.h>
#include <string.h>

static void xor_buf(unsigned char *out, const void *a, const void *b, size_t n)
{
    const unsigned char *x = a, *y = b;
    for (size_t i = 0; i < n; i++)
        out[i] = x[i] ^ y[i];
}

int main(void)
{
    static const unsigned char ks[14] = { /* 同一段 keystream 用二次 */
        0x5f, 0x2e, 0xe3, 0xe0, 0xbc, 0x7a, 0x73,
        0x95, 0x4c, 0x92, 0x9e, 0xc8, 0xae, 0x63,
    };
    const char *p1 = "ATTACK AT DAWN", *p2 = "RETREAT TO HQ!";
    unsigned char c1[14], c2[14], d[14], r[15] = {0};

    xor_buf(c1, p1, ks, 14);
    xor_buf(c2, p2, ks, 14);
    xor_buf(d, c1, c2, 14);
    xor_buf(r, p1, p2, 14);
    printf("C1^C2 == P1^P2: %s\n", memcmp(d, r, 14) ? "no" : "yes");

    xor_buf(r, d, p1, 14); /* 已知 P1 即得 P2 */
    printf("P2  = %s\n", (char *) r);

    c1[10] ^= 'D' ^ 'N';   /* 不知道金鑰也能竄改明文 */
    c1[11] ^= 'A' ^ 'O';
    c1[12] ^= 'W' ^ 'O';
    xor_buf(r, c1, ks, 14);
    printf("P1' = %s\n", (char *) r);
    return 0;
}
```

```
C1^C2 == P1^P2: yes
P2  = RETREAT TO HQ!
P1' = ATTACK AT NOON
```

- **金鑰重複使用**：`c₁ ⊕ c₂ = m₁ ⊕ m₂`，金鑰完全抵銷；知道一段明文就得到另一段。蘇聯 KGB 重複使用部分 pad 頁面，讓美國的 VENONA 計畫自 1943 年起破解約 3,000 則訊息；802.11 的 WEP 只有 24 位元 IV，依生日悖論約 5000 個封包就會重複 keystream（Borisov、Goldberg、Wagner，MobiCom 2001）。
- **可塑性（malleability）**：把密文 XOR 一個差值 δ，解密結果就是 `m ⊕ δ`，不需要金鑰——上面把「DAWN」改成「NOON」。**保密不等於完整性**，密文必須另以 MAC 驗證（GCM 的 GHASH 就是補這個洞）。

現代設計中 XOR 負責把金鑰與資料結合：CTR 模式與串流加密（密文 = 明文 ⊕ keystream，Linux `crypto/ctr.c` 呼叫 `crypto_xor()`）、Feistel 網路（`R_{i+1} = L_i ⊕ F(R_i, K_i)`，F 不必可逆）、AES 的 AddRoundKey、ChaCha 的 ARX（add-rotate-XOR：XOR 與旋轉在 GF(2) 上線性，模 2³² 加法的進位提供非線性）、HMAC 的 ipad/opad。

### 常數時間比較

驗證 MAC 時，若遇到第一個不同的位元組就返回，執行時間會洩漏猜對的前綴長度。Nate Lawson 2009 年指出 Google Keyczar 的 HMAC 驗證就有這個問題。修正是把每個位置的 XOR 以 OR 累積，走完整段才判斷：

```c
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

/* 相等回傳 0，不等回傳 -1；迴圈走完全部 n 個位元組，沒有依資料的分支 */
int ct_verify(const void *a, const void *b, size_t n)
{
    const volatile unsigned char *x = a, *y = b;
    uint32_t d = 0;
    for (size_t i = 0; i < n; i++)
        d |= x[i] ^ y[i];
    return (int) (1 & ((d - 1) >> 8)) - 1;
}

__attribute__((noinline)) int naive_verify(const void *a, const void *b, size_t n)
{
    const unsigned char *x = a, *y = b;
    for (size_t i = 0; i < n; i++)
        if (x[i] != y[i])
            return -1;
    return 0;
}

static double now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e9 + ts.tv_nsec;
}

int main(void)
{
    enum { N = 4096, REPS = 20000 };
    static unsigned char a[N], b[N];
    memset(a, 0x5A, N);
    for (int w = 0; w < REPS; w++) (void) ct_verify(a, a, N);  /* 暖機 */
    size_t pos[] = {0, 2048, 4095};
    printf("mismatch at   naive(ns)     ct(ns)\n");
    for (int k = 0; k < 3; k++) {
        memcpy(b, a, N);
        b[pos[k]] ^= 1;
        volatile int sink = 0;
        double t0 = now();
        for (int r = 0; r < REPS; r++) sink += naive_verify(a, b, N);
        double t1 = now();
        for (int r = 0; r < REPS; r++) sink += ct_verify(a, b, N);
        double t2 = now();
        printf("%-10zu %10.0f %10.0f\n", pos[k], (t1 - t0) / REPS, (t2 - t1) / REPS);
    }
    memcpy(b, a, N);
    printf("equal: %d, ", ct_verify(a, b, N));
    b[7] ^= 0x80;
    printf("differ: %d\n", ct_verify(a, b, N));
    return 0;
}
```

```
mismatch at   naive(ns)     ct(ns)
0                   2       1656
2048              979       1724
4095             2171       1690
equal: 0, differ: -1
```

（gcc 11 `-O2`，`taskset -c 2`；WSL 上連跑三次，前兩次一致，第三次的 ct 欄在 1655–3095 間跳動——計時量測要多跑幾次。）`naive_verify` 的時間與第一個不同的位置成正比，`ct_verify` 大致固定。最後一行：d 為 0 時 `d − 1` 是 `0xFFFFFFFF`，右移 8 位元後最低位為 1，結果 0；d 在 1..255 時右移後為 0，結果 −1——沒有分支（取自 TweetNaCl 的 `vn`，假設 `CHAR_BIT == 8`）。

但**原始碼層級的常數時間，不保證機器碼也是常數時間**：執行時間不屬於 C 的可觀察行為，編譯器依 as-if 規則可以把 OR 累積的迴圈改回提早返回。這不是假設——clangover 研究指出 Clang 15–18 在 x86 以 `-Os`、`-O1` 編譯 Kyber 參考實作時，把遮罩寫成的 `poly_frommsg` 編成與秘密相關的分支，5–10 分鐘即可還原 ML-KEM-512 私鑰。實務對策：`volatile`、空的內嵌組合語言當最佳化屏障（Linux 的 `OPTIMIZER_HIDE_VAR()`）、`noinline` 防 LTO 看穿、用 Valgrind 類工具檢查產出。Linux 的 `crypto_memneq()`、libsodium 的 `crypto_verify_16`、OpenSSL 的 `CRYPTO_memcmp` 都是這個手法。

### XOR 秘密分享與 RAID 同位

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void)
{
    /* RAID-5 式同位：P = D0 ^ D1 ^ D2；任一塊遺失可由其餘 XOR 還原 */
    unsigned char d[3][8] = {"disk-0!", "disk-1?", "disk-2#"}, p[8] = {0}, rec[8];
    for (int i = 0; i < 8; i++)
        p[i] = d[0][i] ^ d[1][i] ^ d[2][i];
    for (int i = 0; i < 8; i++)              /* 假設 disk-1 壞了 */
        rec[i] = d[0][i] ^ d[2][i] ^ p[i];
    printf("recovered disk-1: %s\n", (char *) rec);

    /* 2-of-2 秘密分享：share1 = r，share2 = s ^ r */
    const char *secret = "PIN=4271";
    unsigned char r[9], s2[9], out[9] = {0};
    srand(42);                               /* 教學示範；真實系統要用 CSPRNG */
    for (int i = 0; i < 8; i++) {
        r[i] = (unsigned char) rand();
        s2[i] = (unsigned char) secret[i] ^ r[i];
        out[i] = r[i] ^ s2[i];
    }
    printf("share1:");
    for (int i = 0; i < 8; i++) printf(" %02x", r[i]);
    printf("\nshare2:");
    for (int i = 0; i < 8; i++) printf(" %02x", s2[i]);
    printf("\ncombined: %s\n", (char *) out);
    return 0;
}
```

```
recovered disk-1: disk-1?
share1: 46 64 31 29 64 86 ed 9c
share2: 16 2d 7f 14 50 b4 da ad
combined: PIN=4271
```

（`rand()` 的值依 glibc 版本而定；真實系統必須用密碼學安全的亂數源。）兩者用的是同一個性質：每個元素是自己的反元素，所以「加上」與「消去」是同一個動作。秘密分享的任一份單獨看都是均勻分布（上面的證明）；RAID-5 以 `P = D₀ ⊕ D₁ ⊕ … ⊕ D_{n−1}` 儲存同位，任一磁碟損毀時其餘的 XOR 就是遺失的內容（Patterson、Gibson、Katz，SIGMOD 1988）。Linux 的 `lib/raid/xor/` 在開機時量測並挑選最快的 XOR 實作供 md RAID-5 使用。

## 對比與取捨

| 運算 | 軟體寫法 | 內建函式 | x86-64 指令 | 0 的處理 |
|---|---|---|---|---|
| ctz | de Bruijn 查表、二分 | `__builtin_ctz` | `bsf`（舊）/`tzcnt`（BMI1） | builtin 未定義；`stdc_trailing_zeros` = 寬度 |
| clz / log₂ | 折半、擴散 + popcount | `__builtin_clz` | `bsr`（舊）/`lzcnt` | 同上；`__builtin_clzg(x, fb)` |
| popcount | SWAR、查表 | `__builtin_popcount` | `popcnt`（需 `-mpopcnt`，否則呼叫 libgcc） | 有定義 |
| 旋轉 | 遮罩寫法 | — | `rol`/`ror`（編譯器自動辨認） | 寫對就有定義 |
| 位元反轉 | 分治五步 | clang `__builtin_bitreverse32` | 無（gcc/clang 合併出 `bswap`） | — |
| Morton | spread/compact | `_pdep_u32` | `pdep`（BMI2；Zen 2 以前很慢） | — |

**選擇原則**：能用 C23 `<stdbit.h>` 就用（可攜、0 有定義）；舊編譯器用 `__builtin_*` 並自行排除 0；只在需要 `constexpr` 式的常數計算、不能依賴編譯器、或要教學時寫軟體版本。**寫完一定要看組合語言**，確認編譯器有沒有認出來，以及你的 `-march` 讓它用了哪條指令。

## 踩雷集錦

1. **「`__builtin_clz(0)` 會回傳 32」**：未定義。我們實測預設旗標下回傳垃圾（541364383），加 `-mlzcnt` 才是 32。同一份原始碼依 `-march` 改變結果。
2. **「`__builtin_popcount` 一定是一道指令」**：x86-64 baseline 沒有 `popcnt`，gcc 呼叫 libgcc 的 `__popcountdi2`。熱迴圈要加 `-mpopcnt` 或 `-march=x86-64-v2`。
3. **「`(v << c) | (v >> (32 - c))` 是正確的旋轉」**：c = 0 時右移 32 位元，UB。用 `-c & 31`；Linux 從 v4.4 到 v5.2 花了兩次提交才全部修好。
4. **「用 float 算 log₂ 很巧妙」**：binary32 只有 24 位元有效位數，`0x01FFFFFF` 會被捨入成 2²⁵，答案錯 1。要用 `double`，或乾脆用 clz。
5. **「`-O0` 下比較快的寫法就是比較快」**：RGBA 案例中 `byte` 在 `-O0` 最快、`-O2` 最慢。效能比較只在最佳化後、目標平台上有意義。
6. **「Kahan 加總配 `-ffast-math` 更快更準」**：`-ffast-math` 會把補償項化簡掉，Kahan 退化成普通加總。
7. **「把 `memcmp` 換成 OR 累積就是常數時間」**：原始碼層級不等於機器碼層級；clangover 證明編譯器真的會把遮罩改回分支。要加最佳化屏障並檢查產出。

## 進階：再往深一層

**位元掃描的換算網。** Fabian Giesen 的〈Bit scanning equivalencies〉整理了 ctz、clz、popcount、ffs、`x & -x`、`x ^ (x − 1)` 之間的所有換算——例如 `ctz(x) = popcount((x & -x) - 1)`、`clz` 可由「擴散後 popcount」得到。在沒有某條指令的平台上，知道怎麼換算比背技巧更有用。

**SIMD 版本的 popcount。** Wojciech Muła、Nathan Kurz、Daniel Lemire 的〈Faster Population Counts Using AVX2 Instructions〉（The Computer Journal，2018）以 `vpshufb` 做 4 位元查表，在大陣列上比 `popcnt` 指令快；這是 SWAR 推廣到 256 位元暫存器的版本。

**`pdep`/`pext` 的其他用途**：西洋棋引擎的 magic bitboard 改用 `pext` 取代乘法雜湊；Morton 碼的 3D 版本；從位元串流中抽取任意欄位。

**Linux 的 `sched_find_first_bit` 與 `fls`。** 核心 `include/asm-generic/bitops/` 有 `__ffs`、`fls`、`__fls`、`hweight32`（popcount）的通用實作與各架構的組合語言版本，`ilog2()` 巨集在常數引數時展開成一長串 `?:` 讓編譯器在編譯時期算出、非常數時呼叫 `fls`——值得對照本章讀一遍。

**面試題的正確姿態**：「數 1 的個數」「判斷 2 的冪」「找最低位元的 1」「反轉位元」「不用 `+` 做加法」。答完軟體寫法後，主動補一句「實務上用 `__builtin_popcount`／`<stdbit.h>`，注意 0 的處理與 `-march`」，會比多背一個技巧更加分。[`../c_interview/26-branchless.md`](../c_interview/26-branchless.md) 有更多題型。

## 動手練習

1. 用 de Bruijn 手法寫 64 位元的 `ctz64`：先寫一個程式**搜尋**一個 64 位元 de Bruijn 常數（從 `0x0218A392CD3D5DBF` 驗證起，或自己用深度優先搜尋生成 B(2,6)），再以 2⁶⁴ 中的 10⁸ 個隨機值加上所有 2^k 對照 `__builtin_ctzll`。
2. 寫一個 `next_pow2(uint32_t x)`：回傳 ≥ x 的最小 2 的冪（x = 0 與 x > 2³¹ 時要定義行為）。用 clz 寫一版、用「擴散」寫一版，窮舉比對。這就是 C23 的 `stdc_bit_ceil`。
3. 把 `pop_swar` 改成 64 位元版本，算清楚最後一步的乘數與右移量，並說明為什麼總和 64 不會溢出一個位元組。
4. 寫 3D Morton 碼：三個 10 位元座標交錯成 30 位元。推導每一步的遮罩（提示：第一步是 `(x | x << 16) & 0x030000FF`），以窮舉 2¹⁰ 驗證每個座標，再用 `_pdep_u32(x, 0x09249249)` 對照。
5. 在 `ct_verify` 上試著讓編譯器「看穿」它：拿掉 `volatile`、用 `-O3` 編譯，看組合語言有沒有出現提早返回或向量化；再加上 `__asm__("" : "+r"(d))` 當屏障，比較差異。

## 本章重點整理

- `x & (x − 1)` 清除最低位元的 1、`x & -x` 只留它；這兩個式子是 ctz、popcount、2 的冪判斷的共同起點。
- ctz/clz 有 de Bruijn、二分、浮點指數三類軟體寫法；硬體上是 `bsf/bsr`（0 未定義）或 `tzcnt/lzcnt`（0 得寬度）。`__builtin_clz(0)` 是 UB；C23 `<stdbit.h>` 對 0 有定義。
- SWAR 把暫存器當成並列窄欄位，只要欄位間不進位，一道加法算多個欄位；乘以 `0x01010101` 把位元組加總到最高位元組。
- 2^k ≡ 1 (mod 2^k − 1) 讓「取餘數」變成「位數和」，`pop_mod`、`rev_mod` 都靠它。
- 旋轉要寫成 `(v << (c & m)) | (v >> (-c & m))`；gcc/clang 在 `-O2` 把各種寫法都辨認成 `rol`，差別只在原始碼有沒有 UB。
- 位元反轉、Morton 碼都是「位移 + 遮罩」的分治搬移；BMI2 的 `pdep`/`pext` 一道指令完成，但要看目標 CPU。
- 效能比較只在最佳化後、目標平台上有意義；`-ffast-math` 會毀掉 Kahan 這類依賴「結合律不成立」的演算法。
- XOR 是可逆、無進位、保持均勻分布的群運算，所以是金鑰與資料結合的基本運算；但它線性且可塑，必須配合不重複的 keystream 與 MAC。

## 自我檢核

- [ ] 不看筆記，能推導 `x & -x` 為什麼只留下最低位元的 1 嗎？
- [ ] 能解釋 de Bruijn 序列為什麼能把「2 的幾次方」變成查表索引，並說出程式怎麼驗證常數的正確性嗎？
- [ ] 能逐步解釋 `pop_swar` 的四行，特別是為什麼第三步可以「先加再遮罩」嗎？
- [ ] 如果面試官問「`__builtin_clz(0)` 會回傳什麼」，你能說出硬體層面的原因與兩種安全寫法嗎？
- [ ] 能證明 `-c & (w − 1)` 在 c = 0 與 c ≠ 0 時都給出正確的右移量嗎？
- [ ] 能說出為什麼 `-ffast-math` 讓 `sum_f` 能向量化、卻讓 Kahan 失效嗎？這兩件事背後是同一條代數性質嗎？
- [ ] 能解釋為什麼 AND 與 OR 不能拿來加密，而 XOR 可以，並說出 two-time pad 的攻擊方式嗎？

## 延伸閱讀

### 論文

- **[Using de Bruijn Sequences to Index a 1 in a Computer Word](http://supertech.csail.mit.edu/papers/debruijn.pdf)** — Charles E. Leiserson, Harald Prokop, Keith H. Randall（MIT 技術報告，1998）
  - **核心貢獻**：提出以 de Bruijn 序列乘法加查表計算 ctz 的方法，並證明正確性。
  - **讀哪裡**：Section 2–3（構造與證明），只有幾頁。
  - **和本章的關聯**：`ctz_debruijn` 與 `log2_32` 的原始出處。

- **[A technique for counting ones in a binary computer](https://doi.org/10.1145/367236.367286)** — Peter Wegner，Communications of the ACM 3(5)（1960）
  - **核心貢獻**：`v &= v − 1` 的 popcount 迴圈，一頁的短文。
  - **讀哪裡**：整篇。
  - **為什麼值得讀**：看一個 1960 年的技巧如何活到今天的 K&R 習題與面試題。

- **[Faster Population Counts Using AVX2 Instructions](https://arxiv.org/abs/1611.07612)** — Wojciech Muła, Nathan Kurz, Daniel Lemire，The Computer Journal 61(1)（2018）
  - **核心貢獻**：以 `vpshufb` 的 4 位元查表與 Harley-Seal 進位保留加法器，在大陣列上勝過 `popcnt` 指令。
  - **讀哪裡**：Section 2（既有方法比較，含本章 SWAR）與 Section 3 的 AVX2 演算法；效能圖表可略讀。
  - **前提知識**：本章的 SWAR 一節。

### 部落格 / 技術文章

- **[Bit Twiddling Hacks](https://graphics.stanford.edu/~seander/bithacks.html)** — Sean Eron Anderson（Stanford）
  - **讀哪裡**：「Counting bits set」「Computing parity」「Reverse bit sequence」「Find integer log base 2」「Interleave bits」幾節，對應本章大部分技巧。
  - **注意**：原文多用 `int` 並依賴實作定義行為，抄之前改寫成無號。

- **[Bit scanning equivalencies](https://fgiesen.wordpress.com/2013/10/18/bit-scanning-equivalencies/)** — Fabian Giesen（2013）
  - **這篇說什麼**：ctz、clz、ffs、popcount 等運算之間的完整換算關係。
  - **讀哪裡**：整篇；配合本章「最低位元的 1」的表格讀。
  - **為什麼值得讀**：作者是 RAD Game Tools 的資深工程師，寫低階最佳化的文章以精確著稱。

- **[Bit Twiddling Hacks 解析（一）](https://hackmd.io/@0xff07/ORAORAORAORA)** — 0xff07
  - **這篇說什麼**：逐條推導 Bit Twiddling Hacks 的技巧，中文、附證明。
  - **讀哪裡**：系列（一）到（三），挑本章對應的段落。

### 官方文件 / Spec

- **[GCC Manual: Bit Operation Builtins](https://gcc.gnu.org/onlinedocs/gcc/Bit-Operation-Builtins.html)**
  - **讀哪裡**：`__builtin_clz`、`__builtin_ctz` 對 0 未定義的說明，以及 GCC 14 起的 `__builtin_clzg`/`__builtin_ctzg`。
- **[N3220（C23 工作草案）](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n3220.pdf)** 7.18「Bit and byte utilities `<stdbit.h>`」
  - **讀哪裡**：7.18.3（leading zeros）、7.18.12（count ones）、7.18.14（bit width）；注意每個函式對 0 的定義。

### 書籍

- **《Hacker's Delight》2nd ed.** — Henry S. Warren（Addison-Wesley，2012）
  - **讀哪幾章**：Chapter 2（最低位元操作）、Chapter 5（popcount、parity、clz/ctz）、Chapter 7（位元反轉與置換，含 `pdep` 類操作）、Chapter 10（除以常數）。
  - **前提知識**：本章與 [Ch 23](./23-bitwise-basics.md)。

- **《The Art of Computer Programming, Vol. 4A: Combinatorial Algorithms, Part 1》** — Donald E. Knuth（2011）
  - **這本書的定位**：7.1.3 節「Bitwise Tricks and Techniques」是位元技巧最深入的論述，含 broadword computing（SWAR 的一般化）。
  - **讀哪幾章**：7.1.3 前半；數學較重，適合讀完本章後挑戰。

整數的位元我們已經玩遍了，`ctz_float` 和 `ilog2_double` 甚至偷看了浮點數的指數欄位。下一章正式打開 IEEE 754：符號、指數、尾數怎麼排、為什麼有次正規數、NaN 與 −0 從哪裡來、捨入模式又如何決定 `0.1 + 0.2` 的最後一位。

→ [Ch 25 IEEE 754：格式、次正規數、特殊值與捨入](./25-ieee754.md)
