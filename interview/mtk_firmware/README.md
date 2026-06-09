# MTK 韌體工程師面試衝刺：一週把 C／嵌入式／OS／計組／資料結構掃完

> 給一週後要面聯發科（MediaTek）韌體／軟韌體工程師、需要快速但扎實複習的人。

把這個職位面試會考的五大塊——**C 語言（含上機考）、嵌入式/韌體、作業系統、計算機組織、資料結構**——用「精簡概念複習 + 大量考古題詳解」一次掃完。每章先快速喚回核心觀念，再用真實的 MTK 風格考古題逼你應用。學完你能在面試現場對這些題目反射性作答。

考點分布依這個職位調權重：**C 與嵌入式最重（線上 C 上機考 + 韌體技術面），OS 次之，計組／DS 適中。**

學完你應該能：

- 反射性答出 C 經典考題：volatile/const/static、複雜宣告、bit operation、巨集陷阱、記憶體對齊、整數轉型
- 處理韌體專屬題：存取固定記憶體位址、ISR 設計、reentrancy、endianness、ARM 基礎、RTOS
- 講清楚 OS 核心：process/thread、deadlock、同步、scheduling、virtual memory、IPC
- 答出計組：cache、pipeline、編譯連結流程、補數/浮點、DMA
- 手寫資料結構題：linked list 反轉/找環、tree traversal、sorting
- 走完一場完整模擬面試（上機 C + 技術問答 + 行為）

## 為什麼用這本書衝刺？

- **時間有限、考點集中**：MTK 韌體面試的題目高度可預測（社群考古題大量重複）。這本書把高頻考點濃縮，不浪費你一週裡的每一小時。
- **概念+考古題雙軌**：只背題目，換個問法就答不出；只讀概念，臨場反應不夠快。本書每章「先複習觀念、再用考古題驗證」，兩者一起練。
- **韌體職位專屬**：一般 CS 面試書不會專講 ISR、memory-mapped I/O、reentrancy、endian——但這些正是 MTK 韌體面試的核心。本書獨立一個 Part 講它們。
- **上機考導向**：MTK 有線上 C 上機考。Part 1 + 練習 A 直接針對它，給你「拿到題目怎麼下手」的手感。

## 先修知識

- C 語言（程度：寫過、懂指標/struct/malloc，本書是**複習**不是從零教）
- 基本 CS 背景（程度：修過或自學過 OS／計組／資料結構，至少聽過這些名詞）
- 沒有也沒關係的：ARM 組語細節、RTOS 實作經驗（本書會補夠面試用的程度）

> 認識論誠實：本書是**面試複習**用的濃縮教材，不是各科的完整教科書。每章會給「想更深入去哪」的延伸閱讀，但目標是「一週內讓你面試能答」，不是「精通該科」。考古題來自社群分享（見下方來源），題型與重點可能隨年份微調——掌握**觀念**比背特定題目重要。

## 課程地圖

### Part 0 — 起手（Ch 0）
- [Ch 0 MTK 韌體面試全貌 + 一週讀書計畫](./00-interview-overview.md)

### Part 1 — C 語言核心（Ch 1–12）★最重，上機考
- [Ch 1 變數、儲存類別與作用域](./01-storage-classes-scope.md)
- [Ch 2 static 全解](./02-static.md)
- [Ch 3 const 與 volatile](./03-const-volatile.md)
- [Ch 4 指標基礎與陣列](./04-pointers-arrays.md)
- [Ch 5 複雜宣告與函式指標](./05-complex-declarations-function-pointers.md)
- [Ch 6 前置處理器與巨集](./06-preprocessor-macros.md)
- [Ch 7 位元運算](./07-bit-manipulation.md)
- [Ch 8 struct/union/enum 與記憶體對齊](./08-struct-union-alignment.md)
- [Ch 9 型別轉換與整數陷阱](./09-type-conversion-integer.md)
- [Ch 10 運算子優先序與表達式](./10-operator-precedence-expressions.md)
- [Ch 11 記憶體模型 stack/heap/static/text](./11-memory-model.md)
- [Ch 12 字串與標準函式](./12-strings-stdlib.md)
- [練習 A：C 上機考模擬](./practice-a-c-coding-test.md)

### Part 2 — 嵌入式/韌體專屬（Ch 13–19）
- [Ch 13 存取固定記憶體位址與 memory-mapped I/O](./13-memory-mapped-io.md)
- [Ch 14 中斷與 ISR](./14-interrupts-isr.md)
- [Ch 15 reentrancy 與 thread-safe](./15-reentrancy-thread-safe.md)
- [Ch 16 endianness](./16-endianness.md)
- [Ch 17 ARM 與處理器基礎](./17-arm-processor-basics.md)
- [Ch 18 RTOS 概念](./18-rtos-concepts.md)
- [Ch 19 低功耗、debug 與韌體開發實務](./19-firmware-practice.md)
- [練習 B：嵌入式情境題](./practice-b-embedded-scenario.md)

### Part 3 — 作業系統（Ch 20–28）
- [Ch 20 process vs thread](./20-process-vs-thread.md)
- [Ch 21 process 排程](./21-scheduling.md)
- [Ch 22 同步：mutex/semaphore/critical section](./22-synchronization.md)
- [Ch 23 deadlock](./23-deadlock.md)
- [Ch 24 經典同步問題](./24-classic-sync-problems.md)
- [Ch 25 記憶體管理 paging/segmentation](./25-memory-management.md)
- [Ch 26 virtual memory 與置換](./26-virtual-memory.md)
- [Ch 27 IPC](./27-ipc.md)
- [Ch 28 system call、user/kernel mode](./28-syscall-kernel-mode.md)
- [練習 C：OS 綜合考古題](./practice-c-os-questions.md)

### Part 4 — 計算機組織（Ch 29–35）
- [Ch 29 數字表示與運算](./29-number-representation.md)
- [Ch 30 記憶體階層與 cache](./30-cache-memory-hierarchy.md)
- [Ch 31 CPU pipeline](./31-cpu-pipeline.md)
- [Ch 32 指令集與組語基礎](./32-isa-assembly.md)
- [Ch 33 編譯/組譯/連結/載入](./33-compile-link-load.md)
- [Ch 34 I/O 與 DMA、匯流排](./34-io-dma-bus.md)
- [Ch 35 並行硬體基礎](./35-concurrency-hardware.md)
- [練習 D：計組綜合（cache + pipeline）](./practice-d-comporg-questions.md)

### Part 5 — 資料結構與演算法（Ch 36–41）
- [Ch 36 array / linked list](./36-array-linked-list.md)
- [Ch 37 stack / queue](./37-stack-queue.md)
- [Ch 38 tree / BST / heap](./38-tree-bst-heap.md)
- [Ch 39 hash table](./39-hash-table.md)
- [Ch 40 sorting](./40-sorting.md)
- [Ch 41 搜尋、圖與複雜度](./41-search-graph-complexity.md)
- [練習 E：DS/演算法手寫題](./practice-e-ds-handwriting.md)

### Part 6 — 整合衝刺（Ch 42–43）
- [Ch 42 行為面試與非技術](./42-behavioral-interview.md)
- [Ch 43 一週衝刺計畫 + 速查表](./43-cheat-sheet-week-plan.md)
- [Final：模擬面試](./final-project-mock-interview.md)

## 學習方式建議

1. **先看 Ch 0 排計畫**：一週時間有限，Ch 0 給你「哪些必讀、哪些選讀」的優先序——別線性讀。
2. **每題先自己答再看詳解**：考古題的價值在「逼你回憶」。看到題目先遮住答案自己想，卡住再看——這比直接讀詳解有效十倍。
3. **手寫 C 題**：上機考要在電腦上寫出來。練習 A 的題目親手打、編譯、跑——別只在腦中想。
4. **考前一晚讀 Ch 43 速查表**：把每科最高頻考點濃縮，當臨門一腳的複習。

## 精選資料庫

整門課最值得參照的資源；每章「考古題詳解」會標明題目來源。

### 必讀基礎（教科書，當查詢用，不是一週讀完）

- **《Computer Systems: A Programmer's Perspective (CSAPP)》** — Bryant & O'Hallaron
  - 計組 + 系統的聖經；本書計組（Part 4）與記憶體（Ch 11）的權威來源。面試前查特定主題即可。
- **《Operating System Concepts (恐龍書)》** — Silberschatz 等
  - OS 的標準教科書；Part 3 的概念以它為準。查 scheduling/deadlock/virtual memory 章節。
- **《Operating Systems: Three Easy Pieces (OSTEP)》** — Remzi（[免費線上](https://pages.cs.wisc.edu/~remzi/OSTEP/)）
  - 比恐龍書好讀的 OS 教材；Part 3 想快速複習觀念時讀它對應章節。

### 考古題來源（本書題目主要參考）

- **[韌體工程師的0x10個問題 — HackMD](https://hackmd.io/@Chienyu/S1loEqCuo)**
  - Nigel Jones 經典嵌入式 C 面試 16 題的中文整理；Part 1/2 的骨幹考點（volatile/const/static/bit/ISR/固定位址）。
- **[發哥(聯發科)上機考題目整理 — HackMD](https://hackmd.io/@Rance/SkSJL_5gX)**
  - MTK 上機 C 考題彙整；練習 A 的題型來源。
- **[面試紀錄 & 練習（聯發科）— HackMD](https://hackmd.io/@chiangkd/interview)**
  - 完整的 MTK 面試準備筆記（C/OS/計組/DS 全涵蓋）。
- **[聯發科 C語言測試題目 — Jaime Lin (Medium)](https://jaime-lin.medium.com/%E8%81%AF%E7%99%BC%E7%A7%91-c%E8%AA%9E%E8%A8%80%E6%B8%AC%E8%A9%A6%E9%A1%8C%E7%9B%AE-7097f09add02)**
  - MTK C 測驗實題與解析。

### 推薦複習文章

- **[常見 C 語言觀念題目總整理 — Mr. Opengate](https://www.mropengate.com/2017/08/cc-c.html)**
  - C/C++ 面試觀念題大全；Part 1 的補充題庫。
- **[MediaTek Interview Experience — GeeksforGeeks](https://www.geeksforgeeks.org/interview-experiences/mediatek-interview-experience-on-campus/)**
  - MTK 面試流程與題型（英文，含 OS/計組/DS 角度）。

## 環境

- C 編譯器：`gcc`（任何近代版本都行；上機考通常標準 C）。本書 C 範例都能 `gcc -Wall` 編譯驗證。
- 一台能跑 gcc 的機器（Linux / WSL / macOS 皆可），練習 A 親手打題用。

Ch 0 會給你一週的具體讀書計畫。
