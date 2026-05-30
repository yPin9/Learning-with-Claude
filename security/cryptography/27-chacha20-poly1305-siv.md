# Ch 27 — ChaCha20-Poly1305 與 AES-GCM-SIV：TLS 的另一條路

> **目標**：能解釋 ChaCha20-Poly1305 為什麼成為 TLS 的第一選擇（在沒有 AES-NI 的平台上），理解 AES-GCM-SIV 的 nonce-misuse-resistance 設計——nonce 重複只洩漏「兩個 plaintext 是否相同」，不洩漏 plaintext 本身。

## 為什麼需要這個？

Ch 26 展示了 AES-GCM 的強大，也展示了它的致命弱點：nonce reuse 會直接洩漏 GHASH key。更實際的問題是：AES-GCM 的高效能**依賴硬體加速**（AES-NI + CLMUL 指令）。在沒有這些指令的平台上——ARM Cortex-A 舊版、MIPS、RISC-V、舊款手機——AES-GCM 的純軟體實作不只慢，還容易產生 timing side-channel（因為 table lookup 的 cache timing 會洩漏資訊）。

Daniel J. Bernstein 在 2008 年設計的 ChaCha20 和 Poly1305 解決了這兩個問題：

1. **純 ARX 運算**（Add、Rotate、XOR）：不需要查表，天生 constant-time，在任何 CPU 上都安全
2. **軟體效能卓越**：在沒有 AES-NI 的平台上，ChaCha20-Poly1305 比 AES-GCM 快 2–3 倍

Google 在 2014 年把 ChaCha20-Poly1305 推進 TLS，Cloudflare 跟進。到了 TLS 1.3，`TLS_CHACHA20_POLY1305_SHA256` 是三個必須支援的 cipher suite 之一。

但 ChaCha20-Poly1305 和 AES-GCM 有共同的弱點：nonce reuse 仍然致命。AES-GCM-SIV 是 Google 提出的解答——犧牲一點效能，換取 nonce-misuse-resistance。

## 先建立直覺

```
AES-GCM（Ch 26）：
  快（有硬體加速時）+ nonce reuse = 災難
  → 適合「確定能管好 nonce」的場景

ChaCha20-Poly1305：
  快（純軟體）+ 無 timing side-channel + nonce reuse = 災難
  → 適合「沒有 AES-NI」或「paranoid about side-channel」的場景

AES-GCM-SIV：
  稍慢 + nonce reuse 只洩漏 equality（不洩漏 plaintext）
  → 適合「不確定能管好 nonce」的場景（如分散式系統、VM snapshot）
```

## 核心概念：ChaCha20 stream cipher

### ChaCha20 的結構

ChaCha20 是 Salsa20 的改良版（Bernstein 2008）。它的 state 是一個 4×4 的 32-bit word matrix：

```
ChaCha20 initial state (512-bit = 16 × 32-bit words):

┌───────────┬───────────┬───────────┬───────────┐
│ "expa"    │ "nd 3"    │ "2-by"    │ "te k"    │  ← 常數（"expand 32-byte k"）
├───────────┼───────────┼───────────┼───────────┤
│ key[0..3] │ key[4..7] │ key[8..11]│key[12..15]│  ← 256-bit key (8 words)
├───────────┼───────────┼───────────┼───────────┤
│key[16..19]│key[20..23]│key[24..27]│key[28..31]│
├───────────┼───────────┼───────────┼───────────┤
│ counter   │ nonce[0..3]│nonce[4..7]│nonce[8..11]│ ← 32-bit counter + 96-bit nonce
└───────────┴───────────┴───────────┴───────────┘
```

### Quarter Round：ChaCha20 的核心運算

```
QR(a, b, c, d):
  a += b;  d ^= a;  d <<<= 16;
  c += d;  b ^= c;  b <<<= 12;
  a += b;  d ^= a;  d <<<= 8;
  c += d;  b ^= c;  b <<<= 7;

只有三種運算：加法、XOR、旋轉（ARX）
沒有乘法、沒有查表、沒有分支 → constant-time 是天生的
```

完整的 ChaCha20 block function：

```
1. 設定 initial state（如上圖）
2. working_state = initial_state
3. 重複 20 rounds（10 次 double-round）：
   每個 double-round：
     QR(0, 4, 8, 12)   QR(1, 5, 9, 13)    ← column rounds
     QR(2, 6, 10, 14)   QR(3, 7, 11, 15)
     QR(0, 5, 10, 15)   QR(1, 6, 11, 12)   ← diagonal rounds
     QR(2, 7, 8, 13)    QR(3, 4, 9, 14)
4. output = working_state + initial_state   ← word-wise addition
5. output 就是 512-bit (64 bytes) 的 keystream block
```

加密：`ciphertext = plaintext ⊕ keystream`（和 AES-CTR 一樣，是 stream cipher）。

### 範例一：Python ChaCha20-Poly1305 加解密

```python
"""
ChaCha20-Poly1305 加解密
RFC 8439 定義的 AEAD construction
"""
import os
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

# 256-bit key（ChaCha20 固定用 256-bit key）
key = ChaCha20Poly1305.generate_key()
chacha = ChaCha20Poly1305(key)

# 96-bit nonce（RFC 8439 規定）
nonce = os.urandom(12)

# Associated Data + Plaintext
ad = b"metadata:version=1,type=message"
plaintext = b"ChaCha20-Poly1305 is the AEAD of choice for software-only platforms"

# 加密
ct = chacha.encrypt(nonce, plaintext, ad)
print(f"Key size:        {len(key)} bytes (256-bit, fixed)")
print(f"Nonce size:      {len(nonce)} bytes (96-bit)")
print(f"Plaintext size:  {len(plaintext)} bytes")
print(f"Ciphertext size: {len(ct)} bytes (plaintext + 16-byte tag)")
print(f"Tag:             {ct[-16:].hex()}")

# 解密
decrypted = chacha.decrypt(nonce, ct, ad)
print(f"\n解密: {decrypted.decode()}")

# 篡改檢測
tampered = bytearray(ct)
tampered[0] ^= 0x01
try:
    chacha.decrypt(nonce, bytes(tampered), ad)
except Exception as e:
    print(f"\n篡改偵測: {e}")
```

## 底層機制：Poly1305 one-time MAC

### Poly1305 的數學

Poly1305 是一個 one-time MAC（一次性訊息驗證碼），由 Bernstein 在 2005 年設計。它的核心是模質數 p = 2¹³⁰ - 5 的多項式求值。

```
Poly1305 的輸入：
  r: 128-bit key（「clamped」——某些 bits 被強制設為 0，限制 r 的值域）
  s: 128-bit key
  msg: 要驗證的訊息

計算過程：
1. 把 msg 切成 16-byte blocks：m₁, m₂, ..., mₙ
2. 每個 block 加上一個 0x01 byte 在最高位（標記 block 長度）
   → 得到 17-byte 整數 c₁, c₂, ..., cₙ
3. 計算多項式（mod p = 2¹³⁰ - 5）：
   tag = ((c₁·r^n + c₂·r^(n-1) + ... + cₙ·r) mod p) + s  (mod 2¹²⁸)

注意最後 + s 是普通整數加法 mod 2¹²⁸（不是 mod p）
```

和 GHASH 的比較：

```
GHASH:    GF(2¹²⁸) 上的多項式求值（XOR + carry-less multiplication）
Poly1305: Z/(2¹³⁰-5) 上的多項式求值（整數加法 + 整數乘法 mod prime）

GHASH 需要 CLMUL 指令才快；Poly1305 用普通整數乘法就快。
```

### RFC 8439 的組合方式

ChaCha20-Poly1305 不是「ChaCha20 加密完再用獨立 key 做 Poly1305」——而是 ChaCha20 的第一個 keystream block 用來生成 Poly1305 的 key：

```
1. ChaCha20 block 0 → 取前 32 bytes 作為 Poly1305 key (r, s)
2. ChaCha20 block 1, 2, 3, ... → 加密 plaintext
3. Poly1305(r, s)(AD_padded || ciphertext_padded || len(AD) || len(CT)) → tag

注意：Poly1305 算在 ciphertext（不是 plaintext）上 → 這是 Encrypt-then-MAC！
```

```
完整流程：

  ChaCha20(key, nonce, counter=0) → [poly_key (32 bytes) | 剩餘丟棄]
  ChaCha20(key, nonce, counter=1) → keystream block 1
  ChaCha20(key, nonce, counter=2) → keystream block 2
  ...

  ciphertext = plaintext ⊕ keystream[1..]

  Poly1305 input = pad(AD) || pad(CT) || len(AD) as 8-byte LE || len(CT) as 8-byte LE
  tag = Poly1305(poly_key, Poly1305_input)

  output = ciphertext || tag
```

## 進一步用法：AES-GCM-SIV 與 nonce-misuse-resistance

### SIV 的核心思想

SIV（Synthetic IV，合成初始向量）由 Rogaway & Shrimpton 在 2006 年提出。核心想法：

**不要讓使用者提供 nonce——從 plaintext 和 AD 生成 nonce。**

```
傳統 AEAD（GCM / ChaCha20-Poly1305）：
  nonce 由使用者提供 → 使用者搞砸 → 災難

SIV 模式：
  synthetic_nonce = PRF(key, AD, plaintext) → 從內容派生
  ciphertext = Encrypt(key, synthetic_nonce, plaintext)
  tag = synthetic_nonce  ← tag 本身就是 nonce！
```

如果兩次加密的 (AD, plaintext) 完全相同：
- synthetic_nonce 相同 → ciphertext 相同 → 洩漏「兩次 plaintext 相同」
- 但不洩漏 plaintext 內容

如果兩次加密的 (AD, plaintext) 不同：
- synthetic_nonce 不同 → 完全安全，和正常 AEAD 一樣

這比 AES-GCM 好太多：GCM 的 nonce reuse 洩漏 GHASH key + plaintext XOR；SIV 的 nonce reuse 最多洩漏 equality。

### AES-GCM-SIV 的具體結構

AES-GCM-SIV（RFC 8452, 2019）結合了 GCM 的效率和 SIV 的 misuse-resistance：

```
AES-GCM-SIV 加密流程：

1. 從 (key, nonce) 派生兩個子 key：
   msg_auth_key = AES(key, nonce || 0)[:16]
   msg_enc_key  = AES(key, nonce || 1)[:16] || AES(key, nonce || 2)[:16]
   （256-bit enc key 時用 4 次 AES）

2. 計算 POLYVAL（GHASH 的「小端」變體）：
   S = POLYVAL(msg_auth_key, AD, plaintext)
   
3. 生成 synthetic tag：
   tag = AES(msg_enc_key, S ⊕ nonce)
   tag 的最高 bit 被清零（用來區分 tag 和 counter）

4. 用 tag 作為 AES-CTR 的 nonce 加密 plaintext：
   ciphertext = AES-CTR(msg_enc_key, tag, plaintext)

5. 輸出 (ciphertext || tag)
```

關鍵區別：tag 在加密之前就計算好了（SIV 的 DAE 模式——Deterministic Authenticated Encryption），然後 tag 本身被用作 CTR 的 nonce。

### 範例二：AES-GCM-SIV 在 nonce reuse 下的行為

```python
"""
AES-GCM-SIV 在 nonce reuse 下的行為：
只洩漏「兩次 plaintext 是否相同」，不洩漏 plaintext 內容
"""
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV

key = AESGCMSIV.generate_key(bit_length=256)
siv = AESGCMSIV(key)

nonce = os.urandom(12)  # 故意重複使用同一個 nonce
ad = b""

# Case 1: 相同 nonce + 相同 plaintext → 相同 ciphertext（洩漏 equality）
p1 = b"same message"
ct1a = siv.encrypt(nonce, p1, ad)
ct1b = siv.encrypt(nonce, p1, ad)  # 同一個 nonce 和 plaintext
print("=== Case 1: 相同 nonce + 相同 plaintext ===")
print(f"ct1a == ct1b? {ct1a == ct1b}")  # True — 洩漏「兩次 plaintext 相同」
print(f"  ct1a tag: {ct1a[-16:].hex()}")
print(f"  ct1b tag: {ct1b[-16:].hex()}")

# Case 2: 相同 nonce + 不同 plaintext → 不同 ciphertext（不洩漏 XOR）
p2 = b"diff message"
ct2 = siv.encrypt(nonce, p2, ad)
print("\n=== Case 2: 相同 nonce + 不同 plaintext ===")
print(f"ct1a == ct2? {ct1a == ct2}")  # False

# 關鍵：ct1a ⊕ ct2 不等於 p1 ⊕ p2（因為 CTR nonce 不同！）
c1_body = ct1a[:-16]
c2_body = ct2[:-16]
ct_xor = bytes(a ^ b for a, b in zip(c1_body, c2_body))
pt_xor = bytes(a ^ b for a, b in zip(p1, p2))
print(f"ct1 ⊕ ct2 == p1 ⊕ p2? {ct_xor == pt_xor}")  # False!

# 對比 AES-GCM：相同 nonce + 不同 plaintext → 洩漏 p1 ⊕ p2
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
gcm_key = AESGCM.generate_key(bit_length=256)
gcm = AESGCM(gcm_key)
gcm_nonce = os.urandom(12)
gcm_ct1 = gcm.encrypt(gcm_nonce, p1, ad)
gcm_ct2 = gcm.encrypt(gcm_nonce, p2, ad)
gcm_ct_xor = bytes(a ^ b for a, b in zip(gcm_ct1[:-16], gcm_ct2[:-16]))
print(f"\n=== 對比 AES-GCM (nonce reuse) ===")
print(f"GCM ct1 ⊕ ct2 == p1 ⊕ p2? {gcm_ct_xor == pt_xor}")  # True! 洩漏了

# 所有 ciphertext 都能正常解密
for label, ct in [("SIV ct1a", ct1a), ("SIV ct1b", ct1b), ("SIV ct2", ct2)]:
    pt = siv.decrypt(nonce, ct, ad)
    print(f"\n{label} 解密: {pt}")
```

輸出的關鍵觀察：
- AES-GCM-SIV：nonce reuse 下 `ct1 ⊕ ct2 ≠ p1 ⊕ p2`——因為 tag（SIV 的 synthetic nonce）不同
- AES-GCM：nonce reuse 下 `ct1 ⊕ ct2 = p1 ⊕ p2`——plaintext XOR 直接洩漏

## 對比與取捨

| 特性 | AES-GCM | ChaCha20-Poly1305 | AES-GCM-SIV |
|---|---|---|---|
| **設計者** | McGrew & Viega (2004) | Bernstein (2008) / RFC 8439 | Gueron & Lindell (2017) / RFC 8452 |
| **內部加密** | AES-CTR | ChaCha20 (ARX) | AES-CTR |
| **內部 MAC** | GHASH (GF(2¹²⁸)) | Poly1305 (mod 2¹³⁰-5) | POLYVAL (GF(2¹²⁸) 變體) |
| **Key size** | 128 / 256 bit | 256 bit (固定) | 128 / 256 bit |
| **Nonce size** | 96 bit (推薦) | 96 bit (固定) | 96 bit |
| **Nonce reuse 後果** | **致命**（H 洩漏 + PT XOR） | **致命**（PT XOR + tag 偽造）| 只洩漏 equality |
| **硬體加速** | AES-NI + CLMUL | **不需要** | AES-NI + CLMUL |
| **軟體效能（無 HW）** | 慢 + side-channel 風險 | **快 + constant-time** | 慢 + side-channel 風險 |
| **硬體效能（有 HW）** | **極快**（~1 cycle/byte） | 快（~1.5 cycle/byte） | 稍慢（多一次 pass） |
| **TLS 1.3 支援** | 是（必須） | 是（必須） | 否（非標準 cipher suite） |
| **Two-pass** | 否（one-pass） | 否（one-pass） | **是**（需讀 plaintext 兩次）|
| **適用場景** | 有 AES-NI 的 server | 手機、IoT、無 HW 加速 | 分散式系統、VM、不信任 nonce |

### 什麼時候用哪個？

```
有 AES-NI + 能保證 nonce 唯一 → AES-GCM（最快）
沒 AES-NI / 擔心 side-channel → ChaCha20-Poly1305
不確定 nonce 是否唯一         → AES-GCM-SIV
不確定 + 沒 AES-NI            → XChaCha20-Poly1305（192-bit nonce 版本）
```

## 踩雷集錦

1. **「ChaCha20-Poly1305 比 AES-GCM 更安全」**：在正確使用下（nonce 不重複），兩者的安全性是等價的。差異在工程層面：ChaCha20 不需要硬體加速、天生 constant-time。但有 AES-NI 的 server 上 AES-GCM 更快，而且 AES 的密碼分析歷史更長（20+ 年）。

2. **「AES-GCM-SIV 是 AES-GCM 的嚴格升級」**：不是。SIV 是 two-pass 設計——需要讀 plaintext 兩次才能加密（第一次算 tag / synthetic nonce，第二次做 CTR 加密）。這意味著它無法做 streaming encryption（必須把整個 plaintext 放在記憶體中），而且效能大約是 AES-GCM 的 60-70%。

3. **「XChaCha20-Poly1305 是標準」**：XChaCha20 用 192-bit nonce（比 ChaCha20 的 96-bit 大一倍），大幅降低 random nonce collision 的風險。但它沒有正式的 RFC（只有 draft），也不在 TLS 1.3 的 cipher suite 中。libsodium 支援它，用於非 TLS 場景很好。

4. **「Poly1305 可以重複使用 key」**：Poly1305 是 one-time MAC——(r, s) pair 只能用一次。如果重複使用同一個 (r, s) 做兩次 MAC，攻擊者能還原 r（和 GHASH nonce reuse 類似的多項式求根攻擊）。RFC 8439 的設計確保每次加密都從 ChaCha20 block 0 生成新的 (r, s)。

5. **「SIV 不需要 nonce」**：嚴格的 SIV（Rogaway 原始設計）確實可以不用 nonce——它是 deterministic encryption。但 AES-GCM-SIV 仍然需要 nonce 輸入——nonce 被用來派生子 key。nonce reuse 的後果被降級為「洩漏 equality」（而非致命），但為了最佳安全性仍然應該用 unique nonce。

## 進階：再往深一層

### ChaCha20 的密碼分析現狀

截至 2024 年，對 ChaCha20（20 rounds）的最佳已知攻擊：

- 7-round ChaCha：被差分密碼分析打破（Aumasson et al., 2008）
- 8-round ChaCha：2²⁴⁸ 的攻擊複雜度（比暴力好一點，但不實用）
- 12-round 和 20-round：沒有比暴力更快的已知攻擊

安全餘裕（security margin）：20 rounds 中只有 7 rounds 被打破 → 65% 的安全餘裕。相比之下 AES-128 的安全餘裕更薄（10 rounds 中有 7 rounds 可被打破的相關 key attack）。

### POLYVAL vs GHASH

AES-GCM-SIV 用的不是 GHASH 而是 POLYVAL——兩者在數學上等價（都是 GF(2¹²⁸) 上的多項式求值），差別在 bit ordering：

```
GHASH:   使用 big-endian bit ordering（MSB first）
POLYVAL: 使用 little-endian bit ordering（LSB first）

POLYVAL 在 little-endian CPU（x86、ARM）上更自然——
不需要 byte-swap，CLMUL 指令可以直接用。
```

### Key Commitment 與 AES-GCM-SIV

AES-GCM-SIV 提供了比 AES-GCM 更強的 key commitment（但不完美）：由於 tag 是從 plaintext 和 key 一起派生的，要找到兩個不同的 key 讓同一個 ciphertext 解密成功更加困難。但完全的 key commitment 需要額外的機制（如 HKDF-based key derivation + commitment tag）。

## 動手練習

1. **ChaCha20 quarter round**：用 Python 實作 `quarter_round(a, b, c, d)`，輸入 (0x11111111, 0x01020304, 0x9b8d6f43, 0x01234567)，和 RFC 8439 Section 2.1.1 的測試向量比對。

2. **效能比較**：寫一個 benchmark，對 1 MB 的資料分別用 AES-128-GCM 和 ChaCha20-Poly1305 加密 1000 次，比較耗時。在你的機器上（有/沒有 AES-NI）差多少？

3. **SIV 的 deterministic 特性**：用 AES-GCM-SIV 對相同的 (key, nonce, ad, plaintext) 加密 100 次，驗證每次的 ciphertext 和 tag 完全相同。然後改一個 byte 的 plaintext，驗證 ciphertext 和 tag 都完全不同。

4. **nonce reuse 比較**：擴展範例二，對比三種 AEAD 在 nonce reuse 下的資訊洩漏：AES-GCM（洩漏 PT XOR + GHASH key）、ChaCha20-Poly1305（洩漏 PT XOR）、AES-GCM-SIV（只洩漏 equality）。

## 本章重點整理

- ChaCha20 是 ARX 結構的 stream cipher：只用加法、旋轉、XOR，天生 constant-time，不需要硬體加速
- Poly1305 是 one-time MAC，在 mod 2¹³⁰-5 的域上做多項式求值；RFC 8439 用 ChaCha20 block 0 生成 Poly1305 key
- AES-GCM-SIV 用 SIV 模式實現 nonce-misuse-resistance：tag 從 plaintext 派生，再用 tag 作為 CTR nonce → nonce reuse 只洩漏 equality
- 三者的選擇取決於硬體支援、nonce 管理能力、streaming 需求

## 自我檢核

- [ ] 能畫出 ChaCha20 的 state matrix（4×4 words: constant / key / counter+nonce）
- [ ] 能解釋 ARX 為什麼天生 constant-time（不查表、不分支）
- [ ] 能說出 Poly1305 為什麼是 one-time MAC（重複使用 key 的後果）
- [ ] 能解釋 SIV 模式的核心思想（從 plaintext 派生 nonce → nonce reuse 只洩漏 equality）
- [ ] 能根據場景（有/無 AES-NI、nonce 管理能力）選擇合適的 AEAD

## 延伸閱讀

- **Daniel J. Bernstein, "ChaCha, a variant of Salsa20" (2008)**
  - **讀哪裡**：全文（8 頁），重點在 Section 2 的 quarter round 改進
  - **學什麼**：ChaCha20 相比 Salsa20 的差異——更好的 diffusion per round
  - **關聯**：本章 ChaCha20 結構的原始出處

- **RFC 8439 "ChaCha20 and Poly1305 for IETF Protocols" (2018)**
  - **讀哪裡**：Section 2（演算法描述）+ Section 2.8（AEAD construction）+ Appendix A（測試向量）
  - **學什麼**：IETF 標準化的 ChaCha20-Poly1305——包含完整的測試向量讓你驗證實作
  - **關聯**：本章範例一的 AEAD construction 出處

- **RFC 8452 "AES-GCM-SIV: Nonce Misuse-Resistant Authenticated Encryption" (2019)**
  - **讀哪裡**：Section 3（演算法）+ Section 7（安全考量）
  - **學什麼**：AES-GCM-SIV 的完整 spec——key derivation、POLYVAL、synthetic tag
  - **關聯**：本章 AES-GCM-SIV 結構的權威規範

- **Adam Langley, "AES-GCM-SIV" (blog post, 2017)**
  - **讀哪裡**：全文
  - **學什麼**：Google 為什麼需要 nonce-misuse-resistant AEAD——VM snapshot / database 場景的 nonce 管理困難
  - **關聯**：AES-GCM-SIV 設計動機的工程視角

→ [Ch 28 Nonce 與隨機性](./28-nonce-randomness.md)
