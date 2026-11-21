# Ch 29 — syscall 入口與路徑：從 SYSCALL_DEFINE 開始追

> **目標**：拿到一個 strace 看到的 syscall 名字，能在一分鐘內找到 kernel 裡的實作；看得懂 `SYSCALL_DEFINE3(read, ...)` 展開後的 `__x64_sys_read`／`__se_sys_read`／`__do_sys_read` 三層，以及為什麼要有 sign-extension 包裝；能把 `read(2)` 從 `syscall` 指令一路畫到 `vfs_read`，包含 syscall table、`entry_SYSCALL_64`、`do_syscall_64` 與 compat 路徑。
> **鴻溝**：本章主要練**間接鴻溝**（syscall 號碼到函式的分派）與**慣用法鴻溝**（`SYSCALL_DEFINE` 這種會生出好幾個函式的巨集），順帶一點脈絡鴻溝（CVE-2009-0029 與 Spectre 留下的程式碼形狀）。

> **環境**：Linux kernel v6.18 原始碼與 `O=~/code_reading/build` 的 x86_64 build（`CONFIG_IA32_EMULATION=y`、`CONFIG_X86_X32_ABI` 未開）；QEMU TCG 模式 + gdb 12；user-space 範例在 WSL2（其 kernel 為 `6.18.58.1-microsoft-standard-WSL2`）上以 gcc 11.4 編譯執行。

## 為什麼需要這個？

幾乎所有 user-space 問題往 kernel 追，起點都是一個 syscall。strace 告訴你程式卡在 `read(0, ...)`，perf 告訴你時間花在 `__x64_sys_futex`，seccomp 的錯誤訊息給你一個 syscall 號碼——接下來你要做的第一件事，永遠是「這個 syscall 的 kernel 實作在哪裡」。

這件事看起來應該很直接，實際上會在幾個地方卡住：

- `grep 'sys_read'` 找不到任何叫 `sys_read` 的函式定義，因為它是巨集**生出來的**。
- perf 報告裡的 `__x64_sys_read`、`ksys_read`、`__se_sys_read`，到底哪個才是「本體」？
- strace 說 `fstat`，kernel 裡卻是 `newfstat`；`mmap` 有十幾個架構各自的版本。
- 你從 `SYSCALL_DEFINE3(read, ...)` 往回追，追不到誰呼叫它——因為呼叫者是一張由腳本產生的表，以及一個由巨集展開的 `switch`。

這些都是間接鴻溝的變形：控制流經過一層「號碼 → 函式」的對應，而這層對應不在任何一個 `.c` 檔裡以普通 C 的形式出現。上一章處理的是「指標 → 函式」，這一章處理「號碼 → 函式」。

## 先建立直覺

把 syscall 想成去區公所辦事。你（user space）不能自己走進檔案室，只能到櫃台抽號碼牌（syscall 號碼放進 `rax`），把申請書（參數）放在指定的幾個格子裡（`rdi`、`rsi`、`rdx`、`r10`、`r8`、`r9`），然後按鈴（`syscall` 指令）。櫃台人員（`entry_SYSCALL_64`）先把你所有的東西拍照存檔（存成 `struct pt_regs`），再照號碼牌查表（`x64_sys_call` 的 switch），把案子轉給負責的承辦人（`__x64_sys_read`）。承辦人從存檔照片上抄下他需要的欄位，做完事後把結果放回 `rax`，你拿到回覆離開。

```
user space                         kernel
──────────                         ──────────────────────────────────────────────
mov $0, %eax   (read 的號碼)
mov fd, %edi
mov buf, %rsi
mov n, %rdx
syscall ─────────────────────────▶ entry_SYSCALL_64        arch/x86/entry/entry_64.S
                                     │ 換 stack、存 pt_regs
                                     ▼
                                   do_syscall_64(regs, nr) arch/x86/entry/syscall_64.c
                                     │ 檢查 nr、seccomp/ptrace 等進入工作
                                     ▼
                                   x64_sys_call(regs, nr)  switch (nr) { case 0: ... }
                                     ▼
                                   __x64_sys_read(regs)    ← SYSCALL_DEFINE3 生成
                                     │ 從 regs 取出 di/si/dx
                                     ▼
                                   __se_sys_read(long,long,long)   ← 型別收斂（inline）
                                     ▼
                                   __do_sys_read(fd, buf, count)   ← 你寫的函式本體（inline）
                                     ▼
                                   ksys_read → vfs_read → f_op->read_iter ...
◀───────────────────────────────── 回傳值放 regs->ax，sysret/iret 回 user space
```

這張圖有三個區段，分別對應三種讀碼技巧：組語入口（讀 `.S` 與註解）、分派（讀生成的表）、巨集展開（讀 `.i`）。下面一段一段拆。

## 第一步：從 strace 的名字找到實作

先看一個真實的 strace：

```
$ echo hello | strace -e trace=read,fstat,newfstatat -o st.txt cat > /dev/null; tail -6 st.txt
newfstatat(3, "", {st_mode=S_IFREG|0644, st_size=353616, ...}, AT_EMPTY_PATH) = 0
newfstatat(1, "", {st_mode=S_IFCHR|0666, st_rdev=makedev(0x1, 0x3), ...}, AT_EMPTY_PATH) = 0
newfstatat(0, "", {st_mode=S_IFIFO|0600, st_size=0, ...}, AT_EMPTY_PATH) = 0
read(0, "hello\n", 131072)              = 6
read(0, "", 131072)                     = 0
+++ exited with 0 +++
```

要找 `read`，標準咒語是：

```
$ rg -n 'SYSCALL_DEFINE\d\(read,' . --type c
./fs/read_write.c:722:SYSCALL_DEFINE3(read, unsigned int, fd, char __user *, buf, size_t, count)
```

`\d` 對應參數個數（0 到 6），`(read,` 的逗號用來排除 `readv`、`readahead`、`readlink`。零參數的 syscall 沒有逗號，要寫 `SYSCALL_DEFINE0\(getpid\)`：

```
$ rg -n 'SYSCALL_DEFINE0\(getpid' . --type c
./kernel/sys.c:999:SYSCALL_DEFINE0(getpid)
```

### 名字對不上的時候：查表才是權威

strace 顯示的名字是 **user-space ABI 的名字**，kernel 裡的函式名可能不同。權威對照是 syscall table：

```
$ grep -nE '\s(fstat|mmap|getpid)\s' arch/x86/entry/syscalls/syscall_64.tbl
17:5	common	fstat			sys_newfstat
21:9	common	mmap			sys_mmap
51:39	common	getpid			sys_getpid
```

第三欄是 ABI 名字，第四欄是 kernel 的進入點。`fstat` 實際上是 `sys_newfstat`（歷史原因：舊的 `struct __old_kernel_stat` 版本保留給老架構），所以要搜的是：

```
$ rg -n 'SYSCALL_DEFINE\d\((fstat|newfstat),' . --type c
./fs/stat.c:450:SYSCALL_DEFINE2(fstat, unsigned int, fd, struct __old_kernel_stat __user *, statbuf)
./fs/stat.c:550:SYSCALL_DEFINE2(newfstat, unsigned int, fd, struct stat __user *, statbuf)
./fs/stat.c:904:COMPAT_SYSCALL_DEFINE2(newfstat, unsigned int, fd,
```

三個結果，x86_64 用的是 550 行那個。`mmap` 更極端：

```
$ rg -n 'SYSCALL_DEFINE\d\(mmap,' . --type c
./arch/riscv/kernel/sys_riscv.c:24:SYSCALL_DEFINE6(mmap, unsigned long, addr, unsigned long, len,
./arch/microblaze/kernel/sys_microblaze.c:36:SYSCALL_DEFINE6(mmap, unsigned long, addr, unsigned long, len,
./arch/sparc/kernel/sys_sparc_64.c:472:SYSCALL_DEFINE6(mmap, unsigned long, addr, unsigned long, len,
./arch/powerpc/kernel/syscalls.c:69:SYSCALL_DEFINE6(mmap, unsigned long, addr, size_t, len,
./arch/sparc/kernel/sys_sparc_32.c:119:SYSCALL_DEFINE6(mmap, unsigned long, addr, unsigned long, len,
./arch/x86/kernel/sys_x86_64.c:82:SYSCALL_DEFINE6(mmap, unsigned long, addr, unsigned long, len,
./arch/loongarch/kernel/syscall.c:28:SYSCALL_DEFINE6(mmap, unsigned long, addr, unsigned long, len, unsigned long,
./arch/x86/um/syscalls_64.c:56:SYSCALL_DEFINE6(mmap, unsigned long, addr, unsigned long, len,
./arch/arm64/kernel/sys.c:21:SYSCALL_DEFINE6(mmap, unsigned long, addr, unsigned long, len,
```

九個架構各自定義。x86_64 是 `arch/x86/kernel/sys_x86_64.c:82`（`arch/x86/um/` 是 User-Mode Linux，不是你要的）。

strace 本身也會「翻譯」：上面的 `newfstatat` 就是 strace 照 kernel 名字顯示的 `fstatat`，而 glibc 的 `fstat()` 在新版本底下其實呼叫的是 `newfstatat`——這也是為什麼我們 `-e trace=fstat` 什麼都沒抓到，要加 `newfstatat` 才看得到。**user-space 函式名、strace 顯示名、syscall table 的 ABI 名、kernel 函式名，四者不一定相同**，table 是唯一不會騙你的。

## 第二步：syscall table 與它生出來的程式碼

`arch/x86/entry/syscalls/syscall_64.tbl` 的開頭自己說明了格式：

```
# The format is:
# <number> <abi> <name> <entry point> [<compat entry point> [noreturn]]
#
# The __x64_sys_*() stubs are created on-the-fly for sys_*() system calls
#
# The abi is "common", "64" or "x32" for this file.
#
0	common	read			sys_read
1	common	write			sys_write
```

（`arch/x86/entry/syscalls/syscall_64.tbl:5-13（v6.18）`）

`abi` 欄：`common` 表示 64-bit 與 x32 共用；`64` 只給 64-bit（例如 `19 64 readv`），x32 另有自己的號碼（`515 x32 readv`，x32 號碼從 512 起跳並加上 `__X32_SYSCALL_BIT`）。

這張表不是 C，它在 build 時由 `scripts/syscalltbl.sh`（`arch/x86/entry/syscalls/Makefile:12` 引用）轉成一個標頭檔，裡面是一串巨集呼叫：

```
$ head -3 ~/code_reading/build/arch/x86/include/generated/asm/syscalls_64.h
__SYSCALL(0, sys_read)
__SYSCALL(1, sys_write)
__SYSCALL(2, sys_open)
```

這是 [Ch 14 — 巨集元程式](./14-macro-metaprogramming.md) 的 X-macro：同一份清單，`#include` 前把 `__SYSCALL` 定義成不同東西，就能生出不同的程式碼。`arch/x86/entry/syscall_64.c` 把它 include 了三次：

```c
#define __SYSCALL(nr, sym) extern long __x64_##sym(const struct pt_regs *);
#define __SYSCALL_NORETURN(nr, sym) extern long __noreturn __x64_##sym(const struct pt_regs *);
#include <asm/syscalls_64.h>
...
/*
 * The sys_call_table[] is no longer used for system calls, but
 * kernel/trace/trace_syscalls.c still wants to know the system
 * call address.
 */
#define __SYSCALL(nr, sym) __x64_##sym,
const sys_call_ptr_t sys_call_table[] = {
#include <asm/syscalls_64.h>
};
#undef  __SYSCALL

#define __SYSCALL(nr, sym) case nr: return __x64_##sym(regs);
long x64_sys_call(const struct pt_regs *regs, unsigned int nr)
{
	switch (nr) {
	#include <asm/syscalls_64.h>
	default: return __x64_sys_ni_syscall(regs);
	}
}
```

（`arch/x86/entry/syscall_64.c:12-41（v6.18）`）

第一次生出所有 `__x64_sys_*` 的宣告；第二次生出 `sys_call_table[]` 陣列；第三次生出一個巨大的 `switch`，`case 0: return __x64_sys_read(regs);` 這樣一路列下去。

注意那段註解：**`sys_call_table[]` 已經不拿來分派 syscall 了**。很多教材（包括許多 rootkit 分析文章）還在講「kernel 用 `sys_call_table[nr](regs)` 分派」，那是 v6.8 以前的事。改成 switch 的是 Linus 本人：

```
$ git log --format='%h %ad %an %s' --date=short --grep="indirect calls for system calls" v6.8..v6.18
...
1e3ad78334a6 2024-04-03 Linus Torvalds x86/syscall: Don't force use of indirect calls for system calls
$ git tag --contains 1e3ad78334a6 | grep -v rc | sort -V | head -1
v6.9
```

commit 訊息的重點：switch 讓編譯器可以產生一串條件分支，而「條件分支預測錯誤只會投機執行到錯的 syscall（無害），不會投機執行到任意 gadget」。這是 BHI（Branch History Injection）漏洞的緩解之一，和 [Ch 28](./28-ops-tables-indirect-calls.md) 講的 retpoline 是同一條脈絡：**Spectre 之後，kernel 在熱路徑上刻意把間接呼叫換成直接呼叫**。

讀碼上的結論：要找「誰呼叫 `__x64_sys_read`」，在原始碼裡 grep 不到，因為呼叫者是巨集展開的 `case 0:`。你只要記住「syscall 實作的呼叫者是 `x64_sys_call` 的 switch，而 switch 由 `syscall_64.tbl` 生成」就夠了。

## 第三步：從 `syscall` 指令到 `do_syscall_64`

`syscall` 指令會讓 CPU 跳到 `MSR_LSTAR` 暫存器裡的位址。開機時 kernel 把它設好：

```c
	wrmsrq(MSR_LSTAR, (unsigned long)entry_SYSCALL_64);
```

（`arch/x86/kernel/cpu/common.c:2230（v6.18）`）

組語入口很長，但前面的註解寫得非常好，讀組語之前先讀註解：

```
 * 64-bit SYSCALL saves rip to rcx, clears rflags.RF, then saves rflags to r11,
 * then loads new ss, cs, and rip from previously programmed MSRs.
 * rflags gets masked by a value from another MSR (so CLD and CLAC
 * are not needed). SYSCALL does not save anything on the stack
 * and does not change rsp.
 *
 * Registers on entry:
 * rax  system call number
 * rcx  return address
 * r11  saved rflags (note: r11 is callee-clobbered register in C ABI)
 * rdi  arg0
 * rsi  arg1
 * rdx  arg2
 * r10  arg3 (needs to be moved to rcx to conform to C ABI)
 * r8   arg4
 * r9   arg5
```

（`arch/x86/entry/entry_64.S:62-77（v6.18）`）

為什麼第四個參數是 `r10` 而不是 C 呼叫慣例的 `rcx`？因為 `syscall` 指令本身會把返回位址寫進 `rcx`，`rcx` 被佔用了。這個細節會在第五步的範例裡用到。入口的主體（節錄）：

```
SYM_CODE_START(entry_SYSCALL_64)
	UNWIND_HINT_ENTRY
	ENDBR

	swapgs
	/* tss.sp2 is scratch space. */
	movq	%rsp, PER_CPU_VAR(cpu_tss_rw + TSS_sp2)
	SWITCH_TO_KERNEL_CR3 scratch_reg=%rsp
	movq	PER_CPU_VAR(cpu_current_top_of_stack), %rsp
...
	/* Construct struct pt_regs on stack */
	pushq	$__USER_DS				/* pt_regs->ss */
	pushq	PER_CPU_VAR(cpu_tss_rw + TSS_sp2)	/* pt_regs->sp */
	pushq	%r11					/* pt_regs->flags */
	pushq	$__USER_CS				/* pt_regs->cs */
	pushq	%rcx					/* pt_regs->ip */
SYM_INNER_LABEL(entry_SYSCALL_64_after_hwframe, SYM_L_GLOBAL)
	pushq	%rax					/* pt_regs->orig_ax */

	PUSH_AND_CLEAR_REGS rax=$-ENOSYS

	/* IRQs are off. */
	movq	%rsp, %rdi
	/* Sign extend the lower 32bit as syscall numbers are treated as int */
	movslq	%eax, %rsi
...
	call	do_syscall_64		/* returns with IRQs disabled */
```

（`arch/x86/entry/entry_64.S:87-121（v6.18）`，省略部分緩解巨集）

讀這段組語不需要懂每一條。抓三個 beacon（[Ch 2](./02-program-comprehension-models.md) 的「信標」）就夠：`swapgs` + 換 `%rsp`（切到 kernel 的 per-CPU 資料與 kernel stack）；一串 `pushq` 加 `PUSH_AND_CLEAR_REGS`（**把 user 的暫存器存成 `struct pt_regs`，並清掉暫存器**，避免 user 控制的值流進 kernel 程式碼）；`movq %rsp, %rdi` + `call do_syscall_64`（第一個參數是 `pt_regs` 的位址，第二個是號碼）。`rax=$-ENOSYS` 表示 `pt_regs->ax` 的預設值是 `-ENOSYS`，所以無效的 syscall 什麼都不做也會回傳 -38。

進到 C：

```c
static __always_inline bool do_syscall_x64(struct pt_regs *regs, int nr)
{
	/*
	 * Convert negative numbers to very high and thus out of range
	 * numbers for comparisons.
	 */
	unsigned int unr = nr;

	if (likely(unr < NR_syscalls)) {
		unr = array_index_nospec(unr, NR_syscalls);
		regs->ax = x64_sys_call(regs, unr);
		return true;
	}
	return false;
}
...
__visible noinstr bool do_syscall_64(struct pt_regs *regs, int nr)
{
	add_random_kstack_offset();
	nr = syscall_enter_from_user_mode(regs, nr);

	instrumentation_begin();

	if (!do_syscall_x64(regs, nr) && !do_syscall_x32(regs, nr) && nr != -1) {
		/* Invalid system call, but still a system call. */
		regs->ax = __x64_sys_ni_syscall(regs);
	}

	instrumentation_end();
	syscall_exit_to_user_mode(regs);
```

（`arch/x86/entry/syscall_64.c:53-100（v6.18）`）

幾個值得停下來的點：

- `unsigned int unr = nr;` 把負數變成超大正數，一次比較同時擋掉負數與過大的號碼。這是 [Ch 9](./09-expressions-integers-bits.md) 講的整數轉換被刻意利用的例子。
- `array_index_nospec()` 防止 CPU 在投機執行時用越界的 `unr` 去查表（Spectre v1 緩解）。
- `syscall_enter_from_user_mode()` 是 seccomp、ptrace（strace 就靠它）、audit、syscall tracepoint 掛進來的地方。它可以改寫 `nr`，甚至回傳 -1 表示「別執行了」。
- `noinstr` 表示這個函式本身不能被 ftrace 追蹤（還沒進入可安全插樁的狀態），所以 [Ch 28](./28-ops-tables-indirect-calls.md) 的 ftrace 方法在這裡看不到 `do_syscall_64`；要用 gdb。

## 第四步：`SYSCALL_DEFINE3` 展開——一個巨集生出三層函式

現在看 `fs/read_write.c:722`：

```c
SYSCALL_DEFINE3(read, unsigned int, fd, char __user *, buf, size_t, count)
{
	return ksys_read(fd, buf, count);
}
```

（`fs/read_write.c:722-725（v6.18）`）

參數列寫成「型別, 名字, 型別, 名字」的怪格式，因為巨集要把型別和名字分開處理。通用版定義在 `include/linux/syscalls.h`：

```c
#define SYSCALL_DEFINE3(name, ...) SYSCALL_DEFINEx(3, _##name, __VA_ARGS__)
...
#define SYSCALL_DEFINEx(x, sname, ...)				\
	SYSCALL_METADATA(sname, x, __VA_ARGS__)			\
	__SYSCALL_DEFINEx(x, sname, __VA_ARGS__)
```

（`include/linux/syscalls.h:227,234-236（v6.18）`）

`SYSCALL_METADATA` 是給 syscall tracepoint 用的中繼資料（`CONFIG_FTRACE_SYSCALLS`），先跳過。重點是 `__SYSCALL_DEFINEx`。x86 用自己的版本覆蓋通用版：

```c
#define __SYSCALL_DEFINEx(x, name, ...)					\
	static long __se_sys##name(__MAP(x,__SC_LONG,__VA_ARGS__));	\
	static inline long __do_sys##name(__MAP(x,__SC_DECL,__VA_ARGS__));\
	__X64_SYS_STUBx(x, name, __VA_ARGS__)				\
	__IA32_SYS_STUBx(x, name, __VA_ARGS__)				\
	static long __se_sys##name(__MAP(x,__SC_LONG,__VA_ARGS__))	\
	{								\
		long ret = __do_sys##name(__MAP(x,__SC_CAST,__VA_ARGS__));\
		__MAP(x,__SC_TEST,__VA_ARGS__);				\
		__PROTECT(x, ret,__MAP(x,__SC_ARGS,__VA_ARGS__));	\
		return ret;						\
	}								\
	static inline long __do_sys##name(__MAP(x,__SC_DECL,__VA_ARGS__))
```

（`arch/x86/include/asm/syscall_wrapper.h:223-235（v6.18）`）

最後一行是**沒有函式本體的函式開頭**——你在 `SYSCALL_DEFINE3(...)` 後面寫的 `{ return ksys_read(...); }` 就接在這裡，成為 `__do_sys_read` 的本體。這是 [Ch 13](./13-preprocessor-basics.md) 提過的「巨集吃掉函式簽章、讓使用者接上大括號」手法。

`__MAP(x, m, t1, a1, t2, a2, ...)` 把每一對（型別, 名字）套上巨集 `m`：

```c
#define __MAP1(m,t,a,...) m(t,a)
#define __MAP2(m,t,a,...) m(t,a), __MAP1(m,__VA_ARGS__)
#define __MAP3(m,t,a,...) m(t,a), __MAP2(m,__VA_ARGS__)
...
#define __SC_DECL(t, a)	t a
#define __TYPE_AS(t, v)	__same_type((__force t)0, v)
...
#define __TYPE_IS_LL(t) (__TYPE_AS(t, 0LL) || __TYPE_AS(t, 0ULL))
#define __SC_LONG(t, a) __typeof(__builtin_choose_expr(__TYPE_IS_LL(t), 0LL, 0L)) a
#define __SC_CAST(t, a)	(__force t) a
...
#define __SC_ARGS(t, a)	a
#define __SC_TEST(t, a) (void)BUILD_BUG_ON_ZERO(!__TYPE_IS_LL(t) && sizeof(t) > sizeof(long))
```

（`include/linux/syscalls.h:118-135（v6.18）`，節錄）

`__X64_SYS_STUBx` 生出接 `pt_regs` 的那一層，參數從暫存器槽位取：

```c
#define SC_X86_64_REGS_TO_ARGS(x, ...)					\
	__MAP(x,__SC_ARGS						\
		,,regs->di,,regs->si,,regs->dx				\
		,,regs->r10,,regs->r8,,regs->r9)			\
...
#define __SYS_STUBx(abi, name, ...)					\
	long __##abi##_##name(const struct pt_regs *regs);		\
	ALLOW_ERROR_INJECTION(__##abi##_##name, ERRNO);			\
	long __##abi##_##name(const struct pt_regs *regs)		\
	{								\
		return __se_##name(__VA_ARGS__);			\
	}
```

（`arch/x86/include/asm/syscall_wrapper.h:56-59,91-97（v6.18）`）

### 不要在腦中展開，讓前置處理器展開

這種多層巨集在腦中展開很容易出錯。[Ch 13](./13-preprocessor-basics.md) 教過：拿 build 留下的編譯指令，把 `-c -o x.o` 換成 `-E -o x.i`。

```
$ cd ~/code_reading/build
$ cmd=$(grep '^savedcmd_' fs/.read_write.o.cmd | sed 's/^savedcmd_[^=]*:= //')
$ cmd=$(echo "$cmd" | sed -e 's/-Wp,-MMD,[^ ]*//' \
      -e 's/ -c -o fs\/read_write.o/ -E -o \/home\/ypp\/code_reading\/scratch\/29\/read_write.i/')
$ eval "$cmd" && wc -l ~/code_reading/scratch/29/read_write.i
82864 /home/ypp/code_reading/scratch/29/read_write.i
```

`SYSCALL_DEFINE3(read, ...)` 展開成一行很長的程式碼。下面是我們**手動加上換行與縮排**的版本，內容（除了把 `__SC_LONG` 那串 `__typeof(__builtin_choose_expr(...))` 縮寫成它最後的結果 `long`，以及刪掉 `_Static_assert` 細節）都是 `.i` 裡的原文：

```c
static long __se_sys_read(long fd, long buf, long count);
static inline long __do_sys_read(unsigned int fd, char * buf, size_t count);

long __x64_sys_read(const struct pt_regs *regs);
long __x64_sys_read(const struct pt_regs *regs)
{
	return __se_sys_read(regs->di, regs->si, regs->dx);
}

long __ia32_sys_read(const struct pt_regs *regs);
long __ia32_sys_read(const struct pt_regs *regs)
{
	return __se_sys_read((unsigned int)(regs->bx), (unsigned int)(regs->cx),
			     (unsigned int)(regs->dx));
}

static long __se_sys_read(long fd, long buf, long count)
{
	long ret = __do_sys_read(( unsigned int) fd, ( char *) buf, ( size_t) count);
	/* 三個 _Static_assert：檢查每個參數型別不比 long 大（long long 除外） */
	do { } while (0);
	return ret;
}

static inline long __do_sys_read(unsigned int fd, char * buf, size_t count)
{
	return ksys_read(fd, buf, count);
}
```

一個巨集，生出**四個**函式：`__x64_sys_read`（64-bit 入口）、`__ia32_sys_read`（32-bit 程式在 64-bit kernel 上的入口）、`__se_sys_read`（型別收斂）、`__do_sys_read`（本體）。`__user` 標註在 `.i` 裡已經消失了——它只對 sparse 有意義（[Ch 18](./18-annotations-and-sparse.md)）。

### 編譯器把它們壓扁成什麼

```
$ objdump -dr --no-show-raw-insn fs/read_write.o | awk '/<__x64_sys_read>:/,/jmp/'
0000000000003470 <__x64_sys_read>:
    3470:	endbr64
    3474:	call   3479 <__x64_sys_read+0x9>
			3475: R_X86_64_PLT32	__fentry__-0x4
    3479:	mov    0x60(%rdi),%rdx
    347d:	mov    0x68(%rdi),%rsi
    3481:	mov    0x70(%rdi),%edi
    3484:	jmp    3380 <ksys_read>
$ nm fs/read_write.o | grep -E 'sys_read$|ksys_read'
00000000000034a0 T __ia32_sys_read
0000000000003470 T __x64_sys_read
0000000000003380 T ksys_read
```

`__se_sys_read` 與 `__do_sys_read` 在符號表裡**不存在**——都被 inline 掉了。剩下的 `__x64_sys_read` 只有四條有意義的指令：從 `pt_regs` 的三個偏移（`0x70` 是 `di`、`0x68` 是 `si`、`0x60` 是 `dx`）載入參數，然後尾呼叫 `ksys_read`。`call __fentry__` 是 ftrace 的掛鉤點（未啟用時在執行時被改成 nop）。

最有意思的是 `mov 0x70(%rdi),%edi`：載入 `fd` 用的是 32-bit 的 `%edi`，不是 64-bit 的 `%rdi`。x86-64 寫入 32-bit 暫存器會把上半部清零，所以這一條指令就完成了 `(unsigned int)fd` 的轉換——**上半 32 bit 不管 user 放了什麼，都被丟掉了**。這正是 `__se_` 那層要確保的語意。

## 為什麼要有 `__se_` 這一層：sign-extension 的故事

直覺上 `__x64_sys_read` 直接呼叫 `__do_sys_read(regs->di, ...)` 不就好了，何必中間多一層「先收成 `long` 再轉回原型別」？

這要回到 2009 年的 **CVE-2009-0029**。在某些 64-bit 架構（s390、powerpc64、mips64、sparc64）的 C 呼叫慣例裡，`int` 參數由**呼叫端**負責在 64-bit 暫存器裡做好 sign-extension，被呼叫端假設上半部已經是正確的。可是 syscall 的呼叫端是 user space，它可以在暫存器上半部放任何東西。kernel 裡被編譯器信任的「已經擴展好的 int」其實是攻擊者控制的 64-bit 值，在某些程式碼裡就能繞過範圍檢查。

修法是 Heiko Carstens 與 Martin Schwidefsky 的一系列「System call wrappers」patch：

```
$ git log --format='%h' --grep='CVE-2009-0029\] System call wrapper infrastructure'
1a94bc34768e
$ git show -s 1a94bc34768e | sed -n 5,14p
    [CVE-2009-0029] System call wrapper infrastructure

    From: Martin Schwidefsky <schwidefsky@de.ibm.com>

    By selecting HAVE_SYSCALL_WRAPPERS architectures can activate
    system call wrappers in order to sign extend system call arguments.

    All architectures where the ABI defines that the caller of a function
    has to perform sign extension probably need this.
$ git tag --contains 1a94bc34768e | grep -v rc | sort -V | head -1
v2.6.29
```

wrapper 的做法：入口函式（今天的 `__se_sys_*`）把每個參數都宣告成 `long`（或 `long long`），也就是**以完整暫存器寬度接收、不信任任何上半部**，然後在呼叫本體時明確轉型回 `unsigned int` 之類的原型別。轉型由 kernel 自己的程式碼執行，就不依賴呼叫端了。`__SC_LONG` 就是在做「這個型別要用 `long` 還是 `long long` 接」的選擇。把那幾個巨集搬到 user space 看看它的選擇：

```c
/* sc_long.c — 把 include/linux/syscalls.h（v6.18）的 __SC_LONG 搬到 user space 觀察 */
#include <stdio.h>
#include <stddef.h>
#define __same_type(a, b) __builtin_types_compatible_p(typeof(a), typeof(b))
#define __TYPE_AS(t, v)	__same_type((t)0, v)
#define __TYPE_IS_LL(t) (__TYPE_AS(t, 0LL) || __TYPE_AS(t, 0ULL))
#define __SC_LONG(t, a) __typeof(__builtin_choose_expr(__TYPE_IS_LL(t), 0LL, 0L)) a

#define SHOW(t) do { __SC_LONG(t, x) = 0; \
	printf("%-18s sizeof=%zu  -> __se_ 參數型別 sizeof=%zu, %s\n", #t, sizeof(t), sizeof(x), \
	       __same_type(x, 0LL) ? "long long" : "long"); } while (0)

int main(void)
{
	SHOW(unsigned int);
	SHOW(int);
	SHOW(char *);
	SHOW(size_t);
	SHOW(long long);
	SHOW(unsigned long long);

	/* 為什麼要先收 long 再轉回 int：模擬「暫存器上半部是垃圾」 */
	long reg = 0xdeadbeef00000005L;   /* 上半 32 bit 是呼叫端沒清的殘渣 */
	unsigned int fd = (unsigned int)reg;
	printf("reg=%#lx  -> (unsigned int)reg = %u\n", reg, fd);
	return 0;
}
```

```
$ gcc -O2 -Wall -o sc_long sc_long.c && ./sc_long
unsigned int       sizeof=4  -> __se_ 參數型別 sizeof=8, long
int                sizeof=4  -> __se_ 參數型別 sizeof=8, long
char *             sizeof=8  -> __se_ 參數型別 sizeof=8, long
size_t             sizeof=8  -> __se_ 參數型別 sizeof=8, long
long long          sizeof=8  -> __se_ 參數型別 sizeof=8, long long
unsigned long long sizeof=8  -> __se_ 參數型別 sizeof=8, long long
reg=0xdeadbeef00000005  -> (unsigned int)reg = 5
```

所有不比 `long` 大的型別都用 `long` 接；`long long` 系列用 `long long` 接（在 32-bit 架構上它們比 `long` 大，要分開處理）。`__SC_TEST` 則是編譯期保險：如果有人把一個比 `long` 大、又不是 `long long` 的型別（例如一個 struct）當 syscall 參數，`BUILD_BUG_ON_ZERO` 直接讓編譯失敗。

x86-64 的呼叫慣例其實沒有這個問題（被呼叫端不信任上半部），但 kernel 選擇所有架構用同一套巨集，讓 syscall 的定義方式統一。

### 第二次改造：pt_regs-based 入口

v4.17（2018）Dominik Brodowski 又把 x86-64 改成今天的「入口只收一個 `pt_regs *`」形式（commit `fa697140f9a2`）。commit 訊息裡的理由：

```
    This approach avoids leaking random user-provided register content down
    the call chain.
```

舊做法是把 user 的六個暫存器原封不動當成 C 參數傳下去；一個只用三個參數的 syscall，另外三個暫存器裡的 user 值仍然留在暫存器裡，可能被深處的程式碼（或投機執行的 gadget）用到。新做法是入口只從 `pt_regs` 取它需要的欄位，其他暫存器早在 `PUSH_AND_CLEAR_REGS` 時就清掉了。同一系列 commit（`d5a00528b58c`）把名字改成 `__x64_sys_*`，所以 perf 與 stack trace 從 v4.17 起顯示的是 `__x64_sys_read` 而不是 `sys_read`。

## 實驗：繞過 libc，親手按鈴

用 inline 組語直接執行 `syscall` 指令，並故意在 `fd` 的上半部塞垃圾：

```c
/* rawread.c — 不經 libc，直接用 syscall 指令呼叫 read(2)
 * 故意在 fd 的上半 32 bit 塞垃圾，看 kernel 怎麼處理 */
#include <stdio.h>

static long raw_syscall3(long nr, long a0, long a1, long a2)
{
	long ret;
	register long r10 __asm__("r10") = 0;
	__asm__ volatile ("syscall"
		: "=a"(ret)
		: "a"(nr), "D"(a0), "S"(a1), "d"(a2), "r"(r10)
		: "rcx", "r11", "memory");   /* SYSCALL 會蓋掉 rcx（返回位址）與 r11（rflags） */
	return ret;
}

int main(void)
{
	char buf[64];
	long fd = 0xdeadbeef00000000L;      /* 低 32 bit = 0（stdin），上半是垃圾 */
	long n = raw_syscall3(0 /* __NR_read */, fd, (long)buf, sizeof(buf) - 1);
	if (n >= 0) { buf[n] = 0; printf("read(%#lx) = %ld: %s", fd, n, buf); }
	else printf("read(%#lx) = %ld\n", fd, n);

	n = raw_syscall3(0, 0x7fffffffL, (long)buf, 1);   /* 不存在的 fd */
	printf("read(0x7fffffff) = %ld (-EBADF = -9)\n", n);

	n = raw_syscall3(100000, 0, 0, 0);                 /* 不存在的 syscall 號 */
	printf("syscall(100000) = %ld (-ENOSYS = -38)\n", n);
	return 0;
}
```

```
$ gcc -O2 -Wall -o rawread rawread.c && echo "hi from stdin" | ./rawread
read(0xdeadbeef00000000) = 14: hi from stdin
read(0x7fffffff) = -9 (-EBADF = -9)
syscall(100000) = -38 (-ENOSYS = -38)
```

三個結果各自對應前面讀過的程式碼：

1. 上半部是 `0xdeadbeef` 的 fd 被當成 fd 0 正常讀取——`mov 0x70(%rdi),%edi` 丟掉了上半部，`fd` 的型別是 `unsigned int`。如果有人把 `SYSCALL_DEFINE3(read, unsigned int, fd, ...)` 改成 `long fd`，這個呼叫就會回傳 `-EBADF`。
2. 不存在的 fd 回 `-EBADF`：`ksys_read` 裡 `fd_empty(f)` 為真，`ret` 維持初始值 `-EBADF`。
3. 號碼 100000 超出 `NR_syscalls`，`do_syscall_x64` 與 `do_syscall_x32` 都回 false，走 `__x64_sys_ni_syscall`，回 `-ENOSYS`。

（這個實驗跑在 WSL2 的 6.18 kernel 上，它是 Microsoft 在 v6.18 系列上加了 patch 的版本；這三條路徑的程式碼與 vanilla v6.18 相同。kernel 回傳的是負的 errno，libc 的 `read()` 包裝會把它轉成 `-1` 加上 `errno`，我們繞過了 libc，所以看到的是原始值。）

## compat：32-bit 程式走的另一條路

`CONFIG_IA32_EMULATION=y` 時，64-bit kernel 可以跑 32-bit 程式。它們用 `int $0x80` 或 `sysenter`/`syscall` 進入 `entry_64_compat.S`，查的是 `syscall_32.tbl`，號碼完全不同：

```
$ grep -nE '^3\s' arch/x86/entry/syscalls/syscall_32.tbl
18:3	i386	read			sys_read
```

32-bit 的 `read` 是 3 號，不是 0 號。大多數 syscall 在 32-bit 下可以共用同一個實作，前面的展開裡 `__ia32_sys_read` 就是 `SYSCALL_DEFINE3` 順便生出來的：從 `regs->bx`、`regs->cx`、`regs->dx`（i386 的參數暫存器）取值，**先轉成 `unsigned int`**（32-bit 程式的暫存器上半部本來就沒有意義），再交給同一個 `__se_sys_read`。

需要另外寫 compat 版本的，是「參數的記憶體佈局在 32/64-bit 下不同」的 syscall：指標大小不同的 struct、或 64-bit 參數在 32-bit 下要拆成兩個暫存器。table 的第五欄就是 compat 進入點：

```
$ grep -nE '^333\s' arch/x86/entry/syscalls/syscall_32.tbl
348:333	i386	preadv			sys_preadv			compat_sys_preadv
```

對應的實作：

```c
COMPAT_SYSCALL_DEFINE5(preadv, compat_ulong_t, fd,
		const struct iovec __user *, vec,
		compat_ulong_t, vlen, u32, pos_low, u32, pos_high)
{
	loff_t pos = ((loff_t)pos_high << 32) | pos_low;

	return do_preadv(fd, vec, vlen, pos, 0);
}
```

（`fs/read_write.c:1229-1235（v6.18）`）

64-bit 的檔案位置 `pos` 在 i386 上用兩個 32-bit 暫存器傳，compat 版本負責拼回去。上方的註解（`fs/read_write.c:1214-1217`）還提醒：iovec 陣列的格式差異由 `import_iovec` 依 `in_compat_syscall()` 處理，所以這裡假裝收的是原生 `struct iovec`。

讀碼規則：**找 syscall 實作時，先確認你在追哪個 ABI**。同一個名字可能有 `SYSCALL_DEFINE`、`COMPAT_SYSCALL_DEFINE`、架構專屬版本三種，table 決定誰被用。

## 完整走一遍 `read(2)`：從 `syscall` 到 `anon_pipe_read`

把前面拼起來，再用 gdb 拿一份真實的 stack 當證據。initramfs 的 `/init` 只做 `echo hello | cat`，在 `anon_pipe_read` 下斷點（QEMU 開 `-gdb tcp::12829 -S`）：

```
$ gdb -q -batch -iex 'set auto-load safe-path /' -x gdbbt ~/code_reading/build/vmlinux
Breakpoint 1 at 0xffffffff81592740: file /home/ypp/code_reading/linux/fs/pipe.c, line 270.
Breakpoint 1, anon_pipe_read (iocb=0xffa000000020be88, to=0xffa000000020be60) at /home/ypp/code_reading/linux/fs/pipe.c:270
#0  anon_pipe_read (iocb=0xffa000000020be88, to=0xffa000000020be60) at .../fs/pipe.c:270
#1  0xffffffff81585b8b in new_sync_read (ppos=0x0, len=2147479552, buf=0x7fcf627d4000 <error: ...>, filp=0xff11000004e84300) at .../fs/read_write.c:491
#2  vfs_read (file=file@entry=0xff11000004e84300, buf=buf@entry=0x7fcf627d4000 <error: ...>, count=count@entry=65536, pos=pos@entry=0x0) at .../fs/read_write.c:572
#3  0xffffffff81586598 in ksys_read (fd=<optimized out>, buf=0x7fcf627d4000 <error: ...>, count=65536) at .../fs/read_write.c:715
#4  0xffffffff823056c4 in do_syscall_x64 (nr=<optimized out>, regs=0xffa000000020bf58) at .../arch/x86/entry/syscall_64.c:63
#5  do_syscall_64 (regs=0xffa000000020bf58, nr=<optimized out>) at .../arch/x86/entry/syscall_64.c:94
#6  0xffffffff81000130 in entry_SYSCALL_64 () at .../arch/x86/entry/entry_64.S:121
$1 = "cat", '\000' <repeats 12 times>
```

（路徑以 `...` 縮寫；`gdbbt` 指令檔內容：`target remote :12829`、`break anon_pipe_read`、`continue`、`bt`、`p $lx_current().comm`。）

由下往上讀，每一層都能對回本章：

| frame | 函式 | 本章哪一節 |
|---|---|---|
| #6 | `entry_SYSCALL_64`，`entry_64.S:121` 正是 `call do_syscall_64` | 第三步 |
| #5 | `do_syscall_64`，`syscall_64.c:94` 是 `if (!do_syscall_x64(...)` | 第三步 |
| #4 | `do_syscall_x64`（inline），`:63` 是 `regs->ax = x64_sys_call(regs, unr)` | 第三步 |
| （無） | `x64_sys_call` 的 switch 與 `__x64_sys_read` | 第二、四步：switch 被 inline，`__x64_sys_read` 是 `jmp ksys_read` 尾呼叫，不留 frame |
| #3 | `ksys_read`，`:715` 是 `ret = vfs_read(...)` | 下面 |
| #2 | `vfs_read`，`:572` 是 `new_sync_read(...)` | 下面 |
| #1 | `new_sync_read`（inline 進 vfs_read），`:491` 是 `f_op->read_iter` | [Ch 28](./28-ops-tables-indirect-calls.md) |
| #0 | `anon_pipe_read` | [Ch 28](./28-ops-tables-indirect-calls.md) 方法二 |

`__x64_sys_read` 不在 stack 上是很好的一課：**尾呼叫最佳化會讓函式從 backtrace 中消失**。看 stack 覺得「少了一層」時，去看那一層的組語是不是 `jmp` 結尾。另外 `pos=0x0`、`ppos=0x0` 告訴你 pipe 是 stream（`FMODE_STREAM`），沒有檔案位置——對應下面 `file_ppos()` 的邏輯。

中間兩層的原始碼：

```c
/* file_ppos returns &file->f_pos or NULL if file is stream */
static inline loff_t *file_ppos(struct file *file)
{
	return file->f_mode & FMODE_STREAM ? NULL : &file->f_pos;
}

ssize_t ksys_read(unsigned int fd, char __user *buf, size_t count)
{
	CLASS(fd_pos, f)(fd);
	ssize_t ret = -EBADF;

	if (!fd_empty(f)) {
		loff_t pos, *ppos = file_ppos(fd_file(f));
		if (ppos) {
			pos = *ppos;
			ppos = &pos;
		}
		ret = vfs_read(fd_file(f), buf, count, ppos);
		if (ret >= 0 && ppos)
			fd_file(f)->f_pos = pos;
	}
	return ret;
}
```

（`fs/read_write.c:698-720（v6.18）`）

`CLASS(fd_pos, f)(fd)` 是 `include/linux/cleanup.h` 的作用域資源寫法（[Ch 17](./17-kernel-error-conventions.md)）：它把 fd 轉成 `struct file` 並取得參考，函式結束時自動釋放，必要時還會鎖住 `f_pos`。`ksys_read` 先把位置複製到區域變數 `pos`、讀完再寫回，是為了讓 `vfs_read` 不直接改 `file->f_pos`。

```c
ssize_t vfs_read(struct file *file, char __user *buf, size_t count, loff_t *pos)
{
	ssize_t ret;

	if (!(file->f_mode & FMODE_READ))
		return -EBADF;
	if (!(file->f_mode & FMODE_CAN_READ))
		return -EINVAL;
	if (unlikely(!access_ok(buf, count)))
		return -EFAULT;

	ret = rw_verify_area(READ, file, pos, count);
	if (ret)
		return ret;
	if (count > MAX_RW_COUNT)
		count =  MAX_RW_COUNT;

	if (file->f_op->read)
		ret = file->f_op->read(file, buf, count, pos);
	else if (file->f_op->read_iter)
		ret = new_sync_read(file, buf, count, pos);
	else
		ret = -EINVAL;
	if (ret > 0) {
		fsnotify_access(file);
		add_rchar(current, ret);
	}
	inc_syscr(current);
	return ret;
}
```

（`fs/read_write.c:552-580（v6.18）`）

`vfs_read` 是「檢查 → 分派 → 記帳」三段：權限與模式檢查（以 `O_WRONLY` 開的 fd 讀取會在第一個 `if` 回 `-EBADF`）、`access_ok` 確認緩衝區在 user 位址範圍、`rw_verify_area` 讓 LSM（SELinux 等）與 fsnotify 介入；接著依 `.read`／`.read_iter` 分派；最後通知 inotify、更新 `/proc/<pid>/io` 的 `rchar`／`syscr` 計數。`MAX_RW_COUNT` 把單次讀取上限壓在略小於 2 GiB——gdb 輸出裡 `new_sync_read` 的 `len=2147479552` 就是一個例子（那是 frame #1 的 inline 參數值，因最佳化不一定精確，不要過度解讀）。

從這裡往下（page cache、`copy_to_user`）是 [練習 E](./practice-e-trace-read-syscall.md) 的範圍。

## 對比與取捨

| 層 | 名字 | 誰生成 | 參數形式 | 在 backtrace 中看得到嗎 |
|---|---|---|---|---|
| 組語入口 | `entry_SYSCALL_64` | 手寫 `.S` | 暫存器 | 看得到 |
| 分派 | `do_syscall_64` → `x64_sys_call` | 手寫 C + table 生成的 switch | `pt_regs *` + `nr` | `do_syscall_64` 看得到，switch 被 inline |
| ABI 入口 | `__x64_sys_read`、`__ia32_sys_read` | `SYSCALL_DEFINE3` | `pt_regs *` | 常被尾呼叫消掉 |
| 型別收斂 | `__se_sys_read` | `SYSCALL_DEFINE3` | 全部 `long` | 被 inline，符號不存在 |
| 本體 | `__do_sys_read` | 你寫的大括號 | 原型別 | 被 inline，符號不存在 |
| 共用實作 | `ksys_read` | 手寫 | 原型別 | 看得到 |

`ksys_*` 這層存在的理由：kernel 內部（例如早期開機程式碼、compat 路徑）有時也要做同樣的事，但不該經過 `pt_regs` 入口，於是把本體抽出來成普通函式。看到 `__do_sys_foo` 只有一行 `return ksys_foo(...)`，就知道真正的邏輯在 `ksys_foo`。

## 踩雷集錦

1. **「`grep 'sys_read('` 可以找到 syscall 的定義」**：只會找到 `include/linux/syscalls.h:482` 的宣告（`asmlinkage long sys_read(...)`）。定義由 `SYSCALL_DEFINE3(read,` 生成，要搜這個字串。
2. **「kernel 用 `sys_call_table[nr]()` 分派 syscall」**：v6.9 起 x86-64 改成 `x64_sys_call()` 的 switch（commit `1e3ad78334a6`）。`sys_call_table[]` 還在，但只剩 syscall tracing 拿來查位址。讀舊文章時要留意版本。
3. **「strace 顯示的名字就是 kernel 函式名」**：`fstat` 對應 `sys_newfstat`；glibc 的 `fstat()` 實際打的可能是 `newfstatat`；`mmap` 每個架構各一份。以 `syscall_64.tbl` 為準。
4. **「perf 裡看到 `__x64_sys_read` 很少，所以 read 很便宜」**：它只是四條指令的轉接頭，時間都算在 `ksys_read` 以下。反過來，backtrace 裡沒有 `__x64_sys_read` 也不代表沒經過它——尾呼叫讓它不留 frame。
5. **「32-bit 程式的 syscall 號碼和 64-bit 一樣」**：`read` 在 i386 是 3、在 x86-64 是 0、在 x32 是 `0 | __X32_SYSCALL_BIT`（`common`）。seccomp 規則或分析工具搞混 ABI 是經典 bug 來源。
6. **「`SYSCALL_DEFINE` 的參數型別隨便寫，反正都是暫存器」**：型別決定了 `__se_` 層的轉型。`unsigned int fd` 讓上半部垃圾被丟掉（本章實驗），寫成 `long` 就不會；寫成太大的型別會被 `__SC_TEST` 擋在編譯期。

## 進階：再往深一層

- **`syscall_enter_from_user_mode()` 內部**：seccomp 過濾、ptrace 的 syscall-enter stop、audit、`sys_enter` tracepoint 都在這裡依序處理（`include/linux/entry-common.h` 與 `kernel/entry/common.c`）。想知道 strace 為什麼會讓程式變慢、seccomp 怎麼在 syscall 執行前擋下它，從這裡讀。
- **vDSO**：`clock_gettime`、`gettimeofday` 多數情況下不會進 kernel，而是呼叫 kernel 映射到每個行程的一小段程式碼（`arch/x86/entry/vdso/`）。strace 看不到它們，是因為根本沒有 syscall 發生。
- **新增一個 syscall 要改哪些地方**：`Documentation/process/adding-syscalls.rst` 是官方清單：`SYSCALL_DEFINEn`、`include/linux/syscalls.h` 宣告、`include/uapi/asm-generic/unistd.h`、各架構的 `.tbl`、`kernel/sys_ni.c` 的 `COND_SYSCALL`、compat 版本。讀懂它等於反向複習了本章。
- **FRED**：新的 Intel CPU 有 FRED（Flexible Return and Event Delivery），取代 `syscall`/`sysret` 與 IDT 的事件傳遞機制。v6.18 tree 裡的 `arch/x86/entry/entry_64_fred.S` 與 `entry_fred.c` 是另一條入口；在支援 FRED 的硬體上，`entry_SYSCALL_64` 不會被使用。它的細節不在本章範圍。
- **面試題**：「從 user space 呼叫 `read()` 到 kernel 回傳，中間發生了什麼？」本章的 ASCII 圖加上 gdb backtrace 就是一個有深度的答案；再加上 pt_regs 清暫存器、`array_index_nospec`、switch 取代函式指標表三個安全細節，能展現你讀過真實的程式碼。

## 動手練習

1. 對 `openat`、`clone3`、`exit_group` 各用 `rg 'SYSCALL_DEFINE\d\(name,'` 找到實作。`exit_group` 在 table 裡有什麼特別的第六欄？它影響了 `syscall_64.c` 哪一行的巨集？
2. 用本章的 `-E` 技巧展開 `SYSCALL_DEFINE0(getpid)`（在 `kernel/sys.c`），比較它和三參數版本少了哪幾層，對照 `arch/x86/include/asm/syscall_wrapper.h` 的 `__SYS_STUB0` 解釋原因。
3. 改 `rawread.c`：用 `raw_syscall3` 呼叫 `write(2)`（號碼查 table），fd 用 `0xffffffff00000001`。預測結果再執行。
4. 用 gdb 在 `__x64_sys_read` 下斷點（不是 `anon_pipe_read`），印出 `regs->di`、`regs->si`、`regs->dx` 與 `regs->orig_ax`，對照 `entry_64.S` 裡建 `pt_regs` 的那串 `pushq`。

## 本章重點整理

- 找 syscall 實作：`rg 'SYSCALL_DEFINE\d\(name,'`；名字對不上時以 `arch/x86/entry/syscalls/syscall_64.tbl` 為準。
- 路徑：`syscall` → `entry_SYSCALL_64`（存 `pt_regs`、清暫存器）→ `do_syscall_64`（號碼檢查、seccomp/ptrace）→ `x64_sys_call` 的 switch（v6.9 起取代函式指標表）→ `__x64_sys_*` → `__se_sys_*` → `__do_sys_*`（本體）。
- `SYSCALL_DEFINEn` 一次生出 `__x64_`、`__ia32_`、`__se_`、`__do_` 四個函式；`__se_` 以 `long` 接收全部參數再轉回原型別，源自 CVE-2009-0029；`pt_regs` 入口源自 v4.17，避免 user 暫存器內容流進 call chain。
- 讀巨集生成的程式碼，用 `-E` 產生 `.i`，再用 `objdump`／`nm` 看編譯器實際留下什麼。inline 與尾呼叫會讓函式從符號表與 backtrace 消失。

## 自我檢核

- [ ] 不看筆記，能畫出 `read(2)` 從 `syscall` 指令到 `vfs_read` 的每一層，並說出哪幾層在 backtrace 中看不到、為什麼？
- [ ] 如果面試官問「為什麼 `SYSCALL_DEFINE` 要先用 `long` 接參數再轉回 `unsigned int`」，你能從 CVE-2009-0029 講到 x86-64 的 `mov ...,%edi` 嗎？
- [ ] 有人給你一份 2019 年的部落格說「hook `sys_call_table` 就能攔截所有 syscall」，你能指出在 v6.18 x86-64 上這個說法哪裡過時了嗎？
- [ ] 遇到一個 32-bit 程式的 strace，你知道要查哪張 table、哪種巨集定義的實作嗎？
- [ ] 能說出 `ksys_read` 為什麼要把 `f_pos` 複製到區域變數，以及 pipe 為什麼 `ppos` 是 NULL 嗎？

## 延伸閱讀

### 官方文件

- **Documentation/process/adding-syscalls.rst**（[docs.kernel.org/process/adding-syscalls.html](https://docs.kernel.org/process/adding-syscalls.html)）
  - **讀哪裡**：「Generic System Call Implementation」、「x86 System Call Implementation」、「Compatibility System Calls (Generic)」三節。
  - **能學到什麼**：從「新增者」的角度看本章每個元件為什麼存在；compat 一節把「何時需要 `COMPAT_SYSCALL_DEFINE`」講得比任何教材清楚。
  - **前提**：本章的路徑圖。
- **Documentation/arch/x86/entry_64.rst**（v6.18 tree 內）
  - **讀哪裡**：整篇（不長），講 x86-64 有哪些進入 kernel 的路徑（syscall、中斷、例外、NMI）以及 `swapgs` 的難處。
  - **能學到什麼**：把 `entry_SYSCALL_64` 放回「所有 kernel 入口」的全景裡；[Ch 32](./32-async-irq-softirq-workqueue.md) 講中斷時會再用到。

### 技術文章

- **The Definitive Guide to Linux System Calls** — Packagecloud 部落格（2016）
  - **這篇說什麼**：從 `int $0x80`、`sysenter`、`syscall` 到 vDSO，逐一示範每種進入 kernel 的方式與對應的 kernel 程式碼。
  - **讀哪裡**：「64-bit fast system calls」與「vDSO」兩節。
  - **注意**：寫於 v4.x 時代，那時還用 `sys_call_table` 分派、還沒有 `pt_regs` 入口；拿來建立硬體層的直覺，kernel 這一側以本章的 v6.18 程式碼為準。
- **Anatomy of a system call**（兩篇）— David Drysdale（LWN，2014）
  - **這篇說什麼**：第一篇講 `SYSCALL_DEFINEn` 與 x86 入口，第二篇講 compat 與其他架構。
  - **讀哪裡**：第一篇的「SYSCALL_DEFINEn() macros」一段，對照本章的 `.i` 展開，能看到 2014 到 2025 年之間多了哪幾層。
  - **為什麼值得讀**：作者是 syscall 相關 patch 的貢獻者，LWN 的版本是同主題最常被引用的解說。（本課未逐一核對 URL，請在 lwn.net 以標題搜尋。）

### 書籍

- **《The Linux Programming Interface》** — Michael Kerrisk（No Starch Press，2010）
  - **讀哪幾章**：第 3 章「System Programming Concepts」的 3.1 節（syscall 的步驟）與第 4 章（檔案 I/O 的 `read`/`write` 語意）。
  - **定位**：從 user space 這一側把語意講清楚；本章是同一件事的 kernel 側。書雖然出版超過十年，syscall 的使用者語意幾乎沒變，仍然適用。

syscall 進到 `anon_pipe_read` 之後，第一件事是 `mutex_lock(&pipe->mutex)`。為什麼是 mutex 不是 spinlock？這把鎖保護哪些欄位？下一章教你從 lock 讀出這些資訊。

→ [Ch 30 讀有 lock 的程式碼：臨界區、lock 規則與 lockdep 標註](./30-reading-locking-code.md)
