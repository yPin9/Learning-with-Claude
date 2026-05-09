# Ch 26 — Loop Pass Pipeline：優化順序問題

> 目標：理解迴圈優化 pass 之間的依賴關係，掌握 LLVM 標準迴圈 pipeline 的順序邏輯，以及順序錯誤時的典型症狀。

## 優化 Pass 有依賴關係

直覺上，「跑越多優化越好」——但實際上，優化 pass 的執行**順序**影響最終效果，某些 pass 必須在其他 pass 之後才能發揮作用，或者會破壞其他 pass 需要的不變式。

## 基礎準備 Pass（必須最先跑）

在任何迴圈優化之前，必須先建立 canonical form：

```
mem2reg          → 把 alloca 變數轉成 SSA（迴圈 phi 才能被 SCEV 分析）
loop-simplify    → 確保有 preheader 和唯一 latch
lcssa            → 迴圈輸出通過出口 phi
indvars-simplify → 把感應變數正規化成最簡形式
```

`indvars-simplify` 很重要：它把形如 `for (i = a; i < b; i += step)` 的迴圈轉換成 `for (iv = 0; iv < trip_count; iv++)`，讓 SCEV 的分析更容易。

```bash
# 標準前置 pipeline
opt -passes="mem2reg,loop-simplify,lcssa,indvars" input.ll
```

## LICM 要在 InstCombine 之後

LICM 依賴指令已被正規化。如果 `a * stride` 沒有被 InstCombine 化簡，LICM 可能無法識別它是不變的。

```
InstCombine → LICM
```

但 LICM 之後，被提出的指令可能暴露新的常數折疊機會，所以通常需要再跑一輪 InstCombine：

```
InstCombine → LICM → InstCombine（再跑）
```

## Loop Unrolling 要在向量化之前

展開把迴圈的多次迭代「顯式化」，讓 SLP 向量化有更多相鄰指令可以打包：

```
Loop Unrolling → SLP Vectorizer
```

但如果先做迴圈向量化（Loop Vectorizer），迴圈結構已改成向量版本，再展開沒有意義。

```
Loop Vectorizer → Loop Unrolling（錯誤順序，展開已向量化的迴圈沒有意義）
```

## SCEV 依賴於 IndVarSimplify

SCEV 在感應變數已是正規化形式（IndVars pass 之後）時，分析結果最精確：

```
IndVarSimplify → SCEV（更精確的 trip count 和 AddRec 識別）
→ Loop Unrolling（trip count 已知 → 全展開判定）
→ Loop Vectorize（VF 決策依賴 trip count）
```

如果沒跑 IndVarSimplify，SCEV 可能返回 `SCEVCouldNotCompute`，導致展開和向量化失敗。

## LLVM O2 的標準迴圈 Pipeline

LLVM O2 的優化流水線（簡化版）：

```
1. mem2reg                    → 建 SSA
2. instcombine                → 正規化
3. simplifycfg                → 清理 CFG
4. loop-simplify              → 迴圈 canonical form
5. lcssa                      → 迴圈閉合 SSA
6. licm                       → 不變式外提
7. indvars                    → 感應變數正規化
8. loop-deletion              → 刪除空迴圈
9. loop-unroll                → 展開（小 trip count）
10. instcombine               → 展開後的化簡
11. gvn                       → 冗餘消除
12. slp-vectorizer            → SLP 向量化
13. loop-vectorize            → 迴圈向量化
14. instcombine               → 向量化後的化簡
15. dce                       → 死碼消除
```

實際 pipeline 更複雜，這是概略順序。

```bash
# 觀察 LLVM O2 的完整 pass list
opt -O2 --print-pipeline-passes /tmp/test.ll -o /dev/null 2>&1 | head -50
```

## 優化的「相互干擾」

有些 pass 的效果會被之後的 pass 「吃掉」或反轉：

**LICM + GVN**：LICM 把不變式提到 preheader 後，GVN 可能發現 preheader 的計算和迴圈外已有的計算等價，把它也消除——LICM 做的工作部分被 GVN 吃掉，但整體來說是好的（preheader 的計算比迴圈內少跑了 n-1 次）。

**Loop Unrolling 破壞 LoopInfo**：展開後，原來的迴圈消失了（對全展開）或者結構變了（部分展開），`LoopAnalysis` 需要失效重算。LLVM 的 NPM 透過 `PreservedAnalyses` 自動處理這件事。

**InstCombine 的正規化干擾 PatternMatch**：InstCombine 把 `x * 2` 轉成 `x << 1`，之後的 pass 如果只匹配 `mul` 就找不到了——所以所有模式匹配都要同時匹配 `mul` 和等效的 `shl`，或者確保在 InstCombine 之後跑。

## 如何調試 Pipeline 問題

症狀：「打開 O2 有效果，但手動組合相同的 pass 沒有效果」

排查方法：

```bash
# 逐步觀察每個 pass 的效果
opt --print-after-all -passes="pass1,pass2,..." input.ll -o /dev/null 2>&1 | less

# 比較不同 pass 順序的輸出
opt -passes="licm,instcombine" input.ll | opt -passes="gvn" -S -o a.ll
opt -passes="instcombine,licm" input.ll | opt -passes="gvn" -S -o b.ll
diff a.ll b.ll
```

## 自我檢核

- [ ] 基礎前置：`mem2reg → loop-simplify → lcssa → indvars`
- [ ] InstCombine 在 LICM 之前（正規化），在展開之後（再化簡）
- [ ] 展開在向量化之前（SLP 需要顯式化的迭代）
- [ ] IndVarSimplify 在 SCEV 依賴的優化之前（trip count 精度）
- [ ] `--print-after-all` 和分段比較是調試 pipeline 的基本工具

→ [練習 B：用 opt 觀察 Loop Pass Pipeline](./practice-b-loop-analysis.md)
