# Ch 12 — 動態記憶體與所有權慣例：誰配置、誰釋放

> **目標**：從函式命名與 API 形狀讀出「這塊記憶體歸誰」，看懂 Redis `zmalloc`/`sds` 與 Lua `lua_Alloc` 這兩套真實配置器的設計，並學會在陌生程式碼裡追蹤一塊記憶體從出生到死亡的路徑。
> **鴻溝**：本章主要練**慣用法鴻溝**（`_new/_free`、`get/put`、header-before-pointer 這些慣例），以及**脈絡鴻溝**（所有權規則常常只寫在註解或作者腦中）。

> **環境**：WSL Ubuntu 22.04、x86_64、gcc 11.4（含 AddressSanitizer）；Redis 7.2.17 以預設設定 build（Linux 上預設 jemalloc 5.3.0），Lua 5.4.9 以 `-g` build。

## 為什麼需要這個？

C 沒有垃圾回收，也沒有 Rust 那種寫在型別裡的所有權。一個函式回傳 `char *`，C 語言**不會告訴你**：這塊記憶體是新配置的、你要負責 free？還是指向某個內部緩衝區、你絕對不能 free？還是指向一個靜態字串、下一次呼叫就被覆蓋？

所有權資訊在 C 裡只能存在於三個地方：**命名慣例、API 形狀、註解**。寫 C 的人彼此之間靠這三樣東西溝通，讀碼的人必須學會解讀它們。Spinellis 在原書第 3.4 節討論動態配置時，重點放在「看出程式碼在建立什麼樣的資料結構」；這一章往前多走一步：**把每一個 `malloc` 和它對應的 `free` 配起來**，這是讀懂任何 C 專案資源管理的關鍵，也是找 memory leak、use-after-free、double free 這類 bug 的起點。

歷史上，C 函式庫自己就給了反面教材：`strtok`、`asctime`、`gethostbyname` 回傳指向內部靜態緩衝區的指標，下一次呼叫就覆蓋，執行緒不安全。後來 POSIX 補上 `strtok_r`、`asctime_r`、`getaddrinfo`/`freeaddrinfo`——把「緩衝區由呼叫者提供」或「配對的釋放函式」變成 API 的一部分。這個演進本身就是所有權慣例的教科書。

## 先建立直覺

把一塊動態記憶體想成**圖書館的一本書**。

```
                ┌─────────── 誰借出？（配置點）
                │
   sdsnew() ────┼──► [ 書 ] ──► 借書證上寫誰的名字？（擁有者）
                │       │
                │       ├──► 影印本可以給別人看（借用 borrowed pointer）
                │       │       但原書還了之後影印本上的頁碼就沒意義了
                │       │
                │       └──► 轉借：把借書證一起交出去（ownership transfer）
                │
   sdsfree() ◄──┴─────────── 誰還書？（釋放點）——而且要還到同一間圖書館
```

讀碼時對每個指標問：**它是借書證（owning pointer）還是影印本（borrowed pointer）？** 借書證只能有一張；影印本可以很多張，但原書一還，影印本全部作廢。最後一句「還到同一間圖書館」也很重要：`sdsnew` 借的書不能拿去 `free` 還，Lua 配置的記憶體不能用 `free` 還——我們會讓這兩件事都真的出錯給你看。

## 從命名讀出所有權

大型 C 專案幾乎都有一套成對的命名慣例。認得它，看到函式名就知道該往哪裡找配對。

| 命名模式 | 語意 | Redis 7.2.17 | Lua 5.4.9 | Linux v6.18 |
|---|---|---|---|---|
| `X_new` / `X_free` | 配置並初始化 / 釋放 | `sdsnew`/`sdsfree`、`quicklistNew`/`quicklistRelease` | `luaM_new`/`luaM_free` | — |
| `X_create` / `X_destroy`（或 `Release`） | 建立一個較重的物件（內含其他配置） | `dictCreate`/`dictRelease`、`listCreate`/`listRelease` | `lua_newstate`/`lua_close` | `kmem_cache_create`/`kmem_cache_destroy` |
| `alloc_X` / `free_X` | 同上，kernel 偏好動詞在前 | — | — | `alloc_pipe_info`/`free_pipe_info`（`fs/pipe.c:792`、`:841`） |
| `get` / `put`、`incr` / `decr` | 參考計數：多一個使用者 / 少一個使用者 | `incrRefCount`/`decrRefCount` | — | `fget`/`fput`（`fs/file.c:1082`、`fs/file_table.c:542`） |
| `X_dup` | 深拷貝，回傳新的、你擁有的 | `sdsdup`、`setTypeDup` | — | `kstrdup` |
| 呼叫者提供緩衝區 | 函式不配置，寫進你給的空間 | `ll2string(char *dst, size_t dstlen, long long)`（`util.c:345`） | — | `strscpy` |

在 Redis 的標頭檔掃一遍成對命名，可以看出這個專案的慣例有多一致：

```
$ rg -o "\b[a-zA-Z]+(Create|Release|New|Free|Dup)\b\(" -g '*.h' . | sed 's/.*://' | sort | uniq -c | sort -rn | head
      2 redisOpArrayFree(
      1 zslFree(
      1 zslCreate(
      1 zsetDup(
      1 zipmapNew(
      1 ziplistNew(
      1 streamNew(
      1 streamDup(
      1 setTypeDup(
      1 setTypeCreate(
      ...
```

`zslCreate`/`zslFree`、`streamNew`/…——看到其中一半，就去找另一半。**找不到配對的那一個，就是你要特別注意的地方**：可能是所有權被轉移到別處，也可能是 bug。

### 參考計數：`get/put` 家族

當一個物件同時被多處使用、沒有單一擁有者時，C 程式用參考計數（reference counting）。Redis 的 `robj`：

```c
/* redis-7.2.17/src/object.c:43 */
robj *createObject(int type, void *ptr) {
    robj *o = zmalloc(sizeof(*o));
    o->type = type;
    o->encoding = OBJ_ENCODING_RAW;
    o->ptr = ptr;
    o->refcount = 1;
    o->lru = 0;
    return o;
}

/* redis-7.2.17/src/object.c:392 */
void decrRefCount(robj *o) {
    if (o->refcount == 1) {
        switch(o->type) {
        case OBJ_STRING: freeStringObject(o); break;
        case OBJ_LIST: freeListObject(o); break;
        ...
        default: serverPanic("Unknown object type"); break;
        }
        zfree(o);
    } else {
        if (o->refcount <= 0) serverPanic("decrRefCount against refcount <= 0");
        if (o->refcount != OBJ_SHARED_REFCOUNT) o->refcount--;
    }
}
```

讀法：`create*` 回傳時 refcount 是 1，**呼叫者擁有那一份參考**。之後每多一個地方保存這個指標就 `incrRefCount`，每放棄一個就 `decrRefCount`，降到零時真的釋放。注意 `OBJ_SHARED_REFCOUNT`：Redis 預先建立了一批共享物件（例如小整數 0–9999，數量由 `server.h:121` 的 `OBJ_SHARED_INTEGERS 10000` 決定），它們的 refcount 是一個特殊值，永遠不會被釋放——讀到 `incrRefCount` 什麼都沒做的分支，原因就在這。

所有權規則常常只寫在註解裡。`db.c` 的 `dbAdd`：

```c
/* redis-7.2.17/src/db.c:188 */
/* Add the key to the DB. It's up to the caller to increment the reference
 * counter of the value if needed.
```

緊接著的 `dbAddRDBLoad`（`db.c:213` 起）則說 key「is retained by the function (and not freed by the caller)」——同一個檔案、相鄰的兩個函式，所有權規則相反。**這就是為什麼讀所有權一定要讀註解**，而且要讀到每個函式。kernel 的 `kref`、`refcount_t` 與 get/put 配對會在 [Ch 20](./20-refcount-and-lifetime.md) 深入。

## API 形狀的五種所有權語意

命名之外，函式簽名本身也透露所有權。

| 形狀 | 例子 | 所有權 |
|---|---|---|
| `T *make(...)` 回傳新指標 | `sds sdsnew(const char *init)` | 呼叫者擁有，要用配對函式釋放 |
| `const T *peek(...)` 回傳 const 指標 | `const char *lua_tostring(L, idx)` | 借用；有效期限由被呼叫者定義 |
| `int fill(T *buf, size_t len, ...)` | `ll2string(dst, dstlen, v)` | 呼叫者配置、呼叫者釋放，函式只寫入 |
| `void take(T *p)` 吞掉參數 | `sdsfree(s)`、`dictAdd` 在某些 `dictType` 下 | 所有權轉移給被呼叫者 |
| `T *update(T *old, ...)` 回傳可能不同的指標 | `sds sdscat(sds s, const char *t)` | 舊指標**可能失效**，一定要用回傳值 |

最後一種最容易讀錯。`sdscat` 可能 `realloc`，所以 Redis 程式碼裡永遠寫成 `s = sdscat(s, "...")`。如果有第二個指標也指向舊的 `s`，它就成了懸空指標。下面「追蹤生命週期」一節會用 AddressSanitizer 抓給你看。

第二種「借用」的有效期限，必須讀文件才知道。Lua 5.4 手冊（`manual/manual.of` 第 2572 行起）說：

> the API guarantees that any pointer to a string in a stack index is valid while the string value at that index is not removed from the stack.

也就是說，`lua_tostring` 回傳的指標，在那個值還留在 stack 上時有效；`lua_pop` 之後就可能被 GC 回收。這種「生命週期綁在另一個東西上」的借用，C 型別系統完全表達不出來。

## Redis 的 `zmalloc`：包裝 malloc 為了什麼

Redis 的所有配置都經過 `zmalloc`/`zfree`。包一層的目的有三個：**記帳**（`INFO memory` 的 `used_memory` 從這裡來）、**OOM 處理**（配置失敗直接 abort，呼叫端不用檢查 `NULL`）、**換配置器**（jemalloc、tcmalloc、libc）。

```c
/* redis-7.2.17/src/zmalloc.c:60 */
#ifdef HAVE_MALLOC_SIZE
#define PREFIX_SIZE (0)
#else
/* Use at least 8 bits alignment on all systems. */
#if SIZE_MAX < 0xffffffffffffffffull
#define PREFIX_SIZE 8
#else
#define PREFIX_SIZE (sizeof(size_t))
#endif
#endif
```

```c
/* redis-7.2.17/src/zmalloc.c:114 */
static inline void *ztrymalloc_usable_internal(size_t size, size_t *usable) {
    /* Possible overflow, return NULL, so that the caller can panic or handle a failed allocation. */
    if (size >= SIZE_MAX/2) return NULL;
    void *ptr = malloc(MALLOC_MIN_SIZE(size)+PREFIX_SIZE);

    if (!ptr) return NULL;
#ifdef HAVE_MALLOC_SIZE
    size = zmalloc_size(ptr);
    update_zmalloc_stat_alloc(size);
    if (usable) *usable = size;
    return ptr;
#else
    *((size_t*)ptr) = size;
    update_zmalloc_stat_alloc(size+PREFIX_SIZE);
    if (usable) *usable = size;
    return (char*)ptr+PREFIX_SIZE;
#endif
}
```

兩條路徑：

```
有 HAVE_MALLOC_SIZE（jemalloc / glibc / macOS）    沒有（其他平台）
┌──────────────────────┐                          ┌────────┬──────────────────┐
│     user data        │                          │ size_t │    user data     │
└──────────────────────┘                          └────────┴──────────────────┘
▲ malloc 回傳 = zmalloc 回傳                       ▲ malloc  ▲ zmalloc 回傳
大小問配置器：zmalloc_size(p)                       大小自己記在前綴裡
```

配置器能回答「這塊多大」的時候（jemalloc 的 `je_malloc_usable_size`、glibc 的 `malloc_usable_size`），Redis 就不浪費前綴；不能的時候，在使用者資料前面藏一個 `size_t` 記大小，回傳往後挪 8 bytes 的指標。`zfree` 對應地往回挪：

```c
/* redis-7.2.17/src/zmalloc.c:365 */
void zfree(void *ptr) {
#ifndef HAVE_MALLOC_SIZE
    void *realptr;
    size_t oldsize;
#endif

    if (ptr == NULL) return;
#ifdef HAVE_MALLOC_SIZE
    update_zmalloc_stat_free(zmalloc_size(ptr));
    free(ptr);
#else
    realptr = (char*)ptr-PREFIX_SIZE;
    oldsize = *((size_t*)realptr);
    update_zmalloc_stat_free(oldsize+PREFIX_SIZE);
    free(realptr);
#endif
}
```

這裡的 `free` 不一定是 libc 的 `free`：`zmalloc.c:82–89` 在 `USE_JEMALLOC` 時把 `malloc`/`free` 等用巨集重新定義成 `je_malloc`/`je_free`。讀到這裡如果你用 IDE 的「跳到定義」，它很可能跳到 glibc 的 `free`，這就是前置處理器造成的**間接鴻溝**——[Ch 13](./13-preprocessor-basics.md) 會教你怎麼把這種 `#ifdef` 叢林「投影」成實際被編譯的那一條路徑。

Redis 在 Linux 上預設用哪一個？看 `src/Makefile:67`：

```make
# Default allocator defaults to Jemalloc on Linux and libc otherwise
MALLOC=libc
ifeq ($(uname_S),Linux)
	MALLOC=jemalloc
endif
```

build 出來的 binary 會自己報告：

```
$ ./redis-server --version
Redis server v=7.2.17 sha=83937c51:0 malloc=jemalloc-5.3.0 bits=64 build=f094c5b50beb74cf
```

## 底層機制：`sds` 的 header-before-pointer 技巧

`sds`（Simple Dynamic String）是 Redis 的字串型別，`sds.h:43` 一行定義：

```c
typedef char *sds;
```

它**就是** `char *`，可以直接丟給 `printf("%s")`、`strcmp`。但字串前面藏了一個 header：

```c
/* redis-7.2.17/src/sds.h:51 */
struct __attribute__ ((__packed__)) sdshdr8 {
    uint8_t len; /* used */
    uint8_t alloc; /* excluding the header and null terminator */
    unsigned char flags; /* 3 lsb of type, 5 unused bits */
    char buf[];
};
```

另外還有 `sdshdr16`、`sdshdr32`、`sdshdr64`，`len`/`alloc` 的寬度不同；`sdshdr5` 則把長度直接塞進 flags 的高 5 bit。所有 header 的**最後一個 byte 都是 flags**，而 `sds` 指標指向 `buf`——所以 `s[-1]` 永遠是 flags，從 flags 的低 3 bit 知道 header 類型，再往前退對應的 bytes 找到 header 開頭：

```
           sdshdr8（packed，共 3 bytes）
          ┌───────┬───────┬───────┬───┬───┬───┬───┬───┬────┐
 記憶體：  │  len  │ alloc │ flags │ h │ e │ l │ l │ o │ \0 │ ...
          └───────┴───────┴───────┴───┴───┴───┴───┴───┴────┘
          ▲                       ▲
          zmalloc 回傳的區塊開頭    sds s 指在這裡
          = s - sizeof(sdshdr8)    s[-1] = flags
```

```c
/* redis-7.2.17/src/sds.h:83 */
#define SDS_HDR_VAR(T,s) struct sdshdr##T *sh = (void*)((s)-(sizeof(struct sdshdr##T)));
#define SDS_HDR(T,s) ((struct sdshdr##T *)((s)-(sizeof(struct sdshdr##T))))

/* redis-7.2.17/src/sds.h:87 */
static inline size_t sdslen(const sds s) {
    unsigned char flags = s[-1];
    switch(flags&SDS_TYPE_MASK) {
        case SDS_TYPE_5:
            return SDS_TYPE_5_LEN(flags);
        case SDS_TYPE_8:
            return SDS_HDR(8,s)->len;
        ...
```

`__packed__` 的理由在這裡變得清楚：如果編譯器在 `flags` 和 `buf` 之間塞 padding，`s[-1]` 就不再是 flags 了。`pahole -C sdshdr8 redis-server` 顯示 `size: 3`、`buf` 在 offset 3，沒有任何洞。`##` 拼接型別名的寫法是 [Ch 14](./14-macro-metaprogramming.md) 的主題。

### 用真的 sds 驗證

把 Redis build 出來的 `sds.o`、`zmalloc.o` 和 jemalloc 連進一支小程式，印出 `s` 前面的 bytes。`sds.o` 依賴 `util.c` 的 `ll2string`/`ull2string`，我們用 `snprintf` 寫兩個替身（`stubs.c`）避免把整個 Redis 拉進來：

```c
/* scratch/12/realsds.c */
#include <stdio.h>
#include "sds.h"
#include "zmalloc.h"

static void dump(const char *tag, sds s) {
    unsigned char flags = s[-1];
    printf("%-8s len=%-5zu avail=%-5zu type=%u  hdr bytes before s:", tag,
           sdslen(s), sdsavail(s), flags & SDS_TYPE_MASK);
    int hdr = (flags & SDS_TYPE_MASK) == SDS_TYPE_8 ? 3 : (flags & SDS_TYPE_MASK) == SDS_TYPE_16 ? 5 : 1;
    for (int k = hdr; k >= 1; k--) printf(" %02x", (unsigned char)s[-k]);
    printf("   used_memory=%zu\n", zmalloc_used_memory());
}

int main(void) {
    printf("allocator: %s\n", ZMALLOC_LIB);
    sds a = sdsnew("hello");            dump("new", a);
    a = sdscat(a, " world");            dump("cat", a);
    sds big = sdsnewlen(NULL, 300);     dump("300B", big);
    sds e = sdsempty();                 dump("empty", e);
    printf("printf 直接用: %s\n", a);
    sdsfree(a); sdsfree(big); sdsfree(e);
    printf("after free used_memory=%zu\n", zmalloc_used_memory());
    return 0;
}
```

```
$ R=../pb/redis/src
$ gcc -I$R -I../pb/redis/deps/jemalloc/include -DUSE_JEMALLOC -o realsds realsds.c stubs.c \
      $R/sds.o $R/zmalloc.o ../pb/redis/deps/jemalloc/lib/libjemalloc.a -lpthread -lm -ldl
$ ./realsds
allocator: jemalloc-5.3.0
new      len=5     avail=0     type=0  hdr bytes before s: 28   used_memory=8
cat      len=11    avail=17    type=1  hdr bytes before s: 0b 1c 01   used_memory=32
300B     len=300   avail=14    type=2  hdr bytes before s: 2c 01 3a 01 02   used_memory=352
empty    len=0     avail=4     type=1  hdr bytes before s: 00 04 01   used_memory=360
printf 直接用: hello world
after free used_memory=0
```

逐行解碼：

- **`new`**：`"hello"` 5 bytes，`sdsReqType` 判定小於 32（`1<<5`）用 type 5。flags `0x28` = `0b00101000`：低 3 bit `000` 是 type 5，高 5 bit `00101` 是長度 5。整個區塊 1（header）+ 5 + 1（`\0`）= 7 bytes，jemalloc 最小的 size class 是 8，所以 `used_memory=8`。
- **`cat`**：append 之後 `sdsMakeRoomFor` 換成 type 8（type 5 沒有 `alloc` 欄位，不能預留空間）。header 三個 byte `0b 1c 01`：`len=11`、`alloc=28`、flags=1。預留空間是 Redis 的貪婪擴張策略（`sds.c:247` 的 `_sdsMakeRoomFor`），讓連續 append 不必每次 realloc。
- **`300B`**：300 ≥ 256，用 type 16。`2c 01` 是 little-endian 的 300，`3a 01` 是 314。
- **`empty`**：`sdsempty()` 長度 0，但 `_sdsnewlen`（`sds.c:110`）特別規定空字串用 type 8 而不是 type 5，原始碼註解說「Empty strings are usually created in order to append」。
- 全部 free 之後 `used_memory=0`：記帳是平的，這也是一個驗證「沒有漏掉 free」的手段。

### 失敗示範：還錯圖書館

`sds` 是 `char *`，編譯器不會阻止你對它呼叫 `free`：

```c
/* scratch/12/badfree.c */
sds s = sdsnew("hello world, a longer string");
printf("s=%p  real block=%p\n", (void*)s, (void*)(s - 3));
fflush(stdout);
free(s);            /* 錯：s 不是 malloc 回傳的指標 */
```

這次用 libc 配置器編（直接編 `sds.c`/`zmalloc.c`，不加 `-DUSE_JEMALLOC`）：

```
$ ./badfree; echo "exit=$?"
s=0x5ebeca0d82a1  real block=0x5ebeca0d829e
free(): invalid pointer
bash: line 5: 326002 Aborted                 (core dumped) ./badfree
exit=134
```

`s` 比真正的區塊開頭多 3 bytes（28 字元的字串用 type 8，header 3 bytes）。glibc 的 `free` 檢查發現這個位址不是它配出去的區塊，直接 abort。第一次寫這個實驗時沒有 `fflush`，`printf` 的內容還在 stdout 緩衝區裡就被 abort 掉了，螢幕上只剩 `free(): invalid pointer`——這也是除錯時常見的誤導：**程式 crash 前的最後幾行輸出可能根本沒印出來**。

## Lua 的 `lua_Alloc`：一個函式包辦配置、擴大、縮小、釋放

Lua 不直接呼叫 `malloc`。整個直譯器的記憶體都經過一個由使用者提供的函式：

```c
/* lua-5.4.9/lua.h:125 */
typedef void * (*lua_Alloc) (void *ud, void *ptr, size_t osize, size_t nsize);
```

語意（手冊 `manual.of` 第 2912 行起）：`nsize == 0` 是釋放；`ptr == NULL` 是新配置，**此時 `osize` 不是大小，而是 Lua 正在建立的物件型別**（`LUA_TSTRING`、`LUA_TTABLE`……）；其他情況是 realloc。標準函式庫 `lauxlib.c:1039` 的預設實作：

```c
static void *l_alloc (void *ud, void *ptr, size_t osize, size_t nsize) {
  (void)ud; (void)osize;  /* not used */
  if (nsize == 0) {
    free(ptr);
    return NULL;
  }
  else
    return realloc(ptr, nsize);
}
```

為什麼要讓使用者換配置器？嵌入式場景要限制記憶體上限、遊戲引擎要用自己的 pool、沙箱要記帳。而且 Lua 在呼叫配置器時**主動告訴你舊區塊多大**（`osize`），配置器不必像 `zmalloc` 那樣自己記前綴——這是 API 設計把所有權資訊往上推的好例子。

### 實驗：一個會記帳、會設上限的配置器

```c
/* scratch/12/luaalloc.c（節錄） */
struct stats { size_t cur, peak, nalloc, nfree, by_tag[9]; size_t limit; };

static void *counting_alloc(void *ud, void *ptr, size_t osize, size_t nsize) {
    struct stats *st = ud;
    if (nsize == 0) {                       /* 釋放 */
        if (ptr) { st->cur -= osize; st->nfree++; }
        free(ptr);
        return NULL;
    }
    size_t old = ptr ? osize : 0;           /* ptr==NULL 時 osize 是型別碼，不是大小 */
    if (st->limit && st->cur - old + nsize > st->limit)
        return NULL;                        /* 模擬記憶體不足 */
    void *np = realloc(ptr, nsize);
    if (!np) return NULL;
    if (!ptr) { st->nalloc++; if (osize < 9) st->by_tag[osize]++; }
    st->cur = st->cur - old + nsize;
    if (st->cur > st->peak) st->peak = st->cur;
    return np;
}
/* run() 用 lua_newstate(counting_alloc, &st) 建立 state，執行
   "local t = {} for i = 1, 10000 do t[i] = tostring(i) end" */
```

```
$ gcc -std=c99 -I../pb/lua -o luaalloc luaalloc.c ../pb/lua/liblua.a -lm -ldl && ./luaalloc
limit=0        rc=0
  cur=700117 peak=700117 nalloc=10298 nfree=28  new objects: string=10212 table=21 function=8
  after lua_close: cur=0
limit=200000   rc=1 not enough memory
  cur=199936 peak=200000 nalloc=3222 nfree=29  new objects: string=3136 table=21 function=8
  after lua_close: cur=0
```

讀這個輸出：

- 無上限時，配置了 10212 個字串物件（一萬個 `tostring(i)` 加上標準函式庫的名字），`lua_close` 之後 `cur=0`——Lua 把它配置的每一個 byte 都還回來了。
- 設上限 200000 bytes 時，配置器回傳 `NULL`，Lua 先嘗試緊急 GC（`lmem.c:162` 的 `tryagain`），還是不夠就拋出 `LUA_ERRMEM`，腳本以 `not enough memory` 失敗，**宿主程式沒有 crash**。對比 `zmalloc` 的「失敗就 abort」，這是兩種完全不同的 OOM 哲學：Redis 是伺服器，記憶體不足時繼續跑反而危險；Lua 是嵌入式語言，宿主必須能存活。
- 釋放時配置器用的 `osize` 是 Lua 告訴它的。如果 Lua 對某塊記憶體報錯大小，我們的 `cur` 最後不會歸零。歸零本身就是一個對 Lua 記帳正確性的小測試。

Lua 內部所有配置都走 `lmem.c` 的 `luaM_realloc_`，它在每次成功配置後更新 `g->GCdebt`（`lmem.c:187`）：配置越多，欠的「GC 債」越多，債到一定程度就觸發一步增量 GC。**配置器同時是 GC 的節拍器**，這是讀 `lgc.c` 之前要先知道的領域知識。

## arena 與 pool：生命週期綁在一起

有時候成千上萬個小物件有**相同的生命週期**——例如 parser 處理一個檔案時建的所有 token。一個一個 free 既慢又容易漏。arena（區域配置器）的做法是：從一大塊記憶體裡往前切，最後整塊一起丟。

```c
/* scratch/12/arena.c（節錄） */
struct arena { char *base; size_t used, cap; };

static void *arena_alloc(struct arena *a, size_t n) {
    size_t off = (a->used + 15) & ~(size_t)15;       /* 對齊到 16，同 malloc 在 x86-64 glibc 的保證 */
    if (off + n > a->cap) return NULL;
    a->used = off + n;
    return a->base + off;
}
```

```
$ gcc -Wall -o arena arena.c && ./arena
token local  @ offset 0
token x      @ offset 32
token =      @ offset 64
token 42     @ offset 96
arena used=115 bytes; 一次 free 全部
```

每個 token 結構 16 bytes 加上它的字串，對齊後各佔 32 bytes。程式結尾只有一個 `free(a.base)`。**讀 arena 程式碼時，你找不到對應每個物件的 free——這不是 leak，是設計。** 判斷方法：找到 arena 本身的建立與銷毀點，所有從它切出來的物件，生命週期都等於 arena 的生命週期。

kernel 有幾個同家族的機制：`mempool_t`（預留物件保證不失敗）、slab cache（同大小物件的 pool），以及 `devm_*`——資源的生命週期綁在 device 上，driver 卸載時自動釋放，所以 probe 函式裡看到 `devm_kzalloc` 卻找不到 `kfree` 是正常的。這些在 [Ch 19](./19-kernel-memory-allocation.md) 展開。

## 讀碼方法：追蹤一塊記憶體的生命週期

拿到一個陌生的指標，用這四步追：

```
1. 出生    找配置點：grep 回傳這個指標的函式，確認它是 new / dup / 借用
           ↓
2. 擁有者  它存進了哪裡？區域變數？struct 欄位？全域表？
           存進長壽命的容器 = 所有權可能轉移給容器
           ↓
3. 分身    還有誰拿到同一個位址？（alias）這些分身是借用還是額外參考（refcount++）？
           ↓
4. 死亡    找釋放點：配對函式、refcount 歸零、arena 銷毀、容器的 destructor
           檢查：所有分身在死亡之後都不再被使用了嗎？
```

第 2 步常常要看容器的設定。Redis 的 `dict` 不知道 key 是什麼型別，key 要不要複製、刪除時要不要釋放，由 `dictType` 的 `keyDup` 和 `keyDestructor` 決定（`dict.h:51`）。同一個 `dictAdd(d, key, val)`，在 `keyDestructor = sdsfree` 的 dict 裡是「所有權轉移給 dict」，在 `keyDestructor = NULL` 的 dict 裡是「dict 只借用」。這是所有權資訊藏在**間接層**裡的例子。

第 3、4 步是 use-after-free 的溫床。用 AddressSanitizer 實際看一次：

```c
/* scratch/12/uaf.c */
#include <stdio.h>
#include "sds.h"
int main(void) {
    sds name = sdsnew("redis");
    sds alias = name;          /* 第二個指標，沒有轉移所有權 */
    name = sdscat(name, "-server-with-a-long-suffix");  /* 可能 realloc，舊位址失效 */
    printf("name=%s\n", name);
    printf("alias=%s\n", alias); /* use-after-free */
    sdsfree(name);
    return 0;
}
```

```
$ gcc -g -fsanitize=address -fno-omit-frame-pointer -I$R -o uaf uaf.c stubs.c $R/sds.c $R/zmalloc.c -lpthread
$ ./uaf
=================================================================
==326015==ERROR: AddressSanitizer: heap-use-after-free on address 0x502000000011 at pc 0x7603c665df89 bp 0x7ffc9b1d07e0 sp 0x7ffc9b1cff58
READ of size 1 at 0x502000000011 thread T0
    ...
    #3 0x580e8613a85f in main /home/ypp/code_reading/scratch/12/uaf.c:8
    ...
0x502000000011 is located 1 bytes inside of 7-byte region [0x502000000010,0x502000000017)
freed by thread T0 here:
    #0 0x7603c66b4537 in __interceptor_free ../../../../src/libsanitizer/asan/asan_malloc_linux.cpp:127
    #1 0x580e86142aff in zfree ../pb/redis/src/zmalloc.c:374
    #2 0x580e8613cc47 in _sdsMakeRoomFor ../pb/redis/src/sds.c:288
    #3 0x580e8613ce37 in sdsMakeRoomFor ../pb/redis/src/sds.c:303
    #4 0x580e8613dd1b in sdscatlen ../pb/redis/src/sds.c:496
    #5 0x580e8613ddef in sdscat ../pb/redis/src/sds.c:509
    #6 0x580e8613a825 in main /home/ypp/code_reading/scratch/12/uaf.c:6
    ...
previously allocated by thread T0 here:
    #0 0x7603c66b4887 in __interceptor_malloc ../../../../src/libsanitizer/asan/asan_malloc_linux.cpp:145
    #1 0x580e86141a7e in ztrymalloc_usable_internal ../pb/redis/src/zmalloc.c:117
    #2 0x580e86141e01 in zmalloc_usable ../pb/redis/src/zmalloc.c:160
    #3 0x580e8613be98 in _sdsnewlen ../pb/redis/src/sds.c:125
    ...
```

ASan 報告本身就是一份「生命週期追蹤」：**出生**在 `_sdsnewlen`（`sds.c:125`），**死亡**在 `_sdsMakeRoomFor`（`sds.c:288`，type 5 升級成 type 8 時，header 大小改變，不能原地 realloc，只能配新的、複製、free 舊的），**死後被使用**在 `uaf.c:8`。「7-byte region」就是 1 + 5 + 1。讀陌生程式碼時，把可疑路徑寫成一個小測試丟進 ASan，比在腦中模擬可靠得多。

## 對比與取捨

| 策略 | 誰記得大小 | 釋放方式 | 優點 | 缺點 | 例子 |
|---|---|---|---|---|---|
| 裸 malloc/free | 配置器 | 逐一 free | 標準、工具支援好 | 所有權全靠紀律 | 小程式 |
| 包裝層（zmalloc） | 配置器或前綴 | 配對函式 | 記帳、統一 OOM 策略、可換配置器 | 混用 `free` 會出事 | Redis |
| 可插拔配置器（lua_Alloc） | 呼叫者告訴配置器 | 同一個函式、`nsize=0` | 宿主完全掌控 | 每個呼叫都要傳大小 | Lua、SQLite（`sqlite3_mem_methods`） |
| 參考計數 | — | 計數歸零時 | 共享物件無單一擁有者 | 循環參考會漏、計數操作有成本 | Redis `robj`、kernel `kref` |
| arena / pool | arena | 整批 | 快、不會漏單一物件 | 不能提早釋放單一物件 | 編譯器、parser、kernel `devm` |

## 踩雷集錦

1. **「回傳 `char *` 就是要我 free」**：不一定。`lua_tostring` 回傳的是借用指標，free 它會破壞 Lua 的 heap；`sds` 是 `char *` 但要用 `sdsfree`。**先查配對函式，再查文件，最後才是型別。**
2. **「找不到 free 就是 leak」**：可能是 arena、refcount、`devm`、或所有權被轉移進某個容器（容器的 destructor 會釋放）。判斷 leak 之前，先確認擁有者是誰。
3. **「`x = realloc(x, n)` 很安全」**：`realloc` 失敗時回傳 `NULL`，原區塊還在，這樣寫會把唯一的指標覆蓋成 `NULL` 導致 leak。Redis 的 `zrealloc` 失敗直接 abort 所以沒這問題；讀其他專案時要注意。另一面是 `sdscat` 這類「可能搬家」的函式，舊指標的所有分身都會失效。
4. **「wrapper 和底層可以混用」**：`zmalloc` 配的用 `free` 還，在 jemalloc build 下會把 jemalloc 的區塊交給 glibc（`zmalloc.c:82–89` 把 `free` 重新定義成 `je_free`，這個巨集只在 `zmalloc.c` 這個編譯單元內有效，其他 `.c` 檔的 `free` 仍是 glibc 的）；在無 `HAVE_MALLOC_SIZE` 的平台會少退 8 bytes。badfree 實驗展示的是 sds 層的同類錯誤。
5. **「free(NULL) 會 crash」**：C 標準規定 `free(NULL)` 什麼都不做，Redis 的 `zfree`、`sdsfree` 也都先檢查 `NULL`（`zmalloc.c:371`、`sds.c:203`）。所以看到 `if (p) free(p);` 是多餘的防禦，不代表 `p` 有特殊意義。kernel 的 `kfree(NULL)` 同樣安全。

## 進階：再往深一層

- **`*_usable` 家族**：Redis 7 的 `zmalloc_usable` 回傳配置器實際給的大小（jemalloc 會向上取整到 size class），`sds` 把多出來的空間直接記進 `alloc`——上面 `"hello"` 拿到 8 bytes 就是這個原因。這種「問配置器實際給了多少」的寫法在高效能程式碼裡越來越常見。
- **`zmalloc.c:41` 的 `madvise` 與 `dismissMemory`**：Redis fork 子行程做 RDB 時，子行程會主動把不再需要的記憶體 `madvise(MADV_DONTNEED)` 掉，減少 copy-on-write 的成本（`object.c` 的 `dismissObject` 系列）。讀到這種「釋放但不是 free」的程式碼，要先懂 fork/COW 的領域知識。
- **靜態分析**：`gcc -fanalyzer`（GCC 10 起）能在編譯期找出部分 double free 與 leak；clang 的 scan-build 也是。它們和 ASan 互補：靜態分析看所有路徑但會誤報，ASan 只看實際跑到的路徑但不誤報。
- **kernel 的 `__free()` 與 cleanup.h**：v6.5 之後 kernel 引入以 GCC `cleanup` attribute 為基礎的 scope-based 釋放，讓「離開 scope 自動 kfree」成為可能。這會改變你找釋放點的方式，[Ch 17](./17-kernel-error-conventions.md) 會講。

## 動手練習

1. 在 Redis 原始碼裡找 `listCreate` 的所有呼叫點（`rg -n "listCreate\(" src`），挑三個，追到它們各自的 `listRelease`。有沒有哪個找不到？它的所有權去了哪裡？
2. 修改 `luaalloc.c`，在 `osize` 是 `LUA_TTABLE` 時印出 `nsize`。一個空 table 的 `Table` 結構多大？用 `pahole -C Table ../pb/lua/lua` 對照。
3. 把 `uaf.c` 的 `"-server-with-a-long-suffix"` 改成 `"!"`（短到不需要升級 type）。ASan 還會報錯嗎？為什麼？這告訴你「沒出事」和「沒有 bug」的差別是什麼？
4. 讀 `sds.c` 的 `sdsfree`（`sds.c:202`）。它怎麼知道要往回退幾個 byte？如果有人傳一個用 `malloc` 配置的普通字串進來，會發生什麼？

## 本章重點整理

- C 的所有權資訊只存在於命名、API 形狀與註解；成對命名（new/free、create/destroy、get/put）是讀資源管理的第一條線索。
- 回傳值可能是新配置、借用、或「可能搬家後的新位址」；`s = sdscat(s, ...)` 的寫法本身就是所有權訊號。
- `zmalloc` 用 `HAVE_MALLOC_SIZE` 決定要不要在前面藏一個 size 前綴；`sds` 把 header 放在指標**之前**，靠 `s[-1]` 的 flags 找回 header。
- `lua_Alloc` 把配置、擴大、縮小、釋放合成一個函式，並由 Lua 主動提供舊大小；Lua 的 OOM 是可恢復錯誤，Redis 的是 abort。
- 追蹤生命週期四步：出生、擁有者、分身、死亡；可疑時寫個小程式丟進 ASan。

## 自我檢核

- [ ] 給你一個沒看過的函式簽名 `struct foo *foo_lookup(struct table *t, const char *key)`，你會怎麼判斷要不要釋放回傳值？列出你會查的三個地方。
- [ ] 不看原始碼，畫出一個 type 8 的 sds 在記憶體中的樣子，標出 `zmalloc` 回傳的位址和 `sds` 指標的位址。
- [ ] 為什麼 `sdshdr` 一定要 packed？如果拿掉 `__packed__`，`sdslen` 的哪一步會壞？
- [ ] `lua_Alloc` 的 `osize` 在什麼情況下不是大小？Lua 為什麼要這樣設計？
- [ ] 有人在 code review 說「這裡 alloc 了卻沒有 free，是 leak」，你會先問他哪兩個問題？
- [ ] 解釋 Redis 和 Lua 對記憶體不足的處理哲學差異，以及各自的理由。

## 延伸閱讀

### 原始碼與官方文件

- **Redis 7.2.17 `src/sds.c` 開頭註解與 `src/sdsalloc.h`**
  - **讀哪裡**：`sdsalloc.h` 只有幾行，說明 sds 如何透過 `s_malloc`/`s_free` 巨集接到 `zmalloc`；`sds.c` 的 `_sdsMakeRoomFor`（第 247 行）是貪婪擴張策略的實作。
  - **前提**：本章的 header 圖。
  - **為什麼值得讀**：sds 也是獨立專案（antirez/sds），README 對設計理由（為什麼指標指在 header 之後、為什麼 binary safe）有完整說明。
- **Lua 5.4 Reference Manual §4.1.3「Pointers to strings」與 §4.6 的 `lua_Alloc` 條目** — 原始碼樹的 `manual/manual.of`（第 2562 行、第 2912 行），線上版在 lua.org/manual/5.4/
  - **讀哪裡**：`lua_Alloc` 的完整語意（特別是 `ptr == NULL` 時 `osize` 的意義）；字串指標有效期限的段落。
  - **和本章的關聯**：借用指標的生命週期與可插拔配置器的契約，原文最精確。

### 部落格 / 技術文章

- **Untangling Lifetimes: The Arena Allocator** — Ryan Fleury（個人 Substack 部落格，2022；以標題搜尋可找到）
  - **這篇說什麼**：從「每個物件各自 malloc/free」的痛點出發，論證把生命週期分組、用 arena 管理的好處，並討論 arena 的各種變體。
  - **讀哪裡**：前半的動機與「lifetime grouping」概念；後半的實作細節可選讀。
  - **為什麼值得讀**：把「找不到 free 不等於 leak」這件事講到底，讀完會改變你看 parser/編譯器程式碼的方式。

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：第 3.4 節「Dynamic Allocation」。原書強調辨認程式碼用動態配置建了什麼資料結構，以及常見的配置錯誤模式。
- **《C Interfaces and Implementations》** — David R. Hanson（Addison-Wesley, 1996）
  - **讀哪幾章**：第 5 章「Memory Management」與第 6 章「More Memory Management」（arena）。Hanson 把每個介面的所有權規則寫成明確的「checked runtime error」，是設計 C API 所有權語意的經典參考。書老，但 C 的所有權問題沒變。

### 工具文件

- **AddressSanitizer 文件**（github.com/google/sanitizers 的 wiki，以及 `gcc` 手冊的 `-fsanitize=address` 條目）
  - **讀哪裡**：報告格式說明（allocated / freed / used 三段堆疊）、`ASAN_OPTIONS` 中的 `detect_leaks`。
  - **和本章的關聯**：本章 UAF 實驗的工具；讀碼時用來驗證所有權假說。

記憶體的歸屬弄清楚之後，下一個常讓人迷路的是：同一份原始碼，因為巨集與 `#ifdef`，實際被編譯的程式碼可能和你看到的不一樣。

→ [Ch 13 前置處理器基礎：巨集、條件編譯、展開來看](./13-preprocessor-basics.md)
