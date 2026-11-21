# Ch 14 — 巨集元程式：X-macro、token pasting 與巨集生成的程式碼

> **目標**：看懂 `#` 字串化、`##` token 拼接、X-macro 與可變參數巨集如何「生成」程式碼；碰到 grep 找不到定義的識別字時，知道它多半是被巨集拼出來的，並能用「搜尋拼接前的片段」與 `gcc -E` 把它找回來。
> **鴻溝**：本章主要練**間接鴻溝**（被生成的名字在原始碼裡不存在，搜尋工具找不到）與**慣用法鴻溝**（雙層巨集、X-macro、`__VA_ARGS__` 技巧）。

> **環境**：WSL Ubuntu 22.04、x86_64、gcc 11.4、ripgrep；語料是 Lua 5.4.9、Redis 7.2.17、Linux v6.18。kernel 範例全部直接讀原始碼與做 user-space 重現，**沒有**對 kernel 檔案跑 `gcc -E`（撰寫時 build 目錄尚未完成，kernel 檔案的完整展開在 Ch 50 示範）。

## 為什麼需要這個？

[Ch 13](./13-preprocessor-basics.md) 的巨集只做「取代」：常數、小函式、條件編譯。這一章的巨集做的是「**生成**」：一份清單產生 enum、名稱表、函式指標表、甚至整組函式；一個 `SYSCALL_DEFINE2(pipe2, ...)` 產生好幾個函式定義。這叫**巨集元程式**（macro metaprogramming）：用程式（巨集）來寫程式。

C 沒有 C++ 的 template、沒有 Rust 的 procedural macro、沒有反射。當 C 程式需要「同一份資訊在五個地方保持一致」或「為每個型別生成一份幾乎一樣的程式碼」，前置處理器是唯一不需要額外工具的手段。X-macro 這個技巧至少可以追溯到 1960 年代的組合語言巨集，Walter Bright 在 Dr. Dobb's 的文章（見延伸閱讀）讓它在 C 社群廣為人知。

對讀者來說，巨集元程式的代價很直接：**被生成的名字不會以完整形式出現在任何原始碼檔案裡**。你在 kernel 裡看到 `trace_sched_switch(...)` 被呼叫，`rg trace_sched_switch` 只找到呼叫點和一行註解，找不到定義。你在 `kernel/bpf/verifier.c` 裡看到一張表引用了 `sk_filter_verifier_ops`，用 grep 找它的使用者，只找到定義本身。這不是工具壞了，是那些名字在 `##` 執行之前根本不存在。這一章教你讀懂這些生成器，並把「找不到定義」變成一個有固定解法的問題。

## 先建立直覺

把巨集元程式想成**印章與印台**：

```
清單（印台上的字）          生成器（印章）               產出（蓋出來的東西）
┌─────────────┐         ┌──────────────────────┐
│ X(RED)      │  ──►    │ AS_ENUM: COLOR_##n,  │  ──►  enum { COLOR_RED, COLOR_GREEN, ... }
│ X(GREEN)    │  ──►    │ AS_NAME: [..] = #n,  │  ──►  { "RED", "GREEN", ... }
│ X(BLUE)     │  ──►    │ AS_PRED: is_##n(){}  │  ──►  is_RED(), is_GREEN(), ...
└─────────────┘         └──────────────────────┘
  只寫一次                 # 把參數變成字串
                          ## 把兩個 token 黏成一個新名字
```

兩個運算子是一切的基礎：

- **`#x`**：把參數 `x` 的**原始拼寫**變成字串常數。
- **`a##b`**：把兩個 token 黏成一個新 token，例如 `is_` 和 `RED` 變成 `is_RED`。

而讀者面對的問題是右邊那欄：你在程式裡看到 `is_GREEN`，要能倒推回中間那欄的印章和左邊的清單。

## `#`：字串化與雙層巨集

```c
/* scratch/14/strpaste.c */
#include <stdio.h>
#define STR(x)    #x
#define XSTR(x)   STR(x)          /* 雙層：先展開參數，再字串化 */
#define VERSION_MAJOR 7
#define PASTE(a, b)  a##b
#define XPASTE(a, b) PASTE(a, b)
#define CHECK(expr) do { if (!(expr)) printf("check failed: %s (%s:%d)\n", #expr, __FILE__, __LINE__); } while (0)

int counter_7 = 77, counter_VERSION_MAJOR = 1;

int main(void) {
    printf("STR(VERSION_MAJOR)  = %s\n", STR(VERSION_MAJOR));
    printf("XSTR(VERSION_MAJOR) = %s\n", XSTR(VERSION_MAJOR));
    printf("STR(__LINE__) = %s, XSTR(__LINE__) = %s\n", STR(__LINE__), XSTR(__LINE__));
    printf("STR( a  +   b ) = [%s]\n", STR( a  +   b ));
    printf("STR(\"q\\n\") = %s\n", STR("q\n"));
    printf("PASTE(counter_, VERSION_MAJOR)  = %d\n", PASTE(counter_, VERSION_MAJOR));
    printf("XPASTE(counter_, VERSION_MAJOR) = %d\n", XPASTE(counter_, VERSION_MAJOR));
    int len = 3;
    CHECK(len > 5 && "length too small");
    return 0;
}
```

```
$ gcc -Wall -o strpaste strpaste.c && ./strpaste
STR(VERSION_MAJOR)  = VERSION_MAJOR
XSTR(VERSION_MAJOR) = 7
STR(__LINE__) = __LINE__, XSTR(__LINE__) = 14
STR( a  +   b ) = [a + b]
STR("q\n") = "q\n"
PASTE(counter_, VERSION_MAJOR)  = 1
XPASTE(counter_, VERSION_MAJOR) = 77
check failed: len > 5 && "length too small" (strpaste.c:20)
```

關鍵規則（C11 6.10.3.1）：**巨集參數如果是 `#` 或 `##` 的運算元，就不會先被展開**；其他位置的參數會先完整展開再代入。所以：

- `STR(VERSION_MAJOR)`：`x` 直接被 `#` 吃掉，得到 `"VERSION_MAJOR"`。
- `XSTR(VERSION_MAJOR)`：`XSTR` 的本體裡 `x` 不是 `#` 的運算元，於是先展開成 `7`，再傳給 `STR`，得到 `"7"`。

這就是**雙層巨集**（double expansion）。在 Redis 和 kernel 裡都看得到同一個模式：

```c
/* redis-7.2.17/src/zmalloc.h:34 */
/* Double expansion needed for stringification of macro values. */
#define __xstr(s) __str(s)
#define __str(s) #s

/* redis-7.2.17/src/zmalloc.h:49 */
#define ZMALLOC_LIB ("jemalloc-" __xstr(JEMALLOC_VERSION_MAJOR) "." __xstr(JEMALLOC_VERSION_MINOR) "." __xstr(JEMALLOC_VERSION_BUGFIX))
```

```c
/* include/linux/stringify.h:9（v6.18） */
#define __stringify_1(x...)	#x
#define __stringify(x...)	__stringify_1(x)
```

`ZMALLOC_LIB` 最後變成 `"jemalloc-" "5" "." "3" "." "0"`，相鄰字串常數被編譯器接起來成為 `"jemalloc-5.3.0"`——Ch 12 的 `realsds` 實驗印出的就是它。kernel 版的 `x...` 是 GNU 的具名可變參數，讓 `__stringify(a, b)` 也能用。

`CHECK` 示範了 `#` 最常見的用途：**把一個運算式本身印出來**。Redis 的斷言就是這樣做的：

```c
/* redis-7.2.17/src/server.h:673 */
#define serverAssert(_e) ((_e)?(void)0 : (_serverAssert(#_e,__FILE__,__LINE__),redis_unreachable()))
```

斷言失敗時 log 裡出現的那串條件文字，就是 `#_e` 產生的。讀碼時看到一個函式參數是 `#something`，就知道「這是給人看的訊息」。

## `##`：拼出新名字

`PASTE(counter_, VERSION_MAJOR)` 回傳 1、`XPASTE` 回傳 77，和 `#` 是同一條規則：`##` 的運算元不先展開，`PASTE` 拼出的是 `counter_VERSION_MAJOR`，`XPASTE` 先把 `VERSION_MAJOR` 展開成 `7` 再拼出 `counter_7`。kernel 的 `include/linux/compiler_types.h:83` 有一模一樣的一對：

```c
#define ___PASTE(a,b) a##b
#define __PASTE(a,b) ___PASTE(a,b)
```

**拼接的結果必須是一個合法的 token**。故意拼一個不合法的：

```c
/* scratch/14/badpaste.c */
#define FIELD(s, f) s.##f
struct p { int x; } pt;
int main(void) { return FIELD(pt, x); }
```

```
badpaste.c: In function ‘main’:
badpaste.c:1:22: error: pasting "." and "x" does not give a valid preprocessing token
    1 | #define FIELD(s, f) s.##f
      |                      ^
```

`.` 和 `x` 黏起來是 `.x`，不是任何一種 token。正確寫法是 `s.f`，根本不需要 `##`。讀碼時看到 `##` 的兩側，問自己「黏起來會是什麼名字」——那個名字就是你稍後要搜尋的東西。

Redis 的 sds 用 `##` 從數字拼出型別名：

```c
/* redis-7.2.17/src/sds.h:83 */
#define SDS_HDR_VAR(T,s) struct sdshdr##T *sh = (void*)((s)-(sizeof(struct sdshdr##T)));
#define SDS_HDR(T,s) ((struct sdshdr##T *)((s)-(sizeof(struct sdshdr##T))))
```

`SDS_HDR(8, s)` 展開成 `((struct sdshdr8 *)((s)-(sizeof(struct sdshdr8))))`。如果你在 `sds.c` 裡看到 `sh->alloc` 想知道 `sh` 是什麼型別，`rg "sh ="` 找不到宣告，因為宣告是 `SDS_HDR_VAR(16,s);` 生成的。

Redis module API 同時用了 `#` 和 `##`：

```c
/* redis-7.2.17/src/redismodule.h:946 */
#define REDISMODULE_GET_API(name) \
    RedisModule_GetApi("RedisModule_" #name, ((void **)&RedisModule_ ## name))
```

`REDISMODULE_GET_API(Alloc)` 變成 `RedisModule_GetApi("RedisModule_" "Alloc", ((void **)&RedisModule_Alloc))`：同一個參數，一次變成字串（給執行期查表用），一次變成識別字（給編譯期取位址用）。這是「名字同時存在於字串世界和符號世界」的典型手法。

## 運算子也能當參數

前置處理器只看 token，`+`、`*` 也是 token，所以可以把運算子當成巨集參數傳進去。Lua 5.4.9 的整數運算就是這樣寫的：

```c
/* lua-5.4.9/lvm.h:73 */
#define intop(op,v1,v2) l_castU2S(l_castS2U(v1) op l_castS2U(v2))

/* lua-5.4.9/lvm.c:896 */
#define l_addi(L,a,b)	intop(+, a, b)
```

為什麼要繞這一圈？`l_castS2U` 把有號整數轉成無號再運算，因為 C 的有號整數溢位是未定義行為，無號溢位則是定義好的環繞（wraparound）。Lua 規定整數運算溢位要環繞，所以必須在無號域做完再轉回來。我們的重現：

```c
/* scratch/14/variadic.c（節錄） */
#define intop(op, v1, v2)  ((long long)((unsigned long long)(v1) op (unsigned long long)(v2)))
    long long big = 0x7fffffffffffffffLL;
    printf("intop(+, LLONG_MAX, 1) = %lld\n", intop(+, big, 1));
    printf("intop(*, 6, 7)         = %lld\n", intop(*, 6, 7));
```

```
intop(+, LLONG_MAX, 1) = -9223372036854775808
intop(*, 6, 7)         = 42
```

`LLONG_MAX + 1` 環繞成 `LLONG_MIN`，沒有觸發未定義行為。讀到 `intop(+, ...)` 時不要以為是打錯字：它是一個「把運算子參數化」的生成器，`l_addi`、`l_subi`、`l_muli` 都由它而來。

## X-macro：一份清單，多種產出

X-macro 解決的問題是：**同一份清單必須在多個地方保持完全一致的順序與內容**。例如 enum 和它的名稱字串表。不用 X-macro 的話，你維護兩份清單，總有一天有人只改了一份。

```c
/* scratch/14/xmacro.c */
#include <stdio.h>

/* X-macro：清單只寫一次 */
#define COLOR_LIST(X) \
    X(RED,   0xff0000) \
    X(GREEN, 0x00ff00) \
    X(BLUE,  0x0000ff)

/* 用途一：產生 enum */
#define AS_ENUM(name, rgb)  COLOR_##name,
enum color { COLOR_LIST(AS_ENUM) NCOLOR };

/* 用途二：產生名稱表（stringify） */
#define AS_NAME(name, rgb)  [COLOR_##name] = #name,
static const char *const color_name[] = { COLOR_LIST(AS_NAME) };

/* 用途三：產生值表 */
#define AS_RGB(name, rgb)   [COLOR_##name] = rgb,
static const unsigned color_rgb[] = { COLOR_LIST(AS_RGB) };

/* 用途四：產生函式（「巨集生成函式」的迷你版） */
#define AS_PRED(name, rgb)  static int is_##name(enum color c) { return c == COLOR_##name; }
COLOR_LIST(AS_PRED)

/* 對照組：Lua 式的「平行陣列 + ORDER 註解」，故意讓順序錯開 */
enum shape { SHAPE_CIRCLE, SHAPE_SQUARE, SHAPE_TRIANGLE };     /* ORDER SHAPE */
static const char *const shape_name[] = { "circle", "triangle", "square" }; /* 有人插錯位置 */

int main(void) {
    for (int c = 0; c < NCOLOR; c++)
        printf("%d %-5s #%06x\n", c, color_name[c], color_rgb[c]);
    printf("is_GREEN(COLOR_GREEN)=%d is_RED(COLOR_BLUE)=%d\n", is_GREEN(COLOR_GREEN), is_RED(COLOR_BLUE));
    printf("SHAPE_SQUARE 的名字是 %s\n", shape_name[SHAPE_SQUARE]);
    return 0;
}
```

```
$ gcc -Wall -o xmacro xmacro.c && ./xmacro
xmacro.c:22:40: warning: ‘is_BLUE’ defined but not used [-Wunused-function]
   22 | #define AS_PRED(name, rgb)  static int is_##name(enum color c) { return c == COLOR_##name; }
      |                                        ^~~
xmacro.c:7:5: note: in expansion of macro ‘AS_PRED’
    7 |     X(BLUE,  0x0000ff)
      |     ^
xmacro.c:23:1: note: in expansion of macro ‘COLOR_LIST’
   23 | COLOR_LIST(AS_PRED)
      | ^~~~~~~~~~
0 RED   #ff0000
1 GREEN #00ff00
2 BLUE  #0000ff
is_GREEN(COLOR_GREEN)=1 is_RED(COLOR_BLUE)=0
SHAPE_SQUARE 的名字是 triangle
```

兩件值得注意的事：

1. **警告的 note 鏈就是展開路徑**。`is_BLUE` 沒被用到，gcc 告訴你它來自第 22 行的 `AS_PRED`，被第 7 行的 `X(BLUE, ...)` 觸發，而那又是第 23 行 `COLOR_LIST(AS_PRED)` 展開的一部分。編譯器替你做了「從產出倒推印章與清單」的工作。讀碼時，有時候故意製造一個錯誤（例如把某個生成的名字拼錯）來讓編譯器印出展開路徑，是很有效的手段。
2. **對照組的平行陣列默默出錯**：`shape_name[SHAPE_SQUARE]` 印出 `triangle`。沒有警告。這正是 X-macro 想消滅的那一類 bug。

`gcc -E -P` 看產出：

```
enum color { COLOR_RED, COLOR_GREEN, COLOR_BLUE, NCOLOR };
static const char *const color_name[] = { [COLOR_RED] = "RED", [COLOR_GREEN] = "GREEN", [COLOR_BLUE] = "BLUE", };
static int is_RED(enum color c) { return c == COLOR_RED; } static int is_GREEN(enum color c) { return c == COLOR_GREEN; } static int is_BLUE(enum color c) { return c == COLOR_BLUE; }
```

### X-macro 的另一種形式：清單放在獨立檔案

上面的清單是一個巨集 `COLOR_LIST(X)`。另一種常見寫法是把清單放在一個**沒有 include guard** 的檔案裡，每次 include 之前重新定義 `X`、include 之後 `#undef`。Linux v6.18 的 BPF 子系統就是這樣：

```c
/* include/linux/bpf_types.h:1（v6.18，節錄） */
/* SPDX-License-Identifier: GPL-2.0 */
/* internal file - do not include directly */

#ifdef CONFIG_NET
BPF_PROG_TYPE(BPF_PROG_TYPE_SOCKET_FILTER, sk_filter,
	      struct __sk_buff, struct sk_buff)
BPF_PROG_TYPE(BPF_PROG_TYPE_SCHED_CLS, tc_cls_act,
	      struct __sk_buff, struct sk_buff)
...
```

這個檔案沒有 include guard，第二行明說「do not include directly」，內容只是一連串還沒定義的 `BPF_PROG_TYPE(...)`。它在 v6.18 被 include 了 8 次（`include/linux/bpf.h` 1 次、`kernel/bpf/btf.c` 3 次、`verifier.c` 1 次、`syscall.c` 3 次），每次搭配不同的定義：

```c
/* include/linux/bpf.h:2405（v6.18）——產生 extern 宣告 */
#define BPF_PROG_TYPE(_id, _name, prog_ctx_type, kern_ctx_type) \
	extern const struct bpf_prog_ops _name ## _prog_ops; \
	extern const struct bpf_verifier_ops _name ## _verifier_ops;
#define BPF_MAP_TYPE(_id, _ops) \
	extern const struct bpf_map_ops _ops;
#define BPF_LINK_TYPE(_id, _name)
#include <linux/bpf_types.h>
#undef BPF_PROG_TYPE
#undef BPF_MAP_TYPE
#undef BPF_LINK_TYPE
```

```c
/* kernel/bpf/verifier.c:36（v6.18）——產生以 prog type 為索引的 ops 表 */
static const struct bpf_verifier_ops * const bpf_verifier_ops[] = {
#define BPF_PROG_TYPE(_id, _name, prog_ctx_type, kern_ctx_type) \
	[_id] = & _name ## _verifier_ops,
#define BPF_MAP_TYPE(_id, _ops)
#define BPF_LINK_TYPE(_id, _name)
#include <linux/bpf_types.h>
#undef BPF_PROG_TYPE
#undef BPF_MAP_TYPE
#undef BPF_LINK_TYPE
};
```

讀法：

- 同一行 `BPF_PROG_TYPE(BPF_PROG_TYPE_SOCKET_FILTER, sk_filter, ...)`，在 `bpf.h` 裡變成 `extern const struct bpf_verifier_ops sk_filter_verifier_ops;`，在 `verifier.c` 裡變成 `[BPF_PROG_TYPE_SOCKET_FILTER] = & sk_filter_verifier_ops,`。
- 不需要的欄位，生成器定義成空的（`#define BPF_MAP_TYPE(_id, _ops)`），等於「這一次不蓋這個章」。
- 清單裡的 `#ifdef CONFIG_NET` 也會跟著生效：關掉網路時，所有表都自動少了那幾項。

designated initializer `[_id] = ...`（[Ch 11](./11-structs-unions-tagged.md) 介紹過）讓表的順序不再依賴清單順序，X-macro 加 designated initializer 是 kernel 建表的標準組合。

### errno 名稱表：`#` + designated initializer

```c
/* lib/errname.c:16（v6.18） */
#define E(err) [err + BUILD_BUG_ON_ZERO(err <= 0 || err > 300)] = "-" #err
static const char *names_0[] = {
	E(E2BIG),
	E(EACCES),
	E(EADDRINUSE),
	...
```

`E(EACCES)` 變成 `[EACCES + BUILD_BUG_ON_ZERO(...)] = "-" "EACCES"`。三個技巧疊在一起：`#err` 把巨集名本身變成字串（這裡 `err` 是 `#` 的運算元，所以**不會**先展開成數字 13，字串是 `"EACCES"` 而不是 `"13"`）；索引位置的 `err` 不是 `#` 的運算元，會展開成數字當陣列索引；`BUILD_BUG_ON_ZERO` 在條件成立時讓編譯失敗、否則值為 0，等於一個寫在索引裡的編譯期斷言。這張表讓 `printk("%pe", ERR_PTR(-EACCES))` 能印出 `-EACCES` 而不是 `-13`。這個檔案嚴格說不是 X-macro（清單只用一次），但它展示了同一個參數在 `#` 與非 `#` 位置的不同命運。

### Lua 為什麼不用 X-macro

Lua 5.4.9 的 opcode 有**四份**平行清單：`lopcodes.h:201` 的 `enum OpCode`、`lopcodes.c` 的 `luaP_opmodes[]`、`lopnames.h` 的 `opnames[]`、`ljumptab.h` 的 `disptab[]`。它們靠註解維持一致：

```c
/* lua-5.4.9/lopcodes.h:190 */
/*
** Grep "ORDER OP" if you change these enums. Opcodes marked with a (*)
** has extra descriptions in the notes after the enumeration.
*/
```

`ljumptab.h` 甚至在 `#if 0` 裡留了一行 sed 指令，說明如何從 `lopcodes.h` 重新產生那張表。

為什麼不用 X-macro？Lua 原始碼沒有說明，以下是推測：每一份清單的格式差異很大（enum 有長註解說明每個指令的語意、`opmodes` 有 5 個位元欄位、`disptab` 是 GNU 的 `&&label` 語法），塞進同一個 X-macro 會讓清單本身難讀；而 opcode 只在大版本之間變動，Lua 團隊選擇「清單可讀」優先於「自動同步」。這是真實的取捨，不是無知：我們的 `shape_name` 對照組展示了代價。讀碼時看到 `ORDER XXX` 這類註解，就知道它是「手工 X-macro」，改其中一處時必須 grep 所有同名註解。

## 可變參數巨集 `__VA_ARGS__`

C99 引入 `...` 和 `__VA_ARGS__`，讓巨集接受任意數量的參數。Redis 的 debug 巨集：

```c
/* redis-7.2.17/src/server.h:3739 */
#define redisDebug(fmt, ...) \
    printf("DEBUG %s:%d > " fmt "\n", __FILE__, __LINE__, __VA_ARGS__)
```

問題出在**沒有額外參數**時。我們的仿製品（`scratch/14/variadic.c`）：

```c
#define DEBUG_BAD(fmt, ...)  printf("DEBUG %s:%d > " fmt "\n", __FILE__, __LINE__, __VA_ARGS__)
/* GNU 擴充：##__VA_ARGS__ 在沒有參數時吃掉前面的逗號 */
#define DEBUG_GNU(fmt, ...)  printf("DEBUG %s:%d > " fmt "\n", __FILE__, __LINE__, ##__VA_ARGS__)
/* C23 / GCC 8+：__VA_OPT__ */
#define DEBUG_OPT(fmt, ...)  printf("DEBUG %s:%d > " fmt "\n", __FILE__, __LINE__ __VA_OPT__(,) __VA_ARGS__)
```

`DEBUG_BAD("no args")` 展開成 `printf("...", __FILE__, __LINE__, )`，結尾多一個逗號：

```
variadic.c:4:95: error: expected expression before ‘)’ token
    4 | #define DEBUG_BAD(fmt, ...)  printf("DEBUG %s:%d > " fmt "\n", __FILE__, __LINE__, __VA_ARGS__)
      |                                                                                               ^
variadic.c:26:5: note: in expansion of macro ‘DEBUG_BAD’
```

另外兩個版本正常：

```
DEBUG variadic.c:18 > x=1 y=2
DEBUG variadic.c:19 > no args
DEBUG variadic.c:20 > no args either
```

兩種解法的出處不同。`, ##__VA_ARGS__` 是 GNU 擴充，kernel 大量使用（例如 `BUILD_BUG_ON_ZERO(e, ...)` 的定義裡就有 `##__VA_ARGS__`，`include/linux/build_bug.h:16`）。`__VA_OPT__(,)` 是 C++20 引入、C23 納入的標準寫法，gcc 8 之後在 C 模式也接受。用 `-std=c11 -pedantic` 編譯時 gcc 會老實告訴你這些都不是 C11：

```
variadic.c:8:83: warning: __VA_OPT__ is not available until C++20
variadic.c:19:24: warning: ISO C99 requires at least one argument for the "..." in a variadic macro
```

（gcc 11 的這則警告訊息只提到 C++20，是因為 gcc 11 時 C23 還沒定案。）Redis 的 `redisDebug` 沒處理零參數的情況，所以 Redis 程式碼裡每次呼叫它都至少帶一個參數。

### 數參數個數

可變參數巨集還有一個讀碼時常撞見的技巧：**數參數的個數**。

```c
#define NARGS_(_1, _2, _3, _4, _5, N, ...) N
#define NARGS(...) NARGS_(__VA_ARGS__, 5, 4, 3, 2, 1, 0)
```

```
NARGS(a)=1 NARGS(a,b,c)=3
```

原理：`NARGS(a, b, c)` 變成 `NARGS_(a, b, c, 5, 4, 3, 2, 1, 0)`。實際參數把倒數序列往右推，第六個位置 `N` 剛好落在 3。kernel 的 `COUNT_ARGS`（`include/linux/args.h:22`）就是同一招，只是數到 15，並用 `, ##X` 讓零參數也能算出 0。看到一串遞減的數字跟在 `__VA_ARGS__` 後面，就是在數參數。

## 底層機制：拆解 kernel 的 `IS_ENABLED`

Ch 13 提到 kernel 用 `IS_ENABLED(CONFIG_X)` 取代 `#ifdef`。它要做到一件前置處理器原本做不到的事：在**運算式**裡判斷一個巨集是否被定義為 1，並產生 0 或 1。`#ifdef` 是指令，不能出現在運算式中。kernel 的做法（`include/linux/kconfig.h`，v6.18）：

```c
/* include/linux/kconfig.h:13 */
#define __ARG_PLACEHOLDER_1 0,
#define __take_second_arg(__ignored, val, ...) val
...
/* include/linux/kconfig.h:41 */
#define __is_defined(x)			___is_defined(x)
#define ___is_defined(val)		____is_defined(__ARG_PLACEHOLDER_##val)
#define ____is_defined(arg1_or_junk)	__take_second_arg(arg1_or_junk 1, 0)
...
#define IS_BUILTIN(option) __is_defined(option)
#define IS_MODULE(option) __is_defined(option##_MODULE)
#define IS_ENABLED(option) __or(IS_BUILTIN(option), IS_MODULE(option))
```

我們把這幾行原樣複製到 user-space（`scratch/14/isenabled.c`），模擬 `autoconf.h`：`=y` 的選項定義成 1，`=m` 的定義 `CONFIG_FOO_MODULE` 為 1，`=n` 的不定義：

```c
#define CONFIG_VMAP_STACK 1
#define CONFIG_EXT4_FS_MODULE 1
```

```
$ ./isenabled
VMAP_STACK=1 EXT4_FS=1 WATCH_QUEUE=0
IS_BUILTIN(EXT4_FS)=0 IS_MODULE(EXT4_FS)=1
```

`gcc -E -P` 確認它在前置處理階段就變成常數：`printf("...", 1, 1, 0);`。逐步展開（`scratch/14/steps.c`，每一行是一個中間步驟丟給 `gcc -E -P` 的結果）：

```
A0: __is_defined(CONFIG_VMAP_STACK)                                  → 1
A:  ___is_defined(CONFIG_VMAP_STACK)                                 → 0   ← 少了一層！
B:  ____is_defined(__ARG_PLACEHOLDER_1)                              → 1
C:  __take_second_arg(0, 1, 0)                                       → 1
D:  ____is_defined(__ARG_PLACEHOLDER_CONFIG_WATCH_QUEUE)             → 0
E:  __take_second_arg(__ARG_PLACEHOLDER_CONFIG_WATCH_QUEUE 1, 0)     → 0
```

讀這張表：

```
定義為 1 的選項：
  __is_defined(CONFIG_VMAP_STACK)
  → ___is_defined(1)                       外層先把 CONFIG_VMAP_STACK 展開成 1
  → ____is_defined(__ARG_PLACEHOLDER_1)    ## 拼出這個名字
  → __take_second_arg(0, 1, 0)             __ARG_PLACEHOLDER_1 展開成 "0,"——多了一個逗號！
  → 1                                       第二個參數是 1

未定義的選項：
  → ____is_defined(__ARG_PLACEHOLDER_CONFIG_WATCH_QUEUE)   拼出的名字不是巨集，原樣留著
  → __take_second_arg(__ARG_PLACEHOLDER_CONFIG_WATCH_QUEUE 1, 0)
  → 0                                       那串垃圾和 1 擠在第一個參數裡，第二個參數是 0
```

精髓是 `__ARG_PLACEHOLDER_1` 展開成 `0,`：**一個逗號改變了參數的個數**。`__take_second_arg(arg1_or_junk 1, 0)` 原本只有兩個參數，第二個是 `0`；如果 `arg1_or_junk` 展開成 `0,`，參數列就變成 `(0, 1, 0)`，第二個參數變成 `1`。只有當選項被定義為恰好 `1` 時，拼出的名字才是 `__ARG_PLACEHOLDER_1` 這個存在的巨集，才會多出那個逗號。

第 A 行展示了為什麼需要 `__is_defined` 這層看似多餘的轉手：直接呼叫 `___is_defined(CONFIG_VMAP_STACK)` 時，`val` 是 `##` 的運算元，不會先展開，拼出的是 `__ARG_PLACEHOLDER_CONFIG_VMAP_STACK`，結果是錯的 0。這和 `STR` / `XSTR` 是同一條規則。**kernel 裡任何「看起來多餘的一層巨集轉手」，第一個假設都應該是：它在強迫參數先展開。**

## 巨集生成函式：`DEFINE_*` 家族預告

X-macro 的 `AS_PRED` 已經示範了「一個巨集生成一個函式」。kernel 把這個做到極致：`DEFINE_MUTEX(name)` 定義並初始化一個 mutex 變數、`SYSCALL_DEFINE2(pipe2, ...)` 生成 syscall 進入點與包裝函式、`TRACE_EVENT(...)` 生成一整組 tracepoint 函式、`DEFINE_PER_CPU`、`DEFINE_STATIC_KEY_FALSE`、`DEFINE_GUARD`……讀 kernel 時，**看到 `DEFINE_` 開頭的頂層「呼叫」，它幾乎一定是在定義東西**，而被定義的名字是某個參數加上前後綴。

這個家族在 [Ch 22](./22-kernel-macro-decoding.md) 逐一拆解、[Ch 29](./29-syscall-entry-paths.md) 會從 `SYSCALL_DEFINE` 開始追 syscall。這裡只用它們來練下一節的技能。

## 讀碼技能：grep 找不到定義怎麼辦

### 症狀一：找得到定義，找不到使用者

```
$ cd ~/code_reading/linux
$ rg -n "sk_filter_verifier_ops" .
./net/core/filter.c:11103:const struct bpf_verifier_ops sk_filter_verifier_ops = {
```

一個 `const` 的全域 ops table，整個 kernel 沒有任何地方用它？不可能。使用者是被 `##` 拼出來的。**解法：搜尋拼接後的「後綴片段」，並把 `##` 考慮進去**：

```
$ rg -n "_verifier_ops,|_name ## _verifier_ops" include kernel
kernel/bpf/verifier.c:38:	[_id] = & _name ## _verifier_ops,
kernel/sched/ext.c:5065:	.verifier_ops = &bpf_scx_verifier_ops,
include/linux/bpf.h:2407:	extern const struct bpf_verifier_ops _name ## _verifier_ops;
```

`verifier.c:38` 就是使用者，前綴 `sk_filter` 來自 `bpf_types.h` 的 `BPF_PROG_TYPE(..., sk_filter, ...)`。用 `rg -n "\bsk_filter\b" include/linux/bpf_types.h` 可以確認。

（小提醒：在 heredoc 或管線裡跑 `rg` 時一定要給路徑參數，例如結尾的 `.`。沒有路徑而 stdin 又不是終端機時，`rg` 會去搜 stdin，安靜地回傳「沒找到」。我們寫這一章時就被它騙過一次。）

### 症狀二：找得到呼叫，找不到定義

```
$ rg -n "\btrace_sched_switch\b" --type c .
./kernel/sched/core.c:6926:		trace_sched_switch(preempt, prev, next, prev_state);
./kernel/trace/trace_osnoise.c:1248: * trace_sched_switch - sched:sched_switch trace event handler
./include/linux/tracepoint.h:511: * 'trace_sched_switch() function' from now on.
```

一個呼叫、一個註解、一份文件，沒有定義。**解法：把名字拆成「前綴 + 核心詞」，分別搜尋**。核心詞 `sched_switch`：

```
$ rg -n "TRACE_EVENT\(sched_switch" include/
include/trace/events/sched.h:220:TRACE_EVENT(sched_switch,
```

前綴 `trace_`，搜尋 `trace_##`：

```
$ rg -n "trace_##name\b|trace_##name\(" include/linux/tracepoint.h
...
278:	static inline void trace_##name(proto)				\
...
```

再串起中間的鏈：`TRACE_EVENT`（`tracepoint.h:618`）→ `DECLARE_TRACE_EVENT`（`:480`）→ `__DECLARE_TRACE`（`:269`）→ 第 278 行的 `static inline void trace_##name(proto)`。完整的生成鏈：

```c
/* include/linux/tracepoint.h:618（v6.18） */
#define TRACE_EVENT(name, proto, args, struct, assign, print)	\
	DECLARE_TRACE_EVENT(name, PARAMS(proto), PARAMS(args))

/* include/linux/tracepoint.h:480 */
#define DECLARE_TRACE_EVENT(name, proto, args)				\
	__DECLARE_TRACE(name, PARAMS(proto), PARAMS(args),		\
			cpu_online(raw_smp_processor_id()),		\
			PARAMS(void *__data, proto))

/* include/linux/tracepoint.h:269 */
#define __DECLARE_TRACE(name, proto, args, cond, data_proto)		\
	...
	static inline void trace_##name(proto)				\
	{								\
		if (static_branch_unlikely(&__tracepoint_##name.key))	\
			__do_trace_##name(args);			\
```

一個小小的版本差異警告：v6.18 的 `DECLARE_TRACE(name, ...)`（`tracepoint.h:466`，注意不是 `DECLARE_TRACE_EVENT`）會把名字拼成 `name##_tp`，所以用 `DECLARE_TRACE` 定義的 tracepoint，函式名是 `trace_xxx_tp()`。這是 commit `ac01fa73f530`「tracepoint: Have tracepoints created with DECLARE_TRACE() have _tp suffix」引入的，`git describe --contains` 顯示它進入 v6.16-rc1。如果你照著舊文章去 grep `trace_xxx(`，在新 kernel 上可能什麼都找不到。**巨集生成的名字會隨版本改變，引用時一定釘版本。**

### 症狀三：宣告找得到，函式本體找不到

```
$ rg -n "\bsys_pipe2\b" --type c . | head -4
./tools/include/uapi/asm-generic/unistd.h:165:__SYSCALL(__NR_pipe2, sys_pipe2)
./include/uapi/asm-generic/unistd.h:165:__SYSCALL(__NR_pipe2, sys_pipe2)
./tools/include/nolibc/sys.h:689:int sys_pipe2(int pipefd[2], int flags)
./include/linux/syscalls.h:469:asmlinkage long sys_pipe2(int __user *fildes, int flags);
```

`tools/` 底下那個是 user-space 的 nolibc，不是 kernel 的實作。kernel 的本體在：

```
$ rg -n "^SYSCALL_DEFINE2\(pipe2" fs/pipe.c
1054:SYSCALL_DEFINE2(pipe2, int __user *, fildes, int, flags)
```

搜尋策略：**拿掉前綴 `sys_`，搜 `DEFINE\d?\(pipe2`**。這個模式值得記住：`rg -n "DEFINE[0-9]?\(\s*NAME\b"` 能找到大部分 kernel 巨集生成的定義。

### 通用解法清單

| 手段 | 適用情況 | 代價 |
|---|---|---|
| 拆片段搜尋（後綴 / 核心詞 / 拿掉前綴） | 任何 `##` 生成的名字 | 要猜拆法；練多了就快 |
| 搜 `##` 本身：`rg "## *_suffix\|prefix_ *##"` | 知道後綴或前綴時 | 結果可能很多 |
| 故意製造編譯錯誤看 `note: in expansion of macro` | 能編譯那個檔案時 | 要動到原始碼（在副本上做） |
| `gcc -E` 展開整個檔案再搜 | 能拿到編譯指令時 | 輸出很大；kernel 需要 build 好的 tree（`make fs/pipe.i`） |
| clangd / LSP「跳到定義」 | 有 `compile_commands.json` 時 | 前置作業多，但最準（[Ch 49](./49-clangd-lsp.md)） |
| Bootlin Elixir | 線上快速查 | 它也是文字索引，對 `##` 一樣無能為力 |

前兩種不需要任何環境，永遠先試。

## 對比與取捨

| 生成手段 | 優點 | 缺點 | 誰在用 |
|---|---|---|---|
| X-macro（巨集清單） | 單一真相來源、零外部工具 | 清單格式受限、IDE 看不懂 | kernel BPF、許多 C 專案 |
| 手工平行清單 + `ORDER` 註解 | 每份清單可讀、格式自由 | 不同步時默默出錯 | Lua opcode |
| 外部程式碼生成器 | 清單可以是 JSON/YAML，產出可讀 | 多一個 build 步驟、產物要進版控或每次生成 | Redis `commands.def`（由 `utils/generate-command-code.py` 從 `src/commands/*.json` 生成） |
| C++ template / Rust macro | 型別安全、工具支援好 | 不是 C | — |

Redis 7 的 `commands.def` 值得多看一眼：它是生成器產出的檔案（第 1 行寫著「Automatically generated by generate-command-code.py, do not edit」），然後**再被當成 X-macro 使用**——`commands.c` 和 `cli_commands.c` 各自定義不同的 `MAKE_CMD` 後 include 它，一個產生伺服器的指令表、一個產生 `redis-cli` 的說明表。兩種技術疊在一起。讀這種程式碼時，**真正的原始資料在 JSON 裡**，改 `commands.def` 是沒用的。

Redis 的 config 表則是 designated initializer 包進巨集。`config.c:3073` 的一行：

```c
    createBoolConfig("daemonize", NULL, IMMUTABLE_CONFIG, server.daemonize, 0, NULL, NULL),
```

把 `config.c:1798–1869` 的三個巨集取出來，對這一行跑 `gcc -E -P`：

```
{ .name = ("daemonize"), .alias = (NULL), .flags = (IMMUTABLE_CONFIG), .interface = { .init = (boolConfigInit), .set = (boolConfigSet), .get = (boolConfigGet), .rewrite = (boolConfigRewrite), .apply = (NULL) }, .type = BOOL_CONFIG, .data.yesno = { .config = &(server.daemonize), .default_value = (0), .is_valid_fn = (NULL), } },
```

一行變成一個完整的 struct 初始化子，裡面藏了一張 ops table（`.interface`）。讀到「`daemonize` 設定改變時會呼叫什麼」，答案是 `boolConfigSet`——在原始碼那一行裡完全看不到。

## 踩雷集錦

1. **「`#x` 會印出 x 的值」**：不會，它印出 x 的**拼寫**。要印值必須先展開，也就是雙層巨集。`lib/errname.c` 正是利用這點拿到 `"EACCES"` 而不是 `"13"`。
2. **「那層轉手巨集是多餘的，可以拿掉」**：`XSTR`→`STR`、`__PASTE`→`___PASTE`、`__is_defined`→`___is_defined`，拿掉任何一層都會讓參數不再先展開，結果靜靜地變錯（steps.c 的 A 行）。
3. **「grep 找不到就是不存在」**：`##` 生成的名字在原始碼裡不以完整形式存在。先拆片段搜尋，再考慮 `gcc -E` 或 clangd。反過來，`rg` 只找到定義沒找到使用者，也不代表它是死碼。
4. **「X-macro 的清單檔案應該加 include guard」**：不應該。`bpf_types.h` 必須能被多次 include，加了 guard 第二次 include 就什麼都不產生。讀到一個沒有 guard、註明「do not include directly」的標頭，它就是 X-macro 清單。
5. **「`__VA_ARGS__` 可以是空的」**：在 C11 嚴格模式下不行（`-pedantic` 會警告），而且空的時候結尾逗號會導致語法錯誤。看到 `, ##__VA_ARGS__` 或 `__VA_OPT__`，作者就是在處理這個問題。

## 進階：再往深一層

- **前置處理器的展開演算法**：C 標準 6.10.3.4 規定的「rescan and further replacement」與「blue paint」（一個巨集在自己的展開過程中不會被再次展開）是遞迴巨集技巧的基礎，也是為什麼前置處理器不是圖靈完備、卻能用 `EVAL(EVAL(...))` 模擬有限次迴圈的原因。Boost.Preprocessor（C++ 函式庫，但大部分技巧對 C 有效）把這些推到極限。
- **`_Generic`**：C11 的型別分派，讓巨集能依參數型別選擇不同實作。kernel 用它實作部分泛型 API（例如 v6.18 的 `kmem_cache_create` 在 `include/linux/slab.h:461` 是一個依參數型別分派的巨集），讀這類巨集時要把型別也當成「參數」來看。
- **讀 `.def` 檔案與 `#include` 在函式中間**：Lua 的 `luaV_execute` 在**函式本體內** `#include "ljumptab.h"`（`lvm.c:1166`），把一張 `static const void *const disptab[]` 表和三個巨集的重新定義放進函式的作用域。看到 `#include` 出現在非檔案開頭的地方，它就是在做程式碼生成。練習 B 第 1 題會拆它。
- **工具**：`cpp -dD` 加 line marker 可以追蹤某個生成的名字最後來自哪個標頭；clangd 對巨集展開有「hover 顯示展開結果」的功能，在有 `compile_commands.json` 的 kernel tree 上非常好用。

## 動手練習

1. 把 `xmacro.c` 的 `COLOR_LIST` 加一個 `X(YELLOW, 0xffff00)`，不改任何其他地方，重新編譯執行。再對 `shape_name` 對照組做「加一個 `SHAPE_HEXAGON`」，體會兩者的差異。
2. 在 Linux v6.18 tree 裡找出 `cg_skb_verifier_ops` 的定義與使用者。你用了哪幾個搜尋指令？
3. 用「拆片段」的方法找出 `kernel/fork.c` 裡 `trace_task_newtask(` 的定義鏈，記錄每一層巨集的檔案與行號。
4. 寫一個 `#define DECLARE_COUNTER(name) static int name##_count; static void name##_inc(void) { name##_count++; }`，用它產生三個計數器。故意呼叫一個不存在的 `foo_inc()`，看編譯器的錯誤訊息怎麼描述。
5. 在 `isenabled.c` 加 `#define CONFIG_BROKEN 2`，預測 `IS_ENABLED(CONFIG_BROKEN)` 的結果再驗證。這對 Kconfig 的 tristate 設計說明了什麼？

## 本章重點整理

- `#` 把參數的拼寫變成字串，`##` 把兩個 token 黏成新名字；兩者的運算元都**不會先展開**，所以需要雙層巨集。
- X-macro 讓一份清單產生 enum、名稱表、ops 表與函式；清單可以是巨集參數，也可以是沒有 include guard 的獨立檔案（kernel `bpf_types.h`）。
- `__VA_ARGS__` 的零參數問題用 GNU 的 `, ##__VA_ARGS__` 或標準的 `__VA_OPT__` 解決；遞減數列跟在 `__VA_ARGS__` 後面是在數參數。
- kernel 的 `IS_ENABLED` 靠「拼出 `__ARG_PLACEHOLDER_1`、它展開出一個逗號、改變參數位置」在運算式中判斷巨集是否為 1。
- grep 找不到時：拆成前綴、核心詞、後綴分別搜，搜 `##` 本身，必要時 `gcc -E` 或 clangd。生成的名字會隨版本改變，一律釘版本。

## 自我檢核

- [ ] 不看筆記，解釋 `STR(VERSION_MAJOR)` 和 `XSTR(VERSION_MAJOR)` 為什麼結果不同，並說出 kernel 裡一個同樣結構的巨集。
- [ ] 有人在 code review 想把 `__is_defined(x)` 直接改成 `___is_defined(x)`「少一層比較快」。你會怎麼說服他？
- [ ] 畫出 `BPF_PROG_TYPE(BPF_PROG_TYPE_XDP, xdp, ...)` 這一行在 `bpf.h` 與 `verifier.c` 各自變成了什麼。
- [ ] 你在一個陌生專案看到 `handle_GET(req)` 被呼叫，grep 找不到 `handle_GET` 的定義。列出你會依序嘗試的三個搜尋。
- [ ] Lua 選擇手工平行清單而不是 X-macro，說出這個選擇的一個好處和一個代價。
- [ ] 為什麼 X-macro 的清單檔案不能有 include guard？

## 延伸閱讀

### 官方文件

- **The C Preprocessor（GCC 手冊）** — gcc.gnu.org/onlinedocs/cpp/
  - **讀哪裡**：「Stringizing」「Concatenation」「Variadic Macros」三節；「Argument Prescan」一節完整解釋「`#`/`##` 的運算元不先展開」與雙層巨集的原理。
  - **前提**：Ch 13。
  - **和本章的關聯**：本章所有規則的權威來源；`, ##__VA_ARGS__` 的 GNU 語意也在「Variadic Macros」一節。
- **ISO/IEC 9899:2011（C11）§6.10.3「Macro replacement」** — 草案 N1570
  - **讀哪裡**：6.10.3.1（參數替換與 prescan）、6.10.3.2（`#`）、6.10.3.3（`##`）、6.10.3.4（rescan 與不重複展開自己）。最後的 EXAMPLE 3–5 有標準委員會親自寫的刁鑽例子。

### 文章

- **The X Macro** — Walter Bright，Dr. Dobb's（2010）
  - **這篇說什麼**：D 語言作者介紹 X-macro 的來歷與用法，從組合語言時代的經驗講到 C。
  - **讀哪裡**：整篇，很短。
  - **為什麼值得讀**：把「為什麼要讓一份清單產生多個產物」的動機講得很清楚。Dr. Dobb's 網站已停止更新，以標題搜尋可找到封存版本。

### 原始碼

- **Linux v6.18 `include/linux/kconfig.h`**
  - **讀哪裡**：整個檔案不到 80 行，第 13–73 行是 `IS_ENABLED` 全家；註解解釋了每一步的意圖。
  - **前提**：本章「拆解 IS_ENABLED」一節。
  - **為什麼值得讀**：這是前置處理器技巧在 kernel 裡最被廣泛使用、也最常被問「這到底怎麼運作」的一段。
- **Linux v6.18 `include/linux/tracepoint.h` 開頭的長註解與 `Documentation/trace/tracepoints.rst`**
  - **讀哪裡**：`tracepoint.h` 第 501 行起解釋 `TRACE_EVENT` 用法的長註解（含 `trace_sched_switch` 的例子）；`tracepoints.rst` 說明 tracepoint 的使用方式。
  - **和本章的關聯**：症狀二的完整背景；Ch 51 會實際啟用 tracepoint。

### 書籍

- **《21st Century C》（2nd ed.）** — Ben Klemens（O'Reilly, 2014）
  - **讀哪幾章**：第 10 章「Better Structures」中的可變參數巨集與 designated initializer 技巧。示範如何用這些特性寫出「看起來像有具名參數」的 C API。
  - **定位**：從「寫」的角度看本章的技巧，有助於反過來理解別人為什麼這樣寫。

到這裡，C 語言層的慣用法已經走完一輪：控制流、運算式、指標、資料形狀、記憶體所有權、前置處理器。下一步是把它們全部用上，去解碼 Lua 與 Redis 裡最難讀的幾段真實程式碼。

→ [練習 B：解碼 user-space 天書片段（Lua、Redis）](./practice-b-decode-userspace-snippets.md)
