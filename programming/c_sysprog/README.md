# 你所不知道的 C 語言（深化版）：從規格書、組合語言到 Linux 核心

> 給會寫 C 語法、但說不清背後原理的工程師。

以 jserv 的「[你所不知道的 C 語言](https://hackmd.io/@sysprog/c-programming)」系列為主軸，把 21 篇講座拆成 44 章，對每個主題從直覺講起，再對照 ISO/IEC 9899 規格書、gcc 產生的組合語言、GDB 觀察到的記憶體，以及 Linux 核心與開源專案的真實程式碼。學完後你能做到三件事：遇到疑問先查規格書而非臆測、用工具驗證程式的實際行為、讀得懂核心等級的 C 程式。

## 為什麼學這個？

- **C 不是只有語法**：同一行 `a[i] = i++;`，語法上合法，規格書說它是未定義行為。只學語法的人看不出差別，這門課要補的就是這一層。
- **C 是電腦系統的共同語言**：作業系統、編譯器、資料庫、嵌入式韌體、各種語言的 runtime 都以 C 寫成或以 C ABI 互通。懂 C 的底層，等於拿到讀懂整個系統堆疊的鑰匙。
- **資安與面試都在考這一層**：buffer overflow、integer overflow、UAF 的成因都在 C 的語意裡；嵌入式與系統職缺的考題（`volatile`、`static`、位元操作、ABI）也全在這裡。

## 先修知識

- 寫過 C 的基本語法：變數、迴圈、函式、陣列（程度：能寫出幾十行可以編譯的程式）
- 會在 Linux shell 下下指令、編輯檔案
- 指標還不熟沒關係：Part 0 會從頭重建
- 沒有也沒關係的：組合語言、作業系統、編譯器原理（用到時會在章節內補）

## 環境

WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14、gdb 12.1。需要 C23 新特徵（gcc 13 以上）的段落以 Docker `gcc:14`/`gcc:15` 映像執行（要從 Windows 端 Git Bash 執行 `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W)":/src -w /src gcc:14 ...`，從 WSL 內掛載會看不到檔案，見 [Ch 0](./00-environment-setup.md)），章節內會標明。原文快照放在 [`sources/`](./sources/README.md)。

## 課程地圖

### Part 0 — 打底（Ch 0–3）
- [Ch 0 環境建置：gcc、clang、gdb 與 sanitizer](./00-environment-setup.md)
- [Ch 1 一支 C 程式的一生：從原始碼到行程](./01-life-of-a-c-program.md)
- [Ch 2 型別與物件：整數型別、sizeof、陣列與 struct](./02-types-and-objects.md)
- [Ch 3 指標入門重建：位址、解參考與指標算術](./03-pointer-basics.md)

### Part 1 — 工具與脈絡（Ch 4–6，原文：開發工具和規格標準篇、起源與演化篇）
- [Ch 4 讀規格書：ISO/IEC 9899 與 C89 到 C23 的演進](./04-reading-the-standard.md)
- [Ch 5 GDB 實戰：不靠 printf 觀察程式狀態](./05-gdb-in-practice.md)
- [Ch 6 C 的起源與演化：從 BCPL、B 到 PDP-11](./06-history-of-c.md)

### Part 2 — 指標與函式（Ch 7–14，原文：指標篇、函式呼叫篇）
- [Ch 7 規格書裡的指標：object、type、incomplete type 與 lvalue](./07-pointers-by-the-standard.md)
- [Ch 8 陣列、函式、指標：derived declarator 與 array decay](./08-arrays-functions-pointers.md)
- [Ch 9 void *、指標的指標、function pointer 與 qualifier](./09-void-pointer-and-callbacks.md)
- [Ch 10 字串、null pointer、nullptr 與記憶體安全](./10-strings-and-null.md)
- [Ch 11 行程記憶體布局、x86-64 SysV ABI 與 stack frame](./11-process-layout-and-abi.md)
- [Ch 12 setjmp/longjmp 與協同式多工](./12-setjmp-and-coroutines.md)
- [Ch 13 遞迴與尾端遞迴最佳化](./13-recursion.md)
- [Ch 14 函式呼叫與資安：buffer overflow、canary 與 ROP](./14-function-call-security.md)
- [練習 A：泛型容器與 callback 工具箱](./practice-a-generic-container.md)

### Part 3 — 記憶體與資料結構（Ch 15–20，原文：記憶體管理篇、鏈結串列篇；Ch 20 取材自記憶體管理篇的 concurrent-ll 案例）
- [Ch 15 記憶體階層與虛擬記憶體](./15-memory-hierarchy.md)
- [Ch 16 data alignment、padding 與 _Alignas](./16-alignment-and-padding.md)
- [Ch 17 malloc/free 實作：glibc ptmalloc 與其他配置器](./17-malloc-internals.md)
- [Ch 18 鏈結串列與指標的指標](./18-linked-list-pointer-to-pointer.md)
- [Ch 19 Linux list_head、container_of、list_sort 與洗牌](./19-linux-list-head.md)
- [Ch 20 non-blocking 鏈結串列：CAS 與 C11 atomics](./20-lock-free-linked-list.md)
- [練習 B：自製 malloc](./practice-b-my-malloc.md)

### Part 4 — 數值與位元（Ch 21–26，原文：數值系統篇、浮點數運算篇、bitwise 操作篇）
- [Ch 21 數值系統：進位制、二補數與同餘](./21-number-systems.md)
- [Ch 22 integer overflow：語意、資安案例與防禦](./22-integer-overflow.md)
- [Ch 23 bitwise 基礎：位移語意與位元操作](./23-bitwise-basics.md)
- [Ch 24 bitwise 技巧：clz、popcount、無分支與 Morton 碼](./24-bitwise-tricks.md)
- [Ch 25 IEEE 754：格式、次正規數、特殊值與捨入](./25-ieee754.md)
- [Ch 26 浮點誤差與補救：Kahan、定點數與低精度格式](./26-floating-point-errors.md)
- [練習 C：定點數運算庫與浮點解碼器](./practice-c-fixed-point.md)

### Part 5 — 語言機制與設計手法（Ch 27–35，原文：前置處理器、物件導向、goto、技巧、未定義行為、Stream I/O 篇）
- [Ch 27 前置處理器 I：巨集陷阱、_Generic 與 statement expression](./27-preprocessor-basics.md)
- [Ch 28 前置處理器 II：X macro、元程式設計與 C23 新特徵](./28-preprocessor-advanced.md)
- [Ch 29 物件導向 C：封裝、vtable 與繼承](./29-oop-in-c.md)
- [Ch 30 Linux 核心與開源專案的物件模型](./30-object-models-in-the-wild.md)
- [Ch 31 goto 與流程控制：清理鏈、跳躍表與 computed goto](./31-goto-and-control-flow.md)
- [Ch 32 技巧 I：語法與資源管理](./32-tricks-syntax-and-resources.md)
- [Ch 33 技巧 II：指標手法與效能實測](./33-tricks-pointers-and-performance.md)
- [Ch 34 未定義行為：分類、編譯器推論與偵測](./34-undefined-behavior.md)
- [Ch 35 Stream I/O、EOF 與 signal](./35-stream-io-and-signals.md)
- [練習 D：迷你 unit test 框架](./practice-d-unit-test-framework.md)

### Part 6 — 從原始碼到執行（Ch 36–41，原文：編譯器兩篇、連結器、動態連結器、執行階段程式庫篇）
- [Ch 36 GNU toolchain 與編譯流程](./36-gnu-toolchain.md)
- [Ch 37 編譯器最佳化原理：SSA 與常見最佳化](./37-compiler-optimization.md)
- [Ch 38 編譯器實作：Brainfuck、c4 與 shecc](./38-compiler-construction.md)
- [Ch 39 靜態連結：符號解析、重定位與程式庫](./39-static-linking.md)
- [Ch 40 動態連結：PLT/GOT、LD_PRELOAD 與 dlopen](./40-dynamic-linking.md)
- [Ch 41 執行階段：從 execve 經 _start 到 main](./41-c-runtime.md)
- [練習 E：LD_PRELOAD malloc 追蹤器與 Brainfuck JIT 迴圈最佳化](./practice-e-malloc-tracer-and-bf-jit.md)

### Part 7 — 綜合（Ch 42–43，原文：深究 0x10 道嵌入式系統考題篇）
- [Ch 42 0x10 道嵌入式考題 I（0x01–0x08）](./42-embedded-quiz-part1.md)
- [Ch 43 0x10 道嵌入式考題 II（0x09–0x10）](./43-embedded-quiz-part2.md)
- [Final Project：迷你 stack VM 直譯器](./final-project-stack-vm.md)

## 學習方式建議

1. **每個範例都親手編譯**：用 `gcc -Wall -Wextra -g` 編，再開 `-fsanitize=address,undefined` 跑一次。工具會比你先發現問題。
2. **故意把它弄壞**：把 `<=` 改成 `<`、把 `free` 多呼叫一次，看編譯器、sanitizer、GDB 各自怎麼反應。
3. **有疑問先查規格書**：章節會標出 C11/C17 草案（N1570、N2176）的條款編號，養成「翻到那一段讀原文」的習慣。
4. **看組合語言**：`gcc -S -O2` 或 [Compiler Explorer](https://godbolt.org/)。C 語言最後都變成機械碼，看過一次比讀十段說明有用。
5. **對照原文**：每章開頭標示對應的原文講座，讀完本章再回去看原文，會看懂更多。

## 精選資料庫

### 必讀基礎

- **[ISO/IEC 9899 草案 N1570（C11）](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)、[N3220（C23 後的工作草案）](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n3220.pdf)**
  - 規格書是最終仲裁。本課引用條款以 N1570 為主，C23 新特徵引用 N3220。
- **《Computer Systems: A Programmer's Perspective》（CSAPP，3rd ed.）** — Bryant & O'Hallaron（Pearson，2015）
  - 第 2 章（數值）、第 3 章（機械碼）、第 7 章（連結）、第 9 章（虛擬記憶體）幾乎是 Part 2、4、6 的教科書版本。
- **《The C Programming Language》（K&R，2nd ed.）** — Kernighan & Ritchie（1988）
  - 語言設計者寫的原典；版本老舊（C89），但第 5 章指標仍是經典。

### 推薦論文

- **[The Development of the C Language](https://www.bell-labs.com/usr/dmr/www/chist.html)** — Dennis Ritchie，HOPL-II（1993）
  - C 設計者親述語言的來源；Ch 6 的主要一手資料。
- **[Towards Optimization-Safe Systems: Analyzing the Impact of Undefined Behavior](https://dl.acm.org/doi/10.1145/2517349.2522728)** — Wang et al.，SOSP（2013）
  - 量化編譯器利用 UB 刪掉安全檢查的真實案例；Ch 22、Ch 34 的核心參考。
- **[Return-Oriented Programming: Systems, Languages, and Applications](https://dl.acm.org/doi/10.1145/2133375.2133377)** — Roemer, Buchanan, Shacham, Savage，ACM TISSEC（2012）
  - ROP 的系統性論述；Ch 14 的延伸。

### 推薦部落格 / 文章

- **[What Every C Programmer Should Know About Undefined Behavior](https://blog.llvm.org/2011/05/what-every-c-programmer-should-know.html)** — Chris Lattner（LLVM Blog，2011）
  - LLVM 作者從編譯器角度解釋 UB 為什麼存在、編譯器怎麼利用它。
- **[Eli Bendersky 的部落格](https://eli.thegreenplace.net/)** — Eli Bendersky
  - 連結器、PIC、x86-64 stack frame、computed goto 都有寫得最清楚的一篇。
- **[jserv 的 sysprog 課程筆記](https://hackmd.io/@sysprog)** — 黃敬群
  - 本課主軸的作者；Linux 核心設計、CS:APP 導讀等系列可接續本課。

### 讀完本課之後

- **《Linux Kernel Development》** — Robert Love（把 Ch 19、30 的核心技巧放回完整核心架構中）
- **《Linkers and Loaders》** — John Levine（把 Part 6 推到可以自己寫連結器的程度）
- **[Linux 核心設計講座](https://hackmd.io/@sysprog/linux-kernel-internal)**（jserv 系列的下一站）
