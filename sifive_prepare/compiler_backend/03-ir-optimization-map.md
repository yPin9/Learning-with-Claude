# Ch 3 — IR optimization 地圖：GVN / LICM / InstCombine

> 目標：認識 LLVM 最重要的 10+ 個 IR-level optimization pass，知道每個在做什麼、在什麼情境下生效。這章**不深究實作**（那是讀 source 的工作），重點是建立「這類 code 該期待哪個 pass 處理」的直覺。

## Pass 家族分類

LLVM 的 IR-level pass 大致幾類：

```
Simplification:     mem2reg, sroa, instcombine, dce, adce
Redundancy:         gvn, newgvn, early-cse, licm
Control flow:       simplifycfg, jump-threading, tail-call-elim
Loop:               loop-unroll, loop-rotate, loop-vectorize, licm
Interprocedural:    inliner, ipsccp, function-attrs
Scalar Evolution:   indvars (induction variable simplify)
Memory:             memcpyopt, gvn-hoist, merged-load-store-motion
```

熱門 pass 數量有限。以下介紹最常碰到的。

## mem2reg / SROA

**問題**：C 的 local variable 預設用 `alloca` + `load`/`store`。IR 看起來：

```llvm
%a = alloca i32
store i32 42, ptr %a
%v = load i32, ptr %a
```

這在 memory，opt 難做 SSA 分析。

**mem2reg**：把「只有 `load` / `store` 存取的 alloca」升為 SSA register：

```llvm
%v = i32 42        ; 直接 SSA value
```

之後 optimization 順暢。

**SROA (Scalar Replacement of Aggregates)**：處理 struct / array：

```c
struct { int a; int b; } s;
s.a = 1; s.b = 2; return s.a + s.b;
```

SROA 把 `s` 拆成兩個 scalar，再用 mem2reg 升 SSA。

**幾乎所有優化都依賴這兩個先跑**。

## InstCombine — peephole 大師

最活躍的 pass。裡面有**上千條 rewrite rule**，例如：

```
x + 0     → x
x * 1     → x
x * 2     → x << 1
(x + c1) + c2 → x + (c1+c2)       ; const folding
x & 0xFF  → zext i8 (trunc i32 %x)  ; 窄化
!(!x)     → x
```

source: `llvm/lib/Transforms/InstCombine/`。

**InstCombine 跟 DAGCombiner 類似但在 IR 層**。Ch 5 會看 DAGCombiner。

## DCE / ADCE — 死碼消除

**DCE (Dead Code Elimination)**：砍掉沒 side effect 且結果沒人用的指令。

```llvm
%x = add i32 %a, %b     ; %x 沒人用，砍
ret i32 0
```

**ADCE (Aggressive DCE)**：假設所有 code 是 dead、只有有 side effect / 回傳路徑才 keep。反向證明。

兩者輔助用。現代 pipeline 很多其他 pass 也做 DCE。

## GVN — global value numbering

**問題**：相同 computation 出現多次：

```llvm
%a = add i32 %x, %y
...
%b = add i32 %x, %y      ; 跟 %a 一樣
```

**GVN** 發現 `%a == %b`，把第二個替換成第一個。

實作原理：對每個 SSA value 指定一個「value number」，相同計算得相同 number。跨 basic block 作用（global）。

**效果**：消除 redundant computation、合併 load、減少 register pressure。

新版 `NewGVN` 是重寫、理論上更完整，2026 尚未完全取代老 GVN。

## EarlyCSE — 輕量版 GVN

**Common Subexpression Elimination**：類似 GVN 但更輕量，只在 basic block 或 extended BB 做。

在 pipeline 前期跑，便宜且能消除明顯的 redundancy。

## LICM — loop invariant code motion

**問題**：迴圈內的某個計算每次結果相同。

```llvm
for (i in 0..N) {
    x = a + b;       // a, b 不變
    arr[i] = x;
}
```

**LICM** 把不變的計算提出 loop：

```llvm
x = a + b;           // 出 loop
for (i in 0..N) {
    arr[i] = x;
}
```

**省掉 N-1 次運算**。是迴圈優化的基本。

## Loop Unroll — 展開迴圈

```llvm
for (i in 0..4) arr[i] = i;
```

→

```llvm
arr[0] = 0;
arr[1] = 1;
arr[2] = 2;
arr[3] = 3;
```

**好處**：消除 loop overhead (branch, counter update)、給 scheduler 更多 parallel 機會、enable vectorization。

**壞處**：code 變大、可能 miss I-cache。trade-off。

預設 unroll factor 由 target cost model 決定。

## Loop Vectorize — 自動向量化

把 scalar loop 轉成 vector loop：

```c
for (int i = 0; i < N; i++) c[i] = a[i] + b[i];
```

變成（用 RVV vector）：

```
for i in 0..N step VL:
    vc = vadd.vv va[i..i+VL], vb[i..i+VL]
    store vc
```

條件：

- 無 iteration-carried dependency
- 記憶體 access pattern 能 vectorize
- Target 有 vector extension

RISC-V 的 RVV + LLVM auto-vectorizer 整合是近年 active 領域。Ch 15 深入。

## SimplifyCFG — 控制流簡化

```
if (a) goto x;
x: ...
```

→ 直接合併：

```
... (a 的 side effect) 
...
```

也處理 unreachable block、空 block、雙 branch 合併等。

## Jump Threading — 跨 branch 優化

某些 branch 的結果在編譯時可預測、可以「穿透」：

```
if (a) {
    b = 1;
    c = a;         // a 在 then 分支一定為 true
    if (c) ...     // 可以無條件進
}
```

Jump threading 可以 rewrite 跳過中間 branch。

## Tail Call Elimination

```c
int f(int n, int acc) {
    if (n == 0) return acc;
    return f(n-1, acc * n);      // tail call
}
```

沒有 TCE → stack overflow 對大 N。

**TCE** 把尾呼叫變成 branch：

```
loop:
    if n == 0: return acc
    acc = acc * n
    n = n - 1
    goto loop
```

## Inliner — function inlining

把小 function body 展開到 call site。

```c
static int add(int a, int b) { return a + b; }
int main() { return add(1, 2); }
```

→

```c
int main() { return 1 + 2; }   // 再被 InstCombine 折成 3
```

**Inliner 是 cross-function optimization 的入口**：一旦 inline 了，別的 pass 有更多資訊。

有 cost model：function 太大就不 inline。`alwaysinline`/`noinline` attribute 強制。

## IPSCCP — 全域常數傳播

**Interprocedural Sparse Conditional Constant Propagation**：跨 function 的常數傳播。

```c
static int foo(int x) { return x + 1; }
int main() { return foo(42); }   // always 43
```

IPSCCP 發現 `foo` 的 `x` 永遠是 42 → return 永遠是 43 → 主 function 簡化。

## Function Attrs — 自動推論 attribute

```c
int foo(int a) { return a * 2; }     // Pure function
```

`function-attrs` pass 能推論「foo 不讀 memory、不 throw、沒 side effect」→ 加 attribute：

```llvm
define i32 @foo(i32 %a) nounwind readnone ...
```

下游 opt 能基於這個 attribute 做更多優化。

## `-O2` 的實際 pipeline

看 `llvm/lib/Passes/PassBuilder.cpp` 的 `buildFunctionSimplificationPipeline`：

```cpp
// Simplified:
FPM.addPass(InstCombinePass());
FPM.addPass(SimplifyCFGPass());
FPM.addPass(EarlyCSEPass());
FPM.addPass(GVNPass());
FPM.addPass(SCCPPass());
FPM.addPass(InstCombinePass());   // 再跑一次！
FPM.addPass(LICMPass(LoopPM));
FPM.addPass(LoopUnrollPass());
...
```

很多 pass 跑多次。**這是 LLVM 設計的一個 idiom**：A 可能 enable B、B 改完 enable A → 來回幾次才收斂。

## 寫 IR optimization pass 的三個通則

1. **Use analysis，不要自己算**：需要 dominator tree？用 `DominatorTreeAnalysis`，別自己寫 DFS。
2. **Respect `PreservedAnalyses`**：改完 IR 要正確標示什麼被 invalidate。
3. **驗 corner case**：SSA 有很多 edge case（phi with single predecessor、undef、poison...），要處理。

## 實測：`-O0` vs `-O2`

```c
// fib.c
int fib(int n) {
    if (n < 2) return n;
    return fib(n-1) + fib(n-2);
}
```

`-O0` IR 有大量 alloca / load / store（C 變數）；`-O2` 後幾乎 pure SSA、很多 constant folded。

對 hot 函式 `-O2` 比 `-O0` 快 3-10 倍。這些 pass 的累積效應。

## RISC-V backend 依賴的前置 IR optimization

當 IR 進 SelectionDAG 前，已經經過 `-O2` pipeline。**Backend 假設這些優化都做完了**。所以：

- Backend 不重做 GVN
- Backend 不跑自動向量化（那是前端責任）
- Backend 只做 target-specific transformation

**但 backend 需要的 invariant**：IR 已經 legalized 到某個 "lower" 的層級。Ch 5 會講。

## 常見誤會

1. **「InstCombine 是小 pass」**：不。它 10000+ 行、處理成千 rule，LLVM 最大的 pass 之一。
2. **「GVN 永遠最佳」**：GVN 改多少也是 trade-off。大 function 的 GVN 慢且可能 miss；NewGVN 想 fix。
3. **「LTO 才做跨 function 優化」**：不。`-O2` 本身就做 inline + IPSCCP。LTO 擴大到跨 TU。
4. **「-O3 比 -O2 快很多」**：實測平均 +2%。多數收益在 -O2。-O3 開啟較激進但 questionable 的優化。
5. **「我可以 skip 所有 IR optimization 直接 backend」**：技術上可以（`-O0`），但 code 極差。backend 不會補救。

## 動手練習

1. 寫一個有明顯 redundant computation 的 C code，`-O0` vs `-O2` 比較 IR，看哪個 pass 消除。
2. 用 `opt -passes=mem2reg,licm -S` 手動跑這兩個 pass，觀察 loop-invariant 移出。
3. 寫一個有 tail call 的遞迴 function，看 `-O2` 有沒有 tail-call 優化。
4. 故意寫個會溢位的 loop（大 N），看 `-O3` 有沒有 vectorize。
5. 挑 InstCombine source 的某個 rule（例：`visitAdd`），讀 20 行看它處理什麼 pattern。

## 自我檢核

- [ ] 我能列 10 個以上常用 IR pass 及其作用
- [ ] 我能解釋 mem2reg 為什麼是其他優化的前置
- [ ] 我知道 InstCombine vs DAGCombiner 在不同層次做 peephole
- [ ] 我能讀 `-O2` pipeline 知道 pass 執行順序
- [ ] 我知道 backend 預設 IR 已過 `-O2` pipeline

Part 1 結束。下一章進 Part 2，進入 backend 的 codegen pipeline 起點 —— SelectionDAG。

→ [Ch 4 SelectionDAG 總論](./04-selectiondag-overview.md)
