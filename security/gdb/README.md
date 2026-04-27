# GDB 學習筆記：從起手式到自寫 debugger

> 給用過 GDB 但想從頭系統化、並看進原理的工程師。

以 C 與 Linux / ELF 為主要示範環境，從最基本的 `break` / `run` 一路寫到自己用 `ptrace` + DWARF 實作一個 mini debugger。

學完你應該能：

- 熟練操作 GDB 所有常用與進階功能（含 reverse debugging、遠端、多執行緒、post-mortem）
- 讀懂 core dump，從一個壞掉的 process 還原現場
- 用 Python API 擴充 GDB，寫 pretty printer 與自動化 workflow
- 解釋 GDB 底層怎麼用 `ptrace` 接管 process、怎麼用 DWARF 定位變數、breakpoint 怎麼 patch 成 INT3
- 從零寫一個能下斷點、single-step、看 backtrace 的 mini debugger

## 為什麼學這個？

- **debug 是工程師一輩子的事**：IDE 的圖形化 debugger 是 GDB 的包裝，包裝會換，核心不變。
- **會用 != 學會**：大部分人停在 `bt` 和 `print`。真正的進階招式（reverse、conditional watchpoint、Python scripting、remote、core dump）可以讓你的 debug 能力翻倍。
- **理解底層**：看進 `ptrace` 與 DWARF，你會看懂為什麼 release build 那麼難 debug、為什麼 optimized variable 會顯示 `<optimized out>`、為什麼有些 breakpoint 要用硬體的。
- **debugger 本身是一個迷人的程式**：它在運行中讀別人的記憶體、patch 別人的指令、解別人的 stack。自己寫一個，對作業系統與 ABI 的理解會上一層。

## 課程地圖

### Part 1 — 基礎與心態
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 Debugger 到底在做什麼](./01-debugger-mental-model.md)

### Part 2 — 起手式（從零到能 debug 一隻 segfault）
- [Ch 2 基本執行控制](./02-basic-execution-control.md)
- [Ch 3 看資料：print / display / ptype](./03-inspecting-data.md)
- [Ch 4 檢視記憶體：x 指令全家](./04-examining-memory.md)
- [Ch 5 Stack 與 frame](./05-stack-and-frames.md)
- [Ch 6 條件斷點、watchpoint、catchpoint](./06-conditional-breakpoints-and-watchpoints.md)
- [練習 A：抓一隻經典 segfault](./practice-a-segfault-hunt.md)

### Part 3 — 進階操作與介面
- [Ch 7 TUI 模式與 layout](./07-tui-mode.md)
- [Ch 8 反組譯與暫存器](./08-disassembly-and-registers.md)
- [Ch 9 Signal、fork、exec](./09-signals-fork-exec.md)
- [Ch 10 Reverse debugging](./10-reverse-debugging.md)
- [練習 B：debug heap corruption（配合 valgrind）](./practice-b-heap-corruption.md)

### Part 4 — 多執行緒、遠端、Post-mortem
- [Ch 11 多執行緒 debug](./11-multithreaded-debugging.md)
- [Ch 12 Remote debugging 與 gdbserver](./12-remote-debugging.md)
- [Ch 13 Core dump 與 post-mortem](./13-core-dumps.md)
- [練習 C：從 core 檔還原現場](./practice-c-core-dump.md)

### Part 5 — 腳本化與自動化
- [Ch 14 .gdbinit 與 command script](./14-gdbinit-and-command-scripts.md)
- [Ch 15 Python API（一）：commands 與 breakpoints](./15-python-api-basics.md)
- [Ch 16 Python API（二）：pretty printers 與 frame filters](./16-python-api-pretty-printers.md)
- [練習 D：pretty printer + 自動化 workflow](./practice-d-pretty-printer.md)

### Part 6 — 原理深入
- [Ch 17 ptrace 系統呼叫](./17-ptrace-internals.md)
- [Ch 18 DWARF debug info](./18-dwarf-debug-info.md)
- [Ch 19 Breakpoint 的實作](./19-breakpoint-implementation.md)
- [Ch 20 ASLR / PIE / 符號重定位](./20-aslr-pie-symbol-resolution.md)
- [Ch 21 Frame unwinding 與 inferior call](./21-frame-unwinding-and-inferior-call.md)

### Part 7 — 整合專案
- [Final Project：minidbg（ptrace + DWARF 版）](./final-project-minidbg.md)

## 學習方式建議

1. **每章都開一個 terminal**：不要只讀。每個 GDB 指令親手打過一次，手感才會長出來。
2. **故意改壞**：範例程式跑得順的時候，把它改壞一次。改變數型別、加野指標、砍 `free()`、race condition — 看 GDB 能不能幫你抓到、怎麼抓到。
3. **對照原始碼**：GDB 是開源的。`Ch 19` 之後的章節，我們會偶爾瞄一眼 gdb 的 source tree，眼見為憑。
4. **Python 章節要自己寫**：Python API 那幾章光讀沒用，要把自己日常 debug 的某個動作寫成腳本，才會變成你的東西。

## 建議環境

- Linux（Ubuntu / Debian / Arch 皆可），x86_64
- `gdb >= 10`（Python 3 support、多數範例都需要）
- `gcc` 與 `clang` 兩種 compiler（某些章會比較產出的 DWARF）
- macOS / Windows 使用者建議用 WSL2 或 VM — GDB 在非 Linux 環境有諸多不便（macOS 需 lldb 對照、codesign 問題）。

Ch 0 會一次把環境弄好。

## 參考資料

- 《Debugging with GDB》— 官方 manual：<https://sourceware.org/gdb/current/onlinedocs/gdb/>
- 《The Art of Debugging with GDB, DDD, and Eclipse》— Norman Matloff, Peter Jay Salzman
- 《Linux Debugging and Performance Tuning》— Steve Best（偏 kernel 向，當補充）
- DWARF 規格：<https://dwarfstd.org/>
- 從零寫 debugger 的經典文章系列：Sy Brand's "Writing a Linux Debugger"（本課 Final Project 有參考其思路）
