# Ch 2 — 你必須先懂的 CPU 微架構

> **目標**：把攻擊微架構安全所需的最小 CPU 知識清單打通。不是 CPU 設計課——不講暫存器分配、不講記憶體一致性協定的完整形式——只講「推測執行為什麼能讀到不該讀的東西、以及為什麼那個痕跡留在 cache 上」這條理解路徑上必須過的每個關口。讀完你要能在腦中畫出一條指令從 fetch 到 retire 的完整流程，並且解釋為什麼「推測 + 微架構副作用殘留」是整個瞬態執行攻擊家族的根。

> **環境**：真跑指令用本機 WSL2 i7-10700 Comet Lake。可用 `cpuid` 查微架構參數；`lscpu` 看拓撲；組語部分用 `gcc -S -O2` 或 `objdump -d` 觀察。

## 你腦中要有的那張圖

在深入每個機制之前，先把現代 CPU 的整體架構印到記憶裡。下面這張圖是 Comet Lake 這類超純量 OoO 處理器的骨架：

```
  ┌──────────────────────────────────────────────────────────────┐
  │                        前端（Frontend）                       │
  │                                                              │
  │  I-Cache  →  Fetch  →  分支預測器 →  Decode  →  Rename/Alloc │
  │                         (BTB/BHT/RSB/TAGE)                  │
  └────────────────────────────┬─────────────────────────────────┘
                               │ micro-ops (µops)
  ┌────────────────────────────▼─────────────────────────────────┐
  │                       後端（Backend）                         │
  │                                                              │
  │   ROB（Reorder Buffer）   ←────────────────────────────┐    │
  │   ┌─────────────────────────────────────────────┐      │    │
  │   │  µop0  µop1  µop2  µop3  µop4  µop5  ...    │      │    │
  │   └──────────────┬──────────────────────────────┘      │    │
  │                  │ 亂序 dispatch                         │    │
  │   Scheduler / Reservation Station                       │    │
  │   ┌─────┐ ┌─────┐ ┌─────┐ ┌──────┐ ┌──────┐           │    │
  │   │ EU0 │ │ EU1 │ │ EU2 │ │ EU3  │ │ EU4  │  ...      │    │
  │   │ ALU │ │ ALU │ │ JMP │ │ Load │ │ Store│           │    │
  │   └──┬──┘ └──┬──┘ └──┬──┘ └──┬───┘ └──┬───┘           │    │
  │      │       │       │       │         │               │    │
  │      └───────┴───────┴───────┴─────────┘               │    │
  │                         結果 → 寫回 PRF ────────────────┘    │
  │                                                              │
  │   ROB HEAD → Retire（按程式序 commit）→ 更新架構狀態           │
  │                                                              │
  └──────────────────────────────────┬───────────────────────────┘
                                     │
  ┌──────────────────────────────────▼───────────────────────────┐
  │             記憶體子系統                                       │
  │  L1-D  →  L2  →  LLC (L3, 16MB)  →  DRAM                   │
  │  Load Buffer / Store Buffer / MSHR / Line Fill Buffer         │
  └──────────────────────────────────────────────────────────────┘
```

這張圖裡最關鍵的兩個結構：
- **ROB（Reorder Buffer，重排序緩衝區）**：所有已 fetch 但還沒 commit 的 µop 按程式順序排在這裡。執行可以亂序，但退休（retire/commit）一定照程式序。
- **Scheduler / Reservation Station**：等 µop 的輸入運算元就緒，就送到對應的執行單元（EU）——不管程式序。

理解了這張圖，你就能看出「推測執行」存在於哪個位置：在 ROB 裡、在 EU 執行的途中，指令可能最終被標為「需要 squash」（推測錯了），這時 ROB 把它退出去而不是 commit——架構狀態不更新，但 cache 可能已經被改。

## Pipeline：一條指令走的路

先從最基礎的概念建立共識。

**非 pipeline（前 CPU 時代）**：
```
fetch → decode → execute → writeback     下一條才開始
```
每條指令串行，一條做完才做下一條。一條指令 10 cycles，吞吐量 0.1 IPC。

**Pipeline（流水線）**：
```
Cycle 1: [fetch I1]
Cycle 2: [decode I1] [fetch I2]
Cycle 3: [exec I1]  [decode I2] [fetch I3]
Cycle 4: [wb I1]    [exec I2]  [decode I3] [fetch I4]
```

把指令切成多個 stage，同時進行不同指令的不同 stage——「重疊執行」。理想情況 4 stages 就有 4× 的吞吐量提升。

**Hazard（衝突）打破理想**：
- **Data Hazard**：`add rax, 1 → mov [rbx], rax`——後者需要等前者的結果。需要 stall（暫停 pipeline）或 forwarding（把結果直接路由給下一條）。
- **Control Hazard**：碰到分支，不知道下一條指令在哪。傳統做法：stall 等分支結果——代價極高（現代 CPU 分支每幾條指令就有一條）。**解法：分支預測**。
- **Structural Hazard**：兩條指令都要用同一個執行單元（現代 CPU 靠多個 EU 緩解）。

**Stall 的代價**：現代 CPU 的 pipeline 深度 10–20 stages。一次分支預測失敗（misprediction）要把後面填進去的指令全部沖掉（flush），花 10–20 cycles 重填。如果每 4 條指令有一條分支，10% 的 misprediction 就讓效能打七折。

這就是為什麼「分支預測」在現代 CPU 上是個精心設計的機器學習系統，而不是一個簡單的 heuristic。

## 亂序執行（Out-of-Order Execution, OoO）

Pipeline 讓多條指令重疊，但仍然是「按程式序一條一條 fetch 和提交」。亂序執行更進一步：**dispatch 和 execute 的順序可以跟程式序不同**，只要資料相依允許。

### 為什麼需要亂序執行？

假設程式碼：
```c
int x = A[i];    // 需要等 A[i] 從記憶體載入，MISS = 240 cycles
int y = x + 1;   // 相依於 x，必須等
int z = B[j];    // 跟 x 無關！
int w = z * 2;   // 相依於 z
```

順序執行：等 `A[i]` 的 240 cycles，再做 `x+1`，再等 `B[j]`，再做 `z*2`。兩次記憶體存取串行。

亂序執行：CPU 看到 `B[j]` 跟 `x` 無相依，把 `B[j]` 的 load 和 `A[i]` 的 load**同時送出**——兩次記憶體存取並行。實際等待時間接近 max(240, 240)≈240 而不是 480。這是 OoO 最大的收益來源：**隱藏記憶體延遲**。

### OoO 的三個關鍵結構

**1. Register Renaming（暫存器重命名）**

程式碼可能重複用同一個暫存器（`rax = ...` 寫兩次），看起來有 WAW（write-after-write）相依，但其實是假相依。CPU 把每個暫存器寫入映射到不同的**物理暫存器（Physical Register File, PRF）**，消除假相依，讓更多指令可以並行。

i7-10700（Comet Lake）的 PRF 有 **280 個整數實體暫存器**（架構上只有 16 個 GPR），這讓 ROB 能同時追蹤很多獨立的執行中指令。

**2. ROB（Reorder Buffer）**

ROB 是一個 FIFO，按**程式序**記錄所有「已 fetch 但還沒 commit」的 µop。Comet Lake 的 ROB 大小是 **352 個 µop**（已知公開數據）。

ROB 的每個 entry 記錄：
- 指令（µop）
- 執行狀態（waiting / executing / done）
- 如果 done：結果是否有 exception（缺頁、除以零、保護違規）
- 是否是 speculative（推測執行路徑上的指令）

**retire** 從 ROB HEAD 開始，只有 HEAD 的指令 done 且沒有 exception，才 commit（更新架構暫存器狀態、寫 memory）然後移出 ROB。如果有 exception，所有在它後面的指令都 squash 掉（不 commit）。

**3. Scheduler（Reservation Station）**

ROB 是記帳本，Scheduler 是「送貨員」。每個 µop 進 ROB 之後也進 Scheduler，等它的**所有輸入運算元都 ready**，Scheduler 就把它送到對應的**執行單元（EU）**——不管它在程式序哪個位置。

這就是亂序執行的核心：Scheduler 只看資料相依（data-flow），不看程式序。

### 本機驗證：Comet Lake 的 OoO 窗口

用 cpuid 確認微架構參數（真實輸出）：

```bash
$ cpuid -1 2>/dev/null | grep -i "maximum\|rob\|window\|retire"
# WSL2 的 cpuid 不直接回傳 ROB 大小（需要 Intel 文件）
# 但可確認超純量寬度
```

Comet Lake（i7-10700）的已知微架構數字（Intel Optimization Manual Table 2-1，未實測直接查）：

| 參數 | Comet Lake (Sunny Cove backend) 近似值 |
|---|---|
| Pipeline 深度 | ~14 stages |
| ROB 大小 | 352 µops |
| Scheduler（RS）| 97 entries |
| Integer PRF | 280 entries |
| FP/SIMD PRF | 224 entries |
| Decode width | 4 µops/cycle |
| Retire width | 4 µops/cycle |
| 執行埠 | 10 ports（EU0–9） |

ROB=352 意味著：在一個全是記憶體 miss 的 workload 裡，CPU 能「飛行中（in-flight）」同時有 352 條還沒 commit 的指令——這個窗口大小直接決定 OoO 能「看多遠」去排程非相依指令。

## 推測執行（Speculative Execution）

OoO 解決了「資料相依」的等待問題。**推測執行**解決「控制相依」——遇到條件分支，不知道走哪條路，就**猜一條，繼續執行**，等分支結果出來再確認。

### 分支預測：猜的藝術

現代 CPU 的分支預測器（Branch Predictor Unit, BPU）是個小型的 CPU：

```
  分支指令到達 decode 之前，BPU 已經在讀 PC
         │
         ▼
  ┌──────────────────────────────────────────┐
  │   BTB（Branch Target Buffer）             │
  │   用 PC 查「這條分支跳到哪？」快取         │
  │   Hit → 直接知道 target                   │
  │   Miss → 等 decode 算                    │
  └──────────────────────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────────────┐
  │   方向預測（Taken/Not-Taken）             │
  │   BHT（Branch History Table）            │
  │   2-bit saturating counter 或更複雜的     │
  │   TAGE（Tagged Geometric Length Predictor│
  │   用多層全域歷史 XOR PC 當 index          │
  └──────────────────────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────────────┐
  │   間接分支（jmp *reg）預測                │
  │   BTB + IBTB（Indirect Branch Target Buffer）
  │   Spectre-v2 的攻擊點                    │
  └──────────────────────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────────────┐
  │   RSB（Return Stack Buffer）              │
  │   用 stack 記錄 call 的返回位址            │
  │   ret 推測用 RSB top，不查 BTB            │
  │   Spectre-RSB（Ch 17）的攻擊點            │
  └──────────────────────────────────────────┘
```

**分支預測失敗的代價**：CPU 沿著預測路徑 fetch/decode/dispatch 了一堆指令，然後分支結果出來發現猜錯了——這些指令全部要 squash。Comet Lake 的 branch misprediction penalty 約 15–20 cycles。

**訓練（Training）**：攻擊者可以讓 CPU 的 BHT/BTB 「學習」到他想要的預測——這是 Spectre-v1（訓練 taken/not-taken 方向）和 Spectre-v2（毒化間接分支目標）的核心手段。Ch 14/15/16 深入。

### 推測窗口：攻擊者的機會

關鍵問題：在分支結果出來**之前**，CPU 沿著預測路徑執行了多少指令？

這取決於：
1. **分支結果延遲**：如果分支條件是「`if (x == secret)`，而 `secret` 在記憶體裡（cache miss）」，CPU 要等 240 cycles 才知道跳不跳——這 240 cycles 裡，預測路徑的指令可以執行**很多很多條**。
2. **ROB 大小**：ROB 裡最多放 352 個推測中的 µop，這是窗口上限。
3. **執行埠的吞吐量**：如果預測路徑的指令沒有相依，CPU 每 cycle 可以 dispatch 幾條，窗口裡能執行很多工作。

**典型 Spectre-v1 場景**：

```c
if (x < array1_size) {          // 分支條件：array1_size 在 DRAM（cache miss）
    uint8_t v = array1[x];      //   ← 這兩行在推測窗口裡執行
    uint8_t tmp = array2[v*64]; //   ← 並且改變了 cache 狀態！
}
```

攻擊者訓練 BHT 讓 CPU 預測 taken（x < array1_size），然後給一個 x 超過 array1_size 的值。CPU 推測地執行 `array1[x]`（這是越界讀取，正常情況下會被阻止）；這條 load 的結果 `v` 是禁止讀到的資料（secret）。接著 `array2[v*64]` 也推測性地執行，把 `array2` 的第 `v*64` 個 byte 載入 cache。

分支結果出來（240 cycles 後），CPU 發現預測錯了，squash 掉這兩條指令——**`rax = array1[x]` 的值被抹掉，架構狀態恢復**。但 `array2[v*64]` 這條 line 已經在 cache 裡了，**cache 狀態不被 squash**。

攻擊者接著用 Flush+Reload 探針 `array2` 的所有候選 line，計時最快的那條就是 `v` 的值——secret 就這樣洩漏了。

這個機制理解了，Spectre 家族的所有變體都只是在問：「誰觸發了推測執行（分支預測器？間接分支？ret？exception？）、以及怎麼建立 covert channel 把資料帶出來。」

## 記憶體子系統：載入的路徑

推測執行讀資料，必須走過記憶體子系統。這裡需要知道幾個關鍵結構：

### Load Buffer / Store Buffer

CPU 的 Load Buffer（LB）和 Store Buffer（SB）是記憶體存取的「飛行控制塔」：

- **Load Buffer（~72 entries in Comet Lake）**：記錄所有「已 dispatch 但還沒拿到資料」的 load µop。每個 entry 有 VA、PA（等 TLB 翻譯後填入）、狀態。
- **Store Buffer（~56 entries）**：記錄所有「已執行但還沒 commit 到 cache」的 store。store 的資料先進 SB，retire 後才寫 L1-D。
- **Store-to-Load Forwarding**：如果一個 load 的地址跟 SB 裡某個 store 的地址重疊，CPU 直接從 SB 轉發，不用去 cache——這個機制在 MDS 攻擊裡被利用。

### MSHR（Miss Status Holding Register）

L1-D 每次 cache miss，會發出一個 refill request 到 L2。MSHR 追蹤「正在等填充」的 miss——如果同一個 cache line 有多個 load 都 miss，MSHR 合併成一個請求（coalescing），只去 L2 取一次。

現代 CPU 通常有 ~10–16 個 MSHR，意味著可以同時有這麼多條 outstanding cache miss 飛行在外——這讓 OoO 能隱藏多個串行 miss 的延遲（memory-level parallelism）。

### Line Fill Buffer（LFB）

L1-D miss 後，資料回來之前暫放的地方。MDS 攻擊（Microarchitectural Data Sampling，Ch 19）的核心是：在 LFB 裡的資料在某些情況下可以被推測性地「採樣」到不應該讀到它的指令裡。

### TLB（Translation Lookaside Buffer）

每次記憶體存取（虛擬位址）都要翻成物理位址，走 page table 需要 3–4 次記憶體存取（L1→L2→L3→DRAM 每層都可能 miss）。TLB 是這個翻譯的快取。

i7-10700 的 TLB 幾何（cpuid 查詢，不同 size 的 page 各有 TLB）：
- L1-D TLB（4KB page）：64 entries
- L1-I TLB（4KB page）：128 entries
- L2 TLB（4KB page，unified）：2048 entries

TLB miss 的代價：要走 page table（page walk），視 cache 裡有沒有 page table 條目，代價從 ~20 cycles（所有 PT 都在 L1）到 >100 cycles（某些級別在 DRAM）。

TLB 也是攻擊面：Ch 27 講 TLB 側信道——透過 TLB flush 前後的計時差，推斷受害者存取了哪個虛擬位址範圍（頁對齊精度）。

## Cache 階層概觀（為 Ch 3 鋪路）

本機 i7-10700 的 cache 幾何（`/sys/devices/system/cpu/cpu0/cache/` 真實輸出）：

```bash
$ for f in /sys/devices/system/cpu/cpu0/cache/index*/; do
>     echo "=== L$(cat $f/level) $(cat $f/type) ==="; 
>     echo "  size:   $(cat $f/size)"; 
>     echo "  ways:   $(cat $f/ways_of_associativity)"; 
>     echo "  sets:   $(cat $f/number_of_sets)"; 
>     echo "  line:   $(cat $f/coherency_line_size)B"; 
> done
=== L1 Data ===
  size:   32K
  ways:   8
  sets:   64
  line:   64B
=== L1 Instruction ===
  size:   32K
  ways:   8
  sets:   64
  line:   64B
=== L2 Unified ===
  size:   256K
  ways:   4
  sets:   1024
  line:   64B
=== L3 Unified ===
  size:   16384K
  ways:   16
  sets:   16384
  line:   64B
```

驗算：L1-D = 8 ways × 64 sets × 64B/line = 32768B = 32KB ✓

關鍵觀察：
- **LLC（L3）是 inclusive**（cpuid 輸出 `inclusive to lower caches = true`）——L1/L2 裡的 line，一定也在 L3。這對 Flush+Reload 很重要：flush 一條 line，它在所有層都被清掉。
- **LLC 在多核間共享**（cpuid 顯示 `maximum IDs for CPUs sharing cache = 0xf (15)`，16 個邏輯核心共用這個 LLC）——這是跨進程 LLC 側信道攻擊的前提。
- **L1/L2 是每核心私有**——不同核心之間用 cache coherence 協定（MESIF）同步，不是直接共享。

cache 的詳細結構（set-associative、replacement policy、address mapping）是 Ch 3 的主題，這裡先有個輪廓。

## SMT（超執行緒）：同一個核心裡的共享

i7-10700 每個物理核心有兩條 SMT（Simultaneous Multi-Threading，Intel 叫 Hyper-Threading，HT）執行緒。兩條 HT 執行緒：

```
物理核心
├── 執行緒 0（CPU 0, 2, 4, ...）
└── 執行緒 1（CPU 1, 3, 5, ...）

  共享：L1-D cache、L1-I cache、L2 cache、執行埠、Load Buffer、Store Buffer
  私有：程式計數器、GPR、部分 BPU 狀態（view 獨立，但實體有部分共享）
```

從安全角度，SMT 同核心共享的資源幾乎全是攻擊面：
- **L1 cache 共享** → 精確的 L1 Prime+Probe，同核心解析度最高
- **執行埠共享** → Port Contention（Ch 26），可以在指令粒度觀測受害者的執行
- **Store Buffer** → MDS/MLPDS（Ch 19）的 store-to-load forwarding 跨執行緒洩漏

本機驗證（`lscpu` 真實輸出）：

```bash
$ lscpu | grep -E "(Thread|Core|Socket|^CPU)"
CPU(s):                 16
Thread(s) per core:     2
Core(s) per socket:     8
Socket(s):              1
```

16 個邏輯 CPU = 8 個物理核心 × 2 HT。CPU 0 和 CPU 1 在同一個物理核心。

**攻防重要性**：Meltdown/MDS 的很多場景需要 SMT 在同一個物理核心。防禦上，一些 kernel 選擇在高安全模式下完全關掉 HT（如某些 ChromeOS 和 HPC 集群）——代價是 ~30–50% 的效能損失，但徹底消除這個攻擊面。

## 推測 + 微架構痕跡殘留 = 瞬態執行攻擊的根

把前面所有片段組起來，講清楚那個根本問題：

### CPU 的承諾

ISA 規範（x86 的 Intel SDM）給了一個承諾：「指令只有在 retire 之後，其效果才對架構狀態可見。推測性執行的指令如果被 squash，其效果不會對架構狀態可見。」

這個承諾對**架構狀態**（暫存器、記憶體）成立。

### 它沒承諾的東西

CPU 規範**沒有承諾**：「被 squash 的指令不會改變微架構狀態（cache、TLB、預測器、LFB）。」

這個空白不是疏漏，是設計上的取捨：要完全抹掉 cache 的副作用，等推測執行每一條都確認了才讓 cache 有效——這讓推測執行的延遲隱藏效果大打折扣（你要等確認才能 serve 下一條 load），違背了設計初衷。

### 攻擊者的利用

```
CPU 的承諾邊界：
                    ┌──── 架構狀態 ───┐
                    │                │
  fetch → dispatch → retire ──────► 暫存器、記憶體（受保護的語意邊界）
              │           ×
              │           ×  squash  ← 這裡阻止了架構狀態更新
              │           ×
              ▼
         微架構副作用（cache state、TLB entries、LFB）
              │
              │  ← 沒有承諾這個會被清掉！
              │
              ▼
         攻擊者可觀測（透過計時）
```

這個圖就是整個瞬態執行攻擊家族的「根」。無論你是 Spectre-v1（利用 BHT 訓練繞 bounds check）、Spectre-v2（毒化 BTB 做 gadget injection）、Meltdown（利用 exception 處理延遲）、MDS（利用 LFB 採樣）——底層機制都是：

1. **讓 CPU 推測性地執行「架構上不應該執行」的指令**（越界讀取、跨特權讀取、從 LFB 讀到不該讀的資料）
2. **那個指令把秘密值編碼進了某個微架構狀態**（最常見是 cache：`array[secret * 64]` 把 `secret` 編碼成哪個 cache line 被載入）
3. **squash 發生，架構狀態回滾，但微架構狀態殘留**
4. **攻擊者用計時讀出殘留的微架構狀態，還原 secret**

步驟 4 就是 Part 2 的 cache 側信道原語在做的事。這就是為什麼 Part 2 必須在 Part 3 之前：沒有 cache 原語，你沒有讀出殘留狀態的工具。

## 分支預測器結構：攻擊者需要知道的粒度

分支預測器在 Spectre 攻擊裡被直接利用，需要知道幾個關鍵結構：

**BTB（Branch Target Buffer）**：
- 記憶體：`(PC → target address)` 的快取，幾百到幾千 entries。
- 用 PC 的低位元 index，高位元作 tag（類似 cache 的 set-associative）。
- **攻擊者可以「填充」BTB**：在攻擊者位址空間訓練某個 PC 映射到目標 gadget，然後讓受害者的間接分支剛好 alias 到同一個 BTB entry——受害者的 `jmp *rcx` 就會推測跳到攻擊者指定的位置。這是 Spectre-v2 的核心（Ch 16）。

**BHT / TAGE**：
- 記錄分支的歷史（最近 N 次 taken/not-taken）用來預測下次方向。
- TAGE（現代 Intel 用的方法）：多個 predictor table，各用不同長度的全域歷史（如 8/16/32/64 bits）XOR PC 當 index。
- **Spectre-v1 訓練**：攻擊者多次讓分支 taken，讓 BHT 學到「這個分支幾乎都 taken」，然後給一個越界 index——CPU 相信「會 taken」於是推測執行越界讀取。

**RSB（Return Stack Buffer）**：
- `call` 指令把返回地址 push 進 RSB（大小通常 16–32 entries），`ret` 用 RSB top 預測返回目標（而不是去查 BTB）。
- RSB 在 Intel/AMD 上是**per-execution-thread**的，不跟 HT 配對共享。
- **RSB underflow**：如果 call/ret 不平衡（某些 kernel 路徑或 signal handler 之後），RSB 空了——`ret` 改回查 BTB，成為 Spectre-v2 的另一個變體（ret2spec）。Ch 17 講。

## ARM/RISC-V 對照

（橫向連結：如果你來自 arm 課程）

**ARM Cortex-A 上的 OoO/推測執行**：
- AArch64 上也有完整的 OoO pipeline（Cortex-A72/A76/X2 等）。
- 分支預測器同樣有 BTB/BHT，同樣可以被 Spectre-v1/v2 訓練。
- 沒有 `clflush` 指令——攻擊者要用 `DC CIVAC`（Data Cache Invalidate by VA to PoC）或 eviction set 來做 flush 的等效操作。這讓 ARM 上的 cache 攻擊多了一個障礙，但不是不可能（Prime+Probe、Evict+Reload 都有在 ARM 上實現）。
- ARM 的 Meltdown 行為跟 Intel 不同：ARM 文件上說「推測性讀取越特權記憶體的行為 architecturally defined as UNPREDICTABLE」，實際上較多 ARM 核心沒有 Intel 原版的 Meltdown 弱點（但有些 early Cortex-A75 被確認有類似行為）。

**RISC-V**：
- 高效能 RISC-V 核心（如 SiFive P 系列、BOOM）同樣有 OoO pipeline，但通常比 Intel/AMD 的 ROB 更小（~40–100 entries）、推測窗口更短。
- RISC-V 的 ISA 規範明確不承諾微架構副作用，這讓瞬態攻擊在 RISC-V 上理論上也適用。
- 大部分嵌入式 RISC-V 核心（M-mode only, in-order, no cache）根本不受這些攻擊——因為沒有推測執行和共享 cache。

這個對照說明：微架構攻擊的「根」是推測執行 + 共享微架構狀態，不是 x86 特有的；但攻擊的具體操作（clflush 能不能用、TLB 怎麼 flush、page table walker 的細節）都是平台相關的。

## 對比與取捨

| 機制 | 效能收益 | 攻擊面 |
|---|---|---|
| Pipeline | 指令重疊，吞吐量 ×4–10 | Control hazard → 分支預測 → Spectre |
| OoO 執行 | 隱藏記憶體延遲，IPC ×2–4 | 推測窗口讓越界/越權 load 能執行 |
| 分支預測 | 省 15–20 cycles mispred penalty | 可訓練 → Spectre-v1/v2/RSB |
| 推測執行（整體） | 現代 CPU 的吞吐量核心 | 所有 Spectre/Meltdown 家族的根 |
| SMT | 同核心多執行緒，吞吐量 +20–40% | 共享 L1/EU/LB/SB → port contention/MDS |
| LLC 共享 | 多核資料共享，不需跨 DRAM | 跨進程/跨 VM 的 LLC Prime+Probe |
| Store Buffer | store 先完成，不卡後續指令 | store-to-load forwarding 跨 HT 洩漏（MDS） |

## 踩雷集錦

1. **「亂序執行 = 不按程式序執行 = 結果也亂序」**——錯誤直覺：OoO 讓指令亂序完成，以為結果也可能不對。正確認識：ROB 確保 retire（commit）一定按程式序，架構可見的結果完全正確；亂序只是內部執行優化，外部語意不變。

2. **「推測執行 squash 之後所有副作用都消失」**——錯誤直覺：CPU 說「推測錯了、撤銷」，以為一切回到原狀。正確認識：ISA 規範的「撤銷」只保證架構狀態（暫存器、記憶體）；cache、TLB、LFB 等微架構狀態不在承諾範圍內，殘留就是 Spectre/Meltdown 能工作的原因。

3. **「分支預測失敗只是輕微效能損失，安全上沒影響」**——錯誤直覺：misprediction 是效能問題不是安全問題。正確認識：攻擊者刻意製造「對受害者來說預測正確但實際上越界/越權」的情況——不需要 misprediction，反而需要「被 CPU 相信，成功推測執行」。攻擊者是在「利用正確的預測」讓秘密資料進 cache。

4. **「Spectre 只影響有漏洞程式的程序」**——錯誤直覺：以為只有寫錯的程式才被打。正確認識：Spectre-v1 的 gadget（越界讀 + cache covert channel 那幾行）存在於 Linux kernel、V8 JavaScript engine、JVM 等幾乎所有大型 codebase 裡，因為這是完全合法的程式碼樣式，不是 bug；任何有這個樣式的程式都是潛在的 gadget。

5. **「ROB 很大，推測窗口就大，攻擊面也大」**——正確但不完整：ROB=352 確實是上限，但實際推測窗口受 scheduler entries 數量、執行埠瓶頸、記憶體頻寬等限制。攻擊的實際可用窗口通常比 ROB 大小小很多，設計 PoC 時要量測實際竊取到的資料長度，不能靠 ROB 大小直接估算。

6. **「SMT 關掉就解決了 port contention / MDS 問題」**——正確但有代價：關 HT 確實消除了 SMT-dependent 的攻擊面，但不能消除跨進程的 LLC 攻擊（LLC 還是多核共享），且效能損失 ~30–50% 是真實代價，不是所有場景都能接受。

## 進階：再往深一層

- **µop fusion / macro-fusion**：Intel 的 decode 會把某些指令組合（如 `cmp + je`）fusion 成一個 µop，節省 ROB/scheduler 資源——這影響精確計算攻擊 PoC 的執行窗口。Intel Optimization Manual 附錄 A 有完整列表。
- **Memory Ordering Buffer（MOB）與 Memory Disambiguation**：Load Buffer 裡有個猜「這個 load 跟之前的 store 有沒有地址重疊」的機制（memory disambiguation predictor）——猜錯了也要 squash。這是 Speculative Store Bypass（Spectre-v4/SSB）的攻擊根源，Ch 20 會觸及。
- **Prefetcher 的交互**：OoO + prefetcher 一起工作——CPU 一邊推測執行，一邊 L2 prefetcher 在預測下一個 cache miss 並提前抓。Prefetcher 本身也是側信道（Prefetch Side-Channel，Gruss et al. 2016）；且 prefetcher 行為會污染 Prime+Probe 量測，這是為什麼 Ch 0 要關掉 prefetcher。
- **Comet Lake 的 Enhanced IBRS vs 軟體 IBRS**：IBRS（Indirect Branch Restriction Speculation）是 Spectre-v2 的硬體緩解，有兩種模式：「軟體 IBRS」（每次 kernel entry/exit 都執行 WRMSR，很貴）和「Enhanced IBRS」（一次設定常駐，CPU 自動隔離）。本機 `cat /sys/devices/system/cpu/vulnerabilities/spectre_v2` 顯示 `Enhanced / Automatic IBRS`——Comet Lake 有 Enhanced IBRS 支援。

## 動手練習

1. **觀察 OoO 的效果**：寫兩段 C 程式，一段依序存取兩個不相關陣列（`A[i]; B[j]`），另一段把兩個 load 的相依切斷（引入假依賴讓它串行）。用 Ch 0 的 `timed_access` 量測總時間，觀察 OoO 是否把記憶體延遲隱藏掉了。

2. **用 cpuid 確認微架構拓撲**：跑 `cpuid -1 2>/dev/null | grep -E "cache|associativity|sets"` 把 L1/L2/L3 的幾何讀出來，驗算 size = ways × sets × line_size，跟 `/sys` 的讀數比對。

3. **感受分支預測失敗的代價**：寫一個需要預測的迴圈（條件是 random 陣列的值），用 `__rdtscp` 量總計時，比較「固定樣式（easy to predict）」和「random 樣式（hard to predict）」的時間差。粗估 misprediction penalty 是多少 cycles。

4. **觀察推測執行在哪條指令留下痕跡**：看 Ch 14 的 Spectre-v1 PoC（現在先讀，後面詳解）：`array2[array1[x] * 64]` 這行在「分支被 squash」之後，為什麼 `array2` 的某個特定 cache line 還是 hot？對應到你剛學到的哪個 cache 子系統行為？

## 本章重點整理

- 現代 CPU 的效能來自 Pipeline + OoO + 推測執行 + SMT，每個機制都在「預測未來、提前準備資源」。
- **ROB** 確保 retire 按程式序，維持架構語意正確性；**Scheduler** 讓 dispatch 亂序，榨取 IPC。
- 推測執行的推測窗口（~ROB 大小，Comet Lake = 352 µops）在分支條件是 cache miss 時可高達幾百 cycles——這個窗口就是 Spectre gadget 的工作空間。
- ISA 規範承諾 squash 後**架構狀態**（暫存器/記憶體）不可見；但未承諾 cache、TLB、LFB 等**微架構狀態**被清除——這個缺口是瞬態執行攻擊家族的統一根源。
- SMT 讓同核心兩條 HT 執行緒共享 L1-D、執行埠、Load/Store Buffer——這些共享結構都是 Part 4 攻擊的基礎。
- 分支預測器（BTB/BHT/RSB）可以被攻擊者「訓練」，讓受害者的推測路徑走到攻擊者指定的 gadget——Spectre-v1/v2 的核心手段。

## 自我檢核

- [ ] 畫出一條 `load [memory]` 指令從 fetch 到 commit 的完整路徑，標出 ROB、Scheduler、Load Buffer、L1-D cache 各在哪個位置。
- [ ] 解釋「為什麼一個被 squash 的 load 指令可以讓 cache 裡的資料變 hot」——從 ISA 承諾和微架構實作的角度分別說明。
- [ ] 分辨 BTB、BHT、RSB 各自預測什麼；Spectre-v1、Spectre-v2、Spectre-RSB 各自利用哪一個？
- [ ] i7-10700 有 8 個物理核心 × 2 HT = 16 個邏輯 CPU。CPU 0 和 CPU 1 在同一個物理核心，他們共享哪些結構、不共享哪些？這對攻擊有什麼意義？
- [ ] 為什麼 OoO 執行「隱藏記憶體延遲」這件事，反而讓推測執行的安全窗口變大了？（提示：想一下什麼情況下分支條件要等很久才能確認）

## 延伸閱讀

### 官方文件

- **[Intel 64 and IA-32 Architectures Optimization Reference Manual](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)** — Intel
  - **讀哪裡**：Chapter 2（Out-of-Order Execution Overview）和 Appendix B（Processor Parameters）的 Comet Lake (Ice Lake client) 那一行：ROB size、Scheduler entries、執行埠數。
  - **學什麼**：精確的微架構數字；理解 CPU 設計師的視角。
  - **前提**：知道 pipeline stages 的概念即可。

- **[Agner Fog: Microarchitecture Manual](https://agner.org/optimize/microarchitecture.pdf)** — Agner Fog
  - **讀哪裡**：Intel Sunny Cove / Comet Lake 那一節（search "Sunny Cove"）。
  - **學什麼**：比 Intel 文件更清楚的 OoO、Scheduler、執行埠、分支預測器描述；附上每個指令的 latency/throughput 表。
  - **為什麼值得**：這是做微架構效能分析（和攻擊 PoC 設計）最常引用的第三方文件，準確度高。

### 論文

- **[Spectre Attacks: Exploiting Speculative Execution](https://spectreattack.com/spectre.pdf)** — Kocher et al., IEEE S&P 2019
  - **讀哪裡**：Section II（Background）——這一節對 OoO 和分支預測器的描述正是本章的攻擊者視角。
  - **學什麼**：學術論文怎麼把微架構知識和攻擊設計串起來；本章學的概念在那裡直接被用上。

- **[Out-of-Order Execution and Its Implications for Speculative Attack Research](https://arxiv.org/abs/1902.08698)** — 未必存在的引用，請查 USENIX ATI/Oakland 相關綜述
  - 若要深入 OoO 和推測執行的安全含義，可搜尋「speculative execution security model」找 Mosier/Cauligi 等人 2021–2023 年在 S&P/CCS 的形式化分析論文。

### 工具

- **[uarch-bench](https://github.com/travisdowns/uarch-bench)** — Travis Downs
  - **這是什麼**：量測真實 CPU 的 latency/throughput/ROB 行為的 microbenchmark 套件。
  - **讀哪裡**：README 的 "branch misprediction" 和 "out-of-order window" 部分。
  - **為什麼值得**：不靠 Intel 文件，直接從硬體量出 ROB 大小和 misprediction penalty——驗證你的理論理解。

- **[llvm-mca](https://llvm.org/docs/CommandGuide/llvm-mca.html)** — LLVM 工具鏈
  - **這是什麼**：靜態分析指令序列在特定微架構上的執行埠壓力和 throughput 預測。
  - **讀哪裡**：跑 `echo "add rax, 1; add rbx, 2" | llvm-mca --mcpu=skylake` 看 pipeline 視覺化輸出。
  - **和本章的關聯**：能看到「這段 gadget 在哪個執行埠、會卡多久」，對設計 port contention 攻擊（Ch 26）有用。

打穩了微架構概念之後，下一章進入 cache 的詳細幾何——set-associative 結構、index/tag/offset 位元拆解、LLC slice——這是所有 cache 攻擊在建 eviction set 時需要的精確地圖。

→ [Ch 3 快取階層與 set-associative 組織](./03-cache-hierarchy-organization.md)
