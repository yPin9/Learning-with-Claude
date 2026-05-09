# Ch 4 — PyTorch 入門：Tensor / autograd / training loop

> 目標：掌握 PyTorch 的核心工具箱，能獨立寫出完整的訓練迴圈。

## Tensor 是什麼

Tensor 是 PyTorch 的基本資料結構，就是多維陣列——和 NumPy 的 `ndarray` 幾乎一樣，但多了 GPU 支援和自動微分。

```python
import torch

# 建立 tensor 的幾種方式
a = torch.tensor([1.0, 2.0, 3.0])        # 從 list
b = torch.zeros(3, 4)                     # 全 0，形狀 [3, 4]
c = torch.ones(2, 3)                      # 全 1
d = torch.randn(4, 4)                     # 標準常態分布的隨機值
e = torch.arange(0, 10, 2)               # [0, 2, 4, 6, 8]

# 查屬性
print(a.shape)    # torch.Size([3])
print(a.dtype)    # torch.float32
print(a.device)   # cpu
```

## 常用的 Tensor 操作

```python
x = torch.randn(3, 4)

# 形狀操作
x.reshape(2, 6)       # 改形狀
x.view(12)            # 同上，但要求記憶體連續
x.transpose(0, 1)     # 轉置，[3,4] → [4,3]
x.unsqueeze(0)        # 加一維，[3,4] → [1,3,4]
x.squeeze(0)          # 去掉大小為 1 的維度

# 數學操作
a = torch.randn(3, 4)
b = torch.randn(3, 4)
a + b                 # 逐元素加法
a * b                 # 逐元素乘法
a @ b.T               # 矩陣乘法，[3,4] @ [4,3] → [3,3]
a.mean()              # 所有元素的均值
a.sum(dim=0)          # 沿第 0 維加總，[3,4] → [4]
a.max(dim=1)          # 沿第 1 維取最大值

# 索引
x[0]                  # 第 0 行，shape [4]
x[:, 2]               # 第 2 列，shape [3]
x[1:3, 0:2]           # 切片，shape [2, 2]
```

## 一個常見形狀錯誤

矩陣乘法要求維度對齊：`[m, k] @ [k, n] → [m, n]`

```python
a = torch.randn(3, 4)
b = torch.randn(3, 4)

a @ b     # 錯誤！RuntimeError: size mismatch
a @ b.T   # 正確，[3,4] @ [4,3] → [3,3]
```

遇到形狀問題先 print `.shape`，是除錯最快的方式。

## Dataset 和 DataLoader

訓練時不會把所有資料一次丟進去，而是分 batch：

```python
from torch.utils.data import TensorDataset, DataLoader

# 假設有 100 筆資料，每筆 10 維
X = torch.randn(100, 10)
y = torch.randn(100, 1)

dataset = TensorDataset(X, y)
loader = DataLoader(dataset, batch_size=16, shuffle=True)

for batch_x, batch_y in loader:
    print(batch_x.shape)  # [16, 10]（最後一個 batch 可能少於 16）
    break
```

## 完整訓練迴圈

這個模板幾乎所有 PyTorch 訓練都長這樣：

```python
import torch
import torch.nn as nn
from torch.utils.data import TensorDataset, DataLoader

# 1. 準備資料（用簡單的線性關係做示範）
torch.manual_seed(42)
X = torch.randn(200, 5)
true_w = torch.randn(5, 1)
y = X @ true_w + 0.1 * torch.randn(200, 1)  # y = Xw + 噪聲

dataset = TensorDataset(X, y)
loader = DataLoader(dataset, batch_size=32, shuffle=True)

# 2. 定義模型
model = nn.Linear(5, 1)

# 3. 定義 loss 和 optimizer
criterion = nn.MSELoss()
optimizer = torch.optim.Adam(model.parameters(), lr=0.01)

# 4. 訓練迴圈
for epoch in range(50):
    total_loss = 0.0
    for batch_x, batch_y in loader:
        optimizer.zero_grad()           # 清梯度
        pred = model(batch_x)           # 前向傳播
        loss = criterion(pred, batch_y) # 計算 loss
        loss.backward()                 # 反向傳播
        optimizer.step()                # 更新參數
        total_loss += loss.item()

    if (epoch + 1) % 10 == 0:
        avg_loss = total_loss / len(loader)
        print(f"Epoch {epoch+1:3d} | loss: {avg_loss:.4f}")

# Epoch  10 | loss: 0.1523
# Epoch  20 | loss: 0.0891
# Epoch  30 | loss: 0.0612
# ...
```

## 儲存和載入模型

```python
# 儲存
torch.save(model.state_dict(), "model.pt")

# 載入
loaded_model = nn.Linear(5, 1)
loaded_model.load_state_dict(torch.load("model.pt"))
loaded_model.eval()  # 推論模式（關閉 dropout 等）

# 推論時不需要梯度，加 no_grad 節省記憶體
with torch.no_grad():
    pred = loaded_model(X[:5])
    print(pred)
```

## eval() vs train() 的差別

```python
model.train()  # 開啟 dropout、batch norm 的訓練行為
model.eval()   # 關閉，推論時固定行為
```

這兩個切換很容易忘，評估時忘了 `eval()` 會讓 dropout 隨機關掉神經元，導致每次推論結果不一樣。

## 動手練習

把上面的訓練迴圈改成分類任務：

```python
# 二元分類資料
X = torch.randn(300, 4)
y = (X[:, 0] + X[:, 1] > 0).float().unsqueeze(1)  # 簡單的線性邊界

# 模型加 Sigmoid
class BinaryClassifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(4, 8),
            nn.ReLU(),
            nn.Linear(8, 1),
            nn.Sigmoid()
        )
    def forward(self, x):
        return self.net(x)

model = BinaryClassifier()
criterion = nn.BCELoss()  # Binary Cross Entropy
optimizer = torch.optim.Adam(model.parameters(), lr=0.01)

# 訓練 100 個 epoch，觀察 loss 下降
```

## 自我檢核

- [ ] 知道如何建立 tensor、查 shape、做矩陣乘法
- [ ] 能寫出完整的 train loop（zero_grad → forward → loss → backward → step）
- [ ] 知道 `model.train()` 和 `model.eval()` 的差別
- [ ] 跑過分類任務練習，loss 有下降

→ [Ch 5 損失函數與優化器：Adam 在做什麼](./05-loss-and-optimizer.md)
