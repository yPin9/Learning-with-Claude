# Ch 11 — 別名分析基礎（Alias Analysis）

> 目標：理解別名分析解決什麼問題，掌握 MustAlias/MayAlias/NoAlias 的語意，以及類型別名分析（TBAA）的工作原理。

## 問題：指針讓 SSA 不夠用

SSA 完美解決了純量變數的 use-def 問題，但有一類情況 SSA 幫不了：

```c
void f(int *p, int *q) {
    *p = 1;
    *q = 2;
    return *p;   // 這裡 *p 的值是 1 還是 2？
}
```

如果 `p == q`（兩個指針指向同一位置，即 alias），`*q = 2` 也改了 `*p`，`return *p` 應該返回 2。

如果 `p != q`，`*q = 2` 不影響 `*p`，應該返回 1。

不知道 p 和 q 是否別名，優化器就無法：

- 把 `*p = 1` 和 `return *p` 合在一起（冗餘 load 消除）
- 重排兩個 store 的順序
- 做 LICM（把 load 提到迴圈外）

**別名分析（Alias Analysis）** 的任務：判斷兩個記憶體訪問是否可能指向同一位置。

## 三種回答

別名分析對「A 和 B 是否別名」給出三種答案：

```
MustAlias（必定別名）：A 和 B 在每次執行時都指向同一位置
MayAlias（可能別名）：A 和 B 可能指向同一位置（保守答案）
NoAlias（一定不別名）：A 和 B 不可能指向同一位置
```

**安全性原則**：不確定時返回 MayAlias（寧可保守，不做優化）。只有確定 NoAlias 才能做相關的優化。

## 基本的 NoAlias 情況

不需要複雜分析就能確定 NoAlias 的情況：

**不同類型（TBAA）**：

```c
int *p;
float *q;
*p = 1;
*q = 2.0;
// 根據 C 嚴格別名規則（strict aliasing），int* 和 float* 不能別名
// 優化器可以確定 NoAlias
```

**不同 alloca**：

```c
int a, b;
int *p = &a;
int *q = &b;
// a 和 b 是不同的 alloca，NoAlias
```

**全局變數 vs 堆分配**：

```c
int g;
int *p = malloc(sizeof(int));
// &g 和 p 不會別名（除非有取址後傳遞的情況）
```

## 類型別名分析（TBAA）

C 和 C++ 的**嚴格別名規則（Strict Aliasing Rule）**：

> 只有相同類型的指針（或 char*）才可能別名。

這讓編譯器可以假設：`int*` 和 `float*` 不會指向同一位置，除非其中一個是 `char*`。

LLVM 把這個資訊附加到 load/store 指令的 metadata 上：

```llvm
store i32 1, i32* %p, !tbaa !0
store float 2.0, float* %q, !tbaa !1
```

`!tbaa` metadata 說明這個訪問的類型，如果兩個訪問的 TBAA node 不相容，LLVM 知道它們 NoAlias。

```bash
# 生成帶 TBAA 的 IR
clang -O2 -S -emit-llvm /tmp/alias_test.c -o /tmp/alias_tbaa.ll
grep tbaa /tmp/alias_tbaa.ll  # 找 TBAA metadata
```

## 指向分析（Points-to Analysis）

更強的別名分析：追蹤每個指針可能指向哪些記憶體位置。

**Andersen 分析**（流不敏感）：

```
對每個賦值語句建立約束：
  p = &a   → a ∈ pts(p)
  p = q    → pts(q) ⊆ pts(p)
  p = *q   → ∀ v ∈ pts(q): pts(v) ⊆ pts(p)
  *p = q   → ∀ v ∈ pts(p): pts(q) ⊆ pts(v)

求解約束（不動點）得到每個指針的可能指向集合
```

如果 `pts(p) ∩ pts(q) = {}`，則 NoAlias。

**代價**：Andersen 分析的複雜度是 O(n³)，對大程式要做近似。

**LLVM 的做法**：LLVM 有多個別名分析 pass 組成的「AA Pipeline」，從簡單到複雜：

```
BasicAA：基於 TBAA、alloca 不同、函式引數 restrict 的快速判斷
ScopedNoAliasAA：LLVM 的 region-based noalias
TypeBasedAA：完整的 TBAA
GlobalsAA：分析全局變數的別名
```

## AliasResult 的 LLVM API

```cpp
#include "llvm/Analysis/AliasAnalysis.h"

// 在 pass 中查詢別名
AliasAnalysis &AA = FAM.getResult<AAManager>(F);

Value *P = ...;  // 指針 1
Value *Q = ...;  // 指針 2

AliasResult AR = AA.alias(
    MemoryLocation::getForLoad(LoadI1),
    MemoryLocation::getForLoad(LoadI2)
);

if (AR == AliasResult::NoAlias) {
    // 確定不別名，可以做優化
} else if (AR == AliasResult::MustAlias) {
    // 確定是同一位置
}
```

```bash
# 觀察 LLVM 的 alias analysis 結果
opt -passes="print<aa-eval>" /tmp/alias_test.ll -o /dev/null 2>&1
```

## 別名分析的限制

別名分析**永遠是保守的**——它可能說 MayAlias，但實際不是；它絕對不會說 NoAlias，但實際上是別名。

這是正確性的基本要求：寧可錯過優化機會，不能做出錯誤優化。

## 自我檢核

- [ ] 為什麼純量 SSA 不能處理指針別名問題
- [ ] MustAlias / MayAlias / NoAlias 的語意和安全方向
- [ ] TBAA 的原理：嚴格別名規則 + load/store metadata
- [ ] LLVM AA Pipeline 的多層設計
- [ ] `AA.alias()` 的 API 用法

→ [Ch 12 MemorySSA](./12-memory-ssa.md)
