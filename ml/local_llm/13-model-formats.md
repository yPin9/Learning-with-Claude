# Ch 13 — 模型格式：safetensors / GGUF 是什麼

> 目標：理解訓練好的模型以什麼格式儲存，以及為什麼地端部署幾乎都用 GGUF。

## 模型檔案裡有什麼

訓練完成的模型本質上就是一堆**張量（tensor）**——也就是多維陣列，存著每一層的權重。

```
GPT-2 small：
  tok_emb.weight:    [50257, 768]   ← 38.6M 個 float32 數字
  blocks.0.attn.qkv: [2304, 768]
  blocks.0.attn.proj:[768, 768]
  ...（共 ~117M 個參數）
```

這些張量怎麼打包成一個檔案，就是格式的問題。

## 格式一：PyTorch `.pt` / `.pth`

PyTorch 原生格式，用 Python pickle 序列化：

```python
# 儲存
torch.save(model.state_dict(), "model.pt")

# 載入
state_dict = torch.load("model.pt")
model.load_state_dict(state_dict)
```

缺點：依賴 Python 環境，有安全問題（pickle 可以執行任意程式碼）。

## 格式二：safetensors

Hugging Face 推出的安全格式，純粹儲存張量，不執行任何程式碼：

```python
from safetensors.torch import save_file, load_file

# 儲存
save_file(model.state_dict(), "model.safetensors")

# 載入
state_dict = load_file("model.safetensors")
model.load_state_dict(state_dict)
```

檔案結構：JSON header（描述張量名稱、形狀、dtype）+ 緊跟著的二進制資料。可以只載入部分張量，不用把整個檔案讀進記憶體。

Hugging Face 上的模型現在大多提供 `.safetensors` 格式，比 `.bin`（pickle）安全。

## 格式三：GGUF（GPT-Generated Unified Format）

**llama.cpp 使用的格式，地端部署的標準。** 由 Georgi Gerganov（llama.cpp 作者）設計，取代舊的 GGML 格式：

```
GGUF 檔案結構：
┌─────────────────────────────────────┐
│ magic number: "GGUF"                │
│ version: 3                          │
├─────────────────────────────────────┤
│ metadata (key-value pairs):         │
│   general.architecture = "llama"    │
│   llama.context_length = 8192       │
│   tokenizer.ggml.model = "llama"    │
│   ...                               │
├─────────────────────────────────────┤
│ tensor info (name, shape, offset)   │
├─────────────────────────────────────┤
│ tensor data (連續二進制)             │
└─────────────────────────────────────┘
```

GGUF 的設計重點：
- **Self-contained**：tokenizer、模型架構描述、權重全在同一個檔案
- **Memory-mapped**：可以用 mmap 直接映射到記憶體，不用複製
- **量化友好**：內建多種量化格式（Q4_K_M、Q5_K_S 等）

## 量化後綴的意思

Hugging Face 上看到 `model-Q4_K_M.gguf` 這樣的名字：

| 後綴 | 意思 | 大小（7B 模型） | 品質 |
|------|------|---------------|------|
| F32 | 32-bit 浮點（原始） | ~28 GB | 最高 |
| F16 | 16-bit 浮點 | ~14 GB | 幾乎一樣 |
| Q8_0 | 8-bit 量化 | ~7 GB | 很好 |
| Q4_K_M | 4-bit 量化（K-Quant） | ~4 GB | 好（推薦） |
| Q3_K_M | 3-bit 量化 | ~3 GB | 勉強可用 |
| Q2_K | 2-bit 量化 | ~2 GB | 品質明顯下降 |

**CPU 跑的話，Q4_K_M 是甜蜜點**：品質接近 F16，但記憶體只需要 1/3–1/4。

## safetensors → GGUF 轉換

llama.cpp 提供轉換腳本：

```bash
# 把 Hugging Face 格式的 Llama 3 轉成 GGUF
python convert_hf_to_gguf.py \
    /path/to/llama-3-8b \
    --outtype f16 \
    --outfile llama-3-8b-f16.gguf

# 再量化成 Q4_K_M
./llama-quantize llama-3-8b-f16.gguf llama-3-8b-q4km.gguf Q4_K_M
```

## 查看 GGUF 檔案的 metadata

```python
# pip install gguf
from gguf import GGUFReader

reader = GGUFReader("llama-3-8b-q4km.gguf")

# 查看 metadata
for key, value in reader.fields.items():
    print(f"{key}: {value.parts[-1]}")

# 輸出範例：
# general.architecture: llama
# llama.context_length: 8192
# llama.embedding_length: 4096
# llama.block_count: 32
# tokenizer.ggml.model: llama
```

## 格式選擇指南

| 使用場景 | 推薦格式 |
|---------|---------|
| 用 PyTorch 訓練、fine-tune | safetensors |
| 用 llama.cpp / Ollama 跑 | GGUF (Q4_K_M) |
| Hugging Face 下載 | safetensors（然後轉 GGUF） |
| 發佈模型給他人使用 | safetensors（原始）+ GGUF（量化版） |

## 動手練習

用 Python 讀取一個 safetensors 檔案，查看其中的張量名稱和形狀：

```python
from safetensors import safe_open
import torch

# 如果沒有現成的 safetensors，先用 Ch 12 的模型生成一個
from safetensors.torch import save_file

# 假設你有 Ch 12 的 model
save_file(model.state_dict(), "tiny_gpt.safetensors")

# 查看內容
with safe_open("tiny_gpt.safetensors", framework="pt") as f:
    for key in f.keys():
        tensor = f.get_tensor(key)
        print(f"{key:40s}: {str(tensor.shape):30s} {tensor.dtype}")
```

## 自我檢核

- [ ] 能解釋 safetensors 比 pickle 安全的原因
- [ ] 知道 GGUF 和 safetensors 的使用場景差異
- [ ] 理解量化後綴（Q4_K_M）的意思
- [ ] 跑過 safetensors 的儲存和讀取

→ [Ch 14 量化（Quantization）：INT8/INT4 背後在做什麼](./14-quantization.md)
