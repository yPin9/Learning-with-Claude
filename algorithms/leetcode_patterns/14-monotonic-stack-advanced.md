# Ch 14 — Monotonic Stack 進階：矩形面積、接雨水

> 目標：用單調 Stack 解兩道高頻困難題，理解「以當前元素為瓶頸」的思維框架。

## 兩道題的共同思維

**Largest Rectangle in Histogram** 和 **Trapping Rain Water** 都用同一個框架：

> 對每個位置，找它「向左向右能延伸多遠」，然後計算貢獻。

找「向左向右延伸的邊界」= 找左側和右側第一個比它小（或大）的元素 = Monotonic Stack。

## 題目 1：Largest Rectangle in Histogram（LeetCode 84）

**題目**：長方形圖中，每個柱子的高度是 `heights[i]`，找面積最大的矩形。

**關鍵觀察**：最大矩形的高度一定等於某個柱子的高度。

對每個柱子 `i`，計算：「以 `heights[i]` 為高度，能向左右延伸多寬？」

寬度 = 左側第一個比它矮的柱子的 index + 1，到右側第一個比它矮的柱子的 index - 1。

```
heights = [2, 1, 5, 6, 2, 3]

對 i=2 (height=5)：
  左側第一個 < 5 的是 i=1 (height=1)，左邊界 = 2
  右側第一個 < 5 的是 i=4 (height=2)，右邊界 = 3
  寬度 = 3 - 2 + 1 = 2，面積 = 5 × 2 = 10
```

用 Monotonic Stack（遞增）同時計算每個柱子的左右邊界：

```cpp
int largestRectangleArea(vector<int>& h) {
    int n = h.size();
    stack<int> st;
    int maxArea = 0;

    for (int i = 0; i <= n; i++) {
        int curH = (i == n) ? 0 : h[i];  // 結尾加一個高度 0 清空 stack

        while (!st.empty() && curH < h[st.top()]) {
            int height = h[st.top()]; st.pop();
            int width = st.empty() ? i : i - st.top() - 1;
            maxArea = max(maxArea, height * width);
        }
        st.push(i);
    }
    return maxArea;
}
```

**關鍵細節**：
- `width = st.empty() ? i : i - st.top() - 1`
  - Stack 空 → 沒有左邊界，寬度從 0 到 i-1，等於 i
  - Stack 非空 → 左邊界是 `st.top()`，寬度是 `i - st.top() - 1`
- 結尾加高度 0，強制 pop 完所有剩餘柱子

## 題目 2：Trapping Rain Water（LeetCode 42）

**題目**：高度陣列 `height`，計算能接住多少雨水。

**方法一：前後綴最大值（最直覺）**

每個位置能接的水 = `min(左側最大高度, 右側最大高度) - height[i]`（若為負則為 0）。

```cpp
int trap(vector<int>& height) {
    int n = height.size();
    vector<int> leftMax(n), rightMax(n);

    leftMax[0] = height[0];
    for (int i = 1; i < n; i++)
        leftMax[i] = max(leftMax[i-1], height[i]);

    rightMax[n-1] = height[n-1];
    for (int i = n-2; i >= 0; i--)
        rightMax[i] = max(rightMax[i+1], height[i]);

    int water = 0;
    for (int i = 0; i < n; i++)
        water += min(leftMax[i], rightMax[i]) - height[i];
    return water;
}
```

時間 O(N)，空間 O(N)。

**方法二：Two Pointers（空間 O(1)）**

```cpp
int trap(vector<int>& height) {
    int l = 0, r = height.size() - 1;
    int leftMax = 0, rightMax = 0, water = 0;

    while (l < r) {
        if (height[l] < height[r]) {
            if (height[l] >= leftMax) leftMax = height[l];
            else water += leftMax - height[l];
            l++;
        } else {
            if (height[r] >= rightMax) rightMax = height[r];
            else water += rightMax - height[r];
            r--;
        }
    }
    return water;
}
```

原理：哪邊的牆比較矮，哪邊的水量就由那邊決定。

面試中兩種都要會，通常先說方法一（直覺），再說方法二（最佳化）。

## 比較兩題

| | Largest Rectangle | Trapping Rain Water |
|---|---|---|
| 找的是 | 向左右找更矮的（遞增 stack） | 向左右找更高的（前後綴最大） |
| 瓶頸元素 | 最矮的柱子決定高度 | 最矮的邊界決定水位 |
| 最優解 | O(N) stack | O(N) two pointers |

## 自我檢核

- [ ] 能解釋 Largest Rectangle 中 `width = st.empty() ? i : i - st.top() - 1` 的含義
- [ ] 能用前後綴最大值法手寫接雨水
- [ ] 能用 Two Pointers 法手寫接雨水（不看筆記）
- [ ] 能說出兩題共同的「以當前元素為瓶頸」框架

→ [練習 C：Tree 綜合](./practice-c-tree.md)（Part 4 結束，先做練習再繼續）

→ [Ch 15 Tree 結構 + 前 / 中 / 後序遍歷](./15-tree-traversal.md)
