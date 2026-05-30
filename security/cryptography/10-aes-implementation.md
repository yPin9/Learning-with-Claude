# Ch 10 — AES 完整實作：Rijndael 全流程與 AES-NI

> 目標：手刻完整的 AES-128 encrypt 和 decrypt（Python），理解 key expansion 的每一步，最後用 `cryptography` 套件驗證結果和 OpenSSL 一致。附帶理解 AES-NI 硬體加速的原理和為什麼它比軟體快 10 倍以上。

## 環境

| 工具 | 版本 |
|------|------|
| Python | 3.11+ |
| Ubuntu | 22.04 |
| 套件 | `pip install cryptography` |

```bash
python3 -c "from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes; print('OK')"
```

## 為什麼需要手刻 AES

「能用 library 就好，為什麼要手刻？」

1. **理解攻擊的前提**：Padding Oracle（Ch 11）、timing side-channel、cache attack 都需要你理解 AES 的內部結構。不知道 round 怎麼跑的人寫不出 padding oracle exploit。
2. **debug 能力**：當你的 TLS 握手解密失敗，需要逐 round 追蹤 state 才能定位問題。
3. **面試**：密碼學面試經典題 — 畫出 AES 的 round 結構、解釋 key schedule。

**本章的手刻版只用於教學。生產環境永遠用 OpenSSL / AES-NI。**

## 先建立直覺：AES-128 的全景圖

```
Plaintext (16 bytes)          Key (16 bytes)
      │                           │
      ▼                           ▼
┌──────────────┐         ┌──────────────────┐
│ AddRoundKey  │◄────────│  Round Key 0     │
│ (initial)    │         │  (= original key)│
└──────┬───────┘         └──────────────────┘
       │
       ▼
┌──────────────┐         ┌──────────────────┐
│  Round 1     │◄────────│  Round Key 1     │
│  SubBytes    │         │  (key expansion) │
│  ShiftRows   │         └──────────────────┘
│  MixColumns  │
│  AddRoundKey │
└──────┬───────┘
       │
      ...  (Round 2~9 相同結構)
       │
       ▼
┌──────────────┐         ┌──────────────────┐
│  Round 10    │◄────────│  Round Key 10    │
│  SubBytes    │         │                  │
│  ShiftRows   │         └──────────────────┘
│  ★ 無 MixCol │
│  AddRoundKey │
└──────┬───────┘
       │
       ▼
   Ciphertext (16 bytes)
```

關鍵：

- **10 輪**（AES-128），每輪用不同的 round key
- **最後一輪沒有 MixColumns**（因為加了也不會增加安全性，反而多一步運算）
- 開始前先 XOR 一次原始 key（Round Key 0）

## 核心概念：完整的 AES-128 Encrypt（範例一）

### 預備：Ch 9 的工具函數

```python
# ─── GF(2⁸) 運算 ───
def xtime(a):
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF

def gf_mul(a, b):
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        a = xtime(a)
        b >>= 1
    return result

# ─── S-box（預計算表） ───
SBOX = [
    0x63,0x7C,0x77,0x7B,0xF2,0x6B,0x6F,0xC5,0x30,0x01,0x67,0x2B,0xFE,0xD7,0xAB,0x76,
    0xCA,0x82,0xC9,0x7D,0xFA,0x59,0x47,0xF0,0xAD,0xD4,0xA2,0xAF,0x9C,0xA4,0x72,0xC0,
    0xB7,0xFD,0x93,0x26,0x36,0x3F,0xF7,0xCC,0x34,0xA5,0xE5,0xF1,0x71,0xD8,0x31,0x15,
    0x04,0xC7,0x23,0xC3,0x18,0x96,0x05,0x9A,0x07,0x12,0x80,0xE2,0xEB,0x27,0xB2,0x75,
    0x09,0x83,0x2C,0x1A,0x1B,0x6E,0x5A,0xA0,0x52,0x3B,0xD6,0xB3,0x29,0xE3,0x2F,0x84,
    0x53,0xD1,0x00,0xED,0x20,0xFC,0xB1,0x5B,0x6A,0xCB,0xBE,0x39,0x4A,0x4C,0x58,0xCF,
    0xD0,0xEF,0xAA,0xFB,0x43,0x4D,0x33,0x85,0x45,0xF9,0x02,0x7F,0x50,0x3C,0x9F,0xA8,
    0x51,0xA3,0x40,0x8F,0x92,0x9D,0x38,0xF5,0xBC,0xB6,0xDA,0x21,0x10,0xFF,0xF3,0xD2,
    0xCD,0x0C,0x13,0xEC,0x5F,0x97,0x44,0x17,0xC4,0xA7,0x7E,0x3D,0x64,0x5D,0x19,0x73,
    0x60,0x81,0x4F,0xDC,0x22,0x2A,0x90,0x88,0x46,0xEE,0xB8,0x14,0xDE,0x5E,0x0B,0xDB,
    0xE0,0x32,0x3A,0x0A,0x49,0x06,0x24,0x5C,0xC2,0xD3,0xAC,0x62,0x91,0x95,0xE4,0x79,
    0xE7,0xC8,0x37,0x6D,0x8D,0xD5,0x4E,0xA9,0x6C,0x56,0xF4,0xEA,0x65,0x7A,0xAE,0x08,
    0xBA,0x78,0x25,0x2E,0x1C,0xA6,0xB4,0xC6,0xE8,0xDD,0x74,0x1F,0x4B,0xBD,0x8B,0x8A,
    0x70,0x3E,0xB5,0x66,0x48,0x03,0xF6,0x0E,0x61,0x35,0x57,0xB9,0x86,0xC1,0x1D,0x9E,
    0xE1,0xF8,0x98,0x11,0x69,0xD9,0x8E,0x94,0x9B,0x1E,0x87,0xE9,0xCE,0x55,0x28,0xDF,
    0x8C,0xA1,0x89,0x0D,0xBF,0xE6,0x42,0x68,0x41,0x99,0x2D,0x0F,0xB0,0x54,0xBB,0x16,
]

INV_SBOX = [0] * 256
for i in range(256):
    INV_SBOX[SBOX[i]] = i
```

### Key Expansion（密鑰擴展）

AES-128 需要 11 個 round key（每個 16 bytes），共 176 bytes。從 16 bytes 的原始 key 擴展出來。

```
原始 key:   W₀  W₁  W₂  W₃       (每個 Wᵢ = 4 bytes = 1 word)
Round 1:    W₄  W₅  W₆  W₇
Round 2:    W₈  W₉  W₁₀ W₁₁
...
Round 10:   W₄₀ W₄₁ W₄₂ W₄₃
```

規則：

```
如果 i 是 4 的倍數：
    W[i] = W[i-4] ⊕ SubWord(RotWord(W[i-1])) ⊕ Rcon[i/4]
否則：
    W[i] = W[i-4] ⊕ W[i-1]
```

其中：

- **RotWord**：4 bytes 循環左移 1 byte `[a,b,c,d] → [b,c,d,a]`
- **SubWord**：4 bytes 各自過 S-box
- **Rcon**：Round Constant，`Rcon[i] = [rc_i, 0, 0, 0]`，rc₁=1, rc₂=2, rc₃=4, ... （GF(2⁸) 中 `xtime` 的連續應用）

```python
RCON = [0x00,0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1B,0x36]

def key_expansion(key):
    """AES-128 key expansion: 16 bytes → 176 bytes (11 round keys)"""
    w = list(key)   # 複製成 mutable list
    for i in range(4, 44):     # 44 words = 176 bytes
        temp = w[(i-1)*4 : i*4]   # 前一個 word
        if i % 4 == 0:
            # RotWord
            temp = temp[1:] + temp[:1]
            # SubWord
            temp = [SBOX[b] for b in temp]
            # XOR Rcon
            temp[0] ^= RCON[i // 4]
        # W[i] = W[i-4] XOR temp
        for j in range(4):
            w.append(w[(i-4)*4 + j] ^ temp[j])
    return w
```

### AES State 的排列方式

AES 的 state 是 column-major 排列（不是 row-major！）：

```
Input bytes:   b₀ b₁ b₂ b₃ b₄ b₅ b₆ b₇ b₈ b₉ b₁₀ b₁₁ b₁₂ b₁₃ b₁₄ b₁₅

State matrix:  b₀  b₄  b₈  b₁₂
               b₁  b₅  b₉  b₁₃
               b₂  b₆  b₁₀ b₁₄
               b₃  b₇  b₁₁ b₁₅
               ↑column0  ↑column1  ↑column2  ↑column3
```

```python
def bytes_to_state(b):
    """16 bytes → 4x4 state (column-major)"""
    return [[b[r + 4*c] for c in range(4)] for r in range(4)]

def state_to_bytes(s):
    """4x4 state → 16 bytes"""
    return bytes([s[r][c] for c in range(4) for r in range(4)])
```

### 四個 Round 操作

```python
def sub_bytes(state):
    for r in range(4):
        for c in range(4):
            state[r][c] = SBOX[state[r][c]]

def shift_rows(state):
    # Row 0: no shift
    # Row 1: left shift 1
    state[1] = state[1][1:] + state[1][:1]
    # Row 2: left shift 2
    state[2] = state[2][2:] + state[2][:2]
    # Row 3: left shift 3
    state[3] = state[3][3:] + state[3][:3]

def mix_columns(state):
    for c in range(4):
        col = [state[r][c] for r in range(4)]
        state[0][c] = gf_mul(0x02,col[0]) ^ gf_mul(0x03,col[1]) ^ col[2]          ^ col[3]
        state[1][c] = col[0]          ^ gf_mul(0x02,col[1]) ^ gf_mul(0x03,col[2]) ^ col[3]
        state[2][c] = col[0]          ^ col[1]          ^ gf_mul(0x02,col[2]) ^ gf_mul(0x03,col[3])
        state[3][c] = gf_mul(0x03,col[0]) ^ col[1]          ^ col[2]          ^ gf_mul(0x02,col[3])

def add_round_key(state, round_key):
    """round_key: 16 bytes for this round"""
    for r in range(4):
        for c in range(4):
            state[r][c] ^= round_key[r + 4*c]
```

### 完整 Encrypt

```python
def aes128_encrypt_block(plaintext, key):
    """AES-128 加密一個 16-byte block
    
    Args:
        plaintext: bytes, 長度 16
        key: bytes, 長度 16
    Returns:
        bytes, 長度 16 (ciphertext)
    """
    assert len(plaintext) == 16 and len(key) == 16
    
    # Key expansion
    w = key_expansion(key)
    
    # Init state
    state = bytes_to_state(plaintext)
    
    # Round 0: AddRoundKey
    add_round_key(state, w[0:16])
    
    # Round 1~9: SubBytes → ShiftRows → MixColumns → AddRoundKey
    for rnd in range(1, 10):
        sub_bytes(state)
        shift_rows(state)
        mix_columns(state)
        add_round_key(state, w[rnd*16 : (rnd+1)*16])
    
    # Round 10: SubBytes → ShiftRows → AddRoundKey (no MixColumns)
    sub_bytes(state)
    shift_rows(state)
    add_round_key(state, w[160:176])
    
    return state_to_bytes(state)
```

### 驗證（FIPS-197 附錄 B）

```python
# FIPS-197 Appendix B 的測試向量
key       = bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c')
plaintext = bytes.fromhex('3243f6a8885a308d313198a2e0370734')
expected  = bytes.fromhex('3925841d02dc09fbdc118597196a0b32')

result = aes128_encrypt_block(plaintext, key)
assert result == expected, f"Got {result.hex()}, expected {expected.hex()}"
print(f"Plaintext:  {plaintext.hex()}")
print(f"Key:        {key.hex()}")
print(f"Ciphertext: {result.hex()}")
print("FIPS-197 Appendix B: PASS")
```

## 底層機制：Decrypt 的逆操作

### 逆操作

| Encrypt | Decrypt |
|---------|---------|
| SubBytes | InvSubBytes（用 INV_SBOX） |
| ShiftRows | InvShiftRows（右移） |
| MixColumns | InvMixColumns（用逆矩陣） |
| AddRoundKey | AddRoundKey（XOR 是自己的逆） |

```python
def inv_sub_bytes(state):
    for r in range(4):
        for c in range(4):
            state[r][c] = INV_SBOX[state[r][c]]

def inv_shift_rows(state):
    # Row 1: right shift 1
    state[1] = state[1][-1:] + state[1][:-1]
    # Row 2: right shift 2
    state[2] = state[2][-2:] + state[2][:-2]
    # Row 3: right shift 3
    state[3] = state[3][-3:] + state[3][:-3]

def inv_mix_columns(state):
    for c in range(4):
        col = [state[r][c] for r in range(4)]
        state[0][c] = gf_mul(0x0E,col[0])^gf_mul(0x0B,col[1])^gf_mul(0x0D,col[2])^gf_mul(0x09,col[3])
        state[1][c] = gf_mul(0x09,col[0])^gf_mul(0x0E,col[1])^gf_mul(0x0B,col[2])^gf_mul(0x0D,col[3])
        state[2][c] = gf_mul(0x0D,col[0])^gf_mul(0x09,col[1])^gf_mul(0x0E,col[2])^gf_mul(0x0B,col[3])
        state[3][c] = gf_mul(0x0B,col[0])^gf_mul(0x0D,col[1])^gf_mul(0x09,col[2])^gf_mul(0x0E,col[3])
```

### 完整 Decrypt

Decrypt 的 round 順序是 encrypt 的**逆序**：

```python
def aes128_decrypt_block(ciphertext, key):
    """AES-128 解密一個 16-byte block"""
    assert len(ciphertext) == 16 and len(key) == 16
    
    w = key_expansion(key)
    state = bytes_to_state(ciphertext)
    
    # Round 10 inverse: AddRoundKey → InvShiftRows → InvSubBytes
    add_round_key(state, w[160:176])
    inv_shift_rows(state)
    inv_sub_bytes(state)
    
    # Round 9~1 inverse: AddRoundKey → InvMixColumns → InvShiftRows → InvSubBytes
    for rnd in range(9, 0, -1):
        add_round_key(state, w[rnd*16 : (rnd+1)*16])
        inv_mix_columns(state)
        inv_shift_rows(state)
        inv_sub_bytes(state)
    
    # Round 0 inverse: AddRoundKey
    add_round_key(state, w[0:16])
    
    return state_to_bytes(state)
```

驗證 roundtrip：

```python
key       = bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c')
plaintext = bytes.fromhex('3243f6a8885a308d313198a2e0370734')
ciphertext = aes128_encrypt_block(plaintext, key)
assert aes128_decrypt_block(ciphertext, key) == plaintext
print("Encrypt → Decrypt roundtrip: PASS")
```

## 進一步用法：用 cryptography 套件驗證（範例二）

```python
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

key       = bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c')
plaintext = bytes.fromhex('3243f6a8885a308d313198a2e0370734')

# OpenSSL 的 AES-ECB（單 block 就是 ECB）
cipher = Cipher(algorithms.AES(key), modes.ECB())
enc = cipher.encryptor()
ct_openssl = enc.update(plaintext) + enc.finalize()

assert ct_openssl == aes128_encrypt_block(plaintext, key)
print("Handmade vs OpenSSL: PASS")
```

## AES-NI：硬體加速

### 什麼是 AES-NI

Intel 在 2010 年（Westmere 架構）加入的 CPU 指令集，直接在硬體上執行 AES round：

| 指令 | 功能 |
|------|------|
| `AESENC` | 執行一個 encrypt round（SubBytes + ShiftRows + MixColumns + AddRoundKey） |
| `AESENCLAST` | 執行最後一輪 encrypt（無 MixColumns） |
| `AESDEC` | 一個 decrypt round |
| `AESDECLAST` | 最後一輪 decrypt |
| `AESKEYGENASSIST` | Key expansion 輔助 |
| `AESIMC` | InvMixColumns（用於 decrypt key schedule） |

### 為什麼 AES-NI 快 10x+

1. **單指令完成整個 round**：軟體需要查 S-box 表 16 次 + ShiftRows + MixColumns + XOR，AES-NI 用一條指令搞定
2. **pipeline 友好**：AES-NI 指令延遲約 4 cycles，但 throughput 可以到 1 cycle（pipeline 滿載時）
3. **消除 timing side-channel**：軟體查表的 cache miss pattern 會洩漏 key（cache-timing attack），硬體指令不查表，耗時固定

```
軟體 AES：    ~20 cycles/byte（查表實作）
AES-NI：      ~1-2 cycles/byte（pipeline 滿載時）
加速：         10x-20x
```

### Python 用到 AES-NI 嗎？

`cryptography` 套件底層是 OpenSSL，OpenSSL 會自動偵測 AES-NI 並使用。用 `grep -o aes /proc/cpuinfo` 確認 CPU 支援。實測手刻 Python AES 對 10,000 blocks 大約幾十秒，OpenSSL 版幾毫秒 — 差距幾百倍到上千倍。

## 對比與取捨：AES-128 vs AES-192 vs AES-256

| | AES-128 | AES-192 | AES-256 |
|---|---|---|---|
| Key size | 128 bits | 192 bits | 256 bits |
| Rounds | 10 | 12 | 14 |
| Block size | 128 bits | 128 bits | 128 bits |
| Key schedule words | 44 | 52 | 60 |
| 安全等級 | 128 bits | 192 bits | 256 bits |
| 速度（AES-NI） | 最快 | 中 | 最慢（多 4 輪） |
| Related-key attack | 無已知 | 無已知 | Biclique 2¹²⁶·¹ |
| 推薦用途 | 一般用途 | 高安全需求 | 政府/國防/PQC 過渡 |

### AES-256 的 Related-Key Attack

Biryukov & Khovratovich（2009）發現 AES-256 在 related-key 模型下有弱點。

**related-key attack 假設攻擊者能讓受害者用兩個有已知關係的 key 加密**。這在實際中幾乎不會發生（你不會讓攻擊者選你的 key），但它說明了：

- AES-256 的 key schedule 比 AES-128 的弱
- 「key 越長越安全」不是絕對的真理

**實務影響**：幾乎為零。在 single-key 模型下，AES-256 仍是 2¹²⁸+ 安全。但這提醒我們 key schedule 設計的重要性。

## Key Expansion 的設計哲學

```
W[0] W[1] W[2] W[3]    ← 原始 key（4 words）
 │         ↓
 │    ┌──────────┐
 │    │ RotWord  │  ← [a,b,c,d] → [b,c,d,a]
 │    │ SubWord  │  ← 每 byte 過 S-box
 │    │ ⊕ Rcon   │  ← round constant
 │    └────┬─────┘
 │         │
 ⊕←────────┘
 │
W[4]─→⊕──→W[5]─→⊕──→W[6]─→⊕──→W[7]
      ↑         ↑         ↑
     W[1]      W[2]      W[3]
```

**設計目標**：

1. **雪崩效應**：key 改 1 bit，所有 round key 都大幅改變
2. **抵抗 related-key attack**：RotWord + SubWord 的非線性防止攻擊者控制 round key 之間的關係
3. **防止弱 key**：Rcon 的存在確保即使原始 key 全零，round key 也不會全零

你可以用兩個只差 1 bit 的 key 展開 round key，比較每輪的 Hamming distance。Round 0 只差 1 bit，Round 3 以後大約 64/128 bits（接近理想的 50%），證明雪崩效應。

## 踩雷集錦

### 雷 1：State 是 column-major

```python
# 錯：用 row-major
state = [[plaintext[r*4+c] for c in range(4)] for r in range(4)]

# 對：用 column-major
state = [[plaintext[r+4*c] for c in range(4)] for r in range(4)]
```

弄錯排列方式，所有結果都會錯，但程式不會報錯 — 最陰險的 bug。

### 雷 2：最後一輪有 MixColumns

迴圈 `range(1, 11)` 包含 round 10 — 但 round 10 **不做 MixColumns**。正確寫法：`range(1, 10)` 跑 round 1-9，round 10 單獨寫（SubBytes → ShiftRows → AddRoundKey）。

### 雷 3：Key expansion 的 word 邊界

`key_expansion` 是以 4-byte word 為單位操作的。如果你的實作把 byte 和 word 搞混，展開的 key 會全部錯。

```python
# 容易出錯的地方：
# W[i] 是一個 4-byte word，不是 1 byte
# w[i*4 : (i+1)*4] 才是第 i 個 word 的 4 bytes
```

### 雷 4：Rcon 表搞錯

Rcon 的 rc 值在 GF(2⁸) 中是 2 的冪次（xtime 連續應用）：

```
rc₁ = 0x01
rc₂ = 0x02
rc₃ = 0x04
...
rc₈ = 0x80
rc₉ = 0x1B  ← xtime(0x80) = 0x1B（不是 0x100！）
rc₁₀ = 0x36
```

`rc₉ = 0x1B` 不是 `0x00` — 忘記 xtime 的溢出處理是常見 bug。

### 雷 5：「手刻的 AES 可以用在生產」

**絕對不行。** 手刻的 Python AES：

- 慢幾百倍
- 有 timing side-channel（Python 的 list indexing 不是 constant-time）
- 沒有抵抗 cache attack 的保護
- 沒有經過形式化驗證

生產用 OpenSSL / libsodium / 硬體 AES-NI。

## 進階：Equivalent Inverse Cipher

FIPS-197 §5.3.5 描述了「等價逆密碼」（Equivalent Inverse Cipher）— 把 decrypt 的 round 結構變得和 encrypt 一樣，只需要先對 round key 做 InvMixColumns。核心觀察：InvSubBytes 和 InvShiftRows 可以交換順序（ShiftRows 是逐 byte 位移，不影響 S-box 的逐 byte 操作）。這在硬體實作中很有用 — encrypt 和 decrypt 共用電路。

## 動手練習

1. **逐 round 追蹤**：用 FIPS-197 附錄 B 的測試向量，印出 AES-128 加密每一輪的 state（SubBytes 後、ShiftRows 後、MixColumns 後、AddRoundKey 後），和官方文件逐一比對。

2. **AES-192 擴展**：修改 `key_expansion` 和 `aes128_encrypt_block` 支援 AES-192（key = 24 bytes, 12 rounds）。

3. **性能測量**：測量手刻 AES encrypt 10,000 blocks 和 `cryptography` 的速度差異。

4. **弱 key 實驗**：用全零 key 展開 round key，觀察 round key 是否全零（如果是，代表 key schedule 有弱點；AES 的 key schedule 會防止這件事）。

## 重點整理

- AES-128 = 10 輪，每輪 SubBytes → ShiftRows → MixColumns → AddRoundKey，最後一輪無 MixColumns
- Key expansion 從 16 bytes 生成 176 bytes round key，用 RotWord + SubWord + Rcon 保證雪崩效應
- State 是 column-major 排列 — 這是最常見的實作 bug 來源
- AES-NI 用單條指令完成整個 round，比軟體快 10-20 倍，同時消除 timing side-channel
- AES-256 有 related-key attack（Biryukov 2009），但實務影響幾乎為零
- 手刻的 AES 只能用於教學，生產環境用 OpenSSL / AES-NI

## 自我檢核

- [ ] 能畫出 AES-128 的 10-round 結構圖
- [ ] 能解釋 key expansion 的每一步（RotWord、SubWord、Rcon）
- [ ] 能用手刻的 AES 加密一個 block 並和 OpenSSL 結果一致
- [ ] 能說出 AES-NI 為什麼比軟體快（單指令 round、消除 cache timing）
- [ ] 能解釋 AES-256 related-key attack 的前提和為什麼實務上不重要

## 延伸閱讀

- **FIPS-197**：完整 AES 標準，附錄 B 有詳細的逐 round 測試向量
- **Daemen & Rijmen "The Design of Rijndael" (2002)**：設計者的書，解釋每個選擇的理由
- **Biryukov & Khovratovich "Related-Key Cryptanalysis of the Full AES-192 and AES-256" (2009)**
- **Intel "Intel AES-NI White Paper" (2010)**：AES-NI 指令的架構和性能

---

> **下一章**：[Ch 11 — Block Cipher Modes 與 Padding Oracle Attack](11-block-modes-padding-oracle.md) — 你現在有了 AES 的加密能力，但一個 block 只有 16 bytes。怎麼加密更長的資料？以及為什麼錯誤的 mode 選擇會讓一切白費。
