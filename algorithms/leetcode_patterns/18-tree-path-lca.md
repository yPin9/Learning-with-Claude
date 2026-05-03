# Ch 18 — 路徑問題 + LCA

> 目標：掌握「根到葉的路徑」和「任意兩點路徑」的處理方式，以及 LCA 的兩種解法。

## 路徑問題的兩種類型

**類型 1：根到葉的路徑**（從上往下，不回頭）
- 路徑總和是否等於 target？
- 列出所有根到葉的路徑

**類型 2：任意兩點的路徑**（可以在某個節點「轉彎」）
- 路徑最大總和（含負數）
- 樹的直徑（Ch 17 的 diameter）

類型 1 用 DFS 帶著「剩餘目標」往下走就好。
類型 2 需要在每個節點計算「以此為轉折點的最大值」，用全域變數記錄。

## 類型 1：Path Sum（LeetCode 112）

判斷是否有根到葉的路徑，總和等於 target。

```cpp
bool hasPathSum(TreeNode* root, int target) {
    if (!root) return false;
    if (!root->left && !root->right)  // 葉節點
        return root->val == target;
    return hasPathSum(root->left, target - root->val)
        || hasPathSum(root->right, target - root->val);
}
```

## 類型 1：路徑總和 II（LeetCode 113）

列出所有總和等於 target 的根到葉路徑。

用 backtracking：走到葉節點時記錄，回來時 pop。

```cpp
vector<vector<int>> pathSum(TreeNode* root, int target) {
    vector<vector<int>> result;
    vector<int> path;

    function<void(TreeNode*, int)> dfs = [&](TreeNode* node, int remain) {
        if (!node) return;
        path.push_back(node->val);

        if (!node->left && !node->right && remain == node->val)
            result.push_back(path);

        dfs(node->left,  remain - node->val);
        dfs(node->right, remain - node->val);
        path.pop_back();  // 回溯：移除當前節點
    };

    dfs(root, target);
    return result;
}
```

## 類型 2：Binary Tree Maximum Path Sum（LeetCode 124）

路徑可以從任意節點到任意節點（不必經過根），找路徑最大總和。

函式設計：`gain(node)` 回傳「以 node 為端點，向下延伸的最大增益」。

注意：如果子樹的增益是負數，不如不走（回傳 0）。

```cpp
int maxPathSum(TreeNode* root) {
    int maxSum = INT_MIN;

    function<int(TreeNode*)> gain = [&](TreeNode* node) -> int {
        if (!node) return 0;
        int leftGain  = max(gain(node->left),  0);  // 負增益不取
        int rightGain = max(gain(node->right), 0);
        maxSum = max(maxSum, node->val + leftGain + rightGain);  // 以此節點為轉折
        return node->val + max(leftGain, rightGain);  // 只能選一側延伸
    };

    gain(root);
    return maxSum;
}
```

## Lowest Common Ancestor（LCA）

**題目（LeetCode 236）**：找兩個節點 p, q 的最近公共祖先。

**思路**：對每個節點，如果左子樹找到 p 或 q，右子樹也找到 p 或 q，那這個節點就是 LCA。

```cpp
TreeNode* lowestCommonAncestor(TreeNode* root, TreeNode* p, TreeNode* q) {
    if (!root || root == p || root == q) return root;

    TreeNode* left  = lowestCommonAncestor(root->left,  p, q);
    TreeNode* right = lowestCommonAncestor(root->right, p, q);

    if (left && right) return root;   // 左右各找到一個 → root 是 LCA
    return left ? left : right;       // 只在一側找到 → 返回那側的結果
}
```

**函式語意**：`LCA(root, p, q)` 回傳「在以 root 為根的子樹中，p 或 q 的最淺祖先」。若找不到，回傳 nullptr。

## LCA on BST（LeetCode 235）

BST 有序，LCA 更簡單：

```cpp
TreeNode* lowestCommonAncestor(TreeNode* root, TreeNode* p, TreeNode* q) {
    if (p->val < root->val && q->val < root->val)
        return lowestCommonAncestor(root->left, p, q);
    if (p->val > root->val && q->val > root->val)
        return lowestCommonAncestor(root->right, p, q);
    return root;  // p 和 q 在 root 兩側（或其中一個就是 root）
}
```

## 自我檢核

- [ ] 能寫出 Path Sum（根到葉）的遞迴
- [ ] 能解釋 Maximum Path Sum 為什麼用 `max(gain, 0)` 截斷負增益
- [ ] 能說出 LCA 遞迴的函式語意（而不是死背 code）
- [ ] 知道 BST 的 LCA 比一般樹更簡單（利用大小關係）

→ [練習 C：Tree 綜合](./practice-c-tree.md)
