# Ch 2 — 遞迴 → 迭代：理解什麼時候該轉換

> 目標：知道遞迴和迭代的本質差異，以及何時必須把遞迴改寫成用 explicit stack 的迭代版本。

## 遞迴的代價

遞迴很直覺，但每次函式呼叫都會在 call stack 上佔一個 frame，儲存：
- 函式參數
- 區域變數
- 回傳位址

深度遞迴（例如鏈結串列有 10 萬個節點）會消耗大量 stack 空間，導致 **stack overflow**。

```
預設 stack 大小：
- Linux：約 8 MB
- Windows：約 1 MB
- LeetCode judge：視題目，通常允許幾萬層
```

實務上，遞迴深度超過 **10,000~100,000** 就要注意。

## 核心洞見：遞迴 = 系統幫你管理 stack

你寫：

```cpp
void dfs(int node) {
    // 處理 node
    dfs(node.left);
    dfs(node.right);
}
```

系統在背後做的事：

```
呼叫 dfs(root)    → push frame 到 call stack
  呼叫 dfs(left)  → push frame
    ...
  回傳            → pop frame
回傳              → pop frame
```

迭代版本就是**你自己管理這個 stack**。

## 範例：DFS 的遞迴 vs 迭代

以樹的前序遍歷（Pre-order: root → left → right）為例。

**遞迴版：**

```cpp
void preorder(TreeNode* root, vector<int>& result) {
    if (!root) return;
    result.push_back(root->val);
    preorder(root->left, result);
    preorder(root->right, result);
}
```

**迭代版（自己管理 stack）：**

```cpp
vector<int> preorder(TreeNode* root) {
    vector<int> result;
    if (!root) return result;

    stack<TreeNode*> st;
    st.push(root);

    while (!st.empty()) {
        TreeNode* node = st.top();
        st.pop();
        result.push_back(node->val);

        // 注意：先推 right，再推 left
        // 因為 stack 是 LIFO，left 要先被處理，所以後推
        if (node->right) st.push(node->right);
        if (node->left)  st.push(node->left);
    }
    return result;
}
```

對照兩版的 stack 操作：

```
遞迴版                    迭代版
call stack (系統)         stack<TreeNode*> (你的)
push frame               st.push(node)
pop frame                st.pop()
函式回傳順序              LIFO 取出順序
```

**完全同構**。只是一個由系統管理，一個由你管理。

## 什麼時候必須用迭代？

| 情況 | 建議 |
|---|---|
| 遞迴深度 < 幾千層 | 遞迴就好，更易讀 |
| 遞迴深度可能很大（鏈結串列、退化樹） | 改迭代 |
| 需要在「回溯」時做額外操作 | 視情況（有時遞迴反而更清楚） |
| 面試被要求不能用遞迴 | 迭代 |

LeetCode 上大多數 Tree / Graph 題用遞迴都沒問題，因為深度通常不超過幾千。但如果題目說輸入是「鏈結串列」且長度可達 10⁵，就要小心。

## 尾遞迴：一種特殊情況

如果遞迴呼叫是函式的**最後一件事**（沒有在回傳後做其他計算），叫做尾遞迴（tail recursion）。

```cpp
// 非尾遞迴：回傳後還需要做乘法
int factorial(int n) {
    if (n == 0) return 1;
    return n * factorial(n - 1);  // 回傳後還要乘以 n
}

// 尾遞迴：回傳後什麼都不做
int factorial(int n, int acc = 1) {
    if (n == 0) return acc;
    return factorial(n - 1, n * acc);  // 直接回傳，沒有後續計算
}
```

理論上，尾遞迴可以被編譯器最佳化成迭代（tail call optimization），不消耗額外 stack。但 **C++ 標準不保證這個最佳化**，GCC 加 `-O2` 通常會做，但不要依賴它。

## 動手練習

把這段遞迴改寫成迭代版：

```cpp
// 計算陣列總和（遞迴）
int sum(vector<int>& arr, int i) {
    if (i == arr.size()) return 0;
    return arr[i] + sum(arr, i + 1);
}
```

這個很簡單，不需要 stack，直接用迴圈就夠——為什麼？

因為它是**線性遞迴**（每次只呼叫自己一次），轉成迴圈直覺：

```cpp
int sum(vector<int>& arr) {
    int total = 0;
    for (int x : arr) total += x;
    return total;
}
```

**只有「分支遞迴」（每次呼叫自己多次，例如樹的 DFS）才需要 explicit stack。**

## 自我檢核

- [ ] 能解釋「遞迴 = 系統管理 stack，迭代 = 自己管理 stack」
- [ ] 知道什麼情況下遞迴可能造成 stack overflow
- [ ] 能把樹的前序遍歷從遞迴改成迭代
- [ ] 知道線性遞迴和分支遞迴的差異

地基打完了。從這章開始進入真正的解題技巧。

→ [Ch 3 Two Pointers：相向 vs 同向](./03-two-pointers.md)
