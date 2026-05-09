# Ch 18 — InstCombine：LLVM 的 Peephole 引擎

> 目標：理解 InstCombine 的架構和設計哲學，掌握如何新增一條 InstCombine 規則，以及 LLVM 的 Pattern Match 系統。

## InstCombine 是什麼

InstCombine 是 LLVM 中規模最大的單個 pass，包含數千條 Peephole 規則。它的職責不只是「讓代碼更快」，更重要的是**正規化**：把所有等價的 IR 形式統一成一種標準形式，讓其他 pass 更容易識別模式。

```
InstCombine 做的事：
  常數折疊              (3 + 4 → 7)
  代數化簡              (x * 1 → x, x + 0 → x)
  正規化                (1 + x → x + 1，常數往右)
  強度削減              (x * 8 → x << 3)
  Cast 消除             (zext(trunc(x)) → x，如果範圍允許)
  冗餘比較消除           (x != 0 → true，如果已知 x ≠ 0)
  位運算化簡            (x & ~x → 0)
  ...
```

## 設計原則

**原則 1：只做局部優化**

InstCombine 只看「一條指令及其操作數」，不做全局分析（不走 CFG 邊）。這讓它快速且安全。

全局分析由 GVN、SCCP、LICM 等 pass 負責。

**原則 2：結果不能更複雜**

每條規則的輸出必須比輸入簡單（指令更少、值更小）。不能為了某種形式而增加指令數。

**原則 3：正規化方向固定**

所有正規化都是單向的：`1 + x` 永遠轉成 `x + 1`，不反過來。這讓 IR 有唯一的標準形式。

## 架構

InstCombine 在每個基本塊中反覆跑，直到沒有指令被修改：

```
worklist = 所有指令
while worklist 不空：
    I = 取出
    result = visitInstruction(I)  // 嘗試所有匹配 I 的規則
    if result != nullptr:
        replace I with result
        把 I 的使用者加回 worklist（它們的操作數改了）
```

每類指令有一個 `visitXxx` 方法：

```cpp
visitAdd(BinaryOperator &I)   → 處理 add 指令的所有規則
visitMul(BinaryOperator &I)   → 乘法規則
visitICmp(ICmpInst &I)        → 整數比較規則
visitBitCast(BitCastInst &I)  → bitcast 規則
...
```

## PatternMatch API

Ch 13 已看過基礎。更多進階模式：

```cpp
// 匹配任意整數常數，捕獲到 C
ConstantInt *C;
match(V, m_ConstantInt(C));

// 匹配 add 或 sub
match(V, m_BinOp(m_Value(X), m_Value(Y)));

// 匹配 icmp eq 或 ne
ICmpInst::Predicate Pred;
match(V, m_ICmp(Pred, m_Value(X), m_Value(Y)));
if (Pred == ICmpInst::ICMP_EQ) ...

// 匹配符號擴展
match(V, m_SExt(m_Value(X)));

// 匹配 demorgan：~(a & b) = ~a | ~b
match(V, m_Not(m_And(m_Value(A), m_Value(B))));

// 交換匹配（add 是交換的）
match(V, m_c_Add(m_Value(X), m_ConstantInt(C)));
// 無論常數在左還是右都能匹配
```

## 新增一條規則

假設我們要加入規則：`x & (x - 1) == 0` → `isPowerOfTwo(x)`。

實際上要加的規則更簡單，以 `(x & x) → x`（AND 冪等律）為例：

```cpp
// 在 llvm/lib/Transforms/InstCombine/InstCombineAndOrXor.cpp
// visitAnd() 方法中新增：

// x & x → x（同一個值 AND 自身）
{
    Value *X, *Y;
    if (match(&I, m_And(m_Value(X), m_Value(Y))) && X == Y) {
        return replaceInstUsesWith(I, X);
    }
}
```

`replaceInstUsesWith(I, X)` 把所有使用 `I` 的地方換成 `X`，並標記 `I` 待刪除。

### 一個真實例子：`(~x) & x → 0`

```cpp
// 在 visitAnd 中
{
    Value *X;
    // 匹配 ~x（實作為 xor x, -1）
    if (match(&I, m_c_And(m_Not(m_Value(X)), m_Specific(X)))) {
        return replaceInstUsesWith(I,
            ConstantInt::get(I.getType(), 0));
    }
}
```

`m_c_And` 是交換版（c = commutative），`m_Not` 匹配 `xor V, -1`，`m_Specific(X)` 匹配特定的值 X（保證兩個操作數是同一個 Value）。

## KnownBits：值域資訊

InstCombine 不只做模式匹配，還會查詢「這個值有哪些 bit 是已知的」。

```cpp
KnownBits Known = computeKnownBits(V, DL);
// Known.Zero：哪些 bit 確定是 0
// Known.One：哪些 bit 確定是 1
```

例如：

```llvm
%x = and i32 %a, 0xFF   ; 高 24 bit 確定是 0
%y = zext i8 %b to i32  ; 高 24 bit 確定是 0
%z = add i32 %x, %y     ; 不可能溢出到 bit 16 以上
```

KnownBits 讓 InstCombine 能做更強的化簡：

```
zext(trunc(x to i8)) to i32
→ 如果已知 x 的高 24 bit 是 0，直接是 x（不需要 trunc+zext）
```

## 測試 InstCombine 規則

LLVM 用 FileCheck 測試每條規則：

```llvm
; test/Transforms/InstCombine/and-or-xor.ll

; CHECK-LABEL: @test_and_idem(
; CHECK-NEXT:    ret i32 %x
define i32 @test_and_idem(i32 %x) {
  %r = and i32 %x, %x
  ret i32 %r
}
```

用 `opt -passes=instcombine` 跑，然後用 FileCheck 驗證輸出符合預期。Ch 32 詳細介紹 FileCheck 語法。

## 查看 InstCombine 的所有規則

```bash
wc -l llvm/lib/Transforms/InstCombine/*.cpp
# 通常 20000+ 行，是 LLVM 最大的單個 pass
ls llvm/lib/Transforms/InstCombine/
# InstCombineAddSub.cpp, InstCombineMulDivRem.cpp,
# InstCombineAndOrXor.cpp, InstCombineCasts.cpp, ...
```

每個文件對應一類指令，找到對應文件就能看到所有規則。

## 自我檢核

- [ ] InstCombine 的兩個職責：優化 + 正規化（統一 IR 形式）
- [ ] 設計原則：只看局部，輸出不比輸入複雜，正規化單向
- [ ] PatternMatch：`m_c_Add`（交換）、`m_Not`、`m_Specific` 的用法
- [ ] KnownBits：攜帶「哪些 bit 確定是 0/1」的值域資訊
- [ ] 能寫一個完整的 InstCombine 規則（match + replaceInstUsesWith）

→ [Ch 19 New PassManager 架構](./19-new-pass-manager.md)
