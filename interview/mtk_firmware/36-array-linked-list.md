# Ch 36 — array / linked list

> **目標**：搞懂 array 和 linked list 的取捨，並能手寫 linked list 的經典操作——反轉、找中點、找環（Floyd）、合併。linked list 是面試最高頻的手寫題（C 上機考也常考）。

> **環境**：C，`gcc -Wall`。前置：Ch 4（指標）、Ch 11（記憶體）。

## 為什麼考這個

linked list 是「指標操作」的試金石——反轉、找環這些題目，測你能不能正確操作指標、處理邊界（空、單節點）。MTK 上機考和技術面都高頻考。array vs linked list 的取捨也是常考概念題。

## array vs linked list（概念對比）

```
   array（陣列）：連續記憶體
   [0][1][2][3][4]   ← 一塊連續

   linked list（鏈結串列）：節點 + 指標串起
   [data|next]→[data|next]→[data|NULL]   ← 分散，靠 next 串
```

| | array | linked list |
|---|---|---|
| 記憶體 | 連續 | 分散（節點各自配置）|
| 隨機存取 | **O(1)**（`arr[i]` 直接算位址）| O(n)（要從頭走）|
| 插入/刪除（中間）| O(n)（要搬移後面元素）| **O(1)**（改指標，若已有位置）|
| 大小 | 固定（或要重配）| 動態（隨時加節點）|
| 記憶體額外開銷 | 無 | 每節點多一個指標 |
| cache 友善 | **好**（連續，spatial locality，Ch 30）| 差（分散，cache miss 多）|

選擇：
- **array**：要隨機存取、大小固定、cache 效能（連續記憶體，Ch 30）。
- **linked list**：頻繁中間插入/刪除、大小動態。

> 韌體角度：linked list 每節點要 malloc（碎片、不可預測，Ch 11/19），嵌入式有時偏好 array 或靜態配置的節點池。cache 也偏好 array（連續）。

## linked list 節點定義

```c
typedef struct Node {
    int data;
    struct Node *next;
} Node;
```

singly linked list（單向）：每節點指向下一個。doubly linked list（雙向）多一個 `prev`。面試多考 singly。

## 經典手寫題（必練）

### 反轉 linked list（最高頻）

```c
Node *reverse(Node *head) {
    Node *prev = NULL, *curr = head;
    while (curr != NULL) {
        Node *next = curr->next;   // 先存下一個（不然改了 next 就找不到）
        curr->next = prev;         // 反轉指標
        prev = curr;               // prev 前進
        curr = next;               // curr 前進
    }
    return prev;                   // prev 是新的 head（原本的尾）
}
```

關鍵：用三個指標 prev/curr/next，逐個把 `curr->next` 指向 prev。**順序重要**——先存 next（否則 `curr->next = prev` 後就找不到原本的下一個）。回傳 prev（迴圈結束時 prev 指向原本的最後一個 = 新 head）。

遞迴版（也常考）：

```c
Node *reverse_recursive(Node *head) {
    if (head == NULL || head->next == NULL) return head;  // base
    Node *new_head = reverse_recursive(head->next);
    head->next->next = head;       // 後一個指回自己
    head->next = NULL;             // 自己變尾
    return new_head;
}
```

### 找中點（快慢指標 / Floyd）

```c
Node *find_middle(Node *head) {
    Node *slow = head, *fast = head;
    while (fast != NULL && fast->next != NULL) {
        slow = slow->next;         // 慢的走 1 步
        fast = fast->next->next;   // 快的走 2 步
    }
    return slow;                   // 快的到尾時，慢的剛好在中間
}
```

**快慢指標**：fast 走 2 步、slow 走 1 步——fast 到尾時 slow 在中間（走了一半）。這個技巧也用於找環（下面）。注意 fast 的邊界條件 `fast != NULL && fast->next != NULL`（防解 NULL）。

### 偵測環（Floyd's cycle detection，龜兔賽跑）

```c
int has_cycle(Node *head) {
    Node *slow = head, *fast = head;
    while (fast != NULL && fast->next != NULL) {
        slow = slow->next;         // 龜走 1 步
        fast = fast->next->next;   // 兔走 2 步
        if (slow == fast) return 1;// 相遇 → 有環！
    }
    return 0;                       // fast 到 NULL → 無環
}
```

原理：若有環，快指標會在環裡「追上」慢指標（相遇）；無環則 fast 先到 NULL。這是練習 B/C 的 list 偵測延伸（Ch practice）。

### 合併兩個排序好的 list

```c
Node *merge(Node *a, Node *b) {
    Node dummy;                    // 虛擬頭節點，簡化邊界處理
    Node *tail = &dummy;
    dummy.next = NULL;
    while (a != NULL && b != NULL) {
        if (a->data <= b->data) { tail->next = a; a = a->next; }
        else                    { tail->next = b; b = b->next; }
        tail = tail->next;
    }
    tail->next = (a != NULL) ? a : b;   // 接上剩下的
    return dummy.next;
}
```

技巧：**dummy 節點（虛擬頭）** 簡化「第一個節點」的邊界處理——不用特判 head。這是 linked list 題的常用技巧。

## 考古題詳解

### Q1：手寫反轉 singly linked list（迭代）

<details>
<summary>詳解</summary>

```c
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

三指標 prev/curr/next，**先存 next 再改 curr->next**（否則丟失後續）。回傳 prev（新 head）。處理空 list（head=NULL → prev=NULL 直接回）。

**考點**：反轉 list，最高頻手寫題。
</details>

### Q2：怎麼找 linked list 的中點？

<details>
<summary>詳解</summary>

**快慢指標**：slow 走 1 步、fast 走 2 步，fast 到尾時 slow 在中間。

```c
Node *slow = head, *fast = head;
while (fast && fast->next) { slow = slow->next; fast = fast->next->next; }
return slow;
```

關鍵：邊界 `fast && fast->next`（防解 NULL）。一次走訪 O(n)、O(1) 空間（不用先數長度再走一半）。

**考點**：快慢指標找中點。
</details>

### Q3：怎麼判斷 linked list 有沒有環？

<details>
<summary>詳解</summary>

**Floyd 龜兔賽跑**：slow 走 1、fast 走 2，有環則 fast 會追上 slow（相遇）；無環則 fast 先到 NULL。

```c
Node *slow = head, *fast = head;
while (fast && fast->next) {
    slow = slow->next; fast = fast->next->next;
    if (slow == fast) return 1;  // 有環
}
return 0;
```

O(n) 時間、O(1) 空間（比「用 hash 記錄走過的節點」省空間）。

**考點**：Floyd 找環，高頻。
</details>

### Q4：array 和 linked list 各的優缺點？什麼時候用哪個？

<details>
<summary>詳解</summary>

- **array**：隨機存取 O(1)、cache 友善（連續）、無額外指標開銷；但中間插刪 O(n)、大小固定。
- **linked list**：中間插刪 O(1)（已有位置）、大小動態；但隨機存取 O(n)、每節點多指標、cache 差（分散）。

用 array：隨機存取、大小固定、要 cache 效能。用 linked list：頻繁中間插刪、動態大小。

韌體常偏 array（cache + 避免 malloc 碎片，Ch 11/19）。

**考點**：array vs linked list 取捨，必考。
</details>

### Q5：dummy 節點有什麼用？

<details>
<summary>詳解</summary>

dummy（虛擬頭）節點簡化「頭節點」的邊界處理——很多 list 操作（合併、插入、刪除）對「第一個節點」要特判（因為它沒有前驅）。用一個 dummy 接在真正 head 前面，所有節點就都有「前驅」，不用特判 head，code 更簡潔。最後回傳 `dummy.next`。

**考點**：dummy 節點技巧，展現經驗。
</details>

## 踩雷集錦

1. **反轉 list 沒先存 next**：`curr->next = prev` 之後就找不到原本的下一個。要先 `next = curr->next`。
2. **快慢指標邊界錯**：`while(fast && fast->next)`——少一個條件會解 NULL（crash）。
3. **沒處理空 list / 單節點**：head=NULL 或只有一個節點要正確處理（反轉空 list 回 NULL）。
4. **找環用 hash 記節點**：可行但 O(n) 空間。Floyd 龜兔 O(1) 空間更好。
5. **array 中間插入忘了搬移成本**：array 中間插入要搬後面所有元素（O(n)），不是 O(1)。
6. **linked list 隨機存取以為 O(1)**：要從頭走 O(n)，不像 array 能直接算位址。
7. **malloc 節點忘了 free**：linked list 用完要走訪 free 每個節點（leak，Ch 11）。

## 速記

- **array**：連續、隨機存取 O(1)、cache 友善、中間插刪 O(n)、固定大小。
- **linked list**：分散、隨機存取 O(n)、中間插刪 O(1)、動態、cache 差、每節點多指標。
- **反轉**：三指標 prev/curr/next，**先存 next 再改 curr->next**，回傳 prev。
- **找中點 / 找環**：**快慢指標**（slow 1 步、fast 2 步）；找環 Floyd（相遇=有環），O(1) 空間。
- **dummy 節點**簡化頭節點邊界。
- 邊界：空 list、單節點、`while(fast && fast->next)` 防解 NULL。

## 自我檢核

- [ ] 不看，能手寫迭代反轉 linked list 嗎？為什麼要先存 next？
- [ ] 怎麼用快慢指標找中點？邊界條件是什麼？
- [ ] 怎麼用 Floyd 判斷有沒有環？為什麼比 hash 好（空間）？
- [ ] array 和 linked list 各的時間複雜度（隨機存取、中間插刪）？何時用哪個？
- [ ] dummy 節點解決什麼問題？

## 延伸閱讀

### 書籍

- **《Introduction to Algorithms (CLRS)》** — Ch 10 Elementary Data Structures
  - **讀哪幾章**：10.2（linked list）。
  - **和本章的關聯**：linked list 的標準教材。

### 文章

- **[發哥(聯發科)上機考題目整理 — HackMD](https://hackmd.io/@Rance/SkSJL_5gX)**
  - **讀哪裡**：linked list 反轉/找中點題（Ch 18-19 of that doc）。
  - **和本章的關聯**：MTK 上機考的 linked list 題源頭。

linked list 練熟，下一章是兩個基礎結構——stack 和 queue。

→ [Ch 37 stack / queue](./37-stack-queue.md)
