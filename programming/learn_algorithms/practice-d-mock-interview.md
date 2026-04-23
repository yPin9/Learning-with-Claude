# Practice D — Mock Interview

> 目標:把前面所有章節訓練成「45 分鐘內解 1–2 題並通過溝通」的實戰能力。沒做過 mock 的人,真面試第一次會慌。

## 為什麼 Mock 重要

刷題是閉卷考試,mock 是口試。兩個能力不同:

- **刷題能力**:算法會,code 對。
- **Mock 能力**:算法會 + code 對 + **能一邊做一邊講清楚**。

**同一題**,自己寫 10 分鐘搞定,mock 45 分鐘都講不完——這是常態。原因:平常練習沒逼自己講話。

---

## Mock 的三種做法

### 1. 自己對鏡子講(最低門檻,推薦天天做)

- 找一題 Medium
- 定時 30 分鐘
- **整個過程口述**:從 clarify 到 test,說出每一步
- 錄音,回放聽「自己聽懂嗎」

多數人頭三次會發現:自己想得通但講不通。這是 Mock 的核心價值。

### 2. Pramp / Interviewing.io 配對

- 免費平台,配其他候選人互相面試
- 30-45 分鐘,一人當面試官一人解題
- 能聽到真正「外人」對你講解的反饋

缺點:對方程度不保證,偶爾遇到混的。但便宜。

### 3. 真人面試教練(付費)

- ex-FAANG 工程師當教練
- 一次 $100-$300
- 值得在 onsite 前一週做 2-3 次

---

## 45 分鐘標準流程

每次 mock 嚴格照這個節奏:

| 時間 | 動作 |
|---|---|
| 0:00 - 0:02 | 自我介紹(很短),面試官讀題 |
| 0:02 - 0:05 | Clarify(問 input 範圍、edge case、輸出格式) |
| 0:05 - 0:08 | Example + brute force |
| 0:08 - 0:12 | Optimize 討論 |
| 0:12 - 0:35 | Code |
| 0:35 - 0:42 | 自己 test,找 edge case |
| 0:42 - 0:45 | 複雜度分析 + 問題 |

**嚴格計時**。不能 45 分鐘內完成,表示流程某處不夠熟。

---

## Mock 題目池(按難度)

### Easy 熱身(給新手)

1. Two Sum (LC 1)
2. Valid Parentheses (LC 20)
3. Merge Two Sorted Lists (LC 21)

### Medium 核心(每天一題,持續 30 天)

4. Longest Substring Without Repeating Characters (LC 3)
5. 3Sum (LC 15)
6. Container With Most Water (LC 11)
7. Group Anagrams (LC 49)
8. Longest Palindromic Substring (LC 5)
9. Longest Consecutive Sequence (LC 128)
10. Number of Islands (LC 200)
11. Course Schedule (LC 207)
12. Rotate Image (LC 48)
13. Set Matrix Zeroes (LC 73)
14. Subsets (LC 78)
15. Word Search (LC 79)
16. Kth Largest Element in an Array (LC 215)
17. Product of Array Except Self (LC 238)
18. Meeting Rooms II (LC 253)
19. Top K Frequent Elements (LC 347)
20. Validate Binary Search Tree (LC 98)
21. Implement Trie (LC 208)
22. Coin Change (LC 322)
23. Word Break (LC 139)
24. LRU Cache (LC 146)
25. Longest Increasing Subsequence (LC 300)
26. Search in Rotated Sorted Array (LC 33)
27. Find Minimum in Rotated Sorted Array (LC 153)
28. Decode Ways (LC 91)
29. Generate Parentheses (LC 22)
30. Daily Temperatures (LC 739)
31. Clone Graph (LC 133)
32. Pacific Atlantic Water Flow (LC 417)
33. Binary Tree Level Order Traversal (LC 102)
34. Construct Binary Tree from Preorder and Inorder (LC 105)

### Hard 壓力測試(每週 2-3 題)

35. Merge k Sorted Lists (LC 23)
36. Trapping Rain Water (LC 42)
37. Largest Rectangle in Histogram (LC 84)
38. Word Ladder (LC 127)
39. Word Ladder II (LC 126)
40. Edit Distance (LC 72)
41. Regular Expression Matching (LC 10)
42. Median of Two Sorted Arrays (LC 4)
43. Serialize and Deserialize Binary Tree (LC 297)
44. Find Median from Data Stream (LC 295)
45. Minimum Window Substring (LC 76)

---

## Mock 後的 retrospective

**每次 mock 完立刻做**(趁感覺還在):

1. 最卡的時刻是什麼?
   - Clarify 不夠? 沒想到優化? 寫 bug? 時間不夠?

2. 面試官(或錄音中的自己)有什麼反饋?
   - 溝通不清楚?邊界沒處理?複雜度沒分析?

3. 這題的「核心 insight」是什麼?
   - 我有抓到嗎?多久抓到的?

4. 下次同類題怎麼改進?
   - 具體動作,不是「我要更快」這種廢話

**把這些記錄在 `mock-log.md` 或紙本**。看累積 20 次 mock 的 log,會看出自己的弱點模式。

---

## 常見 Mock 敗招

### 敗招 1:沉默思考

超過 30 秒沒發聲音。面試官以為你卡住或放棄。

**破解**:想不到的時候也要說「I'm trying to see if hashing helps here」。

### 敗招 2:直接衝最優解

跳過 brute force,也跳過 clarify。寫到一半發現題意搞錯。

**破解**:強迫自己前 5 分鐘不碰鍵盤。

### 敗招 3:寫 code 時停止溝通

開始打字後就安靜了 15 分鐘。面試官無法 follow,結束時滿臉問號。

**破解**:每寫完一段(5 行左右)說一下「這段在做什麼」。

### 敗招 4:寫完不主動 test

丟給面試官「done!」,等對方發現 bug。

**破解**:寫完立刻 trace 一個 example。自己抓到 bug 會加分,面試官抓到會扣分。

### 敗招 5:複雜度不分析

寫完等面試官問。

**破解**:寫完最後一行主動說「Time is O(n log n) because ...」

---

## 連續 30 天計畫

如果 onsite 在 1 個月內:

- **週一至週五**:每天自己對鏡子 1 題,45 分鐘。
- **週六**:Pramp 做 2 題(當面試官一次、當候選人一次)。
- **週日**:review 這週所有 mock,整理 bug log、優化話術。

堅持 4 週後,onsite 的第一題不會再慌。**這不是可選項,是能不能過的分水嶺**。

---

## 最後的話

這門課的最後一份練習。走到這裡你應該具備:

- 看題能辨識演算法(Ch 28 的速查表)
- 會寫模板(前 25 章)
- 能流暢溝通(Ch 26)
- 踩過常見雷(Ch 27)

但**這些加起來還不等於「能過面試」**。Mock 是把它們黏起來的膠水。省這步,前面全白讀。

→ [Final Project — 建立你自己的對照表](./final-project-pattern-map.md)
