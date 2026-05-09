# Ch 27 — LoRA 原理：低秩分解怎麼省參數

> 目標：從數學層面理解 LoRA 為什麼有效，以及怎麼在 PyTorch 裡手動實作它。

## 問題：更新所有參數太貴

Full fine-tuning 要更新 7B 個參數，需要 112GB 記憶體。

LoRA（Low-Rank Adaptation）的觀察：**Fine-tuning 時，參數的更新矩陣是低秩的（low-rank）**。

什麼是「低秩」？一個 `[4096, 4096]` 的矩陣，理論上有 16M 個自由度。但 fine-tuning 的更新 ΔW 可以用兩個小矩陣的乘積近似：

```
ΔW ≈ A × B
其中 A: [4096, r]，B: [r, 4096]，r << 4096
```

參數量：`4096×r + r×4096 = 2×4096×r`

如果 r=8，更新矩陣的參數量：`2×4096×8 = 65536`，而不是 `4096²=16M`。

**縮小了 250 倍。**

## LoRA 的數學

原始的線性層：`h = Wx`（W 是凍結的）

加上 LoRA 之後：`h = Wx + ΔWx = Wx + BAx`

訓練時，W 凍結，只更新 A 和 B。推論時，可以把 ΔW = BA 合併回 W（沒有推論開銷）：

```
W_merged = W + B × A × scaling
scaling = alpha / r  （alpha 是 LoRA scaling 超參數）
```

## 用 PyTorch 手動實作 LoRA

```python
import torch
import torch.nn as nn

class LoRALinear(nn.Module):
    def __init__(self, original_linear: nn.Linear, rank: int, alpha: float = 1.0):
        super().__init__()
        in_features  = original_linear.in_features
        out_features = original_linear.out_features

        # 保留原始線性層（凍結）
        self.linear = original_linear
        self.linear.requires_grad_(False)

        # LoRA 分解矩陣
        self.lora_A = nn.Parameter(torch.randn(in_features, rank) * 0.01)
        self.lora_B = nn.Parameter(torch.zeros(rank, out_features))  # B 初始化為 0

        self.rank    = rank
        self.scaling = alpha / rank  # 縮放因子

    def forward(self, x):
        # 原始輸出 + LoRA 增量
        base_out = self.linear(x)
        lora_out = x @ self.lora_A @ self.lora_B * self.scaling
        return base_out + lora_out

    def merge_weights(self):
        """把 LoRA 合併回原始矩陣（推論時用）"""
        delta_W = (self.lora_A @ self.lora_B * self.scaling).T
        self.linear.weight.data += delta_W
        self.linear.requires_grad_(False)

# 測試
original = nn.Linear(64, 64)
lora = LoRALinear(original, rank=4, alpha=8.0)

x = torch.randn(8, 64)
out = lora(x)
print(out.shape)  # [8, 64]

# 統計 LoRA 的參數量
lora_params = sum(p.numel() for p in [lora.lora_A, lora.lora_B])
total_params = 64 * 64  # 原始線性層
print(f"LoRA 參數：{lora_params}（原始 {total_params}，壓縮到 {lora_params/total_params*100:.1f}%）")
```

## 為什麼 B 初始化為 0

訓練開始時，`ΔW = BA = 0`，LoRA 的輸出等於原始模型的輸出。這讓訓練從一個穩定點開始，不會一開始就破壞原始模型的能力。

## 把 LoRA 加到 GPT 模型

```python
def add_lora_to_model(model, rank=8, alpha=16.0, target_modules=None):
    """
    target_modules: 哪些層要加 LoRA（None 表示所有 Linear）
    """
    if target_modules is None:
        target_modules = ['qkv', 'proj', 'w1', 'w2']

    for name, module in model.named_modules():
        if isinstance(module, nn.Linear):
            if any(t in name for t in target_modules):
                # 替換成 LoRA 版本
                parent_name, child_name = name.rsplit('.', 1)
                parent = model.get_submodule(parent_name)
                lora_module = LoRALinear(module, rank=rank, alpha=alpha)
                setattr(parent, child_name, lora_module)

    return model

# 使用
model = GPT(vocab_size=..., d_model=128, ...)
model = add_lora_to_model(model, rank=8, alpha=16.0)

# 只有 LoRA 參數可以訓練
trainable = [p for p in model.parameters() if p.requires_grad]
frozen    = [p for p in model.parameters() if not p.requires_grad]
print(f"可訓練：{sum(p.numel() for p in trainable):,}")
print(f"凍結：  {sum(p.numel() for p in frozen):,}")
```

## LoRA 的超參數

| 超參數 | 說明 | 常見值 |
|--------|------|--------|
| `r`（rank） | 低秩近似的秩 | 8、16、32、64 |
| `alpha` | 縮放因子 | 通常設為 2r（如 r=8，alpha=16） |
| `target_modules` | 哪些層加 LoRA | 通常是 attention 的 Q/K/V/O 投影 |
| `dropout` | LoRA 層的 dropout | 0–0.1 |

**Rank 的選擇**：
- r=4：極少參數，適合資料量少或任務簡單
- r=8：常用甜蜜點
- r=64+：接近全量 FT 的效果，但資料要夠

## 用 Hugging Face PEFT 套件

實際使用不用手寫，直接用 PEFT：

```python
from peft import get_peft_model, LoraConfig, TaskType
from transformers import AutoModelForCausalLM

# 載入模型
model = AutoModelForCausalLM.from_pretrained("meta-llama/Llama-3.2-3B")

# 設定 LoRA
lora_config = LoraConfig(
    task_type=TaskType.CAUSAL_LM,
    r=16,
    lora_alpha=32,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    lora_dropout=0.05,
    bias="none",
)

# 套用 LoRA
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()
# trainable params: 6,815,744 || all params: 3,219,816,448 || trainable%: 0.2117
```

只有 0.2% 的參數需要訓練，記憶體需求從 48GB 降到幾 GB。

## 動手練習

在 Practice C 的小型唐詩 GPT 上加 LoRA，比較訓練效率：

```python
# 1. 用 Practice C 訓練好的模型
# 2. 凍結所有原始參數，加 LoRA
model = add_lora_to_model(pretrained_model, rank=4, alpha=8.0)

trainable_before = sum(p.numel() for p in pretrained_model.parameters())
trainable_after  = sum(p.numel() for p in model.parameters() if p.requires_grad)
print(f"參數壓縮：{trainable_after}/{trainable_before} = {trainable_after/trainable_before*100:.2f}%")

# 3. 用宋詞資料 fine-tune（用 LoRA，不更新原始參數）
# 4. 比較：LoRA vs Full FT 的收斂速度和最終品質
```

## 自我檢核

- [ ] 能用矩陣分解解釋 LoRA 為什麼省參數
- [ ] 理解為什麼 B 矩陣要初始化為 0
- [ ] 知道 `scaling = alpha / r` 的作用
- [ ] 手寫 `LoRALinear` 並驗證輸出形狀正確

→ [Ch 28 QLoRA 實戰：CPU 上用 llama.cpp fine-tune](./28-qlora-cpu.md)
