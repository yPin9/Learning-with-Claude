# Ch 22 — 橢圓曲線數學：群運算、Montgomery ladder、Curve25519

> 目標：從零教橢圓曲線群運算（point addition、doubling、scalar multiplication）、為什麼 ECC 用 256-bit 等同 RSA 3072-bit 的安全度、Montgomery ladder 的 const-time 設計、Curve25519 為什麼是現代首選（vs NIST P-256）。

## 為什麼從 RSA / DH 跳到 ECC

```
                RSA-3072        ECC-256
key size        3072 bit        256 bit       (12× 小)
sign 時間       ~5 ms           ~0.1 ms       (50×)
verify 時間     ~0.1 ms         ~0.5 ms
keygen          慢（找質數）    快
頻寬            大              小
TLS handshake   多 round trip   少 RT
mobile / IoT    痛苦            好
```

**ECC 同安全度下，效率全面領先**。1985 Koblitz / Miller 各自獨立提，但花了 25 年才主流化（學術抗拒、專利、PKCS 慢）。2010 起 TLS / SSH / Bitcoin / Signal 全採用。

## 橢圓曲線是什麼

**Weierstrass form**（最一般）：

```
y² = x³ + ax + b   (在某 field 上)
```

加上「無限遠點 O」當單位元。

例：實數上 `y² = x³ - x + 1`：

```
       y
       │
       │  ╱╲
       │ ╱  ╲
       │╱    ╲
  ─────┼──────────── x
       │╲    ╱
       │ ╲  ╱
       │  ╲╱
```

這條曲線在 ℝ 上長這樣（有兩個分支）。但密碼學用 **GF(p) 上的曲線**（離散點）：

```
y² ≡ x³ + ax + b (mod p)
```

點集合 = 滿足這條方程的所有 `(x, y)` + 無限遠點 O。**這是有限集合**（最多約 p+1 個點）。

```python
# 簡單例：y² = x³ + 2x + 3 mod 97
p = 97
def on_curve(x, y, a=2, b=3):
    return (y*y) % p == (x*x*x + a*x + b) % p

points = [(x, y) for x in range(p) for y in range(p) if on_curve(x, y)]
print(len(points))  # 100 個點
```

## Point Addition：群運算

點加法定義：

```
P + Q：
  通過 P 與 Q 的直線交曲線於第三點 R'
  R' 對 x 軸鏡射 = R = P + Q

P + P （doubling）：
  P 的切線交曲線於 R'
  鏡射 = 2P
```

幾何 + 代數 closed form：

```
若 P = (x_p, y_p), Q = (x_q, y_q), P + Q = R = (x_r, y_r)：

case 1: P ≠ Q
  λ = (y_q - y_p) / (x_q - x_p)
  
case 2: P = Q (doubling)
  λ = (3 × x_p² + a) / (2 × y_p)

x_r = λ² - x_p - x_q
y_r = λ × (x_p - x_r) - y_p

case 3: x_p == x_q 且 y_p == -y_q
  P + Q = O（無限遠點）
```

注意所有運算都在 GF(p) 上 — 除法 = modular inverse。

```python
def point_add(P, Q, p, a):
    if P is None: return Q
    if Q is None: return P
    x1, y1 = P
    x2, y2 = Q
    if x1 == x2 and y1 != y2:
        return None  # 無限遠
    if P == Q:
        # doubling
        m = (3 * x1 * x1 + a) * pow(2 * y1, -1, p) % p
    else:
        m = (y2 - y1) * pow(x2 - x1, -1, p) % p
    x3 = (m * m - x1 - x2) % p
    y3 = (m * (x1 - x3) - y1) % p
    return (x3, y3)
```

**這個 group operation 形成 abelian group**：closed、associative、有單位元 O、有逆元（-P = (x, -y)）、commutative。

## Scalar Multiplication：核心運算

```
k × P = P + P + P + ... + P (k 次)
```

對小 k 直接加。對大 k（256-bit）必用 **double-and-add**（square-and-multiply 的群版）：

```python
def scalar_mult(k, P, p, a):
    R = None  # 無限遠
    Q = P
    while k > 0:
        if k & 1:
            R = point_add(R, Q, p, a)
        Q = point_add(Q, Q, p, a)
        k >>= 1
    return R
```

複雜度：O(log k) 次 point operation。對 256-bit k，約 256-512 次 add。每次 add 約 10-20 mod inversion + 加減乘 — 整體 0.1-1 ms。

## ECDLP：橢圓曲線離散對數問題

```
給 G, P = k × G
找 k
```

**沒已知 polynomial-time 算法**（甚至沒已知 sub-exponential，比 GNFS 對普通 DH 強）。最佳算法：**Pollard rho**，O(√n) where n = group order。

對 256-bit 曲線：n ≈ 2²⁵⁶，攻擊成本 ≈ 2¹²⁸ — 現代算力不可達。

對比：

```
DH (GF(p))     n bits → security bit ≈ n/2 (但 GNFS 把它降到 cube-root-like)
                所以 3072-bit DH ≈ 128-bit security
ECDLP          n bits → security bit ≈ n/2
                所以 256-bit ECC ≈ 128-bit security
```

**這就是 ECC 為什麼省那麼多 bit**：沒有 GNFS-like 加速算法。

## Curve 選擇

不是任意曲線都安全。要避免：

- **anomalous curves**：order = p（有特殊 attack）
- **supersingular curves**：MOV reduction 把 ECDLP 拉到 finite field DLP
- **smooth order**：Pohlig-Hellman 切到 small subgroup
- **non-twist-secure curves**：twist attack

業界標準曲線：

```
NIST 系列（1999-2000）：
  P-256, P-384, P-521 — 廣泛採用，但 random 來源不透明

Brainpool（2010）：
  brainpoolP256r1 等 — 歐洲推，generation 過程公開

Curve25519 / Curve448 (Bernstein)：
  純設計優勢，沒專利
  Ed25519 / X25519 用這個

secp256k1：
  Bitcoin、以太坊用
  特殊優化（j-invariant = 0）
```

## NIST P-256 vs Curve25519

```
P-256:
  曲線參數：a, b 各 256-bit，random-ish
  性能：軟體中等，硬體加速好
  Const-time 實作：困難（很多 paper 揭露 timing leak）
  專利：曾有過，現在過期

Curve25519:
  y² = x³ + 486662 × x² + x  (Montgomery form)
  prime p = 2²⁵⁵ - 19（特殊形狀）
  性能：純軟體最快
  Const-time：天生（Montgomery ladder）
  設計者公開 rationale
```

**Curve25519 設計目標就是「const-time 容易實作」**。Bernstein 2005 paper 提整套 — 後來成為 IETF 標準（RFC 7748、RFC 8032）。

## Montgomery Ladder：const-time scalar mult

普通 double-and-add 的 timing 取決於 k 的 bit pattern（is bit set → do add）。**timing attack 能洩漏 k**。

**Montgomery ladder** 修正：

```python
def montgomery_ladder(k, P, ...):
    R0 = None  # 無限遠
    R1 = P
    for bit in bin(k)[2:]:  # MSB first
        if bit == '0':
            R1 = R0 + R1
            R0 = 2 * R0
        else:
            R0 = R0 + R1
            R1 = 2 * R1
    return R0
```

**每個 bit 都做相同數量的 operation**（一次 add + 一次 double）— const-time。

進一步：**Montgomery curve 上的 X-only 加法**只用 x 座標，連除法都省（用變形公式）：

```
  x(P+Q) and x(P-Q) known → x(2P+Q) one formula
  x(P) and x(2P) known → x(P+(P+Q)) and x(2(P+Q)) computable
```

整套就是 **X25519** 的核心。輸出全 32 byte，輸入私鑰 32 byte，公開 key 32 byte。

## X25519 程式範例

```python
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

# Alice
alice = X25519PrivateKey.generate()
alice_pub = alice.public_key()

# Bob
bob = X25519PrivateKey.generate()
bob_pub = bob.public_key()

# ECDH
shared_a = alice.exchange(bob_pub)
shared_b = bob.exchange(alice_pub)
assert shared_a == shared_b
print(shared_a.hex())
```

**32 byte 輸入、32 byte 輸出**。比 FFDHE 簡潔得多。

## ECDLP 上的攻擊面

雖然沒 sub-exponential 一般攻擊，仍有特殊場景：

### Pohlig-Hellman

如果 group order n 有小因子（n = q_1 × q_2 × ... × q_k），ECDLP 可拆成各 q_i 上的 sub-problem。

修補：用 prime order group（n 是質數）。Curve25519 的 group 階 = 8 × prime（cofactor = 8），實務 setup 用 cofactor multiplication 避開。

### Invalid curve attack

attacker 送一個「**不在曲線上**」的點，server 不檢查就算 — 結果 group 是別的曲線（可能弱）。

修補：**收到 public key 必驗 on-curve**。Curve25519 設計上少這個風險（Montgomery ladder 自然 handle）。

### Twist attack

對某些 curve，「twist」是另一條 curve（quadratic twist）— 若它的 group 弱，invalid point attack 可利用。

修補：**twist-secure curve**（Curve25519 是；NIST P-256 不是 twist-secure）。

## ECC 比 RSA 寬鬆嗎？

不是。ECC 對「**正確選曲線**」極敏感。隨便造曲線可能殺死 ECDLP — 這也是為什麼業界用 standardized curves（NIST、Brainpool、Curve25519）而非自製。

**Bernstein 推 SafeCurves 網站** <https://safecurves.cr.yp.to/>：列舉曲線安全屬性。Curve25519、Curve448 全綠；NIST P-256 一些屬性紅。**新系統優先 Curve25519**。

## 一個常見誤解

「ECC 比較新所以可能還沒被破」

**ECC 1985 提出，1990 年代學術已分析過，2000 年代成熟**。比 RSA（1977）短，但比 NIST PQC（2010+）長很多。**業界對 ECC 的信任基於 30+ 年 cryptanalysis 沒突破**。

ECC 真正的「年輕」風險在量子電腦：Shor 算法對 ECDLP 同樣有效（甚至比 RSA 還容易，因為 256-bit ECC 比 2048-bit RSA 用更少 qubit）。**post-quantum 時代 ECC 與 RSA 一起退場**，要遷移到 ML-KEM / ML-DSA。Ch 29-32 詳述。

## 自我檢核

- [ ] 我能寫 GF(p) 上的 point add / double
- [ ] 我能解釋 ECDLP 為什麼比 GF(p) DLP 安全等級高
- [ ] 我能寫 Montgomery ladder 並解釋為什麼 const-time
- [ ] 我能比較 NIST P-256 與 Curve25519
- [ ] 我能說出 invalid curve attack 與其修補
- [ ] 我能列舉一個曲線必滿足的安全屬性

下一章看 ECC 的實際應用：ECDSA、EdDSA、X25519。

→ [Ch 23 ECDSA / EdDSA / X25519](./23-ecdsa-eddsa-x25519.md)
