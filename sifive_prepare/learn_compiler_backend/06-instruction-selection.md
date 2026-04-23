# Ch 6 — Instruction selection：pattern matching

> 目標：理解 LLVM 的 instruction selection 機制 —— 把 legalized DAG 的 ISD node 轉成 target-specific MachineInstr。這是 TableGen pattern 發揮作用的地方，也是你加 custom instruction 時要修改的層級。

## Instruction selection 的目標

Legalization 結束後 DAG 全是 legal 的 generic ISD node。還需要：

```
Generic DAG:
  ISD::ADD i32 %a, %b
         ↓ instruction selection
RISC-V MachineInstr:
  ADD      x10, x11, x12
```

**每個 ISD node → 一條或多條 target instruction**。這個映射叫 instruction selection (ISel)。

## 兩種實作

LLVM 有兩套 ISel 系統：

### 1. SelectionDAG ISel（主流、成熟）

- 用 TableGen pattern match
- 處理 SelectionDAG → MachineInstr
- 所有 target 都有（包括 RISC-V）
- 本章主題

### 2. GlobalISel（新、發展中）

- 從 IR 直接到 MachineInstr，不經 DAG
- 支援 cross-BB 優化
- 某些 target（ARM AArch64）主力
- RISC-V 正在遷移中（Ch 10 深入）

2026 時點 **RISC-V 主要用 SelectionDAG**，本章聚焦它。

## Pattern matching 的概念

`.td` 裡宣告 pattern：

```
def : Pat<(ISD::ADD GPR:$a, GPR:$b),
          (ADD GPR:$a, GPR:$b)>;
```

意思：「遇到 DAG 上一個 `ISD::ADD` 以 `GPR$a` 跟 `GPR$b` 為 operand 的 pattern，換成 `ADD` 這條 MachineInstr」。

這是**最簡單的 pattern**：輸入 ISD node，輸出 target instruction，operand 直接對應。

`GPR` 是 register class。`$a` 是 pattern variable（對應 operand）。

## 複雜 pattern：multi-instruction

某些 `ISD` node 要對應多條 target instruction：

```
(ISD::ADD GPR:$a, (imm:$imm))
      ↓
ADDI GPR:$a, $imm       ; 如果 imm 在 -2048..2047
```

或：

```
(ISD::MUL GPR:$a, (imm 3))
      ↓
SH1ADD GPR:$a, GPR:$a  ; Zba: (a<<1) + a
```

這種「compiler 看到某 pattern 時換成特殊指令」就是 backend 優化的核心手段。

## Pattern 的三個元素

一條 pattern：

```
def : Pat<
    (輸入 DAG pattern),      // 要 match 的 DAG
    (輸出 MachineInstr)       // 替換的指令
>;
```

輸入可以嵌套：

```
def : Pat<(add (mul $a, $b), $c), (MADD $a, $b, $c)>;
```

意思：「`a*b + c` pattern」→ 一條 `MADD` 指令（如果 target 有）。RISC-V Zfhmin 有類似 fused pattern。

## Complex Pattern

有些 pattern 用 C++ function 判定 match：

```
def MyAddr : ComplexPattern<iPTR, 2, "SelectMyAddr",
                            [add, frameindex], []>;
```

C++ 實作：

```cpp
bool RISCVDAGToDAGISel::SelectMyAddr(SDValue N, SDValue &Base, SDValue &Offset) {
    // 判斷 N 是否 match 「base+offset」形式的 addressing
    // 若是，填 Base 跟 Offset，return true
    if (N.getOpcode() == ISD::ADD) {
        Base = N.getOperand(0);
        Offset = N.getOperand(1);
        return true;
    }
    return false;
}
```

Complex pattern 用於無法 declarative 表達的 match 邏輯（e.g., addressing mode 計算、constant 範圍檢查）。

## TableGen 生成 `.inc`

`RISCVInstrInfo.td` 被 `llvm-tblgen` 處理，產生：

```
RISCVGenDAGISel.inc       ; pattern match 的 C++ code
RISCVGenInstrInfo.inc      ; instruction info
RISCVGenAsmMatcher.inc     ; asm parser
RISCVGenDisassemblerTables.inc ; disassembler
RISCVGenRegisterInfo.inc   ; register info
...
```

`RISCVDAGToDAGISel.cpp` 會 include `RISCVGenDAGISel.inc` 取得 pattern match code。

**TableGen 是 LLVM backend 的 meta-compiler**。你改 `.td`、TableGen re-generate `.inc`、C++ 重 build → 新指令生效。

## SelectionDAG ISel 的工作流程

```cpp
// RISCVDAGToDAGISel.cpp
void RISCVDAGToDAGISel::Select(SDNode *Node) {
    // 1. 自訂 pre-select hook
    if (tryCustomSelect(Node)) return;

    // 2. 走 TableGen 生成的 pattern matcher
    SelectCode(Node);
}
```

`tryCustomSelect` 處理 pattern 難以表達的 case（例：某些 vector instruction 依賴 vsetvl 上下文）。

`SelectCode` 是 TableGen 生的 big function，內部是一棵 match tree。

## 看 ISel 過程

```bash
llc -debug-only=isel hello.ll 2>&1 | head -50
```

會看到：

```
ISEL: Starting selection on root node: t16: i32 = add nsw t14, t4
ISEL: Starting pattern match
  Morphed node: t16: i32 = ADDW t14, t4   ; ← matched pattern
ISEL: Match complete!
```

每個 node 一行。嘗試的 pattern 多、match 的時候 "Morphed"。

## 一個 RISC-V ADD pattern 的實際長相

`llvm/lib/Target/RISCV/RISCVInstrInfo.td`：

```tablegen
class ALU_rr<bits<7> funct7, bits<3> funct3, string opcodestr,
             bit Commutable = 0>
    : RVInstR<funct7, funct3, OPC_OP, (outs GPR:$rd), (ins GPR:$rs1, GPR:$rs2),
              opcodestr, "$rd, $rs1, $rs2">,
      Sched<[WriteIALU, ReadIALU, ReadIALU]> {
    let isCommutable = Commutable;
}

def ADD  : ALU_rr<0b0000000, 0b000, "add", /*Commutable=*/1>;
def SUB  : ALU_rr<0b0100000, 0b000, "sub">;
...

// Pattern
def : PatGprGpr<add, ADD>;
def : PatGprGpr<sub, SUB>;
```

`PatGprGpr` 是個 helper class：

```tablegen
class PatGprGpr<SDPatternOperator OpNode, RVInst Inst>
    : Pat<(OpNode GPR:$rs1, GPR:$rs2), (Inst GPR:$rs1, GPR:$rs2)>;
```

展開就是 `def : Pat<(add GPR:$rs1, GPR:$rs2), (ADD GPR:$rs1, GPR:$rs2)>`。

**這是加法的 pattern**。compile 時 IR 的 `add i32 %a, %b` 經過 legalization 變 `ISD::ADD`、這個 pattern match → RISC-V 的 `ADD` 指令。

## Operand type 約束

Pattern 常寫 operand type：

```
(ADD GPR:$rs1, GPR:$rs2)    ; 兩個都要是 GPR
(ADDI GPR:$rs1, simm12:$imm) ; 第二個是 signed 12-bit immediate
(LUI uimm20:$imm)             ; unsigned 20-bit
```

Immediate type（`simm12`, `uimm5`, `uimm20`...）在 `RISCVInstrInfo.td` 定義，表達 range 限制。

## ComplexPattern 的真實例子

RISC-V 的 addressing mode：

```tablegen
def AddrRegImm : ComplexPattern<iPTR, 2, "SelectAddrRegImm",
                                [], [SDNPWantRoot]>;
```

C++：

```cpp
bool RISCVDAGToDAGISel::SelectAddrRegImm(SDValue Addr, SDValue &Base, SDValue &Offset) {
    if (auto *FIN = dyn_cast<FrameIndexSDNode>(Addr)) {
        // frame index → base = stack pointer
        Base = CurDAG->getTargetFrameIndex(FIN->getIndex(), XLenVT);
        Offset = CurDAG->getTargetConstant(0, SDLoc(Addr), XLenVT);
        return true;
    }
    if (Addr.getOpcode() == ISD::ADD) {
        auto *RHS = dyn_cast<ConstantSDNode>(Addr.getOperand(1));
        if (RHS && isInt<12>(RHS->getSExtValue())) {
            Base = Addr.getOperand(0);
            Offset = CurDAG->getTargetConstant(RHS->getSExtValue(), SDLoc(Addr), XLenVT);
            return true;
        }
    }
    ...
    return false;
}
```

這個 C++ 判斷「這個地址 expr 能不能寫成 `base+imm`」，是 RISC-V `lw rd, imm(rs1)` 指令的關鍵。

## Pattern 的 complexity

多個 pattern 同時 match 時，LLVM 選「Complexity 最高」的。

```tablegen
def : Pat<..., ..., 10>;         // 低 complexity
def : Pat<..., ..., 100>;        // 高 complexity (優先)
```

手動指定 complexity 讓更複雜的 pattern 優先 match。例：`sh*add`（複合） > `slli + add`（分開）。

## 自訂 hook：manualSelect

有些情境 TableGen 不夠。你寫 C++：

```cpp
void RISCVDAGToDAGISel::Select(SDNode *Node) {
    unsigned Op = Node->getOpcode();

    // Custom handle VectorOp
    if (Op == ISD::INTRINSIC_VOID || Op == ISD::INTRINSIC_W_CHAIN) {
        tryIntrinsicSelect(Node);
        return;
    }
    ...
    SelectCode(Node);
}
```

Vector intrinsic 常走 custom select（因為有動態 vsetvl 上下文）。

## 選擇跟 Scheduling 的關係

ISel 結束後 DAG 上的 node 都是 MachineInstr 了。但還是 DAG 形式，尚未序列化。**Scheduling** 把它變成 linear MBB。Ch 13 專講。

從此 ISel 的 output 進入 MIR 世界。

## 加 custom instruction 的 ISel 修改

當你加一條 RISC-V extension 指令（Ch 9 做完整 demo），需要：

1. 在 `.td` 宣告 instruction
2. 寫 pattern 讓它被 match
3. 需要 C++ 自訂邏輯時，寫 Select hook

大部分情況只要 step 1 + 2。

## 一個 end-to-end example

假設加一條新指令 `CUSTOM_MADD rd, rs1, rs2, rs3` = `(rs1 * rs2) + rs3`。

`.td`：

```tablegen
let Predicates = [HasStdExtXCustom] in {
def CUSTOM_MADD
    : RVInstR4<0b00, 0b000, OPC_CUSTOM_0, (outs GPR:$rd),
               (ins GPR:$rs1, GPR:$rs2, GPR:$rs3),
               "custom.madd", "$rd, $rs1, $rs2, $rs3">;
}

def : Pat<(add (mul GPR:$rs1, GPR:$rs2), GPR:$rs3),
          (CUSTOM_MADD GPR:$rs1, GPR:$rs2, GPR:$rs3)>,
      Requires<[HasStdExtXCustom]>;
```

Pattern 意思：「看到 `(a*b) + c` 且 target 有 XCustom extension → 用 CUSTOM_MADD」。

compiler 產生這種 code 的情境：

```c
int foo(int a, int b, int c) { return a * b + c; }
```

沒 XCustom：

```asm
mul  t0, a0, a1
add  a0, t0, a2
```

有 XCustom：

```asm
custom.madd a0, a0, a1, a2
```

**一行 pattern 換來一條指令的優化**。

## 常見誤會

1. **「Pattern 越多越好」**：過多 pattern 使 match 慢、conflict 多。精選。
2. **「TableGen 可以表達所有 pattern」**：不。dynamic condition（vector context、runtime value）要 C++ hook。
3. **「ISel 之後 DAG 不再是 DAG」**：仍是 DAG、只是 node 從 ISD::... 變成 target instruction。Scheduling 才線性化。
4. **「Pattern match 是 greedy」**：是基於 complexity 的 best match。有 priority、但同 priority 可能 arbitrary。
5. **「ISel 處理所有轉換」**：legalization → ISel 共同責任。ISel 假設 legalization 已處理不合法的東西。

## 動手練習

1. 用 `llc -debug-only=isel hello.ll` 看一個簡單 function 的每個 node 的 pattern match。
2. 在 `RISCVInstrInfo.td` 找 `def ADD` 跟其 pattern，對照展開的 Pat。
3. 寫一段 C code 產生 `a * b + c`，看 compiler 是否 match `madd` 或類似 fused pattern（可能需要特定 extension）。
4. 讀 `RISCVInstrInfoZbb.td`，看 `popcount` 的 pattern 怎麼連到 `CPOP` 指令。
5. 用 `-print-after=finalize-isel` 看 ISel 結束後的 MIR。

## 自我檢核

- [ ] 我能用 `def : Pat<...>` 寫一條簡單 pattern
- [ ] 我知道 ComplexPattern 為何要 C++
- [ ] 我能讀 TableGen 展開的 `.inc` 檔找特定 pattern
- [ ] 我能用 `-debug-only=isel` 追 pattern match 過程
- [ ] 我知道 ISel 完成後 DAG 變成 target MachineInstr

Part 2 結束。下一章進入 Part 3 —— TableGen 語言本身。這是加 custom instruction 的主力語言。

→ [Ch 7 TableGen 語言速成](./07-tablegen-language.md)
