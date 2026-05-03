# 練習 B — Binary Search 題型辨識

> 目標：練習識別「這題要用哪種二分」，並在不看模板的情況下正確寫出邊界。

**寫完再看！**

---

## 題目一：Search in Rotated Sorted Array（LeetCode 33）

**題目規格**

原本已排序的陣列在某個 pivot 處被旋轉，例如 `[4,5,6,7,0,1,2]`。在其中找 target，回傳 index，找不到回傳 -1。陣列中沒有重複元素。

**期望輸出**

```
nums=[4,5,6,7,0,1,2], target=0 → 4
nums=[4,5,6,7,0,1,2], target=3 → -1
nums=[1], target=0 → -1
```

**實作步驟**

**Step 1**：旋轉後，每次分出的兩半，其中一半一定是有序的（sorted）。

**Step 2**：判斷哪半是有序的：若 `nums[l] <= nums[mid]`，左半有序；否則右半有序。

**Step 3**：判斷 target 在有序的那半裡面嗎？在的話縮到那半，否則縮到另一半。

**Step 4**：注意等號：`nums[l] <= nums[mid]`（而不是 `<`），因為 `l == mid` 時左半只有一個元素，也是「有序的」。

---

## 題目二：Find Minimum in Rotated Sorted Array（LeetCode 153）

**題目規格**

旋轉排序陣列，找最小值。陣列無重複。

**期望輸出**

```
nums=[3,4,5,1,2] → 1
nums=[4,5,6,7,0,1,2] → 0
nums=[11,13,15,17] → 11
```

**實作步驟**

**Step 1**：最小值在哪邊？如果 `nums[mid] > nums[r]`，最小值在右半（mid+1 到 r）；否則在左半（l 到 mid，包含 mid）。

**Step 2**：用半開區間 `[l, r)`，`while (l < r)`，最後回傳 `nums[l]`。

**Step 3**：不用每次找到 target，迴圈結束時 `l == r` 就是最小值的位置。

---

## 題目三：Koko Eating Bananas（LeetCode 875）

**題目規格**

`piles[i]` 是第 i 堆香蕉的數量，`h` 是小時數。以速度 k（根/小時）吃，每小時最多吃一堆的 k 根。問最小的 k 使得 h 小時內能吃完。

**期望輸出**

```
piles=[3,6,7,11], h=8 → 4
piles=[30,11,23,4,20], h=5 → 30
piles=[30,11,23,4,20], h=6 → 23
```

**實作步驟**

**Step 1**：答案範圍是 `[1, max(piles)]`。

**Step 2**：寫驗證函式 `canFinish(piles, k, h)`：計算以速度 k 需要幾小時，判斷是否 ≤ h。每堆需要 `ceil(piles[i] / k)` 小時，用整數：`(piles[i] + k - 1) / k`。

**Step 3**：在答案空間上 Binary Search，找第一個讓 `canFinish` 為 true 的 k（lower_bound 形式：`r = mid` 或 `l = mid + 1`）。

---

## 完整參考解答

**寫完再看！**

<details>
<summary>點開參考實作</summary>

```cpp
// 題目一：Search in Rotated Sorted Array
int search(vector<int>& nums, int target) {
    int l = 0, r = nums.size() - 1;
    while (l <= r) {
        int mid = l + (r - l) / 2;
        if (nums[mid] == target) return mid;

        if (nums[l] <= nums[mid]) {  // 左半有序
            if (nums[l] <= target && target < nums[mid])
                r = mid - 1;
            else
                l = mid + 1;
        } else {  // 右半有序
            if (nums[mid] < target && target <= nums[r])
                l = mid + 1;
            else
                r = mid - 1;
        }
    }
    return -1;
}

// 題目二：Find Minimum in Rotated Sorted Array
int findMin(vector<int>& nums) {
    int l = 0, r = nums.size() - 1;
    while (l < r) {
        int mid = l + (r - l) / 2;
        if (nums[mid] > nums[r])
            l = mid + 1;  // 最小在右半
        else
            r = mid;      // 最小在左半（含 mid）
    }
    return nums[l];
}

// 題目三：Koko Eating Bananas
int minEatingSpeed(vector<int>& piles, int h) {
    int l = 1, r = *max_element(piles.begin(), piles.end());
    while (l < r) {
        int mid = l + (r - l) / 2;
        long long hours = 0;
        for (int p : piles) hours += (p + mid - 1) / mid;
        if (hours <= h) r = mid;
        else l = mid + 1;
    }
    return l;
}
```

</details>

---

## 測試用例

```cpp
assert(search({4,5,6,7,0,1,2}, 0) == 4);
assert(search({4,5,6,7,0,1,2}, 3) == -1);
assert(findMin({3,4,5,1,2}) == 1);
assert(findMin({11,13,15,17}) == 11);
assert(minEatingSpeed({3,6,7,11}, 8) == 4);
assert(minEatingSpeed({30,11,23,4,20}, 6) == 23);
```

## 自我檢核

- [ ] Search in Rotated：能說出判斷哪半有序的條件 `nums[l] <= nums[mid]`（含等號的原因）
- [ ] Find Minimum：知道為什麼移動條件是 `nums[mid] > nums[r]`（與 r 比較，不與 l 比較）
- [ ] Koko：能說出答案範圍的上下界
- [ ] 三題都能說出用的是哪種 Binary Search（精確查找 / lower_bound / on-answer）

→ [Ch 9 Prefix Sum：一維與二維](./09-prefix-sum.md)
