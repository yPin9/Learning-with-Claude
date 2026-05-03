# Ch 1 — 遞迴直覺：Call Stack 視覺化

> 目標：建立「函式呼叫自己」的心智模型，能在腦中追蹤 call stack 的變化，不再對遞迴感到神秘。

## 遞迴是什麼？一個比喻

你去查字典，發現「遞迴」的解釋是「見遞迴」。

這就是遞迴：**定義本身包含自己**。

在程式裡，遞迴函式會呼叫自己，但每次呼叫都在處理「更小一點的問題」，直到問題小到可以直接回答為止。

## 兩個必要元素

任何遞迴函式都有且只有兩個部分：

```
遞迴函式 = 基本情況 (base case) + 遞迴情況 (recursive case)
```

**Base case**：問題小到可以直接回答，**不再呼叫自己**。
**Recursive case**：把問題縮小，然後呼叫自己。

缺少 base case → 無限遞迴 → stack overflow（後面會示範）。

## 範例：計算 n!

數學定義：
```
factorial(0) = 1           ← base case
factorial(n) = n × factorial(n-1)   ← recursive case
```

C++ 實作：

```cpp
int factorial(int n) {
    if (n == 0) return 1;          // base case
    return n * factorial(n - 1);   // recursive case
}
```

很直覺。但這段 code 在執行時，記憶體裡到底發生了什麼？

## Call Stack 視覺化

執行 `factorial(4)` 時，call stack 長這樣：

```
呼叫順序（往下堆）：
┌─────────────────────────┐
│ factorial(4)            │  → 等待 factorial(3) 回傳
├─────────────────────────┤
│ factorial(3)            │  → 等待 factorial(2) 回傳
├─────────────────────────┤
│ factorial(2)            │  → 等待 factorial(1) 回傳
├─────────────────────────┤
│ factorial(1)            │  → 等待 factorial(0) 回傳
├─────────────────────────┤
│ factorial(0)            │  → 直接回傳 1（base case）
└─────────────────────────┘

回傳順序（往上拆）：
factorial(0) = 1
factorial(1) = 1 × 1 = 1
factorial(2) = 2 × 1 = 2
factorial(3) = 3 × 2 = 6
factorial(4) = 4 × 6 = 24
```

關鍵理解：**遞迴不是循環**。每一層都是獨立的函式呼叫，有自己的變數 `n`。
`factorial(3)` 裡的 `n` 是 3，`factorial(2)` 裡的 `n` 是 2，互不影響。

## 另一個範例：費波那契數列

```cpp
int fib(int n) {
    if (n <= 1) return n;           // base case: fib(0)=0, fib(1)=1
    return fib(n - 1) + fib(n - 2); // recursive case
}
```

`fib(5)` 的呼叫樹（注意這次是「樹」不是「線」）：

```
                    fib(5)
                  /        \
            fib(4)          fib(3)
           /      \        /      \
        fib(3)  fib(2)  fib(2)  fib(1)
        /    \
     fib(2) fib(1)
```

這裡有個大問題：`fib(3)` 被算了**兩次**，`fib(2)` 被算了**三次**。
時間複雜度是 **O(2ⁿ)**，非常慢。

解法是記憶化（memoization）——算過的結果存起來，下次直接查，DP 章節會詳細說。

## 常見錯誤：缺少 base case

```cpp
int factorial(int n) {
    return n * factorial(n - 1);  // 沒有 base case！
}
```

執行 `factorial(4)` 會一直往下呼叫：
`factorial(4) → factorial(3) → ... → factorial(-1) → factorial(-2) → ...`

直到 stack 空間耗盡，程式 crash：

```
Segmentation fault (core dumped)
```

這是遞迴最常見的 bug。每次寫遞迴，**第一行就寫 base case**。

## 常見錯誤：recursive case 沒有縮小問題

```cpp
int factorial(int n) {
    if (n == 0) return 1;
    return n * factorial(n);  // 寫成 n 而不是 n-1！
}
```

`factorial(n)` 永遠呼叫 `factorial(n)`，問題從未縮小，同樣無限遞迴。

## 如何思考遞迴（重要心法）

寫遞迴時，**不要試圖追蹤每一層在做什麼**。你會頭暈。

正確的思考方式：

1. **定義函式的職責**：這個函式接收什麼、回傳什麼？
2. **假設它對更小的問題是正確的**（信任它）
3. **只思考當前這一層要做什麼**

以 `factorial(n)` 為例：
- 函式職責：回傳 `n!`
- 假設 `factorial(n-1)` 已經正確回傳 `(n-1)!`
- 當前這層只需要：`return n * factorial(n-1)`

這種思考方式叫**數學歸納法**，是理解所有遞迴的關鍵。

## 動手練習

試著用遞迴寫「計算陣列所有元素的總和」：

```cpp
// arr: 陣列，n: 目前處理到第幾個元素（從後往前）
int sumArray(vector<int>& arr, int n) {
    // 你的 base case 是什麼？
    // recursive case 是什麼？
}
```

提示：`sumArray(arr, n)` = 第 n 個元素 + `sumArray(arr, n-1)`

## 自我檢核

- [ ] 能解釋 base case 和 recursive case 分別是什麼
- [ ] 能用手追蹤 `factorial(3)` 的 call stack（畫出來）
- [ ] 知道缺少 base case 會造成什麼後果
- [ ] 能用「信任遞迴」的方式思考，而不是逐層追蹤

遞迴有了直覺，下一步看什麼時候該把遞迴轉成迭代。

→ [Ch 2 遞迴 → 迭代：理解什麼時候該轉換](./02-recursion-to-iteration.md)
