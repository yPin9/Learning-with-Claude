# Ch 34 — 多 GPU：P2P、UVA、NCCL、MPI+CUDA

> **目標**：掌握 `cudaSetDevice` 管理多張卡；理解 P2P（peer-to-peer）傳輸的前提（NVLink vs PCIe）；理解 UVA（Unified Virtual Addressing）帶來的好處；掌握 NCCL 的 `ncclAllReduce`、`ncclBroadcast` 等集合通訊 API 的基本形狀；對 MPI+CUDA 多節點有基礎心智圖。

> **環境**：CUDA 12.x, Colab T4 (sm_75)。P2P 功能在 Colab 通常只有單 GPU，多 GPU 測試需要雲端多卡環境（如 GCP A100 多卡實例）。效能數字標「文獻/官方數字」或「理論預期，實測請驗證」。

---

## 為什麼需要多 GPU？

一張 T4 的 FP16 Tensor Core 峰值約 65 TOPS（文獻/官方數字）。GPT-3 訓練需要 ~3.14 × 10²³ FLOP，在 65 TFLOPS 下理論上需要 ~56 天（不算通訊開銷）。單卡不夠用，多卡是標配。

問題不是「要不要多 GPU」，而是「GPU 之間如何高效交換資料」。GPU 不能直接讀另一張卡的記憶體（除非啟用 P2P），通訊延遲和頻寬是多 GPU 訓練的核心瓶頸。

---

## cudaSetDevice：切換當前 GPU

CUDA runtime 有一個 per-thread 的「當前 GPU」狀態：

```cpp
int n_devices;
cudaGetDeviceCount(&n_devices);
printf("系統有 %d 張 GPU\n", n_devices);

// 切到 GPU 0
cudaSetDevice(0);
float *d_a;
cudaMalloc(&d_a, 1024 * sizeof(float));   // 分配到 GPU 0

// 切到 GPU 1
cudaSetDevice(1);
float *d_b;
cudaMalloc(&d_b, 1024 * sizeof(float));   // 分配到 GPU 1
```

任何後續 CUDA 呼叫（`cudaMalloc`、kernel launch、`cudaMemcpy`）都作用在「當前 GPU」上。切換本身幾乎是 free（只是更新一個整數），但 `cudaMalloc` 等實際操作在哪張卡上取決於切換後的狀態。

**跨裝置傳輸**：不需要額外 API，`cudaMemcpy` 可以跨裝置（但不一定走最快路徑）：

```cpp
// GPU 0 → GPU 1 傳輸（走最快可用路徑）
cudaMemcpyPeer(d_b, 1 /*dst device*/, d_a, 0 /*src device*/, size);
```

---

## P2P：GPU 直接讀寫另一張 GPU 的記憶體

預設情況下，GPU 0 要讀 GPU 1 的資料，必須先搬到 host，再搬到 GPU 0——走兩次 PCIe，慢。

**P2P（Peer-to-Peer）** 讓 GPU 直接存取另一張 GPU 的記憶體，繞過 CPU/host DRAM，頻寬高、延遲低。

### 啟用 P2P

```cpp
int can_access_01, can_access_10;
cudaDeviceCanAccessPeer(&can_access_01, 0, 1);   // GPU 0 能否存取 GPU 1？
cudaDeviceCanAccessPeer(&can_access_10, 1, 0);   // GPU 1 能否存取 GPU 0？

if(can_access_01) {
    cudaSetDevice(0);
    cudaDeviceEnablePeerAccess(1, 0);   // flags 目前必須是 0
}
if(can_access_10) {
    cudaSetDevice(1);
    cudaDeviceEnablePeerAccess(0, 0);
}
```

P2P 啟用後，GPU 0 上的 kernel 可以直接 dereference 指向 GPU 1 記憶體的指標：

```cpp
cudaSetDevice(0);
__global__ void read_peer(float *peer_ptr, float *local, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if(i < n) local[i] = peer_ptr[i];   // 直接讀 GPU 1 的記憶體
}
```

### NVLink vs PCIe

P2P 能走兩種物理路徑，效能差距很大：

| | NVLink（A100 NVLink 3.0） | PCIe 4.0 x16 |
|--|--|--|
| 單向頻寬（文獻/官方） | ~400 GB/s（A100 NVLink 總頻寬）| ~32 GB/s |
| 延遲 | 低（~1 μs） | 高（~5–10 μs） |
| 拓撲 | GPU 對 GPU 直連，可組 NVSwitch mesh | 走 PCIe switch，受 host 拓撲影響 |
| 常見平台 | DGX A100、H100 NVL | 消費級多卡工作站、雲端 V100 |

T4 不支援 NVLink，Colab 通常只有單卡。如果你在多卡 A100 上跑，`nvidia-smi topo -m` 可以看 GPU 之間的連結拓撲。

**為什麼 PCIe P2P 有時反而比 host 中繼更慢**：PCIe P2P 在某些 CPU 拓撲下（跨 NUMA 節點的 PCIe switch）實際頻寬比透過 host memory 還低。`cudaDeviceCanAccessPeer` 回傳 true 只代表「可以這樣用」，不代表「這樣用一定最快」。NCCL 內部會自動選擇最優路徑。

---

## UVA：Unified Virtual Addressing

UVA 是 CUDA 的統一虛擬位址空間——所有 GPU 和 host 的記憶體都映射到同一個 64-bit 位址空間，CUDA runtime 能從指標的位址區段判斷資料在哪裡：

```cpp
// 在 UVA 下，不需要明確說明 src/dst 在 host 還是 device
// cudaMemcpy 自動判斷方向
cudaMemcpy(dst, src, size, cudaMemcpyDefault);   // UVA 下可以用 Default
```

UVA 的實際意義：
- `cudaMemcpyDefault`：讓 runtime 根據指標自動選 Host2Device / Device2Host / Device2Device
- P2P 的前提：如果兩張 GPU 都在同一個 UVA 空間（現代多 GPU 系統通常如此），P2P 才能運作
- NCCL、Thrust 的多 GPU 支援都依賴 UVA

---

## NCCL：深度學習的集合通訊骨幹

NCCL（NVIDIA Collective Communications Library）是多 GPU / 多節點訓練的通訊層。PyTorch DDP（DistributedDataParallel）、Horovod、JAX 的分散式訓練都在用它。

### 核心概念

**Communicator（通訊子）**：一組 GPU 的集合，類似 MPI 的 communicator。所有集合操作在 communicator 內進行。

**集合操作（Collective Operations）**：

| 操作 | 語義 | DL 用途 |
|------|------|---------|
| AllReduce | 所有 GPU 各提供一個 tensor，結果（sum/max/...）廣播回所有 GPU | 梯度同步（data parallel） |
| Broadcast | 一個 GPU 的 tensor 傳給所有 GPU | 參數初始化廣播 |
| Reduce | 所有 GPU 提供 tensor，結果匯總到一個指定 GPU | 不常用 |
| AllGather | 每個 GPU 各提供一部分，所有 GPU 都拿到完整結果 | 參數 server，tensor parallel |
| ReduceScatter | Reduce 後每個 GPU 拿到結果的不同切片 | 梯度 sharding（FSDP） |

### AllReduce 的 Ring 演算法直覺

Ring AllReduce 是 NCCL 的預設演算法（對中大型 tensor），直覺如下：

假設 4 張 GPU，每張各有 tensor `[a, b, c, d]`（4 個分量）：

**Phase 1：Reduce-Scatter（2*(N-1) = 6 步的前半 N-1 步）**
每輪，每張 GPU 把自己的一個分量發給「右鄰居」，同時接收「左鄰居」發來的值並加到自己的對應分量。N-1 輪後，每張 GPU 有一個分量的全局 sum。

**Phase 2：AllGather（另外 N-1 步）**
每輪，每張 GPU 把自己已完成的全局 sum 分量發給右鄰居。N-1 輪後，所有 GPU 都有完整的全局 sum。

**為什麼 Ring 比中心化方案好**：
- 星型方案（所有 GPU 先發給一個 parameter server，再廣播）：parameter server 的頻寬是瓶頸
- Ring：每張 GPU 的收/發頻寬都被充分利用，總通訊量是 `2*(N-1)/N * message_size`（接近 2x，不是 N 倍）

Ring AllReduce 的效能接近理論最優（文獻/官方數字：頻寬效率 > 90% 於 N ≤ 16）。

### NCCL API 基本形狀

```cpp
#include <nccl.h>

// 初始化：建立 communicator（單節點 4 GPU 的範例）
int n_gpus = 4;
ncclComm_t comms[4];
int devs[4] = {0, 1, 2, 3};
ncclCommInitAll(comms, n_gpus, devs);   // 最簡單的初始化方式

// 在各 GPU 上分配 tensor（通常在各自的 thread 裡）
float *d_tensors[4];
cudaStream_t streams[4];
for(int i = 0; i < n_gpus; i++) {
    cudaSetDevice(i);
    cudaMalloc(&d_tensors[i], N * sizeof(float));
    cudaStreamCreate(&streams[i]);
}

// 執行 AllReduce（對所有 GPU 同步執行）
ncclGroupStart();
for(int i = 0; i < n_gpus; i++) {
    ncclAllReduce(
        d_tensors[i],         // send buffer（輸入）
        d_tensors[i],         // recv buffer（輸出，可以和 send 相同 = in-place）
        N,                    // 元素個數
        ncclFloat,            // 資料型別
        ncclSum,              // reduction operator（也有 ncclMax, ncclMin, ncclProd）
        comms[i],             // 第 i 個 GPU 的 communicator
        streams[i]            // 跑在哪條 stream
    );
}
ncclGroupEnd();

// 等待所有 GPU 完成
for(int i = 0; i < n_gpus; i++) {
    cudaSetDevice(i);
    cudaStreamSynchronize(streams[i]);
}

// 清理
for(int i = 0; i < n_gpus; i++) {
    ncclCommDestroy(comms[i]);
}
```

**`ncclGroupStart` / `ncclGroupEnd`**：把多個 NCCL 呼叫打包成一個 group，讓 NCCL 內部做批次排程（减少開銷）。單節點多 GPU 必須用 group，否則可能 deadlock（每個 communicator 要等對應的另一個 GPU 發起操作）。

### Ring vs Tree 演算法

NCCL 根據 tensor 大小和 GPU 數量自動選擇演算法：

| | Ring | Tree（reduce-scatter + all-gather binary tree） |
|--|--|--|
| 頻寬效率 | 高（大 tensor 時接近最優）| 較低 |
| 延遲 | 高（O(N) 步） | 低（O(log N) 步） |
| 適用 | 大 tensor（>1 MB 量級）| 小 tensor、多 GPU（延遲敏感） |

模型梯度通常很大（GPT-3 梯度 ~3.15 GB），Ring 是對的選擇。Small AllReduce（loss 標量、norm 計算）走 Tree 更好。NCCL 3.x 自動切換，你不需要手動選。

---

## 多節點：MPI + CUDA 概覽

單機多 GPU 用 NCCL 就夠。跨節點需要把不同機器的 GPU 組成一個 communicator，這時用 **MPI + NCCL** 的組合：

```cpp
// 典型多節點初始化模式
MPI_Init(&argc, &argv);
int rank, world_size;
MPI_Comm_rank(MPI_COMM_WORLD, &rank);
MPI_Comm_size(MPI_COMM_WORLD, &world_size);

// 每個 MPI rank 對應一張 GPU
int local_rank = rank % gpus_per_node;
cudaSetDevice(local_rank);

// 用 MPI 交換 NCCL ID（讓所有 rank 知道如何建立 communicator）
ncclUniqueId nccl_id;
if(rank == 0) ncclGetUniqueId(&nccl_id);
MPI_Bcast(&nccl_id, sizeof(nccl_id), MPI_BYTE, 0, MPI_COMM_WORLD);

// 建立 NCCL communicator（現在跨節點）
ncclComm_t comm;
ncclCommInitRank(&comm, world_size, nccl_id, rank);

// 之後的 ncclAllReduce 等操作跨節點進行
// 底層透過 InfiniBand（NVLink 只在同機器內）
```

**IB（InfiniBand）**：節點間的高速互聯，HDR IB 提供 200 Gb/s 的頻寬，遠低於 NVLink 的節點內頻寬。這就是為什麼多節點訓練的瓶頸幾乎都在 AllReduce（梯度同步的通訊量）。

你不需要自己寫這段 MPI+CUDA 程式碼——PyTorch 的 `torch.distributed`、Horovod、DeepSpeed 都封裝好了。了解底層是為了能 debug 通訊瓶頸（看 NCCL 日誌：`NCCL_DEBUG=INFO`）。

---

## 踩雷清單

**錯誤直覺 1：`cudaDeviceEnablePeerAccess` 之後，GPU 0 自動看得到 GPU 1 的記憶體。**
正確：P2P access 是有方向性的。`cudaSetDevice(0); cudaDeviceEnablePeerAccess(1, 0)` 只讓 GPU 0 可以存取 GPU 1 的記憶體，反過來還要 `cudaSetDevice(1); cudaDeviceEnablePeerAccess(0, 0)`。不對稱地啟用會導致某個方向的 kernel 在 dereference 指標時 segfault。

**錯誤直覺 2：`cudaDeviceCanAccessPeer` 回傳 true，P2P 就一定比 host 中繼快。**
正確：`cudaDeviceCanAccessPeer` 只保證硬體上支援 P2P，不保證性能。跨 NUMA 的 PCIe P2P 有時比 host 中繼更慢，因為要繞過 CPU cache 的情況。生產環境要 benchmark 再決定。

**錯誤直覺 3：NCCL AllReduce 呼叫後，資料立刻可用。**
正確：`ncclAllReduce` 是非同步的，提交到 stream 就返回。要等操作完成，必須 `cudaStreamSynchronize(stream)`。

**錯誤直覺 4：單節點多 GPU 可以不用 `ncclGroupStart/End`。**
正確：單節點多 GPU 且每個 GPU 在不同 CPU thread 裡各自呼叫 NCCL 時，必須用 group（或者確保呼叫順序）。否則可能死鎖：GPU 0 的 AllReduce 在等 GPU 1，但 GPU 1 的 NCCL 呼叫還沒執行。

**錯誤直覺 5：`ncclCommInitAll` 可以跨節點使用。**
正確：`ncclCommInitAll` 只適合單節點多 GPU（它自己建立 ncclUniqueId）。跨節點要用 `ncclCommInitRank` 配合 MPI 或其他方式廣播 `ncclUniqueId`。

---

## 進階：Pipeline 並行和 Tensor 並行（概念）

數據並行（data parallel）是 NCCL AllReduce 的主戰場：每張 GPU 放一個完整的模型副本，跑不同的 mini-batch，梯度用 AllReduce 同步。

對更大的模型（GPT-4 量級），單張 GPU 放不下完整模型，需要：

- **Pipeline 並行**：模型層切割，GPU 0 跑 layer 0–10，GPU 1 跑 layer 11–20；GPU 0 的輸出 activate 傳給 GPU 1（activation 傳輸用 P2P 或 NCCL Send/Recv）
- **Tensor 並行（Megatron 風格）**：一個 matrix multiply 切成多塊，每張 GPU 各算一部分，用 AllReduce 或 AllGather 聚合中間結果

這兩種並行 NCCL 也有對應的 primitive：`ncclSend` / `ncclRecv`（點對點），以及 AllGather / ReduceScatter（tensor 並行的梯度交換）。

---

## 進階：用 PyTorch 驗證 NCCL 行為（單機模擬）

在 Colab 單 GPU 環境下，雖然沒有真正的多 GPU，可以用 PyTorch 的 `torch.distributed` 模擬（fork 多個 process，每個 process 各管一個 GPU），理解 API 語義：

```python
# torchrun_demo.py（在有多卡的機器上跑）
import os
import torch
import torch.distributed as dist

def main():
    # 初始化 process group（NCCL backend）
    dist.init_process_group(backend='nccl')

    rank = dist.get_rank()
    world_size = dist.get_world_size()
    device = torch.device(f'cuda:{rank}')

    # 每個 rank 持有自己的 tensor
    tensor = torch.ones(4, device=device) * (rank + 1)
    print(f"Rank {rank} before AllReduce: {tensor.tolist()}")

    # AllReduce：所有 rank 的 tensor 相加，結果廣播回所有 rank
    dist.all_reduce(tensor, op=dist.ReduceOp.SUM)
    print(f"Rank {rank} after AllReduce: {tensor.tolist()}")
    # 預期：world_size=2 下，rank 0 持有 [1,1,1,1]，rank 1 持有 [2,2,2,2]
    # AllReduce 後兩個 rank 都有 [3,3,3,3]

    dist.destroy_process_group()

if __name__ == '__main__':
    main()
```

啟動方式（雙 GPU）：

```bash
# torchrun 自動設定 RANK, WORLD_SIZE, MASTER_ADDR 等環境變數
torchrun --nproc_per_node=2 torchrun_demo.py
```

`torch.distributed` 的 `all_reduce` 在 GPU 後端就是呼叫 NCCL 的 `ncclAllReduce`，理解這個 PyTorch API 等同於理解 NCCL 的語義。

---

## 效能建模：Roofline 視角下的 AllReduce

AllReduce 的通訊量（ring 演算法）：每個 GPU 發送和接收的總量各是 `2 * (N-1)/N * message_size`，近似 `2 * message_size`（N 很大時）。

如果 message 是 10 GB（大型模型的梯度），在 NVLink（400 GB/s 雙向）上：

```
AllReduce 時間 ≈ 2 * 10 GB / (400 GB/s / 2)  （雙向 400 GB/s → 每方向 200 GB/s）
              ≈ 0.1 秒  （文獻/官方數字，假設 8 張 A100 NVLink ring）
```

一個 transformer forward+backward step 如果需要 0.5 秒，AllReduce 佔 20%——這已經是瓶頸。DeepSpeed 的 ZeRO optimization 和 gradient compression 都是為了降低這個比例。

---

## 動手練習

**Colab 環境限制**：Colab T4 通常只有一張 GPU，無法直接練習多 GPU。如果你有 GCP / AWS / Azure 多卡實例（A10、V100 8x），可以跑以下練習。或者：Colab Pro+ 有時有雙 GPU。

練習 A（可用 Colab）：用 `cudaGetDeviceCount` 查詢 GPU 數量，用 `cudaGetDeviceProperties` 印出每張 GPU 的名稱、顯存大小、`warpSize`、`maxThreadsPerBlock`。

練習 B（需多卡）：啟用 P2P，在 GPU 0 上跑一個 kernel 填入陣列，P2P memcpy 到 GPU 1，在 GPU 1 上跑 reduction，比較有無 P2P 的延遲。

練習 C（需多卡）：用 NCCL 實作 2 GPU AllReduce：GPU 0 和 GPU 1 各持有 `[1,2,3,4]`，AllReduce 後兩張卡都應有 `[2,4,6,8]`。

練習 D（可用 Colab + PyTorch）：如果 Colab Pro+ 有雙 GPU，用上面的 `torchrun_demo.py` 範例驗證 AllReduce 的語義。用 `torch.distributed.all_gather` 實作 AllGather，觀察每個 rank 的輸出。

---

## 本章重點

- `cudaSetDevice` 切換當前 GPU；`cudaMemcpyPeer` 跨裝置傳輸
- P2P：需要 `cudaDeviceEnablePeerAccess`；NVLink 比 PCIe 快一個數量級
- UVA：統一位址空間，是 P2P 和 NCCL 的基礎
- NCCL AllReduce 語義：所有 GPU 提供 tensor，結果（sum/max/...）廣播回全部
- Ring AllReduce：頻寬效率高，適合大 tensor；Tree：延遲低，適合小 tensor
- 多節點：MPI 廣播 `ncclUniqueId`，再 `ncclCommInitRank`

## 自我檢核（主動回憶）

1. Ring AllReduce 的兩個 phase 分別是什麼？N 張 GPU 需要多少步？
2. `ncclGroupStart` / `ncclGroupEnd` 的用途是什麼？不用它單節點多 GPU 會發生什麼？
3. NVLink 和 PCIe P2P 的頻寬差異大約多少倍？
4. `ncclCommInitAll` 和 `ncclCommInitRank` 的區別？
5. 數據並行、Pipeline 並行、Tensor 並行各自解決什麼問題？

## 延伸閱讀

1. **NCCL 官方文件** — [docs.nvidia.com/deeplearning/nccl](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/index.html)：完整 API 說明，包含所有 collective 操作和 group 操作
2. **Ring AllReduce 詳解** — Baidu Research 部落格「Bringing HPC Techniques to Deep Learning」（2017）：Ring AllReduce 被引入 DL 訓練的原始文章，有清晰的圖解
3. **NVIDIA Multi-GPU Programming Guide** — [developer.nvidia.com](https://developer.nvidia.com/blog/cuda-pro-tip-expose-cuda-multi-gpu-multi-process-parallelism)：P2P、UVA、NCCL 的整合使用指南
4. **Megatron-LM 論文**（Shoeybi et al., 2019）：Tensor 並行的原始描述，解釋 AllReduce/AllGather 在 transformer 矩陣乘中的位置
5. **NCCL GitHub** — [github.com/NVIDIA/nccl](https://github.com/NVIDIA/nccl)：原始碼和 test/ 下的範例，`nccl/test/allReduce_perf.cu` 是最好的參考實作

---

多 GPU 解決了「怎麼讓多張卡協作」，但每張卡上的 kernel launch overhead 還是問題。

## 補充：`nvidia-smi` 工具快速診斷多 GPU 拓撲

在你自己的多 GPU 機器或雲端多卡環境，這兩個指令是第一步診斷工具：

```bash
# 印出每張 GPU 的基本資訊（名稱、記憶體、溫度、功耗）
nvidia-smi

# 印出 GPU 之間的拓撲（NVLink / PCIe / SYS）
nvidia-smi topo -m
```

輸出範例（DGX A100）：

```
        GPU0  GPU1  GPU2  GPU3  GPU4  GPU5  GPU6  GPU7
GPU0    X     NV12  NV12  NV12  NV12  NV12  NV12  NV12
GPU1    NV12  X     NV12  NV12  NV12  NV12  NV12  NV12
...

Legend:
  NV12   = NVLink 12（第 12 代 NVLink，A100 SXM 上是 12 條 NVLink）
  PIX    = PCIe，同一個 PCIe switch
  PXB    = PCIe，跨 PCIe switch
  SYS    = 跨 NUMA / QPI
```

`NV#` 表示 GPU 之間用 NVLink 直連，`PIX` / `PXB` 表示走 PCIe。同樣是「PCIe P2P」，`PIX`（同 switch）的有效頻寬比 `PXB`（跨 switch）高。NCCL 在初始化時會讀取這個拓撲，自動選擇最優的 ring/tree 路徑。

---

→ [Ch 35 CUDA Graphs](./35-cuda-graphs.md)
