# Ch 5 — Legalization：type + DAG

> 目標：理解 SelectionDAG 為什麼要分 type legalization 跟 DAG legalization 兩階段、每階段處理什麼、以及 target 如何控制「哪些 operation 合法、哪些要 custom lower」。

## 為什麼需要 legalization

IR 是 target-neutral：`i128` / `i1` / `<4 x i32>` / `fp128` 全部合法。

但 RISC-V 硬體不支援所有型別跟 operation。例：

- `i128`：沒 native，要用 multi-precision
- `i1`：用 `i32` 表示（1 bit 在 reg file 要升到 reg 寬）
- `<4 x i32>` fixed vector：RISC-V 沒 SSE 式固定 SIMD、要轉 scalar 或 RVV
- `fp128`：沒硬體、要 lib call

**Legalization**：把 "illegal type" / "illegal operation" 轉成 target 能處理的形式。

兩階段：

1. **Type legalization**：處理型別（i128 → i64 + i64 pair）
2. **DAG legalization**：處理 operation（`ISD::CTTZ` → software sequence）

## 第一階段：Type Legalization

### 三種策略

對不合法的 type：

**1. Promote**：升到更大的合法 type。

```
i1  → i32 (or i8, see below)     ; bool 變 word
i8  → i32                         ; (如果 i8 不合法)
half → float
```

**2. Expand**：拆成多個合法 type。

```
i128 → (i64, i64)       ; high + low half
i64  → (i32, i32)       ; RV32 上
<8 x i32> → (<4 x i32>, <4 x i32>)
```

**3. Soften**：浮點無硬體時，變 integer 模擬 + lib call。

```
float (如果無 F extension) → i32 + libcall (__addsf3)
```

### 如何設定

每個 target 在 `XxxISelLowering.cpp` constructor 裡設 type action：

```cpp
// RISCVISelLowering.cpp (簡化)
RISCVTargetLowering::RISCVTargetLowering(...) {
    // RV64 上 i32 不是最佳，但合法
    addRegisterClass(MVT::i32, &RISCV::GPRRegClass);
    addRegisterClass(MVT::i64, &RISCV::GPRRegClass);

    // i128 → expand 成 (i64, i64)
    setOperationAction(ISD::ADD, MVT::i128, Expand);
    setOperationAction(ISD::MUL, MVT::i128, Expand);
    // ...

    // FP types
    if (Subtarget.hasStdExtF())
        addRegisterClass(MVT::f32, &RISCV::FPR32RegClass);
    else
        // soften f32
        setOperationAction(ISD::FADD, MVT::f32, LibCall);
    ...
}
```

**`addRegisterClass` 宣告 「這個 type 合法，放這個 register class」**。`setOperationAction` 設定某個 operation 對某個 type 的處理策略。

### 策略細分

```cpp
setOperationAction(Op, VT, Action);

Action 可以是：
  Legal          // 直接支援（TableGen 有 pattern）
  Promote        // 升到更大 type
  Expand         // 拆成更小 type 或 software sequence
  Custom         // 呼叫 LowerXxx 自訂 lower
  LibCall        // 產 libcall (__muldi3 等)
```

**Custom 是 target 的自由手**。例：RISC-V 對 `ISD::DYNAMIC_STACKALLOC` 用 Custom，lower 成跟 frame pointer 互動的 code。

## 第二階段：DAG Legalization

type 都合法後，處理 operation。

某些 operation generic ISD 有、但 RISC-V 沒 native：

```
ISD::CTTZ   (count trailing zeros)    → 沒 Zbb 要 software
ISD::BSWAP  (byte swap)               → 沒 Zbb 要 shift+or 組
ISD::ROTL / ROTR                      → 沒 Zbb 要 shift+or 組
ISD::FCEIL / FFLOOR                   → 某些 FP 要 lib call
```

DAG legalization 把它們轉成合法序列。

### Custom lower 範例：CTTZ on non-Zbb

```cpp
// RISCVISelLowering.cpp
setOperationAction(ISD::CTTZ, XLenVT, Custom);

SDValue RISCVTargetLowering::lowerCTTZ(SDValue Op, SelectionDAG &DAG) const {
    // Lower cttz(x) to 32 - clz(x & -x) or similar
    // (or to libcall)
    ...
}
```

Lower 成 RISC-V 能處理的 DAG node 序列。

## Legalization 的 DAG combining

每階段 legalization 之間跑 DAGCombiner。例：

```
Initial DAG
 ↓ DAG combine (round 1)
 ↓ Type legalization
 ↓ DAG combine (round 2)
 ↓ Op legalization (DAG legalization)
 ↓ DAG combine (round 3)
```

**為什麼來回**：

- legalization 可能產生新 pattern、DAGCombiner 可以再優化
- DAGCombiner 可能產生 illegal pattern、下一輪 legalize 再處理

這種「multi-round converge」是 LLVM 多個 pipeline 的通用設計。

## TargetLowering::isOperationLegal

Legalization 的核心查詢：

```cpp
if (TLI.isOperationLegal(ISD::CTTZ, MVT::i64))
    // 直接 select
else
    // legalize
```

`TLI`（Target Lowering Info）知道每個 op × type 組合的 action。

## Target 的 type system：MVT

SelectionDAG 用 **MVT (Machine Value Type)** 描述 type：

```cpp
MVT::i1   MVT::i8   MVT::i16   MVT::i32   MVT::i64   MVT::i128
MVT::f16  MVT::f32  MVT::f64   MVT::f128
MVT::v4i32  MVT::v8i16         ; fixed-length vector
MVT::nxv4i32  MVT::nxv2i64     ; scalable vector (RVV)
MVT::Other   MVT::Glue           ; internal
```

`MVT::nxv4i32` 是 RVV 的 "scalable vector of 4-element groups of i32"。runtime 長度 `vscale × 4`。

## Soften FP：沒硬體時怎辦

如果 target 沒 F/D extension：

```cpp
if (!Subtarget.hasStdExtF()) {
    for (auto Op : {ISD::FADD, ISD::FSUB, ISD::FMUL, ISD::FDIV, ...}) {
        setOperationAction(Op, MVT::f32, LibCall);
    }
    setLibcallName(RTLIB::ADD_F32, "__addsf3");
    setLibcallName(RTLIB::SUB_F32, "__subsf3");
    ...
}
```

Legalization 看到 `fadd f32`、發現 LibCall → 產 `call __addsf3` 的 DAG node。

對應的 runtime 實作在 **compiler-rt** 或 **libgcc**。`.so` 連進來。

## Vector legalization：複雜最多

Vector type 的 legalization 特別麻煩：

```
<16 x i8>  (固定 16 byte)  →  分 <8 x i8> × 2 或 promote 到 <16 x i16>？
<1 x i64>                    → scalarize 成 i64？
```

每個 target 有自己規則：

- x86 偏好 128-bit SIMD → fixed vector 直接用
- ARM NEON 類似
- RISC-V RVV 走 scalable vector → `<4 x i32>` 會被 promote 成 `<vscale x 4 x i32>` 或 scalarize

RISC-V backend 對 fixed vector 的支援要看 target 有沒有 V extension + 實作者怎麼 map。

## 看 legalization 行動

```bash
llc -debug-only=isel hello.ll 2>&1 | grep -A2 "Legalized"
```

找 `Type-legalized` 跟 `Legalized selection DAG` 兩塊對比。你會看到 i128 操作被拆成多條 i64。

## 一個具體例子：RV32 的 i64 操作

RV32 沒 64-bit register，`i64 + i64` 怎麼做？

Legalization 走 Expand：

```
i64 ADD (a, b)
  ↓ expand
tuple<i32, i32> = (lo_a + lo_b, hi_a + hi_b + carry)
```

實際上對應 RISC-V 的：

```asm
add   lo_result, lo_a, lo_b
sltu  carry, lo_result, lo_a      ; detect overflow
add   hi_result, hi_a, hi_b
add   hi_result, hi_result, carry
```

Legalization 把 i64 ADD 拆成兩條 i32 ADD + 一個 carry。這是 RV32 `i64 +` 的真實展開。

## Legalization 跟 register class 的關係

Register class 是 target 定義的「哪些 type 進哪個 reg」：

```
GPR    = 32 個 x0..x31 integer reg, type = i32 (RV32) or i64 (RV64)
FPR32  = 32 個 f0..f31 (f32)
FPR64  = 32 個 f0..f31 (f64)
VR     = 32 個 v0..v31 (vector)
```

`addRegisterClass(MVT::i32, &RISCV::GPRRegClass)` 宣告「i32 放 GPR」。

Legalization 必須確保所有存活到 instruction selection 的 type 都有 register class。

## Target-specific lowering 代碼位置

```
llvm/lib/Target/RISCV/RISCVISelLowering.cpp
  - RISCVTargetLowering constructor     (type actions)
  - LowerOperation                       (custom lower 入口)
  - LowerCTTZ / LowerBSWAP / ...         (每個 custom op 的實作)
  - PerformDAGCombine                     (target DAGCombiner)
```

讀這個檔大約 5000 行。**RISC-V backend 最大的一個 file**。

## 常見誤會

1. **「Type legalization 只是 type 變更」**：不只。會產生新 DAG node，可能大幅增加 node 數（i128 → 4 個 i64 node pair）。
2. **「Custom 總是比 Expand 好」**：不。Custom 是額外手寫 code、容易 bug。Expand 是 legalization framework 自動處理。能 Expand 就 Expand。
3. **「Legalization 是 target-agnostic」**：legalization framework 是 generic 的，但「什麼合法」是 target-specific。
4. **「沒 F extension 也能寫 `float`」**：對，但效能慘。soften FP 變 libcall。
5. **「Legalization 後 DAG 都是 target instruction」**：還沒。DAG 仍用 ISD:: node（target-neutral），只是保證 legal。instruction selection 才把 ISD → MachineInstr。

## 動手練習

1. 寫一個用 `long long` 的 RV32 C program，用 `llc -march=riscv32 -debug-only=isel` 看 i64 如何 expand。
2. 寫一個沒 F extension 的 build（`-march=rv64im`），用 `float` 計算，看 libcall 產生。
3. 讀 `RISCVISelLowering.cpp` 的 constructor 前 100 行，列出 setOperationAction 的前 10 個。
4. 用 `__builtin_ctz` (CTTZ) 在沒 Zbb 的 build 編，看 lowering 成什麼 sequence。
5. 在 `RISCVISelLowering.cpp` 找 `lowerCTTZ` 或類似 function，讀實作。

## 自我檢核

- [ ] 我能解釋 type legalization 跟 DAG legalization 的分工
- [ ] 我知道 Promote / Expand / Custom / LibCall 四種 action
- [ ] 我能預測 `i128 +` 在 RV64 會 expand 成什麼
- [ ] 我能找到 RISC-V 的 `setOperationAction` 宣告
- [ ] 我知道 `addRegisterClass` 的角色

Part 2 第一輪完成。下一章進入 instruction selection —— 把 legalized DAG 轉成 target 的 MachineInstr，TableGen 的主戰場。

→ [Ch 6 Instruction selection：pattern matching](./06-instruction-selection.md)
