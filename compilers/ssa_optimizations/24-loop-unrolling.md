# Ch 24 — 迴圈展開（Loop Unrolling）

> 目標：理解全展開與部分展開的差異，掌握 trip count 在展開決策中的作用，以及 remainder loop 的處理。

## 為什麼要展開迴圈

迴圈展開的目標：

```
1. 減少分支開銷（loop counter update + branch 每次迭代都有）
2. 暴露更多 ILP（指令級並行）給 CPU 流水線
3. 讓向量化有更多資料可以打包
4. 減少 loop overhead 相對於計算的比例
```

```c
// 原始
for (int i = 0; i < 4; i++)
    a[i] = b[i] + c[i];

// 展開 4 次（全展開）
a[0] = b[0] + c[0];
a[1] = b[1] + c[1];
a[2] = b[2] + c[2];
a[3] = b[3] + c[3];
```

## 全展開（Full Unrolling）

**適用條件**：trip count 是**靜態已知**的常數（通常很小）。

```c
for (int i = 0; i < 3; i++)   // trip count = 3，靜態已知
    a[i] = 0;
```

LLVM 會把這個迴圈完全消除，展開成三條 store 指令。

**SCEV 的角色**：SCEV 計算 trip count（Ch 23），如果是常數且足夠小，全展開。

閾值（LLVM 默認）：

```
trip count ≤ unroll-threshold（默認約 150 個指令）才全展開
```

## 部分展開（Partial Unrolling）

trip count 不是靜態常數，或太大不值得全展開，但可以做部分展開：

```c
// 原始（n 不確定）
for (int i = 0; i < n; i++)
    sum += a[i];

// 展開 4 次（unroll factor = 4）
int i;
for (i = 0; i < n - 3; i += 4) {   // 主迴圈：每次處理 4 個
    sum += a[i];
    sum += a[i+1];
    sum += a[i+2];
    sum += a[i+3];
}
for (; i < n; i++)                  // remainder 迴圈：處理剩餘的 n%4 個
    sum += a[i];
```

展開因子（Unroll Factor）= 4：每次迭代處理 4 個元素，迴圈次數減少到 n/4。

### Remainder Loop

部分展開後，如果 `n` 不是展開因子的倍數，需要一個 remainder loop 處理剩餘的迭代。

如果 trip count 的奇偶性已知（例如 SCEV 告訴我們 n 是 4 的倍數），可以省去 remainder loop。

## 展開的代價

展開不是免費的：

```
1. 代碼膨脹（code size 增加）：每個展開的拷貝都佔 icache
2. 暫存器壓力增加（展開後的迭代並發，需要更多暫存器同時 live）
3. 如果展開因子太大，反而因 icache miss 變慢
```

LLVM 的展開決策基於 **cost model**：

```
估計展開後的指令數（是否超過 unroll-threshold）
估計展開後的 register pressure
考慮 trip count（小的 trip count 更值得展開）
```

## LLVM 中的 Loop Unrolling

```bash
# 強制展開
opt -passes="loop-unroll" \
    -unroll-count=4 \            # 展開因子
    -unroll-allow-partial \      # 允許部分展開
    /tmp/loop.ll -o /tmp/unrolled.ll

# 觀察效果
cat /tmp/unrolled.ll
```

在 C 代碼中也可以用 pragma 控制：

```c
#pragma clang loop unroll(full)          // 要求全展開
for (int i = 0; i < 4; i++) { ... }

#pragma clang loop unroll_count(4)       // 指定展開因子
for (int i = 0; i < n; i++) { ... }

#pragma clang loop unroll(disable)       // 禁止展開
for (...) { ... }
```

## 展開後的優化機會

展開最大的價值往往不是「減少分支」，而是**暴露優化機會**：

```c
// 展開後，相鄰迭代的計算可以重排
sum += a[i] + a[i+1] + a[i+2] + a[i+3];
// 編譯器可以用 SIMD 指令一次處理 4 個加法
```

迴圈展開 + 向量化（Ch 25）是非常強的組合：展開讓向量化有足夠的元素可以打包。

## 展開與 SCEV 的互動

展開後，迴圈的 SCEV 分析也要更新：

```
原始：i = {0, +, 1}_loop
展開 4 次後：i = {0, +, 4}_loop（主迴圈每次加 4）
展開後的迭代 i, i+1, i+2, i+3 的索引直接計算
```

LLVM 在展開後重跑 SCEV 分析，更新感應變數的表示。

## 自我檢核

- [ ] 全展開：trip count 靜態已知且小，直接消除迴圈結構
- [ ] 部分展開：展開因子 k，主迴圈每次跳 k，加 remainder loop
- [ ] Remainder loop：處理 n % k 的剩餘迭代（trip count 是倍數時可省）
- [ ] 展開的代價：code size 膨脹 + register pressure
- [ ] 展開 + 向量化組合的意義

→ [Ch 25 向量化基礎：SLP 與迴圈向量化](./25-vectorization.md)
