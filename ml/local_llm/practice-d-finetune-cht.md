# 練習 D — Fine-tune 小模型讓它說繁體中文並遵守格式指令

> 目標：用 llama.cpp 的 CPU fine-tune，把一個英文主導的小模型調整成能穩定輸出繁體中文、遵守特定格式的版本。

## 任務規格

| 項目 | 規格 |
|------|------|
| 基礎模型 | Qwen2.5-1.5B-Instruct Q4_K_M（CPU 可跑） |
| 訓練方法 | llama.cpp finetune（LoRA） |
| 訓練資料 | 50 筆繁體中文 instruction 資料（自製） |
| 目標 | 模型輸出繁體中文比例 > 95%，遵守「條列格式」指令 |
| 評估 | 10 個測試 prompt，人工評分 |

## 任務一：準備高品質訓練資料

建立 `data/train.jsonl`，包含以下類別：

```python
# 資料分布
categories = {
    "格式遵守":  15,  # 要求用條列、表格、標題等格式
    "繁中詞彙":  15,  # 強化台灣常用詞（軟體→軟件改回，網路→網絡改回）
    "技術解釋":  10,  # ML/Python/系統相關
    "日常對話":  10,  # 確保不會變成純技術模型
}
```

**格式遵守範例（最重要）**：

```json
{"instruction": "請用條列式說明 Python 的三大特點", "input": "", "output": "Python 的三大特點：\n\n1. **簡潔易讀**：語法接近英語，程式碼可讀性高\n2. **跨平台**：可在 Windows、macOS、Linux 上執行\n3. **豐富生態**：有 NumPy、Pandas、PyTorch 等大量套件\n"}
{"instruction": "列出五種常見的排序演算法及其時間複雜度", "input": "", "output": "| 演算法 | 平均複雜度 | 最壞複雜度 |\n|--------|-----------|----------|\n| 泡沫排序 | O(n²) | O(n²) |\n| 快速排序 | O(n log n) | O(n²) |\n| 合併排序 | O(n log n) | O(n log n) |\n| 堆積排序 | O(n log n) | O(n log n) |\n| 插入排序 | O(n²) | O(n²) |\n"}
```

**繁體中文詞彙強化**：

```json
{"instruction": "什麼是軟體工程師？", "input": "", "output": "軟體工程師（Software Engineer）是負責設計、開發、測試和維護軟體系統的專業人員。..."}
{"instruction": "解釋什麼是網路協定", "input": "", "output": "網路協定（Network Protocol）是電腦網路中通訊的規則集合..."}
```

## 任務二：準備訓練資料格式

llama.cpp finetune 用純文字格式，需要把 JSONL 轉成 ChatML 格式：

```python
import json

def jsonl_to_chatml(input_path, output_path):
    with open(input_path, encoding='utf-8') as f:
        samples = [json.loads(line) for line in f]

    with open(output_path, 'w', encoding='utf-8') as f:
        for sample in samples:
            prompt = f"<|im_start|>system\n你是一個台灣資深工程師，只用繁體中文回答。<|im_end|>\n"
            prompt += f"<|im_start|>user\n{sample['instruction']}"
            if sample.get('input'):
                prompt += f"\n\n{sample['input']}"
            prompt += f"<|im_end|>\n<|im_start|>assistant\n{sample['output']}<|im_end|>\n"
            f.write(prompt + "\n")
    print(f"轉換完成：{len(samples)} 筆 → {output_path}")

jsonl_to_chatml("data/train.jsonl", "data/train_chatml.txt")
```

## 任務三：執行 fine-tuning

```bash
# 下載基礎模型
huggingface-cli download Qwen/Qwen2.5-1.5B-Instruct-GGUF \
    qwen2.5-1.5b-instruct-q4_k_m.gguf \
    --local-dir models/

# 執行 fine-tune（預計 30–60 分鐘，CPU）
./build/bin/llama-finetune \
    --model-base models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
    --train-data data/train_chatml.txt \
    --save-every 50 \
    --threads 8 \
    --lora-r 8 \
    --lora-alpha 16 \
    --batch 4 \
    --epochs 3 \
    --ctx 512 \
    --lr 1e-4 \
    --lora-out output/my-tw-lora.bin \
    2>&1 | tee training_log.txt
```

## 任務四：評估

設計 10 個測試 prompt，比較 fine-tune 前後的差異：

```python
test_prompts = [
    # 格式遵守
    "請用條列式說明三個 Python 的優點",
    "列出五種設計模式，用表格呈現",
    "以編號清單說明 Transformer 的主要零件",

    # 繁中一致性
    "什麼是軟體開發生命週期？",
    "解釋網路安全的重要性",
    "如何成為一名資深工程師？",

    # 技術問題
    "LoRA 和全量 fine-tuning 的差別是什麼？",
    "解釋梯度消失問題",

    # 一般對話
    "今天工作很累，有什麼建議？",
    "推薦幾本台灣工程師應該讀的書",
]
```

**評分標準**：

| 面向 | 滿分 | 說明 |
|------|------|------|
| 繁體中文正確率 | 30 | 不含簡體字、英文句子不超過 20% |
| 格式遵守 | 30 | 要求條列就給條列，要求表格就給表格 |
| 內容品質 | 25 | 是否準確、詳細 |
| 自然度 | 15 | 讀起來是否流暢，不像機翻 |

## 完整評估腳本

<details>
<summary>點開評估腳本</summary>

```python
import subprocess, json, re
from pathlib import Path

def run_model(model_path, lora_path, prompt, max_tokens=200):
    cmd = [
        "./build/bin/llama-cli",
        "-m", model_path,
        "-n", str(max_tokens),
        "--temp", "0.7",
        "--top-k", "40",
        "-p", prompt,
        "--no-display-prompt",
    ]
    if lora_path:
        cmd.extend(["--lora", lora_path])

    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    return result.stdout.strip()

def count_chinese_ratio(text):
    total = len([c for c in text if c.strip()])
    if total == 0: return 0
    chinese = len([c for c in text if '一' <= c <= '鿿'])
    return chinese / total

def format_prompt(instruction):
    return f"<|im_start|>system\n你是一個台灣資深工程師，只用繁體中文回答。<|im_end|>\n<|im_start|>user\n{instruction}<|im_end|>\n<|im_start|>assistant\n"

MODEL = "models/qwen2.5-1.5b-instruct-q4_k_m.gguf"
LORA  = "output/my-tw-lora.bin"

test_prompts = [
    "請用條列式說明三個 Python 的優點",
    "解釋什麼是網路協定",
    "LoRA 和全量 fine-tuning 的差別？",
]

print("=" * 60)
for prompt in test_prompts:
    print(f"\n[Prompt] {prompt}")
    fmt = format_prompt(prompt)

    out_base = run_model(MODEL, None,  fmt)
    out_ft   = run_model(MODEL, LORA,  fmt)

    ratio_base = count_chinese_ratio(out_base)
    ratio_ft   = count_chinese_ratio(out_ft)

    print(f"\n[Base]  中文率 {ratio_base:.0%}")
    print(out_base[:200])
    print(f"\n[FT]    中文率 {ratio_ft:.0%}")
    print(out_ft[:200])
    print("-" * 40)
```

</details>

## 測試用例

| 條件 | 期望 |
|------|------|
| 要求條列式 | 輸出有編號或 `-` 開頭的條列 |
| 問「軟體」相關 | 輸出「軟體」而非「軟件」 |
| 問「網路」相關 | 輸出「網路」而非「網絡」 |
| 整體中文率 | > 80%（訊息的主要語言是中文） |

## 自我檢核

- [ ] 準備了至少 30 筆 instruction 訓練資料
- [ ] 成功執行 llama.cpp fine-tune，有看到 loss 輸出
- [ ] 用評估腳本比較 fine-tune 前後的繁體中文率
- [ ] 至少有一個格式指令（條列/表格）在 fine-tune 後正確遵守

→ [Ch 31 推論加速：FlashAttention / KV-cache 原理](./31-inference-optimization.md)
