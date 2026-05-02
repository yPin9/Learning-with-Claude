# Ch 12 — Stream cipher：RC4 興衰、ChaCha20 為什麼贏

> 目標：搞懂 stream cipher 的設計取捨（為什麼比 block 快、為什麼 nonce reuse 是死罪）、RC4 從 SSL 主流到 2015 全面退役的過程、ChaCha20 的 ARX 結構與抗 timing 優勢。

## Stream cipher 核心想法

```
key + nonce ──► PRG ──► keystream
                        │
plaintext ─────────────XOR──► ciphertext
```

PRG 從 key + nonce 生成 keystream，與 plaintext XOR 得 ciphertext。**概念上是 OTP 的計算近似**（Ch 6）。

特性：

- **byte-by-byte 加密**（不用 block size 對齊）
- **加密 = 解密**（XOR 自反）
- **沒 padding**
- **平行性**：取決於設計（ChaCha20 可，RC4 不可）

但有個**絕對紀律**：**(key, nonce) pair 不能重用**。重用 → keystream 重複 → two-time pad。

## RC4：1987-2015

Ron Rivest 1987 為 RSA Security 設計，**長期保密**到 1994 才被洩漏到 Cypherpunks 郵件 list。後來被廣泛用於 SSL、WEP、WPA-TKIP。

```
RC4 內部 state：
  S[0..255]：256-byte 排列
  i, j：兩個 8-bit 索引

Key Scheduling Algorithm (KSA):
  for i in 0..255: S[i] = i
  j = 0
  for i in 0..255:
    j = (j + S[i] + key[i % keylen]) mod 256
    swap(S[i], S[j])

Pseudo-Random Generation Algorithm (PRGA):
  i = j = 0
  loop:
    i = (i + 1) mod 256
    j = (j + S[i]) mod 256
    swap(S[i], S[j])
    K = S[(S[i] + S[j]) mod 256]
    output K (一個 byte 的 keystream)
```

**簡單到能背**。整個 RC4 < 30 行 C，這是它流行的原因之一。

## RC4 的問題演化

### 1. Fluhrer-Mantin-Shamir (FMS) 2001

WEP 用 RC4，但**把 IV concat 在 key 前面**（IV || key）。FMS 發現：

> 給定許多 (IV, ciphertext) pair，能透過特定 IV pattern 推出 key 部分 byte。

WEP 因此被秒破（2001 後幾分鐘破譯任意 WEP 網路）。**WPA 才換成 TKIP 替代**（仍用 RC4 但程序改）。

### 2. RC4 Biases

學術研究發現 RC4 的 **keystream 不夠隨機**：

- output 第 2 byte 偏向特定值（Mantin-Shamir 2001）
- 多種 byte position 有 statistical bias
- 大量已知 plaintext 後可恢復 key

每年都有新 bias 被發現。RC4 變成「**沒人想用，但因兼容性還在**」。

### 3. RC4-NOMORE Attack (2015)

最後一根稻草。AlFardan / Bernstein / Paterson 等：

> 對 TLS 中的 RC4，有 plaintext 在每個 session 重複出現（cookie），用 multi-session 統計能 75 小時恢復 cookie。

2015 IETF 正式 deprecate RC4 in TLS（RFC 7465）。Microsoft、Mozilla、Google 同年禁用。

**RC4 算是「實戰被 retire」的密碼**。今天不該在任何新系統用。

## RC4 教學版實作

```python
def rc4_ksa(key):
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i], S[j] = S[j], S[i]
    return S

def rc4_prga(S, n):
    """產生 n byte keystream"""
    out = []
    i = j = 0
    S = S.copy()
    for _ in range(n):
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        K = S[(S[i] + S[j]) % 256]
        out.append(K)
    return bytes(out)

def rc4(key, data):
    S = rc4_ksa(key)
    keystream = rc4_prga(S, len(data))
    return bytes(a ^ b for a, b in zip(data, keystream))

ct = rc4(b"Key", b"Plaintext")
print(ct.hex())  # bbf316e8d940af0ad3
```

寫起來真的短 — 這是 RC4 流行的核心理由。但簡潔不等於安全。

## ChaCha20：Bernstein 2008

Daniel Bernstein 2008 設計（前身 Salsa20，2005）。RFC 8439（2018）標準化。**現代 stream cipher 首選**。

### ARX 結構

ChaCha20 的核心是 **ARX**：

```
A = Add (32-bit modular)
R = Rotate (cyclic shift)
X = XOR
```

只有這三種運算 — **沒 S-box、沒 lookup table**。**天生 const-time**（沒 cache miss）。

### 內部 state

512-bit state 排成 4×4 個 32-bit word：

```
constant constant constant constant
key       key       key       key
key       key       key       key
counter  nonce     nonce     nonce
```

具體：

```
"expa"  "nd 3"  "2-by"  "te k"   ← 16-byte 常數
key[0]  key[1]  key[2]  key[3]
key[4]  key[5]  key[6]  key[7]
ctr     nonce0  nonce1  nonce2
```

key 256-bit、nonce 96-bit、counter 32-bit。

### Quarter Round

ChaCha20 的核心 round function：

```
def quarter_round(a, b, c, d):
    a = (a + b) & 0xFFFFFFFF; d ^= a; d = rotl32(d, 16)
    c = (c + d) & 0xFFFFFFFF; b ^= c; b = rotl32(b, 12)
    a = (a + b) & 0xFFFFFFFF; d ^= a; d = rotl32(d, 8)
    c = (c + d) & 0xFFFFFFFF; b ^= c; b = rotl32(b, 7)
    return a, b, c, d
```

每輪對 4 個 word 操作。整個 ChaCha20 跑 20 輪（10 個「double-round」=10 column rounds + 10 diagonal rounds）。

### 完整 ChaCha20

```python
def rotl32(x, n):
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF

def quarter_round(state, a, b, c, d):
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = rotl32(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = rotl32(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = rotl32(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = rotl32(state[b] ^ state[c], 7)

def chacha20_block(key, counter, nonce):
    constants = [0x61707865, 0x3320646e, 0x79622d32, 0x6b206574]
    state = constants[:] + list(struct.unpack('<8I', key)) + \
            [counter] + list(struct.unpack('<3I', nonce))
    working = state.copy()
    for _ in range(10):
        # column rounds
        quarter_round(working, 0, 4, 8, 12)
        quarter_round(working, 1, 5, 9, 13)
        quarter_round(working, 2, 6, 10, 14)
        quarter_round(working, 3, 7, 11, 15)
        # diagonal rounds
        quarter_round(working, 0, 5, 10, 15)
        quarter_round(working, 1, 6, 11, 12)
        quarter_round(working, 2, 7, 8, 13)
        quarter_round(working, 3, 4, 9, 14)
    final = [(s + w) & 0xFFFFFFFF for s, w in zip(state, working)]
    return struct.pack('<16I', *final)

def chacha20_encrypt(key, nonce, plaintext, initial_counter=1):
    out = bytearray()
    counter = initial_counter
    for i in range(0, len(plaintext), 64):
        ks = chacha20_block(key, counter, nonce)
        block = plaintext[i:i+64]
        out.extend(a ^ b for a, b in zip(block, ks))
        counter += 1
    return bytes(out)
```

ChaCha20 一個 block = 64 byte（不是 16 byte 像 AES）。

## ChaCha20 vs AES

| | AES-128 (CTR) | ChaCha20 |
|---|---|---|
| 軟體性能（無 AES-NI） | ~10 cycle/byte | ~3 cycle/byte |
| 軟體性能（AES-NI） | ~1 cycle/byte | ~2 cycle/byte |
| 行動裝置 / IoT | 需 AES-NI 才快 | 純軟體就快 |
| Const-time | AES-NI 是；軟體 T-table 可能洩漏 | 永遠是 |
| 標準化 | NIST FIPS 197 | IETF RFC 8439 |
| key size | 128 / 192 / 256 | 256 |
| Block 概念 | 128-bit block | 64-byte block, stream |

**ChaCha20 在沒 AES-NI 的場景大勝**：行動處理器、嵌入式、舊 CPU。Google 2014 起在 mobile Chrome 預設用 ChaCha20-Poly1305 over AES-GCM。

**有 AES-NI 的伺服器**兩者性能接近，多數系統提供 cipher suite 並列，let client 選。

## Salsa20、XChaCha20 變種

- **Salsa20**：ChaCha20 的前身（2005），quarter round 略不同。仍安全但 ChaCha20 是改良版。
- **XChaCha20**：把 nonce 從 96-bit 擴到 192-bit（用 HChaCha20 derive sub-key）。允許 random nonce 而不擔心碰撞。
- **XSalsa20**：類似的 192-bit nonce Salsa20。

`libsodium` 預設用 **XChaCha20-Poly1305**，因為 192-bit nonce 用 random 也安全（96-bit nonce 重複機率是 2⁻⁴⁸ 在 2³² 訊息後，**不夠安全 for random nonce**，要 counter）。

## Stream cipher 的死罪：nonce reuse

```python
# 假設不小心用同個 nonce
ks = chacha20_block(key, counter=0, nonce=nonce)
ct1 = xor(message1, ks)
ct2 = xor(message2, ks)

# Attacker 看 ct1 與 ct2
# ct1 XOR ct2 = message1 XOR message2  ← key 消掉了
# 用 Ch 6 介紹的滑動猜詞攻擊可能還原 message1, message2
```

對 ChaCha20 / AES-CTR / RC4 都成立。**stream cipher 系統設計時，nonce 管理是核心安全責任**。常見做法：

1. **Counter-based**：nonce = 訊息序號（保證不重複）
2. **Random nonce**：用 192-bit XChaCha20，random 也夠安全
3. **Nonce-misuse-resistant 設計**：AES-GCM-SIV（Ch 27），就算 nonce 重複也不會崩

## 一個常見誤解

「stream cipher 比 block cipher 不安全」

**沒這回事**。ChaCha20 的安全度與 AES-256 並列。**RC4 不安全是 RC4 的設計問題**，不是 stream cipher 概念問題。

事實上 stream cipher **構造更簡單、更易分析**。block cipher 走 mode（CTR）才能加密 stream — 等於走了一圈回來。**現代趨勢是 stream cipher 與 block cipher 邊界模糊**（AES-GCM 就是 AES-CTR + GMAC，本質是 stream）。

## 自我檢核

- [ ] 我能寫 RC4 KSA + PRGA
- [ ] 我能說出至少兩個 RC4 攻擊（FMS、RC4-NOMORE）
- [ ] 我能寫 ChaCha20 quarter round
- [ ] 我能說出 ChaCha20 vs AES 在不同硬體的性能差
- [ ] 我能解釋 XChaCha20 為什麼用 192-bit nonce
- [ ] 我能說出 stream cipher nonce reuse 的後果

到這裡 Part 3 章節結束。下一個是練習 A — 手刻 AES-128（C + Python），跑 ECB penguin 圖、實作 padding oracle attack。

→ [練習 A：AES + padding oracle](./practice-a-aes-and-padding-oracle.md)
