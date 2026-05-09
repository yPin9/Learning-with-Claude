# 練習 A — 實作 DCE + Constant Folding Pass

> 目標：把 Ch 13–19 的知識整合，從零實作兩個 LLVM Pass，用 FileCheck 驗證正確性。

## 任務規格

在 Ch 0 建立的 `~/ssa_passes/` 專案中，新增兩個 pass：

### Pass 1：SimpleDCE

消除「use count 為 0 且無副作用」的指令，支持遞迴傳播（刪除指令後操作數可能也變成死碼）。

**要求**：
- 使用 Worklist 驅動（不要掃描整個函式多次）
- 正確處理 `mayHaveSideEffects()`（store、call、ret 不能刪）
- 返回正確的 `PreservedAnalyses`（DCE 不改 CFG）

### Pass 2：ConstFold

對每條指令，如果所有操作數都是 `ConstantInt`，用 `ConstantFoldInstruction` 折疊它。

**要求**：
- 不需要考慮 phi 節點（可以跳過）
- 折疊後把使用者加入 Worklist（遞迴傳播）
- 返回正確的 `PreservedAnalyses`

## 期望輸出

### SimpleDCE 輸入

```llvm
define i32 @test_dce(i32 %x) {
entry:
  %dead1 = add i32 %x, 1        ; 沒有使用者
  %dead2 = mul i32 %dead1, 2    ; 沒有使用者，且操作數 dead1 也可以刪
  %live = add i32 %x, 0         ; 有使用者（ret）
  ret i32 %live
}
```

預期輸出：`dead1` 和 `dead2` 被刪除（`live` 和 `ret` 保留）。

### ConstFold 輸入

```llvm
define i32 @test_cf() {
entry:
  %a = add i32 3, 4       ; → 7
  %b = mul i32 %a, 2      ; → 14（%a 被折疊後）
  %c = add i32 %b, 1      ; → 15
  ret i32 %c
}
```

預期輸出：所有指令被折疊，`ret i32 15`。

## 實作步驟

### Step 1：搭建專案結構

```bash
cd ~/ssa_passes
mkdir PracticeA
```

**PracticeA/CMakeLists.txt**

```cmake
add_llvm_pass_plugin(PracticeAPass PracticeAPass.cpp)
```

**根目錄 CMakeLists.txt** 加入：

```cmake
add_subdirectory(PracticeA)
```

### Step 2：實作 SimpleDCE

```cpp
// PracticeA/PracticeAPass.cpp
#include "llvm/IR/PassManager.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Passes/PassPlugin.h"
#include "llvm/IR/Instructions.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/Support/raw_ostream.h"

using namespace llvm;

struct SimpleDCEPass : PassInfoMixin<SimpleDCEPass> {
    PreservedAnalyses run(Function &F, FunctionAnalysisManager &) {
        bool Changed = false;
        SmallVector<Instruction *, 16> Worklist;
        
        // 初始化：找所有死碼
        for (auto &BB : F)
            for (auto &I : BB)
                if (I.use_empty() && !I.mayHaveSideEffects())
                    Worklist.push_back(&I);
        
        while (!Worklist.empty()) {
            Instruction *I = Worklist.pop_back_val();
            if (!I->use_empty() || I->mayHaveSideEffects())
                continue;  // 狀態已改變，重新檢查
            
            // 刪前，把操作數也加入 Worklist（可能也變成死碼）
            for (Use &U : I->operands())
                if (auto *OpI = dyn_cast<Instruction>(U.get()))
                    if (OpI->use_empty() && !OpI->mayHaveSideEffects())
                        Worklist.push_back(OpI);
            
            I->eraseFromParent();
            Changed = true;
        }
        
        if (!Changed) return PreservedAnalyses::all();
        
        // DCE 不改 CFG
        PreservedAnalyses PA;
        PA.preserveSet<CFGAnalyses>();
        return PA;
    }
};
```

### Step 3：實作 ConstFold

```cpp
#include "llvm/Analysis/ConstantFolding.h"

struct ConstFoldPass : PassInfoMixin<ConstFoldPass> {
    PreservedAnalyses run(Function &F, FunctionAnalysisManager &FAM) {
        const DataLayout &DL = F.getParent()->getDataLayout();
        bool Changed = false;
        SmallVector<Instruction *, 16> Worklist;
        
        for (auto &BB : F)
            for (auto &I : BB)
                Worklist.push_back(&I);
        
        while (!Worklist.empty()) {
            Instruction *I = Worklist.pop_back_val();
            if (isa<PHINode>(I) || isa<TerminatorInst>(I)) continue;
            
            // 嘗試折疊
            if (Constant *C = ConstantFoldInstruction(I, DL)) {
                // 把使用者加入 Worklist（它們的操作數改了）
                for (User *U : I->users())
                    if (auto *UI = dyn_cast<Instruction>(U))
                        Worklist.push_back(UI);
                
                I->replaceAllUsesWith(C);
                I->eraseFromParent();
                Changed = true;
            }
        }
        
        if (!Changed) return PreservedAnalyses::all();
        PreservedAnalyses PA;
        PA.preserveSet<CFGAnalyses>();
        return PA;
    }
};
```

### Step 4：注冊 Pass

```cpp
llvm::PassPluginLibraryInfo getPracticeAPluginInfo() {
    return {LLVM_PLUGIN_API_VERSION, "PracticeA", LLVM_VERSION_STRING,
            [](PassBuilder &PB) {
                PB.registerPipelineParsingCallback(
                    [](StringRef Name, FunctionPassManager &FPM,
                       ArrayRef<PassBuilder::PipelineElement>) {
                        if (Name == "simple-dce") {
                            FPM.addPass(SimpleDCEPass());
                            return true;
                        }
                        if (Name == "const-fold") {
                            FPM.addPass(ConstFoldPass());
                            return true;
                        }
                        return false;
                    });
            }};
}

extern "C" LLVM_ATTRIBUTE_WEAK ::llvm::PassPluginLibraryInfo
llvmGetPassPluginInfo() {
    return getPracticeAPluginInfo();
}
```

### Step 5：編寫 FileCheck 測試

```
tests/dce_test.ll
tests/cf_test.ll
tests/run_tests.sh
```

**tests/dce_test.ll**

```llvm
; RUN: opt -load-pass-plugin %plugin -passes="simple-dce" -S %s | FileCheck %s

define i32 @test_basic_dce(i32 %x) {
entry:
; CHECK-LABEL: @test_basic_dce
; CHECK-NOT: %dead
  %dead = add i32 %x, 1
  %live = mul i32 %x, 2
; CHECK: ret i32 %live
  ret i32 %live
}

define void @test_chain_dce(i32 %x) {
entry:
; CHECK-LABEL: @test_chain_dce
; CHECK-NOT: %a
; CHECK-NOT: %b
  %a = add i32 %x, 1
  %b = mul i32 %a, 2    ; a 和 b 都應該被刪
  ret void
}
```

**tests/cf_test.ll**

```llvm
; RUN: opt -load-pass-plugin %plugin -passes="const-fold" -S %s | FileCheck %s

define i32 @test_fold_chain() {
; CHECK-LABEL: @test_fold_chain
; CHECK: ret i32 15
entry:
  %a = add i32 3, 4
  %b = mul i32 %a, 2
  %c = add i32 %b, 1
  ret i32 %c
}
```

### Step 6：編譯並跑測試

```bash
cmake --build ~/ssa_passes/build -j$(nproc)

PLUGIN=~/ssa_passes/build/PracticeA/PracticeAPass.so

# 手動測試
opt -load-pass-plugin $PLUGIN -passes="simple-dce" -S tests/dce_test.ll
opt -load-pass-plugin $PLUGIN -passes="const-fold" -S tests/cf_test.ll

# FileCheck 測試
opt -load-pass-plugin $PLUGIN -passes="simple-dce" -S tests/dce_test.ll | \
    FileCheck tests/dce_test.ll
```

## 進階挑戰

完成基本版後，嘗試以下擴展：

1. **組合 pipeline**：把 SimpleDCE 和 ConstFold 串聯，觀察折疊後暴露更多死碼的情況

2. **統計輸出**：在 pass 結束時印出「刪了幾條死碼 / 折疊了幾條指令」

3. **處理 phi**：修改 ConstFold，讓它也能折疊「所有引數相同的 phi」

4. **正確性驗證**：寫一個 C 程式，確認 pass 前後的執行結果相同

## 自我檢核

- [ ] SimpleDCE 能正確刪除鏈式死碼（刪 %b 後 %a 也變 dead）
- [ ] ConstFold 能遞迴傳播（折疊 %a 後，用到 %a 的 %b 也能折疊）
- [ ] 兩個 pass 都返回正確的 `PreservedAnalyses`
- [ ] FileCheck 測試全部通過

→ [Ch 20 迴圈識別：Natural Loop 與 Loop Nesting Forest](./20-loop-identification.md)
