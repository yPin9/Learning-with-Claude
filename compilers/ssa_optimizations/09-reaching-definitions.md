# Ch 9 — 到達定義（Reaching Definitions）

> 目標：推導到達定義的資料流方程，理解它在 SSA 中退化成語法查找，以及它在 non-SSA 優化中的角色。

## 定義

**定義 `d` 到達程式點 `p`（reaches p）**：存在一條從 `d` 到 `p` 的執行路徑，且路徑上沒有對同一個變數的其他定義（kill）。

用途：
- 常數傳播：如果到達某使用點的所有定義都是常數 c，就可以替換
- 複製傳播：如果到達某使用點的定義是 `x = y`，可以把 x 替換成 y
- Dead code detection：如果某定義沒有到達任何使用點，就是死碼

## 資料流方程

到達定義是**前向 May-analysis**：

- **前向**：信息沿控制流方向傳播（定義往後走）
- **May**：某條路徑上到達即可（取 ∪）

每個基本塊 B 的 Gen 和 Kill：

```
Gen_B：B 中產生的定義（B 內部的賦值），且在 B 內部沒有被 kill
Kill_B：對同一個變數，B 中的定義 kill 了前面的所有定義
```

方程：

```
Out[B] = Gen_B ∪ (In[B] \ Kill_B)
In[B]  = ∪_{P ∈ pred(B)} Out[P]
初始：In[entry] = {}
```

## 手動例子

```
B1: d1: a = 1
    d2: b = 2
    → B2, B3

B2: d3: a = 3   // kill d1
    → B4

B3: d4: c = a   // use a（不 kill a）
    → B4

B4: d5: b = a + b  // use a, b; kill d2
```

計算 Gen/Kill：

```
B1: Gen = {d1, d2}, Kill = {}
B2: Gen = {d3}, Kill = {d1}   （d3 kill 了 d1，因為都是對 a 的定義）
B3: Gen = {d4}, Kill = {}
B4: Gen = {d5}, Kill = {d2}
```

迭代計算（第一輪）：

```
In[B1] = {}
Out[B1] = Gen_B1 ∪ (In[B1] \ Kill_B1) = {d1, d2}

In[B2] = Out[B1] = {d1, d2}
Out[B2] = {d3} ∪ ({d1, d2} \ {d1}) = {d3, d2}

In[B3] = Out[B1] = {d1, d2}
Out[B3] = {d4} ∪ ({d1, d2} \ {}) = {d1, d2, d4}

In[B4] = Out[B2] ∪ Out[B3] = {d3, d2} ∪ {d1, d2, d4} = {d1, d2, d3, d4}
Out[B4] = {d5} ∪ ({d1,d2,d3,d4} \ {d2}) = {d1, d3, d4, d5}
```

`d1` 和 `d3` 同時到達 B4（都是對 a 的定義）——這意味著在 B4 使用 a 時，a 的值不是常數，無法做常數傳播。

## SSA 中的退化

在 SSA 中，每個變數只有一個定義，所以到達定義分析**退化成了語法查找**：

- 每個 `%x` 只有一個定義點，就是對應的指令
- 使用 `%x` 的地方，其到達定義永遠是且只是那一個定義
- 不需要任何資料流分析

這是 SSA 帶來的根本簡化之一。原本 O(n) 的到達定義分析在 SSA 中是 O(1)。

**隱含的代價**：φ-function 的語意要求「合法的 reaching definition」——在匯合點，到達定義的集合正好是 φ-function 的引數集合。SSA 構造（Ch 5）保證了這個不變式。

## Use-Def 和 Def-Use 鏈

到達定義分析產生兩種鏈：

```
Use-Def 鏈（UD chains）：
  對每個使用點 u，記錄所有到達 u 的定義
  用於：找「這個值從哪來」

Def-Use 鏈（DU chains）：
  對每個定義點 d，記錄所有被 d 到達的使用點
  用於：找「這個值被誰用」
```

在 SSA 中：

```
UD chain = 唯一。每個使用 %x 的地方，UD chain 就是 %x 的定義指令。
DU chain = 指令的 users 列表（LLVM API：Value::users()）
```

```cpp
// LLVM C++ API：遍歷某個值的所有使用者
for (User *U : myValue->users()) {
    if (Instruction *I = dyn_cast<Instruction>(U)) {
        errs() << "Used in: " << *I << "\n";
    }
}

// 找到某個指令的定義（UD chain）
if (Instruction *Def = dyn_cast<Instruction>(someUse->getOperand(0))) {
    errs() << "Defined by: " << *Def << "\n";
}
```

## 在 non-SSA 優化中的角色

雖然 SSA 內建了到達定義，但有幾個場合仍需要類似的分析：

**機器代碼層**：暫存器分配後的代碼不是 SSA，需要真正的到達定義分析來做 peephole 優化。

**記憶體操作**：SSA 對純量變數有效，但指針 dereference（load/store）不在 SSA 框架內。`MemorySSA`（Ch 12）把 SSA 的思想延伸到記憶體操作。

**別名分析的輸入**：判斷兩個指針是否可能指向同一位置，需要知道指針的定義和傳播路徑。

## 自我檢核

- [ ] 到達定義的定義：存在路徑且未被 kill
- [ ] 前向 May-analysis：`Out = Gen ∪ (In \ Kill)`
- [ ] SSA 中到達定義退化成語法查找（唯一定義）
- [ ] LLVM API：`Value::users()` 是 DU chain，`getOperand()` 是 UD chain

→ [Ch 10 稀疏條件常數傳播（SCCP）](./10-sccp.md)
