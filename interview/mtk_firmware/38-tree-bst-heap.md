# Ch 38 — tree / BST / heap

> **目標**：搞懂二元樹走訪（前/中/後序 + 層序）、BST（二元搜尋樹）的搜尋/插入/刪除與時間複雜度、heap（堆積）的性質與應用。樹是面試的高頻概念題與手寫題。

> **環境**：C，`gcc -Wall`。前置：Ch 36（指標/節點）、Ch 37（stack/queue 用於走訪）。

## 為什麼考這個

樹是「有層次的資料」的基礎結構——檔案系統、語法樹、索引都是樹。面試會問「中序走訪寫一下」「BST 的時間複雜度」「heap 怎麼實作 priority queue」。樹的走訪也是遞迴的最佳練習（Ch 41 也會用）。

## 先建立直覺

```
        二元樹（binary tree）：每個節點最多兩個小孩

              10          ← root（根）
             /  \
            5    15       ← 5、15 是 10 的小孩
           / \     \
          3   7     20    ← 葉節點（leaf，無小孩）
```

名詞：root（根，最上）、leaf（葉，無子）、parent/child、height（高度，root 到最深 leaf 的邊數）、depth（深度，root 到該節點）。

```c
typedef struct TreeNode {
    int val;
    struct TreeNode *left, *right;
} TreeNode;
```

## 走訪（traversal，必考手寫）

四種走訪。前三種是 DFS（深度優先，用遞迴/stack），最後一種是 BFS（廣度優先，用 queue）。

**差別只在「什麼時候處理根節點」**：

```c
// 前序 preorder：根 → 左 → 右
void preorder(TreeNode *root) {
    if (!root) return;
    printf("%d ", root->val);   // 先處理根
    preorder(root->left);
    preorder(root->right);
}
// 中序 inorder：左 → 根 → 右
void inorder(TreeNode *root) {
    if (!root) return;
    inorder(root->left);
    printf("%d ", root->val);   // 中間處理根
    inorder(root->right);
}
// 後序 postorder：左 → 右 → 根
void postorder(TreeNode *root) {
    if (!root) return;
    postorder(root->left);
    postorder(root->right);
    printf("%d ", root->val);   // 最後處理根
}
```

以上面那棵樹為例：
- 前序：`10 5 3 7 15 20`
- 中序：`3 5 7 10 15 20`（**BST 的中序 = 由小到大排序！** 重要性質）
- 後序：`3 7 5 20 15 10`（後序常用於「先處理子節點再處理自己」——如刪除整棵樹、算目錄大小）

**層序走訪（level order / BFS）**：一層一層，用 queue：

```c
void levelorder(TreeNode *root) {
    if (!root) return;
    TreeNode *queue[1000]; int front = 0, rear = 0;
    queue[rear++] = root;
    while (front < rear) {
        TreeNode *node = queue[front++];   // dequeue
        printf("%d ", node->val);
        if (node->left)  queue[rear++] = node->left;   // enqueue 小孩
        if (node->right) queue[rear++] = node->right;
    }
}
```

層序：`10 5 15 3 7 20`。**DFS 用 stack（或遞迴）、BFS 用 queue**（Ch 37 的應用！）。

## BST（二元搜尋樹）

**BST 性質**：每個節點，左子樹全部 < 它、右子樹全部 > 它。所以**中序走訪 = 由小到大排序**。

搜尋（像二分搜，O(樹高)）：

```c
TreeNode *search(TreeNode *root, int key) {
    while (root && root->val != key)
        root = (key < root->val) ? root->left : root->right;  // 比根小往左、大往右
    return root;
}
```

插入：往下找到空位掛上去（同搜尋路徑）。

**時間複雜度的關鍵**：
- **平衡時**：樹高 O(log n)，搜尋/插入/刪除都 O(log n)。
- **退化時**（最壞）：插入已排序資料 → 變成一條鏈（每個只有右小孩）→ 樹高 O(n)，全部退化成 **O(n)**！

```
插入 1,2,3,4,5（已排序）→ 退化成鏈：
   1
    \
     2
      \
       3      ← 樹高 O(n)，搜尋變 O(n)（跟 linked list 一樣慢）
        \
         4...
```

這就是為什麼有**平衡 BST**（AVL、紅黑樹）——自動旋轉維持 O(log n)。面試知道「BST 會退化、平衡樹解決它」即可，不用手寫紅黑樹。

| 操作 | BST 平均 | BST 最壞（退化）| 平衡 BST |
|---|---|---|---|
| 搜尋 | O(log n) | O(n) | O(log n) |
| 插入 | O(log n) | O(n) | O(log n) |
| 刪除 | O(log n) | O(n) | O(log n) |

## heap（堆積）

**heap 是完全二元樹（complete binary tree）**，滿足 heap 性質：
- **max-heap**：每個節點 ≥ 它的小孩 → root 是最大值。
- **min-heap**：每個節點 ≤ 它的小孩 → root 是最小值。

heap **不是** BST！heap 只保證父子關係（父≥子或父≤子），左右子節點之間沒有順序。

heap 用 **array 實作**（完全二元樹可以緊密放進 array，不用指標）：

```
   array: [_, 50, 30, 40, 10, 20]   (索引 0 不用，從 1 開始)
   節點 i：左小孩 2i、右小孩 2i+1、父 i/2

           50(1)
          /    \
        30(2)  40(3)
        /  \
     10(4) 20(5)
```

操作：
- **insert**：放到陣列尾，向上「上浮」（與父比較交換）——O(log n)。
- **extract-max/min**：取 root，把尾元素移到 root，向下「下沉」——O(log n)。
- **peek**：看 root，O(1)。

heap 的應用（必考）：
- **priority queue（優先佇列）**：取最高優先（OS 排程 Ch 21、Dijkstra）。
- **heap sort**（Ch 40）：建 heap + 反覆 extract，O(n log n)。
- **找 top-K**：用大小 K 的 heap，O(n log K)。

## 考古題詳解

### Q1：二元樹的前/中/後序走訪差在哪？BST 哪種走訪會排序？

<details>
<summary>詳解</summary>

差別在「何時處理根」：前序（根左右）、中序（左根右）、後序（左右根）。

**BST 的中序走訪 = 由小到大排序**（因為左<根<右，先左再根再右剛好遞增）。

前序常用於複製樹、後序用於刪除樹/算大小（先處理子再處理自己）。

**考點**：走訪順序 + BST 中序排序性質，必考。
</details>

### Q2：手寫中序走訪

<details>
<summary>詳解</summary>

```c
void inorder(TreeNode *root) {
    if (!root) return;       // base case
    inorder(root->left);     // 左
    printf("%d ", root->val);// 根
    inorder(root->right);    // 右
}
```

遞迴最簡潔。base case 是 `root==NULL`。也可用 stack 寫迭代版（進階）。

**考點**：手寫走訪，最高頻。
</details>

### Q3：BST 的搜尋時間複雜度？什麼時候會退化？

<details>
<summary>詳解</summary>

平均 O(log n)（樹高 log n）。**退化**：插入已排序資料時，BST 變成一條鏈（樹高 O(n)），搜尋退化成 **O(n)**（跟 linked list 一樣）。

解決：平衡 BST（AVL、紅黑樹）自動旋轉維持 O(log n)。

**考點**：BST 退化問題，必考。
</details>

### Q4：heap 和 BST 差在哪？heap 怎麼實作？

<details>
<summary>詳解</summary>

- **BST**：左<根<右（全序），中序可排序，用指標節點。
- **heap**：完全二元樹，父≥子（max）或父≤子（min），**左右子之間無序**，root 是最值，用 **array 實作**（i 的左 2i、右 2i+1、父 i/2）。

heap 不能快速搜尋任意值（只快速取最值 O(1) peek、O(log n) extract）。

**考點**：heap vs BST，必考混淆點。
</details>

### Q5：要找一堆數字裡最大的 K 個，怎麼做？

<details>
<summary>詳解</summary>

用大小 K 的 **min-heap**：走訪所有數字，維持 heap 只留 K 個——若新數 > heap 頂（目前 K 個裡最小），就 extract 頂、insert 新數。最後 heap 裡就是最大的 K 個。

時間 O(n log K)，空間 O(K)。比「全部排序 O(n log n) 再取前 K」更省（當 K << n）。

```
為什麼 min-heap 找「最大」K 個？因為頂是這 K 個裡的最小，
新數只要比它大就值得進來、把最小的踢掉。
```

**考點**：top-K 問題（heap 經典應用），高頻。
</details>

## 踩雷集錦

1. **heap 當成 BST**：heap 左右子無序，不能像 BST 那樣二分搜尋任意值。heap 只快速取最值。
2. **BST 以為一定 O(log n)**：退化（插入排序資料）變 O(n)。要平衡 BST 才保證 O(log n)。
3. **走訪 base case 漏了**：`if(!root) return;`——少了會解 NULL（crash）。
4. **前/中/後序記混**：看「根」的位置（前=根在前、中=根在中、後=根在後），左右永遠左先右後。
5. **層序走訪用 stack**：層序（BFS）要用 **queue**，用 stack 會變成 DFS。
6. **heap array 索引搞錯**：從 1 開始時 i 的左 2i、右 2i+1、父 i/2；從 0 開始則左 2i+1、右 2i+2、父 (i-1)/2。
7. **以為 heap 能排序就是排好的 array**：heap array 不是完全排序的，只滿足父子關係。

## 速記

- **走訪**：前序（根左右）、中序（左根右）、後序（左右根）；**BST 中序 = 由小到大排序**。前中後 = DFS（遞迴/stack）；**層序 = BFS（queue）**。
- **BST**：左<根<右；搜尋/插入/刪除平均 O(log n)，**退化（排序資料）成 O(n)**；平衡 BST（AVL/紅黑樹）保證 O(log n)。
- **heap**：完全二元樹，max（父≥子，root 最大）/ min（父≤子，root 最小），**左右子無序**，array 實作（i 左 2i、右 2i+1、父 i/2），insert/extract O(log n)、peek O(1)。
- heap 應用：priority queue、heap sort、top-K（min-heap 找最大 K 個，O(n log K)）。
- heap ≠ BST：heap 取最值快、不能搜任意值；BST 可搜任意值、可排序。

## 自我檢核

- [ ] 不看，能手寫前/中/後序走訪嗎？三者差在哪？
- [ ] 為什麼 BST 的中序走訪會得到排序結果？
- [ ] BST 什麼時候退化成 O(n)？怎麼解決？
- [ ] heap 和 BST 的差別？heap 為什麼能用 array 實作？
- [ ] 找最大的 K 個數，為什麼用 min-heap？複雜度多少？

## 延伸閱讀

### 書籍

- **《Introduction to Algorithms (CLRS)》** — Ch 12（BST）、Ch 6（Heapsort）
  - **讀哪幾章**：12.1–12.3（BST 操作）、6.1–6.4（heap）。
  - **和本章的關聯**：BST 與 heap 的標準教材，含正確性證明。

### 文章

- **[GeeksforGeeks — Binary Tree / BST / Heap](https://www.geeksforgeeks.org/binary-tree-data-structure/)**
  - **讀哪裡**：走訪、BST 操作、heap 操作各篇。
  - **和本章的關聯**：附動畫與多語言實作，補強手寫細節。

樹是非線性結構的開始，下一章看另一個高頻結構——hash table（雜湊表），O(1) 查找的魔法。

→ [Ch 39 hash table](./39-hash-table.md)