# Ch 25 — 為什麼要 fine-tune：base vs instruct vs chat

> 目標：理解三種主要模型類型的差別，以及 fine-tuning 在整個訓練管線中的位置。

## Pre-training 之後發生了什麼

你在 Ollama 跑的模型，幾乎不是從頭下載的 base model，而是經過多個 fine-tuning 階段的產物。

一個 LLM 的完整訓練流程：

```
大量文字語料
    │
    ▼ Pre-training（幾個月，幾千張 GPU）
    │
Base Model（會預測下一個 token，但不聽話）
    │
    ▼ Supervised Fine-tuning (SFT)
    │
Instruct Model（會遵守指令）
    │
    ▼ RLHF / DPO（偏好對齊）
    │
Chat Model（安全、有幫助、符合人類偏好）
```

## Base Model：什麼都做，但不聽話

Base model 的訓練目標只有一個：預測下一個 token。這讓它學到大量的世界知識，但它不懂「回答問題」。

```
輸入：「台灣的首都是什麼？」
Base model 輸出：「台灣的首都是什麼？這是許多人常問的問題。台灣的政治體制...」
（它在生成「接在問題後面最可能出現的文字」，不是在回答你）
```

Base model 更像是一個「任意補全器」——你給什麼開頭，它就補什麼。

## Instruct Model：聽指令的版本

Supervised Fine-tuning（SFT）用「問答對」微調 base model：

```python
# SFT 的訓練資料格式（Alpaca 格式）
{
    "instruction": "解釋什麼是梯度下降",
    "input": "",
    "output": "梯度下降是一種最佳化演算法，用於找到函數的最小值..."
}

# 訓練時，模型只計算 output 部分的 loss
# instruction 和 input 只是 context，不更新梯度
```

幾千–幾萬筆這樣的資料，就能讓 base model 學會「被問到問題就給出答案」的行為模式。

## Chat Model：安全對齊的版本

Instruct model 還是可能輸出有害、不準確或奇怪的內容。RLHF / DPO 進一步把模型的輸出對齊人類的偏好：

- 拒絕有害請求
- 承認自己不知道（而非瞎猜）
- 用更友善的語氣

## 三種模型的實際差異

| 面向 | Base | Instruct | Chat |
|------|------|----------|------|
| 遵守指令 | 不穩定 | 穩定 | 穩定 |
| 拒絕有害內容 | 不會 | 部分 | 會 |
| 對話格式 | 不懂 | 懂 | 懂 |
| 知識廣度 | 最高 | 和 base 相同 | 和 base 相同 |
| 適合 fine-tune 嗎？ | 是（可以完全客製） | 可以（針對特定任務） | 通常不推薦 |

**Fine-tuning 的起點通常是 base model 或 instruct model，不是 chat model**。Chat model 已經有安全限制，過度 fine-tune 可能破壞對齊。

## 什麼時候需要 fine-tune

| 場景 | 建議 |
|------|------|
| 一般問答 / 程式輔助 | 直接用現成的 instruct/chat model |
| 特定領域術語（法律、醫療） | Fine-tune |
| 特定格式輸出（JSON、表格） | Instruction tuning 或 fine-tune |
| 特定語言（繁體中文強化） | Fine-tune |
| 特定人格/語氣 | Fine-tune 或 system prompt |
| 注入最新知識 | RAG（Ch 33）通常比 fine-tune 好 |

## Fine-tuning 的成本

Fine-tuning 不需要重新訓練整個模型，但也不是免費的：

| 方法 | 硬體需求（7B 模型） | 時間 | 品質 |
|------|-----------------|------|------|
| 全量 FT（Full FT） | 多張 A100 | 數小時–天 | 最好 |
| LoRA | 1× 24GB GPU | 1–4 小時 | 接近全量 |
| QLoRA | 1× 8GB GPU | 2–8 小時 | 略低於 LoRA |
| llama.cpp fine-tune（CPU） | 32GB RAM | 幾天 | 比 LoRA 低 |

## 這門課的策略

因為你沒有 GPU：

1. **練習 D**：用 `llama.cpp` 的 CPU fine-tune，訓練一個能說繁體中文的小模型（3B 或更小）
2. **理解 LoRA/QLoRA 原理**（Ch 27–28），等有 GPU 時直接上手
3. **Final Project**：完整走一遍訓練 → fine-tune → 部署的流程，用 CPU 可跑的規模

## 動手練習

查看 Ollama 的模型是 base 還是 instruct：

```bash
ollama show llama3.2
# 注意 modelfile 裡的 chat template 和 system prompt
# Instruct 模型會有 chat template（如 [INST] ... [/INST]）
# Base 模型沒有或很簡單

# 測試：用 base-like 的方式補全文字
ollama run llama3.2 "The capital of France is"
# instruct 模型可能回答問題，base 模型會直接補全 "Paris"
```

## 自我檢核

- [ ] 能解釋 base / instruct / chat 三種模型的差別
- [ ] 知道 SFT 的訓練資料格式長什麼樣
- [ ] 理解什麼時候該 fine-tune，什麼時候用 RAG 或 prompt engineering
- [ ] 查過 Ollama 模型的 modelfile，判斷它是哪種類型

→ [Ch 26 全量 Fine-tuning：什麼情況才值得做](./26-full-finetuning.md)
