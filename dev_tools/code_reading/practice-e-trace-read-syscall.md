# 練習 E — 從 read(2) 一路追到 page cache

> **目標**：把 Ch 28–34 的工具全部用上，對一次 `read(2)` 產出一份完整的 call path 筆記：從 syscall 入口到 page cache 的 xarray 查找、再到資料複製回 user space，每一步標出 `檔案:行號（v6.18）`、經過的關鍵資料結構、持有的 lock / RCU、可能睡眠的點。最後在 QEMU 裡用 ftrace function_graph 驗證你的靜態推論，並解釋推論和實測不一致的地方。

> **鴻溝**：本練習同時練**間接鴻溝**（`f->f_op->read_iter()`、`mapping->a_ops->readahead()`、`bio->bi_end_io` 三次斷點）、**領域鴻溝**（page cache、folio、readahead 是什麼）與**目標鴻溝**（整條路徑上幾百個函式，你要決定哪些深入、哪些當黑箱）。

> **環境**：Linux kernel v6.18 原始碼（`~/code_reading/linux`）與同版本 x86_64 build（`~/code_reading/build`，`x86_64_defconfig` + ftrace）；QEMU TCG + busybox initramfs，guest 的 `/tmp` 是 tmpfs；ext4 實驗用 `mkfs.ext4 -d` 在 host 上做一個 16 MB 映像檔，以 virtio-blk 掛進 guest。測試程式用 gcc 11.4 `-static` 編譯。

## 背景與動機

「`read(2)` 到底做了什麼」是 kernel 讀碼最經典的一條路徑：它短到一個下午能追完，又長到經過 syscall 層、VFS、檔案系統、page cache、block layer、中斷完成路徑和 scheduler。追完它，你會親手碰到前面每一章的主題：

| 章 | 這條路徑上的對應物 |
|---|---|
| [Ch 28](./28-ops-tables-indirect-calls.md) ops table | `file->f_op->read_iter`、`mapping->a_ops->readahead` |
| [Ch 29](./29-syscall-entry-paths.md) syscall 入口 | `SYSCALL_DEFINE3(read, ...)`、`x64_sys_call` |
| [Ch 30](./30-reading-locking-code.md) lock | `mapping->invalidate_lock`（rw_semaphore）、folio lock（`PG_locked`） |
| [Ch 31](./31-reading-rcu-code.md) RCU | `filemap_get_read_batch()` 在 `rcu_read_lock()` 下走 xarray |
| [Ch 26](./26-xarray-maple-tree.md) xarray | `mapping->i_pages` |
| [Ch 32](./32-async-irq-softirq-workqueue.md) 非同步 | I/O 完成在中斷路徑，ext4 解密丟 workqueue |
| [Ch 33](./33-wait-queues-completion.md) 等待 | cold cache 時睡在 folio 的 hashed wait queue 上 |
| [Ch 34](./34-notifiers-and-callbacks.md) callback | `bio->bi_end_io = mpage_end_io` |

真實工作裡，這條路徑會在你追「為什麼這個讀取這麼慢」「為什麼 `read` 回傳的資料是舊的」「這個 CVE 的 page cache 污染是怎麼發生的」時出現。這個練習不是假題目，是你之後會一再回來的地圖。

## 任務規格

### 輸入

- 一個 user-space 程式對一個**已存在的一般檔案**呼叫一次 `read(fd, buf, 4096)`，`fd` 以 `O_RDONLY` 開啟（沒有 `O_DIRECT`），檔案大小小於一個 page。
- 三種檔案系統情境，**至少完成 A 與 B，C 為加分**：
  - **A. tmpfs**（guest 的 `/tmp`，defconfig 有 `CONFIG_TMPFS=y`、`CONFIG_SHMEM=y`）。
  - **B. ext4，page cache 已命中（warm）**：同一檔案第二次讀。
  - **C. ext4，page cache 未命中（cold）**：剛 mount 後第一次讀，需要真的發 I/O。

### 輸出：一份 call path 筆記

每一步一列，欄位如下：

| 欄位 | 內容 |
|---|---|
| 深度 | 呼叫深度（從 syscall 入口算 0） |
| 函式 | 函式名；inline 的標 `(inline)` |
| 位置 | `檔案:行號（v6.18）`，定義處 |
| 怎麼到這裡 | 直接呼叫／函式指標（寫出指標欄位與賦值點）／巨集展開 |
| 資料結構 | 這一步讀寫的關鍵 struct 與欄位 |
| 鎖／RCU | 這一步持有或取得的 lock、RCU read-side |
| 可能睡？ | 是／否，並寫理由 |

另外附三段文字：

1. **間接呼叫的解析過程**：對每一個函式指標，寫出你怎麼找到實際目標（搜尋指令與結果）。
2. **睡眠點清單**：情境 C 下 task 在哪裡睡、被誰叫醒、叫醒者在什麼 context。
3. **實測差異報告**：ftrace 輸出和你的靜態筆記有哪些不一致，每一處的原因。

### 限制

- 所有程式碼引用必須來自 v6.18 tree，行號要能在 `~/code_reading/linux` 對上。
- 不要求深入 block layer 與 virtio driver 內部（`submit_bio` 之後可以當黑箱），但要能說出完成通知怎麼回到 page cache。
- 不要求追 `security_file_permission` / SELinux 內部。

### 驗收標準

- [ ] 情境 A、B 的筆記從 `SYSCALL_DEFINE3(read, ...)` 寫到資料複製回 user space，沒有斷點。
- [ ] 每個函式指標都有「賦值點」證據。
- [ ] 至少一份 function_graph 實測輸出，並逐行對應到筆記。
- [ ] 說得出為什麼 tmpfs 和 ext4 在 `->read_iter` 之後走不同的路。
- [ ] （情境 C）說得出 `io_schedule()` 是從哪個函式、因為什麼條件呼叫的。

## 期望輸出範例

筆記的前兩列長這樣（格式示範，內容你要自己查證）：

```
| 深度 | 函式 | 位置 | 怎麼到這裡 | 資料結構 | 鎖／RCU | 可能睡？ |
|---|---|---|---|---|---|---|
| 0 | __x64_sys_read（由 SYSCALL_DEFINE3(read,...) 生成） | fs/read_write.c:722 | x64_sys_call() 的 switch，syscall 號 0（syscall_64.tbl:12） | struct pt_regs | 無 | 本身不睡 |
| 1 | ksys_read | fs/read_write.c:704 | 直接呼叫 | struct fd（CLASS(fd_pos)）、file->f_pos | f_pos_lock（若需要） | 可能（f_pos_lock 是 mutex） |
```

ftrace 驗證的期望形狀（情境 B，warm ext4）：

```
ksys_read
  fdget_pos
  vfs_read
    rw_verify_area
    ext4_file_read_iter
      generic_file_read_iter
        filemap_read
          filemap_get_pages
            filemap_get_read_batch      ← rcu_read_lock 下走 xarray
          folio_mark_accessed
          touch_atime
```

## 如果你卡住了

1. **追到 `file->f_op->read_iter` 就斷了**：不要在 `vfs_read` 附近找答案。去找「`f_op` 是在哪裡被設定的」：一般檔案的 `file->f_op` 來自 `inode->i_fop`，而 `i_fop` 是檔案系統在建 inode 時設的。搜 `i_fop = &` 加上檔案系統目錄。
2. **找不到 `new_sync_read` 出現在 trace 裡**：它是 `static` 函式，可能被 inline 了。用 `nm ~/code_reading/build/vmlinux | grep new_sync_read` 確認符號是否存在。
3. **tmpfs 的路徑和你想的不一樣**：別假設所有檔案系統都走 `generic_file_read_iter`。打開 `mm/shmem.c` 的 `shmem_file_operations` 看 `.read_iter` 指向誰，再讀那個函式。
4. **`copy_page_to_iter` 在 trace 裡找不到**：先確認它有沒有被呼叫（讀原始碼），再確認它能不能被 ftrace 追蹤（看它所在目錄的 Makefile 有沒有拿掉 ftrace 的編譯旗標）。
5. **情境 C 看不到 `schedule`**：檢查你有沒有關掉 `options/funcgraph-irqs`。我們實測時關掉它的那一次，`schedule` 子樹沒有出現；打開後才看到。

## 實作步驟建議

### Step 1：從 syscall 號到 `ksys_read`

用 [Ch 29](./29-syscall-entry-paths.md) 的方法：`syscall_64.tbl` 裡找 `read` 的號碼 → `SYSCALL_DEFINE3(read, ...)` → `ksys_read`。讀 `ksys_read` 時注意 `CLASS(fd_pos, f)(fd)`——這是 `include/linux/cleanup.h` 的 scope-based 資源管理（[Ch 17](./17-kernel-error-conventions.md)），展開後在進入時呼叫 `fdget_pos()`、離開 scope 時自動 `fdput_pos()`。

**子目標**：寫出深度 0–2 的三列，說明 `pos` 為什麼先複製到區域變數、最後才寫回 `f_pos`。

### Step 2：`vfs_read` 與第一個間接呼叫

讀 `vfs_read`：權限檢查（`FMODE_READ`、`FMODE_CAN_READ`）、`access_ok`、`rw_verify_area`，然後是分支——`f_op->read` 優先，否則 `new_sync_read` → `f_op->read_iter`。解析 `f_op`：

```
$ rg -n '\.read_iter\s*=' fs/ext4/file.c mm/shmem.c fs/ramfs/file-mmu.c
$ rg -n 'i_fop = &(ext4_file_operations|shmem_file_operations|ramfs_file_operations)' fs mm
```

**子目標**：三種檔案系統各自的 `read_iter` 實作與 `i_fop` 賦值點。

### Step 3：page cache 命中路徑

情境 B：`ext4_file_read_iter` → `generic_file_read_iter` → `filemap_read` → `filemap_get_pages` → `filemap_get_read_batch`。讀 `filemap_get_read_batch` 時對照 [Ch 26](./26-xarray-maple-tree.md) 的 `XA_STATE` / `xas_load` / `xas_next`，以及 [Ch 31](./31-reading-rcu-code.md) 的「RCU 下拿 reference、再 reload 確認」模式。

情境 A：`shmem_file_read_iter` → `shmem_get_folio` → `shmem_get_folio_gfp` → `filemap_get_entry`。

**子目標**：兩條路徑各自在哪裡查 xarray、在哪裡把資料複製出去；兩者的 folio lock 處理有什麼不同。

### Step 4：資料怎麼回到 user space

`copy_folio_to_iter`（inline）→ `copy_page_to_iter` → `_copy_to_iter`。`iov_iter` 是在 `new_sync_read` 裡用 `iov_iter_ubuf(&iter, ITER_DEST, buf, len)` 建的，`buf` 是 user 指標。

**子目標**：說出 `copy_page_to_iter` 為什麼要 `kmap_local_page`（在 64-bit 上它幾乎不做事，但程式碼要對 32-bit HIGHMEM 也正確），以及 user buffer 若發生 page fault 會怎樣（可能睡）。

### Step 5：cache 未命中與睡眠

情境 C：`filemap_get_read_batch` 回傳空 → `page_cache_sync_ra` → `page_cache_ra_order` → `page_cache_ra_unbounded`（配 folio、`filemap_add_folio` 插進 xarray、`read_pages`）→ `mapping->a_ops->readahead`（第二個間接呼叫）→ `ext4_readahead` → `ext4_mpage_readpages` → `submit_bio`。回到 `filemap_get_pages`，再查一次 batch，這次 folio 在但不是 uptodate → `filemap_update_page` → `folio_trylock` 失敗 → `folio_put_wait_locked` → `folio_wait_bit_common` → `io_schedule`。

叫醒者：`bio->bi_end_io`（第三個間接呼叫，`mpage_end_io`）→ `__read_end_io` → `folio_end_read` → `folio_wake_bit`。

**子目標**：睡眠點清單，含叫醒者的 context。

### Step 6：用 ftrace 驗證並寫差異報告

見下一節。把每一行 trace 對到筆記的一列；trace 有而筆記沒有的（例如中斷）、筆記有而 trace 沒有的（inline、不可追蹤的函式），逐一解釋。

## 驗證：在 QEMU 裡跑 function_graph

### 測試程式

這支程式只在自己的 pid 上開 tracing，把 trace 範圍縮到那一次 `read`：

```c
/* readone.c: 只在自己的 pid 上開 function_graph，對檔案做一次 read(2) */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void wr(const char *path, const char *s)
{
	int fd = open(path, O_WRONLY | O_TRUNC);
	if (fd < 0) { perror(path); exit(1); }
	if (write(fd, s, strlen(s)) < 0) { perror(path); exit(1); }
	close(fd);
}

int main(int argc, char **argv)
{
	char buf[4096], pid[32];
	const char *T = "/sys/kernel/tracing/";
	char p[256];
	if (argc < 2) { fprintf(stderr, "usage: %s file\n", argv[0]); return 1; }
	int fd = open(argv[1], O_RDONLY);
	if (fd < 0) { perror(argv[1]); return 1; }

	snprintf(pid, sizeof pid, "%d", getpid());
	snprintf(p, sizeof p, "%sset_ftrace_pid", T);  wr(p, pid);
	snprintf(p, sizeof p, "%stracing_on", T);      wr(p, "1");
	ssize_t n = read(fd, buf, sizeof buf);         /* <- 要追的那一次 */
	wr(p, "0");
	printf("read returned %zd, first bytes: %.8s\n", n, buf);
	close(fd);
	return 0;
}
```

`open` 放在開 tracing 之前，所以 trace 裡只有 `read`。`buf` 是 4096 bytes，`%.8s` 只印前 8 個字元，避免檔案內容沒有結尾 NUL 時印出垃圾。

### 準備 ext4 映像檔與 guest 腳本

在 WSL host 上（不需要 root，`mkfs.ext4 -d` 直接把目錄內容放進映像檔）：

```sh
cd ~/code_reading/scratch/pe
gcc -static -O2 -o readone readone.c
rm -rf d && mkdir d && printf 'hello ext4 page cache\n' > d/f && head -c 65536 /dev/urandom > d/big
mkfs.ext4 -q -F -d d ext4.img 16M
```

guest 腳本 `guest2.sh`：

```sh
#!/bin/sh
T=/sys/kernel/tracing
mkdir -p /mnt/e /mnt/r
mount -t ext4 /dev/vda /mnt/e && grep ' /mnt/e ' /proc/mounts
mount -t ramfs none /mnt/r && echo "hello ramfs" > /mnt/r/f
echo 0 > $T/tracing_on
echo ksys_read > $T/set_graph_function
echo function_graph > $T/current_tracer
echo 1 > $T/options/funcgraph-tail
echo 0 > $T/options/funcgraph-irqs
for f in /mnt/e/f /mnt/e/f /mnt/r/f /tmp/f; do
  [ "$f" = /tmp/f ] && echo hi > /tmp/f
  echo > $T/trace
  echo "#### $f"
  /bin/readone $f
  cat $T/trace
done
```

同一個 ext4 檔案讀兩次：第一次 cold、第二次 warm。ramfs 是加分對照組（它的 `read_iter` 直接是 `generic_file_read_iter`）。執行：

```sh
QEMU_EXTRA="-drive file=$PWD/ext4.img,format=raw,if=virtio" \
  ~/code_reading/qemu/run-script.sh ./guest2.sh ./readone > out2.txt 2>&1
```

### 實測輸出：情境 B（ext4 warm）

```
#### /mnt/e/f
read returned 22, first bytes: hello ex
# tracer: function_graph
#
# CPU  DURATION                  FUNCTION CALLS
# |     |   |                     |   |   |   |
 0)               |  ksys_read() {
 0)   4.679 us    |    fdget_pos();
 0)               |    vfs_read() {
 0)               |      rw_verify_area() {
 0)               |        security_file_permission() {
 0)               |          selinux_file_permission() {
 0)   0.881 us    |            avc_policy_seqno();
 0)   3.596 us    |          } /* selinux_file_permission */
 0)   5.190 us    |        } /* security_file_permission */
 0)   6.743 us    |      } /* rw_verify_area */
 0)               |      ext4_file_read_iter() {
 0)               |        generic_file_read_iter() {
 0)               |          filemap_read() {
 0)   0.671 us    |            __cond_resched();
 0)               |            filemap_get_pages() {
 0)               |              filemap_get_read_batch() {
 0)   0.671 us    |                __rcu_read_lock();
 0)   0.921 us    |                __rcu_read_lock();
 0)   0.751 us    |                __rcu_read_unlock();
 0)   0.701 us    |                __rcu_read_unlock();
 0) + 14.236 us   |              } /* filemap_get_read_batch */
 0) + 17.612 us   |            } /* filemap_get_pages */
 0)               |            folio_mark_accessed() {
 0)   1.853 us    |              __folio_batch_add_and_move();
 0)   2.395 us    |              workingset_activation();
 0) + 11.541 us   |            } /* folio_mark_accessed */
 0)               |            touch_atime() {
 0)               |              atime_needs_update() {
 0)   0.701 us    |                make_vfsuid();
 0)   0.711 us    |                make_vfsgid();
 0)               |                current_time() {
 0)   0.691 us    |                  ktime_get_coarse_real_ts64_mg();
 0)   0.751 us    |                  timestamp_truncate();
 0)   4.108 us    |                } /* current_time */
 0) + 11.611 us   |              } /* atime_needs_update */
 0) + 13.916 us   |            } /* touch_atime */
 0) + 60.722 us   |          } /* filemap_read */
 0) + 62.685 us   |        } /* generic_file_read_iter */
 0) + 66.002 us   |      } /* ext4_file_read_iter */
 0) + 79.758 us   |    } /* vfs_read */
 0) ! 108.020 us  |  } /* ksys_read */
```

整個 `read` 約 108 µs（TCG 模擬，比實機慢一到兩個數量級，數字只能拿來看相對比例）。這份輸出就是情境 B 的骨架。

### 實測輸出：情境 A（tmpfs）

```
#### /tmp/f
read returned 3, first bytes: hi

 1)               |  ksys_read() {
 1) + 13.365 us   |    fdget_pos();
 1)               |    vfs_read() {
 1)               |      rw_verify_area() {
 ...
 1) + 11.902 us   |      } /* rw_verify_area */
 1)               |      shmem_file_read_iter() {
 1)               |        shmem_get_folio_gfp.constprop.0() {
 1)               |          filemap_get_entry() {
 1)   1.172 us    |            __rcu_read_lock();
 1)   1.423 us    |            __rcu_read_lock();
 1)   1.272 us    |            __rcu_read_unlock();
 1)   5.841 us    |            irq_enter_rcu();
 1)   5.380 us    |            idle_cpu();
 1)   1.473 us    |            __rcu_read_unlock();
 1) ! 259.853 us  |          } /* filemap_get_entry */
 1)   1.353 us    |          __cond_resched();
 1) ! 276.734 us  |        } /* shmem_get_folio_gfp.constprop.0 */
 1)   3.246 us    |        folio_unlock();
 1)               |        folio_mark_accessed() {
 1)   3.246 us    |          __folio_batch_add_and_move();
 1)   4.107 us    |          workingset_activation();
 1) + 18.685 us   |        } /* folio_mark_accessed */
 1)   1.342 us    |        __cond_resched();
 1)               |        touch_atime() {
 ...
 1) + 79.236 us   |        } /* touch_atime */
 1) ! 413.356 us  |      } /* shmem_file_read_iter */
 1) ! 439.595 us  |    } /* vfs_read */
 1) ! 527.197 us  |  } /* ksys_read */
```

**沒有 `generic_file_read_iter`，也沒有 `filemap_read`**。如果你的靜態筆記照著「所有檔案系統都走 `generic_file_read_iter`」去寫 tmpfs，這裡就是第一個差異。`filemap_get_entry` 裡夾著一個 `irq_enter_rcu`：`options/funcgraph-irqs` 設為 0 只隱藏了 hardirq handler 本體，中斷的進出點仍然留下痕跡，所以 `filemap_get_entry` 的 259 µs 大部分不是它自己花的。

### 實測輸出：ramfs 對照組

```
 1)               |  ksys_read() {
 1)   4.298 us    |    fdget_pos();
 1)               |    vfs_read() {
 ...
 1)               |      generic_file_read_iter() {
 1)               |        filemap_read() {
 1)   0.871 us    |          __cond_resched();
 1)               |          filemap_get_pages() {
 1)               |            filemap_get_read_batch() {
 ...
```

ramfs 沒有自己的 `read_iter` 包裝，`vfs_read` 直接呼叫 `generic_file_read_iter`。和情境 B 相比少了 `ext4_file_read_iter` 那一層，其餘相同——這證明 ext4 在 buffered read 上的「專屬程式碼」只有那一層檢查。

### 實測輸出：情境 C（ext4 cold）的睡眠點

cold read 的 trace 有五百多行，大部分是 readahead 與 bio 提交。要看睡眠點，用另一份腳本打開 `funcgraph-irqs`（保持預設值 1）並啟用兩個事件：

```sh
echo ksys_read > $T/set_graph_function
echo function_graph > $T/current_tracer
echo 1 > $T/options/funcgraph-tail
echo 1 > $T/options/funcgraph-proc
echo 1 > $T/events/sched/sched_switch/enable
echo 1 > $T/events/block/block_rq_complete/enable
echo > $T/trace
/bin/readone /mnt/e/f
cat $T/trace
```

關鍵片段（刪掉 `_raw_spin_*` 與 `__rcu_read_*` 行）：

```
 1)   readone-75   | # 1029.912 us |                    } /* read_pages */
 1)   readone-75   |   1.223 us    |                    up_read();
 1)   readone-75   | # 5150.965 us |                  } /* page_cache_ra_unbounded */
 1)   readone-75   | # 5190.849 us |                } /* page_cache_ra_order */
 1)   readone-75   | # 5688.698 us |              } /* page_cache_sync_ra */
 1)   readone-75   |               |              filemap_get_read_batch() {
 1)   readone-75   | + 63.037 us   |              } /* filemap_get_read_batch */
 1)   readone-75   |               |              down_read() {
 1)   readone-75   |   0.952 us    |                __cond_resched();
 1)   readone-75   |   3.086 us    |              } /* down_read */
 1)   readone-75   |   1.011 us    |              up_read();
 1)   readone-75   |               |              io_schedule() {
 1)   readone-75   |               |                schedule() {
 ...
 1)   readone-75   |               |                  __traceiter_sched_switch() {
 1)   readone-75   |               |                    /* sched_switch: prev_comm=readone prev_pid=75 prev_prio=120 prev_state=D ==> next_comm=swapper/1 next_pid=0 next_prio=120 */
 1)   readone-75   | + 23.293 us   |                  } /* __traceiter_sched_switch */
 ------------------------------------------
 1)   readone-75   =>    <idle>-0   
 ------------------------------------------

 1)    <idle>-0    |               |                  /* block_rq_complete: 253,0 RA () 10456 + 8 be,0,4 [0] */
 1)    <idle>-0    |               |                  /* sched_switch: prev_comm=swapper/1 prev_pid=0 prev_prio=120 prev_state=R ==> next_comm=readone next_pid=75 next_prio=120 */
 ------------------------------------------
 1)    <idle>-0    =>   readone-75  
 ------------------------------------------

 1)   readone-75   |               |                  finish_task_switch.isra.0() {
 1)   readone-75   |   6.272 us    |                  } /* finish_task_switch.isra.0 */
 1)   readone-75   | ! 704.455 us  |                } /* schedule */
 1)   readone-75   | ! 707.040 us  |              } /* io_schedule */
 1)   readone-75   |   1.432 us    |              finish_wait();
 1)   readone-75   |               |              filemap_get_read_batch() {
 1)   readone-75   | + 52.168 us   |              } /* filemap_get_read_batch */
 1)   readone-75   | # 6742.876 us |            } /* filemap_get_pages */
```

逐行對照原始碼（答案在參考解答裡有完整推導，這裡先看形狀）：

- `page_cache_sync_ra` 結束 → 第二次 `filemap_get_read_batch`：readahead 已經把 folio 插進 xarray，這次拿得到，但 I/O 還沒完成，folio 不是 uptodate。
- `down_read` / `up_read`：`filemap_update_page`（被 inline，trace 裡看不到名字）裡的 `filemap_invalidate_lock_shared` / `unlock_shared`，鎖的是 `mapping->invalidate_lock`（`struct address_space` 的 `rw_semaphore`，`include/linux/fs.h:509（v6.18）`）。
- `io_schedule` → `sched_switch ... prev_state=D`：task 進入 `TASK_KILLABLE`（ps 顯示為 D）睡眠。
- `block_rq_complete: 253,0 RA () 10456 + 8`：virtio 磁碟完成了一個 readahead（`RA`）請求，8 個 sector（4 KB），記錄在 `<idle>` 身上——完成中斷打斷的是 idle task。
- 約 0.7 ms 後切回 `readone`，`finish_wait`，再查一次 batch（`AOP_TRUNCATED_PAGE` → `goto retry`），這次 folio uptodate，往下複製。

### 這份 trace 告訴我們的四件「靜態閱讀猜不到」的事

1. **`new_sync_read` 不見了**：`vfs_read` 直接呼叫 `ext4_file_read_iter`。`new_sync_read` 是 `static`，被 inline 進 `vfs_read`。同理 `filemap_update_page`、`folio_wait_bit_common`（後者原始碼就寫 `static inline`）、`copy_folio_to_iter`。
2. **`copy_page_to_iter` 也不見了，但不是因為 inline**：`nm ~/code_reading/build/vmlinux | grep ' copy_page_to_iter$'` 顯示 `T copy_page_to_iter`，符號存在。原因在 `lib/Makefile:6（v6.18）`：

   ```make
   ccflags-remove-$(CONFIG_FUNCTION_TRACER) += $(CC_FLAGS_FTRACE)
   ```

   整個 `lib/` 目錄編譯時拿掉了 ftrace 的 instrumentation，`lib/iov_iter.c` 裡的函式都追不到。**function_graph 沒出現 ≠ 沒被呼叫**。
3. **`funcgraph-irqs=0` 會讓睡眠子樹消失**：我們第一次跑情境 C 時關了 `funcgraph-irqs`，trace 在 `up_read` 之後直接跳到 `finish_wait`，`io_schedule` / `schedule` 那一段完全沒有出現。打開之後才看到。我們沒有深究 ftrace 內部的原因，只記錄現象：**要看睡眠，別關 `funcgraph-irqs`**。
4. **softirq 借用了 read 的 stack**：cold read 的 trace 裡，`read_pages` 底下出現了 `handle_softirqs() → run_timer_softirq()`，`bio_add_page` 底下出現了 `handle_softirqs() → rcu_core_si()`。這是 [Ch 32](./32-async-irq-softirq-workqueue.md) 講的「中斷退出時順手跑 softirq」，和 read 沒有呼叫關係。

## 完整參考解答

**寫完再看！** 先自己追、自己跑，否則學不到「斷點在哪、怎麼接回去」的那個過程。

<details>
<summary>點開參考 call path 筆記</summary>

### 一、共同前段（三種情境相同）

| 深度 | 函式 | 位置（v6.18） | 怎麼到這裡 | 資料結構 | 鎖／RCU | 可能睡？ |
|---|---|---|---|---|---|---|
| — | 系統呼叫號 0 | `arch/x86/entry/syscalls/syscall_64.tbl:12` | `0 common read sys_read` | — | — | — |
| 0 | `do_syscall_64` | `arch/x86/entry/syscall_64.c:87` | entry 組語 | `struct pt_regs` | 無 | 否（本身） |
| 1 | `do_syscall_x64`（inline） | `arch/x86/entry/syscall_64.c:53` | 直接 | `regs->ax` | 無 | 否 |
| 2 | `x64_sys_call` | `arch/x86/entry/syscall_64.c:35` | 第 63 行 `regs->ax = x64_sys_call(regs, unr)`；內部是由 tbl 生成的 switch | — | 無 | 否 |
| 3 | `__x64_sys_read` | `fs/read_write.c:722` | `SYSCALL_DEFINE3(read, ...)` 巨集生成（[Ch 29](./29-syscall-entry-paths.md)） | 從 `pt_regs` 取出 fd/buf/count | 無 | 否 |
| 4 | `ksys_read` | `fs/read_write.c:704` | 直接 | `CLASS(fd_pos, f)(fd)` → `struct fd`；`pos` 先複製到區域變數，成功後寫回 `fd_file(f)->f_pos` | `fdget_pos()` 可能拿 `file->f_pos_lock`（mutex） | **可能**（f_pos_lock） |
| 5 | `fdget_pos` | `fs/file.c:1225`；`DEFINE_CLASS(fd_pos, ...)` 在 `include/linux/file.h:85` | `CLASS()` 展開 | fd table → `struct file *` | 查 fd table 走 RCU | 可能（同上） |
| 5 | `vfs_read` | `fs/read_write.c:552` | 直接 | `file->f_mode`（`FMODE_READ`、`FMODE_CAN_READ`）、`access_ok(buf, count)` | 無 | 依下層 |
| 6 | `rw_verify_area` | `fs/read_write.c:451` | 直接 | 檢查 pos/count 溢位、`security_file_permission` | 無 | 否（一般情況） |
| 6 | `new_sync_read`（inline） | `fs/read_write.c:481` | `vfs_read` 第 572 行（沒有 `f_op->read` 時） | 在 stack 上建 `struct kiocb`（`init_sync_kiocb`）與 `struct iov_iter`（`iov_iter_ubuf(&iter, ITER_DEST, buf, len)`） | 無 | 依下層 |
| 7 | **`file->f_op->read_iter`** | `fs/read_write.c:491` | **函式指標**，見下表 | `kiocb.ki_pos`、`iov_iter` | 無 | 依實作 |

**間接呼叫一：`f_op->read_iter` 的解析**

```
$ rg -n '\.read_iter\s*=' fs/ext4/file.c mm/shmem.c fs/ramfs/file-mmu.c
fs/ext4/file.c:965:	.read_iter	= ext4_file_read_iter,
fs/ramfs/file-mmu.c:42:	.read_iter	= generic_file_read_iter,
mm/shmem.c:5213:	.read_iter	= shmem_file_read_iter,
$ rg -n 'i_fop = &ext4_file_operations|i_fop = &shmem_file_operations|i_fop = &ramfs_file_operations' fs/ext4/inode.c mm/shmem.c fs/ramfs/inode.c
fs/ramfs/inode.c:75:			inode->i_fop = &ramfs_file_operations;
mm/shmem.c:3089:		inode->i_fop = &shmem_file_operations;
fs/ext4/inode.c:5451:		inode->i_fop = &ext4_file_operations;
```

`open(2)` 時 VFS 把 `inode->i_fop` 複製成 `file->f_op`（[Ch 41](./41-layering-and-vfs.md) 會讀 `do_dentry_open`），所以一般檔案的 `f_op` 就是建 inode 時設的那個表。

### 二、情境 B：ext4，page cache 命中

| 深度 | 函式 | 位置（v6.18） | 怎麼到這裡 | 資料結構 | 鎖／RCU | 可能睡？ |
|---|---|---|---|---|---|---|
| 7 | `ext4_file_read_iter` | `fs/ext4/file.c:130` | `f_op->read_iter` | 檢查 `ext4_forced_shutdown`、`IS_DAX`、`IOCB_DIRECT` | 無 | 依下層 |
| 8 | `generic_file_read_iter` | `mm/filemap.c:2899` | 直接（buffered 路徑） | `iocb->ki_flags & IOCB_DIRECT` 為假，跳過 direct I/O 分支 | 無 | 依下層 |
| 9 | `filemap_read` | `mm/filemap.c:2711` | 第 2940 行 | `filp->f_ra`（readahead 狀態）、`mapping = filp->f_mapping`、`struct folio_batch fbatch`（stack 上） | 無 | **是**：迴圈開頭 `cond_resched()` |
| 10 | `filemap_get_pages` | `mm/filemap.c:2610` | 第 2748 行 | `index = ki_pos >> PAGE_SHIFT`、`last_index` | 無 | 可能（cold 時） |
| 11 | `filemap_get_read_batch` | `mm/filemap.c:2398` | 第 2628 行 | **`mapping->i_pages`（xarray）**：`XA_STATE`、`xas_load`、`xas_next`；`folio_try_get` 拿 reference 後 `xas_reload` 確認沒被換掉 | **`rcu_read_lock()`** | **否**（RCU read-side） |
| 10 | `folio_mark_accessed` | `mm/swap.c:455` | `filemap_read` 第 2777 行 | LRU 狀態 | 內部 per-CPU batch | 否 |
| 10 | `copy_folio_to_iter`（inline） | `include/linux/uio.h:201` | `filemap_read` 第 2799 行 | `folio->page` | 無 | **是**（見下） |
| 11 | `copy_page_to_iter` | `lib/iov_iter.c:349` | 直接；**ftrace 追不到**（`lib/Makefile:6`） | `kmap_local_page(page)` | 無 | **是**：複製到 user buffer 時可能 page fault |
| 12 | `_copy_to_iter` | `lib/iov_iter.c:179` | 直接 | 最終走到 `copy_to_user` 系列 | 無 | 同上 |
| 10 | `file_accessed` → `touch_atime` | `include/linux/fs.h:2670`、`fs/inode.c:2180` | `filemap_read` 結尾 | `inode` 的 atime | 可能拿 `mnt_get_write_access` | 可能（ext4 需要寫 journal 時） |

**為什麼 RCU 就夠**：`filemap_get_read_batch` 只讀 xarray，對每個 folio 用 `folio_try_get()` 拿一個 reference（refcount 為 0 表示正在被釋放，拿失敗就 `goto retry`），再用 `xas_reload()` 確認那個位置仍然是同一個 folio（[Ch 31](./31-reading-rcu-code.md) 的「RCU 下取 reference 再驗證」）。拿到 reference 之後就可以離開 RCU read-side，folio 不會被釋放。

**為什麼 trace 裡 `__rcu_read_lock` 出現兩次**：一次是 `filemap_get_read_batch` 自己的 `rcu_read_lock()`。另一次我們沒有逐一確認來源（可能是 `folio_try_get` 一路下去的某個 helper）；這種「多出一對」的現象不影響理解主路徑，可以標記後跳過——這就是 [Ch 4](./04-selective-reading.md) 說的接受只懂 80%。

**第二次讀為什麼沒有 `generic_update_time`**：映像檔用預設的 `relatime` 掛載（`/proc/mounts` 輸出 `rw,relatime`）。第一次讀已經把 atime 更新到比 mtime 新，第二次讀時 `atime_needs_update` 判斷不需要再寫，所以 warm 讀取的 `touch_atime` 只有檢查、沒有 `__mark_inode_dirty`。cold 讀取那次則走了完整的 `ext4_dirty_inode` → jbd2 journal 路徑。

### 三、情境 A：tmpfs

| 深度 | 函式 | 位置（v6.18） | 怎麼到這裡 | 資料結構 | 鎖／RCU | 可能睡？ |
|---|---|---|---|---|---|---|
| 7 | `shmem_file_read_iter` | `mm/shmem.c:3330` | `f_op->read_iter` | **自己的 `for (;;)` 迴圈**，不經過 `filemap_read` | 無 | 是：迴圈尾 `cond_resched()` |
| 8 | `shmem_get_folio` | `mm/shmem.c:2638` | 第 3352 行，`SGP_READ` | — | 無 | 可能（swap in） |
| 9 | `shmem_get_folio_gfp` | `mm/shmem.c:2435` | 直接（trace 中為 `.constprop.0` 版本，見 [Ch 50](./50-compiler-as-reading-tool.md)） | — | 回傳時 folio **已上鎖** | 可能 |
| 10 | `filemap_get_entry` | `mm/filemap.c:1894` | `mm/shmem.c:2459` | `mapping->i_pages` xarray；`xa_is_value()` 判斷 swap entry | `rcu_read_lock()` | 否 |
| 8 | `folio_unlock` | — | `shmem_file_read_iter` 拿到 folio 後立刻解鎖 | `PG_locked` | — | 否 |
| 8 | `copy_folio_to_iter` → `copy_page_to_iter` | `mm/shmem.c:3414` | 直接 | 同情境 B | 無 | 是（user page fault） |
| 8 | `file_accessed` → `touch_atime` | 迴圈結束後 | 直接 | — | — | 可能 |

**為什麼 tmpfs 不走 `filemap_read`**：tmpfs 的資料**只存在** page cache 裡（或被換到 swap），沒有後端儲存可以 readahead。`filemap_read` 的大半邏輯（readahead、`read_folio`、uptodate 檢查）對它沒有意義；反而 tmpfs 需要處理 hole（沒寫過的位置要讀出 0，`ZERO_PAGE(0)`）與 swap entry（`xa_is_value`），所以它自己寫了一個迴圈。**檔案系統可以完全不用 generic 路徑**，這是追 `->read_iter` 時一定要實際打開實作的原因。

### 四、情境 C：ext4 cold，睡眠與喚醒

| 深度 | 函式 | 位置（v6.18） | 怎麼到這裡 | 資料結構 | 鎖／RCU | 可能睡？ |
|---|---|---|---|---|---|---|
| 11 | `filemap_get_read_batch` | `mm/filemap.c:2398` | 第一次：空 | xarray 沒有這個 index | RCU | 否 |
| 11 | `page_cache_sync_ra` | `mm/readahead.c:554` | `filemap_get_pages` 第 2638 行 | `DEFINE_READAHEAD(ractl, ...)`、`filp->f_ra` | 無 | 是 |
| 12 | `page_cache_ra_order` | `mm/readahead.c:464` | 直接 | — | — | 是 |
| 13 | `page_cache_ra_unbounded` | `mm/readahead.c:210` | 直接 | 配置 folio（`filemap_alloc_folio`）、`filemap_add_folio` 插進 xarray（folio **以上鎖狀態**插入、非 uptodate） | `filemap_invalidate_lock_shared`（trace 中的 `down_read`） | **是**：配置記憶體、拿 rwsem |
| 14 | `read_pages` | `mm/readahead.c:149` | 直接 | `blk_start_plug` / `blk_finish_plug` 把 bio 批次送出 | — | 是 |
| 15 | **`aops->readahead(rac)`** | `mm/readahead.c:163` | **函式指標**：`fs/ext4/inode.c:3978` `.readahead = ext4_readahead` | `struct readahead_control` | — | 是 |
| 16 | `ext4_readahead` → `ext4_mpage_readpages` | `fs/ext4/inode.c:3398`、`fs/ext4/readpage.c:211` | 直接 | `ext4_map_blocks` 查 extent（trace 中可見 `ext4_es_lookup_extent`、`ext4_find_extent`）；建 bio，**`bio->bi_end_io = mpage_end_io`**（`fs/ext4/readpage.c:365`） | `ext4_map_blocks` 內的 `down_read(&EXT4_I(inode)->i_data_sem)`（`fs/ext4/inode.c:772` 一帶；trace 中 `ext4_map_blocks` 底下的 `down_read`） | 是 |
| 17 | `submit_bio` | `fs/ext4/readpage.c:351/377` 呼叫 | 直接；之後 block layer 當黑箱 | — | — | 是（可能） |
| 11 | `filemap_get_read_batch` | — | 第二次：拿到 folio，但 `!folio_test_uptodate` 就 break | — | RCU | 否 |
| 11 | `filemap_update_page`（inline） | `mm/filemap.c:2487` | `filemap_get_pages` 第 2661 行 | 先 `filemap_invalidate_lock_shared`；`folio_trylock` 失敗（folio 仍被 I/O 鎖著）；放掉 invalidate lock | `invalidate_lock`（trace 中的 `down_read`/`up_read`） | 是 |
| 12 | `folio_put_wait_locked` | `mm/filemap.c:1494` | `filemap_update_page` 第 2510 行，`TASK_KILLABLE` | — | — | **是** |
| 13 | `folio_wait_bit_common`（inline） | `mm/filemap.c:1251` | 直接 | **hashed wait queue** `folio_waitqueue(folio)`，自訂 `wake_page_function`（[Ch 33](./33-wait-queues-completion.md)） | 在 `q->lock` 下上名單 | **是** |
| 14 | `io_schedule` | `mm/filemap.c:1330` 呼叫 | 直接 | `sched_switch prev_state=D` | — | **睡眠點** |
| — | 回到 `filemap_get_pages` | — | 回傳 `AOP_TRUNCATED_PAGE` → `goto retry` → 第三次查 batch，這次 uptodate | — | — | — |

**叫醒鏈（間接呼叫三：`bio->bi_end_io`）**：

```
virtio-blk 完成中斷（hardirq）
  └─ block layer 完成處理（可能在 hardirq 或 BLOCK_SOFTIRQ，依 queue 設定）
       └─ bio_endio(bio)
            └─ bio->bi_end_io(bio)  ==  mpage_end_io      fs/ext4/readpage.c:162
                 ├─ 需要解密/verity → INIT_WORK + queue 到 workqueue（fs/ext4/readpage.c:116-143）
                 └─ 否則 __read_end_io(bio)               fs/ext4/readpage.c:69
                      └─ folio_end_read(folio, success)   mm/filemap.c:1531
                           清 PG_locked、設 PG_uptodate；有 waiter → folio_wake_bit
                                └─ __wake_up_locked_key → wake_page_function → try_to_wake_up(readone)
```

叫醒者的 context 是中斷完成路徑，不能睡；這正是為什麼 fscrypt 解密必須另外丟 workqueue（[Ch 32](./32-async-irq-softirq-workqueue.md) 動手練習 1）。我們的 trace 只記錄了 `block_rq_complete` 事件，沒有追到 `mpage_end_io` 本身（function_graph 的 pid filter 擋掉了 idle task 上的呼叫）；要親眼看，可以另跑一次 `function` tracer、`set_ftrace_filter` 設 `mpage_end_io folio_end_read`、不設 pid filter，看它的旗標欄。**這一步本練習未實測**，以上叫醒鏈由原始碼推導。

### 五、差異報告（參考）

| 現象 | 原因 | 怎麼確認 |
|---|---|---|
| trace 沒有 `new_sync_read` | static 函式被 inline | `nm vmlinux \| grep new_sync_read` 無輸出 |
| trace 沒有 `filemap_update_page`、`folio_wait_bit_common` | inline（後者原始碼標 `static inline`） | 同上 |
| trace 沒有 `copy_page_to_iter` | `lib/` 移除了 ftrace 旗標 | `nm` 有符號；`lib/Makefile:6` |
| tmpfs 沒有 `generic_file_read_iter` | `shmem_file_read_iter` 自己實作迴圈 | 讀 `mm/shmem.c:3330` |
| `hrtimer_interrupt`、`handle_softirqs` 出現在 read 底下 | 中斷借用 task stack，時間巢狀而非呼叫 | 旗標欄、[Ch 32](./32-async-irq-softirq-workqueue.md) |
| `shmem_get_folio_gfp.constprop.0` | GCC 常數傳播產生的特化版本 | [Ch 50](./50-compiler-as-reading-tool.md) |
| `funcgraph-irqs=0` 時看不到 `io_schedule` | ftrace 選項的副作用，內部原因未深究 | 打開後重跑即出現 |
| warm 讀取沒有 `generic_update_time` | `relatime` | `/proc/mounts` 顯示 `relatime` |

</details>

**解答說明**：這份筆記刻意在三個地方停下來「當黑箱」：SELinux、block layer 內部、scheduler 內部。它們各自都是一整個子系統，對「`read` 怎麼拿到資料」這個問題不是關鍵。判斷依據是 [Ch 3](./03-question-driven-reading.md) 的提問驅動：每深入一層之前，先問「這一層的細節會改變我對主問題的答案嗎？」

## 測試用例

把 readone.c 改一改，預測 trace 會怎麼變，再實際跑：

| 輸入 | 預期行為 | 說明 |
|---|---|---|
| 情境 B，`read(fd, buf, 0)` | `generic_file_read_iter` 開頭 `if (!count) return 0;` 立刻返回，**沒有** `touch_atime` | 註解寫 `/* skip atime */`；ext4 層也有同樣的檢查 |
| 讀超過 EOF（先 `lseek` 到檔尾） | `filemap_read` 在 `ki_pos >= i_size_read(inode)` 時 break，回傳 0 | 不碰 xarray |
| `open` 時加 `O_DIRECT`（ext4） | `ext4_file_read_iter` 走 `ext4_dio_read_iter`，不經過 page cache | buffer 要對齊 block size，否則 `-EINVAL` |
| `O_NONBLOCK` 讀一般檔案 | 和情境 B 相同 | 一般檔案的 buffered read 不看 `O_NONBLOCK`；要非阻塞讀 page cache 要用 `preadv2(..., RWF_NOWAIT)`，對應 `IOCB_NOWAIT` |
| 情境 C 時對 task 送 `SIGKILL` | `folio_wait_bit_common` 因 `TASK_KILLABLE` 醒來，回傳 `-EINTR` 一路往上 | `SIGINT` 不會叫醒它（不是 fatal signal） |
| 讀 `d/big`（64 KB）的前 4 KB，cold | readahead 會一次讀進多個 page（`last_index - index` 只是下限，`ra` 狀態可能擴大） | 用 `block_rq_complete` 看 sector 數 |
| 對 FIFO 或 pipe 讀 | `f_op` 是 pipe 的表（匿名 pipe 為 `pipeanon_fops`），走 `anon_pipe_read`，完全不碰 page cache | [Ch 33](./33-wait-queues-completion.md) 的路徑 |

## 延伸挑戰（加分）

- **mmap 對照**：把 `read` 換成 `mmap` + 讀記憶體，追 page fault 路徑 `filemap_fault`（`mm/filemap.c`）。比較它和 `filemap_read` 共用了哪些函式（提示：`page_cache_sync_ra` 在 `mm/filemap.c:3288` 也被呼叫）。
- **追到 `mpage_end_io`**：照參考解答最後的說明，不設 pid filter 跑 `function` tracer，確認 `mpage_end_io` 與 `folio_end_read` 的 context 旗標，把本練習標「未實測」的那一段補上真實輸出。
- **gdb 單步**：用 `QEMU_EXTRA='-s -S'` 啟動 guest，在 host 用 gdb 對 `filemap_get_read_batch` 下斷點，印出 `mapping->i_pages` 的 xarray 結構（[Ch 52](./52-qemu-gdb-kernel.md)）。
- **另一個檔案系統**：在 host 上用 `mkfs.vfat` 或 `mkfs.ext2` 做映像檔重做一次，找出它們 `read_iter` 的實作與 ext4 的差異（提示：ext2 也直接用 `generic_file_read_iter`，但有自己的 DAX 分支）。

## 自我檢核

- [ ] 不看筆記，畫出情境 B 從 syscall 到 `copy_page_to_iter` 的路徑，標出三個「間接呼叫或巨集」的位置。
- [ ] 解釋 `filemap_get_read_batch` 為什麼用 RCU 就夠、不必拿任何 lock，以及 `xas_reload` 那一行防的是什麼。
- [ ] cold read 時 task 睡在哪個 wait queue 上？那個 wait queue 為什麼不在 folio 結構裡？
- [ ] 同事給你一份 function_graph 輸出，說「`copy_page_to_iter` 沒被呼叫，所以資料不是從 page cache 複製的」。你怎麼反駁？
- [ ] tmpfs 和 ext4 的讀取路徑在哪一層分岔？說出 tmpfs 選擇自己實作迴圈的兩個理由。
- [ ] 能說出自己的筆記和參考解答的差異，並解釋各自取捨（例如你在哪裡選擇當黑箱）。

Part 5 到此結束。你現在能追著控制流穿過 ops table、lock、RCU、中斷和 wait queue。下一個 Part 換一個角度：程式碼「為什麼」長這樣——從原始碼樹的地圖開始，到 Kconfig、git 考古與 mailing list。

→ [Ch 35 kernel 原始碼樹地圖與子系統邊界](./35-kernel-source-tree-map.md)
