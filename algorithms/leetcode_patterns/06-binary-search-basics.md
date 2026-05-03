# Ch 6 — Binary Search 基礎：邊界陷阱完全解析

> 目標：寫出一個永遠不會 off-by-one 的二分搜尋，理解三種寫法的差異，選定一種記熟。

## 為什麼二分搜尋這麼容易寫錯？

問過很多工程師，幾乎沒有人第一次寫能完全正確。問題不在演算法，在**邊界條件**：

- `while (l < r)` 還是 `while (l <= r)`？
- `mid = (l + r) / 2` 還是 `l + (r - l) / 2`？
- 找到時直接 `return` 還是繼續縮小？
- 最後回傳 `l` 還是 `r` 還是 `l - 1`？

這些問題的答案取決於你選擇的寫法模板。本章給你一種最穩的寫法，從頭到尾只用這一種。

## 前提：二分搜尋的本質

二分搜尋適用於**有序**的搜索空間。每次將搜索空間縮小一半，O(log N)。

```
搜索範圍 [l, r]
mid = l + (r - l) / 2   ← 避免 (l+r) 可能的整數溢位

若 arr[mid] == target → 找到
若 arr[mid] < target  → target 在右半，l = mid + 1
若 arr[mid] > target  → target 在左半，r = mid - 1
```

## 推薦模板：閉區間 [l, r]

```cpp
int binarySearch(vector<int>& arr, int target) {
    int l = 0, r = arr.size() - 1;  // 閉區間：兩端都包含

    while (l <= r) {                  // l == r 時還有一個元素要檢查
        int mid = l + (r - l) / 2;

        if (arr[mid] == target) return mid;
        else if (arr[mid] < target) l = mid + 1;
        else r = mid - 1;
    }

    return -1;  // 找不到
}
```

**為什麼用 `l + (r - l) / 2` 而不是 `(l + r) / 2`？**

當 `l` 和 `r` 都很大時（接近 INT_MAX），`l + r` 可能整數溢位。`l + (r - l) / 2` 等價但安全。

**為什麼 `while (l <= r)`？**

因為我們定義搜索區間是閉區間 `[l, r]`。當 `l == r` 時，`[l, r]` 還有一個元素需要檢查，所以 `<=`。

當 `l > r`，區間為空，結束。

## 手動追蹤一遍

```
arr = [1, 3, 5, 7, 9, 11], target = 7

l=0, r=5, mid=2: arr[2]=5 < 7 → l=3
l=3, r=5, mid=4: arr[4]=9 > 7 → r=3
l=3, r=3, mid=3: arr[3]=7 = 7 → return 3
```

```
arr = [1, 3, 5, 7, 9, 11], target = 6

l=0, r=5, mid=2: arr[2]=5 < 6 → l=3
l=3, r=5, mid=4: arr[4]=9 > 6 → r=3
l=3, r=3, mid=3: arr[3]=7 > 6 → r=2
l=3, r=2: l > r → return -1
```

## 常見錯誤：死迴圈

```cpp
// 危險！
while (l < r) {
    int mid = (l + r) / 2;
    if (arr[mid] < target) l = mid;  // ← 這裡沒有 +1
    else r = mid;
}
```

當 `l = 0, r = 1` 時：
- `mid = 0`
- 若 `arr[0] < target`，`l = mid = 0`，**沒有進展**，無限迴圈。

修法：移動指標時一定要排除 `mid` 本身：`l = mid + 1` 或 `r = mid - 1`。

（半開區間的寫法 `[l, r)` 可以允許 `r = mid`，但需要整套配套，混用就出錯。）

## 二分搜尋的三種使用情境

| 情境 | 說明 |
|---|---|
| 精確查找 | 找到 target 回傳 index，找不到回傳 -1（本章） |
| 邊界查找 | 找第一個 / 最後一個滿足條件的位置（下一章） |
| 答案空間二分 | target 不是陣列元素，而是「答案的可能範圍」（Ch 8） |

## LeetCode 704 — Binary Search（直接練）

最基本的題，就是找 target，回傳 index 或 -1。
用上面的模板直接 AC。

## 自我檢核

- [ ] 能背出閉區間模板（`while l<=r`，`mid+1`，`mid-1`）
- [ ] 知道為什麼用 `l + (r-l)/2`
- [ ] 能手動追蹤「找到」和「找不到」兩種情況
- [ ] 知道什麼寫法會造成死迴圈

→ [Ch 7 lower_bound / upper_bound 變形](./07-binary-search-variants.md)
