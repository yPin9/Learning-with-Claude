# Ch 29 — 過程間常數傳播（IPCP）與函式特化

> 目標：理解 IPCP 如何跨越函式邊界傳播常數，掌握函式特化（Function Specialization）的機制，以及 LLVM IPSCCPPass 的工作原理。

## 函式邊界阻止了優化

考慮：

```c
int double_val(int x) {
    return x * 2;
}

int main() {
    return double_val(21);   // 呼叫時 x = 21（常數）
}
```

如果不看 `main`，SCCP 分析 `double_val` 只知道 x 是未知整數（⊤），無法化簡 `x * 2`。

如果把 `main` 一起分析，知道 `x = 21`，`x * 2 = 42`，整個呼叫可以替換成常數 42。

這就是**過程間常數傳播（IPCP）**。

## IPCP 的基本策略

**方法一：內聯後跑 SCCP**

把 `double_val(21)` 內聯到 `main` 後，SCCP 自然能把 `21 * 2 = 42` 算出來。這是 LLVM 最常用的做法。

**方法二：函式摘要（Summary-based IPCP）**

不內聯，但為每個函式建立「在哪些呼叫點，哪個參數是什麼值」的摘要，然後在函式內傳播。

```
呼叫點：double_val(21) → x = 21
在 double_val 的函式體內，用 21 替換 x → 計算 42 → 返回 42
把 main 中的呼叫替換成 42（dead code elimination）
```

## LLVM 的 IPSCCPPass

`IPSCCPPass`（Interprocedural SCCP）是 LLVM 過程間優化的核心，結合了：

- **Ch 10 的 SCCP**（函式內的稀疏條件常數傳播）
- **Call graph 的傳播**（跨函式傳遞常數資訊）

工作方式（簡化）：

```
1. 從 main（或所有外部可見函式）開始
2. 在 call graph 上做「過程間 SCCP」：
   - 遇到呼叫點 f(c) where c 是常數：把 c 傳給 f 的對應參數
   - 在 f 的函式體內跑 SCCP，使用傳入的常數
   - f 的返回值格值傳回到呼叫點
3. 不動點迭代，直到沒有更多常數可以傳播
```

```bash
# 觀察 IPSCCP 的效果
cat > /tmp/ipsccp_test.c << 'EOF'
static int config_value() { return 42; }  // 返回常數

int process(int x) {
    if (x > 100) return x * 2;    // config_value() 不會 > 100
    return x + 1;
}

int main() {
    return process(config_value());  // 傳入 42
}
EOF

clang -O0 -S -emit-llvm /tmp/ipsccp_test.c -o /tmp/ipsccp_in.ll
opt -S -passes="ipsccp,dce" /tmp/ipsccp_in.ll -o /tmp/ipsccp_out.ll
cat /tmp/ipsccp_out.ll  # main 應該直接返回 43
```

## 函式特化（Function Specialization）

內聯和 IPCP 都要求在**呼叫點**已知常數。如果一個函式被呼叫很多次，每次都用不同常數，全部內聯代碼膨脹太大。

**函式特化**：為特定的常數參數創建一個函式的「特化版本」：

```c
int process(int mode, int x) {
    if (mode == 0) return x + 1;
    if (mode == 1) return x * 2;
    return x;
}

// 如果 mode=1 的呼叫很頻繁，創建特化版本：
static int process_mode1(int x) {
    return x * 2;   // mode=1 已化簡
}
```

特化後，`mode=1` 的呼叫點用 `process_mode1`，既得到了常數傳播的效果，又避免了大量內聯的代碼膨脹。

LLVM 17+ 的 `FunctionSpecializationPass` 做這件事（相對較新，還在演化中）。

## Dead Argument Elimination

過程間分析的另一個應用：如果一個函式的某個參數**在所有呼叫點都從未被使用**，或者**對函式行為沒有影響**，這個參數可以刪除。

```c
// g 的第二個參數從來沒被用到
void g(int x, int y) { use(x); }

// 所有呼叫點
g(1, 2);
g(3, 4);
// 可以把 y 參數刪除，呼叫點也省一個參數
```

LLVM 的 `DeadArgumentEliminationPass`（`deadargelim`）做這件事，在 IPSCCP 之後跑。

## IPCP 的限制

```
1. 外部可見函式（external linkage）不能特化，因為其他編譯單元可能用不同的參數呼叫
   → 用 static（internal linkage）的函式才能做激進的 IPCP
   
2. 函式指針：間接呼叫的目標未知，保守處理
   
3. 遞迴函式：參數值無限傳播，最終升到 ⊤
```

這也是 LTO（Ch 30）的動機：讓整個程式的所有函式都在同一個模組可見，消除 external linkage 的限制。

## 自我檢核

- [ ] IPCP 的兩種做法：內聯後 SCCP，vs 函式摘要直接傳播
- [ ] IPSCCPPass：過程間 SCCP + call graph 不動點迭代
- [ ] 函式特化：為頻繁使用的常數參數創建特化版本，避免內聯膨脹
- [ ] Dead Argument Elimination：所有呼叫點都不用的參數可以刪除
- [ ] IPCP 的限制：external linkage、間接呼叫、遞迴

→ [Ch 30 LTO：連結時優化](./30-lto.md)
