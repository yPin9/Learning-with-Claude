# Ch 13 — Monotonic Stack：核心思維

> 目標：理解單調 Stack 的維護邏輯，能對「下一個更大 / 更小元素」類問題快速套用模板。

## 問題：Next Greater Element

**題目（LeetCode 496）**：對每個元素，找它右側第一個比它大的元素。

暴力：O(N²)。Monotonic Stack：O(N)。

## 核心思維

維護一個「**遞減**的 Stack」。

規則：當新元素要入 Stack 時，把所有**比它小的元素**先 pop 出去。

為什麼這樣就能找到「下一個更大的元素」？

因為：當 `arr[i]` 被 pop 出去時，pop 它的那個 `arr[j]`（j > i）就是 `arr[i]` 右側第一個比它大的元素。

```
arr = [2, 1, 5, 3, 6]

i=0: push 2.   stack: [2]
i=1: push 1.   stack: [2, 1]   (1 < 2, 不 pop)
i=2: arr[2]=5 > stack.top()=1 → pop 1, 1 的 NGE = 5
         5 > stack.top()=2 → pop 2, 2 的 NGE = 5
         push 5.  stack: [5]
i=3: push 3.   stack: [5, 3]   (3 < 5, 不 pop)
i=4: arr[4]=6 > stack.top()=3 → pop 3, 3 的 NGE = 6
         6 > stack.top()=5 → pop 5, 5 的 NGE = 6
         push 6.  stack: [6]

結束後 stack 剩 [6]，6 沒有 NGE → -1
```

```cpp
vector<int> nextGreaterElement(vector<int>& nums) {
    int n = nums.size();
    vector<int> ans(n, -1);
    stack<int> st;  // 存 index（單調遞減）

    for (int i = 0; i < n; i++) {
        while (!st.empty() && nums[i] > nums[st.top()]) {
            ans[st.top()] = nums[i];
            st.pop();
        }
        st.push(i);
    }
    return ans;
}
```

Stack 裡的元素始終保持**遞減**（從 bottom 到 top）。

## 四種變形

| 找什麼 | Stack 維護 | pop 條件 |
|---|---|---|
| 右側第一個更大 | 遞減 Stack | `nums[i] > top` |
| 右側第一個更小 | 遞增 Stack | `nums[i] < top` |
| 左側第一個更大 | 遞減 Stack | 從右往左遍歷 |
| 左側第一個更小 | 遞增 Stack | 從右往左遍歷 |

**口訣**：找更大 → 遞減 Stack；找更小 → 遞增 Stack。

## 循環陣列的處理

**題目（LeetCode 503）**：Next Greater Element II，陣列是循環的。

技巧：遍歷兩遍（或用 `i % n` 模擬）：

```cpp
vector<int> nextGreaterElements(vector<int>& nums) {
    int n = nums.size();
    vector<int> ans(n, -1);
    stack<int> st;

    for (int i = 0; i < 2 * n; i++) {  // 遍歷兩遍
        while (!st.empty() && nums[i % n] > nums[st.top()])
            ans[st.top()] = nums[i % n], st.pop();
        if (i < n) st.push(i);          // 只在第一遍 push
    }
    return ans;
}
```

## 應用：Daily Temperatures（再看一遍）

Ch 12 已經寫過，現在你知道它的本質是 **Monotonic Stack（找右側更大元素）**：

```cpp
vector<int> dailyTemperatures(vector<int>& t) {
    stack<int> st;  // 遞減 stack（存 index）
    vector<int> ans(t.size(), 0);

    for (int i = 0; i < t.size(); i++) {
        while (!st.empty() && t[i] > t[st.top()])
            ans[st.top()] = i - st.top(), st.pop();
        st.push(i);
    }
    return ans;
}
```

## 複雜度分析

看起來有個 while 迴圈，感覺是 O(N²)？

不是。每個元素最多 push 一次、pop 一次。總操作數 = 2N = O(N)。

這是**均攤分析（amortized analysis）**：單次操作可能慢，但平均下來是 O(1)。

## 自我檢核

- [ ] 能說出「遞減 Stack」維護的不變量是什麼
- [ ] 能手追蹤 `[2, 1, 5, 3, 6]` 的 stack 狀態
- [ ] 知道找「更大」vs「更小」對應哪種 stack
- [ ] 能解釋為什麼整體複雜度是 O(N) 而不是 O(N²)

→ [Ch 14 Monotonic Stack 進階：矩形面積、接雨水](./14-monotonic-stack-advanced.md)
