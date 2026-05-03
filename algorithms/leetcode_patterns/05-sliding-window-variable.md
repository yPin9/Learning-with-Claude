# Ch 5 — Sliding Window（可變窗口）

> 目標：掌握可變窗口的「擴展 / 收縮」邏輯，能對任意條件寫出正確的 while 迴圈控制。

## 可變窗口的核心問題

固定窗口的大小是已知的。可變窗口要回答一個問題：

> **窗口什麼時候該縮小？**

這是這類題的全部難點。確定了收縮條件，模板就定了。

## 通用模板

```cpp
int left = 0;
// 視情況維護窗口的狀態（sum、頻率表、計數器等）

for (int right = 0; right < n; right++) {
    // 1. 把 arr[right] 加入窗口
    // ...

    // 2. 窗口不合法時，縮小（右移 left）
    while (/* 窗口違反條件 */) {
        // 移除 arr[left]
        left++;
    }

    // 3. 此時窗口合法，更新答案
    // ans = max/min(ans, right - left + 1)
}
```

**注意**：`while` 還是 `if`？
- 若每次右移 left 一格就能恢復合法 → 用 `if`
- 若需要縮小多次才合法 → 用 `while`（幾乎都用 while，比較安全）

## 範例 1：最短子陣列，總和 ≥ S

**題目**：正整數陣列，找最短的連續子陣列使總和 ≥ S。

**收縮條件**：`windowSum >= S`（已經滿足條件，可以試著縮小）

```
arr = [2, 1, 5, 2, 3, 2], S = 7

right=0: window=[2], sum=2 < 7
right=1: window=[2,1], sum=3 < 7
right=2: window=[2,1,5], sum=8 >= 7 → 記錄長度3, 縮小
         移除arr[0]=2, left=1, sum=6 < 7, 停止縮小
right=3: window=[1,5,2], sum=8 >= 7 → 記錄長度3, 縮小
         移除arr[1]=1, left=2, sum=7 >= 7 → 記錄長度2, 縮小
         移除arr[2]=5, left=3, sum=2 < 7, 停止縮小
right=4: window=[2,3], sum=5 < 7
right=5: window=[2,3,2], sum=7 >= 7 → 記錄長度3, 縮小
         移除arr[3]=2, left=4, sum=5 < 7, 停止縮小

最短長度 = 2
```

```cpp
int minSubarrayLen(int target, vector<int>& nums) {
    int n = nums.size();
    int left = 0, windowSum = 0;
    int minLen = INT_MAX;

    for (int right = 0; right < n; right++) {
        windowSum += nums[right];

        while (windowSum >= target) {
            minLen = min(minLen, right - left + 1);
            windowSum -= nums[left++];
        }
    }

    return minLen == INT_MAX ? 0 : minLen;
}
```

時間：O(N)（每個元素最多進出窗口各一次），空間：O(1)。

## 範例 2：最長無重複字元子字串

**題目（LeetCode 3）**：找最長的子字串，其中不含重複字元。

**收縮條件**：`s[right]` 已在窗口內出現過

**狀態維護**：用 `unordered_map<char, int>` 記錄每個字元最後出現的位置。

```cpp
int lengthOfLongestSubstring(string s) {
    unordered_map<char, int> lastSeen;
    int left = 0, maxLen = 0;

    for (int right = 0; right < s.size(); right++) {
        // 如果 s[right] 在窗口內出現過
        if (lastSeen.count(s[right]) && lastSeen[s[right]] >= left) {
            left = lastSeen[s[right]] + 1;  // 左指標跳到重複位置的下一個
        }
        lastSeen[s[right]] = right;
        maxLen = max(maxLen, right - left + 1);
    }

    return maxLen;
}
```

這題的 `left` 不是逐步右移，而是**直接跳**到重複字元的下一個位置。效果相同，但更快。

注意 `lastSeen[s[right]] >= left` 的判斷：如果重複字元上次出現在窗口外（left 左邊），就不需要縮小。

## 可變窗口的兩種子類型

**求最長**（Max）：
```
窗口只要合法就盡量擴展，不合法時縮小
→ while (違反條件) { 縮小; }
→ 更新 maxLen
```

**求最短**（Min）：
```
窗口達到目標就記錄並嘗試縮小，直到不再滿足為止
→ while (滿足條件) { 更新 minLen; 縮小; }
```

方向相反，但模板結構相同。

## 題型辨識：何時用可變窗口？

| 題目說的 | 對應操作 |
|---|---|
| 最長子字串，不含 X | 擴展；遇到 X 就縮小（求最長） |
| 最短子陣列，總和 ≥ S | 擴展；滿足條件就縮小（求最短） |
| 子陣列中最多 K 個不同元素 | 擴展；不同元素超過 K 就縮小 |

核心問題永遠是：**什麼條件觸發收縮？**

## 常見錯誤

- 用 `if` 而不是 `while` 來收縮：如果一次縮小不夠，答案就錯了
- 更新答案的位置放錯：「最長」在 while 外更新，「最短」在 while 內更新
- 忘記檢查 `minLen == INT_MAX`（沒有合法窗口時的特殊情況）

## 自我檢核

- [ ] 能背出可變窗口的通用模板框架
- [ ] 知道「求最長」和「求最短」的更新位置差異
- [ ] 能從頭寫出 LeetCode 3（最長無重複字元子字串）
- [ ] 能說出「什麼觸發收縮」是每道題的核心問題

→ [練習 A：Two Pointers + Sliding Window 綜合](./practice-a-two-pointers-sliding-window.md)
