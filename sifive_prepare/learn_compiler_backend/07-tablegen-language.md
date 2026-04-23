# Ch 7 — TableGen 語言速成

> 目標：讀懂並能寫 `.td` 檔。TableGen 是 LLVM 的宣告式語言、用來描述 instruction / register / pattern / scheduling model。這章給你 TableGen 的基本語法、class 系統、常用 idiom。

## 為什麼要有 TableGen

LLVM backend 的 instruction 資訊巨大：

- RISC-V 有 400+ 指令（含各 extension）
- 每條指令的 encoding、operand 類型、pattern、latency 等
- 寫成 C++ 要幾萬行 switch/case

**TableGen 是 LLVM 作者為了這個問題發明的 DSL**：

- 宣告式描述 table / record
- `llvm-tblgen` 處理 `.td` 產生 C++ code
- 減少重複、強制結構化

早期只想做 instruction table，後來擴充到 pattern、subtarget、scheduling、relocation...

## 基本語法：class 與 def

TableGen 的核心：**class** 跟 **def**。

```tablegen
class Animal<string kind> {
    string Name = kind;
    int Legs = 4;
}

def Dog : Animal<"dog">;
def Cat : Animal<"cat">;
```

解讀：

- `class Animal<string kind>`：一個 parametrized template，帶一個 string parameter
- `def Dog : Animal<"dog">`：建立一個 record，其 `Name = "dog"`, `Legs = 4`

TableGen 是 **record-based**：每個 `def` 產生一個 record。record 的 field 從 class 繼承 + override。

## 多重繼承

```tablegen
class Animal<string kind> {
    string Name = kind;
    int Legs = 4;
}

class Mammal {
    bit HasFur = true;
}

def Dog : Animal<"dog">, Mammal;
```

`Dog` 的 fields: `Name = "dog"`, `Legs = 4`, `HasFur = true`.

LLVM backend 大量用多重繼承組 instruction 屬性。

## 常見類型

```tablegen
bit            // 單 bit
bits<N>        // N-bit 固定寬
int            // 整數
string         // 字串
list<T>        // list
T { ... }      // anonymous record

// LLVM-specific
dag            // DAG fragment，pattern match 用
code           // raw C++ code
```

## Instruction 的 class 架構

`RISCVInstrInfo.td` 的核心：

```tablegen
class RVInst<dag outs, dag ins, string opcodestr, string argstr, ...>
    : Instruction {
    field bits<32> Inst;        // 32-bit encoding
    let OutOperandList = outs;
    let InOperandList = ins;
    let AsmString = opcodestr # "\t" # argstr;
    ...
}

class RVInstR<bits<7> funct7, bits<3> funct3, ...>
    : RVInst<outs, ins, opcodestr, argstr, ...> {
    bits<5> rs2;
    bits<5> rs1;
    bits<5> rd;
    let Inst{31-25} = funct7;
    let Inst{24-20} = rs2;
    let Inst{19-15} = rs1;
    let Inst{14-12} = funct3;
    let Inst{11-7}  = rd;
    let Inst{6-0}   = OPC_OP.Value;
}

def ADD : RVInstR<0b0000000, 0b000, (outs GPR:$rd), (ins GPR:$rs1, GPR:$rs2),
                  "add", "$rd, $rs1, $rs2">;
```

每個 `def` 產生一個具體指令。class 提供 template、def 填具體參數。

## `let` 的意思

```tablegen
let Inst{31-25} = funct7;
```

意思：「把這個 record 的 `Inst` field 的 bit 31-25 設為 `funct7`」。

`let` 用於：

- 設 field 值
- override 父 class 的預設值
- 設 flag 類 field

## `field` 的意思

```tablegen
field bits<32> Inst;
```

宣告一個 field。`field` 讓子 class 可以 refine 這個 field 的值（用 `let`）。

## Pattern 語法

```tablegen
def : Pat<(add GPR:$rs1, GPR:$rs2), (ADD GPR:$rs1, GPR:$rs2)>;
```

拆解：

- `def :`：anonymous def（沒名字、只為了 side effect）
- `Pat<a, b>`：表達 "match a, replace with b"
- `(add ...)`：DAG fragment，第一個是 operator（`ISD::ADD` 簡寫 `add`）
- `GPR:$rs1`：operand 是 GPR，binding 到 `$rs1`
- `(ADD ...)`：要替換成的 MachineInstr

## Multiclass：產生多個 def

```tablegen
multiclass ALUOp<string opname, SDNode node> {
    def _rr : RVInstR<..., (outs GPR:$rd), (ins GPR:$rs1, GPR:$rs2), ...>;
    def _ri : RVInstI<..., (outs GPR:$rd), (ins GPR:$rs1, simm12:$imm), ...>;
    def : Pat<(node GPR:$rs1, GPR:$rs2), (!cast<Instruction>(NAME#"_rr") ...)>;
    ...
}

defm ADD : ALUOp<"add", add>;
defm SUB : ALUOp<"sub", sub>;
```

multiclass 產生多個 def。`defm ADD : ALUOp<"add", add>` 實例化 multiclass、產 `ADD_rr`、`ADD_ri` 等。

**多條 RISC-V ALU 指令用 multiclass 寫**，大量減少重複。

## `defm` 跟 `!` prefix operators

```tablegen
!cast<Instruction>(NAME#"_rr")
```

TableGen 的「bang operator」：

```
!cast<T>(x)    // cast
!add(a, b)     // integer add
!strconcat     // string concat
!con(a, b)     // list concat
NAME           // 當前 def 的名字
```

很多 LLVM `.td` 用 bang operator 做 meta-programming。剛開始看很奇怪。

## 一個完整例子

```tablegen
// Register class
def GPR : RegisterClass<"RISCV", [i32, i64], 32,
                        (sequence "X%u", 0, 31)> {
    let Size = 32;
}

// Immediate operand type
def simm12 : Operand<i32>, ImmLeaf<i32, [{ return isInt<12>(Imm); }]> {
    let DecoderMethod = "decodeSImmOperand<12>";
}

// Instruction class
class RVInstR<bits<7> funct7, bits<3> funct3, RVInstOpcode opcode,
              dag outs, dag ins, string opcodestr, string argstr>
    : Instruction {
    bits<5> rd, rs1, rs2;
    bits<32> Inst = 0;
    let Inst{31-25} = funct7;
    let Inst{24-20} = rs2;
    let Inst{19-15} = rs1;
    let Inst{14-12} = funct3;
    let Inst{11-7}  = rd;
    let Inst{6-0}   = opcode.Value;
    let OutOperandList = outs;
    let InOperandList = ins;
    let AsmString = opcodestr # "\t" # argstr;
}

// Concrete def
def ADD : RVInstR<0b0000000, 0b000, OPC_OP,
                  (outs GPR:$rd), (ins GPR:$rs1, GPR:$rs2),
                  "add", "$rd, $rs1, $rs2">;

// Pattern
def : Pat<(add GPR:$rs1, GPR:$rs2), (ADD GPR:$rs1, GPR:$rs2)>;
```

五個部分：

1. Register class
2. Immediate operand
3. Instruction format class (RVInstR)
4. 具體指令 def (ADD)
5. Pattern

RISC-V `.td` 大部分內容是這五類的組合。

## Predicates（subtarget 條件）

```tablegen
let Predicates = [HasStdExtM] in {
    def MUL : RVInstR<..., ..., "mul", ...>;
}

def : Pat<..., (MUL ...)>, Requires<[HasStdExtM]>;
```

`HasStdExtM` 是 SubtargetFeature，表示 target 有 M extension。

這讓 `-march=rv64i` 不會選到 `MUL`、`-march=rv64im` 才能。

## `SubtargetFeature`

```tablegen
def FeatureStdExtM
    : SubtargetFeature<"m", "HasStdExtM", "true",
                       "'M' (Integer Multiplication and Division)">;

def HasStdExtM : Predicate<"Subtarget->hasStdExtM()">,
                 AssemblerPredicate<(all_of FeatureStdExtM),
                                    "'M' (Integer Multiplication and Division)">;
```

每個 extension 有兩層：

- `FeatureXxx`：真實的 subtarget feature
- `HasStdExtXxx`：predicate，給 pattern / instruction 用

## Scheduling model 也用 TableGen

```tablegen
def WriteIALU    : SchedWrite;
def WriteShift   : SchedWrite;
def ReadIALU     : SchedRead;

class ALU_rr<...>
    : RVInstR<...>, Sched<[WriteIALU, ReadIALU, ReadIALU]> {
}

// 在 scheduling model 裡定義：
let SchedModel = SiFive7Model in {
    def : WriteRes<WriteIALU, [SiFive7IntALU]> {
        let Latency = 1;
    }
}
```

Ch 13 會深入 scheduling model。這裡只看語法。

## `include` 機制

```tablegen
include "llvm/Target/Target.td"

// Our definitions
include "RISCVRegisterInfo.td"
include "RISCVInstrFormats.td"
include "RISCVInstrInfo.td"
include "RISCVInstrInfoM.td"
include "RISCVInstrInfoA.td"
include "RISCVInstrInfoF.td"
include "RISCVInstrInfoV.td"
...
```

`.td` 檔彼此 include。**`RISCV.td` 是頂層**，include 其他所有 module。

## Dump 出 TableGen 的輸出

```bash
llvm-tblgen -I llvm/include -I llvm/lib/Target/RISCV \
    llvm/lib/Target/RISCV/RISCV.td -gen-instr-info > insts.inc
```

`insts.inc` 是幾 MB 的 C++ code。你不會直接 debug 這個，但有時看一下 TableGen 最終展開什麼有幫助。

## 常用 backend generator

```
-gen-instr-info           ; instruction info class
-gen-register-info        ; register info class
-gen-dag-isel             ; pattern match code
-gen-asm-matcher          ; asm parser
-gen-asm-writer           ; asm printer
-gen-disassembler         ; disassembler
-gen-subtarget            ; subtarget features
-gen-subtarget-info       ; (same)
```

每個 `.td` 通常被多種 generator 跑過，產多個 `.inc` 檔。

## Debug TableGen

`.td` 錯時 `llvm-tblgen` 會報錯。典型錯誤：

```
error: Value 'rs3' unknown!
```

原因：你用了一個沒宣告的 field。檢查 class 跟 def。

進階：

```bash
llvm-tblgen --print-records ...  # 印所有 record
llvm-tblgen --dump-json ...       # JSON 格式 dump
```

## TableGen 的哲學

TableGen 是**非圖靈完備**的 DSL：

- 沒 loop（沒 for / while）
- `foreach` 是 meta-programming，不是 runtime
- 沒一般 function，只有 class + multiclass 的 composition

這個限制有意：**確保 TableGen 可以 static analyze + generate deterministic C++**。想加 logic 就要寫 C++（例：ComplexPattern 的 C++ function）。

## 常見誤會

1. **「TableGen 是程式語言」**：不完全。是 declarative DSL，語法受限。
2. **「`.td` 跟 C++ 混寫」**：分開的。TableGen → `.inc`、`.cpp` include `.inc`。
3. **「我可以執行 `.td`」**：不能。`.td` 是 source、`llvm-tblgen` 處理它。
4. **「class 跟 C++ class 一樣」**：語法像、語意不同。TableGen class 是 record template，沒 method。
5. **「`def`s 名稱可以隨便」**：不。名稱有特殊意義（gen 時成為 C++ 的 global symbol）。

## 動手練習

1. 讀 `RISCVInstrFormats.td` 的前 200 行，認出 RVInst、RVInstR、RVInstI 的關係。
2. 讀 `RISCVInstrInfo.td` 找 `ADD`、`SUB` 的 def，對照本章的範例。
3. 試改一個 `.td` 檔（e.g., 加 log）、重 build LLVM、看 `.s` 輸出是否改變。
4. 跑 `llvm-tblgen --print-records` 印所有 record，grep 找 `ADD` 相關的。
5. 寫一段 multiclass（本章範例基礎），產生 10 個 def。

## 自我檢核

- [ ] 我能寫一個簡單 class + def 的 TableGen
- [ ] 我知道 `let` vs `field` 的語意
- [ ] 我能讀一條 RISC-V instruction 的 `.td` 定義
- [ ] 我知道 multiclass 如何減少重複
- [ ] 我能用 `llvm-tblgen --print-records` dump 資料 debug

下一章走讀整個 `RISCVInstrInfo.td` —— RISC-V backend 的中樞神經。

→ [Ch 8 RISCVInstrInfo.td 走讀](./08-riscv-instrinfo-td.md)
