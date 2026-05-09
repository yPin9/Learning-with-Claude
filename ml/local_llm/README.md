# 地端 LLM 全端工程：從訓練到部署

> 給只會 Python、想從零搞懂並親手打造地端語言模型的工程師。

這門課從神經網路基礎出發，一路走到 Transformer 架構、CPU 上的 pre-training、QLoRA fine-tuning、最後用 Ollama 部署自己的繁體中文小模型。每章都有可以跑的程式碼，理論與實作並行。

## 為什麼學這個？

- **不被雲端綁架**：地端跑模型，資料不出機器，推論成本為零。
- **真正理解 LLM**：會呼叫 API 不等於懂語言模型，自己訓練一遍才算懂。
- **fine-tuning 是工程師最有用的技能**：用自己的資料調出專屬模型，比 prompt engineering 強一個量級。

## 環境需求

- Python 3.10+
- PyTorch（CPU 版本即可）
- llama.cpp（源碼編譯）
- Ollama
- RAM 建議 16GB+（8GB 勉強可跑小模型）

## 課程地圖

### Part 1 — 神經網路補底
- [Ch 1 學習地圖：LLM 是什麼做成的](./01-llm-overview.md)
- [Ch 2 神經網路直覺：線性層 + 激活函數](./02-neural-network-basics.md)
- [Ch 3 反向傳播：梯度怎麼流](./03-backpropagation.md)
- [Ch 4 PyTorch 入門：Tensor / autograd / training loop](./04-pytorch-basics.md)
- [Ch 5 損失函數與優化器：Adam 在做什麼](./05-loss-and-optimizer.md)
- [Ch 6 過擬合防治：dropout / layernorm / 早停](./06-regularization.md)

### Part 2 — Transformer 解剖
- [Ch 7 語言模型是什麼：next-token prediction](./07-language-model-basics.md)
- [Ch 8 Embedding：把詞變成向量](./08-embeddings.md)
- [Ch 9 Attention：讓模型看全句](./09-attention.md)
- [Ch 10 Multi-head Attention + 位置編碼](./10-multihead-attention-and-pe.md)
- [Ch 11 Transformer Block：FFN / Residual / LayerNorm](./11-transformer-block.md)
- [Ch 12 GPT 架構：decoder-only 怎麼生成文字](./12-gpt-architecture.md)
- [練習 A：用純 PyTorch 從頭實作 tiny Transformer](./practice-a-tiny-transformer.md)

### Part 3 — 地端跑起來
- [Ch 13 模型格式：safetensors / GGUF 是什麼](./13-model-formats.md)
- [Ch 14 量化（Quantization）：INT8/INT4 背後在做什麼](./14-quantization.md)
- [Ch 15 llama.cpp 實戰：編譯、轉換、跑](./15-llamacpp.md)
- [Ch 16 Ollama 實戰：Modelfile / API / 換模型](./16-ollama.md)
- [Ch 17 硬體估算：RAM / tokens-per-sec 怎麼算](./17-hardware-estimation.md)
- [練習 B：用 Ollama 架一個本地 chat API，接 Python 客戶端](./practice-b-ollama-chat-api.md)

### Part 4 — 從頭訓練小模型（CPU 可跑版）
- [Ch 18 資料管線：語料清洗 + tokenization 流程](./18-data-pipeline.md)
- [Ch 19 自製 BPE Tokenizer](./19-bpe-tokenizer.md)
- [Ch 20 Pre-training loop：DataLoader / checkpointing](./20-pretraining-loop.md)
- [Ch 21 Loss 曲線解讀：如何判斷訓練是否健康](./21-loss-diagnostics.md)
- [Ch 22 學習率排程：warmup / cosine decay](./22-lr-schedule.md)
- [Ch 23 分散式訓練概念：DDP / gradient accumulation](./23-distributed-training.md)
- [Ch 24 評估：perplexity / 生成品質怎麼量](./24-evaluation.md)
- [練習 C：訓練一個 character-level 語言模型（金庸語料）](./practice-c-char-lm.md)

### Part 5 — Fine-tuning 與對齊
- [Ch 25 為什麼要 fine-tune：base vs instruct vs chat](./25-why-finetune.md)
- [Ch 26 全量 Fine-tuning：什麼情況才值得做](./26-full-finetuning.md)
- [Ch 27 LoRA 原理：低秩分解怎麼省參數](./27-lora.md)
- [Ch 28 QLoRA 實戰：CPU 上用 llama.cpp fine-tune](./28-qlora-cpu.md)
- [Ch 29 Instruction Tuning：資料格式 Alpaca / ChatML](./29-instruction-tuning.md)
- [Ch 30 RLHF / DPO 入門：偏好對齊概念](./30-rlhf-dpo.md)
- [練習 D：fine-tune 小模型讓它說繁體中文並遵守格式指令](./practice-d-finetune-cht.md)

### Part 6 — 部署與整合
- [Ch 31 推論加速：FlashAttention / KV-cache 原理](./31-inference-optimization.md)
- [Ch 32 本地推論服務：llama-server / Ollama API](./32-local-serving.md)
- [Ch 33 RAG 基礎：向量資料庫 + 檢索增強生成](./33-rag.md)
- [Ch 34 全棧回顧：raw text → 部署完成的 chatbot](./34-full-stack-review.md)
- [Final Project：訓練 + Fine-tune + 部署你自己的地端繁體中文小模型](./final-project-local-llm.md)

## 學習方式建議

1. **每章跑一遍程式碼**：不要只讀，看到 code block 就開 terminal 跑，改一個參數看結果怎麼變。
2. **Part 1-2 是地基**：想跳到 fine-tuning 的衝動先壓住，Transformer 不懂 LoRA 就是魔法。
3. **練習題先自己做**：參考解答藏在 `<details>` 裡，抵抗打開的誘惑至少一小時。

## 參考資料

- Andrej Karpathy《nanoGPT》— GitHub，從頭訓練 GPT 的最佳範例
- 《Dive into Deep Learning》— d2l.ai，免費線上書，數學解釋清楚
- llama.cpp 官方 README — 地端推論的第一手文件
- Hugging Face PEFT 文件 — LoRA / QLoRA 實作參考
