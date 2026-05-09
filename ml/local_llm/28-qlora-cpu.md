# Ch 28 — QLoRA 實戰：CPU 上用 llama.cpp fine-tune

> 目標：用 llama.cpp 的 finetune 功能，在沒有 GPU 的情況下，對 GGUF 模型做 LoRA fine-tuning。

## QLoRA 是什麼

QLoRA（Quantized LoRA）= 量化（Q）+ LoRA，最初由 Dettmers 等人提出（2023）：

- 模型用 4-bit 量化載入（省記憶體）
- LoRA adapter 用 FP16/BF16 訓練
- 反向傳播時動態反量化計算梯度

原版 QLoRA 需要 CUDA（`bitsandbytes` 套件），但 llama.cpp 實作了純 CPU 版本。

## llama.cpp 的 finetune 功能

llama.cpp 提供 `llama-finetune`（或 `finetune`）工具：

```bash
# 查看 finetune 工具是否存在
ls build/bin/llama-finetune  # Linux/macOS
dir build\bin\llama-finetune.exe  # Windows
```

如果沒有，需要重新編譯（預設有包含）：

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --target llama-finetune -j
```

## 準備訓練資料

llama.cpp fine-tune 接受純文字格式的訓練檔：

```bash
# 格式一：純文字，模型學習補全
cat train.txt
# 春眠不覺曉，處處聞啼鳥。
# 夜來風雨聲，花落知多少。
# 床前明月光，疑是地上霜。
# ...
```

或是 instruction tuning 格式（需要特定模板）：

```
### Human: 請寫一首關於春天的七言絕句
### Assistant: 春雨細細潤無聲，萬紫千紅映眼明。梨花帶雨人如玉，柳絮飄飄入畫屏。
```

## 執行 fine-tune

```bash
./build/bin/llama-finetune \
    --model-base models/qwen2.5-1.5b-q4km.gguf \
    --train-data train.txt \
    --save-every 100 \
    --threads 8 \
    --lora-r 4 \
    --lora-alpha 8 \
    --batch 4 \
    --epochs 3 \
    --ctx 256 \
    --lr 1e-4 \
    --lora-out lora-output.bin
```

**參數說明**：
- `--model-base`：起點模型（GGUF 格式）
- `--lora-r`：LoRA rank（越大效果越好，但越慢）
- `--lora-alpha`：通常設為 `2 × lora-r`
- `--batch`：batch size（CPU 上 4–8 就好）
- `--ctx`：context length（越長越慢）
- `--epochs`：訓練幾輪
- `--threads`：CPU 執行緒數

## 重要的現實問題

CPU fine-tuning **非常慢**。

```
評估（1.5B 模型，Q4_K_M，純 CPU）：
  batch=4, ctx=256, lora-r=4
  → 約 5–10 分鐘/epoch（取決於資料量和 CPU）

對 3B 模型：
  → 可能需要幾小時

建議：
  - 用最小的模型（1.5B 或更小）
  - epochs 設 1–3（不要太多，容易過擬合）
  - ctx 盡量短（256 或以下）
  - lora-r 設小（4–8）
```

## 使用 LoRA adapter 推論

fine-tune 輸出的是 `.bin` 格式的 LoRA adapter，不是完整模型：

```bash
# 方法一：推論時動態載入 adapter
./build/bin/llama-cli \
    -m models/qwen2.5-1.5b-q4km.gguf \
    --lora lora-output.bin \
    -p "寫一首七言絕句：" \
    -n 100

# 方法二：把 adapter 合併進模型（一次性）
./build/bin/llama-export-lora \
    -m models/qwen2.5-1.5b-q4km.gguf \
    --lora lora-output.bin \
    -o models/qwen2.5-1.5b-finetuned.gguf
```

合併後的模型可以直接用 Ollama 跑：

```bash
# 建立 Modelfile
cat > Modelfile << EOF
FROM ./models/qwen2.5-1.5b-finetuned.gguf
SYSTEM "你是一個擅長寫古典詩詞的 AI。"
EOF

ollama create my-poet -f Modelfile
ollama run my-poet "寫一首關於中秋的詩"
```

## 用 Hugging Face + PEFT 做 QLoRA（如果未來有 GPU）

```python
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import get_peft_model, LoraConfig, prepare_model_for_kbit_training
from trl import SFTTrainer

# 4-bit 量化載入
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_compute_dtype=torch.bfloat16,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_use_double_quant=True,
)

model = AutoModelForCausalLM.from_pretrained(
    "Qwen/Qwen2.5-7B",
    quantization_config=bnb_config,
    device_map="auto",
)

model = prepare_model_for_kbit_training(model)
model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, ...))
# 然後用 SFTTrainer 訓練
```

## 替代方案：unsloth（更快的 QLoRA）

如果你有 NVIDIA GPU，`unsloth` 讓 QLoRA 快 2–5 倍：

```python
from unsloth import FastLanguageModel
model, tokenizer = FastLanguageModel.from_pretrained("unsloth/Qwen2.5-7B-bnb-4bit")
model = FastLanguageModel.get_peft_model(model, r=16)
# 然後正常訓練
```

## 動手練習

在最小的可用模型上試跑 llama.cpp fine-tune：

```bash
# 1. 下載一個小模型（Qwen 1.5B 是不錯的起點）
ollama pull qwen2.5:1.5b

# 2. 找到 GGUF 路徑（Ollama 存在 ~/.ollama/models/blobs/）
# 或直接從 Hugging Face 下載：
# huggingface-cli download Qwen/Qwen2.5-1.5B-Instruct-GGUF \
#   qwen2.5-1.5b-instruct-q4_k_m.gguf

# 3. 準備 20–50 行訓練資料（古詩或你想要的任何風格）

# 4. 跑 fine-tune（1 epoch，lora-r=4）
./build/bin/llama-finetune \
    --model-base qwen2.5-1.5b-instruct-q4_k_m.gguf \
    --train-data train.txt \
    --epochs 1 --batch 4 --ctx 128 \
    --lora-r 4 --lora-alpha 8 \
    --lora-out my-lora.bin

# 5. 測試前後差異
./build/bin/llama-cli -m qwen2.5-1.5b-instruct-q4_k_m.gguf -p "春天" -n 30
./build/bin/llama-cli -m qwen2.5-1.5b-instruct-q4_k_m.gguf --lora my-lora.bin -p "春天" -n 30
```

## 自我檢核

- [ ] 理解 QLoRA 和 LoRA 的差異（量化基礎 + LoRA adapter）
- [ ] 成功跑了 llama.cpp finetune（哪怕只是 1 epoch）
- [ ] 知道如何用 `--lora` 載入 adapter 做推論
- [ ] 理解 CPU fine-tune 的速度限制，知道何時應該租 GPU

→ [Ch 29 Instruction Tuning：資料格式 Alpaca / ChatML](./29-instruction-tuning.md)
