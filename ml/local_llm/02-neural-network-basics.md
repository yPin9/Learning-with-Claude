# Ch 2 — 神經網路直覺：線性層 + 激活函數

> 目標：從數學角度理解「神經元」是什麼，並用 PyTorch 寫出第一個可跑的神經網路。

## 神經元其實是個加權求和

一個神經元做的事只有兩步：

```
1. 把輸入乘上權重、加偏置：z = w₁x₁ + w₂x₂ + ... + wₙxₙ + b
2. 把結果丟進激活函數：y = activation(z)
```

用矩陣記法，如果輸入是向量 **x**（形狀 [n]），權重是矩陣 **W**（形狀 [m, n]），偏置是 **b**（形狀 [m]）：

```
z = Wx + b     ← 線性變換，輸出形狀 [m]
y = activation(z)
```

這就是 `nn.Linear(n, m)`——一個線性層，把 n 維輸入映射到 m 維輸出。

## 為什麼不能只用線性層

一個常見誤解：堆更多線性層，表達能力更強。

**不對。** 兩個線性層堆疊等於一個線性層：

```
y = W₂(W₁x + b₁) + b₂
  = W₂W₁x + W₂b₁ + b₂
  = Ax + c          ← 還是線性
```

不管堆幾層，沒有激活函數，整個網路只能擬合線性關係。語言、圖像、任何有意思的資料都是非線性的，光靠線性層解決不了。

## 三個最常見的激活函數

### ReLU（Rectified Linear Unit）

```python
def relu(x):
    return max(0, x)

# 特性：負數輸出 0，正數原樣輸出
# 計算超快，大部分 hidden layer 預設用這個
```

```
ReLU(x):
  -3 → 0
   0 → 0
   2 → 2
   5 → 5
```

### Sigmoid

```python
import math
def sigmoid(x):
    return 1 / (1 + math.exp(-x))

# 輸出範圍 (0, 1)
# 常用在二元分類的最後一層
```

### GELU（Gaussian Error Linear Unit）

LLM 裡幾乎都用 GELU，比 ReLU 多了個平滑的過渡區：

```
GELU(x) ≈ 0.5x(1 + tanh(√(2/π)(x + 0.044715x³)))
```

不需要背這個公式，記住「GELU 是 Transformer FFN 的標配」就夠了。

## 用 PyTorch 寫

```python
import torch
import torch.nn as nn

# 定義一個簡單的兩層網路
class SimpleNet(nn.Module):
    def __init__(self, input_dim, hidden_dim, output_dim):
        super().__init__()
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, output_dim)
        self.relu = nn.ReLU()

    def forward(self, x):
        x = self.fc1(x)     # 線性變換
        x = self.relu(x)    # 非線性
        x = self.fc2(x)     # 線性變換
        return x

# 建立一個輸入 4 維、隱藏 8 維、輸出 2 維的網路
model = SimpleNet(input_dim=4, hidden_dim=8, output_dim=2)

# 丟一筆資料進去（batch_size=3, input_dim=4）
x = torch.randn(3, 4)
output = model(x)
print(output.shape)  # torch.Size([3, 2])
```

## 查看網路有多少參數

```python
total_params = sum(p.numel() for p in model.parameters())
print(f"總參數量：{total_params}")
# fc1: 4×8 + 8 = 40
# fc2: 8×2 + 2 = 18
# 總計：58

# 查看每層的形狀
for name, param in model.named_parameters():
    print(f"{name}: {param.shape}")
# fc1.weight: torch.Size([8, 4])
# fc1.bias:   torch.Size([8])
# fc2.weight: torch.Size([2, 8])
# fc2.bias:   torch.Size([2])
```

## 神經網路和 LLM 的關係

LLM 裡的 FFN（Feed-Forward Network）就是這個結構的放大版：

```
FFN(x) = GELU(xW₁ + b₁)W₂ + b₂
```

輸入是 4096 維向量（hidden size），中間膨脹到 16384 維，再壓回 4096 維。結構完全相同，只是維度大了幾千倍。

## 動手練習

改造上面的 `SimpleNet`，把 ReLU 換成 GELU，驗證輸出形狀不變：

```python
import torch.nn.functional as F

class SimpleNetGELU(nn.Module):
    def __init__(self, input_dim, hidden_dim, output_dim):
        super().__init__()
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        x = F.gelu(self.fc1(x))  # ← 改這裡
        return self.fc2(x)

model = SimpleNetGELU(4, 8, 2)
x = torch.randn(3, 4)
print(model(x).shape)  # 應該還是 [3, 2]
```

接著故意把 `forward` 裡移掉激活函數，看輸出有沒有改變——維度不變，但模型的「表達能力」已經退化成線性了。

## 自我檢核

- [ ] 能用一句話解釋為什麼線性層堆疊還是線性
- [ ] 知道 ReLU / Sigmoid / GELU 各自的適用場景
- [ ] 跑過上面的程式碼，看到 `torch.Size([3, 2])`
- [ ] 會查模型的參數量

→ [Ch 3 反向傳播：梯度怎麼流](./03-backpropagation.md)
