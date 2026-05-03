# Ch 15 — Tree 結構 + 前 / 中 / 後序遍歷

> 目標：熟悉二元樹的節點定義，能默寫三種遞迴遍歷，以及它們的迭代版本。

## 節點定義

LeetCode 上的標準定義：

```cpp
struct TreeNode {
    int val;
    TreeNode* left;
    TreeNode* right;
    TreeNode(int x) : val(x), left(nullptr), right(nullptr) {}
};
```

指標為 `nullptr` 代表沒有子節點。

## 三種深度優先遍歷（DFS）

命名的依據是「根節點在什麼時候被訪問」：

```
        1
       / \
      2   3
     / \
    4   5

前序（Pre-order）：根 → 左 → 右  = [1, 2, 4, 5, 3]
中序（In-order）：左 → 根 → 右  = [4, 2, 5, 1, 3]
後序（Post-order）：左 → 右 → 根 = [4, 5, 2, 3, 1]
```

**重要性質**：對 BST（Binary Search Tree），中序遍歷得到**有序序列**。

## 遞迴實作（三種都是三行）

```cpp
void preorder(TreeNode* root, vector<int>& res) {
    if (!root) return;
    res.push_back(root->val);   // 根
    preorder(root->left, res);  // 左
    preorder(root->right, res); // 右
}

void inorder(TreeNode* root, vector<int>& res) {
    if (!root) return;
    inorder(root->left, res);   // 左
    res.push_back(root->val);   // 根
    inorder(root->right, res);  // 右
}

void postorder(TreeNode* root, vector<int>& res) {
    if (!root) return;
    postorder(root->left, res);  // 左
    postorder(root->right, res); // 右
    res.push_back(root->val);    // 根
}
```

三種的差別**只有 `res.push_back(root->val)` 的位置**。

## 迭代實作：前序

用 Stack，先 push right 再 push left（因為 stack LIFO，left 要先處理）：

```cpp
vector<int> preorderIterative(TreeNode* root) {
    vector<int> res;
    if (!root) return res;
    stack<TreeNode*> st;
    st.push(root);

    while (!st.empty()) {
        TreeNode* node = st.top(); st.pop();
        res.push_back(node->val);
        if (node->right) st.push(node->right);  // 先 push right
        if (node->left)  st.push(node->left);   // 後 push left（先被 pop）
    }
    return res;
}
```

## 迭代實作：中序

中序稍微複雜，因為要先到達最左邊再開始記錄：

```cpp
vector<int> inorderIterative(TreeNode* root) {
    vector<int> res;
    stack<TreeNode*> st;
    TreeNode* cur = root;

    while (cur || !st.empty()) {
        while (cur) {           // 一直往左走，沿途 push
            st.push(cur);
            cur = cur->left;
        }
        cur = st.top(); st.pop();  // 左邊走完了，處理當前節點
        res.push_back(cur->val);
        cur = cur->right;          // 轉向右子樹
    }
    return res;
}
```

## 重要性質應用

**BST 驗證（LeetCode 98）**：中序遍歷應得到嚴格遞增序列。

```cpp
bool isValidBST(TreeNode* root) {
    long prev = LONG_MIN;
    function<bool(TreeNode*)> inorder = [&](TreeNode* node) {
        if (!node) return true;
        if (!inorder(node->left)) return false;
        if (node->val <= prev) return false;
        prev = node->val;
        return inorder(node->right);
    };
    return inorder(root);
}
```

## 三種遍歷的用途

| 遍歷 | 常見用途 |
|---|---|
| 前序 | 複製樹、序列化（root 先存） |
| 中序 | BST 相關、取有序序列 |
| 後序 | 刪除樹、計算依賴後子節點的值（子樹先算完） |

## 自我檢核

- [ ] 能默寫三種遞迴遍歷（不看筆記）
- [ ] 能追蹤上方範例樹，寫出三種遍歷結果
- [ ] 能寫出中序的迭代版本
- [ ] 知道中序遍歷 BST 的結果是有序的

→ [Ch 16 BFS on Tree：層序遍歷](./16-tree-bfs.md)
