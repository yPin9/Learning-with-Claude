# Ch 26 — AES-GCM 解剖：GHASH、nonce 結構、misuse 災難

> 目標：把 AES-GCM 拆透：CTR mode + GHASH（GF(2¹²⁸) 上 polynomial MAC）、96-bit nonce 為什麼不能重複、forbidden attack（nonce reuse 直接洩漏 GHASH key）的真實案例。

## AES-GCM 結構

```
     ┌─ AES-CTR ──► encrypt 階段（產 ciphertext）
     │
plaintext + key
     │
     └─ GHASH ────► tag 階段（產 16-byte authentication tag）
```

`GCM = Galois Counter Mode`（galois 是 GF(2¹²⁸)）。

NIST SP 800-38D（2007）標準。**最廣用 AEAD**。

## 流程

```
input: key K (128/192/256), nonce N (96-bit 推薦), plaintext P, aad A

1. 算 H = AES_enc(K, 0^128)   ← GHASH 用的 hash key
2. 算 J_0 (initial counter):
   if |N| = 96: J_0 = N || 0^31 || 1
   else: J_0 = GHASH(H, A=∅, 0^? || N || 0^? || len(N))   (96-bit 之外的處理較複雜)
3. ciphertext = CTR_encrypt(K, inc(J_0), P)
4. tag = AES_enc(K, J_0) XOR GHASH(H, A, ciphertext)

return (ciphertext, tag)
```

幾個關鍵：

- **H = AES_enc(K, 0^128)** 是 GHASH 的 key
- **96-bit nonce + 32-bit counter**（從 1 開始遞增，0 留給 tag）
- **tag = AES(J_0) XOR GHASH(...)** — 不是直接 GHASH
- **CTR encrypt 從 inc(J_0) 開始**，counter = 1, 2, 3, ...

## GHASH：GF(2¹²⁸) 上 polynomial

```
GHASH(H, A, C):
  X_0 = 0
  for each 16-byte block of A:
    X = (X XOR A_i) × H   (in GF(2^128))
  for each 16-byte block of C:
    X = (X XOR C_i) × H
  X = (X XOR len_block) × H
  return X
```

GF(2¹²⁸) 用 reduction polynomial: `x^128 + x^7 + x^2 + x + 1`。乘法可硬體加速（Intel `PCLMULQDQ`、ARM `PMULL`）。

`len_block` 是 64-bit |A| || 64-bit |C|（bit count）。

## 為什麼這樣構造

GHASH 是 **Wegman-Carter MAC** 變體（Ch 16 介紹過）：

- 用 `H` 當 universal hash key（PRF assumption from AES）
- 用 `AES_enc(J_0)` 當 one-time pad 遮 GHASH 結果

**安全 iff (K, N) 對只用一次**。如果 nonce 重複：

- 同 (K, N) 兩個訊息共用 H 與 J_0
- attacker 拿兩個 (C, T) → `T_1 XOR T_2 = GHASH(C_1) XOR GHASH(C_2)`
- 可用代數方法 **解出 H** → 後續可偽造任意 GHASH

這叫 **forbidden attack**（Joux 2002, Handschuh-Preneel 2008）。

## Forbidden Attack 步驟

```
給定兩組同 nonce 的 (C_1, T_1) 與 (C_2, T_2)：

T_1 = AES(J_0) XOR GHASH(H, A_1, C_1)
T_2 = AES(J_0) XOR GHASH(H, A_2, C_2)

T_1 XOR T_2 = GHASH(H, A_1, C_1) XOR GHASH(H, A_2, C_2)
            = polynomial in H

解 polynomial root → 找到 H
有了 H 攻擊者可：
  1. 對任意 (A', C') 算 GHASH(H, A', C')
  2. 用 (T_1 XOR GHASH(H, A_1, C_1)) 算 AES(J_0) = E (恢復 J_0 對應 keystream)
  3. 構造任意訊息 + 偽造 valid tag
```

Böck / Zauner / Devlin / Somorovsky 2016 paper "Nonce-Disrespecting Adversaries" 揭露：**184 個 HTTPS server 因 GCM nonce 重複被攻**（軟體 bug 把 random nonce 重複用）。

## Nonce 結構建議

NIST 與 RFC 對 GCM nonce 給三種模式：

### 1. Random 96-bit (deterministic risk)

```python
nonce = os.urandom(12)  # 96 bit
```

**問題**：96-bit nonce + birthday → 2⁴⁸ 訊息後 ~50% 機率重複。對 high-volume server **不夠**（Cloudflare 一秒處理億級訊息）。

NIST SP 800-38D：random 96-bit 適合 ≤ 2³² 訊息（安全 margin）。

### 2. Counter-based

```python
counter = 0
def encrypt(plaintext):
    global counter
    counter += 1
    nonce = counter.to_bytes(12, 'big')
    return aes_gcm_encrypt(key, nonce, plaintext)
```

**保證 unique** 但要 stateful：multi-process 或 reboot 後要小心同步。

### 3. Hybrid: prefix + counter

```python
prefix = os.urandom(8)  # 64-bit instance ID
counter = 0
nonce = prefix + counter.to_bytes(4, 'big')
```

兩個 server 各自 random prefix 不同 → 不會撞，每 server 內部 counter 保 unique。

### 真實 case：TLS 1.3 用法

```
TLS 1.3 GCM nonce:
  64-bit "implicit": derived from handshake (一次 connection 固定)
  64-bit "explicit": sequence number per record

每 record nonce 獨立、counter 內建在 sequence number。
不會重複，且 stateless（從 sequence 算）。
```

## 96-bit 之外的 nonce

如果 nonce 不是 96-bit，GCM 規範用 GHASH 算 J_0（複雜很多）。**避免用非 96-bit nonce**：

- 多一次 GHASH 計算
- 規範細節容易實作錯

**永遠用 96-bit nonce**。要更大 nonce 用 XChaCha20-Poly1305 或 AES-GCM-SIV。

## 程式範例

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os

key = AESGCM.generate_key(bit_length=128)
aesgcm = AESGCM(key)

nonce = os.urandom(12)
ad = b"X-Request-ID: 12345"
plaintext = b"Sensitive data"

ciphertext = aesgcm.encrypt(nonce, plaintext, ad)
# ciphertext = ct_actual + tag (16-byte)

# Decrypt
recovered = aesgcm.decrypt(nonce, ciphertext, ad)
assert recovered == plaintext

# Tampering 測試
tampered = bytearray(ciphertext)
tampered[0] ^= 1
try:
    aesgcm.decrypt(nonce, bytes(tampered), ad)
except Exception as e:
    print("AUTH FAILED")
```

## C 級實作（OpenSSL）

```c
#include <openssl/evp.h>

int aes_gcm_encrypt(const unsigned char *key, const unsigned char *nonce,
                    const unsigned char *plaintext, int plaintext_len,
                    const unsigned char *aad, int aad_len,
                    unsigned char *ciphertext, unsigned char *tag) {
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    int len, ciphertext_len;
    EVP_EncryptInit_ex(ctx, EVP_aes_128_gcm(), NULL, NULL, NULL);
    EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, 12, NULL);
    EVP_EncryptInit_ex(ctx, NULL, NULL, key, nonce);
    EVP_EncryptUpdate(ctx, NULL, &len, aad, aad_len);
    EVP_EncryptUpdate(ctx, ciphertext, &len, plaintext, plaintext_len);
    ciphertext_len = len;
    EVP_EncryptFinal_ex(ctx, ciphertext + len, &len);
    ciphertext_len += len;
    EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, 16, tag);
    EVP_CIPHER_CTX_free(ctx);
    return ciphertext_len;
}
```

OpenSSL GCM 內部用 PCLMULQDQ + AES-NI — 極快（10+ GB/s on modern CPU）。

## Tag truncation

GCM tag 預設 16 byte。可 truncate 到 12, 8, 4 byte：

```
tag 16 byte: 2⁻¹²⁸ forge 機率
tag 12 byte: 2⁻⁹⁶
tag 8 byte:  2⁻⁶⁴   ← 邊界，多數場景不夠
tag 4 byte:  2⁻³²   ← 危險，attacker 可 brute force
```

預設 16 byte，**不要 truncate** 除非規範要求且攻擊者線上能查 query 受限。

## 一個常見誤解

「AES-GCM 比 AES-CBC + HMAC 安全得多」

**架構上一樣安全**（兩者都達 IND-CCA2）。但 GCM 工程上更難寫錯：

- 一個 primitive，library 包好
- 沒 padding 問題（CTR 不需要 padding）
- 速度更快（GMAC 用 PCLMUL + AES-NI 平行算）
- 有正式安全證明（McGrew-Viega 2004）

**但 GCM 對 nonce reuse 比 CBC 還毒**（forbidden attack 直接洩漏 H）。**正確管 nonce 比正確選 mode 更關鍵**。

## 自我檢核

- [ ] 我能畫出 AES-GCM 的 encrypt 流程
- [ ] 我能解釋 H = AES_enc(K, 0¹²⁸) 是 GHASH key
- [ ] 我能寫 forbidden attack：給兩個同 nonce 的 (C, T) 算 H
- [ ] 我能說出 96-bit random nonce 的 birthday 限制
- [ ] 我能用 OpenSSL EVP API 跑 AES-128-GCM
- [ ] 我能解釋 tag 不應 truncate 到 < 16 byte 的理由

下一章看 ChaCha20-Poly1305 與 nonce-misuse-resistant 變體（GCM-SIV）。

→ [Ch 27 ChaCha20-Poly1305 與 AES-GCM-SIV](./27-chacha20-poly1305-siv.md)
