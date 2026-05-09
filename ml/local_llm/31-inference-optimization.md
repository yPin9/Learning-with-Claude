# Ch 31 — 推論加速：FlashAttention / KV-cache 原理

> 目標：理解 KV-cache 是什麼、為什麼是推論的核心，以及 FlashAttention 解決了什麼問題。

## 推論的兩個階段

LLM 生成文字分兩個截然不同的階段：

```
Prefill（預填充）：
  輸入 prompt，一次性計算所有 token 的表示
  特點：可以並行計算，速度快
  例：處理 "解釋 Transformer 架構" 這 11 個 token

Decode（解碼）：
  逐個 token 生成，每次只生成一個新 token
  特點：必須是序列的，速度受記憶體頻寬限制
  例：生成 "Transformer 是一種..." 一個字一個字出來
```

這兩個階段的速度差異很大：prefill 快，decode 慢。

## KV-cache：避免重複計算

在 decode 階段，每次生成新 token 時，如果重新計算所有之前 token 的 Key 和 Value，計算量會是 O(n²)（n 是目前序列長度）。

**KV-cache**：把每一層的 K 和 V 快取起來，生成新 token 時只需要計算新 token 的 Q，然後和快取的 K/V 做 attention：

```python
class GPTWithKVCache(nn.Module):
    def __init__(self, ...):
        super().__init__()
        # 快取：每一層一個 K 和 V 的 cache
        self.kv_cache = {}  # {layer_idx: (K_cache, V_cache)}

    def forward_with_cache(self, new_token_id, layer_idx, past_k=None, past_v=None):
        # 只計算新 token 的 Q, K, V
        x = self.tok_emb(new_token_id)  # [1, 1, d_model]

        q = self.Wq(x)  # [1, 1, d_k]
        k = self.Wk(x)  # [1, 1, d_k]
        v = self.Wv(x)  # [1, 1, d_v]

        # 拼上之前快取的 K, V
        if past_k is not None:
            k = torch.cat([past_k, k], dim=1)  # [1, seq_len+1, d_k]
            v = torch.cat([past_v, v], dim=1)  # [1, seq_len+1, d_v]

        # 新的 Q 對所有 K 做 attention
        scores = q @ k.transpose(-2, -1) / math.sqrt(self.d_k)
        attn   = torch.softmax(scores, dim=-1)
        out    = attn @ v  # [1, 1, d_v]

        return out, k, v  # 返回更新後的 cache
```

**KV-cache 的記憶體代價**（Ch 17 提過）：

```
大小 = 2 × num_layers × num_kv_heads × d_head × seq_len × dtype_bytes
```

序列越長，KV cache 越大。這就是為什麼長 context 的模型需要更多 RAM。

## FlashAttention：解決注意力的記憶體瓶頸

標準 attention 計算的記憶體問題：

```python
# 標準計算：需要把 seq_len × seq_len 的矩陣放進 GPU HBM
scores  = Q @ K.T                    # [seq, seq] 全部存在記憶體裡
weights = softmax(scores, dim=-1)    # [seq, seq]
output  = weights @ V                # [seq, d_v]
```

對 seq_len=4096，`scores` 就需要 `4096 × 4096 × 2 bytes = 32 MB`（每個 attention head）。有 32 個 head，32 層，就是 `32 GB`——這就是為什麼長 context 這麼貴。

**FlashAttention**（Dao 等，2022）的核心思想：**不要把完整的 attention 矩陣寫進 HBM**，改為分塊計算，在 SRAM（快）裡完成 softmax 和乘法，再寫回 HBM：

```
傳統：O(n²) 的 HBM 讀寫
FlashAttention：O(n) 的 HBM 讀寫（塊大小是常數）
```

數學結果完全相同，速度快 2–8 倍，記憶體少 4–20 倍。

```python
# PyTorch 2.0 內建 FlashAttention
# 只需要用 F.scaled_dot_product_attention，它會自動用 FlashAttention
import torch.nn.functional as F

output = F.scaled_dot_product_attention(
    Q, K, V,
    attn_mask=None,
    is_causal=True,    # 因果注意力
    dropout_p=0.0,
)
# PyTorch 會自動選擇最快的後端（FlashAttention / math / xformers）
```

## 推論時的其他優化

### Continuous Batching

傳統 serving 要等一個 batch 全部完成才開始下一個。Continuous batching（vLLM 的核心技術）讓不同長度的請求可以混在一起，用完就釋放，效率更高。

### Speculative Decoding

用一個小（快）模型先草稿生成幾個 token，再用大（慢）模型驗證。驗證一次就能接受多個 token，提高吞吐量。

### Quantization（已在 Ch 14 介紹）

INT4 量化讓模型和 KV cache 都更小，也更快。

## CPU 上你能做的優化

沒有 GPU 的情況下，llama.cpp 已經替你做了很多：

```bash
# 查看 llama.cpp 使用的後端
./build/bin/llama-cli --list-devices

# 用 numa 親和性優化（多路 CPU）
numactl --cpunodebind=0 ./build/bin/llama-cli ...

# 增加 context 長度的快取（會佔更多 RAM）
./build/bin/llama-cli -m model.gguf -c 8192  # 8K context

# 控制 batch size（prefill 的平行度）
./build/bin/llama-cli -m model.gguf --batch-size 512
```

## 動手練習

測量 KV cache 對生成速度的影響：

```python
import torch, math, time
import torch.nn as nn, torch.nn.functional as F

# 用 Ch 12 的 GPT 模型
# 比較有無 KV cache 的生成速度

def generate_no_cache(model, prompt_ids, n=50):
    ids = prompt_ids.clone()
    start = time.time()
    for _ in range(n):
        logits, _ = model(ids)
        next_tok = logits[0, -1, :].argmax().unsqueeze(0).unsqueeze(0)
        ids = torch.cat([ids, next_tok], dim=1)
    elapsed = time.time() - start
    return n / elapsed  # tokens/second

# 計算並印出 tokens/s
tps = generate_no_cache(model, torch.zeros(1, 10, dtype=torch.long), n=30)
print(f"無 cache：{tps:.1f} tok/s")
# 可以觀察到隨著序列變長，速度會越來越慢
```

## 自我檢核

- [ ] 理解 prefill 和 decode 兩個階段的速度差異
- [ ] 能解釋 KV-cache 省了什麼計算
- [ ] 知道 KV-cache 的大小公式
- [ ] 理解 FlashAttention 優化的是記憶體讀寫（IO）而非浮點運算

→ [Ch 32 本地推論服務：llama-server / Ollama API](./32-local-serving.md)
