# Ch 48 — elixir、cscope 與 GNU Global

> **目標**：會用 elixir 在瀏覽器裡跨版本查 kernel 識別字；能在**不污染 linux tree** 的前提下替 v6.18 建出 cscope、GNU Global、ctags 三種索引，用命令列查定義、呼叫者、參考與賦值點；並且用實測的建索引時間、索引大小與查詢結果，判斷什麼時候該用哪一個、它們共同的盲點在哪。
> **鴻溝**：本章主要練**間接鴻溝**（「誰呼叫它」「誰把它塞進 ops table」從一次搜尋變成一次查表）。

> **環境**：WSL2 Ubuntu 22.04；cscope 15.9、GNU Global 6.6.15、Universal Ctags 6.2.0（皆裝在 `~/.local/bin`）；kernel v6.18 原始碼在 `~/code_reading/linux`，索引一律建在 `~/code_reading/scratch/48/` 底下。elixir 的查詢結果是 2026-11 實際連線 elixir.bootlin.com 取得的。

## 為什麼需要這個？

[Ch 47](./47-grep-and-regex-search.md) 的 rg 每次查詢都重新掃 62,000 個檔案。兩百毫秒聽起來很快，但讀一個子系統時你會連續問幾百次「這個定義在哪」「誰呼叫這個」，而且每次都要自己想 regex、自己濾雜訊。Spinellis 在原書 10.7 節介紹的 code browser（當年是 cscope、LXR、ctags）解決的就是這件事：**先花一次時間把整個 tree 的符號拆好、存起來，之後每次查詢都是查表**。

查表帶來的不只是速度，還有**分類**。rg 只知道「這一行有這個字」；索引工具知道「這一行是**定義**、那一行是**呼叫**、另一行是**賦值**」。這個分類就是你在讀碼時真正要的答案。

這一章的三個工具都還是**語法層**的：它們用自己的 C parser 掃原始碼，不跑前置處理器、不知道 `.config`。所以它們繼承了 rg 大部分盲點（巨集拼接、條件編譯、函式指標），只是用起來快得多、準一點。真正懂語意的工具留到 [Ch 49](./49-clangd-lsp.md)。

## 先建立直覺

```
                  ┌───────────── 建一次索引（秒～分鐘） ─────────────┐
 原始碼 62k 檔 ──▶│ parser 掃每個檔案：                                │──▶ 索引檔（GB 級）
                  │  - 這是定義？（函式、巨集、struct、變數）          │
                  │  - 這是參考？（呼叫、取址、賦值）                  │
                  │  - 它在哪個函式裡面？                              │
                  └────────────────────────────────────────────────────┘
                                                                         │
     查詢（毫秒）：「anon_pipe_read 的定義？」「誰呼叫 vfs_read？」 ◀──────┘
```

把索引想成**書末的索引頁**：作者（parser）事先翻過整本書，把每個名詞出現在哪幾頁、哪一頁是定義列出來。你查「pipe」時不用重讀全書。但索引頁是照**印出來的字**編的：如果書裡有一句「下文中 X 一律簡稱 Y」（巨集），索引頁不會知道 Y 就是 X。

elixir 是同一個概念的線上版：bootlin 替**每一個 kernel 版本**都建好了索引，放在網站上。

## elixir：不用建索引的線上版

elixir（elixir.bootlin.com）是 bootlin 維護的 kernel 交叉參照網站，前身是 LXR。它的價值在於**每個 tag 都有一份**，切版本只要改網址。

### URL 格式

```
https://elixir.bootlin.com/linux/<版本>/source/<路徑>#L<行號>     看檔案
https://elixir.bootlin.com/linux/<版本>/A/ident/<識別字>           查識別字（所有語言）
https://elixir.bootlin.com/linux/<版本>/C/ident/<識別字>           查識別字（只看 C）
https://elixir.bootlin.com/linux/latest/source/<路徑>              最新版（會 302 轉址到具體版本）
```

我逐一用 curl 確認過這幾種格式都回 200（`latest` 回 302，轉到 `v7.3.1`，代表 elixir 上的最新 tag 已經遠超本課的 v6.18——**連結請永遠寫死版本號**，否則行號一年後就對不上）。

### 實際查詢

以下是 `/linux/v6.18/C/ident/<名字>` 頁面的內容（從 HTML 擷取文字）：

```
anon_pipe_read:
  Defined in 1 files as a function:   fs/pipe.c  line 269
  Referenced in 1 files:              fs/pipe.c  line 409, line 1259

mutex_lock:
  Documented in 1 files:              kernel/locking/mutex.c  line 248
  Defined in 2 files as a prototype:  include/linux/mutex.h line 189, tools/perf/util/mutex.h line 111
  Defined in 1 files as a macro:      include/linux/mutex.h line 168
  Defined in 6 files as a function:   kernel/locking/mutex.c line 269, kernel/locking/rtmutex_api.c line 601, ...
  Referenced in 5360 files: ...

sys_pipe2:
  Defined in 1 files as a prototype:  include/linux/syscalls.h line 469
  Defined in 2 files as a function:   fs/pipe.c line 1054, tools/include/nolibc/sys.h line 689

folio_test_dirty:
  Unknown identifier 'folio_test_dirty'
```

四個結果各有一課：

- **`anon_pipe_read`**：定義與參考分開列，`line 1259` 就是 ops table 那一行。和 rg 的結果一樣，但已經分好類。
- **`mutex_lock`**：elixir 列出**全部**候選——巨集版（`mutex.h:168`，`CONFIG_DEBUG_LOCK_ALLOC` 開啟時用）、原型（`mutex.h:189`，沒開時用）、兩個實作（一般 mutex 與 PREEMPT_RT 的 rtmutex 版本）、以及 `tools/` 底下的同名函式。**它不知道你的 `.config` 選了哪一個**。
- **`sys_pipe2`**：elixir 認得 `SYSCALL_DEFINE2(pipe2, ...)` 就是 `sys_pipe2` 的定義（`fs/pipe.c:1054`）。這是它特別替 kernel 處理過的巨集。
- **`folio_test_dirty`**：未知識別字。它由 `FOLIO_TEST_FLAG` 巨集用 `##` 拼出來（[Ch 47](./47-grep-and-regex-search.md) 看過），elixir 的 parser 沒有展開這個巨集，所以整個網站查不到它。

### elixir 適合什麼

- **跨版本比對**：把網址裡的 `v6.18` 改成 `v6.1`，同一個檔案的舊版就出來了。比 blobless clone 上的 `git grep <tag>` 快得多（[Ch 47](./47-grep-and-regex-search.md) 提過那個陷阱）。
- **分享**：寫筆記、回 mailing list、問同事，一個帶版本與行號的 elixir 連結是最精確的引用方式。
- **沒有本地 tree 的時候**：在手機上、在別人的電腦上。

它不適合的是**批次處理**：你沒辦法對 elixir 下「列出所有 `.read_iter` 的賦值」這種查詢，也沒辦法把結果接進管線。這種事要靠本地索引。

## cscope：老牌、查詢種類最多

cscope 最早是 Bell Labs 在 1980 年代為 PDP-11 寫的工具，至今仍是 kernel 開發者常用的索引器。它的強項是**查詢種類**：除了定義與呼叫者，還能查「被這個函式呼叫的」「對這個符號的賦值」。

### 建索引：先讀 kernel 的 `scripts/tags.sh`

kernel 的頂層 Makefile 有 `tags`、`TAGS`、`cscope`、`gtags` 四個目標，全部交給同一支腳本：

```make
tags TAGS cscope gtags: FORCE
	$(call cmd,tags)
```
（`Makefile:2043-2044`（v6.18）；`cmd_tags = $(BASH) $(srctree)/scripts/tags.sh $@`）

讀 `scripts/tags.sh` 能回答三個關鍵問題。

**一、索引檔寫在哪？** `docscope()` 在**目前目錄**寫 `cscope.files` 和 `cscope.out`：

```bash
docscope()
{
	(echo \-k; echo \-q; all_target_sources) > cscope.files
	cscope -b -f cscope.out
}
```
（`scripts/tags.sh:128-132`（v6.18））

`make O=<dir>` 時目前目錄是 `<dir>`，所以**用 `O=` 就不會在 source tree 裡寫任何檔案**。這正是我們要的：本課的 linux tree 不能被改動。`-k` 是 kernel mode（不去 `/usr/include` 找標頭），`-q` 是建反向索引（查詢變快，代價是多兩個大檔）。

**二、哪些檔案會被收進去？** `all_sources()` 收 `include/`、`arch/$SRCARCH/`，以及 arch 以外的整棵樹。關鍵是 arch：

```bash
# Detect if ALLSOURCE_ARCHS is set. If not, we assume SRCARCH
if [ "${ALLSOURCE_ARCHS}" = "" ]; then
	ALLSOURCE_ARCHS=${SRCARCH}
elif [ "${ALLSOURCE_ARCHS}" = "all" ]; then
	ALLSOURCE_ARCHS=$(find ${tree}arch/ -mindepth 1 -maxdepth 1 -type d -printf '%f ')
fi
```
（`scripts/tags.sh:42-47`（v6.18））

預設只收**一個** arch（沒指定 `ARCH=` 時就是主機架構 x86）。實測 `cscope.files` 裡 `arch/` 底下只有 `arch/x86/` 的 1,330 個檔案，tree 裡 `arch/` 總共有 10,405 個 `.c/.h/.S`。這個限縮是好事：否則查 `switch_to` 會得到二十幾個架構的定義。要讀別的架構就 `make ARCH=arm64 ...`，要全部就 `ALLSOURCE_ARCHS=all`。

**三、能不能只收實際編譯過的檔案？** 可以，設 `COMPILED_SOURCE=1`，tags.sh 會改走 `all_compiled_sources()`，從 build 目錄裡的 `*.cmd` 檔抓出真正參與編譯的原始檔與標頭。前提是你在**已經 build 過的** O= 目錄裡跑，因為 `.cmd` 檔在那裡。這是語法層工具最接近「懂 `.config`」的辦法——但它只能決定**收哪些檔案**，檔案內部的 `#ifdef` 它還是看不懂（後面會看到）。

### 實際建索引

```console
$ mkdir -p ~/code_reading/scratch/48/obj
$ cd ~/code_reading/linux
$ time make O=../scratch/48/obj cscope
make[1]: Entering directory '/home/ypp/code_reading/scratch/48/obj'
  GEN     cscope
make[1]: Leaving directory '/home/ypp/code_reading/scratch/48/obj'

real	2m2.713s
$ ls -la ../scratch/48/obj
-rw-r--r-- 1 ypp ypp    3575440 Nov 21 11:52 cscope.files
-rw-r--r-- 1 ypp ypp 1031357386 Nov 21 11:54 cscope.out
-rw-r--r-- 1 ypp ypp  259678208 Nov 21 11:54 cscope.out.in
-rw-r--r-- 1 ypp ypp 1062158712 Nov 21 11:54 cscope.out.po
$ head -4 ../scratch/48/obj/cscope.files
-k
-q
/home/ypp/code_reading/linux/arch/x86/include/asm/intel-mid.h
/home/ypp/code_reading/linux/arch/x86/include/asm/GEN-for-each-reg.h
$ git status --short          # linux tree 沒有任何變動
$
```

54,368 個檔案、2 分鐘、2.3 GB（`cscope.out` 是主索引，`.in`/`.po` 是 `-q` 產生的反向索引）。這次建索引時背景剛好有 `git gc` 在 repack，所以 2 分鐘可能偏高。注意 `cscope.files` 裡是**絕對路徑**：tags.sh 在 O= 模式下把 `tree` 設成 `${srctree}/`，所以索引搬到別處就失效。

如果你**不想用 kernel 的 Makefile**（例如 tree 不完整、或想自訂檔案清單），等價的手動做法是自己產生清單、自己呼叫 cscope，一樣寫在 scratch 目錄：

```bash
cd ~/code_reading/scratch/48/manual
find ~/code_reading/linux/fs ~/code_reading/linux/include -name '*.[ch]' > cscope.files
cscope -b -q -k -i cscope.files
```

`-b` 只建索引不進互動介面，`-i` 指定檔案清單。

### 命令列查詢：`-dL -<n>`

cscope 的互動介面是 curses 畫面，教材裡沒辦法貼；它的**行模式**（`-L`）把每個查詢變成一行一筆的輸出，適合腳本和管線。`-d` 表示「不要檢查原始碼是否更新、不要重建」，查詢會快很多。`-<n>` 是查詢種類：

| 旗標 | 查詢 | 讀碼問題 |
|---|---|---|
| `-0` | 這個 C 符號的所有出現處 | 全部列出來 |
| `-1` | 全域定義 | X 定義在哪？ |
| `-2` | 被這個函式呼叫的函式 | X 往下會呼叫誰？ |
| `-3` | 呼叫這個函式的函式 | 誰呼叫 X？ |
| `-4` | 文字字串 | 等同 grep |
| `-6` | egrep 模式 | regex 搜尋 |
| `-7` | 檔案 | 找檔名 |
| `-8` | include 這個檔案的檔案 | 誰 include 這個標頭？ |
| `-9` | 對這個符號的賦值 | 誰把 X 設成什麼？ |

實際跑（在 `scratch/48/obj` 裡）：

```console
$ time cscope -dL -1 anon_pipe_read
/home/ypp/code_reading/linux/fs/pipe.c anon_pipe_read 269 anon_pipe_read(struct kiocb *iocb, struct iov_iter *to)
real	0m0.013s

$ cscope -dL -3 anon_pipe_read
/home/ypp/code_reading/linux/fs/pipe.c fifo_pipe_read 409 int ret = anon_pipe_read(iocb, to);

$ cscope -dL -3 pipe_double_lock
/home/ypp/code_reading/linux/fs/splice.c splice_pipe_to_pipe 1743 pipe_double_lock(ipipe, opipe);
/home/ypp/code_reading/linux/fs/splice.c link_pipe 1866 pipe_double_lock(ipipe, opipe);
```

輸出格式是「檔案 所在函式 行號 該行內容」。第二欄**所在函式**是 rg 給不了的：你直接知道 `pipe_double_lock` 是被 `splice_pipe_to_pipe` 和 `link_pipe` 呼叫，下一步要讀誰一目了然。

`-2` 列出 `anon_pipe_read` 往下呼叫的東西（節錄前段）：

```console
$ cscope -dL -2 anon_pipe_read | head -12
/home/ypp/code_reading/linux/fs/pipe.c iov_iter_count 271 size_t total_len = iov_iter_count(to);
/home/ypp/code_reading/linux/fs/pipe.c unlikely 278 if (unlikely(total_len == 0))
/home/ypp/code_reading/linux/fs/pipe.c mutex_lock 282 mutex_lock(&pipe->mutex);
/home/ypp/code_reading/linux/fs/pipe.c smp_load_acquire 294 unsigned int head = smp_load_acquire(&pipe->head);
/home/ypp/code_reading/linux/fs/pipe.c watch_sizeof 309 n.info = watch_sizeof(n);
/home/ypp/code_reading/linux/fs/pipe.c copy_to_iter 310 if (copy_to_iter(&n, sizeof(n), to) != sizeof(n)) {
/home/ypp/code_reading/linux/fs/pipe.c pipe_empty 321 if (!pipe_empty(head, tail)) {
/home/ypp/code_reading/linux/fs/pipe.c pipe_buf 322 struct pipe_buffer *buf = pipe_buf(pipe, tail);
/home/ypp/code_reading/linux/fs/pipe.c pipe_buf_confirm 336 error = pipe_buf_confirm(pipe, buf);
/home/ypp/code_reading/linux/fs/pipe.c copy_page_to_iter 343 written = copy_page_to_iter(buf->page, buf->offset, chars, to);
/home/ypp/code_reading/linux/fs/pipe.c unlikely 344 if (unlikely(written < chars)) {
/home/ypp/code_reading/linux/fs/pipe.c pipe_full 360 wake_writer |= pipe_full(head, tail, pipe->max_usage);
```

這是一份不錯的「這個函式依賴什麼」清單，但要帶著懷疑看：`unlikely` 是巨集不是函式；`watch_sizeof` 和 `copy_to_iter(&n, ...)` 那段在 `#ifdef CONFIG_WATCH_QUEUE` 裡，x86_64 defconfig 沒開，根本沒編進去。cscope 照單全收。

`-9`（賦值）是讀 ops table 的利器：

```console
$ time cscope -dL -9 read_iter | wc -l
99
real	0m5.011s
$ cscope -dL -9 read_iter | grep pipe
/home/ypp/code_reading/linux/fs/pipe.c <global> 1248 .read_iter = fifo_pipe_read,
/home/ypp/code_reading/linux/fs/pipe.c <global> 1259 .read_iter = anon_pipe_read,
```

99 筆，和 [Ch 47](./47-grep-and-regex-search.md) 用 `rg '\.read_iter\s*='` 數出來的一樣。`<global>` 表示賦值發生在函式外（靜態初始化的 ops table）。`-9` 花了 5 秒，比 `-1` 慢很多，因為它要掃整個符號表找賦值語境。

### cscope 的盲點：三個實測例子

```console
$ cscope -dL -1 folio_test_dirty            # ## 拼出來的名字
$
$ cscope -dL -1 sys_read                    # SYSCALL_DEFINE3(read, ...) 認不得
/home/ypp/code_reading/linux/tools/include/nolibc/sys.h sys_read 729 ssize_t sys_read(int fd, void *buf, size_t count)
/home/ypp/code_reading/linux/tools/testing/selftests/proc/proc-self-syscall.c sys_read 25 static inline ssize_t sys_read(int fd, void *buf, size_t len)
$ cscope -dL -3 vfs_read                    # 條件編譯
/home/ypp/code_reading/linux/fs/exec.c read_code 832 ssize_t res = vfs_read(file, (void __user *)addr, len, &pos);
/home/ypp/code_reading/linux/fs/read_write.c ksys_read 715 ret = vfs_read(fd_file(f), buf, count, ppos);
/home/ypp/code_reading/linux/fs/read_write.c ksys_pread64 763 return vfs_read(fd_file(f), buf, count, &pos);
```

第二個最危險：你問 `sys_read` 在哪，它很有自信地給你兩個 **user-space 測試程式**裡的函式，真正的 kernel 定義（`SYSCALL_DEFINE3(read, ...)`）不在結果裡。第三個的 `read_code` 包在 `#if defined(CONFIG_BINFMT_FLAT) || defined(CONFIG_BINFMT_ELF_FDPIC)` 裡，沒編進 kernel。[Ch 49](./49-clangd-lsp.md) 會看到 clangd 對同一個查詢只回兩筆。

## GNU Global：建得快、查得快

GNU Global（指令是 `gtags` 建索引、`global` 查詢）的定位和 cscope 相近，但設計比較新：索引分成三個資料庫——`GTAGS`（定義）、`GRTAGS`（參考）、`GPATH`（路徑）——查詢極快，也比較容易接進編輯器。

### 建索引

```console
$ mkdir -p ~/code_reading/scratch/48/gobj
$ time make O=../scratch/48/gobj gtags
  GEN     gtags
real	0m39.220s
$ ls -la ../scratch/48/gobj
-rw-r--r-- 1 ypp ypp  10838016 Nov 21 11:55 GPATH
-rw-r--r-- 1 ypp ypp 598728704 Nov 21 11:55 GRTAGS
-rw-r--r-- 1 ypp ypp 857612288 Nov 21 11:55 GTAGS
```

tags.sh 的 `dogtags()` 是 `all_target_sources | gtags -i -C "${tree:-.}" -f - "$PWD"`：檔案清單和 cscope 同一套規則（只收 x86），`-C` 指定原始碼根目錄，資料庫寫到 `$PWD`（也就是 O= 目錄）。腳本裡還有一段註解值得一讀：gtags 拒絕索引目前目錄以外的檔案，所以 O= 模式下 tags.sh 會把路徑轉成絕對路徑。

### 查詢：記得設兩個環境變數

資料庫不在 source tree 裡，`global` 要靠環境變數才找得到它，**而且要在 source tree 裡執行**（`-f` 這類以檔案為單位的查詢會拒絕 tree 外的路徑）：

```console
$ cd ~/code_reading/linux
$ export GTAGSROOT=$PWD GTAGSDBPATH=$HOME/code_reading/scratch/48/gobj
$ time global -x anon_pipe_read
anon_pipe_read    269 fs/pipe.c        anon_pipe_read(struct kiocb *iocb, struct iov_iter *to)
real	0m0.002s
$ global -rx vfs_read
vfs_read          832 fs/exec.c        	ssize_t res = vfs_read(file, (void __user *)addr, len, &pos);
vfs_read          715 fs/read_write.c  		ret = vfs_read(fd_file(f), buf, count, ppos);
vfs_read          763 fs/read_write.c  		return vfs_read(fd_file(f), buf, count, &pos);
vfs_read         2413 include/linux/fs.h extern ssize_t vfs_read(struct file *, char __user *, size_t, loff_t *);
```

如果你在 gobj 目錄裡跑，`-f fs/pipe.c` 會得到 `global: 'fs/pipe.c' is out of the source project.`——這就是上面那條規則。

| 旗標 | 查詢 | 備註 |
|---|---|---|
| （無） / `-x` | 定義；`-x` 加上行號與內容 | 對應 cscope `-1` |
| `-r` | 參考（呼叫、使用） | 對應 cscope `-0`/`-3`，但**不告訴你在哪個函式裡** |
| `-s` | 「其他符號」：在 GTAGS 裡**沒有定義**的名字的出現處 | struct 成員、區域變數 |
| `-g` | grep 模式 | 對應 cscope `-6` |
| `-f <file>` | 列出一個檔案裡的所有定義 | 檔案大綱 |
| `-c <prefix>` | 補完：列出以此開頭的符號 | 不記得全名時 |

```console
$ global -sx ki_filp | head -3
ki_filp            82 block/fops.c     	bio.bi_write_hint = file_inode(iocb->ki_filp)->i_write_hint;
ki_filp           216 block/fops.c     		bio->bi_write_hint = file_inode(iocb->ki_filp)->i_write_hint;
ki_filp           344 block/fops.c     		bio->bi_write_hint = file_inode(iocb->ki_filp)->i_write_hint;
$ global -sx ki_filp | wc -l
362
$ global -c anon_pipe
anon_pipe_buf_release
anon_pipe_buf_try_steal
anon_pipe_get_page
anon_pipe_put_page
anon_pipe_read
anon_pipe_write
$ global -f fs/pipe.c | head -4
PIPE_MIN_DEF_BUFFERS   49 fs/pipe.c        #define PIPE_MIN_DEF_BUFFERS 2
pipe_lock_cmp_fn   81 fs/pipe.c        static int pipe_lock_cmp_fn(const struct lockdep_map *a,
pipe_lock          88 fs/pipe.c        void pipe_lock(struct pipe_inode_info *pipe)
pipe_unlock        95 fs/pipe.c        void pipe_unlock(struct pipe_inode_info *pipe)
```

`-s` 的用途需要解釋一下：gtags 內建的 C parser **不把 struct 成員當成定義**，所以 `ki_filp`（`struct kiocb` 的成員）落在「其他符號」那一類，要用 `-s` 才查得到它的 362 處使用。這正好可以回答「誰碰過這個欄位」。`-c` 用來找一整族函式：`anon_pipe_*` 這組名字就是匿名 pipe 的完整方法清單。

### Global 的盲點

```console
$ global -x mutex_lock
mutex_lock        168 include/linux/mutex.h #define mutex_lock(lock) mutex_lock_nested(lock, 0)
mutex_lock        269 kernel/locking/mutex.c void __sched mutex_lock(struct mutex *lock)
mutex_lock        601 kernel/locking/rtmutex_api.c void __sched mutex_lock(struct mutex *lock)
mutex_lock         58 tools/perf/util/mutex.c void mutex_lock(struct mutex *mtx)
...（共 7 筆）
$ global -x sys_pipe2
sys_pipe2         689 tools/include/nolibc/sys.h int sys_pipe2(int pipefd[2], int flags)
```

和 elixir 一樣，`mutex_lock` 給你全部候選，不知道哪一個生效；和 cscope 一樣，`sys_pipe2` 只找到 user-space 的同名函式。tags.sh 裡那一大串 `SYSCALL_DEFINE` 的 regex 只套用在 ctags 上（下一節），**gtags 用不到**。

## Universal Ctags：最輕、最多編輯器支援

ctags 產生的 `tags` 檔是一個排序過的純文字檔，每行一個定義。它**只記定義、不記參考**，所以回答不了「誰呼叫 X」，但幾乎每個編輯器都原生支援它（vim 的 `Ctrl-]` 就是查 tags 檔）。

### 單檔查詢：`-x` 與 `--fields`

不建全域索引，直接對一個檔案跑 ctags，是快速取得**檔案大綱**的好方法：

```console
$ ctags -x --c-kinds=f fs/pipe.c | head -4
SYSCALL_DEFINE1  function   1059 fs/pipe.c        SYSCALL_DEFINE1(pipe, int __user *, fildes)
SYSCALL_DEFINE2  function   1054 fs/pipe.c        SYSCALL_DEFINE2(pipe2, int __user *, fildes, int, flags)
__do_pipe_flags  function    982 fs/pipe.c        static int __do_pipe_flags(int *fd, struct file **files, int flags)
account_pipe_buffers function    767 fs/pipe.c        unsigned long account_pipe_buffers(struct user_struct *user,
$ ctags -o - --fields=+nKSe --c-kinds=fs fs/pipe.c | grep -E '^(anon_pipe_read|fifo_pipe_read)\b'
anon_pipe_read	fs/pipe.c	/^anon_pipe_read(struct kiocb *iocb, struct iov_iter *to)$/;"	function	line:269	typeref:typename:ssize_t	file:	signature:(struct kiocb * iocb,struct iov_iter * to)	end:404
fifo_pipe_read	fs/pipe.c	/^fifo_pipe_read(struct kiocb *iocb, struct iov_iter *to)$/;"	function	line:407	typeref:typename:ssize_t	file:	signature:(struct kiocb * iocb,struct iov_iter * to)	end:413
```

注意第一段的前兩行：**沒有 kernel 專用規則時，ctags 把 `SYSCALL_DEFINE1` 當成函式名**。`--fields=+nKSe` 加上行號（n）、長名稱的種類（K）、簽名（S）、結束行（e）；`end:404` 告訴你 `anon_pipe_read` 佔了 269 到 404 行——136 行的函式，讀之前先知道它多長，有助於決定要不要分段讀（[Ch 4](./04-selective-reading.md)）。

struct 成員加上 scope 欄位，可以看到匿名 union 是怎麼被命名的：

```console
$ ctags -x --c-kinds=m --_xformat='%-16N %-4n %s' include/linux/pipe_fs_i.h | awk '$1=="head"||$1=="tail"'
head             56   pipe_index::__anon6efc413b0108
head             94   pipe_inode_info::__anon6efc413b020a::__anon6efc413b0308
tail             57   pipe_index::__anon6efc413b0108
tail             95   pipe_inode_info::__anon6efc413b020a::__anon6efc413b0308
```

`pipe_inode_info` 的 `head`/`tail` 藏在「匿名 union 裡的匿名 struct」裡，ctags 用 `__anon...` 假名標出這兩層。這是在純文字裡看出巢狀結構的一個便宜方法。

### `make tags`：kernel 規則的代價

tags.sh 替 ctags 準備了一長串 regex（`regex_c` 陣列），把 `SYSCALL_DEFINEn(name` 轉成 `sys_name`、把 `PAGEFLAG(Dirty` 轉成 `PageDirty`/`SetPageDirty`/`ClearPageDirty` 等：

```bash
regex_c=(
	'/^SYSCALL_DEFINE[0-9]([[:space:]]*\([[:alnum:]_]*\).*/sys_\1/'
	...
	'/^PAGEFLAG([[:space:]]*\([[:alnum:]_]*\).*/Page\1/'
```
（`scripts/tags.sh:151-160`（v6.18），節錄）

效果很好，代價很大：

```console
$ time make O=../scratch/48/tobj tags
real	13m43.635s
$ ls -la ../scratch/48/tobj
-rw-r--r-- 1 ypp ypp 1731895176 Nov 21 12:09 tags
$ grep -P '^(anon_pipe_read|sys_pipe2|folio_test_dirty|PageDirty)\t' ../scratch/48/tobj/tags | cut -c1-120
PageDirty	/home/ypp/code_reading/linux/include/linux/page-flags.h	/^PAGEFLAG(Dirty, dirty, PF_HEAD) TESTSCFLA
anon_pipe_read	/home/ypp/code_reading/linux/fs/pipe.c	/^anon_pipe_read(struct kiocb *iocb, struct iov_iter *to)$/;"
sys_pipe2	/home/ypp/code_reading/linux/fs/pipe.c	/^SYSCALL_DEFINE2(pipe2, int __user *, fildes, int, flags)$/;"
sys_pipe2	/home/ypp/code_reading/linux/include/linux/syscalls.h	/^asmlinkage long sys_pipe2(int __user *fildes, int flags);$/;"
sys_pipe2	/home/ypp/code_reading/linux/tools/include/nolibc/sys.h	/^int sys_pipe2(int pipefd[2], int flags)$/;"
```

`sys_pipe2` 終於指到 `fs/pipe.c:1054`，`PageDirty` 也找得到了。但 **`folio_test_dirty` 仍然沒有**：tags.sh 的 regex 只涵蓋舊的 `Page*` 命名，沒跟上 folio 化。手工 regex 永遠追不上巨集的演進。

13 分 43 秒是同一份檔案清單、不帶 kernel regex 的純 ctags（`ctags -L 清單`，1 分 31 秒、1.46 GB）的 9 倍：每一行都要對幾十條 regex 比對一次。

## 對比與取捨

四種索引在同一台機器、同一個 v6.18 tree（x86 檔案清單 54,368 個檔）上的實測：

| | cscope | GNU Global | ctags（`make tags`） | ctags（純） | clangd 背景索引（Ch 49） |
|---|---|---|---|---|---|
| 建索引時間 | 2 分 3 秒 | 39 秒 | 13 分 44 秒 | 1 分 31 秒 | 約 4 分 10 秒 |
| 索引大小 | 2.3 GB | 1.4 GB | 1.7 GB | 1.5 GB | 103 MB |
| 定義 | 有 | 有 | 有 | 有 | 有（依 `.config`） |
| 呼叫者 / 參考 | 有，附所在函式 | 有，不附函式 | **無** | **無** | 有，附所在函式 |
| 賦值查詢 | 有（`-9`） | 無 | 無 | 無 | 無（但 references 涵蓋） |
| `SYSCALL_DEFINE` | 不認得 | 不認得 | 認得（靠 regex） | 不認得 | 認得（真的展開巨集） |
| `##` 拼接的名字 | 不認得 | 不認得 | 部分（只有 regex 寫到的） | 不認得 | 認得 |
| 排除未編譯的 `#ifdef` | 不能 | 不能 | 不能 | 不能 | **能** |
| 前置條件 | 原始碼 | 原始碼 | 原始碼 | 原始碼 | 能 build、有 `compile_commands.json` |

這張表的讀法：

- **語法層三兄弟的分工**：要「誰呼叫它、在哪個函式裡」用 cscope；要「快速查定義、查參考、接編輯器」用 Global；要「最輕、什麼編輯器都能用」用 ctags。
- **大小與時間不是線性關係**：Global 最快，cscope 中等，帶 regex 的 ctags 最慢。clangd 的索引小一個數量級，因為它只收實際編譯過的 3,000 個 TU（加上它們 include 的標頭），而不是整棵樹的 54,000 個檔案。
- **所有語法層工具共享同一組盲點**：巨集、條件編譯、函式指標。最後一項連 clangd 也無解，要靠 [Ch 28](./28-ops-tables-indirect-calls.md) 的方法與 [Ch 51](./51-dynamic-tracing.md) 的動態追蹤。

## 和編輯器整合

三個工具都有成熟的編輯器整合：vim/neovim 內建 `:tag`/`Ctrl-]`（ctags）與 `:cscope`（舊版 vim；neovim 0.9 起移除了內建 cscope，要靠外掛），GNU Global 附帶 `gtags-cscope` 可以假扮成 cscope 讓舊介面沿用；Emacs 有 `xref` 與 `ggtags`。

本 repo 的 [neovim 課程](../neovim/README.md) 有完整的設定與操作：[Ch 24 ctags 與 tagstack](../neovim/24-ctags-tagstack.md)、[Ch 25 GNU Global（gtags）與 cscope](../neovim/25-gtags-cscope.md)，以及說明「什麼時候 clangd 不能用、要退回 tags」的 [Ch 23](../neovim/23-why-tags-fallback.md)。這裡只強調一個原則：**編輯器整合只是查詢的快捷鍵**，查詢本身的能力與盲點，就是本章用命令列量出來的那些。

## 踩雷集錦

1. **在 source tree 裡跑 `make cscope`**：索引檔會寫進 linux tree（雖然 `.gitignore` 有列 `cscope.*`、`GTAGS`、`tags`，`git status` 看不到，但 tree 確實被改了，而且 2 GB）。用 `make O=<scratch> cscope`。
2. **以為索引涵蓋所有架構**：tags.sh 預設只收 `SRCARCH`（本機是 x86）。讀 arm64 的程式碼要 `make ARCH=arm64 O=... cscope`；要全部就 `ALLSOURCE_ARCHS=all`，索引會大很多。
3. **相信「定義只有一個」**：`mutex_lock` 在 Global 裡有 7 筆、在 elixir 裡有巨集、原型、兩個實作。語法層工具不知道哪個生效；要嘛看 `.config` 自己判斷，要嘛用 clangd。
4. **`global` 在 tree 外執行**：資料庫放在 O= 目錄時，要設 `GTAGSROOT`/`GTAGSDBPATH` 並在 source tree 裡下指令，否則檔案類查詢會說 out of the source project。
5. **把 elixir 的 `latest` 連結寫進筆記**：`latest` 會隨時間轉到新版本（現在已經是 v7.3.1），行號會錯位。永遠寫死版本。
6. **期待 ctags 回答「誰呼叫它」**：ctags 只記定義。要參考就用 cscope 或 Global。

## 進階：再往深一層

**`COMPILED_SOURCE=1` 讓索引縮小又更準。** 在已 build 的 O= 目錄（本課的 `~/code_reading/build`）跑 `make O=../build COMPILED_SOURCE=1 cscope`，檔案清單會從 `.cmd` 檔產生，只包含實際編譯過的檔案與它們 include 的標頭。這能消掉「別的 driver 裡的同名函式」這類雜訊，但**消不掉檔案內部的 `#ifdef`**——`fs/exec.c` 有被編譯，所以 `read_code` 照樣在索引裡。本章沒有在共用的 build 目錄實跑這一招（會在別人也在用的目錄裡寫 2 GB 檔案）；想試的話，先把 build 目錄複製一份，或者照 tags.sh 裡 `all_compiled_sources()` 的邏輯自己產生清單，再用前面「手動做法」那三行建索引。

**把 cscope 行模式接進管線。** `-L` 輸出是一行一筆、欄位以空白分隔，可以直接 `awk`。例如「列出 `vfs_read` 所有呼叫者的函式名」：`cscope -dL -3 vfs_read | awk '{print $2}' | sort -u`。這是 [練習 G](./practice-g-ops-callback-finder.md) 寫 callback 定位工具時的一塊積木。

**每個版本各一份索引。** 跨版本比對時，用 `git worktree add ../linux-v6.1 v6.1` 開一個獨立工作樹（不影響主 tree），再對它建一份索引。比在同一個 tree 裡切版本、重建索引省事。blobless clone 上，`worktree add` 會觸發下載整個版本的 blob，要預留時間與頻寬。

## 動手練習

1. 用 cscope `-3` 往上追 `copy_page_to_iter` 兩層，畫出呼叫樹；標出其中哪些呼叫者在你的 `.config` 裡**沒有**被編譯（用 `ls ~/code_reading/build/<path>.o` 驗證）。
2. 用 `global -sx` 找出 `struct pipe_inode_info` 的 `max_usage` 欄位在 `fs/` 以外被誰讀寫。（提示：這個名字很常見，用 `rg` 輔助過濾出真的是 `pipe->max_usage` 的那些。）
3. 在 elixir 上打開 `fs/pipe.c`，分別切到 v6.1 和 v6.18，找出 `pipe_read` 改名成 `anon_pipe_read` 前後，`file_operations` 表（`pipefifo_fops`）有什麼變化。
4. 用 `ctags -x --c-kinds=f` 列出 `fs/pipe.c` 的所有函式，再用 `--fields=+e` 算出每個函式的行數，找出最長的三個——這通常就是這個檔案的核心。

## 本章重點整理

- 索引工具把「搜尋」變成「查表」，並且把結果分類成定義、參考、賦值。
- elixir 是每個版本都建好的線上索引，最適合跨版本與分享；連結要寫死版本。
- kernel 的 `make O=<dir> cscope|gtags|tags` 由 `scripts/tags.sh` 執行，預設只收一個架構，索引寫在 O= 目錄，不污染 source tree。
- cscope 查詢種類最多（呼叫者附所在函式、賦值查詢）；Global 建得最快；ctags 只有定義但最普及。
- 三者都不跑前置處理器：`##` 拼接的名字找不到、`#ifdef` 刪掉的程式碼照樣列出、同名定義全部並列。

## 自我檢核

- [ ] 不看筆記，說出 `make O=../scratch cscope` 的索引檔寫在哪裡、為什麼這樣不會改到 linux tree、檔案清單預設涵蓋哪些架構。
- [ ] 同事說「cscope 說 `read_code` 呼叫了 `vfs_read`，所以 `read(2)` 可能走到 `read_code`」，你會怎麼反駁？要用什麼證據？
- [ ] 解釋為什麼 `sys_pipe2` 在 elixir 和 `make tags` 產生的 tags 檔裡查得到，在 cscope 和 Global 裡查不到。
- [ ] 如果你明天要讀一個你沒有 build 環境的 arm64 driver，你會建哪一種索引、怎麼下指令？
- [ ] 什麼情況下 ctags 就夠用，什麼情況下一定要 cscope 或 Global？

## 延伸閱讀

- **《Code Reading: The Open Source Perspective》10.7 節 Code Browsers and Beautifiers** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪裡**：code browser 的部分（cscope、LXR、ctags 的介紹）。
  - **學到什麼**：交叉參照工具的設計動機與典型操作；LXR 就是 elixir 的前身，二十多年後操作方式幾乎沒變。
  - **前提**：無。
- **[Elixir Cross Referencer 原始碼與說明（bootlin/elixir）](https://github.com/bootlin/elixir)** — Bootlin
  - **讀哪裡**：README 的架構說明（就我所知，它用 ctags 抽定義、用自己的 tokenizer 抽參考，細節以 README 為準）。
  - **學到什麼**：elixir 為什麼查得到 `sys_pipe2`（有 kernel 專用規則）卻查不到 `folio_test_dirty`（沒有展開巨集）；也能照著在自己的機器上架一份，索引私有的 kernel tree。
  - **前提**：會讀 Python。
- **cscope(1) man page 與 [cscope 官方網站](https://cscope.sourceforge.net/)**
  - **讀哪裡**：man page 的 `-L`、`-<n>` 與 `-d`、`-k`、`-q` 選項說明；網站上的「Using Cscope on large projects (example: the Linux kernel)」一文。
  - **學到什麼**：每一種查詢的精確定義，以及大型專案（就是拿 kernel 當例子）的建索引建議。
  - **前提**：無。
- **[GNU GLOBAL Source Code Tag System 手冊](https://www.gnu.org/software/global/globaldoc_toc.html)** — Shigio Yamaguchi 等
  - **讀哪裡**：「Command line」一章裡的 Preparation、Basic usage、Applications 三節，以及 `global` 的 `-r`、`-s`、`-x` 說明。
  - **學到什麼**：GTAGS/GRTAGS/GPATH 的分工、`GTAGSROOT`/`GTAGSDBPATH` 的語意、`gtags-cscope` 的用法。
  - **前提**：無。
- **[Universal Ctags 文件](https://docs.ctags.io/)**
  - **讀哪裡**：講輸出格式的章節（`--fields` 與 xref（`-x`）格式）、「Extending ctags with Regex parser (optlib)」。
  - **學到什麼**：本章用到的 `--fields=+nKSe`、`--_xformat` 的完整欄位表；以及 tags.sh 裡那串 regex 的語法來源。
  - **前提**：無。

語法層工具已經把「找名字」做到極限。要讓工具知道 `mutex_lock` 在你的 `.config` 下到底是哪一個、`read_code` 根本沒被編譯，需要一個真的跑過前置處理器、真的知道編譯旗標的工具。

→ [Ch 49 clangd 與 LSP：compile_commands.json 讓編輯器真的懂 kernel](./49-clangd-lsp.md)
