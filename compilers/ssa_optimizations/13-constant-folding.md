# Ch 13 — 常數折疊（Constant Folding）與 Peephole

> 目標：掌握編譯時求值的完整規則集，理解 LLVM 的正規化（canonicalization）策略，以及如何寫一個 Peephole 規則。

## 常數折疊

**常數折疊（Constant Folding）**：在編譯期計算「操作數全是常數」的運算式。

```
2 + 3         → 5
true && false → false
(int)3.14     → 3
sizeof(int)   → 4
```

這是最簡單的優化，但效果顯著——C 模板、內聯函式展開後往往留下大量常數運算。

LLVM 的常數折疊在 `ConstantFoldInstruction()` 函式中集中實作，也在 IRBuilder 的每個 `Create*` 方法中按需調用（建立指令時如果操作數都是常數，直接返回 ConstantInt 而不是指令）。

```cpp
// LLVM：建立指令時自動折疊常數
IRBuilder<> Builder(BB);
Value *C1 = ConstantInt::get(Type::getInt32Ty(Ctx), 3);
Value *C2 = ConstantInt::get(Type::getInt32Ty(Ctx), 4);
Value *Result = Builder.CreateAdd(C1, C2);
// Result 直接是 ConstantInt(7)，不是 add 指令
```

## 代數恆等式

除了純常數折疊，還可以利用代數性質：

```
x + 0 = x          （加法零元）
x * 1 = x          （乘法單位元）
x * 0 = 0          （乘法吸收元）
x - x = 0
x | 0 = x
x & ~0 = x         （~0 是全 1）
x ^ x = 0          （XOR 自消）
x >> 0 = x
x << 0 = x
```

整數溢出規則：有符號整數的溢出是 UB，所以 `x + 1 > x` 可以優化成 `true`（假設 x 不是 INT_MAX）。無符號整數環繞，規則不同。

## 正規化（Canonicalization）

正規化的目標是把「語義等價的多種寫法」統一成一種標準形式，讓後續 pass 更容易識別模式。

**原則**：把「複雜形式」化簡成「標準形式」，不一定讓代碼更快，但讓後續優化更容易。

```
常數挪到右側：
  1 + x  →  x + 1    （加法可交換，常數往右）
  1 * x  →  x        （1 是乘法單位元）

比較正規化：
  x != y  →  !(x == y)
  x >= y  →  !(x < y)

減法轉加法：
  x - y  →  x + (-y)   （在某些分析中更方便）
```

LLVM 的 `InstCombine` 大量做正規化，目的是讓 `GVN`、`LICM` 等 pass 能匹配到更多模式。

## Peephole 優化

**Peephole 優化**：對一小塊相鄰指令（「小窗口」）做模式匹配和替換。

不需要全局分析，只需要看局部幾條指令。

### 常見 Peephole 模式

**雙重否定消除**：

```llvm
%a = xor i1 %x, true    ; a = !x
%b = xor i1 %a, true    ; b = !a = !!x = x
; 優化成：直接用 %x
```

**比較折疊**：

```llvm
%cmp1 = icmp eq i32 %x, %y
%cmp2 = icmp eq i32 %y, %x
; 等價，可以替換成同一個值
```

**加法轉移位**：

```llvm
%mul = mul i32 %x, 8    ; x * 8 = x << 3
; 優化成：
%shift = shl i32 %x, 3
```

**連續 cast 消除**：

```llvm
%trunc = trunc i32 %x to i8     ; 32→8 bit
%zext  = zext i8 %trunc to i32  ; 8→32 bit（零擴展）
; 如果原始值在 [0, 255] 範圍內，兩步可以合成一步
```

## 寫一個 Peephole Pass

```cpp
#include "llvm/IR/PassManager.h"
#include "llvm/IR/PatternMatch.h"

using namespace llvm;
using namespace llvm::PatternMatch;

struct SimplePeepholePass : PassInfoMixin<SimplePeepholePass> {
    PreservedAnalyses run(Function &F, FunctionAnalysisManager &) {
        bool Changed = false;
        
        for (auto &BB : F) {
            for (auto It = BB.begin(); It != BB.end(); ) {
                Instruction *I = &*It++;
                
                // 模式：x * 0 → 0
                Value *X;
                if (match(I, m_Mul(m_Value(X), m_Zero()))) {
                    I->replaceAllUsesWith(
                        ConstantInt::get(I->getType(), 0));
                    I->eraseFromParent();
                    Changed = true;
                    continue;
                }
                
                // 模式：x + 0 → x
                if (match(I, m_Add(m_Value(X), m_Zero()))) {
                    I->replaceAllUsesWith(X);
                    I->eraseFromParent();
                    Changed = true;
                    continue;
                }
                
                // 模式：x ^ x → 0
                Value *Y;
                if (match(I, m_Xor(m_Value(X), m_Value(Y))) && X == Y) {
                    I->replaceAllUsesWith(
                        ConstantInt::get(I->getType(), 0));
                    I->eraseFromParent();
                    Changed = true;
                    continue;
                }
            }
        }
        
        return Changed ? PreservedAnalyses::none()
                       : PreservedAnalyses::all();
    }
};
```

`PatternMatch` 是 LLVM 提供的 DSL，讓模式匹配更易讀：

```cpp
m_Value(X)     // 匹配任意值，捕獲到 X
m_Zero()       // 匹配常數 0
m_Add(a, b)    // 匹配 add 指令，操作數匹配 a 和 b
m_Mul(a, b)    // 匹配 mul 指令
m_ICmp(Pred, a, b)  // 匹配整數比較
```

## 常數折疊 vs Peephole vs InstCombine

```
常數折疊：操作數全是常數，直接求值
Peephole：局部模式匹配，不需要全局信息
InstCombine：LLVM 的「大 Peephole」，結合常數折疊、正規化、代數化簡
```

三者在 LLVM 中的關係：InstCombine 調用常數折疊，自己做 Peephole。Ch 18 詳細看 InstCombine 的架構。

## 自我檢核

- [ ] 常數折疊：操作數都是常數 → 編譯期求值
- [ ] 正規化的目的：統一等價形式，方便後續 pass 匹配
- [ ] PatternMatch API：`m_Add(m_Value(X), m_Zero())` 等語法
- [ ] `replaceAllUsesWith` + `eraseFromParent` 是 pass 修改指令的標準流程

→ [Ch 14 死碼消除（DCE 與 ADCE）](./14-dce.md)
