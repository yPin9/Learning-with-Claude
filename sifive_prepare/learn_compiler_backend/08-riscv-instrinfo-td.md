# Ch 8 — RISCVInstrInfo.td 走讀

> 目標：帶你走讀 `llvm/lib/Target/RISCV/RISCVInstrInfo.td` 這個 4000+ 行的核心檔。讀完你能自信找到「ADDI 在哪裡定義」「branch 的 pattern 在哪」「F 擴充如何整合」。

## 檔案地圖

先打開：

```
llvm/lib/Target/RISCV/
├── RISCV.td                     ← top-level (include 其他)
├── RISCVRegisterInfo.td          ← register definitions
├── RISCVInstrFormats.td          ← instruction format classes
├── RISCVInstrInfo.td             ← base instructions + patterns
├── RISCVInstrInfoA.td            ← Atomic
├── RISCVInstrInfoC.td            ← Compressed
├── RISCVInstrInfoF.td            ← F (Single-precision FP)
├── RISCVInstrInfoD.td            ← D (Double-precision FP)
├── RISCVInstrInfoM.td            ← M (Mul/Div)
├── RISCVInstrInfoZba.td          ← Zba (bitmanip)
├── RISCVInstrInfoZbb.td          ← Zbb
├── RISCVInstrInfoZbc.td          ← Zbc
├── RISCVInstrInfoZbs.td          ← Zbs
├── RISCVInstrInfoV.td            ← Vector extension (大，2000+ 行)
├── RISCVInstrInfoXSf*.td         ← SiFive vendor extensions
├── RISCVInstrInfoXTHead*.td      ← T-Head XuanTie extensions
├── RISCVSchedule.td              ← Scheduling classes
├── RISCVScheduleV.td             ← Vector scheduling
├── RISCVSchedRocket.td / SchedSiFive7.td / ...  ← per-core schedules
└── (more...)
```

總體 10000+ 行 `.td`。本章只走 `RISCVInstrInfo.td` 的骨架。

## `RISCV.td` 的 top 結構

```tablegen
include "llvm/Target/Target.td"
include "RISCVFeatures.td"
include "RISCVSchedule.td"
include "RISCVRegisterInfo.td"
include "RISCVCallingConv.td"
include "RISCVInstrInfo.td"
include "RISCVInstrInfoA.td"
include "RISCVInstrInfoF.td"
...

def RISCVInstrInfo : InstrInfo;

def RISCV : Target {
    let InstructionSet = RISCVInstrInfo;
    let AssemblyParsers = [RISCVAsmParser];
    ...
}

// Define processor models
class ProcessorModel<string name, SchedMachineModel m, list<SubtargetFeature> fs>
    : Processor<name, NoItineraries, fs> {
    let SchedModel = m;
}

def : ProcessorModel<"generic-rv64", Rocket64Model, [Feature64Bit, ...]>;
def : ProcessorModel<"sifive-s76", SiFive7Model, [Feature64Bit, FeatureStdExtZba, ...]>;
...
```

**這是 backend 的入口**。每個 `-mcpu=` 的值都在這裡定義。

## `RISCVFeatures.td`：所有 extension

```tablegen
def FeatureStdExtM
    : SubtargetFeature<"m", "HasStdExtM", "true",
                       "'M' (Integer Multiplication and Division)">;
def HasStdExtM : Predicate<"Subtarget->hasStdExtM()">, ...;

def FeatureStdExtA
    : SubtargetFeature<"a", "HasStdExtA", "true", ...>;
def HasStdExtA : Predicate<"Subtarget->hasStdExtA()">, ...;

...
```

每個 extension 兩個 def：

- `FeatureXxx`：代表「有這 extension」
- `HasStdExtXxx`：pattern / instruction 用來限定 predicate

`-march=rv64gc_zbb` 解析時 enable 對應的 Feature。

## `RISCVRegisterInfo.td`：register class

```tablegen
// Integer registers X0..X31
foreach Index = 0-31 in {
    def X#Index : RISCVReg<Index, "x"#Index>;
}

// X0 是 zero (constant 0)
def X0_zero : RISCVReg<0, "zero">;

// ABI names (mapping aliases)
def : Register<"ra", X1>;
def : Register<"sp", X2>;
...

// Register class
def GPR : RegisterClass<"RISCV", [XLenVT], 32,
                        (add (sequence "X%u", 10, 17),   // a0-a7
                             (sequence "X%u", 5, 7),      // t0-t2
                             ...
                             X0)> {
    ...
}
```

`foreach` 是 meta-programming：一次產生 32 個 def。

`add` 指定 register 順序 —— **allocation order**。reg alloc 會優先選前面的。

## 指令格式 class

`RISCVInstrFormats.td`：

```tablegen
class RVInst<dag outs, dag ins, string opcodestr, string argstr, ...> : Instruction {
    field bits<32> Inst;
    ...
}

class RVInstR<bits<7> funct7, bits<3> funct3, RVInstOpcode opcode,
              dag outs, dag ins, string opcodestr, string argstr>
    : RVInst<outs, ins, opcodestr, argstr, ...> {
    bits<5> rs2;
    bits<5> rs1;
    bits<5> rd;

    let Inst{31-25} = funct7;
    let Inst{24-20} = rs2;
    let Inst{19-15} = rs1;
    let Inst{14-12} = funct3;
    let Inst{11-7}  = rd;
    let Inst{6-0}   = opcode.Value;
}

class RVInstI<bits<3> funct3, RVInstOpcode opcode,
              dag outs, dag ins, string opcodestr, string argstr>
    : RVInst<outs, ins, opcodestr, argstr, ...> {
    bits<12> imm12;
    bits<5> rs1;
    bits<5> rd;

    let Inst{31-20} = imm12;
    let Inst{19-15} = rs1;
    let Inst{14-12} = funct3;
    let Inst{11-7}  = rd;
    let Inst{6-0}   = opcode.Value;
}

// 其他：RVInstS, RVInstB, RVInstU, RVInstJ...
```

**對應 `learn_riscv` Ch 1 的六種 format**。這裡宣告它們的 bit layout。

## `RISCVInstrInfo.td` 正式開始

```tablegen
//===----------------------------------------------------------------------===//
// Immediate operand types
//===----------------------------------------------------------------------===//

class ImmAsmOperand<string prefix, int width, string suffix> : AsmOperandClass {
    let Name = prefix # "Imm" # width # suffix;
    let RenderMethod = "addImmOperands";
    let DiagnosticType = !strconcat("Invalid", Name);
}

def simm12 : Operand<XLenVT>,
             ImmLeaf<XLenVT, [{return isInt<12>(Imm);}]> {
    let ParserMatchClass = SImmAsmOperand<12>;
    let EncoderMethod = "getImmOpValue";
    let DecoderMethod = "decodeSImmOperand<12>";
    let OperandType = "OPERAND_SIMM12";
    let OperandNamespace = "RISCVOp";
    let MCOperandPredicate = [{
        int64_t Imm;
        if (MCOp.evaluateAsConstantImm(Imm))
            return isInt<12>(Imm);
        return MCOp.isBareSymbolRef();
    }];
}

def uimm5 : Operand<XLenVT>,
            ImmLeaf<XLenVT, [{return isUInt<5>(Imm);}]> {
    ...
}
```

這定義各種 immediate 類型。`simm12` = signed 12-bit，用於 `addi` 等。`uimm5` = unsigned 5-bit，用於 shift amount。

## ALU 指令

接下來是 ALU class：

```tablegen
let hasSideEffects = 0, mayLoad = 0, mayStore = 0 in
class ALU_rr<bits<7> funct7, bits<3> funct3, string opcodestr,
             bit Commutable = 0>
    : RVInstR<funct7, funct3, OPC_OP, (outs GPR:$rd), (ins GPR:$rs1, GPR:$rs2),
              opcodestr, "$rd, $rs1, $rs2"> {
    let isCommutable = Commutable;
}

// 具體指令
def ADD  : ALU_rr<0b0000000, 0b000, "add", /*Commutable=*/1>;
def SUB  : ALU_rr<0b0100000, 0b000, "sub">;
def SLL  : ALU_rr<0b0000000, 0b001, "sll">;
def SLT  : ALU_rr<0b0000000, 0b010, "slt">;
def SLTU : ALU_rr<0b0000000, 0b011, "sltu">;
def XOR  : ALU_rr<0b0000000, 0b100, "xor", /*Commutable=*/1>;
def SRL  : ALU_rr<0b0000000, 0b101, "srl">;
def SRA  : ALU_rr<0b0100000, 0b101, "sra">;
def OR   : ALU_rr<0b0000000, 0b110, "or",  /*Commutable=*/1>;
def AND  : ALU_rr<0b0000000, 0b111, "and", /*Commutable=*/1>;
```

10 條 R-type ALU 指令一次寫完。

跟 `learn_riscv` Ch 1 的 funct3/funct7 表對照，你會看到一一對應。

## I-type ALU

```tablegen
let hasSideEffects = 0, mayLoad = 0, mayStore = 0 in
class ALU_ri<bits<3> funct3, string opcodestr>
    : RVInstI<funct3, OPC_OP_IMM, (outs GPR:$rd), (ins GPR:$rs1, simm12:$imm12),
              opcodestr, "$rd, $rs1, $imm12"> {}

def ADDI  : ALU_ri<0b000, "addi">;
def SLTI  : ALU_ri<0b010, "slti">;
def SLTIU : ALU_ri<0b011, "sltiu">;
def XORI  : ALU_ri<0b100, "xori">;
def ORI   : ALU_ri<0b110, "ori">;
def ANDI  : ALU_ri<0b111, "andi">;
```

立即數版本。

## Pattern 的集中宣告

```tablegen
// Generic patterns for int operations
def : PatGprGpr<add, ADD>;
def : PatGprGpr<sub, SUB>;
def : PatGprGpr<and, AND>;
def : PatGprGpr<or,  OR>;
def : PatGprGpr<xor, XOR>;

// ADDI with immediate
def : PatGprSimm12<add, ADDI>;
def : PatGprSimm12<or,  ORI>;
def : PatGprSimm12<and, ANDI>;
def : PatGprSimm12<xor, XORI>;

// Complex patterns for shifts
def : Pat<(shl GPR:$rs1, GPR:$rs2), (SLL GPR:$rs1, GPR:$rs2)>;
def : Pat<(shl GPR:$rs1, uimm5:$shamt), (SLLI GPR:$rs1, uimm5:$shamt)>;
...
```

`PatGprGpr`、`PatGprSimm12` 是 helper class：

```tablegen
class PatGprGpr<SDPatternOperator OpNode, RVInst Inst>
    : Pat<(OpNode GPR:$rs1, GPR:$rs2), (Inst GPR:$rs1, GPR:$rs2)>;

class PatGprSimm12<SDPatternOperator OpNode, RVInstI Inst>
    : Pat<(OpNode GPR:$rs1, simm12:$imm12), (Inst GPR:$rs1, simm12:$imm12)>;
```

又是 TableGen 的重複消除。

## Load / Store

```tablegen
class Load_ri<bits<3> funct3, string opcodestr, DAGOperand rty = GPR>
    : RVInstI<funct3, OPC_LOAD, (outs rty:$rd),
              (ins GPR:$rs1, simm12:$imm12),
              opcodestr, "$rd, ${imm12}(${rs1})"> {
    let mayLoad = 1;
    let IsSignExtendingOpW = 1;
}

def LB  : Load_ri<0b000, "lb">;
def LH  : Load_ri<0b001, "lh">;
def LW  : Load_ri<0b010, "lw">;
def LBU : Load_ri<0b100, "lbu">;
def LHU : Load_ri<0b101, "lhu">;

// 對應 pattern
def : Pat<(sextloadi8  (AddrRegImm AddrRegImm:$rs1, simm12:$imm12)),
          (LB GPR:$rs1, simm12:$imm12)>;
def : Pat<(sextloadi16 ...), (LH ...)>;
def : Pat<(load        ...), (LW ...)>;
def : Pat<(zextloadi8  ...), (LBU ...)>;
def : Pat<(zextloadi16 ...), (LHU ...)>;
```

`AddrRegImm` 是 complex pattern（前章範例），匹配 `base+imm` addressing。

## Branch

```tablegen
class Branch<bits<3> funct3, string opcodestr>
    : RVInstB<funct3, OPC_BRANCH, (outs),
              (ins GPR:$rs1, GPR:$rs2, simm13_lsb0:$imm12),
              opcodestr, "$rs1, $rs2, $imm12"> {
    let isBranch = 1;
    let isTerminator = 1;
}

def BEQ  : Branch<0b000, "beq">;
def BNE  : Branch<0b001, "bne">;
def BLT  : Branch<0b100, "blt">;
def BGE  : Branch<0b101, "bge">;
def BLTU : Branch<0b110, "bltu">;
def BGEU : Branch<0b111, "bgeu">;

// Pattern
def : Pat<(riscv_brcc GPR:$rs1, GPR:$rs2, SETEQ, bb:$imm12),
          (BEQ GPR:$rs1, GPR:$rs2, bb:$imm12)>;
def : Pat<(riscv_brcc GPR:$rs1, GPR:$rs2, SETNE, bb:$imm12),
          (BNE GPR:$rs1, GPR:$rs2, bb:$imm12)>;
...
```

`riscv_brcc` 是 RISC-V 專屬的 target ISD node（前面被 lower 出來）。

## Jumps + Call

```tablegen
def JAL  : RVInstJ<OPC_JAL, (outs GPR:$rd), (ins simm21_lsb0_jal:$imm20),
                   "jal", "$rd, $imm20">;

def JALR : RVInstI<0b000, OPC_JALR, (outs GPR:$rd),
                   (ins GPR:$rs1, simm12:$imm12),
                   "jalr", "$rd, ${imm12}(${rs1})">;

// Pseudo instructions
def PseudoBR : Pseudo<(outs), (ins simm21_lsb0_jal:$imm20), [(br bb:$imm20)]>,
               PseudoInstExpansion<(JAL X0, simm21_lsb0_jal:$imm20)>;

def PseudoRET : Pseudo<(outs), (ins), [(riscv_retglue)]>,
                PseudoInstExpansion<(JALR X0, X1, 0)>;
```

`Pseudo` 是佔位符指令，之後 lowering 展成真實指令。`PseudoRET` 最後變 `jalr x0, ra, 0`（Ch 3 of learn_riscv 講的）。

## Lui / Auipc

```tablegen
def LUI : RVInstU<OPC_LUI, (outs GPR:$rd), (ins uimm20_lui:$imm20),
                  "lui", "$rd, $imm20">;

def AUIPC : RVInstU<OPC_AUIPC, (outs GPR:$rd), (ins uimm20_auipc:$imm20),
                    "auipc", "$rd, $imm20">;
```

PC-relative 寬指令。配合 pattern / relocation 處理 `la` / `call` 等 pseudo。

## Pseudo instructions

LLVM 的 `Pseudo` 是 backend 中間形式的「假指令」，最終會展開：

```tablegen
def PseudoLI : Pseudo<(outs GPR:$rd), (ins ixlenimm_li:$imm), [(set GPR:$rd, imm:$imm)]>;

// 在 RISCVExpandPseudoInsts.cpp 展開為 LUI + ADDI 等
```

Pseudo 的好處：

- pattern match 階段只看到一條指令（簡化）
- 後續 expand 階段才處理具體展開

`learn_riscv` Ch 3 講的 `li` / `la` / `call` / `ret` 都在 `.td` 以 Pseudo 形式存在。

## RV64-specific

RV64 有額外的 `w`-variant 指令（32-bit operation on 64-bit register）：

```tablegen
let Predicates = [IsRV64] in {
def ADDW  : ALUW_rr<0b0000000, 0b000, "addw">;
def SUBW  : ALUW_rr<0b0100000, 0b000, "subw">;
def SLLW  : ALUW_rr<0b0000000, 0b001, "sllw">;
...
}
```

`Predicates = [IsRV64]` 限定只在 RV64 target 啟用。

## 如何找具體指令

Grep 技巧：

```bash
cd llvm/lib/Target/RISCV
grep -n "^def ADD" *.td
grep -n "ADDI : " *.td
```

找 `def XYZ` 開頭就找到定義。找 pattern：

```bash
grep "Pat<(add" *.td
```

## 讀整個檔的建議

第一次讀：**跳過細節、看 section comment**。`RISCVInstrInfo.td` 有頭部註解分類：

```
//===----------------------------------------------------------------------===//
// Immediate operand types
//===----------------------------------------------------------------------===//

//===----------------------------------------------------------------------===//
// Instruction formats
//===----------------------------------------------------------------------===//

//===----------------------------------------------------------------------===//
// Instruction Classes
//===----------------------------------------------------------------------===//

//===----------------------------------------------------------------------===//
// Instructions
//===----------------------------------------------------------------------===//

//===----------------------------------------------------------------------===//
// Pseudo-instructions and codegen patterns
//===----------------------------------------------------------------------===//
```

每次關注一個 section。看 3 次讀完整個檔。

## 動手練習

1. 打開 `RISCVInstrInfo.td`，找到 `def ADDI` 的定義，列出它的 funct3 / opcode。
2. 找到所有 `PatGpr*` helper class，寫出它們的 Pat 展開形式。
3. 找 `PseudoRET` 的 expansion，對照 `JALR` 的 field。
4. 找 `LUI` 跟 `AUIPC` 的 pattern，看它們何時被 match。
5. 打開 `RISCVInstrInfoZbb.td`，找 `CPOP` 的定義跟 pattern。

## 常見誤會

1. **「改一個 pattern 馬上生效」**：改 `.td` 要重跑 `llvm-tblgen`（ninja 自動）+ rebuild `.o`。
2. **「所有指令在 RISCVInstrInfo.td」**：不。RVV 在 RISCVInstrInfoV.td、vendor extension 另有檔。
3. **「`.td` 全是宣告，沒邏輯」**：TableGen 有 `!if` / `foreach` 可以 meta-program。
4. **「Pattern 只用本檔定義的 ISD」**：也可用 generic（add、sub、load 等）。實際上 generic pattern 多數。
5. **「改 instruction format 會全面 ripple」**：是。改 RVInstR 會影響所有 R-type 指令的 encoding，謹慎。

## 自我檢核

- [ ] 我能在 `RISCVInstrInfo.td` 找到任一基礎指令（ADD / BEQ / LW）的定義
- [ ] 我能對照 `learn_riscv` Ch 1 的 format 跟 `RVInstR` class
- [ ] 我能讀 pattern 判斷它何時 match
- [ ] 我知道 `Pseudo` 的角色跟展開時機
- [ ] 我能用 grep 在 `.td` 裡快速定位

下一章是本課最核心的實戰 —— 加一條 custom instruction 的端到端流程。你會寫一個自己的指令從 `.td` 到最終 `llc` 產出的 `.s`。

→ [Ch 9 加一條 custom instruction 端到端](./09-adding-custom-instruction.md)
