# Ch 25 — AEAD 概念：encrypt-then-MAC、IND-CCA2

> 目標：搞懂為什麼「光加密不夠」（unauthenticated encryption 已死）— authenticated encryption with associated data（AEAD）、encrypt-then-MAC vs MAC-then-encrypt vs encrypt-and-MAC、IND-CCA2 安全等級。

## 為什麼需要 AEAD

回顧前幾章：

```
Ch 11: padding oracle 攻擊 — 因為 server 對 padding 對 / 錯回不同錯誤
Ch 15: length extension — 因為 H(secret || M) 不是真 MAC
Ch 20: Bleichenbacher 1998 — RSA padding oracle
```

**所有這些攻擊的根：純加密沒檢查 ciphertext integrity**。Eve 可以改 ciphertext，server 仍解密但結果錯，回的錯誤訊息洩漏資訊。

**AEAD = Authenticated Encryption with Associated Data**：

- **加密**：保密 plaintext
- **驗證**：偵測 ciphertext 被改
- **AD (Associated Data)**：頂層 metadata 不加密但要驗證

```
encrypt(key, nonce, plaintext, aad) → (ciphertext, tag)
decrypt(key, nonce, ciphertext, tag, aad) → plaintext or "AUTH FAIL"
```

任何 ciphertext / aad / tag 修改 → decrypt 直接 reject，**不洩漏其他資訊**。

## Encrypt-then-MAC vs 其他選擇

把對稱加密與 MAC 組合有三種：

```
1. Encrypt-then-MAC（ETM）：
     c = Enc(K_E, m)
     tag = MAC(K_M, c)
     send (c, tag)
     
2. MAC-then-encrypt（MtE）：
     tag = MAC(K_M, m)
     c = Enc(K_E, m || tag)
     send c
     
3. Encrypt-and-MAC（EaM）：
     c = Enc(K_E, m)
     tag = MAC(K_M, m)
     send (c, tag)
```

三種看起來都 OK，但**只有 Encrypt-then-MAC 安全**（在 generic composition 下）。

### 為什麼 ETM 才安全

Bellare-Namprempre 2000 paper 給正式分析：

| | IND-CPA | INT-CTXT | IND-CCA2 |
|---|---|---|---|
| ETM | ✓ | ✓ | ✓ |
| MtE | ✓ | ✗ | ✗ |
| EaM | ✓ | ✗ | ✗ |

**INT-CTXT (Integrity of Ciphertext)**：attacker 不能產生**新 ciphertext** 通過 verify。

ETM 達成 → attacker 改 c 必須能算出新的 valid tag（要 K_M）→ 改不了。

MtE：attacker 改 c → server 解密、看 tag 是否對 plaintext。**解密本身**就是處理（padding check 等）洩漏資訊。

EaM：attacker 改 c 但保留原 tag。**tag 只驗 plaintext**，change c 解出新 m'，attacker 看 m' 是否正常 → 部分洩漏。

### 真實案例

**TLS 1.0 / 1.1 用 MAC-then-encrypt** → padding oracle 攻擊（POODLE、Lucky 13）。**TLS 1.2 開始改 encrypt-then-MAC**（cipher suite 名字含 `_GCM_` 或 `_POLY1305_`）。**TLS 1.3 強制 AEAD**。

**SSH 一直用 ETM**（早期就對）。
**IPSec ESP 是 ETM**。

## AEAD 的 nonce / IV / counter

```
AES-GCM:        96-bit nonce (推薦)，內部 32-bit counter
ChaCha20-Poly1305:  96-bit nonce
AES-GCM-SIV:    96-bit nonce, 但 nonce-misuse-resistant
XChaCha20-Poly1305: 192-bit nonce (random 安全)
```

**nonce 不能重複**（同 (key, nonce) 用兩次直接破）。實務管理：

1. **counter-based**：訊息序號當 nonce
2. **random**：96-bit nonce 在 ~2³² 訊息後 birthday 重複（不夠安全）
3. **192-bit (XChaCha20)**：random 也安全

## Associated Data

AD 是 **不加密但要驗證的 metadata**：

```
TCP/IP packet:
  header (IP/TCP) ←── AD (要驗證但要 router 看)
  payload         ←── 加密
```

```python
ciphertext, tag = aes_gcm_encrypt(key, nonce, plaintext, aad=ip_header)
# 收方：
plaintext = aes_gcm_decrypt(key, nonce, ciphertext, tag, aad=ip_header)
# 如果 ip_header 被改 → 解密失敗
```

不然 attacker 改 packet header 而不改 payload，receiver 看不出。

## IND-CCA2：AEAD 的安全等級

回憶 Ch 7：

- **IND-CPA**：attacker 能加密，無法區分兩個 plaintext
- **IND-CCA**：attacker 還能解密任意 ciphertext（除 challenge）
- **IND-CCA2**：adaptive 版，attacker 看 challenge 後仍能查 oracle

**ETM with secure components → IND-CCA2**：

- attacker 能改 c → 但要產 tag 必失敗
- 解密 oracle 對 invalid tag 直接 abort，不洩漏其他

**這就是 AEAD 給的承諾**：抗 chosen-ciphertext attack。

## 主流 AEAD

```
AES-GCM:        AES-CTR + GMAC
                NIST SP 800-38D
                AES-NI 加速最快
                Ch 26 詳述
                
ChaCha20-Poly1305: ChaCha20 stream + Poly1305
                IETF RFC 8439
                純軟體最快
                Ch 27 詳述
                
AES-CCM:        AES-CTR + CBC-MAC
                802.11 (WPA2)、Bluetooth、IPSec
                為硬體限制場景
                
AES-GCM-SIV:    AES-GCM 加 nonce-misuse-resistance
                RFC 8452
                Ch 27 詳述
                
AES-OCB:        AES + 提的另一種構造
                曾被專利限制，現開放
                高效但生態小
                
AEGIS:          基於 AES round function 的 AEAD
                IRTF CFRG 推薦
                極高吞吐
```

## 怎麼選

```
TLS 1.3 / HTTPS：AES-GCM 或 ChaCha20-Poly1305
  - 有 AES-NI hardware：AES-GCM
  - 純軟體 / mobile：ChaCha20-Poly1305
  
WiFi (WPA2/3)：AES-CCM
Bluetooth：AES-CCM

嵌入式 / IoT：
  - 有 AES 加速：AES-CCM 或 AES-GCM
  - 純軟體：ChaCha20-Poly1305
  
要 nonce-misuse-resistance：AES-GCM-SIV、XChaCha20-Poly1305
高吞吐 server：AEGIS
```

預設選擇：**ChaCha20-Poly1305**（普適最佳，沒 AES-NI 需求）。

## 通用 AEAD API

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305

key = AESGCM.generate_key(bit_length=128)
aesgcm = AESGCM(key)
nonce = os.urandom(12)
ciphertext = aesgcm.encrypt(nonce, plaintext=b"secret", associated_data=b"header")
plaintext = aesgcm.decrypt(nonce, ciphertext, associated_data=b"header")

# ChaCha20-Poly1305 同 API
chacha = ChaCha20Poly1305(ChaCha20Poly1305.generate_key())
nonce = os.urandom(12)
ct = chacha.encrypt(nonce, b"secret", b"aad")
pt = chacha.decrypt(nonce, ct, b"aad")
```

**輸出 ciphertext = encrypted_data + tag** 接在一起（library 自動處理）。

## libsodium：simpler API

```python
from nacl.secret import SecretBox
from nacl.utils import random

box = SecretBox(random(SecretBox.KEY_SIZE))
nonce = random(SecretBox.NONCE_SIZE)  # 24-byte XSalsa20 nonce
ct = box.encrypt(b"hello", nonce)
pt = box.decrypt(ct)
```

libsodium 用 XSalsa20-Poly1305（24-byte nonce 是 random 安全）。**API 比 cryptography 還簡單**。對嵌入式 / 跨平台首選。

## 一個常見誤解

「我用 AES-CBC + HMAC 不就等同 AEAD？」

**理論上是，但工程上很容易寫錯**：

- key separation：CBC key 與 HMAC key 必須**不同**（不能 same key）
- order：必 encrypt-then-MAC（ETM）
- timing：tag 比較必 const-time
- padding：CBC 仍有 padding 必正確處理 + MAC 防止
- nonce / IV：必 unique

**所以業界推 AEAD primitive**（GCM、Poly1305）— 由 library 把這些細節包好。**自己 compose AES-CBC + HMAC = 5 個踩雷點**。

## 自我檢核

- [ ] 我能解釋為什麼純加密（無 MAC）不安全
- [ ] 我能比較 ETM、MtE、EaM 的安全性
- [ ] 我能列出 IND-CPA、IND-CCA、IND-CCA2 的差別
- [ ] 我能說出 AEAD 的三個輸入（key、nonce、plaintext）+ AD
- [ ] 我能用 Python `cryptography` library 跑 AES-GCM 與 ChaCha20-Poly1305
- [ ] 我能說出實務 AEAD 選擇 cheat sheet

下一章看 AES-GCM 完整解剖：CTR + GMAC、forbidden attack。

→ [Ch 26 AES-GCM 解剖](./26-aes-gcm.md)
