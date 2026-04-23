# Practice B — 樹與圖

> 目標:把 Ch 7–14 的工具(tree recursion、BST、heap、DSU、BFS/DFS、拓撲、最短路、MST)熟練到看題秒出思路。

## 怎麼練

跟 Practice A 一樣:自己寫、造 case、口述思路、記 bug。

樹與圖題容易「想得到但寫不對」——pointer 手抖、visited 漏、base case 忘。**寫對比想對難**,多練手感。

---

## 題目清單

### Binary Tree 基本

1. **Maximum Depth of Binary Tree (LC 104)**
   - 暖身。`1 + max(l, r)`。

2. **Same Tree (LC 100)** / **Symmetric Tree (LC 101)**
   - 雙遞迴。

3. **Invert Binary Tree (LC 226)**
   - 左右子樹 swap + 遞迴。三行。

4. **Diameter of Binary Tree (LC 543)**
   - 回傳高度,答案在過程更新。

5. **Maximum Path Sum (LC 124, Hard)**
   - 跟 Diameter 同形狀,但邊可負。

6. **Lowest Common Ancestor (LC 236)**
   - 多義回傳值:「子樹中找到的 p 或 q 或 LCA」。

### BFS on Tree

7. **Binary Tree Level Order Traversal (LC 102)**
   - `for _ in range(len(q))` 分層。

8. **Zigzag Level Order (LC 103)**
   - 交替 insert 方向,或每兩層反轉。

9. **Binary Tree Right Side View (LC 199)**
   - BFS,每層取最後一個;或 DFS 優先右子,記第一個到達某深度的。

### BST

10. **Validate BST (LC 98)**
    - 帶 (lo, hi) 範圍遞迴,或 inorder 檢查單調。

11. **Kth Smallest in BST (LC 230)**
    - Inorder 到第 k 個停。Iterative 版更穩。

12. **Lowest Common Ancestor of BST (LC 235)**
    - 二分下降。

13. **Recover BST (LC 99, Hard)**
    - Inorder 找逆序對。兩個錯置節點。

14. **Convert Sorted Array to BST (LC 108)**
    - 取中間當 root,遞迴。

### 序列化 / 建樹

15. **Serialize and Deserialize Binary Tree (LC 297, Hard)**
    - Preorder + null 標記。

16. **Construct Binary Tree from Preorder and Inorder (LC 105)**
    - Hash inorder 找位置。

### Trie

17. **Implement Trie (LC 208)**
    - 裸實作。

18. **Design Add and Search Words Data Structure (LC 211)**
    - `.` 萬用字元 → DFS + backtrack。

19. **Word Search II (LC 212, Hard)**
    - Trie + DFS。用 trie 剪枝是關鍵。

### Heap

20. **Kth Largest Element in an Array (LC 215)**
    - 三種解法:sort / min-heap size k / quickselect。寫 quickselect。

21. **Top K Frequent Elements (LC 347)**
    - 已經在 Practice A,這裡用 heap 版。

22. **Merge k Sorted Lists (LC 23, Hard)**
    - 已在 Practice A。

23. **Find Median from Data Stream (LC 295, Hard)**
    - 兩個 heap。

24. **K Closest Points to Origin (LC 973)**
    - Max-heap 維持 k 個,或 quickselect。

### Union-Find

25. **Number of Connected Components in an Undirected Graph (LC 323)**
    - 標準 DSU。

26. **Redundant Connection (LC 684)**
    - 找造成環的邊。

27. **Accounts Merge (LC 721)**
    - 經典 DSU 應用。

28. **Number of Islands (LC 200)**
    - DFS 或 DSU。兩種都寫。

29. **Graph Valid Tree (LC 261)**
    - DSU 檢測環 + 連通性。

### BFS / DFS on Graph / Grid

30. **Clone Graph (LC 133)**
    - Hash mapping + DFS。

31. **Number of Islands (LC 200)**
    - 列兩次,這次寫 DFS 版。

32. **Rotting Oranges (LC 994)**
    - Multi-source BFS。

33. **Pacific Atlantic Water Flow (LC 417)**
    - 反向 BFS,從海邊出發。

34. **Word Ladder (LC 127, Hard)**
    - BFS + pattern map 剪枝。

35. **Surrounded Regions (LC 130)**
    - 反向思考:從邊緣的 O 開始 DFS。

36. **01 Matrix (LC 542)**
    - Multi-source BFS(所有 0 當起點)。

### 拓撲排序

37. **Course Schedule (LC 207)**
    - Kahn BFS。

38. **Course Schedule II (LC 210)**
    - 輸出順序。

39. **Alien Dictionary (LC 269, Hard)**
    - 建圖 + 拓撲。兩種失敗 case。

40. **Minimum Height Trees (LC 310)**
    - 反向拓撲:從 leaves 一層層剝,最後剩下的 1-2 個就是中心。

### 最短路

41. **Network Delay Time (LC 743)**
    - Dijkstra 教科書題。

42. **Cheapest Flights Within K Stops (LC 787)**
    - 帶狀態的 Dijkstra 或 Bellman-Ford 變形。

43. **Path with Minimum Effort (LC 1631)**
    - Dijkstra 變形:dist 是「路徑最大邊」。

44. **Shortest Path in Binary Matrix (LC 1091)**
    - 8 方向 BFS。

### MST

45. **Min Cost to Connect All Points (LC 1584)**
    - Kruskal 標準題。

46. **Connecting Cities with Minimum Cost (LC 1135)**
    - 同上,含「不連通」判斷。

---

## 進階挑戰

47. **Reconstruct Itinerary (LC 332, Hard)**
    - Eulerian path + 字典序最小。Hierholzer 演算法。

48. **Swim in Rising Water (LC 778, Hard)**
    - 二分答案 + BFS,或 Dijkstra 變形。

49. **Bus Routes (LC 815, Hard)**
    - BFS,但節點是「路線」不是「站」。

50. **Longest Increasing Path in a Matrix (LC 329, Hard)**
    - DFS + memo(本質是拓撲 DP)。

---

## 做完後的自我檢驗

這系列做完應該能:

- [ ] 閉眼寫 BFS / DFS 模板
- [ ] 閉眼寫 DSU(含 path compression + union by rank)
- [ ] 閉眼寫 Dijkstra
- [ ] 知道看到「依賴 / 先後」就是拓撲、「連通 / 合併」就是 DSU、「最短 / 最少步」就是 BFS 或 Dijkstra
- [ ] 知道 2D grid 的方向向量怎麼寫

做到這些再進 Part 3 DP。
