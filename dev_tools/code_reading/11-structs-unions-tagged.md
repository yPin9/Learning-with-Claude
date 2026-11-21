# Ch 11 — struct、union 與 tagged union：讀懂資料的形狀

> **目標**：看到一個 struct 或 union 宣告，能說出它扮演什麼角色（記錄、介面、物件、變體），能用 `offsetof` 與 `pahole` 量出它在記憶體裡的真實形狀，並能把 Lua 5.4.9 的 `TValue` 從 tag 位元到 union 成員完整拆開。
> **鴻溝**：本章主要練**慣用法鴻溝**（union、bitfield、匿名成員、designated initializer 這些「每個字都認得」的寫法），附帶一點**領域鴻溝**（為什麼一個直譯器要在乎 8 個 byte）。

> **環境**：WSL Ubuntu 22.04、x86_64、gcc 11.4、pahole v1.25；語料是 Lua 5.4.9、Redis 7.2.17、Linux v6.18。本章所有 layout 數字都是 **x86-64 LP64** 下量出來的；32 位元平台的對齊規則不同，數字會變。

## 為什麼需要這個？

Spinellis 在原書第 3 章花了很大篇幅講 struct 與 union，理由一句話：**資料結構決定了程式碼的形狀**。Fred Brooks 那句老話「給我看你的表格，我就不用看你的流程圖」講的是同一件事。[Ch 5](./05-entry-points-and-data-first.md) 提過「資料結構優先」的讀法，這一章給你讀資料結構時需要的 C 語言細節。

讀 C 程式碼時，struct 宣告是最密集的資訊來源，也是最容易被跳過的地方。很多人看到一個 30 個欄位的 struct 就直接往下找函式，結果讀函式時每三行就要回頭查「這個欄位是什麼」。更麻煩的是 union：同一塊記憶體在不同時候代表不同的東西，**宣告本身不會告訴你現在是哪一個**——那個資訊藏在別的欄位、別的巨集，甚至藏在呼叫者的上下文裡。

在 C 還沒有成熟的型別系統可依賴的年代（1970–80 年代），union 是唯一能在 C 裡表達「這個值可能是 A 或 B」的手段，也是唯一能「重新解讀位元」的合法管道之一。後來的語言把這件事做成語言特性：Pascal 的 variant record、ML/Haskell 的 algebraic data type、Rust 的 `enum`。C 沒有，所以 C 程式設計師手工打造它，這就是 **tagged union**（帶標籤的聯合）：一個 tag 欄位說明現在 union 裡裝的是哪一種，所有存取都先看 tag。Lua、Python（CPython 的 `PyObject` 走另一條路）、Ruby、各種 JSON 函式庫都靠這個模式活著。讀懂它，你就讀懂了一大半的直譯器與資料格式程式碼。

## 先建立直覺

把一個 struct 想成一張**表格的一列**：欄位依宣告順序排在記憶體裡，中間可能有編譯器塞的空白（padding），讓每個欄位落在它喜歡的對齊位置上。

把一個 union 想成**一個置物櫃格子**：格子大小等於最大那件物品，但同一時間只放一件。格子外面沒有標籤，放了什麼要靠你自己記。

tagged union 就是**在格子外面貼一張標籤**：

```
struct（一列記錄）           union（一個格子）           tagged union（格子 + 標籤）
┌────┬───────┬──────┐       ┌─────────────────┐        ┌─────────────────┬─────┐
│ a  │ (pad) │  d   │       │ i  或  n  或  p │        │ i  或  n  或  p │ tag │
└────┴───────┴──────┘       └─────────────────┘        └─────────────────┴─────┘
 每個欄位各有位置             所有成員共用位址 0           讀之前先看 tag 決定讀哪個
```

讀碼時對每個 struct/union 問三個問題：

1. **它扮演什麼角色？**（記錄、介面、物件、變體）
2. **它的不變量是什麼？**（哪些欄位必須一致，例如 tag 與 union 內容）
3. **誰負責維持那個不變量？**（通常是一組巨集或 accessor 函式）

第三個問題特別重要：Lua 從不讓你直接寫 `o->tt_ = 3`，而是透過 `setivalue(o, 42)` 同時寫值和寫 tag。找到這組「守門員巨集」，你就找到了讀這個資料結構的正門。

## struct 的三種角色

同樣是 `struct`，在程式裡的角色不同，讀法也不同。

| 角色 | 特徵 | 例子 | 讀法 |
|---|---|---|---|
| **記錄（record）** | 純資料、欄位彼此獨立 | Redis `struct redisObject` | 看欄位與註解，猜它代表什麼實體 |
| **介面（interface）** | 幾乎全是函式指標 | kernel `struct file_operations`、Redis `dictType` | 找「誰填這張表」與「誰呼叫這些指標」（Ch 28） |
| **物件（object）** | 開頭是共同 header，被當成「基底類別」強轉 | Lua `GCObject` + `CommonHeader`、kernel 嵌入 `struct kobject` | 找共同 header，找轉型巨集（Ch 16、Ch 42） |

**記錄**最直觀。Redis 7.2.17 的物件 header：

```c
/* redis-7.2.17/src/server.h:900 */
struct redisObject {
    unsigned type:4;
    unsigned encoding:4;
    unsigned lru:LRU_BITS; /* LRU time (relative to global lru_clock) or
                            * LFU data (least significant 8 bits frequency
                            * and most significant 16 bits access time). */
    int refcount;
    void *ptr;
};
```

**介面**是一張函式指標表。Redis 的 `dictType`（`dict.h:51`）讓同一份雜湊表程式碼能服務不同型別的 key：`hashFunction`、`keyDup`、`keyCompare`、`keyDestructor`……讀到 `d->type->keyCompare(d, k1, k2)` 時，答案不在 `dict.c` 裡，而在建立這個 dict 時傳進去的那張表。這是**間接鴻溝**的入口，Ch 28 會專門處理。

**物件**是 C 裡模擬繼承的手法。Lua 5.4.9 的所有可回收物件開頭都是同一組欄位：

```c
/* lua-5.4.9/lobject.h:288 */
#define CommonHeader	struct GCObject *next; lu_byte tt; lu_byte marked

/* lua-5.4.9/lobject.h:292 */
typedef struct GCObject {
  CommonHeader;
} GCObject;
```

`TString`、`Table`、`Closure`、`lua_State` 的第一個成員都是 `CommonHeader`，所以 GC 可以把任何一個物件當成 `GCObject *` 串在 `next` 鏈上，看 `tt` 決定它到底是什麼。C 標準保證「指向 struct 的指標，轉型後指向它的第一個成員」（C11 6.7.2.1p15），這是整個技巧的法律依據。

> 如果你對函式指標陣列與「指標的指標」還不熟，先回看 [Ch 10 — 指標慣用法](./10-pointer-idioms.md)。

## 量出資料的真實形狀：padding 與 offsetof

宣告順序不等於你以為的大小。先看一個故意排壞的 struct：

```c
/* scratch/11/layout.c（節錄） */
struct bad  { char a; double d; char b; int i; };
struct good { double d; int i; char a; char b; };

typedef union Value { void *p; long long i; double n; unsigned char ub; } Value;
typedef struct TValue { Value value_; unsigned char tt_; } TValue;

struct NaiveNode { TValue val; TValue key; int next; };
struct NodeKey { Value value_; unsigned char tt_; unsigned char key_tt; int next; Value key_val; };
```

用 `sizeof` 與 `offsetof` 印出來（`gcc -g -Wall -o layout layout.c && ./layout`）：

```
bad : size=24  a@0 d@8 b@16 i@20
good: size=16  d@0 i@8 a@12 b@13
Value=8 TValue=16 (tt_@8)
NaiveNode=40 NodeKey=24 (key_tt@9 next@12 key_val@16)
```

同樣四個欄位，`bad` 24 bytes、`good` 16 bytes。規則是：x86-64 SysV ABI 要求 `double` 對齊到 8、`int` 對齊到 4，struct 整體大小要是最大對齊的倍數。`a` 佔 1 byte 之後，`d` 必須等到 offset 8，中間 7 bytes 是空的。

`pahole`（Poke-A-Hole，dwarves 套件）讀 DWARF 除錯資訊，直接把洞標出來。注意：型別必須真的被某個變數用到，DWARF 才會有它；只出現在 `sizeof` 裡的型別，gcc 不一定輸出，`pahole` 會說 `type 'bad' not found`（我們第一次跑就踩到，補上全域變數 `struct bad g_bad;` 才看得到）。

```
$ pahole -C bad layout
struct bad {
	char                       a;                    /*     0     1 */

	/* XXX 7 bytes hole, try to pack */

	double                     d;                    /*     8     8 */
	char                       b;                    /*    16     1 */

	/* XXX 3 bytes hole, try to pack */

	int                        i;                    /*    20     4 */

	/* size: 24, cachelines: 1, members: 4 */
	/* sum members: 14, holes: 2, sum holes: 10 */
	/* last cacheline: 24 bytes */
};
```

`pahole --reorganize -C bad layout` 還會提議一個重排方案，最後一行寫 `/* saved 8 bytes! */`。讀碼時這個工具的用途不是幫你最佳化，而是**回答「這個欄位在第幾個 byte」**——當你在 gdb 裡看到 `*(rdi+0x10)`、在 crash dump 裡看到一個偏移量，`pahole` 告訴你那是哪個欄位。Ch 50 會在 kernel `vmlinux` 上用它。

### 真實案例：Lua 的 `Node` 為什麼長這樣

上面的 `NaiveNode` 是「天真」的雜湊表節點：key 和 value 各是一個完整的 `TValue`，加一個 `next`。40 bytes。Lua 5.4.9 實際的寫法：

```c
/* lua-5.4.9/lobject.h:699 */
typedef union Node {
  struct NodeKey {
    TValuefields;  /* fields for value */
    lu_byte key_tt;  /* key type */
    int next;  /* for chaining */
    Value key_val;  /* key value */
  } u;
  TValue i_val;  /* direct access to node's value as a proper 'TValue' */
} Node;
```

對真正 build 出來的 Lua（`make MYCFLAGS="-std=c99 -DLUA_USE_LINUX -g"`）跑 `pahole`：

```
$ pahole -C NodeKey lua
struct NodeKey {
	Value                      value_;               /*     0     8 */
	lu_byte                    tt_;                  /*     8     1 */
	lu_byte                    key_tt;               /*     9     1 */

	/* XXX 2 bytes hole, try to pack */

	int                        next;                 /*    12     4 */
	Value                      key_val;              /*    16     8 */

	/* size: 24, cachelines: 1, members: 5 */
	/* sum members: 22, holes: 1, sum holes: 2 */
	/* last cacheline: 24 bytes */
};
```

24 bytes，比天真版省 40%。技巧是：value 的 tag `tt_` 後面原本有 7 bytes padding，Lua 把 key 的 tag 和 `next` 塞進那個洞裡。外層再包一個 union，讓 `i_val` 和 `u` 的前 16 bytes 重疊——程式碼要「把 value 當成一個正常 `TValue`」時就讀 `node->i_val`，不用重新組裝。原始碼上方的註解（`lobject.h:692–697`）就在講這件事。

這是讀碼時一個重要的 beacon（Ch 2 的「信標」）：**看到一個 union 的兩個成員「開頭長得一樣」，它通常是在做 layout 重疊，不是在表達變體。**

## union 的三種用途

讀到 `union` 時，先判斷它屬於哪一種。三種的讀法完全不同。

| 用途 | 判斷線索 | 誰記得「現在是哪個成員」 |
|---|---|---|
| **節省空間（變體）** | 旁邊有 tag/type/kind 欄位 | tag 欄位（tagged union） |
| **型別雙關（type punning）** | 寫一個成員、立刻讀另一個 | 沒有人——就是要重新解讀位元 |
| **layout 重疊** | 成員的前綴欄位相同 | 不需要記，前綴本來就一樣 |

### 用途二：型別雙關

```c
/* scratch/11/tagged.c（節錄） */
union { double d; uint64_t u; } pun = { .d = 3.5 };
uint64_t bits; double x = 3.5; memcpy(&bits, &x, sizeof bits);
printf("3.5 的位元: union=0x%016llx memcpy=0x%016llx\n", ...);
```

```
3.5 的位元: union=0x400c000000000000 memcpy=0x400c000000000000
```

兩種寫法結果相同。C11 允許透過 union 重新解讀（6.5.2.3 的註腳 95），C++ 則把它列為未定義行為；`memcpy` 在兩邊都合法，而且現代編譯器會把它最佳化成一條 `movq`。讀碼時看到 union 雙關，知道作者的意圖是「看位元」即可；自己寫新碼時，`memcpy` 是比較不會惹爭議的選擇。

### 用途三：layout 重疊——Lua 的 `GCUnion`

```c
/* lua-5.4.9/lstate.h:353 */
union GCUnion {
  GCObject gc;  /* common header */
  struct TString ts;
  struct Udata u;
  union Closure cl;
  struct Table h;
  struct Proto p;
  struct lua_State th;  /* thread */
  struct UpVal upv;
};

/*
** ISO C99, 6.7.2.1 p.14:
** "A pointer to a union object, suitably converted, points to each of
** its members [...], and vice versa."
*/
#define cast_u(o)	cast(union GCUnion *, (o))

/* macros to convert a GCObject into a specific value */
#define gco2ts(o)  \
	check_exp(novariant((o)->tt) == LUA_TSTRING, &((cast_u(o))->ts))
```

這個 union **從來沒有被當成變數宣告過**。它存在的唯一目的是讓 `gco2ts(o)` 能合法地把 `GCObject *` 轉成 `TString *`：先轉成 union 指標，再取成員。作者把標準條文直接貼在註解裡，這是 Lua 原始碼的風格——它很在乎「每一步都有標準背書」。`check_exp` 在一般 build 是空的，在 debug build（`LUAI_ASSERT`）會檢查 tag 對不對，等於在轉型時順便驗證 tagged union 的不變量。

## 底層機制：Lua 的 `TValue` 完整拆解

這一節把 Lua 5.4.9 的值表示法從頭拆到尾。每一個 Lua 值——數字、字串、table、函式——在 C 裡都是一個 `TValue`。

### 第一層：union `Value` 放內容

```c
/* lua-5.4.9/lobject.h:49 */
typedef union Value {
  struct GCObject *gc;    /* collectable objects */
  void *p;         /* light userdata */
  lua_CFunction f; /* light C functions */
  lua_Integer i;   /* integer numbers */
  lua_Number n;    /* float numbers */
  /* not used, but may avoid warnings for uninitialized value */
  lu_byte ub;
} Value;
```

所有成員最大是 8 bytes（指標、`long long`、`double`），所以 `Value` 是 8 bytes。字串、table 這類大東西不放在 union 裡，union 只放指向它們的 `gc` 指標。

### 第二層：struct `TValue` 加上 tag

```c
/* lua-5.4.9/lobject.h:65 */
#define TValuefields	Value value_; lu_byte tt_

typedef struct TValue {
  TValuefields;
} TValue;
```

為什麼欄位要用巨集 `TValuefields` 定義，而不是直接寫在 struct 裡？因為其他 struct（`NodeKey`、`StackValue` 裡的 `tbclist`）需要「把 TValue 的欄位攤平放在自己開頭」，好讓 layout 重疊成立。如果寫成 `TValue val;` 嵌入，就沒辦法把 `key_tt` 塞進 `tt_` 後面的 padding 了。

### 第三層：tag 位元的編碼

```c
/* lua-5.4.9/lobject.h:34 */
/*
** tags for Tagged Values have the following use of bits:
** bits 0-3: actual tag (a LUA_T* constant)
** bits 4-5: variant bits
** bit 6: whether value is collectable
*/

/* add variant bits to a type */
#define makevariant(t,v)	((t) | ((v) << 4))
```

```
 tt_ 的 8 個 bit
 ┌───┬───┬───┬───┬───┬───┬───┬───┐
 │ 7 │ 6 │ 5 │ 4 │ 3 │ 2 │ 1 │ 0 │
 └───┴───┴───┴───┴───┴───┴───┴───┘
       │   └─┬─┘   └──────┬──────┘
       │  variant    基本型別 LUA_T*（lua.h:65–73，0=nil 3=number 4=string 5=table 6=function）
  collectable（BIT_ISCOLLECTABLE = 1<<6，lobject.h:298）
```

`novariant(t)` 是 `(t) & 0x0F`，`ctb(t)`（"collectable bit"）是 `(t) | BIT_ISCOLLECTABLE`。於是：

- 整數 `LUA_VNUMINT = makevariant(3, 0) = 0x03`
- 浮點 `LUA_VNUMFLT = makevariant(3, 1) = 0x13`
- 短字串 `ctb(LUA_VSHRSTR) = ctb(makevariant(4, 0)) = 0x44`

### 用真的 Lua 驗證

光看巨集算不保險，直接從 Lua 的 stack 上讀 `tt_`。這支程式用了內部標頭 `lstate.h`，只是為了觀察；正常的 C API 使用者不該這樣做：

```c
/* scratch/11/realtt.c */
#include <stdio.h>
#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"
#include "lstate.h"   /* 內部標頭：為了直接看 TValue 的 tt_ */

static void top_tt(lua_State *L, const char *expr) {
    TValue *o = s2v(L->top.p - 1);
    printf("%-28s tt_=0x%02x  ttype=%d  lua_typename=%s\n",
           expr, rawtt(o), ttype(o), lua_typename(L, lua_type(L, -1)));
    lua_pop(L, 1);
}

int main(void) {
    lua_State *L = luaL_newstate(); luaL_openlibs(L);
    const char *exprs[] = { "return 42", "return 3.5", "return 'hi'",
        "return string.rep('x', 50)", "return {}", "return print", "return function() end",
        "return true", "return nil" };
    for (size_t k = 0; k < sizeof exprs / sizeof exprs[0]; k++) {
        luaL_dostring(L, exprs[k]);
        top_tt(L, exprs[k]);
    }
    lua_close(L);
    return 0;
}
```

```
$ gcc -std=c99 -I../pb/lua -o realtt realtt.c ../pb/lua/liblua.a -lm -ldl && ./realtt
return 42                    tt_=0x03  ttype=3  lua_typename=number
return 3.5                   tt_=0x13  ttype=3  lua_typename=number
return 'hi'                  tt_=0x44  ttype=4  lua_typename=string
return string.rep('x', 50)   tt_=0x54  ttype=4  lua_typename=string
return {}                    tt_=0x45  ttype=5  lua_typename=table
return print                 tt_=0x16  ttype=6  lua_typename=function
return function() end        tt_=0x46  ttype=6  lua_typename=function
return true                  tt_=0x11  ttype=1  lua_typename=boolean
return nil                   tt_=0x00  ttype=0  lua_typename=nil
```

逐行對照：

- `42` 和 `3.5` 在 Lua 層面都是 `number`，但 tag 分別是 `0x03` 和 `0x13`：variant bit 區分整數與浮點。這就是 Lua 5.3 引入整數子型別之後的實作方式。
- `'hi'` 是 `0x44`（短字串，collectable），50 字元的字串是 `0x54`（`makevariant(4,1)` 加 collectable = 長字串）。分界在 `LUAI_MAXSHORTLEN`，`llimits.h:216` 定義為 40。短字串會被 intern，長字串不會。
- `print` 是 `0x16`：`LUA_VLCF`（light C function，`lobject.h:589`），**沒有** collectable bit，因為它只是一個 C 函式指標，不需要 GC 管。Lua 寫的 `function() end` 是 `0x46`，是 GC 物件。
- `true` 是 `0x11`：布林值的真假直接編碼在 variant bit 裡（`LUA_VTRUE = makevariant(LUA_TBOOLEAN, 1)`，`lobject.h:240`），union 裡什麼都不放。

### 守門員巨集

```c
/* lua-5.4.9/lobject.h:344 */
#define setivalue(obj,x) \
  { TValue *io=(obj); val_(io).i=(x); settt_(io, LUA_VNUMINT); }

/* lua-5.4.9/lobject.h:91 */
#define checktag(o,t)		(rawtt(o) == (t))
#define checktype(o,t)		(ttype(o) == (t))
```

`setXvalue` 家族同時寫值和 tag，`ttisX` 家族檢查 tag，`Xvalue` 家族（如 `ivalue(o)`）讀值並在 debug build 斷言 tag 正確。讀 `lvm.c` 時你會看到滿滿的這些巨集——認得它們的命名規則（`set*value`、`ttis*`、`*value`、`chg*value`），一行程式碼的意思就浮出來了。`checktag` 比對完整 tag（含 variant），`checktype` 只比基本型別：`ttisnumber` 用 `checktype`，`ttisinteger` 用 `checktag`。

### 失敗示範：tag 和內容不一致

tagged union 的不變量沒有任何語言機制保護。我們的迷你版（`scratch/11/tagged.c`）故意違反它：

```c
TValue bad = { .tt_ = V_NUMINT };
bad.value_.n = 3.5;          /* tag 說 int，內容寫 double */
show("bad", &bad);
```

```
a          tt_=0x03  type=3  variant=0  collectable=0  -> int 42
b          tt_=0x13  type=3  variant=1  collectable=0  -> float 3.5
c          tt_=0x44  type=4  variant=0  collectable=1  -> string "hi"
d          tt_=0x00  type=0  variant=0  collectable=0  -> nil
bad        tt_=0x03  type=3  variant=0  collectable=0  -> int 4615063718147915776
```

`4615063718147915776` 就是 `0x400c000000000000`——3.5 的 IEEE 754 位元被當成整數讀出來。編譯器不警告、執行時不報錯。這就是為什麼 Lua 要用巨集把「寫值」和「寫 tag」綁在一起：**一旦允許分開寫，遲早有人只寫一半。**

## bitfield：把多個小欄位塞進一個字

Redis 的 `redisObject` 把 type（4 bit）、encoding（4 bit）、lru（24 bit）塞進一個 32 位元字。對 build 好的 `redis-server` 跑 `pahole`：

```
$ pahole -C redisObject redis-server
struct redisObject {
	unsigned int               type:4;               /*     0: 0  4 */
	unsigned int               encoding:4;           /*     0: 4  4 */
	unsigned int               lru:24;               /*     0: 8  4 */
	int                        refcount;             /*     4     4 */
	void *                     ptr;                  /*     8     8 */

	/* size: 16, cachelines: 1, members: 5 */
	/* last cacheline: 16 bytes */
};
```

`0: 4  4` 的意思是「位於 byte 0、從第 4 個 bit 開始、所在儲存單位 4 bytes」。每個 Redis key 都有一個 `robj`，16 bytes 對比不用 bitfield 的 24 bytes，乘上幾千萬個 key 就是真金白銀。

自己寫一個仿製品來看位元怎麼排（`scratch/11/bits.c`）：

```
$ gcc -std=c11 -Wall -Wextra -o bits bits.c && ./bits
bits.c: In function ‘main’:
bits.c:27:14: warning: unsigned conversion from ‘int’ to ‘unsigned char:4’ changes value from ‘17’ to ‘1’ [-Woverflow]
   27 |     o.type = 17;  /* 超過 4 bit 會怎樣？ */
      |              ^~
sizeof(robj)=16  first word=0xabcdefb3
o.type after =17 : 1
```

設定 `type=3, encoding=11 (0xb), lru=0xABCDEF` 之後，第一個字是 `0xabcdefb3`：`type` 在最低 4 bit、`encoding` 在其上、`lru` 在最高 24 bit。這是 **gcc 在 x86-64 上**的排法；C 標準把 bitfield 的配置順序、是否跨儲存單位都留給實作決定（C11 6.7.2.1p11），所以 bitfield **不能**拿來描述檔案格式或網路封包的位元佈局。寫入 17 被截成 1，gcc 有警告，但如果值是執行期才知道的變數，就是無聲截斷。

讀碼時 bitfield 有兩個陷阱：

1. **不能取位址**。`offsetof(struct robj, encoding)` 直接編譯失敗：

```
bf_off.c:3:25: error: attempt to take address of bit-field structure member ‘encoding’
    3 | int x = offsetof(struct robj, encoding);
```

   Linux v6.18 的 `struct sk_buff` 為了能算出一組 bitfield 的位置，在它們前面放了一個零長度陣列當標記：

```c
/* include/linux/skbuff.h:953（v6.18） */
	__u8			__cloned_offset[0];
	/* public: */
	__u8			cloned:1,
				nohdr:1,
				fclone:2,
				peeked:1,
				head_frag:1,
				pfmemalloc:1,
				pp_recycle:1; /* page_pool recycle indicator */
```

   看到 `xxx_offset[0]` 這種「沒有大小的陣列欄位」，它通常是位置標記，不存資料。

2. **同一個字的 bitfield 寫入不是原子的**。改 `type` 會讀出整個字、改 4 bit、寫回去；兩個執行緒分別改 `type` 和 `lru` 會互相覆蓋。kernel 對共享 bitfield 有很多鎖的規則，Ch 30 會再碰到。

## 匿名 struct/union 與 designated initializer

C11 正式納入匿名 struct/union（GNU C 更早就支援）：內層成員直接掛在外層名下，不用多寫一層名字。

```c
/* scratch/11/bits.c（節錄） */
struct pkt {
    int kind;
    union {                     /* 匿名 union：成員直接掛在 pkt 上 */
        struct { int x, y; };   /* 匿名 struct */
        double weight;
    };
};
enum color { RED, GREEN, BLUE, NCOLOR };
static const char *const color_name[NCOLOR] = {
    [BLUE] = "blue", [RED] = "red",       /* 順序可以亂寫，位置由索引決定 */
};
```

```
p.x=10 p.y=20  offsetof(x)=8 offsetof(weight)=8 sizeof=16
color_name[0] = red
color_name[1] = (NULL)
color_name[2] = blue
```

`p.x` 和 `p.weight` 的 offset 都是 8：同一個匿名 union 裡。讀 kernel 的 `struct page`（`include/linux/mm_types.h:78`，v6.18）時這是必備技能——它的第二個欄位是一個巨大的匿名 union，裡面又是好幾個匿名 struct，分別給 page cache、page_pool、slab 等不同用途，每個用途對同一組 word 有不同解讀：

```c
/* include/linux/mm_types.h:78（v6.18，節錄） */
struct page {
	memdesc_flags_t flags;		/* Atomic flags, some possibly
					 * updated asynchronously */
	/*
	 * Five words (20/40 bytes) are available in this union.
	 * WARNING: bit 0 of the first word is used for PageTail(). That
	 * means the other users of this union MUST NOT use the bit to
	 * avoid collision and false-positive PageTail().
	 */
	union {
		struct {	/* Page cache and anonymous pages */
			...
			struct address_space *mapping;
			...
		};
		struct {	/* page_pool used by netstack */
			unsigned long pp_magic;
			struct page_pool *pp;
			...
```

這裡的 tag 在哪？**不在這個 struct 裡的某個欄位**——它分散在 `flags` 的位元、`page_type`，以及「誰配置了這個 page」的上下文。這正是 union 最難讀的情況：讀 `page->mapping` 之前，你得先知道這個 page 現在屬於哪個子系統。這是**領域鴻溝**，Ch 19 和 Ch 26 會補上。

**designated initializer**（指定初始化）是 C99 的特性：用 `.欄位 =` 或 `[索引] =` 指定要初始化誰，沒提到的成員一律歸零。上面 `color_name[1]` 是 `NULL` 就是這個規則。它在大型專案裡有兩個主要用途：

- **填 ops table**：kernel `fs/pipe.c:1246`（v6.18）

```c
const struct file_operations pipefifo_fops = {
	.open		= fifo_open,
	.read_iter	= fifo_pipe_read,
	.write_iter	= fifo_pipe_write,
	.poll		= pipe_poll,
	.unlocked_ioctl	= pipe_ioctl,
	.release	= pipe_release,
	.fasync		= pipe_fasync,
	.splice_write	= iter_file_splice_write,
};
```

  `struct file_operations` 有幾十個欄位，沒寫的（例如 `.mmap`）都是 `NULL`，呼叫端會檢查 `NULL` 並回傳錯誤或走預設路徑。讀碼時：**沒列出來的欄位，就是這個實作不支援的操作。**

- **以 enum 為索引的表**：kernel `lib/errname.c` 用 `[err + ...] = "-" #err` 建 errno 名稱表，Ch 14 拆解它。

Redis 7.2.17 `config.c:1860` 的 `createBoolConfig` 巨集則是把 designated initializer 包進巨集裡，一行展開成一整個 struct 初始化子；那是 Ch 14 的題材。

## typedef：用還是不用

Lua 和 Redis 大量使用 `typedef struct X X;`，Linux kernel 則幾乎不用。在 v6.18 tree 粗略數一下：

```
$ cat lua-5.4.9/*.[ch] | grep -c "typedef struct"
47
$ cat redis-7.2.17/src/*.[ch] | grep -c "typedef struct"
264
$ cat fs/*.c include/linux/fs.h | grep -c 'typedef struct'      # 在 linux/ 下
2
```

kernel 的立場寫在 `Documentation/process/coding-style.rst` 第 5 節「Typedefs」（v6.18 第 359 行起），開頭就說：

> It's a **mistake** to use typedef for structures and pointers.

理由是讀者看到 `vps_t a;` 不知道 `a` 是什麼，看到 `struct virtual_container *a;` 立刻知道它是指標、指向一個 struct。文件列出 typedef 合理的五種情況：(a) 完全不透明的物件（如 `pte_t`，只能用 accessor 存取）、(b) 有理由在不同設定下變成不同寬度的整數型別、(c) 用 sparse 建立新型別做型別檢查、(d) 與 C99 標準型別等價的 `u8/u16/u32/u64`、(e) 給 userspace 看的 `__u32` 這類型別。結論那句（第 437 行）：「the rule should basically be to NEVER EVER use a typedef unless you can clearly match one of those rules」，以及「a pointer, or a struct that has elements that can reasonably be directly accessed should **never** be a typedef」。

kernel 裡少數 `typedef struct` 正好落在 (a) 類，例如 `include/linux/types.h:181` 的 `typedef struct { int counter; } atomic_t;`——包一層 struct 的目的是**阻止**你寫 `a + 1`，逼你用 `atomic_inc()`。

讀碼的實務結論：

| 看到 | 心裡要想 |
|---|---|
| `TValue *o`（Lua） | typedef 藏了 struct，按 tag 看 `lobject.h` |
| `sds s`（Redis） | `typedef char *sds;`——它是指標！而且指在 header **之後**（Ch 12） |
| `pte_t pte`（kernel） | 不透明型別，不要直接戳欄位，找 accessor |
| `struct inode *inode`（kernel） | 名字就是答案 |

`sds` 是 typedef 隱藏指標的最佳反例：不看 `sds.h:43` 你不會知道 `sds` 可以直接傳給 `printf("%s")`，也不會知道 `free(s)` 是錯的。

## 對比與取捨

| 表示「多型值」的方法 | 大小（x86-64） | 型別安全 | 誰在用 |
|---|---|---|---|
| tagged union（tag + union） | 16 bytes（8 值 + 1 tag + 7 padding） | 靠巨集紀律 | Lua `TValue`、多數 JSON 函式庫 |
| 一律指向堆積物件，物件 header 帶型別 | 8 bytes 指標 + 堆積物件 | 靠 header 檢查 | CPython `PyObject *`、Redis `robj` |
| NaN-boxing（把值塞進 double 的 NaN 空間） | 8 bytes | 更靠紀律、平台相依 | LuaJIT、部分 JavaScript 引擎 |
| pointer tagging（用指標低位元當 tag） | 8 bytes | 同上 | Redis 7.2 `dict.c` 的 entry 指標（練習 B 第 8 題） |

Lua 選 tagged union 的理由寫在它的設計論文裡（見延伸閱讀）：純 ANSI C 可攜性優先，NaN-boxing 依賴浮點格式與指標寬度，不符合 Lua「在任何 C 編譯器上都能跑」的目標。CPython 的「全部是堆積物件」讓整數也要配置記憶體，小整數只能靠快取補救；Lua 的整數和浮點直接放在 `TValue` 裡，不配置。

## 踩雷集錦

1. **「struct 大小 = 欄位大小加總」**：錯。`struct bad` 欄位總和 14 bytes，實際 24。讀二進位格式、算陣列步長、看 crash dump 偏移時，一律用 `offsetof`/`pahole` 量，不要心算。
2. **「union 讀錯成員會報錯」**：不會。`bad` 那個例子印出 `4615063718147915776`，安安靜靜。tagged union 的安全完全靠「所有寫入都經過守門員巨集」這個紀律。讀碼時看到有人繞過巨集直接寫 `->tt_` 或 `->value_`，那一行值得多看一眼。
3. **「bitfield 可以描述硬體暫存器或封包格式」**：在 gcc/x86-64 上剛好是 LSB-first，但標準不保證。kernel 的網路標頭（`struct iphdr`）用 `#if defined(__LITTLE_ENDIAN_BITFIELD)` 兩套宣告來處理這件事，正好說明它不可攜。
4. **「typedef 讓程式碼比較好讀」**：對作者而言或許是，對讀者而言常常相反。`sds` 是 `char *`、`StkId` 是 `StackValue *`——不追 typedef，你連「這是不是指標」都不知道。遇到不認得的型別名，第一件事是找它的 typedef。
5. **「沒初始化的欄位是垃圾值」**：只要用了 designated initializer（或任何部分初始化子），沒提到的成員一律歸零（C11 6.7.9p21）。kernel ops table 依賴這條規則：沒寫的函式指標保證是 `NULL`。反過來，`struct foo x;` 在函式內沒有任何初始化子，才是真的垃圾值。

## 進階：再往深一層

- **flexible array member**：Lua 的 `TString` 最後一個成員是 `char contents[1];`（`lobject.h:395`），這是 C89 時代的「struct hack」；Redis 的 `sdshdr8` 用 C99 的 `char buf[];`。兩者都表示「字串內容緊接在 header 後面，一次配置」。`pahole` 對 `TString` 顯示 `contents` 在 offset 24、整體 `size: 32`，配置時 Lua 用 `sizelstring(l)`（`lstring.h:26`，即 `offsetof(TString, contents) + (l + 1)`）計算，而不是 `sizeof(TString)`。Ch 15 會講 kernel 怎麼從 `[0]`、`[1]` 遷移到 `[]` 以及 `__counted_by`。
- **`__attribute__((packed))`**：Redis 的 sds header 全部 packed，`pahole -C sdshdr8 redis-server` 顯示 `size: 3`，沒有任何 padding——因為 sds 要靠 `s[-1]` 找到 flags byte，header 必須緊貼字串。代價是非對齊存取；在 x86 上便宜，在某些 ARM 上可能很貴。下一章展開。
- **從 tagged union 到 Ch 16**：kernel 很少用 tagged union，它用的是「嵌入 + `container_of`」：把通用 struct（如 `list_head`）嵌在具體 struct 裡，從成員指標反推外層。概念上和 Lua 的 `CommonHeader` 是同一家族，方向相反——Lua 的 header 在開頭所以直接轉型，kernel 的成員可以在任何位置所以要減 offset。
- **用 `ptype /o`**：gdb 8.1 之後的 `ptype /o struct bad` 也能印出 offset 與洞，在沒有 pahole 的環境可以替代。

## 動手練習

1. 對 `scratch/pb/lua/lua` 跑 `pahole -C lua_State lua` 和 `pahole -C Table lua`，找出所有 hole。挑一個說明它為什麼存在（哪個欄位的對齊造成的）。
2. 在 Redis 原始碼找出 `quicklistNode`（`quicklist.h:46`）。它有 7 個 bitfield，加總幾個 bit？用 `pahole` 驗證它們全部擠在同一個 4-byte 單位裡。
3. 修改 `realtt.c`，加上 `return 2^53`、`return 1//0`（會報錯，觀察 `luaL_dostring` 回傳後 stack 上是什麼）、`return io.stdout`（full userdata）。先根據 `lobject.h` 預測 tag，再跑程式驗證。
4. 把 `scratch/11/tagged.c` 的 `show()` 改成先用 `novariant()` 做第一層 switch、再用 variant 做第二層——這就是 `lvm.c` 很多地方的分派結構。

## 本章重點整理

- struct 有記錄、介面、物件三種角色；讀之前先分類，介面要找填表的人，物件要找共同 header 與轉型巨集。
- union 有變體、型別雙關、layout 重疊三種用途；只有變體需要 tag，而 tag 的不變量只靠巨集紀律維持。
- Lua `TValue` = 8-byte `Value` union + 1-byte `tt_`；`tt_` 的 bit 0–3 是基本型別、4–5 是 variant、6 是 collectable。
- 量 layout 用 `offsetof` 和 `pahole`，不要心算；bitfield 的位元順序是實作定義的。
- kernel 反對 typedef struct，理由是讀者需要看見型別的本質；Lua/Redis 大量用 typedef，讀它們時要先把 typedef 追到底。

## 自我檢核

- [ ] 不看筆記，畫出 Lua `tt_` 的 8 個 bit 各代表什麼，並算出長字串、table、Lua closure 的 tag 值。
- [ ] 有人說「`Node` 用 union 是為了省記憶體，因為 key 和 value 不會同時存在」——這句話錯在哪裡？
- [ ] 給你一個 struct，你能在不跑程式的情況下預估它的大小嗎？預估完之後，你會用哪個工具驗證？
- [ ] 解釋為什麼 `struct page` 的 union 比 Lua `TValue` 難讀（提示：tag 在哪裡）。
- [ ] 如果你要在 kernel 裡新增一個型別，什麼情況下 coding style 允許你用 typedef？舉出 kernel 裡一個合理的例子。
- [ ] 說出兩個 bitfield 不適合的場景。

## 延伸閱讀

### 論文

- **[The Implementation of Lua 5.0](https://www.lua.org/doc/jucs05.pdf)** — Roberto Ierusalimschy, Luiz Henrique de Figueiredo, Waldemar Celes，Journal of Universal Computer Science 11(7)（2005）
  - **核心貢獻**：Lua 作者親自說明 5.0 的值表示法、table 的 array/hash 混合設計、register-based VM。
  - **讀哪裡**：第 3 節「The Representation of Values」與第 4 節「Tables」。5.0 還沒有整數子型別和 variant bit，但 tagged union 的動機（為什麼不用 NaN-boxing、為什麼不全部配置在堆積上）完全適用。
  - **和本章的關聯**：本章 `TValue` 與 `Node` 的「為什麼」都在這篇。

### 官方文件 / 規範

- **Linux kernel coding style，第 5 節「Typedefs」與第 11 節「Data structures」** — v6.18 tree 的 `Documentation/process/coding-style.rst`（線上版 docs.kernel.org/process/coding-style.html）
  - **讀哪裡**：第 359–441 行（Typedefs）；第 763 行起（Data structures，講 refcount 與資料結構可見性）。
  - **前提**：無。
  - **和本章的關聯**：kernel 為什麼幾乎不 typedef struct，原文比任何轉述都清楚。
- **ISO/IEC 9899:2011（C11）§6.7.2.1「Structure and union specifiers」** — 草案 N1570 可公開取得
  - **讀哪裡**：p11（bitfield 配置是實作定義）、p13（匿名成員）、p15（指向 struct 的指標等於指向第一個成員）、p16（union 指標）。§6.7.9 p21 是「沒提到的成員歸零」。
  - **和本章的關聯**：Lua 原始碼註解引用的條文就在這裡；讀碼遇到「這樣轉型合法嗎」時的最終仲裁。

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：第 3.2–3.5 節（struct、union、動態配置、typedef 的讀法）。例子是 NetBSD 與 Apache，觀念與本章一致。
  - **定位**：本章的原型；看原書如何把 struct 歸類為「把資料組在一起、從函式回傳多個值、建構鏈結結構、映射硬體與檔案格式」，可以和本章的三種角色對照。
- **《Expert C Programming: Deep C Secrets》** — Peter van der Linden（Prentice Hall, 1994）
  - **讀哪幾章**：第 3 章「Unscrambling Declarations in C」——讀複雜宣告的方法，對解 typedef 鏈很有用。書很老，但 C 宣告語法沒變。

### 工具文件

- **pahole(1) man page**（dwarves 套件）
  - **讀哪裡**：`-C`、`--reorganize`、`--expand_types` 三個選項。Arnaldo Carvalho de Melo 寫這個工具最初就是為了找 kernel struct 的洞。
  - **和本章的關聯**：本章所有 layout 輸出都出自它；Ch 50 會在 vmlinux 上用。

有了資料的形狀，下一個問題是這些資料從哪裡來、什麼時候消失——誰配置、誰釋放。

→ [Ch 12 動態記憶體與所有權慣例：誰配置、誰釋放](./12-memory-ownership-conventions.md)
