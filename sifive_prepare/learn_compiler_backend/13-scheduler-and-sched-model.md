# Ch 13 — Scheduler 與 scheduling model

> 目標：理解 LLVM scheduler 在做什麼、scheduling model 如何描述硬體 pipeline、為什麼 SiFive 每個 core 都要一份自己的 scheduling model。這章直接對應 job spec 的「work with benchmarking teams」。

## Scheduling 是什麼

**Scheduling**：重排指令順序、讓硬體 pipeline 跑得更快。

考慮：

```asm
lw  t0, 0(a0)      # load, latency 5 cycles
add t1, t0, t2     # 依賴 t0, 要等 load 完
add t3, t4, t5     # 獨立指令
```

Naive scheduling：

```
Cycle 0: lw  t0, 0(a0)
Cycle 1: stall (waiting for lw)
Cycle 2: stall
Cycle 3: stall
Cycle 4: stall
Cycle 5: add t1, t0, t2
Cycle 6: add t3, t4, t5
Total: 7 cycles
```

Better scheduling（把獨立指令插進 load 的 latency gap）：

```
Cycle 0: lw  t0, 0(a0)
Cycle 1: add t3, t4, t5     # fills the bubble
Cycle 2: stall
Cycle 3: stall
Cycle 4: stall
Cycle 5: add t1, t0, t2
Total: 6 cycles
```

省一個 cycle。**scheduler 決定執行順序讓 pipeline 飽和**。

## LLVM 的 scheduler

LLVM 有兩套主要 scheduler：

### 1. MachineScheduler (MISched)

- 現代主流
- 跑**兩次**：pre-RA 一次、post-RA 一次
- 用 DAG（per-region）+ priority queue
- 支援 bidirectional scheduling（top-down + bottom-up）
- 用 scheduling model 做 cost 估算

### 2. PostRAScheduler (legacy)

- 較舊
- 只 post-RA
- 新 target 多用 MISched

RISC-V 用 **MISched**。

## Scheduling model：描述硬體

Scheduler 需要知道：

- 每條指令佔用哪些硬體 resource？
- 每條指令的 latency 多少？
- 有幾個 issue slot（每 cycle 發射多少條指令）？

這些資訊寫在 **Scheduling Model**（`.td` 檔）。每個 target / core 有自己一份。

## RISC-V 的 scheduling models

```
llvm/lib/Target/RISCV/
    RISCVSchedule.td          ← generic SchedRead/Write 定義
    RISCVScheduleV.td         ← Vector extension
    RISCVSchedRocket.td       ← Rocket core (學術 reference)
    RISCVSchedSiFive7.td      ← SiFive 7 系列
    RISCVSchedSiFiveP400.td   ← SiFive P400
    RISCVSchedSiFiveP600.td   ← SiFive P600
    RISCVSchedSiFiveP800.td   ← SiFive P800 (high-performance)
    RISCVSchedSyntacoreSCR1.td
    RISCVSchedXiangShanNanHu.td ← XiangShan (中國)
    ...
```

每個 `Sched*.td` 描述該 core 的 pipeline。

**每加一個新 core 要寫一份 scheduling model**。這是 SiFive compiler 工程師的定期工作。

## SchedRead / SchedWrite

先看 generic 定義：

```tablegen
// RISCVSchedule.td
def WriteIALU    : SchedWrite;  // Integer ALU
def WriteIMul    : SchedWrite;  // Integer multiply
def WriteIDiv    : SchedWrite;  // Integer divide
def WriteLDB     : SchedWrite;  // Load byte
def WriteLDH     : SchedWrite;  // Load halfword
...

def ReadIALU    : SchedRead;
def ReadIMul    : SchedRead;
...
```

每條指令被標示「我是什麼 SchedWrite / SchedRead」：

```tablegen
class ALU_rr<...>
    : RVInstR<...>, Sched<[WriteIALU, ReadIALU, ReadIALU]> {
    // WriteIALU = 輸出屬於 IALU 類
    // ReadIALU, ReadIALU = 兩個輸入都屬於 IALU 類
}

class Load_ri<...>
    : RVInstI<...>, Sched<[WriteLDB, ReadLDB]> {
}
```

**這些 SchedWrite/Read 是抽象 label**，具體 latency 在每個 core 的 scheduling model 定義。

## 具體 core 的 scheduling model

```tablegen
// RISCVSchedSiFive7.td (簡化)
def SiFive7Model : SchedMachineModel {
    let MicroOpBufferSize = 0;
    let IssueWidth = 2;           // 每 cycle 2 條
    let LoadLatency = 3;
    let MispredictPenalty = 3;
    ...
}

// Define the resources
def SiFive7IntPipe : ProcResource<2>;   // 2 integer pipes
def SiFive7MulDiv : ProcResource<1>;    // 1 mul/div unit

// Map SchedWrite to resources + latency
let SchedModel = SiFive7Model in {

def : WriteRes<WriteIALU, [SiFive7IntPipe]> {
    let Latency = 1;
}

def : WriteRes<WriteIMul, [SiFive7MulDiv]> {
    let Latency = 3;
    let ResourceCycles = [3];
}

def : WriteRes<WriteIDiv, [SiFive7MulDiv]> {
    let Latency = 34;            // 除法慢
    let ResourceCycles = [34];
}

def : ReadAdvance<ReadIALU, 0>;
}
```

**這段告訴 scheduler**：

- SiFive 7 每 cycle 可發射 2 條指令
- `WriteIALU` 用 IntPipe、latency 1
- `WriteIMul` 用 MulDiv unit、latency 3、佔 3 cycle（unit 忙 3 cycle）
- `WriteIDiv` latency 34、佔 34 cycle

**Scheduler 據此決定最佳排序**。

## `ResourceCycles` vs `Latency`

- **Latency**：指令 def 出來後 dependent instruction 要等幾 cycle 才能 use
- **ResourceCycles**：指令佔用 functional unit 幾 cycle（不能 dispatch 另一條同 unit 指令）

一條 3-cycle multiply：

```
cycle 0: dispatch MUL (latency 3, resource cycles 3)
cycle 1: MUL 繼續佔 unit
cycle 2: MUL 繼續佔 unit
cycle 3: MUL 結果 ready、unit 釋放
cycle 4: 可以 dispatch 另一條 MUL
```

若另一條 MUL 在 cycle 0 dispatch → unit conflict → 延遲到 cycle 3。

## Schedulable vs Unschedulable model

兩個選項：

- **InOrder model**：simple pipeline、scheduling 決定執行順序 = 實際順序
- **OutOfOrder model**：有 reorder buffer、scheduler 是 "hint"、實際重排在硬體

大部分 SiFive 低階 core 是 InOrder。高階 core（P800）是 OoO。

```tablegen
def SiFive7Model : SchedMachineModel {
    let CompleteModel = 1;      // 我完整模擬這個 core
    let MicroOpBufferSize = 0;   // InOrder (OoO 會設 > 0)
    ...
}
```

## 為什麼每個 core 要一份 sched model

因為每個 core 的 microarchitecture 不同：

- SiFive P270 (in-order): 簡單 2-issue
- SiFive P670 (OoO): 複雜 4-issue + reorder buffer
- Rivos Veyron: 超大 8-issue

**同樣指令的 latency 完全不同**。`WriteIMul` 在 P270 可能 5 cycle、P670 3 cycle。

sched model 是 per-core 寫的。

## SiFive job spec 的「performance analysis」對應什麼

Job spec："Work with SiFive's benchmarking teams to analyze performance results and suggest new compiler optimizations."

典型工作：

1. benchmarking team 跑 SPEC CPU、Coremark，量化每個 benchmark 的 IPC、cache miss 等
2. 發現某個 benchmark 在新 core 上表現差
3. Profile 找 hot function
4. 看產生的 asm，發現 sched 沒排好、或 register pressure 高、或某個 pattern 沒優化
5. **改 sched model 或加新 DAGCombine rule**
6. 重測、驗證改進

**step 5 是你改 `.td` 或寫 pass 的具體時機**。

## 看 scheduling decision

```bash
llc -debug-only=machine-scheduler hello.ll 2>&1 | head -100
```

輸出：

```
*** Analyze Region: bb.0.entry
SU(0):   %0 = COPY $x10
  # ZONE_TOP
SU(1):   %1 = COPY $x11
  # ZONE_TOP
SU(2):   %2 = ADDW %0, %1
  # Latency: 1
  # Successors: (no successors)
...

Scheduling SU(2) Height=1
```

每個 node (SU = ScheduleUnit)、latency、dependency、執行順序。

## MIR 前後 scheduler 都跑

```
ISel → MIR
  ↓
[pre-RA scheduler] MachineScheduler   ← register pressure heuristic
  ↓
Register Allocation
  ↓
[post-RA scheduler] MachineScheduler   ← spill-aware scheduling
  ↓
Final MIR
```

pre-RA 跑時 scheduler 盡量平衡 register pressure（免得 RA spill）。post-RA 跑時 physical reg 已固定、處理真實 pipeline 細節。

## 一個寫 scheduling model 的例子

假設 SiFive 推出新 core「P350」，你要寫它的 sched model。流程：

1. **拿 core 的 pipeline spec**：硬體團隊給你「每個 functional unit 幾 cycle、幾條 pipe、scheduling priority」
2. **複製現有最接近的 sched model** 當 baseline（e.g., SiFiveP400）
3. **調整 IssueWidth、LoadLatency、MispredictPenalty** 等 global 參數
4. **針對 custom extension** 加 SchedWrite/ReadAdvance
5. **跑 SPEC benchmark** 驗證
6. **iterate**：哪個 benchmark 結果跟 naive expectation 不符 → 查 sched 指令分布 → 調
7. **文件化**：註解讓下個工程師看懂

這過程幾週到幾個月。**多花時間在 step 5-6**（實測+iterate）。

## 寫自訂 scheduler strategy

LLVM 允許 target 自訂 scheduler：

```cpp
// 舊做法
class RISCVMachineSchedStrategy : public MachineSchedStrategy {
    ...
};

// 新做法 (通常用 default GenericScheduler 即可)
```

RISC-V 目前沒太多自訂 strategy，主要靠 sched model + generic scheduler。

## Pre-RA scheduling 的 register pressure 平衡

Pre-RA scheduler 的一個目標：**不讓太多 virtual reg 同時 live**。

考慮：

```
%a = LD ...
%b = LD ...
%c = LD ...
%d = ADD %a, %b
%e = ADD %c, %d
```

Schedule as-is：4 個 live 同時（`%a`, `%b`, `%c`, `%d`）。

Reorder：

```
%a = LD ...
%b = LD ...
%d = ADD %a, %b     ; free %a, %b
%c = LD ...
%e = ADD %c, %d     ; free %c, %d
```

只 3 個 live。少 spill 機會。

這是 pre-RA scheduling 的 heuristic 之一。

## Vector instruction 的 scheduling

RVV 指令的 scheduling 特別複雜：

- Vector length 影響 latency
- vsetvl 之間 config 不能隨意 reorder
- Vector vs scalar unit 的 contention

`RISCVScheduleV.td` 定義一堆 vector-specific SchedWrite：

```tablegen
def WriteVLoad    : SchedWrite;
def WriteVStore   : SchedWrite;
def WriteVIALUV   : SchedWrite;   // vector ALU (vector-vector)
def WriteVIALUX   : SchedWrite;   // vector ALU (vector-scalar)
...
```

每個 core 的 sched model 再 map 到具體 resources。

## 常見誤會

1. **「scheduling model 是 optional」**：不算。沒有的 target compile 能跑，但效能差、benchmark 看會發現。
2. **「sched model 寫對 1 次就 work」**：不。反覆 iterate，測+調。
3. **「latency 越低越好」**：不是 sched model 設低 latency 就快。sched model 是描述事實，不是優化。寫錯反而誤導 scheduler。
4. **「每個 extension 不用寫 sched」**：要。沒寫的 fallback 用 default、但效能差。
5. **「sched 跟 RA 獨立」**：深度互動。pre-RA scheduling 影響 RA 結果、RA 改 liveness 影響 post-RA scheduling。

## 動手練習

1. 讀 `RISCVSchedSiFive7.td`，列出它定義的所有 functional unit 跟 SchedWrite。
2. 用 `-debug-only=machine-scheduler` 看簡單 function 的 scheduling。
3. 在 `RISCVSchedSiFive7.td` 改一個 latency（e.g., `WriteIMul` 從 3 改 10），重 build、看 `-mcpu=sifive-s76 hello.c` 的 asm 差異。
4. 查 SiFive 某 core 的 datasheet，對比 `.td` 的 sched model 是否對。
5. 寫一個計算密集的 C function，`-mcpu=sifive-p670 -O3 -S` 跟 `-mcpu=sifive-u74` 對比 asm，看 scheduling 差異。

## 自我檢核

- [ ] 我能解釋 scheduler 為什麼存在、做什麼
- [ ] 我知道 Latency 跟 ResourceCycles 的分別
- [ ] 我能讀 `SchedRes` / `ReadAdvance` / `WriteRes` TableGen
- [ ] 我知道 pre-RA 跟 post-RA scheduling 的差異
- [ ] 我能描述「客戶報告 benchmark 差」時的改進流程

Part 4 結束。下一章進 Part 5 —— intrinsic 的 codegen 全流程。

→ [Ch 14 Intrinsic → codegen 全流程](./14-intrinsic-codegen.md)
