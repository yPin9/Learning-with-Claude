# Ch 27 — ChaCha20-Poly1305 與 AES-GCM-SIV

> 目標：兩個現代 AEAD：ChaCha20-Poly1305（軟體實作快、抗 timing、Google / Cloudflare 大量採用）、AES-GCM-SIV（nonce-misuse-resistant，nonce 重複不會崩）。看設計者怎麼回應 GCM 的痛點。

## ChaCha20-Poly1305

Google 2014 Adam Langley 推進 TLS。RFC 8439（2018）正式標準。

```
ChaCha20-Poly1305(K, N, P, A):
  poly_key = ChaCha20_block(K, counter=0, N)[:32]
  ciphertext = ChaCha20_encrypt(K, counter=1, N, P)
  tag = Poly1305(poly_key, A || pad || C || pad || len_block)
  return (ciphertext, tag)

len_block = 8-byte len(A) || 8-byte len(C)
pad = pad to 16-byte boundary
```

**concept**：

- ChaCha20（Ch 12）做 stream encrypt
- Poly1305（Ch 16）做 MAC
- 用 ChaCha20 同 key + counter=0 derive Poly1305 一次性 key
- 真正 encrypt 從 counter=1 開始

## 為什麼贏 AES-GCM

```
        ChaCha20-Poly1305    AES-GCM
有 AES-NI 硬體：    慢一點         快
無 AES-NI（mobile / 舊 CPU）：     快很多       慢
Const-time：       天生            軟體易踩雷
規範簡單度：       簡單             複雜（GHASH）
專利 / 法律風險：   無              無（GCM 無）
```

Google 的 motivation：mobile users（沒 AES-NI）佔很多。**HTTPS 上推 ChaCha20-Poly1305**，Android / iOS browser 預設 prefer 它。

Cloudflare 也大量採用，2020 起。

## Forbidden attack 對 ChaCha20-Poly1305

ChaCha20-Poly1305 **同樣對 nonce reuse 致命**：

```
同 (K, N) 兩次：
  poly_key 同 → Poly1305 也是 universal hash
  attacker 拿兩個 (C, T) 仍能解 H_poly
  類似 GCM forbidden attack
```

**所有 nonce-based AEAD 都這個問題**。修補：nonce 必 unique。**XChaCha20-Poly1305** 用 192-bit nonce 緩解（random 也安全）。

## XChaCha20-Poly1305

24-byte nonce：

```
HChaCha20(K, N[:16]) → K_sub  (32-byte derived key)
ChaCha20-Poly1305(K_sub, N[16:], plaintext, aad)
```

前 16 byte nonce 派生 sub-key，後 8 byte 當實際 nonce。**192-bit random nonce 抗 birthday**：2⁹⁶ 訊息後才碰撞。

libsodium 預設 `crypto_secretbox` 用 XSalsa20-Poly1305（XChaCha20 的姊妹），nonce 24 byte。**API 安全 default**。

```python
from nacl.secret import SecretBox
box = SecretBox(os.urandom(32))
nonce = os.urandom(24)  # 安全：random 即可
ct = box.encrypt(b"hello", nonce)
```

## AES-GCM-SIV：nonce-misuse-resistant

Gueron / Lindell 2015。RFC 8452 (2019)。

**SIV = Synthetic IV**：nonce 從 plaintext 派生。**就算 nonce 重複，只洩漏「兩個訊息相同」的事實**，不會 catastrophic 破。

```
GCM-SIV(K, N, P, A):
  K_auth = derive from K, N
  K_enc = derive from K, N
  T = POLYVAL(K_auth, A || P || len_block)   ← polynomial
  T = T XOR N (truncated)
  T = AES_enc(K_enc, T) ← MSB cleared
  C = AES-CTR(K_enc, T, P)
  return (C, T)

decrypt:
  recover P from C using AES-CTR
  recompute T'
  if T' != T: AUTH FAIL
```

**特點**：

- nonce 重複時，只在「相同 (K, N, P, A)」場景產生相同 ciphertext（confidentiality 對重複的訊息有限制，但**不會** key 洩漏）
- **比 GCM 略慢**（多一次 polynomial）
- 對 unreliable nonce 場景（embedded, multi-thread without sync）安全

**Google 在 production 用** GCM-SIV 給內部系統（不易確保 nonce uniqueness 的環境）。

## 實際選擇

```
場景                         首選
─────────────────────────────────────
TLS 1.3 (server)             AES-GCM (有 AES-NI) 或 ChaCha20-Poly1305
TLS 1.3 (mobile client)      ChaCha20-Poly1305
端對端訊息 (Signal, etc.)    ChaCha20-Poly1305 / XChaCha20
不確定 nonce 管理            AES-GCM-SIV / XChaCha20-Poly1305
嵌入式 / IoT                 ChaCha20-Poly1305
WiFi (WPA2/3)                AES-CCM (規範要求)
File encryption              XChaCha20-Poly1305
```

## API 對照

```python
# AES-GCM
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
key = AESGCM.generate_key(128)
gcm = AESGCM(key)
ct = gcm.encrypt(nonce_12_byte, plaintext, aad)
pt = gcm.decrypt(nonce, ct, aad)

# ChaCha20-Poly1305 (12-byte nonce)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
chacha = ChaCha20Poly1305(ChaCha20Poly1305.generate_key())
ct = chacha.encrypt(nonce_12_byte, plaintext, aad)

# XChaCha20-Poly1305 (24-byte nonce, libsodium)
import nacl.bindings as nb
ct = nb.crypto_aead_xchacha20poly1305_ietf_encrypt(
    plaintext, aad, nonce_24_byte, key
)

# AES-GCM-SIV (Python: pycryptodome)
from Crypto.Cipher import AES
cipher = AES.new(key, AES.MODE_GCM_SIV, nonce=nonce_12_byte)
cipher.update(aad)
ct, tag = cipher.encrypt_and_digest(plaintext)
```

## Tag length

ChaCha20-Poly1305、AES-GCM-SIV 的 tag 都 16 byte。**不能 truncate**（規範禁止）。

AES-GCM 規範允許 truncate 但不建議。

## Which is faster?

modern Intel/AMD server (with AES-NI + PCLMUL):

```
AES-128-GCM:       ~3 GB/s
AES-256-GCM:       ~2.5 GB/s
ChaCha20-Poly1305: ~1.5 GB/s
AES-GCM-SIV:       ~1 GB/s
```

modern Apple M1 / Cortex-A (ARM crypto extensions):

```
AES-128-GCM:       ~3 GB/s
ChaCha20-Poly1305: ~3 GB/s    (ARM 上接近 AES-GCM)
```

mobile / older CPU (no AES-NI):

```
AES-128-GCM:       ~200 MB/s   (T-table fallback)
ChaCha20-Poly1305: ~1 GB/s     (純 ARX 軟體)
```

**沒硬體 AES 時 ChaCha20-Poly1305 大勝 5×**。

## 一個常見誤解

「ChaCha20-Poly1305 比 AES-GCM 安全」

**安全度等同**（128-bit security level）。差別只在性能與工程：

- ChaCha20-Poly1305：純軟體快、const-time 容易、規範簡單
- AES-GCM：硬體加速強、規範複雜、軟體 fallback 困難

**新系統選 ChaCha20-Poly1305 不是「更安全」，是「更難寫錯」+ 「mobile 友善」**。

## 自我檢核

- [ ] 我能寫 ChaCha20-Poly1305 encrypt 的 5 個步驟
- [ ] 我能解釋為什麼 ChaCha20-Poly1305 對 nonce reuse 仍致命
- [ ] 我能說出 XChaCha20 與 ChaCha20 的 nonce 差別
- [ ] 我能解釋 AES-GCM-SIV 的 nonce-misuse-resistance 機制
- [ ] 我能比較 AES-GCM、ChaCha20-Poly1305、AES-GCM-SIV 的選擇情境
- [ ] 我能用三種 library API 寫範例

下一章專門看 nonce 與隨機性 — Sony PS3、Debian OpenSSL 等災難。

→ [Ch 28 Nonce 與隨機性正確使用](./28-nonce-and-randomness.md)
