# Ch 22 — Bit Manipulation

> 目標:熟練位元操作的常見 idiom,辨識「XOR 配對」「bitmask 枚舉」等典型題。

## 基本操作速查

| 操作 | 寫法 | 效果 |
|---|---|---|
| 取第 i 位 | `(x >> i) & 1` | 1 或 0 |
| 設第 i 位 | `x \| (1 << i)` | 把第 i 位設成 1 |
| 清第 i 位 | `x & ~(1 << i)` | 把第 i 位設成 0 |
| 翻第 i 位 | `x ^ (1 << i)` | 0/1 互換 |
| 最低位的 1 | `x & -x` | 例如 `0b1100 → 0b0100` |
| 清最低位的 1 | `x & (x - 1)` | 例如 `0b1100 → 0b1000` |
| popcount | `bin(x).count('1')` 或 `x.bit_count()`(3.10+) | 1 的個數 |
| 判斷 2 的冪 | `x > 0 and x & (x-1) == 0` | |

## XOR 的三個性質

**面試題 90% 的位元題都靠 XOR 的這三性質**:

1. `x ^ 0 = x`
2. `x ^ x = 0`
3. 可交換、可結合

**推論**:一串數字做 XOR,重複的互相消掉,剩下獨一無二的。

---

## 經典題:XOR 找獨特數

### Single Number (136)

> 其他數都出現兩次,找只出現一次的。

```python
def single_number(nums):
    x = 0
    for n in nums: x ^= n
    return x
```

O(n) 時間,O(1) 空間。

### Single Number III (260)

> 其他數都出現兩次,有**兩個**只出現一次的。

```python
def single_number_iii(nums):
    xor_all = 0
    for n in nums: xor_all ^= n
    # xor_all = a ^ b,兩不同數的 XOR 必有某位為 1
    diff_bit = xor_all & -xor_all    # 最低位的 1
    a = 0
    for n in nums:
        if n & diff_bit:
            a ^= n
    return [a, xor_all ^ a]
```

**心法**:用某一位 bit 把 nums 分成兩組,每組只含一個單身。分組後各組 XOR 互抵消。

### Missing Number (268)

> `[0, n]` 少了一個。

```python
def missing_number(nums):
    x = len(nums)
    for i, n in enumerate(nums):
        x ^= i ^ n
    return x
```

XOR(所有 index + n)^ XOR(所有值)= 缺的那個。

---

## 經典題:bitmask 枚舉子集

### Subsets (78) 的位元解

```python
def subsets(nums):
    n = len(nums)
    res = []
    for mask in range(1 << n):
        subset = [nums[i] for i in range(n) if (mask >> i) & 1]
        res.append(subset)
    return res
```

**枚舉所有子集 = 枚舉所有 bitmask**。n ≤ 20 可行。

### 枚舉 mask 的子集

這是進階技巧,對給定 mask 枚舉其所有子集(包括 mask 本身和 0):

```python
sub = mask
while sub:
    # 處理 sub
    sub = (sub - 1) & mask
# 記得處理 sub = 0
```

**複雜度**:所有 `mask` 的所有 `sub` 合起來是 **O(3^n)**(每個 bit 位於 mask 內/外/sub 內三態)。

這個枚舉在某些狀壓 DP 必用(如「分組」類)。

---

## 經典題:整數運算位元化

### Counting Bits (338)

> 對每個 `0..n`,數 binary 中 1 的個數。

**DP**:`dp[i] = dp[i >> 1] + (i & 1)`。把最低位拆出來,剩下的是 `i >> 1` 的問題。

```python
def count_bits(n):
    dp = [0] * (n + 1)
    for i in range(1, n + 1):
        dp[i] = dp[i >> 1] + (i & 1)
    return dp
```

### Sum of Two Integers (371,不用 + 做加法)

用 XOR 模擬「不進位加」,AND + shift 模擬「進位」:

```python
def sum_two(a, b):
    MASK = 0xFFFFFFFF
    while b:
        carry = ((a & b) << 1) & MASK
        a = (a ^ b) & MASK
        b = carry
    return a if a < 0x80000000 else ~(a ^ MASK)
```

**Python 模擬 32-bit 要小心**——`int` 是無限位,用 MASK 模擬溢位和符號位。

### Reverse Bits (190)

```python
def reverse_bits(n):
    res = 0
    for _ in range(32):
        res = (res << 1) | (n & 1)
        n >>= 1
    return res
```

---

## 經典題:XOR 進階

### Maximum XOR (421,Ch 8 trie 解已寫)

另一種看問題的角度:trie + 貪婪。

### XOR Queries of a Subarray (1310)

**前綴 XOR**。和前綴和一樣的 pattern:

```python
prefix = [0]
for x in arr:
    prefix.append(prefix[-1] ^ x)
# query [l, r] 的 XOR = prefix[r+1] ^ prefix[l]
```

### Subarray XOR = k(類似 Subarray Sum = k)

前綴 XOR + hash。跟 Ch 4 的 Subarray Sum 一樣的結構。

---

## Python 的特殊情況

**Python int 是無限位**,沒有 signed 32-bit 的概念。寫 bit 題時:

```python
# 需要 32-bit mask
MASK = 0xFFFFFFFF
x = x & MASK
```

**負數的 shift**:Python `-1 >> 1` 是 `-1`(arithmetic shift),`-1 & 0xFF` 是 `255`(取低 8 位)。跟 C 行為不同。

---

## 訊號速查

| 題目訊號 | 考慮 |
|---|---|
| 「出現偶數次,找單身」 | XOR |
| 「子集枚舉」+ n 小 | bitmask for mask in range(1 << n) |
| 「異或最大 / 最小」 | trie on bits |
| 「3 的冪」「2 的冪」 | `x & (x - 1) == 0` 等 trick |
| 「popcount」 | `bin(x).count('1')` |

---

## 自我檢核

- [ ] `x & -x` 的效果?
- [ ] Single Number III 怎麼用一個 XOR 結果分組?
- [ ] 用 bitmask 枚舉 n 個元素的所有子集,時間複雜度?
- [ ] 枚舉 mask 的所有子集,總複雜度為什麼是 O(3^n) 而不是 O(2^n × 2^n)?
- [ ] Python 的 int 在 bit 操作上跟 C/Java 有什麼不同?

→ [Ch 23 Interval Problems](./23-intervals.md)
