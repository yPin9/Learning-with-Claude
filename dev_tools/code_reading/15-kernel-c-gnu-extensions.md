# Ch 15 — GNU C 擴充：statement expression、typeof、attribute、flexible array

> **目標**：看到 `({ ... })`、`typeof`、`__auto_type`、`__attribute__((...))`、`data[]` 加 `struct_size()`、`__builtin_constant_p`、`case 'a' ... 'z':`、`asm goto` 時，知道它是哪一種 GNU 擴充、為什麼 kernel 要用它、讀碼時該把它「翻譯」成什麼意思。
> **鴻溝**：本章主要練**慣用法鴻溝**（認得 pattern），附帶一點**脈絡鴻溝**（為什麼 ISO C 不夠用）。

> **環境**：Linux kernel v6.18 原始碼；gcc 11.4、clang 14（WSL Ubuntu 22.04，x86_64）。本章所有 user-space 範例都用 `-std=gnu11` 編過、兩個編譯器各跑過一次。

## 為什麼需要這個？

你在 K&R 或大學課學的是 ISO C。打開 `include/linux/minmax.h`，第一個 `min()` 就讀不下去：

```c
/* include/linux/minmax.h:91-95（v6.18） */
#define __careful_cmp_once(op, x, y, ux, uy) ({		\
	__auto_type ux = (x); __auto_type uy = (y);	\
	BUILD_BUG_ON_MSG(!__types_ok(ux, uy),		\
		#op"("#x", "#y") signedness error");	\
	__cmp(op, ux, uy); })
```

`({` 是什麼？`__auto_type` 是型別嗎？這不是你 C 不好，是 **kernel 寫的根本不是 ISO C**。頂層 Makefile 寫得很清楚：

```make
# Makefile:587（v6.18）
KBUILD_CFLAGS += -std=gnu11
```

`scripts/Makefile.extrawarn:31` 還補了一句註解：「The kernel builds with '-std=gnu11' so use of GNU extensions is acceptable.」——GNU 擴充在 kernel 不是偷偷用，是**政策上允許**。

歷史脈絡：kernel 從 1991 年起就綁 gcc，很長一段時間用的是 `-std=gnu89`（C89 加 GNU 擴充）。2022 年 Arnd Bergmann 的 commit `e8c07082a810`（"Kbuild: move to -std=gnu11"，進 v5.18）才把它換成 gnu11。commit message 寫得很直白：Linus 在一次 patch 討論中提議換標準，因為新標準**允許在 `for()` 裡宣告變數**；早年 gcc-5 時代試過一次，因為舊編譯器對 designated initializer 的警告而作罷，等最低編譯器版本拉高後才成功。換句話說，**v5.18 之前的 kernel 程式碼不能寫 `for (int i = 0; ...)`**，你讀舊版程式碼時會看到變數一律宣告在區塊開頭，這是語言版本的限制，不是風格偏好。（`git show e8c07082a810` 自己讀一次，這是 [Ch 37](./37-git-archaeology.md) 考古手法的預習。）

為什麼 kernel 不乖乖寫 ISO C？因為它要的東西 ISO C 給不了：

- **巨集要像函式一樣安全**：參數只求值一次、能宣告區域變數、能回傳值 → statement expression + `typeof`
- **精確控制記憶體佈局與連結**：封包格式不能有 padding、某些函式要放進會被丟掉的 section → `__attribute__`
- **把編譯期能算的事在編譯期做完**：常數走常數路徑、變數走執行期路徑 → `__builtin_constant_p`
- **改 machine code 自己**：static key 在執行期把 `nop` 改成 `jmp` → `asm goto`

## 先建立直覺

把 GNU C 擴充想成「kernel 方言裡的外來語」。你讀日文時看到片假名，不需要懂它的語源，只要知道「這是外來語，意思大概是 X」。讀 kernel 時看到這些語法，先歸類，再翻譯：

```
你看到的                       它屬於哪一類         讀碼時翻成
---------------------------   -----------------   ---------------------------------
({ int t = f(x); t * 2; })    statement expr      「一個區塊，值是最後一個運算式」
typeof(x) / __auto_type       型別推導            「和 x 同型別」
__attribute__((packed))       屬性（佈局）         「不准插 padding」
__attribute__((section(".x")))屬性（連結）         「放進叫 .x 的 section」
__attribute__((cleanup(f)))   屬性（生命週期）     「離開 scope 時呼叫 f」
char data[];  struct_size()   flexible array      「struct 後面黏一段變長陣列」
__builtin_constant_p(x)       編譯器內建函式       「x 在編譯期是常數嗎？」
__builtin_expect(x, 0)        編譯器內建函式       「x 通常是假的」（= unlikely）
case 'a' ... 'z':             語法擴充            「a 到 z 的每一個值」
asm goto(... : l_yes)         inline asm 擴充     「組語可以直接跳到 C label」
```

kernel 幾乎從不直接寫 `__attribute__((...))`，而是包成短巨集（`__packed`、`__aligned(x)`、`__section(s)`、`__cold`），集中定義在 `include/linux/compiler_attributes.h` 和 `compiler_types.h`。所以讀碼的第一個反射是：**看到兩個底線開頭、你不認得的小寫字，先去這兩個檔案找**。

> 如果你對巨集展開還不熟，先回看 [Ch 13 — 前置處理器基礎](./13-preprocessor-basics.md) 的「展開來看」一節；本章很多例子要靠 `gcc -E` 看展開結果。

## Statement expression：`({ ... })`

### 它解決什麼問題

ISO C 的巨集只是文字替換。經典的 `MAX` 巨集會把參數求值兩次：

```c
#define MAX_NAIVE(a, b)  ((a) > (b) ? (a) : (b))
```

`MAX_NAIVE(i++, j)` 展開後 `i++` 出現兩次。GNU C 的 statement expression 讓你在運算式裡塞一整個區塊，**區塊裡最後一個運算式的值就是整個 `({ })` 的值**：

```c
/* stmt_expr.c */
#include <stdio.h>

#define MAX_NAIVE(a, b)  ((a) > (b) ? (a) : (b))

#define MAX_SAFE(a, b) ({            \
	typeof(a) _a = (a);          \
	typeof(b) _b = (b);          \
	_a > _b ? _a : _b; })

#define MAX_AUTO(a, b) ({            \
	__auto_type _a = (a);        \
	__auto_type _b = (b);        \
	_a > _b ? _a : _b; })

static int calls;
static int next(void) { return ++calls * 10; }

int main(void)
{
	int i = 5, j = 3;
	int m = MAX_NAIVE(i++, j);
	printf("naive: m=%d i=%d\n", m, i);

	i = 5;
	m = MAX_SAFE(i++, j);
	printf("safe : m=%d i=%d\n", m, i);

	calls = 0;
	m = MAX_NAIVE(next(), 0);
	printf("naive: m=%d next() called %d times\n", m, calls);

	calls = 0;
	m = MAX_AUTO(next(), 0);
	printf("auto : m=%d next() called %d times\n", m, calls);

	/* statement expression 的值是最後一個運算式 */
	int v = ({ int t = 6; t *= 7; t; });
	printf("stmt-expr value: %d\n", v);
	return 0;
}
```

```
$ gcc -std=gnu11 -Wall -O2 stmt_expr.c -o se && ./se
naive: m=6 i=7
safe : m=5 i=6
naive: m=20 next() called 2 times
auto : m=10 next() called 1 times
stmt-expr value: 42
```

clang 14 跑出來一模一樣。注意 naive 版的兩個 bug：`i` 被加了兩次（`m=6 i=7`），`next()` 被呼叫兩次所以回傳的是第二次的 20，而不是第一次的 10。

同一個檔案改用 ISO C 編：

```
$ gcc -std=c11 -pedantic -c stmt_expr.c
stmt_expr.c:6:9: warning: implicit declaration of function ‘typeof’ [-Wimplicit-function-declaration]
    6 |         typeof(a) _a = (a);          \
...
stmt_expr.c:6:19: error: expected ‘;’ before ‘_a’
```

在 `-std=c11` 底下 `typeof` 不是關鍵字，gcc 把它當成一個沒宣告的函式。這就是 kernel 必須用 `gnu11` 的直接證據。（C23 把 `typeof` 收進標準了，但 kernel v6.18 仍是 gnu11。）

### kernel 裡的真實用例

```c
/* include/linux/minmax.h:85-86（v6.18） */
#define __cmp_once_unique(op, type, x, y, ux, uy) \
	({ type ux = (x); type uy = (y); __cmp(op, ux, uy); })
```

這是 `min_t(type, x, y)`（minmax.h:161）的底層：先把 `x`、`y` 各求值一次存進區域變數，再比較。變數名 `ux`、`uy` 由 `__UNIQUE_ID()` 產生（`include/linux/compiler.h:166`，靠 `__COUNTER__` 拼出唯一名字），避免巢狀使用時名字撞到外層變數——這是 [Ch 14](./14-macro-metaprogramming.md) 講過的 token pasting 的實戰。

`container_of` 也是 statement expression，下一章整章拆它。

**讀碼心法**：看到巨集定義以 `({` 開頭，在腦中把它翻成「一個 inline 函式，回傳最後一行」。它和 `do { } while (0)`（[Ch 8](./08-control-flow-idioms.md)）的差別在於：`do-while(0)` 是**陳述句**，沒有值；`({ })` 是**運算式**，有值。

## `typeof` 與 `__auto_type`

`typeof(expr)` 在編譯期取得 `expr` 的型別，**不會求值** `expr`。`__auto_type` 是 gcc 4.9 起支援的寫法，等同 C++ 的 `auto`：從初始值推導型別。

```c
/* include/linux/minmax.h:307-308（v6.18） */
#define swap(a, b) \
	do { typeof(a) __tmp = (a); (a) = (b); (b) = __tmp; } while (0)
```

`swap` 對 `int`、指標、struct 都能用，因為 `__tmp` 的型別跟著 `a` 走。這是 C 沒有 template 時寫泛型巨集的標準手法。

`__auto_type` 在 kernel 裡還不多——v6.18 排除 `tools/` 後，用 `rg -c "__auto_type"` 數到 21 處，集中在 `minmax.h`、`cleanup.h`、`compiler.h` 這些底層標頭。它比 `typeof(x) v = (x);` 好在 `x` 只寫一次：如果 `x` 本身是一個很長的巨集展開，`typeof(x)` 會讓前置處理器輸出的程式碼大小翻倍。這不是理論問題——Linus 在 2024 年的 commit `3a7e02c040b1`（"minmax: avoid overly complicated constant expressions in VM code"）裡記錄了一個實例：`pageblock_order` 這個 VM 常數因為用 `min_t()` 定義，展開成約 2.5 kB 的前置處理器雜訊，再被包進一個 `max()` 之後，**單一一行陳述式展開成 253 kB**。2024 年 7 月到 11 月間 minmax.h 被 Linus 和 David Laight 連續重寫了十幾個 commit，目標就是壓低展開量（`git log v6.6..v6.18 -- include/linux/minmax.h` 可以看到整串）。

`typeof` 的另一個重要用途是**型別比對**：

```c
/* include/linux/compiler_types.h:537（v6.18） */
#define __same_type(a, b) __builtin_types_compatible_p(typeof(a), typeof(b))
```

`__builtin_types_compatible_p` 是 gcc 內建函式，兩個型別相容回傳 1，否則 0，而且是**編譯期常數**，可以餵給 `static_assert`。Ch 16 的 `container_of` 型別檢查就靠它。

## `__attribute__`：給編譯器的附加指示

attribute 是貼在宣告上的標籤，告訴編譯器「這個東西有特殊性質」。kernel 把它們包成巨集：

| kernel 巨集 | 展開 | 定義位置（v6.18） |
|---|---|---|
| `__aligned(x)` | `__attribute__((__aligned__(x)))` | compiler_attributes.h:33 |
| `__cleanup(func)` | `__attribute__((__cleanup__(func)))` | compiler_attributes.h:76 |
| `__noreturn` | `__attribute__((__noreturn__))` | compiler_attributes.h:262 |
| `__packed` | `__attribute__((__packed__))` | compiler_attributes.h:291 |
| `__section(s)` | `__attribute__((__section__(s)))` | compiler_attributes.h:321 |
| `__always_unused` / `__maybe_unused` | `__attribute__((__unused__))` | compiler_attributes.h:342-343 |
| `__must_check` | `__attribute__((__warn_unused_result__))` | compiler_attributes.h:368 |

注意 kernel 一律用 `__packed__` 這種前後雙底線的拼法，`compiler_attributes.h` 開頭的註解說明了原因：避免和使用者定義的同名巨集衝突（如果有人 `#define packed ...`，`__attribute__((packed))` 就壞了）。

下面這支程式把六個最常見的 attribute 一次跑給你看：

```c
/* attrs.c */
#include <stdio.h>
#include <stdlib.h>
#include <stddef.h>
#include <stdint.h>

/* packed：拿掉 padding，常見於網路封包、硬體暫存器、磁碟格式 */
struct hdr_natural { uint8_t type; uint32_t len; uint16_t id; };
struct hdr_packed  { uint8_t type; uint32_t len; uint16_t id; } __attribute__((packed));

/* aligned：強迫對齊，常見於「一個 cache line 一個物件」 */
struct counter { long v; } __attribute__((aligned(64)));

/* section：把東西放進自訂 section，linker 幫你排成陣列 */
typedef void (*initfn_t)(void);
#define my_initcall(fn) \
	static initfn_t __initcall_##fn __attribute__((used, section("my_initcalls"))) = fn
extern initfn_t __start_my_initcalls[], __stop_my_initcalls[];

static void init_a(void) { puts("  init_a()"); }
static void init_b(void) { puts("  init_b()"); }
my_initcall(init_a);
my_initcall(init_b);

/* unused：告訴編譯器「沒用到是故意的」 */
static int helper(void) __attribute__((unused));
static int helper(void) { return 1; }
static int helper2(void) { return 2; }   /* 沒標，會被 -Wall 罵 */

/* noreturn：這個函式不會回來 */
__attribute__((noreturn)) static void die(const char *msg)
{
	fprintf(stderr, "die: %s\n", msg);
	exit(1);
}

/* cleanup：變數離開 scope 時自動呼叫函式 */
static void free_charp(char **p)
{
	printf("  cleanup: free(%s)\n", *p ? "buf" : "NULL");
	free(*p);
}

static int work(int fail)
{
	char *buf __attribute__((cleanup(free_charp))) = malloc(32);
	if (!buf)
		return -1;
	if (fail) {
		puts("  work: early return");
		return -2;
	}
	puts("  work: normal return");
	return 0;
}

int main(int argc, char **argv)
{
	printf("natural: sizeof=%zu offsetof(len)=%zu\n",
	       sizeof(struct hdr_natural), offsetof(struct hdr_natural, len));
	printf("packed : sizeof=%zu offsetof(len)=%zu\n",
	       sizeof(struct hdr_packed), offsetof(struct hdr_packed, len));
	printf("aligned(64): sizeof=%zu alignof=%zu\n",
	       sizeof(struct counter), _Alignof(struct counter));

	puts("run initcalls from section:");
	for (initfn_t *f = __start_my_initcalls; f < __stop_my_initcalls; f++)
		(*f)();

	puts("cleanup demo:");
	work(1);
	work(0);

	if (argc > 1)
		die(argv[1]);
	return 0;
}
```

```
$ gcc -std=gnu11 -Wall -O2 attrs.c -o attrs && ./attrs
attrs.c:27:12: warning: ‘helper2’ defined but not used [-Wunused-function]
   27 | static int helper2(void) { return 2; }   /* 沒標，會被 -Wall 罵 */
      |            ^~~~~~~
natural: sizeof=12 offsetof(len)=4
packed : sizeof=7 offsetof(len)=1
aligned(64): sizeof=64 alignof=64
run initcalls from section:
  init_b()
  init_a()
cleanup demo:
  work: early return
  cleanup: free(buf)
  work: normal return
  cleanup: free(buf)
$ ./attrs boom; echo "exit=$?"
...
die: boom
exit=1
$ objdump -h attrs | grep my_initcalls
 25 my_initcalls  00000010  0000000000004010  0000000000004010  00003010  2**3
```

逐項解讀：

- **packed**：自然佈局是 12 bytes（`type` 後面補 3 bytes 讓 `len` 對齊到 4），packed 之後變 7 bytes，`len` 落在 offset 1。代價是存取未對齊的 `len` 在某些架構上要拆成多次讀取。
- **aligned(64)**：一個 `long` 被撐到 64 bytes。kernel 用這招避免 false sharing（兩個 CPU 各寫各的變數，卻落在同一條 cache line 上互相打架）。
- **section**：`my_initcalls` section 大小 0x10 = 兩個 8-byte 函式指標。GNU ld 對「名字是合法 C 識別字的 section」會自動生成 `__start_<name>` 和 `__stop_<name>` 兩個符號，所以我們能把它當陣列走訪。
- **注意執行順序**：gcc 版印出 `init_b` 再 `init_a`，**clang 14 版是 `init_a` 再 `init_b`**（實測）。section 內的排列順序不是語言保證的。kernel 的 initcall 要分 0–7 級、由 linker script 依級別排序，就是因為不能依賴這個順序。
- **cleanup**：兩條 return 路徑都觸發了 `free_charp`。這是 Ch 17 `cleanup.h` 的地基。

### kernel 裡的真實用例

**packed**——乙太網路標頭，線上格式就是 14 bytes，一個 byte 都不能多：

```c
/* include/uapi/linux/if_ether.h:173-177（v6.18） */
struct ethhdr {
	unsigned char	h_dest[ETH_ALEN];	/* destination eth addr	*/
	unsigned char	h_source[ETH_ALEN];	/* source ether addr	*/
	__be16		h_proto;		/* packet type ID field	*/
} __attribute__((packed));
```

uapi 標頭要給 user space 用，所以這裡寫完整的 `__attribute__`，而不是 kernel 內部的 `__packed` 巨集。`__be16` 是 Ch 18 會講的 `__bitwise` 型別。

**aligned + section 合體**：

```c
/* include/linux/cache.h:71-75（v6.18） */
#ifndef __cacheline_aligned
#define __cacheline_aligned					\
  __attribute__((__aligned__(SMP_CACHE_BYTES),			\
		 __section__(".data..cacheline_aligned")))
#endif /* __cacheline_aligned */
```

**section + 釋放**——`__init`：

```c
/* include/linux/init.h:45-46（v6.18） */
#define __init		__section(".init.text") __cold __latent_entropy	\
						__no_kstack_erase
```

標了 `__init` 的函式被放進 `.init.text`，開機完成後 kernel 把整個 section 的記憶體還給 page allocator。Ch 18 會在 QEMU 裡實際看這件事發生。

**initcall 的 section 陣列**——和我們的 `my_initcall` 是同一個招式：

```c
/* include/linux/init.h:262-264（v6.18），!CONFIG_HAVE_ARCH_PREL32_RELOCATIONS 分支 */
#define ____define_initcall(fn, __unused, __name, __sec)	\
	static initcall_t __name __used 			\
		__attribute__((__section__(__sec))) = fn;
```

x86_64 實際走的是上方 init.h:254-260 的 PREL32 分支（用 inline asm 存 32-bit 相對位移、省一半空間），但概念完全一樣：每個 `module_init`/`device_initcall` 在 `.initcallN` section 留一個條目，`include/asm-generic/vmlinux.lds.h:927` 起的 `INIT_CALLS` linker script 把它們依級別排好。

**noreturn**：

```c
/* kernel/exit.c:897（v6.18） */
void __noreturn do_exit(long code)
```

讀碼時看到 `__noreturn`，就知道呼叫它之後的程式碼不會執行。編譯器也知道，所以不會對呼叫點之後「缺少 return」發出警告。

## Zero-length array、flexible array member 與 `struct_size()`

### 從 `[0]` 到 `[]`

一個 struct 後面黏一段長度在執行期才知道的陣列，是系統程式的常見需求（封包、檔案 handle、訊息）。歷史上有三種寫法：

```
寫法                      出處              現況（v6.18）
char data[1];             ISO C89 hack     棄用：sizeof 多算一格、越界檢查失效
char data[0];             GNU 擴充          棄用中：逐步被換掉
char data[];              C99 flexible     標準寫法
```

`Documentation/process/deprecated.rst:237` 起的「Zero-length and one-element arrays」一節明說：一律用 flexible array member。換掉的工作是 Gustavo A. R. Silva 等人從 2020 年前後開始的長期清理，目的是讓 `-fstrict-flex-arrays` 和 `FORTIFY_SOURCE` 能正確做越界檢查。

### `struct_size()`：防溢位的大小計算

配置一個帶 flexible array 的 struct，直覺寫法是 `sizeof(*p) + n * sizeof(p->data[0])`。問題是 `n` 如果來自 user space，乘法可能溢位，算出一個很小的大小，之後的寫入就衝出 buffer。kernel 的解法：

```c
/* include/linux/overflow.h:440-443（v6.18） */
#define struct_size(p, member, count)					\
	__builtin_choose_expr(__is_constexpr(count),			\
		sizeof(*(p)) + flex_array_size(p, member, count),	\
		size_add(sizeof(*(p)), flex_array_size(p, member, count)))
```

又是一層 GNU 擴充：`__builtin_choose_expr` 是編譯期的三元運算子（`count` 是常數就直接算，不是就走 `size_add`/`size_mul`，溢位時飽和到 `SIZE_MAX`）。`SIZE_MAX` 傳給 `kmalloc` 必然失敗回傳 NULL，於是溢位被轉換成一個「配置失敗」的正常錯誤路徑。

真實用例：

```c
/* include/linux/fs.h:1255-1259（v6.18） */
struct file_handle {
	__u32 handle_bytes;
	int handle_type;
	/* file identifier */
	unsigned char f_handle[] __counted_by(handle_bytes);
};

/* fs/fhandle.c:49-50（v6.18） */
	handle = kzalloc(struct_size(handle, f_handle, f_handle.handle_bytes),
			 GFP_KERNEL);
```

`__counted_by(handle_bytes)`（`compiler_types.h:368`）是更新的 attribute：告訴編譯器「這個 flexible array 的長度存在 `handle_bytes` 欄位」，讓 `-fsanitize=bounds` 和 FORTIFY 能在執行期檢查越界。編譯器不支援時它展開成空的（compiler_types.h:370），所以讀碼時可以把它當成一段**機器可讀的註解**。

user-space 重現溢位情境：

```c
/* flex.c（節錄關鍵部分；完整程式見本章「動手練習」） */
static size_t size_mul(size_t a, size_t b)
{
	size_t r;
	if (__builtin_mul_overflow(a, b, &r))
		return SIZE_MAX;            /* 飽和：溢位就給 SIZE_MAX，讓 malloc 失敗 */
	return r;
}
static size_t size_add(size_t a, size_t b)
{
	size_t r;
	if (__builtin_add_overflow(a, b, &r))
		return SIZE_MAX;
	return r;
}
#define struct_size(p, member, count) \
	size_add(sizeof(*(p)), size_mul(count, sizeof(*(p)->member)))

struct msg_old { uint32_t len; char data[0]; };   /* GNU zero-length array（舊寫法） */
struct msg     { uint32_t len; uint64_t data[]; }; /* C99 flexible array member */
```

```
$ gcc -std=gnu11 -Wall -O2 flex.c -o flex && ./flex
sizeof(struct msg_old)=4 sizeof(struct msg)=8
struct_size(m, data, 4)      = 40
naive sizeof + huge*8        = 0
struct_size(m, data, huge)   = 18446744073709551615 (SIZE_MAX=18446744073709551615)
m->data[3]=9
malloc(struct_size(huge)) = (nil)
```

`huge = SIZE_MAX / 4`，乘 8 之後是 `0xFFFFFFFFFFFFFFF8`，再加 `sizeof(struct msg)` 的 8 剛好繞回 **0**。naive 寫法會拿 0 去配置，接著往裡面寫 `huge` 個元素——典型的 heap overflow。`struct_size` 版本給出 `SIZE_MAX`，`malloc` 回 NULL。gcc 甚至在編譯期就看出 `malloc(SIZE_MAX)` 不可能成功，丟出 `-Walloc-size-larger-than=` 警告（實測）。

另外注意 `sizeof(struct msg)` 是 8 而不是 4：flexible array 本身不佔空間，但 struct 的對齊要配合 `uint64_t`，所以 `len` 後面有 4 bytes padding。這是「`sizeof(*p)` 不等於所有固定欄位大小總和」的一個常見坑。

## 編譯器內建函式：`__builtin_expect`、`__builtin_constant_p`

```c
/* include/linux/compiler.h:76-77（v6.18） */
# define likely(x)	__builtin_expect(!!(x), 1)
# define unlikely(x)	__builtin_expect(!!(x), 0)
```

`__builtin_expect(e, v)` 的值就是 `e`，語意不變，只是告訴編譯器「`e` 通常等於 `v`」，讓它把常走的路徑排成 fall-through、少一次跳躍。`!!(x)` 把任意非零值正規化成 1，因為比對的對象是 `1` 或 `0`。Ch 18 會用 objdump 看它實際改變了什麼。

`__builtin_constant_p(e)`：`e` 在編譯期能被證明是常數就回傳 1，否則 0。kernel 用它寫「常數走編譯期、變數走執行期」的雙路巨集：

```c
/* include/linux/log2.h:156-164（v6.18） */
#define ilog2(n) \
( \
	__builtin_constant_p(n) ?	\
	((n) < 2 ? 0 :			\
	 63 - __builtin_clzll(n)) :	\
	(sizeof(n) <= 4) ?		\
	__ilog2_u32(n) :		\
	__ilog2_u64(n)			\
 )
```

常數分支整段都能在編譯期算完，所以 `ilog2(4096)` 可以拿去初始化全域變數；變數分支呼叫 arch 最佳化的函式（x86 上是 `bsr` 指令）。

```c
/* builtins.c（節錄） */
static int ilog2_runtime(unsigned long n) { return 63 - __builtin_clzl(n); }
#define ilog2(n) (__builtin_constant_p(n) ? \
	((n) < 2 ? 0 : 63 - __builtin_clzll(n)) : ilog2_runtime(n))

/* 全域初始化必須是常數運算式：__builtin_constant_p 分支讓 ilog2(4096) 可以放這裡 */
static int page_shift = ilog2(4096);
```

```
$ gcc -std=gnu11 -Wall -O2 builtins.c -o builtins && ./builtins
__builtin_constant_p(4096) = 1
__builtin_constant_p(n)    = 0
page_shift = 12, ilog2(n) = 10
'7' -> digit, 'q' -> letter, '#' -> other
likely() does not change semantics: still a plain if
likely(42) = 1
```

`4096 = 2^12`，所以 `page_shift = 12`；`n = argc * 1024 = 1024 = 2^10`。

**認識論提醒**：`__builtin_constant_p` 的結果**取決於最佳化等級**。在 `-O0` 下，很多「人看起來是常數」的東西（例如經過 inline 函式傳進來的常數參數）編譯器不會證明，回傳 0。kernel 一律用 `-O2`（或 `-Os`）編，所以 kernel 程式碼可以依賴 inline 之後的常數傳播；你在 user space 用 `-O0` 試時結果可能不同。

## case range：`case 'a' ... 'z':`

```c
/* kernel/printk/printk.c:2166（v6.18） */
		case '0' ... '7':
			if (level && *level == LOGLEVEL_DEFAULT)
				*level = kern_level - '0';
			break;
```

這是 printk 解析 `KERN_ERR`（`"\0013"`）這類 log level 前綴的地方。`'0' ... '7'` 是 GNU 擴充，等同八個 `case`。**`...` 前後要有空格**：`case 1...5:` 會被 tokenizer 讀成浮點數 `1.` 接 `.5`，編不過。v6.18 裡用 `rg -c "case [^:]+ \.\.\. "` 粗估約 3600 處，非常常見。

上面 `builtins.c` 的 `classify()` 示範了它和多個 case 疊加的寫法：

```c
	switch (c) {
	case '0' ... '9':      return "digit";
	case 'a' ... 'z':
	case 'A' ... 'Z':      return "letter";
	default:               return "other";
	}
```

## labels as values 與 computed goto：`&&label`、`goto *p`

GNU C 允許用 `&&label` 取得一個標籤的位址（型別是 `void *`），再用 `goto *p` 跳過去。直譯器用它取代 `switch` 分派：每個 opcode 處理完直接跳到下一個 opcode 的處理碼，不回到迴圈頂端。[Ch 8](./08-control-flow-idioms.md) 讀 Lua 的 `vmdispatch` 時已經碰過它，[練習 B](./practice-b-decode-userspace-snippets.md) 第 1 題也是從這裡出題的。這裡補上它在 kernel 裡的樣子。

先看一個最小可跑的版本：

```c
/* cg.c */
#include <stdio.h>

enum { OP_INC, OP_DBL, OP_PRINT, OP_HALT };

static int run(const unsigned char *code)
{
	static const void *jumptable[] = {
		[OP_INC]   = &&do_inc,
		[OP_DBL]   = &&do_dbl,
		[OP_PRINT] = &&do_print,
		[OP_HALT]  = &&do_halt,
	};
	int acc = 0;
#define NEXT() goto *jumptable[*code++]
	NEXT();
do_inc:   acc += 1;               NEXT();
do_dbl:   acc *= 2;               NEXT();
do_print: printf("acc=%d\n", acc); NEXT();
do_halt:  return acc;
#undef NEXT
}

int main(void)
{
	const unsigned char prog[] = { OP_INC, OP_DBL, OP_DBL, OP_PRINT, OP_INC, OP_PRINT, OP_HALT };
	printf("result=%d\n", run(prog));
	return 0;
}
```

```
$ gcc -Wall -O2 cg.c -o cg && ./cg
acc=4
acc=5
result=5
$ gcc -Wall -std=c11 -pedantic -c cg.c -o /dev/null
cg.c: In function ‘run’:
cg.c:8:17: warning: taking the address of a label is non-standard [-Wpedantic]
```

`-pedantic` 的警告說明了它的身分：這不是標準 C。kernel 用 `-std=gnu11` 編譯，所以可以合法使用。

kernel 裡最重要的用例是 eBPF 直譯器：

```c
/* kernel/bpf/core.c:1708-1736（v6.18） */
static u64 ___bpf_prog_run(u64 *regs, const struct bpf_insn *insn)
{
#define BPF_INSN_2_LBL(x, y)    [BPF_##x | BPF_##y] = &&x##_##y
#define BPF_INSN_3_LBL(x, y, z) [BPF_##x | BPF_##y | BPF_##z] = &&x##_##y##_##z
	static const void * const jumptable[256] __annotate_jump_table = {
		[0 ... 255] = &&default_label,
		/* Now overwrite non-defaults ... */
		BPF_INSN_MAP(BPF_INSN_2_LBL, BPF_INSN_3_LBL),
		/* Non-UAPI available opcodes. */
		[BPF_JMP | BPF_CALL_ARGS] = &&JMP_CALL_ARGS,
		...
	};
#undef BPF_INSN_3_LBL
#undef BPF_INSN_2_LBL
	u32 tail_call_cnt = 0;

#define CONT	 ({ insn++; goto select_insn; })
#define CONT_JMP ({ insn++; goto select_insn; })

select_insn:
	goto *jumptable[insn->code];
```

這十幾行幾乎把本章的擴充全部用上了：

- `[0 ... 255] = &&default_label`：designated initializer 的 **range** 形式（和上一節的 case range 同一種語法），先把 256 格全部填成「未知 opcode」。
- `BPF_INSN_2_LBL` 用 `##` 把 `ALU`、`ADD`、`X` 拼成標籤名 `ALU_ADD_X`；`BPF_INSN_MAP` 是一個 X-macro（[Ch 14](./14-macro-metaprogramming.md)），對每個合法 opcode 呼叫一次這個巨集，覆寫預設值。
- `CONT` 是 statement expression，`insn++` 之後跳回 `select_insn`，再由 `goto *jumptable[...]` 分派。

讀碼時的後果：**整個 `kernel/bpf/core.c` 裡一次都找不到 `ALU_ADD_X` 這個字串**。不只表格的那一頭是拼出來的，標籤本身也是：

```c
/* kernel/bpf/core.c:1764-1770、1777（v6.18） */
#define ALU(OPCODE, OP)					\
	ALU64_##OPCODE##_X:				\
		DST = DST OP SRC;			\
		CONT;					\
	ALU_##OPCODE##_X:				\
		DST = (u32) DST OP (u32) SRC;		\
		CONT;					\
	...
	ALU(ADD,  +)
```

`ALU(ADD, +)` 這一行展開後才出現 `ALU64_ADD_X:`、`ALU_ADD_X:` 等四個標籤和它們的處理碼。所以要找「BPF 的 ADD 指令怎麼執行」，正確的搜尋是拼接前的片段：`rg -n 'ALU\(ADD' kernel/bpf/core.c` 找到第 1777 行，再回頭讀 `#define ALU`。不然就像 [Ch 50](./50-compiler-as-reading-tool.md) 那樣，用 `make O=../build kernel/bpf/core.i` 看展開結果。這是 Ch 14「巨集生成的名字 grep 不到」問題的又一個實例：表格和標籤兩頭都是拼出來的。

## `asm goto`：只要認得

```c
/* arch/x86/include/asm/jump_label.h:34-42（v6.18） */
static __always_inline bool arch_static_branch(struct static_key * const key, const bool branch)
{
	asm goto(ARCH_STATIC_BRANCH_ASM("%c0 + %c1", "%l[l_yes]")
		: :  "i" (key), "i" (branch) : : l_yes);

	return false;
l_yes:
	return true;
}
```

`asm goto` 讓 inline 組語可以跳到 C 的 label（`l_yes`）。這段的組語是一個 5-byte `nop`，外加一筆記錄寫進 `__jump_table` section。平常 CPU 執行 `nop` 然後 `return false`；有人呼叫 `static_branch_enable()` 時，kernel 在執行期把這個 `nop` **改寫**成 `jmp l_yes`。效果是一個「沒有任何比較指令的 if」，用在 tracepoint 這種預設關閉、但每秒可能經過幾百萬次的地方。

讀碼時你需要的只是：看到 `static_branch_likely(&key)` / `static_branch_unlikely(&key)` 時，知道它是**執行期可切換、但讀取成本接近零的布林值**，底層是 `asm goto`。細節留給 [Ch 50](./50-compiler-as-reading-tool.md) 用 objdump 看。v6.18 裡直接寫 `asm goto` 的地方不到一百處（粗估 93），幾乎都在 arch 目錄。

## clang 也是 GNU C

kernel 現在可以完整用 clang/LLVM 編（`make LLVM=1`，見 `Documentation/kbuild/llvm.rst`；主流架構大約在 2018–2019 年前後達到可用，Android 與 ChromeOS 的 kernel 很早就改用 clang 出貨）。clang 刻意實作了 kernel 用到的絕大多數 GNU 擴充：本章每個 user-space 範例在 clang 14 上都編過、輸出相同（除了 section 內的排列順序）。差異主要在邊角：

- 某些 attribute 只有一邊支援，`compiler_attributes.h` 用 `__has_attribute()` 檢查，不支援時定義成空的，並在註解標明原因，例如 compiler_attributes.h:86-87 的「Optional: only supported since gcc >= 9」「Optional: not supported by clang」。
- 診斷不同：Ch 17 會看到 gcc 11 對「goto 跳過 cleanup 變數的初始化」不吭聲，clang 直接報錯。
- 版本門檻：`Documentation/process/changes.rst:32-33` 列出 v6.18 的最低需求是 GCC 8.1、Clang/LLVM 15.0.0。本課環境的 clang 14 **不夠編 v6.18 kernel**，但跑本章的 user-space 範例沒問題。

讀碼時遇到 `CONFIG_CC_IS_CLANG`（`init/Kconfig:27`）或 `compiler-clang.h`，就是在處理兩邊的差異。

## 對比與取捨

| 需求 | ISO C 做法 | GNU C 做法 | kernel 選擇的理由 |
|---|---|---|---|
| 安全的泛型巨集 | static inline 函式（但沒有泛型） | `({ })` + `typeof` | inline 函式要寫死型別；`_Generic`（C11）可以分派但寫起來冗長 |
| 結構體不要 padding | 手動逐 byte 組裝 | `__packed` | 直接對應線上/硬體格式，讀碼也直觀 |
| 變長尾巴 | `data[1]` hack | `data[0]` → C99 `data[]` | 現在用標準的 `[]`，再加 `__counted_by` |
| 開機後丟掉的程式碼 | 不可能 | `__section(".init.text")` | 省下數 MB 常駐記憶體 |
| 常數/變數雙路 | 寫兩個 API | `__builtin_constant_p` | 呼叫端不用管參數是不是常數 |

## 踩雷集錦

1. **以為 `({ })` 是 C 標準**：把 kernel 巨集抄到 MSVC 或 `-std=c11 -pedantic` 專案會直接編不過。user-space 專案要用就明確加 `-std=gnu11`。
2. **以為 `typeof(x++)` 會讓 x 加一**：`typeof` 不求值。`typeof(f())` 也不會呼叫 `f`。但**變長陣列（VLA）型別**是例外，`typeof` 一個 VLA 會求值它的大小運算式——kernel 禁止 stack 上的 VLA（`scripts/Makefile.extrawarn:58-65` 用 `-Wvla-larger-than=1` 擋），所以 kernel 裡不太會碰到。
3. **以為 `__packed` 是免費的**：在不支援非對齊存取的架構，存取 packed 成員會被拆成逐 byte 讀取；對 packed 成員取位址再當一般指標用，可能觸發 alignment fault。gcc 會對這種情況警告 `-Waddress-of-packed-member`。
4. **以為 section 內的順序可靠**：本章實測 gcc 和 clang 對同一份 `my_initcall` 排出相反順序。要順序就要靠 linker script 排序或名字編號。
5. **以為 `likely()` 會讓程式更快**：它只影響程式碼排列。猜錯的 `likely` 會讓常走路徑變成跳躍路徑，反而更慢。kernel 有 `CONFIG_PROFILE_ANNOTATED_BRANCHES` 專門統計 likely/unlikely 猜錯率（Ch 18）。
6. **以為 `sizeof(struct with flex array)` 等於固定欄位大小總和**：padding 可能讓它更大（`struct msg` 實測是 8 不是 4）。永遠用 `struct_size()` 算，不要手算。

## 進階：再往深一層

- **`_Generic` 和 `typeof` 合作**：Ch 16 的 `container_of_const` 用 `_Generic(ptr, const typeof(*(ptr)) *: ..., default: ...)` 依「指標是否指向 const」選擇回傳型別。這是 C11 標準的泛型選擇，配上 GNU 的 `typeof`，可以做到接近 C++ overload 的效果。
- **`__builtin_choose_expr` vs `?:`**：`?:` 的結果型別由兩邊共同決定（要相容，還會做 usual arithmetic conversion）；`__builtin_choose_expr` 的結果**就是被選中那一邊的型別與值**，沒選中的那一邊不會被求值。`struct_size` 用它，是為了讓常數分支保持「整數常數運算式」的身分（可以用在 `static_assert`、陣列大小），變數分支才走 `size_add`。
- **`__is_constexpr`**（`include/linux/compiler.h:335`）：判斷一個運算式是不是 ISO C 意義下的「整數常數運算式」（比 `__builtin_constant_p` 更嚴格，後者可能因為最佳化而回傳 1）。它的實作是一個利用 null pointer constant 規則的著名技巧，有興趣可以讀那幾行，展開來看會讓你對 C 型別規則有新的認識。
- **想看任何巨集最終長什麼樣**：在 `~/code_reading/linux` 下 `make O=../build fs/pipe.i`，打開 `../build/fs/pipe.i` 搜尋你要的函式。所有 GNU 擴充都還在，但 kernel 巨集全部展開了。

## 動手練習

1. 把 `stmt_expr.c` 的 `MAX_SAFE` 改成 `MAX_SAFE(a, b) ((typeof(a))(a) > (b) ? (a) : (b))`（拿掉 `({ })`），重跑，確認 `i++` 的 bug 回來了。
2. 在 `attrs.c` 裡加第三個 `my_initcall(init_c)`，分別用 gcc 和 clang 編，記錄順序。然後想辦法讓順序固定（提示：把 section 名字改成 `my_initcalls.1`、`my_initcalls.2`，再寫一個最小 linker script 用 `SORT()`；或者改成在名字裡編號後自己排序）。
3. 完整的 `flex.c`（本章只節錄）：

```c
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
/* size_mul / size_add / struct_size / struct msg_old / struct msg 同上文 */

int main(void)
{
	printf("sizeof(struct msg_old)=%zu sizeof(struct msg)=%zu\n",
	       sizeof(struct msg_old), sizeof(struct msg));

	struct msg *m = NULL;
	size_t n = 4;
	printf("struct_size(m, data, 4)      = %zu\n", struct_size(m, data, n));

	size_t huge = SIZE_MAX / 4;
	printf("naive sizeof + huge*8        = %zu\n", sizeof(*m) + huge * sizeof(m->data[0]));
	printf("struct_size(m, data, huge)   = %zu (SIZE_MAX=%zu)\n",
	       struct_size(m, data, huge), (size_t)SIZE_MAX);

	m = malloc(struct_size(m, data, n));
	m->len = n;
	for (uint32_t i = 0; i < m->len; i++)
		m->data[i] = i * i;
	printf("m->data[3]=%lu\n", (unsigned long)m->data[3]);
	free(m);

	printf("malloc(struct_size(huge)) = %p\n", malloc(struct_size(m, data, huge)));
	return 0;
}
```

   跑起來之後，把 `huge` 改成 `SIZE_MAX / 8`，算算看 naive 版會得到多少，再跑一次驗證。
4. 在 v6.18 tree 用 `rg -n "\[0\];$" include/uapi/ | head` 找幾個還沒換成 `[]` 的 zero-length array，用 `git log -S` 查查看同檔案其他欄位是什麼時候被換掉的（[Ch 37](./37-git-archaeology.md) 的考古手法預習）。

## 本章重點整理

- kernel 用 `-std=gnu11`（Makefile:587，v5.18 起；之前是 gnu89），GNU 擴充是政策允許的，不是例外。
- `({ })` = 有值的區塊；`typeof`/`__auto_type` = 型別推導；兩者合起來讓巨集只求值一次參數。
- attribute 被包成 `__packed`、`__aligned`、`__section`、`__cold`、`__noreturn`、`__cleanup` 等巨集，去 `compiler_attributes.h` 查。
- flexible array 用 `[]`，大小一律用 `struct_size()`，它會把溢位飽和成 `SIZE_MAX` 讓配置失敗。
- `__builtin_constant_p` 讓同一個巨集對常數和變數走不同路徑；結果和最佳化等級有關。
- `asm goto` 是 static key 的底層，認得就好。

## 自我檢核

- [ ] 不看筆記，能解釋為什麼 `MAX_NAIVE(next(), 0)` 回傳 20 而不是 10。
- [ ] 有人問你「kernel 為什麼不能用 `-std=c11` 編」，你能舉出至少三個具體的語法例子。
- [ ] 能說出 `data[0]`、`data[1]`、`data[]` 三者的差別，以及 kernel 為什麼要花好幾年把前兩者換掉。
- [ ] 能解釋 `struct_size()` 怎麼把一個整數溢位漏洞變成一個普通的 `-ENOMEM`。
- [ ] 看到 `static_branch_unlikely(&foo_key)`，能說出它和 `if (unlikely(foo_enabled))` 在機器碼層級的差別。
- [ ] 知道什麼時候 `__packed` 會讓程式變慢或出錯。

## 延伸閱讀

### 官方文件 / Spec

- **[GCC Manual — Extensions to the C Language Family](https://gcc.gnu.org/onlinedocs/gcc/C-Extensions.html)**
  - **讀哪裡**：「Statements and Declarations in Expressions」「Referring to a Type with typeof」「Arrays of Length Zero」「Case Ranges」「Other Built-in Functions」幾節，各一兩頁。`compiler_attributes.h` 每個巨集上方的註解都附了對應的 GCC 文件 URL，可以從那裡直接點。
  - **能學到什麼**：每個擴充的精確語意與邊界情況（例如 statement expression 裡 `return` 和 `goto` 的行為）。
  - **前提**：會 C；本章讀完再查最有效。
- **`Documentation/process/deprecated.rst`**（v6.18 tree 內，線上版在 docs.kernel.org/process/deprecated.html）
  - **讀哪裡**：「open-coded arithmetic in allocator arguments」與「Zero-length and one-element arrays」兩節。
  - **能學到什麼**：kernel 官方為什麼要求 `struct_size()` 和 flexible array，以及其他被棄用的 C 寫法清單。讀碼時看到舊寫法，就知道那是待清理的遺留物，而不是另一種慣用法。
- **`Documentation/kbuild/llvm.rst`**（docs.kernel.org/kbuild/llvm.html）
  - **讀哪裡**：開頭到「LLVM Utilities」。
  - **能學到什麼**：怎麼用 clang 編 kernel、哪些架構支援；配合 `Documentation/process/changes.rst` 看最低版本。

### 原始碼（最權威的文件）

- **`include/linux/compiler_attributes.h`、`include/linux/compiler_types.h`、`include/linux/compiler.h`**
  - **讀哪裡**：compiler_attributes.h 整份不到 400 行，照字母排序、每個都有 GCC/clang 文件連結；從頭掃一遍，之後看到不認得的 `__xxx` 就知道去哪裡查。
  - **和本章的關聯**：本章表格的所有巨集都定義在這三個檔案。

### 書籍

- **《Expert C Programming: Deep C Secrets》** — Peter van der Linden（Prentice Hall, 1994）
  - **這本書的定位**：講 ISO C 的暗角（宣告語法、陣列與指標、連結），沒有涵蓋 GNU 擴充，年代也老，但「C 的型別系統和連結模型怎麼運作」的部分至今適用。
  - **讀哪幾章**：第 3 章（解讀宣告）、第 4–5 章（陣列與指標、連結）。讀完再回來看 `typeof` 和 section attribute，會更清楚它們在補 ISO C 的哪一塊。

GNU 擴充是 kernel 方言的文法，接下來看用這套文法寫出來、出現兩萬多次的那一個巨集。

→ [Ch 16 container_of：kernel 最重要的一個巨集](./16-container-of.md)
