# Ch 14 — Intrinsic → codegen 全流程

> 目標：從 C 的 `__builtin_riscv_*` 到 LLVM IR 的 `@llvm.riscv.*` 到最終 assembly，走完 intrinsic 的整個處理鏈。能為新 custom instruction 加 intrinsic support。

## 什麼是 intrinsic

**Intrinsic**：C 層的「偽函式」，compiler 會把它直接編譯成特定指令（或指令序列）。不是真的 library function，不是 macro。

C 寫：

```c
int x = __builtin_riscv_cpop(n);    // count population (popcount)
```

Compiler 直接生：

```asm
cpop  a0, a0
```

沒有 function call、沒有 stack frame。intrinsic 是「名字像 function、行為像 instruction」。

## 為什麼要 intrinsic

- **提供 C 語法呼叫 ISA 特殊指令**：不用寫 inline asm
- **型別安全**：compiler 知道 arg / return type
- **優化 friendly**：compiler 懂它的語意、可以 reorder / CSE / DCE
- **跨 compiler 相容**：GCC 跟 Clang 用同樣命名（多數情況）

intrinsic 是「受管理的 asm」：安全、可優化、可讀。

## RISC-V intrinsic 的三層架構

```
C 層:      __builtin_riscv_cpop(x)
            │ Clang AST + CodeGen
            ▼
IR 層:     call i32 @llvm.riscv.cpop.i32(i32 %x)
            │ SelectionDAG Builder
            ▼
DAG 層:    (riscv_cpop GPR:$rs1)
            │ Pattern match
            ▼
MIR 層:    CPOP $x10, $x10
```

每層都有對應的 source 檔：

- Clang builtin: `clang/include/clang/Basic/BuiltinsRISCV*.def`, `clang/lib/CodeGen/CGBuiltin.cpp`
- LLVM intrinsic: `llvm/include/llvm/IR/IntrinsicsRISCV.td`
- LLVM pattern: `RISCVInstrInfoXxx.td`

## Step 1: Clang Builtin 宣告

```cpp
// clang/include/clang/Basic/BuiltinsRISCV.def
TARGET_BUILTIN(__builtin_riscv_cpop_32, "UiUi", "nc", "zbb")
```

拆解：

- `__builtin_riscv_cpop_32`: builtin 名字
- `"UiUi"`: 型別 encoding（return = unsigned int、arg = unsigned int）
- `"nc"`: attributes (n = no side-effect, c = const)
- `"zbb"`: 需要的 target feature

Clang CodeGen 在 `CGBuiltin.cpp` 處理這個 builtin：

```cpp
case RISCV::BI__builtin_riscv_cpop_32:
    ID = Intrinsic::riscv_cpop;
    break;
```

生成對應的 LLVM intrinsic call。

## Step 2: LLVM intrinsic 宣告

```tablegen
// llvm/include/llvm/IR/IntrinsicsRISCV.td

let TargetPrefix = "riscv" in {
    def int_riscv_cpop
        : Intrinsic<[llvm_anyint_ty],            // return types
                    [LLVMMatchType<0>],          // arg types
                    [IntrNoMem, IntrSpeculatable, IntrWillReturn]>;
}
```

拆解：

- `int_riscv_cpop`: TableGen 裡叫這個，C++ 存取叫 `Intrinsic::riscv_cpop`
- `[llvm_anyint_ty]`: return 是任意 integer type（i32 或 i64）
- `[LLVMMatchType<0>]`: arg 是跟 return 相同 type
- `IntrNoMem`: 不碰 memory
- `IntrSpeculatable`: 可 speculatively 執行
- `IntrWillReturn`: 一定 return（不 infinite loop）

這些 attribute 讓 LLVM optimizer 知道 intrinsic 能 hoist / CSE。

## Step 3: LLVM codegen pattern

```tablegen
// RISCVInstrInfoZbb.td

def : Pat<(int_riscv_cpop GPR:$rs1),
          (CPOP GPR:$rs1)>;

// 對 i64:
def : Pat<(i64 (int_riscv_cpop GPR:$rs1)),
          (CPOP GPR:$rs1)>;
```

Pattern 把 `int_riscv_cpop` DAG node 映射到 `CPOP` 指令。

## 第三層更普遍：用 SDNode + PatFrag

有些 intrinsic 要定義 target-specific ISD node：

```tablegen
def riscv_vslideup :
    SDNode<"RISCVISD::VSLIDEUP", ...>;

def : Pat<(riscv_vslideup ...), (VSLIDEUP_VX ...)>;
```

這讓 DAGCombiner 能用這個 node 做優化（e.g., merge consecutive slides）。

## 測試 intrinsic

C 層：

```c
// test.c
unsigned popcount(unsigned x) {
    return __builtin_riscv_cpop_32(x);
}
```

```bash
clang --target=riscv64-unknown-elf -march=rv64gc_zbb -O2 -S test.c
cat test.s
```

期望：

```asm
popcount:
    cpop  a0, a0
    ret
```

IR 層（看 `-emit-llvm`）：

```bash
clang ... -emit-llvm -S test.c -o test.ll
```

```llvm
define i32 @popcount(i32 %x) {
  %r = call i32 @llvm.riscv.cpop.i32(i32 %x)
  ret i32 %r
}
```

## GNU 對應：`__builtin_riscv_*` 也在 GCC

GCC 跟 Clang 通常**共用同名 builtin**，這是 convention。這樣寫 portable code：

```c
#if defined(__riscv) && defined(__riscv_zbb)
    y = __builtin_riscv_cpop_32(x);
#else
    y = __popcount_sw(x);    // software fallback
#endif
```

兩個 compiler 都認。

## 加一個新 intrinsic：step-by-step

續 Ch 9 的 `XMADD`，要加 `__builtin_riscv_xmadd` intrinsic。

### Clang side

```cpp
// clang/include/clang/Basic/BuiltinsRISCV.def
TARGET_BUILTIN(__builtin_riscv_xmadd, "iiii", "nc", "xmyext")
```

`"iiii"`: return int, 3 × int args.

```cpp
// clang/lib/CodeGen/CGBuiltin.cpp (在 RISCV section)
case RISCV::BI__builtin_riscv_xmadd:
    ID = Intrinsic::riscv_xmadd;
    break;
```

### LLVM side

```tablegen
// llvm/include/llvm/IR/IntrinsicsRISCV.td
let TargetPrefix = "riscv" in {
    def int_riscv_xmadd
        : Intrinsic<[llvm_i32_ty],
                    [llvm_i32_ty, llvm_i32_ty, llvm_i32_ty],
                    [IntrNoMem, IntrSpeculatable]>;
}
```

### Pattern

```tablegen
// RISCVInstrInfoXMyExt.td
def : Pat<(int_riscv_xmadd GPR:$rs1, GPR:$rs2, GPR:$rs3),
          (XMADD GPR:$rs1, GPR:$rs2, GPR:$rs3)>;
```

### Test

```c
int test(int a, int b, int c) {
    return __builtin_riscv_xmadd(a, b, c);
}
```

```bash
clang --target=riscv64 -march=rv64gc_xmyext -O2 -S test.c -o test.s
# 期望: test: xmadd a0, a0, a1, a2; ret
```

## Intrinsic 的 header：`<riscv_intrinsic.h>` 跟 `<riscv_vector.h>`

標準 RISC-V intrinsic（V extension 等）有 standard header：

```c
#include <riscv_vector.h>

vint32m1_t foo(vint32m1_t a, vint32m1_t b, size_t vl) {
    return __riscv_vadd_vv_i32m1(a, b, vl);
}
```

這些 header 提供 C-style API，內部呼叫 `__builtin_riscv_*`。spec 在 <https://github.com/riscv-non-isa/rvv-intrinsic-doc>。

Ch 15 會深入 RVV intrinsic。

## Intrinsic 的 attribute 深入

LLVM intrinsic attribute 影響 optimizer 行為：

```
IntrNoMem          不讀/寫 memory
IntrReadMem        只讀 memory
IntrWriteMem       只寫 memory
IntrReadWriteMem   讀寫 memory (default)

IntrSpeculatable   可以 speculatively 執行 (不會 trap 或 side effect)
IntrWillReturn     保證 return (不 infinite loop)
IntrHasSideEffects 有 side effect (不能 DCE)
IntrNoReturn       不 return
IntrNoDuplicate    不能複製 (e.g., barrier)

IntrConvergent     某些 lane 必須一起執行 (GPU)
```

**設錯會造成 miscompile**。例：`amoadd` 有 memory side effect、如果標 `IntrNoMem` 會被 optimizer 錯誤 DCE。

## Intrinsic lowering

Intrinsic 進入 backend 時：

```
LLVM IR:
  call i32 @llvm.riscv.cpop.i32(i32 %x)
    ↓ SelectionDAGBuilder::visitIntrinsicCall
DAG:
  (riscv_cpop %x) → Pattern match → (CPOP %x)
```

SelectionDAG 對 intrinsic 的 handling 在 `SelectionDAGBuilder::visitIntrinsicCall()`。

如果 pattern 沒 match、LLVM 會 error：

```
fatal error: ... llvm.riscv.xxx: unimplemented
```

這時需要檢查：pattern 寫了嗎？predicate（feature check）對嗎？

## Vector intrinsic 的複雜

RVV intrinsic 特別繁：

- 每個 op × SEW × LMUL → 一個 intrinsic
- vadd 就有 `vv`, `vx`, `vi` 三個 variant × 四個 SEW × 七個 LMUL = ~80 個

LLVM 用 TableGen 生成：

```tablegen
// IntrinsicsRISCVXTHeadV.td (simplified)
class RISCVBinaryAAXUnMasked
    : Intrinsic<[llvm_anyvector_ty],
                [LLVMMatchType<0>, LLVMMatchType<0>, ...],
                [IntrNoMem]>;

defm int_riscv_vadd : RISCVBinaryAAX;
```

展開後每個 SEW/LMUL 都有自己的 intrinsic。Ch 15 細講。

## 常見誤會

1. **「Intrinsic 就是 inline asm」**：不。Intrinsic 是 compiler 認識的偽函式，有 semantic；inline asm 是 opaque byte string。
2. **「加 intrinsic 只改 LLVM」**：不夠。Clang 那邊要對應 builtin，兩邊搭配。
3. **「intrinsic 永遠沒 side effect」**：看 attribute。`IntrReadWriteMem` 的有 side effect。
4. **「我可以自訂 intrinsic name」**：受限。`@llvm.riscv.*` 是 reserved prefix，新 intrinsic 要 upstream 同意或 prefix `@llvm.MyVendor.*`。
5. **「intrinsic 會 inline」**：比 inline 更強 —— 根本不存在 function call，直接是指令。

## 動手練習

1. 找 `IntrinsicsRISCV.td`，列出 Zbb 相關所有 intrinsic。
2. 讀 `clang/lib/CodeGen/CGBuiltin.cpp` 的 `EmitRISCVBuiltinExpr()`，看它怎麼 dispatch builtin。
3. 寫個 C 程式用 `__builtin_riscv_cpop_32`，跟用 `__builtin_popcount` 比較 asm。
4. 加一個 dummy intrinsic（e.g., `__builtin_riscv_identity(x) = x`），從 Clang 到 Pattern 全鏈。
5. 讀 `riscv_vector.h` 的幾行，認出它的 intrinsic wrapper 長什麼樣。

## 自我檢核

- [ ] 我能描述 intrinsic 從 C 到 asm 的完整 flow
- [ ] 我知道 Clang builtin / LLVM intrinsic / TableGen pattern 三層的對應
- [ ] 我能加一個新 intrinsic 並 test
- [ ] 我知道 intrinsic attribute 的語意（IntrNoMem 等）
- [ ] 我能解釋 intrinsic 跟 inline asm 的差別

下一章進 RVV codegen —— vector 是 RISC-V 最複雜的 codegen 領域。

→ [Ch 15 RVV codegen 與 VSETVLI 放置](./15-rvv-codegen.md)
