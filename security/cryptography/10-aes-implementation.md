# Ch 10 — AES 完整實作

> 目標：把 Ch 9 的數學變成可跑的程式。完整 Rijndael 流程（KeyExpansion + 10/12/14 輪 + 最後輪）、AES-NI 硬體加速差異、為什麼純軟體 AES 大多踩 cache timing 雷。

## AES-128 整體結構

```
plaintext (128-bit)
       │
       ▼
   AddRoundKey (round 0 用 round_key_0)
       │
       ▼
┌──────────────────┐
│ SubBytes         │
│ ShiftRows        │ ← round 1
│ MixColumns       │
│ AddRoundKey (k1) │
└──────────────────┘
       │
       ▼
   ... rounds 2-9 (相同) ...
       │
       ▼
┌──────────────────┐
│ SubBytes         │
│ ShiftRows        │ ← round 10（沒 MixColumns）
│ AddRoundKey (k10)│
└──────────────────┘
       │
       ▼
ciphertext (128-bit)
```

關鍵細節：

- **第 0 輪只 AddRoundKey**（pre-whitening）
- **最後一輪沒 MixColumns**（簡化解密邏輯，且不影響安全）
- AES-128 共 11 個 round key（每個 128-bit）

| 變體 | block | key | rounds | round keys |
|---|---|---|---|---|
| AES-128 | 128 | 128 | 10 | 11 |
| AES-192 | 128 | 192 | 12 | 13 |
| AES-256 | 128 | 256 | 14 | 15 |

## State 排列

128-bit state 排成 **4×4 byte 矩陣，column-major**：

```
plaintext bytes p_0..p_15 排成：
┌──┬──┬──┬──┐
│p0│p4│p8│p12│   ← column 0  column 1  column 2  column 3
│p1│p5│p9│p13│
│p2│p6│p10│p14│
│p3│p7│p11│p15│
└──┴──┴──┴──┘
```

注意 column-major：`p0..p3` 是 column 0，不是 row 0。

## KeyExpansion：產生 round key

AES-128 把 16-byte master key 擴展成 11 × 16 = 176 byte（11 個 round key）。

**演算法**（簡化）：

```
words[0..3] = master_key 切成 4 個 32-bit words

for i in 4..43:
    temp = words[i-1]
    if i % 4 == 0:
        temp = SubWord(RotWord(temp)) XOR Rcon[i/4]
    words[i] = words[i-4] XOR temp
```

- **RotWord**：32-bit word 內 4 byte 循環左移 1（[a,b,c,d] → [b,c,d,a]）
- **SubWord**：對 4 byte 各自 SBOX
- **Rcon**：round constant 陣列

```python
RCON = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]
# Rcon[i] 在 GF(2⁸) 是 (x)^(i-1)

def key_expansion(key: bytes) -> list[bytes]:
    """key 為 16 bytes，回傳 11 個 16-byte round key"""
    words = [list(key[i*4:(i+1)*4]) for i in range(4)]
    for i in range(4, 44):
        temp = words[i-1].copy()
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]                       # RotWord
            temp = [SBOX[b] for b in temp]                    # SubWord
            temp[0] ^= RCON[i // 4]
        new_word = [words[i-4][j] ^ temp[j] for j in range(4)]
        words.append(new_word)
    round_keys = []
    for r in range(11):
        rk = bytes(b for w in words[r*4:(r+1)*4] for b in w)
        round_keys.append(rk)
    return round_keys
```

## 完整 Python 實作

```python
SBOX = [
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    # ... (完整 256 個值見 FIPS 197)
]

INV_SBOX = [...]  # 反 SBOX，解密用

RCON = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]

def xtime(b):
    return ((b << 1) ^ 0x1b) & 0xFF if b & 0x80 else (b << 1) & 0xFF

def sub_bytes(state):
    return [SBOX[b] for b in state]

def shift_rows(state):
    """state 為 16-byte，column-major 排列"""
    s = state.copy()
    # row 1 left-shift 1
    s[1], s[5], s[9], s[13] = s[5], s[9], s[13], s[1]
    # row 2 left-shift 2
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    # row 3 left-shift 3
    s[3], s[7], s[11], s[15] = s[15], s[3], s[7], s[11]
    return s

def mix_columns(state):
    s = state.copy()
    for c in range(4):
        col = s[c*4:c*4+4]
        s0, s1, s2, s3 = col
        s[c*4]   = xtime(s0) ^ (xtime(s1) ^ s1) ^ s2 ^ s3
        s[c*4+1] = s0 ^ xtime(s1) ^ (xtime(s2) ^ s2) ^ s3
        s[c*4+2] = s0 ^ s1 ^ xtime(s2) ^ (xtime(s3) ^ s3)
        s[c*4+3] = (xtime(s0) ^ s0) ^ s1 ^ s2 ^ xtime(s3)
    return s

def add_round_key(state, round_key):
    return [s ^ k for s, k in zip(state, round_key)]

def aes128_encrypt(plaintext: bytes, key: bytes) -> bytes:
    assert len(plaintext) == 16 and len(key) == 16
    round_keys = key_expansion(key)
    state = list(plaintext)
    state = add_round_key(state, list(round_keys[0]))
    for r in range(1, 10):
        state = sub_bytes(state)
        state = shift_rows(state)
        state = mix_columns(state)
        state = add_round_key(state, list(round_keys[r]))
    state = sub_bytes(state)
    state = shift_rows(state)
    state = add_round_key(state, list(round_keys[10]))
    return bytes(state)
```

驗證用 NIST test vector：

```python
key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
pt  = bytes.fromhex("00112233445566778899aabbccddeeff")
expected_ct = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")

ct = aes128_encrypt(pt, key)
assert ct == expected_ct
```

## 解密

每個步驟有 inverse：

```
Inverse SubBytes  → 用 INV_SBOX
Inverse ShiftRows → 右移（不是左移）
Inverse MixColumns→ 用 inverse matrix
                    | 0E 0B 0D 09 |
                    | 09 0E 0B 0D |
                    | 0D 09 0E 0B |
                    | 0B 0D 09 0E |
```

解密順序倒過來：

```python
def aes128_decrypt(ciphertext, key):
    round_keys = key_expansion(key)
    state = list(ciphertext)
    state = add_round_key(state, list(round_keys[10]))
    for r in range(9, 0, -1):
        state = inv_shift_rows(state)
        state = inv_sub_bytes(state)
        state = add_round_key(state, list(round_keys[r]))
        state = inv_mix_columns(state)
    state = inv_shift_rows(state)
    state = inv_sub_bytes(state)
    state = add_round_key(state, list(round_keys[0]))
    return bytes(state)
```

注意順序與 Inverse 操作的對應 — 解密最後一輪也沒 inv MixColumns。

## C 高效實作：T-table

純軟體 AES 在 8-bit CPU 跑可以；32-bit CPU 用 **T-table** 加速：把 SubBytes + ShiftRows + MixColumns 三步合併查表。

```
T₀, T₁, T₂, T₃ 是 4 個 256-entry × 32-bit 表
每輪：
  out[0] = T₀[s_0] XOR T₁[s_5] XOR T₂[s_10] XOR T₃[s_15] XOR rk[0]
  out[1] = ...
```

整輪 16 次 lookup + 16 次 XOR = 極快。

**問題：cache timing attack**。lookup table 在 cache 裡，攻擊者觀察 cache miss pattern 推 key — 經典 attack 由 Bernstein 2005 公開。後來 Osvik / Tromer / Shamir 用 PRIME+PROBE 強化。

**OpenSSL 後來推 const-time T-table 變體**（pre-load 整 table 到 cache，避免 miss-based timing）— 但更好的解是用 AES-NI。

## AES-NI：Intel 硬體加速

Intel 2008 起 CPU 加 **AES-NI 指令**：

```
AESENC      xmm1, xmm2/m128    一輪 AES（SubBytes + ShiftRows + MixColumns + AddRoundKey）
AESENCLAST  xmm1, xmm2/m128    最後一輪
AESDEC      xmm1, xmm2/m128    一輪解密
AESDECLAST  xmm1, xmm2/m128    解密最後一輪
AESKEYGENASSIST                key expansion 輔助
AESIMC                          解密 round key 預處理
```

**一條指令 = 一輪 AES**。整個 AES-128 加密 11 條指令搞定。

性能：
- 純軟體 AES：~10 cycles/byte
- AES-NI：~1 cycle/byte
- AVX-512 + VAES（一條指令多 block）：< 0.5 cycle/byte

且 AES-NI **天生 const-time**（不用 lookup）— 沒 cache timing 問題。

```c
#include <wmmintrin.h>

__m128i aes_round(__m128i state, __m128i round_key) {
    return _mm_aesenc_si128(state, round_key);
}
```

ARM 也有 ARMv8 Crypto Extensions 提供類似指令（`AESE`、`AESMC`）。M1 / Cortex-A 都有。

## OpenSSL 怎麼做

OpenSSL 的 AES 實作分多個 path：

```
crypto/aes/
├── aes_core.c       純 C 版本（教學 / fallback）
├── asm/aesni-x86_64.pl     AES-NI 用 perlasm 寫
├── asm/vpaes-x86_64.pl     SSSE3 const-time 軟體版（無 AES-NI 時用）
├── asm/aes-armv8.pl        ARMv8 Crypto Extensions
├── asm/aes-mips64.pl       MIPS
└── asm/aes-ppc.pl          PowerPC
```

`vpaes`（vector permutation AES）值得一提：**沒 AES-NI 但有 SSSE3 時**（2008-2010 Intel CPU），用 `pshufb` 模擬 SBOX lookup 達 const-time 加速。Mike Hamburg 設計，妙。

## 一致性測試

寫完自己 AES 必跑 NIST test vector + 比對 OpenSSL：

```python
# 比對 cryptography library
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
def aes128_ref(pt, key):
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(pt) + enc.finalize()

import os
for _ in range(1000):
    key = os.urandom(16)
    pt = os.urandom(16)
    assert aes128_encrypt(pt, key) == aes128_ref(pt, key)
print("AES-128 自寫實作通過 1000 隨機測試")
```

## 一個常見誤解

「自己寫 AES 不安全是因為 algorithm 寫錯」

**通常不是**。算法照 FIPS 197 寫對其實不難（看上面 Python 程式 < 50 行）。**寫錯的不是算法，是 timing：**

- T-table lookup 觸發 cache miss → side-channel
- 比較 MAC 用 `memcmp` → 時間洩漏
- AES key expansion 在 secret-dependent branch
- 編譯器把 const-time code 優化掉

**production AES 一定用 AES-NI（硬體保證 const-time）+ 標準 library**。自己寫只在學習與 fallback 場景。

## 自我檢核

- [ ] 我能寫出 AES-128 完整 encrypt 流程
- [ ] 我能寫 KeyExpansion 並產生 11 個 round key
- [ ] 我能用 NIST test vector 驗證自己實作
- [ ] 我能說出 T-table 加速法與其 cache timing 風險
- [ ] 我知道 AES-NI 是什麼、為什麼 const-time
- [ ] 我能解釋為什麼 production 用 OpenSSL 而非自寫

下一章把 AES 變成「能加密任意長訊息」 — block cipher modes（ECB / CBC / CTR）+ padding oracle 攻擊。

→ [Ch 11 區塊模式 + padding oracle](./11-block-modes-padding-oracle.md)
