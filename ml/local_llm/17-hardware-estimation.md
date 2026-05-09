# Ch 17 — 硬體估算：RAM / tokens-per-sec 怎麼算

> 目標：在下載模型之前，能估算記憶體需求和推論速度，不浪費時間跑不起來的模型。

## 記憶體估算：最重要的問題

「這個模型我跑得動嗎？」答案取決於 RAM（沒有 GPU 的情況下）。

**粗略公式**：

```
模型記憶體（GB）≈ 參數量（B）× 每參數 bytes

每參數 bytes：
  FP32 → 4 bytes
  FP16 → 2 bytes
  Q8_0 → 1 byte
  Q4   → 0.5 bytes
```

```python
def estimate_model_memory(params_billion, dtype="Q4_K_M"):
    bytes_per_param = {
        "FP32":   4.0,
        "FP16":   2.0,
        "Q8_0":   1.0,
        "Q4_K_M": 0.5,
        "Q3_K_M": 0.375,
    }
    bpp = bytes_per_param[dtype]
    gb = params_billion * 1e9 * bpp / 1e9
    return gb

# 常見模型的記憶體需求
for model, params in [("3B", 3), ("7B", 7), ("13B", 13), ("70B", 70)]:
    for dtype in ["FP16", "Q4_K_M"]:
        print(f"{model} {dtype}: {estimate_model_memory(params, dtype):.1f} GB")

# 3B  FP16:   6.0 GB
# 3B  Q4_K_M: 1.5 GB
# 7B  FP16:  14.0 GB
# 7B  Q4_K_M: 3.5 GB
# 13B FP16:  26.0 GB
# 13B Q4_K_M: 6.5 GB
# 70B FP16: 140.0 GB
# 70B Q4_K_M:35.0 GB
```

## KV Cache 的額外記憶體

推論時，Transformer 的每一層需要儲存 Key 和 Value（KV Cache），讓生成不用每次重算整個序列：

```
KV Cache 大小 ≈ 2 × num_layers × num_kv_heads × d_head × seq_len × 2 bytes（FP16）
```

對 Llama 3 8B（32 層，8 個 KV head，d_head=128，GQA），context = 8192：

```python
layers = 32; kv_heads = 8; d_head = 128; seq = 8192
kv_bytes = 2 * layers * kv_heads * d_head * seq * 2  # bytes
print(f"KV Cache: {kv_bytes / 1e9:.2f} GB")  # 約 1.07 GB
```

**結論**：KV Cache 大約額外佔 1–2 GB（8K context），不是主要的記憶體殺手。

## 我能跑哪些模型？

| RAM | 可用模型 |
|-----|---------|
| 8 GB | 3B Q4_K_M 舒適；7B Q2_K 勉強 |
| 16 GB | 7B Q4_K_M 舒適；13B Q3_K_M 可跑 |
| 32 GB | 13B Q4_K_M 舒適；32B Q3_K 可跑 |
| 64 GB | 70B Q2_K 可跑；32B Q4_K_M 舒適 |

**注意**：這是模型記憶體，系統本身還需要 2–4 GB，算的時候要預留。

## tokens/second 估算

CPU 推論速度取決於：**記憶體頻寬（Memory Bandwidth）**

原因：推論時每次生成一個 token，要把整個模型從 RAM 讀一遍，計算量反而不是瓶頸。

```python
def estimate_tokens_per_second(ram_bandwidth_GBs, model_size_GB):
    """
    ram_bandwidth_GBs: RAM 頻寬（GB/s）
    model_size_GB: 量化後的模型大小（GB）
    """
    return ram_bandwidth_GBs / model_size_GB

# DDR4-3200：理論 51.2 GB/s，實際約 40 GB/s
# DDR5-6400：理論 102 GB/s，實際約 80 GB/s

# 7B Q4_K_M ≈ 4 GB
print(f"DDR4（40 GB/s）+ 7B Q4: {estimate_tokens_per_second(40, 4):.0f} tok/s")   # ≈ 10
print(f"DDR5（80 GB/s）+ 7B Q4: {estimate_tokens_per_second(80, 4):.0f} tok/s")   # ≈ 20
print(f"DDR5（80 GB/s）+ 3B Q4: {estimate_tokens_per_second(80, 1.8):.0f} tok/s") # ≈ 44
```

這只是理論值，實際會低 30–50%（作業系統 overhead、計算量等）。

## 查自己的記憶體頻寬

```bash
# Linux
sudo dmidecode -t memory | grep "Speed"

# Windows（PowerShell）
Get-WmiObject Win32_PhysicalMemory | Select-Object Speed, Manufacturer

# macOS
system_profiler SPMemoryDataType
```

常見配置：
- 雙通道 DDR4-3200：51.2 GB/s
- 雙通道 DDR5-5600：89.6 GB/s
- M1 MacBook（統一記憶體）：68.25 GB/s（M1 Pro/Max 更高）
- M3 Max MacBook：400 GB/s（接近低階 GPU）

Apple Silicon 的高記憶體頻寬是它跑 LLM 特別快的原因。

## 為什麼 GPU 快很多

GPU 的 HBM 頻寬遠高於 CPU RAM：

| 硬體 | 記憶體頻寬 |
|------|---------|
| 一般 CPU（DDR4） | 40–50 GB/s |
| CPU（DDR5） | 80–100 GB/s |
| RTX 4090 | 1008 GB/s |
| A100 80GB | 2000 GB/s |

這就是為什麼同一個 7B 模型，RTX 4090 能跑 100+ tokens/s，CPU 只能跑 10–20 tokens/s。

## Ollama 看實際速度

```bash
# 跑任何模型後，結尾會印出統計
ollama run llama3.2 "你好"

# 輸出包含：
# eval rate:       23.45 tokens/s  ← 生成速度
# prompt eval rate: 150.2 tokens/s ← 處理 prompt 的速度（prefill）
```

Prefill（處理輸入）比 decode（生成輸出）快很多，因為 prefill 可以並行計算。

## 動手練習

建立自己的「硬體規格 vs 模型能力」對應表：

```python
import platform, subprocess

# 1. 查 RAM 大小
# 2. 查 CPU 核心數
# 3. 用 Ollama 跑 3B 和 7B 模型，記錄 tokens/s
# 4. 根據公式估算你的 RAM 頻寬

# 填入你的數字
my_ram_gb = 16          # 你的 RAM
my_bandwidth_estimate = 40  # 估算的頻寬（DDR4 約 40，DDR5 約 70）

models = {"3B Q4": 1.8, "7B Q4": 4.0, "13B Q4": 7.0}
print("估算 tokens/second：")
for name, size in models.items():
    if size * 1.2 < my_ram_gb:  # 留 20% 給系統
        tps = my_bandwidth_estimate / size * 0.6  # 60% 效率
        print(f"  {name}: ~{tps:.0f} tok/s ✓")
    else:
        print(f"  {name}: RAM 不足 ✗")
```

## 自我檢核

- [ ] 能估算任意模型在特定量化下的記憶體需求
- [ ] 知道為什麼 CPU 推論速度受記憶體頻寬限制
- [ ] 查過自己機器的 RAM 大小和頻寬
- [ ] 用 Ollama 量測過實際的 tokens/s

→ [練習 B：用 Ollama 架一個本地 chat API，接 Python 客戶端](./practice-b-ollama-chat-api.md)
