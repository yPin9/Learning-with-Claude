# 練習 B — 加一條 pseudo-instruction

> 目標：在 LLVM 加一條 RISC-V pseudo-instruction（不是真指令、expand 成其他真指令）。這是比 Ch 9 加 custom instruction 更簡單的版本，讓你熟悉 `RISCVExpandPseudoInsts.cpp` 跟相關 flow。

## 什麼是 pseudo instruction

Ch 8 提過。簡單回顧：

**Pseudo** = backend 裡的「假指令」，最終被 expand 成一條或多條真指令。好處：

- 簡化 pattern matching（一個 pseudo 比多條指令容易 match）
- 延後展開時機（在 pseudo 狀態下可以跨 pass 維持 abstract）
- 方便 scheduling / RA（視為單個 unit）

典型 RISC-V pseudo：

- `PseudoLI`: load immediate, expand 成 `ADDI` or `LUI+ADDI` or 更複雜
- `PseudoCALL`: call, expand 成 `AUIPC+JALR`
- `PseudoRET`: return, expand 成 `JALR x0, ra, 0`
- `PseudoLLA`: load local address, expand 成 `AUIPC+ADDI`

## 本練習要做的

加一個 pseudo: **`PseudoNEG`**：

- 輸入：一個 GPR
- 輸出：一個 GPR
- 語意：`rd = -rs`（取負）
- 實際展開：`SUB rd, x0, rs`（因為 x0=0，`0 - rs = -rs`）

為什麼這不用真指令：RISC-V 沒 native `NEG`、用 SUB + x0 模擬。pseudo 讓 `.td` 的 pattern 直接寫 `(neg rs)` → `PseudoNEG rd, rs`、後面再 expand 成 SUB。

**這個 optimization 其實不大**（既有 pattern 已 handle），但作為練習剛剛好。

## Step 1: 宣告 pseudo

編輯 `llvm/lib/Target/RISCV/RISCVInstrInfo.td`，在 pseudo 區塊加：

```tablegen
let hasSideEffects = 0, mayLoad = 0, mayStore = 0,
    Size = 4, isCodeGenOnly = 1 in
def PseudoNEG : Pseudo<(outs GPR:$rd), (ins GPR:$rs),
                       [(set GPR:$rd, (ineg GPR:$rs))]>;
```

`isCodeGenOnly = 1` 表示 "only in codegen, never in asm"。assembler 看不到這個名字、只有 compiler 內部用。

`[(set GPR:$rd, (ineg GPR:$rs))]` 是 pattern：match `ineg`（negation）。

## Step 2: 加 expand logic

編輯 `RISCVExpandPseudoInsts.cpp`。找到 `expandMI` function，在 switch 加 case：

```cpp
bool RISCVExpandPseudoInsts::expandMI(MachineBasicBlock &MBB,
                                      MachineBasicBlock::iterator MBBI,
                                      MachineBasicBlock::iterator &NextMBBI) {
    switch (MBBI->getOpcode()) {
    case RISCV::PseudoLI:
        return expandLoadImm(MBB, MBBI);
    case RISCV::PseudoCALL:
        return expandCALL(MBB, MBBI);
    // ...
    case RISCV::PseudoNEG:                        // ← 新加
        return expandNEG(MBB, MBBI);
    }
    return false;
}

bool RISCVExpandPseudoInsts::expandNEG(MachineBasicBlock &MBB,
                                        MachineBasicBlock::iterator MBBI) {
    MachineInstr &MI = *MBBI;
    Register DstReg = MI.getOperand(0).getReg();
    Register SrcReg = MI.getOperand(1).getReg();

    // 構造 SUB rd, x0, rs
    BuildMI(MBB, MBBI, MI.getDebugLoc(), TII->get(RISCV::SUB), DstReg)
        .addReg(RISCV::X0)
        .addReg(SrcReg);

    MI.eraseFromParent();
    return true;
}
```

**declare 的話要在 header**：

```cpp
// 私有 method declaration
private:
    bool expandNEG(MachineBasicBlock &MBB, MachineBasicBlock::iterator MBBI);
```

## Step 3: Build

```bash
cd build
ninja llc
```

如果 TableGen 錯：檢查 `.td` syntax。
如果 C++ 錯：檢查 `expandNEG` 的 declaration / signature。

## Step 4: 測試

寫 test IR：

```llvm
; negation.ll
define i32 @negate(i32 %x) {
    %r = sub i32 0, %x
    ret i32 %r
}
```

或直接 negate operator：

```llvm
define i32 @negate(i32 %x) {
    %r = sub nsw i32 0, %x
    ret i32 %r
}
```

跑：

```bash
./bin/llc -march=riscv64 -O2 negation.ll -o negation.s
cat negation.s
```

期望：

```asm
negate:
    sub a0, zero, a0        ; SUB x0 - x0, 存 a0
    ret
```

## Step 5: Verify pseudo 確實被用

用 `-stop-before=riscv-expand-pseudo` 看 expand 前 MIR：

```bash
./bin/llc -march=riscv64 -O2 -stop-before=riscv-expand-pseudo negation.ll -o neg_before.mir
cat neg_before.mir
```

期望看到 `PseudoNEG`：

```mir
body:             |
  bb.0:
    liveins: $x10
    %0:gpr = COPY $x10
    %1:gpr = PseudoNEG %0           ; ← 這裡！
    $x10 = COPY %1
    PseudoRET implicit $x10
```

再用 `-stop-after=riscv-expand-pseudo` 看 expand 後：

```bash
./bin/llc -march=riscv64 -O2 -stop-after=riscv-expand-pseudo negation.ll -o neg_after.mir
```

```mir
body:             |
  bb.0:
    liveins: $x10
    %0:gpr = COPY $x10
    %1:gpr = SUB $x0, %0             ; ← expand 成 SUB
    $x10 = COPY %1
    PseudoRET implicit $x10
```

## Step 6: 寫 FileCheck test

建 test file：

```
// llvm/test/CodeGen/RISCV/pseudo-neg.ll
; RUN: llc -mtriple=riscv64 -O2 < %s | FileCheck %s

define i32 @negate(i32 %x) {
; CHECK-LABEL: negate:
; CHECK:       sub a0, zero, a0
  %r = sub i32 0, %x
  ret i32 %r
}
```

跑：

```bash
cd build
./bin/llvm-lit ../llvm/test/CodeGen/RISCV/pseudo-neg.ll
```

## 延伸：更複雜 pseudo

如果 PseudoNEG 太簡單，試：

### PseudoLOAD_64:

合併 `LUI + ADDI + SLLI + ADDI` 的 64-bit constant load。比較複雜但模式類似。

### PseudoSWAP:

swap 兩個 register 的值。實務少用但寫起來有挑戰（要處理 destination 衝突）。

### 自訂 PseudoCALL_indirect:

某些 trampoline 需要指令序列，用 pseudo 包起來。

## Debug tips

### Pseudo 沒被用

檢查：

1. Pattern 寫對了嗎？`[(set GPR:$rd, (ineg GPR:$rs))]`
2. LLVM IR 真的產生 `ineg` (negation) 嗎？可能 `sub 0, x` 被 DAGCombiner 改成其他。
3. Target-specific DAGCombine 可能優先 handle。

### Expand 後 MIR 錯

檢查 `expandNEG()`：

1. Register class 對嗎？
2. MBB iterator 正確嗎？
3. `eraseFromParent()` 呼叫了嗎？

### Assertion failure in isel

可能 pseudo 有未處理 case。加：

```cpp
llvm_unreachable("Unexpected operand in PseudoNEG");
```

抓 crash point。

## 為什麼這是好練習

完成練習 B 後你會：

- 熟悉 `.td` 的 Pseudo 語法
- 熟悉 MachineFunctionPass 的寫法
- 理解 pseudo → real instruction 的 expansion
- 知道如何 plug 新 opcode 到 expand pipeline

**這些全部是 Ch 9 / Final Project 的 prerequisite**。

## 自我檢核

- [ ] 我加了一個 pseudo 到 `.td`
- [ ] 我寫了 expand function 到 `RISCVExpandPseudoInsts.cpp`
- [ ] 我能用 `-stop-before/-after` 看 expand 前後 MIR
- [ ] 我寫了 FileCheck test 並 pass
- [ ] 我能解釋 pseudo 存在的 3 個原因

## 下一步

→ [Final Project：加一個 custom extension 端到端](./final-project-add-extension.md)
