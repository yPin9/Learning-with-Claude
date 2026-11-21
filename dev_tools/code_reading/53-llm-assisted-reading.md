# Ch 53 — 用 LLM 輔助讀碼，以及它會在哪裡騙你

> **目標**：知道 LLM 在讀碼的哪些環節真的省時間、在哪些環節會給你流暢但錯誤的答案；能把「LLM 說的」轉成「有 `file:line` 證據的」，並寫出讓 LLM 比較不容易騙你的提示詞。
> **鴻溝**：本章橫跨**五道鴻溝**——LLM 對慣用法與領域鴻溝幫助最大，對間接與脈絡鴻溝最危險，對目標鴻溝則是兩面刃。

> **環境**：本章的每一個「LLM 容易講錯」的例子都在 Linux v6.18 tree（`~/code_reading/linux`）上用 `rg`、`git log`、`git show <tag>:<path>` 或 QEMU guest 驗證過，指令與輸出附在旁邊。LLM 本身的能力隨模型版本快速變化，本章不比較特定產品，談的是**結構性**的弱點：只要模型是從某個時間點以前的程式碼訓練出來的，這些弱點就存在。
>
> 一個誠實的揭露：這門課的章節是由 LLM 撰寫、由工具驗證的。本章有兩個例子就發生在寫這門課的過程中。

## 為什麼需要這個？

Spinellis 寫《Code Reading》的 2003 年，讀碼的輔助工具是 grep、ctags、cscope 和人。今天多了一個會說話的工具：你貼一段程式碼，它用自然語言解釋；你問「這個巨集是幹嘛的」，它三秒鐘給你答案。

對讀者來說，這是一個巨大的誘惑。[Ch 1](./01-why-you-cant-read-it.md) 的五道鴻溝裡，有幾道正好是 LLM 最擅長的：看不懂 `container_of` 的指標運算（慣用法鴻溝）、不知道 page cache 是什麼（領域鴻溝）——問一下就懂了。

問題在於 LLM 的錯誤和正確答案**長得一模一樣**。它不會說「這段我不太確定」，除非你要求它說；它會用同樣流暢、同樣自信的語氣告訴你一個五年前就被刪掉的函式名。讀者分辨不出來，因為他就是來學的。這和 [learn skill 的正確性關卡](../../.claude/skills/learn/SKILL.md)要求作者「事實斷言要查，不要靠記憶」是同一個道理：**模型記憶對版本細節的錯誤率很高，而且錯得很自信**。

所以這章不是要你別用 LLM，而是要你把它放在對的位置：**假說產生器，不是事實來源**。

## 先建立直覺

把 LLM 想成一位讀過大量 kernel 程式碼的資深同事，但有三個怪癖：

1. 他讀的是**好幾年份混在一起**的版本，記不清哪個細節屬於哪一版。
2. 他**從不翻原始碼**，所有回答都憑記憶。
3. 他**很少說「我不知道」**，記憶模糊的地方會用「聽起來合理」的內容補上。

```
            你的問題
               │
               ▼
     ┌───────────────────┐
     │   LLM（記憶）      │  ← 訓練資料：v2.6 ... v6.x 混在一起
     └───────────────────┘
               │ 流暢的答案（對錯混雜，語氣一致）
               ▼
     ┌───────────────────┐
     │   假說             │  ← 這裡是它該停的地方
     └───────────────────┘
               │ rg / clangd / git / ftrace / gdb
               ▼
     ┌───────────────────┐
     │   有 file:line 的   │
     │   結論             │  ← 只有這個能寫進你的筆記
     └───────────────────┘
```

這個比喻有一個地方不精確：真正的資深同事知道自己哪裡記不清，LLM 在預設狀態下對「哪裡記不清」的自我評估並不可靠。所以後面的提示詞技巧，有一半是在逼它把不確定的地方說出來。

## LLM 擅長什麼

這些場景中，LLM 的答案通常是對的，或者錯了也很容易被發現：

| 場景 | 為什麼適合 | 驗證成本 |
|---|---|---|
| **解碼慣用法**：「`container_of(ptr, type, member)` 在做什麼？」 | 這類慣用法幾十年沒變，訓練資料裡有成千上萬份解釋 | 低：對照 [Ch 16](./16-container-of.md) 或 `include/linux/container_of.h` |
| **解釋巨集展開的意圖**：「`DEFINE_SHOW_ATTRIBUTE(foo)` 會產生什麼？」 | 模式固定 | 低：`make O=../build fs/xxx.i` 看真正的展開（[Ch 50](./50-compiler-as-reading-tool.md)） |
| **摘要長函式**：「把這 170 行的 `anon_pipe_write` 分成幾個階段」 | 你**貼了原始碼**，它在做的是閱讀理解，不是回憶 | 中：逐段對照 |
| **補領域背景**：「pipe 的 ring buffer 為什麼用 head/tail 而不是 count？」 | 概念層的知識穩定 | 中：對照 LWN、Documentation |
| **產生假說與問題清單**：「讀 `fs/pipe.c` 時我該問哪些問題？」 | 錯了也沒損失，只是多一個假說 | 不需要：問題本身不用驗證 |
| **翻譯 C 的語法細節**：「`(void)` 轉型、`!!x`、`x & (x - 1)` 是什麼意思？」 | 語言規則不隨 kernel 版本變 | 低 |

共通點：**答案不依賴特定版本的特定事實**，或者**你把事實（原始碼）放進了問題裡**。

## LLM 不擅長什麼：實測七個例子

下面每個例子都是「憑記憶回答時很容易出現的說法」，後面接 v6.18 上的驗證。這些不是故意設計來讓 LLM 出糗的刁鑽題，而是讀 `fs/pipe.c`、`fs/read_write.c` 時最自然會問的問題。

### 例 1：函式改名——`pipe_write` 已經不存在

「匿名 pipe 的 `write_iter` 是哪個函式？」幾乎所有教材、部落格、舊版 kernel 都會告訴你 `pipe_write`。這門課的任務說明（由另一個 LLM 撰寫）原本也寫著「用 gdb `b pipe_write`」。驗證：

```console
$ cd ~/code_reading/linux
$ rg -n '^pipe_write|^anon_pipe_write|\.write_iter' fs/pipe.c
431:anon_pipe_write(struct kiocb *iocb, struct iov_iter *from)
1249:	.write_iter	= fifo_pipe_write,
1260:	.write_iter	= anon_pipe_write,
$ git show v6.1:fs/pipe.c | grep -n "^pipe_write\|\.write_iter\|^pipe_read"
231:pipe_read(struct kiocb *iocb, struct iov_iter *to)
416:pipe_write(struct kiocb *iocb, struct iov_iter *from)
1222:	.write_iter	= pipe_write,
$ git log --oneline -S'anon_pipe_write' -- fs/pipe.c
f017b0a4951f pipe: don't update {a,c,m}time for anonymous pipes
$ git tag --contains f017b0a4951f | grep -vE 'rc|-' | sort -V | head -1
v6.15
```

v6.1 還是 `pipe_write`；f017b0a4951f（Oleg Nesterov，2025-02）把它拆成 `anon_pipe_write` 與包一層的 `fifo_pipe_write`，v6.15 起生效。照舊名設斷點，gdb 會說 `Function "pipe_write" not defined.`，ftrace 會說 `write error: Invalid argument`（[Ch 51](./51-dynamic-tracing.md)、[Ch 52](./52-qemu-gdb-kernel.md) 都實測過）。

這是**最好**的一種錯誤：它會吵。

### 例 2：呼叫鏈裡消失的一層——`__vfs_write`

「`vfs_write` 怎麼走到 `write_iter`？」憑 2019 年以前的記憶，答案會是 `vfs_write → __vfs_write → new_sync_write → f_op->write_iter`。

```console
$ rg -n '__vfs_write' fs/ include/
$ echo $?
1
$ git log --format='%h %ad %an %s' --date=short --grep='__vfs_write' v5.0..v6.0 -- fs/read_write.c
53ad86266bda 2020-05-13 Christoph Hellwig fs: remove __vfs_write
5f739e4a491a 2019-03-12 Linus Torvalds Merge branch 'work.misc' of git://git.kernel.org/pub/scm/linux/kernel/git/viro/vfs
12e1e7af1a55 2019-02-20 Geert Uytterhoeven vfs: Make __vfs_write() static
$ git tag --contains 53ad86266bda | grep -vE 'rc|-' | sort -V | head -1
v5.8
```

`rg` 什麼都沒找到（exit code 1），`git log` 告訴你它在 v5.8 被「Fold it into the two callers」。這個錯誤比例 1 安靜：你不會去 `b __vfs_write`，你只會在筆記裡畫一張多一層的呼叫圖。

### 例 3：API 整批改名——timer 家族

kernel 的 timer API 在 2025 年經歷了一輪 treewide 改名。憑記憶寫 timer 程式碼的人（和模型）會寫出 `del_timer_sync()` 和 `from_timer()`：

```console
$ grep -n "del_timer\b\|timer_delete_sync\b" include/linux/timer.h
33: * IRQ handlers, for example, by calling timer_delete_sync().
166:extern int timer_delete_sync(struct timer_list *timer);
$ rg -n "define from_timer|define timer_container_of" include/linux/timer.h
132:#define timer_container_of(var, callback_timer, timer_fieldname)	\
$ git log --format='%h %ad %an %s' --date=short --grep='from_timer' -i v6.12..v6.18 -- include/linux/timer.h
41cb08555c41 2025-05-09 Ingo Molnar treewide, timers: Rename from_timer() to timer_container_of()
$ git log --format='%h %ad %an %s' --date=short --grep='del_timer' -i v6.12..v6.18 -- include/linux/timer.h
367ed4e35734 2025-05-07 Ingo Molnar treewide, timers: Rename try_to_del_timer_sync() as timer_delete_sync_try()
8fa7292fee5c 2025-04-05 Thomas Gleixner treewide: Switch/rename to timer_delete[_sync]()
```

`git tag --contains` 顯示 8fa7292fee5c 在 v6.15、41cb08555c41 在 v6.16。`git show 8fa7292fee5c -- include/linux/timer.h` 可以看到被刪掉的相容層，它的註解寫著「Do not use in new code. Use timer_delete_sync() instead.」——舊名字留了好幾個版本當過渡，最後才刪。這解釋了為什麼模型的記憶特別頑固：舊名字在訓練資料裡出現的次數遠多於新名字。

同類例子：`strlcpy()` 在 v6.8 被移除（d26270061ae6「string: Remove strlcpy()」，Kees Cook）；x86 的 `text_poke_bp()` 在 v6.16 改名為 `smp_text_poke_single()`（9586ae48e785，Ingo Molnar）。

### 例 4：struct 欄位改變——`file_operations`

「列出 `struct file_operations` 的主要欄位。」憑記憶的清單常常包含 `iterate`、缺少 `fop_flags` 和 `mmap_prepare`。[Ch 52](./52-qemu-gdb-kernel.md) 的 gdb 實際印出的 v6.18 `pipeanon_fops` 是權威答案，這裡用 git 找出每個差異的來源：

```console
$ grep -n "iterate\b\|iterate_shared\|fop_flags_t fop_flags\|mmap_prepare)" include/linux/fs.h | head -3
2270:	fop_flags_t fop_flags;
2278:	int (*iterate_shared) (struct file *, struct dir_context *);
2311:	int (*mmap_prepare)(struct vm_area_desc *);
```

| 變更 | commit | 最早 tag |
|---|---|---|
| 移除舊的 `->iterate`，只留 `->iterate_shared` | 3e3271549670「vfs: get rid of old '->iterate' directory operation」（Linus Torvalds） | v6.5 |
| 新增 `fop_flags`（把一部分 `FMODE_*` 移到 ops table） | 210a03c9d51a「fs: claw back a few FMODE_* bits」（Christian Brauner） | v6.10 |
| 新增 `mmap_prepare` | c84bf6dd2b83「mm: introduce new .mmap_prepare() file callback」（Lorenzo Stoakes） | v6.16 |

（找法：`git log --grep=<關鍵字> <舊tag>..<新tag> -- include/linux/fs.h` 先用 commit message 縮小範圍，再 `git tag --contains`。在 blobless clone 上，`git log -S` 對 `include/linux/fs.h` 這種大檔案要下載大量歷史 blob，會跑很久；`--grep` 只看 commit message，幾秒就有結果。這是 [Ch 37](./37-git-archaeology.md) pickaxe 的實務變通。）

### 例 5：lock 規則變了——`pipe_write` 裡的 spinlock

「`pipe_write` 更新 `pipe->head` 時拿什麼 lock？」如果記憶來自 v5.x 到 v6.6，答案會是「`pipe->mutex`，外加 `spin_lock_irq(&pipe->rd_wait.lock)` 保護 head 的更新」。v6.18 的 `anon_pipe_write` 第 530 行附近：

```c
/* fs/pipe.c:528（v6.18） */
			}

			pipe->head = head + 1;
			/* Insert it into the buffer array */
			buf = pipe_buf(pipe, head);
```

沒有 spinlock。考古：

```console
$ git show dfaabf916b1c --format='%s%n%n%b' -- fs/pipe.c | head -12
fs/pipe: remove unnecessary spinlock from pipe_write()

This reverts commit 8df441294dd3 ("pipe: Check for ring full inside of
the spinlock in pipe_write()") which was obsoleted by commit
c73be61cede ("pipe: Add general notification queue support") because
now pipe_write() fails early with -EXDEV if there is a watch_queue.

Without a watch_queue, no notifications can be posted to the pipe and
mutex protection is enough, as can be seen in splice_pipe_to_pipe()
which does not use the spinlock either.
```

`git tag --contains dfaabf916b1c` 是 v6.7。同一時期還有 85f273a6a1e4（Kent Overstreet，v6.9）刪掉了 `__pipe_lock()`/`pipe_lock_nested()`，`anon_pipe_write` 現在直接呼叫 `mutex_lock(&pipe->mutex)`（第 458 行）。

lock 規則的錯誤是**最危險**的一類：程式碼看起來都合理，錯誤的 lock 解釋會讓你在 review 或寫 patch 時做出錯誤判斷（多拿一個 lock 變成 deadlock，少拿一個變成 race）。[Ch 30](./30-reading-locking-code.md) 的規則在這裡加一條：**lock 規則只信當前版本的原始碼和註解，不信任何人的記憶**。

### 例 6：捏造不存在的名字

前五個例子都是「曾經存在、現在變了」。另一類是「從來沒存在過，但聽起來很合理」。例如 `pipe_write_iter`——它符合 kernel 的命名習慣（`xxx_write_iter` 很常見），問「pipe 的 write_iter 實作叫什麼」時是一個很自然的錯誤答案。驗證「從來沒有」比驗證「現在沒有」多一步：

```console
$ rg -c 'pipe_write_iter' .
$ echo "rg exit=$?"
rg exit=1
$ git grep -c 'pipe_write_iter' v4.0 v5.0 v6.0 v6.18 -- fs/pipe.c include/linux/pipe_fs_i.h
$ echo "exit=$?"
exit=1
```

v6.18 整棵 tree 找不到，四個相隔多年的 tag 在相關檔案裡也找不到。嚴格說，這只證明「在這些 tag 的這些檔案裡沒有」，不是數學上的「從來沒有」；但對讀碼來說足夠了。捏造的欄位名、巨集名、Kconfig 選項、commit hash 都用同樣的方法檢查。**commit hash 特別要查**：`git show <hash>` 回答 `fatal: bad object` 就是捏造的。

### 例 7：間接呼叫的「合理猜測」——`/dev/null`

「`write(fd, buf, n)` 寫到 `/dev/null` 時，`vfs_write` 會呼叫誰？」最自然的推理：先找 `/dev/null` 的 ops table，看到 `.write_iter = write_iter_null`，所以答案是 `write_iter_null`。

這個推理每一步都看似有原始碼支持，但結論是錯的。寫 [Ch 51](./51-dynamic-tracing.md) 時，作者（一個 LLM）用 kprobe 讀出 `file->f_op->write_iter` 是 `write_iter_null`，一度就這樣寫進草稿了。用 function_graph 實際跑一次：

```
 1)               |  ksys_write() {
 1)   1.933 us    |    fdget_pos();
 1)               |    vfs_write() {
 1)   4.739 us    |      rw_verify_area();
 1)   3.687 us    |      write_null();
 1) + 18.074 us   |    }
 1) + 25.898 us   |  }
```

是 `write_null`。原因在 `drivers/char/mem.c:644` 的 `null_fops` 同時有 `.write = write_null` 和 `.write_iter = write_iter_null`，而 `vfs_write()` 的分派順序是：

```c
/* fs/read_write.c:683（v6.18） */
	if (file->f_op->write)
		ret = file->f_op->write(file, buf, count, pos);
	else if (file->f_op->write_iter)
		ret = new_sync_write(file, buf, count, pos);
```

LLM 對間接呼叫的回答，本質上是「找到一個看起來對的 ops table，挑一個名字看起來對的欄位」。它不會執行分派邏輯，也不知道這個檔案在這個 config 下實際是哪一種。**間接鴻溝只能用執行期證據（[Ch 51](./51-dynamic-tracing.md)、[Ch 52](./52-qemu-gdb-kernel.md)）或完整讀過分派邏輯來跨越**。

## 底層機制：為什麼會這樣錯

這些錯誤不是隨機的，有可預測的結構：

```
錯誤類型           根源                                  例子
─────────────────────────────────────────────────────────────────────
版本混淆        訓練資料跨越多個版本，舊版出現次數更多    例 1、2、3、4
規則過時        lock/記憶體序規則改了，舊說法仍大量流傳   例 5
捏造            依命名慣例「生成」最可能的 token 序列     例 6
間接臆測        不執行程式，只做文字層的模式匹配          例 7
```

1. **舊版的「票數」比較多**。一個函式名存在了十五年、改名才一年，網路上（和訓練資料裡）提到舊名的文件、部落格、mailing list 討論多出一兩個數量級。模型傾向輸出出現頻率高的版本。
2. **模型輸出的是「最可能的文字」，不是「查到的事實」**。`pipe_write_iter` 是一個高機率的 token 序列，因為 kernel 裡有大量 `xxx_write_iter`。流暢性和正確性是兩件事。
3. **知識截止日**。模型有訓練資料的截止時間，之後的 kernel 版本它完全沒看過。v6.18 是 2025 年底的版本，任何截止日更早的模型對它的細節都只能猜。
4. **長上下文的注意力不均**。你貼了 600 行原始碼，模型對中間段落的利用通常比開頭和結尾差（見延伸閱讀的 Lost in the Middle）。所以「我貼了整個檔案，它應該都看了」不是安全的假設。

## 正確的工作流：假說 → 驗證 → 只收有證據的結論

把 [Ch 3](./03-question-driven-reading.md) 的假說驗證迴圈套上 LLM：

```
1. 你帶著問題   「anon_pipe_write 什麼時候會睡？」
       │
2. LLM 產生假說  「pipe 滿了（pipe_full）且不是 O_NONBLOCK 時，
       │          在 wr_wait 上 wait_event_interruptible_exclusive」
       │
3. 拆成可驗證的斷言，每條指定驗證工具
       │   a. 有 wait_event_interruptible_exclusive(pipe->wr_wait, ...) 這一行  → rg
       │   b. 條件是 pipe_full 且非 O_NONBLOCK / IOCB_NOWAIT              → 讀那幾行
       │   c. 睡之前會先放掉 pipe->mutex                                   → 讀那幾行
       │   d. 實際行為：寫滿 16 頁時會走到這裡                              → ftrace/gdb
       │
4. 執行驗證，記下 file:line
       │   a. fs/pipe.c:573  ✓
       │   b. fs/pipe.c:510、551-552 ✓
       │   c. fs/pipe.c:569 mutex_unlock 在 573 之前 ✓
       │   d. （留作 Ch 51 動手練習 3）
       │
5. 寫進筆記的只有第 4 步的結果
```

驗證工具的選擇，對照斷言的類型：

| 斷言類型 | 例子 | 驗證工具 |
|---|---|---|
| 某名字存在／不存在 | 「有個函式叫 X」 | `rg -n '\bX\b'`；clangd go-to-definition（[Ch 49](./49-clangd-lsp.md)） |
| 某名字何時改變 | 「X 在 vN 加入/移除」 | `git log --grep`、`git log -S`、`git tag --contains`（[Ch 37](./37-git-archaeology.md)） |
| 舊版長什麼樣 | 「以前是這樣寫的」 | `git show v6.1:path` |
| 巨集展開後是什麼 | 「這個巨集產生 Y」 | `make O=../build path/file.i`（[Ch 50](./50-compiler-as-reading-tool.md)） |
| struct layout | 「欄位 Z 在 offset 48」 | `pahole -C`（[Ch 50](./50-compiler-as-reading-tool.md)） |
| 某段程式碼有沒有被編進去 | 「這個 config 下會走這裡」 | `.config`、Makefile、`.o` 是否存在（[Ch 36](./36-kconfig-kbuild-ifdef.md)、[練習 G](./practice-g-ops-callback-finder.md)） |
| 間接呼叫的目標 | 「`f_op->x` 是 Y」 | ftrace function_graph、kprobe、gdb（[Ch 51](./51-dynamic-tracing.md)、[Ch 52](./52-qemu-gdb-kernel.md)） |
| 設計理由 | 「這樣寫是為了避免 race」 | commit message、lore 討論（[Ch 38](./38-mailing-list-archaeology.md)） |
| lock 規則 | 「這裡要持有 L」 | 原始碼註解、`lockdep_assert_held()`、`__must_hold` 標註（[Ch 30](./30-reading-locking-code.md)） |

最後一列有個實用的觀察：LLM 對「設計理由」的回答最難驗證，因為它常常編出一個**邏輯上說得通**的理由。說得通不等於是真的理由。真正的理由只在 commit message 和 mailing list 裡，例 5 的 dfaabf916b1c 就是範例：理由是「watch_queue 的檢查已經提前了」，這不是你從程式碼本身推得出來的。

## 提示詞寫法：讓它比較不容易騙你

原則是**把事實放進問題，把不確定逼出答案**。

### 1. 貼原始碼，附版本與行號

不要問「`pipe_write` 在做什麼」，要貼程式碼。用 `awk` 加行號，讓模型能引用：

```console
$ awk 'NR>=431 && NR<=465 {print NR": "$0}' fs/pipe.c
431: anon_pipe_write(struct kiocb *iocb, struct iov_iter *from)
432: {
433: 	struct file *filp = iocb->ki_filp;
434: 	struct pipe_inode_info *pipe = filp->private_data;
...
```

並在問題第一行寫明「以下是 Linux v6.18 的 `fs/pipe.c` 第 431–601 行」。版本號讓模型知道不該用記憶覆蓋你貼的內容；行號讓你能逐條核對它的回答。

### 2. 要求每個斷言附行號

「每個結論都標出依據的行號；如果某個結論不是來自我貼的程式碼，明確標成『來自一般知識』。」這一句把模型的回答分成兩堆：有行號的你可以秒驗，標「一般知識」的就是你該去查的。

### 3. 要求列出不確定之處與反證方法

「最後列出你不確定的三件事，以及我可以用什麼指令驗證每一件。」模型被要求產生「不確定清單」時，往往會把它原本會用肯定語氣帶過的東西列出來。這不保證完整，但能抓到不少。

### 4. 版本特定的問題，明說不要憑記憶

「`file_operations` 在 v6.18 有哪些欄位」這種問題，與其讓它回憶，不如貼 `include/linux/fs.h` 的那段 struct 定義，或者乾脆自己 `sed -n` 看。凡是答案在 tree 裡一個指令就查得到的問題，查比問快，也比問可靠。

### 一個完整的提示詞範本

```
以下是 Linux v6.18 的 fs/pipe.c 第 431–601 行（行號在每行開頭）。
<貼上 awk 輸出>

問題：這個函式在什麼條件下會讓呼叫者睡眠？睡之前和醒來之後各做了什麼？

要求：
1. 每個結論標出依據的行號。
2. 不是來自上面這段程式碼的內容（例如其他函式的行為），標成「推測，需驗證」。
3. 不要用你記憶中舊版 pipe.c 的內容；如果你發現這段程式碼和你記得的不同，指出差異。
4. 最後列出 3 個你最不確定的地方，以及我可以用什麼指令（rg / git / ftrace）驗證。
```

第 3 條特別有用：模型如果記得舊版有 `__pipe_lock`，被要求「指出差異」時，它會把版本落差攤開，而不是悄悄混用。

## 五道鴻溝 × LLM 能幫多少

| 鴻溝 | LLM 能幫多少 | 怎麼用 | 主要風險 |
|---|---|---|---|
| **慣用法** | 多 | 「`ERR_PTR`/`IS_ERR` 是什麼慣例？」「`do { } while (0)` 為什麼？」 | 低；慣用法穩定。少數 kernel 特有的新慣用法（`cleanup.h` 的 `__free()`、`guard()`）可能不在舊模型的記憶裡 |
| **間接** | 少 | 產生候選清單、提醒「可能還有 `.write` 優先」這類分派細節 | **高**：例 7。答案必須來自執行期證據或完整的分派邏輯 |
| **領域** | 多 | 「page cache 是什麼？」「為什麼 pipe 用 ring buffer？」 | 中：概念通常對，具體數字和實作細節要查（例如 pipe 預設幾個 buffer：`PIPE_DEF_BUFFERS 16`，查 `include/linux/pipe_fs_i.h:5`） |
| **脈絡** | 少到中 | 幫你讀懂一篇長的 commit message 或 mailing list thread（你貼進去） | **高**：憑空解釋「為什麼這樣寫」時會編出合理但不真的理由（例 5）。理由只信 git 與 lore |
| **目標** | 兩面刃 | 「讀這個子系統我該先問哪些問題？」是好用法 | 「幫我總結整個 fs/pipe.c」會讓你跳過帶問題讀的過程，得到一份你無法驗證的摘要，等於沒讀 |

## 對比與取捨

| 工具 | 回答速度 | 對當前版本的忠實度 | 能回答「為什麼」 | 適合 |
|---|---|---|---|---|
| LLM（憑記憶） | 秒 | 低到中 | 會回答，但可能是編的 | 概念、慣用法、產生假說 |
| LLM（貼原始碼） | 秒 | 中到高（取決於你貼了什麼） | 只能從程式碼推 | 摘要、逐段解釋 |
| rg / clangd | 秒 | 完全忠實 | 不能 | 存在性、定義、引用 |
| git / lore | 秒到分鐘 | 完全忠實 | 能，而且是真的理由 | 脈絡鴻溝 |
| ftrace / gdb | 分鐘 | 完全忠實（對那次執行） | 不能 | 間接鴻溝 |
| 問 maintainer | 天 | 高 | 能 | 前面都解決不了的 |

LLM 最大的價值是**把「我不知道該查什麼」變成「我知道該查什麼」**。查本身，交給忠實的工具。

## 踩雷集錦

1. **「它引用了函式名和行號，所以是對的」**：模型可以生成看起來像引用的東西。行號要自己 `sed -n` 對過，函式名要 `rg` 過。尤其當你**沒有**貼原始碼、它卻給了行號時，那個行號幾乎一定是編的。

2. **「我貼了整個檔案，它的摘要就涵蓋了全部」**：長輸入中段的內容容易被忽略。重要的函式分開問，或者先讓它列出「這個檔案裡有哪些函式」，對照 `ctags -x` 的輸出確認它沒漏。

3. **「它說的設計理由很有道理」**：有道理是最低標準，不是證據。例 5 的真實理由（watch_queue 檢查提前，所以 spinlock 多餘）你從程式碼本身推不出來；模型編的理由也許更有道理，但不是真的。

4. **把 LLM 的回答直接貼進筆記**：三個月後你不會記得哪句是驗證過的。筆記裡只寫有 `file:line` 或 commit hash 的結論，LLM 給的假說另外標「未驗證」。這是 [Ch 7](./07-reading-notes-and-diagrams.md) 筆記法的延伸。

5. **因為被騙過一次就完全不用**：反應過度同樣是損失。慣用法、語法、領域概念這幾類問題，LLM 的命中率高、驗證成本低，用它省下的時間是真的。問題從來不是「用不用」，而是「這個答案我打算怎麼驗證」。

## 進階：再往深一層

- **讓 LLM 自己跑工具**：能執行 `rg`、`git`、編譯、甚至 QEMU 的 agent 型工具，把「假說 → 驗證」的迴圈交給模型自己跑。這門課的章節就是這樣產生的：例 7 的錯誤是模型自己犯的，也是模型自己用 function_graph 抓到的。工具不會讓模型不犯錯，但會讓錯誤**有機會被證據推翻**。前提是你要求它「每個斷言附上驗證指令與輸出」，並且你抽查。
- **把 tree 變成上下文**：與其讓模型回憶，不如讓它檢索。用 [練習 G](./practice-g-ops-callback-finder.md) 這類工具或 `rg` 先把相關片段撈出來，再連同版本資訊一起餵給模型。檢索到的內容是當前版本，模型的記憶不是。
- **隱私與授權**：讀公司內部程式碼時，貼到外部 LLM 服務之前先確認政策。kernel 是 GPL 公開程式碼，沒有這個問題；你工作上的 codebase 可能有。
- **用 LLM 做 code review 的輔助**（[Ch 57](./57-reading-patches-and-review.md)）：讓它列出「這個 patch 可能的問題」當作 checklist 起點，而不是當作 review 結論。它特別容易漏掉需要跨檔案知識的問題（lock 順序、生命週期）。

## 動手練習

1. 挑 `fs/eventfd.c` 的 `eventfd_write`。**先不看原始碼**，寫下你（或你用的 LLM 憑記憶）認為的：函式簽名、它拿什麼 lock、它什麼時候會睡。再打開 v6.18 的原始碼逐條核對，每條標 ✓ 或 ✗ 並附行號。（這個練習的價值在於親手體驗「記憶 vs 原始碼」的落差。）
2. 用本章的提示詞範本，貼 `fs/pipe.c` 的 `anon_pipe_read`（含行號）問 LLM「什麼時候回傳 0、什麼時候回傳 -EAGAIN」。把回答裡每個行號 `sed -n` 驗證，統計正確率。
3. 找三個你覺得「應該存在」的 kernel 函式名（例如照命名慣例猜的），用例 6 的方法驗證它們是否存在、是否曾經存在。
4. 選一個 [Ch 37](./37-git-archaeology.md) 用過的 commit，請 LLM 憑記憶解釋「這個改動為什麼要做」，再和 commit message 原文比較。

## 本章重點整理

- LLM 是**假說產生器**，不是事實來源；只有附 `file:line` 或 commit hash 的結論能進筆記。
- 擅長：慣用法、語法、巨集意圖、領域概念、摘要你貼進去的程式碼、產生問題清單。
- 不擅長：版本特定細節（改名、刪除、欄位變更）、lock 規則、捏造合理的名字、間接呼叫的實際目標、編造設計理由。
- 錯誤有結構：舊版「票數」多、生成的是最可能的文字、有知識截止日、長上下文注意力不均。
- 提示詞：貼原始碼並標版本與行號、要求每條附行號、要求列出不確定之處與驗證指令、明說不要用記憶覆蓋貼上的內容。

## 自我檢核

- [ ] 不看筆記，說出 LLM 讀碼錯誤的四種結構性來源，各舉本章一個例子。
- [ ] 有人說「我讓 LLM 解釋了整個 `fs/pipe.c`，現在我懂 pipe 了」，你會問他哪三個問題來檢驗他到底懂了沒？
- [ ] 解釋為什麼例 7（`/dev/null`）連「先找 ops table 再挑欄位」這種看似有原始碼支持的推理都會錯，以及哪兩種工具能抓到這個錯。
- [ ] 不翻書，寫出一個驗證「某 commit hash 是否真的存在、在哪個版本合入」的指令序列。
- [ ] 想一個你工作中常讀的 codebase，列出五道鴻溝裡你最常卡的兩道，並說明在那兩道上你打算怎麼用（或不用）LLM。

## 延伸閱讀

### 論文

- **Lost in the Middle: How Language Models Use Long Contexts** — Nelson F. Liu 等，Transactions of the ACL（2024）
  - **核心貢獻**：在多文件問答與鍵值檢索任務上，模型對位於輸入開頭和結尾的資訊利用得最好，中段明顯較差。
  - **讀哪裡**：第 2 節（多文件 QA 實驗）的圖表就足以建立直覺；方法細節可略過。
  - **和本章的關聯**：踩雷第 2 條「貼了整個檔案就涵蓋全部」的實證基礎。注意論文測的是特定模型與任務，新模型的程度不同，但「別假設均勻注意」這個教訓仍然適用。
- **Large Language Models for Software Engineering: A Systematic Literature Review** — Xinyi Hou 等，ACM Transactions on Software Engineering and Methodology（2024）
  - **核心貢獻**：整理數百篇 LLM 用於軟體工程的研究，按任務（程式碼理解、摘要、生成、修復……）分類。
  - **讀哪裡**：「code summarization」與「code comprehension」相關的章節，看研究社群怎麼評估「LLM 解釋程式碼」的品質。
  - **前提知識**：不需要 ML 背景，是綜述性質。

### 官方文件

- **Linux kernel 社群對 AI 輔助產生 patch 的規範**
  - **現況**：我們在 v6.18 tree 用 `rg -n -i 'large language|LLM|AI-generated' Documentation/process/` 實測，**找不到**專門的文件，`Documentation/process/` 下也沒有對應檔名。相關討論與規範草案在 mailing list 上持續進行，可能在之後的版本才收錄。
  - **怎麼追**：到 lore.kernel.org 搜尋相關關鍵字，並在新版 tree 重跑上面的 `rg`。這部分演進很快，我們不給特定連結。
  - **和本章的關聯**：社群關心的核心問題和本章一樣——誰為 AI 產生內容的正確性負責、怎麼揭露、怎麼驗證。

### 本課內的工具章節

- **[Ch 37 git 考古](./37-git-archaeology.md)、[Ch 49 clangd](./49-clangd-lsp.md)、[Ch 51 ftrace](./51-dynamic-tracing.md)、[Ch 52 QEMU + gdb](./52-qemu-gdb-kernel.md)**
  - **為什麼列在這裡**：本章驗證流程的每一步都用到它們。LLM 讓你更快提出問題，這四章讓你能回答問題。

Part 8 的最後一步，是把「找出 ops table callback」這件事自動化：寫一個工具，把 Ch 28 的靜態方法變成一條指令。

→ [練習 G：寫一個 ops table callback 自動定位工具](./practice-g-ops-callback-finder.md)
