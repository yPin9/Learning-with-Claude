# Ch 17 — MC layer：assembler / disassembler / streamer

> 目標：理解 LLVM 的 MC layer（Machine Code Layer） — 處理 assembler / disassembler / object file emission 的底層。讀完你能 debug 「encoding 錯」這類 bug、能為 custom instruction 加 asm parser / disassembler 支援。

## 什麼是 MC layer

**MC layer** 是 LLVM 中 "machine code" 的抽象：**比 MIR 更低層、比 bytes 更抽象**。

```
MIR (MachineInstr)
  ↓ MCInst emission
MCInst (MCInst + operand)
  ↓ MC streamer
Object bytes / asm text
```

MCInst 是「已經選好 operand 的具體指令」。跟 MachineInstr 像但更簡。

## 為什麼要獨立 MC layer

歷史：LLVM 最初沒 MC。所有 asm emission 在 MIR 層直接 format 成 string。問題：

- **Disassembler 要自己另寫一套**：不共用
- **LLVM 自己的 assembler (llvm-mc) 也要獨立**
- **Integrated assembler**：clang 想直接把 asm 組成 `.o`，要共用 logic

2009 加入 MC layer，把 encoding / decoding / emission 統一。

## 三個核心物件

### 1. MCInst

```cpp
class MCInst {
    unsigned Opcode;
    SmallVector<MCOperand, 8> Operands;
};
```

類似 MachineInstr 但沒 metadata、沒 virtual register。純 "what instruction, what operands"。

### 2. MCOperand

```cpp
class MCOperand {
    enum { kInvalid, kRegister, kImmediate, kFPImmediate, kExpr, kInst } Kind;
    union { ... };
};
```

operand 可以是 register、immediate、expression（symbol reference）、甚至 nested MCInst。

### 3. MCStreamer

把 MCInst stream 輸出成 text 或 object file：

```cpp
class MCStreamer {
    virtual void emitInstruction(const MCInst &Inst, ...) = 0;
    virtual void emitLabel(MCSymbol *Symbol, ...) = 0;
    virtual void emitSymbolValue(...) = 0;
    ...
};

// Concrete 實作
class MCAsmStreamer : public MCStreamer;      // output asm text
class MCObjectStreamer : public MCStreamer;   // output .o
```

一個 streamer 吃 MCInst、吐 bytes。

## MC 的子 module

```
llvm/lib/MC/                     ← generic MC code
llvm/lib/Target/RISCV/MCTargetDesc/
    RISCVMCCodeEmitter.cpp       ← MCInst → bytes
    RISCVMCTargetDesc.cpp         ← MC 初始化
    RISCVAsmBackend.cpp           ← relaxation, fixup
    RISCVELFObjectWriter.cpp      ← ELF emission
    RISCVInstPrinter.cpp          ← MCInst → asm text
llvm/lib/Target/RISCV/AsmParser/
    RISCVAsmParser.cpp            ← asm text → MCInst
llvm/lib/Target/RISCV/Disassembler/
    RISCVDisassembler.cpp         ← bytes → MCInst
```

加新 instruction 時可能每個 file 都要改。

## MIR → MCInst：AsmPrinter

最後階段 `AsmPrinter` pass 把 MIR 轉成 MCInst：

```cpp
void RISCVAsmPrinter::emitInstruction(const MachineInstr *MI) {
    MCInst TmpInst;
    LowerRISCVMachineInstrToMCInst(MI, TmpInst, *this);
    EmitToStreamer(*OutStreamer, TmpInst);
}
```

`LowerRISCVMachineInstrToMCInst` 在 `RISCVMCInstLower.cpp` 裡。每個 MI 產生一個 MCInst（少數情況多個）。

## MCCodeEmitter：MCInst → bytes

```cpp
// RISCVMCCodeEmitter.cpp
void RISCVMCCodeEmitter::encodeInstruction(const MCInst &MI, raw_ostream &OS, ...) {
    uint32_t Bits = getBinaryCodeForInstr(MI, Fixups, STI);
    support::endian::write<uint32_t>(OS, Bits, support::little);
}
```

`getBinaryCodeForInstr()` 是 TableGen 生成的 function，按 `.td` 裡的 `Inst{...}` 定義產 bits。

## Fixup：linker 填值的佔位符

遇到 symbol reference 的指令（例 `call foo`）：

```
auipc ra, 0      ← 假值
jalr  ra, 0      ← 假值
```

真值要 linker 填（對應 Ch 5 of learn_elf_linking）。MC emit 時產生 **Fixup**：

```cpp
Fixups.push_back(MCFixup::create(Offset, Value, MCFixupKind::fixup_riscv_call));
```

`MCAsmBackend::applyFixup()` 最終填值。如果 output 是 `.o`，fixup 會變 relocation entry 寫進 `.rela.text`。

## Asm parser

`RISCVAsmParser.cpp`：讀 `.s` → 產 MCInst。

核心 function：

```cpp
bool RISCVAsmParser::ParseInstruction(ParseInstructionInfo &Info, StringRef Name,
                                       SMLoc NameLoc, OperandVector &Operands) {
    // 解析 mnemonic（Name）
    // 對每個 operand 呼叫 parser
    // 最後 match 到 MCInst
}
```

Mnemonic 比對用 TableGen 生的 `RISCVGenAsmMatcher.inc`。operand 解析是手寫 C++。

### Immediate parsing

```cpp
// 解析 "123" 或 "0x7f" 成 MCExpr
bool parseImmediate(OperandVector &Operands) {
    // parse int / hex / symbol
    if (getLexer().is(AsmToken::Integer)) {
        Imm = getLexer().getTok().getIntVal();
        Operands.push_back(RISCVOperand::createImm(Imm, ...));
        return false;
    }
    // fallback: parse as symbol reference
    ...
}
```

### Pseudo instruction expansion

`.s` 裡常用 pseudo：

```
li a0, 42       → addi a0, x0, 42
ret              → jalr x0, ra, 0
nop              → addi x0, x0, 0
```

asm parser 認得 pseudo 時展開成真實 instruction（或依 TableGen 的 `PseudoInstExpansion`）。

## Disassembler

`RISCVDisassembler.cpp`：bytes → MCInst。

```cpp
DecodeStatus RISCVDisassembler::getInstruction(MCInst &MI, uint64_t &Size, ArrayRef<uint8_t> Bytes, ...) {
    // 讀 2 byte (for compressed) or 4 byte
    // 查 TableGen 生成的 decode table
    // 填 MCInst
}
```

decode table 在 `RISCVGenDisassemblerTables.inc` (TableGen 生)。

某些 operand 需要自訂 decoder：

```cpp
// DecodeGPRRegisterClass
static DecodeStatus DecodeGPRRegisterClass(MCInst &Inst, uint32_t RegNo, ...) {
    if (RegNo >= 32)
        return MCDisassembler::Fail;
    Inst.addOperand(MCOperand::createReg(RISCV::X0 + RegNo));
    return MCDisassembler::Success;
}
```

## InstPrinter

MCInst → human-readable asm：

```cpp
// RISCVInstPrinter.cpp
void RISCVInstPrinter::printInstruction(const MCInst *MI, uint64_t Address, const MCSubtargetInfo &STI, raw_ostream &O) {
    // TableGen 生的 print function
    printInstructionBody(MI, O);
}
```

用 TableGen 產的 `RISCVGenAsmWriter.inc`。

某些操作要自訂：

```cpp
void RISCVInstPrinter::printRegName(raw_ostream &O, MCRegister Reg) const {
    O << getRegisterName(Reg);    // "a0", "sp", "x10", etc.
}
```

依 `RegisterName` 選 ABI name (`a0`) 或 numeric (`x10`)。

## 用 llvm-mc 測 MC

LLVM 的 `llvm-mc` 是 MC layer 的 testbed：

```bash
# Assemble one instruction
echo "addi a0, a1, 42" | llvm-mc -triple=riscv64 -show-encoding
# Output:
#     addi a0, a1, 42               # encoding: [0x13,0x85,0xa5,0x02]

# Disassemble
echo "0x13 0x85 0xa5 0x02" | xxd -r -p | llvm-mc -triple=riscv64 -disassemble
# Output:
#     addi a0, a1, 42

# Verify round-trip
```

這是加 custom instruction 時 verify encoding / asm / disasm 的標準流程。

## Integrated assembler

Clang 內建 assembler（走 MC layer）。`clang -c hello.c` 直接產 `.o`，不 invoke external `as`。

好處：

- 快（不用 subprocess）
- Consistent（asm 跟 compiler 在同一套 codebase）
- Better error messages

壞處：

- 某些 GNU as extension 不支援
- 某些 macro 行為有差

用 `-fno-integrated-as` 可以 fallback 到 `as`：

```bash
clang -fno-integrated-as hello.s -o hello.o
```

## RISC-V Integrated assembler 的狀況

RISC-V 的 integrated-as 大部分 feature work：

- 基本 ISA ✓
- Pseudo instructions ✓
- Relocation ✓
- Relaxation ✓

少數 GNU extension 不支援，碰到再 fallback。

## 加 asm parser 支援（Ch 9 的延伸）

回到 Ch 9 加 `XMADD` 指令。asm parser 基本會被 TableGen 自動處理（因為 `.td` 有 `def XMADD`）。

但如果指令有特殊 operand（比如 custom addressing mode），要手寫 parsing method。例子：RVV 的 `v0.t` mask operand 就需要 custom parse。

## Relocation 在 MC 層

MC 產生 object file 時，對 symbol reference 產 relocation entry：

```cpp
// RISCVELFObjectWriter.cpp
unsigned RISCVELFObjectWriter::getRelocType(MCContext &Ctx, const MCValue &Target, const MCFixup &Fixup, bool IsPCRel) const {
    switch (Fixup.getTargetKind()) {
    case RISCV::fixup_riscv_call:
        return ELF::R_RISCV_CALL_PLT;
    case RISCV::fixup_riscv_pcrel_hi20:
        return ELF::R_RISCV_PCREL_HI20;
    ...
    }
}
```

每個 MC fixup kind 對應一個 ELF relocation type。

`learn_elf_linking` Ch 5 講的所有 relocation 在這裡誕生。

## Relax at assembler level

**有些 relaxation 是在 assembler 就做，不是 linker**。例：

- `li a0, 42` pseudo → assembler 立刻展開（根據 value 決定 `addi` 或 `lui+addi`）
- branch 距離太遠 → assembler 自動換成 jump

這些在 `RISCVAsmBackend.cpp` 的 `fixupNeedsRelaxation` / `relaxInstruction`。

Linker relaxation 比這個大範圍、跨 section；assembler relaxation 是 local。

## Debug MC

```bash
# Show encoding
llvm-mc -triple=riscv64 hello.s -show-encoding

# Show instructions after relax
llvm-mc -triple=riscv64 -mattr=+c hello.s -filetype=obj -o hello.o
llvm-objdump -d hello.o

# Show relocations
llvm-objdump -r hello.o
```

## 加 instruction 到 MC layer：完整 checklist

假設 `XMADD` 已經在 `RISCVInstrInfoXMyExt.td` 定義。MC layer 自動得到：

- ✓ Encoding (via TableGen `getBinaryCodeForInstr`)
- ✓ Disassembly (via decode table)
- ✓ Asm printing (via `RISCVGenAsmWriter`)
- ✓ Asm parsing (via `RISCVGenAsmMatcher`)

**多數情況自動 work**。要 verify：

```bash
# Build llc + llvm-mc
ninja llc llvm-mc

# Test encoding
echo "xmadd x5, x6, x7, x8" | ./bin/llvm-mc -triple=riscv64 -mattr=+xmyext -show-encoding
# 期望: xmadd x5, x6, x7, x8   # encoding: [...]

# Test disassembly
echo "0x8b 0x02 0x73 0x40" | xxd -r -p | ./bin/llvm-mc -triple=riscv64 -mattr=+xmyext -disassemble
# 期望: xmadd x5, x6, x7, x8
```

如果 assembler 不認得、或 disassembler 印錯名字 → 檢查 `.td` 的 opcode 定義。

## 寫 MC test

LLVM 有 MC-level test：

```
llvm/test/MC/RISCV/
  rv64i-valid.s
  rv64i-invalid.s
  zbb-valid.s
  xmyext-valid.s     ← 你加新的
```

格式：

```
# llvm/test/MC/RISCV/xmyext-valid.s
# RUN: llvm-mc -triple=riscv64 -mattr=+xmyext -show-encoding < %s | FileCheck %s

# CHECK: xmadd x5, x6, x7, x8
# CHECK: encoding: [0x8b,0x02,0x73,0x40]
xmadd x5, x6, x7, x8
```

這種 test 跟 Ch 9 的 codegen test 互補。

## 常見誤會

1. **「MC 只處理 `.s` → `.o`」**：不。也處理 inline asm、disasm、object dump。
2. **「Encoding 錯就是 `.td` 錯」**：可能。也可能 MCCodeEmitter 自訂 method 錯（某些 custom operand 需要）。
3. **「`llvm-mc` 跟 clang 用不同 MC」**：同一套。`clang` 的 integrated-as 就是 MC。
4. **「MCInst 跟 MachineInstr 一樣」**：類似但 MCInst 輕量（沒 RA 資訊、virtual reg 等）。
5. **「Disassembler 永遠 works」**：有 bug 機會。custom encoding 要對應 decode function。

## 動手練習

1. 用 `llvm-mc -show-encoding` 測試 5 條 RISC-V 指令的 encoding。
2. 手寫一個 `.s` 檔含 pseudo `li a0, 0x12345678`，用 `llvm-mc -filetype=obj` 產 `.o`，`objdump -d` 看 assembler 怎麼展開。
3. 讀 `RISCVMCCodeEmitter.cpp` 的 `getImmOpValue` 等 method，看 encoding override 怎麼做。
4. 寫一個 Zbb 的 asm test，用 `FileCheck` 格式。
5. 試 disassemble 一段 random bytes，看 disasm 怎麼處理 invalid encoding。

## 自我檢核

- [ ] 我能解釋 MCInst / MCOperand / MCStreamer 的角色
- [ ] 我知道 MIR → MCInst → bytes 的轉換流程
- [ ] 我能用 `llvm-mc` 測試 encoding / disasm
- [ ] 我能找到 MC layer 相關檔案
- [ ] 我能寫 MC-level FileCheck test

下一章對照 GCC backend，讓你在兩個主流 compiler 之間有橋。

→ [Ch 18 GCC 對照篇：machine description / match.pd](./18-gcc-backend-comparison.md)
