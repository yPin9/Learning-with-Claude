# Ch 39 — 文件與風格：Documentation、kernel-doc、coding style

> **目標**：知道 kernel 的文件放在哪、長什麼樣、怎麼從原始碼抽出來；會把 kernel-doc 的 `Context:`／`Return:` 當成呼叫契約來讀；會用 coding style 預測程式碼的形狀；最重要的是，知道文件會過時、會說謊，並且有方法抓出來。
>
> **鴻溝**：本章主要練**領域鴻溝**（文件是補背景最便宜的來源）與**脈絡鴻溝**（風格規則解釋了「為什麼寫成這樣」），順帶處理一部分**慣用法鴻溝**。

> **環境**：Linux kernel v6.18（`~/code_reading/linux`），WSL Ubuntu 22.04，Python 3.10.12，`scripts/kernel-doc`（v6.18 是 Python 版）。

## 為什麼需要這個？

Spinellis 原書把「風格慣例」（Ch 7）和「文件」（Ch 8）分成兩章，但對讀碼的人來說它們是同一件事的兩面：**程式碼之外、作者留給讀者的線索**。風格是隱性的線索（命名、縮排、函式長度告訴你作者怎麼切分問題），文件是顯性的線索（明說這個函式在什麼 context 能呼叫、回傳什麼）。

很多人讀 kernel 的方式是：打開 `.c` 檔，從第一行往下讀，碰到看不懂就 Google。這樣做會漏掉三個免費的資訊來源：

1. **函式頭上的 `/** ... */` 註解**——kernel-doc 格式，裡面的 `Context:` 欄位直接告訴你能不能睡、要持哪把 lock。讀不懂函式本體之前先讀這段，常常就能回答你的問題。
2. **`Documentation/` 底下的 3800 多份 `.rst`**——很多子系統的維護者在這裡寫了設計說明，比程式碼本身好讀得多。
3. **coding style 文件**——它不是給你寫 code 用的而已。知道「kernel 的函式該短、label 該說它做什麼、`foo_bar()` 這種動詞名稱回傳 -errno」，你就能在讀之前先預測程式碼的形狀，讀的時候只確認例外。

但這一章同時要你保持警覺：**文件是作者某個時間點的意圖，程式碼才是現在的事實**。兩者不一致時，信程式碼。本章後半會給你兩個在 v6.18 中真實存在、親手驗證過的「註解說謊」例子，以及一支偵測腳本。

## 先建立直覺

把一個子系統想成一座城市：

```
            文件（地圖）                       程式碼（地形）
   ┌──────────────────────────┐        ┌──────────────────────────┐
   │ Documentation/*.rst      │        │ fs/、mm/、kernel/ ...    │
   │   設計意圖、名詞、架構    │──指向──▶│   真正會執行的東西        │
   │ kernel-doc 註解           │        │                          │
   │   每個函式的呼叫契約      │◀─生成──│   /** ... */ 在函式頭上   │
   │ coding-style.rst          │        │                          │
   │   預測「路」大概怎麼鋪    │──約束──▶│   縮排、命名、goto 收尾   │
   └──────────────────────────┘        └──────────────────────────┘
          畫地圖的時間點：過去               地形：v6.18 當下
```

地圖比地形好讀，所以先看地圖；但地圖是某年畫的，路可能已經改了。讀碼高手的習慣是：**用地圖決定往哪走，用地形確認自己真的到了**。

> 如果你對「帶問題讀」還不熟，先回看 [Ch 3 — 帶著問題讀](./03-question-driven-reading.md)。文件是回答問題的工具，不是要你從頭讀完的書。

## Documentation/ 的組織：v6.18 實際長什麼樣

先看實際的目錄，不要靠印象：

```console
$ cd ~/code_reading/linux
$ ls Documentation | wc -l
87
$ find Documentation -name '*.rst' | wc -l
3829
$ find Documentation -name '*.txt' | wc -l
899
```

87 個頂層項目、3829 份 reStructuredText、899 份還沒轉換的純文字檔。頂層目錄名稱多半就是子系統名（`filesystems/`、`mm/`、`networking/`、`scheduler/`），但有幾個「跨子系統」的目錄最值得認得：

| 目錄 | `.rst` 數量（v6.18） | 回答的問題 | 讀碼時什麼時候打開 |
|---|---|---|---|
| `process/` | 52 | 怎麼送 patch、coding style、行為規範 | 讀不懂某個寫法是不是「規定」 |
| `core-api/` | 71 | kernel 內部共用 API（xarray、maple tree、workqueue、refcount） | 碰到通用資料結構或基礎設施 |
| `driver-api/` | 348 | 給 driver 作者用的 API | 讀 driver 時 |
| `filesystems/` | 141 | VFS 與各檔案系統 | 讀 `fs/` 時 |
| `userspace-api/` | 452 | kernel 對 user space 的承諾（syscall、ioctl 語意） | 想知道某個行為「是不是 ABI」 |
| `admin-guide/` | 391 | 給系統管理者的說明（sysctl、開機參數） | 想知道某個 knob 的用途 |
| `dev-tools/` | 53 | kselftest、KUnit、KASAN、sparse、gdb | Ch 40、Part 8 會一直用到 |
| `doc-guide/` | 7 | 怎麼寫 kernel 文件（包含 kernel-doc 語法） | 本章 |
| `ABI/` | — | 分 `stable/`、`testing/`、`obsolete/`、`removed/` 的 sysfs 等介面規格 | 想知道某個 sysfs 檔的承諾等級 |

（數字是 `find Documentation/<dir> -name '*.rst' | wc -l` 的結果。）

`Documentation/index.rst` 把這些目錄分成幾大區：「Working with the development community」「Internal API manuals」「Development tools and processes」「User-oriented documentation」「Firmware-related documentation」「Architecture-specific documentation」「Other documentation」「Translations」。這個分法本身就是一張地圖：**你是要用 kernel（user-oriented）、在 kernel 裡寫 code（internal API）、還是跟社群打交道（process）**。

還有幾個「歷史遺跡」值得認得，因為很多舊文章和舊註解會指向它們：

```console
$ cat Documentation/CodingStyle
This file has moved to process/coding-style.rst
```

`Documentation/CodingStyle`、`Documentation/Changes`、`Documentation/SubmittingPatches` 這些大寫檔名是舊位置，留著當路標。

### docs.kernel.org 和原始碼的對應

[docs.kernel.org](https://docs.kernel.org/) 是用 Sphinx 把 `Documentation/` 建出來的網站。對應規則很規律：`Documentation/core-api/xarray.rst` → `https://docs.kernel.org/core-api/xarray.html`。所以當你在網站上看到一頁，可以直接在 tree 裡找到原始檔，用 `git log` 看它最後一次更新是什麼時候——這是判斷文件是否過時的第一步。

網站上的「API 參考」部分不是手寫的，而是從 `.c`／`.h` 裡的 kernel-doc 註解抽出來的。`.rst` 裡用 `kernel-doc::` 指令把原始碼拉進來：

```console
$ grep -rn 'kernel-doc:: fs/eventfd.c\|kernel-doc:: fs/pipe.c\|kernel-doc:: lib/xarray.c\|kernel-doc:: mm/filemap.c' Documentation
Documentation/core-api/xarray.rst:510:.. kernel-doc:: lib/xarray.c
Documentation/core-api/mm-api.rst:61:.. kernel-doc:: mm/filemap.c
Documentation/filesystems/splice.rst:22:.. kernel-doc:: fs/pipe.c
Documentation/filesystems/api-summary.rst:119:.. kernel-doc:: fs/eventfd.c
```

這個 grep 反過來也很有用：**想知道某個 `.c` 檔的函式文件會出現在網站哪一頁，grep `kernel-doc:: <檔名>`**。

## kernel-doc 註解：函式的呼叫契約

kernel-doc 的語法規範在 `Documentation/doc-guide/kernel-doc.rst`。辨識方式是「以 `/**` 開頭的註解」——兩個星號，不是一個。先看一個完整的真實例子：

```c
/**
 * xa_erase() - Erase this entry from the XArray.
 * @xa: XArray.
 * @index: Index of entry.
 *
 * After this function returns, loading from @index will return %NULL.
 * If the index is part of a multi-index entry, all indices will be erased
 * and none of the entries will be part of a multi-index entry.
 *
 * Context: Any context.  Takes and releases the xa_lock.
 * Return: The entry which used to be at this index.
 */
void *xa_erase(struct xarray *xa, unsigned long index)
{
	void *entry;

	xa_lock(xa);
	entry = __xa_erase(xa, index);
	xa_unlock(xa);

	return entry;
}
EXPORT_SYMBOL(xa_erase);
```
（`lib/xarray.c:1653-1675`（v6.18））

逐欄拆：

| 欄位 | 寫法 | 讀碼時告訴你什麼 |
|---|---|---|
| 第一行 | `name() - 一句話摘要` | 函式做什麼。括號可有可無（`xa_erase()` 和 `folio_unlock` 兩種都有人寫） |
| `@param:` | 每個參數一行 | 參數的意義。注意 `@` 在描述文字裡也會出現（`@index`），代表「引用參數」 |
| 描述段 | 自由文字 | 語意細節、邊界情況（這裡講 multi-index entry 的行為） |
| `Context:` | 能在哪種 context 呼叫、拿什麼 lock | **最重要的一欄**，見下節 |
| `Return:` | 回傳值的意義 | 錯誤碼、NULL 的含義 |
| `%NULL`、`&struct foo` | 特殊標記 | `%` 表示常數，`&` 表示 struct 參照，生成文件時會變成連結或等寬字 |

### `Context:` 是寫給讀者的限制條件

`kernel-doc.rst` 對 `Context` 的要求是：說明函式會不會睡、能不能在中斷 context 呼叫、它拿哪些 lock、期待呼叫者持有哪些 lock。文件給的範例格式：

```
  * Context: Any context.
  * Context: Any context. Takes and releases the RCU lock.
  * Context: Any context. Expects <lock> to be held by caller.
  * Context: Process context. May sleep if @gfp flags permit.
  * Context: Process context. Takes and releases <mutex>.
  * Context: Softirq or process context. Takes and releases <lock>, BH-safe.
  * Context: Interrupt context.
```

v6.18 裡有多少函式真的寫了？

```console
$ rg -c ' \* Context:' --type c . | awk -F: '{s+=$2} END{print s}'
1326
```

1326 處。對一個幾萬個函式的 tree 來說不算多——**沒寫 `Context:` 不代表沒有限制**，只代表作者沒寫。但有寫的地方，它是你讀鎖相關程式碼時最可靠的捷徑。

看 xarray 的一對函式，就能理解 `Context:` 怎麼幫你讀碼：

```console
$ rg -n 'Context:' lib/xarray.c | sed -n '8,11p'
lib/xarray.c:1643: * Context: Any context.  Expects xa_lock to be held on entry.
lib/xarray.c:1662: * Context: Any context.  Takes and releases the xa_lock.
lib/xarray.c:1688: * Context: Any context.  Expects xa_lock to be held on entry.  May
lib/xarray.c:1723: * Context: Any context.  Takes and releases the xa_lock.
```

1643 行是 `__xa_erase()`，1662 行是 `xa_erase()`。一個「Expects xa_lock to be held」、一個「Takes and releases the xa_lock」。這揭露了 kernel 一個很常見的命名慣例：**`__foo()` 是「呼叫者自己負責 lock」的版本，`foo()` 是「我幫你拿 lock」的包裝**。上面的 `xa_erase()` 本體正是 `xa_lock(); __xa_erase(); xa_unlock();`。這個慣例不在 coding-style 文件裡，是讀多了才看得出來的 pattern；`Context:` 欄位幫你確認它。

> 這和 [Ch 30 — 讀有 lock 的程式碼](./30-reading-locking-code.md) 的「lock 規則寫在哪」直接相關：`Context:` 和 lockdep 標註（`lockdep_assert_held()`）是兩個互相印證的來源。

另一個例子，`folio_unlock()`：

```c
/**
 * folio_unlock - Unlock a locked folio.
 * @folio: The folio.
 *
 * Unlocks the folio and wakes up any thread sleeping on the page lock.
 *
 * Context: May be called from interrupt or process context.  May not be
 * called from NMI context.
 */
void folio_unlock(struct folio *folio)
{
	/* Bit 7 allows x86 to check the byte's sign bit */
	BUILD_BUG_ON(PG_waiters != 7);
	BUILD_BUG_ON(PG_locked > 7);
	VM_BUG_ON_FOLIO(!folio_test_locked(folio), folio);
	if (folio_xor_flags_has_waiters(folio, 1 << PG_locked))
		folio_wake_bit(folio, PG_locked);
}
EXPORT_SYMBOL(folio_unlock);
```
（`mm/filemap.c:1497-1515`（v6.18））

「May not be called from NMI context」這句話，從函式本體幾乎看不出來（要追進 `folio_wake_bit()` 看它拿 waitqueue 的 spinlock，才知道 NMI 裡不能這樣做）。文件把這個需要追三層才得到的結論直接給你。

順帶注意本體裡那行註解 `/* Bit 7 allows x86 to check the byte's sign bit */`——這是 coding style 說的「函式內只在特別聰明或特別醜的地方留短註解」的標準例子：它不解釋 HOW，它解釋 **為什麼 PG_waiters 一定要是第 7 位**。

### 不是每個人都照格式寫

`fs/eventfd.c` 的 kernel-doc 用了自己的變體：

```c
/**
 * eventfd_ctx_remove_wait_queue - Read the current counter and removes wait queue.
 * @ctx: [in] Pointer to eventfd context.
 * @wait: [in] Wait queue to be removed.
 * @cnt: [out] Pointer to the 64-bit counter value.
 *
 * Returns %0 if successful, or the following error codes:
 *
 * -EAGAIN      : The operation would have blocked.
 *
 * This is used to atomically remove a wait queue entry from the eventfd wait
 * queue head, and read/reset the counter value.
 */
```
（`fs/eventfd.c:185-197`（v6.18））

`[in]`／`[out]` 是作者自加的標記（kernel-doc 不認得，會原樣輸出），回傳值寫成 `Returns ...` 而不是 `Return:` 段落。下一節你會看到工具怎麼抓這種偏差。

## 實際跑 scripts/kernel-doc

`scripts/kernel-doc` 是把註解抽成文件的工具。先確認你手上是哪個版本：

```console
$ ls -la scripts/kernel-doc
lrwxrwxrwx 1 ypp ypp 13 Nov 21 11:31 scripts/kernel-doc -> kernel-doc.py
$ git log --oneline -1 -- scripts/kernel-doc
fc862949a35a scripts/kernel-doc: switch to use kernel-doc.py
$ git tag --contains fc862949a35a | sort -V | head -1
v6.16
```

**版本注意**：kernel-doc 原本是一支 Perl 腳本（歷史可追到 1998 年），v6.16 起 `scripts/kernel-doc` 這個 symlink 改指向 Python 重寫版 `kernel-doc.py`，Perl 版改名為 `kernel-doc.pl` 保留。根據 6.19 的 Documentation pull request，之後 Perl 版會被移除、Python 版會搬到 `tools/docs/`（這是 v6.18 之後的事，本課不涵蓋，請以你手上的 tree 為準）。舊教學裡的 `scripts/kernel-doc -rst`、`-man` 等選項在 Python 版仍然相容。

### 例一：抽出一個檔案的 reST

```console
$ ./scripts/kernel-doc -rst fs/eventfd.c | head -20
.. c:function:: void eventfd_signal_mask (struct eventfd_ctx *ctx, __poll_t mask)

   Increment the event counter

.. container:: kernelindent

  **Parameters**

  ``struct eventfd_ctx *ctx``
    [in] Pointer to the eventfd context.

  ``__poll_t mask``
    [in] poll mask

  **Description**

  This function is supposed to be called by the kernel in paths that do not
  allow sleeping. In this function we allow the counter to reach the ULLONG_MAX
  value, and we signal this as overflow condition by returning a EPOLLERR
  to poll(2).
```

注意 `[in]` 被原樣保留了。這就是 docs.kernel.org 上 `filesystems/api-summary` 那頁 eventfd 部分的原料。

### 例二：只抽一個符號，看 `Context` 和 `Return` 被轉成什麼

```console
$ ./scripts/kernel-doc -rst -s xa_erase lib/xarray.c
.. c:function:: void * xa_erase (struct xarray *xa, unsigned long index)

   Erase this entry from the XArray.

.. container:: kernelindent

  **Parameters**

  ``struct xarray *xa``
    XArray.

  ``unsigned long index``
    Index of entry.

  **Description**

  After this function returns, loading from **index** will return ``NULL``.
  If the index is part of a multi-index entry, all indices will be erased
  and none of the entries will be part of a multi-index entry.

  **Context**

  Any context.  Takes and releases the xa_lock.

  **Return**

  The entry which used to be at this index.
```

`@index` 變成粗體、`%NULL` 變成等寬的 ``NULL``，`Context:`、`Return:` 變成獨立小節。這就是為什麼格式要照規範寫：**工具認得的欄位，才會在網站上變成可掃視的結構**。

### 例三：產生 man page

```console
$ ./scripts/kernel-doc -man -s folio_unlock mm/filemap.c
.TH "folio_unlock" 9 "folio_unlock" "November 2026" "Kernel Hacker's Manual" LINUX
.SH NAME
folio_unlock \- Unlock a locked folio.
.SH SYNOPSIS
.B "void" folio_unlock
.BI "(struct folio *folio "  ");"
.SH ARGUMENTS
.IP "folio" 12
The folio.
.SH "DESCRIPTION"
Unlocks the folio and wakes up any thread sleeping on the page lock.
.SH "CONTEXT"
May be called from interrupt or process context.  May not be
called from NMI context.
```

section 9 是 man page 傳統上留給 kernel 內部介面的章節。你可以 `| man -l -` 直接在終端機看。

### 例四：讓工具幫你挑毛病

`-none -Wall` 不輸出文件，只回報問題：

```console
$ ./scripts/kernel-doc -none -Wall fs/eventfd.c
Warning: fs/eventfd.c:199 No description found for return value of 'eventfd_ctx_remove_wait_queue'
Warning: fs/eventfd.c:323 No description found for return value of 'eventfd_fget'
Warning: fs/eventfd.c:348 No description found for return value of 'eventfd_ctx_fdget'
Warning: fs/eventfd.c:366 No description found for return value of 'eventfd_ctx_fileget'
```

上一節看到 `eventfd_ctx_remove_wait_queue` 明明有寫回傳值——但寫成 `Returns ...` 散文，不是 `Return:` 段落，工具就認為沒寫。

故意寫一個參數名對不上的 kernel-doc，看它怎麼反應（`~/code_reading/scratch/39/demo.c`）：

```c
/**
 * ring_push - Push one item into the ring.
 * @r: The ring.
 * @itm: Item to push.
 *
 * Context: Any context. Takes and releases r->lock.
 */
int ring_push(struct ring *r, void *item)
{
	return 0;
}
```

```console
$ ~/code_reading/linux/scripts/kernel-doc -none -Wall demo.c
Warning: demo.c:8 function parameter 'item' not described in 'ring_push'
Warning: demo.c:8 No description found for return value of 'ring_push'
exit=0
```

兩個觀察：(1) 它抓到 `item` 沒描述；(2) 在這個版本、這組選項下，它**沒有**對多出來的 `@itm` 報「excess parameter」，而且 exit code 是 0（要 `-Werror` 才會失敗）。所以 kernel-doc 能抓「漏寫」，不保證抓「寫錯」。它檢查的是格式，不是內容是否正確——這是下面「文件說謊」一節的伏筆。

### 小技巧：用 `-export` 看一個檔案對外的 API 面

```console
$ ./scripts/kernel-doc -rst -export fs/eventfd.c 2>/dev/null | grep 'c:function'
.. c:function:: void eventfd_signal_mask (struct eventfd_ctx *ctx, __poll_t mask)
.. c:function:: void eventfd_ctx_put (struct eventfd_ctx *ctx)
.. c:function:: int eventfd_ctx_remove_wait_queue (struct eventfd_ctx *ctx, wait_queue_entry_t *wait, __u64 *cnt)
.. c:function:: struct file * eventfd_fget (int fd)
.. c:function:: struct eventfd_ctx * eventfd_ctx_fdget (int fd)
.. c:function:: struct eventfd_ctx * eventfd_ctx_fileget (struct file *file)
```

`-export` 只列有 `EXPORT_SYMBOL` 的函式的文件，`-internal` 只列沒 export 的。對一個陌生檔案，`-export` 的輸出就是「其他子系統會怎麼用它」的清單，比 `grep EXPORT_SYMBOL` 多了說明文字。

## coding style：用規則預測程式碼

`Documentation/process/coding-style.rst`（v6.18 共 1290 行、22 節）通常被當成「寫 patch 的規定」。換個角度讀：**每一條規則都是一個預測**。知道規則，你就知道一般情況長什麼樣，讀的時候注意力可以留給違反規則的地方——那裡通常有故事。

| 規則（節號） | 原文要點 | 讀碼時的預測 |
|---|---|---|
| 1) Indentation | 「Tabs are 8 characters」；超過 3 層縮排「you're screwed anyway」 | 看到 4、5 層巢狀，要嘛是舊碼，要嘛作者有不得已的理由；先找它為什麼沒拆 |
| 2) 行長 | 偏好 80 欄，但可讀性優先 | 長字串不切行是為了能 grep 到錯誤訊息 |
| 4) Naming | 全域名稱要描述性（`count_active_users()`，不是 `cntusr()`）；區域變數短（`i`、`tmp`）；反對匈牙利命名 | 長名字 = 跨檔案可見；短名字 = 只在這個函式活 |
| 6) Functions | 一兩個螢幕；區域變數「shouldn't exceed 5-10」 | 長函式通常是一個大 `switch`（規則允許），或者是待重構的地方 |
| 7) Centralized exiting | goto 用來集中收尾；label 要說它做什麼，`out_free_buffer:` 好、`err1:` 差 | 讀 label 名就知道每條錯誤路徑釋放了什麼（[Ch 17](./17-kernel-error-conventions.md)） |
| 8) Commenting | 「tell WHAT your code does, not HOW」；註解放函式頭，說它做什麼、**可能還有為什麼** | 函式內的註解少而重要；出現的地方通常在解釋一個不直觀的決定 |
| 16) Return values | 動作／命令式名稱回傳 -errno（`add_work()` 回 0 或 `-EBUSY`）；述語式名稱回傳布林（`pci_dev_present()`） | 看名字就知道 `if (foo())` 的成功分支是哪邊 |

第 16 節值得特別背。它直接解決一個讀碼時常卡的問題：`if (ret)` 到底是成功還是失敗？規則說——**EXPORT 的函式「must respect this convention」**：

- `xa_insert()`、`add_work()` 這種動詞 → 0 成功、負數失敗，所以 `if (ret)` 是錯誤分支。
- `folio_test_locked()`、`pci_dev_present()`、`pipe_has_watch_queue()` 這種述語 → true 代表「是」。

### 讓工具告訴你什麼是「不像 kernel 的 code」

`scripts/checkpatch.pl` 是 coding style 的自動檢查器。寫一段 user-space 習慣的 C：

```c
int CountUsers(int *p, int n) {
    int i, cnt = 0;
    for (i = 0; i < n; i++)
    {
        if (p[i]) cnt++;
    }
    return cnt;
}
```

```console
$ perl ~/code_reading/linux/scripts/checkpatch.pl --no-tree --terse -f bad_style.c
bad_style.c:1: WARNING: Missing or malformed SPDX-License-Identifier tag in line 1
bad_style.c:1: ERROR: open brace '{' following function definitions go on the next line
bad_style.c:2: WARNING: please, no spaces at the start of a line
bad_style.c:3: WARNING: Missing a blank line after declarations
bad_style.c:3: WARNING: please, no spaces at the start of a line
bad_style.c:3: ERROR: that open brace { should be on the previous line
bad_style.c:4: WARNING: please, no spaces at the start of a line
bad_style.c:5: ERROR: code indent should use tabs where possible
bad_style.c:5: WARNING: please, no spaces at the start of a line
bad_style.c:5: ERROR: trailing statements should be on next line
bad_style.c:6: WARNING: please, no spaces at the start of a line
bad_style.c:7: WARNING: please, no spaces at the start of a line
total: 4 errors, 8 warnings, 8 lines checked
```

注意 checkpatch 預設**沒有**抱怨 `CountUsers` 這個 CamelCase 名稱。CamelCase 檢查在 checkpatch 裡屬於 `CHK` 等級，要加 `--strict` 才會出現：

```console
$ perl ~/code_reading/linux/scripts/checkpatch.pl --no-tree --terse --strict -f bad_style.c | grep -i camel
bad_style.c:1: CHECK: Avoid CamelCase: <CountUsers>
```

而且即使加了 `--strict`，它也只能告訴你「這是 CamelCase」，不能告訴你「`cnt` 這個名字在這裡夠不夠清楚」。工具抓得到的是機械性規則（大括號位置、tab），抓不到命名品質。這也是為什麼讀碼時你得自己內化規則，而不是依賴工具。

對讀碼最大的回報是：當你在 `fs/` 裡看到一個函式**違反**這些規則（例如四層巢狀、十幾個區域變數、`err1:` 式 label），把它當成一個 beacon（[Ch 2](./02-program-comprehension-models.md)）：這段很可能是很老的程式碼、或是熱路徑上刻意不拆的程式碼。`git blame` 一下（[Ch 37](./37-git-archaeology.md)），答案通常就出來了。

## 文件過時與說謊

這是本章最重要的一節。先下結論：**註解和程式碼不一致時，程式碼是事實，註解是線索**。註解不會被編譯器檢查，改程式碼的人沒有義務、也常常忘了改註解。

### 實例一：註解提到一個五年前就不存在的函式

`fs/namei.c` 裡的 `follow_automount()`：

```c
/*
 * Perform an automount
 * - return -EISDIR to tell follow_managed() to stop and return the path we
 *   were called with.
 */
static int follow_automount(struct path *path, int *count, unsigned lookup_flags)
```
（`fs/namei.c:1426-1431`（v6.18））

註解說回傳 `-EISDIR` 是「告訴 `follow_managed()` 停下來」。我們驗證 `follow_managed()` 存不存在：

```console
$ for t in v5.6 v5.7 v6.18; do echo "$t: $(git show $t:fs/namei.c | grep -c 'follow_managed')"; done
v5.6: 6
v5.7: 1
v6.18: 1
$ git show v5.7:fs/namei.c | grep -n follow_managed
1158: * - return -EISDIR to tell follow_managed() to stop and return the path we
```

v5.6 有 6 處提到它（定義加呼叫），v5.7 起只剩這一行註解。用 commit message 搜尋找出誰移除了它（`--grep` 只搜訊息，在 blobless clone 上很快；`-S` 要讀每個版本的檔案內容，會慢很多）：

```console
$ git log --oneline --grep='follow_managed' | head -6
233a806b00e3 Merge tag 'docs-5.14' of git://git.lwn.net/linux
993b892610d1 docs: path-lookup: update follow_managed() part
9deed3ebca24 new helper: traverse_mounts()
ea936aeb3ead massage __follow_mount_rcu() a bit
aca2903eefd0 new step_into() flag: WALK_NOFOLLOW
bd7c4b508344 handle_mounts(): start building a sane wrapper for follow_managed()
$ git show 9deed3ebca24 | grep -n '^-.*follow_managed'
39:-static int follow_managed(struct path *path, struct nameidata *nd)
198:-	ret = follow_managed(path, nd);
$ git describe --contains 9deed3ebca24
v5.7-rc1~128^2~37
```

Al Viro 在 2020 年 1 月的 `9deed3ebca24`（"new helper: traverse_mounts()"）刪掉了 `follow_managed()`，進入 v5.7。現在真正處理 `-EISDIR` 的是 `__traverse_mounts()`：

```c
		// uncovered automount point
		ret = follow_automount(path, count, lookup_flags);
		flags = smp_load_acquire(&path->dentry->d_flags);
		if (ret < 0)
			break;
	}

	if (ret == -EISDIR)
		ret = 0;
```
（`fs/namei.c:1511-1519`（v6.18），所在函式 `__traverse_mounts()` 從 1468 行開始）

更有意思的是 `993b892610d1`（"docs: path-lookup: update follow_managed() part"，v5.14）。它的 diff 把文件裡的「Each of these will call ``follow_managed()``」改寫成「`step_into()` ... calls ``handle_mounts()``」，也把「function names such as "``follow_managed()``"」刪掉了：**`Documentation/filesystems/path-lookup.rst` 在 v5.14 已經更新，但程式碼裡的註解到 v6.18 都沒人改**。所以「文件比註解新」這種事真的會發生。

讀碼的教訓：註解的**語意**仍然是對的（`-EISDIR` 的意思是「停，回傳原本的 path」），只是函式名字過時了。看到這種註解，正確的反應是「找出現在誰扮演 `follow_managed()` 的角色」，而不是去找一個不存在的函式然後卡住。

### 實例二：測試裡的註解和測試本身相反

`tools/testing/selftests/splice/short_splice_read.sh` 結尾：

```sh
### /sys/ has no splice interface; these should all fail.
if ! [ -d /sys/module/test_module/sections ] ; then
	expect_success "test_module kernel module load" modprobe test_module
fi
expect_success "kernfs attr splice" test_splice /sys/module/test_module/coresize
expect_success "kernfs binattr splice" test_splice /sys/module/test_module/sections/.init.text
```
（`tools/testing/selftests/splice/short_splice_read.sh:126-131`（v6.18））

註解說「應該全部失敗」，程式碼寫的是 `expect_success`。誰對？

```console
$ git log --format='%h %ad %s' --date=short -- tools/testing/selftests/splice/short_splice_read.sh
177f504cb70c 2022-11-25 selftests: splice_read: Fix sysfs read cases
6daf076b717d 2021-05-26 selftests: splice: Adjust for handler fallback removal
9af47666cb0f 2020-08-06 selftests: splice: Check behavior of full and short splices
$ git show 177f504cb70c | sed -n '5,11p'
    selftests: splice_read: Fix sysfs read cases

    sysfs now supports splice_* operations with

     commit f2d6c2708bd84 ("kernfs: wire up ->splice_read and ->splice_write")

    Update the selftests to expect success instead of failure.
```

`177f504cb70c` 把兩行 `expect_failure` 改成 `expect_success`，理由是 kernfs 在 `f2d6c2708bd84`（v5.11-rc5 期間合入）之後支援 splice 了。diff 只改了那兩行，正上方的註解沒動。**程式碼是對的，註解是 2020 年的事實**。

這個例子在 [Ch 40](./40-tests-as-specification.md) 會再出現：測試本身是可執行的規格，所以它會跟著行為更新；測試裡的註解不會被執行，所以它會落後。

### 偵測方法：讓腳本找「只在註解裡出現的函式名」

上面兩個例子不是隨手翻到的，是用一支腳本找出候選、再逐一人工驗證的。腳本邏輯：從註解裡抽出 `foo()` 形式的名字，到整個 tree 裡找「不在註解行中」的 `foo(`；一處都找不到，就列為可疑。

```python
#!/usr/bin/env python3
"""Find identifiers written as foo() inside C comments that are not
defined/declared anywhere in the tree (candidate stale comments)."""
import re, subprocess, sys

COMMENT = re.compile(r'/\*.*?\*/|//[^\n]*', re.S)
CALL = re.compile(r'\b([a-z_][a-z0-9_]{3,})\(\)')

def defined(name, tree):
    r = subprocess.run(['rg', '-n', '--no-heading', '--type', 'c', '--type', 'asm',
                        r'(^|[^a-z0-9_])' + name + r'\s*\(', tree],
                       capture_output=True, text=True)
    hits = []
    for l in r.stdout.splitlines():
        f, _, text = l.split(':', 2)
        if re.match(r'\s*(\*|/\*|//)', text):   # comment line, ignore
            continue
        hits.append(f)
    return hits

tree = sys.argv[1]
for path in sys.argv[2:]:
    src = open(path, errors='replace').read()
    seen = set()
    for m in COMMENT.finditer(src):
        line = src.count('\n', 0, m.start()) + 1
        for name in CALL.findall(m.group()):
            if name in seen:
                continue
            seen.add(name)
            files = defined(name, tree)
            stripped = [f for f in files if not f.endswith(path)]
            if not stripped:
                code = COMMENT.sub('', src)
                if not re.search(r'\b' + name + r'\s*\(', code):
                    print(f'{path}:{line}: {name}() not found outside comments')
```

在 v6.18 跑幾個檔案（每個檔案要對整個 tree 跑多次 `rg`，這組大約 20 秒）：

```console
$ python3 ~/code_reading/scratch/39/stale_refs.py . fs/pipe.c fs/eventfd.c mm/filemap.c \
      fs/splice.c fs/read_write.c kernel/fork.c fs/namei.c mm/memory.c
mm/filemap.c:1656: folio_test_clear_reclaim() not found outside comments
mm/filemap.c:4617: cachestat() not found outside comments
fs/splice.c:1127: splice_in() not found outside comments
fs/splice.c:1235: start_file_write() not found outside comments
kernel/fork.c:2879: clone2() not found outside comments
fs/namei.c:47: _follow_link() not found outside comments
fs/namei.c:47: dir_namei() not found outside comments
fs/namei.c:47: _namei() not found outside comments
fs/namei.c:47: lookup_dentry() not found outside comments
fs/namei.c:1427: follow_managed() not found outside comments
mm/memory.c:1936: compound_mapcount() not found outside comments
mm/memory.c:2748: pfn_t_has_page() not found outside comments
mm/memory.c:5249: __lock_page_retry() not found outside comments
```

**這份輸出不是「13 個錯誤註解」，是 13 個候選**。逐一判讀：

| 候選 | 判定 | 理由 |
|---|---|---|
| `folio_test_clear_reclaim()` | 假陽性 | 由 `TESTCLEARFLAG(Reclaim, reclaim, PF_NO_TAIL)`（`include/linux/page-flags.h:603`）巨集生成，grep 找不到字面定義（[Ch 14](./14-macro-metaprogramming.md) 講過巨集生成的名字） |
| `cachestat()` | 假陽性 | 指的是 syscall，定義寫成 `SYSCALL_DEFINE4(cachestat, ...)`（`mm/filemap.c:4651`） |
| `clone2()` | 假陽性 | 指的是 ia64 的 syscall 名，是在說明歷史 |
| `fs/namei.c:47` 那四個 | 刻意的歷史記錄 | 檔頭那段註解本來就在講舊版 namei 的演進 |
| `follow_managed()` | **真的過時** | 實例一 |
| `__lock_page_retry()`、`compound_mapcount()` | 很可能過時，留給你驗證 | 見本章動手練習 |

偵測工具的價值是**縮小範圍**，判讀仍然要人來做。這和 [Ch 53](./53-llm-assisted-reading.md) 會講的 LLM 輔助是同一個道理：機器產生候選，人負責驗證。

## 原書 Ch 8 的文件類型，在 kernel 裡對應什麼

Spinellis 把專案文件分成幾類：系統規格、需求、設計文件、測試規格、使用者文件，外加 man page 和變更記錄。kernel 沒有正式的「需求文件」流程，但每一類都有對應物，只是分散在不同地方：

| 原書類型 | kernel 的對應物 | 在哪裡 | 可信度與注意事項 |
|---|---|---|---|
| 規格（specification） | 對 user space 的承諾 | `Documentation/userspace-api/`、`Documentation/ABI/stable/`、man-pages 專案 | ABI 有「不破壞 user space」的強約束，可信度最高；但 man-pages 是獨立專案，偶爾落後於 kernel |
| 設計文件 | 子系統設計說明 | `Documentation/<subsys>/*.rst`（例如 `filesystems/path-lookup.rst`、`core-api/maple_tree.rst`） | 品質差異極大，取決於維護者；要看最後更新時間 |
| 設計理由 | patch series cover letter、pull request 訊息 | lore.kernel.org、merge commit 的訊息 | 寫的是「當時為什麼」；[Ch 38](./38-mailing-list-archaeology.md) |
| 介面說明 | kernel-doc 註解 | 原始碼內，生成到 docs.kernel.org | 和程式碼在同一個檔案，比 `.rst` 不容易過時，但仍會過時 |
| man page | section 2（syscall）由 man-pages 專案維護；section 9 可由 kernel-doc 生成 | man7.org、`scripts/kernel-doc -man` | section 2 是讀 syscall 語意的首選；section 9 很少人看 |
| 測試規格 | kselftest、KUnit | `tools/testing/selftests/`、`lib/tests/` | 可執行，會跟著行為更新；[Ch 40](./40-tests-as-specification.md) |
| changelog | git commit message；每版的變更摘要 | `git log`、kernelnewbies.org 的 LinuxChanges、LWN 的 merge window 系列 | commit message 是最細的 changelog；kernel 原始碼樹裡沒有傳統的 `CHANGELOG` 檔 |

最後一列是 kernel 和一般專案最不同的地方。很多專案有 `CHANGELOG.md`；kernel 的 changelog **就是 git 歷史本身**，而且 commit message 的品質要求很高（`Documentation/process/submitting-patches.rst` 有專節說明怎麼寫）。這也是為什麼 [Ch 37](./37-git-archaeology.md) 的 git 考古在 kernel 上特別有效。

## 對比與取捨

| 資訊來源 | 優點 | 缺點 | 讀碼時的用法 |
|---|---|---|---|
| 程式碼本體 | 唯一的事實 | 只說 what／how，很少說 why；間接多時難追 | 最終仲裁 |
| 函式內註解 | 貼著程式碼，常解釋不直觀的決定 | 改碼時最容易被忘記 | 當作假說，驗證後採信 |
| kernel-doc | 結構化；`Context:` 給出呼叫契約 | 覆蓋率不完整；工具只檢查格式 | 讀鎖相關、context 相關問題的第一站 |
| `Documentation/*.rst` | 有架構觀、有名詞定義 | 和程式碼分離，更容易過時 | 建立概念模型；先 `git log` 看更新時間 |
| commit message | 寫了 why，而且有日期 | 描述的是那個時間點；函式名可能已改 | 脈絡鴻溝的主要解法 |
| coding style | 讓你預測一般情況 | 舊程式碼不一定符合 | 找「違反規則的地方」當 beacon |

## 踩雷集錦

1. **「有 kernel-doc 就代表這是穩定 API」**：錯。kernel-doc 只是文件格式，和 API 穩定性無關。kernel 內部 API 沒有穩定承諾（`Documentation/process/stable-api-nonsense.rst` 專門講這件事）；穩定的只有對 user space 的介面。
2. **「沒寫 `Context:` 就是任何 context 都能呼叫」**：錯。v6.18 只有 1326 處寫了 `Context:`。沒寫代表作者沒寫，限制仍然存在。要自己追：它有沒有 `might_sleep()`、`mutex_lock()`、`GFP_KERNEL` 配置？
3. **「註解和程式碼不一致，一定是程式碼有 bug」**：通常相反。實例一、實例二都是程式碼正確、註解落後。先 `git log` 那段程式碼，看是誰在什麼時候改了行為；如果改動有清楚的理由，註解就是過時了。
4. **「scripts/kernel-doc 沒警告就代表文件正確」**：它不比對內容。demo.c 裡寫錯的 `@itm` 它沒報（在 v6.18 Python 版、`-Wall` 下實測），exit code 也是 0。
5. **「coding style 是寫 code 的規定，跟讀碼無關」**：這是本章最想扭轉的觀念。第 16 節的回傳值慣例、第 7 節的 label 命名，都是直接幫你讀懂 `if (ret)` 和 `goto out_unlock` 的工具。
6. **「docs.kernel.org 上看到的就是我這個版本的行為」**：網站預設顯示最新版。讀 v6.18 程式碼時，用 tree 裡的 `Documentation/` 或網站的版本化 URL，不要拿 latest 的文件對照 v6.18 的程式碼。

## 進階：再往深一層

- **把 kernel-doc 接進你的編輯器**：clangd（[Ch 49](./49-clangd-lsp.md)）hover 時會顯示函式頭的註解，等於隨時看到 kernel-doc。這是把「先讀契約再讀本體」變成反射動作最有效的方法。
- **`make htmldocs`**：在本機把整個 `Documentation/` 建成網站（需要 Sphinx 與相依套件，建置時間以分鐘計，本章未實測）。好處是能看到 v6.18 確切版本的網站，並且用 `make SPHINXDIRS=core-api htmldocs` 只建一部分。
- **`Documentation/ABI/` 的分級**：`stable/` 是承諾、`testing/` 是「大概不會變」、`obsolete/` 是即將移除、`removed/` 是已移除。讀 sysfs 相關程式碼時，先查這裡知道它的承諾等級。
- **偵測腳本的改進方向**：(a) 先跑一次 `gtags` 或 `ctags`（[Ch 48](./48-elixir-cscope-gtags.md)），用符號表取代 `rg`，速度會快很多，也能部分處理巨集生成的名字；(b) 對每個候選自動跑 `git log -S'name(' -- <file>`，直接找出移除它的 commit（在 blobless clone 上要限定路徑與版本範圍，否則會觸發大量 blob 下載）。

## 動手練習

1. **驗證 `__lock_page_retry()`**：`mm/memory.c:5249` 附近的註解說「See filemap_fault() and __lock_page_retry()」。用 `git log --grep` 搜尋 `__lock_page_or_retry` 相關 commit，判斷 (a) `__lock_page_retry()` 是否曾經存在過（可能從一開始就是筆誤）；(b) 現在扮演這個角色的函式叫什麼。提示：`git log --oneline --grep='__folio_lock_or_retry'`。
2. **找一個 `Context:` 與本體矛盾的函式**：挑 `lib/xarray.c` 裡三個寫了「Takes and releases the xa_lock」的函式，確認本體真的有 `xa_lock()`／`xa_unlock()`。再挑一個寫「May sleep」的，找出它可能睡的那一行。
3. **對你下一個要讀的檔案跑 `kernel-doc -none -Wall`**，記錄有幾個警告。警告多的檔案，代表文件維護度低，讀的時候對註解要更多疑。
4. **跑 stale_refs.py 在 `kernel/sched/core.c` 或 `net/ipv4/tcp.c` 上**，至少判讀三個候選，把結論寫進你的讀碼筆記（[Ch 7](./07-reading-notes-and-diagrams.md)）。

## 本章重點整理

- `Documentation/` 有 3829 份 `.rst`；`process/`、`core-api/`、`userspace-api/`、`dev-tools/` 是讀碼最常用的四個目錄。網站頁面和 tree 內檔案一一對應。
- kernel-doc 以 `/**` 開頭；`Context:` 是呼叫契約，`Return:` 是回傳語意。`scripts/kernel-doc`（v6.16 起是 Python 版）能抽成 reST、man page，`-none -Wall` 能挑格式問題。
- coding style 的每條規則都是一個預測；違反規則的地方是 beacon。
- 註解會過時。實例：`follow_managed()` 在 v5.7 被刪，v6.18 的註解還在提它；splice selftest 的註解說「should all fail」，程式碼在 2022 年已改成 `expect_success`。
- 信程式碼，用註解當假說，用 git 找出兩者分歧的時間點。

## 自我檢核

- [ ] 不看筆記，說出 `xa_erase()` 和 `__xa_erase()` 在 lock 責任上的差別，以及你是從哪個欄位知道的。
- [ ] 如果同事說「這函式沒寫 `Context:`，所以中斷裡可以呼叫」，你會怎麼反駁？要追哪些東西才能確定？
- [ ] 解釋 coding style 第 16 節的回傳值慣例，並用它判斷 `if (pipe_has_watch_queue(pipe))` 和 `if (xa_insert(...))` 各自的 true 分支是什麼意思。
- [ ] 你在一段程式碼的註解裡看到一個 grep 不到的函式名。描述你接下來的三個步驟。
- [ ] 舉出原書 Ch 8 的三種文件類型，說出它們在 kernel 裡的對應物與可信度。
- [ ] 為什麼「文件比註解新」這種事會發生？用 `follow_managed()` 的例子說明。

## 延伸閱讀

### 官方文件

- **[Linux kernel coding style](https://docs.kernel.org/process/coding-style.html)**（tree 內：`Documentation/process/coding-style.rst`）
  - **讀哪裡**：第 1、4、6、7、8、16 節。第 16 節（Function return values and names）對讀碼的回報最高。
  - **能學到什麼**：本章表格的完整原文；每條規則背後 Linus 式的理由。
  - **前提**：會寫 C。
- **[Writing kernel-doc comments](https://docs.kernel.org/doc-guide/kernel-doc.html)**（tree 內：`Documentation/doc-guide/kernel-doc.rst`）
  - **讀哪裡**：「Function documentation」「Function context」「Return values」三節，以及最後的 `kernel-doc::` 指令說明（`:export:`、`:internal:`、`:identifiers:`）。
  - **能學到什麼**：所有欄位的正式語法；struct、enum、typedef 的 kernel-doc 寫法（本章沒展開）。
  - **前提**：看過本章的 `xa_erase()` 例子。
- **[Pathname lookup](https://docs.kernel.org/filesystems/path-lookup.html)**（tree 內：`Documentation/filesystems/path-lookup.rst`）
  - **讀哪裡**：搜尋「handle_mounts」與「managed」相關段落，對照本章實例一。
  - **能學到什麼**：一份高品質、由開發者維護的設計文件長什麼樣；也能看到它在 v5.14 被更新的痕跡（`993b892610d1`）。
  - **前提**：知道 dentry、mount 是什麼。

### 部落格 / 技術文章

- **[Kernel documentation with Sphinx, part 1: how we got here](https://lwn.net/Articles/693509/)** — Jonathan Corbet（LWN，2016）
  - **這篇說什麼**：kernel 文件從 DocBook 轉到 Sphinx／reStructuredText 的來龍去脈，以及 kernel-doc 在其中的角色。
  - **讀哪裡**：整篇；它是一個系列的第一篇，後續篇章在 LWN 的 Kernel Index「Documentation」條目下。
  - **為什麼值得讀**：作者是 kernel 文件的維護者，解釋了為什麼現在的文件長這樣。注意這是 2016 年的文章，kernel-doc 已在 v6.16 改成 Python 實作。

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：Ch 7（Coding Standards and Conventions）、Ch 8（Documentation）。
  - **這本書的定位**：原書用 NetBSD 和 Apache 舉例，方法論仍然適用；本章把它的文件分類對應到 kernel。特別值得讀的是 Ch 8 談文件問題的那一節：文件與程式碼脫節不是 kernel 特有的毛病，原書在 2003 年就把它當成讀碼者必須預期的常態。

文件告訴你作者的意圖、註解告訴你當時的事實；但有一種文件會被機器執行、因此不會偷偷過時——測試。下一章把 selftest 和 KUnit 當成規格來讀。

→ [Ch 40 把測試當規格讀：selftests 與 KUnit](./40-tests-as-specification.md)
