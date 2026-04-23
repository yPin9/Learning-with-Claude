# Practice C — DP 專項

> 目標:把 Ch 19–21 的 DP 套路實戰到反射動作。DP 最需要刷題——光讀懂模板不會自動變成會寫。

## 怎麼練 DP

DP 刷題有個特殊建議:**先用 `@cache` 寫遞迴版通過,再改成 bottom-up 迭代版**。兩種都寫一次,理解 state 定義和轉移方向。

**bug 日誌要記兩類**:
1. State 定義錯(常常少一維)
2. 轉移條件錯(常常漏 case)

---

## 一維 DP 基礎

1. **Climbing Stairs (LC 70)**
2. **House Robber (LC 198)**
3. **House Robber II (LC 213)** — 環形,拆兩個線性
4. **Maximum Subarray (LC 53, Kadane)** — `dp[i] = max(arr[i], dp[i-1] + arr[i])`
5. **Maximum Product Subarray (LC 152)** — 要記 max 和 min 兩個 state(負數乘負數變正)
6. **Decode Ways (LC 91)** — 類似爬樓梯但判斷合法
7. **Word Break (LC 139)**
8. **Perfect Squares (LC 279)** — `dp[i] = min(dp[i - j*j] + 1)`
9. **Longest Increasing Subsequence (LC 300)** — O(n²) 和 O(n log n) 都寫

## 二維 DP 基礎

10. **Unique Paths (LC 62)** — 滾動優化到一維
11. **Unique Paths II (LC 63)** — 有障礙
12. **Minimum Path Sum (LC 64)**
13. **Longest Common Subsequence (LC 1143)**
14. **Edit Distance (LC 72)** — 三操作的 min
15. **Interleaving String (LC 97)**
16. **Distinct Subsequences (LC 115)** — s 中出現 t 作為子序列的方式數
17. **Longest Palindromic Substring (LC 5)** — DP 或中心擴展,兩種都寫
18. **Palindromic Substrings (LC 647)** — 數總共幾個回文子串

## 股票系列(同一套狀態機)

19. **Best Time to Buy and Sell Stock (LC 121)** — k = 1
20. **Best Time to Buy and Sell Stock II (LC 122)** — k = ∞,貪婪
21. **Best Time to Buy and Sell Stock III (LC 123)** — k = 2,4 變數寫法
22. **Best Time to Buy and Sell Stock IV (LC 188)** — k 一般
23. **Best Time to Buy and Sell Stock with Cooldown (LC 309)**
24. **Best Time to Buy and Sell Stock with Transaction Fee (LC 714)**

**全部做完後要能口述狀態機**。

## Knapsack 家族

25. **Partition Equal Subset Sum (LC 416)** — 01 背包 + 布林
26. **Target Sum (LC 494)** — 轉化成 01 背包
27. **Coin Change (LC 322)** — 最少枚數(完全背包)
28. **Coin Change II (LC 518)** — 湊法數(迴圈順序關鍵!)
29. **Ones and Zeroes (LC 474)** — 二維 01 背包
30. **Combination Sum IV (LC 377)** — 外層 target 內層 nums(允許順序,排列)

## 區間 DP

31. **Palindrome Partitioning II (LC 132)** — 最少切刀
32. **Burst Balloons (LC 312, Hard)** — 枚舉最後戳的
33. **Stone Game (LC 877 / 1140 / 1406)** — 雙人博弈 DP
34. **Remove Boxes (LC 546, Hard)** — 三維區間 DP,進階

## 樹形 DP

35. **House Robber III (LC 337)** — 二維回傳
36. **Binary Tree Cameras (LC 968, Hard)** — 三狀態
37. **Longest Univalue Path (LC 687)**
38. **Diameter of Binary Tree (LC 543)** — 已寫過,算熱身

## 狀壓 DP

39. **Partition to K Equal Sum Subsets (LC 698)** — n ≤ 16
40. **Shortest Path Visiting All Nodes (LC 847)** — BFS + bitmask
41. **Find Minimum Time to Finish All Jobs (LC 1723)** — 枚舉子集

## 其他高頻

42. **Jump Game (LC 55)** — 貪婪 / DP 都可
43. **Jump Game II (LC 45)** — BFS / 貪婪
44. **Wildcard Matching (LC 44, Hard)** — 二維 DP,`?` 和 `*` 處理
45. **Regular Expression Matching (LC 10, Hard)** — 類似,`.` 和 `*`
46. **Russian Doll Envelopes (LC 354, Hard)** — 2D LIS
47. **Maximal Square (LC 221)** — `dp[i][j] = 1 + min(三個鄰居)`
48. **Dungeon Game (LC 174, Hard)** — 反向 DP(從終點往起點推)
49. **Minimum Falling Path Sum (LC 931)** — 二維 DP 基本
50. **Number of Longest Increasing Subsequence (LC 673)** — LIS 計數

---

## 進階挑戰(選做,很 Hard)

51. **Palindrome Partitioning IV (LC 1745)** — 兩個獨立的回文切分 DP
52. **Count Different Palindromic Subsequences (LC 730, Hard)**
53. **Strange Printer (LC 664, Hard)** — 區間 DP,反直覺
54. **Minimum Insertion Steps to Make a String Palindrome (LC 1312, Hard)** — n - LCS(s, reversed(s))

---

## DP 刷完後的檢驗

應該能:

- [ ] 看題 10 秒內判斷是不是 DP
- [ ] 寫 @cache 版 5 分鐘內通過
- [ ] 寫 bottom-up 版 10 分鐘內通過
- [ ] 能口述 state 定義 + 轉移公式
- [ ] 知道空間優化方向(滾動、一維)

**DP 做到上面五點,Medium 題不會卡,Hard 題有一半也能啃**。

這五點做不到就繼續刷,不要跳。DP 是面試分水嶺。
