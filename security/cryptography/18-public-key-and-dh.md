# Ch 18 — 公鑰動機與 Diffie-Hellman：1976 的革命

> 目標：理解 key distribution problem（為什麼對稱密碼不夠用）、Diffie-Hellman 1976 的 key exchange 構造、DLP（Discrete Log Problem）安全假設、MITM 為什麼 DH 自己防不了。

## Key Distribution Problem

對稱密碼解決「**Alice 與 Bob 共享 key**」後的通訊。但**怎麼共享 key**？

```
2 個人：1 把 key
3 個人：3 把 key (C(3,2))
4 個人：6 把 key
n 個人：n(n-1)/2 把 key
```

100 萬用戶 → 5 × 10¹¹ 把 key。**每一對都要先見面交換**。網際網路時代不可能。

1976 之前的解法：

- **可信第三方 (KDC)**：Kerberos 那種，所有 key 跟 KDC 共享
- **靠物理信使**：軍用密碼本

但有個根本問題：**怎麼跟新陌生人通訊**？網路上 Bob 第一次看到 Alice，沒共享 key 之前一切沒法開始。

## 1976：Diffie & Hellman 的 paper

**"New Directions in Cryptography"**，IEEE Transactions on Information Theory 1976。Whitfield Diffie + Martin Hellman 提：

> **公開 channel 上，Alice 與 Bob 能不見面就建立共享 secret**。

這個概念**之前沒人想過**（其實 GCHQ 的 James Ellis 1969 內部想到，但 classified 沒公開）。論文一發，整個密碼學翻轉 — 開啟「**公鑰密碼學**」時代。

## DH key exchange

```
公開參數（雙方都知）：
  p：大質數（如 2048-bit）
  g：generator of Z*_p

Alice：
  選私鑰 a ∈ [2, p-2] 隨機
  算公鑰 A = g^a mod p
  送 A 給 Bob

Bob：
  選私鑰 b ∈ [2, p-2] 隨機
  算公鑰 B = g^b mod p
  送 B 給 Alice

Alice 算 K = B^a mod p = g^(ba) mod p
Bob 算   K = A^b mod p = g^(ab) mod p

兩者相等！K 就是 shared secret。
```

數學基礎：`(g^a)^b = g^(ab) = (g^b)^a` (mod p)。

```python
import secrets

p = 23           # 教學用小質數
g = 5            # generator

# Alice
a = secrets.randbelow(p - 2) + 2
A = pow(g, a, p)

# Bob
b = secrets.randbelow(p - 2) + 2
B = pow(g, b, p)

# Alice 與 Bob 各自算 shared secret
K_alice = pow(B, a, p)
K_bob = pow(A, b, p)

assert K_alice == K_bob
```

## DLP：DH 的安全假設

Eve 看著 channel，能看到 `g, p, A, B`。她要算 `K = g^(ab) mod p`，等同要：

```
給 g, A = g^a mod p
找 a
```

這叫 **Discrete Logarithm Problem (DLP)**。對「合適選的」p（safe prime）這個問題**非常難**。

**最佳已知算法：General Number Field Sieve (GNFS)**。對 p 是 2048-bit：成本約 2¹¹², 接近現代 brute-force 上限。**1024-bit p 在 2024 年已不安全**（Logjam 攻擊 2015 demo nation-state 級可破）。

所以實務上 DH 用：

- **p ≥ 2048 bit**（最低）
- **p ≥ 3072 bit**（NIST 2024 推薦）
- **safe prime**：p = 2q + 1，q 也是質數

或者用**橢圓曲線 DH（ECDH）**，用 256-bit 達到等同 3072-bit DH 安全。Ch 22-23 會展開。

## 標準 group：RFC 3526

實務不要每個 server 自己挑 (p, g) — 用 IETF 標準化的：

- **MODP Group 14**：2048-bit p（RFC 3526，2003）
- **MODP Group 15**：3072-bit
- **MODP Group 16**：4096-bit
- **MODP Group 17/18**：6144 / 8192-bit

**為什麼共用 (p, g) 安全**？因為 p 公開不影響 DLP 難度。共用反而省每次 setup 成本。

但這也是 **Logjam 攻擊**的核心：太多 server 共用同個 1024-bit p → attacker 預計算 NFS 一次（幾個月 + 幾百萬美元）後，**對所有共用此 p 的 server 即時破解**。教訓：**共用 group 必須夠大**。

## Generator g

`g` 必須是 `Z*_p` 的 generator（或 large subgroup 的）。

實務：

- **g = 2**（最常見，計算快）
- **g = 5**（某些標準）

驗證 g 是 generator 比較貴（要因式分解 p-1），但對 safe prime（p = 2q + 1）相對簡單：g 是 generator iff `g^q mod p ≠ 1` and `g^2 mod p ≠ 1`。

## DH 的 MITM 問題

DH 本身**不能驗證對方身分**：

```
Alice          Eve (MITM)             Bob
  │              │                     │
  │── A = g^a ──►│                     │
  │              │── A' = g^a' ───────►│
  │              │                     │
  │              │◄───── B = g^b ──────│
  │◄── B' = g^b' │                     │
  │              │                     │
                 ↓                     ↓
            K_AE = g^(a*b')      K_EB = g^(a'*b)

Alice 與 Eve 共享 K_AE
Eve 與 Bob 共享 K_EB
Alice 以為跟 Bob 通，實際在跟 Eve 通
```

**DH 必須配 authentication**：

- **Static DH key in certificate**（公鑰證書內含 DH 公鑰）
- **DH 後簽 transcript**（TLS 1.3 的做法）
- **預共享 key for handshake**（PSK）

純 anonymous DH 只在「**也接受 MITM**」場景能用（有些 P2P 系統的 weak threat model）。

## Ephemeral DH (DHE) 與 forward secrecy

**Static DH**：A 與 B 一輩子 key 不變。**不安全**：A 私鑰被偷 → 過去所有通訊一次都洩漏。

**Ephemeral DH (DHE)**：每個 session 各自隨機選新 (a, b)。session 結束 a, b 丟掉。

**Forward Secrecy**：未來 long-term key 洩漏，**過去 session 仍安全**（因為 ephemeral key 已經丟了）。

TLS 1.3 強制 DHE 或 ECDHE — **不允許 static DH**。Ch 34 詳述。

## TLS 1.3 把 DH 用在哪

```
client                              server
  │                                   │
  │── ClientHello (g^x for X25519) ──►│
  │                                   │
  │   ◄── ServerHello (g^y) ─────────│
  │   ◄── Certificate ───────────────│
  │   ◄── CertificateVerify ─────────│  ← server 用 cert 私鑰簽
  │   ◄── Finished ──────────────────│  ← MAC over transcript
  │                                   │
  │── Finished ──────────────────────►│
  │                                   │
  │◄═════ encrypted application data ═►│
```

DH 提供共享 secret + Certificate 提供身分。**結合對抗 MITM**。

## 程式範例：Python `cryptography`

```python
from cryptography.hazmat.primitives.asymmetric import dh
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

# Alice 與 Bob 共用 parameters（實務用 RFC 3526 預製）
parameters = dh.generate_parameters(generator=2, key_size=2048)

# 各自 generate key
alice_priv = parameters.generate_private_key()
alice_pub = alice_priv.public_key()
bob_priv = parameters.generate_private_key()
bob_pub = bob_priv.public_key()

# 各自算 shared secret
alice_shared = alice_priv.exchange(bob_pub)
bob_shared = bob_priv.exchange(alice_pub)
assert alice_shared == bob_shared

# DH output 用 KDF derive 真正的 key
derived = HKDF(algorithm=hashes.SHA256(), length=32,
               salt=None, info=b"handshake").derive(alice_shared)
```

注意 `exchange()` 回傳 raw bytes — **必須 KDF**（不要直接當 key 用）。

## Computational vs Decisional DH

兩個假設：

```
CDH (Computational DH):
  given g, g^a, g^b, find g^(ab)
  困難 → DH key exchange 安全
  
DDH (Decisional DH):
  given g, g^a, g^b, g^c
  decide if c = ab
  
DDH 更強假設，但不是所有 group 都成立
（如 GF(p) 一般成立，bilinear pairing group 不成立）
```

對 standard DH 用 CDH 就夠。某些進階構造（pairing-based crypto）依賴 DDH 不成立的特性。

## 為什麼 ECC 取代 DH

純 DH 在 GF(p) 上要 2048-3072-bit p → key 大、計算慢。

橢圓曲線版（ECDH）只要 256-bit → key 小 12 倍、計算快幾倍、安全等同 3072-bit DH。

**現代 TLS 預設 X25519**（一種 ECDH），不用 finite field DH。Ch 22-23 詳述。

## 一個常見誤解

「DH 與 RSA 都是公鑰算法，差別不大吧？」

**功能不同**：

- **DH**：key exchange — 兩方共建 shared secret
- **RSA**：encryption + signing — 一方加密 / 簽，另一方解 / 驗

實務上 TLS 1.3 用 DH 做 key exchange、用 RSA 或 ECDSA 做簽章 — **不混用**。早期 TLS 用 RSA 做 key exchange（client 用 server 公鑰加密 pre-master secret），這個被 forward secrecy 需求淘汰（RSA static、無 ephemeral version）。Ch 34 展開。

## 自我檢核

- [ ] 我能寫 DH key exchange 並跑通 Alice/Bob 同 secret
- [ ] 我能解釋 DLP 是什麼以及 GNFS 為什麼讓 1024-bit p 不安全
- [ ] 我能展示 MITM attack 對純 DH 為何成立
- [ ] 我能解釋 ephemeral DH 與 forward secrecy
- [ ] 我能說出 RFC 3526 標準 group 與為什麼共用安全
- [ ] 我能比較 CDH 與 DDH 假設

下一章看 RSA — 公鑰密碼學的另一支柱、Euler totient、CRT 加速、padding 模式。

→ [Ch 19 RSA](./19-rsa.md)
