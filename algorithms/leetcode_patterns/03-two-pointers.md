# Ch 3 — Two Pointers：相向 vs 同向

> 目標：認識兩種 two pointers 的使用場景，能判斷一題應該用「相向」還是「同向」，並寫出不會 off-by-one 的實作。

## Two Pointers 的本質

Two Pointers 是一種**避免暴力雙迴圈**的技巧。

暴力解通常是：

```cpp
for (int i = 0; i < n; i++)
    for (int j = i+1; j < n; j++)
        // 處理 arr[i] 和 arr[j]
```

這是 O(N²)。Two Pointers 讓你用兩個指標聰明地移動，壓到 O(N)。

關鍵前提：**陣列通常是有序的**（或者問題結構讓指標的移動有明確方向）。

## 兩種模式

```
相向（Opposite Direction）：   同向（Same Direction）：
l →          ← r             s →
[1, 2, 3, 4, 5, 6, 7]        f →
 l               r             [1, 2, 2, 3, 4]
                                s   f
```

| | 相向 | 同向 |
|---|---|---|
| 初始位置 | `l=0, r=n-1` | `slow=0, fast=0` 或 `fast=1` |
| 移動方向 | 往中間靠攏 | 都往右 |
| 終止條件 | `l >= r` | `fast` 到底 |
| 典型題型 | Two Sum II、3Sum、回文 | 去重、Fast/Slow Pointer |

## 相向：Two Sum II

**題目（LeetCode 167）**：已排序陣列，找兩個數字使其和等於 target，回傳 index（1-indexed）。

**為什麼相向有效？**

陣列已排序，`arr[l] + arr[r]` 的值：
- 太大 → `r` 左移（縮小較大的數）
- 太小 → `l` 右移（增大較小的數）
- 等於 target → 找到了

```
arr = [2, 7, 11, 15], target = 9
       l            r

arr[l] + arr[r] = 2 + 15 = 17 > 9  → r 左移
       l        r
arr[l] + arr[r] = 2 + 11 = 13 > 9  → r 左移
       l     r
arr[l] + arr[r] = 2 + 7 = 9 = target → 找到！
```

```cpp
vector<int> twoSum(vector<int>& numbers, int target) {
    int l = 0, r = numbers.size() - 1;
    while (l < r) {
        int sum = numbers[l] + numbers[r];
        if (sum == target) return {l + 1, r + 1};
        else if (sum < target) l++;
        else r--;
    }
    return {};
}
```

時間：O(N)，空間：O(1)。

## 相向：Valid Palindrome

**題目（LeetCode 125）**：字串只考慮字母和數字，忽略大小寫，判斷是否為回文。

```cpp
bool isPalindrome(string s) {
    int l = 0, r = s.size() - 1;
    while (l < r) {
        while (l < r && !isalnum(s[l])) l++;  // 跳過非字母數字
        while (l < r && !isalnum(s[r])) r--;
        if (tolower(s[l]) != tolower(s[r])) return false;
        l++;
        r--;
    }
    return true;
}
```

常見陷阱：跳過字元時忘記加 `l < r` 的保護，可能導致兩個指標錯過彼此。

## 同向：Remove Duplicates from Sorted Array

**題目（LeetCode 26）**：已排序陣列，in-place 去除重複，回傳新長度。

`slow` 指向下一個要填入的位置，`fast` 往前掃：

```
arr = [1, 1, 2, 3, 3]
       s
       f

f=0: arr[f]=1, slow 位置放 1, slow++
     s=1, f=1: arr[f]=1 == arr[slow-1]=1, 跳過
     s=1, f=2: arr[f]=2 != arr[slow-1]=1, 放入, slow++
     s=2, f=3: arr[f]=3 != arr[slow-1]=2, 放入, slow++
     s=3, f=4: arr[f]=3 == arr[slow-1]=3, 跳過

結果: [1, 2, 3, _, _], 回傳 slow=3
```

```cpp
int removeDuplicates(vector<int>& nums) {
    if (nums.empty()) return 0;
    int slow = 1;
    for (int fast = 1; fast < nums.size(); fast++) {
        if (nums[fast] != nums[slow - 1]) {
            nums[slow++] = nums[fast];
        }
    }
    return slow;
}
```

## 同向：Fast & Slow Pointer（Floyd's Algorithm）

偵測 linked list 是否有環。`slow` 每次走一步，`fast` 每次走兩步，如果有環，它們一定會相遇。

```cpp
bool hasCycle(ListNode* head) {
    ListNode* slow = head;
    ListNode* fast = head;
    while (fast && fast->next) {
        slow = slow->next;
        fast = fast->next->next;
        if (slow == fast) return true;
    }
    return false;
}
```

為什麼一定會相遇？想像跑步：fast 在環上比 slow 快，相對速度為 1，遲早追上。

## 題型辨識：何時用 Two Pointers？

看到這些關鍵字就考慮：
- **已排序陣列** + 找兩個/三個數的組合 → 相向
- **去重 / in-place 修改** 有序陣列 → 同向
- **回文** 判斷 → 相向
- **Linked List 環 / 中點** → fast & slow

## 常見錯誤

- `while (l < r)` 忘記寫 `=`，在 l==r 時多走一步
- 相向時兩個指標同時移動（應該只移動一個）
- Fast/slow pointer 沒檢查 `fast->next` 是否為 null

## 自我檢核

- [ ] 能解釋相向和同向的適用場景
- [ ] 能從頭寫出 Two Sum II 的 two pointers 解
- [ ] 知道 fast & slow pointer 為什麼能偵測環
- [ ] 知道 `while (l < r)` 和 `while (l <= r)` 的差異

→ [Ch 4 Sliding Window（固定窗口）](./04-sliding-window-fixed.md)
