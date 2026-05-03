# Ch 8 — 在答案空間二分（Binary Search on Answer）

> 目標：識別「對答案二分」的題型，掌握「假設答案是 X，驗證可不可行」的思維框架。

## 這類題的樣子

前兩章的二分搜尋，搜索的是**陣列的 index**。

這章的二分搜尋，搜索的是**答案本身的值域**。

題目特徵：
- 問「最小化最大值」或「最大化最小值」
- 問「最少需要幾個/最多能用幾個」
- 暴力枚舉答案的範圍太大，但**驗證某個答案是否可行只需要 O(N)**

## 核心思維：二分 + 驗證

把問題拆成兩步：

1. **定義答案的搜索範圍**：`[lo, hi]`
2. **設計 `feasible(mid)` 函式**：假設答案是 `mid`，判斷是否可行

若 `feasible` 具有**單調性**（可行的範圍是連續的），就能二分。

```
可行性：F F F F T T T T
                ↑
             找這個（第一個 T）
```

## 範例 1：Koko 吃香蕉（LeetCode 875）

**題目**：有 `n` 堆香蕉，每堆有 `piles[i]` 根。Koko 有 `h` 小時，每小時吃 `k` 根（同一堆）。問最小的 `k` 是多少，能讓她在 `h` 小時內吃完？

**答案範圍**：
- 最小可能的 k = 1（最慢）
- 最大需要的 k = max(piles)（一小時吃完最大的一堆）

**驗證函式**：給定 k，總共需要幾小時？

```cpp
// 以速度 k 吃完所有香蕉需要幾小時
long long hoursNeeded(vector<int>& piles, int k) {
    long long hours = 0;
    for (int p : piles)
        hours += (p + k - 1) / k;  // ceil(p / k)
    return hours;
}
```

**二分**：在 `[1, max(piles)]` 上找最小的 k，使得 `hoursNeeded(k) <= h`。

```cpp
int minEatingSpeed(vector<int>& piles, int h) {
    int l = 1, r = *max_element(piles.begin(), piles.end());

    while (l < r) {
        int mid = l + (r - l) / 2;
        if (hoursNeeded(piles, mid) <= h)
            r = mid;      // mid 可行，答案可能更小，縮小右邊
        else
            l = mid + 1;  // mid 不可行，答案要更大，縮小左邊
    }

    return l;
}
```

這是 lower_bound 的形式：找第一個「可行」的答案。

## 範例 2：在 D 天內運完貨物（LeetCode 1011）

**題目**：`weights[i]` 是第 i 個貨物的重量，船每天最多載 `capacity` 重量，按順序裝貨。問最小的 `capacity` 是多少，能在 `D` 天內運完？

**答案範圍**：
- 最小 = `max(weights)`（否則有貨物裝不上船）
- 最大 = `sum(weights)`（一天全部運完）

**驗證函式**：載重 cap 的船需要幾天？

```cpp
int daysNeeded(vector<int>& weights, int cap) {
    int days = 1, current = 0;
    for (int w : weights) {
        if (current + w > cap) {
            days++;
            current = 0;
        }
        current += w;
    }
    return days;
}
```

```cpp
int shipWithinDays(vector<int>& weights, int days) {
    int l = *max_element(weights.begin(), weights.end());
    int r = accumulate(weights.begin(), weights.end(), 0);

    while (l < r) {
        int mid = l + (r - l) / 2;
        if (daysNeeded(weights, mid) <= days)
            r = mid;
        else
            l = mid + 1;
    }

    return l;
}
```

## 題型辨識清單

看到這些句子就往「答案空間二分」想：

- 「最小化最大值」
- 「最大化最小值」
- 「至少需要 X 個」/ 「最多能做到 X」
- 答案是一個整數，範圍有上下界

然後問自己：**假設答案是 mid，我能 O(N) 或 O(N log N) 驗證嗎？** 能 → 就用這個技巧。

## 模板（最小化問題）

```cpp
int lo = 最小可能答案, hi = 最大可能答案;

while (lo < hi) {
    int mid = lo + (hi - lo) / 2;
    if (feasible(mid))
        hi = mid;        // 可行，嘗試更小
    else
        lo = mid + 1;    // 不可行，要更大
}

return lo;  // 第一個可行的答案
```

## 自我檢核

- [ ] 能識別「答案空間二分」的題目特徵
- [ ] 知道如何定義 `[lo, hi]` 的範圍
- [ ] 能為 Koko 吃香蕉寫出驗證函式
- [ ] 知道「最小化問題」的 lo/hi 移動方向

→ [練習 B：Binary Search 題型辨識](./practice-b-binary-search.md)
