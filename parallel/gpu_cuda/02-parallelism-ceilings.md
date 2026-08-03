# Ch 2 — 平行的天花板

> **目標**：用 Amdahl 定律算出「這個程式值不值得平行化」，用 Roofline model 診斷「這個 kernel 卡在哪裡」，從此不再憑直覺猜效能瓶頸。

---

## 為什麼需要這個？

有人把程式扔上 GPU，加速了 5 倍，很開心。但這個程式理論上可以加速 50 倍——他不知道，繼續優化浪費在錯的地方。

也有人把 kernel 改了一整週，把計算量砍了 30%，結果性能幾乎沒變——因為 kernel 根本是 memory-bound，改計算量沒用。

這兩個問題都有工具可以在動手之前就給出答案。Amdahl 定律告訴你**上限**；Roofline model 告訴你**瓶頸在哪**。這是 GPU 效能工程師日常用的兩個思考框架，比任何調校技巧都重要。

---

## 先建立直覺：廚房的類比

**Amdahl 定律的直覺**：想像你在準備一桌宴席，總共 120 分鐘的工作。其中 30 分鐘是洗米、煮飯（只能一個人做，因為只有一台電鍋）；90 分鐘是切菜、炒菜（可以多人同時做）。

你雇了很多廚師來幫忙切菜炒菜。切菜炒菜的部分，理論上可以無限縮短（假設廚師無限多）。但是洗米煮飯那 30 分鐘，不管多少廚師都沒辦法縮短。

所以無論你雇了多少廚師，總時間最快也只能是 30 分鐘——**序列部分決定了上限**。

**Roofline model 的直覺**：你在開車去遠地，路上有兩段：市區（速限 50 km/h）和高速公路（速限 110 km/h）。總行程時間取決於兩段各佔多少比例，以及你有沒有超速被抓。

Roofline model 的「屋頂」就是這個速限：一個是「記憶體頻寬屋頂」（市區速限），一個是「計算能力屋頂」（高速公路速限）。你的 kernel 跑在哪個屋頂下，就是哪個瓶頸。

---

## Amdahl 定律（Amdahl's Law）

### 公式

1967 年，Gene Amdahl 在論文 "Validity of the Single Processor Approach to Achieving Large-Scale Computing Capabilities" 裡給出這個公式：

設：
- T_serial = 程式的序列（不可平行）部分執行時間
- T_parallel = 程式的可平行部分執行時間
- T_total = T_serial + T_parallel（總執行時間，以 1 個處理器為基準，設為 1）
- P = 可平行比例 = T_parallel / T_total，即 0 ≤ P ≤ 1
- s = P 的序列比例 = 1 - P
- N = 處理器數量

在 N 個處理器上，可平行部分理想加速為 N 倍，序列部分不變：

```
T(N) = s × T_total + (P / N) × T_total
     = T_total × (s + P/N)
     = T_total × ((1-P) + P/N)
```

加速比（Speedup）：

```
Speedup(N) = T(1) / T(N) = T_total / [T_total × ((1-P) + P/N)]

             1
Speedup(N) = ─────────────
             (1-P) + P/N
```

當 N → ∞（無限多處理器），P/N → 0：

```
             1
Speedup_max = ─────
              1-P
```

**序列比例決定上限，和有多少處理器無關**。

### 算例：感受上限

設程式 95% 可平行（P = 0.95），序列部分 s = 0.05：

| 處理器數 N | Speedup |
|-----------|---------|
| 1 | 1.00× |
| 2 | 1.90× |
| 4 | 3.48× |
| 8 | 5.93× |
| 16 | 9.14× |
| 32 | 12.6× |
| 64 | 15.5× |
| 128 | 17.4× |
| ∞ | **20.0×** |

算法：N=8 時，Speedup = 1 / (0.05 + 0.95/8) = 1 / (0.05 + 0.1188) = 1 / 0.1688 ≈ 5.93

N→∞ 時，Speedup_max = 1/0.05 = **20 倍**——不管你有多少 GPU，**最多 20 倍**。

再極端一點，如果 P = 0.99（99% 可平行）：

| 處理器數 N | Speedup |
|-----------|---------|
| 10 | 9.17× |
| 100 | 50.2× |
| 1000 | 90.9× |
| ∞ | **100×** |

GPU T4 有 2560 個 CUDA Core，但 Amdahl 告訴我們：如果你的程式有 1% 序列，就算 2560 個核心全用到，上限是 100 倍——根本用不到 2560 倍。

### 視覺化：Amdahl 的加速曲線

```
Speedup
  100× │                              ___________  P=0.99
       │                         ____/
   50× │                    ____/
       │               ____/
   20× │         ______/              ___________  P=0.95
       │    ____/               _____/
   10× │ __/               ____/
       │/              ____/          ___________  P=0.80
    5× │           ___/         _____/
       │       ___/         ___/
    2× │   ___/         ___/
       │__/         ___/
    1× │────────────────────────────────────────→ N（處理器數）
       1    2    4   8   16   64  256  1024
```

每條曲線越到右邊越趨於水平——**加更多處理器的邊際效益遞減**，最終貼著那條水平天花板。

---

## Gustafson 定律（Gustafson's Law）

Amdahl 定律看起來很悲觀。1988 年，John Gustafson 提出了反駁："Reevaluating Amdahl's Law"。

Amdahl 的假設：問題的大小固定，只改處理器數量。

Gustafson 的觀點：真實世界裡，**有更多處理器，我們往往會解更大的問題**，而不是用更多核心做一樣大的事。

設：
- 用 N 個處理器時，總工作量定義為 1
- 序列部分佔 a，平行部分佔 (1-a)
- 如果只用 1 個處理器，平行部分需要 N 倍時間（因為要串行）

Scaled Speedup = a + N × (1-a) = N - a × (N-1)

這個公式看起來比 Amdahl 樂觀很多——加速比線性增長於 N，只被 `a × (N-1)` 的常數項拖慢。

**誰說得更對？**

兩者都對，但描述的是不同場景：
- **Amdahl**：固定問題規模，問「N 倍核心快多少」。適用於你不能放大問題的情境（例如跑一個已知大小的模型做推論）。
- **Gustafson**：隨核心數放大問題規模，問「在固定時間內能解多大的問題」。適用於科學計算（用更多 GPU 解更精細的物理模擬）或 AI 訓練（更大的 batch、更大的模型）。

在 GPU 程式設計裡，兩個框架都有用，取決於你的使用情境。

---

## Roofline Model

Amdahl 告訴你理論上限；Roofline 告訴你你的 kernel **現在** 卡在哪裡。

### 核心概念

一個 kernel 的執行性能受兩個資源限制：
1. **計算能力（compute throughput）**：GPU 每秒能做多少 FLOP（TFLOPS）。
2. **記憶體頻寬（memory bandwidth）**：GPU 每秒能從記憶體搬多少資料（GB/s）。

**算術強度（arithmetic intensity, AI）** = FLOP 數 / 搬動的 byte 數

在給定的算術強度下，你的 kernel 最多能跑多快？

```
Performance (FLOP/s) ≤ min(
    Peak_Compute,                          ← 計算能力上限
    AI × Peak_Bandwidth                    ← 頻寬乘以算術強度
)
```

這兩個上限，哪個小就卡在哪裡。

### T4 的 Roofline 圖

T4 規格（來源：NVIDIA T4 Datasheet）：
- FP32 峰值計算能力：**8.1 TFLOPS**（= 8.1 × 10¹² FLOP/s）
- 記憶體頻寬：**320 GB/s**（= 320 × 10⁹ byte/s）

**脊點（ridge point）** = 兩個屋頂的交叉算術強度：

```
脊點 AI = Peak_Compute / Peak_Bandwidth
         = 8.1 × 10¹² FLOP/s ÷ 320 × 10⁹ byte/s
         = 8100 / 320 FLOP/byte
         ≈ 25.3 FLOP/byte
```

**算術強度 < 25.3 FLOP/byte → memory-bound**（被頻寬屋頂壓住）
**算術強度 > 25.3 FLOP/byte → compute-bound**（被計算屋頂壓住）

```
Roofline Model（T4，FP32）

性能 (GFLOP/s)
  8100 │─────────────────────────────────────────── 計算屋頂 (8.1 TFLOPS)
       │                                    ╱
       │                                   ╱  compute-bound 區域
       │                                  ╱   (在這裡優化計算才有效)
       │                                 ╱
       │                                ╱
  4000 │                               ╱
       │                              ╱
       │                             ╱
  2000 │           memory-bound 區域╱
       │         (在這裡優化頻寬才有效)
       │                           ╱
  1000 │                          ╱  斜率 = 320 GB/s
       │                         ╱   (頻寬屋頂)
   500 │                        ╱
       │                       ╱
   200 │                      ╱
       │                     ╱
   100 │                    ╱
       │                   ╱
    50 │                  ╱
       │                 ╱
    25 │                ╱
       │               ╱
    10 │──────────────╳───────────────────────────→ 算術強度 (FLOP/byte)
       0    1    2    5   10   25   50  100  250
                           ↑
                        脊點 ~25 FLOP/byte
```

### 幾個常見 kernel 的算術強度

來算幾個例子：

**1. 向量加法 C[i] = A[i] + B[i]（N 個元素）**

搬的資料：讀 A（4N bytes）+ 讀 B（4N bytes）+ 寫 C（4N bytes）= 12N bytes
做的計算：N 次加法 = N FLOP
AI = N / 12N = **1/12 ≈ 0.083 FLOP/byte**

0.083 << 25，嚴重 memory-bound。優化計算量完全沒用，瓶頸在記憶體頻寬。

**2. SAXPY: Y[i] = a × X[i] + Y[i]**

搬的資料：讀 X（4N bytes）+ 讀 Y（4N bytes）+ 寫 Y（4N bytes）= 12N bytes
做的計算：N FMA（= 2N FLOP）
AI = 2N / 12N = **1/6 ≈ 0.167 FLOP/byte**

仍然 memory-bound，但比向量加法好一點。

**3. 矩陣乘法 C = A × B（N×N）**

搬的資料（最壞情況，無 shared memory）：
  每個 C[i][j] 需要讀 A 的一行（N 個 float，4N bytes）和 B 的一列（N 個 float，4N bytes）
  C 有 N² 個元素，共 (4N + 4N) × N² = 8N³ bytes
  寫 C：4N² bytes ≈ 8N³ bytes for large N

做的計算：每個 C[i][j] 做 N FMA = 2N FLOP，共 2N³ FLOP

AI = 2N³ / 8N³ = **1/4 FLOP/byte**

等等，這樣矩陣乘法也是 memory-bound？

**重點**：這是「無 shared memory」的最壞情況。有了 shared memory（tile-based），每個 tile 的資料從全域記憶體載入一次，在 shared memory 裡重複使用多次。有效算術強度可以提升到 O(tile_size)，讓大矩陣乘法進入 compute-bound 區域。這就是 Ch 6 的主題。

**4. Reduction（對 N 個元素求和）**

搬的資料：讀 N 個 float = 4N bytes
做的計算：N-1 次加法 ≈ N FLOP
AI = N / 4N = **0.25 FLOP/byte**

Memory-bound。優化 reduction 的重點是記憶體存取模式，不是計算量。

---

## 大多數 Kernel 是 Memory-bound 的現實

算術強度 < 25 的 kernel 都是 memory-bound。看看上面的例子：

- 向量加法：0.083 FLOP/byte
- SAXPY：0.167 FLOP/byte
- Reduction：0.25 FLOP/byte
- 點積：0.25 FLOP/byte
- Softmax：~0.3 FLOP/byte
- Layer Normalization：~0.5 FLOP/byte

這些全部是 memory-bound。**在 CUDA 課程裡學的絕大多數 kernel，瓶頸都是記憶體頻寬，不是計算能力**。

這意味著：
- 用更快的 GPU（更高 TFLOPS）對這些 kernel 沒幫助，除非同時提升頻寬
- 優化 kernel 的方向是**減少記憶體存取**（合併存取、使用 shared memory、減少資料搬移）
- 把 AI 算出來是開始優化前的第一步

---

## 底層機制：為什麼記憶體頻寬這麼難突破

**記憶體系統的層次**（T4，從快到慢）：

| 層次 | 容量 | 延遲 | 頻寬（per SM） |
|------|------|------|---------------|
| Register File | 256 KB | ~1 cycle | 幾十 TB/s |
| L1 Cache / Shared Memory | 64-96 KB | ~20 cycles | 幾 TB/s |
| L2 Cache | 4 MB | ~200 cycles | ~1.5 TB/s（整晶片）|
| Global Memory（GDDR6）| 16 GB | ~300-400 cycles | **320 GB/s**（整晶片）|

從表中可以看出：Global Memory 的頻寬跟 Register File 差了幾個數量級。

**為什麼 GDDR6 頻寬是 320 GB/s 不是更高？**

頻寬 = 資料寬度 × 時脈。T4 用 GDDR6，記憶體位元寬（bus width）256-bit，時脈約 5 GHz effective（DDR × 4 = GDDR6），頻寬 = 256/8 bytes × 5G = 160 GB/s × 2 = 320 GB/s。要提升頻寬，要麼加寬 bus（物理佈線面積上去了），要麼加快時脈（散熱問題），要麼換成 HBM（High Bandwidth Memory，A100 是 2000 GB/s）。T4 是 SXM2/PCIe 工作站卡，成本限制它用 GDDR6 而非 HBM。

---

## 如何在實際 Kernel 量測 AI

理論 AI 是「假設所有記憶體存取都打到 global memory」計算出來的。實際上 L1/L2 cache 會讓某些存取被快取，等效 AI 更高。profiler 可以量測實際的 DRAM 流量：

**(Colab 預期輸出，未在本機實測；在 Colab 選 GPU runtime 後執行可驗證)**

```python
# 使用 nvprof 量測 kernel 的記憶體流量（舊版，CUDA < 12）
!nvprof --metrics dram_read_transactions,dram_write_transactions ./your_kernel

# 使用 Nsight Compute（新版，CUDA >= 11.5）
!ncu --metrics sm__sass_thread_inst_executed_op_fadd_pred_on,\
              l1tex__t_bytes_pipe_lsu_mem_global_op_ld.sum \
     ./your_kernel
```

本課在 Ch 9 會詳細介紹 Nsight Compute 的使用方式。現在只需要知道：AI 是可以量測的，不只是算出來的。

---

## 對比與取捨

| 框架 | 問的問題 | 輸入 | 輸出 | 限制 |
|------|---------|------|------|------|
| Amdahl 定律 | 平行化最多快多少倍 | 序列比例 s、處理器數 N | 加速比上限 | 假設平行部分完全理想加速 |
| Gustafson 定律 | 固定時間內能解多大問題 | 序列比例 a、處理器數 N | Scaled speedup | 假設問題可以隨核心數放大 |
| Roofline model | 這個 kernel 卡在哪裡 | AI、Peak_Compute、Peak_BW | 是 compute/memory bound | 不考慮 cache 效果（最保守） |

這三個框架互補，不互斥：
- Amdahl 用來決定「這個程式的哪個部分值得投資」
- Gustafson 用來決定「要不要買更多 GPU 做更大的科學計算」
- Roofline 用來決定「這個 kernel 要從哪個方向優化」

---

## 踩雷集錦

**1. 「我的程式 95% 的時間跑在 GPU kernel 上，所以 P = 0.95，上限 20 倍」**
錯誤直覺：「跑在 kernel 上」= 可平行化。
正確認識：就算 kernel 佔了 95% 的時間，kernel 內部本身可能還有序列瓶頸（例如 reduction 的最後幾步，或依賴前一個 kernel 輸出的循環）。Amdahl 的 P 是指「可以在更多處理器上線性加速」的比例，這比「時間佔比」更嚴格。

**2. 「向量加法算術強度很低，換成 A100 就快了」**
錯誤直覺：更快的 GPU 對所有 kernel 都有幫助。
正確認識：如果 kernel 是 memory-bound，瓶頸是頻寬，不是 TFLOPS。A100 的 HBM2 頻寬 2000 GB/s 確實比 T4 的 320 GB/s 高很多，所以 memory-bound kernel 換 A100 是有幫助的——但如果你換一張計算更快但頻寬相同的卡，完全沒幫助。

**3. 「Roofline 算出來是 compute-bound，努力做 memory coalescing 也沒用」**
錯誤直覺：compute-bound = 記憶體存取不重要。
正確認識：Roofline 的 AI 是理論值。真正的 AI 可能因為 cache miss 而降低（effective AI 比理論 AI 低），讓你以為是 compute-bound 的 kernel 實際上因為亂序記憶體存取而 degraded 到 memory-bound 區域。永遠用 profiler 量測，不只靠算。

**4. 「Amdahl 的 P 可以用程式碼行數估計」**
錯誤直覺：for loop 佔 90% 的程式碼 = P = 0.90。
正確認識：P 是執行時間比例，不是程式碼行數比例。一個只有兩行的資料載入可能佔 80% 的執行時間。用 profiler 量測各部分的執行時間，再計算 P。

**5. 「Roofline 的脊點是 25 FLOP/byte，所以 AI > 25 就達到峰值 TFLOPS 了」**
錯誤直覺：只要 AI 夠高，就能跑到 8.1 TFLOPS。
正確認識：Roofline 是上限，不是保證。就算你的 AI 高到 compute-bound 區域，kernel 可能因為 low occupancy（active warp 不夠多，藏不住延遲）、分支發散（warp divergence）、register spilling 等原因，實際性能遠低於計算屋頂。Roofline 告訴你上限，profiler 告訴你為什麼沒到上限。

**6. 「Gustafson 推翻了 Amdahl，所以 Amdahl 過時了」**
錯誤直覺：新的定律取代舊的，Amdahl 不用看了。
正確認識：兩者描述不同場景。Gustafson 的「放大問題規模」假設在某些場景（科學計算、AI 訓練）成立，但不是普遍的。GPU 推論（inference）的 batch size 不能無限放大（延遲限制），Amdahl 更相關。

---

## 進階：再往深一層

### 記憶體牆（Memory Wall）

1995 年，William Wulf 和 Sally McKee 的論文 "Hitting the Memory Wall" 指出：CPU 的時脈增長速度遠快於記憶體頻寬的增長速度，兩者的差距越來越大。這個問題從未真正解決：

```
速度增長趨勢（粗略估計）

1990-2010 CPU TFLOPS：每 18 個月翻倍（Moore's Law + Dennard Scaling）
1990-2010 DRAM 頻寬：每 5-10 年翻倍

2010-2025 CPU TFLOPS：每 4-5 年翻倍（Dennard Scaling 終結）
2010-2025 DRAM 頻寬：每 3-4 年翻倍（HBM 出現）
```

Memory Wall 是 GPU 設計者持續面對的問題。解法包括：
- **HBM（High Bandwidth Memory）**：3D 堆疊記憶體，A100 = 2000 GB/s，H100 = 3350 GB/s
- **大型 L2 Cache**：A100 的 L2 有 40 MB，H100 有 50 MB
- **Tensor Core**：把整個矩陣乘法做成一條「指令」，讓算術強度暴增

### 「Roofline Ceilings」：多個層次的限制

真實的 Roofline model 有多個「天花板」（ceiling），從高到低：

```
FP32 峰值 (8.1 TFLOPS)          ─────────────────────── (理論上限)
   ↓ 被 ILP 不足限制
Achieved Compute Peak            ─── (實際計算上限，通常 70-90% of 峰值)
   ↓ 被 L1 Cache 頻寬限制
L2 Bandwidth Roof                ─── (L2 頻寬上限，~1.5 TB/s)
   ↓ 被 Global Memory 頻寬限制
DRAM Bandwidth Roof (320 GB/s)   ─── (最常見的瓶頸)
```

你的 kernel 可能被其中任何一層壓住。只有 profiler 能告訴你是哪層。

### 浮點精度的 Roofline 差異

T4 不同精度的算力不同：
- FP32：8.1 TFLOPS
- FP16（Tensor Core）：65 TFLOPS（搭配 FP16 累積器）
- INT8（Tensor Core）：130 TOPS
- FP64：0.254 TFLOPS（只有 FP32 的 1/32）

切換精度會改變 Roofline 圖的計算屋頂，但不改變頻寬屋頂。FP16 的脊點 = 65000 / 320 ≈ 203 FLOP/byte，也就是說要到 compute-bound 需要更高的算術強度——反而更容易是 memory-bound。

---

## 動手練習

1. 計算以下 kernel 的理論算術強度（AI），並根據 T4 的脊點（~25 FLOP/byte）判斷是 compute-bound 還是 memory-bound：
   - (a) 點積 `dot = sum(A[i] * B[i])` for N=10M 個 float
   - (b) Scale-and-bias `C[i] = alpha * A[i] + beta` for N=10M 個 float
   - (c) 元素級 sigmoid `C[i] = 1/(1+exp(-A[i]))` for N=10M 個 float（提示：exp 算多少 FLOP？用 20 FLOP 估算）

2. 用 Amdahl 定律回答：
   - 一個程式有 80% 可平行、20% 序列。用 T4 的 2560 個 CUDA Core，理論上能快多少倍？
   - 要讓這個程式加速 4 倍，需要最少多少個處理器？
   - 如果序列部分從 20% 降到 5%，無限處理器的上限從幾倍變成幾倍？

3. 假設你有一個 kernel，測量到執行時間 2 ms，期間從 global memory 讀寫了共 500 MB，做了 20 GFLOP 的計算：
   - 量測到的算術強度 AI 是多少？
   - T4 理論上這 500 MB 的傳輸需要幾 ms（用 320 GB/s 算）？
   - 這個 kernel 是 memory-bound 還是 compute-bound？

---

## 本章重點整理

- **Amdahl 定律**：序列比例 s = (1-P) 決定加速比上限 = 1/s。95% 可平行 → 上限 20×，無論多少處理器。
- **Gustafson 定律**：反駁 Amdahl 的前提——如果隨核心數放大問題規模，加速近似線性。兩者描述不同場景，互補。
- **脊點（Ridge Point）**：Roofline 中兩個屋頂的交叉點，T4 FP32 約 25 FLOP/byte。
- **AI < 脊點 → memory-bound**：優化方向是減少記憶體存取（coalescing、shared memory、tiling）。
- **AI > 脊點 → compute-bound**：優化方向是提高計算利用率（occupancy、指令並行）。
- 向量加法、SAXPY、reduction、softmax 等常見 kernel 全是 memory-bound。
- Roofline 是上限，不是保證；真正性能還受 occupancy、warp divergence、cache 效果等影響。
- 用 profiler（Nsight Compute）量測實際 AI 和各層次頻寬，不要只靠推算。

---

## 自我檢核

- [ ] 不看書，能推導出 Amdahl 定律的公式，並說明「無限多處理器」的上限是哪個表達式？
- [ ] T4 的 FP32 計算能力是 8.1 TFLOPS，記憶體頻寬是 320 GB/s，脊點是多少 FLOP/byte？自己算。
- [ ] 向量加法 `C[i] = A[i] + B[i]` 的算術強度是多少？是 compute-bound 還是 memory-bound？
- [ ] Amdahl 和 Gustafson 各適用於什麼場景？舉一個各自適用的實際例子。
- [ ] Roofline model 為什麼只是「上限」而不是實際性能？哪些因素讓你達不到上限？

---

## 延伸閱讀

1. **Amdahl, G. M. (1967). "Validity of the Single Processor Approach to Achieving Large-Scale Computing Capabilities."** AFIPS Conference Proceedings. Vol. 30, pp. 483-485.
   原始論文只有 2 頁，值得親讀一次。確認公式推導和原作者的原始意圖（他當時其實是在論證大型並行機器沒有商業價值——歷史諷刺）。

2. **Gustafson, J. L. (1988). "Reevaluating Amdahl's Law."** Communications of the ACM, 31(5), pp. 532-533.
   也只有 2 頁。讀完 Amdahl 原文後立刻讀，看 Gustafson 如何用 "scaled speedup" 翻轉論點。這兩篇加起來不到 5 頁，是本課最值得手動推導的數學。

3. **Williams, S., Waterman, A., & Patterson, D. (2009). "Roofline: An Insightful Visual Performance Model for Multicore Architectures."** Communications of the ACM, 52(4), pp. 65-76.
   Roofline model 的原始論文（CACM 2009）。讀 Section 2（The Roofline Model）和 Section 3（Ridge Point and Optimization Strategies）。前提：理解基本的記憶體層次架構。

4. **NVIDIA Nsight Compute 文件 — "Roofline Analysis"**
   [docs.nvidia.com/nsight-compute/ProfilingGuide/#roofline](https://docs.nvidia.com/nsight-compute/ProfilingGuide/)。官方 Roofline profiling 工作流，包含如何用 Nsight Compute GUI 畫出 Roofline 圖、如何解讀量測到的 AI。本課 Ch 9 會實作，這份文件是配套參考。

5. **Mark Harris, "How to Implement Performance Metrics"，NVIDIA Developer Blog**
   [developer.nvidia.com/blog/how-implement-performance-metrics-cuda-cc/](https://developer.nvidia.com/blog/how-implement-performance-metrics-cuda-cc/)。介紹如何用 CUDA events 手動量測 kernel 執行時間，是計算「量測到的 GFLOP/s」的第一步，直接服務本章的練習題 3。

---

下一章我們釐清「並行（concurrency）」和「平行（parallelism）」的差別——這兩個詞常被混用，但在 GPU 程式設計裡是截然不同的概念。

→ [Ch 3 — 並行 vs 平行](./03-concurrency-vs-parallelism.md)
