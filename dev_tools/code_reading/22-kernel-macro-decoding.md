# Ch 22 — kernel 巨集解碼大全：for_each、DEFINE_*、module_init、EXPORT_SYMBOL

> **目標**：遇到 `for_each_possible_cpu(cpu)`、`static DEFINE_PER_CPU(...)`、`fs_initcall(init_pipe_fs);`、`EXPORT_SYMBOL_GPL(foo);`、`min(a, b)`、`BUG_ON(x)` 這些「看起來像函式、其實是巨集」的東西，能讀出它展開後**產生了什麼**（一個迴圈？一個放在特殊 section 的變數？一個編譯期斷言？），並且知道怎麼用 `make fs/pipe.i` 讓編譯器替你展開，用 `nm`/`objdump` 在產物裡找到它。

> **鴻溝**：本章主要練**慣用法鴻溝**。kernel 巨集是慣用法鴻溝最集中的地方——每個字都認得，展開後卻是另一個世界。順帶處理一點**間接鴻溝**：`module_init` 註冊的函式沒有任何呼叫點，是透過 linker section 被呼叫的。

> **環境**：Linux v6.18 原始碼與 `~/code_reading/build`（x86_64 defconfig + kvm_guest，`CONFIG_NR_CPUS=64`）。巨集展開用 `make O=../build fs/pipe.i`；自己的測試程式用 out-of-tree module 的方式（`make -C ~/code_reading/linux O=~/code_reading/build M=$PWD`）對著真正的 kernel header 編譯，所以錯誤訊息都是真的。

## 為什麼需要這個？

[Ch 13](./13-preprocessor-basics.md) 和 [Ch 14](./14-macro-metaprogramming.md) 講過前置處理器的機制。這一章是應用：kernel 有幾十個你**每天都會遇到**的巨集家族，它們有三個共同特性，讓讀碼的人吃足苦頭：

1. **長得像函式或關鍵字**。`for_each_possible_cpu(cpu) { ... }` 看起來像語言內建的迴圈，`DEFINE_MUTEX(lock);` 看起來像函式呼叫，但它其實是一個**變數定義**。
2. **定義藏得很深**。`min()` 背後是五層巨集；`fs_initcall()` 背後是八層，最底下是一段 inline assembly。
3. **效果不在呼叫點**。`EXPORT_SYMBOL(pipe_lock)` 這一行不產生任何程式碼，它在 object file 裡塞一筆資料，最後由 `modpost` 和 linker 處理。`fs_initcall(init_pipe_fs)` 讓 `init_pipe_fs` 在開機時被呼叫，但你 `rg init_pipe_fs` 找不到任何呼叫者。

面對這種巨集，有兩種錯誤做法：一層一層手動展開（費時、容易錯、而且 `#ifdef` 會讓你展開錯的分支），或者忽略它、靠名字猜（猜錯的代價很高）。正確做法是**讓編譯器展開給你看**，再用本章的「巨集語意對照表」理解它。

## 先建立直覺

把 kernel 巨集按「它產生什麼」分成五類，讀碼時先判斷是哪一類：

```
┌─────────────────┬──────────────────────────────┬──────────────────────────┐
│ 類別             │ 例子                          │ 展開後是                  │
├─────────────────┼──────────────────────────────┼──────────────────────────┤
│ 控制結構         │ for_each_possible_cpu         │ for (...; ...; ...)       │
│                 │ list_for_each_entry           │ 迴圈本體由你接上           │
├─────────────────┼──────────────────────────────┼──────────────────────────┤
│ 定義             │ DEFINE_MUTEX / DEFINE_SPINLOCK│ 一個已初始化的變數          │
│                 │ DEFINE_PER_CPU                │ （可能在特殊 section）      │
├─────────────────┼──────────────────────────────┼──────────────────────────┤
│ 登記（registration）│ module_init / fs_initcall │ 在特殊 section 放一筆資料，  │
│                 │ EXPORT_SYMBOL                 │ 由 linker/modpost/開機程式 │
│                 │                               │ 讀取。呼叫點不產生程式碼    │
├─────────────────┼──────────────────────────────┼──────────────────────────┤
│ 編譯期檢查       │ BUILD_BUG_ON / ARRAY_SIZE 的  │ 條件不成立就編譯失敗；       │
│                 │ __must_be_array / min 的型別檢查│ 成立就消失（或變成 0）     │
├─────────────────┼──────────────────────────────┼──────────────────────────┤
│ 執行期斷言       │ WARN_ON / BUG_ON              │ if + 一條陷阱指令 +         │
│                 │                               │ __bug_table 裡的一筆資料    │
└─────────────────┴──────────────────────────────┴──────────────────────────┘
```

「登記」類是最需要轉換心態的：**看到它，不要找呼叫點，要找讀取那個 section 的程式**。

## 方法：讓編譯器替你展開

kernel 的 Kbuild 支援單檔的前置處理目標：

```
$ cd ~/code_reading/linux
$ make O=../build fs/pipe.i
  ...
$ wc -l ../build/fs/pipe.i
81576 ../build/fs/pipe.i
```

`fs/pipe.c` 原本 1553 行，展開後 81576 行——多出來的全是 header。產出的 `.i` 是**用你這份 `.config` 的設定**展開的，所以 `#ifdef CONFIG_xxx` 走的是真正會被編譯的分支，這比手動展開可靠得多。（[Ch 50](./50-compiler-as-reading-tool.md) 會把 `.i`、`.s`、`objdump` 當成一整套讀碼工具來講。）

在 `.i` 裡找東西，用原始碼中獨特的字串去 grep。例如 `pipe_double_lock` 開頭的 `BUG_ON`：

```
$ grep -n "pipe_double_lock" -A6 ../build/fs/pipe.i
```

自己想實驗的巨集，不要改 kernel tree（本課規定不動它），寫一個 out-of-tree module：

```
$ mkdir -p ~/code_reading/scratch/22/mod && cd ~/code_reading/scratch/22/mod
$ echo 'obj-m := demo.o' > Kbuild
$ make -C ~/code_reading/linux O=~/code_reading/build M=$PWD demo.i     # 只展開
$ make -C ~/code_reading/linux O=~/code_reading/build M=$PWD modules    # 真的編譯
```

這樣編譯用的是和 kernel 本體完全相同的 header 與 flags，錯誤訊息和 kernel 開發者看到的一樣。本章所有「故意寫錯」的範例都是這樣跑出來的。

## for_each_* 家族：迴圈本體由你接上

```c
/* include/linux/cpumask.h:1118（v6.18，CONFIG_SMP 分支） */
#define for_each_possible_cpu(cpu) for_each_cpu((cpu), cpu_possible_mask)
```

```c
/* include/linux/cpumask.h:376-377（v6.18） */
#define for_each_cpu(cpu, mask)				\
	for_each_set_bit(cpu, cpumask_bits(mask), small_cpumask_bits)
```

同一個檔案第 1109 行還有一個 UP（單核）版本：`for ((cpu) = 0; (cpu) < 1; (cpu)++)`。哪個生效取決於 `NR_CPUS`——這就是手動展開容易錯的地方。用 module 測試：

```c
/* demo.c（節錄） */
static DEFINE_PER_CPU(unsigned long, demo_hits);

int demo_sum(void)
{
	int cpu;
	unsigned long sum = 0;

	for_each_possible_cpu(cpu)
		sum += per_cpu(demo_hits, cpu);
	...
}
```

`make ... demo.i` 後的真實展開（只保留迴圈頭，`per_cpu` 的展開下一節看）：

```c
 for (((cpu)) = 0; ((cpu)) = find_next_bit((((((const struct cpumask *)&__cpu_possible_mask))->bits)), (((unsigned int)64)), ((cpu))), ((cpu)) < (((unsigned int)64)); ((cpu))++)
```

讀法：

- 這是一個普通的 `for`。條件式裡用逗號運算子先 `find_next_bit` 找下一個 bit 為 1 的 CPU，再比較是否超出範圍——所以**不存在的 CPU 編號會被跳過**，`cpu` 不一定連續。
- `64` 來自 `CONFIG_NR_CPUS=64`（`small_cpumask_bits` 在 `NR_CPUS` 不大於一個 word 時等於 `NR_CPUS`）。
- 括號多到誇張（`((cpu))`），因為每一層巨集都保守地把參數括起來（[Ch 13](./13-preprocessor-basics.md) 講過為什麼）。讀 `.i` 時把多餘的括號在腦中剝掉。

`for_each_*` 家族的共同結構是「巨集提供 `for (...)` 的頭，迴圈本體是你寫在後面的那條敘述」。所以：

- `for_each_xxx(...) { ... }` 和 `for_each_xxx(...) stmt;` 都合法；
- 在本體裡 `break` / `continue` 的行為和普通 `for` 一樣；
- 但**迴圈結束後游標變數的值通常不能用**（例如 `list_for_each_entry` 結束時，游標指向一個由 list head 反推出來的假物件）。這是 [Ch 23](./23-list-head-hlist.md) 的主要踩雷點之一，那裡會把 `list_for_each_entry` 的 `container_of` 展開完整拆一次。

`for_each_possible_cpu` / `for_each_online_cpu` / `for_each_present_cpu` 的差別是領域知識：possible 是開機時決定的「可能存在」的 CPU 集合（包括可熱插拔但目前沒插上的），online 是目前在排程的。per-CPU 資料要對 possible 走訪，因為下線的 CPU 上可能還留著計數。

## DEFINE_* 家族：定義加初始化

看到 `DEFINE_XXX(name)`，它幾乎一定是「定義一個叫 `name` 的變數，並靜態初始化」。同一份 `demo.c`：

```c
static DEFINE_MUTEX(demo_lock);
static DEFINE_SPINLOCK(demo_spin);
static DEFINE_PER_CPU(unsigned long, demo_hits);
static DEFINE_STATIC_KEY_FALSE(demo_key);
```

真實展開：

```c
static struct mutex demo_lock = { .owner = { (0) } , .wait_lock = (raw_spinlock_t) { .raw_lock = { { .val = { (0) } } }, } , .wait_list = { &(demo_lock.wait_list), &(demo_lock.wait_list) } };
static spinlock_t demo_spin = (spinlock_t) { { .rlock = { .raw_lock = { { .val = { (0) } } }, } } };
static __attribute__((section(".data..percpu" ""))) __typeof__(unsigned long) demo_hits;
static struct static_key_false demo_key = (struct static_key_false){ .key = { .enabled = { 0 }, { .type = 0UL } }, };
```

每一行都有讀點：

- **`DEFINE_MUTEX`**（`include/linux/mutex.h:86`）：`.wait_list = { &(demo_lock.wait_list), &(demo_lock.wait_list) }`——一個空的 `list_head` 是「next 和 prev 都指向自己」，所以巨集需要知道變數名稱來取自己的位址。這就是為什麼它必須是 `DEFINE_MUTEX(name)` 而不能是 `struct mutex name = MUTEX_INIT;`。動態配置的 mutex 則要在執行期呼叫 `mutex_init()`（[Ch 19](./19-kernel-memory-allocation.md) 引用的 `alloc_pipe_info` 裡就有 `mutex_init(&pipe->mutex)`）。讀碼時看到一個結構裡的鎖，**找它的初始化點**：靜態的找 `DEFINE_*`，動態的找 `*_init()`。
- **`DEFINE_SPINLOCK`**：這個 build 沒開 lockdep（`CONFIG_PROVE_LOCKING` 未設），所以展開很乾淨。開了 lockdep 後，`DEFINE_*` 系列還會為每把鎖產生一個 `lock_class_key` 與名字字串，這是 lockdep 用來區分鎖類別的依據（[Ch 30](./30-reading-locking-code.md)）。**同一個巨集在不同 config 下展開出完全不同的東西**，這是又一個「用你的 `.config` 展開」的理由。
- **`DEFINE_PER_CPU`**：變數被放進 `.data..percpu` section。開機時 kernel 為每個 CPU 複製一份這個 section，`demo_hits` 這個符號的「位址」其實是一個**偏移量**。所以你不能直接 `demo_hits++`，要用 `this_cpu_inc(demo_hits)` 或 `per_cpu(demo_hits, cpu)`。後者展開成：

```c
(*({ ... (typeof(...)) (__ptr + (((__per_cpu_offset[(cpu)])))); }))
```

  也就是「符號位址 + 該 CPU 的偏移量」。`this_cpu_inc` 在 x86 上則展開成一條帶 `%gs` segment 前綴的 `inc` 指令（展開結果中可以看到 `__seg_gs`），一條指令完成「找到本 CPU 的副本並加一」，天生不怕被搶佔到別的 CPU。[Ch 27](./27-bitmap-percpu-kfifo.md) 會完整讀 per-CPU 變數。
- **`DEFINE_STATIC_KEY_FALSE`**（`include/linux/jump_label.h:385`）：一個 static key。搭配的 `static_branch_unlikely(&demo_key)` 展開成：

```c
if (({ bool branch; if (__builtin_types_compatible_p(typeof(*&demo_key), struct static_key_true)) branch = arch_static_branch_jump(&(&demo_key)->key, false); else if (__builtin_types_compatible_p(typeof(*&demo_key), struct static_key_false)) branch = arch_static_branch(&(&demo_key)->key, false); else branch = ____wrong_branch_error(); __builtin_expect(!!(branch), 0); }))
```

  `__builtin_types_compatible_p`（[Ch 15](./15-kernel-c-gnu-extensions.md)）在編譯期依 key 的型別選分支。`arch_static_branch` 在 x86 上是一段 inline asm，產生一條 **nop**，並在 `__jump_table` section 登記它的位址；之後有人 `static_branch_enable(&demo_key)` 時，kernel 在執行期把那條 nop **改寫成 jmp**。讀碼的意義：`if (static_branch_unlikely(&key))` 包住的程式碼平常**完全沒有分支成本**，所以 tracing、debug 檢查常用它包起來；看到它，就知道那段程式碼預設是關的。

## 登記類一：module_init 與 initcall

這是本章的重頭戲。`fs/pipe.c` 的結尾：

```c
/* fs/pipe.c:1536-1553（v6.18） */
static int __init init_pipe_fs(void)
{
	int err = register_filesystem(&pipe_fs_type);

	if (!err) {
		pipe_mnt = kern_mount(&pipe_fs_type);
		if (IS_ERR(pipe_mnt)) {
			err = PTR_ERR(pipe_mnt);
			unregister_filesystem(&pipe_fs_type);
		}
	}
#ifdef CONFIG_SYSCTL
	register_sysctl_init("fs", fs_pipe_sysctls);
#endif
	return err;
}

fs_initcall(init_pipe_fs);
```

`rg -n 'init_pipe_fs' .` 只會找到這兩處。沒有人呼叫它，它卻會在開機時執行——**間接鴻溝**的一種極端形式：呼叫關係是透過 linker 建立的。

### 一層一層看定義

```c
/* include/linux/init.h:292-310（v6.18，節錄） */
#define pure_initcall(fn)		__define_initcall(fn, 0)

#define core_initcall(fn)		__define_initcall(fn, 1)
#define postcore_initcall(fn)		__define_initcall(fn, 2)
#define arch_initcall(fn)		__define_initcall(fn, 3)
#define subsys_initcall(fn)		__define_initcall(fn, 4)
#define fs_initcall(fn)			__define_initcall(fn, 5)
#define rootfs_initcall(fn)		__define_initcall(fn, rootfs)
#define device_initcall(fn)		__define_initcall(fn, 6)
#define late_initcall(fn)		__define_initcall(fn, 7)

#define __initcall(fn) device_initcall(fn)
```

（每一級還有一個 `_sync` 版本，例如 `fs_initcall_sync` 是 `5s`，在同級之後執行。）

```c
/* include/linux/init.h:254-276（v6.18，節錄） */
#ifdef CONFIG_HAVE_ARCH_PREL32_RELOCATIONS
#define ____define_initcall(fn, __stub, __name, __sec)		\
	__define_initcall_stub(__stub, fn)			\
	asm(".section	\"" __sec "\", \"a\"		\n"	\
	    __stringify(__name) ":			\n"	\
	    ".long	" __stringify(__stub) " - .	\n"	\
	    ".previous					\n");	\
	static_assert(__same_type(initcall_t, &fn));
#else
#define ____define_initcall(fn, __unused, __name, __sec)	\
	static initcall_t __name __used 			\
		__attribute__((__section__(__sec))) = fn;
#endif

#define __unique_initcall(fn, id, __sec, __iid)			\
	____define_initcall(fn,					\
		__initcall_stub(fn, __iid, id),			\
		__initcall_name(initcall, __iid, id),		\
		__initcall_section(__sec, __iid))

#define ___define_initcall(fn, id, __sec)			\
	__unique_initcall(fn, id, __sec, __initcall_id(fn))

#define __define_initcall(fn, id) ___define_initcall(fn, id, .initcall##id)
```

有兩個版本：`#else` 那個比較好懂——定義一個函式指標變數，放進名為 `.initcall5.init` 的 section。x86_64 開了 `CONFIG_HAVE_ARCH_PREL32_RELOCATIONS`（`.config` 第 878 行），走的是上面那個 inline asm 版本：不存 8-byte 的絕對指標，而是存 4-byte 的**相對偏移**（`.long fn - .`），省一半空間，也不需要開機時做 relocation。

### 編譯器展開的結果

不用手動追這八層。`fs/pipe.i` 的最後一行就是答案：

```c
static void * __attribute__((__used__)) __attribute__((__section__(".discard.addressable"))) __UNIQUE_ID___addressable_init_pipe_fs648 = (void *)(uintptr_t)&init_pipe_fs; asm(".section	\"" ".initcall5" ".init" "\", \"a\"		\n" "__initcall__kmod_pipe__647_1553_init_pipe_fs5" ":			\n" ".long	" "init_pipe_fs" " - .	\n" ".previous					\n"); _Static_assert(__builtin_types_compatible_p(typeof(initcall_t), typeof(&init_pipe_fs)), "__same_type(initcall_t, &init_pipe_fs)");;
```

拆成三件事：

1. `__UNIQUE_ID___addressable_init_pipe_fs648`：一個放在 `.discard.addressable` 的變數，取了 `init_pipe_fs` 的位址。目的是讓編譯器認為 `init_pipe_fs` 「有被用到」，不會把這個 `static` 函式當成死碼刪掉（inline asm 裡的引用編譯器看不見）。
2. `asm(".section \".initcall5.init\" ...")`：在 `.initcall5.init` section 放一個標籤 `__initcall__kmod_pipe__647_1553_init_pipe_fs5`，內容是 `.long init_pipe_fs - .`。標籤名稱的格式寫在 `init.h:201` 的註解：`<modname>__<counter>_<line>_<fn>`，加上級別 `5`——`pipe` 是模組名、`647` 是 `__COUNTER__`、`1553` 是 `fs_initcall` 所在的行號。
3. `_Static_assert(...)`：確認 `init_pipe_fs` 的型別是 `initcall_t`（`int (*)(void)`）。寫成 `void init_pipe_fs(void)` 會編譯失敗。

### 在產物裡找到它

```
$ objdump -h ../build/fs/pipe.o | grep -E "initcall|export_symbol|bug_table"
  3 .export_symbol 00000050  0000000000000000  0000000000000000  000028d8  2**3
  4 .initcall5.init 00000004  0000000000000000  0000000000000000  00002928  2**0
 11 __bug_table   00000018  0000000000000000  0000000000000000  00002d7c  2**0

$ nm ../build/vmlinux | grep -E "__initcall5_start|__initcall6_start|init_pipe_fs5"
ffffffff83c913c0 D __initcall5_start
ffffffff83c914e0 D __initcall6_start
ffffffff83c91408 d __initcall__kmod_pipe__647_1553_init_pipe_fs5
```

`pipe.o` 的 `.initcall5.init` 正好 4 bytes——一個 `.long`。在 `vmlinux` 裡，我們展開看到的那個標籤名真的存在，位址落在 `__initcall5_start` 和 `__initcall6_start` 之間。

這兩個 `_start` 符號是 linker script 產生的：

```c
/* include/asm-generic/vmlinux.lds.h:927-944（v6.18） */
#define INIT_CALLS_LEVEL(level)						\
		__initcall##level##_start = .;				\
		KEEP(*(.initcall##level##.init))			\
		KEEP(*(.initcall##level##s.init))			\

#define INIT_CALLS							\
		__initcall_start = .;					\
		KEEP(*(.initcallearly.init))				\
		INIT_CALLS_LEVEL(0)					\
		INIT_CALLS_LEVEL(1)					\
		...
		INIT_CALLS_LEVEL(5)					\
		INIT_CALLS_LEVEL(rootfs)				\
		INIT_CALLS_LEVEL(6)					\
		INIT_CALLS_LEVEL(7)					\
		__initcall_end = .;
```

linker 把所有 object file 的 `.initcall5.init` 和 `.initcall5s.init` 依序接在一起，前面放一個 `__initcall5_start` 標籤。`KEEP` 防止 linker 的 garbage collection 把這些「沒人引用」的 section 丟掉。

最後，讀取這張表的程式：

```c
/* init/main.c:1303-1346（v6.18，節錄） */
static initcall_entry_t *initcall_levels[] __initdata = {
	__initcall0_start,
	__initcall1_start,
	...
	__initcall7_start,
	__initcall_end,
};

/* Keep these in sync with initcalls in include/linux/init.h */
static const char *initcall_level_names[] __initdata = {
	"pure",
	"core",
	...
	"late",
};
...
static void __init do_initcall_level(int level, char *command_line)
{
	initcall_entry_t *fn;
	...
	for (fn = initcall_levels[level]; fn < initcall_levels[level+1]; fn++)
		do_one_initcall(initcall_from_entry(fn));
}
```

整條鏈：

```
fs_initcall(init_pipe_fs)
  └─► .initcall5.init section 裡一筆 {.long init_pipe_fs - .}       [編譯期]
        └─► linker 把所有 .initcall5.init 接成陣列，夾在
            __initcall5_start 和 __initcall6_start 之間             [連結期]
              └─► do_initcalls() 依級別走訪陣列，
                  initcall_from_entry() 把相對偏移轉回函式指標，
                  do_one_initcall() 呼叫它                           [開機期]
```

**讀碼結論**：級別就是順序。pipe 用 `fs_initcall`（5），所以在 `subsys_initcall`（4）之後、`device_initcall`（6，一般 driver 的 `module_init`）之前。如果你在追「為什麼 X 在 Y 之前初始化」，答案常常就在兩邊用了哪個級別的 initcall。開機參數 `initcall_debug` 會讓 kernel 印出每一個 initcall 的呼叫與耗時；**本章沒有在 QEMU 裡實測這個參數**，要驗證可以修改 `~/code_reading/qemu/run-script.sh` 的 `-append` 加上 `initcall_debug`，再 `dmesg | grep init_pipe_fs`。

### `__init`：用完就丟

`init_pipe_fs` 前面的 `__init`：

```c
/* include/linux/init.h:45（v6.18） */
#define __init		__section(".init.text") __cold __latent_entropy	\
```

函式被放進 `.init.text`。開機完成後，kernel 呼叫 `free_initmem()` 把整個 init section 還給 buddy allocator（開機訊息裡的 `Freeing unused kernel image (initmem) memory`）。讀碼的推論：**`__init` 函式開機後就不存在了**，任何在開機後還可能執行的程式碼都不能呼叫它。`modpost` 會檢查這類「section mismatch」並警告。`__initdata`（上面 `initcall_levels` 就有）是資料版本。

### module_init 的兩種身分

```c
/* include/linux/module.h:89（v6.18，#ifndef MODULE 分支） */
#define module_init(x)	__initcall(x);
```

```c
/* include/linux/module.h:131-136（v6.18，#ifdef MODULE 分支） */
#define module_init(initfn)					\
	static inline initcall_t __maybe_unused __inittest(void)		\
	{ return initfn; }					\
	int init_module(void) __copy(initfn)			\
		__attribute__((alias(#initfn)));		\
	___ADDRESSABLE(init_module, __initdata);
```

同一行 `module_init(foo)`：

- **編進 kernel** 時（沒定義 `MODULE`），是 `device_initcall(foo)`，開機時依級別 6 執行；
- **編成 .ko** 時（Kbuild 會加 `-DMODULE`），變成「宣告一個叫 `init_module` 的符號，它是 `foo` 的別名」。`insmod` 載入模組時，module loader 找的就是 `init_module` 這個符號。

用我們的 demo module 驗證，`demo.i` 裡的展開：

```c
static inline __attribute__((__gnu_inline__)) __attribute__((__unused__)) __attribute__((no_instrument_function)) initcall_t __attribute__((__unused__)) __inittest(void) { return demo_init; } int init_module(void) __attribute__((__copy__(demo_init))) __attribute__((alias("demo_init"))); static void * __attribute__((__used__)) __attribute__((__section__(".init.data"))) __UNIQUE_ID___addressable_init_module367 = (void *)(uintptr_t)&init_module;;;
```

`__inittest` 是一個型別檢查的小技巧：它回傳 `initfn`，如果 `initfn` 的型別不是 `initcall_t`，這個函式就編譯不過。它永遠不會被呼叫。

同理，`module.h:113-125` 那一串 `#define core_initcall(fn) module_init(fn)` 說明：**編成模組時，所有級別都退化成 `module_init`**，因為模組是在開機之後才載入，級別沒有意義。

## 登記類二：EXPORT_SYMBOL

```c
/* fs/pipe.c:88-93（v6.18） */
void pipe_lock(struct pipe_inode_info *pipe)
{
	if (pipe->files)
		mutex_lock(&pipe->mutex);
}
EXPORT_SYMBOL(pipe_lock);
```

```c
/* include/linux/export.h:75-92（v6.18，節錄） */
#define __EXPORT_SYMBOL(sym, license, ns)			\
	extern typeof(sym) sym;					\
	__ADDRESSABLE(sym)					\
	__GENDWARFKSYMS_EXPORT(sym)				\
	asm(__stringify(___EXPORT_SYMBOL(sym, license, ns)))
...
#define EXPORT_SYMBOL(sym)		_EXPORT_SYMBOL(sym, "")
#define EXPORT_SYMBOL_GPL(sym)		_EXPORT_SYMBOL(sym, "GPL")
#define EXPORT_SYMBOL_NS(sym, ns)	__EXPORT_SYMBOL(sym, "", ns)
#define EXPORT_SYMBOL_NS_GPL(sym, ns)	__EXPORT_SYMBOL(sym, "GPL", ns)
```

`fs/pipe.i` 裡的展開：

```c
extern typeof(pipe_lock) pipe_lock; static void * __attribute__((__used__)) __attribute__((__section__(".discard.addressable"))) __UNIQUE_ID___addressable_pipe_lock629 = (void *)(uintptr_t)&pipe_lock; asm(".section \".export_symbol\",\"a\" ; __export_symbol_pipe_lock: ; .asciz \"\" ; .ascii \"\" \"\\0\" ; .balign 8 ; .quad pipe_lock ; .previous");
```

和 initcall 是同一個套路：在 `.export_symbol` section 放一筆資料，內容是**授權字串**（`.asciz ""`，`EXPORT_SYMBOL_GPL` 的話是 `"GPL"`）、**namespace 字串**、和**符號位址**。我們的 demo module 用 `EXPORT_SYMBOL_GPL(demo_sum)`，展開裡就是 `.asciz \"GPL\"`。

`pipe.o` 的 `.export_symbol` 是 0x50 = 80 bytes，`fs/pipe.c` 有 5 個 `EXPORT_SYMBOL`，每筆 16 bytes（兩個字串各 1 byte、對齊到 8、加 8 bytes 位址）。接下來 `modpost` 讀這個 section，產生真正的 `__ksymtab_*` 項目：

```
$ nm ../build/vmlinux | grep -E " (__ksymtab_pipe_lock|__kstrtab_pipe_lock)$"
ffffffff82b5103f r __kstrtab_pipe_lock
ffffffff82b2e898 r __ksymtab_pipe_lock
```

這個「`.export_symbol` section + modpost 產生 ksymtab」的流程是 v6.5 才有的（Masahiro Yamada，commit `ddb5cdbafaaa kbuild: generate KSYMTAB entries by modpost`）。讀舊版 kernel 或舊文章時，你會看到 `EXPORT_SYMBOL` 直接在 C 裡定義 `__ksymtab_` 變數——機制變了，語意沒變。

**讀碼時 `EXPORT_SYMBOL` 告訴你的事**：

| 你看到 | 讀出 |
|---|---|
| 沒有 export | 只在 kernel 本體內使用；模組不能呼叫。改它的簽名只要改 tree 內的呼叫者 |
| `EXPORT_SYMBOL` | 任何模組都可以用，包括非 GPL 模組。它是對外 API 的一部分 |
| `EXPORT_SYMBOL_GPL` | 只有宣告 GPL 相容授權（`MODULE_LICENSE("GPL")` 等）的模組能用。通常是「深入內部實作」的介面 |
| `EXPORT_SYMBOL_NS(_GPL)` | 模組必須 `MODULE_IMPORT_NS(ns)` 才能用。用來把某組符號限定給特定子系統 |

在 [Ch 20](./20-refcount-and-lifetime.md) 讀 `fs/file_table.c` 時，`fput` 是 `EXPORT_SYMBOL`、`flush_delayed_fput` 是 `EXPORT_SYMBOL_GPL`——前者是每個 driver 都會用的基本操作，後者是「請不要隨便叫」的內部機制。

## 編譯期檢查：BUILD_BUG_ON、ARRAY_SIZE、min

這一類巨集在條件成立時**什麼都不產生**，條件不成立時讓編譯失敗。讀碼時它們是**作者寫下的不變式**——比註解更可靠，因為編譯器會檢查。

### BUILD_BUG_ON

```c
/* include/linux/build_bug.h:39-50（v6.18，節錄） */
#define BUILD_BUG_ON_MSG(cond, msg) compiletime_assert(!(cond), msg)
...
#define BUILD_BUG_ON(condition) \
	BUILD_BUG_ON_MSG(condition, "BUILD_BUG_ON failed: " #condition)
```

```c
/* include/linux/compiler_types.h:573-583, 601-602（v6.18，節錄） */
# define __compiletime_assert(condition, msg, prefix, suffix)		\
	do {								\
		...
		__noreturn extern void prefix ## suffix(void)		\
			__compiletime_error(msg);			\
		if (!(condition))					\
			prefix ## suffix();				\
	} while (0)
...
#define compiletime_assert(condition, msg) \
	_compiletime_assert(condition, msg, __compiletime_assert_, __COUNTER__)
```

技巧很巧妙：宣告一個帶 `__attribute__((error(msg)))` 的外部函式（名字用 `__COUNTER__` 保證唯一），然後「如果條件不成立就呼叫它」。條件是編譯期常數時，編譯器的最佳化會把成立的分支連同呼叫一起刪掉；不成立的話呼叫留下來，`error` attribute 觸發編譯錯誤。

故意寫錯：

```c
/* bad3.c */
#include <linux/module.h>
#include <linux/build_bug.h>

struct hdr { u32 magic; u16 ver; u16 flags; };

static int __init bad3_init(void)
{
	BUILD_BUG_ON(sizeof(struct hdr) != 12);  /* 實際是 8 */
	return 0;
}
module_init(bad3_init);
MODULE_LICENSE("GPL");
```

```
$ make -C ~/code_reading/linux O=~/code_reading/build M=$PWD modules 2>&1 | grep error
/home/ypp/code_reading/linux/include/linux/compiler_types.h:602:45: error: call to ‘__compiletime_assert_366’ declared with attribute error: BUILD_BUG_ON failed: sizeof(struct hdr) != 12
```

錯誤訊息裡有 `__compiletime_assert_366`（366 就是 `__COUNTER__`）和原始條件的字串（`#condition`）。在 kernel 裡看到 `BUILD_BUG_ON(sizeof(struct foo) != 64)` 這類斷言，你就知道**某處依賴這個大小**（可能是 cache line、可能是 ABI、可能是 union 的對齊），改 struct 前要找出那個依賴。

因為依賴最佳化刪除死碼，`BUILD_BUG_ON` 的條件必須是編譯器能算出來的常數。實務上也要開最佳化編譯（kernel 一律 `-O2`）。

### ARRAY_SIZE 與陣列檢查

```c
/* include/linux/array_size.h:11（v6.18） */
#define ARRAY_SIZE(arr) (sizeof(arr) / sizeof((arr)[0]) + __must_be_array(arr))
```

```c
/* include/linux/compiler.h:197-203（v6.18） */
#define __BUILD_BUG_ON_ZERO_MSG(e, msg, ...) ((int)sizeof(struct {_Static_assert(!(e), msg);}))
...
#define __is_array(a)		(!__same_type((a), &(a)[0]))
#define __must_be_array(a)	__BUILD_BUG_ON_ZERO_MSG(!__is_array(a), \
							"must be array")
```

C 的經典陷阱：陣列傳進函式會退化成指標，`sizeof(arr) / sizeof(arr[0])` 會變成「指標大小 / 元素大小」，安靜地算出錯的數字。kernel 的 `ARRAY_SIZE` 加上 `__must_be_array`：

- `__is_array(a)`：如果 `a` 是陣列，`a` 和 `&a[0]` 型別不同（`int[8]` vs `int *`）；如果 `a` 已經是指標，兩者型別相同。
- `__BUILD_BUG_ON_ZERO_MSG`：在一個匿名 struct 裡放 `_Static_assert`，再取這個 struct 的 `sizeof`——斷言通過時這個空 struct 的大小是 0（GNU C 擴充），所以加到 `ARRAY_SIZE` 上不改變結果。

`fs/pipe.i` 裡 `anon_pipe_get_page` 的迴圈就展開成這樣：

```c
 for (int i = 0; i < (sizeof(pipe->tmp_page) / sizeof((pipe->tmp_page)[0]) + ((int)sizeof(struct {_Static_assert(!(!(!__builtin_types_compatible_p(typeof((pipe->tmp_page)), typeof(&(pipe->tmp_page)[0])))), "must be array");}))); i++) {
```

故意寫錯：

```c
/* bad.c（節錄） */
static int count_entries(int *tbl)          /* 陣列退化成指標 */
{
	return ARRAY_SIZE(tbl);
}
```

```
/home/ypp/code_reading/linux/include/linux/array_size.h:11:38: error: division ‘sizeof (int *) / sizeof (int)’ does not compute the number of array elements [-Werror=sizeof-pointer-div]
bad.c:10:16: note: in expansion of macro ‘ARRAY_SIZE’
/home/ypp/code_reading/linux/include/linux/compiler.h:197:67: error: static assertion failed: "must be array"
bad.c:10:16: note: in expansion of macro ‘ARRAY_SIZE’
```

兩道防線同時觸發：gcc 自己的 `-Wsizeof-pointer-div`（kernel 用 `-Werror` 把它升級成錯誤），和 kernel 的 `"must be array"` 斷言。

### min / max / clamp：型別安全

教科書的 `#define MIN(a, b) ((a) < (b) ? (a) : (b))` 有兩個問題：參數會被求值兩次（`MIN(i++, j)` 出事），以及 signed/unsigned 混用時的隱式轉換（[Ch 9](./09-expressions-integers-bits.md)：`-1 < 10u` 是 false）。kernel 的版本：

```c
/* include/linux/minmax.h:83-105（v6.18，節錄） */
#define __cmp(op, x, y)	((x) __cmp_op_##op (y) ? (x) : (y))
...
#define __careful_cmp_once(op, x, y, ux, uy) ({		\
	__auto_type ux = (x); __auto_type uy = (y);	\
	BUILD_BUG_ON_MSG(!__types_ok(ux, uy),		\
		#op"("#x", "#y") signedness error");	\
	__cmp(op, ux, uy); })

#define __careful_cmp(op, x, y) \
	__careful_cmp_once(op, x, y, __UNIQUE_ID(x_), __UNIQUE_ID(y_))

#define min(x, y)	__careful_cmp(min, x, y)
```

逐片：

- `({ ... })` + `__auto_type ux = (x)`：每個參數只求值一次，存進暫存變數（[Ch 15](./15-kernel-c-gnu-extensions.md) 的 statement expression 與 `__auto_type`）。
- `__UNIQUE_ID(x_)`：暫存變數名用 `__COUNTER__` 產生，避免 `min(x_, y)` 這種名字衝突，也讓巢狀的 `min(min(a, b), c)` 不會遮蔽。
- `#define __cmp_op_min <`：用 token pasting 從 `min` 選出運算子（[Ch 14](./14-macro-metaprogramming.md)）。
- `BUILD_BUG_ON_MSG(!__types_ok(ux, uy), ...)`：signedness 檢查。`__types_ok` 依賴 `__sign_use`（`minmax.h:49`）：signed 型別如果**編譯期可證明非負**（例如常數 `42`），也被接受和 unsigned 比較。

故意混用：

```c
/* bad2.c */
#include <linux/module.h>
#include <linux/minmax.h>

static noinline int pick(int want, unsigned int len)
{
	return min(want, len);                  /* signed vs unsigned */
}
static int __init bad2_init(void) { return pick(-1, 10); }
module_init(bad2_init);
MODULE_LICENSE("GPL");
```

```
/home/ypp/code_reading/linux/include/linux/compiler_types.h:602:45: error: call to ‘__compiletime_assert_368’ declared with attribute error: min(want, len) signedness error
```

讀碼時的對應規則：

- 看到 `min(a, b)` → 兩邊型別相容，可以放心；
- 看到 `min_t(type, a, b)` → 作者**刻意**把兩邊轉成 `type` 再比（`minmax.h:161`，用 `__cmp_once` 不做 signedness 檢查）。這是一個值得停下來的地方：轉型會不會截斷？負數轉 unsigned 會不會變成巨大值？
- `clamp(val, lo, hi)`（`minmax.h:206`）另外檢查 `lo > hi`（「clamp() low limit … greater than high limit」）。

## 執行期斷言：WARN_ON 與 BUG_ON

```c
/* include/asm-generic/bug.h:71（v6.18） */
#define BUG_ON(condition) do { if (unlikely(condition)) BUG(); } while (0)
```

```c
/* include/asm-generic/bug.h:122-127（v6.18） */
#define WARN_ON(condition) ({						\
	int __ret_warn_on = !!(condition);				\
	if (unlikely(__ret_warn_on))					\
		__WARN();						\
	unlikely(__ret_warn_on);					\
})
```

（x86 有自己的 `__WARN_FLAGS` 實作，但結構相同。）注意 `WARN_ON` 是 statement expression，**有回傳值**：條件本身。所以 kernel 裡常見 `if (WARN_ON(x)) return -EINVAL;`——警告並恢復。`BUG_ON` 是 `do { } while (0)`，沒有值，因為 `BUG()` 不會返回。

`fs/pipe.c:105` 的 `BUG_ON(pipe1 == pipe2)` 在 x86 上展開成（`fs/pipe.i`，節錄）：

```c
do { if (__builtin_expect(!!(pipe1 == pipe2), 0)) do { ... asm __inline volatile("1:\t" " " ".byte 0x0f, 0x0b ;" " " "\n" ".pushsection __bug_table,\"aw\"\n" "2:\t" ".long " "1b" " - ." "\t# bug_entry::bug_addr\n" "\t" ".long " "%c0" " - ." "\t# bug_entry::file\n" "\t.word " "%c1" "\t# bug_entry::line\n" ... ".popsection\n" "" : : "i" ("fs/pipe.c"), "i" (105), "i" (0), "i" (sizeof(struct bug_entry))); } while (0); __builtin_unreachable(); } while (0); } while (0);
```

又是登記類的套路：

- `.byte 0x0f, 0x0b` 是 x86 的 `ud2` 指令（未定義指令，執行就觸發 invalid opcode 例外）。
- `__bug_table` section 裡放一筆 `bug_entry`：陷阱指令的位址、檔名 `"fs/pipe.c"`、行號 `105`、旗標。
- 例外處理程式拿陷阱位址去 `__bug_table` 查，就能印出 `kernel BUG at fs/pipe.c:105!`。

`pipe.o` 的 `__bug_table` section（上面 `objdump -h` 的輸出）就是這些項目。這個設計讓熱路徑上只多一個條件跳躍和一條兩 byte 的指令，檔名、行號這些資料都放在不會被載入 cache 的別處。

讀碼時的態度差異寫在 coding style 裡：

> Do not add new code that uses any of the BUG() variants, such as BUG(), BUG_ON(), or VM_BUG_ON(). Instead, use a WARN*() variant, preferably WARN_ON_ONCE(), and possibly with recovery code.
>
> —— Documentation/process/coding-style.rst:1224-1227（v6.18）

`include/asm-generic/bug.h:79-84` 的註解還加了一條：WARN 不能用來檢查**外部輸入**（syscall 參數、網路封包、裝置資料）或 ENOMEM 這類暫時狀況。所以讀到一個 `WARN_ON`，正確的解讀是「作者認為這**絕對不該發生**，發生就是 kernel 自己的 bug」。反過來，如果你發現某個 `WARN_ON` 的條件可以由 user-space 觸發，那就是一個真 bug（syzbot 回報的大量問題就是這類），而且在開了 `panic_on_warn` 的系統上是 DoS。

`pipe_double_lock` 的 `BUG_ON(pipe1 == pipe2)` 是舊程式碼；照現在的規範大概會寫成 `WARN_ON_ONCE` 加恢復路徑。讀到 `BUG_ON` 時，可以問：這個條件真的「無法繼續」嗎？

## SYSCALL_DEFINEn：預告

最後一個你一定會遇到的巨集：

```c
/* fs/pipe.c:1054（v6.18） */
SYSCALL_DEFINE2(pipe2, int __user *, fildes, int, flags)
```

型別和參數名用逗號分開成**兩個巨集參數**，這樣巨集才能分別處理型別（例如把每個參數先擴展成 `long`）和名字。`fs/pipe.i` 裡它展開成好幾個函式（節錄）：

```c
long __x64_sys_pipe2(const struct pt_regs *regs) { return __se_sys_pipe2(regs->di, regs->si); }
long __ia32_sys_pipe2(const struct pt_regs *regs) { return __se_sys_pipe2(...(regs->bx), ...(regs->cx)); }
static long __se_sys_pipe2(... fildes, ... flags) { long ret = __do_sys_pipe2(( int *) fildes, ...
```

`__x64_sys_pipe2` 從暫存器 `rdi`、`rsi` 取參數（x86_64 syscall ABI），`__ia32_sys_pipe2` 從 `ebx`、`ecx` 取（32-bit 相容模式），兩者都呼叫 `__se_sys_pipe2`（sign-extend 包裝），最後才進到你寫的本體 `__do_sys_pipe2`。這就是為什麼你在 stack trace 裡看到的是 `__x64_sys_close` 而不是 `sys_close`（[Ch 20](./20-refcount-and-lifetime.md) 的 ftrace 輸出）。完整的 syscall 入口路徑是 [Ch 29](./29-syscall-entry-paths.md) 的主題。

## 對比與取捨

| 讀巨集的方法 | 優點 | 缺點 | 何時用 |
|---|---|---|---|
| 靠名字猜 | 最快 | 猜錯代價高，尤其是登記類 | 已經熟悉的家族 |
| 手動逐層展開 | 理解每一層的用意 | 慢；容易走錯 `#ifdef` 分支 | 想學巨集技巧時 |
| `make foo.i` | 用真實 config 展開，絕對正確 | 輸出巨大、括號多、沒有格式 | 想知道「實際產生了什麼」 |
| clangd hover / 展開 | 在編輯器裡就地看 | 需要 `compile_commands.json` | 日常讀碼（[Ch 49](./49-clangd-lsp.md)） |
| elixir.bootlin.com | 點擊跳定義 | 不懂你的 config | 快速查定義在哪 |
| `objdump` / `nm` | 看到最終產物 | 只看得到登記類的結果 | 驗證 section、符號是否真的存在 |

## 踩雷集錦

1. **「`DEFINE_MUTEX(x);` 是函式呼叫」**：它是變數定義。所以它可以出現在檔案層級，而且前面可以加 `static`。在函式內部用它定義的是區域變數，生命週期隨函式結束——幾乎一定是 bug。
2. **「找不到呼叫者，所以這個函式沒用」**：登記類巨集（`module_init`、`*_initcall`、`late_initcall`、`EXPORT_SYMBOL`、`__setup`、`early_param`）註冊的函式都沒有直接呼叫點。刪之前先看它下面有沒有一行登記巨集。ops table 的 callback 也是一樣（[Ch 28](./28-ops-tables-indirect-calls.md)）。
3. **「`min_t` 比 `min` 好，因為更明確」**：`min_t` 繞過 signedness 檢查，是在告訴編譯器「我知道我在做什麼」。它有時是必要的，但每一個 `min_t` 都該讀一下轉型有沒有截斷或符號問題。
4. **「`BUG_ON` 是比較嚴格的 `WARN_ON`」**：`BUG_ON` 會殺掉當前 task（在某些設定下 panic 整台機器），而且可能留下拿著的鎖。新程式碼被要求用 `WARN_ON_ONCE` 加恢復路徑。
5. **「巨集在每個 config 下展開都一樣」**：`DEFINE_SPINLOCK` 在有沒有 lockdep 時、`for_each_possible_cpu` 在 SMP/UP 時、`module_init` 在 built-in/module 時展開完全不同。永遠用你在乎的那個 config 展開。
6. **「`ARRAY_SIZE` 對指標會算錯」**：kernel 的版本不會算錯，它會**編譯失敗**。如果你在 user-space 專案裡看到自製的 `ARRAY_SIZE` 沒有 `__must_be_array`，那才會安靜地算錯。

## 進階：再往深一層

- **其他登記類巨集**：`__setup("foo=", handler)` 與 `early_param` 註冊開機參數處理函式（放在 `.init.setup` section）；`core_param` / `module_param` 註冊參數；`DEFINE_STATIC_CALL` 是函式指標版本的 static key。它們都遵循「放進 section → 由某個走訪迴圈讀取」的模式，認出模式後，去 `vmlinux.lds.h` 找 section 名，再 `rg` 那個 `_start` 符號就能找到讀取端。
- **`__ADDRESSABLE` 的用意**：initcall 和 EXPORT_SYMBOL 的展開都有一個 `.discard.addressable` 變數。它取函式位址讓編譯器保留函式，而 `.discard.*` section 會在最後連結時被丟掉，不占空間。這是 kernel 和 LTO（link-time optimization）周旋的手段之一，`init.h:217` 起的註解說明了 `CONFIG_LTO_CLANG` 下還要再多一層 stub。
- **`__COUNTER__` 與 `__UNIQUE_ID`**：每次展開遞增的計數器，讓巨集產生唯一名字。`initcall`、`min`、`compiletime_assert` 都靠它。在 `.i` 裡看到 `__UNIQUE_ID_xxx123` 這類名字，就知道是巨集產生的暫存名，不用去 grep 它的定義。
- **用 `pahole` 和 `nm` 驗證巨集的效果**：`BUILD_BUG_ON(sizeof(...))` 的依賴可以用 `pahole -C struct_name vmlinux` 看實際 layout；登記類巨集可以用 `nm vmlinux | grep __initcall` 列出某一級的所有 initcall，那就是開機時的實際順序。

## 動手練習

1. `nm ~/code_reading/build/vmlinux | grep '__initcall__kmod_.*5$' | sort | head -30` 列出 fs 級別的 initcall，挑兩個不認識的，找到它們的原始碼。這份清單的順序（照位址排序）就是它們的執行順序——用 `sort` 而不是 `sort -k3` 是故意的，想想為什麼。
2. 在 demo module 裡試 `min(42, len)`（`len` 是 `unsigned int`），會不會報 signedness error？對照 `__sign_use` 的註解解釋結果。再試 `min(-1, len)`。
3. 在 `fs/` 裡找三個 `EXPORT_SYMBOL_GPL`，各自說明為什麼作者選擇 GPL-only（看這個函式碰到了多深的內部結構）。
4. 把 demo module 裡的 `DEFINE_SPINLOCK` 換成在 `.config` 開了 `CONFIG_DEBUG_SPINLOCK` 的 build 下會怎麼展開——**不要重建 kernel**，改用 `rg -n '__SPIN_LOCK_UNLOCKED|SPIN_DEBUG_INIT' include/linux/spinlock_types*.h` 手動推導，然後說明為什麼本章說「手動展開容易走錯分支」。

## 本章重點整理

- kernel 巨集按產物分五類：控制結構、定義、登記、編譯期檢查、執行期斷言；先判斷類別再讀。
- 用 `make O=../build foo.i`（tree 內）或 out-of-tree module 的 `M=` 方式展開，結果依你的 `.config`，比手動展開可靠。
- 登記類巨集（initcall、`EXPORT_SYMBOL`、`BUG_ON` 的 `__bug_table`）在特殊 section 放資料，由 linker script 與走訪程式讀取；它們註冊的函式沒有呼叫點。
- `BUILD_BUG_ON`、`ARRAY_SIZE`、`min` 是作者寫下的編譯期不變式；`min_t` 是刻意繞過檢查的訊號。
- `WARN_ON` 有回傳值、可恢復，`BUG_ON` 不返回；新程式碼應使用 `WARN_ON_ONCE`，且 WARN 條件不該能被 user 觸發。

## 自我檢核

- [ ] 不看書，描述 `fs_initcall(init_pipe_fs)` 從編譯期到開機期的完整路徑，說出每一階段由誰（編譯器、linker、哪個函式）處理。
- [ ] 解釋同一行 `module_init(foo)` 在 built-in 與 `.ko` 下各自展開成什麼，以及為什麼模組不需要 initcall 級別。
- [ ] `ARRAY_SIZE` 如何在不改變結果的情況下加入「必須是陣列」的檢查？那個「大小為 0 的 struct」依賴什麼擴充？
- [ ] 有人問你「`min(a, b)` 和 `min_t(int, a, b)` 哪個好？」你會怎麼回答，並舉一個 `min_t` 會出錯的例子？
- [ ] 你在一段 driver 程式碼裡發現 `WARN_ON(len > MAX)`，`len` 來自 ioctl 參數。這有什麼問題？在 `panic_on_warn=1` 的系統上會怎樣？

## 延伸閱讀

### 官方文件

- **[Linux kernel coding style](https://docs.kernel.org/process/coding-style.html)**（tree 內：`Documentation/process/coding-style.rst`）
  - **讀哪裡**：第 12 節「Macros, Enums and RTL」（巨集寫法的規範，解釋為什麼多敘述巨集用 `do { } while (0)`）、第 18 節「Don't re-invent the kernel macros」（點名 `ARRAY_SIZE` 等應直接使用的巨集）、第 22 節「Do not crash the kernel」（`BUG` vs `WARN` 的完整規範，本章引用的段落）。
  - **和本章的關聯**：本章讀到的巨集風格（括號、`do-while`、`WARN_ON_ONCE`）的規範出處。
- **`include/linux/init.h` 開頭與 initcall 段落的註解**（v6.18 tree 內）
  - **讀哪裡**：第 1–60 行（`__init`、`__initdata` 系列的說明）與第 180–310 行（initcall 機制）。
  - **為什麼值得讀**：initcall 的格式、LTO 的特殊處理、PREL32 的動機都寫在註解裡，比任何二手資料新。
- **[Static Keys](https://docs.kernel.org/staging/static-keys.html)**（tree 內：`Documentation/staging/static-keys.rst`）
  - **讀哪裡**：「Motivation」與「Solution」兩節。
  - **和本章的關聯**：`DEFINE_STATIC_KEY_FALSE` 與 `static_branch_unlikely` 展開成 nop 的原理。

### 書籍

- **《Linkers and Loaders》** — John R. Levine（Morgan Kaufmann, 1999）
  - **讀哪幾章**：第 3 章（object file 與 section）、第 4 章（storage allocation，section 如何被 linker 合併）。
  - **這本書的定位**：讀完你就理解「登記類巨集」背後的機制——自訂 section 加 linker script 的 `_start`/`_end` 符號。書很老，ELF 的部分仍然適用。
- **《Linux Kernel Development》（3rd ed.）** — Robert Love（Addison-Wesley, 2010）
  - **讀哪幾章**：第 17 章「Devices and Modules」中 module_init / EXPORT_SYMBOL 的部分。概念正確，實作細節（例如 ksymtab 的產生方式）已經改了，對照本章的 v6.18 展開讀。

### 部落格 / 技術文章

- **LWN.net 的 "Kernel development" 每週專欄中關於 `minmax.h` 重寫的報導**（2024 年前後，`min()`/`max()` 因為展開過大拖慢編譯而被重寫）
  - **這篇說什麼**：kernel 的 `min()` 曾經展開到數十 KB、讓某些檔案的編譯時間暴增，Linus Torvalds 等人如何重寫它。在 [LWN Kernel Index](https://lwn.net/Kernel/Index/) 搜尋 "minmax" 可以找到。
  - **為什麼值得讀**：一個「巨集元程式的代價」的真實案例，和本章 `min` 的五層展開互相印證。具體文章標題請以 LWN 搜尋結果為準。

Part 3 的 kernel C 方言到這裡講完了：GNU 擴充、`container_of`、錯誤慣例、標註、配置、refcount、barrier、巨集。接下來的練習把它們全部拿來用，逐行解碼十段真實的 kernel 程式碼。

→ [練習 C：逐行解碼 10 段 kernel 天書](./practice-c-decode-kernel-snippets.md)
