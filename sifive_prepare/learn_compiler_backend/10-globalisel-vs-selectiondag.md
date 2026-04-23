# Ch 10 — GlobalISel vs SelectionDAG：為什麼在遷移

> 目標：理解 LLVM 為什麼在開發新的 ISel framework（GlobalISel）、它跟 SelectionDAG 差在哪、RISC-V target 的遷移狀態。面試被問到「為什麼選 SelectionDAG / GlobalISel」能答有深度。

## SelectionDAG 的限制

回顧 Ch 4-6：SelectionDAG 每個 basic block 獨立的 DAG。這帶來幾個問題：

### 限制 1：跨 BB 優化做不到

```llvm
bb1:
  %a = add i32 %x, %y
  br label %bb2
bb2:
  %b = add i32 %x, %y   ; 跟 bb1 的 %a 一樣
  ...
```

SelectionDAG 在 bb2 看不到 bb1 的 DAG。無法 CSE 掉 `%b`。

IR 層已經做了（GVN），但 backend-specific optimization（target-specific CSE / combine）受限。

### 限制 2：Compile time

每個 BB 建 DAG → combine → legalize → select。DAG 建立 / 維護 overhead 實際上不小。

### 限制 3：TableGen 表達力不足

某些 pattern 在 TableGen 難寫：

- 條件依賴 runtime 值
- 需要跨 BB context
- 複雜的 addressing mode with side effect

要靠 C++ 補。但補得越多、越混亂。

### 限制 4：Debug 困難

SelectionDAG 的中間狀態多、錯誤訊息難讀。新手踩雷率高。

## GlobalISel 的設計

**GlobalISel (Global Instruction Selector)** 是 LLVM 從 2015 開始開發的新 framework，目標解決上述問題。

核心設計：

### 1. Whole function 一起 lower

不分 BB。直接 IR → MIR（with generic opcode）→ MIR（with target opcode）。

### 2. Pass-based pipeline

IR → MIR 的 lowering 是一連串 **GIPass**（類似 FunctionPass）：

```
IRTranslator       ; IR → MIR (generic opcode)
Legalizer          ; legalize types + ops
RegBankSelect      ; 分配 register bank
InstructionSelect  ; select final target instruction
```

每個 pass 可以獨立 debug / 優化。

### 3. Generic MIR (gMIR)

中間形式是 MIR 的 generic opcode：`G_ADD`, `G_SUB`, `G_LOAD`, `G_STORE`, `G_ICMP`...

```
%1:_(s32) = G_ADD %2, %3
```

比 SelectionDAG 的 `ISD::ADD` 接近 MIR，轉換更直接。

### 4. 無 DAG 中間步驟

直接操作 linear MIR，不建 DAG。Compile 快。

## GlobalISel vs SelectionDAG 的 pipeline 對比

**SelectionDAG**：

```
IR
 ↓ SelectionDAGBuilder (per-BB)
SDAG
 ↓ DAGCombine
 ↓ Type legalize
 ↓ DAGCombine
 ↓ DAG legalize
 ↓ DAGCombine
 ↓ Instruction selection (TableGen pattern)
MachineInstr (still in DAG form)
 ↓ Scheduler
MIR
```

**GlobalISel**：

```
IR
 ↓ IRTranslator
Generic MIR (gMIR)
 ↓ Legalizer
Legal gMIR
 ↓ RegBankSelect
 ↓ InstructionSelect
Target MIR
```

GlobalISel 少了 DAG 這層。Lowering 更直接。

## 誰在用 GlobalISel

2026 狀態：

- **AArch64 (ARM 64-bit)**：**-O0 / -O1 預設 GlobalISel**，-O2+ 仍用 SelectionDAG 或混用
- **AMD GPU**：部分用 GlobalISel
- **RISC-V**：GlobalISel 支援**實驗性**、不穩定，主力仍 SelectionDAG
- **X86**：GlobalISel 支援有限

**GlobalISel 的成熟度是 per-target 議題**。AArch64 走最快。

## RISC-V 的 GlobalISel 狀態

進度（2026 時點）：

- `-O0`/`-O1` 基本 RV64I operation 可以走 GlobalISel
- M/A extension 部分支援
- F/D extension 初步
- V extension **幾乎沒有**
- custom extension 幾乎沒有

**SiFive / T-Head 等廠商仍主力開發 SelectionDAG path**，GlobalISel 是研究+長期計畫。

RISC-V GlobalISel 完整路線圖在 LLVM discourse 討論：<https://discourse.llvm.org/t/riscv-globalisel-status/>。

## 開 GlobalISel 跑看看

```bash
llc -mtriple=riscv64 -global-isel hello.ll -o hello.s
```

可能會 fallback 到 SelectionDAG（如果 GlobalISel 不支援）：

```
warning: unable to translate instruction: ... falling back to SelectionDAG
```

這是 normal。**GlobalISel 目前在 RISC-V 是「能跑就跑、跑不動 fallback」**。

## GlobalISel 的資料結構：MIR

MIR（Machine IR）是 LLVM 的 target-specific IR。GlobalISel 跟 SelectionDAG 最終都產 MIR，但 GlobalISel 一開始就在 MIR 上工作。

看 MIR dump：

```bash
llc -stop-after=finalize-isel hello.ll -o hello.mir
cat hello.mir
```

範例：

```mir
---
name:            foo
tracksRegLiveness: true
body:             |
  bb.0:
    liveins: $x10, $x11
    %0:gpr = COPY $x10
    %1:gpr = COPY $x11
    %2:gpr = ADD %0, %1
    $x10 = COPY %2
    PseudoRET implicit $x10
...
```

看起來接近 assembly，但仍有 SSA virtual register（`%0`, `%1`, `%2`）等待 register allocation。

## GlobalISel 的 legalize 差在哪

SelectionDAG legalize 用 `setOperationAction`。GlobalISel 用 `LegalizerInfo`：

```cpp
// RISCVLegalizerInfo.cpp
RISCVLegalizerInfo::RISCVLegalizerInfo(const RISCVSubtarget &ST) {
    using namespace TargetOpcode;

    getActionDefinitionsBuilder(G_ADD)
        .legalFor({s32, s64})
        .clampScalar(0, s32, s64);

    getActionDefinitionsBuilder(G_MUL)
        .legalFor({s32, s64})
        .widenScalarToNextPow2(0);

    getActionDefinitionsBuilder({G_SDIV, G_UDIV, G_SREM, G_UREM})
        .legalFor({s32, s64})
        .customFor({s8, s16});    // custom lower

    // ...
}
```

宣告式設定。`legalFor` / `clampScalar` / `widenScalar` 等 combinator 表達策略。

比 SelectionDAG 的 `setOperationAction` 抽象更高。

## InstructionSelect：仍用 TableGen

GlobalISel 的 instruction selection 也用 TableGen pattern，但 pattern 是**寫在 gMIR 層級**：

```tablegen
// 舊 SelectionDAG pattern:
def : Pat<(add GPR:$rs1, GPR:$rs2), (ADD ...)>;

// GlobalISel 也認這個 pattern（大部分）
```

LLVM 設計上讓 pattern 盡可能共用。兩套 ISel 都能 consume。

但某些 SelectionDAG 專屬 pattern 在 GlobalISel 跑不起來（因為 DAG-specific opcode）。所以有些 pattern 要寫兩版。

## 為什麼遷移這麼慢

GlobalISel 2015 提出、2018 AArch64 開始用。**10 年了仍沒 full 取代 SelectionDAG**。原因：

1. **Feature 完整度**：SelectionDAG 幾千條 pattern、GlobalISel 要重建同樣成熟度要幾千人年
2. **Target 特殊需求**：vector、custom instruction 在 SelectionDAG 早有完整支援
3. **RL 成熟**：SelectionDAG 的 DAGCombiner 是幾十年優化累積
4. **沒爆發式收益**：-O0 compile time 提升有感，-O2 差異不大

**實務結論**：短期（5 年）內 SelectionDAG 仍主流，但新 code 建議也 think about GlobalISel compatibility。

## 對 compiler 工程師的意義

如果你進 SiFive：

- **日常工作 SelectionDAG**：所有 production 路徑
- **需要了解 GlobalISel 基本**：遇到 fallback 要會 debug
- **新 extension 要 GlobalISel 支援嗎？**：看 target 優先順序。2026 時點多數 focus SDAG

面試可能問：「如果你加一條 custom instruction、需要同時支援兩套 ISel 嗎？」

答：「主力 SelectionDAG（pattern）。GlobalISel 先加最小 legalize + manual select，不鋪 pattern。等 GlobalISel 在 RISC-V 成熟再補。」

這是務實答案。

## GlobalISel 的長期願景

LLVM core team 的願景：

- `-O0`: GlobalISel (快 compile)
- `-O2`/`-O3`: GlobalISel 或共用 framework

最終 SelectionDAG 被砍。但不會快發生。**研究 GlobalISel 原始碼是長期投資**。

## GlobalISel 讀源碼入口

```
llvm/include/llvm/CodeGen/GlobalISel/
    IRTranslator.h           ; IR → gMIR
    Legalizer.h
    LegalizerHelper.h
    RegBankSelect.h
    InstructionSelector.h

llvm/lib/CodeGen/GlobalISel/*.cpp       ; generic
llvm/lib/Target/RISCV/GISel/*.cpp       ; RISC-V 專屬
llvm/lib/Target/AArch64/GISel/*.cpp     ; AArch64（最成熟 reference）
```

**讀 AArch64 GISel** 看成熟版的樣子，再看 RISC-V 的狀態。

## 一個簡單 GlobalISel pattern 範例

TableGen 的 pattern match 在 GlobalISel 基本語法相同（LLVM intention），但有些專屬：

```tablegen
// G_ADD pattern
def : GINodeEquiv<G_ADD, add>;
```

`GINodeEquiv` 聲明「`G_ADD` 等價於 IR 的 `add`」。然後 SelectionDAG 的 pattern `def : Pat<(add ...)>` 也能被 GlobalISel 用。

還有些 GlobalISel-only pattern：

```tablegen
def : GICombineRule<
    (defs root:$dst),
    (match (G_ADD i32:$src, (G_MUL i32:$a, i32:$b)):$dst),
    (apply (CUSTOM_MADD i32:$dst, i32:$a, i32:$b, i32:$src))>;
```

Pattern-based combine rule。

## 常見誤會

1. **「GlobalISel 總是比 SelectionDAG 快」**：-O0 是、-O2 不一定。
2. **「GlobalISel 一定會完全取代」**：長期預期是，但沒時間表。
3. **「一個 target 要選一套 ISel」**：可以混用。-O0 走 GlobalISel、-O2 走 SelectionDAG 是常見策略。
4. **「我改 ISel pattern 兩邊都生效」**：部分。共用 pattern 是，GISel-only 或 SDAG-only 的 pattern 不互通。
5. **「RISC-V 不用學 GlobalISel」**：錯。長期投資。新 extension 的 GlobalISel 支援成本越來越被要求。

## 動手練習

1. `llc -global-isel hello.ll` 跑一個簡單 function，看能不能成功。
2. 讀 `RISCVLegalizerInfo.cpp` 前 100 行，對比 SelectionDAG 的 `RISCVISelLowering.cpp` constructor。
3. 用 `-global-isel-abort=0` 讓 fallback 靜默、看哪些 op 成功走 GISel。
4. 讀 AArch64 的 `AArch64InstructionSelector.cpp` 的 `select()` function（大約 500 行 skim），理解 GISel-style select 長什麼樣。
5. 查 LLVM discourse 上 RISC-V GlobalISel 最新 status thread。

## 自我檢核

- [ ] 我能解釋 GlobalISel 跟 SelectionDAG 的架構差異
- [ ] 我知道 RISC-V 在 GlobalISel 的遷移狀態
- [ ] 我能用 `-global-isel` + `-debug` 看 GISel 執行
- [ ] 我了解為什麼 AArch64 走得比 RISC-V 快
- [ ] 我能在面試回答「新 extension 要不要支援 GlobalISel」的取捨

下一章深入 MIR 這個 backend 最重要的資料結構。

→ [Ch 11 Machine IR (MIR)](./11-machine-ir.md)
