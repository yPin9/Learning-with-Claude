# Ch 1 — Debugger 到底在做什麼

> **目標**：建立貫穿整門課的心智模型。學完你能用自己的話說清楚：GDB 怎麼一邊讓程式執行、一邊在任意時刻凍結它、偷看它的記憶體與暫存器、甚至改它。理解 inferior / target / symbol 這三個核心抽象。

> **環境**：概念為主，不綁版本。示範以 GDB 13、Linux x86_64。

## 為什麼需要先建立模型，而不是直接背指令？

你可以背一百個 GDB 指令，但只要腦中沒有「debugger 是怎麼運作的」這張圖，碰到沒背過的狀況就卡死。反過來，只要你懂底層模型，很多指令的行為你用猜的都能猜對——因為它們都是同一套機制的不同包裝。

這一章不教任何「實用」指令。它教的是讓後面 41 章都站得穩的地基。

## 先建立直覺：兩個 process，一個偷看另一個

最關鍵的一件事，很多人從沒意識到：**GDB 和你 debug 的程式是兩個獨立的 process。**

```
   ┌─────────────────┐                    ┌──────────────────────┐
   │   GDB process   │                    │  你的程式 (inferior) │
   │                 │   ptrace syscall   │                      │
   │  - 讀你的指令   │ ─────────────────> │  - 真正在跑的程式碼  │
   │  - 解析 DWARF   │ <───────────────── │  - 它的記憶體/暫存器 │
   │  - 控制執行     │   stop 通知/狀態   │  - 它被 GDB 凍結/喚醒│
   └─────────────────┘                    └──────────────────────┘
        控制方                                    被控制方
```

GDB 自己不「執行你的程式碼」。它**指揮作業系統**去啟動、暫停、繼續另一個 process，並透過 OS 提供的管道讀寫那個 process 的記憶體和暫存器。在 Linux 上，這個管道就是 `ptrace` 系統呼叫（Ch 2 細講）。

這個「兩個 process」的事實能解釋一堆現象：

- 為什麼 GDB 崩潰時你的程式可能還活著（或變殭屍）——它們是分開的。
- 為什麼 `attach` 一個跑到一半的 process 是可能的——只要 OS 允許一個 process trace 另一個。
- 為什麼有些操作需要 root 或特殊權限——偷看別人的 process 記憶體是高權限動作。

GDB 把它正在控制的那個 process 叫 **inferior**（下級、被控方）。整門課你會一直看到這個詞。

## Debugger 的三件核心工作

不管多複雜的功能，debugger 做的事都歸到三類：

### 1. 執行控制（execution control）

讓 inferior「跑 / 停」，並且能在**精確的時機**停下來。

- 跑：`run`、`continue`
- 停：到 breakpoint、收到 signal、single-step 走完一步、watchpoint 觸發
- 精細控制：一次一條原始碼行（`step`）、一次一條機器指令（`stepi`）、跑完目前函式（`finish`）

底層機制：透過 ptrace 讓 OS 在事件發生時把 inferior 凍住並通知 GDB。「凍住」的意思是 OS 把那個 process 的狀態改成 stopped，CPU 不再排程它，直到 GDB 說繼續。

### 2. 狀態檢視與修改（inspection & mutation）

inferior 停下來後，看它的內部、必要時改它。

- 看記憶體：`x`、`print`（Ch 7）
- 看暫存器：`info registers`（Ch 11）
- 看 call stack：`backtrace`（Ch 10）
- 改：`set var x = 5`、`set $rax = 0`——直接改 inferior 的記憶體或暫存器

「看記憶體」底層就是 GDB 透過 ptrace 去讀 inferior 位址空間的某段 byte，再用 DWARF 把那串 byte 解釋成「`int x = 42`」這種人看得懂的東西。

### 3. 符號翻譯（symbolication）

把人類語言（`main`、`x`、`hello.c:10`）和機器語言（位址 `0x555...1149`、暫存器、offset）互相翻譯。

- 你說 `break main`，GDB 查 symbol table / DWARF，找出 `main` 的位址，去那裡下斷點。
- inferior 停在某位址，GDB 反查 DWARF，告訴你「這是 `hello.c` 第 10 行、在函式 `add` 裡」。

這層翻譯就是 Ch 0 講的「地圖」。沒有它，前兩件事 GDB 都還能做（對位址操作），只是你看到的全是裸位址。

```
    你打的指令              GDB 內部                  對 inferior 的動作
   ┌───────────┐        ┌──────────────┐          ┌──────────────────┐
   │ break add │ ─────> │ 查 DWARF:    │  ptrace  │ 把 0x1131 那個    │
   │           │        │ add=0x1131   │ ───────> │ byte 換成 INT3    │
   └───────────┘        └──────────────┘          └──────────────────┘
   ┌───────────┐        ┌──────────────┐          ┌──────────────────┐
   │ print sum │ ─────> │ 查 DWARF:    │  ptrace  │ 讀 rbp-0x4 那     │
   │           │        │ sum=rbp-0x4  │ <─────── │ 4 個 byte         │
   └───────────┘        └──────────────┘          └──────────────────┘
```

整門課其實就是把這張圖的每一格放大來看。

## GDB 的核心抽象詞彙

這些詞你會看一輩子，現在先定義清楚。

| 詞 | 意思 | 第一次深入的章節 |
|---|---|---|
| **inferior** | GDB 正在控制的那個被 debug 的 process（可以有多個） | Ch 3 |
| **target** | inferior「住在哪」——本機 process、core dump、遠端 gdbserver、模擬器。GDB 用同一套指令操作不同 target | Ch 36 |
| **symbol** | 名字（函式、變數、型別）到位址 / 位置的對應，來自 ELF symtab 或 DWARF | Ch 6 |
| **frame** | call stack 上的一層，對應一次函式呼叫 | Ch 10 |
| **breakpoint** | 「跑到這裡停下來」的設定，底層多半是把指令換成 INT3 | Ch 4 |
| **value / value history** | GDB 對一個運算結果的表示；每次 `print` 的結果都存進 `$1`、`$2`… | Ch 8 |

**target 這個抽象特別重要**：GDB 厲害的地方在於，無論你 debug 的是「本機正在跑的 process」、「一個 core dump 檔案」、「網路另一頭的 gdbserver」、還是「QEMU 模擬的 ARM」，你用的指令幾乎一模一樣。底層差很多（讀 core 是讀檔案、遠端是走 RSP 協定、本機是 ptrace），但 GDB 用 **target stack** 把這些差異藏起來。Ch 36 會看到 `target remote`，Ch 33 會看到 `target core`，Ch 42 會看 target stack 的內部。

## 一次 `break main; run` 背後發生什麼

把上面的東西串起來，走一遍最日常的操作：

```
(gdb) file hello          # 1. 載入符號
(gdb) break main          # 2. 設斷點
(gdb) run                 # 3. 啟動並停在斷點
```

1. **`file hello`**：GDB 開啟 ELF，解析 `.symtab` 與 `.debug_*`，把符號表載進記憶體（**此時還沒有任何 process 在跑**）。
2. **`break main`**：查到 `main` 在 `0x1149`。但**此刻還不能下 INT3**——因為 process 還沒啟動，記憶體還不存在。GDB 先記著「等程式起來後在 0x1149 下斷」（pending 的概念，Ch 4）。
3. **`run`**：
   - GDB `fork` 出一個子 process。
   - 子 process 呼叫 `ptrace(PTRACE_TRACEME)`，宣告「我要被我爸 trace」，然後 `execve("hello")`。
   - `execve` 後程式映像載入記憶體，此時 GDB 才真的去 `0x1149` 把原本的 byte 存起來、換成 `0xCC`（INT3）。
   - GDB 讓子 process 繼續（`PTRACE_CONT`）。
   - 程式跑到 `0x1149`，CPU 執行到 INT3，觸發 `SIGTRAP`，OS 凍結 inferior、通知 GDB。
   - GDB 醒來，發現停在斷點，把 `0xCC` 換回原本的 byte，把 `$pc` 退回一格，反查 DWARF 印出「`Breakpoint 1, main () at hello.c:9`」，把控制權還給你。

這一整套，Ch 2（ptrace）、Ch 4（breakpoint）、Ch 39（breakpoint 底層）會逐段放大。現在你只要記得這個**因果鏈**：你的指令 → GDB 查符號 → ptrace 操作 inferior → 事件發生 OS 凍結並通知 → GDB 翻譯後回報你。

## 歷史脈絡：在 GDB 之前

理解設計，要看它解決了什麼歷史問題。

- **最早**：沒有 debugger。debug 靠 `printf`（現在還很多人這樣，而且有時是對的）和讀 core dump 的 hex。
- **ptrace 的出現**（早期 Unix）：OS 提供一個機制，讓一個 process 能合法地檢視/控制另一個。這是所有 Unix debugger 的共同地基。`adb`、`dbx`、後來的 `gdb` 都建在它上面。
- **GDB**（1986, Richard Stallman 起手）：把符號除錯做成跨平台、跨語言、開源的標準。它的 target 抽象讓同一個 GDB 能 debug 本機、遠端、嵌入式、core。
- **現代分裂**：LLVM 陣營做了 **LLDB**（macOS / iOS 主推）。兩者模型極像（都建在 ptrace 類機制上），指令語法不同。Apple 平台主推 LLDB，Linux 仍以 GDB 為王。

> 為什麼不是「一個 process 直接讀另一個的記憶體就好」？因為 process 隔離是 OS 的基本安全保證——你不能隨便讀別人的記憶體。ptrace 是 OS 開的一道**受控、需授權**的後門，專門給 debugger 用。這也是為什麼 Ch 2 會講到 `ptrace_scope` 這種安全限制。

## 踩雷集錦

1. **以為 GDB「執行」你的程式**：不是。程式由 OS 排程在它自己的 process 裡跑，GDB 只是控制者。理解這點，多 process（Ch 17）、多執行緒（Ch 16）的行為才不會混亂。
2. **以為沒有原始碼就不能用 GDB**：能。執行控制與記憶體檢視都不需要 DWARF，逆向工程整天在沒符號的情況下工作（Ch 11、練習 A）。沒符號失去的只是「翻譯成人話」這層。
3. **把 target 跟 inferior 搞混**：inferior 是「被 debug 的那個東西」，target 是「它住在哪 / 怎麼存取它」。本機 debug 時兩者像是一體，但一接觸 core dump 和 remote 就得分清楚。
4. **以為 attach 跟 run 是完全不同的東西**：本質都是「讓 GDB 成為某 process 的 tracer」。`run` 是自己生一個 inferior，`attach` 是接管一個已存在的。底層機制相通（Ch 3）。
5. **以為 `print` 只是讀**：`print` 可以呼叫函式、可以有副作用（Ch 8 的 inferior call）。它是在 inferior 裡求值，不是純讀。

## 進階：再往深一層

- **GDB 是事件驅動的**：核心是一個「讓 inferior 跑 → 等它停 → 處理停的原因 → 決定下一步」的迴圈。Ch 15（async / non-stop）、Ch 25（Python events）都圍著這個迴圈轉。
- **「停下來」有很多種原因**：breakpoint、watchpoint、signal、single-step 完成、syscall entry/exit（catchpoint）、exec/fork。GDB 內部用一個 stop reason 來區分，你在 Python API（Ch 25）會直接碰到它。
- **target stack 是可疊加的**：例如 record/replay（Ch 34）會在原本的 process target 上面再疊一層，攔截執行控制。這就是為什麼 reverse-continue 能用同一套指令運作。

## 動手練習

1. 開兩個 terminal。一個跑 `sleep 1000 &` 記下 PID，另一個 `gdb -p <PID>`。觀察 GDB attach 後 `sleep` 被凍結（`ps` 看狀態變 `t`）。`continue` 後它繼續。體會「兩個 process、一個控制另一個」。
2. 對任意 `-g` binary：`gdb -batch -ex "break main" -ex run -ex bt ./hello`。對照本章「一次 break main; run 背後發生什麼」，指出每個輸出對應哪一步。
3. 把同一個 binary `strip` 掉再 `gdb`，`break main` 會怎樣？`run` 之後還能 `stepi`、`info registers` 嗎？驗證「沒符號仍能做執行控制」。

## 本章重點整理

- GDB 與 inferior 是**兩個 process**；GDB 透過 OS（Linux 上是 ptrace）控制與檢視 inferior。
- Debugger 的三件核心工作：執行控制、狀態檢視/修改、符號翻譯。
- inferior / target / symbol / frame 是貫穿全課的抽象；target 讓同一套指令能操作本機/core/遠端。
- 沒有 DWARF 也能 debug，只是失去「翻譯成人話」這層。

## 自我檢核

- [ ] 不看筆記，能不能用「兩個 process」的圖解釋 GDB 怎麼控制程式？
- [ ] 講得出 debugger 的三件核心工作各是什麼、分別對應後面哪些章嗎？
- [ ] 面試時被問「GDB attach 到一個 process 時底層發生什麼」，你的一句話版本是？
- [ ] 能說清楚 inferior 與 target 的差別嗎？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Inferiors and Programs](https://sourceware.org/gdb/current/onlinedocs/gdb/Inferiors-Connections-and-Programs.html)**
  - **讀哪裡**：開頭對 inferior / connection 的定義。
  - **和本章的關聯**：把本章口語化的「inferior」「target」對到 GDB 官方術語。

### 部落格 / 文章

- **[How debuggers work: Part 1 (Basics)](https://eli.thegreenplace.net/2011/01/23/how-debuggers-work-part-1)** — Eli Bendersky
  - **這篇說什麼**：用最小的 C 程式示範 ptrace 怎麼控制另一個 process。
  - **讀哪裡**：整篇都短而精；本章的「兩個 process」模型在這裡有可跑的 code 版本。
  - **為什麼值得讀**：Eli 的系統文章是出了名的清楚；這是理解 Ch 2 的最佳暖身。

- **[Writing a Linux Debugger: Setup](https://blog.tartanllama.com/writing-a-linux-debugger-setup/)** — Sy Brand
  - **這篇說什麼**：從零寫 debugger 的系列開篇，講清楚 debugger 的責任分工。
  - **和本章的關聯**：本章的三件核心工作，這個系列會用 code 一件件實作；Ch 41 mini debugger 與它同路。

### 歷史

- **[GDB 的維基條目與 GNU 專案歷史](https://www.gnu.org/software/gdb/)**
  - **讀哪裡**：專案首頁的 history 與 supported architectures。
  - **為什麼值得讀**：理解 GDB 為何如此強調跨平台/跨 target——這是它的設計初衷。

下一章我們把「GDB 怎麼控制 inferior」這句話拆開，直接看那個底層機制：`ptrace`。

→ [Ch 2 接管一個 process 的根：ptrace](./02-ptrace-the-foundation.md)
