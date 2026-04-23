# Ch 6 — Linked List:考古題但還是會考

> 目標:把 linked list 該有的刀磨利——dummy head、快慢指針、in-place reverse。考題花樣不多,但手抖一個 pointer 就 null。

## Linked List 還考嗎?

考。雖然實務幾乎不用(cache locality 差、operations 的常數大),但它是面試「看你 pointer 手感」的工具。Microsoft / Amazon / Google 的 phone screen 常出。

**不要自己發明 node class**,按慣例:

```python
class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next
```

## 三大心法

### 心法 1:Dummy Head

幾乎所有修改 head 的題,**先建一個 dummy node 指向 head**。

```python
dummy = ListNode(0, head)
prev = dummy
# ... 處理 ...
return dummy.next
```

**為什麼**:省去「刪除 / 修改的節點恰好是 head」的特判。一行 dummy 換來少 10 行 if-else。

### 心法 2:快慢指針

```python
slow = fast = head
while fast and fast.next:
    slow = slow.next
    fast = fast.next.next
```

**fast 走兩步,slow 走一步**,fast 到底時 slow 在中點。

應用:
- 找中點(偶數長度時 slow 停在較右的中點;若要較左,改 loop 條件)
- 檢測環(Floyd's Cycle Detection)
- 找倒數第 k 個(fast 先走 k 步,再一起走)

### 心法 3:In-place Reverse

```python
def reverse_list(head):
    prev = None
    cur = head
    while cur:
        nxt = cur.next      # 1. 記住下一個
        cur.next = prev     # 2. 反轉
        prev = cur          # 3. prev 前進
        cur = nxt           # 4. cur 前進
    return prev
```

**四步節奏**:記下一、反轉、進 prev、進 cur。背熟這個順序,反轉題一輩子不用想。

---

## 經典題

### 1. Reverse Linked List (206)

上面已經寫了 iterative 版。遞迴版:

```python
def reverse_list(head):
    if not head or not head.next:
        return head
    new_head = reverse_list(head.next)
    head.next.next = head     # 反轉
    head.next = None          # 斷尾
    return new_head
```

遞迴版漂亮但有 O(n) stack 深度。面試寫 iterative 較安全。

### 2. Merge Two Sorted Lists (21)

```python
def merge(l1, l2):
    dummy = ListNode()
    tail = dummy
    while l1 and l2:
        if l1.val <= l2.val:
            tail.next = l1
            l1 = l1.next
        else:
            tail.next = l2
            l2 = l2.next
        tail = tail.next
    tail.next = l1 or l2    # 接上剩下的
    return dummy.next
```

`tail.next = l1 or l2` 是 Python idiom:返回第一個 truthy。

### 3. Linked List Cycle (141) / Cycle II (142)

檢測環:

```python
def has_cycle(head):
    slow = fast = head
    while fast and fast.next:
        slow = slow.next
        fast = fast.next.next
        if slow == fast:
            return True
    return False
```

找環的起點(Cycle II)用數學: 設 head 到環起點距離 a、環起點到相遇點距離 b、剩下環長 c。相遇時 slow 走 `a+b`、fast 走 `a+b+c+b`,fast = 2 * slow → `a + b + c + b = 2(a + b)` → `a = c`。

**所以相遇後,把 slow 放回 head,兩個一起一步走,再次相遇就是環起點**。

```python
def detect_cycle(head):
    slow = fast = head
    while fast and fast.next:
        slow, fast = slow.next, fast.next.next
        if slow == fast:
            slow = head
            while slow != fast:
                slow, fast = slow.next, fast.next
            return slow
    return None
```

**這個數學推導要會講**。面試官愛問「為什麼是這樣」。

### 4. Remove Nth From End (19)

快指針先走 n 步,然後一起走,快到底時慢指針在倒數第 n+1 個。

```python
def remove_nth_from_end(head, n):
    dummy = ListNode(0, head)
    fast = slow = dummy
    for _ in range(n):
        fast = fast.next
    while fast.next:
        fast, slow = fast.next, slow.next
    slow.next = slow.next.next
    return dummy.next
```

Dummy head 是救命——要刪的恰好是 head 時不用特判。

### 5. Reorder List (143)

> `L0 → L1 → … → Ln-1 → Ln` 變成 `L0 → Ln → L1 → Ln-1 → L2 → Ln-2 → …`

三步組合技:
1. 找中點(快慢指針)
2. 反轉後半
3. 交替合併

```python
def reorder_list(head):
    # 1. 找中點
    slow = fast = head
    while fast.next and fast.next.next:
        slow, fast = slow.next, fast.next.next
    second = slow.next
    slow.next = None

    # 2. 反轉後半
    prev = None
    cur = second
    while cur:
        nxt = cur.next
        cur.next = prev
        prev = cur
        cur = nxt
    second = prev

    # 3. 交替合併
    first = head
    while second:
        t1, t2 = first.next, second.next
        first.next = second
        second.next = t1
        first, second = t1, t2
```

能拆成這三個「獨立子程序」,就能寫得不慌。新手容易想把三件事合併在一個 loop,幾乎必爆 pointer。

### 6. Merge k Sorted Lists (23)

Heap 解:

```python
import heapq

def merge_k_lists(lists):
    h = []
    for i, node in enumerate(lists):
        if node:
            heapq.heappush(h, (node.val, i, node))   # i 是 tiebreaker,避免 val 相同時比 node
    dummy = ListNode()
    tail = dummy
    while h:
        val, i, node = heapq.heappop(h)
        tail.next = node
        tail = tail.next
        if node.next:
            heapq.heappush(h, (node.next.val, i, node.next))
    return dummy.next
```

**陷阱**:如果堆裡只放 `(val, node)`,當 val 相同時 Python 會比較 `ListNode`,沒定義 `__lt__` 會 TypeError。加一個 tiebreaker(index)就解決。

複雜度 O(N log k),N 是總節點數。

分治解也可(兩兩合併),複雜度相同。

### 7. Copy List with Random Pointer (138)

節點有 `next` 和 `random`,deep copy 整個 list。

**方法 A:hash map 兩遍**

```python
def copy_random_list(head):
    if not head: return None
    m = {}
    cur = head
    while cur:
        m[cur] = ListNode(cur.val)
        cur = cur.next
    cur = head
    while cur:
        m[cur].next = m.get(cur.next)
        m[cur].random = m.get(cur.random)
        cur = cur.next
    return m[head]
```

**方法 B:交錯連結(O(1) space)**

複製每個節點插入原 list,再拆出來。省空間但 code 多。onsite 能寫 A 就寫 A,被追問 O(1) space 再寫 B。

---

## 陷阱清單

### 陷阱 1:忘了更新 tail / prev

迴圈裡改 `cur.next = ...` 之後忘了 `cur = cur.next.next`,無限迴圈。

### 陷阱 2:處理 null 不當

```python
while cur and cur.next:   # 兩者都不能是 None
    ...
```

和

```python
while cur:                # 只處理 cur
    nxt = cur.next        # nxt 可能是 None 要接受
    ...
```

哪個對要看邏輯。寫之前先問:「這個 loop 退出時 cur 應該是 None 還是最後一個節點?」

### 陷阱 3:快指針判空

```python
# fast = fast.next.next 前必須確認兩個都非空
while fast and fast.next:
    fast = fast.next.next
```

`fast.next.next` 如果 `fast.next` 是 None,AttributeError。

### 陷阱 4:Dummy 不用時別加

純掃描(不修改)題不用 dummy。無腦加 dummy 會讓 code 看起來笨。

---

## Doubly Linked List

考得比 singly 少,但 LRU Cache (146) 必考 doubly + hash。Ch 9 相關的題才會觸及。

```python
class Node:
    def __init__(self, key=0, val=0):
        self.key, self.val = key, val
        self.prev = self.next = None
```

LRU 心法:hash map 存 `key → node`,doubly linked list 維持使用順序,`get` / `put` 都要把 node 移到 front。

---

## 自我檢核

- [ ] 為什麼要用 dummy head?寫一個不用 dummy 的 Remove Nth from End,看要多幾個 if。
- [ ] 快慢指針找中點,偶數長度時 slow 停在哪?(答:較右的中點,除非改 loop 條件)
- [ ] 反轉 linked list 的四步節奏:_____ / _____ / _____ / _____
- [ ] Merge k Sorted Lists 用 heap 時為什麼要放 tiebreaker?
- [ ] Cycle II 為什麼相遇後放 slow 回 head,兩個一起走就能找到環起點?

→ [Practice A — 線性結構綜合](./practice-a-linear.md)(先略過,繼續章節)

→ [Ch 7 Binary Tree:recursion 是唯一方法](./07-binary-tree.md)
