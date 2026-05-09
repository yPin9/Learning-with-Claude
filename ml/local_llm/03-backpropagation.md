# Ch 3 — 反向傳播：梯度怎麼流

> 目標：理解梯度是什麼、為什麼反向傳播能自動算出來、PyTorch autograd 如何替你做這件事。

## 訓練的本質是「往哪裡走一步」

訓練神經網路，就是反覆問這個問題：**參數往哪個方向調整，能讓輸出更接近正確答案？**

答案是梯度（gradient）。梯度告訴你，當某個參數微調一點點時，loss 會往哪個方向變。訓練就是沿著梯度的**反方向**調整參數（gradient descent）。

```
新參數 = 舊參數 - 學習率 × 梯度
w ← w - lr × ∂L/∂w
```

問題是：在一個有幾億個參數的網路裡，怎麼高效算出每個參數的梯度？答案是反向傳播（backpropagation）。

## 從最簡單的例子開始

一個函數 `f(x) = x²`，梯度是 `df/dx = 2x`。

當 x = 3，梯度是 6。意思是：x 往正方向移一點點，f(x) 會增加約 6 倍的量。要讓 f(x) 減小，就把 x 往負方向移。

網路裡的梯度同理，只是有幾億個 x，而且透過連鎖規則串在一起。

## 連鎖規則（Chain Rule）

假設有兩個函數串起來：`L = g(f(x))`

連鎖規則說：`dL/dx = dL/dg · dg/dx`

這就是反向傳播的核心——loss 對第一層參數的梯度，等於 loss 對第二層輸入的梯度，再乘以第二層輸入對第一層輸出的梯度。

```
前向：  x → [層 1] → a → [層 2] → L
反向：  ∂L/∂x ← [層 1 的梯度] ← ∂L/∂a ← [層 2 的梯度] ← 1
```

梯度從 loss 往回傳，每一層把收到的梯度乘以自己的局部導數，再往前傳。這就是「反向」傳播的由來。

## PyTorch autograd：不用手算

```python
import torch

# 建一個需要梯度的 tensor
x = torch.tensor(3.0, requires_grad=True)

# 做一些計算
y = x ** 2        # y = x²
z = 2 * y + 1    # z = 2x² + 1

# 觸發反向傳播
z.backward()

# 查梯度
print(x.grad)    # tensor(12.)
# dz/dx = 4x = 4×3 = 12 ✓
```

PyTorch 在前向傳播時偷偷記錄了計算圖（computational graph），backward() 一呼叫就沿著這張圖把梯度算回來。

## 更完整的例子：一個線性層的梯度

```python
import torch
import torch.nn as nn

# 一個線性層
model = nn.Linear(3, 1)  # 輸入 3 維，輸出 1 維

# 輸入資料和真實標籤
x = torch.randn(4, 3)    # 4 筆資料，每筆 3 維
y_true = torch.randn(4, 1)

# 前向傳播
y_pred = model(x)

# 計算 loss（MSE：均方誤差）
loss = ((y_pred - y_true) ** 2).mean()
print(f"loss: {loss.item():.4f}")

# 反向傳播：算出所有參數的梯度
loss.backward()

# 查看梯度
print(f"weight.grad shape: {model.weight.grad.shape}")  # [1, 3]
print(f"bias.grad shape:   {model.bias.grad.shape}")    # [1]
print(f"weight.grad: {model.weight.grad}")
```

## 梯度累積的陷阱

**PyTorch 的梯度是累積的，不會自動清零。**

```python
for epoch in range(3):
    y_pred = model(x)
    loss = ((y_pred - y_true) ** 2).mean()
    loss.backward()
    # 錯誤！沒有清零，每次梯度都加在上一次的基礎上

print(model.weight.grad)  # 梯度是三輪的總和，錯的
```

正確做法是在每次 backward 之前清零：

```python
optimizer = torch.optim.SGD(model.parameters(), lr=0.01)

for epoch in range(3):
    optimizer.zero_grad()          # ← 每輪先清零
    y_pred = model(x)
    loss = ((y_pred - y_true) ** 2).mean()
    loss.backward()
    optimizer.step()               # 用梯度更新參數
    print(f"epoch {epoch}, loss: {loss.item():.4f}")
```

## 為什麼不需要手寫反向傳播

幾乎所有人學反向傳播時都手算過一次，理解原理後就不用再碰了。PyTorch 的 autograd 對任意可微分計算圖都能自動算梯度，包括 Transformer 裡幾十層的複雜結構。

你只需要：
1. 把計算寫成前向傳播
2. 算 loss
3. 呼叫 `.backward()`
4. 呼叫 `optimizer.step()`

剩下的 autograd 搞定。

## 動手練習

手動驗證 PyTorch 的梯度計算正確：

```python
import torch

w = torch.tensor(2.0, requires_grad=True)
b = torch.tensor(1.0, requires_grad=True)
x = torch.tensor(3.0)

# y = wx + b
y = w * x + b
# dy/dw = x = 3
# dy/db = 1

y.backward()
print(w.grad)  # 應該是 3.0
print(b.grad)  # 應該是 1.0
```

再試更複雜的：`z = (wx + b)²`，手算 `dz/dw`，和 PyTorch 的結果比對。

## 自我檢核

- [ ] 能解釋梯度下降的更新公式 `w ← w - lr × ∂L/∂w`
- [ ] 能用連鎖規則手算兩層網路的梯度
- [ ] 知道為什麼每次訓練前要 `zero_grad()`
- [ ] 跑過上面的程式碼，確認 autograd 結果和手算一致

→ [Ch 4 PyTorch 入門：Tensor / autograd / training loop](./04-pytorch-basics.md)
