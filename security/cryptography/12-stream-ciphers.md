# Ch 12 — Stream Ciphers：RC4 的興衰與 ChaCha20

> 目標：理解 stream cipher 和 block cipher 的本質差異，能解釋 RC4 為什麼從 WEP/TLS 被全面淘汰，以及 Daniel Bernstein 設計的 ChaCha20 為什麼成為 TLS 1.3 的兩大加密算法之一。

## 環境

| 工具 | 版本 |
|------|------|
| Python | 3.11+ |
| Ubuntu | 22.04 |
| 套件 | `pip install cryptography` |

```bash
pip install cryptography
```

## 為什麼需要 stream cipher

Block cipher（AES）每次處理固定大小的 block（16 bytes）。如果你的資料是串流的（live audio、網路封包、即時通訊），等湊滿 16 bytes 再加密就太慢了。

Stream cipher 的模型：

```
Key + Nonce → Keystream Generator → Keystream
                                      │
Plaintext ──────────────────── XOR ───┘──→ Ciphertext
```

一次產生一個 byte（或一個 bit）的 keystream，立刻 XOR plaintext。**不需要等 block 對齊，不需要 padding。**

「那 AES-CTR 不就是 stream cipher 嗎？」

沒錯。AES-CTR 把 block cipher 當成 keystream generator 使用，效果和 stream cipher 一樣。但「原生」的 stream cipher（RC4、ChaCha20）有自己的設計，通常更輕量或在某些場景更快。

## 先建立直覺：XOR 的魔法與致命傷

Stream cipher 的安全性 **100% 依賴 keystream 的品質**：

```
如果 keystream = truly random（一次性密碼本 / One-Time Pad）
    → 資訊理論安全，無法破解

如果 keystream 有 bias（某些 byte 值出現頻率不均）
    → 統計攻擊可以恢復 plaintext

如果 keystream 被重複使用（nonce reuse）
    → C₁ ⊕ C₂ = P₁ ⊕ P₂ → crib dragging 解密
```

**所有 stream cipher 的安全故事都圍繞「keystream 的品質」和「nonce 有沒有重複」。**

## 核心概念：RC4（範例一）

### RC4 的歷史

- **1987**：Ron Rivest 為 RSA Data Security 設計 RC4（Rivest Cipher 4）
- **商業秘密**：RC4 從未公開 — 它是 RSA 的專利商品
- **1994**：有人在 Usenet 上匿名貼出 RC4 的原始碼（「alleged RC4」），和商用版本完全一致
- **之後**：成為最廣泛使用的 stream cipher — WEP、WPA-TKIP、SSL/TLS、PDF、MS Office...
- **2013-2015**：大量攻擊出爐，RC4 在 TLS 中被禁用（RFC 7465, 2015）

### RC4 的結構

RC4 極其精簡 — 兩個階段：

**KSA（Key-Scheduling Algorithm）**：用 key 初始化 256-byte 排列 S[0..255]

```python
def rc4_ksa(key):
    """RC4 Key-Scheduling Algorithm"""
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i], S[j] = S[j], S[i]   # swap
    return S
```

**PRGA（Pseudo-Random Generation Algorithm）**：產生 keystream

```python
def rc4_prga(S, length):
    """RC4 Pseudo-Random Generation Algorithm"""
    S = S[:]          # 不修改原始 S
    i = j = 0
    keystream = []
    for _ in range(length):
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        K = S[(S[i] + S[j]) % 256]
        keystream.append(K)
    return bytes(keystream)
```

**完整的 RC4 encrypt/decrypt**（XOR 是自己的逆）：

```python
def rc4(key, data):
    """RC4 加密 / 解密（一樣的操作）"""
    S = rc4_ksa(key)
    keystream = rc4_prga(S, len(data))
    return bytes(a ^ b for a, b in zip(data, keystream))

# 測試
key = b'SecretKey'
plaintext = b'Hello, RC4!'
ciphertext = rc4(key, plaintext)
recovered = rc4(key, ciphertext)
assert recovered == plaintext
print(f"RC4 encrypt: {ciphertext.hex()}")
print(f"RC4 decrypt: {recovered}")
```

RC4 的美麗在於它的精簡：**不到 30 行 code 就是完整實作**。

### RC4 的致命缺陷

#### Bias 1：第二個 byte 偏向 0x00

RC4 keystream 的第二個 byte（index 1）有大約 2/256 的機率是 0x00（正常應該是 1/256）。

```python
# 實驗：觀察 RC4 第二個 byte 的偏差
from collections import Counter
import os

count = Counter()
for _ in range(100000):
    key = os.urandom(16)
    S = rc4_ksa(key)
    ks = rc4_prga(S, 2)
    count[ks[1]] += 1

# 統計
expected = 100000 / 256
print(f"Expected per value: {expected:.0f}")
print(f"Byte 0x00 count:    {count[0]}")   # 大約是 expected 的 2 倍
print(f"Ratio:              {count[0]/expected:.2f}x")
```

#### Bias 2：前 256 bytes 整體偏差

Fluhrer, Mantin, Shamir（2001）發現 RC4 前幾百 bytes 的 keystream 有可度量的 statistical bias。這意味著：

**如果你知道 plaintext 的某些 bytes（例如 HTTP header 的固定格式），你可以利用 bias 推斷 key。**

#### WEP 攻擊（Fluhrer-Mantin-Shamir, 2001）

WEP（Wired Equivalent Privacy）用 RC4 加密 WiFi 封包：

```
WEP key = IV (3 bytes, 明文傳輸) || shared key
RC4(WEP key, plaintext)
```

3-byte IV 只有 2²⁴ = 16M 種，很快就會重複。更糟的是，FMS 攻擊利用 RC4 的 KSA 弱點，**只要收集夠多帶有特定 IV 的封包，就能直接恢復 shared key**。

```
攻擊所需封包數：約 500,000 ~ 2,000,000
攻擊時間：幾分鐘
工具：aircrack-ng
```

WEP 在 2004 年被 WPA/WPA2 取代。

#### TLS 中的 RC4 攻擊

- **AlFardan et al. (2013) "On the Security of RC4 in TLS"**：用 2³⁰ 次加密同一段 plaintext（利用 TLS 的 session resumption），利用 RC4 的前 256 bytes bias 恢復 session cookie
- **Bar-Mitzvah Attack (2015)**：利用 RC4 前幾 bytes 的 bias，13×2³⁰ 次嘗試恢復 plaintext 的前 100 bytes

**2015 年 RFC 7465 正式禁止在 TLS 中使用 RC4。**

### RC4 的 drop-N 緩解

一個常見的緩解措施：**丟掉前 N bytes 的 keystream**。

```python
def rc4_drop256(key, data):
    """RC4-drop256: 丟掉前 256 bytes keystream"""
    S = rc4_ksa(key)
    _ = rc4_prga(S, 256)         # 丟掉
    keystream = rc4_prga(S, len(data))
    return bytes(a ^ b for a, b in zip(data, keystream))
```

Drop-256 或 drop-768 能消除前 N bytes 的 bias，但**無法解決 RC4 更深層的結構性弱點**。RC4 已經被判死刑，不要在任何新系統中使用。

## 底層機制：ChaCha20 的設計

### 背景

Daniel Bernstein 在 2005 年設計了 **Salsa20**（eSTREAM 計畫入選算法），2008 年發表改良版 **ChaCha20**。

ChaCha20 的設計哲學和 RC4 完全相反：

| | RC4 | ChaCha20 |
|---|---|---|
| 設計年代 | 1987 | 2008 |
| 內部狀態 | 256 bytes permutation | 64 bytes (16 個 32-bit words) |
| 結構 | swap-based | ARX (Add-Rotate-XOR) |
| Nonce | 無（key 就是全部） | 96-bit nonce + 32-bit counter |
| 軟體性能 | 快（但有 bias） | 非常快（ARX 對 CPU 友好） |
| 側信道 | S-box 查表有 cache timing | 無 lookup table → constant-time |

### ChaCha20 的 state

ChaCha20 的 state 是 4×4 的 32-bit word 矩陣（共 512 bits = 64 bytes）：

```
State:
┌──────────┬──────────┬──────────┬──────────┐
│ "expa"   │ "nd 3"   │ "2-by"   │ "te k"   │  ← 常數 "expand 32-byte k"
├──────────┼──────────┼──────────┼──────────┤
│ key[0]   │ key[1]   │ key[2]   │ key[3]   │  ← 256-bit key（前半）
├──────────┼──────────┼──────────┼──────────┤
│ key[4]   │ key[5]   │ key[6]   │ key[7]   │  ← 256-bit key（後半）
├──────────┼──────────┼──────────┼──────────┤
│ counter  │ nonce[0] │ nonce[1] │ nonce[2] │  ← 32-bit counter + 96-bit nonce
└──────────┴──────────┴──────────┴──────────┘
```

常數 `"expand 32-byte k"` 的 ASCII 碼（little-endian）：

```python
CONSTANTS = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574]
```

**為什麼有常數？** 防止 related-key attack — 即使 key 和 nonce 很相似，常數的存在保證 state 的差異。

### Quarter Round

ChaCha20 的核心操作 — **quarter round**：

```python
def quarter_round(state, a, b, c, d):
    """ChaCha20 quarter round: ARX (Add-Rotate-XOR)"""
    def rotl32(v, n):
        return ((v << n) | (v >> (32 - n))) & 0xFFFFFFFF
    
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF; state[d] ^= state[a]; state[d] = rotl32(state[d], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF; state[b] ^= state[c]; state[b] = rotl32(state[b], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF; state[d] ^= state[a]; state[d] = rotl32(state[d], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF; state[b] ^= state[c]; state[b] = rotl32(state[b], 7)
```

**ARX = Add + Rotate + XOR。** 只用這三個操作，不需要 S-box、不需要乘法、不需要查表。

每個操作都是 constant-time → **天生免疫 timing side-channel**。

### 完整 ChaCha20 Block Function

```python
import struct

def chacha20_block(key, counter, nonce):
    """ChaCha20 block function: 產生 64 bytes keystream"""
    # 初始化 state
    state = list(CONSTANTS)
    state += struct.unpack('<8I', key)      # 8 個 32-bit words from 256-bit key
    state += [counter]
    state += struct.unpack('<3I', nonce)    # 3 個 32-bit words from 96-bit nonce
    
    # 保存初始 state
    initial = state[:]
    
    # 20 rounds (10 double-rounds)
    for _ in range(10):
        # Column rounds
        quarter_round(state, 0, 4, 8, 12)
        quarter_round(state, 1, 5, 9, 13)
        quarter_round(state, 2, 6, 10, 14)
        quarter_round(state, 3, 7, 11, 15)
        # Diagonal rounds
        quarter_round(state, 0, 5, 10, 15)
        quarter_round(state, 1, 6, 11, 12)
        quarter_round(state, 2, 7, 8, 13)
        quarter_round(state, 3, 4, 9, 14)
    
    # 加回初始 state（mod 2³²）
    state = [(s + i) & 0xFFFFFFFF for s, i in zip(state, initial)]
    
    # 轉成 bytes（little-endian）
    return struct.pack('<16I', *state)


def chacha20_encrypt(key, nonce, plaintext, initial_counter=0):
    """ChaCha20 加密"""
    ciphertext = bytearray()
    counter = initial_counter
    
    for i in range(0, len(plaintext), 64):
        block = plaintext[i:i+64]
        keystream = chacha20_block(key, counter, nonce)
        for j in range(len(block)):
            ciphertext.append(block[j] ^ keystream[j])
        counter += 1
    
    return bytes(ciphertext)
```

### 驗證（RFC 8439 測試向量）

```python
# RFC 8439 §2.4.2 — "Ladies and Gentlemen of the class of '99..."
key = bytes.fromhex('000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f')
nonce = bytes.fromhex('000000000000004a00000000')
plaintext = (b"Ladies and Gentlemen of the class of '99: "
             b"If I could offer you only one tip for the future, sunscreen would be it.")
ct = chacha20_encrypt(key, nonce, plaintext, initial_counter=1)
assert ct[:16].hex() == '6e2e359a2568f98041ba0728dd0d6981'
print("ChaCha20 RFC 8439 test vector: PASS")
```

## 進一步用法：ChaCha20-Poly1305 AEAD（範例二）

實務中 ChaCha20 幾乎總是和 **Poly1305 MAC** 搭配使用，形成 AEAD（Authenticated Encryption with Associated Data）：

```python
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
import os

key = ChaCha20Poly1305.generate_key()
aead = ChaCha20Poly1305(key)
nonce = os.urandom(12)   # 96-bit nonce

plaintext = b'Sensitive data here'
aad = b'associated data'   # 不加密但認證的資料（例如 header）

ct = aead.encrypt(nonce, plaintext, aad)
print(f"Extra bytes (Poly1305 tag): {len(ct) - len(plaintext)}")  # 16 bytes

pt = aead.decrypt(nonce, ct, aad)
assert pt == plaintext
print("ChaCha20-Poly1305 roundtrip: PASS")
```

注意 `cryptography` 的裸 `ChaCha20` nonce 格式是 128-bit（前 32-bit 是 counter），和 RFC 8439 的 96-bit nonce 不同。用 `ChaCha20Poly1305` 就是標準的 96-bit nonce。

## 對比與取捨

| | RC4 | ChaCha20 | AES-CTR |
|---|---|---|---|
| Key size | 40-256 bits | 256 bits | 128/192/256 bits |
| Nonce | 無 | 96-bit | 取決於實作 |
| 結構 | Swap table | ARX | Block cipher + counter |
| 軟體速度 | 快 | 非常快 | 快（無 AES-NI）/ 極快（有 AES-NI） |
| 硬體加速 | 無 | 無專用（SIMD 有幫助） | AES-NI |
| Timing side-channel | 有（table lookup） | 無（pure ARX） | 有（軟體）/ 無（AES-NI） |
| 已知攻擊 | 多（bias, FMS, ...） | 無 | 無（AES 本身安全） |
| 2024 狀態 | **已死** | TLS 1.3 標準 | TLS 1.3 標準 |
| 適用場景 | 無 | 無 AES-NI 的平台 | 有 AES-NI 的平台 |

**為什麼 TLS 1.3 同時有兩套？** 有 AES-NI 的 x86 server 用 AES-GCM 最快；沒有 AES-NI 的 ARM 手機用 ChaCha20-Poly1305 快 3 倍以上。Google 在 2014 年率先在 Chrome 中啟用 ChaCha20-Poly1305，因為 Android 手機大多沒有 AES-NI。

## 踩雷集錦

### 雷 1：「stream cipher 不需要 nonce」

RC4 確實沒有 nonce 的概念（key 就是全部），但這正是它的弱點。ChaCha20 **強制要求 nonce**。

**nonce reuse = keystream reuse = 災難。** 這對所有 stream cipher 和 CTR mode 都成立。

```python
# 災難：nonce reuse
key = b'\x00' * 32
nonce = b'\x00' * 16   # 固定 nonce

pt1 = b'Attack at dawn!!'
pt2 = b'Attack at dusk!!'

ct1 = chacha20_encrypt(key, nonce[:12], pt1)
ct2 = chacha20_encrypt(key, nonce[:12], pt2)

xored = bytes(a ^ b for a, b in zip(ct1, ct2))
print(f"ct1 ⊕ ct2 = pt1 ⊕ pt2 = {xored}")
# 攻擊者做 crib dragging 就能恢復兩段 plaintext
```

### 雷 2：RC4 的 key 重複使用

RC4 沒有 nonce，所以 **key 重複使用 = nonce 重複使用**。

WEP 的「解法」是在 key 前面加 3-byte IV。但 3 bytes = 16M 種可能，在繁忙的 WiFi 網路上幾小時就會用完。

### 雷 3：用 RC4 時不 drop 前 N bytes

如果你非得用 RC4（legacy 系統），**至少 drop 前 768 bytes 的 keystream**。但更好的做法是：**不要用 RC4**。

### 雷 4：ChaCha20 裸用（不搭配 Poly1305）

ChaCha20 只提供加密，不提供完整性。攻擊者可以翻轉 ciphertext 的任意 bit → plaintext 對應 bit 被翻轉。

**永遠用 ChaCha20-Poly1305，不要裸用 ChaCha20。**

## 進階：Salsa20 vs ChaCha20

ChaCha20 是 Salsa20 的改良版。主要差異：

| | Salsa20 | ChaCha20 |
|---|---|---|
| Quarter round | 不同的 ARX pattern | 改良的 ARX pattern |
| Diffusion 速度 | 4 rounds 達到 full diffusion | **2 rounds 達到 full diffusion** |
| 安全 margin | 20 rounds（相當充裕） | 20 rounds（更充裕） |

ChaCha20 的改良使得每一 round 的 diffusion 更強，所以 20 rounds 後的安全 margin 更大。

### XChaCha20：extended nonce

ChaCha20 的 96-bit nonce 有碰撞風險（birthday bound ≈ 2⁴⁸）。**XChaCha20** 用 192-bit nonce（用 HChaCha20 做 key derivation），birthday bound 推到 2⁹⁶，實務上不可能碰撞。libsodium 預設使用 XChaCha20-Poly1305。

## 動手練習

1. **RC4 bias 實驗**：生成 100,000 個隨機 key，收集每個 key 的 RC4 keystream 前 16 bytes，統計每個位置每個 byte 值的出現頻率，畫出 heatmap（偏差應該在前幾個 byte 最明顯）。

2. **ChaCha20 手刻驗證**：用本章的手刻 ChaCha20 加密一段文字，再用 `cryptography` 套件解密，確認結果一致。

3. **WEP 模擬攻擊**：模擬 WEP 的 IV+key 結構，生成 50,000 個封包（隨機 3-byte IV + 固定 key），統計 IV 碰撞次數。

4. **nonce reuse 攻擊**：給你兩段用相同 key+nonce 的 ChaCha20 ciphertext，其中一段的 plaintext 已知，恢復另一段的 plaintext。

## 重點整理

- Stream cipher = keystream generator + XOR，不需要 padding，適合串流資料
- RC4：精簡（30 行 code）但有嚴重 bias — 前 256 bytes 的 keystream 有統計偏差，已被 TLS/WiFi 全面禁用
- ChaCha20：ARX 設計（Add-Rotate-XOR），無查表 → constant-time，256-bit key + 96-bit nonce，TLS 1.3 標準
- ChaCha20 在無 AES-NI 的平台比 AES-GCM 快 3 倍+
- **nonce reuse 對所有 stream cipher 都是致命的** — 兩段 ciphertext XOR = 兩段 plaintext XOR
- 實務中永遠搭配 MAC：ChaCha20-Poly1305 或 AES-GCM

## 自我檢核

- [ ] 能解釋 stream cipher 和 block cipher 的本質差異
- [ ] 能描述 RC4 的 KSA 和 PRGA 流程
- [ ] 能說出至少 3 個 RC4 被淘汰的原因
- [ ] 能解釋 ChaCha20 的 quarter round（ARX）
- [ ] 能說明為什麼 nonce reuse 對 stream cipher 是致命的

## 延伸閱讀

- **RFC 8439 "ChaCha20 and Poly1305 for IETF Protocols"**：ChaCha20-Poly1305 的正式規範
- **Bernstein 2008 "ChaCha, a variant of Salsa20"**：ChaCha20 的原始論文
- **Fluhrer, Mantin, Shamir 2001 "Weaknesses in the Key Scheduling Algorithm of RC4"**：FMS 攻擊（WEP 的喪鐘）
- **AlFardan et al. 2013 "On the Security of RC4 in TLS and WPA"**
- **RFC 7465 "Prohibiting RC4 Cipher Suites" (2015)**

---

> **下一章**：[Ch 13 — Hash Functions：SHA-2 與 Length Extension Attack](13-hash-sha2.md) — 離開加密（encryption），進入 hash（one-way function）。SHA-256 怎麼運作？為什麼 SHA-256(secret || message) 不安全？
