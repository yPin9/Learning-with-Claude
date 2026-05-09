# Ch 16 — 複製傳播（Copy Propagation）

> 目標：理解 SSA 下複製傳播的退化形式，掌握 phi-of-identical-values 的消除，以及 copy coalescing 的角色。

## Copy Propagation 的定義

**複製傳播**：如果有 `x = y`（一個純複製），那麼後續所有使用 `x` 的地方都可以直接用 `y`，然後刪除這條 copy。

```c
int x = y;        // copy
int result = x * 2; // 用 x
→
int result = y * 2; // 直接用 y
// x = y 現在變成死碼，可以刪除
```

在 **非 SSA** 中，複製傳播需要到達定義分析：確認從 `x = y` 到使用 `x` 的所有路徑上，x 和 y 都沒有被重新定義。

在 **SSA** 中，`x = y` 這樣的純複製極少出現——因為 SSA 保證每個名字只有一個定義，x 和 y 本來就有不同的版本號。但有幾種情況仍然相關。

## SSA 中的 Copy：Bitcast 和 Cast

在 SSA IR 中，「純複製」通常以類型轉換的形式出現：

```llvm
%y = bitcast i8* %x to i32*   ; 純位模式轉換，沒有計算
%z = add i32 %y_val, 0        ; 加 0，等於複製
%w = or i32 %val, 0           ; or 0，等於複製
```

對這些「無效指令」，InstCombine（Ch 18）會把它們替換成操作數本身。

## Phi-of-Identical-Values

SSA 中最重要的複製傳播場景：**所有引數值相同的 phi**。

```llvm
%x = phi [%a, B1], [%a, B2], [%a, B3]
; 不管從哪條邊來，值都是 %a
; 這個 phi 等價於 %x = %a（複製）
; 可以直接用 %a 替換所有 %x 的使用
```

這種情況在多輪優化後很常見：各分支的計算被化簡成同一個值，phi 就變成了冗餘。

GVN 會處理這種情況（phi 的所有引數值編號相同 → 替換）。

## SSA Copy Coalescing

更重要的「複製傳播」發生在**後端**：暫存器分配時。

SSA phi 節點的語意是：「phi 的結果和引數是同一個值，只是名字不同」。暫存器分配時，如果能把 phi 的引數和結果分配到**同一個物理暫存器**，就不需要插入 copy 指令。

這叫 **Copy Coalescing（複製合併）**：

```
%x3 = phi [%x1, B1], [%x2, B2]

暫存器分配前：x1 → r1, x2 → r2, x3 → r3（需要 copy: r3=r1 和 r3=r2）
Copy coalescing 後：如果可以讓 x1, x2, x3 都用 r1 → 不需要 copy
```

干擾：如果 x1 和 x3 同時活躍（在 phi 所在塊的某個後繼中，x1 還在用），就無法 coalesce（因為 r1 被兩個值用到了）。

LLVM 的 `RegisterCoalescer` pass 在暫存器分配前做這件事。

## 更廣義的 Copy：ssa-copy 指令

某些情況下，pass 主動插入 copy（`llvm.ssa.copy` intrinsic）來攜帶額外資訊（比如 poison status、known bits）：

```llvm
%x_copy = call i32 @llvm.ssa.copy.i32(i32 %x) ; 創建 %x 的「別名」
```

這讓後續分析可以附加額外屬性到 `%x_copy` 上，而不影響 `%x` 本身。

## 在 LLVM Pass 中做 Copy Propagation

對 SSA IR 來說，「複製傳播」的實際工作很少需要專門的 pass——大部分已由以下機制自動處理：

```
InstCombine：消除 bitcast、add 0、or 0 等
GVN：合併值相同的 phi，消除冗餘計算
DCE：複製傳播後，源 copy 的 use count 降到 0，自動被 DCE 刪除
RegisterCoalescer：暫存器分配時做 phi 的 copy coalescing
```

如果你要在自己的 pass 中做類似的事：

```cpp
// 找出所有「只有一個使用者的 bitcast」，直接傳播
for (auto &BB : F) {
    for (auto &I : BB) {
        if (auto *BC = dyn_cast<BitCastInst>(&I)) {
            if (BC->getSrcTy() == BC->getDestTy()) {
                // 身份 bitcast，替換所有使用
                BC->replaceAllUsesWith(BC->getOperand(0));
                // 標記待刪除
            }
        }
    }
}
```

## 自我檢核

- [ ] 非 SSA 的 copy propagation 需要到達定義分析；SSA 下幾乎不需要
- [ ] Phi-of-identical-values：引數全相同的 phi 直接替換為引數值
- [ ] Copy coalescing：暫存器分配時讓 phi 引數和結果共用同一物理暫存器
- [ ] LLVM 中 copy propagation 的工作分散在 InstCombine、GVN、RegisterCoalescer

→ [Ch 17 強度削減（Strength Reduction）](./17-strength-reduction.md)
