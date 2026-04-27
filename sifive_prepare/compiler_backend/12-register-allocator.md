# Ch 12 — Register allocator：greedy / fast

> 目標：理解 LLVM 的 register allocator 做什麼、為什麼有兩種（greedy 跟 fast）、spill / coalescing 的意義。讀完你能解釋「為什麼 hot loop 突然多了 load/store」這類 RA 相關效能問題。

## Register allocation 是什麼

MIR 在 RA 前用 **virtual register**（`%0`, `%1`, ...），無限多個。MIR 在 RA 後必須用 **physical register**（`$x10`, `$x11`, ...），RISC-V 只有 32 個。

**RA (Register Allocation)**：決定每個 virtual reg 映射到哪個 physical reg。當 live virtual reg > physical reg → **spill** 到 stack。

## 一個簡單例子

Before RA（只有 virtual reg）：

```
%0 = COPY $x10     ; 讀 arg0
%1 = COPY $x11     ; 讀 arg1
%2 = ADD %0, %1
%3 = SUB %0, %1
%4 = MUL %2, %3
$x10 = COPY %4     ; return
RET $x10
```

RA 後：

```
$x10 = COPY $x10   ; useless (coalesced)
$x11 = COPY $x11
$x12 = ADD $x10, $x11
$x13 = SUB $x10, $x11
$x10 = MUL $x12, $x13
RET $x10
```

RA 讓每個 virtual reg 綁到 physical reg。一連串優化（coalescing）去掉冗餘 copy。

## LLVM 的兩個 RA

### fast (RegAllocFast)

- **O(N) 線性時間**
- 不做 interference graph
- **僅用於 -O0**
- 每個 BB 獨立處理，quality 差但快

### greedy (RegAllocGreedy)

- **Priority queue + backtracking**
- Build live interval graph
- **-O1 / -O2 / -O3 預設**
- 做得好、慢

還有個比較實驗性的 basic (RegAllocBasic)，較少用。

## Live range / live interval

RA 的核心資訊：**每個 virtual reg 的 live range**。

```
%0 = ADD ...        ; %0 從這裡開始 live
...                  ; %0 的 live range
USE %0               ; last use → live range 結束
```

Live range = [def, last use]。跨 BB 的 range 處理靠 LiveIntervalAnalysis pass。

## Interference

兩個 virtual reg interfere = 它們同時 live（需要不同 physical reg）。

```
%0 = ADD ...       ; %0 live 開始
%1 = SUB ...       ; %1 live 開始；%0 仍 live → interfere
USE %0             ; %0 結束
USE %1             ; %1 結束
```

`%0` 跟 `%1` 不能分到同一 physical reg。

## Greedy RA 演算法概述

1. 算所有 virtual reg 的 live interval
2. 按 priority 排序（長 interval、高 use count 優先）
3. 對每個 interval：
   - 試找一個 physical reg 沒 interfere
   - 若找得到 → assign
   - 若找不到 → **spill** 或 **split** interval

### Spill

沒 reg 可用時，把某個 virtual reg 的值存到 stack、用時再 load。成本：

- 每次 def → store 到 stack
- 每次 use → load 回 reg

**hot loop 裡的 spill 是效能殺手**。

### Split

把長 interval 拆成短 interval。短 interval 更容易 fit。代價：拆點要插 copy。

Greedy 會試各種 split 策略找最佳。

## Coalescing

RA 前 MIR 充滿 `COPY` 指令（ISel 產生）：

```
%1 = COPY $x10
%2 = ADD %1, %3
```

如果 `%1` 跟 `$x10` 沒 interfere，coalesce 成：

```
%1 = ADD $x10, %3   ; 消除 copy
```

**Register coalescer** 是獨立 pass（在 RA 前跑），盡量消除 unnecessary copy。

## Spill 成本模型

Greedy 判斷「要 spill 哪個」靠 cost model：

```
cost = (def count × store_cost) + (use count × load_cost) / loop_depth
```

High loop depth 的 reg 優先保留（別 spill）。Leaf 區域的 reg 優先 spill。

這個 heuristic 在 `RegAllocGreedy.cpp` 裡、target 可以 tune。

## Register class 的影響

RISC-V 有多個 register class：

- `GPR`: x0-x31（32 顆）
- `GPRNoX0`: x1-x31（31 顆，SP/FP 等不能用作 dest）
- `GPRC`: x8-x15（8 顆，C 擴充壓縮版用）
- `FPR32` / `FPR64`: f0-f31
- `VR`: v0-v31（vector）
- `VMV0`: 只有 v0（mask register，特殊用）

每個 virtual reg 有 register class 限制。RA 只能在該 class 內分配。

**壓縮指令（C 擴充）約束 GPRC**：某些 c.add variant 只能用 x8-x15。compiler 產生 compressed instruction 時會把 virtual reg bind 到 GPRC → 限制 RA 可選。

## Prologue / epilogue 插入

RA 跑完後 **`PrologEpilogInserter` pass** 插入：

- Prologue：`addi sp, sp, -N`、存 callee-saved reg
- Epilogue：還 callee-saved reg、`addi sp, sp, N`

這需要知道「這個 function 用了哪些 callee-saved reg、用了多少 stack」。RA 提供這些資訊。

## Callee-saved vs caller-saved

回顧 `learn_riscv` Ch 2：

- callee-saved：`s0..s11`（x8, x9, x18-x27），callee 要保存
- caller-saved：`t0..t6, a0..a7, ra`，caller 要自己存

RA 傾向用 caller-saved（省 prologue overhead）。但如果 function 有 call、或 virtual reg 跨 call live，用 callee-saved 比較好（不用每個 call 前後存/還）。

這種「call-crossing live range」的判斷是 greedy RA 的一個 dimension。

## Debug RA

```bash
llc -debug-only=regalloc hello.ll 2>&1 | head -100
```

輸出：

```
Spilling %2: "ADD ..." due to register pressure
Selected physreg $x15 for %5
Cross-BB live interval split at bb2...
```

超詳細的 debug info。看 RA 在決定什麼。

## Spill 的 debug

```bash
llc -rewriter-print-spills hello.ll 2>&1 | grep -A2 SPILL
```

印出每個 spill / reload 的位置。

## 一個 spill 的例子

function 用 20 個同時 live 的變數：

```c
int foo(int a, ...) {  // 假設 20 個參數 / variable
    int x1 = a + 1;
    int x2 = a + 2;
    ...  // 20 個
    return x1 + x2 + ... + x20;
}
```

RISC-V 只有 ~25 個可用 GPR（扣 SP/GP/TP）。20 個 live + 中間計算 → 可能 spill。

compile 看：

```bash
clang -target riscv64 -O2 -S foo.c -o foo.s
```

會看到 `sw` / `lw` 對 sp 的存取 — 那些是 spill / reload。

**效能 debug 的一個 insight**：過多 variable live 同時 = 高 register pressure = 潛在 spill。某些優化（e.g., loop unroll 展開過多 iteration）會意外增加 register pressure。

## Register allocator 的 target hooks

Target 可以透過 `TargetRegisterInfo` 的 method 影響 RA：

```cpp
class RISCVRegisterInfo : public RISCVGenRegisterInfo {
public:
    // 這些 reg 不能被 RA 使用（永遠 reserved）
    BitVector getReservedRegs(const MachineFunction &MF) const override {
        BitVector Reserved = ...;
        Reserved.set(RISCV::X0);  // zero
        Reserved.set(RISCV::X2);  // sp
        Reserved.set(RISCV::X3);  // gp
        Reserved.set(RISCV::X4);  // tp
        return Reserved;
    }

    // callee-saved reg 清單
    const MCPhysReg *getCalleeSavedRegs(const MachineFunction *MF) const override;
};
```

## Stack slot colouring

多個 spilled variable 如果 live range 不 interfere，可以共用同一 stack slot。省 stack 空間。

**`StackSlotColoring` pass** 做這個 optimization。

## MachineFrameInfo

每個 MachineFunction 有 `MachineFrameInfo`，管理 stack layout：

- spill slot
- local variable
- struct return value space
- variadic args

prologue 產生時讀這個資訊算 frame size。

## 常見誤會

1. **「RA 越多 reg 越好」**：到某個點邊際效益降。32 顆 RV 夠多數 code，128 顆 GPU-style 反而 cache pollution。
2. **「spill 一定是 RA 失敗」**：有時刻意 spill 是最優解（e.g., 跨多 call 的變數用 callee-saved）。
3. **「virtual reg 永遠 SSA」**：RA 前是。RA 後 physical reg 可能重複寫。
4. **「所有 COPY 會被 coalescing 消掉」**：不。有些 COPY 不能消（例：RA 限制下 source 跟 dest 的 class 不同）。
5. **「RA 完成後 register 數字固定」**：prologue/epilogue / post-RA pass 可能還會產生新 COPY。

## 動手練習

1. 寫一個 function 有 15+ 個 live variable，對比 `-O0` vs `-O2` 的 spill 差異。
2. `llc -debug-only=regalloc hello.ll 2>&1 | grep -i spill` 看有沒有 spill。
3. 用 `-stop-after=greedy` dump RA 後的 MIR、確認 virtual reg 全變 physical reg。
4. 寫一個很大的 loop unroll 展開、看 register pressure 是否變高、產生 spill。
5. 讀 `RegAllocGreedy.cpp` 的 `selectOrSplit` 前 100 行，看 RA 的決策邏輯。

## 自我檢核

- [ ] 我能解釋 virtual reg → physical reg 的映射過程
- [ ] 我知道 live interval 跟 interference graph 的概念
- [ ] 我能解釋 spill / split / coalesce 的作用
- [ ] 我看到效能問題能判斷是不是 RA pressure 造成
- [ ] 我知道 RA 如何跟 callee-saved / caller-saved 互動

下一章講 scheduler 跟 scheduling model — 決定指令執行順序、壓榨 pipeline parallelism。

→ [Ch 13 Scheduler 與 scheduling model](./13-scheduler-and-sched-model.md)
