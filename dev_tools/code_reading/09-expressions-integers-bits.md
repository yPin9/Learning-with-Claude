# Ch 9 — 運算式、整數與位元：flags、mask、整數提升的陷阱

> **目標**：看到 `flags & ~EFD_FLAGS_SET`、`FIELD_GET(GENMASK(23, 16), reg)`、`!!(t & X)`、`x & (x - 1)`、`(uint64_t)size + append` 這類運算式時，能一眼說出它的用途；同時知道 C 的整數提升與 signed/unsigned 轉換會在哪裡讓「看起來對」的條件式其實是錯的，並能用編譯器警告把它們抓出來。
> **鴻溝**：本章主要練**慣用法鴻溝**（位元技巧的辨識），兼練一點**脈絡鴻溝**（為什麼 kernel 要把 `GENMASK` 寫得這麼複雜）。

> **環境**：gcc 11.4（WSL Ubuntu 22.04，x86_64，LP64：`int` 32 bit、`long` 與指標 64 bit）；kernel v6.18，Redis 7.2.17，Lua 5.4.9。範例在 `~/code_reading/scratch/09/`。本章所有數字結果都以 LP64 為前提，32-bit 平台上 `long` 的寬度不同，部分輸出會改變。

## 為什麼需要這個？

C 程式碼最密的地方通常不是函式呼叫，而是運算式。一行 `if (!(regval & GLINT_CTL_DIS_AUTOMASK_M) && (FIELD_GET(GLINT_CTL_ITR_GRAN_200_M, regval) == ICE_ITR_GRAN_US) && ...` 裡面沒有任何你不認得的運算子，但第一次看就是讀不進去。原因有兩個：

1. **位元技巧是一種壓縮過的語言**。`x & (x - 1)`、`(x + a - 1) & ~(a - 1)` 這些寫法，熟手看到就認得是「清掉最低位的 1」、「向上對齊」，新手要在紙上算二進位。這是慣用法鴻溝。
2. **C 的整數規則會偷偷改寫你的運算式**。`uint8_t` 相加結果是 `int`，`int` 跟 `unsigned` 比較時 `-1` 會變成四十億。這類錯誤讀碼時特別難發現，因為程式碼**看起來**完全正確。

Spinellis 原書第 2 章講運算式與條件的讀法。本章把它們換成 kernel 的現代寫法：kernel 這十年來把很多手寫位元運算包成了有名字的巨集（`BIT()`、`GENMASK()`、`FIELD_GET()`、`ALIGN()`），認得這些巨集，等於認得它們背後的手寫版本。

## 先建立直覺：一個整數是一排開關

把一個 32-bit 的 `unsigned int` 想成 32 個並排的開關。所有位元運算都是在對這排開關做事：

```
            bit:  31        23      16                 3  0
                  ┌─┬───────┬────────┬───────────────┬────┐
  reg (32-bit) :  │E│ ..... │ CLKDIV │ ............. │MODE│
                  └─┴───────┴────────┴───────────────┴────┘

  x & MASK     → 只看 MASK 蓋住的那幾格（其他格強制 0）    「讀」
  x | MASK     → 把 MASK 蓋住的格子全部打開               「設」
  x & ~MASK    → 把 MASK 蓋住的格子全部關掉               「清」
  x ^ MASK     → 把 MASK 蓋住的格子全部翻轉               「切換」
  (x & M) >> s → 把一個欄位取出來，搬到最低位             「取欄位」= FIELD_GET
  (v << s) & M → 把一個值搬到欄位位置                     「放欄位」= FIELD_PREP
```

讀碼時，先問「這個運算式是在讀、設、清、切換、取欄位、還是放欄位？」，運算式就從一串符號變成一個動作。

## flags 與 mask：四個動作

用 `fcntl.h` 的真實旗標和 `fs/eventfd.c` 的驗證邏輯寫一個小程式（`flags.c`）：

```c
#define EFD_SEMAPHORE (1 << 0)
#define EFD_SHARED_FCNTL_FLAGS (O_CLOEXEC | O_NONBLOCK)
#define EFD_FLAGS_SET (EFD_SHARED_FCNTL_FLAGS | EFD_SEMAPHORE)

static int check(int flags)
{
	if (flags & ~EFD_FLAGS_SET)
		return -22;   /* -EINVAL */
	return 0;
}
```

輸出：

```
O_NONBLOCK = 04000 (octal) = 0x800
O_CLOEXEC  = 02000000 (octal) = 0x80000
EFD_FLAGS_SET  = 0x80801
~EFD_FLAGS_SET = 0xfff7f7fe
f = 0x802, (f & O_NONBLOCK) = 0x800, !!(f & O_NONBLOCK) = 1
set   CLOEXEC  -> 0x80802
clear NONBLOCK -> 0x80002
toggle NONBLOCK-> 0x80802
check(EFD_NONBLOCK|EFD_SEMAPHORE) = 0
check(O_APPEND)                   = -22
```

幾個要記住的讀碼重點：

- **`O_NONBLOCK` 在原始碼裡是八進位**。kernel 的 `include/uapi/asm-generic/fcntl.h:38` 寫的是 `#define O_NONBLOCK	00004000`，開頭的 `0` 代表八進位，等於 `0x800`。`open(2)` 的旗標歷史上一直用八進位寫，讀到 `0` 開頭的常數要提高警覺。
- **`flags & ~ALLOWED` 是「有沒有不認得的旗標」**。這是 syscall 參數驗證的標準寫法，kernel 的 `do_eventfd()` 和 `__do_pipe_flags()` 開頭都是這個形狀：

```c
/* fs/eventfd.c:390（v6.18） */
	if (flags & ~EFD_FLAGS_SET)
		return -EINVAL;
```

```c
/* include/linux/eventfd.h:26（v6.18） */
#define EFD_SHARED_FCNTL_FLAGS (O_CLOEXEC | O_NONBLOCK)
#define EFD_FLAGS_SET (EFD_SHARED_FCNTL_FLAGS | EFD_SEMAPHORE)
```

  為什麼要拒絕不認得的旗標，而不是忽略它？因為如果今天忽略，明天 kernel 新增一個旗標賦予它意義，舊程式碼傳了垃圾值的使用者就會突然得到新行為。拒絕未知旗標讓 API 可以安全擴充。

- **`(x & FLAG)` 的值不是 0/1，而是 0/FLAG**。`f & O_NONBLOCK` 是 `0x800`，不是 `1`。拿它當 `if` 條件沒問題，拿它賦值給一個只有 1 bit 的欄位就出事（下面 `!!` 那節）。

kernel 讀碼時最常見的兩種寫法：

```c
/* fs/pipe.c:374（v6.18），anon_pipe_read() */
		if ((filp->f_flags & O_NONBLOCK) ||
		    (iocb->ki_flags & IOCB_NOWAIT)) {
```

兩個獨立旗標欄位（`file` 的 `f_flags` 和這次 I/O 的 `ki_flags`）任一個說「不要阻塞」就不阻塞。讀到這種條件，把每個 `(x & FLAG)` 念成「x 有設 FLAG」就順了。

## `BIT()`、`GENMASK()`、`FIELD_GET()`、`FIELD_PREP()`

### 定義（v6.18）

```c
/* include/vdso/bits.h:7（v6.18） */
#define BIT(nr)			(UL(1) << (nr))
#define BIT_ULL(nr)		(ULL(1) << (nr))
```

`UL(1)` 展開成 `1UL`。**為什麼不寫 `1 << nr`？** 因為 `1` 是 `int`，`1 << 31` 在 32-bit `int` 上是未定義行為，`1 << 32` 以上更是。用 `unsigned long` 的 1，在 64-bit 上可以安全移到第 63 位（下面有實驗）。

```c
/* include/uapi/linux/bits.h:7（v6.18），給 user-space 與組語用的基本版 */
#define __GENMASK(h, l) (((~_UL(0)) << (l)) & (~_UL(0) >> (__BITS_PER_LONG - 1 - (h))))
```

拆開讀：`~0UL << l` 是「第 l 位以上全 1」，`~0UL >> (63 - h)` 是「第 h 位以下全 1」，兩者 AND 就是 `[h, l]` 這一段全 1。

kernel 內部用的版本多了編譯期檢查：

```c
/* include/linux/bits.h:34、46（v6.18） */
#define GENMASK_INPUT_CHECK(h, l) BUILD_BUG_ON_ZERO(const_true((l) > (h)))
...
#define GENMASK_TYPE(t, h, l)					\
	((t)(GENMASK_INPUT_CHECK(h, l) +			\
	     (type_max(t) << (l) &				\
	      type_max(t) >> (BITS_PER_TYPE(t) - 1 - (h)))))

#define GENMASK(h, l)		GENMASK_TYPE(unsigned long, h, l)
```

`GENMASK_INPUT_CHECK` 在參數合法時值為 0（加上去不影響結果），參數順序寫反時讓編譯失敗。`git blame` 顯示 `GENMASK_TYPE` 是 Vincent Mailhol 在 2025 年加的（commit `19408200c0948`，"bits: introduce fixed-type GENMASK_U*()"，`git describe --contains` 顯示在 v6.16-rc1 週期進入），所以**如果你讀的是 v6.15 以前的 tree，`GENMASK` 的定義會長得不一樣**，但語意相同。

```c
/* include/linux/bitfield.h:45、113、153（v6.18） */
#define __bf_shf(x) (__builtin_ffsll(x) - 1)
...
#define FIELD_PREP(_mask, _val)						\
	({								\
		__BF_FIELD_CHECK(_mask, 0ULL, _val, "FIELD_PREP: ");	\
		((typeof(_mask))(_val) << __bf_shf(_mask)) & (_mask);	\
	})
...
#define FIELD_GET(_mask, _reg)						\
	({								\
		__BF_FIELD_CHECK(_mask, _reg, 0U, "FIELD_GET: ");	\
		(typeof(_mask))(((_reg) & (_mask)) >> __bf_shf(_mask));	\
	})
```

`__builtin_ffsll(mask) - 1` 是 mask 最低的 1 在第幾位，也就是欄位要位移多少。**FIELD_GET/FIELD_PREP 不需要你另外傳位移量**——mask 本身就帶著位移資訊。這是它比手寫 `(reg >> 16) & 0xff` 好的地方：位移量和寬度只寫一次，不會不同步。

### 跑一次：user-space 簡化版

把上面的定義拿掉編譯期檢查，搬到 user-space（`bits.c`），描述一個假想的暫存器：bit 31 是 ENABLE、bits 23..16 是 CLKDIV、bits 3..0 是 MODE：

```c
#define CTRL_ENABLE	BIT(31)
#define CTRL_CLKDIV	__GENMASK(23, 16)
#define CTRL_MODE	__GENMASK(3, 0)
...
	reg |= CTRL_ENABLE;
	reg |= FIELD_PREP(CTRL_CLKDIV, 0x2a);
	reg |= FIELD_PREP(CTRL_MODE, 5);
```

```
BIT(0)=0x1 BIT(5)=0x20 BIT(63)=0x8000000000000000
GENMASK(15, 8)  = 0xff00
GENMASK(23, 16) = 0xff0000
GENMASK(8, 15)  = 0   <- 參數順序寫反
reg = 0x802a0005
CLKDIV = 0x2a, MODE = 5, ENABLE = 1
FIELD_PREP(MODE, 0x1f) = 0xf  <- 高位被 mask 靜默截掉
hand-written (reg >> 16) & 0xff = 0x2a
```

注意兩行失敗輸出：

- `GENMASK(8, 15)` 在沒有檢查的版本裡**安靜地變成 0**。任何拿它當 mask 的程式碼都會永遠讀到 0，沒有警告。
- `FIELD_PREP(MODE, 0x1f)` 把 5 bit 的值塞進 4 bit 欄位，最高位被 `& mask` 截掉，結果是 `0xf`。

### 讓 kernel 版本替你抓錯

既然 `~/code_reading/build/` 有建好的 v6.18 kernel，我們寫一個不載入、只編譯的 module，看真正的 kernel 巨集怎麼反應：

```c
/* ~/code_reading/scratch/09/kmod/gm.c */
#ifdef BAD_ORDER
static unsigned long m1 = GENMASK(8, 15);
#endif
#ifdef BAD_FIELD
static u32 v1;
static int __init gm_init(void) { v1 = FIELD_PREP(GENMASK(3, 0), 0x1f); return 0; }
#endif
```

```
$ make -C ~/code_reading/linux O=~/code_reading/build M=$PWD ccflags-y=-DBAD_ORDER modules
.../include/linux/compiler.h:197:67: error: static assertion failed: "const_true((15) > (8)) is true"

$ make ... ccflags-y=-DBAD_FIELD modules
.../include/linux/compiler_types.h:602:45: error: call to ‘__compiletime_assert_368’ declared with attribute error: FIELD_PREP: value too large for the field
```

兩個錯誤都在**編譯期**被抓到。這就是 kernel 把一行位元運算包成一個看起來很複雜的巨集的理由：**複雜度換來的是編譯期驗證**。讀碼時看到 `GENMASK_INPUT_CHECK`、`__BF_FIELD_CHECK` 這類東西，可以直接跳過——它們不影響執行期的值，只是護欄。這也是 Ch 4「選擇性閱讀」的實際應用：知道哪些部分可以黑箱。

### 在真實 driver 裡認出它們

```c
/* drivers/net/ethernet/intel/ice/ice_base.c:220（v6.18） */
static void ice_cfg_itr_gran(struct ice_hw *hw)
{
	u32 regval = rd32(hw, GLINT_CTL);

	/* no need to update global register if ITR gran is already set */
	if (!(regval & GLINT_CTL_DIS_AUTOMASK_M) &&
	    (FIELD_GET(GLINT_CTL_ITR_GRAN_200_M, regval) == ICE_ITR_GRAN_US) &&
	    (FIELD_GET(GLINT_CTL_ITR_GRAN_100_M, regval) == ICE_ITR_GRAN_US) &&
	    (FIELD_GET(GLINT_CTL_ITR_GRAN_50_M, regval) == ICE_ITR_GRAN_US) &&
	    (FIELD_GET(GLINT_CTL_ITR_GRAN_25_M, regval) == ICE_ITR_GRAN_US))
		return;

	regval = FIELD_PREP(GLINT_CTL_ITR_GRAN_200_M, ICE_ITR_GRAN_US) |
```

`_M` 結尾是這個 driver 對 mask 的命名慣例，定義在自動產生的標頭：

```c
/* drivers/net/ethernet/intel/ice/ice_hw_autogen.h:185（v6.18） */
#define GLINT_CTL_ITR_GRAN_200_M		ICE_M(0xF, 16)
/* drivers/net/ethernet/intel/ice/ice_osdep.h:31（v6.18） */
#define ICE_M(m, s)		((m ## U) << (s))
```

`ICE_M(0xF, 16)` 是「從第 16 位開始的 4 bit」，等價於 `GENMASK(19, 16)`。每個 driver 可能有自己的 mask 巨集，**但讀法都一樣：找出 mask 蓋住哪幾位**。整個條件式翻成人話是：「auto-mask 沒關閉、而且四個粒度欄位都已經是我們要的值，就不用重寫暫存器」。

v6.18 tree 裡 `FIELD_GET(` 出現 9534 次，大多數在 driver。讀 driver 時它會是你最常見的巨集之一。

## 整數提升與 signed/unsigned 陷阱

### 整數提升：小於 `int` 的型別先變成 `int`

C 標準規定，運算式裡 `char`、`short`（含 unsigned 版本）、`uint8_t`、`uint16_t` 在做算術之前會先被提升成 `int`（如果 `int` 放得下它的所有值）。這叫**整數提升**（integer promotion）。實驗（`promo.c`）：

```c
	uint8_t a = 200, b = 100;
	printf("a + b            = %d  (型別是 int，不是 uint8_t)\n", a + b);
	uint8_t c = a + b;

	uint8_t x = 0x0f;
	if (~x == 0xf0) ...

	volatile unsigned short us = 0xffff;
	printf("us * us = %d ...", us * us);
```

`gcc -Wall -Wextra` 先給一個警告：

```
promo.c:12:16: warning: comparison of promoted bitwise complement of an unsigned value with constant [-Wsign-compare]
   12 |         if (~x == 0xf0)
      |                ^~
```

執行：

```
a + b            = 300  (型別是 int，不是 uint8_t)
uint8_t c = a+b  = 44
~x != 0xf0，因為 ~x = 0xfffffff0
(uint8_t)~x == 0xf0
us * us = -131071  <- 兩個 unsigned short 相乘，提升成 int 後溢位
```

逐條解釋：

- `a + b` 是 `int` 的 300，存回 `uint8_t` 才截成 300 − 256 = 44。
- `~x`：`x` 先提升成 `int` 的 `0x0000000f`，取反得到 `0xfffffff0`，不等於 `0xf0`。這是讀 driver 時最常踩的坑：**對窄型別取 `~`，結果是寬的**。
- `us * us`：兩個 `unsigned short` 都提升成**有號** `int`，65535 × 65535 = 4294836225 超過 `INT_MAX`，有號溢位是未定義行為。加 `-fsanitize=undefined` 跑：

```
promo.c:20:2: runtime error: signed integer overflow: 65535 * 65535 cannot be represented in type 'int'
```

這是很多人想不到的：**兩個 unsigned 運算元相乘，卻發生有號溢位**。（我們加了 `volatile` 防止 gcc 在編譯期把常數乘法算掉，否則 UBSan 在執行期看不到這個乘法。）

### signed 與 unsigned 比較

當一個 `int` 和一個 `unsigned int`（或更寬的 unsigned，例如 `size_t`）比較，C 的「一般算術轉換」會把 `int` 轉成 unsigned。`-1` 轉成 `unsigned int` 就是 4294967295。實驗（`signcmp.c`）：

```c
	int i = -1;
	unsigned int u = 1;
	if (i < u) ...

	char buf[16];
	int n = -1;
	if (n < sizeof(buf)) ...

	size_t got = fake_read(buf, 8, 1);   /* fake_read 失敗回傳 -1（ssize_t） */
	if (got > 0) ...
```

先看編譯器在不同警告等級下說什麼：

```
== gcc -Wall
== gcc -Wall -Wextra
signcmp.c: In function ‘main’:
signcmp.c:19:15: warning: comparison of integer expressions of different signedness: ‘int’ and ‘unsigned int’ [-Wsign-compare]
   19 |         if (i < u)
      |               ^
signcmp.c:26:15: warning: comparison of integer expressions of different signedness: ‘int’ and ‘long unsigned int’ [-Wsign-compare]
   26 |         if (n < sizeof(buf))
      |               ^
```

**在 C 模式下，`-Wall` 不含 `-Wsign-compare`，要 `-Wextra` 才有**（C++ 模式下 `-Wall` 就有，這是很多人的誤解來源）。執行結果：

```
-1 < 1u 不成立：-1 轉成 unsigned 後是 4294967295
n=-1 卻「不小於」sizeof(buf)=16
讀到 18446744073709551615 bytes？其實是錯誤 -1
ssize_t 版本正確偵測到錯誤 ret=-1
```

第三種錯誤（`size_t` 接 `ssize_t`）連 `-Wextra` 都不警告，要 `-Wsign-conversion`：

```
signcmp.c:32:22: warning: conversion to ‘size_t’ {aka ‘long unsigned int’} from ‘ssize_t’ {aka ‘long int’} may change the sign of the result [-Wsign-conversion]
   32 |         size_t got = fake_read(buf, 8, 1);
```

kernel 不開 `-Wsign-conversion`（會產生海量警告），所以這類錯誤在 kernel 裡要靠讀碼和 review 抓。

### kernel 怎麼防：`min()` 的型別檢查

kernel 的 `min()` / `max()` 不是一般教科書的 `((a) < (b) ? (a) : (b))`。它的註解直接說明了設計目標：

```c
/* include/linux/minmax.h:10（v6.18） */
/*
 * min()/max()/clamp() macros must accomplish several things:
 *
 * - Avoid multiple evaluations of the arguments (so side-effects like
 *   "x++" happen only once) when non-constant.
 * - Perform signed v unsigned type-checking (to generate compile
 *   errors instead of nasty runtime surprises).
 * - Unsigned char/short are always promoted to signed int and can be
 *   compared against signed or unsigned arguments.
 * - Unsigned arguments can be compared against non-negative signed constants.
 * - Comparison of a signed argument against an unsigned constant fails
 *   even if the constant is below __INT_MAX__ and could be cast to int.
 */
```

讀碼時看到 `min_t(size_t, a, b)`，它的意思是「我知道 a、b 型別不同，請都轉成 `size_t` 再比」——作者明確地做了型別決定，這是一個值得停下來想「轉換安全嗎」的 beacon。

### `size_t` 與 `ssize_t`：回傳值的兩種語言

- `size_t`：無號，表示大小或數量。`sizeof` 的型別、`strlen()` 的回傳型別。
- `ssize_t`：有號（POSIX 定義，不是 ISO C），表示「數量或 -1 錯誤」。`read(2)`、`write(2)` 回傳它。

kernel 的 VFS 介面用 `ssize_t` 回傳「讀到幾個 byte，或 -errno」：

```c
/* include/linux/fs.h:2272、2274（v6.18），struct file_operations */
	ssize_t (*read) (struct file *, char __user *, size_t, loff_t *);
	ssize_t (*read_iter) (struct kiocb *, struct iov_iter *);
```

參數裡要讀幾個是 `size_t`，回傳讀到幾個是 `ssize_t`——同一個函式裡兩種語言並存。`eventfd_read()` 是個好例子：

```c
/* fs/eventfd.c:214（v6.18），節錄 */
static ssize_t eventfd_read(struct kiocb *iocb, struct iov_iter *to)
{
	...
	if (iov_iter_count(to) < sizeof(ucnt))
		return -EINVAL;
	...
	if (unlikely(copy_to_iter(&ucnt, sizeof(ucnt), to) != sizeof(ucnt)))
		return -EFAULT;

	return sizeof(ucnt);
}
```

`return -EINVAL` 回的是負數，`return sizeof(ucnt)` 回的是 8（`size_t` 隱式轉成 `ssize_t`）。呼叫端用 `ret < 0` 分辨。

另一個要認的變體是 `copy_to_user()`：

```c
/* include/linux/uaccess.h:216（v6.18） */
static __always_inline unsigned long __must_check
copy_to_user(void __user *to, const void *from, unsigned long n)
```

它回傳 `unsigned long`，意思是「**沒有**複製成功的 byte 數」，0 代表全部成功。所以 kernel 裡的標準寫法是 `if (copy_to_user(...)) return -EFAULT;`——非零就是失敗。**同樣是「回傳一個數字」，`read` 系列是「成功的量」，`copy_to_user` 是「失敗的量」**，讀碼時一定要查清楚是哪一種。

### Redis 怎麼防溢位：先轉 unsigned 再加

```c
/* redis-7.2.17/src/t_string.c:40 */
static int checkStringLength(client *c, long long size, long long append) {
    if (mustObeyClient(c))
        return C_OK;
    /* 'uint64_t' cast is there just to prevent undefined behavior on overflow */
    long long total = (uint64_t)size + append;
    /* Test configured max-bulk-len represending a limit of the biggest string object,
     * and also test for overflow. */
    if (total > server.proto_max_bulk_len || total < size || total < append) {
```

`(uint64_t)size + append`：一邊是 `uint64_t`，另一邊 `long long` 被轉成 `uint64_t`，加法在無號域進行，溢位有定義（模 2^64）。結果再轉回 `long long`（實作定義行為，gcc 上是位元原樣保留），溢位時會變成負數，被 `total < size` 抓到。我們驗證有號版本真的是 UB（`overflow.c`，`-O0 -fsanitize=undefined`）：

```
overflow.c:16:12: runtime error: signed integer overflow: 9223372036854775807 + 1 cannot be represented in type 'long long int'
check(10, 20)           = 0
check(LLONG_MAX, 1)     = -1
check_naive(LLONG_MAX,1)= -1
```

兩個版本在這次執行都回傳 -1，但 naive 版本是「碰巧對」。同一個程式用 `-O2` 編譯時 UBSan 沒有報告——最佳化器把運算改寫掉了，這正說明依賴未定義行為的檢查是不可靠的：**編譯器有權假設有號溢位不會發生，進而把 `total < size` 這種檢查最佳化掉**。kernel 對這類需求有專用的 `check_add_overflow()`（`include/linux/overflow.h:67`），讀到它就知道作者在處理溢位。

同一個檔案的 `getrangeCommand()` 還有一個型別轉換值得注意：

```c
/* redis-7.2.17/src/t_string.c:532 */
    if ((unsigned long long)end >= strlen) end = strlen-1;
```

`end` 是 `long long`，`strlen` 是 `size_t`。前面幾行已經保證 `end >= 0`，所以作者明確轉成 unsigned 來比較，既避免 `-Wsign-compare` 警告，也表明「我知道這裡兩邊型別不同，而且轉換是安全的」。

### 位移的陷阱

```c
/* shift.c */
	int n = 30 + argc;        /* 執行期才知道：沒帶參數時 n = 31 */
	unsigned long ok = 1UL << n;
	int bad = 1 << n;         /* int 的 1 左移 31 位 */
```

```
1UL << 31 = 0x80000000
1 << 31   = -2147483648
shift.c:9:14: runtime error: left shift of 1 by 31 places cannot be represented in type 'int'
```

這就是 `BIT()` 用 `UL(1)` 的理由。讀到 `1 << n` 而 `n` 可能到 31 以上，就是一個 bug 候選。

## 短路求值：運算式即控制流

`&&` 和 `||` 保證由左到右求值，而且左邊已經決定結果時右邊**不會被執行**。這讓它們變成控制流工具。

### 便宜的檢查放左邊，有副作用的放右邊

```c
/* fs/pipe.c:787（v6.18） */
bool pipe_is_unprivileged_user(void)
{
	return !capable(CAP_SYS_RESOURCE) && !capable(CAP_SYS_ADMIN);
}
...
/* fs/pipe.c:809（v6.18），alloc_pipe_info() */
	if (too_many_pipe_buffers_soft(user_bufs) && pipe_is_unprivileged_user()) {
```

`capable()` 不是純函式。它在檢查成功時會設定行程旗標：

```c
/* kernel/capability.c:342（v6.18），ns_capable_common() */
	capable = security_capable(current_cred(), ns, cap, opts);
	if (capable == 0) {
		current->flags |= PF_SUPERPRIV;
		return true;
	}
	return false;
```

`PF_SUPERPRIV` 記錄「這個行程用過特權」（行程會計會用到），而 `security_capable()` 會經過 LSM，SELinux 這類模組可能在拒絕時留下稽核紀錄。所以「先檢查用量是否超標，超標了才問有沒有特權」這個順序，除了效率，還讓沒有超標的一般情況完全不觸發 capability 檢查。（這個順序的動機是我們從程式碼推論的；引入這段的 commit `85c2dd5473b2` 的說明只講了「讓 root 能超過限制」，沒有討論順序。kernel 另外提供 `ns_capable_noaudit()` 給明確不想留稽核紀錄的場合。）

用一個 user-space 模擬驗證兩種順序的差別（`shortcircuit.c`，`capable()` 每次被呼叫就計數）：

```
A: too_many_soft(bufs) && !capable(...)
    audits=0
B: !capable(...) && too_many_soft(bufs)
    capable(CAP_SYS_RESOURCE) called
    audits=1
C: !capable(A) && !capable(B)
    capable(CAP_SYS_RESOURCE) called
    capable(CAP_SYS_ADMIN) called
    unpriv=1 audits=3
```

**邏輯上 A 和 B 等價，行為上不等價**。重構時把 `&&` 兩邊對調，是會改變系統行為的。

### De Morgan：把否定推進去或拉出來

`!capable(A) && !capable(B)` 依 De Morgan 定律等於 `!(capable(A) || capable(B))`：「既沒有 A 也沒有 B」=「不是（有 A 或有 B）」。讀複雜條件時，把否定拉到最外層常常更好懂。`ice_cfg_itr_gran()` 那個五項 `&&` 也可以這樣讀：整個條件成立就 `return`，等於「只要有任一項不符合，就往下重寫暫存器」。

### 把斷言和副作用藏在短路裡

Lua 直譯器主迴圈有這麼一行：

```c
/* lua-5.4.9/lvm.c:1188 */
    lua_assert(isIT(i) || (cast_void(L->top.p = base), 1));
```

拆開讀：如果 `isIT(i)` 為真（這條指令需要用到 top），短路，什麼都不做；否則執行逗號運算式 `(L->top.p = base, 1)`——先把 `top` 設成 `base`（讓錯誤使用 top 的指令更容易被發現），再產生值 1 讓斷言通過。**這是一個偽裝成斷言的賦值**。

但它只在除錯建置時存在：

```c
/* lua-5.4.9/llimits.h:103 */
#if defined LUAI_ASSERT
#undef NDEBUG
#include <assert.h>
#define lua_assert(c)           assert(c)
#endif

#if defined(lua_assert)
...
#else
#define lua_assert(c)		((void)0)
```

一般建置下 `lua_assert(...)` 整個變成 `((void)0)`，裡面的賦值消失。讀碼時要分清：**斷言裡的副作用只存在於某些建置**，不能當成正式邏輯。

## 位元技巧速查：一眼認出來

用一個程式把常見技巧跑一遍（`tricks.c`，巨集定義從 v6.18 複製）：

```
s.nonblock = flags & 0x800    -> 0  (0x800 的低 1 bit 是 0)
s.nonblock = !!(flags & 0x800) -> 1
x=0     x&(x-1)=0     is_power_of_2=0 popcount=0  x&-x=0
x=1     x&(x-1)=0     is_power_of_2=1 popcount=1  x&-x=1
x=6     x&(x-1)=4     is_power_of_2=0 popcount=2  x&-x=2
x=64    x&(x-1)=0     is_power_of_2=1 popcount=1  x&-x=64
x=96    x&(x-1)=64    is_power_of_2=0 popcount=2  x&-x=32
x=4096  x&(x-1)=0     is_power_of_2=1 popcount=1  x&-x=4096
ALIGN(13, 8)=16 ALIGN(16, 8)=16 round_up(13, 8)=16 round_down(13, 8)=8
DIV_ROUND_UP(13, 8)=2  (需要幾個 8-byte 塊)
IS_ALIGNED(4096, 4096)=1 IS_ALIGNED(4100, 4096)=0
ALIGN(13, 12)=16  <- 12 不是 2 的冪，結果錯（不是 24）
```

### `!!x`：正規化成 0 或 1

`s.nonblock` 是 1 bit 的 bit-field。`flags & 0x800` 的值是 `0x800`，存進 1 bit 只留最低位，也就是 0——**旗標明明有設，存進去卻是 0**。`gcc -Wall -Wextra` 對這行完全沉默，要 `-Wconversion` 才會說：

```
tricks.c:37:22: warning: conversion from ‘unsigned int’ to ‘unsigned char:1’ may change value [-Wconversion]
   37 |         s.nonblock = flags & 0x800;
```

`!!` 是兩次邏輯否定：任何非零值 → 0 → 1；零 → 1 → 0。kernel 用它把「位元測試結果」轉成「布林值」，特別是在要寫進 ABI 結構的時候：

```c
/* kernel/audit.c:1539（v6.18） */
		s.enabled = t & AUDIT_TTY_ENABLE;
		s.log_passwd = !!(t & AUDIT_TTY_LOG_PASSWD);
```

這兩行並排很有教育意義。`include/linux/audit.h:149` 定義 `AUDIT_TTY_ENABLE` 是 `BIT(0)`、`AUDIT_TTY_LOG_PASSWD` 是 `BIT(1)`；而 `struct audit_tty_status`（`include/uapi/linux/audit.h:506`）的欄位註解寫著 `1 = enabled, 0 = disabled`。`BIT(0)` 測出來本來就是 0 或 1，不需要 `!!`；`BIT(1)` 測出來是 0 或 2，必須 `!!` 才符合 ABI 承諾的 1。**看到一行有 `!!`、隔壁一行沒有，通常不是作者偷懶，而是位元位置不同**。

### `x & (x - 1)`：清掉最低位的 1

`x - 1` 會把最低位的 1 變成 0、它下面的 0 全變成 1；再和 `x` AND，效果是「清掉最低位的 1」。兩個常見用途：

- **判斷 2 的冪**：2 的冪只有一個 1，清掉後變 0。

```c
/* include/linux/log2.h:44（v6.18） */
static __always_inline __attribute__((const))
bool is_power_of_2(unsigned long n)
{
	return (n != 0 && ((n & (n - 1)) == 0));
}
```

  注意 `n != 0` 的守衛：`0 & (0 - 1)` 也是 0，沒有守衛的話 0 會被誤判成 2 的冪（上面輸出 `x=0` 那行的 `x&(x-1)=0` 就是這個情況）。

- **數 1 的個數**（Kernighan 的方法）：`while (x) { x &= x - 1; n++; }`，迴圈次數 = 1 的個數。

相關的 `x & -x` 是「只留最低位的 1」，上面輸出 96 → 32。看到它通常是在找最低的設定位。

### `ALIGN()`、`round_up()`、`round_down()`、`DIV_ROUND_UP()`

```c
/* include/vdso/align.h:8（v6.18） */
#define ALIGN(x, a)		__ALIGN_KERNEL((x), (a))
/* include/uapi/linux/const.h:48（v6.18） */
#define __ALIGN_KERNEL(x, a)		__ALIGN_KERNEL_MASK(x, (__typeof__(x))(a) - 1)
#define __ALIGN_KERNEL_MASK(x, mask)	(((x) + (mask)) & ~(mask))
```

`(x + a - 1) & ~(a - 1)`：先加上 `a - 1`（讓不足一格的部分「進位」），再把低位清掉。只在 `a` 是 2 的冪時正確——`a - 1` 必須是低位全 1 的 mask。上面 `ALIGN(13, 12)` 得到 16 而不是 24，就是用了非 2 的冪。`include/vdso/align.h:7` 的註解 `/* @a is a power of 2 value */` 是唯一的提醒，巨集本身不檢查。

`round_up(x, y)` 用的是另一個寫法 `((x - 1) | (y - 1)) + 1`，結果相同。`DIV_ROUND_UP(n, d)` 是 `(n + d - 1) / d`，用除法，所以**不要求** 2 的冪，回答的是「需要幾格」而不是「對齊到哪」。

### 為什麼 kernel 要 `__typeof__(x)`

`include/linux/math.h:9` 的註解說：

```c
/* include/linux/math.h:9（v6.18） */
/*
 * This looks more complex than it should be. But we need to
 * get the type for the ~ right in round_down (it needs to be
 * as wide as the result!), and we want to evaluate the macro
 * arguments just once each.
 */
#define __round_mask(x, y) ((__typeof__(x))((y)-1))
```

「`~` 要和結果一樣寬」是什麼意思？實驗（`width.c`）：64-bit 位址，32-bit 對齊量：

```
addr                     = 0x123456789abc
naive  addr & ~(page-1)  = 0x56789000  <- 高 32 bit 被清掉
kernel round_down()      = 0x123456789000
~(page-1) 是 4-byte 的 0xfffff000
```

`page` 是 `unsigned int`，`~(page - 1)` 是 32-bit 的 `0xfffff000`；和 64-bit 的 `addr` AND 時，它被**零擴展**成 `0x00000000fffff000`，高 32 位全部被清掉。kernel 版本先把 mask 轉成 `x` 的型別再取反，`~` 就在 64-bit 上進行。這正是前面「對窄型別取 `~`」陷阱的反面：這次是 unsigned 的零擴展，結果一樣錯。

## 對比與取捨

| 寫法 | 優點 | 缺點 | 讀碼時的判讀 |
|---|---|---|---|
| `(reg >> 16) & 0xff` | 不需任何巨集 | 位移量和寬度寫兩處，容易不同步 | 手寫欄位擷取 |
| `FIELD_GET(GENMASK(23,16), reg)` | 欄位定義只寫一次；編譯期檢查 | 要認得巨集 | 同上，有護欄 |
| `1 << n` | 短 | `n >= 31` 時 UB | 可疑 |
| `BIT(n)` | `unsigned long`，到 63 都安全 | 32-bit 平台上只到 31 | 安全的單一位元 |
| `x & ~(a - 1)` | 短 | `a` 型別比 `x` 窄時截斷高位 | 檢查型別 |
| `round_down(x, a)` / `ALIGN_DOWN` | 型別正確 | 仍要求 2 的冪 | 安全的對齊 |
| `a + b` 再檢查 `< a` | 直覺 | 有號時是 UB，可能被最佳化掉 | 可疑 |
| `check_add_overflow(a, b, &d)` | 明確、可攜 | 要認得 | 作者在處理溢位 |

## 踩雷集錦

1. **「`-Wall` 會警告 signed/unsigned 比較」**：C 模式下不會，要 `-Wextra`（C++ 下 `-Wall` 才有）。`size_t` 接 `ssize_t` 的隱式轉換連 `-Wextra` 都不管，要 `-Wsign-conversion`。
2. **「兩個 unsigned 運算不會有有號溢位」**：小於 `int` 的 unsigned 型別會先提升成**有號** `int`。`unsigned short` × `unsigned short` 可以觸發有號溢位 UB。
3. **「`x & FLAG` 的結果是 0 或 1」**：是 0 或 `FLAG`。當條件用沒問題，存進 `bool` 也沒問題（C99 的 `_Bool` 轉換會正規化），但存進 1 bit 的 bit-field 或寫進 ABI 結構就要 `!!`。
4. **「`ALIGN(x, 12)` 會對齊到 12 的倍數」**：不會，`ALIGN` 只對 2 的冪正確，而且不檢查。任意倍數請用 `roundup()`（注意：沒有底線的那個，定義在同一個 `math.h`，用除法）。
5. **「`a && b` 和 `b && a` 一樣」**：邏輯值一樣，副作用不一樣。`capable()` 這類有副作用的函式，位置是有意義的。
6. **「`lua_assert` 裡的程式碼一定會執行」**：只在定義了 `LUAI_ASSERT` 的建置會。kernel 的 `WARN_ON(x)` 則相反：條件**一定**會被求值（它還會回傳條件值，所以常見 `if (WARN_ON(!p)) return;`）。讀到斷言類巨集，一律先查它在正式建置下是否求值。

## 進階：再往深一層

- **`-fwrapv` 與 kernel**：kernel 用 `-fno-strict-overflow` 編譯（v6.18 頂層 `Makefile:1065`），讓有號溢位的行為在實務上接近二補數環繞，避免上面 `check_naive` 那種檢查被最佳化掉。這是 kernel 的 C 和標準 C 又一個不同之處（Ch 15）。但不要因此在 kernel 裡依賴有號溢位，`overflow.h` 才是正路。
- **`__builtin_ffs`、`__builtin_ctz`、`__builtin_popcount`**：kernel 的 `__ffs()`、`hweight32()` 等最後都會用到這類 builtin 或對應指令（x86 的 `bsf`/`tzcnt`/`popcnt`）。用 `gcc -O2 -S` 看 `popcount_kernighan()` 會被編譯成什麼，在支援 `popcnt` 的 `-march` 下可能整個迴圈被換成一條指令。
- **bitmap API**：當旗標多到一個 `unsigned long` 放不下，kernel 改用 `DECLARE_BITMAP` 和 `set_bit()` / `test_bit()`，`BIT_WORD(nr)` 和 `BIT_MASK(nr)`（`include/linux/bits.h:8–9`）負責把位元編號拆成「第幾個 word」和「word 裡的哪一位」。這在 [Ch 27](./27-bitmap-percpu-kfifo.md) 詳講。
- **endianness**：`FIELD_GET` 對的是 CPU 原生位元組序的值。從裝置讀來的 `__le32` 要先 `le32_to_cpu()`，或用 `le32_get_bits()` 這類合併版（同樣在 `bitfield.h`，由第 191 行的 `____MAKE_OP` 巨集為 le/be 16/32/64 各產生一組）。sparse 會檢查你有沒有忘記轉（Ch 18）。

## 動手練習

1. 在 v6.18 tree 裡 `git grep -n "& (.* - 1)" -- mm/*.c | head -20`，逐一判斷每一處是 2 的冪判斷、對齊、還是取餘數（`x & (n - 1)` 在 `n` 是 2 的冪時等於 `x % n`）。
2. 修改 `width.c`，把 `addr` 改成 `uint32_t`、`page` 改成 `uint64_t`，預測 naive 版本和 kernel 版本的結果，再跑一次驗證。
3. 寫一個 `FIELD_PREP` 誤用的 user-space 程式：mask 是 `GENMASK(7, 4)`，值是 `0x13`。先在紙上算結果，再執行驗證。然後在 `kmod/gm.c` 裡用常數試同樣的錯，看 kernel 版本的錯誤訊息。
4. 找出 `fs/pipe.c` 裡所有 `&&` 右邊是函式呼叫的條件式，判斷每一個函式有沒有副作用，對調左右會不會改變行為。

## 本章重點整理

- 位元運算只有六個動作：讀、設、清、切換、取欄位、放欄位。`FIELD_GET`/`FIELD_PREP` 讓 mask 同時帶著位移量。
- kernel 把位元運算包成巨集，複雜度換來編譯期檢查；讀碼時 `*_CHECK` 類的部分可以黑箱。
- 小於 `int` 的型別在運算前提升成有號 `int`；signed 與 unsigned 比較時 signed 轉成 unsigned。`-Wextra` 抓比較，`-Wsign-conversion` 抓賦值。
- 短路求值是控制流：便宜/無副作用的檢查放左邊，`&&` 兩邊不可隨意對調。
- `!!` 正規化成 0/1；`x & (x-1)` 清最低位的 1；`ALIGN` 只對 2 的冪正確；mask 要和被 mask 的值一樣寬。

## 自我檢核

- [ ] 不看本章，寫出 `GENMASK(h, l)` 的基本版定義，並說明參數順序寫反時沒有檢查的版本會得到什麼
- [ ] 有人寫 `uint8_t x = ...; if (~x == 0)`，你能解釋為什麼這個條件永遠不成立
- [ ] 能說出 `-Wall`、`-Wextra`、`-Wsign-conversion` 各自抓得到上面 `signcmp.c` 的哪幾個錯
- [ ] 能解釋 `kernel/audit.c` 那兩行為什麼一行有 `!!`、一行沒有
- [ ] 能說出 `read()` 系列和 `copy_to_user()` 的回傳值語意差異，以及各自的錯誤判斷寫法
- [ ] 解釋 Redis `checkStringLength()` 為什麼要先轉 `uint64_t` 再加，以及「拿掉轉型在測試裡也能過」為什麼不代表沒問題

## 延伸閱讀

### 書籍

- **《Hacker's Delight》（2nd ed.）** — Henry S. Warren（Addison-Wesley, 2012）
  - **讀哪裡**：第 2 章 "Basics"（`x & (x - 1)`、`x & -x` 這類操作最右邊位元的技巧都在這裡）、第 3 章 "Power-of-2 Boundaries"（對齊與 round up/down）。
  - **能學到什麼**：本章所有位元技巧的系統化推導與更多變體。讀完你會發現 kernel 的 `ALIGN`、`is_power_of_2` 都是這本書裡的標準式。
  - **前提**：熟悉二補數表示法。

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪裡**：第 2 章「Basic Programming Elements」中講運算式與條件的段落。
  - **能學到什麼**：原書強調「把運算式翻譯成意圖」，本章的六個動作就是這個方法的具體化。
  - **前提**：無。

### 官方文件 / 標準

- **ISO/IEC 9899（C 標準），6.3.1.1 "Boolean, characters, and integers" 與 6.3.1.8 "Usual arithmetic conversions"**（可讀 C11 的公開草案 N1570）
  - **讀哪裡**：這兩小節加起來不到兩頁，定義了整數提升與一般算術轉換。
  - **能學到什麼**：本章 `promo.c`、`signcmp.c` 每一個結果的權威依據。
  - **前提**：本章的實驗。

- **GCC Manual — Warning Options**（gcc.gnu.org 線上手冊）
  - **讀哪裡**：`-Wsign-compare`、`-Wsign-conversion`、`-Wconversion`、`-Wtype-limits` 四個條目，注意每個條目寫著它被 `-Wall` 或 `-Wextra` 包含與否。
  - **能學到什麼**：知道自己的建置設定能抓到哪些整數錯誤、抓不到哪些。
  - **前提**：無。

- **kernel 原始碼中的 `include/linux/bitfield.h` 開頭註解**（v6.18）
  - **讀哪裡**：檔案開頭的使用說明（第 21 行起的 Example 段），有 `FIELD_GET`/`FIELD_PREP` 的範例。
  - **能學到什麼**：官方建議的 mask 定義與使用方式。
  - **前提**：本章的 `bits.c` 實驗。

### 部落格 / 技術文章

- **"Bit Twiddling Hacks"** — Sean Eron Anderson（Stanford Graphics 網站）
  - **讀哪裡**：「Determining if an integer is a power of 2」、「Counting bits set」兩節。
  - **能學到什麼**：每個技巧都附正確性說明與邊界條件（例如 0 是否算 2 的冪）。和《Hacker's Delight》互補：這份是速查表，那本是推導。
  - **前提**：無。

運算式讀懂了，下一層是指標：走訪、指標的指標、函式指標——kernel 的間接性幾乎全建立在它們上面。

→ [Ch 10 指標慣用法：走訪、指標的指標、函式指標](./10-pointer-idioms.md)
