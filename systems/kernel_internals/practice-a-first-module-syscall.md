# 練習 A — 第一個核心模組 + 自訂 syscall

> **這是 Part 1（Ch 0–8）的整合練習。** 前八章你各自學了：模組怎麼載入（Ch 8）、syscall 怎麼跨進 kernel（Ch 4）、`list_head`/`container_of` 怎麼串資料（Ch 5）、`kmalloc`/GFP 怎麼要記憶體（Ch 6）、以及為什麼並行下要保護共享狀態（Ch 7）。這個練習把它們拼成一個能跑的東西：一個維護「process 筆記本」的核心模組，外加一條讀筆記數量的自訂 syscall。做完你會第一次感覺到——這些機制不是各自獨立的知識點，是拼在一起才成立的一套系統。

## 背景與動機

你在 `linux_commands` 裡用過 `/proc/self/status`、在 `bpf` 裡從 tracepoint 撈過 process 資訊，那些都是 kernel **已經幫你維護好**的資料，你只是讀。這個練習反過來：**你自己在 kernel 裡維護一份資料結構**，決定它長怎樣、掛在哪、誰能改、卸載時誰負責清乾淨。

我們要做的「process 筆記本」很小，但它踩到的點正是每個真實子系統天天在做的事：

- **動態配置物件並串起來**：每筆記用 `kmalloc` 配一塊，用 `list_head` 掛上一條鏈——這就是 task list、VMA list、timer list 的迷你版
- **開放 user 介面**：透過 `/proc` 讓 user space 能寫入（新增筆記）、讀出（列出筆記）——這是 kernel 和 user 對話最常見的其中一條路
- **並行保護**：同一時間可能有兩個 process 都在寫你的 list，沒有鎖就是 race。這裡先用最粗暴的 `mutex` 起步，理由講清楚，細節留 Part 4（Ch 24–28）
- **資源生命週期**：模組卸載時，list 上所有 `kmalloc` 出來的筆記都得 `kfree`，否則就是 kernel memory leak——而 kernel 沒有 process 結束自動回收這回事，漏了就一直漏到重開機

主線任務把上面四件事做出來。進階任務再加一條自訂 syscall，讓 user 不透過 `/proc` 而是直接用 syscall 問「現在有幾筆記」——把 Ch 4 的知識也接進來。

**全程在 Ch 0 的 QEMU + gdb 環境驗證。** 你不需要真機、不需要 root 你自己的筆電——弄壞了就重開虛擬機。

## 先建立心智模型

動手前先在腦中畫清楚這個模組長怎樣、資料掛在哪、user 怎麼進來。整張圖是這樣：

```
   user space                          kernel space（notebook.ko 模組內）
   ─────────                           ────────────────────────────────
                                        static LIST_HEAD(nb_list)  ← 鏈頭（模組全域）
                                             │
   echo "x" > /proc/notebook                 │  list_add 把新 note 掛頭
        │  write(2) syscall                  ▼
        ▼                             ┌──────────────┐  ┌──────────────┐
   VFS → proc_ops.proc_write ────────►│ struct note  │─►│ struct note  │─► (回 nb_list)
        │  copy_from_user             │ pid  = 41    │  │ pid  = 41    │
        │                             │ text = "x"   │  │ text = "..." │
        │                             │ list ────────┘  │ list ────────┘   每個都 kmalloc
   cat /proc/notebook                 └──────────────┘  └──────────────┘   來的一塊
        │  read(2) syscall
        ▼  proc_ops.proc_read（seq_file）
   list_for_each_entry 走一遍，seq_printf 印出

   全部進出 nb_list 的動作都夾在 mutex_lock(&nb_lock) … mutex_unlock 之間
```

三個關鍵認知，對上前八章：

- **`nb_list` 是模組的「根」**：所有筆記都從這根鏈頭掛出去。這跟 kernel 用 `init_task.tasks` 當所有 process 的鏈頭是同一個套路（Ch 5/Ch 9）。你這個練習就是那套結構的最小可跑版。
- **`note` 是 `kmalloc` 出來的、`list` 欄位嵌在裡面**：不是「list 節點裝著一個 note 指標」，而是「note 裡嵌一個 list 節點」——侵入式（Ch 5）。所以從 list 走回 note 要靠 `container_of`（`list_for_each_entry` 幫你包好了）。
- **`/proc/notebook` 是唯一的門**：user 對這份資料的所有操作，都得穿過 `proc_ops` 裡的 `proc_read`/`proc_write`，就像所有 syscall 都穿過單一入口查表（Ch 4）。你控制這扇門，就控制了誰能碰你的 list。

## 任務規格

### 主線任務：process 筆記本模組

寫一個核心模組 `notebook.ko`，行為如下。

**資料模型**：模組內維護一條 `list_head` 串起的鏈，每個節點是一筆「筆記」，含三個欄位：

- `pid`：寫入這筆記的 process 的 PID（從 `current->pid` 取，見 Ch 2/Ch 9）
- `text`：一段訊息字串（上限自訂，建議 128 bytes）
- `list`：`struct list_head`，用來掛上鏈（侵入式，見 Ch 5）

每個節點用 `kmalloc(..., GFP_KERNEL)` 配置（見 Ch 6，為什麼是 `GFP_KERNEL` 不是 `GFP_ATOMIC`，你要能答）。

**User 介面**：用 procfs 開一個 `/proc/notebook` 檔：

- **寫入**（`echo "some text" > /proc/notebook`）：新增一筆記，`pid` = 寫入者 PID、`text` = 寫入的內容，掛到 list 頭
- **讀取**（`cat /proc/notebook`）：把 list 上所有筆記依序印出，每行格式 `[<pid>] <text>`

**並行保護**：新增筆記（改 list）、遍歷 list（讀）都要用一把 `mutex` 保護。你要能在踩雷集錦或自我檢核裡回答「為什麼這裡不能用 spinlock」。

**卸載清理**：`rmmod notebook` 時，把 list 上所有節點 `kfree` 乾淨、移除 `/proc/notebook`。用 KASAN 或 `kmemleak`（Ch 53 會深入，這裡先當驗收工具）驗證沒有 leak。

### 進階任務：自訂 syscall 讀筆記數量

讓 user space 不透過 `/proc` 而是直接用一條 syscall 問「現在筆記本有幾筆」。給你兩條路，難度與正統程度不同：

- **路 (a)｜正規做法**：照 Ch 4 加一條 syscall `sys_notebook_count`，改 `arch/x86/entry/syscalls/syscall_64.tbl` + 寫 `SYSCALL_DEFINE` + 重編 kernel。難點是**這條 syscall 要怎麼拿到「模組裡那個計數」**——syscall 是編進 kernel 的，模組是後載入的，兩者怎麼溝通？（提示在下面。）
- **路 (b)｜模組內 hacky 做法**：不動 kernel 源碼、不重編，在模組裡想辦法「攔截」一個既有的、你不在乎的 syscall，或直接改 `sys_call_table`。你**不必真的實作**它到能跑，但要在解答裡**說清楚具體怎麼做、為什麼危險、正式場合為什麼零可行性**。

主線任務是必做，進階任務至少完成路 (a) 或把路 (b) 的分析寫清楚其一。

### 驗收標準

| # | 檢查項 | 怎麼驗 |
|---|---|---|
| 1 | 模組能 `insmod` 成功、`dmesg` 有載入訊息 | `insmod notebook.ko; dmesg | tail` |
| 2 | `echo "hello" > /proc/notebook` 後 `cat /proc/notebook` 看得到 `[<pid>] hello` | 手動操作 |
| 3 | 多筆寫入依序都在，PID 正確 | 連寫三筆，`cat` 看三行 |
| 4 | `rmmod` 後 `/proc/notebook` 消失、無 KASAN/kmemleak 報告 | `rmmod; ls /proc/notebook`（應 No such file）；`echo scan > /sys/kernel/debug/kmemleak; cat /sys/kernel/debug/kmemleak`（應無本模組相關項）|
| 5 | 兩個 process 同時狂寫，list 不損壞、不 crash | 兩個 shell 各跑一個寫入迴圈，`cat` 檢查筆數正確、內容不亂 |
| 6 |（進階 a）user 程式呼叫自訂 syscall 拿到的數字，和 `cat /proc/notebook` 的行數一致 | 跑 caller 程式對照 |

## 期望輸出範例

```
/ # insmod /notebook.ko
[   12.3] notebook: loaded, /proc/notebook ready
/ # echo "fix the scheduler bug"  > /proc/notebook
/ # echo "read cfs_rq source"     > /proc/notebook
/ # cat /proc/notebook
[   41] read cfs_rq source
[   41] fix the scheduler bug
/ # 
```

（`[41]` 是寫入者 PID，因為兩次 `echo` 都由同一個 shell 的子行程或 shell 自己發出——實際 PID 依你的環境而定。新的掛在 list 頭，所以後寫的先印。）

進階任務路 (a) 的 caller：

```
/ # ./nb_count
notebook has 2 note(s)
```

卸載並驗證無 leak：

```
/ # rmmod notebook
[   88.7] notebook: unloaded, freed 2 note(s)
/ # cat /proc/notebook
cat: can't open '/proc/notebook': No such file or directory
```

## 卡關提示

1. **procfs 該用哪組 API**：v6.12 開 `/proc` 檔用 `proc_create()`（`include/linux/proc_fs.h`），第四參數傳一個 `struct proc_ops`（**不是**舊教材裡的 `file_operations`——5.6 起 proc 檔改用獨立的 `proc_ops`，用錯型別編不過）。讀走 `proc_ops.proc_read`，寫走 `proc_ops.proc_write`。最省事的讀法是用 `seq_file`（`single_open` + `proc_create` 系列），但直接寫 `.proc_read` 手動 `copy_to_user` 也行、更能看清底層。

2. **從 user 搬字串進 kernel**：`.proc_write` 收到的是 user 指標，**不能直接解參**。要用 `copy_from_user(kbuf, ubuf, len)`（Ch 4 講過，`sys_hello` 就這樣搬字串）。回傳值不是 0 代表有 bytes 沒搬成，要處理。搬進來記得留結尾、砍掉 `echo` 帶的換行 `\n`。

3. **`kmalloc` 的 GFP 選哪個**：你的 `.proc_write` 跑在 **process context**（是某個 user process 因為 write syscall 進來的），能睡，所以用 `GFP_KERNEL`。如果哪天你把配置搬到 spinlock 保護區內或中斷裡，才需要 `GFP_ATOMIC`——這正是 Ch 6/Ch 7 那個「這裡能不能睡」判斷的實戰。這裡先 `GFP_KERNEL`。

4. **mutex 而不是 spinlock，為什麼**：你在臨界區裡呼叫 `kmalloc(GFP_KERNEL)` 和 `copy_from_user`——這兩個**都可能睡**（`GFP_KERNEL` 允許 reclaim 時阻塞、`copy_from_user` 可能觸發 page fault 而睡）。spinlock 持有時**絕對不能睡**（會死鎖，Ch 25 詳述），所以這裡只能用能睡的鎖：`mutex`。用 `DEFINE_MUTEX`、`mutex_lock`/`mutex_unlock`（`include/linux/mutex.h`）。

5. **進階路 (a) 的「syscall 怎麼看到模組的計數」**：syscall 編在 kernel 裡、模組後載入，最乾淨的接法是——在 kernel 裡放一個 `atomic_t notebook_count`（或一個函式指標）當**契約**，syscall 讀它、模組更新它。但這需要你的 syscall body 引用一個模組會更新的全域符號。**更簡單、對這個練習夠用的做法**：不追求「syscall 讀模組的活資料」，而是讓 syscall 本身就是完整的（例如 body 直接回一個固定值先驗證管線通），或把整個計數邏輯放進 kernel 而非模組。想清楚你要驗證的是「syscall 管線通不通」還是「syscall 能不能讀模組狀態」——後者本質上就是 kernel/模組共享狀態問題，是 EXPORT_SYMBOL 的用途，可以先不碰。參考解答會示範最務實的版本。

## 分步實作建議

1. **先讓空模組 + 空 `/proc/notebook` 通**。`module_init` 裡 `proc_create("notebook", 0666, NULL, &nb_pops)`，`module_exit` 裡 `proc_remove()`。`.proc_read` 先固定印一行、`.proc_write` 先只 `pr_info` 收到幾 bytes。`insmod`、`echo`、`cat` 跑一遍，確認管線通。這步不碰 list、不碰 kmalloc，先把 procfs 接線弄對。

2. **加資料結構與寫入**。定義 `struct note { int pid; char text[128]; struct list_head list; }` 和一個 `LIST_HEAD(nb_list)`。`.proc_write` 裡：`kmalloc` 一個 `note`、`copy_from_user` 填 `text`、`current->pid` 填 `pid`、`mutex_lock` 後 `list_add(&n->list, &nb_list)`、`mutex_unlock`。

3. **實作讀取（列出）**。`.proc_read` 或 `seq_file` 的 show 函式裡，`mutex_lock` 後 `list_for_each_entry(n, &nb_list, list)` 印每筆，`mutex_unlock`。若用手寫 `.proc_read`，注意 `*ppos` 語意（讀完要回傳 0 表 EOF，否則 `cat` 會無限迴圈）——這就是為什麼 `seq_file` 存在，它幫你處理分頁。

4. **實作卸載清理**。`module_exit` 裡：`proc_remove` 先拔掉 user 介面（避免拔到一半還有人在寫），然後 `list_for_each_entry_safe(n, tmp, &nb_list, list) { list_del(&n->list); kfree(n); }`——**一定要用 `_safe` 版**，因為你邊遍歷邊刪，普通 `list_for_each_entry` 會在 `kfree` 後用到已釋放的 `next` 指標（UAF）。

5. **（進階）加自訂 syscall**。照 Ch 4 的「動手」那節：`syscall_64.tbl` 加一號、`SYSCALL_DEFINE0(notebook_count)` 寫 body、重編 kernel、寫 `nb_count.c` 用 `syscall(548)` 呼叫。決定你要驗「管線通」還是「讀模組狀態」，參考解答給前者的最小版與後者的 `EXPORT_SYMBOL` 走法。

## 完整參考解答

<details>
<summary>點開看完整可編譯解答（notebook.c + Makefile + 測試程式 + 進階 syscall）</summary>

### `notebook.c`（主線任務，用 seq_file 讀）

```c
// notebook.c — process 筆記本核心模組（練習 A 主線）
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/slab.h>        // kmalloc / kfree
#include <linux/list.h>        // list_head 一族
#include <linux/mutex.h>       // mutex
#include <linux/uaccess.h>     // copy_from_user
#include <linux/sched.h>       // current

#define NB_TEXT_MAX 128

struct note {
    int pid;
    char text[NB_TEXT_MAX];
    struct list_head list;
};

static LIST_HEAD(nb_list);           // 鏈頭（空 list）
static DEFINE_MUTEX(nb_lock);        // 保護 nb_list 的鎖
static int nb_count;                 // 目前筆數（受 nb_lock 保護）

// ---- 讀：用 seq_file 列出所有筆記 ----
static int nb_show(struct seq_file *m, void *v)
{
    struct note *n;

    mutex_lock(&nb_lock);
    list_for_each_entry(n, &nb_list, list)
        seq_printf(m, "[%d] %s\n", n->pid, n->text);
    mutex_unlock(&nb_lock);
    return 0;
}

static int nb_open(struct inode *inode, struct file *file)
{
    // single_open：整份輸出一次算出，seq_file 幫我們處理分頁與 *ppos
    return single_open(file, nb_show, NULL);
}

// ---- 寫：新增一筆記 ----
static ssize_t nb_write(struct file *file, const char __user *ubuf,
                        size_t len, loff_t *ppos)
{
    struct note *n;
    size_t copy = len;

    if (copy >= NB_TEXT_MAX)
        copy = NB_TEXT_MAX - 1;      // 留一格給 '\0'

    // 跑在 process context，能睡 → GFP_KERNEL（見 Ch 6）
    n = kmalloc(sizeof(*n), GFP_KERNEL);
    if (!n)
        return -ENOMEM;

    if (copy_from_user(n->text, ubuf, copy)) {
        kfree(n);
        return -EFAULT;              // user 指標有問題，別讓它變 leak
    }
    n->text[copy] = '\0';
    // 砍掉 echo 帶的結尾換行，印出來才乾淨
    if (copy > 0 && n->text[copy - 1] == '\n')
        n->text[copy - 1] = '\0';

    n->pid = current->pid;           // 誰寫的（Ch 2 的 current / Ch 9 的 task_struct）
    INIT_LIST_HEAD(&n->list);

    mutex_lock(&nb_lock);            // 改 list → 進臨界區
    list_add(&n->list, &nb_list);    // 掛到 list 頭（新的先印）
    nb_count++;
    mutex_unlock(&nb_lock);

    return len;                      // 回報「吃掉」了全部 len，否則 echo 會重試
}

static const struct proc_ops nb_pops = {
    .proc_open    = nb_open,
    .proc_read    = seq_read,        // seq_file 提供
    .proc_lseek   = seq_lseek,
    .proc_release = single_release,
    .proc_write   = nb_write,
};

static struct proc_dir_entry *nb_entry;

static int __init nb_init(void)
{
    // 0666：user 可讀可寫（示範用；正式模組別開這麼鬆）
    nb_entry = proc_create("notebook", 0666, NULL, &nb_pops);
    if (!nb_entry)
        return -ENOMEM;
    pr_info("notebook: loaded, /proc/notebook ready\n");
    return 0;
}

static void __exit nb_exit(void)
{
    struct note *n, *tmp;
    int freed = 0;

    // 先拔掉 user 介面，確保之後沒人能再進 nb_write
    proc_remove(nb_entry);

    // 邊遍歷邊刪，必須用 _safe（先存 next 再 kfree，避免 UAF）
    mutex_lock(&nb_lock);
    list_for_each_entry_safe(n, tmp, &nb_list, list) {
        list_del(&n->list);
        kfree(n);
        freed++;
    }
    mutex_unlock(&nb_lock);

    pr_info("notebook: unloaded, freed %d note(s)\n", freed);
}

module_init(nb_init);
module_exit(nb_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Practice A: process notebook module");
MODULE_AUTHOR("kernel_internals");
```

**幾個設計決定的理由**：

- **`proc_ops` 不是 `file_operations`**：5.6 起 proc 檔用專屬的 `struct proc_ops`（`fs/proc/` 裡的變更），欄位名有 `proc_` 前綴。用舊的 `file_operations` 會型別不符編不過。這是新舊教材最常見的斷層。
- **`seq_file` 幫你處理 `*ppos`**：手寫 `.proc_read` 你得自己管「這是第幾次 read、還有沒有資料」，寫錯 `cat` 會無限印或印不全。`single_open` + `seq_read` 把整份輸出當一次算好，分頁交給 seq_file，適合「輸出量不大、一次算得完」的場景，我們的筆記本正是。
- **`list_add` 掛頭**：O(1)，但輸出是後進先出。想要先進先出改 `list_add_tail`。
- **`_safe` 遍歷**：`list_for_each_entry_safe` 會先把 `next` 存進 `tmp` 再讓你動 `n`，所以 `kfree(n)` 之後不會踩到已釋放記憶體。這是 Ch 5 的重點，也是新手最容易漏的 UAF。

### `Makefile`

```makefile
# 注意：recipe 行首是 Tab 不是空白（make 的老陷阱）
obj-m += notebook.o

KDIR := /path/to/your/linux-6.12      # 指向你 Ch 0 build 的那棵源碼樹

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
```

```bash
make
ls notebook.ko          # 編出來的模組
```

把 `notebook.ko` 放進 Ch 0 的 initramfs（`cp notebook.ko initramfs/`，重打包 cpio），QEMU 開機後 `insmod /notebook.ko`。

### 進階任務 路 (a)：自訂 syscall（正規做法）

照 Ch 4 的流程。先決定驗證目標——這裡示範**最務實的版本**：把「筆數」的權威放在 kernel 裡（不是模組），syscall 直接讀，避免 kernel/模組共享符號的複雜度。

**做法一（最簡，驗管線）**：syscall 只回一個固定值或讀一個 kernel 內全域，確認 user→syscall 管線通。

在 `arch/x86/entry/syscalls/syscall_64.tbl` 加一行（號碼挑一個 6.12 沒用到的，例如 548（462 在 6.12 已被 `mseal` 佔用），實際請確認你那份 `.tbl`）：

```
548	common	notebook_count		sys_notebook_count
```

找一個合適的 kernel 檔（或就近開一個 `kernel/notebook_count.c` 並加進 `kernel/Makefile`）寫 body：

```c
#include <linux/syscalls.h>
#include <linux/atomic.h>

// 由「把筆記本做進 kernel」的版本更新；模組版見做法二
atomic_t notebook_count_val = ATOMIC_INIT(0);

SYSCALL_DEFINE0(notebook_count)
{
    return atomic_read(&notebook_count_val);
}
```

`make -j"$(nproc)"` 重編、用新 `bzImage`/`vmlinux` 開 QEMU。

**做法二（要讓 syscall 讀「模組」的活計數）**：這本質是 **kernel/模組共享狀態**問題。模組把計數 `EXPORT_SYMBOL` 出來，或反過來 kernel 匯出一個 `atomic_t` 讓模組更新、syscall 讀。示意：

```c
// 在 kernel 內（非模組）某 .c：定義並匯出計數
#include <linux/export.h>
atomic_t notebook_count_val = ATOMIC_INIT(0);
EXPORT_SYMBOL(notebook_count_val);   // 讓模組能連到這個符號

// syscall body 同上，atomic_read(&notebook_count_val)
```

模組 `notebook.c` 端改成更新這個匯出的符號（把 `nb_count++` 換成 `atomic_inc(&notebook_count_val)`，並在卸載時 `atomic_set(..., 0)`）。這樣 syscall 讀到的就是模組維護的活計數。代價：模組現在依賴一個 kernel 匯出符號，得先重編 kernel（帶這個 `EXPORT_SYMBOL`）才能載入模組。這正是「編進 kernel 的東西」和「後載入的模組」溝通的標準管道——`EXPORT_SYMBOL`。

### `nb_count.c`（進階任務 user 端 caller）

```c
// nb_count.c — 呼叫自訂 syscall 548 讀筆記數
#include <stdio.h>
#include <unistd.h>
#include <sys/syscall.h>

#ifndef __NR_notebook_count
#define __NR_notebook_count 548      // 和 syscall_64.tbl 裡填的號碼一致
#endif

int main(void)
{
    long n = syscall(__NR_notebook_count);
    if (n < 0) {
        perror("syscall");
        return 1;
    }
    printf("notebook has %ld note(s)\n", n);
    return 0;
}
```

編譯（靜態連結，才能丟進 busybox initramfs 跑）：

```bash
gcc -static -o nb_count nb_count.c
cp nb_count initramfs/          # 重打包 cpio
```

### 進階任務 路 (b)：模組內 hacky 攔截（只分析，不建議實作）

不重編 kernel、想在模組裡「多一條 syscall 的效果」，現實中的 hack 手法與其風險：

- **改 `sys_call_table`**：找到 `sys_call_table` 位址（現代 kernel 不 export，得靠 `kallsyms_lookup_name` 或掃記憶體），把某個你不在乎的既有 syscall entry 換成你的函式指標。**問題**：`sys_call_table` 在 `.rodata`，是唯讀的（頁表 WP 保護，Ch 4 踩雷提過）。你得先關 CR0 的 WP 位元才能寫——這是教科書級 rootkit 技法，會被 CFI、kernel lockdown、以及和其他 CPU 的 race 反噬。而且你是**佔用**了一個既有 syscall number，不是憑空多一個，會破壞那個 syscall 的正常語意。
- **kprobe 攔既有 syscall**：在某個 `__x64_sys_xxx` 入口下 kprobe，在 handler 改行為（Ch 4 對比表、Ch 51 詳述）。適合**觀測**，但同樣沒法憑空多一個 syscall number，改既有行為也脆弱（函式改名、inline 就失效），且有效能開銷。

**結論**：路 (b) 對「新增一條 syscall」本質上做不到，只能劫持既有的，代價是安全性、穩定性全丟。正式場合零可行性，價值僅在於理解 rootkit 怎麼玩、以及為什麼 kernel 要把 `sys_call_table` 設唯讀。加 syscall 的正途永遠是路 (a)。

</details>

## 測試用例表

| 測試 | 操作 | 期望結果 | 對應驗收 |
|---|---|---|---|
| 載入 | `insmod notebook.ko` | 回 0；`dmesg` 有 `loaded, /proc/notebook ready` | #1 |
| 空讀 | 剛載入就 `cat /proc/notebook` | 無輸出（空 list）、不 crash | #2 |
| 單寫單讀 | `echo hi > /proc/notebook; cat /proc/notebook` | `[<pid>] hi` | #2 |
| 多寫 | 連寫三筆再 `cat` | 三行，順序為後寫先印（`list_add` 掛頭）、PID 正確 | #3 |
| 換行處理 | `echo "a" > /proc/notebook` | 印 `[pid] a`，結尾**沒有**多餘空行/亂碼 | #2 |
| 超長字串 | `echo <300字元> > /proc/notebook` | 截斷到 127 字元，不溢位、不 crash | 邊界 |
| 壞指標 | user 程式傳一個非法指標給 write | 回 `-EFAULT`，**不 leak**（kfree 過），list 不變 | 邊界 |
| 並行寫 | 兩 shell 各 `while true; do echo x > /proc/notebook; done`，跑幾秒後停、`cat` | 不 crash、list 結構完整、筆數 = 兩邊寫入總和 | #5 |
| 卸載清理 | 寫 N 筆後 `rmmod notebook` | `dmesg` 顯示 `freed N note(s)`；`/proc/notebook` 消失 | #4 |
| leak 檢查 | 卸載後 `echo scan > /sys/kernel/debug/kmemleak; cat` | 無本模組相關 leak（需 config 開 `DEBUG_KMEMLEAK`）| #4 |
| syscall（進階） | 寫 2 筆後跑 `./nb_count` | `notebook has 2 note(s)`，和 `cat` 行數一致 | #6 |

> **並行測試要看到 race 得先「弄壞它」**：想真正確認 mutex 有用，把 `mutex_lock`/`mutex_unlock` 全註解掉重編，在 QEMU 裡開 KASAN（Ch 0 config 加 `--enable KASAN`）跑並行寫入迴圈——沒鎖時 `list_add` 的 `next`/`prev` 指標會被兩個 CPU 交錯改壞，KASAN 大概率報 use-after-free 或 list 損毀（也可能剛好沒踩到，race 本來就時好時壞，這正是 Ch 7 說的「race 難以復現」）。看到它壞、加回鎖看到它好，你就真的懂為什麼要鎖了。

## 卡關時的 gdb 用法

延續 Ch 0 的 QEMU + gdb。`insmod` 後在 gdb 裡 `lx-symbols` 載入模組符號，就能停在你的函式：

```gdb
(gdb) lx-symbols
(gdb) break nb_write
(gdb) continue
```

回 QEMU 跑 `echo hi > /proc/notebook`，gdb 會停進 `nb_write`。`step` 進去看 `copy_from_user` 怎麼把字串搬進 `n->text`、`print *n` 看整個 note 長怎樣、`print current->pid` 對照 PID。這把「讀懂」變成「親眼看它跑」，正是本課的核心手法。

進階任務停 syscall：`break __x64_sys_notebook_count`（整個 syscall 入口）或 `break __do_sys_notebook_count`（你的 body，可能被 inline 停不到），跑 `./nb_count` 觀察。

## 踩雷集錦

1. **用 `file_operations` 而非 `proc_ops`**：抄舊教材最容易中的一槍。5.6 起 `/proc` 檔的 handler 表換成 `struct proc_ops`（欄位有 `proc_` 前綴），塞 `file_operations` 給 `proc_create` 第四參數型別不符、編不過。記住這個版本斷層。

2. **`.proc_write` 回傳值寫成 0 或 `copy`**：write 的語意是回「吃掉幾 bytes」。你只 copy 了 `copy` 個（因為截斷），但要回 `len`——否則 `echo` 以為沒寫完，會帶著剩下的 bytes 再呼叫你一次，你的筆記本會多出半截的重複筆記。回 `len` 表示「這 len bytes 我認了」。

3. **手寫 `.proc_read` 不處理 `*ppos`，`cat` 無限迴圈**：`cat` 會一直 read 到你回 0（EOF）為止。若你每次 read 都回一樣的內容、不推進 `*ppos`、不在讀完後回 0，`cat` 就永遠印不完。這正是我們用 `seq_file` 的理由——它幫你管 EOF 與分頁。要手寫請務必在資料讀完後回 0。

4. **卸載沒先 `proc_remove` 就開始 `kfree`**：如果你先清 list 再拔 `/proc/notebook`，中間這個空檔還有 user 能 `cat`/`echo` 進來，可能踩到你正在 free 的節點。順序是**先拔門（`proc_remove`）再清屋（`kfree`）**——`proc_remove` 會等到沒有進行中的 proc 操作才返回。

5. **臨界區內 `kmalloc(GFP_KERNEL)` 卻用 spinlock**：這是這個練習最核心的並行陷阱。spinlock 持有時 preemption 關閉、絕對不能睡，而 `GFP_KERNEL` 允許 reclaim 時阻塞、`copy_from_user` 可能因 page fault 而睡——在 spinlock 裡做這些會死鎖或觸發 `scheduling while atomic`。所以這裡只能 mutex。這不是風格選擇，是硬約束（Ch 25 詳述 spinlock 為何不能睡）。

## 延伸挑戰

1. **加「清空」與「刪一筆」**：讓 `echo clear > /proc/notebook` 清空所有筆記、`echo "del <pid>" > /proc/notebook` 刪掉指定 PID 的筆記。你會第一次面對「在 `list_for_each_entry_safe` 裡有條件地刪」的實戰。
2. **筆數上限 + LRU 淘汰**：限制最多 100 筆，滿了寫新的就 `list_del` + `kfree` 掉最舊的（`list_add_tail` 尾進、砍頭）。這就是一個迷你 LRU，page cache/dentry cache 的淘汰是同一個骨架的放大版（Ch 21）。
3. **換 misc device 介面**：把 procfs 換成 misc device（`/dev/notebook`，Ch 38 主題）。同一份 list 邏輯，不同的 user 介面，體會 procfs vs 字元裝置的取捨。
4. **把 mutex 換成 RCU 讀路徑**（做完 Part 4 再回來）：讀（`cat`）遠比寫頻繁，用 RCU 讓讀者無鎖、寫者用鎖，感受「讀多寫少」為什麼是 RCU 的主場（Ch 27）。這是把本練習升級成接近真實子系統寫法的一步。
5. **per-CPU 計數**（做完 Ch 7 延伸）：把 `nb_count` 換成 per-CPU 變數避免計數本身的 cache line bouncing，`cat` 時再加總。體會「連一個計數器都有並行成本」。

## 自我檢核

- [ ] 不看解答，能說出為什麼這裡的臨界區只能用 `mutex` 不能用 `spinlock`（關鍵字：臨界區內會睡——`GFP_KERNEL` 的 kmalloc、`copy_from_user` 的 page fault）
- [ ] 能解釋 `.proc_write` 裡為什麼不能直接讀 `ubuf`、`copy_from_user` 失敗時為什麼要先 `kfree(n)` 再回 `-EFAULT`
- [ ] 能說出卸載時為什麼一定要用 `list_for_each_entry_safe` 而非 `list_for_each_entry`（UAF：`kfree` 後不能再用 `n->next`）
- [ ] 能解釋「kernel 沒有 process 結束自動回收記憶體這回事」，所以模組漏 `kfree` 就是漏到重開機的真 leak
- [ ] 面試被問「怎麼給 kernel 加一條 syscall」，能講出改 `.tbl` + `SYSCALL_DEFINE` + 重編的正途，以及為什麼劫持 `sys_call_table` 是 rootkit 而非工程做法
- [ ] 能講清 kernel 內程式碼和後載入模組要共享一個變數，管道是 `EXPORT_SYMBOL`——這就是進階任務路 (a) 做法二的本質
- [ ] 能用 `lx-symbols` + `break nb_write` 在 gdb 裡停進自己模組的函式，`print *n` 看到筆記內容

## 這個練習把哪些章拼在了一起

- **Ch 2 執行環境**：`current->pid`、判斷「這段跑在 process context 能不能睡」
- **Ch 4 syscall**：`copy_from_user`、進階任務的自訂 syscall 全套（`.tbl` + `SYSCALL_DEFINE` + `EXPORT_SYMBOL`）
- **Ch 5 資料結構**：`list_head`、`list_add`、`list_for_each_entry`、`list_for_each_entry_safe`、`container_of` 的實戰
- **Ch 6 記憶體配置**：`kmalloc(GFP_KERNEL)` 與 GFP 選擇、`kfree` 生命週期
- **Ch 7 並行本質**：為什麼共享 list 要保護、mutex vs spinlock 的第一次抉擇（細節 Part 4）
- **Ch 8 模組載入**：`module_init`/`module_exit`、`insmod`/`rmmod` 的生命週期、`lx-symbols`

做完這個練習，你手上有一個能跑、能被 gdb 停、能被 KASAN 驗、涵蓋 Part 1 每個機制的模組。下一步我們深入 kernel 裡最核心的那個結構——每個 process 在 kernel 裡的化身、掛滿了排程/記憶體/檔案/信號所有子系統指標的 `struct task_struct`。你這個練習裡順手用的 `current->pid`，就是從它取出來的。

→ [Ch 9 task_struct 解剖](./09-task-struct.md)
