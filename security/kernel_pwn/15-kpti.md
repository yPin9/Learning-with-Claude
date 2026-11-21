# Ch 15 — KPTI：swapgs_restore_regs_and_return_to_usermode 與 signal trampoline

> **目標**：補上全 mitigation 的最後一道牆。學完你要能解釋 KPTI 為什麼存在、它怎麼讓 [Ch 12](./12-control-flow-hijack.md) 的 `swapgs; iretq` 回程失效，以及兩條正解——借 kernel 自己的出口 trampoline `swapgs_restore_regs_and_return_to_usermode`，或用 signal handler 回程。這一章把 Ch 11–14 串起來：到本章結束，你具備在「KASLR + SMEP + SMAP + KPTI + canary 全開」下打穿一題 stack overflow 的全部零件，正好接練習 B。

> **環境**：Linux 6.6 LTS、x86-64、QEMU。KPTI 預設開；要關用 cmdline **`pti=off`**（注意不是 `kpti=`——那是 ARM64 的參數名，x86 用 `pti=`）。trampoline 的精確進入 offset 依你的 vmlinux，exploit code 未在本環境實測，標為理論預期行為。

## 為什麼需要 KPTI：從 Meltdown 說起

2018 年初的 Meltdown（CVE-2017-5754）打破了一個大家以為牢不可破的假設：user-space 竟然能透過 CPU 亂序執行的側信道，讀到映射在自己 page table 裡、但標記為「僅 kernel 可存取」的 kernel 記憶體。問題的根在**硬體**，但 Linux 得在軟體層擋——做法就是 KPTI（Kernel Page Table Isolation）。

KPTI 的想法很暴力：既然「kernel 記憶體映射在 user 的 page table 裡」是 Meltdown 的前提，那就**把 kernel 的映射從 user 的 page table 裡幾乎全部拿掉**。user-space 跑的時候，它的 page table 裡只剩極少數進出 kernel 必需的跳板（entry/exit code、部分 cpu_entry_area）；真正的 kernel 要等切進 ring 0、換上**另一套** page table 才看得到。

```
  KPTI 之前：一套 page table
     user 跑時：  [ user 映射 | kernel 映射全在 ]  ← Meltdown 從這裡偷讀 kernel

  KPTI 之後：兩套 page table，用 CR3 切換
     user 跑時 (user CR3)：  [ user 映射 | 只剩 entry/exit 跳板 ]
     kernel 跑時(kernel CR3)：[ user 映射 | kernel 映射全在 ]
     進出 kernel 時切 CR3
```

代價是每次進出 kernel 多一次 CR3 切換（TLB 成本），這也是當年 Meltdown 緩解「讓系統變慢」的來源。對我們攻擊者，重點是那句話：**進出 kernel 要切 CR3。**

## KPTI 怎麼弄壞我們的回程

[Ch 12](./12-control-flow-hijack.md) 的回程是 `swapgs; iretq`——它處理了 GS base，但**沒碰 CR3**。在 KPTI 開著時，你劫持控制流那一刻跑在 **kernel CR3**（kernel 映射齊全）；你直接 `iretq` 回 user，CPU 切回 ring 3 了，但 **CR3 還是 kernel 那套**。

後果：user-space 的程式碼現在用 kernel page table 去跑。user 的大部分映射在 kernel CR3 裡**不存在**（KPTI 把 user 跑時該有的那套關係反過來了）——於是回到 user 的第一條指令通常就 page fault，而且是在一個 CR3 錯亂的狀態下 fault，往往直接 panic 或無窮 fault。

```
  劫持點（kernel CR3）
     │ swapgs; iretq      ← Ch 12 的回程
     ▼
  ring 3，但 CR3 還是 kernel 那套
     │ 執行 user 第一條指令
     ▼
  page fault（user 映射在此 CR3 下不對）→ 崩
```

結論：**KPTI 一開，回程就必須包含「切回 user CR3」。** 自己手刻 CR3 切換（讀出 user CR3、設好 bit、`mov cr3`）很容易寫錯，所以正解是不自己刻。

## 正解一：借 kernel 的出口 trampoline

kernel 自己離開 ring 0 回 user 時，走的就是一段**已經正確處理 swapgs + CR3 切換**的 code：`swapgs_restore_regs_and_return_to_usermode`（在 `arch/x86/entry/entry_64.S`）。我們不重寫它，直接**跳進去借用**。

它的結構大致是（概念，實際以你的 vmlinux 反組譯為準）：

```asm
swapgs_restore_regs_and_return_to_usermode:
    ... pop r15; pop r14; ... pop rdi      ; 還原一堆被 callee 存的暫存器
    mov rdi, rsp                           ; 準備切 CR3 的暫存器
    ... 切到 user CR3 ...
    swapgs
    iretq                                  ; 真正回 user（期望 stack 上有 iretq frame）
```

關鍵技巧：我們**不從頭**跳進去（頭段那些 `pop` 會吃掉我們不想給的東西），而是跳到它**中段**——跳過前面還原暫存器的部分，落在「開始切 CR3」之前的某個點。常見做法是用符號位址 **加一個固定小 offset**（通常跳過最前面幾個 pop，網路上寫 workflow 常見 `+22`、`+0x16` 之類，**但這個 offset 依版本/build 不同，必須對你的 vmlinux 反組譯確認**）：

```c
/* 回程改成：ROP 鏈 / 溢位把 RIP 導到 trampoline 中段，後面接它期望的 frame */
unsigned long kpti_tramp = kbase + OFF_SWAPGS_RESTORE + TRAMP_SKIP; /* 依 vmlinux 確認 */

/* trampoline 中段 iretq 期望 stack 上（它會再 pop rax; pop rdi 兩個佔位，再進 iretq frame）：
   [ 0 ]          ← 佔位（視進入點而定）
   [ 0 ]          ← 佔位
   [ user_rip ]   ← 回 user 落點 win()
   [ user_cs  ]
   [ user_rflags ]
   [ user_rsp ]
   [ user_ss  ]   */
```

落點、以及中段進入點前面要墊幾個佔位值，取決於你跳進去的**精確 offset**——這是整個 KPTI 回程最容易卡的地方。做法：對 vmlinux `objdump -d` 找到 `swapgs_restore_regs_and_return_to_usermode`，數清楚從哪一條指令進入能跳過 pop、落在「`mov rdi, rsp` 切 CR3」之前，用那條指令的位址當進入點。

> 為什麼不自己 `mov cr3`？因為 user CR3 的值要從 kernel CR3 推（差一個固定 bit/PCID 關係），算錯直接崩；而且還要自己處理 swapgs 的配合。借官方 trampoline 等於讓 kernel 用它驗證過的方式替你做完，穩得多。

## 正解二：signal trampoline 回程

另一條路避開「手動排 trampoline frame」：**預先註冊 signal handler，提權後故意觸發 fault，讓 kernel 的 signal 遞送機制把你送回 user**。

為什麼這招能跳過 CR3 煩惱？因為 signal 的遞送是 kernel 正常流程的一部分——kernel 要把你送進 user 的 signal handler 時，本來就會走完整的「回 user」路（含 CR3 切換）。你只要在劫持的上下文裡提完權、然後觸發一個會送 signal 的 fault（例如解一個壞指標產生 SIGSEGV），醒來時人已經乾淨地在 user 的 handler 裡、身分是 root。

```c
void handler(int sig) {           /* 已回到 user-space、已 root */
    if (getuid() == 0) system("/bin/sh");
    _exit(0);
}
/* exploit 一開始： */
signal(SIGSEGV, handler);
/* 劫持後：提權 → 觸發一個 fault（例如 *(char*)0 = 1 的等效 kernel 操作）
   → kernel 送 SIGSEGV → 落進 handler，已 root */
```

適用情境：你劫持的上下文很難乾淨排 iretq frame（例如在中斷/軟中斷 context、或 stack 狀態詭異）。缺點：你得在劫持點能「觸發一個會回到你 handler 的 fault」，不是每題都方便。

## 兩條回程怎麼選

| 回程方式 | 優點 | 缺點 | 適用 |
|---|---|---|---|
| `swapgs_restore_regs_and_return_to_usermode` | 最通用、最穩，kernel 自己驗證過 | 要精確算進入 offset 與佔位 | 大多數 stack/ROP 題 |
| signal handler 回程 | 不用手排 iretq frame | 需能在劫持點觸發回自己的 fault | 上下文詭異、難排 frame 時 |
| 手動 `mov cr3; swapgs; iretq` | 不依賴符號 | 最易寫錯（user CR3 推算、PCID） | 幾乎不用，除非 trampoline 不可用 |

實務預設走 trampoline；signal 回程當備案。手動切 CR3 只在特殊約束下才考慮。

## 踩雷集錦

1. **KPTI 開著還用 `swapgs; iretq`**——回到 user 但 CR3 是 kernel 那套，第一條 user 指令就 fault。症狀常被誤判成「提權失敗」，其實提權成功、死在回程。先用 `pti=off` 跑通 Ch 12 的回程，再開 KPTI 換 trampoline，變因才分得清。
2. **trampoline 進入 offset 照抄**——`+22`、`+0x16` 這類數字綁死特定 build。版本/config 一變，前面的 pop 數量就變，進入點跟著變。一定對**你的 vmlinux** `objdump` 數指令確認，別信別人的 magic offset。
3. **把進入點前的佔位值漏掉或多給**——跳進 trampoline 中段後，它可能還會 `pop` 一兩個暫存器才進 iretq frame；你 stack 上要墊對應數量的佔位值，iretq frame 的五個值才會對齊。對齊錯 → iretq 用到錯的 cs/rip → 崩。數 offset 時連帶數清楚還會 pop 幾個。
4. **以為關掉 KPTI 是合理的「簡化」就交卷**——練習 B 和真實題 KPTI 預設開。`pti=off` 只該用在「分階段除錯」時暫時排除變因，最終 exploit 必須在 KPTI 開著下能動。
5. **signal 回程忘了先註冊 handler / handler 裡用了會再 fault 的東西**——handler 要在觸發前就 `signal()` 註冊好；handler 內要簡單（直接 `system`/`execve`），在裡面做複雜操作可能又 fault，繞回來更亂。

## 進階：KPTI 也順手擋了一類 leak

KPTI 的副作用之一：因為 user CR3 下 kernel 映射幾乎都不見了，一些**依賴「固定位址的 kernel 結構」**的 leak/攻擊也被連帶影響——user 跑時能看到的 kernel 相關位址變少。但進出 kernel 必需的那一小塊（cpu_entry_area、entry trampoline）仍映在 user CR3 裡且位置相對固定，歷史上出現過利用這塊固定映射來 de-randomize 或當跳板的研究。這類細節屬於進階，知道「KPTI 不是只影響回程、它改變了 user 視角能看到的 kernel 版圖」即可。

另外值得記住的大局：到本章為止，我們打的都是**控制流劫持**路線（劫 RIP → ROP/ret2usr → 提權 → 回程）。KPTI、SMEP/SMAP、canary、KASLR 每一道都在這條路上加摩擦。這正是為什麼 Part 3 後半和 Part 4 會轉向 **data-only**——那條路根本不回 user、不劫 RIP，於是 KPTI 回程、CFI 這些針對控制流的牆通通繞過。本章是「控制流路線」的技術頂點，也是它開始顯得笨重的地方。

## 動手練習

在自有 QEMU 靶上，把 Ch 11–14 的零件合起來，但仍分階段：

1. 先 `pti=off`，用 Ch 14 的 KASLR leak + Ch 13 的 ROP，確認「開 KASLR、關 KPTI」能提權回 user（排除 KPTI 變因）。
2. 對你的 vmlinux `objdump -d`，找出 `swapgs_restore_regs_and_return_to_usermode`，數出該從哪個 offset 進入能跳過 pop，記下進入點與需墊的佔位數。
3. 打開 KPTI（移除 `pti=off`），把 ROP 鏈尾的回程換成 trampoline 版，跑通「全開」提權。
4. 加碼：改用 signal handler 回程重打一次，體會兩種回程的差異。

## 本章重點整理

- KPTI 為擋 Meltdown 而生：把 kernel 映射從 user page table 幾乎拿掉，進出 kernel 切 CR3。
- `swapgs; iretq` 不切 CR3，所以 KPTI 開著時回程會死在「user 用 kernel CR3 跑」的 fault。
- 正解一：跳進 `swapgs_restore_regs_and_return_to_usermode` 中段，借它切 CR3 + swapgs + iretq（進入 offset 須對自己 vmlinux 確認）。
- 正解二：signal handler 回程，借 kernel 的 signal 遞送把你送回 user，避開手排 frame。
- 本章是「控制流劫持路線」的頂點；Part 3/4 的 data-only 會整條繞過 KPTI 回程與 CFI。

## 自我檢核

- [ ] 能用 Meltdown 解釋 KPTI 為什麼要把 kernel 映射從 user page table 拿掉
- [ ] 能說出 `swapgs; iretq` 在 KPTI 下為什麼回到 user 就 fault（缺了哪一步）
- [ ] 能解釋為什麼要跳 trampoline「中段」而非從頭，以及進入 offset 為何不能照抄
- [ ] 能描述 signal handler 回程為什麼能避開手動切 CR3，以及它的適用與限制
- [ ] 能說出為什麼 data-only 路線能整條繞過本章的 KPTI 回程問題

## 延伸閱讀

- **[swapgs_restore_regs_and_return_to_usermode（arch/x86/entry/entry_64.S）](https://elixir.bootlin.com/linux/v6.6/source/arch/x86/entry/entry_64.S)**
  - **讀哪裡**：搜該符號，逐條看 pop 暫存器 → 切 CR3（`SWITCH_TO_USER_CR3_STACK`）→ swapgs → iretq 的順序。
  - **和本章的關聯**：你要跳進去的就是這段；數進入 offset、數佔位，全靠讀懂它。
  - **前提知識**：會讀 AT&T 組語、Ch 12 的 iretq frame。
- **[Meltdown 論文](https://meltdownattack.com/meltdown.pdf)** — Lipp 等（2018）
  - **核心貢獻**：首次系統性展示跨權限的亂序執行側信道讀取，直接催生 KPTI。
  - **讀哪裡**：Section 3–5（攻擊）與對策一節；微架構細節可略讀。
  - **和本章的關聯**：理解 KPTI「為什麼要這麼暴力地拆映射」的根因。
- **[KPTI bypass / de-randomization 研究（各 Project Zero / 學術 writeup）](https://googleprojectzero.blogspot.com/)**
  - **這篇說什麼**：利用 KPTI 下仍固定映射的 entry 區域做 de-randomization 或跳板。
  - **讀哪裡**：挑一篇討論 cpu_entry_area / entry trampoline 的文章，對照本章「進階」一節。
  - **前提知識**：Ch 6 記憶體佈局、本章 KPTI 機制。

你現在手上有 Ch 11–15 的全部零件：溢位控 RIP、KASLR leak、kernel ROP、SMEP/SMAP 繞法、KPTI 回程、canary restore。下一章先把「提權的最後一步」擴充——除了 `commit_creds`，還有一整套不靠乾淨 RIP 的經典提權原語；然後練習 B 會逼你把這一切在全 mitigation 下串成一條完整的鏈。

→ [Ch 16 — 經典利用原語：modprobe_path / core_pattern / poweroff_cmd / cred，任意寫轉提權](./16-classic-primitives.md)
