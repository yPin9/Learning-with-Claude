# Ch 20 — RSA 攻擊

> 目標：能實作 Wiener / Hastad / Bleichenbacher 三個攻擊，理解每個打的是 RSA 的什麼弱點。

---

## 為什麼需要這章

Ch 19 的 RSA 看起來很堅固——把 2048-bit 的 n 分解需要天文數字的運算量。但攻擊者不一定要分解 n。RSA 的實務部署中有大量「用錯」的情形：private exponent 太小、public exponent 太小、padding 有 oracle——每種錯誤都有對應的攻擊。

本章涵蓋四個經典攻擊：

| 攻擊 | 打什麼弱點 | 年份 | 複雜度 |
|---|---|---|---|
| Wiener | d 太小（d < n^0.25 / 3） | 1990 | 多項式時間 |
| Hastad Broadcast | e=3 + 同 message 發 3 人 | 1985 | 多項式時間 |
| Common Modulus | 兩人共用 n、不同 e | — | O(log n) |
| Bleichenbacher | PKCS#1 v1.5 的 padding oracle | 1998 | ~2^20 次 oracle 查詢 |

---

## 先建立直覺

RSA 的數學是正確的——m^(ed) ≡ m (mod n)。攻擊者沒辦法推翻歐拉定理。但攻擊者可以利用：

1. **參數選擇錯誤**：d 太小 → 連分數逼近可以直接算出 d
2. **使用方式錯誤**：同一個 m 用不同公鑰加密多次 → CRT 復原
3. **協議設計錯誤**：padding 的結構洩露了「是否合法」的資訊 → oracle 攻擊

這些攻擊的共同教訓：**密碼系統的安全性不只在於數學正確，還在於使用方式正確**。

---

## 攻擊一：Wiener Attack

### 什麼情況下 d 會太小

有人為了加速解密，選一個很小的 d。因為 ed ≡ 1 (mod φ(n)) 會讓 e 很大，但加密用的是 e，大的 e 會讓加密變慢——所以這個人同時犧牲了安全性和加密速度。

另一種情形：某些 key generation 的 bug 導致 d 比預期小。

### Wiener 的定理

**定理**（Wiener, 1990）：若 d < n^(1/4) / 3，且 q < p < 2q，則可以從 (n, e) 高效恢復 d。

### 數學原理

```
ed ≡ 1 (mod φ(n))  →  ed = 1 + k·φ(n)

除以 d·φ(n)：
  e/φ(n) = k/d + 1/(d·φ(n))

因為 φ(n) ≈ n（精確地說 |n - φ(n)| < 3√n），所以：
  e/n ≈ k/d

當 d 很小時，k/d 是 e/n 的一個「好的有理逼近」
→ k/d 會出現在 e/n 的連分數展開的收斂子（convergent）中
```

### 連分數（Continued Fractions）速覽

```
任何實數都可以表示成連分數：
  x = a₀ + 1/(a₁ + 1/(a₂ + 1/(a₃ + ...)))

記作 [a₀; a₁, a₂, a₃, ...]

第 i 個收斂子 pᵢ/qᵢ 是對 x 的最佳有理逼近。

關鍵性質（Legendre）：
  如果 |x - a/b| < 1/(2b²)，則 a/b 是 x 的某個收斂子。
```

### Python PoC：Wiener Attack

```python
"""
Wiener's Attack on RSA with small d
"""
from math import isqrt, gcd


def continued_fraction(num, den):
    """連分數展開 [a0; a1, a2, ...]"""
    cf = []
    while den:
        q = num // den
        cf.append(q)
        num, den = den, num - q * den
    return cf

def convergents(cf):
    """從連分數算所有收斂子 (p_i, q_i)"""
    convs = []
    h0, h1, k0, k1 = 0, 1, 1, 0
    for a in cf:
        h0, h1 = h1, a * h1 + h0
        k0, k1 = k1, a * k1 + k0
        convs.append((h1, k1))
    return convs


def wiener_attack(e, n):
    """
    嘗試用 Wiener attack 恢復 d。
    成功回傳 d，失敗回傳 None。
    """
    cf = continued_fraction(e, n)
    convs = convergents(cf)

    for k, d in convs:
        if k == 0 or d == 0:
            continue

        # 如果 k/d 是正確的 k/d，那麼：
        # ed = 1 + k·φ(n) → φ(n) = (ed - 1) / k
        if (e * d - 1) % k != 0:
            continue

        phi = (e * d - 1) // k

        # φ(n) = n - p - q + 1 → p + q = n - φ(n) + 1
        s = n - phi + 1  # s = p + q
        # p 和 q 是 x² - sx + n = 0 的根
        discriminant = s * s - 4 * n
        if discriminant < 0:
            continue

        sqrt_disc = isqrt(discriminant)
        if sqrt_disc * sqrt_disc != discriminant:
            continue

        p = (s + sqrt_disc) // 2
        q = (s - sqrt_disc) // 2

        if p * q == n:
            return d, p, q

    return None


# === 演示（不需要外部套件）===
print("=== Wiener Attack 演示 ===")
# 已知弱 RSA 參數
n = 90581
e = 17993

result = wiener_attack(e, n)
if result:
    d_found, p_found, q_found = result
    print(f"n = {n}")
    print(f"e = {e}")
    print(f"找到 d = {d_found}")
    print(f"找到 p = {p_found}, q = {q_found}")
    print(f"驗證：p × q = {p_found * q_found}")

    m = 42
    c = pow(m, e, n)
    m_dec = pow(c, d_found, n)
    print(f"加密 m={m} → c={c}")
    print(f"解密 → m={m_dec}")
```

---

## 攻擊二：Hastad Broadcast Attack

### 攻擊場景

同一個明文 m 被用三個不同的公鑰（都用 e=3）加密：

```
Alice 公鑰 (n1, 3) → c1 = m³ mod n1
Bob   公鑰 (n2, 3) → c2 = m³ mod n2
Carol 公鑰 (n3, 3) → c3 = m³ mod n3

攻擊者知道 c1, c2, c3 和 n1, n2, n3
```

### 為什麼能攻擊

用中國剩餘定理（CRT）：

```
已知：
  m³ ≡ c1 (mod n1)
  m³ ≡ c2 (mod n2)
  m³ ≡ c3 (mod n3)

因為 n1, n2, n3 兩兩互質（各自的質因數不同），
CRT 保證存在唯一的 X ∈ [0, n1·n2·n3) 使得：
  X ≡ c1 (mod n1)
  X ≡ c2 (mod n2)
  X ≡ c3 (mod n3)

X = m³
因為 m < min(n1, n2, n3)，所以 m³ < n1·n2·n3
→ X = m³ 是一個「普通」整數（不是模運算的結果）
→ 直接開三次根號：m = X^(1/3)
```

### Python PoC：Hastad Broadcast Attack

```python
"""
Hastad's Broadcast Attack (e=3, 3 recipients)
"""
from math import gcd


def extended_gcd(a, b):
    if a == 0:
        return b, 0, 1
    g, x, y = extended_gcd(b % a, a)
    return g, y - (b // a) * x, x


def crt(remainders, moduli):
    """
    中國剩餘定理：解聯立同餘方程
    x ≡ r_i (mod m_i) for all i
    """
    M = 1
    for m in moduli:
        M *= m

    x = 0
    for r, m in zip(remainders, moduli):
        Mi = M // m
        _, Mi_inv, _ = extended_gcd(Mi % m, m)
        x += r * Mi * Mi_inv

    return x % M


def integer_cube_root(n):
    """精確整數三次根號（Newton's method）"""
    if n <= 0: return 0
    x = 1 << ((n.bit_length() + 2) // 3)
    while True:
        x1 = (2 * x + n // (x * x)) // 3
        if x1 >= x: break
        x = x1
    while x**3 > n: x -= 1
    while (x+1)**3 <= n: x += 1
    return x


def hastad_broadcast_attack(ciphertexts, moduli, e=3):
    """
    Hastad broadcast attack。
    需要 e 個密文（用不同公鑰加密同一個明文）。
    """
    assert len(ciphertexts) == e and len(moduli) == e

    # 用 CRT 恢復 m^e
    m_e = crt(ciphertexts, moduli)

    # 開 e 次根號
    m = integer_cube_root(m_e)

    # 驗證
    if m ** e == m_e:
        return m
    else:
        return None


# === 演示（小參數）===
n1, n2, n3, e = 55, 91, 187, 3  # 5×11, 7×13, 11×17
m = 10
c1, c2, c3 = pow(m,3,n1), pow(m,3,n2), pow(m,3,n3)
m_recovered = hastad_broadcast_attack([c1,c2,c3], [n1,n2,n3], e)
print(f"m={m}, recovered={m_recovered}, success={m_recovered==m}")

```

---

## 攻擊三：Common Modulus Attack

### 攻擊場景

一個組織為了「方便管理」，讓所有員工共用同一個 n，但各自有不同的 (e_i, d_i)。

如果同一個明文 m 被兩個不同的 e 加密（且 gcd(e1, e2) = 1）：

```
c1 = m^e1 mod n
c2 = m^e2 mod n

因為 gcd(e1, e2) = 1，由 Bezout identity：
存在 s, t 使得 e1·s + e2·t = 1

c1^s × c2^t = m^(e1·s) × m^(e2·t) = m^(e1·s + e2·t) = m^1 = m  (mod n)
```

### Python PoC

```python
"""
Common Modulus Attack
"""
def common_modulus_attack(c1, c2, e1, e2, n):
    """
    已知同一個 m 被 (n,e1) 和 (n,e2) 加密，
    且 gcd(e1, e2) = 1，恢復 m。
    """
    def ext_gcd(a, b):
        if a == 0:
            return b, 0, 1
        g, x, y = ext_gcd(b % a, a)
        return g, y - (b // a) * x, x

    g, s, t = ext_gcd(e1, e2)
    assert g == 1, "gcd(e1, e2) must be 1"

    # 如果 s 或 t 是負數，需要用模逆元
    if s < 0:
        c1 = pow(c1, -1, n)  # c1 的模逆元
        s = -s
    if t < 0:
        c2 = pow(c2, -1, n)
        t = -t

    m = (pow(c1, s, n) * pow(c2, t, n)) % n
    return m


# 演示
n, e1, e2, m = 3233, 17, 23, 42  # n = 61 × 53
c1, c2 = pow(m, e1, n), pow(m, e2, n)
m_recovered = common_modulus_attack(c1, c2, e1, e2, n)
print(f"n={n}, e1={e1}, e2={e2}, m_recovered={m_recovered}, success={m_recovered==m}")
```

教訓：**永遠不要讓多個使用者共用同一個 n**。每人應該獨立生成自己的 key pair。

---

## 攻擊四：Bleichenbacher 1998（PKCS#1 v1.5 Padding Oracle）

### 背景

PKCS#1 v1.5 的 padding 格式：

```
EM = 00 || 02 || PS || 00 || M

PS = 至少 8 bytes 隨機非零填充
M  = 明文

合法條件：
  1. 第一個 byte 是 0x00
  2. 第二個 byte 是 0x02
  3. PS 至少 8 bytes，都非零
  4. PS 後面有一個 0x00 分隔符
```

### Oracle 的存在

當 server 收到密文 c 後：
1. 解密得到 m = c^d mod n
2. 檢查 m 是否符合 PKCS#1 v1.5 格式
3. 如果格式不對 → 回傳錯誤（或行為不同）

攻擊者可以觀察到 **「格式對不對」** 這一個 bit 的資訊。Bleichenbacher 證明這一個 bit 足以逐步收窄明文的範圍，直到完全恢復明文。

### 攻擊原理

```
RSA 的 multiplicative homomorphism：
  如果 c = m^e mod n
  那麼 c' = c × s^e mod n = (m × s)^e mod n

→ 攻擊者可以把密文「乘以 s」，改變對應的明文

Bleichenbacher 的策略：
  選擇不同的 s 值，把 c 乘以 s^e
  送給 server，觀察 server 回傳「padding 合法」還是「padding 不合法」
  每次「padding 合法」就縮小 m 的可能範圍

合法 padding 意味著：
  2B ≤ m × s mod n < 3B
  其中 B = 2^(8(k-2))，k = key 的 byte 數

經過約 2^20 次 oracle 查詢（RSA-2048），可以完全恢復 m
```

完整 PoC 見練習 C（Phase 4）。這裡只展示流程圖：

### Bleichenbacher 攻擊流程圖

```
攻擊者                              Server（有 oracle）
  │                                      │
  │ 有密文 c = m^e mod n                  │
  │ 目標：恢復 m                          │
  │                                      │
  │ Step 1: 找 s₁ 使 oracle(c·s₁ᵉ)=True  │
  │──── c' = c·s₁ᵉ mod n ──────────────>│
  │<──── "padding invalid" ──────────────│
  │──── c' = c·s₂ᵉ mod n ──────────────>│
  │<──── "padding valid!" ───────────────│
  │                                      │
  │ 知道 m·s₂ mod n ∈ [2B, 3B)           │
  │ → m 的範圍縮小了！                     │
  │                                      │
  │ Step 2: 繼續找更大的 s               │
  │ 每次 "valid" 都把 m 的範圍           │
  │ 切成更小的區間                        │
  │                                      │
  │ ...（約 2^20 次查詢後）...             │
  │                                      │
  │ m 的範圍縮小到唯一的值                 │
  │ 攻擊成功！                            │
```

---

## 底層機制：各攻擊的前提條件整理

| 攻擊 | 需要什麼 | 攻擊者能力 |
|---|---|---|
| Wiener | d < n^(1/4)/3 | 只需 (n, e) |
| Hastad | e 個相同明文、不同 n | 只需公鑰和密文 |
| Common Modulus | 共用 n + gcd(e1,e2)=1 | 只需密文和公鑰 |
| Bleichenbacher | PKCS#1 v1.5 + padding oracle | 需要 oracle access |

---

## 對比與取捨

### 防禦措施

| 攻擊 | 防禦 |
|---|---|
| Wiener | d 必須足夠大——用標準 key generation（d ≈ n） |
| Hastad | 使用 OAEP padding（加隨機性，每次不同） |
| Common Modulus | 每人獨立生成 key pair |
| Bleichenbacher | 用 OAEP 取代 PKCS#1 v1.5；或 constant-time 處理（不洩露 padding 是否合法） |

### TLS 1.3 的根治

TLS 1.3 直接**移除了 RSA key transport**——不再用 RSA 加密 pre-master secret，改用 ECDHE key exchange。Bleichenbacher 攻擊的前提（server 解密 RSA 密文並檢查 padding）不存在了。

但 TLS 1.2 和更舊的版本仍然支援 RSA key transport。2018 年的 ROBOT 攻擊（Return Of Bleichenbacher's Oracle Threat）發現許多 TLS 實作仍然有 padding oracle。

---

## 踩雷集錦

### 雷 1：以為用了 OAEP 就不用擔心

OAEP 防的是 chosen-ciphertext 攻擊。但如果你的 OAEP 實作有 timing side-channel（例如 error message 的時間不同），攻擊者仍然可以利用。Manger 2001 展示了針對 OAEP 的 timing oracle 攻擊。

### 雷 2：Bleichenbacher 的「修補」不完整

很多 TLS 實作嘗試修補 Bleichenbacher：如果 padding 不合法，用隨機值繼續。但如果 error handling 的時間不同，oracle 仍然洩露。

### 雷 3：e=3 搭配 OAEP 其實安全

Hastad broadcast attack 打的是 **textbook RSA**（沒有 padding）。如果用了 OAEP，每次加密的 padded message 不同，CRT 恢復的值也不同——攻擊失敗。

但 e=65537 仍然是更穩的選擇（defense in depth）。

### 雷 4：以為「我不用 e=3 就沒事」

Wiener attack 跟 e 的大小無關——它打的是 d 太小。而且 Boneh-Durfee attack（1999）把 Wiener 的 bound 從 d < n^0.25 推到 d < n^0.292。

### 雷 5：Common Modulus 的變種

即使 gcd(e1, e2) ≠ 1，只要 gcd(e1, e2) = g 很小，攻擊者可以恢復 m^g，然後嘗試開 g 次根。

---

## 進階

### Coppersmith's Method（1996）

Coppersmith 提出用 lattice reduction（LLL 演算法）找「模多項式的小根」。應用範圍極廣：

- **小 e + 部分明文已知**：如果知道明文的一半，可以恢復另一半
- **小 e + short pad**：padding 太短時，兩個密文可以互相解
- **Factoring with partial knowledge**：如果知道 p 的一半 bit，可以分解 n

```python
# SageMath 中的 Coppersmith（概念）
# P.<x> = PolynomialRing(Zmod(n))
# f = (known_part + x)^e - c
# f.small_roots(X=2^unknown_bits, beta=0.5)
```

### Boneh-Durfee Attack（1999）

改進 Wiener 的 bound：d < n^0.292 時仍可恢復 d。用的是 Coppersmith 的 lattice method。

### ROBOT（2018）

2018 年 ROBOT（Return Of Bleichenbacher's Oracle Threat）測試 Alexa Top 100，發現約 27% 仍有 Bleichenbacher oracle——修補不完整、TLS 加速器 firmware bug、OpenSSL 引入新 side-channel。二十年後攻擊仍在野外存活。

---

## 動手練習

1. **Wiener Attack 實戰**：用 `sympy` 生成 1024-bit RSA key（d < n^0.25/3），跑 Wiener attack 恢復 d。

2. **Hastad Attack 變種**：改成 e=5，用 5 個不同的公鑰加密同一個明文。需要 5 份密文的 CRT + 開五次根號。

3. **Common Modulus**：用兩個不同的 e 加密同一個明文，驗證 Bezout identity 可以恢復 m。

4. **CryptoHack 挑戰**：去 https://cryptohack.org/challenges/rsa/ 打 RSA 相關的題目。

5. **思考題**：為什麼 TLS 1.3 移除 RSA key transport 可以同時解決 Bleichenbacher 攻擊和 forward secrecy 的問題？

---

## 重點整理

```
Wiener Attack（d 太小）：
  條件：d < n^(1/4) / 3
  方法：e/n 的連分數展開 → 收斂子裡找到 k/d
  防禦：用標準 key generation（d ≈ n）

Hastad Broadcast Attack（e=3 + 重複明文）：
  條件：同 m 用 e 個不同公鑰加密
  方法：CRT 恢復 m^e → 開 e 次根
  防禦：使用 OAEP padding

Common Modulus Attack（共用 n）：
  條件：兩人共用 n，不同 e，gcd(e1,e2)=1
  方法：Bezout identity → c1^s × c2^t = m
  防禦：每人獨立生成 key pair

Bleichenbacher 1998（Padding Oracle）：
  條件：PKCS#1 v1.5 + oracle 可判斷 padding 合法性
  方法：利用 RSA 的 multiplicative homomorphism + oracle 縮小 m 範圍
  ~2^20 次 oracle 查詢恢復 RSA-2048 的明文
  防禦：用 OAEP；或 constant-time 處理 padding error

根治：TLS 1.3 移除 RSA key transport
```

---

## 自我檢核

- [ ] 我能解釋 Wiener attack 用連分數打的是什麼弱點
- [ ] 我能手算 Hastad broadcast attack（小參數 + CRT + 開根號）
- [ ] 我能解釋 Common Modulus attack 用的 Bezout identity
- [ ] 我能畫出 Bleichenbacher 攻擊的流程圖
- [ ] 我知道每個攻擊對應的防禦措施
- [ ] 我能解釋 RSA 的 multiplicative homomorphism 為什麼是 Bleichenbacher 攻擊的核心
- [ ] 我知道 TLS 1.3 如何根治 RSA key transport 的問題

---

## 延伸閱讀

- **"Cryptanalysis of Short RSA Secret Exponents"**（Wiener, 1990）：連分數攻擊的原始論文
- **"Chosen Ciphertext Attacks Against Protocols Based on the RSA Encryption Standard PKCS#1"**（Bleichenbacher, 1998）：padding oracle 攻擊的原始論文
- **"Return Of Bleichenbacher's Oracle Threat (ROBOT)"**（Böck et al., 2018）：二十年後 Bleichenbacher 仍然在野外存活
- **"Twenty Years of Attacks on the RSA Cryptosystem"**（Boneh, 1999）：Dan Boneh 的經典攻擊總覽

---

## 下一章連結

[Ch 21 — DH 細節 + Logjam](./21-dh-details-logjam.md)：回到 Diffie-Hellman，深入 safe prime、small subgroup attack、Logjam 降級攻擊——DH 的「用錯」跟 RSA 一樣致命。
