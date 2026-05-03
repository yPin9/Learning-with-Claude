# 練習 F — 綜合混合題（模擬面試節奏）

> 目標：用 Ch 40 的 RUCTS 流程，在時間限制內解完三道混合題型的題目，練習「說思路 → 寫 code → 測試」的完整面試節奏。

**建議**：每題限時 20 分鐘，時間到就翻到解答，找出自己卡在哪裡。

---

## 題目一：LRU Cache（LeetCode 146）

**題目規格**

實作 LRU（Least Recently Used）快取，支援：
- `get(key)`：若存在回傳 value，否則 -1
- `put(key, value)`：插入。若超過容量，移除最近最少使用的 entry

兩個操作都要求 O(1)。

**期望輸出**

```
LRUCache(2)
put(1,1), put(2,2)
get(1) → 1
put(3,3)     // 容量滿，移除 2（最久未用）
get(2) → -1  // 2 已被移除
get(3) → 3
put(4,4)     // 移除 1
get(1) → -1
get(3) → 3
get(4) → 4
```

**思維提示**

`get` 和 `put` 都是 O(1)，需要：
- O(1) 查詢：HashMap
- O(1) 插入 / 刪除最舊元素 / 移動元素到「最新」位置：Doubly Linked List

用 `unordered_map<int, ListNode*>` 記錄每個 key 對應的節點，雙向鏈結串列維護使用順序。

C++ 可以直接用 `list<pair<int,int>>` + `unordered_map<int, list<...>::iterator>`。

---

## 題目二：Find Median from Data Stream（LeetCode 295）

**題目規格**

動態插入數字，隨時查詢當前所有數字的中位數。

- `addNum(int num)`：加入一個數字
- `findMedian()`：回傳當前中位數

**期望輸出**

```
addNum(1), addNum(2), findMedian() → 1.5
addNum(3), findMedian() → 2.0
```

**思維提示**

如何在 O(log N) 插入的同時保持中位數可以 O(1) 查詢？

用兩個 heap：
- `maxHeap`：存較小的一半（max-heap，top 是較小半的最大值）
- `minHeap`：存較大的一半（min-heap，top 是較大半的最小值）

維護兩個 heap 的大小差 ≤ 1，中位數就是：
- 大小相等：兩個 top 的平均
- maxHeap 多一個：maxHeap 的 top

---

## 題目三：Trapping Rain Water II（LeetCode 407）

**題目規格**

三維的接雨水：`heightMap[i][j]` 是每個格子的高度，計算能接多少水。

**期望輸出**

```
heightMap=[[1,4,3,1,3,2],[3,2,1,3,2,4],[2,3,3,2,3,1]] → 4
```

**思維提示**

這是二維版的接雨水，思路和一維不同：

從外圍（邊界）往內「灌水」，水從最矮的邊界洩漏。

用 min-heap（priority_queue）+ BFS：
1. 把所有邊界格子加入 min-heap，標記為已訪問
2. 每次取出最矮的邊界格子（`water level`），向四個方向擴展
3. 若鄰居比 `water level` 矮，能積水 `water level - height[nr][nc]`，且積水後的高度變為 `water level`

---

## 完整參考解答

**寫完再看！**

<details>
<summary>點開參考實作</summary>

```cpp
// 題目一：LRU Cache
class LRUCache {
    int cap;
    list<pair<int,int>> lst;  // {key, value}，front 是最新的
    unordered_map<int, list<pair<int,int>>::iterator> mp;

public:
    LRUCache(int capacity) : cap(capacity) {}

    int get(int key) {
        if (!mp.count(key)) return -1;
        lst.splice(lst.begin(), lst, mp[key]);  // 移到最前
        return mp[key]->second;
    }

    void put(int key, int value) {
        if (mp.count(key)) {
            lst.splice(lst.begin(), lst, mp[key]);
            mp[key]->second = value;
        } else {
            if (lst.size() == cap) {
                mp.erase(lst.back().first);
                lst.pop_back();
            }
            lst.push_front({key, value});
            mp[key] = lst.begin();
        }
    }
};

// 題目二：Find Median from Data Stream
class MedianFinder {
    priority_queue<int> maxH;                            // 小的一半，max-heap
    priority_queue<int, vector<int>, greater<int>> minH; // 大的一半，min-heap

public:
    void addNum(int num) {
        maxH.push(num);
        minH.push(maxH.top()); maxH.pop();  // 平衡
        if (minH.size() > maxH.size()) {
            maxH.push(minH.top()); minH.pop();
        }
    }

    double findMedian() {
        return maxH.size() > minH.size()
            ? maxH.top()
            : (maxH.top() + minH.top()) / 2.0;
    }
};

// 題目三：Trapping Rain Water II
int trapRainWater(vector<vector<int>>& heightMap) {
    int m = heightMap.size(), n = heightMap[0].size();
    if (m < 3 || n < 3) return 0;

    priority_queue<tuple<int,int,int>, vector<tuple<int,int,int>>, greater<>> pq;
    vector<vector<bool>> visited(m, vector<bool>(n, false));

    for (int i = 0; i < m; i++) for (int j : {0, n-1}) {
        pq.push({heightMap[i][j], i, j}); visited[i][j] = true; }
    for (int j = 0; j < n; j++) for (int i : {0, m-1}) {
        if (!visited[i][j]) { pq.push({heightMap[i][j], i, j}); visited[i][j] = true; } }

    int water = 0;
    vector<vector<int>> dirs = {{0,1},{0,-1},{1,0},{-1,0}};
    while (!pq.empty()) {
        auto [h, r, c] = pq.top(); pq.pop();
        for (auto& d : dirs) {
            int nr = r+d[0], nc = c+d[1];
            if (nr<0||nr>=m||nc<0||nc>=n||visited[nr][nc]) continue;
            visited[nr][nc] = true;
            water += max(0, h - heightMap[nr][nc]);
            pq.push({max(h, heightMap[nr][nc]), nr, nc});
        }
    }
    return water;
}
```

</details>

---

## 自我檢核

- [ ] LRU Cache：能說出為什麼需要「HashMap + Doubly Linked List」的組合（各自解決什麼問題）
- [ ] Median Finder：能說出兩個 heap 的不變量（大小關係）以及 addNum 的平衡步驟
- [ ] Trapping Rain Water II：能說出「從外圍往內灌水」的直覺
- [ ] 三題都嘗試在 20 分鐘內完成（即使沒寫出來，也能說出思路）

→ [Final Project：30 題衝刺計畫 + 自我評估表](./final-project-30-problems.md)
