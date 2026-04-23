# Ch 8 — BST / Trie:兩個不同宇宙的樹

> 目標:BST 的 invariant 與利用方式、Trie 的典型應用場景。

## BST(Binary Search Tree)

定義:**對每個節點,左子樹所有值 < 它,右子樹所有值 > 它**。

**最重要的性質:inorder 遍歷產生升序序列**。幾乎所有 BST 題都從這一點出發。

### 為什麼這性質重要

任何 BST 題,**先問自己:能不能 inorder 掃一遍就解?** 常常可以。

### 經典題 1:Validate BST (98)

**新手的錯解**:

```python
# BAD:只檢查左 < 根 < 右,沒保證整個子樹
def is_valid_bst(root):
    if not root: return True
    if root.left and root.left.val >= root.val: return False
    if root.right and root.right.val <= root.val: return False
    return is_valid_bst(root.left) and is_valid_bst(root.right)
```

反例:`[5, 1, 6, None, None, 3, 7]`。根是 5,右子是 6,6 的左子是 3——「6 > 3」成立,但 3 < 5,整個樹不是 BST。上面的 code 會錯判成 True。

**正解:帶 min/max 範圍**:

```python
def is_valid_bst(root):
    def valid(node, lo, hi):
        if not node: return True
        if not (lo < node.val < hi): return False
        return valid(node.left, lo, node.val) and valid(node.right, node.val, hi)
    return valid(root, float('-inf'), float('inf'))
```

或用 inorder 檢查單調:

```python
def is_valid_bst(root):
    prev = [float('-inf')]
    def inorder(node):
        if not node: return True
        if not inorder(node.left): return False
        if node.val <= prev[0]: return False
        prev[0] = node.val
        return inorder(node.right)
    return inorder(root)
```

### 經典題 2:Kth Smallest in BST (230)

Inorder 遍歷到第 k 個即停。用 iterative 好寫。

```python
def kth_smallest(root, k):
    stack = []
    cur = root
    while stack or cur:
        while cur:
            stack.append(cur)
            cur = cur.left
        cur = stack.pop()
        k -= 1
        if k == 0: return cur.val
        cur = cur.right
```

### 經典題 3:LCA in BST (235)

BST 版比一般 tree 版容易太多:

```python
def lca_bst(root, p, q):
    while root:
        if p.val < root.val and q.val < root.val:
            root = root.left
        elif p.val > root.val and q.val > root.val:
            root = root.right
        else:
            return root
```

**關鍵**:一旦 p、q 在 root 兩側(或有一個等於 root),當前 root 就是 LCA。BST 的有序性讓我們可以二分下降,不需要遞迴兩邊子樹。

### 其他常見 BST 題

- Insert into BST (701)
- Delete Node in BST (450)——有三種 case(無子、一子、兩子),最難
- Recover BST (99)——inorder 找逆序對
- Convert Sorted Array to BST (108)——遞迴取中間當 root

### BST 的實務問題:不平衡會退化成鏈

Red-Black Tree、AVL 是自平衡 BST。面試**幾乎不會考實作**,但被問「為什麼 BST 查找不是保證 O(log n)?」要會答「不平衡」。

Python 沒內建平衡 BST,有 `sortedcontainers.SortedList`(底層是 sorted list of sorted lists,不是 BST 但介面類似)。

---

## Trie(前綴樹,Prefix Tree)

**字串集合的快速前綴查詢**。每個節點代表一個字元,根到某節點的路徑拼起來就是一個前綴。

### 實作

```python
class Trie:
    def __init__(self):
        self.children = {}
        self.is_word = False

    def insert(self, word):
        node = self
        for c in word:
            if c not in node.children:
                node.children[c] = Trie()
            node = node.children[c]
        node.is_word = True

    def search(self, word):
        node = self._find(word)
        return node is not None and node.is_word

    def starts_with(self, prefix):
        return self._find(prefix) is not None

    def _find(self, s):
        node = self
        for c in s:
            if c not in node.children: return None
            node = node.children[c]
        return node
```

**操作複雜度**:insert / search / prefix 都是 O(L),L 是字串長度。跟集合大小無關,這是 Trie 的威力。

### 何時用 Trie

訊號:

- 題目涉及**一組字串**(不是單一字串)
- 需要「前綴匹配」「字典查詢」「自動補全」
- 需要「掃一個長字串,看哪些字典字出現」

經典題:

- Implement Trie (208)——裸 Trie 實作
- Design Add and Search Words Data Structure (211)——支援 `.` 萬用字元,DFS + backtrack
- Word Search II (212, Hard)——在 grid 中找多個 word,Trie 做剪枝
- Longest Common Prefix (14)——Trie 可以,但直接比較更快
- Replace Words (648)

### Word Search II 的 Trie 威力

> 給一個 char grid 和一組 words,找 grid 中能拼出哪些 word。

樸素解:對每個 word 做一次 grid search,O(W × cells × 4^L)。

Trie 解:把所有 words 塞進一個 trie,然後對 grid 做一次 DFS,沿途檢查是否匹配 trie 的某條路徑。**一次 DFS 解決所有 words**。

```python
def find_words(board, words):
    root = {}
    for w in words:
        node = root
        for c in w:
            node = node.setdefault(c, {})
        node['#'] = w   # 終止標記,存完整 word

    rows, cols = len(board), len(board[0])
    ans = []

    def dfs(r, c, node):
        ch = board[r][c]
        nxt = node.get(ch)
        if not nxt: return
        if '#' in nxt:
            ans.append(nxt.pop('#'))   # 找到就移除,避免重複
        board[r][c] = '#'
        for dr, dc in [(0,1),(1,0),(0,-1),(-1,0)]:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and board[nr][nc] != '#':
                dfs(nr, nc, nxt)
        board[r][c] = ch

    for r in range(rows):
        for c in range(cols):
            dfs(r, c, root)
    return ans
```

**技巧**:
- Trie 用 nested dict,不用寫 class。
- `'#'` 當 sentinel 標記完整 word。
- `nxt.pop('#')` 找到就移除,避免 `["a", "a"]` 重複答案(並順便剪枝 trie)。
- `board[r][c] = '#'` 占位防止繞回,完再還原。

---

## 用數字 Trie 解 XOR 題

Trie 不只用於字串。**二進位 Trie**(每個節點兩個 children 對應 0/1)可以做:

- Maximum XOR of Two Numbers (421)
- 範圍最大 XOR 查詢

```python
def find_max_xor(nums):
    root = {}
    for n in nums:     # 先建 trie
        node = root
        for i in range(31, -1, -1):
            b = (n >> i) & 1
            node = node.setdefault(b, {})

    best = 0
    for n in nums:
        node = root
        x = 0
        for i in range(31, -1, -1):
            b = (n >> i) & 1
            toggle = 1 - b
            if toggle in node:
                x |= (1 << i)
                node = node[toggle]
            else:
                node = node[b]
        best = max(best, x)
    return best
```

**心法**:XOR 要最大就每一位都希望不同。Trie 儲存所有數的 bit-prefix,貪婪選「跟自己不同」的分支。

---

## BST vs Trie 不同宇宙

| | BST | Trie |
|---|---|---|
| 節點存什麼 | 一個值 | 一個字元/bit |
| 搜尋 | 比大小走左右 | 按字元走對應 child |
| 空間 | O(n) | O(總字元數) |
| 時間 | O(log n) 平均,O(n) 最壞 | O(L) 絕對 |
| 典型場景 | 有序數值集 | 字串集合、前綴匹配 |

**不要搞混**。被問「前綴查詢」說用 BST 是錯的,會不夠快。

---

## 自我檢核

- [ ] BST 的 inorder 有什麼性質?怎麼用它檢查 BST?
- [ ] Validate BST 為什麼不能只比「父 vs 左 / 右子」?
- [ ] Kth smallest 的 O(H + k) 解怎麼寫(H 是高度)?
- [ ] Trie 的搜尋時間跟集合大小無關,為什麼?
- [ ] 寫一個 Trie 的 `starts_with(prefix)`。

→ [Ch 9 Heap / Priority Queue](./09-heap.md)
