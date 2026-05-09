# Ch 26 — 全量 Fine-tuning：什麼情況才值得做

> 目標：理解 Full Fine-tuning 的機制、成本、以及和 LoRA 相比的取捨。

## Full Fine-tuning 是什麼

Full Fine-tuning（全量微調）：用新的資料繼續訓練**所有**模型參數，就和 pre-training 一樣，只是換了資料和更小的 learning rate。

```python
# Full fine-tuning 的程式碼幾乎和 pre-training 一樣
model = load_pretrained_model("llama-3.2-3b")

# 所有參數都可以更新
optimizer = torch.optim.AdamW(model.parameters(), lr=1e-5)  # lr 遠小於 pre-training

for x, y in fine_tune_dataloader:
    optimizer.zero_grad()
    _, loss = model(x, y)
    loss.backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
    optimizer.step()
```

## 記憶體需求

Full fine-tuning 的記憶體需求遠超推論：

```
推論需要：模型參數（FP16）= 2 bytes/param
訓練需要：
  模型參數（FP32）= 4 bytes/param  ← 訓練時要 FP32
  梯度               = 4 bytes/param
  Adam 優化器狀態    = 8 bytes/param（m 和 v 各 4 bytes）
  總計 ≈ 16 bytes/param
```

7B 模型的 full fine-tuning 需要：
```
7B × 16 bytes = 112 GB 顯存
```

即使是 A100 80GB 也放不下，需要分散式或 ZeRO Stage 3。

## 什麼時候 Full Fine-tuning 值得

### 適合的情況

**語言遷移**：把英文 LLM 遷移到繁體中文，embedding 層和所有層都需要調整，LoRA 的低秩近似可能不夠。

**領域差距很大**：醫療病理報告、法律文書、程式語言（特別是新語言），base model 見過的資料極少，需要深度調整。

**你有充足的高品質資料**（>10 萬筆對話對），LoRA 在資料量很大時收益邊際遞減，但 full FT 持續提升。

### 不適合的情況

- 資料量少（<1 萬筆）：full FT 容易過擬合
- 硬體不足：沒有多張 A100
- 任務和 base model 差異不大：LoRA 效果接近全量

## 學習率的選擇

Full fine-tuning 的 lr 比 pre-training 小 10–100 倍：

| 階段 | 典型 lr |
|------|---------|
| Pre-training（GPT-2 規模） | 3e-4 |
| Pre-training（Llama 3 規模） | 3e-4 |
| Full fine-tuning | 1e-5 – 5e-5 |
| LoRA fine-tuning | 1e-4 – 3e-4 |

**為什麼 fine-tuning 用更小的 lr**：模型已經有很好的初始化，大步更新容易破壞已學到的知識（catastrophic forgetting）。

## Catastrophic Forgetting 問題

Full fine-tuning 的最大風險：**新任務的學習覆蓋了原本的知識**。

例如：你用「客服對話」fine-tune Llama，模型可能忘記了 Python 程式設計的能力。

緩解方法：

**1. 混合原始資料**：把少量 pre-training 資料混入 fine-tuning batch（通常 5–10%）。

**2. 較小 learning rate**：越小的 lr，catastrophic forgetting 越少（但收斂越慢）。

**3. Regularization**：在 loss 裡加入對原始模型的 KL 散度懲罰（稱為 proximal policy 或 anchoring）：

```python
# 概念：讓 fine-tuned 模型的輸出分布不要偏離原始模型太多
original_model = load_original()  # 凍結，不更新

for x, y in loader:
    fine_tuned_logits, ft_loss = model(x, y)
    orig_logits, _ = original_model(x)  # 不需要梯度

    # KL 散度懲罰
    kl_loss = F.kl_div(
        F.log_softmax(fine_tuned_logits, dim=-1),
        F.softmax(orig_logits, dim=-1),
        reduction='batchmean'
    )
    total_loss = ft_loss + 0.1 * kl_loss
    total_loss.backward()
```

## 部分參數 fine-tuning（折中方案）

在 full FT 和 LoRA 之間有個折中：只 fine-tune 某些層：

```python
# 只 fine-tune 最後幾個 Transformer block 和 LM head
for name, param in model.named_parameters():
    if 'blocks.30' in name or 'blocks.31' in name or 'lm_head' in name:
        param.requires_grad = True
    else:
        param.requires_grad = False  # 凍結

# 只更新可訓練的參數
trainable = [p for p in model.parameters() if p.requires_grad]
optimizer = torch.optim.AdamW(trainable, lr=2e-5)

total = sum(p.numel() for p in model.parameters())
trainable_count = sum(p.numel() for p in trainable)
print(f"可訓練參數：{trainable_count/1e9:.3f}B / {total/1e9:.3f}B ({trainable_count/total*100:.1f}%)")
```

最後 2 層通常只有 5–10% 的參數，記憶體需求大幅降低。

## 動手練習

在 Ch 12 的小型 GPT 上，比較全量 FT 和部分 FT 的效果：

```python
# 假設你有一個預訓練好的唐詩模型（來自 Practice A/C）
# 現在要讓它學「宋詞」風格

# 1. 全量 FT：所有參數更新
# 2. 部分 FT：只更新最後一個 block
# 3. 比較：新任務（宋詞）的 loss 下降速度，以及舊任務（唐詩）有沒有遺忘

import copy

# 備份原始模型
original_model = copy.deepcopy(model)

# 全量 FT
def full_ft(model, new_data, steps=200):
    opt = torch.optim.AdamW(model.parameters(), lr=5e-5)
    for step in range(steps):
        x, y = get_batch(new_data, 16, 128)
        _, loss = model(x, y)
        opt.zero_grad(); loss.backward(); opt.step()
    return model

# 測試遺忘程度
model_full_ft = full_ft(copy.deepcopy(model), ci_data)
old_ppl = compute_perplexity(model_full_ft, val_data)  # 唐詩 ppl
new_ppl = compute_perplexity(model_full_ft, ci_val)    # 宋詞 ppl
print(f"全量 FT：唐詩 ppl={old_ppl:.1f}，宋詞 ppl={new_ppl:.1f}")
```

## 自我檢核

- [ ] 能計算 7B 模型 full FT 需要多少 VRAM
- [ ] 理解 catastrophic forgetting 是什麼，三種緩解方法各是什麼
- [ ] 知道什麼情況選 full FT，什麼情況選 LoRA
- [ ] 跑過部分 FT 的練習，查過可訓練參數比例

→ [Ch 27 LoRA 原理：低秩分解怎麼省參數](./27-lora.md)
