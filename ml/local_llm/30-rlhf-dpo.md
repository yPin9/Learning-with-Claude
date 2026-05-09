# Ch 30 — RLHF / DPO 入門：偏好對齊概念

> 目標：理解 RLHF 和 DPO 解決什麼問題，以及它們如何成為讓模型「更乖」的最後一步。

## SFT 之後的問題

Instruction tuning（SFT）讓模型學會遵守指令，但還有一個問題：模型學到的是「我見過的回答」，而不是「什麼是好的回答」。

測試題：問同一個問題，模型可能給出這兩種回答：

```
A：「Python 的 list 是動態陣列，記憶體連續存放，append 是 O(1) 攤銷...」
B：「Python 的 list 挺好用的，你可以放很多東西，比如數字或字串，非常方便...」
```

兩個都「符合指令」，但 A 明顯更有幫助。SFT 本身沒辦法區分這個差異。

RLHF / DPO 解決的就是這個問題：**用人類偏好（哪個回答更好）來進一步調整模型**。

## RLHF（Reinforcement Learning from Human Feedback）

RLHF 是 InstructGPT / ChatGPT 使用的技術，分三個步驟：

### Step 1：SFT（已完成）

有一個 fine-tuned 模型。

### Step 2：訓練 Reward Model

人類比較同一個 prompt 的兩個回答（A vs B），標注哪個更好：

```
Prompt: "解釋梯度下降"
A（較差）: "梯度下降是一個方法"
B（較好）: "梯度下降是一種最佳化演算法..."
標注：B > A
```

用這些比較資料訓練一個 Reward Model（RM），讓 RM 能預測人類的偏好分數：

```python
# Reward Model 的輸出是一個純量：這個回答有多好
reward_A = reward_model(prompt + response_A)  # 如 2.1
reward_B = reward_model(prompt + response_B)  # 如 4.7
# 訓練目標：B 的分數要比 A 高
```

### Step 3：用 RL 最佳化原始模型

用 Reward Model 的分數作為 reward，用 PPO（Proximal Policy Optimization）調整 SFT 模型，讓它傾向生成高分的回答：

```
reward = RM(response) - β × KL(fine_tuned || original)
           ↑高品質獎勵   ↑不讓模型偏離太多（防止 reward hacking）
```

**RLHF 的問題**：
- 需要大量人工標注（昂貴）
- RL 訓練不穩定，難以調試
- PPO 的超參數多，容易失敗

## DPO（Direct Preference Optimization）

DPO 是 RLHF 的替代方案（Rafailov 等，2023），繞過了 Reward Model，直接用偏好資料調整模型：

```python
# DPO 的訓練資料格式
{
    "prompt": "解釋梯度下降",
    "chosen": "梯度下降是一種最佳化演算法，用於找函數最小值...",   # 較好的回答
    "rejected": "梯度下降是一個方法"                              # 較差的回答
}
```

**DPO 的損失函數**（簡化版）：

```python
def dpo_loss(model, ref_model, prompt, chosen, rejected, beta=0.1):
    # 計算模型對兩個回答的 log prob
    log_prob_chosen   = get_log_prob(model, prompt + chosen)
    log_prob_rejected = get_log_prob(model, prompt + rejected)

    # 計算原始模型的 log prob
    ref_log_prob_chosen   = get_log_prob(ref_model, prompt + chosen)
    ref_log_prob_rejected = get_log_prob(ref_model, prompt + rejected)

    # DPO 的隱式 reward
    reward_chosen   = beta * (log_prob_chosen   - ref_log_prob_chosen)
    reward_rejected = beta * (log_prob_rejected - ref_log_prob_rejected)

    # 讓 chosen 的隱式 reward 比 rejected 高
    loss = -F.logsigmoid(reward_chosen - reward_rejected)
    return loss
```

DPO 的優點：
- 不需要 Reward Model
- 比 PPO 穩定很多
- 同等效果，但計算更便宜

## 現代對齊技術的全景

| 技術 | 發布時間 | 特點 |
|------|---------|------|
| RLHF + PPO | InstructGPT, 2022 | 最原始，需要 Reward Model |
| DPO | 2023 | 不需要 RM，穩定，被廣泛採用 |
| ORPO | 2024 | 把 SFT 和 DPO 合為一步 |
| SimPO | 2024 | 不需要 ref model，更簡單 |

## 用 trl 套件做 DPO（有 GPU 時）

```python
from trl import DPOTrainer, DPOConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from datasets import Dataset

model     = AutoModelForCausalLM.from_pretrained("your-sft-model")
ref_model = AutoModelForCausalLM.from_pretrained("your-sft-model")  # 凍結

tokenizer = AutoTokenizer.from_pretrained("your-sft-model")

# 偏好資料集
dataset = Dataset.from_list([
    {
        "prompt":   "解釋梯度下降",
        "chosen":   "梯度下降是最佳化演算法...",
        "rejected": "梯度下降是一個方法",
    },
    # ... 幾千筆
])

config = DPOConfig(
    beta=0.1,
    learning_rate=5e-7,
    num_train_epochs=3,
)

trainer = DPOTrainer(
    model=model,
    ref_model=ref_model,
    args=config,
    train_dataset=dataset,
    tokenizer=tokenizer,
)
trainer.train()
```

## 在 CPU 上怎麼辦？

真實的 DPO 需要 GPU（同時跑 model 和 ref_model），CPU 不現實。

替代方案：
1. **用現成的 aligned 模型**：Qwen2.5-Instruct、Llama-3.2-Instruct 都已經做過 RLHF/DPO
2. **System prompt 模擬對齊**：在 Ollama Modelfile 裡寫強的 system prompt
3. **租 GPU**：Google Colab T4（免費）、Lambda Labs（便宜），足以跑小模型的 DPO

## 常見誤解

**「RLHF 讓模型更聰明」**：不對。RLHF/DPO 不增加知識，它改變的是回答風格和偏好。知識來自 pre-training。

**「DPO 資料越多越好」**：品質比數量重要。1000 筆高品質比較 vs 100000 筆低品質比較，前者效果更好。

**「可以用 GPT-4 輸出當 chosen」**：理論上可以，但各家都有服務條款限制（OpenAI 明確禁止用它的輸出訓練競爭模型）。

## 動手練習（概念驗證）

用一個玩具例子理解 DPO loss 的行為：

```python
import torch
import torch.nn.functional as F

# 假設一個極簡的「模型」：只有一個 token 的 log prob
# 比較：chosen response 的 log prob 比 rejected 高多少

beta = 0.1

# 情境一：模型正確偏好 chosen
log_p_chosen   = torch.tensor(-1.0)   # 較高
log_p_rejected = torch.tensor(-3.0)   # 較低
ref_chosen     = torch.tensor(-2.0)
ref_rejected   = torch.tensor(-2.0)

reward_chosen   = beta * (log_p_chosen   - ref_chosen)
reward_rejected = beta * (log_p_rejected - ref_rejected)
loss = -F.logsigmoid(reward_chosen - reward_rejected)
print(f"正確偏好時的 loss: {loss.item():.4f}")  # 應該很小

# 情境二：模型偏好 rejected（需要矯正）
log_p_chosen   = torch.tensor(-3.0)   # 較低
log_p_rejected = torch.tensor(-1.0)   # 較高
reward_chosen   = beta * (log_p_chosen   - ref_chosen)
reward_rejected = beta * (log_p_rejected - ref_rejected)
loss = -F.logsigmoid(reward_chosen - reward_rejected)
print(f"偏好錯誤時的 loss: {loss.item():.4f}")  # 應該較大
```

## 自我檢核

- [ ] 能用一段話解釋 RLHF 的三個步驟
- [ ] 理解 DPO 為什麼不需要 Reward Model
- [ ] 知道 DPO 訓練資料的格式（prompt / chosen / rejected）
- [ ] 跑過玩具 DPO loss 實驗，理解 loss 在兩種情境下的差異

→ [練習 D：fine-tune 小模型讓它說繁體中文並遵守格式指令](./practice-d-finetune-cht.md)
