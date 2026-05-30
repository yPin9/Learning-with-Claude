# Ch 11 — Block Cipher Modes 與 Padding Oracle Attack

> 目標：理解 ECB / CBC / CTR 三種 block cipher mode 的差異與適用場景，掌握 PKCS#7 padding 的格式，然後從零實作 CBC Padding Oracle Attack（Vaudenay 2002）解密不知道 key 的密文。

## 環境

| 工具 | 版本 |
|------|------|
| Python | 3.11+ |
| Ubuntu | 22.04 |
| 套件 | `pip install cryptography flask` |

```bash
pip install cryptography flask
```

## 為什麼 block cipher 不能直接用

AES 是 block cipher，每次只加密 **16 bytes**。現實中的資料幾乎都超過 16 bytes。

「把資料切成 16-byte blocks 各自加密不就好了？」

那就是 ECB mode — 也是最不安全的 mode。看看為什麼。

## 先建立直覺：ECB Penguin

把一張 BMP 圖片用 AES-ECB 加密：

```
原始圖片：          ECB 加密後：        CBC 加密後：
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  🐧          │    │  🐧模糊但   │    │             │
│  Linux      │    │  輪廓清晰   │    │  完全隨機   │
│  penguin    │    │  可辨識!    │    │  noise      │
└─────────────┘    └─────────────┘    └─────────────┘
```

ECB 加密後，**相同的 plaintext block 產生相同的 ciphertext block**。圖片中大面積同色的區域被加密後還是同色 — 輪廓清晰可辨。

這是 ECB 的致命缺陷：**它不隱藏 plaintext 的 pattern**。

## 核心概念：三種 Mode（範例一）

### ECB（Electronic Codebook）

```
P₁ ──→ [AES_K] ──→ C₁
P₂ ──→ [AES_K] ──→ C₂
P₃ ──→ [AES_K] ──→ C₃
```

每個 block 獨立加密。

```python
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

def ecb_encrypt(key, plaintext):
    """AES-ECB 加密（需要 plaintext 已經 padded 到 16 的倍數）"""
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(plaintext) + enc.finalize()

key = b'\x00' * 16
# 兩個相同的 block → 兩個相同的 ciphertext block
pt = b'YELLOW SUBMARINE' * 2   # 32 bytes = 2 blocks
ct = ecb_encrypt(key, pt)
print(f"Block 1: {ct[:16].hex()}")
print(f"Block 2: {ct[16:].hex()}")
# 兩者完全相同！
```

**ECB 的問題**：
- 相同 plaintext → 相同 ciphertext（pattern leakage）
- block 可以被攻擊者任意重排、刪除、複製（malleability）
- **唯一合理的用途**：加密單一 block（例如 key wrapping）

### CBC（Cipher Block Chaining）

```
IV ──┐
     ⊕←── P₁ ──→ [AES_K] ──→ C₁ ──┐
                                     ⊕←── P₂ ──→ [AES_K] ──→ C₂ ──┐
                                                                     ⊕←── P₃ ──→ [AES_K] ──→ C₃
```

加密：C_i = AES_K(P_i ⊕ C_{i-1})，C₀ = IV

解密：P_i = AES_K⁻¹(C_i) ⊕ C_{i-1}

```python
import os

def cbc_encrypt(key, plaintext, iv=None):
    """AES-CBC 加密"""
    if iv is None:
        iv = os.urandom(16)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    ct = enc.update(plaintext) + enc.finalize()
    return iv + ct   # IV 通常 prepend 到 ciphertext

key = b'\x00' * 16
pt = b'YELLOW SUBMARINE' * 2
ct1 = cbc_encrypt(key, pt)
ct2 = cbc_encrypt(key, pt)
print(ct1.hex() != ct2.hex())  # True — 每次 IV 不同，ciphertext 不同
```

**CBC 的性質**：
- 相同 plaintext + 不同 IV → 不同 ciphertext（OK）
- 加密必須序列化（C_{i-1} 算出來才能算 C_i）→ 不能平行
- 解密可以平行（每個 block 獨立 AES decrypt 後 XOR 前一個 ciphertext block）
- **需要 padding**（plaintext 長度必須是 16 的倍數）

### CTR（Counter）

```
Nonce||Counter₁ ──→ [AES_K] ──→ Keystream₁ ──⊕── P₁ ──→ C₁
Nonce||Counter₂ ──→ [AES_K] ──→ Keystream₂ ──⊕── P₂ ──→ C₂
Nonce||Counter₃ ──→ [AES_K] ──→ Keystream₃ ──⊕── P₃ ──→ C₃
```

AES 加密的不是 plaintext 而是 counter，產生 keystream，再 XOR plaintext。

```python
def ctr_encrypt(key, plaintext, nonce=None):
    """AES-CTR 加密"""
    if nonce is None:
        nonce = os.urandom(16)
    cipher = Cipher(algorithms.AES(key), modes.CTR(nonce))
    enc = cipher.encryptor()
    ct = enc.update(plaintext) + enc.finalize()
    return nonce + ct

key = b'\x00' * 16
pt = b'Hello, this is not aligned to 16 bytes!'  # 任意長度
ct = ctr_encrypt(key, pt)
print(f"Plaintext:  {len(pt)} bytes")
print(f"Ciphertext: {len(ct) - 16} bytes (+ 16 bytes nonce)")
# Ciphertext 和 plaintext 等長 — 不需要 padding
```

**CTR 的性質**：
- **不需要 padding**（keystream XOR plaintext，任意長度）
- **可以平行**（每個 counter 獨立加密）
- **可以 random access**（要解密第 i 個 block，直接算 counter = i）
- **只用 AES encrypt**（不需要 AES decrypt — 解密也是 XOR keystream）
- **nonce 不能重複**（reuse nonce = 災難，下面的踩雷會講）

## 底層機制：PKCS#7 Padding

CBC 要求 plaintext 是 16 的倍數。如果不是，要 padding。

**PKCS#7 規則**：如果需要 pad n bytes（1 ≤ n ≤ 16），就填 n 個值為 n 的 byte。

```
原始:     b'HELLO'           (5 bytes, 需要 pad 11)
Padded:   b'HELLO\x0b\x0b\x0b\x0b\x0b\x0b\x0b\x0b\x0b\x0b\x0b'

原始:     b'YELLOW SUBMARIN' (15 bytes, 需要 pad 1)
Padded:   b'YELLOW SUBMARIN\x01'

原始:     b'YELLOW SUBMARINE' (16 bytes, 需要 pad 16!)
Padded:   b'YELLOW SUBMARINE\x10\x10...\x10'  (多一整個 block)
```

**注意最後一個**：即使 plaintext 剛好是 16 的倍數，也要加一整個 padding block。否則解密時無法區分「最後一 byte 是資料」和「最後一 byte 是 padding」。

```python
def pkcs7_pad(data, block_size=16):
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)

def pkcs7_unpad(data, block_size=16):
    pad_len = data[-1]
    if pad_len < 1 or pad_len > block_size:
        raise ValueError("Invalid padding")
    if data[-pad_len:] != bytes([pad_len] * pad_len):
        raise ValueError("Invalid padding")
    return data[:-pad_len]
```

## Padding Oracle Attack（Vaudenay 2002）

這是本章的重頭戲。

### 情境

你攔截到一段 CBC 加密的密文（IV + ciphertext），你**不知道 key**。但你能把修改過的密文送給 server，server 會告訴你 **padding 是否 valid**。

```
┌──────────┐                    ┌──────────┐
│ Attacker │ ── modified CT ──→ │  Server  │
│          │ ←── "padding OK" ──│  (oracle) │
│          │ ←── "padding BAD" ─│          │
└──────────┘                    └──────────┘
```

**這個 "padding valid/invalid" 的回應就是 oracle。** 有了它，你可以逐 byte 解密整段密文，完全不需要 key。

### 攻擊的數學基礎

CBC 解密：

```
P[i] = AES_K⁻¹(C[i]) ⊕ C[i-1]
```

定義中間值 I[i] = AES_K⁻¹(C[i])（AES decrypt 後、XOR 前的值）：

```
P[i] = I[i] ⊕ C[i-1]
```

**攻擊者不知道 I[i]，但可以控制 C[i-1]。** 如果攻擊者把 C[i-1] 改成 C'[i-1]，server 會解出：

```
P'[i] = I[i] ⊕ C'[i-1]
```

攻擊者的目標：**找出 I[i] 的每一個 byte**。知道 I[i] 後，P[i] = I[i] ⊕ C[i-1]（用原始的 C[i-1]）。

### 攻擊第一個 byte

假設目標 block 是 C[1]（前面是 C[0]）。攻擊者想找 I[1][15]（最後一個 byte）。

攻擊者嘗試所有 256 個 C'[0][15] 值，其他 byte 保持不變，送給 server：

```
C'[0] = C[0][0:15] || guess_byte
C[1]  = C[1]（不變）
```

Server 解密後得到 P'[1]，然後檢查 padding。

如果 P'[1][15] = 0x01，padding valid（因為 0x01 是合法的 1-byte padding）。

```
P'[1][15] = I[1][15] ⊕ C'[0][15] = 0x01
→  I[1][15] = 0x01 ⊕ C'[0][15]
```

找到！

### 攻擊圖解

```
目標：解密 C[1] 的最後一 byte

                C'[0]                    C[1]
     ┌──┬──┬──┬──┬──┬──┬──┬──┐    ┌──┬──┬──┬──┐
     │  │  │  │  │  │  │  │??│    │  │  │  │  │
     └──┴──┴──┴──┴──┴──┴──┴─┬┘    └──┴──┴──┴──┘
                             │          │
                             │    ┌─────┴─────┐
                             │    │ AES⁻¹(K)  │
                             │    └─────┬─────┘
                             │          │
                             │    I[1][15] ← 未知！
                             │          │
                             ⊕──────────┘
                             │
                       P'[1][15]
                             │
                    希望這等於 0x01
                    （valid padding）

攻擊者從 0x00 試到 0xFF：
  C'[0][15] = 0x00 → server: "BAD padding"
  C'[0][15] = 0x01 → server: "BAD padding"
  ...
  C'[0][15] = 0x3C → server: "GOOD padding!"  ← 找到了！

→ I[1][15] = 0x01 ⊕ 0x3C = 0x3D
→ P[1][15] = 0x3D ⊕ C[0][15]  (用原始 C[0][15])
```

### 攻擊第二個 byte

已知 I[1][15]。現在要找 I[1][14]。

設定 C'[0][15] 使 P'[1][15] = 0x02（因為我們要讓最後兩 bytes 是 `\x02\x02` — valid 2-byte padding）：

```
C'[0][15] = I[1][15] ⊕ 0x02    （已知值，固定）
```

然後嘗試所有 256 個 C'[0][14]，找到讓 server 回 "valid" 的：

```
P'[1][14] = I[1][14] ⊕ C'[0][14] = 0x02
→  I[1][14] = 0x02 ⊕ C'[0][14]
```

### 以此類推

第 k 個 byte（從最後往前數）：

1. 設定 C'[0][16-k+1 .. 15] 使已解出的位置都等於 `0x(k)`
2. 嘗試 256 個 C'[0][16-k]
3. 找到 valid 的那個 → 得到 I[1][16-k]

**每個 byte 最多 256 次嘗試，16 bytes = 最多 4096 次。** 加密了 N 個 block 就最多 4096×N 次。

### Python PoC

```python
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding

# ─── 受害 Server（有 padding oracle 的漏洞） ───
class VulnerableServer:
    def __init__(self):
        self.key = os.urandom(16)
    
    def encrypt(self, plaintext):
        """加密並回傳 IV + ciphertext"""
        padder = padding.PKCS7(128).padder()
        padded = padder.update(plaintext) + padder.finalize()
        iv = os.urandom(16)
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(iv))
        enc = cipher.encryptor()
        ct = enc.update(padded) + enc.finalize()
        return iv + ct
    
    def is_padding_valid(self, data):
        """解密並回傳 padding 是否 valid — 這就是 oracle！"""
        iv, ct = data[:16], data[16:]
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(iv))
        dec = cipher.decryptor()
        try:
            pt = dec.update(ct) + dec.finalize()
        except Exception:
            return False
        # 檢查 PKCS7 padding
        pad_len = pt[-1]
        if pad_len < 1 or pad_len > 16:
            return False
        return all(pt[-i] == pad_len for i in range(1, pad_len + 1))


# ─── Padding Oracle Attack ───
def padding_oracle_attack(server, ciphertext):
    """用 padding oracle 解密整段 CBC 密文（不需要 key）"""
    blocks = [ciphertext[i:i+16] for i in range(0, len(ciphertext), 16)]
    # blocks[0] = IV, blocks[1..n] = ciphertext blocks
    
    plaintext = b''
    
    for block_idx in range(1, len(blocks)):
        prev_block = bytearray(blocks[block_idx - 1])
        target_block = blocks[block_idx]
        intermediate = [0] * 16   # I[block_idx]
        
        for byte_pos in range(15, -1, -1):  # 從最後一 byte 開始
            pad_value = 16 - byte_pos        # 要湊的 padding 值
            
            # 設定已解出的 bytes
            crafted = bytearray(16)
            for k in range(byte_pos + 1, 16):
                crafted[k] = intermediate[k] ^ pad_value
            
            found = False
            for guess in range(256):
                crafted[byte_pos] = guess
                # 保留前面的 bytes 不動（設為 0 也行，因為我們不在乎它們的解密結果）
                for k in range(0, byte_pos):
                    crafted[k] = 0  # 無所謂
                
                test_data = bytes(crafted) + target_block
                if server.is_padding_valid(test_data):
                    # 可能是 false positive（例如最後兩 bytes 剛好是 \x02\x02）
                    # 對於最後一個 byte 需要額外驗證
                    if byte_pos == 15:
                        # 修改倒數第二 byte 看是否還 valid
                        verify = bytearray(crafted)
                        verify[14] ^= 0x01
                        if not server.is_padding_valid(bytes(verify) + target_block):
                            continue  # false positive
                    
                    intermediate[byte_pos] = guess ^ pad_value
                    found = True
                    break
            
            if not found:
                raise RuntimeError(f"Failed at block {block_idx}, byte {byte_pos}")
        
        # 用原始的 prev_block 算出 plaintext
        pt_block = bytes([intermediate[i] ^ prev_block[i] for i in range(16)])
        plaintext += pt_block
    
    # 去掉 PKCS7 padding
    pad_len = plaintext[-1]
    return plaintext[:-pad_len]


# ─── 測試 ───
server = VulnerableServer()

secret_message = b'The magic words are Squeamish Ossifrage'
print(f"原始明文: {secret_message}")

ct = server.encrypt(secret_message)
print(f"密文長度: {len(ct)} bytes")

recovered = padding_oracle_attack(server, ct)
print(f"解密結果: {recovered}")
assert recovered == secret_message
print("Padding Oracle Attack: SUCCESS")
```

## 對比與取捨

| | ECB | CBC | CTR |
|---|---|---|---|
| 安全性 | 不安全（pattern leak） | 安全（需要 random IV） | 安全（需要 unique nonce） |
| 平行加密 | 可 | 不可 | 可 |
| 平行解密 | 可 | 可 | 可 |
| Random access | 可 | 不可 | 可 |
| 需要 padding | 是 | 是 | 否 |
| Padding Oracle 風險 | 無（無 padding oracle 因為 pattern 已洩漏更嚴重） | 有 | 無（無 padding） |
| 錯誤傳播 | 1 block | 2 blocks | 1 bit |
| Nonce/IV 要求 | 無 | Random IV | Unique nonce（不需要 random） |
| 2024 推薦 | 永遠不要用 | legacy（用 AES-GCM 取代） | 搭配 MAC（或直接用 GCM） |

**2024 年的最佳實踐**：不要裸用 CBC 或 CTR。用 **AEAD mode**（AES-GCM, ChaCha20-Poly1305）— 這些 mode 內建 MAC，同時提供加密和完整性。Ch 25-28 會詳細講 AEAD。

## 踩雷集錦

### 雷 1：CTR mode 的 nonce reuse

```python
# 災難演示：CTR mode 用相同的 nonce 加密兩段不同的 plaintext
key = b'\x00' * 16
nonce = b'\x00' * 16   # 固定 nonce — 嚴重錯誤！

cipher1 = Cipher(algorithms.AES(key), modes.CTR(nonce))
enc1 = cipher1.encryptor()
ct1 = enc1.update(b'Attack at dawn!!') + enc1.finalize()

cipher2 = Cipher(algorithms.AES(key), modes.CTR(nonce))
enc2 = cipher2.encryptor()
ct2 = enc2.update(b'Attack at dusk!!') + enc2.finalize()

# ct1 XOR ct2 = pt1 XOR pt2（keystream 消掉了！）
xored = bytes(a ^ b for a, b in zip(ct1, ct2))
print(f"ct1 ⊕ ct2 = {xored}")
# 攻擊者知道 pt1 XOR pt2 就能做 crib dragging 解出兩段明文
```

### 雷 2：CBC 的 IV 用固定值

```python
# 錯誤：固定 IV
iv = b'\x00' * 16
ct1 = cbc_encrypt(key, b'same plaintext!!', iv)
ct2 = cbc_encrypt(key, b'same plaintext!!', iv)
assert ct1 == ct2   # 洩漏「兩次加密了相同的 plaintext」

# 正確：每次用 random IV
ct1 = cbc_encrypt(key, b'same plaintext!!')  # random IV
ct2 = cbc_encrypt(key, b'same plaintext!!')  # different random IV
assert ct1 != ct2   # 無法判斷 plaintext 是否相同
```

### 雷 3：把 padding oracle 當成理論攻擊

Padding Oracle Attack 不是學術玩具。真實受害者：

- **ASP.NET（2010, CVE-2010-3332）**：預設的加密 cookie 有 padding oracle → 任意解密 session token
- **POODLE（2014, CVE-2014-3566）**：SSL 3.0 的 CBC padding 檢查不嚴格 → padding oracle
- **Lucky Thirteen（2013）**：TLS 的 CBC padding 檢查有 timing difference → timing-based padding oracle

### 雷 4：「我不回傳 error message 就沒有 oracle」

Padding oracle 不一定是明確的 error message。**任何行為差異**都可以是 oracle：

- HTTP 500 vs HTTP 200
- 回應時間差（timing side-channel）
- 連線是否被重設
- 是否觸發重新認證

Lucky Thirteen 就是用 **timing difference** 做的 padding oracle — server 沒回傳 error，但 padding valid 和 invalid 的處理時間差了幾微秒。

### 雷 5：CBC 密文可以 bit-flip

```
修改 C[i-1] 的 bit j → P[i] 的 bit j 被翻轉
                     → P[i-1] 完全損壞（因為 AES⁻¹ 的雪崩效應）
```

攻擊者可以精準翻轉某一 block 的某一 bit，代價是毀掉前一個 block。如果前一個 block 的內容不重要（例如 HTTP header 的空白部分），攻擊者就能無損修改目標 block。

**這就是為什麼 CBC 需要搭配 MAC（Message Authentication Code）** — 光加密不夠，還需要完整性保護。

## 進階：GCM = CTR + GHASH

AES-GCM（Galois/Counter Mode）= CTR mode 加密 + GHASH（GF(2¹²⁸) 多項式 hash）認證。同時解決 padding oracle（沒有 padding）和 bit-flip（有 MAC 保護）。Ch 25 完整講 GCM。

## POODLE 攻擊（2014）

SSL 3.0 的 CBC padding 只檢查最後一個 byte 等於 padding 長度，**不檢查前面的 padding bytes 是否一致**（TLS 會檢查全部）。Google 的 Bodo Möller 利用這個差異發表 POODLE（Padding Oracle On Downgraded Legacy Encryption），直接導致 SSL 3.0 被全面禁用。

## 動手練習

1. **ECB penguin 重現**：找一張 BMP 圖片，用 AES-ECB 加密 pixel data（保留 BMP header），用圖片檢視器打開加密後的檔案，觀察 pattern leakage。

2. **CBC bit-flip**：加密 `admin=0;username=guest`，然後修改 ciphertext 讓解密結果的 `admin=0` 變成 `admin=1`（提示：翻轉前一個 block 對應位置的 bit）。

3. **Padding Oracle 效能**：測量攻擊 100 bytes 明文需要多少次 oracle query。理論值是多少？

4. **CTR nonce reuse 攻擊**：給你兩段用相同 nonce 加密的 CTR ciphertext 和其中一段的 plaintext，還原另一段的 plaintext。

## 重點整理

- ECB：每 block 獨立 → pattern leakage → 永遠不要用
- CBC：chain 前一個 ciphertext → 需要 random IV → 有 padding oracle 風險
- CTR：counter 產生 keystream → 可平行、無 padding → nonce 絕對不能重複
- PKCS#7 padding：pad n bytes 就填 n 個 `\x(n)`；即使對齊也要加一整個 block
- Padding Oracle Attack：利用 server 的 padding valid/invalid 回應，逐 byte 解密密文，每 byte 最多 256 次嘗試
- 2024 年最佳實踐：用 AEAD（AES-GCM / ChaCha20-Poly1305），不要裸用 CBC/CTR

## 自我檢核

- [ ] 能畫出 ECB / CBC / CTR 三種 mode 的加密和解密流程圖
- [ ] 能解釋為什麼 ECB 不安全
- [ ] 能手寫 PKCS#7 pad/unpad
- [ ] 能完整描述 Padding Oracle Attack 的攻擊流程（oracle 定義、逐 byte 猜測、intermediate value 推導）
- [ ] 能用 Python 實作 padding oracle attack 解密不知道 key 的密文

## 延伸閱讀

- **Vaudenay 2002 "Security Flaws Induced by CBC Padding"**（EUROCRYPT 2002）：原始論文
- **POODLE (2014)**：Bodo Möller et al., "This POODLE Bites: Exploiting the SSL 3.0 Fallback"
- **Lucky Thirteen (2013)**：AlFardan & Paterson, "Lucky Thirteen: Breaking the TLS and DTLS Record Protocols"
- **NIST SP 800-38A**：Block cipher modes of operation 標準

---

> **下一章**：[Ch 12 — Stream Ciphers：RC4 的興衰與 ChaCha20](12-stream-ciphers.md) — 離開 block cipher 的世界，看另一種加密方式：直接產生 keystream 去 XOR plaintext。
