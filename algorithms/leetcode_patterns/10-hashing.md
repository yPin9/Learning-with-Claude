# Ch 10 — HashMap / HashSet：計數與配對

> 目標：掌握 HashMap 和 HashSet 的三大使用場景：計數、配對、去重，能判斷什麼時候用哪種。

## C++ 的雜湊容器

```cpp
unordered_map<int, int>  mp;   // key-value，查/插 O(1) 平均
unordered_set<int>       st;   // 只存 key，查/插 O(1) 平均
map<int, int>            ord;  // 有序，查/插 O(log N)
set<int>                 ords; // 有序集合，查/插 O(log N)
```

面試預設用 `unordered_map` / `unordered_set`（更快）。只有在需要有序查詢（如 lower_bound）時才用 `map` / `set`。

常用操作：

```cpp
mp[key]++;           // 計數加一（key 不存在時自動初始化為 0）
mp.count(key)        // 存在回傳 1，不存在回傳 0
mp.find(key)         // 回傳 iterator，找不到回傳 mp.end()
st.insert(x);
st.count(x);         // 1 或 0
```

## 場景 1：計數

統計每個元素出現幾次。

**範例：找出陣列中出現超過一半的元素（LeetCode 169，Boyer-Moore 投票法更優，但 HashMap 最直覺）**

```cpp
int majorityElement(vector<int>& nums) {
    unordered_map<int, int> cnt;
    for (int x : nums) cnt[x]++;
    for (auto& [val, freq] : cnt)
        if (freq > nums.size() / 2) return val;
    return -1;
}
```

## 場景 2：配對（Two Sum 類）

**題目（LeetCode 1）**：找兩個數使其和等於 target，回傳 index。

關鍵觀察：對每個 `nums[i]`，找 `target - nums[i]` 在不在前面出現過。

```cpp
vector<int> twoSum(vector<int>& nums, int target) {
    unordered_map<int, int> seen;  // value → index

    for (int i = 0; i < nums.size(); i++) {
        int complement = target - nums[i];
        if (seen.count(complement))
            return {seen[complement], i};
        seen[nums[i]] = i;
    }
    return {};
}
```

**一遍掃描**：在加入 `nums[i]` 之前先查詢，確保不會用到自己。

## 場景 3：去重 / 快速查詢存在性

**範例：Longest Consecutive Sequence（LeetCode 128）**

找最長的連續數字序列（元素可以不在陣列中連續）。

暴力是 O(N²)。用 HashSet 可以 O(N)：

```cpp
int longestConsecutive(vector<int>& nums) {
    unordered_set<int> s(nums.begin(), nums.end());
    int best = 0;

    for (int x : s) {
        // 只從序列的「起點」開始算
        if (!s.count(x - 1)) {
            int cur = x, len = 1;
            while (s.count(cur + 1)) { cur++; len++; }
            best = max(best, len);
        }
    }
    return best;
}
```

**關鍵**：只從 `x-1` 不在集合的元素開始計算，避免重複計算同一個序列。

## 場景 4：滑動窗口 + 計數

**範例：Longest Substring with At Most K Distinct Characters**

可變窗口 + HashMap 記錄窗口內字元頻率：

```cpp
int lengthOfLongestSubstringKDistinct(string s, int k) {
    unordered_map<char, int> freq;
    int left = 0, maxLen = 0;

    for (int right = 0; right < s.size(); right++) {
        freq[s[right]]++;

        while (freq.size() > k) {
            freq[s[left]]--;
            if (freq[s[left]] == 0) freq.erase(s[left]);
            left++;
        }

        maxLen = max(maxLen, right - left + 1);
    }
    return maxLen;
}
```

`freq.size()` = 目前窗口內不同字元的數量。這是 HashMap 和滑動窗口的經典組合。

## unordered_map 的坑

**Hash collision 導致退化**：極少數情況下，惡意測試資料可以讓 `unordered_map` 退化到 O(N) per operation。競程中有人用自訂 hash 繞過，面試通常不需要擔心。

**`mp[key]` 會插入 key**：如果只想查詢，用 `mp.count(key)` 或 `mp.find(key)`，不要用 `mp[key]`（它會插入預設值）。

## 何時 HashMap，何時 HashSet？

- 需要記錄額外資訊（index、頻率）→ `unordered_map`
- 只需要知道某個值存不存在 → `unordered_set`

## 自我檢核

- [ ] 能從頭寫出 Two Sum 的 HashMap 解法
- [ ] 知道 `mp[key]` 和 `mp.count(key)` 的差異
- [ ] 能解釋 Longest Consecutive Sequence 為何從「起點」開始
- [ ] 能寫出「滑動窗口 + 計數 HashMap」的框架

→ [Ch 11 組合技：Prefix Sum + Hashing](./11-prefix-sum-hashing-combo.md)
