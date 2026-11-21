# Ch 37 — git 考古：blame、log -L、pickaxe、Fixes tag

> **目標**：看到一行「為什麼要這樣寫」的程式碼，能用 git 在幾分鐘內找出引入它的 commit、讀懂 commit message 的論證、沿著 `Fixes:` 鏈追到最初的 bug、查出它進入哪個版本，並拿到通往 mailing list 討論的 `Link:`；同時懂得 blame、`log -L`、pickaxe 背後在做什麼，知道它們什麼時候會給你錯的答案。
> **鴻溝**：本章主要練**脈絡鴻溝**——這是五道鴻溝裡唯一一道「程式碼本身完全不包含答案」的，答案在歷史裡。

> **環境**：git 2.34（Ubuntu 22.04 內建），`~/code_reading/linux` 是 torvalds/linux 的 **blobless clone**（`remote.origin.partialclonefilter = blob:none`），HEAD 在 v6.18，共 1,398,773 個 commit。所有指令與輸出都是在這個 tree 上實際跑出來的，commit hash 都是真的。為了讓輸出乾淨，部分指令前面加了 `git -c gc.auto=0 -c maintenance.auto=false`（原因見「blobless clone 的效能」一節），下文為了易讀省略這個前綴。

## 為什麼需要這個？

[Ch 1](./01-why-you-cant-read-it.md) 定義過脈絡鴻溝：每個字都懂、邏輯也看懂了，可是不知道**為什麼要這樣寫**。看這段 `fs/pipe.c` 的結尾：

```c
/* fs/pipe.c:583（v6.18），anon_pipe_write() 的結尾 */
	/*
	 * If we do do a wakeup event, we do a 'sync' wakeup, because we
	 * want the reader to start processing things asap, rather than
	 * leave the data pending.
	 *
	 * This is particularly important for small writes, because of
	 * how (for example) the GNU make jobserver uses small writes to
	 * wake up pending jobs
	 *
	 * Epoll nonsensically wants a wakeup whether the pipe
	 * was already empty or not.
	 */
	if (was_empty || pipe->poll_usage)
		wake_up_interruptible_sync_poll(&pipe->rd_wait, EPOLLIN | EPOLLRDNORM);
	kill_fasync(&pipe->fasync_readers, SIGIO, POLL_IN);
```

再配上 `pipe_poll()` 裡這一行：

```c
/* fs/pipe.c:666（v6.18） */
	/* Epoll has some historical nasty semantics, this enables them */
	WRITE_ONCE(pipe->poll_usage, true);
```

讀到這裡，一個認真的讀者會有一串問題：

1. 只在 pipe 原本是空的時候喚醒 reader，邏輯上完全合理（不空代表 reader 已經知道有資料）。那 `|| pipe->poll_usage` 是在幹嘛？
2. 「Epoll nonsensically wants」——kernel 的註解在罵 epoll？
3. 為什麼 `kill_fasync()` 不在 `if` 裡面，每次都送 SIGIO？
4. `poll_usage` 只會被設成 `true`、從來不清掉，為什麼還要 `WRITE_ONCE`？

光讀 v6.18 的程式碼，**這四個問題一個都答不出來**。答案散落在 2019–2022 年間的五個 commit 和三條 mailing list 討論串裡。這一章用 git 把它們一個個挖出來；下一章（[Ch 38](./38-mailing-list-archaeology.md)）再去讀那些討論串。

Spinellis 寫《Code Reading》時（2003），版本控制還是 CVS 的天下，原書談到修訂歷史時主要是把它當成「看變更紀錄」的工具。git 和 kernel 社群的 commit message 文化把這件事推進了一大步：kernel 的 commit message 被要求解釋**為什麼**（`Documentation/process/submitting-patches.rst` 的「Describe your changes」一節），加上 `Fixes:`、`Link:`、`Reported-by:` 這些結構化的 trailer，整個 git 歷史等於一個可查詢的設計決策資料庫。

## 先建立直覺：程式碼是地層

把一個檔案想成一面地層剖面。每一行都是某次 commit 沉積下來的；後來的 commit 會把某些行沖掉、蓋上新的一層。

```
  時間 ↑
  2022-04  f485922d8fe4  ─ pipe->poll_usage = 1  →  WRITE_ONCE(pipe->poll_usage, true)
  2021-08  fe67f4dd8daa  ─ kill_fasync() 移出 if
  2021-08  3b844826b6c6  ─ 加入 poll_usage，「Epoll nonsensically」註解搬到結尾
  2021-07  3a34b13a88ca  ─ was_empty = true;（每次 write 都喚醒）
  2019-12  0ddad21d3e99  ─ exclusive wait
  2019-12  1b6b26ae7053  ─ 「只在 pipe 原本為空時喚醒」+ GNU make jobserver 註解
  ...
  2005-04  1da177e4c3f4  ─ Linux-2.6.12-rc2（git 歷史的起點，之前的歷史不在這個 repo）
```

考古工具各自回答不同問題：

| 你想問 | 工具 | 回答的形式 |
|---|---|---|
| 這一行**最後**是誰、哪個 commit 改的？ | `git blame` | 每一行一個 commit |
| 這一段（或這個函式）**所有**改過它的 commit？ | `git log -L` | commit 清單 + 每次的 diff |
| 某個字串什麼時候**出現或消失**？ | `git log -S`（pickaxe） | 改變該字串出現次數的 commit |
| 哪些 commit 的 diff **碰到**符合某個 regex 的行？ | `git log -G` | 新增或刪除行符合 regex 的 commit |
| 檔案改名前的歷史？ | `git log --follow` | 跨越 rename 的 commit 清單 |
| 這個 commit 修了什麼、誰修了它？ | `Fixes:` trailer + `git log --grep` | 一條因果鏈 |
| 它在哪個版本出現？ | `git describe --contains`、`git tag --contains` | tag 名稱 |
| 當時大家怎麼討論的？ | `Link:` trailer | lore.kernel.org 的 URL（Ch 38） |

blame 只看得到最上面一層。**考古的第一課是：blame 給你的往往不是你要找的那個 commit**，而是最後一個碰過那一行的 commit——可能只是改了縮排、改了變數名、加了 `WRITE_ONCE`。

## 底層機制：這些指令在做什麼

### git 只存快照，不存「行的歷史」

git 的 commit 指向一個 tree（目錄快照），tree 指向 blob（檔案內容）。**git 從來不記錄「第 595 行來自哪裡」**，也不記錄 rename。所有「行的歷史」都是指令在執行時，拿相鄰 commit 的兩個 blob 做 diff **即時算出來**的。這解釋了三件事：

1. blame 和 `log -L` 很貴：它們要沿著歷史一路往回，每一步都 diff 一次。
2. rename 偵測是猜的：`--follow` 靠「刪掉一個檔、新增一個內容相似（預設 50% 以上）的檔」來推測改名。
3. 在 blobless clone 上，每一次 diff 都可能需要先從網路抓 blob。

### blame 的演算法

從 HEAD 的檔案開始，所有行都標為「尚未歸屬」。對目前的 commit C 和它的 parent P 做 diff：

```
  C 的版本         P 的版本
  line A  ───────  line A       ← 在 P 裡也有：責任往 P 推，繼續往回追
  line B  ───────  line B
  line X  ╳                     ← 在 P 裡沒有：這一行「歸屬於 C」，停止追蹤
  line D  ───────  line D
```

重複直到所有行都有歸屬（或到達歷史起點）。三個選項改變了「在 P 裡也有」的判定方式：

- `-w`：比對時忽略空白差異。純縮排的改動不再搶走責任。
- `-M`：偵測**同一個檔案內**搬移的行。
- `-C`：偵測從**同一個 commit 修改過的其他檔案**搬過來的行；`-C -C` 擴大到建立該檔案的 commit 裡的所有檔案；`-C -C -C` 擴大到**任何 commit 的所有檔案**。每多一個 `-C`，要比對的 blob 數量就大幅增加。

### log -L 的演算法

`git log -L <start>,<end>:<file>` 先在 HEAD 定位那個行範圍，然後往回走：每遇到一個 commit，就用 diff 把行範圍「映射」到 parent 的對應位置。如果這個 commit 的 diff 和範圍有交集，就列出來並顯示那一塊 diff。`-L :funcname:file` 用 git 的函式偵測（和 `git diff` 的 hunk header 同一套，C 檔用預設規則就能認出大部分 kernel 函式）找出函式的起訖行，再做同樣的事。

它比 blame 好的地方：blame 只告訴你**最後一層**，`log -L` 把**每一層**都列出來。

### pickaxe：-S 與 -G 的差別

這是最多人搞混的地方，而且搞混了會漏掉 commit：

- `git log -S<string>`：列出「**該字串出現次數有改變**」的 commit。它不看 diff，看的是 parent 和 child 兩個版本裡這個字串各出現幾次。
- `git log -G<regex>`：列出「**diff 中新增或刪除的行**符合 regex」的 commit。

如果一個 commit 把 `pipe->poll_usage = 1;` 改成 `WRITE_ONCE(pipe->poll_usage, true);`，`poll_usage` 出現次數沒變（1 → 1），**`-S poll_usage` 不會列出它**；但刪除行和新增行都含 `poll_usage`，`-G poll_usage` 會。後面會實測這個差異。

## 實例：從那行 wakeup 一路挖到 2019 年

### 第一步：blame

```
$ git blame -L 590,598 fs/pipe.c
1b6b26ae7053e (Linus Torvalds 2019-12-07 12:14:28 -0800 590) 	 * wake up pending jobs
3b844826b6c6a (Linus Torvalds 2021-08-05 10:04:43 -0700 591) 	 *
3b844826b6c6a (Linus Torvalds 2021-08-05 10:04:43 -0700 592) 	 * Epoll nonsensically wants a wakeup whether the pipe
3b844826b6c6a (Linus Torvalds 2021-08-05 10:04:43 -0700 593) 	 * was already empty or not.
1b6b26ae7053e (Linus Torvalds 2019-12-07 12:14:28 -0800 594) 	 */
fe67f4dd8daa2 (Linus Torvalds 2021-08-24 10:39:25 -0700 595) 	if (was_empty || pipe->poll_usage)
0ddad21d3e99c (Linus Torvalds 2019-12-09 09:48:27 -0800 596) 		wake_up_interruptible_sync_poll(&pipe->rd_wait, EPOLLIN | EPOLLRDNORM);
fe67f4dd8daa2 (Linus Torvalds 2021-08-24 10:39:25 -0700 597) 	kill_fasync(&pipe->fasync_readers, SIGIO, POLL_IN);
0ddad21d3e99c (Linus Torvalds 2019-12-09 09:48:27 -0800 598) 	if (wake_next_writer)

$ git blame -L 665,668 fs/pipe.c
a11e1d432b51f (Linus Torvalds    2018-06-28 09:43:44 -0700 665) 
3b844826b6c6a (Linus Torvalds    2021-08-05 10:04:43 -0700 666) 	/* Epoll has some historical nasty semantics, this enables them */
f485922d8fe4e (Kuniyuki Iwashima 2022-04-29 14:38:01 -0700 667) 	WRITE_ONCE(pipe->poll_usage, true);
3b844826b6c6a (Linus Torvalds    2021-08-05 10:04:43 -0700 668) 
```

九行程式碼，四個不同的 commit。注意第 595 行：blame 說是 `fe67f4dd8daa`，但 `poll_usage` 這個概念不是它發明的——同一個 `if` 的註解（592–593 行）屬於 `3b844826b6c6`。這就是「blame 只看最上層」的典型樣子：`fe67f4dd8daa` 只是把 `if (...) {` 的大括號拿掉（因為把 `kill_fasync` 移出去了），就把整行的責任搶走了。

### 第二步：git show 讀 commit message

先看看起來最核心的 `3b844826b6c6`：

```
$ git show --stat 3b844826b6c6a
commit 3b844826b6c6affa80755254da322b017358a2f4
Author: Linus Torvalds <torvalds@linux-foundation.org>
Date:   Thu Aug 5 10:04:43 2021 -0700

    pipe: avoid unnecessary EPOLLET wakeups under normal loads

    I had forgotten just how sensitive hackbench is to extra pipe wakeups,
    and commit 3a34b13a88ca ("pipe: make pipe writes always wake up
    readers") ended up causing a quite noticeable regression on larger
    machines.

    Now, hackbench isn't necessarily a hugely meaningful benchmark, and it's
    not clear that this matters in real life all that much, but as Mel
    points out, it's used often enough when comparing kernels and so the
    performance regression shows up like a sore thumb.

    It's easy enough to fix at least for the common cases where pipes are
    used purely for data transfer, and you never have any exciting poll
    usage at all.  So set a special 'poll_usage' flag when there is polling
    activity, and make the ugly "EPOLLET has crazy legacy expectations"
    semantics explicit to only that case.

    I would love to limit it to just the broken EPOLLET case, but the pipe
    code can't see the difference between epoll and regular select/poll, so
    any non-read/write waiting will trigger the extra wakeup behavior.  That
    is sufficient for at least the hackbench case.

    Apart from making the odd extra wakeup cases more explicitly about
    EPOLLET, this also makes the extra wakeup be at the _end_ of the pipe
    write, not at the first write chunk.  ...

    [ For stable people: I'm putting a "Fixes" tag on this, but I leave it
      up to you to decide whether you actually want to backport it or not.
      It likely has no impact outside of synthetic benchmarks  - Linus ]

    Link: https://lore.kernel.org/lkml/20210802024945.GA8372@xsang-OptiPlex-9020/
    Fixes: 3a34b13a88ca ("pipe: make pipe writes always wake up readers")
    Reported-by: kernel test robot <oliver.sang@intel.com>
    Tested-by: Sandeep Patil <sspatil@android.com>
    Tested-by: Mel Gorman <mgorman@techsingularity.net>
    Signed-off-by: Linus Torvalds <torvalds@linux-foundation.org>

 fs/pipe.c                 | 15 +++++++++------
 include/linux/pipe_fs_i.h |  2 ++
 2 files changed, 11 insertions(+), 6 deletions(-)
```

問題 1 有了一半答案：`poll_usage` 是一個**效能妥協**。有人用 poll/select/epoll 等這個 pipe 時，才啟用「每次 write 都喚醒」的昂貴行為；純資料傳輸的 pipe 不受影響。而且作者明說了這個 flag 太粗——它分不出 epoll 和一般 poll。

可是它說「修正 `3a34b13a88ca` 造成的 regression」，那 `3a34b13a88ca` 又為什麼要「讓每次 write 都喚醒 reader」？順著 `Fixes:` 往下挖。

### 第三步：沿著 Fixes: 往回

```
$ git show 3a34b13a88ca
commit 3a34b13a88caeb2800ab44a4918f230041b37dd9
Author: Linus Torvalds <torvalds@linux-foundation.org>
Date:   Fri Jul 30 15:42:34 2021 -0700

    pipe: make pipe writes always wake up readers

    Since commit 1b6b26ae7053 ("pipe: fix and clarify pipe write wakeup
    logic") we have sanitized the pipe write logic, and would only try to
    wake up readers if they needed it.

    In particular, if the pipe already had data in it before the write,
    there was no point in trying to wake up a reader, since any existing
    readers must have been aware of the pre-existing data already.  Doing
    extraneous wakeups will only cause potential thundering herd problems.

    However, it turns out that some Android libraries have misused the EPOLL
    interface, and expected "edge triggered" be to "any new write will
    trigger it".  Even if there was no edge in sight.

    Quoting Sandeep Patil:
     "The commit 1b6b26ae7053 ('pipe: fix and clarify pipe write wakeup
      logic') changed pipe write logic to wakeup readers only if the pipe
      was empty at the time of write. However, there are libraries that
      relied upon the older behavior for notification scheme similar to
      what's described in [1]

      One such library 'realm-core'[2] is used by numerous Android
      applications. The library uses a similar notification mechanism as GNU
      Make but it never drains the pipe until it is full. When Android moved
      to v5.10 kernel, all applications using this library stopped working.
      ..."

    Our regression rule for the kernel is that if applications break from
    new behavior, it's a regression, even if it was because the application
    did something patently wrong.  ...

    So add the extraneous wakeup, to approximate the old behavior.
    ...
    Link: https://lore.kernel.org/lkml/20210729222635.2937453-1-sspatil@android.com/
    Reported-by: Sandeep Patil <sspatil@android.com>
    Cc: Michael Kerrisk <mtk.manpages@gmail.com>
    Signed-off-by: Linus Torvalds <torvalds@linux-foundation.org>

diff --git a/fs/pipe.c b/fs/pipe.c
...
 	/*
-	 * Only wake up if the pipe started out empty, since
-	 * otherwise there should be no readers waiting.
+	 * Epoll nonsensically wants a wakeup whether the pipe
+	 * was already empty or not.
 	 *
...
 	head = pipe->head;
-	was_empty = pipe_empty(head, pipe->tail);
+	was_empty = true;
```

問題 2 解了：「Epoll nonsensically」這句註解誕生於這個 commit。被罵的不是 epoll 本身，而是**某些程式對 EPOLLET（edge-triggered）的錯誤期待**：它們以為「每次有新資料寫入」就算一個 edge，即使 pipe 一直是可讀的。Android 的 realm-core 函式庫依賴這個錯誤期待，Android 換到 v5.10 kernel 後，用這個函式庫的 app 全壞了。

kernel 的規則是：**user space 依賴的舊行為，即使是錯的，改掉就是 regression**（`Documentation/admin-guide/reporting-regressions.rst` 寫成文的「no regressions」原則）。所以 kernel 屈服，把行為改回來。

這裡值得注意 diff 的手法：`was_empty = true;`——不是新增一個變數，而是讓「pipe 原本是空的嗎」這個變數**說謊**。這是最小改動，但也讓程式碼的語意變得難讀。三週後的 `3b844826b6c6` 把它改回 `was_empty = pipe_empty(...)`，改用獨立的 `poll_usage` 表達這個特例。

### 第四步：找到 bug 的源頭

`3a34b13a88ca` 說的「`1b6b26ae7053` 改變了行為」，是這整串故事的起點：

```
$ git log -1 --format='%h %ad %s' --date=short 1b6b26ae7053
1b6b26ae7053 2019-12-07 pipe: fix and clarify pipe write wakeup logic
$ git log -1 --format='%B' 1b6b26ae7053 | head -12
pipe: fix and clarify pipe write wakeup logic

The pipe rework ends up having been extra painful, partly becaused of
actual bugs with ordering and caching of the pipe state, but also
because of subtle performance issues.

In particular, the pipe rework caused the kernel build to inexplicably
slow down.

The reason turns out to be that the GNU make jobserver (which limits the
parallelism of the build) uses a pipe to implement a "token" system: a
parallel submake will read a character from the pipe to get the job
```

這也是第 590 行「GNU make jobserver」註解的出處（blame 把 590 行歸給它）。2019 年的 pipe 大改寫（v5.5）為了效能，把喚醒改成「只在需要時」。這個改動在邏輯上正確，卻打破了一個沒人知道有人依賴的行為。

### 第五步：誰又修了它？反向追 Fixes

`Fixes:` 是往回指的。要往前找「誰修了 X」，用 `--grep`：

```
$ git log --oneline --grep='Fixes: 3b844826b6c6'
f485922d8fe4 pipe: make poll_usage boolean and annotate its access
fe67f4dd8daa pipe: do FASYNC notifications for every pipe IO, not just state changes
```

`fe67f4dd8daa` 回答了問題 3：

```
$ git show --stat fe67f4dd8daa2
    pipe: do FASYNC notifications for every pipe IO, not just state changes

    It turns out that the SIGIO/FASYNC situation is almost exactly the same
    as the EPOLLET case was: user space really wants to be notified after
    every operation.
    ...
    But as with EPOLLET, we have at least one case (stress-ng) where the
    kernel sent SIGIO due to the pipe being marked for asynchronous
    notification, but the user space signal handler then didn't actually
    necessarily read it all before returning ...

    This is arguably a user space bug - and Colin King already fixed the
    stress-ng code in question - but the kernel regression rules are clear:
    it doesn't matter if kernel people think that user space did something
    silly and wrong.  What matters is that it used to work.
    ...
    It was then unwittingly fixed by commit 3a34b13a88ca ("pipe: make pipe
    writes always wake up readers") only to be broken again when by commit
    3b844826b6c6 ("pipe: avoid unnecessary EPOLLET wakeups under normal
    loads").
    ...
    Fix it for good (knock wood) by simply making the kill_fasync() case
    separate from the wakeup case.  FASYNC is quite rare, and we clearly
    shouldn't even try to use the "avoid unnecessary wakeups" logic for it.

    Link: https://lore.kernel.org/lkml/20210824151337.GC27667@xsang-OptiPlex-9020/
    Fixes: 3b844826b6c6 ("pipe: avoid unnecessary EPOLLET wakeups under normal loads")
```

`kill_fasync()` 被移出 `if`，是因為 SIGIO 也有人依賴「每次都送」的舊行為。

`f485922d8fe4` 回答問題 4：

```
$ git show --stat f485922d8fe4e
commit f485922d8fe4e44f6d52a5bb95a603b7c65554bb
Author: Kuniyuki Iwashima <kuniyu@google.com>
Date:   Fri Apr 29 14:38:01 2022 -0700

    pipe: make poll_usage boolean and annotate its access

    Patch series "Fix data-races around epoll reported by KCSAN."

    This series suppresses a false positive KCSAN's message and fixes a real
    data-race.

    This patch (of 2):

    pipe_poll() runs locklessly and assigns 1 to poll_usage.  Once poll_usage
    is set to 1, it never changes in other places.  However, concurrent writes
    of a value trigger KCSAN, so let's make KCSAN happy.

    BUG: KCSAN: data-race in pipe_poll / pipe_poll
    ...
    Link: https://lkml.kernel.org/r/20220322002653.33865-1-kuniyu@amazon.co.jp
    Link: https://lkml.kernel.org/r/20220322002653.33865-2-kuniyu@amazon.co.jp
    Fixes: 3b844826b6c6 ("pipe: avoid unnecessary EPOLLET wakeups under normal loads")
    Signed-off-by: Kuniyuki Iwashima <kuniyu@amazon.co.jp>
    ...
    Signed-off-by: Andrew Morton <akpm@linux-foundation.org>
```

`WRITE_ONCE` 不是為了正確性，而是為了讓 KCSAN（Kernel Concurrency Sanitizer，資料競爭偵測器）安靜：多個 CPU 同時寫同一個值，在 C 記憶體模型下仍算 data race（[Ch 21](./21-read-once-and-barriers.md) 講過 `READ_ONCE`／`WRITE_ONCE` 的語意）。commit message 自己說了這是 false positive。

這個 commit 還藏著兩個小知識：

- **Author 是 `kuniyu@google.com`，Signed-off-by 卻是 `kuniyu@amazon.co.jp`**。這是 `.mailmap` 在作用：作者後來換了公司，`.mailmap` 把舊信箱對應到新信箱，`git log` 預設顯示對應後的結果。驗證：
  ```
  $ git log -1 --format='%an <%ae> | mailmap: %aN <%aE>' f485922d8fe4e
  Kuniyuki Iwashima <kuniyu@amazon.co.jp> | mailmap: Kuniyuki Iwashima <kuniyu@google.com>
  ```
  考古時如果要對照 mailing list（信件上是當時的信箱），用 `%ae` 而不是 `%aE`。
- **它是經由 Andrew Morton 的 tree 進來的**（最後的 `Signed-off-by: Andrew Morton`），所以 commit message 開頭有 Andrew 的工具自動貼上的「Patch series "..."」和「This patch (of 2):」，`Link:` 用的是 `lkml.kernel.org/r/` 而不是 `lore.kernel.org`。不同子系統的 commit 長相不同，是 [Ch 35](./35-kernel-source-tree-map.md) 說的「子系統是社會性邊界」的又一個證據。

### 第六步：進入哪個版本

```
$ git describe --contains 3a34b13a88ca 3b844826b6c6a fe67f4dd8daa2 f485922d8fe4e
v5.14-rc4~7
v5.14-rc7~25
v5.14~25
v5.19-rc1~121^2~40
$ git log -1 --format='%h %s' f467a6a66419; git describe --contains 1b6b26ae7053 f467a6a66419
f467a6a66419 pipe: fix and clarify pipe read wakeup logic
v5.5-rc1~18
v5.5-rc1~17
```

`v5.14-rc4~7` 讀作「從 v5.14-rc4 往回走 7 個 first-parent commit」，也就是**第一個包含它的 tag 是 v5.14-rc4**。`v5.19-rc1~121^2~40` 裡的 `^2` 表示它是從某個 merge 的第二個 parent（一個子系統分支）進來的。哪個 merge？

```
$ git log --oneline --merges --ancestry-path f485922d8fe4e..v5.19 | tail -2
77fb622de139 Merge tag 'mm-hotfixes-stable-2022-05-27' of git://git.kernel.org/pub/scm/linux/kernel/git/akpm/mm
6f664045c868 Merge tag 'mm-nonmm-stable-2022-05-26' of git://git.kernel.org/pub/scm/linux/kernel/git/akpm/mm
```

最早的那個 merge（清單最後一行）是 Andrew 的 `mm-nonmm-stable` 分支——和上面 `Signed-off-by` 的推論一致。

`git tag --contains` 也能查，但有一個排序陷阱：

```
$ git tag --contains 3a34b13a88ca | sort -V | head -3
v5.14
v5.14-rc4
v5.14-rc5
$ git tag --contains 3a34b13a88ca --sort=creatordate | head -2
v5.14-rc4
v5.14-rc5
```

`sort -V` 把 `v5.14` 排在 `v5.14-rc4` 前面（版本排序不懂 `-rc` 是「之前」），會讓你誤以為它是在正式版才進去的。用 `--sort=creatordate` 或直接用 `git describe --contains`。

### 整理成時間線

| 日期 | commit | 版本 | 做了什麼 | 為什麼 |
|---|---|---|---|---|
| 2019-12 | `1b6b26ae7053` | v5.5 | write 只在 pipe 原本為空時喚醒 reader | pipe 改寫後 GNU make jobserver 變慢；同時減少多餘喚醒 |
| 2021-07 | `3a34b13a88ca` | v5.14-rc4 | `was_empty = true;`，每次 write 都喚醒 | Android realm-core 依賴錯誤的 EPOLLET 語意，升級 v5.10 後 app 全壞 |
| 2021-08 | `3b844826b6c6` | v5.14-rc7 | 新增 `poll_usage`，只對有 poll 的 pipe 多喚醒 | 上一個 commit 讓 hackbench 在大機器上退步 12.6% |
| 2021-08 | `fe67f4dd8daa` | v5.14 | `kill_fasync()` 移出 `if`，每次都送 SIGIO | stress-ng 依賴每次 write 都收到 SIGIO |
| 2022-04 | `f485922d8fe4` | v5.19-rc1 | `unsigned int` → `bool`，加 `WRITE_ONCE` | KCSAN 回報 data race（false positive） |

四個問題全部有了答案，而沒有任何一個答案寫在 v6.18 的程式碼裡。這就是脈絡鴻溝。

## log -L：一次看到所有地層

blame 一層一層挖太慢時，`log -L` 一次列出所有碰過某段程式碼的 commit。用 regex 定位行範圍：

```
$ git log --oneline -L '/Epoll nonsensically/,+6:fs/pipe.c' v6.18 | grep -E '^[0-9a-f]{7,} '
fe67f4dd8daa pipe: do FASYNC notifications for every pipe IO, not just state changes
3b844826b6c6 pipe: avoid unnecessary EPOLLET wakeups under normal loads
0ddad21d3e99 pipe: use exclusive waits when reading or writing
1b6b26ae7053 pipe: fix and clarify pipe write wakeup logic
3c0edea9b29f pipe: Remove sync on wake_ups
a9a08845e9ac vfs: do bulk POLL* -> EPOLL* replacement
28e58ee8ce1f Fix broken "pipe: use event aware wakeups" optimization
e462c448fdc8 pipe: use event aware wakeups
71e20f1873d4 sched: affine sync wakeups
923f4f23940d [PATCH] pipe.c/fifo.c code cleanups
9aeedfc4712e [PATCH] get rid of the PIPE_*() macros
1da177e4c3f4 Linux-2.6.12-rc2
```

（實際輸出每個 commit 後面都跟著那一塊的 diff，這裡用 grep 只留 commit 行。）一次看到 12 層，包括 blame 完全看不到的 `3c0edea9b29f`「Remove sync on wake_ups」（2019-11-01，`describe --contains` → `v5.5-rc1~32^2~2`）——2019 年那波 pipe 改寫的過程中，sync wakeup 先被拿掉，一個多月後 `1b6b26ae7053` 又因為 GNU make 變慢把它加回來。這就是 `1b6b26ae7053` 那句「we had clearly all forgotten the reasons and rules for it」的背景。

注意 `3a34b13a88ca` **不在**清單裡：它改的是當時 `pipe_write()`（v6.15 起改名為 `anon_pipe_write()`）前段的 `was_empty` 初始化，不在這 7 行範圍內。`log -L` 只追你指定的範圍，範圍選太窄就會漏。

用函式名當範圍更穩：

```
$ time git log --oneline -s -L :pipe_poll:fs/pipe.c | head -30
3d252160b8180 fs/pipe: Read pipe->{head,tail} atomically outside pipe->mutex
5a957bbac3ab9 pipe: use f_pipe
f485922d8fe4e pipe: make poll_usage boolean and annotate its access
3b844826b6c6a pipe: avoid unnecessary EPOLLET wakeups under normal loads
0ddad21d3e99c pipe: use exclusive waits when reading or writing
ad910e36da4ca pipe: fix poll/select race introduced by the pipe rework
8c7b8c34ae952 pipe: Remove assertion from pipe_poll()
6718b6f855a0b pipe: Allow pipes to have kernel-reserved slots
8cefc107ca54c pipe: Use head and tail pointers for the ring, not cursor and length
a11e1d432b51f Revert changes to convert to ->poll_mask() and aio IOCB_CMD_POLL
dd67081b36a1f pipe: convert to ->poll_mask
a9a08845e9acb vfs: do bulk POLL* -> EPOLL* replacement
35f3d14dbbc58 pipe: add support for shrinking and growing pipes
923f4f23940d2 [PATCH] pipe.c/fifo.c code cleanups
9aeedfc4712ed [PATCH] get rid of the PIPE_*() macros
5e5d7a2229261 [PATCH] pipe: remove redundant fifo_poll abstraction
1da177e4c3f41 Linux-2.6.12-rc2

real	0m4.088s
```

`pipe_poll()` 從 2005 年到 v6.18 的完整演化，17 個 commit。`-s` 抑制 diff 輸出；拿掉它就能看到每次的變化。`dd67081b36a1f`「convert to ->poll_mask」後面緊跟著 `a11e1d432b51f`「Revert ...」——一次失敗的 API 重構，考古時看到 revert 要特別留意，它常常藏著「為什麼不這樣設計」的答案。

## pickaxe：-S 漏掉了什麼

```
$ time git log --oneline -S poll_usage -- fs/pipe.c include/linux/pipe_fs_i.h
3b844826b6c6 pipe: avoid unnecessary EPOLLET wakeups under normal loads

real	2m53.286s

$ time git log --oneline -G 'poll_usage' -- fs/pipe.c include/linux/pipe_fs_i.h
f485922d8fe4 pipe: make poll_usage boolean and annotate its access
fe67f4dd8daa pipe: do FASYNC notifications for every pipe IO, not just state changes
3b844826b6c6 pipe: avoid unnecessary EPOLLET wakeups under normal loads

real	0m3.588s
```

`-S` 只找到引入 `poll_usage` 的那一個 commit——它回答的問題是「這個字串什麼時候誕生」，答得很準。但 `fe67f4dd8daa`（改了 `if (was_empty || pipe->poll_usage) {` 那行的大括號）和 `f485922d8fe4`（`= 1` 改 `WRITE_ONCE`）都沒有改變 `poll_usage` 的**出現次數**，所以 `-S` 看不到。`-G` 看的是 diff 行，三個都抓到。

選擇法則：

- 想知道「某個函式／常數／欄位**什麼時候被加入或移除**」→ `-S`。輸出少、準。
- 想知道「**哪些 commit 動過**用到某個識別字的程式碼」→ `-G`。
- 兩者都加 `-- <path>` 限縮範圍，否則要掃整個 repo 140 萬個 commit。

兩者速度差了 50 倍，**但這不是 `-S` 和 `-G` 本身的差別**：`-S` 先跑，它在 blobless clone 上逐一下載了這兩個檔案歷代版本的 blob；`-G` 後跑時 blob 已經在本機。下一節細講。

## blobless clone 的效能

我們的 clone 用了 `--filter=blob:none`：下載所有 commit 和 tree，但 **blob（檔案內容）按需下載**。這讓 clone 從數 GB 降到可接受的大小，代價是任何需要「舊版本檔案內容」的操作，第一次都要連網：

| 操作 | 需要舊 blob 嗎 | 在 blobless clone 上 |
|---|---|---|
| `git log --oneline -- path` | 不需要（只看 tree） | 快（`fs/smb/client/connect.c` 108 個 commit，1.5 秒） |
| `git log --grep` | 不需要（只看 commit message） | 快 |
| `git show <commit>`（含 diff） | 需要兩個版本 | 第一次稍慢 |
| `git blame`、`git log -L` | 需要每一代版本 | 第一次慢，之後快 |
| `git log -S`／`-G` | 需要每一代版本 | 第一次很慢（上面 2 分 53 秒） |
| `git blame -C -C -C` | 需要大量**其他檔案**的版本 | 非常慢，見下 |
| `get_maintainer.pl --git` | 需要 | Ch 35 實測 18 秒 |

第一次跑 `-S` 時，輸出夾雜了幾十行：

```
Auto packing the repository in background for optimum performance.
See "git help gc" for manual housekeeping.
```

每次按需下載都寫進一個新的小 packfile，小 pack 太多時 git 就觸發自動 gc。這不影響正確性，但會干擾輸出、和其他 git 程序搶資源。考古時可以在單一指令上加 `-c gc.auto=0 -c maintenance.auto=false` 暫時關掉（不改 repo 設定），事後再手動 `git gc`。

`-C -C -C` 是最極端的情況。對 `fs/pipe.c` 的 21 行跑 `git blame -s -L 1405,1425`：不加 `-C` 是 0.54 秒（blob 已快取）；加上 `-w -C -C -C` 之後，git 要拿這 21 行去比對每個相關 commit 裡的**所有檔案**，在 blobless clone 上變成逐一下載大量 blob，程序幾乎都在等網路（`ps` 顯示 CPU 使用率 0.3%），跑了 10 分 13 秒仍未結束（user time 只有 17 秒），我們中止了它。**`-C -C -C` 的實際輸出本節未取得**；它在完整 clone 上可以跑，但仍然比一般 blame 慢上許多。

實務建議：

1. 先用便宜的指令（`log --oneline -- path`、`log --grep`）縮小範圍，再對少數 commit 用 `show`。
2. `-C` 從一個開始加，需要時才加第二、第三個；並配合 `-L` 限縮行範圍。
3. 要長期大量考古一個子系統，可以預先 `git rev-list --objects HEAD -- fs/ | ...` 之類的方式批次抓 blob，或乾脆對那個子系統的路徑做一次完整 fetch。最簡單的做法是另外準備一個完整 clone。

## --follow：跨越改名

檔案改名後，`git log -- 新路徑` 只看得到改名之後的歷史：

```
$ git log --oneline -- fs/smb/client/connect.c | wc -l
108
$ git log --oneline --follow -- fs/smb/client/connect.c | wc -l
1032
$ git log --oneline --follow --diff-filter=R --name-status -- fs/smb/client/connect.c | head -8
38c8a9a52082 smb: move client and server files to common directory fs/smb
R100	fs/cifs/connect.c	fs/smb/client/connect.c
```

108 對 1,032：不加 `--follow`，九成的歷史消失了。`R100` 表示 100% 相似的改名，發生在 `38c8a9a52082`（`git describe --contains` → `v6.4-rc4~22^2~2`）。`--follow` 的限制：只能接**一個**檔案，而且它是用相似度猜的；如果改名時內容也大改，相似度低於門檻（預設 50%，可用 `-M<n>%` 調整）就斷了。`git blame` 預設就會跟著整檔改名，不需要額外選項。

## 用 trailer 做統計

kernel 的 trailer 是結構化資料，可以直接當資料庫查：

```
$ git log --format=%h -- fs/pipe.c | wc -l
279
$ git log --format=%h --grep='^Fixes:' -- fs/pipe.c | wc -l
25
$ git log --format=%h --grep='^Link:' -- fs/pipe.c | wc -l
54
```

`fs/pipe.c` 的 279 個 commit 裡，25 個自稱修正了先前的 commit，54 個附了 `Link:`。誰被修得最多？

```
$ git log --format='%h %(trailers:key=Fixes,valueonly,separator=%x2C)' -- fs/pipe.c | awk 'NF>1' | head -12
d810d4c27bf3 3d252160b818 ("fs/pipe: Read pipe->{head,tail} atomically outside pipe->mutex")Cc: Oleg Nesterov <oleg@redhat.com>
3d252160b818 8cefc107ca54 ("pipe: Use head and tail pointers for the ring, not cursor and length")
2a42754b3104 20bf82a898b6 ("mm: don't allow huge faults for files with pre content watches")
e95aada4cb93 c73be61cede5 ("pipe: Add general notification queue support")
055ca8355991 c73be61cede5 ("pipe: Add general notification queue support")
c04fe8e32f90 afed6271f5b0 ("pipe: set FMODE_NOWAIT on pipes")
189b0ddc2451 c73be61cede5 ("pipe: Add general notification queue support")
f485922d8fe4 3b844826b6c6 ("pipe: avoid unnecessary EPOLLET wakeups under normal loads")
2ed147f015af c73be61cede5 ("pipe: Add general notification queue support")
db8facfc9faf c73be61cede5 ("pipe: Add general notification queue support")
fe67f4dd8daa 3b844826b6c6 ("pipe: avoid unnecessary EPOLLET wakeups under normal loads")
3b844826b6c6 3a34b13a88ca ("pipe: make pipe writes always wake up readers")
```

（第一行的 `Cc:` 是 trailer 解析的雜訊：那個 commit 的 trailer 區塊格式不標準，git 把下一行也併進來了。trailer 是慣例，不是強制格式，統計時要容忍這種髒資料。）

`c73be61cede5`「pipe: Add general notification queue support」在這 12 行裡就被修了 5 次。它正是 [Ch 36](./36-kconfig-kbuild-ifdef.md) 裡那些 `#ifdef CONFIG_WATCH_QUEUE` 的來源。**被 `Fixes:` 指最多次的 commit，通常就是一個子系統裡最微妙、最容易出錯的設計**——這是一個很有效率的「該先讀哪裡」訊號。

## 對比與取捨

| 指令 | 回答的問題 | 優點 | 盲點 |
|---|---|---|---|
| `git blame` | 每行最後被誰改 | 直覺、快 | 只看最上層；會被縮排、改名、格式化搶走責任 |
| `git blame -w -M -C` | 同上，忽略空白與搬移 | 穿透重構 | `-C` 越多越慢，blobless clone 上尤其嚴重 |
| `git log -L` | 一段程式碼的完整演化 | 每一層都看到，含 diff | 範圍選錯就漏；要跨檔案搬移時會斷 |
| `git log -S` | 字串何時出現／消失 | 精準定位誕生點 | 看不到「改了但次數沒變」的 commit |
| `git log -G` | 哪些 diff 碰到某 regex | 抓得全 | 雜訊多 |
| `git log --follow` | 跨改名的檔案歷史 | 不漏改名前的歷史 | 只能一個檔案；相似度門檻 |
| `git log --grep` | commit message 搜尋 | 不需要 blob，極快；配合 trailer 很強 | 依賴作者寫得好 |
| `git describe --contains` | 進入哪個版本 | 一行答案 | 需要本機有 tag |

## 踩雷集錦

1. **「blame 指到的 commit 就是答案」**：常常不是。`fe67f4dd8daa` 搶走了 595 行的責任，但 `poll_usage` 的設計理由在 `3b844826b6c6`。blame 之後一定要問：「這個 commit 是**創造**了這行的意義，還是只是**碰到**它？」不確定就用 `git blame <commit>^ -- file` 從那個 commit 的前一版再 blame 一次，或改用 `log -L`。
2. **「-S 會找到所有相關 commit」**：`-S` 只找出現次數改變的 commit。實測漏了 3 個裡的 2 個。要「所有碰過的 commit」用 `-G`。
3. **「Author 日期就是它進主線的日期」**：`3b844826b6c6` 的 author date 是 2021-08-05，committer date 是 2021-08-18（`git log -1 --format='%ad | %cd'` 可驗證）。patch 寫好到被套用之間可能隔幾天到幾個月；「何時進入哪個版本」要用 `describe --contains`，不要看日期。
4. **「`Fixes:` 指向的就是 bug 的根源」**：`fe67f4dd8daa` 的作者自己寫了「the "Fixes" tag below is somewhat ad hoc, but it matches when the issue was noticed」——真正最早打破行為的是 `1b6b26ae7053`。`Fixes:` 是作者的判斷，讀 commit message 本文才知道它有多可靠。
5. **「沒有 `--follow` 也看得到完整歷史」**：`fs/smb/client/connect.c` 不加 `--follow` 只看得到 108／1,032 個 commit。看到一個子系統的歷史從某個日期突然開始，先懷疑改名。
6. **「git 歷史從 Linux 誕生開始」**：這個 repo 的歷史從 2005 年的 `1da177e4c3f4`（Linux-2.6.12-rc2）開始，那之前的歷史在另外的「history」repo 裡。blame 停在 `1da177e4c3f4` 代表「2005 年之前就存在」，不代表「Linus 在 2005 年寫的」。

## 進階：再往深一層

- **`git log --first-parent`**：只走主線，每個子系統的合併只看到一個 merge commit。想知道「v5.14 的 merge window 裡 VFS 送了什麼進來」，用 `git log --first-parent --merges v5.13..v5.14-rc1` 再 `git show <merge>`，merge commit 的訊息通常是維護者寫的該批變更摘要。
- **`git log -L` 跨檔案**：可以給多個 `-L`，例如同時追 `fs/pipe.c` 的 `pipe_poll` 和 `include/linux/pipe_fs_i.h` 的 `struct pipe_inode_info` 定義，看兩者如何一起演化。
- **`git bisect`**：考古是「從結果找原因」；bisect 是「從症狀找引入的 commit」。kernel test robot 找到 `3a34b13a88ca` 造成 hackbench 退步，用的就是自動化 bisect（[Ch 38](./38-mailing-list-archaeology.md) 會看到那封報告）。
- **`git range-diff`**：比較同一個 patch series 的兩個版本（v1 vs v2），讀 review 歷程時很有用；搭配 Ch 38 的 b4。
- **stable 分支的 backport**：`3b844826b6c6` 寫著「For stable people: ... I leave it up to you」。stable tree（`linux-5.10.y` 等）不在 torvalds/linux 裡；要查某個修正有沒有被 backport，要另外 fetch stable repo，然後在 commit message 裡找 `commit <upstream hash> upstream.` 這一行。

## 動手練習

1. 對 `fs/pipe.c` 的 `get_pipe_info()` 第 1423 行（`if (for_splice && pipe_has_watch_queue(pipe))`）跑 `git blame`。blame 會指到 `b4bd6b4bac8e`「fs/pipe: move check to pipe_has_watch_queue()」——這是「碰到」還是「創造」？用 `git log -S for_splice -- fs/pipe.c` 找出 `for_splice` 參數真正的誕生點，再讀那個 commit 解釋為什麼 watch queue pipe 不能被 splice。
2. 用 `git log -S` 找出 `pipe_buf_release` 這個函式名稱是哪個 commit 引入的，再用 `-G` 列出所有動過它的 commit，比較兩份清單。
3. 選 `fs/pipe.c` 裡任何一段你覺得奇怪的程式碼（候選：`pipe_resize_ring()` 裡的 `spin_lock_irq`、`anon_pipe_buf_try_steal()`），照本章六個步驟寫一張時間線表格。
4. 找出 `fs/eventfd.c` 被 `Fixes:` 指最多次的 commit，讀它的 commit message，猜猜它為什麼容易出錯。

## 本章重點整理

- git 只存快照，「行的歷史」都是指令即時 diff 算出來的；所以 blame／`log -L`／pickaxe 都很貴，rename 偵測是猜的，blobless clone 上第一次要連網。
- blame 只看最上層，常常指到「碰過」而不是「創造」這行意義的 commit；`log -L` 一次列出所有地層。
- `-S` 找字串出現次數改變的 commit（誕生與消失），`-G` 找 diff 行符合 regex 的 commit（所有碰過的）。實測 `-S` 漏掉三個裡的兩個。
- kernel 的 `Fixes:`、`Link:`、`Reported-by:` trailer 讓 git 歷史變成可查詢的因果鏈；`--grep='Fixes: <hash>'` 反向找出誰修了某個 commit。
- `fs/pipe.c` 那行 `if (was_empty || pipe->poll_usage)` 是五個 commit、兩年、三次 regression 修正疊出來的；它的每一個怪異之處都有理由，而理由全部在歷史裡。

## 自我檢核

- [ ] 不看筆記，能不能用自己的話解釋 `git log -S foo` 和 `git log -G foo` 的差別，並舉一個 `-S` 會漏掉的 commit 類型？
- [ ] 有人問你「`fs/pipe.c` 裡為什麼有個 `poll_usage` 欄位」，你能在不翻本章的情況下，講出 Android、hackbench、stress-ng 三段故事各扮演什麼角色嗎？
- [ ] blame 指到一個 commit，你怎麼判斷它是不是你要找的那個？說出至少兩種往下挖的方法。
- [ ] 在 blobless clone 上，哪些 git 操作是便宜的、哪些是昂貴的？為什麼 `-C -C -C` 特別慢？
- [ ] 想一個你自己專案裡「看起來很怪」的程式碼。用本章的流程，你預期第幾步會找到答案？如果那個專案的 commit message 都寫「fix bug」，哪一步會失效？

## 延伸閱讀

### 官方文件

- **`Documentation/process/submitting-patches.rst`**（[docs.kernel.org/process/submitting-patches.html](https://docs.kernel.org/process/submitting-patches.html)）
  - **讀哪裡**：「Describe your changes」一節（commit message 要寫什麼），以及談 `Fixes:` 與 `Link:` 的段落（`Link:` 要用 lore.kernel.org、以 Message-ID 組 URL）
  - **學到什麼**：從寫的人那一端理解為什麼 kernel 的 commit message 值得讀、trailer 的正式語意
  - **前提**：無
- **`Documentation/admin-guide/reporting-regressions.rst`** 與 **`Documentation/process/handling-regressions.rst`**
  - **讀哪裡**：開頭講「no regressions」規則的部分
  - **學到什麼**：本章實例裡 Linus 反覆引用的「user space 依賴的行為改了就是 regression」原則，正式寫在這裡；它解釋了 kernel 裡大量「為了相容而存在的怪程式碼」
  - **前提**：無
- **git 官方手冊 `git-blame(1)`、`git-log(1)`、`gitdiffcore(7)`**（[git-scm.com/docs](https://git-scm.com/docs)）
  - **讀哪裡**：`git-blame` 的 `-C` 與 `-M` 說明；`git-log` 的 `-L`、`-S`、`-G`、`--follow`；`gitdiffcore` 的「diffcore-pickaxe」一節
  - **學到什麼**：`gitdiffcore(7)` 是 pickaxe 行為的權威說明，本章 `-S`／`-G` 的差別就以它為準
  - **前提**：會用基本 git

### 書籍

- **《Pro Git》（2nd ed.）** — Scott Chacon、Ben Straub（Apress, 2014；[git-scm.com/book](https://git-scm.com/book) 免費線上版）
  - **讀哪幾章**：第 7.10 節「Debugging with Git」（blame、bisect）；第 10 章「Git Internals」（物件模型，解釋為什麼 git 沒有行歷史）
  - **這本書的定位**：本章「底層機制」一節的延伸；第 10 章讀完，blobless clone 的行為就很好理解

### 技術文章

- **LWN.net 上關於 kernel regression 政策與 `Fixes:` tag 的報導**
  - **讀哪裡**：在 [LWN Kernel Index](https://lwn.net/Kernel/Index/) 的 "Development model" 與 "Regressions" 分類
  - **為什麼值得讀**：`Fixes:` tag 如何被 stable 團隊和自動化工具使用，決定了它在 commit message 裡的份量
- **GitHub Blog 上關於 partial clone（`--filter=blob:none`）的介紹文章**（作者為 git 核心貢獻者 Derrick Stolee，2020）
  - **讀哪裡**：比較 blobless、treeless、shallow clone 的段落
  - **學到什麼**：本章 blobless clone 效能一節的背景；哪些 git 操作會觸發按需下載

git 告訴你**誰在什麼時候改了什麼、他自己怎麼解釋**；但 commit message 是事後整理過的結論。下一章順著 `Link:` 去 lore.kernel.org，讀那些沒進 commit 的部分：被拒絕的版本、reviewer 的反對、Linus 回信說「我把附件忘了」。

→ [Ch 38 mailing list 考古：lore.kernel.org 與 patch series](./38-mailing-list-archaeology.md)
