# Ch 4 — Sliding Window（固定窗口）

> 目標：建立固定大小滑動窗口的完整實作模板，理解「滑動 = 加新元素 + 移除舊元素」的核心操作。

## 這類問題的樣子

題目特徵：
- 連續子陣列（subarray）或子字串（substring）
- 固定長度 K
- 求最大值 / 最小值 / 某個聚合結果

暴力解是 O(N×K)，滑動窗口是 O(N)。

## 核心思維

窗口向右滑動一格：

```
移除左端元素 arr[i-1]
加入右端元素 arr[i+k-1]
```

不需要重新計算整個窗口的聚合值。

```
arr = [2, 1, 5, 1, 3, 2], k = 3

window [0..2]: sum = 2+1+5 = 8
window [1..3]: sum = 8 - arr[0] + arr[3] = 8 - 2 + 1 = 7
window [2..4]: sum = 7 - arr[1] + arr[4] = 7 - 1 + 3 = 9  ← max
window [3..5]: sum = 9 - arr[2] + arr[5] = 9 - 5 + 2 = 6
```

## 固定窗口模板

```cpp
// 最大子陣列和，長度固定為 k
int maxSumSubarray(vector<int>& arr, int k) {
    int n = arr.size();

    // Step 1: 建立第一個窗口
    int windowSum = 0;
    for (int i = 0; i < k; i++)
        windowSum += arr[i];

    int maxSum = windowSum;

    // Step 2: 滑動窗口（窗口起點從 1 到 n-k）
    for (int i = 1; i <= n - k; i++) {
        windowSum += arr[i + k - 1];  // 加入右端新元素
        windowSum -= arr[i - 1];      // 移除左端舊元素
        maxSum = max(maxSum, windowSum);
    }

    return maxSum;
}
```

這就是你在課程開始時推導出的解法，C++ 版本。

## 進一步：窗口內的最大值（非總和）

如果不是求「總和」而是求「窗口內的最大值」，每次滑動都重新掃一遍是 O(NK)，太慢。

這時要用 **Monotonic Deque**（單調雙端佇列）。先記著這個需求，Ch 13~14 會詳細說。

現在先看一個更有趣的固定窗口題：

## 範例：字串中的所有字母異位詞

**題目（LeetCode 438）**：給字串 `s` 和 `p`，找出 `s` 中所有 `p` 的字母異位詞（anagram）的起始 index。

字母異位詞 = 組成字母相同、順序不同。例如 `"cba"` 和 `"abc"` 是異位詞。

**觀察**：`p` 的長度固定，所以窗口大小固定。
**判斷方式**：窗口內每個字母的頻率 == `p` 的字母頻率。

```cpp
vector<int> findAnagrams(string s, string p) {
    vector<int> result;
    if (s.size() < p.size()) return result;

    int k = p.size();
    vector<int> pCount(26, 0), winCount(26, 0);

    // 建立 p 的頻率表 + 第一個窗口的頻率表
    for (int i = 0; i < k; i++) {
        pCount[p[i] - 'a']++;
        winCount[s[i] - 'a']++;
    }

    if (pCount == winCount) result.push_back(0);

    // 滑動窗口
    for (int i = 1; i <= (int)s.size() - k; i++) {
        winCount[s[i + k - 1] - 'a']++;  // 加入右端
        winCount[s[i - 1] - 'a']--;       // 移除左端
        if (pCount == winCount) result.push_back(i);
    }

    return result;
}
```

直接比較兩個 `vector<int>` 是 O(26) = O(1)，整體 O(N)。

## 固定窗口 vs 可變窗口

| | 固定窗口 | 可變窗口 |
|---|---|---|
| 窗口大小 | 固定 k | 動態變化 |
| 典型操作 | 滑動時加一個減一個 | 擴展（右移 r）或收縮（右移 l） |
| 典型題型 | 固定長度子陣列問題 | 最短/最長滿足條件的子陣列 |

可變窗口下一章。

## 常見錯誤

- 迴圈終點寫 `i < n - k` 而不是 `i <= n - k`（少算最後一個窗口）
- 第一個窗口忘記單獨建立，直接進滑動迴圈
- 陣列越界：`arr[i + k - 1]` 當 `i = n - k` 時，index 是 `n - 1`，沒問題；但如果 `i` 跑到 `n - k + 1` 就越界了

## 自我檢核

- [ ] 能從空白寫出固定窗口的模板（不看筆記）
- [ ] 知道為什麼迴圈終點是 `n - k + 1`（含）
- [ ] 理解字母異位詞題的頻率表做法
- [ ] 知道「窗口內最大值」問題需要額外資料結構

→ [Ch 5 Sliding Window（可變窗口）](./05-sliding-window-variable.md)
