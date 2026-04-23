# Ch 7 — Binary Tree:recursion 是唯一方法

> 目標:建立「一個節點該做什麼、子樹的回傳值該是什麼」的思考框架,擺脫把 tree 題當流程圖寫的習慣。

## 病:把 tree 當流程控制

新手寫 tree 題愛這樣:

```python
def f(root):
    if not root: return ???
    # 先看看左子樹,找個 variable 存一下
    # 再看看右子樹,找個 variable 存一下
    # 然後 if 一大串
```

寫著寫著迷失在 index 和特判裡。

**正確的思考框架只問兩件事**:

1. **這個節點做什麼?**(我這層要處理什麼)
2. **我要子樹告訴我什麼?**(遞迴的回傳值是什麼)

把第二點想清楚,code 自然就寫對。

## 節點定義

```python
class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right
```

## 遍歷三式

```python
def inorder(root):      # 中序:左、根、右
    if not root: return
    inorder(root.left)
    print(root.val)
    inorder(root.right)

def preorder(root):     # 前序:根、左、右
    if not root: return
    print(root.val)
    preorder(root.left)
    preorder(root.right)

def postorder(root):    # 後序:左、右、根
    if not root: return
    postorder(root.left)
    postorder(root.right)
    print(root.val)
```

**三者的面試意義**:

- **Inorder**:對 BST 會輸出**升序**。出現在所有 BST 題。
- **Preorder**:「先處理自己,再交給子」——由上往下傳遞信息(如 path sum)。
- **Postorder**:「子算完我再算」——由下往上彙總(如子樹大小、子樹最值)。

大多數 tree 題屬於 postorder。

## Iterative 遍歷(偶爾考)

用 stack 模擬遞迴,**preorder 最直觀**:

```python
def preorder_iter(root):
    if not root: return []
    stack = [root]
    ans = []
    while stack:
        node = stack.pop()
        ans.append(node.val)
        if node.right: stack.append(node.right)   # 後進先出,先推右
        if node.left: stack.append(node.left)
    return ans
```

**Inorder iterative**(BST 的 k-th smallest 會用到):

```python
def inorder_iter(root):
    stack = []
    cur = root
    ans = []
    while stack or cur:
        while cur:
            stack.append(cur)
            cur = cur.left
        cur = stack.pop()
        ans.append(cur.val)
        cur = cur.right
    return ans
```

**Morris Traversal**(O(1) space)存在但面試很少考,遇到面試官硬問再說。

## 遞迴模板(postorder / divide & conquer)

```python
def f(node):
    if not node: return BASE_CASE
    left = f(node.left)
    right = f(node.right)
    return combine(node, left, right)
```

**三個設計決策**:
1. `BASE_CASE`:null 節點的回傳值。想清楚「空樹代表什麼」。
2. `combine`:怎麼用左右子樹的結果 + 自己的值,推出這層的結果。
3. 回傳類型:可能是單一值,也可能是 tuple(多個量一起算)。

---

## 經典題:postorder 威力展示

### Maximum Depth (104)

```python
def max_depth(root):
    if not root: return 0
    return 1 + max(max_depth(root.left), max_depth(root.right))
```

### Diameter of Binary Tree (543)

> 樹的直徑:最長路徑上的邊數,不一定經過 root。

```python
def diameter(root):
    best = 0
    def depth(node):
        nonlocal best
        if not node: return 0
        l = depth(node.left)
        r = depth(node.right)
        best = max(best, l + r)     # 以此節點為「頂」的路徑長
        return 1 + max(l, r)        # 回傳高度
    depth(root)
    return best
```

**關鍵心法**:遞迴回傳「高度」,但答案在遞迴過程中「順便」更新。很多 tree 題都是這個形狀:**回傳值 ≠ 答案**。

### Maximum Path Sum (124, Hard)

> 路徑可以從任意節點到任意節點,找路徑 sum 最大值。

```python
def max_path_sum(root):
    best = float('-inf')
    def gain(node):
        nonlocal best
        if not node: return 0
        l = max(gain(node.left), 0)   # 負數不要
        r = max(gain(node.right), 0)
        best = max(best, node.val + l + r)   # 以此為頂
        return node.val + max(l, r)          # 回傳「延伸到父的 gain」
    gain(root)
    return best
```

**關鍵**:`gain` 回傳「從該節點向下單邊延伸的最大 sum」(因為父節點只能接一邊),但答案用「兩邊都接 + 自己」更新。

### Lowest Common Ancestor (236)

> 找兩節點的最近共同祖先。樹不是 BST。

```python
def lowest_common_ancestor(root, p, q):
    if not root or root == p or root == q:
        return root
    l = lowest_common_ancestor(root.left, p, q)
    r = lowest_common_ancestor(root.right, p, q)
    if l and r: return root     # 兩邊各找到一個 → 當前就是 LCA
    return l or r
```

**優雅**:回傳值含義是「這棵子樹裡找到的 p 或 q 或 LCA」。分類討論:

- 左右都有返回 → 根是 LCA
- 只有一邊有 → 傳上去
- 都沒有 → None

這種「多義回傳值」是 tree 題常見技巧。

---

## BFS 在 tree 上(level order)

```python
from collections import deque

def level_order(root):
    if not root: return []
    q = deque([root])
    ans = []
    while q:
        level = []
        for _ in range(len(q)):     # 固定當前這層的節點數
            node = q.popleft()
            level.append(node.val)
            if node.left: q.append(node.left)
            if node.right: q.append(node.right)
        ans.append(level)
    return ans
```

**關鍵**:`for _ in range(len(q))` 鎖定當前 level 的節點數,用來分層。這招背下來。

經典題:
- Binary Tree Level Order Traversal (102)
- Zigzag Level Order (103):用 `deque` 交替插入方向。
- Right Side View (199):每 level 的最後一個。

---

## 序列化 / 反序列化 (297)

Tree 的「印出來能再建回來」。preorder + null 標記是標準解。

```python
def serialize(root):
    res = []
    def dfs(node):
        if not node:
            res.append("#")
            return
        res.append(str(node.val))
        dfs(node.left)
        dfs(node.right)
    dfs(root)
    return ",".join(res)

def deserialize(data):
    it = iter(data.split(","))
    def build():
        v = next(it)
        if v == "#": return None
        node = TreeNode(int(v))
        node.left = build()
        node.right = build()
        return node
    return build()
```

**為什麼 preorder**:先寫 root 讓 reader 知道當前位置是什麼。inorder 行不通(不知道哪個是 root)。

---

## 從遍歷重建樹 (105, 106)

> 給 preorder 和 inorder,建樹。

```python
def build_tree(preorder, inorder):
    idx_map = {v: i for i, v in enumerate(inorder)}
    pre_idx = [0]
    def build(l, r):
        if l > r: return None
        val = preorder[pre_idx[0]]
        pre_idx[0] += 1
        node = TreeNode(val)
        mid = idx_map[val]
        node.left = build(l, mid - 1)
        node.right = build(mid + 1, r)
        return node
    return build(0, len(inorder) - 1)
```

**心法**:preorder 第一個是 root,inorder 中 root 左邊是左子樹、右邊是右子樹。hash inorder 找位置,避免 O(n) 線性搜。

Postorder + inorder 類似,差別是 postorder 最後一個是 root、要從後往前消耗。

---

## Tree 的常見陷阱

### 陷阱 1:忘了 `not node: return BASE`

寫 tree 遞迴第一行就該是 null check。忘了 AttributeError。

### 陷阱 2:搞混「回傳」和「順便算答案」

很多題(Diameter、Max Path Sum)的答案不是遞迴的回傳值,而是在過程中更新的 `best`。搞清楚:**這個函數回傳的是什麼給父節點?答案又是怎麼被記錄?**

### 陷阱 3:不 balanced 的 recursion 深度

極度不平衡的樹(鏈狀)深度到 10^5,會爆 stack。必要時改 iterative 或調 `sys.setrecursionlimit`。

### 陷阱 4:Global 變數 vs nonlocal

Python 裡用 `nonlocal best` 才能從閉包修改外層變數:

```python
def solve(root):
    best = 0
    def dfs(node):
        nonlocal best    # ← 必要
        best = max(best, ...)
    dfs(root)
    return best
```

或用 list 技巧(不需要 nonlocal):

```python
best = [0]
best[0] = max(best[0], ...)
```

兩種都行,前者 cleaner。

---

## 自我檢核

- [ ] 寫 tree 遞迴,先問的兩個問題是什麼?
- [ ] Diameter 題:遞迴函數回傳的是什麼?答案存在哪?
- [ ] BFS level order 用什麼技巧區分「每一層」?
- [ ] 序列化為什麼要用 preorder 不能用 inorder?
- [ ] `nonlocal` 和 `global` 的差別?

→ [Ch 8 BST / Trie](./08-bst-trie.md)
