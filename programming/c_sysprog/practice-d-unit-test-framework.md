# 練習 D — 迷你 unit test 框架

> **目標**：把 Part 5 的語言機制拼起來，用巨集加函式指標寫一個可以真的拿來用的 C 單元測試框架：`TEST()` 寫完就自動註冊（`constructor` 屬性或連結器區段）、`ASSERT_EQ` 印出檔名行號與原始運算式並依型別格式化（`_Generic`）、fixture 的 setup/teardown、一個測試失敗不影響其他測試（`setjmp`/`longjmp`）、最後輸出統計與正確的結束碼。完成後你驗證了 Ch 27（`#`、`##`、兩層間接）、Ch 28（程式碼產生、section 註冊）、Ch 29–30（函式指標表、以 `void *` 傳遞物件）、Ch 31（集中出口與「離開時誰收拾」）與 Ch 12（非區域跳躍）的實戰能力。

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14，GNU ld 2.38。參考解答以 `-std=gnu11 -Wall -Wextra -fsanitize=address,undefined` 編譯，兩個編譯器皆無警告、ASan/UBSan 無報告。

## 背景與動機

C 沒有反射，也沒有 annotation：Java 的 JUnit 可以掃描所有標了 `@Test` 的方法，Python 的 pytest 可以找出所有 `test_` 開頭的函式，C 的執行檔卻不知道自己有哪些函式。C 的測試框架因此都在回答同一組問題：

1. **怎麼知道有哪些測試？** 手寫一張總表（容易漏）、用外部腳本掃描原始碼產生總表（Unity 的 `generate_test_runner.rb`）、或讓每個測試在 `main` 之前自己報到（snow 與 Criterion 用 `constructor` 屬性或連結器區段）。
2. **怎麼讓失敗訊息有用？** `assert(x == 5)` 失敗只告訴你條件不成立；好的框架要印出「左邊算出來是 4、右邊是 5」，而且整數用十進位、位元遮罩用十六進位、字串比內容、浮點數印足夠的位數。
3. **一個測試失敗或當掉，其他測試怎麼辦？** 標準 `assert` 會 `abort()` 整個程式。框架必須讓「這個測試」提早結束，但下一個測試照跑，而且已配置的 fixture 要釋放。

Linux 核心的 `tools/testing/selftests/kselftest_harness.h` 就是這種框架（提供 `TEST`、`FIXTURE`、`FIXTURE_SETUP`、`TEST_F`、`ASSERT_EQ`），本練習的介面刻意與它相近。做完之後，你讀 Unity、cmocka、Criterion 或 kselftest 的原始碼都會很快。

## 任務規格

### 檔案結構

| 檔案 | 內容 |
|---|---|
| `ut.h` | 使用者 `#include` 的標頭：所有巨集與宣告 |
| `ut.c` | 框架本體：註冊表、斷言後端、`main()` 執行器 |
| `test_*.c` | 使用者的測試檔，任意多個，不需要任何額外登記 |

建置方式：`gcc -std=gnu11 -Wall -Wextra ut.c test_a.c test_b.c -o tests -lm`

### 必須提供的介面

| 介面 | 行為 |
|---|---|
| `TEST(suite, name) { ... }` | 定義並自動註冊一個測試。`suite` 與 `name` 是識別字。 |
| `FIXTURE(fx) { 欄位; };` | 宣告一個 fixture 結構 |
| `FIXTURE_SETUP(fx) { ... }` / `FIXTURE_TEARDOWN(fx) { ... }` | 函式本體中以 `self` 存取 fixture |
| `TEST_F(fx, name) { ... }` | 使用 fixture 的測試；每個 `TEST_F` 都拿到一份**全新**、清為 0 的 fixture |
| `ASSERT_EQ(a, b)` | 不相等時印出訊息並**結束這個測試**（fatal） |
| `EXPECT_EQ(a, b)` | 不相等時印出訊息，**繼續**執行本測試（non-fatal） |
| `ASSERT_TRUE(c)` / `EXPECT_TRUE(c)` | 條件為偽時失敗 |
| `ASSERT_NEAR(a, b, eps)` | `|a - b| > eps` 時失敗 |

### 型別感知的比較規則（依**左運算元**的型別決定）

| 左運算元型別 | 比較方式 | 失敗訊息的格式 |
|---|---|---|
| 有號整數（`char`、`short`、`int`、`long`、`long long`） | 轉 `long long` 後 `==` | 十進位 |
| 無號整數（含 `_Bool`） | 轉 `unsigned long long` 後 `==` | 十進位與十六進位 |
| 浮點數（`float`、`double`、`long double`） | 轉 `long double` 後 `==` | `%.17Lg`（足以區分相鄰的 `double`） |
| `char *`、`const char *` | `strcmp`；兩個 `NULL` 視為相等 | 加引號；`NULL` 印成 `NULL` |
| 其他（指標） | 位址 `==` | `%p` |

### 失敗訊息格式

```
<file>:<line>: Failure: <ASSERT_EQ|EXPECT_EQ|...>(<a 的原始文字>, <b 的原始文字>)
    left : <a 的值>
    right: <b 的值>
```

原始文字必須是**使用者寫的樣子**：`ASSERT_EQ(p, NULL)` 要印出 `NULL`，而不是展開後的 `((void *)0)`。

### 執行器

- 依「檔名、行號」排序後執行，輸出順序必須可重現。
- 每個測試輸出 `[ RUN      ] suite.name`，結束時輸出 `[       OK ]` 或 `[  FAILED  ]`。
- 最後輸出 `[==========] N test(s) ran, P passed, F failed (T ms)`，再列出所有失敗的測試名稱。
- 命令列第一個引數若存在，只執行完整名稱（`suite.name`）**包含**該子字串的測試。
- 全部通過時結束碼為 0，否則為 1。
- fatal 斷言失敗後，若有 fixture，**teardown 仍必須執行**；setup 本身失敗則不執行 teardown。
- 不得有記憶體洩漏（ASan 檢查）。

### 禁止與限制

- 不可在 `ut.c` 中寫死任何測試名稱，也不可要求使用者另外登記。
- 斷言巨集的每個引數只能被求值一次。
- 允許使用 GNU 擴充 `__attribute__((constructor))`、`section`、`used`；不使用 statement expression 也做得到。

## 期望輸出範例

測試檔 `test_math.c`：

```c
#include <limits.h>
#include <string.h>
#include "ut.h"

static int add(int a, int b) { return a + b; }
static unsigned mask_low(unsigned x, int bits) { return x & ((1u << bits) - 1); }

TEST(math, add_small)
{
    ASSERT_EQ(add(2, 3), 5);
    ASSERT_EQ(add(-1, 1), 0);
}

TEST(math, add_wrong_on_purpose)
{
    EXPECT_EQ(add(2, 2), 5);          /* non-fatal: keeps going */
    ASSERT_EQ(add(1, 1), 3);          /* fatal: stops this test only */
    ASSERT_EQ(1, 2);                  /* never reached */
}

TEST(math, unsigned_shows_hex)
{
    ASSERT_EQ(mask_low(0xABCDu, 8), 0xCDu);
    ASSERT_EQ(mask_low(0xABCDu, 4), 0xCCu);
}

TEST(math, floating)
{
    ASSERT_EQ(0.5 + 0.25, 0.75);      /* exact in binary */
    ASSERT_NEAR(0.1 + 0.2, 0.3, 1e-12);
    ASSERT_EQ(0.1 + 0.2, 0.3);        /* fails: see Ch 25 */
}

TEST(strings, compare_contents_not_pointers)
{
    char buf[8];
    strcpy(buf, "abc");
    ASSERT_EQ(buf, "abc");            /* char * branch: strcmp */
    ASSERT_EQ((const char *) NULL, (const char *) NULL);
    ASSERT_EQ("abc", "abd");
}
```

測試檔 `test_stack.c`（含 fixture）：

```c
#include <stdio.h>
#include <stdlib.h>
#include "ut.h"

/* ---- code under test: a bounded int stack ---- */
typedef struct { int *items; int top, cap; } Stack;
static Stack *stack_new(int cap)
{
    Stack *s = malloc(sizeof *s);
    s->items = malloc((size_t) cap * sizeof *s->items);
    s->top = 0, s->cap = cap;
    return s;
}
static int stack_push(Stack *s, int v) { if (s->top == s->cap) return -1; s->items[s->top++] = v; return 0; }
static int stack_pop(Stack *s, int *out) { if (!s->top) return -1; *out = s->items[--s->top]; return 0; }
static void stack_free(Stack *s) { free(s->items); free(s); }

/* ---- fixture: every TEST_F gets a fresh stack of capacity 2 ---- */
FIXTURE(stack) {
    Stack *s;
    int setups;
};

FIXTURE_SETUP(stack)
{
    self->s = stack_new(2);
    self->setups++;
}

FIXTURE_TEARDOWN(stack)
{
    printf("    (teardown: freeing stack with %d item(s))\n", self->s->top);
    stack_free(self->s);
}

TEST_F(stack, starts_empty)
{
    int v = 0;
    ASSERT_EQ(self->s->top, 0);
    ASSERT_EQ(stack_pop(self->s, &v), -1);
    ASSERT_EQ(self->setups, 1);       /* fresh fixture each time */
}

TEST_F(stack, push_pop_lifo)
{
    int v = 0;
    ASSERT_EQ(stack_push(self->s, 1), 0);
    ASSERT_EQ(stack_push(self->s, 2), 0);
    ASSERT_EQ(stack_pop(self->s, &v), 0);
    ASSERT_EQ(v, 2);
}

TEST_F(stack, overflow_is_reported)
{
    ASSERT_EQ(stack_push(self->s, 1), 0);
    ASSERT_EQ(stack_push(self->s, 2), 0);
    ASSERT_EQ(stack_push(self->s, 3), 0);   /* fails: capacity is 2; teardown still runs */
}

TEST(pointers, identity)
{
    int x = 0, y = 0;
    int *p = &x;
    ASSERT_EQ(p, &x);
    ASSERT_TRUE(p != &y);
    ASSERT_EQ((void *) NULL, (void *) p);
}
```

執行（位址與時間每次不同，以 `ADDR`、`T` 代替）：

```
$ gcc -std=gnu11 -Wall -Wextra -g -fsanitize=address,undefined ut.c test_math.c test_stack.c -o tests -lm
$ ./tests
[ RUN      ] math.add_small
[       OK ] math.add_small
[ RUN      ] math.add_wrong_on_purpose
test_math.c:16: Failure: EXPECT_EQ(add(2, 2), 5)
    left : 4
    right: 5
test_math.c:17: Failure: ASSERT_EQ(add(1, 1), 3)
    left : 2
    right: 3
[  FAILED  ] math.add_wrong_on_purpose
[ RUN      ] math.unsigned_shows_hex
test_math.c:24: Failure: ASSERT_EQ(mask_low(0xABCDu, 4), 0xCCu)
    left : 13 (0xd)
    right: 204 (0xcc)
[  FAILED  ] math.unsigned_shows_hex
[ RUN      ] math.floating
test_math.c:31: Failure: ASSERT_EQ(0.1 + 0.2, 0.3)
    left : 0.30000000000000004
    right: 0.29999999999999999
[  FAILED  ] math.floating
[ RUN      ] strings.compare_contents_not_pointers
test_math.c:40: Failure: ASSERT_EQ("abc", "abd")
    left : "abc"
    right: "abd"
[  FAILED  ] strings.compare_contents_not_pointers
[ RUN      ] stack.starts_empty
    (teardown: freeing stack with 0 item(s))
[       OK ] stack.starts_empty
[ RUN      ] stack.push_pop_lifo
    (teardown: freeing stack with 1 item(s))
[       OK ] stack.push_pop_lifo
[ RUN      ] stack.overflow_is_reported
test_stack.c:57: Failure: ASSERT_EQ(stack_push(self->s, 3), 0)
    left : -1
    right: 0
    (teardown: freeing stack with 2 item(s))
[  FAILED  ] stack.overflow_is_reported
[ RUN      ] pointers.identity
test_stack.c:66: Failure: ASSERT_EQ((void *) NULL, (void *) p)
    left : (nil)
    right: ADDR
[  FAILED  ] pointers.identity
[==========] 9 test(s) ran, 3 passed, 6 failed (T ms)
[  FAILED  ] math.add_wrong_on_purpose
[  FAILED  ] math.unsigned_shows_hex
[  FAILED  ] math.floating
[  FAILED  ] strings.compare_contents_not_pointers
[  FAILED  ] stack.overflow_is_reported
[  FAILED  ] pointers.identity
```

觀察重點：`add_wrong_on_purpose` 的 `EXPECT_EQ` 失敗後繼續，`ASSERT_EQ` 失敗後停止，第 18 行的 `ASSERT_EQ(1, 2)` 沒有出現；`overflow_is_reported` 失敗後 teardown 照常執行；`0.1 + 0.2` 印出 17 位有效數字才看得出差異（[Ch 25](./25-ieee754.md)）；`buf` 與 `"abc"` 位址不同卻比較成功，因為走的是 `strcmp`。

```
邊界輸入：./tests stack.push           （過濾）
輸出：
[ RUN      ] stack.push_pop_lifo
    (teardown: freeing stack with 1 item(s))
[       OK ] stack.push_pop_lifo
[==========] 1 test(s) ran, 1 passed, 0 failed (T ms)
結束碼：0
```

```
邊界輸入：./tests nomatch              （沒有任何測試符合）
輸出：[==========] 0 test(s) ran, 0 passed, 0 failed (T ms)
結束碼：0
```

```
邊界輸入：./tests stack                 （含一個失敗的測試）
結束碼：1
```

## 如果你卡住了

1. **「`main` 怎麼知道有哪些測試？」** 想想 Ch 28 的 initcall：讓每個 `TEST` 展開成一個資料結構加一個 `__attribute__((constructor))` 函式，constructor 在 `main` 之前由 C runtime 呼叫（[Ch 41](./41-c-runtime.md) 會看到是誰呼叫的），它只做一件事：把資料結構串進全域鏈結串列。另一條路是 `section` 屬性加 `__start_<name>`/`__stop_<name>`。
2. **「`TEST(math, add) { ... }` 後面那對大括號要變成什麼？」** 巨集展開的**最後一段**必須是一個函式宣告子，例如 `static void ut_body_math_add(void *unused)`，使用者寫的 `{ ... }` 就自然成為它的函式本體。在它之前，你可以先產生原型、註冊資料與 constructor。
3. **「fatal 斷言怎麼只結束『這個』測試？」** 斷言可能出現在測試呼叫的輔助函式深處，`return` 只能離開一層。執行器在呼叫測試前 `setjmp`，斷言失敗時 `longjmp` 回來（[Ch 12](./12-setjmp-and-coroutines.md)）。記得 `setjmp` 與 `longjmp` 之間被修改的區域變數要 `volatile`。
4. **「`_Generic` 只能看一個運算式，兩個運算元怎麼辦？」** 不必同時看兩個：用左運算元的型別挑**函式**，把兩個運算元都當引數傳進去，右運算元會依函式的參數型別隱式轉換。另外，非選中的分支仍要通過型別檢查，所以不能寫 `_Generic((a) + (b), ...)`（指標加指標不合法）。
5. **「為什麼印出來的是 `((void *)0)` 而不是 `NULL`？」** 回想 Ch 27 的規則 ②：參數只有與 `#` 相鄰時才不先展開。如果 `ASSERT_EQ` 把 `a` 傳給下一層巨集、在下一層才做 `#a`，`NULL` 早就展開了。字串化要在使用者直接呼叫的那一層做。

## 實作步驟建議

### Step 1：最小的註冊與執行器

先只做 `TEST(suite, name)` 與一個會列出並執行所有測試的 `main()`，測試本體只用 `printf`。驗收：兩個 `.c` 檔各寫兩個測試，不修改 `ut.c` 就能全部跑到。用 `gcc -E -P test_math.c | tail -20` 檢查展開結果。

### Step 2：`ASSERT_EQ` 只支援 `int`，失敗即 `longjmp`

寫一個 `ut_cmp_i(file, line, "a", "b", long long a, long long b)`，失敗時印訊息、遞增失敗計數、`longjmp`。執行器在每個測試前 `setjmp`。驗收：一個測試中有兩個失敗的 `ASSERT_EQ`，只印出第一個；下一個測試照跑。

### Step 3：`_Generic` 型別分派與 `EXPECT_EQ`

把呼叫點資訊打包成一個結構（檔名、行號、巨集名、兩段原始文字、是否 fatal），用 `_Generic` 依左運算元挑選 `ut_cmp_i`/`_u`/`_d`/`_s`/`_p`。加入 non-fatal 版本。驗收：期望輸出範例中 `math.*` 與 `strings.*` 的訊息格式完全一致，包括十六進位與 17 位有效數字。

### Step 4：fixture

`FIXTURE(fx)` 只是 `struct ut_fixture_fx` 的開頭；`FIXTURE_SETUP(fx)` 要產生一個「接收 `void *`、轉呼叫接收 `struct ut_fixture_fx *self` 的函式」的包裝，這樣註冊表裡的函式指標型別才能統一（Ch 29 的手法）。`TEST_F` 把 fixture 大小、setup、teardown 一起放進註冊資料。執行器為每個 `TEST_F` 以 `calloc` 配置新的 fixture，setup → body → teardown → free。驗收：`stack.*` 三個測試的輸出（包括失敗後仍印出 teardown 訊息）。

### Step 5：整合與測試

排序、過濾、統計、結束碼。以 `-fsanitize=address,undefined` 跑完整套件確認沒有洩漏；分別用 gcc 與 clang 編譯；故意在 teardown 中放一個會失敗的 `ASSERT_TRUE`，確認不會無窮迴圈。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>點開參考實作：ut.h</summary>

```c
/* ut.h - a tiny unit test framework: auto registration, type-aware asserts,
 * fixtures, and failures that never stop the other tests. C11 + GNU attributes. */
#ifndef UT_H
#define UT_H

#include <setjmp.h>
#include <stddef.h>

/* ---------- registry ---------- */
typedef struct ut_test {
    const char *suite, *name, *file;
    int line;
    void (*run)(void *fixture);          /* test body */
    void (*setup)(void *fixture);        /* may be NULL */
    void (*teardown)(void *fixture);     /* may be NULL */
    size_t fixture_size;                 /* 0 = no fixture */
    struct ut_test *next;
} ut_test;

void ut_register(ut_test *t);

/* ---------- per-test state used by the assert functions ---------- */
typedef struct {
    jmp_buf env;           /* where a fatal assertion jumps back to */
    int failures;          /* assertions failed in the current test */
} ut_state;
extern ut_state ut_cur;

#define UT_CAT_(a, b) a##b
#define UT_CAT(a, b) UT_CAT_(a, b)
#define UT_ID(prefix, suite, name) UT_CAT(prefix, UT_CAT(suite, UT_CAT(_, name)))

/* ---------- TEST(suite, name) ---------- */
#define UT_DEFINE(suite, name, setup_fn, teardown_fn, size, body_param)          \
    static void UT_ID(ut_body_, suite, name)(body_param);                         \
    static void UT_ID(ut_run_, suite, name)(void *f)                              \
    {                                                                             \
        UT_ID(ut_body_, suite, name)(f);                                          \
    }                                                                             \
    static ut_test UT_ID(ut_rec_, suite, name) = {                                \
        #suite, #name, __FILE__, __LINE__, UT_ID(ut_run_, suite, name),           \
        setup_fn, teardown_fn, size, NULL };                                      \
    __attribute__((constructor)) static void UT_ID(ut_reg_, suite, name)(void)    \
    {                                                                             \
        ut_register(&UT_ID(ut_rec_, suite, name));                                \
    }                                                                             \
    static void UT_ID(ut_body_, suite, name)(body_param)

#define TEST(suite, name) \
    UT_DEFINE(suite, name, NULL, NULL, 0, void *ut_unused_ __attribute__((unused)))

/* ---------- fixtures ---------- */
#define FIXTURE(fx) struct ut_fixture_##fx
#define FIXTURE_SETUP(fx)                                                   \
    static void ut_setup_##fx(struct ut_fixture_##fx *self);                \
    static void ut_setup_wrap_##fx(void *p) { ut_setup_##fx(p); }           \
    static void ut_setup_##fx(struct ut_fixture_##fx *self)
#define FIXTURE_TEARDOWN(fx)                                                \
    static void ut_teardown_##fx(struct ut_fixture_##fx *self);             \
    static void ut_teardown_wrap_##fx(void *p) { ut_teardown_##fx(p); }     \
    static void ut_teardown_##fx(struct ut_fixture_##fx *self)
#define TEST_F(fx, name)                                                    \
    UT_DEFINE(fx, name, ut_setup_wrap_##fx, ut_teardown_wrap_##fx,           \
              sizeof(struct ut_fixture_##fx), struct ut_fixture_##fx *self)

/* ---------- type-aware comparison ---------- */
typedef struct {
    const char *file;
    int line;
    const char *op, *lhs, *rhs;
    int fatal;
} ut_site;

void ut_cmp_i(ut_site s, long long a, long long b);
void ut_cmp_u(ut_site s, unsigned long long a, unsigned long long b);
void ut_cmp_d(ut_site s, long double a, long double b);
void ut_cmp_s(ut_site s, const char *a, const char *b);
void ut_cmp_p(ut_site s, const void *a, const void *b);
void ut_check(ut_site s, int ok);
void ut_near(ut_site s, double a, double b, double eps);

/* pick the comparison back end from the type of the LEFT operand */
#define UT_CMP_FN(x) _Generic((x),                                        \
    _Bool: ut_cmp_u, char: ut_cmp_i, signed char: ut_cmp_i,               \
    unsigned char: ut_cmp_u, short: ut_cmp_i, unsigned short: ut_cmp_u,   \
    int: ut_cmp_i, unsigned: ut_cmp_u, long: ut_cmp_i,                    \
    unsigned long: ut_cmp_u, long long: ut_cmp_i,                         \
    unsigned long long: ut_cmp_u,                                         \
    float: ut_cmp_d, double: ut_cmp_d, long double: ut_cmp_d,             \
    char *: ut_cmp_s, const char *: ut_cmp_s,                             \
    default: ut_cmp_p)

/* stringize HERE, in the macro the user called: if #a were done one level
 * deeper, the argument would already be macro-expanded (NULL -> ((void *)0)) */
#define UT_SITE(op, sa, sb, fatal) ((ut_site){ __FILE__, __LINE__, op, sa, sb, fatal })

#define ASSERT_EQ(a, b) UT_CMP_FN(a)(UT_SITE("ASSERT_EQ", #a, #b, 1), (a), (b))
#define EXPECT_EQ(a, b) UT_CMP_FN(a)(UT_SITE("EXPECT_EQ", #a, #b, 0), (a), (b))
#define ASSERT_TRUE(c)  ut_check(UT_SITE("ASSERT_TRUE", #c, "", 1), !!(c))
#define EXPECT_TRUE(c)  ut_check(UT_SITE("EXPECT_TRUE", #c, "", 0), !!(c))
#define ASSERT_NEAR(a, b, eps) \
    ut_near(UT_SITE("ASSERT_NEAR", #a, #b, 1), (a), (b), (eps))

#endif
```

</details>

<details>
<summary>點開參考實作：ut.c</summary>

```c
/* ut.c - runner and assertion back ends */
#define _POSIX_C_SOURCE 199309L
#include "ut.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

ut_state ut_cur;
static ut_test *ut_head;
static size_t ut_count;

void ut_register(ut_test *t)
{
    t->next = ut_head;           /* order is fixed later by sorting */
    ut_head = t;
    ut_count++;
}

/* ---------- reporting ---------- */
static void ut_fail(ut_site s)
{
    ut_cur.failures++;
    if (s.fatal)
        longjmp(ut_cur.env, 1);  /* abandon the rest of this test body */
}

static void ut_header(ut_site s)
{
    if (*s.rhs)
        printf("%s:%d: Failure: %s(%s, %s)\n", s.file, s.line, s.op, s.lhs, s.rhs);
    else
        printf("%s:%d: Failure: %s(%s)\n", s.file, s.line, s.op, s.lhs);
}

void ut_cmp_i(ut_site s, long long a, long long b)
{
    if (a == b) return;
    ut_header(s);
    printf("    left : %lld\n    right: %lld\n", a, b);
    ut_fail(s);
}

void ut_cmp_u(ut_site s, unsigned long long a, unsigned long long b)
{
    if (a == b) return;
    ut_header(s);
    printf("    left : %llu (0x%llx)\n    right: %llu (0x%llx)\n", a, a, b, b);
    ut_fail(s);
}

void ut_cmp_d(ut_site s, long double a, long double b)
{
    if (a == b) return;
    ut_header(s);
    printf("    left : %.17Lg\n    right: %.17Lg\n", a, b);
    ut_fail(s);
}

void ut_cmp_s(ut_site s, const char *a, const char *b)
{
    if (a == b || (a && b && strcmp(a, b) == 0)) return;
    ut_header(s);
    printf("    left : %s%s%s\n", a ? "\"" : "", a ? a : "NULL", a ? "\"" : "");
    printf("    right: %s%s%s\n", b ? "\"" : "", b ? b : "NULL", b ? "\"" : "");
    ut_fail(s);
}

void ut_cmp_p(ut_site s, const void *a, const void *b)
{
    if (a == b) return;
    ut_header(s);
    printf("    left : %p\n    right: %p\n", a, b);
    ut_fail(s);
}

void ut_check(ut_site s, int ok)
{
    if (ok) return;
    ut_header(s);
    printf("    evaluated to false\n");
    ut_fail(s);
}

void ut_near(ut_site s, double a, double b, double eps)
{
    if (fabs(a - b) <= eps) return;
    ut_header(s);
    printf("    left : %.17g\n    right: %.17g\n    |diff| = %.3g > %.3g\n",
           a, b, fabs(a - b), eps);
    ut_fail(s);
}

/* ---------- runner ---------- */
static int ut_order(const void *x, const void *y)
{
    const ut_test *a = *(ut_test *const *) x, *b = *(ut_test *const *) y;
    int c = strcmp(a->file, b->file);
    return c ? c : (a->line > b->line) - (a->line < b->line);
}

static double ut_now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

/* fx and set_up are modified between setjmp and longjmp, so they must be
 * volatile, otherwise their values are indeterminate after longjmp (C11 7.13.2.1p3) */
static int ut_run_one(const ut_test *t)
{
    void *volatile fx = NULL;
    volatile int set_up = 0;

    ut_cur.failures = 0;
    if (t->fixture_size) {
        fx = calloc(1, t->fixture_size);
        if (!fx) {
            puts("    cannot allocate fixture");
            return 0;
        }
    }
    if (setjmp(ut_cur.env) == 0) {
        if (t->setup)
            t->setup(fx);
        set_up = 1;
        t->run(fx);
    }
    /* reached normally, or via longjmp from a fatal assertion */
    if (set_up && t->teardown) {
        if (setjmp(ut_cur.env) == 0)   /* a fatal assert inside teardown lands here */
            t->teardown(fx);
    }
    free(fx);
    return ut_cur.failures == 0;
}

int main(int argc, char *argv[])
{
    const char *filter = argc > 1 ? argv[1] : NULL;
    size_t cap = ut_count ? ut_count : 1;
    ut_test **v = malloc(cap * sizeof *v);
    char (*names)[128] = malloc(cap * sizeof *names);
    size_t *failed = malloc(cap * sizeof *failed);
    if (!v || !names || !failed)
        return 2;

    size_t n = 0;
    for (ut_test *t = ut_head; t; t = t->next)
        v[n++] = t;
    qsort(v, n, sizeof *v, ut_order);   /* deterministic: by file, then line */

    size_t ran = 0, nfail = 0;
    double t0 = ut_now_ms();
    for (size_t i = 0; i < n; i++) {
        snprintf(names[i], sizeof names[i], "%s.%s", v[i]->suite, v[i]->name);
        if (filter && !strstr(names[i], filter))
            continue;
        printf("[ RUN      ] %s\n", names[i]);
        fflush(stdout);
        ran++;
        if (ut_run_one(v[i])) {
            printf("[       OK ] %s\n", names[i]);
        } else {
            failed[nfail++] = i;
            printf("[  FAILED  ] %s\n", names[i]);
        }
    }
    printf("[==========] %zu test(s) ran, %zu passed, %zu failed (%.3f ms)\n",
           ran, ran - nfail, nfail, ut_now_ms() - t0);
    for (size_t k = 0; k < nfail; k++)
        printf("[  FAILED  ] %s\n", names[failed[k]]);

    free(v);
    free(names);
    free(failed);
    return nfail ? 1 : 0;
}
```

</details>

**解答說明**

1. **`TEST` 展開成四樣東西**。以 `TEST(math, add_small)` 為例，`gcc -E -P` 可以看到它產生：本體的前置宣告 `static void ut_body_math_add_small(void *ut_unused_ ...);`、一個統一簽章 `void (*)(void *)` 的包裝 `ut_run_math_add_small`、一筆 `static ut_test ut_rec_math_add_small = { "math", "add_small", "test_math.c", 8, ... }`、一個 constructor `ut_reg_math_add_small`，最後是**未完成的** `static void ut_body_math_add_small(void *ut_unused_ ...)`，使用者的 `{ ... }` 接上去成為它的本體。`UT_ID` 經過 `UT_CAT` 兩層間接，是因為 `suite`/`name` 理論上可能是巨集（Ch 27「兩層間接」）。`#suite` 與 `#name` 直接在 `UT_DEFINE` 中做，因為 `UT_DEFINE` 收到的是使用者寫的識別字；若使用者傳入的是巨集，這裡會得到展開前或展開後的名字取決於它被傳了幾層，這是巨集框架常見的微妙處。

2. **為什麼需要 `ut_run_*` 包裝**。`TEST` 的本體參數是 `void *`，`TEST_F` 的本體參數是 `struct ut_fixture_stack *self`。註冊表要用同一種函式指標型別，而用不相容的函式指標型別呼叫函式是未定義行為（N1570 6.5.2.2 §9），所以不能把 `void (*)(struct ut_fixture_stack *)` 硬轉成 `void (*)(void *)` 呼叫。包裝函式接收 `void *`，再以隱式轉換傳給真正的本體，型別上完全合法。fixture 的 setup/teardown 包裝也是同理。這正是 Ch 29 的 vtable 原則：介面統一用基底型別，實作內部再轉型。

3. **`constructor` 的順序不可依賴**。GCC 不保證不同翻譯單元間 constructor 的執行順序，同一檔案內的順序也是實作細節（本實作用頭插法，未排序時順序反過來）。所以執行器在 `main` 中依「檔名、行號」`qsort`，輸出才可重現。如果你的實作輸出順序和範例不同，先檢查這裡。

4. **`_Generic` 選函式而不是選運算式**。`UT_CMP_FN(a)` 的結果是一個函式指示符，接著立刻以 `(site, (a), (b))` 呼叫。每個引數只出現一次，所以只求值一次；右運算元依被選中函式的參數型別轉換。代價是「依左運算元決定型別」：`ASSERT_EQ(0, p)` 會選中 `ut_cmp_i`，把指標轉成整數並觸發 `-Wint-conversion` 警告（見「測試用例」）。字串分支用 `strcmp` 是刻意的：C 程式員寫 `ASSERT_EQ(buf, "abc")` 幾乎總是想比內容；要比位址請轉成 `(void *)`。

5. **字串化必須在使用者呼叫的那一層**。第一版的實作寫成 `#define UT_SITE(op, a, b, fatal) (... #a, #b ...)`，由 `ASSERT_EQ(a, b)` 把 `a`、`b` 原樣傳下去，結果：

   ```
   test_stack.c:66: Failure: ASSERT_EQ((void *) ((void *)0), (void *) p)
   ```

   `ASSERT_EQ` 的參數 `a` 在它自己的替換清單中不與 `#` 相鄰，所以傳給 `UT_SITE` 之前就被完整展開（N1570 6.10.3.1 §1），`NULL` 變成了 `((void *)0)`。修正後由 `ASSERT_EQ` 自己做 `#a`，訊息才是使用者寫的 `(void *) NULL`。`assert.h` 的實作也必須注意這一點。

6. **`setjmp`/`longjmp` 的三個細節**。(a) `setjmp` 出現在 `if (setjmp(...) == 0)`，符合 7.13.1.1 §4 允許的語境。(b) `fx` 與 `set_up` 在 `setjmp` 之後、`longjmp` 之前被修改，依 7.13.2.1 §3 必須宣告 `volatile`，否則 `longjmp` 回來後其值不確定；`-O2` 下編譯器可能把它們放在暫存器裡，回來時讀到舊值，結果是 teardown 沒跑或 fixture 洩漏。注意是 `void *volatile fx`（指標本身 volatile），不是 `volatile void *fx`。(c) teardown 前重新 `setjmp`，否則 teardown 中的 fatal 斷言會 `longjmp` 回第一個 `setjmp`，造成無窮迴圈。

7. **集中出口**。`ut_run_one` 不論測試正常結束或被 `longjmp` 打斷，都匯入同一段「teardown → free」程式碼，這就是 Ch 31 的清理鏈思想：`set_up` 旗標記錄「進度」，決定 teardown 的前提是否成立（setup 本身失敗時，fixture 可能只建了一半，不能 teardown）。

8. **為什麼 `longjmp` 跳過 fixture 的區域變數不會有問題**。被跳過的是測試本體與輔助函式的 stack frame，它們的 automatic 變數本來就隨函式結束而消失；fixture 在 heap 上，由執行器持有指標並負責釋放。但如果使用者在測試本體中 `malloc` 了東西、然後 `ASSERT` 失敗，那塊記憶體會洩漏，這就是 fixture 存在的理由：**需要保證釋放的資源放進 fixture，由 teardown 收拾**。

## 測試用例

| 輸入 | 預期輸出 | 說明 |
|---|---|---|
| 期望輸出範例的兩個測試檔，不帶引數 | 9 ran、3 passed、6 failed，結束碼 1 | 正常情況 |
| `./tests stack.push` | 1 ran、1 passed，結束碼 0 | 過濾 |
| `./tests nomatch` | 0 ran，結束碼 0 | 沒有符合的測試 |
| `./tests pointers` | 2 ran（`strings.compare_contents_not_pointers` 也符合子字串） | 過濾是子字串比對，不是 suite 比對 |
| `EXPECT_EQ` 失敗兩次後 `ASSERT_EQ` 通過 | 兩段失敗訊息，測試結果 FAILED，本體跑完 | non-fatal |
| fixture 中 `ASSERT` 失敗 | 失敗訊息之後仍出現 teardown 的輸出 | teardown 保證 |
| teardown 中 `ASSERT_TRUE(0)` | 印出失敗、測試 FAILED、下一個測試照跑，不會無窮迴圈 | 第二個 `setjmp` |
| `ASSERT_EQ(0, p)`（`p` 是指標） | 編譯警告 `passing argument 3 of ‘ut_cmp_i’ makes integer from pointer without a cast [-Wint-conversion]`；執行時 left 0、right 是位址的十進位 | 左運算元決定型別的限制 |
| `ASSERT_EQ(*q, 1)`，`q` 為 `NULL` | `Segmentation fault`，整個執行器結束（結束碼 139） | 本框架不隔離當機，見延伸挑戰 |
| `-fsanitize=address,undefined` 跑全部 | 沒有任何 ASan/UBSan 報告 | fixture 與執行器無洩漏 |
| 測試檔中寫 `int v;` 再 `ASSERT_EQ(stack_pop(s, &v), 0); ASSERT_EQ(v, 2);`，以 gcc 11 `-O2` 編譯 | `‘v’ may be used uninitialized [-Wmaybe-uninitialized]` | 編譯器不知道 `ut_cmp_i` 在失敗時會 `longjmp` 而不返回，於是認為「pop 失敗、`v` 未設定、程式繼續讀 `v`」是可能路徑；`int x;` 再把 `&x` 傳給 `const void *` 參數也會觸發同一警告。範例因此一律寫 `int v = 0;` |

最後兩列是實測結果：

```
$ gcc -std=gnu11 -Wall -Wextra ut.c warn.c -o warn -lm
ut.h:97:76: warning: passing argument 3 of ‘ut_cmp_i’ makes integer from pointer without a cast [-Wint-conversion]
$ ./warn
[ RUN      ] warn.int_vs_pointer
warn.c:6: Failure: ASSERT_EQ(0, p)
    left : 0
    right: 140726967213700
[  FAILED  ] warn.int_vs_pointer
[ RUN      ] warn.crash
Segmentation fault (core dumped)
```

## 延伸挑戰（加分）

- **當機隔離**：在 `ut_run_one` 中 `fork()`，子行程執行測試並以結束碼回報，父行程 `waitpid` 後用 `WIFSIGNALED` 判斷是否被 signal 終止，印出 `[  CRASH   ] suite.name (signal 11)`。這是 Check 與 Criterion 的預設做法。想想 `fork` 之後的 stdout 緩衝區要怎麼處理（[Ch 35](./35-stream-io-and-signals.md)）。
- **改用連結器區段註冊**：拿掉 constructor，讓 `TEST` 產生一筆放進 `section("ut_tests")` 的 `const` 紀錄，執行器走訪 `__start_ut_tests` 到 `__stop_ut_tests`（Ch 28 initcall 的同一招）。下面這個最小原型實測可運作：

  ```c
  typedef struct { const char *name; void (*fn)(void); } entry;
  #define REGISTER(f) \
      static const entry reg_##f __attribute__((used, section("ut_tests"), aligned(sizeof(void *)))) = { #f, f }
  ...
  extern const entry __start_ut_tests[], __stop_ut_tests[];
  for (const entry *e = __start_ut_tests; e < __stop_ut_tests; e++) e->fn();
  ```

  比較兩種方式：區段版不需要在執行時期跑任何程式碼、紀錄可以是 `const`（放進唯讀區）；constructor 版不依賴 GNU ld 的 `__start_`/`__stop_` 慣例，在 macOS 的 Mach-O 上也比較容易移植。
- **更多斷言**：`ASSERT_NE`、`ASSERT_LT`（注意有號與無號混合比較的陷阱，[Ch 22](./22-integer-overflow.md)）、`ASSERT_MEM_EQ(a, b, n)`（失敗時印出第一個不同的位元組與前後各 8 位元組的十六進位）、`ASSERT_STR_CONTAINS`。
- **右運算元也參與型別判斷**：用巢狀 `_Generic` 在兩個運算元一個有號、一個無號時發出編譯期錯誤，而不是靜靜地轉型比較。
- **TAP 輸出**：加一個 `--tap` 選項輸出 Test Anything Protocol（`ok 1 - math.add_small`），讓 CI 系統可以解析。
- **只有 setup 或只有 teardown 的 fixture**：目前 `TEST_F` 要求兩者都定義。想辦法讓沒定義的那個自動變成 `NULL`（提示：弱符號 `__attribute__((weak))`，或要求使用者寫 `FIXTURE_NO_TEARDOWN(fx)`）。

## 自我檢核

- [ ] 能不看解答，說出 `TEST(math, add) { ... }` 展開後依序產生的四樣東西，以及為什麼使用者的大括號能接在最後
- [ ] 能解釋為什麼註冊表的函式指標型別必須統一，以及包裝函式如何避免 6.5.2.2 §9 的未定義行為
- [ ] 能說明 `_Generic` 在這裡「選函式而非選運算式」的好處，並指出「依左運算元決定型別」造成的具體限制
- [ ] 能解釋第一版印出 `((void *)0)` 的原因，並引用 6.10.3.1 的規則
- [ ] 能說出 `ut_run_one` 中兩個 `volatile` 的必要性與第二個 `setjmp` 的作用
- [ ] 能比較 constructor 註冊與 section 註冊的取捨
- [ ] 能說出自己的實作和參考解答的差異，並解釋各自取捨

## 延伸閱讀

- **[kselftest_harness.h](https://github.com/torvalds/linux/blob/master/tools/testing/selftests/kselftest_harness.h)** — Linux 核心
  - **讀哪裡**：檔案開頭的大段註解（API 說明），再看 `TEST`、`FIXTURE`、`TEST_F`、`__EXPECT` 的定義
  - **和本練習的關聯**：介面幾乎相同；它用 `fork()` 隔離每個測試、用 `__attribute__((constructor))` 註冊，正好是本練習加上第一個延伸挑戰的工業版本

- **[Unity](https://github.com/ThrowTheSwitch/Unity) 與 `extras/fixture/src/unity_fixture.h`** — ThrowTheSwitch
  - **讀哪裡**：`unity_fixture.h` 的 `TEST_GROUP`、`TEST`、`RUN_TEST_CASE`；`unity_internals.h` 中依型別分流的 `UNITY_TEST_ASSERT_EQUAL_*`
  - **為什麼值得讀**：嵌入式領域最常用的 C 測試框架，刻意不依賴 GNU 擴充（沒有 constructor），改用手寫或腳本產生的 runner；和本練習對照，可以看到「可攜性 vs 自動註冊」的取捨

- **[Some obscure C features you might not know about](https://mort.coffee/home/obscure-c-features/)** — Martin Dørum（2018）
  - **這篇說什麼**：snow 測試框架的作者介紹他用到的手法：以 `__VA_ARGS__` 接收含逗號的程式碼區塊、巢狀 `_Generic` 區分指標與整數、labels as values 實作 `defer`、constructor 自動註冊
  - **前提知識**：Ch 27、Ch 28

- **[Criterion](https://github.com/Snaipe/Criterion)** — Snaipe
  - **讀哪裡**：README 的功能列表與 `include/criterion/internal/test.h` 中 `Test()` 巨集的定義
  - **為什麼值得讀**：以連結器區段註冊、預設 `fork` 隔離、支援平行執行與參數化測試，是本練習所有延伸挑戰的完整實作

Part 5 到這裡結束：你看過了前置處理器的規則與極限、用 C 手工打造的物件模型、`goto` 與跳躍表，以及技巧、未定義行為與 I/O 的種種細節，並親手寫出一個用到其中大半的測試框架。下一個 Part 往下一層，看這些 C 程式碼如何經過 GNU toolchain 變成目的檔、連結成執行檔、在 `execve` 之後跑起來。

→ [Ch 36 GNU toolchain 與編譯流程](./36-gnu-toolchain.md)
