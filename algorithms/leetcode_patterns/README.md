# 演算法與資料結構：LeetCode 面試刷題指南

> 給「看得懂解法、但寫不出穩定程式碼」的工程師，用 C++ 建立可複製的解題框架。

從遞迴直覺開始打底，到 DP、Graph、Greedy 全覆蓋。每個主題先建思維模型，再用真實 LeetCode 題目帶你一步一步實作，最後才給完整解法。

## 為什麼學這個？

- **面試導向**：每章選題來自高頻 LeetCode，直接對應 FAANG 面試考點
- **從暴力推最佳**：不背模板，理解每一行 code 為什麼這樣寫
- **C++ 實戰**：STL 容器、指標操作、複雜度分析，全部實際跑過

## 課程地圖

### Part 0 — 地基
- [Ch 0 環境設置：C++ for LeetCode](./00-environment-setup.md)
- [Ch 1 遞迴直覺：Call Stack 視覺化](./01-recursion-intuition.md)
- [Ch 2 遞迴 → 迭代：理解什麼時候該轉換](./02-recursion-to-iteration.md)

### Part 1 — 雙指標與滑動窗口
- [Ch 3 Two Pointers：相向 vs 同向](./03-two-pointers.md)
- [Ch 4 Sliding Window（固定窗口）](./04-sliding-window-fixed.md)
- [Ch 5 Sliding Window（可變窗口）](./05-sliding-window-variable.md)
- [練習 A：Two Pointers + Sliding Window 綜合](./practice-a-two-pointers-sliding-window.md)

### Part 2 — 二分搜尋
- [Ch 6 Binary Search 基礎：邊界陷阱完全解析](./06-binary-search-basics.md)
- [Ch 7 lower_bound / upper_bound 變形](./07-binary-search-variants.md)
- [Ch 8 在答案空間二分（Binary Search on Answer）](./08-binary-search-on-answer.md)
- [練習 B：Binary Search 題型辨識](./practice-b-binary-search.md)

### Part 3 — 前綴和與雜湊
- [Ch 9 Prefix Sum：一維與二維](./09-prefix-sum.md)
- [Ch 10 HashMap / HashSet：計數與配對](./10-hashing.md)
- [Ch 11 組合技：Prefix Sum + Hashing](./11-prefix-sum-hashing-combo.md)

### Part 4 — Stack 與單調 Stack
- [Ch 12 Stack 基礎：括號、計算器類題](./12-stack-basics.md)
- [Ch 13 Monotonic Stack：核心思維](./13-monotonic-stack.md)
- [Ch 14 Monotonic Stack 進階：矩形面積、接雨水](./14-monotonic-stack-advanced.md)

### Part 5 — Tree
- [Ch 15 Tree 結構 + 前 / 中 / 後序遍歷](./15-tree-traversal.md)
- [Ch 16 BFS on Tree：層序遍歷](./16-tree-bfs.md)
- [Ch 17 Recursion on Tree：分治思維](./17-tree-recursion.md)
- [Ch 18 路徑問題 + LCA](./18-tree-path-lca.md)
- [練習 C：Tree 綜合](./practice-c-tree.md)

### Part 6 — Graph
- [Ch 19 Graph 表示法 + BFS](./19-graph-bfs.md)
- [Ch 20 DFS：連通分量 + Cycle Detection](./20-graph-dfs.md)
- [Ch 21 Topological Sort（Kahn + DFS 兩種）](./21-topological-sort.md)
- [Ch 22 Union-Find（並查集）：路徑壓縮 + 按秩合併](./22-union-find.md)
- [Ch 23 最短路徑：Dijkstra](./23-dijkstra.md)
- [練習 D：Graph 綜合](./practice-d-graph.md)

### Part 7 — Dynamic Programming
- [Ch 24 DP 核心思維：記憶化遞迴 → 遞推](./24-dp-fundamentals.md)
- [Ch 25 一維 DP：爬樓梯、House Robber](./25-dp-1d.md)
- [Ch 26 二維 DP：網格路徑](./26-dp-2d.md)
- [Ch 27 0/1 背包](./27-dp-knapsack.md)
- [Ch 28 子序列 DP：LIS / LCS](./28-dp-subsequence.md)
- [Ch 29 區間 DP](./29-dp-interval.md)
- [Ch 30 DP 空間優化（滾動陣列）](./30-dp-space-optimization.md)
- [練習 E：DP 綜合](./practice-e-dp.md)

### Part 8 — Greedy
- [Ch 31 Greedy 思維：何時能用？反例在哪裡？](./31-greedy-thinking.md)
- [Ch 32 區間 Greedy：Interval Scheduling](./32-greedy-intervals.md)
- [Ch 33 其他 Greedy 題型](./33-greedy-misc.md)

### Part 9 — Simulation 與 State Machine
- [Ch 34 Simulation：辨識與實作框架](./34-simulation.md)
- [Ch 35 State Machine：字串解析、遊戲邏輯](./35-state-machine.md)

### Part 10 — 數學與位元技巧
- [Ch 36 位元操作（Bit Manipulation）](./36-bit-manipulation.md)
- [Ch 37 數學技巧：GCD、快速冪、質數篩](./37-math-tricks.md)
- [Ch 38 排列組合：計數類題入門](./38-combinatorics.md)

### Part 11 — 整合與面試策略
- [Ch 39 題型辨識總表：看到什麼 → 用什麼](./39-pattern-recognition.md)
- [Ch 40 面試拆題流程：從讀題到寫 code](./40-interview-approach.md)
- [練習 F：綜合混合題（模擬面試節奏）](./practice-f-mixed.md)
- [Final Project：30 題衝刺計畫 + 自我評估表](./final-project-30-problems.md)

## 學習方式建議

1. **先想再看**：每章都有引導問題，強迫自己先思考再看解析，不然學不到東西
2. **練習不偷看**：練習題的參考解答折疊在 `<details>` 裡，寫完再打開
3. **複雜度必看**：每題都標時間/空間複雜度，面試被問時要能馬上說出來

## 參考資料

- 《算法導論 (CLRS)》— Cormen et al.（理論基礎，遇到不懂的概念可查）
- LeetCode 官網 — 本課所有例題均來自此，可直接提交驗證
