# Ch 18 — 編譯器標註與 sparse：__user、__rcu、__must_check、likely

> **目標**：讀到函式簽名或 struct 欄位上的 `__user`、`__iomem`、`__rcu`、`__percpu`、`__force`、`__bitwise`、`__must_check`、`__init`、`likely`、`__cold`、`__acquires` 等標註時，能說出「它告訴讀者什麼資訊、誰會檢查它、正常編譯時它變成什麼」；能自己從原始碼裝 sparse，用它檢查一個最小範例和真實的 kernel 檔案，並讀懂它的警告。
> **鴻溝**：本章主要練**慣用法鴻溝**（標註是 pattern），附帶**領域鴻溝**（`__user`、`__rcu`、`__iomem` 各自背後是一整塊 kernel 知識，本章給最小必要的背景）。

> **環境**：Linux kernel v6.18 原始碼與 x86_64 build（`~/code_reading/build`，gcc 11.4 編譯，x86_64_defconfig 加 kvm_guest 與 tracing 選項）；sparse 從 git 原始碼裝到 `~/.local`（`v0.6.5-rc1-21-g84ec065`）；QEMU（TCG 模式）跑 v6.18 bzImage；clang 14。

## 為什麼需要這個？

看這個函式簽名：

```c
/* fs/read_write.c:704（v6.18） */
ssize_t ksys_read(unsigned int fd, char __user *buf, size_t count)
```

把 `__user` 拿掉，它就是一個普通的 `char *buf`。事實上，**gcc 正常編譯 kernel 時真的就把它拿掉了**——用 `make O=../build fs/read_write.i` 產生前置處理結果：

```
$ grep -n "ksys_read(unsigned int fd" ~/code_reading/build/fs/read_write.i
79495:ssize_t ksys_read(unsigned int fd, char *buf, size_t count);
81823:ssize_t ksys_read(unsigned int fd, char *buf, size_t count)
```

那它存在的意義是什麼？`__user` 告訴你一件**極其重要**的事：`buf` 這個位址是 user space 給的，kernel **不能**直接 `*buf` 去讀寫它——它可能沒有對應的記憶體、可能指向 kernel 自己的資料（攻擊者故意的）、可能在你讀到一半時被另一個 thread `munmap` 掉。必須透過 `copy_to_user()` 這類函式存取，它們會檢查位址範圍並處理 page fault。

這種「型別系統表達不了、但對正確性至關重要」的資訊，kernel 用**標註（annotation）**記錄下來。標註有三種用途：

1. **給人讀**：函式簽名多一個字，就省下讀者翻半天程式碼確認「這個指標從哪來」。
2. **給靜態檢查器讀**：sparse 會把 `__user` 當成一個獨立的「位址空間」，直接解參考或混進普通指標就警告。
3. **給編譯器讀**：`__must_check`、`likely`、`__cold`、`__init` 會真的改變產生的程式碼或警告。

讀碼時，**標註是作者留給你的最高密度資訊**。很多人第一次讀 kernel 會把它們當雜訊跳過，這是錯的方向。

## 先建立直覺

把指標想成「有顏色的鑰匙」：

```
  普通指標（kernel 位址）    ── 白色鑰匙：直接開門（*p）
  __user  指標                ── 紅色鑰匙：只能交給 copy_from_user/get_user 這位門房
  __iomem 指標                ── 黃色鑰匙：只能交給 readl/writel（MMIO 存取函式）
  __percpu 指標               ── 藍色鑰匙：要先用 per_cpu_ptr/this_cpu_* 換成白色鑰匙
  __rcu   指標                ── 綠色鑰匙：要用 rcu_dereference 在 RCU 保護下換成白色鑰匙
  __force                     ── 「我知道我在做什麼」的換色許可，明寫在轉型裡
```

在 C 編譯器眼中，所有鑰匙都是同一種顏色（全部是 `T *`）。sparse 看得見顏色，混用就報警。

其他標註則是「便條紙」，貼在函式或變數上：

```
  __must_check     「呼叫我之後一定要看回傳值」             → gcc 警告
  __init / __exit  「我只在開機/卸載時用，用完可以丟」        → 放進特殊 section
  likely/unlikely  「這個條件通常為真/假」                   → 影響程式碼排列
  __cold           「我很少被呼叫（錯誤路徑）」              → 放進 .text.unlikely
  __always_inline  「一定要 inline」/ noinline「一定不要」   → 控制 inline 決策
  __acquires(l)    「我回來時持有鎖 l」/ __releases(l)       → sparse 追蹤鎖平衡
```

## 定義在哪：`include/linux/compiler_types.h`

位址空間和鎖相關的標註定義在同一處，分成「sparse 在跑」和「正常編譯」兩個分支：

```c
/* include/linux/compiler_types.h:27-80（v6.18） */
/* sparse defines __CHECKER__; see Documentation/dev-tools/sparse.rst */
#ifdef __CHECKER__
/* address spaces */
# define __kernel	__attribute__((address_space(0)))
# define __user		__attribute__((noderef, address_space(__user)))
# define __iomem	__attribute__((noderef, address_space(__iomem)))
# define __percpu	__attribute__((noderef, address_space(__percpu)))
# define __rcu		__attribute__((noderef, address_space(__rcu)))
static inline void __chk_user_ptr(const volatile void __user *ptr) { }
static inline void __chk_io_ptr(const volatile void __iomem *ptr) { }
/* context/locking */
# define __must_hold(x)	__attribute__((context(x,1,1)))
# define __acquires(x)	__attribute__((context(x,0,1)))
# define __cond_acquires(x) __attribute__((context(x,0,-1)))
# define __releases(x)	__attribute__((context(x,1,0)))
# define __acquire(x)	__context__(x,1)
# define __release(x)	__context__(x,-1)
# define __cond_lock(x,c)	((c) ? ({ __acquire(x); 1; }) : 0)
/* other */
# define __force	__attribute__((force))
# define __nocast	__attribute__((nocast))
# define __safe		__attribute__((safe))
# define __private	__attribute__((noderef))
# define ACCESS_PRIVATE(p, member) (*((typeof((p)->member) __force *) &(p)->member))
#else /* __CHECKER__ */
/* address spaces */
# define __kernel
# ifdef STRUCTLEAK_PLUGIN
#  define __user	__attribute__((user))
# else
#  define __user	BTF_TYPE_TAG(user)
# endif
# define __iomem
# define __percpu	__percpu_qual BTF_TYPE_TAG(percpu)
# define __rcu		BTF_TYPE_TAG(rcu)

# define __chk_user_ptr(x)	(void)0
# define __chk_io_ptr(x)	(void)0
/* context/locking */
# define __must_hold(x)
# define __acquires(x)
# define __cond_acquires(x)
# define __releases(x)
# define __acquire(x)	(void)0
# define __release(x)	(void)0
# define __cond_lock(x,c) (c)
/* other */
# define __force
# define __nocast
# define __safe
# define __private
# define ACCESS_PRIVATE(p, member) ((p)->member)
# define __builtin_warning(x, y...) (1)
#endif /* __CHECKER__ */
```

讀這段的要點：

- **sparse 分支**（`__CHECKER__` 有定義）：`address_space(__user)` 把指標放進一個獨立的位址空間；`noderef` 表示「不准直接解參考」。`context(x, in, out)` 描述函式進入時和離開時「持有 x」的計數。
- **正常編譯分支**：幾乎全部展開成空的。例外是 `__user`、`__percpu`、`__rcu` 可能展開成 `BTF_TYPE_TAG(...)`——在 `CONFIG_DEBUG_INFO_BTF` 且 pahole 支援 BTF type tag 時（compiler_types.h:20-25），這些標註會被保留進 BTF 除錯資訊，讓 BPF verifier 知道一個指標是 user 指標。本課的 build 開了 `CONFIG_DEBUG_INFO_BTF=y`，但 gcc 11 不支援 `btf_type_tag`、`CONFIG_PAHOLE_HAS_BTF_TAG` 沒有被設定，所以 `__user` 確實展開成空的（上面 `.i` 檔的實測結果）。
- `__percpu_qual`：在支援的架構與編譯器上，per-cpu 指標會被放進一個真正的「具名位址空間」（x86 的 `__seg_gs`，見 `arch/x86/include/asm/percpu.h:90-95`，條件包含 `CONFIG_USE_X86_SEG_SUPPORT` 與編譯器支援 `typeof_unqual`），這時編譯器自己就能擋掉一部分誤用；不符合條件時它是空的（`include/asm-generic/percpu.h:19`）。細節屬於 [Ch 27](./27-bitmap-percpu-kfifo.md)。

## 逐一解讀：讀碼時它告訴你什麼

### `__user`：這個位址來自 user space

```c
/* include/linux/uaccess.h:204-205（v6.18） */
static __always_inline unsigned long __must_check
copy_from_user(void *to, const void __user *from, unsigned long n)
```

讀碼訊號：

- 參數有 `__user` → 這是 syscall 或 ioctl 的資料流入口；往下追一定會遇到 `copy_from_user` / `get_user` / `strncpy_from_user` 之類。
- 如果你看到 `__user` 指標被直接解參考，那**就是 bug**（或者是在 `user_access_begin()`/`unsafe_get_user()` 這種明確開啟的區段裡）。這是安全研究者審 driver 時第一個搜尋的 pattern。
- `copy_from_user` 的回傳值是「**沒有**複製成功的 byte 數」，0 表示全部成功。它被標了 `__must_check`，這不是巧合：不檢查它就等於信任了一段可能沒填好的 buffer。

### `__iomem`：這是 MMIO 位址

```c
/* arch/x86/include/asm/io.h:195（v6.18） */
void __iomem *ioremap(resource_size_t offset, unsigned long size);
```

`ioremap` 把裝置的實體暫存器位址映射進 kernel 虛擬位址空間，回傳 `__iomem` 指標。讀碼訊號：這個指標背後是**硬體暫存器**，讀寫有副作用（讀一次可能清除中斷狀態），必須用 `readl()`/`writel()`/`ioread32()` 存取，這些函式保證存取寬度、順序和不被編譯器最佳化掉。看到 driver 裡 `void __iomem *base`，就知道後面一堆 `readl(base + REG_STATUS)` 是在跟硬體講話。

### `__rcu`：這個指標要在 RCU 規則下存取

```c
/* include/linux/sched.h:1074（v6.18），struct task_struct 的欄位 */
	struct task_struct __rcu	*real_parent;

/* kernel/sys.c:1016-1025（v6.18） */
SYSCALL_DEFINE0(getppid)
{
	int pid;

	rcu_read_lock();
	pid = task_tgid_vnr(rcu_dereference(current->real_parent));
	rcu_read_unlock();

	return pid;
}
```

讀碼訊號：欄位標了 `__rcu`，代表**讀者不拿鎖**，寫者會在讀者還在用舊值時替換指標，舊物件等 grace period 過後才釋放。讀者必須在 `rcu_read_lock()` 區段內用 `rcu_dereference()` 取值。`rcu_dereference` 內部怎麼「換鑰匙顏色」：

```c
/* include/linux/rcupdate.h:529-535（v6.18） */
#define __rcu_dereference_check(p, local, c, space) \
({ \
	/* Dependency order vs. p above. */ \
	typeof(*p) *local = (typeof(*p) *__force)READ_ONCE(p); \
	RCU_LOCKDEP_WARN(!(c), "suspicious rcu_dereference_check() usage"); \
	rcu_check_sparse(p, space); \
	((typeof(*p) __force __kernel *)(local)); \
})
```

注意兩個 `__force` 轉型：先把 `__rcu` 指標用 `READ_ONCE`（[Ch 21](./21-read-once-and-barriers.md)）讀出來並強制轉成普通指標，最後再明確標成 `__kernel`。`rcu_check_sparse(p, space)` 讓 sparse 確認傳進來的真的是 `__rcu` 指標。完整的 RCU 讀法是 [Ch 31](./31-reading-rcu-code.md) 的主題。

### `__percpu`：每個 CPU 一份

```c
/* kernel/sched/sched.h:3570-3576（v6.18） */
static inline void mm_cid_put_lazy(struct task_struct *t)
{
	struct mm_struct *mm = t->mm;
	struct mm_cid __percpu *pcpu_cid = mm->pcpu_cid;
	int cid;

	lockdep_assert_irqs_disabled();
	cid = __this_cpu_read(pcpu_cid->cid);
```

`__percpu` 指標不是一個真正可用的位址，而是一個「偏移量」，要加上當前 CPU 的 per-cpu 區域基址才是真位址。讀碼訊號：看到 `__percpu`，就要找 `this_cpu_read`/`per_cpu_ptr(p, cpu)` 這類存取；直接 `p->field` 是錯的。

### `__force`：明寫的逃生門

```c
/* include/uapi/linux/byteorder/little_endian.h:34-35（v6.18） */
#define __cpu_to_le32(x) ((__force __le32)(__u32)(x))
#define __le32_to_cpu(x) ((__force __u32)(__le32)(x))
```

`__force` 只在轉型裡出現，告訴 sparse「這次跨位址空間／跨 bitwise 型別的轉換是故意的」。讀碼訊號：**`__force` 出現的地方就是型別系統的邊界**。它應該集中在底層 helper（像上面這種轉換函式、`rcu_dereference` 內部）；如果你在一般的 driver 邏輯裡看到一堆 `__force`，那通常是有人在壓 sparse 警告，值得多看兩眼。v6.18 排除 `tools/` 後大約 3500 處。

### `__bitwise`：不准直接拿來算的整數

```c
/* include/uapi/linux/types.h:28-41（v6.18），節錄 */
#define __bitwise	__attribute__((bitwise))
...
typedef __u16 __bitwise __le16;
typedef __u16 __bitwise __be16;
typedef __u32 __bitwise __le32;
typedef __u32 __bitwise __be32;
typedef __u64 __bitwise __le64;
typedef __u64 __bitwise __be64;
```

（第 28 行是 `__CHECKER__` 分支，第 30 行的非 sparse 分支定義成空的。）

`__le32` 表示「這 4 bytes 是 little-endian 排列的」，可能來自磁碟或網路。sparse 把每個 `__bitwise` typedef 當成**不相容的獨立型別**：`__le32` 不能直接和 `u32` 相加、比較或互相賦值，一定要經過 `le32_to_cpu()`。讀碼訊號：struct 欄位是 `__le32`/`__be16` → 這個 struct 描述的是**外部格式**（ext4 的磁碟結構、網路封包標頭、USB 描述子），讀寫一定伴隨轉換：

```c
/* fs/ext4/super.c:330（v6.18） */
	return le32_to_cpu(bg->bg_block_bitmap_lo) |
```

`__bitwise` 不只用在 endian。`typedef unsigned __bitwise __poll_t;`（types.h:60）讓 `POLLIN` 這類旗標不會和普通 `int` 混用；`Documentation/dev-tools/sparse.rst` 裡用 `pm_request_t` 示範同一招。這是 C 裡做 **newtype** 的手法。

### `__must_check`：回傳值一定要看

```c
/* include/linux/compiler_attributes.h:368（v6.18） */
#define __must_check                    __attribute__((__warn_unused_result__))
```

這是**編譯器本身**會檢查的（不需要 sparse）。Ch 17 的 `ERR_PTR`、`IS_ERR`、`PTR_ERR`、`no_free_ptr` 全部標了它。讀碼訊號：一個函式標了 `__must_check`，代表作者認為「忽略它的回傳值幾乎一定是 bug」——通常是資源取得（不檢查就會用到無效資源）或部分成功（`copy_from_user` 回傳沒複製的 byte 數）。

### `__init`、`__exit`、`__initdata`：用完就丟的程式碼與資料

```c
/* include/linux/init.h:45-49（v6.18） */
#define __init		__section(".init.text") __cold __latent_entropy	\
						__no_kstack_erase
#define __initdata	__section(".init.data")
#define __initconst	__section(".init.rodata")
#define __exitdata	__section(".exit.data")

/* include/linux/init.h:79 */
#define __exit          __section(".exit.text") __exitused __cold notrace
```

`__init` 函式只在開機時執行一次（或 module 載入時），之後整個 `.init.text` section 被釋放。在我們的 build 上實際看：

```
$ readelf -SW ~/code_reading/build/vmlinux | grep -E " \.text | \.init\.text | \.init\.data | \.exit\.text "
  [ 1] .text             PROGBITS        ffffffff81000000 200000 131b840 00  AX  0   0 4096
  [21] .init.text        PROGBITS        ffffffff83adb000 2cdb000 08de24 00  AX  0   0 16
  [23] .init.data        PROGBITS        ffffffff83b6e000 2d6e000 123d88 00  WA  0   0 8192
  [32] .exit.text        PROGBITS        ffffffff83e3d380 303d380 003dfb 00  AX  0   0 16

$ nm ~/code_reading/build/vmlinux | grep -E " (T|t) (start_kernel|do_exit|pipe_fcntl|init_pipe_fs)$"
ffffffff812a32d0 T do_exit
ffffffff83b2cef0 t init_pipe_fs
ffffffff81594be0 T pipe_fcntl
ffffffff83adb9b0 T start_kernel
```

`start_kernel`（`init/main.c:909-910`：`asmlinkage __visible __init __no_sanitize_address __noreturn __no_stack_protector void start_kernel(void)`）和 `init_pipe_fs`（`fs/pipe.c:1536`：`static int __init init_pipe_fs(void)`）的位址都落在 `.init.text`（`0xffffffff83adb000` 起、大小 `0x8de24`）；一般函式 `do_exit`、`pipe_fcntl` 在 `.text`。

在 QEMU 裡開機，用 `~/code_reading/qemu/run-script.sh` 執行一個只有兩行的 guest 腳本：

```sh
#!/bin/sh
dmesg | grep -i "freeing unused"
grep -i " T start_kernel\| t kernel_init_freeable\| T do_exit" /proc/kallsyms 2>/dev/null | head
```

```
[    2.475713] Freeing unused kernel image (initmem) memory: 3664K
[    2.480688] Freeing unused kernel image (text/rodata gap) memory: 912K
[    2.481326] Freeing unused kernel image (rodata/data gap) memory: 188K
ffffffff812a32d0 T do_exit
ffffffff83adb9b0 T start_kernel
ffffffff83adc210 t kernel_init_freeable
```

開機 2.5 秒左右，kernel 釋放了 3664 KB 的 init 記憶體（`.init.text` + `.init.data` 等）。`/proc/kallsyms` 仍然列出 `start_kernel` 的符號，但那個位址的記憶體已經還給 page allocator 了。

讀碼訊號：

- 看到 `__init`，就知道這個函式**只在初始化時跑**，讀執行期路徑時可以跳過它。
- **非 `__init` 函式不能呼叫 `__init` 函式**（開機後那段程式碼已經不在了）。build 時 `modpost` 會檢查這種引用，發出「section mismatch in reference」警告（`scripts/mod/modpost.c:1038`）。
- `__exit`：module 卸載時才執行。編進 kernel 本體（built-in）的程式碼永遠不會被卸載，所以 `__exit` 函式在 built-in 時沒有用。x86 的 linker script 註解說明 `.exit.text` 是「discarded at runtime, not link time, to deal with references from .altinstructions」（`arch/x86/kernel/vmlinux.lds.S:321-327`），所以上面 readelf 還看得到它。

### `likely()` / `unlikely()`：分支提示

```c
/* include/linux/compiler.h:76-77（v6.18） */
# define likely(x)	__builtin_expect(!!(x), 1)
# define unlikely(x)	__builtin_expect(!!(x), 0)
```

（Ch 15 介紹過 `__builtin_expect`。）它到底改變了什麼？寫一個對照：

```c
/* expect.c */
#define likely(x)   __builtin_expect(!!(x), 1)
#define unlikely(x) __builtin_expect(!!(x), 0)

void slow_path(void);
void fast_path(void);

void with_unlikely(int err)
{
	if (unlikely(err))
		slow_path();
	else
		fast_path();
}

void with_likely(int err)
{
	if (likely(err))
		slow_path();
	else
		fast_path();
}
```

```
$ gcc -O2 -c expect.c && objdump -dr --no-show-raw-insn expect.o   （刪去 nop 填充）
0000000000000000 <with_unlikely>:
   0:	endbr64
   4:	test   %edi,%edi
   6:	jne    10 <with_unlikely+0x10>
   8:	jmp    d <with_unlikely+0xd>
			9: R_X86_64_PLT32	fast_path-0x4
  10:	jmp    15 <with_unlikely+0x15>
			11: R_X86_64_PLT32	slow_path-0x4
0000000000000020 <with_likely>:
  20:	endbr64
  24:	test   %edi,%edi
  26:	je     30 <with_likely+0x10>
  28:	jmp    2d <with_likely+0xd>
			29: R_X86_64_PLT32	slow_path-0x4
  30:	jmp    35 <with_likely+0x15>
			31: R_X86_64_PLT32	fast_path-0x4
```

兩個函式的原始碼只差 `likely`/`unlikely`，語意完全相同。差別在**哪一個呼叫緊接在條件跳躍之後（fall-through）**：`with_unlikely` 把 `fast_path` 排在不跳的那條路，`with_likely` 把 `slow_path` 排在不跳的那條路。CPU 取指令是順著往下讀的，fall-through 路徑對 instruction cache 和分支預測比較友善。

讀碼訊號：`unlikely(...)` 幾乎總是包著**錯誤檢查**。讀碼時可以把 `if (unlikely(x)) { ... }` 理解成「這是錯誤路徑」，第一遍閱讀時直接跳過，專注在主線。v6.18 排除 `tools/` 後 `likely(`/`unlikely(` 合計約 2.6 萬處。

`compiler.h:17-47` 還有一個 `CONFIG_TRACE_BRANCH_PROFILING` 分支：開啟後 `likely`/`unlikely` 會被換成一個會記錄「猜對幾次、猜錯幾次」的版本（`__branch_check__`，把統計資料放進 `_ftrace_annotated_branch` section），讓開發者找出標錯的提示。本課的 build 是 `CONFIG_BRANCH_PROFILE_NONE=y`，沒有開。

### `__always_inline`、`noinline`、`__cold`

```c
/* include/linux/compiler_attributes.h:55（v6.18） */
#define __always_inline                 inline __attribute__((__always_inline__))
/* compiler_attributes.h:231 */
#define   noinline                      __attribute__((__noinline__))
/* include/linux/compiler_types.h:113（條件式定義，見 98-116 行的註解） */
#define __cold				__attribute__((__cold__))
```

- `__always_inline`：一般的 `inline` 只是建議，編譯器可以不理。`__always_inline` 強制 inline，用在**必須** inline 的地方：效能關鍵的小函式、`noinstr` 區段（不能有可被追蹤的函式呼叫）、或者需要 `__builtin_constant_p` 在呼叫端看到常數的函式（Ch 15）。
- `noinline`：禁止 inline。讀碼訊號：作者想讓這個函式**在 stack trace 和 ftrace 裡看得到**，或者避免把大的 stack frame 併進呼叫端（`noinline_for_stack`，compiler_types.h:265，就是為這個理由取的別名）。
- `__cold`：告訴編譯器這個函式很少被呼叫，放進 `.text.unlikely`、用較省空間的最佳化，並且呼叫它的分支會被視為 unlikely。注意 compiler_types.h:98-116 的註解：在 `-falign-functions` 和舊版 gcc 的組合下，`__cold` 會被定義成空的，因為 gcc 會丟掉 cold 函式的對齊。

user-space 實測：

```c
/* attrs18.c */
#include <stdio.h>
#include <stdlib.h>

#define __must_check    __attribute__((__warn_unused_result__))
#define __cold          __attribute__((__cold__))
#define noinline        __attribute__((__noinline__))
#undef  __always_inline   /* glibc 的 <sys/cdefs.h> 也定義了它 */
#define __always_inline inline __attribute__((__always_inline__))

static __must_check int reserve(int n) { return n > 100 ? -12 : 0; }   /* -ENOMEM */

__cold noinline void report_error(const char *what)
{
	fprintf(stderr, "error: %s\n", what);
}

static __always_inline int add1(int x) { return x + 1; }
noinline int add2(int x) { return x + 2; }

int main(int argc, char **argv)
{
	reserve(argc * 200);                 /* 回傳值被丟掉：__must_check 會罵 */
	if (reserve(argc) < 0)
		report_error("reserve");
	printf("%d %d\n", add1(argc), add2(argc));
	return 0;
}
```

```
$ gcc -std=gnu11 -Wall -O2 -c attrs18.c -o attrs18.o
attrs18.c: In function ‘main’:
attrs18.c:22:9: warning: ignoring return value of ‘reserve’ declared with attribute ‘warn_unused_result’ [-Wunused-result]
   22 |         reserve(argc * 200);                 /* 回傳值被丟掉：__must_check 會罵 */
      |         ^~~~~~~~~~~~~~~~~~~
$ objdump -t attrs18.o | grep -E "report_error|add1|add2|main"
0000000000000021 l     F .text.unlikely	0000000000000011 main.cold
0000000000000000 g     F .text.unlikely	0000000000000021 report_error
0000000000000000 g     F .text	0000000000000008 add2
0000000000000000 g     F .text.startup	0000000000000033 main
```

- `__must_check` 觸發了 `-Wunused-result`（clang 14 也會，訊息略不同）。
- `report_error` 被放進 `.text.unlikely`；gcc 甚至把 `main` 裡呼叫它的那一段切出來成為 `main.cold`，也放進 `.text.unlikely`。讀 kernel 的 objdump 或 stack trace 時看到 `xxx.cold` 符號，就是這個機制。
- `add1` 沒有符號（被 inline 掉了），`add2` 有（noinline）。
- 另一個實測副產品：沒加 `#undef` 時 gcc 會警告 `"__always_inline" redefined`，因為 glibc 的 `<sys/cdefs.h>` 也定義了同名巨集。kernel 不用 glibc，沒有這個問題；但如果你把 kernel 的標頭技巧搬到 user space，要注意撞名。

### `__acquires`、`__releases`、`__must_hold`：鎖的契約

```c
/* kernel/sched/core.c:705-706（v6.18） */
struct rq *__task_rq_lock(struct task_struct *p, struct rq_flags *rf)
	__acquires(rq->lock)
```

讀碼訊號：這個函式**回來時持有 `rq->lock`**，呼叫端負責之後解鎖。這種「拿鎖的函式和放鎖的函式分開」的寫法在 kernel 很常見，沒有標註的話，你得讀完函式本體才知道它離開時鎖的狀態。`__releases(x)` 相反；`__must_hold(x)` 表示「呼叫我之前你必須已經持有 x，我回來時你還持有」。v6.18 排除 `tools/` 後三者合計約 1270 處。

sparse 用 `context(x, 進入時計數, 離開時計數)` 追蹤：`__acquires` 是 (0,1)、`__releases` 是 (1,0)、`__must_hold` 是 (1,1)。函式內每條路徑結束時的計數不符，就報 `context imbalance`。

注意：這套 sparse 機制正在被 lockdep 的執行期檢查（`lockdep_assert_held()`）和 clang 的 thread-safety analysis 討論取代或補充，讀到新程式碼時可能會看到別的寫法。[Ch 30](./30-reading-locking-code.md) 會細談。

## sparse：讓標註真的被檢查

### 它是什麼

sparse 是 Linus Torvalds 在 2003 年開始寫的 C 語意檢查器，專門理解 kernel 的這些標註。它是一個前端：解析 C、建立型別資訊、跑檢查，**不產生目的碼**。`Documentation/dev-tools/sparse.rst` 是 kernel 這邊的使用說明，sparse 本身的文件在 sparse.docs.kernel.org。

### 從原始碼安裝（無 root）

本課環境沒有 sudo，不能 `apt install sparse`。從 kernel.org 的 git 倉庫編：

```
$ mkdir -p ~/code_reading/scratch/18 && cd ~/code_reading/scratch/18
$ git clone --depth 50 https://git.kernel.org/pub/scm/devel/sparse/sparse.git
$ cd sparse && git describe --tags
v0.6.5-rc1-21-g84ec065
$ make -j8 PREFIX=$HOME/.local install
...
  INSTALL /home/ypp/.local/bin/sparse
  INSTALL /home/ypp/.local/bin/c2xml
$ ~/.local/bin/sparse --version
v0.6.5-rc1-21-g84ec065
```

在本環境只用到 gcc 和 make，clone 加編譯在幾分鐘內完成。如果 git.kernel.org 連不上，GitHub 上有鏡像。之後要確保 `~/.local/bin` 在 `PATH` 裡：kernel 頂層 Makefile:543 寫的是 `CHECK = sparse`，`make C=1` 會直接呼叫名為 `sparse` 的指令（可用 `make CHECK=/path/to/sparse` 覆寫）。

### 最小範例：故意犯五種錯

不需要 kernel tree，自己定義標註就能讓 sparse 檢查：

```c
/* user_demo.c：最小 sparse 示範，不需要 kernel tree */
#ifdef __CHECKER__
# define __user    __attribute__((noderef, address_space(__user)))
# define __force   __attribute__((force))
# define __bitwise __attribute__((bitwise))
# define __acquires(x) __attribute__((context(x,0,1)))
# define __releases(x) __attribute__((context(x,1,0)))
# define __acquire(x)  __context__(x,1)
# define __release(x)  __context__(x,-1)
#else
# define __user
# define __force
# define __bitwise
# define __acquires(x)
# define __releases(x)
# define __acquire(x) (void)0
# define __release(x) (void)0
#endif

typedef unsigned int __bitwise le32;

unsigned long copy_from_user(void *to, const void __user *from, unsigned long n);

int bad_read(int __user *uptr)
{
	return *uptr;                   /* (1) 直接解參考 user 指標 */
}

int good_read(int __user *uptr)
{
	int v;
	if (copy_from_user(&v, uptr, sizeof(v)))
		return -14;             /* -EFAULT */
	return v;
}

void leak(int *kptr, int __user *uptr)
{
	kptr = uptr;                    /* (2) user 指標混進 kernel 指標 */
	(void)kptr;
}

void ok_cast(int *kptr, int __user *uptr)
{
	kptr = (__force int *)uptr;     /* (3) 明說「我知道我在做什麼」 */
	(void)kptr;
}

unsigned int endian(le32 x)
{
	return x + 1;                   /* (4) bitwise 型別直接拿來算 */
}

void lock(void) __acquires(L);
void unlock(void) __releases(L);

int forgot_unlock(int x)
{
	lock();
	if (x)
		return -1;              /* (5) 這條路徑忘了 unlock */
	unlock();
	return 0;
}
```

先用 gcc 編，證明一般編譯器什麼都看不出來：

```
$ gcc -Wall -c user_demo.c -o /dev/null && echo "gcc: no warnings"
gcc: no warnings
```

再用 sparse（`-Wno-decl` 關掉「非 static 函式沒有事先宣告」的提醒，讓輸出聚焦）：

```
$ sparse -Wno-decl user_demo.c
user_demo.c:39:14: warning: incorrect type in assignment (different address spaces)
user_demo.c:39:14:    expected int *kptr
user_demo.c:39:14:    got int [noderef] __user *uptr
user_demo.c:51:16: warning: restricted le32 degrades to integer
user_demo.c:26:17: warning: dereference of noderef expression
user_demo.c:57:5: warning: context imbalance in 'forgot_unlock' - wrong count at exit
```

逐條對照：

| 行 | 錯誤 | sparse 訊息 |
|---|---|---|
| 26 | (1) 直接 `*uptr` | `dereference of noderef expression` |
| 39 | (2) `__user` 指標賦值給普通指標 | `incorrect type in assignment (different address spaces)`，並印出 `int [noderef] __user *` |
| 45 | (3) 有 `__force` 的轉型 | **沒有警告**——這就是 `__force` 的用途 |
| 51 | (4) `le32 + 1` | `restricted le32 degrades to integer` |
| 57 | (5) 一條路徑拿了鎖沒放 | `context imbalance in 'forgot_unlock' - wrong count at exit` |

兩個實測細節：sparse 的輸出**不按行號排序**（第 26 行的警告排在第 51 行之後，因為解參考檢查在比較後面的階段做）；還有，一開始我把 `lock()`/`unlock()` 的函式本體也寫在同一檔、但只在宣告上加 `__acquires`，sparse 反而對 `lock` 和 `unlock` 本身報了 context imbalance——**標註要加在函式定義上**，不只是宣告。kernel 的慣例是寫在定義的簽名後面（像上面 `__task_rq_lock` 那樣）。

### 對真實 kernel 檔案跑 sparse

`Documentation/dev-tools/sparse.rst:96-97`：「make C=1」只檢查**需要重新編譯**的檔案，「make C=2」不管需不需要重編都檢查。我們的 build 已經完成，所以 C=1 什麼都不會檢查：

```
$ cd ~/code_reading/linux
$ make O=../build C=1 fs/pipe.o
make[1]: Entering directory '/home/ypp/code_reading/build'
  GEN     Makefile
  CALL    /home/ypp/code_reading/linux/scripts/checksyscalls.sh
  DESCEND objtool
  INSTALL libsubcmd_headers
  DESCEND bpf/resolve_btfids
  INSTALL libsubcmd_headers
make[1]: Leaving directory '/home/ypp/code_reading/build'

$ make O=../build C=2 fs/pipe.o
make[1]: Entering directory '/home/ypp/code_reading/build'
  GEN     Makefile
  CHECK   /home/ypp/code_reading/linux/scripts/mod/empty.c
  CALL    /home/ypp/code_reading/linux/scripts/checksyscalls.sh
  DESCEND objtool
  INSTALL libsubcmd_headers
  DESCEND bpf/resolve_btfids
  INSTALL libsubcmd_headers
  CHECK   /home/ypp/code_reading/linux/fs/pipe.c
make[1]: Leaving directory '/home/ypp/code_reading/build'
```

`CHECK fs/pipe.c` 那一行就是 sparse 在跑，沒有任何警告：fs/pipe.c 對 sparse 是乾淨的（一次不到兩秒）。用 C=2 掃了幾個檔案，統計警告數：

```
fs/eventfd.o 0
fs/read_write.o 1
kernel/fork.o 23
kernel/sys.o 3
fs/namei.o 2
fs/select.o 0
kernel/signal.o 77
mm/memory.o 1
net/core/sock.o 3
kernel/sched/core.o 20
```

看 `kernel/sys.c` 的三個：

```
$ make O=../build C=2 kernel/sys.o 2>&1 | grep -A3 "warning:"
/home/ypp/code_reading/linux/kernel/sys.c:1144:32: warning: incorrect type in argument 1 (different address spaces)
/home/ypp/code_reading/linux/kernel/sys.c:1144:32:    expected struct task_struct *p1
/home/ypp/code_reading/linux/kernel/sys.c:1144:32:    got struct task_struct [noderef] __rcu *real_parent
/home/ypp/code_reading/linux/kernel/sys.c:1784:9: warning: context imbalance in '__se_sys_prlimit64' - different lock contexts for basic block
/home/ypp/code_reading/linux/kernel/sys.c:1912:33: warning: context imbalance in 'getrusage' - different lock contexts for basic block
```

第一個警告的現場：

```c
/* kernel/sys.c:1144（v6.18），在 SYSCALL_DEFINE2(setpgid, ...) 裡 */
	if (same_thread_group(p->real_parent, group_leader)) {
```

`real_parent` 是 `__rcu` 欄位（sched.h:1074），這裡沒有經過 `rcu_dereference` 就直接傳給一個期待普通指標的函式，sparse 依規則報警。但往上看，這個 syscall 在 kernel/sys.c:1133 已經 `write_lock_irq(&tasklist_lock)`——`real_parent` 的修改受 `tasklist_lock` 保護，持有寫鎖時讀它是安全的。**這是一個「程式正確、但標註沒表達出保護關係」的警告**；完全照規矩的寫法是 `rcu_dereference_protected(p->real_parent, lockdep_is_held(&tasklist_lock))`。

再看 `fs/read_write.c` 的那一個：

```
fs/read_write.c:159:1: warning: context imbalance in 'generic_file_llseek_size' - different lock contexts for basic block
```

這個函式（fs/read_write.c:158 起）在 `SEEK_CUR` 分支裡用了 `guard(spinlock)(&file->f_lock)`（Ch 17 的 cleanup.h）。sparse 的 context 追蹤看起來無法理解 cleanup attribute 帶來的隱式解鎖，於是誤判鎖不平衡。這是 cleanup.h 普及後 sparse 誤報增加的一個例子。

讀碼與審碼的心法：

- **sparse 警告不等於 bug**。kernel 樹裡長期存在大量 sparse 警告（`kernel/signal.c` 一個檔案就 77 個），多半是 `__rcu` 存取沒有走標準 API、或鎖的條件路徑 sparse 推不出來。
- **新增的 sparse 警告值得認真看**。kernel 的 CI（例如 0-day bot）會比對 patch 前後的 sparse 輸出，新增的警告會被回報給作者。
- **把 sparse 當讀碼工具**：對一個陌生檔案跑 C=2，`different address spaces` 的警告會直接告訴你哪些變數是 `__user`、`__rcu`、`__iomem`，等於一張「這個檔案和哪些外部世界打交道」的地圖。

## 對比與取捨

| 檢查方式 | 什麼時候跑 | 抓什麼 | 代價 |
|---|---|---|---|
| gcc 屬性（`__must_check`、`__cold`） | 每次編譯 | 回傳值沒用、程式碼排列 | 無 |
| sparse（`__user`、`__bitwise`、context） | `make C=1/2` | 位址空間混用、endian、鎖平衡（靜態） | 誤報不少；需另外安裝 |
| lockdep（`lockdep_assert_held`） | 執行期（debug kernel） | 實際執行到的鎖順序與持有狀態 | 只抓跑到的路徑；有效能成本 |
| KASAN / UBSAN | 執行期 | 實際發生的越界、未定義行為 | 大幅變慢 |
| 純註解 | 永遠 | 什麼都不抓 | 會過時 |

標註的價值在於它**同時**是文件和檢查條件：寫成 `__user` 比寫一行 `/* user pointer */` 好，因為前者會被 sparse 驗證、不會悄悄過時。

## 踩雷集錦

1. **以為標註會在正常編譯時被檢查**：`__user`、`__rcu`、`__acquires` 在 gcc 編譯時全部是空的（實測 `.i` 檔裡 `char __user *buf` 變成 `char *buf`）。不跑 sparse，什麼都不會被抓。
2. **以為 sparse 沒警告就安全**：sparse 只檢查它看得到的標註。沒標 `__user` 的 user 指標，sparse 一樣放行。它也不懂 cleanup.h 的隱式解鎖，會誤報。
3. **用 `__force` 壓警告**：`__force` 是給底層 helper 用的明確邊界。在一般邏輯裡用 `__force` 把 `__user` 轉成普通指標再解參考，sparse 安靜了，bug 還在。
4. **以為 `likely()` 能讓程式變快**：它只調整排列。猜錯的提示讓常走路徑變成跳躍路徑；而且現代 CPU 的動態分支預測器很強，提示的效果有限。kernel 社群對「到處加 likely」的 patch 普遍持保留態度。
5. **在非 `__init` 函式裡呼叫 `__init` 函式**：開機後那段記憶體已經被釋放（實測 3664K），執行期呼叫會跳進已回收的記憶體。modpost 的 section mismatch 警告就是在擋這個。
6. **只在宣告上加 `__acquires`**：本章實測，sparse 需要定義上也有標註，否則會對鎖函式本身報 context imbalance。

## 進階：再往深一層

- **`CF=` 傳額外參數給 sparse**：`make C=2 CF="-Wsparse-all" fs/pipe.o` 打開所有 sparse 檢查（大量輸出，適合研究不適合日常）。sparse 的選項見 `sparse --help` 與 sparse.docs.kernel.org。
- **smatch**：Dan Carpenter 基於 sparse 前端寫的更強的靜態分析器，做跨函式的資料流分析（例如「這個值可能是 user 控制的、而且沒檢查上界」）。在 mailing list 上常看到 patch 帶著「Reported-by: Dan Carpenter」與 smatch 的警告原文，那就是它抓到的。安裝方式和 sparse 類似。
- **Coccinelle**：另一種靜態工具，用語意 patch 描述程式碼模式，`scripts/coccinelle/` 裡有現成規則（例如找出 `IS_ERR` 用錯的地方）。和 sparse 互補：sparse 看型別，Coccinelle 看寫法。
- **BTF type tag 與 BPF**：`__user` 在 clang 編的 kernel 上可以保留到 BTF（compiler_types.h:20-25），BPF verifier 因此能拒絕 BPF 程式直接解參考 user 指標。同一個標註，從 2003 年的 sparse 一路被重用到今天的 BPF。
- **clang 的 `-Wthread-safety`**：kernel 社群有人提出用 clang 的 thread-safety（capability）分析補強或取代 sparse 的 context 檢查。v6.18 的 `include/linux/` 裡還找不到 `__guarded_by` 這類標註（`rg` 實查為零），如果你讀的是更新的版本並看到它們，就是這個方向的成果。

## 動手練習

1. 在 `user_demo.c` 裡加一個 `__iomem` 定義，寫一個函式直接解參考 `int __iomem *reg`，再寫一個把 `__iomem` 指標傳給期待 `__user` 指標的函式，看 sparse 怎麼分別報。
2. 拿掉 `user_demo.c` 第 45 行的 `__force`，重跑 sparse，確認警告出現。
3. 對 `kernel/fork.c` 跑 `make O=../build C=2 kernel/fork.o`，挑一個 `different address spaces` 警告，找到現場，判斷它是真 bug 還是「保護關係沒有被標註表達」。
4. 用 `rg -n "__must_hold\(" kernel/ | head` 找一個 `__must_hold` 函式，找出它的所有呼叫端，確認每個呼叫端都持有那把鎖。這就是 sparse 在做的事——你手動做一次就知道它省了多少工。
5. 在 QEMU guest 腳本裡加一行 `grep -c " t \| T " /proc/kallsyms` 和 `dmesg | grep -i "memory:"`，對照開機訊息裡 kernel 報告的各 section 大小（`kernel code`、`rwdata`、`init` 等）和上面 readelf 看到的 `.init.text`、`.init.data` 大小，試著解釋 3664K 是怎麼加出來的。

## 本章重點整理

- 位址空間標註（`__user`、`__iomem`、`__percpu`、`__rcu`）告訴讀者「這個指標不能直接解參考，要透過哪一族 API」；正常編譯時是空的，sparse 才檢查。
- `__force` 是明寫的型別邊界，`__bitwise` 是 C 的 newtype（`__le32`、`__poll_t`）。
- `__must_check`、`__cold`、`noinline`、`__always_inline`、`likely` 是 gcc 會處理的；`__init` 把程式碼放進開機後釋放的 section（實測釋放 3664K）。
- `__acquires`/`__releases`/`__must_hold` 是鎖的契約，sparse 用 context 計數檢查。
- sparse 從原始碼裝到 `~/.local` 即可；`make C=2` 強制檢查；kernel 樹本身有大量既有警告，新增的才是重點。

## 自我檢核

- [ ] 不看筆記，說出 `__user` 指標為什麼不能直接解參考（至少三個理由），以及正確的存取方式。
- [ ] 有人在 code review 寫「這裡加個 `__force` 就沒警告了」，你會怎麼回應？
- [ ] 能解釋為什麼 `struct ethhdr` 的 `h_proto` 是 `__be16` 而不是 `u16`，以及讀它時要做什麼。
- [ ] 能說出 `make C=1` 和 `make C=2` 的差別，以及在一個已經 build 完的 tree 上應該用哪一個。
- [ ] 看到 sparse 報 `context imbalance ... different lock contexts for basic block`，你會先懷疑哪兩種可能（真 bug vs. 誤報），各怎麼確認？
- [ ] 想一個你熟悉的 user-space 專案，有沒有哪種「同型別、不同語意」的整數或指標（例如網路位元組序、檔案 offset vs. 記憶體 offset），適合用 `__bitwise` 這種手法區分？

## 延伸閱讀

### 官方文件

- **`Documentation/dev-tools/sparse.rst`**（docs.kernel.org/dev-tools/sparse.html）
  - **讀哪裡**：全文約一百多行；「Using sparse for typechecking」（16 行起）講 `__bitwise`，「Using sparse for lock checking」（56 行起）講 context，「Getting sparse」（75 行起）與「Using sparse」（93 行起，C=1/C=2 的用法）在後半。
  - **能學到什麼**：kernel 官方對這些標註的定義與 sparse 用法；本章的 C=1/C=2 說明出自這裡。前提：讀完本章。
- **sparse 官方文件（sparse.docs.kernel.org）**
  - **讀哪裡**：從首頁的文件索引找 annotation 與命令列選項的說明；sparse 原始碼目錄下的 `Documentation/` 是同一份內容（本章 clone 下來的 `~/code_reading/scratch/18/sparse/Documentation/`）。
  - **能學到什麼**：`address_space`、`noderef`、`context`、`bitwise` 這些 attribute 的精確語意，以及 `-W` 選項清單。
- **`Documentation/arch/x86/x86_64/mm.rst`**
  - **讀哪裡**：x86-64 4-level paging 的記憶體佈局表。
  - **能學到什麼**：user 與 kernel 位址空間怎麼切，`__user` 指標為什麼「看起來」和 kernel 指標沒兩樣、卻指向完全不同的世界。

### 技術文章

- **[LWN 的 sparse 概覽文章](https://lwn.net/Articles/689907/)** — LWN.net（2016）
  - **這篇說什麼**：`Documentation/dev-tools/sparse.rst` 開頭直接引用這篇作為「sparse 概覽」，介紹 sparse 做什麼檢查、怎麼和 kernel build 整合。
  - **讀哪裡**：整篇。
  - **為什麼值得讀**：kernel 文件本身指定的入門文章。前提：知道編譯器前端大致在做什麼（[`compiler_frontend`](../../compilers/compiler_frontend/README.md) 課程的程度就夠）。

### 原始碼

- **`include/linux/compiler_types.h`、`include/linux/compiler_attributes.h`、`include/linux/init.h`（v6.18）**
  - **讀哪裡**：compiler_types.h 的 1–120 行；init.h 的 1–90 行（`__init` 系列與註解）。
  - **能學到什麼**：本章所有標註的權威定義，以及每個標註附帶的設計註解（例如 `__cold` 為什麼有時是空的）。

### 書籍

- **《Linux Device Drivers》（3rd ed.）** — Corbet, Rubini, Kroah-Hartman（O'Reilly, 2005）
  - **讀哪幾章**：第 3 章「read and write」一節（`copy_to_user`/`copy_from_user` 與 user 指標的處理）、第 9 章「Using I/O Memory」（`ioremap` 與 `readl`/`writel`）。
  - **這本書的定位**：書中的範例已經寫成 `char __user *buf` 這種形式，但重點不在 sparse，而在「為什麼不能直接碰 user 指標和 MMIO 位址」的領域背景，正好補本章的領域鴻溝。API 細節以 2.6.10 為準，已有變化。

讀懂了函式簽名上的標註，下一個你會在每個函式裡撞見的東西是記憶體配置：`kmalloc(size, GFP_KERNEL)` 那個 `GFP_KERNEL` 到底在說什麼。

→ [Ch 19 kernel 記憶體配置：kmalloc、GFP、slab、vmalloc、devm](./19-kernel-memory-allocation.md)
