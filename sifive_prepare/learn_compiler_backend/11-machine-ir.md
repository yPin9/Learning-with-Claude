# Ch 11 — Machine IR (MIR)

> 目標：理解 LLVM 的 Machine IR — instruction selection 後、到最終 assembly 之前的中間形式。讀完你能 dump MIR、認出 virtual register / physical register、知道 MachineFunctionPass 工作層級。

## 什麼是 MIR

**Machine IR (MIR)** 是 target-specific 的 instruction stream。每個 instruction 是 **MachineInstr**、對應一條（或少數情況多條）target machine instruction。

MIR 跟 assembly 的差：

- MIR 仍有 **virtual register**（`%0`, `%1`）— 尚未 register allocate
- MIR 用 pseudo instruction（某些指令還沒展開）
- MIR 保留更多 metadata（debug info、liveness...）

## MIR vs IR vs asm

```
LLVM IR:
  %sum = add i32 %a, %b        ; SSA, abstract register

MIR (after isel, before RA):
  %2:gpr = ADDW %0, %1         ; target op, still virtual reg

MIR (after RA):
  $x10 = ADDW $x11, $x12        ; physical reg

Final asm:
  addw a0, a1, a2
```

三層級漸次降低抽象。

## MachineFunction 結構

```
MachineFunction
  ├── MachineBasicBlock (MBB) *
  │     └── MachineInstr (MI) *
  ├── MachineFrameInfo          (stack layout)
  ├── MachineRegisterInfo       (virtual/physical register table)
  └── MachineConstantPool       (.rodata 常數池)
```

每個 `MachineInstr` 有 opcode、operand list。Operand 可以是 register、immediate、memory operand、symbol 等。

## 看 MIR dump

```bash
llc -stop-after=finalize-isel hello.ll -o hello.mir
cat hello.mir
```

典型輸出：

```mir
--- |
  ; ModuleID = 'hello.c'
  ...
...
---
name:            foo
alignment:       2
tracksRegLiveness: true
registers:
  - { id: 0, class: gpr }
  - { id: 1, class: gpr }
  - { id: 2, class: gpr }
body:             |
  bb.0.entry:
    liveins: $x10, $x11

    %1:gpr = COPY $x11
    %0:gpr = COPY $x10
    %2:gpr = ADDW %1, %0
    $x10 = COPY %2
    PseudoRET implicit $x10

...
```

拆解：

- `registers`：virtual register 列表
- `bb.0.entry`：first basic block
- `liveins`：MBB 開始時活著的 physical register
- `%0:gpr = COPY $x10`：virtual reg `%0` 從 physical reg `$x10` 拷貝
- `ADDW %1, %0`：target instruction
- `PseudoRET implicit $x10`：return，`$x10` 是 implicit input

## 在任何階段 stop 看 MIR

LLVM 有各 stage 的 dump flag：

```bash
-stop-after=finalize-isel              ; ISel 結束
-stop-after=livevars                    ; 活變數分析後
-stop-after=phi-node-elimination        ; phi 消除後
-stop-after=regallocbasic               ; 簡單 RA 後
-stop-after=greedy                      ; greedy RA 後
-stop-after=prologepilog                ; prologue/epilog 插入後
-stop-after=machine-scheduler           ; scheduler 後
-stop-after=branch-folder                ; branch 折疊後

-stop-before=<same-list>                ; 某 pass 前
-run-pass=<pass-name>                   ; 只跑某 pass
```

**組合用 `-run-pass`** 做 pass-level 測試：

```bash
llc -run-pass=greedy input.mir -o output.mir
```

傳入 MIR 而非 IR、只跑 greedy RA。這是 regression test 常見 pattern。

## MIR 的 SSA-ness

MIR 在 register allocation 之前是 SSA（virtual register 只 assign 一次）：

```mir
%0:gpr = ADD %1, %2    ; %0 只這裡定義
%3:gpr = SUB %0, %4    ; %0 被用
```

RA 後 SSA 被打破（physical register 會重複寫）。

**phi node** 在 MIR 也存在，但通常 phi elimination pass 會消除（轉成 copy）。

## MachineInstr 的 operand

每個 MachineInstr 有 operand list。Operand 類型：

```cpp
// MachineOperand 的 kind
MO_Register         // virtual reg (%N) or physical reg ($xN)
MO_Immediate        // integer
MO_CImmediate       // big integer
MO_FPImmediate
MO_MachineBasicBlock // branch target
MO_FrameIndex        // stack slot 索引
MO_ConstantPoolIndex // .rodata index
MO_GlobalAddress     // global 符號
MO_ExternalSymbol    // ext symbol
MO_BlockAddress      // ...
MO_Metadata          // debug
MO_RegisterMask      // regmask (for call)
```

register operand 還有 flag：

```cpp
MachineOperand::isDef()       // 寫
MachineOperand::isUse()       // 讀
MachineOperand::isImplicit()  // implicit use/def
MachineOperand::isKill()      // last use
MachineOperand::isDead()      // dead def
```

這些 flag 給 optimization 跟 register allocator 用。

## Implicit operand

```mir
CALL @printf, implicit-def $x10, implicit-def $x1, implicit $x10
```

`implicit-def`：指令會寫這些 reg（但不在 explicit operand list）。`implicit`：讀。

典型 case：CALL 會 clobber 所有 caller-saved reg、`implicit-def` 標示。return 指令 `implicit $x10` 表示要讀 return value。

這些讓 liveness / RA 知道真實 def/use。

## 寫自己的 MachineFunctionPass

```cpp
class MyMachinePass : public MachineFunctionPass {
public:
    static char ID;
    MyMachinePass() : MachineFunctionPass(ID) {}

    bool runOnMachineFunction(MachineFunction &MF) override {
        for (MachineBasicBlock &MBB : MF) {
            for (MachineInstr &MI : MBB) {
                if (MI.getOpcode() == RISCV::ADDW) {
                    // do something
                }
            }
        }
        return false;   // true if changed
    }

    StringRef getPassName() const override { return "My Pass"; }
};
```

註冊到 pipeline：

```cpp
// RISCVTargetMachine.cpp
void RISCVPassConfig::addPreSched2() {
    addPass(createMyMachinePass());
}
```

這讓 pass 在 scheduler pass 前執行。

## MIR 的 iteration 跟 modification

在 pass 裡 iterate:

```cpp
for (auto &MBB : MF) {
    for (auto I = MBB.begin(); I != MBB.end(); ) {
        MachineInstr &MI = *I++;   // advance before potential modification
        if (...) {
            // rewrite MI
            BuildMI(MBB, I, MI.getDebugLoc(),
                    TII->get(RISCV::ADD), DstReg)
                .addReg(SrcReg1)
                .addReg(SrcReg2);
            MI.eraseFromParent();
        }
    }
}
```

`BuildMI` 是建新 MachineInstr 的 helper。`TII` 是 `TargetInstrInfo`。

## 常見 backend pass

ISel 後 RISC-V 會跑：

```
1. FinalizeMachineBundles
2. RemoveRedundantDebugValues
3. RISCV_Specific passes (e.g., RISCVInsertVSETVLI)
4. PeepholeOptimizer
5. MachineCSE
6. MachineSinking
7. EarlyIfConversion
8. TwoAddressInstruction
9. RegisterCoalescer
10. LiveVariableAnalysis / LiveIntervals
11. Register Allocator (greedy)
12. Register Rewriter
13. Prologue/Epilogue insertion
14. Machine scheduler
15. Branch folding / tail duplication
16. MachineOutliner (optional)
17. Instruction emission
```

每個都是 MachineFunctionPass。Ch 12 / Ch 13 深入 RA 跟 Scheduler。

## RISCV 專屬的 MachineFunctionPass

幾個重要的：

```
RISCVInsertVSETVLI     插入 vsetvl 指令 (RVV)
RISCVInsertReadWriteCSR 某些 CSR 存取優化
RISCVMergeBaseOffset   合併連續 addressing 操作
RISCVExpandPseudoInsts 展開 Pseudo 指令 (li, tail, ...)
RISCVMakeCompressible  盡量讓指令能 relax 成 compressed
```

`RISCVInsertVSETVLI.cpp` 是 RVV 的靈魂 pass，幾千行 code 做 dataflow 決定 vsetvl 位置。Ch 15 會專講。

## `-print-after-all` 看每個 pass

```bash
llc -print-after-all hello.ll 2>&1 | less
```

輸出巨大，每個 pass 後的 MIR snapshot。debug 時超有用。

配合 `-filter-print-funcs=foo` 只印某 function：

```bash
llc -print-after-all -filter-print-funcs=foo hello.ll 2>&1 | less
```

## MachineInstr 的 TableGen 來源

回顧 Ch 7-8：每條 MachineInstr 的 opcode（例 `RISCV::ADD`、`RISCV::ADDW`）來自 `.td` 檔的 `def ADD`、`def ADDW`。

TableGen 產的 `.inc`：

```cpp
// RISCVGenInstrInfo.inc (TableGen 生成)
namespace RISCV {
    enum {
        ADD = 1234,
        ADDW = 1235,
        ...
    };
}
```

所以 `MI.getOpcode() == RISCV::ADD` 是對照 TableGen 定義的 ID。

## 常見誤會

1. **「MIR 跟 assembly 一樣」**：不。MIR 有 virtual reg、pseudo、實際多處理階段才會變 asm。
2. **「MIR 只是 data，沒語意」**：有。每個 opcode 在 `.td` 定義、`TargetInstrInfo` 有 property 查詢（isCall, isTerminator, mayLoad...）。
3. **「backend pass 只動 MIR」**：大部分是，但有些 MachineFunctionPass 操作 MachineFrameInfo 等 metadata。
4. **「MIR 不 portable」**：有。LLVM 有 YAML-format 的 MIR，可以 `.mir` 檔 import/export 做 pass-level testing。
5. **「register allocation 後 MIR 就接近 asm」**：是，但還有 prologue/epilogue、branch folding 等 pass。

## 動手練習

1. 用 `llc -stop-after=finalize-isel hello.ll` 產 `hello.mir`，讀它辨認 virtual reg / physical reg / opcode。
2. 比較 `-stop-after=finalize-isel` 跟 `-stop-after=greedy`，看 RA 後 virtual reg 消失。
3. 用 `-run-pass=greedy -o out.mir in.mir` 只跑 greedy RA、對照 output。
4. 寫一個簡單 MachineFunctionPass（計數 MI 數），plug 進 LLVM pipeline。
5. 讀 `RISCVInsertVSETVLI.cpp` 的 `runOnMachineFunction`，skim 整體結構。

## 自我檢核

- [ ] 我能看 `.mir` 文件認出 virtual/physical register、basic block、implicit operand
- [ ] 我知道 MIR 跟 IR、assembly 的差異
- [ ] 我能用 `-stop-after` / `-run-pass` 在 pass level 工作
- [ ] 我能寫簡單 MachineFunctionPass
- [ ] 我知道 RISC-V 有哪些專屬 MachineFunctionPass

下一章進 register allocator —— 把 virtual register 映射到 physical register 的 pass。

→ [Ch 12 Register allocator：greedy / fast](./12-register-allocator.md)
