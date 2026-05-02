# Ch 11 — 區塊模式 + padding oracle

> 目標：把 ECB / CBC / CTR / CFB / OFB 五個 mode 講清楚，特別是 ECB 的 penguin image 教訓、CBC 的 IV 與 padding 規範。然後實作 Vaudenay 的 CBC padding oracle 攻擊，看為什麼一個 1-bit error 訊號就能解密整個密文。

## block cipher 處理長訊息的問題

AES 一次只能處理 16-byte block。**訊息超過 16 byte 怎麼辦**？把訊息切 block 各自加密 → 「mode」設計問題。

不同 mode 處理：

1. **block 之間怎麼鏈接**（讓相同 plaintext 不總對應相同 ciphertext）
2. **訊息長度不是 block size 倍數時怎麼 pad**

## ECB：絕對不要用

**Electronic CodeBook**：每 block 獨立加密。

```
P_1 → AES_enc(k, P_1) → C_1
P_2 → AES_enc(k, P_2) → C_2
P_3 → AES_enc(k, P_3) → C_3
```

**問題**：相同 plaintext block 永遠對應相同 ciphertext block。

```python
def ecb_encrypt(pt, key):
    return b''.join(aes128_encrypt(pt[i:i+16], key) for i in range(0, len(pt), 16))
```

最有名的視覺化：**ECB penguin**。把 Linux Tux 圖片用 ECB AES 加密 — 仍能看到企鵝形狀，因為相同 pixel block 加密成相同 ciphertext block。

```python
# 用 ECB 加密一張 BMP 圖片
from PIL import Image
import io
img = Image.open('tux.bmp')
data = img.tobytes()
key = b'YELLOW SUBMARINE'
encrypted = ecb_encrypt(data + b'\x00' * (16 - len(data) % 16), key)
# 把 encrypted 當 raw pixel 重組成 BMP
# 結果仍能看到企鵝
```

**ECB 不滿足 IND-CPA**，違反 Ch 7 的安全定義。**任何狀況都不要用 ECB** — 但工業界仍偶爾踩雷（AWS 早期某個服務、某些舊 Java code 預設 mode 是 ECB）。

## CBC：經典但有坑

**Cipher Block Chaining**：每個 block 加密前先 XOR 上一個 ciphertext block。

```
C_0 = IV   （隨機選，不保密但要不重複）
C_i = AES_enc(k, P_i XOR C_{i-1})
```

```python
def cbc_encrypt(pt, key, iv):
    out = []
    prev = iv
    for i in range(0, len(pt), 16):
        block = pt[i:i+16]
        xored = bytes(a ^ b for a, b in zip(block, prev))
        prev = aes128_encrypt(xored, key)
        out.append(prev)
    return b''.join(out)

def cbc_decrypt(ct, key, iv):
    out = []
    prev = iv
    for i in range(0, len(ct), 16):
        block = ct[i:i+16]
        decrypted = aes128_decrypt(block, key)
        xored = bytes(a ^ b for a, b in zip(decrypted, prev))
        out.append(xored)
        prev = block
    return b''.join(out)
```

CBC 達到 IND-CPA（**前提：IV 真隨機**）。但有幾個問題：

1. **不能平行加密**（每 block 依賴前一個）— 解密可平行
2. **bit-flip 攻擊**：改 C_i 的某 bit → P_{i+1} 同位置 bit 反轉（攻擊者可控制）
3. **Padding oracle**：下面詳述
4. **IV reuse 退化**：同 IV + 同 key 加密相同 plaintext 一樣 → 部分破壞 IND-CPA

## PKCS#7 Padding

CBC 要求 plaintext 是 block size 倍數。**PKCS#7 padding** 規定：

```
若還缺 N 個 byte 才滿 16，補 N 個 0xN
若 plaintext 剛好滿，補一整個 block 全 0x10

例：
plaintext "HELLO" (5 byte) → 補 11 個 0x0B → 16 byte
plaintext 16 byte → 補 16 個 0x10 → 32 byte
```

解密後檢查：

1. 看最後 byte 值 N
2. 確認最後 N 個 byte 都是 N
3. 拿掉這 N 個 byte

**錯誤 padding 應拒絕**。但問題：**怎麼回報錯誤？**

## Padding Oracle Attack

Vaudenay 2002 的經典 attack。場景：

```
client 送 ciphertext 到 server
server 解密、檢查 padding
  padding 對   → 處理訊息（回 200 OK）
  padding 錯   → 回 400 Bad Request
  訊息對但內容錯 → 回 500
```

**只要 server 對「padding 對 / 錯」回不同錯誤訊息**，attacker 能 byte-by-byte 解密任意 ciphertext，**不知道 key**。

### 攻擊原理

CBC 解密一個 block：

```
P_2 = AES_dec(k, C_2) XOR C_1
```

attacker 控制 `C_1`：改 `C_1` 的某個 byte → `P_2` 同位置 byte 改變。

目標：解密最後一個 byte。

```
記 P_2[15] = AES_dec(k, C_2)[15] XOR C_1[15]
attacker 知道 C_1[15], C_2 (固定)
要算 P_2[15]，等同要算 AES_dec(k, C_2)[15]（記為 D）
```

Attacker 構造 `C_1' = C_1 XOR g_15`，發給 server：

```
server 解密看 P_2'[15] = D XOR C_1'[15] = D XOR C_1[15] XOR g_15
```

如果 `g_15` 選對，使 P_2'[15] = 0x01（合法 padding），server 回 OK。試 256 個值找到那個 → 算出 D = P_2[15] XOR g_15 XOR original_C_1[15]。

**256 次嘗試解一個 byte**。一個 16-byte block 共 4096 次嘗試。

### 完整攻擊邏輯

解一個 block 從最後 byte 往前推：

```
解 byte 15:
  fake C_1 的 byte 15 從 0..255 試
  找到讓 server 回 "padding OK" 的值 g_15
  → 對應 padding 0x01
  → 算出 P_2[15]

解 byte 14:
  把 byte 15 設好讓它解出 0x02（已知 D[15]）
  fake C_1 的 byte 14 從 0..255 試
  找到讓 padding 對應 0x02 0x02 的值
  → 算出 P_2[14]

...以此類推到 byte 0
```

整個 block 4096 次嘗試左右（256 × 16）。對 N 個 block 的訊息：4096 × N。

### Vaudenay 的歷史影響

這個 attack 影響：

- **SSL 3.0 / TLS 1.0 / TLS 1.1**：CBC mode + MAC-then-encrypt 結構受影響
- **POODLE 攻擊 (2014)**：對 SSL 3.0 的 padding oracle 變種
- **Lucky Thirteen (2013)**：對 TLS 1.0/1.1 的 timing-based padding oracle
- **CRIME, BREACH**：相關 chosen-plaintext attack

**TLS 1.2 改 encrypt-then-MAC + AEAD 才解決根本問題**。TLS 1.3 砍掉 CBC mode（只保留 AEAD：AES-GCM、ChaCha20-Poly1305、AES-CCM）。

### 完整 padding oracle 攻擊範例

```python
def padding_oracle_attack(ciphertext, oracle):
    """
    ciphertext: IV || C_1 || C_2 || ...
    oracle(ct) -> True if padding OK, else False
    回傳 plaintext
    """
    blocks = [ciphertext[i:i+16] for i in range(0, len(ciphertext), 16)]
    plaintext = b''
    for i in range(1, len(blocks)):
        target = blocks[i]
        prev = blocks[i-1]
        decrypted = decrypt_block(target, prev, oracle)
        plaintext += decrypted
    return plaintext

def decrypt_block(target, prev, oracle):
    """解一個 block"""
    intermediate = bytearray(16)
    for byte_pos in range(15, -1, -1):
        pad_value = 16 - byte_pos
        # 構造 fake prev block
        for guess in range(256):
            fake_prev = bytearray(16)
            # 已解出的 byte：填好讓它們解成 pad_value
            for k in range(byte_pos+1, 16):
                fake_prev[k] = intermediate[k] ^ pad_value
            fake_prev[byte_pos] = guess
            if oracle(bytes(fake_prev) + target):
                intermediate[byte_pos] = guess ^ pad_value
                break
    # plaintext = intermediate XOR original prev
    return bytes(a ^ b for a, b in zip(intermediate, prev))
```

實際攻擊用 multi-thread + fast network：1 秒 ~10000 次 oracle query → 1 block 不到 1 秒。

## CTR：現代首選

**Counter mode**：用 block cipher 當 PRG（生成 keystream）：

```
C_i = P_i XOR AES_enc(k, nonce || counter_i)
```

```python
def ctr_encrypt(pt, key, nonce):
    out = b''
    counter = 0
    for i in range(0, len(pt), 16):
        ks = aes128_encrypt(nonce + counter.to_bytes(8, 'big'), key)
        block = pt[i:i+16]
        out += bytes(a ^ b for a, b in zip(block, ks))
        counter += 1
    return out
```

**優點**：

- **平行加密**（每個 keystream block 獨立）
- **隨機存取**（解密第 N block 不需要前面的）
- **沒 padding 問題**（直接 XOR keystream）
- **加密 = 解密**（自反，硬體簡單）
- **AES-NI 友善**

**唯一致命限制**：**nonce 絕對不能重複**。同 nonce + 同 key → keystream 重複 → two-time pad（Ch 6 的災難）。

CTR 是現代 mode 的基礎。**AES-GCM 就是 CTR + GMAC**（Ch 26）。

## CFB / OFB：歷史殘留

**CFB（Cipher Feedback）** 與 **OFB（Output Feedback）** 是 1970-80 年代設計，現在很少用：

```
CFB: C_i = P_i XOR AES_enc(k, C_{i-1})
OFB: O_i = AES_enc(k, O_{i-1}), C_i = P_i XOR O_i
```

CFB：可變 byte stream（不用 padding）；對 bit error 自我同步。
OFB：keystream 預先算好，但每次必須串行（不能 random access）。

兩者**現代沒明顯優勢**，被 CTR + AEAD 取代。仍出現於遺留系統（早期 SSH、IPSec）。

## XTS：硬碟加密 mode

特殊用途：**全硬碟加密**（FileVault、LUKS、BitLocker）。要求：

- 同 sector 同 plaintext 加密成相同 ciphertext（位置敏感）
- 不能改 sector 大小（不能加 IV / MAC）
- 必須 random-access

XTS-AES 用兩把 key + sector tweak：

```
C = AES_enc(k1, P XOR T) XOR T
T = AES_enc(k2, sector_number) × α^j   （α 是 GF(2¹²⁸) 生成元）
```

不適合普通通訊（沒 authenticity），但對硬碟加密合適。

## Mode 選擇 cheat sheet

| 場景 | 用什麼 |
|---|---|
| 加密訊息（最常見） | **AES-GCM** 或 **ChaCha20-Poly1305**（AEAD） |
| TLS 1.3 | 只 AEAD（GCM / CCM / ChaCha20-Poly1305） |
| 全硬碟加密 | XTS-AES |
| 嵌入式 / 簡單 | CTR + HMAC（自己組 AEAD） |
| Legacy 系統互通 | CBC + HMAC（且記得 encrypt-then-MAC） |
| **不要用** | ECB（永遠錯） |

## 一個常見誤解

「padding oracle 已經被修了，現代系統沒這問題」

部分對。**TLS 1.3 砍掉 CBC** 直接終結 TLS 上的這個 attack。但：

- 自家應用（API token、cookie 加密）仍可能用 CBC
- Web framework 預設 mode 不一定是 AEAD（PHP 早期 `mcrypt` 預設 CBC，Java JCA 預設 ECB）
- 嵌入式系統 / IoT 有大量遺留 CBC 部署

**CTF / pentest 仍經常出現 padding oracle 場景**。學會這個 attack 是 baseline。

## 自我檢核

- [ ] 我能說出 ECB / CBC / CTR / CFB / OFB 五種 mode 的差別
- [ ] 我能解釋為什麼 ECB 不滿足 IND-CPA
- [ ] 我能寫 CBC 與 CTR 的 encrypt / decrypt
- [ ] 我能說出 PKCS#7 padding 規則
- [ ] 我能寫 Vaudenay padding oracle attack 的完整 logic
- [ ] 我知道現代為什麼選 AEAD（GCM / ChaCha20-Poly1305）

下一章看 stream cipher：RC4 興衰史與 ChaCha20 為什麼贏。

→ [Ch 12 Stream cipher](./12-stream-ciphers.md)
