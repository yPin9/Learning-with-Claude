# Ch 31 — Undefined Behavior 在優化中的角色

> 目標：理解為什麼 UB 是優化的合法前提，掌握 LLVM 的 Poison Value 模型，以及最常見的 UB 驅動優化和它們的危險性。

## UB 是優化的「許可證」

C/C++ 標準定義了大量**未定義行為（Undefined Behavior，UB）**：有符號整數溢出、NULL 解引用、讀取未初始化變數、越界訪問等。

對這些行為，標準說「程式行為完全未定義」——不是「會崩潰」，而是**任何行為都是合法的**，包括讓程式「跑起來好像沒發生過一樣」。

這給了優化器一個前提：

> **假設 UB 不發生**。

如果 UB 發生了，程式已經是「任意行為」，優化器不需要保留語意。如果 UB 不發生，優化是安全的。

這個前提允許非常激進的優化。

## 例子：有符號整數溢出

```c
int f(int x) {
    return x + 1 > x;
}
```

如果 `x` 是 `INT_MAX`，`x + 1` 溢出，這是 UB。

優化器假設 UB 不發生，即 `x + 1` 不溢出。那麼 `x + 1 > x` 恆成立（因為 +1 後的結果更大）。

結果：`f(x)` 被優化成 `return 1`（恆真）。

這在某些情況下是正確的（x != INT_MAX 時），但如果 x = INT_MAX，實際行為不確定。GCC 和 Clang 都會做這個優化。

## 例子：嚴格別名

```c
int x = 42;
float *p = (float*)&x;   // 違反嚴格別名規則
*p = 1.0f;               // UB：float* 訪問 int 對象
return x;                // 優化器可以假設 x 沒有被改變（因為 UB）
```

編譯器假設 `float*` 和 `int*` 不別名，所以 `*p = 1.0f` 不影響 `x`，`return x` 返回 42。

實際上 `x` 的位元被修改了，但這是 UB，優化器不保留語意。

## LLVM 的 Poison Value

LLVM 用 `poison` 值（「有毒值」）模型來精確化 UB 的傳播：

```
poison：一個值，如果被使用（用於計算、解引用、分支），行為未定義
⊂ undef：undef 可以是任意值；poison 更危險，使用即 UB
```

LLVM 指令的 flags：

```llvm
%a = add nsw i32 %x, 1    ; nsw = no signed wrap
; 如果 %x + 1 溢出 → %a = poison（使用則 UB）

%b = getelementptr inbounds i32* %p, i32 %i  ; inbounds
; 如果越界 → %b = poison
```

這讓優化器可以：

1. 假設帶 `nsw` 的 add 不溢出（基於此做優化）
2. 如果使用了 poison 值，行為已是 UB，任何優化都合法

`freeze` 指令（LLVM 10+）可以把 poison/undef「凍結」成任意固定值，讓後續使用安全：

```llvm
%frozen = freeze i32 %potentially_poison
; %frozen 不再是 poison，可以安全使用（但值不確定）
```

## 常見的 UB 驅動優化

**1. SROA（Scalar Replacement of Aggregates）**

依賴「本地 alloca 不被取地址後傳出去」的假設（否則別名分析失效）。

**2. 迴圈感應變數優化**

```c
for (int i = 0; i < n; i++) { /* ... */ }
```

如果 `n > INT_MAX`，`i < n` 的迴圈終止依賴有符號整數溢出。優化器假設 `n` 不會讓 `i` 溢出，大膽地做感應變數優化。

**3. 指針算術**

`p + i`（`inbounds`）假設不越界，讓 GVN 能合并兩個「基址相同、偏移是常數差」的地址計算。

## UB 帶來的陷阱

以下是 C 程式員經常踩的坑：

```c
// 陷阱 1：有符號整數溢出
// 常見的「溢出前檢查」反而錯誤：
if (x + 1 < x) { /* overflow */ }  // UB：加法已溢出才觸發，但編譯器認為永不成立
// 正確：
if (x == INT_MAX) { /* overflow */ }

// 陷阱 2：NULL 解引用優化
// 如果編譯器看到 p->field，它可以假設 p != NULL（否則是 UB）
// 後面的 if (p != NULL) 可能被優化掉
void use(Obj *p) {
    p->field = 0;         // 這裡 p != NULL（否則 UB）
    if (p != NULL) { }    // 被優化成 if (true) { }
}

// 陷阱 3：死代碼 UB 傳染
int arr[4];
arr[10] = 1;  // UB（越界），即使後來不讀 arr[10]
// 編譯器可以「穿越」這個 UB 做優化，影響看起來不相關的代碼
```

## UBSan：讓 UB 可見

`-fsanitize=undefined` 在執行時捕捉 UB：

```bash
clang -fsanitize=undefined -g /tmp/ub_test.c -o ub_test
./ub_test
# 輸出：runtime error: signed integer overflow: 2147483647 + 1 cannot be represented in type 'int'
```

在開發和測試時強烈建議開啟，production build 再關掉（有性能開銷）。

## 自我檢核

- [ ] UB 是優化的「許可證」：假設 UB 不發生，基於此做優化
- [ ] `nsw`/`nuw`/`inbounds` flag 在 LLVM IR 中的含義
- [ ] Poison value vs undef：poison 更危險，使用即 UB
- [ ] `freeze` 指令：把 poison 固化為某個任意值，後續安全使用
- [ ] 常見 UB 陷阱：有符號溢出、NULL 解引用假設、別名規則

→ [Ch 32 驗證與測試：Alive2、lit + FileCheck、CSmith](./32-testing-alive2.md)
