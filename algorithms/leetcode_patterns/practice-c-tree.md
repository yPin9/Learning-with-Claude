# 練習 C — Tree 綜合

> 目標：不依賴模板，能對「任意 Tree 題」判斷用 DFS 還是 BFS，並正確實作遞迴的函式語意。

**寫完再看！**

---

## 題目一：Balanced Binary Tree（LeetCode 110）

**題目規格**

判斷二元樹是否是高度平衡樹（Height-Balanced）。高度平衡 = 對每個節點，其左右子樹的高度差 ≤ 1。

**期望輸出**

```
[3,9,20,null,null,15,7] → true
[1,2,2,3,3,null,null,4,4] → false
```

**實作步驟**

**Step 1**：定義一個函式 `height(node)`，回傳子樹高度。如果子樹不平衡，回傳 -1 作為「特殊標記」。

**Step 2**：在計算高度時，若左或右子樹回傳 -1（不平衡），直接向上傳遞 -1。

**Step 3**：若 `abs(leftH - rightH) > 1`，回傳 -1；否則回傳 `max(leftH, rightH) + 1`。

**Step 4**：主函式只需判斷 `height(root) != -1`。

---

## 題目二：Construct Binary Tree from Preorder and Inorder Traversal（LeetCode 105）

**題目規格**

給前序遍歷和中序遍歷，重建二元樹。

**期望輸出**

```
preorder=[3,9,20,15,7], inorder=[9,3,15,20,7]
→ 重建出：
        3
       / \
      9  20
        /  \
       15   7
```

**實作步驟**

**Step 1**：前序的第一個元素 = 根節點。

**Step 2**：在中序中找根節點的位置，它左邊的是左子樹，右邊的是右子樹。

**Step 3**：左子樹的大小 `leftSize` 決定了前序中左子樹的範圍。

**Step 4**：遞迴建立左右子樹（傳入正確的 preorder 和 inorder 的子範圍）。

**優化**：用 `unordered_map` 預存中序的每個值的 index，避免每次線性搜尋。

---

## 題目三：Binary Tree Level Order Traversal II（LeetCode 107）

**題目規格**

按層序遍歷，但**從底部到頂部**輸出（最深層先）。

**期望輸出**

```
root = [3,9,20,null,null,15,7]
→ [[15,7],[9,20],[3]]
```

**實作步驟**

**Step 1**：標準 BFS 層序遍歷，結果存在 `vector<vector<int>>`。

**Step 2**：最後 `reverse(result.begin(), result.end())` 就好。

---

## 完整參考解答

**寫完再看！**

<details>
<summary>點開參考實作</summary>

```cpp
// 題目一：Balanced Binary Tree
bool isBalanced(TreeNode* root) {
    function<int(TreeNode*)> height = [&](TreeNode* node) -> int {
        if (!node) return 0;
        int l = height(node->left);
        if (l == -1) return -1;
        int r = height(node->right);
        if (r == -1) return -1;
        if (abs(l - r) > 1) return -1;
        return max(l, r) + 1;
    };
    return height(root) != -1;
}

// 題目二：Construct Binary Tree from Preorder and Inorder
TreeNode* buildTree(vector<int>& preorder, vector<int>& inorder) {
    unordered_map<int, int> inIdx;
    for (int i = 0; i < inorder.size(); i++) inIdx[inorder[i]] = i;

    function<TreeNode*(int, int, int, int)> build =
        [&](int preL, int preR, int inL, int inR) -> TreeNode* {
        if (preL > preR) return nullptr;
        int rootVal = preorder[preL];
        int mid = inIdx[rootVal];
        int leftSize = mid - inL;

        TreeNode* node = new TreeNode(rootVal);
        node->left  = build(preL+1, preL+leftSize, inL, mid-1);
        node->right = build(preL+leftSize+1, preR, mid+1, inR);
        return node;
    };

    return build(0, preorder.size()-1, 0, inorder.size()-1);
}

// 題目三：Level Order Traversal II
vector<vector<int>> levelOrderBottom(TreeNode* root) {
    vector<vector<int>> result;
    if (!root) return result;
    queue<TreeNode*> q;
    q.push(root);
    while (!q.empty()) {
        int size = q.size();
        vector<int> level;
        for (int i = 0; i < size; i++) {
            auto node = q.front(); q.pop();
            level.push_back(node->val);
            if (node->left)  q.push(node->left);
            if (node->right) q.push(node->right);
        }
        result.push_back(level);
    }
    reverse(result.begin(), result.end());
    return result;
}
```

</details>

---

## 測試用例

```
題目一：
[1,2,2,3,3,null,null,4,4] → false
[1,2,3,4,5] → true（高度差為 1）

題目二：
preorder=[1,2], inorder=[1,2] → 根 1，右子 2

題目三：
[1] → [[1]]
```

## 自我檢核

- [ ] Balanced Binary Tree：能說出為什麼用 `-1` 當特殊標記（而不是 global boolean）
- [ ] Build Tree：能說出 `leftSize` 怎麼算，以及前序的左右子樹範圍怎麼確定
- [ ] 能判斷哪道題適合 DFS、哪道適合 BFS
- [ ] 三題的 base case 都處理正確（空節點、單節點）

→ [Ch 19 Graph 表示法 + BFS](./19-graph-bfs.md)
