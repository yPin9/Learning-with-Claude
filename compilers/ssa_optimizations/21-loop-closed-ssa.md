# Ch 21 — Loop-Closed SSA Form

> 目標：理解為什麼普通 SSA 在迴圈優化中不方便，掌握 LCSSA 的轉換過程及其給迴圈 pass 帶來的好處。

## 問題：SSA 值跨越迴圈邊界

考慮一個簡單迴圈：

```llvm
entry:
  br loop_header

loop_header:
  %i = phi [0, entry], [%i_next, loop_latch]
  %cond = icmp slt i32 %i, %n
  br i1 %cond, loop_body, loop_exit

loop_body:
  %val = mul i32 %i, 2
  br loop_latch

loop_latch:
  %i_next = add i32 %i, 1
  br loop_header

loop_exit:
  ret i32 %val    ; 迴圈外使用了 %val（迴圈內的值）
```

`%val` 在迴圈內定義，在迴圈外被使用。對於迴圈優化 pass 來說，這讓「迴圈的值是否逃逸（escape）」的判斷很複雜。

## LCSSA（Loop-Closed SSA）

**LCSSA 的規則**：迴圈內定義的值，如果在迴圈外被使用，必須通過迴圈出口的 **phi 節點**傳遞。

轉換上面的例子：

```llvm
loop_exit:
  %val_lcssa = phi [%val, loop_body]  ; 新增 LCSSA phi
  ret i32 %val_lcssa
```

現在 `%val` 只在迴圈內使用，`%val_lcssa` 是它的「出口代表」。

LCSSA phi 有一個特徵：它**只有一個引數**（因為每個出口基本塊只從特定的迴圈內塊退出）。

## LCSSA 給迴圈優化帶來什麼

**好處 1：容易判斷值是否逃逸**

在 LCSSA 中，迴圈內的 SSA 值如果沒有出口 phi，就一定不逃逸。不需要掃描迴圈外的使用。

```cpp
// 非 LCSSA：需要遍歷所有 users，判斷哪些在迴圈外
for (User *U : V->users())
    if (!L->contains(cast<Instruction>(U)->getParent()))
        // 逃逸

// LCSSA：檢查出口基本塊的 phi 就夠了，邏輯更簡單
```

**好處 2：迴圈外提（hoisting）不改變結構**

LICM 把迴圈不變式提到 preheader 時，如果該值在迴圈外有 LCSSA phi，只需要把 phi 的引數指向 preheader，不需要在迴圈外插入新的定義。

**好處 3：迴圈刪除的正確性**

如果整個迴圈可以刪除（例如迴圈主體沒有副作用），LCSSA phi 自動說明了「迴圈的輸出值」，可以安全地用 undef 或者迴圈的初始值替換。

## LCSSA 轉換算法

```
對迴圈 L 中的每個定義 %v：
  對 %v 的每個在 L 外的使用 u：
    找到 u 所在基本塊 B_u 的前驅 B_pred（在迴圈出口上）
    如果 B_pred 的出口沒有 %v 的 LCSSA phi：
      在 B_pred 的後繼（迴圈出口）插入 %v_lcssa = phi [%v, B_pred]
    把 u 的這個使用改為 %v_lcssa
```

LCSSA 轉換後，迴圈的所有輸出都通過出口 phi 顯式表示。

## LLVM 中的 LCSSA

LLVM 的許多迴圈 pass 要求 LCSSA form 作為前提：

```cpp
// 如果迴圈不在 LCSSA form，先轉換
if (!L->isLCSSAForm(DT)) {
    formLCSSA(*L, DT, &LI, nullptr);
}
```

查詢迴圈是否已是 LCSSA form：

```cpp
L->isLCSSAForm(DT)  // 需要 DominatorTree
```

```bash
# 轉換成 LCSSA form
opt -passes="mem2reg,loop-simplify,lcssa" /tmp/loop.ll -o /tmp/lcssa.ll
cat /tmp/lcssa.ll   # 找出口基本塊的 phi（只有一個引數的）
```

## LCSSA phi 的識別

在 IR 中，LCSSA phi 的特徵是：它在迴圈出口基本塊，且只有一個引數（因為每個出口塊通常只有一條進入的迴圈邊）。

```llvm
loop_exit:
  %x_lcssa = phi [ %x, %loop_body ]    ; LCSSA phi，一個引數
  %y_lcssa = phi [ %y, %loop_body ]    ; 另一個 LCSSA phi
  ; 使用 %x_lcssa 和 %y_lcssa...
```

```cpp
// 判斷一個 phi 是否是 LCSSA phi
bool isLCSSAPhi(PHINode *PN, LoopInfo &LI) {
    BasicBlock *BB = PN->getParent();
    Loop *L = LI.getLoopFor(BB);
    // LCSSA phi：在迴圈外，且所有引數塊在某個迴圈內
    return !L && any_of(PN->blocks(), [&](BasicBlock *PredBB) {
        return LI.getLoopFor(PredBB) != nullptr;
    });
}
```

## LCSSA 和 Simplified Form 的配合

完整的迴圈優化準備流程：

```bash
opt -passes="loop-simplify,lcssa" input.ll -o prepared.ll
```

- `loop-simplify`：確保有唯一 preheader 和 latch
- `lcssa`：確保迴圈輸出通過出口 phi

這兩個 pass 通常在其他迴圈優化 pass 的依賴鏈中自動跑。

## 自我檢核

- [ ] LCSSA 規則：迴圈外使用迴圈內定義的值，必須通過出口 phi
- [ ] LCSSA phi 的特徵：只有一個引數（每個迴圈出口塊對應一條邊）
- [ ] LCSSA 讓「值逃逸判斷」從全局 DU 遍歷降到出口 phi 檢查
- [ ] `formLCSSA()` 的使用時機
- [ ] Loop Simplified Form + LCSSA 是大多數迴圈優化的前提條件

→ [Ch 22 LICM：Loop-Invariant Code Motion](./22-licm.md)
