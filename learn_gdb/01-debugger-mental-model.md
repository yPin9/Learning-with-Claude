# Ch 1 — Debugger 到底在做什麼

> 目標：在開始背指令之前，先在腦中建立一張圖：GDB 是個獨立的 process，它憑什麼能「暫停」「看記憶體」「印變數」另一個 process？答案是三個詞：**ptrace**、**signal**、**debug info**。

## 一個看似簡單的問題

你打開 gdb，打 `break main`、`run`，然後程式在 `main` 停下來，你輸入 `print x` 看到 `3`。

這件事背後有多少魔法？

- **為什麼程式會乖乖停在 `main`？** 它是你寫的 code，你又沒在原始碼裡寫 `exit()`。
- **GDB 怎麼「看得到」你的變數 `x`？** GDB 是另一個 process，它的記憶體空間跟你的程式完全隔開的。
- **GDB 怎麼知道 `x` 這個名字對應到哪個位址？** binary 裡都是機器碼，哪有變數名。

這章不會教任何指令，但看完之後你會懂：你打的每個 GDB 指令，底下發生什麼事。

## 一張圖：兩個 process 的舞蹈

```
┌──────────────────┐                    ┌──────────────────┐
│  GDB process     │                    │  your program    │
│  (pid 1001)      │                    │  (pid 1002)      │
│                  │  ptrace(ATTACH)    │                  │
│                  │ ─────────────────► │  被接管           │
│                  │                    │                  │
│                  │ ◄───────────────── │  SIGTRAP         │
│                  │     (遇到斷點)      │                  │
│                  │                    │                  │
│   讀你的 memory   │  ptrace(PEEKDATA) │  讓它讀           │
│                  │ ─────────────────► │                  │
│                  │                    │                  │
└──────────────────┘                    └──────────────────┘
       ▲
       │ 同時，GDB 在本機讀：
       │
   ┌───┴────────────────┐
   │ your_program 這個   │
   │ ELF 檔裡的          │
   │ DWARF debug info    │
   └────────────────────┘
```

三個角色：

1. **GDB process**：就是你輸入指令的那個 gdb。
2. **Inferior**（被 debug 的 process）：GDB 術語，指被它控制的目標程式。上圖就是「your program」。
3. **ELF 檔 + DWARF debug info**：躺在硬碟上的 binary。GDB 直接讀檔，不是從 inferior 讀。

## 第一個魔法：ptrace

Linux 提供一個系統呼叫叫 `ptrace`（process trace），它就是 debugger 的瑞士刀。一個 process 對另一個 process 呼叫 `ptrace`，就能做這些事：

| ptrace 動作 | 作用 |
|---|---|
| `PTRACE_ATTACH` | 接管一個已經在跑的 process |
| `PTRACE_TRACEME` | 「我」主動讓父 process 控制我（`gdb prog` 時用這個） |
| `PTRACE_PEEKDATA` | 讀目標 process 的記憶體（一個 word） |
| `PTRACE_POKEDATA` | 寫目標 process 的記憶體（下斷點就是靠這個） |
| `PTRACE_GETREGS` | 讀目標的暫存器（RIP、RSP、RAX、...） |
| `PTRACE_SETREGS` | 寫目標的暫存器 |
| `PTRACE_SINGLESTEP` | 讓目標執行**一條**機器指令然後停下 |
| `PTRACE_CONT` | 讓目標繼續跑 |

有這套東西，GDB 就有了所有需要的能力。**GDB 的本質就是一個會對 inferior 反覆呼叫 ptrace 的 process。**

Ch 17 會我們自己真的呼叫 `ptrace` 寫一個能下斷點的玩具 debugger。現在先記住：ptrace 是給 debugger 的後門。

## 第二個魔法：signal

`ptrace` 讓 GDB 能控制 inferior，但**什麼時候**控制？GDB 怎麼知道斷點到了？答案是 **signal**。

當 inferior 遇到以下狀況時，kernel 會對它發 signal：

| 狀況 | Signal |
|---|---|
| 執行到斷點指令 `int3`（x86） | `SIGTRAP` |
| 除零、segfault、非法指令 | `SIGFPE` / `SIGSEGV` / `SIGILL` |
| `Ctrl-C` | `SIGINT` |
| 另一個 process 殺它 | `SIGTERM` / `SIGKILL` |

當 inferior 被 ptrace 管著，**任何 signal 都會被 kernel 先轉交給 GDB**，GDB 決定要不要把 signal 吞掉、要不要通知你、要不要轉給 inferior。

所以整個執行循環長這樣：

```
GDB：ptrace(CONT)          ← 放 inferior 跑
     │
     ▼
inferior 跑、跑、跑、踩到一個 int3 指令
     │
     ▼
kernel 丟 SIGTRAP 給 inferior
     │
     ▼
但 inferior 被 ptrace，signal 被攔截
     │
     ▼
GDB 從 wait() 系統呼叫醒來，知道「inferior 停了，原因是 SIGTRAP」
     │
     ▼
GDB 查當前 RIP，對照斷點表，告訴你「Breakpoint 1, main () at hello.c:9」
     │
     ▼
你打指令，GDB 處理...
     │
     ▼
你打 continue → GDB：ptrace(CONT) → 回到最上面
```

這個「`ptrace(CONT)` → `wait()` 醒來 → 處理」的迴圈，就是 debugger 的心跳。

## 第三個魔法：breakpoint 怎麼「下」的？

這是很多人學 gdb 很久都沒想過的問題：你打 `break main`，GDB 到底做了什麼？

**它偷偷改你的機器碼。**

`main` 函式在 binary 裡，原本第一個指令可能是 `push %rbp`（opcode = `0x55`）。GDB 把那個 byte 改成 `0xcc`，也就是 `int3` — 一個 x86 專門用來觸發斷點的單 byte 指令。

```
原本：  55 48 89 e5 48 83 ec 10 ...      push %rbp; mov %rsp,%rbp; sub $0x10,%rsp ...
下斷後： cc 48 89 e5 48 83 ec 10 ...     int3       ; mov %rsp,%rbp; sub $0x10,%rsp ...
        ▲
        原本的 0x55 被 GDB 用 ptrace(POKEDATA) 改成 0xcc
```

CPU 執行到這裡會觸發 `SIGTRAP` → GDB 收到 → 告訴你停了。

那你 `continue` 時怎麼繼續跑？GDB 要做三件事：

1. 把 `0xcc` 改回 `0x55`（還原原指令）
2. 把 RIP 倒退一格（因為 `int3` 已經執行完，RIP 往前走了）
3. `ptrace(SINGLESTEP)` 執行那一條原指令
4. 再把 `0x55` 改回 `0xcc`（重新 armed，下次還能停）
5. `ptrace(CONT)` 正常跑

這叫 **software breakpoint**。它有一個特性：**不佔 CPU 資源、但數量受限於你有多少 byte 可改（通常無上限）**。

還有一種叫 **hardware breakpoint**，用 CPU 的 debug registers（x86 的 DR0–DR3）。硬體斷點的特性：不用改 memory、可設在 read-only 區域、但**整個 CPU 只有 4 個**。

Ch 19 會詳細看 breakpoint 實作。現在知道「GDB 改你 memory」這個事實就夠震撼了。

## 第四個魔法：debug info

你問 `print x`，GDB 怎麼知道 `x` 在哪？

答：**編譯器在 binary 裡留了一張地圖**。這張地圖叫 **DWARF**（Debugging With Arbitrary Record Formats）。

用 `readelf` 看你剛才編的 `hello`：

```bash
$ readelf -WS hello | grep debug
  [28] .debug_aranges    PROGBITS       ...
  [29] .debug_info       PROGBITS       ...
  [30] .debug_abbrev     PROGBITS       ...
  [31] .debug_line       PROGBITS       ...
  [32] .debug_str        PROGBITS       ...
  [33] .debug_line_str   PROGBITS       ...
```

這些 `.debug_*` 段就是 DWARF。它們記了：

- **`.debug_info`**：每個函式、每個變數、每個型別的描述。「`x` 是 `int`，放在 `rbp - 0x4`」。
- **`.debug_line`**：機器碼位址 ↔ 原始碼行號的對照表。「位址 `0x1149` 對應 `hello.c` 第 9 行」。
- **`.debug_str`**：所有字串（變數名、檔名、函式名）的字串池。

GDB 打開 binary 時把這些段讀進來，建出一棵內部資料結構。之後你打 `break main`，它查 `.debug_info` 找到 `main` 的位址；你打 `print x`，它查 `x` 的 location（`rbp - 0x4`），呼叫 `ptrace(PEEKDATA, rbp - 0x4)` 把 4 個 byte 撈出來當成 `int` 印。

**`-g` 旗標的作用就是叫 compiler 產生 DWARF。** 沒 `-g`，這些段就不存在，GDB 只有一片機器碼，變數名跟行號都沒了。

Ch 18 會整整一章拆 DWARF。現在記住：DWARF 是 **compiler 留給 debugger 的地圖**。

## 把三件事串起來

回到最初那個問題：`break main` → `run` → `print x` 到底發生什麼？

1. **啟動時**：GDB 讀 binary，讀 DWARF，建立「名字 → 位址 → 型別」的對照表。
2. **`break main`**：查 DWARF，`main` 位址是 `0x1149`。記錄「斷點 #1 在 `0x1149`」。
3. **`run`**：`fork()` + `execve()` 啟動 inferior，子 process 先做 `PTRACE_TRACEME` 後才 `execve`。
4. **`run` 繼續**：GDB 對 inferior 寫入 `0xcc` 到 `0x1149`，然後 `ptrace(CONT)`。
5. **inferior 跑到 `0x1149`**：觸發 `int3` → `SIGTRAP` → kernel 轉給 GDB。
6. **GDB 醒來**：`wait()` 回報 inferior 停了。查 RIP = `0x114a`（已超過 int3 一位），對照斷點表，確認命中 main。印出「Breakpoint 1, main () at hello.c:9」。
7. **`print x`**：查 DWARF，`x` 在 `rbp - 0x4`。`ptrace(GETREGS)` 拿 rbp，算出位址。`ptrace(PEEKDATA, addr)` 拿 4 個 byte。解讀成 `int`。印出 `$1 = 3`。

**這整套機制只需要 Linux kernel（ptrace）、signal、ELF/DWARF 這三樣東西。** 沒有魔法，只有分工。

## 一個常見誤解

「GDB 是不是一個 interpreter？它把我的程式一句一句解釋執行？」

**不是。** 你的程式**原生地在 CPU 上跑**，跟你平常 `./hello` 跑是一樣的。GDB 只是：

- 編譯階段留的 debug info 讓它看得懂位址
- ptrace 讓它能暫停、讀寫
- signal 讓它收到通知

這是 GDB 跟 JavaScript 的 debugger、Python 的 `pdb` 很大的差別 — 後兩者的「debugger」是 interpreter 的一部分，從一開始就在解釋執行 bytecode。GDB 是**外掛式**的。

## 動手驗證

先不用背，但用 `readelf` 看一眼 DWARF 實際存在那裡：

```bash
gcc -g hello.c -o hello
readelf --debug-dump=info hello | head -40
```

你會看到類似：

```
 <1><1e>: Abbrev Number: 2 (DW_TAG_subprogram)
    <1f>   DW_AT_external    : 1
    <1f>   DW_AT_name        : (indirect string, offset: 0xab): add
    <23>   DW_AT_decl_file   : 1
    <24>   DW_AT_decl_line   : 3
    ...
 <2><3c>: Abbrev Number: 3 (DW_TAG_formal_parameter)
    <3d>   DW_AT_name        : a
    <41>   DW_AT_type        : <0x65>
    <45>   DW_AT_location    : 2 byte block: 91 6c  (DW_OP_fbreg -20)
```

這就是地圖。翻譯：「有個叫 `add` 的函式，它在 `hello.c` 第 3 行，有個參數叫 `a`，型別編號 0x65，位置是 frame base 減 20」。

Ch 18 會整章拆這個。

## 自我檢核

- [ ] 我能說出 debugger 的三個基本元件：ptrace、signal、debug info
- [ ] 我知道 software breakpoint 的原理是改記憶體成 `int3`
- [ ] 我能解釋 `-g` 旗標產生的 `.debug_*` 段是給 GDB 看的地圖
- [ ] 我知道 GDB 跟 inferior 是兩個獨立的 process
- [ ] 我能描述 `print x` 從輸入到輸出的完整流程

下一章開始動手。先把最常用的六個指令摸熟：`run / break / continue / step / next / finish / until`。

→ [Ch 2 基本執行控制](./02-basic-execution-control.md)
