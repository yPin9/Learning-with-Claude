# 演算法:面試導向的系統化課程

> 給已經會寫 code、刷過一些題,還停留在「靠 pattern 記憶」階段,想從原理上把它打通的工程師。Python 為主。

---

## 這門課不是什麼

- **不是 LeetCode 題解集**。題解滿網路都是,這裡講的是思考方式。
- **不是競程教材**。FFT、網路流、重鏈剖分這些面試不考,不收。
- **不是演算法學術書**。不刻意證明漸近下界,但會教你一眼看出「這題會是 O(n log n)」的直覺。

## 這門課講什麼

- 複雜度分析的**實用形式**:面試官問時間複雜度,他想聽的不是 Big-O 定義。
- **Python 特有的刀與陷阱**:`list.pop(0)` 是 O(n),你用錯就掉進去。
- **題型辨識的訊號**:看到「subarray sum」、「k-th」、「minimum window」這些字,應該本能反應出演算法。
- **白板上的溝通流程**:code 寫對只佔 40%,剩下 60% 是跟面試官的對話。

## 為什麼用 Python

面試公司普遍接受。語法乾淨、標準庫強、`dict` / `heapq` 拿來就用。代價是要清楚它的慢操作在哪(`list.pop(0)` 是 O(n),字串拼接迴圈是 O(n²),切片會複製)。這些陷阱 Ch 1–2 會處理掉。

## 課程地圖

### Part 0 — 心法
- [Ch 0 為什麼刷 500 題還是不會](./00-why-grinding-fails.md)
- [Ch 1 複雜度:面試官真正想聽的答案](./01-complexity.md)
- [Ch 2 Python 的刀:標準庫速查](./02-python-toolkit.md)

### Part 1 — 線性結構
- [Ch 3 Array / String:雙指針與滑動視窗](./03-two-pointers-sliding-window.md)
- [Ch 4 Hash 的威力與陷阱](./04-hash.md)
- [Ch 5 Stack / Queue / Monotonic Stack](./05-stack-queue.md)
- [Ch 6 Linked List](./06-linked-list.md)
- [Practice A — 線性結構綜合](./practice-a-linear.md)

### Part 2 — 樹與圖
- [Ch 7 Binary Tree:recursion 是唯一方法](./07-binary-tree.md)
- [Ch 8 BST / Trie](./08-bst-trie.md)
- [Ch 9 Heap / Priority Queue](./09-heap.md)
- [Ch 10 Union-Find](./10-union-find.md)
- [Ch 11 Graph:BFS / DFS](./11-graph-bfs-dfs.md)
- [Ch 12 Topological Sort](./12-topological-sort.md)
- [Ch 13 Shortest Path](./13-shortest-path.md)
- [Ch 14 MST(點到即可)](./14-mst.md)
- [Practice B — 樹與圖](./practice-b-tree-graph.md)

### Part 3 — 演算法範式
- [Ch 15 Binary Search:找答案不是找數字](./15-binary-search.md)
- [Ch 16 Greedy:什麼時候可以貪](./16-greedy.md)
- [Ch 17 Backtracking:剪枝才是關鍵](./17-backtracking.md)
- [Ch 18 Divide & Conquer](./18-divide-conquer.md)
- [Ch 19 DP 一:辨識與模板](./19-dp-basics.md)
- [Ch 20 DP 二:區間 / 樹形 / 狀壓](./20-dp-advanced.md)
- [Ch 21 DP 三:經典題型](./21-dp-classics.md)
- [Practice C — DP 專項](./practice-c-dp.md)

### Part 4 — 雜項但會考
- [Ch 22 Bit Manipulation](./22-bit-manipulation.md)
- [Ch 23 Interval Problems](./23-intervals.md)
- [Ch 24 字串進階(KMP / Rabin-Karp)](./24-string-advanced.md)
- [Ch 25 數學與數論](./25-math.md)

### Part 5 — 面試實戰
- [Ch 26 白板流程](./26-whiteboard.md)
- [Ch 27 常見坑](./27-pitfalls.md)
- [Ch 28 題目辨識速查](./28-pattern-recognition.md)
- [Practice D — Mock interview](./practice-d-mock-interview.md)

### Final Project
- [自建「訊號 → 演算法」對照表](./final-project-pattern-map.md)

---

## 學習建議

1. **Part 0 一定先讀**。它定調整門課的思考方式,跳過的話後面每一章你都在錯頻道接收。
2. **不按章節順序硬刷**。Part 0 讀完後,挑你最弱的 Part 優先處理。
3. **每章末的自我檢核做完再往下**,不是逐字讀過就算。檢核裡故意放了些「你以為你懂但其實不懂」的點。
4. **Practice 不是讀的,是寫的**。開 LeetCode 或白紙,自己實作。沒自己寫過,不算學會。
5. **Final project 不是總結**,是把整門課的知識變成你自己的速查表。那份表帶到面試前 30 分鐘看一遍,才真的有用。

## 學這個要幾個小時?

對已經會寫 code 但演算法薄弱的工程師,大致估算:

- Part 0(心法):3–4 小時,純讀。
- Part 1–4(技術章):每章 1–2 小時讀 + 2–4 小時做對應 LeetCode 題。全部約 80–120 小時。
- Part 5(面試實戰):讀 3 小時 + mock interview 10 小時。
- Final project:8–12 小時整理出自己的速查表。

**總計 100–150 小時**才會到能穩過 onsite 的程度。少於 50 小時的話,你大概率只是換個方式在刷題。

## 參考

- *Algorithm Design Manual*, Skiena — 面試視角的經典。每題都有「war story」,看解題者怎麼思考。
- *Introduction to Algorithms*, CLRS — 嚴謹但太厚,當字典,不要當教材讀。
- *Competitive Programming 4*, Halim — 題型辨識的範本,偏競程但思路共通。
- Neetcode 150 / Blind 75 — 刷題清單,配合這門課的 Ch 28 使用。
- LeetCode Discuss 區的頂票題解 — 比官方題解有用 10 倍。
