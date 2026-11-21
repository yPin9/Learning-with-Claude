# Ch 35 — kernel 原始碼樹地圖與子系統邊界

> **目標**：拿到 kernel 原始碼樹裡任何一個路徑，能說出它屬於哪個子系統、它的標頭檔對誰公開、它和架構相關還是通用、誰在維護它、要去哪個 mailing list 問問題；並且知道一個典型子系統內部大概長什麼樣，打開陌生目錄時不會迷路。
> **鴻溝**：本章主要練**脈絡鴻溝**（這段程式碼活在哪個社群、受誰的規則管），順帶處理**目標鴻溝**（先知道地圖，才知道要往哪裡找）。

> **環境**：Linux kernel v6.18（`git describe` = `v6.18`，commit `7d0a66e4bb90`），WSL Ubuntu 22.04，x86_64 build 位於 `~/code_reading/build`。本章所有數字都是在這個 tree 上實際跑出來的；其他版本的數字會不同，但結構從 v3.x 以來大致穩定。

## 為什麼需要這個？

前面 34 章，我們一直在「放大鏡」模式：盯著一個函式、一個巨集、一個資料結構。可是讀 kernel 真正卡住的時刻，常常不是某一行看不懂，而是這種問題：

- 「`fs/pipe.c` 有 bug，我要寄 patch 給誰？」
- 「`include/linux/fcntl.h` 和 `include/uapi/linux/fcntl.h` 為什麼有兩份？我該改哪一份？」
- 「`O_NONBLOCK` 在 x86 是 `00004000`，為什麼 mips 不一樣？」
- 「這段 code 在 `arch/x86/mm/` 裡，我在 arm64 機器上看到的行為是不是跑同一段？」

這些問題的答案都不在程式碼本身，而在**程式碼的位置**。kernel 是一個 3,600 萬行（下面會實測）的 monorepo，由大約三千個維護區段各自管理。它的目錄結構不是隨便排的，而是一份**組織圖**：目錄邊界大致等於子系統邊界，子系統邊界等於「誰有權決定這裡怎麼寫」的邊界。

Spinellis 在《Code Reading》第 6 章（大型專案）講過同一件事：大型專案的目錄結構、建置系統與組態機制本身就是文件，讀者要先學會讀它們，才能有效率地讀程式碼。2003 年他舉的是 NetBSD 和 Apache；今天 kernel 多了一樣原書沒有、但對讀碼者價值極高的東西——一份機器可讀的組織圖 `MAINTAINERS`，加上一支能查它的腳本。

## 先建立直覺

把 kernel tree 想成一座城市：

```
                    ┌──────────────── 城市（kernel tree）────────────────┐
  市政規章         │ Documentation/  Kconfig  Makefile  MAINTAINERS     │
                    │                                                     │
  核心公共建設     │ kernel/ mm/ fs/ net/ block/ ipc/ security/ ...      │  ← 和架構無關的「通用碼」
  （通用）         │ lib/（共用工具）  crypto/  io_uring/                │
                    │                                                     │
  各國領事館       │ arch/x86/  arch/arm64/  arch/riscv/ ...             │  ← 和 CPU 架構綁死的部分
  （架構相關）     │                                                     │
                    │                                                     │
  工業區           │ drivers/  sound/                                    │  ← 佔了七成以上的程式碼
                    │                                                     │
  公共介面         │ include/linux/（城內用）  include/uapi/（對外承諾） │
                    │                                                     │
  工具與周邊       │ scripts/ tools/ samples/ usr/ rust/ certs/ virt/    │
                    └─────────────────────────────────────────────────────┘
```

這個比喻在兩件事上成立：一、每一區有自己的管理者（MAINTAINERS 裡的區段），二、區與區之間靠公開的「道路」（標頭檔裡的 API）連接。它在一件事上不成立：kernel 的區界**不是硬隔離**——`fs/` 的程式碼可以直接呼叫 `mm/` 的函式，沒有任何機制阻止，差別只在社群規範與審查。所以「子系統邊界」是社會性的，不是技術性的。這點後面會一再出現。

## 頂層目錄逐一說明

先看實際的頂層（v6.18，`ls` 結果）：

```
$ cd ~/code_reading/linux && ls
COPYING  CREDITS  Documentation  Kbuild  Kconfig  LICENSES  MAINTAINERS  Makefile  README
arch  block  certs  crypto  drivers  fs  include  init  io_uring  ipc  kernel  lib  mm
net  rust  samples  scripts  security  sound  tools  usr  virt
```

用 git 追蹤的檔案數來量每一區有多大（`git ls-files` 只算被版本控制的檔案，不會把 build 產物算進去）：

```
$ git ls-files | cut -d/ -f1 | sort | uniq -c | sort -rn
  36519 drivers
  18088 arch
  10819 Documentation
   8599 tools
   6494 include
   2911 sound
   2256 fs
   1983 net
    762 lib
    672 kernel
    572 scripts
    295 security
    287 samples
    227 rust
    197 mm
    193 crypto
    100 block
     78 io_uring
     23 LICENSES
     20 virt
     17 init
     13 ipc
     12 usr
     12 certs
```

檔案數會被 device tree、文件這類非 C 檔拉高。改量 C／標頭／組語／Rust 原始檔的總行數（環境裡沒有 cloc／tokei，這裡用 `git ls-files -- DIR | grep -E '\.(c|h|S|rs)$' | xargs cat | wc -l`，包含註解與空行）：

```
25132945 drivers
 2517913 arch
 1595680 sound
 1562983 fs
 1391843 include
 1304636 net
 1258334 tools
  521412 kernel
  289477 lib
  211323 mm
  121754 security
  109944 crypto
   70593 block
   51204 scripts
   50758 rust
   37506 samples
   24560 io_uring
   10826 virt
    9911 ipc
    4631 init
    1063 certs
     831 usr
     708 Documentation
total 36280835
```

三個讓人意外的數字，每個都會改變你讀碼的策略：

1. **`drivers/` 佔了 69%（2,513 萬 / 3,628 萬行）**。其中 `drivers/gpu/` 一個子目錄就 775 萬行，而 `drivers/gpu/drm/amd/include/` 這一個目錄就有 480 萬行——幾乎全是硬體廠商自動產生的暫存器定義標頭。結論：**「kernel 很大」這句話大半是 driver 和產生出來的標頭造成的**。核心子系統 `kernel/` + `mm/` + `fs/`（不含各檔案系統）加起來的規模，是一個人可以在幾年內熟悉的量級。
2. **`mm/` 只有 21 萬行**。記憶體管理是公認最難的子系統之一，可是它不大。難的是領域知識與並行，不是行數（領域鴻溝，見 [Ch 46](./46-acquiring-domain-knowledge.md)）。
3. **`fs/` 的 156 萬行裡，各檔案系統占大宗**：`fs/xfs/` 22 萬、`fs/btrfs/` 16 萬、`fs/smb/` 11 萬、`fs/ext4/` 6.6 萬；直接放在 `fs/` 底下的 71 個 `.c` 檔（VFS 本體，包括我們一直在讀的 `fs/pipe.c`）合計 85,158 行。

逐一說明每個頂層目錄在讀碼時的角色：

| 目錄 | 內容 | 讀碼時的意義 |
|---|---|---|
| `arch/` | 21 個 CPU 架構各自的程式碼（`arch/x86/` 47 萬行最大，其次 powerpc、mips、arm、arm64） | 開機、例外／中斷入口、分頁表、context switch、atomic 的實作。讀通用碼追到 `arch_*` 函式時要往這裡跳 |
| `kernel/` | 排程（`kernel/sched/`）、process 生命週期（`fork.c`、`exit.c`）、signal、timer、RCU（`kernel/rcu/`）、locking、BPF、tracing、printk、module loader | 「OS 核心」的大部分。注意 `kernel/watch_queue.c` 這種放在這裡但實際上是給 pipe 用的檔案——目錄不是完美分類 |
| `mm/` | 頁框配置、slab（`mm/slub.c`）、虛擬記憶體、page cache 的通用部分、reclaim、memcg | 和 `arch/*/mm/` 分工：架構相關的頁表操作在 arch，策略在這裡 |
| `fs/` | VFS 本體（`fs/*.c`）＋ 各檔案系統（`fs/ext4/` …）＋ 與檔案相關的機制（pipe、eventfd、epoll、exec、coredump） | 很多「不像檔案系統」的東西住在這裡，因為它們以 `struct file` 的形式出現 |
| `net/` | 網路協定棧（`net/ipv4/`、`net/core/`、`net/netfilter/` …） | 網卡 driver 不在這裡，在 `drivers/net/` |
| `block/` | block layer、I/O scheduler、bio／request | 介於 `fs/` 和 `drivers/block`、`drivers/nvme` 之間 |
| `drivers/` | 所有硬體 driver 與 bus 架構（`drivers/base/` 是 driver model 核心，見 [Ch 43](./43-driver-model.md)） | 依「裝置類別」分子目錄，而不是依廠商 |
| `include/` | 跨檔案共用的標頭（下一節細講） | 子系統之間的「公開 API」大多宣告在這裡 |
| `lib/` | 與子系統無關的通用演算法：字串、rbtree、xarray、kfifo、sort、CRC | Part 4 的資料結構大多在這裡實作 |
| `security/` | LSM 框架與 SELinux、AppArmor、Landlock 等 | hook 被插在其他子系統裡，實作在這裡 |
| `crypto/` | kernel 的密碼學 API 與演算法 | |
| `io_uring/` | io_uring（commit `ed29b0b4fd83`「io_uring: move to separate directory」，進入 v6.0，從 `fs/io_uring.c` 拆出成獨立目錄） | 一個「長大後搬家」的例子：目錄會隨子系統成熟而改變 |
| `ipc/` | System V IPC、POSIX message queue | 注意：pipe **不在**這裡 |
| `init/` | `start_kernel()`（`init/main.c`）、initramfs、`init/Kconfig`（大量通用選項） | 開機路徑的起點 |
| `virt/` | KVM 的架構無關部分（`virt/kvm/`） | 架構相關部分在 `arch/x86/kvm/` 等 |
| `sound/` | ALSA 與音效 driver | 歷史因素沒有放進 `drivers/` |
| `rust/` | Rust for Linux 的綁定與基礎 crate | v6.1 起進入主線 |
| `scripts/` | 建置與維護腳本：Kconfig 工具、`checkpatch.pl`、`get_maintainer.pl`、`kernel-doc` | 讀碼工具箱的一部分 |
| `tools/` | user-space 工具：perf、bpftool、objtool、**selftests**（`tools/testing/selftests/`，見 [Ch 40](./40-tests-as-specification.md)） | 雖然在 kernel tree 裡，但編成 user-space 程式 |
| `samples/` | 示範用 module 與程式 | 學 API 用法的好地方 |
| `Documentation/` | reST 格式文件，即 docs.kernel.org 的原始檔（見 [Ch 39](./39-documentation-and-style.md)） | |
| `usr/` | 產生內建 initramfs 的工具；`usr/include/` 是匯出 uapi 標頭的地方 | |
| `certs/` | module 簽章的金鑰處理 | |

## arch/ 與通用碼的分界

kernel 支援 21 個架構，但通用碼只有一份。這是靠一套「通用碼呼叫抽象介面、每個架構各自實作」的分工完成的。分界有三種形式：

**形式一：命名慣例 `arch_*`**。通用碼呼叫 `atomic_add()`，經過 `include/linux/atomic/` 的包裝後落到 `arch_atomic_add()`，由各架構提供：

```c
/* arch/x86/include/asm/atomic.h:31（v6.18） */
static __always_inline void arch_atomic_add(int i, atomic_t *v)
```

讀碼時看到 `arch_` 前綴，就知道「往下追要進 `arch/<你的架構>/`」。

**形式二：`#include <asm/xxx.h>`**。通用碼寫 `#include <asm/uaccess.h>`，**沒有寫是哪個架構**。實際是哪一份，由 build 時的 `-I` 搜尋順序決定。直接看 `fs/pipe.o` 的編譯指令（Kbuild 把它存在 `.pipe.o.cmd`）：

```
$ tr ' ' '\n' < ~/code_reading/build/fs/.pipe.o.cmd | grep -E '^-I|^-include'
-I/home/ypp/code_reading/linux/arch/x86/include
-I./arch/x86/include/generated
-I/home/ypp/code_reading/linux/include
-I./include
-I/home/ypp/code_reading/linux/arch/x86/include/uapi
-I./arch/x86/include/generated/uapi
-I/home/ypp/code_reading/linux/include/uapi
-I./include/generated/uapi
-include
-include
-include
```

`arch/x86/include` 排在 `include` 前面，所以 `<asm/atomic.h>` 會找到 `arch/x86/include/asm/atomic.h`。三個 `-include` 分別強制引入 `include/linux/compiler-version.h`、`include/linux/kconfig.h`、`include/linux/compiler_types.h`——每個 kernel 檔案都隱性地帶著這三個標頭，這就是為什麼 `__user`、`IS_ENABLED()` 不用 include 就能用（[Ch 18](./18-annotations-and-sparse.md)、[Ch 36](./36-kconfig-kbuild-ifdef.md)）。

**形式三：asm-generic 預設實作**。不想自己寫的架構，可以在 `arch/*/include/asm/Kbuild` 裡宣告「用通用版」：

```
$ head -20 arch/x86/include/asm/Kbuild
...
generic-y += early_ioremap.h
generic-y += fprobe.h
generic-y += mcs_spinlock.h
generic-y += mmzone.h
```

build 時 Kbuild 會產生一個只有一行 include 的包裝檔：

```
$ cat ~/code_reading/build/arch/x86/include/generated/asm/mmiowb.h
#include <asm-generic/mmiowb.h>
```

讀碼陷阱就在這裡：在原始碼樹裡 `find -name mmiowb.h` 找不到 `arch/x86/include/asm/mmiowb.h`，會以為 x86 沒有這個標頭；其實它是 build 時才產生的。

`fs/pipe.c` 只寫了 25 行 `#include`，但它的 `.cmd` 檔記錄的依賴標頭有 716 個。把它們按來源歸類：

```
$ grep -oE '\S+\.h' fs/.pipe.o.cmd \
    | sed -E 's#^\$\(srctree\)/##; s#/home/ypp/code_reading/linux/##' \
    | awk -F/ '{ if ($1=="arch") print $1"/"$2"/"$3"/"$4; else print $1"/"$2 }' \
    | sort | uniq -c | sort -rn
    401 include/linux
    131 arch/x86/include/asm
     85 include/uapi
     40 include/asm-generic
     21 arch/x86/include/generated
     19 arch/x86/include/uapi
     12 include/vdso
      4 include/generated
      ...
      1 fs/internal.h
```

一個「通用」的 `fs/pipe.c`，編譯時有 171 個標頭來自 x86（131 + 21 + 19）。**通用碼的原始碼是架構無關的，但它的編譯結果不是。** 這就是為什麼讀 inline 函式、巨集時，要用你目標架構的 build（`compile_commands.json`、`.i` 檔）來確認，而不是憑原始碼猜。

## include/linux、include/uapi、arch/*/include：三種標頭的承諾等級

```
include/linux/foo.h          kernel 內部 API。可以隨時改，改了就順手修所有呼叫者。
include/uapi/linux/foo.h     user-space ABI。匯出給 glibc 等使用，一旦發布永不破壞。
arch/<arch>/include/asm/     架構相關的內部實作。
arch/<arch>/include/uapi/asm 架構相關的 ABI（例如 struct stat 的佈局、ioctl 編號）。
include/asm-generic/         「沒有特別需求的架構」共用的預設版本。
```

uapi（User-space API）這套拆分是 David Howells 在 2012 年做的大規模整理（例如 commit `607ca46e97a1`「UAPI: (Scripted) Disintegrate include/linux」，`git describe --contains` 顯示進入 v3.7）：在那之前，一個標頭裡用 `#ifdef __KERNEL__` 區分哪些給 user space 看，讀起來很亂、也容易不小心把內部細節洩漏成 ABI。拆分之後，「我能不能改這個定義」的答案就寫在路徑上了。

實際例子：`include/linux/fcntl.h` 第 6 行是 `#include <uapi/linux/fcntl.h>`——kernel 內部版本包住 uapi 版本，再加上只給 kernel 用的東西。而 `O_NONBLOCK` 這個 user space 看得到的常數，定義在 uapi 層，而且**各架構不同**：

```
$ grep -n 'O_NONBLOCK' include/uapi/asm-generic/fcntl.h arch/*/include/uapi/asm/fcntl.h
include/uapi/asm-generic/fcntl.h:38:#define O_NONBLOCK	00004000
arch/alpha/include/uapi/asm/fcntl.h:10:#define O_NONBLOCK	 00004
arch/mips/include/uapi/asm/fcntl.h:16:#define O_NONBLOCK	0x0080
arch/parisc/include/uapi/asm/fcntl.h:11:#define O_NONBLOCK	000200000
arch/sparc/include/uapi/asm/fcntl.h:11:#define O_NONBLOCK	0x4000
```

x86 沒有自己的 `fcntl.h`：`include/uapi/asm-generic/Kbuild` 裡的 `mandatory-y += fcntl.h` 讓沒有提供的架構自動用 generic 版。alpha、mips、parisc、sparc 的值不同，是因為它們當年要和各自的商業 Unix（OSF/1、IRIX、HP-UX、SunOS）二進位相容——這是一個純粹的脈絡鴻溝：程式碼本身完全不會告訴你為什麼。

讀碼判斷法則：

- 你在 `include/uapi/` 看到一個怪異的欄位或 padding → 它多半是為了 ABI 相容，**不能**用「這樣寫比較乾淨」的理由去改。
- 你在 `include/linux/` 看到一個怪異的設計 → 可能只是歷史包袱，有機會被重構。
- 一個 `static inline` 函式住在 `include/linux/` 而不是 `.c` 檔 → 它是給**別的子系統**用的 API，或者是效能考量需要 inline。
- 一個標頭和 `.c` 檔放在同一個目錄（例如 `fs/internal.h`、`fs/ext4/ext4.h`）→ 子系統**私有**，外部不該 include。

## MAINTAINERS：kernel 的組織圖

`MAINTAINERS` 是頂層的一個純文字檔，v6.18 有 28,419 行。用 awk 粗估約有 3,100 個區段（以「空行後接全大寫開頭的標題行」計算，會包含少量非區段的標題），9,179 條 `F:`（檔案樣式）、2,951 條 `L:`（mailing list）。

檔案開頭有格式說明，摘重點（`MAINTAINERS:4` 起）：

```
	M: *Mail* patches to: FullName <address@domain>
	R: Designated *Reviewer*: FullName <address@domain>
	L: *Mailing list* that is relevant to this area
	S: *Status*, one of the following:
	T: *SCM* tree type and location.
	F: *Files* and directories wildcard patterns.
	   F:	drivers/net/	all files in and below drivers/net
	   F:	drivers/net/*	all files in drivers/net, but not below
	X: *Excluded* files and directories that are NOT maintained, same
	N: Files and directories *Regex* patterns.
	K: *Content regex* (perl extended) pattern match in a patch or file.
```

`F: drivers/net/` 和 `F: drivers/net/*` 的差別（含子目錄 vs 不含）是最常被看錯的地方，等一下就會撞到。

`S:`（狀態）的分布本身就是情報：

```
$ grep '^S:' MAINTAINERS | sort | uniq -c | sort -rn
   2101 S:	Maintained
    768 S:	Supported          ← 有人「拿薪水」在顧
    139 S:	Orphan             ← 沒人管
     87 S:	Odd Fixes
     19 S:	Odd fixes
      3 S:	Obsolete
      ...
      1 S:	Buried alive in reporters
```

最後那個 `Buried alive in reporters` 是 `THE REST` 區段（`MAINTAINERS:28413`），維護者是 Linus Torvalds，`F: *` 和 `F: */`——所有沒被其他區段認領的檔案最後都歸他。

## get_maintainer.pl：實際查一次

手動在 28,000 行裡比對樣式不實際。`scripts/get_maintainer.pl` 會幫你做：

```
$ perl scripts/get_maintainer.pl -f fs/pipe.c
Alexander Viro <viro@zeniv.linux.org.uk> (maintainer:FILESYSTEMS (VFS and infrastructure))
Christian Brauner <brauner@kernel.org> (maintainer:FILESYSTEMS (VFS and infrastructure))
Jan Kara <jack@suse.cz> (reviewer:FILESYSTEMS (VFS and infrastructure))
linux-fsdevel@vger.kernel.org (open list:FILESYSTEMS (VFS and infrastructure))
linux-kernel@vger.kernel.org (open list)
```

括號裡的區段名就是子系統邊界的名字。加 `--sections` 看完整區段：

```
$ perl scripts/get_maintainer.pl --nogit-fallback --sections -f fs/pipe.c
FILESYSTEMS (VFS and infrastructure)
M:	Alexander Viro <viro@zeniv.linux.org.uk>
M:	Christian Brauner <brauner@kernel.org>
R:	Jan Kara <jack@suse.cz>
L:	linux-fsdevel@vger.kernel.org
S:	Maintained
T:	git https://git.kernel.org/pub/scm/linux/kernel/git/vfs/vfs.git
F:	fs/*
F:	include/linux/fs.h
F:	include/linux/fs_types.h
F:	include/uapi/linux/fs.h
F:	include/uapi/linux/openat2.h
F:	rust/kernel/fs.rs
...
F:	init/do_mounts*
F:	init/*initramfs*

THE REST
M:	Linus Torvalds <torvalds@linux-foundation.org>
L:	linux-kernel@vger.kernel.org
S:	Buried alive in reporters
...
```

注意 `F: fs/*`——**有星號**，所以只含 `fs/` 底下的檔案、不含子目錄。`fs/ext4/inode.c` 不歸 VFS 區段：

```
$ perl scripts/get_maintainer.pl --nogit-fallback -f fs/ext4/inode.c
"Theodore Ts'o" <tytso@mit.edu> (maintainer:EXT4 FILE SYSTEM)
Andreas Dilger <adilger.kernel@dilger.ca> (maintainer:EXT4 FILE SYSTEM)
linux-ext4@vger.kernel.org (open list:EXT4 FILE SYSTEM)
linux-kernel@vger.kernel.org (open list)
```

`T:` 那行也值得記下：VFS 的 patch 先進 `vfs/vfs.git`，再由 Christian Brauner 在 merge window 送給 Linus。追「這個 commit 從哪條路進主線」時（[Ch 37](./37-git-archaeology.md)），merge commit 的訊息會對應到這個 tree。

### 失敗案例：沒有維護者的標頭

pipe 的核心資料結構 `struct pipe_inode_info` 定義在 `include/linux/pipe_fs_i.h`。查它：

```
$ perl scripts/get_maintainer.pl -f include/linux/pipe_fs_i.h
linux-kernel@vger.kernel.org (open list)
```

**沒有任何人**，只有 LKML 這個每天上千封信的總表。原因是 VFS 區段只列了 `include/linux/fs.h` 等少數標頭，沒列 `pipe_fs_i.h`，於是它掉進 `THE REST`。而 `THE REST` 的維護者 Linus 被腳本刻意濾掉：`scripts/get_maintainer.pl:87` 把他列進 `@penguin_chief`，除非加 `--git-chief-penguins`，否則不會出現在名單裡（沒有人該為了一個標頭直接寄信給 Linus）。這是一個重要的事實：**MAINTAINERS 是人寫的，有漏洞**。

補救方法是讓腳本去看 git 歷史，誰最近常改這個檔案：

```
$ time perl scripts/get_maintainer.pl --git --git-since=2-years-ago -f include/linux/pipe_fs_i.h
K Prateek Nayak <kprateek.nayak@amd.com> (commit_signer:2/7=29%,authored:1/7=14%)
Oleg Nesterov <oleg@redhat.com> (commit_signer:1/7=14%)
Mateusz Guzik <mjguzik@gmail.com> (commit_signer:1/7=14%,authored:1/7=14%,removed_lines:1/19=5%)
Christian Brauner <brauner@kernel.org> (commit_signer:1/7=14%)
linux-kernel@vger.kernel.org (open list)

real	0m17.989s
```

`--git` 模式要讀 git 歷史，在我們的 blobless clone 上第一次跑花了 18 秒（中途還觸發好幾次 `Auto packing the repository in background`，因為它按需下載了 blob，見 [Ch 37](./37-git-archaeology.md) 的效能一節）。結果裡出現 Christian Brauner——和 `fs/pipe.c` 的 VFS 維護者是同一人，這才是實際上該 Cc 的對象。

### 多個區段重疊

一個檔案可以同時屬於好幾個區段。Intel 網卡 driver：

```
$ perl scripts/get_maintainer.pl --nogit-fallback -f drivers/net/ethernet/intel/e1000e/netdev.c
Tony Nguyen <anthony.l.nguyen@intel.com> (maintainer:INTEL ETHERNET DRIVERS)
Przemek Kitszel <przemyslaw.kitszel@intel.com> (maintainer:INTEL ETHERNET DRIVERS)
Andrew Lunn <andrew+netdev@lunn.ch> (maintainer:NETWORKING DRIVERS)
"David S. Miller" <davem@davemloft.net> (maintainer:NETWORKING DRIVERS)
Eric Dumazet <edumazet@google.com> (maintainer:NETWORKING DRIVERS)
Jakub Kicinski <kuba@kernel.org> (maintainer:NETWORKING DRIVERS)
Paolo Abeni <pabeni@redhat.com> (maintainer:NETWORKING DRIVERS)
intel-wired-lan@lists.osuosl.org (moderated list:INTEL ETHERNET DRIVERS)
netdev@vger.kernel.org (open list:NETWORKING DRIVERS)
linux-kernel@vger.kernel.org (open list)
```

兩層：廠商自己的 driver 維護者（`INTEL ETHERNET DRIVERS`，它的 list 還是 Intel 自己架的 `lists.osuosl.org`），加上整個網路 driver 的上層維護者（`NETWORKING DRIVERS`）。這個層級關係就是 patch 的流向：先過 Intel 的 tree，再進 netdev 的 tree，最後到 Linus。

x86 的 page fault 處理也類似，`arch/x86/mm/fault.c` 同時屬於 `X86 MM`（Dave Hansen、Andy Lutomirski、Peter Zijlstra）和 `X86 ARCHITECTURE (32-BIT AND 64-BIT)`（Thomas Gleixner、Ingo Molnar、Borislav Petkov）。`mm/slub.c` 則屬於 `SLAB ALLOCATOR`，list 是 `linux-mm@kvack.org`——不在 vger 上，這也是只有查了才會知道的事。

## 子系統的典型內部結構

打開一個陌生子系統目錄，先找這幾樣東西。以 `fs/ext4/` 為例（51 個檔案）：

```
fs/ext4/
├── Kconfig           ← 這個子系統有哪些編譯選項（Ch 36）
├── Makefile          ← 哪些 .c 會被編、受哪個 CONFIG 控制（Ch 36）
├── ext4.h            ← 私有主標頭：核心 struct 全在這裡 → 「資料結構優先」從這裡開始（Ch 5）
├── ext4_jbd2.h, extents_status.h, mballoc.h, xattr.h ...  ← 子模組的私有標頭
├── super.c           ← 掛載、模組初始化、file_system_type 註冊 → 入口點（Ch 5）
├── inode.c, file.c, dir.c, namei.c  ← 實作 VFS ops table 的地方（Ch 28）
├── extents.c, mballoc.c, ...        ← 內部演算法
├── sysfs.c           ← 對 user space 暴露的調整旋鈕
└── inode-test.c, mballoc-test.c     ← KUnit 測試（Ch 40）
```

它在 tree 的其他角落還有分身，這些都列在 MAINTAINERS 的 `EXT4 FILE SYSTEM` 區段（`MAINTAINERS:9364`）裡：

```
F:	Documentation/filesystems/ext4/
F:	fs/ext4/
F:	include/trace/events/ext4.h
F:	include/uapi/linux/ext4.h
```

所以**MAINTAINERS 的 `F:` 清單就是一個子系統的完整「檔案邊界」**——比任何目錄結構都準。tracepoint 定義在 `include/trace/events/`、uapi 在 `include/uapi/linux/`、文件在 `Documentation/`，這些散落的部分只有 `F:` 會把它們收在一起。

歸納成一個模板，大部分子系統都長這樣：

| 角色 | 典型檔名 | 讀碼用途 |
|---|---|---|
| 建置 | `Kconfig`、`Makefile` | 知道哪些檔案在你的 config 下存在 |
| 私有主標頭 | `<subsys>.h`、`internal.h` | 核心資料結構 |
| 公開 API | `include/linux/<subsys>.h` | 別的子系統怎麼用它 |
| ABI | `include/uapi/linux/<subsys>.h` | user space 怎麼用它 |
| 入口／註冊 | `core.c`、`main.c`、`super.c`、`*_init()` | 從哪裡開始讀 |
| ops 實作 | `file.c`、`ops.c`、`*_ops` | 間接呼叫的目的地 |
| tracepoint | `include/trace/events/<subsys>.h` | 動態觀察的掛點（[Ch 51](./51-dynamic-tracing.md)） |
| 測試 | `*-test.c`、`*_kunit.c`、`tools/testing/selftests/<subsys>/` | 可執行的規格（[Ch 40](./40-tests-as-specification.md)） |
| 文件 | `Documentation/<area>/<subsys>*` | 設計說明（[Ch 39](./39-documentation-and-style.md)） |

## 決定「這個檔案屬於誰」的流程

```
拿到一個路徑 P
   │
   ├─ 1. 看頂層目錄：arch/? drivers/? 通用核心？ → 大分類
   ├─ 2. get_maintainer.pl --nogit-fallback --sections -f P
   │       → 區段名 = 子系統名；M/R = 人；L = 去哪裡問；T = patch 走哪個 tree
   ├─ 3. 結果只有 linux-kernel@ ？ → MAINTAINERS 有漏洞
   │       → get_maintainer.pl --git -f P   或   git log --format='%aN' -- P | sort | uniq -c
   ├─ 4. 有多個區段？ → 由內到外是 patch 的流向
   └─ 5. 打開那個區段的所有 F: 路徑 → 這個子系統的完整檔案邊界
```

## 對比與取捨

| 找「誰負責」的方法 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| 看目錄名 | 零成本 | 粗；`kernel/watch_queue.c` 其實和 pipe 綁在一起 | 第一印象 |
| `get_maintainer.pl -f` | 權威、含 list 與 tree | 依賴人工維護的 `F:`，有漏洞 | 預設做法 |
| `get_maintainer.pl --git` | 反映實際上誰在改 | 慢（blobless clone 更慢）；只看近期，可能找到路過的人 | MAINTAINERS 沒結果時 |
| `git log --format=%aN -- P` 統計 | 完全自控 | 要自己判斷誰是維護者、誰只是送過一個 patch | 研究性質的考古 |
| 文件開頭的 copyright／作者註解 | 有時有原作者 | 常常是 20 年前的人，早已不維護 | 幾乎只有歷史價值 |

## 踩雷集錦

1. **「`fs/` 底下都是檔案系統」**：錯。pipe、eventfd、epoll、exec、coredump、io 多工都在 `fs/`。判準是「它是否以 `struct file` 的樣子出現在 user space」，不是「它是否存資料到磁碟」。
2. **「找不到 `arch/x86/include/asm/xxx.h` 就代表 x86 沒有」**：可能是 `generic-y` 在 build 時產生的包裝檔，去 `build/arch/x86/include/generated/asm/` 找，或看 `arch/x86/include/asm/Kbuild`。
3. **「`F: fs/*` 包含 `fs/ext4/`」**：不包含。`F: dir/` 含子目錄，`F: dir/*` 不含。這一個字元決定了你的 patch 要寄給誰。
4. **「`get_maintainer.pl` 印出的名單全部都要 Cc」**：預設模式下沒問題；但用 `--git` 時，名單裡會混入只是順手改過一次的人。對照 `--sections` 的結果再決定。
5. **「通用碼 = 所有架構跑同一份機器碼」**：原始碼同一份，但 inline 函式和巨集展開後大量是架構相關的。`fs/pipe.c` 在 x86 上編譯時就拉進 171 個 x86 標頭。需要知道「實際跑什麼」時，看 `.i` 或反組譯（[Ch 50](./50-compiler-as-reading-tool.md)）。
6. **「uapi 裡的醜設計可以順手清掉」**：uapi 是 ABI 承諾。看起來多餘的 padding、各架構不同的常數，多半是為了和某個已經發布的 user-space 程式相容。

## 進階：再往深一層

- **`scripts/get_maintainer.pl` 還能吃 patch**：`git format-patch -1 <commit> | perl scripts/get_maintainer.pl` 會根據 patch 動到的所有檔案算出收件人，這是送 patch 的標準流程（`Documentation/process/submitting-patches.rst`）。
- **`K:` 內容樣式**：有些區段不是靠路徑，而是靠「patch 內容裡出現某個識別字」來認領，例如某個 API 的使用者。這讓子系統邊界可以橫切目錄。
- **`P:`（Subsystem Profile）**：部分子系統有自己的規則文件，例如 tip tree（x86、排程、timer 等）的 `Documentation/process/maintainer-tip.rst`。讀那個子系統的程式碼時，它的 profile 解釋了很多「為什麼 commit message 要這樣寫」「為什麼這裡要這樣排版」。完整清單在 `Documentation/process/maintainer-handbooks.rst`。
- **用統計找出熱區**：`git log --since=1.year --name-only --format= -- fs/ | sort | uniq -c | sort -rn | head` 告訴你一個子系統最近哪些檔案最常被改——那通常就是 bug 最多、設計還在變動的地方，也是讀碼投資報酬率最高的地方。

## 動手練習

1. 對 `kernel/watch_queue.c` 跑 `get_maintainer.pl --sections`。它在 `kernel/` 底下，但和 pipe 關係密切——它歸誰管？和 `fs/pipe.c` 的維護者有沒有交集？
2. 在 `MAINTAINERS` 裡找出一個 `S: Orphan` 的區段，看它的 `F:` 指向哪些檔案，再用 `git log --oneline -5 -- <那些檔案>` 看最近還有沒有人改。
3. 找出 `struct stat` 的 user-space 定義在 x86_64 實際用的是哪一個檔案（提示：`arch/x86/include/uapi/asm/` 與 `include/uapi/asm-generic/` 都看一下）。
4. 選一個你感興趣的子系統，用 MAINTAINERS 的 `F:` 清單列出它在 tree 裡的所有角落，對照上面的模板表格，填出每一格對應的檔案。

## 本章重點整理

- kernel tree 的目錄大致等於子系統邊界，但邊界是社會性的：程式碼可以互相呼叫，分界靠 MAINTAINERS 與審查。
- `drivers/` 佔了約七成程式碼，其中大量是產生出來的暫存器標頭；核心子系統的規模遠比「3,600 萬行」給人的印象小。
- 通用碼透過 `arch_*` 命名、`<asm/...>` 搜尋路徑、`asm-generic` + `generic-y` 三種方式和架構碼接合；原始碼通用，編譯結果不通用。
- `include/linux` 是內部 API（可改），`include/uapi` 是 ABI（不可破壞）；路徑本身就告訴你這段定義的承諾等級。
- `get_maintainer.pl --sections -f` 告訴你區段名、人、list、tree；結果只剩 LKML 時，用 `--git` 或 git log 補。

## 自我檢核

- [ ] 不看筆記，能不能說出 `include/linux/foo.h` 與 `include/uapi/linux/foo.h` 的差別，以及為什麼後者裡的醜設計不能隨便改？
- [ ] 有人問你「`<asm/atomic.h>` 在 x86 build 裡實際是哪個檔案、kernel 怎麼決定的」，你能用 `-I` 順序解釋嗎？
- [ ] 能說出 `F: fs/` 和 `F: fs/*` 的差別，並舉出這個差別導致 `fs/pipe.c` 和 `fs/ext4/inode.c` 分屬不同維護者的例子嗎？
- [ ] 遇到 `get_maintainer.pl` 只回 `linux-kernel@vger.kernel.org` 時，你的下一步是什麼？為什麼？
- [ ] 想一個你最近讀過的 kernel 檔案：它的私有標頭、公開 API、uapi、tracepoint、測試分別在哪裡？

## 延伸閱讀

### 官方文件

- **`Documentation/process/maintainers.rst`**（[docs.kernel.org/process/maintainers.html](https://docs.kernel.org/process/maintainers.html)）
  - **讀哪裡**：開頭的格式說明，等同 `MAINTAINERS` 檔頭；後面是 MAINTAINERS 本身的排版版本
  - **學到什麼**：每個欄位字母的正式定義，尤其 `F:`／`X:`／`N:`／`K:` 的比對規則
  - **前提**：無
- **`Documentation/process/submitting-patches.rst`**（[docs.kernel.org/process/submitting-patches.html](https://docs.kernel.org/process/submitting-patches.html)）
  - **讀哪裡**：「Select the recipients for your patch」一節
  - **學到什麼**：社群期待你怎麼用 `get_maintainer.pl`、哪些 list 要 Cc；從「寫 patch 的人」的角度理解子系統邊界
  - **前提**：會用 git format-patch
- **`Documentation/process/maintainer-handbooks.rst`** 與 **`Documentation/process/maintainer-tip.rst`**
  - **讀哪裡**：先看 handbooks 列表，挑你要讀的子系統；tip 那篇是寫得最完整的一份
  - **學到什麼**：各子系統特有的規矩，解釋了很多程式碼風格與 commit 格式上的「為什麼」
  - **前提**：讀完本章

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：第 6 章（Tackling Large Projects），特別是談目錄結構與建置系統的小節
  - **這本書的定位**：本章的骨架。它的例子是 NetBSD／Apache，但「目錄結構就是設計文件」的觀點完全適用今天的 kernel
- **《Linux Kernel Development》（3rd ed.）** — Robert Love（Addison-Wesley, 2010）
  - **讀哪幾章**：第 2 章（Getting Started with the Kernel）有原始碼樹導覽
  - **注意**：基於 2.6.34，`io_uring/`、`rust/`、uapi 拆分都不在書裡；把它當「歷史版地圖」對照本章的實測

### 技術文章

- **LWN.net 上關於 UAPI 標頭拆分的報導**（2012 年，David Howells 的 "UAPI disintegration" patch 系列）
  - **這篇說什麼**：為什麼要把 `__KERNEL__` 混雜的標頭拆成 uapi 與內部兩份，以及拆分的規模
  - **讀哪裡**：在 [LWN Kernel Index](https://lwn.net/Kernel/Index/) 搜尋 "UAPI"
  - **為什麼值得讀**：它解釋了 `include/uapi/` 這個目錄「為什麼存在」，正是本章 uapi 一節的脈絡

地圖有了，但地圖上的檔案不一定會被編進你的 kernel——下一章處理那個最陰險的讀碼陷阱：你讀了半天的程式碼，在你的 config 下根本不存在。

→ [Ch 36 Kconfig、Kbuild 與 #ifdef：哪些程式碼真的會被編進去](./36-kconfig-kbuild-ifdef.md)
