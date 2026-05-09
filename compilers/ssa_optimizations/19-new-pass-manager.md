# Ch 19 — New PassManager 架構

> 目標：理解 New PassManager（NPM）的設計，掌握 AnalysisManager 的失效機制，以及如何在 NPM 框架內寫依賴分析的 pass。

## 為什麼要換 PassManager

LLVM 舊版 PassManager（Legacy PM）的設計在 LLVM 10 以前主導，有幾個根本問題：

```
1. Pass 之間的依賴是全局的：每個 pass 聲明它需要哪些分析，
   但分析的計算和緩存是全局狀態，難以並行

2. 分析失效不精確：一個 pass 改了 IR 後，所有依賴的分析都
   被標記為失效，即使實際上沒有被破壞

3. 無法 nesting：不能在不同粒度（module/function/loop）的
   pass 間組合優化
```

**New PassManager（NPM，LLVM 14 正式替換）** 解決了這些問題：

```
1. AnalysisManager 是 pass 的局部狀態，不共享全局
2. 分析失效是精確的：pass 聲明 PreservedAnalyses（保留了哪些）
3. 層次化設計：ModulePassManager → FunctionPassManager → LoopPassManager
```

## Pass 的三種粒度

```
ModulePass：對整個模組操作（跨函式分析、LTO）
FunctionPass：對單個函式操作（大多數優化）
LoopPass：對單個迴圈操作（LICM、loop unrolling）
```

每種粒度有對應的 PassManager 和 AnalysisManager：

```cpp
ModuleAnalysisManager MAM;
FunctionAnalysisManager FAM;
LoopAnalysisManager LAM;
```

## 寫一個 FunctionPass

```cpp
struct MyPass : PassInfoMixin<MyPass> {
    // run 函式：必須實作
    PreservedAnalyses run(Function &F, FunctionAnalysisManager &FAM) {
        // 查詢分析（第一次查詢時自動計算，之後從緩存取）
        auto &DT = FAM.getResult<DominatorTreeAnalysis>(F);
        auto &LI = FAM.getResult<LoopAnalysis>(F);
        
        bool Changed = doSomething(F, DT, LI);
        
        if (!Changed) {
            return PreservedAnalyses::all();    // 沒改 IR，保留所有分析
        }
        
        // 聲明哪些分析仍然有效
        PreservedAnalyses PA;
        PA.preserve<DominatorTreeAnalysis>();   // 支配樹沒被破壞
        PA.preserve<LoopAnalysis>();            // 迴圈分析也還有效
        // 其他分析（CFG structure, SCCP 等）失效
        return PA;
    }
    
    // 靜態方法：讓 NPM 知道這個 pass 的名字（用於 -passes="mypass"）
    static StringRef name() { return "mypass"; }
};
```

`PreservedAnalyses` 是精確失效的關鍵。如果你的 pass 只改了指令的值但沒改 CFG（沒有新增/刪除基本塊，沒有改跳轉），通常支配樹和迴圈結構都還有效。

## 寫一個 Analysis（分析）

分析和 pass 不同：它只計算資訊，不修改 IR。

```cpp
struct MyAnalysis : AnalysisInfoMixin<MyAnalysis> {
    // 分析的結果類型
    struct Result {
        int someData;
        // 結果被丟棄時是否需要清理？
        bool invalidate(Function &F, const PreservedAnalyses &PA,
                        FunctionAnalysisManager::Invalidator &Inv) {
            // 如果這個分析依賴的其他分析失效了，我們也失效
            return !PA.preservedSet<AllAnalysesOn<Function>>().isPreserved();
        }
    };
    
    Result run(Function &F, FunctionAnalysisManager &FAM) {
        // 計算並返回結果
        return Result{42};
    }
    
    static AnalysisKey Key;  // 唯一標識符
};
AnalysisKey MyAnalysis::Key;
```

使用：

```cpp
auto &MyResult = FAM.getResult<MyAnalysis>(F);
```

NPM 保證：同一個 `F` 在 `MyAnalysis` 有效期間，`getResult` 返回相同的緩存結果，不重複計算。

## AnalysisManager 的失效機制

當一個 pass 返回 `PreservedAnalyses PA`，NPM 會：

1. 對 `PA` 中列出的分析：保留緩存
2. 對其他分析：標記為失效，下次 `getResult` 會重新計算

精確的失效傳播：

```
pass 改了 CFG 結構
→ DominatorTreeAnalysis 失效
→ 所有依賴 DominatorTreeAnalysis 的分析（如 LoopAnalysis）也失效
```

失效的傳播是通過各個 Analysis 的 `invalidate()` 方法實現的（見上面的 `Result::invalidate`）。

## Pass Pipeline 組合

```cpp
// 建立 pipeline
FunctionPassManager FPM;
FPM.addPass(InstCombinePass());
FPM.addPass(GVNPass());
FPM.addPass(DCEPass());

// 把 FunctionPassManager 放進 ModulePassManager
ModulePassManager MPM;
MPM.addPass(createModuleToFunctionPassAdaptor(std::move(FPM)));

// 跑 pipeline
MPM.run(*M, MAM);
```

`createModuleToFunctionPassAdaptor` 把 FunctionPassManager 包裝成 ModulePass，對每個函式依次執行。

## 命令行的 Pass Pipeline

`opt` 的 `-passes=` 語法對應到 NPM：

```bash
# 等價的 C++ 和命令行
opt -passes="instcombine,gvn,dce" input.ll -o output.ll

# 帶分析的 pass
opt -passes="loop-vectorize" input.ll -o output.ll
# 內部會自動計算需要的 LoopInfo、ScalarEvolution 等

# 不同粒度的組合
opt -passes="module(inline,function(instcombine,gvn))" input.ll
# 先跑 inline（module 級），再對每個函式跑 instcombine 和 gvn
```

## 自我檢核

- [ ] NPM vs Legacy PM：精確失效 + 層次化 + 無全局狀態
- [ ] `PreservedAnalyses`：不改 CFG 通常能保留 DominatorTreeAnalysis
- [ ] Pass vs Analysis：pass 改 IR，analysis 只計算信息
- [ ] `FAM.getResult<AnalysisType>(F)` 自動緩存，不重複計算
- [ ] 能用 `-passes=` 組合不同粒度的 pass pipeline

→ [練習 A：實作 DCE + Constant Folding Pass](./practice-a-dce-pass.md)
