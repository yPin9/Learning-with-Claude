# Ch 2 — Pass manager：legacy vs new

> 目標：理解 LLVM 的 Pass 系統 —— 為什麼所有 optimization / analysis 都是 pass、新舊兩套 pass manager 的差異、如何寫自己的 pass 跟 plug 進 pipeline。

## 什麼是 Pass

LLVM 的核心設計：**每個 optimization / analysis 是一個獨立 module，叫 pass**。例如：

- `mem2reg`：把 stack allocation 轉成 SSA register
- `instcombine`：peephole 優化
- `gvn`：global value numbering
- `inliner`：function inlining
- `loop-unroll`：loop 展開
- `dce`：dead code elimination

每個 pass **接收 IR、改 IR（或 analyze 出資訊）、回傳**。compile 就是一連串 pass 的 pipeline。

## 兩套 Pass Manager

LLVM 有兩套 API：

### Legacy Pass Manager (舊, 2003-2022)

```cpp
class MyPass : public FunctionPass {
public:
    static char ID;
    MyPass() : FunctionPass(ID) {}
    bool runOnFunction(Function &F) override {
        // modify F
        return true; // return true if changed
    }
};
```

設計問題：

- Analysis cache 管理繁瑣
- 無法精細控制哪個 pass 對哪個 scope
- IR 以外要傳遞資訊困難

### New Pass Manager (新, 2017+, 預設 2022+)

```cpp
class MyPass : public PassInfoMixin<MyPass> {
public:
    PreservedAnalyses run(Function &F, FunctionAnalysisManager &AM) {
        // modify F
        return PreservedAnalyses::none();  // 告訴 PM 哪些 analyses 仍有效
    }
};
```

改進：

- Analysis 用 `AnalysisManager` 管理
- `PreservedAnalyses` 表達「我改了什麼、哪些 analysis 還可信」
- 更多 template、更少 virtual function → 編譯器可以 inline → 更快
- 支援 `ModulePassManager > FunctionPassManager > LoopPassManager` 的嵌套

2026 今天 **90%+ code 用 new pass manager**。Legacy 還沒完全移除（有些 backend 仍用），但新 code 一律 new。

## Pass 的 scope

不同 pass 作用在不同 scope：

```
ModulePass       → 整個 Module（所有 function）
FunctionPass     → 一個 Function
LoopPass         → 一個 Loop
BasicBlockPass   → 一個 BasicBlock（極少用）
RegionPass       → 一個 Region（Control-flow region）
```

scope 越小越 local、parallel 越好。ModulePass 要掃整個 `.ll`、無法 parallel。

## Opt 的 pipeline

```bash
opt -passes="mem2reg,instcombine,gvn" hello.ll -S -o optimized.ll
```

`-passes=` 是 new PM 的語法。用 `,` 串接 pass、`(...)` 控制巢狀：

```bash
opt -passes="function(mem2reg),module(inline)" ...
```

意思：先對每個 function 跑 `mem2reg`、再對 module 跑 `inline`。

## 標準 pipeline

Clang `-O0` / `-O1` / `-O2` / `-O3` 各自對應一個「標準 pipeline」，由 `PassBuilder` 組出來。

`-O0`（幾乎沒優化）：

```
Trivial dead code elimination
Lower intrinsics
...
```

`-O2`（production 預設）：

```
CFGSimplification
SROA + mem2reg
Early CSE
Function inlining
GVN
LICM
Loop unroll
...
```

幾十個 pass。看 dump：

```bash
opt -O2 -debug-pass-manager hello.ll 2>&1 | head -50
```

會印出實際執行的 pass 順序。

## Analysis vs Transformation

Pass 分兩類：

- **Analysis**：不改 IR、只算資訊。例：`DominatorTreeAnalysis`、`LoopAnalysis`、`AliasAnalysis`
- **Transformation**：改 IR。例：`Mem2RegPass`、`InstCombinePass`

Transformation 通常依賴 Analysis。例：`LICM` 需要 `LoopAnalysis` 告訴它「這是 loop」。

Analysis 的結果被 **cached**。如果 transformation 沒 invalidate，下個 pass 可以 reuse。

## `PreservedAnalyses`

Pass 結束時告訴 PM：

```cpp
PreservedAnalyses PA;
PA.preserve<DominatorTreeAnalysis>();   // dominator tree 沒變
PA.preserve<LoopAnalysis>();             // loop 資訊沒變
return PA;
```

或全保留：

```cpp
return PreservedAnalyses::all();
```

或全 invalidate（最保守）：

```cpp
return PreservedAnalyses::none();
```

**寫錯很危險**：宣稱保留但其實動了 → 下游 pass 用 stale analysis → miscompile。

## 寫一個最簡 Function pass

```cpp
// MyCountInst.cpp
#include "llvm/IR/PassManager.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Passes/PassPlugin.h"
#include "llvm/IR/Function.h"
#include "llvm/Support/raw_ostream.h"

using namespace llvm;

struct MyCountInst : PassInfoMixin<MyCountInst> {
    PreservedAnalyses run(Function &F, FunctionAnalysisManager &) {
        unsigned count = 0;
        for (auto &BB : F)
            count += BB.size();
        errs() << F.getName() << " has " << count << " instructions\n";
        return PreservedAnalyses::all();
    }
};

// Register as a plugin
extern "C" PassPluginLibraryInfo LLVM_ATTRIBUTE_WEAK llvmGetPassPluginInfo() {
    return {
        LLVM_PLUGIN_API_VERSION, "MyCountInst", "0.1",
        [](PassBuilder &PB) {
            PB.registerPipelineParsingCallback(
                [](StringRef Name, FunctionPassManager &FPM,
                   ArrayRef<PassBuilder::PipelineElement>) {
                    if (Name == "my-count-inst") {
                        FPM.addPass(MyCountInst());
                        return true;
                    }
                    return false;
                });
        }};
}
```

Build：

```bash
clang++ -shared -fPIC \
    -I<llvm-include> \
    MyCountInst.cpp -o MyCountInst.so \
    `llvm-config --cxxflags`
```

執行：

```bash
opt -load-pass-plugin=./MyCountInst.so \
    -passes="my-count-inst" hello.ll -disable-output
# @add has 3 instructions
# @main has 5 instructions
```

**你寫了第一個 LLVM pass！**

## Pass 的 dependencies

要 analysis 結果：

```cpp
PreservedAnalyses run(Function &F, FunctionAnalysisManager &AM) {
    auto &DT = AM.getResult<DominatorTreeAnalysis>(F);
    // use DT...
}
```

PM 自動確保 `DominatorTreeAnalysis` 在你之前執行、cache 結果、在你後面 invalidation 時重算。

## 常見 analysis

```cpp
DominatorTreeAnalysis          // 支配樹
PostDominatorTreeAnalysis      // 後支配樹
LoopAnalysis                    // 迴圈偵測
BranchProbabilityAnalysis       // 分支機率
BlockFrequencyAnalysis          // 塊執行頻率
MemorySSAAnalysis               // memory SSA
AAManager (AliasAnalysis)       // 別名分析
ScalarEvolutionAnalysis         // 迴圈計數、誘導變數
TargetIRAnalysis                // target-specific cost
```

這些是現代 optimization 的基礎。Ch 3 會看具體 pass 怎麼用它們。

## `-fpass-plugin` 用自己的 pass

現代 Clang 也支援：

```bash
clang -fpass-plugin=./MyCountInst.so -mllvm -passes=my-count-inst hello.c
```

讓你的 pass 進 `-O2` pipeline 的某個 extension point。

## Backend 裡的 pass

Backend（`llc` 的部分）也是一連串 pass，但 scope 不同：

```
LLVM IR
  ↓ IR pass pipeline (前端已跑)
  ↓
Target-specific IR lowering
  ↓
SelectionDAG / GlobalISel
  ↓ DAG passes
  ↓
MIR (Machine IR)
  ↓ MachineFunctionPass pipeline
  ↓
MCInst
```

每層有自己的 pass manager。**Backend pass 多半是 `MachineFunctionPass`**，作用在 MIR 上。

用 `llc -print-after-all hello.ll` 看：

```
# *** IR Dump After EarlyIfConverterPass ***
...
# *** MachineFunction Dump After MachineSchedulerPass ***
...
```

每個 `*** Dump After X ***` 是一個 pass 執行後的 snapshot。

## 加新 pass 的流程

假設你要加一個 RISC-V-specific pass：

1. 寫 `RISCVMyOpt.cpp` 實作 pass
2. 加到 `CMakeLists.txt`
3. 在 `RISCVTargetMachine.cpp` 的 pass pipeline 註冊：
   ```cpp
   addPass(createRISCVMyOptPass());
   ```
4. Build + test

實例：`RISCVInsertVSETVLI.cpp` 就是這樣加上去的。Ch 15 會深入這個 pass。

## 常見誤會

1. **「Legacy PM 完全廢」**：Backend 裡還大量用 legacy（MachineFunctionPass 就是）。IR 層級大部分切 new PM。
2. **「一個 pass 只做一件事」**：理想如此，但實務很多 pass 組合多個 transformation。`instcombine` 跑上千個 peephole rule。
3. **「Analysis 不改 IR」**：理論上對，但某些 cache 的副作用存在。
4. **「Pass 越多越慢」**：不一定。短期可能慢、但 enable 下游優化可能快很多。`-O2` 的 pass 多、但整體編譯時間還可接受。
5. **「我可以隨意調 pass 順序」**：不能。很多 pass 預設 `mem2reg` 已跑過才能工作。亂調會 crash 或 miscompile。

## 動手練習

1. 跑 `opt -passes="mem2reg,instcombine" -debug hello.ll` 看每個 pass 的 debug 輸出。
2. 寫一個 simple pass 印出所有 function name + instruction count（參考上面的範例）。
3. 讀 `llvm/lib/Passes/PassBuilder.cpp` 的 `buildO2DefaultPipeline()`，看 `-O2` 到底跑哪些 pass。
4. 用 `-print-after=gvn` 只看 GVN 後的 IR。
5. 挑一個 open bug（LLVM issue tracker 上有「good first issue」），看能不能理解 pass 相關的 reproduce。

## 自我檢核

- [ ] 我能分辨 legacy 跟 new pass manager 的 API 差異
- [ ] 我能寫一個最簡的 FunctionPass 並 plug 進 `opt`
- [ ] 我知道 analysis 跟 transformation 的差別
- [ ] 我能用 `PreservedAnalyses` 正確表達 pass 的 invalidation
- [ ] 我知道 backend pass（MachineFunctionPass）跟 IR pass 的 scope 不同

下一章看 IR optimization pass 的地圖 —— InstCombine / GVN / LICM 等明星 pass 各做什麼。

→ [Ch 3 IR optimization 地圖：GVN / LICM / InstCombine](./03-ir-optimization-map.md)
