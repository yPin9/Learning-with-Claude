# Ch 5 — CPU 微架構速成：pipeline / OoO / ROB / cache

> 目標：用最少篇幅讓你熟悉現代 CPU 微架構詞彙。讀完聽得懂 "IPC", "branch predictor", "ROB size", "L2 miss", "dispatch width" 等詞，足以跟硬體工程師對話。

## 這章的目標不是成硬體設計師

本章**不是**要你設計 CPU。是要你：

- 讀 datasheet 看 IPC / cache / ROB 不陌生
- 分析 perf output 時知道 "L1-icache-load-misses" 意味什麼
- 跟 SiFive 硬體工程師聊 scheduling model 不失語

**3000 字版的 Hennessy & Patterson**。想深入：讀 H&P、讀 Agner Fog。

## Pipeline 的五階段（教科書版）

```
Fetch → Decode → Execute → Memory → Writeback
```

每個 stage 一個 cycle。一條指令 5 cycle。但**同時五條指令在不同 stage**：

```
cycle:  0   1   2   3   4   5   6
inst1:  F   D   E   M   W
inst2:      F   D   E   M   W
inst3:          F   D   E   M   W
```

**Throughput**：每 cycle 一條指令完成 → IPC = 1.

這是 1980 年代 RISC 的目標。現代 CPU 更複雜。

## Super-scalar：多 issue

現代 CPU 每 cycle 可以 issue 多條指令（不是 1 條）：

```
cycle:  0
inst1:  F1D1E1M1W1
inst2:  F2D2E2M2W2        ← 平行
inst3:  F3D3E3M3W3        ← 平行
inst4:  F4D4E4M4W4        ← 平行
```

4-wide super-scalar → IPC 最高 4。

典型 CPU 的 issue width：

- Cortex-M4: 1-wide
- Cortex-A72: 3-wide
- SiFive U74: 2-wide
- SiFive P870: 6-wide
- Apple M2 P-core: 8-wide (!!)
- AMD Zen 4: 4-wide dispatch, 6-wide rename

## Out-of-Order execution

問題：一條 load miss L1 → wait 10 cycle → 下一條 dependent instruction 也 stall。

OoO：CPU 看後面幾百條指令，找**不依賴**的先執行。load 在等 memory 時，unrelated 指令照常跑。

關鍵資料結構：**Reorder Buffer (ROB)**。紀錄 in-flight 指令、等 retire。

ROB 容量 = 「CPU 能看多遠」：

- Cortex-M: 無 ROB（in-order）
- Cortex-A72: ROB ~128
- SiFive U74: in-order（無）
- SiFive P870: ROB ~250
- Apple M2: ROB ~670 (!)

ROB 越大 → 越能吸收 memory latency → 但面積 / power 成本高。

## Register renaming

SSA-like 概念在硬體。ISA 的 architectural register（RISC-V 的 x0-x31）有限。OoO 需要更多 physical register 避免 false dependency。

```asm
add t0, a0, a1     ; t0 = first use
sw  t0, ...
add t0, a2, a3     ; 又用 t0 = overwrite
```

沒 rename → 第二個 `add` 等第一個 `sw`。
有 rename → 硬體分配不同 physical reg → 兩個 `add` 可以並行。

典型 physical register file 大小：

- Cortex-A72: 128 int reg
- Apple M2: 400+ (!)
- SiFive P870: ~200

## Branch prediction

遇到 branch 才 fetch 下一條？太慢 → 猜一個、先 execute、猜錯 rollback。

典型 branch predictor 準確度：

- Simple pattern: 99%+
- Hard data-dependent pattern: 50-80%
- Sorted data: 99%+
- Random data: 50%

**Branch mispredict 成本**：10-20 cycle（pipeline flush + refetch）。Branch-heavy code 的主要瓶頸。

## Memory hierarchy

```
Registers   ~1 cycle   ~256 bytes
L1 cache    ~3-5       32-64 KB
L2 cache    ~10-15     256 KB - 1 MB
L3 cache    ~30-50     2-32 MB
DRAM        ~150-300   GBs
SSD / NVMe  μs         TBs
```

每層延遲 10× 增加、容量 10× 增加。

### Cache miss 的 3C

- **Compulsory (Cold)**：第一次 access，必 miss
- **Capacity**：working set 大於 cache
- **Conflict**：不同 address map 到同 set → 互相踢出

寫 code 時考慮：**keep working set small, access 連續、avoid conflict**。

### Cache line 大小

通常 64 byte (RISC-V / x86) 或 32/128 byte。每次 access 一次 load 整個 line。

**False sharing**：兩個 thread 改同 cache line 的不同 variable → 互相 invalidate → 慢。

## TLB (Translation Lookaside Buffer)

virtual → physical address 的 cache。miss 時去 walk page table（慢）。

- L1 DTLB: 32-64 entries
- L2 TLB: 512-2000 entries

**大 working set → TLB pressure**。Huge pages (2MB, 1GB) 減少 TLB miss。

## Prefetcher

硬體觀察 memory access pattern，自動 prefetch。

典型 pattern：

- Stride access (`arr[i], arr[i+8], arr[i+16]...`)：prefetcher 認得
- Random access：prefetcher 幫不上
- Pointer chasing：prefetcher 基本沒用

**Prefetcher 是 modern CPU 的隱形武器**。寫 cache-friendly code 就在服侍它。

## Speculation

OoO + branch prediction 自然推向 speculation：

- 執行猜測的 branch path
- 執行猜測的 memory load
- 結果存 ROB、等確認後 commit（或 discard）

**Spectre / Meltdown 漏洞的根源**。2018 後硬體與軟體配合 mitigation。

對 benchmark 意義：speculation 讓 "看起來 bad" 的 code 變快（因為 speculate 隱藏 stall）、讓 "看起來 good" 的 code 在 mitigation 後變慢（speculation window 縮小）。

## IPC：Instructions Per Cycle

**最重要的 metric**。衡量 CPU 效率。

```
IPC = instructions / cycles
```

對各類型 code 的典型 IPC：

- Integer-heavy loop: 2-4
- Memory-bound: 0.3-1.0
- Branch-heavy: 1-2
- Well-vectorized: 4+ (但 vector 指令 1 條 做多 ops)
- Waiting on cache miss: <0.5

**IPC 低 → 可能 opportunity**。但低 IPC 不一定壞（memory bound 是 fundamental）。

## CPU vs system behavior

Perf event 常分兩層：

- **CPU 事件**：cache miss、branch miss、IPC
- **System 事件**：context switch、page fault、interrupt

兩者都影響效能。micro benchmark 常 CPU-bound、macro benchmark 兩者都重。

## 現代 CPU 的 "Front-end / Back-end"

```
Front-end               Back-end
--------------          --------
Fetch → Decode    →    Rename → Schedule → Execute → Retire
- I-cache               - ROB
- Branch pred           - Reservation station
- Decoders              - Execution units
                         - Writeback
```

Bottleneck 可能：

- **Front-end bound**：fetch 不夠快（branch miss、I-cache miss）
- **Back-end bound**：execute 不夠快（ALU 忙、memory wait）

perf 有工具 (Top-down methodology) 幫你判斷。

## Top-down 分析

Intel 推的效能分析框架。把 slots 分類：

```
Retiring    - 實際做有用事 (目標)
Bad Speculation - 猜錯、rollback
Front-End Bound - fetch/decode 不夠
Back-End Bound  - execute 不夠
```

`perf stat -M TopdownL1` 在新 Intel CPU 印這些 ratio。RISC-V 社群還在 adopt。

## RISC-V microarchitecture 對照

| CPU | Type | Issue | ROB | Freq |
|-----|------|-------|-----|------|
| SiFive E31 | In-order | 1 | - | <500 MHz |
| SiFive U74 | In-order | 2 | - | 1.5 GHz |
| SiFive P270 | OoO | 2 | 64 | 1.5 GHz |
| SiFive P550 | OoO | 3 | 128 | 2 GHz |
| SiFive P670 | OoO | 4 | 192 | 2.5 GHz |
| SiFive P870 | OoO | 6 | 256 | 3.0 GHz |
| Rivos Veyron | OoO | 8+ | 500+ | 3+ GHz |

數字持續更新、可能不精確。看 whitepaper 為準。

## 對 compiler 工程師的啟示

知道微架構讓你做更好決策：

- **低 IPC 的 hot function** → 看是 front-end 還是 back-end bound
- **Branch-heavy** → 可能要 Zicond 或 branch-free pattern
- **Memory bound** → 看 prefetcher 是否 work、可不可以 improve locality
- **Vector 不夠飽和** → auto-vectorize miss 什麼 pattern

## 常見誤會

1. **「IPC = 1 是 target」**：現代 CPU 的 target 是 3-5. IPC=1 的 CPU 是 1990 年代水準。
2. **「clock 越高越快」**：有關但不絕對。ARM 1.5 GHz 可能比 x86 2.5 GHz 的某 workload 快（IPC 差異）。
3. **「L1 cache 命中就 fast」**：本身 fast，但 L1 容量小（32K）。working set 大 → L1 miss 必然。
4. **「OoO 完全隱藏 memory latency」**：ROB 容量有限。長 latency miss 仍 stall。
5. **「branch predictor 總是對」**：95-99%。但 hot loop 中 1% miss 也累積成大 overhead。

## 動手練習

1. 跑 `lscpu` 看你 CPU 的 L1/L2/L3 size、core 數。
2. 寫一個 cache-friendly vs cache-unfriendly loop，用 perf 對比 miss 數。
3. 寫 branch-heavy vs branch-free 版本，量 branch miss。
4. 讀 SiFive P870 的 whitepaper，認出 pipeline width / ROB size / cache hierarchy。
5. 用 `perf stat -M TopdownL1`（若 CPU 支援）分析一個 program，看 Retiring / Bad Spec / FE/BE bound 分布。

## 自我檢核

- [ ] 我能解釋 pipeline / super-scalar / OoO 的差別
- [ ] 我知道 ROB / physical register 的角色
- [ ] 我能畫 memory hierarchy 並給各層典型 latency
- [ ] 我能看 IPC 數字判斷 code 是否 efficient
- [ ] 我知道 Front-end vs Back-end bound 的差異

下一章專攻 performance event —— perf 工具能量的具體事件清單。

→ [Ch 6 Performance events：IPC / cache miss / branch miss](./06-performance-events.md)
