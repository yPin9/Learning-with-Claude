# Ch 47 — grep、ripgrep 與 regex 搜尋策略

> **目標**：能在 v6.18 的 kernel tree 上，用 ripgrep 在一秒內寫出「找定義、找 struct、找 ops 賦值、找巨集、找呼叫點、找 syscall」六種搜尋，並且知道每一種在什麼情況下會漏、會誤報，漏的時候該換哪個工具。
> **鴻溝**：本章主要練**間接鴻溝**（從名字找到定義、從 callback 找到賦值點），順帶處理**目標鴻溝**（搜尋要先有問題，搜尋模式就是問題的形狀）。

> **環境**：WSL2 Ubuntu 22.04、ripgrep 15.2.0（`cargo install` 版，**沒有編進 PCRE2**）、GNU grep 3.7、git 2.34.1；語料是 `~/code_reading/linux`（tag v6.18，blobless clone，約 62,000 個 `.c`/`.h`，工作樹 1.7 GB、`.git` 2.4 GB）。本章所有輸出與計時都在這台機器上實際跑出來；計時是熱快取（第二次以後）的數字。

## 為什麼需要這個？

Spinellis 在原書第 10 章把 regex 和 grep 放在讀碼工具的第一位，理由到今天都沒變：**讀碼時一半以上的動作是「這個名字在哪裡？」**。函式在哪定義、struct 長什麼樣、誰呼叫它、誰把它塞進 ops table——這些問題都可以先用文字搜尋回答，而且文字搜尋**不需要任何前置設定**：不用建索引、不用能編譯、不用知道 `.config`。拿到一個剛 clone 下來、還沒辦法 build 的 tree，你能用的第一個工具就是它。

2003 年的問題是 grep 太慢、regex 太難寫。今天 grep 在 kernel 這種規模的 tree 上仍然是秒級，ripgrep（`rg`）把它壓到兩百毫秒以內，慢這件事基本消失了。剩下的問題是第二個：**大部分人只會搜「一個字」，不會搜「一個形狀」**。搜 `pipe_read` 會得到 19 行、裡面沒有任何一行是定義（後面會看到為什麼）；搜「行首是函式名、後面接左括號、整行不以分號結尾」才是在問「它定義在哪」。

這一章要建立的就是這套「形狀」的詞彙，以及對它的極限的清楚認識：文字搜尋不懂 C，所以巨集拼接出來的名字、函式指標、條件編譯，它一概看不到。知道它在哪裡會瞎，你才知道什麼時候該換 [Ch 48](./48-elixir-cscope-gtags.md) 的索引工具或 [Ch 49](./49-clangd-lsp.md) 的 clangd。

## 先建立直覺

把文字搜尋想成**在一間沒有目錄的圖書館裡拿手電筒照書脊**。手電筒照得很快（rg 一秒掃完 62,000 本），但它只認得字形：它不知道哪本是小說、哪本是字典，也不知道某本書其實是另一本書的別名。

```
你的問題                 搜尋模式（形狀）                     結果                  人工篩選
─────────────────        ──────────────────────────          ─────────            ──────────
「X 定義在哪？」   ──▶   行首 + X( + 整行沒有 ;         ──▶  1~5 行       ──▶   看一眼就知道
「誰呼叫 X？」     ──▶   縮排 + \bX\s*\(                 ──▶  幾十行       ──▶   挑跟問題有關的
「X 被塞進哪個     ──▶   \.read_iter\s*=\s*X             ──▶  1~2 行       ──▶   找到 ops table
  ops table？」
                          ▲
                          └── 文字工具看不到的：巨集拼出來的名字、f->op->x() 的實際目標、
                              #ifdef 裡沒被編進去的程式碼
```

這張圖的重點是中間那一欄：**搜尋模式是你把問題翻譯成字形的結果**。翻譯得好，結果少而準；翻譯得差，要嘛漏、要嘛淹沒在幾千行裡。

> 如果你對「為什麼要先有問題才開始讀」還不熟，先回看 [Ch 3 — 帶著問題讀](./03-question-driven-reading.md)。

## ripgrep 對 grep：實測

先把「rg 比較快」這件事量清楚，因為它決定你會不會願意**隨手**搜尋。下面每一行都是在 v6.18 tree 根目錄跑的，搜尋字串是 `pipe_read`（結果都是 55 行）：

```console
$ time (grep -rn 'pipe_read' . | wc -l)
grep: ./.git/objects/pack/pack-1d93f553bbeb85a3a2cdc2af6d52bd67f59c323e.pack: binary file matches
55
real	0m2.204s

$ time (grep -rn --include='*.[ch]' 'pipe_read' . | wc -l)
55
real	0m1.542s

$ time (rg -n 'pipe_read' | wc -l)
55
real	0m0.213s

$ time (rg -j1 -n 'pipe_read' | wc -l)        # 強迫 rg 單執行緒
55
real	0m2.574s

$ time (git grep -n 'pipe_read' | wc -l)
55
real	0m3.776s
```

更多組合（同樣熱快取）：

| 搜尋 | grep -r（限 `*.[ch]`） | rg | 倍數 |
|---|---|---|---|
| `pipe_read`（字面字串） | 1.54 s | 0.21 s | 約 7× |
| `-w pipe_read`（整字） | 1.97 s | 0.25 s | 約 8× |
| `-i 'spin_lock_irqsave\|raw_spin_lock_irqsave'`（16,220 行） | 3.54 s | 0.18 s | 約 20× |
| `'^\w.*\bkmalloc\('`（grep 要 `-P` 才有 `\w`/`\b` 的這種寫法） | 5.07 s | 0.22 s | 約 23× |

幾個讀數比數字本身更重要：

1. **rg 快的主因是平行化**。`-j1` 讓它退回單執行緒，時間從 0.21 s 變成 2.57 s，和 grep 同一個量級。這台機器 16 核，rg 預設會用滿。
2. **rg 預設跳過的東西**：`.gitignore` 列出的檔案、隱藏檔與隱藏目錄（`.git/` 也在內）、二進位檔。grep -r 沒有這些規則，所以它會去掃 2.4 GB 的 `.git/objects`，還在 stderr 抱怨 pack 檔「binary file matches」。`rg -uuu` 關掉全部過濾，結果多出一行，就是那個 pack 檔。
3. **regex 越複雜，差距越大**。case-insensitive 加 alternation、或 grep 要切到 `-P` 才能用的 `\w`/`\b`，grep 會掉到 3–5 秒，rg 幾乎不變。rg 的 regex 引擎（Rust `regex` crate）用有限自動機保證線性時間，並會先抽出字面字串用 SIMD 掃描；細節看延伸閱讀裡 Andrew Gallant 和 Russ Cox 的文章。
4. **git grep 在這台機器上反而最慢**，sys time 高達 8–9 秒。我沒有深究原因（WSL 的檔案系統、這個 repo 是 blobless clone 都可能有關）；在原生 Linux 的完整 clone 上它通常不慢。這裡的重點是：**計時結果和環境綁得很死，別把別人的 benchmark 當成你的**。

### 一個會讓你懷疑人生的陷阱：rg 會讀 stdin

這是我寫這章時真的踩到的。在腳本裡（stdin 不是終端機）跑 `rg pattern` 而**沒給路徑**，rg 會去搜 stdin，而不是目前目錄：

```console
$ echo "hello pipe_read from stdin" | rg pipe_read
hello pipe_read from stdin
```

在互動式 shell 裡 stdin 是終端機，rg 判斷你想搜目錄；放進 `bash -s <<EOF` 這種 heredoc 裡，rg 會把**腳本剩下的部分**當成輸入吃掉，然後整個腳本靜悄悄地結束。解法：在腳本裡永遠明確寫路徑（`rg pattern .`），或加 `</dev/null`。

## rg 的常用旗標：每個都對應一個讀碼問題

| 旗標 | 意思 | 讀碼時的用途 |
|---|---|---|
| `-w` | 整字比對（兩側是非字元） | `pipe_read` 不要命中 `pipe_readable` |
| `-t c` | 只搜 C 類型（`rg --type-list` 顯示 `c: *.[chH], *.[chH].in, *.cats`） | 排除 Documentation、Makefile、Rust |
| `-g 'fs/**'` | 只搜符合 glob 的路徑 | 把範圍限在一個子系統 |
| `-g '!tools/'` | 排除符合 glob 的路徑（`!` 開頭） | 排除 user-space 的 `tools/`，它常有同名函式 |
| `-l` / `-c` | 只列檔名 / 每檔計數 | 「有多少檔案這樣做？」「誰用最多？」 |
| `-A n` / `-B n` / `-C n` | 顯示命中後 / 前 / 前後 n 行 | 看 ops table 其他欄位、看函式開頭幾行 |
| `-o -r '$1'` | 只輸出比對部分，並用捕獲群組改寫 | 抽出 `= xxx` 裡的 `xxx` 做統計 |
| `--pcre2` | 換成 PCRE2 引擎（支援 lookaround、backreference） | 「`=` 後面**不是** generic 的那些」 |

實際跑幾個：

```console
$ rg -l '\.read_iter\s*=\s*generic_file_read_iter' -t c | wc -l
23
$ rg -c '\.read_iter\s*=' -t c | sort -t: -k2 -nr | head -5
fs/smb/client/cifsfs.c:6
fs/proc_namespace.c:3
drivers/tty/tty_io.c:3
drivers/char/mem.c:3
io_uring/mock_file.c:2
$ rg -l '\.read_iter\s*=' -g 'fs/**' | wc -l
58
$ rg -n -B2 -A3 '\.read_iter\s*=\s*anon_pipe_read' fs/pipe.c
1257-static const struct file_operations pipeanon_fops = {
1258-	.open		= fifo_open,
1259:	.read_iter	= anon_pipe_read,
1260-	.write_iter	= anon_pipe_write,
1261-	.poll		= pipe_poll,
1262-	.unlocked_ioctl	= pipe_ioctl,
```

`-B2 -A3` 這一招在讀 ops table 時特別好用：你找到 `read_iter` 的賦值，順手就看到同一張表的 `write_iter`、`poll`，整個物件的「方法清單」一次到手。

### `--pcre2`：先確認你的 rg 有沒有

想找「`.read_iter =` 後面**不是** `generic_file_read_iter` 也不是 `seq_read_iter`」的檔案系統（也就是自己實作 read 路徑的那些），自然的寫法是 negative lookahead。但這台機器上：

```console
$ rg --version
ripgrep 15.2.0

features:-pcre2
...
$ rg -n --pcre2 '\.read_iter\s*=\s*(?!generic_file_read_iter\b|seq_read_iter\b)\w+' -t c
rg: PCRE2 is not available in this build of ripgrep
$ rg -n '\.read_iter\s*=\s*(?!generic)' -t c
rg: regex parse error:
    (?:\.read_iter\s*=\s*(?!generic))
                         ^^^
error: look-around, including look-ahead and look-behind, is not supported
```

`features:-pcre2` 那一行就是答案：用 `cargo install ripgrep` 裝的版本預設不帶 PCRE2（要 `--features pcre2` 重編）；發行版套件通常有帶。沒有的時候兩條退路：

```console
# 退路一：GNU grep -P（慢，但有 lookahead；\K 丟掉前綴只留名字）
$ grep -rhoP --include='*.c' '\.read_iter\s*=\s*\K(?!generic_file_read_iter\b|seq_read_iter\b)\w+' . \
    | sort | uniq -c | sort -nr | head -6
      2 v9fs_file_read_iter
      2 tty_read
      2 read_iter_zero
      2 proc_reg_read_iter
      2 ocfs2_file_read_iter
      2 ntfs_file_read_iter

# 退路二：rg 抽出名字，再用第二段 grep -v 排除（快，而且更好懂）
$ rg -o -N --no-filename '\.read_iter\s*=\s*(\w+)' -r '$1' -t c \
    | grep -vxE 'generic_file_read_iter|seq_read_iter' | wc -l
72
```

兩種寫法都得到 72 個自訂的 `read_iter`。我的建議是優先用退路二：**把一個複雜 regex 拆成「抽取」和「過濾」兩段管線**，每一段都簡單到一眼看懂，出錯時也知道是哪段錯。這正是 Spinellis 在 10.5 節說的「自己組工具」——Unix 管線就是最便宜的讀碼工具製造機。

## 搜尋模式食譜

下面六個模式是本課後面每一章都會用到的。每個都先看它在真實 tree 上的結果，再看它的弱點。

### 1. 找函式定義：第一版會失敗

直覺寫法是「行首有字、後面某處是 `foo(`」：

```console
$ rg -n '^\w.*\banon_pipe_read\(' -t c ; echo "rc=$?"
rc=1
```

找不到。原因在 kernel coding style：**回傳型別常常獨占一行**，函式名從第 0 欄開始：

```c
static ssize_t
anon_pipe_read(struct kiocb *iocb, struct iov_iter *to)
{
```
（`fs/pipe.c:268-270`（v6.18））

`^\w.*\b` 要求函式名前面至少還有一個字元，所以剛好漏掉這種寫法。修正版把「前綴」變成可有可無，再加上「整行沒有分號」來排除宣告與呼叫：

```console
$ rg -n '^(\w.*[\s*])?anon_pipe_read\([^;]*$' -t c
fs/pipe.c:269:anon_pipe_read(struct kiocb *iocb, struct iov_iter *to)
$ rg -n '^(\w.*[\s*])?vfs_read\([^;]*$' -t c
fs/read_write.c:552:ssize_t vfs_read(struct file *file, char __user *buf, size_t count, loff_t *pos)
$ rg -n '^(\w.*[\s*])?kfree\([^;]*$' -t c
mm/slub.c:6829:void kfree(const void *object)
include/linux/slab.h:512:DEFINE_FREE(kfree, void *, if (!IS_ERR_OR_NULL(_T)) kfree(_T))
tools/virtio/ringtest/ptr_ring.c:55:static void kfree(void *p)
tools/virtio/linux/kernel.h:80:static inline void kfree(void *p)
tools/lib/slab.c:30:void kfree(void *p)
```

拆解：`^` 行首；`(\w.*[\s*])?` 可選的「型別 + 空白或星號」；`anon_pipe_read\(` 名字緊接左括號；`[^;]*$` 到行尾都沒有分號。`kfree` 的結果示範了典型的雜訊：`DEFINE_FREE(kfree, ...)` 是巨集呼叫碰巧以 `kfree(` 開頭的片段、`tools/` 底下是 user-space 測試替身。加 `-g '!tools/'` 可以去掉後者。

### 2. 找 struct 定義

```console
$ rg -n '^struct pipe_inode_info \{'
include/linux/pipe_fs_i.h:86:struct pipe_inode_info {
```

struct 定義在 kernel 裡幾乎一律從行首開始、`{` 在同一行，這個模式非常穩。漏的情況是 `typedef struct { ... } foo_t;`（匿名 struct，名字在最後）——kernel 不鼓勵 typedef，但 driver 裡還是有。

### 3. 找 ops 賦值：間接鴻溝的第一把鑰匙

```console
$ rg -n 'read_iter' fs/pipe.c
1248:	.read_iter	= fifo_pipe_read,
1259:	.read_iter	= anon_pipe_read,
$ rg '\.read_iter\s*=' -t c | wc -l
99
```

指定初始化（designated initializer）`.field = func` 是 kernel 填 ops table 的標準寫法，所以「誰實作了 `read_iter`」可以直接搜。99 行就是 v6.18 裡 99 個 `read_iter` 實作點（包括重複使用 `generic_file_read_iter` 的）。這個模式會漏掉執行期才賦值的寫法（`ops->read_iter = foo;`），把 `\.` 換成 `(\.|->)` 可以一起抓。[Ch 28](./28-ops-tables-indirect-calls.md) 會把這個技巧發展成完整方法。

### 4. 找巨集定義

```console
$ rg -n '#\s*define\s+wait_event_interruptible\b'
include/linux/wait.h:501:#define wait_event_interruptible(wq_head, condition)				\
$ rg -n '#\s*define\s+kmalloc\b' include/
include/linux/slab.h:963:#define kmalloc(...)				alloc_hooks(kmalloc_noprof(__VA_ARGS__))
```

`#\s*define` 是因為 kernel 在巢狀 `#if` 裡會寫成 `# define`；`\b` 避免命中 `wait_event_interruptible_timeout`。第二個結果是一個好教訓：在 v6.18，**`kmalloc` 不是函式**，是包了一層 `alloc_hooks()` 的巨集，真正的函式叫 `kmalloc_noprof`。所以用「找函式定義」的模式搜 `kmalloc` 會得到空結果：

```console
$ rg -n '^(\w.*[\s*])?kmalloc\([^;]*$' -t c -g '!tools/'
$
```

找定義找不到的時候，**第二步永遠是試試看它是不是巨集**。

### 5. 找所有呼叫點

呼叫點在函式本體裡，所以一定有縮排；定義與原型從第 0 欄開始。利用這個排版事實：

```console
$ rg -n '^\s+.*\bpipe_double_lock\s*\(' -t c
fs/splice.c:1743:	pipe_double_lock(ipipe, opipe);
fs/splice.c:1866:	pipe_double_lock(ipipe, opipe);
$ rg -n '^\s+.*\bvfs_read\s*\(' -t c
fs/exec.c:832:	ssize_t res = vfs_read(file, (void __user *)addr, len, &pos);
fs/read_write.c:715:		ret = vfs_read(fd_file(f), buf, count, ppos);
fs/read_write.c:763:		return vfs_read(fd_file(f), buf, count, &pos);
```

`vfs_read` 的三個結果裡藏著一個誤報，後面「條件編譯」那段會揭曉。

### 6. 找 syscall 的進入點

syscall 不是用一般函式語法定義，而是用 `SYSCALL_DEFINEn(name, ...)` 巨集（[Ch 29](./29-syscall-entry-paths.md) 會拆它）。所以搜 `sys_pipe2` 找不到定義，要搜巨集：

```console
$ rg -n 'SYSCALL_DEFINE\d\(pipe2?\b'
fs/pipe.c:1054:SYSCALL_DEFINE2(pipe2, int __user *, fildes, int, flags)
fs/pipe.c:1059:SYSCALL_DEFINE1(pipe, int __user *, fildes)
$ rg -n 'SYSCALL_DEFINE\d\(read,'
fs/read_write.c:722:SYSCALL_DEFINE3(read, unsigned int, fd, char __user *, buf, size_t, count)
```

`\d` 讓你不用記參數個數；`pipe2?\b` 一次抓到 `pipe` 和 `pipe2`。

## 底層機制：文字搜尋為什麼會漏、為什麼會誤報

文字搜尋看到的是**前置處理之前的原始碼**。編譯器看到的是**前置處理之後**的東西（[Ch 50](./50-compiler-as-reading-tool.md) 會讓你親眼看到這個差距：`fs/pipe.c` 1,553 行，展開後 81,576 行）。兩者之間的落差，就是文字搜尋的盲區：

```
                原始碼（rg 看得到）                         前置處理後（編譯器看到）
   ┌────────────────────────────────────┐              ┌──────────────────────────────┐
   │ PAGEFLAG(Dirty, dirty, PF_HEAD)     │ ── ## 拼接 ─▶│ folio_test_dirty(...) {...}   │ ← rg 找不到定義
   │ SYSCALL_DEFINE3(read, ...)          │ ── ## 拼接 ─▶│ __x64_sys_read / __do_sys_read│ ← rg 找不到
   │ DEFINE_FREE(kfree, ...)             │ ── ## 拼接 ─▶│ __free_kfree(void *p) {...}   │ ← rg 找不到
   │ filp->f_op->read_iter(&kiocb, ...)  │ ── 原樣 ───▶│ 間接呼叫，目標在執行期決定    │ ← 誰都找不到
   │ #if defined(CONFIG_BINFMT_FLAT)     │ ── 刪掉 ───▶│ （不存在）                    │ ← rg 照樣找到
   │   ... vfs_read(...) ...             │              │                              │
   └────────────────────────────────────┘              └──────────────────────────────┘
```

逐項看真實例子。

**漏：token pasting 造出來的名字。** `folio_test_dirty` 在 53 個 C 檔裡被使用，但整個 tree 裡沒有任何一行寫著它的定義：

```console
$ rg -n '^(\w.*[\s*])?folio_test_dirty\(' -t c
$ rg -nw 'folio_test_dirty' include/linux/page-flags.h
$ rg -n 'PAGEFLAG\(Dirty' include/linux/page-flags.h
556:PAGEFLAG(Dirty, dirty, PF_HEAD) TESTSCFLAG(Dirty, dirty, PF_HEAD)
$ rg -n 'define FOLIO_TEST_FLAG\b' -A2 include/linux/page-flags.h
416:#define FOLIO_TEST_FLAG(name, page)					\
417-static __always_inline bool folio_test_##name(const struct folio *folio) \
418-{ return test_bit(PG_##name, const_folio_flags(folio, page)); }
```

`folio_test_##name` 在巨集展開時把 `dirty` 貼上去，名字才誕生。對付它的方法是**搜名字的一部分**：去掉前綴，搜 `PAGEFLAG(Dirty` 或 `folio_test_##`。syscall 也一樣：`__x64_sys_read` 在 vmlinux 的符號表裡存在，原始碼裡一次都沒出現：

```console
$ rg -nw '__x64_sys_read' -t c -t asm
$ grep -wE '__x64_sys_read|ksys_read' ~/code_reading/build/System.map
ffffffff815864e0 T ksys_read
ffffffff815865d0 T __x64_sys_read
```

oops 訊息裡看到的就是 `__x64_sys_read` 這種名字，所以你常常會從一個搜不到的名字開始讀。[Ch 14](./14-macro-metaprogramming.md) 的 token pasting 一節教你怎麼從結果名字反推巨集。

**漏：函式指標。** 想知道 `anon_pipe_read` 是被誰呼叫的，搜呼叫點：

```console
$ rg -nw 'anon_pipe_read|fifo_pipe_read'
fs/pipe.c:269:anon_pipe_read(struct kiocb *iocb, struct iov_iter *to)
fs/pipe.c:407:fifo_pipe_read(struct kiocb *iocb, struct iov_iter *to)
fs/pipe.c:409:	int ret = anon_pipe_read(iocb, to);
fs/pipe.c:1248:	.read_iter	= fifo_pipe_read,
fs/pipe.c:1259:	.read_iter	= anon_pipe_read,
```

唯一的「呼叫」是 `fifo_pipe_read` 包了它一層。真正從 `read(2)` 進來的路徑走的是 `fs/read_write.c:491` 的 `filp->f_op->read_iter(&kiocb, &iter)`——那一行沒有 `anon_pipe_read` 這個字。文字搜尋能給你的是兩端：「誰在呼叫 `->read_iter(`」（11 個檔案）和「誰被塞進 `.read_iter`」（99 行），**中間的連線要你自己接**：先找 `.read_iter = anon_pipe_read` 在哪張表（`pipeanon_fops`），再找這張表在哪裡被掛到 `struct file` 上。這就是間接鴻溝，[Ch 28](./28-ops-tables-indirect-calls.md) 專門處理。

**漏：改名。** 這章一開始說搜 `pipe_read` 找不到定義，原因是它在 v6.15 被改名了：

```console
$ git log --oneline -S'anon_pipe_read' -- fs/pipe.c
f017b0a4951fa pipe: don't update {a,c,m}time for anonymous pipes
$ git tag --contains f017b0a4951fa | grep -v rc | sort -V | head -1
v6.15
```

網路上的文章、你的記憶、LLM 的回答，很多都還停在 `pipe_read`。**搜不到的時候先懷疑名字，不要懷疑自己。**

**誤報：條件編譯。** 回頭看 `vfs_read` 的三個呼叫點，`fs/exec.c:832` 那一個：

```c
#if defined(CONFIG_BINFMT_FLAT) || defined(CONFIG_BINFMT_ELF_FDPIC)
ssize_t read_code(struct file *file, unsigned long addr, loff_t pos, size_t len)
```
（`fs/exec.c:829-830`（v6.18））

x86_64 defconfig 沒開這兩個選項，這整段根本沒被編進 kernel。文字搜尋不知道 `.config`，照樣回報。誤報的另外兩大來源是**註解**（`rg -nw pipe_read` 的 19 行裡有 6 行落在註解裡）和**同名不同物**（`tools/` 底下 selftest 裡叫 `pipe_read` 的區域變數）。

## 跨版本搜尋：git grep 加 tag

`git grep` 最被低估的能力是**直接搜歷史版本的樹，不用 checkout**：

```console
$ git grep -n -w -e pipe_read -e anon_pipe_read v6.1 v6.12 v6.18 -- fs/pipe.c | grep -v '^\S*: \*'
v6.1:fs/pipe.c:231:pipe_read(struct kiocb *iocb, struct iov_iter *to)
v6.1:fs/pipe.c:1221:	.read_iter	= pipe_read,
v6.12:fs/pipe.c:251:pipe_read(struct kiocb *iocb, struct iov_iter *to)
v6.12:fs/pipe.c:1234:	.read_iter	= pipe_read,
v6.18:fs/pipe.c:269:anon_pipe_read(struct kiocb *iocb, struct iov_iter *to)
v6.18:fs/pipe.c:409:	int ret = anon_pipe_read(iocb, to);
v6.18:fs/pipe.c:1259:	.read_iter	= anon_pipe_read,
```

一行指令就把改名事件釘在 v6.12 和 v6.18 之間。語法是 `git grep <pattern> <tree-ish>... -- <pathspec>`，tree-ish 可以是 tag、branch、commit。

**blobless clone 的警告。** 本課的 tree 是 `--filter=blob:none` 的 clone，舊版本的檔案內容（blob）不在本地。我試著對 `v6.1 -- fs/` 整個目錄做查詢，觀察到 git 為**每一個缺少的 blob 各啟動一次** `git fetch --filter=blob:none --stdin`，還順便觸發了 `git gc --auto` 的 repack；跑了兩分鐘還沒結束，我手動中止。單一檔案（像上面的 `fs/pipe.c`）沒問題，一次只抓幾個 blob。結論：**在 partial clone 上，跨版本 git grep 要把 pathspec 收窄到幾個檔案**；需要大範圍跨版本搜尋，用完整 clone，或改用 elixir（[Ch 48](./48-elixir-cscope-gtags.md)）。

## 對比與取捨

| 工具 | 速度（本機，全 tree） | 預設過濾 | 跨版本 | regex | 適合 |
|---|---|---|---|---|---|
| `grep -r` | 1.5–5 s | 無（會掃 `.git`） | 不行 | BRE/ERE；`-P` 有 PCRE | 沒有 rg 的機器、單一檔案 |
| `rg` | 0.2 s | `.gitignore`、隱藏檔、二進位 | 不行 | Rust regex（無 lookaround）；`--pcre2` 要編譯時開 | 日常主力 |
| `git grep` | 本機 3.8 s（環境相依） | 只搜 git 追蹤的檔案 | **可以**（`<tag> --`） | BRE/ERE/`-P` | 跨版本比對、確保不搜到產生檔 |
| 索引工具（Ch 48） | 建索引數十秒到十幾分鐘，查詢毫秒級 | 依 tags.sh 規則 | 每個版本各建一份 | 有限 | 反覆查同一個 tree |
| clangd（Ch 49） | 建索引數分鐘 | 只看實際編譯的檔案 | 不行 | 無 | 需要語意正確的時候 |

## 踩雷集錦

1. **「搜不到就代表不存在」**：錯。搜不到可能是改名（`pipe_read` → `anon_pipe_read`）、是巨集拼接（`folio_test_dirty`）、是巨集包裝（`kmalloc` → `kmalloc_noprof`）。正確反應是依序檢查：名字對不對、是不是巨集、是不是 `##` 造出來的。
2. **「搜到就代表會被執行」**：錯。`fs/exec.c:832` 的 `vfs_read` 呼叫在 x86_64 defconfig 根本沒編進去。要確認「有沒有編進去」，看 `.config`、看 `.o` 存不存在、看 `nm vmlinux`（[Ch 36](./36-kconfig-kbuild-ifdef.md)、[Ch 50](./50-compiler-as-reading-tool.md)）。
3. **「找定義用 `^\w.*foo\(` 就夠了」**：kernel 的回傳型別常常獨占一行，這個模式會整批漏掉。用 `^(\w.*[\s*])?foo\([^;]*$`。
4. **在腳本裡跑 `rg pattern` 不給路徑**：stdin 不是終端機時 rg 會搜 stdin，可能把整個 heredoc 吃掉。腳本裡一律寫 `rg pattern .` 或加 `</dev/null`。
5. **以為 `--pcre2` 一定能用**：`cargo install` 的 rg 預設沒有 PCRE2。先看 `rg --version` 的 `features:` 行；沒有就拆成「抽取 + `grep -v`」兩段。
6. **在 partial clone 上對大目錄做跨版本 `git grep`**：會逐一下載缺少的 blob，又慢又可能觸發 gc。收窄 pathspec。

## 進階：再往深一層

**把搜尋變成工具。** 讀一個子系統時，你會反覆問同一類問題。與其每次重打 regex，不如寫成 shell 函式：

```bash
# 放進 ~/.bashrc：kdef 找定義，kcall 找呼叫點，kops 找 ops 賦值
kdef()  { rg -n "^(\w.*[\s*])?$1\([^;]*\$" -t c -g '!tools/' . ; rg -n "#\s*define\s+$1\b" -t c -g '!tools/' . ; }
kcall() { rg -n "^\s+.*\b$1\s*\(" -t c -g '!tools/' . ; }
kops()  { rg -n "(\.|->)$1\s*=\s*\w+" -t c -g '!tools/' . ; }
```

`kdef` 同時搜函式和巨集，正好處理踩雷 1 的第二步。這三個函式在本機實測過：`kdef kmalloc` 回傳 `include/linux/slab.h:963` 的巨集定義（外加 `lib/decompress_unxz.c:164` 開機前解壓縮環境（pre-boot）把 `kmalloc` 改接到 `malloc` 的替身），`kcall pipe_double_lock` 回傳 splice.c 的兩行，`kops read_iter | wc -l` 是 100——比 `\.read_iter\s*=` 的 99 多一行，多的就是一個 `->read_iter = ...` 的執行期賦值。

**統計式讀碼。** `-o -r '$1'` 加 `sort | uniq -c` 能回答「最常見的做法是什麼」：上面的退路二顯示 72 個 `read_iter` 是自訂實作，其餘 27 個用 `generic_file_read_iter` 或 `seq_read_iter`。這種數字能幫你決定先讀哪個實作——讀最典型的那個，而不是第一個搜到的那個。

**什麼時候該停止用 grep。** 當你發現自己在寫第三層 `| grep -v` 來排除誤報，或在手動追第二層函式指標，就是該換工具的時候。[Ch 48](./48-elixir-cscope-gtags.md) 的索引工具把「找定義 / 找呼叫者」做成一次查詢；[Ch 49](./49-clangd-lsp.md) 的 clangd 能排除沒編譯的程式碼。

## 動手練習

1. 用本章的「找函式定義」模式找 `copy_page_to_iter` 的定義。如果第一次沒找到，按照踩雷 1 的順序排查，記下它到底是函式、巨集、還是 inline wrapper。
2. 算出 v6.18 裡有多少個 `file_operations` 實作了 `.splice_read`，其中有幾個用的是 `filemap_splice_read`。用「抽取 + 過濾」兩段管線寫。
3. 用 `git grep` 加幾個 tag（例如 v6.1、v6.6、v6.12、v6.18），確認 `pipe_inode_info` 的 `head`/`tail` 欄位在哪兩個版本之間被包進 `union`（搜 `head_tail`）。只搜 `include/linux/pipe_fs_i.h` 一個檔案，再用 [Ch 37](./37-git-archaeology.md) 的 pickaxe 把範圍縮到單一 commit。
4. 故意在腳本裡寫一個不帶路徑的 `rg`，用 heredoc 跑，觀察腳本怎麼提早結束；再加 `</dev/null` 修好。

## 本章重點整理

- 文字搜尋是零設定的第一工具；rg 在 kernel tree 上比 grep -r 快 7–23 倍，主因是平行化與預設過濾。
- 搜尋模式是問題的形狀：定義、struct、ops 賦值、巨集、呼叫點、syscall 各有一個可靠的模式。
- 文字搜尋看的是前置處理前的原始碼：`##` 拼出來的名字它找不到，`#ifdef` 刪掉的程式碼它照樣找到，函式指標的實際目標它永遠看不到。
- `git grep <pattern> <tag> -- <path>` 能跨版本搜尋；在 partial clone 上要收窄路徑。

## 自我檢核

- [ ] 不看筆記，寫出「找函式定義」的 regex，並解釋為什麼 `^\w.*\bfoo\(` 在 kernel 裡會漏。
- [ ] 有人跟你說「我 grep 過了，這個函式沒有人呼叫」，你會問他哪三個問題？
- [ ] 用自己的話解釋：為什麼 `rg -j1` 會比 `rg` 慢十倍以上？這說明 rg 的速度主要來自哪裡？
- [ ] 給你一個 oops 裡的符號名 `__x64_sys_openat`，你要怎麼用文字搜尋找到它的原始碼？
- [ ] 什麼情況下你會放棄 grep，換成索引工具或 clangd？說出至少兩個訊號。

## 延伸閱讀

- **《Code Reading: The Open Source Perspective》第 10 章 Code-Reading Tools** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪裡**：10.1（Regular Expressions）、10.3（Code Searching with grep）、10.5（Tools of Your Own）。
  - **學到什麼**：用 regex 表達「程式碼的形狀」這個想法的原型，以及用管線組合小工具的態度。例子是 2003 年的 NetBSD，但方法完全沒過時。
  - **前提**：會基本 regex 即可。
- **[ripgrep User Guide（GUIDE.md）](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md)** — Andrew Gallant
  - **讀哪裡**：「Automatic filtering」「Manual filtering: globs」「Manual filtering: file types」三節，以及「Replacements」。
  - **學到什麼**：rg 預設跳過哪些檔案、`-g`/`-t` 的精確語意、`-r` 改寫輸出的用法；本章的旗標表是它的子集。
  - **前提**：無。
- **[ripgrep is faster than {grep, ag, git grep, ucg, pt, sift}](https://blog.burntsushi.net/ripgrep/)** — Andrew Gallant（2016）
  - **讀哪裡**：「Anatomy of a grep」與 linux kernel 那組 benchmark 的分析段落。
  - **學到什麼**：rg 為什麼快——字面字串抽取、SIMD、平行目錄走訪、ignore 規則的成本。也是少數誠實討論 benchmark 方法論的工具文章，對照本章「計時和環境綁得很死」的提醒。
  - **前提**：知道 DFA/NFA 是什麼有幫助，不知道也能讀懂大意。
- **[Regular Expression Matching Can Be Simple And Fast](https://swtch.com/~rsc/regexp/regexp1.html)** — Russ Cox（2007）
  - **讀哪裡**：整篇，特別是開頭那張「Perl vs Thompson NFA」的效能圖。
  - **學到什麼**：為什麼 backtracking 引擎（PCRE）在某些模式上會指數爆炸，而 Thompson NFA / DFA 保證線性時間；這解釋了 rg 預設引擎為什麼不支援 lookaround 和 backreference。
  - **前提**：大學程度的自動機概念。
- **`git help grep`（git-grep(1) man page）**
  - **讀哪裡**：`<tree>...` 參數、`--` pathspec、`-e`/`--and`/`--or` 組合查詢。
  - **學到什麼**：跨版本搜尋的完整語法；`--and` 可以做到「同一行同時出現 A 和 B」這種 rg 要用管線才做得到的查詢。

文字搜尋每次都從零開始掃。當你發現自己對同一個 tree 問了第二十次「X 定義在哪」，就該讓機器先建好目錄。

→ [Ch 48 elixir、cscope 與 GNU Global](./48-elixir-cscope-gtags.md)
