# Ch 13 — Vectorization report 閱讀

> 目標：學會讀 compiler 的 vectorization report，判斷哪些 loop 被 vectorize、哪些失敗、原因是什麼。對 RVV (RISC-V Vector) 程式碼尤其重要。

## 為什麼 vectorization report 重要

現代 compiler 有 auto-vectorizer，但它保守 —— 很多 loop 看起來能 vectorize、實際 fail。Report 揭露原因。

對 RISC-V，RVV 的 auto-vectorization 仍在 maturing。**能讀 report 的工程師才能 judge「這 loop 我該手動 intrinsic」**。

## Clang 的 -Rpass 系列

```bash
clang -O2 -Rpass=loop-vectorize foo.c -c        # 印 vectorize 成功
clang -O2 -Rpass-missed=loop-vectorize foo.c -c # 印 vectorize 失敗
clang -O2 -Rpass-analysis=loop-vectorize foo.c -c # 印分析細節
```

三個可以 combine：

```bash
clang -O2 -Rpass=loop-vectorize \
      -Rpass-missed=loop-vectorize \
      -Rpass-analysis=loop-vectorize \
      foo.c -c
```

## 範例：成功 vectorize

```c
// add.c
void add(int *a, int *b, int *c, int n) {
    for (int i = 0; i < n; i++)
        c[i] = a[i] + b[i];
}
```

```bash
clang --target=riscv64 -march=rv64gcv -O3 \
      -Rpass=loop-vectorize add.c -c
```

輸出：

```
add.c:4:5: remark: vectorized loop (vectorization width: vscale x 4, 
           interleaved count: 2) [-Rpass=loop-vectorize]
    for (int i = 0; i < n; i++)
    ^
```

解讀：

- `vectorization width: vscale x 4`：RVV scalable vector
- `interleaved count: 2`：每 iter 處理 2 × vscale × 4 = 8 × vscale element

## 範例：失敗 vectorize

```c
// fail.c
void fail(int *a, int *b, int *c, int n) {
    for (int i = 1; i < n; i++)
        c[i] = c[i-1] + a[i];    // dependency!
}
```

```bash
clang --target=riscv64 -march=rv64gcv -O3 \
      -Rpass-missed=loop-vectorize fail.c -c
```

```
fail.c:3:5: remark: loop not vectorized: unsafe dependent memory 
           operations in loop. Use #pragma loop distribute(enable) 
           to allow loop distribution to attempt to isolate the 
           offending operations into a separate loop [-Rpass-missed=loop-vectorize]
```

**"unsafe dependent memory operations"**：`c[i]` 依賴 `c[i-1]`，不能 vectorize。

## 常見 fail 原因

### 1. Loop-carried dependency

```c
for (int i = 1; i < n; i++) c[i] = c[i-1] + ...;
```

無解，除非改 algorithm（prefix sum 有 parallel 版）。

### 2. Pointer aliasing

```c
void foo(int *a, int *b, int *c, int n) {
    for (int i = 0; i < n; i++) c[i] = a[i] + b[i];
}
```

Compiler 不知道 `c` 跟 `a` / `b` 是否 overlap → 保守。

解法：

```c
void foo(int * __restrict a, int * __restrict b, int * __restrict c, int n) { ... }
```

`__restrict` 告訴 compiler「這 pointer 不跟其他 pointer 別名」。

### 3. Non-countable loop

```c
while (*p != 0) { ... p++; }
```

Loop 結束條件 runtime 才知道 → hard to vectorize。C++ iterator 有時類似。

### 4. Function call in loop

```c
for (...) { sum += foo(x); }
```

foo 沒 inline → 保守。opt pass ordering 有時能 inline + vectorize，有時不行。

### 5. Complex control flow

```c
for (...) {
    if (cond1) a[i] = 1;
    else if (cond2) a[i] = 2;
    else a[i] = 3;
}
```

某些 compiler 能 vectorize（用 mask）、某些不能。看 report 確認。

### 6. 不可 reduce 的 reduction

```c
float s = 0;
for (...) s *= arr[i];    // 非 associative (FP multiplication)
```

`-O2` 不會 vectorize（怕 FP reorder）。`-O3 -ffast-math` 能。

## GCC 的 equivalent

```bash
gcc -O3 -fopt-info-vec foo.c -c        # 成功
gcc -O3 -fopt-info-vec-missed foo.c -c # 失敗
gcc -O3 -fopt-info-vec-all foo.c -c    # 全部 details
```

Output 類似 Clang。

## 實例：debug 一個失敗的 vectorization

```c
// mat.c
void matmul(int *A, int *B, int *C, int n) {
    for (int i = 0; i < n; i++)
        for (int j = 0; j < n; j++)
            for (int k = 0; k < n; k++)
                C[i*n+j] += A[i*n+k] * B[k*n+j];
}
```

```bash
clang -O3 -march=rv64gcv -Rpass-missed=loop-vectorize mat.c -c
```

可能 output：

```
mat.c:5:17: remark: loop not vectorized: Access has unknown stride
```

"Unknown stride" = access pattern 不規律。`B[k*n+j]` 的 stride 是 n，runtime 決定。

解法：

1. Loop interchange (swap i/j/k order)
2. Blocking
3. 手動用 intrinsic

## 用 `#pragma` 強制

有時你知道 safe，compiler 保守。用 pragma 強制：

```c
#pragma clang loop vectorize(enable) vectorize_width(4)
for (int i = 0; i < n; i++) ...
```

GCC 類似：

```c
#pragma GCC ivdep   // ignore vector dependencies
for (...)
```

**慎用**。寫錯 → miscompile。只在 review 過 safety 才加。

## Vectorization width 跟 VF

- **VF (Vectorization Factor)**：一次 iter 處理幾個 element
- Width 4 → 一次處理 4 × element

對 fixed-length SIMD（SSE/NEON）明確；對 RVV scalable 是 `vscale × N`。

## Auto-vectorize 的限制

現代 compiler 的 auto-vec 有極限：

- Inner loop (outer loop 通常不 vec)
- Simple pattern（element-wise）
- Straight-line（minimal control flow）

複雜 workload 仍需要手動 intrinsic：

- **Crypto**：ChaCha20、AES-GCM
- **FFT**：butterfly
- **Image processing**：某些 kernel

SiFive / Cray 等公司都有 hand-tuned library。

## `-march` 影響 vectorize

```bash
clang -O3 -march=rv64gc foo.c         # 無 V extension
clang -O3 -march=rv64gcv foo.c         # 有 V
```

沒 `v` 的話 vectorize 走 "scalar vectorize" (loop unroll 變種)，efficiency 很低。

## Inner Loop Vectorization (ILV) vs Outer Loop Vec (OLV)

標準 auto-vec 是 inner loop（最內 loop）。

**Outer loop vectorization**：vectorize 外層 loop（複雜但更多收益）。LLVM 2023+ 加入實驗性支援：

```bash
clang -O3 -mllvm -enable-vplan-native-path foo.c -c
```

RISC-V 的 OLV 還在開發。

## Slp (Super-word Level Parallelism)

另一種 vectorize：把 straight-line code 裡的 parallel computation pack into vector。

```c
a = p[0] + q[0];
b = p[1] + q[1];
c = p[2] + q[2];
d = p[3] + q[3];
```

SLP vectorizer 可能把這 4 條 combine 成一條 vector add。

`-Rpass=slp-vectorizer` 看這個。

## 對 benchmark 的實際意義

Vectorize 成功通常 **+2× ~ +10×** 速度（for compute-bound inner loop）。失敗變 scalar、可能慢 10×。

所以：

- 看 hot loop 有沒有 vec
- 沒有 → 看 report → 修 code 或 compiler flag
- 仍沒 → 手動 intrinsic

**這是 SiFive 工程師的日常 routine**。

## 工具：`opt-viewer`

LLVM 的 `opt-viewer.py` 把 remark 轉 HTML：

```bash
clang -O3 -fsave-optimization-record foo.c -c
opt-viewer.py foo.opt.yaml -o out/
```

Web UI 瀏覽每個 function 的 optimization decision。大型 project 用。

## 動手練習

1. 寫 5 個 vectorize-friendly / -hostile 的 C loop、用 `-Rpass=loop-vectorize` 看 report。
2. 加 `__restrict` keyword、觀察 report 變化。
3. 寫含 FP reduction 的 loop，試 `-O3` vs `-O3 -ffast-math`。
4. 找 Coremark 的 hot loop、看 compiler 有沒有 vectorize。
5. 試 `#pragma clang loop` 強制 vectorize / 加 hint。

## 常見誤會

1. **「-O3 就自動 vectorize」**：大部分 yes、但 corner case 不 vec。用 report 驗。
2. **「所有 loop 都該 vectorize」**：有些 loop 不適合（iteration 少、element tiny）。
3. **「Clang report 完整」**：常漏 detail。combine -Rpass + -Rpass-missed + -Rpass-analysis。
4. **「__restrict 免費」**：多加不會錯，但你保證 runtime 真的沒 alias。
5. **「RVV auto-vec 成熟」**：2026 時點仍 improving。很多 pattern 需 handle 手動 intrinsic。

## 自我檢核

- [ ] 我能用 `-Rpass=loop-vectorize` 跟 `-Rpass-missed` 分析 code
- [ ] 我能辨認 5+ 種常見 vectorization 失敗原因
- [ ] 我知道 `__restrict` 對 aliasing 的解法
- [ ] 我知道 SLP vs ILV 的差異
- [ ] 我能 articulate 「這 loop 手動 intrinsic 還是 auto-vec 好」

下一章整合前面所有 — 從 hot loop 倒推到 compiler optimization 的思考框架。

→ [Ch 14 從 hot loop 倒推「該加什麼 optimization」](./14-hot-loop-thinking.md)
