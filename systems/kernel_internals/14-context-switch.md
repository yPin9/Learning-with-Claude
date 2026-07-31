# Ch 14 — Context switch 與 preemption（x86 vs ARM64）

> **目標**：搞懂 `__schedule()` 選好下一個 task 之後，kernel 到底怎麼「從 task A 真的變成 task B」——切位址空間（換 page table 根）加切暫存器與 stack（`switch_to`）兩件事各自在做什麼、x86_64 和 ARM64 為何做法不同、為什麼一次 `switch_to` 呼叫裡會牽涉三個 task，以及 preemption 模型決定「什麼時候你會被踢下 CPU」。

## 為什麼需要這個？

Ch 11–13 我們花了三章讀 `__schedule()` 怎麼從 runqueue 挑出下一個要跑的 task（`pick_next_task`）。但挑到之後呢？CPU 現在正跑著 task A 的指令、A 的區域變數在 A 的 kernel stack 上、頁表根指向 A 的位址空間。你不能就這樣「宣布現在換 B 了」——CPU 只有一組實體暫存器、一個 stack pointer、一個頁表根暫存器，這些**現在全被 A 佔著**。要換成 B，得把 A 的這些狀態存起來、把 B 之前存的狀態載回來。

這件事聽起來像「儲存/還原一組暫存器」那麼單純，但魔鬼在細節：

- 你正在用 A 的 stack 執行「切換」這段程式碼。切換途中把 stack pointer 改成 B 的，接下來的 `return` 會返回到誰？
- 頁表根一換，接下來每一次記憶體存取都走 B 的位址空間——連你正在執行的這段 kernel 程式碼，它的位址還對嗎？
- kernel thread（像 `kswapd`、workqueue worker）根本沒有自己的使用者位址空間，切到它時要不要換頁表？換成什麼？
- 換頁表意味著 TLB（Translation Lookaside Buffer）裡快取的舊翻譯全都作廢——如果每次切換都全清 TLB，切換後的頭幾百次記憶體存取全 miss，成本高得嚇人。怎麼避免？

這些問題的答案就是這一章。而且這是**整門課少數 x86_64 和 ARM64 真的長得不一樣**的地方——x86 有歷史包袱（TSS、硬體 task switch 的殘骸），ARM64 是乾淨的純軟體切換。兩者並重才看得出「context switch 的本質」和「某個架構的偶然決定」差在哪。

## 先建立直覺

一次 context switch 由 `__schedule()` 在選好 `next` 之後呼叫 `context_switch()`（`kernel/sched/core.c`）完成，它只做兩件事：

```
                    context_switch(rq, prev, next)
                              │
          ┌───────────────────┴────────────────────┐
          ▼                                         ▼
   ① switch_mm_irqs_off(A_mm, B_mm)          ② switch_to(A, B, prev)
      「換位址空間」                              「換 CPU 狀態」
      載入 B 的頁表根：                          存 A 的 callee-saved 暫存器
        x86  → 寫 CR3                             + stack pointer 到 A.thread
        ARM64→ 寫 TTBR0_EL1                     載入 B 的那組回來
      可能 flush TLB（PCID/ASID 可避免全清）      → 換 stack、換執行流
```

一句話心智模型：**switch_mm 換「B 能看到的記憶體」，switch_to 換「CPU 正在執行誰」**。前者是 MMU 層的事（Ch 16 頁表、Ch 23 TLB 會深入），後者是暫存器與 stack 層的事，且高度架構相關（arch 特定組合語言）。

順序很重要：先換位址空間、再換執行流。因為 `switch_to` 一旦切了 stack，「當下這個執行流」就變成 B 了，B 需要在自己的位址空間裡醒來。

> 有一個例外會顛倒直覺：切到 **kernel thread** 時，因為它沒有自己的使用者 `mm`，kernel **不換頁表**——它借用前一個 task 的位址空間（lazy TLB，下面詳談）。所以「每次切換都換 CR3」是錯的，這是常見誤解。

## 正文一：context_switch —— 兩步的膠水

進到 `kernel/sched/core.c` 的 `context_switch(struct rq *rq, struct task_struct *prev, struct task_struct *next, ...)`。剝掉統計與 lockdep 的雜訊，骨架是：

```c
// kernel/sched/core.c，context_switch()（示意，已簡化）
prepare_task_switch(rq, prev, next);

if (!next->mm) {                       // next 是 kernel thread（沒有使用者 mm）
    next->active_mm = prev->active_mm; // 借用 prev 正在用的位址空間
    if (prev->mm)                      // prev 是使用者 task
        mmgrab_lazy_tlb(prev->active_mm);
    else
        prev->active_mm = NULL;
    enter_lazy_tlb(prev->active_mm, next);   // 進入 lazy TLB 模式
} else {                               // next 是使用者 task，真的要換位址空間
    membarrier_switch_mm(rq, prev->active_mm, next->mm);
    switch_mm_irqs_off(prev->active_mm, next->mm, next);   // ① 換頁表
    ...
}

// ... 處理 prev 若是 kernel thread、要把借來的 active_mm 還回去

switch_to(prev, next, prev);           // ② 換暫存器與 stack（arch 特定）
barrier();
return finish_task_switch(prev);       // 切回來之後的收尾（見下面「三個 task」）
```

兩個關鍵欄位（都在 `struct task_struct`，Ch 9 解剖過）：

- `mm`：這個 task **自己的**使用者位址空間。使用者行程有；kernel thread 是 `NULL`。
- `active_mm`：這個 task **正在用的**位址空間。使用者行程的 `active_mm == mm`；kernel thread 的 `active_mm` 是它借來的那個。

分這兩個欄位的唯一理由就是 kernel thread：讓它「有一個能用的頁表」但「不必擁有一個」。

把「prev 與 next 各是使用者 task 還是 kernel thread」排列組合，`context_switch` 的行為分成四格：

```
                          next 是使用者 task          next 是 kernel thread
                       ┌──────────────────────────┬──────────────────────────┐
 prev 是使用者 task     │ switch_mm(換頁表)          │ 不換頁表，next 借 prev->mm │
                       │ TLB 依 PCID/ASID 決定清不清 │ enter_lazy_tlb           │
                       ├──────────────────────────┼──────────────────────────┤
 prev 是 kernel thread │ switch_mm(換到 next->mm)   │ 不換頁表，next 繼續借      │
                       │ 把 prev 借的 active_mm 放掉 │ 兩個 kernel thread 共用殼  │
                       └──────────────────────────┴──────────────────────────┘
```

看懂這張表就抓到重點：**只有右上兩格（next 是 kernel thread）走 lazy TLB 不換頁表**；左邊兩格（next 是使用者 task）才呼叫 `switch_mm`。實務上使用者 task ↔ kernel thread 交錯得很頻繁（每次 softirq worker、workqueue 插進來都是），lazy TLB 省下的 `switch_mm` 次數相當可觀。

## 正文二：switch_mm —— 換位址空間與 lazy TLB

### 換頁表根

`switch_mm_irqs_off()` 是 arch 特定的（x86 在 `arch/x86/mm/tlb.c`，ARM64 在 `arch/arm64/include/asm/mmu_context.h` / `arch/arm64/mm/context.c`）。核心動作是**把新 mm 的頁表根載入 CPU 的頁表根暫存器**：

- **x86_64**：寫 `CR3`。CR3 存的是 `pgd`（top-level page table，即 PML4/PML5）的實體位址。寫 CR3 這個動作本身在硬體上就會 flush 掉非 global 的 TLB entry（除非用 PCID，見下）。
- **ARM64**：使用者空間頁表根寫進 `TTBR0_EL1`（Translation Table Base Register 0）。ARM64 把位址空間切兩半：`TTBR0_EL1` 管低位址（使用者空間），`TTBR1_EL1` 管高位址（kernel 空間）。**kernel 的頁表根 `TTBR1_EL1` 從開機後幾乎不動**——這是 ARM64 相對 x86 的一個結構優勢：換 process 只動 `TTBR0_EL1`，kernel 映射天生就在另一個暫存器裡不受影響。x86 則是使用者與 kernel 共用同一份頁表階層（PTI 之後有例外），整個 CR3 一起換。

> 頁表本身、`pgd`/`p4d`/`pud`/`pmd`/`pte` 這些階層、頁表 walk 怎麼把虛擬位址翻成實體，是 Ch 16 的主題。這裡只需要知道「切換時載入一個指向 B 頁表樹根的暫存器」。

### 為什麼要 flush TLB，以及怎麼避免

TLB 快取「虛擬位址 → 實體位址」的翻譯結果。A 和 B 的同一個虛擬位址（例如 `0x400000`）通常對到不同實體頁。換到 B 後，若 TLB 裡還留著 A 的 `0x400000→...` 翻譯，B 存取 `0x400000` 就會讀到 A 的記憶體——安全與正確性大災難。所以最保守的做法是**每次切換全清 TLB**。

但全清代價高：切換後 B 的每一次記憶體存取都得重新走頁表（page table walk，好幾次記憶體存取），直到 TLB 重新填滿。於是硬體給了「位址空間標籤」：

- **x86 的 PCID（Process Context IDentifier）**：CR3 裡除了頁表根位址，還有 12 bits 的 PCID。TLB entry 帶上 PCID 標籤後，A 和 B 的翻譯可以**同時**留在 TLB 裡而不打架——切到 B 只是換 PCID，A 的 entry 留著不清，之後切回 A 還能命中。寫 CR3 時設 bit 63 可要求「不要 flush 這個 PCID 的 entry」。Linux 用一個小的 PCID 池（每 CPU 少數幾個）循環分配給近期跑過的 mm。
- **ARM64 的 ASID（Address Space IDentifier）**：概念完全一樣，標籤叫 ASID，放在 `TTBR0_EL1` 的高位（或 `TTBR1`，看 `TCR_EL1.A1`）。ARM64 的 ASID 可以是 8 或 16 bits。`arch/arm64/mm/context.c` 的 `check_and_switch_context()` 管 ASID 的分配與回收（rollover）：ASID 空間用完時做一次全域 flush 再重新發號。

兩者本質相同：**給每個位址空間一個編號，讓 TLB 能區分不同 process 的翻譯，避免切換時全清**。差別只在名字（PCID vs ASID）、位元數、放在哪個暫存器。

還有一個切換以外、但和 `switch_mm` 同源的問題：**TLB shootdown**。context switch 換 CR3/TTBR 只影響**這一顆 CPU** 的 TLB。但如果是「改了某個 mm 的頁表映射」（例如 `munmap`、CoW 斷開、頁面被 reclaim），而這個 mm 正被**多顆 CPU** 同時使用（多執行緒行程），那每一顆跑著這個 mm 的 CPU 的 TLB 都得失效——這需要發 IPI（Inter-Processor Interrupt）叫其他 CPU 各自 flush，叫 TLB shootdown。x86 由 `arch/x86/mm/tlb.c` 的 `flush_tlb_mm_range` / `native_flush_tlb_others` 處理；ARM64 有 `TLBI` 指令搭配 broadcast（`tlbi vae1is` 的 `is` = inner shareable，硬體自動廣播，通常**不必**發 IPI，這是 ARM64 相對 x86 的另一個結構優勢）。`context_switch` 裡記錄「這顆 CPU 現在跑哪個 mm」（`mm_cpumask`）就是為了讓 shootdown 只打真正需要的 CPU，不是全機廣播。完整機制在 Ch 23。

### lazy TLB：切到 kernel thread 時的偷懶

回到那個例外。切到 `next`（kernel thread）時，`next->mm == NULL`，`context_switch()` 走 `if (!next->mm)` 分支：**不呼叫 `switch_mm`**，直接讓 `next->active_mm` 指向 `prev` 的 `active_mm`，並 `enter_lazy_tlb()`。

為什麼能這樣？因為 kernel thread 只跑在 kernel 空間，而 kernel 的映射在**所有**位址空間裡都一樣（x86 是每份 pgd 的高半部相同、ARM64 是共用的 `TTBR1_EL1`）。kernel thread 用哪個使用者位址空間的「殼」都行——它根本不碰使用者位址那半。既然如此，何必花成本換頁表 / 動 TLB？**借前一個 task 的來用，省一次 `switch_mm`**。這在「使用者 task ↔ kernel thread ↔ 使用者 task」頻繁交錯時省下可觀的 TLB 成本（想像 workqueue、softirq 執行緒插進來又出去）。

代價是：那個被借用的 mm 不能在 kernel thread 還借著時被釋放。所以有 `mmgrab_lazy_tlb()` / `mmdrop_lazy_tlb()` 這對特殊引用計數（`active_mm` 的引用），確保「還有人 lazy 借著」的 mm 不會被回收。`active_mm` 這個欄位存在的唯一理由就是撐起 lazy TLB（Ch 9 提過這個欄位，這裡是它的用武之地）。

## 正文三：switch_to —— 換 CPU 狀態（x86 vs ARM64）

`switch_mm` 換完位址空間，`switch_to(prev, next, prev)` 才是真正「換執行流」的一步。它是個 arch 特定的 macro，展開成組合語言。**要存/還原的是 callee-saved 暫存器加 stack pointer 加（部分）特殊暫存器**——caller-saved 暫存器不用存，因為 `switch_to` 是被正常函式呼叫進來的，caller-saved 早就依 ABI 慣例被呼叫端存過了。

### x86_64

x86_64 分兩段（`include/asm/switch_to.h` → `arch/x86/entry/entry_64.S` 的 `__switch_to_asm` → `arch/x86/kernel/process_64.c` 的 `__switch_to`）：

1. **`__switch_to_asm`（組合語言，`entry_64.S`）**：存 A 的 callee-saved 暫存器（`rbx rbp r12 r13 r14 r15`）到 A 的 stack，把 A 的 `RSP` 存進 `A->thread.sp`，載入 `B->thread.sp` 到 `RSP`（**這一刻 stack 就換成 B 的了**），再從 B 的 stack pop 回 B 的 callee-saved 暫存器，最後 `jmp __switch_to`。
2. **`__switch_to`（C，`process_64.c`）**：處理剩下的 per-task 狀態——FS/GS base（`fsgsbase`，thread-local storage 用）、TLS、I/O bitmap、更新 TSS 的 `sp0`（下次從使用者態進 kernel 時要用的 kernel stack 頂）、`fpu__switch()`（延遲的 FPU/SIMD 狀態切換）。

把 `__switch_to_asm`（`entry_64.S`）的骨架攤開看，才會真正抓到「stack 一換人格就換」的機制：

```asm
; arch/x86/entry/entry_64.S 的 __switch_to_asm（示意，已簡化）
; 進來時 rdi = prev(A)、rsi = next(B)，都是 task_struct*
SYM_FUNC_START(__switch_to_asm)
    pushq   %rbp            ; 把 A 的 callee-saved 暫存器
    pushq   %rbx            ; 依序壓到 A 現在的 kernel stack 上
    pushq   %r12
    pushq   %r13
    pushq   %r14
    pushq   %r15

    movq    %rsp, TASK_threadsp(%rdi)   ; 存 A 的 RSP → A->thread.sp
    movq    TASK_threadsp(%rsi), %rsp   ; 載入 B->thread.sp → RSP  ★ 這一行 stack 換成 B 的

    ; ── 分界線：這行以後，我們踩在 B 的 stack 上 ──
    popq    %r15            ; 從 B 的 stack pop 回「B 當年被切走時壓進去的」
    popq    %r14            ; callee-saved 暫存器
    popq    %r13
    popq    %r12
    popq    %rbx
    popq    %rbp

    jmp     __switch_to     ; 跳去 C 段做剩下的（FS/GS、TSS.sp0、FPU…）
SYM_FUNC_END(__switch_to_asm)
```

`★` 那一行是整個 kernel 最關鍵的一條指令之一。它之前壓 stack 的是 A、之後 pop 的卻是 B——因為 B 上次被切走時，它的 `__switch_to_asm` 也壓了 6 個暫存器進 B 的 stack，現在 `RSP` 指向 B 的 stack，`pop` 自然拿回 B 的暫存器。**push 的是 A、pop 的是 B，中間只差換了 `RSP`**。這就是「換 stack = 換執行流」最赤裸的一行。ARM64 的 `cpu_switch_to` 邏輯完全對應，只是它把暫存器存進 `cpu_context` struct（`stp x19, x20, [x8], ...`）而非壓 stack，再 `ret` 到 `x30`（LR）——換的是 `x30` 帶出的返回位址。

**x86 的歷史包袱**：386 時代 x86 有「硬體 task switch」——CPU 能靠一個 TSS（Task State Segment）自動存還原整組暫存器。Linux **從不用**這個硬體機制（太慢又不靈活），改成純軟體切換。但 TSS 沒完全消失：現在每個 CPU 一個 TSS，只用它的 `sp0`（syscall/中斷從 ring3 進 ring0 時 CPU 硬體會去 TSS 讀 kernel stack pointer）和 I/O permission bitmap。所以 x86 是「軟體切換為主、殘留一點硬體 TSS 用途」的混血。

### ARM64

ARM64 乾淨得多（`arch/arm64/kernel/process.c` 的 `__switch_to` → `arch/arm64/kernel/entry.S` 的 `cpu_switch_to`）：

- **`cpu_switch_to`（組合語言，`entry.S`）**：把 A 的 callee-saved 暫存器 `x19–x28`、frame pointer `x29`、stack pointer `sp`、以及 `thread_struct.keys`/`sp_el0`(`current` 指標)等存進 `A->thread.cpu_context`，再從 `B->thread.cpu_context` 載回。`cpu_context` 是個明明白白列出要存哪些暫存器的 struct（`arch/arm64/include/asm/processor.h` 的 `struct cpu_context`），純軟體、沒有任何硬體 task switch 概念。
- **`__switch_to`（C，`process.c`）**：切換 per-task 的 FPSIMD/SVE 狀態、TLS（`tpidr_el0`）、debug/hardware breakpoint、pointer authentication keys、`sp_el0`（ARM64 把 `current` 指標放在 `sp_el0`）等，最後呼叫 `cpu_switch_to`。

### x86 vs ARM64 對比

| 面向 | x86_64 | ARM64 |
|---|---|---|
| 頁表根暫存器 | `CR3`（單一，使用者+kernel 共用階層*） | `TTBR0_EL1`（使用者）+ `TTBR1_EL1`（kernel，幾乎不動） |
| 換 process 時 | 整個 CR3 換掉 | 只換 `TTBR0_EL1`，kernel 映射不受影響 |
| TLB 標籤 | PCID（CR3 低 12 bit） | ASID（`TTBR` 高位，8/16 bit） |
| 暫存器切換 | `__switch_to_asm`（asm）+ `__switch_to`（C） | `cpu_switch_to`（asm）+ `__switch_to`（C） |
| callee-saved | `rbx rbp r12–r15` + `RSP` | `x19–x28 x29(fp)` + `sp` |
| 硬體 task switch | TSS 存在（歷史包袱），Linux 不用其自動切換，只借 `sp0` / I/O bitmap | 無此概念，純軟體 |
| `current` 指標放哪 | per-CPU 變數（`pcpu_hot.current_task`，GS-relative） | `sp_el0` 暫存器 |
| kernel stack top 給硬體 | TSS 的 `sp0`（進 ring0 時硬體讀） | `SP_EL1` / 進 exception 時由 vector 設定 |

\* PTI（Page Table Isolation，Meltdown 緩解）之後，x86 使用者態與 kernel 態各有一份 CR3，syscall 進出時再切一次——這是安全機制加的複雜度，不是 context switch 的本質。

## 底層機制：一個 switch_to，三個 task

這是整章最反直覺、面試最愛問的一點。看 `context_switch()` 尾巴：

```c
switch_to(prev, next, prev);
barrier();
return finish_task_switch(prev);
```

魔術發生在 `switch_to` 換 stack pointer 的那一瞬間。想像三個 task 在同一顆 CPU 上輪流：

```
時間軸（同一顆 CPU）：

  ... task A 正在跑 __schedule → context_switch → switch_to(A, B, A)
                                                        │
      switch_to 內部：存 A 的 RSP，載入 B 的 RSP  ──────┤  ← stack 換成 B 的了
                                                        │
      「函式呼叫時是 A，但 return 出來時，執行的是 B」   ▼
  ... task B 從它上次被切走的地方醒來（也在 switch_to 內），
      繼續往下跑，return 到 B 的 finish_task_switch(prev)
                                                        │
      B 過了一陣子，又呼叫 __schedule → switch_to(B, C, B)
                                                        │
  ... 更久以後，某次別的 CPU/這顆 CPU 又切回 A：
      A 從它「當年被切走的那個 switch_to 內部」醒來，
      return 到 A 的 finish_task_switch(prev=???)
```

關鍵理解：**每個 task 都是在自己上次執行 `switch_to` 的那一點「凍結」的**。當它下次被排到，執行流就從那一點解凍繼續。所以：

- A 呼叫 `switch_to(A, B, A)`，這個函式**在 A 的執行流裡進去，但不會在 A 的執行流裡出來**——它出來時 stack 已是 B 的，出來的是 B 的 `finish_task_switch`。
- 反過來，B 這次「從 `switch_to` 出來」，其實是 B **很久以前**呼叫 `switch_to(B, X, B)` 時凍結在那，現在解凍。B 出來後拿到的 `prev` 是「把 CPU 讓給 B 的那個 task」——可能既不是 A 也不是 B，是**第三個 task**。

這就是 `switch_to(prev, next, prev)` 第三個參數的意義：它是個 output——「切回來後，是誰剛剛把 CPU 交給我」。arch 組合語言把它從暫存器帶出來，`finish_task_switch(prev)` 才知道要去收尾哪個 task（例如那個 task 若剛 `exit`，這裡要釋放它的資源；若它從別的 CPU 遷移過來，這裡處理 runqueue 鎖的移交）。

一句話記住：**`switch_to` 是唯一一個「你進去時是一個 task，出來時是另一個 task」的函式。** stack 換了，執行流就換了人格。

### 一個被忽略的細節：rq lock 是怎麼跨過切換的

`__schedule()` 進來時是**持著 runqueue 的鎖（`rq->lock`）**的（Ch 11）。這把鎖保護「這顆 CPU 上誰在跑、runqueue 裡有誰」。問題來了：`switch_to` 之後執行流變成 B，而 B 上次是在**它自己的** `__schedule` 裡被切走的——鎖是誰放的？

答案藏在 `finish_task_switch()` 與 `context_switch()` 一開始的 `prepare_task_switch()` / `rq_lock` 交接裡：切換是「**帶著鎖進去，在新 task 的 `finish_task_switch` 裡放掉**」。也就是 A 在 `context_switch` 前半持鎖 → 切到 B → B 從它當年的 `switch_to` 醒來 → B 執行 `finish_task_switch(prev)`，在這裡才 `raw_spin_rq_unlock_irq(rq)` 把鎖放掉。鎖的「上鎖」和「解鎖」發生在**不同 task 的執行流裡**——這也是 `switch_to` 三 task 魔術的延伸後果。新誕生的 task（`fork` 出來第一次被排到，Ch 10）也一樣：它的第一口氣是 `ret_from_fork`，而 `ret_from_fork` 開頭就會呼叫 `finish_task_switch` 去放掉那把「別人幫它上的鎖」。這解釋了為什麼看 `copy_process` 時，新 task 的 stack 被預先「假造」成好像它剛從 `switch_to` 返回一樣——不假造，第一次切到它就會炸。

## 正文四：preemption 模型 —— 什麼時候你會被踢下 CPU

前面講「怎麼切」，這節講「什麼時候能切」。核心資料結構兩個：

- **`preempt_count`**（per-CPU，`include/linux/preempt.h`）：一個 32-bit 計數器，非 0 代表「現在不准搶佔我」。它不是單純一個數，而是把好幾個獨立的巢狀計數塞進不同 bit 欄位：

  ```
   preempt_count（32 bit）欄位佈局（示意，實際 shift 見 preempt.h 的 *_SHIFT）
   ┌──────┬──────────┬──────────┬─────────────────────┐
   │ NMI  │ HARDIRQ  │ SOFTIRQ  │  PREEMPT（搶佔停用次數）│
   │(1bit)│ (4 bit)  │ (8 bit)  │      (8 bit)         │
   └──────┴──────────┴──────────┴─────────────────────┘
   還有一個 PREEMPT_NEED_RESCHED 的 fold bit（最高位，反相儲存以便快速比較）
  ```

  拆成欄位的好處：一次讀 `preempt_count` 就能同時判斷「在不在中斷裡（`in_interrupt()` 看 HARDIRQ|SOFTIRQ|NMI 欄位）」「搶佔關了幾層」。持 spinlock（Ch 25）會 `+1` PREEMPT 欄位、進 hardirq handler 會 `+1` HARDIRQ 欄位、明確 `preempt_disable()` 也動 PREEMPT 欄位——**只要整個 `preempt_count` 非 0（扣掉那個 fold bit），就不准搶佔**。這也解釋了為什麼「spinlock 臨界區不能睡也不會被搶」——拿鎖那一下 PREEMPT 欄位就 +1 了。
- **`TIF_NEED_RESCHED`**（thread flag，在 `task_struct` 的 `thread_info->flags`）：排程器想切換時（例如更高優先權的 task 醒來、時間片用完）設這個旗標，意思是「有機會就重新排程」。它是個**請求**，不是立即動作。

真正的搶佔在**檢查點**發生——kernel 在幾個固定位置檢查「`TIF_NEED_RESCHED` 有沒有設 && `preempt_count == 0`」，成立就呼叫 `__schedule()`：

1. **中斷/exception 返回時**（返回使用者態一定檢查；返回 kernel 態要看有沒有開 preempt）
2. **`preempt_enable()`**：把 `preempt_count` 減回 0 時，順手檢查旗標，成立就馬上排程
3. **syscall 返回使用者態的路上**
4. **主動呼叫 `schedule()`**（如等 I/O 時 `schedule()` 讓出 CPU——這叫 voluntary）

四種 config 決定「kernel 態程式碼能不能被搶」：

| 模型（config） | kernel 態能被搶嗎 | 延遲 | 吞吐 | 典型用途 |
|---|---|---|---|---|
| `PREEMPT_NONE` | 不能，只在返回使用者態/主動 `schedule()` 時切 | 最差（一段 kernel 迴圈可能霸佔 CPU 很久） | 最好 | 伺服器、批次運算 |
| `PREEMPT_VOLUNTARY` | 不能，但在很多地方加了 `cond_resched()` 顯式讓出點 | 中等 | 好 | 桌面預設 |
| `PREEMPT`（FULL） | 能，只要 `preempt_count == 0` 就可在幾乎任何 kernel 點被搶 | 好 | 略降 | 低延遲桌面/多媒體 |
| `PREEMPT_RT` | 能，且把大部分 spinlock 換成可睡眠鎖、中斷 threaded 化，追求硬即時 | 最好（有界） | 最低 | 即時系統、工控、音訊 |

> **6.x 的新方向**：**6.13** 併入了 **lazy preemption**（`PREEMPT_LAZY`，Thomas Gleixner 等人推動；v6.12 尚無），目標是「用一份 kernel binary 在執行期選擇搶佔行為」，逐步取代編譯期四選一。這還在演進，本課以傳統四模型為主軸講清楚語意；細節可追 LWN 的相關報導。

`PREEMPT_RT` 長年在 mainline 外維護，直到近年才大部分併入主線——這條線和本 repo `bpf` 課的低延遲觀測、`observability_tools` 課裡「為什麼我的 tracing 有時延遲」相關。Ch 31（threaded IRQ 與 -rt）會深入 RT 的中斷處理。

### cond_resched 與「誰真的決定要切」

VOLUNTARY 模型靠 `cond_resched()` 在耗時的 kernel 迴圈裡插「自願讓出點」。它做的事很直白：檢查 `TIF_NEED_RESCHED`，設了就呼叫 `__schedule()`，沒設就當沒事、幾乎零成本繼續跑。kernel 裡凡是「可能跑很久」的迴圈（大範圍記憶體清零、掃描一長串 list）都會週期性 `cond_resched()`，避免在 NONE/VOLUNTARY 下霸佔 CPU 太久造成延遲尖峰。

而 `TIF_NEED_RESCHED` 到底何時被設？主要是 `check_preempt_curr()` / 排程 class 的 `wakeup_preempt`（Ch 11–13）：當一個 task 被喚醒、或 tick 到時發現「當前 task 該讓賢了」（EEVDF 下是它的 eligible/deadline 判斷，Ch 13），就設旗標。**設旗標的是排程器的決策邏輯（Ch 11–13），檢查旗標並真的切換的是本章的檢查點機制**——兩者一個管「該不該換」、一個管「什麼時候能換」，合起來才是完整的搶佔。這也是為什麼 lazy preemption（6.13 併入，v6.12 尚無）能存在：它加了一個「弱版」的 need-resched（`TIF_NEED_RESCHED_LAZY`），讓排程器可以表達「想切但不急」，把「該不該換」和「多急著換」解耦。

## 動手：用 gdb 看 CR3 與暫存器真的換了

沿用 Ch 0 的 QEMU + gdb 環境（x86_64，記得 `nokaslr`）。目標：停在 `__switch_to`，看切換前後 CR3 與 stack pointer 變化。

```bash
# 終端一：QEMU 凍住等 gdb
qemu-system-x86_64 -kernel arch/x86/boot/bzImage \
    -initrd initramfs.cpio.gz -append "console=ttyS0 nokaslr" \
    -nographic -m 512M -S -s
```

```gdb
# 終端二
gdb vmlinux
(gdb) target remote :1234
(gdb) source vmlinux-gdb.py
(gdb) break __switch_to             # x86_64 的 C 段切換（process_64.c）
(gdb) continue
```

QEMU 開機到 shell 後隨便跑點東西（`ls`、`while true; do :; done &`）製造切換，gdb 會停在 `__switch_to`。這時：

```gdb
(gdb) print prev_p->comm            # 切出去的 task 名字
(gdb) print next_p->comm            # 切進來的 task 名字
(gdb) print/x $cr3                  # 目前 CR3（還是 prev 的位址空間，因為 switch_mm 已在前面跑過）
(gdb) print/x next_p->thread.sp     # next 的 kernel stack pointer（即將被載入的）
```

要看 CR3 真的變，在 `switch_mm_irqs_off` 附近下斷點更直接：

```gdb
(gdb) break switch_mm_irqs_off
(gdb) continue
(gdb) print/x $cr3                  # switch_mm 之前
(gdb) finish                        # 執行完 switch_mm
(gdb) print/x $cr3                  # switch_mm 之後——低位（頁表根+PCID）應該變了
```

比對兩次 `$cr3`：高位的頁表根實體位址不同（換了位址空間），低 12 bit 的 PCID 也可能不同。若你切到的是 kernel thread，會發現 `switch_mm` **根本沒被呼叫**（走了 lazy TLB 分支）——這就是前面講的例外，親眼看到。

> ARM64 版本：用 `qemu-system-aarch64 -M virt -cpu cortex-a72 ...`（Ch 0 提過 ARM64 差異章會另給指令）跑一顆 arm64 kernel，斷點改成 `cpu_switch_to` / `__switch_to`，看的暫存器換成 `$TTBR0_EL1`。QEMU 的 `info registers` 也能印系統暫存器。

### 用 perf / vmstat 量 context switch 頻率

不進 gdb，也能從使用者空間感受切換成本（呼應 `observability_tools` 課）：

```bash
# 系統層級：每秒 context switch 數（cs 欄）
vmstat 1

# 針對某條命令量它引發多少次切換
perf stat -e context-switches,cpu-migrations ./your_program

# 區分 voluntary（主動讓出，多半等 I/O）與 involuntary（被搶佔，多半 CPU 競爭）
cat /proc/$PID/status | grep ctxt      # voluntary_ctxt_switches / nonvoluntary_ctxt_switches
```

`nonvoluntary_ctxt_switches` 高代表這個 task 常被搶（CPU 不夠或有更高優先權者）；`voluntary` 高代表它常主動睡（等 I/O、等鎖）。這個區分在調效能時是第一手線索。

## 對比與取捨

| 議題 | 選項 A | 選項 B | 取捨 |
|---|---|---|---|
| 硬體 vs 軟體切換 | x86 硬體 TSS task switch | 純軟體切 | Linux 全選軟體：硬體 task switch 慢、僵化、無法只存需要的狀態。TSS 只留 `sp0` 等硬體必需用途 |
| 切換時 TLB | 全 flush | PCID/ASID 標籤 | 標籤省下切回舊 process 的 TLB 重填，但標籤數有限、rollover 要全 flush。現代 CPU 一律用標籤 |
| kernel thread 頁表 | 也給它一份/也換 | lazy TLB 借用 | 借用省一次 switch_mm 與 TLB 擾動；代價是 `active_mm` 引用計數的複雜度 |
| preemption | NONE（高吞吐） | FULL/RT（低延遲） | 吞吐 vs 延遲的經典權衡。搶佔點越多，反應越快但 cache/pipeline 被打斷越頻繁、鎖持有窗口要越短 |

## 踩雷集錦

1. **「每次 context switch 都換 CR3/頁表」——錯**。切到 kernel thread 不換（lazy TLB 借用前一個 mm）。只有切到**有自己 `mm` 的使用者 task**、且 `mm` 和前一個不同時才真的 `switch_mm`。gdb 在 `switch_mm_irqs_off` 下斷點會看到它常常沒被呼叫。

2. **「PCID/ASID 讓 TLB 永遠不用清」——錯**。標籤讓「不同 process 的翻譯共存」，但標籤數量有限（x86 的 PCID 池很小、ARM64 ASID 8/16 bit）。用完要 rollover——ARM64 的 `check_and_switch_context()` 在 ASID 耗盡時做全域 flush 再重發。而且改頁表映射（如 CoW、munmap）仍需針對性 flush，甚至跨 CPU 的 TLB shootdown（Ch 23）。

3. **把 `switch_to` 當普通函式讀**。它不是「進去做事、返回原地」。它換 stack，返回時執行流已是另一個 task。想不通「為什麼 `switch_to(prev, next, prev)` 要傳兩次 prev」的人，都是沒抓到「返回時的 prev 是第三個 task」這件事。

4. **以為 `TIF_NEED_RESCHED` 一設就立刻切換**。它只是**請求**。真正切換要等到檢查點（中斷返回、`preempt_enable`、syscall 返回），且 `preempt_count` 必須是 0。持著 spinlock（`preempt_count != 0`）時就算旗標設了也不會被搶——這正是「spinlock 臨界區不能睡」的機制基礎（Ch 25）。

5. **x86 的 `current` 和 ARM64 的 `current` 實作不同卻混談**。x86 用 GS-relative 的 per-CPU 變數（`pcpu_hot.current_task`）取 `current`；ARM64 把 `current` 放在 `sp_el0` 暫存器。切換時各自要更新對應位置，讀源碼時別把一邊的做法套到另一邊。

## 進階：再往深一層

- **FPU/SIMD 的延遲切換**：存還原全套 FPU/AVX-512/SVE 暫存器很貴。x86 的 `fpu__switch` 與 ARM64 的 FPSIMD 切換都用「**用到才切**」策略——切換時不急著存還原，等 next 真的碰 FPU 指令時才 trap 進來載入。大多 kernel thread 根本不碰 FPU，這省很多。
- **context switch 到底幾個 cycle**：純暫存器切換是幾十到上百 cycle，但**間接成本**（TLB 冷掉、L1/L2 cache 被新 task 的 working set 擠掉、branch predictor 失準）往往是直接成本的數倍甚至數量級。所以「context switch 是效能問題」講的多半是這些間接成本，不是那幾條 `mov`。本 repo `perf_bench` 課會教你用 `perf` 把這些拆開量。這也是「減少 context switch」（batch、affinity、避免鎖競爭）常帶來效能提升的原因。
- **`membarrier` 與切換**：`membarrier()` syscall 要能對特定位址空間的所有執行緒下記憶體屏障，`context_switch` 裡的 `membarrier_switch_mm()` 就是配合它記錄「這顆 CPU 現在跑哪個 mm」，Ch 24（memory ordering）會用到。
- **面試常問**：「一次 context switch 發生什麼？」——標準答案要涵蓋 (1) `switch_mm` 換頁表根（提 PCID/ASID）、(2) `switch_to` 存還原 callee-saved + SP、(3) lazy TLB 例外、(4) 三個 task 的 stack 魔術、(5) 直接 vs 間接成本。能把 x86 CR3 / ARM64 TTBR 的差異說出來是加分。

## 動手練習

1. **看見三個 task**：在 `finish_task_switch` 下斷點，每次停都印 `prev->comm` 和 `current->comm`（`current` 是切進來的 next）。連續幾次，記錄 (prev, current) 對，驗證「這次的 current 下次可能變成別人的 prev」，且某次的 prev 未必是上次的 current——親眼確認「第三個 task」。

2. **抓 lazy TLB**：在 `context_switch` 的 `if (!next->mm)` 分支下條件斷點（或在 `switch_mm_irqs_off` 下斷點後統計命中率），跑一陣子後比較「切換總次數」與「`switch_mm` 實際被呼叫次數」。差額就是切到 kernel thread、走 lazy TLB 省下的換頁表次數。

3. **量搶佔 vs 主動讓出**：寫兩支程式，一支狂算（純 CPU、會被 involuntary 搶）、一支狂讀檔（狂 I/O、會 voluntary 睡）。各跑一分鐘後看 `/proc/$PID/status` 的 `voluntary_ctxt_switches` 與 `nonvoluntary_ctxt_switches`，驗證兩者比例和你的預期一致。

4. **（進階）改 preemption 模型重編**：把 kernel config 從 `PREEMPT_VOLUNTARY` 改成 `PREEMPT`（FULL），重編，用 `perf stat` 對同一 workload 比較 context-switches 數與延遲分佈，體會吞吐/延遲的權衡。

## 本章重點整理

- 一次 context switch = `switch_mm`（換頁表根：x86 寫 CR3 / ARM64 寫 TTBR0_EL1，靠 PCID/ASID 避免全清 TLB）+ `switch_to`（存還原 callee-saved 暫存器與 stack pointer，arch 特定組合語言）。
- 切到 kernel thread 走 **lazy TLB**：不換頁表，借用前一個 task 的 `active_mm`，省一次 `switch_mm`。`active_mm` 這欄位就是為此存在。
- `switch_to` 是「進去是一個 task、出來是另一個 task」的函式——stack 一換，執行流換人格；第三個參數 `prev` 是 output，告訴你切回來後「是誰把 CPU 交給我」。
- preemption 由 `TIF_NEED_RESCHED`（請求）+ `preempt_count`（0 才准搶）在固定檢查點決定；四種 config（NONE/VOLUNTARY/PREEMPT/RT）是吞吐 vs 延遲的權衡，6.13 起有 lazy preemption 走向執行期可選（v6.12 尚無）。
- context switch 的成本大頭是**間接成本**（TLB/cache 冷掉），不是那幾條存暫存器的指令。

## 自我檢核

- [ ] 不看筆記，能畫出 `context_switch` = `switch_mm` + `switch_to` 兩步，並說出各自改了哪個暫存器
- [ ] 能解釋 x86 CR3 與 ARM64 TTBR0/TTBR1 的差別，以及為什麼 ARM64 換 process 時 kernel 映射不受影響
- [ ] 能講清楚 PCID/ASID 為何存在、以及它們**不能**讓 TLB 永不清的原因
- [ ] 能對面試官解釋「一個 `switch_to` 三個 task」的 stack 魔術，說出第三個參數 `prev` 是什麼
- [ ] 能說出切到 kernel thread 為什麼不換頁表（lazy TLB），以及 `active_mm` 的角色
- [ ] 能比較四種 preemption 模型「kernel 態能不能被搶」及對應的延遲/吞吐取捨
- [ ] 能用 gdb 在 `switch_mm_irqs_off` / `__switch_to` 觀察 CR3 變化，用 `perf stat`/`/proc/PID/status` 量切換頻率與 voluntary/involuntary 比例

## 延伸閱讀

### 官方文件

- **[Documentation/scheduler/sched-domains.rst 及 scheduler/ 目錄](https://www.kernel.org/doc/html/latest/scheduler/index.html)**
  - **讀哪裡**：先掃 `sched-design-CFS`（已被 EEVDF 部分取代但架構仍在）與整體索引，理解 `__schedule` → `context_switch` 在排程器裡的位置
  - **和本章的關聯**：本章接在排程器選好 next 之後，這份文件給你上游的全貌

- **[Documentation/mm/ 與 arch TLB 相關文件](https://www.kernel.org/doc/html/latest/mm/index.html)**
  - **讀哪裡**：與 TLB flush、lazy TLB、ASID 相關的段落
  - **能學到什麼**：`switch_mm` 背後 TLB 管理的權威說明，補足本章點到為止、留給 Ch 23 的部分

### 文章

- **[LWN：Lazy preemption 系列報導](https://lwn.net/Kernel/Index/#Scheduler-Preemption)**
  - **讀哪裡**：搜尋 "lazy preemption" / "preemption models" 的文章
  - **為什麼值得讀**：本章的四模型正在演進成執行期可選，LWN 是這條路線最好的一手記錄；想知道「為什麼要統一 preemption 模型」看這裡
  - **前提**：讀完本章的 preemption 節

- **[LWN：How the PCID/ASID and TLB flushing works](https://lwn.net/Kernel/Index/#Memory_management-TLB)**
  - **讀哪裡**：TLB 與 PCID 相關文章
  - **能學到什麼**：PCID 池管理、TLB shootdown 的實作演進，是 Ch 23 的前置暖身

### 書籍與源碼

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 3、4 章（Process Management、Scheduling）
  - **定位**：白話講 context switch 與搶佔的心智模型，讀完再回頭啃源碼會順很多；版本較舊，函式名以 v6.12 源碼為準
- **arch 源碼直讀**（配 [Bootlin v6.12](https://elixir.bootlin.com/linux/v6.12/source)）
  - `kernel/sched/core.c` 的 `context_switch`；`arch/x86/entry/entry_64.S` 的 `__switch_to_asm`、`arch/x86/kernel/process_64.c` 的 `__switch_to`；`arch/arm64/kernel/entry.S` 的 `cpu_switch_to`、`arch/arm64/kernel/process.c` 的 `__switch_to`
  - **怎麼讀**：對照本章的兩步模型與對比表，逐行標出「這行對應存哪個暫存器」，是把本章從「懂概念」變「能改」的關鍵一步

切完 task 之後，下一個問題是「這些 task 在多顆 CPU 上怎麼分配」——一顆 CPU 的 runqueue 空了要去別顆偷 task、NUMA 機器上該把 task 放在離它記憶體近的 CPU。這就是下一章 SMP 排程與負載平衡。

→ [Ch 15 SMP、load balancing、CPU affinity、NUMA](./15-smp-numa-balancing.md)
