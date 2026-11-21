# Ch 40 — 把測試當規格讀：selftests 與 KUnit

> **目標**：會在讀一段 kernel 程式碼之前先找它的測試，從測試名稱、斷言與邊界值讀出 API 的預期行為；會實際編譯、執行 kselftest，判讀失敗是行為問題還是環境問題；會用 kselftest harness 寫自己的「規格探針」去驗證讀碼時的假說；知道 KUnit 測什麼、怎麼跑。
>
> **鴻溝**：本章主要練**目標鴻溝**（測試把模糊的「這函式做什麼」變成具體的輸入輸出問題）與**領域鴻溝**（測試用最小的例子告訴你一個 API 的語意），也碰到**脈絡鴻溝**（測試檔的 git 歷史記錄了行為何時改變）。

> **環境**：Linux kernel v6.18 原始碼；WSL2 kernel `6.18.58.1-microsoft-standard-WSL2` 與 QEMU 裡開機的 v6.18 自建 kernel（`~/code_reading/build`）；gcc 11.4；Lua 5.4.9。

## 為什麼需要這個？

[Ch 39](./39-documentation-and-style.md) 最後的結論是：文件與註解會過時，因為沒有東西會檢查它們。測試不一樣——測試會被執行，行為改了而測試沒改，CI 就會變紅。所以**測試是唯一一種「被迫跟上程式碼」的文件**。

它還有另一個優點：測試是**具體的**。文件說「eventfd 的計數器有上限」，測試說「寫入 `0xffffffffffffffff` 會得到 `EINVAL`」。前者讓你點頭，後者讓你能預測。

讀碼時的實際用法是：

```
  你的問題：「eventfd 開了 EFD_SEMAPHORE 之後 read 會回傳什麼？」
     │
     ├─ 讀 fs/eventfd.c ──▶ 要先懂 wait queue、spinlock、iov_iter ... （慢）
     │
     └─ 讀 eventfd_test.c ─▶ TEST(eventfd_check_read_with_semaphore)
                              寫 100000 次 1，讀 100000 次，每次都是 1，
                              第 100001 次在 NONBLOCK 下得到 EAGAIN     （快）
```

讀完測試，你帶著一個**已知答案**去讀實作，任務從「理解這段程式碼」變成「找到產生這個行為的那一行」。這就是 [Ch 3](./03-question-driven-reading.md) 提問驅動閱讀的最佳實踐：測試替你把問題問好了。

但測試也有它的歷史問題：**測試常常比功能晚很多才出現**。`EFD_SEMAPHORE` 在 2009 年由 Davide Libenzi 的 `bcd0b235bf38` 加入（v2.6.30），而 eventfd 的 selftest 要到 2024 年 Wen Yang 的 `7c37857fc23a` 才出現（v6.9）：

```console
$ git log -1 --format='%h %ad %an %s' --date=short 7c37857fc23a
7c37857fc23a 2024-01-08 Wen Yang selftests: add eventfd selftests
$ git describe --contains 7c37857fc23a
v6.9-rc1~106^2~73
$ git describe --contains bcd0b235bf38
v2.6.30-rc1~522
```

十五年間，這個 flag 的「規格」只存在於 commit message 和 man page。kernel 的測試覆蓋率遠不如一般 user-space 專案，這點要先有心理準備：**有測試時它是最好的規格；沒測試時，你可以自己寫一個**（本章後半）。

## 先建立直覺

把一個 API 想成一份合約。文件是合約條文，測試是**附在合約後面的驗收程序**：「交貨時，甲方會做以下 9 個檢查，每一項都通過才算履約。」

```
         ┌──────────────── API 合約 ────────────────┐
         │ 條文（文件、man page、kernel-doc）        │  ← 人寫給人看，可能過時
         │                                           │
         │ 驗收程序（測試）                           │  ← 機器執行，必須跟上
         │   1. 用 flag X 建立 → fcntl 看到 O_RDWR    │
         │   2. 寫 4 bytes → EINVAL                   │
         │   3. 寫 UINT64_MAX → EINVAL                │
         │   ...                                      │
         └───────────────────────────────────────────┘
                  ▲                         ▲
            正常路徑                    邊界與錯誤路徑
         （文件通常會寫）            （文件常常沒寫，測試會寫）
```

這個比喻的限制要說清楚：驗收程序只檢查它列出來的項目。**測試通過不代表合約沒有其他條款**，只代表列出來的條款成立。本章會實際找到一個 eventfd selftest 沒涵蓋、但程式碼裡明確存在的行為。

## 兩種測試，兩種讀法

kernel 主要有兩套測試框架。`Documentation/dev-tools/testing-overview.rst` 對它們的區分是：KUnit 是「entirely in-kernel system for "white box" testing」，kselftest 主要從 user space 測 kernel 對外的行為。

| | kselftest | KUnit |
|---|---|---|
| 位置 | `tools/testing/selftests/<子系統>/` | 散布在原始碼旁，常見 `lib/tests/*_kunit.c`、`*_test.c`、`*-test.c` |
| 規模（v6.18） | `tools/testing/selftests/` 下 129 個項目 | 222 個 `.c` 檔含 `kunit_test_suite` |
| 執行在哪 | user space，一般的程式（C、shell、Python） | kernel 裡，開機或載入 module 時執行 |
| 測什麼 | syscall、ioctl、/proc、/sys 的**外部行為** | kernel 內部函式、資料結構的**內部行為** |
| 讀碼時回答 | 「user space 看到的語意是什麼」 | 「這個內部 API 的邊界情況是什麼」 |
| 輸出格式 | TAP（Test Anything Protocol） | KTAP（kernel 版 TAP） |
| 對應章節 | 本章實例一、二 | 本章 KUnit 一節 |

（129 是 `ls tools/testing/selftests | wc -l`，包含幾個非目錄的輔助檔；222 是 `rg -l 'kunit_test_suite' -g '*.c' . | wc -l`。）

找測試的方法：

```console
$ ls tools/testing/selftests/filesystems/
Makefile  anon_inode_test.c  binderfs  devpts_pts.c  dnotify_test.c  epoll  eventfd  fat
fclog.c  file_stressor.c  fuse  kernfs_test.c  mount-notify  nsfs  overlayfs  statmount
utils.c  utils.h  wrappers.h
$ rg -l 'pipe2?\(|eventfd' tools/testing/selftests --type c | head -5
tools/testing/selftests/vfio/vfio_pci_device_test.c
tools/testing/selftests/pidfd/pidfd_wait.c
tools/testing/selftests/vfio/lib/include/vfio_util.h
tools/testing/selftests/pidfd/pidfd_test.c
tools/testing/selftests/vfio/lib/vfio_pci_device.c
```

第二個指令告訴你一件重要的事：**用 API 名稱 grep 測試，會找到大量「順便用到」它的測試**（vfio 用 eventfd 當通知機制，不是在測 eventfd）。先看目錄結構找專門的測試，再用 grep 補漏。

## 實例一：把 eventfd selftest 當規格讀

`tools/testing/selftests/filesystems/eventfd/eventfd_test.c` 共 311 行。先別讀內容，先看**測試名稱清單**——這就是規格的目錄：

```console
$ grep -n '^TEST' eventfd_test.c
45:TEST(eventfd_check_flag_rdwr)
59:TEST(eventfd_check_flag_cloexec)
73:TEST(eventfd_check_flag_nonblock)
88:TEST(eventfd_check_flag_cloexec_and_nonblock)
166:TEST(eventfd_check_flag_semaphore)
192:TEST(eventfd_check_write)
220:TEST(eventfd_check_read)
248:TEST(eventfd_check_read_with_nonsemaphore)
282:TEST(eventfd_check_read_with_semaphore)
311:TEST_HARNESS_MAIN
```

九個測試，分三組：flag 行為（前五個）、write 的錯誤條件、read 的兩種模式。只看名字就知道 eventfd 的「規格面積」：四個 flag、一個 write 語意、兩個 read 語意。

再看其中一個測試的完整內容。注意它的註解是**直接從 man page 抄來的規格句子**：

```c
/*
 * If EFD_SEMAPHORE was specified and the eventfd counter has a nonzero value,
 * then a read(2) returns 8 bytes containing the value 1, and the counter's
 * value is decremented by 1.
 * If the eventfd counter is zero at the time of the call to read(2), then the
 * call fails with the error EAGAIN if the file descriptor has been made nonblocking.
 */
TEST(eventfd_check_read_with_semaphore)
{
	uint64_t value;
	ssize_t size;
	int fd;
	int i;

	fd = sys_eventfd2(0, EFD_SEMAPHORE|EFD_NONBLOCK);
	ASSERT_GE(fd, 0);

	value = 1;
	for (i = 0; i < EVENTFD_TEST_ITERATIONS; i++) {
		size = write(fd, &value, sizeof(value));
		EXPECT_EQ(size, sizeof(value));
	}

	for (i = 0; i < EVENTFD_TEST_ITERATIONS; i++) {
		size = read(fd, &value, sizeof(value));
		EXPECT_EQ(size, sizeof(value));
		EXPECT_EQ(value, 1);
	}

	size = read(fd, &value, sizeof(value));
	EXPECT_EQ(size, -1);
	EXPECT_EQ(errno, EAGAIN);

	close(fd);
}
```
（`tools/testing/selftests/filesystems/eventfd/eventfd_test.c:274-309`（v6.18）；`EVENTFD_TEST_ITERATIONS` 在第 16 行定義為 `100000UL`）

註解是條文，程式碼是驗收程序。現在帶著「semaphore 模式每次 read 只拿 1」這個已知答案去 `fs/eventfd.c` 找實作，一行就找到：

```c
void eventfd_ctx_do_read(struct eventfd_ctx *ctx, __u64 *cnt)
{
	lockdep_assert_held(&ctx->wqh.lock);

	*cnt = ((ctx->flags & EFD_SEMAPHORE) && ctx->count) ? 1 : ctx->count;
	ctx->count -= *cnt;
}
```
（`fs/eventfd.c:175-181`（v6.18））

同樣地，`eventfd_check_write` 的註解說「寫入 `0xffffffffffffffff` 會失敗並回傳 `EINVAL`」，對應到：

```c
	if (count != sizeof(ucnt))
		return -EINVAL;
	if (copy_from_user(&ucnt, buf, sizeof(ucnt)))
		return -EFAULT;
	if (ucnt == ULLONG_MAX)
		return -EINVAL;
```
（`fs/eventfd.c:254-259`（v6.18））

**測試 → 實作**的方向比**實作 → 理解**快得多。你不需要先搞懂 `wait_event_interruptible_locked_irq()` 就能確認這兩個規格。

### 讀懂 harness 的兩種斷言

kselftest harness（`tools/testing/selftests/kselftest_harness.h`）有兩類斷言，讀測試時要分清楚，因為它們表達的規格強度不同：

```
 * ASSERT_* calls will stop test execution immediately.
 * EXPECT_* calls will emit a failure warning, note it, and continue.
```
（`kselftest_harness.h:500-501`（v6.18））

- `ASSERT_GE(fd, 0)`：前提條件。不成立的話後面都沒意義，所以直接中止。
- `EXPECT_EQ(value, 1)`：要驗證的規格本身。不成立就記錄，繼續檢查其他條款。

讀測試時，**`ASSERT` 是「假設」，`EXPECT` 是「主張」**。

### 實際跑起來

selftest 多數是一般的 user-space 程式，不需要 root、不需要整套 `make kselftest`。直接編譯（輸出放 scratch 目錄，不弄髒 tree）：

```console
$ cd ~/code_reading/scratch/40
$ S=~/code_reading/linux/tools/testing/selftests
$ gcc -Wall -O2 -o eventfd_test $S/filesystems/eventfd/eventfd_test.c -lpthread
$ ./eventfd_test
TAP version 13
1..9
# Starting 9 tests from 1 test cases.
#  RUN           global.eventfd_check_flag_rdwr ...
#            OK  global.eventfd_check_flag_rdwr
ok 1 global.eventfd_check_flag_rdwr
#  RUN           global.eventfd_check_flag_cloexec ...
#            OK  global.eventfd_check_flag_cloexec
ok 2 global.eventfd_check_flag_cloexec
...（中略，3–8 都是 OK）
#  RUN           global.eventfd_check_read_with_semaphore ...
#            OK  global.eventfd_check_read_with_semaphore
ok 9 global.eventfd_check_read_with_semaphore
# PASSED: 9 / 9 tests passed.
# Totals: pass:9 fail:0 xfail:0 xpass:0 skip:0 error:0
```

這是在 WSL2 的 kernel 上跑的（`uname -r` 是 `6.18.58.1-microsoft-standard-WSL2`）。WSL 的 kernel 是微軟的分支，不完全等於 v6.18 原版。要在確切的 v6.18 上驗證，把它靜態連結後丟進 QEMU 裡用自建 kernel 開機執行：

```console
$ gcc -static -O2 -o eventfd_test_static $S/filesystems/eventfd/eventfd_test.c -lpthread
$ cat guest.sh
#!/bin/sh
uname -r
eventfd_test_static | tail -3
$ ~/code_reading/qemu/run-script.sh guest.sh eventfd_test_static
...
6.18.0
ok 9 global.eventfd_check_read_with_semaphore
# PASSED: 9 / 9 tests passed.
# Totals: pass:9 fail:0 xfail:0 xpass:0 skip:0 error:0
```

（`run-script.sh` 是 [Ch 0](./00-environment-setup.md) 建好的 QEMU 包裝腳本：把腳本與檔案打包進 initramfs、開機執行、印出結果後關機。）

## 實例二：測試記錄了行為的改變

`tools/testing/selftests/splice/short_splice_read.sh` 測的是「對各種偽檔案系統的檔案做 splice() 會發生什麼」。它的特別之處在於：**這份規格自己有版本歷史**。

```console
$ git log --format='%h %ad %s' --date=short -- tools/testing/selftests/splice/short_splice_read.sh
177f504cb70c 2022-11-25 selftests: splice_read: Fix sysfs read cases
6daf076b717d 2021-05-26 selftests: splice: Adjust for handler fallback removal
9af47666cb0f 2020-08-06 selftests: splice: Check behavior of full and short splices
```

三個 commit，三個時代：2020 年寫下初版、2021 年因為 set_fs() 重構移除了 splice 的通用 fallback 而調整預期、2022 年因為 kernfs 加上 splice 支援又改成預期成功。**讀測試檔的 `git log`，等於讀這個行為的 changelog**。檔頭的註解也承認了這段歷史：

```sh
# Test for mishandling of splice() on pseudofilesystems, which should catch
# bugs like 11990a5bd7e5 ("module: Correctly truncate sysfs sections output")
#
# Since splice fallback was removed as part of the set_fs() rework, many of these
# tests expect to fail now. See https://lore.kernel.org/lkml/202009181443.C2179FB@keescook/
```
（`tools/testing/selftests/splice/short_splice_read.sh:4-8`（v6.18））

腳本呼叫一個 helper 程式 `splice_read`，所以先編譯它，再把腳本複製到同一個目錄執行（腳本用 `$(dirname "$0")/splice_read` 找 helper）：

```console
$ gcc -Wall -O2 -o splice_read ~/code_reading/linux/tools/testing/selftests/splice/splice_read.c
$ cp ~/code_reading/linux/tools/testing/selftests/splice/short_splice_read.sh .
$ sh ./short_splice_read.sh; echo "exit=$?"

proc_single_open(), seq_read() splice ...
  checking /proc/319812/limits ...
    splicing 4096 bytes ...
splice: Invalid argument
      no match: '' vs full read
ok: proc_single_open(), seq_read() splice correctly failed

special open(), seq_read() splice ...
  checking /proc/319812/comm ...
    splicing 4096 bytes ...
splice: Invalid argument
      no match: '' vs full read
ok: special open(), seq_read() splice correctly failed

proc_handler: proc_dointvec_minmax() splice ...
  checking /proc/sys/fs/nr_open ...
    splicing 4096 bytes ...
      matched full read
    splicing 2 bytes ...
      matched '10'
ok: proc_handler: proc_dointvec_minmax() splice succeeded

proc_handler: proc_dostring() splice ...
  checking /proc/sys/kernel/modprobe ...
    splicing 4096 bytes ...
      matched full read
    splicing 2 bytes ...
      matched '/s'
ok: proc_handler: proc_dostring() splice succeeded

proc_handler: special read splice ...
  checking /proc/sys/kernel/version ...
    splicing 4096 bytes ...
      matched full read
    splicing 2 bytes ...
      matched '#1'
ok: proc_handler: special read splice succeeded

test_module kernel module load ...
modprobe: FATAL: Module test_module not found in directory /lib/modules/6.18.58.1-microsoft-standard-WSL2
FAIL: test_module kernel module load should work

kernfs attr splice ...
  checking /sys/module/test_module/coresize ...
cat: /sys/module/test_module/coresize: No such file or directory
FAIL: something else went wrong

kernfs binattr splice ...
  checking /sys/module/test_module/sections/.init.text ...
cat: /sys/module/test_module/sections/.init.text: No such file or directory
FAIL: something else went wrong
exit=3
```

這份真實輸出值得逐段讀，因為它示範了**判讀測試失敗**的基本功：

1. 前五項全部符合預期。從中讀出規格：`/proc/<pid>/` 下的檔案**不支援** splice（`EINVAL`），`/proc/sys/` 下的檔案**支援**，而且支援「只讀 2 bytes」的短 splice。
2. 後三項失敗，但失敗原因是 `modprobe: FATAL: Module test_module not found`——**環境問題**，不是 kernel 行為問題。這個測試需要 `tools/testing/selftests/kmod/` 那類的測試 module，WSL 沒有，我們也沒有 root 可以載入。第二、三項的 `FAIL: something else went wrong` 是被第一項連帶拖垮的。
3. 所以正確的結論是：「5 項驗證通過，3 項因缺少 test_module 無法執行」，**不是**「kernfs 的 splice 壞了」。

測試失敗時，先問：**失敗訊息是在說 kernel 的行為，還是在說測試的前提？** 這和 harness 裡 `ASSERT`（前提）與 `EXPECT`（主張）的區分是同一件事。

> [Ch 39](./39-documentation-and-style.md) 的「文件說謊」一節用過這個檔案：第 126 行的註解寫「these should all fail」，但 `177f504cb70c` 已經把下面的預期改成 `expect_success`。測試的**程式碼**會跟著行為更新，測試的**註解**不一定。

## 自己寫規格探針

讀碼時你常會冒出一個假說：「這裡的意思應該是 X」。與其在腦中推，不如寫一個測試問 kernel。kselftest harness 是一個 header-only 的框架，你可以在自己的檔案裡用它，只要 `-I` 指到 selftests 目錄：

```c
// 用 kselftest harness 寫自己的「規格探針」
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <unistd.h>
#include <sys/eventfd.h>
#include "kselftest_harness.h"

/* eventfd 的計數器上限是 0xfffffffffffffffe，不是 UINT64_MAX */
TEST(eventfd_counter_max_is_ullong_max_minus_1)
{
	uint64_t v = UINT64_MAX - 1;
	int fd = eventfd(0, EFD_NONBLOCK);

	ASSERT_GE(fd, 0);
	EXPECT_EQ(write(fd, &v, sizeof(v)), sizeof(v));
	v = 1;	/* 再加 1 就會超過上限：非阻塞模式回 EAGAIN */
	EXPECT_EQ(write(fd, &v, sizeof(v)), -1);
	EXPECT_EQ(errno, EAGAIN);
	close(fd);
}

/* pipe 的預設容量 */
TEST(pipe_default_capacity)
{
	int p[2];

	ASSERT_EQ(pipe(p), 0);
	EXPECT_EQ(fcntl(p[0], F_GETPIPE_SZ), 65536);
	close(p[0]);
	close(p[1]);
}

/* selftest 只測了「小於 8 bytes」；程式碼顯示 read 與 write 對「大於 8」的處理不對稱 */
TEST(eventfd_oversized_buffer_asymmetry)
{
	uint64_t buf[2] = { 1, 0 };
	int fd = eventfd(5, 0);

	ASSERT_GE(fd, 0);
	EXPECT_EQ(write(fd, buf, sizeof(buf)), -1);	/* write 要求剛好 8 bytes */
	EXPECT_EQ(errno, EINVAL);
	EXPECT_EQ(read(fd, buf, sizeof(buf)), 8);	/* read 只要求至少 8 bytes */
	EXPECT_EQ(buf[0], 5);
	close(fd);
}

/* 故意寫錯的預期：以為 EFD_SEMAPHORE 的 read 會回傳整個計數 */
TEST(wrong_belief_semaphore_returns_whole_count)
{
	uint64_t v = 3;
	int fd = eventfd(0, EFD_SEMAPHORE);

	ASSERT_GE(fd, 0);
	ASSERT_EQ(write(fd, &v, sizeof(v)), sizeof(v));
	ASSERT_EQ(read(fd, &v, sizeof(v)), sizeof(v));
	EXPECT_EQ(v, 3);
	close(fd);
}

TEST_HARNESS_MAIN
```

```console
$ gcc -Wall -O2 -I ~/code_reading/linux/tools/testing/selftests -o spec_probe spec_probe.c
$ ./spec_probe; echo "exit=$?"
TAP version 13
1..4
# Starting 4 tests from 1 test cases.
#  RUN           global.eventfd_counter_max_is_ullong_max_minus_1 ...
#            OK  global.eventfd_counter_max_is_ullong_max_minus_1
ok 1 global.eventfd_counter_max_is_ullong_max_minus_1
#  RUN           global.pipe_default_capacity ...
#            OK  global.pipe_default_capacity
ok 2 global.pipe_default_capacity
#  RUN           global.eventfd_oversized_buffer_asymmetry ...
#            OK  global.eventfd_oversized_buffer_asymmetry
ok 3 global.eventfd_oversized_buffer_asymmetry
#  RUN           global.wrong_belief_semaphore_returns_whole_count ...
# spec_probe.c:58:wrong_belief_semaphore_returns_whole_count:Expected v (1) == 3 (3)
# wrong_belief_semaphore_returns_whole_count: Test failed
#          FAIL  global.wrong_belief_semaphore_returns_whole_count
not ok 4 global.wrong_belief_semaphore_returns_whole_count
# FAILED: 3 / 4 tests passed.
# Totals: pass:3 fail:1 xfail:0 xpass:0 skip:0 error:0
exit=1
```

四個探針各自的來源，要能說得出來：

| 探針 | 假說從哪來 | 對應的程式碼 |
|---|---|---|
| 計數器上限 | `eventfd_write()` 的條件式 `ULLONG_MAX - ctx->count > ucnt` | `fs/eventfd.c:262`：count = `ULLONG_MAX-1` 時，`1 > 1` 不成立，非阻塞回 `-EAGAIN` |
| pipe 容量 65536 | `PIPE_DEF_BUFFERS` | `include/linux/pipe_fs_i.h:5` 定義為 16；16 個 buffer × 4096 bytes page = 65536 |
| read/write 不對稱 | 讀碼時發現兩個檢查寫法不同 | read：`iov_iter_count(to) < sizeof(ucnt)`（`fs/eventfd.c:220`）；write：`count != sizeof(ucnt)`（`fs/eventfd.c:254`） |
| 故意錯的假說 | 「read 會把整個計數讀走」 | 對 semaphore 模式不成立，harness 精確指出 `Expected v (1) == 3 (3)` |

第三個探針是本章最重要的示範：**官方 selftest 只測了「小於 8 bytes 會失敗」，沒有測「大於 8 bytes」**。讀程式碼發現兩邊檢查不同 → 寫探針 → kernel 確認 write 拒絕 16 bytes、read 接受 16 bytes 並只填 8 bytes。這種行為沒寫在 selftest 裡，但它是 kernel 實際的合約。

第四個探針示範「失敗是教學素材」：harness 的失敗訊息格式 `檔案:行號:測試名:Expected <實際值> == <預期值>` 讓你一眼看出假說錯在哪。

## KUnit：kernel 內部的規格

kselftest 只看得到 user space 能觸及的行為。kernel 內部的資料結構（kfifo、list、bitmap、xarray）沒有 syscall 可以直接戳，它們的規格寫在 KUnit 測試裡。

以 [Ch 27](./27-bitmap-percpu-kfifo.md) 介紹過的 kfifo 為例，`lib/tests/kfifo_kunit.c`（224 行，v6.15 加入）：

```c
static void kfifo_test_reset_should_clear_the_fifo(struct kunit *test)
{
	DEFINE_KFIFO(my_fifo, u8, KFIFO_SIZE);

	kfifo_put(&my_fifo, 1);
	kfifo_put(&my_fifo, 2);
	kfifo_put(&my_fifo, 3);
	KUNIT_EXPECT_EQ(test, kfifo_len(&my_fifo), 3);

	kfifo_reset(&my_fifo);

	KUNIT_EXPECT_EQ(test, kfifo_len(&my_fifo), 0);
	KUNIT_EXPECT_TRUE(test, kfifo_is_empty(&my_fifo));
}
```
（`lib/tests/kfifo_kunit.c:14-27`（v6.18））

這個檔案的測試函式命名風格是 `<物件>_test_<動作>_should_<結果>`，名字本身就是規格句子。看結尾的測試清單：

```c
static struct kunit_case kfifo_test_cases[] = {
	KUNIT_CASE(kfifo_test_reset_should_clear_the_fifo),
	KUNIT_CASE(kfifo_test_define_should_define_an_empty_fifo),
	KUNIT_CASE(kfifo_test_len_should_ret_n_of_stored_elements),
	KUNIT_CASE(kfifo_test_put_should_insert_and_get_should_pop),
	KUNIT_CASE(kfifo_test_in_should_insert_multiple_elements),
	KUNIT_CASE(kfifo_test_out_should_pop_multiple_elements),
	KUNIT_CASE(kfifo_test_dec_init_should_define_an_empty_fifo),
	KUNIT_CASE(kfifo_test_define_should_equal_declare_init),
	KUNIT_CASE(kfifo_test_alloc_should_initiliaze_a_ptr_fifo),
	KUNIT_CASE(kfifo_test_peek_should_not_remove_elements),
	{},
};

static struct kunit_suite kfifo_test_module = {
	.name = "kfifo",
	.test_cases = kfifo_test_cases,
};

kunit_test_suites(&kfifo_test_module);
```
（`lib/tests/kfifo_kunit.c:201-220`（v6.18））

十個名字讀下來，你就知道 kfifo 的 API 有 reset、len、put/get、in/out、peek、`DEFINE_KFIFO` 與 `DECLARE_KFIFO`+`INIT_KFIFO` 兩種宣告方式，以及「peek 不會移除元素」這個關鍵語意。比讀 `include/linux/kfifo.h` 那堆巨集（[Ch 22](./22-kernel-macro-decoding.md)）快得多。

每個 KUnit 測試都有對應的 Kconfig 選項：

```
config KFIFO_KUNIT_TEST
	tristate "KUnit Test for the generic kernel FIFO implementation" if !KUNIT_ALL_TESTS
	depends on KUNIT
	default KUNIT_ALL_TESTS
```
（`lib/Kconfig.debug:2747-2750`（v6.18））

### 怎麼跑 KUnit

KUnit 的官方包裝工具是 `tools/testing/kunit/kunit.py`。跑 kfifo 這一組的指令是：

```console
$ ./tools/testing/kunit/kunit.py run 'kfifo'
```

**本段未實測，為理論預期行為。** 原因：`kunit.py run` 會用 `--build_dir`（預設 `.kunit`）在 tree 裡另建一份 kernel，預設架構 `--arch` 是 `um`（User Mode Linux，把 kernel 編成一個 user-space 程式來跑）。這門課的規定是不改動 `~/code_reading/linux` 這棵樹、也不另外再 build 一份 kernel。它的預期行為是：產生一份 `.kunitconfig`（預設內容來自 `tools/testing/kunit/configs/default.config`：`CONFIG_KUNIT=y`、`CONFIG_KUNIT_EXAMPLE_TEST=y`、`CONFIG_KUNIT_ALL_TESTS=y`）、編出 UML kernel、執行並把 KTAP 輸出解析成摘要。若你有自己的可寫 tree，照 `Documentation/dev-tools/kunit/start.rst` 的步驟做即可驗證。

我們能實測的是 KUnit 框架本身。`~/code_reading/build` 的 `.config` 有 `CONFIG_KUNIT=y`、`CONFIG_KUNIT_AUTORUN_ENABLED=y`，但沒有打開任何測試 suite（`grep -c 'KUNIT_TEST is not set' .config` 得到 61）。在 QEMU 裡開機看 dmesg：

```console
$ cat guest2.sh
#!/bin/sh
dmesg | grep -A2 'KTAP version'
$ ~/code_reading/qemu/run-script.sh guest2.sh | grep -A2 KTAP
[    2.097370] KTAP version 1
[    2.097394] 1..0
```

`1..0` 的意思是「計畫執行 0 個測試」：KUnit executor 在開機時跑了，但沒有 suite 被編進來。要看到 kfifo 的結果，得把 `CONFIG_KFIFO_KUNIT_TEST=y` 加進 config 重新建置——這就回到上面「未實測」的部分。

## user-space 對照：Lua 與 Redis 怎麼寫規格

kernel 的測試文化相對年輕。成熟的 user-space 專案把測試當規格的程度更深，拿來對照很有啟發。

**Lua 5.4.9** 的 `testes/` 目錄有 35 個項目，每個檔案對應語言或標準庫的一塊。它的寫法是最純粹的「斷言即規格」：

```lua
assert(string.rep('teste', 0) == '')
assert(string.rep('', 10) == '')
...
assert(string.rep('teste', 0, 'xuxu') == '')
assert(string.rep('teste', 1, 'xuxu') == 'teste')
assert(string.rep('\1\0\1', 2, '\0\0') == '\1\0\1\0\0\1\0\1')
assert(string.rep('', 10, '.') == string.rep('.', 9))
```
（`lua-5.4.9/testes/strings.lua:108-122` 節錄）

`string.rep` 在重複 0 次、空字串、帶分隔符、內嵌 `\0` 時的行為，全部一行一個。讀 `lstrlib.c` 的 `str_rep()` 之前讀這幾行，你就知道要找哪些分支。

實際執行（在 scratch 複製一份原始碼建置，因為預設 makefile 要求 readline，這台機器沒有 readline 標頭檔，所以覆寫掉相關旗標）：

```console
$ cp -r ~/code_reading/lua-5.4.9 lua && cd lua
$ make -s MYCFLAGS='-std=c99 -DLUA_USE_LINUX' MYLIBS='-ldl'
$ cd testes && ../lua -e "_U=true" strings.lua
'ctype' locale not found

 >>> testC not active: skipping 'pushfstring' tests <<<

OK
```

注意中間那行：`testC not active`。Lua 的測試分兩層，一層用公開 API，另一層需要用 `-DLUA_USER_H='"ltests.h"'` 編譯出內部測試介面（makefile 第 66 行註解掉的 `TESTS=` 那行）。這和 kselftest（外部）／KUnit（內部）的分層是同一個概念。

**Redis 7.2.17** 的 `tests/unit/` 用 Tcl 寫，測試名稱本身就是規格句子：

```console
$ grep -n 'test {' tests/unit/type/incr.tcl | head -12
2:    test {INCR against non existing key} {
8:    test {INCR against key created by incr itself} {
...
37:    test {INCR fails against key with spaces (left)} {
43:    test {INCR fails against key with spaces (right)} {
49:    test {INCR fails against key with spaces (both)} {
55:    test {DECRBY negation overflow} {
```

```tcl
    test {INCR fails against key with spaces (left)} {
        r set novar "    11"
        catch {r incr novar} err
        format $err
    } {ERR*}
```
（`redis-7.2.17/tests/unit/type/incr.tcl:37-41`）

「值是 `"    11"` 時 INCR 要失敗」這種邊界，你從 `t_string.c` 的 `incrDecrCommand()` 很難一眼看出（它藏在 `string2ll()` 的解析細節裡），測試直接告訴你。Redis 測試需要先建置 `redis-server` 再用 `./runtest` 啟動，這台機器上的 Redis 原始碼沒有建置過，**本段 Redis 測試未實測**；Tcl 片段是從原始碼直接複製的。

| 專案 | 測試位置 | 風格 | 和 kernel 的對照 |
|---|---|---|---|
| Lua | `testes/*.lua` | 大量 `assert`，一行一條規格 | 類似 KUnit 的密度，但用語言本身寫 |
| Redis | `tests/unit/**/*.tcl` | 透過真實 client 連線測 | 類似 kselftest：從外部介面測 |
| kernel | `tools/testing/selftests/`、KUnit | 兩層並存 | 覆蓋率不均，很多子系統只有其中一種或都沒有 |

## 對比與取捨

| 規格來源 | 優點 | 缺點 | 何時優先用 |
|---|---|---|---|
| kselftest | 測外部行為；可以在 user space 直接跑 | 只涵蓋作者想到的情況；有些需要 root 或特定 module | 想知道 syscall／ioctl 語意 |
| KUnit | 能測內部函式與邊界 | 要建 kernel 才能跑；覆蓋率不一 | 讀資料結構、lib/ 底下的通用程式碼 |
| man page | 完整、面向使用者 | 獨立專案，可能落後 | 測試沒涵蓋的 syscall 語意 |
| kernel-doc | 和程式碼同檔 | 不會被執行 | 內部 API 的 context 限制 |
| 自己的探針 | 精確回答你的假說 | 要自己判讀是否寫對 | 上面都沒答案時 |

## 踩雷集錦

1. **「測試通過，所以這個 API 沒有其他行為」**：錯。eventfd selftest 只測了「小於 8 bytes」，沒測「大於 8 bytes 時 read/write 不對稱」。測試是規格的**下界**，不是全貌。
2. **「測試失敗就是 kernel 有 bug」**：先判讀失敗訊息。實例二的 3 個 FAIL 全部來自 `modprobe: FATAL: Module test_module not found`——環境前提不成立。kselftest 有些測試會在前提不成立時回報 SKIP，但不是每個測試都寫得這麼周到。
3. **「在 WSL 跑過就等於在 v6.18 跑過」**：WSL2 的 kernel 是微軟維護的分支，`config` 也不同（例如它沒開 `CONFIG_WATCH_QUEUE`）。對版本敏感的結論，要在 QEMU 裡用確切的 kernel 驗證，如本章對 eventfd selftest 做的。
4. **「grep 到 API 名字的測試檔就是它的測試」**：大量測試只是**使用**該 API。`rg eventfd tools/testing/selftests` 會找到 vfio、kvm、pidfd 的測試，它們把 eventfd 當通知工具。先從目錄名找專門的測試。
5. **「測試的註解和測試一樣可信」**：註解不被執行。`short_splice_read.sh:126` 的註解和下面的程式碼意思相反（[Ch 39](./39-documentation-and-style.md)）。信 `expect_success`／`EXPECT_EQ`，不信 `#` 和 `/* */`。
6. **「KUnit 測試會在一般發行版 kernel 開機時跑」**：要 `CONFIG_KUNIT=y` 且對應的 `CONFIG_*_KUNIT_TEST` 被選進來。本章的 build 只有框架、沒有 suite，所以開機只印出 `1..0`。

## 進階：再往深一層

- **把測試檔的 `git log` 當行為 changelog**：`git log --format='%h %ad %s' --date=short -- tools/testing/selftests/<子系統>/` 列出每次預期行為的變化。測試從 `expect_failure` 改成 `expect_success` 的那個 commit，通常會引用改變行為的那個 kernel commit（如 `177f504cb70c` 引用 `f2d6c2708bd84`）。這是找「某個行為何時改變」最快的方法之一，在 [練習 F](./practice-f-feature-archaeology.md) 會用到。
- **syzkaller reproducer 也是測試**：很多 Fixes commit 的訊息裡有 `Reported-by: syzbot+...` 和 `Closes: https://syzkaller.appspot.com/bug?extid=...`。syzbot 頁面上的 C reproducer 是一個最小的觸發程式，讀它等於讀「這個 bug 的規格」。
- **`make kselftest` 的正式跑法**：`Documentation/dev-tools/kselftest.rst` 說明 `make -C tools/testing/selftests TARGETS=ptrace run_tests` 與 `make O=/tmp/kselftest TARGETS="size timers" kselftest` 等用法。本章刻意繞開它，直接用 gcc 編單一測試，因為讀碼時你通常只要一個測試。
- **測試即規格的極端形式**：`tools/testing/radix-tree/` 把 xarray 和 maple tree 的 kernel 原始碼直接編成 user-space 程式來測。[Ch 46](./46-acquiring-domain-knowledge.md) 會實際建置並執行 maple tree 的這套測試，用它來建立對一個陌生資料結構的概念模型。

## 動手練習

1. **測試先讀**：打開 `tools/testing/selftests/filesystems/epoll/epoll_wakeup_test.c`，只讀 `TEST(...)` 的名字和每個測試上方的註解，不讀 `fs/eventpoll.c`。寫下你推論出的 5 條 epoll 規格，然後編譯執行它驗證。
2. **找測試的缺口**：讀 `fs/eventfd.c` 的 `eventfd_poll()`（`count == ULLONG_MAX` 時會加上 `EPOLLERR`），確認 eventfd selftest 有沒有測這個。沒有的話，思考：user space 有沒有辦法讓 count 變成 `ULLONG_MAX`？（提示：看 `eventfd_write()` 的上限，再看 `eventfd_signal_mask()` 的上限，兩者不同。）
3. **探針練習**：替 pipe 寫一個 harness 探針，驗證「`F_SETPIPE_SZ` 設成 5000 時，實際容量會被向上取整成多少」。先讀 `fs/pipe.c` 的 `pipe_set_size()` 與 `round_pipe_size()` 預測答案，再跑。
4. **讀 KUnit 學 API**：挑 `lib/tests/list-test.c` 或 `lib/tests/hashtable_test.c`，只讀 `KUNIT_CASE` 清單，寫出你推論的 API 清單，再對照 [Ch 23](./23-list-head-hlist.md) 或 [Ch 25](./25-hash-tables.md)。

## 本章重點整理

- 測試是唯一會被機器檢查的文件，所以最不容易過時；讀碼前先找測試，帶著已知答案去讀實作。
- kselftest 測 user space 看得到的行為，大多可以用 gcc 直接編譯執行；KUnit 測 kernel 內部 API，要建 kernel 才能跑。
- 讀測試的順序：測試名稱清單 → 註解（常是 man page 原句）→ `ASSERT`（前提）與 `EXPECT`（主張）。
- 測試失敗先分辨是行為問題還是環境問題。
- 測試是規格的下界；讀碼發現的行為若測試沒涵蓋，用 harness 寫探針自己問 kernel。

## 自我檢核

- [ ] 不看筆記，解釋為什麼「測試比註解不容易過時」，並舉出一個測試**註解**仍然過時的反例。
- [ ] 如果面試官問：「kselftest 和 KUnit 你會怎麼選來理解一個陌生的 kernel 功能？」你會怎麼回答？
- [ ] 實例二的輸出中有 3 個 FAIL。不翻回去，說出它們的根本原因，以及你怎麼從輸出判斷出來的。
- [ ] 描述你從讀 `fs/eventfd.c` 到寫出 `eventfd_oversized_buffer_asymmetry` 探針的推理過程。
- [ ] 想一個你最近讀過、但不確定行為的 syscall 或 library 函式，設計一個 3 條斷言的探針。

## 延伸閱讀

### 官方文件

- **[Kernel Testing Guide](https://docs.kernel.org/dev-tools/testing-overview.html)**（tree 內：`Documentation/dev-tools/testing-overview.rst`）
  - **讀哪裡**：「The Difference Between KUnit and kselftest」一節。
  - **能學到什麼**：官方對兩套框架的分工說明，以及其他工具（KASAN、sparse、Coccinelle）在測試體系中的位置。
  - **前提**：無。
- **[Linux Kernel Selftests](https://docs.kernel.org/dev-tools/kselftest.html)**（tree 內：`Documentation/dev-tools/kselftest.rst`）
  - **讀哪裡**：「Running the selftests」與「Test Harness」兩節。
  - **能學到什麼**：`TARGETS=`、`O=` 的正式跑法；harness 的 `FIXTURE`、`TEST_F` 等本章沒展開的進階用法。
  - **前提**：本章的 harness 範例。
- **[KUnit - Linux Kernel Unit Testing](https://docs.kernel.org/dev-tools/kunit/index.html)**（tree 內：`Documentation/dev-tools/kunit/`）
  - **讀哪裡**：`start.rst`（Getting Started）與 `style.rst`（測試命名慣例）。
  - **能學到什麼**：`kunit.py run` 的完整流程（本章未實測的部分），以及 KUnit 測試的命名規則，讓你在 tree 裡更快找到測試檔。
  - **前提**：知道怎麼 build kernel（[Ch 0](./00-environment-setup.md)）。

### 原始碼

- **`tools/testing/selftests/kselftest_harness.h`**（v6.18）
  - **讀哪裡**：檔頭到約第 500 行的註解，特別是 `ASSERT_*`／`EXPECT_*` 的語意說明（第 500–501 行）。
  - **能學到什麼**：harness 怎麼用 `fork()` 隔離每個測試、怎麼產生 TAP 輸出。它本身也是一個很好的「巨集元程式」讀碼素材（[Ch 14](./14-macro-metaprogramming.md)）。
  - **前提**：Ch 13–14 的巨集基礎。

### 書籍

- **《The Linux Programming Interface》** — Michael Kerrisk（No Starch Press, 2010）
  - **這本書的定位**：作者是 man-pages 專案的長期維護者；書中每個 syscall 都附有可執行的範例程式，本質上就是「規格探針」的集合。
  - **讀哪幾章**：Ch 44（Pipes and FIFOs）與 Ch 63（Alternative I/O Models）跟本章的 pipe、eventfd、epoll 最相關。版本較舊，eventfd 只有簡短提及，但寫探針驗證行為的方法論完全適用。

到這裡，Part 6 的工具都到齊了：原始碼樹地圖、Kconfig、git 考古、mailing list、文件、測試。下一個練習要你把它們全部用上，替一個真實的 kernel 功能還原它的設計理由。

→ [練習 F：考古一個功能，還原它的設計理由](./practice-f-feature-archaeology.md)
