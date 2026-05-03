# Ch 32 — 區間 Greedy：Interval Scheduling

> 目標：掌握「按結束時間排序」的核心策略，能解各種區間貪心問題。

## 區間問題的核心排序策略

大多數區間 Greedy 問題的關鍵在於排序方式。

**按結束時間排序**：貪心地選擇最早結束的區間，讓後面有最多空間。

**按開始時間排序**：合併區間、計算最少需要幾個房間等。

## 題目 1：Non-overlapping Intervals（LeetCode 435）

**題目**：移除最少數量的區間，使剩餘的區間互不重疊。

等價問題：保留最多不重疊的區間（Interval Scheduling Maximization，ISMP）。

**Greedy 策略**：按結束時間升序排列，貪心地選每個不與已選區間重疊的區間。

**交換論證的直覺**：選結束時間早的區間，留給後續選擇的空間最大。選結束時間晚的區間，可能擋住更多後續區間。

```cpp
int eraseOverlapIntervals(vector<vector<int>>& intervals) {
    sort(intervals.begin(), intervals.end(),
         [](auto& a, auto& b) { return a[1] < b[1]; });  // 按結束時間排序

    int count = 0, end = INT_MIN;
    for (auto& iv : intervals) {
        if (iv[0] >= end) {  // 不重疊，選這個
            end = iv[1];
            count++;
        }
        // 若重疊，貪心地跳過（因為它結束時間更晚）
    }
    return intervals.size() - count;  // 移除數 = 總數 - 保留數
}
```

## 題目 2：Merge Intervals（LeetCode 56）

**題目**：合併所有重疊的區間。

按開始時間排序，依次合併相鄰重疊區間。

```cpp
vector<vector<int>> merge(vector<vector<int>>& intervals) {
    sort(intervals.begin(), intervals.end());  // 按開始時間排序
    vector<vector<int>> result;

    for (auto& iv : intervals) {
        if (!result.empty() && iv[0] <= result.back()[1]) {
            // 有重疊，合併（更新結束時間）
            result.back()[1] = max(result.back()[1], iv[1]);
        } else {
            result.push_back(iv);
        }
    }
    return result;
}
```

## 題目 3：Meeting Rooms II（LeetCode 253）

**題目**：給一組會議時間，求至少需要幾間會議室。

**Greedy 思維**：某個時刻同時進行的會議數 = 需要的會議室數。用 min-heap 追蹤每個房間的「最早結束時間」。

```cpp
int minMeetingRooms(vector<vector<int>>& intervals) {
    sort(intervals.begin(), intervals.end());  // 按開始時間排序
    priority_queue<int, vector<int>, greater<int>> minHeap;  // 存結束時間

    for (auto& iv : intervals) {
        if (!minHeap.empty() && minHeap.top() <= iv[0]) {
            minHeap.pop();  // 這個房間空出來了，重用
        }
        minHeap.push(iv[1]);  // 分配房間（或開新房間）
    }
    return minHeap.size();
}
```

heap 的大小就是需要的房間數。

## 題目 4：Jump Game（LeetCode 55）

**題目**：`nums[i]` 是從位置 i 能跳的最大步數，判斷能否到達最後一格。

**Greedy**：維護目前能到達的最遠位置 `reach`。

```cpp
bool canJump(vector<int>& nums) {
    int reach = 0;
    for (int i = 0; i < nums.size(); i++) {
        if (i > reach) return false;  // 到不了 i
        reach = max(reach, i + nums[i]);
    }
    return true;
}
```

## 區間問題的排序選擇

| 問題 | 排序依據 | 原因 |
|---|---|---|
| 最多不重疊區間 | 結束時間升序 | 留最大後續空間 |
| 合併重疊區間 | 開始時間升序 | 依序處理重疊 |
| 最少會議室 | 開始時間升序 | 按到來順序分配 |
| 最少箭刺破氣球 | 結束時間升序 | 同 Non-overlapping |

## 自我檢核

- [ ] 能說出「按結束時間排序」的直覺（為什麼留最大後續空間）
- [ ] 能從頭寫出 Non-overlapping Intervals
- [ ] 能說出 Merge Intervals 的排序依據
- [ ] 能用 min-heap 解 Meeting Rooms II

→ [Ch 33 其他 Greedy 題型](./33-greedy-misc.md)
