# C 語言面試深度準備

> 給有 C 基礎、想打進系統/嵌入式/高頻/後端職位的工程師。

從記憶體模型打底，過 UB 陷阱、動態記憶體、ABI、嵌入式細節、效能技巧，最後三章是真實面試題集。每章都有「故意寫錯看輸出」的動手練習，不只講概念。

## 為什麼學這個？

- **面試考的不是 Hello World**：考官假設你會寫 C，考的是你知不知道 `i = i++` 是 UB、知不知道 `sizeof(arr)` 傳給函式後變什麼。
- **底層才是差距**：知道 `malloc` 內部如何管理 chunk、知道 stack frame 長什麼樣，能讓你和其他候選人拉開一個量級。
- **跨方向通用**：同一套底層知識，嵌入式工程師、核心開發者、高頻交易工程師都在考。

## 課程地圖

### Part 1 — 記憶體模型
- [Ch 1 記憶體分區：text / data / BSS / heap / stack](./01-memory-layout.md)
- [Ch 2 指標深度剖析：運算、const、restrict](./02-pointers-deep.md)
- [Ch 3 陣列與指標的真正關係](./03-arrays-vs-pointers.md)
- [Ch 4 struct / union / bitfield 記憶體佈局](./04-struct-union-bitfield.md)
- [Ch 5 Alignment 與 Padding](./05-alignment-padding.md)

### Part 2 — 未定義行為與型別陷阱
- [Ch 6 未定義行為（UB）全圖](./06-undefined-behavior.md)
- [Ch 7 型別轉換與 Type Punning：嚴格別名規則](./07-type-punning-aliasing.md)
- [Ch 8 字串陷阱](./08-string-traps.md)
- [Ch 9 整數系統：有號/無號/提升/轉換](./09-integer-arithmetic.md)
- [Ch 10 volatile、Sequence Point、記憶體順序](./10-volatile-sequence-point.md)
- [練習 A：UB 偵錯題集](./practice-a-ub-debug.md)

### Part 3 — 動態記憶體管理
- [Ch 11 malloc / calloc / realloc / free 內部機制](./11-malloc-internals.md)
- [Ch 12 記憶體錯誤完整圖鑑](./12-memory-errors.md)
- [Ch 13 自製 Memory Pool：arena allocator](./13-memory-pool.md)
- [Ch 14 Valgrind 與 AddressSanitizer 實戰](./14-asan-valgrind.md)

### Part 4 — 函式、呼叫慣例與 ABI
- [Ch 15 函式指標、callback、C 模擬 vtable](./15-function-pointers.md)
- [Ch 16 x86-64 System V ABI：暫存器、stack frame、紅區](./16-calling-convention-abi.md)
- [Ch 17 可變引數（stdarg）與 printf 實作](./17-variadic-printf.md)
- [Ch 18 編譯器優化與 inline / restrict](./18-compiler-optimization.md)

### Part 5 — 底層系統
- [Ch 19 前置處理器陷阱：macro、X-macro、include guard](./19-preprocessor-traps.md)
- [Ch 20 連結器：linkage、符號可見性、weak symbol](./20-linker-symbols.md)
- [Ch 21 嵌入式 C：volatile、interrupt、DMA](./21-embedded-c.md)
- [Ch 22 C11 並行：pthread、mutex、\_Atomic、memory_order](./22-c11-concurrency.md)
- [Ch 23 signal、setjmp / longjmp](./23-signal-setjmp.md)
- [練習 B：系統程式設計實作題](./practice-b-systems-impl.md)

### Part 6 — 效能與硬體
- [Ch 24 Cache 友善設計：false sharing、loop tiling](./24-cache-friendly.md)
- [Ch 25 SIMD 入門：SSE/AVX intrinsics](./25-simd-vectorization.md)
- [Ch 26 Branch Prediction 與無分支技巧](./26-branchless.md)
- [Ch 27 Lock-free：CAS、memory fence、ABA](./27-lock-free.md)

### Part 7 — 真實面試題
- [Ch 28 經典筆試陷阱 40 道（含詳解）](./28-classic-traps-40.md)
- [Ch 29 實作類面試題：linked list / stack / ring buffer](./29-impl-interview-questions.md)
- [Ch 30 系統設計類 C 題：malloc / shell / state machine](./30-system-design-c.md)
- [練習 C：模擬面試 30 題](./practice-c-mock-interview.md)
- [Final Project：從零實作 mini libc](./final-project-mini-libc.md)

## 學習方式建議

1. **每章必須動手跑**：特別是 UB 章節，在 `-O0` 和 `-O2` 下分別跑，親眼看編譯器怎麼「優化掉」你的程式。
2. **面試模式讀 Ch 28**：先自己解答，再看詳解，和面試一樣限時。
3. **工具鏈**：`gcc -Wall -Wextra -fsanitize=address,undefined` 是你最好的老師。

## 環境

```bash
gcc --version    # GCC 11+ 或 Clang 14+
valgrind --version
```

## 參考資料

- 《C Programming Language》— Kernighan & Ritchie（必讀）
- 《Expert C Programming》— Peter van der Linden（陷阱集）
- cppreference.com — 最好用的線上 C/C++ 參考
- C17 標準草案：ISO/IEC 9899:2017
