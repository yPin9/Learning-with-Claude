# Ch 1 — 學習地圖：LLM 是什麼做成的

> 目標：在腦裡建一張地圖——LLM 由哪些零件組成、這門課的 34 章對應到哪些零件、學到哪裡才算「真的懂」。

## LLM 是一個函數

先把神秘感拿掉。語言模型（Large Language Model, LLM）本質上是一個函數：

```
f(token_1, token_2, ..., token_n) → 下一個 token 的機率分布
```

輸入一串文字（先轉成整數 token），輸出「下一個字最可能是什麼」的機率表。反覆採樣這個輸出，就是你看到的「ChatGPT 在打字」。

沒有魔法，只有矩陣乘法。

## 一張圖看清楚全部

```
原始文字
   │
   ▼
┌──────────────┐
│  Tokenizer   │  把文字切成 token，每個 token 對應一個整數 ID
└──────┬───────┘
       │  [15496, 11, 995, 0]  ← "Hello, world!" 的 token ids
       ▼
┌──────────────┐
│  Embedding   │  每個 token ID → 一個向量（例如 4096 維）
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Transformer │  N 層 Transformer Block 堆疊
│   Block × N  │  每層做：Attention + FFN + Residual + LayerNorm
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  LM Head     │  把最後的向量投影回 vocab 大小，softmax 得到機率
└──────┬───────┘
       │
       ▼
  機率分布 → 採樣 → 下一個 token
```

這套流程從 Ch 7 開始拆，Ch 12 全部串起來。

## 五個核心零件

| 零件 | 負責什麼 | 對應章節 |
|------|----------|----------|
| **Tokenizer** | 文字 ↔ token id 互轉 | Ch 7, Ch 19 |
| **Embedding** | token id → 高維向量 | Ch 8 |
| **Transformer Block** | 向量 → 更好的向量（理解上下文） | Ch 9–11 |
| **訓練迴圈** | 調整所有參數讓 loss 下降 | Ch 2–6, Ch 20–22 |
| **推論引擎** | 有效率地跑訓練好的模型 | Ch 13–17, Ch 31–32 |

## 課程分六個 Part，解決六個不同的問題

**Part 1（Ch 1–6）：你現在不懂神經網路**

在碰 Transformer 之前，要先知道梯度下降是什麼、PyTorch 怎麼用。這六章是地基，想跳過的衝動壓住。

**Part 2（Ch 7–12）：Transformer 到底在算什麼**

Attention 是整個架構的靈魂，Ch 9 是這門課最密的一章。讀懂它，後面所有東西都會變得有邏輯。

**Part 3（Ch 13–17）：怎麼在自己電腦上跑現成模型**

在自己訓練之前，先體驗「跑起來」是什麼感覺。llama.cpp 和 Ollama 讓你用 CPU 在本機跑 7B/13B 模型。

**Part 4（Ch 18–24）：自己從頭訓練一個小模型**

規模不大（幾百萬參數），但每一步都是你自己寫的。練習 C 會訓練一個能生成古風中文的 character-level 模型。

**Part 5（Ch 25–30）：用自己的資料調校現成模型**

LoRA / QLoRA 是實務上最有用的技術——不用從頭訓練，只改少數參數，就能讓模型說繁體中文、遵守特定格式。

**Part 6（Ch 31–34）：讓模型能被人用**

推論優化、本地 API、RAG，把模型包裝成可以接入應用程式的服務。

## 「地端」是什麼意思

這門課的目標是**完全離線、完全在你自己的機器上**跑語言模型：

- 資料不送出去（隱私）
- 推論沒有 API 費用（成本）
- 可以客製模型行為（控制）

代價是：你需要夠多的 RAM（建議 16GB+），速度比 GPU 慢，最大能跑的模型大小有限。這門課所有實作都假設你**沒有 GPU**，純 CPU 跑。

## 什麼是「token」

很多人第一次聽到 token 會以為就是「字」，這不全然對。

Token 是 tokenizer 決定的基本單位。不同語言、不同 tokenizer 切法不同：

```python
# 用 tiktoken 示範
import tiktoken
enc = tiktoken.get_encoding("gpt2")

print(enc.encode("Hello, world!"))
# [15496, 11, 995, 0]  ← 4 個 token

print(enc.encode("你好世界"))
# [19526, 254, 22755, 238, 19526, 231, 30236, 233]  ← 8 個 token
# 中文比英文「貴」— 同樣的資訊量消耗更多 token
```

英文單字通常 1–2 個 token，中文一個字約 2 個 token（因為 BPE 原本針對英文語料設計）。這個不對稱是 Ch 19 自製 tokenizer 的動機之一。

## 「訓練」vs「推論」vs「fine-tuning」

這三個詞常被混用，在這門課裡定義清楚：

| 詞彙 | 意思 | 什麼時候做 |
|------|------|-----------|
| **Pre-training** | 從隨機初始化開始，在大量語料上訓練 | 一次性，花大量時間和算力 |
| **Fine-tuning** | 在已訓練的模型上用小量特定資料繼續訓練 | 客製行為時 |
| **推論（Inference）** | 用訓練好的模型產生輸出 | 每次使用模型 |

你在 ChatGPT 輸入問題、等待回答，那是推論。Anthropic 花幾個月在幾千張 GPU 上跑，那是 pre-training。你用自己的客服對話資料微調 Llama，那是 fine-tuning。

## 這門課不包含什麼

- **大規模分散式訓練**：我們不會真的用 100 張 GPU 訓練模型，但 Ch 23 會解釋它的原理。
- **模型架構創新**：不設計新的 attention 變體，只用 vanilla GPT 架構。
- **RLHF 完整實作**：Ch 30 只講原理，不跑 reward model 訓練流程（需要 GPU）。

## 動手練習

在開始 Ch 2 之前，先把環境裝好：

```bash
# 1. 確認 Python 版本
python --version  # 要 3.10+

# 2. 裝 PyTorch（CPU 版本）
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# 3. 裝後續會用到的套件
pip install numpy matplotlib tiktoken transformers datasets

# 4. 驗證 PyTorch 可用
python -c "import torch; print(torch.__version__); x = torch.tensor([1.0, 2.0]); print(x)"
```

如果看到版本號和 tensor 輸出，就可以繼續了。

## 自我檢核

- [ ] 能用一句話說明 LLM 的核心操作（next-token prediction）
- [ ] 知道 token 和「字」的差別
- [ ] 分得清楚 pre-training / fine-tuning / 推論三個詞
- [ ] PyTorch CPU 版安裝完成

這門課的其餘 33 章都在填滿這張地圖的細節。

→ [Ch 2 神經網路直覺：線性層 + 激活函數](./02-neural-network-basics.md)
