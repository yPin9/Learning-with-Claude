# Ch 11 — 組合技：Prefix Sum + Hashing

> 目標：理解前綴和與 HashMap 搭配使用的模式，能用 O(N) 解決「子陣列總和等於 k」類問題。

## 為什麼要組合？

前綴和讓你 O(1) 計算任意子陣列總和。
但若要找「有幾個子陣列總和等於 k」，暴力枚舉所有 (l, r) 是 O(N²)。

關鍵洞見：`arr[l..r] = k` 等價於 `prefix[r] - prefix[l-1] = k`，也就是 `prefix[l-1] = prefix[r] - k`。

所以問題變成：對每個 `r`，在前面掃過的前綴和中，有幾個值等於 `prefix[r] - k`？

這就是 HashMap 的用武之地。

## 模板

```
遍歷陣列，維護：
  - 當前的前綴和 prefix
  - 一個 HashMap: seen[v] = 前綴和等於 v 的次數

對每個位置 r：
  1. prefix += arr[r]
  2. 查 seen[prefix - k]，加入答案
  3. seen[prefix]++
```

初始化 `seen[0] = 1`（空前綴的貢獻）。

## 範例 1：Subarray Sum Equals K（LeetCode 560）

```cpp
int subarraySum(vector<int>& nums, int k) {
    unordered_map<int, int> seen;
    seen[0] = 1;
    int prefix = 0, count = 0;

    for (int x : nums) {
        prefix += x;
        count += seen[prefix - k];
        seen[prefix]++;
    }
    return count;
}
```

## 為什麼 `seen[0] = 1`？

考慮 `nums = [3], k = 3`。

`prefix` 走到第一個元素後等於 3。
查 `seen[3 - 3] = seen[0]`。

如果沒有 `seen[0] = 1`，這個「從頭開始的子陣列」就被漏掉了。

哨兵 `seen[0] = 1` 代表「選 0 個元素的空前綴，其前綴和為 0，出現 1 次」。

## 範例 2：Continuous Subarray Sum（LeetCode 523）

**題目**：找是否存在長度 ≥ 2 的子陣列，其總和是 k 的倍數（k > 0）。

**轉化**：`arr[l..r]` 是 k 的倍數 ⟺ `prefix[r] % k == prefix[l-1] % k`

只需要在 HashMap 裡記錄**餘數**，而不是前綴和本身。

```cpp
bool checkSubarraySum(vector<int>& nums, int k) {
    unordered_map<int, int> seen;
    seen[0] = -1;  // 空前綴在 index -1（保證長度 >= 2）
    int prefix = 0;

    for (int i = 0; i < nums.size(); i++) {
        prefix = (prefix + nums[i]) % k;
        if (seen.count(prefix)) {
            if (i - seen[prefix] >= 2) return true;
        } else {
            seen[prefix] = i;  // 只記第一次出現的位置
        }
    }
    return false;
}
```

**注意**：這裡 `seen` 存的是 index（不是次數），因為要確認長度 ≥ 2。且只記第一次出現——越早出現的餘數能創造越長的子陣列。

## 範例 3：和為 k 的最長子陣列（若有負數）

如果只問「最長」而不是「個數」，稍微改一下：

```cpp
int maxSubarrayLen(vector<int>& nums, int k) {
    unordered_map<int, int> firstSeen;
    firstSeen[0] = -1;
    int prefix = 0, maxLen = 0;

    for (int i = 0; i < nums.size(); i++) {
        prefix += nums[i];
        if (firstSeen.count(prefix - k))
            maxLen = max(maxLen, i - firstSeen[prefix - k]);
        if (!firstSeen.count(prefix))
            firstSeen[prefix] = i;  // 只記第一次，保證最長
    }
    return maxLen;
}
```

## 這個模式的通用型

| 問什麼 | seen 存什麼 | 更新條件 |
|---|---|---|
| 有幾個子陣列總和 = k | 頻率（count） | 每次都更新 |
| 最長子陣列總和 = k | 第一次出現的 index | 只記第一次 |
| 存在長度 ≥ 2 且總和是 k 倍數 | 第一次出現的 index（存餘數） | 只記第一次 |

## 自我檢核

- [ ] 能推導出「子陣列總和 = k」轉化成前綴和查詢的過程
- [ ] 知道 `seen[0] = 1` 的作用（不要死背，要能說出理由）
- [ ] 能解釋 LeetCode 523 為什麼存餘數而非前綴和本身
- [ ] 知道「求個數」和「求最長」在 seen 的更新策略上的差異

→ [Ch 12 Stack 基礎：括號、計算器類題](./12-stack-basics.md)
