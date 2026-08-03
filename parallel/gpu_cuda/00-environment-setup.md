# Ch 0 — 環境搭建

> **目標**：讓第一個 CUDA kernel 在你的螢幕上印出字，從此以後不再對著「找不到 nvcc」乾瞪眼。
> **環境**：CUDA 12.x（Colab 預裝）+ Google Colab T4（sm_75，16 GB）。本機/WSL 路徑另附於章末。

---

## 為什麼選 Colab？

在學 CUDA 之前，你可能要花好幾天光搞環境：裝驅動、裝 CUDA Toolkit、處理版本衝突、重開機、再處理版本衝突。我們不做這件事——先讓程式跑起來，再去琢磨底層。

Google Colab 提供免費的 NVIDIA T4 GPU，已預裝驅動與 CUDA Toolkit，開一個 notebook 就能用 `nvcc` 編譯。限制是：每天有使用時間配額、session 閒置會斷線、不能跑需要多 GPU 的範例。這些對本課前半段完全不是問題。

**等你需要的東西：**
- 免費 Colab（T4，16 GB）：覆蓋 Part 0～Part 4 的所有範例
- Colab Pro / Pro+（A100 40/80 GB）：Part 5 以後的效能實驗，非必要
- 本機 NVIDIA GPU + CUDA Toolkit 12.x：最佳體驗，但非必要

---

## 開 Colab 並選 GPU Runtime

1. 前往 [colab.research.google.com](https://colab.research.google.com)，新建一個 notebook（或直接開這門課附的 `ch00_hello_gpu.ipynb`）。
2. 點上方選單：**Runtime → Change runtime type**。
3. 在 Hardware accelerator 選 **T4 GPU**，按 Save。
4. 等 runtime 重連（約 10-20 秒），右上角會出現 RAM / Disk 指示條。

> **注意**：如果沒選 GPU，所有 `%%cuda` cell 都會回傳「CUDA driver version is insufficient」或安靜地跑 CPU——看起來沒報錯，但根本沒用到 GPU。養成習慣：**每次開 notebook 先確認 runtime 類型**。

---

## 第一步：`nvidia-smi` 確認有 GPU

在第一個 cell 執行：

```python
!nvidia-smi
```

**(Colab 預期輸出，未在本機實測；在 Colab 選 GPU runtime 後執行可驗證)**

```
Sun Aug  2 03:14:22 2026
+-----------------------------------------------------------------------------+
| NVIDIA-SMI 525.105.17   Driver Version: 525.105.17   CUDA Version: 12.0    |
|-------------------------------+----------------------+----------------------+
| GPU  Name        Persistence-M| Bus-Id        Disp.A | Volatile Uncorr. ECC |
| Fan  Temp  Perf  Pwr:Usage/Cap|         Memory-Usage | GPU-Util  Compute M. |
|                               |                      |               MIG M. |
|===============================+======================+======================|
|   0  Tesla T4          Off   | 00000000:00:04.0 Off |                    0 |
| N/A   52C    P8    11W /  70W |      2MiB / 15360MiB |      0%      Default |
|                               |                      |                  N/A |
+-------------------------------+----------------------+----------------------+

+-----------------------------------------------------------------------------+
| Processes:                                                                  |
|  GPU   GI   CI        PID   Type   Process name                  GPU Memory |
|        ID   ID                                                   Usage      |
|=============================================================================|
|  No running processes found                                                 |
+-----------------------------------------------------------------------------+
```

### 逐行解析

| 欄位 | 數值（範例） | 意義 |
|------|------------|------|
| `NVIDIA-SMI 525.105.17` | 驅動版本 | 不是 CUDA Toolkit，是 kernel-space 驅動；可以比 Toolkit 新，但不能舊 |
| `Driver Version: 525.105.17` | 同上 | 在 Colab 你無法自己裝，固定版本由 Google 維護 |
| `CUDA Version: 12.0` | 驅動支援的最高 CUDA 版本 | 實際使用的 Toolkit 版本由 `nvcc --version` 確認，可以比這個舊 |
| `Tesla T4` | GPU 型號 | Turing 架構，Compute Capability 7.5（sm_75） |
| `Persistence-M: Off` | persistence mode | 關閉表示 GPU 閒置會斷電，可降低電費；Colab 預設關 |
| `15360MiB` | 顯示記憶體上限 | T4 共 16 GB GDDR6，nvidia-smi 顯示為 15360 MiB（≈15 GiB）；其中 2 MiB 已被 driver 佔用 |
| `Pwr: 11W / 70W` | 目前功耗 / TDP | 閒置時很低，跑 kernel 可能飆到 60-70W |
| `Temp: 52C` | GPU 核心溫度 | T4 TDP 70W，散熱設計保守；Colab 機器室溫較高所以閒置也有 50°C+ |
| `GPU-Util: 0%` | GPU 計算核心使用率 | 沒有 kernel 在跑，所以 0%；這不代表 GPU 沒在用（顯示驅動等不計算在內） |
| `Compute M.: Default` | Compute Mode | Default = 允許多個 process 共用 GPU；Exclusive = 只允許一個 |

**Compute Capability 7.5（sm_75）** 是 Turing 架構的代號。不同架構支援不同 CUDA 功能：例如 `__ballot_sync`、warp-level primitives 需要 sm_70+，Tensor Core FP16 需要 sm_75+。本課範例全用 T4 可跑的功能。

---

## CUDA Toolkit 版本確認

```python
!nvcc --version
```

**(Colab 預期輸出，未在本機實測；在 Colab 選 GPU runtime 後執行可驗證)**

```
nvcc: NVIDIA (R) Cuda compiler driver
Copyright (c) 2005-2022 NVIDIA Corporation
Built on Wed_Sep_21_10:41:10_PDT_2022
Cuda compilation tools, release 12.0, V12.0.76
Build cuda_12.0.r12.0/compiler.31968024_0
```

`nvcc` 是 CUDA C++ 的編譯器驅動（compiler driver），類似 `gcc`。它本身不做所有編譯工作，而是：
1. 把 `.cu` 檔裡的 `__global__` / `__device__` 函式分離，送 PTX assembler 編譯成 GPU 指令（SASS）
2. 把 host 端的 C++ 程式碼送給系統的 `gcc` / `cl.exe` 編譯
3. 把兩份目標檔連結成一個可執行檔

你也可以查 CUDA runtime 版本（程式中用的是 runtime API，不是 nvcc 版本）：

```python
import subprocess
result = subprocess.run(['python3', '-c',
    'import ctypes; lib = ctypes.CDLL("libcudart.so"); '
    'ver = ctypes.c_int(); lib.cudaRuntimeGetVersion(ctypes.byref(ver)); '
    'print(f"CUDA Runtime: {ver.value // 1000}.{(ver.value % 1000) // 10}")'],
    capture_output=True, text=True)
print(result.stdout)
```

---

## 第一個 CUDA Kernel：Hello from GPU

建立一個新 cell，貼入以下程式碼並執行：

```python
# Colab cell 1：把 C++ 原始碼寫到檔案
hello_cu = """
#include <cstdio>

// __global__ 表示這個函式在 GPU 上執行，從 CPU 呼叫
__global__ void hello_kernel() {
    // blockIdx.x、threadIdx.x 是 CUDA 內建變數，代表執行緒的座標
    printf("Hello from GPU! block=%d, thread=%d\\n",
           blockIdx.x, threadIdx.x);
}

int main() {
    // <<<blocksPerGrid, threadsPerBlock>>>：執行配置（launch config）
    // 這裡啟動 2 個 block，每 block 4 個 thread，共 8 個執行緒
    hello_kernel<<<2, 4>>>();

    // 等 GPU 跑完（GPU 是非同步的，不加這行有時看不到輸出）
    cudaDeviceSynchronize();
    return 0;
}
"""

with open('/tmp/hello.cu', 'w') as f:
    f.write(hello_cu)
print("原始碼已寫入 /tmp/hello.cu")
```

```python
# Colab cell 2：用 nvcc 編譯
!nvcc -o /tmp/hello /tmp/hello.cu
print("編譯完成")
```

```python
# Colab cell 3：執行
!/tmp/hello
```

**(Colab 預期輸出，未在本機實測；在 Colab 選 GPU runtime 後執行可驗證)**

```
Hello from GPU! block=0, thread=0
Hello from GPU! block=0, thread=1
Hello from GPU! block=0, thread=2
Hello from GPU! block=0, thread=3
Hello from GPU! block=1, thread=0
Hello from GPU! block=1, thread=1
Hello from GPU! block=1, thread=2
Hello from GPU! block=1, thread=3
```

> **實際上輸出順序可能不同**：GPU 執行緒沒有順序保證。你可能看到 block=1 的輸出在 block=0 前面，或同一個 block 內的 thread 亂序。這是正常的——這就是 GPU 的本質，我們在 Ch 3 會深入討論。

### 程式碼解析

| 關鍵詞 | 含義 |
|--------|------|
| `__global__` | 函式限定詞（qualifier）：在 GPU 執行，從 CPU 呼叫 |
| `__device__` | 在 GPU 執行，只能從 GPU 呼叫（本課後面會用到） |
| `__host__` | 在 CPU 執行（預設，不用特別寫）|
| `<<<2, 4>>>` | 執行配置（launch configuration）：2 個 block，每 block 4 個 thread |
| `blockIdx.x` | 當前執行緒所在的 block 編號（0 到 gridDim.x-1） |
| `threadIdx.x` | 當前執行緒在 block 內的編號（0 到 blockDim.x-1） |
| `cudaDeviceSynchronize()` | 等 GPU 上所有已啟動的 kernel 完成 |

---

## CUDA 執行模型快速地圖

這是課程後面會深挖的概念，現在只需要有個印象：

```
你的程式（host code，跑在 CPU）
    │
    │  cudaMalloc() — 在 GPU 上分配記憶體
    │  cudaMemcpy() — CPU 記憶體 → GPU 記憶體
    │
    ▼
kernel<<<grid, block>>>() — 啟動 GPU kernel
    │
    ▼ GPU 上的執行結構：

Grid（網格）
├── Block 0                 ← blockIdx.x = 0
│   ├── Thread 0  (threadIdx.x = 0)
│   ├── Thread 1  (threadIdx.x = 1)
│   ├── ...
│   └── Thread 31 (threadIdx.x = 31)   ← 這 32 個 thread 構成一個 warp
│   ├── Thread 32 ... Thread 63         ← 下一個 warp
│   └── ...（最多 1024 個 thread per block）
├── Block 1                 ← blockIdx.x = 1
│   └── ... （跟 Block 0 結構相同）
└── ...（最多 2³¹-1 個 block per grid，但 SM 有限）

    │
    │  cudaDeviceSynchronize() — CPU 等 GPU 跑完
    │  cudaMemcpy() — GPU 記憶體 → CPU 記憶體
    ▼
結果回到 CPU 端
```

三個概念一起記：
- **Grid** = 整次 kernel launch 的全部執行緒
- **Block** = 一組可以互相透過 shared memory 溝通、可以 `__syncthreads()` 同步的執行緒
- **Warp** = GPU 硬體排程的基本單位（32 個 thread），程式設計者不用顯式控制，但必須理解它的行為

每個 thread 透過 `blockIdx`、`threadIdx`、`blockDim`、`gridDim` 這四個內建變數知道自己的位置。

```cpp
// 計算「全域 ID」的標準公式（最常用）
int i = blockIdx.x * blockDim.x + threadIdx.x;

// 舉例：<<<4, 8>>>，共 32 個 thread
// blockDim.x = 8
// Block 0, Thread 3：i = 0 * 8 + 3 = 3
// Block 2, Thread 5：i = 2 * 8 + 5 = 21
```

Ch 3 和 Ch 4 會完整深挖這個模型。現在先記住這個公式能讓你把 thread 對應到陣列元素。

---

## 第二個 Kernel：真正做計算

Hello world 只是熱身。來看一個最小的「有意義」的 kernel：向量加法。

```python
vector_add_cu = """
#include <cstdio>

// 每個 thread 負責計算 result[i] = a[i] + b[i] 中的一個 i
__global__ void vector_add(const float* a, const float* b, float* result, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) {          // 防止越界（n 不一定是 blockDim.x 的倍數）
        result[i] = a[i] + b[i];
    }
}

int main() {
    const int N = 1024;
    const size_t size = N * sizeof(float);

    // 1. 在 CPU 端準備資料
    float h_a[N], h_b[N], h_result[N];
    for (int i = 0; i < N; i++) {
        h_a[i] = (float)i;
        h_b[i] = (float)(N - i);
    }

    // 2. 在 GPU 上分配記憶體
    float *d_a, *d_b, *d_result;
    cudaMalloc(&d_a, size);
    cudaMalloc(&d_b, size);
    cudaMalloc(&d_result, size);

    // 3. 把資料從 CPU 複製到 GPU
    cudaMemcpy(d_a, h_a, size, cudaMemcpyHostToDevice);
    cudaMemcpy(d_b, h_b, size, cudaMemcpyHostToDevice);

    // 4. 啟動 kernel：每個 block 256 個 thread，需要幾個 block？
    int threads_per_block = 256;
    int blocks = (N + threads_per_block - 1) / threads_per_block;  // ceiling division
    vector_add<<<blocks, threads_per_block>>>(d_a, d_b, d_result, N);

    // 5. 等 GPU 完成
    cudaDeviceSynchronize();

    // 6. 把結果從 GPU 複製回 CPU
    cudaMemcpy(h_result, d_result, size, cudaMemcpyDeviceToHost);

    // 7. 驗證
    bool correct = true;
    for (int i = 0; i < N; i++) {
        if (h_result[i] != N) {  // a[i] + b[i] = i + (N-i) = N，恆為 1024
            correct = false;
            printf("Error at i=%d: %f\\n", i, h_result[i]);
            break;
        }
    }
    printf("%s\\n", correct ? "Vector add PASSED!" : "Vector add FAILED!");

    // 8. 釋放 GPU 記憶體
    cudaFree(d_a);
    cudaFree(d_b);
    cudaFree(d_result);
    return 0;
}
"""

with open('/tmp/vector_add.cu', 'w') as f:
    f.write(vector_add_cu)
```

```python
!nvcc -o /tmp/vector_add /tmp/vector_add.cu && /tmp/vector_add
```

**(Colab 預期輸出，未在本機實測；在 Colab 選 GPU runtime 後執行可驗證)**

```
Vector add PASSED!
```

這個程式展示了完整的 CUDA 程式流程：`cudaMalloc` → `cudaMemcpy`（H2D）→ kernel launch → `cudaDeviceSynchronize` → `cudaMemcpy`（D2H）→ `cudaFree`。後面每一章的範例都是這個骨架的變體。

**ceiling division `(N + block_size - 1) / block_size`** 是 CUDA 程式裡最常寫的表達式之一。原因：N 不一定是 block_size 的倍數，整除會少啟動一個 block，導致最後幾個元素沒有 thread 處理。所以要往上取整（ceiling），再在 kernel 裡用 `if (i < n)` 防止越界。

---

## 本機 / WSL 裝 CUDA（參考路徑）

如果你有 NVIDIA GPU 的機器，流程是：

**Linux / WSL2：**
```bash
# 確認有 NVIDIA GPU
lspci | grep -i nvidia

# 安裝 CUDA Toolkit（以 Ubuntu 22.04 + CUDA 12.3 為例）
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update
sudo apt-get -y install cuda-toolkit-12-3

# 把 nvcc 加進 PATH
echo 'export PATH=/usr/local/cuda/bin:$PATH' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH' >> ~/.bashrc
source ~/.bashrc

# 確認
nvcc --version
nvidia-smi
```

**WSL2 的特殊注意**：WSL2 不需要在 Linux 側裝 GPU 驅動，只要在 Windows 側裝好 NVIDIA 驅動即可；WSL2 透過 `wsl2-x86_64.inf` 的 DXGKRNL 橋接訪問 GPU。但 CUDA Toolkit 要在 Linux 側裝（只裝 Toolkit，不裝驅動）。

**版本對應原則**：驅動版本決定支援的最高 CUDA 版本（查 [CUDA Toolkit Release Notes](https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/) 的相容表）。Toolkit 可以比驅動支援的最高版本舊，但不能新。

---

## 踩雷集錦

**1. 沒選 GPU Runtime 卻沒看到報錯**
錯誤直覺：執行沒錯誤，kernel 一定有在 GPU 上跑。
正確認識：如果忘了選 GPU runtime，`nvcc` 編譯可能失敗，但有時 Colab 會模擬執行；更糟的是你以為在跑 GPU 但其實跑的是 CPU fallback。永遠先 `!nvidia-smi`。

**2. `nvidia-smi` 的 CUDA Version 跟 `nvcc --version` 不一樣**
錯誤直覺：兩個數字要一樣才對。
正確認識：`nvidia-smi` 顯示的是驅動支援的最高 CUDA 版本（上限），`nvcc` 顯示的是安裝的 Toolkit 版本（實際版本）。Toolkit ≤ 驅動上限才合法。

**3. 沒有 `cudaDeviceSynchronize()` 就看不到 `printf` 輸出**
錯誤直覺：GPU kernel 的 `printf` 應該跟 CPU 的一樣即時印出。
正確認識：GPU `printf` 輸出存在一個 circular buffer，只有在 `cudaDeviceSynchronize()` 或程式結束時才 flush 到 stdout。開發階段每個 kernel 後都要加。

**4. WSL2 裡在 Linux 側裝了 GPU 驅動導致衝突**
錯誤直覺：WSL2 是 Linux 環境，當然要在 Linux 側裝 NVIDIA 驅動。
正確認識：WSL2 的 GPU 驅動由 Windows 側提供，不應該在 Linux 側再裝驅動（只裝 Toolkit）。裝了會產生版本衝突，`nvidia-smi` 跳奇怪錯誤。

**5. 執行緒輸出順序跟程式碼的 block/thread 編號順序不一致**
錯誤直覺：block 0 一定比 block 1 先印出來。
正確認識：GPU 執行緒沒有執行順序的保證。Warp scheduler 按照自己的邏輯排程，輸出順序每次可能不同。如果你的程式依賴執行緒順序，那是設計錯誤。

---

## 動手練習

1. 開一個新的 Colab notebook，選 T4 GPU runtime，執行 `!nvidia-smi` 並截圖記錄 CUDA Version 和記憶體容量。
2. 把 `hello_kernel<<<2, 4>>>()` 改成 `<<<4, 8>>>`，預期輸出幾行？執行後驗證。
3. 在 kernel 裡計算 `int global_id = blockIdx.x * blockDim.x + threadIdx.x;`，並把 `global_id` 也印出來。觀察 global_id 是否連續（提示：不一定按順序，但數字集合應該是 0-31）。

---

## 本章重點整理

- Colab T4 是學 CUDA 最快的起跑點；先讓程式跑，再琢磨底層。
- `nvidia-smi` 能看到：驅動版本、CUDA 上限版本、GPU 型號、記憶體用量、溫度功耗。
- T4 是 Turing sm_75，16 GB GDDR6，本課程基準環境。
- `__global__` 函式在 GPU 執行，用 `<<<grid, block>>>` 語法啟動。
- GPU kernel 是非同步的，`cudaDeviceSynchronize()` 是等待點。
- 執行緒輸出順序不確定——這是 GPU 的設計，不是 bug。

---

## 自我檢核

- [ ] 不看書，能說出 `nvidia-smi` 輸出中「15360 MiB」代表什麼？跟 T4 的「16 GB」怎麼換算？
- [ ] `__global__`、`__device__`、`__host__` 三個限定詞各自在哪執行、從哪呼叫？
- [ ] 為什麼 kernel 後面要加 `cudaDeviceSynchronize()`？不加會怎樣？
- [ ] `<<<2, 4>>>` 裡兩個數字分別叫什麼？分別指定什麼？
- [ ] 如果 `nvidia-smi` 的 CUDA Version 是 12.2，你能不能裝 CUDA Toolkit 12.5？

---

## 延伸閱讀

1. **CUDA C++ Programming Guide — Chapter 1: Introduction**
   NVIDIA 官方文件，[docs.nvidia.com/cuda/cuda-c-programming-guide/](https://docs.nvidia.com/cuda/cuda-c-programming-guide/)。Ch 1 講 GPU 架構動機（latency vs throughput），2-3 小時讀完。和本章的「為什麼用 GPU」直接呼應。前提：看得懂 C。

2. **NVIDIA T4 Datasheet**
   [nvidia.com/content/dam/en-zz/Solutions/Data-Center/tesla-t4/t4-tensor-core-datasheet-951643.pdf](https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/tesla-t4/t4-tensor-core-datasheet-951643.pdf)。一頁規格表：FP32 8.1 TFLOPS、記憶體頻寬 300 GB/s（部分文件標 320 GB/s，視測試方法）、TDP 70W。讀本章前確認數字出處。

3. **Getting Started with CUDA on WSL2 — NVIDIA Developer Blog**
   [developer.nvidia.com/cuda/wsl](https://developer.nvidia.com/cuda/wsl)。官方 WSL2 CUDA 安裝指南，包含為什麼「不在 Linux 側裝驅動」的解釋。如果你在 Windows 機器上學本課，這是必讀。

4. **《Programming Massively Parallel Processors》第 4 版（Kirk & Hwu），Chapter 2**
   本課程的配套教科書，Ch 2 是「Heterogeneous Data Parallel Computing」。和本章的 hello kernel 完全對應，書中有更詳細的執行緒模型圖解。在讀 Ch 1 之後讀。

---

下一章我們拆解 CPU 和 GPU 的架構哲學差異——為什麼 CPU 堆一堆 cache，而 GPU 堆一堆 ALU。

→ [Ch 1 — latency machine vs throughput machine](./01-cpu-vs-gpu-philosophy.md)
