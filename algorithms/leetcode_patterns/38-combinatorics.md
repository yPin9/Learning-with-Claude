# Ch 38 — 排列組合：計數類題入門

> 目標：掌握組合數的計算與性質，能用 Pascal's Triangle 和乘法原理解決計數類題。

## 基本概念

**排列 P(n, k)**：從 n 個中選 k 個排成一列（順序有關）。

```
P(n, k) = n! / (n-k)! = n × (n-1) × ... × (n-k+1)
```

**組合 C(n, k)**：從 n 個中選 k 個（順序無關）。

```
C(n, k) = n! / (k! × (n-k)!) = P(n,k) / k!
```

## Pascal's Triangle（帕斯卡三角形）

`C(n, k) = C(n-1, k-1) + C(n-1, k)`

直覺：從 n 個中選 k 個，可以分成：
- 包含第 n 個元素：`C(n-1, k-1)`（剩下的從 n-1 個中選 k-1 個）
- 不包含第 n 個：`C(n-1, k)`

```
Pascal's Triangle:
       1
      1 1
     1 2 1
    1 3 3 1
   1 4 6 4 1
```

**LeetCode 119：Pascal's Triangle II**，回傳第 k 行：

```cpp
vector<int> getRow(int rowIndex) {
    vector<int> row(rowIndex+1, 0);
    row[0] = 1;
    for (int i = 1; i <= rowIndex; i++)
        for (int j = i; j >= 1; j--)  // 從右往左，避免覆蓋舊值
            row[j] += row[j-1];
    return row;
}
```

## 計算 C(n, k) 避免溢位

直接算階乘會溢位。用邊乘邊除的方式：

```cpp
long long comb(int n, int k) {
    if (k > n - k) k = n - k;  // C(n,k) = C(n, n-k)，取較小的 k
    long long result = 1;
    for (int i = 0; i < k; i++) {
        result = result * (n - i) / (i + 1);  // 保持整數，必須先乘後除且順序正確
    }
    return result;
}
```

## Unique Paths（又見到了）

Unique Paths（LeetCode 62）用 DP 已解，但也可以用組合數：

從左上到右下，必須走 `(m-1)` 步向下和 `(n-1)` 步向右，共 `(m+n-2)` 步，選其中 `(m-1)` 步是向下的：

```
答案 = C(m+n-2, m-1)
```

```cpp
int uniquePaths(int m, int n) {
    return comb(m + n - 2, m - 1);
}
```

## 乘法原理

計數問題的基礎：獨立的步驟，各步的選擇數相乘。

**應用：LeetCode 60 — Permutation Sequence**

第 k 個排列（1-indexed）。`n` 個數字共 `n!` 個排列，第一位決定了哪組 `(n-1)!` 個排列，以此類推：

```cpp
string getPermutation(int n, int k) {
    string digits = "";
    vector<int> fact(n+1, 1);
    for (int i = 1; i <= n; i++) {
        digits += (char)('0' + i);
        fact[i] = fact[i-1] * i;
    }

    k--;  // 轉成 0-indexed
    string result = "";
    for (int i = n; i >= 1; i--) {
        int idx = k / fact[i-1];
        result += digits[idx];
        digits.erase(idx, 1);
        k %= fact[i-1];
    }
    return result;
}
```

## 計數問題的常見解法

| 問題類型 | 解法 |
|---|---|
| 從 n 個選 k 個的方法數 | `C(n, k)` |
| 路徑計數（只能往右/下） | `C(m+n-2, m-1)` |
| 有條件的排列 | 乘法原理 + 分組 |
| 環狀排列 | `(n-1)!`（固定一個元素）|
| 有重複元素的全排列 | `n! / (k1! × k2! × ...)` |

## 自我檢核

- [ ] 能說出 `C(n,k) = C(n-1,k-1) + C(n-1,k)` 的組合意義
- [ ] 能用 Pascal's Triangle 遞推法計算第 k 行
- [ ] 能用組合數解 Unique Paths（不用 DP）
- [ ] 知道計算 C(n,k) 時「邊乘邊除」的原因

→ [Ch 39 題型辨識總表：看到什麼 → 用什麼](./39-pattern-recognition.md)
