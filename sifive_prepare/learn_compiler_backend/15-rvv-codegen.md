# Ch 15 — RVV codegen 與 VSETVLI 放置

> 目標：理解 LLVM 如何產生 RVV 向量 code、VSETVLI 指令什麼時候插、如何合併。這是 SiFive 向量 compiler 工程師的日常戰場。

## RVV 對 compiler 的挑戰

RVV 的 VLA (vector-length agnostic) 模型對 compiler 帶來幾個獨特挑戰：

### 1. Vector 長度 runtime 決定

Fixed-length SIMD（SSE/NEON）compile 時知道長度：4 個 int、8 個 short 等。RVV 不知道。

compiler 要產「對任何 VLEN 都對」的 code。

### 2. vsetvl 管理

每段 vector code 前要設 SEW / LMUL / vl。這些透過 `vsetvli` 指令設定。但：

- 每條 vsetvli ~30 cycle (pipeline flush)
- 放太多 → 慢
- 放太少 → 錯的 config
- 跨 basic block 的 config 傳遞要注意

### 3. LMUL 跟 register grouping

LMUL 決定一條 vector 指令涉及幾個 register。LMUL=8 → v0..v7 算一組。register allocation 要處理 group。

### 4. Mask 處理

每條 vector 指令可帶 mask。mask 是 v0 register。special handling。

### 5. vscale 型別系統

LLVM IR 用 `<vscale x N x T>` 描述 scalable vector。整套 type system 要懂 vscale。

## LLVM IR 的 scalable vector

```llvm
%vec = alloca <vscale x 4 x i32>         ; scalable vector of 4-groups of i32
%v2 = add <vscale x 4 x i32> %a, %b       ; vector add
```

`<vscale x 4 x i32>` 表示 "vscale 組的 4 個 i32"。VLEN / 32 / 4 = 8/VLEN=256, 16/VLEN=512... LMUL=1 對應。

LMUL=2 → `<vscale x 8 x i32>` (8 個 element)
LMUL=4 → `<vscale x 16 x i32>`
LMUL=8 → `<vscale x 32 x i32>`

**這是 LLVM 特有的抽象**。Clang vector builtin 產生這些 type。

## RVV intrinsic 的 IR

```c
vint32m1_t vsum(vint32m1_t a, vint32m1_t b, size_t vl) {
    return __riscv_vadd_vv_i32m1(a, b, vl);
}
```

產 IR：

```llvm
define <vscale x 2 x i32> @vsum(<vscale x 2 x i32> %a, <vscale x 2 x i32> %b, i64 %vl) {
    %ret = call <vscale x 2 x i32> @llvm.riscv.vadd.nxv2i32(
        <vscale x 2 x i32> poison,
        <vscale x 2 x i32> %a,
        <vscale x 2 x i32> %b,
        i64 %vl,
        i64 1          ; vxrm (rounding mode)
    )
    ret <vscale x 2 x i32> %ret
}
```

注意：

- `<vscale x 2 x i32>`：i32m1 在 RV64 的對應（VLEN=128 時 m1 就是 4 個 i32、vscale=2、2*2=4）
- intrinsic 第一個 arg 是 `poison`（或 merge value），第二/三個是 source，然後 vl + rounding mode

## 從 IR 到 MIR

經過 SelectionDAG：

```
IR: @llvm.riscv.vadd.nxv2i32
  ↓ Pattern
DAG: (riscv_vadd_vv ...)
  ↓ ISel
MIR: PseudoVADD_VV_M1 %dst, %src1, %src2, vl, sew
```

`PseudoVADD_VV_M1` 是 Pseudo 指令 (`Pseudo` 因為要 vsetvli 插入 pass 處理)。

接下來 `RISCVInsertVSETVLI` pass 插入 vsetvli 並把 Pseudo 展成真的 VADD。

## RISCVInsertVSETVLI：vsetvli 放置 pass

**`llvm/lib/Target/RISCV/RISCVInsertVSETVLI.cpp`** 是 RVV 的靈魂 pass。幾千行、核心邏輯：

### 目標

每條 vector instruction 需要知道 `vtype` (SEW + LMUL + tail/mask policy) 跟 `vl`。PseudoVADD_VV_M1 等 MI 的 operand 裡有這資訊。vsetvli 指令把它告訴硬體。

### 演算法

- 對每個 BB 跑 dataflow 分析：track "current vtype state" 在每個 point 是什麼
- 如果 next instruction 需要的 vtype 跟 current 不同 → 插入 `vsetvli`
- 如果 current 已經匹配 → 不插

目標：**插最少的 vsetvli**。

### 跨 BB 的處理

如果 BB1 結束時 vtype = X、BB2 開始時需要 Y、X≠Y → BB2 開頭要插 vsetvli。

但如果兩個 predecessor 都是 X → BB2 不用插。

這是 meet-over-paths dataflow。

### 實例

```mir
bb.0:
  ; 假設前面沒 set vtype
  %1 = PseudoVADD_VV_M1 %a, %b, vl=%vl, sew=32
  %2 = PseudoVADD_VV_M1 %1, %c, vl=%vl, sew=32
  ; 兩條都 SEW=32, LMUL=m1, vl=%vl
  PseudoRET
```

Insert vsetvli pass 跑完：

```mir
bb.0:
  vsetvli %vl, %vl, e32, m1, ta, ma   ; 只插一次
  %1 = VADD_VV_M1 %a, %b
  %2 = VADD_VV_M1 %1, %c               ; reuse vtype
  PseudoRET
```

**兩條 vector 指令共用一次 vsetvli**。

## Debug VSETVLI insertion

```bash
llc -march=riscv64 -mattr=+v -print-before=riscv-insert-vsetvli -print-after=riscv-insert-vsetvli hello.ll 2>&1 | less
```

看 pass 執行前後 MIR，對比 vsetvli 的位置。

## VSETVLI 相關優化

### 1. VSETVLI 合併

連續多條相同 vtype 的 vector op，只插一次 vsetvli。

### 2. Tail/mask policy 優化

`ta`/`tu`、`ma`/`mu` 可以讓硬體有優化空間。compiler 盡量用 agnostic (`ta,ma`)。

### 3. AVL (Application Vector Length) 優化

```c
for (; n > 0; n -= vl) {
    vl = __riscv_vsetvl_e32m1(n);
    ...
}
```

compiler 辨認這個 pattern，**不重設 vsetvli**（因為 vl 自己會更新）。

### 4. Cross-BB VSETVLI elision

function 入口如果 caller 已經設好 vtype (某種 convention) → 省入口的 vsetvli。RVV ABI 還在討論這個。

## Auto-vectorization 跟 RVV

LLVM 的 LoopVectorize pass 產生 scalable vector code：

```c
for (int i = 0; i < N; i++) c[i] = a[i] + b[i];
```

`-O3 -march=rv64gc_v`：

```asm
loop:
    vsetvli t0, a3, e32, m1, ta, ma
    vle32.v v0, (a1)
    vle32.v v1, (a2)
    vadd.vv v0, v0, v1
    vse32.v v0, (a0)
    sub     a3, a3, t0
    slli    t1, t0, 2
    add     a1, a1, t1
    add     a2, a2, t1
    add     a0, a0, t1
    bnez    a3, loop
```

loop + vl adaptive = 標準 RVV idiom。

### 啟用 auto-vectorization

```bash
clang --target=riscv64-linux-gnu -march=rv64gcv -O3 -ffast-math ...
```

`-march=rv64gcv` 的 `v` 啟用 V extension。某些情況需要 `-ffast-math` 才能向量化（浮點 reordering）。

### 限制

- Pointer alias 問題 → 用 `__restrict`
- Data dependency → compiler 保守
- 非 counted loop (exit condition 複雜) → 不向量化

看 remark：

```bash
clang -O3 -Rpass=loop-vectorize ... | grep remark
```

告訴你 vectorization 失敗原因。

## Vector intrinsic 的 spec

RVV intrinsic 由 RISC-V International 管：

- **Spec repo**: <https://github.com/riscv-non-isa/rvv-intrinsic-doc>
- **v1.0 ratified**: 2024

每個 op × SEW × LMUL × masked/unmasked → 獨立 intrinsic。總共幾千個。

## Fractional LMUL

LMUL 可以 `1/2`, `1/4`, `1/8`：

```c
vint16mf4_t = vint16 with LMUL = 1/4
```

為什麼：widening 操作。

```
vwmul.vv vd, vs1, vs2     ; i16 × i16 = i32, LMUL × 2
```

如果輸入 LMUL=1，輸出 LMUL=2。為了輸入不爆 register，輸入可用 fractional LMUL = 1/2。

fractional LMUL 的 codegen 是 RVV 最難的部分之一。

## VSETVLI 的 AVL argument

vsetvli 的 `rs1` 是 application vector length (AVL)：

```
vsetvli rd, rs1, e32, m1, ta, ma
                │
              AVL
```

硬體算 `vl = min(AVL, VLMAX)`、寫 `rd` 回報。

compiler 產生 code：

```
; AVL = 待處理 element 數
; vl = 本次實際處理
vsetvli t0, a3, e32, m1
; t0 = vl (≤ a3)
...
sub a3, a3, t0
bnez a3, loop
```

## VL 的 liveness

vl 是 CSR，算「implicit value」。但 LLVM 把它視為 value 做 liveness 分析：

- 某條指令 def vl (vsetvli)
- 某條指令 use vl (vector op)
- 跨 call vl 通常 clobbered

**這讓 vl 的處理跟普通 virtual reg 類似**。

## 面試可能的問題

SiFive 面試 RVV 相關：

1. **「解釋 VSETVLI 的 overhead 跟如何減少」**
2. **「LoopVectorize 產生的 RVV code 為什麼是 strip-mining 形式」**
3. **「fractional LMUL 何時用」**
4. **「debug 一個 vectorize failure 的流程」**

準備好這些，你在 SiFive RVV 組不會差。

## 讀 RISCVInsertVSETVLI.cpp

結構概覽（真實大約 3000 行）：

```
class VSETVLIInfo {
    // 表示一個 vtype state
    SEW, LMUL, TailAgnostic, MaskAgnostic, AVL
};

class RISCVInsertVSETVLI {
    // Forward dataflow: track vtype state at each BB boundary
    // Meet operation: 算 PHI-style predecessor vtype
    // 對每個 pseudo instruction: 若 current vtype 不匹配 → insert vsetvli
};
```

建議讀法：

1. `runOnMachineFunction` 的總體流程
2. `needVSETVLI()` 判斷
3. `insertVSETVLI()` 實際插入
4. Meet operation 在 `computeVLVTYPEChanges()`

大約讀 500 行能 grasp 80%。

## 常見 RVV codegen 問題

1. **「我的 vector loop 慢於預期」**：check VSETVLI 數量。過多 → scheduling 問題。
2. **「vector compile 時 LLVM segfault」**：check LLVM version。RVV 生態仍有很多 edge case。
3. **「某 intrinsic 沒被 select」**：check pattern、check extension flag (`-mattr=+v`)。
4. **「fractional LMUL 產生的 code 奇怪」**：RA 可能對 fractional 處理 suboptimal。report upstream 或手調。
5. **「auto-vectorize fail on simple loop」**：aliasing / data dep / non-counted loop。用 `__restrict` + check 條件。

## 動手練習

1. 寫個 vector add loop，`-O3` 看 compiler 產生 RVV asm，數 VSETVLI 次數。
2. 用 intrinsic 版 vs auto-vectorize 版寫同樣 algorithm，對比 perf。
3. 讀 `RISCVInsertVSETVLI.cpp` 的 `runOnMachineFunction`，認出它的 forward dataflow 結構。
4. 試寫 fractional LMUL 的 intrinsic code、看 compiler 處理。
5. 用 `-Rpass-missed=loop-vectorize` 看 fail 原因，修 code 讓它能 vectorize。

## 自我檢核

- [ ] 我能解釋 scalable vector 型別在 LLVM IR 的意義
- [ ] 我知道 vsetvli 的 cost 跟 elision 策略
- [ ] 我能找到 `RISCVInsertVSETVLI.cpp` 並描述它的 dataflow
- [ ] 我能寫 RVV intrinsic + 驗證 codegen
- [ ] 我能 debug auto-vectorization 失敗

下一章處理 inline assembly —— compile 跟 asm 的最後介面。

→ [Ch 16 Inline assembly 與 constraint](./16-inline-assembly.md)
