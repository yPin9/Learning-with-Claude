# Ch 37 — 數學技巧：GCD、快速冪、質數篩

> 目標：掌握面試常用的三個數學工具，理解它們的原理而不是死背公式。

## 1. GCD（最大公因數）

**Euclidean Algorithm**：`gcd(a, b) = gcd(b, a % b)`，直到 `b = 0`。

```cpp
int gcd(int a, int b) {
    return b == 0 ? a : gcd(b, a % b);
}

// 或用 C++17 的 std::gcd
#include <numeric>
int g = gcd(48, 18);  // = 6
```

**為什麼正確？**

若 `d | a` 且 `d | b`，則 `d | (a % b)`（因為 `a % b = a - (a/b)*b`）。
所以 `gcd(a, b)` 的所有公因數 = `gcd(b, a%b)` 的所有公因數。

**LCM（最小公倍數）**：`lcm(a, b) = a / gcd(a, b) * b`（先除再乘，避免溢位）。

## 應用：Fraction Addition（分數加減）

```cpp
// 分數 a/b + c/d = (a*d + c*b) / (b*d)，然後化簡
int g = gcd(abs(numerator), denominator);
numerator /= g;
denominator /= g;
```

## 2. 快速冪（Fast Exponentiation）

計算 `a^n mod m`。樸素做法 O(N)，快速冪 O(log N)。

**原理**：
- 若 n 是偶數：`a^n = (a^(n/2))^2`
- 若 n 是奇數：`a^n = a * a^(n-1)`

每次把 n 減半，共 O(log N) 步。

```cpp
long long fastPow(long long base, long long exp, long long mod) {
    long long result = 1;
    base %= mod;

    while (exp > 0) {
        if (exp & 1)           // exp 是奇數
            result = result * base % mod;
        base = base * base % mod;
        exp >>= 1;
    }
    return result;
}
```

**應用**：Pow(x, n)（LeetCode 50）、RSA 加密、模逆元。

處理負指數：`x^(-n) = (1/x)^n`，C++ 中用 `1.0 / fastPow(x, -n, ...)` 或特殊處理。

## 3. 質數篩（Sieve of Eratosthenes）

找出 [2, n] 中所有質數。

**原理**：從最小的質數 2 開始，把所有它的倍數標為合數；再找下一個未被標記的數（即質數），繼續篩。

```cpp
vector<bool> sieve(int n) {
    vector<bool> isPrime(n+1, true);
    isPrime[0] = isPrime[1] = false;

    for (int i = 2; i * i <= n; i++) {  // 只需到 sqrt(n)
        if (isPrime[i]) {
            for (int j = i*i; j <= n; j += i)  // 從 i² 開始（i² 之前的倍數已被更小的質數篩掉）
                isPrime[j] = false;
        }
    }
    return isPrime;
}
```

時間複雜度：O(N log log N)，空間 O(N)。

**Count Primes（LeetCode 204）**：直接用 sieve，統計 `true` 的個數。

## 4. 常用數學技巧

**整除向上取整**：`ceil(a / b) = (a + b - 1) / b`（整數運算，不用浮點）

```cpp
int hours = (minutes + 59) / 60;  // 向上取整到小時
```

**判斷完全平方數**：`sqrt(n)` 取整後再平方看是否等於 n（注意浮點誤差）：

```cpp
bool isPerfectSquare(int n) {
    long sq = sqrt((double)n);
    return sq * sq == n || (sq+1)*(sq+1) == n;
}
```

**模運算性質**：`(a + b) % m = ((a % m) + (b % m)) % m`

**常見陷阱**：C++ 中 `-7 % 3 = -1`（C++ 對負數取模結果可能是負數），若需要非負餘數：

```cpp
int mod = ((x % m) + m) % m;
```

## 自我檢核

- [ ] 能寫出 GCD 的遞迴（一行）並解釋為什麼正確
- [ ] 能從頭寫出快速冪（迭代版，含取模）
- [ ] 能寫出質數篩，並說出「從 i² 開始」的原因
- [ ] 知道整數向上取整的公式 `(a+b-1)/b`

→ [Ch 38 排列組合：計數類題入門](./38-combinatorics.md)
