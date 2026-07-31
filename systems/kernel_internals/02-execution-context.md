# Ch 2 — Kernel 的執行環境：context、stack、current

> **目標**：建立「在 kernel 裡寫 code 和在 user space 完全是兩個世界」的世界觀。學完你能判斷手上這段程式跑在哪種 context、能不能睡、要不要用 `copy_from_user`、`current` 是怎麼在任何地方憑空拿到當前 task 的——這四個判斷是後面 51 章每一次寫模組、讀源碼的底層直覺。

## 為什麼需要這個？

你會 C，寫過 user space 程式。你以為進 kernel 只是換個 API：把 `malloc` 換成 `kmalloc`、`printf` 換成 `pr_info`。這個類比會害死你。

user space 程式活在一個被 kernel 精心佈置好的溫室裡：有 libc 幫你包好一切、有 8 MB 起跳可自動成長的 stack、崩了頂多這個 process 死掉、浮點想用就用、想睡（阻塞在 I/O）就睡、記憶體不夠 kernel 會幫你換頁。這些服務**全部是 kernel 提供的**。當你的 code 變成 kernel 的一部分，你就站到了提供這些服務的那一側——溫室的牆，現在由你來當。

具體來說，進 kernel 之後這些事全變了：

- **沒有 libc**。`printf`/`malloc`/`memcpy` 這些你以為是 C 語言內建的東西，其實是 libc 給的。kernel 不連結 libc，你只有 kernel 自己那套（`pr_info`、`kmalloc`、`memcpy` 的 kernel 版本）。
- **沒有 `main()`**。模組的入口是你用 `module_init()` 註冊的函式（Ch 0 那個 `hello_init`），kernel 在載入時呼叫它，跑完就返回——沒有一個「主迴圈」等著你。
- **stack 小到你不敢相信**。x86_64 每個 thread 的 kernel stack 通常只有 16 KB（`THREAD_SIZE`），而且不會自動長大。爆掉不是 segfault，是直接踩爛旁邊的記憶體、拖垮整台機器。
- **不能隨便睡**。user space 呼叫 `read()` 阻塞是天經地義；在 kernel 某些 context 裡「睡一下」會直接讓系統死鎖或 panic。
- **崩潰帶走全部**。user space 的 bug 是 SIGSEGV，kernel 的 bug 是 Oops 甚至 panic，可能連你的檔案系統一起帶走。

這一章要把這個新世界的規則講清楚。掌握它之前，你寫的每一行 kernel code 都是在地雷區裡蒙眼走路。

## 先建立直覺

先把三個核心概念放在一張圖裡：一個 CPU 在某個時刻，跑在某種 **context** 裡，用著某個 **kernel stack**，而 `current` 這個巨集永遠指向「此刻這顆 CPU 上的當前 task」。

```
   一顆 CPU 在任一時刻的執行狀態
   ┌──────────────────────────────────────────────────────────────┐
   │                                                                │
   │   跑在哪種 context？                能不能睡？   有 current 嗎？ │
   │   ────────────────────────         ─────────    ────────────  │
   │   process context                    能睡 ✔       有 ✔        │
   │   （代表某個 task 執行 syscall）                                │
   │                                                                │
   │   ──── 中斷來了，搶走 CPU ────────────────────────────────────│
   │                                                                │
   │   interrupt (hardirq) context        不能睡 ✘     不可靠 ✘     │
   │   softirq / tasklet context          不能睡 ✘     不可靠 ✘     │
   │   （借用被打斷那個 task 的 stack 跑，但邏輯上不屬於它）          │
   │                                                                │
   └──────────────────────────────────────────────────────────────┘

   current ──► struct task_struct  ─┬─► comm  "bash"
   （this_cpu_read / sp_el0）        ├─► pid   1337
                                     ├─► mm    使用者位址空間（Ch 19）
                                     └─► ...   （完整解剖見 Ch 9）
```

三個要記在骨子裡的判斷：

1. **這段 code 跑在哪種 context？** 決定它「能不能睡」。
2. **能不能睡？** 決定你能用哪些 API（`GFP_KERNEL` vs `GFP_ATOMIC`、mutex vs spinlock）。
3. **`current` 現在指向誰、可不可信？** process context 裡它是「發起這次呼叫的那個 task」；中斷 context 裡它只是「剛好被打斷的那個倒楣鬼」，跟你的中斷邏輯毫無關係。

這三個判斷會反覆出現在整本書。現在把地基打穩。

## kernel 裡不能做的事：一份清單

進 kernel 之前，先記住幾條「user space 能、kernel 不能（或要很小心）」的紅線。這不是規矩潔癖，每一條背後都是實實在在的崩潰。

**不能用 libc。** kernel 是獨立連結的（`-nostdlib`），它有自己的一套：字串用 `include/linux/string.h` 的 `strscpy`/`memcpy`；格式化輸出用 `pr_info`/`pr_err`（走 printk，不是 stdout）；配置記憶體用 `kmalloc`（Ch 6）。你 `#include <stdio.h>` 會直接編不過。

**不能隨便用浮點與 SIMD。** kernel 進入時預設**不保存** FPU/SIMD 暫存器狀態（為了效能，context switch 少存一大坨東西）。你在 kernel 隨手寫個 `double` 運算，會污染 user space 的 FPU 狀態，導致某個使用者程式的浮點計算莫名其妙出錯。真的要用得包在 `kernel_fpu_begin()` / `kernel_fpu_end()`（`arch/x86/kernel/fpu/core.c`）之間，成本很高。結論：kernel code 幾乎一律用整數。

**不能開大的區域陣列。** 這條直接連到下一節的主角——stack。user space 你敢 `char buf[65536];` 開在 stack 上，kernel 這樣寫是自殺。

**不能假設記憶體連續、不能假設能換頁。** user space 的 `malloc` 給你的是虛擬連續的記憶體，kernel 幫你按需分頁。kernel 裡 `kmalloc` 給的是**實體連續**（有大小上限、可能失敗），`vmalloc` 給的是虛擬連續但實體分散（Ch 6 詳解）。而且某些記憶體（例如中斷處理要碰的）根本不能被換出去。

**不能假設「這行之後我還在同一顆 CPU、同一個時間點」。** kernel 預設可搶佔（preemptible）、可被中斷。你這行跑完，下一行可能是幾毫秒後、在另一顆 CPU 上、被別的 task 插隊之後才執行的。這是後面所有並行章（Ch 24–28）的前提，本章最後一節會展開。

## kernel stack：16 KB，爆了就死

user space 的 stack 起手 8 MB，不夠還能自動長（guard page 觸發 growth）。kernel 給每個 thread 的 kernel stack 是**固定小塊**：x86_64 上 `THREAD_SIZE` 通常是 16 KB（`arch/x86/include/asm/page_64_types.h`，定義為 `4 * PAGE_SIZE`，也就是 4 個 4 KB page）。

為什麼這麼小？因為系統裡可能有**幾萬個 thread**，每個 thread 都要一塊 kernel stack。如果每塊 8 MB，光 stack 就吃光記憶體。16 KB × 上萬 thread 才是可承受的量。這是一個典型的 kernel 設計取捨：**用嚴格的紀律換取可擴展性**。

代價是：這 16 KB 要裝下整條 kernel 呼叫鏈——syscall 進來、走過 VFS、走過檔案系統、走過 block layer、也許還被一個中斷插進來借用同一條 stack。深遞迴、大區域變數在這裡都是奢侈品。

```
   x86_64 kernel stack（一個 thread，16 KB = THREAD_SIZE）

   高位址 ┌────────────────────────────┐
          │  __x64_sys_write 的 frame  │  ← syscall 入口
          │  vfs_write 的 frame        │
          │  ...檔案系統...             │  往低位址長
          │  ...block layer...         │  ↓
          │                            │
          │   ← 還剩多少？不多。        │
          ├────────────────────────────┤
          │  struct thread_info（部分   │  stack 底部附近放
          │  架構）/ stack guard        │  執行緒中繼資料
   低位址 └────────────────────────────┘
          再往下踩 = stack overflow = 踩爛別的記憶體
```

**爆 stack 的後果比 user space 惡劣得多。** user space 爆 stack 會撞到 guard page，乖乖收到 SIGSEGV。kernel stack 傳統上下面**沒有可靠的 guard**（`CONFIG_VMAP_STACK` 起，x86_64 會用 vmalloc 空間配 stack 並加 guard page，能把「靜默踩壞」變成「明確的 kernel Oops」，但你仍然是崩潰，只是崩得比較體面）。踩過界你可能直接改寫到相鄰 thread 的 `thread_info` 或別人的資料，症狀是幾秒後在一個完全無關的地方莫名其妙 panic——最難 debug 的那種 bug。

實務守則：

- **大 buffer 用 `kmalloc` 配到 heap，不要開在 stack。**
- **避免深遞迴。** kernel code 幾乎不寫遞迴，要嘛改迴圈，要嘛明確限制深度。
- **函式別開幾百 bytes 的區域變數。** 編譯時 `-Wframe-larger-than=`（kernel 預設對過大的 frame 會警告）會抓你。

## 三種執行 context：能不能睡是核心判斷

「這段 code 能不能睡（sleep / block）？」是 kernel 程式設計最重要的單一問題。答案完全由**你跑在哪種 context** 決定。

### Process context（行程脈絡）

CPU 正代表某個 task 執行 kernel code——最典型的是**該 task 發了一個 syscall**，現在 kernel 在幫它做事（例如它呼叫 `read()`，kernel 跑在 VFS 讀取路徑上）。你的模組的 `init` 函式、file operations 的 `.read`/`.write` handler、大部分驅動程式的一般路徑，都在 process context。

特徵：

- **能睡。** 可以呼叫會阻塞的函式（等 I/O、拿 mutex、`GFP_KERNEL` 配記憶體可能觸發回收而睡）。因為你背後有個 task，睡著了 scheduler 就把 CPU 讓給別人，醒了再排回來。
- **`current` 有意義且可信。** 它就是發起這次呼叫的那個 task。你可以放心讀 `current->pid`、`current->mm`、`current->comm`。
- **可被搶佔。** 除非你手動關搶佔或拿了 spinlock，否則隨時可能被排程換走。

### 中斷 context（interrupt / hardirq context）

硬體中斷來了（網卡收到封包、磁碟完成 I/O、timer 到期），CPU **立刻放下手邊的事**去跑中斷處理常式（interrupt handler，Ch 29 詳解）。這段程式碼跑在中斷 context。

特徵，全部是「禁止」：

- **絕對不能睡。** 這是鐵律。中斷 handler 不屬於任何 task——它是**借用**「剛好被打斷的那個 task」的 kernel stack 在跑的（見下圖）。如果你在這裡睡著，scheduler 想換走你……換走誰？這裡沒有一個「可以被排回來」的 task 實體對應你的中斷邏輯。結果是死鎖或 panic。
- **`current` 不可信。** 它指向的是「剛好被中斷打斷的那個倒楣 task」，跟你的中斷邏輯毫無關係。在中斷 handler 裡讀 `current->pid` 拿到的是隨機某個 process 的 pid，不是「誰觸發了這個中斷」。**中斷沒有「發起者 task」這個概念。**
- **要快。** 中斷期間可能關著其他中斷，你賴著不走整個系統的反應都被你卡住。所以 kernel 把「非做不可、要快」的部分放在中斷 handler（top half），「可以晚點做」的推遲到 softirq/tasklet/workqueue（bottom half，Ch 30）。

```
   中斷「借用」stack 的畫面

   bash 這個 task 正在跑 read() syscall（process context）
   ┌──────────────┐
   │ bash 的       │   ← current == bash，能睡
   │ kernel stack  │
   │  vfs_read...  │
   │       ▲       │
   │       │ 網卡中斷「碰！」插進來
   │  ┌────┴─────┐ │
   │  │ IRQ frame │ │   ← 中斷 handler 疊在 bash 的 stack 上跑
   │  │（借用中） │ │      current 還是「bash」，但這毫無意義
   │  └──────────┘ │      在這裡不能睡：睡了 bash 就再也醒不過來
   └──────────────┘      （新版 x86_64 中斷改用獨立 IRQ stack，
                          但「不能睡、current 不可信」的結論不變）
```

### Softirq / tasklet / atomic context

介於兩者之間、但實務上和中斷 context 歸為同一類「**不能睡**」的原子脈絡（atomic context）。softirq 與 tasklet（Ch 30）是中斷的 bottom half，跑在中斷返回後的一個特殊階段，同樣**不能睡**。

還有一種是你**自己造出來**的原子 context：只要你**持有 spinlock**（Ch 25）或**手動 `preempt_disable()`**，你就進入了不可睡的狀態——即使原本在 process context。拿著 spinlock 睡著是經典的死鎖來源：你睡了不放鎖，另一顆 CPU 在那個 spinlock 上空轉等你，永遠等不到。

### 一張判斷表

| Context | 能睡？ | `current` 可信？ | 能用的配置 flag | 能用的鎖 |
|---|---|---|---|---|
| process context | 能 | 是 | `GFP_KERNEL`（可睡） | mutex、semaphore、spinlock 都行 |
| 持 spinlock / `preempt_disable` | 不能 | 是（但無意義） | `GFP_ATOMIC`（不睡） | 只能 spinlock（不可睡鎖） |
| softirq / tasklet | 不能 | 否 | `GFP_ATOMIC` | 只能 spinlock |
| 中斷（hardirq） | 不能 | 否 | `GFP_ATOMIC` | 只能 spinlock（且要 `_irqsave` 版） |

這張表是往後好幾章的索引：「能不能睡」直接決定 Ch 6 你選 `GFP_KERNEL` 還是 `GFP_ATOMIC`、Ch 26 你選可睡的 mutex 還是 Ch 25 不可睡的 spinlock。現在不用背細節，記住**判斷的軸線是 context → 能不能睡 → 選什麼工具**。

## 底層機制：`current` 怎麼在任何地方拿到當前 task

`current` 是 kernel 裡最常見的巨集之一。你在任何函式裡寫 `current->pid` 就拿到當前行程的 pid，不用傳參數、不用查表。它怎麼做到的？

先想清楚問題的難處：kernel 是**單一份程式碼被所有 CPU、所有 task 共用執行**的。同一個 `vfs_read` 函式，此刻 CPU 0 上是 bash 在跑、CPU 3 上是 nginx 在跑。`current` 得根據「是哪顆 CPU 在問」給出不同答案。這正是 **per-CPU 變數**（Ch 7 詳解）的用武之地：每顆 CPU 有自己的一份 `current_task`，指向「這顆 CPU 現在跑的 task」。context switch（Ch 14）時，`__switch_to` 會更新這個 per-CPU 指標。

### x86_64：per-CPU 的 `pcpu_hot.current_task`

看 `arch/x86/include/asm/current.h`。x86_64 的 `current` 展開成大致：

```c
#define current  get_current()

static __always_inline struct task_struct *get_current(void)
{
    return this_cpu_read_stable(pcpu_hot.current_task);
}
```

`pcpu_hot` 是一個 per-CPU 結構，把最常存取的幾個欄位（`current_task`、`preempt_count`、當前 CPU 編號等）**放在同一條 cache line 上**，因為它們每次進出 kernel 都要碰，擠在一起對 cache 友善。`this_cpu_read`（Ch 7）在 x86_64 上編譯成**一條指令**：用 `%gs` 段暫存器當基底去讀 per-CPU 區域裡的 `current_task`。所以 `current` 幾乎零成本——不是查表、不是走鏈結串列，就是一次 `%gs` 相對定址的記憶體讀取。

> **6.x 起的細節**：把這些熱欄位打包進 `pcpu_hot` 是相對近期的重構（把散落的 per-CPU 熱變數收攏成一個結構以優化 cache line）。你在較舊的 kernel 看到的是獨立的 `current_task` per-CPU 變數，機制相同（`%gs` 相對讀取），只是欄位的組織方式不同。

### ARM64：`sp_el0` 暫存器

看 `arch/arm64/include/asm/current.h`。ARM64 用完全不同的手法：把當前 task 指標**存在 `sp_el0` 這個系統暫存器裡**。

```c
static __always_inline struct task_struct *get_current(void)
{
    unsigned long sp_el0;
    asm ("mrs %0, sp_el0" : "=r" (sp_el0));   // 從 sp_el0 讀出 task 指標
    return (struct task_struct *)sp_el0;
}
```

`sp_el0` 原本是「EL0（使用者態）的 stack pointer」。但 kernel 跑在 EL1（核心態），用的是 `sp_el1`，`sp_el0` 這個暫存器在核心態閒著沒用——於是 ARM64 kernel 徵用它來存 `current` 指標。`mrs`（move from system register）一條指令就讀出來，同樣幾乎零成本。context switch 時更新 `sp_el0`。

**x86_64 vs ARM64 的差異點**：兩者都要「per-CPU 地拿到當前 task」，但實作路徑不同——x86_64 走 per-CPU 記憶體（`%gs` 段 + `pcpu_hot`），ARM64 走一個被徵用的**系統暫存器**（`sp_el0`）。結果一樣：一條指令、零查表。你只要記得 `current` 不是魔法，它背後是「當前 CPU 的一個固定位置存著 task 指標」，x86 那個位置在 per-CPU 記憶體、ARM64 那個位置在 `sp_el0` 暫存器。這種「同一個抽象、各架構各自實作」的模式，是後面 context switch（Ch 14）、page table（Ch 16）、memory barrier（Ch 23）章節反覆出現的主題。

拿到 `current` 之後，它指向 `struct task_struct`（定義在 `include/linux/sched.h`）——kernel 描述「一個 task 的一切」的巨型結構，pid、狀態、記憶體、開啟的檔案、排程資訊全在裡面。這一章我們只碰它的表皮（`comm`、`pid`），完整解剖是 Ch 9。

## 使用者／核心邊界：user pointer 不能直接碰

process context 裡 `current` 指向的 task 有它自己的**使用者位址空間**（`current->mm`，Ch 19）。當一個 syscall 帶進來一個指標參數（例如 `read(fd, buf, len)` 的 `buf`），這個 `buf` 是**使用者空間的位址**。kernel 能不能直接 `*buf` 解參考？

**不能。而且這是安全與穩定的紅線。** 三個理由：

1. **那個位址可能根本沒對映、或指向 kernel 空間。** user 傳個亂指標甚至故意傳一個 kernel 位址進來，你直接解參考，輕則 Oops，重則被利用來讀寫 kernel 記憶體——這正是 `kernel_pwn` 課裡一整類漏洞的根源。
2. **那塊 user 記憶體可能還沒載入（需要 page fault 換頁）。** 直接存取要能安全地處理 fault。
3. **user 可能在你檢查後、使用前偷偷改掉它**（TOCTOU / double-fetch，`kernel_pwn` 常見攻擊面）。

所以 kernel 規定：**跨越 user/kernel 邊界搬資料，只能透過專用函式**——`copy_from_user()`（user → kernel）、`copy_to_user()`（kernel → user），定義在 `include/linux/uaccess.h`。它們做三件你自己 `memcpy` 不會做的事：

- **驗證**這個 user 位址範圍合法（`access_ok`：確實落在 user 空間、沒越界）。
- **安全地處理 page fault**：搬運過程若目標頁還沒載入，能觸發換頁而不是崩潰（靠 exception table，Ch 4 會碰到）。
- **回傳「還有幾 bytes 沒搬成」**——回傳 0 才是全部成功，非 0 代表部分失敗，你**必須檢查**。

```c
// file operation 的 .read handler，典型 process context
static ssize_t my_read(struct file *f, char __user *ubuf,
                       size_t len, loff_t *off)
{
    char kbuf[64];
    int n = snprintf(kbuf, sizeof(kbuf), "pid=%d\n", current->pid);

    if (len < n)
        n = len;
    // 不能 memcpy(ubuf, kbuf, n)！ubuf 是 user 指標
    if (copy_to_user(ubuf, kbuf, n))   // 回傳非 0 = 有 bytes 沒搬成
        return -EFAULT;                // 標準錯誤：user 位址壞掉
    return n;
}
```

注意那個 `__user` 標註：kernel 用 `sparse` 靜態分析工具追蹤哪些指標是 user 空間的，你若不小心直接解參考一個 `__user` 指標，`make C=1` 會警告你。這是 kernel 把「別碰 user 指標」這條紀律**編進型別系統**的方式。

> `read()`/`write()` 的完整路徑（syscall 怎麼進來、`__user` 指標怎麼一路傳到這裡）是 Ch 4（syscall 機制）和 Ch 34（read 路徑）的主題。這裡先建立「邊界只能用 `copy_*_user` 穿越」的鐵律。

## preemption：你的 code 隨時會被打斷

最後一塊世界觀：**Linux kernel 預設是可搶佔的（preemptible）。** 你寫的 kernel code，跑到一半可能：

- 被**硬體中斷**打斷（去跑中斷 handler，回來繼續）；
- 被**搶佔**——scheduler 決定讓更高優先權的 task 上場，把你這個 task 換下去（`CONFIG_PREEMPT` 系列，Ch 14）。

換句話說，你不能假設任何一段 kernel code 是「一口氣跑完、中間沒人插手」的。這正是為什麼 kernel 需要那一整套並行原語（atomic、spinlock、RCU，Ch 24–28）——因為**共享資料隨時可能被另一顆 CPU 或被搶佔進來的另一個 task 同時碰**。

kernel 用一個 per-CPU 的 **`preempt_count`**（`include/linux/preempt.h`）追蹤「現在能不能被搶佔／在不在原子 context」。它不是單純計數，是把幾件事打包進一個整數的不同 bit 欄位：搶佔停用的巢狀次數、hardirq 巢狀次數、softirq 巢狀次數。相關巨集：

- `preempt_disable()` / `preempt_enable()`：手動關/開搶佔（把 `preempt_count` 加一/減一）。拿 spinlock 時內部就會呼叫它。
- `in_interrupt()`：現在在中斷或 softirq context 嗎？（查 `preempt_count` 的 hardirq/softirq 欄位）
- `in_atomic()`：現在在原子 context 嗎（不能睡）？（查整個 `preempt_count` 是否非零）

`preempt_count` 為什麼要塞進 `pcpu_hot`（前面 `current` 那節提過）？因為它每次進出中斷、每次拿放鎖都要改，是最熱的 per-CPU 資料之一，和 `current_task` 擠同一條 cache line。

> `in_atomic()` 有個常見誤用要先打預防針：它**只在啟用了搶佔計數的 config 下**能可靠地告訴你「持有 spinlock」。用它來「猜自己能不能睡」在某些 config 下不準。判斷能不能睡的正確方式是**靠你對自己所在 context 的認知**（我在中斷 handler 裡嗎？我持著 spinlock 嗎？），而不是 runtime 去問 `in_atomic()`。這個坑放進踩雷集錦。

## 動手：印 current，然後故意在中斷 context 裡睡

用一個模組把這章的三個概念——`current`、context、「不能睡」——親手驗證一遍。沿用 Ch 0 的 QEMU + gdb 環境。

### 第一部分：process context 裡印 current

模組的 `init` 函式跑在 process context（是 `insmod` 這個行程觸發載入的）。在這裡 `current` 就是 `insmod`：

```c
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/sched.h>      // struct task_struct、current

static int __init ctx_init(void)
{
    pr_info("ctx: in process context\n");
    pr_info("ctx: current->comm = %s\n", current->comm);   // 觸發載入的行程名，通常 "insmod"
    pr_info("ctx: current->pid  = %d\n", current->pid);
    pr_info("ctx: in_atomic()   = %d\n", in_atomic());     // process context 且沒持鎖 → 0
    pr_info("ctx: in_interrupt()= %d\n", in_interrupt());  // 不在中斷 → 0
    return 0;
}

static void __exit ctx_exit(void)
{
    pr_info("ctx: current->comm = %s (unloading)\n", current->comm);  // 通常 "rmmod"
}

module_init(ctx_init);
module_exit(ctx_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Ch2: execution context demo");
```

編、載入、看 `dmesg`：

```
/ # insmod /ctx.ko
ctx: in process context
ctx: current->comm = insmod
ctx: current->pid  = 71
ctx: in_atomic()   = 0
ctx: in_interrupt()= 0
```

`current->comm` 印出 `insmod`——這就是「`current` 是發起這次呼叫的 task」的直接證據。`rmmod` 時再看，`comm` 變成 `rmmod`（或視 busybox 實作而定的行程名）。

### 第二部分：用 gdb 從另一側看 current

不改 code，用 Ch 0 的 gdb 環境驗證同一件事。QEMU 加 `-S -s` 開機，gdb 接上後：

```gdb
(gdb) break ctx_init          # 需要先 lx-symbols 載入模組符號（Ch 0/Ch 8）
(gdb) continue
(gdb) print current->comm     # gdb 直接讀 current 指向的 task_struct
$1 = "insmod\000..."
(gdb) print current->pid
$2 = 71
(gdb) print current->mm       # process context 有使用者位址空間（非 NULL）
```

你從 kernel code 裡（`pr_info`）和從 gdb 外面（`print current->`）看到**同一個 task**，這確認了 `current` 不是什麼玄學，就是一個當下可讀的指標。

### 第三部分：在中斷 context 裡呼叫會睡的函式，看它爆

這是本章最有教育意義的一步：**親手違反「中斷不能睡」的規則，看 kernel 怎麼罵你。** 我們用一個 timer callback（跑在 softirq context，同樣禁止睡眠）故意呼叫一個會睡的函式 `msleep()`：

```c
#include <linux/timer.h>
#include <linux/delay.h>      // msleep（會睡）

static struct timer_list bad_timer;

static void bad_timer_fn(struct timer_list *t)
{
    // 這裡是 softirq context：in_interrupt() 為真、current 不可信、不能睡
    pr_info("ctx: timer ctx in_interrupt()=%lu\n", in_interrupt());
    msleep(100);              // 罪證：在原子 context 呼叫會睡的函式
    pr_info("ctx: 這行大概印不出來，因為上面就爆了\n");
}

// 在 ctx_init 裡加：
//   timer_setup(&bad_timer, bad_timer_fn, 0);
//   mod_timer(&bad_timer, jiffies + msecs_to_jiffies(500));
// 在 ctx_exit 裡加：
//   timer_delete_sync(&bad_timer);
```

載入後約 0.5 秒，`dmesg` 會噴出類似：

```
BUG: scheduling while atomic: swapper/0/0/0x00000100
...
Call Trace:
 __schedule
 schedule
 schedule_timeout
 msleep
 bad_timer_fn
 ...
```

`scheduling while atomic` 就是 kernel 的「你在不能睡的地方睡了」的官方報錯——`msleep` 想睡（呼叫 `schedule` 讓出 CPU），但當前 `preempt_count` 顯示在原子 context，kernel 的 `__schedule` 偵測到這個矛盾，直接發 BUG。你也會注意到報錯裡的行程是 `swapper/0`（idle task）——這正是「中斷 context 裡 `current` 不可信」的鐵證：觸發 timer 的不是任何有意義的 task，`current` 只是剛好被打斷的 idle task。

（想開更嚴格的偵測，config 開 `CONFIG_DEBUG_ATOMIC_SLEEP`，它會在「原子 context 呼叫可能睡的函式」時更早、更明確地叫出來。生產 kernel 為了效能通常關掉，除錯 kernel 一定開。）

親手看過這個 BUG，你對「context 決定能不能睡」就不再是背條文，而是有畫面。

## 對比與取捨

| 主題 | user space 的世界 | kernel 的世界 |
|---|---|---|
| 標準庫 | libc（printf/malloc/…） | 沒有，只有 kernel 內部 API（pr_info/kmalloc/…） |
| 入口 | `main()` | `module_init()` 註冊的函式，跑完即返回 |
| stack | 8 MB 起、可自動成長 | 固定 16 KB（`THREAD_SIZE`），不成長，爆了就崩 |
| 浮點 | 隨便用 | 幾乎禁用，要用得 `kernel_fpu_begin/end` 且成本高 |
| 阻塞 / 睡眠 | 任何時候都行 | 只有 process context 能睡，中斷/原子 context 睡了就死 |
| 拿「我是誰」 | `getpid()`（syscall） | `current`（一條指令，per-CPU / sp_el0） |
| 存取任意指標 | 直接解參考 | user 指標只能 `copy_from/to_user`，還要檢查回傳值 |
| 被打斷 | 感覺不到（kernel 幫你隱藏） | 隨時被中斷/搶佔，共享資料要自己上鎖 |
| bug 的後果 | SIGSEGV，死一個 process | Oops / panic，可能帶走整台機器 |

## 踩雷集錦

1. **錯誤直覺：「kernel 就是 C，`printf`/`malloc` 應該能用」。** → 正確認識：kernel 不連結 libc。用 `pr_info`（走 printk）、`kmalloc`（Ch 6），`#include <stdio.h>` 直接編不過。你熟的「C 標準庫」其實是 libc 提供的服務，不是語言本身。

2. **錯誤直覺：「開個 `char buf[8192]` 在 stack 上沒事」。** → 正確認識：kernel stack 只有 16 KB，還要裝整條呼叫鏈加可能插進來的中斷。大 buffer 一律 `kmalloc` 到 heap。爆 kernel stack 不是 SIGSEGV，是踩爛別人記憶體後在無關的地方神秘 panic，最難查。

3. **錯誤直覺：「中斷 handler 裡 `current` 就是觸發中斷的那個 process」。** → 正確認識：中斷沒有「發起者 task」。`current` 在中斷 context 指向的是「剛好被打斷的隨機 task」（常常是 idle task `swapper`），跟你的中斷邏輯毫無關係，讀它的 pid/mm 是無意義甚至危險的。

4. **錯誤直覺：「持著 spinlock，睡一下應該還好」。** → 正確認識：拿 spinlock 就進了原子 context，睡眠（包括 `GFP_KERNEL` 配記憶體、`mutex_lock`、`copy_from_user` 可能觸發的換頁睡眠）會導致 `scheduling while atomic` 甚至死鎖。持鎖期間只能用不會睡的操作（`GFP_ATOMIC` 等）。

5. **錯誤直覺：「用 `in_atomic()` 判斷自己現在能不能睡」。** → 正確認識：`in_atomic()` 在未啟用搶佔計數的 config 下對「持有 spinlock」不可靠。判斷能不能睡要靠**你對自己所處 context 的認知**（我在中斷/softirq 裡嗎？我持鎖嗎？），不是 runtime 去問它。想在開發期抓睡眠違規，開 `CONFIG_DEBUG_ATOMIC_SLEEP`。

## 進階：再往深一層

- **中斷有自己的 stack（近代 x86_64）**：早期中斷 handler 直接借用被打斷 task 的 kernel stack（前面 ASCII 圖那樣），代價是佔用本來就緊的 16 KB。近代 x86_64 為 hardirq 配了獨立的 IRQ stack（per-CPU），把中斷的 stack 使用和 process 的分開。這改變了「借用 stack」的細節，但「中斷不能睡、`current` 不可信」的結論**不變**——那是 context 的性質，不是 stack 佈局決定的。

- **`preempt_count` 的 bit 佈局**：它不是單一計數器，而是把 preempt-disable 巢狀數、softirq 巢狀數、hardirq 巢狀數塞進同一個 32-bit 整數的不同欄位（見 `include/linux/preempt.h` 的 `PREEMPT_BITS`/`SOFTIRQ_BITS`/`HARDIRQ_BITS` 巨集）。`in_interrupt()`、`in_softirq()`、`in_task()` 都是對這些欄位的位元查詢。理解這個佈局，你就懂為什麼一條指令的檢查能同時回答「在哪種 context」。

- **`CONFIG_PREEMPT` 家族**：Linux 有好幾種搶佔模型——`PREEMPT_NONE`（伺服器，吞吐優先）、`PREEMPT_VOLUNTARY`、`PREEMPT`（桌面/低延遲）、以及併入主線的 `PREEMPT_RT`（硬即時，把大部分 spinlock 變成可睡的、中斷 threaded 化）。你的 code 要能在所有模型下正確，就得假設「隨時可能被搶佔」。這是 Ch 14（context switch/preemption）和 Ch 31（threaded IRQ / -rt）的主題。

- **面試常問**：「`GFP_KERNEL` 和 `GFP_ATOMIC` 差在哪、什麼時候用哪個？」——答案的根就在這章：`GFP_KERNEL` 可能睡（會觸發回收），只能在 process context 用；`GFP_ATOMIC` 保證不睡，用在中斷/持鎖等原子 context，代價是更容易配置失敗。你能不能答好這題，取決於你有沒有把「context → 能不能睡」這條軸線內化。Ch 6 會把配置這一側講透。

## 動手練習

1. **驗證 `current` 換人**：跑第一部分的模組，`insmod` 看 `current->comm` 是 `insmod`，`rmmod` 看它變 `rmmod`（或對應行程名）。想一想為什麼 `init` 和 `exit` 的 `current` 不同——它們分別由誰觸發？

2. **gdb 對照**：用第二部分的方法，在 gdb 裡 `print current->comm` / `current->pid`，和模組 `pr_info` 印出的值對照，確認兩側看到同一個 task。順便 `print current->mm`，觀察 process context 下它非 NULL。

3. **弄壞它（本章重點練習）**：跑第三部分的 timer 版本，在 `dmesg` 抓到 `BUG: scheduling while atomic`。看 call trace，指認出 `msleep → schedule` 這條「試圖睡眠」的路徑，以及報錯行程是 `swapper`（idle task）而非任何真實 process——親眼確認「中斷 context 不能睡、`current` 不可信」。

4. **加碼**：把 config 開 `CONFIG_DEBUG_ATOMIC_SLEEP` 重編 kernel，再跑一次練習 3，比較報錯訊息有沒有更早、更明確。體會為什麼除錯 kernel 要開這個選項、生產 kernel 為什麼關掉。

5. **量 stack**（選做）：寫一個模組在 process context 呼叫時，用 `pr_info` 印出區域變數的位址，估算此刻離 stack 底還有多少空間。試著逐步加大一個 stack 上的陣列，觀察 build 時 `-Wframe-larger-than` 的警告什麼時候跳出來。

## 本章重點整理

- kernel 不是「加了前綴的 user space C」：沒有 libc、沒有 `main`、幾乎禁浮點、kernel stack 只有 16 KB（`THREAD_SIZE`）爆了就崩、bug 會帶走整台機器。
- **能不能睡是 kernel 程式設計的核心判斷**，由 context 決定：process context 能睡且 `current` 可信；中斷/softirq/持鎖等原子 context 不能睡、`current` 不可信。這條軸線直通 Ch 6（GFP flag）和 Ch 25/26（spinlock vs mutex）。
- `current` 是「當前 CPU 的固定位置存著的 task 指標」——x86_64 走 per-CPU 的 `pcpu_hot.current_task`（`%gs` 相對讀取），ARM64 走被徵用的 `sp_el0` 暫存器，都是一條指令、零查表。
- user/kernel 邊界只能用 `copy_from_user`/`copy_to_user` 穿越，且**必須檢查回傳值**；直接解參考 user 指標是穩定性與安全的紅線（kernel_pwn 一整類漏洞的源頭）。
- kernel 預設可搶佔、可被中斷，你的 code 隨時被打斷——這是後面所有並行章（Ch 24–28）的前提；`preempt_count` 是追蹤「能不能搶佔／在不在原子 context」的 per-CPU 計數。

## 自我檢核

- [ ] 不看筆記，能說出「這段 code 能不能睡」該怎麼判斷，以及判斷結果會影響你選哪些 API
- [ ] 能解釋 process context 和中斷 context 裡 `current` 的意義差在哪，為什麼中斷 context 讀 `current->pid` 是無意義的
- [ ] 能說出 x86_64 和 ARM64 各自怎麼實作 `current`（`pcpu_hot`/`%gs` vs `sp_el0`），以及它們的共同抽象是什麼
- [ ] 面試被問「`GFP_KERNEL` vs `GFP_ATOMIC` 什麼時候用哪個」，你能從 context/能不能睡的角度答出來
- [ ] 能說出為什麼 kernel code 不敢開大區域陣列、爆 kernel stack 的後果為什麼比 user space 惡劣
- [ ] 能解釋為什麼 user 指標不能直接解參考，`copy_from_user` 多做了哪三件事

## 延伸閱讀

### 官方文件

- **[Documentation/core-api/local_ops.rst](https://www.kernel.org/doc/html/latest/core-api/local_ops.html)** — kernel 官方文件
  - **讀哪裡**：關於 per-CPU 操作與 `this_cpu_*` 的部分
  - **和本章的關聯**：補充 `current` 背後的 per-CPU 機制；深入版是 Ch 7

- **[Documentation/RCU/checklist.rst 與 Documentation/locking/](https://www.kernel.org/doc/html/latest/locking/index.html)** — kernel locking 文件
  - **讀哪裡**：先看 locking 索引頁對「哪些 context 能拿哪種鎖」的說明
  - **能學到什麼**：把本章「context → 能不能睡 → 選什麼鎖」的軸線用官方語言講一遍，為 Ch 24–28 鋪路

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love（Addison-Wesley, 2010）
  - **讀哪裡**：Ch 3（Process Management，講 `current`/`task_struct`/`thread_info`）與 Ch 7–8（中斷與 bottom half，講中斷 context 的限制）
  - **這本書的定位**：本章的「world view」很多來自這裡；Love 講「process context vs interrupt context」講得最清楚。注意版本較舊，`current` 的具體實作以本章 6.12 為準

- **《Understanding the Linux Kernel, 3rd Ed.》** — Bovet & Cesati（O'Reilly, 2005）
  - **讀哪裡**：Ch 3（Processes）談 kernel stack 與 `thread_info` 的佈局
  - **注意**：對應 2.6 早期 kernel，stack/`thread_info` 佈局細節已變（近代用 `pcpu_hot`、VMAP_STACK），但「kernel stack 很小、要省著用」的原理不變

### 線上資源

- **[Bootlin Elixir：arch/x86/include/asm/current.h（v6.12）](https://elixir.bootlin.com/linux/v6.12/source/arch/x86/include/asm/current.h)** 與 **[arch/arm64 版本](https://elixir.bootlin.com/linux/v6.12/source/arch/arm64/include/asm/current.h)**
  - **這是什麼**：本章 `current` 兩種實作的原始碼
  - **怎麼讀**：對照本章的 x86_64/ARM64 兩節，親眼看 `this_cpu_read(pcpu_hot.current_task)` 和 `mrs %0, sp_el0` 這兩行；再點進 `include/linux/preempt.h` 看 `preempt_count` 的 bit 定義

有了 context / stack / current 這套世界觀，你已經知道 kernel code 活在什麼樣的環境裡。下一章我們回到最開頭：這一切是怎麼建立起來的——從 bootloader 交棒給 kernel，`start_kernel` 如何一步步把這個執行環境（per-CPU、scheduler、init task）搭起來，最後跑起第一個使用者行程。

→ [Ch 3 開機流程：從 start_kernel 到第一個行程](./03-kernel-boot-flow.md)
