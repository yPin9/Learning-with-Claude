# Ch 7 — lower_bound / upper_bound 變形

> 目標：掌握「找第一個 ≥ target」和「找最後一個 ≤ target」的寫法，這兩個是面試最常考的二分搜尋變形。

## 為什麼需要邊界查找？

精確查找很簡單。但很多題目問的不是「target 在哪裡」，而是：

- 「target 第一次出現的位置」（陣列有重複元素）
- 「第一個 ≥ target 的元素在哪裡」
- 「target 最後出現的位置」

這類題統稱邊界查找，對應 C++ STL 的 `lower_bound` 和 `upper_bound`。

## lower_bound：第一個 ≥ target 的位置

數學定義：找最小的 `i` 使得 `arr[i] >= target`。

```
arr = [1, 2, 2, 4, 5]
         0  1  2  3  4

lower_bound(2) = 1  (第一個 >= 2 的位置)
lower_bound(3) = 3  (第一個 >= 3 的位置，arr[3]=4)
lower_bound(6) = 5  (沒有 >= 6 的元素，回傳 n)
```

**思路**：搜索空間分成兩半：
- 左半：`arr[mid] < target`（一定不是答案，`l = mid + 1`）
- 右半：`arr[mid] >= target`（可能是答案，但也許更左有更好的，`r = mid`）

```cpp
int lowerBound(vector<int>& arr, int target) {
    int l = 0, r = arr.size();  // 注意 r = n，不是 n-1
                                 // 因為答案可能是 n（target 比所有元素大）
    while (l < r) {              // 注意是 l < r，不是 l <= r
        int mid = l + (r - l) / 2;
        if (arr[mid] < target) l = mid + 1;
        else r = mid;            // arr[mid] >= target，r 縮到 mid（不排除 mid）
    }
    return l;                    // 最終 l == r，就是答案
}
```

**為什麼這裡用半開區間 `[l, r)` 而不是閉區間？**

因為答案可能是 `n`（超出陣列範圍），閉區間無法表示 `r = n`。半開區間 `[l, r)` 允許 `r = n`，且迴圈條件 `l < r` 不會越界。

## upper_bound：第一個 > target 的位置

數學定義：找最小的 `i` 使得 `arr[i] > target`。

```
arr = [1, 2, 2, 4, 5]

upper_bound(2) = 3  (第一個 > 2 的位置，arr[3]=4)
upper_bound(3) = 3  (第一個 > 3 的位置，arr[3]=4)
upper_bound(5) = 5  (沒有 > 5 的元素，回傳 n)
```

只改一個字：

```cpp
int upperBound(vector<int>& arr, int target) {
    int l = 0, r = arr.size();
    while (l < r) {
        int mid = l + (r - l) / 2;
        if (arr[mid] <= target) l = mid + 1;  // ← 這裡是 <=
        else r = mid;
    }
    return l;
}
```

## 從 lower_bound / upper_bound 推導其他查找

| 想找 | 做法 |
|---|---|
| target 第一次出現的位置 | `lowerBound(target)`，然後確認 `arr[l] == target` |
| target 最後一次出現的位置 | `upperBound(target) - 1`，然後確認 `arr[l] == target` |
| target 出現的次數 | `upperBound(target) - lowerBound(target)` |
| 第一個 > target 的位置 | `upperBound(target)` |
| 最後一個 < target 的位置 | `lowerBound(target) - 1` |

## C++ STL 的版本

C++ 已內建，直接用：

```cpp
#include <algorithm>

vector<int> arr = {1, 2, 2, 4, 5};

// 回傳 iterator，用 - arr.begin() 轉成 index
auto it1 = lower_bound(arr.begin(), arr.end(), 2);
int idx1 = it1 - arr.begin();  // = 1

auto it2 = upper_bound(arr.begin(), arr.end(), 2);
int idx2 = it2 - arr.begin();  // = 3

// target 的出現次數
int count = upper_bound(..., 2) - lower_bound(..., 2);  // = 2
```

面試時如果題目允許，直接用 STL 就好，不用自己寫。

## 範例：LeetCode 34 — 在排序陣列中找元素的第一個和最後一個位置

```cpp
vector<int> searchRange(vector<int>& nums, int target) {
    int first = lower_bound(nums.begin(), nums.end(), target) - nums.begin();

    // 確認 first 位置確實是 target
    if (first == nums.size() || nums[first] != target)
        return {-1, -1};

    int last = upper_bound(nums.begin(), nums.end(), target) - nums.begin() - 1;
    return {first, last};
}
```

## 兩套模板的對比

| | 閉區間 `[l, r]` | 半開區間 `[l, r)` |
|---|---|---|
| 初始值 | `r = n - 1` | `r = n` |
| 迴圈條件 | `l <= r` | `l < r` |
| 找不到時 | `return -1` | `return l`（可能是 n） |
| 適合場景 | 精確查找 | 邊界查找 |

**建議**：精確查找用閉區間（Ch 6），邊界查找用半開區間（本章）。兩套各記一個，不要混用。

## 自我檢核

- [ ] 能寫出 lower_bound 的實作（包含 `r = n` 和 `l < r` 的原因）
- [ ] 知道 lower_bound 和 upper_bound 的一字之差（`<` vs `<=`）
- [ ] 能用 lower_bound + upper_bound 算出 target 的出現次數
- [ ] 能使用 C++ STL 的 `lower_bound` / `upper_bound`

→ [Ch 8 在答案空間二分（Binary Search on Answer）](./08-binary-search-on-answer.md)
