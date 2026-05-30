# Ch 16 — MAC：HMAC、Poly1305（深挖章）

> 目標：能解釋 MAC 的安全定義（EUF-CMA），理解 HMAC 為什麼安全，解釋 Poly1305 的 Wegman-Carter 構造。

---

## 為什麼需要 MAC

Ch 15 我們打破了 `H(key || message)` 這種天真的 MAC 構造。但「訊息認證」的需求不會消失——你下載一個 patch，怎麼確認它沒被篡改？你收到一個 API 請求，怎麼確認它來自持有 secret key 的人？

這些需求指向一個正式的密碼學原語：**Message Authentication Code（訊息認證碼，MAC）**。

MAC 吃一個金鑰 K 和訊息 M，吐出一個固定長度的 tag T：

```
T = MAC(K, M)
```

驗證方拿著同一把 K，對收到的 M 重算 MAC，比對 T 是否一致。

聽起來像 hash？差別在於 MAC 有 key。沒有 key 的人無法偽造合法的 tag——這就是 MAC 和 hash 的根本區別。

---

## 先建立直覺

MAC 像是一個「有鑰匙的蠟封」：

- **hash（無鑰）**：任何人都能對任何訊息蓋章，但蓋出來的章是固定的——你能驗證「這個章確實對應這份文件」，但無法阻止攻擊者自己蓋一個新章
- **MAC（有鑰）**：只有拿到鑰匙的人才能蓋章。沒有鑰匙的人即使看到 100 個 (message, tag) 對，也造不出一個新的合法 (message', tag')

---

## 核心概念：EUF-CMA 安全定義

MAC 的標準安全定義叫做 **EUF-CMA（Existential Unforgeability under Chosen-Message Attack）**。

用白話說：

1. 攻擊者可以選擇任意的 message，要求 oracle（持有 key 的黑箱）計算 MAC
2. 攻擊者可以看到任意多的 (message, tag) 對
3. 即使有了以上所有資訊，攻擊者仍然**無法**產出一個「之前沒問過的」(message', tag') 對

```
攻擊者                           Oracle（有 K）
  │                                 │
  │──── m₁ ────────────────────────→│
  │←─── t₁ = MAC(K, m₁) ──────────│
  │                                 │
  │──── m₂ ────────────────────────→│
  │←─── t₂ = MAC(K, m₂) ──────────│
  │                                 │
  │         ... 重複 q 次 ...        │
  │                                 │
  │ 目標：產出 (m*, t*) 使得         │
  │   t* = MAC(K, m*)               │
  │   且 m* ∉ {m₁, m₂, ..., mq}    │
  │                                 │
  └─ 如果做不到 → MAC 是 EUF-CMA 安全
```

注意：攻擊者不需要還原 key——只要能 forge 一個新的 tag 就算破了。

### 為什麼 `H(key || msg)` 不是 EUF-CMA

攻擊者問 oracle 要 `MAC(K, m₁) = H(K || m₁)`，拿到 tag。透過 length extension，攻擊者能算出 `H(K || m₁ || padding || extension)` 的合法 tag。這是一個新的 message（`m₁ || padding || extension`）但從未問過 oracle → EUF-CMA 破了。

---

## HMAC：Hash-based MAC

### HMAC 的構造

HMAC 由 Bellare、Canetti、Krawczyk 在 1996 年提出（RFC 2104）。

```
HMAC(K, M) = H( (K ⊕ opad) || H( (K ⊕ ipad) || M ) )
```

其中：
- `K` = 金鑰（如果比 block 長就先 hash，比 block 短就補零到 block 大小）
- `ipad` = `0x36` 重複到 block 大小（Inner pad）
- `opad` = `0x5c` 重複到 block 大小（Outer pad）
- `H` = 底層 hash 函式（通常是 SHA-256）

### HMAC 的 ASCII 圖解

```
金鑰 K（補零到 block size = 64 bytes for SHA-256）
  │
  ├──⊕ ipad (0x363636...36) ──→ K_ipad (64 bytes)
  │                                │
  │                                ▼
  │                         ┌─────────────┐
  │                         │ K_ipad || M  │
  │                         └──────┬──────┘
  │                                │
  │                                ▼
  │                         ┌─────────────┐
  │                         │   H(...)     │  ← 內層 hash
  │                         └──────┬──────┘
  │                                │
  │                          inner_hash (32 bytes for SHA-256)
  │                                │
  └──⊕ opad (0x5c5c5c...5c) ──→ K_opad (64 bytes)
                                   │
                                   ▼
                            ┌──────────────────┐
                            │ K_opad || inner   │
                            └────────┬─────────┘
                                     │
                                     ▼
                            ┌──────────────────┐
                            │    H(...)        │  ← 外層 hash
                            └────────┬─────────┘
                                     │
                                     ▼
                              HMAC(K, M)  (32 bytes)
```

### 為什麼 HMAC 安全——直覺版

HMAC 防禦 length extension 的核心在於**雙層結構**：

1. **內層** `H(K_ipad || M)` 確實是 M-D 構造，攻擊者**理論上**能做 length extension
2. 但 length extension 出來的結果要再過**外層** `H(K_opad || ...)`
3. 外層的 `K_opad` 包含 key——攻擊者不知道 key，無法計算外層的結果
4. 即使攻擊者改了內層的輸出，過外層時缺 key，算不出合法的 HMAC

更精確地說：HMAC 的安全性建立在「即使 H 不是 random oracle，只要 H 的壓縮函式是 PRF（偽隨機函式），HMAC 就是 PRF」。

### Bellare 2006 的安全證明（直覺）

Bellare 在 2006 年的論文 "New Proofs for NMAC and HMAC: Security without Collision-Resistance" 證明了：

> HMAC 的安全性不需要底層 hash 的碰撞抗性——只需要壓縮函式是 PRF。

這意味著即使 SHA-256 的碰撞被找到（像 SHA-1 一樣），HMAC-SHA-256 仍然可能安全。因為 HMAC 的安全性依賴更弱的假設。

實務上：HMAC-MD5 至今沒有已知攻擊，儘管 MD5 的碰撞在 2004 年就被打破了。

### 範例一：HMAC-SHA-256 的 Python 實作

```python
import hmac
import hashlib

key = b"my-secret-key-2024"
message = b"amount=100&to=alice"

# 方法一：用 hmac 模組（推薦）
mac_tag = hmac.new(key, message, hashlib.sha256).hexdigest()
print(f"HMAC-SHA-256: {mac_tag}")

# 方法二：手動實作 HMAC
def hmac_sha256_manual(key: bytes, msg: bytes) -> bytes:
    block_size = 64  # SHA-256 block size

    # 如果 key 比 block 長，先 hash
    if len(key) > block_size:
        key = hashlib.sha256(key).digest()

    # 補零到 block_size
    key = key.ljust(block_size, b'\x00')

    # ipad 和 opad
    ipad = bytes(k ^ 0x36 for k in key)
    opad = bytes(k ^ 0x5c for k in key)

    # 內層 hash
    inner = hashlib.sha256(ipad + msg).digest()

    # 外層 hash
    outer = hashlib.sha256(opad + inner).digest()

    return outer

manual_tag = hmac_sha256_manual(key, message).hex()
print(f"手動 HMAC:    {manual_tag}")
assert mac_tag == manual_tag
print("兩種方法結果一致 ✓")

# 驗證 length extension 對 HMAC 無效
# 攻擊者能看到 mac_tag，但做不了 length extension
# 因為 mac_tag 是外層 hash 的輸出
# 要繼續外層 hash 需要知道 K_opad → 需要 key
```

### 驗證 HMAC 時的 timing attack 防禦

```python
import hmac

# 錯誤：用 == 比較
def verify_bad(key, msg, received_tag):
    expected = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return expected == received_tag  # timing leak!

# 正確：用 hmac.compare_digest
def verify_good(key, msg, received_tag):
    expected = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_tag)

# hmac.compare_digest 做 constant-time 比較
# 不管哪個 byte 不同，花的時間都一樣
```

---

## Poly1305：Polynomial MAC

### 為什麼需要 Poly1305

HMAC 安全，但需要呼叫 hash 函式三次（一次 ipad hash、一次把 inner hash 放進去、一次 opad hash）。在需要高吞吐的場景（像 TLS 資料加密），有沒有更快的 MAC？

Poly1305 是 Daniel J. Bernstein 在 2005 年設計的 MAC，吞吐量遠超 HMAC。它的設計基於 **Wegman-Carter 構造**——一個數學上優雅的方法論。

### Wegman-Carter 構造

Wegman-Carter（1981）的想法：

1. 設計一個 **almost-universal hash family（幾乎萬能雜湊族）**：一族 hash 函式 `{h_r}`，其中 r 是隨機選的 key。對任意兩個不同的 message m₁ ≠ m₂，碰撞機率很低：`Pr[h_r(m₁) = h_r(m₂)] ≤ ε`
2. 用這個 hash 算出一個短 tag：`t = h_r(m)`
3. 用一個 one-time pad 加密這個 tag：`T = t + s`，其中 s 是一次性的隨機值

```
MAC(r, s, M) = h_r(M) + s

r = polynomial evaluation key（可重複使用，但 Poly1305 要求 one-time）
s = one-time masking key（每次訊息必須不同）
```

安全性來自兩個層面：
- `h_r` 的 almost-universal 性質保證不同 message 的 `h_r(M)` 幾乎不碰撞
- `s` 的 one-time pad 保證 tag 的值被完全隱藏

### Poly1305 的具體數學

Poly1305 的 `h_r` 是一個多項式求值（Polynomial Evaluation）：

```
把訊息 M 切成 16-byte blocks：c₁, c₂, ..., cₙ

h_r(M) = (c₁ · rⁿ + c₂ · rⁿ⁻¹ + ... + cₙ · r¹) mod p

其中：
  p = 2¹³⁰ - 5（一個質數，Bernstein 選的，讓 mod 運算特別快）
  r = 128-bit key（有特殊的「clamping」限制）
  cᵢ = 第 i 個 16-byte block 加上一個 1 bit 的 sentinel
```

最終 tag：

```
T = (h_r(M) + s) mod 2¹²⁸

s = 128-bit one-time key
T = 128-bit tag
```

### Poly1305 的 ASCII 圖解

```
訊息 M 切成 16-byte blocks
┌─────┬─────┬─────┬─────┐
│ c₁  │ c₂  │ c₃  │ c₄  │
└──┬──┴──┬──┴──┬──┴──┬──┘
   │     │     │     │
   ▼     ▼     ▼     ▼
  c₁·r⁴  c₂·r³  c₃·r²  c₄·r¹     (mod 2¹³⁰ - 5)
   │     │     │     │
   └──┬──┴──┬──┴──┬──┘
      │     │     │
      ▼     ▼     ▼
      ──── + + + ────  (mod 2¹³⁰ - 5)
            │
            ▼
         h_r(M)   (130 bits)
            │
            + s    (mod 2¹²⁸)
            │
            ▼
          Tag T    (128 bits)
```

### 為什麼 Poly1305 這麼快

1. **多項式求值可以用 Horner's method**：只需要 n 次乘法 + n 次加法
2. **mod 2¹³⁰ - 5 的 reduction 特別快**：`x mod (2¹³⁰ - 5)` 可以用位移和加法完成，不需要通用除法
3. **沒有複雜的 round function**：不像 SHA-256 有 64 rounds 的壓縮函式
4. **128-bit 運算**：在 64-bit CPU 上用兩個 64-bit 乘法就能做一個 128-bit 乘法

### 範例二：Poly1305 的 Python 觀察

```python
# Poly1305 通常和 ChaCha20 一起用（ChaCha20-Poly1305）
# Python 3.11+ 的 hashlib 沒有獨立的 Poly1305
# 但 cryptography 套件有

from cryptography.hazmat.primitives.poly1305 import Poly1305
import os

# Poly1305 需要 32-byte key = r (16 bytes) + s (16 bytes)
key = os.urandom(32)
message = b"amount=100&to=alice"

# 計算 tag
p = Poly1305(key)
p.update(message)
tag = p.finalize()
print(f"Poly1305 tag: {tag.hex()}")
print(f"Tag 長度: {len(tag)} bytes = {len(tag)*8} bits")

# 驗證
p2 = Poly1305(key)
p2.update(message)
p2.verify(tag)  # 不 raise 就是驗證通過
print("驗證通過")

# 注意：Poly1305 的 key 是 one-time 的
# 同一個 key 用在兩個不同的 message 上會破壞安全性
# 實務上 key 由 ChaCha20 的 keystream 前 32 bytes 產生
# 每次加密用不同的 nonce → 不同的 key → 安全
```

### Poly1305 的 One-Time 限制

Poly1305 的 key (r, s) **必須是 one-time** 的——同一組 (r, s) 用在兩個不同的 message 上，攻擊者能還原 r：

```
已知：
  T₁ = h_r(M₁) + s  (mod 2¹²⁸)
  T₂ = h_r(M₂) + s  (mod 2¹²⁸)

T₁ - T₂ = h_r(M₁) - h_r(M₂)  (mod 2¹²⁸)
         = 多項式差，可以解出 r
```

一旦 r 被還原，攻擊者能算 s，然後 forge 任意訊息的 tag。

**解法**：ChaCha20-Poly1305（Ch 27）中，每次加密用 ChaCha20 的前 32 bytes 作為 Poly1305 的 key。不同的 nonce → 不同的 key → 安全。

---

## HMAC vs Poly1305 完整對比

| 維度 | HMAC-SHA-256 | Poly1305 |
|------|-------------|----------|
| 構造 | 雙層 hash | Wegman-Carter（多項式 + one-time pad） |
| Tag 長度 | 256 bit（可截斷） | 128 bit |
| 安全假設 | H 的壓縮函式是 PRF | r 是 one-time 的 |
| 速度（短訊息）| 中（至少 3 次 hash block） | 快 |
| 速度（長訊息）| 中 | 快（~3x 以上） |
| Key 管理 | 一把 key 可以用在多個 message | key 必須 one-time |
| 獨立使用 | 可以 | 需要搭配 stream cipher（通常 ChaCha20） |
| 標準化 | RFC 2104 / FIPS 198 | RFC 8439（和 ChaCha20 綁定） |
| 適用場景 | API 簽章、token 驗證、通用 MAC | AEAD 內的 MAC（TLS、WireGuard） |

### 什麼時候用哪個？

- **需要獨立的 MAC**（不跟加密綁在一起）→ HMAC-SHA-256
- **需要 AEAD**（加密 + 認證一起做）→ ChaCha20-Poly1305 或 AES-GCM
- **需要 NIST 認證**→ HMAC-SHA-256（或 CMAC-AES）
- **需要最快速度**→ Poly1305（但必須確保 key one-time）

---

## 踩雷集錦

### 踩雷 1：HMAC 的 key 太短

HMAC 的 key 應該至少和底層 hash 的 output 一樣長（SHA-256 → 至少 32 bytes）。太短的 key 安全性下降。太長的 key 會先被 hash 壓縮——不會出錯，但浪費效能。

### 踩雷 2：Poly1305 重用 key

Poly1305 的 key 是 **one-time** 的。重用 key = 破壞安全性。前面已經展示了攻擊。在 ChaCha20-Poly1305 中，key 由 ChaCha20 的 nonce 衍生，所以只要 nonce 不重複，key 就不會重複。但如果你手動用 Poly1305，你要自己保證 key 的唯一性。

### 踩雷 3：MAC-then-Encrypt vs Encrypt-then-MAC

組合 encryption 和 MAC 的順序很重要：

| 順序 | 安全性 | 問題 |
|------|--------|------|
| Encrypt-then-MAC | 安全（先加密再 MAC 密文） | 推薦 |
| MAC-then-Encrypt | 可能不安全 | TLS < 1.3 用這個，導致 Padding Oracle |
| Encrypt-and-MAC | 可能洩漏明文資訊 | SSH 的歷史做法 |

AEAD（Ch 25）把加密和 MAC 綁在一起，避免這個選擇錯誤。

### 踩雷 4：用 CMAC 但不知道 block cipher 的限制

CMAC（Cipher-based MAC，NIST SP 800-38B）用 AES 做底層。AES block 是 128 bit → birthday bound 是 2⁶⁴。當 MAC 了超過 2⁶⁴ 個 blocks（~256 EB），tag 碰撞的機率不可忽略。HMAC 沒有這個問題（SHA-256 的 state 是 256 bit）。

### 踩雷 5：忘記 MAC 不提供保密性

MAC 保證完整性（integrity）和認證（authentication），不保證保密性（confidentiality）。`HMAC(K, M)` 不會隱藏 M 的內容。如果你需要保密 + 認證，用 AEAD。

---

## 進階

### NMAC 和 HMAC 的關係

HMAC 其實是 NMAC 的實用化版本。NMAC（Nested MAC）定義為：

```
NMAC(K₁, K₂, M) = H_K₁(H_K₂(M))
```

其中 `H_K` 表示用 K 作為 IV 的 hash 函式。NMAC 需要兩個獨立 key，且需要修改 hash 的 IV（大部分 hash 函式不暴露這個 API）。

HMAC 的 trick：用 `K ⊕ ipad` 和 `K ⊕ opad` 當作「等效的兩個不同 key」，且把 key 放在 message 的開頭（而不是修改 IV），讓 HMAC 能用標準的 hash API 實作。

### GHash vs Poly1305

AES-GCM 的 MAC 部分用的是 GHash（Galois Hash），和 Poly1305 屬於同一類：polynomial MAC over a finite field。

| | GHash（AES-GCM） | Poly1305（ChaCha20-Poly1305） |
|---|---|---|
| 有限域 | GF(2¹²⁸) | Z/(2¹³⁰ - 5) |
| 運算 | 無進位乘法（carry-less multiply） | 整數乘法 mod 質數 |
| 硬體加速 | PCLMULQDQ（Intel） | 無專用指令（但多項式 mod 很快） |
| 軟體效能 | 慢（無硬體時） | 快 |
| Nonce misuse | Tag 可預測 | Tag 可預測 |

兩者都是 Wegman-Carter 構造，都需要 one-time key。差別在有限域的選擇和硬體支援。

### KMAC（Keccak-based MAC）

NIST SP 800-185 定義的 KMAC 基於 cSHAKE，是 SHA-3 生態的 MAC 標準：

```
KMAC256(K, X, L, S) = cSHAKE256(bytepad(encode_string(K), 168) || X || right_encode(L), L, "KMAC", S)
```

KMAC 的優勢：基於 sponge，天生沒有 length extension；支援可變長度輸出（作為 XOF）；有 domain separation（自訂字串 S）。

---

## 動手練習

1. **手動 HMAC**：不用 `hmac` 模組，用 `hashlib.sha256` 手動實作 HMAC-SHA-256。對照 `hmac.new()` 的結果驗證正確性

2. **Poly1305 key reuse 攻擊**：用同一組 32-byte key 對兩個不同的 message 算 Poly1305 tag，然後手動解出 r（提示：`T₁ - T₂ = h_r(M₁) - h_r(M₂)` mod 2¹²⁸，對單 block 訊息 `h_r(M) = c·r mod p` → `T₁ - T₂ = (c₁ - c₂)·r mod p`）

3. **HMAC 效能測量**：對 1 KB、10 KB、100 KB、1 MB 的資料，分別測量 HMAC-SHA-256、HMAC-SHA-512、HMAC-SHA3-256 的速度，畫出 throughput 圖

4. **（挑戰）Wegman-Carter 安全實驗**：自己實作一個簡化版的 polynomial MAC（mod 一個小質數如 251），對 1000 個隨機 message 對統計碰撞率，驗證 almost-universal 的性質

---

## 重點整理

1. **MAC 的安全定義是 EUF-CMA**：攻擊者有 oracle 也不能 forge 新的 (message, tag) 對
2. **`H(key || msg)` 不是安全的 MAC**：length extension 讓攻擊者能 forge
3. **HMAC 用雙層 hash 結構**：inner hash（K⊕ipad || M）+ outer hash（K⊕opad || inner），防禦 length extension
4. **HMAC 的安全性不需要碰撞抗性**：Bellare 2006 證明只需要壓縮函式是 PRF
5. **Poly1305 是 Wegman-Carter MAC**：多項式求值 + one-time pad，比 HMAC 快，但 key 必須 one-time
6. **選擇指引**：獨立 MAC 用 HMAC-SHA-256；AEAD 場景用 Poly1305（配 ChaCha20）或 GHash（配 AES-GCM）

---

## 自我檢核

1. EUF-CMA 的三個字母分別是什麼意思？攻擊者的能力和目標是什麼？
2. HMAC 的 ipad 和 opad 分別是什麼值？為什麼需要兩層 hash？
3. 為什麼 HMAC 的安全性不需要底層 hash 的碰撞抗性？
4. Poly1305 的 key 為什麼必須是 one-time？重用會怎樣？
5. Encrypt-then-MAC、MAC-then-Encrypt、Encrypt-and-MAC 哪個最安全？為什麼？

---

## 延伸閱讀

- **RFC 2104**：HMAC 的原始規格
- **Bellare (2006)**："New Proofs for NMAC and HMAC: Security without Collision-Resistance"——HMAC 不需要碰撞抗性的證明
- **Bernstein (2005)**："The Poly1305-AES message-authentication code"——Poly1305 的原始論文
- **RFC 8439**：ChaCha20 和 Poly1305 的完整規格
- **NIST SP 800-185**：KMAC 和 cSHAKE 的規格
- **Serious Cryptography, Ch 7**：MAC 的工程導向介紹

---

## 下一章預告

[Ch 17 — 密碼雜湊與 KDF](./17-password-hashing-kdf.md)：hash 的另一個重要應用——為什麼密碼不能用 SHA-256 直接 hash？PBKDF2、bcrypt、scrypt、Argon2 的設計取捨是什麼？
