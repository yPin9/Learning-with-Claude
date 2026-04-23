# Ch 28 — 題目辨識速查:訊號 → 演算法

> 目標:把整門課濃縮成一張「看到 X 訊號就想到 Y」的對照表。面試前 30 分鐘過一次,比刷 10 題有用。

---

## 從題目結構辨識

### 看到 array / string

| 訊號 | 可能工具 |
|---|---|
| sorted + 找 pair | 雙指針(相向) |
| sorted + 找 target | 二分 |
| subarray 連續 + 和 / 長度 | sliding window 或前綴和 |
| subarray 連續 + 有負數 | 前綴和 + hash(sliding window 不 work) |
| subsequence 不連續 | DP |
| 「top k」 | heap(min-heap 維持 k 個最大)或 quickselect |
| 「第 k 個」 | quickselect 或 heap |
| 「所有組合 / 排列」 | backtracking |
| 「去重後 ...」 | set / sort + 雙指針 |
| 「連續遞增」 | 貪婪 / 雙指針 |
| 「最長遞增子序列」(不連續) | DP O(n²) 或 patience sort O(n log n) |
| 「回文」 | 中心擴展或 Manacher |
| 「anagram」 | 計數 / sorted key |

### 看到 tree

| 訊號 | 可能工具 |
|---|---|
| 「最大 / 最小 / 深度」 | postorder 遞迴 |
| 「從根到葉的 path」 | preorder + backtrack |
| BST + 有序 | inorder |
| 「level 相關」 | BFS + `len(q)` 分層 |
| 「兩節點的關係」 | LCA |
| 「序列化」 | preorder + null 標記 |
| 「左 / 右視角」 | BFS 取每層最後 / 第一個 |

### 看到 graph

| 訊號 | 可能工具 |
|---|---|
| 「最少步數」+ 等權 | BFS |
| 「最少步數」+ 權重 0/1 | 0-1 BFS |
| 「最少權重」+ 正權 | Dijkstra |
| 「最少權重」+ 可負 | Bellman-Ford |
| 「全對全最短」 | Floyd(V 小) |
| 「依賴順序」 / 「先修」 | 拓撲排序 |
| 「環檢測」(有向) | DFS 三色 |
| 「環檢測」(無向) | DFS / DSU |
| 「連通分量」 | DFS / BFS / DSU |
| 「動態合併」 | DSU |
| 「連通最小成本」 | MST(Kruskal / Prim) |
| 「2D grid 類」 | DFS / BFS + 方向向量 |
| 「狀態擴展」 | 帶狀態的 BFS / Dijkstra |

### 看到區間

| 訊號 | 可能工具 |
|---|---|
| 「合併」 | sort + 掃 |
| 「最多重疊」 | sweep line 或 heap |
| 「最少刪除不重疊」 | sort by end + 貪婪 |
| 「會議室數量」 | heap / sweep line |

### 看到選擇 / 組合

| 訊號 | 可能工具 |
|---|---|
| 「所有排列 / 組合 / 子集」 | backtracking |
| 「選或不選 + 容量限制」 | 01 背包 DP |
| 「湊目標」 | 完全背包 DP |
| 「路徑 / 順序」+ 圖 | DFS + memo / 拓撲 DP |

---

## 從複雜度要求辨識

| n 範圍 | 可行複雜度 | 提示的演算法 |
|---|---|---|
| n ≤ 10 | O(n!) / O(2^n × n) | 全排列 / 全子集枚舉 / 狀壓 DP |
| n ≤ 20 | O(2^n) | 子集枚舉 / 狀壓 DP |
| n ≤ 100 | O(n³) | Floyd / 三維 DP |
| n ≤ 1000 | O(n²) | 二維 DP / LIS 的 O(n²) |
| n ≤ 10⁵ | O(n log n) | sort / 堆 / 線段樹 / 二分 |
| n ≤ 10⁶ | O(n) | sliding window / 前綴和 / BFS |
| n ≤ 10⁹ | O(log n) / O(√n) | 二分答案 / 數學 |

**訊號反向用**:n=100,暗示 O(n³) 可以 → 三維 DP 或 Floyd。

---

## 從字眼辨識

| 題目出現字眼 | 很可能是 |
|---|---|
| "count the number of ways" | DP |
| "find the minimum / maximum" | DP / 貪婪 / 二分答案 |
| "is there a path" | DFS / BFS / DSU |
| "connected" | DFS / BFS / DSU |
| "sorted" | 二分 / 雙指針 |
| "shortest" | BFS / Dijkstra |
| "rearrange" / "permute" | backtrack / 貪婪 |
| "partition" | DP / 貪婪 |
| "substring" | sliding window / KMP |
| "subsequence" | DP |
| "anagram" | Counter |
| "k 大 / k 小 / k 個" | heap / quickselect |

---

## 從輸入形狀辨識

| 輸入 | 常見 |
|---|---|
| 一個 int n | 數學 / DP |
| 一個 array | 單指針 / 雙指針 / hash |
| 兩個 array | 雙指針 / hash / DP(LCS 類) |
| 一個 string | 雙指針 / hash / DP / KMP |
| 一組 intervals | sort + 掃 / sweep line |
| 一個 tree | 遞迴 |
| 一個 graph(edges) | 建 adj list + BFS/DFS/DSU |
| 2D grid | 方向向量 + BFS/DFS |
| 一組 words | trie / DP / BFS(Word Ladder) |

---

## 解題心法 Flow Chart

```
看到題
  ↓
1. 輸入是什麼形狀?
    array / tree / graph / grid / intervals / string
  ↓
2. 問什麼?
    最大 / 最小 / 計數 / 存在性 / 所有解
  ↓
3. n 有多大?
    → 目標複雜度
  ↓
4. 有沒有結構?
    sorted? BST? DAG? 連續?
  ↓
5. 從本章對照表找候選演算法
  ↓
6. 驗證:這個演算法的假設都滿足嗎?
  ↓
7. 寫 brute force → optimize → code → test
```

---

## 速查口訣

背下來:

- **Sorted → 二分 / 雙指針**
- **Subarray → sliding / 前綴和**
- **Top k → heap**
- **Shortest → BFS / Dijkstra**
- **All combos → backtrack**
- **最優化 + 重疊 → DP**
- **連通 / 合併 → DSU**
- **依賴 → 拓撲**
- **最大重疊 → sweep line**
- **n 很小 → 狀壓 / 回溯**

---

## 遇到不在清單裡的題怎麼辦

對照表覆蓋 80% 面試題。剩下 20% 是組合題——兩三個 pattern 疊一起。應對:

1. **先辨識「最明顯的訊號」**:這部分像 X,那部分像 Y。
2. **想「能不能拆成兩個子問題」**:一步 sort + 一步 DP、或先找候選 + 再驗證。
3. **卡住就問面試官**:「我注意到這看起來像 X,但又有 Y 的特徵。能否提示方向?」

---

## 最後提醒

這張表**不是學習,是速查**。看這張表前,你應該已經讀完前 27 章。

不然你只是換一種方式在背答案,沒建立思考。

---

→ [Practice D — Mock Interview](./practice-d-mock-interview.md)
→ [Final Project — 建立你自己的對照表](./final-project-pattern-map.md)
