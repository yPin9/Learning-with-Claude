# Ch 26 — 編譯流程：從 CUDA C++ 到 cubin

> **目標**：弄清楚 `nvcc` 對你的 `.cu` 檔案做了什麼——它如何分離 host/device 程式碼、經過哪些中間表示、最終打包成能跑在不同 GPU 上的 binary；理解 PTX 與 SASS 的差異和設計理由；搞懂 `-arch`、`-code`、`-gencode` 這組旗標為什麼這麼難用。

> **環境**：CUDA 12.x nvcc，目標 sm_75（Turing）。PTX/SASS 輸出與指令皆供讀者在 Colab 或有 nvcc 的機器重現；作者環境無 GPU/nvcc，指令輸出以「（Colab，供讀者重現）」標注。

---

你寫了一個 `vector_add.cu`，打下 `nvcc -arch=sm_75 vector_add.cu -o vector_add`，按下 Enter。這條指令背後發生了什麼？大多數人的答案是「nvcc 把 .cu 編譯成 binary」——對，但這個答案沒有任何資訊量。

真實的答案是：nvcc 扮演 orchestrator，把你的程式碼拆開，至少呼叫了四個獨立程式（cicc、ptxas、fatbinary、主機編譯器），產出多層中間表示，最後才組裝成一個 binary。搞懂這個流程，你才能理解：為什麼 PTX 可以移植但 SASS 不行、為什麼一個 binary 能跑在不同世代的 GPU 上、為什麼有些 JIT 第一次跑特別慢。這些不是邊角知識，它們直接影響你的部署決策與效能診斷。

---

## 一、先建立直覺：兩套語言，兩條路

`.cu` 檔案裡同時存在兩種程式碼：

- **Host code**：`main()`、`cudaMalloc()`、`kernel<<<...>>>()`——這些在 CPU 上跑，本質上就是普通 C++，只是多了 CUDA runtime API 呼叫。
- **Device code**：`__global__` kernel、`__device__` 函式、`__shared__` 變數——這些要在 GPU SM 上執行，根本不能交給 gcc 處理。

這兩種程式碼的編譯路徑完全不同，必須分開處理再合併。nvcc 做的核心工作就是這個分離動作，然後把兩條路的產物接在一起。

---

## 二、完整編譯流程圖

```
vector_add.cu
│
├─── [nvcc 前處理：分離 host/device code]
│
├──────────────────────────────────────────────────────────────┐
│  Device 路徑                                                  │  Host 路徑
│                                                              │
│  CUDA C++ (device portion)                                   │  CUDA C++ (host portion)
│       │                                                      │  + CUDA runtime API calls
│       ▼                                                      │       │
│  cicc  ← (CUDA 的 LLVM-based 前端)                           │       ▼
│  輸出：NVVM IR (LLVM IR 的超集)                               │  gcc / cl.exe (host compiler)
│       │                                                      │       │
│       ▼                                                      │       ▼
│  cicc（再往下一階段）                                         │  host object file (.o)
│  輸出：PTX（Parallel Thread eXecution）                       │
│       │                                                      │
│       ▼                                                      │
│  ptxas  ← (PTX assembler)                                    │
│  做：register allocation, instruction scheduling             │
│  輸出：cubin (SASS)                                           │
│       │                                                      │
│       ▼                                                      │
│  fatbinary  ← 打包工具                                        │
│  把多個 (PTX, cubin) 組合打成一個 fatbinary blob              │
│       │                                                      │
└───────┴──────────────────────────────────────────────────────┘
        │                     │
        ▼                     ▼
   fatbinary blob        host object
        │                     │
        └──────────┬──────────┘
                   ▼
              ld / nvlink
                   │
                   ▼
            vector_add  (ELF binary，fatbinary 嵌在 .nv_fatbin section)
```

整個流程的每一步都有意義，沒有冗餘。接下來逐一解釋。

---

## 三、nvcc 是 orchestrator，不是編譯器

這個觀念很重要：`nvcc` 本身**不編譯任何東西**。它是一個 driver 程式，負責：

1. 解析旗標與輸入檔
2. 把 `.cu` 前處理、拆分 host/device 程式碼片段
3. 按正確順序呼叫真正的工具：`cicc`（device 前端）、`ptxas`（PTX assembler）、`fatbinary`（打包工具）、系統 host compiler（gcc/cl.exe）
4. 傳遞正確的旗標給每個工具
5. 最後呼叫 linker

你可以用 `--dryrun` 旗標讓 nvcc 只列出它會執行的指令，而不真正執行：

```bash
nvcc --dryrun -arch=sm_75 vector_add.cu -o vector_add
# （Colab，供讀者重現）
# 輸出大概長這樣（已簡化）：
#   /usr/local/cuda/bin/cicc ... -o vector_add.ptx
#   /usr/local/cuda/bin/ptxas ... vector_add.ptx -o vector_add.cubin
#   /usr/local/cuda/bin/fatbinary ... --create vector_add.fatbin ...
#   /usr/bin/gcc ... -o vector_add.o ...
#   /usr/bin/gcc ... vector_add.o -o vector_add ...
```

這條指令是學習編譯流程最直接的工具——你能看到 nvcc 實際呼叫了什麼，以及每個子工具拿到了哪些旗標。

---

## 四、Device 路徑深解：cicc、NVVM IR、PTX

### cicc 與 NVVM IR

`cicc` 是 CUDA 的 device code 編譯器前端，基於 LLVM 技術棧。它接收 device 程式碼，輸出 **NVVM IR**（NVIDIA Virtual Machine IR）——這是 LLVM IR 的超集，加入了 CUDA 特有的 metadata（如 `!nvvm.annotations`，用來標注 kernel 函式）和內建函式（如 `__nvvm_read_ptx_sreg_tid_x()`，對應 `threadIdx.x`）。

類比：如果你熟悉 LLVM 工具鏈，cicc 扮演的角色相當於 `clang`——把高階語言降到中間表示（IR），但還沒到機器碼。

NVVM IR 這一層你平時不需要直接碰，但它的存在解釋了為什麼 CUDA 能利用 LLVM 的優化基礎設施（內聯、常數傳播、向量化），同時又能加入 GPU 特有的優化。

### PTX：Parallel Thread eXecution

**PTX**（Parallel Thread eXecution）是 NVIDIA GPU 的虛擬 ISA（Virtual Instruction Set Architecture）。「虛擬」的意思是：PTX 假設了一個**無限多虛擬暫存器**、**SSA（Static Single Assignment）風格**的理想 GPU，不對應任何具體硬體。

PTX 的設計目標是**可移植性**：一份 PTX 程式碼可以在 sm_75、sm_80、sm_90 等不同架構上執行，只需要在 runtime 由下一階段工具（ptxas）轉換到具體機器碼。

一段典型的 PTX 長這樣：

```ptx
// vector_add kernel 的 PTX 片段（sm_75）
// （Colab，供讀者重現：nvcc -ptx -arch=compute_75 vector_add.cu -o vector_add.ptx）

.visible .entry _Z10vector_addPfS_S_i(
    .param .u64 _Z10vector_addPfS_S_if_param_0,  // float* a
    .param .u64 _Z10vector_addPfS_S_if_param_1,  // float* b
    .param .u64 _Z10vector_addPfS_S_if_param_2,  // float* c
    .param .u32 _Z10vector_addPfS_S_if_param_3   // int n
)
{
    .reg .pred  %p<2>;       // predicate 暫存器（無限個，SSA 風格）
    .reg .f32   %f<4>;       // float 暫存器
    .reg .b32   %r<6>;       // 32-bit 暫存器
    .reg .b64   %rd<8>;      // 64-bit 暫存器

    // 讀取 threadIdx.x、blockIdx.x、blockDim.x
    mov.u32     %r1, %tid.x;
    mov.u32     %r2, %ctaid.x;
    mov.u32     %r3, %ntid.x;
    mad.lo.s32  %r4, %r2, %r3, %r1;    // idx = blockIdx.x * blockDim.x + threadIdx.x

    ld.param.s32 %r5, [_Z10vector_addPfS_S_if_param_3];  // 載入 n
    setp.ge.s32  %p0, %r4, %r5;         // if (idx >= n)
    @%p0 bra    $L__BB0_2;              // 條件跳轉（predicate 控制流）

    // ... 載入 a[idx]、b[idx]，相加，存 c[idx]
    ld.global.f32  %f1, [%rd5];
    ld.global.f32  %f2, [%rd7];
    add.f32         %f3, %f1, %f2;
    st.global.f32  [%rd9], %f3;

$L__BB0_2:
    ret;
}
```

PTX 的關鍵特性：
- **無限虛擬暫存器**：你看到的 `%r1`、`%f1`、`%rd1` 數量不受限，ptxas 會在下一步做暫存器分配
- **SSA 形式**：每個虛擬暫存器只被賦值一次，讓優化更容易分析
- **型別化記憶體空間**：`ld.global`、`st.shared`、`ld.param` 明確標注記憶體空間，這在 GPU 上有實質語意差異（全域記憶體 vs shared memory vs 參數記憶體）
- **predicate 控制流**：`@%p0 bra` 是 GPU 的條件執行方式，對應硬體的 predicate 機制（[Ch 21](./21-warp-divergence.md) 討論過 warp divergence，predicate 就是底層機制）

產生 PTX 的指令：

```bash
# 只產生 PTX，不產 cubin（compute_75 = 虛擬架構，只決定 PTX 語義版本）
nvcc -ptx -arch=compute_75 vector_add.cu -o vector_add.ptx
```

---

## 五、ptxas：把虛擬 ISA 轉成真實機器碼

**ptxas**（PTX Assembler）是把 PTX 轉換成 **SASS**（Streaming ASSembler，NVIDIA 內部叫法，也就是真正的 machine code）的工具，對應到具體的 GPU 架構（sm_75、sm_80 等）。

ptxas 做的事情比「組譯」要重得多：

- **暫存器分配**（Register Allocation）：把無限虛擬暫存器映射到硬體的有限暫存器（Turing 每個 thread 最多 255 個，實際限制由 occupancy 決定，[Ch 11](./11-occupancy.md) 有詳細討論）
- **指令排程**（Instruction Scheduling）：針對 GPU pipeline 的 latency，排列指令順序以隱藏延遲（[Ch 29](./29-instruction-level.md) 深挖）
- **Stall count 計算**：決定指令間需要插入多少 stall cycle（SASS 每條指令帶有 `yield/stall` 控制欄位）
- **架構特定優化**：針對 sm_75 vs sm_80 的差異生成不同指令序列

類比：ptxas 相當於 LLVM 的 machine code emission 階段（`llc`）——把架構無關的 IR 轉成特定硬體的機器碼。

```bash
# 只產生 cubin（SASS 的二進位格式）
nvcc -cubin -arch=sm_75 vector_add.cu -o vector_add.cubin

# 或者從現有 PTX 組譯
ptxas -arch=sm_75 vector_add.ptx -o vector_add.cubin
```

---

## 六、SASS：真實機器碼

**SASS** 是 GPU 實際執行的指令集。每個 SM 架構有自己的 SASS 指令集，Turing（sm_75）的 SASS 和 Ampere（sm_80）不相容，跟 x86 vs ARM 一樣本質上是不同的 ISA。

要查看 SASS，用 `cuobjdump` 或 `nvdisasm`：

```bash
# 反組譯 cubin 的 SASS
cuobjdump --dump-sass vector_add.cubin
# 或
nvdisasm -c vector_add.cubin
# （Colab，供讀者重現）

# 輸出片段長這樣（sm_75 Turing）：
# .text._Z10vector_addPfS_S_i:
#         /*0000*/  MOV R1, c[0x0][0x28];
#         /*0010*/  S2R R0, SR_CTAID.X;
#         /*0020*/  S2R R3, SR_TID.X;
#         /*0030*/  IMAD.MOV.U32 R2, RZ, RZ, c[0x0][0x168];
#         /*0040*/  IMAD R0, R0, c[0x0][0x0], R3;   // idx = blockIdx.x * blockDim.x + threadIdx.x
#         /*0050*/  ISETP.GE.AND P0, PT, R0, R2, PT; // predicate: idx >= n
#         /*0060*/  @P0 EXIT;                        // 條件退出
#         /*0070*/  IMAD.WIDE R2, R0, 0x4, c[0x0][0x160];  // 計算 a+idx 位址
#         /*0080*/  LDG.E R4, [R2.64];               // load global float a[idx]
#         /*0090*/  IMAD.WIDE R4, R0, 0x4, c[0x0][0x168];
#         /*00a0*/  LDG.E R5, [R4.64];               // load global float b[idx]
#         /*00b0*/  FADD R4, R4, R5;                 // float add
#         /*00c0*/  IMAD.WIDE R2, R0, 0x4, c[0x0][0x170];
#         /*00d0*/  STG.E [R2.64], R4;               // store global c[idx]
#         /*00e0*/  EXIT;
```

SASS 和 PTX 相比最明顯的差異：
- **有限的真實暫存器**：`R0`、`R1`、`R2`...（最多 255 個，ptxas 做了分配）
- **常數記憶體 `c[0x0][...]`**：kernel 參數在硬體上以常數記憶體傳遞
- **`SR_CTAID.X` 等特殊暫存器**：硬體 built-in，對應 PTX 的 `%ctaid.x`
- **`LDG` / `STG`**：Load/Store Global——具體的 global memory 存取指令
- **`ISETP` / `P0`**：整數比較，結果放進 predicate 暫存器 `P0`

SASS 是你在做深度效能優化時真正要讀的東西（[Ch 28](./28-reading-sass.md) 教你怎麼判讀）。

---

## 七、fatbinary：多架構打包

問題來了：你的程式要部署到客戶環境，但你不知道他的 GPU 是 Turing（T4, RTX 2080）、Ampere（A100, RTX 3080）還是 Hopper（H100）。一個 binary 怎麼同時支援？

答案是 **fatbinary**（胖二進位）。`fatbinary` 工具把多個 (cubin, PTX) 組合打包成一個 blob，這個 blob 嵌入在最終 ELF binary 的 `.nv_fatbin` section 裡。

執行時，CUDA driver 掃描 fatbinary，找到最匹配的版本執行：
- 如果找到對應 GPU 架構的 cubin → 直接用（最快）
- 如果找不到 cubin，但找到 PTX → JIT 編譯成當前 GPU 的 SASS（稍慢，只在第一次）
- 如果都沒有 → `CUDA_ERROR_NO_BINARY_FOR_GPU`

查看 fatbinary 內容：

```bash
cuobjdump -all vector_add
# （Colab，供讀者重現）
# 輸出：
# Fatbin elf code:
# ================
# arch = sm_75
# code version = [1,7]
# ...
# Fatbin ptx code:
# ================
# arch = sm_75
# ...
```

---

## 八、JIT Compilation

**JIT compilation**（Just-In-Time 編譯）指的是 CUDA driver 在 runtime 把 PTX 編譯成當前 GPU 的 SASS 的過程。

典型場景：你的 binary 包含 PTX（compute_90）但沒有 sm_90+ 的 cubin。當程式跑在一張 Hopper H100 上，driver 發現 fatbinary 裡沒有 sm_90 cubin，就拿 PTX 即時編譯一個。

JIT 的結果會 cache 在 `~/.nv/ComputeCache/`（Linux）或 `%AppData%\NVIDIA\ComputeCache\`（Windows）。第一次跑慢（秒級），之後就用 cache，快很多。

JIT 帶來的好處：**前向相容性**。你今天打包一個含 PTX 的 binary，在未來出現的新 GPU 架構上也能跑（只要 PTX ISA 版本相容），不需要重新編譯。

---

## 九、-arch vs -code vs -gencode：搞清楚這組旗標

這是整個 nvcc 旗標系統最容易搞混的地方。先說結論：

| 旗標 | 控制什麼 | 對應什麼 |
|------|----------|----------|
| `-arch=compute_75` | **虛擬架構（Virtual Arch）** | PTX 的語義/指令版本 |
| `-code=sm_75` | **真實架構（Real Arch）** | ptxas 輸出的 SASS 目標 |
| `-arch=sm_75` | 兩者的縮寫 | 等價於 `-arch=compute_75 -code=sm_75` |
| `-gencode` | 指定多組 (virtual, real) | 產出多個 cubin + PTX |

### -arch（virtual architecture）

`-arch=compute_75` 告訴 cicc：「用 sm_75 語義版本來解釋 PTX」。它決定哪些 PTX 指令和功能可用（例如 Volta sm_70 加入了 tensor core 指令；Ampere sm_80 加入了 `cp.async`），但**不決定輸出什麼硬體二進位**。

單獨用 `-arch=compute_75` 而不加 `-code`，nvcc 只會產出 PTX，不產 cubin。

### -code（real architecture）

`-code=sm_75` 告訴 ptxas：「把 PTX 組譯成 sm_75 的 SASS（cubin）」。沒有 `-arch` 配對提供 PTX，這個旗標沒意義。

### -arch=sm_75（縮寫）

這個縮寫最常用。它等價於 `-arch=compute_75 -code=sm_75`——產生 compute_75 語義的 PTX，然後組譯成 sm_75 cubin。

### -gencode：多架構打包

```bash
nvcc \
  -gencode arch=compute_70,code=sm_70 \
  -gencode arch=compute_75,code=sm_75 \
  -gencode arch=compute_80,code=sm_80 \
  -gencode arch=compute_90,code=\"sm_90,compute_90\" \
  vector_add.cu -o vector_add
```

最後一個 `-gencode arch=compute_90,code="sm_90,compute_90"` 的 `compute_90` 是關鍵：它讓 fatbinary 裡**同時**包含 sm_90 的 cubin 和 compute_90 的 PTX。這個 PTX 是 forward-compatibility 的保險——未來 sm_90+（假設 sm_95）的 GPU 遇到這個 binary，找不到對應 cubin，就用這份 PTX JIT。

---

## 十、-keep：保留中間產物，親眼看流程

```bash
# -keep 保留所有中間檔案（.ptx, .cubin, .fatbin 等）
nvcc -keep -arch=sm_75 vector_add.cu -o vector_add
# （Colab，供讀者重現）

# 執行後目錄下多出：
# vector_add.cudafe1.cpp    ← host 程式碼（分離後）
# vector_add.1.sm_75.ptx   ← PTX（cicc 輸出）
# vector_add.1.sm_75.cubin ← cubin（ptxas 輸出）
# vector_add.fatbin         ← fatbinary blob
# vector_add.o              ← host object
```

配合 `-keep`，再加 `cuobjdump -all vector_add` 或 `nvdisasm -c vector_add.1.sm_75.cubin`，你能完整追蹤每一步的產物。

---

## 十一、對比取捨：PTX vs SASS

| | PTX | SASS (cubin) |
|-|-----|-------------|
| 可移植性 | 高：跨 GPU 世代可移植 | 低：架構鎖定（sm_75 ≠ sm_80） |
| 執行方式 | 需要 JIT | 直接執行 |
| 首次執行延遲 | 有（JIT 時間） | 無 |
| 優化程度 | ptxas 可做額外優化 | 固定，產出時已決定 |
| 人類可讀性 | 較易（SSA、虛擬 reg） | 較難（真實 reg、stall bits） |
| 進行效能分析 | 間接（還沒分配暫存器） | 直接（能數 cycle，見 [Ch 28](./28-reading-sass.md)）|
| 前向相容 | 是 | 否 |

**實務建議**：分發 library 或 SDK（如 PyTorch extension）時，fatbinary 裡要放最新 PTX 作為 forward-compatibility 保險；同時提供主要目標架構的 cubin 讓常見 GPU 不用 JIT。

---

## 十二、踩雷

**踩雷 1：`-arch=sm_75` 和 `-arch=compute_75` 不一樣**

`-arch=sm_75` 是縮寫，等價於 `-arch=compute_75 -code=sm_75`，同時設定虛擬和真實架構，產出 cubin。
`-arch=compute_75` 只設虛擬架構，**只產 PTX，不產 cubin**。單獨用這個旗標的結果是 binary 只有 PTX，跑起來每次都要 JIT，你可能不自覺。

**踩雷 2：沒有 PTX fallback，binary 在未來 GPU 死掉**

如果你的 `-gencode` 只有 `-gencode arch=compute_75,code=sm_75`（沒有 `compute_XX` 作為 code 的元素），binary 裡沒有 PTX。當有人拿這個 binary 跑在 sm_80 或更新的 GPU 上，得到 `CUDA_ERROR_NO_BINARY_FOR_GPU`。標準做法是在最高版本的 `-gencode` 裡加 `compute_XX` 讓 binary 含 PTX。

**踩雷 3：`-code=sm_75` 沒有配合 `-arch`**

```bash
# 錯誤：沒有 -arch，ptxas 不知道 PTX 的語義版本
nvcc -code=sm_75 vector_add.cu -o vector_add   # 可能報錯或行為未定義
```

`-code` 必須搭配 `-arch`（或用 `-gencode` 同時指定兩者）。

**踩雷 4：JIT cache 污染問題**

JIT 結果 cache 在 `~/.nv/ComputeCache/`。如果你在開發期間改了 `.cu`、重新編譯，但沒清 cache，有時舊的 JIT 結果被誤用（driver 以 hash 判斷，但有邊緣 case）。遇到「改了 code 但行為沒變」的奇怪現象，先試著清空 `~/.nv/ComputeCache/` 或設 `CUDA_CACHE_DISABLE=1`。

**踩雷 5：`--gpu-architecture` 和 `-arch` 是同一個旗標**

nvcc 有長短兩版旗標：`--gpu-architecture=sm_75` 等於 `-arch=sm_75`；`--gpu-code=sm_75` 等於 `-code=sm_75`。混用長短版時要注意，用 `nvcc --help` 確認對應關係。某些 CI 腳本用長版，讀起來比較清楚但更囉嗦。

---

## 十三、進階：nvcc 旗標深水區

幾個實務上有用但文件藏得比較深的旗標：

**`--generate-line-info`（`-lineinfo`）**：在 cubin 裡嵌入 source line 資訊，讓 Nsight Compute 能在 SASS 旁顯示對應的原始碼行號。做 profiling 時永遠加這個。

**`--device-debug`（`-G`）**：產生 device debug 資訊，讓 cuda-gdb 能在 kernel 裡設斷點。注意：`-G` 會**完全關閉 device 端優化**，產出的 SASS 和優化版完全不同，絕對不要在效能測試裡用。

**`--ptxas-options=-v`**：讓 ptxas 輸出暫存器使用量、shared memory 用量、local memory 用量。這是最快速確認 kernel 資源佔用的方式：

```bash
nvcc --ptxas-options=-v -arch=sm_75 vector_add.cu -o vector_add
# （Colab，供讀者重現）
# 輸出：
# ptxas info    : 0 bytes gmem
# ptxas info    : Compiling entry function '_Z10vector_addPfS_S_i' for 'sm_75'
# ptxas info    : Function properties for _Z10vector_addPfS_S_i
#     0 bytes stack frame, 0 bytes spill stores, 0 bytes spill loads
# ptxas info    : Used 8 registers, 372 bytes cmem[0]
```

8 個暫存器代表這個 kernel 每 warp（32 thread）只用 8 × 32 = 256 個暫存器位置，佔用率上限高。「spill」非零代表暫存器不夠用、ptxas 溢出到 local memory（慢），要想辦法降低暫存器壓力。

**`-maxrregcount=N`**：強制限制每個 thread 最多用 N 個暫存器。可以強制提高 occupancy，但有 spill 風險。與 `__launch_bounds__` 的選擇：prefer `__launch_bounds__` 因為它給編譯器更多資訊（maxThreadsPerBlock 和 minBlocksPerMultiprocessor），`-maxrregcount` 只是硬性截斷。

---

## 動手練習

以下在 Colab（或任何有 nvcc 12.x + GPU 的環境）執行，作者環境無 nvcc，以讀者重現為準。

**練習 1：觀察 dryrun 輸出**

```bash
cat > vector_add.cu << 'EOF'
__global__ void vector_add(float* a, float* b, float* c, int n) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx < n) c[idx] = a[idx] + b[idx];
}
int main() { return 0; }
EOF

# 看 nvcc 的完整呼叫鏈
nvcc --dryrun -arch=sm_75 vector_add.cu -o vector_add 2>&1
```

數一下輸出裡有幾行指令，對照本章的流程圖。

**練習 2：產生並比較 PTX 和 SASS**

```bash
# PTX（虛擬）
nvcc -ptx -arch=compute_75 vector_add.cu -o va.ptx
cat va.ptx

# cubin → 反組譯 SASS（真實）
nvcc -cubin -arch=sm_75 vector_add.cu -o va.cubin
nvdisasm -c va.cubin
```

對照兩個輸出：PTX 的 `%r0`、`%f0` 虛擬暫存器，在 SASS 裡變成哪個真實暫存器（`R0`、`R1`...）？

**練習 3：ptxas verbose 看資源**

```bash
nvcc --ptxas-options=-v -arch=sm_75 vector_add.cu -o va_verbose
```

記下暫存器數量。把 kernel 改複雜一點（加 loop 和更多運算），看暫存器數量怎麼變。

**練習 4：多架構 fatbinary**

```bash
nvcc \
  -gencode arch=compute_70,code=sm_70 \
  -gencode arch=compute_75,code=sm_75 \
  -gencode arch=compute_80,code=sm_80 \
  -gencode arch=compute_86,code=\"sm_86,compute_86\" \
  vector_add.cu -o va_multi

# 看 fatbinary 裡有什麼
cuobjdump -all va_multi
```

確認 fatbinary 裡有 sm_70、sm_75、sm_80 的 cubin，以及一份 compute_86 的 PTX（fallback 用）。

**練習 5：-keep 保留中間產物**

```bash
nvcc -keep -arch=sm_75 vector_add.cu -o va_keep
ls -la vector_add*
```

逐一確認每個中間檔案的作用，對照本章流程圖。

---

## 本章重點

1. **nvcc 是 orchestrator**：它本身不編譯，而是依序呼叫 cicc（前端）、ptxas（組譯）、fatbinary（打包）、host compiler，並管理中間產物。
2. **兩條路徑**：host code 走系統 compiler，device code 走 cicc → PTX → ptxas → SASS，最後合併到同一個 binary。
3. **PTX = 虛擬 ISA**：無限虛擬暫存器、SSA 風格、跨架構可移植，是 NVVM IR（LLVM-based）降階的產物，但還不是機器碼。
4. **SASS = 真實機器碼**：ptxas 把 PTX 轉成具體架構（sm_75）的機器碼，同時做暫存器分配與指令排程。
5. **fatbinary 打包多架構**：同一個 binary 能嵌入多個 (cubin, PTX) 組合，driver 在 runtime 選最合適的版本。
6. **JIT = PTX 轉 SASS 在 runtime**：讓 binary 前向相容，結果 cache 在 `~/.nv/ComputeCache/`。
7. **-arch 控制 PTX 語義版本，-code 控制 cubin 目標**：`-arch=sm_75` 是兩者的縮寫；`-gencode` 一次指定多組。

---

## 自我檢核

回答得出來再往下走：

1. `nvcc -arch=compute_75 vector_add.cu -o va` 的 `va` 裡有沒有 cubin？為什麼？
2. ptxas 在 PTX → SASS 的過程中做了哪三件「比組譯更重」的事？
3. 為什麼 `-gencode arch=compute_90,code="sm_90,compute_90"` 的 code 裡要同時寫 `sm_90` 和 `compute_90`？只寫 `sm_90` 會少掉什麼？
4. `CUDA_ERROR_NO_BINARY_FOR_GPU` 最常見的原因是什麼？怎麼預防？
5. `-G` 旗標（`--device-debug`）對 SASS 有什麼影響？為什麼不能在效能測試裡用？

---

## 延伸閱讀

1. **NVCC 官方文件**：[docs.nvidia.com/cuda/cuda-compiler-driver-nvcc](https://docs.nvidia.com/cuda/cuda-compiler-driver-nvcc/)——完整旗標參考，Section 6「Using Separate Compilation in CUDA」很值得讀。
2. **PTX ISA 文件**：[docs.nvidia.com/cuda/parallel-thread-execution](https://docs.nvidia.com/cuda/parallel-thread-execution/)——PTX 的完整指令參考，分析 cicc 輸出時的 ground truth。
3. **Dissecting the NVIDIA Turing T4 GPU**：arXiv 1903.07486——從硬體角度分析 Turing 微架構，幫助理解 ptxas 的排程決策背後的硬體現實。
4. **CUDA Binary Utilities**：[docs.nvidia.com/cuda/cuda-binary-utilities](https://docs.nvidia.com/cuda/cuda-binary-utilities/)——`cuobjdump` 和 `nvdisasm` 的完整文件，第一手來源學兩個工具的所有選項。

---

## 銜接 Ch 27

現在你知道 nvcc 怎麼產 PTX，以及 PTX 在整條流程裡扮演什麼角色。下一步是真正讀懂 PTX——理解它的記憶體空間模型、型別系統、控制流表達方式，以及如何從 PTX 回溯推斷編譯器的優化決策。

[→ Ch 27：讀 PTX：虛擬 ISA 與記憶體 space](./27-reading-ptx.md)
