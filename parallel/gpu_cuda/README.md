# GPU / CUDA / 平行運算學習筆記：從平行思維到手刻優化 kernel

> 給有 C/C++ 底子、懂一點電腦架構、想真正搞懂 GPU 怎麼算得快的系統／ML 工程師。

這系列從「為什麼要平行、平行的天花板在哪」講起，先在 CPU 上把平行思維（SIMD / 多執行緒 / parallel patterns）建立起來，再進 GPU：SM 微架構 → CUDA 程式設計 → 效能優化重頭戲 → 深挖 PTX/SASS 看編譯器到底做了什麼 → 生態函式庫 → 最後把整套用到 AI/深度學習 kernel（GEMM、FlashAttention）。學完你能寫出 profiling 過、逼近硬體極限的 CUDA kernel，並且讀得懂它編出來的機器碼。

## 為什麼學這個？

- **會寫 ≠ 寫得快**：CUDA 的 API 一天就學得會，但一個沒優化的 kernel 和一個優化過的 kernel 差 10–100 倍。這門課的重點是後者——記憶體階層、coalescing、occupancy、warp divergence，這些才是分水嶺。
- **理解「為什麼快」而不只是「怎麼用」**：我們一路挖到 PTX/SASS，讓你看到自己的 C++ 變成什麼指令、編譯器幫你做了什麼、沒做什麼。這是把 GPU 從黑盒變成透明機器的關鍵。
- **職涯剛需**：HPC、深度學習系統、推論優化、編譯器後端——GPU 平行是這些領域的硬通貨。看得懂 FlashAttention 為什麼快、能自己寫 fused kernel，是這條路的門票。

## 先修知識

- **C/C++**（程度：能寫指標、struct、動態記憶體；C++ 到 class/template 基礎即可，Part 7 會用到一點）
- **電腦架構基礎**（程度：知道 cache、記憶體階層、pipeline 大概是什麼。完全沒有也能讀，Part 1–2 會補）
- **一點線性代數**（程度：知道矩陣乘法怎麼算。Part 7 會用到）
- 沒有也沒關係的：CUDA 經驗（零基礎開始）、GPU 硬體知識（Part 2 從頭建立）、深度學習（Part 7 會給足背景）

## 環境與驗證說明

- **主線環境**：CUDA Toolkit 12.x、Colab 免費層的 **NVIDIA T4（Turing, sm_75）**。Ampere（A100, sm_80）與 Hopper（H100, sm_90）特性（TMA、FP8、thread block cluster）以對照方式標注。
- **CPU 平行章節**（Part 1、練習 A）：gcc 14 + OpenMP + AVX2，範例都在本機真編譯執行，貼的是真實輸出。
- **CUDA 章節的誠實標注**：本課作者環境無 NVIDIA GPU，所有 CUDA 程式的輸出、profiling 數字皆標注為 **「Colab 預期行為／未在本機實測」**，並附上你該在 Colab 怎麼跑、怎麼驗證的步驟。PTX/SASS 反組譯結果同理——給你在自己環境重現的指令。凡是理論推導與硬體規格，都盡量對照官方文件（CUDA C++ Programming Guide、各架構 whitepaper）。

## 課程地圖

### Part 0 — 為什麼要 GPU（Ch 0–2）
- [Ch 0 環境搭建：Colab / CUDA Toolkit / nvcc / nvidia-smi](./00-environment-setup.md)
- [Ch 1 latency machine vs throughput machine：CPU 與 GPU 的哲學分歧](./01-cpu-vs-gpu-philosophy.md)
- [Ch 2 平行的天花板：Amdahl / Gustafson / Roofline](./02-parallelism-ceilings.md)

### Part 1 — 平行運算地基（先在 CPU 建立，Ch 3–6）
- [Ch 3 並行 vs 平行、硬體平行的層次](./03-concurrency-vs-parallelism.md)
- [Ch 4 SIMD 向量化：AVX intrinsics vs 自動向量化](./04-simd-vectorization.md)
- [Ch 5 多執行緒：OpenMP / pthreads / race / false sharing](./05-multithreading-openmp.md)
- [Ch 6 平行 pattern 導論：map / reduce / scan / stencil](./06-parallel-patterns.md)
- [練習 A：CPU 平行 — OpenMP reduction + AVX](./practice-a-cpu-parallel.md)

### Part 2 — GPU 硬體架構（Ch 7–11）
- [Ch 7 GPU 架構總覽：SIMT 與 throughput machine](./07-gpu-architecture-overview.md)
- [Ch 8 SM 剖析：CUDA core / warp scheduler / register file](./08-sm-anatomy.md)
- [Ch 9 記憶體階層：global / L2 / L1 / shared / register](./09-memory-hierarchy.md)
- [Ch 10 warp 與 SIMT 執行：lockstep 與 warp divergence](./10-warp-simt-execution.md)
- [Ch 11 佔用率（occupancy）：資源壓力的權衡](./11-occupancy.md)
- [練習 B：讀真實 GPU 規格 + 手算 occupancy](./practice-b-occupancy.md)

### Part 3 — CUDA 程式設計基礎（Ch 12–17）
- [Ch 12 第一個 kernel：host/device / launch config / indexing](./12-first-kernel.md)
- [Ch 13 記憶體管理：cudaMalloc / unified / pinned memory](./13-memory-management.md)
- [Ch 14 thread 階層與索引映射：1D/2D/3D grid](./14-thread-indexing.md)
- [Ch 15 錯誤處理與除錯：compute-sanitizer / cuda-gdb](./15-error-handling-debugging.md)
- [Ch 16 同步：__syncthreads / cooperative groups / grid 同步](./16-synchronization.md)
- [Ch 17 shared memory 與 tiling：手動 cache](./17-shared-memory-tiling.md)
- [練習 C：矩陣乘法 naive → tiled](./practice-c-matmul.md)

### Part 4 — CUDA 效能優化（重頭戲，Ch 18–25）
- [Ch 18 memory coalescing：global memory 存取樣式](./18-memory-coalescing.md)
- [Ch 19 bank conflict 深挖：shared memory 32 bank](./19-bank-conflict.md)
- [Ch 20 occupancy vs ILP：低佔用率也能跑滿](./20-occupancy-vs-ilp.md)
- [Ch 21 warp divergence 消除](./21-warp-divergence.md)
- [Ch 22 atomics 與 reduction 優化：warp shuffle](./22-atomics-reduction.md)
- [Ch 23 streams 與異步：overlap 計算/傳輸](./23-streams-async.md)
- [Ch 24 進階啟動：dynamic parallelism / persistent kernel](./24-advanced-launch.md)
- [Ch 25 profiling：Nsight Compute/Systems / roofline 判讀](./25-profiling.md)
- [練習 D：reduction 七版優化](./practice-d-reduction.md)

### Part 5 — 深挖微架構 / PTX / SASS（Ch 26–31）
- [Ch 26 編譯流程：nvcc / PTX / cubin / JIT](./26-compilation-pipeline.md)
- [Ch 27 讀 PTX：虛擬 ISA 與記憶體 space](./27-reading-ptx.md)
- [Ch 28 讀 SASS：cuobjdump / nvdisasm / 驗證優化](./28-reading-sass.md)
- [Ch 29 指令層級真相：latency hiding / dual issue](./29-instruction-level.md)
- [Ch 30 Tensor Core：WMMA / MMA / 混合精度](./30-tensor-core.md)
- [Ch 31 現代特性：cp.async / TMA / thread block cluster](./31-modern-features.md)
- [練習 E：改一行 code，對照 PTX/SASS 驗證](./practice-e-ptx-sass.md)

### Part 6 — 生態與進階（Ch 32–37）
- [Ch 32 函式庫：cuBLAS / cuDNN / CUB — 何時別自己寫](./32-libraries.md)
- [Ch 33 Thrust：STL 風格高階平行](./33-thrust.md)
- [Ch 34 多 GPU：NCCL / P2P / UVA](./34-multi-gpu.md)
- [Ch 35 CUDA graphs：砍掉 launch overhead](./35-cuda-graphs.md)
- [Ch 36 跨平台：OpenCL / SYCL / HIP / OpenACC](./36-cross-platform.md)
- [Ch 37 Triton：用 Python 寫 GPU kernel](./37-triton.md)

### Part 7 — AI / 深度學習 kernel（接 ml/，Ch 38–43）
- [Ch 38 GEMM 深挖：DL 的核心，register blocking](./38-gemm-deep-dive.md)
- [Ch 39 卷積：im2col / implicit GEMM / Winograd](./39-convolution.md)
- [Ch 40 softmax / layernorm / reduction kernel](./40-softmax-layernorm.md)
- [Ch 41 FlashAttention：memory-bound 與 online softmax](./41-flash-attention.md)
- [Ch 42 低精度：FP16 / BF16 / INT8 / FP8 量化](./42-low-precision.md)
- [Ch 43 PyTorch 底層：custom CUDA extension / kernel fusion](./43-pytorch-custom-kernel.md)
- [練習 F：手寫一個 fused kernel](./practice-f-fused-kernel.md)
- [Final Project：優化 GEMM / mini-FlashAttention + profiling 報告](./final-project-optimized-gemm.md)

## 學習方式建議

1. **讀完一章就動手**：CUDA 是手感活。開一個 Colab notebook，把每章的 kernel 打進去、改 launch config、看 Nsight 數字變化。光讀不跑學不會優化。
2. **故意把它弄慢**：把 coalesced 的存取改成 strided，看頻寬掉多少；把 shared memory 拿掉，看變慢幾倍。差異會刻在你腦裡。
3. **讀 SASS 驗證你的假設**：你以為編譯器會展開這個迴圈？`cuobjdump -sass` 看看它到底做了沒。Part 5 教你怎麼讀。
4. **對照 roofline 想問題**：每個 kernel 先問「我是 compute-bound 還是 memory-bound」，這決定你該優化什麼。

## 精選資料庫

整門課最值得反覆參照的資源，每章「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[CUDA C++ Programming Guide](https://docs.nvidia.com/cuda/cuda-c-programming-guide/)** — NVIDIA 官方
  - 整門課的權威來源；行為不符預期時的最終仲裁。第 5 章（Performance Guidelines）、附錄 K（Compute Capabilities）反覆查
- **[CUDA C++ Best Practices Guide](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/)** — NVIDIA 官方
  - Part 4 優化章的骨架；memory optimization、execution configuration 兩節必讀
- **《Programming Massively Parallel Processors》(4th ed.)** — Hwu, Kirk, El Hajj（Morgan Kaufmann, 2022）
  - GPU 平行的標準教科書；第 4–9 章涵蓋本課 Part 2–4 的 80%

### 推薦論文 / 技術報告

- **[Volkov & Demmel, "Benchmarking GPUs to Tune Dense Linear Algebra"](https://mc.stanford.edu/cgi-bin/images/6/65/SC08_Volkov_GPU.pdf)** — SC'08
  - 「低 occupancy 靠 ILP 也能跑滿」的原始論據，Ch 20 的核心
- **[Dao et al., "FlashAttention"](https://arxiv.org/abs/2205.14135)** — NeurIPS'22
  - Ch 41 的原始論文；把 attention 從 memory-bound 救回來的經典 IO-aware 設計
- **各架構 whitepaper**（[Volta](https://images.nvidia.com/content/volta-architecture/pdf/volta-architecture-whitepaper.pdf) / [Ampere](https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/nvidia-ampere-architecture-whitepaper.pdf) / [Hopper](https://resources.nvidia.com/en-us-tensor-core)）
  - Part 2、Part 5 對照硬體規格的一手來源

### 推薦部落格 / 文章

- **[Mark Harris, "Optimizing Parallel Reduction in CUDA"](https://developer.download.nvidia.com/assets/cuda/files/reduction.pdf)** — NVIDIA
  - 練習 D 的藍本；七版逐步優化 reduction，CUDA 優化教學的黃金範例
- **[NVIDIA Developer Blog — CUDA 標籤](https://developer.nvidia.com/blog/tag/cuda/)**
  - 「How to Access Global Memory Efficiently」「Using Shared Memory」等 Mark Harris 系列，coalescing/shared memory 寫得最清楚的來源
- **[Simon Boehm, "How to Optimize a CUDA Matmul Kernel"](https://siboehm.com/articles/22/CUDA-MMM)**
  - Final Project 的實戰藍本；從 naive 到接近 cuBLAS 的完整優化路徑，每步附 profiling

### 讀完本課之後

- **《Programming Massively Parallel Processors》進階章（Ch 15+）**（把 sparse、graph、DL 推得更深）
- **[CUTLASS](https://github.com/NVIDIA/cutlass)**（NVIDIA 的 GEMM 模板庫；讀完 Part 7 想看生產級 GEMM 怎麼寫，這裡是聖經）
- **[Triton 官方 tutorials](https://triton-lang.org/main/getting-started/tutorials/index.html)**（把 Ch 37 的 Triton 推到能寫 fused attention）

---

> 讀法建議：Part 0–1 可以快讀建立 mental model，Part 2 開始慢下來——GPU 架構的每個名詞後面都對應到後面優化章的一個技巧。Part 4、Part 5 是這門課的靈魂，值得反覆讀+動手。
