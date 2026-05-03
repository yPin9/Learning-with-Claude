# Ch 36 — 位元操作（Bit Manipulation）

> 目標：掌握常用的位元技巧，能用它們解決計數、去重、配對類問題。

## 基本操作速查

```cpp
x & y    // AND：兩者都是 1 才是 1
x | y    // OR：有一個是 1 就是 1
x ^ y    // XOR：不同才是 1（相同為 0）
~x       // NOT：取反
x << k   // 左移 k 位（乘以 2^k）
x >> k   // 右移 k 位（除以 2^k，向下取整）
```

常用技巧：

```cpp
x & 1          // 判斷奇偶（結果為 1 是奇數）
x & (x-1)      // 清除最低位的 1
x & (-x)       // 取出最低位的 1（lowbit）
x ^ x == 0     // 同一個數 XOR 等於 0
x ^ 0 == x     // 和 0 XOR 等於自己
```

## 題目 1：Single Number（LeetCode 136）

**題目**：陣列中所有數字出現兩次，只有一個出現一次，找它。

**關鍵**：`a ^ a = 0`，`a ^ 0 = a`，XOR 所有數字，成對的抵消，剩下就是孤單的那個。

```cpp
int singleNumber(vector<int>& nums) {
    int result = 0;
    for (int x : nums) result ^= x;
    return result;
}
```

O(N) 時間，O(1) 空間。

## 題目 2：Single Number II（LeetCode 137）

**題目**：所有數字出現三次，只有一個出現一次。

XOR 不夠用了（三次不是兩次）。改用位元計數：對每個 bit，算「所有數字這個 bit 的總和 mod 3」，剩下的就是孤單數字的那個 bit。

```cpp
int singleNumber(vector<int>& nums) {
    int result = 0;
    for (int i = 0; i < 32; i++) {
        int sum = 0;
        for (int x : nums)
            sum += (x >> i) & 1;  // 統計第 i 個 bit 的總和
        result |= (sum % 3) << i; // 若 sum%3==1，孤單數在這個 bit 上是 1
    }
    return result;
}
```

## 題目 3：Counting Bits（LeetCode 338）

**題目**：回傳 0 到 n 每個數字的二進位表示中 1 的個數。

利用 `dp[i] = dp[i >> 1] + (i & 1)`：

- `i >> 1` 就是把 i 的最低位去掉（即 i/2），1 的個數是 `dp[i >> 1]`
- 最低位若是 1，加 1

```cpp
vector<int> countBits(int n) {
    vector<int> dp(n+1, 0);
    for (int i = 1; i <= n; i++)
        dp[i] = dp[i >> 1] + (i & 1);
    return dp;
}
```

## 題目 4：Reverse Bits（LeetCode 190）

逐位反轉 32 位整數。

```cpp
uint32_t reverseBits(uint32_t n) {
    uint32_t result = 0;
    for (int i = 0; i < 32; i++) {
        result = (result << 1) | (n & 1);
        n >>= 1;
    }
    return result;
}
```

## 題目 5：Power of Two（LeetCode 231）

判斷是否是 2 的冪次。

2 的冪次的二進位表示只有一個 1，所以 `n & (n-1) == 0`（清除最低位的 1 後變為 0）。

```cpp
bool isPowerOfTwo(int n) {
    return n > 0 && (n & (n-1)) == 0;
}
```

## 位元操作的辨識線索

- 題目提到「每個數字出現偶數次，找出現奇數次的」→ XOR
- 需要「O(1) 空間找唯一元素」→ 位元操作
- 2 的冪次判斷 → `n & (n-1)`
- 需要按 bit 計數 → 逐 bit 遍歷

## 注意事項

- C++ 中 `int` 是有符號的，右移 `>>` 對負數的行為是「算術右移」（高位補符號位）。若需要邏輯右移，用 `unsigned int` 或 `uint32_t`
- `~x` 在有符號整數上等於 `-(x+1)`（兩補數表示）

## 自我檢核

- [ ] 知道 `x & (x-1)` 的作用（清除最低位 1）
- [ ] 能用 XOR 解 Single Number（一行）
- [ ] 能解釋 Counting Bits 的 DP 轉移式 `dp[i] = dp[i>>1] + (i&1)`
- [ ] 知道 Power of Two 的位元技巧

→ [Ch 37 數學技巧：GCD、快速冪、質數篩](./37-math-tricks.md)
