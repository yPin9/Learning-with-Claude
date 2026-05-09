# Ch 34 — 全棧回顧：raw text → 部署完成的 chatbot

> 目標：把 34 章的所有知識串成一條線，列出完整的地端 LLM 工程師 checklist。

## 你走了多遠

```
Part 1 完成：神經網路、梯度、PyTorch、loss、優化器、正則化
Part 2 完成：語言模型、embedding、attention、Transformer、GPT
Part 3 完成：GGUF 格式、量化、llama.cpp、Ollama、硬體估算
Part 4 完成：資料管線、BPE tokenizer、pre-training、診斷、lr schedule、評估
Part 5 完成：fine-tune、LoRA、QLoRA、instruction tuning、RLHF/DPO
Part 6 完成：KV-cache、本地服務、RAG
```

你現在具備從零訓練模型、微調、部署的完整能力。

## 完整路徑示意圖

```
原始資料（網頁、書籍、對話）
       │
       ▼ Ch 18–19
  清洗 + BPE tokenization
       │
       ▼ Ch 20–24
  Pre-training（小模型 CPU 可跑）
       │
       ▼ Ch 29
  準備 instruction tuning 資料
       │
       ▼ Ch 25–28
  Fine-tuning（LoRA / QLoRA / llama.cpp）
       │
       ▼ Ch 13
  轉換 GGUF 格式
       │
       ▼ Ch 14
  量化（Q4_K_M）
       │
       ▼ Ch 16
  Ollama 匯入 + Modelfile 設定
       │
       ▼ Ch 32–33
  llama-server / Ollama API + RAG
       │
       ▼
  你的 chatbot 應用
```

## 各技術的選用指南

### 我應該用哪個推論引擎？

```
只想試試模型 → Ollama（最簡單）
需要 LoRA adapter → llama.cpp + --lora
需要 API 服務 → llama-server 或 Ollama API
需要高吞吐（有 GPU） → vLLM
```

### 我應該用哪種 fine-tuning？

```
沒有 GPU → llama.cpp finetune（CPU LoRA）
有 8–16GB VRAM → QLoRA（bitsandbytes + PEFT）
有 24GB+ VRAM → LoRA 或 Full FT（小模型）
有多張 A100 → Full FT 或 ZeRO Stage 2/3
```

### 我的模型需要最新知識或私有文件？

```
是 → RAG（Ch 33）
否 → 現有模型夠用
```

### 我需要模型「更聽話」？

```
輕微調整 → System prompt（Modelfile）
需要特定格式 → Instruction tuning（Ch 29）
需要特定人格/風格 → Fine-tuning（Ch 27–28）
需要安全對齊 → DPO（Ch 30）
```

## 常見坑和解法

| 坑 | 解法 |
|----|------|
| fine-tune 後模型退化成亂碼 | 降低 lr、減少 epochs、加早停 |
| 模型輸出簡體字 | 訓練資料全部繁體，加 system prompt |
| RAG 檢索不準 | 縮小 chunk_size、換更好的 embedding |
| llama.cpp 編譯失敗 | 確認有 C++ 編譯器，看 GitHub issues |
| Ollama 消耗太多 RAM | 換更小的模型或更高量化（Q2_K） |
| pre-training loss 不動 | 檢查 lr、梯度流、初始化 |

## 知識地圖：哪些概念有關聯

```
Transformer block
  ├── Attention（Ch 9）→ Multi-head（Ch 10）→ KV-cache（Ch 31）
  ├── FFN（Ch 11）→ SwiGLU、GELU（Ch 2）
  └── LayerNorm（Ch 6）→ RMSNorm（Ch 11）

訓練
  ├── Cross-entropy loss（Ch 5）→ Perplexity（Ch 24）
  ├── AdamW（Ch 5）→ LR schedule（Ch 22）
  └── Gradient clipping（Ch 20）→ Backprop（Ch 3）

Fine-tuning
  ├── LoRA（Ch 27）→ QLoRA（Ch 28）→ PEFT 套件
  ├── Instruction tuning（Ch 29）→ RLHF/DPO（Ch 30）
  └── llama.cpp finetune → 合併 GGUF → Ollama 部署
```

## 你應該能獨立完成的事

學完這 34 章 + 4 個練習，你應該能：

- [ ] 從頭訓練一個 character-level GPT，讓它生成特定風格的文字
- [ ] 用 Ollama 跑任何 GGUF 模型，並用 Python 呼叫
- [ ] 準備 instruction tuning 資料，用 llama.cpp 做 CPU fine-tuning
- [ ] 估算任意模型在你的機器上能不能跑、速度大概多快
- [ ] 建立一個 RAG 系統，讓模型「讀」你自己的文件
- [ ] 把模型包裝成本地 HTTP API

## 下一步建議

**如果想深入訓練：**
- Andrej Karpathy 的 nanoGPT、minGPT（GitHub）
- 《Dive into Deep Learning》— 免費線上書

**如果想深入部署：**
- vLLM（有 GPU 後）
- LangChain / LlamaIndex（RAG 框架）

**如果想深入 fine-tuning：**
- Axolotl（整合 QLoRA + datasets 的框架）
- Unsloth（快 2–5 倍的 QLoRA）

**如果想做繁體中文 LLM：**
- Taiwan LLM 相關專案（GitHub 搜尋）
- TAIDE（台灣 AI 對話引擎）

## Final Project 在等你

你已經有所有工具了。去做 Final Project 吧。

→ [Final Project：訓練 + Fine-tune + 部署你自己的地端繁體中文小模型](./final-project-local-llm.md)
