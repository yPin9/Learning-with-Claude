# Ch 19 — ARM 弱記憶體模型與屏障

> 目標：搞懂 ARM 的「**弱**記憶體模型」是什麼意思、x86 的 TSO 多強、ARM 的 DMB / DSB / ISB 三個屏障各自做什麼、acquire/release semantics、為什麼 lock-free 程式 ARM 與 x86 行為不同。

## 強 vs 弱：兩個世界

「memory model」指的是「在多核 / 多 master 系統裡，**memory operation 觀察到的順序**規則」。

```
強模型 (TSO, Total Store Order)：
  x86 / x86_64 / SPARC TSO
  load 不會被 store 重排，store 之間順序保證，多核看到全域總序。

弱模型 (Weakly Ordered)：
  ARM / RISC-V / POWER
  幾乎所有 memory op 都可以重排（除非顯式屏障）
  多核可能看到不同順序
```

**ARM 是弱模型**。意思是 CPU / cache / memory subsystem 可以為了性能任意重排 load / store，**只要單線程結果一致**。多線程下你看到「不可能」的順序時，往往就是這個。

## 經典範例：訊息傳遞

```c
// thread A
data = 42;          // (A1) write
ready = 1;          // (A2) write

// thread B
while (!ready) ;    // (B1) read
print(data);        // (B2) read
```

x86：thread B 看到 `ready = 1` 後 `data` 一定是 42（store-store 順序保證）。

ARM：B1 看到 `ready = 1` 但 B2 可能讀到 `data = 0`！原因：A2 可能比 A1 先到 memory（store-store 重排），或者 B 的 cache fetch 順序錯。

ARM 修正：

```c
// thread A
data = 42;
__atomic_store_n(&ready, 1, __ATOMIC_RELEASE);  // 等於 dmb ish + str

// thread B
while (!__atomic_load_n(&ready, __ATOMIC_ACQUIRE)) ;
print(data);
```

**Acquire / Release semantics** 強制必要的順序，但比 full barrier 弱 — 編譯器與 CPU 仍可在不打破 acquire/release 的前提下重排。

## 三個屏障：DMB / DSB / ISB

```
DMB <option>     Data Memory Barrier
                 在這條之前的 memory ops 必須在之後的 memory ops 之前完成
                 但不等他們真寫到 DRAM

DSB <option>     Data Synchronization Barrier
                 等所有之前的 memory ops 真正完成（到 DRAM / MMIO）
                 之後才繼續

ISB              Instruction Synchronization Barrier
                 把 pipeline 清掉，重新從現在開始抓指令
```

`<option>` 控制範圍與類型：

```
SY    full system, both load+store
ISH   inner shareable, both
ISHST inner shareable, store-store
ISHLD inner shareable, load-load
NSH   non-shareable
OSH   outer shareable
LD    load
ST    store
```

**最常用：`DMB ISH`**（inner shareable，兩向）— 多核同步用。

## 三者實際差別

```c
// 場景 1：多核同步 (lock-free)
write_data();
DMB ISH;            // 確保 data write 之前完成
write_flag();

// 場景 2：等 MMIO 寫真的到 device
write_to_device_register();
DSB;                // 等寫真到 device，可能要 1000+ cycle
// 之後才能假設 device 看到了

// 場景 3：改完 vector table、page table、system register 後
update_page_table();
DSB ISH;            // 等所有 stores 完成
TLBI ...;           // invalidate
DSB ISH;            // 等 invalidate 完成
ISB;                // 確保 pipeline 不抓舊指令
```

**ISB 是「instruction」barrier，不是「memory」**。它影響 CPU **取指**，不影響 data。改完 SCTLR、VBAR 之類 system register 必須 ISB。

## acquire/release：精準屏障

```asm
ldr   x0, [x1]              ; ordinary load
ldar  x0, [x1]              ; load-acquire（讀後屏障）
ldapr x0, [x1]              ; load-acquire processor-only（弱版，ARMv8.3+）

str   x0, [x1]              ; ordinary store
stlr  x0, [x1]              ; store-release（寫前屏障）
```

**LDAR**：保證後面的 memory ops 不會被重排到 LDAR 之前（讀完後固定點）。
**STLR**：保證前面的 memory ops 不會被重排到 STLR 之後（寫前固定點）。

兩者組合就是 acquire-release pattern：

```c
// 寫者
data = 42;
stlr ready, 1;     // release

// 讀者
ldar v, ready;
if (v == 1) print(data);
```

LDAR / STLR 比 DMB ISH 高效率，因為**不阻塞無關的 memory ops**，只在自己的位置設順序點。x86 從 ARMv8 引入 LDAR/STLR 後，C++ atomic 在 ARM 性能大幅提升。

## ARMv8 的記憶體模型（半正式版）

ARMv8 規範叫 **Other-Multi-Copy Atomic**（OMCA）。簡化規則：

1. **單一線程內**，看自己的寫立刻可見（program order）
2. **不同線程看相同位址的寫**，最終會看到一個一致的順序（coherence）
3. **不同位址的寫**，沒順序保證（除非顯式屏障）
4. **acquire 之前 / release 之後 不可重排**

第 3 條是弱模型的核心。x86 TSO 用「全域 store 順序」強化了這個。

## 為什麼 ARM 選弱模型？

性能。CPU 設計者多年寫 ARM 為了：

- **out-of-order 執行的自由度**：強 ordering 約束太多重排機會
- **cache 與 memory subsystem 設計簡單**：不用維護 TSO 級別的 invariant
- **多核擴展性**：多核變多時，全域 ordering 的廣播成本指數增長

代價是「**程式設計師要小心**」— 寫 lock-free 程式 ARM 比 x86 難。但有了 LDAR/STLR 與 C++ atomic 抽象，多數人不用直接面對。

## 看一段 lock-free counter

```c
// 增加 atomic counter，read-modify-write
atomic_fetch_add(&counter, 1, memory_order_relaxed);
```

**relaxed** 在 ARM 編成：

```asm
1: ldxr  w0, [x_counter]
   add   w0, w0, #1
   stxr  w1, w0, [x_counter]
   cbnz  w1, 1b
```

LL/SC 模式，但**沒有屏障**。對多核不保證和其他 memory op 的順序，但 atomic counter 自身正確。

要 strict ordering：

```c
atomic_fetch_add(&counter, 1, memory_order_seq_cst);
```

ARM 編成：

```asm
1: ldaxr w0, [x_counter]    ; load-acquire-exclusive
   add   w0, w0, #1
   stlxr w1, w0, [x_counter] ; store-release-exclusive
   cbnz  w1, 1b
```

順序最強，但每次執行 RMW 都帶 acq/rel 開銷。**memory_order_relaxed 在 ARM 比 x86 省更多**，因為 x86 預設就是 sequential consistent。

## ARMv8.1-A LSE：減少 LL/SC 重試

ARM 觀察到 LL/SC 在高 contention 下重試很多，加了 **LSE (Large System Extensions)** 提供單指令 atomic：

```asm
ldadd  w1, w0, [x_counter]     ; atomic fetch_add（單指令 RMW）
cas    w1, w2, [x_counter]     ; compare-and-swap
swp    w1, w0, [x_counter]     ; exchange
```

這些指令 hardware 保證 atomic，不用重試。**對 spinlock / counter 性能巨大提升**，AWS Graviton2 / Apple M1 等都實作 LSE。

## ARMv8.4-A：Persistent Memory ordering

ARMv8.4 還加了給 NVRAM / PMEM 用的指令：`DC CVAP`（clean to point of persistence）。應用很窄但 persistent memory 程式設計需要。

## 一個常見誤解

「volatile 在 C 裡是不是就解決 memory ordering 了？」

**完全不是**。`volatile` 告訴**編譯器**「這個變數每次都從記憶體讀」（不要 cache 在 register）— 但對 **CPU 重排和 cache** 沒影響。

要正確的 multi-threaded ordering 用 C11 `_Atomic` / C++ `std::atomic`，編譯器會插對應的 LDAR/STLR/DMB。`volatile` 只在 MMIO（單線程，不期望編譯器 cache）對的。

混 `volatile` 與 atomic 是 ARM 嵌入式 driver 常見錯誤。

## 自我檢核

- [ ] 我能說出強 vs 弱記憶體模型的差別與代表平台
- [ ] 我能寫一個 message-passing 範例顯示弱模型問題
- [ ] 我能解釋 DMB / DSB / ISB 各自做什麼
- [ ] 我能說出 LDAR / STLR 的 acquire/release 語意
- [ ] 我能比較 LSE atomic 與 LL/SC 的差別
- [ ] 我能解釋 volatile 為什麼不夠 multi-threaded sync

下一章看 ARM 的原子操作 — LL/SC、LSE、CAS、各種 RMW 指令。

→ [Ch 20 原子操作：LDXR/STXR 與 LSE atomics](./20-atomics.md)
