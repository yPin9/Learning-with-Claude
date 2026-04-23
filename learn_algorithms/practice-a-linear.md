# Practice A — 線性結構綜合

> 目標:把 Ch 3–6 的工具(雙指針、sliding window、hash、stack/queue、linked list)用到實戰題目上。

## 怎麼練

**每題做完再看提示**。提示只給「訊號」和「大概思路」,不給 code。卡超過 30 分鐘再看 LeetCode discuss。

**完成一題的標準**:

1. 自己在白紙 / 空白 IDE 寫出 code
2. 自己跑過至少 3 個 case(含 edge)
3. 能講出為什麼這解 work、複雜度是多少
4. 能想到至少一種 follow-up 變形

---

## 題目清單

### 雙指針 / Sliding Window

1. **Two Sum II - Input Array Is Sorted (LC 167)**
   - 提示:已排序,雙指針相向。
   - Follow-up:未排序版(LC 1)怎麼解?

2. **3Sum (LC 15)**
   - 提示:先 sort,固定一個 loop,剩下雙指針。去重是關鍵。
   - Edge case:重複元素、全負、全正。

3. **Container With Most Water (LC 11)**
   - 提示:雙指針 + 貪婪。「移動較矮那邊」的證明要能說。

4. **Longest Substring Without Repeating Characters (LC 3)**
   - 提示:sliding window + hash 記最後出現位置。

5. **Minimum Window Substring (LC 76, Hard)**
   - 提示:sliding window 的試金石。missing / need 兩個 state。

6. **Longest Repeating Character Replacement (LC 424)**
   - 提示:sliding window + `window_size - max_freq <= k` 的條件。
   - 陷阱:`max_freq` 不用每次精確維持,取歷史 max 也對,因為只要 window 能擴大就有意義。

7. **Find All Anagrams in a String (LC 438)**
   - 提示:固定長度 sliding window + Counter。

### Hash

8. **Two Sum (LC 1)**
   - 提示:一遍 hash,邊掃邊查。

9. **Subarray Sum Equals K (LC 560)**
   - 提示:前綴和 + hash。`seen[0] = 1` 初始化。

10. **Longest Consecutive Sequence (LC 128)**
    - 提示:set + 「只從序列起點開始數」的剪枝。

11. **Group Anagrams (LC 49)**
    - 提示:sorted 字串或字元計數 tuple 當 key。

12. **Top K Frequent Elements (LC 347)**
    - 提示:Counter + `most_common(k)`,或 bucket sort(面試官要 O(n))。

### Stack / Queue / Monotonic

13. **Valid Parentheses (LC 20)**
    - 基本款 stack。結尾別忘 check 空。

14. **Min Stack (LC 155)**
    - 存 `(val, current_min)`。

15. **Daily Temperatures (LC 739)**
    - Monotonic stack,單調遞減,遇到更大彈出。

16. **Largest Rectangle in Histogram (LC 84, Hard)**
    - Monotonic stack 的巔峰題。哨兵 0 收尾。

17. **Trapping Rain Water (LC 42, Hard)**
    - 三種解法:雙指針 / 單調 stack / 預計算左右最大值。每種都寫一次。

18. **Sliding Window Maximum (LC 239, Hard)**
    - Monotonic deque。踢過期 + 踢廢棄的 back。

### Linked List

19. **Reverse Linked List (LC 206)**
    - 四步節奏:記下一、反轉、進 prev、進 cur。

20. **Merge Two Sorted Lists (LC 21)**
    - Dummy head + tail 指針。

21. **Linked List Cycle (LC 141)**
    - 快慢指針。

22. **Linked List Cycle II (LC 142)**
    - Cycle 進階:相遇後放 slow 回 head,再次相遇就是環起點。要能說出數學推導。

23. **Remove Nth Node From End (LC 19)**
    - 快指針先走 n 步,dummy head 救命。

24. **Reorder List (LC 143)**
    - 三步:找中點、反轉後半、交替合併。

25. **Merge k Sorted Lists (LC 23, Hard)**
    - Heap:記得 tiebreaker。

26. **Copy List with Random Pointer (LC 138)**
    - Hash 兩遍最簡。交錯連結 O(1) space 是 follow-up。

---

## 進階挑戰(選做)

27. **Substring with Concatenation of All Words (LC 30, Hard)**
    - Sliding window 的極致變形。

28. **Longest Valid Parentheses (LC 32, Hard)**
    - Stack 記 index,或 DP。兩種都寫。

29. **Basic Calculator (LC 224) / II (LC 227)**
    - Stack 處理括號和運算子優先順序。

30. **LFU Cache (LC 460, Hard)**
    - LRU 的進階版。多層 doubly linked list + hash。

---

## 做完後的自我檢驗

每題做完問自己三個問題:

1. **這題的「訊號」是什麼?**(我看到什麼關鍵字就該想到這解法)
2. **如果題目變成 X,解還 work 嗎?**(X 自己編:變大、變多、加條件)
3. **最可能寫錯的地方是?**(把它寫在一個「bug 日誌」)

那個 bug 日誌,等你刷到第 30 題時會變成你最有價值的資產。

---

## 做完清單的標準

**不是刷完就完事**——而是:

- [ ] 每題都能 90 秒內口述思路
- [ ] 每題都能白紙無提示寫對
- [ ] 至少 10 題能跟朋友(或假想面試官)講一遍全流程
- [ ] Bug 日誌有 20 條以上

做到這些,Part 1 的 Medium 題應該都穩了。
