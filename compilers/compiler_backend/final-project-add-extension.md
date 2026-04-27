# Final Project — 加一個 custom extension 端到端

> 目標：從零加一個完整的 RISC-V custom extension 到 LLVM。包含 Clang builtin、LLVM IR intrinsic、TableGen pattern、MC support、scheduling、FileCheck test。完成後你有一個可以放履歷、面試拿出來 demo 的完整 portfolio。

## 為什麼這是好 final project

1. **整合整個課程**：從 Ch 0 的 build 到 Ch 20 的 source map 全部用到
2. **對 SiFive 面試 killing**：能說「我加過 RISC-V extension」是 staff engineer 級的簡歷加分
3. **可 incremental**：MVP 1 週、production-quality 1 個月
4. **放 GitHub**：面試直接亮 source code link

## 設計：XMyMA extension

我們要加的 extension 叫 `XMyMA`（My Multiply-Add）：

```
XMyMA contains:
  XMADD rd, rs1, rs2, rs3      ; rd = rs1 * rs2 + rs3
  XMSUB rd, rs1, rs2, rs3      ; rd = rs1 * rs2 - rs3
  XNMADD rd, rs1, rs2, rs3     ; rd = -(rs1 * rs2) + rs3
```

三條 fused multiply-add 指令。有用 + 不太複雜 + 正好 fit R4-type。

**現實世界對照**：F extension 本身就有這些指令（`fmadd.s` 等），我們做 integer 版本的 custom extension。

## MVP 定義

最小可用：

- [x] `-march=rv64g_xmyma` 啟用
- [x] C code `a * b + c` → compiler 產 `xmadd`
- [x] C code `a * b - c` → `xmsub`
- [x] C code `c - a * b` → `xnmadd`
- [x] `__builtin_riscv_xmadd/xmsub/xnmadd` intrinsic 可用
- [x] Assembler / disassembler 認得 `xmadd`/`xmsub`/`xnmadd`
- [x] Test 全 pass

估算：2500-4000 行 change、1-2 週。

## Stretch goals

- GlobalISel 支援
- Scheduling model (for SiFive 7, SiFive P400, etc.)
- GCC 對應版本（Ch 18 知識應用）
- 效能 benchmark（SPEC / Coremark 子集）
- Upstream PR（真的送 LLVM！）

最終版本 = 送 PR = 你是 LLVM contributor。

## 階段 milestone

### Week 1 — MVP

**Day 1-2: Setup**
- Fork LLVM、建 branch
- Build release + debug 兩個 build

**Day 3-4: Feature + 第一條 instruction (XMADD)**
- 加 `FeatureStdExtXMyMA` 到 `RISCVFeatures.td`
- 加到 `RISCVISAInfo.cpp`
- 建 `RISCVInstrInfoXMyMA.td` 檔、定義 `XMADD`
- 寫 pattern: `(add (mul ...), ...)` → `XMADD`

**Day 5: Test XMADD**
- 寫 FileCheck test (`llvm/test/CodeGen/RISCV/xmyma.ll`)
- 寫 MC test (`llvm/test/MC/RISCV/xmyma-valid.s`)
- verify encoding / disasm 正確

**Day 6: XMSUB + XNMADD**
- 加另外兩條指令
- Pattern 寫對（`sub` 的方向要注意）

**Day 7: Intrinsic**
- Clang builtin: `__builtin_riscv_xmadd` 等
- LLVM intrinsic: `@llvm.riscv.xmadd`
- Pattern: intrinsic → XMADD

### Week 2 — Polish

**Day 8: Comprehensive test**
- Edge case (signed overflow, commutative swap, 等)
- 組合 test (nested madd + msub)

**Day 9: Scheduling**
- 在某個 SiFive sched model 加 `XMADD`/etc 的 latency
- 假設跟 MUL 類似的 latency

**Day 10: Documentation**
- `llvm/docs/RISCVUsage.rst` 加描述
- code 加 doxygen comment

**Day 11-12: Integration test**
- 編個有 madd/msub 的 benchmark (e.g., dot product)
- 對比 `-march=rv64g` vs `-march=rv64g_xmyma` 的 asm + perf

**Day 13-14: Cleanup + README**
- Code format (clang-format)
- 寫 project README
- 錄 demo video / 截圖

## Step-by-step 實作

### Step 1: Feature

```tablegen
// RISCVFeatures.td
def FeatureStdExtXMyMA
    : SubtargetFeature<"xmyma", "HasStdExtXMyMA", "true",
                       "'XMyMA' (My Multiply-Add Extension)">;

def HasStdExtXMyMA
    : Predicate<"Subtarget->hasStdExtXMyMA()">,
      AssemblerPredicate<(all_of FeatureStdExtXMyMA),
                         "'XMyMA' (My Multiply-Add Extension)">;
```

### Step 2: InstrInfo

建 `RISCVInstrInfoXMyMA.td`：

```tablegen
//===-- RISCVInstrInfoXMyMA.td - RISC-V XMyMA ------*- tablegen -*-===//

// R4-type format (similar to FMADD)
class RVInstR4_XMyMA<bits<2> funct2, bits<3> funct3, RVInstOpcode opcode,
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

//===----------------------------------------------------------------------===//
// Instruction class
//===----------------------------------------------------------------------===//

let Predicates = [HasStdExtXMyMA], hasSideEffects = 0,
    mayLoad = 0, mayStore = 0 in {

def XMADD : RVInstR4_XMyMA<0b00, 0b000, OPC_CUSTOM_0,
                           (outs GPR:$rd),
                           (ins GPR:$rs1, GPR:$rs2, GPR:$rs3),
                           "xmadd", "$rd, $rs1, $rs2, $rs3">,
            Sched<[WriteIMul, ReadIMul, ReadIMul, ReadIALU]>;

def XMSUB : RVInstR4_XMyMA<0b01, 0b000, OPC_CUSTOM_0,
                           (outs GPR:$rd),
                           (ins GPR:$rs1, GPR:$rs2, GPR:$rs3),
                           "xmsub", "$rd, $rs1, $rs2, $rs3">,
            Sched<[WriteIMul, ReadIMul, ReadIMul, ReadIALU]>;

def XNMADD : RVInstR4_XMyMA<0b10, 0b000, OPC_CUSTOM_0,
                            (outs GPR:$rd),
                            (ins GPR:$rs1, GPR:$rs2, GPR:$rs3),
                            "xnmadd", "$rd, $rs1, $rs2, $rs3">,
             Sched<[WriteIMul, ReadIMul, ReadIMul, ReadIALU]>;

} // Predicates

//===----------------------------------------------------------------------===//
// Codegen patterns
//===----------------------------------------------------------------------===//

let Predicates = [HasStdExtXMyMA] in {

// (rs1 * rs2) + rs3
def : Pat<(add (mul GPR:$rs1, GPR:$rs2), GPR:$rs3),
          (XMADD GPR:$rs1, GPR:$rs2, GPR:$rs3)>;

// rs3 + (rs1 * rs2) — commutative mul
def : Pat<(add GPR:$rs3, (mul GPR:$rs1, GPR:$rs2)),
          (XMADD GPR:$rs1, GPR:$rs2, GPR:$rs3)>;

// (rs1 * rs2) - rs3
def : Pat<(sub (mul GPR:$rs1, GPR:$rs2), GPR:$rs3),
          (XMSUB GPR:$rs1, GPR:$rs2, GPR:$rs3)>;

// rs3 - (rs1 * rs2) = -(rs1*rs2) + rs3
def : Pat<(sub GPR:$rs3, (mul GPR:$rs1, GPR:$rs2)),
          (XNMADD GPR:$rs1, GPR:$rs2, GPR:$rs3)>;

// Intrinsic patterns
def : Pat<(int_riscv_xmadd GPR:$rs1, GPR:$rs2, GPR:$rs3),
          (XMADD GPR:$rs1, GPR:$rs2, GPR:$rs3)>;
def : Pat<(int_riscv_xmsub GPR:$rs1, GPR:$rs2, GPR:$rs3),
          (XMSUB GPR:$rs1, GPR:$rs2, GPR:$rs3)>;
def : Pat<(int_riscv_xnmadd GPR:$rs1, GPR:$rs2, GPR:$rs3),
          (XNMADD GPR:$rs1, GPR:$rs2, GPR:$rs3)>;

} // Predicates
```

### Step 3: Include

編輯 `RISCV.td`：

```tablegen
include "RISCVInstrInfoXMyMA.td"
```

### Step 4: ISAInfo

編輯 `llvm/lib/Support/RISCVISAInfo.cpp`（或對應 `.def` 表），加 `"xmyma"` extension。實際檔案名依 LLVM 版本而定（可能是 `RISCVFeatures.def` 等），grep 類似 "zba" 的 entry 找到。

### Step 5: Intrinsic

編輯 `llvm/include/llvm/IR/IntrinsicsRISCV.td`：

```tablegen
let TargetPrefix = "riscv" in {
  def int_riscv_xmadd
      : Intrinsic<[llvm_anyint_ty],
                  [LLVMMatchType<0>, LLVMMatchType<0>, LLVMMatchType<0>],
                  [IntrNoMem, IntrSpeculatable, IntrWillReturn]>;

  def int_riscv_xmsub
      : Intrinsic<[llvm_anyint_ty], [...], [...]>;

  def int_riscv_xnmadd
      : Intrinsic<[llvm_anyint_ty], [...], [...]>;
}
```

### Step 6: Clang builtin

編輯 `clang/include/clang/Basic/BuiltinsRISCV.def`：

```
TARGET_BUILTIN(__builtin_riscv_xmadd, "iiii", "nc", "xmyma")
TARGET_BUILTIN(__builtin_riscv_xmsub, "iiii", "nc", "xmyma")
TARGET_BUILTIN(__builtin_riscv_xnmadd, "iiii", "nc", "xmyma")
```

在 `clang/lib/CodeGen/CGBuiltin.cpp` 加：

```cpp
case RISCV::BI__builtin_riscv_xmadd:
    ID = Intrinsic::riscv_xmadd;
    break;
case RISCV::BI__builtin_riscv_xmsub:
    ID = Intrinsic::riscv_xmsub;
    break;
case RISCV::BI__builtin_riscv_xnmadd:
    ID = Intrinsic::riscv_xnmadd;
    break;
```

### Step 7: Tests

```
// llvm/test/CodeGen/RISCV/xmyma.ll
; RUN: llc -mtriple=riscv64 -mattr=+xmyma < %s | FileCheck %s

define i32 @madd(i32 %a, i32 %b, i32 %c) {
; CHECK-LABEL: madd:
; CHECK:       xmadd a0, a0, a1, a2
  %mul = mul i32 %a, %b
  %res = add i32 %mul, %c
  ret i32 %res
}

define i32 @msub(i32 %a, i32 %b, i32 %c) {
; CHECK-LABEL: msub:
; CHECK:       xmsub a0, a0, a1, a2
  %mul = mul i32 %a, %b
  %res = sub i32 %mul, %c
  ret i32 %res
}

define i32 @nmadd(i32 %a, i32 %b, i32 %c) {
; CHECK-LABEL: nmadd:
; CHECK:       xnmadd a0, a0, a1, a2
  %mul = mul i32 %a, %b
  %res = sub i32 %c, %mul
  ret i32 %res
}

define i32 @intrinsic_madd(i32 %a, i32 %b, i32 %c) {
; CHECK-LABEL: intrinsic_madd:
; CHECK:       xmadd a0, a0, a1, a2
  %res = call i32 @llvm.riscv.xmadd.i32(i32 %a, i32 %b, i32 %c)
  ret i32 %res
}

declare i32 @llvm.riscv.xmadd.i32(i32, i32, i32)
```

MC test:

```
// llvm/test/MC/RISCV/xmyma-valid.s
# RUN: llvm-mc -triple=riscv64 -mattr=+xmyma -show-encoding < %s | FileCheck %s

# CHECK: xmadd x1, x2, x3, x4
# CHECK: encoding: [...]
xmadd x1, x2, x3, x4

# CHECK: xmsub
xmsub x1, x2, x3, x4

# CHECK: xnmadd
xnmadd x1, x2, x3, x4
```

### Step 8: Clang test

```
// clang/test/CodeGen/RISCV/xmyma.c
// RUN: %clang_cc1 -triple riscv64-unknown-elf -target-feature +xmyma -emit-llvm %s -o - | FileCheck %s

int test_madd(int a, int b, int c) {
// CHECK: call i32 @llvm.riscv.xmadd.i32
  return __builtin_riscv_xmadd(a, b, c);
}
```

### Step 9: Build + Test

```bash
cd build
ninja llc llvm-mc clang
./bin/llvm-lit -v ../llvm/test/CodeGen/RISCV/xmyma.ll
./bin/llvm-lit -v ../llvm/test/MC/RISCV/xmyma-valid.s
./bin/llvm-lit -v ../clang/test/CodeGen/RISCV/xmyma.c
```

全 pass → MVP 完成。

## Stretch: GCC port

對應 Ch 18，在 GCC 做同樣的：

```
gcc/config/riscv/riscv-ext.def     ; 加 xmyma entry
gcc/config/riscv/riscv.md           ; 或新 xmyma.md
gcc/config/riscv/riscv-builtins.cc ; intrinsic
gcc/testsuite/gcc.target/riscv/    ; tests
```

這個 Stretch 展示兩個 compiler 的 parallel support，**非常加分**。

## Stretch: Benchmark

寫一個 dot product：

```c
int dot(int *a, int *b, int n) {
    int sum = 0;
    for (int i = 0; i < n; i++) sum += a[i] * b[i];
    return sum;
}
```

編 with/without xmyma、對比 asm 指令數。

用 `perf`（或 cycle counter）量實際 runtime 差異。

寫進 README。

## Stretch: Upstream!

**MVP 完成後，真的 submit LLVM PR**。

- Fork 已有
- 做 clang-format
- 拆成 3-5 個小 PR（feature, MC, codegen, intrinsic, test）
- 開 PR 等 review

真的 merged → 你是 LLVM contributor。**這是履歷的 nuclear option**。

## README 範本

```markdown
# LLVM RISC-V XMyMA Extension

Adds support for XMyMA (My Multiply-Add) extension to LLVM RISC-V backend.

## Features
- XMADD: rd = rs1 * rs2 + rs3
- XMSUB: rd = rs1 * rs2 - rs3
- XNMADD: rd = -(rs1 * rs2) + rs3
- Clang `__builtin_riscv_xmadd/xmsub/xnmadd` intrinsics
- LLVM `@llvm.riscv.xmadd/xmsub/xnmadd`
- Assembler/Disassembler support
- FileCheck tests passing

## Build
```bash
cmake ... # standard LLVM
ninja llc llvm-mc clang
```

## Usage
```c
int result = __builtin_riscv_xmadd(a, b, c);  // or let compiler infer
```

```bash
clang -march=rv64g_xmyma test.c -o test
```

## Impact
[dot product benchmark result: e.g., 30% fewer instructions]

## What I learned
- TableGen for instruction definitions
- Intrinsic lowering (Clang → LLVM IR → MIR)
- MC layer encoding
- Scheduling model basics
- FileCheck testing methodology
```

## 評估標準

**60 分 (MVP)**：3 條指令 + pattern + test pass

**75 分**：加 intrinsic + Clang builtin

**85 分**：加 scheduling model + benchmark

**95 分**：GCC port 完成

**100 分**：上游 LLVM merge

## 時間預估

| 目標 | 時間 |
|------|------|
| MVP (60 分) | 1-2 週 |
| + intrinsic (75 分) | 2-3 週 |
| + sched + benchmark (85 分) | 3-4 週 |
| + GCC (95 分) | 1-2 月 |
| + upstream (100 分) | 2-6 月 |

**不要求每個 level 都做**。MVP 已經是強履歷。

## 面試 demo 流程

做完 project 後，面試當場：

1. Open laptop, terminal 到 project
2. `ls` 看檔案 + 結構
3. 顯示一個 C code 範例
4. `clang -march=... test.c -S` 印出 asm
5. diff with + without extension 的 asm
6. 開一個 `.td` 檔解釋你改了什麼
7. 問問題：對不對？有沒有 edge case 沒處理？

**5 分鐘內 showcase 整個專案**。面試官會印象深刻。

## 最後

到這裡，你從 Ch 0 (build LLVM) 走到 Final Project (add extension)。

從 `riscv` (ISA 基礎) 到 `elf_linking` (binary format) 到 `compiler_backend` (你現在這裡)，你有了 SiFive job spec 要求的全部核心能力。

剩下兩門課（`perf_bench` + `yocto`）是應用與配套。**你已經有 80% 的戰力**。

去做。

---

## 自我檢核

- [ ] 我有完整的 XMyMA project repo on GitHub
- [ ] 所有 test pass
- [ ] README 清楚
- [ ] 能 live demo 給面試官看
- [ ] (Stretch) 有 benchmark 結果
- [ ] (Stretch) 有 GCC 對應版本
- [ ] (Stretch) 有 LLVM upstream PR

完成任一個 stretch goal = 你是 RISC-V compiler 領域的專業人士。
