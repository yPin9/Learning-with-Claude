# Ch 22 — integer overflow：語意、資安案例與防禦

> **目標**：分清楚 C 裡「有號溢位（未定義行為）」「無號回繞（有定義）」「轉換截斷（實作定義）」三種語意；能拆解 FreeBSD、XDR、OpenSSH、SACK Panic、MySQL、CVE-2016-0728 等真實漏洞的數值成因；看懂編譯器如何依未定義行為刪掉溢位檢查；並會用 `__builtin_*_overflow`、C23 `ckd_*`、`-ftrapv`、`-fwrapv`、UBSan 建立防線。

> **對應原文**：[你所不知道的 C 語言：數值系統篇](https://hackmd.io/@sysprog/c-numerics) 的「Integer Overflow」「Integer Overflow 案例分析」「數值表示與資訊安全」「有號溢位與迴圈變數的寬度」等節

> **環境**：WSL Ubuntu 22.04 x86_64（Ryzen 7 5700G），gcc 11.4、clang 14、glibc 2.35。C23 `<stdckdint.h>` 在 Docker `gcc:14`（GCC 14.4）執行並標明。所有漏洞重現都是**在自己的機器上執行的簡化模型**，不碰任何真實系統。

> 如果你對「無號是 ℤ/2^wℤ、有號是 ℤ」「整數提升」還不熟，先回看 [Ch 21 — 數值系統](./21-number-systems.md) 的「C 只把群結構交給無號型別」與「整數提升與一般算術轉換」兩節。

## 為什麼需要這個？

integer overflow 很少自己造成災難，它通常是**第一張骨牌**：一個乘法回繞，讓 `malloc` 拿到太小的緩衝區，接著 `memcpy` 照原本的個數寫入，變成 heap overflow；一個計數器回繞，讓參考計數提早歸零，物件被釋放後還有人在用，變成 use-after-free。CWE 把這條因果鏈直接命名為 CWE-680（Integer Overflow to Buffer Overflow）。

更麻煩的是，C 對「溢位」有三種完全不同的語意，而一般人腦中只有一種（「會繞回」）：

- 無號運算回繞：**有定義**，但多半是 bug。
- 有號運算溢位：**未定義行為（UB）**，編譯器可以假設它不發生，甚至刪掉你寫的檢查。
- 轉換成較窄的有號型別：**實作定義**，GCC/Clang 取模。

在這之前人們怎麼防？大多靠「事後檢查結果」：`if (a + b < a)`。這在無號型別上正確，在有號型別上卻會被編譯器當成恆假而刪除——2008 年 CERT 為此發布 VU#162289，Linux 核心則從 2009 年起一路以 `-fwrapv`、`-fno-strict-overflow` 對抗。C23 終於把「先檢查再運算」標準化成 `ckd_add`。這一章把這段演進講清楚。

## 先建立直覺

想像一台加油機，金額顯示只有四位數。加到 9999 元再加 1 元，顯示 0000。問題不在顯示，而在**之後的決策用了這個顯示值**：

```
  使用者輸入 n（元素個數）、size（每個多大）
              │
              ▼
     bytes = n * size        ← 第一張骨牌：乘積超過 2^w，回繞成小數字
              │
              ▼
     if (bytes > LIMIT) 拒絕  ← 檢查用的是回繞後的小數字，通過了
              │
              ▼
     buf = malloc(bytes)     ← 配到「小」緩衝區
              │
              ▼
     for (i = 0; i < n; i++) ← 迴圈卻用原本的 n
         memcpy(buf + i*size, src[i], size)
              │
              ▼
          heap overflow → 任意寫入 → 可能執行任意程式碼
```

[Ch 21](./21-number-systems.md) 說過，ℤ/2^wℤ 上沒有與加法相容的大小順序。這張圖裡的 `if` 用的是 ℤ 的直覺，跑在 ℤ/2^wℤ 的機器上——這就是整章的核心矛盾。

## 三種語意：先把規則釘死

| 運算 | 型別 | 超出範圍時 | 規格書 |
|---|---|---|---|
| `+ - *`、`++`、`--` | 無號 | 取模 2^N，**有定義** | C11 6.2.5 §9 |
| `+ - *`、`-x`、`++` | 有號 | **未定義行為** | C11 6.5 §5 |
| `INT_MIN / -1`、`INT_MIN % -1` | 有號 | **未定義行為**（商無法表示） | C11 6.5.5 §6 |
| 整數 → 較窄無號 | — | 取模，有定義 | C11 6.3.1.3 §2 |
| 整數 → 較窄有號 | — | **實作定義**（GCC/Clang 取模） | C11 6.3.1.3 §3 |
| 浮點 → 整數，整數部分放不下 | — | **未定義行為** | C11 6.3.1.4 §1 |
| `<<` 結果放不下、位移量 ≥ 寬度 | — | **未定義行為** | C11 6.5.7 §3–4（[Ch 23](./23-bitwise-basics.md)） |

前兩列的差別用一支程式就看得到：

```c
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    int x = argc > 1 ? atoi(argv[1]) : INT_MAX;  /* 執行時才知道 */
    unsigned u = (unsigned) x;
    printf("u + 1 = %u\n", u + 1);   /* 定義良好：模 2^32 */
    printf("x + 1 = %d\n", x + 1);   /* x == INT_MAX 時是未定義行為 */
    return 0;
}
```

```
$ gcc -O2 wrap.c -o wrap && ./wrap
u + 1 = 2147483648
x + 1 = -2147483648
```

第二行「看起來」也繞回了，因為 x86 的 `add` 指令本來就是模 2³² 的。**但這是硬體在這次執行的行為，不是 C 的保證**。下面「編譯器怎麼利用 UB」一節會看到，只要編譯器能在編譯時期推論，結果就完全不同。

`INT_MIN / -1` 甚至連「看起來」都沒有——x86 的 `idiv` 會直接觸發除法例外：

```
$ gcc -O0 divmin.c -o dm && ./dm
-2147483648 / -1 = Floating point exception (core dumped)
$ echo $?
136
```

（136 = 128 + 8，SIGFPE 是 8 號訊號；名稱雖然叫 floating point，整數除法例外在 Linux 上也送這個訊號。）

## 三類真實事故

原文把溢位事故分成三類，這個分類很有用，因為三類的防法不同。

### 第一類：計數器回繞

```c
#include <stdio.h>

int main(void)
{
    double days787 = 2147483648.0 / (100.0 * 86400);    /* 2^31 個 1/100 秒 */
    double days_u32_ms = 4294967296.0 / (1000.0 * 86400); /* 2^32 毫秒 */
    double years_t32 = 2147483648.0 / (365.2425 * 86400); /* 32 位元 time_t */
    printf("int32 @ 100 Hz overflows after %.2f days\n", days787);
    printf("uint32 milliseconds wrap after %.2f days\n", days_u32_ms);
    printf("int32 seconds since 1970 overflow after %.2f years\n", years_t32);
    return 0;
}
```

```
int32 @ 100 Hz overflows after 248.55 days
uint32 milliseconds wrap after 49.71 days
int32 seconds since 1970 overflow after 68.05 years
```

- **波音 787**：發電機控制單元（GCU）連續供電 248 天後，4 部 GCU 同時進入 failsafe 模式。FAA 的 AD 2015-09-07 要求每 120 天內至少斷電一次。AD 沒有公布計數器的寬度與頻率；Phil Koopman 推算是「32 位元有號整數、每 1/100 秒加一」，第一行的 248.55 天與事故吻合。注意這是**外界推算**，不是官方說明。
- **49.7 天**：32 位元毫秒計數器（Windows 的 `GetTickCount`、許多 RTOS 的 tick）的經典回繞週期。
- **2038 年問題**：32 位元有號 `time_t` 從 1970 年起算，68 年後（2038-01-19）溢位。
- **Deep Impact**（2013 年 8 月失聯）：JPL 歸因於時間標記問題；外界推算「自 2000-01-01 起以 1/10 秒為單位的 32 位元無號值」恰在 2013-08-11 到達 2³²，但官方未公布確切成因。
- **〈江南 Style〉** 2014 年 12 月超過 2,147,483,647 次觀看。YouTube 說因此改用 64 位元，事後澄清那是彩蛋，計數器早就升級了——這是**預先避開**的溢位，不是事故。

防法：計數器用夠寬的型別（64 位元毫秒計數要 5.8 億年才回繞），或者在比較時間時只看**差值**（`(int32_t)(now - then) > timeout`，依靠無號回繞的同態性質）。

### 第二類：轉換超出範圍

**Ariane 5 首航（1996）**：慣性參考系統把 64 位元浮點的水平偏差值轉成 16 位元有號整數，值太大觸發未處理的 Operand Error，主備兩套系統以相同方式失效，火箭在起飛後約 40 秒解體。那段程式是 Ada，從 Ariane 4 沿用；Ariane 5 的飛行軌跡讓水平速度大得多。

在 C 裡，同樣的轉換（浮點 → 整數放不下）是**未定義行為**（C11 6.3.1.4 §1）；實作若遵循 Annex F，則引發 invalid 浮點例外並得到未指定的值。兩者都不讓未檢查的轉換變安全。

### 第三類：以輸入計算的長度與金額

- **OpenSSH 2.9.9–3.3（2002）**：sshd 以用戶端傳來的回應數 `nresp` 計算 `xmalloc(nresp * sizeof(char*))`，乘積溢位後配置過小，**未經認證**即可造成 heap overflow。
- **Bitcoin 區塊 74638（2010）**：一筆交易的輸出金額相加時溢位，通過檢查，憑空產生 184,467,440,737.09551616 BTC（CVE-2010-5139）。這個數字恰好是 2⁶⁴ = 18446744073709551616 satoshi（1 BTC = 10⁸ satoshi），正是 64 位元回繞的指紋。

三類之中，第三類在 C 程式碼裡最常見，下面的案例大多屬於它。

## 案例分析

以下每個案例都附一個**簡化重現**：抽出數值成因，在自己的機器上跑，不重現攻擊本身。

### 2002 年 FreeBSD：有號長度

FreeBSD-SA-02:38 指出 `accept`、`getsockname`、`getpeername` 等系統呼叫把長度參數當成有號整數，以負數呼叫即可通過邊界檢查，讓核心把大量記憶體複製到使用者空間。CS:APP 把它簡化成：

```c
#define KSIZE 1024
char kbuf[KSIZE];

int copy_from_kernel(void *user_dest, int maxlen)
{
    int len = KSIZE < maxlen ? KSIZE : maxlen;
    memcpy(user_dest, kbuf, len);
    return len;
}
```

`maxlen` 為負數時，`KSIZE < maxlen` 是兩個 `int` 的比較，`len` 得到該負數。`memcpy` 的第三個參數是 `size_t`，呼叫時依 C11 6.5.2.2 §7「as if by assignment」轉成 `size_t`，再依 6.3.1.3 §2 加上 2⁶⁴：

```c
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#define KSIZE 1024
static char kbuf[KSIZE];

/* 有漏洞的版本：長度用 int（只示範算出來的長度，不真的呼叫 memcpy） */
static int vuln_len(int maxlen)
{
    int len = KSIZE < maxlen ? KSIZE : maxlen;
    return len;
}

/* 修正版：長度一律以 size_t 表示，len 不超過 KSIZE */
static size_t copy_from_kernel(void *user_dest, size_t maxlen)
{
    size_t len = KSIZE < maxlen ? KSIZE : maxlen;
    memcpy(user_dest, kbuf, len);
    return len;
}

int main(void)
{
    int bad = vuln_len(-1);
    printf("vuln: len = %d, memcpy would see %zu\n", bad, (size_t) bad);

    char buf[16];
    printf("fixed: %zu\n", copy_from_kernel(buf, sizeof buf));
    return 0;
}
```

```
vuln: len = -1, memcpy would see 18446744073709551615
fixed: 16
```

−1 變成 `SIZE_MAX`。修正版讓長度從頭到尾都是 `size_t`，比較在無號中進行，`len` 不會超過 `KSIZE`。但這**不等於擋下負數**：呼叫端若把 `int` 的 −1 傳進來，依同一條規則成為 `SIZE_MAX`，函式照樣複製 1024 位元組；目的緩衝區不足 1024 時仍會越界寫入。`maxlen` 必須如實描述目的端容量，有號的長度要在**轉換之前**檢查。這類錯誤在 CWE 是 CWE-195（Signed to Unsigned Conversion Error）。

### 2002 年 XDR：乘法溢位

Sun RPC 的 XDR 程式庫在 `xdr_array` 中以「元素個數 × 元素大小」計算配置量，乘積溢位後配置過小，遠端攻擊者可藉此執行任意程式碼（CVE-2002-0391）。CS:APP 的簡化版：

```c
void *copy_elements(void *ele_src[], int ele_cnt, size_t ele_size)
{
    void *result = malloc(ele_cnt * ele_size);
    if (result == NULL)
        return NULL;
    void *next = result;
    for (int i = 0; i < ele_cnt; i++) {
        memcpy(next, ele_src[i], ele_size);
        next += ele_size; /* void * 的算術是 GNU 擴充 */
    }
    return result;
}
```

`ele_cnt * ele_size` 先經一般算術轉換：`size_t` 的等級不低於 `int`，`int` 轉成 `size_t`，以無號乘法計算，溢位時回繞（不是 UB）。`size_t` 為 32 位元時，ele_cnt = 2²⁰ + 1、ele_size = 2¹² 的乘積 2³² + 2¹² 回繞成 4096。我們的 WSL 沒有裝 32 位元函式庫，所以用 `uint32_t` 模擬 32 位元的 `size_t`：

```c
#include <stdint.h>
#include <stdio.h>

/* 模擬 32 位元平台的 size_t：所有大小運算都截成 32 位元 */
typedef uint32_t size32;

int main(void)
{
    int ele_cnt = (1 << 20) + 1;
    size32 ele_size = 1 << 12;
    size32 bytes = (size32) ele_cnt * ele_size;      /* 回繞 */
    uint64_t want = (uint64_t) ele_cnt * ele_size;   /* 數學上的值 */
    printf("ele_cnt * ele_size: 32-bit = %u, true = %llu\n", bytes, (unsigned long long) want);
    printf("malloc 拿到 %u bytes，迴圈卻要寫 %llu bytes（%.0f 倍）\n",
           bytes, (unsigned long long) want, (double) want / bytes);

    size32 safe;
    int ovf = __builtin_mul_overflow((size32) ele_cnt, ele_size, &safe);
    printf("__builtin_mul_overflow: %d\n", ovf);
    return 0;
}
```

```
ele_cnt * ele_size: 32-bit = 4096, true = 4294971392
malloc 拿到 4096 bytes，迴圈卻要寫 4294971392 bytes（1048577 倍）
__builtin_mul_overflow: 1
```

64 位元平台在這組數值不會溢位，但更大的輸入同樣會。C23 的修正版用 `<stdckdint.h>`（以下在 Docker `gcc:14`、`-std=c23` 執行）：

```c
#include <stdckdint.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void *copy_elements(void *ele_src[], int ele_cnt, size_t ele_size)
{
    size_t bytes;
    if (ele_cnt < 0 || ckd_mul(&bytes, ele_cnt, ele_size))
        return NULL;
    unsigned char *result = malloc(bytes);
    if (!result)
        return NULL;
    for (int i = 0; i < ele_cnt; i++)
        memcpy(result + (size_t) i * ele_size, ele_src[i], ele_size);
    return result;
}

int main(void)
{
    int cnt = (1 << 20) + 1;
    size_t size = 1 << 12;
    uint32_t r32;
    bool ovf = ckd_mul(&r32, cnt, size);

    printf("sizeof(size_t) = %zu, cnt * size = %zu\n", sizeof(size_t), cnt * size);
    printf("ckd_mul into uint32_t: overflow = %d, r32 = %u\n", ovf, (unsigned) r32);
    printf("copy_elements(..., -1, 8) = %p\n", copy_elements(NULL, -1, 8));
    printf("copy_elements(..., 2, SIZE_MAX) = %p\n", copy_elements(NULL, 2, SIZE_MAX));

    int sum;
    bool o = ckd_add(&sum, 2147483647, 1);
    printf("ckd_add(&sum, INT_MAX, 1) = %d, sum = %d\n", o, sum);
    return 0;
}
```

```
sizeof(size_t) = 8, cnt * size = 4294971392
ckd_mul into uint32_t: overflow = 1, r32 = 4096
copy_elements(..., -1, 8) = (nil)
copy_elements(..., 2, SIZE_MAX) = (nil)
ckd_add(&sum, INT_MAX, 1) = 1, sum = -2147483648
```

`ckd_mul(&r, a, b)` 的語意（C23 7.20）：**以無限精度計算數學值**，再判斷能否放進 `*r` 的型別；放不下回傳 `true`，並把「依該寬度回繞的值」寫入 `*r`。注意兩個細節：`ele_cnt < 0` 要另外擋（`ckd_mul` 會把 −1 × 8 = −8 判為放不進 `size_t` 而回傳 true，但明確檢查讓意圖清楚）；溢位時 `*r` 仍被寫入回繞值，**不要在回傳 true 之後使用它**。

### 截斷：寬值存入窄型別

**Linux SACK Panic（CVE-2019-11477）**，由 Netflix 的 Jonathan Looney 發現。`struct tcp_skb_cb` 以 `u16 tcp_gso_segs` 記錄一個 skb 含幾個 TCP 區段，`tcp_skb_pcount_add(skb, int segs)` 以 `+=` 累加後寫回 16 位元欄位。對端把 MSS 宣告為 Linux 接受的最小值 48，扣掉 40 位元組的 TCP 選項，每個區段只剩 8 位元組資料；SACK 處理合併重送佇列中的 skb，17 個 32 KiB 分段可容納 17 × 32768 / 8 = 69632 個區段，超過 65535：

```c
#include <stdint.h>
#include <stdio.h>

/* 模擬 struct tcp_skb_cb 的 u16 tcp_gso_segs 欄位 */
struct skb_cb { uint16_t gso_segs; };

static void pcount_add(struct skb_cb *cb, int segs) { cb->gso_segs += segs; }

int main(void)
{
    struct skb_cb cb = {0};
    int mss_payload = 48 - 40;               /* MSS 48 扣掉 40 位元組 TCP 選項 */
    int per_frag = 32768 / mss_payload;      /* 一個 32 KiB 分段含幾個區段 */
    for (int frag = 0; frag < 17; frag++)
        pcount_add(&cb, per_frag);
    printf("true segments = %d, stored in u16 = %u\n", 17 * per_frag, cb.gso_segs);
    printf("BUG_ON(pcount(skb) < pcount)? %s\n", cb.gso_segs < 17 * per_frag ? "yes -> panic" : "no");
    return 0;
}
```

```
true segments = 69632, stored in u16 = 4096
BUG_ON(pcount(skb) < pcount)? yes -> panic
```

69632 mod 65536 = 4096。核心的 `tcp_shifted_skb()` 有 `BUG_ON(tcp_skb_pcount(skb) < pcount)`，截斷後的小計數讓斷言成立，核心 panic——一個遠端封包就能讓機器當機。Eric Dumazet 的修正（commit `3b4929f65b0d`，〈tcp: limit payload size of sacked skbs〉）在合併前檢查 `tcp_skb_pcount(to) + pcount > 65535`，納入 Linux v5.2 並回移到 4.4.182、4.9.182、4.14.127、4.19.52、5.1.11 等穩定版。Netflix 的公告 NFLX-2019-001 指出 v2.6.29 起受影響。

注意這裡的 `cb->gso_segs += segs`：右邊先提升成 `int` 算出 69632 以內的正確值，**寫回 `u16` 時才截斷**，而且截斷到無號型別是有定義的取模，`-Wall` 不會警告。這類錯誤歸 CWE-197（Numeric Truncation Error）。

**MySQL/MariaDB 認證繞過（CVE-2012-2122）**。`sql/password.c` 的 `check_scramble` 寫成：

```c
return memcmp(hash_stage2, hash_stage2_reassured, SHA1_HASH_SIZE);
```

回傳型別 `my_bool` 是 `typedef char my_bool`。C11 7.24.4.1 §3 只規定 `memcmp` 回傳值的**正負號**，SSE 最佳化的 glibc `memcmp` 可以回傳 −128..127 以外的值；低 8 位元恰為 0 的非零值截成 `char` 後就成為「相等」。MariaDB 的 Sergei Golubchik 指出，由於協定每次使用隨機 scramble，以任意密碼連線成功的機率約 1/256，重試約 300 次不到一秒。原文用一個合乎標準、回傳值不限於 8 位元的 `word_memcmp` 重現：

```c
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef char my_bool; /* MySQL 5.5 的 my_global.h */

/* 合乎標準的 memcmp：只保證正負號，傳回值的大小不受限制；
 * 為求簡短，n 須為 4 的倍數 (此處為 20 位元組的 SHA-1 摘要) */
static int word_memcmp(const unsigned char *a, const unsigned char *b, size_t n)
{
    for (size_t i = 0; i < n; i += 4) {
        uint32_t u = (uint32_t) a[i] << 24 | a[i + 1] << 16 | a[i + 2] << 8 | a[i + 3];
        uint32_t v = (uint32_t) b[i] << 24 | b[i + 1] << 16 | b[i + 2] << 8 | b[i + 3];
        if (u != v)
            return u > v ? (int) ((u - v) >> 2) + 1 : -(int) ((v - u) >> 2) - 1;
    }
    return 0;
}

static my_bool check_bad(const unsigned char *a, const unsigned char *b)
{
    return word_memcmp(a, b, 20); /* 非零的 int 被截成 char */
}

static my_bool check_good(const unsigned char *a, const unsigned char *b)
{
    return word_memcmp(a, b, 20) != 0;
}

int main(void)
{
    unsigned char expect[20], token[20];
    long bad = 0, good = 0, trials = 1000000;
    srand(1);
    for (long t = 0; t < trials; t++) {
        for (int i = 0; i < 20; i++) {
            expect[i] = (unsigned char) rand();
            token[i] = (unsigned char) rand(); /* 錯誤的密碼 */
        }
        bad += check_bad(token, expect) == 0;
        good += check_good(token, expect) == 0;
    }
    printf("accepted: bad %ld/%ld (1/%.0f), good %ld\n", bad, trials,
           (double) trials / bad, good);
    return 0;
}
```

```
$ gcc -O2 -Wall -Wextra mysql.c -o mysql && ./mysql
accepted: bad 3935/1000000 (1/254), good 0
```

`-Wall -Wextra` 完全沒警告。要加 `-Wconversion` 才看得到：

```
$ gcc -Wall -Wextra -Wconversion -c mysql.c
mysql.c: In function ‘check_bad’:
mysql.c:22:12: warning: conversion from ‘int’ to ‘my_bool’ {aka ‘char’} may change value [-Wconversion]
   22 |     return word_memcmp(a, b, 20); /* 非零的 int 被截成 char */
      |            ^~~~~~~~~~~~~~~~~~~~~
```

修正版 MySQL 5.5.24 改成 `return test(memcmp(...))`，`test(a)` 展開為 `((a) ? 1 : 0)`。更好的做法是**真值用 `bool` 承載**：C11 6.3.1.2 §1 規定任何純量轉成 `_Bool` 時「比較等於 0 得 0，否則得 1」，轉成 `bool` 不會截斷。

### 正負號：`char` 與 `-INT_MIN`

**`char` 的符號是實作定義**（C11 6.2.5 §15）：x86-64 Linux 上是有號，arm64 Linux 上是無號。UTF-8 的 `é`（`0xC3 0xA9`）在 x86-64 上讀成 −61 與 −87；拿它當陣列索引，就讀到陣列開頭之前。`<ctype.h>` 的函式也要求引數「可表示為 `unsigned char` 或等於 EOF」（C11 7.4 §1），否則是 UB。CERT C 的 STR37-C 要求先轉 `(unsigned char)`。這類「窄有號值擴寬時符號擴充」歸 CWE-194。

**取負數也會溢位**。libsndfile 1.0.28 的 `i2ulaw_array()` 對負的樣本計算 `- ptr[count]`，`ptr[count]` 為 `INT_MIN` 時有號溢位，得到的負數當成查表索引，讀到緩衝區之外（CVE-2018-19661）：

```c
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    int sample = argc > 1 ? atoi(argv[1]) : INT_MIN;
    if (sample < 0) {
        int idx = -sample;                 /* INT_MIN 時溢位 */
        printf("idx = %d\n", idx);
    }
    return 0;
}
```

```
$ gcc -O0 -fsanitize=undefined negmin.c -o nm && ./nm
idx = -2147483648
negmin.c:9:13: runtime error: negation of -2147483648 cannot be represented in type 'int'; cast to an unsigned type to negate this value to itself
```

「確定是負數，取負就變正數」這個直覺在 ℤ 成立，在 32 位元二補數不成立——[Ch 21](./21-number-systems.md) 說過 `INT_MIN` 的反元素是自己。修正（commit `8ddc442d`，〈a/ulaw: fix multiple buffer overflows〉）的說明寫明「INT_MIN is a special value since - INT_MIN cannot be represented as int」，改以 `INT_MAX` 代替。UBSan 的訊息還順便教你另一種寫法：轉成無號再取負。

### 參考計數的回繞：從溢位到 use-after-free

參考計數只該遞增與遞減；一旦回繞，原本合法的遞減會讓它提早歸零，物件被釋放，仍持有參考的程式碼就在使用已釋放的記憶體。CWE-190 的整數溢位在這裡直接變成 CWE-416 的 use-after-free。

**CVE-2016-0728**（Linux keyring）。Perception Point 的分析指出，`keyctl(KEYCTL_JOIN_SESSION_KEYRING, name)` 要求加入行程已在使用的 session keyring 時，`join_session_keyring()` 的錯誤路徑漏掉一次 `key_put()`，每次呼叫讓 `struct key` 的 `usage` 多 1。`usage` 是 `atomic_t`，在 32 與 64 位元架構上都是 32 位元 `int`；呼叫約 2³² 次後計數繞回初始值附近，之後一次正常的 `key_put()` 就把它減到 0 而觸發回收。錯誤自 v3.8 存在，攻擊程式在 Core i7-5500 上約 30 分鐘取得 root。修正 commit `23567fd052a9`（〈KEYS: Fix keyring ref leak in join_session_keyring()〉）納入 v4.5 與 4.4.1。

漏掉的 `key_put()` 只是遺漏，讓它成為權限提升的是**計數器會回繞**。Peter Zijlstra 在 v4.11 加入 `refcount_t`，說明寫：「It provides saturation semantics such that overflow becomes impossible and thereby 'spurious' use-after-free is avoided.」下面是一個**教學用的簡化模型**（非原子、不是核心實作），對照兩種計數器在漏掉 2³² 次 put 之後的狀態：

```c
#include <limits.h>
#include <stdint.h>
#include <stdio.h>

/* 教學用簡化版：模仿 refcount_t 的飽和語意（非原子、非核心實作） */
#define SATURATED (INT_MIN / 2)

typedef struct { int32_t v; } refcount;

static void ref_inc(refcount *r)
{
    uint32_t old = (uint32_t) r->v;
    uint32_t nv = old + 1;                       /* 以無號運算避免有號溢位 */
    if ((int32_t) old <= 0)                      /* 已飽和或發生過錯誤：停在飽和值 */
        nv = (uint32_t) SATURATED;
    r->v = (int32_t) nv;
}

static int ref_dec_and_test(refcount *r)
{
    if (r->v == SATURATED || r->v <= 0) {        /* 飽和後永不歸零：寧可洩漏 */
        r->v = SATURATED;
        return 0;
    }
    return --r->v == 0;
}

int main(void)
{
    /* atomic_t 式的 32 位元計數：漏掉 2^32 次 put 之後繞回原值 */
    uint32_t plain = 1;
    for (uint64_t i = 0; i < (1ull << 32); i++)
        plain++;                                 /* 模擬每次 keyctl 多 +1 */
    printf("plain counter after 2^32 leaks: %u  (一次合法的 put 就歸零 -> UAF)\n", plain);

    refcount r = {1};
    for (uint64_t i = 0; i < (1ull << 32); i++)
        ref_inc(&r);
    printf("saturating counter: %d (0x%08x), dec_and_test -> %d\n",
           r.v, (uint32_t) r.v, ref_dec_and_test(&r));
    return 0;
}
```

```
plain counter after 2^32 leaks: 1  (一次合法的 put 就歸零 -> UAF)
saturating counter: -1073741824 (0xc0000000), dec_and_test -> 0
```

真實的 `REFCOUNT_SATURATED` 定義在 `include/linux/refcount.h`，也是 `(INT_MIN / 2)`，即 `0xc000_0000`。為什麼選這個值？它與 0 及 `INT_MAX` 的距離相近：實作為了不在每次遞增都用 `cmpxchg` 迴圈，採「先遞增再檢查」，多執行緒同時操作時計數可能暫時偏離飽和值；核心註解以 `PID_MAX_LIMIT`（0x400000）估算，從飽和值到回繞為 0 的距離是 (2³² − 0xc0000000) / 0x400000 = 256 倍的 task 上限，計數離不開飽和區。**飽和的代價是物件永遠不會被釋放**——以記憶體洩漏換取不發生 use-after-free。這正是 [Ch 21](./21-number-systems.md)「進階」說的「放棄反元素、換取單調」。

### 配置大小的回繞：BadAlloc 與 Stagefright

2021 年 Microsoft Section 52 團隊以 **BadAlloc** 之名回報超過 25 個 CVE，成因都是 RTOS、嵌入式 SDK 與 C 程式庫的 `malloc`、`calloc`、`realloc`、`memalign` 未檢查大小運算；CISA ICSA-21-119-04 列出的受影響產品包括 Amazon FreeRTOS 10.4.1、Zephyr 2.5 之前、newlib 4.0.0 之前、uClibc-NG 1.0.36 之前、VxWorks、RIOT、NuttX、Mbed OS。

以 FreeRTOS 的 `xQueueGenericCreate()`（CVE-2021-31571）為例：原本以 `configASSERT` 檢查了 `uxQueueLength * uxItemSize` 的乘法，**卻沒檢查其後加上 `sizeof(Queue_t)` 的加法**；修正補上加法檢查。更糟的是，`FreeRTOS.h` 在使用者未定義 `configASSERT` 時把它展開為空——兩道檢查都可能根本不存在。教訓：**大小運算的每一步都要檢查**，而且檢查不能依賴可被關掉的斷言。

Android **Stagefright**（CVE-2015-1538）出自 C++ 的 libstagefright：解析 MP4 的 `stsc` atom 時，`SampleTable::setSampleToChunkParams` 以檔案提供的條目數計算配置大小而未檢查乘法溢位，一個媒體檔即可遠端執行任意程式碼。Joshua J. Drake 的修正在配置前加入：

```cpp
if (SIZE_MAX / sizeof(SampleToChunkEntry) <= mNumSampleToChunkOffsets)
```

這是「除法反檢查」的標準寫法。C23 也把 `calloc` 的檢查寫進規範（7.24.3.2 §3）：乘積會回繞時必須回傳 null；C11 沒有這句話。

## 底層機制：編譯器怎麼利用 UB 刪除檢查

這一節是本章的靈魂。為什麼「用溢位後的結果判斷是否溢位」在有號整數上會失效？

### 推論鏈

C11 6.5 §5 說有號溢位是 UB。UB 的意思是「標準對這個程式不做任何要求」，於是編譯器採用的推論是：

```
  程式沒有 UB（否則怎樣都對）
      ⇒ x + 1 不會溢位
      ⇒ x + 1 的數學值就是 x + 1（在 ℤ 中）
      ⇒ 在 ℤ 中 x + 1 < x 恆為假
      ⇒ 把整個運算式換成常數 0
```

每一步在 ℤ 中都對。問題在於程式設計者寫這行時，腦中想的是 ℤ/2³²ℤ。同樣地，指標算術的界線在 C11 6.5.6 §8：結果超出同一個陣列物件（含尾後一格）就是 UB，所以 `buf + len < buf` 也可以被當成恆假。CERT 在 2008 年 4 月的 VU#162289〈C compilers may silently discard some wraparound checks〉就是以 `if (buf + len < buf)` 為例；CWE-733（Compiler Optimization Removal or Modification of Security-critical Code）收錄這一類。

### 實測：四種選項、兩個編譯器

```c
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static int add_wraps(int x) { return x + 1 < x; }         /* 依賴有號溢位 */
static int add_wraps_ok(int x) { return x == INT_MAX; }   /* 先比較再運算 */
static int ptr_wraps(const char *p, size_t n) { return p + n < p; } /* 依賴指標溢位 */
static int ptr_wraps_ok(const char *p, size_t n) { return n > UINTPTR_MAX - (uintptr_t) p; }

int main(int argc, char **argv)
{
    int x = argc > 1 ? atoi(argv[1]) : INT_MAX;
    size_t n = argc > 2 ? strtoull(argv[2], NULL, 0) : SIZE_MAX - 15;
    char buf[16];
    printf("x + 1 < x: %d, x == INT_MAX: %d\n", add_wraps(x), add_wraps_ok(x));
    printf("buf + n < buf: %d, uintptr_t check: %d\n", ptr_wraps(buf, n), ptr_wraps_ok(buf, n));
    return 0;
}
```

x 為 `INT_MAX`、n 為 `SIZE_MAX − 15` 時（`add_wraps_ok` 與 `ptr_wraps_ok` 在所有組態都輸出 1）：

| 編譯器 | `-O0` | `-O2` | `-O2 -fwrapv` | `-O2 -fno-strict-overflow` |
|---|---|---|---|---|
| gcc 11.4：`x + 1 < x` | **0** | **0** | 1 | 1 |
| clang 14：`x + 1 < x` | 1 | **0** | 1 | 1 |
| gcc 11.4 / clang 14：`buf + n < buf` | 1 | 1 | 1 | 1 |

最令人意外的是第一列：**gcc 在 `-O0` 就把檢查刪掉了**。看組合語言：

```
$ gcc -O0 -S fold.c        # add_wraps 的部分
add_wraps:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	%edi, -4(%rbp)
	movl	$0, %eax          ← 直接回傳 0，沒有任何加法或比較
	popq	%rbp
	ret
```

這是 gcc 前端的常數摺疊（folding），在最佳化階段之前就發生，所以「用 `-O0` 編譯就安全」是錯的。原文在 GCC 13/14、Clang 18/20 觀察到一樣的模式。

### 更多被「合法改寫」的例子

```c
int add_wraps(int x) { return x + 1 < x; }
int add_wraps_u(unsigned x) { return x + 1 < x; }
int twice_half(int x) { return x * 2 / 2; }
unsigned twice_half_u(unsigned x) { return x * 2 / 2; }
int ptr_wraps(const char *p, unsigned long n) { return p + n < p; }
```

`gcc -O2`（clang 14 只差在指令順序）：

```
add_wraps:              add_wraps_u:              twice_half:        twice_half_u:
  xorl %eax, %eax         xorl  %eax, %eax          movl %edi, %eax    movl %edi, %eax
  ret                     cmpl  $-1, %edi           ret                andl $2147483647, %eax
                          sete  %al                                    ret
                          ret
ptr_wraps:
  shrq  $63, %rsi
  movq  %rsi, %rax
  ret
```

- `add_wraps_u`：無號版本被正確化簡成「x == UINT_MAX」——**無號的檢查是對的**，因為回繞有定義。
- `twice_half`：Chris Lattner 在 LLVM 部落格舉的例子，「乘法不可能溢位，所以 `X*2/2` 就是 `X`」。無號版本則必須丟掉最高位元（`& 0x7FFFFFFF`），因為模 2³² 的乘法真的會丟掉它。
- `ptr_wraps`：被編成 `n >> 63`，只檢查 n 的最高位元而**與 p 無關**。我們的測試值 `SIZE_MAX − 15` 碰巧最高位元是 1，所以表格那一列看起來「正確」——這是巧合，不是檢查。原文記錄 Clang 20 更進一步直接化簡為 0 並警告「pointer comparison always evaluates to false」。

加上 `-fwrapv` 之後，gcc 忠實地執行回繞：

```
add_wraps:                        twice_half:
  xorl  %eax, %eax                  leal  (%rdi,%rdi), %eax
  cmpl  $2147483647, %edi           sarl  %eax
  sete  %al                         ret
  ret
```

### UB 的另一面：它也讓迴圈變快

同一條規則不只刪檢查，也讓迴圈得以最佳化。Lattner 寫道：「This particularly affects 64-bit platforms since so much code uses "int" as induction variables.」

```c
void scale_i(float *restrict d, const float *restrict s, int n, int k, float f)
{
    for (int i = 0; i < n; i++)
        d[i] = f * s[i + k];
}

void scale_u(float *restrict d, const float *restrict s, unsigned n, unsigned k, float f)
{
    for (unsigned i = 0; i < n; i++)
        d[i] = f * s[i + k];
}
```

`int` 的 `i + k` 不會溢位，所以 `s + k + i` 在 64 位元位址上是等差數列，`s + k` 可以移出迴圈；`unsigned` 的 `i + k` 可能在 2³² 處回繞，下一個位址不一定緊接在後。gcc 11 `-O2` 的迴圈主體：

```
scale_i 的迴圈：                      scale_u 的迴圈：
.L3:                                  .L8:
  movss (%rcx,%rax,4), %xmm1            movl  %ecx, %eax      ← 每輪重算 32 位元索引
  mulss %xmm0, %xmm1                    addl  $1, %ecx
  movss %xmm1, (%rdi,%rax,4)            addq  $4, %rdi
  addq  $1, %rax                        movss (%rsi,%rax,4), %xmm1
  cmpq  %rax, %rdx                      mulss %xmm0, %xmm1
  jne   .L3                             movss %xmm1, -4(%rdi)
                                        cmpl  %ecx, %edx
                                        jne   .L8
```

差別在 `-O3` 擴大：數 `mulps`（4 個 float 一起乘的向量指令），`scale_i` 有 2 道、`scale_u` 是 0 道——**gcc 11 只向量化了 `int` 版本**。原文在 Zen+ 上量到 GCC 14 `-O3` 時 `int` 版每元素 0.07 ns、`unsigned` 與 `-fwrapv` 版 0.36 ns，差 5 倍；Clang 則三者都向量化，但無號版本會先在執行時期檢查 `k + n − 1` 是否回繞。

所以 C11 6.5 §5 有兩面：同一條規則讓 `x + 1 < x` 被刪除，也讓迴圈得以向量化。**需要回繞的程式用無號型別；迴圈索引用 `int`、`long` 或 `size_t`，避免「32 位元無號索引加位移量」的組合**。

### Linux 核心的選擇：`-fwrapv` → `-fno-strict-overflow`

Linus Torvalds 在 v2.6.29 以〈Add '-fwrapv' to gcc CFLAGS〉說明：「This makes sure that gcc doesn't try to optimize away wrapping arithmetic, which the kernel occasionally uses for overflow testing, ie things like `if (ptr + offset < ptr)`」。GCC 4.1 的 `-fwrapv` 有缺陷，v2.6.31 改用 `-fno-strict-overflow`。今天的頂層 `Makefile` 仍是：

```make
# disable invalid "can't wrap" optimizations for signed / pointers
KBUILD_CFLAGS += -fno-strict-overflow
```

核心選擇了「安全」那一面，代價是部分迴圈最佳化。注意這些選項只讓檢查**保留下來**，回繞本身仍是錯誤的結果；可攜的程式應該在運算**之前**比較。

## 防禦

### 寫法一：先比較，再運算

不依賴任何編譯器特性的可攜寫法，核心原則是**在不會溢位的範圍內比較**：

| 要算的 | 先檢查（成立就是會溢位） | 備註 |
|---|---|---|
| 有號 `a + b` | `b > 0 ? a > INT_MAX - b : a < INT_MIN - b` | 兩個減法都不會溢位 |
| 無號 `a + b` | `a > UINT_MAX - b`，或事後 `a + b < a` | 無號可以事後檢查（回繞有定義） |
| `n * size`（size_t） | `size != 0 && n > SIZE_MAX / size` | `size` 為 0 時除法本身是 UB |
| `-x`（有號） | `x == INT_MIN` | |
| `a / b`（有號） | `b == 0 \|\| (a == INT_MIN && b == -1)` | |
| 中點 `(lo + hi) / 2` | 改寫為 `lo + (hi - lo) / 2`（已知 lo ≤ hi） | Java `binarySearch` 的 2006 年 bug |

最後一列是 Joshua Bloch 2006 年的〈Nearly All Binary Searches and Mergesorts are Broken〉：Java 的 `Arrays.binarySearch` 以 `(low + high) / 2` 計算中點，元素超過 2³⁰ 個時溢位。文中引用 Jon Bentley：第一個二分搜尋在 1946 年發表，對所有 n 都正確的版本到 1962 年才出現。

```c
int low = 1500000000, high = 2000000000;          /* 陣列夠大時的合法索引 */
unsigned bad_mid = ((unsigned) low + (unsigned) high) / 2;  /* 以無號模擬回繞 */
int wrapped = (int) ((unsigned) low + (unsigned) high);
printf("(low + high) 回繞成 %d，除以 2 得 %d\n", wrapped, wrapped / 2);
printf("low + (high - low) / 2 = %d\n", low + (high - low) / 2);
printf("無號相加再除以 2       = %u\n", bad_mid);
uint32_t x = UINT32_MAX, y = UINT32_MAX - 2;
printf("average(x, y) = %u, (x + y) / 2 = %u\n", (x & y) + ((x ^ y) >> 1), (x + y) / 2);
```

```
(low + high) 回繞成 -794967296，除以 2 得 -397483648
low + (high - low) / 2 = 1750000000
無號相加再除以 2       = 1750000000
average(x, y) = 4294967294, (x + y) / 2 = 2147483646
```

（為了不在示範程式裡寫出真正的有號溢位 UB，回繞以無號運算模擬。）最後一行的 `(x & y) + ((x ^ y) >> 1)` 來自 [Ch 21](./21-number-systems.md) 的 `x + y = (x ^ y) + 2(x & y)`：兩邊除以 2，右邊永遠不會超過 2^w − 1。

### 寫法二：`__builtin_*_overflow`（GCC 5+、Clang 3.8+）

```c
#include <limits.h>
#include <stdint.h>
#include <stdio.h>

int main(void)
{
    int r;
    int o;
    o = __builtin_add_overflow(INT_MAX, 1, &r);
    printf("add(INT_MAX, 1)          overflow=%d r=%d\n", o, r);
    o = __builtin_mul_overflow(65536, 65536, &r);
    printf("mul(65536, 65536)        overflow=%d r=%d\n", o, r);

    uint8_t u8;
    o = __builtin_add_overflow(200, 100, &u8);
    printf("add(200, 100) -> uint8_t overflow=%d r=%u\n", o, u8);

    unsigned u;
    o = __builtin_sub_overflow(0, 1, &u);
    printf("sub(0, 1) -> unsigned    overflow=%d r=%u\n", o, u);

    long l;
    o = __builtin_mul_overflow(65536, 65536, &l);
    printf("mul(65536, 65536) -> long overflow=%d r=%ld\n", o, l);

    size_t bytes, n = (size_t) 1 << 61, size = 16;
    if (__builtin_mul_overflow(n, size, &bytes))
        printf("n * size overflows size_t; refuse to malloc\n");
    return 0;
}
```

```
add(INT_MAX, 1)          overflow=1 r=-2147483648
mul(65536, 65536)        overflow=1 r=0
add(200, 100) -> uint8_t overflow=1 r=44
sub(0, 1) -> unsigned    overflow=1 r=4294967295
mul(65536, 65536) -> long overflow=0 r=4294967296
n * size overflows size_t; refuse to malloc
```

重點：判斷依據是**結果指標所指的型別**，與運算元型別無關。同樣是 65536 × 65536，存進 `int` 溢位、存進 `long` 不溢位；0 − 1 存進 `unsigned` 也算溢位（數學值 −1 放不進去）。代價幾乎為零：

```
checked_add:                    checked_mul:     (size_t × size_t)
  xorl  %eax, %eax                movq  %rdi, %rax
  addl  %esi, %edi                movq  %rdx, %r8
  movl  %edi, (%rdx)              mulq  %rsi
  seto  %al          ← OF 旗標    movq  %rax, (%r8)
  ret                             seto  %al
                                  movzbl %al, %eax
                                  ret
```

加法就是一道 `add` 加一道 `seto`（取溢位旗標），乘法用 `mulq` 的溢位旗標。手寫的「先比較再運算」反而更長。

### 寫法三：C23 `<stdckdint.h>`

C23 7.20 把上面的內建函式標準化成三個巨集 `ckd_add`、`ckd_sub`、`ckd_mul`，引數順序是「結果指標在前」。前面 XDR 修正版已經示範過；在 GCC 14 上它就是內建函式的包裝，產生的程式碼一樣（`-std=c23 -O2`）：

```
add_ck:
	addl	%edx, %esi
	movl	%esi, (%rdi)
	seto	%al
	ret
```

gcc 11 / glibc 2.35 沒有 `<stdckdint.h>`，舊環境可以退回 `__builtin_*_overflow`，兩者語意相同。

### 編譯器旗標與 sanitizer

**`-ftrapv`**：讓有號溢位在執行時期中止。我們以 `trap.c`（`int y = x + 1;`，x 預設 `INT_MAX`）實測：

```
gcc -O0 -ftrapv:   exit=134   (SIGABRT)
gcc -O2 -ftrapv:   exit=134
clang -O0 -ftrapv: exit=132   (SIGILL)
clang -O2 -ftrapv: exit=132
```

`-S` 看得出兩者的做法不同：gcc 把加法換成呼叫 libgcc 的 `__addvsi3`，它偵測到溢位就呼叫 `abort()`；clang 則就地 `incl` 之後檢查溢位旗標（`seto` 或 `jo`），溢位時執行一道 `ud1l` 非法指令。

這次四個組態都中止了，但不要以為 `-ftrapv` 可靠：原文記錄 GCC 13/14 的 `-O2` 會**先把 `x + 1 < x` 化簡掉**，沒有任何加法可供檢查，`-ftrapv` 形同無效。它檢查的是「還留在程式裡的加法」，不是你寫在原始碼的每一個加法。

**UBSan**（`-fsanitize=signed-integer-overflow`）是開發階段最好用的工具：

```
$ gcc -O2 -fsanitize=signed-integer-overflow trap.c -o t && ./t
trap.c:8:9: runtime error: signed integer overflow: 2147483647 + 1 cannot be represented in type 'int'
x + 1 = -2147483648
$ clang -O2 -fsanitize=signed-integer-overflow trap.c -o t && ./t
trap.c:8:15: runtime error: signed integer overflow: 2147483647 + 1 cannot be represented in type 'int'
SUMMARY: UndefinedBehaviorSanitizer: undefined-behavior trap.c:8:15 in
x + 1 = -2147483648
```

預設只印訊息、繼續執行；加 `-fsanitize-undefined-trap-on-error` 改成直接 `ud2`（exit 132），適合 fuzzing。對前面的 `wrapchk.c`，`-fsanitize=signed-integer-overflow,pointer-overflow` 下兩個編譯器都報有號溢位，**只有 clang 報指標溢位**：

```
wrapchk.c:8:58: runtime error: addition of unsigned offset to 0x7ffec8d880b0 overflowed to 0x7ffec8d880a0
```

（位址每次執行不同。）而且注意：開了 UBSan 之後 gcc 的 `x + 1 < x` 變成輸出 1——sanitizer 改變了程式的最佳化方式，**不要拿 sanitizer 版本的輸出推論正式版本的行為**。

**無號回繞與截斷**不是 UB，標準的 UBSan 不管；clang 提供兩個額外的檢查（gcc 11 不支援）：

```c
#include <stdio.h>
#include <stdlib.h>

typedef char my_bool;

int main(int argc, char **argv)
{
    unsigned n = argc > 1 ? (unsigned) strtoul(argv[1], NULL, 0) : 0x40000001u;
    unsigned bytes = n * 4u;               /* 有定義的回繞，但多半是 bug */
    int cmp = argc > 2 ? atoi(argv[2]) : 256;
    my_bool same = cmp;                   /* 截斷：256 變成 0 */
    printf("bytes = %u, same = %d\n", bytes, same);
    return 0;
}
```

```
$ clang -O0 -fsanitize=unsigned-integer-overflow,implicit-conversion unsan.c -o un && ./un
unsan.c:9:24: runtime error: unsigned integer overflow: 1073741825 * 4 cannot be represented in type 'unsigned int'
unsan.c:11:20: runtime error: implicit conversion from type 'int' of value 256 (32-bit, signed) to type 'my_bool' (aka 'char') changed the value to 0 (8-bit, signed)
bytes = 4, same = 0
```

這兩個檢查會在「刻意回繞」的程式碼（雜湊函式、亂數產生器）上誤報，適合有針對性地開。

**`-fwrapv` 與 UBSan 互相抵觸**。`-fwrapv` 讓有號回繞成為「有定義」，UBSan 就不再把它當錯誤：

```
$ gcc -O2 -fwrapv -fsanitize=signed-integer-overflow trap.c -o t && ./t
x + 1 = -2147483648
$ clang -O2 -fwrapv -fsanitize=signed-integer-overflow trap.c -o t && ./t
x + 1 = -2147483648
```

兩個都沉默了。原文記錄 Clang 20 改為照常回報——新版 Clang 上「有定義的回繞」與「溢位偵測」可以並存。這個抵觸直接影響了 Linux：v5.12 以〈ubsan: remove overflow checks〉移除有號溢位的 sanitizer，理由正是 `-fwrapv` 讓它形同無效；Kees Cook 在 v6.9 重新加入 `CONFIG_UBSAN_SIGNED_WRAP`，並以 `wrapping_add()` 等函式標示**刻意的**回繞。

### 防禦總表

| 弱點 | CWE | CERT C | 編譯時期 | 執行時期 | 程式寫法 |
|---|---|---|---|---|---|
| 無號運算回繞 | 190、680 | INT30-C | 無（有定義） | clang `-fsanitize=unsigned-integer-overflow` | `ckd_*`、`n > SIZE_MAX / size` |
| 有號運算溢位 | 190、733 | INT32-C | 無 | `-fsanitize=signed-integer-overflow`、`-ftrapv` | `ckd_*`、先比較再運算 |
| 截斷與正負號 | 194–197、681 | INT31-C、STR37-C | `-Wconversion`、`-Wsign-conversion`、`-Wsign-compare` | clang `-fsanitize=implicit-conversion` | 長度一律 `size_t`、真值用 `bool` |
| 參考計數 | 911 → 416 | — | — | — | 飽和計數（`refcount_t`） |
| 浮點特殊值 | 681 | FLP04-C、FLP34-C | 無 | `-fsanitize=float-cast-overflow` | 寫成「接受範圍內」、先 `isfinite` |

## 對比與取捨

| 方案 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| 先比較再運算 | 可攜、零依賴 | 容易寫錯（有號要分正負）、可讀性差 | 函式庫、需要支援舊編譯器 |
| `__builtin_*_overflow` | 一道 `seto`，混合型別也正確 | GCC/Clang 專屬 | 現行 Linux 程式碼（核心的 `check_add_overflow()` 就是它） |
| C23 `ckd_*` | 標準化、語意與內建函式相同 | 要 GCC 14 / Clang 18 以上 | 新專案 |
| 飽和運算 | 單調，大的不會繞成小的 | 失去反元素；可能洩漏 | 參考計數、配置大小（`size_mul()`） |
| `-fwrapv` / `-fno-strict-overflow` | 保住既有的手寫檢查 | 回繞仍是錯的結果；擋掉部分最佳化；舊版與 UBSan 衝突 | 大型既有程式碼（Linux 核心） |
| `-ftrapv` | 溢位即中止 | 只檢查還留在程式裡的運算；有效能成本 | 測試 |
| UBSan | 精確指出檔名行號 | 只在被執行到的路徑有效；改變最佳化 | 開發、CI、fuzzing |

## 踩雷集錦

1. **「我用 `-O0` 編譯，溢位檢查就不會被刪」**：gcc 11 在 `-O0` 就把 `x + 1 < x` 摺疊成 0（見上方組合語言）。唯一可靠的是不寫依賴 UB 的檢查。
2. **「x86 的 `add` 會繞回，所以 `int` 溢位會繞回」**：硬體會，C 不保證。一旦編譯器能推論，`x + 1 < x`、`x * 2 / 2`、迴圈的歸納變數都會依 ℤ 的規則改寫。
3. **「我改成 `size_t` 就擋住負數了」**：FreeBSD 修正版說明了相反的事——負的 `int` 傳進來會先變成 `SIZE_MAX`。有號的長度要在轉換**之前**檢查。
4. **「只檢查乘法就夠了」**：FreeRTOS 的 CVE-2021-31571 正是乘法有檢查、後面的加法沒檢查。`n * size + header` 的每一步都可能回繞。
5. **「`printf("%d %d", __builtin_add_overflow(a, b, &r), r)` 可以一次印出來」**：函式引數的求值順序未指定，`r` 可能在內建函式寫入之前就被讀走。我們第一次寫範例時就踩到，gcc 印出 `overflow=1 r=0`，並警告 `operation on 'r' may be undefined [-Wsequence-point]`。先把回傳值存起來再印。
6. **「`-Wall -Wextra` 會抓到截斷」**：MySQL 的 `my_bool` 截斷在 `-Wall -Wextra` 下完全安靜。要 `-Wconversion`（clang 另有 `-fsanitize=implicit-conversion`）。
7. **「拒絕範圍外的值」就安全**：浮點版的同一個錯。`v < 0 || v > 255` 放行 NaN：

```
128   reject_bad 0, accept_good 1, lrint 128
300   reject_bad 1, accept_good 0, lrint 300
-1    reject_bad 1, accept_good 0, lrint -1
nan   reject_bad 0, accept_good 0, lrint -9223372036854775808
inf   reject_bad 1, accept_good 0, lrint -9223372036854775808
```

NaN 與任何數比較都是 false（C11 7.12.14），所以「拒絕越界」的寫法讓 NaN 通過；glibc 的 `lrint` 在 x86-64 是一道 `cvtsd2si`，超出範圍回傳 `LONG_MIN`，拿去當索引就越界。libsndfile 的 CVE-2017-14245/14246 正是這個組合。**把條件寫成「接受範圍內」，或先 `isfinite`**。NaN 的位元表示見 [Ch 25](./25-ieee754.md)。

## 進階：再往深一層

**Linux 核心的「回繞是錯還是對」之爭。** Kees Cook 2024 年的提案（[LWN 的報導](https://lwn.net/Articles/979747/)有記錄）主張：核心平均每年約 6 個重大的整數溢位缺陷，問題不在 UB，而在**有定義卻非預期的回繞**，所以應該讓無號回繞也能被偵測，刻意的回繞再用 `wrapping_add()` 標示。Linus Torvalds 反對一律視為錯誤：「unsigned arithmetic is well-defined as wrapping around, and it is so for a good reason」，主張只檢查用作大小或索引的值。後續 v6.15 把選項改名為 `CONFIG_UBSAN_INTEGER_WRAP` 並依型別逐步啟用，原文記錄目前它在 `lib/Kconfig.ubsan` 被標為 `depends on BROKEN`，並預告將由 Overflow Behavior Types 取代。這個爭論沒有共識，值得自己讀原始討論。

**`struct_size()` 與飽和的大小運算。** 核心的 `size_mul()`、`size_add()` 溢位時回傳 `SIZE_MAX`（飽和），`struct_size(p, member, n)` 用它們計算「結構 + 彈性陣列」的大小。`SIZE_MAX` 一定配置失敗，於是過大的請求在配置器被擋下，而不是繞回成小請求。這是「以單調換反元素」的工程實踐，[Ch 33](./33-tricks-pointers-and-performance.md) 會再看到。

**`-fsanitize=implicit-conversion` 的細分**：clang 把它分成 `implicit-integer-truncation`、`implicit-integer-sign-change` 等子項，可以只開截斷。對大型程式，先開截斷、再逐步開正負號，噪音比較小。

**形式化方法。** Frama-C 的 WP 外掛與 CBMC 可以證明一段 C 程式「在所有輸入下都不會有號溢位」，Wang 等人的 SOSP 2013 論文（見延伸閱讀）則用靜態分析找出「因 UB 而被刪除的檢查」。這些工具能補 sanitizer 只看執行路徑的不足。

## 動手練習

1. 把 `wrapchk.c` 加上第三個檢查 `static int add_wraps2(int x, int y) { return x + y < x; }`，在 gcc/clang 的 `-O0`、`-O2` 下各會被化簡成什麼？用 `-S` 看組合語言，寫下編譯器推論的每一步。
2. 寫一個 `bool safe_mul_size(size_t a, size_t b, size_t *out)`，**不用**內建函式，用 10⁷ 組隨機輸入（含 0、1、`SIZE_MAX`）與 `__builtin_mul_overflow` 比對。
3. 改 `sack.c`：讓 `pcount_add` 在寫回前檢查 `cb->gso_segs + segs > 65535`，模擬 Dumazet 的修正，確認 17 個分段時會拒絕合併。
4. 用 `-fsanitize=implicit-conversion` 跑 `mysql.c`（需要 clang），看 UBSan 報出哪個值被截成什麼；再把 `my_bool` 改成 `bool`，確認 `bad` 歸零。
5. 在 `scale_i`/`scale_u` 上用 `gcc -O3 -fopt-info-vec-missed` 看編譯器說它為什麼不向量化 `scale_u`。

## 本章重點整理

- C 的「溢位」有三種語意：無號回繞（有定義）、有號溢位（UB）、轉成窄有號型別（實作定義）。
- 溢位通常是第一張骨牌：乘法回繞 → 配置過小 → heap overflow；計數回繞 → 提早釋放 → use-after-free。
- 有號溢位是 UB，讓編譯器能依 ℤ 的規則刪掉 `x + 1 < x`（gcc 連 `-O0` 都刪），也讓 `int` 迴圈索引得以向量化。
- 正確的檢查要在不會溢位的範圍內比較，或用 `__builtin_*_overflow` / C23 `ckd_*`（一道 `seto`，幾乎免費）。
- `-fwrapv`/`-fno-strict-overflow` 保住舊檢查但不修正錯誤；`-ftrapv` 只檢查留下來的運算；UBSan 是開發期最有效的工具，但舊版與 `-fwrapv` 衝突。
- 截斷不會被 `-Wall -Wextra` 抓到；要 `-Wconversion`。真值用 `bool`，長度用 `size_t`，跨信任邊界的 `char` 先轉 `unsigned char`。

## 自我檢核

- [ ] 不看筆記，能說出 C 標準中哪三種「超出範圍」分別是有定義、實作定義、未定義嗎？
- [ ] 能畫出「乘法回繞 → heap overflow」的因果鏈，並指出在哪一步加檢查最有效嗎？
- [ ] 如果面試官問「為什麼 `if (x + 1 < x)` 會被編譯器刪掉，而 `if (u + 1 < u)` 不會」，你能用 ℤ 與 ℤ/2^wℤ 的語言回答嗎？
- [ ] 能解釋為什麼「有號溢位是 UB」對效能有好處，舉出一個具體的迴圈例子嗎？
- [ ] 能說出 `-fwrapv`、`-fno-strict-overflow`、`-ftrapv`、`-fsanitize=signed-integer-overflow` 各自做什麼，以及哪兩個會互相抵觸嗎？
- [ ] 能解釋 `refcount_t` 為什麼選擇飽和而不是回繞，以及它付出的代價嗎？
- [ ] 回想你寫過的程式裡任何「長度 × 個數」的配置，它有檢查乘法和後續的加法嗎？

## 延伸閱讀

### 論文

- **[Towards Optimization-Safe Systems: Analyzing the Impact of Undefined Behavior](https://dl.acm.org/doi/10.1145/2517349.2522728)** — Xi Wang, Nickolai Zeldovich, M. Frans Kaashoek, Armando Solar-Lezama，SOSP（2013）
  - **核心貢獻**：提出 STACK 靜態分析器，找出「因為 UB 而被編譯器刪除或改寫」的程式碼，在 Linux 核心、PostgreSQL 等專案中找到上百個真實 bug。
  - **讀哪裡**：開頭幾節的 UB 案例分類（檢查如何被刪掉）與後段在真實專案找到的 bug；中段的求解器細節可以跳過。
  - **和本章的關聯**：本章「編譯器怎麼利用 UB 刪除檢查」的系統化研究版本；也是 [Ch 34](./34-undefined-behavior.md) 的核心參考。

- **[Understanding Integer Overflow in C/C++](https://www-old.cs.utah.edu/~regehr/papers/tosem15.pdf)** — Will Dietz, Peng Li, John Regehr, Vikram Adve，ACM TOSEM（2015；ICSE 2012 版的擴充）
  - **核心貢獻**：以動態檢查工具 IOC（後來併入 Clang 的 UBSan）分析 SPEC 與開源專案，量化「刻意的回繞」與「意外的溢位」各佔多少。
  - **讀哪裡**：前段的溢位分類（有定義／未定義、刻意／意外）與後段對真實程式的實測結果；工具實作細節可略讀。
  - **前提知識**：本章前半的三種語意。

### 部落格 / 技術文章

- **[What Every C Programmer Should Know About Undefined Behavior #1/3](https://blog.llvm.org/2011/05/what-every-c-programmer-should-know.html)** — Chris Lattner（LLVM Blog，2011）
  - **這篇說什麼**：LLVM 作者從編譯器角度解釋 UB 為什麼存在；本章的 `X*2/2` 與 `int` 歸納變數例子都出自這裡。
  - **讀哪裡**：「Signed integer overflow」與「Advantages of Undefined Behavior」兩段。
  - **為什麼值得讀**：從「編譯器的視角」看 UB，能理解為什麼它不會消失。

- **[Nearly All Binary Searches and Mergesorts are Broken](https://research.google/blog/extra-extra-read-all-about-it-nearly-all-binary-searches-and-mergesorts-are-broken/)** — Joshua Bloch（Google Research Blog，2006）
  - **這篇說什麼**：Java `Arrays.binarySearch` 的中點溢位，以及三種修正寫法。
  - **讀哪裡**：整篇，五分鐘。
  - **為什麼值得讀**：說明「教科書上的正確程式」也會被固定寬度打敗。

- **[CVE-2016-0728 分析](https://web.archive.org/web/20160119231540/http://perception-point.io/2016/01/14/analysis-and-exploitation-of-a-linux-kernel-vulnerability-cve-2016-0728/)** — Perception Point（2016）
  - **這篇說什麼**：keyring 參考計數洩漏如何經由 2³² 次呼叫回繞，變成 use-after-free 與權限提升。
  - **讀哪裡**：「The Vulnerability」與「Exploitation」前半；後段的 heap spray 細節可以留到學 kernel exploitation 時再讀。
  - **前提知識**：本章的參考計數一節、[Ch 17](./17-malloc-internals.md) 的配置器概念。

### 官方文件 / Spec

- **[SEI CERT C Coding Standard — Integers (INT)](https://cmu-sei.github.io/secure-coding-standards/sei-cert-c-coding-standard/rules/integers-int/)**
  - **讀哪裡**：INT30-C（無號回繞）、INT31-C（轉換不得遺失資料）、INT32-C（有號溢位）；每條都有不合規與合規的程式碼對照。
  - **注意**：INT32-C 的「合規」範例就是本章「先比較再運算」表格的完整版。

- **[GCC Manual: Integer Overflow Builtins](https://gcc.gnu.org/onlinedocs/gcc/Integer-Overflow-Builtins.html)** 與 **[Clang UBSan](https://clang.llvm.org/docs/UndefinedBehaviorSanitizer.html)**
  - **讀哪裡**：前者看「結果以無限精度計算、依結果型別判斷」那段；後者看「Available checks」列表，找出 `unsigned-integer-overflow`、`implicit-conversion`、`pointer-overflow` 的定義與限制。

### 書籍

- **《Computer Systems: A Programmer's Perspective》3rd ed.** — Bryant & O'Hallaron（2015）
  - **讀哪幾章**：2.3 節的兩個 Aside（FreeBSD `getpeername` 與 XDR）就是本章前兩個案例的出處；2.3.2 練習 2.27–2.31 是寫溢位檢查的好練習。

- **《Secure Coding in C and C++》2nd ed.** — Robert C. Seacord（Addison-Wesley，2013）
  - **這本書的定位**：CERT C 作者寫的整數安全專章，比 CERT 網站更有脈絡。
  - **讀哪幾章**：Chapter 5「Integer Security」整章。

溢位的根源是「把整數當數字」；下一章換個角度，把整數當成**一排位元**來操作——位移、遮罩、設定與清除。你會發現位移也有自己的一套 UB，而且有號與無號混用的比較，正是本章 FreeBSD 漏洞的近親。

→ [Ch 23 bitwise 基礎：位移語意與位元操作](./23-bitwise-basics.md)
