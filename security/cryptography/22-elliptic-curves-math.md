# Ch 22 — 橢圓曲線數學

> 目標：能在 GF(p) 上做 EC point addition/doubling，理解 ECDLP，知道 Curve25519 的設計選擇。

---

## 為什麼需要橢圓曲線

Ch 18-21 的 finite-field DH 有兩個問題：

1. **Key 太長**：2048-bit DH 才等效 112-bit 安全；3072-bit 才到 128-bit
2. **太慢**：2048-bit 模冪在行動裝置上耗時明顯

橢圓曲線密碼學（Elliptic Curve Cryptography, ECC）用 **256 bit 就達到 128-bit 安全**——key 短 12 倍，運算快 10-50 倍。

```
等效安全性比較：

對稱 key    RSA / DH      ECC
──────────────────────────────
80 bit      1024 bit     160 bit
112 bit     2048 bit     224 bit
128 bit     3072 bit     256 bit    ← 目前標準
192 bit     7680 bit     384 bit
256 bit     15360 bit    512 bit
```

為什麼差這麼多？因為對 finite-field DLP，有 **index calculus** 等 sub-exponential 演算法；但對 ECDLP，目前最好的通用攻擊仍然是 **Pollard's rho**（O(√n)，fully exponential）。

---

## 先建立直覺

### 實數上的橢圓曲線

橢圓曲線的 Weierstrass 方程：**y² = x³ + ax + b**

```
                    y
                    │      ╭───╮
                    │     ╱     ╲
                    │    ╱       ╲      ← 上半部
                    │   │         │
          ──────────┼───┼─────────┼──────── x
                    │   │         │
                    │    ╲       ╱      ← 下半部（對稱）
                    │     ╲     ╱
                    │      ╰───╯
                    │
```

關鍵觀察：
- 曲線關於 x 軸**對稱**（因為是 y²）
- 曲線是**光滑**的（沒有 cusp 或 self-intersection，條件：4a³ + 27b² ≠ 0）
- 曲線上的點可以做**加法**——但不是普通的向量加法

### Point Addition 的幾何直覺

```
在實數上的橢圓曲線 point addition：P + Q = R

  1. 畫一條直線通過 P 和 Q
  2. 這條直線會跟曲線交於第三個點 R'
  3. 把 R' 關於 x 軸翻轉，得到 R = P + Q

                y
                │
            R'  ●───────────────● P
                │ ╲           ╱
                │  ╲         ╱
                │   ╲       ╱
          ──────┼────╲─────╱────── x
                │     ╲   ╱
                │      ╲ ╱
                │       ● Q
                │       │
                │       ↓ 翻轉
                │       ● R = P + Q
                │
```

**Point Doubling**（P + P）：畫 P 點的切線，跟曲線交於另一個點，再翻轉。

**單位元**：無窮遠點 O（identity element）。對任何 P：P + O = P。

### 群結構

橢圓曲線上的點（加上無窮遠點 O）在 point addition 下形成一個 **阿貝爾群（Abelian group）**：

```
群公理驗證：
  封閉性：P + Q 仍在曲線上         ✓
  結合律：(P + Q) + R = P + (Q + R) ✓
  單位元：P + O = P                 ✓
  逆元素：P + (-P) = O              ✓（-P = (x, -y)）
  交換律：P + Q = Q + P             ✓（阿貝爾群）
```

---

## 核心概念：GF(p) 上的橢圓曲線

### 從實數到有限域

密碼學不用實數（精度問題）。把橢圓曲線放到有限域 GF(p)（p 是大質數）上：

```
y² ≡ x³ + ax + b  (mod p)

GF(p) 上的曲線不再是連續曲線，而是有限個離散的點。

例：y² = x³ + 2x + 3 (mod 97)

把 x 從 0 到 96 代入，對每個 x：
  計算 rhs = x³ + 2x + 3 mod 97
  檢查 rhs 是否是 mod 97 的二次剩餘（quadratic residue）
  如果是，y = ±√rhs mod 97
  → 得到 0 或 2 個點
```

### Point Addition 的代數公式

在 GF(p) 上，point addition 的幾何直覺變成代數公式：

```
給定 P = (x₁, y₁), Q = (x₂, y₂)

情形 1：P ≠ Q（一般加法）
  λ = (y₂ - y₁) × (x₂ - x₁)⁻¹ mod p    ← 斜率
  x₃ = λ² - x₁ - x₂ mod p
  y₃ = λ(x₁ - x₃) - y₁ mod p
  P + Q = (x₃, y₃)

情形 2：P = Q（point doubling）
  λ = (3x₁² + a) × (2y₁)⁻¹ mod p         ← 切線斜率
  x₃ = λ² - 2x₁ mod p
  y₃ = λ(x₁ - x₃) - y₁ mod p
  2P = (x₃, y₃)

情形 3：P = -Q（即 x₁ = x₂, y₁ = -y₂）
  P + Q = O（無窮遠點）
```

### 範例一：Python 手刻 EC Point Addition

```python
"""GF(p) 上的橢圓曲線算術（教學用，不是 constant-time）"""

def ec_add(P, Q, a, p):
    """Point addition on y²=x³+ax+b (mod p)。P, Q 是 (x,y) tuple 或 None（無窮遠點）"""
    if P is None: return Q
    if Q is None: return P
    x1, y1 = P; x2, y2 = Q

    if x1 == x2:
        if y1 != y2: return None  # P + (-P) = O
        if y1 == 0:  return None
        lam = (3 * x1 * x1 + a) * pow(2 * y1, -1, p) % p  # doubling
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, p) % p          # addition

    x3 = (lam * lam - x1 - x2) % p
    y3 = (lam * (x1 - x3) - y1) % p
    return (x3, y3)

def ec_mul(k, P, a, p):
    """k * P（double-and-add）"""
    result = None  # infinity
    addend = P
    while k > 0:
        if k & 1: result = ec_add(result, addend, a, p)
        addend = ec_add(addend, addend, a, p)
        k >>= 1
    return result

# 演示：y² = x³ + 2x + 3 (mod 97)
a_coeff, p = 2, 97
# 找曲線上的點
pts = [(x, y) for x in range(p) for y in range(p)
       if (y*y - x**3 - 2*x - 3) % p == 0]
print(f"曲線上有 {len(pts)} 個點")

P, Q = pts[0], pts[1]
print(f"P={P}, Q={Q}")
print(f"P+Q = {ec_add(P, Q, a_coeff, p)}")
print(f"2P  = {ec_add(P, P, a_coeff, p)}")
print(f"3P  = {ec_mul(3, P, a_coeff, p)}")

# 找 P 的 order
for i in range(1, 200):
    if ec_mul(i, P, a_coeff, p) is None:
        print(f"P 的 order = {i}"); break
```

### 範例二：SageMath 驗算

```python
# SageMath
E = EllipticCurve(GF(97), [2, 3])  # y² = x³ + 2x + 3 mod 97
print(f"曲線 order: {E.order()}")
P, Q = E.points()[1], E.points()[2]
print(f"P={P}, Q={Q}, P+Q={P+Q}, 2P={2*P}")
# ECDLP
G = E.gens()[0]; k = 42; kG = k * G
k_found = discrete_log(kG, G, G.order(), operation='+')
print(f"ECDLP: {k_found}*G = {kG}")
```

---

## 底層機制

### Scalar Multiplication（double-and-add）

ECDH 和 ECDSA 的核心運算是 **scalar multiplication**：給定點 P 和整數 k，計算 kP = P + P + ... + P（k 次）。

```
計算 13P（13 = 1101 in binary）：

Double-and-add（從 MSB 到 LSB）：
  result = O (infinity)
  bit 1: result = 2·O + P = P        → P
  bit 1: result = 2·P + P = 3P       → 3P
  bit 0: result = 2·(3P) = 6P        → 6P
  bit 1: result = 2·(6P) + P = 13P   → 13P

需要 3 次 doubling + 2 次 addition = 5 次群運算
（直接加 13 次 P 需要 12 次 addition）
```

### ECDLP（橢圓曲線離散對數問題）

```
正向：已知 k 和 P，計算 Q = kP       → 快（double-and-add，O(log k)）
反向：已知 P 和 Q = kP，求 k          → 困難（目前最好是 O(√n)）
```

為什麼 ECDLP 比 DLP 更難？

```
Finite-field DLP（Z_p*）的攻擊：
  Pollard's rho:     O(√p)          ← 通用
  Index calculus:    L_p[1/3, c]     ← sub-exponential！
  Number Field Sieve: L_p[1/3, c']  ← sub-exponential！

ECDLP 的攻擊：
  Pollard's rho:     O(√n)          ← 通用
  Index calculus:    不適用           ← 沒有「smooth numbers」的概念
  NFS:              不適用           ← 結構不同

結論：
  2048-bit DLP ≈ 112-bit 安全（被 NFS 削弱）
  256-bit ECDLP ≈ 128-bit 安全（只有 Pollard's rho）
```

Index calculus 依賴把群元素分解成「小質數」的乘積。在有限域 Z_p* 中，整數有自然的因式分解。但在橢圓曲線群中，沒有對應的「因式分解」概念——點沒有乘法結構。

### Hasse's Theorem

```
橢圓曲線 E/GF(p) 的 order #E(GF(p)) 滿足：

|#E(GF(p)) - (p + 1)| ≤ 2√p

也就是說，曲線上的點數大約是 p + 1，上下浮動不超過 2√p。

對密碼學來說：256-bit 的 p → 曲線上大約有 2^256 個點
→ ECDLP 的安全性 ≈ √(2^256) = 2^128
```

---

## 進一步用法：曲線的選擇

### Weierstrass vs Montgomery vs Edwards

| 形式 | 方程 | 優點 | 代表曲線 |
|---|---|---|---|
| Short Weierstrass | y² = x³ + ax + b | 最通用 | NIST P-256, secp256k1 |
| Montgomery | By² = x³ + Ax² + x | constant-time ladder | Curve25519 |
| (Twisted) Edwards | ax² + y² = 1 + dx²y² | 完整加法公式（no special cases） | Ed25519 |

### Curve25519 的設計（Bernstein, 2006）

Daniel Bernstein 設計 Curve25519 時的目標：

```
1. 128-bit 安全（p ≈ 2^255）
2. Constant-time 實作容易（Montgomery ladder）
3. 抗 side-channel
4. 不需要信任 NIST（用 "nothing-up-my-sleeve" 參數）
5. 快

Curve25519: y² = x³ + 486662x² + x  (mod 2^255 - 19)

為什麼選 p = 2^255 - 19？
  - Mersenne-like prime → 模約化非常快
  - 19 是滿足 2^255 - c 是質數的最小正整數 c

為什麼選 A = 486662？
  - 最小的 A 使得 (A-2)/4 是整數
  - 曲線的 order 是 8 × (大質數)
  - 沒有 backdoor 的餘地（deterministic 選擇）
```

### Montgomery Ladder

```
計算 kP 的 Montgomery ladder（constant-time）：

  R0 = O, R1 = P
  for each bit b_i of k (from MSB to LSB):
    if b_i == 0:
      R1 = R0 + R1
      R0 = 2 * R0
    else:
      R0 = R0 + R1
      R1 = 2 * R1

特性：
  每一步都做一次 add + 一次 double
  不管 b_i 是 0 還是 1，運算量完全相同
  → constant-time！side-channel 攻擊者無法從 timing 推斷 k

對比 double-and-add：
  b_i = 0 時只做 double
  b_i = 1 時做 double + add
  → timing 洩露 k 的 bit pattern
```

### NIST 曲線 vs Curve25519

| 面向 | NIST P-256 | Curve25519 |
|---|---|---|
| 設計者 | NSA / NIST | Daniel Bernstein |
| 參數選擇 | 不透明（seed 公開但來源不明） | Deterministic |
| Constant-time | 需要額外工程 | Montgomery ladder 天生 |
| Cofactor | 1 | 8（需要注意 small subgroup） |
| 採用 | TLS, X.509, 銀行 | Signal, WireGuard, Tor, SSH |
| 標準化 | FIPS 186-4 | RFC 7748 |
| 信任問題 | NSA backdoor 疑慮 | 社群信任度高 |

NIST P-256 的 seed `c49d3608 86e70493 6a6678e1 139d26b7 819f7e90` 聲稱是 SHA-1 的輸出，但輸入從未公開。這讓一些密碼學家不安——雖然沒有證據表明有 backdoor，但「我不知道參數怎麼來的」在 Dual_EC_DRBG 事件後是一個合理的擔憂。

---

## 對比與取捨

### 各主流曲線比較

| 曲線 | 位元 | 安全等級 | 形式 | 用途 |
|---|---|---|---|---|
| P-256 (secp256r1) | 256 | 128 | Weierstrass | TLS, X.509 |
| P-384 (secp384r1) | 384 | 192 | Weierstrass | 高安全需求 |
| secp256k1 | 256 | 128 | Weierstrass | Bitcoin |
| Curve25519 | 255 | ~128 | Montgomery | ECDH (X25519) |
| Ed25519 | 255 | ~128 | Twisted Edwards | EdDSA 簽章 |
| Ed448 | 448 | ~224 | Edwards | EdDSA 高安全 |

### secp256k1（Bitcoin 的曲線）

y² = x³ + 7 (mod 2^256 - 2^32 - 977)。a=0 讓加法稍快，Koblitz endomorphism 加速 scalar mult ~30%。參數生成透明，Bitcoin 社群信任。

---

## 踩雷集錦

### 雷 1：在 Curve25519 上不做 cofactor clearing

Curve25519 的 cofactor 是 8——群的 order = 8 × q（q 是大質數）。如果不做 cofactor clearing，攻擊者可以送 order 8 的點做 small subgroup attack。

```python
# X25519 的規範要求：clamping private key
# 最低 3 bit 清零（強制是 8 的倍數）→ 自動做 cofactor clearing
key[0] &= 248   # 清掉最低 3 bit
key[31] &= 127  # 清掉最高 bit
key[31] |= 64   # 設定第二高 bit
```

### 雷 2：用非 constant-time 的 scalar multiplication

```python
# 錯誤：leaky double-and-add
def scalar_mult_leaky(k, P):
    result = INFINITY
    for bit in bin(k)[2:]:
        result = double(result)
        if bit == '1':       # ← timing leak！
            result = add(result, P)
    return result

# 正確：Montgomery ladder（每步都做 add + double）
```

### 雷 3：不驗證點在曲線上

```python
# 錯誤：直接用對方給的 (x, y)
shared = scalar_mult(my_key, ECPoint(their_x, their_y))

# 正確：先驗證
assert (their_y**2) % p == (their_x**3 + a*their_x + b) % p
```

如果對方給的點不在曲線上（invalid curve attack），計算結果可能洩露私鑰資訊。

### 雷 4：混淆 Curve25519 和 Ed25519

- **Curve25519**：Montgomery 曲線，用於 **ECDH**（X25519）
- **Ed25519**：Twisted Edwards 曲線，用於 **簽章**（EdDSA）
- 兩者在同一個群上（birational equivalence），但座標系和用途不同

### 雷 5：以為 ECC 對量子安全

ECC 跟 RSA 一樣會被量子電腦的 Shor's algorithm 打穿。256-bit ECC 只需要 ~2500 logical qubits。Part 7（Post-Quantum）會講替代方案。

---

## 進階

### Bilinear Pairings

某些曲線支援 pairing e: G₁ × G₂ → G_T，使得 e(aP, bQ) = e(P,Q)^(ab)。應用包括 BLS signature（Ethereum 2.0 的 signature aggregation）、IBE、zk-SNARKs。

### Twist Attack 與 MOV Attack

- **Twist attack**：Montgomery 曲線有 quadratic twist。不在主曲線上的 x 座標可能在 twist 上。X25519 規範允許此情形（twist 安全性足夠）。
- **MOV attack**：如果 embedding degree 很小，可用 pairing 把 ECDLP 映射到有限域 DLP（可用 index calculus）。密碼學曲線要求 embedding degree 足夠大。P-256 和 Curve25519 都安全。

---

## 動手練習

1. **手算 EC 加法**：在 y² = x³ + 2x + 3 (mod 97) 上，找兩個點 P, Q，手算 P + Q。
   提示：先找 x = 0 對應的 y（0³ + 0 + 3 = 3，3 是不是 mod 97 的二次剩餘？）

2. **SageMath 探索**：
   ```python
   E = EllipticCurve(GF(97), [2, 3])
   print(E.order())
   G = E.gens()[0]
   for i in range(1, 20):
       print(f"{i}G = {i*G}")
   ```

3. **比較曲線形式**：在 SageMath 中定義 Curve25519（Montgomery）和 Ed25519（Twisted Edwards），驗證它們在同一個群上。

4. **ECDLP 體驗**：用 Pollard's rho 在小曲線上解 ECDLP。觀察隨曲線大小增加，解的時間如何增長。

5. **Montgomery Ladder**：實作 constant-time 的 Montgomery ladder，用 `timeit` 驗證不同 k 值的運算時間一致。

---

## 重點整理

```
橢圓曲線 E/GF(p)：y² = x³ + ax + b (mod p)
  曲線上的點 + 無窮遠點形成阿貝爾群
  群運算：point addition + point doubling
  核心運算：scalar multiplication kP = P + P + ... + P

ECDLP：
  已知 P, Q = kP → 求 k → 困難
  最好的通用攻擊：Pollard's rho O(√n)
  Index calculus 不適用 → 比 finite-field DLP 更難
  256 bit → 128-bit 安全（vs DH 需要 3072 bit）

曲線形式：
  Weierstrass：最通用（P-256, secp256k1）
  Montgomery：constant-time ladder（Curve25519）
  Edwards：完整加法公式（Ed25519）

Curve25519 設計亮點：
  p = 2^255 - 19（快速模約化）
  Montgomery ladder（抗 side-channel）
  Deterministic 參數（"nothing-up-my-sleeve"）

ECC 不抗量子：Shor's algorithm 可以在多項式時間解 ECDLP
```

---

## 自我檢核

- [ ] 我能用 point addition 公式在 GF(p) 上手算 P + Q
- [ ] 我能解釋 ECDLP 為什麼比 finite-field DLP 更難（index calculus 不適用）
- [ ] 我能畫出 double-and-add 的步驟圖
- [ ] 我知道 Curve25519 選 p = 2^255 - 19 和 A = 486662 的理由
- [ ] 我能區分 Weierstrass / Montgomery / Edwards 三種形式
- [ ] 我知道 Montgomery ladder 為什麼是 constant-time 的
- [ ] 我能解釋為什麼 Curve25519 需要 cofactor clearing

---

## 延伸閱讀

- **"Curve25519: new Diffie-Hellman speed records"**（Bernstein, 2006）：Curve25519 的原始論文
- **SafeCurves**：https://safecurves.cr.yp.to/ — 各曲線的安全標準評估
- **"A (Relatively Easy to Understand) Primer on Elliptic Curve Cryptography"**（Ars Technica, Nick Sullivan）
- **Boneh & Shoup Ch 15-16**：橢圓曲線的嚴格數學
- **CryptoHack ECC challenges**：https://cryptohack.org/challenges/ecc/

---

## 下一章連結

[Ch 23 — ECDSA / EdDSA / X25519](./23-ecdsa-eddsa-x25519.md)：數學搞定了，來看橢圓曲線的三大應用——ECDSA 簽章（和 nonce reuse 的災難）、EdDSA（deterministic nonce 救世）、X25519（ECDH）。
