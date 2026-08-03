# Ch 5 — Lua 的值表示與 table

> **目標**：拆開 Ch 4 裡 `R[A]`、`R[B]` 裝的那個 `TValue`——一個 tagged union，怎麼用一個小小的 struct 同時能是 nil、boolean、整數、浮點、字串、table、function。讀懂 `lua_State`/`CallInfo` 怎麼在一條 data stack 上佈局呼叫堆疊。最後攻 `ltable.c`：Lua 唯一的複合資料結構，一個 table 同時有 array part + hash part 的混合設計，以及它何時 rehash。

> **目標codebase**：Lua `v5.4.7`（commit `1ab3208`）

## 為什麼需要這個？

上一章讀 VM 時我們一直說「register 裡的值」「把值取出來相加」，但迴避了一個問題：**一個 register slot 裡到底裝什麼？** Lua 是動態型別，同一個變數這行是整數、下行是字串。C 沒有這種東西，所以 Lua 得自己造一個「能裝任何型別」的容器——`TValue`。這是所有動態語言 runtime 都要解決的第一個問題，讀懂 Lua 的解法，你就有了讀 CPython `PyObject`、Ruby `VALUE`、V8 tagged pointer 的模板。

第二個要讀的是 **table**。Lua 只有一種複合結構：table。陣列是 table、字典是 table、物件是 table、命名空間是 table。這種「一個結構統治一切」的設計，逼 Lua 把 table 做到極致——它內部同時是陣列和雜湊表，會根據你怎麼用自動切換。這是本課「混合資料結構」pattern 的來源（Ch 7 會收成卡片）。

## 先建立直覺：一個值 = 值 + 標籤

動態型別的核心難題：C 的變數型別在編譯期就定死，但 Lua 變數的型別要到執行期才知道。解法只有一個方向——**每個值自己帶著它的型別標籤跑**。

```
   一個 Lua 值 = ┌──────────────┬──────┐
                 │  實際的資料   │ 標籤 │
                 │  (8 bytes)   │(1B)  │
                 └──────────────┴──────┘
                       ▲            ▲
              可能是整數/指標/    告訴你上面那 8 bytes
              浮點，看標籤解讀     該當成什麼型別讀
```

「資料」那格用 C 的 **union** 實作：同一塊記憶體，可以當整數讀、當浮點讀、當指標讀，看你怎麼解讀。「標籤」那格告訴你**該怎麼解讀**。這就是 **tagged union**。整個 runtime 對值的每一次操作，第一步都是看標籤：「這是整數嗎？是就走整數加法；是 table 嗎？是就查 metamethod。」

## `Value` union 與 `TValue`

先看 union 本身。`lobject.h`（v5.4.7）：

```c
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

這一塊記憶體（一個機器字寬）可以是五種東西之一：

- `gc`：指向一個**可回收物件**（GCObject）。字串、table、function、thread 都是堆上物件，`gc` 指過去。這類值要 GC 管。
- `p`：light userdata，一個裸 C 指標（宿主塞進來的東西），Lua 不管它的生命週期。
- `f`：light C function，一個裸函式指標。
- `i`：整數（Lua 5.3 起整數浮點分家，這是整數那半）。
- `n`：浮點（`lua_Number`，通常是 `double`）。

注意前三個（`gc`/`p`/`f`）是指標，後兩個（`i`/`n`）是**直接存值**。整數和浮點小到可以直接塞進這個字寬，不必進堆、不必 GC——這是效能關鍵，算術不碰記憶體配置。

union 加上標籤才是完整的值。`lobject.h`（v5.4.7）：

```c
#define TValuefields	Value value_; lu_byte tt_

typedef struct TValue {
  TValuefields;
} TValue;
```

`TValue` = `value_`（那個 union）+ `tt_`（type tag，一個 byte）。這就是 Lua 的「值」，VM 的 register、table 的元素、C API stack 的每一格，裝的都是 `TValue`。

## 標籤怎麼編碼：type + variant

`tt_` 只有一個 byte，但 Lua 塞進了三層資訊。`lobject.h`（v5.4.7）的註解說得很清楚：

```
** bits 0-3: actual tag (a LUA_T* constant)
** bits 4-5: variant bits
** bit 6: whether value is collectable
```

- **bits 0–3**：基本型別，就是 `lua.h` 那組 `LUA_T*` 常數：`LUA_TNIL=0`、`LUA_TBOOLEAN=1`、`LUA_TNUMBER=3`、`LUA_TSTRING=4`、`LUA_TTABLE=5`、`LUA_TFUNCTION=6`……
- **bits 4–5**：variant（變體）。同一個基本型別下的子類。例如 number 分整數變體和浮點變體；string 分短字串和長字串；function 分 Lua closure、C closure、light C function。
- **bit 6**：是否可回收（GC 要不要管它）。

variant 的組合巨集（`lobject.h`，v5.4.7）：

```c
#define makevariant(t,v)	((t) | ((v) << 4))
```

它把基本型別 `t` 放低位、變體號 `v` 左移 4 位放進 bits 4–5。實例（`lobject.h`，v5.4.7）：

```c
#define LUA_VNIL	makevariant(LUA_TNIL, 0)
#define LUA_VFALSE	makevariant(LUA_TBOOLEAN, 0)
#define LUA_VTRUE	makevariant(LUA_TBOOLEAN, 1)
```

`LUA_VFALSE` 和 `LUA_VTRUE` 是兩個不同的 tag——**Lua 把 boolean 的 true/false 直接編進 tag**，值本身不用存。取值的巨集也分兩層（`lobject.h`，v5.4.7）：

```c
#define novariant(t)	((t) & 0x0F)          /* 只取 bits 0-3，基本型別 */
#define withvariant(t)	((t) & 0x3F)          /* 取 bits 0-5，含變體 */
#define ttype(o)	(novariant(rawtt(o)))     /* 值的基本型別 */
#define ttypetag(o)	withvariant(rawtt(o))     /* 值的完整型別+變體 */
```

**讀碼提示**：你會在 code 裡同時看到 `ttype(o)`（要基本型別，如「這是不是 number」）和 `ttypetag(o)`（要精確變體，如「這是整數還是浮點」）。第一次讀分不清很正常，記住 `type` = 粗分類、`typetag` = 細分類。Ch 4 讀 `luaH_get` 時看到的 `case LUA_VNUMINT`/`case LUA_VNUMFLT` 就是在 `ttypetag` 上 switch，因為它要區分整數浮點走不同查表路徑。

**Lua 沒用 NaN-boxing**。有些動態語言（早期 LuaJIT、SpiderMonkey）把值塞進 IEEE 754 double 的 NaN 位元裡省空間。Lua 官方 5.4 沒用，它老老實實用 union + 一個 byte 的 tag。好處是可讀、可攜、整數浮點分家乾淨；代價是每個值多一個 byte（對齊後其實多 8 bytes）。讀 Lua 值表示不會被 NaN-boxing 的位元魔術卡住，這也是它適合當教材的原因。

## GCObject：可回收物件的共同表頭

`Value` union 的 `gc` 指標指向的東西（字串、table、closure……），都以一個**共同表頭**開頭。`lobject.h`（v5.4.7）：

```c
#define CommonHeader	struct GCObject *next; lu_byte tt; lu_byte marked

typedef struct GCObject {
  CommonHeader;
} GCObject;
```

`CommonHeader` 三個欄位：

- `next`：串成一條鏈（GC 把所有物件掛在一條 `allgc` 鏈上，掃描時走這條鏈）。
- `tt`：這個物件的型別 tag（和 `TValue.tt_` 同一套編碼）。
- `marked`：GC 的顏色標記（白/灰/黑），Ch 6 的主角。

**關鍵設計**：每種可回收物件（`TString`、`Table`、`LClosure`……）的 struct 都以 `CommonHeader` 開頭。看 `Table`（`lobject.h`，v5.4.7）：

```c
typedef struct Table {
  CommonHeader;
  lu_byte flags;  /* 1<<p means tagmethod(p) is not present */
  lu_byte lsizenode;  /* log2 of size of 'node' array */
  unsigned int alimit;  /* "limit" of 'array' array */
  TValue *array;  /* array part */
  Node *node;
  Node *lastfree;  /* any free position is before this position */
  struct Table *metatable;
  GCObject *gclist;
} Table;
```

因為都以 `CommonHeader` 開頭，GC 拿到一個 `GCObject*` 不用知道它具體是什麼，就能讀 `tt`/`marked`/`next`。要當成具體型別用時，再靠 `tt` 判斷、用 `gco2t`（GCObject to Table）之類的巨集轉型。這是 C 裡「繼承」的慣用手法——**共同前綴 + 型別 tag 分派**，你在 Linux kernel（`struct list_head` 內嵌）、CPython（`PyObject` 頭）都會再遇到。第一次讀會困惑「`gco2t(o)` 怎麼就把 `GCObject*` 變成 `Table*` 了」——因為兩者共享起始佈局，C 標準允許這種轉換（`lstate.h` 的 `GCUnion` 註解引了 ISO C99 條文），這是 `reading_code` Ch 23「讀懂 indirection」的核心 idiom。

## data stack 佈局：`lua_State` / `CallInfo`

Ch 4 說 register 是「data stack 上的 slot」，現在看這條 stack 誰在管。`lua_State`（一份 Lua 執行緒/協程的狀態，`lstate.h`，v5.4.7）節錄關鍵欄位：

```c
struct lua_State {
  CommonHeader;
  lu_byte status;
  ...
  StkIdRel top;  /* first free slot in the stack */
  global_State *l_G;
  CallInfo *ci;  /* call info for current function */
  StkIdRel stack_last;  /* end of stack (last element + 1) */
  StkIdRel stack;  /* stack base */
  ...
  CallInfo base_ci;  /* CallInfo for first level (C calling Lua) */
  ...
};
```

- `stack`：這條 data stack 的起點（一個 `StackValue` 陣列）。
- `top`：目前用到哪（第一個空 slot）。
- `stack_last`：stack 的盡頭。
- `ci`：**當前正在執行的函式**的 CallInfo。
- `l_G`：指向 `global_State`（所有 thread 共享的全域狀態，GC 的資料都在那）。

一份 Lua 世界的記憶體長這樣：

```
  lua_State
   ├─ stack ────▶ ┌──────┬──────┬──────┬──────┬──────┬─── ...
   │              │ slot │ slot │ slot │ slot │ slot │
   │              └──────┴──────┴──────┴──────┴──────┴───
   │                 ▲                     ▲          ▲
   │               stack                 top      stack_last
   │                 │
   ├─ ci ──▶ CallInfo(當前函式) ─previous─▶ CallInfo(呼叫者) ─▶ ... ─▶ base_ci
   │            │ func  ──────────────────┐
   │            │ top                     │指向 stack 上這個函式的 frame
   │            └──────────────────────────┘
   └─ l_G ──▶ global_State（GC 狀態、字串駐留表、metatable...）
```

`CallInfo` 是一層函式呼叫的資訊（`lstate.h`，v5.4.7）節錄：

```c
struct CallInfo {
  StkIdRel func;  /* function index in the stack */
  StkIdRel	top;  /* top for this function */
  struct CallInfo *previous, *next;  /* dynamic call link */
  union {
    struct {  /* only for Lua functions */
      const Instruction *savedpc;
      volatile l_signalT trap;
      int nextraargs;
    } l;
    struct {  /* only for C functions */
      lua_KFunction k;
      ptrdiff_t old_errfunc;
      lua_KContext ctx;
    } c;
  } u;
  ...
  unsigned short callstatus;
};
```

看懂幾個關鍵點：

- `func`：這層函式在 stack 上的位置。Ch 4 的 `base = ci->func.p + 1` 就是從這裡算 register 0。
- `previous`/`next`：CallInfo 串成雙向鏈。**這條鏈就是 Lua 的呼叫堆疊**（Ch 4 說過，Lua 呼叫 Lua 不吃 C stack，深度記在這條鏈上）。
- `u` 是個 union：Lua 函式用 `u.l`（存 `savedpc`——這層函式被中斷時 pc 停在哪，Ch 4 的 `pc = ci->u.l.savedpc` 從這裡取），C 函式用 `u.c`（存 continuation，給 coroutine yield 用）。一個 CallInfo 要嘛管 Lua 函式、要嘛管 C 函式，兩組資料互斥，用 union 省空間——又一個 tagged union 的縮影（`callstatus` 的 `CIST_C` bit 當那個 tag）。

**這一整套佈局就是 Ch 4 那些 `base`/`pc`/`ci` 的來源**。讀完這節，回頭看 `luaV_execute` 開頭的 `cl = ci_func(ci); k = cl->p->k; pc = ci->u.l.savedpc; base = ci->func.p + 1;`，每個賦值你現在都知道從哪個欄位取、為什麼。

## 底層機制：table 的 array + hash 混合設計

Lua table 是全 runtime 最精巧的結構。回看 `Table` struct 的關鍵欄位：

```c
  unsigned int alimit;  /* "limit" of 'array' array */
  TValue *array;  /* array part */
  Node *node;      /* hash part */
  Node *lastfree;
```

一個 table **同時**有兩塊儲存：

- `array`：一段連續的 `TValue` 陣列，存**整數 key**（`t[1]`、`t[2]`……連續的正整數）。
- `node`：一張雜湊表，存**其他所有 key**（字串 key、非連續整數、浮點 key……）。

為什麼要混合？因為 Lua 的 table 既當陣列又當字典。`{10, 20, 30}` 這種順序陣列如果全走雜湊，每次存取都要算 hash、可能碰撞，慢又浪費空間。純陣列部分直接用整數當索引 `array[key-1]`，O(1) 無 hash。而 `{name="Lua", year=1993}` 這種字典就走 hash。一個結構,兩種儲存策略,根據 key 自動選路。

查一個整數 key 走哪條路？看 `luaH_getint`（`ltable.c`，v5.4.7）：

```c
const TValue *luaH_getint (Table *t, lua_Integer key) {
  lua_Unsigned alimit = t->alimit;
  if (l_castS2U(key) - 1u < alimit)  /* 'key' in [1, t->alimit]? */
    return &t->array[key - 1];
  ...
  else {  /* key is not in the array part; check the hash */
    Node *n = hashint(t, key);
    for (;;) {  /* check whether 'key' is somewhere in the chain */
      if (keyisinteger(n) && keyival(n) == key)
        return gval(n);  /* that's it */
      ...
    }
    return &absentkey;
  }
}
```

邏輯清楚：key 落在 `[1, alimit]` 就是 `array[key-1]`（一次陣列索引，沒有 hash）；否則進 hash 部分,`hashint` 算出起始 Node,沿著碰撞鏈（`gnext`）走找。找不到回傳 `&absentkey`（一個哨兵值,代表「這個 key 不存在」——注意不是 NULL,是特殊的 nil）。

一般的 `luaH_get`（任意 key）先按 key 型別分流（`ltable.c`，v5.4.7）：

```c
const TValue *luaH_get (Table *t, const TValue *key) {
  switch (ttypetag(key)) {
    case LUA_VSHRSTR: return luaH_getshortstr(t, tsvalue(key));
    case LUA_VNUMINT: return luaH_getint(t, ivalue(key));
    case LUA_VNIL: return &absentkey;
    case LUA_VNUMFLT: { ... }  /* 整數值的浮點 key 轉整數走 getint */
    default:
      return getgeneric(t, key, 0);
  }
}
```

**這就是 Ch 4 `OP_GETTABLE` 的下游**。Ch 4 看到 `OP_GETTABLE` 的 fast path `luaV_fastget(..., luaH_get)`,那個 `luaH_get` 就是這裡。整條 `t[k]` 的鏈：`OP_GETTABLE`（VM）→ `luaH_get`（按 key 型別分流）→ `luaH_getint`/`luaH_getshortstr`（走 array 或 hash）。你 Ch 4 讀的 opcode 和 Ch 5 讀的 table,在這裡接上了。

## rehash：兩塊儲存的大小怎麼調

array/hash 各多大不是固定的,table 成長時會重算。當 hash 部分滿了要插新 key,Lua 呼叫 `rehash`（`ltable.c`，v5.4.7）：

```c
static void rehash (lua_State *L, Table *t, const TValue *ek) {
  unsigned int asize;  /* optimal size for array part */
  unsigned int na;  /* number of keys in the array part */
  unsigned int nums[MAXABITS + 1];
  ...
  na = numusearray(t, nums);  /* count keys in array part */
  totaluse = na;
  totaluse += numusehash(t, nums, &na);  /* count keys in hash part */
  ...
  asize = computesizes(nums, &na);  /* compute new size for array part */
  luaH_resize(L, t, asize, totaluse - na);  /* resize to new sizes */
}
```

`rehash` 的精髓在 `nums[]` 這個直方圖:它按「2 的次方區間」統計現有整數 key 的分布（`nums[i]` = key 落在 `(2^(i-1), 2^i]` 的數量）。`computesizes` 據此算出「array 部分開多大最省」——原則是**讓 array 部分至少半滿**。如果一堆 key 是連續小整數,就把 array 開大、把它們從 hash 挪進 array;如果 key 稀疏,就縮小 array、多用 hash。

這解釋了一個 Lua 效能現象:`t = {}; for i=1,1000 do t[i]=i end` 過程中 table 會 rehash 幾次,每次 array 部分翻倍(類似 `std::vector` 的攤還成長)。而 `t[1000000]=1; t[1]=1` 這種稀疏 key 不會把 array 撐到一百萬,會留在 hash——Lua 自己判斷「這不是密集陣列,別浪費」。

## 對比與取捨

| 值表示策略 | 代表 | 優點 | 缺點 |
|---|---|---|---|
| **tagged union**（Lua） | Lua、CPython(PyObject) | 可讀、可攜、整數浮點分家乾淨 | 每值多一個 tag（對齊後多 8B） |
| NaN-boxing | LuaJIT、SpiderMonkey | 值只佔 8B,快取友善 | 位元魔術,難讀難移植 |
| 全部裝箱(everything on heap) | 早期 Ruby | 一致、簡單 | 整數也進堆,GC 壓力大 |

| table 設計 | 代表 | 特性 |
|---|---|---|
| **array+hash 混合**（Lua） | Lua | 一個結構兼顧陣列/字典,自動選路 |
| 純 hash | 多數語言的 dict | 陣列存取也走 hash,順序陣列較慢 |
| 陣列與字典分開型別 | Python(list/dict) | 各自最佳化,但使用者要選對型別 |

## 踩雷集錦

1. **以為 `tt_` 就是基本型別**。`tt_` 是一個 byte 塞了三層:基本型別(bits 0-3)、變體(bits 4-5)、可回收 bit(6)。用 `ttype(o)` 拿基本型別、`ttypetag(o)` 拿含變體的精確型別。看到 code 在 `LUA_VNUMINT` vs `LUA_VNUMFLT` 上分流,那是在 `ttypetag` 層,不是 `ttype`。
2. **以為 Lua 用 NaN-boxing**。官方 5.4 沒有。它用老實的 union + 一個 tag byte。你若在別的資料看到「Lua 值塞在 NaN 裡」,那是 LuaJIT(另一個實作),不是本課讀的官方 Lua。
3. **以為 `gco2t(o)` 是某種 cast 函式做了轉換工作**。它只是把 `GCObject*` reinterpret 成 `Table*`,因為兩者共享 `CommonHeader` 起始佈局。沒有搬資料、沒有配置,純指標型別轉換。困惑時去看 `lstate.h` 的 `GCUnion` 註解,它引了 C99 標準說明為什麼合法。
4. **以為 table 一定用 hash**。連續正整數 key(`t[1]`..`t[n]`)走 array 部分,直接索引無 hash。這是 `#t`(取長度)和 `ipairs` 快的原因。字串 key、稀疏整數才走 hash。一個 table 兩塊儲存,別假設它是純字典。
5. **以為 `t[1000000]=x` 會配一百萬格陣列**。不會。`rehash` 的 `computesizes` 要求 array 部分至少半滿,稀疏 key 會留在 hash。想觸發 array 成長,得放密集的連續整數 key。誤解這點會寫出「以為在填陣列、其實全進 hash」的慢 code。

## 進階：再往深一層

- **字串駐留(interning)**:短字串在 Lua 是**駐留**的——內容相同的短字串全世界只存一份,比較字串相等只要比指標。看 `lstring.c` 的 `luaS_new`/`luaS_newlstr` 和 `global_State.strt`(string table)。這是 table 用字串 key 快的另一半原因(key 比較是指標比較)。本課不精讀,但值得掃一眼知道它存在。
- **`gdb` 看真 TValue**:`break luaV_execute`,在迴圈裡 `print *(TValue*)(base+0)`,看 `tt_` 和 `value_.i`/`value_.gc`,親眼確認一個 register 裝的 tagged union。配合 Ch 4 的 opcode 追蹤,值和指令一起看。
- **對照 CPython 的 PyObject**:CPython 每個值是 heap 上的 `PyObject`,頭部有 refcount + 型別指標(`ob_type`)。跟 Lua 的差異:CPython **整數也裝箱**(小整數有快取),Lua 整數直接存在 union 不進堆。讀完這章去 Ch 23 看 CPython object model,這個對比會很鮮明——同樣是動態型別,兩種截然不同的值表示。
- **`Node` 的碰撞鏈設計**:Lua hash 部分用「Brent's variation」的鏈式碰撞,`Node` 裡的 `next` 是**相對偏移**(`n += nx`)而非指標,讓 Node 陣列可整塊搬動。細看 `ltable.c` 的 `luaH_newkey`,是雜湊表實作的漂亮範本。

## 本章重點整理

- Lua 的值是 **tagged union** `TValue` = `Value`(union,能裝指標/整數/浮點)+ `tt_`(1 byte 標籤)。動態型別靠「每個值自帶型別標籤」實現。
- `tt_` 一個 byte 三層:bits 0-3 基本型別、bits 4-5 變體、bit 6 可回收。`ttype()` 取粗分類、`ttypetag()` 取細分類(整數 vs 浮點靠這層區分)。
- 整數浮點**直接存在 union**不進堆、不 GC;字串/table/function 是 heap 物件,`Value.gc` 指過去,都以 `CommonHeader`(next/tt/marked)開頭,靠共同前綴 + tag 分派。
- data stack:`lua_State` 有一條 `stack`,`CallInfo` 鏈當呼叫堆疊(`previous`/`next`),`ci->func` + 1 = register 0。Ch 4 的 `base`/`pc`/`ci` 全從這裡來。
- table 是 **array + hash 混合**:連續整數 key 走 `array`(直接索引無 hash),其餘走 `node`(雜湊)。`luaH_get` 按 key 型別分流,是 Ch 4 `OP_GETTABLE` 的下游。
- `rehash` 用 2 次方直方圖 `nums[]` + `computesizes` 決定 array/hash 各多大,原則是 array 部分至少半滿,稀疏 key 不撐爆陣列。

## 自我檢核

- [ ] 我能畫出 `TValue` 的結構,說出 `Value` union 五個成員各是什麼、哪些直接存值哪些是指標
- [ ] 我能解釋 `tt_` 一個 byte 怎麼塞下基本型別、變體、可回收三層,以及 `ttype` vs `ttypetag` 的差別
- [ ] 我知道為什麼整數不進堆而 table 進堆,以及 `CommonHeader` 讓 GC 能統一處理各種物件
- [ ] 我能對照 Ch 4 的 `base = ci->func.p + 1`,說出 data stack、`lua_State`、`CallInfo` 怎麼佈局
- [ ] 我能講清楚 table 的 array/hash 兩塊各存什麼 key,`luaH_getint` 怎麼決定走哪塊
- [ ] 我理解 `rehash` 為什麼稀疏整數 key 不會撐爆 array 部分

## 延伸閱讀

- **《The Implementation of Lua 5.0》— §2 (The Type System) 與 §4 (Tables)**（[lua.org/doc/jucs05.pdf](https://www.lua.org/doc/jucs05.pdf)）
  - **讀哪裡**:§2 講 tagged union 的設計取捨,§4 講 table 的 array+hash 混合與 rehash 演算法(直方圖那段)。作者一手解釋,和本章讀的 code 直接對得上(雖是 5.0,值表示與 table 核心思想沿用到 5.4)。
  - **前提**:讀過本章,知道 `TValue`/`Table` 長什麼樣。
- **[Lua 5.4 Reference Manual — §2.1 Values and Types](https://www.lua.org/manual/5.4/manual.html#2.1)**（官方）
  - **讀哪裡**:整節。從語言使用者視角看八種型別,再回頭對照 code 裡的 `LUA_T*` tag,語意與實作兩邊打通。
  - **前提**:無。
- **`reading_code` Ch 23「讀懂 indirection」**（本 repo）
  - **讀哪裡**:本章的 `CommonHeader` 共同前綴 + `gco2t` 型別轉換,以及 `s2v`/`StackValue` 那層,都是這章講的「C 用共享佈局做多型」的活例子。回頭對照。
  - **前提**:無。

值表示和 table 都讀懂了,但這些 heap 物件(字串、table、closure)什麼時候被回收?下一章是本課第一個難章:Lua 的增量三色標記-清除 GC。慢慢來。

→ [Ch 6 Lua 的增量 GC](./06-lua-incremental-gc.md)
