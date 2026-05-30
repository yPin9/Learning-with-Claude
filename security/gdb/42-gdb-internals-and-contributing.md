# Ch 42 — GDB 內部架構與如何貢獻

> **目標**：俯瞰 GDB 本體的內部架構——target stack、gdbarch、symbol/value/frame 子系統怎麼組織，並指出怎麼讀 GDB 原始碼、怎麼 build、怎麼貢獻 patch。這章把你從「能寫 GDB 插件」推向「能改 GDB 本身」，是這門課「會用 → 能改」的最終一哩。

> **環境**：GDB 13/14 原始碼，Linux x86_64。本章偏導覽（GDB source 超過百萬行，目標是給你地圖，不是逐行讀）。

## 為什麼要看 GDB 自己的原始碼

你寫過 mini debugger（Ch 41）懂了本體原理，寫過 Python 插件（Part 5）懂了擴充。最後一步：看 GDB 真正的內部，理解它怎麼把你學的所有東西組織成一個跨架構、跨 target、跨語言的工業級系統。

回報：

- debug GDB 本身的問題（它行為怪、某功能壞）
- 為 GDB 加功能 / 修 bug（貢獻 upstream）
- 理解你的 Python 插件底下 C++ 在做什麼
- 把「能改」推到極致——不只改 GDB 的行為（插件），而是改 GDB 的程式碼

> 認識論誠實：GDB 是個有 30+ 年歷史、百萬行 C++ 的老專案，內部複雜、文件有限、學習曲線陡。這章給你**地圖與切入點**，不是要你讀完。多數人不會改 GDB 本體——但知道它怎麼組織，讓你成為真正理解這個工具的人。

## 先建立直覺：GDB 的分層

```
   ┌─────────────────────────────────────────────────┐
   │ 使用者介面：CLI / TUI / MI / Python API           │ ← 你下指令的地方
   ├─────────────────────────────────────────────────┤
   │ 指令處理 / 表示式求值 / 值系統 (value)            │ ← parse_and_eval 底下
   ├─────────────────────────────────────────────────┤
   │ 符號層：symtab / DWARF reader / type system       │ ← Ch 6,9,38
   ├─────────────────────────────────────────────────┤
   │ frame / unwinding                                 │ ← Ch 10,27
   ├─────────────────────────────────────────────────┤
   │ gdbarch：架構抽象（x86/ARM/RISC-V 各一份）         │ ← 跨架構的關鍵
   ├─────────────────────────────────────────────────┤
   │ target stack：本機/core/remote/sim 各一層         │ ← Ch 1,33,36
   ├─────────────────────────────────────────────────┤
   │ 底層：ptrace / RSP / core 讀取                     │ ← Ch 2,36
   └─────────────────────────────────────────────────┘
```

兩個最重要的抽象——**target stack** 和 **gdbarch**——是 GDB 能「一套指令操作任何東西」的關鍵。

## target stack：Ch 1 的 target 抽象，實作版

承 Ch 1：GDB 用同一套指令操作本機 process、core dump、遠端、模擬器。這靠 **target stack**——一疊可堆疊的 target_ops，每層攔截/轉發操作：

```
   你下 "continue"
        │
        ▼
   target stack（由上而下找誰能處理）
   ┌──────────────────────┐
   │ record（若開）         │ ← Ch 34，攔截做 record/replay
   ├──────────────────────┤
   │ thread layer          │
   ├──────────────────────┤
   │ process target:       │ ← 本機=linux-nat（ptrace）
   │   - linux-nat (ptrace)│    core=core target（讀檔）
   │   - remote (RSP)      │    遠端=remote（Ch 36）
   │   - core              │
   └──────────────────────┘
```

每個 target 實作 `target_ops` 的方法（`to_resume`、`to_wait`、`to_read_memory`…）。`continue` 呼叫 `target_resume`，target stack 找到能處理的那層（本機→ptrace、遠端→送 RSP、core→報錯不能跑）。

原始碼：`target.h` / `target.c`（介面）、`linux-nat.c`（本機 Linux ptrace）、`remote.c`（RSP）、`corelow.c`（core）、`record-full.c`（record）。**這就是為什麼你 Ch 33/36 用同樣指令操作 core 和遠端**——它們是 target stack 的不同層。

## gdbarch：跨架構抽象

承 Ch 37：x86/ARM/RISC-V 的暫存器、calling convention、指令都不同。GDB 用 **gdbarch** 把架構差異封裝——每個架構一個 gdbarch 物件，提供「這架構怎麼讀暫存器、怎麼 unwind、斷點指令是什麼、怎麼解碼指令」。

```c
// 概念：每個架構註冊自己的 gdbarch
gdbarch->breakpoint_from_pc   // x86 回 {0xCC}；ARM Thumb 回別的
gdbarch->skip_prologue        // 怎麼跳過函式 prologue（Ch 4 break vs break*）
gdbarch->unwind               // 怎麼 unwind frame
gdbarch->register_name        // 暫存器叫什麼
```

原始碼：`gdbarch.c`（生成的）、`i386-tdep.c` / `amd64-tdep.c`（x86）、`aarch64-tdep.c`（ARM64）、`riscv-tdep.c`。`*-tdep.c`（target-dependent）是各架構的實作——這是 Ch 39 講的「INT3 是 x86 專屬、其他架構不同」的程式碼層。新增架構支援 = 寫一個 `*-tdep.c`。

## 符號與值子系統

對應你學的：

- **symtab**（`symtab.h`、`symfile.c`、`dwarf2/`）：符號表、DWARF reader（Ch 6/38）。`dwarf2/read.c` 是 DWARF 解析的核心。
- **value**（`value.h`、`value.c`）：`gdb.Value`（Ch 23）的 C++ 本體——一個值的型別、位置、內容。
- **type system**（`gdbtypes.h`）：型別（Ch 9）的內部表示，DWARF type DIE 解析成這些。
- **frame**（`frame.h`、`frame-unwind.h`）：frame 與 unwinding（Ch 10/27）。

你的 Python API（Part 5）幾乎是這些 C++ 物件的薄包裝——`gdb.Value` 包 `struct value`、`gdb.Frame` 包 `frame_info`。`python/` 目錄是 Python 綁定。

## GDB source tree 導覽

clone 並大致看結構：

```bash
git clone git://sourceware.org/git/binutils-gdb.git
cd binutils-gdb/gdb
```

```
gdb/
├── *-tdep.c          架構相關（amd64-tdep.c, aarch64-tdep.c...）— gdbarch 實作
├── *-nat.c           本機 target（linux-nat.c）— ptrace
├── remote.c          遠端 RSP（Ch 36）
├── corelow.c         core dump（Ch 33）
├── breakpoint.c      斷點管理（Ch 4,12,39）— 巨大檔案
├── infrun.c          執行控制核心（Ch 5）— "the heart"，stepping/continue 邏輯
├── dwarf2/           DWARF reader（Ch 38）
├── symtab.c          符號表（Ch 6）
├── value.c           值系統（Ch 23）
├── frame.c           frame/unwind（Ch 10）
├── python/           Python API（Part 5）— py-value.c, py-breakpoint.c...
├── tui/              TUI（Ch 18）
├── testsuite/        測試（貢獻 patch 必須附測試）
└── doc/              文件（gdb.texinfo = manual 原始碼）
```

幾個值得知道的核心檔：

- **`infrun.c`**：執行控制的心臟——`continue`/`step` 怎麼決定下一步、處理 stop reason。GDB 最複雜的檔之一（「infrun」= inferior run）。
- **`breakpoint.c`**：所有斷點/watchpoint/catchpoint 的管理，巨大。
- **`dwarf2/read.c`**：DWARF 解析（Ch 38 的實作）。
- **`python/py-*.c`**：你 Part 5 每個 Python 類別的 C++ 實作（`py-breakpoint.c` = `gdb.Breakpoint`）。

## build GDB（debug GDB 用 GDB）

```bash
cd binutils-gdb
mkdir build && cd build
../configure --with-python=python3 --enable-targets=all
make -j$(nproc) all-gdb
./gdb/gdb                          # 你編的 GDB
```

debug GDB 本身（很 meta）：

```bash
gdb ./gdb/gdb                      # 用 GDB debug GDB
(gdb) break breakpoint_re_set      # 對 GDB 的函式下斷
(gdb) run ./test-program           # 讓你的 GDB 去 debug 一個程式
```

「用 GDB debug GDB」是理解內部、debug GDB bug 的標準方式。

## 貢獻 patch

GDB 用郵件列表 + git 的傳統流程（不是 GitHub PR）：

1. **找事做**：GDB bugzilla（sourceware.org/bugzilla）的 bug、或你遇到的問題。
2. **改 + 加測試**：`testsuite/` 必須有對應測試（`gdb.base/*.exp`，用 DejaGnu/expect）。
3. **遵守規範**：GNU coding standards、ChangeLog（現在多在 commit message）、`gdb/CONTRIBUTE`。
4. **寄 patch**：`git format-patch` + 寄到 `gdb-patches@sourceware.org`（或用 `git send-email`）。
5. **review**：maintainer 在郵件列表 review，可能多輪修改。
6. **copyright assignment**：較大的貢獻可能需要 FSF copyright assignment（或 DCO，近年放寬）。

第一個貢獻建議從小 bug、文件修正、或測試開始，熟悉流程。`gdb/MAINTAINERS` 列出各部分的負責人。

## 從「改行為」到「改程式碼」的光譜

這門課教你逐步掌握 GDB 的每個層次：

```
   會用指令          (Part 1-4)        ← 大部分人停在這
        ↓
   寫命令語言腳本    (Part 4)
        ↓
   寫 Python 插件    (Part 5) ← gef/pwndbg 等級，改行為
        ↓
   懂本體原理        (Part 8, Ch 41)  ← 自寫 mini debugger
        ↓
   改 GDB 本體       (Ch 42)          ← 貢獻 upstream，改程式碼
```

你已經走完整條路。多數人只在第一層；你現在站在能寫插件、懂原理、甚至能改本體的位置。這就是「會用 → 能改」的完整旅程。

## 踩雷集錦

1. **想一次讀懂 GDB source**：百萬行，不可能。用本章的地圖，從你關心的功能切入（要改斷點看 `breakpoint.c`），別從頭讀。
2. **`infrun.c` 嚇退**：它確實複雜（執行控制的所有 corner case）。除非要改執行控制，否則先別碰。
3. **改了沒加測試**：GDB 貢獻**必須**附 testsuite 測試，否則不會被接受。
4. **用 GitHub PR**：GDB 不收 GitHub PR，用郵件列表（`gdb-patches@`）。
5. **build 慢/失敗**：GDB + binutils 是大專案，build 久；`--enable-targets=all` 更久。只 `make all-gdb`、只啟用需要的 target 加速。
6. **以為 Python API 是核心**：Python 是薄包裝，核心邏輯在 C++。改深層功能要動 C++。

## 進階：再往深一層

- **MI（Machine Interface）**：`mi/` 目錄——GDB/MI 是給 IDE（VS Code、Eclipse）用的機器可讀協定。IDE 的 GDB 後端走 MI 而非 CLI。
- **`maint` 指令**：`maint info`、`maint print`、`maint set` 一系列 maintenance 指令暴露內部狀態——debug GDB 自己、理解內部的視窗（Ch 6/9 提過 `maint print symbols/type`）。
- **gdbserver source**：`gdbserver/` 是獨立的輕量後端（Ch 36），比 GDB 本體小很多，是理解「最小 debugger 後端」的好材料。
- **testsuite 機制**：DejaGnu + expect，`.exp` 檔。寫測試本身是理解 GDB 行為的好方式。
- **GDB 的歷史包袱**：30 年的 C→C++ 漸進遷移，新舊風格混雜。讀 source 會看到歷史地層。
- **和 LLDB 對比**：LLDB（LLVM 的 debugger）架構更現代（library-first 設計，`liblldb`），是另一種 debugger 架構哲學。對比兩者很有啟發。
- **貢獻的真實體驗**：GDB 社群嚴謹但友善，郵件列表 review 仔細。第一個 patch 從文件/小 bug 開始。

## 動手練習

1. clone GDB source，用本章的導覽對照，找出 `breakpoint.c`、`infrun.c`、`dwarf2/read.c`、`python/py-breakpoint.c`。
2. 找你 Part 5 用過的一個 Python 類別（如 `gdb.Breakpoint`），在 `python/py-breakpoint.c` 找它的 C++ 實作，看 `stop()` 怎麼橋接。
3. build GDB（`configure` + `make all-gdb`），用你編的 GDB debug 一個程式確認可用。
4. 「用 GDB debug GDB」：`gdb ./gdb/gdb`，對 `infrun.c` 的某函式下斷，run 一個目標程式，看 GDB 的執行控制邏輯。
5. 逛 GDB bugzilla，找一個「good first bug」或文件問題，理解貢獻的入口。
6. 讀 `gdb/CONTRIBUTE` 與 `MAINTAINERS`，理解貢獻流程與各部分負責人。

## 本章重點整理

- GDB 分層：UI → 指令/值 → 符號/型別 → frame → gdbarch → target stack → 底層（ptrace/RSP）。
- **target stack**：可堆疊的 target_ops，讓同一套指令操作本機/core/遠端/record（Ch 1 抽象的實作）。
- **gdbarch**：架構抽象，每架構一個（`*-tdep.c`），封裝暫存器/斷點指令/unwind 差異。
- source 地圖：`infrun.c`（執行控制心臟）、`breakpoint.c`、`dwarf2/`（DWARF）、`python/`（你的 API 的 C++ 本體）。
- 貢獻走郵件列表（`gdb-patches@`，非 GitHub PR），必附 testsuite 測試。
- 完整光譜：用指令 → 命令語言 → Python 插件 → 懂原理（mini debugger）→ 改本體——你走完了全程。

## 自我檢核

- [ ] target stack 怎麼讓同一個 `continue` 操作本機/core/遠端？
- [ ] gdbarch 封裝了哪些架構差異？新增架構支援要寫什麼？
- [ ] 你的 `gdb.Breakpoint`（Python）對應哪個 C++ 檔？
- [ ] 要改斷點/執行控制/DWARF 解析，各看哪個檔？
- [ ] 貢獻 GDB patch 的流程和 GitHub 專案有什麼不同？

## 延伸閱讀

### 官方文件 / 原始碼

- **[GDB Internals (gdbint)](https://sourceware.org/gdb/wiki/Internals)** — GDB Wiki
  - **讀哪裡**：target architecture、gdbarch、了解 source 結構的入口。
  - **和本章的關聯**：本章導覽的權威擴充。

- **[GDB source tree](https://sourceware.org/git/?p=binutils-gdb.git)** 與 **`gdb/CONTRIBUTE`、`gdb/MAINTAINERS`**
  - **讀哪裡**：CONTRIBUTE（貢獻流程）、MAINTAINERS（負責人）。
  - **和本章的關聯**：實際貢獻的權威指引。

### 部落格 / 文章

- **[An overview of the GDB architecture](https://developers.redhat.com/blog/2016/11/22/gdb-internals)** 類 Red Hat 內部文
  - **這篇說什麼**：target stack、gdbarch、frame 的高層導覽。
  - **為什麼值得讀**:比 wiki 更友善的架構概覽。

### 對比

- **[LLDB Architecture](https://lldb.llvm.org/resources/architecture.html)**
  - **為什麼值得讀**:對比 LLDB 的 library-first 設計，理解 debugger 架構的不同哲學。

恭喜——你走完了從 `break main` 到讀懂 GDB 本體的全程。最後，把整門課學的 Python API 整合成你自己的作品：一套 gef 風格的插件套件。

→ [Final Project：打造你自己的 GDB 插件套件](./final-project-gdb-plugin-suite.md)
