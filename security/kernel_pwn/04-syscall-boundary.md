# Ch 4 — syscall 與 user/kernel boundary：ABI、copy_from/to_user、ioctl、uaccess

> **目標**：理解 user-space 和 kernel-space 之間唯一合法的正式通道——syscall——它的 x86-64 呼叫慣例、為什麼 kernel 不能直接解引用 user-space 傳進來的指標、`copy_from_user`/`copy_to_user`/`get_user`/`put_user` 怎麼安全地跨這條邊界搬資料、`ioctl` 的命令編碼慣例,以及這條邊界和 SMAP 硬體機制的關係。這是整本書「攻擊面」概念最具體的落地——幾乎所有 vulnerable module 的漏洞,都發生在這條邊界被處理錯誤的地方。

> **環境**：x86-64，Linux 6.6。ABI 細節是架構相關的，本章內容限定 x86-64；其他架構（ARM64 等）的暫存器配置不同，Ch 46 會談差異。

## 為什麼需要這一章？

Ch 1 的地圖把「Bug」放在第一步，但一個 bug 要先**能被觸發**才有意義——而「觸發」這件事，對 kernel 來說幾乎永遠意味著「某個 user-space 程式碼透過某條正式通道，把資料或控制流遞進了 kernel」。這條通道就是 syscall（直接或者透過更高層的包裝，像 `ioctl`）。理解這條邊界的規則——尤其是「kernel 永遠不能天真地信任 user-space 傳來的任何東西」這個核心原則——是看懂**為什麼**某個 vulnerable module 範例會寫成那樣、以及**真實世界**的 kernel 漏洞絕大多數集中在「邊界處理」而不是「邊界內部的普通邏輯」的關鍵。

## 先建立直覺：两個獨立的位址空間,中間只有幾個收費站

```
┌─────────────────────────┐          ┌─────────────────────────┐
│   User-space             │          │   Kernel-space           │
│   (process A 的虛擬位址   │          │  (所有 process 共用同一份  │
│    空間，例如 0x0 ~       │          │   kernel 映射,              │
│    0x7fffffffffff)       │          │   例如 0xffff800000000000+)│
│                          │          │                          │
│   buf = malloc(100)      │          │   這段記憶體在 kernel 的    │
│   buf 這個位址,只在       │          │   位址空間裡完全看不到、    │
│   process A 自己的虛擬    │          │   也不能直接存取           │
│   位址空間裡有意義         │          │   (除非走合法通道)         │
└───────────┬──────────────┘          └─────────────┬────────────┘
            │                                        │
            │   這條邊界只有幾個收費站能過：           │
            │   syscall 指令、page fault、interrupt   │
            └──────────────────▶ ◀──────────────────┘
```

關鍵心智模型：**user-space 的虛擬位址和 kernel-space 的虛擬位址,是同一台 CPU 上兩套完全不同的「座標系統」**。一個 user-space 程式手上的指標（例如 `malloc` 回傳的位址），只有在**那個 process 自己的** page table context 下才有意義——它指向那個 process 的虛擬位址空間裡某塊實體記憶體的映射。Kernel 執行的時候,雖然技術上**可以**看到同一份 page table（x86-64 的經典設計是 user 和 kernel 共用同一份 page table，只是 kernel 那一半的 entry 標記了「只有 ring 0 能存取」，Ch 6 細談這個佈局），但這不代表 kernel 可以**天真地**把一個 user-space 傳進來的數字當成「指向合法記憶體的指標」直接解引用——這個數字可能是惡意構造的、可能指向完全不屬於呼叫者的記憶體、甚至可能是一個根本沒有映射的位址。

## x86-64 syscall ABI：暫存器怎麼傳參

User-space 程式呼叫 syscall,不是函式呼叫（沒有 `call` 指令跳進某個位址），是透過一條專門的 CPU 指令：

```asm
mov rax, 257        ; syscall number (這裡示範 openat,見 Ch 2 查到的表)
mov rdi, dfd         ; 第 1 個參數
mov rsi, filename    ; 第 2 個參數
mov rdx, flags       ; 第 3 個參數
mov r10, mode        ; 第 4 個參數 (注意不是 rcx!)
syscall              ; 觸發從 ring 3 切到 ring 0
```

x86-64 Linux 的 syscall 呼叫慣例，六個暫存器依序對應最多六個參數：

| 參數順序 | 暫存器 |
|---|---|
| 1 | `rdi` |
| 2 | `rsi` |
| 3 | `rdx` |
| 4 | `r10` |
| 5 | `r8` |
| 6 | `r9` |

`rax` 在呼叫前放 syscall number，呼叫後被覆寫成回傳值（成功時是結果,失敗時是**負的** errno,例如 `-EINVAL`——user-space 的 libc wrapper 負責把這個負數轉換成「回傳 -1 並設定全域變數 `errno`」這個 C 語言慣例的使用者介面,syscall 層本身沒有 `errno` 這個概念）。

**第 4 個參數為什麼是 `r10` 不是 `rcx`**：這是容易踩的坑。x86-64 一般函式呼叫慣例（System V ABI，也就是 `call`/`ret` 那套)第 4 個參數確實用 `rcx`。但 `syscall` 指令本身會把目前的 `rip`（下一條要執行的指令位址）存進 `rcx`，供之後 `sysret` 指令返回 user-space 時使用——`rcx` 被指令本身佔用了,Linux 的 syscall 慣例因此改用 `r10` 代替。這個差異在你需要手寫組語觸發 syscall、或者讀 exploit 的組語片段時經常出現,務必記住。

## entry_SYSCALL_64：kernel 這一側接到了什麼

`syscall` 指令執行時,CPU 做幾件事：切換到 ring 0、把目前 `rip` 存進 `rcx`、把目前 flags 存進 `r11`、跳到一個事先透過 `MSR`（model-specific register，`MSR_LSTAR`）設定好的固定位址——這個位址就是 kernel 裡 `entry_SYSCALL_64` 這段組語入口（6.6 原始碼位置大致在 `arch/x86/entry/entry_64.S`，用 Ch 2 的方法可以自己找到確切行號）。

`entry_SYSCALL_64` 的工作：

1. 切換 stack——從 user-space 的 stack 切到這個 CPU 專屬的 kernel stack（每個 CPU、每個 task 都有自己的 kernel stack，絕對不會用 user-space 的 stack 執行 kernel 程式碼）。
2. 把目前所有暫存器狀態存進 `pt_regs` 結構（之後要用它恢復狀態、或者如果是 `ptrace`/signal 之類的場景要讀取使用者當時的暫存器值）。
3. 根據 `rax` 裡的 syscall number，查表（Ch 2 看到的 `syscall_64.tbl` 編譯後的版本）跳到對應的 `sys_xxx` handler。
4. handler 執行完，恢復暫存器、切回 user-space stack、執行 `sysret`（`syscall` 的反向操作)回到 user-space，`rip` 恢復成之前存在 `rcx` 裡的值。

這整個流程**未實測，理論預期行為**——這段描述基於 x86-64 entry 程式碼長期以來公開的架構文件與原始碼慣例，但確切的組語細節（尤其是 KPTI 開啟時會插入的額外 page table 切換，Ch 15 細談）隨版本調整過，建議直接在你的 6.6 樹讀 `arch/x86/entry/entry_64.S` 搭配 Documentation/x86/entry_64.rst 核對一次。

## 為什麼不能直接解引用 user pointer

回到 `openat(int dfd, const char *filename, int flags, mode_t mode)` 這個例子——`filename` 是 user-space 傳進來的一個指標，指向 user-space 某塊記憶體裡的路徑字串。Kernel 的 `do_sys_open` 要讀這個字串的內容（路徑名稱），如果天真地寫：

```c
/* 絕對不要這樣寫 */
printk("opening %s\n", filename);
```

這裡假設 `filename` 是 kernel 自己配置的 buffer 時這樣寫沒問題——但 `filename` 是**user-space 的指標**，直接對它解引用，問題不是「有沒有邊界檢查」而是更根本的：

1. **這個位址可能完全沒有映射**（user-space 傳了一個隨便編造的數字，不對應任何合法記憶體）——kernel 嘗試讀取時會觸發 page fault，而這次 page fault 發生在 kernel context 執行期間，如果沒有正確的錯誤處理機制，kernel 會直接當成「kernel 自己的程式碼存取了非法記憶體」而 panic（這正是為什麼你需要專門的、「預期可能失敗」的存取方式,而不是普通指標解引用）。
2. **這個位址可能指向別的 process 的記憶體,或者 kernel 自己的記憶體**（取決於攻擊者精心構造的位址）——如果沒有任何檢查，攻擊者可以讓 kernel 讀寫它原本不該碰的記憶體,這本身就是一個漏洞。
3. **TOCTOU（time-of-check to time-of-use）風險**——即使 kernel 檢查過一次「這個位址對這個 process 合法」，user-space 的另一個 thread 完全可能在 kernel 檢查完、真正讀取之前，把這塊記憶體的映射換掉（例如用 `mmap`/`munmap` 操作虛擬記憶體佈局）。這類 race condition 是 Ch 40 的 double-fetch 漏洞類型的根源之一。

## `access_ok`：先問「這個範圍合不合理」

在真正搬資料之前,kernel 有時會先用 `access_ok` 做一個快速、粗略的合理性檢查：

```c
#include <linux/uaccess.h>

if (!access_ok(user_ptr, len))
        return -EFAULT;
```

`access_ok` 檢查的是「這個位址範圍,看起來**像**是落在合法的 user-space 位址空間裡」（例如確認它沒有超出 user-space 位址空間的上限、沒有整數溢位導致範圍本身不合理）——它**不保證**這塊記憶體真的有映射、真的屬於目前這個 process。真正的映射有效性檢查是在底層的 `copy_from_user`/`copy_to_user` 透過**嘗試存取並處理可能的 page fault**來完成的（下一節細談）。現代 kernel 的 `copy_from_user`/`copy_to_user` 實作內部已經整合了等同 `access_ok` 的檢查，所以你在很多現代程式碼裡**不會**單獨看到顯式呼叫 `access_ok`——它常被內嵌在更高層的 API 裡；本章仍然介紹它，因為概念上理解「範圍合理性檢查」和「真正的安全存取」是兩個獨立步驟，有助於看懂舊版本程式碼或者某些需要手動檢查的場景。

## `copy_from_user` / `copy_to_user`：安全跨界搬資料

這是 kernel module 處理 user-space 資料最常用的介面：

```c
#include <linux/uaccess.h>

char kbuf[256];

static ssize_t my_write(struct file *filp, const char __user *ubuf,
                         size_t len, loff_t *off)
{
        if (len > sizeof(kbuf))
                return -EINVAL;              /* 先檢查大小,避免 kbuf 溢位 */

        if (copy_from_user(kbuf, ubuf, len))
                return -EFAULT;              /* 複製失敗(通常是 page fault),回報 EFAULT */

        /* 現在可以安全地對 kbuf（kernel 自己的記憶體）做任何操作 */
        return len;
}
```

`copy_from_user(到哪裡_kernel_buf, 從哪裡_user_ptr, 長度)` 做的事：它會**嘗試**從 `ubuf` 指向的 user-space 記憶體讀取 `len` bytes 到 `kbuf`。這個「嘗試」的關鍵在於——底層實作用一種叫 **exception table**（`extable`）的機制：如果這次讀取真的觸發 page fault（代表 `ubuf` 指向的記憶體確實沒有合法映射），kernel 的 page fault handler 會查這張表,發現「這個 fault 發生在一段標記過『預期可能失敗』的程式碼內」，於是**不會**讓整個 kernel panic，而是讓 `copy_from_user` 乾淨地回傳「沒複製完成的 bytes 數」（部分版本/路徑是回傳剩餘未複製的 bytes，呼叫者通常把非零回傳值直接當作「失敗」處理並回 `-EFAULT`）。

`copy_to_user` 方向相反——把 kernel 自己的資料寫到 user-space 指定的位址：

```c
static ssize_t my_read(struct file *filp, char __user *ubuf,
                        size_t len, loff_t *off)
{
        char kbuf[] = "hello from kernel\n";
        size_t to_copy = min(len, sizeof(kbuf));

        if (copy_to_user(ubuf, kbuf, to_copy))
                return -EFAULT;

        return to_copy;
}
```

### `get_user`/`put_user`：單個值的輕量版本

如果只是搬一個單一的整數、指標大小的值（不是一整塊 buffer），`get_user`/`put_user` 是更輕量的版本，語法上用宏把型別資訊也帶進去（避免你自己手動指定錯誤的大小）：

```c
int value;
int __user *uptr;

if (get_user(value, uptr))      /* 等同 copy_from_user(&value, uptr, sizeof(value)) 但更有效率 */
        return -EFAULT;

if (put_user(value, uptr))      /* 反方向 */
        return -EFAULT;
```

## `__user` 註記與 sparse：讓編譯器幫你抓錯

你可能注意到上面範例裡指標型別寫成 `const char __user *ubuf` 而不是單純 `const char *ubuf`。`__user` 是一個**註記**（annotation），在一般 `gcc` 編譯時它會被巨集展開成空（不影響實際編譯出的機器碼），但它是給 **sparse**（一個 kernel 社群專用的靜態分析工具）看的語義標記——sparse 把 `__user` 指標當成一個獨立的型別，如果你的程式碼把一個 `__user` 指標直接傳給一般函式（像不透過 `copy_from_user` 就直接 `memcpy` 它）、或者把一個普通 kernel 指標賦值給 `__user` 變數而沒有明確轉型，sparse 會報警告。

```bash
make C=1 M=$(pwd) modules     # C=1 觸發 sparse 檢查(需要先裝 sparse)
```

**這個標記本身不提供任何執行期保護**——它純粹是靜態分析/文件化工具,幫開發者（和之後讀程式碼的人）清楚標出「這個指標的值來自不可信的 user-space,任何直接存取都要走 uaccess API」。讀 kernel module 原始碼時看到 `__user`，立刻反應「這個指標不能被直接解引用」。

## uaccess 與 SMAP 的關係

即使邏輯上你用了正確的 `copy_from_user`,還有另一層硬體保護：**SMAP**（Supervisor Mode Access Prevention）。SMAP 是 Intel/AMD 在比較新的 CPU 上加的特性——**在 ring 0 執行的程式碼,如果嘗試存取標記為 user-space 的記憶體頁面，CPU 直接 fault**，除非這段存取發生在明確用 `stac`（set AC flag）指令「解除保護」之後、並且在 `clac`（clear AC flag）之前重新鎖上。

```
正常情況（沒有明確 stac）：
  kernel 程式碼嘗試讀 user 頁面 ──▶ SMAP 擋下，直接 fault

copy_from_user 內部實際發生的事：
  stac                       ← 告訴 CPU：接下來這段,我確實要碰 user 記憶體
  (真正的讀取/寫入指令)
  clac                       ← 讀完了,重新鎖上保護
```

`copy_from_user`/`copy_to_user`/`get_user`/`put_user` 的底層實作，已經正確地把需要的 `stac`/`clac` 包在裡面——這是這些 API 之所以是「唯一正確方式」的另一層理由（不只是邊界檢查和 exception table，還有和 SMAP 正確配合)。如果你（或者某段有漏洞的 kernel 程式碼）試圖**繞過** uaccess API、直接對一個 user-space 位址解引用，在 SMAP 開著的機器上會直接 fault——這正是 Ch 13 細談的「ret2usr 類攻擊在 SMAP 之後為什麼行不通」的硬體機制源頭：SMAP 不只擋你讀寫 user 記憶體，也擋 kernel **執行** user-space 頁面上的程式碼（這個執行層面的對應保護嚴格說是 SMEP，Ch 13 分開講兩者差異）。

本章只需要知道：SMAP 的存在意味著「就算你的 vulnerable module 漏洞本身繞過了 `copy_from_user` 的邏輯檢查，直接手刻一段存取 user 記憶體的程式碼，在 SMAP 開著的環境下依然會被硬體擋下」——這不是軟體層的 bug 防禦，是另一層獨立的硬體防禦。Ch 13 會給出完整的繞過/應對策略。

## ioctl 慣例：cmd 編碼、`_IOR`/`_IOW`

`ioctl` 是 driver 之間溝通「標準 read/write 之外的自訂操作」的通用介面，本書的 vulnerable module 範例幾乎全部走這條路（原因：ioctl 讓一個 driver 能定義任意數量的自訂命令，很適合當教學用的「攻擊面入口」，也貼近真實世界大量 kernel LPE 漏洞的觸發路徑——很多真實 CVE 正是某個 driver 的 ioctl handler 處理不當）。

```c
long my_ioctl(struct file *filp, unsigned int cmd, unsigned long arg)
{
        switch (cmd) {
        case MY_IOCTL_READ:
                /* ... */
                break;
        case MY_IOCTL_WRITE:
                /* ... */
                break;
        default:
                return -ENOTTY;      /* 不認得的 cmd,慣例回這個 errno */
        }
        return 0;
}
```

`cmd` 這個 32-bit 數字不是隨便挑的整數——Linux 慣例用一組宏把它編碼成四個欄位：方向（讀/寫/兩者皆否）、大小、magic number（識別這個 driver 自己的命名空間，避免跟別的 driver 的 cmd 數字衝突）、序號：

```c
#include <linux/ioctl.h>

#define MY_MAGIC 'k'     /* 自訂一個 magic number,通常用一個字元 */

#define MY_IOCTL_READ   _IOR(MY_MAGIC, 1, int)     /* 這個 ioctl 會把資料從 kernel 讀到 user */
#define MY_IOCTL_WRITE  _IOW(MY_MAGIC, 2, int)     /* 這個 ioctl 會把資料從 user 寫進 kernel */
#define MY_IOCTL_BOTH   _IOWR(MY_MAGIC, 3, int)    /* 雙向 */
```

`_IOR(type, nr, size)`/`_IOW(...)`/`_IOWR(...)` 這些宏（定義在 `include/uapi/asm-generic/ioctl.h`）把方向、`type`（你的 magic number）、`nr`（序號）、`sizeof(size)` 這幾個資訊打包進同一個 32-bit 整數,user-space 呼叫 `ioctl(fd, MY_IOCTL_READ, &value)` 時，這個打包好的數字直接作為第二個參數傳過去。

這個編碼本身**不是安全機制**——它只是一個組織慣例，讓不同 driver 的 ioctl 命令不容易互相撞到，也讓某些通用工具（像 `strace`）能解碼出「這個 ioctl 大概想幹什麼」。真正的安全性完全取決於 `my_ioctl` 函式內部怎麼處理 `arg`——如果 `arg` 本身是一個指向更複雜結構的指標（常見模式），函式內部必須用 `copy_from_user` 把它複製進 kernel 才能安全讀取，**絕對不能**假設 `cmd` 的編碼（像 `_IOW` 標記的方向)本身提供了任何執行期的強制檢查——它只是給人看的文件化慣例,kernel 不會因為你標了 `_IOR` 就自動幫你做邊界檢查。

## 對比與取捨

| 方式 | 用途 | 安全性責任在哪 |
|---|---|---|
| `copy_from_user`/`copy_to_user` | 搬一塊任意長度的 buffer | API 本身處理映射有效性（exception table）+ SMAP（stac/clac）；呼叫者仍要自己檢查長度是否合理 |
| `get_user`/`put_user` | 搬單一個值（int/long/指標大小） | 同上，但編譯期知道確切大小,少一個「長度算錯」的出錯點 |
| 直接解引用 user 指標 | （不應該這樣用） | 沒有任何保護——軟體邏輯錯誤 + SMAP 硬體 fault，雙重地雷 |
| `ioctl` 的 `_IOR`/`_IOW` 編碼 | 組織/文件化命令空間 | 純慣例，不提供任何執行期強制檢查 |

## 踩雷集錦

1. **錯誤直覺：「`__user` 標記會讓編譯器自動阻止我直接解引用這個指標。」** 正確認識：`__user` 只是給 **sparse** 這個額外的靜態分析工具用的語義標記，一般 `gcc`/`make` 編譯流程完全不會因為這個標記阻止你寫出危險的程式碼——它只在你額外跑 `make C=1`（開 sparse 檢查）時才會報警告，而且是警告,不是編譯錯誤。真正的保護來自你有沒有正確呼叫 `copy_from_user` 之類的 API，不是標記本身。

2. **錯誤直覺：「第 4 個 syscall 參數用 `rcx` 傳,因為那是一般函式呼叫慣例的第 4 個暫存器。」** 正確認識：Linux 的 syscall 呼叫慣例因為 `syscall` 指令本身會用 `rcx` 存返回位址,第 4 個參數改用 `r10`。這和一般 C 函式呼叫（System V ABI 的 `call`/`ret`）慣例不同,手寫組語觸發 syscall 時混用兩套慣例是常見的低級錯誤來源。

3. **錯誤直覺：「`access_ok` 回傳成功,就代表這塊記憶體現在真的可以安全讀寫。」** 正確認識：`access_ok` 只做位址範圍的粗略合理性檢查（落在 user-space 位址空間範圍內、沒有明顯的整數溢位），**不保證**這塊記憶體真的有映射、真的屬於呼叫者、或者在你真正讀寫的那一刻依然有效（TOCTOU 風險）。真正的有效性檢查發生在 `copy_from_user`/`copy_to_user` 內部透過 exception table 處理可能的 page fault——這是兩個獨立的檢查層次，不要把「range 合理」和「真的能安全存取」混為一談。

4. **錯誤直覺：「ioctl 的 `_IOR`/`_IOW` 編碼裡標了方向和大小，kernel 會自動幫我驗證傳進來的資料符合這個大小。」** 正確認識：這組宏編碼出的數字,kernel 的 ioctl 分派機制（`do_vfs_ioctl` 這類函式）**不會**主動解讀它、也不會因此自動幫你做任何邊界檢查——這個數字進到你的 `unlocked_ioctl` 函式裡，完全由你自己的 `switch` 邏輯決定怎麼處理 `arg`。`_IOR`/`_IOW` 純粹是給開發者和除錯工具看的文件慣例，不是執行期強制機制。

5. **錯誤直覺：「SMAP 擋掉的是『kernel 讀寫 user 記憶體』，所以只要我的 exploit 不去讀寫 user 記憶體就不受影響。」** 正確認識：SMAP 擋的是**資料存取**（讀/寫 user 頁面)；擋**執行** user 頁面上的程式碼是另一個相關但獨立的機制 SMEP（Supervisor Mode Execution Prevention）。兩者常常被一起提、一起開關（因為兩者合起來才真正堵死經典的 ret2usr 手法），但它們是兩個獨立的 CPU 特性,各自可以單獨開關（Ch 0 的 `-cpu` 旗標與 `-append nosmep nosmap` 就是分開寫的兩個開關，對應這兩個獨立機制）,Ch 13 會把這個差異講得更細。

## 進階：再往深一層

- **讀 `arch/x86/entry/entry_64.S` 裡 `entry_SYSCALL_64` 的完整組語**，搭配 Documentation 裡 `Documentation/arch/x86/entry_64.rst`（6.6 的文件路徑,舊版本可能在別處)，理解 KPTI 開啟時這段入口會多插入哪些 page table 切換指令——這是 Ch 15 深入 KPTI 之前,提前建立的背景。
- **讀一次 `copy_from_user` 的實際實作**（`arch/x86/lib/usercopy_64.c` 或者更底層的 `rep_movs_alternative` 一類函式,具體符號名稱隨版本略有調整，用 Ch 2 的方法自己追一次)，親眼看到 `stac`/`clac` 指令和 exception table entry 是怎麼被組語宏插進去的。
- **研究 compat ioctl/compat syscall**：32-bit 程式在 64-bit kernel 上執行時（「compat mode」），syscall ABI 和資料結構佈局都可能不同，很多真實 CVE 恰好出在 compat 層的轉換邏輯寫錯——這是一個進階但高產的漏洞挖掘方向,本書 Ch 42（patch 分析方法論）會提到如何辨認這類漏洞。

## 動手練習

寫一段最小的使用者空間 C 程式,直接用 `syscall()` 這個 libc 包裝函式（不是個別的 `open()`/`read()` wrapper）觸發一個 syscall，驗證你對暫存器傳參的理解：

```c
#include <stdio.h>
#include <unistd.h>
#include <sys/syscall.h>

int main(void) {
    /* 直接用 syscall number 呼叫 getpid (x86-64 上編號是 39) */
    long pid = syscall(SYS_getpid);
    printf("pid = %ld\n", pid);
    return 0;
}
```

1. 編譯執行，確認輸出的 pid 和 `getpid()`（一般 libc wrapper）回傳的值一致。
2. 用 `gdb` 對這支程式下斷點在 `syscall` 這行,`disas` 看編譯器產生的組語，確認 `rax` 被設成 `39`（可以用 `objdump -d` 或 gdb `x/5i` 驗證,`SYS_getpid` 的實際數值請以你系統的 `<sys/syscall.h>` 或 `syscall_64.tbl` 為準）。
3. 改用 `strace` 跑這支程式，對照 `strace` 印出的 `getpid()` 呼叫，體會「`strace` 其實是在 kernel 邊界上觀察這條通道」。

## 本章重點整理

- Syscall 是 user-space 與 kernel-space 之間唯一的正式通道，x86-64 用 `rdi`/`rsi`/`rdx`/`r10`/`r8`/`r9` 傳參（第 4 個是 `r10` 不是 `rcx`，因為 `rcx` 被 `syscall` 指令本身佔用存返回位址）。
- Kernel 不能直接解引用 user-space 傳來的指標——位址可能無效、可能指向不該碰的記憶體、還有 TOCTOU 風險；`copy_from_user`/`copy_to_user`/`get_user`/`put_user` 透過 exception table 處理可能的 page fault，是唯一正確的跨界搬資料方式。
- SMAP 是硬體層的額外保護，擋住所有沒有明確 `stac`/`clac` 包住的 user 記憶體存取；uaccess API 內部已經正確處理這一層，繞過 API 直接存取在 SMAP 開啟的環境下會直接 fault。
- `ioctl` 的 `_IOR`/`_IOW` 編碼是文件化慣例,不是執行期強制檢查——真正的安全責任全部落在 handler 自己的邏輯上。

## 自我檢核

- [ ] 不看筆記，能畫出一條 syscall 從 user-space `syscall` 指令到 kernel handler 的完整路徑
- [ ] 能解釋為什麼第 4 個 syscall 參數用 `r10` 而不是一般函式呼叫慣例的 `rcx`
- [ ] 能說出 `access_ok` 和 `copy_from_user` 檢查的是兩個不同層次的問題
- [ ] 能解釋 SMAP 和 `__user` 標記分別提供哪一層保護,兩者是否可以互相替代

## 延伸閱讀

### 官方文件 / Spec

- **[System V Application Binary Interface AMD64 Architecture Processor Supplement](https://gitlab.com/x86-psABIs/x86-64-ABI)**
  - **讀哪裡**：Chapter 3（函式呼叫慣例，用於對比一般 C 呼叫慣例與 syscall 慣例的差異）
  - **和本章的關聯**：本章強調的「第 4 個參數用 `r10` 不是 `rcx`」正是 syscall 慣例偏離一般 ABI 慣例的具體例子，讀這份 spec 能看到完整的一般呼叫慣例作對照

- **[Documentation/core-api/kernel-api.rst](https://www.kernel.org/doc/html/latest/core-api/kernel-api.rst.html)**
  - **讀哪裡**：搜尋 "User Space Memory Access" 一節，涵蓋 `copy_from_user`/`copy_to_user`/`get_user`/`put_user` 的正式說明
  - **和本章的關聯**：本章給的是使用範例與機制解釋，這裡是權威的函式簽名與語意來源

### 部落格 / 技術文章

- **[Linux Inside — Syscall 章節](https://0xax.gitbooks.io/linux-insides/content/SysCall/)** — 0xAX
  - **這篇說什麼**：逐步拆解 `entry_SYSCALL_64` 的組語實作，搭配圖解
  - **讀哪裡**：整個 SysCall 章節,尤其是 entry 組語逐行解釋的部分
  - **為什麼值得讀**：目前網路上少數願意真的逐行讀組語而不是只講概念的系列，雖然引用的 kernel 版本可能略舊於 6.6，entry 流程的核心邏輯變化不大

### 書籍

- **《The Linux Programming Interface》** — Michael Kerrisk（No Starch Press, 2010）
  - **這本書的定位**：業界公認講 Linux syscall/系統介面最完整的參考書之一，雖然不談 kernel 內部實作，但對「syscall 作為一個介面,使用者視角該怎麼理解它的語意」極其詳盡
  - **讀哪幾章**：第 3 章（System Programming Concepts）談 syscall 的一般概念與 errno 慣例,和本章 kernel 視角互補

下一章把本章學到的全部概念——`file_operations`、`copy_from_user`、`ioctl` 編碼——兜成一個完整、能編譯、能故意打壞的 vulnerable module，這是你接下來整本書反覆使用的靶子原型。

→ [Ch 5 第一個 vulnerable kernel module](./05-first-vulnerable-module.md)
