# Ch 0 — 環境設置：C++ for LeetCode

> 目標：建立一個能在本機快速驗證 LeetCode 解法的 C++ 環境，不需要每次都上傳網頁才知道對不對。

## 為什麼要在本機跑？

LeetCode 的線上 judge 很方便，但有幾個問題：

- 看不到中間變數的值（除非加 `cout`，還得一直刪）
- 無法快速測試邊界條件
- 網路斷掉就沒得寫

本機環境讓你把「寫 → 測 → 改」的循環壓到幾秒內。

## 安裝 C++ 編譯器

**Windows：** 裝 [MSYS2](https://www.msys2.org/)，然後在 MSYS2 terminal 裡：

```bash
pacman -S mingw-w64-x86_64-gcc
```

裝完後確認：

```bash
g++ --version
# 應該看到 g++ (Rev...) 14.x.x 之類的
```

**macOS：**

```bash
xcode-select --install
```

**Linux (Ubuntu/Debian)：**

```bash
sudo apt install g++
```

## 標準解題檔案結構

每次寫一題，建一個 `.cpp` 檔就夠了。不需要複雜的 CMake。

```cpp
#include <bits/stdc++.h>
using namespace std;

// 把 LeetCode 的 class Solution 直接貼進來
class Solution {
public:
    int someFunction(vector<int>& nums) {
        // 你的解法
        return 0;
    }
};

// 自己寫 main 來測試
int main() {
    Solution sol;

    // 測試 case 1
    vector<int> nums = {2, 1, 5, 1, 3, 2};
    cout << sol.someFunction(nums) << endl;  // 期望輸出

    // 測試 edge case
    vector<int> empty = {};
    cout << sol.someFunction(empty) << endl;

    return 0;
}
```

編譯並執行：

```bash
g++ -O2 -std=c++17 -o sol solution.cpp && ./sol
```

## `#include <bits/stdc++.h>` 是什麼？

這是 GCC 的萬用標頭檔，一行包含所有常用的 STL：`vector`、`map`、`queue`、`algorithm`... 全部都有。

**面試時不要依賴它**（部分公司的線上環境不支援），但本機練習可以盡量用，省去找 header 的時間。上 LeetCode 提交時它也是支援的。

## 常用 STL 速查表

面試最常用的就這幾個，先認識名字：

| 用途 | STL 類別 | 範例 |
|---|---|---|
| 動態陣列 | `vector<int>` | `v.push_back(x)` |
| 雙端佇列 | `deque<int>` | `d.push_front(x)` |
| 堆積（優先佇列） | `priority_queue<int>` | `pq.top()` |
| 雜湊表 | `unordered_map<int,int>` | `mp[key]++` |
| 雜湊集合 | `unordered_set<int>` | `s.count(x)` |
| 有序集合 | `set<int>` | `s.lower_bound(x)` |
| 堆疊 | `stack<int>` | `st.top(); st.pop()` |
| 佇列 | `queue<int>` | `q.front(); q.pop()` |

不用現在全記，每次用到時查就好。課程後面遇到的時候會詳細說明。

## 一個讓你少犯錯的習慣：印出中間狀態

寫滑動窗口或 DP 的時候，最容易 debug 的方式是把每一步的狀態印出來：

```cpp
for (int i = 0; i < n; i++) {
    // 做某件事
    cerr << "i=" << i << " val=" << arr[i] << "\n";  // debug 用 cerr
}
```

用 `cerr` 而不是 `cout`，因為有些場合 `cerr` 不會被 redirect，比較不會干擾輸出。提交前直接刪掉就好。

## 動手練習

裝好環境後，把這段 code 貼進 `test.cpp`，編譯後確認輸出是 `9`：

```cpp
#include <bits/stdc++.h>
using namespace std;

int maxSum(vector<int>& arr, int k) {
    int n = arr.size();
    int windowSum = 0;
    for (int i = 0; i < k; i++) windowSum += arr[i];
    int maxS = windowSum;
    for (int i = 1; i <= n - k; i++) {
        windowSum += arr[i + k - 1] - arr[i - 1];
        maxS = max(maxS, windowSum);
    }
    return maxS;
}

int main() {
    vector<int> arr = {2, 1, 5, 1, 3, 2};
    cout << maxSum(arr, 3) << endl;  // 應該輸出 9
    return 0;
}
```

這就是你在對話裡剛推導出來的 sliding window 解法，C++ 版本。

## 自我檢核

- [ ] `g++ --version` 能正常執行
- [ ] 能編譯並執行 `.cpp` 檔案
- [ ] 知道 `#include <bits/stdc++.h>` 的用途與限制
- [ ] 知道用 `cerr` 印 debug 資訊

環境準備好了。接下來補最重要的地基——遞迴直覺，後面 Tree、DFS、DP 全靠它。

→ [Ch 1 遞迴直覺：Call Stack 視覺化](./01-recursion-intuition.md)
