# Ch 36 — Kconfig、Kbuild 與 #ifdef：哪些程式碼真的會被編進去

> **目標**：讀任何一段 kernel 程式碼之前，能先回答「在我的 config 下，這段到底存不存在」；讀得懂 Kconfig 的 `depends on`／`select`／`default`、Makefile 的 `obj-$(CONFIG_FOO)`、`#ifdef` 與 `IS_ENABLED()` 的差別、標頭檔裡的 stub inline 函式；並且會用 `.config`、`.o`、`.i`、`nm` 四種方法實際驗證。
> **鴻溝**：本章主要練**脈絡鴻溝**（這段程式碼存在的前提條件是什麼），也處理一種特殊的**間接鴻溝**：同一個函式名在不同 config 下指向完全不同的實作。

> **環境**：Linux kernel v6.18，x86_64，`~/code_reading/build` 是用 `O=` 建好的 build（`.config` 開頭寫著 `Linux/x86 6.18.0 Kernel Configuration`、gcc 11.4.0）。本章所有 `.config` 查詢、`nm`、`objdump`、`make fs/pipe.i` 都在這份 build 上實際跑過。你的 `.config` 不同，結論就可能不同——這正是本章的重點。

## 為什麼需要這個？

先講一個真實會發生的場景。你在讀 `fs/pipe.c` 的 `anon_pipe_read()`，看到這段：

```c
/* fs/pipe.c:297（v6.18） */
#ifdef CONFIG_WATCH_QUEUE
		if (pipe->note_loss) {
			struct watch_notification n;

			if (total_len < 8) {
				if (ret == 0)
					ret = -ENOBUFS;
				break;
			}
```

你花了半小時搞懂 `note_loss` 是什麼、watch queue 怎麼把通知塞進 pipe、`WATCH_META_LOSS_NOTIFICATION` 何時產生。然後你在 QEMU 裡用 gdb 下斷點，怎麼都停不下來。

原因是：在我們這份 build 裡，

```
$ grep -n 'CONFIG_WATCH_QUEUE' ~/code_reading/build/.config
57:# CONFIG_WATCH_QUEUE is not set
```

這段程式碼**根本沒有被編譯**。你讀的是一段在你的 kernel 裡不存在的程式碼。

kernel 有上萬個組態選項。同一份原始碼，在你的筆電、Android 手機、路由器、雲端伺服器上，編出來的是非常不同的程式。Spinellis 在《Code Reading》第 6 章談組態（configuration）時就提醒過：條件編譯讓「程式碼」和「實際執行的程式」分家，讀者必須先確定自己在讀哪個變體。kernel 把這件事推到了極端。用 `rg` 在 v6.18 的 `.c`／`.h` 裡粗算：

```
$ rg -c '^#\s*if(def)?\s+(defined\()?CONFIG_' -g '*.[ch]' . | awk -F: '{s+=$2} END{print s}'
36868
$ rg -c 'IS_ENABLED\(CONFIG_' -g '*.[ch]' . | awk -F: '{s+=$2} END{print s}'
8969
```

將近 3.7 萬行直接以 `CONFIG_` 開頭的 `#if`／`#ifdef`（還沒算 `#if defined(A) && defined(B)` 這類複合條件與 `#else` 分支），近九千行 `IS_ENABLED()`。這是讀 kernel 時最大的陷阱之一，而且陷阱是靜默的：沒有任何東西會提醒你「這段不存在」。

## 先建立直覺

把 kernel 的組態機制想成三層篩子，每一層都在決定「什麼東西留下來」：

```
  Kconfig 檔（宣告有哪些選項、彼此的依賴）
      │  make defconfig / menuconfig / olddefconfig
      ▼
  .config（每個選項的值：y / m / 沒設）
      │  自動產生
      ├──────────────► include/generated/autoconf.h   （給 C 前置處理器：#define CONFIG_FOO 1）
      └──────────────► include/config/auto.conf       （給 Makefile：CONFIG_FOO=y）
                              │
   第一層篩子：檔案級         ▼
   Makefile: obj-$(CONFIG_FOO) += foo.o   → foo.c 整個檔案編或不編
                              │
   第二層篩子：程式碼區塊級   ▼
   #ifdef CONFIG_FOO ... #endif           → 前置處理器把區塊刪掉，編譯器看不到
                              │
   第三層篩子：編譯器最佳化   ▼
   if (IS_ENABLED(CONFIG_FOO)) { ... }    → 編譯器看得到、會檢查，然後當死碼刪掉
```

三層的差別在「誰負責刪」：Make、前置處理器、還是編譯器。讀碼時這決定了你要去哪裡查證。

## Kconfig 語法讀法

先讀一個真實的、和 pipe 有關的選項：

```
# init/Kconfig:486（v6.18）
config WATCH_QUEUE
	bool "General notification queue"
	default n
	help

	  This is a general notification queue for the kernel to pass events to
	  userspace by splicing them into pipes.  It can be used in conjunction
	  with watches for key/keyring change notifications and device
	  notifications.

	  See Documentation/core-api/watch_queue.rst
```

逐行：

- `config WATCH_QUEUE`：符號名。C 程式碼裡會變成 `CONFIG_WATCH_QUEUE`。
- `bool "..."`：型別是布林（只能 y 或不設），引號裡的字串是 menuconfig 顯示的提示（prompt）。**有 prompt 的選項，使用者才看得到、改得了。**
- `default n`：預設不開。這就是我們的 build 裡它沒開的原因。

再看一個比較複雜的：

```
# fs/ext4/Kconfig:2（v6.18）
config EXT4_FS
	tristate "The Extended 4 (ext4) filesystem"
	select BUFFER_HEAD
	select JBD2
	select CRC16
	select CRC32
	select FS_IOMAP
	select FS_ENCRYPTION_ALGS if FS_ENCRYPTION
	help
	  ...

config EXT4_USE_FOR_EXT2
	bool "Use ext4 for ext2 file systems"
	depends on EXT4_FS
	depends on EXT2_FS=n
	default y
```

- `tristate`：三態——`y`（編進 vmlinux）、`m`（編成 module）、不設。
- `select JBD2`：「我開了，JBD2 也必須開」。這是**反向依賴**：由上往下強迫。
- `select ... if FS_ENCRYPTION`：條件式 select。
- `depends on EXT4_FS`：「EXT4_FS 沒開，我就不能開（連選項都不會出現）」。這是**正向依賴**：由下往上限制。
- `depends on EXT2_FS=n`：可以比較值。只有在沒有獨立 ext2 driver 時，才讓 ext4 接手 ext2。

`depends on` 和 `select` 方向相反，這是讀 Kconfig 最容易搞混的地方：

```
        depends on                        select
  A ──────────────► B             A ──────────────► B
  「A 要能出現，B 必須已開」      「A 開了，就把 B 強制打開」
   使用者看到的選項變少             使用者不用管 B（B 通常沒有 prompt）
```

`Documentation/kbuild/kconfig-language.rst:148` 對 select 有一段明確警告：

```
	select should be used with care. select will force
	a symbol to a value without visiting the dependencies.
	By abusing select you are able to select a symbol FOO even
	if FOO depends on BAR that is not set.
	In general use select only for non-visible symbols
	(no prompts anywhere) and for symbols with no dependencies.
```

「非可見符號」（non-visible symbol）的真實例子：

```
# fs/Kconfig:22（v6.18）
config FS_STACK
	bool
```

只有 `bool`、沒有 prompt。使用者在 menuconfig 裡**永遠看不到它**。它唯一被打開的方式是被別人 select：

```
$ rg -n 'select FS_STACK' -g 'Kconfig*' .
./fs/fuse/Kconfig:61:	select FS_STACK
./fs/overlayfs/Kconfig:4:	select FS_STACK
```

而 `fs/Makefile:43` 寫著 `obj-$(CONFIG_FS_STACK) += backing-file.o`。所以讀碼時看到 `fs/backing-file.c`，要問的問題是「FUSE 或 overlayfs 有沒有開」，不是「FS_STACK 有沒有開」。我們的 build 兩個都沒開，於是：

```
$ ./scripts/config --file ../build/.config -s FS_STACK
undef
$ ls ../build/fs/backing-file.o
ls: cannot access ...: No such file or directory
```

第三種常見寫法是「只有在 EXPERT 模式才問你」：

```
# init/Kconfig:1809（v6.18）
config EVENTFD
	bool "Enable eventfd() system call" if EXPERT
	default y
```

`if EXPERT` 掛在 prompt 上：`CONFIG_EXPERT` 沒開時，prompt 不出現，值就固定是 `default y`。我們的 build 是 `# CONFIG_EXPERT is not set`、`CONFIG_EVENTFD=y`。這類選項的意思是「正常人不該關它，但嵌入式系統可以為了省空間關掉」。

## 從 .config 到 C 與 Make

`.config` 會被轉成兩個檔，一個給 C、一個給 Make：

```
$ grep -n 'CONFIG_EXT4_FS\b' build/include/generated/autoconf.h build/include/config/auto.conf
include/generated/autoconf.h:1379:#define CONFIG_EXT4_FS 1
include/config/auto.conf:1379:CONFIG_EXT4_FS=y
```

規則（`scripts/kconfig/` 產生）：

| `.config` 的值 | `autoconf.h` 裡 | `auto.conf` 裡 |
|---|---|---|
| `CONFIG_FOO=y` | `#define CONFIG_FOO 1` | `CONFIG_FOO=y` |
| `CONFIG_FOO=m` | `#define CONFIG_FOO_MODULE 1`（**沒有** `CONFIG_FOO`） | `CONFIG_FOO=m` |
| `# CONFIG_FOO is not set` | 什麼都沒有 | 什麼都沒有 |

第二列是陷阱：模組化的選項，`#ifdef CONFIG_FOO` 是**假**的。這就是 `IS_ENABLED()` 存在的原因之一。

`autoconf.h` 被 `include/linux/kconfig.h` 引入，而 `kconfig.h` 透過 `-include` 被強制加進每個 kernel 編譯單元（[Ch 35](./35-kernel-source-tree-map.md) 看過 `.pipe.o.cmd` 裡的那三個 `-include`）。所以 kernel 的 `.c` 檔不用 include 任何東西就能用 `CONFIG_*` 和 `IS_ENABLED()`。

## 第一層篩子：Makefile 的 obj-$(CONFIG_FOO)

`fs/Makefile`（v6.18）開頭：

```make
obj-y :=	open.o read_write.o file_table.o super.o \
		char_dev.o stat.o exec.o pipe.o namei.o fcntl.o \
...
obj-$(CONFIG_BUFFER_HEAD)	+= buffer.o mpage.o
obj-$(CONFIG_PROC_FS)		+= proc_namespace.o
obj-$(CONFIG_LEGACY_DIRECT_IO)	+= direct-io.o
obj-y				+= notify/
obj-$(CONFIG_EPOLL)		+= eventpoll.o
obj-y				+= anon_inodes.o
obj-$(CONFIG_SIGNALFD)		+= signalfd.o
obj-$(CONFIG_TIMERFD)		+= timerfd.o
obj-$(CONFIG_EVENTFD)		+= eventfd.o
obj-$(CONFIG_USERFAULTFD)	+= userfaultfd.o
```

這個技巧的原理是 Make 的變數展開：`CONFIG_EVENTFD=y` 時，`obj-$(CONFIG_EVENTFD)` 變成 `obj-y`（編進 vmlinux）；`=m` 時變成 `obj-m`（編成 module）；沒設時變成 `obj-`，一個沒人讀的變數，檔案就被丟掉。不需要任何 `if`。

讀碼結論：`pipe.o` 在 `obj-y` 裡，**永遠會編**；`eventfd.o` 看 `CONFIG_EVENTFD`；`userfaultfd.o` 看 `CONFIG_USERFAULTFD`。

子系統的 Makefile 常用「複合物件」：

```make
# fs/ext4/Makefile（v6.18）
obj-$(CONFIG_EXT4_FS) += ext4.o

ext4-y	:= balloc.o bitmap.o block_validity.o dir.o ext4_jbd2.o extents.o \
		...
ext4-$(CONFIG_EXT4_FS_POSIX_ACL)	+= acl.o
ext4-$(CONFIG_EXT4_FS_SECURITY)		+= xattr_security.o
ext4-$(CONFIG_FS_VERITY)		+= verity.o
ext4-$(CONFIG_FS_ENCRYPTION)		+= crypto.o
```

`ext4.o` 是由 `ext4-y` 列出的所有 `.o` 連結而成，其中某些檔案又各自受別的選項控制。`fs/ext4/verity.c` 存不存在，要同時看 `EXT4_FS` 和 `FS_VERITY` 兩個選項。

### 實測：哪些 fs/*.c 在這份 build 裡沒被編

```
$ cd ~/code_reading/build
$ for f in ../linux/fs/*.c; do b=$(basename $f .c); [ -e fs/$b.o ] || echo -n "$b "; done; echo
backing-file binfmt_elf_fdpic binfmt_flat bpf_fs_kfuncs dax userfaultfd
```

`fs/` 底下 71 個 `.c` 有 6 個沒被編譯。逐一對回 Makefile 就能找到原因（`userfaultfd` ← `CONFIG_USERFAULTFD` 沒開；`backing-file` ← `CONFIG_FS_STACK` 沒被 select；`dax` ← `CONFIG_FS_DAX`；`bpf_fs_kfuncs` ← `CONFIG_BPF_LSM`……）。這個五秒鐘的迴圈，是讀一個陌生子系統前最划算的檢查。

## 第二層篩子：#ifdef 與 stub inline 函式

回到 pipe。`struct pipe_inode_info` 裡有兩個欄位是條件存在的：

```c
/* include/linux/pipe_fs_i.h:107（v6.18） */
	bool poll_usage;
#ifdef CONFIG_WATCH_QUEUE
	bool note_loss;
#endif
	struct page *tmp_page[2];
	...
	struct user_struct *user;
#ifdef CONFIG_WATCH_QUEUE
	struct watch_queue *watch_queue;
#endif
};
```

**資料結構的形狀本身就隨 config 改變**。這會一路影響到 `sizeof`、欄位 offset，以及 pahole 的輸出（[Ch 50](./50-compiler-as-reading-tool.md)）。

既然欄位不一定存在，所有用到它的地方都必須跟著條件編譯。kernel 的標準做法是把條件集中在標頭檔的一個小 inline 函式裡，讓呼叫者不用寫 `#ifdef`：

```c
/* include/linux/pipe_fs_i.h:170（v6.18） */
static inline bool pipe_has_watch_queue(const struct pipe_inode_info *pipe)
{
#ifdef CONFIG_WATCH_QUEUE
	return pipe->watch_queue != NULL;
#else
	return false;
#endif
}
```

更常見的版本是整組 API 在 `#else` 分支提供空殼，叫做 **stub**（樁函式）：

```c
/* include/linux/watch_queue.h（v6.18），第 17 行 #ifdef CONFIG_WATCH_QUEUE 的 #else 分支 */
#else
static inline int watch_queue_init(struct pipe_inode_info *pipe)
{
	return -ENOPKG;
}

#endif
```

功能沒開時，呼叫者拿到 `-ENOPKG`（「套件未安裝」），而不是連結錯誤。更大的例子是 userfaultfd，`include/linux/userfaultfd_k.h:294` 的 `#else /* CONFIG_USERFAULTFD */` 一路到 `:418`，一百多行全是 stub：

```c
/* include/linux/userfaultfd_k.h:297（v6.18） */
static inline vm_fault_t handle_userfault(struct vm_fault *vmf,
				unsigned long reason)
{
	return VM_FAULT_SIGBUS;
}
...
static inline bool userfaultfd_missing(struct vm_area_struct *vma)
{
	return false;
}
```

而 `mm/memory.c` 的 page fault 路徑照常呼叫它們，沒有任何 `#ifdef`：

```c
/* mm/memory.c:5171（v6.18） */
		/* Deliver the page fault to userland, check inside PT lock */
		if (userfaultfd_missing(vma)) {
			pte_unmap_unlock(vmf->pte, vmf->ptl);
			return handle_userfault(vmf, VM_UFFD_MISSING);
		}
```

讀碼陷阱：你用 ctags／cscope 跳到 `handle_userfault` 的定義，工具可能帶你到 `fs/userfaultfd.c` 那個幾百行的真實實作；但在我們這份 build 裡，被呼叫的其實是上面那個一行回傳 `VM_FAULT_SIGBUS` 的 stub——而且因為 `userfaultfd_missing()` 恆為 `false`，連呼叫都不會發生。這就是本章開頭說的「同名不同實作」的間接鴻溝。clangd 搭配 `compile_commands.json`（[Ch 49](./49-clangd-lsp.md)）會跳到正確的那個，因為它用的是真實的編譯參數；純文字索引的 ctags 不會。

整組 syscall 也有對應的 stub 機制。`kernel/sys_ni.c`（ni = not implemented）列出可選的 syscall：

```
$ grep -n 'eventfd\|userfaultfd' kernel/sys_ni.c
54:COND_SYSCALL(eventfd2);
256:COND_SYSCALL(userfaultfd);
317:COND_SYSCALL(eventfd);
```

x86 的 `COND_SYSCALL` 最後展開成一個 weak 函式（`arch/x86/include/asm/syscall_wrapper.h:99`）：

```c
#define __COND_SYSCALL(abi, name)					\
	__weak long __##abi##_##name(const struct pt_regs *__unused);	\
	__weak long __##abi##_##name(const struct pt_regs *__unused)	\
	{								\
		return sys_ni_syscall();				\
	}
```

功能有開時，真實實作是強符號，蓋掉 weak 版本；沒開時 weak 版本留下來，回傳 `-ENOSYS`。直接在 vmlinux 上驗證：

```
$ nm build/vmlinux | grep -E ' __x64_sys_(userfaultfd|eventfd2)$'
ffffffff815f2300 T __x64_sys_eventfd2
ffffffff812d3330 W __x64_sys_userfaultfd
```

`T` 是一般的文字段符號（真實實作），`W` 是 weak（stub）。這份 vmlinux 裡共有 29 個 `W __x64_sys_*`——29 個「存在於 syscall 表、但呼叫只會拿到 `-ENOSYS`」的系統呼叫。

## 第三層篩子：IS_ENABLED() 與為什麼偏好它

`IS_ENABLED(CONFIG_FOO)` 是一個在前置處理階段就變成 `0` 或 `1` 的巨集。它的實作是 kernel 最聰明的巨集技巧之一：

```c
/* include/linux/kconfig.h:13（v6.18） */
#define __ARG_PLACEHOLDER_1 0,
#define __take_second_arg(__ignored, val, ...) val
...
#define __is_defined(x)			___is_defined(x)
#define ___is_defined(val)		____is_defined(__ARG_PLACEHOLDER_##val)
#define ____is_defined(arg1_or_junk)	__take_second_arg(arg1_or_junk 1, 0)
```

推導 `__is_defined(CONFIG_FOO)`：

```
情況 A：#define CONFIG_FOO 1
  ___is_defined(1)                       ← CONFIG_FOO 先被展開成 1
  ____is_defined(__ARG_PLACEHOLDER_1)    ← ## 黏成這個名字
  ____is_defined(0,)                     ← 它剛好是一個巨集，展開成 "0,"
  __take_second_arg(0, 1, 0)             ← 多出一個逗號，參數往後推
  → 1

情況 B：CONFIG_FOO 沒定義
  ___is_defined(CONFIG_FOO)              ← 沒東西可展開
  ____is_defined(__ARG_PLACEHOLDER_CONFIG_FOO)   ← 不是巨集，原樣留著
  __take_second_arg(__ARG_PLACEHOLDER_CONFIG_FOO 1, 0)
  → 0
```

關鍵在 `__ARG_PLACEHOLDER_1` 展開成 `0,`——多出來的**逗號**把參數位置往後擠一格。然後 `IS_ENABLED` 把 builtin 和 module 兩種情況 OR 起來：

```c
#define IS_BUILTIN(option) __is_defined(option)
#define IS_MODULE(option) __is_defined(option##_MODULE)
#define IS_ENABLED(option) __or(IS_BUILTIN(option), IS_MODULE(option))
```

不要相信推導，跑一次。把這些巨集原樣複製到 user space（`~/code_reading/scratch/36/isen.c`）：

```c
#include <stdio.h>
/* 從 include/linux/kconfig.h（v6.18）複製的巨集 */
#define __ARG_PLACEHOLDER_1 0,
#define __take_second_arg(__ignored, val, ...) val
#define __or(x, y)			___or(x, y)
#define ___or(x, y)			____or(__ARG_PLACEHOLDER_##x, y)
#define ____or(arg1_or_junk, y)		__take_second_arg(arg1_or_junk 1, y)
#define __is_defined(x)			___is_defined(x)
#define ___is_defined(val)		____is_defined(__ARG_PLACEHOLDER_##val)
#define ____is_defined(arg1_or_junk)	__take_second_arg(arg1_or_junk 1, 0)
#define IS_BUILTIN(option) __is_defined(option)
#define IS_MODULE(option) __is_defined(option##_MODULE)
#define IS_ENABLED(option) __or(IS_BUILTIN(option), IS_MODULE(option))

/* 模擬 autoconf.h：=y 產生 CONFIG_X 1；=m 產生 CONFIG_X_MODULE 1；沒設什麼都不產生 */
#define CONFIG_FOO 1
#define CONFIG_BAR_MODULE 1

int main(void)
{
	printf("FOO(y)=%d BAR(m)=%d BAZ(unset)=%d\n",
	       IS_ENABLED(CONFIG_FOO), IS_ENABLED(CONFIG_BAR), IS_ENABLED(CONFIG_BAZ));
	printf("IS_BUILTIN(BAR)=%d IS_MODULE(BAR)=%d\n",
	       IS_BUILTIN(CONFIG_BAR), IS_MODULE(CONFIG_BAR));
	return 0;
}
```

```
$ gcc -Wall -o isen isen.c && ./isen
FOO(y)=1 BAR(m)=1 BAZ(unset)=0
IS_BUILTIN(BAR)=0 IS_MODULE(BAR)=1
$ gcc -E isen.c | tail -8 | head -5
int main(void)
{
 printf("FOO(y)=%d BAR(m)=%d BAZ(unset)=%d\n",
        1, 1, 0);
```

`gcc -E` 證實：到了編譯器眼中，`IS_ENABLED(...)` 已經是字面常數 `1`、`1`、`0`。

### 為什麼 kernel 偏好 IS_ENABLED()

`Documentation/process/coding-style.rst:1181` 寫得很清楚：

```
Within code, where possible, use the IS_ENABLED macro to convert a Kconfig
symbol into a C boolean expression, and use it in a normal C conditional:
...
The compiler will constant-fold the conditional away, and include or exclude
the block of code just as with an #ifdef, so this will not add any runtime
overhead.  However, this approach still allows the C compiler to see the code
inside the block, and check it for correctness (syntax, types, symbol
references, etc).  Thus, you still have to use an #ifdef if the code inside the
block references symbols that will not exist if the condition is not met.
```

兩個理由：一、`#ifdef` 區塊在 config 關閉時**編譯器完全看不到**，裡面打錯字、型別錯誤、用了已經改名的 API，只要沒人用那個 config 編譯，就永遠不會被發現（「bit rot」）。二、`=m` 的情況 `#ifdef CONFIG_FOO` 會判斷錯。

最後一句話同樣重要：**引用到不一定存在的符號時，還是得用 `#ifdef`**。這正好解釋了 `fs/pipe.c` 為什麼用 `#ifdef CONFIG_WATCH_QUEUE` 而不是 `IS_ENABLED()`——`note_loss` 欄位本身就是條件存在的。用一個最小重現看清楚（`scratch/36/deadcode.c`）：

```c
struct pipe { int head; };   /* 注意：沒有 note_loss 欄位 */

int read_ifdef(struct pipe *p)
{
#ifdef CONFIG_WATCH_QUEUE
	if (p->note_loss)        /* 欄位不存在，但沒人發現 */
		return -1;
#endif
	return p->head;
}

int read_isen(struct pipe *p)
{
	if (IS_ENABLED(CONFIG_WATCH_QUEUE) && p->note_loss)  /* 編譯器一定會檢查 */
		return -1;
	return p->head;
}
```

```
$ gcc -Wall -o deadcode deadcode.c
deadcode.c: In function ‘read_isen’:
deadcode.c:22:48: error: ‘struct pipe’ has no member named ‘note_loss’
   22 |         if (IS_ENABLED(CONFIG_WATCH_QUEUE) && p->note_loss)  /* 編譯器一定會檢查 */
      |                                                ^~
```

`read_ifdef` 裡同樣的錯誤沒有被報出來——這就是 bit rot 的樣子。而 `read_isen` 的錯誤正是 IS_ENABLED 的「代價」：條件不成立時，程式碼還是必須能編譯。在真實 kernel 裡，解法就是前面那個 `pipe_has_watch_queue()`：把對條件欄位的存取包進一個有 stub 的 inline 函式，呼叫端寫一般的 `if`。

kernel 裡 IS_ENABLED 的典型用法（v6.18 實例）：

```c
/* fs/buffer.c:2399 */
	/* This is needed for ext4. */
	if (IS_ENABLED(CONFIG_FS_VERITY) && IS_VERITY(inode))
		limit = inode->i_sb->s_maxbytes;

/* fs/libfs.c:78 */
	if (IS_ENABLED(CONFIG_UNICODE) && IS_CASEFOLDED(dir))
		return NULL;
```

它也能用在前置處理器條件裡：`fs/libfs.c:1792` 有 `#if IS_ENABLED(CONFIG_UNICODE)`，這是在「需要 `#if`，但選項可能是 `m`」時的正確寫法。

## 實際確認：某段 code 在我的 build 裡存不存在

四種方法，由粗到細。全部以 `CONFIG_WATCH_QUEUE` 那段 pipe 程式碼為例。

**方法一：查 `.config`**（或用 `scripts/config` 一次查多個）

```
$ ./scripts/config --file ../build/.config -s WATCH_QUEUE -s EXT4_FS -s USERFAULTFD -s FS_STACK -s SYSCTL -s NOSUCH
n
y
n
undef
y
undef
```

`n` 是「被明確設成不開」，`undef` 是「這個符號在 `.config` 裡根本沒出現」——對非可見符號（`FS_STACK`）或打錯名字（`NOSUCH`）都是 `undef`，所以 `undef` 要多看一眼。

**方法二：看 `.o` 有沒有產生**（檔案級）

```
$ ls build/kernel/watch_queue.o
ls: cannot access 'kernel/watch_queue.o': No such file or directory
```

`kernel/Makefile:139` 是 `obj-$(CONFIG_WATCH_QUEUE) += watch_queue.o`，這和 `.config` 吻合。

**方法三：看前置處理後的 `.i`**（區塊級）

Kbuild 支援單檔目標，直接要 `.i`：

```
$ cd ~/code_reading/linux
$ time make O=../build fs/pipe.i
  ...
  CPP     fs/pipe.i
real	0m1.942s
$ wc -l ../build/fs/pipe.i
81576 ../build/fs/pipe.i
$ grep -c 'note_loss' ../build/fs/pipe.i
0
$ grep -n 'poll_usage' ../build/fs/pipe.i | head -2
71188: bool poll_usage;
80674: if (was_empty || pipe->poll_usage)
```

2,000 行左右的 `fs/pipe.c` 展開成 81,576 行（大部分是標頭）。`note_loss` 一次都沒出現——`#ifdef CONFIG_WATCH_QUEUE` 的區塊確實被刪了。而 stub 函式在 `.i` 裡長這樣：

```c
static inline __attribute__((__gnu_inline__)) __attribute__((__unused__)) __attribute__((no_instrument_function)) bool pipe_has_watch_queue(const struct pipe_inode_info *pipe)
{



 return false;
```

`#ifdef` 分支留下的空行還在，`return false;` 是唯一剩下的東西。`.i` 是「前置處理器說了算」的最終版本，所有 `#ifdef` 疑問都可以在這裡定案（`make` 的這個單檔目標，[Ch 50](./50-compiler-as-reading-tool.md) 會再用到）。

**方法四：看機器碼**（編譯器最佳化之後）

`.i` 裡 `pipe_update_tail()` 還是有 `if (pipe_has_watch_queue(pipe)) { spin_lock_irq(...); ... }`——前置處理器只刪了 `#ifdef` 那三行，`if` 本身還在。真正刪掉它的是編譯器：`pipe_has_watch_queue()` inline 後恆為 `false`，整個分支是死碼。驗證方法是數 `fs/pipe.o` 裡對 spinlock 函式的呼叫重定位：

```
$ grep -n 'spin_lock_irq\|spin_unlock_irq' fs/pipe.c
250:		spin_lock_irq(&pipe->rd_wait.lock);
256:		spin_unlock_irq(&pipe->rd_wait.lock);
1305:	spin_lock_irq(&pipe->rd_wait.lock);
1312:		spin_unlock_irq(&pipe->rd_wait.lock);
1353:	spin_unlock_irq(&pipe->rd_wait.lock);
$ objdump -dr build/fs/pipe.o | grep -oE 'R_X86_64_PLT32\s+\S*spin\S*' | sort | uniq -c
      5 R_X86_64_PLT32	_raw_spin_lock-0x4
      1 R_X86_64_PLT32	_raw_spin_lock_irq-0x4
     10 R_X86_64_PLT32	_raw_spin_unlock-0x4
      2 R_X86_64_PLT32	_raw_spin_unlock_irq-0x4
```

原始碼有 2 個 `spin_lock_irq`、3 個 `spin_unlock_irq`；目的檔只剩 1 個和 2 個。少掉的恰好是第 250、256 行那一對——`pipe_update_tail()` 的 watch queue 分支。再補一個符號層的確認：

```
$ nm build/fs/pipe.o | grep -i watch; echo "exit=$?"
exit=1
```

`fs/pipe.o` 裡沒有任何和 watch 有關的符號。

## 對比與取捨

| 機制 | 誰刪掉程式碼 | 編譯器會檢查關閉分支嗎 | `=m` 時正確嗎 | 什麼時候必須用 |
|---|---|---|---|---|
| `obj-$(CONFIG_X)` | Make | 不會（整個檔案不編） | 正確（變成 `obj-m`） | 整個功能獨立成檔 |
| `#ifdef CONFIG_X` | 前置處理器 | **不會** | **錯**（只有 `CONFIG_X_MODULE`） | 引用條件存在的欄位、型別、函式；struct 定義 |
| `#if IS_ENABLED(CONFIG_X)` | 前置處理器 | 不會 | 正確 | 需要 `#if` 但選項是 tristate |
| `if (IS_ENABLED(CONFIG_X))` | 編譯器（常數折疊） | **會** | 正確 | 預設做法：程式碼在兩種 config 下都能編 |
| stub inline 函式 | 編譯器 | 會（呼叫端） | 看 stub 怎麼寫 | 一組 API 給多個子系統用 |
| `COND_SYSCALL` weak 符號 | 連結器 | — | — | 可選的 syscall |

## 踩雷集錦

1. **「grep 得到就代表存在」**：`rg` 是對原始碼搜尋，完全不知道你的 config。讀到 `#ifdef CONFIG_` 包住的程式碼，先查 `.config`；讀一整個陌生目錄，先跑一次「哪些 `.c` 沒有 `.o`」的迴圈。
2. **「`#ifdef CONFIG_FOO` 等於 FOO 有開」**：`CONFIG_FOO=m` 時 `autoconf.h` 只有 `CONFIG_FOO_MODULE`，`#ifdef CONFIG_FOO` 為假。看到 tristate 選項配 `#ifdef`，要懷疑它是不是故意只在 built-in 時生效（有時是故意的，例如 module 無法提供某個早期開機功能）。
3. **「跳到定義就是被呼叫的那個」**：純文字索引（ctags、elixir 預設）可能帶你到真實實作，而你的 build 用的是 stub。用 `compile_commands.json` 驅動的 clangd，或直接看 `.i`。
4. **「`depends on` 和 `select` 差不多」**：方向相反。`select` 不會檢查被選者的依賴，所以 Kconfig 文件明說它只該用在非可見、無依賴的符號上。讀到一個 `select` 了有 prompt 符號的寫法，那可能是個潛在的組態 bug。
5. **「`undef` 就是沒開」**：通常是，但也可能是你把名字打錯（`scripts/config -s NOSUCH` 一樣回 `undef`）。確認符號拼字：`rg -n '^config WATCH_QUEUE' -g 'Kconfig*'`。
6. **「`.i` 裡的 `if` 還在，所以程式碼會執行」**：`.i` 只反映前置處理器。`if (0)` 形態的死碼要到 `.o` 才會消失，最終裁判是 `objdump` 或 `nm`。

## 進階：再往深一層

- **`make O=... fs/pipe.s`、`fs/pipe.o`、`fs/pipe.lst`**：頂層 `Makefile:301` 的 `single-targets := %.a %.i %.ko %.lds %.ll %.lst %.mod %.o %.rsi %.s %/` 列出所有支援的單檔目標；`.lst` 是含原始碼對照的反組譯（`Makefile:1671` 的 help 文字）。讀一段 config 敏感的程式碼時，`.s` 可以直接看到哪些分支留下來（[Ch 50](./50-compiler-as-reading-tool.md)）。
- **`IS_REACHABLE()`**：`include/linux/kconfig.h` 裡還有這一個，用於「built-in 程式碼呼叫一個可能是 module 的功能」——built-in 碼呼叫不到 module 裡的符號，所以這時 `IS_ENABLED` 為真但呼叫會連結失敗。讀到 `IS_REACHABLE` 就知道作者在處理 y/m 混搭。
- **用多個 config 比對**：`make O=../build-allno allnoconfig`、`tinyconfig`、`allmodconfig` 各產生一份 `.config`，再對同一個檔案產生 `.i` 後 diff，可以一次看出某段程式碼在不同 config 下的所有變體。本章沒有實際這樣做（會再跑一次完整 build 的前置步驟，與其他人共用的機器不適合），但就我們對 Kbuild 的理解，產生單一 `.i` 前要完成的是 `prepare` 階段（產生標頭與 autoconf），不必編譯整個 kernel，成本比完整 build 低很多。**本段未實測**。
- **menuconfig 的搜尋**：在 `make menuconfig` 裡按 `/` 搜尋符號，會顯示 `Depends on:`、`Selected by:`、`Defined at:`。這是理解一個選項為什麼是現在這個值最快的方法。本環境是非互動 shell，**未實測**；非互動的替代方案是直接 `rg -n 'select FOO\b|depends on.*\bFOO\b' -g 'Kconfig*'`。
- **`#endif /* CONFIG_FOO */` 註解**：coding style 要求長的 `#if` 區塊在 `#endif` 後註明條件（`include/linux/userfaultfd_k.h:418` 就是），讀碼時遇到巢狀 `#ifdef` 迷路，先找這些註解。

## 動手練習

1. 在你的 build 上跑「哪些 `.c` 沒有 `.o`」迴圈，對象換成 `mm/` 和 `kernel/`。挑三個沒被編的檔案，從 Makefile 追到 Kconfig，說出它們為什麼沒開。
2. `fs/eventfd.c` 在我們的 build 裡有被編（`CONFIG_EVENTFD=y`）。找出 `include/linux/eventfd.h` 的 stub 分支，說明 AIO 在 `!CONFIG_EVENTFD` 時會發生什麼事（註解裡有一句很直白的評語）。
3. `fs/pipe.c:80` 有一個 `#ifdef CONFIG_PROVE_LOCKING`。查我們的 `.config`，再用 `make O=../build fs/pipe.i` 和 grep 確認那段在 `.i` 裡是否存在。
4. 把 `scratch/36/isen.c` 改成 `#define CONFIG_BAR_MODULE 1` 並且也加上 `#define CONFIG_BAR 1`，`IS_ENABLED(CONFIG_BAR)` 會是多少？用 `gcc -E` 驗證你的預測，並解釋 `__or` 是怎麼運作的。

## 本章重點整理

- 同一份 kernel 原始碼在不同 config 下是不同的程式；讀任何被條件包住的程式碼前，先確定它在你的 build 裡存在。
- 三層篩子：Makefile 的 `obj-$(CONFIG_X)`（檔案級）、`#ifdef`（區塊級，編譯器看不到）、`IS_ENABLED()`（編譯器看得到、再當死碼刪掉）。
- `IS_ENABLED()` 靠 `__ARG_PLACEHOLDER_1` 展開成 `0,` 的逗號技巧在前置處理階段變成常數；偏好它是因為關閉分支仍會被型別檢查，且正確處理 `=m`。
- 條件存在的欄位逼出 `#ifdef`，kernel 用 stub inline 函式把它集中在標頭裡；ctags 可能帶你到錯的那個實作。
- 驗證四法：`.config`／`scripts/config`、`.o` 是否存在、`make fs/xxx.i`、`objdump`／`nm`。

## 自我檢核

- [ ] 不看筆記，能不能解釋 `CONFIG_FOO=m` 時 `#ifdef CONFIG_FOO` 為什麼是假，`IS_ENABLED(CONFIG_FOO)` 為什麼是真？
- [ ] 有人在 code review 時問你「這裡為什麼不用 `IS_ENABLED()` 而用 `#ifdef`」，以 `fs/pipe.c` 的 `note_loss` 為例你怎麼回答？
- [ ] 能說出 `depends on` 和 `select` 的方向差別，以及 `FS_STACK` 為什麼在 menuconfig 裡永遠找不到嗎？
- [ ] 給你一個 `.c` 檔裡被 `if (some_api())` 包住的區塊，`some_api()` 在標頭裡有 stub，你會用哪兩個步驟確認這個區塊在你的 build 裡是否會執行？
- [ ] 想一個你曾經讀過、後來發現「根本沒編進去」的程式碼（任何專案都算）。如果當時知道本章的四種驗證法，你會在哪一步發現？

## 延伸閱讀

### 官方文件

- **`Documentation/kbuild/kconfig-language.rst`**（[docs.kernel.org/kbuild/kconfig-language.html](https://docs.kernel.org/kbuild/kconfig-language.html)）
  - **讀哪裡**：「Menu attributes」一節（`depends on`、`select`、`imply`、`default` 的正式語意），以及第 148 行附近 select 的警告；後半「Kconfig hints」有常見寫法與陷阱
  - **學到什麼**：本章 Kconfig 一節的權威來源；`imply`（弱化版 select）本章沒展開，這裡有
  - **前提**：讀完本章的 Kconfig 一節
- **`Documentation/kbuild/makefiles.rst`**（[docs.kernel.org/kbuild/makefiles.html](https://docs.kernel.org/kbuild/makefiles.html)）
  - **讀哪裡**：「Built-in object goals - obj-y」（第 106 行）與「Loadable module goals - obj-m」（第 136 行）兩節；`<module_name>-y` 複合物件的寫法就在 obj-m 那一節裡
  - **學到什麼**：`obj-y`／`obj-m`／`<name>-y` 複合物件的正式定義
  - **前提**：會讀一般 Makefile
- **`Documentation/process/coding-style.rst`** 第 21 節「Conditional Compilation」（第 1159 行起）
  - **學到什麼**：社群對 `#ifdef` 的立場：盡量放在標頭、用 stub、用 `IS_ENABLED()`、長區塊的 `#endif` 要註明條件
  - **前提**：無
- **`Documentation/kbuild/kconfig.rst`**
  - **讀哪裡**：整篇不長，重點是各種 config 目標（`menuconfig`、`oldconfig` 等）與環境變數的說明
  - **學到什麼**：從 `.config` 的維護面理解它：`.config` 是怎麼產生、怎麼在版本升級時被更新的

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：第 5 章（進階控制流）中談條件編譯的部分，以及第 6 章談組態與建置系統的小節
  - **這本書的定位**：原書用 `#ifdef` 與 autoconf 為例講「程式碼的變體」；kernel 的 Kconfig 是它的超大規模版本

### 技術文章

- **LWN.net 上關於 Kconfig 與 kernel 組態的報導**
  - **讀哪裡**：在 [LWN Kernel Index](https://lwn.net/Kernel/Index/) 的 "Kconfig" 與 "Build system" 分類
  - **這些文章說什麼**：Kconfig 語言的演進、tinyconfig 的由來、`select` 濫用造成的問題討論
  - **為什麼值得讀**：Kconfig 的很多設計（例如為什麼 select 不檢查依賴）只能從歷史討論裡找到理由

確定程式碼真的存在之後，下一個問題就是它**為什麼長這樣**。下一章用 git 把 `fs/pipe.c` 裡一行「看起來很沒道理」的 wakeup 一路追回它的誕生。

→ [Ch 37 git 考古：blame、log -L、pickaxe、Fixes tag](./37-git-archaeology.md)
