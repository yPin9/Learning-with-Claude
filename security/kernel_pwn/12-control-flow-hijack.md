# Ch 12 — 控制流劫持基礎：劫 RIP、commit_creds(prepare_kernel_cred(0))、安全回 userland

> **目標**：把 [Ch 11](./11-stack-overflow.md) 一帶而過的「拿到 RIP 之後那半段」拆開講透。學完你要能回答三個問題：提權那一行咒語在 CPU 層面到底改了什麼？那兩個函式的位址怎麼找？以及——最多人卡死的——怎麼把 CPU 從 ring 0 乾淨切回 ring 3，讓原本的 process 帶著 root 身分活著回到 user-space。這一章和漏洞種類無關，不管你靠 stack overflow、UAF 還是任意寫拿到控制流，收尾都走這裡。

> **環境**：Linux 6.6 LTS、x86-64、QEMU；延續 Ch 11 的 `nokaslr nosmep nosmap`，但 KPTI 分成開/關兩種情況討論。所有 exploit code 未在本環境實測（需自有 QEMU 靶），標為理論預期行為。

## 為什麼把這段獨立成一章

因為「控制流劫持之後要做什麼」是**所有** memory corruption 利用鏈的共同終點。stack overflow 給你乾淨的 RIP、UAF 給你劫持一個函式指標、任意寫讓你改一個 ops table——入口千百種，但一旦 RIP 到手，接下來「提權 + 回 user」這套收尾是共用的。把它學成一個獨立模組，以後每條鏈都直接套。

而且這段是初學者 panic 次數最多的地方。提權那一行幾乎不會錯；**回程**才是反覆 panic 的來源——swapgs 漏了、iretq frame 擺反了、KPTI 沒處理。把回程的每一步對應到 CPU 在做什麼，panic 就從「玄學」變成「可讀的錯誤」。

## 先建立直覺：你劫持的是一條「借來的執行緒」

想像你的 process 呼叫一個 syscall，CPU 借了你的 thread、切到 ring 0、在 kernel stack 上跑 kernel 的 code。正常情況它跑完會**原路切回** ring 3，你的 process 繼續。

```
 ring 3 (你的 process)         ring 0 (借來的執行)
 ───────────────────          ───────────────────
 syscall  ───────────────────▶ entry：swapgs，切 CR3，存 user 暫存器到 pt_regs
                               ... kernel 幹活 ...
 繼續跑  ◀─────────────────── exit：還原 pt_regs，切 CR3，swapgs，sysret/iretq
```

控制流劫持就是在「kernel 幹活」那段把 RIP 搶過來。你搶過來之後**也必須自己走完那條 exit 路**——還原 GS、還原 CR3（KPTI）、還原 user 的 cs/ss/rflags/rsp/rip——否則 CPU 帶著 kernel 的狀態回 user，或根本回不去，就 panic。回程 trampoline 的本質，就是**手動重做 kernel 正常的 exit 路**。

## 提權咒語在 CPU 層面做了什麼

```c
commit_creds(prepare_kernel_cred(NULL));
```

拆開看：

- 每個 process（`task_struct`）有一個指標 `cred`，指向一份 `struct cred`——裡面是 uid/gid、各種 capability set（`cap_effective` 等）。核心做權限判斷時讀的就是 `current->cred`。
- `prepare_kernel_cred(NULL)` 配一份新的 `struct cred`，內容是 kernel/root 樣板：uid=gid=0、capability 全開。
- `commit_creds(new)` 把 `current->cred`（和 `real_cred`）換成這份新的，並處理舊 cred 的 refcount。

所以「提權」在資料層面只是**一個指標換指向**。這也解釋了為什麼拿到**任意寫**（不用控 RIP）也能提權——直接找到 `current->cred` 指到的那份 struct，把它的 uid 欄位寫成 0 即可（[Ch 16](./16-classic-primitives.md) 的 data-only 路線）。函式呼叫版只是在你有乾淨 RIP 時比較順手。

> `prepare_kernel_cred` 的參數語意是**版本相關**的坑：早期傳 `NULL` 即以 init cred 為樣板；某些版本（約 6.2 前後的改動）對傳 `NULL` 的處理變了，建議傳 `&init_task` 或直接讀你 kernel 的 source 確認。照抄十年前 writeup 的 `prepare_kernel_cred(0)` 在新 kernel 可能拿到非預期的 cred。（版本差異，未逐版實測。）

## 怎麼找這兩個函式的位址

三條路，按真實程度排：

1. **`/proc/kallsyms`**（教學/本機）：`grep` 得到符號位址。但預設對非 root 顯示為 0（`kptr_restrict`），所以這招通常只在你能 root 的靶機上拿「答案」來對照，不是 exploit 內的手段。
2. **固定位址 + `nokaslr`**（本章簡化）：KASLR 關時位址固定，從 gdb（`p commit_creds`）或 root 的 kallsyms 查到後寫死進 exploit。只在教學環境成立。
3. **leak kernel base + 靜態 offset**（真實題）：KASLR 開時，kernel image 整塊平移一個隨機量。你只要 leak **任何一個** kernel text 位址，就能算出 base，再加上「`commit_creds` 相對 base 的固定 offset」（從你的 vmlinux 算出）得到實際位址。這是 [Ch 14](./14-kaslr-infoleak.md) 的核心。

關鍵心智模型：KASLR 下你不是逐一 leak 每個函式，而是 **leak 一次 base、其餘全用 offset 推**。offset 來自你手上那份 vmlinux，和目標同一個 build 就準。

## 回程 trampoline：手動重做 kernel exit

這是本章的重頭。目標：從 ring 0 切回 ring 3，落在你指定的 user 函式，且狀態乾淨。需要的五個值（`iretq` 會按順序從 stack 取）：

```
  iretq 期望的 stack frame（由低到高）：
  ┌──────────┐  ← rsp 指這
  │  RIP     │  回 user 後執行的位址（你的 win()）
  │  CS      │  user code segment（= 進 kernel 前存的 %cs）
  │  RFLAGS  │  user flags（進 kernel 前存的）
  │  RSP     │  user stack pointer（進 kernel 前存的 %rsp）
  │  SS      │  user stack segment（= 進 kernel 前存的 %ss）
  └──────────┘
```

這五個值裡，CS/SS/RFLAGS/RSP 必須是**你這個 process 進 kernel 之前**的真實值——所以要在觸發 bug 前先存（[Ch 11](./11-stack-overflow.md) 的 `save_state`）。RIP 填你想落回的函式。

除了擺好 frame，切回去之前還有兩件 CPU 狀態要處理：

- **`swapgs`**：kernel 進入時做過一次 `swapgs`，把 `GS_BASE` 從 user 的換成 per-cpu 的。回 user 前要再 `swapgs` 換回來。漏了它，user-space 一碰 TLS/`%gs` 相關就爆。
- **CR3（只在 KPTI 開時）**：KPTI 讓 kernel 與 user 用兩套 page table。回 user 前要把 CR3 切成 user 的那套。純 `swapgs; iretq` 在 KPTI 開時**不夠**——這正是本章把 KPTI 單獨拉到 [Ch 15](./15-kpti.md) 的原因。

本章（KPTI 關）的最小回程：

```c
/* escalate() 結尾：提權完，手動切回 ring 3 */
__asm__ volatile(
    "swapgs\n"               /* GS base 換回 user */
    "movq %0, %%rsp\n"       /* 把 rsp 指到我們排好的 iretq frame */
    "iretq\n"
    :: "r"(iret_frame));     /* iret_frame = [rip, cs, rflags, rsp, ss] */
```

`iret_frame` 在 C 裡先填好：

```c
unsigned long frame[5];
frame[0] = (unsigned long)&win;   /* RIP：回 user 落點 */
frame[1] = user_cs;               /* save_state 存的 */
frame[2] = user_rflags;
frame[3] = user_sp;
frame[4] = user_ss;
```

### KPTI 開時：為什麼要「借官方的門」

KPTI 開著時，自己手刻 CR3 切換很難寫對。真實題的通行做法是**不自己刻**，而是跳到 kernel 自己的 exit trampoline——`swapgs_restore_regs_and_return_to_usermode`。它是 kernel 離開 ring 0 時本來就會走的那段，已經正確處理了 swapgs + CR3 切換。你只要把 RIP 劫到它「中段」（跳過前面 pop 暫存器的幾條指令），再在 stack 上擺好它期望的 frame，它會替你把 CPU 乾淨送回 user。細節、精確的進入 offset、以及另一條「用 signal handler 回程」的替代路，都在 Ch 15。本章先知道：**KPTI 一開，`swapgs; iretq` 這招就要升級成借官方 trampoline。**

## 一個常被忽略的替代收尾：signal 回程

有時候你劫持的上下文很難乾淨 `iretq`（例如你在一個中斷/軟中斷 context，或 stack 狀態詭異）。另一招是：**先註冊好一個 signal handler**，提權完**故意觸發一個 fault**（例如解一個壞指標），讓 kernel 把你送進 signal handler——而 signal 遞送本身會把你帶回 user-space。你在 handler 裡醒來時已是 user-space、已 root。這招對「回程環境很糟」的情況特別有用，Ch 15 會和 KPTI trampoline 一起比較。

## 不靠 RIP 的提權：data-only 預告

本章講的是「有乾淨 RIP」的收尾。但 2024 之後 CFI/KCFI（[Ch 28](./28-cfi-kcfi.md)）讓「劫持函式指標去呼叫 `commit_creds`」越來越難——因為 CFI 會檢查「這個間接呼叫的目標型別對不對」。於是主流轉向**完全不劫控制流**的提權：

- 有任意寫 → 直接寫 `current->cred->uid = 0`（找 cred 的方法 Ch 16）。
- 改 `modprobe_path` / `core_pattern` 字串，誘導 kernel 以 root 執行你的程式（Ch 16）。
- Dirty Pagetable（[Ch 23](./23-dirty-pagetable.md)）/ Dirty Cred（[Ch 24](./24-dirty-cred.md)）：連 kernel 位址都不用 leak。

本章的函式呼叫版是**基礎**，不是**唯一**。先學會它，才懂後面 data-only 為什麼要繞開它。

## 踩雷集錦

1. **swapgs 漏做或做兩次**——回 user 前少 swapgs，user-space 第一次碰 `%gs`（TLS、stack canary 的 user 版）就爆；在已經 swapgs 的 context 又多做一次，等於沒做。要搞清楚你劫持的那個點**此刻 GS 是哪一套**。
2. **iretq frame 順序記反**——順序是 `RIP, CS, RFLAGS, RSP, SS`（低到高）。把 CS/SS 或 RSP/RIP 擺反，`iretq` 會用錯的 segment 或跳到錯位址，典型症狀是回 user 瞬間 `general protection` 或跳進亂碼。
3. **用「現在」的暫存器當 user 狀態**——`save_state` 必須在**觸發 bug 前**跑。bug 觸發後你在 kernel，讀到的 `%cs`/`%rsp` 是 kernel 的，拿去 `iretq` 必崩。
4. **KPTI 開著還用 `swapgs; iretq`**——會回到 user 但 page table 還是 kernel 那套（user 視角下大半位址無效），通常回 user 後立刻 fault。KPTI 要走 trampoline（Ch 15），不是調 flag 就好。
5. **提權成功但 `getuid()` 非 0 就斷定提權失敗**——先分清是**提權**還是**回程**出錯。用 gdb 在 `commit_creds` 下斷點，確認 cred 真的換了；若換了但 `getuid()` 還不是 0，問題在回程把你送到了錯的 process/context，不在提權。

## 進階：把收尾寫成可重用的一段

成熟的 exploit 會把「save_state + escalate + 回程」封成一個固定模板，和漏洞部分解耦。好處是換題時只改「怎麼拿到 RIP」這半段，收尾直接複用。真實 kernelCTF submission 幾乎都這樣組織：一個 `prepare()` 存狀態、一個 `escalate()` 提權、一個 `kpti_trampoline` 處理回程，main 裡觸發 bug 把 RIP 導到 `escalate`。把本章和 Ch 15 的收尾合起來做成你自己的 header，是很值得的投資。

另外，`commit_creds`/`prepare_kernel_cred` 不是唯一能被劫去呼叫的函式。有些題控制流落點受限（只能跳固定 offset），這時改用「一個 call-oriented 的 gadget 幫你呼叫」或乾脆走 data-only，比硬湊函式呼叫實際。控制流劫持是手段不是目的——目的始終是「換掉身分」或「拿到任意讀寫」。

## 動手練習

在自有 QEMU 靶（KPTI 先關）上：

1. 寫一個最小的 `save_state` + `iret_frame`，RIP 不導去提權、直接導回 `win()`，驗證「純回程」能不能乾淨回到 user（排除提權變因，先把回程調對）。
2. 回程 OK 後，在中間插 `commit_creds(prepare_kernel_cred(NULL))`，拿 root shell。
3. 把 KPTI 打開重跑，觀察步驟 2 現在怎麼 fault——這就是 Ch 15 要解的問題，先親眼看到它壞。

## 本章重點整理

- RIP 到手後的收尾（提權 + 回 user）是所有 corruption 鏈的共用終點，值得抽成獨立模組。
- 提權 = 把 `current->cred` 換成 root 樣板；資料層面只是換一個指標，所以任意寫也能做到。
- 回程 = 手動重做 kernel exit：擺好 `iretq` frame（RIP/CS/RFLAGS/RSP/SS）、swapgs、（KPTI 開則切 CR3）。
- KPTI 開時 `swapgs; iretq` 不夠，要借 `swapgs_restore_regs_and_return_to_usermode`（Ch 15）。
- CFI 時代主流轉向 data-only 提權，但函式呼叫版是理解那些繞法的基礎。

## 自我檢核

- [ ] 能用「借來的執行緒要原路還回去」解釋為什麼劫持後必須自己走 exit 路
- [ ] 能說出 `commit_creds(prepare_kernel_cred(NULL))` 在資料層面只是換一個指標，並據此解釋任意寫為何也能提權
- [ ] 不看筆記，能寫出 `iretq` frame 的五個值與順序，以及各自從哪來
- [ ] 能解釋 swapgs 的作用，以及為什麼 KPTI 開著時 `swapgs; iretq` 不夠
- [ ] 遇到「提權後 getuid 非 0」，能說出用什麼方法區分是提權錯還是回程錯

## 延伸閱讀

- **[swapgs_restore_regs_and_return_to_usermode 原始碼（arch/x86/entry/entry_64.S）](https://elixir.bootlin.com/linux/v6.6/source/arch/x86/entry/entry_64.S)**
  - **讀哪裡**：搜 `swapgs_restore_regs_and_return_to_usermode`；看它 pop 暫存器、swapgs、切 CR3、`iretq` 的完整順序。
  - **和本章的關聯**：本章的回程就是手動重做這段；Ch 15 會教你劫到它的哪個 offset。讀 source 才知道「借官方 trampoline」借的是什麼。
- **[Learning Linux Kernel Exploitation（Midas）](https://0x434b.dev/dissecting-and-exploiting-tcp-ip-rce-vulnerability-eternalblue/)** 之外，推薦 **[lkmidas: Learning Linux Kernel Exploitation Part 1-3](https://lkmidas.github.io/posts/20210123-linux-kernel-pwn-part-1/)**
  - **這篇說什麼**：三篇一路從環境、ret2usr、到 bypass SMEP/KPTI，回程 trampoline 寫得很清楚、有完整 code。
  - **讀哪裡**：Part 1 的 "Returning to userland" 與 Part 2 的 KPTI 段。
  - **為什麼值得讀**：把本章的回程和 Ch 13/15 串成一條可跑的線，程式碼可直接對照。
- **[struct cred 與 commit_creds（kernel/cred.c、include/linux/cred.h）](https://elixir.bootlin.com/linux/v6.6/source/kernel/cred.c)**
  - **讀哪裡**：`struct cred` 定義（uid/gid/cap_* 欄位）與 `commit_creds` 本體。
  - **前提知識**：Ch 10 的 refcount 概念；注意 cred 也是 refcount 管理的，提權時舊 cred 的 put 不要漏。

下一章把本章刻意關掉的 SMEP/SMAP 加回來——ret2usr 這條路一旦被封，我們就得把整條執行留在 kernel space，改用 kernel ROP 與 stack pivot。

→ [Ch 13 — SMEP / SMAP：ret2usr 封死後的 kernel ROP 與 stack pivot](./13-smep-smap.md)
