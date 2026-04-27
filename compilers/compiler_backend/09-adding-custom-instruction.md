# Ch 9 — 加一條 custom instruction 端到端

> 目標：走一次完整流程 —— 加一條新指令 `XMADD rd, rs1, rs2, rs3`（`rs1 * rs2 + rs3`）到 LLVM RISC-V backend。從 feature 宣告、instruction def、pattern match、到 assembler / disassembler 支援 + 驗證。

## 任務定義

我們要加的指令：

```
XMADD rd, rs1, rs2, rs3     # rd = rs1 * rs2 + rs3 (signed)
```

屬於 imaginary extension "XMyExt"。格式：RISC-V R4-type（4 個 register operand）。opcode 放 `custom-0` 區。

**目標**：

1. `-march=rv64g_xmyext` flag 啟用
2. 看到 C code `a * b + c` → compiler 產 `xmadd`
3. Assembler 認得 `xmadd` 指令
4. Disassembler 能反組譯回來

## Step 1: 宣告 subtarget feature

編輯 `llvm/lib/Target/RISCV/RISCVFeatures.td`：

```tablegen
def FeatureStdExtXMyExt
    : SubtargetFeature<"xmyext", "HasStdExtXMyExt", "true",
                       "'XMyExt' (My Custom Extension)">;

def HasStdExtXMyExt
    : Predicate<"Subtarget->hasStdExtXMyExt()">,
      AssemblerPredicate<(all_of FeatureStdExtXMyExt),
                         "'XMyExt' (My Custom Extension)">;
```

解讀：

- `FeatureStdExtXMyExt`：寫到 C++ 的 flag、綁到 `-march=...xmyext`
- `HasStdExtXMyExt`：Predicate，給 pattern / instruction 的 gating 用

## Step 2: 加到 RISCV.td

```tablegen
// 在 RISCV.td 的 include 部分
include "RISCVInstrInfoXMyExt.td"
```

## Step 3: 定義 R4-type format

RISC-V base 沒 R4-type（需要 5-bit rs3 + 2-bit funct2 + 3-bit funct3 + ...）。我們先加 format class。

編輯 `RISCVInstrFormats.td`（或放在新檔案裡）：

```tablegen
class RVInstR4_XMyExt<bits<2> funct2, bits<3> funct3, RVInstOpcode opcode,
                     dag outs, dag ins, string opcodestr, string argstr>
    : RVInst<outs, ins, opcodestr, argstr, [], InstFormatR4> {
    bits<5> rs3;
    bits<5> rs2;
    bits<5> rs1;
    bits<5> rd;

    let Inst{31-27} = rs3;
    let Inst{26-25} = funct2;
    let Inst{24-20} = rs2;
    let Inst{19-15} = rs1;
    let Inst{14-12} = funct3;
    let Inst{11-7}  = rd;
    let Inst{6-0}   = opcode.Value;
}
```

這個 format 沿用 F extension 的 fmadd / fmsub 格式（它們也是 R4-type）。

## Step 4: 建立 `RISCVInstrInfoXMyExt.td`

```tablegen
//===-- RISCVInstrInfoXMyExt.td - RISC-V XMyExt ---*- tablegen -*-===//

let Predicates = [HasStdExtXMyExt] in {

let hasSideEffects = 0, mayLoad = 0, mayStore = 0 in
def XMADD : RVInstR4_XMyExt<0b00, 0b000, OPC_CUSTOM_0,
                            (outs GPR:$rd),
                            (ins GPR:$rs1, GPR:$rs2, GPR:$rs3),
                            "xmadd", "$rd, $rs1, $rs2, $rs3">,
            Sched<[WriteIALU, ReadIALU, ReadIALU, ReadIALU]>;

} // Predicates = [HasStdExtXMyExt]

//===----------------------------------------------------------------------===//
// Codegen patterns
//===----------------------------------------------------------------------===//

let Predicates = [HasStdExtXMyExt] in {

// Pattern: rs1 * rs2 + rs3 → XMADD
def : Pat<(add (mul GPR:$rs1, GPR:$rs2), GPR:$rs3),
          (XMADD GPR:$rs1, GPR:$rs2, GPR:$rs3)>;

} // Predicates = [HasStdExtXMyExt]
```

**關鍵**：`OPC_CUSTOM_0` 要先在 `RISCVInstrFormats.td` 定義：

```tablegen
def OPC_CUSTOM_0  : RVInstOpcode<0b0001011>;
```

## Step 5: Parse `-march=...xmyext`

`-march` 的解析由 `llvm/lib/Support/RISCVISAInfo.cpp` 處理。搜尋其他 extension（例：`zba`）找到對應區域、加上：

```cpp
// 簡化範例
static const RISCVSupportedExtension SupportedExtensions[] = {
    ...
    {"xmyext", {1, 0}},
    ...
};
```

這讓 `-march=rv64gc_xmyext` 被認得。

（實務 LLVM 有 `RISCVExtensionsDependencies.def` / `RISCVExtensionBitmask.def` 等更複雜的表，視版本而定）

## Step 6: Build 驗證

```bash
cd build
ninja llc
```

Ninja 會重跑 TableGen + 重 build 相關 `.cpp`。大約 2-5 分鐘。

如果 `.td` 有錯，TableGen 會報：

```
error: Value 'xmadd' unknown!
```

回去修。

## Step 7: 測試 —— assembler

```bash
echo "xmadd x5, x6, x7, x8" | ./bin/llvm-mc -triple=riscv64 -mattr=+xmyext -show-encoding
```

期望輸出：

```
xmadd x5, x6, x7, x8         # encoding: [0x8b,0x02,0x73,0x40]
```

（具體 bytes 依 funct2/funct3/opcode 而定）

如果沒加 `-mattr=+xmyext` 則 assembler 拒絕：

```
error: instruction requires the following: 'XMyExt' (My Custom Extension)
```

## Step 8: 測試 —— disassembler

```bash
echo "xmadd x5, x6, x7, x8" | ./bin/llvm-mc -triple=riscv64 -mattr=+xmyext -show-encoding 2>/dev/null | \
    grep -o '0x[0-9a-f]\+' | head -4 | tr '\n' ' '

# 假設得到: 0x8b 0x02 0x73 0x40
# 反組譯：
echo "0x8b 0x02 0x73 0x40" | xxd -r -p | ./bin/llvm-mc -triple=riscv64 -mattr=+xmyext -disassemble
```

期望 disassembler 吐回 `xmadd x5, x6, x7, x8`。

## Step 9: 測試 —— compiler 自動 match

寫 C：

```c
// test.c
int foo(int a, int b, int c) { return a * b + c; }
```

```bash
./bin/clang --target=riscv64-unknown-elf -march=rv64gc_xmyext -O2 -S test.c -o test.s
cat test.s
```

期望：

```asm
foo:
    xmadd  a0, a0, a1, a2
    ret
```

如果 pattern 沒 match（仍看到 `mul + add`），檢查：

1. Pattern 的 Requires 是否對
2. `-march` 是否正確啟用 feature
3. DAG combining 階段有沒有別的 pass 先處理掉

## Step 10: 寫 LLVM regression test

```
// llvm/test/CodeGen/RISCV/xmyext.ll
; RUN: llc -mtriple=riscv64 -mattr=+xmyext < %s | FileCheck %s

define i32 @foo(i32 %a, i32 %b, i32 %c) {
; CHECK-LABEL: foo:
; CHECK:       xmadd a0, a0, a1, a2
  %mul = mul i32 %a, %b
  %res = add i32 %mul, %c
  ret i32 %res
}
```

跑：

```bash
./bin/llvm-lit llvm/test/CodeGen/RISCV/xmyext.ll
```

`FileCheck` 檢查 output 含 `xmadd a0, ...`。這是 LLVM regression test 的標準格式。

## Step 11: 加 intrinsic（可選）

讓 C code 能明確呼叫：

```c
int x = __riscv_xmadd(a, b, c);
```

在 `llvm/include/llvm/IR/IntrinsicsRISCV.td` 宣告：

```tablegen
let TargetPrefix = "riscv" in {
  def int_riscv_xmadd
      : Intrinsic<[llvm_anyint_ty],
                  [LLVMMatchType<0>, LLVMMatchType<0>, LLVMMatchType<0>],
                  [IntrNoMem, IntrSpeculatable]>;
}
```

然後在 `RISCVInstrInfoXMyExt.td` 加 pattern：

```tablegen
def : Pat<(int_riscv_xmadd GPR:$rs1, GPR:$rs2, GPR:$rs3),
          (XMADD GPR:$rs1, GPR:$rs2, GPR:$rs3)>;
```

Clang 那邊要加 builtin 定義（`clang/include/clang/Basic/BuiltinsRISCV.def`）：

```
TARGET_BUILTIN(__builtin_riscv_xmadd, "iiii", "nc", "xmyext")
```

這讓 C 層有 `__builtin_riscv_xmadd(a, b, c)` 可用。

## 遇到問題怎麼 debug

### 症狀 1: Pattern 沒 match

```bash
./bin/llc -mattr=+xmyext -debug-only=isel test.ll 2>&1 | head -50
```

看 ISel 過程，找 `Starting selection on root node: t16: i32 = add ...` 之類，看它實際 match 到什麼。

### 症狀 2: TableGen 錯

```bash
ninja llc 2>&1 | less
```

找 `error:` 行。常見錯誤：

- `Value 'xxx' unknown!` → typo
- `Cannot parse pattern` → pattern 語法錯
- `Instruction XXX has inconsistent value of Inst{N}` → encoding bit 衝突

### 症狀 3: 產生的 binary 不認得

Assembler 能接受 `xmadd`、但 linker 或下游 tool 不認。**可能 opcode 衝突**（`custom-0` 某個 sub-space 被其他 extension 用）。查 `OPC_CUSTOM_*` 是否唯一。

## 一個重要細節：隔離 .td 檔

**新 extension 永遠開新 `.td` 檔**，不要混進現有的。好處：

- 清楚看到「這個 extension 的全部」
- 可以獨立 enable/disable
- upstream 時 reviewer 好看

`RISCVInstrInfoXMyExt.td` 的命名遵循 `RISCVInstrInfoXSf*.td` 的慣例。

## 更複雜的 custom instruction

本章範例簡化了很多。真實 custom extension 可能：

1. 有 memory side effect → 要設 `mayLoad = 1; mayStore = 1;`
2. 改 CSR → 要設 `Defs = [CSRX];`
3. 只在特定上下文合法 → 多 predicate
4. 需要 ComplexPattern 處理動態 operand

處理這些：看 `RISCVInstrInfoV.td`（RVV）跟 `RISCVInstrInfoXSf*.td`（SiFive vendor）的範例。

## 還要修的地方（完整性）

做到 production quality：

1. **Disassembler**：自動從 `.td` 生成，但 custom extension 可能要 add `DecodeXXX` function
2. **Assembler**：自動生成，但複雜 operand 要 add parser method
3. **Scheduling model**：`Sched<[...]>` 要對應實際 core 的 latency
4. **Extension version**：`Zxxx1p0` 之類的版本標示
5. **Profile compatibility**：如果進 profile 要檢查 interaction
6. **Doc**：`llvm/docs/RISCVUsage.rst` 加說明
7. **Upstream**：review 過可能要改 n 次（Ch 19）

本章是入門 demo。完整 submission 要做 1-2 週的完整工作。

## 完整檔案清單

改過的檔案（簡化版）：

```
llvm/lib/Target/RISCV/RISCVFeatures.td              ; + FeatureStdExtXMyExt
llvm/lib/Target/RISCV/RISCV.td                       ; + include
llvm/lib/Target/RISCV/RISCVInstrInfoXMyExt.td        ; new file
llvm/lib/Target/RISCV/RISCVInstrFormats.td           ; + OPC_CUSTOM_0 (if not exist)
llvm/lib/Support/RISCVISAInfo.cpp                    ; + extension entry
llvm/test/CodeGen/RISCV/xmyext.ll                    ; test
llvm/test/MC/RISCV/xmyext.s                          ; assembler test
```

實際 commit 約 200-500 行。

## 常見誤會

1. **「改 `.td` 立刻生效」**：不，要 ninja 重跑 TableGen + rebuild。
2. **「我不 care assembler / disassembler，只要 compiler 會用」**：不行。assembler 要支援，否則 inline asm、手寫 `.S` 沒法測。
3. **「Pattern 寫一條就夠」**：可能要多個 variant（不同 operand order、commutative 對稱等）。
4. **「一條 extension 指令最快半天寫完」**：demo 可以，production 要 scheduling / testing / doc 兩週起跳。
5. **「opcode 衝突不會發生」**：會。不同 vendor extension 可能撞。必須有 registry 檢查。

## 動手練習

1. 實際 follow 本章 stepby step，加一條 `XSUB3 rd, rs1, rs2` = `rs1 - rs2 * 3`（或任意你的設計）。
2. 寫對應的 FileCheck test。
3. 故意設錯 opcode 某個 bit，看 TableGen 會不會警告。
4. 試加一條 memory instruction（有 mayLoad），注意 side effect 宣告。
5. 讀一個 SiFive vendor extension 的 `.td`（例：`RISCVInstrInfoXSfvcp.td`），對照本章架構看差異。

## 自我檢核

- [ ] 我能從零加一條 RISC-V custom instruction 到 LLVM
- [ ] 我知道要動 Feature / InstrInfo / ISAInfo 三個地方
- [ ] 我能寫 pattern 讓 C code 的模式自動 match
- [ ] 我能寫 FileCheck test 驗證結果
- [ ] 我知道 production quality 要多做哪些事（doc、sched、etc）

Part 3 結束。下一章進入 Part 4 —— GlobalISel 是新一代 ISel，RISC-V 正在遷移中。

→ [Ch 10 GlobalISel vs SelectionDAG：為什麼在遷移](./10-globalisel-vs-selectiondag.md)
