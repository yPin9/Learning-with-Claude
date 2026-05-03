# Final Project — 30 題衝刺計畫 + 自我評估表

> 目標：在完成課程後，用這 30 道精選題驗證你的掌握程度，並建立「上了面試場」的實戰信心。

## 使用說明

1. 每道題**限時 25 分鐘**，計時開始
2. 如果超時或解不出來，記下卡點（是定義狀態？是邊界條件？還是完全不知道用什麼方法？）
3. 看解答，重新寫一遍（不看筆記）
4. 填寫下方的自我評估表

目標完成率：25 道以上能在時限內完成 → 準備好面試了。

---

## 題目清單

### Part 1 — 線性技巧（第 1–8 題）

| # | 題目 | LeetCode # | 目標複雜度 | 核心技巧 |
|---|---|---|---|---|
| 1 | 3Sum | 15 | O(N²) | Two Pointers |
| 2 | Longest Substring Without Repeating Characters | 3 | O(N) | Sliding Window |
| 3 | Minimum Window Substring | 76 | O(N) | Sliding Window + HashMap |
| 4 | Product of Array Except Self | 238 | O(N) | Prefix Product |
| 5 | Subarray Sum Equals K | 560 | O(N) | Prefix Sum + HashMap |
| 6 | Trapping Rain Water | 42 | O(N) | Two Pointers 或 Prefix Max |
| 7 | Largest Rectangle in Histogram | 84 | O(N) | Monotonic Stack |
| 8 | Daily Temperatures | 739 | O(N) | Monotonic Stack |

### Part 2 — 二分搜尋（第 9–11 題）

| # | 題目 | LeetCode # | 目標複雜度 | 核心技巧 |
|---|---|---|---|---|
| 9 | Search a 2D Matrix | 74 | O(log MN) | Binary Search |
| 10 | Find Peak Element | 162 | O(log N) | Binary Search |
| 11 | Split Array Largest Sum | 410 | O(N log N) | Binary Search on Answer |

### Part 3 — Tree（第 12–16 題）

| # | 題目 | LeetCode # | 目標複雜度 | 核心技巧 |
|---|---|---|---|---|
| 12 | Binary Tree Maximum Path Sum | 124 | O(N) | DFS + 全域變數 |
| 13 | Serialize and Deserialize Binary Tree | 297 | O(N) | BFS 或 DFS |
| 14 | Lowest Common Ancestor | 236 | O(N) | 遞迴 LCA |
| 15 | Kth Smallest Element in a BST | 230 | O(H+K) | 中序遍歷 |
| 16 | Word Search II | 212 | O(M×N×4^L) | Trie + DFS |

### Part 4 — Graph（第 17–20 題）

| # | 題目 | LeetCode # | 目標複雜度 | 核心技巧 |
|---|---|---|---|---|
| 17 | Clone Graph | 133 | O(V+E) | DFS/BFS + HashMap |
| 18 | Pacific Atlantic Water Flow | 417 | O(MN) | 反向 BFS/DFS |
| 19 | Alien Dictionary | 269 | O(C) | Topological Sort |
| 20 | Minimum Spanning Tree | 1584 | O(E log E) | Union-Find 或 Prim |

### Part 5 — Dynamic Programming（第 21–26 題）

| # | 題目 | LeetCode # | 目標複雜度 | 核心技巧 |
|---|---|---|---|---|
| 21 | Word Break | 139 | O(N²) | 1D DP |
| 22 | Coin Change 2 | 518 | O(N×amount) | 完全背包（計數）|
| 23 | Longest Common Subsequence | 1143 | O(MN) | 2D DP |
| 24 | Edit Distance | 72 | O(MN) | 2D DP |
| 25 | Decode Ways | 91 | O(N) | 1D DP |
| 26 | Best Time to Buy and Sell Stock III | 123 | O(N) | State Machine DP |

### Part 6 — 整合（第 27–30 題）

| # | 題目 | LeetCode # | 目標複雜度 | 核心技巧 |
|---|---|---|---|---|
| 27 | Design Add and Search Words | 211 | O(M×26^N) | Trie + DFS |
| 28 | Task Scheduler | 621 | O(N) | Greedy |
| 29 | Meeting Rooms II | 253 | O(N log N) | Greedy + min-heap |
| 30 | Number of Islands | 200 | O(MN) | BFS/DFS 或 Union-Find |

---

## 自我評估表

完成每題後填寫：

| # | 完成？ | 時間（分鐘） | 卡點 | 需要複習的章節 |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| 5 | | | | |
| 6 | | | | |
| 7 | | | | |
| 8 | | | | |
| 9 | | | | |
| 10 | | | | |
| 11 | | | | |
| 12 | | | | |
| 13 | | | | |
| 14 | | | | |
| 15 | | | | |
| 16 | | | | |
| 17 | | | | |
| 18 | | | | |
| 19 | | | | |
| 20 | | | | |
| 21 | | | | |
| 22 | | | | |
| 23 | | | | |
| 24 | | | | |
| 25 | | | | |
| 26 | | | | |
| 27 | | | | |
| 28 | | | | |
| 29 | | | | |
| 30 | | | | |

---

## 完成標準與下一步

| 完成率 | 意義 | 建議 |
|---|---|---|
| ≥ 25/30 在時限內 | 面試準備充足 | 可以開始投履歷 |
| 20–24/30 | 有幾個技巧還不穩 | 針對未通過的章節補強，再做一輪 |
| < 20/30 | 某些主題還需要更多練習 | 回到對應章節，多做 LeetCode 同類題 |

## 刷題策略建議

**第一輪（掌握）**：本課程的 30 題 + 每章的 1–2 道例題，重點在理解。

**第二輪（速度）**：30 題再做一遍，目標是 15 分鐘以內解完每道 Medium。

**第三輪（面試模擬）**：在 LeetCode 開 mock interview 模式，模擬真實面試節奏。

## 高頻考題（面試前必背）

這 10 道題在 FAANG 面試中出現頻率最高，確保能盲寫：

1. Two Sum（HashMap）
2. Longest Substring Without Repeating Characters（Sliding Window）
3. Merge Intervals
4. Maximum Subarray（Kadane's Algorithm）
5. Binary Tree Level Order Traversal
6. Lowest Common Ancestor
7. Number of Islands
8. Course Schedule（Topological Sort）
9. Word Break（DP）
10. LRU Cache（HashMap + Linked List）

---

恭喜完成這套課程。演算法不是天賦，是肌肉記憶。繼續練，直到看到題目的瞬間大腦就開始連結到解法為止。
