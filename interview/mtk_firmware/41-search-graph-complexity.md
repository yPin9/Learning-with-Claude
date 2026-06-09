# Ch 41 — search / graph / 複雜度分析

> **目標**：掌握二分搜尋（binary search）、圖的表示與走訪（BFS/DFS）、以及 Big-O 複雜度分析的方法。這章把演算法 Part 收尾——複雜度分析是貫穿所有題目的基本功，面試必問。

> **環境**：C，`gcc -Wall`。前置：Ch 37（stack/queue → DFS/BFS）、Ch 40（複雜度概念）。

## 為什麼考這個

複雜度分析（Big-O）是讀任何演算法題的共同語言——面試官問完做法一定追問「複雜度多少」。binary search 是「有序就能加速」的經典；圖走訪（BFS/DFS）是樹走訪的一般化，連結 Ch 37 的 stack/queue。這章是 Part 5 的整合。

## 複雜度分析（Big-O）

Big-O 描述「輸入變大時，執行時間/空間怎麼成長」（漸進、看最高次項、忽略常數）。

```
   O(1)      < O(log n) < O(n)   < O(n log n) < O(n²)   < O(2ⁿ)  < O(n!)
   常數         對數        線性       線性對數       平方       指數      階乘
   ↑越快                                                          越慢↑
```

常見來源：
- **O(1)**：陣列存取、hash 查找（平均）。
- **O(log n)**：binary search、平衡 BST、heap 操作——「每次砍一半」。
- **O(n)**：走一遍陣列、linked list 走訪。
- **O(n log n)**：好的排序（merge/quick）、「對每個元素做 log n 工作」。
- **O(n²)**：雙層迴圈、爛排序（bubble）。
- **O(2ⁿ)**：窮舉子集、naive 費氏數列遞迴。

分析技巧：
- 看迴圈：單層 O(n)、雙層巢狀 O(n²)、每次砍半 O(log n)。
- 遞迴用遞迴式：`T(n)=2T(n/2)+O(n)` → O(n log n)（merge sort，主定理）。
- **只看最高次項、丟常數**：`3n²+5n+2` → O(n²)。

口頭常考：「best/average/worst case」（如 quicksort 平均 O(n log n)、最壞 O(n²)）；「時間 vs 空間複雜度」要分開講。

## binary search（二分搜尋）

前提：**陣列已排序**。每次比中間，砍掉一半 → O(log n)：

```c
int binary_search(int arr[], int n, int target) {
    int lo = 0, hi = n - 1;
    while (lo <= hi) {                  // 注意 <=
        int mid = lo + (hi - lo) / 2;   // 防溢位（Ch 9/40）
        if (arr[mid] == target) return mid;
        else if (arr[mid] < target) lo = mid + 1;   // 在右半
        else hi = mid - 1;                           // 在左半
    }
    return -1;  // not found
}
```

魔鬼在邊界：
- `while (lo <= hi)`——用 `<=` 不是 `<`（否則漏查最後一個）。
- `mid = lo + (hi-lo)/2`——防 `lo+hi` 溢位。
- `lo = mid+1` / `hi = mid-1`——要 +1/-1，否則可能無限迴圈。

O(log n)：n=10億，最多約 30 次比較。對比線性搜尋 O(n)（10 億次）。

## 圖（graph）

圖 = 節點（vertex）+ 邊（edge）。有向/無向、有權/無權。兩種表示：

```
   鄰接矩陣 adjacency matrix：n×n 陣列，matrix[i][j]=1 表示 i→j 有邊
     - 查兩點是否相連 O(1)；但空間 O(n²)（稀疏圖浪費）

   鄰接串列 adjacency list：每節點一個 list 存它的鄰居
     - 空間 O(V+E)（稀疏圖省）；查相連要走 list
```

| | 鄰接矩陣 | 鄰接串列 |
|---|---|---|
| 空間 | O(V²) | O(V+E) |
| 查 i-j 相連 | O(1) | O(degree) |
| 遍歷某點鄰居 | O(V) | O(degree) |
| 適合 | 稠密圖 | 稀疏圖（多數情況）|

## 圖走訪：BFS 與 DFS

樹走訪的一般化（圖有環，要記 visited 防重複）。

**BFS（廣度優先）**：用 **queue**，一層一層擴散。找**最短路徑**（無權圖）：

```c
void bfs(int start) {
    int visited[N] = {0};
    int queue[N], front=0, rear=0;
    visited[start] = 1; queue[rear++] = start;
    while (front < rear) {
        int u = queue[front++];          // dequeue
        printf("%d ", u);
        for (each neighbor v of u)
            if (!visited[v]) { visited[v]=1; queue[rear++]=v; }  // enqueue
    }
}
```

**DFS（深度優先）**：用 **stack**（或遞迴），一條路走到底再回頭。找**連通性、環、拓樸排序**：

```c
void dfs(int u, int visited[]) {
    visited[u] = 1;
    printf("%d ", u);
    for (each neighbor v of u)
        if (!visited[v]) dfs(v, visited);   // 遞迴 = 隱含的 stack
}
```

兩者都 O(V+E)（每點每邊看一次）。**BFS=queue=最短路；DFS=stack/遞迴=走到底**。這是 Ch 37 stack/queue 的最大應用。

## 考古題詳解

### Q1：常見的 Big-O 從快到慢排序

<details>
<summary>詳解</summary>

O(1) < O(log n) < O(n) < O(n log n) < O(n²) < O(2ⁿ) < O(n!)。

各舉例：O(1) 陣列存取、O(log n) binary search、O(n) 遍歷、O(n log n) 好排序、O(n²) 雙迴圈、O(2ⁿ) 窮舉子集。只看最高次項、丟常數。

**考點**：複雜度排序 + 來源，必拿分。
</details>

### Q2：手寫 binary search，講邊界陷阱

<details>
<summary>詳解</summary>

（見上方程式）前提陣列已排序。三個陷阱：
1. `while(lo <= hi)` 用 `<=`（用 `<` 漏最後一個）。
2. `mid = lo+(hi-lo)/2` 防溢位。
3. `lo=mid+1`/`hi=mid-1` 要 ±1（否則無限迴圈）。

O(log n)。

**考點**：手寫 binary search + 邊界，高頻（最易寫錯的「簡單」題）。
</details>

### Q3：BFS 和 DFS 差在哪？各用什麼資料結構？各的應用？

<details>
<summary>詳解</summary>

- **BFS**：用 **queue**，一層層擴散。應用：無權圖最短路徑、層序。
- **DFS**：用 **stack（或遞迴）**，一條路走到底。應用：連通性、找環、拓樸排序、回溯。

都 O(V+E)，都要 visited 防重複（圖有環）。

**考點**：BFS vs DFS，必考（連結 stack/queue）。
</details>

### Q4：圖的兩種表示法？怎麼選？

<details>
<summary>詳解</summary>

- **鄰接矩陣**：n×n 陣列，查相連 O(1)，但空間 O(V²)——適合稠密圖。
- **鄰接串列**：每點存鄰居 list，空間 O(V+E)——適合稀疏圖（多數實際圖）。

稀疏圖（邊遠少於 V²）用 list，稠密圖或常查任兩點相連用 matrix。

**考點**：圖表示法取捨。
</details>

### Q5：怎麼分析一段巢狀迴圈的複雜度？

<details>
<summary>詳解</summary>

看迴圈次數相乘：
```c
for (i=0;i<n;i++)          // n 次
    for (j=0;j<n;j++)      // 每次又 n 次
        sum++;             // → O(n²)
```
單層 O(n)、雙層巢狀 O(n²)。但要看實際範圍：`for(j=i;j<n;j++)` 是 n+(n-1)+...+1 = n(n+1)/2 → 仍 O(n²)。每次砍半（`j*=2`）→ O(log n)。遞迴用遞迴式（主定理）。最後只留最高次項、丟常數。

**考點**：複雜度分析方法，貫穿所有題。
</details>

## 踩雷集錦

1. **binary search 用 `<` 不是 `<=`**：漏查最後一個元素。
2. **binary search 忘 ±1**：`lo=mid` 不 +1 → 無限迴圈。
3. **binary search 沒先排序**：前提是有序，否則結果錯。
4. **圖走訪忘 visited**：圖有環，不記 visited 會無限迴圈（樹沒環所以不用，這是圖 vs 樹的差別）。
5. **BFS/DFS 結構記反**：BFS=queue、DFS=stack/遞迴。
6. **複雜度看常數**：`O(2n)`、`O(n+5)` 都是 O(n)，丟常數。但實務上常數有時重要（quicksort vs heapsort）。
7. **空間複雜度漏算遞迴堆疊**：遞迴 DFS 的 stack 深度算空間（最壞 O(V)）。

## 速記

- **Big-O**：O(1)<O(log n)<O(n)<O(n log n)<O(n²)<O(2ⁿ)<O(n!)；看最高次項、丟常數；分 best/avg/worst、時間/空間。
- **binary search**：前提**已排序**，每次砍半 O(log n)；陷阱 `<=`、`lo+(hi-lo)/2`、±1。
- **圖表示**：鄰接矩陣（O(V²)、查相連 O(1)、稠密）vs 鄰接串列（O(V+E)、稀疏，常用）。
- **BFS=queue=最短路（無權）**；**DFS=stack/遞迴=連通/找環/拓樸**；都 O(V+E)、都要 **visited**（圖有環）。
- 複雜度分析：迴圈相乘、遞迴用遞迴式、丟常數。

## 自我檢核

- [ ] 能由快到慢排出常見 Big-O，並各舉一個來源嗎？
- [ ] 不看，能手寫 binary search 並避開三個邊界陷阱嗎？
- [ ] BFS 和 DFS 各用什麼資料結構？各擅長什麼問題？
- [ ] 圖的兩種表示法怎麼選？為什麼圖走訪要 visited 而樹不用？
- [ ] 給一段巢狀迴圈，你能算出它的複雜度嗎？

## 延伸閱讀

### 書籍

- **《Introduction to Algorithms (CLRS)》** — Ch 22（圖走訪 BFS/DFS）、Ch 3（複雜度漸進記號）
  - **讀哪幾章**：3.1（Big-O 定義）、22.2（BFS）、22.3（DFS）。
  - **和本章的關聯**：圖演算法與複雜度的權威來源。

### 文章

- **[GeeksforGeeks — Graph Data Structure And Algorithms](https://www.geeksforgeeks.org/graph-data-structure-and-algorithms/)**
  - **讀哪裡**：BFS/DFS、圖表示法篇。
  - **和本章的關聯**：補強圖走訪實作與更多圖演算法。

資料結構與演算法 Part 完成。接著用練習 E 把這個 Part 的手寫題驗收一遍。

→ [練習 E：資料結構手寫題](./practice-e-ds-handwriting.md)
