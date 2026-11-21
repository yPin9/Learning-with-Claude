# Ch 41 — 拉長 race window：userfaultfd、FUSE、調度操控

> **目標**：把 [Ch 40](./40-race-conditions.md) 辨認出來的 TOCTOU/double-fetch/refcount race,從「理論上存在,但自然發生機率極低」變成「幾乎每次都能命中」的可重複原語。學完你要能說出三大拉窗技巧(`userfaultfd`、FUSE、調度操控)各自卡住的是哪一種 kernel 操作、為什麼能卡、以及防禦者可以怎麼收緊 `unprivileged_userfaultfd` 這一道閥門。

> **環境**:Linux 6.6 LTS、x86-64。`vm.unprivileged_userfaultfd` sysctl 的預設值我們查到的結論是 **0(停用,需 `CAP_SYS_PTRACE` 才能用)**,來源是 kernel 官方文件 `Documentation/admin-guide/mm/userfaultfd.rst`(查證於 6.7 文件版本,6.6 的行為應一致,但請以你的 kernel 版本文件為準)。這個 sysctl 在歷史上曾經預設為 1(2019 年引入時),後來的 patch 改回 0,理由是「攻擊者能用 userfaultfd 拖延 kernel page fault 處理,拉長其他漏洞的利用窗口」——這正是本章要講的機制本身被拿來當作改預設值的理由。本章程式碼片段除非特別標注,**未實測,為理論預期行為**。

## 為什麼需要這一章

[Ch 40](./40-race-conditions.md) 結尾的動手練習第 2 題已經暗示了問題:你照著教科書模式寫出一個 double-fetch 的 vulnerable module,開兩個 thread 狂打,開著 KASAN 觀察——**很可能什麼事都沒發生**。不是你的程式碼寫錯,是 race window 太窄:TOCTOU 的 check 和 use 之間,如果中間只隔著幾個指令(幾個奈秒的執行時間),另一個 thread 要剛好在那幾奈秒內插進來改掉狀態,機率低到你測試一整晚都碰不上。

這不是「這個漏洞不值得打」的訊號,是「你還缺一個工具」的訊號。真實世界的 race-based exploit(包括 [Ch 40](./40-race-conditions.md) 兩個 CVE 案例背後的公開 PoC)幾乎都會用到某種**人工拉長 window** 的技巧——把原本窄到幾奈秒的 window,撐開到幾毫秒甚至更久,讓「命中」從一個稀有事件變成幾乎確定的事件。這一章教三種最常見、原理互不相同的拉窗技巧。

## 先建立直覺:為什麼「卡住」比「加速」更有效

遇到一個窄 race window,直覺的反應常常是「讓攻擊 thread 跑更快」,但這個方向效果有限——你再怎麼優化 user-space 的程式碼,也快不過 kernel 內部兩行指令之間的間隔。真正有效的方向是反過來:**讓受害的那個 thread(正在執行 check-then-use 的 kernel 路徑)停在 window 中間動不了,給你的攻擊 thread 充裕的時間去完成競爭操作**。

```
  硬加速攻擊 thread（效果有限）：
  Victim: check ──(幾奈秒)── use
  Attack:  ...race...race...race...（還是追不上幾奈秒的窗口）

  卡住 victim thread（本章的方向）：
  Victim: check ──(被你卡住,停在這裡不動)── ... ── use
  Attack:            （這段時間你從容完成改狀態的操作)
```

三大技巧的共同精神都是「卡住 victim,不是加速 attacker」,差別只在於**用什麼手段讓 kernel 的執行路徑卡住、以及卡在哪個點**。

## 技巧一:userfaultfd——在 page fault 時把執行緒卡住

### 機制

`userfaultfd` 是一個 Linux 的 syscall/機制,**設計本意是給 user-space 實作自訂的 page fault 處理邏輯**(例如 live migration、延遲載入大型記憶體映像)。它讓你:

1. 建立一個 userfaultfd 物件(`userfaultfd(2)` syscall)。
2. 用 `ioctl(UFFDIO_REGISTER)` 把某一塊虛擬記憶體範圍(例如 `mmap` 出來的一塊匿名記憶體)註冊給這個 userfaultfd 監控。
3. 之後任何人(包括 kernel 自己!)對這塊記憶體**觸發 page fault**(第一次存取、或者這塊記憶體目前沒有對應的 physical page),kernel 不會走原本的 fault 處理,而是**把觸發 fault 的那個執行緒掛起,送一個訊息到你的 userfaultfd fd**,等你(user-space 的另一個 thread)讀到這個訊息、決定怎麼處理(例如用 `UFFDIO_COPY` 把一塊真正的內容填進去)之後,被掛起的執行緒才會繼續跑。

```
  正常的 page fault：
  某個執行緒存取一塊還沒映射的記憶體
       │
       ▼
  kernel page fault handler 立刻處理（配 page、填內容）
       │
       ▼
  執行緒幾乎無感地繼續跑（延遲極短）

  userfaultfd 介入後：
  某個執行緒存取一塊「註冊給 userfaultfd」的記憶體
       │
       ▼
  kernel 不自己處理，把這個執行緒掛起，
  send 一則 fault 事件到 userfaultfd fd
       │
       ▼
  attacker 控制的另一個 thread 讀到這個事件──▶ 故意不理它,
  或者先去做別的事（這段時間，被掛起的執行緒完全動不了）
       │
       ▼
  attacker 準備好之後，才用 UFFDIO_COPY/UFFDIO_CONTINUE 回應，
  被掛起的執行緒這才繼續跑下去
```

### 為什麼它能拉長 kernel 內部的 race window

重點在於:**很多 kernel 的 check-then-use 路徑,中間會存取一塊 user-space 傳進來的記憶體**——例如 [Ch 40](./40-race-conditions.md) 的 double-fetch 場景,第二次 `copy_from_user` 讀取的目標,如果你把它背後對應的 user page **故意不預先 fault in、而是註冊成 userfaultfd 監控的對象**,那麼 kernel 執行到「第二次讀取」這一行、嘗試存取這塊記憶體時,會觸發 page fault——而這個 page fault 恰好發生在**你想要拉長的那個 window 裡**。kernel 執行緒(處理你的 ioctl 的那個 kernel thread)就會被掛起,停在「正要讀第二次」的那個瞬間,直到你(user-space)決定餵它資料繼續跑。

這給了你一個近乎無限長的視窗:在 kernel thread 被掛起的這段時間,你可以**從容地**完成任何你想做的競爭操作(改掉另一個欄位、觸發另一個 syscall、甚至什麼都不急著做,先確認狀態),準備好之後再回應 userfaultfd 事件,讓 kernel thread 繼續執行——這時 race 已經不是賭時序,是你完全掌控節奏的確定性操作。

```c
/* 概念骨架:用 userfaultfd 卡住一次 copy_from_user,未實測,為理論預期行為 */
#include <linux/userfaultfd.h>
#include <sys/ioctl.h>
#include <poll.h>

void *region = mmap(NULL, PAGE_SIZE, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

long uffd = syscall(SYS_userfaultfd, O_CLOEXEC | O_NONBLOCK);

struct uffdio_api api = { .api = UFFD_API, .features = 0 };
ioctl(uffd, UFFDIO_API, &api);

struct uffdio_register reg = {
        .range = { .start = (unsigned long)region, .len = PAGE_SIZE },
        .mode  = UFFDIO_REGISTER_MODE_MISSING,
};
ioctl(uffd, UFFDIO_REGISTER, &reg);

/* 把 region 裡某個欄位(例如前面章節示意的 uarg->len)擺在這塊記憶體,
   讓受害的 ioctl 第二次讀取這個欄位時,正好落在 region 上 */

pthread_t victim_thread;
pthread_create(&victim_thread, NULL, trigger_vulnerable_ioctl, region);
/* trigger_vulnerable_ioctl 內部呼叫會觸發兩次讀取的那個 ioctl */

/* 這個 thread 負責等 page fault 事件,等到了先不處理，
   先完成我方想做的競爭操作，再回應 */
struct uffd_msg msg;
struct pollfd pfd = { .fd = uffd, .events = POLLIN };
poll(&pfd, 1, -1);                 /* 等 page fault 事件 */
read(uffd, &msg, sizeof(msg));     /* 讀出事件,此刻 victim_thread 已被掛起 */

/* ---- victim_thread 現在卡在「正要讀 region」的那一刻,完全動不了 ----
   在這裡從容完成你想做的競爭操作(例如準備好要覆寫的內容) */

struct uffdio_copy copy = {
        .dst = (unsigned long)region,
        .src = (unsigned long)attacker_payload,   /* 這裡放你想讓第二次讀取讀到的內容 */
        .len = PAGE_SIZE,
};
ioctl(uffd, UFFDIO_COPY, &copy);   /* 餵資料，victim_thread 才會繼續跑下去 */
```

### 限制:`unprivileged_userfaultfd`

本章環境小節查證的事實在這裡發揮作用:**`vm.unprivileged_userfaultfd` 預設是 0**,意味著一般無特權 process 呼叫 `userfaultfd(2)` 會直接失敗(`EPERM`),除非該 process 有 `CAP_SYS_PTRACE`。這個限制本身就是防禦者對本節機制的直接回應——如果你的攻擊場景是「拿到的是一個完全無特權的本地使用者」,userfaultfd 這條路可能一開始就走不通,得確認目標系統這個 sysctl 的實際值,或退而求其次用技巧二(FUSE)。

## 技巧二:FUSE——讓 kernel 讀你的「檔案」時卡住

### 機制

FUSE(Filesystem in Userspace,使用者空間檔案系統)讓**一個完全跑在 user-space 的程式,充當一個檔案系統的實作**——kernel 的 VFS 層([Ch 34](./34-filesystem-attack-surface.md) 有更完整的檔案系統攻擊面介紹)把對這個檔案系統的操作(讀、寫、`stat`……)透過 `/dev/fuse` 轉送給你寫的 user-space daemon,daemon 決定怎麼回應,kernel 等 daemon 的回應才能繼續。

```
  某個 kernel 路徑需要讀一個檔案(這個檔案掛在你的 FUSE 掛載點下)
       │
       ▼
  VFS 把這個讀取請求轉送到 /dev/fuse
       │
       ▼
  attacker 寫的 FUSE daemon 收到請求──▶ 故意不立刻回應
       │  （這段時間，發出讀取請求的那個 kernel 執行緒完全被卡住）
       ▼
  attacker 準備好之後，才讓 daemon 回應這次讀取
       │
       ▼
  kernel 執行緒拿到回應，繼續往下跑
```

這和 userfaultfd 的精神一致,但卡住的「觸發點」不同:userfaultfd 卡的是**虛擬記憶體層的 page fault**,FUSE 卡的是**檔案系統層的 I/O 請求**。對攻擊者而言,FUSE 適用的情境是:victim 的 check-then-use 路徑裡,中間那一步是**讀/寫一個檔案**,而不是解參照一塊記憶體——例如某些 kernel 路徑會先 `stat` 一個檔案的屬性做檢查,之後才真正 `open`/`read` 它,如果這個檔案是掛在你控制的 FUSE 掛載點下,你就能在「stat 回來之後、真正 open 之前」的這段時間裡,讓 FUSE daemon 拖著不回應下一個請求,製造一個任意長度的 window。

```c
/* 概念骨架:FUSE daemon 故意拖延回應,未實測,為理論預期行為 */

/* 使用 libfuse 或直接操作 /dev/fuse 協定實作一個最小檔案系統,
   在 read callback 裡：
   1. 收到 kernel 對這個檔案的讀取請求
   2. 不立刻 reply（例如先 sleep，或等某個條件達成再 reply）
   3. 在這段延遲期間，另一個 attacker thread 去完成競爭操作
      （例如改掉這個檔案背後對應的某個 kernel 狀態、或觸發另一個相關 syscall）
   4. 條件達成後才真正呼叫 fuse_reply_buf() 回應，victim 路徑才繼續往下跑
*/
```

### FUSE 相對 userfaultfd 的優勢與限制

FUSE 掛載本身通常不需要 `CAP_SYS_PTRACE` 這類特權(一般使用者在多數 distro 預設可以掛載自己的 FUSE 檔案系統,視 `/etc/fuse.conf` 的 `user_allow_other` 等設定),所以在 `unprivileged_userfaultfd` 被收緊的系統上,FUSE 常常是**替代方案**。但它只能卡住「真的會經過檔案系統 I/O」的路徑——如果 victim 的 window 中間是解參照一塊匿名記憶體(不是檔案),FUSE 用不上,得回到 userfaultfd 或乾脆用 `tmpfs`/`shmem` 搭配其他手法。

## 技巧三:調度操控——綁 CPU、優先權、cache eviction

前兩個技巧卡住的是「kernel 執行緒在等某個特定事件」;第三個技巧不等事件,是**操控排程器(scheduler)的決策,讓 race 的兩邊更容易同時發生,或讓其中一邊的執行時間被放大**。這類手法比較依賴 [Ch 39](./39-concurrency-model.md) 的硬體與排程直覺,精確度不如前兩者,但不需要 `userfaultfd`/FUSE 任何一種特權或掛載權限,門檻最低。

- **CPU affinity 綁定(`sched_setaffinity`)**:把攻擊 thread 和 victim thread 刻意綁到**不同的實體核心**,確保它們是真正同時執行(不是單核心上的交錯),這是觸發任何 SMP race 的基本前提——[Ch 39](./39-concurrency-model.md) 強調過,SMP 的「真正同時」才是本書討論的 race 模型,如果兩個 thread 被排程到同一個核心上輪流執行,很多類型的 race 窗口反而會因為時間片輪轉的特性變得難以預測。
- **優先權操控(`sched_setscheduler`、`nice`)**:把其中一個 thread 的優先權提高(例如使用 `SCHED_FIFO` 這類即時排程策略,需要相應的 `CAP_SYS_NICE` 權限,或在某些系統預設允許有限度使用),讓它在關鍵時刻能搶到 CPU、不被排程器任意搶占,行為更可預測。
- **Cache eviction 與時間放大**:讓受害路徑裡「讀某塊記憶體」這個動作變慢——如果你能讓目標資料結構被踢出 CPU cache(例如故意存取大量其他記憶體把 cache line 洗掉),victim thread 下一次讀取那塊資料時要付出 cache miss 的代價(從 L1/L2 cache 升到 L3 甚至主記憶體,延遲可以放大一個數量級),這段被拉長的存取時間,就是攻擊 thread 的機會窗口。[Ch 40](./40-race-conditions.md) 延伸閱讀提到的 Project Zero `dup`/`__fget_files` race 寫法,核心手法正是**刻意讓目標 `struct file` 保持在 cache 之外**,把原本僅有十餘條指令長度的窗口,放大到攻擊 thread 來得及完成操作的程度。

這三種調度操控手法通常不是單獨使用,而是**和前兩種技巧搭配**:先用 CPU affinity 確保兩條路徑真的在不同核心同時跑,再用 userfaultfd/FUSE 卡住關鍵的一步,最後用 cache eviction 處理剩下那些「卡不住、只能靠放大」的窄縫——組合越完整,命中率越接近 100%。

## 具體例子

### 例子一:userfaultfd 拉長一個教學用 double-fetch(對照 Ch 40)

延續 [Ch 40](./40-race-conditions.md) double-fetch 一節的教學靶:ioctl 第一次讀 `len` 做檢查、配置 buffer,第二次又重新讀了一次 `uarg->len`。把 `uarg` 指向的這塊記憶體換成一塊你用 `mmap` 配置、並註冊進 userfaultfd 的匿名記憶體:

```
  Step 1：攻擊 thread A 呼叫這個 ioctl，傳入的 uarg 指向 userfaultfd 監控的記憶體，
          第一次讀取（len=小值，通過檢查，kmalloc 小 buffer）完成（這次讀取發生在
          記憶體已經被 UFFDIO_COPY 填過一次內容的情況下，不會 fault）。

  Step 2：程式碼走到「第二次讀取 uarg->len」這一行——
          這次換成讀取記憶體的另一部分（或者你故意先用 UFFDIO_ZEROPAGE/
          madvise 讓這塊記憶體回到「未填充」狀態），觸發 page fault。

  Step 3：kernel thread（正在執行這個 ioctl）被掛起，送出 fault 事件。

  Step 4：攻擊 thread B 收到事件，這時從容把 uarg->len 對應的那塊記憶體內容，
          用 UFFDIO_COPY 換成一個大得多的值。

  Step 5：回應 userfaultfd 事件，kernel thread 繼續跑，
          「第二次讀取」讀到的是 Step 4 換上去的大值，
          配置時用的是 Step 1 的小 buffer，接下來的 copy 就是 heap overflow。
```

這個流程把 [Ch 40](./40-race-conditions.md) 裡「中間 window 只有幾奈秒,幾乎碰不上」的 double-fetch,變成了**確定性**觸發——只要 Step 2 真的會觸發 page fault(這取決於你能不能精確控制「這塊記憶體目前是否已填充」的狀態),Step 3 到 Step 5 完全在你的控制之下,不再需要賭時序。這段流程**未實測,為理論預期行為**,親手驗證需要一個真正會做兩次讀取的教學靶和前述 userfaultfd 的完整 API 操作序列。

### 例子二:技巧選錯造成的失敗

假設你想拉長的 window,是某個 kernel 路徑「檢查一個 socket fd 的狀態,之後才真正對它操作」這種 TOCTOU——中間沒有任何一步涉及 user-space 記憶體的 page fault,也不涉及檔案系統 I/O。這時你套用技巧一(userfaultfd):把一塊匿名記憶體註冊進去,期待它能卡住這條路徑——結果什麼都沒發生,因為**這條 victim 路徑根本沒有任何一步會去存取你註冊的那塊記憶體**,userfaultfd 的掛起機制只對「真的存取到被監控記憶體」的執行緒生效,它不是一個通用的「讓任意 kernel 路徑變慢」的旋鈕。

正確的判斷順序應該是:先確認 window 中間那一步,victim 到底在做什麼動作(解參照記憶體?讀寫檔案?單純的計算或鎖操作?),再決定用哪個技巧——這對應回 [Ch 40](./40-race-conditions.md) 分類三種模式時強調的「填清楚 check 和 use 分別是什麼動作」,這個分析在這裡直接決定了你該選技巧一、二,還是只能退回技巧三的調度操控。

## 對比與取捨

| 技巧 | 卡住的動作 | 特權需求 | 精確度 | 限制 |
|---|---|---|---|---|
| userfaultfd | 虛擬記憶體的 page fault | 需 `CAP_SYS_PTRACE`(`unprivileged_userfaultfd=0` 時) | 高(近乎無限長、你完全掌控恢復時機) | 只對「真的存取到被監控記憶體」的路徑生效;sysctl 可能擋死 |
| FUSE | 檔案系統 I/O | 一般掛載權限(視 distro 設定) | 高(同上,但只對檔案 I/O 路徑生效) | 只適用於 window 中間是檔案讀寫的情境 |
| 調度操控(affinity/優先權/cache eviction) | 任意 CPU 排程行為、記憶體存取延遲 | 低(多數手法不需特殊 capability) | 低到中(放大而非確定性卡住) | 精確度不如前兩者,常需要搭配使用 |

讀這張表的方式:**先問 window 中間那一步是什麼動作**(解析上一節例子二的教訓),決定技巧一或二能不能用;**用不上時,技巧三是保底方案,但要接受較低的確定性**,通常需要大量重試並搭配統計手法確認命中率。

## 踩雷集錦

1. **以為 userfaultfd 能卡住任何 kernel 路徑**——它只對「執行到了真的會存取被監控記憶體」的那一刻生效。如果 window 中間的動作不涉及記憶體存取(例如純粹的邏輯判斷、或存取的是另一塊沒被監控的記憶體),userfaultfd 完全無效,見本章例子二。
2. **忽略 `unprivileged_userfaultfd` 的 sysctl 狀態就假設技巧一一定能用**——這個 sysctl 預設是 0,在很多真實系統(尤其是收緊過安全設定的 distro、容器環境)上,無特權 process 連 `userfaultfd(2)` 都呼叫不了。拿到新目標先查這個 sysctl,決定要不要改走 FUSE 或調度操控。
3. **FUSE daemon 拖延回應拖得太久,觸發 kernel 自己的逾時/看門狗機制**——某些呼叫路徑對下游 I/O 有逾時保護,daemon 拖太久可能導致呼叫直接失敗、或觸發額外的錯誤處理路徑(這本身也可能改變你原本分析的攻擊鏈)。拖延時間要抓一個「夠長到你能完成操作,但不會觸發逾時」的中間值。
4. **只用 CPU affinity 綁定兩個 thread 到不同核心,就以為 race window 自動拉長**——綁定只保證兩者真正同時執行,不等於窗口變長。它是觸發 SMP race 的**必要條件**,不是**拉窗手段**,真正拉長窗口還是要靠技巧一、二,或額外的 cache eviction。
5. **把拉窗技巧當成萬能工具,忽略了部分 kernel 子系統已經對特定路徑加了針對性的互斥(mutex)保護**——拉長 window 讓你更容易「撞上」原本很窄的縫,但如果 [Ch 39](./39-concurrency-model.md) 教的同步機制本身在那段路徑上是完整的(鎖包住了整段 check-then-use),拉再長的 window 也撞不進去。拉窗技巧是放大已存在的縫,不是製造新的縫。

## 進階:再往深一層

### 防禦視角:`unprivileged_userfaultfd` 的取捨史

本章環境小節查證到的時間線值得多說一句,因為它本身是一個「防禦者在攻防資訊不對稱下調整預設值」的典型案例:這個 sysctl 在 2019 年由 Peter Xu 的 patch 引入時,預設值是 **1**(開放);後續討論(我們查到 2020 年的一系列 patch 與 LKML 討論)中,有開發者明確指出「攻擊者可以用 userfaultfd 拖延 kernel page fault 的處理,藉此拉長其他漏洞的利用窗口」這個威脅模型,促成了預設值改回 **0**。這個修改本身**沒有修補任何一個具體的記憶體漏洞**,它修補的是「讓所有記憶體漏洞的利用都變得更容易穩定觸發」這個放大器——這和 [Ch 26](./26-modern-mitigations.md) 討論的「第二代 mitigation 打破可預測性,而不是守最後一步」是同一種防禦哲學的體現:封鎖拉窗工具,不直接擋任何一個特定 bug,但讓所有 race 類 bug 的利用成本整體上升。

### 這些技巧和 heap grooming 的交集

[Ch 20](./20-heap-grooming.md) 教過用 `setxattr` 的臨時 buffer 配合「阻塞技巧」讓一塊可控記憶體「卡住不釋放」——那一章當時只是點出了這個需求,把細節留給本章。現在你知道了,那個「阻塞技巧」具體就是本章的 userfaultfd 或 FUSE:在 `setxattr` 執行到一半、那塊暫時配置的 buffer 還活著的時候,用這兩種技巧之一卡住執行緒,讓那塊原本「用完即釋放」的記憶體,延長存在的時間,變成一個可控的 spray 元件。race 拉窗技巧和 heap grooming 不是兩個獨立的主題,它們在真實 exploit 裡經常是同一套操作的兩面。

## 動手練習

> 全部在你自己的 VM/靶機操作,前兩項**未實測,為理論預期行為**。

1. 在你自己的環境查 `sysctl vm.unprivileged_userfaultfd`,確認目前的值。如果是 0,嘗試以無特權使用者呼叫 `userfaultfd(2)`,確認收到的錯誤是 `EPERM`;再用 `sudo sysctl -w vm.unprivileged_userfaultfd=1` 臨時改回 1(只在你自己受控的環境操作),重試確認能成功。
2. 實作本章例子一的完整流程(對照 [Ch 40](./40-race-conditions.md) 動手練習第 2 題的教學 double-fetch module),用 userfaultfd 卡住第二次讀取,觀察命中率相較於 [Ch 40](./40-race-conditions.md) 不用拉窗技巧時的差異(建議各跑 50 次記錄命中次數)。
3. 寫一個最小的 FUSE 檔案系統(可以用 `libfuse` 的 high-level API,`read` callback 故意 `sleep(5)` 才回應),搭配一個會對這個掛載點下的檔案做「stat 後幾行才真正 open」的教學程式,體會「卡住 I/O 路徑」和「卡住記憶體存取路徑」在使用上的差異。
4. 讀 2020 年 LKML 關於 `unprivileged_userfaultfd` 預設值變更的討論串(在 lore.kernel.org 搜尋 "Control over userfaultfd kernel-fault handling"),寫下作者列出的威脅模型,和本章「進階」小節的描述對照。

## 本章重點整理

- 拉長 race window 的共同精神是「卡住 victim,不是加速 attacker」。
- userfaultfd 卡住的是虛擬記憶體的 page fault,精確度最高,但受 `unprivileged_userfaultfd`(預設 0)限制。
- FUSE 卡住的是檔案系統 I/O 請求,精確度相近,門檻通常較低,但只適用於 window 中間是檔案讀寫的情境。
- 調度操控(CPU affinity、優先權、cache eviction)不卡住任何事件,而是放大時間差,精確度較低,常作為前兩者的輔助或兩者都用不上時的保底。
- 這三種技巧的選擇,取決於 [Ch 40](./40-race-conditions.md) 分析出的「window 中間 victim 到底在做什麼動作」。
- `unprivileged_userfaultfd` 的預設值變更史,是「封鎖放大器而非封鎖單一漏洞」這種防禦哲學的具體案例。

## 自我檢核

- [ ] 不看筆記,能解釋為什麼「卡住 victim」比「加速 attacker」更有效
- [ ] 能說出 userfaultfd 和 FUSE 分別卡住哪一類 kernel 操作,以及如何判斷該用哪一個
- [ ] 能解釋為什麼 CPU affinity 綁定本身不是拉窗手段,而是觸發 SMP race 的必要條件
- [ ] 知道 `unprivileged_userfaultfd` 的預設值,以及它被收緊的理由
- [ ] 想一個你在 [Ch 40](./40-race-conditions.md) 分析過的 race 候選:它的 window 中間動作是什麼?這決定了你會選哪個技巧

## 延伸閱讀

### 官方文件

- **[Documentation/admin-guide/mm/userfaultfd.rst](https://docs.kernel.org/admin-guide/mm/userfaultfd.html)**
  - **讀哪裡**：整篇,特別是 `unprivileged_userfaultfd` 一節與其預設值說明。
  - **和本章的關聯**：本章環境小節查證的直接出處;前提知識是本章技巧一。

### Patch 與討論串

- **[[PATCH 1/3] userfaultfd/sysctl: introduce unprivileged_userfaultfd（Peter Xu，LKML 2019）](https://lkml.iu.edu/hypermail/linux/kernel/1903.1/01648.html)**
  - **這篇說什麼**：這個 sysctl 第一次被引入時的設計動機與預設值(當時是 1)。
  - **讀哪裡**：patch 的 commit message,說明為什麼需要一個開關而不是直接禁止無特權使用。
  - **和本章的關聯**：本章「進階」小節的時間線起點。
- **[[PATCH v5 0/2] Control over userfaultfd kernel-fault handling（2020,LKML 討論串）](https://lore.kernel.org/linux-doc/20201024020843.GB19707@redhat.com/T/)**
  - **這篇說什麼**：把預設值收緊為 0 的討論,明確列出「拉長其他漏洞利用窗口」的威脅模型。
  - **讀哪裡**：討論串裡提出威脅模型的那幾則回覆。
  - **和本章的關聯**：本章「進階」小節的核心佐證;動手練習第 4 題直接對應。

### 技術報告 / 部落格

- **[James Forshaw（Project Zero）— Windows Exploitation Tricks: Trapping Virtual Memory Access](https://projectzero.google/2021/01/windows-exploitation-tricks-trapping.html)**
  - **這篇說什麼**：雖然主題是 Windows,但文中明確點出 Linux 上 userfaultfd 是這類「卡住記憶體存取」技巧的第一個範例,FUSE 可以達到類似效果,是理解「卡住 victim」這個通用設計思路的跨平台參照。
  - **讀哪裡**：開頭對 Linux userfaultfd/FUSE 的簡短對照段落,以及後續 Windows 對應機制的详細部分可視興趣選讀。
  - **為什麼值得讀**：作者是 Project Zero 的資深研究員,這篇把「拖延記憶體存取以拉長 race window」當成一個跨平台的通用攻擊模式來討論,幫你把本章技巧放進更大的攻防脈絡。
- **[Project Zero — 分析 `dup`/`__fget_files` race 的相關研究](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：示範如何透過讓目標資料結構脫離 CPU cache,把原本僅十餘條指令的視窗放大到可利用的程度——技巧三(cache eviction)的實戰案例。
  - **讀哪裡**：搜尋站內與 `__fget_files`/race 相關的文章。
  - **和本章的關聯**：本章技巧三 cache eviction 一節引用的具體案例;前提知識是 [Ch 39](./39-concurrency-model.md) 對 CPU cache 與記憶體存取延遲的基礎認識。

三個拉窗技巧都講完了,你現在手上有辨認 race(Ch 40)和把它變穩定(本章)兩套能力。接下來的練習,要求你把這兩章和 Part 3 的 heap 利用方法論全部串起來,針對一個具體的 double-fetch 或 TOCTOU UAF,寫出一個完整的 race condition exploit 骨架。

→ [練習 F — 寫一個 race condition exploit（userfaultfd 拉 window）](./practice-f-race-exploit.md)
