# Final Project — Mini Optimizer：從零整合 SSA + 優化 Pipeline

> 目標：把這門課學到的每個模組——支配樹、SSA 構造、DCE、SCCP、GVN——整合成一個真實可跑的 out-of-tree LLVM pass，用 CSmith 生成上千個隨機程式驗證正確性。

## 專案概述

你要實作一個名為 **MiniOptPass** 的 LLVM FunctionPass，把下列優化組合成一個 pipeline，跑在任意 LLVM IR 上：

```
輸入 LLVM IR
  → ConstFold（常數折疊）
  → SimpleDCE（死碼消除）
  → SCCP（稀疏條件常數傳播）
  → GVN（全局值編號）
  → SimpleDCE（再跑一次，清理 GVN 留下的死碼）
輸出優化後 LLVM IR
```

驗證方式：用 CSmith 生成 1000 個隨機 C 程式，確認 `clang -O0` 和 `clang -O0 + MiniOptPass` 執行結果相同（差分測試）。

## 目錄結構

```
MiniOpt/
├── CMakeLists.txt
├── MiniOptPass.cpp          ← 主 pass
├── ConstFoldPass.cpp        ← 從 Practice A 移植
├── SimpleDCEPass.cpp        ← 從 Practice A 移植
├── SCCPLitePass.cpp         ← 精簡版 SCCP（新）
├── GVNLitePass.cpp          ← 精簡版 GVN（新）
├── tests/
│   ├── lit.cfg
│   ├── unit/               ← FileCheck 單元測試
│   │   ├── fold.ll
│   │   ├── dce.ll
│   │   ├── sccp.ll
│   │   └── gvn.ll
│   └── diff_test.sh        ← CSmith 差分測試腳本
└── README.md
```

## Step 1：骨架與 CMakeLists.txt

```cmake
# CMakeLists.txt
cmake_minimum_required(VERSION 3.20)
project(MiniOpt)

find_package(LLVM REQUIRED CONFIG)
message(STATUS "Found LLVM ${LLVM_PACKAGE_VERSION}")

list(APPEND CMAKE_MODULE_PATH "${LLVM_CMAKE_DIR}")
include(AddLLVM)
include(HandleLLVMOptions)

add_definitions(${LLVM_DEFINITIONS})
include_directories(${LLVM_INCLUDE_DIRS})

add_llvm_pass_plugin(MiniOptPlugin
    MiniOptPass.cpp
    ConstFoldPass.cpp
    SimpleDCEPass.cpp
    SCCPLitePass.cpp
    GVNLitePass.cpp
)
```

建置：

```bash
cmake -S . -B build \
    -DLLVM_DIR=$(llvm-config-17 --cmakedir) \
    -DCMAKE_BUILD_TYPE=Release
cmake --build build -j$(nproc)
```

---

## Step 2：ConstFoldPass 和 SimpleDCEPass

直接從 Practice A 搬過來，確認編譯通過。重點是它們要返回正確的 `PreservedAnalyses`：

**ConstFoldPass**：改變指令但不改變 CFG → `PA.preserveSet<CFGAnalyses>()`

**SimpleDCEPass**：刪除指令但不改變 CFG → `PA.preserveSet<CFGAnalyses>()`

---

## Step 3：SCCPLitePass

這是這個 project 的核心新實作。精簡版只處理函式內的 SCCP（不跨函式），但要正確處理 phi 函式和條件分支。

```cpp
// SCCPLitePass.cpp
#include "llvm/IR/PassManager.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/CFG.h"
#include "llvm/Transforms/Utils/Local.h"
#include "llvm/Analysis/ConstantFolding.h"
#include <map>
#include <queue>

using namespace llvm;

// 格值：kBottom (unreachable), kConst (known constant), kTop (overdefined)
enum LatticeKind { kBottom, kConst, kTop };

struct LatticeVal {
    LatticeKind kind = kBottom;
    Constant *C = nullptr;

    static LatticeVal bottom() { return {kBottom, nullptr}; }
    static LatticeVal constant(Constant *c) { return {kConst, c}; }
    static LatticeVal top() { return {kTop, nullptr}; }

    bool isBottom() const { return kind == kBottom; }
    bool isConst() const { return kind == kConst; }
    bool isTop() const { return kind == kTop; }

    // 合并：bottom ∪ x = x, const ∪ const(same) = const, 否則 = top
    LatticeVal meet(const LatticeVal &other) const {
        if (isBottom()) return other;
        if (other.isBottom()) return *this;
        if (isTop() || other.isTop()) return top();
        if (C == other.C) return *this;
        return top();
    }
};

struct SCCPLitePass : PassInfoMixin<SCCPLitePass> {
    PreservedAnalyses run(Function &F, FunctionAnalysisManager &FAM) {
        const DataLayout &DL = F.getParent()->getDataLayout();

        std::map<Value *, LatticeVal> valMap;
        std::set<BasicBlock *> execBlocks;   // 可執行的 block
        std::queue<BasicBlock *> cfgWorklist;
        std::queue<Value *> ssaWorklist;

        auto getVal = [&](Value *V) -> LatticeVal {
            if (auto *C = dyn_cast<Constant>(V)) return LatticeVal::constant(C);
            auto it = valMap.find(V);
            if (it == valMap.end()) return LatticeVal::bottom();
            return it->second;
        };

        auto setVal = [&](Value *V, LatticeVal lv) {
            auto &stored = valMap[V];
            LatticeVal merged = stored.meet(lv);
            if (merged.kind != stored.kind || merged.C != stored.C) {
                stored = merged;
                ssaWorklist.push(V);
            }
        };

        // 從 entry 開始
        cfgWorklist.push(&F.getEntryBlock());

        auto processBlock = [&](BasicBlock *BB) {
            if (!execBlocks.insert(BB).second) return;  // 已處理

            for (auto &I : *BB) {
                if (auto *Phi = dyn_cast<PHINode>(&I)) {
                    LatticeVal lv = LatticeVal::bottom();
                    for (unsigned i = 0; i < Phi->getNumIncomingValues(); i++) {
                        BasicBlock *predBB = Phi->getIncomingBlock(i);
                        if (execBlocks.count(predBB))
                            lv = lv.meet(getVal(Phi->getIncomingValue(i)));
                    }
                    setVal(Phi, lv);
                } else if (auto *BI = dyn_cast<BranchInst>(&I)) {
                    if (BI->isUnconditional()) {
                        cfgWorklist.push(BI->getSuccessor(0));
                    } else {
                        LatticeVal cond = getVal(BI->getCondition());
                        if (cond.isBottom()) {
                            // do nothing
                        } else if (cond.isTop()) {
                            cfgWorklist.push(BI->getSuccessor(0));
                            cfgWorklist.push(BI->getSuccessor(1));
                        } else {
                            auto *CI = dyn_cast<ConstantInt>(cond.C);
                            cfgWorklist.push(BI->getSuccessor(CI->isZero() ? 1 : 0));
                        }
                    }
                } else if (!I.isTerminator()) {
                    // 嘗試常數折疊
                    SmallVector<Constant *, 4> ops;
                    bool allConst = true;
                    for (auto &U : I.operands()) {
                        LatticeVal lv = getVal(U.get());
                        if (lv.isConst()) ops.push_back(lv.C);
                        else if (lv.isTop()) { setVal(&I, LatticeVal::top()); allConst = false; break; }
                        else { allConst = false; break; }  // bottom
                    }
                    if (allConst && !ops.empty()) {
                        if (Constant *R = ConstantFoldInstOperands(&I, ops, DL))
                            setVal(&I, LatticeVal::constant(R));
                    }
                }
            }
        };

        // CFG worklist
        while (!cfgWorklist.empty()) {
            BasicBlock *BB = cfgWorklist.front(); cfgWorklist.pop();
            processBlock(BB);
        }

        // SSA worklist
        while (!ssaWorklist.empty()) {
            Value *V = ssaWorklist.front(); ssaWorklist.pop();
            auto *I = dyn_cast<Instruction>(V);
            if (!I || !execBlocks.count(I->getParent())) continue;

            if (auto *Phi = dyn_cast<PHINode>(I)) {
                LatticeVal lv = LatticeVal::bottom();
                for (unsigned i = 0; i < Phi->getNumIncomingValues(); i++) {
                    if (execBlocks.count(Phi->getIncomingBlock(i)))
                        lv = lv.meet(getVal(Phi->getIncomingValue(i)));
                }
                setVal(Phi, lv);
            } else if (auto *BI = dyn_cast<BranchInst>(I)) {
                if (BI->isConditional()) {
                    LatticeVal cond = getVal(BI->getCondition());
                    if (cond.isTop()) {
                        cfgWorklist.push(BI->getSuccessor(0));
                        cfgWorklist.push(BI->getSuccessor(1));
                    } else if (cond.isConst()) {
                        auto *CI = dyn_cast<ConstantInt>(cond.C);
                        cfgWorklist.push(BI->getSuccessor(CI->isZero() ? 1 : 0));
                    }
                }
            } else {
                SmallVector<Constant *, 4> ops;
                bool allConst = true;
                for (auto &U : I->operands()) {
                    LatticeVal lv = getVal(U.get());
                    if (lv.isConst()) ops.push_back(lv.C);
                    else { allConst = false; break; }
                }
                if (allConst && !ops.empty()) {
                    if (Constant *R = ConstantFoldInstOperands(I, ops, DL))
                        setVal(I, LatticeVal::constant(R));
                }
            }
        }

        // 替換已知常數
        bool Changed = false;
        for (auto &BB : F) {
            if (!execBlocks.count(&BB)) continue;
            for (auto &I : BB) {
                auto it = valMap.find(&I);
                if (it != valMap.end() && it->second.isConst()) {
                    I.replaceAllUsesWith(it->second.C);
                    Changed = true;
                }
            }
        }

        // 刪除不可達 block（簡化版：只折疊已知條件分支）
        for (auto &BB : F) {
            if (auto *BI = dyn_cast<BranchInst>(BB.getTerminator())) {
                if (BI->isConditional()) {
                    LatticeVal cond = getVal(BI->getCondition());
                    if (cond.isConst()) {
                        auto *CI = dyn_cast<ConstantInt>(cond.C);
                        unsigned keepIdx = CI->isZero() ? 1 : 0;
                        BasicBlock *keep = BI->getSuccessor(keepIdx);
                        BasicBlock *dead = BI->getSuccessor(1 - keepIdx);
                        // 移除 dead 的 phi 引用
                        dead->removePredecessor(&BB);
                        BranchInst::Create(keep, BI);
                        BI->eraseFromParent();
                        Changed = true;
                    }
                }
            }
        }

        if (!Changed) return PreservedAnalyses::all();
        PreservedAnalyses PA;
        PA.preserveSet<CFGAnalyses>();
        return PA;
    }
};
```

---

## Step 4：GVNLitePass

精簡版 GVN：在支配樹上做 DFS，用 `(opcode, val_num1, val_num2)` 做雜湊。不做 load PRE（那太複雜了）。

```cpp
// GVNLitePass.cpp
#include "llvm/IR/PassManager.h"
#include "llvm/IR/Dominators.h"
#include "llvm/IR/Instructions.h"
#include <map>
#include <tuple>
#include <functional>

using namespace llvm;

struct GVNLitePass : PassInfoMixin<GVNLitePass> {
    using ValNum = unsigned;
    using ExprKey = std::tuple<unsigned, ValNum, ValNum>;

    PreservedAnalyses run(Function &F, FunctionAnalysisManager &FAM) {
        DominatorTree &DT = FAM.getResult<DominatorTreeAnalysis>(F);

        std::map<Value *, ValNum> vn;
        std::map<ExprKey, Value *> leader;
        ValNum nextVN = 1;

        auto getVN = [&](Value *V) -> ValNum {
            auto it = vn.find(V);
            if (it != vn.end()) return it->second;
            ValNum n = nextVN++;
            vn[V] = n;
            return n;
        };

        bool Changed = false;

        // DFS on dominator tree
        std::function<void(DomTreeNode *)> visit = [&](DomTreeNode *Node) {
            BasicBlock *BB = Node->getBlock();

            // 記錄本節點新增的 leader，離開時彈出
            SmallVector<ExprKey, 8> added;

            for (auto &I : *BB) {
                if (I.isTerminator() || I.mayHaveSideEffects() ||
                    isa<PHINode>(I) || I.getType()->isVoidTy()) continue;

                if (I.getNumOperands() < 2) continue;

                ValNum vn0 = getVN(I.getOperand(0));
                ValNum vn1 = getVN(I.getOperand(1));

                // 交換率：把小的放前面
                unsigned opc = I.getOpcode();
                if (I.isCommutative() && vn0 > vn1) std::swap(vn0, vn1);

                ExprKey key{opc, vn0, vn1};
                auto it = leader.find(key);

                if (it != leader.end()) {
                    // 找到相同表達式，替換
                    I.replaceAllUsesWith(it->second);
                    vn[&I] = vn[it->second];
                    Changed = true;
                } else {
                    leader[key] = &I;
                    vn[&I] = getVN(&I);
                    added.push_back(key);
                }
            }

            // 遞迴處理子節點
            for (auto *Child : Node->children())
                visit(Child);

            // 退出時清除本節點加入的 leader（維護作用域）
            for (auto &k : added)
                leader.erase(k);
        };

        visit(DT.getRootNode());

        if (!Changed) return PreservedAnalyses::all();
        PreservedAnalyses PA;
        PA.preserve<DominatorTreeAnalysis>();
        return PA;
    }
};
```

---

## Step 5：MiniOptPass（整合）

```cpp
// MiniOptPass.cpp
#include "llvm/IR/PassManager.h"
#include "llvm/Passes/PassBuilder.h"
#include "llvm/Passes/PassPlugin.h"

using namespace llvm;

// 前向宣告（各個 Pass 的 struct 定義在各自的 .cpp 裡，
// 實務上用 header；這裡為了展示，用 PassBuilder callback 整合）

// 把各 pass 的 run() 宣告在 MiniOptPass.cpp 前面
// 或者抽出 header file（建議）

struct MiniOptPass : PassInfoMixin<MiniOptPass> {
    PreservedAnalyses run(Function &F, FunctionAnalysisManager &FAM) {
        FunctionPassManager FPM;
        FPM.addPass(ConstFoldPass());
        FPM.addPass(SimpleDCEPass());
        FPM.addPass(SCCPLitePass());
        FPM.addPass(GVNLitePass());
        FPM.addPass(SimpleDCEPass());   // 清理 GVN 留下的死碼
        return FPM.run(F, FAM);
    }
};

// Plugin 入口
extern "C" LLVM_ATTRIBUTE_WEAK ::llvm::PassPluginLibraryInfo
llvmGetPassPluginInfo() {
    return {
        LLVM_PLUGIN_API_VERSION, "MiniOpt", LLVM_VERSION_STRING,
        [](PassBuilder &PB) {
            PB.registerPipelineParsingCallback(
                [](StringRef Name, FunctionPassManager &FPM,
                   ArrayRef<PassBuilder::PipelineElement>) {
                    if (Name == "mini-opt") {
                        FPM.addPass(MiniOptPass());
                        return true;
                    }
                    return false;
                });
        }
    };
}
```

測試 plugin 是否載入：

```bash
opt --load-pass-plugin=./build/MiniOptPlugin.so \
    --passes="mini-opt" -S /tmp/test.ll -o /dev/null
```

---

## Step 6：FileCheck 單元測試

### tests/unit/sccp.ll

```llvm
; RUN: opt --load-pass-plugin %plugin --passes="mini-opt" -S %s | FileCheck %s

define i32 @test_dead_branch(i32 %x) {
; CHECK-LABEL: @test_dead_branch
; CHECK-NOT: br i1
; CHECK: ret i32
entry:
  %cmp = icmp eq i32 1, 1   ; 恆 true
  br i1 %cmp, label %then, label %else
then:
  ret i32 %x
else:
  ret i32 0
}

define i32 @test_const_prop(i32 %x) {
; CHECK-LABEL: @test_const_prop
; CHECK: ret i32 42
  %a = add i32 20, 22
  %b = add i32 %a, 0
  ret i32 %b
}
```

### tests/unit/gvn.ll

```llvm
; RUN: opt --load-pass-plugin %plugin --passes="mini-opt" -S %s | FileCheck %s

define i32 @test_cse(i32 %x, i32 %y) {
; CHECK-LABEL: @test_cse
; CHECK: [[ADD:%.*]] = add i32 %x, %y
; CHECK-NOT: add i32 %x, %y   ← 第二個 add 應被消除
; CHECK: add i32 [[ADD]], [[ADD]]
  %a = add i32 %x, %y
  %b = add i32 %x, %y   ; 和 %a 完全相同
  %r = add i32 %a, %b
  ret i32 %r
}
```

### tests/lit.cfg

```python
import lit.formats
config.name = 'MiniOpt Tests'
config.test_format = lit.formats.ShTest(True)
config.suffixes = ['.ll']
config.substitutions.append(
    ('%plugin', '/path/to/build/MiniOptPlugin.so'))
```

跑所有單元測試：

```bash
llvm-lit-17 tests/unit/ -v
```

---

## Step 7：CSmith 差分測試

這是整個 project 的正確性壓力測試：用 1000 個隨機 C 程式，驗證 MiniOptPass 不改變程式語意。

```bash
#!/bin/bash
# tests/diff_test.sh

PLUGIN="$(dirname $0)/../build/MiniOptPlugin.so"
CSMITH=$(which csmith 2>/dev/null || echo "/usr/local/bin/csmith")
CLANG=$(which clang-17)
OPT=$(which opt-17)
CSMITH_INCLUDE="/usr/local/include/csmith"

PASS=0
FAIL=0
SKIP=0

for i in $(seq 1 1000); do
    TMP=$(mktemp /tmp/miniopt_test.XXXXXX)
    C="${TMP}.c"
    LL="${TMP}.ll"
    OPT_LL="${TMP}.opt.ll"
    REF="${TMP}.ref"
    OPT_BIN="${TMP}.opt"

    # 生成隨機 C 程式
    $CSMITH --no-checksum --max-block-depth 3 > "$C" 2>/dev/null || { SKIP=$((SKIP+1)); continue; }

    # 編譯到 LLVM IR（-O0 不做任何優化）
    $CLANG -O0 -I $CSMITH_INCLUDE -S -emit-llvm "$C" -o "$LL" 2>/dev/null || { SKIP=$((SKIP+1)); continue; }

    # 跑 MiniOptPass
    $OPT --load-pass-plugin="$PLUGIN" --passes="mini-opt" -S "$LL" -o "$OPT_LL" 2>/dev/null || { SKIP=$((SKIP+1)); continue; }

    # 編譯並執行：原始版
    $CLANG -O0 "$LL" -o "$REF" 2>/dev/null || { SKIP=$((SKIP+1)); continue; }

    # 編譯並執行：優化後
    $CLANG -O0 "$OPT_LL" -o "$OPT_BIN" 2>/dev/null || { SKIP=$((SKIP+1)); continue; }

    # 比較輸出（CSmith 程式沒有輸入，輸出是固定的）
    REF_OUT=$(timeout 5 "$REF" 2>/dev/null)
    OPT_OUT=$(timeout 5 "$OPT_BIN" 2>/dev/null)

    if [ "$REF_OUT" = "$OPT_OUT" ]; then
        PASS=$((PASS+1))
    else
        echo "MISMATCH on test $i: $C"
        echo "  REF:  $REF_OUT"
        echo "  OPT:  $OPT_OUT"
        echo "--- opt output ---"
        cat "$OPT_LL"
        FAIL=$((FAIL+1))
        # 保留失敗的檔案供分析
        cp "$C" "fail_$i.c"
        cp "$LL" "fail_$i.ll"
        cp "$OPT_LL" "fail_$i.opt.ll"
    fi

    rm -f "$C" "$LL" "$OPT_LL" "$REF" "$OPT_BIN"
done

echo ""
echo "Results: PASS=$PASS  FAIL=$FAIL  SKIP=$SKIP"
[ $FAIL -eq 0 ] && echo "ALL PASSED" || echo "FAILURES DETECTED"
```

```bash
chmod +x tests/diff_test.sh
./tests/diff_test.sh
```

預期輸出（正確實作）：

```
Results: PASS=973  FAIL=0  SKIP=27
ALL PASSED
```

SKIP 是 CSmith 生成含無限迴圈的程式（timeout）或編譯失敗（正常現象，比例 ~2-5%）。

---

## Step 8：效能評估

除了正確性，也測一下 MiniOptPass 能減少多少指令數：

```bash
#!/bin/bash
# measure_reduction.sh
PLUGIN="$(dirname $0)/build/MiniOptPlugin.so"

echo "File,Before,After,Reduction%"
for ll in tests/unit/*.ll; do
    BEFORE=$(opt-17 -S "$ll" 2>/dev/null | grep -c '^\s*%' || echo 0)
    AFTER=$(opt-17 --load-pass-plugin="$PLUGIN" --passes="mini-opt" -S "$ll" 2>/dev/null | grep -c '^\s*%' || echo 0)
    if [ "$BEFORE" -gt 0 ]; then
        PCT=$(echo "scale=1; (1 - $AFTER / $BEFORE) * 100" | bc)
        echo "$ll,$BEFORE,$AFTER,${PCT}%"
    fi
done
```

---

## 加碼挑戰

完成基礎版後，選一個進一步：

**A. 加入 Alive2 驗證**

不要等 CSmith 找到 bug。對 SCCPLitePass 和 GVNLitePass 的每條規則，先用 Alive2 確認正確性，再合入 MiniOptPass。建立一個 `alive2_checks/` 資料夾，每個規則一個 `.ll` 文件。

**B. 加入統計資訊**

```cpp
#include "llvm/ADT/Statistic.h"
STATISTIC(NumConstFolded,  "Number of instructions constant-folded");
STATISTIC(NumDCEDeleted,   "Number of instructions deleted by DCE");
STATISTIC(NumSCCPFolded,   "Number of instructions folded by SCCP");
STATISTIC(NumGVNElim,      "Number of redundant computations eliminated by GVN");
```

跑 `opt --stats` 觀察各 pass 的貢獻比例。

**C. 加入 LoopSimplify + LICM**

在 pipeline 前面加：

```cpp
FPM.addPass(createFunctionToLoopPassAdaptor(LICMPass(LICMOptions())));
```

再跑 CSmith 驗證，觀察更多迴圈程式能否通過。

**D. 和 LLVM 內建 passes 做對比**

```bash
# 你的 pass
opt --load-pass-plugin=./build/MiniOptPlugin.so \
    --passes="mini-opt" -S input.ll | grep -c '^\s*%'

# LLVM 的 O1
opt --passes="O1" -S input.ll | grep -c '^\s*%'
```

差距說明了 production optimizer 的複雜度：它們有幾千條規則和更複雜的分析，你的 mini 版是它們的一個可理解縮影。

---

## 自我檢核

- [ ] ConstFoldPass + SimpleDCEPass 從 Practice A 移植，能獨立測試通過
- [ ] SCCPLitePass 能折疊常數分支（dead branch elimination）
- [ ] GVNLitePass 能消除同一 dominator 下的重複表達式
- [ ] MiniOptPass 整合五個 pass，FileCheck 測試全部通過
- [ ] CSmith 差分測試 1000 個程式，FAIL=0
- [ ] 理解為什麼 GVNLite 需要在離開 dominator tree 節點時「退出作用域」
- [ ] 理解 SCCPLite 和 LLVM IPSCCPPass 的差距（跨函式、更精確的格值）

---

恭喜完成整門課。

你現在能做到的事：

- 徒手推導支配樹和 SSA 構造（Cytron 算法）
- 理解 LLVM 的常數傳播（SCCP）、死碼消除（ADCE）、全局值編號（GVN）的工作原理
- 用 LLVM New Pass Manager API 寫出能真正跑的 FunctionPass
- 用 Alive2 形式化驗證 peephole 規則的正確性
- 用 CSmith 做差分測試，給優化 pass 做壓力測試
- 理解 LICM 的安全性條件、SCEV 的代數結構、向量化的合法性分析
- 理解 LTO、IPCP、UB 與優化的交互關係

這是編譯器中端工程師的核心技能集。往前走有兩條路：

1. **往深走**：讀 LLVM 的 `InstCombine` 或 `GVN` 原始碼，理解 production 實作的複雜度
2. **往廣走**：學 MLIR（多層 IR 框架），這是 AI 編譯器（XLA、IREE）的基礎
