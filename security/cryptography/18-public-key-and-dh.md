# Ch 18 — 公鑰密碼與 Diffie-Hellman

> 目標：理解公鑰密碼解決的 key distribution 問題，能手算 Diffie-Hellman，知道 DH 本身不提供 authentication。

---

## 為什麼需要公鑰密碼

回顧 Part 3 和 Part 4 的所有對稱密碼——AES、ChaCha20、HMAC——都有一個共同前提：**通訊雙方必須事先共享同一把 secret key**。

問題在「事先共享」這四個字。

### key distribution nightmare

假設一個網路有 n 個人，任意兩人之間要安全通訊：

- 2 人 → 1 把 key
- 3 人 → 3 把 key
- 10 人 → 45 把 key
- 1000 人 → 499,500 把 key
- n 人 → **n(n-1)/2** 把 key

```
n 人完全互聯所需 key 數：

     n = 10     →    45
     n = 100    →  4,950
     n = 1,000  → 499,500
     n = 10,000 → 49,995,000

公式：C(n,2) = n(n-1)/2
```

問題不只是數量。每把 key 都必須透過 **安全通道**（面交、可信信差、已加密的通道）傳遞。但如果你已經有安全通道了，為什麼還需要密碼學？

這是 1976 年以前密碼學的根本困境：**你需要加密來保護 key，但你需要 key 來做加密**。

### 現實中的土法煉鋼

在公鑰密碼出現以前，解法都很痛苦：

| 方法 | 缺點 |
|---|---|
| 面對面交換 | 不可規模化 |
| 可信信差（courier） | 信差本身是攻擊面 |
| KDC（Key Distribution Center） | 單點故障 + 中央集權 |
| 預先載入大量 key | 每次用完就少一把 |

軍事系統用 KDC：一台中央伺服器管所有 key，每次通訊前先跟 KDC 要 session key。Kerberos 就是這個模型的現代版。但 KDC 被打下來，整個系統就完了。

---

## 先建立直覺

Diffie 和 Hellman 在 1976 年發表 "New Directions in Cryptography" 時，提出一個看起來不可能的事：

> 兩個人在公開通道上交換訊息，旁聽者可以看到所有訊息，但交換完成後兩人共享一個 secret，而旁聽者無法得知這個 secret。

用顏色混合的比喻：

```
Alice                             Bob
  |                                 |
  |  各自選一個秘密顏色               |
  |  (Alice: 紅)    (Bob: 藍)       |
  |                                 |
  |  公開協議一個共同顏色：黃          |
  |                                 |
  |  Alice: 黃 + 紅 = 橙            |
  |  Bob:   黃 + 藍 = 綠            |
  |                                 |
  |-------- 交換混合色 -------->     |
  |<------- 交換混合色 ---------     |
  |                                 |
  |  Alice: 綠 + 紅 = 棕            |
  |  Bob:   橙 + 藍 = 棕            |
  |                                 |
  |  共同 secret：棕                 |
  v                                 v

旁聽者看到：黃、橙、綠
但無法從 橙 分離出 紅（因為混合顏色容易，分離顏色困難）
```

關鍵洞見：**存在某些數學運算，正向計算容易，反向計算困難**。這就是 one-way function（單向函式）的概念。

---

## 核心概念：Diffie-Hellman Key Exchange

### 數學基礎

DH 依賴的數學結構是 **離散對數問題（Discrete Logarithm Problem, DLP）**。

在模運算（modular arithmetic）中：

- **正向**：給定 g, a, p，計算 g^a mod p → **快速**（square-and-multiply，O(log a) 次乘法）
- **反向**：給定 g, g^a mod p, p，求 a → **困難**（目前最好的演算法是 sub-exponential 的）

### DH 協議步驟

```
公開參數（所有人都知道）：
  p = 一個大質數（至少 2048 bit）
  g = p 的一個生成元（generator）

Alice                              Bob
─────                              ────
1. 選秘密 a ∈ [2, p-2]           1. 選秘密 b ∈ [2, p-2]
2. 計算 A = g^a mod p             2. 計算 B = g^b mod p
3. 發送 A ──────────────────────> 收到 A
4. 收到 B <────────────────────── 發送 B
5. 計算 s = B^a mod p             5. 計算 s = A^b mod p

s = B^a = (g^b)^a = g^(ab) mod p
s = A^b = (g^a)^b = g^(ab) mod p

→ 兩邊算出的 s 相同！
```

旁聽者 Eve 看到 p, g, A = g^a mod p, B = g^b mod p，但要算 g^ab mod p，她必須先從 A 求出 a（或從 B 求出 b）——這就是 DLP。

### 範例一：Python DH Key Exchange

```python
"""
DH key exchange 手刻版（教學用，不用於生產）
"""
import secrets
from hashlib import sha256


def generate_dh_params():
    """
    生產環境用 RFC 3526 / RFC 7919 的標準 group。
    這裡用小參數演示流程。
    """
    # RFC 3526 Group 14 (2048-bit MODP)
    # 實際值太長，這裡用小質數示範
    p = 0xFFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF
    g = 2
    return p, g


def dh_keypair(p, g):
    """生成 DH 私鑰和公鑰"""
    private = secrets.randbelow(p - 3) + 2  # [2, p-2]
    public = pow(g, private, p)             # g^private mod p
    return private, public


def dh_shared_secret(their_public, my_private, p):
    """計算共享秘密"""
    # 重要：驗證對方的公鑰在合法範圍內
    if their_public <= 1 or their_public >= p - 1:
        raise ValueError("Invalid public key")
    shared = pow(their_public, my_private, p)  # B^a mod p
    return shared


def derive_key(shared_secret, p):
    """從共享秘密派生對稱金鑰（用 SHA-256）"""
    # 把 shared_secret 轉成固定長度的 key
    ss_bytes = shared_secret.to_bytes((p.bit_length() + 7) // 8, 'big')
    return sha256(ss_bytes).digest()


# === 執行 DH key exchange ===
p, g = generate_dh_params()

# Alice 端
a_priv, a_pub = dh_keypair(p, g)
print(f"Alice 公鑰 A = g^a mod p")
print(f"  A = {hex(a_pub)[:40]}...")

# Bob 端
b_priv, b_pub = dh_keypair(p, g)
print(f"Bob 公鑰 B = g^b mod p")
print(f"  B = {hex(b_pub)[:40]}...")

# 交換公鑰後各自計算
alice_secret = dh_shared_secret(b_pub, a_priv, p)
bob_secret = dh_shared_secret(a_pub, b_priv, p)

assert alice_secret == bob_secret, "Something went wrong"

alice_key = derive_key(alice_secret, p)
bob_key = derive_key(bob_secret, p)
assert alice_key == bob_key

print(f"\n共享金鑰（SHA-256 of shared secret）：")
print(f"  {alice_key.hex()}")
```

---

## 底層機制

### DLP 為什麼困難

計算 g^a mod p（模冪運算）可以用 **square-and-multiply**：

```
計算 g^13 mod p（13 = 1101 in binary）

步驟：
  result = 1
  bit 1 (MSB): result = result² × g = g
  bit 1:       result = result² × g = g³
  bit 0:       result = result²     = g⁶
  bit 1 (LSB): result = result² × g = g¹³

4 步搞定，不需要做 13 次乘法
時間複雜度：O(log a) 次模乘，每次模乘 O(n²) bit 運算
→ 2048-bit 的模冪在毫秒內完成
```

反過來，已知 g, A, p 求 a（離散對數）：

| 演算法 | 時間複雜度 | 備註 |
|---|---|---|
| 暴力搜索 | O(p) | 對 2048-bit p 完全不可行 |
| Baby-step Giant-step | O(√p) | Shanks 演算法，空間 O(√p) |
| Pollard's rho | O(√p) | 空間 O(1) |
| Index Calculus | L_p[1/3, c] | Sub-exponential，最強的通用攻擊 |
| Number Field Sieve | L_p[1/3, (64/9)^(1/3)] | 目前最好 |

對 2048-bit p，Number Field Sieve 需要的運算量大約等同破解 2048-bit RSA——在可預見的未來不可行。

### generator 的選擇

g 必須是 Z_p* 的 **generator（生成元）**：g 的冪次能跑遍整個群。

```
Z_7* = {1, 2, 3, 4, 5, 6}（跟 7 互質的正整數）

g = 3 的冪次：
  3^1 = 3
  3^2 = 2  (9 mod 7)
  3^3 = 6  (27 mod 7)
  3^4 = 4  (81 mod 7)
  3^5 = 5  (243 mod 7)
  3^6 = 1  (729 mod 7)
  → 跑遍所有 6 個元素 ✓

g = 2 的冪次：
  2^1 = 2
  2^2 = 4
  2^3 = 1  (8 mod 7)
  → 只生成 {1, 2, 4}，order = 3 ✗（不是 generator）
```

如果 g 的 order 太小，DLP 就變容易了。Ch 21 會深入討論 safe prime 和 small subgroup attack。

---

## 進一步用法

### DH 不提供 Authentication

DH 的致命弱點：**它不告訴你「對方是誰」**。

考慮 Man-in-the-Middle（MITM）攻擊：

```
Alice                 Mallory (MITM)              Bob
  |                       |                        |
  |--- A = g^a mod p ---->|                        |
  |                       |--- M1 = g^m1 mod p --->|
  |                       |                        |
  |                       |<-- B = g^b mod p ------|
  |<-- M2 = g^m2 mod p --|                        |
  |                       |                        |
  |  s1 = M2^a = g^(m2·a) |  sA = A^m1 = g^(a·m1)  |
  |  （Alice-Mallory）     |  sB = M1^b = g^(m1·b)   |
  |                       |  （Mallory-Bob）         |
```

Mallory 跟 Alice 建立一把共享金鑰，跟 Bob 建立另一把。之後 Mallory 可以：

1. 用 Alice-Mallory 金鑰解密 Alice 的訊息
2. 讀取（甚至修改）內容
3. 用 Mallory-Bob 金鑰重新加密送給 Bob

Alice 和 Bob 以為在跟彼此通訊，但 Mallory 全程在中間。

### 範例二：MITM 攻擊 DH 的 PoC

```python
"""DH MITM PoC"""
import secrets
p, g = generate_dh_params()  # 沿用範例一的函式

a, A = secrets.randbelow(p-3)+2, None; A = pow(g,a,p)
b, B = secrets.randbelow(p-3)+2, None; B = pow(g,b,p)

# Mallory 攔截並替換
m1 = secrets.randbelow(p-3)+2; M1 = pow(g,m1,p)  # 冒充 Alice→Bob
m2 = secrets.randbelow(p-3)+2; M2 = pow(g,m2,p)  # 冒充 Bob→Alice

# Alice 收到 M2 以為是 Bob → alice_key = M2^a
# Mallory 用 A 和 m2 算出同一把 key
alice_key = pow(M2, a, p)
mallory_alice = pow(A, m2, p)
bob_key = pow(M1, b, p)
mallory_bob = pow(B, m1, p)

assert alice_key == mallory_alice  # Mallory 跟 Alice 共享
assert bob_key == mallory_bob      # Mallory 跟 Bob 共享
assert alice_key != bob_key        # Alice 和 Bob 的 key 不同！
# → Mallory 全程解密、轉發、甚至修改
```

---

## 對比與取捨

### 對稱 vs 公鑰密碼

| 面向 | 對稱密碼 | 公鑰密碼 |
|---|---|---|
| key distribution | 需要安全通道 | 不需要（公鑰可公開） |
| 速度 | 快（AES-NI: ~GB/s） | 慢（RSA-2048: ~1000x slower） |
| key 長度（等效安全） | 128 bit | RSA 3072 bit / ECC 256 bit |
| 前向保密（Forward Secrecy） | 不提供 | DH / ECDHE 提供 |
| 用途 | 大量資料加密 | key exchange、數位簽章 |

### 各種 key exchange 比較

| 方法 | 需要事先共享？ | 抗 MITM？ | Forward Secrecy？ |
|---|---|---|---|
| Pre-shared key | 是 | 是 | 否 |
| KDC (Kerberos) | 是（跟 KDC） | 是 | 否 |
| Static DH | 否 | 否（需加簽章） | 否 |
| Ephemeral DH (DHE) | 否 | 否（需加簽章） | **是** |
| Authenticated DH | 否 | 是 | 是 |

**Forward Secrecy（前向保密）**：即使長期私鑰洩露，過去的通訊仍然安全。Ephemeral DH 每次 session 生成新的 a, b，用完就丟——即使事後 Eve 拿到 Alice 的長期私鑰，也無法回推過去的 session key。TLS 1.3 強制使用 ephemeral DH。

---

## 踩雷集錦

### 雷 1：把 DH 的 shared secret 直接當 AES key

```python
# 錯誤：直接用 shared secret
aes_key = shared_secret.to_bytes(32, 'big')[:16]

# 正確：用 KDF 派生
from hashlib import sha256
import hkdf  # 或用 cryptography 套件的 HKDF

aes_key = sha256(shared_secret.to_bytes(256, 'big')).digest()[:16]
# 更好：用 HKDF（RFC 5869），可以加 salt 和 info
```

shared secret 的 bit 分布不均勻（高位比低位有更多結構），直接截取會降低安全性。永遠用 KDF。

### 雷 2：不驗證對方公鑰

```python
# 錯誤：直接算
shared = pow(their_public, my_private, p)

# 正確：先驗證
if their_public <= 1 or their_public >= p - 1:
    raise ValueError("Invalid DH public key")
# 更嚴格（safe prime 時）：確認 their_public^q == 1 mod p
shared = pow(their_public, my_private, p)
```

如果攻擊者送 public key = 0 或 1 或 p-1，shared secret 就變成 0 或 1——完全可預測。

### 雷 3：重複使用同一組 (a, A)

每次 session 都要生成新的隨機 a。如果 Alice 在不同 session 用同一個 a，那些 session 的 shared secret 之間就有數學關聯，前向保密就沒了。

### 雷 4：p 太小

2024 年的建議：p 至少 2048 bit。1024-bit 的 DH 已經被 academic-level 的攻擊者威脅（Logjam 的 precomputation 攻擊，Ch 21 詳述）。

### 雷 5：混淆 DH 和 RSA 的用途

DH 是 **key agreement（金鑰協議）**——雙方共同產生一把 key，任何一方都不能單方面決定。
RSA encryption 是 **key transport（金鑰傳輸）**——一方選 key，加密後傳給另一方。

兩者在 TLS 中都有用到，但 TLS 1.3 只保留了 DH（ECDHE），淘汰了 RSA key transport。原因：DH 天生提供 forward secrecy，RSA key transport 不提供。

---

## 進階

### Computational Diffie-Hellman (CDH) vs Decisional Diffie-Hellman (DDH)

DH 的安全性依賴兩個相關但不同的假設：

- **CDH 假設**：給定 g, g^a, g^b，計算 g^ab 是困難的
- **DDH 假設**：給定 g, g^a, g^b, Z，判斷 Z 是 g^ab 還是隨機數是困難的

DDH 比 CDH 更強（DDH 成立 → CDH 成立，反過來不一定）。DH key exchange 的安全性需要 DDH 假設。

```
安全假設的階層：

DLP（最強假設：離散對數困難）
  ↓ DLP 困難 → CDH 困難
CDH（計算 DH 困難）
  ↓ CDH 困難 → DDH 困難（不一定成立！）
DDH（決策 DH 困難）

某些群上 CDH 成立但 DDH 不成立（例如 bilinear pairing groups）
```

### 從 DH 到 ElGamal

ElGamal（1985）把 DH 改成加密方案：公鑰 (g, p, h=g^x)，加密時選隨機 k，密文 = (g^k, m·h^k)，解密用 c2 × (c1^x)^(-1)。ElGamal 是 probabilistic 的——同一個 m 每次加密產生不同密文。

### 歷史

1976 年 Diffie-Hellman 發表 "New Directions in Cryptography"。但解密的 GCHQ 文件顯示，英國的 Ellis、Cocks、Williamson 在 1969-1973 年就獨立發現了公鑰密碼——因保密而無法發表。

---

## 動手練習

1. **手算 DH**：取 p=23, g=5。Alice 選 a=6，Bob 選 b=15。手動計算：
   - A = 5^6 mod 23 = ?
   - B = 5^15 mod 23 = ?
   - Alice 的 shared secret = B^6 mod 23 = ?
   - Bob 的 shared secret = A^15 mod 23 = ?
   - 驗證兩邊一樣

2. **SageMath 驗證**：
   ```python
   p = 23; g = Mod(5, p)
   a, b = 6, 15
   A = g^a; B = g^b
   print(f"A={A}, B={B}, shared={B^a}, check={A^b}")
   ```

3. **改寫 MITM PoC**：在範例二的基礎上，加入簽章驗證（用 HMAC 模擬：Alice 和 Bob 各自有一個 pre-shared secret 跟 server），讓 MITM 攻擊失敗。

4. **測量性能**：用 `timeit` 比較 2048-bit DH key exchange vs AES-256 加密 1MB 資料的時間差。

---

## 重點整理

```
公鑰密碼解決的問題：
  n 人互聯需要 n(n-1)/2 把對稱 key → 不可規模化
  公鑰密碼：每人只需 1 對 key → O(n)

DH key exchange：
  公開：p (大質數), g (generator)
  Alice 選秘密 a → 送 A = g^a mod p
  Bob   選秘密 b → 送 B = g^b mod p
  共享 secret = g^(ab) mod p

DH 的安全性依賴 DLP（離散對數問題）：
  正向：g^a mod p → 快（square-and-multiply, O(log a)）
  反向：已知 g^a mod p 求 a → 困難（sub-exponential）

DH 的致命缺陷：不提供 authentication
  → MITM 攻擊者可以冒充雙方
  → 解法：數位簽章（Ch 24）或 PKI

Forward Secrecy：
  每次 session 用新的 ephemeral key pair
  即使長期私鑰洩露，過去的通訊仍安全
  TLS 1.3 強制使用 ephemeral DH
```

---

## 自我檢核

- [ ] 我能解釋為什麼 n 人互聯的對稱金鑰數量是 O(n²) 而不是 O(n)
- [ ] 我能用手算完成一次小參數的 DH key exchange
- [ ] 我能解釋 DLP 為什麼是「困難」的（square-and-multiply vs index calculus）
- [ ] 我能畫出 MITM 攻擊 DH 的流程圖
- [ ] 我能解釋 forward secrecy 為什麼需要 ephemeral DH
- [ ] 我知道 DH shared secret 不能直接當 AES key 用（要用 KDF）
- [ ] 我能區分 CDH 和 DDH 假設
- [ ] 我能解釋為什麼 TLS 1.3 淘汰了 RSA key transport

---

## 延伸閱讀

- **"New Directions in Cryptography"**（Diffie & Hellman, 1976）：公鑰密碼的起點，可讀性高
- **RFC 3526**："More Modular Exponential (MODP) Diffie-Hellman groups for IKE"——標準化的 DH group 參數
- **RFC 7919**："Negotiated Finite Field Diffie-Hellman Ephemeral Parameters for TLS"——TLS 用的 DH 參數
- **Boneh & Shoup Ch 10**：DH 假設的嚴格定義和 reduction proof
- **CryptoHack DH challenges**：https://cryptohack.org/challenges/diffie-hellman/

---

## 下一章連結

[Ch 19 — RSA](./19-rsa.md)：從 DH 的 key exchange 跳到第一個完整的公鑰加密系統——RSA。你會看到 Euler's theorem 如何讓加密和解密互為逆運算。
