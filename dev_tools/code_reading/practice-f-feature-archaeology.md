# 練習 F — 考古一個功能，還原它的設計理由

> **目標**：把 Ch 35–40 的工具全部用上——原始碼樹地圖、Kconfig、git 考古、mailing list、文件、測試——替 Linux kernel 的**通知管道（notification pipe，`O_NOTIFICATION_PIPE` / watch_queue）**寫一份「設計理由報告」：它為什麼存在、為什麼長這樣、後來被修了什麼、現在還有誰在用。完成後，你驗證的是「拿到一個陌生功能，能在一天內講出它的來龍去脈，且每句話都有出處」的能力。
>
> **鴻溝**：本練習主要練**脈絡鴻溝**，同時用到**領域鴻溝**（pipe 的內部結構）與**目標鴻溝**（報告的每一節都是一個要回答的問題）。

> **環境**：Linux kernel v6.18（`~/code_reading/linux`，blobless clone，HEAD 在 v6.18）；WSL Ubuntu 22.04。**所有 `git log -S`／`-L`／`blame` 都限定路徑與版本範圍，並包 `timeout 300`**——blobless clone 第一次讀某個舊版本的檔案時要從網路下載 blob，不限範圍的 pickaxe 可能跑上十幾分鐘。

## 背景與動機

你在讀 `fs/pipe.c` 的 `anon_pipe_write()` 時，看到這段：

```c
	/*
	 * Reject writing to watch queue pipes before the point where we lock
	 * the pipe.
	 * Otherwise, lockdep would be unhappy if the caller already has another
	 * pipe locked.
	 * If we had to support locking a normal pipe and a notification pipe at
	 * the same time, we could set up lockdep annotations for that, but
	 * since we don't actually need that, it's simpler to just bail here.
	 */
	if (pipe_has_watch_queue(pipe))
		return -EXDEV;
```
（`fs/pipe.c:442-452`（v6.18），所在函式 `anon_pipe_write()` 從 431 行開始）

> **版本注意**：v6.18 裡匿名 pipe 的 `f_op` 是 `pipeanon_fops`，它的 `.read_iter`／`.write_iter` 是 `anon_pipe_read`／`anon_pipe_write`（`fs/pipe.c:1257-1260`）。這兩個函式在 v6.15 之前叫 `pipe_read`／`pipe_write`（改名發生在 `f017b0a4951f` "pipe: don't update {a,c,m}time for anonymous pipes" 這組變更中，v6.15-rc1）。**舊的 commit message 和文章會用舊名字**，考古時要記得兩個名字是同一個東西。

一連串問題冒出來：什麼是「watch queue pipe」？為什麼 pipe 會拒絕 `write()`？為什麼錯誤碼是 `-EXDEV`（跨裝置連結）這種看起來不相干的值？為什麼要特別提 lockdep？

這正是 kernel 讀碼的日常：**一段看似奇怪的程式碼，背後是一個功能、一串設計決策、和幾年的修補歷史**。這個練習要你把這段歷史完整挖出來。

選這個功能的理由：

- **歷史清楚**：一次性合入（v5.8），之後五年有二十多個帶 `Fixes:` tag 的修補，形成完整的 Fixes 鏈。
- **設計理由有公開記錄**：pull request 的 merge commit 裡有一大段「WHY」與「DESIGN DECISIONS」。
- **有「沒走成的路」**：原始 RFC 想支援的事件來源（mount、superblock、block、USB）最後只有 key/keyring 合入；mount 通知後來改由別的機制實現。
- **和你已經讀過的程式碼相連**：它直接改了 `fs/pipe.c`，[練習 E](./practice-e-trace-read-syscall.md) 和 final project 都會碰到。

## 任務規格

產出一份 Markdown 報告 `watch_queue_design_rationale.md`（放在你自己的筆記目錄，不要放進 kernel tree）。報告必須包含以下八節，每一節的每個事實主張都要附出處（commit hash、`檔案:行號（v6.18）`、或文件路徑）：

| 節 | 要回答的問題 | 主要工具（章節） |
|---|---|---|
| 1. 一句話定義 | 這個功能是什麼？給 user space 什麼介面？ | 文件、uapi 標頭（Ch 39） |
| 2. 引入 | 哪些 commit 引入它？誰寫的？進入哪個版本？merge commit 是哪個？ | `git log --diff-filter=A`、`git describe --contains`（Ch 37） |
| 3. 設計決策與被拒絕的替代方案 | 為什麼建在 pipe 上？為什麼用 `O_EXCL` 的值？為什麼禁止 splice？作者明說要避免什麼？ | commit message、merge message、cover letter（Ch 37–38） |
| 4. 程式碼中的落點 | 在 v6.18 中，這個功能改變了 pipe 的哪些行為？各在哪一行？ | `rg`、`git blame`（Ch 35、37） |
| 5. 後續修改時間線 | 合入後到 v6.18 之間，`kernel/watch_queue.c` 被改了幾次？哪些是 bug fix？ | `git log -- <path>` |
| 6. Fixes 鏈 | 哪些 commit 帶 `Fixes:` 指向引入 commit？其中有沒有二階修補（修補的修補）？ | `git log --grep='Fixes: <hash>'`（Ch 37） |
| 7. 文件、測試、組態 | 文件在哪？搬過家嗎？有 selftest 嗎？要什麼 Kconfig？預設開不開？ | `Documentation/`、`tools/testing/`、`init/Kconfig`（Ch 36、39、40） |
| 8. 現況與評價 | v6.18 中誰在用？原本計畫的使用者後來怎麼了？你對這個設計的評價？ | `rg`、`git log`（全部） |

**驗收標準**：

- 第 2 節必須有**完整 12 位以上的 commit hash** 與**版本號**（例如 `v5.8-rc1`）。
- 第 6 節至少列出 15 個帶 `Fixes:` 的 commit，並找出至少一個二階修補。
- 第 4 節至少指出 4 處 v6.18 程式碼，附 `檔案:行號`，並用 `git blame` 指出每處最後是被哪個 commit 改成現在的樣子。
- 第 3 節至少列出 4 個設計決策，每個都要有**作者原話**的出處。
- 所有 git 指令都要真的在你的 tree 上跑過；報告附上關鍵指令與輸出摘錄。
- **禁止**只憑 LLM 或部落格的說法寫任何一句事實；可以用它們找線索，但結論要回到 git 與原始碼驗證（[Ch 53](./53-llm-assisted-reading.md)）。

## 期望輸出範例

報告第 2 節的格式大致像這樣（hash 刻意遮掉，請自己找）：

```markdown
## 2. 引入

| commit | 作者 | 標題 | 進入版本 |
|---|---|---|---|
| 0858caa4XXXX | David Howells | uapi: General notification queue definitions | v5.8-rc1 |
| ...          | ...           | pipe: Add O_NOTIFICATION_PIPE                  | v5.8-rc1 |
| ...          | ...           | pipe: Add general notification queue support   | v5.8-rc1 |

merge commit：XXXXXXXXXXXX "Merge tag 'notifications-20200601' ..."（Linus Torvalds，2020-06-13）

查法：
$ timeout 300 git log --oneline --reverse <merge>^1..<merge>^2
```

一個邊界情況的期望輸出——在一個**沒開** `CONFIG_WATCH_QUEUE` 的 kernel 上建立通知管道：

```
輸入：pipe2(fds, O_NOTIFICATION_PIPE)，kernel 為 CONFIG_WATCH_QUEUE is not set
輸出：pipe2(O_NOTIFICATION_PIPE): Package not installed (errno=65)
```

你的報告第 7 節要解釋這個 errno 從哪裡來。

## 如果你卡住了

1. **找不到引入 commit？** 先找「這個功能最核心的檔案」第一次出現的 commit：`git log --diff-filter=A --oneline -- kernel/watch_queue.c`。新檔案的誕生通常就是功能的誕生。再用 `git describe --contains <hash>` 找版本、用 `git log --merges --ancestry-path <hash>..v5.8 --oneline | tail -1` 之類的方法找 merge commit（或直接看 `git log --oneline -- kernel/watch_queue.c` 裡最早的那個 Merge）。
2. **`git log -S` 跑不完？** blobless clone 上，`-S` 要讀每個歷史版本的檔案內容，會觸發大量下載。先用 `--grep`（只搜 commit message，不需要 blob）；真的要 `-S` 時，限定 `v5.7..v5.8 -- fs/pipe.c` 這種窄範圍，並包 `timeout 300`。
3. **Fixes 鏈只找到一層？** 二階修補是「Fixes: 指向某個 Fixes commit」。把第一層的 hash 收集起來，再對每個 hash 搜一次。比逐個 `git log --grep` 快的方法：一次把 `v5.8..v6.18` 所有 commit 的 `Fixes:` 行抽成一個文字檔，之後用 `grep` 查。
4. **merge message 太長讀不完？** 帶著第 3 節的問題去讀：搜尋「WHY」「DESIGN DECISIONS」「avoid」「disabled」「O_EXCL」這幾個字。[Ch 3](./03-question-driven-reading.md) 的提問驅動在這裡最省時間。
5. **`git blame` 指到一個 merge commit？** 這不是工具壞了：代表那一行是在**合併時解衝突**產生的，兩邊分支都沒有這一行的最終樣子。用 `git show --cc <merge> -- <file>` 看 combined diff。（這個現象在本練習裡真的會碰到。）

## 實作步驟建議

### Step 1：定位功能與它的介面（30 分鐘）

- 讀 uapi 標頭 `include/uapi/linux/watch_queue.h`，找 `O_NOTIFICATION_PIPE` 的定義和 `enum watch_notification_type`。
- 讀 `Documentation/core-api/watch_queue.rst` 的開頭兩節。
- 讀 `init/Kconfig` 裡 `config WATCH_QUEUE` 的說明。
- 產出：第 1 節的一句話定義，以及一份「它對 user space 承諾了什麼」的清單（flag、ioctl、訊息格式）。

### Step 2：找出引入的 commit 與 merge（30 分鐘）

- `kernel/watch_queue.c` 的誕生 commit、它所在的 series、merge commit。
- 用 `git show --stat` 看引入 commit 動了哪些檔案——這張清單就是第 4 節要去找的落點。
- 產出：第 2 節的表格。

### Step 3：讀設計理由（45 分鐘）

- 依序讀：`O_NOTIFICATION_PIPE` 那個 commit 的訊息、核心 commit 的訊息、merge commit 的訊息。
- 每讀到一個「為什麼」就記一條：決策是什麼、作者的理由、作者提到的替代方案。
- 如果想看更早的版本，到 lore.kernel.org 搜尋 David Howells 在 2019 年 10 月起發的 RFC 系列（主旨含「pipe: Keyrings, Block and USB notifications」）。
- 產出：第 3 節。

### Step 4：在 v6.18 中找落點，blame 每一處（45 分鐘）

- `rg -n 'watch_queue|O_NOTIFICATION_PIPE|PIPE_BUF_FLAG_WHOLE|pipe_has_watch_queue' fs/pipe.c include/linux/pipe_fs_i.h`
- 對每一處跑 `timeout 300 git blame -s -L <起>,<迄> v6.18 -- <file>`。
- 產出：第 4 節，一張「v6.18 行號 → 行為 → 最後修改的 commit」表。

### Step 5：時間線與 Fixes 鏈（60 分鐘）

- `git log --format='%h %ad %s' --date=short -- kernel/watch_queue.c`，標出哪些是 bug fix。
- 收集所有 `Fixes: <引入 commit>` 的 commit，加上它們進入的版本。
- 找二階修補。
- 挑三個你覺得最有意思的 fix，讀完整訊息，寫一句話說明它修了什麼類型的 bug（記憶體安全、並行、資源洩漏、計量錯誤……）。
- 產出：第 5、6 節。

### Step 6：文件、測試、現況（45 分鐘）

- 文件的歷史：`git log --oneline --follow -- Documentation/core-api/watch_queue.rst`。
- 測試：`rg -l 'O_NOTIFICATION_PIPE|watch_queue' tools/testing/ samples/`。
- 使用者：`rg -n 'post_watch_notification|add_watch_to_object' -g '*.c' -g '*.h' . | grep -v kernel/watch_queue.c`。
- 在你的 WSL 上實際呼叫一次 `pipe2(fds, O_NOTIFICATION_PIPE)`，記錄結果並解釋。
- 產出：第 7、8 節。

### Step 7：整合與自我審查

- 逐句檢查：每個事實主張旁邊都有出處嗎？
- 用本頁下方的「測試用例」表自我測驗。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。以下所有指令都在 v6.18 tree 上實際跑過，輸出為真實結果（長輸出有節錄並標註）。

<details>
<summary>點開參考報告</summary>

### 1. 一句話定義

通知管道是一種特殊的 pipe：用 `pipe2(fds, O_NOTIFICATION_PIPE)` 建立後，kernel 可以在**不持有 pipe mutex** 的情況下把固定格式的通知訊息塞進去，user space 用一般的 `read()`／`poll()` 讀出；watch（監視點）可以掛在 kernel 物件上（v6.18 中只有 key/keyring），物件變化時 kernel 就把通知送進所有監視它的管道。

user space 看得到的介面：

```c
#define O_NOTIFICATION_PIPE	O_EXCL	/* Parameter to pipe2() selecting notification pipe */
```
（`include/uapi/linux/watch_queue.h:9`（v6.18））

```c
enum watch_notification_type {
	WATCH_TYPE_META		= 0,	/* Special record */
	WATCH_TYPE_KEY_NOTIFY	= 1,	/* Key change event notification */
	WATCH_TYPE__NR		= 2
};
```
（`include/uapi/linux/watch_queue.h`（v6.18））

另外有 `IOC_WATCH_QUEUE_SET_SIZE`、`IOC_WATCH_QUEUE_SET_FILTER` 兩個 ioctl（`fs/pipe.c:643`、`649` 的 `pipe_ioctl()` 中轉給 `watch_queue_set_size()`／`watch_queue_set_filter()`）。

### 2. 引入

```console
$ timeout 300 git log --oneline --diff-filter=A -- kernel/watch_queue.c include/linux/watch_queue.h \
      include/uapi/linux/watch_queue.h samples/watch_queue/watch_test.c
f5b5a164f9a1 Add sample notification program
c73be61cede5 pipe: Add general notification queue support
0858caa419e6 uapi: General notification queue definitions
$ git describe --contains c73be61cede5
v5.8-rc1~22^2~8
$ git describe --contains 6c3297841472
v5.8-rc1~22
$ timeout 300 git log --oneline --reverse 6c3297841472^1..6c3297841472^2
0858caa419e6 uapi: General notification queue definitions
344fa64ef8f6 security: Add a hook for the point of notification insertion
b580b93664f9 pipe: Add O_NOTIFICATION_PIPE
c73be61cede5 pipe: Add general notification queue support
998f50407ffc security: Add hooks to rule on setting a watch
f7e47677e39a watch_queue: Add a key/keyring notification facility
f5b5a164f9a1 Add sample notification program
8cfba76383e9 pipe: Allow buffers to be marked read-whole-or-error for notifications
e7d553d69cf6 pipe: Add notification lossage handling
8c0637e950d6 keys: Make the KEY_NEED_* perms an enum rather than a mask
3e412ccc22e2 selinux: Implement the watch_key security hook
a8478a602913 smack: Implement the watch_key and post_notification hooks
```

| 角色 | commit（完整） | 作者 | 日期 | 版本 |
|---|---|---|---|---|
| 核心 | `c73be61cede5882f9605a852414db559c0ebedfd` "pipe: Add general notification queue support" | David Howells | 2020-01-14 | v5.8-rc1 |
| flag | `b580b93664f9` "pipe: Add O_NOTIFICATION_PIPE" | David Howells | — | v5.8-rc1 |
| 第一個使用者 | `f7e47677e39a` "watch_queue: Add a key/keyring notification facility" | David Howells | — | v5.8-rc1 |
| merge | `6c3297841472` "Merge tag 'notifications-20200601' ..." | Linus Torvalds | 2020-06-13 | v5.8-rc1 |

核心 commit 的檔案清單（第 4 節的尋寶圖）：

```console
$ timeout 300 git show --stat --format='%h %s' c73be61cede5 | tail -11
 Documentation/userspace-api/ioctl/ioctl-number.rst |   1 +
 Documentation/watch_queue.rst                      | 339 +++++++++++
 fs/pipe.c                                          | 206 ++++---
 fs/splice.c                                        |  12 +-
 include/linux/pipe_fs_i.h                          |  19 +-
 include/linux/watch_queue.h                        | 127 ++++
 include/uapi/linux/watch_queue.h                   |  20 +
 init/Kconfig                                       |  12 +
 kernel/Makefile                                    |   1 +
 kernel/watch_queue.c                               | 657 +++++++++++++++++++++
 10 files changed, 1318 insertions(+), 76 deletions(-)
```

注意：commit 的 author date 是 2020-01-14，merge 是 2020-06-13。**作者日期不等於合入日期**——series 在這五個月中被重寫過好幾版。

### 3. 設計決策與被拒絕的替代方案

所有引文出自 `b580b93664f9`、`c73be61cede5`、`6c3297841472` 的 commit message（`git log -1 --format=%B <hash>`）。

| # | 決策 | 作者的理由（原話） | 提到的替代方案 |
|---|---|---|---|
| D1 | 建在標準 pipe 上，而不是新的裝置或 syscall | merge message「DESIGN DECISIONS」第一條：「The notification queue is built on top of a standard pipe. Messages are effectively spliced in.」 | 早期設計有 `/dev/watch_queue` 裝置。證據：`8fe62e0c0e2e`（v5.12-rc1）的訊息「The merged API doesn't use a watch_queue device, but instead relies on pipes, so let the documentation reflect that.」 |
| D2 | `O_NOTIFICATION_PIPE` 借用 `O_EXCL` 的值 | `b580b93664f9`：「The flag is given the same value as O_EXCL as it seems unlikely that this flag will ever be applicable to pipes and I don't want to use up another O_* bit unnecessarily.」 | 同一段：「An alternative could be to add a pipe3() system call.」merge message 裡作者還在問「[?] Should this be done some other way?」 |
| D3 | 禁止 splice／vmsplice／tee／sendfile | `b580b93664f9`：「calling iov_iter_revert() on a pipe when a kernel notification message has been inserted into the middle of a multi-buffer splice will be messy.」 | — |
| D4 | kernel 插入訊息時不拿 `pipe->mutex`，只拿 wait queue 的 spinlock | `c73be61cede5`：「post_one_notification() cannot take pipe->mutex」；merge：「The kernel inserts messages with the wait queue spinlock held.」 | — |
| D5 | 暫不允許 user space `write()` | merge：「It should be possible to allow write() to insert data into the notification pipes too, but this is currently disabled as the kernel has to be able to insert messages into the pipe *without* holding pipe->mutex and the code to make this work needs careful auditing.」 | 「should be possible」——作者認為可行，只是還沒審過 |
| D6 | 空間不足時不睡，標記遺失、讀取時補一筆「loss」通知 | merge：「When recording a notification, the kernel will not sleep, but will rather mark a queue as having lost a message if there's insufficient space.」 | — |
| D7 | read() 不切割訊息 | `c73be61cede5`：「it will not split a notification across buffers - rather it will return a short read or EMSGSIZE.」實作在 `8cfba76383e9` 的 `PIPE_BUF_FLAG_WHOLE` | — |

作者明說要避免的（merge message「Things I want to avoid」）：

- 讓核心 VFS 依賴網路堆疊（「ie. usage of netlink」）——這解釋了為什麼不用 netlink。
- 把事件丟進 dmesg 再由 daemon 解析——「puts the responsibility for security into userspace and makes handling namespaces tricky」。
- 讓使用者看到不該看到的事件——所以有 LSM hooks（`344fa64ef8f6`、`998f50407ffc`）。

動機（merge message「WHY」）：GNOME 桌面要監控存在 keyring 裡的 kerberos ticket，keyring 無法非同步監控，只能輪詢，「not so good on a laptop」。

**D1 和 D5 的交互是理解本練習開頭那段程式碼的關鍵**：因為建在 pipe 上（D1），notification pipe 在 VFS 眼中就是一個 pipe，`write()` 會走到 pipe 的寫入函式；因為 kernel 插入不拿 mutex（D4），user space 寫入會和 kernel 插入打架，所以要禁止（D5）。至於為什麼回 `-EXDEV`，作者沒有寫理由；`-EXDEV` 已經在核心 commit 裡用來表示「不能這樣接」，見第 4 節。

### 4. 程式碼中的落點（v6.18）

```console
$ rg -n 'watch_queue|O_NOTIFICATION_PIPE|PIPE_BUF_FLAG_WHOLE|pipe_has_watch_queue' fs/pipe.c include/linux/pipe_fs_i.h
（節錄）
include/linux/pipe_fs_i.h:12:#define PIPE_BUF_FLAG_WHOLE	0x20	/* read() must return entire buffer or error */
include/linux/pipe_fs_i.h:170:static inline bool pipe_has_watch_queue(const struct pipe_inode_info *pipe)
fs/pipe.c:249:	if (pipe_has_watch_queue(pipe)) {
fs/pipe.c:328:				if (buf->flags & PIPE_BUF_FLAG_WHOLE) {
fs/pipe.c:451:	if (pipe_has_watch_queue(pipe))
fs/pipe.c:935:	if (flags & O_NOTIFICATION_PIPE) {
fs/pipe.c:987:	if (flags & ~(O_CLOEXEC | O_NONBLOCK | O_DIRECT | O_NOTIFICATION_PIPE))
fs/pipe.c:1423:	if (for_splice && pipe_has_watch_queue(pipe))
```

逐處 blame（`timeout 300 git blame -s -L ... v6.18 -- <file>`）：

| v6.18 位置 | 行為 | blame 結果 | 說明 |
|---|---|---|---|
| `fs/pipe.c:987` | `pipe2()` 接受 `O_NOTIFICATION_PIPE` | `c73be61cede58` | 引入時就是這樣 |
| `fs/pipe.c:935-936` | 建立 pipe 時呼叫 `watch_queue_init()` | 935：`c73be61cede58`；936：`8a018eb55e3ac` | 936 被「pipe: Fix memory leaks in create_pipe_files()」改過（Fixes 鏈成員，v5.9） |
| `fs/pipe.c:328-332` | `anon_pipe_read()`：帶 `PIPE_BUF_FLAG_WHOLE` 的 buffer 放不下就回 `-ENOBUFS` | `8cfba76383e90` | D7 的實作 |
| `fs/pipe.c:442-452` | `anon_pipe_write()`：在拿鎖**之前**就回 `-EXDEV` | `055ca83559912`（Jann Horn，2023-11-24） | 原本的檢查在拿鎖之後；見下 |
| `fs/pipe.c:1423-1424` | `get_pipe_info(..., for_splice)` 對通知管道回 NULL → splice 拒絕 | 1423：`b4bd6b4bac8ed`；1424：`c73be61cede58` | D3 的實作；1423 被「fs/pipe: move check to pipe_has_watch_queue()」（v6.7-rc1）重構成 helper |
| `include/linux/pipe_fs_i.h:12` | `PIPE_BUF_FLAG_WHOLE` 的值是 `0x20` | **`6c3297841472b`（merge commit）** | 見下方「blame 指到 merge」 |

`055ca8355991` 的 diff 顯示 `-EXDEV` 原本就存在，只是被往前移到拿鎖之前：

```console
$ timeout 300 git show 055ca8355991 -- fs/pipe.c | sed -n '/^@@/,$p' | head -30
@@ -446,6 +446,18 @@ pipe_write(struct kiocb *iocb, struct iov_iter *from)
 	bool was_empty = false;
 	bool wake_next_writer = false;
 
+	/*
+	 * Reject writing to watch queue pipes before the point where we lock
+	 ...
+	if (pipe_has_watch_queue(pipe))
+		return -EXDEV;
+
 	/* Null write succeeds. */
 	if (unlikely(total_len == 0))
 		return 0;
@@ -458,11 +470,6 @@ pipe_write(struct kiocb *iocb, struct iov_iter *from)
 		goto out;
 	}
 
-	if (pipe_has_watch_queue(pipe)) {
-		ret = -EXDEV;
-		goto out;
-	}
-
```

訊息說明了原因：splice 一個普通 pipe 到通知管道時，`get_pipe_info(..., true)` 失敗後會退回 `iter_file_splice_write()`，先鎖輸入 pipe、再透過 `vfs_iter_write()` 鎖輸出 pipe——lockdep 看到「持有一個 pipe lock 時又拿另一個」就報警。syzbot 回報（`Reported-and-tested-by: syzbot+011e4ea1da6692cf881c@...`）。注意 hunk header 裡寫的是 `pipe_write`——2023 年時它還叫這個名字，v6.15 才改成 `anon_pipe_write`。

**blame 指到 merge commit**：`PIPE_BUF_FLAG_WHOLE` 那一行的 blame 是 merge commit 本身，代表它是合併時解衝突寫出來的：

```console
$ timeout 300 git show --cc 6c3297841472 -- include/linux/pipe_fs_i.h | sed -n '/^diff/,$p' | head -16
diff --cc include/linux/pipe_fs_i.h
index 0c31b9461262,6626f511de6f..50afd0d0084c
--- a/include/linux/pipe_fs_i.h
+++ b/include/linux/pipe_fs_i.h
@@@ -8,7 -8,10 +8,11 @@@
  #define PIPE_BUF_FLAG_ATOMIC	0x02	/* was atomically mapped */
  #define PIPE_BUF_FLAG_GIFT	0x04	/* page is a gift */
  #define PIPE_BUF_FLAG_PACKET	0x08	/* read() as a packet */
 -#define PIPE_BUF_FLAG_WHOLE	0x10	/* read() must return entire buffer or error */
 +#define PIPE_BUF_FLAG_CAN_MERGE	0x10	/* can merge buffers */
++#define PIPE_BUF_FLAG_WHOLE	0x20	/* read() must return entire buffer or error */
+ #ifdef CONFIG_WATCH_QUEUE
 -#define PIPE_BUF_FLAG_LOSS	0x20	/* Message loss happened after this buffer */
++#define PIPE_BUF_FLAG_LOSS	0x40	/* Message loss happened after this buffer */
+ #endif
```

同一個 merge window 裡，Christoph Hellwig 的 `f6dd975583bd`（"pipe: merge anon_pipe_buf*_ops"）先佔用了 `0x10` 給 `PIPE_BUF_FLAG_CAN_MERGE`，Howells 分支裡的 `WHOLE`（`0x10`）與 `LOSS`（`0x20`）在合併時被往後挪成 `0x20` 與 `0x40`。查 `CAN_MERGE` 的來源：

```console
$ timeout 300 git log --format='%h %an %s' -S'PIPE_BUF_FLAG_CAN_MERGE' v5.7..v5.8 -- include/linux/pipe_fs_i.h
f6dd975583bd Christoph Hellwig pipe: merge anon_pipe_buf*_ops
```

### 5. 後續修改時間線

```console
$ timeout 300 git log --format='%h %ad %s' --date=short -- kernel/watch_queue.c
（35 筆，節錄非 merge 的重點）
f13abc1e8e1a 2025-02-27 watch_queue: fix pipe accounting mismatch
1bfc466b13cf 2023-12-21 watch_queue: fix kcalloc() arguments order
ca0776571d31 2023-09-20 kernel: watch_queue: copy user-array safely
943211c87427 2023-06-05 watch_queue: prevent dangling pipe pointer
03e1d60e177e 2023-03-07 watch_queue: fix IOC_WATCH_QUEUE_SET_SIZE alloc error paths
e64ab2dbd882 2022-07-28 watch_queue: Fix missing locking in add_watch_to_object()
e0339f036ef4 2022-07-28 watch_queue: Fix missing rcu annotation
353f7988dd84 2022-07-19 watchqueue: make sure to serialize 'wqueue->defunct' properly
b490207017ba 2022-03-28 watch_queue: Free the page array when watch_queue is dismantled
3d8dcf278b1e 2022-03-21 watch_queue: Actually free the watch
a635415a064e 2022-03-21 watch_queue: Fix NULL dereference in error cleanup
2ed147f015af 2022-03-11 watch_queue: Fix lack of barrier/sync/lock between post and read
...（2022-03-11 當天 kernel/watch_queue.c 有 8 筆變更，其中 7 筆帶 Fixes）
c993ee0f9f81 2022-03-11 watch_queue: Fix filter limit check
29e44f4535fa 2020-08-17 watch_queue: Limit the number of watches a user can hold
```

觀察：

- **2022 年 3 月是爆發點**：3 月 11 日一天內有 8 個帶 `Fixes: c73be61cede5` 的修補（含 `c993ee0f9f81`、`2ed147f015af`），全部進入 v5.17-rc8。其中 `2ed147f015af` 的訊息寫著 `Reported-by: Jann Horn`。
- **持續到 2025 年**：合入近五年後仍在修計量錯誤（`f13abc1e8e1a`，v6.15-rc1）。
- **不只 watch_queue.c**：`fs/pipe.c` 也有一系列因它而生的修補（`189b0ddc2451` "pipe: Fix missing lock in pipe_resize_ring()"、`055ca8355991`、`e95aada4cb93`）。

三個代表性的 fix：

| commit | 版本 | bug 類型 | 一句話 |
|---|---|---|---|
| `c993ee0f9f81` "Fix filter limit check" | v5.17-rc8 | 記憶體越界寫入 | `watch_queue_set_filter()` 第二處檢查誤用 `sizeof(...) * BITS_PER_LONG`，過大的 type 會造成 `__set_bit()` 與 `filters[]` 越界寫；修法是改用 `WATCH_TYPE__NR`（v6.18 的 `kernel/watch_queue.c:352`、`368`）。訊息附了 KASAN 的 slab-out-of-bounds 報告 |
| `2ed147f015af` "Fix lack of barrier/sync/lock between post and read" | v5.17-rc8 | 並行／memory ordering | `post_one_notification()` 拿 `rd_wait.lock`，`pipe_read()` 只拿 `pipe->mutex`，兩者沒有同步；修法是用 barrier 設定與讀取 `pipe->head`（[Ch 21](./21-read-once-and-barriers.md)） |
| `f13abc1e8e1a` "fix pipe accounting mismatch" | v6.15-rc1 | 資源計量 | `watch_queue_set_size()` 改了 `user->pipe_bufs` 卻沒更新 `pipe->nr_accounted`，釋放時扣錯數字可能 underflow，導致之後 `too_many_pipe_buffers_soft()` 誤判 `-EPERM` |

### 6. Fixes 鏈

一階（`Fixes: c73be61cede5`），只搜 commit message，在這台機器上約 12 秒：

```console
$ time (timeout 300 git log --oneline --grep='Fixes: c73be61cede5' | wc -l)
21
real	0m12.571s
```

| commit | 版本 | 標題 |
|---|---|---|
| `8a018eb55e3a` | v5.9 | pipe: Fix memory leaks in create_pipe_files() |
| `29e44f4535fa` | v5.9-rc2 | watch_queue: Limit the number of watches a user can hold |
| `c993ee0f9f81` | v5.17-rc8 | watch_queue: Fix filter limit check |
| `db8facfc9faf` | v5.17-rc8 | watch_queue, pipe: Free watchqueue state after clearing pipe ring |
| `96a4d8912b28` | v5.17-rc8 | watch_queue: Fix to always request a pow-of-2 pipe ring size |
| `c1853fbadcba` | v5.17-rc8 | watch_queue: Fix to release page in ->release() |
| `3b4c0371928c` | v5.17-rc8 | watch_queue: Fix the alloc bitmap size to reflect notes allocated |
| `7ea1a0124b6d` | v5.17-rc8 | watch_queue: Free the alloc bitmap when the watch_queue is torn down |
| `2ed147f015af` | v5.17-rc8 | watch_queue: Fix lack of barrier/sync/lock between post and read |
| `4edc0760412b` | v5.17-rc8 | watch_queue: Make comment about setting ->defunct more accurate |
| `a635415a064e` | v5.18-rc1 | watch_queue: Fix NULL dereference in error cleanup |
| `3d8dcf278b1e` | v5.18-rc1 | watch_queue: Actually free the watch |
| `b490207017ba` | v5.18-rc1 | watch_queue: Free the page array when watch_queue is dismantled |
| `f5461124d59b` | v5.19-rc1 | Documentation: move watch_queue to core-api |
| `189b0ddc2451` | v5.19-rc1 | pipe: Fix missing lock in pipe_resize_ring() |
| `e0339f036ef4` | v5.19 | watch_queue: Fix missing rcu annotation |
| `e64ab2dbd882` | v5.19 | watch_queue: Fix missing locking in add_watch_to_object() |
| `03e1d60e177e` | v6.3-rc2 | watch_queue: fix IOC_WATCH_QUEUE_SET_SIZE alloc error paths |
| `247c8d2f9837` | v6.5-rc1 | fs: pipe: reveal missing function protoypes |
| `055ca8355991` | v6.8-rc1 | fs/pipe: Fix lockdep false-positive in watchqueue pipe_write() |
| `e95aada4cb93` | v6.8-rc1 | pipe: wakeup wr_wait after setting max_usage |

（版本以 `git describe --contains <hash> | sed 's/[~^].*//'` 取得；`v5.19` 這種沒有 `-rc` 的代表在最後一個 rc 之後才合入。）

二階：先把 `v5.8..v6.18` 所有 commit 的 `Fixes:` 行抽成檔案，再查每個一階 commit 有沒有被別人 Fixes：

```console
$ timeout 300 git log --format='@@%h|%s%n%b' v5.8..v6.18 \
    | awk '/^@@/{c=substr($0,3); next} /^Fixes: /{print c "|" $2}' > fixes.txt
$ wc -l fixes.txt
78607 fixes.txt
$ grep -i '|c73be61cede5' fixes.txt | cut -d'|' -f1 > l1.txt
$ while read h; do grep "|${h:0:12}" fixes.txt; done < l1.txt
f13abc1e8e1a|watch_queue: fix pipe accounting mismatch|e95aada4cb93d
c02b872a7ca7|Documentation: update watch_queue.rst references|f5461124d59b
```

兩條二階鏈：

```
c73be61cede5 (v5.8-rc1) 引入
   ├── e95aada4cb93 (v6.8-rc1) pipe: wakeup wr_wait after setting max_usage
   │      └── f13abc1e8e1a (v6.15-rc1) watch_queue: fix pipe accounting mismatch
   └── f5461124d59b (v5.19-rc1) Documentation: move watch_queue to core-api
          └── c02b872a7ca7 Documentation: update watch_queue.rst references
```

第一條是程式碼的「修補引入新 bug」：`e95aada4cb93` 修了一個喚醒問題，卻讓計量出錯，兩年後才被修。第二條是文件搬家後忘了更新參照。

其他引入 commit 也有人 Fixes：`8fe62e0c0e2e`（v5.12-rc1，"watch_queue: Drop references to /dev/watch_queue"）Fixes `f7e47677e39a`——這就是第 3 節 D1「早期有 `/dev/watch_queue` 裝置」的證據。

注意 `fixes.txt` 的範圍是 `v5.8..v6.18`，**不含 v5.8 本身**。在 v5.8 的 merge window 裡就被修掉的問題（例如 `298ce0fd5030` "watch_queue: add gitignore for generated sample program"，Fixes `f5b5a164f9a1`）不會出現在這個檔案裡，要另外用 `--grep` 查。

### 7. 文件、測試、組態

**文件**：最初是 `Documentation/watch_queue.rst`（核心 commit 新增，339 行），v5.19-rc1 由 `f5461124d59b` 搬到 `Documentation/core-api/watch_queue.rst`，之後 `c02b872a7ca7` 修正搬家後的參照。

**組態**：

```
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
（`init/Kconfig:486-496`（v6.18））

`default n`。本課的 WSL kernel 與 `~/code_reading/build` 都沒開：

```console
$ zcat /proc/config.gz | grep WATCH_QUEUE
# CONFIG_WATCH_QUEUE is not set
$ grep WATCH_QUEUE ~/code_reading/build/.config
# CONFIG_WATCH_QUEUE is not set
```

說明文字提到「device notifications」，但 v6.18 的 `enum watch_notification_type` 只有 `WATCH_TYPE_KEY_NOTIFY`——這是 [Ch 39](./39-documentation-and-style.md) 說的「文件描述從未合入的功能」。原始 RFC 系列的主旨是「pipe: Keyrings, Block and USB notifications」，block 與 USB 通知最後沒有合入，Kconfig 說明卻留著。

**沒開 CONFIG 時的行為**（在 WSL 上實測）：

```c
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

/* include/uapi/linux/watch_queue.h:9 (v6.18) */
#ifndef O_NOTIFICATION_PIPE
#define O_NOTIFICATION_PIPE O_EXCL
#endif

int main(void)
{
	int fds[2];

	if (pipe2(fds, O_NOTIFICATION_PIPE) < 0) {
		printf("pipe2(O_NOTIFICATION_PIPE): %s (errno=%d)\n",
		       strerror(errno), errno);
		return 1;
	}
	printf("notification pipe created: fds %d %d\n", fds[0], fds[1]);
	return 0;
}
```

```console
$ gcc -Wall -o notipipe notipipe.c && ./notipipe; echo "exit=$?"
pipe2(O_NOTIFICATION_PIPE): Package not installed (errno=65)
exit=1
```

errno 65 是 `ENOPKG`，來自沒開 CONFIG 時的 stub：

```c
static inline int watch_queue_init(struct pipe_inode_info *pipe)
{
	return -ENOPKG;
}
```
（`include/linux/watch_queue.h:126-129`（v6.18））

`create_pipe_files()`（`fs/pipe.c:935-942`）看到 `O_NOTIFICATION_PIPE` 就呼叫它，失敗則釋放 pipe 並回傳錯誤。注意 `pipe2()` 的 flag 檢查（`fs/pipe.c:987`）**不論有沒有開 CONFIG 都接受** `O_NOTIFICATION_PIPE`，所以錯誤不是 `EINVAL`，而是更晚的 `ENOPKG`——這讓 user space 能區分「kernel 太舊不認得這個 flag」（`EINVAL`）和「kernel 認得但沒編進來」（`ENOPKG`）。

（第一次寫這支程式時如果 `#include <linux/watch_queue.h>`，會和 glibc 的 `<fcntl.h>` 衝突，出現 `redefinition of 'struct flock'` 等錯誤。這是 uapi 標頭與 libc 標頭混用的常見問題，所以上面直接自己定義常數。）

**測試**：`tools/testing/selftests/` 下沒有 watch_queue 的 selftest；`rg -l 'O_NOTIFICATION_PIPE|watch_queue' tools/testing/selftests` 找不到專門的測試。有的是 `samples/watch_queue/watch_test.c`（範例程式，不是自動化測試），以及 merge message 提到的外部測試：keyutils 的 pipe-watch 分支執行 `make test` 時會開一個監控程序核對所有 key 事件。[Ch 40](./40-tests-as-specification.md) 說過 kernel 的測試覆蓋率不均，這是一個例子：**一個 2022 年一天出了 8 個修補的功能，in-tree 沒有 selftest**。

### 8. 現況與評價

v6.18 中的使用者：

```console
$ rg -n 'post_watch_notification|add_watch_to_object' -g '*.c' -g '*.h' . | grep -v '^./kernel/watch_queue.c'
./include/linux/watch_queue.h:79:extern void __post_watch_notification(struct watch_list *,
./include/linux/watch_queue.h:86:extern int add_watch_to_object(struct watch *, struct watch_list *);
./include/linux/watch_queue.h:102:static inline void post_watch_notification(struct watch_list *wlist,
./include/linux/watch_queue.h:108:		__post_watch_notification(wlist, n, cred, id);
./security/keys/keyctl.c:1823:		ret = add_watch_to_object(watch, key->watchers);
./security/keys/internal.h:191:	post_watch_notification(key->watchers, &n.watch, current_cred(),
```

**只有 `security/keys/`**。merge message 列出的 `watch_mount()`、`watch_sb()` 從未合入（v6.18 中 `rg 'watch_mount|watch_sb'` 找不到）。mount 事件通知後來由另一個機制實現：fanotify 的 `FAN_MNT_ATTACH`／`FAN_MNT_DETACH`，Miklos Szeredi 的 `0f46d81f2bce`（"fanotify: notify on mount attach and detach"，v6.15-rc1），對應的 selftest 是 `tools/testing/selftests/filesystems/mount-notify/`。

評價（這一段是參考答案作者的觀點，你的可以不同，但要有依據）：

- **設計上的代價清楚可見**：為了讓 kernel 能在不睡、不拿 mutex 的 context 插入訊息（D4），pipe 這個原本單純的結構多了一條「旁路寫入」，所有 splice、write、resize 路徑都得特別處理。Fixes 鏈裡大量的並行、鎖、計量錯誤，幾乎都出在這條旁路和既有 pipe 路徑的交界處。
- **通用性沒有兌現**：設計目標是「general notification queue」，但五年後只有 key/keyring 一個使用者，`default n`，主流發行版的 kernel 未必有開（本課的兩個 kernel 都沒開）。mount 通知選擇了 fanotify。
- **作者自己的疑慮成真了一部分**：merge message 中的「[?] Should this be done some other way? ... should I add a pipe3() system call instead?」顯示 `O_EXCL` 重用在當時就有疑問。
- **可學的讀碼教訓**：本練習開頭那段 `-EXDEV` 程式碼，表面上是 pipe 的一個小檢查；它背後是一個功能的核心設計假設（D4、D5）加上一個 syzbot 找到的 lockdep 問題（`055ca8355991`）。**看起來奇怪的程式碼，幾乎都有一段可以考古的歷史。**

</details>

**解答說明**：參考報告示範了幾個值得注意的考古技巧——

1. **先 `--grep` 後 `-S`**：Fixes 鏈與大部分線索都靠搜 commit message 取得，只有確認 `CAN_MERGE` 來源時才用 `-S`，而且限定在 `v5.7..v5.8 -- include/linux/pipe_fs_i.h`。
2. **把全歷史的 `Fixes:` 抽成檔案**：一次 `git log` 換來之後任意次的 `grep`，找二階修補的成本從幾分鐘降到不到一秒。
3. **blame 指到 merge 時用 `git show --cc`**：這是很多人第一次碰到會以為工具壞掉的地方。
4. **每個「為什麼」都引原話**：設計理由最容易被轉述走樣，原文引用加 hash 讓讀者能自己核對。
5. **區分作者說的與你推論的**：第 8 節明確標示是評價，前七節只寫有出處的事實。

## 測試用例

用這張表自我檢查你的報告。每一題都應該能在你的報告裡找到答案與出處。

| 問題 | 預期答案要點 | 考的是 |
|---|---|---|
| `O_NOTIFICATION_PIPE` 的數值是什麼？為什麼？ | 等於 `O_EXCL`；作者不想多用一個 `O_*` bit，認為 `O_EXCL` 對 pipe 沒有意義；替代方案是 `pipe3()` | 讀 commit message |
| 為什麼通知管道禁止 splice？ | kernel 插入的訊息可能夾在 multi-buffer splice 中間，`iov_iter_revert()` 會很難處理 | 讀 commit message |
| v6.18 中對通知管道 `write()` 會得到什麼？從哪個版本起在拿鎖之前就拒絕？ | `-EXDEV`；`055ca8355991`，v6.8-rc1 | blame + describe |
| 在沒開 `CONFIG_WATCH_QUEUE` 的 kernel 上 `pipe2(O_NOTIFICATION_PIPE)` 會怎樣？ | 失敗，`ENOPKG`（65），來自 stub `watch_queue_init()` | 實測 + 讀標頭 |
| 越界寫入的 filter 檢查 bug 在哪個版本修掉？ | `c993ee0f9f81`，v5.17-rc8 | Fixes 鏈 |
| 找出一個「修補的修補」 | `e95aada4cb93` → `f13abc1e8e1a` | 二階 Fixes |
| `PIPE_BUF_FLAG_WHOLE` 為什麼是 `0x20` 而不是作者原本寫的 `0x10`？ | 合併時和 `PIPE_BUF_FLAG_CAN_MERGE`（`f6dd975583bd`）衝突，在 merge commit `6c3297841472` 中被挪位 | blame 指到 merge |
| v6.18 誰在用這個機制？ | 只有 `security/keys/` | rg |
| 原本計畫的 mount 通知後來怎麼了？ | 沒合入；v6.15 改由 fanotify `FAN_MNT_ATTACH` 實現（`0f46d81f2bce`） | 跨子系統考古 |
| 文件放在哪裡？ | 原為 `Documentation/watch_queue.rst`，v5.19-rc1 搬到 `Documentation/core-api/` | 文件歷史 |
| （非法輸入）有人說「watch_queue 支援 USB 裝置通知，Kconfig 說明裡寫了」 | 錯。`enum watch_notification_type` 只有 KEY_NOTIFY；Kconfig 說明是未合入 RFC 的殘留 | 文件 vs 程式碼 |

## 延伸挑戰（加分）

- **挑戰一：Dirty Pipe 的伏筆**。第 4 節發現 `PIPE_BUF_FLAG_CAN_MERGE` 是 v5.8 由 `f6dd975583bd` 引入的。2022 年 Max Kellermann 回報的 Dirty Pipe（CVE-2022-0847）修補是 `9d2231c5d74e` "lib/iov_iter: initialize "flags" in new pipe_buffer"（v5.17-rc6），它的 `Fixes:` 指向 `241699cd72a8`（"new iov_iter flavour: pipe-backed"）。用本練習的方法寫一份短報告：為什麼一個「忘記初始化 `flags`」的 bug 存在多年都無害，直到 `CAN_MERGE` 出現後才變成可利用的漏洞？（提示：讀 `f6dd975583bd` 的 diff，看 `CAN_MERGE` 讓 `pipe_write()` 做了什麼新的事。）想從攻擊者角度深入，接 [kernel_pwn 課程](../../security/kernel_pwn/README.md)。
- **挑戰二：真的跑一次通知管道**。在你自己的 kernel tree（不是本課共用的 `~/code_reading/linux`）裡開 `CONFIG_WATCH_QUEUE=y` 與 `CONFIG_KEY_NOTIFICATIONS=y`，在 QEMU 裡用 `samples/watch_queue/watch_test.c` 監看 keyring 事件。**本挑戰未在本課環境實測**（本課規定不另外建置 kernel）；預期行為是：建立通知管道、對 session keyring 設 watch 後，`keyctl` 新增 key 時 `watch_test` 會印出對應的 key 通知記錄。
- **挑戰三：對照另一個功能**。用同樣的八節格式考古 eventfd 的 `EFD_SEMAPHORE`：引入是 Davide Libenzi 的 `bcd0b235bf38`（v2.6.30-rc1），測試要到 `7c37857fc23a`（v6.9-rc1）才出現，fdinfo 顯示 flag 是 `33d8b5d7824c`（v6.5-rc1）。它的歷史短得多——比較兩份報告，思考「什麼樣的設計會留下長長的 Fixes 鏈」。
- **挑戰四：lore 上的原始討論**。到 lore.kernel.org 找 2019 年 10 月的 RFC「pipe: Keyrings, Block and USB notifications」系列，找出 block／USB 通知為什麼沒被合入的討論，補進你報告的第 3 節。

## 自我檢核

- [ ] 能不看報告，用三分鐘向別人說明「為什麼 v6.18 的 pipe 寫入函式一開頭要檢查 `pipe_has_watch_queue()`」，並說出至少兩個 commit hash 當證據。
- [ ] 能解釋為什麼在 blobless clone 上要先用 `--grep` 再用 `-S`，以及怎麼把二階 Fixes 搜尋的成本降到最低。
- [ ] 碰到 `git blame` 指向 merge commit 時，知道下一步該跑什麼。
- [ ] 能區分報告裡哪些是有出處的事實、哪些是你的評價。
- [ ] 能說出自己的報告和參考解答的差異，並解釋各自取捨（例如你多找到或漏掉了哪些修補）。

Part 6 結束。到這裡你能回答「這段程式碼為什麼長這樣」；接下來的 Part 7 把視角拉高，從單一函式與功能，走向整個子系統的架構——先從 kernel 最經典的分層設計 VFS 開始。

→ [Ch 41 分層與抽象：VFS 作為範例](./41-layering-and-vfs.md)
