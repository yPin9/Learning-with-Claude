# Ch 16 — MAC：HMAC、GMAC、Poly1305

> 目標：搞懂三種主流 MAC 構造：HMAC（用 hash function、抗 length extension）、GMAC（GF(2¹²⁸) 上 polynomial、AES-GCM 的一半）、Poly1305（Wegman-Carter 構造、ChaCha20-Poly1305 的另一半）。

## MAC 是什麼

**Message Authentication Code**：產生一個 tag 讓收件方驗證「這訊息是有 key 的人寫的，而且沒被改」。

```
sender:   tag = MAC(K, M)
          send (M, tag)
receiver: verify M and tag using K
```

對比：

- **Hash**：完整性，但任何人能算（沒 key）
- **MAC**：完整性 + 來源驗證（要 key）
- **數位簽章**：完整性 + 來源驗證 + 不可否認（公鑰系統）

MAC 是對稱的（同一個 K 能 sign 也能 verify）— 因此沒「**不可否認**」（兩方都能算 tag）。

## MAC 安全要求

```
EUF-CMA（Existential Unforgeability under Chosen Message Attack）：
attacker 能任意查 oracle MAC(K, M_i)
但她無法產生**新訊息** M* 與正確 tag*
```

正確 tag 不能可枚舉（256-bit tag 才安全；64-bit 就太短被 brute-force）。

## HMAC：通用 hash-based MAC

Bellare、Canetti、Krawczyk 1996 提，1997 IETF RFC 2104 標準。**最廣用 MAC**。

```
HMAC(K, M) = H((K' XOR opad) || H((K' XOR ipad) || M))

其中：
  K' = K padded 到 hash 的 block size
       (K 短就 0-pad；K 長就先 H(K))
  ipad = 0x36 重複（block size byte）
  opad = 0x5C 重複（block size byte）
```

**雙重 hash 結構**：

1. 內層：`H(K' XOR ipad || M)` 把 K_inner 與 M 雜湊
2. 外層：`H(K' XOR opad || 內層結果)` 再 hash 一次

**為什麼能擋 length extension**：attacker 拿到 HMAC 結果 = 外層 hash，要 extend 必須知道 `K' XOR opad || 內層結果`，但 K_outer 不知道 → 失敗。

## HMAC 實作

```python
import hashlib

def hmac_sha256(key: bytes, message: bytes) -> bytes:
    BLOCK_SIZE = 64   # SHA-256 block
    if len(key) > BLOCK_SIZE:
        key = hashlib.sha256(key).digest()
    if len(key) < BLOCK_SIZE:
        key = key + b'\x00' * (BLOCK_SIZE - len(key))
    
    ipad = bytes(b ^ 0x36 for b in key)
    opad = bytes(b ^ 0x5C for b in key)
    
    inner = hashlib.sha256(ipad + message).digest()
    outer = hashlib.sha256(opad + inner).digest()
    return outer
```

驗證 RFC 4231 test vector：

```python
key = bytes.fromhex("0b" * 20)
msg = b"Hi There"
expected = bytes.fromhex(
    "b0344c61d8db38535ca8afceaf0bf12b"
    "881dc200c9833da726e9376c2e32cff7")
assert hmac_sha256(key, msg) == expected
```

Python 內建 `hmac` 模組：

```python
import hmac
mac = hmac.new(key, message, hashlib.sha256).digest()
```

## HMAC 的安全性

```
若 H 的 compression function 是 PRF
  → HMAC 是 PRF
  → HMAC 是 secure MAC
```

這個證明 1996 由 Bellare 發表，被廣泛審查。**HMAC 是迄今最受信任的 MAC 構造**。

實務 truncation：很多協定用 HMAC 但只取前 128-bit（節省空間）— `HMAC-SHA256-128`。仍安全（128-bit MAC tag 比 128-bit attack 大 2¹²⁸ 倍）。

## verify 必 const-time

```c
// 危險：用 memcmp
if (memcmp(received_tag, computed_tag, 32) == 0) accept();
// memcmp 在第一個不同 byte 就 return，timing 洩漏哪個位置不同
// → attacker 可一個 byte 一個 byte 試出正確 tag
```

正確：

```c
unsigned char diff = 0;
for (int i = 0; i < 32; i++)
    diff |= received_tag[i] ^ computed_tag[i];
if (diff == 0) accept();
```

每個 byte 都比較，最後看 OR 結果。**不論差在哪裡時間相同**。

OpenSSL 提供 `CRYPTO_memcmp()`、Python `hmac.compare_digest()`。**寫 MAC verify 永遠用這些**。

## Poly1305：Wegman-Carter 構造

Bernstein 2005 設計。**不是 hash-based**，是 **GF(2¹³⁰ - 5) 上的 polynomial evaluation**：

```
給定 16-byte key (r, s)：
  把訊息切成 16-byte chunks: c_1, c_2, ..., c_q
  每個 chunk 加上一個 1 byte（防擴展）
  
  tag = ((c_1 × r^q + c_2 × r^(q-1) + ... + c_q × r) mod (2^130 - 5)) + s mod 2^128
```

**polynomial 在 prime field 上**，乘法快、抗碰撞由質數結構保證。

**Wegman-Carter MAC** 是 1981 年 Wegman & Carter 證明的構造：**universal hash + 一次性 pad** = perfect MAC。Poly1305 把 universal hash 用 polynomial evaluation 實作。

## Poly1305 的「one-time」性質

**(r, s) 對只能用一次**！理由：universal hash 對 chosen-message 不安全，需要 one-time pad 遮蓋。

實務怎麼用：**配合 PRG 為每條訊息產生新 (r, s)**：

```python
def poly1305_keygen(master_key, nonce):
    """產生一次性 (r, s)"""
    return chacha20_block(master_key, counter=0, nonce=nonce)[:32]

def chacha20_poly1305_encrypt(key, nonce, plaintext, aad):
    poly_key = poly1305_keygen(key, nonce)
    ciphertext = chacha20_encrypt(key, nonce, plaintext, initial_counter=1)
    tag = poly1305_mac(poly_key, aad || ciphertext || lengths)
    return ciphertext, tag
```

整個 ChaCha20-Poly1305（Ch 27）就是這樣構造。

## Poly1305 簡化實作

```python
def clamp(r):
    """把 r 的某些 bit 清零（規範要求）"""
    r = bytearray(r)
    r[3] &= 15;  r[7] &= 15;  r[11] &= 15;  r[15] &= 15
    r[4] &= 252; r[8] &= 252; r[12] &= 252
    return bytes(r)

def poly1305_mac(key, message):
    r = int.from_bytes(clamp(key[:16]), 'little')
    s = int.from_bytes(key[16:32], 'little')
    p = (1 << 130) - 5
    accumulator = 0
    for i in range(0, len(message), 16):
        chunk = message[i:i+16]
        # 加上一個 1 byte（防 extension）
        chunk_int = int.from_bytes(chunk + b'\x01', 'little')
        accumulator = (accumulator + chunk_int) * r % p
    return ((accumulator + s) & ((1 << 128) - 1)).to_bytes(16, 'little')
```

簡化版（沒處理 padding 等細節）。完整見 RFC 8439。

## GMAC：GF(2¹²⁸) 上的 polynomial

**AES-GCM 的 MAC 部分**。類似 Poly1305 但用 GF(2¹²⁸)：

```
tag = (M_1 × H^q + M_2 × H^(q-1) + ... + M_q × H + len_block × H + IV_encrypted)
其中 H = AES_enc(K, 0^128)
```

`GHASH` 是核心。Ch 26 AES-GCM 詳述。

GF(2¹²⁸) 用 polynomial: x¹²⁸ + x⁷ + x² + x + 1。乘法可硬體加速（PCLMULQDQ 指令）。

## CMAC / OMAC：block cipher 直接做 MAC

不用 hash，直接用 AES 構造 MAC：

```
CMAC(K, M):
  L = AES_enc(K, 0^128)
  K1, K2 = derive from L  (subkey)
  
  M 切成 16-byte block：M_1, M_2, ..., M_n
  if M 完整 block：
    M_n = M_n XOR K1
  else:
    M_n = pad(M_n) XOR K2
  
  state = 0
  for i in 1..n:
    state = AES_enc(K, state XOR M_i)
  return state
```

CMAC 是 NIST 標準（SP 800-38B），內建 IPSec、Bluetooth。**比 HMAC 慢但不需要額外 hash function**（嵌入式有 AES 硬體就免費）。

## KMAC：基於 SHA-3 的 MAC

NIST SP 800-185。SHA-3 sponge 構造**內建 keying**，不需 HMAC trick：

```
KMAC256(K, X, L) = SHAKE256(prefix || encode(K) || X || encode(L), L)
```

很乾淨。但因為 SHA-3 軟體性能不及 SHA-256，KMAC 沒大規模採用。**post-quantum 與 PQC 系統大量用**（ML-KEM、ML-DSA 內部用 SHAKE）。

## 對照與選擇

| MAC | 速度（軟體） | 速度（硬體） | 場景 |
|---|---|---|---|
| **HMAC-SHA256** | 中 | 中（SHA-NI） | 通用、預設選擇 |
| **Poly1305** | 快 | 快 | ChaCha20-Poly1305、TLS |
| **GMAC** | 中（含 PCLMUL） | 快（PCLMUL） | AES-GCM 內部 |
| **CMAC** | 慢（多次 AES） | 快（AES-NI） | 嵌入式、IPSec |
| **KMAC** | 中 | 快 | PQC、SHA-3 系統 |

實務選擇：

- **TLS / 一般 API**：用 AES-GCM 或 ChaCha20-Poly1305（含 MAC，不單獨選）
- **檔案完整性 / cookie 簽章**：HMAC-SHA256
- **SSH HMAC**：HMAC-SHA256 / HMAC-SHA512
- **PQC**：KMAC

## 一個常見誤解

「MAC tag 64-bit 就夠，省頻寬」

**不夠**。tag 大小 = 攻擊者偽造機率：

```
64-bit tag → 偽造機率 2⁻⁶⁴
128-bit tag → 2⁻¹²⁸
```

64-bit 在現代算力下可能被 brute-force 線上偽造（攻擊者試 2⁶⁴ 個 tag 直到通過）。**最低 128-bit**，多數選 256-bit 留 margin。

## 自我檢核

- [ ] 我能寫 HMAC-SHA256 並通過 RFC 4231 test vector
- [ ] 我能解釋為什麼 HMAC 抗 length extension
- [ ] 我能寫 const-time MAC verify
- [ ] 我能解釋 Poly1305 的 Wegman-Carter 構造
- [ ] 我能說出 (r, s) 為什麼必須 one-time
- [ ] 我能列出五種 MAC 構造的選擇場景

下一章看密碼雜湊（password hashing）— 跟普通 MAC 完全不同的設計目標，PBKDF2、bcrypt、scrypt、Argon2。

→ [Ch 17 密碼雜湊與 KDF](./17-password-hashing-kdf.md)
