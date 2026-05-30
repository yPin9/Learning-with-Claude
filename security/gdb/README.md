# GDB 學習筆記：從會用到能改，最後寫出自己的 gef

> 給用過 GDB、但想把它從頭吃透，並且最終能寫出自己客製指令與 gef / pwndbg 等級插件的工程師。

大部分人對 GDB 的掌握停在 `break`、`run`、`bt`、`print` 四招。這門課把 GDB 當成一個**可程式化的除錯平台**來教：先把所有功能系統化吃乾抹淨，再看進 ptrace 與 DWARF 的底層，最後用 Python API 寫出一整套 gef 風格的插件。學完你不只是 GDB 的重度使用者，而是能**改造 GDB**的人。

以 C / C++ 與 Linux x86_64 為主要示範環境，並補上 Rust、Go、跨架構與嵌入式。

學完你應該能：

- 熟練操作 GDB 幾乎所有功能——含 conditional watchpoint、reverse debugging、non-stop mode、remote、core dump
- 讀懂 core dump、用 rr 做時間旅行，從一個壞掉的 process 還原當機現場
- 用 Python API 寫自訂指令、pretty-printer、frame filter、unwinder、xmethod、TUI window
- 解釋 GDB 底層怎麼用 ptrace 接管 process、怎麼讀 DWARF 定位變數、breakpoint 怎麼 patch 成 INT3
- 自己用 ptrace + DWARF 寫一個 mini debugger，理解 GDB 的本體
- **打造一套 gef / pwndbg 風格的插件套件**：context 視窗、heap 分析、telescope、自動化 workflow

## 為什麼學這個？

- **debug 是工程師一輩子的事**：IDE 的圖形化 debugger 多半是 GDB / LLDB 的包裝。包裝會換，核心不變。把核心吃透，換什麼 IDE 都不慌。
- **會用 ≠ 學會**：停在 `bt` 和 `print` 等於只用了 GDB 的 5%。conditional breakpoint、watchpoint、Python scripting、reverse、remote 這些招式，能讓你的 debug 效率翻好幾倍。
- **理解底層**：看進 ptrace 與 DWARF，你會懂為什麼 release build 那麼難 debug、為什麼變數顯示 `<optimized out>`、為什麼有些 breakpoint 得用硬體的、為什麼 PIE 執行檔的位址每次都不一樣。
- **能改才是真的會**：gef、pwndbg、Voltron 這些神級工具，本質都是 GDB Python API 的插件。看懂它們、進而寫出自己的，你對作業系統、ABI、除錯資訊的理解會上一個量級——這也是資安、逆向、系統工程的硬底子。

## 先修知識

- C 語言（程度：能讀寫含指標、struct、malloc 的程式）
- 基本組合語言（程度：看得懂 x86-64 的 `mov`/`call`/`push`，不懂也沒關係，Ch 11 會補）
- Linux 命令列與 process 概念（程度：知道 fork/exec、signal、fd 大致是什麼）
- Python（程度：會寫 class 與 function；Part 5 之後才需要）
- 沒有也沒關係的：DWARF、ELF 細節、編譯器最佳化——這些課程內會教

## 課程地圖

### Part 1 — 基礎與心智模型（Ch 0–4）
- [Ch 0 環境與「可除錯的 build」](./00-environment-and-debuggable-builds.md)
- [Ch 1 Debugger 到底在做什麼](./01-what-a-debugger-does.md)
- [Ch 2 接管一個 process 的根：ptrace](./02-ptrace-the-foundation.md)
- [Ch 3 啟動、附加、inferior 管理](./03-launching-attaching-inferiors.md)
- [Ch 4 Breakpoint 的世界](./04-breakpoints-overview.md)
- [練習 A：逆出一個無原始碼程式的控制流](./practice-a-controlflow-reversing.md)

### Part 2 — 看穿程式狀態（Ch 5–11）
- [Ch 5 Stepping 全家](./05-stepping.md)
- [Ch 6 原始碼與符號](./06-source-and-symbols.md)
- [Ch 7 看資料：print / display / x](./07-print-display-examine.md)
- [Ch 8 表示式語言與 convenience variables](./08-expressions-and-convenience-vars.md)
- [Ch 9 型別系統](./09-type-system.md)
- [Ch 10 Stack 與 frame](./10-stack-and-frames.md)
- [Ch 11 暫存器與記憶體](./11-registers-and-memory.md)
- [練習 B：資料結構偵探](./practice-b-data-structure-detective.md)

### Part 3 — 進階斷點與並行（Ch 12–17）
- [Ch 12 條件斷點與 breakpoint commands](./12-conditional-breakpoints-and-commands.md)
- [Ch 13 Watchpoint](./13-watchpoints.md)
- [Ch 14 Catchpoint](./14-catchpoints.md)
- [Ch 15 Signal 與非同步控制](./15-signals-and-async.md)
- [Ch 16 多執行緒除錯](./16-multithreaded-debugging.md)
- [Ch 17 多行程與 fork / exec](./17-multiprocess-fork-exec.md)
- [練習 C：多執行緒 race condition 圍捕](./practice-c-race-condition.md)

### Part 4 — 介面、腳本、自動化基礎（Ch 18–21）
- [Ch 18 TUI 與 layout](./18-tui-and-layouts.md)
- [Ch 19 .gdbinit、auto-load 與安全模型](./19-gdbinit-and-autoload.md)
- [Ch 20 GDB 命令語言](./20-command-language.md)
- [Ch 21 自訂指令模式集](./21-custom-command-patterns.md)
- [練習 D：用純命令語言寫自動化指令](./practice-d-command-language-automation.md)

### Part 5 — Python API：從會用到能寫插件（Ch 22–28）★重頭戲
- [Ch 22 Python API 入門](./22-python-api-intro.md)
- [Ch 23 Value / Type / Symbol / Frame 物件模型](./23-value-type-symbol-frame.md)
- [Ch 24 自訂 Command 與 Parameter](./24-python-commands-and-parameters.md)
- [Ch 25 程式化 Breakpoint 與事件](./25-python-breakpoints-and-events.md)
- [Ch 26 Pretty-printer 框架](./26-pretty-printer-framework.md)
- [Ch 27 Frame filter / decorator / Unwinder](./27-frame-filters-and-unwinders.md)
- [Ch 28 Xmethod、彩色輸出、Python TUI window](./28-xmethods-and-tui-windows.md)
- [練習 E：寫一套 Python 插件（heap 視覺化）](./practice-e-python-plugin-pack.md)

### Part 6 — 多語言與真實世界 binary（Ch 29–32）
- [Ch 29 C++ 深度除錯](./29-cpp-deep-debugging.md)
- [Ch 30 C++ STL pretty-printer 實戰](./30-stl-pretty-printers.md)
- [Ch 31 Rust 與 Go 除錯](./31-rust-and-go-debugging.md)
- [Ch 32 除錯最佳化過的 binary](./32-debugging-optimized-binaries.md)
- [練習 F：替自訂 C++ 容器寫 printer + xmethod](./practice-f-cpp-container-printer.md)

### Part 7 — 事後分析、時間旅行、遠端、嵌入式（Ch 33–37）
- [Ch 33 Core dump 事後分析](./33-core-dumps.md)
- [Ch 34 Reverse debugging](./34-reverse-debugging.md)
- [Ch 35 rr：record-replay 時間旅行](./35-rr-record-replay.md)
- [Ch 36 gdbserver 與 remote protocol](./36-gdbserver-and-remote-protocol.md)
- [Ch 37 跨架構與嵌入式](./37-cross-arch-and-embedded.md)
- [練習 G：從 production core dump 還原現場](./practice-g-production-core-dump.md)

### Part 8 — 底層原理與打造你自己的工具（Ch 38–42）
- [Ch 38 DWARF 除錯資訊剖析](./38-dwarf-debug-info.md)
- [Ch 39 Breakpoint / single-step 底層實作](./39-breakpoint-singlestep-internals.md)
- [Ch 40 ASLR / PIE / 符號重定位](./40-aslr-pie-relocation.md)
- [Ch 41 用 ptrace + DWARF 寫 mini debugger](./41-ptrace-dwarf-mini-debugger.md)
- [Ch 42 GDB 內部架構與如何貢獻](./42-gdb-internals-and-contributing.md)
- [Final Project：打造你自己的 GDB 插件套件](./final-project-gdb-plugin-suite.md)

## 學習方式建議

1. **每章都開一個 terminal**：不要只讀。每個 GDB 指令親手打過一次，手感才會長出來。本課所有範例都能在 Linux x86_64 上實際跑出來。
2. **故意改壞**：範例跑得順時，把它改壞一次——改型別、加野指標、砍 `free()`、製造 race。看 GDB 能不能抓到、怎麼抓到。失敗的輸出比成功的更會教你東西。
3. **Python 章節一定要自己寫**：Part 5 光讀沒用。把你日常 debug 的某個重複動作寫成腳本，它才會變成你的東西。
4. **對照真實工具的原始碼**：gef、pwndbg 都是開源的。Part 5 之後，挑它們其中一個指令來讀，你會發現課程教的每個 API 都用得上。

## 建議環境

- Linux（Ubuntu 22.04+ / Debian 12+ / Arch 皆可），x86_64
- `gdb >= 13`（本課以 13/14 為基準；Python 3.8+ 內嵌，多數範例都需要）
- `gcc` 與 `clang` 兩種 compiler（某些章會比較它們產出的 DWARF）
- 選配：`rr`（Ch 35）、`qemu-user` / `qemu-system`（Ch 37）、`gef` 或 `pwndbg`（Ch 0 裝）
- macOS / Windows 使用者請用 WSL2 或 Linux VM——GDB 在非 Linux 環境諸多不便（macOS 需處理 codesign、且 Apple 主推 lldb）。

Ch 0 會一次把環境弄好。

## 精選資料庫

整門課最值得反覆參照的資源。每章「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **《Debugging with GDB》** — GDB 官方 manual：<https://sourceware.org/gdb/current/onlinedocs/gdb/>
  - 整門課的權威來源；行為不符預期時，這裡是最終仲裁。Python API 章節（Extending GDB → Python API）尤其要常翻。
- **《The Art of Debugging with GDB, DDD, and Eclipse》** — Norman Matloff, Peter Jay Salzman（No Starch, 2008）
  - 雖然舊，但「除錯思維」那部分不過時；當操作面的補充。

### 推薦論文 / 規格

- **[DWARF Debugging Information Format v5](https://dwarfstd.org/doc/DWARF5.pdf)** — DWARF 標準委員會（2017）
  - Part 8 的 DWARF 章直接根據它；先讀 §1–2 概論、§6.2 line number program。
- **[rr: Lightweight Recording & Deterministic Debugging](https://arxiv.org/abs/1705.05937)** — O'Callahan et al., USENIX ATC（2017）
  - Ch 35 的理論基礎；解釋 rr 怎麼用 record-replay 做到確定性重播。

### 推薦部落格 / 文章

- **[Writing a Linux Debugger](https://blog.tartanllama.com/writing-a-linux-debugger-setup/)** — Sy Brand（系列）
  - Ch 41 mini debugger 的思路來源；用 C++ 從零寫一個 ptrace + DWARF debugger。
- **[Undo / GDB 官方 blog 的 Python API 系列](https://developers.redhat.com/blog/2017/11/10/gdb-python-api)** — Red Hat Developers
  - Part 5 的實用補充，講 Python API 在真實除錯場景的用法。

### 讀完本課之後

- **gef** (<https://github.com/hugsy/gef>) 與 **pwndbg** (<https://github.com/pwndbg/pwndbg>) 的原始碼
  - 你的 Final Project 的「業界標竿」；讀它們怎麼組織指令、怎麼做 heap 分析。
- **《Linux Kernel Debugging》** — Kaiwan N Billimoria（Packt, 2022）（把除錯推進 kernel 與 kgdb / KGDB / crash 的方向）
