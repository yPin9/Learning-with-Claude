# Ch 10 — 指標慣用法：走訪、指標的指標、函式指標

> **目標**：讀到 `while ((*dest++ = *src++) != '\0')`、`struct hlist_node **pprev`、`c->cmd->proc(c)`、`struct eventfd_ctx *ctx = file->private_data;`、`static const void *const disptab[]` 時，能說出每個 `*` 和 `const` 的意思，知道這個形狀是哪一種慣用法，以及它把「真正的資料」或「真正的程式碼」藏在哪裡。
> **鴻溝**：本章主要練**慣用法鴻溝**，並為 Part 5 的**間接鴻溝**打底——函式指標與 `void *` 正是讀 kernel 時「追到這裡就斷了」的根源。

> **環境**：gcc 11.4（WSL Ubuntu 22.04，x86_64，LP64）；kernel v6.18，Redis 7.2.17，Lua 5.4.9。範例在 `~/code_reading/scratch/10/`，ASan 版本用 `-fsanitize=address -g` 編譯。

## 為什麼需要這個？

C 的指標語法只有兩個符號（`*` 和 `&`），但組合出來的慣用法有十幾種，而且同一個 `*` 在宣告和運算式裡意思相反（宣告裡是「這是指標」，運算式裡是「取指向的東西」）。讀碼時卡在指標，通常是下面三種情況之一：

1. **符號太密**：`*dest++ = *src++` 一行裡有四個運算子，要想運算子優先序才知道誰先做。
2. **間接層數**：`struct node **pp` 多了一層，腦中要同時追「指標本身」、「它指向的指標」、「再指向的節點」三樣東西。
3. **型別被藏起來**：`void *private_data` 或函式指標讓你在當下的程式碼裡看不到真正的型別或真正被呼叫的函式。

前兩種是慣用法鴻溝，認得形狀就解決了。第三種是間接鴻溝的開端，本章先教你認出它，真正的追蹤方法在 [Ch 28](./28-ops-tables-indirect-calls.md)。

Spinellis 原書第 3 章（Advanced C Data Types）的前半講的就是指標：用指標走訪、指標當參數、函式指標。本章沿用它的分類，例子換成 kernel、Redis、Lua。

## 先建立直覺：指標是「地址條」，`*` 是「照著地址走過去」

```
  變數          記憶體                       讀法
  ─────         ─────────────────────        ──────────────────────────
  int  x   →   [ 42 ]                        x 是一個盒子，裝 42
  int *p   →   [ &x ] ──────→ [ 42 ]         p 是一張紙條，寫著 x 的地址
  int **pp →   [ &p ] ──→ [ &x ] ──→ [ 42 ]  pp 是一張紙條，寫著「另一張紙條」的地址

  *p   = 照紙條走一步，到 x
  **pp = 走兩步，到 x
  *pp  = 走一步，到 p 那張紙條本身 —— 改 *pp 就是「改寫 p 這張紙條上的地址」
```

最後一行是本章最重要的直覺：**拿到「指標的指標」，你就能改寫別人手上的那張紙條**。鏈結串列刪除、輸出參數、`hlist` 的 `pprev` 全都建立在這上面。

## 指標走訪：字串與陣列

### kernel 的 `strlen` 和 `strcpy`

```c
/* lib/string.c:416（v6.18） */
size_t strlen(const char *s)
{
	const char *sc;

	for (sc = s; *sc != '\0'; ++sc)
		/* nothing */;
	return sc - s;
}
```

```c
/* lib/string.c:80（v6.18），在 #ifndef __HAVE_ARCH_STRCPY 裡 */
char *strcpy(char *dest, const char *src)
{
	char *tmp = dest;

	while ((*dest++ = *src++) != '\0')
		/* nothing */;
	return tmp;
}
```

兩個值得認的形狀：

- **空迴圈體 `/* nothing */;`**：所有工作都在迴圈條件和遞增裡完成。kernel 習慣把分號單獨放一行並加註解，表示「我是故意的」，不是多打了一個分號。
- **`sc - s`**：兩個指向同一陣列的指標相減，結果是**元素個數**（型別 `ptrdiff_t`），不是 byte 數。對 `char` 兩者恰好相同，對其他型別不同。

`strcpy` 外面包著 `#ifndef __HAVE_ARCH_STRCPY`：架構可以提供自己的最佳化版本，這個 C 版本只是後備。讀 `lib/` 底下的函式時，先確認你的目標架構是否真的用這一份（[Ch 36](./36-kconfig-kbuild-ifdef.md)）。

### `*p++`、`(*p)++`、`*++p`：一個實驗說清楚

後置 `++` 的優先序比 `*` 高，所以 `*p++` 是 `*(p++)`：先取出 `*p` 的值，然後 `p` 往前走。實驗（`walk.c`）：

```c
	int a[] = { 10, 20, 30 };
	p = a; v = *p++;
	p = a; v = (*p)++;
	a[0] = 10;
	p = a; v = *++p;
	int *end = a + 3;
```

```
my_strlen("kernel") = 6
my_strcpy -> "pipe"
v = *p++   : v=10, 之後 p 指向 a[1], a[0]=10
v = (*p)++ : v=10, 之後 p 指向 a[0], a[0]=11
v = *++p   : v=20, 之後 p 指向 a[1]
end - a = 3 個元素, (char *)end - (char *)a = 12 bytes
```

| 寫法 | 動的是誰 | 取到的值 |
|---|---|---|
| `*p++` | 指標往前一格 | 舊位置的值 |
| `(*p)++` | 被指的值加一 | 加一前的值 |
| `*++p` | 指標先往前 | 新位置的值 |

讀 `*dest++ = *src++` 時就是「把 src 目前的字元複製到 dest 目前的位置，兩個指標各往前一格」。外面包一層 `( ... ) != '\0'`，是因為賦值運算式的值就是被賦的值——複製完 `'\0'` 之後迴圈停止。

> 寫這個實驗時，第一版我們把 `*p++` 和 `*p` 放在同一個 `printf` 的參數裡，gcc 立刻警告 `operation on ‘p’ may be undefined [-Wsequence-point]`：函式參數的求值順序未指定，同一個運算式裡又讀又改 `p` 是未定義行為。讀到別人的程式碼裡有這種寫法，那是 bug，不是技巧。

### 指標與「結尾」的三種約定

讀走訪迴圈時，先找出它靠什麼停下來：

| 約定 | 停止條件 | 例子 |
|---|---|---|
| sentinel（哨兵值） | `*p != '\0'`、`l->name != NULL` | C 字串、Lua `luaL_Reg` 陣列 |
| 長度 | `i < n` 或 `p < base + n` | Redis sds（長度存在 header） |
| 結尾指標 | `p != end` | `end = a + n`，C++ STL 也是這個慣例 |

Redis 的 sds 字串值得特別提一下：`typedef char *sds;`，它**看起來**是一個 C 字串，但長度存在指標**前面**的 header 裡（`sdslen()` 從 `s[-1]` 讀出型別再往前找 header，[練習 A](./practice-a-cold-read-userspace.md) 會實際追它）。所以 sds 可以裝 `'\0'`，走訪時要用長度而不是哨兵。**同樣是 `char *`，結尾約定可能完全不同**，這要從資料型別的定義去查，不能從使用處猜。

## 指標的指標

### 鏈結串列刪除：兩種寫法

先看教科書版：

```c
/* ~/code_reading/scratch/10/unlink.c */
static void remove_prev(struct node **headp, int v)
{
	struct node *prev = NULL, *cur = *headp;

	while (cur && cur->val != v) {
		prev = cur;
		cur = cur->next;
	}
	if (!cur)
		return;
	if (prev)
		prev->next = cur->next;
	else
		*headp = cur->next;     /* 特例：刪的是頭 */
	free(cur);
}
```

再看指標的指標版：

```c
static void remove_pp(struct node **headp, int v)
{
	struct node **pp = headp;

	while (*pp && (*pp)->val != v)
		pp = &(*pp)->next;
	if (*pp) {
		struct node *victim = *pp;
		*pp = victim->next;     /* 頭或中間，同一行 */
		free(victim);
	}
}
```

輸出（ASan 無錯誤）：

```
start                       : 1 2 3 4 5
remove_prev(3)              : 1 2 4 5
remove_prev(1) (head)       : 2 4 5
remove_pp(3)                : 1 2 4 5
remove_pp(1) (head)         : 2 4 5
remove_pp(5) (tail)         : 2 4
remove_pp(42) (missing)     : 2 4
```

關鍵在於 `pp` 的意義：**它不指向節點，它指向「那個指向目前節點的指標」**。一開始那個指標是 `head` 本身；往前走一步之後，那個指標是前一個節點的 `next` 欄位。`*pp = victim->next` 就是「把指向 victim 的那張紙條改寫成 victim 的下一個」——不管那張紙條是 `head` 還是某個 `next`，寫法都一樣，所以頭節點的特例消失了。

```
remove_pp(&head, 3)，走到 3 時：

  head ──→ [1|next] ──→ [2|next] ──→ [3|next] ──→ [4|next] ──→ ...
                            ▲
                            └── pp 指向這個 next 欄位（它的值是 &node3）

  *pp = victim->next  ⇒  node2.next 改成指向 node4
```

Linus Torvalds 在 2016 年的 TED 訪談（"The mind behind Linux"）裡拿這兩種寫法當例子，說後者是程式設計的「good taste」：好的寫法讓特例消失，而不是用 `if` 處理特例。這個說法常被引用，但要注意它是一種風格主張，不是正確性問題——兩個版本的行為完全相同。

讀碼時，`pp = &(*pp)->next` 這行是辨識這個慣用法的 beacon：**看到「取一個欄位的位址再存回同一個變數」，就是在用指標的指標走訪**。

### kernel 的 `hlist`：把 `pprev` 存進節點

`hlist` 是 kernel 給 hash table bucket 用的單向鏈結串列。它的節點長這樣：

```c
/* include/linux/types.h:203（v6.18） */
struct hlist_head {
	struct hlist_node *first;
};

struct hlist_node {
	struct hlist_node *next, **pprev;
};
```

`pprev` 正是上一節的 `pp`，只是**直接存在每個節點裡**：它指向「指向我的那個指標」——對第一個節點，是 `head->first`；對其他節點，是前一個節點的 `next`。

```c
/* include/linux/list.h:1033（v6.18） */
static inline void hlist_add_head(struct hlist_node *n, struct hlist_head *h)
{
	struct hlist_node *first = h->first;
	WRITE_ONCE(n->next, first);
	if (first)
		WRITE_ONCE(first->pprev, &n->next);
	WRITE_ONCE(h->first, n);
	WRITE_ONCE(n->pprev, &h->first);
}
```

```c
/* include/linux/list.h:987（v6.18） */
static inline void __hlist_del(struct hlist_node *n)
{
	struct hlist_node *next = n->next;
	struct hlist_node **pprev = n->pprev;

	WRITE_ONCE(*pprev, next);
	if (next)
		WRITE_ONCE(next->pprev, pprev);
}
```

`WRITE_ONCE` 先當成普通賦值讀（它防止編譯器拆開或合併寫入，[Ch 21](./21-read-once-and-barriers.md) 講）。去掉它之後，`__hlist_del` 的核心就是 `*pprev = next`——和 `remove_pp` 的 `*pp = victim->next` 一模一樣。

我們把這兩個函式搬到 user-space 驗證（`hlist.c`），三個 item 依序 `hlist_add_head` 進同一個 bucket：

```
after add     : 3 2 1
it[2].node.pprev == &bucket.first   ? 1
it[1].node.pprev == &it[2].node.next? 1
*it[1].node.pprev == &it[1].node    ? 1
del key 3     : 2 1
del key 1     : 2
```

第三行是 `pprev` 的不變量：**`*n->pprev == n`**，沿著 `pprev` 走一步再解參考，會回到自己。

**為什麼不用雙向鏈結串列的 `prev` 指標？** 因為 hash table 有大量 bucket，大多數是空的。`list_head` 的頭需要兩個指標（`next`、`prev`），`hlist_head` 只要一個（`first`），bucket 陣列的記憶體減半。代價是頭和節點的型別不同，沒辦法用普通的 `prev` 指回頭——`pprev` 用「指向指標的指標」解決了這個型別不一致：不管前面是 `hlist_head.first` 還是 `hlist_node.next`，它們都是 `struct hlist_node *`，所以都能用 `struct hlist_node **` 指到。刪除時也不需要知道 bucket 在哪（上面刪 key 3 和 key 1 都沒傳 bucket）。`list_head` 與 `hlist` 的完整比較在 [Ch 23](./23-list-head-hlist.md)。

### 輸出參數（out parameter）

C 函式只能回傳一個值。要回傳多個值，或「錯誤碼 + 結果」，慣例是讓呼叫端傳一個指標進來，函式透過它寫回結果：

```c
/* redis-7.2.17/src/object.c:909 */
int getLongFromObjectOrReply(client *c, robj *o, long *target, const char *msg) {
    long long value;

    if (getLongLongFromObjectOrReply(c, o, &value, msg) != C_OK) return C_ERR;
    if (value < LONG_MIN || value > LONG_MAX) {
        if (msg != NULL) {
            addReplyError(c,(char*)msg);
        } else {
            addReplyError(c,"value is out of range");
        }
        return C_ERR;
    }
    *target = value;
    return C_OK;
}
```

讀輸出參數時要確認三件事：

1. **回傳值是什麼**：這裡是 `C_OK` / `C_ERR`，結果在 `*target`。
2. **失敗時輸出參數有沒有被寫**：這裡**沒有**，`*target = value` 只在成功路徑。呼叫端在失敗時不能讀 `target`。Ch 8 看過的 `setrangeCommand()` 就是 `if (getLongFromObjectOrReply(...) != C_OK) return;`，失敗立刻離開。
3. **能不能傳 NULL**：`msg` 可以是 NULL（用預設訊息），`target` 不行。Lua 的 `lua_tolstring` 則允許長度參數是 NULL：

```c
/* lua-5.4.9/lapi.c:405、419 */
LUA_API const char *lua_tolstring (lua_State *L, int idx, size_t *len) {
  ...
  if (len != NULL)
    *len = tsslen(tsvalue(o));
```

「`if (out != NULL) *out = ...`」是「這個輸出是選用的」的 beacon。

kernel 裡最常見的輸出參數是 `struct xxx **`，用來回傳一個新配置的物件：

```c
/* fs/pipe.c:926（v6.18） */
int create_pipe_files(struct file **res, int flags)
```

`res` 是兩個 `struct file *` 的陣列，函式把讀端和寫端分別寫進 `res[0]`、`res[1]`，回傳值是 0 或 -errno。kernel 的另一個選擇是直接回傳指標、用 `ERR_PTR()` 把錯誤碼編進指標值裡，這在 [Ch 17](./17-kernel-error-conventions.md) 講。

### 會搬家的物件：回傳新指標 vs 傳指標的指標

一個可能 `realloc` 的物件，函式有兩種方式把新位址交回去。Redis 的 sds 選了「回傳新指標」：

```c
/* redis-7.2.17/src/sds.c:488 */
/* Append the specified binary-safe string pointed by 't' of 'len' bytes to the
 * end of the specified sds string 's'.
 *
 * After the call, the passed sds string is no longer valid and all the
 * references must be substituted with the new pointer returned by the call. */
sds sdscatlen(sds s, const void *t, size_t len) {
```

所以 Redis 裡到處是 `o->ptr = sdscatlen(o->ptr, ...)` 這種「自己賦值給自己」的寫法。讀到它要知道：**舊指標在這行之後可能已經失效**，任何先前存起來的別名（alias）都不能再用。另一種設計是 `void sdscat(sds *sp, ...)`，由函式透過指標的指標改寫呼叫端的變數；Redis 沒有這樣做，它的取捨是 API 比較好寫，但容易忘記接回傳值。

## 函式指標

### 兩種 typedef 風格

```c
/* lua-5.4.9/lua.h:106 */
typedef int (*lua_CFunction) (lua_State *L);
```

```c
/* redis-7.2.17/src/server.h:2218、2329 */
typedef void redisCommandProc(client *c);
...
    redisCommandProc *proc; /* Command implementation */
```

這兩個 typedef 看起來很像，意思不同：

- Lua 的 `lua_CFunction` 是**函式指標型別**。宣告變數時直接寫 `lua_CFunction f;`。
- Redis 的 `redisCommandProc` 是**函式型別**（不是指標）。宣告變數時要自己加 `*`：`redisCommandProc *proc;`。

Redis 的風格有個好處：`*` 在宣告處看得見，讀者知道這是指標。也因為 `redisCommandProc` 是函式型別，它可以拿來宣告函式原型（例如 `redisCommandProc appendCommand;`），雖然 Redis 實際上沒這樣用。兩種寫法我們都編譯驗證過（`fptr.c`），`sizeof(commandProc *)` 是 8，和任何指標一樣。

### 呼叫函式指標的寫法

```c
	cfunc f = square;
	printf("f(5)=%d (*f)(5)=%d (**f)(5)=%d\n", f(5), (*f)(5), (**f)(5));
```

```
f(5)=25 (*f)(5)=25 (**f)(5)=25
```

函式名稱在運算式裡會自動轉成指向它的指標，對函式指標解參考又得到函式，函式又自動轉回指標——所以 `f(5)`、`(*f)(5)`、`(**f)(5)` 全部等價。舊程式碼常寫 `(*f)(x)` 強調「這是間接呼叫」，kernel 和 Redis 現在多半寫 `f->op(x)`。**讀碼時，任何 `x->y(...)` 或 `x.y(...)` 都要意識到：這不是一個函式名稱，這是一個間接呼叫，你在這裡看不到真正被呼叫的是誰**。

### 分派表：用名稱或索引找函式

把函式指標放進陣列，就成了分派表（dispatch table）。兩種形狀：

**名稱查表**（每個元素 = 名字 + 函式指標）：

```c
/* lua-5.4.9/lauxlib.h:38 */
typedef struct luaL_Reg {
  const char *name;
  lua_CFunction func;
} luaL_Reg;
```

```c
/* lua-5.4.9/lbaselib.c:506 */
static const luaL_Reg base_funcs[] = {
  {"assert", luaB_assert},
  {"collectgarbage", luaB_collectgarbage},
  {"dofile", luaB_dofile},
  ...
```

這個陣列由 Ch 8 看過的 `luaL_setfuncs()` 用 sentinel 迴圈逐一註冊進 Lua 的全域表。所以「Lua 程式碼呼叫 `assert()` 時會執行哪個 C 函式」的答案，就是在 `base_funcs` 裡找 `"assert"`。

Redis 的命令表是同一個形狀，但由腳本產生，一行很長：

```c
/* redis-7.2.17/src/commands.def:10870（節錄） */
{MAKE_CMD("append","Appends a string to the value of a key. ...", ...,appendCommand,3,CMD_WRITE|CMD_DENYOOM|CMD_FAST, ...),.args=APPEND_Args},
```

真正的呼叫點只有一行：

```c
/* redis-7.2.17/src/server.c:3527，call() */
    c->cmd->proc(c);
```

**這一行就是 Redis 的間接鴻溝**：從 `call()` 往下追，你在這行斷掉。解法是反過來找「`proc` 欄位在哪裡被賦值」——答案是 `commands.def` 這張表，`APPEND` 對應 `appendCommand`。這個「找賦值點」的技巧就是 Ch 28 的主題。

**索引查表**（陣列索引 = opcode）：Ch 8 看過 Lua 直譯器在 gcc 上用 `disptab[]` 存每個 opcode 處理程式的標籤位址，`goto *disptab[op]`。一般 C 版本則是函式指標陣列：

```c
/* ~/code_reading/scratch/10/fptr.c */
static const cfunc optable[] = { twice, square };
...
	for (unsigned op = 0; op < sizeof(optable) / sizeof(optable[0]); op++)
		printf("optable[%u](7) = %d\n", op, optable[op](7));
```

```
get    arity=2 reply=5
append arity=3 reply=-1
optable[0](7) = 14
optable[1](7) = 49
```

`sizeof(optable) / sizeof(optable[0])` 是「陣列元素個數」的慣用寫法，kernel 包成 `ARRAY_SIZE()`。

### kernel 的 ops table

kernel 把一組相關的函式指標放進一個 struct，稱為 ops table。`struct file` 帶著一個指向它的指標：

```c
/* include/linux/fs.h:1211、1214（v6.18） */
struct file {
	...
	const struct file_operations	*f_op;
```

每種檔案提供自己的表，用 designated initializer（`.欄位 = 值`）填：

```c
/* fs/eventfd.c:302（v6.18） */
static const struct file_operations eventfd_fops = {
#ifdef CONFIG_PROC_FS
	.show_fdinfo	= eventfd_show_fdinfo,
#endif
	.release	= eventfd_release,
	.poll		= eventfd_poll,
	.read_iter	= eventfd_read,
	.write		= eventfd_write,
	.llseek		= noop_llseek,
};
```

讀 ops table 的訣竅：**它是一張「介面方法 → 實作函式」的對照表**。看到 VFS 呼叫 `file->f_op->read_iter(...)`，如果你知道這個 file 是 eventfd，就到這張表找 `.read_iter`，答案是 `eventfd_read`。沒列出的欄位是 NULL，呼叫端通常會先檢查（或提供預設行為）。Ch 42 會把它和 C++ 的 vtable 對照。

## `void *`：泛型與 cookie

### `private_data`：通用層不知道、驅動層知道

ops table 解決了「呼叫哪個函式」，`void *` 解決「帶著什麼資料」。`struct file` 有一個欄位：

```c
/* include/linux/fs.h:1216（v6.18） */
	void				*private_data;
```

VFS（通用層）不知道它是什麼，只負責保管。建立檔案的驅動把自己的物件放進去，之後在自己的 callback 裡取出來：

```c
/* fs/eventfd.c:409（v6.18），do_eventfd() 建立檔案時傳入 ctx */
	file = anon_inode_getfile_fmode("[eventfd]", &eventfd_fops,
					ctx, flags, FMODE_NOWAIT);
```

```c
/* fs/anon_inodes.c:173（v6.18），存進 private_data */
	file->private_data = priv;
```

```c
/* fs/eventfd.c:214（v6.18），eventfd 自己的 read 取出來 */
static ssize_t eventfd_read(struct kiocb *iocb, struct iov_iter *to)
{
	struct file *file = iocb->ki_filp;
	struct eventfd_ctx *ctx = file->private_data;
```

`fs/pipe.c` 的 `anon_pipe_read()` 第一段同樣是 `struct pipe_inode_info *pipe = filp->private_data;`。**這一行是 kernel callback 最常見的開場白**：從通用物件取回「我自己的資料」。

這種 `void *` 欄位在不同地方有不同名字，但都是同一個概念——**cookie**：一個由註冊者提供、由框架原封不動保管與回傳的不透明值。

| 地方 | 名稱 | 誰放進去 | 誰取出來 |
|---|---|---|---|
| kernel `struct file` | `private_data` | 建立檔案的驅動 | 同一個驅動的 `f_op` 函式 |
| Redis 事件迴圈 | `clientData` | `aeCreateFileEvent()` 的呼叫者 | 註冊的 `aeFileProc` 回呼 |
| C 標準函式庫 | `qsort` 的元素指標 | `qsort` 自己 | 比較函式 |
| pthread | `void *arg` | `pthread_create` 的呼叫者 | 執行緒函式 |

Redis 的版本：

```c
/* redis-7.2.17/src/ae.h:66 */
typedef void aeFileProc(struct aeEventLoop *eventLoop, int fd, void *clientData, int mask);
```

### 跑一個縮小版的 VFS

`cookie.c` 模擬一個通用層 `vfs_read()`，它只知道 `f->f_op->read(f, ...)`，兩種「驅動」各自把不同的結構放進 `private_data`：

```c
static long vfs_read(struct file *f, char *buf, size_t len)
{
	return f->f_op->read(f, buf, len);    /* 間接呼叫：通用層的盡頭 */
}

static long eventfd_read(struct file *f, char *buf, size_t len)
{
	struct eventfd_ctx *ctx = f->private_data;   /* void * 隱式轉回來 */
	...
}
```

```
vfs_read(eventfd) = 3
vfs_read(eventfd) = 0 (計數已清零)
vfs_read(pipe)    = 8
qsort: -7 0 13 42
```

（`qsort` 那行用的比較函式是 `(x > y) - (x < y)`，避免 `x - y` 在極端值時溢位——Ch 9 的整數陷阱又出現了。）

### 故意弄壞：型別放錯

`void *` 的代價是**編譯器不再檢查型別**。把 `pipe_info` 當成 `eventfd_ctx` 讀（`cookie_bug.c`）：

```c
	struct pipe_info pi = { .bytes = 100 };
	void *private_data = &pi;                    /* 實際上是 pipe_info */
	struct eventfd_ctx *ctx = private_data;      /* 當成 eventfd_ctx 讀 */
	printf("ctx->count = %llu\n", ctx->count);
```

`gcc -Wall -Wextra` 沒有任何警告。執行時 ASan 抓到越界讀取（`pipe_info` 只有 4 byte，`count` 讀 8 byte）：

```
==347687==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7ffeb7ba45c0 at pc 0x5875d5f33341 bp 0x7ffeb7ba4580 sp 0x7ffeb7ba4570
READ of size 8 at 0x7ffeb7ba45c0 thread T0
    #0 0x5875d5f33340 in main /home/ypp/code_reading/scratch/10/cookie_bug.c:9
```

如果兩個結構一樣大，連 ASan 都抓不到，只會讀出錯誤的值。所以讀 kernel 時，**`private_data` 的真實型別要從「誰把它放進去」去查**，不能從欄位宣告查。這正是間接鴻溝的第二種形式：函式指標藏起「誰被呼叫」，`void *` 藏起「資料是什麼」。

> 注意：C 允許 `void *` 隱式轉換成任何物件指標，所以 C 程式碼裡 `struct eventfd_ctx *ctx = file->private_data;` 不需要轉型。C++ 不允許，要寫 `static_cast`。看到 C 程式碼裡寫 `(struct foo *)malloc(...)`，通常是作者習慣 C++ 或程式碼要同時能被 C++ 編譯。

kernel 另一個「從通用指標取回具體物件」的方法是 `container_of`：不是用 `void *` 存，而是把通用結構**嵌入**具體結構裡，再用位移量算回外層。上面 `hlist.c` 的 `item_of()` 就是一個簡化版。它比 `void *` 多一點型別安全，在 [Ch 16](./16-container-of.md) 專章講。

## 讀懂 `const` 與複雜宣告

### 右左法則

讀 C 宣告的方法：**從變數名稱開始，先往右讀，碰到右括號或行尾就往左讀，碰到左括號就跳出這一層繼續往右**。`[]` 念「陣列」，`()` 念「函式」，`*` 念「指標，指向」，`const` 修飾它**左邊**緊鄰的東西（如果左邊沒東西，就修飾右邊）。

拿 Lua 的 `ljumptab.h` 練習：

```c
/* lua-5.4.9/ljumptab.h */
static const void *const disptab[NUM_OPCODES] = {
```

```
disptab                     disptab 是
disptab[NUM_OPCODES]        一個有 NUM_OPCODES 個元素的陣列，元素是
*const                      const 的指標（指標本身不能改），指向
const void                  const void（指向的東西也不能改）
```

兩個 `const` 各管一層：表裡的指標不能被改寫（表是唯讀的），透過這些指標也不能改寫指向的東西（它們指向程式碼標籤）。

### 讓編譯器替你念：錯誤訊息技巧

這台環境沒有 `cdecl`（也沒有 sudo 可以安裝）。替代方法是故意用錯型別初始化，讓 gcc 在警告裡把型別寫出來（`decl.c`）：

```c
static const void *const disptab[3];
static const char *names[4];
static char *const fixed;
static int (*lua_cf)(lua_State *L);
void (*signal_like(int sig, void (*handler)(int)))(int);
static const char *const *argvp;

void show(void)
{
	long t1 = &disptab;
	...
}
```

gcc 的 `-Wint-conversion` 警告依序吐出（我們只留下型別部分）：

```
‘const void * const (*)[3]’
‘const char * (*)[4]’
‘char * const*’
‘int (**)(lua_State *)’
‘void (* (*)(int,  void (*)(int)))(int)’
‘const char * const**’
```

因為我們取了 `&`（避免陣列和函式自動退化成指標），每個型別外面都多了一層 `(*)` 或 `*`，扣掉那一層就是原宣告的型別。第五個是 `signal(2)` 的經典形狀：「一個函式，參數是 int 和一個函式指標，回傳一個函式指標」。真實程式碼裡沒人這樣寫，都用 typedef 拆開（glibc 的 `sighandler_t`）——**讀到難懂的宣告，先看有沒有 typedef 可以用，或自己在筆記裡拆一個**。

### `const` 的位置：實驗

```c
	const char *p = buf;      /* 指向 const char 的指標 */
	char *const q = buf;      /* 指向 char 的 const 指標 */
	const char *const r = buf;

	p++;        /* OK */
	*p = 'x';   /* 錯 */
	*q = 'x';   /* OK */
	q++;        /* 錯 */
	*r = 'x';   /* 錯 */
	r++;        /* 錯 */
```

```
9:12: error: assignment of read-only location ‘*p’
11:10: error: increment of read-only variable ‘q’
12:12: error: assignment of read-only location ‘*(const char *)r’
13:10: error: increment of read-only variable ‘r’
```

口訣：**`const` 在 `*` 左邊，保護的是資料；在 `*` 右邊，保護的是指標本身**。`const char *` 和 `char const *` 是同一個意思（kernel 兩種都有，前者多）。

### kernel 為什麼到處 `const struct xxx_operations`

`static const struct file_operations eventfd_fops` 的 `const` 讓整張表被放進唯讀區段（`.rodata`）。ops table 裡全是函式指標，如果它可寫，攻擊者只要能寫一個 kernel 位址，就能把某個 `.read_iter` 改成任意函式。kernel 這些年持續把 ops table 加上 `const`（社群稱為 constification），也是這個原因。讀碼時看到**沒有** `const` 的 ops table，通常代表它需要在執行期被修改（例如依硬體能力填入不同函式），值得多看一眼它在哪裡被寫。

函式參數上的 `const` 則是**承諾**：`size_t strlen(const char *s)` 承諾不改你的字串。讀碼時這是很有用的資訊——一個參數是 `const T *`，你就知道這個函式對它是唯讀的，追資料流時可以跳過。

## 對比與取捨

| 需求 | 做法 A | 做法 B | kernel / Redis / Lua 的選擇 |
|---|---|---|---|
| 刪除單向鏈結串列節點 | 追 `prev`，頭節點特判 | `struct node **pp` | kernel `hlist` 把 `pprev` 存進節點 |
| 回傳結果 + 錯誤 | 輸出參數 + 錯誤碼 | 回傳指標，錯誤編進指標（`ERR_PTR`） | 兩種都有；Redis 多用前者 |
| 可能搬家的物件 | 回傳新指標 | 傳指標的指標 | Redis sds 回傳新指標 |
| 每種物件不同的行為 | `switch (type)` | ops table（函式指標 struct） | kernel 幾乎全用 ops table |
| 回呼帶自己的資料 | `void *` cookie | 嵌入 + `container_of` | 兩種都有；新程式碼偏好 `container_of` |
| 函式指標 typedef | `typedef int (*fn)(...)` | `typedef int fn(...)` | Lua 用 A，Redis 用 B |

## 踩雷集錦

1. **「`*p++` 是把 `*p` 加一」**：不是。後置 `++` 優先序比 `*` 高，`*p++` 是取值後指標前進。要加被指的值寫 `(*p)++`。
2. **「指標相減得到 byte 數」**：得到的是元素個數。`int *` 相差 3 個元素就是 12 byte（`walk.c` 輸出）。要 byte 數得先轉成 `char *`。
3. **「`pprev` 指向前一個節點」**：不是，它指向「前一個節點的 `next` 欄位」（或 `head->first`）。所以 `pprev` 的型別是 `struct hlist_node **`，不是 `struct hlist_node *`。把它當成前一個節點去存取欄位，會讀到錯的位址。
4. **「`void *` 轉換有編譯器把關」**：沒有。C 的 `void *` 可以隱式轉成任何物件指標，`-Wall -Wextra` 不會警告型別放錯（`cookie_bug.c`）。真實型別要從賦值處查。
5. **「輸出參數失敗時會被設成 0 或 NULL」**：不一定。Redis 的 `getLongFromObjectOrReply()` 失敗時完全不碰 `*target`。除非文件或程式碼明確保證，失敗時不要讀輸出參數。
6. **「`const char *p` 的 `p` 不能改」**：`p` 可以改（可以 `p++`），不能改的是 `*p`。要讓指標本身不能改，`const` 要寫在 `*` 右邊。

## 進階：再往深一層

- **函式指標與 CFI**：kernel 的間接呼叫在開啟 `CONFIG_CFI`（kernel Control Flow Integrity，kCFI；v6.18 的 `arch/Kconfig:927`，舊名 `CONFIG_CFI_CLANG` 在 v6.18 只剩過渡用的 transitional 選項）時，會在呼叫前檢查目標函式的靜態型別是否符合。這讓「把 ops table 裡的函式指標改成任意函式」更難利用，也解釋了為什麼 kernel 開發者對函式指標型別的轉型越來越謹慎。讀碼時看到 `__nocfi` 這類標註就是相關的。
- **retpoline 與間接呼叫的成本**：Spectre v2 之後，x86 kernel 的間接呼叫可能被編譯成 retpoline thunk，成本比直接呼叫高。kernel 因此在熱路徑上有一些「如果是最常見的那個實作就直接呼叫」的最佳化（例如網路堆疊的 `INDIRECT_CALL_*` 巨集）。讀到它時，知道它的語意等價於普通的間接呼叫即可。
- **用 gdb 解開間接呼叫**：在執行中的程式裡，`p c->cmd->proc` 會直接印出 `(redisCommandProc *) 0x... <appendCommand>`，函式名稱就在角括號裡。這是追間接呼叫最直接的動態方法，[練習 A](./practice-a-cold-read-userspace.md) 會在 Redis 上實際用 gdb 停在 `_sdsMakeRoomFor`，Ch 52 把同樣的技巧用在 kernel。
- **`restrict`**：C99 的 `restrict` 承諾「這個指標是存取該物件的唯一途徑」，讓編譯器可以做更激進的最佳化。kernel 很少用（kernel 用 `-fno-strict-aliasing` 編譯，對別名的態度本來就保守），但 user-space 的高效能程式碼（例如 `memcpy` 的原型）會看到。

## 動手練習

1. 在 `unlink.c` 加一個 `insert_sorted_pp(struct node **headp, int v)`，用指標的指標把新節點插入排序位置。寫完之後檢查：插在頭、中間、尾巴三種情況是不是同一段程式碼處理。
2. 在 v6.18 tree 裡 `git grep -n "\*\*pprev" -- include/linux/*.h`，找出除了 `hlist` 之外還有哪些結構用了同樣的技巧。
3. 從 Redis 的 `call()`（`server.c:3527`）出發，不用 gdb，只靠 `grep` 找出 `GETRANGE` 命令最後會執行哪個 C 函式。寫下你搜尋的每一步。
4. 用 `decl.c` 的技巧，讓 gcc 念出 `include/linux/fs.h` 裡 `struct file_operations` 的 `read_iter` 欄位的型別（提示：在 user-space 複製那一行宣告，把 kernel 型別換成不完整的 struct 宣告）。

## 本章重點整理

- 指標走訪有三種結尾約定（哨兵、長度、結尾指標），讀迴圈前先確認是哪一種；`*p++` 是取值後指標前進。
- 指標的指標讓你改寫「別人手上的那個指標」，是消除鏈結串列頭節點特例、實作輸出參數、`hlist` 的 `pprev` 的共同基礎。不變量：`*n->pprev == n`。
- 函式指標和 ops table 藏起「誰被呼叫」，`void *` cookie 藏起「資料是什麼」；兩者都要從賦值處反查，這是間接鴻溝的根源。
- 宣告用右左法則讀；`const` 在 `*` 左邊保護資料、右邊保護指標。沒有 `cdecl` 時，讓 gcc 的錯誤訊息替你念型別。

## 自我檢核

- [ ] 不看本章，在紙上寫出 `remove_pp()`，並用一張圖解釋為什麼它不需要處理頭節點特例
- [ ] 能解釋 `hlist_node` 的 `pprev` 為什麼是 `**` 而不是 `*`，以及 `hlist` 比 `list_head` 省在哪裡
- [ ] 有人給你一行 `c->cmd->proc(c);`，你能說出兩種找出真正被呼叫函式的方法（靜態與動態各一）
- [ ] 能說明 Redis 的 `typedef void redisCommandProc(client *c);` 和 Lua 的 `typedef int (*lua_CFunction)(lua_State *L);` 的差異
- [ ] 不看本章，用右左法則念出 `static const void *const disptab[N]`
- [ ] 能說出 kernel 為什麼要把 ops table 宣告成 `const`

## 延伸閱讀

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪裡**：第 3 章「Advanced C Data Types」的前半（指標的各種用途：走訪、參數傳遞、函式指標）。
  - **能學到什麼**：原書把指標用途分類成幾種固定模式，本章的分類就是從這裡來的。
  - **前提**：會寫 C。

- **《Expert C Programming: Deep C Secrets》** — Peter van der Linden（Prentice Hall, 1994）
  - **讀哪裡**：第 3 章 "Unscrambling Declarations in C"（教你一套讀複雜宣告的流程，並拆解 `signal` 的宣告）、第 4 章 "The Shocking Truth: C Arrays and Pointers Are NOT the Same!"。
  - **能學到什麼**：本章右左法則的完整版，以及陣列與指標什麼時候等價、什麼時候不等價。書很老，但 C 的宣告語法沒變，內容仍然適用。
  - **前提**：會寫 C。

### 官方文件

- **Linux kernel 的 `include/linux/list.h`**（v6.18）
  - **讀哪裡**：`hlist_` 開頭的函式（`hlist_del` 在第 1004 行，之後依序是 `hlist_add_head`、`hlist_add_before`（1048）、`hlist_add_behind`（1062）），觀察 `pprev` 在插入時怎麼被更新。
  - **能學到什麼**：本章 `hlist.c` 只示範了 `add_head` 和 `del`，其他操作是同一個不變量的延伸。
  - **前提**：本章「kernel 的 hlist」一節。

- **Overview of the Linux Virtual File System**（v6.18 tree 的 `Documentation/filesystems/vfs.rst`）
  - **讀哪裡**："struct file_operations" 一節，逐條說明每個 callback 什麼時候被呼叫。
  - **能學到什麼**：ops table 的每個欄位在介面層面的語意，這是解讀 `f_op->xxx()` 時的權威參考。
  - **前提**：本章「kernel 的 ops table」一節。

### 演講

- **"The mind behind Linux"** — Linus Torvalds 與 Chris Anderson 的對談（TED, 2016）
  - **看哪裡**：中段討論「good taste」的部分，他在螢幕上比較了本章 `remove_prev` / `remove_pp` 兩種鏈結串列刪除寫法。
  - **能學到什麼**：kernel 維護者怎麼看待「消除特例」這種程式碼品味，以及為什麼指標的指標在 kernel 文化裡被視為基本功。
  - **前提**：本章「指標的指標」一節。

到這裡，Part 2 的前三章處理了「一行程式碼」層級的慣用法。下一章往上一層，看資料本身的形狀：struct、union，以及 C 裡模擬「多型資料」的 tagged union。

→ [Ch 11 struct、union 與 tagged union：讀懂資料的形狀](./11-structs-unions-tagged.md)
