# Ch 19 — ptrace 進階：process 注入

> **目標**：把 Ch 3-4 的 ptrace 知識推到「控制和修改」——不只觀察 tracee，還改它的暫存器和記憶體、注入程式碼、修改 syscall 的參數和回傳值。理解 debugger 的「修改」能力（gdb 怎麼改變數、設斷點）、以及這些技術的攻防意義（反調試、惡意注入）。這是「動手三大章」之二（mini-strace、注入、LD_PRELOAD），把「理解工具底層」推到「能控制 process」。

> **環境**：Linux x86-64，C（ptrace）。trace 自己的子 process 不需 sudo。

## 為什麼要學 process 注入？

Ch 3-4 你用 ptrace **觀察** tracee（讀暫存器、看 syscall）。但 ptrace 不只能讀，還能**寫**——改 tracee 的暫存器、改它的記憶體、注入程式碼、修改 syscall 的行為。這是 debugger「修改」能力的底層——gdb 怎麼改變數的值（PTRACE_POKEDATA 寫記憶體）、怎麼設斷點（POKEDATA 寫入中斷指令）、怎麼改執行流（SETREGS 改 PC）。

理解這些讓你完整理解 debugger（不只觀察，還能控制和修改）。它也有攻防意義——惡意軟體用注入隱藏自己、反調試對抗分析、安全工具用注入做監控。這章把 ptrace 的「控制」面講透。理解 process 注入，你對「一個 process 能多大程度控制另一個」有了完整認識——這是 debugger、注入工具、安全分析的核心。

> **倫理提醒**：process 注入是強大的技術，有合法用途（debugger、監控、熱修補）也可能被濫用（惡意注入）。本章以理解技術原理和 debugger 機制為目的，針對你自己的 process。理解攻擊技術也是為了防禦。

## 先建立直覺:從觀察到控制

```
ptrace 的兩面：觀察（Ch 3-4）+ 控制（這章）

  觀察（Ch 3-4）：
    PTRACE_GETREGS  讀暫存器
    PTRACE_PEEKDATA 讀記憶體
    → strace 用這些看 syscall
        │
  控制（這章）：
    PTRACE_SETREGS  改暫存器（改 PC、改參數、改回傳值）
    PTRACE_POKEDATA 改記憶體（改變數、注入程式碼、設斷點）
    → debugger 用這些改變數、設斷點、控制執行
        │
  能做什麼（控制的威力）：
    改 syscall 參數：open("a") 攔截改成 open("b")
    改 syscall 回傳值：讓 read 看起來回傳不同的東西
    改變數的值：gdb 的 set var
    設斷點：在某位址寫入中斷指令（INT3）
    注入並執行程式碼：讓 tracee 執行你的程式碼
        │
  → ptrace 讓 tracer 完全控制 tracee
    這是 debugger 全部能力的來源，也是注入的基礎
```

關鍵心智：ptrace 不只能**觀察**（讀，Ch 3-4），還能**控制**（寫）——`PTRACE_SETREGS`（改暫存器：改 PC/參數/回傳值）、`PTRACE_POKEDATA`（改記憶體：改變數/注入程式碼/設斷點）。這讓 tracer **完全控制** tracee——改 syscall 行為、改變數、設斷點、注入並執行程式碼。這是 debugger 全部能力（改變數、設斷點、控制執行）的來源。

> 這章建立在 Ch 3-4 的 ptrace 觀察上，加上「控制」。如果對 PTRACE_GETREGS/PEEKDATA、暫存器佈局不熟，回看 [Ch 3](./03-ptrace-syscall-deep-dive.md)。

## 修改 syscall 的參數與回傳值

```c
// 攔截並修改 syscall（在 syscall-entry 改參數、syscall-exit 改回傳值）
// 概念範例：把 tracee 開的檔案從 "secret.txt" 改成 "fake.txt"
#include <stdio.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <sys/user.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <string.h>

int main(int argc, char *argv[]) {
    pid_t child = fork();
    if (child == 0) {
        ptrace(PTRACE_TRACEME, 0, NULL, NULL);
        execvp(argv[1], &argv[1]);
    }
    int status;
    waitpid(child, &status, 0);
    int in = 0;
    while (1) {
        ptrace(PTRACE_SYSCALL, child, NULL, NULL);
        waitpid(child, &status, 0);
        if (WIFEXITED(status)) break;
        struct user_regs_struct regs;
        ptrace(PTRACE_GETREGS, child, NULL, &regs);

        if (!in && regs.orig_rax == SYS_openat) {
            // syscall-entry: 改 openat 的回傳值（讓它「失敗」）
            // 例：強制讓某些 open 失敗（attack/defense 示範）
            // 這裡示範「改回傳值」：syscall-exit 時把 rax 改成 -1
            // （改參數要改記憶體裡的檔名，較複雜）
        }
        if (in && regs.orig_rax == SYS_openat) {
            // syscall-exit: 看回傳值，可以改它
            // regs.rax = -1;  // 改成失敗
            // ptrace(PTRACE_SETREGS, child, NULL, &regs);  // 寫回
        }
        in = !in;
    }
    return 0;
}
```

```
修改 syscall 的時機（entry vs exit）：

  syscall-entry（進入前）：改「參數」
    改 rdi/rsi/rdx（如改 open 的檔名指標指向的內容）
    → 攔截並改變 syscall 要做什麼
        │
  syscall-exit（返回後）：改「回傳值」
    改 rax（如把成功改成失敗，或反之）
    → 讓 tracee 看到不同的結果
        │
  例（合法用途）：
    sandbox：攔截危險的 syscall，改成失敗（保護）
    record/replay：記錄 syscall 結果，replay 時注入相同的
    fault injection：故意讓某些 syscall 失敗（測試錯誤處理）
```

> **改 syscall 在 entry 改參數、在 exit 改回傳值——這是 sandbox、fault injection、record/replay 的底層機制**。ptrace 能在 syscall 邊界**修改** syscall：在 **syscall-entry**（進入前）改**參數**（改 rdi/rsi/rdx，如改 open 的檔名、改 write 的內容）——攔截並改變 syscall 要做什麼；在 **syscall-exit**（返回後）改**回傳值**（改 rax，如把成功改失敗）——讓 tracee 看到不同的結果。這有重要的合法用途：**sandbox**（攔截危險的 syscall 改成失敗，保護系統——如不讓某 process 開某些檔案）；**fault injection**（故意讓某些 syscall 失敗，測試程式的錯誤處理——「如果 malloc 失敗會怎樣」「如果 read 回 -1 程式有處理嗎」）；**record/replay**（記錄 syscall 的結果，replay 時注入相同的——用於 debug 不確定的 bug、time-travel debugging）。這些工具（如 gVisor 的 sandbox、rr 的 record/replay）底層都用 ptrace 攔截修改 syscall。理解這個機制，你知道「為什麼一個 process 能完全控制另一個的 syscall 行為」——這是強大也危險的能力。改參數比改回傳值複雜（參數常是指標，要改記憶體裡的內容，用 POKEDATA），改回傳值簡單（改 rax）。這展示了 ptrace 從「觀察 syscall」（strace）到「控制 syscall」（sandbox/injection）的延伸。

## 設斷點:debugger 的核心

```
debugger 怎麼設斷點（用 ptrace 改記憶體）：

  設斷點在某位址 addr：
    1. 讀出 addr 的原始指令（PTRACE_PEEKDATA）
    2. 在 addr 寫入「中斷指令」INT3（0xCC，x86）
       （PTRACE_POKEDATA，把第一個 byte 改成 0xCC）
    3. tracee 執行到 addr → 遇到 INT3 → 觸發 SIGTRAP → 暫停
    4. tracer 被通知（斷點命中）→ 讓使用者看變數等
        │
  繼續執行（過斷點）：
    1. 把原始指令寫回（恢復 addr）
    2. PTRACE_SINGLESTEP 執行那一條原始指令
    3. 再把 INT3 寫回（斷點繼續有效）
    4. PTRACE_CONT 繼續
        │
  → gdb 的斷點 = 在目標位址寫入 INT3（0xCC）
    命中時恢復原指令、單步、再設回斷點
    這就是「軟體斷點」的原理
        │
  改變數（gdb set var）= PTRACE_POKEDATA 改那個變數的記憶體
```

```c
// 設斷點的核心（概念）
long orig = ptrace(PTRACE_PEEKDATA, child, addr, NULL);  // 讀原始指令
long bp = (orig & ~0xFF) | 0xCC;                          // 第一個 byte 改成 INT3
ptrace(PTRACE_POKEDATA, child, addr, bp);                // 寫入斷點
// tracee 執行到 addr → SIGTRAP → tracer 暫停
// 過斷點：恢復 orig、SINGLESTEP、再寫 bp
```

> **debugger 的軟體斷點 = 在目標位址寫入中斷指令 INT3（0xCC）——這是 gdb 斷點的底層原理**。gdb 怎麼設斷點？答案是用 ptrace **改記憶體**：(1) 讀出目標位址的原始指令（PEEKDATA）；(2) 在那裡**寫入中斷指令 INT3（0xCC，x86）**（POKEDATA，把第一個 byte 改成 0xCC）；(3) tracee 執行到那裡遇到 INT3 → 觸發 **SIGTRAP** → 暫停 → tracer 被通知（斷點命中）→ 讓你看變數、堆疊等。**過斷點繼續**：恢復原始指令、`PTRACE_SINGLESTEP` 執行那一條、再寫回 INT3（斷點繼續有效）、`PTRACE_CONT`。這就是「**軟體斷點**」的原理——在程式碼裡植入一個會觸發中斷的 byte。`gdb` 的 `break` 命令就是這樣運作的（對硬體斷點，用 CPU 的 debug 暫存器，不改記憶體，但軟體斷點是基礎）。同理，gdb 的 **`set var`（改變數）= POKEDATA 改那個變數的記憶體**、**`print var`（看變數）= PEEKDATA 讀記憶體**、**`step`（單步）= PTRACE_SINGLESTEP**。理解這些，你完整理解了 debugger 的底層——它全是 ptrace 的觀察（GETREGS/PEEKDATA）和控制（SETREGS/POKEDATA/SINGLESTEP）的組合。這是本課「理解工具底層」的延伸——從理解 strace（Ch 4）到理解整個 debugger。當你用 gdb 設斷點、改變數、單步，你知道底層是 ptrace 在改 tracee 的記憶體和暫存器。這也連到 gdb 課（如果有）——那課深入 debugger 的操作，本章是它的底層機制。

## 程式碼注入

```
程式碼注入（把程式碼放進 tracee 並執行）：

  目標：讓 tracee 執行「你的」程式碼（不是它原本的）
        │
  步驟（簡化）：
    1. 暫停 tracee（attach 或斷點）
    2. 備份當前的暫存器和某段記憶體（之後恢復）
    3. 把「你的程式碼」（shellcode）寫進 tracee 的記憶體（POKEDATA）
    4. 改 PC（SETREGS 把 rip 指向你的程式碼）
    5. 繼續執行 → tracee 執行你的程式碼
    6. 完成後恢復原本的暫存器和記憶體（tracee 像沒事一樣繼續）
        │
  合法用途：
    熱修補（hot patching）：不重啟 process 修 bug
    動態插樁：注入監控程式碼
    debugger 的 call（gdb 的 call 命令，在 tracee 裡呼叫函式）
        │
  攻防用途：
    惡意注入（把惡意程式碼放進正常 process 躲避偵測）
    → 安全工具偵測這類注入
        │
  → 注入 = 寫程式碼進 tracee + 改 PC 執行 + 恢復
    這是 ptrace 控制能力的極致
```

> **程式碼注入 = 寫程式碼進 tracee + 改 PC 執行 + 恢復——這是 ptrace 控制能力的極致，有熱修補等合法用途也有惡意用途**。程式碼注入讓 tracee 執行「你的」程式碼：(1) 暫停 tracee；(2) **備份**當前暫存器和某段記憶體（之後恢復）；(3) 把你的程式碼（shellcode）**寫進** tracee 的記憶體（POKEDATA）；(4) 改 PC（SETREGS 把 rip 指向你的程式碼）；(5) 繼續執行 → tracee 執行你的程式碼；(6) **恢復**原本的狀態（tracee 像沒事一樣繼續）。這是 ptrace 控制能力的極致——你能讓一個 process 執行任意程式碼。**合法用途**：**熱修補**（不重啟 process 修 bug——對不能停的服務很有用）、**動態插樁**（注入監控程式碼）、**gdb 的 `call` 命令**（在 tracee 裡呼叫函式，如 `call my_function()`——它就是注入呼叫程式碼）。**攻防用途**：惡意軟體用注入把惡意程式碼放進正常 process（如把惡意碼注入 explorer.exe 躲避偵測），安全工具偵測這類注入（看 process 的記憶體有沒有異常的可執行區、PC 有沒有指向奇怪的地方）。理解注入，你知道「一個 process 能讓另一個執行任意程式碼」這個強大且危險的能力——這是 debugger 的 `call`、熱修補工具、和惡意注入的共同底層。本章以理解原理為目的（針對自己的 process），實際的注入工具（如 frida、各種 hot-patch 框架）建立在這個機制上。這完成了 ptrace 的全貌——從觀察（strace）到控制（改 syscall/設斷點/注入）。

## 反調試:對抗 ptrace

```c
// 反調試：程式偵測「自己被 trace」（惡意軟體用，理解防禦）
#include <stdio.h>
#include <sys/ptrace.h>
int main() {
    // 技巧 1：自己 ptrace 自己（如果已被 trace 會失敗）
    if (ptrace(PTRACE_TRACEME, 0, NULL, NULL) == -1) {
        printf("Being traced! (anti-debug triggered)\n");
        return 1;   // 偵測到 debugger → 退出/改變行為
    }
    printf("Not traced, running normally\n");
    return 0;
}
```

```bash
# gcc -o antidebug antidebug.c
# ./antidebug              → Not traced（正常跑）
# strace ./antidebug       → Being traced!（偵測到 strace 在 trace 它）

# 其他反調試技巧：
#   檢查 /proc/self/status 的 TracerPid（非 0 = 被 trace）
#   時間檢查（被 trace 會變慢，Ch 3 的開銷）
#   檢查斷點（找 INT3 0xCC）
#       │
# → 惡意軟體用反調試躲避分析
#   分析者用「對抗反調試」的技術（patch 掉檢查、用更隱蔽的工具）
#   這是 ptrace 攻防的延伸
```

> **反調試（偵測自己被 trace）是 ptrace 攻防的延伸——理解它對分析惡意軟體和理解 ptrace 的限制很重要**。**反調試（anti-debugging）** 是程式偵測「自己被 debugger/tracer 追蹤」並改變行為（躲避分析）。經典技巧：(1) **自己 ptrace 自己**——`ptrace(PTRACE_TRACEME)` 如果**已被 trace** 會失敗（一個 tracee 只能一個 tracer，Ch 3），所以失敗 = 被 trace；(2) **檢查 /proc/self/status 的 TracerPid**（非 0 = 被某 process trace）；(3) **時間檢查**（被 trace 會變慢，Ch 3 的開銷——程式測自己某段執行多久，異常慢 = 被 trace）；(4) **找斷點**（掃描自己的程式碼有沒有 INT3 0xCC = 被設斷點）。**惡意軟體用反調試躲避分析**（偵測到 debugger 就退出、休眠、或顯示假行為，讓分析者看不到真實惡意行為）。**分析者用「對抗反調試」**（patch 掉反調試檢查、用更隱蔽的工具如硬體斷點、或修改 ptrace 的行為騙過檢查）。這是 ptrace 攻防的軍備競賽（呼應 networking 課的審查攻防——技術的攻防本質）。理解反調試對：(1) **分析惡意軟體**（知道它可能反調試，要對抗）；(2) **理解 ptrace 的限制**（不是所有程式都能順利 trace，有些主動對抗）；(3) **資安**（知道攻擊和防禦的技術）。本章以理解原理為目的（合法的 debug/分析），不是教你寫惡意軟體——理解攻擊技術是為了防禦和分析。這完成了 Ch 19 的 ptrace 進階——從修改 syscall、設斷點、注入到反調試，你完整理解了「一個 process 控制另一個」的能力光譜，以及它的攻防意義。

## 動手練習

1. 改回傳值：寫一個 tracer，攔截 tracee 的某個 syscall，改它的回傳值，看 tracee 的反應

2. 理解斷點：讀「設斷點」那節的程式碼，理解 gdb 的 break 怎麼用 INT3 運作

3. 對照 gdb：用 gdb 設斷點、`set var` 改變數、`call` 函式，對照它們的 ptrace 底層

4. 反調試：編譯 antidebug.c，直接跑（正常）vs strace 跑（偵測到），理解反調試

5. 對抗反調試：思考怎麼讓 antidebug.c 即使被 trace 也偵測不到（patch ptrace 檢查）

## 本章重點整理

- ptrace 不只觀察（讀，Ch 3-4），還能控制（寫）：SETREGS 改暫存器、POKEDATA 改記憶體
- 改 syscall：entry 改參數、exit 改回傳值——sandbox、fault injection、record/replay 的底層
- debugger 斷點 = 寫入 INT3（0xCC）；改變數 = POKEDATA；單步 = SINGLESTEP——gdb 全是 ptrace 組合
- 程式碼注入 = 寫程式碼進 tracee + 改 PC 執行 + 恢復；熱修補/gdb call 的合法用途，也有惡意用途
- 反調試（偵測自己被 trace）是 ptrace 攻防延伸；理解它對分析惡意軟體和理解 ptrace 限制重要

## 自我檢核

- [ ] 知道 ptrace 能控制 tracee（改暫存器/記憶體），不只觀察
- [ ] 理解怎麼改 syscall 的參數和回傳值，以及合法用途
- [ ] 理解 debugger 的斷點/改變數/單步怎麼用 ptrace 實現
- [ ] 知道程式碼注入的原理和用途（合法 + 攻防）
- [ ] 理解反調試和 ptrace 攻防

## 延伸閱讀

### 文章

- **[Playing with ptrace, Part II](https://www.linuxjournal.com/article/6210)** — Linux Journal
  - **讀哪裡**：注入和修改那部分
  - **為什麼值得讀**：本章注入技術的經典教學（Ch 4 的 Part I 延續）

- **[Writing a Linux Debugger - breakpoints](https://blog.tartanllama.xyz/writing-a-linux-debugger-breakpoints/)** — TartanLlama
  - **這篇說什麼**：用 ptrace 實作斷點（INT3）的完整教學
  - **為什麼值得讀**：本章「設斷點」的完整實作版

### 官方文件

- **[ptrace(2)](https://man7.org/linux/man-pages/man2/ptrace.2.html)** — Linux man-pages
  - **讀哪裡**：PTRACE_SETREGS/POKEDATA/SINGLESTEP
  - **為什麼值得讀**：控制 tracee 的 ptrace 請求的權威

### 資安

- **[Anti-debugging techniques](https://www.apriorit.com/dev-blog/367-anti-reverse-engineering-protection-techniques-to-use-before-releasing-software)** — Apriorit
  - **為什麼值得讀**：理解反調試技術（分析惡意軟體/理解 ptrace 限制）

下一章看 LD_PRELOAD——另一種攔截技術，用動態連結（Ch 6 的 PLT/GOT）覆蓋 library 函式。這是「動手三大章」之三，和 ptrace 注入是不同的攔截方式。

→ [Ch 20 LD_PRELOAD 攔截器](./20-ld-preload-interceptor.md)
