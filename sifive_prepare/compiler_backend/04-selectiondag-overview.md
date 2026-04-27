# Ch 4 — SelectionDAG 總論

> 目標：理解 SelectionDAG 是什麼、為什麼 LLVM backend 要用 DAG 而不是 linear IR、以及 DAG 怎麼從 IR 轉換而來。這是 backend pipeline 的起點，也是 TableGen pattern matching 的戰場。

## 為什麼要 SelectionDAG

進入 backend 後，LLVM 不直接對 IR 做 instruction selection，而是先轉成 **SelectionDAG**。原因：

- **Per-basic-block DAG 更適合 pattern matching**：local optimization 可以 context-free 地做
- **Target lowering 有彈性**：每個 target 可以自訂某些 operation 的表示
- **依賴關係清晰**：DAG 邊表達 data/chain dependency，pattern match 可以直接 walk
- **歷史**：SelectionDAG 是 2005 時代 LLVM 的第一個 backend codegen 框架，後來才有 GlobalISel 這個新選項

Ch 10 會講 GlobalISel 為什麼要取代 SelectionDAG。但 2026 時點 **RISC-V 主力仍是 SelectionDAG**，所以先把這套打穿。

## DAG 是什麼

**SelectionDAG**：每個 basic block 一個 DAG。節點 = 指令或 operation，邊 = dependency。

```
    store
      │
    add
    /  \
  load  const 1
   │
  arg
```

葉子是 input（argument、constant、load 來源）、根是 output（return value、store）。

**DAG 而非 tree**：因為一個 value 可能被多個 consumer 用（share）。

## DAG 的邊類型

```
SDValue:  代表一個節點的某個 output
          - "Data" edge: 資料流
          - "Chain" edge: 順序（memory / side effect）
          - "Glue" edge: 實體綁定（不能被拆開）
```

「Chain」很重要。例：

```
store1 → store2   (chain: store2 必須在 store1 後)
store2 → load3    (chain: load3 必須在 store2 後)
```

這確保 memory 操作順序不被亂序。pure arithmetic 不用 chain。

## 從 IR 到 DAG：SelectionDAGBuilder

每個 function 的 codegen 從 `SelectionDAGBuilder::visit(Instruction &I)` 開始，逐條 IR 指令 → SDNode：

```cpp
// Simplified
void visitAdd(const User &I) {
    SDValue op1 = getValue(I.getOperand(0));
    SDValue op2 = getValue(I.getOperand(1));
    SDValue result = DAG.getNode(ISD::ADD, DL, VT, op1, op2);
    setValue(&I, result);
}
```

`ISD::ADD` 是 SelectionDAG 的 **generic opcode**。類似 IR 的 `add`，但 backend-agnostic。

每條 IR 指令 map 到 1-N 個 SDNode。

## ISD opcode：target-neutral 的中介

`ISD::` 命名空間有幾百個 opcode：

```
ISD::ADD / SUB / MUL / SDIV / UDIV      ; 算術
ISD::AND / OR / XOR / SHL / SRL / SRA   ; 邏輯
ISD::LOAD / STORE                        ; memory
ISD::BR / BRCOND                         ; control
ISD::SELECT / SELECT_CC                  ; conditional
ISD::SETCC                               ; 比較
ISD::CALLSEQ_START / CALLSEQ_END         ; call setup
ISD::BUILD_VECTOR / EXTRACT_VECTOR_ELT  ; vector
ISD::BITCAST / ZERO_EXTEND / SIGN_EXTEND; type conversion
```

SelectionDAG 的 **canonical 形式**就是這些 generic ISD node。Target-specific opcode（例：`RISCVISD::CALL`）在之後的階段才出現。

## target-specific ISD

每個 target 定義自己的 ISD opcode（不跟 generic 衝突的 range）：

```
llvm/lib/Target/RISCV/RISCVISelLowering.h
namespace RISCVISD {
    enum NodeType {
        FIRST_NUMBER = ISD::BUILTIN_OP_END,
        CALL,           // function call
        RET_GLUE,        // return
        SELECT_CC,       // conditional select
        BR_CC,           // conditional branch
        BuildPairF64,
        SplitF64,
        ...
    };
}
```

這些是 RISC-V 的「特殊語義」—— 例：`RISCVISD::CALL` 表示 RISC-V 的 function call（不是 generic 的 `ISD::CALL`，已被 lower 成 target 版本）。

## Chain 跟 Glue 的管理

```
   [prev memory op]
        │ chain
        ▼
       load
        │ chain + data
        ▼
      store
        │ chain
        ▼
    [next memory op]
```

`getNode(ISD::LOAD, ...)` 回傳一個 SDNode，它有多個 output：

- output 0：資料 value
- output 1：chain

Consumer 用 `SDValue(Node, 0)` 拿資料、`SDValue(Node, 1)` 拿 chain。

Glue 比較稀有：表示「這兩個 node 必須相鄰，別插東西中間」。例：condition code setup + branch 可能用 glue 綁。

## Lowering：IR 到 DAG 的 target-specific 部分

Generic 的 IR → DAG 是共用 code。但有些 IR 指令需要 target-specific lowering：

- `call`：ABI 決定 arg 怎麼擺（RISC-V lp64 vs lp64d）
- `ret`：return value 怎麼放
- `va_start` / `va_arg`：varargs 處理
- Atomic：lower 到具體的 LR/SC 或 AMO

這些在 `RISCVISelLowering.cpp` 的 `LowerXxx` function 裡。

### 範例：LowerCall

```cpp
// 簡化
SDValue RISCVTargetLowering::LowerCall(CallLoweringInfo &CLI, SmallVectorImpl<SDValue> &InVals) const {
    // 1. 分析 ABI: 每個 arg 放在哪個 reg or stack
    CCState CCInfo(...);
    CCInfo.AnalyzeCallOperands(CLI.Outs, CCAssignFnForCall(...));

    // 2. Generate argument copy (to regs or stack)
    // ...

    // 3. Emit call node
    SDValue Callee = CLI.Callee;
    Chain = DAG.getNode(RISCVISD::CALL, DL, ..., Callee, ...);

    // 4. Copy return value from result reg to SDValue
    // ...
    return Chain;
}
```

**ABI 邏輯寫在這裡**。`learn_riscv` Ch 2 的 `a0..a7` / soft-float vs hard-float 全部對應到這個 function 的行為。

## DAG 的 visualization

```bash
llc -view-dag-combine1-dags hello.ll
```

會開 GUI 顯示 DAG。需要 graphviz。字體看起來有點雜但對 debug 很有用。

Terminal alternative：

```bash
llc -debug-only=isel hello.ll 2>&1 | head -200
```

會 dump DAG 的 text form。

## DAG combining

DAG 初步建好後，LLVM 跑多輪 **DAGCombiner**：**類似 InstCombine 但在 DAG 層做的 peephole**。規則類似：

```
(add x, 0)       → x
(shl x, 0)       → x
(zext (trunc x))  → x (if bits match)
...
```

DAGCombiner 的 rule 寫在 `DAGCombiner.cpp`（~20000 行）+ target-specific 的 DAGCombine 擴充（`RISCVISelLowering.cpp`）。

DAG combining 跑多輪，pattern → simpler pattern → ...

## Target DAGCombine 範例

RISC-V 的 DAGCombine 有很多 pattern：

```cpp
// RISCVISelLowering.cpp (簡化)
SDValue RISCVTargetLowering::PerformDAGCombine(SDNode *N, DAGCombinerInfo &DCI) const {
    switch (N->getOpcode()) {
    case ISD::ADD:
        return performADDCombine(N, DCI);
    case ISD::MUL:
        return performMULCombine(N, DCI);
    ...
    }
}

// 例：把 a + (b * 3) 改成 a + (b << 1) + b
SDValue performADDCombine(...) {
    // 如果其中一個 operand 是 mul by 3 → rewrite
    ...
}
```

這就是 RISC-V backend 專屬的優化。

## Lowering 的 legality

有些 generic ISD node RISC-V 不原生支援：

- `ISD::CTTZ` (count trailing zeros)：如果沒 Zbb 擴充 → 要 lower 成 software sequence
- `ISD::BSWAP`：沒 Zbb 的 `rev8` → lower
- `ISD::UMUL_LOHI`：需要 multi-step

**Legalization 處理這個**。Ch 5 專講。

## Codegen pipeline 再看

```
LLVM IR (function)
     ↓ SelectionDAGBuilder
Initial DAG (basic-block-local)
     ↓ DAG combining (multiple rounds)
Combined DAG
     ↓ Type legalization
     ↓ DAG combining
     ↓ DAG legalization
     ↓ DAG combining
Legalized DAG
     ↓ Instruction selection (TableGen pattern match)
MachineInstr (still in DAG form)
     ↓ Scheduling + emit
MachineBasicBlock + MachineInstr
     ↓
MIR
```

幾個階段之間穿插 DAG combining 清 garbage。

## 每個 basic block 獨立 DAG

**重要限制**：SelectionDAG 是 per-basic-block。跨 BB 的優化做不到（在 DAG 層）。

跨 BB 優化靠：

- IR 層（之前跑的 GVN / LICM 等）
- MIR 層（之後的 MachineCSE、MachineSink 等）

SelectionDAG 只做 BB-local 決策。

## DAG 節點的 SDLoc

每個節點有個 `SDLoc`，記錄它來自 IR 的哪個位置。用於 debug info 傳遞。

```cpp
SDValue RISCVTargetLowering::LowerFoo(...) {
    SDLoc DL(N);        // 從 IR 位置抓
    return DAG.getNode(..., DL, ...);
}
```

確保 DWARF 的 line 表正確。

## Debug 技巧

### 看 lowering 過程

```bash
llc -debug-only=isel hello.ll 2>&1 | less
```

印出每個階段 DAG dump：

```
Initial selection DAG: %bb.0 'main:entry'
SelectionDAG has 20 nodes:
  t0: ch,glue = EntryToken
  t2: i32,ch = CopyFromReg t0, Register:i32 %0
  ...

Optimized lowered selection DAG: %bb.0 'main:entry'
...

Type-legalized selection DAG: %bb.0 'main:entry'
...

Legalized selection DAG: %bb.0 'main:entry'
...
```

追每個階段的差別。

### 只看特定階段

```bash
llc -print-after=finalize-isel hello.ll 2>&1 | head -50
```

## 常見誤會

1. **「DAG 跟 IR 差不多」**：不。IR 是 linear instruction list，DAG 是 per-BB 圖，opcode 不同。
2. **「DAGCombiner 等於 InstCombine」**：類似精神、不同階段。DAGCombiner 後期工作在 DAG 層。
3. **「SelectionDAG 能跨 BB 優化」**：不能。每 BB 獨立 DAG。
4. **「Target 的 LowerCall 是 arch-specific 複雜 code」**：對，ABI 邏輯都塞這。新手最好跳過、需要再讀。
5. **「DAG 不保存順序」**：chain edge 保存 memory/side effect 順序。pure arithmetic 可重排。

## 動手練習

1. 用 `-view-dag-combine1-dags` 或 `-debug-only=isel` 看一個簡單 function 的 DAG 變化。
2. 讀 `RISCVISelLowering::LowerCall` 的前 100 行，辨認 ABI 相關部分。
3. 寫一個 C function 有呼叫 `printf`，看 DAG 裡 `RISCVISD::CALL` 的 context。
4. 故意寫會觸發 legalization 的 IR（i128 操作）、看 legalization 過程。
5. 找 `performADDCombine` 或類似 function 在 `RISCVISelLowering.cpp`，讀一個 pattern。

## 自我檢核

- [ ] 我能解釋 SelectionDAG 跟 IR 的差異
- [ ] 我能分辨 data edge / chain edge / glue edge
- [ ] 我知道 ISD:: 是 target-neutral、RISCVISD:: 是 target-specific
- [ ] 我能找到 LowerCall 這類 lowering function 的位置
- [ ] 我能用 `-debug-only=isel` 看 DAG 變化

下一章看 legalization —— 把「不合法」的 DAG 轉成 target 能處理的形式。

→ [Ch 5 Legalization：type + DAG](./05-legalization.md)
