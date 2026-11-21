# Ch 3 — kernel C 與執行環境：無 libc、GFP flags、per-cpu、atomic/sleepable context

> **目標**：理解「在 kernel 裡寫 C」和「在 user-space 寫 C」的根本差異——沒有 libc 能用、記憶體配置要帶著「這塊記憶體給誰用、能不能睡眠」的上下文資訊、per-cpu 變數避免鎖競爭、錯誤處理與參考計數的慣例。這些是後面每一個 vulnerable module 範例共同依賴的基礎知識,寫錯這些,module 編不過、或編得過但一跑就崩潰（而且崩潰原因往往不是你以為的那個 bug）。

> **環境**：Linux 6.6，x86-64，所有範例片段假設在 Ch 0 建好的 kernel module 編譯環境下編譯（`make -C /path/to/linux-6.6 M=$(pwd) modules`）。

## 為什麼需要這一章？

你在 user-space 寫 C，`malloc`、`printf`、`strlen` 這些函式背後是 glibc（或其他 libc 實作），它們處理了一大堆你看不到的細節：記憶體配置的底層 `brk`/`mmap` 管理、buffered I/O、locale……kernel 程式碼**沒有這層**。Kernel 本身就是「下面已經沒有作業系統可以幫你」的那一層——它要自己實作所有這些基礎設施，而且是用一套**完全不同的規則**實作的,因為 kernel 的執行環境有一些 user-space 沒有的限制：它可能在「不能睡眠」的 context 裡執行（例如 interrupt handler），它要在多核心同時執行的情況下保持資料一致，它沒有「程式結束就釋放所有資源」這種便宜的安全網（kernel 一旦洩漏記憶體，直到下次重開機都洩漏著）。

這些限制具體化成一套你寫 kernel module 時必須遵守的規則。這章把規則講清楚——不是因為規則本身多有趣，是因為**違反這些規則是 kernel module 最常見的「意外」崩潰來源**，分不清哪個崩潰是你故意種的漏洞、哪個是你不小心違反了執行環境規則,會讓後面每一章的除錯變得很痛苦。

## 先建立直覺：kernel 是「沒有操作系統的程式」

```
user-space 程式的世界觀：
┌─────────────────────────────────┐
│  你的程式                        │
│  malloc() ─────┐                 │
│  printf() ─────┼──▶ libc ──▶ syscall ──▶ kernel 替你做事
│  pthread_*() ──┘                 │
└─────────────────────────────────┘
   你永遠有一層 libc + kernel 幫你兜底

kernel 程式碼的世界觀：
┌─────────────────────────────────┐
│  kernel 自己的程式碼              │
│  kmalloc() ──▶ 直接操作 SLUB     │
│  printk() ───▶ 直接寫 log buffer │
│  spin_lock() ─▶ 直接操作 CPU 層  │
│                 的同步原語        │
└─────────────────────────────────┘
   沒有下面一層兜底,你自己就是「系統」
```

這個心智模型解釋了為什麼 kernel 有一整套看起來「多此一舉」的 API——`kmalloc` 而不是 `malloc`，`printk` 而不是 `printf`，因為下面真的沒有別人可以幫你實作 `malloc`/`printf` 依賴的那些機制（記憶體管理本身就是 kernel 在做的事,kernel 不能呼叫自己尚未存在的服務）。

## 沒有 libc：kernel 自己的核心 API

### 記憶體配置：`kmalloc`/`kfree`

```c
#include <linux/slab.h>

void *buf = kmalloc(256, GFP_KERNEL);
if (!buf)
        return -ENOMEM;
/* ... 使用 buf ... */
kfree(buf);
```

`kmalloc` 和 `malloc` 表面相似（給大小、拿指標），但底層完全不同——`malloc` 的實作（glibc 的 ptmalloc2 / tcache）管理的是 user-space 的虛擬記憶體；`kmalloc` 直接向 **SLUB allocator**（Ch 8 整章專講）要記憶體，拿到的是**實體記憶體直接對應（physically contiguous）**的一塊——這個特性之後會變得很重要：kernel exploit 裡大量的 heap spray/grooming 技巧，都是建立在「knowing roughly which SLUB cache a given size lands in」這個事實上,glibc 的 tcache 技巧在這裡完全用不上。

第二個參數 `GFP_KERNEL` 是本章稍後細講的 **GFP flags**——它不是可選參數，是在告訴 allocator「你現在在什麼 context 下要這塊記憶體」。

### 輸出：`printk`

```c
printk(KERN_INFO "my_module: got value %d\n", value);
```

`printk` 寫進一個 kernel 內部的 ring buffer（log buffer），不是直接寫到某個終端。`KERN_INFO` 這類前綴是**日誌等級**（log level，從 `KERN_EMERG` 最嚴重到 `KERN_DEBUG` 最瑣碎），決定這條訊息要不要同時顯示在 console 上（取決於目前的 console log level 設定）。你在 Ch 0 的 QEMU 環境裡看到的 `dmesg` 輸出,就是在讀這個 ring buffer。

6.6 之後建議用更簡潔的 `pr_info()`/`pr_err()`/`pr_debug()` 系列宏（定義在 `include/linux/printk.h`），它們自動幫你插入正確的 log level 前綴，並且常常會自動帶上模組名稱前綴（如果你在檔案開頭定義了 `#define pr_fmt(fmt) "my_module: " fmt`）：

```c
#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt
#include <linux/printk.h>

pr_info("got value %d\n", value);
pr_err("allocation failed\n");
```

### 字串與記憶體操作：`memcpy`/`strlen` 等

這些基本函式 kernel 有自己的實作（`lib/string.c`），用法和 libc 的同名函式幾乎一樣——`memcpy`、`memset`、`strlen`、`strcpy`、`strncpy`……區別在於**kernel 版本的這些函式不會替你檢查記憶體是不是合法的**（和 user-space 一樣，C 標準庫這類函式本來就不做邊界檢查，kernel 這邊沒有變得更危險，只是沒有額外的防護層）。真正的危險點在於這些函式如果用在 user-space 傳進來的指標上會直接出事——這正是 Ch 4 要講的 `copy_from_user`/`copy_to_user` 存在的理由：**永遠不要直接對 user-space 指標跑 `memcpy`/`strcpy`**，要透過專門的 uaccess API。

## GFP flags：記憶體配置要帶的「上下文資訊」

`GFP` 是 "Get Free Page" 的縮寫（歷史命名，源自最底層的 page allocator，`kmalloc` 最終也是建立在它上面）。`GFP_xxx` flag 不是「配置選項」，是**你告訴 allocator「現在呼叫你的這段程式碼,處在什麼限制下」**——這個資訊會影響 allocator 怎麼處理「暫時沒有足夠記憶體」這個情況。

| Flag | 意思 | 什麼時候用 |
|---|---|---|
| `GFP_KERNEL` | 一般情況用，**允許在記憶體不足時睡眠**（觸發 page reclaim、甚至等待記憶體被釋放出來） | 絕大多數「正常」的 kernel 程式碼路徑，像 syscall handler、ioctl handler——這些 context 允許睡眠 |
| `GFP_ATOMIC` | **不允許睡眠**，拿不到就立刻失敗回傳 NULL | interrupt handler、持有 spinlock 期間——這些 context 絕對不能睡眠（下一節細講原因） |
| `__GFP_ZERO` | 配置出來的記憶體**自動清零**（等同配置完立刻 `memset(0)`） | 想避免「忘記初始化導致洩漏未初始化記憶體內容」這類漏洞（Ch 10 的 info leak 類型之一），通常搭配其他 flag 用：`kmalloc(size, GFP_KERNEL \| __GFP_ZERO)`，或者直接用 `kzalloc(size, GFP_KERNEL)` 這個等效的便利函式 |
| `GFP_KERNEL_ACCOUNT` | 讓這塊配置計入 **cgroup 的記憶體 accounting**（也就是計入目前 process 所屬 cgroup 的記憶體用量限制），同時——這點對 heap 利用很關鍵——會把物件分配到**獨立的、accounted 專用的 kmalloc cache**，而不是和不帶這個 flag 的配置共用同一塊 cache | kernel 裡會被不受信任的 user-triggered 配置大量用到的路徑（像某些 socket、檔案系統物件），這是 Ch 9（SLUB hardening）會細談的「dedicated caches」機制的一部分 |

**為什麼這件事對 kernel exploit 很關鍵**：Ch 19/20 的 heap spray/grooming，核心問題之一就是「我想噴的這個目標物件，和我能控制的 spray 物件，會不會落在同一個 SLUB cache 裡」——而這個問題的答案，部分取決於兩者配置時用的 GFP flag 是否相容（`GFP_KERNEL_ACCOUNT` 配置出來的物件，和不帶這個 flag 配置出來的物件，即使大小一樣，也可能落在不同 cache，因為 accounting 機制會把它們分開）。這個細節現在知道就好，Ch 9 會展開完整機制。

## Atomic context vs sleepable context：為什麼睡眠是個大問題

這是本章最重要的概念。先看具體問題：

```c
/* 這段程式碼在 spinlock 保護的區間內 */
spin_lock(&my_lock);
void *buf = kmalloc(1024, GFP_KERNEL);   /* 錯！ */
spin_unlock(&my_lock);
```

**為什麼這是錯的**：`GFP_KERNEL` 允許 allocator 在記憶體不足時讓目前的 thread **睡眠**（context switch 出去，等記憶體被釋放出來再被喚醒）。而 `spin_lock` 保護的區間——在單核或者不支援搶占的情況下——本質假設「拿到鎖的這段程式碼會跑得很快、不會把 CPU 讓給別人」。如果在持有 spinlock 期間睡眠，而**喚醒你的那個條件恰好需要另一個 thread 也拿到這個鎖才能達成**，你就製造了一個死鎖——更麻煩的是，kernel 在很多情況下會直接偵測到這種「在不該睡眠的地方睡眠」並且主動 `BUG()`（搭配 `CONFIG_DEBUG_ATOMIC_SLEEP` 開啟時，這是除錯用的檢測機制），而不是悄悄運作。

正確寫法是把配置挪到鎖外，或者改用 `GFP_ATOMIC`：

```c
/* 選項 A：挪到鎖外 */
void *buf = kmalloc(1024, GFP_KERNEL);
spin_lock(&my_lock);
/* 使用 buf，不在這裡配置記憶體 */
spin_unlock(&my_lock);

/* 選項 B：如果邏輯上非要在鎖內配置，換成不睡眠的版本 */
spin_lock(&my_lock);
void *buf = kmalloc(1024, GFP_ATOMIC);
if (!buf) {
        spin_unlock(&my_lock);
        return -ENOMEM;
}
spin_unlock(&my_lock);
```

### 哪些地方算「不能睡眠」的 context

- 持有 **spinlock** 期間（`spin_lock`/`spin_lock_irqsave` 之後到對應 `unlock` 之前）
- **interrupt handler** 內（硬體中斷處理函式，top-half）
- 持有 **RCU read lock** 期間（`rcu_read_lock()` 到 `rcu_read_unlock()` 之間——Ch 39 會細談 RCU 這個並發機制）
- 任何已經標記 `preempt_disable()` 的區間

而一般的 syscall handler、ioctl handler、workqueue 裡的函式——這些都允許睡眠,是因為它們跑在一個普通的 process context 裡,被 context switch 出去完全安全。

### 為什麼 kernel pwn 讀者要在意這個

這不只是「寫 module 不要犯這個錯」的衛生習慣——理解 atomic/sleepable context 的邊界，直接影響你怎麼寫一個**不會在觸發漏洞之前自己先把系統搞崩**的 vulnerable module（本章末的練習就會刻意示範一次錯誤版本），也影響你讀別人的 exploit 時能不能看懂「為什麼這一步要特別小心用 `GFP_ATOMIC`」「為什麼這段 race condition 利用程式碼要避開某個 lock 區間」。

## Per-CPU 變數：避免鎖競爭的另一條路

多核心系統上,如果每個 CPU 都要頻繁更新同一個全域計數器（例如某種統計資訊），用鎖保護會造成大量 CPU 之間互相等待（鎖競爭,lock contention）。**per-cpu 變數**是另一種解法——每個 CPU 拿到自己**獨立的一份拷貝**，平常各自讀寫自己那份不需要鎖，只在真正需要加總所有 CPU 的值時才做一次跨 CPU 的同步。

```c
#include <linux/percpu.h>

DEFINE_PER_CPU(int, my_counter);

void bump_counter(void)
{
        this_cpu_inc(my_counter);     /* 遞增「目前執行這段程式碼的那個 CPU」自己的那份 */
}

int read_total(void)
{
        int total = 0, cpu;
        for_each_possible_cpu(cpu)
                total += per_cpu(my_counter, cpu);   /* 讀取特定 CPU 的那份 */
        return total;
}
```

`this_cpu_inc` 這類 `this_cpu_*` API 的操作在硬體層面通常是**不可中斷的單一指令**（在支援的架構上），不需要額外加鎖；`per_cpu(var, cpu)` 則是明確指定讀取哪個 CPU 的那份拷貝。

**對 kernel pwn 的意義**：per-cpu 資料結構本身常常是 info leak 的來源之一（Ch 14 會細談）——某些 per-cpu 變數的位址和「目前在哪個 CPU 上執行」綁定，如果能讀出一個 per-cpu 變數的位址,某些情況下可以反推出 kernel base 的位移。另外，SLUB allocator 內部大量用 per-cpu 的 `kmem_cache_cpu` 結構做快速路徑配置（Ch 8），理解 per-cpu 這個機制是看懂 SLUB 內部實作的前提之一。

## 錯誤處理慣例：`ERR_PTR`/`PTR_ERR`/`IS_ERR`

Kernel 函式如果回傳值本身應該是一個指標（成功時），但又需要回報「失敗，而且失敗原因是某個 errno」，不能像一般函式那樣回傳 `-EINVAL` 這種整數（型別不對）。慣例解法是把錯誤碼**編碼進一個看起來像指標、但其實落在不可能是合法指標的位址範圍**裡：

```c
#include <linux/err.h>

struct foo *get_foo(int id)
{
        if (id < 0)
                return ERR_PTR(-EINVAL);     /* 把 -EINVAL 編碼成一個「指標」 */

        struct foo *f = kmalloc(sizeof(*f), GFP_KERNEL);
        if (!f)
                return ERR_PTR(-ENOMEM);
        return f;
}

void caller(void)
{
        struct foo *f = get_foo(5);
        if (IS_ERR(f)) {
                int err = PTR_ERR(f);         /* 解碼回原本的 errno */
                pr_err("get_foo failed: %d\n", err);
                return;
        }
        /* f 是合法指標，正常使用 */
}
```

`ERR_PTR`/`IS_ERR`/`PTR_ERR` 的實作依賴一個事實：kernel 虛擬位址空間裡，最高的一小段位址範圍永遠不可能是一個合法配置出來的指標（x86-64 上大致是最後 4KB，即 `-4095` 到 `-1` 對應的位址,具體範圍定義在 `include/linux/err.h` 的 `MAX_ERRNO` 附近），所以把 errno 的負數值直接當成指標數值塞進去，之後用 `IS_ERR` 判斷這個指標數值是否落在那個保留範圍內，就能分辨「這是真指標」還是「這是偽裝成指標的錯誤碼」。

## Goto 清理階梯：kernel 風格的資源釋放

Kernel 程式碼大量使用一種叫「goto 清理階梯」（cleanup ladder）的慣例——在函式裡配置多個資源，任何一步失敗都 `goto` 到對應的清理點，依配置的相反順序逐層釋放：

```c
static int setup_device(struct my_dev *dev)
{
        int ret;

        dev->buf1 = kmalloc(SIZE1, GFP_KERNEL);
        if (!dev->buf1) {
                ret = -ENOMEM;
                goto err_buf1;
        }

        dev->buf2 = kmalloc(SIZE2, GFP_KERNEL);
        if (!dev->buf2) {
                ret = -ENOMEM;
                goto err_buf2;
        }

        ret = register_something(dev);
        if (ret)
                goto err_register;

        return 0;

err_register:
        kfree(dev->buf2);
err_buf2:
        kfree(dev->buf1);
err_buf1:
        return ret;
}
```

這個模式乍看冗長，但保證了**不管在哪一步失敗，已經配置成功的資源都會被正確釋放，而且順序正確**（後配置的先釋放）。它是 C 語言沒有 RAII（C++ 那種「物件離開 scope 自動呼叫解構子」機制）之下的標準替代方案，整個 kernel 原始碼裡隨處可見這個模式——讀 CVE 分析時，很多漏洞恰好就是這個清理階梯寫錯了（某個 `goto` 跳錯層,導致漏釋放或者釋放兩次,Ch 10 的 double-free 類型漏洞不少源自這裡）。

## 參考計數：`refcount_t` 與 `kref`

Kernel 裡大量物件的生命週期不是「一個函式配置、同一個函式釋放」這種簡單模式，而是「被多個地方同時引用，最後一個引用釋放時才真正釋放記憶體」。這就是**參考計數（reference counting）**。

### `refcount_t`：基本的原子計數器，帶溢位保護

```c
#include <linux/refcount.h>

struct my_obj {
        refcount_t refs;
        /* ... 其他欄位 ... */
};

void my_obj_get(struct my_obj *obj)
{
        refcount_inc(&obj->refs);
}

void my_obj_put(struct my_obj *obj)
{
        if (refcount_dec_and_test(&obj->refs))
                kfree(obj);     /* 遞減後發現歸零,真正釋放 */
}
```

`refcount_t`（相對早期單純用 `atomic_t` 手刻計數器）的關鍵差異是它內建**溢位與下溢檢測**——如果計數器被遞增到快要溢位，或者被遞減到低於零，`refcount_*` 系列函式會觸發警告（在 `CONFIG_REFCOUNT_FULL` 之類的加固設定下甚至會主動阻止這個操作生效），而不是悄悄讓計數器錯亂。這是 kernel 社群對一整類「refcount overflow 導致 UAF」漏洞的回應——如果攻擊者能透過某個邏輯錯誤觸發計數器被遞減太多次而低於零（underflow 再繞回一個很大的正數），就可能製造出「kernel 以為這個物件還有人在用，但實際上已經被釋放」的 UAF 條件。Ch 10 會把這類漏洞獨立列成一個類型。

### `kref`：更早期、更簡單的參考計數包裝

`kref` 是比 `refcount_t` 更早引入、至今仍在部分子系統裡沿用的包裝：

```c
#include <linux/kref.h>

struct my_obj {
        struct kref ref;
};

static void my_obj_release(struct kref *ref)
{
        struct my_obj *obj = container_of(ref, struct my_obj, ref);
        kfree(obj);
}

kref_get(&obj->ref);                        /* 遞增 */
kref_put(&obj->ref, my_obj_release);         /* 遞減,歸零時呼叫 release callback */
```

`kref_put` 的第二個參數是一個 callback——歸零時由呼叫者決定怎麼釋放（不一定是單純 `kfree`，可能還要先做其他清理）,`kref_get`/`kref_put` 的底層計數本身也有溢位保護。

**現在先知道這兩個 API 存在、用法大致如上**，不需要深入它們的記憶體屏障細節——Ch 10 會把「refcount 錯誤」獨立列成一個漏洞類型，到時候回頭看這一節的程式碼。

## `container_of` 宏：從成員指標反推外層結構指標

這個宏在 kernel 原始碼裡無處不在，先建立直覺。假設有：

```c
struct my_obj {
        int id;
        struct list_head list;   /* list 是這個 struct 的其中一個成員 */
};
```

Kernel 的 linked list（`struct list_head`）設計成**嵌入**在你自己的 struct 裡，而不是像很多語言的容器那樣「外面包一層,裡面放你的資料」。走訪 list 時，你拿到的是 `struct list_head *`（指向某個 `my_obj` 裡那個 `list` 成員的位址），但你真正想要的是整個 `struct my_obj *`。`container_of` 就是做這個反推：

```c
#include <linux/container_of.h>   /* 或舊版本在 linux/kernel.h */

struct list_head *pos;
list_for_each(pos, &my_list_head) {
        struct my_obj *obj = container_of(pos, struct my_obj, list);
        pr_info("id = %d\n", obj->id);
}
```

`container_of(ptr, type, member)` 的實作原理：它知道 `member` 在 `type` 裡的 offset（透過 `offsetof`，Ch 2 介紹過的那個編譯期常數），把 `ptr` 的位址減掉這個 offset，就回推出整個外層 struct 的起始位址。這個宏之所以值得單獨一節講——**它是很多 type confusion 類漏洞的技術背景**：如果攻擊者能讓 kernel 對一塊記憶體用錯的 `type`/`member` 組合跑 `container_of`，算出來的「外層結構指標」會指向錯誤的位置，後續對這個指標的任何讀寫都會作用在錯的記憶體上——Ch 10 會把這個場景具體化成一個漏洞類型。

## 對比與取捨

| 概念 | user-space 對應 | 關鍵差異 |
|---|---|---|
| `kmalloc`/`kfree` | `malloc`/`free` | 底層是 SLUB 不是 ptmalloc；需要搭配 GFP flag 宣告 context |
| `printk`/`pr_info` | `printf` | 寫進 ring buffer，不是直接輸出；帶 log level |
| `ERR_PTR`/`IS_ERR` | 回傳 errno 或設 `errno` 全域變數 | 把錯誤碼編碼進指標本身，避免雙重回傳值的麻煩 |
| `refcount_t`/`kref` | 手刻計數器、或語言內建 GC | 內建溢位保護，沒有 GC 兜底,錯一步就是 UAF/leak |
| atomic context 限制 | （user-space 基本不存在這個限制） | kernel 特有，違反會死鎖或被 `BUG()` |

## 踩雷集錦

1. **錯誤直覺：「`GFP_KERNEL` 和 `GFP_ATOMIC` 差別只是『哪個比較快』。」** 正確認識：兩者的差別是「允不允許在記憶體不足時睡眠」，這是**正確性**問題不是效能問題——在 atomic context 用 `GFP_KERNEL` 可能死鎖或觸發 `BUG()`；在允許睡眠的 context 不必要地用 `GFP_ATOMIC` 則會讓你的配置在記憶體緊張時更容易失敗（因為它不會等待記憶體被釋放出來,直接放棄）。

2. **錯誤直覺：「kernel 裡的 `memcpy` 比 user-space 更危險，因為沒有邊界檢查。」** 正確認識：`memcpy` 本身在 user-space 和 kernel 一樣都不做邊界檢查（C 語言本來就這樣）；kernel 真正危險的地方是對**user-space 傳進來的指標**直接用這些函式——這不是「邊界檢查」問題,是「你完全沒有驗證過這個指標指向的記憶體是不是真的屬於這個 user process、是不是真的映射在那裡」。這正是 Ch 4 的 `copy_from_user`/`copy_to_user` 存在的原因，不要把這兩件事混成同一個問題。

3. **錯誤直覺：「反正 kernel module 用完就 `kfree`，記憶體管理跟 user-space 寫 C 一樣小心就夠了。」** 正確認識：kernel 沒有 process 退出時「全部資源一起回收」這種安全網——user-space 程式忘記 `free` 的記憶體，程式結束 OS 會整塊收回；kernel module 如果在某個錯誤路徑忘記 `kfree`，這塊記憶體**一直洩漏到下次重開機**（或者你 `rmmod` 時自己手動清理乾淨，但很多 bug 路徑根本沒機會執行到你寫的清理程式碼）。goto 清理階梯這麼被重視正是因為這個後果比 user-space 嚴重得多。

4. **錯誤直覺：「`IS_ERR(ptr)` 可以拿來檢查任何函式回傳的指標是不是 NULL。」** 正確認識：`IS_ERR` 檢查的是指標數值是否落在 `ERR_PTR` 編碼用的那個保留範圍，**不是**檢查 NULL——`NULL`（數值 0）本身不在那個範圍內，`IS_ERR(NULL)` 會回傳 false。一個函式如果約定「失敗回傳 NULL」，你要用 `!ptr` 檢查；一個函式如果約定「失敗回傳 `ERR_PTR(errno)`」，你要用 `IS_ERR(ptr)` 檢查——兩種慣例在 kernel 裡都存在，讀函式的文件/註解確認它用哪一種，不要混著用錯檢查方式（混用的後果：把一個合法的 `ERR_PTR` 當成正常指標去解引用，直接讓 kernel 崩潰，而這個崩潰的位址看起來會很奇怪，因為你解引用了一個落在 `-4095`~`-1` 附近的「假指標」）。

5. **錯誤直覺：「per-cpu 變數加上 `this_cpu_inc` 之後就完全不需要擔心並發問題了。」** 正確認識：`this_cpu_*` API 保證對**同一個 CPU 當下這個操作**是安全的，但如果這段程式碼可能被**搶占（preemption）**打斷、搬到別的 CPU 繼續跑（例如沒有關閉 preemption 的一般 context），你拿到的「目前 CPU」在函式中途可能已經變了——這類邊界情況需要搭配 `preempt_disable()`/`get_cpu()`/`put_cpu()` 一類的保護，per-cpu 變數本身不是萬能護身符，Ch 39（並發模型）會更完整地談這個。

## 進階：再往深一層

- **讀 `include/linux/gfp_types.h`（6.6 版本路徑，舊版本可能是 `include/linux/gfp.h`）的完整 flag 列表**，本章只列了四個最常用的；真實配置路徑常常是多個 flag 用 `|` 疊加（例如 `GFP_KERNEL | __GFP_ZERO | __GFP_NOWARN`），理解每個 flag 的獨立意義能幫你讀懂任何一行配置呼叫背後的完整意圖。
- **追一次 `kmalloc` 從呼叫到真正進入 SLUB 的路徑**（用 Ch 2 的方法論）——這會提前讓你看到 Ch 8 要細講的 slab 內部結構，先建立一個模糊印象，Ch 8 讀起來會更順。
- **理解 `might_sleep()` 這個除錯用的斷言宏**：很多 kernel API 內部會呼叫 `might_sleep()`，在 `CONFIG_DEBUG_ATOMIC_SLEEP` 開啟時，如果目前 context 不允許睡眠卻呼叫了一個標記 `might_sleep()` 的函式，會立刻印出警告——這是你在 Part 0 寫 vulnerable module 時，用來自我檢查「我是不是不小心在 atomic context 用了不該用的 API」的工具。

## 動手練習

寫一個最小 kernel module，刻意示範本章兩個規則的違反與正確版本（這段**未實測，理論預期行為**——下方描述的行為基於 kernel atomic-context 偵測機制的已知設計，但實際錯誤訊息的精確文字請你在自己的 QEMU 環境實測核對）：

1. 寫一個在 `spin_lock`/`spin_unlock` 之間用 `GFP_KERNEL` 配置記憶體的函式，在開著 `CONFIG_DEBUG_ATOMIC_SLEEP` 的 kernel 上 `insmod` 並觸發它，觀察 `dmesg` 的警告（預期會看到 `BUG: sleeping function called from invalid context` 一類的訊息，提到 `might_sleep` 相關的檢查失敗）。
2. 把同一段程式碼改成 `GFP_ATOMIC`，重新編譯、重新跑一次，確認警告消失。
3. 寫一個刻意漏掉某一條 `kfree` 的清理階梯（模擬一個「這條錯誤路徑忘記釋放」的 bug），用 `CONFIG_DEBUG_KMEMLEAK`（如果你的 config 開著）或者簡單用 `kmalloc`/`kfree` 配對次數的 `pr_info` 計數，驗證洩漏確實發生。

## 本章重點整理

- Kernel 沒有 libc 兜底，`kmalloc`/`printk`/`lib/string.c` 系列函式是用來替代它的核心 API,但底層機制（SLUB、ring buffer）和 user-space 對應物完全不同。
- GFP flag 不是配置選項,是「宣告目前 context 允不允許睡眠」的必要資訊——atomic context（持有 spinlock、interrupt handler 內）用錯會死鎖或觸發 `BUG()`。
- `ERR_PTR`/`IS_ERR`/`PTR_ERR` 把錯誤碼編碼進指標的保留位址範圍，用來在「回傳值型別是指標」的函式裡同時回報失敗原因；`IS_ERR` 和 `!ptr`（NULL 檢查）是兩套不同的慣例,不能混用。
- 參考計數（`refcount_t`/`kref`）管理共享物件的生命週期，內建溢位保護是對一整類 refcount 漏洞的回應；`container_of` 讓 kernel 的嵌入式資料結構（像 `list_head`）能從成員指標反推外層物件,也是 type confusion 漏洞的技術背景。

## 自我檢核

- [ ] 不看筆記，能解釋為什麼 kernel 不能直接「借用」user-space 的 libc
- [ ] 能說出 atomic context 不能睡眠的具體原因（不只是「規則上不能」，是底層會發生什麼）
- [ ] 能解釋 `IS_ERR(ptr)` 和 `ptr == NULL` 檢查的是兩件不同的事
- [ ] 能說出 `refcount_t` 相對手刻 `atomic_t` 計數器,多了什麼保護,這個保護對應哪一類漏洞

## 延伸閱讀

### 官方文件 / Spec

- **[Documentation/core-api/memory-allocation.rst](https://www.kernel.org/doc/html/latest/core-api/memory-allocation.rst.html)**
  - **讀哪裡**：「GFP flags」一節
  - **和本章的關聯**：本章只列了四個最常用的 flag，這份文件是完整列表的權威來源,之後碰到沒見過的 flag 組合時回來查

### 部落格 / 技術文章

- **[Linux Kernel Module Programming Guide — Chapter on /proc and memory](https://sysprog21.github.io/lkmpg/)**
  - **這篇說什麼**：延續 Ch 0 引用的同一份指南，這裡聚焦在記憶體配置與 `/proc` 介面的章節
  - **讀哪裡**：Memory Allocation 相關章節
  - **為什麼值得讀**：持續維護、範例可直接編譯跑通，適合搭配本章動手練習

### 書籍

- **《Linux Device Drivers》第 3 版** — Jonathan Corbet, Alessandro Rubini, Greg Kroah-Hartman（O'Reilly, 2005，線上免費版持續維護）
  - **這本書的定位**：driver 開發的經典教材，雖然部分 API 細節隨版本變化，但本章談的 GFP/atomic context/參考計數這些基礎概念的解說至今仍是最清楚的入門材料之一
  - **讀哪幾章**：第 8 章（Allocating Memory）談 GFP flags 與配置策略;第 5 章（Concurrency and Race Conditions）談 atomic context 與鎖的基礎,這兩章和本章內容直接對應

下一章把範圍收窄到一個更具體、更關鍵的邊界——user-space 和 kernel-space 之間的 syscall 介面本身，這是攻擊面真正的入口。

→ [Ch 4 syscall 與 user/kernel boundary](./04-syscall-boundary.md)
