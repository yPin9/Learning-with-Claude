# Ch 26 — AES-GCM：拆解 GHASH + CTR 的 AEAD 引擎

> **目標**：能拆解 AES-GCM 的 GHASH + CTR 結構，理解 nonce 結構（96-bit nonce || 32-bit counter），能解釋 nonce-reuse 在 GCM 下為什麼是致命的——攻擊者能還原 GHASH key H，之後能偽造任何 tag。

## 為什麼需要這個？

AES-GCM（Galois/Counter Mode）是目前部署最廣泛的 AEAD 演算法。TLS 1.3 的 cipher suite 預設就是 `TLS_AES_128_GCM_SHA256` 或 `TLS_AES_256_GCM_SHA256`。你的瀏覽器此刻可能正在用 AES-GCM 保護 HTTPS 連線。

但 AES-GCM 有一個設計上的硬約束：**nonce 絕對不能重複**。在 AES-CBC + HMAC 的年代，nonce（IV）重複頂多洩漏 plaintext 的 XOR。在 AES-GCM 中，nonce 重複的後果是 **authentication key 被還原**——攻擊者拿到 GHASH key H 後，能偽造任何 message 的 tag，整個 AEAD 的 integrity 保證瞬間歸零。

理解 GCM 的內部結構，才能理解這個災難為什麼會發生。

## 先建立直覺

AES-GCM 做兩件事：

```
加密：用 AES-CTR（和 Ch 11 學的一樣）
驗證：用 GHASH（一種基於 GF(2¹²⁸) 的 universal hash）

GCM = AES-CTR 做加密 + GHASH 做 MAC + 最後用 AES 加密 GHASH 輸出

        ┌──────┐
nonce → │AES-CTR│ → keystream → ⊕ plaintext → ciphertext
        └──────┘
                           ↓
                    ┌──────────┐
AD, ciphertext ──→ │  GHASH   │ → hash_value
                    └──────────┘
                           ↓
              hash_value ⊕ AES_K(nonce || 0x00000001) → tag
```

關鍵洞察：GHASH 的 key `H = AES_K(0¹²⁸)` 只取決於 AES key K，**和 nonce 無關**。也就是說，同一個 K 的所有 encryption 都用同一個 H。如果攻擊者能還原 H，就能偽造任何 message 的 tag。

## 核心概念：GCM 的完整結構

### Step 1：計算 GHASH key H

```
H = AES_K(0¹²⁸)

這是一個 128-bit 值，在 key K 的整個生命週期中固定不變。
H 是 GHASH 函式的 key——它不是 nonce-dependent 的。
```

### Step 2：AES-CTR 加密

GCM 用 AES-CTR 加密 plaintext，但 counter 的初始值有特殊結構：

```
96-bit nonce 的情況（推薦）：
  initial counter = nonce || 0x00000001  （32-bit counter 從 1 開始）
  J₀ = nonce || 0x00000001

  counter 0 → 保留給 tag 加密
  counter 1 → 加密第 1 個 plaintext block
  counter 2 → 加密第 2 個 plaintext block
  ...

非 96-bit nonce 的情況（不推薦）：
  J₀ = GHASH_H(nonce)  ← 要額外做一次 GHASH，慢而且複雜
```

```
CTR 加密流程：

  AES_K(J₀ + 1) ⊕ P₁ = C₁
  AES_K(J₀ + 2) ⊕ P₂ = C₂
  AES_K(J₀ + 3) ⊕ P₃ = C₃
  ...

  其中 P₁, P₂, ... 是 plaintext blocks（128-bit each）
        C₁, C₂, ... 是 ciphertext blocks
```

### Step 3：GHASH 計算

GHASH 把 Associated Data（AD）和 ciphertext 混在一起，用多項式求值（polynomial evaluation）算出一個 128-bit hash：

```
輸入：A₁, A₂, ..., Aₘ  (AD blocks, 128-bit each, 最後一塊 padding 到 128-bit)
      C₁, C₂, ..., Cₙ  (ciphertext blocks)
      len(A) || len(C)   (各 64-bit，記錄 AD 和 ciphertext 的 bit 長度)

GHASH 計算（迭代）：
  X₀ = 0¹²⁸
  X₁ = (X₀ ⊕ A₁) · H       ← GF(2¹²⁸) 上的乘法
  X₂ = (X₁ ⊕ A₂) · H
  ...
  Xₘ = (Xₘ₋₁ ⊕ Aₘ) · H
  Xₘ₊₁ = (Xₘ ⊕ C₁) · H
  Xₘ₊₂ = (Xₘ₊₁ ⊕ C₂) · H
  ...
  Xₘ₊ₙ = (Xₘ₊ₙ₋₁ ⊕ Cₙ) · H
  Xₘ₊ₙ₊₁ = (Xₘ₊ₙ ⊕ [len(A) || len(C)]) · H

  GHASH_H(A, C) = Xₘ₊ₙ₊₁
```

### Step 4：產生 tag

```
tag = GHASH_H(A, C) ⊕ AES_K(J₀)

其中 J₀ = nonce || 0x00000001（counter 0 的加密輸出）
```

注意：tag 不是裸的 GHASH 輸出——最後要 XOR 一個和 nonce 相關的值。這個 XOR 是關鍵，它讓同一個 (A, C) pair 在不同 nonce 下產生不同 tag。

### 完整流程圖

```
                    Key K
                      │
                      ▼
              ┌───────────────┐
    0¹²⁸ ──→ │    AES_K      │ ──→ H (GHASH key)
              └───────────────┘
                      │
    nonce ──→ ┌───────────────┐
  (96-bit)   │ J₀=nonce||0x1 │
              └───────┬───────┘
                      │
          ┌───────────┼───────────────────────┐
          ▼           ▼                       ▼
    ┌──────────┐ ┌──────────┐           ┌──────────┐
    │AES_K(J₀) │ │AES_K(J₀+1)│   ...   │AES_K(J₀+n)│
    └────┬─────┘ └────┬─────┘           └────┬─────┘
         │            │                      │
         │       P₁ ⊕─┘→ C₁            Pₙ ⊕─┘→ Cₙ
         │            │                      │
         │            ▼                      ▼
         │      ┌─────────────────────────────────┐
         │      │           GHASH_H               │
         │      │  (A₁..Aₘ, C₁..Cₙ, len||len)   │
         │      └────────────┬────────────────────┘
         │                   │
         └──────→ ⊕ ←───────┘
                  │
                  ▼
                 tag
```

### 範例一：AES-GCM 加解密 + 驗證 tag

```python
"""
AES-GCM 加解密 + 手動觀察 tag
"""
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

key = AESGCM.generate_key(bit_length=128)
aesgcm = AESGCM(key)

nonce = os.urandom(12)  # 96-bit = 12 bytes
ad = b"header:seq=1"
plaintext = b"secret message for alice"

# 加密（回傳 ciphertext || tag）
ct_with_tag = aesgcm.encrypt(nonce, plaintext, ad)

ct_body = ct_with_tag[:-16]  # ciphertext（和 plaintext 等長）
tag = ct_with_tag[-16:]       # 最後 16 bytes 是 tag

print(f"Plaintext length:  {len(plaintext)} bytes")
print(f"Ciphertext length: {len(ct_body)} bytes (same as plaintext)")
print(f"Tag length:        {len(tag)} bytes (always 128-bit)")
print(f"Total output:      {len(ct_with_tag)} bytes")
print(f"Tag (hex): {tag.hex()}")

# 解密
decrypted = aesgcm.decrypt(nonce, ct_with_tag, ad)
assert decrypted == plaintext
print(f"\n解密成功: {decrypted}")

# 同一個 plaintext + 不同 nonce → 不同 ciphertext 和 tag
nonce2 = os.urandom(12)
ct2 = aesgcm.encrypt(nonce2, plaintext, ad)
print(f"\n不同 nonce → 不同 tag: {ct2[-16:].hex()}")
assert ct_with_tag != ct2  # 密文和 tag 都不同
```

## 底層機制：GHASH 的數學

### GF(2¹²⁸) 有限域

GHASH 在 GF(2¹²⁸) 上運算——這是一個有 2¹²⁸ 個元素的有限域（Galois Field），和 Ch 9 的 GF(2⁸) 是同一類東西，只是更大。

```
GF(2¹²⁸) 的元素：128-bit 的二進位串
加法：XOR（和 GF(2⁸) 一樣）
乘法：多項式乘法 mod 不可約多項式
  不可約多項式：x¹²⁸ + x⁷ + x² + x + 1
  （GCM spec 用這個，記作 0xE1000...0）

GHASH 就是在這個域上做多項式求值：
  GHASH_H(X₁, X₂, ..., Xₛ) = X₁·H^s ⊕ X₂·H^(s-1) ⊕ ... ⊕ Xₛ·H
```

展開來看，GHASH 其實是把輸入 blocks 當作多項式的係數，在 H 這個點求值：

```
f(x) = X₁·x^s ⊕ X₂·x^(s-1) ⊕ ... ⊕ Xₛ·x

GHASH_H(X₁, ..., Xₛ) = f(H)
```

這就是為什麼 GHASH 叫做 **universal hash**：對於任意兩個不同的輸入，它們的 hash 碰撞的機率最多是 s/2¹²⁸（其中 s 是輸入 block 數）。

### 為什麼 GHASH 用多項式？

多項式求值有一個美妙的性質：**差分不可預測（difference unpredictable）**。

如果你知道 f(H) 但不知道 H，你無法預測「改變一個輸入 block 會讓 f(H) 變成什麼」——因為改變一個係數 X_i 會影響到 H^(s-i) 這一項，而 H 是未知的。

這和 AES-CTR 的 bit-flip attack 形成對比：CTR 的 XOR 結構讓攻擊者能精確預測修改效果；GHASH 的多項式結構讓預測不可能（除非知道 H）。

## 進一步用法：nonce reuse 的致命後果

### 為什麼 nonce reuse 在 GCM 下是災難

回顧 tag 的計算：

```
tag = GHASH_H(A, C) ⊕ AES_K(J₀)

其中 J₀ = nonce || 0x00000001
```

如果兩次加密用了**相同的 nonce**（但不同的 plaintext/AD）：

```
tag₁ = GHASH_H(A₁, C₁) ⊕ AES_K(J₀)
tag₂ = GHASH_H(A₂, C₂) ⊕ AES_K(J₀)

XOR 兩個 tag：
tag₁ ⊕ tag₂ = GHASH_H(A₁, C₁) ⊕ GHASH_H(A₂, C₂)
             = [消掉了 AES_K(J₀)！]
```

`AES_K(J₀)` 被消掉了！攻擊者得到的是 GHASH 的兩個輸出的 XOR——這是一個**關於 H 的方程**。

展開 GHASH：

```
GHASH_H(A₁,C₁) ⊕ GHASH_H(A₂,C₂)
= (A₁₁⊕A₂₁)·H^(m+n+1) ⊕ ... ⊕ (len₁⊕len₂)·H
= 一個以 H 為未知數的多項式 = 0

攻擊者知道 A₁, C₁, A₂, C₂（全是密文或明文 AD），
所以能構造這個多項式，然後在 GF(2¹²⁸) 上解方程求 H。
```

H 是 GF(2¹²⁸) 上的多項式根——用 factoring 就能找到所有可能的 H。如果多項式的度數是 d，最多有 d 個根，通常只有一個合理的 H。

### 拿到 H 之後能做什麼

1. **偽造 tag**：攻擊者能算任意 (A', C') 的 GHASH_H(A', C')
2. 只差 AES_K(J₀)——但攻擊者知道某個合法的 (tag, A, C)，可以算出 AES_K(J₀) = tag ⊕ GHASH_H(A, C)
3. 對任何新的 nonce，攻擊者**還是**能偽造 tag（因為 H 和 nonce 無關；只需要知道對應 nonce 的 AES_K(J₀)）

等等——新 nonce 的 AES_K(J₀) 不知道啊？沒錯，但如果攻擊者能觀察到使用該 nonce 的合法 ciphertext，就能算出那個 nonce 的 AES_K(J₀)，然後偽造。

更糟的是：nonce reuse 也洩漏了 plaintext XOR（因為 CTR keystream 相同）：

```
C₁ ⊕ C₂ = P₁ ⊕ P₂
```

和 OTP 重複使用 key 的問題一模一樣（Ch 6）。

### 範例二：nonce reuse GHASH key recovery（SageMath PoC）

以下 PoC 展示攻擊者如何從兩次 nonce reuse 的 ciphertext 中還原 GHASH key H。

```python
"""
AES-GCM nonce-reuse attack: 還原 GHASH key H
需要 SageMath 環境（sage -python 或 SageMath notebook）

攻擊流程：
1. 觀察兩次使用相同 nonce 的 (AD, CT, tag)
2. 計算 tag₁ ⊕ tag₂ → 得到關於 H 的多項式方程
3. 在 GF(2¹²⁸) 上 factor 這個多項式 → 得到 H
4. 用 H 偽造任意 message 的 tag
"""

# --- 這段需要在 SageMath 環境中執行 ---

# GCM 使用的不可約多項式：x^128 + x^7 + x^2 + x + 1
# SageMath 中定義 GF(2^128)
# F.<a> = GF(2^128, modulus=x^128 + x^7 + x^2 + x + 1)

# 但 SageMath 的 GF(2^128) 用的是 lexicographic order，
# GCM 用的是 reflected bit order——需要轉換。
# 以下是概念性的 pseudocode：

"""
# 假設攻擊者觀察到：
# nonce 相同
# (AD₁, CT₁, tag₁) 和 (AD₂, CT₂, tag₂)

# Step 1: 構造多項式
# 把 AD 和 CT 的 128-bit blocks 轉成 GF(2^128) 元素
# blocks₁ = [A₁_1, ..., A₁_m, C₁_1, ..., C₁_n, len₁]
# blocks₂ = [A₂_1, ..., A₂_m, C₂_1, ..., C₂_n, len₂]

# Step 2: 計算差分多項式
# diff_blocks = [b1 ^ b2 for b1, b2 in zip(blocks₁, blocks₂)]
# T_diff = tag₁ ^ tag₂  (AES_K(J₀) 被消掉)
#
# 多項式 g(x) = diff_blocks[0]*x^s + diff_blocks[1]*x^(s-1) + ... + T_diff
# H 是 g(x) 的根

# Step 3: 在 GF(2^128) 上 factor g(x)
# roots = g.roots()
# 其中一個 root 就是 H

# Step 4: 驗證 H → 用已知的 (AD₁, CT₁, tag₁) 檢查
# GHASH_H(AD₁, CT₁) ⊕ mask₁ == tag₁ ?
# mask₁ = tag₁ ⊕ GHASH_H(AD₁, CT₁)  (= AES_K(J₀))

# Step 5: 偽造
# 選任意 (AD', CT')，計算 GHASH_H(AD', CT')
# tag' = GHASH_H(AD', CT') ⊕ mask₁
# (AD', CT', tag') 是合法的 AEAD output！
"""
```

以下是一個用純 Python 模擬的簡化版本（不依賴 SageMath，用小域演示概念）：

```python
"""
簡化版 nonce-reuse 攻擊演示（用 Python cryptography 庫）
展示 nonce reuse 如何洩漏 plaintext XOR
"""
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

key = AESGCM.generate_key(bit_length=128)
aesgcm = AESGCM(key)

# 致命錯誤：兩次加密用同一個 nonce
nonce = os.urandom(12)
ad = b""

p1 = b"attack at dawn!!"  # 16 bytes
p2 = b"attack at dusk!!"  # 16 bytes

ct1 = aesgcm.encrypt(nonce, p1, ad)
ct2 = aesgcm.encrypt(nonce, p2, ad)

# 攻擊者觀察到 ct1 和 ct2（不知道 p1, p2）
# CTR keystream 相同 → ct1 ⊕ ct2 = p1 ⊕ p2
c1_body = ct1[:-16]
c2_body = ct2[:-16]

xor_ct = bytes(a ^ b for a, b in zip(c1_body, c2_body))
xor_pt = bytes(a ^ b for a, b in zip(p1, p2))

print(f"ct1 ⊕ ct2 = {xor_ct.hex()}")
print(f"p1  ⊕ p2  = {xor_pt.hex()}")
print(f"相等？ {xor_ct == xor_pt}")
# True! 攻擊者知道了 p1 ⊕ p2

# 如果攻擊者知道 p1（known-plaintext），直接算出 p2
if xor_ct == xor_pt:
    recovered_p2 = bytes(a ^ b for a, b in zip(p1, xor_ct))
    print(f"\n已知 p1 = {p1}")
    print(f"還原 p2 = {recovered_p2}")
    # 輸出: attack at dusk!!

# tag 部分：tag1 ⊕ tag2 洩漏 GHASH 差分（需要 GF(2^128) 求解才能還原 H）
tag1 = ct1[-16:]
tag2 = ct2[-16:]
tag_xor = bytes(a ^ b for a, b in zip(tag1, tag2))
print(f"\ntag1 ⊕ tag2 = {tag_xor.hex()}")
print("（這個值包含 GHASH key H 的資訊——用 GF(2^128) factoring 可以還原 H）")
```

## 對比與取捨

| 特性 | AES-GCM | AES-CCM | AES-EAX |
|---|---|---|---|
| 內部結構 | CTR + GHASH | CTR + CBC-MAC | CTR + CMAC (OMAC) |
| Nonce 長度 | 推薦 96-bit | 56-bit 到 104-bit | 任意 |
| Tag 長度 | 128-bit（可截短到 96/64） | 32-128 bit | 128-bit |
| Nonce reuse 後果 | **致命**（H 洩漏 + plaintext XOR）| plaintext XOR（MAC key 不洩漏）| plaintext XOR（MAC key 不洩漏）|
| 硬體加速 | AES-NI + CLMUL（極快）| AES-NI（快）| AES-NI（快）|
| Parallelizable | **是**（CTR + GHASH 都可並行）| 否（CBC-MAC 是 sequential）| 否（CMAC 是 sequential）|
| 標準化 | NIST SP 800-38D, TLS 1.3 | NIST SP 800-38C, Bluetooth | NIST recommended |
| 最大加密量 / nonce | 2³² blocks ≈ 64 GB | 視 nonce/tag 設定 | 無硬限制 |

## 踩雷集錦

1. **「隨機 nonce 就安全」**：96-bit random nonce 在 2⁴⁸ messages 後有 birthday collision（機率 ≈ 50%）。如果你的系統會加密超過幾兆（trillion）個 messages，必須用 counter-based nonce 或 deterministic nonce。Google 的建議：用 96-bit counter nonce（前 32-bit 是 sender ID，後 64-bit 是 sequence number）。

2. **「tag 可以截短到 32-bit 省空間」**：GCM 允許截短 tag，但截到 32-bit 意味著攻擊者有 2⁻³² 的機率偽造成功——對 online protocol 來說太高了。NIST SP 800-38D 要求至少 64-bit tag；TLS 1.3 固定用 128-bit。

3. **「AES-GCM 在軟體上很快」**：不一定。GHASH 的 GF(2¹²⁸) 乘法如果沒有 CLMUL（Carry-less Multiplication）硬體指令，純軟體實作非常慢而且容易有 timing side-channel。在沒有 AES-NI + CLMUL 的平台（如舊手機、IoT 設備），ChaCha20-Poly1305 通常更快更安全。

4. **「64 GB per nonce 的限制不重要」**：GCM 的 32-bit counter 最多跑 2³² blocks = 64 GB per nonce。如果你用同一個 nonce 加密超過 64 GB 的資料，counter 會 wrap around → 等同 nonce reuse → 致命。加密大檔案時必須分段，每段用新 nonce。

5. **「AES-256-GCM 一定比 AES-128-GCM 好」**：安全性方面，AES-128 的 128-bit security level 對目前的計算能力綽綽有餘。AES-256 的好處在於抵抗 Grover's algorithm（量子），但 GCM 的 GHASH 不受 key length 影響——GHASH 的安全性只取決於 tag length（128-bit）。Multi-key attack 下 AES-256 有優勢，但對單一 key 場景差異不大。

## 進階：再往深一層

### GHASH 的 Horner's Method 實作

GHASH 的迭代計算其實就是 Horner's method（秦九韶演算法）：

```
f(H) = X₁·H^s + X₂·H^(s-1) + ... + Xₛ·H

用 Horner's method：
result = 0
for i in 1..s:
    result = (result ⊕ Xᵢ) · H

這正是 GCM spec 定義的 GHASH 迭代公式。
每一步只需要一次 GF(2¹²⁸) 乘法 + 一次 XOR。
```

### 為什麼 96-bit nonce 最好

GCM spec 允許任意長度的 nonce，但只有 96-bit nonce 時，initial counter J₀ 的計算是確定性的（`J₀ = nonce || 0x00000001`）。非 96-bit nonce 要先做一次 GHASH 才能得到 J₀——這引入了額外的複雜度和潛在的安全風險。

更重要的是，96-bit nonce 保證了 CTR 的 counter space 是完整的 32-bit（2³² blocks = 64 GB），而其他長度的 nonce 在 GHASH 壓縮後可能碰撞。

### AES-GCM 的 multi-key security

在 multi-key 場景下（比如 TLS server 同時服務數百萬連線），攻擊者可以嘗試碰撞不同 key 的 GHASH key H。由於 H = AES_K(0¹²⁸)，不同的 K 產生不同的 H，但如果攻擊者能觀察到足夠多的 (ciphertext, tag) pair，birthday attack 在 multi-key 場景下的影響需要考慮。

NIST 的建議：key 的使用壽命（key lifetime）應該考慮 nonce 空間和 multi-key attack 的交叉影響。

### GCM 的 key commitment 問題

AES-GCM 不提供 key commitment：同一個 (nonce, ciphertext, tag) 可能在兩個不同的 key 下都能成功解密，但解出不同的 plaintext。這在某些 protocol（如 OPAQUE、message franking）中是問題。AES-GCM-SIV 和帶 key commitment 的 AEAD（如 HPKE 的 Export 模式）可以解決。

## 動手練習

1. **觀察 nonce 影響**：用同一個 key + 同一個 plaintext + 同一個 AD，但不同的 nonce 加密 10 次。觀察 ciphertext 和 tag 都完全不同——即使輸入一模一樣。

2. **counter 上限測試**：寫一個程式嘗試用 AES-GCM 加密超過 2³⁹ bits（= 64 GB）的資料（用假資料 stream），觀察 library 會不會報錯。（提示：Python `cryptography` 有 64 GB 限制檢查。）

3. **nonce reuse 偵測**：在範例二的基礎上，寫一個函式接收一系列 (nonce, ciphertext, tag) tuples，檢查是否有 nonce 重複。計算：在 12-byte random nonce 下，加密多少筆資料後 nonce collision 的機率超過 1%？（提示：birthday problem，n ≈ √(2 × 2⁹⁶ × 0.01)）

4. **GHASH 手算**：在紙上（或用 Python）計算一個只有 1 block AD + 1 block ciphertext 的 GHASH。把 GF(2¹²⁸) 的乘法當黑盒（Python 的 `gmpy2` 或手寫 `gf128_mul`），驗證你理解了 GHASH 的迭代公式。

## 本章重點整理

- AES-GCM = AES-CTR（加密）+ GHASH（驗證），兩者用同一個 AES key 但角色不同
- GHASH key H = AES_K(0¹²⁸)，和 nonce 無關——一個 key 的所有 encryption 共用同一個 H
- Nonce reuse 在 GCM 下是致命的：消掉 AES_K(J₀) 後，攻擊者能在 GF(2¹²⁸) 上解方程還原 H，之後能偽造任意 tag
- 96-bit nonce 是最佳選擇——簡潔、確定性、完整的 32-bit counter space
- 每個 nonce 最多加密 2³² blocks（64 GB）——超過就等於 counter wrap = nonce reuse

## 自我檢核

- [ ] 能畫出 AES-GCM 的完整結構圖（CTR 加密 + GHASH + tag 生成）
- [ ] 能解釋 H = AES_K(0¹²⁸) 為什麼和 nonce 無關
- [ ] 能用自己的話解釋 nonce reuse 為什麼能還原 H（多項式方程 → GF(2¹²⁸) 求根）
- [ ] 能說出 96-bit nonce 和非 96-bit nonce 在 J₀ 計算上的差異
- [ ] 能計算 random nonce 的 birthday collision 機率（給定 nonce 長度和 message 數量）

## 延伸閱讀

- **David A. McGrew & John Viega, "The Galois/Counter Mode of Operation (GCM)" (2004)**
  - **讀哪裡**：Section 2 的 GCM spec、Section 4 的安全分析
  - **學什麼**：GCM 的原始設計文件——理解設計者的意圖和 trade-off
  - **關聯**：本章所有結構圖的原始出處

- **Antoine Joux, "Authentication Failures in NIST version of GCM" (2006)**
  - **讀哪裡**：全文（很短），重點在 nonce reuse 的攻擊構造
  - **學什麼**：GCM nonce reuse attack 的正式描述——GHASH key recovery 的數學細節
  - **關聯**：本章 nonce reuse 攻擊的學術來源

- **NIST SP 800-38D "Recommendation for Block Cipher Modes of Operation: Galois/Counter Mode (GCM) and GMAC"**
  - **讀哪裡**：Section 5 的演算法描述、Section 8 的使用指南
  - **學什麼**：官方 spec——nonce 長度、tag 長度、counter 結構的規範要求
  - **關聯**：GCM 的權威規範文件

- **Shay Gueron, "AES-GCM for Efficient Authenticated Encryption — Ending the Reign of HMAC-SHA-1?" (2013)**
  - **讀哪裡**：Section 3-4 的 CLMUL 優化
  - **學什麼**：AES-NI + CLMUL 如何讓 GCM 在現代 CPU 上達到接近 AES-CTR alone 的速度
  - **關聯**：為什麼 GCM 在有硬體加速的平台上碾壓其他 AEAD

→ [Ch 27 ChaCha20-Poly1305 + SIV](./27-chacha20-poly1305-siv.md)
