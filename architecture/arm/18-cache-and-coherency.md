# Ch 18 — Cache 階層、coherency、shareable domain

> 目標：搞懂 ARM cache 的命名規矩（PIPT / VIPT / VIVT）、cache 維護指令（IC / DC / 各種 op）、shareability domain（Inner / Outer Shareable）、為什麼 DMA 要 cache flush、ARM 的 SMP coherency 模型。

## 命名約定：先看怎麼 index、怎麼比 tag

cache 用什麼 address 做 index、用什麼比 tag，會決定它有沒有 alias 問題：

```
PIPT  Physically Indexed, Physically Tagged
VIPT  Virtually Indexed, Physically Tagged
VIVT  Virtually Indexed, Virtually Tagged
```

| | 速度 | alias 問題 | 用在哪 |
|---|---|---|---|
| PIPT | 慢一點（要 TLB lookup） | 沒有 | 多數 ARM L1 / L2 |
| VIPT | 快（VA index 與 TLB lookup 並行） | 可能 | 早期 ARM L1（要設計避 alias） |
| VIVT | 最快 | 嚴重 alias，context switch 要 flush | 古老 ARM (ARM7、ARM9) |

**VIPT 的 alias 問題**：兩個不同 VA map 同 PA，VIPT cache 用 VA 做 index 會 cache 兩次 → 不 coherent。解法是**保證 cache 大小 / way ≤ 一個 page**（PIPT 等價）— 這在 4 KB page 上限制 cache way size 在 4 KB 內，所以多 way。

**現代 Cortex-A 都 PIPT**（A53 起 L1 D / L1 I 都 PIPT），不會 alias。VIVT 已經淘汰。

## Cache 階層

典型 Cortex-A 系統：

```
┌─ Core 0 ─┐    ┌─ Core 1 ─┐
│ L1 I  L1D│    │ L1 I  L1D│   per-core, 32–64 KB
│  (PIPT)  │    │  (PIPT)  │
├──────────┤    ├──────────┤
│   L2     │    │   L2     │   per-cluster or per-core
│ (PIPT)   │    │ (PIPT)   │   256 KB – 1 MB
└──────────┴────┴──────────┘
        │              │
        ▼              ▼
   ┌──────────────────────┐
   │   L3 / SLC           │   shared, 4–32 MB
   └──────────────────────┘
        │
        ▼
   ┌──────────────────────┐
   │   DRAM               │
   └──────────────────────┘
```

Apple M1 架構特殊（Firestorm + Icestorm + 系統 L2 + System Level Cache），但概念類似。

## Cache 維護指令家族

ARM 的 cache 操作分三大類：

```
IC <op>{, Xt}       Instruction Cache 操作
DC <op>{, Xt}       Data Cache 操作
```

`<op>` 可選：

| op | 意義 |
|---|---|
| `IALL` / `IALLU` | invalidate all I-cache |
| `IVAU`, Xt | invalidate I-cache by VA to PoU |
| `IVAC`, Xt | invalidate D-cache by VA to PoC |
| `CVAC`, Xt | clean D-cache by VA to PoC |
| `CIVAC`, Xt | clean & invalidate D-cache by VA to PoC |
| `CVAU`, Xt | clean D-cache by VA to PoU |
| `ZVA`, Xt | zero D-cache line by VA |

什麼是 PoC / PoU？

- **PoC (Point of Coherency)**：所有 master（CPU、DMA、GPU）看到一致的點 — 通常是 DRAM
- **PoU (Point of Unification)**：I-cache 與 D-cache 看到一致的點 — 通常是 L2 或 L3

寫指令到記憶體（self-modifying code、JIT）後讓 CPU 抓到新指令的 sequence：

```asm
; 1. clean D-cache 把寫進的指令推到 PoU
dc cvau, x0
; 2. dsb 等 clean 完成
dsb ish
; 3. invalidate I-cache 那條 line
ic ivau, x0
; 4. dsb 等 invalidate 完成
dsb ish
; 5. isb 確保 pipeline 重抓指令
isb
```

JIT compiler、debugger 寫斷點都需要這套。Linux `__flush_icache_range()` 就是這個。

## Shareability Domain

ARM 的 cache coherency 範圍由 **shareability domain** 控制：

```
                       ┌──────────────────────────────┐
                       │  System (whole SoC)          │
                       │  ┌────────────────────────┐  │
                       │  │  Outer Shareable       │  │
                       │  │  ┌──────────────────┐  │  │
                       │  │  │ Inner Shareable  │  │  │
                       │  │  │ ┌─────┐ ┌─────┐  │  │  │
                       │  │  │ │ Cor │ │ Cor │  │  │  │
                       │  │  │ │  e0 │ │  e1 │  │  │  │
                       │  │  │ └─────┘ └─────┘  │  │  │
                       │  │  └──────────────────┘  │  │
                       │  │  GPU、其他 cluster      │  │
                       │  └────────────────────────┘  │
                       │  DMA、外部 device            │
                       └──────────────────────────────┘
```

- **Non-Shareable**：只一個 master 看，不參與 coherency
- **Inner Shareable (IS)**：同 cluster 多核共享 cache coherence
- **Outer Shareable (OS)**：包含 GPU / 其他 cluster
- **System Shareable**：全 SoC（包含 DMA、外部 device）

PT entry 的 SH bits 設置每個 page 的 shareability。Linux 的 normal memory 通常設 **Inner Shareable**（多核 SMP coherent）。MMIO 設 device + Non-shareable 或 OS 看週邊類型。

## DMA 與 cache：經典頭痛

```
場景 A：CPU 寫一塊 buffer，DMA 引擎讀
   1. CPU 寫的資料可能還在 D-cache，未到 DRAM
   2. DMA 從 DRAM 讀，看到 stale 資料
   解：CPU 寫完要 clean cache → DRAM
       (DC CVAC + DSB)

場景 B：DMA 寫一塊 buffer 到 DRAM，CPU 讀
   1. CPU D-cache 可能有舊版本（之前 read 過）
   2. CPU 讀到 stale 資料
   解：CPU 讀前要 invalidate cache
       (DC IVAC + DSB)
```

**ARM 沒有 cache-coherent DMA 的硬規定**（看 SoC 設計）。多數 SoC 把 DMA 接到 ACE-Lite / Cache Coherent Interconnect（CCI / CMN），這樣 DMA 能加入 coherency。但**外部裝置（USB、PCIe）通常不**，要靠軟體做 cache 維護。

Linux `dma_alloc_coherent()` 與 `dma_map_single()` 的差別：前者分一塊不 cache 的 memory，後者用普通 memory 但每次傳前後做 cache flush/invalidate。

## SMP coherency：MOESI 與 ACE 協定

多核 ARM SoC 用 **AMBA ACE（AXI Coherency Extensions）**做 cache 間的 coherency 協定。CCI / CMN 是 ARM 的 coherent interconnect IP。

協定狀態（MOESI 變種）：

- **M**odified：髒，獨占
- **O**wned：髒，共享（其他 cache 也有 copy 但 M 持有真資料）
- **E**xclusive：乾淨，獨占
- **S**hared：乾淨，共享
- **I**nvalid：無效

不同 ARM core 用不同變種（A72 用 MOESI、某些用 MESI）。**對軟體基本透明** — 你寫 SMP code 不用直接管狀態，但要懂 false sharing 之類效應。

## False sharing：cache line 級別的 contention

兩個變數放在同個 cache line（64 byte），不同核分別寫 — **不會 race**（資料不同），但**會 cache line ping-pong**：

```c
struct {
    int counter_a;   // 假設 core 0 一直寫
    int counter_b;   // 假設 core 1 一直寫
} bad_struct;        // 兩個 int 在同一 cache line
```

每次 core 0 寫 → core 1 cache 那 line 變 invalid → core 1 要寫先 fetch → core 0 cache 變 invalid → 循環。**性能比兩個獨立 cache line 慢 10×**。

解：

```c
struct {
    int counter_a;
    char pad[60];        // 補滿到下一個 cache line
    int counter_b;
} good_struct;

// 或用 alignas
alignas(64) int counter_a;
alignas(64) int counter_b;
```

`__attribute__((aligned(64)))` 也可以。寫 lock-free / atomic-heavy 程式碼前先想 false sharing。

## I/D cache 不互相 coherent

這是 ARM 特性：**I-cache 與 D-cache 之間不自動同步**。

意思：你用 D-cache 寫一段指令，**I-cache 看不到**！這就是為什麼 self-modifying code、JIT 需要 `IC IVAU` 顯式 invalidate。

x86 的 I/D cache 是 coherent（intel 強調），ARM 不是 — 這是 ARM 性能 / 簡化的取捨。

## CMO (Cache Maintenance Operation) instructions

ARMv8.5 起加了 CMO 改進：`DC CGVAC` (cleans Tagged), `DC GVA` 等支援 MTE（memory tagging）。一般情況用不到，但寫 hypervisor 或 kernel 會碰到。

## 一個常見誤解

「ARM 的 cache flush 是不是寫一個指令就好？」

**沒有單一「flush all cache」指令**。要做以下其中一種：

1. **By VA range**：對你關心的範圍每個 cache line 一個 `DC CVAC`（loop）
2. **By Set/Way**：用 `DC CSW` 一個 set/way 一個 set/way 處理（kernel 啟動 / 關 MMU 時用）
3. **Power down cache**：透過 EL3 firmware 命令 disable + flush

Linux kernel 寫了一大堆 `flush_cache_*` helpers，對 driver 暴露同步 API，背後都是上面的某種組合。

## 自我檢核

- [ ] 我能說出 PIPT / VIPT / VIVT 各自意義與 alias 問題
- [ ] 我能寫 CPU 寫指令後讓 I-cache 重抓的 sequence
- [ ] 我能解釋 PoC 與 PoU 的差別
- [ ] 我能畫出 Inner / Outer / System Shareable 的範圍
- [ ] 我能解釋 DMA 何時要 cache flush
- [ ] 我能識別 false sharing 並修

下一章看 ARM 的弱記憶體模型 — DMB、DSB、ISB、acquire/release，以及為什麼 ARM 比 x86 容易踩 ordering 雷。

→ [Ch 19 ARM 弱記憶體模型與屏障](./19-memory-model-barriers.md)
