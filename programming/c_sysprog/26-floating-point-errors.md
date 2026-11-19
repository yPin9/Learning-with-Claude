# Ch 26 — 浮點誤差與補救：Kahan、定點數與低精度格式

> **目標**：能辨認浮點誤差的四種典型樣態（表示誤差、大數吃小數、災難性相消、比較失準），對每一種說得出成因並選出對的補救手段（改寫算式、補償加總、定點數、任意精度）；理解 x87 延伸精度、FMA、`-ffast-math` 怎麼讓「同一份程式碼」得出不同結果；認識 IEEE 754-2019、C23 與 BFloat16 / FP8 這些新格式在解決什麼問題。

> **對應原文**：[你所不知道的 C 語言：浮點數運算篇](https://hackmd.io/@sysprog/c-floating-point) 的「數值誤差的典型樣態」「補救手段」「跨平台的一致性」「標準的演進：IEEE 754-2019 與 C23」「BFloat16 與低精度格式」等節

> **環境**：WSL Ubuntu 22.04 x86_64（AMD Ryzen 7 5700G，支援 FMA/AVX2），gcc 11.4、glibc 2.35、GMP 6.2。C23、`_Float16`、`__bf16` 與 MPFR 段落以 Docker `gcc:14`（gcc 14.4、glibc 2.41、MPFR 4.2.2）執行。GPU、DSP、Arm 段落**未實測**，會逐段標明。

## 為什麼需要這個？

上一章說 IEEE 754 保證每一個基本運算都正確捨入。問題是：**每一步都正確，不代表整個計算正確**。一個運算的誤差頂多半個 ULP，但它會被下一個運算繼承、被減法放大、被迴圈累積。歷史上幾個代價高昂的事故都是這樣來的：

- **愛國者飛彈（1991）**：系統時鐘以 0.1 秒為單位計數，轉成實數時 0.1 在 24 位元暫存器裡被截斷，每次誤差約 $9.5 \times 10^{-8}$ 秒。連續運作 100 小時後累積成 0.34 秒，雷達追蹤窗偏移約 687 公尺，沒能攔截飛彈，28 人死亡（美國 GAO 報告 IMTEC-92-26）。
- **Anarchy Online（2001）**：伺服器時間用 `float` 存秒數，連續運作數週後，每個 tick 加上的 1/60 秒小於相鄰 `float` 間距的一半，時間停止前進，伺服器當機。
- **《哥薩克：歐洲戰爭》（2000）**：多人連線時各玩家電腦的浮點結果有細微差異，模擬逐漸分歧無法同步，最後整個模擬改寫成定點數。

這三個案例對應本章三個主題：表示誤差的累積、大數吃小數、跨平台一致性。

> 本章大量使用 ULP、機器 epsilon、次正規數、`%a` 格式。如果還不熟，先回看 [Ch 25 — IEEE 754](./25-ieee754.md) 的「binary32 逐位元拆解」與「`<float.h>`」兩節。

## 先建立直覺：每一步都是「精確值 + 一點點」

把 IEEE 754 的保證寫成一條式子，整章都從這裡出發：

$$\text{fl}(a \circ b) = (a \circ b)(1 + \delta), \quad |\delta| \le u$$

$u$ 是**單位捨入誤差**（unit roundoff），就近捨入時是機器 epsilon 的一半：`float` 為 $2^{-24} \approx 6 \times 10^{-8}$，`double` 為 $2^{-53} \approx 1.1 \times 10^{-16}$。白話：**每一次運算都會乘上一個非常接近 1 的隨機因子**。

一次運算的相對誤差很小，麻煩出在三種情況：

```
1. 累積：n 次運算，誤差最壞是 n·u
        s = x1 + x2 + ... + xn       誤差界 ≈ (n-1)·u·Σ|xi|

2. 吞噬：大數 + 小數，小數比大數的半個 ULP 還小就整個消失
        1e8f + 1.0f == 1e8f

3. 放大：兩個帶誤差的相近數相減，正確的高位抵消，剩下的全是誤差
        1.000000|43  (真值 1.000000|12)
      − 1.000000|21  (真值 1.000000|00)
      = 0.000000|22  (真值 0.000000|12)   相對誤差從 1e-7 暴增到 80%
```

輸入本身也帶誤差：`0.1` 寫進原始碼那一刻就不是 0.1 了。所以分析誤差要分兩層——**表示誤差**（輸入離真值多遠）與**運算誤差**（每一步又加了多少）。

## 樣態一：表示誤差與 0.1 + 0.2

```c
#include <math.h>
#include <stdio.h>

int main(void)
{
    /* 相鄰 float 的間距（ULP）隨數值大小成長 */
    float xs[] = {1.0f, 1000.0f, 604800.0f, 16777216.0f, 1e10f};
    for (int i = 0; i < 5; i++)
        printf("ULP(%-10.0f) = %g\n", xs[i], nextafterf(xs[i], INFINITY) - xs[i]);

    /* 大數吃小數 */
    volatile float big = 1e8f, one = 1.0f;
    printf("1e8f + 1 - 1e8f = %g\n", (big + one) - big);
    volatile double dbig = 1e16, done = 1.0;
    printf("1e16 + 1 - 1e16 = %g\n", (dbig + done) - dbig);

    /* 0.1 + 0.2 */
    printf("0.1 + 0.2 = %.17g (%a)\n", 0.1 + 0.2, 0.1 + 0.2);
    printf("0.3       = %.17g (%a)\n", 0.3, 0.3);
    printf("0.1 exactly = %.55f\n", 0.1);
    return 0;
}
```

```
ULP(1         ) = 1.19209e-07
ULP(1000      ) = 6.10352e-05
ULP(604800    ) = 0.0625
ULP(16777216  ) = 2
ULP(10000000000) = 1024
1e8f + 1 - 1e8f = 0
1e16 + 1 - 1e16 = 0
0.1 + 0.2 = 0.30000000000000004 (0x1.3333333333334p-2)
0.3       = 0.29999999999999999 (0x1.3333333333333p-2)
0.1 exactly = 0.1000000000000000055511151231257827021181583404541015625
```

`0.1` 存進 `double` 的**精確值**是 0.1000000000000000055511…（二進位有限小數一定能寫成有限位十進位，這裡 55 位）。`0.1`、`0.2`、`0.3` 各自捨入，`0.1 + 0.2` 的結果又捨入一次，最後恰好落在 `0.3` 的下一個 `double`。用 `%a` 看就很清楚：尾數差 1。

Anarchy Online 的當機也能從 ULP 表讀出來：604800 秒（一週）附近 `float` 的間距是 0.0625 秒，每個 tick 的 $1/60 \approx 0.0167$ 秒不到半個間距，加上去就被捨入掉：

```c
#include <stdio.h>

int main(void)
{
    float t = 604800.0f;            /* 伺服器已運作一週（秒） */
    const float dt = 1.0f / 60;     /* 每個 tick 前進 1/60 秒 */
    float before = t;
    for (int i = 0; i < 600; i++)   /* 模擬 10 秒 */
        t += dt;
    printf("float  elapsed: %g s\n", t - before);

    double td = 604800.0, bd = td;
    for (int i = 0; i < 600; i++)
        td += 1.0 / 60;
    printf("double elapsed: %.12g s\n", td - bd);
    return 0;
}
```

```
float  elapsed: 0 s
double elapsed: 10.0000000326 s
```

`double` 撐得住，但仍有 $3 \times 10^{-8}$ 秒的誤差，而且會隨運作時間增加。**真正的修法是用整數記 tick 數**，需要秒數時才換算：`seconds = ticks / 60.0`，誤差不會累積。

### 迴圈的終止條件

```c
#include <stdio.h>

int main(void)
{
    const double a = 1.0, b = 2.0, h = (b - a) / 3;
    int count = 0;
    for (double x = a; x < b; x += h) {
        printf("x = %.17g\n", x);
        count++;
    }
    printf("iterations: %d (expected 3)\n", count);
    return 0;
}
```

```
x = 1
x = 1.3333333333333333
x = 1.6666666666666665
x = 1.9999999999999998
iterations: 4 (expected 3)
```

三次累加後 `x` 是 1.9999999999999998，仍然小於 2，多跑一次。**用整數計數，位置用 `a + i * h` 算**：迭代次數由整數決定，不會多也不會少。

## 樣態二：災難性相消

兩個相近的數相減時，**減法本身是精確的**（Sterbenz 引理：同號且 $y/2 \le x \le 2y$ 時，$x - y$ 必可精確表示），問題是運算元原本的誤差變成了結果的主體。這叫災難性相消（catastrophic cancellation）。

$\frac{1 - \cos x}{x^2}$ 在 $x \to 0$ 時趨近 0.5。直接算會發生什麼事：

```c
#include <math.h>
#include <stdio.h>

int main(void)
{
    /* (1 - cos x) / x^2 → 0.5 當 x → 0 */
    printf("%-8s %-22s %-22s\n", "x", "(1-cos x)/x^2", "2 sin^2(x/2)/x^2");
    for (double x = 1e-2; x >= 1e-9; x /= 100) {
        double naive = (1 - cos(x)) / (x * x);
        double s = sin(x / 2);
        double good = 2 * s * s / (x * x);
        printf("%-8.0e %-22.17g %-22.17g\n", x, naive, good);
    }

    /* exp(x) - 1 與 expm1、log(1+x) 與 log1p */
    double x = 1e-10;
    printf("exp(x)-1   = %.17g\nexpm1(x)   = %.17g\n", exp(x) - 1, expm1(x));
    printf("log(1+x)   = %.17g\nlog1p(x)   = %.17g\n", log(1 + x), log1p(x));
    return 0;
}
```

```
x        (1-cos x)/x^2          2 sin^2(x/2)/x^2
1e-02    0.49999583334736641    0.49999583334722214
1e-04    0.49999999696126451    0.49999999958333341
1e-06    0.50004445029117051    0.49999999999995831
1e-08    0                      0.5
exp(x)-1   = 1.000000082740371e-10
expm1(x)   = 1.00000000005e-10
log(1+x)   = 1.000000082690371e-10
log1p(x)   = 9.9999999995000007e-11
```

- $x = 10^{-6}$ 時 $\cos x \approx 1 - 5 \times 10^{-13}$，`1 - cos(x)` 只剩約 3–4 位有效數字，結果錯在第 5 位。
- $x = 10^{-8}$ 時 $\cos x$ 和 1 的差小於 `double` 在 1 附近的半個間距，`cos(x)` **就是** 1.0，結果是 0。
- 用三角恆等式 $1 - \cos x = 2\sin^2(x/2)$ 改寫，沒有減法，每一列都正確。
- `exp(x) - 1` 與 `log(1 + x)` 是同一類問題，C99 為此提供 `expm1` 與 `log1p`：它們內部不經過「先算出接近 1 的數」這一步。上面的 `exp(x)-1` 錯在第 8 位。

**提高精度無法解決相消，只能延後它**：換成 `long double`，$x = 10^{-10}$ 時還是會崩。根治的辦法是**改寫算式，讓減法消失**。

### 改寫算式：一元二次方程式

$x^2 + 200x - 10.0025 = 0$ 的根是 $-200.05$ 與 $0.05$。公式解 $\frac{-b + \sqrt{b^2 - 4ac}}{2a}$ 裡 $\sqrt{b^2 - 4ac} \approx 200.1$ 和 $b = 200$ 很接近，發生相消。改寫：先算不會相消的根，另一個根用韋達定理 $x_1 x_2 = c/a$ 求：

```c
#include <math.h>
#include <stdio.h>

int main(void)
{
    float a = 1.0f, b = 200.0f, c = -10.0025f;      /* 根為 -200.05 與 0.05 */
    float d = sqrtf(b * b - 4 * a * c);
    float r1 = (-b - d) / (2 * a), r2 = (-b + d) / (2 * a);
    printf("standard : %.10g %.10g\n", r1, r2);
    float x1 = (-b - copysignf(d, b)) / (2 * a);    /* 不相消的那個根 */
    float x2 = c / (a * x1);                        /* 韋達定理 x1*x2 = c/a */
    printf("rewritten: %.10g %.10g\n", x1, x2);
    return 0;
}
```

```
standard : -200.0500031 0.05000305176
rewritten: -200.0500031 0.04999999702
```

小根的誤差從 $3 \times 10^{-6}$ 降到 $3 \times 10^{-9}$，改善一千倍，而且沒有換更寬的型別。同類手法：$\sqrt{x+1} - \sqrt{x}$ 乘上共軛式變成 $\frac{1}{\sqrt{x+1} + \sqrt{x}}$；$\sqrt{a^2 + b^2}$ 用 `hypot` 避免平方溢位；連乘改成對數域加總。

### 統計量：perf 的 Welford 更新

樣本變異數的「單遍公式」$\frac{1}{n-1}\left[\sum x_i^2 - \frac{1}{n}(\sum x_i)^2\right]$ 是兩個巨大的數相減。原文實測 $10^6$ 筆約 $10^9$ 的資料（變異數約 0.667），單遍公式得到 $9.9 \times 10^6$，錯了 7 個數量級。Welford（1962）的遞推式只累加偏差的乘積，不出現 $x_i^2$：

$$m_k = m_{k-1} + \frac{x_k - m_{k-1}}{k}, \quad M_k = M_{k-1} + (x_k - m_{k-1})(x_k - m_k)$$

Linux 的 `perf stat -r` 重複量測時就用它，[`tools/perf/util/stat.c`](https://github.com/torvalds/linux/blob/master/tools/perf/util/stat.c) 的 `update_stats()`：

```c
void update_stats(struct stats *stats, u64 val)
{
	double delta;

	stats->n++;
	delta = val - stats->mean;
	stats->mean += delta / stats->n;
	stats->M2 += delta*(val - stats->mean);
}
```

注意 `M2` 那行用的是**已更新**的 `mean`，對應 $(x_k - m_{k-1})(x_k - m_k)$，兩行順序不能對調。

## 樣態三：比較——epsilon 不是萬靈丹

`a == b` 對帶有捨入誤差的結果幾乎沒用。常見的「改進」是 `fabs(a - b) < 1e-9`，但**固定的絕對誤差門檻在大數時太嚴、在小數時太鬆**：`1e20` 附近相鄰 `double` 就差 16384，`1e-9` 門檻永遠不會成立；`1e-15` 附近，門檻又比數值本身大一百萬倍。

兩種比較好的做法：

1. **相對誤差**：$|a - b| \le \varepsilon_{rel} \cdot \max(|a|, |b|)$，再加一個絕對誤差下限處理接近 0 的情況。
2. **ULP 距離**：兩數之間隔了幾個可表示的數。利用 Ch 25 講過的「位元樣式單調」性質，把負數翻轉後相減即可。

```c
#include <float.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* 把 double 的位元映射成「單調遞增」的整數：負數翻轉，讓 -0 與 +0 相鄰 */
static int64_t ordered(double d)
{
    int64_t i;
    memcpy(&i, &d, sizeof i);
    return i < 0 ? INT64_MIN - i : i;
}

/* 兩數之間隔了幾個可表示的 double；任一為 NaN 回傳 -1 */
static int64_t ulp_diff(double a, double b)
{
    if (isnan(a) || isnan(b))
        return -1;
    int64_t x = ordered(a), y = ordered(b);
    return x > y ? x - y : y - x;
}

/* 絕對 + 相對誤差混合比較 */
static bool nearly_equal(double a, double b, double abs_eps, double rel_eps)
{
    if (a == b)
        return true;                      /* 含 +0 == -0、inf == inf */
    if (!isfinite(a) || !isfinite(b))
        return false;
    double diff = fabs(a - b);
    return diff <= abs_eps || diff <= rel_eps * fmax(fabs(a), fabs(b));
}

int main(void)
{
    printf("ulp_diff(0.1+0.2, 0.3)   = %lld\n", (long long) ulp_diff(0.1 + 0.2, 0.3));
    printf("ulp_diff(1.0, nextafter) = %lld\n", (long long) ulp_diff(1.0, nextafter(1.0, 2.0)));
    printf("ulp_diff(-0.0, +0.0)     = %lld\n", (long long) ulp_diff(-0.0, 0.0));
    printf("ulp_diff(-DBL_TRUE_MIN, DBL_TRUE_MIN) = %lld\n",
           (long long) ulp_diff(-DBL_TRUE_MIN, DBL_TRUE_MIN));
    printf("ulp_diff(1.0, 2.0)       = %lld\n", (long long) ulp_diff(1.0, 2.0));

    double e = 1e-12, r = 4 * DBL_EPSILON;
    printf("|a-b| < 1e-9 for 1e20 vs 1e20+1e5 : %d\n", fabs(1e20 - (1e20 + 1e5)) < 1e-9);
    printf("nearly(0.1+0.2, 0.3)       : %d\n", nearly_equal(0.1 + 0.2, 0.3, e, r));
    printf("nearly(1e20, 1e20+1e5)     : %d\n", nearly_equal(1e20, 1e20 + 1e5, e, r));
    printf("nearly(1e-15, 0)  rel only : %d\n", nearly_equal(1e-15, 0.0, 0.0, r));
    printf("nearly(1e-15, 0)  abs+rel  : %d\n", nearly_equal(1e-15, 0.0, e, r));
    printf("nearly(NaN, NaN)           : %d\n", nearly_equal(NAN, NAN, e, r));
    return 0;
}
```

```
ulp_diff(0.1+0.2, 0.3)   = 1
ulp_diff(1.0, nextafter) = 1
ulp_diff(-0.0, +0.0)     = 0
ulp_diff(-DBL_TRUE_MIN, DBL_TRUE_MIN) = 2
ulp_diff(1.0, 2.0)       = 4503599627370496
|a-b| < 1e-9 for 1e20 vs 1e20+1e5 : 0
nearly(0.1+0.2, 0.3)       : 1
nearly(1e20, 1e20+1e5)     : 0
nearly(1e-15, 0)  rel only : 0
nearly(1e-15, 0)  abs+rel  : 1
nearly(NaN, NaN)           : 0
```

- `ordered()` 對負數做 `INT64_MIN - i`，把「位元越大、數值越小」的負數段翻過來接在正數前面，$-0$ 和 $+0$ 都映射到 0。
- `1.0` 和 `2.0` 之間隔了 $2^{52}$ 個 `double`，ULP 距離很能反映「這兩個數在浮點世界裡有多遠」。
- `nearly(1e20, 1e20+1e5)` 判為不等：相對差 $10^{-15}$ 大於門檻 $4 \times 2^{-52} \approx 8.9 \times 10^{-16}$。**門檻沒有放諸四海皆準的值**，要從演算法的誤差分析推出來：做了多少次運算、有沒有相消。
- 只用相對誤差時，任何非零數和 0 都不「接近」，所以需要絕對誤差下限。

## 補救一：補償加總（Kahan、Neumaier、成對加總）

加總是最常見、也最容易改善的誤差來源。用 `float` 把 0.1f 加一千萬次，四種演算法對照：

```c
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#define N 10000000

static float naive(const float *v, long n)
{
    float s = 0.0f;
    for (long i = 0; i < n; i++)
        s += v[i];
    return s;
}

static float pairwise(const float *v, long n)
{
    if (n <= 8) {                    /* 小段落直接加，避免遞迴開銷 */
        float s = 0.0f;
        for (long i = 0; i < n; i++)
            s += v[i];
        return s;
    }
    long h = n / 2;
    return pairwise(v, h) + pairwise(v + h, n - h);
}

static float kahan(const float *v, long n)
{
    float s = 0.0f, c = 0.0f;
    for (long i = 0; i < n; i++) {
        float y = v[i] - c;
        float t = s + y;
        c = (t - s) - y;
        s = t;
    }
    return s;
}

static float neumaier(const float *v, long n)
{
    float s = 0.0f, c = 0.0f;
    for (long i = 0; i < n; i++) {
        float t = s + v[i];
        if (fabsf(s) >= fabsf(v[i]))
            c += (s - t) + v[i];     /* v[i] 的低位遺失 */
        else
            c += (v[i] - t) + s;     /* s 的低位遺失 */
        s = t;
    }
    return s + c;
}

static void report(const char *name, double got, double ref)
{
    printf("%-9s %-16.9g abs err %-11.3g rel err %.3g\n", name, got,
           fabs(got - ref), fabs(got - ref) / fabs(ref));
}

int main(void)
{
    float *v = malloc(N * sizeof *v);
    for (long i = 0; i < N; i++)
        v[i] = 0.1f;
    /* 0.1f 只有 24 個有效位元，N 也小於 2^24，乘積可精確放進 double */
    double ref = (double) 0.1f * N;
    printf("reference = %.9g\n", ref);
    report("naive", naive(v, N), ref);
    report("pairwise", pairwise(v, N), ref);
    report("kahan", kahan(v, N), ref);
    report("neumaier", neumaier(v, N), ref);
    double d = 0.0;
    for (long i = 0; i < N; i++)
        d += v[i];
    report("double", d, ref);

    float w[] = {1.0f, 1e20f, 1.0f, -1e20f};    /* 正確總和為 2 */
    printf("\n[1, 1e20, 1, -1e20]: naive %g, kahan %g, neumaier %g\n",
           naive(w, 4), kahan(w, 4), neumaier(w, 4));
    free(v);
    return 0;
}
```

```
$ gcc -Wall -Wextra -g -O2 sum.c -o sum -lm && ./sum
reference = 1000000.01
naive     1087937          abs err 8.79e+04    rel err 0.0879
pairwise  1000000          abs err 0.0149      rel err 1.49e-08
kahan     1000000          abs err 0.0149      rel err 1.49e-08
neumaier  1002001.75       abs err 2e+03       rel err 0.002
double    1000000.01       abs err 0           rel err 0

[1, 1e20, 1, -1e20]: naive 0, kahan 0, neumaier 2
```

### Kahan 加總的機制

逐一累加錯了 8.8%：部分和越來越大，每次加上的 0.1 有越來越多低位被捨掉。Kahan（1965）的想法是**把每次被捨掉的部分記下來，下一輪補回去**：

```
         s = 1000000.000        y = 0.1000000015 (含上一輪補償)
   t = s + y  = 1000000.125     ← 捨入後；1e6 附近 float 間距 0.0625
   t - s      =       0.125     ← 實際加進去的量（這個減法精確，Sterbenz）
  (t - s) - y =       0.0249999985  ← 多加的部分 = 捨入誤差 c
   下一輪：y = v[i] - c          ← 把多加的扣回來
```

關鍵是 `(t - s) - y` 這兩次減法在 $|s| \ge |y|$ 時都是精確的（Fast2Sum 定理），所以 `c` **精確地**等於這一步的捨入誤差。誤差界從逐一累加的 $(n-1)u\sum|x_i|$ 降到 $(2u + O(nu^2))\sum|x_i|$，在 $nu \ll 1$ 時幾乎與 $n$ 無關。結果的 0.0149 誤差，是最終值 1000000.01 捨入到 `float`（間距 0.0625）本身就有的誤差，Kahan 已經做到格式允許的極限。

### Neumaier 的改良，以及一個失敗示範

Kahan 要求累加值比加數大。加數比累加值大時（`[1, 1e20, 1, -1e20]`），`c` 抓不到誤差，結果是 0。Neumaier（1974）的版本每步比較兩者大小，從較小的那一方取出遺失的低位，最後一次加回——上面那組資料它得到正確的 2。Python 3.12 起內建的 `sum()` 對浮點數就用這個方法。

但同一支程式裡 Neumaier 加總一千萬個 0.1f 卻錯了 2000。原因是**它的補償項 `c` 本身是用逐一累加算出來的**：這組資料每步遺失約 0.02，一千萬步累積到 $c \approx 88000$，`c` 在這個量級的 `float` 間距是 0.0078，再往上加 0.02 的時候自己也在丟位元。Kahan 每步就把 `c` 回饋進去，`c` 始終很小，沒有這個問題。把 `c` 改成 `double` 再跑：

```
neumaier(c as double) 1000000 abs err 0.0149
```

教訓：**補償演算法的保證有前提**。Neumaier 適合「補償總量相對於總和很小」的情況（多數實務資料如此）；像這組「每步都丟一大截」的極端資料，補償項需要更寬的型別。Python 的實作是 `double` 資料配 `double` 補償，前提通常成立。

### 成對加總

把陣列對半分、各自加總再相加，誤差界降到 $O(u \log_2 n)$。它不需要額外的補償變數，還天然適合平行化與 SIMD——NumPy 的 `sum` 沿連續記憶體加總時用的就是分塊的成對加總。上面的結果和 Kahan 一樣好。

| 演算法 | 誤差界（乘在 $\sum\|x_i\|$ 前） | 額外成本 | 適用 |
|---|---|---|---|
| 逐一累加 | $(n-1)u$ | 無 | 少量資料、誤差不敏感 |
| 成對加總 | $\approx u\log_2 n$ | 遞迴或分塊 | 大陣列、可平行 |
| Kahan | $2u + O(nu^2)$ | 每項 4 次運算 | 串流資料、累加值 ≥ 加數 |
| Neumaier | $2u + O(nu^2)$ | 每項 4 次運算 + 比較 | 加數可能大於累加值 |
| 換更寬型別 | 依型別 | 頻寬加倍、SIMD 減半 | 有更寬型別可用時最省事 |

> 這些誤差界都乘在 $\sum|x_i|$ 前面。如果資料正負相消、總和遠小於 $\sum|x_i|$，相對誤差仍然可能很大——這是相消，不是加總演算法能解的。

## 補救二：定點數

如果數值範圍已知、需要的是**絕對**精度與**逐位元可重現**，定點數比浮點數合適。原文的種胡蘿蔔例子：步道寬 4.8 英尺、每 1.25 英尺種一根，`float` 版本在跨越 4096 時間距誤差超過 $10^{-4}$；改成以 0.01 英尺為單位的 `long`，一萬根全部正確，因為**整數加法沒有捨入**。

定點數的本質是「整數 + 一個大家約定好的比例」。常見的二進位寫法 **Qm.n** 表示 m 位整數、n 位小數，例如 Q16.16 是 32 位元整數除以 $2^{16}$：

```
Q16.16:  raw = 0x0003_8000
         ┌────────────────┬────────────────┐
         │ 0000 0000 0000 │ 1000 0000 0000 │
         │ 0011 (整數 3)  │ 0000 (0.5)     │
         └────────────────┴────────────────┘
         值 = raw / 65536 = 3.5
```

加減法就是整數加減；乘法要先把兩個 Q16.16 相乘得到 Q32.32（需要 64 位元中間值），再右移 16 位；除法反過來先左移。細節（溢位飽和、捨入、負數右移的陷阱、整數牛頓法開根號）是[練習 C](./practice-c-fixed-point.md) 的主題。

Linux 核心大量使用定點數，因為核心預設不碰 FPU（每次進出核心都要保存與還原 FPU 狀態太貴）：

- **load average**：[`include/linux/sched/loadavg.h`](https://github.com/torvalds/linux/blob/master/include/linux/sched/loadavg.h) 以 `FSHIFT = 11` 位小數的定點數計算指數移動平均，`FIXED_1 = 1 << FSHIFT` 代表 1.0。
- **時間子系統**：cycles 換算奈秒用 $\text{ns} = (\text{cycles} \times \text{mult}) \gg \text{shift}$，`struct tk_read_base` 的 `xtime_nsec` 以左移 `shift` 位的定點數保存次奈秒的小數，下一次更新一併進位——和 Kahan 補償同一個思路：**被捨掉的部分留在狀態裡，之後補回**，差別是整數運算沒有額外的捨入。

## 補救三：有理數與任意精度

**有理數**把數表示成分子 / 分母，0.1 就是 1/10，加減乘除都精確，代價是分子分母會越來越大。**任意精度浮點數**則讓你指定尾數要幾個位元。C 生態系的標準選擇是 [GMP](https://gmplib.org/)（整數 `mpz_t`、有理數 `mpq_t`）與建立在 GMP 上的 [MPFR](https://www.mpfr.org/)（任意精度、**每個運算都正確捨入**的浮點數）。gcc 本身就用 MPFR 在編譯期折疊浮點常數，保證結果和執行期正確捨入的結果一致。

```c
#include <stdio.h>
#include <gmp.h>
#include <mpfr.h>

int main(void)
{
    /* GMP 有理數：0.1 + 0.2 == 0.3 精確成立 */
    mpq_t a, b, c, s;
    mpq_inits(a, b, c, s, NULL);
    mpq_set_str(a, "1/10", 10);
    mpq_set_str(b, "2/10", 10);
    mpq_set_str(c, "3/10", 10);
    mpq_canonicalize(b);
    mpq_canonicalize(c);
    mpq_add(s, a, b);
    gmp_printf("GMP mpq: 1/10 + 2/10 = %Qd, equal to 3/10: %d\n", s, mpq_equal(s, c));
    mpq_clears(a, b, c, s, NULL);

    /* MPFR：200 位元精度、正確捨入的浮點運算 */
    mpfr_t x, y;
    mpfr_inits2(200, x, y, (mpfr_ptr) 0);
    mpfr_set_str(x, "0.1", 10, MPFR_RNDN);
    mpfr_set_str(y, "0.2", 10, MPFR_RNDN);
    mpfr_add(x, x, y, MPFR_RNDN);
    mpfr_printf("MPFR 200-bit: 0.1 + 0.2 = %.50Rf\n", x);

    mpfr_set_ui(x, 1, MPFR_RNDN);
    mpfr_exp(x, x, MPFR_RNDN);                     /* e，正確捨入到 200 位元 */
    mpfr_printf("MPFR 200-bit: e = %.55Rf\n", x);

    /* 同一個 0.1 用 53 位元（= double）表示 */
    mpfr_set_prec(y, 53);
    mpfr_set_str(y, "0.1", 10, MPFR_RNDN);
    mpfr_printf("MPFR 53-bit : 0.1 = %.55Rf\n", y);
    mpfr_clears(x, y, (mpfr_ptr) 0);
    printf("MPFR version %s\n", mpfr_get_version());
    return 0;
}
```

```
$ gcc -Wall -Wextra mp.c -o mp -lmpfr -lgmp && ./mp     # Docker gcc:14 + libmpfr-dev
GMP mpq: 1/10 + 2/10 = 3/10, equal to 3/10: 1
MPFR 200-bit: 0.1 + 0.2 = 0.30000000000000000000000000000000000000000000000000
MPFR 200-bit: e = 2.7182818284590452353602874713526624977572470936999595750
MPFR 53-bit : 0.1 = 0.1000000000000000055511151231257827021181583404541015625
MPFR version 4.2.2
```

- `mpq_set_str` 不會自動約分，`2/10` 要呼叫 `mpq_canonicalize` 才會變成 `1/5`，否則 `mpq_equal` 比較的是未約分的分子分母。這是 GMP 新手常踩的雷。
- 200 位元下 `0.1 + 0.2` 印到 50 位小數都是 0——誤差仍在，只是在第 60 位之後。**任意精度不是消除誤差，是把誤差推到你在乎的位數之外**。
- MPFR 設成 53 位元時，`0.1` 的值和 C 的 `double` 逐位元相同。這讓 MPFR 成為驗證 libm 的參考實作：用 200 位元算「真值」，再看 `double` 版本差幾個 ULP。

本機 WSL 只有 GMP 的開發標頭，MPFR 只有執行期函式庫，所以這段在 Docker `gcc:14` 裡另外安裝 `libmpfr-dev` 執行。Ubuntu 上 `sudo apt install libmpfr-dev` 即可。

## 跨平台一致性：同一份程式碼，不同的答案

IEEE 754 保證**單一運算**的結果，但不保證**一整段程式**的結果。差異來自四個地方：中間結果的精度、編譯器是否合併運算、編譯器是否重排運算、數學函式庫。

### x87 延伸精度與 `FLT_EVAL_METHOD`

32 位元 x86 傳統上用 x87 FPU：暫存器是 80 位元延伸精度，中間結果保留 64 位元尾數與 15 位元指數，**存回記憶體時才捨入**。何時存回由暫存器配置決定，所以同一份程式碼換個最佳化等級就可能得到不同結果。C 用 `<float.h>` 的 `FLT_EVAL_METHOD` 揭露這件事（N1570 5.2.4.2.2 §9）：`0` 表示依運算元型別求值，`2` 表示一律以 `long double` 的精度與範圍求值。

本機沒有 32 位元函式庫（缺 `gcc-multilib`），`-m32` 無法連結，但可以只編譯看組合語言，以及只做前置處理看巨集值：

```
$ gcc -m32 -mfpmath=387 -O2 -S -masm=intel x87asm.c -o -     (節錄)
mul_div:
	fld	QWORD PTR 4[esp]
	fmul	QWORD PTR 12[esp]
	fdiv	QWORD PTR 12[esp]
	ret
$ gcc -O2 -S -masm=intel x87asm.c -o -                       (x86-64 預設，節錄)
mul_div:
	mulsd	xmm0, xmm1
	divsd	xmm0, xmm1
	ret
$ echo FLT_EVAL_METHOD | gcc -m32 -mfpmath=387 -include float.h -E -P - | tail -1
2
```

`double mul_div(double a, double b) { return a * b / b; }` 在 x87 上，`a * b` 的結果留在 80 位元的 `st(0)` 裡，不捨入成 `double` 就直接拿去除。x86-64 的 ABI 規定 `float`/`double` 用 SSE2，每一步都捨入到 `double`，`FLT_EVAL_METHOD` 為 0。

x86-64 上 `long double` 仍走 x87，可以拿來模擬 x87 的行為：

```c
#include <float.h>
#include <stdio.h>

int main(void)
{
    volatile double big = 1e308, ten = 10.0;
    double r = big * ten / ten;                        /* SSE2：每步捨入到 double */
    long double lr = (long double) big * ten / ten;    /* x87 80-bit：指數範圍大得多 */
    printf("FLT_EVAL_METHOD = %d\n", FLT_EVAL_METHOD);
    printf("double      : %g\n", r);
    printf("long double : %Lg\n", lr);
    printf("LDBL_MAX = %Lg, LDBL_EPSILON = %Lg\n", LDBL_MAX, LDBL_EPSILON);

    /* 雙重捨入（double rounding）：先捨入到 64 位尾數，再捨入到 53 位 */
    volatile double a = 1.0, b = 0x1p-53 + 0x1p-64;
    double direct = a + b;                             /* 一次捨入 */
    double twice = (double) ((long double) a + b);     /* 兩次捨入 */
    printf("a + b direct : %a\n", direct);
    printf("a + b twice  : %a\n", twice);
    return 0;
}
```

```
FLT_EVAL_METHOD = 0
double      : inf
long double : 1e+308
LDBL_MAX = 1.18973e+4932, LDBL_EPSILON = 1.0842e-19
a + b direct : 0x1.0000000000001p+0
a + b twice  : 0x1p+0
```

- 中間結果 $10^{309}$ 超出 `double` 範圍：SSE2 得到 `inf`，80 位元格式吸收了溢位。原文在真正的 `-m32 -mfpmath=387` 下得到同樣的 `1e+308`（本機未能連結 32 位元程式，此處以 `long double` 模擬）。
- **雙重捨入**更隱蔽：$1 + 2^{-53} + 2^{-64}$ 直接捨入到 `double`，因為比中點 $1 + 2^{-53}$ 大一點點，應該往上進位；先捨入到 64 位元尾數時 $2^{-64}$ 恰好是半個 ULP，平手取偶數被丟掉，剩下的 $1 + 2^{-53}$ 再捨入到 `double` 又是平手、又取偶數，結果變 1.0。**兩次正確的捨入，合起來是錯的**。這是 x87 時代「多一點精度反而讓結果和標準不一致」的典型例子。

### FMA 與 contraction

FMA（fused multiply-add）計算 $a \times b + c$ 只捨入一次。C 標準允許編譯器把 `x * y + z` 合併成 FMA（N1570 6.5 §8 稱為 contraction），合併與否是實作定義：

```c
#include <math.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    (void) argv;
    volatile double x = 0.1 * argc;      /* argc = 1：避免編譯期常數折疊 */
    double r = x * 10.0 - 1.0;           /* 可能被合併為一道 FMA 指令 */
    printf("x*10-1      = %a\n", r);
    printf("fma(x,10,-1)= %a\n", fma(x, 10.0, -1.0));

    /* 用 FMA 取出乘法的捨入誤差：a*b = p + e，且 e 可精確表示 */
    double a = 0.1, b = 0.3;
    double p = a * b;
    double e = fma(a, b, -p);
    printf("p = %.17g, e = %a\n", p, e);
    return 0;
}
```

| 編譯選項 | `x*10-1` | 說明 |
|---|---|---|
| `-O2` | `0x0p+0` | 沒有 `-march`，基準 x86-64 沒有 FMA 指令 |
| `-O2 -march=native` | `0x1p-54` | 編出 `vfmadd132sd`，露出 0.1 的表示誤差 |
| `-O2 -march=native -std=c11` | `0x0p+0` | ISO 模式預設 `-ffp-contract=off` |

`fma()` 函式在三種情況下都是 `0x1p-54`。`0.1 * 10` 分開捨入時恰好得到 1.0；FMA 保留未捨入的乘積，0.1 的表示誤差 $2^{-54}$ 就跑出來了。**哪個比較「對」？FMA 的結果更接近數學真值**，但它讓結果依賴編譯選項與 CPU。gcc 在 GNU 方言（預設 `-std=gnu17`）下是 `-ffp-contract=fast`，可以跨敘述合併；`#pragma STDC FP_CONTRACT OFF` gcc 不支援，要用 `-ffp-contract=off`。需要逐位元可重現的專案（例如遊戲的鎖步同步、共識演算法）應該明確關掉 contraction。

程式後半是 FMA 最重要的用途——**無誤差變換**（error-free transformation）：`e = fma(a, b, -p)` 精確等於 `a * b` 的捨入誤差，於是 $a \times b = p + e$ 精確成立。配合 Kahan 的 TwoSum，就能用 `double` 模擬接近兩倍精度的「double-double」運算，這是許多高精度函式庫（如 QD）的基礎。

### `-ffast-math` 的後果（實測）

`-ffast-math` 是一組旗標的總稱，gcc 11 中包含：`-fno-math-errno`、`-funsafe-math-optimizations`（內含 `-fassociative-math`、`-freciprocal-math`、`-fno-signed-zeros`、`-fno-trapping-math`）、`-ffinite-math-only`、`-fno-rounding-math`、`-fno-signaling-nans`、`-fcx-limited-range`、`-fexcess-precision=fast`。換句話說：**允許編譯器假設浮點數滿足結合律、沒有 NaN 與 Inf、沒有 $-0$**。

```c
#include <float.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <xmmintrin.h>

/* 輸入從命令列來，編譯器無法在編譯期算完；不給引數時 x = 0 */
int main(int argc, char **argv)
{
    unsigned csr = _mm_getcsr();               /* 先讀，避免被後面的運算旗標污染 */
    double x = argc > 1 ? strtod(argv[1], NULL) : 0.0;
    double q = x / x;                          /* x = 0 時數學上是 NaN */

    printf("MXCSR at start      = 0x%04X (FTZ=%u DAZ=%u)\n", csr,
           (csr >> 15) & 1, (csr >> 6) & 1);
    printf("x / x               = %g\n", q);
    printf("isnan(x / x)        = %d\n", isnan(q));

    volatile float tiny = FLT_MIN;
    printf("FLT_MIN / 4         = %g\n", tiny / 4);

    volatile double a = 1e308;
    double b = a * 10.0 / 10.0;
    printf("1e308 * 10 / 10     = %g\n", b);
    return 0;
}
```

```
$ gcc -O2 fast.c -o fast -lm && ./fast
MXCSR at start      = 0x1F80 (FTZ=0 DAZ=0)
x / x               = -nan
isnan(x / x)        = 1
FLT_MIN / 4         = 2.93874e-39
1e308 * 10 / 10     = inf
$ gcc -O2 -ffast-math fast.c -o fast -lm && ./fast
MXCSR at start      = 0x9FC0 (FTZ=1 DAZ=1)
x / x               = 1
isnan(x / x)        = 0
FLT_MIN / 4         = 0
1e308 * 10 / 10     = 1e+308
```

四個效果一次看到：

1. **`x / x` 被化簡成 1**：在「沒有 NaN、沒有 0/0」的假設下這是對的代數。輸入是 0 時，數學上該是 NaN，現在是 1。
2. **`isnan` 被折疊成 0**：`-ffinite-math-only` 讓編譯器認定 NaN 不存在。Ch 25 的 `self_eq` 在 fast-math 下也被編成 `mov eax, 1`。**開了 fast-math 就不能在同一個編譯單元裡偵測 NaN**。
3. **FTZ/DAZ 在 `main` 之前就被打開了**：MXCSR 從 `0x1F80` 變成 `0x9FC0`。原因是連結時多了一個啟動檔，`gcc -v` 看得到：

   ```
   $ gcc -O2 -ffast-math -v fast.c -o fast -lm 2>&1 | grep -o crtfastmath.o
   crtfastmath.o
   ```

   它的建構函式在程式啟動時設定 MXCSR，**影響整個行程**，包括沒用 fast-math 編譯的函式庫。歷史上 gcc 連建構共享程式庫（`-shared`）時也會連進它，一個用 fast-math 編譯的 Python 擴充模組就能讓整個直譯器的次正規數消失；GCC PR 55522 的修正（2022 年底到 2023 年初合入，對應 gcc 13 開發週期）讓 `-ffast-math` 搭配 `-shared` 時不再連結它（x86 上若明確指定 `-mdaz-ftz` 仍會連結）。本機 gcc 11 仍是舊行為，此差異未在 gcc 13 上實測。
4. **`a * 10 / 10` 被化簡成 `a`**：重新結合讓溢位消失。看起來是「更對」，但它讓程式行為依賴最佳化細節。

對 Kahan 加總的影響更直接（同一支 `sum.c`）：

```
$ gcc -O2 -ffast-math sum.c -o sum -lm && ./sum
naive     1087937          abs err 8.79e+04    rel err 0.0879
pairwise  1000000.06       abs err 0.0476      rel err 4.76e-08
kahan     1087937          abs err 8.79e+04    rel err 0.0879
neumaier  1087937          abs err 8.79e+04    rel err 0.0879

[1, 1e20, 1, -1e20]: naive 0, kahan 0, neumaier 0
```

代數上 `c = ((s + y) - s) - y` 恆等於 0，`-fassociative-math` 允許編譯器這樣化簡，Kahan 和 Neumaier 的補償被整個刪掉，退化成逐一累加。原文以 gcc 14 測得「只開 `-ffast-math` 時向量化改變了累加順序，Kahan 反而輸出正確值」；本機 gcc 11 不論加不加 `-fno-tree-vectorize`，補償都被消掉。**同一個旗標，不同版本的編譯器給出不同結果**——這正是數值敏感程式碼不該用 `-ffast-math` 的理由。如果效能真的需要，改用細項旗標（例如只開 `-fno-math-errno`，讓 `sqrt` 編成單一指令），並把數值核心放在單獨不開 fast-math 的檔案。

### CPU 之外：Arm、GPU、DSP（本段未實測，為文件與原文整理）

以下內容本機沒有對應硬體，整理自原文與廠商文件，請以實際裝置為準：

- **Arm**：AArch32 的 VFP 指令符合 IEEE 754，NEON 指令卻固定 flush-to-zero、預設 NaN、只支援就近捨入；AArch64 的 Advanced SIMD 才完整遵循 IEEE 754。浮點例外攔截在 AArch64 是選配功能，許多處理器沒有實作。
- **NVIDIA GPU**：雙精度從 compute capability 1.3 起提供 IEEE 754 捨入、次正規數與 FMA；單精度到 2.0（Fermi）才不再 flush-to-zero。GPU 沒有浮點例外旗標；`nvcc --use_fast_math` 會啟用 FTZ 與近似的除法、平方根、超越函數。NVIDIA 的白皮書 *Precision & Performance: Floating Point and IEEE 754 Compliance for NVIDIA GPUs*（2011）有詳細說明。
- **DSP**：許多多媒體 DSP 為了面積與功耗沒有浮點單元（例如 Hexagon 680 的 HVX 向量單元只處理定點，到 Hexagon V68 才加入浮點向量），常見做法是量化成定點數模擬。
- **數學函式庫**：IEEE 754 只**建議**而不要求 `sin`、`exp` 等超越函數正確捨入，glibc、Apple libm、MSVC 的結果常差 1 個 ULP 左右。C23 保留了 `cr_` 開頭的函式名稱，供未來的正確捨入版本使用；[CORE-MATH 專案](https://core-math.gitlabpages.inria.fr/)正在提供這類實作。

**要求逐位元可重現時的檢查清單**：固定運算順序（不要平行歸約，或用固定的歸約樹）、關掉 contraction（`-ffp-contract=off`）、不用 `-ffast-math`、不用平台的 libm（自帶實作或用 CORE-MATH / CRlibm）、確認沒有人改 MXCSR（FTZ/DAZ、捨入模式是**每個執行緒**的狀態）。做不到時，退回定點數——就像《哥薩克》最後做的那樣。

## IEEE 754-2008/2019 與 C23

標準在 1985 年之後修訂過兩次：

**IEEE 754-2008**：併入十進位浮點（decimal32/64/128，給金融用）；新增 binary16（半精度，作為儲存格式）與 binary128；把 FMA 列為必備運算；建議（但不要求）超越函數正確捨入；建議 quiet/signaling NaN 用尾數最高位區分。

**IEEE 754-2019**（亦即 ISO/IEC 60559:2020）：移除 2008 年版的 `minNum`/`maxNum`（遇到 sNaN 時不滿足結合律），改成建議的 `minimum`/`maximum`（NaN 會傳遞，且 $-0 < +0$）與 `minimumNumber`/`maximumNumber`（忽略 NaN）；新增 augmented 運算（同時回傳捨入結果與誤差，用於可重現加總）；新增 NaN payload 存取與以 $\pi$ 為單位的三角函數。

C23 把 Annex F 更新為對應 ISO/IEC 60559:2020，並把 TS 18661 的多數內容收進正式標準。在 Docker `gcc:14` 實測：

```c
#define __STDC_WANT_IEC_60559_TYPES_EXT__ 1
#define __STDC_WANT_IEC_60559_EXT__ 1
#include <float.h>
#include <math.h>
#include <stdio.h>

int main(void)
{
    printf("__STDC_VERSION__ = %ldL\n", (long) __STDC_VERSION__);
#ifdef __STDC_IEC_60559_BFP__
    printf("__STDC_IEC_60559_BFP__ = %ldL\n", (long) __STDC_IEC_60559_BFP__);
#endif
    printf("FLT_IS_IEC_60559 = %d, FLT_NORM_MAX = %a\n", FLT_IS_IEC_60559, FLT_NORM_MAX);

    float s = FLT_SNAN, q = NAN;
    printf("issignaling(FLT_SNAN) = %d, issignaling(NAN) = %d\n", issignaling(s), issignaling(q));
    printf("issubnormal(FLT_TRUE_MIN) = %d, iszero(-0.0) = %d\n",
           issubnormal(FLT_TRUE_MIN), iszero(-0.0));
    printf("roundeven(2.5) = %.1f, round(2.5) = %.1f\n", roundeven(2.5), round(2.5));
    printf("nextup(1.0) - 1.0 = %a, nextdown(1.0) = %a\n", nextup(1.0) - 1.0, nextdown(1.0));
    printf("fmaximum(NAN, 1.0) = %f, fmaximum_num(NAN, 1.0) = %f\n",
           fmaximum(NAN, 1.0), fmaximum_num(NAN, 1.0));
    printf("fminimum(-0.0, +0.0) = %+.1f, fmin(-0.0, +0.0) = %+.1f\n",
           fminimum(-0.0, 0.0), fmin(-0.0, 0.0));
    double a = -0.0, b = 0.0;
    printf("totalorder(-0, +0) = %d, totalorder(+0, -0) = %d\n",
           totalorder(&a, &b), totalorder(&b, &a));
    printf("exp10(3) = %g\n", exp10(3.0));

    _Float16 h = 1.0f16 / 3.0f16;            /* binary16 */
    printf("_Float16 1/3 = %.10g, FLT16_EPSILON = %g, FLT16_MAX = %g\n",
           (double) h, (double) FLT16_EPSILON, (double) FLT16_MAX);

    __bf16 bf = (__bf16) 3.14159f;
    __bf16 big = (__bf16) 256.0f;
    __bf16 sum = (__bf16) ((float) big + 1.0f);
    printf("__bf16(3.14159) = %.10g, bf16(256+1) = %g\n", (double) (float) bf, (double) (float) sum);
    return 0;
}
```

```
$ gcc -std=c23 -Wall -Wextra c23.c -o c23 -lm && ./c23     # Docker gcc:14, glibc 2.41
__STDC_VERSION__ = 202000L
__STDC_IEC_60559_BFP__ = 201404L
FLT_IS_IEC_60559 = 1, FLT_NORM_MAX = 0x1.fffffep+127
issignaling(FLT_SNAN) = 1, issignaling(NAN) = 0
issubnormal(FLT_TRUE_MIN) = 1, iszero(-0.0) = 1
roundeven(2.5) = 2.0, round(2.5) = 3.0
nextup(1.0) - 1.0 = 0x1p-52, nextdown(1.0) = 0x1.fffffffffffffp-1
fmaximum(NAN, 1.0) = nan, fmaximum_num(NAN, 1.0) = 1.000000
fminimum(-0.0, +0.0) = -0.0, fmin(-0.0, +0.0) = -0.0
totalorder(-0, +0) = 1, totalorder(+0, -0) = 0
exp10(3) = 1000
_Float16 1/3 = 0.3332519531, FLT16_EPSILON = 0.000976562, FLT16_MAX = 65504
__bf16(3.14159) = 3.140625, bf16(256+1) = 256
```

幾個值得注意的地方：

- **`__STDC_VERSION__` 是 `202000L`**，不是 C23 定案後的 `202311L`：gcc 14 的 `-std=c23` 仍用暫定值。用版本號偵測 C23 功能時，判斷條件寫 `> 201710L` 比較保險。
- **一開始沒加那兩行 `#define` 時編不過**：`totalorder` 是 implicit declaration、`FLT16_EPSILON` 未宣告。glibc 2.41 把這些放在 `__STDC_WANT_IEC_60559_EXT__` 與 `__STDC_WANT_IEC_60559_TYPES_EXT__` 後面（C23 Annex H 的 `_FloatN` 系列巨集也要求這樣啟用）。實作對 C23 的支援程度不一，用之前先實測。
- `fmaximum` 傳遞 NaN、`fmaximum_num` 忽略 NaN；`totalorder` 讓 $-0 < +0$，連 NaN 都有固定位置，可以直接拿來寫正確的 `qsort` 比較函式（Ch 25 的失敗示範）。
- `fmin(-0.0, +0.0)` 在這裡回傳 $-0$，但 C99 的 `fmin` **沒有規定** ±0 的順序；`fminimum` 才保證。

**`_FloatN` 型別**：C23 Annex H 定義 `_Float16`、`_Float32`、`_Float64`、`_Float128` 這些交換格式型別與 `_Float32x`、`_Float64x` 延伸格式型別（選配）。gcc 早就以擴充形式提供，本機 gcc 11 可以用 `_Float128`（軟體模擬的 binary128）：

```
$ gcc -Wall -Wextra floatn.c -o floatn -lm && ./floatn       # gcc 11.4
_Float128 1/3 = 0.333333333333333333333333333333333317
_Float64x 1/3 = 0.333333333333333333342
FLT128_MANT_DIG = 113, FLT64X_MANT_DIG = 64
sizeof: _Float32 4, _Float64 8, _Float64x 16, _Float128 16
```

`_Float64x` 在 x86-64 就是 80 位元的 `long double`。但 `_Float16` 在 gcc 11 的 x86 上還不支援：

```
f16.c:1:1: error: '_Float16' is not supported on this target
f16.c:2:1: error: unknown type name '__bf16'
```

x86 的 `_Float16` 要 gcc 12 起，`__bf16` 的算術支援要 gcc 13 起，所以上面的 C23 程式在 Docker 裡跑。

## BFloat16 與 FP8：深度學習的低精度格式

神經網路訓練的瓶頸常常是**記憶體頻寬與乘法器面積**，而不是精度：權重和梯度只要大致對、範圍夠大就能收斂。這催生了比 binary16 更「怪」的格式：

```
             s  exponent   fraction        範圍                精度（有效位）
binary32     1     8          23           ~1e-38 .. 3.4e38      24
binary16     1     5          10           ~6e-5  .. 65504       11
bfloat16     1     8           7           ~1e-38 .. 3.4e38       8
FP8 E5M2     1     5           2           ~6e-5  .. 57344        3
FP8 E4M3     1     4           3           ~0.016 .. 448          4
```

**BFloat16**（brain floating point，Google Brain 提出）的設計重點是**指數和 FP32 一樣寬**：動態範圍完全相同，FP32 轉 BF16 等於砍掉低 16 位元。binary16 的指數只有 5 位，梯度一小就下溢、一大就溢位，訓練時得搭配 loss scaling；BF16 不必。乘法器面積大致與尾數寬度平方成正比，8 位元有效位數的乘法器比 24 位元的小得多，這是 TPU 選 BF16 的理由。

FP32 轉 BF16 的兩種做法，親手實作：

```c
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static uint32_t f2u(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
static float u2f(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

/* FP32 → BF16：直接截掉低 16 位元（等於朝 0 捨入） */
static uint16_t bf16_trunc(float f) { return (uint16_t) (f2u(f) >> 16); }

/* FP32 → BF16：就近捨入、平手取偶數（NaN 另外處理，避免進位把 NaN 變成 Inf） */
static uint16_t bf16_rne(float f)
{
    uint32_t u = f2u(f);
    if ((u & 0x7FFFFFFF) > 0x7F800000)        /* NaN：保留為 quiet NaN */
        return (uint16_t) ((u >> 16) | 0x0040);
    uint32_t lsb = (u >> 16) & 1;             /* 保留部分的最低位元 */
    u += 0x7FFF + lsb;                        /* 0x7FFF = 「差一點就一半」，加上 lsb 處理平手 */
    return (uint16_t) (u >> 16);
}

static float bf16_to_f32(uint16_t h) { return u2f((uint32_t) h << 16); }

int main(void)
{
    float xs[] = {3.14159265f, 1.0f / 3.0f, 257.0f, 259.0f, 65504.0f, 1e-40f, 3.0e38f, NAN};
    printf("%-14s %-8s %-14s %-8s %-14s\n", "fp32", "trunc", "value", "rne", "value");
    for (int i = 0; i < 8; i++) {
        uint16_t t = bf16_trunc(xs[i]), r = bf16_rne(xs[i]);
        printf("%-14.8g 0x%04X   %-14.8g 0x%04X   %-14.8g\n", xs[i], t,
               bf16_to_f32(t), r, bf16_to_f32(r));
    }
    return 0;
}
```

```
fp32           trunc    value          rne      value
3.1415927      0x4049   3.140625       0x4049   3.140625
0.33333334     0x3EAA   0.33203125     0x3EAB   0.33398438
257            0x4380   256            0x4380   256
259            0x4381   258            0x4382   260
65504          0x477F   65280          0x4780   65536
9.9999461e-41  0x0001   9.1835496e-41  0x0001   9.1835496e-41
3e+38          0x7F61   2.990763e+38   0x7F62   3.0040553e+38
nan            0x7FC0   nan            0x7FC0   nan
```

- **捨入的技巧**：加上 `0x7FFF`（比被截掉部分的一半少 1）再截斷，被截部分大於一半就進位、小於一半不進位；恰好一半時加上保留部分的最低位 `lsb`，奇數進位、偶數不進位，就是平手取偶數。這個寫法和 Ch 24 的位元技巧同一家族。
- **257 與 259**：BF16 在 $[256, 512)$ 的間距是 2。257 是平手，256 的最低位是偶數，留在 256；259 也是平手，258 是奇數、260 是偶數，所以進到 260。截斷版本一律往下。
- **65504**（binary16 的最大值）轉 BF16 進位成 65536：BF16 沒有溢位問題，binary16 有。
- **NaN 特判**：不特判的話，尾數全 1 的 NaN 加上 `0x7FFF` 可能進位到指數，NaN 變成 Inf 或 0。
- Docker 裡 gcc 14 的原生 `__bf16` 得到 `3.140625` 與 `256 + 1 = 256`，和手寫版本一致。BF16 在 256 附近加 1 就被吃掉，所以**累加一律用 FP32**，BF16 只用在儲存與乘法的輸入。

**FP8** 走得更遠。Arm、Intel、NVIDIA 共同提出、由 Open Compute Project 制定的 OFP8 規格有兩種：E4M3 偏重精度、E5M2 偏重範圍（E5M2 有 Inf 與 NaN，結構和 IEEE 754 一致；E4M3 為了多擠一點範圍，**沒有 Inf**，只有 `S.1111.111` 是 NaN，所以 `0x7E` 才是最大值）。用一個通用解碼器驗證：

```
$ ./fp8
E4M3: max 448, min normal 0.015625, min subnormal 0.00195312, 0x7F = nan
E5M2: max 57344, min normal 6.10352e-05, min subnormal 1.52588e-05, 0x7C = inf
E4M3 values in [1, 2): 1 1.125 1.25 1.375 1.5 1.625 1.75 1.875
E5M2 values in [1, 2): 1 1.25 1.5 1.75
```

E4M3 在 $[1, 2)$ 只有 8 個值、E5M2 只有 4 個。實務上訓練時前向傳遞用 E4M3、梯度用 E5M2，並且每個張量配一個 FP32 的縮放因子把數值搬進可表示範圍。IEEE 也成立了 P3109 工作小組，制定機器學習用的低精度算術標準。完整解碼器程式碼留給練習 C 的延伸挑戰。

## 對比與取捨

| 補救手段 | 解決什麼 | 不解決什麼 | 成本 |
|---|---|---|---|
| 改寫算式（`expm1`、韋達、`hypot`） | 相消、中間溢位 | 輸入本身的誤差 | 需要數學分析 |
| 換更寬的型別 | 累積、吞噬（延後發生） | 相消、條件數不佳 | 記憶體與頻寬加倍、SIMD 減半 |
| Kahan / Neumaier / 成對加總 | 加總的累積誤差 | 正負相消、輸入誤差 | 每項數倍運算 |
| 定點數 | 可重現性、無 FPU 平台、固定絕對精度 | 大動態範圍 | 手動管理比例與溢位 |
| 有理數 / 任意精度 | 精確或任意高精度 | 效能 | 慢 1–3 個數量級以上 |
| 區間算術（捨入模式） | 得到保證的誤差界 | 區間可能過度膨脹 | 每個運算算兩次 |

## 踩雷集錦

1. **「換成 `double` 就沒事了」**：`double` 把 Anarchy Online 的時鐘撐住了，但 $(1-\cos x)/x^2$ 在 $x = 10^{-8}$ 時一樣是 0。換型別延後吞噬與累積，**不解決相消**。
2. **「用 `fabs(a-b) < 1e-9` 比較」**：大數時永遠不成立、小數時永遠成立。用相對誤差 + 絕對下限，或 ULP 距離，門檻從誤差分析推。
3. **「Kahan 加總一定比較準」**：`-ffast-math` 會把補償整個刪掉（本機 gcc 11 實測）；加數比累加值大時 Kahan 失效，要 Neumaier；Neumaier 的補償項自己也會累積誤差。用之前在**實際的編譯選項**下測。
4. **「`-ffast-math` 只是讓程式快一點」**：它改變語意（`x/x` 變 1、`isnan` 變 0），還透過 `crtfastmath.o` 改了整個行程的 MXCSR。
5. **「同一份程式碼在每台機器結果都一樣」**：FMA contraction、x87、libm、平行歸約順序、別人改的 MXCSR，任何一個都能讓結果在最後幾位不同；混沌系統（原文的邏輯斯蒂映射）會把這幾位放大到整個結果。
6. **「`0.1f` 和 `0.1` 一樣」**：`float f = 0.1f; if (f == 0.1)` 永遠為假——`f` 提升為 `double` 後是 0.100000001490116…。混用 `float` 與 `double` 常數是嵌入式程式最常見的此類 bug，加 `-Wdouble-promotion` 可以抓。

## 進階：再往深一層

**條件數（condition number）**：$\kappa = \left|\frac{x f'(x)}{f(x)}\right|$ 描述「輸入的相對誤差被放大幾倍」。相消的本質就是 $f(x, y) = x - y$ 在 $x \approx y$ 時條件數很大。條件數是**問題**的性質，穩定性是**演算法**的性質：條件數大的問題，再好的演算法也救不了；條件數小的問題被不穩定的演算法算壞（單遍變異數公式），才是可以修的。這組概念是 Higham 那本書的骨架。

**誤差分析工具**：[Herbie](https://herbie.uwplse.org/)（PLDI 2015）自動把浮點算式改寫成更準確的形式，丟 `(1 - cos(x)) / (x * x)` 進去，它會建議類似本章的改寫；gcc/clang 的 `-fsanitize=float-divide-by-zero`、`-fsanitize=float-cast-overflow` 在執行期抓除以零與越界轉換。

**Posit 與其他替代格式**：John Gustafson 提出的 posit（unum type III）用可變長度的「regime」欄位，在 1 附近給更多精度、極端值給更少，部分研究顯示同位寬下精度優於 IEEE 754。它有支持者也有批評者（例如 Kahan 本人），目前沒有主流硬體採用；這是有爭議、尚無共識的領域。

**面試常見題**：「為什麼金融不用 `double` 存金額？」（0.01 無法精確表示，累加後分錢對不上；用整數的「分」或十進位浮點）「Kahan 加總為什麼在 `-ffast-math` 下失效？」（代數化簡把補償消成 0）「`a + b + c` 和 `a + (b + c)` 一樣嗎？」（不一樣，浮點加法沒有結合律）。

## 動手練習

1. 用 `sum.c` 的四個演算法加總 $1, 1/2, 1/3, \ldots, 1/10^7$（調和級數）。先從大加到小、再從小加到大，比較逐一累加的誤差差多少，解釋為什麼「從小加到大」比較準。
2. 把 Kahan 加總獨立寫在一個檔案，用 `-O2 -ffast-math -S` 編譯，在組合語言裡確認補償的那兩道減法還在不在。再加上 `__attribute__((optimize("no-fast-math")))` 或把該檔案改用 `-O2` 單獨編譯，看能不能救回來。
3. 用 MPFR 以 200 位元精度計算 $\sin(10^{22})$，再和 glibc 的 `sin(1e22)` 比較差幾個 ULP。（提示：$10^{22}$ 可精確表示為 `double`，這是測試 libm 參數縮減的經典輸入。）
4. 實作 `two_sum(a, b, &s, &e)`（不要求 $|a| \ge |b|$ 的 Knuth 版本，6 次運算）與 `two_prod(a, b, &p, &e)`（用 `fma`），驗證 $a + b = s + e$ 與 $a \times b = p + e$ 對隨機輸入都精確成立（用 `_Float128` 或 MPFR 當參考）。

## 本章重點整理

- 每個運算 $\text{fl}(a \circ b) = (a \circ b)(1+\delta)$、$|\delta| \le u$；麻煩來自累積、吞噬、相消三種放大機制，再加上輸入本身的表示誤差。
- 相消要靠改寫算式消除減法（`expm1`、`log1p`、韋達定理、共軛式），提高精度只能延後問題。
- 比較用相對誤差 + 絕對下限或 ULP 距離，門檻從誤差分析推，沒有萬用的 epsilon。
- Kahan 補償把加總誤差界降到與 $n$ 幾乎無關；Neumaier 處理加數大於累加值的情況；兩者都會被 `-ffast-math` 消滅。
- 逐位元可重現需要固定運算順序、關掉 contraction 與 fast-math、避開平台 libm；做不到就用定點數。
- BF16 保留 FP32 的範圍、犧牲精度；FP8 E4M3/E5M2 在精度與範圍間取捨；低精度格式一律搭配高精度累加。

## 自我檢核

- [ ] 不看筆記，能用一個三行的例子向同事解釋什麼是災難性相消，以及為什麼換 `double` 救不了
- [ ] 能在紙上推一次 Kahan 加總的一輪，說明 `c` 為什麼精確等於捨入誤差、需要什麼前提
- [ ] 面試官問「為什麼你的單元測試在 CI（不同 CPU）上浮點結果不一樣？」，能列出至少四個可能原因與對應的檢查方法
- [ ] 能說出 `-ffast-math` 裡至少三個子旗標各自允許編譯器做什麼，以及它為什麼會影響沒用它編譯的程式碼
- [ ] 能解釋 BF16 和 binary16 各自犧牲了什麼，以及為什麼 BF16 訓練不需要 loss scaling
- [ ] 想一個你寫過的、用浮點數累加或比較的程式，這章的哪個樣態可能在它身上發生？

## 延伸閱讀

### 論文

- **[Further Remarks on Reducing Truncation Errors](https://doi.org/10.1145/363707.363723)** — W. Kahan，Communications of the ACM 8(1)（1965）
  - **核心貢獻**：Kahan 補償加總的原始論文，只有一頁。
  - **讀哪裡**：整篇；記號是 1960 年代的風格，對照本章的 C 實作讀。
  - **和本章的關聯**：本章「補償加總」一節的出處。前提：懂本章的 Kahan 推導即可。
- **[Automatically Improving Accuracy for Floating Point Expressions](https://fpbench.cs.washington.edu/pldi15-paper.pdf)** — Panchekha, Sanchez-Stern, Wilcox, Tatlock，PLDI（2015）
  - **核心貢獻**：Herbie 工具，以取樣找出誤差大的輸入區域，再用重寫規則搜尋更準確的等價算式。
  - **讀哪裡**：Section 1–3（問題定義與方法）；Section 5 的評估可以略讀。
  - **和本章的關聯**：把本章「改寫算式」的人工技巧自動化；讀完會理解為什麼改寫需要「依輸入區間分段」。
- **[FP8 Formats for Deep Learning](https://arxiv.org/abs/2209.05433)** — Micikevicius 等（NVIDIA、Arm、Intel），arXiv（2022）
  - **核心貢獻**：提出 E4M3 / E5M2 兩種 FP8 格式並在大型模型上驗證訓練與推論。
  - **讀哪裡**：Section 2（格式定義，含 E4M3 為何不要 Inf）與 Section 4（實驗）。
  - **和本章的關聯**：本章 FP8 解碼器的規格來源；前提：讀懂本章 BF16 一節。

### 部落格 / 技術文章

- **[Comparing Floating Point Numbers, 2012 Edition](https://randomascii.wordpress.com/2012/02/25/comparing-floating-point-numbers-2012-edition/)** — Bruce Dawson（Random ASCII）
  - **這篇說什麼**：相對誤差、ULP 比較、接近 0 時的處理，每種方法的失敗案例都有。
  - **讀哪裡**：整篇；同系列的 *Floating-Point Determinism* 一文對應本章跨平台一節。
  - **為什麼值得讀**：作者長年在 Valve、Google 處理遊戲與 Chrome 的浮點問題，這個系列是業界引用最多的實務指南。
- **[The many ways of converting FP32 to FP16](https://www.corsix.org/content/converting-fp32-to-fp16)** — Peter Cawley
  - **這篇說什麼**：從純位元操作到硬體指令（F16C），各種 FP32→FP16 轉換的實作與正確捨入技巧。
  - **讀哪裡**：整篇；前半的位元技巧可直接對照本章 `bf16_rne`。
  - **為什麼值得讀**：把「加偏移量再截斷」的捨入技巧講到極致，也是練習 C 延伸挑戰（實作 binary16 轉換）的最佳參考。

### 官方文件 / Spec

- **[GCC Manual：Optimize Options](https://gcc.gnu.org/onlinedocs/gcc/Optimize-Options.html)**
  - **讀哪裡**：搜尋 `-ffast-math`、`-ffp-contract`、`-frounding-math`、`-fexcess-precision` 四個條目；每一條都寫了它允許的變換與預設值。
  - **注意**：預設值會隨版本改變（例如 C 標準模式下的 `-ffp-contract`），請讀你手上 gcc 版本的手冊。
- **[OCP 8-bit Floating Point Specification (OFP8) Rev 1.0](https://www.opencompute.org/documents/ocp-8-bit-floating-point-specification-ofp8-revision-1-0-2023-06-20-pdf)**
  - **讀哪裡**：Section 5（E4M3 與 E5M2 的編碼表、特殊值）。
  - **注意**：轉換時的飽和（saturation）模式是選項，與 IEEE 754 的溢位到 Inf 不同。

### 書籍

- **《Accuracy and Stability of Numerical Algorithms》2nd ed.** — Nicholas J. Higham（SIAM，2002）
  - **這本書的定位**：數值誤差分析的標準教科書，出版超過 20 年，內容（誤差界、條件數、加總演算法）至今仍是領域基準。
  - **讀哪幾章**：第 1 章（原則與經典範例）、第 2 章（浮點運算模型，即本章的 $\text{fl}(a \circ b)$ 式）、第 4 章（加總，Kahan 與成對加總的完整誤差界）。
- **《Handbook of Floating-Point Arithmetic》2nd ed.** — Jean-Michel Muller 等（Birkhäuser，2018）
  - **這本書的定位**：Ch 25 已推薦；本章對應的是第 4–5 章（TwoSum、FMA 無誤差變換、補償演算法）與第 10 章（正確捨入的數學函式）。

格式、誤差、補救都看過了，接下來動手：自己寫一套不碰 FPU 的 Q16.16 定點數庫，再寫一個能把任何 `float`/`double` 拆開重建的解碼器，把 Part 4 的位元與數值知識整合起來。

→ [練習 C：定點數運算庫與浮點解碼器](./practice-c-fixed-point.md)
