# Ch 20 — RISC-V target 源碼地圖

> 目標：本課終章。給你 LLVM RISC-V backend 所有檔案的用途表、各情境的查詢 flowchart。之後每碰到問題都回來查這章。

## 完整檔案地圖

```
llvm/lib/Target/RISCV/
├── RISCV.td                              ← Top-level entry
├── RISCVFeatures.td                      ← Extension 定義
├── RISCVSchedule.td                      ← Generic sched classes
├── RISCVScheduleV.td                     ← Vector sched classes
├── RISCVRegisterInfo.td                  ← Register classes
├── RISCVCallingConv.td                   ← ABI calling conv
│
├── RISCVInstrFormats.td                  ← Instruction format classes
├── RISCVInstrInfo.td                     ← Base ISA instructions
├── RISCVInstrInfoA.td                    ← A (Atomic)
├── RISCVInstrInfoC.td                    ← C (Compressed)
├── RISCVInstrInfoD.td                    ← D (FP64)
├── RISCVInstrInfoF.td                    ← F (FP32)
├── RISCVInstrInfoM.td                    ← M (Mul/Div)
├── RISCVInstrInfoZba.td                  ← Zba
├── RISCVInstrInfoZbb.td                  ← Zbb
├── RISCVInstrInfoZbc.td                  ← Zbc
├── RISCVInstrInfoZbs.td                  ← Zbs
├── RISCVInstrInfoZc.td                   ← Zc* family
├── RISCVInstrInfoV.td                    ← Vector (huge)
├── RISCVInstrInfoVPseudos.td             ← Vector pseudo instructions
├── RISCVInstrInfoZk*.td                  ← Zk scalar crypto
├── RISCVInstrInfoZv*.td                  ← Vector crypto
├── RISCVInstrInfoXSf*.td                 ← SiFive vendor
├── RISCVInstrInfoXTHead*.td              ← T-Head vendor
├── RISCVInstrInfoXAndes*.td              ← Andes vendor
│
├── RISCVSchedRocket.td                   ← Rocket core
├── RISCVSchedSiFive7.td                  ← SiFive 7 series
├── RISCVSchedSiFiveP400.td
├── RISCVSchedSiFiveP600.td
├── RISCVSchedSiFiveP800.td
├── RISCVSchedSyntacoreSCR1.td
├── RISCVSchedXiangShanNanHu.td
│
├── RISCVTargetMachine.h / .cpp           ← Top-level driver
├── RISCVSubtarget.h / .cpp               ← Subtarget info (feature check)
├── RISCVRegisterInfo.h / .cpp            ← Register info (custom methods)
│
├── RISCVISelLowering.h / .cpp            ← SelectionDAG lowering (大)
├── RISCVISelDAGToDAG.h / .cpp            ← DAG → MachineInstr selector
│
├── RISCVInstrInfo.h / .cpp               ← Instruction info (custom methods)
├── RISCVMCInstLower.cpp                  ← MI → MCInst
│
├── RISCVFrameLowering.h / .cpp           ← Prologue/epilogue
├── RISCVMachineFunctionInfo.h / .cpp     ← Function-specific info
├── RISCVMergeBaseOffset.cpp              ← Addressing mode merge
├── RISCVExpandPseudoInsts.cpp            ← Pseudo expansion
├── RISCVExpandAtomicPseudoInsts.cpp      ← Atomic pseudo expansion
├── RISCVInsertVSETVLI.cpp                ← VSETVLI insertion (RVV 靈魂)
├── RISCVInsertReadWriteCSR.cpp
├── RISCVMakeCompressible.cpp             ← Compressed relax pass
├── RISCVMoveMerger.cpp
├── RISCVPushPopOptimizer.cpp             ← Zcmp optimization
├── RISCVRedundantCopyElimination.cpp
├── RISCVSExtWRemoval.cpp
├── RISCVOptWInstrs.cpp
│
├── RISCVAsmPrinter.cpp                   ← Produce final asm
├── RISCVLegalizerInfo.cpp                ← GlobalISel legalizer
├── RISCVCallLowering.cpp                 ← GlobalISel call lowering
├── RISCVInstructionSelector.cpp          ← GlobalISel selector
├── RISCVRegisterBankInfo.cpp             ← GlobalISel RB
│
├── MCTargetDesc/
│   ├── RISCVMCTargetDesc.cpp             ← MC init
│   ├── RISCVMCCodeEmitter.cpp            ← Encoding
│   ├── RISCVMCAsmInfo.cpp                ← Asm info
│   ├── RISCVInstPrinter.cpp              ← MCInst → text
│   ├── RISCVAsmBackend.cpp               ← Fixup, relax
│   ├── RISCVELFObjectWriter.cpp          ← ELF emit
│   └── RISCVMCObjectFileInfo.cpp         ← Section info
│
├── AsmParser/
│   └── RISCVAsmParser.cpp                ← Asm text → MCInst
│
├── Disassembler/
│   └── RISCVDisassembler.cpp             ← Bytes → MCInst
│
├── TargetInfo/
│   └── RISCVTargetInfo.cpp               ← Registration
│
└── GISel/                                 ← GlobalISel related
    ├── RISCVCallLowering.cpp
    ├── RISCVInstructionSelector.cpp
    ├── RISCVLegalizerInfo.cpp
    └── RISCVRegisterBankInfo.cpp
```

總約 100+ 檔、幾十萬行。

## 各情境查詢 flowchart

### 「我想加一條 instruction」

```
主 location: llvm/lib/Target/RISCV/RISCVInstrInfoXxx.td
加 extension: RISCVFeatures.td
加到 march: llvm/lib/Support/RISCVISAInfo.cpp
Clang builtin: clang/include/clang/Basic/BuiltinsRISCV.def + CGBuiltin.cpp
LLVM intrinsic: llvm/include/llvm/IR/IntrinsicsRISCV.td
Scheduling: RISCVSchedXxx.td (每個 core 一份)
Test: llvm/test/CodeGen/RISCV/ + llvm/test/MC/RISCV/
```

### 「我的 intrinsic 產不出對的 code」

```
1. Check pattern: 對應 .td 檔
2. Check predicate: `Predicates = [HasStdExt...]` 對嗎
3. Check DAGCombiner: RISCVISelLowering.cpp 有沒有 pre-combine 掉
4. Check SelectionDAG: `llc -debug-only=isel` 看 match 過程
```

### 「我的 RVV code 產很多 vsetvli」

```
主要檔: RISCVInsertVSETVLI.cpp
Debug: llc -debug-only=riscv-insert-vsetvli
```

### 「function 開頭 / 結尾 asm 錯」

```
RISCVFrameLowering.cpp 管 prologue/epilogue
RISCVCallingConv.td 管 ABI argument 傳遞
```

### 「編 .so 時 relocation 錯」

```
MC Fixup: RISCVAsmBackend.cpp
ELF Relocation: RISCVELFObjectWriter.cpp (map Fixup → R_RISCV_*)
Linker 那邊: 另一個課的 LLD lld/ELF/Arch/RISCV.cpp
```

### 「某 core 的 benchmark 退步」

```
Scheduling: RISCVSchedSiFiveXxx.td (檢查每個指令 latency / resource)
Cost model: RISCVTargetTransformInfo.cpp (給 optimizer 用)
Run llc -mcpu=xxx -print-after=machine-scheduler 看排程
```

### 「VLA vector 寫 code 錯」

```
Type lowering: RISCVISelLowering.cpp ( `setOperationAction` for scalable vector)
Legalizer: LegalizeVectorOps / LegalizeVectorTypes (generic + RISC-V 特化)
VSETVLI: RISCVInsertVSETVLI.cpp
```

## 各檔案的 "size" 感

只記特別大的：

```
RISCVISelLowering.cpp      ~20000 行 (最大)
RISCVInstrInfo.td           ~4000 行
RISCVInstrInfoV.td          ~2000 行
RISCVInstrInfoVPseudos.td   ~5000 行
RISCVInsertVSETVLI.cpp      ~3000 行
```

## 查 log 的 flag cheatsheet

```bash
# 看 ISel 過程
-debug-only=isel

# 看 VSETVLI insertion
-debug-only=riscv-insert-vsetvli

# 看 RA
-debug-only=regalloc

# 看 scheduler
-debug-only=machine-scheduler

# 看所有 pass 前後 IR/MIR
-print-after-all
-print-before-all

# 篩選 function
-filter-print-funcs=foo

# Stop at a specific pass
-stop-after=finalize-isel
-stop-before=greedy

# Run single pass
-run-pass=riscv-insert-vsetvli

# MIR round-trip
-stop-after=... -o out.mir    ; llc out.mir -start-after=...
```

## 外部檔案（clang / libcxx / compiler-rt）

```
clang/lib/Basic/Targets/RISCV.h/cpp           ; Clang target info
clang/include/clang/Basic/BuiltinsRISCV.def    ; Builtin 列表
clang/lib/CodeGen/CGBuiltin.cpp                ; Builtin → LLVM intrinsic
clang/lib/Basic/Targets/RISCVTargetInfo.cpp    ; Target info

compiler-rt/lib/builtins/                     ; Runtime libcall 實作
libcxx/                                         ; C++ stdlib
```

## 實用腳本

### 找 instruction 定義

```bash
cd llvm/lib/Target/RISCV
grep -rn "^def ADD" *.td
grep -rn "def ADDI " *.td
```

### 找 pattern

```bash
grep "Pat<(add" *.td
grep "Pat<(mul" *.td
```

### 找某 opcode 的 encoding

```bash
# 建好 llvm-tblgen 後
llvm-tblgen -I ../../../include -gen-instr-info RISCV.td > /tmp/insts.inc
grep -A3 "RISCV::ADD " /tmp/insts.inc
```

## 面試常考的「這段功能的 source 在哪」

SiFive 面試的典型考法：

- 「VSETVLI 的插入 logic 在哪個檔？」 → `RISCVInsertVSETVLI.cpp`
- 「加一個 Zbb 指令要改哪些 file？」 → `.td` + optional intrinsic + test
- 「ABI 的 call 實作在哪？」 → `RISCVISelLowering::LowerCall`
- 「Relocation type 定義在哪？」 → `include/llvm/BinaryFormat/ELFRelocs/RISCV.def` + `RISCVELFObjectWriter.cpp`

把本章的 map 記在腦中，面試能秒答。

## 整個課程的回顧

```
Ch 0:  Build LLVM
Ch 1-3: LLVM IR + Pass + Optimization
Ch 4-6: SelectionDAG pipeline
Ch 7-9: TableGen + RISCV.td + Custom instruction
Ch 10-13: GlobalISel + MIR + RA + Scheduler
Ch 14-16: Intrinsic + RVV + Inline asm
Ch 17-20: MC + GCC + Upstream + Source map
```

加上 practice + final project：

```
Practice A: Read an LLVM pass
Practice B: Add a pseudo-instruction  
Final: Add a custom extension end-to-end
```

完成這 20 章 + 3 個 hands-on，你是 LLVM RISC-V 的 competent engineer。

## 下一步的進階材料

課後要深化，建議：

1. **讀 LLD 的 RISC-V 部分**：`lld/ELF/Arch/RISCV.cpp`
2. **讀 RVV intrinsic spec**：<https://github.com/riscv-non-isa/rvv-intrinsic-doc>
3. **讀 SiFive Intelligence spec + 對應 LLVM 實作**：`RISCVInstrInfoXSf*.td`
4. **讀 Linux kernel RISC-V port**：`arch/riscv/`（不在這課範圍但值得）
5. **訂閱 LLVM Discourse 的 `#risc-v` 標籤**：實時跟進 upstream

## 到這裡你能做什麼

完成這 20 章 + practice + final project 後，你應該能：

- [ ] 看懂並修改 LLVM RISC-V backend 任一檔案
- [ ] 加一個完整的 custom extension（spec → Clang → LLVM → MC → test）
- [ ] Debug 多數 codegen 問題（pattern 沒 match、vsetvli 太多等）
- [ ] 寫 scheduling model 給新 core
- [ ] 送 upstream PR 並處理 review
- [ ] 跟硬體團隊、benchmarking 團隊、客戶成功團隊 cross-talk
- [ ] 在 SiFive interview 的技術回合表現得像 staff engineer 候選人

這正是 job spec 要的能力。

## 最後的建議

做完這門課，你有理論。**剩下的是實戰**：

1. **挑一個 LLVM RISC-V `good first issue` 真的解決**
2. **寫 final project（加 custom extension）並放 GitHub**
3. **讀一個 complete 的 upstream PR 從提出到 merge**
4. **參加一場 LLVM Dev Meeting**（線上也 OK）

這些不能代替 knowledge、但沒這些 knowledge 也沒用。**現在去做**。

---

## 動手練習

這是本章沒太多新學、但是整合練習：

1. **列表格**：把 20 章的每章對應到「job spec 哪條要求」。
2. **flowchart**：畫出 C code → binary 的完整流程、標出每個 chapter 對應的階段。
3. **source code 熟悉度**：挑 5 個 file，花 10 分鐘看懂每個檔的結構。
4. **面試 Q&A**：自問自答 15 個 compiler backend 的面試題。
5. **Star LLVM repo**：去 GitHub `llvm/llvm-project`、star 了、開始 follow。

## 自我檢核

- [ ] 我能把任一 codegen 問題 map 到某個檔案 / pass
- [ ] 我知道如何用 `-debug-only=` 跟 `-print-after=` 定位問題
- [ ] 我能回顧所有 20 章並描述每章的核心概念
- [ ] 我有 final project 的 concrete plan
- [ ] 我準備好面試 SiFive compiler 職位

課程正式結束。後面只剩 practices 跟 final project，去做，跑起來你就贏了。

→ [練習 A：讀懂一個 LLVM pass](./practice-a-read-a-pass.md)
→ [練習 B：加一條 pseudo-instruction](./practice-b-add-pseudo-instruction.md)
→ [Final Project：加一個 custom extension 端到端](./final-project-add-extension.md)
