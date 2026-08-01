# Ch 27 — 讀懂 kernel/系統程式慣例

> **目標**：Linux kernel 與底層系統 C 有一套自成一格的慣用法（idiom），第一次讀的人會覺得像另一種語言：`container_of` 從一個成員指標回推整個結構、`list_head` 這種「侵入式」鏈表把節點嵌進資料裡、函式用一連串 `goto err` 收尾、指標裡塞著錯誤碼（`ERR_PTR`/`IS_ERR`）、到處是 `likely`/`unlikely`。這章把這些慣例一次講透——為什麼要這樣寫、讀到時心裡該翻譯成什麼。`container_of` 和侵入式鏈表我們用最小 C 範例**真編真跑印出位址推導**、用 redis 的 `adlist` 做「侵入式 vs 非侵入式」對照、`likely`/`unlikely` 用 objdump 看它真的改了分支佈局；kernel 專屬的 `container_of`/`list_head`/`ERR_PTR` 原始碼直接引自真實 Linux 樹。讀完你有底氣接你的 kernel_internals / kernel_pwn 課。

## 為什麼 kernel code 讀起來像另一種語言？

kernel 和一般應用程式的 C，長得不一樣，因為它們的約束不同：

- **不能用 malloc 隨便配、不能有 GC**：kernel 的記憶體很珍貴、配置有嚴格規則，所以它發展出「把鏈表節點直接嵌進你的資料結構」這種省一次配置的手法（侵入式資料結構）。
- **不能有例外（exception）**：C 本來就沒有，但 kernel 連 `setjmp/longjmp` 都不用。於是錯誤處理只能靠 return code + `goto` 清理，還發展出「把錯誤碼塞進指標回傳值」的 `ERR_PTR` 慣例來省一個 out-parameter。
- **效能是生死線**：kernel 一條熱路徑可能一秒跑幾百萬次，所以它在意「分支預測」（`likely`/`unlikely`）、「每 CPU 一份避免鎖」（per-cpu）、「讀者不用鎖」（RCU）這些一般應用不會計較的東西。
- **沒有型別安全網、沒有 template**：純 C，泛型只能靠巨集和 `void *` 加位址運算硬幹。`container_of` 就是「用純 C 做出型別安全的向上轉型」的產物。

所以這些慣例不是 kernel 作者愛炫技，是**約束逼出來的解**。讀懂它們的最快方式，是理解「它在解什麼問題」。這章每個慣例都從問題講起。

## container_of：從成員指標回推整個結構

這是 kernel 第一慣用法，也是所有侵入式資料結構的地基。問題是：**我手上只有一個指向 struct 某成員的指標，怎麼拿回整個 struct 的指標？**

先看它為什麼會發生。想像一條泛型鏈表：鏈表的節點型別是 `struct list_head`，它只有 `next`/`prev`，不知道你的資料長怎樣。你把 `list_head` **嵌進**你的資料結構裡：

```c
struct task {
    int id;
    char name[16];
    struct list_head node;   /* 嵌入的鏈表節點，不是指標 */
};
```

當你走訪鏈表時，迭代器給你的是 `struct list_head *`（指向某個 `task` 的 `node` 成員）。但你要的是那個 `task`。`node` 在 `task` 裡的偏移是固定的，所以「node 的位址減掉偏移」就是 `task` 的位址。這就是 `container_of`。

**真跑最小範例**（自寫 C，真編真跑）：

```c
#include <stddef.h>
#define container_of(ptr, type, member) \
    ((type *)((char *)(ptr) - offsetof(type, member)))

struct list_head { struct list_head *next, *prev; };
struct task { int id; char name[16]; struct list_head node; };

int main(void) {
    struct task t = { .id = 42, .name = "worker" };
    struct list_head *lp = &t.node;         /* 只握有成員指標 */
    struct task *back = container_of(lp, struct task, node);
    printf("offsetof(node) = %zu\n", offsetof(struct task, node));
    printf("&t          = %p\n", (void*)&t);
    printf("lp (&t.node)= %p\n", (void*)lp);
    printf("back        = %p  id=%d name=%s\n", (void*)back, back->id, back->name);
}
```

真跑輸出：

```
offsetof(node) = 24
&t          = 0x7ffdd8455d20
lp (&t.node)= 0x7ffdd8455d38
back        = 0x7ffdd8455d20  id=42 name=worker
```

看位址就懂了：`node` 在 `task` 裡偏移 24（int 4 + name 16 = 20，對齊到 8 補到 24）。`lp` 是 `0x...d38`，減掉 24（`0x18`）正好回到 `0x...d20` = `&t`。`back->id` 拿到 42、`back->name` 拿到 "worker"，**從一個成員指標完整還原了整個結構**。這就是 `container_of` 的全部魔法——一次減法。

**真實 kernel 的版本**（引自 `include/linux/container_of.h`）比我們的多了型別安全檢查：

```c
#define container_of(ptr, type, member) ({				\
	void *__mptr = (void *)(ptr);					\
	static_assert(__same_type(*(ptr), ((type *)0)->member) ||	\
		      __same_type(*(ptr), void),			\
		      "pointer type mismatch in container_of()");	\
	((type *)(__mptr - offsetof(type, member))); })
```

多出來的 `static_assert(__same_type(...))` 在編譯期檢查「你傳的 `ptr` 型別，真的跟 `type` 裡那個 `member` 的型別一致嗎」——防止你把偏移算錯。核心運算 `__mptr - offsetof(type, member)` 跟我們的最小版一模一樣。那個 `({ ... })` 是 GCC 的 statement expression（Ch 22 巨集章講過），讓巨集能有區域變數又能當表達式用。

> 讀碼要點：**看到 `container_of(ptr, SomeType, some_member)`，立刻翻譯成「ptr 其實指向某個 SomeType 的 some_member 成員，我現在要拿回那個 SomeType」。** 這是 kernel 裡「從通用回到具體」的標準動作，鏈表、紅黑樹、work queue、driver model 全靠它。

## 侵入式資料結構：list_head

理解了 `container_of`，就能理解 kernel 的鏈表為什麼跟你學過的不一樣。對比兩種設計：

**非侵入式（intrusive 的反面，redis 的 `adlist` 就是這種）**——節點自己配一塊，資料用 `void *value` 指過去（引自 redis `adlist.h`）：

```c
typedef struct listNode {
    struct listNode *prev;
    struct listNode *next;
    void *value;              /* 指向真正的資料 */
} listNode;
```

要把一個 `task` 放進這種鏈表，你 `listAddNodeTail(list, task)`——它**另外配一個 `listNode`**，讓 `node->value` 指向你的 `task`。資料和鏈表節點是**兩塊記憶體**，一次遍歷要跳兩次指標（node → value）。

**侵入式（kernel 的 `list_head`）**——鏈表節點**嵌進**你的資料裡（引自 Linux `include/linux/types.h`）：

```c
struct list_head { struct list_head *next, *prev; };  /* 沒有 value！ */
```

`list_head` 裡**沒有指向資料的指標**。因為它根本不需要——它靠 `container_of` 從自己的位址回推資料。你的 `task` 裡直接嵌一個 `struct list_head node;`，串鏈表時串的是這些嵌入的 `node`，要拿資料時用 `container_of(node, struct task, node)` 回推。

這個差別的後果：

| | 非侵入式（redis adlist） | 侵入式（kernel list_head） |
|---|---|---|
| 記憶體 | 資料 + 額外的 node 兩塊 | 只有資料一塊（node 嵌在裡面） |
| 配置次數 | 每次加入配一個 node | 零額外配置 |
| 一個資料能在幾條鏈上 | 一個 node 一條（要多條要多個 node） | 嵌幾個 `list_head` 就能上幾條鏈 |
| 泛型方式 | `void *value`（執行期，無型別檢查） | `container_of`（編譯期型別檢查） |
| 遍歷取資料 | `node->value`（跳一次） | `container_of`（一次減法，無跳指標） |
| 讀碼難度 | 直覺 | 要懂 container_of 才讀得動 |

kernel 選侵入式，是為了**省配置、省 cache miss、讓一個物件同時掛在多條鏈上**（例如一個 `page` 可以同時在 LRU 鏈和 free 鏈上，各嵌一個 `list_head`）。代價是可讀性——你不懂 `container_of` 就完全看不懂它怎麼從 `list_head` 拿到資料。

真實 kernel 走訪鏈表的巨集（引自 `include/linux/list.h`），把這些拼起來：

```c
#define list_entry(ptr, type, member) \
	container_of(ptr, type, member)          /* list_entry 就是 container_of */

#define list_for_each_entry(pos, head, member)				\
	for (pos = list_first_entry(head, typeof(*pos), member);	\
	     !list_entry_is_head(pos, head, member);			\
	     pos = list_next_entry(pos, member))
```

`list_for_each_entry(task, &task_list, node)` 這一行，展開後就是「從 head 開始，每次用 `container_of`（`list_entry`）把 `list_head *` 轉回 `task *`，走到鏈尾為止」。**讀 kernel 看到 `list_for_each_entry`，心裡翻譯成「foreach 每個容器物件」，`container_of` 已經幫你藏在裡面了。**

## 錯誤處理：goto 清理與 ERR_PTR

### goto err 的清理鏈

C 沒有 RAII、沒有 `defer`、沒有 `try/finally`。一個函式配了多個資源（記憶體、鎖、fd），中途任何一步失敗都得把**已經配好的**全部釋放。硬寫會變成巢狀 if 的地獄。kernel 的標準解法是 `goto` 清理鏈——這在 redis 也大量出現（`ae.c`、`anet.c`、`acl.c` 都是，真實 grep 到幾十處）。骨架長這樣：

```c
int setup(void) {
    A *a = alloc_a();
    if (!a) goto err;                 /* 什麼都沒配成，直接跳最末 */

    B *b = alloc_b();
    if (!b) goto err_free_a;          /* a 配了，要還 a */

    C *c = alloc_c();
    if (!c) goto err_free_b;          /* a、b 都配了，要還 b 再還 a */

    return 0;                         /* 成功，不走清理 */

err_free_b: free_b(b);               /* 清理標籤「疊」成階梯 */
err_free_a: free_a(a);               /* fall-through：從哪跳進來，就從那還到底 */
err:        return -1;
}
```

讀這個 pattern 的關鍵：**清理標籤是「反向階梯」，而且刻意用 fall-through 串起來。** 從 `err_free_b` 進來會依序 `free_b` → `free_a` → return；從 `err_free_a` 進來只 `free_a` → return。標籤的順序精確對應「配置的逆序」——後配的先釋放。看到一串 `goto err_xxx` + 底部階梯狀的標籤，別被 `goto` 嚇到（這裡的 `goto` 是**受控的、單向往下的**，不是義大利麵），它就是 C 版的解構順序。**讀法：對每個 `goto err_X`，問「跳到 X 會釋放哪些、漏了哪些」——漏釋放就是 leak bug，多釋放就是 double-free bug，這是 kernel code review 的高頻缺陷。**

### ERR_PTR / PTR_ERR / IS_ERR：指標裡藏錯誤碼

回傳指標的函式怎麼報錯？傳統做法是「回傳 NULL 代表失敗」，但這樣**分不出不同的失敗原因**（是沒記憶體？還是沒權限？還是找不到？）。kernel 的解法很妙：**把錯誤碼（一個小負數）當成指標值回傳**。真實 kernel 實作（引自 `include/linux/err.h`）：

```c
#define MAX_ERRNO	4095
#define IS_ERR_VALUE(x) unlikely((unsigned long)(void *)(x) >= (unsigned long)-MAX_ERRNO)

static __always_inline void * ERR_PTR(long error) { return (void *) error; }
static __always_inline long   PTR_ERR(const void *ptr) { return (long) ptr; }
static __always_inline bool   IS_ERR(const void *ptr) { return IS_ERR_VALUE((unsigned long)ptr); }
```

原理：合法的 kernel 指標永遠不會落在「最高的那 4096 個位址」（`-4095` 到 `-1` 這段，即 `0xFFFFF000` 之後），那段被保留當錯誤碼用。所以：

- `ERR_PTR(-ENOMEM)` 把錯誤碼 `-12` 轉成一個「看起來像指標、其實是 `0xFFFFFFF4`」的值回傳。
- 呼叫端用 `IS_ERR(p)` 檢查：如果 `p` 落在那段保留區（`>= -4095`），就是錯誤。
- 確認是錯誤後，`PTR_ERR(p)` 把它轉回錯誤碼。

**讀碼慣例**：看到函式回傳指標，呼叫端寫的不是 `if (!p)` 而是 `if (IS_ERR(p)) return PTR_ERR(p);`——這就是 ERR_PTR 慣例。它讓「一個回傳值同時攜帶成功指標或失敗原因」，省掉一個 out-parameter。**警訊**：如果某段 code 對 `ERR_PTR` 函式的回傳只檢查 `if (!p)`（NULL 檢查）而不檢查 `IS_ERR`，那個錯誤指標會被當成合法指標解引用 → 幾乎必然 crash 或更糟。這是讀 kernel driver 常見的 bug。

## likely / unlikely：告訴編譯器哪條路熱

kernel 熱路徑上到處是 `if (unlikely(err)) { ... }`、`if (likely(fast_path)) { ... }`。這是**分支預測提示**。定義（引自 `include/linux/compiler.h`）：

```c
# define likely(x)	__builtin_expect(!!(x), 1)
# define unlikely(x)	__builtin_expect(!!(x), 0)
```

`__builtin_expect(cond, 1)` 告訴編譯器「這個條件通常為真」，`, 0` 是「通常為假」。編譯器據此**安排指令佈局**：把預期會走的路徑放在「fall-through」（不跳轉，CPU 預取更順），把不常走的路徑（如錯誤處理）搬到後面去。

真跑驗證這不是空話。這段 code：

```c
int f(int err) {
    if (unlikely(err)) { cold(); return -1; }  // 標記為冷路徑
    hot();                                       // 熱路徑
    return 0;
}
```

`gcc -O2` 後 objdump（真跑輸出）：

```
0000000000000000 <f>:
   4:	sub    $0x8,%rsp
   8:	test   %edi,%edi
   a:	jne    20 <f+0x20>       ← err 為真才跳到 0x20（冷路徑放後面）
   c:	call   11 <f+0x11>       ← 熱路徑 hot() 直接 fall-through
  11:	xor    %eax,%eax         ← return 0
  17:	ret
   ...
  20:	call   25 <f+0x25>       ← 冷路徑 cold() 被搬到函式尾
  25:	mov    $0xffffffff,%eax  ← return -1
  2e:	ret
```

看清楚了：`hot()` + `return 0`（熱路徑）排在前面、直接 fall-through；`cold()` + `return -1`（`unlikely` 標的冷路徑）被搬到 `0x20` 之後。CPU 順序取指時，熱路徑不用跳轉、指令 cache 更緊湊。**這對讀碼的意義：`unlikely(...)` 是作者在告訴你「這個分支是例外情況／錯誤處理，不是主線」。** 讀熱路徑時看到 `if (unlikely(err))` 可以先跳過那個 block、專注主線，因為作者已經明示那是岔路。這是一個很好用的「作者留給讀者的路標」。

## per-cpu 與 RCU（未實測，理論預期）

以下兩個是 kernel 專屬、redis 沒有，我們沒有可執行的 kernel 環境跑，**標為「未實測，理論預期」**，細節與動手請接你的 kernel_internals 課的相應章節。

**per-cpu 變數（未實測，理論預期）**：kernel 為了避免多核心搶同一個變數要加鎖，常給「每個 CPU 一份獨立的副本」。你會看到 `DEFINE_PER_CPU(type, name)` 宣告、`this_cpu_read(name)` / `per_cpu(name, cpu)` 存取。**讀碼翻譯**：看到 per-cpu，心裡想「這個變數其實有 N 份（N = CPU 數），每個核心動自己那份，所以不用鎖」。它常用於統計計數器、快取。要注意的坑是「存取 per-cpu 變數時通常要關搶佔（preemption）」，否則你讀到一半被排程到別的 CPU，就讀錯份了——所以會看到 `get_cpu()`/`put_cpu()` 或 `this_cpu_*` 這種自帶保護的存取器包住。

**RCU 讀端慣例（未實測，理論預期，且本身是難主題）**：RCU（Read-Copy-Update）是 kernel 讓「讀者幾乎零成本、不用鎖」的同步機制，代價是寫者要做複製與延遲釋放。**讀端**的慣例信號是：`rcu_read_lock()` / `rcu_read_unlock()` 包住讀取區、用 `rcu_dereference(ptr)` 讀受 RCU 保護的指標、走訪用 `list_for_each_entry_rcu`。**讀碼翻譯**：看到 `rcu_read_lock()`，想「這段是 RCU 讀者臨界區，我讀到的資料保證在 `rcu_read_unlock()` 前不會被釋放，但可能不是最新版本」。寫端會看到 `rcu_assign_pointer()`（發布新指標）和 `synchronize_rcu()` / `call_rcu()`（等所有讀者離開後才真正釋放舊版本）。RCU 的完整語義涉及 memory ordering（接 Ch 25 的簡化心智模型注意事項）與 grace period，是 kernel 同步裡最難的一塊，這裡只給「讀到它認得出來、知道大方向」的程度。

## 對比與取捨

kernel 慣例 vs 應用層寫法，各自的取捨：

| kernel 慣例 | 解決的問題 | 換來的代價 | 應用層對照 |
|---|---|---|---|
| `container_of` | 純 C 做型別安全向上轉型 | 要懂位址運算才讀得動 | C++ 的繼承 / 成員存取 |
| 侵入式 list | 省配置、省 cache miss、一物多鏈 | 資料結構被鏈表「汙染」、難讀 | 一般 `List<T>`（非侵入） |
| `goto` 清理鏈 | 無 RAII 下的有序資源釋放 | `goto` 容易寫錯漏/多釋放 | C++ RAII / Go defer |
| `ERR_PTR`/`IS_ERR` | 一個回傳值攜帶指標或錯誤碼 | 忘了 `IS_ERR` 就解引用錯誤指標 | 例外 / `Result<T,E>` |
| `likely`/`unlikely` | 引導分支佈局、指令 cache | 標錯反而更慢、增加噪音 | 通常交給 PGO |
| per-cpu | 免鎖的每核狀態 | 要管搶佔、跨 CPU 讀要小心 | thread-local |
| RCU | 讀者零成本免鎖 | 寫端複雜、語義極難 | 讀寫鎖（但慢得多） |

一個總結：**kernel 用「更手動、更貼近硬體、更難讀」換「更省、更快、更可控」。** 這些慣例對應用工程師是負擔，對 kernel 是生存必需。讀它們時別評判「為什麼寫這麼繞」，而是問「它在省什麼、換什麼」。

## 踩雷集錦

1. **錯誤直覺：「`container_of` 是某種指標轉型，看不懂可以跳過」→ 正確：它是所有 kernel 侵入式結構的地基，跳過它你就讀不懂 list/rbtree/work queue 怎麼從節點拿到資料。** 花十分鐘把本章的位址推導範例真跑一次，之後看 kernel 才有底。

2. **錯誤直覺：「kernel 的 `list_head` 裡應該有指向資料的指標」→ 正確：它只有 next/prev，靠 container_of 從自身位址回推資料。** 這是侵入式的核心差異。找不到「node 怎麼連到資料」就是還沒抓到這點。

3. **錯誤直覺：「看到 `goto` 就是壞味道」→ 正確：kernel 的 `goto err` 清理鏈是受控的、單向往下的資源釋放慣例，是 C 沒有 RAII 下最乾淨的解。** 該警惕的不是 `goto` 本身，而是「跳到某標籤釋放的資源對不對」——漏釋放=leak、多釋放=double-free。

4. **錯誤直覺：「回傳指標的函式，檢查 NULL 就夠了」→ 正確：用 `ERR_PTR` 慣例的函式，失敗時回傳的是「藏了錯誤碼的偽指標」，必須用 `IS_ERR` 檢查、`PTR_ERR` 取碼。** 只檢查 `!p` 會把錯誤指標當合法指標解引用。看到函式內部 `return ERR_PTR(...)`，呼叫端就必須 `IS_ERR`。

5. **錯誤直覺：「`likely`/`unlikely` 只是註解，不影響 code」→ 正確：它透過 `__builtin_expect` 真的改變編譯器的指令佈局（本章 objdump 實測：冷路徑被搬到函式尾）。** 它同時也是作者留的路標：`unlikely` 標的通常是錯誤/例外岔路，讀主線時可先略過。

6. **錯誤直覺：「RCU 讀者臨界區裡讀到的資料就是最新的」→ 正確：RCU 保證的是「你讀的舊版本在你讀完前不會被釋放」，不保證是最新版本。** 這是 RCU 用一致性換讀取效能的本質。（未實測，理論預期——細節接 kernel_internals。）

## 進階：再往深一層

- **kernel 的 `list.h` 值得整份精讀**：它是「用巨集在純 C 裡做泛型資料結構」的教科書。除了 `list_for_each_entry`，還有 `_safe` 版本（走訪時可安全刪除當前節點——因為它預先存好 next）、`list_move`、`list_splice`。讀懂這一個 header，kernel 大半資料結構的走訪你都會了。redis 的 `adlist.c` 可以當「非侵入式對照組」一起讀，兩相比較最能體會設計取捨。

- **`offsetof` 的底層**：`container_of` 靠 `offsetof(type, member)` 拿成員偏移。`offsetof` 傳統實作是 `((size_t)&((type*)0)->member)`——「假裝有個 0 位址的物件，取它某成員的位址，那個位址值就是偏移」。這在標準上其實是 UB（對 NULL 解引用），現代編譯器用 `__builtin_offsetof` 取代。讀到這種「拿 0 指標算位址」的老 code，知道它在算偏移即可。

- **`__init` / `__exit` 與 section 放置**：kernel module 常見 `static int __init foo_init(void)`、`static void __exit foo_exit(void)`。`__init` 是個屬性巨集，展開成 `__attribute__((section(".init.text")))`——把這個函式放進特殊的 `.init` section，kernel 啟動完成後**整個 section 的記憶體會被回收**（因為初始化函式只跑一次）。`__exit` 類似，在編譯成 built-in（非 module）時可被丟棄。**讀碼翻譯**：看到 `__init`，想「這是只在啟動/載入時跑一次、之後記憶體會還掉的初始化 code」。這也是為什麼 `__init` 函式不能被非 `__init` 函式呼叫（會存取已釋放的記憶體）——編譯器會警告。（未實測，理論預期。）

- **記憶體屏障與 `READ_ONCE`/`WRITE_ONCE`**：kernel 不信任編譯器對共享變數的優化，用 `READ_ONCE(x)`/`WRITE_ONCE(x, v)`（底層是 `volatile` 轉型）強制「這次讀寫真的落到記憶體、不被暫存器快取、不被重排到別的存取之前」。這跟 Ch 25 的 memory ordering 和 Ch 28 的 volatile 是同一組主題的 kernel 版本。讀 RCU、lockless kernel code 必然撞到，接 kernel_internals 的並發章。

## 動手練習

1. **真跑 container_of 位址推導**：把本章的最小範例編譯執行（`gcc -O0 cof.c -o cof && ./cof`），確認 `lp - offsetof = back = &t`。改變 struct 成員順序（把 `node` 放最前面），看 `offsetof` 變 0、`lp == back`，體會偏移怎麼來的。

2. **侵入式 vs 非侵入式對照**：讀 redis `src/adlist.h`（非侵入，有 `void *value`）和 Linux `include/linux/types.h` 的 `list_head`（侵入，無 value）。用一句話說出「一個 `task` 要同時上兩條鏈」在兩種設計下各怎麼做。

3. **讀一條真實的 goto 清理鏈**：打開 redis `src/ae.c` 的 `aeCreateEventLoop`（開頭幾行有 `goto err`），畫出「哪個失敗點跳到哪、釋放了什麼」。確認它是不是漏釋放或多釋放。

4. **驗證 likely/unlikely 改佈局**：把本章的 `lik.c` 用 `gcc -O2 -c` 編譯，objdump 看冷路徑被搬到後面。然後把 `unlikely` 改成 `likely`（或拿掉），重編，對照佈局變化。

5. **讀 kernel container_of 巨集**：讀 `include/linux/container_of.h` 全文，說明那個 `static_assert(__same_type(...))` 在防什麼、`({ ... })` 為什麼要用 statement expression。跟本章的最小版比，多了什麼、為什麼。

6. **（概念）追一個 ERR_PTR 的用法**：在 `include/linux/err.h` 讀懂 `IS_ERR_VALUE` 的 `>= -MAX_ERRNO` 判斷。想清楚：為什麼合法指標不會落在那段、為什麼 `-12`（`-ENOMEM`）轉成指標後 `IS_ERR` 會回真。

## 本章重點整理

- kernel 慣例不是炫技，是「無 malloc 隨便配、無例外、效能生死線、無 template」這些約束逼出來的解。讀懂它們先問「它在解什麼問題」。
- `container_of(ptr, type, member)`：從成員指標減掉 `offsetof` 回推整個結構。所有侵入式結構的地基（本章真跑：偏移 24，減完回到 `&t`）。
- 侵入式 list（kernel `list_head`）把節點嵌進資料、無 `value` 指標、靠 container_of 拿資料；非侵入式（redis adlist）另配 node 用 `void *value` 指過去。前者省配置省 cache、一物多鏈，代價是難讀。
- `goto err` 清理鏈是 C 版的有序資源釋放：標籤排成反向階梯、fall-through 串起來、對應配置的逆序。讀它問「跳到某標籤釋放對不對」。
- `ERR_PTR`/`IS_ERR`/`PTR_ERR`：把錯誤碼藏進指標回傳值。回傳指標的函式要用 `IS_ERR` 檢查而非 `!p`。
- `likely`/`unlikely` = `__builtin_expect`，真的改分支佈局（本章 objdump 實測冷路徑被搬尾），也是作者標記「哪條是主線、哪條是岔路」的路標。
- per-cpu / RCU / `__init` 是 kernel 專屬（本章標未實測、理論預期），接 kernel_internals 課深挖。

## 自我檢核

- [ ] 我能不能把 `container_of` 的位址推導畫出來，並解釋為什麼一次減法就能從成員回到整個結構？
- [ ] 我能不能說出侵入式 vs 非侵入式鏈表的三個具體差別，以及 kernel 為什麼選侵入式？
- [ ] 給我一段 `goto err_free_b; ... err_free_b: ...; err_free_a: ...` 的清理鏈，我能不能指出每個入口釋放了哪些、有沒有漏/多？
- [ ] 看到一個回傳指標的 kernel 函式，我知道要用 `IS_ERR` 而不是 `!p` 檢查嗎？我能解釋 `ERR_PTR` 的原理嗎？
- [ ] 我能不能解釋 `unlikely(err)` 對編譯器做了什麼、對我讀主線有什麼幫助？
- [ ] 看到 `rcu_read_lock()` / per-cpu 變數，我能不能說出它們的大方向（即使細節要查 kernel_internals）？

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、前提。

- **[Linux `include/linux/list.h` 原始碼](https://github.com/torvalds/linux/blob/master/include/linux/list.h)**
  - **讀哪裡**：從 `struct list_head`、`INIT_LIST_HEAD`、`__list_add`、`list_entry`、`list_for_each_entry` 順著讀，再看 `list_for_each_entry_safe` 為什麼多存一個 next。
  - **學到什麼**：「用巨集 + container_of 在純 C 做泛型侵入式鏈表」的完整教科書實作。讀懂這一個 header，kernel 大半資料結構走訪都通了。
  - **前提**：本章的 container_of；C 巨集與 statement expression（Ch 22）。

- **[Linux Kernel Newbies: "Kernel Glossary" 與 Documentation/core-api](https://www.kernel.org/doc/html/latest/core-api/index.html)**
  - **讀哪裡**：core-api 裡的 "Kernel data structures"（linked lists）、"Error handling"（`ERR_PTR` 家族）、"Concurrency primitives"（RCU 入門）幾節。
  - **學到什麼**：本章各慣例的官方說明與更多變體，尤其 ERR_PTR 家族和 RCU 讀端規則的權威版本。
  - **前提**：本章讀完；RCU 那節較硬，配合 kernel_internals 課讀。

- **《Linux Kernel Development》— Robert Love（3rd ed., Addison-Wesley）**
  - **讀哪裡**：講資料結構的那章（linked lists / kernel data structures）與同步那章（per-cpu、RCU 入門）。
  - **學到什麼**：把本章的慣例放回「kernel 為什麼需要它」的完整脈絡，用平實的講法帶你看真實子系統怎麼用。是 kernel 入門的經典。
  - **前提**：C 熟練；本章當前置暖身，這本補脈絡。

- **[LWN: "What every C programmer should know about undefined behavior" 與 RCU 系列](https://lwn.net/Articles/262464/)**
  - **讀哪裡**：先讀 RCU 的入門文章（LWN 有多篇 "What is RCU?" 系列，由 Paul McKenney 撰寫）。
  - **學到什麼**：RCU 由發明者本人講，是理解「讀者零成本免鎖」機制最權威的來源。承接本章「未實測、理論預期」的 RCU 段落，補上嚴謹版本。
  - **前提**：Ch 25 的並發與 memory ordering 心智模型；有耐心（RCU 本身很難）。

kernel 慣例讀通，你已經備好接手最底層系統程式碼的基本語彙。到這裡我們讀的都還是 source。下一章跨過最後一道界線——當 source「說謊」（UB 被優化、巨集展開後真相不明、只有 binary 沒 source）時，你要能從 source **下沉到組合語言**，用 objdump 和 gdb 看編譯器實際生成了什麼。這也把前面幾章（inline、RAII 的隱形 call、likely 的佈局）的「真相」全部落到指令層驗證。

→ [Ch 28 source ↔ disassembly 對照](./28-source-vs-disassembly.md)
