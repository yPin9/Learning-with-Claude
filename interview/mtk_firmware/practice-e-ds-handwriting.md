# 練習 E — 資料結構手寫題

> **目標**：驗收 Part 5（Ch 36–41）的資料結構與演算法——尤其**手寫題**（linked list 反轉、走訪、quicksort、binary search）。這些是 MTK 上機考和技術面白板題的核心。先遮答案，紙筆手寫。

> **環境**：C，`gcc -Wall`。前置：Part 5 全部。

## 怎麼用這份練習

DS/演算法面試分「概念」（複雜度、取捨）和「手寫」（白板/上機）。手寫題沒寫過就是寫不出來——這份逼你不看答案手寫。寫完用 gcc 編譯跑測資驗證（很多人手寫的 code 有 off-by-one）。

---

## 第一部分：手寫題（紙筆 → gcc 驗證）

### Q1（Ch 36）反轉 singly linked list（迭代）

<details>
<summary>參考解答</summary>

```c
typedef struct Node { int data; struct Node *next; } Node;
Node *reverse(Node *head) {
    Node *prev = NULL, *curr = head;
    while (curr) {
        Node *next = curr->next;   // 先存
        curr->next = prev;         // 反轉
        prev = curr; curr = next;  // 前進
    }
    return prev;
}
```
三指標，先存 next 再改 curr->next，回傳 prev。處理空 list（回 NULL）。Ch 36。
</details>

### Q2（Ch 36）找 linked list 是否有環（Floyd）

<details>
<summary>參考解答</summary>

```c
int has_cycle(Node *head) {
    Node *slow = head, *fast = head;
    while (fast && fast->next) {
        slow = slow->next; fast = fast->next->next;
        if (slow == fast) return 1;
    }
    return 0;
}
```
快慢指標，相遇=有環。O(n) 時間 O(1) 空間。邊界 `fast && fast->next`。Ch 36。
</details>

### Q3（Ch 38）中序走訪二元樹（遞迴 + 說明 BST 性質）

<details>
<summary>參考解答</summary>

```c
typedef struct TreeNode { int val; struct TreeNode *left, *right; } TreeNode;
void inorder(TreeNode *root) {
    if (!root) return;
    inorder(root->left);
    printf("%d ", root->val);
    inorder(root->right);
}
```
左根右。**BST 的中序 = 由小到大排序**。base case `!root`。Ch 38。
</details>

### Q4（Ch 40）手寫 quicksort

<details>
<summary>參考解答</summary>

```c
void swap(int *a,int *b){int t=*a;*a=*b;*b=t;}
int partition(int a[],int lo,int hi){
    int pivot=a[hi], i=lo-1;
    for(int j=lo;j<hi;j++) if(a[j]<pivot) swap(&a[++i],&a[j]);
    swap(&a[i+1],&a[hi]);
    return i+1;
}
void quicksort(int a[],int lo,int hi){
    if(lo<hi){ int p=partition(a,lo,hi); quicksort(a,lo,p-1); quicksort(a,p+1,hi); }
}
```
分治 partition。最壞 O(n²)（pivot 選爛），平均 O(n log n)。Ch 40。
</details>

### Q5（Ch 41）手寫 binary search

<details>
<summary>參考解答</summary>

```c
int binary_search(int a[], int n, int target){
    int lo=0, hi=n-1;
    while(lo<=hi){                       // <=
        int mid=lo+(hi-lo)/2;            // 防溢位
        if(a[mid]==target) return mid;
        else if(a[mid]<target) lo=mid+1; // ±1
        else hi=mid-1;
    }
    return -1;
}
```
前提已排序。三陷阱：`<=`、`lo+(hi-lo)/2`、±1。O(log n)。Ch 41。
</details>

### Q6（Ch 37）用 array 實作 circular queue 的 enqueue/dequeue

<details>
<summary>參考解答</summary>

```c
#define SIZE 8
typedef struct { int data[SIZE]; int front, rear, count; } CQ;
void init(CQ *q){ q->front=q->rear=q->count=0; }
int enqueue(CQ *q,int x){
    if(q->count==SIZE) return -1;        // 滿
    q->data[q->rear]=x; q->rear=(q->rear+1)%SIZE; q->count++;
    return 0;
}
int dequeue(CQ *q,int *out){
    if(q->count==0) return -1;           // 空
    *out=q->data[q->front]; q->front=(q->front+1)%SIZE; q->count--;
    return 0;
}
```
`%SIZE` 環繞，count 區分滿/空。= 韌體 ring buffer（練習 B Q7）。Ch 37。
</details>

---

## 第二部分：概念與分析

### Q7（Ch 36）array vs linked list 各 O()？何時用哪個？

<details>
<summary>解答</summary>

array：隨機存取 O(1)、中間插刪 O(n)、cache 友善、固定大小。linked list：隨機存取 O(n)、中間插刪 O(1)、cache 差、動態。array 用於隨機存取/cache；linked list 用於頻繁中間插刪。Ch 36。
</details>

### Q8（Ch 38）BST 什麼時候退化成 O(n)？heap 和 BST 差在哪？

<details>
<summary>解答</summary>

BST 插入已排序資料 → 退化成鏈 → O(n)（平衡 BST 如紅黑樹解決）。heap：完全二元樹、父子有序但左右無序、array 實作、取最值快；BST：左<根<右、可搜任意值、可排序。Ch 38。
</details>

### Q9（Ch 39）hash table 平均 O(1) 怎麼來？最壞？碰撞兩解法？

<details>
<summary>解答</summary>

hash function 把 key→索引，查找變陣列存取 O(1)。最壞 O(n)（全碰撞）。碰撞：chaining（掛 list）vs open addressing（找空 slot）。Ch 39。
</details>

### Q10（Ch 40）七種排序複雜度表 + 哪些穩定？

<details>
<summary>解答</summary>

bubble/selection/insertion O(n²)；quick 平均 O(n log n) 最壞 O(n²)；merge/heap O(n log n)；counting O(n+k)。穩定：bubble/insertion/merge/counting。不穩定：selection/quick/heap。Ch 40。
</details>

### Q11（Ch 41）BFS/DFS 各用什麼結構？複雜度？為什麼要 visited？

<details>
<summary>解答</summary>

BFS=queue（最短路）、DFS=stack/遞迴（連通/找環）。都 O(V+E)。圖有環，visited 防無限迴圈（樹無環所以不用）。Ch 41。
</details>

## 自評與弱點

| 題 | 章 | 類型 | 考點 |
|---|---|---|---|
| Q1 | 36 | 手寫 | linked list 反轉 |
| Q2 | 36 | 手寫 | Floyd 找環 |
| Q3 | 38 | 手寫 | 中序走訪 |
| Q4 | 40 | 手寫 | quicksort |
| Q5 | 41 | 手寫 | binary search |
| Q6 | 37 | 手寫 | circular queue |
| Q7 | 36 | 概念 | array vs list |
| Q8 | 38 | 概念 | BST 退化/heap |
| Q9 | 39 | 概念 | hash table |
| Q10 | 40 | 概念 | 排序表 |
| Q11 | 41 | 概念 | BFS/DFS |

- **手寫題（Q1-6）寫不出或編譯錯** → 對應章重看 + **反覆手寫到不看也對**。這是上機考/白板的核心，一定要練到肌肉記憶。
- **概念題（Q7-11）說不清** → 複雜度表（Q10）必背；BST 退化、hash 最壞、BFS/DFS 結構是高頻追問。

## 如果你卡住了

1. **linked list**：畫圖！三個指標 prev/curr/next，紙上畫箭頭怎麼改。先存 next。
2. **走訪**：base case 是 `!root`；前中後序只差 printf 位置。
3. **quicksort**：先寫 partition（i 是小區右界），再寫遞迴。
4. **binary search**：記三陷阱 `<=`、`lo+(hi-lo)/2`、±1。
5. **circular queue**：`%SIZE` 環繞、count 判滿空。
6. 手寫完**一定用 gcc 編譯跑測資**——手寫 code 最常 off-by-one。

## 自我檢核

- [ ] 不看答案，六題手寫題都能寫出且 gcc 編譯通過跑對測資
- [ ] linked list 反轉/找環、quicksort、binary search 練到肌肉記憶
- [ ] 我能默寫排序複雜度與穩定性表
- [ ] BST 退化、hash 最壞 O(n)、BFS/DFS 結構我都答得出
- [ ] array/list/tree/hash 的取捨我能說清楚「何時用哪個」

Part 5 驗收完成。最後 Part 6 進入軟性收尾——行為面試、一週衝刺計畫 cheat sheet，與模擬面試 final。

→ [Ch 42 行為面試與軟實力](./42-behavioral-interview.md)
