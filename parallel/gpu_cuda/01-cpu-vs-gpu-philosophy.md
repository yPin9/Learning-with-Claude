# Ch 1 — latency machine vs throughput machine

> **目標**：理解 CPU 和 GPU 的設計哲學為什麼根本不同，能用「藏延遲」解釋 GPU 為什麼能從大量執行緒裡搾出高吞吐量（throughput）。

---

## 為什麼需要這個？

你可能聽過「GPU 有幾千個核心，所以很快」。這個說法害了很多人——他們以為把 for loop 扔給 GPU 就能快一千倍，結果反而慢了。

真正的問題是：**CPU 和 GPU 的「快」是對不同問題的快**。CPU 是為了讓一條指令序列跑得盡可能快；GPU 是為了讓幾千條獨立任務同時往前跑。這是根本不同的設計取向，不是量的差異，是質的差異。

搞清楚這件事之後，你才能回答：
- 這個問題值不值得放到 GPU？
- GPU 跑很慢，是哪裡出了問題？
- Tensor Core 為什麼能快那麼多？

---

## 先建立直覺：晶片面積的分配

先看這張 ASCII 圖，這是 CPU 和 GPU 晶片面積（die area）的粗略分配：

```
CPU（以 Intel Core i7 為例）

┌────────────────────────────────────────────────────────────────┐
│                         L3 Cache (巨大)                        │
│                    ~20-30 MB，佔 die 面積 40-60%               │
├─────────────┬─────────────┬─────────────┬──────────────────────┤
│   Core 0    │   Core 1    │   Core 2    │   Core 3             │
│  ┌────────┐ │  ┌────────┐ │  ┌────────┐ │  ┌────────┐         │
│  │ L1/L2  │ │  │ L1/L2  │ │  │ L1/L2  │ │  │ L1/L2  │         │
│  │ Cache  │ │  │ Cache  │ │  │ Cache  │ │  │ Cache  │         │
│  ├────────┤ │  ├────────┤ │  ├────────┤ │  ├────────┤         │
│  │分支預測│ │  │分支預測│ │  │分支預測│ │  │分支預測│         │
│  │亂序執行│ │  │亂序執行│ │  │亂序執行│ │  │亂序執行│         │
│  │超純量  │ │  │超純量  │ │  │超純量  │ │  │超純量  │         │
│  │ ALU(少)│ │  │ ALU(少)│ │  │ ALU(少)│ │  │ ALU(少)│         │
│  └────────┘ │  └────────┘ │  └────────┘ │  └────────┘         │
└─────────────┴─────────────┴─────────────┴──────────────────────┘
 結論：大部分面積是 cache 和控制邏輯，ALU 是少數

GPU（以 NVIDIA T4 為例）

┌────────────────────────────────────────────────────────────────┐
│  SM0  │  SM1  │  SM2  │  SM3  │  SM4  │  SM5  │  SM6  │  SM7  │
│ ████  │ ████  │ ████  │ ████  │ ████  │ ████  │ ████  │ ████  │
│ ████  │ ████  │ ████  │ ████  │ ████  │ ████  │ ████  │ ████  │
│SM8-15 │SM16-23│SM24-31│SM32-39│SM40-47│SM48-55│SM56-63│SM64-71│
│ ████  │ ████  │ ████  │ ████  │ ████  │ ████  │ ████  │ ████  │
│ ████  │ ████  │ ████  │ ████  │ ████  │ ████  │ ████  │ ████  │
├────────────────────────────────────────────────────────────────┤
│          L2 Cache（相對小，2-6 MB）                            │
└────────────────────────────────────────────────────────────────┘
 每個 SM（Streaming Multiprocessor）裡有大量 ALU，少量控制邏輯
 T4 有 40 個 SM，每個 SM 有 64 個 CUDA Core（FP32 ALU）
 共 40 × 64 = 2560 個 CUDA Core
 結論：大部分面積是 ALU，cache 和控制邏輯是少數
```

這張圖就是兩者設計哲學的縮影：**CPU 的面積交給了「讓單執行緒快」的硬體；GPU 的面積交給了「讓很多執行緒同時跑」的計算單元**。

---

## CPU 為什麼堆這麼多「控制邏輯」？

CPU 的設計目標是：讓一條指令序列執行得越快越好。問題是：記憶體比 CPU 慢了幾十到幾百倍。

一個 L1 cache hit 約 4 cycles，L2 hit 約 12 cycles，L3 hit 約 40 cycles，DRAM 存取約 200-300 cycles。如果 CPU 每次等記憶體都呆坐著，光等待就占了大部分執行時間。

CPU 工程師的解法：**不讓 CPU 閒著**。具體手段有三：

### 1. Cache 層次結構

把可能用到的資料提前拉近來。L1 4 cycles，L2 12 cycles，L3 40 cycles，希望大部分存取都命中 cache。現代 CPU 的 L3 cache 動輒 20-30 MB，這個面積就是從 die 裡借來的。

**賭局**：cache 賭的是「程式有 locality（局部性）」——時間局部性（剛用過的資料馬上會再用）和空間局部性（用到某個地址，旁邊的也會用到）。大部分單執行緒程式確實有 locality，cache 賭對了。

### 2. 分支預測（branch prediction）

程式裡有大量 `if/else`、`for` loop。CPU 不等 branch 結果，**先猜**一條路徑，繼續執行。現代分支預測器準確率 95-99%，猜錯了就 flush pipeline（代價約 15-20 cycles），但猜對的時候等於「免費」執行了下一段程式碼。

這個電路本身就要佔大量 die 面積和電力。

### 3. 亂序執行（out-of-order execution, OoO）

指令之間沒有相依性的話，可以亂序執行。假設指令 A 在等記憶體，指令 B/C/D 不依賴 A 的結果，CPU 先執行 B/C/D，等 A 的記憶體回來再補上。這需要複雜的 Reorder Buffer（ROB）和指令排程硬體。

**結論**：CPU 的所有這些機制，都是為了「降低延遲（latency）」——讓一條單一的指令序列以最低延遲完成。**CPU 是 latency machine**。

---

## GPU 為什麼幾乎不這樣做？

GPU 的設計目標不同：**讓大量獨立計算任務的總吞吐量最高**。

GPU 不用猜測式執行，不用精密的分支預測器，L1/L2 cache 相對 CPU 小很多。原因是：GPU 有另一個辦法藏延遲。

### GPU 藏延遲的方式：換執行緒（warp switching）

GPU 把 thread 組織成 32 個一組的 **warp**（見 Ch 4 會深挖）。當某個 warp 在等記憶體存取（典型 200-400 cycles）時，Warp Scheduler 立刻切換到另一個 warp 繼續執行。只要有夠多的 warp 可以切換，等待的週期就被「填滿」了。

用圖來表示：

```
時間軸 ──────────────────────────────────────────────>

CPU（單執行緒，等記憶體）：
  執行  ▓▓▓  等 ░░░░░░░░░░░░  執行  ▓▓▓  等 ░░░░░░░░░░░░
        3ns      200ns          3ns       200ns
  CPU 利用率很低，大部分時間在等

GPU（大量 warp，互相掩蓋延遲）：
  Warp 0  ▓▓▓ 發出記憶體請求，等待中...
  Warp 1      ▓▓▓ 執行，等待中...
  Warp 2          ▓▓▓ 執行，等待中...
  ...
  Warp N                         ▓▓▓ 此時 Warp 0 資料回來了!
  Warp 0                             ▓▓▓ 繼續執行

  GPU 每個週期都在執行某個 warp，記憶體等待被「藏」進去了
```

這個技巧的成本是：GPU 需要同時追蹤非常多 warp 的狀態（每個 warp 的 register、program counter 等）。T4 每個 SM 最多可以有 32 個 active warp，這些狀態都放在 SM 內部的 register file（很大的 SRAM），讓 context switch 幾乎零延遲——不用 flush 到記憶體，也不用 restore。

這和 CPU 的 thread context switch（需要存到核心，花幾微秒）完全不同。

---

## 核心對比：什麼叫「算術密度（arithmetic intensity）」

這個概念在 Ch 2 的 Roofline model 裡會正式登場，但現在先建立直覺：

**算術密度（arithmetic intensity）= 做了多少浮點運算 / 搬了多少 bytes**

計算一個向量加法 `C[i] = A[i] + B[i]`：
- 每個元素：讀 A[i]（4 bytes）、讀 B[i]（4 bytes）、寫 C[i]（4 bytes），共 12 bytes
- 做 1 次加法，1 FLOP
- 算術密度 = 1/12 ≈ 0.08 FLOP/byte

計算矩陣乘法 C = A × B（N×N 矩陣）：
- 搬的資料量：O(N²) bytes
- 做的運算量：O(N³) FLOP
- 算術密度 = O(N) → 隨 N 增加而增加

算術密度低的程式（如向量加法）：大部分時間在等記憶體，是 **memory-bound**。GPU 的記憶體頻寬雖然很高（T4 320 GB/s），但如果算術密度太低，還是會被頻寬限制住。

算術密度高的程式（如矩陣乘法、神經網路 forward pass）：計算多到記憶體頻寬跟不上，是 **compute-bound**。這才是 GPU 能完全發揮 8.1 TFLOPS 的情境。

---

## 什麼工作適合 GPU？什麼不適合？

### 適合 GPU 的工作

**資料平行（data parallel）**：同一份操作對一大批資料做。向量加法、矩陣乘法、CNN 卷積、圖像處理、光線追蹤——每個元素的計算互相獨立，完美。

**算術密度高**：每從記憶體讀幾個 bytes，就做幾十到幾百個 FLOP。矩陣乘法的算術密度是 O(N)，大矩陣時高到記憶體頻寬不再是瓶頸。

**可以容忍執行緒之間沒有嚴格順序**：GPU 不保證執行緒執行順序，你的演算法必須能接受這一點（或用同步 primitive 明確控制）。

### 不適合 GPU 的工作

**序列相依（sequential dependency）**：下一步的計算依賴上一步的結果，無法並行。費氏數列、動態規劃的逐步推進、某些遞迴演算法——強制串行，GPU 的平行度幫不上忙。

**大量不規則分支（divergent branching）**：GPU 的 warp 裡 32 個 thread 必須走同一條指令路徑（見 Ch 4）。如果這 32 個 thread 走不同的 if/else 分支，GPU 要串行執行每一條分支，浪費大量算力。

**資料量小**：啟動一個 kernel 本身有固定的 overhead（幾十微秒）。如果計算量很小（處理 100 個元素），這個 overhead 比計算本身還大。

**不規則記憶體存取（scattered access）**：GPU 的記憶體合併存取（memory coalescing，見 Ch 5）效率極高，但如果你的存取模式是隨機的（例如 hash table lookup），性能會慘不忍睹。

| 工作類型 | 適合 GPU？ | 原因 |
|---------|-----------|------|
| 矩陣乘法 N×N (N>512) | 極適合 | 高算術密度、完全資料平行 |
| 向量加法 (N>1M) | 適合（記憶體瓶頸）| 資料平行但算術密度低 |
| 向量加法 (N<1K) | 不適合 | kernel overhead 吃掉所有好處 |
| 費氏數列第 N 項 | 不適合 | 完全序列相依 |
| 神經網路前向傳播 | 極適合 | 高算術密度、批次資料平行 |
| 字串搜尋（規則）| 適合 | 每個字元位置獨立掃 |
| 複雜 if/else 決策樹 | 不適合 | 分支發散讓 warp 效率崩潰 |
| 圖（Graph）BFS | 取決於實作 | 不規則記憶體存取是大挑戰 |
| 排序 | 部分適合 | GPU 有 radix sort 等特化算法 |
| 密碼學（AES, SHA256）| 適合 | 高算術密度、批次平行 |

---

## 底層機制：SM 的內部結構

T4 的一個 Streaming Multiprocessor（SM）裡有：

```
SM（Streaming Multiprocessor）
┌─────────────────────────────────────────────────────────┐
│  Warp Scheduler × 4   （每個 cycle 可發出 4 條指令）    │
├──────────┬──────────┬──────────┬───────────────────────┤
│ INT32    │ FP32     │ FP64     │ Tensor Core            │
│ ALU ×64  │ ALU ×64  │ ALU ×32  │ ×8（FP16/INT8 矩陣乘）│
├──────────┴──────────┴──────────┴───────────────────────┤
│  Load/Store Unit ×32（LD/ST 指令）                      │
│  Special Function Unit ×16（sin/cos/sqrt/exp）          │
├─────────────────────────────────────────────────────────┤
│  Register File：256 KB（可容納大量 warp 的暫存器狀態）  │
├─────────────────────────────────────────────────────────┤
│  L1 Cache / Shared Memory：64-96 KB（可程式化分配比例） │
└─────────────────────────────────────────────────────────┘
```

重點：
- **Register File 很大**（256 KB per SM）：這是 GPU 能同時追蹤幾十個 warp 狀態的原因。每個 warp 的 32 個 thread 各有最多 255 個 32-bit register，全部常駐在 SRAM 裡，context switch 完全不需要存讀記憶體。
- **Shared Memory 是可程式化的 L1**：你可以明確控制要多少 shared memory、多少 L1 cache（Ch 6 會用到）。CPU 的 cache 是自動管理的，GPU 讓你自己決定。
- **Warp Scheduler × 4**：每個 SM 有 4 個 warp scheduler，每個 cycle 可以各發出一條指令給不同的 warp，實現指令級的平行。

---

## 「大量執行緒」的成本

GPU 不是「執行緒越多越好」的無限遊戲。限制來自：

**Register 數量**：每個 SM 有 65536 個 32-bit register。如果一個 thread 用了 32 個 register，一個 SM 最多塞 65536 / 32 = 2048 個 thread（64 個 warp）。如果一個 thread 用了 64 個 register，只能塞 1024 個 thread（32 個 warp），**佔用率（occupancy）** 降低，能藏延遲的 warp 數減少，性能可能下降。

**Shared Memory 容量**：Shared memory 是 SM 內的多個 thread block 共享的，也是有限的（T4 每個 SM 最多 64 KB）。Thread block 要求的 shared memory 越多，同時能存活的 block 越少，佔用率越低。

**Thread block 大小**：每個 SM 最多 2048 個 thread，但 CUDA 限制每個 thread block 最多 1024 個 thread，且 block 數量受限於 SM 資源。調 block 大小是常見的調校手段。

這些限制的概念叫 **occupancy**（佔用率），Ch 7 會詳細討論。現在知道「GPU 有限制，不是想開多少 thread 就開多少」就夠了。

---

## 對比與取捨

| 特性 | CPU | GPU |
|------|-----|-----|
| 核心數 | 4-128（通用） | 數千（CUDA Core） |
| 單核心時脈 | 3-5 GHz | 1-2 GHz |
| Cache 大小（L3 vs L2） | 數十 MB | 2-6 MB |
| 控制邏輯 | 複雜（OoO、BP、superscalar）| 簡單 |
| 延遲策略 | 預測執行 + cache | 大量執行緒藏延遲 |
| 記憶體頻寬 | 50-100 GB/s（DDR5）| 300-2000 GB/s（HBM/GDDR6）|
| FP32 峰值算力（單顆） | ~1-3 TFLOPS | 8-300 TFLOPS |
| 適合工作 | 序列、低延遲、分支複雜 | 大量獨立、高算術密度 |
| 程式設計難度 | 低（單執行緒模型直覺）| 高（需理解 warp/memory 層次）|
| 啟動 kernel 的 overhead | 無（直接呼叫函式）| 幾十微秒 |

---

## 踩雷集錦

**1. 「GPU 有幾千個核心，一定比 CPU 快」**
錯誤直覺：核心數 ≈ 加速倍數，幾千倍加速理所當然。
正確認識：GPU 核心每個都很簡單，且需要問題高度平行化才能用到。序列問題、小資料量問題在 GPU 上反而更慢（kernel launch overhead + 無法充分利用平行度）。

**2. 「GPU 核心的時脈只有 CPU 的一半，所以每個核心比 CPU 慢一半」**
錯誤直覺：時脈可以直接比較計算速度。
正確認識：GPU core 每個 cycle 完成的事情跟 CPU core 不同。CPU core 一個 cycle 可以 OoO 同時發出多條指令；GPU warp 一個 cycle 讓 32 個 thread 同時執行同一條指令（SIMT）。不能只比時脈。

**3. 「GPU 只能做圖形（graphics），CPU 才能做計算」**
錯誤直覺：GPU 是顯示卡，就是為了畫圖的。
正確認識：GPGPU（General Purpose GPU Computing）在 2006 年 CUDA 出現後就是主流。現代資料中心 GPU（A100、H100）甚至沒有顯示輸出端口，純粹做計算。

**4. 「GPU 有記憶體，可以直接讀我的陣列」**
錯誤直覺：CPU 記憶體（DRAM）和 GPU 記憶體（VRAM/GDDR6）是同一個。
正確認識：CPU 和 GPU 有各自獨立的記憶體，透過 PCIe 匯流排（或 NVLink）連接。資料必須先用 `cudaMemcpy` 從 CPU 記憶體複製到 GPU 記憶體，才能在 kernel 裡存取。這個傳輸本身有成本（PCIe 頻寬約 16-32 GB/s，遠低於 GPU 記憶體的 320 GB/s）。

**5. 「藏延遲的效果不限個數，越多 warp 越好」**
錯誤直覺：多 warp 沒有壞處，開到最大就對了。
正確認識：warp 的狀態存在 register file，register file 有限制。warp 太多、每個 warp 用太多 register，就放不下，SM 上的 active warp 反而變少（occupancy 降低）。這是一個調校平衡點，不是越多越好。

**6. 「GPU 分支很慢，所以所有分支都要消除」**
錯誤直覺：只要有 if/else 就是壞的，要不惜一切改掉。
正確認識：只有「warp 內 32 個 thread 走不同分支（divergence）」才貴。如果所有 thread 都走同一分支（uniform branch），就跟沒有分支一樣快。消除 divergence 才是重點，不是消除所有分支。

---

## GPU 記憶體層次：為什麼頻寬是關鍵數字

GPU 的設計把 die 面積給了 ALU，而非 cache。這個選擇的代價是：**cache 小，容易 cache miss，需要頻繁存取 DRAM**。

T4 的記憶體層次（從近到遠）：

```
Thread register file  ~1 TB/s（per SM，幾乎免費）
        │
        ▼
Shared Memory / L1    ~10 TB/s（per SM，~20 cycles）
        │
        ▼
L2 Cache (4 MB)       ~1.5 TB/s（整晶片）
        │
        ▼
Global Memory (GDDR6) 320 GB/s（整晶片，~300 cycles 延遲）
        │
        ▼
PCIe / NVLink         ~16-32 GB/s（到 CPU 記憶體，慢一個數量級）
```

每個層次的頻寬差了大約一個數量級。**Global memory（DRAM）的 320 GB/s 是最常見的瓶頸**，因為絕大多數 kernel 都會存取它。

這也是為什麼 Ch 5 和 Ch 6 整章在講「如何減少 global memory 存取」——不是因為學術，是因為這決定你的 kernel 能不能快。

CPU 的策略（大 cache）和 GPU 的策略（大量執行緒藏延遲）在記憶體頻寬面前達到了相同的效果：讓 ALU 不閒著。只是達到方式完全不同。

---

## 「異質計算（heterogeneous computing）」的分工模型

CPU 和 GPU 不是競爭關係——是分工關係：

```
程式執行流程：

CPU（Host）                    GPU（Device）
    │                               │
    │  序列邏輯、條件判斷            │
    │  資料準備、IO 操作             │
    │  kernel 啟動與協調            │
    │                               │
    │──cudaMemcpy H→D──────────────>│
    │                               │  大量平行計算
    │──kernel launch───────────────>│  （Vector Add、矩陣乘法、
    │                               │   神經網路 forward pass）
    │<─cudaDeviceSynchronize────────│
    │                               │
    │──cudaMemcpy D→H──────────────>│
    │                               │
    │  分析結果、決定下一步          │
```

真實的深度學習推論流程就是這樣：CPU 負責讀資料、做 batch、決定迭代策略；GPU 負責矩陣乘法和激活函式的大量計算。

**PCIe 傳輸是隱藏的瓶頸**：CPU-GPU 之間的資料搬移（`cudaMemcpy`）走 PCIe，頻寬約 16-32 GB/s，遠低於 GPU 記憶體的 320 GB/s。如果你的程式頻繁在 CPU 和 GPU 之間傳資料，傳輸成本可能超過計算本身。解法：把計算盡量留在 GPU 上，減少來回搬移（Ch 8 的 CUDA Streams 和 Unified Memory 相關）。

---

## 進階：再往深一層

### SIMT vs SIMD

CPU 也有 SIMD（Single Instruction Multiple Data），例如 AVX-512 可以一條指令對 16 個 float 做加法。但 SIMD 是「真正強迫你同時對一組資料做同一件事」——你必須顯式地打包資料。

GPU 的 SIMT（Single Instruction Multiple Threads）表面上看起來類似：32 個 thread 同時執行同一條指令。但 SIMT 的關鍵差異是：每個 thread 有自己的 register（`threadIdx`、指針等），可以有不同的記憶體存取地址。SIMD 看起來像一個運算對多個槽（slot）；SIMT 看起來像多個有自己狀態的執行緒同時跑。

這讓 SIMT 寫起來更像寫單執行緒程式，但有 warp divergence 的代價。

### Volta 以後的 Independent Thread Scheduling

Volta 架構（V100，2017）之前，一個 warp 的 32 個 thread 共用一個 program counter，要 diverge 就要 serialize。Volta 之後每個 thread 有獨立的 program counter，理論上可以在 warp 內有更細的交錯執行（inter-thread interleaving）。這讓某些演算法（例如 warp-level synchronization primitives）更靈活。T4 是 Turing（sm_75），也有 Independent Thread Scheduling。

---

## 動手練習

1. 用本章的概念，預測下列哪個工作更適合 GPU，並說明原因：
   - (a) 把一個 1000×1000 矩陣轉置
   - (b) 從 10 個數字裡找最大值
   - (c) 跑一個 100 層的神經網路（每層 4096 個神經元，batch size 32）
   - (d) 用遞迴實作快速排序

2. T4 有 40 個 SM，每個 SM 有 64 個 FP32 CUDA Core，時脈約 1.59 GHz（boost）。用這些數字算出 T4 的 FP32 峰值算力（TFLOPS），並跟 NVIDIA 官方的 8.1 TFLOPS 比對。（提示：每個 CUDA Core 每個 cycle 做 1 個 FMA = 2 FLOP）

3. 解釋為什麼 GPU 的 register file（256 KB per SM）比 L1 cache 還重要。如果 register file 不夠大，會發生什麼事？

---

## 本章重點整理

- CPU 是 **latency machine**：用大量 cache、分支預測、亂序執行優化單執行緒延遲。
- GPU 是 **throughput machine**：用大量簡單 ALU + 大量 warp 切換藏記憶體延遲，優化整體吞吐量。
- GPU 藏延遲的方式：當一個 warp 在等記憶體，warp scheduler 立刻切換到另一個 warp。零 context switch 成本，因為所有 warp 的 register 常駐 SRAM。
- **算術密度（arithmetic intensity）= FLOP / byte**：決定程式是 compute-bound 還是 memory-bound。
- 適合 GPU 的工作：資料平行、算術密度高、可以容忍執行緒無序。
- 不適合 GPU 的工作：序列相依、大量 warp 內分支發散、資料量小、不規則記憶體存取。
- T4：40 SM × 64 FP32 core = 2560 CUDA Core，FP32 峰值 8.1 TFLOPS，記憶體頻寬 320 GB/s，16 GB GDDR6。

---

## 自我檢核

- [ ] 不看書，能畫出 CPU 和 GPU 的 die 面積分配示意圖，並說出差異的原因？
- [ ] 「藏延遲」這個機制需要哪兩個前提條件（一個硬體前提，一個軟體前提）才能有效？
- [ ] 算術密度低的程式，在 GPU 上的瓶頸是什麼？算術密度高的呢？
- [ ] 為什麼 GPU 的 context switch 幾乎零成本，而 CPU 的 thread context switch 要幾微秒？
- [ ] 什麼叫 warp divergence？只要有 if/else 就會發生嗎？

---

## 延伸閱讀

1. **CUDA C++ Programming Guide §4.1-4.2（SIMT Architecture, Hardware Multithreading）**
   [docs.nvidia.com/cuda/cuda-c-programming-guide/#simt-architecture](https://docs.nvidia.com/cuda/cuda-c-programming-guide/)。官方文件，精確描述 warp scheduling 和 independent thread scheduling 的語意。讀完本章後讀，著重 §4.1（SM 的 warp 排程模型）。前提：已理解 warp 概念。

2. **NVIDIA Turing Architecture Whitepaper (2018)**
   [images.nvidia.com/akamai/technology/turing/NVIDIA-Turing-Architecture-Whitepaper.pdf](https://images.nvidia.com/akamai/technology/turing/NVIDIA-Turing-Architecture-Whitepaper.pdf)。T4 所用架構的官方架構說明，含 SM 組成圖、Tensor Core 架構、RT Core。讀 Section 3（SM Architecture）對應本章的 SM 內部結構。

3. **《Programming Massively Parallel Processors》第 4 版，Chapter 4（Memory and Data Locality）**
   Kirk & Hwu 的教科書，Ch 4 從記憶體層次切入解釋為什麼 GPU 需要大量執行緒藏延遲。搭配本章的「藏延遲」直覺讀，強化底層理解。無特別前提。

4. **"Demystifying GPU Microarchitecture through Microbenchmarking" (Aamodt et al.)**
   透過 microbenchmark 逆向工程 NVIDIA GPU 的各種微架構細節（cache latency、warp 切換延遲、SM 數量等）。適合已理解 GPU 基本架構後，想驗證理論理解是否正確的讀者。

5. **Mark Harris, "An Easy Introduction to CUDA C and C++"，NVIDIA Developer Blog**
   [developer.nvidia.com/blog/easy-introduction-cuda-c-and-c/](https://developer.nvidia.com/blog/easy-introduction-cuda-c-and-c/)。Mark Harris 是 NVIDIA 的 Distinguished Engineer，這篇 blog 是業界最常被引用的 CUDA 入門，補充了許多本章概念的實作角度。讀完本章後讀，鞏固直覺。

---

下一章我們用數學量化「平行化能快多少」——Amdahl 定律告訴你上限在哪，Roofline model 告訴你現在的 kernel 卡在哪。

→ [Ch 2 — 平行的天花板](./02-parallelism-ceilings.md)
