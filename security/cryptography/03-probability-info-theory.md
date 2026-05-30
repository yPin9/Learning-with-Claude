# Ch 3 — 機率與資訊論速覽

> **目標**：理解 entropy、Shannon 的完美保密定義、PRG/PRF 的概念、IND-CPA 直覺——這些是密碼學所有安全定義的根基。

> 這章的數學比 Ch 2 輕，但概念更抽象。每個定義都會配一個「如果你的系統不滿足這個定義，會被怎麼打」的具體場景。

## Entropy（熵）

### 定義

給定一個隨機變數 X，它的值域是 {x₁, x₂, ..., xₙ}，每個值的機率是 P(xᵢ)。Shannon entropy 定義為：

```
H(X) = -Σ P(xᵢ) × log₂(P(xᵢ))
```

單位是 bit。

### 直覺

Entropy 衡量的是**不確定性**——「需要幾個 bit 才能描述這個隨機事件的結果」。

```
公正硬幣: P(正) = P(反) = 1/2
H(X) = -(1/2)log₂(1/2) - (1/2)log₂(1/2) = 1 bit
→ 需要 1 bit 描述結果（0 或 1）

偏斜硬幣: P(正) = 0.99, P(反) = 0.01
H(X) = -(0.99)log₂(0.99) - (0.01)log₂(0.01) ≈ 0.081 bit
→ 結果幾乎確定是「正」，不確定性很低

均勻骰子: P(1) = P(2) = ... = P(6) = 1/6
H(X) = -6 × (1/6)log₂(1/6) = log₂(6) ≈ 2.585 bit
```

**關鍵性質**：在給定值域大小 n 下，均勻分佈的 entropy 最大，為 log₂(n)。

Python 計算：

```python
import math

def entropy(probs):
    """計算 Shannon entropy (bits)"""
    return -sum(p * math.log2(p) for p in probs if p > 0)

# 公正硬幣
print(f"公正硬幣: {entropy([0.5, 0.5]):.3f} bit")        # 1.000

# 偏斜硬幣
print(f"偏斜硬幣: {entropy([0.99, 0.01]):.3f} bit")       # 0.081

# 均勻骰子
print(f"均勻骰子: {entropy([1/6]*6):.3f} bit")            # 2.585

# 英文字母（不均勻——e 最常見，z 最少見）
# 大約 4.0 bit（而 log₂(26) ≈ 4.7——因為不均勻，entropy 比最大值低）
```

### Entropy 在密碼學的意義

一把 128-bit 的 AES key 如果是從均勻隨機分佈產生的，entropy 是 128 bit——攻擊者平均需要嘗試 2^128 次才能猜到。

但如果你的 key 是從 8 個英文小寫字母的密碼導出的：
- 每個字母的 entropy 上界是 log₂(26) ≈ 4.7 bit
- 8 個字母最多 37.6 bit entropy
- 但實際的密碼通常更低（常用詞、重複字母），大概 20–30 bit

這就是 KDF（Key Derivation Function）存在的原因：用 Argon2 或 scrypt 把低 entropy 的 password 拉慢暴力搜索的速度（Ch 17）。

## Shannon 的完美保密（Perfect Secrecy）

### 定義

一個加密系統滿足完美保密，當且僅當：

```
P(M = m | C = c) = P(M = m)    對所有 m, c
```

翻譯：**看到密文 c 之後，攻擊者對明文 m 的猜測和之前一樣好——密文不洩漏任何關於明文的資訊。**

等價定義（更直覺）：

```
P(C = c | M = m₁) = P(C = c | M = m₂)    對所有 m₁, m₂, c
```

翻譯：不管明文是什麼，產生特定密文 c 的機率相同。攻擊者看到 c，無法判斷明文是 m₁ 還是 m₂。

### Shannon 的定理

Shannon 在 1949 年證明：滿足完美保密的加密系統，key space 的大小必須 ≥ message space 的大小。

直白地說：**key 至少要和明文一樣長**。

這就是為什麼 One-Time Pad（OTP）是唯一滿足完美保密的實用系統——而它要求 key 和明文等長。AES-256 的 key 只有 256 bit，但可以加密任意長度的明文，所以 AES 不滿足完美保密。AES 的安全性是建立在「計算上不可行」的假設上，而非「資訊論上不可能」。

## One-Time Pad（OTP）

### 為什麼 OTP 滿足完美保密

OTP 的加密：C = M ⊕ K（明文 XOR key）

其中 K 是和 M 等長的均勻隨機 key，且只使用一次。

```python
import os

def otp_encrypt(plaintext: bytes) -> tuple[bytes, bytes]:
    key = os.urandom(len(plaintext))  # key 和明文等長，完全隨機
    ciphertext = bytes(p ^ k for p, k in zip(plaintext, key))
    return ciphertext, key

def otp_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    return bytes(c ^ k for c, k in zip(ciphertext, key))

# 演示
msg = b"ATTACK AT DAWN"
ct, key = otp_encrypt(msg)
pt = otp_decrypt(ct, key)
print(f"明文: {msg}")
print(f"密鑰: {key.hex()}")
print(f"密文: {ct.hex()}")
print(f"解密: {pt}")
assert pt == msg
```

完美保密的直覺證明：

對任意密文 c 和任意明文 m，存在唯一的 key k = c ⊕ m 使得加密 m 得到 c。因為 key 是均勻隨機的，每個 k 出現的機率相同。所以不管明文是什麼，密文 c 出現的機率都一樣——符合完美保密的定義。

### OTP 的致命限制

1. **key 必須和明文等長**：加密 1 GB 的檔案需要 1 GB 的 key。key 的分配和儲存成本等於直接分配明文
2. **key 不可重用**：「One-Time」是關鍵——如果同一把 key 加密兩段明文：

```python
# 重用 key 的災難
key = os.urandom(14)
m1 = b"ATTACK AT DAWN"
m2 = b"DEFEND AT DUSK"

c1 = bytes(p ^ k for p, k in zip(m1, key))
c2 = bytes(p ^ k for p, k in zip(m2, key))

# 攻擊者計算 c1 ⊕ c2 = m1 ⊕ m2（key 被消掉了）
leak = bytes(a ^ b for a, b in zip(c1, c2))
print(f"c1 ⊕ c2 = {leak}")
print(f"m1 ⊕ m2 = {bytes(a ^ b for a, b in zip(m1, m2))}")
# 兩者相同——攻擊者拿到了兩段明文的 XOR
# 對英文文本，用頻率分析可以從 m1 ⊕ m2 還原出兩段原文
```

這就是為什麼實務上不用 OTP——而是用 PRG 把短 key「展開」成長的偽隨機序列。

## PRG（Pseudorandom Generator）

### 定義

PRG 是一個確定性函式 G: {0,1}^s → {0,1}^n，其中 n >> s（輸出比輸入長很多）。

安全的 PRG 滿足：G(seed) 和真隨機的 n-bit 字串**在多項式時間內不可區分**。

也就是說：沒有高效的演算法能判斷你手上的 n-bit 字串是 G(seed) 產生的還是真隨機的。

### 直覺

PRG 是 OTP 的「便宜替代品」：

```
OTP:  C = M ⊕ K           （K 是真隨機，和 M 等長）
PRG:  C = M ⊕ G(seed)     （seed 是短的 key，G 展開成和 M 等長）
```

AES-CTR（counter mode）就是這個思路：用 AES 加密遞增的 counter 來產生偽隨機序列，再 XOR 明文。seed 是 AES key（128/256 bit），輸出可以任意長。

```python
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# AES-CTR 就是一個 PRG
key = os.urandom(32)       # 256-bit seed
nonce = os.urandom(16)     # counter 初始值

cipher = Cipher(algorithms.AES(key), modes.CTR(nonce))
encryptor = cipher.encryptor()

# 加密就是 PRG(key, nonce) ⊕ plaintext
plaintext = b"This is a much longer message than the key itself."
ciphertext = encryptor.update(plaintext) + encryptor.finalize()
print(f"AES-CTR 加密: {ciphertext.hex()[:40]}...")
```

PRG 不滿足完美保密（key 比明文短），但滿足**計算安全性**——破解需要的計算量超過攻擊者的能力。

### PRG 不是隨便寫的

一個常見錯誤是用 `random.random()` 或 `rand()` 來產生加密用的偽隨機數。Python 的 `random` 模組用 Mersenne Twister——觀察 624 個 32-bit 輸出就能完全預測後續所有輸出。它是統計用的 PRNG，不是密碼學用的 PRG。

```python
# 錯誤：用 random 產生 key
import random
key_bad = bytes([random.randint(0, 255) for _ in range(32)])
# Mersenne Twister 可預測——key 不安全

# 正確：用 os.urandom 或 secrets
import secrets
key_good = secrets.token_bytes(32)
# 來自 OS 的 CSPRNG（Cryptographically Secure PRG）
```

## PRF（Pseudorandom Function）

### 定義

PRF 是一個 keyed function F: K × X → Y，對隨機選擇的 key k，F(k, ·) 和從 X 到 Y 的所有函式中均勻隨機選的一個函式**在多項式時間內不可區分**。

### 直覺

PRG 產生一長串偽隨機 bits。PRF 是一個「查詢式」的偽隨機——給任意輸入 x，回傳一個偽隨機的輸出 F(k, x)。

AES 本身就是一個 PRF：

```
AES(key, block) → 128-bit output
```

給定 key，AES 把任意 128-bit block 映射到一個看起來隨機的 128-bit output。如果 AES 是安全的 PRF，攻擊者不能區分 AES(key, ·) 和一個真正的隨機函式。

PRF 是 MAC 的基礎——HMAC 就是建立在 PRF 性質上。

### PRG 和 PRF 的關係

```
PRG: seed → 長隨機序列         （固定輸入，長輸出）
PRF: (key, x) → 隨機值         （可查詢，多輸入）

一個安全的 PRF 可以建構 PRG（把 counter 當 x）
一個安全的 PRG 可以建構 PRF（GGM construction）
```

## IND-CPA（Indistinguishability under Chosen Plaintext Attack）

### 直覺

IND-CPA 是衡量加密方案安全性的標準定義。用一個遊戲（game）來描述：

```
IND-CPA 遊戲:

1. 攻擊者選兩段等長的明文 m₀ 和 m₁，交給 challenger
2. Challenger 隨機選 b ∈ {0, 1}，加密 m_b，把密文 c 交給攻擊者
3. 攻擊者可以要求 challenger 加密任意其他明文（chosen plaintext）
4. 攻擊者猜 b 是 0 還是 1

如果攻擊者猜對的機率不比 1/2 好（除了 negligible 的優勢），
加密方案是 IND-CPA 安全的。
```

翻譯成白話：**攻擊者自己選了兩段明文，拿到其中一段的密文，而且還可以要求加密任意其他明文——在這麼大的優勢下，仍然猜不出密文對應哪段明文。**

### 哪些方案滿足 IND-CPA，哪些不滿足

**不滿足 IND-CPA：ECB mode**

```python
from Crypto.Cipher import AES
import os

key = os.urandom(16)

# ECB mode：相同明文 → 相同密文
cipher = AES.new(key, AES.MODE_ECB)
block = b'YELLOW SUBMARINE'   # 16 bytes

c1 = cipher.encrypt(block)
c2 = cipher.encrypt(block)
print(f"c1 == c2: {c1 == c2}")  # True!

# IND-CPA 攻擊：
# 攻擊者選 m0 = "AAAAAAAAAAAAAAAA" 和 m1 = "BBBBBBBBBBBBBBBB"
# 收到密文 c
# 要求加密 m0，拿到 c0
# 如果 c == c0 → b = 0，否則 b = 1
# 猜對機率 = 100%
```

ECB 的問題：確定性加密（deterministic encryption）永遠不可能 IND-CPA 安全——相同明文永遠產生相同密文，攻擊者可以用 chosen plaintext 直接比對。

**滿足 IND-CPA：CTR mode、CBC mode（with random IV）**

```python
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

key = os.urandom(32)

# CTR mode：每次加密用不同 nonce
nonce1 = os.urandom(16)
nonce2 = os.urandom(16)

cipher1 = Cipher(algorithms.AES(key), modes.CTR(nonce1)).encryptor()
cipher2 = Cipher(algorithms.AES(key), modes.CTR(nonce2)).encryptor()

block = b'YELLOW SUBMARINE'
c1 = cipher1.update(block) + cipher1.finalize()
c2 = cipher2.update(block) + cipher2.finalize()
print(f"c1 == c2: {c1 == c2}")  # False!——不同 nonce 產生不同密文
```

隨機化（random nonce/IV）是達到 IND-CPA 的關鍵。

### IND-CPA vs IND-CCA

IND-CPA 的遊戲裡，攻擊者可以要求**加密**任意明文。IND-CCA（Chosen Ciphertext Attack）更強——攻擊者還可以要求**解密**任意密文（除了 challenge 密文本身）。

```
IND-CPA: 攻擊者有 encryption oracle
IND-CCA: 攻擊者有 encryption oracle + decryption oracle
```

AEAD 方案（AES-GCM、ChaCha20-Poly1305）滿足 IND-CCA。單純的 AES-CBC 不滿足 IND-CCA——Padding Oracle attack（Ch 11）就是利用 decryption oracle 來破解。

這就是為什麼現代做法是 AEAD 而非單獨的加密模式：AEAD 在 IND-CCA 下安全，比 IND-CPA 強。

## 為什麼這些概念重要

密碼學的所有安全聲明都建立在這些概念上：

| 概念 | 用在哪裡 | 如果忽略會怎樣 |
|---|---|---|
| Entropy | key 的強度評估、密碼強度評估 | 用低 entropy 的 key → 暴力破解可行 |
| Perfect Secrecy | OTP 的安全證明 | 理解為什麼 OTP 以外的方案都依賴計算假設 |
| PRG | stream cipher、CTR mode | 用非密碼學 PRNG → key 可預測 |
| PRF | block cipher（AES）、MAC | AES 的安全假設就是「AES 是 PRF」 |
| IND-CPA | 所有加密方案的最低標準 | 用 ECB mode → 密文洩漏明文的模式 |
| IND-CCA | AEAD 的安全標準 | 只有 IND-CPA → Padding Oracle 打穿 |

如果你跳過這章，後面每次看到「這個方案是 IND-CPA 安全的」你都不知道這句話在說什麼。花時間把 IND-CPA 的遊戲理解透——它是密碼學安全定義的範本，後面的 IND-CCA、EUF-CMA（簽章安全性）、PRF 安全性都是同一個框架的變體。

## 踩雷集錦

### 1. 「Entropy 高 = 安全」

Entropy 衡量不確定性，不直接等於安全強度。一把 256-bit key 的 entropy 是 256 bit——如果它是均勻隨機的。但如果這把 key 是用 `hash("password123")` 產生的，entropy 只有 password 本身的 entropy（大約 20 bit），即使輸出是 256 bit。

Entropy 是必要條件（低 entropy 一定不安全），但不是充分條件（高 entropy 的 key 還可以被 side-channel 或 protocol misuse 打穿）。

### 2. 「PRG 只要通過統計測試就安全」

NIST SP 800-22 定義了一套統計隨機性測試（frequency test、runs test、serial test 等）。通過這些測試是**必要不充分條件**——一個 PRG 如果連統計測試都過不了，它一定不安全。但通過統計測試不代表它是密碼學安全的。

Mersenne Twister 通過幾乎所有統計測試，但它不是密碼學安全的 PRG——觀察 624 個輸出就能預測之後的全部輸出。

### 3. IND-CPA 和 IND-CCA 的區別

容易搞混。記住：

- **IND-CPA**：攻擊者只能加密 → AES-CTR 夠用
- **IND-CCA**：攻擊者還能解密 → 需要 AEAD

實務上的差別：如果你的系統會在解密失敗時回傳錯誤訊息（例如 web server 回 "Padding error"），攻擊者就有了 decryption oracle。這時你需要 IND-CCA 安全——也就是 AEAD。

Padding Oracle（Ch 11）是最經典的「只有 IND-CPA 不夠」的案例。

## 本章重點整理

- Entropy H(X) = -Σ p(x) log₂ p(x)——衡量不確定性，均勻分佈最大
- Perfect Secrecy：密文不洩漏明文的任何資訊；Shannon 證明 key 必須 ≥ 明文長度
- OTP 是唯一滿足完美保密的實用系統，但 key 等長且不可重用
- PRG：短 seed → 長偽隨機序列（AES-CTR 就是 PRG）
- PRF：keyed 偽隨機函式（AES 就是 PRF）
- IND-CPA：攻擊者選兩段明文，拿到其中一段的密文，猜不出是哪一段
- IND-CCA 比 IND-CPA 強——攻擊者還有 decryption oracle；AEAD 滿足 IND-CCA

## 自我檢核

- [ ] 能解釋為什麼 8 個英文字母的密碼的 entropy 不是 64 bit（8 × 8）
- [ ] 能用自己的話說 perfect secrecy 的定義，並解釋為什麼 AES 不滿足它
- [ ] 能解釋為什麼 OTP 的 key 重用會洩漏 m₁ ⊕ m₂
- [ ] 能區分 PRG 和 PRF，並各舉一個對應的密碼學方案
- [ ] 能描述 IND-CPA 遊戲的流程，並解釋為什麼 ECB mode 不是 IND-CPA 安全的
- [ ] 能解釋為什麼 Padding Oracle 攻擊意味著 AES-CBC 不是 IND-CCA 安全的

## 延伸閱讀

### 論文

- **"Communication Theory of Secrecy Systems"（Shannon, 1949）**
  - **讀哪裡**：Section 1–3（secrecy systems 的定義、perfect secrecy 的證明）
  - **學什麼**：密碼學的數學奠基——所有現代安全定義的起點
  - **前提**：基礎機率論

### 書籍

- **《A Graduate Course in Applied Cryptography》— Ch 2, Ch 3** — Boneh & Shoup
  - **讀哪裡**：Ch 2（Encryption）講 IND-CPA 的完整形式化定義和 reduction；Ch 3（Message Integrity）講 PRF 和 MAC 的安全模型
  - **學什麼**：本章所有概念的嚴格數學版本——game-based security definition 的完整寫法
  - **前提**：本章的直覺理解 + 基礎離散數學

- **《Serious Cryptography》— Ch 1, Ch 2** — Aumasson
  - **讀哪裡**：Ch 1（Encryption）的 security notion 部分、Ch 2（Randomness）
  - **學什麼**：更偏工程角度的安全定義解釋，配合真實案例
  - **前提**：無

→ [Ch 4 古典密碼：Caesar、Vigenère、頻率分析](./04-classical-ciphers.md)
