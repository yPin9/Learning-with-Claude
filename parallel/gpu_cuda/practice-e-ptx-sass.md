# 練習 E：改一行 Code，對照 PTX/SASS 驗證編譯器行為

**所屬 Part**：Part 5 深挖微架構 / PTX / SASS（Ch 26–31）  
**前置**：已完成 Ch 26–31，熟悉 `nvcc -ptx`、`cuobjdump -sass`、godbolt.org 基本操作  
**環境**：godbolt.org（nvcc 12.x）或 Colab（nvcc 12.x）；本練習所有期望觀察以 **nvcc 12.x -arch=sm_75** 為準，作者環境無 nvcc，以讀者重現為準

---

## 本練習的目標

我們在 Ch 26–31 談了很多「編譯器應該會做什麼」。本練習的目的是讓你親手確認「它實際上做了什麼」——只改一行 code，然後用 PTX/SASS 驗證差異。

這四個實驗各自對應一個常見的性能迷思：

| 實驗 | 改的東西 | 要驗證的事 |
|------|----------|------------|
| A | 加 `__restrict__` | 是否真的產生 `.nc` read-only cache hint |
| B | 加 `#pragma unroll` | SASS 層的 loop 展開與 ILP |
| C | 換成 `__fmaf_rn()` | FFMA 的生成條件 |
| D | 小 if-else | predication vs BRA 的決策邏輯 |

---

## 實驗 A：`__restrict__` 與 LDG cache hint

### 背景動機

`__restrict__` 是 C99 指標別名限制宣告的 CUDA 版本，告訴編譯器這幾個指標之間不互相重疊（no aliasing）。理論上，編譯器可以利用這個資訊在 PTX 層產生 `ld.global.nc.f32`（`.nc` = non-coherent，走 read-only cache，對應 `__ldg()`），或是讓 ptxas 在排程決策上更積極。

我們要驗證的問題是：`__restrict__` 到底做了什麼？只影響 alias analysis，還是真的改變了指令？

### 操作步驟

在 godbolt.org 選 **nvcc 12.x**，Compiler options 欄位填 `-ptx -arch=compute_75`。  
或在 Colab：

```bash
# Colab 環境確認 nvcc 版本
nvcc --version
```

**版本 A（無 restrict）**

```cuda
// restrict_test_a.cu
__global__ void add(float* a, float* b, float* c, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) c[i] = a[i] + b[i];
}
```

```bash
# Colab
nvcc -ptx -arch=compute_75 restrict_test_a.cu -o restrict_a.ptx
grep "ld.global" restrict_a.ptx
```

**版本 B（有 restrict）**

```cuda
// restrict_test_b.cu
__global__ void add(const float* __restrict__ a,
                    const float* __restrict__ b,
                    float* __restrict__ c, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) c[i] = a[i] + b[i];
}
```

```bash
# Colab
nvcc -ptx -arch=compute_75 restrict_test_b.cu -o restrict_b.ptx
grep "ld.global" restrict_b.ptx

# 對照 PTX 差異（Colab）
diff restrict_a.ptx restrict_b.ptx

# 如果要看 SASS，改用 -cubin 流程
nvcc -cubin -arch=sm_75 restrict_test_b.cu -o restrict_b.cubin
cuobjdump -sass restrict_b.cubin | grep "LDG"
```

**godbolt.org 流程**：把兩個版本各自貼進去，把 Compiler options 從 `-ptx -arch=compute_75` 改成 `-cubin -arch=sm_75`，然後在 Output 旁邊的下拉選 "PTX" 或看 ASM output（注意：godbolt 的 nvcc 直接輸出 PTX，SASS 需要額外 `cuobjdump` 步驟，建議用 Colab 看 SASS）。

### 期望觀察

**PTX 層（版本 A vs B）**

版本 A 的 load 預期是：
```ptx
ld.global.f32   %f1, [%rd3];
ld.global.f32   %f2, [%rd5];
```

版本 B 的 load 預期可能是：
```ptx
ld.global.nc.f32   %f1, [%rd3];
ld.global.nc.f32   %f2, [%rd5];
```

`.nc`（non-coherent）表示走 read-only cache path，繞過 L1 的 dirty state 檢查。

**SASS 層**

版本 A 的 load：
```sass
LDG.E.SYS R2, [R4]
LDG.E.SYS R3, [R6]
```

版本 B 可能看到：
```sass
LDG.E.CONSTANT.SYS R2, [R4]
LDG.E.CI.SYS R2, [R4]    // .CI = cache-invalidate = read-only path
```

具體修飾詞依 driver 版本和 sm 目標不同而略有差異。

**誠實說明**：CUDA 編譯器不保證 `__restrict__` 一定產生 `.nc` 或 `CONSTANT` 修飾。有時 CUDA 編譯器只把這個資訊用在 alias analysis（讓 ptxas 更積極排程、減少 dependency stall），PTX/SASS 指令本身看起來和沒加 `__restrict__` 一樣。讀者的實際觀察才是本練習的輸出。

<details>
<summary>參考觀察與解釋</summary>

**為什麼 `.nc` 對性能有幫助？**

沒有 `__restrict__` 時，編譯器必須假設 `c` 可能和 `a` 或 `b` 的記憶體重疊。這意味著：在寫入 `c[i]` 之後，下一次讀 `a[i]` 或 `b[i]` 時，編譯器不能確定 L1 cache 裡的值是否已被自己的寫入污染，所以必須走一般的 coherent load path。

有了 `__restrict__`，編譯器知道 `a` 和 `b` 的記憶體不會被 `c` 的寫入影響，可以安全地走 read-only cache path（texture cache / L1 read-only partition）。這個 cache path 在很多 GPU 架構上有獨立的 bandwidth，不和 L1 write path 競爭。

**`.nc` 和 `__ldg()` 的關係**

`__ldg(ptr)` 是明確地走 read-only cache 的 intrinsic，PTX 層就是 `ld.global.nc`。`__restrict__` 是讓編譯器「自己決定要不要走」。兩者的 PTX 指令可能相同，但語意不同：`__ldg` 是你的承諾，`__restrict__` 是給編譯器的 hint。

**實際性能差異**

在 bandwidth-bound kernel 中（如這個純加法），`.nc` 的影響可以有 5–15% 的差異，取決於資料是否適合放進 read-only cache 且 cache line 重用率夠高。在 compute-bound kernel 中幾乎無感。

</details>

### 自我檢核

1. 如果把 `a` 標 `__restrict__` 但 `b` 不標，PTX 層的 `a` 和 `b` 的 load 指令會不同嗎？你預期會看到什麼？
2. `ld.global.nc` 走的是哪個 cache？它和 L1 data cache 是同一個嗎？（參考 Ch 27）
3. 在什麼情況下 `__restrict__` 對 PTX/SASS 毫無影響，但仍然對編譯速度或程式正確性有意義？

---

## 實驗 B：`#pragma unroll` 前後 SASS 差異

### 背景動機

`#pragma unroll` 告訴 ptxas 展開緊接著的迴圈。展開的效果在 CPU 和 GPU 上都有，但 GPU 的理由更具體：消除 loop overhead（IMAD counter + ISETP compare + BRA）之後，ptxas 可以把多個迴圈迭代的 LDG 和 FFMA 重新排程，讓記憶體延遲和計算指令互相重疊（ILP）。

我們要在 SASS 層直接確認：展開前有沒有 `BRA`，展開後有沒有 `BRA`，以及 FFMA 的排列方式是否改變。

### 操作步驟

```cuda
// unroll_test.cu
// 切換 #pragma unroll 這一行，其他不動

__global__ void dot4(const float* __restrict__ a,
                     const float* __restrict__ b,
                     float* result) {
    float sum = 0.0f;
    // 版本 A：把下面這行拿掉
    // 版本 B：加上下面這行
    // #pragma unroll
    for (int i = 0; i < 4; i++) {
        sum += a[i] * b[i];
    }
    if (threadIdx.x == 0) *result = sum;
}
```

```bash
# Colab — 版本 A（無 unroll）
nvcc -cubin -arch=sm_75 -O2 unroll_a.cu -o unroll_a.cubin
cuobjdump -sass unroll_a.cubin

# 版本 B（有 unroll），只需把 #pragma unroll 取消註解
nvcc -cubin -arch=sm_75 -O2 unroll_b.cu -o unroll_b.cubin
cuobjdump -sass unroll_b.cubin

# 比較指令數
cuobjdump -sass unroll_a.cubin | grep -c "SASS"
cuobjdump -sass unroll_b.cubin | grep -c "SASS"
```

ptxas 對固定次數迴圈（trip count 在編譯期已知）通常會自動展開，所以版本 A 也可能被展開。用 `#pragma unroll 1` 強制不展開：

```cuda
// 版本 A 強制不展開
#pragma unroll 1
for (int i = 0; i < 4; i++) {
    sum += a[i] * b[i];
}
```

### 期望觀察

**版本 A（強制不展開，`#pragma unroll 1`）**，SASS 大致結構：

```sass
// loop setup
MOV R0, 0x0          // i = 0
// loop body (重複 4 次靠 BRA 跳回)
LDG.E.SYS R2, [R4+offset]    // a[i]
LDG.E.SYS R3, [R6+offset]    // b[i]
FFMA R1, R2, R3, R1           // sum += a[i] * b[i]
IADD3 R0, R0, 0x1, RZ         // i++
ISETP.LT.AND P0, PT, R0, 0x4, PT  // i < 4?
@P0 BRA loop_top               // 跳回
```

**版本 B（展開）**，SASS 大致結構：

```sass
// 4 組展開，沒有 BRA
LDG.E.SYS R2, [R4+0x0]     // a[0]
LDG.E.SYS R4, [R6+0x0]     // b[0]
LDG.E.SYS R6, [R8+0x4]     // a[1]  (ptxas 可能提前發出以隱藏延遲)
LDG.E.SYS R8, [R10+0x4]    // b[1]
// ...更多 LDG...
FFMA R1, R2, R4, R1          // sum += a[0]*b[0]
FFMA R1, R6, R8, R1          // sum += a[1]*b[1]
FFMA R1, R12, R14, R1
FFMA R1, R16, R18, R1
```

實際 SASS 的暫存器編號和排列順序由 ptxas 決定，上面只是示意。重點是：展開後沒有 `BRA`，且 LDG 和 FFMA 會交叉排列（ptxas 刻意讓 LDG 比對應的 FFMA 早幾條指令發出，以隱藏記憶體延遲）。

**誠實說明**：期望觀察以 Colab nvcc 12.x -arch=sm_75 / godbolt.org nvcc 12.x 為準，作者環境無 nvcc，以讀者重現為準。

<details>
<summary>參考觀察與解釋</summary>

**ILP 的具體體現**

沒有展開時，ptxas 在每次迴圈迭代中只能看到「一對 LDG + 一個 FFMA」，可重排的空間很有限。展開後，ptxas 能同時看到 4 對 LDG + 4 個 FFMA，可以把所有 LDG 集中在前面（讓記憶體請求盡早發出），然後在等待 LDG 回傳的過程中插入其他指令，最後才收 FFMA。

這個效果在 Volta/Turing（sm_70/sm_75）及之後的架構上特別明顯，因為這些架構的 L1 記憶體延遲約 28–32 個 clock，一對 LDG+FFMA 根本不夠填這個 bubble。

**Register pressure 的代價**

展開 4 次後，ptxas 需要同時持有 4 個 `a[i]` 和 4 個 `b[i]` 的值，所以 register 用量增加。對於很大的展開次數（如 `#pragma unroll 16`），register 用量可能超過門檻而觸發 register spilling（把暫存器溢出到 local memory），反而降低性能。

`#pragma unroll 2` 或 `#pragma unroll 4` 是在 ILP 和 register pressure 之間取平衡的常見做法。

**ptxas 自動展開的條件**

當迴圈 trip count 在編譯期確定（如 `for (int i = 0; i < 4; i++)`），ptxas 通常會自動展開，不需要 `#pragma unroll`。加了 `#pragma unroll 1` 才能強制不展開。對於 trip count 不固定的迴圈（如 `for (int i = 0; i < n; i++)`），`#pragma unroll` 才真正有效。

</details>

### 自我檢核

1. 展開後的 SASS 中，LDG 和 FFMA 的排列順序是否符合你預期的「LDG 先發出，FFMA 後收割」模式？
2. `#pragma unroll 2` 和 `#pragma unroll 4` 的 SASS 輸出中，`BRA` 指令消失了嗎？register 用量（可用 `--ptxas-options=-v` 查）有何差異？
3. 如果迴圈 body 裡面有 `__syncthreads()`，`#pragma unroll` 還能展開嗎？

---

## 實驗 C：`float` 加法 vs `__fmaf_rn()` 看 FFMA 生成

### 背景動機

FFMA（Fused Multiply-Add）是 GPU 計算的基礎單元，理論上 `a * b + c` 的寫法應該自動融合成 FFMA。但這個融合有條件：預設的 `--fmad=true` 才允許，`--fmad=false` 會強制拆開。`volatile` 則是另一個強制拆開的手段。

我們測試三種寫法，並且用 `--fmad=false` 對照，確認 SASS 層的實際行為。

### 操作步驟

```cuda
// fma_test.cu
__device__ float test_v1(float a, float b, float c) {
    return a * b + c;           // 依賴 --fmad=true 融合
}

__device__ float test_v2(float a, float b, float c) {
    return __fmaf_rn(a, b, c); // 強制 FMA，round-to-nearest-even
}

__device__ float test_v3(float a, float b, float c) {
    volatile float tmp = a * b; // volatile 強制分兩步
    return tmp + c;
}

// 讓 linker 不把 device function 最佳化掉
__global__ void wrapper(float* out, float a, float b, float c) {
    out[0] = test_v1(a, b, c);
    out[1] = test_v2(a, b, c);
    out[2] = test_v3(a, b, c);
}
```

```bash
# Colab — 預設 fmad=true
nvcc -cubin -arch=sm_75 -O2 fma_test.cu -o fma_on.cubin
cuobjdump -sass fma_on.cubin | grep -E "FFMA|FMUL|FADD"

# 關閉 FMA 融合
nvcc -cubin -arch=sm_75 -O2 --fmad=false fma_test.cu -o fma_off.cubin
cuobjdump -sass fma_off.cubin | grep -E "FFMA|FMUL|FADD"

# 也看 PTX 層的差異
nvcc -ptx -arch=compute_75 -O2 fma_test.cu -o fma_on.ptx
nvcc -ptx -arch=compute_75 -O2 --fmad=false fma_test.cu -o fma_off.ptx
grep "fma\|mul\|add" fma_on.ptx
grep "fma\|mul\|add" fma_off.ptx
```

### 期望觀察

| 寫法 | `--fmad=true`（預設） | `--fmad=false` |
|------|----------------------|----------------|
| `v1: a * b + c` | FFMA（通常） | FMUL + FADD |
| `v2: __fmaf_rn(a, b, c)` | FFMA（保證） | FFMA（保證，ignore `--fmad`） |
| `v3: volatile tmp = a*b; tmp + c` | FMUL + FADD（volatile 阻止融合） | FMUL + FADD |

PTX 層的對應：

```ptx
// v1 with fmad=true
fma.rn.f32   %f3, %f1, %f2, %f0;    // PTX fma

// v1 with fmad=false
mul.rn.f32   %f3, %f1, %f2;         // PTX mul
add.rn.f32   %f4, %f3, %f0;         // PTX add

// v2 無論哪個 flag
fma.rn.f32   %f3, %f1, %f2, %f0;   // __fmaf_rn 直接對應 fma.rn
```

SASS 層對應：

```sass
// FFMA（v1 fmad=true 或 v2）
FFMA R2, R0, R1, R3

// 拆開（v1 fmad=false 或 v3）
FMUL R4, R0, R1
FADD R2, R4, R3
```

**誠實說明**：期望觀察以 Colab nvcc 12.x -arch=sm_75 / godbolt.org nvcc 12.x 為準，作者環境無 nvcc，以讀者重現為準。

<details>
<summary>參考觀察與解釋</summary>

**FMA 的精度差異**

FFMA 和 FMUL+FADD 在數學上等價，但在 IEEE 754 浮點精度上不等價：

- `FMUL + FADD`：兩次 round，累積誤差較大
- `FFMA`：只 round 一次（在最後結果），中間的 `a * b` 以無限精度保存

這表示 `--fmad=true` 可能讓你的程式在精度上比 `--fmad=false` 更好（只 round 一次），但行為和嚴格 IEEE 754 分開計算不同。

在金融計算或需要嚴格可重現結果的場景，`--fmad=false` 是必要的。在一般科學計算中，`--fmad=true` 的精度通常更高。

**`__fmaf_rn()` 的語意**

`__fmaf_rn(a, b, c)` 直接對應 PTX 的 `fma.rn.f32`，是不受 `--fmad` flag 影響的強制 FMA 路徑。`rn` 表示 round-to-nearest-even（IEEE 754 預設模式）。其他變體：

- `__fmaf_rz()`：round toward zero
- `__fmaf_rd()`：round down
- `__fmaf_ru()`：round up

**volatile 的作用機制**

`volatile float tmp = a * b` 強制編譯器在這個點真正計算並儲存 `a * b` 的結果，不允許跨過這個點進行 FMA 融合。這是一個在不改變 `-fmad` flag 的情況下局部禁止 FMA 的技巧，但有額外的 store/load overhead（雖然通常在 local memory / register 層面，實際 overhead 取決於編譯器是否真的 spill）。

</details>

### 自我檢核

1. `--fmad=false` 的影響體現在 PTX 層還是 SASS 層？還是兩個層面都有？（試著分別用 `-ptx` 和 `-cubin` 比較）
2. `__fmaf_rn(a, b, c)` 在 `--fmad=false` 的環境下仍然產生 FFMA。這是好事還是壞事？取決於什麼？
3. 如果你的 kernel 需要嚴格符合 IEEE 754 加法結合律，`--fmad=true` 是否安全？

---

## 實驗 D：分支 Predication（小 if 變 `@P0` Predicated 指令）

### 背景動機

GPU 的 warp divergence 是眾所周知的性能殺手，但 ptxas 有一個緩解機制：predication。對於 body 很短的 if-else，ptxas 可能把分支轉換成 predicated 指令（`@P0` 前綴），讓 warp 裡所有 thread 執行相同的指令序列，但只有滿足條件（predicate = true）的 thread 的結果被寫入目標暫存器。

predication 消除了 `BRA` 帶來的 warp divergence overhead，但代價是 predicated-off 的 thread 仍然消耗 instruction slot。我們要在 SASS 層看到這個決策。

### 操作步驟

```cuda
// predicate_test.cu
__global__ void clamp(float* data, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) {
        float x = data[i];
        if (x > 1.0f) x = 1.0f;       // 小分支 → 候選 predication
        else if (x < 0.0f) x = 0.0f;  // 小分支 → 候選 predication
        data[i] = x;
    }
}
```

```bash
# Colab
nvcc -cubin -arch=sm_75 -O2 predicate_test.cu -o predicate_test.cubin
cuobjdump -sass predicate_test.cubin

# 看 PTX
nvcc -ptx -arch=compute_75 -O2 predicate_test.cu -o predicate_test.ptx
cat predicate_test.ptx
```

**對照版本：加長 if body，觀察 ptxas 何時改回 BRA**

```cuda
__global__ void clamp_heavy(float* data, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) {
        float x = data[i];
        if (x > 1.0f) {
            // 故意加長 body，讓 ptxas 覺得 predication 不划算
            x = 1.0f;
            x = __fmaf_rn(x, 0.999f, 0.001f);
            x = __fmaf_rn(x, 0.999f, 0.001f);
            x = __fmaf_rn(x, 0.999f, 0.001f);
            x = __fmaf_rn(x, 0.999f, 0.001f);
            x = __fmaf_rn(x, 0.999f, 0.001f);
        }
        data[i] = x;
    }
}
```

```bash
nvcc -cubin -arch=sm_75 -O2 predicate_heavy.cu -o predicate_heavy.cubin
cuobjdump -sass predicate_heavy.cubin | grep -E "BRA|ISETP|FSETP|@P"
```

### 期望觀察

**clamp kernel 的 SASS（predication 版本）**

外層 boundary check（`i < n`）仍然是 `BRA`，因為 skip 的 body 太長：

```sass
ISETP.LT.AND P0, PT, R0, R5, PT    // P0 = (i < n)
@!P0 BRA exit_label                  // i >= n 就跳走
```

內層小 if（`x > 1.0f`）預期 predication：

```sass
FSETP.GT.FTZ.AND P1, PT, R2, 1.0f, PT  // P1 = (x > 1.0f)
@P1 MOV R2, 1.0f                          // 只有 P1=true 的 thread 執行
FSETP.LT.FTZ.AND P2, PT, R2, 0.0f, PT  // P2 = (x < 0.0f)
@P2 MOV R2, RZ                            // 只有 P2=true 的 thread 執行
// 注意：沒有 BRA
```

PTX 層的對應：

```ptx
setp.gt.f32   %p1, %f1, 0f3F800000;   // 1.0f = 0x3F800000
@%p1 mov.f32  %f2, 0f3F800000;
setp.lt.f32   %p2, %f2, 0f00000000;
@%p2 mov.f32  %f2, 0f00000000;
```

**clamp_heavy kernel**

當 if body 變長（5 個 FFMA），ptxas 預計切回 `BRA`：

```sass
FSETP.GT.FTZ.AND P1, PT, R2, 1.0f, PT
@!P1 BRA skip_heavy               // 不滿足條件就跳過長 body
FFMA R2, R2, ...
FFMA R2, R2, ...
// ... 5 個 FFMA ...
skip_heavy:
STG.E.SYS [R4], R2
```

**誠實說明**：期望觀察以 Colab nvcc 12.x -arch=sm_75 / godbolt.org nvcc 12.x 為準，作者環境無 nvcc，以讀者重現為準。ptxas 的 predication 決策門檻在不同版本和架構下可能不同，以讀者實際觀察為準。

<details>
<summary>參考觀察與解釋</summary>

**Predication 的決策邏輯**

ptxas 決定用 predication 還是 BRA 的依據主要是分支 body 的長度（指令數）。一個常見的近似：

- body ≤ 4 條指令：predication（避免 BRA + pipeline flush 的 overhead）
- body > 4 條指令：BRA（predicated-off thread 消耗的 slot 超過 BRA overhead）

這個門檻不是固定的，ptxas 有更複雜的 cost model，但這個直覺大致正確。

**Predication vs BRA 的性能比較**

predication 在以下情況更快：
- 分支 body 很短（1–3 條指令）
- warp 內的 branch 分佈接近 50/50（divergence 嚴重）

BRA 在以下情況更快：
- 分支 body 很長（10+ 條指令）
- warp 內幾乎所有 thread 走同一條路（coherent branch）

當 warp 裡所有 thread 都走同一條路時，BRA 只是一條「沒有 warp divergence」的跳轉，代價很低。這時用 predication 反而浪費 slot（predicated-off 的 thread 根本沒有，但指令還是要佔 slot）。

**`__builtin_expect` 在 GPU 上的效果**

CPU 上常用 `__builtin_expect(condition, 1)` 提示分支預測。在 GPU 上，ptxas 不支援類似的 hint，所以這個技巧在 CUDA 中幾乎無效。ptxas 的決策只看 body 長度，不看 branch probability hint。

**FSETP vs ISETP**

- `FSETP`：浮點數比較（`x > 1.0f` 這類）
- `ISETP`：整數比較（`i < n` 這類）

兩者都把比較結果寫進 predicate register（`P0`–`P6`），然後後續指令的 `@P` 前綴決定是否執行。

</details>

### 自我檢核

1. 在你觀察到的 SASS 輸出中，外層 `if (i < n)` 和內層 `if (x > 1.0f)` 哪個用了 `BRA`，哪個用了 predication？是否符合「body 長度決定策略」的預期？
2. 把 clamp 的內層 if 改成三個 clamp 條件（如 `if (x > 1.0f)`, `if (x < -1.0f)`, `if (x == 0.0f)`），SASS 還是 predication 嗎？
3. predicated-off 的 thread 不寫入結果，但它消耗了 instruction slot。這在 warp occupancy 的角度上意味著什麼？

---

## 整合自我檢核

完成四個實驗後，回答這幾個橫跨實驗的問題：

1. `__restrict__` 保證一定產生 `.nc` cache hint 嗎？為什麼？如果編譯器選擇不產生 `.nc`，`__restrict__` 還有沒有任何作用？

2. `#pragma unroll` 展開後 register 用量會增加。如果 register 用量超過 sm_75 的每 thread 上限（255 個 register），ptxas 會怎麼做？這對 warp occupancy 和 kernel 性能有什麼影響？（提示：用 `--ptxas-options=-v` 看 `registers per thread` 和 `spills`）

3. `--fmad=false` 的 PTX 輸出和 `--fmad=true` 的差別體現在哪個層面？PTX、SASS，還是都有？試著用實驗 C 的結果回答。

4. predicated 執行和 BRA 分支哪個「更快」？答案取決於哪兩個關鍵因素？在哪種極端情況下各自佔優？

---

## 本練習的核心結論

1. **PTX/SASS 是驗證編譯器行為的唯一可靠手段**：你根據文件「推斷」編譯器應該做的事，和它實際做的事往往不同。直接看 SASS 才能確認。

2. **Hints 不是保證**：`__restrict__`、`#pragma unroll`、`__fmaf_rn` 都是給編譯器的資訊或 intrinsic，但最終 ptxas 有自己的 cost model。`__fmaf_rn` 是最強的（直接對應 PTX fma），其他都只是 hint。

3. **Predication 是 GPU 對 short branch 的解法**，但不是免費的——predicated-off thread 仍然消耗 instruction slot。body 長度是 ptxas 選擇策略的主要依據。

4. **`--fmad=true` 改變了 IEEE 754 語意**：FFMA 只 round 一次，和 FMUL+FADD 的兩次 round 不等價。在需要嚴格可重現結果的場景，這個差異是實際問題，不是理論問題。

5. **Godbolt.org 是快速實驗 PTX 的利器**：比 Colab 省去環境設定時間，PTX 輸出即時呈現。SASS 則需要 Colab（`cuobjdump -sass`），因為 godbolt 不提供 `cuobjdump`。

---

## 銜接

PTX/SASS 是理解 GPU 執行行為的底層語言。下一章我們往上走一層：

**[Ch 32 函式庫](./32-libraries.md)**：cuBLAS、cuDNN、NCCL——這些高層工具的性能和正確性，最終都建立在我們這幾章分析過的 PTX/SASS 行為上。理解底層讓你在使用這些函式庫時知道「它們在做什麼」，而不只是「呼叫一個黑盒子」。
