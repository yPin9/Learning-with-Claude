# Ch 40 — sorting

> **目標**：掌握主流排序演算法的複雜度、穩定性、原地與否，能手寫 quicksort 和 merge sort，理解 quicksort 最壞情況與 partition、以及「什麼時候用哪個」。排序是面試必考——複雜度表要背熟，至少一個要能手寫。

> **環境**：C，`gcc -Wall`。前置：Ch 36（array）、Ch 38（heap → heap sort）。

## 為什麼考這個

排序是「演算法分析」的最佳教材——同一個問題有 O(n²) 和 O(n log n) 的解，比較它們能測你對複雜度、分治、取捨的理解。面試常要你「手寫 quicksort」「比較 quicksort 和 merge sort」「quicksort 最壞情況」。複雜度表是必拿分的記憶題。

## 複雜度總表（必背）

| 演算法 | 平均 | 最壞 | 最佳 | 空間 | 穩定 | 原地 |
|---|---|---|---|---|---|---|
| Bubble sort | O(n²) | O(n²) | O(n) | O(1) | ✓ | ✓ |
| Selection sort | O(n²) | O(n²) | O(n²) | O(1) | ✗ | ✓ |
| Insertion sort | O(n²) | O(n²) | O(n) | O(1) | ✓ | ✓ |
| **Quick sort** | O(n log n) | **O(n²)** | O(n log n) | O(log n) | ✗ | ✓ |
| **Merge sort** | O(n log n) | O(n log n) | O(n log n) | **O(n)** | ✓ | ✗ |
| **Heap sort** | O(n log n) | O(n log n) | O(n log n) | O(1) | ✗ | ✓ |
| Counting sort | O(n+k) | O(n+k) | O(n+k) | O(k) | ✓ | ✗ |

關鍵記憶點：
- **比較排序的下限是 O(n log n)**（理論證明，靠比較最快就這樣）。
- **quick sort 平均最快**（常數小、cache 友善），但**最壞 O(n²)**。
- **merge sort 穩定且保證 O(n log n)**，但要 O(n) 額外空間。
- **heap sort 原地 + 保證 O(n log n)**，但常數大、cache 差。
- **counting sort 突破 O(n log n)**——因為它不比較（用計數），但要 key 範圍小（k）。

**穩定（stable）**：相同 key 的元素排序後相對順序不變。多關鍵字排序時重要（先排次要、再排主要）。
**原地（in-place）**：只用 O(1) 或 O(log n) 額外空間。

## quicksort（必手寫）

分治：選一個 pivot，partition 成「< pivot」和「> pivot」兩堆，遞迴排兩堆。

```c
void swap(int *a, int *b) { int t = *a; *a = *b; *b = t; }

int partition(int arr[], int low, int high) {
    int pivot = arr[high];      // 選最後一個當 pivot（Lomuto）
    int i = low - 1;            // i = 「< pivot 區」的右界
    for (int j = low; j < high; j++) {
        if (arr[j] < pivot) {   // 比 pivot 小 → 換到左區
            i++;
            swap(&arr[i], &arr[j]);
        }
    }
    swap(&arr[i+1], &arr[high]);// pivot 歸位
    return i + 1;               // pivot 最終位置
}

void quicksort(int arr[], int low, int high) {
    if (low < high) {
        int p = partition(arr, low, high);
        quicksort(arr, low, p - 1);    // 排左半
        quicksort(arr, p + 1, high);   // 排右半
    }
}
```

**最壞 O(n²)**：每次 pivot 都選到最大或最小（如已排序的 array + 選最後一個當 pivot）→ partition 切成 0 和 n-1 → 退化。
解法：**隨機選 pivot** 或 **三數取中（median-of-three）**，讓最壞情況難發生。

## merge sort（必懂）

分治：對半切、遞迴排兩半、merge（合併兩個排好的）：

```c
void merge(int arr[], int l, int m, int r) {
    int n1 = m-l+1, n2 = r-m;
    int L[n1], R[n2];                       // 額外空間 O(n)！
    for (int i=0;i<n1;i++) L[i]=arr[l+i];
    for (int j=0;j<n2;j++) R[j]=arr[m+1+j];
    int i=0,j=0,k=l;
    while (i<n1 && j<n2) arr[k++] = (L[i]<=R[j]) ? L[i++] : R[j++];  // <= 保持穩定
    while (i<n1) arr[k++]=L[i++];
    while (j<n2) arr[k++]=R[j++];
}
void mergesort(int arr[], int l, int r) {
    if (l < r) {
        int m = l + (r-l)/2;       // 防溢位（不用 (l+r)/2，Ch 9）
        mergesort(arr, l, m);
        mergesort(arr, m+1, r);
        merge(arr, l, m, r);
    }
}
```

merge sort **保證 O(n log n)**（不管輸入）、**穩定**（merge 時 `<=` 取左邊），但要 **O(n) 額外空間**。適合 linked list（不用隨機存取）、外部排序（資料太大放不進記憶體）。

注意 `m = l + (r-l)/2` 而非 `(l+r)/2`——後者 l+r 可能整數溢位（Ch 9 的踩雷）。

## quick sort vs merge sort（高頻對比）

| | quick sort | merge sort |
|---|---|---|
| 平均 | O(n log n) | O(n log n) |
| 最壞 | **O(n²)** | O(n log n)（保證）|
| 空間 | O(log n)（遞迴堆疊）| **O(n)** |
| 穩定 | ✗ | ✓ |
| cache | **好**（原地、局部性）| 較差 |
| 實務 | 一般 array 首選（常數小）| 要穩定 / linked list / 外部排序 |

實務上 quick sort 常更快（cache 友善、常數小），所以多數標準庫的 array 排序基於 quicksort 變體（如 introsort：quicksort + 退化時轉 heapsort 保證 O(n log n)）。

## 考古題詳解

### Q1：各排序的時間複雜度？哪些是 O(n log n)？

<details>
<summary>詳解</summary>

O(n²)：bubble/selection/insertion（平均）。O(n log n)：quick（平均，最壞 O(n²)）、merge、heap。O(n+k)：counting（非比較）。

比較排序下限 O(n log n)。

**考點**：複雜度表，必背必拿分。
</details>

### Q2：手寫 quicksort，並說最壞情況

<details>
<summary>詳解</summary>

（見上方 partition + quicksort 程式）分治：選 pivot → partition（小的左、大的右）→ 遞迴兩半。

最壞 **O(n²)**：pivot 每次選到極值（如已排序資料選最後一個）→ 切成 0 和 n-1。解法：隨機 pivot / 三數取中。

**考點**：手寫 quicksort + 最壞情況，最高頻。
</details>

### Q3：quick sort 和 merge sort 怎麼選？

<details>
<summary>詳解</summary>

- **quick sort**：一般 array 首選——平均 O(n log n)、常數小、原地（O(log n) 堆疊）、cache 好。但最壞 O(n²)、不穩定。
- **merge sort**：要穩定、要保證 O(n log n)、linked list、外部排序（資料太大）時用。代價 O(n) 額外空間。

實務 array 多用 quicksort 變體（introsort）。

**考點**：兩者取捨，必考。
</details>

### Q4：什麼是穩定排序？為什麼重要？

<details>
<summary>詳解</summary>

穩定 = 相同 key 的元素排序後相對順序不變。重要於**多關鍵字排序**：先按次要 key 排、再按主要 key 排（穩定排序），次要順序會保留。例：先按名字排、再按年齡穩定排 → 同齡者仍按名字序。

穩定：bubble/insertion/merge/counting。不穩定：selection/quick/heap。

**考點**：穩定性定義 + 用途。
</details>

### Q5：能不能比 O(n log n) 更快排序？

<details>
<summary>詳解</summary>

**比較排序**不行——理論下限 O(n log n)（n 個元素有 n! 種排列，比較樹高至少 log(n!) ≈ n log n）。

但**非比較排序**可以：counting sort O(n+k)、radix sort O(d·n)、bucket sort——它們不靠比較，靠 key 的數值性質。代價：要 key 範圍小（counting 的 k）或特定分布。嵌入式排小範圍整數（如感測值 0–255）時 counting sort 很快。

**考點**：排序下限 + 非比較排序，分辨深度。
</details>

## 踩雷集錦

1. **quicksort 以為一定 O(n log n)**：最壞 O(n²)（pivot 選爛）。要隨機/三數取中避免。
2. **merge sort 忘了 O(n) 空間**：它不是原地，要額外陣列。記憶體緊時是缺點。
3. **`(l+r)/2` 整數溢位**：大陣列 l+r 可能溢位。用 `l+(r-l)/2`（Ch 9）。
4. **穩定性記錯**：quick/heap/selection 不穩定；merge/insertion/bubble 穩定。
5. **以為非比較排序萬能**：counting/radix 要 key 範圍小或整數。range 大就爆空間。
6. **partition 邊界錯**：Lomuto 的 i/j 指標、pivot 歸位位置易錯。多練。
7. **小陣列還用 quicksort**：小 n（如 < 16）insertion sort 反而快（常數小）。實務庫對小段切換 insertion。

## 速記

- **複雜度**：bubble/selection/insertion O(n²)；quick/merge/heap O(n log n)；counting O(n+k)。比較排序下限 **O(n log n)**。
- **quick sort**：分治 partition，平均 O(n log n)、原地、cache 好、常數小（array 首選）；**最壞 O(n²)**（pivot 選爛 → 隨機/三數取中）、不穩定。
- **merge sort**：分治 merge，**保證 O(n log n)**、**穩定**；但 **O(n) 額外空間**。適合 linked list / 外部排序。
- **heap sort**：原地 + 保證 O(n log n)，但常數大 cache 差、不穩定。
- **穩定** = 相同 key 順序不變（多關鍵字排序用）：merge/insertion/bubble/counting 穩定；quick/heap/selection 不穩定。
- **非比較排序**（counting/radix）可突破 O(n log n)，但要 key 範圍小。
- `l+(r-l)/2` 防溢位。

## 自我檢核

- [ ] 不看，能默寫七種排序的複雜度與穩定性表嗎？
- [ ] 能手寫 quicksort 嗎？最壞情況什麼時候發生、怎麼避免？
- [ ] quick sort 和 merge sort 怎麼選？各的代價？
- [ ] 穩定排序是什麼？為什麼多關鍵字排序需要它？
- [ ] 為什麼比較排序不可能比 O(n log n) 快？什麼排序能突破？

## 延伸閱讀

### 書籍

- **《Introduction to Algorithms (CLRS)》** — Ch 7（Quicksort）、Ch 8（Sorting in Linear Time）
  - **讀哪幾章**：7.1–7.4（quicksort 與隨機化）、8.1（比較排序下限證明）、8.2（counting sort）。
  - **和本章的關聯**：排序的權威分析，含 O(n log n) 下限的完整證明。

### 文章

- **[GeeksforGeeks — Sorting Algorithms](https://www.geeksforgeeks.org/sorting-algorithms/)**
  - **讀哪裡**：各排序的視覺化與實作對比表。
  - **和本章的關聯**：快速複習各演算法細節與動畫。

排序完成，下一章收尾演算法 Part——搜尋、圖走訪（BFS/DFS）與複雜度分析（Big-O），把演算法思維補齊。

→ [Ch 41 search / graph / 複雜度分析](./41-search-graph-complexity.md)
