# 練習 A — Two Pointers + Sliding Window 綜合

> 目標：把 Ch 3–5 學到的技巧拼起來，不看筆記獨立解出三道題，並能說出每題的收縮條件或指標移動邏輯。

**寫完再看！不要偷看**，否則學不到東西。

---

## 題目一：Container With Most Water（LeetCode 11）

**題目規格**

給 `n` 個高度 `height[i]`，代表垂直線段。找兩條線使它們和 x 軸圍成的容積最大。

容積 = `min(height[i], height[j]) × (j - i)`

**期望輸出**

```
height = [1,8,6,2,5,4,8,3,7] → 49
height = [1,1] → 1
```

**實作步驟**

**Step 1**：想想為什麼這題能用 Two Pointers（相向）。

提示：如果 `height[l] < height[r]`，`l` 左移還是 `r` 右移？往哪個方向移動才可能找到更大的容積？

**Step 2**：確定移動規則後，寫出主迴圈。

**Step 3**：處理 edge case：`height` 長度為 2 的情況。

**Step 4**：驗證複雜度：時間 O(N)，空間 O(1)。

---

## 題目二：Minimum Size Subarray Sum（LeetCode 209）

**題目規格**

給正整數陣列 `nums` 和整數 `target`，找長度最小的連續子陣列使其總和 ≥ target。不存在則回傳 0。

**期望輸出**

```
target=7, nums=[2,3,1,2,4,3] → 2（子陣列 [4,3]）
target=4, nums=[1,4,4] → 1
target=11, nums=[1,1,1,1,1,1,1,1] → 0
```

**實作步驟**

**Step 1**：這是可變窗口。收縮條件是什麼？

**Step 2**：更新答案的位置在 while 裡面還是外面？（最短 → 在裡面更新）

**Step 3**：初始化 `minLen = INT_MAX`，最後要記得處理「沒有找到」的情況。

---

## 題目三：Longest Substring with At Most Two Distinct Characters（LeetCode 159）

**題目規格**

找最長的子字串，其中最多有兩種不同的字元。

**期望輸出**

```
s = "eceba" → 3（子字串 "ece"）
s = "ccaabbb" → 5（子字串 "aabbb"）
```

**實作步驟**

**Step 1**：這是可變窗口 + HashMap。HashMap 記錄什麼？

**Step 2**：收縮條件是 `freq.size() > 2`（窗口內不同字元超過 2 個）。

**Step 3**：收縮時，頻率降到 0 的字元要從 HashMap 中移除（否則 `freq.size()` 不準確）。

**Step 4**：在收縮結束後更新 `maxLen`。

---

## 完整參考解答

**寫完再看！不要偷看**

<details>
<summary>點開參考實作</summary>

```cpp
// 題目一：Container With Most Water
int maxArea(vector<int>& height) {
    int l = 0, r = height.size() - 1;
    int maxVol = 0;
    while (l < r) {
        maxVol = max(maxVol, min(height[l], height[r]) * (r - l));
        if (height[l] < height[r]) l++;  // 矮的那邊移動（高的那邊留著）
        else r--;
    }
    return maxVol;
}
// 為什麼移動矮的那邊？因為移動高的那邊只會讓寬度減小且高度不變或更低，一定不會更好。

// 題目二：Minimum Size Subarray Sum
int minSubarrayLen(int target, vector<int>& nums) {
    int n = nums.size(), left = 0, sum = 0;
    int minLen = INT_MAX;
    for (int right = 0; right < n; right++) {
        sum += nums[right];
        while (sum >= target) {
            minLen = min(minLen, right - left + 1);
            sum -= nums[left++];
        }
    }
    return minLen == INT_MAX ? 0 : minLen;
}

// 題目三：Longest Substring with At Most Two Distinct
int lengthOfLongestSubstringTwoDistinct(string s) {
    unordered_map<char, int> freq;
    int left = 0, maxLen = 0;
    for (int right = 0; right < s.size(); right++) {
        freq[s[right]]++;
        while (freq.size() > 2) {
            freq[s[left]]--;
            if (freq[s[left]] == 0) freq.erase(s[left]);
            left++;
        }
        maxLen = max(maxLen, right - left + 1);
    }
    return maxLen;
}
```

</details>

---

## 測試用例

```cpp
// 題目一
assert(maxArea({1,8,6,2,5,4,8,3,7}) == 49);
assert(maxArea({1,1}) == 1);

// 題目二
assert(minSubarrayLen(7, {2,3,1,2,4,3}) == 2);
assert(minSubarrayLen(11, {1,1,1,1,1,1,1,1}) == 0);

// 題目三
assert(lengthOfLongestSubstringTwoDistinct("eceba") == 3);
assert(lengthOfLongestSubstringTwoDistinct("ccaabbb") == 5);
```

## 自我檢核

- [ ] Container With Most Water：能說出「移動矮的那邊」的理由（交換論證）
- [ ] Minimum Size Subarray Sum：`minLen` 的更新在 while 裡面，知道為什麼
- [ ] 兩種收縮條件（≥ target vs freq.size() > 2）都能獨立寫出
- [ ] 三題都能說出時間和空間複雜度

→ [Ch 6 Binary Search 基礎：邊界陷阱完全解析](./06-binary-search-basics.md)
