# Ch 21 — Diffie-Hellman 細節：DLP、small subgroup、Logjam

> 目標：補 Ch 18 沒講完的 DH 進階話題：為什麼一定要用 safe prime（p = 2q + 1）、small subgroup confinement attack、Logjam（2015，512-bit DH 共享 prime 被 nation-state 級攻擊預算破解）。

## 為什麼要 safe prime

回憶 DH：

```
g 是 Z*_p 的 generator
public key = g^a mod p
shared secret = g^(ab) mod p
```

問題：**Z*_p 的 generator 不只一個**，且 subgroup 有不同大小：

```
Z*_p 階 = p-1
若 p - 1 = q × m （q 是 small prime，m 是其餘）
則 Z*_p 有大小 q 的 subgroup
```

**Small subgroup 是 attack vector**：若 attacker 能讓 shared secret 落在 small subgroup（如階為 2 或 q），暴力枚舉就破。

**Safe prime**：`p = 2q + 1`，q 也是質數。

```
Z*_p 階 = p - 1 = 2q
subgroups: 階 1 (just {1})、階 2 ({1, -1})、階 q、階 2q
```

只有大小 q 與 2q 兩個「大」subgroup。**避開 small subgroup 簡單**。

## Subgroup confinement attack

```
場景：用 non-safe prime，p - 1 = q × m, m 有小因子
攻擊：attacker 改 (g^a) 為某 small-order element
       使 shared secret 在 small subgroup
       暴力試 small subgroup 元素還原 b
```

具體：Alice 送 `A = g^a`，attacker 改成 `A' = A × h`（h 階為 d，小 prime factor）。Bob 算：

```
K = (A')^b = (A × h)^b = g^(ab) × h^b
```

`h^b` 的可能值只有 d 個（h 階為 d）— attacker 可枚舉。配合 partial information leak（如 timing 或 oracle）可以完整還原 b mod d。重複多次得到 b mod (d_1 × d_2 × ...) — **CRT 還原 b**。

**修補**：

1. **用 safe prime**（p = 2q + 1，沒 small subgroup）
2. **驗證收到的 public key**：`A^q ≡ 1 (mod p)` 確保 A 在大 subgroup
3. **限制使用：static key + 不同 group → reject**

## RFC 7919：FFDHE 標準群

IETF 2016 推 **Finite Field Diffie-Hellman Ephemeral (FFDHE)** 標準 groups：

```
ffdhe2048 (2048-bit)
ffdhe3072 (3072-bit)
ffdhe4096 (4096-bit)
ffdhe6144 (6144-bit)
ffdhe8192 (8192-bit)
```

每個都是：

- **safe prime**（p = 2q + 1）
- 公開、被審查、已知參數
- TLS 1.3 與 TLS 1.2 with FFDHE

**TLS 1.3 之前 server 自選 group**（很多選錯，1024-bit 弱質數）。FFDHE 統一參數，**全 internet 用同一組**（與 ECC 共用同曲線是同思路）。

## Logjam Attack（2015）

Adrian / Bhargavan / Durumeric 等 paper "Imperfect Forward Secrecy: How Diffie-Hellman Fails in Practice"。

### 背景

- 1990s 美國出口管制：DH 必 ≤ 512-bit（弱）
- 2015 仍有 server 接受 EXPORT_DHE cipher suite（512-bit DH）
- 多數 server 共用一小撮 1024-bit DH primes（OpenSSL 預設、Apache 預設）

### 攻擊

```
1. Attacker 預計算 GNFS first stage 對某共用 1024-bit p
   時間：幾個月、幾百萬美元（nation-state 級）
   一次預算後可重複用對所有共用此 p 的 server

2. MITM 改 TLS handshake，downgrade 到 EXPORT_DHE（512-bit）
   client 與 server 都協商成 512-bit DH

3. 用預計算結果，秒級破譯 DH（GNFS final stage 對此 p 已準備好）
```

```
影響：
  18% 的 HTTPS server (Top 1M)
  26% 的 SSH server
  56% 的 IPSec/IKE server
受影響 server 都共用某常用 1024-bit p
```

### 教訓

1. **「Export-grade」cipher suite 是慢性病**。即使「我們不會用」，留著就被 downgrade
2. **Common parameters + 大算力 = 一次預算多次破解**
3. **1024-bit DH 在 2015 已 nation-state 可破**

### 修補

- **server**：disable EXPORT cipher suite、用 ≥ 2048-bit DH（FFDHE 群）、優先 ECDHE
- **client**：拒絕弱 DH parameters
- **TLS 1.3**：根本解 — 砍掉所有 export，砍掉 client-chosen DH params

## 1024-bit DH：當年完美，今天不夠

NIST 估算 GNFS 對 1024-bit DH：

```
2015：~1 億美元 + 1 年
2024：~ 千萬美元 + 數月（Cloud GPU）
2030：可能百萬美元
```

**1024-bit DH 對 nation-state 已破**。**民用 / 學術也要避免**。最低 2048-bit（112-bit security）。

NIST SP 800-56A 2024：

- **discrete log 需 ≥ 2048-bit p（112-bit security）**
- **128-bit security 推薦 3072-bit p**

## DH 與 ECC 對照

| | DH (FFDHE) | ECDH (Curve25519) |
|---|---|---|
| 安全 = 128-bit | 3072-bit p | 256-bit curve |
| key size | 384 byte | 32 byte |
| Operation 時間 | 5 ms | 0.1 ms |
| 軟體實作難度 | 中 | 中（曲線數學） |
| 抗 timing | 普通 | Montgomery ladder = const-time |
| TLS 1.3 預設 | (可用) | **是** |

**現代新系統一律 ECDH（X25519）**，FFDHE 留給合規需求或特殊場景。

## DH 變種

### Static DH

兩方 long-term key 不變。**沒 forward secrecy**，現代不用。

### Ephemeral DH (DHE / ECDHE)

每 session 各自隨機 (a, b)。**有 forward secrecy**。TLS 1.3 強制。

### Triple DH (3DH)

Signal X3DH 用。每方有 3 種 key（identity / signed prekey / one-time prekey），三組 DH 結果合併。**post-compromise security** + **deniability**。

## Anonymous DH

完全沒 authentication 的 DH。**沒 MITM 防禦**。某些 P2P / IoT 場景能用，但對抗主動攻擊不行。

```python
# Anonymous ECDH 示意
import os
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

# Both sides
priv_a = X25519PrivateKey.generate()
priv_b = X25519PrivateKey.generate()

shared_a = priv_a.exchange(priv_b.public_key())
shared_b = priv_b.exchange(priv_a.public_key())

assert shared_a == shared_b
# 沒 cert 驗證 → MITM 可能
```

## DH 程式踩雷

### 1. 不檢查 public key

```python
# 危險
def dh_compute(my_priv, their_pub, p):
    if their_pub <= 1 or their_pub >= p - 1:
        # 應 reject 但很多人不檢查
        pass
    return pow(their_pub, my_priv, p)
```

`their_pub = 1` → shared = 1 給定（無論 priv）。`their_pub = p - 1 = -1` → shared = ±1。Attacker 強制弱 secret。

正確：`assert 2 ≤ their_pub ≤ p - 2`。

### 2. 不 KDF

```python
# 危險：直接當 key
key = pow(their_pub, my_priv, p).to_bytes(...)
aes_encrypt(key[:32], message)
```

DH output 有結構（不是均勻隨機 byte）。**必 KDF**：

```python
shared = pow(...)
key = HKDF(SHA256, length=32, salt=None, info=b"context").derive(shared.to_bytes(...))
```

### 3. 重用 ephemeral key

「省 keygen 成本」重用 ephemeral key 數次 → 失去 forward secrecy。每 session 一次性產生新 key。

## 一個常見誤解

「DH 不需要 cert，比 RSA 簡單」

**不對**。**純 DH 沒 authentication，必須配 cert / 簽章**才能對抗 MITM。TLS 1.3 用 ECDHE + cert（server 簽 transcript）。

「沒 cert 比較簡單」是錯覺 — **加 authentication 是必要的，工程上 DH 與 RSA 處理是同層級複雜度**。

## 自我檢核

- [ ] 我能解釋 safe prime 與 non-safe prime 的差別
- [ ] 我能描述 small subgroup confinement attack
- [ ] 我能說出 Logjam 三個關鍵步驟（預計算、downgrade、即時破譯）
- [ ] 我能說出 RFC 7919 FFDHE 是什麼
- [ ] 我能列出 DH 程式至少 3 個必驗證的安全點
- [ ] 我能比較 FFDHE 與 ECDH 的優劣

下一章看橢圓曲線數學 — ECC 取代 DH/RSA 的關鍵基礎。

→ [Ch 22 橢圓曲線數學](./22-elliptic-curves-math.md)
