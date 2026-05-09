# 練習 C — 用 Alive2 驗證 Peephole 規則

> 目標：把 Ch 13 和 Ch 18 學到的 Peephole 規則，在加入 LLVM 代碼之前用 Alive2 形式化驗證正確性。

## 任務規格

五條 Peephole 規則，每條需要：

1. 寫出 Source IR（優化前）和 Target IR（優化後）
2. 用 Alive2 線上工具（`alive2.llvm.org`）驗證
3. 記錄 Alive2 的結論（LGTM 或反例）
4. 如果有反例，分析錯在哪裡並修正規則

## 規則一：`x - x → 0`

```llvm
; Source
define i32 @src(i32 %x) {
  %r = sub i32 %x, %x
  ret i32 %r
}

; Target
define i32 @tgt(i32 %x) {
  ret i32 0
}
```

**預期**：LGTM（`x - x` 對任何 i32 都是 0，即使溢出，結果也是 0）。

**驗證**：貼到 `alive2.llvm.org`，記錄結果。

## 規則二：`(x + y) - y → x`（整數消去）

```llvm
; Source
define i32 @src(i32 %x, i32 %y) {
  %sum = add i32 %x, %y
  %r = sub i32 %sum, %y
  ret i32 %r
}

; Target
define i32 @tgt(i32 %x, i32 %y) {
  ret i32 %x
}
```

**預期**：LGTM（加減互消，不管溢出，模 2^32 下成立）。

## 規則三：`(x * 2) / 2 → x`（乘除互消？）

```llvm
; Source
define i32 @src(i32 %x) {
  %mul = mul i32 %x, 2
  %div = sdiv i32 %mul, 2
  ret i32 %div
}

; Target
define i32 @tgt(i32 %x) {
  ret i32 %x
}
```

**預期**：這條規則有問題。找出 Alive2 給的反例，解釋為什麼錯。

<details>
<summary>提示</summary>

考慮 `x = 0x40000000`（1073741824）：`x * 2 = 0x80000000`（溢出，變成 `-2147483648`），`-2147483648 / 2 = -1073741824 ≠ x`。

正確的條件：需要 `nsw`（no signed wrap）flag。

修正版 Source：
```llvm
%mul = mul nsw i32 %x, 2
%div = sdiv i32 %mul, 2
```

有了 `nsw`，Alive2 接受。

</details>

## 規則四：`(x >> 1) << 1 → x & ~1`

```llvm
; Source（無符號右移後左移）
define i32 @src(i32 %x) {
  %shr = lshr i32 %x, 1    ; logical right shift（無符號）
  %shl = shl i32 %shr, 1
  ret i32 %shl
}

; Target
define i32 @tgt(i32 %x) {
  %r = and i32 %x, -2       ; -2 = 0xFFFFFFFE = ~1
  ret i32 %r
}
```

**預期**：驗證這兩個操作是否等價。

## 規則五：`icmp eq (x + c1), c2 → icmp eq x, (c2 - c1)`

把「比較加法的結果和常數」轉換成「直接比較原始值」：

```llvm
; Source
define i1 @src(i32 %x) {
  %add = add i32 %x, 5
  %cmp = icmp eq i32 %add, 10
  ret i1 %cmp
}

; Target
define i1 @tgt(i32 %x) {
  %cmp = icmp eq i32 %x, 5    ; 10 - 5 = 5
  ret i1 %cmp
}
```

**預期**：驗證並記錄。注意：這條規則對有符號溢出是否安全？

<details>
<summary>分析</summary>

`x + 5 == 10` ⟺ `x == 5`，這在模 2^32 算術下是成立的（icmp eq 不管溢出）。

如果 `x + 5` 溢出（x 接近 INT_MAX），`%add` 的值還是那個溢出後的數，但 `icmp eq` 只看 bit pattern，和 `x == 5` 等價。

Alive2 應該接受。

</details>

## 加碼：自己設計一條規則並驗證

設計一條你認為正確的 Peephole 規則（不能是上面五條的變形），用 Alive2 驗證：

建議方向：
- 位運算（and/or/xor/not 的組合）
- 比較指令（icmp）的化簡
- cast 的消除（zext/sext/trunc）

```llvm
; 你的規則
; Source
define i32 @src(i32 %x) {
  ; ...
}

; Target  
define i32 @tgt(i32 %x) {
  ; ...
}
```

記錄：規則是什麼 → Alive2 結果 → 如果有反例，分析原因。

## 把驗證過的規則加入 Pass

選一條驗證通過的規則，加入 Ch 13 實作的 SimplePeepholePass：

```cpp
// 新增到 PracticeA/PracticeAPass.cpp 的 SimplePeepholePass 中

// 規則五：icmp eq (x + c), C → icmp eq x, (C - c)
{
    Value *X;
    const APInt *C1, *C2;
    if (match(I, m_ICmp(ICmpInst::ICMP_EQ,
                        m_Add(m_Value(X), m_APInt(C1)),
                        m_APInt(C2)))) {
        APInt NewC = *C2 - *C1;
        auto *NewCmp = new ICmpInst(I, ICmpInst::ICMP_EQ, X,
                                    ConstantInt::get(X->getType(), NewC));
        I->replaceAllUsesWith(NewCmp);
        I->eraseFromParent();
        Changed = true;
    }
}
```

寫 FileCheck 測試：

```llvm
; tests/peephole_test.ll
; RUN: opt -load-pass-plugin %plugin -passes="simple-dce,my-peephole" -S %s | FileCheck %s

define i1 @test_icmp_fold(i32 %x) {
; CHECK-LABEL: @test_icmp_fold
; CHECK: icmp eq i32 %x, 5
; CHECK-NOT: add
  %add = add i32 %x, 5
  %cmp = icmp eq i32 %add, 10
  ret i1 %cmp
}
```

## 自我檢核

- [ ] 完成五條規則的 Alive2 驗證，記錄每條的結論
- [ ] 規則三找到了反例（乘除互消需要 `nsw` 條件）
- [ ] 自己設計了一條規則並驗證（無論結果）
- [ ] 至少一條規則被加入 Pass 並有 FileCheck 測試

→ [Final Project：從零實作 Mini Optimizer](./final-project-mini-optimizer.md)
