# 練習 C — 定點數運算庫與浮點解碼器

> **目標**：把 Ch 21–26 學到的二補數、位元操作、integer overflow 防禦與 IEEE 754 格式拼起來，做出兩個工具：一套不碰 FPU、有明確溢位與捨入語意的 **Q16.16 定點數庫**，以及一個能把任何 `float` / `double` 拆成 sign / exponent / mantissa、正確分類、再**只用整數欄位重建數值**的**浮點解碼器**。完成後你驗證了自己能在位元層級精確推理「一個數到底是什麼」。

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4。所有測試以 `-std=c11 -Wall -Wextra -fsanitize=address,undefined` 通過。

## 背景與動機

**定點數庫**模擬的是嵌入式與核心開發的日常：沒有 FPU 的 MCU（Cortex-M0、許多 RISC-V 核心）、不准用浮點的 Linux 核心、需要逐位元可重現的遊戲鎖步同步（Ch 26 的《哥薩克》案例）、DSP 的量化運算。libfixmath、Q 格式的 CMSIS-DSP、Linux 的 `loadavg` 都是同類產物。寫一套定點數庫逼你面對浮點數替你藏起來的所有問題：乘法要多寬的中間值、除法怎麼捨入、負數右移的方向、溢位時該飽和還是回繞、開根號沒有 `sqrtf` 可叫。

**浮點解碼器**則是除錯工具：當 `0.1 + 0.2 != 0.3`、當某個值莫名變成 `-nan`、當序列化後讀回來差了一點，第一件事就是把位元攤開來看。[h-schmidt.net 的 IEEE-754 Converter](https://www.h-schmidt.net/FloatConverter/IEEE754.html) 做的就是這件事，這裡你自己寫一個，而且要能處理 `double`，並對全部 $2^{32}$ 個 `float` 位元樣式驗證正確。

> 開始前建議先複習：[Ch 22 — integer overflow](./22-integer-overflow.md)（有號溢位是 UB）、[Ch 23 — bitwise 基礎](./23-bitwise-basics.md)（負數右移是實作定義）、[Ch 25 — IEEE 754](./25-ieee754.md)（欄位格式與次正規數）、[Ch 26](./26-floating-point-errors.md) 的「補救二：定點數」。

## 任務規格

### Part 1：Q16.16 定點數庫

型別與常數（放在 `fix16.h`）：

| 名稱 | 定義 | 說明 |
|---|---|---|
| `fix16_t` | `int32_t` | 數值 = raw / 65536 |
| `FIX16_ONE` | `0x00010000` | 1.0 |
| `FIX16_MAX` | `INT32_MAX` | 32767.9999847… |
| `FIX16_MIN` | `INT32_MIN` | −32768.0 |
| `fix16_status` | `unsigned` 全域變數 | 狀態旗標，仿 `<fenv.h>`：運算只**設定**位元，由呼叫端讀取與清除 |

旗標：`FIX16_OVERFLOW`（結果超出範圍，已飽和）、`FIX16_DIVZERO`、`FIX16_INVALID`（負數開根號、NaN 轉換）、`FIX16_INEXACT`（結果經過捨入）。

函式與語意：

| 函式 | 語意 |
|---|---|
| `fix16_from_int(n)` | `n * 65536`，超出範圍飽和並設 OVERFLOW |
| `fix16_from_double(d)` | 就近捨入（平手遠離零）；NaN → 0 並設 INVALID；±Inf 與超出範圍 → 飽和並設 OVERFLOW；有捨入時設 INEXACT |
| `fix16_to_double(x)` | 精確轉換（必須無誤差） |
| `fix16_to_int(x)` | 就近捨入，平手遠離零 |
| `fix16_add` / `fix16_sub` | 飽和加減 |
| `fix16_mul` | 就近捨入（平手遠離零），飽和 |
| `fix16_div` | 就近捨入（平手遠離零），飽和；除以 0：被除數正 → MAX、負 → MIN、0 → 0，並設 DIVZERO |
| `fix16_sqrt` | 就近捨入；負數 → 0 並設 INVALID；**只能用整數運算**（牛頓法） |

限制：

- 程式中**不得有任何有號整數溢位**（UBSan 會檢查）。
- 不得依賴「負數右移是算術右移」這個實作定義行為來做捨入（可以用，但要能說明為什麼結果仍正確；參考解答完全避開）。
- `fix16_sqrt`、`fix16_mul`、`fix16_div` 內部**不得使用浮點運算**。
- 精度要求：所有不溢位的 `mul`、`div`、`sqrt` 結果，與數學真值的誤差不得超過 **0.5 LSB**（$2^{-17}$）。

### Part 2：浮點解碼器

- 寫一個**通用**的解碼函式，輸入是位元樣式（`uint64_t`）與格式參數（指數欄寬、尾數欄寬），因此同一份程式碼能處理 binary32 與 binary64。
- 輸出：sign、指數欄原值與去偏移後的指數、尾數欄、分類（zero / subnormal / normal / infinity / quiet NaN / signaling NaN）、以 $\pm 1.f \times 2^{e}$（或 $0.f$）表示的公式。
- **手動重建數值**：只用解出來的整數欄位與 `ldexp` 重建 `double`，並和原值比較（包含 $-0$ 的正負號）。不准把原本的位元 `memcpy` 回去當成「重建」。
- 驗收：窮舉全部 $2^{32}$ 個 binary32 位元樣式，分類必須與 `fpclassify` 一致、非 NaN 的重建值必須與 C 自己的解讀逐位元相同。

## 期望輸出範例

定點數庫的展示程式（`demo.c`，見參考解答後的測試段）：

```
3.5 + -1.25            = 2.250000     raw 0x00024000  flags:
3.5 * -1.25            = -4.375000    raw 0xFFFBA000  flags:
3.5 / -1.25            = -2.800003    raw 0xFFFD3333  flags: INEXACT
1 / 3                  = 0.333328     raw 0x00005555  flags: INEXACT
sqrt(2)                = 1.414215     raw 0x00016A0A  flags: INEXACT
200 * 200              = 32767.999985 raw 0x7FFFFFFF  flags: OVERFLOW
1 / 0                  = 32767.999985 raw 0x7FFFFFFF  flags: DIVZERO
sqrt(-1)               = 0.000000     raw 0x00000000  flags: INVALID
from_double(0.1)       = 0.100006     raw 0x0000199A  flags: INEXACT
carrot #9999           = 12503.550003 raw 0x30D78CCD  flags:
```

最後一列是 Ch 26 的種胡蘿蔔：4.8 轉 Q16.16 時有 $3 \times 10^{-6}$ 的表示誤差，但之後 9999 次加法**完全沒有誤差**（1.25 可精確表示、整數加法不捨入），誤差從頭到尾都是那 $3 \times 10^{-6}$，不會像 `float` 一樣在跨越 2 的冪時跳動。

浮點解碼器（節錄）：

```
binary32 0x3E200000  value 0.15625
  sign     0 (+)
  exponent 01111100 (raw 124, unbiased -3)
  mantissa 01000000000000000000000
  class    normal
  formula  +1.25 x 2^-3
  rebuilt  0.15625  [match]

binary32 0x00000001  value 1.4012984643248171e-45
  sign     0 (+)
  exponent 00000000 (raw 0, unbiased -126)
  mantissa 00000000000000000000001
  class    subnormal
  formula  +1.1920928955078125e-07 x 2^-126
  rebuilt  1.4012984643248171e-45  [match]

binary32 0x7FA00001  value nan
  sign     0 (+)
  exponent 11111111 (raw 255)
  mantissa 01000000000000000000001
  class    signaling NaN
  rebuilt  nan  [match]
```

邊界輸入：

```
輸入：DBL_MIN / 4（binary64 次正規數）
輸出：exponent 00000000000 (raw 0, unbiased -1022)
      mantissa 0100...0
      class    subnormal
      formula  +0.25 x 2^-1022
```

```
輸入：窮舉模式（./fpdecode all）
輸出：
zero                    2
subnormal        16777214
normal         4261412864
infinity                2
quiet NaN         8388608
signaling NaN     8388606
mismatches: 0
```

這些數字可以手算驗證：normal 有 254 種指數 × $2^{23}$ 種尾數 × 2 種正負號 $= 4261412864$；subnormal 是 $(2^{23} - 1) \times 2$；qNaN 是 $2^{22} \times 2$；sNaN 是 $(2^{22} - 1) \times 2$（尾數全 0 是 Inf，不算 sNaN）。

## 如果你卡住了

1. **乘法結果全錯或 UBSan 報錯？** 先想：兩個 Q16.16 相乘，乘積在數學上最多有幾個位元？`(a * b) >> 16` 這種寫法，乘法是在什麼型別裡做的？這是本機跑出來的錯誤版本：

   ```
   badmul.c:6:58: runtime error: signed integer overflow: 196608 * 131072 cannot be represented in type 'int'
   bad_mul(3, 2)   = 0
   ```

   3.0 × 2.0 得到 0。把其中一個運算元先轉成 `int64_t`。
2. **捨入在負數時不對稱？** `((int64_t) a * b) >> 16` 是把結果「往 $-\infty$」捨入（算術右移等於 floor），所以 $+2^{-16} \times 0.5$ 得 0、$-2^{-16} \times 0.5$ 卻得 $-2^{-16}$（本機實測 `trunc_mul(-2^-16, 0.5) raw = -1`）。想想看：如果先取絕對值、做無號的「加一半再除」，最後再把正負號放回去，是不是就對稱了？
3. **除法的精度不夠？** `(a / b) << 16` 先除後移，小數部分在第一步就沒了。反過來先把被除數左移 16 位——那需要多寬的型別？`INT32_MIN / -1` 這種組合又會發生什麼？
4. **牛頓法開根號不收斂或差 1？** 從**大於**真值的初值出發，牛頓法 $r \leftarrow (r + n/r)/2$ 會單調遞減；一旦下一步不再變小就停，得到的是 $\lfloor\sqrt{n}\rfloor$。初值怎麼保證大於真值？（提示：用 `__builtin_clzll` 算位元數，參考 [Ch 24](./24-bitwise-tricks.md)。）向下取整之後，怎麼只用整數判斷要不要進位？
5. **解碼器的次正規數重建差了 2 倍？** 次正規數的指數是 $1 - \text{bias}$，不是 $0 - \text{bias}$；而且沒有隱含的 1。
6. **`fix16_from_double(1e30)` 被 UBSan 抓到？** 先轉 `int64_t` 再飽和是不夠的：`double` 超出 `int64_t` 範圍時，轉換本身就是 UB（N1570 6.3.1.4）。範圍檢查要在 `double` 的世界裡做完。

## 實作步驟建議

### Step 1：型別、轉換與 `fix16_to_double`

先寫 `fix16.h` 與最簡單的 `fix16_from_int`、`fix16_to_double`。子目標：`fix16_to_double(fix16_from_int(-32768)) == -32768.0`、`fix16_to_double(FIX16_MAX)` 印出 `32767.9999847412109375`（用 `%.20g` 看，必須精確）。想清楚為什麼 `int32_t / 65536.0` 一定精確（提示：$2^{31} < 2^{53}$，除以 2 的冪只改指數）。

### Step 2：飽和加減與狀態旗標

寫一個 `static fix16_t saturate(int64_t v)`，所有運算先在 `int64_t` 裡算完再交給它。子目標：`FIX16_MAX + 1`、`FIX16_MIN - 1`、`0 - FIX16_MIN` 三個案例都飽和並設 OVERFLOW，且 UBSan 安靜。

### Step 3：捨入除法工具與乘除法

寫 `static int64_t div_round(int64_t num, int64_t den)`（`den > 0`，就近、平手遠離零），乘法與除法都靠它。子目標：通過下方測試表中所有 mul/div 列，包括平手（`1 raw × 0.5`）與 `INT32_MIN / -1`。

### Step 4：`fix16_from_double`

處理 NaN、±Inf、超出範圍、捨入。子目標：`0.1` → raw 6554 且 INEXACT、`1e9` → MAX 且 OVERFLOW、`NAN` → 0 且 INVALID。

### Step 5：整數牛頓法 `fix16_sqrt`

推導：$\sqrt{x/2^{16}} \times 2^{16} = \sqrt{x \times 2^{16}}$，所以對 64 位元整數 `x << 16` 開根號就是答案。先寫 `isqrt64` 向下取整，再用餘數判斷進位。子目標：`sqrt(4) = 2` 精確、`sqrt(2)` raw 92682、`sqrt(1 raw) = 256 raw`（$\sqrt{2^{-16}} = 2^{-8}$）。

### Step 6：浮點解碼器

先寫通用的 `fp_decode(bits, format)`，回傳一個 struct；再寫 `report()` 印出；最後寫 `decode_float` / `decode_double` 兩個用 `memcpy` 取位元的包裝。子目標：前面「期望輸出」的每一個案例都 `[match]`。

### Step 7：整合與測試

- 定點數：寫表格式的單元測試（巨集比對 raw 值與旗標），再加一百萬組隨機輸入，以 `long double`（64 位元尾數，能精確表示兩個 Q16.16 的乘積）為參考，檢查誤差 ≤ 0.5 LSB。
- 解碼器：窮舉 $2^{32}$ 個 `float`，`-O2` 下本機約 40 秒。
- 全部用 `-fsanitize=address,undefined` 跑一次。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作：fix16.h</summary>

```c
#ifndef FIX16_H
#define FIX16_H

#include <stdint.h>

/* Q16.16：32 位元有號整數，高 16 位是整數部分、低 16 位是小數部分
 * 數值 = raw / 65536，範圍 [-32768, 32767.9999847]，解析度 2^-16 ≈ 1.53e-5 */
typedef int32_t fix16_t;

#define FIX16_FRAC_BITS 16
#define FIX16_ONE       ((fix16_t) 0x00010000)   /* 1.0 */
#define FIX16_HALF      ((fix16_t) 0x00008000)   /* 0.5 */
#define FIX16_MAX       ((fix16_t) INT32_MAX)    /* 32767.99998... */
#define FIX16_MIN       ((fix16_t) INT32_MIN)    /* -32768.0 */

/* 狀態旗標：仿 <fenv.h>，運算只會「設定」旗標，由呼叫端讀取與清除 */
enum {
    FIX16_OK       = 0,
    FIX16_OVERFLOW = 1 << 0,   /* 結果超出範圍，已飽和到 MAX/MIN */
    FIX16_DIVZERO  = 1 << 1,   /* 除以零 */
    FIX16_INVALID  = 1 << 2,   /* 負數開根號、NaN 轉換 */
    FIX16_INEXACT  = 1 << 3,   /* 結果經過捨入 */
};
extern unsigned fix16_status;

fix16_t fix16_from_int(int32_t n);
fix16_t fix16_from_double(double d);
double  fix16_to_double(fix16_t x);
int32_t fix16_to_int(fix16_t x);              /* 就近捨入，平手遠離零 */

fix16_t fix16_add(fix16_t a, fix16_t b);
fix16_t fix16_sub(fix16_t a, fix16_t b);
fix16_t fix16_mul(fix16_t a, fix16_t b);
fix16_t fix16_div(fix16_t a, fix16_t b);
fix16_t fix16_sqrt(fix16_t x);

#endif
```

</details>

<details>
<summary>點開參考實作：fix16.c</summary>

```c
#include "fix16.h"

#include <math.h>

unsigned fix16_status = FIX16_OK;

/* 把 64 位元的中間結果飽和到 32 位元範圍 */
static fix16_t saturate(int64_t v)
{
    if (v > INT32_MAX) {
        fix16_status |= FIX16_OVERFLOW;
        return FIX16_MAX;
    }
    if (v < INT32_MIN) {
        fix16_status |= FIX16_OVERFLOW;
        return FIX16_MIN;
    }
    return (fix16_t) v;
}

/* 有號除法，就近捨入、平手遠離零；den > 0。
 * 用絕對值運算，避開「負數右移是實作定義」與「/ 朝零截斷」兩個陷阱 */
static int64_t div_round(int64_t num, int64_t den)
{
    uint64_t un = num < 0 ? (uint64_t) 0 - (uint64_t) num : (uint64_t) num;
    uint64_t q = un / (uint64_t) den, r = un % (uint64_t) den;
    if (r != 0)
        fix16_status |= FIX16_INEXACT;
    if (r >= (uint64_t) den - r)           /* r >= den/2，不用 den/2 以免奇數 den 出錯 */
        q++;
    return num < 0 ? -(int64_t) q : (int64_t) q;
}

fix16_t fix16_from_int(int32_t n)
{
    return saturate((int64_t) n * FIX16_ONE);
}

fix16_t fix16_from_double(double d)
{
    if (isnan(d)) {
        fix16_status |= FIX16_INVALID;
        return 0;
    }
    double scaled = d * 65536.0;           /* 乘 2 的冪是精確的（不溢位時） */
    /* 先在 double 的世界判斷範圍：超出 int64 再轉換會是未定義行為 */
    if (scaled >= 2147483647.5) {
        fix16_status |= FIX16_OVERFLOW;
        return FIX16_MAX;
    }
    if (scaled < -2147483648.5) {
        fix16_status |= FIX16_OVERFLOW;
        return FIX16_MIN;
    }
    double r = round(scaled);              /* 平手遠離零 */
    if (r != scaled)
        fix16_status |= FIX16_INEXACT;
    return saturate((int64_t) r);
}

double fix16_to_double(fix16_t x)
{
    return (double) x / 65536.0;           /* 32 位元整數放得進 53 位元尾數：精確 */
}

int32_t fix16_to_int(fix16_t x)
{
    return (int32_t) div_round(x, FIX16_ONE);
}

fix16_t fix16_add(fix16_t a, fix16_t b)
{
    return saturate((int64_t) a + b);      /* 先升到 64 位元，避免有號溢位 UB */
}

fix16_t fix16_sub(fix16_t a, fix16_t b)
{
    return saturate((int64_t) a - b);
}

fix16_t fix16_mul(fix16_t a, fix16_t b)
{
    /* (a/2^16)(b/2^16) = ab/2^32，要存成 Q16.16 就得 ab/2^16
     * |ab| <= 2^62，int64 放得下 */
    int64_t p = (int64_t) a * b;
    return saturate(div_round(p, FIX16_ONE));
}

fix16_t fix16_div(fix16_t a, fix16_t b)
{
    if (b == 0) {
        fix16_status |= FIX16_DIVZERO;
        return a > 0 ? FIX16_MAX : a < 0 ? FIX16_MIN : 0;
    }
    /* (a/2^16)/(b/2^16) = a/b，要存成 Q16.16 就得 a*2^16/b
     * a*2^16 最多 2^47，int64 放得下；b 為負時把符號移到分子 */
    int64_t num = (int64_t) a * FIX16_ONE;
    int64_t den = b;
    if (den < 0) {
        num = -num;
        den = -den;                        /* den 最小是 INT32_MIN，取負在 int64 不會溢位 */
    }
    return saturate(div_round(num, den));
}

/* 64 位元整數平方根（向下取整），牛頓法：r_{k+1} = (r_k + n / r_k) / 2 */
static uint64_t isqrt64(uint64_t n)
{
    if (n < 2)
        return n;
    /* 初值取 >= sqrt(n) 的 2 的冪，牛頓法從上方單調遞減收斂 */
    int bits = 64 - __builtin_clzll(n);
    uint64_t r = (uint64_t) 1 << ((bits + 1) / 2);
    for (;;) {
        uint64_t next = (r + n / r) / 2;
        if (next >= r)
            return r;
        r = next;
    }
}

fix16_t fix16_sqrt(fix16_t x)
{
    if (x < 0) {
        fix16_status |= FIX16_INVALID;
        return 0;
    }
    /* sqrt(x/2^16) * 2^16 = sqrt(x * 2^16)：對整數 x<<16 開根號即得 Q16.16 */
    uint64_t n = (uint64_t) x << 16;
    uint64_t r = isqrt64(n);
    uint64_t rem = n - r * r;
    if (rem != 0)
        fix16_status |= FIX16_INEXACT;
    if (rem > r)                           /* n > r^2 + r 等價於 sqrt(n) > r + 0.5 */
        r++;
    return (fix16_t) r;                    /* 最大 sqrt(2^47) < 2^24，不會溢位 */
}
```

**解答說明**：

- **所有運算都升到 `int64_t` 再飽和**：`(int64_t) a + b` 最大 $2^{32}$、`(int64_t) a * b` 最大 $2^{62}$、`(int64_t) a * 65536` 最大 $2^{47}$，全部放得下，沒有任何有號溢位。這比「先檢查會不會溢位再運算」（Ch 22 的做法）好寫，代價是需要 64 位元乘法——在 32 位元 MCU 上這是一個函式呼叫（`__aeabi_lmul`），若在意效能，可以改用 `__builtin_mul_overflow` 或拆成高低半部相乘。
- **`div_round` 用絕對值做無號除法**：C 的 `/` 朝零截斷、`>>` 對負數是實作定義（gcc 是算術右移，等於 floor），兩者的捨入方向都不對稱。取絕對值後做「商 + 是否進位」再放回正負號，正負對稱、與實作無關。`(uint64_t) 0 - (uint64_t) num` 這個寫法避開了 `-num` 在 `num == INT64_MIN` 時的溢位（雖然本庫的呼叫點不會傳入它，工具函式本身仍應安全）。
- **進位條件寫成 `r >= den - r`** 而不是 `r >= den / 2`：當 `den` 是奇數時 `den / 2` 會向下截斷，`r == den/2` 其實小於一半卻被判成平手。
- **除以負數時把符號移到分子**：`den` 最小是 `INT32_MIN`，在 `int64_t` 裡取負不會溢位。`FIX16_MIN / -FIX16_ONE` 的數學結果是 32768，超出範圍，被 `saturate` 抓到。
- **`fix16_from_double` 先在 `double` 裡比較範圍**：門檻 `2147483647.5` 對應「捨入後恰好超出 `INT32_MAX`」。`d * 65536.0` 只改指數，不溢位時是精確的。
- **`isqrt64` 的初值**：`bits` 是 `n` 的位元數，$2^{\lceil bits/2 \rceil} \ge \sqrt{n}$，牛頓法從上方單調遞減，`next >= r` 時 `r` 就是 $\lfloor\sqrt{n}\rfloor$。理由：只要 $r > \lfloor\sqrt{n}\rfloor$，就有 $n/r < r$，所以下一步嚴格變小；而由算術幾何平均不等式，每一步的結果都不會低於 $\lfloor\sqrt{n}\rfloor$，因此第一次「不再變小」時 $r$ 恰為 $\lfloor\sqrt{n}\rfloor$。
- **sqrt 的進位判斷 `rem > r`**：$\sqrt{n} > r + 0.5 \iff n > r^2 + r + 0.25 \iff \text{rem} > r + 0.25$，整數下即 `rem > r`。$\sqrt{n}$ 不可能恰好等於 $r + 0.5$（否則 $4n = (2r+1)^2$ 是奇數，矛盾），所以沒有平手。

</details>

<details>
<summary>點開參考實作：test_fix16.c</summary>

```c
#include "fix16.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>

static int pass, fail;

/* 檢查 raw 值與旗標；expect_flags 為 -1 表示不檢查旗標 */
#define CHECK(expr, want_raw, want_flags)                                         \
    do {                                                                          \
        fix16_status = 0;                                                         \
        fix16_t got_ = (expr);                                                    \
        int ok_ = got_ == (fix16_t) (want_raw) &&                                 \
                  ((want_flags) < 0 || fix16_status == (unsigned) (want_flags));  \
        if (ok_) pass++;                                                          \
        else {                                                                    \
            fail++;                                                               \
            printf("FAIL %-40s got 0x%08X flags %u, want 0x%08X flags %d\n",      \
                   #expr, (unsigned) got_, fix16_status, (unsigned) (want_raw),   \
                   (int) (want_flags));                                           \
        }                                                                         \
    } while (0)

#define F(d) fix16_from_double(d)
enum { OK = FIX16_OK, OV = FIX16_OVERFLOW, DZ = FIX16_DIVZERO,
       INV = FIX16_INVALID, INX = FIX16_INEXACT };

static void test_convert(void)
{
    CHECK(fix16_from_int(1), 0x00010000, OK);
    CHECK(fix16_from_int(-32768), 0x80000000, OK);
    CHECK(fix16_from_int(32767), 0x7FFF0000, OK);
    CHECK(fix16_from_int(32768), 0x7FFFFFFF, OV);
    CHECK(F(0.5), 0x00008000, OK);
    CHECK(F(-1.5), -0x00018000, OK);
    CHECK(F(0.1), 6554, INX);                  /* 6553.6 → 6554 */
    CHECK(F(1e-6), 0, INX);                    /* 0.065536 → 0 */
    CHECK(F(NAN), 0, INV);
    CHECK(F(1e9), 0x7FFFFFFF, OV);
    CHECK(F(-INFINITY), (int32_t) 0x80000000, OV);
    CHECK(F(32767.9999847412109375), 0x7FFFFFFF, OK);
}

static void test_add_sub(void)
{
    CHECK(fix16_add(F(1.5), F(2.25)), 0x0003C000, OK);
    CHECK(fix16_sub(F(1.5), F(2.25)), -0x0000C000, OK);
    CHECK(fix16_add(FIX16_MAX, FIX16_ONE), 0x7FFFFFFF, OV);
    CHECK(fix16_sub(FIX16_MIN, FIX16_ONE), (int32_t) 0x80000000, OV);
    CHECK(fix16_sub(0, FIX16_MIN), 0x7FFFFFFF, OV);   /* -(-32768) 放不下 */
}

static void test_mul(void)
{
    CHECK(fix16_mul(F(1.5), F(2.5)), 0x0003C000, OK);
    CHECK(fix16_mul(F(-1.5), F(2.5)), -0x0003C000, OK);
    CHECK(fix16_mul(F(0.1), F(0.1)), 655, INX);       /* 42954916/65536 = 655.43 */
    CHECK(fix16_mul(1, FIX16_HALF), 1, INX);          /* 0.5 平手 → 遠離零 */
    CHECK(fix16_mul(-1, FIX16_HALF), -1, INX);
    CHECK(fix16_mul(F(200), F(200)), 0x7FFFFFFF, OV);
    CHECK(fix16_mul(F(-256), F(128)), (int32_t) 0x80000000, OK);
    CHECK(fix16_mul(F(-200), F(200)), (int32_t) 0x80000000, OV);
}

static void test_div(void)
{
    CHECK(fix16_div(FIX16_ONE, F(3)), 21845, INX);    /* 21845.33 */
    CHECK(fix16_div(F(2), F(3)), 43691, INX);         /* 43690.67 */
    CHECK(fix16_div(-FIX16_ONE, F(3)), -21845, INX);
    CHECK(fix16_div(FIX16_ONE, F(-3)), -21845, INX);
    CHECK(fix16_div(F(7.5), F(2.5)), 0x00030000, OK);
    CHECK(fix16_div(FIX16_ONE, 0), 0x7FFFFFFF, DZ);
    CHECK(fix16_div(-FIX16_ONE, 0), (int32_t) 0x80000000, DZ);
    CHECK(fix16_div(0, 0), 0, DZ);
    CHECK(fix16_div(F(32767), F(0.5)), 0x7FFFFFFF, OV);
    CHECK(fix16_div(FIX16_MIN, -FIX16_ONE), 0x7FFFFFFF, OV);
    CHECK(fix16_div(1, F(2)), 1, INX);                /* 0.5 raw 平手 → 1 */
}

static void test_sqrt(void)
{
    CHECK(fix16_sqrt(F(4)), 0x00020000, OK);
    CHECK(fix16_sqrt(F(2)), 92682, INX);              /* 1.41421356*65536 = 92681.9 */
    CHECK(fix16_sqrt(0), 0, OK);
    CHECK(fix16_sqrt(1), 256, OK);                    /* sqrt(2^-16) = 2^-8 */
    CHECK(fix16_sqrt(FIX16_MAX), 11863283, INX);      /* sqrt(32767.99998) = 181.019 */
    CHECK(fix16_sqrt(-FIX16_ONE), 0, INV);
}

static void test_to_int(void)
{
    fix16_status = 0;
    int ok = fix16_to_int(F(2.5)) == 3 && fix16_to_int(F(-2.5)) == -3 &&
             fix16_to_int(F(2.49998)) == 2 && fix16_to_int(FIX16_MIN) == -32768 &&
             fix16_to_int(FIX16_MAX) == 32768;
    ok ? pass++ : (fail++, printf("FAIL fix16_to_int\n"));
}

/* 隨機測試：以 long double（64 位尾數）當參考，誤差不得超過半個 LSB */
static void test_random(void)
{
    const long double half_lsb = 0.5L / 65536.0L;
    long bad = 0;
    srand(12345);
    for (int i = 0; i < 1000000; i++) {
        fix16_t a = (fix16_t) (((uint32_t) rand() << 16) ^ (uint32_t) rand());
        fix16_t b = (fix16_t) (((uint32_t) rand() << 16) ^ (uint32_t) rand()) >> (rand() % 24);
        long double la = a / 65536.0L, lb = b / 65536.0L;

        fix16_status = 0;
        fix16_t m = fix16_mul(a, b);
        if (!(fix16_status & FIX16_OVERFLOW) && fabsl(m / 65536.0L - la * lb) > half_lsb)
            bad++;
        if (b != 0) {
            fix16_status = 0;
            fix16_t d = fix16_div(a, b);
            if (!(fix16_status & FIX16_OVERFLOW) &&
                fabsl(d / 65536.0L - la / lb) > half_lsb * (1 + 1e-12L))
                bad++;
        }
        if (a >= 0) {
            fix16_t s = fix16_sqrt(a);
            if (fabsl(s / 65536.0L - sqrtl(la)) > half_lsb * (1 + 1e-12L))
                bad++;
        }
    }
    bad == 0 ? pass++ : (fail++, printf("FAIL random: %ld mismatches\n", bad));
}

int main(void)
{
    test_convert();
    test_add_sub();
    test_mul();
    test_div();
    test_sqrt();
    test_to_int();
    test_random();
    printf("%d passed, %d failed\n", pass, fail);
    return fail != 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra -O2 -g -fsanitize=address,undefined fix16.c test_fix16.c -o test_fix16 -lm
$ ./test_fix16
44 passed, 0 failed
```

**測試設計說明**：`CHECK` 巨集每次先清 `fix16_status`，同時比對 raw 值與旗標，旗標比對是「完全相等」，所以多設或少設一個 INEXACT 都會失敗——這逼你把每個函式的旗標語意想清楚。隨機測試用 `long double` 當參考：x86-64 的 `long double` 有 64 位元尾數，兩個 Q16.16 的乘積（最多 62 位元）可以精確表示；除法與開根號的參考值本身有約 $2^{-64}$ 的相對誤差，所以門檻乘上 $1 + 10^{-12}$ 的寬容係數，避免把參考值的誤差誤判成失敗。注意這個參考方法**依賴 x86 的 80 位元 `long double`**，在 `long double` 等於 `double` 的平台（例如 MSVC、部分 Arm）上要改用 `__int128` 或 MPFR。

</details>

<details>
<summary>點開參考實作：fpdecode.c</summary>

```c
#include <float.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

/* 一個 IEEE 754 二進位格式由兩個參數決定：指數欄寬與尾數欄寬 */
struct fp_format { const char *name; int ebits, mbits; };
static const struct fp_format BINARY32 = {"binary32", 8, 23};
static const struct fp_format BINARY64 = {"binary64", 11, 52};

enum fp_class { FPC_ZERO, FPC_SUBNORMAL, FPC_NORMAL, FPC_INF, FPC_QNAN, FPC_SNAN };
static const char *class_name[] = {"zero", "subnormal", "normal", "infinity",
                                   "quiet NaN", "signaling NaN"};

struct fp_info {
    unsigned sign;
    uint64_t exp_raw, mant;
    int exp_unbiased;        /* 只對 normal / subnormal 有意義 */
    enum fp_class cls;
    double rebuilt;          /* 只用整數欄位與 ldexp 重建的值 */
};

static struct fp_info fp_decode(uint64_t bits, struct fp_format f)
{
    struct fp_info r;
    const int bias = (1 << (f.ebits - 1)) - 1;
    const uint64_t emax = (UINT64_C(1) << f.ebits) - 1;
    const uint64_t mmask = (UINT64_C(1) << f.mbits) - 1;

    r.sign = (unsigned) (bits >> (f.ebits + f.mbits)) & 1;
    r.exp_raw = (bits >> f.mbits) & emax;
    r.mant = bits & mmask;
    r.exp_unbiased = 0;
    double s = r.sign ? -1.0 : 1.0;

    if (r.exp_raw == emax) {                         /* 指數全 1：Inf 或 NaN */
        if (r.mant == 0) {
            r.cls = FPC_INF;
            r.rebuilt = s * INFINITY;
        } else {
            /* IEEE 754-2008 建議：尾數最高位元 1 = quiet，0 = signaling */
            r.cls = (r.mant >> (f.mbits - 1)) & 1 ? FPC_QNAN : FPC_SNAN;
            r.rebuilt = NAN;
        }
    } else if (r.exp_raw == 0) {                     /* 指數全 0：零或次正規數 */
        r.cls = r.mant == 0 ? FPC_ZERO : FPC_SUBNORMAL;
        r.exp_unbiased = 1 - bias;                   /* 注意是 1 - bias，不是 0 - bias */
        r.rebuilt = s * ldexp((double) r.mant, 1 - bias - f.mbits);
    } else {                                         /* 正規數：補回隱含的 1 */
        r.cls = FPC_NORMAL;
        r.exp_unbiased = (int) r.exp_raw - bias;
        uint64_t sig = (UINT64_C(1) << f.mbits) | r.mant;
        r.rebuilt = s * ldexp((double) sig, r.exp_unbiased - f.mbits);
    }
    return r;
}

static void print_bits(uint64_t v, int n)
{
    for (int i = n - 1; i >= 0; i--)
        putchar('0' + (int) ((v >> i) & 1));
}

static void report(uint64_t bits, struct fp_format f, double original)
{
    struct fp_info r = fp_decode(bits, f);
    int hexw = (1 + f.ebits + f.mbits) / 4;
    printf("%s 0x%0*" PRIX64 "  value %.17g\n", f.name, hexw, bits, original);
    printf("  sign     %u (%c)\n", r.sign, r.sign ? '-' : '+');
    printf("  exponent ");
    print_bits(r.exp_raw, f.ebits);
    printf(" (raw %" PRIu64, r.exp_raw);
    if (r.cls == FPC_NORMAL || r.cls == FPC_SUBNORMAL)
        printf(", unbiased %d", r.exp_unbiased);
    printf(")\n  mantissa ");
    print_bits(r.mant, f.mbits);
    printf("\n  class    %s\n", class_name[r.cls]);
    if (r.cls == FPC_NORMAL || r.cls == FPC_SUBNORMAL || r.cls == FPC_ZERO) {
        double frac = ldexp((double) r.mant, -f.mbits);  /* 0.m */
        printf("  formula  %c%.17g x 2^%d\n", r.sign ? '-' : '+',
               (r.cls == FPC_NORMAL ? 1.0 : 0.0) + frac, r.exp_unbiased);
    }
    int same = isnan(original) ? isnan(r.rebuilt)
                               : (r.rebuilt == original && !signbit(r.rebuilt) == !signbit(original));
    printf("  rebuilt  %.17g  [%s]\n\n", r.rebuilt, same ? "match" : "MISMATCH");
}

static void decode_float(float x)
{
    uint32_t u;
    memcpy(&u, &x, sizeof u);
    report(u, BINARY32, x);
}

static void decode_double(double x)
{
    uint64_t u;
    memcpy(&u, &x, sizeof u);
    report(u, BINARY64, x);
}

static float float_from_bits(uint32_t u) { float f; memcpy(&f, &u, sizeof f); return f; }

/* 窮舉全部 2^32 個 binary32 位元樣式：重建值必須與 C 自己的解讀一致，
 * 分類必須與 fpclassify 一致 */
static int exhaustive(void)
{
    uint64_t bad = 0, count[6] = {0};
    for (uint64_t b = 0; b <= UINT32_MAX; b++) {
        float x = float_from_bits((uint32_t) b);
        struct fp_info r = fp_decode(b, BINARY32);
        count[r.cls]++;
        int c = fpclassify(x);
        int cls_ok = (c == FP_ZERO && r.cls == FPC_ZERO) ||
                     (c == FP_SUBNORMAL && r.cls == FPC_SUBNORMAL) ||
                     (c == FP_NORMAL && r.cls == FPC_NORMAL) ||
                     (c == FP_INFINITE && r.cls == FPC_INF) ||
                     (c == FP_NAN && (r.cls == FPC_QNAN || r.cls == FPC_SNAN));
        int val_ok = c == FP_NAN ||
                     (r.rebuilt == (double) x && !signbit(r.rebuilt) == !signbit(x));
        if (!cls_ok || !val_ok)
            bad++;
    }
    for (int i = 0; i < 6; i++)
        printf("%-14s %10" PRIu64 "\n", class_name[i], count[i]);
    printf("mismatches: %" PRIu64 "\n", bad);
    return bad != 0;
}

int main(int argc, char **argv)
{
    (void) argv;
    if (argc > 1)
        return exhaustive();
    decode_float(0.15625f);
    decode_float(-0.1f);
    decode_float(FLT_TRUE_MIN);
    decode_float(-0.0f);
    decode_float(float_from_bits(0x7FA00001));   /* sNaN */
    decode_double(0.1);
    decode_double(-INFINITY);
    decode_double(DBL_MIN / 4);
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra -O2 fpdecode.c -o fpdecode -lm
$ time ./fpdecode all
zero                    2
subnormal        16777214
normal         4261412864
infinity                2
quiet NaN         8388608
signaling NaN     8388606
mismatches: 0

real	0m39.522s
```

**解答說明**：

- **格式參數化**：`bias = 2^(ebits-1) - 1`、`emax = 2^ebits - 1`，binary32 與 binary64 只差兩個數字。同一個函式傳 `{5, 10}` 就能解 binary16、傳 `{8, 7}` 就能解 bfloat16（延伸挑戰）。
- **重建只用整數欄位**：正規數用 `ldexp((1 << mbits) | mant, e - bias - mbits)`，把「$1.f \times 2^e$」改寫成「整數 $\times 2^{e - mbits}$」。`(double)` 轉換精確，因為 binary64 的 53 位元有效數字放得下 24 或 53 位元的整數；`ldexp` 乘 2 的冪也精確（不溢位、不下溢時）。這也說明了為什麼 binary64 是解碼 binary32 的完美容器。
- **比較時檢查 `signbit`**：`-0.0 == 0.0` 為真，只比 `==` 會漏掉正負號錯誤的 bug。
- **窮舉迴圈用 `uint64_t b <= UINT32_MAX`**：若用 `uint32_t b` 搭配 `b <= UINT32_MAX`，條件永遠成立，迴圈不會結束——經典的無號回繞陷阱（Ch 22）。
- **qNaN/sNaN 判斷依 IEEE 754-2008 的建議**：尾數最高位為 1 是 quiet。這在 x86 與 Arm 成立，在舊 MIPS 上相反，解碼器在那些平台上會給出相反的分類。
- 把 sNaN 以 `float` 參數傳進 `decode_float` 是安全的：傳參與 `memcpy` 不做浮點運算，sNaN 不會被安靜化。但 `report` 的第三個參數是 `double`，`float` 轉 `double` **是**一次浮點運算，sNaN 在那裡會變成 qNaN（並設 invalid 旗標）——所以解碼一定要在轉型之前，用原始位元做。

</details>

<details>
<summary>點開參考實作：demo.c（期望輸出範例的程式）</summary>

```c
#include "fix16.h"

#include <stdio.h>

static void show(const char *expr, fix16_t r)
{
    printf("%-22s = %-12.6f raw 0x%08X  flags:%s%s%s%s\n", expr, fix16_to_double(r),
           (unsigned) r,
           fix16_status & FIX16_OVERFLOW ? " OVERFLOW" : "",
           fix16_status & FIX16_DIVZERO ? " DIVZERO" : "",
           fix16_status & FIX16_INVALID ? " INVALID" : "",
           fix16_status & FIX16_INEXACT ? " INEXACT" : "");
    fix16_status = 0;
}

int main(void)
{
    fix16_t a = fix16_from_double(3.5), b = fix16_from_double(-1.25);
    show("3.5 + -1.25", fix16_add(a, b));
    show("3.5 * -1.25", fix16_mul(a, b));
    show("3.5 / -1.25", fix16_div(a, b));
    show("1 / 3", fix16_div(FIX16_ONE, fix16_from_int(3)));
    show("sqrt(2)", fix16_sqrt(fix16_from_int(2)));
    show("200 * 200", fix16_mul(fix16_from_int(200), fix16_from_int(200)));
    show("1 / 0", fix16_div(FIX16_ONE, 0));
    show("sqrt(-1)", fix16_sqrt(-FIX16_ONE));
    show("from_double(0.1)", fix16_from_double(0.1));

    /* 種胡蘿蔔：4.8 + i * 1.25，用 Q16.16 累加 10000 次 */
    fix16_t pos = fix16_from_double(4.8), step = fix16_from_double(1.25);
    fix16_status = 0;
    for (int i = 0; i < 9999; i++)
        pos = fix16_add(pos, step);
    show("carrot #9999", pos);
    return 0;
}
```

</details>

## 測試用例

### 定點數庫

| 輸入 | 預期 raw | 預期旗標 | 說明 |
|---|---|---|---|
| `from_int(32767)` | `0x7FFF0000` | — | 最大整數 |
| `from_int(32768)` | `0x7FFFFFFF` | OVERFLOW | 飽和 |
| `from_double(0.1)` | 6554 | INEXACT | 6553.6 → 6554 |
| `from_double(NAN)` | 0 | INVALID | |
| `from_double(-INFINITY)` | `0x80000000` | OVERFLOW | |
| `from_double(32767.9999847412109375)` | `0x7FFFFFFF` | — | 恰好是 MAX，精確 |
| `add(MAX, ONE)` | `0x7FFFFFFF` | OVERFLOW | |
| `sub(0, MIN)` | `0x7FFFFFFF` | OVERFLOW | −(−32768) 放不下 |
| `mul(1.5, 2.5)` | `0x0003C000` | — | 3.75 精確 |
| `mul(0.1, 0.1)` | 655 | INEXACT | 42954916 / 65536 = 655.43 |
| `mul(1 raw, 0.5)` | 1 | INEXACT | 平手遠離零 |
| `mul(−1 raw, 0.5)` | −1 | INEXACT | 對稱 |
| `mul(−256, 128)` | `0x80000000` | — | 恰好是 MIN，不算溢位 |
| `mul(200, 200)` | `0x7FFFFFFF` | OVERFLOW | |
| `div(1, 3)` | 21845 | INEXACT | 21845.33 |
| `div(2, 3)` | 43691 | INEXACT | 43690.67 進位 |
| `div(1, −3)` | −21845 | INEXACT | 除數為負 |
| `div(1, 0)` / `div(−1, 0)` / `div(0, 0)` | MAX / MIN / 0 | DIVZERO | |
| `div(MIN, −1)` | `0x7FFFFFFF` | OVERFLOW | 32768 放不下 |
| `sqrt(4)` | `0x00020000` | — | |
| `sqrt(2)` | 92682 | INEXACT | 92681.9 進位 |
| `sqrt(1 raw)` | 256 | — | $\sqrt{2^{-16}} = 2^{-8}$ |
| `sqrt(MAX)` | 11863283 | INEXACT | ≈ 181.019 |
| `sqrt(−1)` | 0 | INVALID | 非法輸入 |
| 隨機 $10^6$ 組 mul/div/sqrt | 誤差 ≤ 0.5 LSB | — | 效能邊界：本機 -O2（無 sanitizer）整支測試 0.06 秒 |

### 浮點解碼器

| 輸入 | 預期分類 | 預期指數（去偏移） | 說明 |
|---|---|---|---|
| `0.15625f` | normal | −3 | 尾數 `01000…` |
| `-0.1f` | normal | −4 | 位元 `0xBDCCCCCD` |
| `FLT_TRUE_MIN` | subnormal | −126 | 尾數只有最低位 1 |
| `-0.0f` | zero | — | 重建值必須是 $-0$ |
| 位元 `0x7FA00001` | signaling NaN | — | 尾數最高位 0 |
| `NAN`（`0x7FC00000`） | quiet NaN | — | |
| `-INFINITY`（double） | infinity | — | `0xFFF0000000000000` |
| `DBL_MIN / 4` | subnormal | −1022 | 公式 `+0.25 x 2^-1022` |
| 全部 $2^{32}$ 個 float | 與 `fpclassify` 一致 | — | mismatches 必須為 0 |

## 延伸挑戰（加分）

- **無 FPU 的字串輸出**：寫 `fix16_to_str(char *buf, fix16_t x, int decimals)`，只用整數運算印出十進位，並正確捨入（`decimals = 2` 時 `0.125` 要印 `0.13`）。然後拿它和 `printf("%.*f", ...)` 對拍所有 $2^{32}$ 個值。
- **binary16 / bfloat16 / FP8**：解碼器已經參數化，加入 `{5, 10}`、`{8, 7}`、E4M3 / E5M2。注意 OCP 的 E4M3 沒有 Inf、只有全 1 是 NaN，通用規則要加例外（Ch 26 的 FP8 一節）。再寫 FP32 → binary16 的就近捨入轉換，對拍 x86 的 `_cvtss_sh`（F16C 指令，本機 CPU 支援）。
- **CORDIC 三角函數**：用 Q16.16 與查表實作 `fix16_sin` / `fix16_cos`（每次迭代只有加減與位移），和 `sin` 比較最大誤差幾個 LSB。這是沒有乘法器的 FPGA 與早期計算機（HP-35）算三角函數的方法。
- **效能對決**：在 `-O2` 下比較 `fix16_mul` 與 `float` 乘法的吞吐量。在 x86-64 上定點數多半**不會**比較快（有 FPU 且 SIMD 寬），把結論寫成「在什麼平台、什麼條件下定點數比較快」，而不是「定點數比較快」。
- **飽和 vs 回繞**：加一組 `fix16_add_wrap` 等回繞版本（用 `uint32_t` 運算再轉回，避開 UB），討論 DSP（多半飽和）與 checksum（需要回繞）各自為什麼這樣選。

## 自我檢核

- [ ] 能不看解答說出 Q16.16 的乘法和除法各需要多寬的中間值、為什麼
- [ ] 能解釋為什麼 `x >> 16` 不是「除以 65536」，兩者在負數時各往哪個方向捨入
- [ ] 能說明整數牛頓法為什麼要從大於真值的初值開始，以及終止條件為什麼正確
- [ ] 能解釋窮舉時 normal、subnormal、qNaN、sNaN 各有幾個，並從欄位寬度推出來
- [ ] 能說出「浮點數轉整數前先在浮點的世界檢查範圍」的理由，並指出 C 規格書哪一條規定了不檢查的後果
- [ ] 能說出自己的實作和參考解答的差異（例如捨入方式、旗標語意、重建方法），並解釋各自取捨

Part 4 把數字在位元層級的樣子看透了：整數怎麼繞回、位元怎麼操作、浮點怎麼編碼與出錯。Part 5 換一個方向，看 C 語言自己的機制——從最古老也最容易誤用的前置處理器開始。

→ [Ch 27 前置處理器 I：巨集陷阱、_Generic 與 statement expression](./27-preprocessor-basics.md)
