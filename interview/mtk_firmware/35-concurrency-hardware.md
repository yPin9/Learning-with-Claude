# Ch 35 — 並行硬體基礎

> **目標**：搞懂多核時代的硬體並行——multicore、cache coherence（快取一致性）、memory ordering 與 memory barrier、atomic 操作。這把 volatile（Ch 3）、同步（Ch 22）、cache（Ch 30）串成「為什麼多核並行這麼難」的完整圖像。

> **環境**：概念為主，多核 CPU。前置：Ch 3（volatile）、Ch 22（同步）、Ch 30（cache）。

## 為什麼考這個

現代 CPU 都是多核——多個核同時跑、各有自己的 cache。這帶來「一個核改的資料，另一個核看不看得到」「指令會被重排」等硬體層的並行問題。這串連 volatile（Ch 3）、mutex/atomic（Ch 22）的底層——為什麼 volatile 不夠、為什麼需要 memory barrier。進階但展現深度的考點。

## 先建立直覺：多核各有 cache，看到的世界不一致

```
   多核系統：
   Core 0 [L1 cache]  ┐
   Core 1 [L1 cache]  ├─ 共享 L3 / 主記憶體
   Core 2 [L1 cache]  ┘

   問題：core 0 把變數 x 改成 5（在 core 0 的 cache 裡）
        core 1 的 cache 還是舊的 x → core 1 讀到舊值！

   → 多核「各看各的 cache」，需要機制讓它們的 cache 保持一致（coherence）
```

核心：**多核各有私有 cache（Ch 30），同一個變數在不同核的 cache 可能不一致。** 加上「編譯器/CPU 會重排指令」——多核並行的正確性比單核難得多。這是 Ch 22 同步、Ch 3 volatile 背後的硬體現實。

## cache coherence（快取一致性）

確保「多核的私有 cache 對同一記憶體位置看到一致的值」的機制。硬體（cache coherence protocol，如 **MESI**）自動做：

```
   MESI protocol：每條 cache line 有四種狀態
   - Modified（M）：本核改過、和記憶體不一致、其他核沒有
   - Exclusive（E）：只有本核有、和記憶體一致
   - Shared（S）：多核都有、和記憶體一致
   - Invalid（I）：無效（別的核改了，本核這份作廢）

   運作：core 0 要改 x → 通知其他核「把你們的 x 設成 Invalid」→ 其他核下次讀 x
        會發現 Invalid → 重新拿最新的 → 一致
```

關鍵：**cache coherence 是硬體自動保證的**（程式設計師不用手動管多核 cache 一致）。MESI 等協定讓「一個核改了，其他核會看到」（透過 invalidate/更新）。

> 注意：cache coherence（多核 cache 一致，硬體自動）和 DMA cache 一致性（Ch 34，要軟體 flush/invalidate）不同！DMA 不是 cache coherence 協定的參與者（它直接讀記憶體），所以 DMA 要軟體手動維護；多核之間有 MESI 自動維護。別搞混。

## memory ordering 與 memory barrier

即使有 cache coherence，還有一個問題：**編譯器和 CPU 會「重排」記憶體存取的順序**（為了效能），導致多核看到的「順序」不一致。

```
   你寫的：              CPU/編譯器可能重排成：
   x = 1;               flag = 1;   ← 順序顛倒了！
   flag = 1;            x = 1;

   單核沒問題（結果一樣）。但多核：
   Core 0: x = 1; flag = 1;        // 想表達「資料 x 準備好了，設 flag 通知」
   Core 1: while(!flag); read x;   // 等 flag，然後讀 x

   若 core 0 的寫被重排（flag 先寫）→ core 1 看到 flag=1 就讀 x，但 x 還沒寫！→ 讀到舊 x
```

問題：**指令重排在單核安全（結果一致），但多核下會讓「順序假設」失效。** 編譯器重排（compiler reordering）+ CPU 亂序執行（out-of-order）+ store buffer 都會造成。

解法：**memory barrier（記憶體屏障 / fence）**——強制「屏障前的記憶體操作，一定在屏障後的之前完成（對其他核可見）」：

```c
   x = 1;
   memory_barrier();     // 屏障：保證 x=1 在 flag=1 之前對其他核可見
   flag = 1;
```

barrier 阻止重排跨越它。這是 mutex/atomic 底層的關鍵——它們內含 barrier，保證臨界區的記憶體操作順序正確。

## volatile vs atomic vs barrier（串 Ch 3/22，最常混）

這三個常被混淆，但解決不同層次的問題：

```
   volatile（Ch 3）：
   - 保證「每次存取都真的讀寫記憶體」（不被編譯器快取進暫存器/最佳化掉）
   - 不保證原子性、不保證多核可見性、不防 CPU 重排
   - 用途：硬體暫存器、ISR 共享變數（單核）——不是多核同步工具！

   atomic（如 C11 _Atomic / __atomic）：
   - 保證操作原子（read-modify-write 不被打斷，如 atomic 的 count++）
   - 通常也含 memory ordering 保證（barrier）
   - 用途：多核/多執行緒的無鎖同步、計數器

   memory barrier：
   - 保證記憶體操作的順序（防重排）
   - 用途：確保多核看到正確的順序
```

關鍵釐清（高頻陷阱）：
- **volatile ≠ 多執行緒同步！** volatile 只防編譯器最佳化掉讀寫，**不提供原子性、不防 CPU 重排、不保證多核可見性**。多核同步要用 **atomic / mutex**（它們含 barrier + 原子性）。
- Java/C# 的 volatile 有記憶體屏障語意（不同於 C/C++）——別混。**C/C++ 的 volatile 不是同步工具。**
- 在多核多執行緒，要原子計數用 atomic、要互斥用 mutex（Ch 22），**不是 volatile**。

這解釋了 Ch 3 一直強調的「volatile ≠ thread-safe」——底層就是：volatile 不管原子性和記憶體順序，那是 atomic/barrier 的事。

## 考古題詳解

### Q1：什麼是 cache coherence？怎麼保證？

<details>
<summary>詳解</summary>

cache coherence：確保多核的私有 cache 對同一記憶體位置看到一致的值。

問題：多核各有 cache，core 0 改了 x（在自己 cache），core 1 的 cache 還是舊的 → 不一致。

保證：硬體的 cache coherence protocol（如 **MESI**）自動處理——core 0 要改 x 就通知其他核 invalidate（作廢）它們的 x，其他核下次讀重新拿最新的。**硬體自動，程式設計師不用手動管。**

（注意：和 DMA cache 一致性 Ch 34 不同——DMA 要軟體 flush/invalidate。）

**考點**：cache coherence + MESI，進階題。
</details>

### Q2：為什麼多核需要 memory barrier？

<details>
<summary>詳解</summary>

因為編譯器和 CPU 會**重排**記憶體存取（為效能）。單核重排不影響結果，但多核下會讓「順序假設」失效——例如「先寫資料 x、再設 flag 通知」，若被重排成「先設 flag」，另一個核可能看到 flag 卻讀到還沒寫的 x。

memory barrier 強制「屏障前的操作在屏障後之前對其他核可見」，阻止重排跨越它，保證順序正確。mutex/atomic 底層含 barrier。

**考點**：memory barrier + 重排問題，進階。
</details>

### Q3：volatile、atomic、memory barrier 差在哪？volatile 能用於多核同步嗎？

<details>
<summary>詳解</summary>

- **volatile**：保證每次存取真的讀寫記憶體（不被最佳化掉）。**不**保證原子性、**不**防重排、**不**保證多核可見性。
- **atomic**：保證操作原子（RMW 不被打斷）+ 通常含 memory ordering（barrier）。
- **memory barrier**：保證記憶體操作順序（防重排）。

**volatile 不能用於多核同步！** 它只防編譯器最佳化掉讀寫，不提供原子性/順序/可見性。多核同步用 atomic / mutex（含 barrier + 原子性）。這是 Ch 3「volatile ≠ thread-safe」的底層原因。

（註：Java/C# volatile 有屏障語意，C/C++ 沒有，別混。）

**考點**：volatile vs atomic vs barrier，高頻陷阱（答對展現深度）。
</details>

### Q4：cache coherence 和 DMA 的 cache 一致性一樣嗎？

<details>
<summary>詳解</summary>

**不一樣。**
- **多核 cache coherence**：硬體（MESI 等）**自動**保證多核 cache 一致——程式不用管。
- **DMA cache 一致性**（Ch 34）：DMA 直接讀寫記憶體，**不參與** cache coherence 協定 → 要**軟體手動** flush/invalidate cache。

差別：多核之間硬體自動；DMA 要軟體手動（因為 DMA 不是 coherence 協定的成員）。

**考點**：兩種 cache 一致性的區別，串 Ch 34，易混。
</details>

### Q5：多核下 `volatile int count; count++` 安全嗎？

<details>
<summary>詳解</summary>

**不安全。** 兩個問題：
1. `count++` 是 read-modify-write（非原子）→ 多核同時做會 race（Ch 22）。volatile 不提供原子性。
2. volatile 不防重排、不保證多核記憶體順序。

正確：用 **atomic**（`atomic_fetch_add` / C11 `_Atomic`）保證原子 + 順序，或用 mutex 保護。volatile 在這完全不夠。

**考點**：volatile 不能解多核 race，串 Ch 3/22。
</details>

## 踩雷集錦

1. **以為 volatile 能做多核同步**：volatile 只防最佳化掉讀寫，**不給原子性/順序/可見性**。多核用 atomic/mutex。（C/C++ 的 volatile，別跟 Java 混。）
2. **以為多核 cache 一致要軟體管**：硬體（MESI）自動。但 **DMA** 的一致性要軟體（Ch 34）——兩者不同。
3. **忽略指令重排**：單核安全的順序假設，多核會被重排破壞。要 barrier。
4. **atomic 和 volatile 混用以為一樣**：atomic 有原子性+順序；volatile 都沒有。
5. **以為加 volatile 就 thread-safe**：不是（Ch 3 一直強調）。底層是 volatile 不管原子/順序。
6. **混淆 cache coherence（多核 cache 一致）和 memory ordering（操作順序）**：coherence 是「值一致」、ordering 是「順序一致」——是兩個不同的多核問題。

## 速記

- 多核各有私有 cache → 同變數可能不一致 → **cache coherence**（MESI 等硬體協定**自動**保證多核 cache 一致）。
- 編譯器/CPU **重排**記憶體操作（單核安全、多核破壞順序假設）→ **memory barrier** 強制順序（防重排跨越）。mutex/atomic 含 barrier。
- **volatile**（每次真讀寫，不給原子/順序/可見性，**不是多核同步工具**）vs **atomic**（原子+順序）vs **barrier**（順序）。
- **volatile ≠ thread-safe**（Ch 3 的底層原因）；多核同步用 **atomic/mutex**。
- 多核 cache coherence（硬體自動）≠ DMA cache 一致性（Ch 34，軟體 flush/invalidate）。

## 自我檢核

- [ ] cache coherence 是什麼？硬體怎麼保證（MESI 大意）？
- [ ] 為什麼多核需要 memory barrier？指令重排怎麼造成問題？
- [ ] volatile、atomic、memory barrier 各保證什麼？volatile 能多核同步嗎？
- [ ] 多核 cache coherence 和 DMA cache 一致性差在哪？
- [ ] 多核下 `volatile int count; count++` 為什麼不安全？該用什麼？

## 延伸閱讀

### 書籍

- **《Computer Systems: A Programmer's Perspective (CSAPP)》** — Ch 12 Concurrent Programming
  - **讀哪幾章**：12.5–12.6（共享變數、同步、memory model 概念）。
  - **和本章的關聯**：並行的權威，連 volatile/atomic 一起。

### 文章

- **[Memory Barriers: a Hardware View for Software Hackers](https://www.cs.columbia.edu/~junfeng/12fa-cs5118/papers/whymb.pdf)** — Paul McKenney
  - **讀哪裡**：開頭的 cache coherence、store buffer、barrier 動機（後面很深可跳）。
  - **為什麼值得讀**：memory barrier 與多核並行的權威，理解為什麼需要 barrier。

- **[Volatile: Almost Useless for Multi-Threaded Programming](https://www.intel.com/content/www/us/en/developer/articles/technical/volatile-almost-useless-for-multi-threaded-programming.html)** — Intel
  - **這篇說什麼**：為什麼 C/C++ 的 volatile 不適合多執行緒同步。
  - **和本章的關聯**：本章「volatile ≠ 多核同步」的權威佐證。

Part 4（計組）寫完了！用練習 D 把 cache + pipeline 的計算題綜合驗收。

→ [練習 D：計組綜合（cache + pipeline）](./practice-d-comporg-questions.md)
