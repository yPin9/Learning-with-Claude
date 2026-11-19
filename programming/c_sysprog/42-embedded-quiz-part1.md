# Ch 42 — 0x10 道嵌入式考題 I（0x01–0x08）

> **目標**：用 Nigel Jones 那份流傳超過四分之一個世紀的嵌入式面試題當鏡子，逐題檢查前 41 章學到的東西是不是真的會用：每題先看常見錯答，再用規格書條款與實測推出正解，最後延伸到 Linux 核心、glibc 與韌體的真實寫法。

> **對應原文**：[你所不知道的 C 語言：深究 0x10 道嵌入式系統考題篇](https://hackmd.io/@sysprog/c-0x10) 的「題目 0x01」到「題目 0x08」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14、glibc 2.35；Arm64 的結果以 `aarch64-linux-gnu-gcc` 11.4 交叉編譯、`qemu-aarch64` 執行；C23 段落以 Docker `gcc:14`（GCC 14.4）執行。實測 CPU 為 AMD Ryzen 7 5700G。原文的實驗用 GCC 13/14、Clang 18/20，凡是本章數字與原文不同的地方都會標出來。

## 為什麼需要這個？

2000 年 5 月，Nigel Jones 在 *Embedded Systems Programming* 雜誌發表〈A 'C' Test: The 0x10 Best Questions for Would-be Embedded Programmers〉，用 16 題篩選韌體工程師。題號用十六進位，`0x10` 就是 16（口頭上念「hex one-zero」或直接說 sixteen，別念成「ten」）。這份考題到今天仍是嵌入式職缺的常客。

為什麼放在這門課的最後？因為它**不考新東西**，考的是把舊東西連起來：一題 `#define` 同時考巨集、整數常數型別、溢位；一題 `volatile` 同時考抽象機器、可觀察行為、記憶體模型。前面 41 章是零件，這兩章是組裝。

另一個理由：**Jones 的標準答案有些是錯的**。題目 0x01 的答案編譯不過；「三元運算子產生較好的程式碼」與實測不符；「`volatile` 可用於多執行緒共享變數」在多核時代已經過時。拿一份寫於 C99 剛出版、8/16 位元 MCU 當道年代的考卷，用 C11/C23 規格書與今天的編譯器重新批改，正好練習本課最核心的能力：**不信權威，查規格、跑實驗**。

## 先建立直覺

把每一題都想成一個三層的洋蔥：

```
        ┌──────────────────────────────────────────┐
        │  第 3 層：真實系統 (Linux 核心 / RTOS / 韌體)│  ← 延伸
        │   ┌──────────────────────────────────┐    │
        │   │ 第 2 層：規格書 + 編譯器實際行為  │    │  ← 正解的依據
        │   │   ┌──────────────────────────┐   │    │
        │   │   │ 第 1 層：面試答案 (一行)  │   │    │  ← 常見錯答多死在這層
        │   │   └──────────────────────────┘   │    │
        │   └──────────────────────────────────┘    │
        └──────────────────────────────────────────┘
```

面試官聽到第 1 層會點頭，聽到第 2 層會追問，聽到第 3 層才會記住你。下面每題都照「題目 → 常見錯答 → 正解 → 規格依據 → 延伸」走一遍。

| 題號 | 主題 | 回看本課 |
|---|---|---|
| 0x01 | 一年有幾秒：巨集與整數常數型別 | Ch 22、Ch 27 |
| 0x02 | MIN 巨集：重複求值與型別檢查 | Ch 27、Ch 37 |
| 0x03 | `#error`：把錯誤提前到編譯時期 | Ch 27、Ch 28 |
| 0x04 | 無窮迴圈：終止假設與整數提升 | Ch 21、Ch 34 |
| 0x05 | 以 `a` 寫八個宣告 | Ch 8、Ch 9 |
| 0x06 | `static` 的三種意義 | Ch 11、Ch 39 |
| 0x07 | `const` 的位置與語意 | Ch 9、Ch 10 |
| 0x08 | `volatile` 三例與記憶體順序 | Ch 20、Ch 34 |

---

## 題目 0x01：一年有幾秒

> 用 `#define` 宣告一個常數，表示一年有多少秒（忽略閏年）。

Jones 想看三件事：`#define` 的語法、知道常數運算式在編譯時期算好、意識到 16 位元機器上會溢位。寫出 `UL` 有加分。

### 常見錯答

**錯答一**：Jones 自己的答案 `#define SECONDS_PER_YEAR (60 * 60 * 24 * 365)UL`。字尾只能黏在整數常數的字面上，黏在右括號後面就變成一個獨立的識別字 `UL`：

```
q1a.c:2:46: error: expected ‘)’ before ‘UL’
    2 | #define SECONDS_PER_YEAR (60 * 60 * 24 * 365)UL
      |                                              ^~
```

**錯答二**：`#define SECONDS_PER_YEAR (60 * 60 * 24 * 365UL)`。看起來對，但乘法由左而右結合，`60 * 60 * 24` 仍以 `int` 計算。在 32 位元 `int` 上 86400 沒事，在 16 位元 `int` 的 MCU 上已經溢位（`INT_MAX` 是 32767）。

### 正解

```c
#define SECONDS_PER_YEAR (60UL * 60 * 24 * 365)
```

字尾放在**第一個**運算元上，整條乘法鏈從一開始就在 `unsigned long`（至少 32 位元）中進行，31536000 小於 2^32，結果正確。

### 規格依據

- 無字尾的十進位常數，型別取 `int`、`long`、`long long` 中第一個裝得下的（C11 6.4.4.1 §5）。所以 `60` 是 `int`，`60 * 60` 也是 `int`。
- 標準只保證 `int` 至少 16 位元、`long` 至少 32 位元（C11 5.2.4.2.1）。
- 有號溢位是未定義行為（C11 6.5 §5）；無號運算依模 2^N 回繞，是定義良好的（C11 6.2.5 §9）。

> 整數常數的型別規則與溢位的語意，回看 [Ch 22 — integer overflow](./22-integer-overflow.md)；巨集為什麼要整個包括號，回看 [Ch 27 — 前置處理器 I](./27-preprocessor-basics.md)。

同一個錯誤在 32 位元 `int` 上也會發生，只要數字夠大。改算「一年的毫秒數」：

```c
#include <stdio.h>
#define MS_PER_YEAR (1000 * 60 * 60 * 24 * 365)
int main(void)
{
    long long ms = MS_PER_YEAR;
    printf("%lld\n", ms);
    return 0;
}
```

```
$ gcc q1b.c -o q1b && ./q1b
q1b.c:5:42: warning: integer overflow in expression of type ‘int’ results in ‘1471228928’ [-Woverflow]
1471228928
```

存進 `long long` 沒用，乘法早在 `int` 裡溢位了（clang 14 回報 `-Winteger-overflow`）。改成 `1000UL` 呢？在 x86-64 上 `long` 是 64 位元，正確；但用 `-m32` 編譯（`long` 只有 32 位元）看組合語言：

```
$ gcc -m32 -O2 -Wall -Wextra -S -o - q1c.c | grep movl
	movl	$1471228928, %eax
```

又是 1471228928，**而且沒有任何警告**——無號回繞是合法的，編譯器沒有理由抱怨。要 64 位元就寫 `1000ULL` 或 `UINT64_C(1000)`。（本機沒裝 32 位元 glibc，`-m32` 只能編到組合語言，不能連結執行。）

### 延伸：一年不只 31536000 秒

這個常數只對平年成立。閏年 31622400 秒；格里曆平均一年 365.2425 天 = 31556952 秒。拿它做日曆運算會出錯：

```c
#define _DEFAULT_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <time.h>

#define SECONDS_PER_YEAR (60UL * 60 * 24 * 365)

static void show(const char *label, time_t t)
{
    struct tm tm;
    gmtime_r(&t, &tm);
    printf("%-24s %04d-%02d-%02d %02d:%02d:%02d\n", label, tm.tm_year + 1900,
           tm.tm_mon + 1, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec);
}

int main(void)
{
    struct tm jan1 = {.tm_year = 2024 - 1900, .tm_mday = 1};
    time_t t = timegm(&jan1);
    show("2024-01-01 + 1 year:", t + SECONDS_PER_YEAR);
    struct tm next = {.tm_year = 2025 - 1900, .tm_mday = 1};
    show("tm_year + 1:", timegm(&next));
    printf("sizeof(time_t) = %zu\n", sizeof(time_t));
    show("INT32_MAX seconds:", (time_t) INT32_MAX);
    show("INT32_MAX + 1 seconds:", (time_t) ((int64_t) INT32_MAX + 1));
    show("(int32_t) wrapped:", (time_t) (int32_t) ((uint32_t) INT32_MAX + 1));
    return 0;
}
```

```
2024-01-01 + 1 year:     2024-12-31 00:00:00
tm_year + 1:             2025-01-01 00:00:00
sizeof(time_t) = 8
INT32_MAX seconds:       2038-01-19 03:14:07
INT32_MAX + 1 seconds:   2038-01-19 03:14:08
(int32_t) wrapped:       1901-12-13 20:45:52
```

2024 是閏年，加 `SECONDS_PER_YEAR` 停在 12 月 31 日。要「明年同一天」，正確做法是 `tm_year + 1` 後交給 `timegm()`/`mktime()` 正規化。最後一行是 **2038 年問題**：32 位元有號 `time_t` 最多 2^31−1 秒，除以 31556952 約 68.05 年，從 1970 起算落在 2038-01-19；多一秒就回繞成 −2^31，日期跳回 1901 年。glibc 2.34 起 32 位元平台可加 `-D_FILE_OFFSET_BITS=64 -D_TIME_BITS=64` 讓 `time_t` 變 64 位元（依原文，本機無 32 位元環境未實測）。還在出貨的 32 位元 MCU 與嵌入式 Linux，是這個問題最可能殘留的地方。

注意 `timegm()` 到 C23 才進標準（C23 7.29.2.4）。gcc 11 在 `-std=c17` 下沒有宣告它時只給警告 `implicit declaration of function ‘timegm’`，Docker 裡的 GCC 14 則直接報錯——GCC 14 把隱式函式宣告升級成錯誤了。C23 之前要定義 `_DEFAULT_SOURCE`。

### 延伸：Linux 核心怎麼在年月日與秒之間換算

核心把年月日換成秒的是 `kernel/time/time.c` 的 `mktime64()`，關鍵一招是**把二月挪到年底**：

```c
    /* 1..12 -> 11,12,1..10 */
    if (0 >= (int) (mon -= 2)) {
        mon += 12; /* Puts Feb last since it has leap day */
        year -= 1;
    }
    return ((((time64_t)
              (year/4 - year/100 + year/400 + 367*mon/12 + day) +
              year*365 - 719499
             )*24 + hour)*60 + min)*60 + sec;
```

為什麼這樣做？閏年只影響 2 月 29 日。把一年的起點移到 3 月，閏日就落在「第 12 個月」的最後，前 11 個月的天數與是否閏年無關。3 月到隔年 1 月的天數是 31、30、31、30、31、31、30、31、30、31、31，以「31、30、31、30、31」五個月為週期，平均 30.6 天，所以「第 `mon` 月之前累積的天數」= ⌊367·mon/12⌋ − 30。驗算一下：

```
$ python3 -c "print([367*m//12-30 for m in range(1,12)])"
[0, 31, 61, 92, 122, 153, 184, 214, 245, 275, 306]
```

正是 3 月起的累積天數。`year/4 - year/100 + year/400` 是到該年為止的閏日數。最後減掉的 719499 是 1970-01-01 代進括號的值（`mon`=11、`year`=1969），讓 Unix time 從 0 開始——同一行 python 算出來恰好是 719499。

反方向（秒 → 年月日）在 `kernel/time/timeconv.c`。舊版是 2009 年從 glibc 移植的「猜年份、修正、再猜」迴圈加查表；Linux v5.14 由 Cassio Neri 改寫，採用他與 Schneider 的論文〈Euclidean affine functions and their application to calendar algorithms〉的演算法：同樣把年初移到 3 月，用一次 64 位元乘法同時取出「世紀內年份」（高 32 位元）與「年內第幾天」（低 32 位元），再用 `2141 * day_of_year + 132377` 的高 16 位元得到月份（2141/2^16 ≈ 5/153，就是「每月 30.6 天」的定點數版本，回看 [Ch 26](./26-floating-point-errors.md) 的定點數）。

我們把二版與 `mktime64()` 搬到使用者空間，和 glibc 的 `gmtime_r()` 逐日比對，並量測一億次換算的時間：

<details>
<summary>點開完整比對程式（timeconv.c，約 150 行）</summary>

```c
#define _POSIX_C_SOURCE 200809L
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#define SECS_PER_DAY 86400

/* Linux kernel/time/time.c: mktime64() */
static int64_t mktime64(unsigned int year0, unsigned int mon0, unsigned int day,
                        unsigned int hour, unsigned int min, unsigned int sec)
{
    unsigned int mon = mon0, year = year0;

    /* 1..12 -> 11,12,1..10 */
    if (0 >= (int) (mon -= 2)) {
        mon += 12; /* Puts Feb last since it has leap day */
        year -= 1;
    }

    return ((((int64_t) (year / 4 - year / 100 + year / 400 + 367 * mon / 12 +
                         day) +
              year * 365 - 719499) * 24 + hour) * 60 + min) * 60 + sec;
}

/* Linux v5.13 kernel/time/timeconv.c (from glibc): days -> y/m/d */
static bool is_leap(long year)
{
    return year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
}

static long math_div(long a, long b)
{
    return a / b - (a % b < 0);
}

static long leaps_between(long y1, long y2)
{
    long leaps1 = math_div(y1 - 1, 4) - math_div(y1 - 1, 100) +
                  math_div(y1 - 1, 400);
    long leaps2 = math_div(y2 - 1, 4) - math_div(y2 - 1, 100) +
                  math_div(y2 - 1, 400);
    return leaps2 - leaps1;
}

static const unsigned short mon_yday[2][13] = {
    {0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334, 365},
    {0, 31, 60, 91, 121, 152, 182, 213, 244, 274, 305, 335, 366},
};

static void days_to_ymd_old(long days, struct tm *r)
{
    long y = 1970;
    while (days < 0 || days >= (is_leap(y) ? 366 : 365)) {
        long yg = y + math_div(days, 365);
        days -= (yg - y) * 365 + leaps_between(y, yg);
        y = yg;
    }
    r->tm_year = y - 1900;
    r->tm_yday = days;
    const unsigned short *ip = mon_yday[is_leap(y)];
    for (y = 11; days < ip[y]; y--)
        continue;
    r->tm_mon = y;
    r->tm_mday = days - ip[y] + 1;
}

/* Linux v5.14 kernel/time/timeconv.c (Neri-Schneider): days -> y/m/d */
static void days_to_ymd_neri(long days, struct tm *r)
{
    uint64_t udays = (uint64_t) days + 2305843009213814918ULL;

    uint64_t n = 4 * udays + 3;
    uint64_t century = n / 146097;
    uint32_t day_of_century = (uint32_t) (n % 146097 / 4);

    uint64_t m = 2939745ULL * (4 * day_of_century + 3);
    uint32_t year_of_century = (uint32_t) (m >> 32);
    uint32_t day_of_year = (uint32_t) m / 2939745 / 4;

    uint64_t year = 100 * century + year_of_century;
    bool leap = year_of_century ? !(year_of_century % 4) : !(century % 4);

    uint32_t t = 2141 * day_of_year + 132377;
    uint32_t month = t >> 16;
    uint32_t day = (uint16_t) t / 2141;

    bool jan_feb = day_of_year >= 306;
    year = year + jan_feb - 6313183731940000ULL;
    month = jan_feb ? month - 12 : month;
    day_of_year += jan_feb ? -306 : 31 + 28 + leap;

    r->tm_year = (long) (year - 1900);
    r->tm_mon = (int) month;
    r->tm_mday = (int) day + 1;
    r->tm_yday = (int) day_of_year;
}

static bool same_date(const struct tm *a, const struct tm *b)
{
    return a->tm_year == b->tm_year && a->tm_mon == b->tm_mon &&
           a->tm_mday == b->tm_mday && a->tm_yday == b->tm_yday;
}

static double bench(void (*f)(long, struct tm *), int bits, long base)
{
    struct tm r;
    long sum = 0;
    uint32_t x = 1;
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int i = 0; i < 100000000; i++) {
        x = x * 1664525 + 1013904223; /* LCG */
        f(base + (long) (x >> (32 - bits)), &r);
        sum += r.tm_mday;
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    if (sum == 42)
        puts("");
    return (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9;
}

int main(int argc, char *argv[])
{
    if (argc > 1 && !strcmp(argv[1], "bench")) {
        /* 2^21 days around 1970, or 2^14 days from 2026-01-01 */
        printf("wide: old %.3f s, neri %.3f s\n",
               bench(days_to_ymd_old, 21, -(1L << 20)),
               bench(days_to_ymd_neri, 21, -(1L << 20)));
        printf("near: old %.3f s, neri %.3f s\n",
               bench(days_to_ymd_old, 14, 20454),
               bench(days_to_ymd_neri, 14, 20454));
        return 0;
    }

    long mismatch = 0;
    for (long d = 0; d < 157054; d++) { /* every day in 1970..2399 */
        time_t t = (time_t) d * SECS_PER_DAY;
        struct tm g;
        gmtime_r(&t, &g);
        if (mktime64(g.tm_year + 1900, g.tm_mon + 1, g.tm_mday, 0, 0, 0) != t)
            mismatch++;
    }
    printf("mktime64, 1970..2399: %ld mismatches\n", mismatch);
    printf("23:59:60 == next 00:00:00: %d\n",
           mktime64(2016, 12, 31, 23, 59, 60) == mktime64(2017, 1, 1, 0, 0, 0));
    printf("24:00:00 == next 00:00:00: %d\n",
           mktime64(2016, 12, 31, 24, 0, 0) == mktime64(2017, 1, 1, 0, 0, 0));

    long bad_old = 0, bad_neri = 0;
    for (long d = -10000000; d <= 10000000; d++) {
        time_t t = (time_t) d * SECS_PER_DAY;
        struct tm g, a, b;
        gmtime_r(&t, &g);
        days_to_ymd_old(d, &a);
        days_to_ymd_neri(d, &b);
        bad_old += !same_date(&g, &a);
        bad_neri += !same_date(&g, &b);
    }
    printf("old vs gmtime_r: %ld mismatches, neri vs gmtime_r: %ld\n",
           bad_old, bad_neri);
    return 0;
}
```

</details>

```
$ gcc -O2 -Wall -Wextra timeconv.c -o timeconv && ./timeconv
mktime64, 1970..2399: 0 mismatches
23:59:60 == next 00:00:00: 1
24:00:00 == next 00:00:00: 1
old vs gmtime_r: 0 mismatches, neri vs gmtime_r: 0
$ ./timeconv bench                       # gcc 11 -O2
wide: old 3.104 s, neri 0.563 s
near: old 2.019 s, neri 0.642 s
$ ./timeconv_c bench                     # clang 14 -O2
wide: old 3.204 s, neri 0.216 s
near: old 2.008 s, neri 0.258 s
```

`mktime64()` 對 1970–2399 每一天都與 `gmtime_r()` 互為反函數；二版 `time64_to_tm` 在 1970 前後各一千萬天（約 27000 年）全部一致。效能上，舊版隨日期離 1970 越遠越慢（猜年份的迴圈多跑幾輪），新版與日期無關。本機近期日期下 gcc 版快約 3.1 倍、clang 版快約 7.8 倍（原文在 Threadripper 上是 gcc 3.8 倍、clang 8.6 倍，量級一致；這是單次量測，換 CPU 數字會變）。

閏秒呢？POSIX 的 "Seconds Since the Epoch" 公式每天固定 86400 秒，裡面那個 31536000 正是本題的 `SECONDS_PER_YEAR`——Unix time **不計閏秒**。`timegm()` 把 23:59:60 對應到隔天 00:00:00（下面是 gcc 11 + `_DEFAULT_SOURCE` 的實測，GCC 14 `-std=c23` 結果相同）：

```
tm_sec = 58 -> 1483228798 -> 2016-12-31 23:59:58
tm_sec = 59 -> 1483228799 -> 2016-12-31 23:59:59
tm_sec = 60 -> 1483228800 -> 2017-01-01 00:00:00
tm_sec = 61 -> 1483228801 -> 2017-01-01 00:00:01
```

閏秒真正發生時，核心 `kernel/time/ntp.c` 的 `second_overflow()` 讓 `CLOCK_REALTIME` **倒退一秒**（23:59:59 出現兩次）。「時間只會前進」的程式因此出事：2012 年閏秒觸發核心 hrtimer 缺陷，大量 Java/MySQL 程式忙碌迴圈；2017 年元旦 Cloudflare 的 Go DNS 服務把兩次 `time.Now()` 相減得到負值而 panic。教訓：**量測時間間隔要用 `CLOCK_MONOTONIC`**（上面的 `bench()` 就是）。國際度量衡大會已決議最遲 2035 年停止插入閏秒（CGPM 2022 Resolution 4）。

---

## 題目 0x02：MIN 巨集

> 寫一個「標準」的 `MIN` 巨集，接受兩個參數並傳回較小者。

Jones 的理由有二：當年 inline 還不是標準 C，巨集是唯一可攜的 inline 手段；三元運算子「讓編譯器產生比 if-else 更好的程式碼」。C99 已經有 `inline`，第一個理由不成立；第二個理由下面實測。

### 常見錯答

**錯答一：少括號。**

```c
#define SQUARE_BAD(x) (x * x)
#define SQUARE(x) ((x) * (x))
/* ... */
printf("SQUARE_BAD(10 - 5) = %d, SQUARE(10 - 5) = %d\n",
       SQUARE_BAD(10 - 5), SQUARE(10 - 5));
```

```
SQUARE_BAD(10 - 5) = -45, SQUARE(10 - 5) = 25
```

`SQUARE_BAD(10 - 5)` 展開成 `(10 - 5 * 10 - 5)`。

**錯答二：括號齊全，但參數求值兩次。** Jones 自己的答案：

```c
#define MIN(A, B) ((A) <= (B) ? (A) : (B))
int nums[] = {100, 200, 300, 400};
int n = 1000, *p = nums;
int least = MIN(*p++, n);   /* 第一次 */
n = 50; p = nums;
least = MIN(*p++, n);       /* 第二次 */
```

```
least = 200, p - nums = 2
least = 50, p - nums = 1
```

第一次條件成立（100 <= 1000），第二運算元的 `*p++` 又執行一次，拿到 200、`p` 前進兩格；第二次條件不成立，只執行一次。**副作用的次數取決於資料**。注意這不是未定義行為：`?:` 在第一運算元求值後有 sequence point（C11 6.5.15 §4），行為確定，只是不是呼叫端想要的。

### 正解

依需求選一個：

1. **型別固定時用 `static inline` 函式**：參數只求值一次、有型別檢查、能取位址。這是首選。
2. **要泛型時用 GNU statement expression + `typeof`**，並解決名稱捕獲。
3. **整數常數運算式（例如陣列大小）才用 K&R 形式**，且只傳常數。

GNU 版本長這樣：

```c
#define MIN(a, b)               \
    ({                          \
        typeof(a) _a = (a);     \
        typeof(b) _b = (b);     \
        _a <= _b ? _a : _b;     \
    })
int main(void)
{
    int nums[] = {100, 200, 300, 400};
    int n = 1000;
    int *p = nums;
    int least = MIN(*p++, n);
    printf("least = %d, p - nums = %td\n", least, p - nums);
    int _a = 3, x = 5;
    printf("MIN(_a, x) = %d\n", MIN(_a, x));
    return 0;
}
```

```
gcc -O0:   least = 100, p - nums = 1 MIN(_a, x) = 2
gcc -O2:   least = 100, p - nums = 1 MIN(_a, x) = 0
clang -O0: least = 100, p - nums = 1 MIN(_a, x) = 0
clang -O2: least = 100, p - nums = 1 MIN(_a, x) = -749199330
```

第一個問題解決了，但第二行揭露**名稱捕獲**：呼叫端剛好有變數叫 `_a`，展開成 `typeof(_a) _a = (_a);`，內層 `_a` 用尚未初始化的自己初始化——未定義行為，四種組合四種答案（clang -O2 的值每次執行可能不同）。只有 clang 在 `-Wall` 下警告 `variable '_a' is uninitialized when used within its own initialization`。Linux 核心 `include/linux/minmax.h` 因此用 `__UNIQUE_ID()` 產生不會撞名的暫存變數。`({ ... })` 至今仍是 GNU 擴充，`-std=c2x -pedantic` 會得到 `ISO C forbids braced-groups within expressions`；C23 只把 `typeof` 收進標準（C23 6.7.3.6）。

> statement expression、`typeof`、`_Generic` 的完整介紹在 [Ch 27](./27-preprocessor-basics.md)。

### 規格依據與延伸：核心如何在編譯時期擋下有號/無號混用

比大小時兩邊一個有號、一個無號，是比重複求值更陰險的錯誤。核心的 `min()` 用 `_Static_assert` 在編譯時期攔截。下面依 David Laight 2023 年的 commit 改寫：

```c
#include <stdio.h>

#define MIN(A, B) ((A) <= (B) ? (A) : (B))

#define __is_constexpr(x) \
    (sizeof(int) == sizeof(*(8 ? ((void *) ((long) (x) * 0l)) : (int *) 8)))
#define is_signed_type(type) (((type) (-1)) < (type) 1)
#define __is_signed(x)                                            \
    __builtin_choose_expr(__is_constexpr(is_signed_type(typeof(x))), \
                          is_signed_type(typeof(x)), 0)
#define __is_noneg_int(x) \
    (__builtin_choose_expr(__is_constexpr(x) && __is_signed(x), x, -1) >= 0)
#define __types_ok(x, y) \
    (__is_signed(x) == __is_signed(y) || __is_noneg_int(x) || __is_noneg_int(y))
#define min(x, y)                                                       \
    ({                                                                  \
        typeof(x) __x = (x);                                            \
        typeof(y) __y = (y);                                            \
        _Static_assert(__types_ok(x, y),                                \
                       "min(" #x ", " #y ") signedness error");         \
        __x < __y ? __x : __y;                                          \
    })

int main(void)
{
    int i = -1;
    unsigned int u = 1;
    printf("MIN(i, u) = %u\n", MIN(i, u));
    printf("min(i, 10) = %d\n", min(i, 10));
#ifdef MIXED
    printf("min(i, u) = %u\n", min(i, u));
#endif
    return 0;
}
```

```
$ gcc -Wall kmin.c -o kmin && ./kmin      # 0 個警告
MIN(i, u) = 1
min(i, 10) = -1
$ gcc -DMIXED kmin.c
kmin.c:19:9: error: static assertion failed: "min(i, u) signedness error"
```

`MIN(i, u)` 中 −1 比 1 小，卻印出 1：`i` 經一般算術轉換變成 `UINT_MAX`（題目 0x0C 詳談），比較結果整個顛倒，而 gcc/clang 在 `-Wall` 下**一個警告都沒有**，要 `-Wextra` 才有 `-Wsign-compare`。`min(i, 10)` 的 10 是非負的有號常數，放行；`min(i, u)` 在編譯時期就失敗（clang 14 會把展開後整條條件印出來，非常長）。

`__is_constexpr()` 是一個值得拆解的技巧：`(void *)((long)(x) * 0l)` 在 `x` 是整數常數運算式時是**空指標常數**，`?:` 的結果型別取另一邊的 `int *`，`sizeof(*...)` 為 `sizeof(int)`；否則它只是普通 `void *`，結果型別變 `void *`，`sizeof(void)` 在 GNU C 是 1。用指標型別規則（C11 6.5.15 §6）偵測「是不是常數」。

### 延伸：三元運算子真的產生比較好的碼嗎？

```c
int max_if(int a, int b)      { if (a > b) return a; else return b; }
int max_ternary(int a, int b) { return (a > b) ? a : b; }
```

gcc 11 `-O2`（Intel 語法）：

```
max_if:
	cmp	edi, esi
	mov	eax, esi
	cmovge	eax, edi
	ret
max_ternary:
	cmp	esi, edi
	mov	eax, edi
	cmovge	eax, esi
	ret
```

一模一樣（只差運算元順序），Arm64 上都是 `cmp` + `csel`。只有 gcc `-O0` 讓兩者不同（`max_if` 用條件跳躍、`max_ternary` 用 `cmovge`）。最佳化器看的是控制流程圖，不是語法；把分支換成條件搬移叫 **if-conversion**（回看 [Ch 37](./37-compiler-optimization.md)）。

條件搬移也不必然比分支快。原文的實驗：迴圈一億次，`n1` 從亂數表或遞增序列取值。本機 gcc 11 `-O2`、`taskset -c 2`、各三次：

| 寫法 / 資料 | 亂數（分支難預測） | 遞增（分支好預測） |
|---|---|---|
| gcc `if`（保留分支） | 0.203 / 0.205 / 0.202 s | 0.051 / 0.050 / 0.057 s |
| gcc `?:`（`cmov`） | 0.059 / 0.059 / 0.074 s | 0.048 / 0.048 / 0.054 s |
| clang 兩種寫法（都用 `cmov`） | 0.040–0.052 s | 0.040–0.050 s |

gcc 11 讓語法影響了程式碼：`if` 版本留下分支，亂數資料下約一半預測失準，慢了 3 倍多。原文在 Threadripper 2990WX 上還觀察到遞增資料時 `cmov` 反而較慢（`cmov` 把控制相依變成資料相依，拉長迴圈攜帶的相依鏈）；本機 Zen 3 上兩者在遞增資料差不多。結論不變：**效能取決於資料能否預測與編譯器的選擇，不取決於你寫 `?:` 還是 `if`**；在乎就看組合語言、用真實資料量。

---

## 題目 0x03：`#error` 的用途

> 前置處理器指示詞 `#error` 的用途是什麼？

Jones 說這題「你要嘛知道、要嘛不知道」，用來分辨會讀教科書附錄的人。但它切中嵌入式實況：同一份原始碼用條件編譯支援多種硬體組態，組態錯誤拖到燒錄後才發現，代價是一整輪除錯。

### 常見錯答

- 「`#error` 會在執行時期印出錯誤」——錯，它在**轉譯時期**讓前置處理器終止編譯（C11 6.10.5）。
- 「用 `#if sizeof(int) == 4` 檢查型別大小」——前置處理器只懂語彙單元與整數，不認識型別：

```
sz.c:1:11: error: missing binary operator before token "("
```

### 正解

`#error` 搭配 `#if` 檢查**組態與巨集**；型別大小、結構配置交給 `_Static_assert`（C11 6.7.10；C23 改為關鍵字 `static_assert`）。環形緩衝區用 `idx & (SIZE - 1)` 取代取餘數，前提是 `SIZE` 為 2 的冪：

```c
#ifndef RX_BUF_SIZE
#define RX_BUF_SIZE 100
#endif

#if RX_BUF_SIZE <= 0 || (RX_BUF_SIZE & (RX_BUF_SIZE - 1)) != 0
#error "RX_BUF_SIZE must be a power of 2"
#endif

static unsigned char rx_buf[RX_BUF_SIZE];

unsigned char rx_get(unsigned int idx)
{
    return rx_buf[idx & (RX_BUF_SIZE - 1)];
}
```

```
$ gcc -c rx.c
rx.c:6:2: error: #error "RX_BUF_SIZE must be a power of 2"
$ gcc -c -DRX_BUF_SIZE=128 rx.c && echo ok
ok
```

沒有這道檢查會怎樣？100 的遮罩 99（二進位 `1100011`）只會產生 16 種不同索引（`python3 -c "print(len(set(i & 99 for i in range(1000))))"` 印出 16），緩衝區一部分永遠用不到、另一部分被重複覆寫——程式不會崩潰，只會偶爾資料錯亂，最難查的那種錯。（`x & (x-1)` 清掉最低位的 1，回看 [Ch 24](./24-bitwise-tricks.md)。）

型別配置的檢查：

```c
struct frame {
    uint8_t type;
    uint32_t len;
};
_Static_assert(sizeof(struct frame) == 5, "struct frame must be packed");
```

```
frame.c:13:1: error: static assertion failed: "struct frame must be packed"
```

`len` 要對齊 4 位元組，中間有 3 位元組 padding，大小是 8（回看 [Ch 16](./16-alignment-and-padding.md)）。

### 延伸：常數要等 inline 之後才確定怎麼辦

`#error` 與 `static_assert` 只能看常數運算式。很多條件要等函式 inline 展開、參數變常數後才確定。Linux 核心的 `BUILD_BUG_ON()` 底層的 `__compiletime_assert()` 用另一招：宣告一個帶 `__attribute__((error(msg)))` 的外部函式，只在條件不成立時呼叫；最佳化器證明條件成立就刪掉呼叫，否則編譯失敗：

```c
#define compiletime_assert(cond, msg)                               \
    do {                                                            \
        extern void compiletime_fail(void) __attribute__((error(msg))); \
        if (!(cond))                                                \
            compiletime_fail();                                     \
    } while (0)

#define SYSCLK_HZ 25000000u
volatile unsigned int systick_reload;

static inline void systick_init(unsigned int tick_hz)
{
    compiletime_assert(SYSCLK_HZ % tick_hz == 0,
                       "tick rate must divide the system clock");
    systick_reload = SYSCLK_HZ / tick_hz - 1;
}

void board_init(void)
{
    systick_init(1000);
#ifdef BAD_RATE
    systick_init(3000);
#endif
}
```

```
gcc -O2:             ok
gcc -O2 -DBAD_RATE:  error: call to ‘compiletime_fail’ declared with attribute error: tick rate must divide the system clock
gcc -O0:             error: call to ‘compiletime_fail’ declared with attribute error: ...
clang -O2 -DBAD_RATE: error: call to compiletime_fail declared with 'error' attribute: ...
clang -O0:           error: call to compiletime_fail declared with 'error' attribute: ...
```

25 MHz 不能被 3000 整除，被抓到。但 `-O0` 下連 1000 也報錯——沒有最佳化就不會刪掉呼叫。所以核心只在定義 `__OPTIMIZE__` 時啟用它，否則退化成不檢查。這是一個**依賴最佳化器的正確性工具**，要知道它的邊界。

C23 另外把 GCC 早就支援的 `#warning` 收進標準（C23 6.10.7），用於提醒但不中止編譯的情況。

---

## 題目 0x04：如何寫無窮迴圈

> 嵌入式系統常用到無窮迴圈，C 怎麼寫？

Jones 偏好 `while (1)`；答 `for (;;)` 他會追問理由，聽到「老師這樣教」就扣分。這題考的是「有沒有想過自己的慣用寫法」。

### 常見錯答

- 「`while (1)` 和 `for (;;)` 一個會被最佳化掉、一個不會」——錯。`for` 省略的第二運算式視同非零常數（C11 6.8.5.3 §2），兩者等價。K&R 慣用 `for (;;)`；某些 lint（例如 MSVC 舊版的 C4127）會對 `while (1)` 的常數條件警告。
- 「等待旗標用 `while (!ready);` 就是無窮迴圈的應用」——這才是本題真正的坑。

### 正解

`while (1) { ... }`、`for (;;) { ... }`、`loop: ... goto loop;` 都行，重點在**迴圈條件依賴程式之外的變化時**要讓編譯器知道。C11 6.8.5 §6 允許實作假設：控制運算式**不是常數**、且迴圈內不做 I/O、不存取 `volatile`、不做同步或 atomic 操作的迴圈**終將結束**。`while (1)` 是常數條件，不受影響；忙碌等待旗標就受影響了：

```c
int ready;
volatile int vready;

void wait_plain(void)    { while (!ready) ; }
void wait_volatile(void) { while (!vready) ; }
```

```
== gcc -O2                    == clang -O2 (預設 gnu17)      == clang -O2 -std=c99
wait_plain:                   wait_plain:                    wait_plain:
  mov eax, DWORD PTR ready      ret                            cmp dword ptr [rip + ready], 0
  test eax, eax                                                je .LBB0_1
  jne .L5                                                      ret
.L4:                                                         .LBB0_1:
  jmp .L4                                                      jmp .LBB0_1
.L5:
  ret
```

gcc 只讀一次 `ready`，若為 0 就跳進一個**不再讀記憶體**的死迴圈——ISR 之後設的旗標永遠看不到。clang 14 更進一步：依 C11 的終止假設把整個迴圈刪掉，`wait_plain` 只剩 `ret`；改用 `-std=c99`（C99 沒有這條假設）才保留死迴圈。兩個編譯器對 `wait_volatile` 都每次重讀 `vready`。這就是題目 0x08 的主題。

> 終止假設屬於「編譯器根據規格推論」的一類，見 [Ch 34 — 未定義行為](./34-undefined-behavior.md)。

### 延伸：整數提升造成的非預期無窮迴圈

8 位元 MCU 上，工程師常用 `uint8_t` 當迴圈計數器省暫存器：

```c
#include <stdint.h>
#include <stdio.h>

static uint8_t table[256];

int main(void)
{
    unsigned long iterations = 0;
    for (uint8_t i = 0; i < sizeof table; i++) {
        table[i] = i;
        if (++iterations > 1000) {
            printf("still looping after %lu iterations\n", iterations - 1);
            return 1;
        }
    }
    printf("done in %lu iterations\n", iterations);
    return 0;
}
```

```
$ gcc -Wall u8loop.c -o u8 && ./u8          # -Wall 沒有警告
still looping after 1000 iterations
$ gcc -Wall -Wextra u8loop.c
warning: comparison is always true due to limited range of data type [-Wtype-limits]
$ clang u8loop.c                            # 不加旗標就警告
warning: result of comparison of constant 256 with expression of type 'uint8_t' (aka 'unsigned char') is always true [-Wtautological-constant-out-of-range-compare]
```

`uint8_t` 永遠 ≤ 255，`i < 256` 恆真，`i++` 在 255 後回繞成 0。拿掉保護的版本，gcc `-O2` 直接把比較刪掉：

```
fill:                              ; x86-64            fill (aarch64):
	xor	eax, eax                                      .L2:
.L2:                                                    add  w1, w0, 1
	movzx	edx, al                                     strb w0, [x2, w0, sxtw]
	mov	BYTE PTR [rcx+rdx], al                          and  w0, w1, 255
	add	eax, 1                                          b    .L2
	jmp	.L2
```

迴圈尾巴是無條件跳躍，`movzx`/`and w0, w1, 255` 把計數器截回 8 位元。在有看門狗的產品上，這類錯誤表現為「裝置週期性重開機」而不是當機。（整數提升規則見 [Ch 21](./21-number-systems.md)。）

### 延伸：RTOS 為何仍需要無窮迴圈

有了 RTOS，無窮迴圈沒有消失，而是從 `main` 搬到每個 task 裡：

```
  裸機 superloop                    thread-per-task (FreeRTOS)        run-to-completion (QP)
  ┌────────────────────┐           ┌─────────────────────────┐       ┌───────────────────────┐
  │ main(): for (;;) { │           │ task A: for (;;) {      │       │ 框架 dispatch loop:    │
  │   poll_inputs();   │◄─旗標─ISR │   xQueueReceive(...,    │       │ for (;;) { 取 event;  │
  │   run_fsm();       │           │       portMAX_DELAY);   │       │   dispatch(ao, e); }  │
  │   __WFI();         │           │   handle(); }           │       │ event handler 處理完   │
  │ }                  │           │ 每個 task 各一份 stack  │       │ 即返回，共用一份 stack │
  └────────────────────┘           └─────────────────────────┘       └───────────────────────┘
```

FreeRTOS 的 Cortex-M 移植層把 task 初始堆疊框的返回位址設為 `prvTaskExitError()`，註解寫明 task 函式「must not exit or attempt to return to its caller as there is nothing to return to」；要結束 task 得呼叫 `vTaskDelete(NULL)`。沒有阻塞點的高優先權迴圈會餓死所有低優先權 task 與 idle task。superloop 圖中省略了「遺失喚醒」的處理：ISR 若在檢查旗標之後、`__WFI()` 之前設旗標，主迴圈仍會睡下去；正確寫法是關中斷檢查旗標再 `__WFI()`（Cortex-M 在 PRIMASK 遮蔽時仍會被待處理中斷喚醒）。Ch 43 題目 0x0B 有一個使用者空間的對照實驗。

---

## 題目 0x05：以變數 `a` 寫出宣告

> 一個整數；指向整數的指標；指向整數指標的指標；十個整數的陣列；十個整數指標的陣列；指向十個整數之陣列的指標；指向「接受整數、傳回整數」之函式的指標；十個這種函式指標的陣列。

Jones 承認幾個宣告要查書，但「面試都不準備，你還會準備什麼？」

### 常見錯答

最常錯的是第 5、6 項互換（`int *a[10]` 和 `int (*a)[10]`），以及第 8 項把括號放錯位置寫成 `int *(a[10])(int)`（那是「函式的陣列」，不合法）。

### 正解

```c
int a;
int *a;
int **a;
int a[10];
int *a[10];
int (*a)[10];
int (*a)(int);
int (*a[10])(int);
```

### 規格依據：怎麼讀

從識別字出發，**後置的 `[]` 與 `()` 比前置的 `*` 結合得緊**，括號改變結合（C11 6.7.6 declarators）。`int *a[10]`：`a` 先和 `[10]` 結合 → 10 個元素的陣列，元素是 `int *`。`int (*a)[10]`：括號讓 `a` 先和 `*` 結合 → 指標，指向 `int[10]`。C 的宣告模仿使用方式：`int *a[10]` 表示運算式 `*a[i]` 的型別是 `int`。指標算術以所指型別為單位，`int (*p)[10]` 做 `p++` 前進 `sizeof(int[10])` = 40 位元組。最後一項實務上用 `typedef` 拆開：

```c
typedef int (*handler_t)(int);
handler_t a[10];
```

> 宣告符的完整讀法、array decay，回看 [Ch 8](./08-arrays-functions-pointers.md)；函式指標與 callback 回看 [Ch 9](./09-void-pointer-and-callbacks.md)。

### 延伸：八個宣告在韌體中的用途

下面這支程式把八種宣告各用一次，對應真實用途：

<details>
<summary>點開 decl.c（八種宣告各用一次，約 75 行）</summary>

```c
#include <stdio.h>

/* 1. int a: an event counter */
static int overruns;

/* 2. int *a: output parameter, the caller owns the storage */
static int read_adc(int channel, int *value)
{
    if (channel < 0 || channel > 3)
        return -1;
    *value = 100 * channel + 7;
    return 0;
}

/* 3. int **a: let the callee advance the caller's cursor */
static int take(int **cursor)
{
    return *(*cursor)++;
}

/* 4. int a[10]: a fixed-size sample buffer */
static int samples[10];

/* 5. int *a[10]: per-channel pointers into separate storage */
static int ch0, ch1, ch2;
static int *channel_value[10] = {&ch0, &ch1, &ch2};

/* 6. int (*a)[10]: ping-pong buffers, switch rows without copying */
static int dma_buf[2][10];

/* 7. int (*a)(int): a pluggable filter */
static int halve(int x) { return x / 2; }
static int clamp(int x) { return x > 250 ? 250 : x; }

/* 8. int (*a[10])(int): a command dispatch table */
static int cmd_reset(int arg) { return printf("reset(%d)\n", arg); }
static int cmd_blink(int arg) { return printf("blink(%d)\n", arg); }
static int (*commands[10])(int) = {cmd_reset, cmd_blink};

int main(void)
{
    int v;
    if (read_adc(2, &v) == 0)
        printf("adc[2] = %d\n", v);
    if (read_adc(9, &v) != 0)
        overruns++;

    for (int i = 0; i < 10; i++)
        samples[i] = i * i;
    int *cursor = samples;
    int first = take(&cursor);
    int second = take(&cursor);
    printf("take: %d %d, cursor at index %td\n", first, second, cursor - samples);

    for (int i = 0; i < 3; i++)
        *channel_value[i] = 10 * i;
    printf("ch2 = %d, overruns = %d\n", ch2, overruns);

    int (*filling)[10] = &dma_buf[0], (*draining)[10] = &dma_buf[1];
    (*filling)[0] = 42;
    int (*tmp)[10] = filling;
    filling = draining;
    draining = tmp;
    printf("draining[0] = %d, sizeof *draining = %zu\n", (*draining)[0],
           sizeof *draining);

    int (*filter)(int) = halve;
    printf("halve(600) = %d, ", filter(600));
    filter = clamp;
    printf("clamp(600) = %d\n", filter(600));

    for (int op = 0; op < 3; op++) {
        if (commands[op])
            commands[op](op);
        else
            printf("command %d not implemented\n", op);
    }
    return 0;
}
```

</details>

```
$ gcc -std=c11 -Wall -Wextra -Werror decl.c -o decl && ./decl    # clang 14 同樣零警告
adc[2] = 207
take: 0 1, cursor at index 2
ch2 = 20, overruns = 1
draining[0] = 42, sizeof *draining = 40
halve(600) = 300, clamp(600) = 250
reset(0)
blink(1)
command 2 not implemented
```

`take(&cursor)` 後 `cursor` 前進兩格：被呼叫端改的是呼叫端的指標。`*draining` 大小 40：一整列 `int[10]`，交換 `filling`/`draining` 不搬任何資料（DMA 雙緩衝）。`commands` 只初始化前兩項，其餘依規則為空指標（C11 6.7.9 §10），**分派前必須檢查**——Cortex-M 上呼叫空指標通常觸發 HardFault。Cortex-M 的中斷向量表就是第 8 種宣告的變形 `void (*const vector_table[])(void)`，以 `const` 放在 flash、用指定初始化子把處理常式放到架構規定的索引。

### 延伸：三個與宣告有關的坑

**空括號在 C 與 C++ 不同。** C23 之前 `int f();` 表示「參數未指定」：

```c
int f();
int g(void) { return f(1, 2); }
```

gcc 11 `-std=c17 -Wall -Wextra` 零警告（要 `-Wstrict-prototypes` 才有 `function declaration isn’t a prototype`）；clang 14 也沒警告（原文說 clang 20 會警告，那是較新版本才加的）。C23 把無參數宣告改成等同 `(void)`（C23 6.7.7.4 §13），GCC 14 `-std=c23` 報錯 `too many arguments to function 'f'`。

**二維陣列不能當指標的指標傳。** `int a[5][10]` decay 成 `int (*)[10]`，不是 `int **`：

```
p2d.c:6:12: warning: passing argument 1 of ‘pass2d’ from incompatible pointer type [-Wincompatible-pointer-types]
p2d.c:1:19: note: expected ‘int **’ but argument is of type ‘int (*)[10]’
```

gcc 11 只是警告，GCC 14 起這是**錯誤**。正確參數型別是 `int (*array)[10]` 或 `int array[][10]`。

**陣列參數會被調整成指標**（C11 6.7.6.3 §7），所以 `void print_size(int arr[10])` 裡 `sizeof(arr)/sizeof(arr[0])` 印出 2（`sizeof(int *)/sizeof(int)`），gcc 預設就警告 `‘sizeof’ on array function parameter ‘arr’ will return size of ‘int *’`。**函式內配置的空間要讓呼叫端接住**：`void new_array(int *local, int size) { local = malloc(...); }` 改的是副本，呼叫端仍是 `(nil)`，ASan 的 LeakSanitizer 回報 `40 byte(s) leaked in 1 allocation(s)`。C 只有傳值呼叫，要改呼叫端的指標就傳 `int **`（回看 [Ch 18](./18-linked-list-pointer-to-pointer.md)）。

---

## 題目 0x06：`static` 的作用

> `static` 關鍵字的作用是什麼？

Jones 觀察到多數人答得出第一種、不少人答得出第二種，答得出第三種（限制函式的可見範圍）的「少得可憐」。

### 常見錯答

- 「`static` 讓變數放在 stack 以外」——只說對一半，而且混淆了 storage duration 與 linkage。
- 只答「函式內的 static 變數會保留值」，漏掉檔案範圍的 internal linkage。

### 正解

`static` 的效果取決於宣告位置，一個關鍵字承擔兩種職責：

| 位置 | 改變的是 | 效果 |
|---|---|---|
| 函式內區域變數 | **storage duration** | 物件存在整個程式執行期間，值在呼叫間保留；scope 仍是區塊，沒有 linkage |
| 檔案範圍的變數或函式 | **linkage** | internal linkage：其他 translation unit 無法用名稱連到它（但仍可經指標存取） |
| 陣列參數 `[static N]` | 對呼叫端的承諾 | 指標非空且至少指向 N 個元素（C99 起） |

### 規格依據

三個不同層次的概念：**scope**（編譯時期，名稱在哪裡可見，C11 6.2.1）、**storage duration**（執行時期，物件活多久，C11 6.2.4）、**linkage**（不同 scope 或 translation unit 的同名識別字是否指同一實體，C11 6.2.2）。translation unit 是一個原始檔展開所有 `#include` 後的整體（C11 5.1.1.1）。區域 `static` 變數的初始化式必須是常數運算式（C11 6.7.9 §4），在程式開始前就初始化——C 沒有 C++ 那種「第一次執行到才初始化」，也就沒有對應的執行緒安全保護。

從目的檔觀察物件放在哪：

```c
int global_init = 100;
int global_uninit;
static int static_global_init = 200;
static int static_global_uninit;

int f(void)
{
    static int static_local_init = 400;
    static int static_local_uninit;
    return global_init + global_uninit + static_global_init +
           static_global_uninit + static_local_init++ + static_local_uninit;
}
```

```
$ gcc -c st.c && nm st.o
0000000000000000 T f
0000000000000000 D global_init
0000000000000000 B global_uninit
0000000000000004 d static_global_init
0000000000000004 b static_global_uninit
0000000000000008 d static_local_init.1
0000000000000008 b static_local_uninit.0
```

有初值的在 `.data`（`D`/`d`），沒有的在 `.bss`（`B`/`b`）。大小寫是 ELF 符號的 binding：大寫全域、小寫區域。區塊內的 `static` 變數多了 `.1`、`.0` 字尾，避免和其他函式的同名變數衝突。`int global_uninit;` 是 **tentative definition**（C11 6.9.2）；GCC 10 起預設 `-fno-common`，兩個檔案各寫 `int count;` 會連結失敗：

```
/usr/bin/ld: ...: multiple definition of `count'; ...: first defined here
```

加 `-fcommon` 才回到舊行為（`nm` 顯示 `C`）。

> 區段、符號表、COMMON，回看 [Ch 39 — 靜態連結](./39-static-linking.md)；`.data`/`.bss` 在行程中的位置回看 [Ch 11](./11-process-layout-and-abi.md)。

### 延伸：`static` 讓編譯器看見全部使用者

編譯器一次只看一個 translation unit。沒有 `static` 的符號可能被別人呼叫或修改，編譯器必須保留定義，也不能把變數初值當常數：

```c
#ifdef USE_STATIC
#define LINKAGE static
#else
#define LINKAGE
#endif

LINKAGE int scale(int x) { return x * 3 + 1; }
LINKAGE int threshold = 100;

int over(int v) { return scale(v) > threshold; }
```

```
== x86-64 gcc -O2 (無 static)          == x86-64 gcc -O2 -DUSE_STATIC
over:                                   over:
	lea eax, [rdi+rdi*2]                  lea eax, [rdi+rdi*2]
	cmp DWORD PTR threshold[rip], eax     cmp eax, 99
	setle al                              setg al
.text 36 B, .data 4 B, 3 個全域符號      .text 17 B, .data 0 B, 只剩 over
```

加上 `static` 後 `scale` 完全 inline、`threshold` 摺疊成立即值，比較變成 `cmp eax, 99`（3x+1 > 100 ⇔ 3x > 99）。aarch64 上 `.text` 從 40 降到 16 位元組。一個函式省 20 位元組不多，但韌體由數百個模組組成，flash 只有數十 KiB 時這會累積成「放不放得進晶片」。

LTO（link-time optimization）把可見範圍擴大到整個程式：

```
$ cat util.c   → scale() 與 unused_helper()，都沒有 static
$ cat lmain.c  → main() 呼叫 scale()
gcc [-O2]:                                       scale unused_helper
gcc [-O2 -ffunction-sections -Wl,--gc-sections]: scale
gcc [-O2 -flto]:                                 (兩者都消失：scale 被 inline 進 main)
clang：三種組態結果相同
```

LTO 的反面：只被硬體或連結器腳本參照的物件也會被當沒用刪掉。以 section 收集的註冊表就是例子：

```c
#ifdef USE_USED
#define CMD_ATTR __attribute__((used, section(".cmds")))
#else
#define CMD_ATTR __attribute__((section(".cmds")))
#endif
static const struct cmd cmd_reset CMD_ATTR = {"reset", do_reset};
```

gcc 與 clang `-O2` 下，沒有 `used` 時 `.cmds` section 整個消失，加了才保留。Linux 核心的 initcall 正是這樣定義，`__used` 不可省略。原文另一個例子：中斷向量表拿掉 `used` 後加 `-flto`，整張表消失，QEMU 重置時讀不到初始堆疊指標（依原文，本機無 Arm 裸機工具鏈未實測）。

裸機韌體靜態連結成單一映像檔，沒有動態連結器替你隔離符號：廠商 SDK、RTOS、協定堆疊與應用程式共用一個全域命名空間。**把模組內部的東西宣告為 `static`，是單一映像檔裡唯一的命名空間隔離手段**。

### 延伸：兩個與生命週期有關的錯誤

```c
int accumulate(int max)
{
    int sum;                    /* 沒有初始化 */
    for (int num = 1; num <= max; num++)
        sum += num;
    return sum;
}
```

| 編譯器 | `-O0 -Wall` | `-O2 -Wall` |
|---|---|---|
| gcc 11 | 無警告 | `‘sum’ may be used uninitialized` |
| clang 14 | `variable 'sum' is uninitialized when used here` | 同左 |

gcc 的這項分析依賴最佳化階段的資料流，`-O0` 抓不到。讀取位址從未被取用的未初始化自動變數是未定義行為（C11 6.3.2.1 §2）。

傳回區域陣列的位址：

```c
char *getstr(const char *name)
{
    char buf[30] = "hello, ";
    strcat(buf, name);
    return buf;
}
```

gcc 警告 `function returns address of local variable` 並在 `-O0` 與 `-O2` 下都**改成傳回空指標**（印出 `(nil)`），讓錯誤的存取立即失敗；clang 警告 `-Wreturn-stack-address` 但真的傳回 stack 位址（印出 `0x7ffd...`，每次不同），後續讀到的是被覆寫的內容。

---

## 題目 0x07：`const` 的作用

> 以下宣告分別代表什麼？
> ```c
> const int a;
> int const a;
> const int *a;
> int * const a;
> int const * a const;
> ```

### 常見錯答

- 「`const int *a` 是常數指標」——錯，它是指向唯讀 `int` 的指標，`a` 本身可以改。
- 「第五行是指向唯讀 int 的唯讀指標」——意圖對，但**它編譯不過**，限定詞不能寫在識別字之後：

```
cq.c:3:18: error: expected ‘;’ before ‘const’
```

- 「`const` 代表常數」——在 C 裡，`const` 代表「唯讀」，不代表常數運算式（見下）。

### 正解

前兩行相同（唯讀 `int`）。`const int *a`：`a` 可改指他處，但不能經由 `a` 寫入。`int * const a`：`a` 不能再改指，但可經由它寫入。第五行應寫 `int const * const a`。讀法：**`const` 修飾緊鄰其左側的型別成分；在最左邊時修飾右側的基本型別**。

### 規格依據

`const int *p` 只限制**經由 `p` 這條存取路徑**寫入，物件本身可能經別的路徑改變——它是 API 契約，不是硬體保證。反過來，經由去掉 `const` 的指標修改**定義為 `const` 的物件**是未定義行為（C11 6.7.3 §6）：

```c
int main(void)
{
    const int n = 100;
    int *ptr = (int *) &n;
    *ptr = 999;
    printf("n = %d, *ptr = %d\n", n, *ptr);
    return 0;
}
```

```
gcc -O0:   n = 999, *ptr = 999
gcc -O2:   n = 100, *ptr = 999
clang -O0: n = 100, *ptr = 999
clang -O2: n = 100, *ptr = 999
```

改成檔案範圍的 `const int limit = 100;`，它會進唯讀的 `.rodata`：gcc（`-O0`/`-O2`）與 clang `-O0` 寫入時 `Segmentation fault`（rc=139），clang `-O2` 則把寫入刪掉、正常印出 `limit = 100`。同一段程式三種結局——這就是 UB。拿掉轉型直接寫 `int *p = &n;` 只得到警告（gcc `-Wdiscarded-qualifiers`、clang `-Wincompatible-pointer-types-discards-qualifiers`），不是錯誤。

C 的 `const` 物件不是常數運算式（C++ 才是）：

```c
const int N = 10;
int table[N];
```

```
gcc:   error: variably modified ‘table’ at file scope
clang: warning: variable length array folded to constant array as an extension [-Wgnu-folding-constant]
```

這是 C 長期用 `#define` 與 `enum` 定義常數的原因。C23 的 `constexpr` 終於解決：

```c
constexpr int N = 10;
int table[N];
/* switch case N: 也可以用 */
```

GCC 14 `-std=c23 -Wall -pedantic` 編譯通過，印出 `N is a constant expression`。

### 延伸：嵌入式為什麼在乎 `const`

RAM 稀少。`const uint8_t crc_table[256]` 放 `.rodata`，連結後留在 flash；不加 `const` 的表放 `.data`，初值存 flash、開機還要複製一份到 RAM，白白吃掉 256 位元組 RAM。但這取決於架構：哈佛架構的 AVR（多數型號）上唯讀資料預設仍在 RAM，要用 `__flash` 或 `progmem` 才放 flash（依 GCC 文件，未實測）。

字串字面值也是一樣的道理：

```c
char *pc = "john";
*pc = 'J';          /* UB：修改字串字面值 (C11 6.4.5 §7) */
```

gcc 與 clang `-O0 -Wall` 都**沒有警告**，執行時 `Segmentation fault`。C 的字串字面值型別是 `char[N]`（C++ 是 `const char[N]`），所以 `char *pc = "john"` 合法；加 `-Wwrite-strings` 才得到 `initialization discards ‘const’ qualifier`。指向字面值的指標應宣告為 `const char *`，要修改就宣告陣列 `char pc[] = "john";`。（回看 [Ch 10](./10-strings-and-null.md)。）

---

## 題目 0x08：`volatile` 的作用，舉三個例子

> `volatile` 的意義為何？舉三個不同的使用例子。

Jones 把這題視為區分 C 程式設計者與嵌入式程式設計者「最根本的問題」，答不出來不錄取。他的三個例子：周邊裝置的硬體暫存器、ISR 存取的非自動變數、多執行緒共享的變數。**前兩個至今成立，第三個已經過時。**

### 常見錯答

- 「`volatile` 讓存取變成 atomic」——錯。
- 「`volatile` 可以用來同步多執行緒」——錯，它不建立執行緒間的記憶體順序。
- 「`volatile` 告訴編譯器不要最佳化這個變數」——太含糊，面試官會追問「哪些最佳化」。

### 正解

`volatile` 告訴編譯器：這個物件可能以程式看不到的方式改變。對 `volatile` 物件的存取屬於**可觀察行為**（C11 5.1.2.3 §6），編譯器必須依抽象機器逐次執行，不能省略、合併或快取在暫存器；「一次存取」如何對應到硬體由實作定義（C11 6.7.3 §7）。三個正確的例子：

1. 記憶體映射 I/O（MMIO）的裝置暫存器（Ch 43 題目 0x0A 詳談）
2. ISR 與主程式共用的旗標（題目 0x04 的 `wait_volatile`）
3. `setjmp` 之後要保留值的區域變數（C11 7.13.2.1 §3；回看 [Ch 12](./12-setjmp-and-coroutines.md)），或 signal handler 寫入的 `volatile sig_atomic_t`

多執行緒共享資料應使用 `<stdatomic.h>` 或 lock。Linux 核心文件〈Why the "volatile" type class should not be used〉說明核心的同步原語已包含必要的 barrier，需要單次存取語意時用 `READ_ONCE()`/`WRITE_ONCE()`。

### 規格依據：`volatile` 不等於 atomic

```c
volatile unsigned int ticks;
void tick_isr(void) { ticks++; }
```

```
tick_isr:                          ; x86-64 gcc -O2        aarch64 gcc -O2
	mov	eax, DWORD PTR ticks[rip]                       ldr w0, [x1, ...]
	add	eax, 1                                          add w0, w0, 1
	mov	DWORD PTR ticks[rip], eax                       str w0, [x1, ...]
```

讀、改、寫三道指令。主程式若也執行 `ticks++`，並在讀與寫之間被中斷，ISR 那次遞增就被覆蓋。即使在單核 MCU 上，ISR 與主程式共用的變數也只有「單一、自然寬度的讀或寫」是安全的；讀改寫要放在關中斷的區段，或用處理器的 atomic 指令。

Jones 的三個延伸問題：

- **參數可以同時 `const` 與 `volatile` 嗎？** 可以。唯讀的硬體狀態暫存器宣告為 `const volatile uint32_t *`：程式不該寫（`const`），值會被硬體改（`volatile`）。
- **指標可以是 `volatile` 嗎？** 可以，分清修飾對象。`volatile int *p` 指向 volatile int；`int *volatile p` 是指標本身 volatile，用於 ISR 會改動指標的情況（例如移動緩衝區寫入位置）。
- **下面錯在哪？**

```c
int square(volatile int *ptr)
{
    return *ptr * *ptr;
}
```

```
square:                          ; x86-64 gcc -O2
	mov	eax, DWORD PTR [rdi]
	mov	edx, DWORD PTR [rdi]
	imul	eax, edx
	ret
```

`*ptr` 出現兩次就讀兩次（aarch64 同樣兩道 `ldr`），兩次之間值變了，傳回的就不是平方。修正：

```c
int square(volatile int *ptr)
{
    int a = *ptr;
    return a * a;      /* 編成一道 mov + imul eax, eax */
}
```

這版仍假設平方放得進 `int`：32 位元 `int` 上輸入 46341 就有號溢位。Jones 原文把傳回型別改成 `long`，但 `a * a` 仍以 `int` 計算，溢位發生在轉成 `long` 之前，沒有解決問題；要完整範圍應寫 `return (long long) a * a;`。

### 延伸：從單核到多核，`volatile` 與 barrier

Jones 寫作時的平台是單核、循序執行的 8051、68HC11、ARM7TDMI，並行只來自中斷。今天的 RP2040、ESP32 是雙核，嵌入式 Linux SoC 普遍多核甚至亂序執行。順序要在三個層次分別處理：

```
  層次      問題                                    工具
  ───────  ──────────────────────────────────────  ──────────────────────────────────────
  編譯器    非 volatile 存取可跨越 volatile 存取重排   compiler barrier：asm volatile("" ::: "memory")
                                                    atomic_signal_fence()（同核：主程式 vs ISR）
  處理器    其他核以不同順序看到我的寫入              CPU barrier：atomic_thread_fence()、smp_mb()
  裝置      DMA 描述子要先對裝置可見，再敲門鈴暫存器    writel() 內的 dma_wmb()、dma_sync_*()
```

store buffering 測試把問題縮到最小：兩個執行緒分別釘在核 2 與核 4，一個「寫 `x` 再讀 `y`」、另一個「寫 `y` 再讀 `x`」。若所有存取依某個全序發生，兩個讀取不可能同時讀到 0：

<details>
<summary>點開 store buffering 測試程式（sb.c）</summary>

```c
#define _GNU_SOURCE
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdio.h>

#if defined(FENCE)
#define BARRIER() atomic_thread_fence(memory_order_seq_cst)
#elif defined(CBARRIER)
#define BARRIER() __asm__ volatile("" ::: "memory")
#else
#define BARRIER() do { } while (0)
#endif

#define ITERS 1000000

static volatile int x, y, r1, r2;
static atomic_int start1, start2, done;

static void pin(int cpu)
{
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    pthread_setaffinity_np(pthread_self(), sizeof set, &set);
}

static void *t1(void *arg)
{
    pin(2);
    for (int i = 1; i <= ITERS; i++) {
        while (atomic_load_explicit(&start1, memory_order_acquire) != i)
            ;
        x = 1;
        BARRIER();
        r1 = y;
        atomic_fetch_add_explicit(&done, 1, memory_order_release);
    }
    return arg;
}

static void *t2(void *arg)
{
    pin(4);
    for (int i = 1; i <= ITERS; i++) {
        while (atomic_load_explicit(&start2, memory_order_acquire) != i)
            ;
        y = 1;
        BARRIER();
        r2 = x;
        atomic_fetch_add_explicit(&done, 1, memory_order_release);
    }
    return arg;
}

int main(void)
{
    pthread_t a, b;
    long both_zero = 0;
    pthread_create(&a, NULL, t1, NULL);
    pthread_create(&b, NULL, t2, NULL);
    for (int i = 1; i <= ITERS; i++) {
        x = y = 0;
        atomic_store_explicit(&done, 0, memory_order_relaxed);
        atomic_store_explicit(&start1, i, memory_order_release);
        atomic_store_explicit(&start2, i, memory_order_release);
        while (atomic_load_explicit(&done, memory_order_acquire) != 2)
            ;
        both_zero += (r1 == 0 && r2 == 0);
    }
    pthread_join(a, NULL);
    pthread_join(b, NULL);
    printf("r1 == 0 && r2 == 0: %ld of %d\n", both_zero, ITERS);
    return 0;
}
```

</details>

```
$ gcc -O2 -pthread sb.c && ./a.out            # 兩次執行
[只有 volatile]        451029 of 1000000 | 527941 of 1000000
[-DCBARRIER]           333209 of 1000000 | 181825 of 1000000
[-DFENCE]                   0 of 1000000 |      0 of 1000000
```

只用 `volatile`：一百萬次中約一半「兩個都讀到 0」。加 compiler barrier 仍有十幾萬到三十幾萬次。改用 `atomic_thread_fence(memory_order_seq_cst)` 才歸零——gcc 為它在 x86-64 產生 `lock orq $0, (%rsp)`、在 aarch64 產生 `dmb ish`。x86-64 的記憶體模型（TSO）允許寫入暫存在核內的 store buffer，讓後面的讀取先完成，這正是觀察到的重排；Arm 的模型更弱。**`volatile` 與 compiler barrier 都只約束編譯器，不約束處理器。**（原文在 Threadripper 上的次數是 99 萬多次，本機約一半；次數依 CPU 與排程而異，關鍵是「不為 0」。）

最後要誠實：這個測試觀察的是硬體行為；就 C11 而言，多執行緒並行存取同一個非 atomic 物件且有寫入就是 data race（C11 5.1.2.4 §25），加了 fence 仍是 UB。可攜寫法是把 `x`、`y` 宣告為 `_Atomic int` 並以 `memory_order_seq_cst` 存取。

> C11 atomics 與記憶體順序，回看 [Ch 20](./20-lock-free-linked-list.md)；`c_interview` 課的 [volatile 與 sequence point](../c_interview/10-volatile-sequence-point.md) 有面試角度的整理。

---

## 對比與取捨

| 需求 | 舊答案（Jones 年代） | 今天的建議 | 理由 |
|---|---|---|---|
| 編譯時期常數 | `#define` | `enum`、C23 `constexpr`、必要時 `#define` 加字尾 | 有型別、受 scope 約束 |
| 泛型 min/max | 巨集 | `static inline`；泛型用核心式 `typeof` + 型別檢查 | 只求值一次、可擋有號/無號混用 |
| 組態檢查 | `#error` | `#error`（巨集）+ `static_assert`（型別）+ `BUILD_BUG_ON`（inline 後常數） | 各管一層 |
| ISR 共用旗標 | `volatile` | `volatile`（單核、自然寬度）或 `atomic` | 讀改寫仍要關中斷或 atomic |
| 執行緒共享 | `volatile` | `<stdatomic.h>`、mutex | `volatile` 不約束 CPU 重排 |
| 模組隔離 | `static` | `static` + LTO + `used`/`KEEP` | LTO 會刪掉只被硬體參照的物件 |

## 踩雷集錦

1. **以為字尾放哪都一樣**：`60 * 60 * 24 * 365UL` 前三個乘法仍在 `int` 裡算。字尾放在第一個運算元，或整條轉成目標型別。
2. **以為無號就安全**：`1000UL * ...` 在 32 位元 `long` 上默默回繞，而且編譯器不警告，因為無號回繞合法。安全的是「足夠寬」，不是「無號」。
3. **以為 `-Wall` 就夠**：`MIN(i, u)` 的有號/無號比較、`uint8_t` 迴圈恆真、`char *pc = "john"`，`-Wall` 全部沉默。至少開 `-Wextra`，字串加 `-Wwrite-strings`，轉換加 `-Wsign-conversion`。
4. **以為 `volatile` 能同步執行緒**：store buffering 測試說明它連順序都保證不了，更別說 atomic。
5. **以為 `const` 物件是常數**：C 的 `const int N` 不能當全域陣列大小，也不能當 `case` 標籤；要常數運算式用 `enum` 或 C23 `constexpr`。
6. **以為 `static` 只是「保留值」**：檔案範圍的 `static` 改的是 linkage，是單一映像檔韌體的命名空間防火牆。

## 進階：再往深一層

- **時間的三種來源**：`CLOCK_REALTIME`（會跳、會倒退）、`CLOCK_MONOTONIC`（只前進，會被 NTP 調速）、`CLOCK_MONOTONIC_RAW`（不調速）。Google 與 AWS 用 leap smear 把閏秒攤平在 24 小時內，代價是攤平期間各家時間來源彼此不一致。
- **核心巨集的演進**：`include/linux/minmax.h` 的 git log 是一部「巨集如何同時做到泛型、單次求值、型別檢查、保留常數運算式身分」的實戰史。2018 年 Kees Cook 清除核心 VLA 時發現 `char buf[max(A, B)]` 因 statement expression 不是常數運算式而變成 VLA，才有今天 `__is_constexpr()` 的分流。
- **常見進階面試追問**：「`while (!flag);` 在 `-O2` 下為什麼可能整個消失？」——答：C11 6.8.5 §6 的終止假設 + 非 volatile 存取可快取。「`static inline` 放在標頭檔與 `inline` 的差別？」——見 C11 6.7.4 的 inline definition 規則。

## 動手練習

1. 把題目 0x01 的 `SECONDS_PER_YEAR` 改成計算「一世紀的微秒數」，分別用 `int`、`UL`、`ULL` 字尾，在 `gcc -m32 -S` 下看哪些版本默默回繞。
2. 把題目 0x02 的 GNU `MIN` 改用 `__COUNTER__` 產生唯一的暫存變數名（核心 `__UNIQUE_ID` 的做法），確認 `MIN(_a, x)` 不再捕獲。
3. 在 `wait_plain` 的迴圈裡呼叫一個外部函式 `void cpu_relax(void);`（只宣告不定義，`-c` 編譯），看 clang `-O2` 是否還刪掉迴圈，用 C11 6.8.5 §6 解釋。
4. 用 `cdecl`（`sudo apt install cdecl`）翻譯 `int (*(*x[3])())[5]`，再自己寫一個「傳回函式指標的函式」宣告並用 `typedef` 改寫。

## 本章重點整理

- 整數常數有型別，乘法由左而右在運算元型別中進行；要寬就讓第一個運算元帶夠寬的字尾。
- 巨集的兩個病：括號與重複求值；今天的解法是 `static inline` 或核心式 `typeof` 巨集加編譯時期型別檢查。
- `#error` 管巨集組態、`static_assert` 管型別配置、`BUILD_BUG_ON` 管 inline 後才確定的常數。
- C11 的迴圈終止假設會讓不讀 `volatile` 的忙碌等待被刪除；`uint8_t` 計數器配上整數提升會變成恆真條件。
- `static` 改 storage duration 或 linkage；`const` 是存取路徑的唯讀，不是常數；`volatile` 是可觀察行為，不是 atomic、不是 barrier。

## 自我檢核

- [ ] 不看筆記，能說出 `60 * 60 * 24 * 365UL` 在 16 位元 `int` 上哪一步溢位、為什麼 `UL` 擺在後面沒救
- [ ] 能解釋 `MIN(*p++, b)` 為什麼不是 UB 卻仍是錯，以及 `typeof` 版本的名稱捕獲為什麼是 UB
- [ ] 能說明 `__is_constexpr()` 利用了哪一條 `?:` 的型別規則
- [ ] 如果面試官問「`while (!flag);` 在 clang `-O2` 下變成 `ret`，是編譯器的 bug 嗎？」，你會怎麼回答
- [ ] 能分辨 scope、storage duration、linkage，並說出 `static` 在三種位置各改了哪一個
- [ ] 能畫出 store buffering 測試中「兩個都讀到 0」的時序，並說出為什麼 compiler barrier 不夠
- [ ] 想一個你寫過的韌體或系統程式：哪個全域變數其實應該是 `static`？哪個 `volatile` 其實應該是 atomic？

## 延伸閱讀

### 論文

- **[Euclidean affine functions and their application to calendar algorithms](https://arxiv.org/abs/2102.06959)** — Cassio Neri, Lorenz Schneider，*Software: Practice and Experience* 53 卷（2023）
  - **核心貢獻**：把日曆換算中的「除以 1461、除以 153」改寫成可用一次乘法與移位完成的「歐幾里得仿射函數」，證明在整個範圍內正確。
  - **讀哪裡**：Section 6（日曆演算法），尤其 Proposition 6.3 對應 Linux v5.14 的 `time64_to_tm()`；前面的數學證明可略讀。
  - **和本章的關聯**：題目 0x01 延伸中的效能對照就是這篇的實作；前提是懂整數除法與定點數（[Ch 26](./26-floating-point-errors.md)）。

### 部落格 / 技術文章

- **[What Every C Programmer Should Know About Undefined Behavior](https://blog.llvm.org/2011/05/what-every-c-programmer-should-know.html)** — Chris Lattner（LLVM Blog，2011）
  - **這篇說什麼**：從編譯器作者角度解釋為什麼編譯器會利用 UB 刪程式碼。
  - **讀哪裡**：Part 1 與 Part 2；Part 3 談 clang 的對策可選讀。
  - **為什麼值得讀**：題目 0x04 的迴圈被刪、題目 0x07 的 `const` 寫入被刪，都是同一套推論邏輯。
- **[How and why the leap second affected Cloudflare DNS](https://blog.cloudflare.com/how-and-why-the-leap-second-affected-cloudflare-dns/)** — John Graham-Cumming（Cloudflare Blog，2017）
  - **這篇說什麼**：一個負的時間間隔如何讓 Go 程式 panic、為什麼要用單調時鐘。
  - **讀哪裡**：整篇，十分鐘可讀完。
  - **為什麼值得讀**：把題目 0x01 的閏秒延伸落到真實事故，事後分析寫得很完整。

### 官方文件 / Spec

- **[ISO/IEC 9899:201x 草案 N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：6.4.4.1（整數常數型別）、6.8.5 §6（迴圈終止假設）、6.2.2/6.2.4（linkage 與 storage duration）、6.7.3（型別限定詞）、5.1.2.3（可觀察行為）。
  - **注意**：C23 的條款編號不同，以 [N3220](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n3220.pdf) 對照。
- **[Why the "volatile" type class should not be used](https://www.kernel.org/doc/html/latest/process/volatile-considered-harmful.html)** — Linux 核心文件
  - **讀哪裡**：整篇，特別是「volatile 還剩哪些合法用途」那段。
  - **前提**：懂題目 0x08 的三層順序模型。

### 書籍

- **《C: A Reference Manual》（5th ed.）** — Harbison & Steele（Prentice Hall，2002）
  - **這本書的定位**：比規格書好讀、比教科書精確的參考書；年代較早（C99），但宣告、型別轉換的章節至今適用。
  - **讀哪幾章**：第 4 章（宣告）、第 6 章（轉換），對應題目 0x05 與 0x0C。

前八題考的是「語言本身」；後八題轉向位元、裝置暫存器、中斷與型別轉換，正是嵌入式工程師每天碰硬體的那一面。

→ [Ch 43 0x10 道嵌入式考題 II（0x09–0x10）](./43-embedded-quiz-part2.md)
