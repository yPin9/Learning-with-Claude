# Ch 25 — 向量化基礎：SLP 與迴圈向量化

> 目標：理解 SLP 和迴圈向量化的不同場景，掌握合法性檢查和 cost model 的基本邏輯，以及如何讀懂向量化的診斷信息。

## 為什麼向量化

現代 CPU 有 SIMD 指令（SSE/AVX/NEON），一條指令可以同時處理多個數據（例如 AVX2 一次處理 8 個 float）。

```c
// 純量（每次處理 1 個）
for (int i = 0; i < 8; i++)
    c[i] = a[i] + b[i];   // 8 次 add

// 向量化後（一次處理 8 個）
__m256 va = _mm256_load_ps(a);
__m256 vb = _mm256_load_ps(b);
__m256 vc = _mm256_add_ps(va, vb);   // 1 次 SIMD add
_mm256_store_ps(c, vc);
```

LLVM 的向量化 pass 自動完成這個轉換（不需要手寫 intrinsic）。

## SLP 向量化（Superword Level Parallelism）

**SLP 向量化**：把相鄰的**獨立標量指令**打包成向量指令。

不需要迴圈，適用於展開後的代碼或原本就是標量的計算：

```c
// 原始（4 個獨立的加法）
a[0] = b[0] + c[0];
a[1] = b[1] + c[1];
a[2] = b[2] + c[2];
a[3] = b[3] + c[3];

// SLP 後（1 個 SIMD 加法）
v_a = v_b + v_c;   // 128-bit vector
```

SLP 合法性條件：

1. 指令的操作相同（都是加法、都是乘法等）
2. 記憶體訪問是連續的且不重疊（`b[0], b[1], b[2], b[3]` 連續）
3. 沒有數據依賴（指令之間互相獨立）

SLP 的 cost model：如果打包後的 SIMD 指令比原始標量指令更快（計算 throughput），才向量化。

## 迴圈向量化（Loop Vectorization）

**迴圈向量化**：把迴圈的多次迭代**並行執行**，每次用向量指令處理 VF（向量因子）個迭代。

```c
for (int i = 0; i < n; i++)
    c[i] = a[i] + b[i];

// 向量化後（VF = 4）
for (int i = 0; i < n - 3; i += 4)
    v_c[i:i+4] = v_a[i:i+4] + v_b[i:i+4];
// + remainder loop
```

### 合法性檢查（Legality Check）

向量化前必須確認：

**數據依賴（Data Dependence）**：迴圈的不同迭代之間不能有依賴。

```c
// 合法：每次迭代讀寫不同的元素
for (int i = 0; i < n; i++)
    c[i] = a[i] + b[i];

// 不合法：迭代 i 依賴迭代 i-1
for (int i = 1; i < n; i++)
    a[i] = a[i-1] + 1;
```

LLVM 的 `LoopVectorizationLegality` 分析數據依賴，判斷向量化是否合法。

**記憶體訪問是否連續（Strided Access）**：

SCEV 告訴 LLVM 訪問模式：`{a, +, sizeof(float)}_loop` 是步長為 1 的連續訪問，可以用 load/store vector。如果步長不規則，需要 gather/scatter 指令（代價高）。

### Cost Model

確認合法後，向量化還需要確認「向量化後是否更快」：

```
estimate_cost(scalar_loop) vs estimate_cost(vector_loop(VF))
只有 cost 降低才執行向量化
```

向量化的代價：
- 向量 load/store 的吞吐量和延遲
- gather/scatter（非連續訪問）通常很昂貴
- 類型轉換（int↔float）的代價
- Remainder loop 的開銷

```bash
# 診斷向量化決策
clang -O2 -Rpass=loop-vectorize -Rpass-missed=loop-vectorize \
      -Rpass-analysis=loop-vectorize /tmp/vec_test.c -o /dev/null
```

輸出說明了哪些迴圈被向量化，哪些沒有以及為什麼：

```
test.c:3:5: remark: vectorized loop (vectorization width: 8, ...)
test.c:8:5: remark: loop not vectorized: cannot prove it is safe to reorder
```

## 向量化的 VF 選擇

VF（Vectorization Factor）= 向量寬度 / 元素寬度。

例如 AVX2（256-bit）處理 float（32-bit）：VF = 8。

LLVM 的選擇邏輯：

```
1. 目標硬件的最大向量寬度（從 Target Triple 取得）
2. 元素類型的大小
3. Cost model 評估：更大的 VF 不一定更快（register pressure、remainder）
4. 用戶可以用 pragma 強制：
   #pragma clang loop vectorize_width(16)
```

## LLVM 的向量化 Pass

```bash
# SLP 向量化
opt -passes="slp-vectorizer" input.ll -o output.ll

# 迴圈向量化
opt -passes="loop-vectorize" input.ll -o output.ll

# 完整的向量化 pipeline（O2 包含這些）
opt -O2 input.ll -o output.ll
```

診斷向量化結果的工具：

```bash
# 生成 opt-remarks（向量化診斷）
opt -passes="loop-vectorize" \
    -pass-remarks=loop-vectorize \
    -pass-remarks-missed=loop-vectorize \
    input.ll -o output.ll 2>&1
```

## 常見向量化失敗原因

```
1. 迴圈計數不夠大（VF > trip count → 不值得）
2. 迴圈內有函式呼叫（除非是已知的 vectorizable intrinsic）
3. 指針別名不確定（a 和 b 可能重疊）
   解決：加 __restrict__，或 -ffast-math（浮點）
4. 迴圈內有 break（提前退出，不好向量化）
5. 非連續的記憶體訪問（gather/scatter 太慢）
```

## 自我檢核

- [ ] SLP vs 迴圈向量化：前者打包獨立的標量指令，後者並行迴圈迭代
- [ ] 合法性檢查：無跨迭代數據依賴 + 連續記憶體訪問
- [ ] Cost model：向量化的吞吐量收益 vs gather/scatter/remainder 的代價
- [ ] VF 選擇：向量寬度 / 元素大小，受 target 和 cost model 限制
- [ ] `clang -Rpass=loop-vectorize` 診斷向量化決策

→ [Ch 26 Loop Pass Pipeline：優化順序問題](./26-loop-pipeline.md)
