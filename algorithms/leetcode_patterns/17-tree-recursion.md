# Ch 17 — Recursion on Tree：分治思維

> 目標：建立「問左子樹要答案、問右子樹要答案、合併」的分治框架，套用到各種 Tree 題。

## 分治思維

樹的遞迴幾乎都是同一個框架：

```
solve(root):
  1. base case：root 為空，回傳什麼？
  2. 從左子樹拿到答案：left = solve(root->left)
  3. 從右子樹拿到答案：right = solve(root->right)
  4. 用 root->val、left、right 計算當前節點的答案
  5. 回傳
```

**重點**：不要試圖追蹤整棵樹。定義好「這個函式回傳什麼」，然後信任它在子樹上也是正確的。

## 範例 1：最大深度（LeetCode 104）

函式定義：`maxDepth(node)` = 以 `node` 為根的子樹的最大深度。

```
base case：node 為空 → 深度為 0
合併：max(左子樹深度, 右子樹深度) + 1（+1 是當前節點這一層）
```

```cpp
int maxDepth(TreeNode* root) {
    if (!root) return 0;
    return max(maxDepth(root->left), maxDepth(root->right)) + 1;
}
```

三行解決。

## 範例 2：對稱樹（LeetCode 101）

判斷樹是否鏡像對稱。

函式定義：`isMirror(l, r)` = 以 l 和 r 為根的兩棵子樹是否互為鏡像。

```
base case：l 和 r 都為空 → 對稱（true）
base case：l 或 r 其中一個為空 → 不對稱（false）
當前層：l->val == r->val（根節點值相同）
遞迴：l 的左子樹 vs r 的右子樹（外側），l 的右子樹 vs r 的左子樹（內側）
```

```cpp
bool isSymmetric(TreeNode* root) {
    function<bool(TreeNode*, TreeNode*)> mirror = [&](TreeNode* l, TreeNode* r) {
        if (!l && !r) return true;
        if (!l || !r) return false;
        return l->val == r->val
            && mirror(l->left, r->right)
            && mirror(l->right, r->left);
    };
    return mirror(root->left, root->right);
}
```

## 範例 3：樹的直徑（LeetCode 543）

樹的直徑 = 任意兩節點路徑的最大長度（以邊數計算）。

**關鍵觀察**：最長路徑一定經過某個節點作為「轉折點」，這條路徑 = 該節點左子樹深度 + 右子樹深度。

```cpp
int diameterOfBinaryTree(TreeNode* root) {
    int maxDiam = 0;

    function<int(TreeNode*)> depth = [&](TreeNode* node) -> int {
        if (!node) return 0;
        int l = depth(node->left);
        int r = depth(node->right);
        maxDiam = max(maxDiam, l + r);  // 以當前節點為轉折點的直徑
        return max(l, r) + 1;           // 回傳深度給父節點用
    };

    depth(root);
    return maxDiam;
}
```

這是樹遞迴中很重要的模式：**函式回傳值給父節點用，同時用全域變數記錄最佳答案**。

## 何時用全域變數，何時直接回傳？

- 若答案**不一定在根節點**（如直徑可能在任意子樹），用全域變數收集
- 若答案**在根節點就能決定**（如最大深度），直接回傳

## 範例 4：相同的樹（LeetCode 100）

```cpp
bool isSameTree(TreeNode* p, TreeNode* q) {
    if (!p && !q) return true;
    if (!p || !q) return false;
    return p->val == q->val
        && isSameTree(p->left, q->left)
        && isSameTree(p->right, q->right);
}
```

## 分治框架的通用模板

```cpp
ReturnType solve(TreeNode* root) {
    // 1. base case
    if (!root) return /* 空樹的答案 */;

    // 2. 分治
    auto leftAns  = solve(root->left);
    auto rightAns = solve(root->right);

    // 3. 合併（用 root->val、leftAns、rightAns）
    return /* 合併結果 */;
}
```

## 自我檢核

- [ ] 能用分治框架說出最大深度的思路（不是背 code，是說思路）
- [ ] 能解釋樹的直徑為什麼需要全域變數
- [ ] 知道什麼情況下直接回傳，什麼情況下用全域變數
- [ ] 能寫出對稱樹的遞迴（注意左右子樹比較的方向）

→ [Ch 18 路徑問題 + LCA](./18-tree-path-lca.md)
