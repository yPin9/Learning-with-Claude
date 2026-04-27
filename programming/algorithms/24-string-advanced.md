# Ch 24 — 字串進階:KMP / Rabin-Karp

> 目標:知道 KMP 的核心思想和失敗函數、Rabin-Karp 的 rolling hash。面試偶爾考,但寫得出來很加分。

## 為什麼要這些

**樸素字串匹配**:對 text 每個位置嘗試匹配 pattern,最壞 O(n × m)。

進階演算法把它降到 O(n + m) 或平均 O(n + m)。

---

## KMP

### 核心思想

**失配時,利用 pattern 自身的重複,跳過已經匹配過的部分,不從頭重試**。

關鍵:**失敗函數 `fail[i]`** = pattern[:i] 的「最長相等前綴後綴長度」。

### 失敗函數建構

```python
def build_fail(p):
    n = len(p)
    fail = [0] * n
    k = 0
    for i in range(1, n):
        while k > 0 and p[k] != p[i]:
            k = fail[k - 1]
        if p[k] == p[i]:
            k += 1
        fail[i] = k
    return fail
```

### 匹配主體

```python
def kmp_search(text, pattern):
    if not pattern: return 0
    fail = build_fail(pattern)
    j = 0
    for i in range(len(text)):
        while j > 0 and text[i] != pattern[j]:
            j = fail[j - 1]
        if text[i] == pattern[j]:
            j += 1
        if j == len(pattern):
            return i - j + 1    # 找到,返回位置
    return -1
```

**複雜度 O(n + m)**。面試不要求秒背,但能寫出 + 講出 fail 的定義就夠了。

### 什麼時候用 KMP

- LeetCode 28 (Find the Index of First Occurrence)
- Repeated Substring Pattern (459)
- Shortest Palindrome (214)

**但 Python 有 `str.find()` 和 `in`**,面試通常接受直接用。KMP 會在「面試官追問能否 O(n+m)」時派上用場。

### Repeated Substring Pattern 的 KMP 解

> 字串是否由某子串重複構成。

```python
def repeated_substring(s):
    fail = build_fail(s)
    n = len(s)
    # s 由長度 n - fail[-1] 的子串重複組成,前提是 n 能被該長度整除
    return fail[-1] > 0 and n % (n - fail[-1]) == 0
```

---

## Rabin-Karp(Rolling Hash)

### 核心思想

**用 hash 值比較,不逐字元比對**。rolling hash 讓滑動視窗的 hash 更新為 O(1)。

### Rolling Hash

```python
def rabin_karp(text, pattern):
    if len(pattern) > len(text): return -1
    BASE = 26
    MOD = 10 ** 9 + 7
    m = len(pattern)
    p_hash = 0
    t_hash = 0
    for i in range(m):
        p_hash = (p_hash * BASE + ord(pattern[i])) % MOD
        t_hash = (t_hash * BASE + ord(text[i])) % MOD

    power = pow(BASE, m - 1, MOD)

    for i in range(len(text) - m + 1):
        if p_hash == t_hash and text[i:i+m] == pattern:    # hash 相等再確認
            return i
        if i + m < len(text):
            t_hash = (t_hash - ord(text[i]) * power) % MOD
            t_hash = (t_hash * BASE + ord(text[i + m])) % MOD
            t_hash %= MOD
    return -1
```

**三個細節**:

1. **Hash 相等才看實字串**:避免 hash 碰撞誤判。
2. **MOD 用大質數**:降低碰撞機率。
3. **Rolling update**:減前、進後、乘 base。

### Rabin-Karp 的典型應用

**多 pattern 匹配**、**重複子串檢測**。單一 pattern 匹配用 KMP 更穩(無碰撞風險)。

### Longest Duplicate Substring (1044, Hard)

> 找最長的「出現至少兩次」的子串。

**二分答案 + rolling hash**:

- 二分長度 L
- check(L):看有沒有長度 L 的子串重複。用 rolling hash,建一個 set,掃所有長度 L 子串。

```python
def longest_dup_substring(s):
    n = len(s)
    MOD = (1 << 61) - 1
    BASE = 26

    def check(L):
        h = 0
        power = pow(BASE, L, MOD)
        seen = {}
        for i in range(L):
            h = (h * BASE + ord(s[i])) % MOD
        seen[h] = 0
        for i in range(1, n - L + 1):
            h = (h * BASE - ord(s[i-1]) * power + ord(s[i + L - 1])) % MOD
            if h in seen and s[seen[h]:seen[h]+L] == s[i:i+L]:
                return i
            seen[h] = i
        return -1

    lo, hi = 1, n
    res_idx = -1
    res_len = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        idx = check(mid)
        if idx != -1:
            res_idx, res_len = idx, mid
            lo = mid + 1
        else:
            hi = mid - 1
    return s[res_idx:res_idx + res_len] if res_idx != -1 else ""
```

這題是面試殺手級 Hard,能寫出的人很少。大部分面試不要求,但能提到這個思路就是加分。

---

## Z-Algorithm(了解即可)

另一個 O(n) 字串匹配演算法,概念類似 KMP 但更好寫(有人主張)。面試幾乎不考,跳過。

---

## Manacher's Algorithm(了解即可)

**O(n) 找所有回文子串**。用中心擴展 + 鏡像。寫對的人少,面試遇到也可以用 O(n²) 中心擴展就過。

---

## 什麼面試該寫這些?

Onsite 遇到「字串匹配」又有明確 O(n+m) 要求 → KMP。

「找某長度的重複子串」→ 二分 + rolling hash。

其他時候,Python 的 `in` / `str.find` / `Counter` 通常夠用。**不要為了炫技寫 KMP 解 Easy 題**。

---

## 自我檢核

- [ ] KMP 的失敗函數 `fail[i]` 的定義?
- [ ] 為什麼 KMP 匹配過程的均攤複雜度是 O(n + m)?
- [ ] Rabin-Karp 的 rolling hash 如何 O(1) 更新?
- [ ] 為什麼 hash 相等還要確認字串相等?
- [ ] Longest Duplicate Substring 的二分 + hash 思路,怎麼判斷二分的單調性?

→ [Ch 25 數學與數論](./25-math.md)
