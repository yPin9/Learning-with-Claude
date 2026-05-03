# Ch 9 — Prefix Sum：一維與二維

> 目標：建立前綴和的計算與查詢思維，能用 O(1) 查詢任意子陣列的總和。

## 問題：子陣列總和

給陣列 `arr`，有 Q 次查詢，每次問 `arr[l..r]` 的總和。

暴力：每次 O(N)，Q 次查詢共 O(NQ)。

前綴和：O(N) 預處理，每次查詢 O(1)，共 O(N + Q)。

## 一維前綴和

定義 `prefix[i]` = `arr[0] + arr[1] + ... + arr[i-1]`（**不含** `arr[i]`，`prefix[0] = 0`）。

```
arr    = [2,  1,  5,  1,  3,  2]
index     0   1   2   3   4   5

prefix = [0,  2,  3,  8,  9, 12, 14]
index     0   1   2   3   4   5   6
```

查詢 `arr[l..r]` 的總和：`prefix[r+1] - prefix[l]`

```
arr[2..4] = 5+1+3 = 9
= prefix[5] - prefix[2] = 12 - 3 = 9  ✓
```

```cpp
vector<int> buildPrefix(vector<int>& arr) {
    int n = arr.size();
    vector<int> prefix(n + 1, 0);
    for (int i = 0; i < n; i++)
        prefix[i + 1] = prefix[i] + arr[i];
    return prefix;
}

// 查詢 arr[l..r] 的總和（閉區間）
int query(vector<int>& prefix, int l, int r) {
    return prefix[r + 1] - prefix[l];
}
```

**為什麼 prefix 的長度是 n+1？**

讓 `prefix[0] = 0` 作為哨兵，使得「從 index 0 開始的子陣列」也能用同一個公式：
`arr[0..r] = prefix[r+1] - prefix[0] = prefix[r+1]`。

## 範例：Subarray Sum Equals K（LeetCode 560）

**題目**：找有幾個子陣列的總和等於 k。

暴力 O(N²)，用前綴和 + HashMap 可以做到 O(N)。

**觀察**：`arr[l..r] = k` 等價於 `prefix[r+1] - prefix[l] = k`，即 `prefix[l] = prefix[r+1] - k`。

對每個 `r`，只要在已看過的前綴和中找有幾個等於 `prefix[r+1] - k`。

```cpp
int subarraySum(vector<int>& nums, int k) {
    unordered_map<int, int> seen;
    seen[0] = 1;  // 哨兵：prefix[0]=0 出現過 1 次
    int prefix = 0, count = 0;

    for (int x : nums) {
        prefix += x;
        count += seen[prefix - k];  // 找前面有幾個 prefix 值 == prefix - k
        seen[prefix]++;
    }

    return count;
}
```

`seen[0] = 1` 的作用：處理「從頭開始的子陣列總和等於 k」的情況。

## 二維前綴和

矩陣查詢：`mat[r1..r2][c1..c2]` 的總和。

定義 `prefix[i][j]` = 矩陣左上角 `(0,0)` 到 `(i-1, j-1)` 的總和。

```
建表公式：
prefix[i][j] = mat[i-1][j-1]
             + prefix[i-1][j]
             + prefix[i][j-1]
             - prefix[i-1][j-1]   ← 去掉重複計算的部分（容斥原理）
```

查詢公式（矩形 `(r1,c1)` 到 `(r2,c2)`）：

```
sum = prefix[r2+1][c2+1]
    - prefix[r1][c2+1]
    - prefix[r2+1][c1]
    + prefix[r1][c1]
```

```cpp
// 建立二維前綴和
vector<vector<int>> build2D(vector<vector<int>>& mat) {
    int m = mat.size(), n = mat[0].size();
    vector<vector<int>> p(m + 1, vector<int>(n + 1, 0));
    for (int i = 1; i <= m; i++)
        for (int j = 1; j <= n; j++)
            p[i][j] = mat[i-1][j-1] + p[i-1][j] + p[i][j-1] - p[i-1][j-1];
    return p;
}

int query2D(vector<vector<int>>& p, int r1, int c1, int r2, int c2) {
    return p[r2+1][c2+1] - p[r1][c2+1] - p[r2+1][c1] + p[r1][c1];
}
```

## 何時用前綴和？

- 多次查詢子陣列 / 子矩陣的總和
- 需要快速判斷是否存在某個總和的子陣列
- 結合 HashMap 解決「總和等於 k」類問題

## 常見錯誤

- `prefix[i]` 的定義搞混（有些人定義含 i，有些不含）。建議永遠用 `prefix[i] = sum of arr[0..i-1]`，長度 n+1
- 查詢公式 `prefix[r+1] - prefix[l]` 的 +1 漏掉
- 二維前綴和的容斥原理漏加 `p[i-1][j-1]`

## 自我檢核

- [ ] 能從頭建立一維前綴和（包含哨兵 prefix[0]=0）
- [ ] 能用 `prefix[r+1] - prefix[l]` 查詢子陣列總和
- [ ] 能解釋 Subarray Sum Equals K 的 HashMap 做法
- [ ] 知道二維前綴和的建表公式（容斥原理）

→ [Ch 10 HashMap / HashSet：計數與配對](./10-hashing.md)
