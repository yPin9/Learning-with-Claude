# Ch 41 — FlashAttention：IO-Aware Attention 的工程藝術

> **目標**：理解 FlashAttention 為何比標準 attention 快不是因為省 FLOP，而是因為省 HBM 讀寫；掌握 online softmax 的數學推導；能看懂 tiled attention kernel 骨架；了解 FA1→FA2→FA3 演進脈絡。
>
> **環境**：A100 SXM 80GB（HBM2e 2 TB/s，SRAM/L2 ≈ 40 MB total）。公式推導與偽碼適用於所有 GPU；具體速度數字引自原論文，非本機實測。

---

## 為什麼標準 Attention 是效能地雷

Transformer 的 Scaled Dot-Product Attention 定義：

```
Attention(Q, K, V) = softmax(QK^T / sqrt(d)) · V
```

其中 Q, K, V ∈ R^{N×d}，N 是序列長度，d 是 head dimension。

### 記憶體炸了

中間矩陣 S = QK^T ∈ R^{N×N}。對 N = 8192, d = 64，FP16（2 bytes）：

```
S 矩陣大小 = N × N × 2 bytes
           = 8192 × 8192 × 2
           = 134,217,728 bytes
           ≈ 128 MB（僅一個 head）
```

多頭（Multi-Head Attention，MHA）設 H = 8 heads：

```
全部 S 矩陣 = 128 MB × 8 = 1 GB
```

A100 的 HBM 是 80 GB，存得下——但問題不是存不存得下，而是讀寫次數。

### 讀寫才是真正的殺手

標準 attention 的 HBM 存取模式（不考慮融合算子）：

| 步驟 | 操作 | HBM 寫入 | HBM 讀回 |
|------|------|----------|----------|
| 1 | S = QK^T | N² | — |
| 2 | P = softmax(S) | N² | N² |
| 3 | O = PV | — | N² |
| **合計** | | **N²** | **2N²** |

N = 8192, d = 64, FP16，三個矩陣的 HBM 存取量：

```
HBM 讀寫 ≈ 3 × N² × 2 bytes
         = 3 × 8192² × 2
         ≈ 402 MB（僅 S/P/O 中間矩陣的搬移）
```

A100 HBM 頻寬 2 TB/s，400 MB 需要約 **0.2 ms**。聽起來短，但一個 forward pass 中 attention 反覆呼叫，加上 backward pass 要再讀一遍 S/P，累積量可觀。

更根本的問題：A100 的 FP16 Tensor Core 峰值是 312 TFLOPS，但做 softmax、除法、逐元素乘法這些「非 matmul 操作」（non-matmul ops）全是 memory-bound。每次 softmax 都要完整讀完 N×N 矩陣，導致 GPU 在等 HBM，而不在算。

**FlashAttention 的核心洞察：我們不需要把 N×N 矩陣實際存在 HBM 裡。**

---

## 先建立直覺：Tiling 怎麼運作

### GPU 記憶體層次回顧

```
┌─────────────────────────────────────────────┐
│  HBM（High Bandwidth Memory）                │
│  A100: 80 GB, 2 TB/s                        │
│  存 Q, K, V, O（整個序列）                    │
└───────────────┬─────────────────────────────┘
                │ 慢（相對 SRAM）
┌───────────────▼─────────────────────────────┐
│  L2 Cache ≈ 40 MB                           │
└───────────────┬─────────────────────────────┘
                │
┌───────────────▼─────────────────────────────┐
│  SRAM（Shared Memory，每個 SM）               │
│  A100: 每 SM 192 KB，速度是 HBM 的 ~19×      │
│  在這裡做所有計算                             │
└─────────────────────────────────────────────┘
```

SRAM 容量有限，放不下整個 N×N 矩陣——但我們可以一次只載一塊進來。

### Tiling 示意圖

把 Q 切成 T_r 個 row block（每塊 B_r 行），把 K、V 切成 T_c 個 column block（每塊 B_c 行）：

```
Q（N×d）          K^T（d×N）     =   S（N×N）
┌──────────┐     ┌───┬───┬───┐       ┌───┬───┬───┐
│  Q_1     │  ×  │K_1│K_2│K_3│   =   │S_1│S_2│S_3│  ← row 1
├──────────┤     └───┴───┴───┘       ├───┼───┼───┤
│  Q_2     │                         │   │   │   │  ← row 2
├──────────┤                         ├───┼───┼───┤
│  Q_3     │                         │   │   │   │  ← row 3
└──────────┘                         └───┴───┴───┘

每個 block Q_i（B_r×d）在 SRAM 固定不動
依序讀 K_j, V_j（B_c×d），計算 S_ij = Q_i K_j^T
```

關鍵問題浮現：S_ij 只是 attention 矩陣的一塊，softmax 需要整列的所有元素——我們怎麼在不看完整列的情況下算出正確的 softmax？

答案是 **online softmax**。

---

## 核心機制：Online Softmax 推導

### Softmax 的問題

標準 softmax 對向量 x ∈ R^T：

```
softmax(x)_i = exp(x_i) / Σ_j exp(x_j)
```

直接算有數值問題：exp(x_i) 可能溢出（overflow）。實際做法是減去最大值：

```
softmax(x)_i = exp(x_i − max(x)) / Σ_j exp(x_j − max(x))
```

問題是 max(x) 要看完整列才知道。如果 x 分成兩塊，第一塊看完後 max 可能在第二塊。

### Online Softmax 直覺

我們想要「邊看新 block，邊更新之前算出來的 partial result」。

假設目前看過前幾個 block，手上有：
- `m`：目前看過所有元素的 running max
- `l`：已校正過 running max 後的 running sum（= Σ exp(x_i − m)）
- `O`：目前累積的 output

來了一個新 block，看到新的最大值 `m_new = max(m, block_max)`。

之前用舊 `m` 算的 exp 值，現在要用 `m_new` 來校正：

```
exp(x_i − m_old) = exp(x_i − m_new) · exp(m_new − m_old)
```

所以之前的 running sum `l` 需要乘以 `exp(m − m_new)` 才能在新的 scale 下繼續累加。這個 rescale factor 永遠 ≤ 1（因為 m_new ≥ m），所以不會 overflow。

### FlashAttention-1 完整推導

**符號定義：**
- Q_i：第 i 個 query row block，B_r × d
- K_j, V_j：第 j 個 key/value row block，B_c × d
- S_ij = Q_i K_j^T ∈ R^{B_r × B_c}：local attention score
- m_ij = rowmax(S_ij) ∈ R^{B_r}：每列在本 block 的最大值
- P̃_ij = exp(S_ij − m_ij) ∈ R^{B_r × B_c}：unnormalized local softmax（廣播減）
- l̃_ij = rowsum(P̃_ij) ∈ R^{B_r}：每列的 partial sum

迭代 j = 1 … T_c，對固定的 row block i：

**Step A — 更新 running max：**
```
m_i^new = max(m_i, m_ij)
```
m_ij 是本 block 的 rowmax，與 running max 取大者。

**Step B — 更新 running sum：**
```
l_i^new = exp(m_i − m_i^new) · l_i + exp(m_ij − m_i^new) · l̃_ij
```

推導：舊的 `l_i` 是基於 `m_i` 算出來的，要換算到新 scale `m_i^new`：

```
Σ_{k<j} exp(x_k − m_i) · exp(m_i − m_i^new)   ← 舊的貢獻，rescaled
  +  Σ_{k=j} exp(x_k − m_ij) · exp(m_ij − m_i^new)  ← 新 block 的貢獻
= Σ_{k≤j} exp(x_k − m_i^new)   ✓
```

兩個 rescale factor 都是 exp(負數)，所以數值穩定。

**Step C — 更新 output（FA1：每步 normalize）：**
```
O_i ← diag(l_i^new)^{-1} · [diag(l_i) · exp(m_i − m_i^new) · O_i
                              + exp(m_ij − m_i^new) · P̃_ij · V_j]
```

方括號裡：前半項把舊的（已 normalize 的）O_i 乘回去 l_i 再 rescale；後半項加入新 block 的 P̃V。最後整個除以 l_i^new 得到新的 normalized output。

**Step D — 滾動狀態：**
```
m_i ← m_i^new
l_i ← l_i^new
```

完成所有 T_c 個 block 後，O_i 就是正確的 normalized attention output。

**數學等價性驗證（展開最後一步看）**

設最終狀態 m_i = m*（全局最大），l_i = Σ_k exp(S_ik − m*)（全局 sum）。
Step C 展開後就是標準 softmax 乘以 V，數學完全等價。

### FlashAttention-2 的核心改動：Unnormalized Accumulator

FA1 每個 block 都做一次 `diag(l_i^new)^{-1}` 的 normalize。這個 diagonal scaling 本身很快，但每步都做是浪費。

FA2 改成不中間 normalize，用一個 unnormalized accumulator Õ_i：

**Step C（FA2）：**
```
Õ_i^(j) = diag(exp(m_i^(j-1) − m_i^(j))) · Õ_i^(j-1)
           + exp(S_i^(j) − m_i^(j)) · V_j
```

只有 rescale（乘 exp factor），不除以 l。

**最後才做一次 normalize：**
```
O_i = diag(l_i^(T_c))^{-1} · Õ_i^(T_c)
```

**Logsumexp 儲存（backward 用）：**
```
L_i = m_i^(T_c) + log(l_i^(T_c))
```

L_i 是 log-sum-exp，一個 R^{B_r} 向量。Backward pass 用它重算 attention 矩陣（recomputation），不需要從 HBM 讀 N×N。

**FA1 vs FA2 的非 matmul 操作量對比：**

| 操作 | FA1 | FA2 |
|------|-----|-----|
| diagonal scaling（diag(exp) · O） | T_c 次（每 block）| 1 次（最後）|
| normalize（/ l） | T_c 次 | 1 次 |
| 省下的 diagonal 操作 | — | T_c − 1 次 |

對長序列（T_c 大），這個差異明顯。更重要的是 FA2 改善了 work partition（thread block 分配），讓 Tensor Core 利用率更高。

---

## Tiled Attention Kernel 骨架

以下是 FlashAttention 的 CUDA 偽碼，清楚標出記憶體層次：

```cuda
// 假設：處理第 i 個 query block，在一個 thread block 內
// gridDim.x = T_r（query blocks），gridDim.y = H（heads）
// blockDim：足夠處理 B_r × d 的 warp 數

__global__ void flash_attention_kernel(
    const half* Q,    // [N, d] in HBM
    const half* K,    // [N, d] in HBM
    const half* V,    // [N, d] in HBM
    half*       O,    // [N, d] in HBM
    float*      L,    // [N]    in HBM (logsumexp)
    int N, int d, int B_r, int B_c
) {
    int i = blockIdx.x;  // query block index

    // --- SRAM 宣告 ---
    __shared__ half  Q_i[B_r][D];     // query block，載入後固定
    __shared__ half  K_j[B_c][D];     // key block，每次換
    __shared__ half  V_j[B_c][D];     // value block，每次換
    __shared__ float S_ij[B_r][B_c];  // local attention scores
    __shared__ float O_i[B_r][D];     // unnormalized output accumulator

    // --- 讀 Q_i 進 SRAM（HBM → SRAM，一次性）---
    // 每個 thread 搬幾個元素
    load_block_from_HBM(Q + i * B_r * d, Q_i, B_r, d);  // ← HBM read

    // --- 初始化 running state（register 中）---
    float m_i[B_r];  // running max，全 -inf
    float l_i[B_r];  // running sum，全 0
    init_registers(m_i, l_i, B_r);
    zero_shared(O_i, B_r, d);

    // --- 主循環：掃過所有 KV block ---
    for (int j = 0; j < T_c; j++) {

        // --- 讀 K_j, V_j 進 SRAM（HBM → SRAM）---
        load_block_from_HBM(K + j * B_c * d, K_j, B_c, d);  // ← HBM read
        load_block_from_HBM(V + j * B_c * d, V_j, B_c, d);  // ← HBM read
        __syncthreads();

        // --- 以下全在 SRAM 內計算，不碰 HBM ---

        // Step 1: S_ij = Q_i @ K_j^T / sqrt(d)
        // 用 Tensor Core 做 GEMM（SRAM → register → SRAM）
        tensor_core_matmul(Q_i, K_j, S_ij, B_r, B_c, d);

        // (optional) causal mask：S_ij[row][col] = -inf if global_col > global_row
        apply_causal_mask(S_ij, i * B_r, j * B_c, B_r, B_c);

        // Step 2: 計算 block 的 rowmax 和 unnorm softmax
        float m_ij[B_r], l_tilde_ij[B_r];
        rowmax(S_ij, m_ij, B_r, B_c);             // SRAM → register
        // P_tilde_ij = exp(S_ij - m_ij)（in-place in S_ij）
        subtract_and_exp(S_ij, m_ij, B_r, B_c);   // SRAM（reuse buffer）
        rowsum(S_ij, l_tilde_ij, B_r, B_c);       // SRAM → register

        // Step 3: 更新 running max（FA2 unnorm accumulator 版）
        float m_i_new[B_r], alpha[B_r];
        for (int r = 0; r < B_r; r++) {
            m_i_new[r] = max(m_i[r], m_ij[r]);
            alpha[r]   = expf(m_i[r] - m_i_new[r]);   // rescale factor for old
        }

        // Step 4: rescale O_i（舊的 unnorm output）
        // O_i *= diag(alpha)（SRAM 逐 row 乘 scalar）
        scale_rows(O_i, alpha, B_r, d);

        // Step 5: O_i += P̃_ij @ V_j
        // exp(S_ij - m_ij) * exp(m_ij - m_i_new) = exp(S_ij - m_i_new)
        // 這裡 S_ij 已經是 P̃_ij，再乘 exp(m_ij - m_i_new) 後做 GEMM
        float beta[B_r];
        for (int r = 0; r < B_r; r++)
            beta[r] = expf(m_ij[r] - m_i_new[r]);
        scale_rows(S_ij, beta, B_r, B_c);
        tensor_core_matmul_accumulate(S_ij, V_j, O_i, B_r, d, B_c);  // O_i +=

        // Step 6: 更新 running sum
        for (int r = 0; r < B_r; r++) {
            l_i[r] = alpha[r] * l_i[r] + beta[r] * l_tilde_ij[r];
            m_i[r] = m_i_new[r];
        }

        __syncthreads();
    }  // 所有 KV block 掃完

    // --- 最後 normalize（SRAM）---
    for (int r = 0; r < B_r; r++)
        for (int c = 0; c < d; c++)
            O_i[r][c] /= l_i[r];

    // --- 寫 O_i 回 HBM（SRAM → HBM，一次性）---
    store_block_to_HBM(O_i, O + i * B_r * d, B_r, d);   // ← HBM write

    // --- 寫 L_i 回 HBM（logsumexp，供 backward 用）---
    for (int r = 0; r < B_r; r++)
        L[i * B_r + r] = m_i[r] + logf(l_i[r]);         // ← HBM write
}
```

**HBM 存取總結：**
- Q_i：讀一次（outer loop 外）
- K_j, V_j：各讀 T_c 次（每個 block 一次）
- O_i：寫一次（最後）
- N×N 矩陣 S/P：**完全不落地 HBM**，在 SRAM 內算完即棄

---

## IO Complexity 分析

**標準 attention HBM 存取量：**

```
Θ(Nd + N²)
```
N² 來自讀寫 S, P 矩陣。

**FlashAttention HBM 存取量：**

```
Θ(N²d / M)
```
M 是 SRAM 大小（bytes）。每個 (query block, KV block) pair 的 SRAM 貢獻 O(Md)，需要 O(N²/M) 個這樣的 block pair，所以是 O(N²d/M)。

實際上我們每個 query block 讀整個 K, V 一次：

```
HBM reads  = N × d（Q）+ T_r × N × d（K，每個 query block 讀一遍）
           + T_r × N × d（V）
           ≈ O(N × d × T_r)
           = O(N²d / B_r)
```

對大 N，這比 O(N²) 要好——因為 d 通常是 64 或 128，而 B_r 與 M 成正比，當 M 夠大（SRAM 能裝下幾個 KV block），整體讀寫量大幅低於標準 attention。

**論文原文數字（A100 80GB，非本機實測，引自 Dao et al. 2022）：**

- GPT-2（N=1024）：FA 比 PyTorch attention 快 **2-4×**
- 長序列（N=4096）：快 **5-9×**
- 訓練 GPT-2 end-to-end 加速：**3×**（forward + backward 加速，PyTorch baseline）

---

## FA1 → FA2 → FA3 演進概覽

| | FA1（2022）| FA2（2023）| FA3（2024）|
|---|---|---|---|
| **目標 GPU** | A100/V100 | A100/H100 | H100/Hopper |
| **Unnorm accumulator** | 否（每步 /l） | 是（最後才 /l） | 是 |
| **Work partition** | 按 batch/head 分 | 加入 seq len 維度分，減少 sync | Warp specialization |
| **非 matmul ops** | T_c 次 diag 乘 | 1 次 diag 乘 | 1 次 diag 乘 |
| **TMA（Tensor Memory Accelerator）** | 否 | 否 | 是（H100 硬體 DMA）|
| **Warp group** | 無特化 | 無特化 | Producer/consumer warp 分離 |
| **Persistent kernel** | 否 | 否 | 是 |
| **Causal mask 支援** | 是 | 是（tile 級別跳過）| 是（更細粒度）|
| **A100 速度（FP16，N=8192）** | ~72% MFU | ~73% MFU | H100 ~75%+ MFU |

**FA2 的 work partition 改動細節：**
FA1 把 outer loop 放在 KV block（T_c），inner loop 在 query block（T_r）。這導致當 batch size 小、head 數少時，SM 無法被填滿。FA2 把 query block loop 放外層，允許不同 query block 分配到不同 thread block，提升並行度。

**FA3 的 Hopper 特化：**
H100 有 TMA（Tensor Memory Accelerator）硬體 DMA，可以用非同步方式搬移 tile，讓計算和搬移重疊。FA3 把 warp 分成 producer（負責 TMA 搬 K/V）和 consumer（負責 Tensor Core 計算），完全 overlap，達成近乎完美的算術強度。

---

## Recomputation：為什麼不存 N×N

### Backward Pass 的困境

標準 attention backward 需要 P（softmax 後的 attention weight）：

```
dV = P^T dO
dP = dO V^T
dS = softmax_backward(P, dP)  ← 需要 P
```

P ∈ R^{N×N}，儲存它的記憶體是 O(N²)。對 N=8192，H=32 heads，一個 A100 的 80 GB 根本裝不下整個 batch 的 P。

### Recomputation（重算）是更好的取捨

FlashAttention 在 backward pass 中，**不從 HBM 讀 P，而是重算它。**

重算 P_ij 所需的資訊：
- Q_i, K_j, V_j（已在 HBM，backward 本來就需要讀）
- L_i（logsumexp，N 個 scalars per head，≪ N²）

重算過程和 forward 完全一樣，只是順帶算出 dQ, dK, dV。

**記憶體對比：**

| 方法 | 額外 HBM | 速度 |
|------|----------|------|
| 存 P（標準） | O(N²) per layer | backward 快（直接讀）|
| Recompute（FA） | O(N)（只存 L_i）| backward 多一次 forward-like compute，但省了 HBM bandwidth |

**為什麼 recompute 值得：**

1. O(N²) 記憶體在長序列/大 batch 根本存不下
2. 重算是 compute-bound（Tensor Core），而讀 HBM 是 memory-bound
3. 重算的 HBM 存取比讀完整 P 矩陣更少
4. 允許更大 batch size → 更高的 GPU 利用率

**與 gradient checkpointing 的關係：**
Gradient checkpointing（PyTorch 的 `checkpoint()`）是另一種 recompute 策略——它不存 activation，在 backward 時重跑 forward。FA 的 recomputation 和 gradient checkpointing 可以同時使用，但要注意不要雙重 recompute（讓 FA 處理 attention 的 recompute，checkpointing 處理其他 layer）。

---

## 踩雷集

**1. 序列長度不是 block size 整數倍**

如果 N 不整除 B_r 或 B_c，最後一個 block 需要 padding。常見錯誤是忘記在 softmax 前把 padding 位置的 score 設成 `-inf`，導致 padding token 影響 attention weight。FlashAttention 的 CUDA 實作用 `if (col < N)` guard，但自己寫 kernel 很容易漏。

**2. Causal mask 的 tiling 邊界**

Causal attention 要求 position i 只能 attend 到 position ≤ i。在 tiling 下，一個 KV block 可能同時含有「合法」和「非法」的位置。處理方式：
- 若整個 KV block 都在 query block 的「未來」→ 直接跳過（完整跳過整個 j iteration）
- 若 KV block 和 query block 有交叉 → 套 mask，逐元素設 `-inf`
- 若整個 KV block 都在「過去」→ 正常算

常見錯誤是把交叉 block 直接跳過或全部 mask，而不是精確處理邊界。

**3. Block size 選擇影響 SRAM 使用量**

B_r 和 B_c 決定 SRAM 需求：

```
SRAM = (B_r + B_c) × d × 2 bytes（Q/K/V）
     + B_r × B_c × 4 bytes（S_ij in fp32）
     + B_r × d × 4 bytes（O accumulator）
```

block size 太大 → 超出 SRAM → kernel 失敗或 spill 到 L2，速度暴跌。
block size 太小 → Tensor Core 效率差（小矩陣吞吐低）。
常見起點：B_r = B_c = 64 或 128，視 d 調整。

**4. Dropout 在 FA 的特殊處理**

標準 attention 可以在 P 上直接 dropout（P 已實體化）。FA 中 P 不實體化，dropout 要在 S_ij 算完後、用 P̃_ij 之前就做，且要保證相同的 dropout mask 在 backward recompute 時還原。FA 實作用 Philox RNG（可根據 sequence position 確定性還原 mask），自己實作 dropout + FA 非常容易出 bug。

**5. Multi-Query Attention（MQA）和 Grouped Query Attention（GQA）**

MQA 讓多個 Q head 共用同一組 K/V。在 FA 中，這意味著不同 Q head 的 outer loop 讀同一份 K/V block，需要改變 thread block 的索引邏輯。直接把 MHA 的 FA kernel 套到 GQA/MQA 會出現 K/V head 索引計算錯誤，導致 silent correctness bug（結果不對但不報錯）。

---

## 動手練習

1. **HBM 存取量計算**：對 N=4096, d=128, FP16, H=16 heads，計算標準 attention 和 FlashAttention（B_r = B_c = 64）的 HBM 讀寫量差異。

2. **Online softmax 驗證**：用 NumPy 實作 online softmax（迭代版），與 `scipy.special.softmax` 對同一向量比較輸出，確認數值等價。

3. **Block size 估算**：A100 每 SM 192 KB shared memory。若 d=64, FP16 for Q/K/V，FP32 for S_ij 和 O accumulator，估算在不超過 100 KB 的限制下，B_r × B_c 最大能設多少。

4. **Causal mask 實作**：寫一個 CPU 版的 tiled attention（不用 CUDA），在 tiling 迴圈中正確處理 causal mask 的三種 block 情形（全過去、邊界交叉、全未來）。

---

## 本章重點

- 標準 attention 的問題不是 FLOP 太多，是 N×N 中間矩陣造成大量 HBM 讀寫（memory-bound）
- FlashAttention 用 tiling 把計算留在 SRAM，從不把完整 N×N 矩陣寫到 HBM
- Online softmax 用 running max/sum + rescale factor `exp(m_old − m_new)` 允許分 block 計算正確的 softmax
- FA1 每步 normalize；FA2 用 unnormalized accumulator，最後才除 l，省去 T_c 次 diagonal scaling
- Backward pass 用 recomputation（只存 logsumexp L_i）重算 attention，省 O(N²) 記憶體
- FA3 在 Hopper 用 TMA + warp specialization 達到更高的 compute/memory overlap

---

## 自我檢核

- 說明 `exp(m_i − m_i^new)` 這個 rescale factor 從哪裡來，為什麼數值穩定。
- FA1 的 Step C 和 FA2 的 Step C 差在哪裡？省掉的是什麼操作？
- 為什麼存 L_i（logsumexp）就能在 backward 重算 P，而不需要存 P 本身？
- Causal mask 在 tiling 中，處理「邊界 block」（query 和 key block 有交叉）的正確做法是什麼？
- 為什麼 B_r/B_c 的選擇需要考慮 SRAM 大小而不只是 warp 數？

---

## 延伸閱讀

- **FlashAttention**：Dao et al. 2022，*FlashAttention: Fast and Memory-Efficient Exact Attention with IO-Awareness*，arXiv 2205.14135
- **FlashAttention-2**：Dao 2023，*FlashAttention-2: Faster Attention with Better Parallelism and Work Partitioning*，arXiv 2307.08691
- **FlashAttention-3**：Shah et al. 2024，*FlashAttention-3: Fast and Accurate Attention with Asynchrony and Low-precision*（Hopper 版）
- **Tri Dao 的 EfficientML lecture notes**：Stanford CS336，包含 IO complexity 完整推導與 benchmark 方法論

---

回顧：
- Ch 40（softmax/layernorm 基礎）：online softmax 的直覺建立於標準 numerically-stable softmax 之上
- Ch 38（GEMM deep dive）：tiling 技巧與 shared memory 用法和這裡完全同源
- Ch 30（Tensor Core）：FA 的 GEMM 部分（S = QK^T 和 O += PV）全走 Tensor Core
- Ch 25（profiling）：用 Nsight 看 HBM bandwidth utilization 是驗證 FA 加速的第一步

→ [Ch 42 低精度](./42-low-precision.md)
