# Ch 33 — 其他 Greedy 題型

> 目標：認識幾種常見的非區間 Greedy 題型，建立「哪類問題有 Greedy 解」的直覺。

## 題目 1：Gas Station（LeetCode 134）

**題目**：環形公路，n 個加油站。`gas[i]` 是 i 站能加的油，`cost[i]` 是從 i 到 i+1 需要的油。找一個起點，讓你能繞一圈，或回傳 -1。

**關鍵觀察 1**：若 `sum(gas) >= sum(cost)`，一定存在合法起點。

**關鍵觀察 2**：若從 start 出發，在某個位置 j 油量變負，那麼 [start, j] 之間的任何點都不能作為起點（從它出發會更早到 j，且到 j 時油量更少或相等）。

**Greedy 策略**：從 0 開始模擬，遇到「油量不足以繼續」就重設起點為下一個站。

```cpp
int canCompleteCircuit(vector<int>& gas, vector<int>& cost) {
    int totalTank = 0, currentTank = 0, start = 0;

    for (int i = 0; i < gas.size(); i++) {
        int gain = gas[i] - cost[i];
        totalTank += gain;
        currentTank += gain;

        if (currentTank < 0) {
            start = i + 1;      // 重設起點
            currentTank = 0;    // 重置當前油量
        }
    }
    return totalTank >= 0 ? start : -1;
}
```

## 題目 2：Jump Game II（LeetCode 45）

**題目**：保證能到達最後，求最少跳幾次。

**Greedy**：每次跳到「在當前一跳範圍內，能讓下一跳跳最遠」的位置。

等價說法：BFS 按層展開，每一「層」是一跳能到達的所有位置，層數就是跳的次數。

```cpp
int jump(vector<int>& nums) {
    int jumps = 0, curEnd = 0, farthest = 0;

    for (int i = 0; i < nums.size() - 1; i++) {
        farthest = max(farthest, i + nums[i]);  // 更新這一跳能到多遠
        if (i == curEnd) {                       // 到達這一跳的邊界
            jumps++;
            curEnd = farthest;                   // 必須再跳一次
        }
    }
    return jumps;
}
```

## 題目 3：Task Scheduler（LeetCode 621）

**題目**：CPU 執行任務，同種任務之間必須間隔至少 n 個時間單位。求完成所有任務的最少時間。

**關鍵觀察**：出現頻率最高的任務決定了最少需要多少「冷卻週期」。

若最高頻率是 `maxFreq`，且有 `maxCount` 個任務達到最高頻率：

最少時間 = `max(tasks.size(), (maxFreq - 1) * (n + 1) + maxCount)`

```cpp
int leastInterval(vector<char>& tasks, int n) {
    vector<int> freq(26, 0);
    for (char t : tasks) freq[t - 'A']++;
    int maxFreq = *max_element(freq.begin(), freq.end());
    int maxCount = count(freq.begin(), freq.end(), maxFreq);

    return max((int)tasks.size(), (maxFreq - 1) * (n + 1) + maxCount);
}
```

直覺：把最高頻率的任務排成 `maxFreq - 1` 個「框架」，每個框架裡有 n+1 個時間槽。最後一個框架剛好放 `maxCount` 個最高頻任務。其他任務填進空隙或追加在後面。

## 題目 4：Reorganize String（LeetCode 767）

**題目**：重排字串，使相鄰字元不同，若不可能回傳空字串。

**Greedy**：每次選頻率最高的字元放入（不能和上一個相同）。用 max-heap。

```cpp
string reorganizeString(string s) {
    vector<int> freq(26, 0);
    for (char c : s) freq[c - 'a']++;

    priority_queue<pair<int,char>> pq;  // {頻率, 字元}
    for (int i = 0; i < 26; i++)
        if (freq[i]) pq.push({freq[i], 'a' + i});

    string result;
    while (pq.size() >= 2) {
        auto [f1, c1] = pq.top(); pq.pop();
        auto [f2, c2] = pq.top(); pq.pop();
        result += c1; result += c2;
        if (f1 - 1 > 0) pq.push({f1-1, c1});
        if (f2 - 1 > 0) pq.push({f2-1, c2});
    }
    if (!pq.empty()) {
        if (pq.top().first > 1) return "";  // 剩下只有一種字元且頻率 > 1
        result += pq.top().second;
    }
    return result;
}
```

## Greedy 常見題型總結

| 題型 | Greedy 策略 |
|---|---|
| 最多不重疊區間 | 按結束時間排序 |
| 最少資源數 | min-heap + 開始時間排序 |
| 能否到達終點 | 維護最遠可達位置 |
| 最少跳躍次數 | BFS 分層（每跳是一層）|
| 排程 / 冷卻 | 頻率最高的任務優先 |

## 自我檢核

- [ ] 能說出 Gas Station 「重設起點」的 Greedy 邏輯
- [ ] 能說出 Jump Game II 的「BFS 按層」思維
- [ ] 能解釋 Task Scheduler 的公式 `(maxFreq-1)*(n+1)+maxCount` 的含義
- [ ] 知道 Greedy 常配合排序或 priority_queue 使用

→ [Ch 34 Simulation：辨識與實作框架](./34-simulation.md)
