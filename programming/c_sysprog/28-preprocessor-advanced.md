# Ch 28 — 前置處理器 II：X macro、元程式設計與 C23 新特徵

> **目標**：能用 X macro 讓多份衍生資料由單一來源產生，能寫出計算引數個數、依個數多載、在前置處理器中「有上限地遞迴」的巨集，知道 C23 的 `__VA_OPT__`、`#embed`、`#elifdef`、`__has_include` 取代了哪些舊擴充；並讀得懂 Linux 核心、CPython、SQLite、BSD、Zephyr、GLib 用前置處理器產生整套程式碼的手法。

> **對應原文**：[你所不知道的 C 語言：前置處理器應用篇](https://hackmd.io/@sysprog/c-preprocessor) 的「C23 的前置處理器新特徵」「應用：String switch in C」「應用：鏈結串列的各式變種」「應用：Unit Test」「應用：Object Model 與 Exception Handling」「應用：ADT」「前置處理器的元程式設計」「Linux 核心的前置處理器技巧」「其他開放原始碼專案的前置處理器應用」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14、GNU ld 2.38；C23 段落以 Docker `gcc:15`（GCC 15.3）執行。

## 為什麼需要這個？

Ch 27 把巨集當成「需要小心的工具」。這章換個角度：巨集的真正價值是**產生程式碼**。C 沒有 template、沒有反射、沒有 annotation、沒有 build-time code generation 的語言內建機制，前置處理器是唯一一個「在編譯器看到程式碼之前，讓你改寫程式碼」的地方。

一個具體的痛點：你有一份錯誤碼清單，需要 `enum`、錯誤碼轉字串、字串轉錯誤碼、錯誤訊息表四份衍生物。手寫四份，新增一個錯誤碼時漏改其中一份，bug 就在三個月後的 log 裡出現「`?`」。其他語言用反射或程式碼產生器解決；C 的傳統答案是 X macro。

再往上一層，Linux 核心開發者只寫一次 `TRACE_EVENT(...)`，前置處理器就產生記錄結構、ftrace 輸出函式、perf 與 BPF 介接程式碼；Zephyr 把整份硬體描述（devicetree）在編譯時期變成巨集，驅動程式讀暫存器位址不需要任何執行時期查詢。這些都是同一套 Ch 27 的展開規則，只是被推到極限。

這章也要誠實面對代價：編譯時間、錯誤訊息、除錯困難。讀完你應該能判斷什麼時候值得、什麼時候該換成外部產生器或乾脆手寫。

## 先建立直覺

把前置處理器想成一台**只會查表與複製貼上的印刷機**：

```
                 ┌──────────────────────────────┐
  資料清單  ───► │  同一份清單 × 不同的 X 定義    │ ───► enum / 字串表 / switch / ...
 (寫一次)        └──────────────────────────────┘        (自動產生、永不脫節)

  "遞迴"        印刷機每掃一輪，被印過的巨集名稱會「塗藍」，這一輪不能再印。
                技巧：讓名稱和括號這一輪不相鄰，下一輪再拼起來 → 再印一次。
                EVAL 就是強迫印刷機多跑 N 輪。輪數寫死 → 遞迴有上限。

  "if/else"     印刷機不會比較大小，只會「把 token 黏起來再查表」：
                IIF_ ## 1 → IIF_1 → 選第一個；IIF_ ## 0 → IIF_0 → 選第二個。
```

三個核心技巧：**同一清單多次展開**（X macro）、**`##` 拼接名稱後查表**（條件與計數）、**延後展開**（遞迴）。後面所有開源專案的手法都是它們的組合。

> 本章大量使用 Ch 27 的展開規則，特別是「`#`/`##` 不先展開引數，所以要多一層間接」與「painted blue」。如果還不熟，先回看 [Ch 27 — 前置處理器 I](./27-preprocessor-basics.md) 的「巨集展開的精確規則」。

## X macro：一份清單，多種展開

```c
#include <stdio.h>
#include <string.h>

/* one list, many expansions */
#define ERRNO_LIST(X)                          \
    X(OK,        0, "success")                 \
    X(ENOMEM,  -12, "out of memory")           \
    X(EINVAL,  -22, "invalid argument")        \
    X(ETIMEOUT,-110, "connection timed out")

enum err {
#define X(name, val, msg) ERR_##name = val,
    ERRNO_LIST(X)
#undef X
};

static const char *err_name(enum err e)
{
    switch (e) {
#define X(name, val, msg) case ERR_##name: return #name;
    ERRNO_LIST(X)
#undef X
    }
    return "?";
}

static const char *err_msg(enum err e)
{
    switch (e) {
#define X(name, val, msg) case ERR_##name: return msg;
    ERRNO_LIST(X)
#undef X
    }
    return "?";
}

static int err_from_name(const char *s, enum err *out)
{
#define X(name, val, msg) if (!strcmp(s, #name)) { *out = ERR_##name; return 1; }
    ERRNO_LIST(X)
#undef X
    return 0;
}

#define X(name, val, msg) +1
enum { ERR_COUNT = 0 ERRNO_LIST(X) };
#undef X

int main(void)
{
    enum err all[] = {
#define X(name, val, msg) ERR_##name,
        ERRNO_LIST(X)
#undef X
    };
    for (int i = 0; i < ERR_COUNT; i++)
        printf("%-9s %5d  %s\n", err_name(all[i]), all[i], err_msg(all[i]));
    enum err e;
    if (err_from_name("EINVAL", &e))
        printf("lookup EINVAL -> %d\n", e);
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra -pedantic xmacro.c -o xmacro && ./xmacro
OK            0  success
ENOMEM      -12  out of memory
EINVAL      -22  invalid argument
ETIMEOUT   -110  connection timed out
lookup EINVAL -> -22
$ gcc -E -P xmacro.c | grep -A2 "^enum err {"
enum err {
    ERR_OK = 0, ERR_ENOMEM = -12, ERR_EINVAL = -22, ERR_ETIMEOUT = -110,
};
```

幾個設計細節：

- 清單巨集把「要套用的巨集」當參數（`ERRNO_LIST(X)`），而不是寫死名稱 `X`。這樣同一個翻譯單元可以同時用不同名字展開，不必依賴 `#define X`/`#undef X` 的順序。兩種風格在野外都很常見。
- `ERR_COUNT = 0 +1 +1 +1 +1` 是在編譯時期**數項目**。錯誤碼不連續時不能用「最後一項 + 1」計數，這招才可靠。
- 錯誤碼值不連續（-110 和 -22 相差很遠），所以字串表用 `switch` 而非陣列；值連續時可以用 designated initializer `[ERR_##name] = #name`，原文的 `COLOR_LIST` 範例就是這樣，再配上 `_Static_assert(sizeof(table)/sizeof(table[0]) == COUNT, ...)` 防脫節。
- 手寫 `switch` 時漏掉某個 `enum` 值，`-Wswitch`（含於 `-Wall`）會警告；X macro 讓這個警告根本沒有機會出現。

### 檔案層級的 X macro：Linux 的 `TRACE_EVENT`

Linux 核心的 tracepoint 把清單從一個巨集擴大到**整個標頭檔**：同一個 `include/trace/events/sched.h` 被反覆 `#include` 好幾次，每次之前先改寫 `TRACE_EVENT`、`__field` 等巨集的定義。以下摘錄自 torvalds/linux（原文摘錄時為 v7.3-rc6，行號日後可能變動）：

```c
/* include/trace/events/sched.h：允許重複引入的防護 */
#if !defined(_TRACE_SCHED_H) || defined(TRACE_HEADER_MULTI_READ)
#define _TRACE_SCHED_H
...
TRACE_EVENT(sched_kthread_stop,
	TP_PROTO(struct task_struct *t),
	TP_ARGS(t),
	TP_STRUCT__entry(
		__string(	comm,	t->comm		)
		__field(	pid_t,	pid		)
	),
	...
```

流程是：

```
一般 .c 檔 #include <trace/events/sched.h>
   └─ TRACE_EVENT → 只產生宣告與 trace_sched_kthread_stop() 呼叫入口

kernel/sched/core.c 先 #define CREATE_TRACE_POINTS 再 #include
   └─ 標頭結尾 #include <trace/define_trace.h>
        ├─ #undef CREATE_TRACE_POINTS（防遞迴）
        ├─ 重新定義 TRACE_EVENT → DEFINE_TRACE(...)
        ├─ #define TRACE_HEADER_MULTI_READ（讓防護放行）
        ├─ 再引入事件標頭 1 次
        └─ #include <trace/trace_events.h>
              ├─ stage 1：__field(type, item) → "type item;"   → struct trace_event_raw_<name>
              ├─ stage 2：→ 動態陣列偏移量
              ├─ ...       → 輸出函式、欄位描述表、事件回呼
              └─ 共引入事件標頭 7 次；perf.h、bpf_probe.h 各再 1 次
```

開發者寫一次描述，記錄結構、ftrace 格式、perf 與 BPF 介接都自動產生、永不脫節。這是 X macro 的工業級版本。

> **本段未實測**：Linux 核心的 tracepoint 需要完整核心建置環境，以上流程依原文與核心原始碼閱讀整理；`xmacro.c` 示範的是同一原理的縮小版。

## 可變參數巨集與 `__VA_OPT__`

C99 加入 `...` 與 `__VA_ARGS__`。老問題是只傳格式字串時留下多餘的逗號：

```c
#define LOG(fmt, ...)  printf(fmt __VA_OPT__(,) __VA_ARGS__)
#define OLD(fmt, ...)  printf(fmt, __VA_ARGS__)
#define GLOG(fmt, ...) printf(fmt, ## __VA_ARGS__)
#define E
LOG("a"); LOG("b %d", 1); LOG("c", E);
OLD("d");
GLOG("e"); GLOG("f %d", 2);
```

```
$ gcc -E -P vaopt.c
printf("a" ); printf("b %d" , 1); printf("c" );
printf("d", );
printf("e"); printf("f %d", 2);
```

- `OLD("d")` 留下 `printf("d", )`，編譯錯誤。而且 C17 6.10.3 §4 要求 `...` 至少對應一個引數，`OLD("d")` 本身就違規。
- GCC 的老解法 `, ## __VA_ARGS__`：`__VA_ARGS__` 為空時 `##` 把前面的逗號吃掉。這是 GNU 擴充，本來 `##` 和空引數串接只會得到逗號。
- C23 的 `__VA_OPT__(x)`（N3220 6.10.5.1）：可變引數「展開後仍有 token」才保留 `x`。`LOG("c", E)` 中 `E` 展開為空，所以 `__VA_OPT__(,)` 也不產生逗號。C23 同時把 6.10.5 §4 放寬為引數個數不少於具名參數即可。

各編譯器在嚴格模式下的反應：

```
$ gcc -std=c17 -pedantic -E -P vaopt.c          # gcc 11
vaopt.c:1:35: warning: __VA_OPT__ is not available until C++20
vaopt.c:5:8: warning: ISO C99 requires at least one argument for the "..." in a variadic macro
$ clang -std=c17 -pedantic -E -P vaopt.c        # clang 14
vaopt.c:3:34: warning: token pasting of ',' and __VA_ARGS__ is a GNU extension [-Wgnu-zero-variadic-macro-arguments]
vaopt.c:5:8: warning: must specify at least one argument for '...' parameter of variadic macro [-Wgnu-zero-variadic-macro-arguments]
```

gcc 11 在 C 模式下的警告文字寫 "until C++20"，這是訊息沿用 C++ 的痕跡（它其實已經在 C 模式提供這個擴充）；原文以 GCC 16 測試時訊息已改為 "'__VA_OPT__' is not available until C23"。clang 14 在 C17 模式下對 `__VA_OPT__` 不發警告。結論：要可攜就寫 `__VA_OPT__` 並以 C23 編譯；舊程式碼裡的 `, ##` 看得懂即可。

### 計算引數個數與依個數多載

C 沒有函式多載，但巨集可以先數引數，再用 `##` 拼出 `area_1`、`area_2` 這類名稱：

```c
#include <stdio.h>

/* count arguments: shift a reversed sequence and pick a fixed position */
#define PP_ARG_N(_1, _2, _3, _4, _5, _6, _7, _8, N, ...) N
#define PP_RSEQ_N() 8, 7, 6, 5, 4, 3, 2, 1, 0
#define PP_NARG_(...) PP_ARG_N(__VA_ARGS__)
#define PP_NARG(...)  PP_NARG_(__VA_ARGS__, PP_RSEQ_N())

/* overload by argument count */
#define PP_CAT(a, b)  PP_CAT_(a, b)
#define PP_CAT_(a, b) a##b
#define PP_OVERLOAD(prefix, ...) PP_CAT(prefix, PP_NARG(__VA_ARGS__))(__VA_ARGS__)

#define area(...) PP_OVERLOAD(area_, __VA_ARGS__)
static double area_1(double r) { return 3.14159265358979 * r * r; }      /* circle */
static double area_2(double w, double h) { return w * h; }               /* rectangle */
static double area_3(double a, double b, double c)                       /* triangle (Heron) */
{
    double s = (a + b + c) / 2;
    return __builtin_sqrt(s * (s - a) * (s - b) * (s - c));
}

/* apply a macro to every argument (fixed upper bound: 4) */
#define FE_1(m, x)      m(x)
#define FE_2(m, x, ...) m(x) FE_1(m, __VA_ARGS__)
#define FE_3(m, x, ...) m(x) FE_2(m, __VA_ARGS__)
#define FE_4(m, x, ...) m(x) FE_3(m, __VA_ARGS__)
#define FOR_EACH(m, ...) PP_CAT(FE_, PP_NARG(__VA_ARGS__))(m, __VA_ARGS__)
#define PRINT_VAR(v) printf(#v " = %d\n", v);

int main(void)
{
    printf("%d %d %d\n", PP_NARG(a), PP_NARG(a, b, c), PP_NARG(1, 2, 3, 4, 5, 6, 7, 8));
    printf("%.2f %.2f %.2f\n", area(1.0), area(2.0, 3.0), area(3.0, 4.0, 5.0));
    int x = 1, y = 2, z = 3;
    FOR_EACH(PRINT_VAR, x, y, z)
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra narg.c -o narg -lm && ./narg
1 3 8
3.14 6.00 6.00
x = 1
y = 2
z = 3
```

`PP_NARG(a, b, c)` 的推導：

```
PP_NARG(a, b, c)
→ PP_NARG_(a, b, c, 8, 7, 6, 5, 4, 3, 2, 1, 0)
→ PP_ARG_N(a, b, c, 8, 7, 6, 5, 4, 3, 2, 1, 0)
            _1 _2 _3 _4 _5 _6 _7 _8 N
                                    ↑ 第 9 個位置 = 3
```

引數越多，倒數序列被推得越右，落在第 9 個位置的數字就越大。中間那層 `PP_NARG_` 不能省：如果直接寫 `PP_ARG_N(__VA_ARGS__, PP_RSEQ_N())`，`PP_RSEQ_N()` 展開出的逗號要等到**重新掃描**才會出現，但切引數發生在那之前，`PP_ARG_N` 只會看到 2 個引數。

這套手法的邊界要清楚：

```
$ gcc -E -P narg0.c
A 1          ← PP_NARG() 回報 1：空引數也算一個
B 9          ← 9 個引數超出表格，結果「剛好」是 9；10 個引數時仍回報 9
```

超過上限時不會報錯，而是靜靜地給錯答案。Boost.Preprocessor、P99 這類函式庫把表格產生到 64 或更多，並用 Ch 27 的 `_Static_assert` 補上檢查。

`PRINT_VAR` 的 `#v` 把變數名稱字串化，配合 `FOR_EACH` 就是一個陽春版 `dbg!`。`FE_N` 必須手寫到上限 N，因為巨集不能遞迴，這正是下一節要解決的問題。

## 前置處理器的元程式設計：有上限的遞迴

N1570 6.10.3.4 §2（C23 為 N3220 6.10.5.4 §2）規定：重新掃描時遇到正在替換的巨集名稱，不予替換，而且這個 token **此後再也不會被替換**。所以：

```
$ cat rec.c
#define FOO(x, ...) x; FOO(__VA_ARGS__)
FOO(1, 2, 3)
$ gcc -E -P rec.c
1; FOO(2, 3)
$ clang -Weverything -E -P rec.c
rec.c:2:1: warning: disabled expansion of recursive macro [-Wdisabled-macro-expansion]
```

但停用的範圍只限於「這一次替換的掃描」。Ch 27 的 `max EMPTY (1, 2)` 已經展示：名稱與括號在本輪不相鄰，名稱就不會被辨識為呼叫，也就不會被塗藍。Paul Fultz II 在 Cloak 專案 wiki 整理出完整工具組：

| 工具 | 作用 | 原理 |
|---|---|---|
| `CAT(a, ...)` | 先展開再串接 | 多一層間接（Ch 27） |
| `IIF(c)(t, f)` | 0/1 選擇 | `IIF_ ## c` 拼出 `IIF_0` 或 `IIF_1` 再查表 |
| `CHECK`/`PROBE` | 判斷某 token 是否「特殊」 | 特殊 token 展開時多吐出一個逗號，讓「第 2 個引數」換人 |
| `NOT`/`BOOL` | 任意值轉 0/1 | 只有 `NOT_0` 有定義，會吐出 `PROBE` |
| `DEFER(id)` | 延後一輪 | `id EMPTY()`：本輪 `id` 與 `(` 之間隔著 `EMPTY()` |
| `OBSTRUCT(...)` | 延後兩輪 | 再多包一層 `DEFER` |
| `EVAL(...)` | 強迫重新掃描很多輪 | 巢狀呼叫自身：5 層 × 每層 3 次 |

完整程式（取自 Cloak wiki 的定義，以 `REPEAT` 在編譯時期產生平方數表）：

```c
#include <stdio.h>

#define CAT(a, ...) PRIMITIVE_CAT(a, __VA_ARGS__)
#define PRIMITIVE_CAT(a, ...) a##__VA_ARGS__

#define IIF(c) PRIMITIVE_CAT(IIF_, c)
#define IIF_0(t, ...) __VA_ARGS__
#define IIF_1(t, ...) t

#define COMPL(b) PRIMITIVE_CAT(COMPL_, b)
#define COMPL_0 1
#define COMPL_1 0

#define DEC(x) PRIMITIVE_CAT(DEC_, x)
#define DEC_0 0
#define DEC_1 0
#define DEC_2 1
#define DEC_3 2
#define DEC_4 3
#define DEC_5 4
#define DEC_6 5
#define DEC_7 6
#define DEC_8 7
#define DEC_9 8

#define CHECK_N(x, n, ...) n
#define CHECK(...) CHECK_N(__VA_ARGS__, 0, )
#define PROBE(x) x, 1,

#define NOT(x) CHECK(PRIMITIVE_CAT(NOT_, x))
#define NOT_0 PROBE(~)
#define BOOL(x) COMPL(NOT(x))
#define IF(c) IIF(BOOL(c))

#define EAT(...)
#define EXPAND(...) __VA_ARGS__
#define WHEN(c) IF(c)(EXPAND, EAT)

#define EMPTY()
#define DEFER(id) id EMPTY()
#define OBSTRUCT(...) __VA_ARGS__ DEFER(EMPTY)()

#ifndef NO_EVAL
#define EVAL(...) EVAL1(EVAL1(EVAL1(__VA_ARGS__)))
#define EVAL1(...) EVAL2(EVAL2(EVAL2(__VA_ARGS__)))
#define EVAL2(...) EVAL3(EVAL3(EVAL3(__VA_ARGS__)))
#define EVAL3(...) EVAL4(EVAL4(EVAL4(__VA_ARGS__)))
#define EVAL4(...) EVAL5(EVAL5(EVAL5(__VA_ARGS__)))
#define EVAL5(...) __VA_ARGS__
#else
#define EVAL(...) __VA_ARGS__
#endif

#define REPEAT(count, macro, ...)                       \
    WHEN(count)                                         \
    (                                                   \
        OBSTRUCT(REPEAT_INDIRECT)()(                    \
            DEC(count), macro, __VA_ARGS__)             \
        OBSTRUCT(macro)(DEC(count), __VA_ARGS__)        \
    )
#define REPEAT_INDIRECT() REPEAT

#define SQUARE(i, _) (i) * (i),

static const int squares[] = { EVAL(REPEAT(8, SQUARE, ~)) };

int main(void)
{
    for (size_t i = 0; i < sizeof(squares) / sizeof(squares[0]); i++)
        printf("%d ", squares[i]);
    printf("\n");
    return 0;
}
```

```
$ gcc -std=c99 -E -P repeat.c | grep "squares\[\] ="
static const int squares[] = { (0) * (0), (1) * (1), (2) * (2), (3) * (3), (4) * (4), (5) * (5), (6) * (6), (7) * (7), };
$ gcc -std=c99 -DNO_EVAL -E -P repeat.c | grep "squares\[\] ="
static const int squares[] = { REPEAT_INDIRECT ()( 6, SQUARE, ~) SQUARE (6, ~) (7) * (7), };
$ gcc -std=c11 -Wall -Wextra -pedantic repeat.c -o repeat && ./repeat
0 1 4 9 16 25 36 49
```

`-DNO_EVAL` 的版本把 `EVAL` 換成只掃一輪的 `__VA_ARGS__`，於是看得到「停在中途」的樣子：第一層 `REPEAT(8, ...)` 已經吐出 `(7) * (7),`，而下一層呼叫以 `REPEAT_INDIRECT ()(6, ...)` 的形式躺著，名稱 `REPEAT_INDIRECT` 和它的 `()` 之間在這一輪不相鄰，沒被塗藍；只要再多掃一輪，它就會展開成新的 `REPEAT(6, ...)`。這裡比原文多走一步（原文停在 `7`），因為我們的 `NO_EVAL` 版仍保留一層 `EVAL(...)` 呼叫，多貢獻了一輪掃描。

### 底層機制：為什麼「延後」能繞過 painted blue

```
第 k 輪掃描（正在替換 REPEAT，REPEAT 被停用）
   產出： ... REPEAT_INDIRECT EMPTY EMPTY () () (7, SQUARE, ~) ...
                         ↑ 名稱後面不是 ( ，不算呼叫，也不會被標記
第 k+1 輪（REPEAT 的停用脈絡已結束）
   EMPTY () → 空
   產出： ... REPEAT_INDIRECT () (7, ...) ...  → 還差一輪
第 k+2 輪
   REPEAT_INDIRECT () → REPEAT
   產出： ... REPEAT (7, ...) ...              → 這個 REPEAT 是全新的 token，可以展開
```

`WHEN(count)` 自己就會消耗一輪，所以這裡需要 `OBSTRUCT`（延後兩輪）而不是 `DEFER`（一輪）。Thomas Mailund 的〈Macro Metaprogramming〉一步步推導出同一個結論，可以當成這段的逐步解說。

### 天花板與代價

- **計數上限**：`DEC` 是查表，`count` 只能是 0–9。
- **遞迴深度上限**：`EVAL` 的掃描輪數寫死，原文以每輪延後一次的巨集實測，這個 `EVAL` 恰好讓它展開 365 輪。前置處理器仍然**不是** Turing 完備：總步數由原始碼文字決定，無法表達「直到條件成立為止」的無界迴圈。Metalang99 的 FAQ 也坦白說，它把上限定義為固定數量的歸約步驟。
- **編譯時間**：`EVAL` 不論實際需要幾輪都跑滿。我們把 `EVAL(REPEAT(8, SQUARE, ~))` 重複 1000 次、只做前置處理：

```
$ time gcc -E -P big.c -o /dev/null      # gcc 11
real	0m2.096s
$ time clang -E -P big.c -o /dev/null    # clang 14
real	0m4.622s
```

  （Ryzen 7 5700G、WSL2，數字僅供量級參考）。同樣的 1000 個常數表直接寫出來，前置處理幾乎不花時間。
- **錯誤訊息**：少一個逗號，編譯器回報的是展開十幾層後的某個內部巨集。Metalang99 建議 GCC 用 `-ftrack-macro-expansion=0`、Clang 用 `-fmacro-backtrace-limit=1` 壓抑展開回溯。
- **除錯**：巨集內無法設中斷點，只能看 `gcc -E -P`。

把這些手法系統化的是 Hirrolot 的 **Metalang99**：在前置處理器上實作一個 continuation-passing style 的直譯器，提供遞迴、自然數、串列、樣式比對、代數資料型態；其上的 **Datatype99** 提供 tagged union 與 `match`，**Interface99** 自動產生 vtable。這兩者處理的正是 Ch 29 會遇到的「vtable 要把方法名稱抄兩次」問題。

## IS_ENABLED：把「是否定義」轉成 C 運算式

Kconfig 產生的 `autoconf.h` 中，每個選項是「定義為 1」或「未定義」。`#ifdef` 能分辨，但被排除的程式碼不會進編譯器，拼錯的欄位名稱要等有人打開選項才曝光。Linux 的 `include/linux/kconfig.h` 用 `CHECK`/`PROBE` 同一招把它變成 0/1：

```c
#include <stdio.h>

#define CONFIG_FOO 1
#define CONFIG_BAR_MODULE 1
/* CONFIG_BAZ is not defined */

#define __ARG_PLACEHOLDER_1 0,
#define __take_second_arg(__ignored, val, ...) val

#define __or(x, y)              ___or(x, y)
#define ___or(x, y)             ____or(__ARG_PLACEHOLDER_##x, y)
#define ____or(arg1_or_junk, y) __take_second_arg(arg1_or_junk 1, y)

#define __is_defined(x)              ___is_defined(x)
#define ___is_defined(val)           ____is_defined(__ARG_PLACEHOLDER_##val)
#define ____is_defined(arg1_or_junk) __take_second_arg(arg1_or_junk 1, 0)

#define IS_BUILTIN(option) __is_defined(option)
#define IS_MODULE(option)  __is_defined(option##_MODULE)
#define IS_ENABLED(option) __or(IS_BUILTIN(option), IS_MODULE(option))

/* a naive version without the extra indirection */
#define NAIVE_IS_DEFINED(x) ____is_defined(__ARG_PLACEHOLDER_##x)

struct baz_stats { int hits; };

int main(void)
{
    printf("BUILTIN: FOO=%d BAR=%d BAZ=%d\n",
           IS_BUILTIN(CONFIG_FOO), IS_BUILTIN(CONFIG_BAR), IS_BUILTIN(CONFIG_BAZ));
    printf("ENABLED: FOO=%d BAR=%d BAZ=%d\n",
           IS_ENABLED(CONFIG_FOO), IS_ENABLED(CONFIG_BAR), IS_ENABLED(CONFIG_BAZ));
    printf("NAIVE  : FOO=%d\n", NAIVE_IS_DEFINED(CONFIG_FOO));
#if IS_ENABLED(CONFIG_FOO)
    puts("#if IS_ENABLED(CONFIG_FOO) works too");
#endif
    if (IS_ENABLED(CONFIG_BAZ)) {
        struct baz_stats s = { .hits = 1 };   /* still type-checked */
        printf("baz %d\n", s.hits);
    }
    return 0;
}
```

```
$ gcc -std=gnu11 -Wall -Wextra isen.c -o isen && ./isen
BUILTIN: FOO=1 BAR=0 BAZ=0
ENABLED: FOO=1 BAR=1 BAZ=0
NAIVE  : FOO=0
#if IS_ENABLED(CONFIG_FOO) works too
$ gcc -E -P isen.c | grep -A1 "BUILTIN:"
    printf("BUILTIN: FOO=%d BAR=%d BAZ=%d\n",
           1, 0, 0);
```

逐層展開 `IS_BUILTIN(CONFIG_FOO)`：

```
__is_defined(CONFIG_FOO)
→ ___is_defined(1)                  引數不與 ## 相鄰，先展開：CONFIG_FOO → 1
→ ____is_defined(__ARG_PLACEHOLDER_1)
→ __take_second_arg(0, 1, 0)        __ARG_PLACEHOLDER_1 展開出 "0,"，多了一個逗號
→ 1                                 第二個引數換成 1

__is_defined(CONFIG_BAZ)            CONFIG_BAZ 沒定義，展開不動
→ ____is_defined(__ARG_PLACEHOLDER_CONFIG_BAZ)
→ __take_second_arg(__ARG_PLACEHOLDER_CONFIG_BAZ 1, 0)   沒有多出逗號
→ 0
```

`NAIVE_IS_DEFINED(CONFIG_FOO)` 得到 0：少了 `__is_defined` 那一層，`##` 抑制了引數展開，拼出來的是 `__ARG_PLACEHOLDER_CONFIG_FOO`。反過來，`IS_MODULE` 的 `option##_MODULE` **刻意**利用抑制，才會檢查 `CONFIG_FOO_MODULE` 而不是 `1_MODULE`。

`if (IS_ENABLED(CONFIG_BAZ))` 的區塊在 `-O2` 下被完全刪除（組語中搜尋不到 `baz`），但它仍經過語法與型別檢查。Linux 的 `coding-style.rst` 因此建議優先寫 `if (IS_ENABLED(...))` 而非 `#ifdef`；限制是區塊內參照的符號在選項關閉時必須仍然存在。以 `-std=c11 -pedantic` 編譯時會得到 "ISO C99 requires at least one argument for the "..." in a variadic macro"，因為未定義路徑下 `__take_second_arg` 只拿到兩個引數；核心用 `-std=gnu11` 編譯，不受影響。

## C23 的前置處理器新特徵

C23 把數項長年仰賴擴充的功能納入標準。條款依 N3220，以下以 Docker `gcc:15` 搭配 `-std=c23` 實測。

### `#embed`：二進位檔案直接變陣列初始化子

過去要把字型、韌體、圖示嵌進程式，得先 `xxd -i` 產生 C 檔，或在組合語言用 `.incbin`。C23 6.10.4 的 `#embed` 把檔案內容展開成逗號分隔的整數常數，並有四個標準參數：`limit`（6.10.4.2）、`suffix`/`prefix`（資源非空時附加，6.10.4.3、6.10.4.4）、`if_empty`（資源為空時取代，6.10.4.5）。

```c
#include <stdio.h>
static const char head[] = {
#embed "msg.bin" limit(4) suffix(, 0)
};
static const unsigned char blob[] = {
#embed "empty.bin" prefix(0xEF, ) if_empty(0xFF)
};
#if __has_embed("msg.bin") == __STDC_EMBED_FOUND__ && \
    __has_embed("empty.bin") == __STDC_EMBED_EMPTY__ && !__has_embed("none.bin")
#define CHECK "ok"
#endif
int main(void)
{
    printf("%zu %s %zu %#x %s\n", sizeof head, head, sizeof blob, blob[0], CHECK);
}
```

`msg.bin` 內容是 `Hello`（5 位元組），`empty.bin` 是空檔：

```
$ gcc -std=c23 -Wall embed.c -o embed && ./embed       # GCC 15.3
5 Hell 1 0xff ok
$ gcc -std=c23 -E -P emb2.c                            # #embed "msg.bin" limit(3) prefix(-1, ) suffix(, -2)
int x[] = {
                                 -1, 72,101,108 , -2
};
```

`head` 只取 4 位元組再補 `0`；`blob` 因檔案為空，`prefix` 被捨棄，只剩 `if_empty` 的 `0xFF`。`__has_embed` 回傳 `__STDC_EMBED_NOT_FOUND__`/`FOUND`/`EMPTY`，即 0/1/2。依 GCC 15 變更說明與 Clang 的 C 支援狀態頁，`#embed` 自 GCC 15、Clang 19 起提供；我們的 gcc 11 與 clang 14 都不支援，舊工具鏈仍要 `xxd -i`。

除了方便，`#embed` 也解決了效能問題：把數 MB 的檔案轉成 `0x48, 0x65, ...` 文字再讓編譯器解析，記憶體與時間都很可觀；實作可以在內部直接搬位元組，不必真的產生文字（這是 WG14 提案 N3017 的主要動機之一，實際加速幅度依實作而定）。

### `#elifdef`、`#elifndef` 與 `#warning`

```c
#ifdef USE_EPOLL
#  define BACKEND "epoll"
#elifdef USE_KQUEUE
#  define BACKEND "kqueue"
#elifndef NO_POLL_FALLBACK
#  define BACKEND "poll"
#  warning "falling back to poll(2)"
#else
#  error "no I/O backend"
#endif
const char *backend = BACKEND;
```

```
$ gcc -std=c23 -E -P elif.c
elif.c:7:4: warning: #warning "falling back to poll(2)" [-Wcpp]
const char *backend = "poll";
$ gcc -std=c23 -DUSE_KQUEUE -E -P elif.c
const char *backend = "kqueue";
$ gcc -std=c23 -DNO_POLL_FALLBACK -E -P elif.c
elif.c:9:4: error: #error "no I/O backend"
$ gcc -std=c17 -DUSE_KQUEUE -E -P elif.c
elif.c:9:4: error: #error "no I/O backend"
const char *backend = BACKEND;
```

最後一個結果是陷阱：`-std=c17` 下 GCC 15 把被略過群組裡的 `#elifdef` 當成未知指引忽略（C17 本來就規定略過群組中的未知指引不處理），於是一路落到 `#else` 的 `#error`。原文以 GCC 16 得到相同結果，並指出 Clang 21 在同樣情況會警告後照樣選出 `"kqueue"`。**用了 C23 指引的程式碼不要搭配 `-std=c17` 編譯**。`#warning`（N3220 6.10.7）把 GCC/Clang 的老擴充標準化：只發診斷，不中止編譯。

### `__has_include` 與 `__has_c_attribute`：特徵偵測取代版本比對

舊程式碼常寫 `#if __GNUC__ >= 8` 這種版本比對，必須記住每個特徵出現在哪個編譯器的哪個版本。C23 6.10.2 提供 `__has_include` 與 `__has_c_attribute`，並規定 `#ifdef`/`defined` 視它們為已定義的巨集，所以可以為舊編譯器補上預設值：

```c
#include <stdio.h>
#ifndef __has_include
#  define __has_include(x) 0
#endif
#ifndef __has_c_attribute
#  define __has_c_attribute(x) 0
#endif
#if __has_include(<stdckdint.h>)
#  include <stdckdint.h>
#  define ADD_OVERFLOW(r, a, b) ckd_add(r, a, b)
#  define HOW "stdckdint.h"
#elif defined(__GNUC__)
#  define ADD_OVERFLOW(r, a, b) __builtin_add_overflow(a, b, r)
#  define HOW "__builtin_add_overflow"
#endif
#if __has_c_attribute(nodiscard)
#  define NODISCARD [[nodiscard]]
#else
#  define NODISCARD
#endif
NODISCARD static _Bool add(int *r, int a, int b) { return ADD_OVERFLOW(r, a, b); }

int main(void)
{
    int r;
    if (add(&r, 2147483647, 1)) puts("overflow");
    add(&r, 1, 2);
    printf("%d %ld via %s\n", r, (long) __has_c_attribute(nodiscard), HOW);
}
```

同一份原始碼在三個環境的結果：

| 編譯器 / 模式 | `add(&r, 1, 2)` 的警告 | 輸出最後一行 |
|---|---|---|
| GCC 15.3 `-std=c23` | `ignoring return value of 'add', declared with attribute 'nodiscard' [-Wunused-result]` | `3 202311 via stdckdint.h` |
| gcc 11.4 `-std=gnu17` | 同上 | `3 202003 via __builtin_add_overflow` |
| clang 14 `-std=gnu17` | 無 | `3 0 via __builtin_add_overflow` |

三種結果都是正確的程式，差在「這個環境提供什麼」。gcc 11 已經在 gnu17 模式支援 `[[nodiscard]]`，回報的是早期草案值 `202003`；C23 定稿值是 `202311L`（N3220 6.7.13.3）。原文也觀察到 Clang 21 對 `nodiscard` 回報 `202003`。所以**判斷支援與否只檢查非零**，不要寫 `>= 202311L`。`<stdckdint.h>` 的溢位檢查見 [Ch 22](./22-integer-overflow.md)。

## 應用一：String switch

C 的 `switch` 只能比對整數。lwan 網頁伺服器的作者 Leandro Pereira 的手法：長度 ≤ 4 的字串整個讀成 32 位元整數比對，`case` 標籤則用巨集在編譯時期把字元組成同樣的整數。

```c
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#if __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
#define STR4_INT(a, b, c, d) ((uint32_t)((a) | (b) << 8 | (c) << 16 | (uint32_t)(d) << 24))
#else
#define STR4_INT(d, c, b, a) ((uint32_t)((a) | (b) << 8 | (c) << 16 | (uint32_t)(d) << 24))
#endif
#define LOWER4(s) ((s) | (uint32_t)0x20202020)
#define STR4_INT_L(a, b, c, d) LOWER4(STR4_INT(a, b, c, d))

static inline uint32_t string_as_uint32(const char *s)
{
    uint32_t u;
    memcpy(&u, s, sizeof(u));   /* no alignment / aliasing problem */
    return u;
}
#define STRING_SWITCH_L(s) switch (LOWER4(string_as_uint32(s)))

static const char *mime(const char *file_name)
{
    char ext[5] = {0};                      /* pad so we may always read 4 bytes */
    const char *dot = strrchr(file_name, '.');
    if (!dot || strlen(dot) > 4)
        goto fallback;
    memcpy(ext, dot, strlen(dot));

    STRING_SWITCH_L(ext) {
    case STR4_INT_L('.', 'c', 's', 's'): return "text/css";
    case STR4_INT_L('.', 'h', 't', 'm'): return "text/html";
    case STR4_INT_L('.', 'j', 'p', 'g'): return "image/jpeg";
    case STR4_INT_L('.', 'p', 'n', 'g'): return "image/png";
    case STR4_INT_L('.', 'j', 's', 0):   return "application/javascript";
    }
fallback:
    return "application/octet-stream";
}

int main(void)
{
    const char *names[] = {"a.css", "INDEX.HTM", "pic.Jpg", "app.js", "x.png", "README", "y.jpeg"};
    for (size_t i = 0; i < sizeof names / sizeof names[0]; i++)
        printf("%-10s -> %s\n", names[i], mime(names[i]));
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra -O2 strswitch.c -o strswitch && ./strswitch
a.css      -> text/css
INDEX.HTM  -> text/html
pic.Jpg    -> image/jpeg
app.js     -> application/javascript
x.png      -> image/png
README     -> application/octet-stream
y.jpeg     -> application/octet-stream
```

幾個關鍵：

- **為什麼用 `memcpy`**：lwan 早期寫 `*((int32_t *)(s))`，有兩個問題：`s` 未必 4 位元組對齊，在要求對齊的架構上會觸發例外；以 `int32_t` 左值存取 `char` 陣列違反 N1570 6.5 §7 的有效型別規則（strict aliasing），編譯器可據此做出錯誤最佳化。`memcpy` 以 `char` 存取，兩個問題都沒有，而且 gcc 11 `-O2` 把固定 4 位元組的 `memcpy` 編成單一載入：

```
string_as_uint32:
	movl	(%rdi), %eax
	ret
```

- **`| 0x20202020` 轉小寫**：ASCII 大小寫字母只差 0x20 這個位元，所以 `.HTM` 與 `.htm` 得到同一個整數。代價是非字母字元也被改：結尾的 `'\0'` 變成 `0x20`，所以 `.js` 的第 4 個位元組寫 `0`，經 `STR4_INT_L` 一樣被 OR 成 `0x20`，兩邊仍相等。
- **越界讀取**：`memcpy` 解決對齊與 aliasing，**不解決**越界。無論字串多長，一律讀 4 位元組。這裡先把副檔名複製進 5 位元組的 `ext` 緩衝區；lwan 則要求呼叫端保證緩衝區夠長。
- **位元組順序**：`STR4_INT` 依 `__BYTE_ORDER__` 調整參數順序，讓 `case` 常數與 `memcpy` 讀到的記憶體內容一致。

## 應用二：產生泛型 ADT

klib 的 `ksort.h` 以 `KSORT_INIT(name, type, lt)` 產生型別專屬的排序函式，比較運算直接展開在迴圈中，不必像 `qsort` 每次都經由函式指標呼叫。以下是同一思路的縮小版，一個巨集產生動態陣列，另一個產生快速排序：

```c
#define DEFINE_VEC(name, T)                                                \
    typedef struct { T *data; size_t len, cap; } name;                    \
    static inline int name##_push(name *v, T x)                           \
    {                                                                      \
        if (v->len == v->cap) {                                            \
            size_t ncap = v->cap ? v->cap * 2 : 8;                         \
            T *p = realloc(v->data, ncap * sizeof(T));                     \
            if (!p) return -1;                                             \
            v->data = p, v->cap = ncap;                                    \
        }                                                                  \
        v->data[v->len++] = x;                                             \
        return 0;                                                          \
    }                                                                      \
    static inline void name##_free(name *v) { free(v->data); *v = (name){0}; }

#define DEFINE_SORT(name, T, lt)                                           \
    static void name##_sort(T *a, size_t n)                                \
    {                                                                      \
        while (n > 16) {                                                   \
            T pivot = a[n / 2];                                            \
            size_t i = 0, j = n - 1;                                       \
            for (;;) {                                                     \
                while (lt(a[i], pivot)) i++;                               \
                while (lt(pivot, a[j])) j--;                               \
                if (i >= j) break;                                         \
                T t = a[i]; a[i] = a[j]; a[j] = t;                         \
                i++, j--;                                                  \
            }                                                              \
            name##_sort(a, j + 1);                                         \
            a += j + 1, n -= j + 1;                                        \
        }                                                                  \
        for (size_t i = 1; i < n; i++) {                                   \
            T x = a[i]; size_t k = i;                                      \
            while (k > 0 && lt(x, a[k - 1])) { a[k] = a[k - 1]; k--; }     \
            a[k] = x;                                                      \
        }                                                                  \
    }

#define INT_LT(a, b) ((a) < (b))
DEFINE_VEC(IntVec, int)
DEFINE_SORT(int, int, INT_LT)

typedef struct { const char *name; double score; } Rec;
#define REC_LT(a, b) ((a).score > (b).score)   /* descending by score */
DEFINE_VEC(RecVec, Rec)
DEFINE_SORT(rec, Rec, REC_LT)
```

`main` 先排三筆 `Rec`，再拿 500 萬個 `rand()` 整數和 glibc `qsort` 比較（完整檔案含計時函式 `now()` 與 `cmp_int`，`-O2`，各跑 3 次）：

```
bob    91.00
carol  85.25
alice  72.50
gcc 11 : macro sort 0.314 s, qsort 0.605 s, same=1
         macro sort 0.301 s, qsort 0.599 s, same=1
         macro sort 0.297 s, qsort 0.568 s, same=1
clang 14: macro sort 0.294 s, qsort 0.585 s, same=1   (其餘兩次 0.299/0.589、0.310/0.569)
```

約快一倍，但這個數字要誠實解讀：glibc 2.35 的 `qsort` 在記憶體足夠時其實是**合併排序**，演算法本身就不同；差距來自「比較函式經指標呼叫、無法行內展開」與「演算法差異」兩者之和，這個實驗沒有把它們分開。要單獨量比較函式的成本，得用同一演算法寫一個接受函式指標的版本對照（本章「動手練習」第 3 題）。

代價也和 C++ template 一樣：每具現化一次就多一份程式碼。原文以 OpenBSD 的 `RB_GENERATE` 實測，一份 2064 位元組、兩種節點型別就成為 4128 位元組；OpenBSD 後來加入 `RBT_*` 介面，把演算法搬回一般函式、巨集只產生型別安全的包裝，這是「程式碼大小」與「行內比較」之間的取捨。

## 應用三：用巨集包出語法：Unit test、scope guard、exception

### Unit test

前置處理器讓 C 的測試框架省去大量樣板。Unity 的 `unity_fixture.h`：

```c
#define TEST(group, name) \
    void TEST_##group##_##name##_(void);\
    void TEST_##group##_##name##_run(void);\
    void TEST_##group##_##name##_run(void)\
    {\
        UnityTestRunner(TEST_##group##_SETUP,\
            TEST_##group##_##name##_,\
            ...
```

寫 `TEST(Stack, PushThenPop) { ... }`，巨集宣告並定義 `TEST_Stack_PushThenPop_run`，大括號內的本體則成為 `TEST_Stack_PushThenPop_` 的函式本體。snow 更進一步用 `__attribute__((constructor))` 讓每個測試在 `main` 之前自動註冊。[練習 D](./practice-d-unit-test-framework.md) 會從零做一個：`TEST()` 自動註冊、`ASSERT_EQ` 以 `_Generic` 依型別印出期望值與實際值。

### 用 `for` 迴圈包住區塊

Serge Zaitsev 的〈Syntactic sugar in C: (ab)using "for" loops〉：`for` 的初始化跑一次、條件每輪前檢查、遞增每輪後執行，所以只跑一輪的 `for` 能在使用者的區塊**前後**各插入程式碼：

```c
#include <stdio.h>

static void tag_open(const char *t) { printf("<%s>", t); }
static void tag_close(const char *t) { printf("</%s>", t); }

#define TAG(t)                                       \
    for (int _once = (tag_open(t), 1); _once;        \
         _once = 0, tag_close(t))

int main(void)
{
    TAG("ul") {
        TAG("li") printf("one");
        TAG("li") printf("two");
    }
    printf("\n");
    TAG("p") { printf("early"); break; }
    printf("\n");
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra -pedantic scope.c -o scope && ./scope
<ul><li>one</li><li>two</li></ul>
<p>early
```

第二行缺了 `</p>`：區塊內的 `break` 跳出這個隱形迴圈，**遞增部分不執行**，收尾動作就被跳過。若收尾是 `mutex_unlock` 或 `fclose`，這就是資源洩漏。區塊內的 `continue` 也只作用於隱形迴圈，而非外層迴圈。這是這種語法糖的根本限制，Linux 的 `scoped_guard()` 改用 `__attribute__((cleanup))`（見 [Ch 31](./31-goto-and-control-flow.md) 的「從 goto 到 cleanup.h」）。

### 用 `setjmp`/`longjmp` 做 try/catch

已封存的 libextc 用一串巢狀 `for` 宣告變數再呼叫 `setjmp`。以下是一個可運作的小版本，例外框架以鏈結串列形成堆疊，支援巢狀與重新拋出：

```c
#include <setjmp.h>
#include <stdio.h>

struct ex_frame {
    jmp_buf env;
    struct ex_frame *prev;
    int code;
};
static struct ex_frame *ex_top;

#define TRY                                                   \
    do {                                                      \
        struct ex_frame _ex_f = { .prev = ex_top };           \
        ex_top = &_ex_f;                                      \
        if (setjmp(_ex_f.env) == 0) {
#define CATCH(var)                                            \
            ex_top = _ex_f.prev;                              \
        } else {                                              \
            int var = _ex_f.code;                             \
            ex_top = _ex_f.prev;
#define END_TRY                                               \
        }                                                     \
    } while (0)

static void THROW(int code)
{
    if (!ex_top) {
        fprintf(stderr, "uncaught exception %d\n", code);
        return;
    }
    ex_top->code = code;
    longjmp(ex_top->env, 1);
}

static int parse_digit(char c)
{
    if (c < '0' || c > '9')
        THROW(c);
    return c - '0';
}

int main(void)
{
    const char *inputs[] = {"7", "x", "3"};
    for (int i = 0; i < 3; i++) {
        TRY {
            printf("digit %d\n", parse_digit(inputs[i][0]));
        } CATCH(e) {
            printf("caught: bad char '%c'\n", e);
        } END_TRY;
    }
    TRY {
        TRY {
            THROW(1);
        } CATCH(e) {
            printf("inner caught %d, rethrow\n", e);
            THROW(e + 1);
        } END_TRY;
    } CATCH(e) {
        printf("outer caught %d\n", e);
    } END_TRY;
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra -pedantic try.c -o try && ./try
digit 7
caught: bad char 'x'
digit 3
inner caught 1, rethrow
outer caught 2
```

注意三件事：

1. `setjmp` 只能出現在 N1570 7.13.1.1 §4 列出的語境：選擇或迭代敘述的整個控制運算式、與整數常數比較的一方、`!` 的運算元、或整個運算式敘述。這裡寫成 `if (setjmp(...) == 0)` 符合規定；libextc 把 `setjmp` 放在 `for` 的變數初始化中，嚴格說是未定義行為，只是主流編譯器上能跑。
2. `TRY` 區塊內修改、之後在 `CATCH` 中讀取的 automatic 變數，若沒有宣告成 `volatile`，`longjmp` 之後其值不確定（7.13.2.1 §3）。
3. `TRY` 中 `return` 或 `break` 會跳過 `ex_top = _ex_f.prev`，留下一個指向已消失 stack frame 的懸置指標。巨集包出的語法看起來像語言內建，但沒有語言保證；`setjmp`/`longjmp` 的底層見 [Ch 12](./12-setjmp-and-coroutines.md)。

### 複合字面常數的靜態串列

```c
#define cons(x, y) (struct llist[]){{x, y}}
struct llist { int val; struct llist *next; };
    struct llist *list = cons(9, cons(5, cons(4, cons(7, NULL))));
```

輸出 `9547`。`cons` 用 C99 複合字面常數（compound literal，N1570 6.5.2.5）建立只有一個元素的匿名陣列，陣列在運算式中退化為指標，恰好可當上一個節點的 `next`。函式內的複合字面常數生命週期與所在區塊相同（6.5.2.5 §5），所以這樣建立的串列**不能從函式回傳**。

## Linux 核心：`module_init` 與連結器區段

驅動程式寫 `module_init(foo_init)`，核心開機時卻不需要一張手寫的總表。秘訣：前置處理器產生帶 `section` 屬性的變數，連結器把散落各檔的變數收集成連續陣列。核心中 `device_initcall(fn)` 最後展開為（`include/linux/init.h`，一般組態）：

```c
#define ____define_initcall(fn, __unused, __name, __sec)	\
	static initcall_t __name __used 			\
		__attribute__((__section__(__sec))) = fn;
```

區段名稱是 `.initcall6.init`，變數名以 `__COUNTER__`、`__LINE__` 與模組名接成唯一名稱；連結器腳本 `vmlinux.lds.h` 依等級排列區段並定義 `__initcall6_start` 等邊界符號，`init/main.c` 走訪邊界之間的陣列。下面在使用者空間重現同一機制。GNU ld 對**名稱是合法 C 識別字**的區段，會自動定義 `__start_<name>` 與 `__stop_<name>`：

```c
/* initcall.h */
#ifndef INITCALL_H
#define INITCALL_H
typedef int (*initcall_t)(void);
struct initcall { initcall_t fn; const char *name; int level; };

#define __PASTE_(a, b) a##b
#define __PASTE(a, b)  __PASTE_(a, b)
/* one static struct per registration, placed in section "my_initcalls" */
#define define_initcall(fn, lvl)                                        \
    static const struct initcall __PASTE(__initcall_, __COUNTER__)      \
    __attribute__((used, section("my_initcalls"), aligned(8))) =         \
        { fn, #fn, lvl }
#define core_initcall(fn)   define_initcall(fn, 1)
#define device_initcall(fn) define_initcall(fn, 6)
#endif
```

```c
/* drv_uart.c */
#include <stdio.h>
#include "initcall.h"
static int uart_init(void) { puts("  uart: probed"); return 0; }
device_initcall(uart_init);
```

```c
/* drv_gpio.c */
#include <stdio.h>
#include "initcall.h"
static int gpio_init(void) { puts("  gpio: probed"); return 0; }
static int clk_init(void)  { puts("  clk : core clock up"); return 0; }
device_initcall(gpio_init);
core_initcall(clk_init);
```

```c
/* main_init.c */
#include <stdio.h>
#include "initcall.h"
/* GNU ld defines these for any section whose name is a valid C identifier */
extern const struct initcall __start_my_initcalls[], __stop_my_initcalls[];

int main(void)
{
    size_t n = __stop_my_initcalls - __start_my_initcalls;
    printf("%zu initcalls found\n", n);
    for (int level = 0; level <= 7; level++)
        for (const struct initcall *c = __start_my_initcalls; c < __stop_my_initcalls; c++)
            if (c->level == level) {
                printf("level %d: %s\n", level, c->name);
                c->fn();
            }
    return 0;
}
```

```
$ gcc -std=gnu11 -Wall -Wextra main_init.c drv_uart.c drv_gpio.c -o initdemo && ./initdemo
3 initcalls found
level 1: clk_init
  clk : core clock up
level 6: uart_init
  uart: probed
level 6: gpio_init
  gpio: probed
$ objdump -h initdemo | grep -A1 my_initcalls
 25 my_initcalls  00000048  0000000000004010  0000000000004010  00003010  2**3
$ nm initdemo | grep -E 'start_my|stop_my'
0000000000004010 D __start_my_initcalls
0000000000004058 D __stop_my_initcalls
```

區段大小 0x48 = 72 = 3 × `sizeof(struct initcall)`（8 + 8 + 4 + 4 填充 = 24）。`main_init.c` 完全不知道有哪些驅動程式；新增一個 `.c` 檔、連進來，它就出現在陣列裡。`aligned(8)` 確保每筆紀錄在區段中以結構大小緊密排列（否則編譯器可能為某些物件加大對齊，在區段中留下空洞，走訪時就讀錯）；`used` 防止編譯器把沒人參照的 `static` 變數刪掉。核心連結器腳本另外用 `KEEP()` 防止 `--gc-sections` 把這些區段當成死碼。lwan 的 `LWAN_HANDLER_ROUTE`、Zephyr 的 `SYS_INIT`、練習 D 的測試註冊都用同一手法。連結器怎麼合併區段，見 [Ch 39](./39-static-linking.md)。

## 其他開源專案的前置處理器應用

### CPython：同一份指令本體，三種分派方式

CPython 的指令語意寫在 `Python/bytecodes.c`，由腳本產生 `generated_cases.c.h`，每道指令以 `TARGET(op)` 開頭。`Python/ceval_macros.h` 依組態把它展開成三種完全不同的控制流程：

```c
#if _Py_TAIL_CALL_INTERP
#   define TARGET(op) Py_NO_INLINE PyObject *Py_PRESERVE_NONE_CC _TAIL_CALL_##op(TAIL_CALL_PARAMS)
#elif USE_COMPUTED_GOTOS
#  define TARGET(op) TARGET_##op:
#  define DISPATCH_GOTO() goto *opcode_targets[opcode]
#else
#  define TARGET(op) case op: TARGET_##op:
#  define DISPATCH_GOTO() dispatch_code = opcode | tracing_mode ; goto dispatch_opcode
#endif
```

可攜版展開成 `case` 標籤；支援 labels as values 時是 computed goto 的跳躍目標；Python 3.14 起的第三種模式把每道指令變成獨立函式、以 `musttail` 串接。指令語意只寫一次，分派策略的切換完全由巨集承擔。三種分派的效能比較在 [Ch 31](./31-goto-and-control-flow.md) 實測。

### SQLite：為測試覆蓋率服務的巨集

SQLite 以 MC/DC 等級的分支覆蓋率聞名，`src/sqliteInt.h` 中：

```c
#if defined(SQLITE_COVERAGE_TEST) || defined(SQLITE_DEBUG)
# define testcase(X)  if( X ){ sqlite3CoverageCounter += (unsigned)__LINE__; }
#else
# define testcase(X)
#endif
...
#if defined(SQLITE_OMIT_AUXILIARY_SAFETY_CHECKS)
# define ALWAYS(X)      (1)
# define NEVER(X)       (0)
#elif !defined(NDEBUG)
# define ALWAYS(X)      ((X)?1:(assert(0),0))
# define NEVER(X)       ((X)?(assert(0),1):0)
#else
# define ALWAYS(X)      (X)
# define NEVER(X)       (X)
#endif
```

- `testcase(X)` 正式版是空的；覆蓋率測試時變成帶副作用的分支，逼測試套件讓 `X` 真假都出現，適合標記邊界值。
- `ALWAYS`/`NEVER` 包住「理論上必真/必假」的防禦性檢查，**同一行程式碼在三種組態有三種意義**：除錯版觸發 `assert`、暴露邏輯錯誤；正式版保留檢查、意外時回傳錯誤而非當機；覆蓋率版硬編碼為常數，無法觸及的防禦分支不被算成未測試。例如 `btree.c` 的 `if( NEVER(nShift>nCell) ) return SQLITE_CORRUPT_BKPT;`。

### BSD 的 `<sys/queue.h>`：全由巨集構成的串列

glibc 也附一份（較舊的 4.4BSD 版本，沒有 FreeBSD 後加的 `_SAFE` 走訪巨集）：

```c
#include <stdio.h>
#include <sys/queue.h>

struct task {
    int id;
    LIST_ENTRY(task) all;      /* every task */
    TAILQ_ENTRY(task) run;     /* run queue */
};

LIST_HEAD(task_list, task);
TAILQ_HEAD(run_queue, task);

int main(void)
{
    struct task t[4] = {{.id = 1}, {.id = 2}, {.id = 3}, {.id = 4}};
    struct task_list all = LIST_HEAD_INITIALIZER(all);
    struct run_queue rq = TAILQ_HEAD_INITIALIZER(rq);
    struct task *p;

    for (int i = 0; i < 4; i++) {
        LIST_INSERT_HEAD(&all, &t[i], all);
        if (t[i].id % 2)
            TAILQ_INSERT_TAIL(&rq, &t[i], run);
    }
    LIST_REMOVE(&t[3], all);

    LIST_FOREACH(p, &all, all)
        printf("%d ", p->id);
    printf("| ");
    TAILQ_FOREACH(p, &rq, run)
        printf("%d ", p->id);
    printf("| last=%d\n", TAILQ_LAST(&rq, run_queue)->id);
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra queue.c -o queue && ./queue
3 2 1 | 1 3 | last=3
$ gcc -E -P queue.c | grep -A3 "^struct task {"
struct task {
    int id;
    struct { struct task *le_next; struct task **le_prev; } all;
    struct { struct task *tqe_next; struct task * *tqe_prev; } run;
```

和 Linux `list_head` 的差別一目了然：`LIST_ENTRY(task)` 展開成匿名結構，`le_next` 的型別直接是 `struct task *`，走訪不需要 `container_of`，型別檢查交給編譯器。代價是每種元素型別要各自宣告串列頭，所有操作只能是巨集，無法有一個 `list_sort()` 服務所有型別。`le_prev` 是指向「前一個元素的 `le_next`」的指標的指標，所以 `LIST_REMOVE` 不需要串列頭參數，刪除第一個元素也不用特例，這就是 [Ch 18](./18-linked-list-pointer-to-pointer.md) 的間接指標技巧。巨集參數 `all` 同時當變數名與成員名也沒問題：前置處理器只換 token，名稱查找由編譯器依位置分別處理。

### Zephyr：devicetree 變成編譯時期常數

Linux 開機時解析 devicetree blob，Zephyr 則在建置時由 `gen_defines.py` 把每個節點與屬性寫成巨集，API 只是在拼接名稱。以下依其命名規則手寫一段「產生檔」測試：

```c
/* --- what gen_defines.py would emit (hand-written subset) --- */
#define DT_N_S_soc_S_serial_40001000_P_current_speed 115200
#define DT_N_NODELABEL_serial1 DT_N_S_soc_S_serial_40001000
#define DT_FOREACH_OKAY_INST_vnd_uart(fn) fn(0) fn(1)
#define DT_N_INST_0_vnd_uart DT_N_S_soc_S_serial_40001000
#define DT_N_INST_1_vnd_uart DT_N_S_soc_S_serial_40002000
#define DT_N_S_soc_S_serial_40002000_P_current_speed 9600

/* --- devicetree.h style API: only token pasting --- */
#define DT_CAT(a1, a2) a1 ## a2
#define DT_CAT3(a1, a2, a3) a1 ## a2 ## a3
#define DT_NODELABEL(label) DT_CAT(DT_N_NODELABEL_, label)
#define DT_PROP(node_id, prop) DT_CAT3(node_id, _P_, prop)
#define DT_PROP_BAD(node_id, prop) node_id ## _P_ ## prop
#define DT_DRV_INST(n) DT_CAT3(DT_N_INST_, n, _vnd_uart)
#define DT_INST_FOREACH_STATUS_OKAY(fn) DT_FOREACH_OKAY_INST_vnd_uart(fn)

int baud = DT_PROP(DT_NODELABEL(serial1), current_speed);
#define UART_DEFINE(n) static const int uart_baud_##n = DT_PROP(DT_DRV_INST(n), current_speed);
DT_INST_FOREACH_STATUS_OKAY(UART_DEFINE)
```

```
$ gcc -E -P dt.c
int baud = 115200;
static const int uart_baud_0 = 115200; static const int uart_baud_1 = 9600;
$ gcc -DBAD -E -P dt.c      # int bad = DT_PROP_BAD(DT_NODELABEL(serial1), current_speed);
dt.c:23:43: error: pasting ")" and "_P_" does not give a valid preprocessing token
```

`DT_PROP` 多一層 `DT_CAT3` 的理由和 `IS_ENABLED` 一模一樣：`node_id` 不與 `##` 相鄰才會先展開成 `DT_N_S_soc_S_serial_40001000`；`DT_PROP_BAD` 直接 `##`，拿到的是未展開的 `DT_NODELABEL(serial1)`，拼接 `)` 與 `_P_` 失敗。板子上有幾個啟用的節點，`DT_INST_FOREACH_STATUS_OKAY` 就展開出幾組 `_0`、`_1` 物件；設定錯誤在編譯時期以未定義識別字現身，而不是執行時期查詢失敗。

### GLib 的 `G_DEFINE_TYPE`

`G_DEFINE_TYPE(MyCounter, my_counter, G_TYPE_OBJECT)` 一行展開成 `my_counter_get_type()`（以 `g_once_init_enter` 保證多執行緒下只註冊一次）、`class_intern_init`（先填好 `parent_class` 再呼叫使用者的 `class_init`）與兩個 `init` 函式的 `static` 原型。型別名稱要以 CamelCase 與 snake_case 兩種寫法傳入，因為前置處理器沒有大小寫轉換能力。`_G_DEFINE_TYPE_EXTENDED_BEGIN` 結尾留下一個未閉合的 `{`，讓 `G_ADD_PRIVATE` 這類片段落在註冊函式內、看得見區域變數 `g_define_type_id`，刻意違背巨集衛生換取可組合性。GObject 的物件模型在 [Ch 30](./30-object-models-in-the-wild.md) 詳談。

> **本節的 CPython、SQLite、GLib 片段為原始碼摘錄**，未在本機建置這些專案；`queue.c`、`dt.c`、`initdemo` 是實測過的縮小版。

## 對比與取捨

| 需求 | 前置處理器 | 外部程式碼產生器（Python 腳本、`gen_defines.py`） | 手寫 |
|---|---|---|---|
| 建置相依 | 無，任何 C 編譯器 | 需要額外工具與建置規則 | 無 |
| 表達能力 | 有上限的遞迴、只有 token 操作 | 任意 | 任意 |
| 錯誤訊息 | 差 | 可以自訂得很好 | 最好 |
| 可讀性 | X macro 尚可；元程式設計很差 | 產生檔可讀 | 最好 |
| 典型用途 | X macro、`IS_ENABLED`、ADT 具現化 | CPython 指令、Zephyr devicetree、Linux syscall 表 | 項目少且不常變 |

注意 CPython 與 Zephyr 都是**兩者並用**：外部腳本產生巨集或資料，前置處理器負責把它組進 C。當巨集需要 `EVAL` 等級的技巧時，通常是改用外部產生器的訊號。

## 踩雷集錦

1. **以為 `PP_NARG` 能數任意個引數**：超過表格上限時不報錯、給錯答案；`PP_NARG()` 回報 1。重要場合要配合 `_Static_assert` 或改用 C23 的 `__VA_OPT__` 判空。
2. **巢狀巨集少一層間接**：`DT_PROP_BAD`、`NAIVE_IS_DEFINED` 都是同一個錯。凡是 `##` 或 `#` 的運算元可能是巨集呼叫，就加一層 `CAT`/`STR`。
3. **`for` 迴圈語法糖裡用 `break`**：收尾不執行，`continue` 也被隱形迴圈吃掉。需要可靠收尾時改用 `__attribute__((cleanup))`。
4. **用 `-std=c17` 編譯含 `#elifdef` 的程式碼**：GCC 靜悄悄地選錯分支（我們的實測落到 `#error`）。新指引要配合 `-std=c23` 或 `gnu` 模式。
5. **把 `__has_c_attribute(x)` 和 `202311L` 比大小**：gcc 11 回報 `202003`、clang 14 回報 0、GCC 15 回報 `202311`，只檢查非零。
6. **以為 `memcpy` 讀 4 位元組就安全**：它只解決對齊與 strict aliasing，不解決越界讀取；string switch 的呼叫端要保證緩衝區長度。
7. **`section` 屬性的區段名稱含 `.`**：GNU ld 只為名稱是合法 C 識別字的區段產生 `__start_`/`__stop_`，取名 `.my_initcalls` 就拿不到邊界符號（Linux 核心因此自己在連結器腳本中定義邊界）。

## 進階：再往深一層

- **Boost.Preprocessor**（C 也能用）：提供 array、list、sequence `(a)(b)(c)`、tuple 四種資料結構與 `BOOST_PP_SEQ_FOR_EACH` 等演算法，是元程式設計工具組的業界標準。Klemens Morgenstern 在 Embedded Artistry 的客座文章〈Exploiting the Preprocessor for Fun and Profit〉以嵌入式開發者角度介紹它。
- **P99**（Jens Gustedt）：以 C99 前置處理器提供預設引數、`P99_FOR` 等，作者是 WG14 成員。
- **`#line` 與程式碼產生器**：yacc/bison 產生的 `.c` 檔用 `#line` 讓錯誤訊息指回 `.y` 原始檔；自己寫產生器時也該這樣做。
- **`_Pragma` 運算子**：`#pragma` 不能出現在巨集中，`_Pragma("GCC diagnostic ignored \"-Wformat\"")` 可以。Linux 的 `__diag_push`/`__diag_ignore` 用它在巨集內局部關閉警告。

## 動手練習

1. 用 X macro 為一個狀態機（`IDLE`、`CONNECTING`、`READY`、`CLOSED`）產生 `enum`、`state_name()` 與一張 `next_state[STATE_COUNT][EVENT_COUNT]` 轉移表的骨架。故意在清單中新增一個狀態，確認所有衍生物都自動更新。
2. 把 `PP_NARG` 擴充到 16，並寫 `PP_IS_EMPTY(...)`：用 C23 `__VA_OPT__(0) 1` 的組合在 `gcc:15 -std=c23` 下判斷可變引數是否為空。
3. 寫一個接受函式指標比較器、但演算法與 `DEFINE_SORT` 完全相同的 `ptr_sort(int *, size_t, int (*)(const void *, const void *))`，和 `int_sort` 比較，把「行內比較」的效益從「演算法差異」中分離出來。
4. 在 `initdemo` 加上 `-Wl,--gc-sections -ffunction-sections -fdata-sections` 重新連結，觀察 `my_initcalls` 是否消失；思考 `used` 屬性與連結器的 `KEEP()` 各自保護的是哪一層。

## 本章重點整理

- X macro 讓多份衍生物由單一清單產生；Linux 的 `TRACE_EVENT` 把清單擴大到整個標頭檔、反覆引入。
- 計數與多載靠「倒數序列 + 取固定位置」與 `##` 拼名稱；條件靠 `##` 查表；遞迴靠延後展開與 `EVAL` 強迫重掃，上限由 `EVAL` 深度決定，前置處理器仍非 Turing 完備。
- `##`/`#` 的運算元可能是巨集時一定要多一層間接，`IS_ENABLED` 與 Zephyr `DT_PROP` 都依賴這點。
- C23 以 `__VA_OPT__`、`#embed`、`#elifdef`、`#warning`、`__has_include`、`__has_c_attribute` 取代 `, ##`、`xxd -i`、版本比對等擴充；嚴格模式與屬性版本值要小心。
- 連結器區段 + `__start_`/`__stop_` 讓註冊表不需要中央清單。
- 代價是編譯時間、錯誤訊息與除錯能力；複雜到需要 `EVAL` 時，考慮外部產生器。

## 自我檢核

- [ ] 不看筆記，能寫出一個 X macro，並說明它相對手寫多份表格解決了什麼問題
- [ ] 能逐步推導 `PP_NARG(a, b, c)` 的展開，並解釋為什麼需要 `PP_NARG_` 這一層
- [ ] 能用自己的話解釋 painted blue，以及 `OBSTRUCT` + `EVAL` 為什麼能繞過它、為什麼仍有上限
- [ ] 能逐步展開 `IS_ENABLED(CONFIG_FOO)` 與未定義的情形，並指出拿掉 `__is_defined` 那層會發生什麼
- [ ] 如果有人問你「C23 的前置處理器加了什麼」，能說出四項以上並各舉一個它取代的舊做法
- [ ] 能解釋 string switch 中 `memcpy` 解決了哪兩個問題、沒解決哪一個
- [ ] 能說明 Linux initcall 從 `module_init` 到 `do_initcall_level` 的完整鏈條，並在使用者空間重現

## 延伸閱讀

### 官方文件 / Spec

- **[N3220（C23 後的工作草案）](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n3220.pdf)**
  - **讀哪裡**：6.10.4「Binary resource inclusion」（`#embed` 的全部參數與 EXAMPLE）、6.10.5.1「Argument substitution」（`__VA_OPT__`，看 EXAMPLE 2 的初始化子用法）、6.10.2「Conditional inclusion」（`__has_include` 的 EXAMPLE 2，即本章補預設值的寫法）
  - **前提知識**：Ch 27 的展開規則

- **[GCC 手冊：Variadic Macros](https://gcc.gnu.org/onlinedocs/cpp/Variadic-Macros.html) 與 [Common Variable Attributes: section](https://gcc.gnu.org/onlinedocs/gcc/Common-Variable-Attributes.html)**
  - **讀哪裡**：前者說明 `, ##` 與 `__VA_OPT__` 在 GCC 中的語意；後者的 `section` 與 `used` 段落是 initcall 手法的依據
  - **為什麼值得讀**：擴充的確切語意只有官方手冊寫得準

### 部落格 / 技術文章

- **[C Preprocessor tricks, tips, and idioms](https://github.com/pfultz2/Cloak/wiki/C-Preprocessor-tricks,-tips,-and-idioms)** — Paul Fultz II（Cloak wiki）
  - **讀哪裡**：整篇，依序是 Pattern Matching → Detection → Recursion；本章 `REPEAT` 範例取自這裡
  - **能學到什麼**：每個工具巨集的設計理由，以及 `DEFER`/`OBSTRUCT` 為什麼要延後一輪或兩輪
  - **前提知識**：Ch 27 的 painted blue

- **[Macros on Steroids, Or: How Can Pure C Benefit From Metaprogramming](https://hirrolot.github.io/posts/macros-on-steroids-or-how-can-pure-c-benefit-from-metaprogramming.html)** — Hirrolot（2021）
  - **這篇說什麼**：Metalang99 作者說明動機，把程式碼重複分成「函式能消除」「一般巨集能消除」「需要遞迴巨集才能消除」三類，並正面回應編譯時間與錯誤訊息的質疑
  - **為什麼值得讀**：幫你建立「什麼時候值得用元程式設計」的判斷標準

- **[Syntactic sugar in C: (ab)using "for" loops](https://zserge.com/posts/c-for-loop-tricks/)** — Serge Zaitsev（2017）
  - **讀哪裡**：整篇；注意作者自己也指出 `break` 的問題
  - **和本章的關聯**：`TAG` 範例的來源，以及 `with_file`、`synchronized` 這類構想

- **[String switch in C](https://web.archive.org/web/20240113020059/https://tia.mat.br/posts/2012/08/09/string_switch_in_c.html)** 與續篇 **[More on string switch in C](https://web.archive.org/web/20240113032048/https://tia.mat.br/posts/2018/02/01/more_on_string_switch_in_c.html)** — Leandro Pereira（封存版）
  - **讀哪裡**：先讀 2012 年原文看想法，再讀 2018 年續篇看他如何修正 aliasing 與對齊問題
  - **前提知識**：[Ch 16](./16-alignment-and-padding.md) 的對齊

### 書籍 / 原始碼

- **Linux 核心原始碼**：`include/linux/kconfig.h`（`IS_ENABLED`，不到 80 行）、`include/linux/init.h`（initcall）、`include/trace/define_trace.h` 與 `include/trace/trace_events.h`（tracepoint 多階段展開）
  - **讀法**：先讀 `kconfig.h` 對照本章逐步展開；`trace_events.h` 很長，看每個 stage 前 `#include` 了哪個 `stages/` 檔案即可掌握結構
  - **前提知識**：本章全部內容

- **[awesome-c-preprocessor](https://github.com/Hirrolot/awesome-c-preprocessor)** — Hirrolot 維護
  - **定位**：Boost.Preprocessor、Chaos-pp、P99、Metalang99、Datatype99、Interface99 等框架與文章的索引，想深入元程式設計時從這裡挑

下一章離開前置處理器，進入它最重要的應用場景之一：用 C 寫物件導向程式。封裝、vtable、繼承都不需要語言支援，靠的是結構、函式指標與一點紀律。

→ [Ch 29 物件導向 C：封裝、vtable 與繼承](./29-oop-in-c.md)
