# Ch 25 — AEAD 概念：同時保密與驗真

> **目標**：理解 AEAD（Authenticated Encryption with Associated Data）解決的問題——同時保證 confidentiality + integrity + authenticity，能解釋 Encrypt-then-MAC 為什麼比 MAC-then-Encrypt 更安全。

## 為什麼需要這個？

到目前為止，你學了兩種獨立的工具：

- **加密**（Ch 11）：AES-CBC、AES-CTR 讓攻擊者看不懂 plaintext
- **MAC**（Ch 16）：HMAC、Poly1305 讓攻擊者無法篡改訊息

問題來了：你需要同時用這兩個工具，但**組合的順序會決定你是否安全**。

歷史反覆證明，工程師在手動組合 encrypt + MAC 時會出錯。TLS 1.0 用了 MAC-then-Encrypt（MtE），結果被 Padding Oracle（Ch 11 學過的那個攻擊）打穿了好幾年。SSH 早期用了 Encrypt-and-MAC（E&M），MAC 直接算在 plaintext 上，洩漏了明文資訊。

AEAD 把「加密 + 驗證」封裝成一個原語（primitive），讓你不需要自己選組合順序——用對 API 就安全，用錯 API 就報錯。這就是為什麼現代密碼學（TLS 1.3、WireGuard、Signal）全面轉向 AEAD。

## 先建立直覺

想像你寄一個包裹：

```
場景一：只加密（unauthenticated encryption）
  你把信放進不透明的箱子，鎖上
  → 郵差看不懂內容 ✓
  → 但郵差可以偷偷打開箱子、改幾個字、再鎖回去
  → 你收到時完全不知道內容被動過 ✗

場景二：只 MAC（no encryption）
  你把信放進透明的箱子，加上防偽封條
  → 任何人看得到內容 ✗
  → 但如果有人改了內容，封條會破損，你知道被動過 ✓

場景三：AEAD
  你把信放進不透明的箱子，鎖上，外面加上防偽封條
  → 郵差看不懂內容 ✓
  → 如果有人動過箱子，封條會破損 ✓
  → 而且封條還保護箱子外面寫的收件地址（Associated Data）✓
```

Associated Data（AD）是那些「不需要加密但需要驗證」的資料——比如封包的 header（路由資訊必須明文才能轉發，但不能被篡改）。

## 核心概念：unauthenticated encryption 有多危險

### Ciphertext malleability：CTR mode 的 bit-flip 攻擊

AES-CTR 是 stream cipher 的結構：`ciphertext = plaintext ⊕ keystream`。

這個 XOR 結構有一個致命特性：攻擊者不需要知道 key，只需要翻轉 ciphertext 的某個 bit，plaintext 對應的 bit 就會跟著翻轉。

```
原始：  plaintext  = "amount=100"
        keystream  = K₁K₂K₃K₄K₅K₆K₇K₈K₉K₁₀
        ciphertext = plaintext ⊕ keystream

攻擊者翻轉 ciphertext 的第 8 個 byte：
        ciphertext' = ciphertext ⊕ (0x00...0x30...0x00)
                                         ↑ 第 8 byte

解密後：plaintext' = ciphertext' ⊕ keystream
                   = (plaintext ⊕ keystream ⊕ delta) ⊕ keystream
                   = plaintext ⊕ delta
                   = "amount=\x00" ... 被修改了！
```

攻擊者不知道 plaintext 是什麼，但如果知道**格式**（比如 `amount=` 後面是數字），就能精確控制修改。

### 範例一：CTR mode bit-flip 攻擊

```python
"""
示範 AES-CTR 的 bit-flip attack：
攻擊者不知道 key，但能修改 ciphertext 讓 plaintext 改變
"""
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# === 合法的加解密 ===
key = os.urandom(16)
nonce = os.urandom(16)  # CTR mode 的 nonce

plaintext = b"transfer:amt=0100,to=alice"
print(f"原始明文: {plaintext}")

# 加密
cipher = Cipher(algorithms.AES(key), modes.CTR(nonce))
encryptor = cipher.encryptor()
ciphertext = encryptor.update(plaintext) + encryptor.finalize()
print(f"密文 (hex): {ciphertext.hex()}")

# === 攻擊者的操作（不需要知道 key）===
# 攻擊者知道 plaintext 格式是 "transfer:amt=XXXX,to=..."
# 想把 amt=0100 改成 amt=9999
# "0100" 的 ASCII: 0x30 0x31 0x30 0x30
# "9999" 的 ASCII: 0x39 0x39 0x39 0x39
# XOR delta:       0x09 0x08 0x09 0x09

# amt= 在 offset 13 開始，數字在 offset 17-20
ct_array = bytearray(ciphertext)
ct_array[13] ^= (ord('0') ^ ord('9'))  # '0' → '9'
ct_array[14] ^= (ord('1') ^ ord('9'))  # '1' → '9'
ct_array[15] ^= (ord('0') ^ ord('9'))  # '0' → '9'
ct_array[16] ^= (ord('0') ^ ord('9'))  # '0' → '9'
tampered_ciphertext = bytes(ct_array)

# === 接收者解密（完全不知道被改過）===
cipher2 = Cipher(algorithms.AES(key), modes.CTR(nonce))
decryptor = cipher2.decryptor()
tampered_plaintext = decryptor.update(tampered_ciphertext) + decryptor.finalize()
print(f"篡改後明文: {tampered_plaintext}")
# 輸出: transfer:amt=9999,to=alice  ← 金額從 100 變成 9999！

# 沒有任何機制告訴接收者 ciphertext 被動過
```

這就是 unauthenticated encryption 的核心問題：**加密保證了 confidentiality，但沒有保證 integrity。**

## 底層機制：三種 Encrypt + MAC 組合

歷史上，工程師嘗試了三種組合 encrypt 和 MAC 的方式。

### 三種組合模式

```
┌────────────────────────────────────────────────────────────────┐
│  Encrypt-and-MAC (E&M)  —— SSH 最初版本                       │
│                                                                │
│  plaintext ─┬─→ [ Encrypt ] ──→ ciphertext ─┐                │
│             │                                 ├─→ 傳送         │
│             └─→ [  MAC   ] ──→ tag ──────────┘                │
│                                                                │
│  問題：MAC(plaintext) 可能洩漏明文資訊                          │
│  （MAC 不保證 hide plaintext — 只保證 integrity）               │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  MAC-then-Encrypt (MtE)  —— TLS 1.0 / SSL 3.0                │
│                                                                │
│  plaintext ─→ [ MAC ] ─→ plaintext || tag ─→ [ Encrypt ] ─→  │
│                                               ciphertext       │
│                                                                │
│  問題：必須先解密才能驗證 MAC                                    │
│  → 解密過程暴露 padding → Padding Oracle Attack                │
│  （Vaudenay 2002、BEAST、Lucky 13、POODLE）                    │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│  Encrypt-then-MAC (EtM)  —— IPsec ESP / 現代最佳實踐           │
│                                                                │
│  plaintext ─→ [ Encrypt ] ─→ ciphertext ─→ [ MAC ] ─→ tag    │
│                                                                │
│  傳送 (ciphertext, tag)                                        │
│                                                                │
│  驗證流程：                                                     │
│  1. 先驗 MAC(ciphertext) → 失敗就丟棄，不解密                   │
│  2. MAC 通過 → 再解密                                          │
│  → 永遠不會對「被篡改的 ciphertext」執行解密                     │
│  → Padding Oracle 無從下手                                     │
└────────────────────────────────────────────────────────────────┘
```

### 為什麼 Encrypt-then-MAC 勝出

EtM 的安全優勢在於一個原則：**先驗證，再解密**。

1. MAC 算在 ciphertext 上 → 攻擊者任何修改都會被 MAC 擋住
2. 解密只在 MAC 驗證通過後才執行 → 解密過程的 side-channel（如 padding error）永遠不會暴露給攻擊者
3. MAC 不接觸 plaintext → 不會洩漏明文資訊

MtE 的致命問題：系統必須先解密才能驗證 MAC。如果解密過程會因為 invalid padding 產生不同的錯誤訊息或時間差，攻擊者就能利用這個 oracle 逐 byte 還原 plaintext。這正是 Ch 11 的 Padding Oracle Attack 的原理。

### IND-CCA2 安全定義

密碼學家用 IND-CCA2（Indistinguishability under Adaptive Chosen-Ciphertext Attack）來定義「足夠安全」的加密：

```
IND-CCA2 遊戲：
1. 攻擊者可以讓 oracle 解密任何 ciphertext（adaptive chosen-ciphertext）
2. 攻擊者提交兩個明文 m₀, m₁
3. Oracle 隨機選一個加密，把 ciphertext c* 給攻擊者
4. 攻擊者可以繼續讓 oracle 解密任何 ciphertext（但不能問 c*）
5. 攻擊者猜 c* 加密的是 m₀ 還是 m₁

如果攻擊者的猜對機率只比 50% 好了 negligible amount → IND-CCA2 安全
```

- AES-CTR alone：不是 IND-CCA2（bit-flip attack 就是 CCA）
- AES-CBC alone：不是 IND-CCA2（Padding Oracle 就是 CCA）
- Encrypt-then-MAC（用 secure encryption + secure MAC）：是 IND-CCA2
- AEAD（AES-GCM、ChaCha20-Poly1305）：設計上就是 IND-CCA2

## 進一步用法：AEAD 的 Associated Data

AEAD 不只是「Authenticated Encryption」，還多了「with Associated Data」。

AD（Associated Data）是那些**不加密但需要驗證完整性**的資料。典型用途：

```
網路封包的結構：
┌──────────────────────────────────────────────┐
│  Header（明文，路由需要讀）                     │
│  - source IP                                  │
│  - destination IP                             │
│  - sequence number                            │
│  - protocol version                           │
├──────────────────────────────────────────────┤
│  Payload（加密）                               │
│  - 實際的應用層資料                             │
└──────────────────────────────────────────────┘

AEAD 的保護範圍：
  - Header  → AD：不加密，但 tag 會驗證它沒被改過
  - Payload → 加密 + 驗證
  - Tag     → 覆蓋 AD + ciphertext
```

如果沒有 AD，攻擊者可以：
- 修改 header 的 destination IP → 把封包導到自己的 server
- 修改 sequence number → 造成封包重排（replay / reorder attack）
- 修改 protocol version → 降級到不安全的版本（downgrade attack）

### 範例二：AES-GCM 的 AD 與 tamper detection

```python
"""
用 AES-GCM 展示 Associated Data + tamper detection
"""
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# 產生 key 和 nonce
key = AESGCM.generate_key(bit_length=256)
aesgcm = AESGCM(key)
nonce = os.urandom(12)  # GCM 推薦 96-bit nonce

# Associated Data：封包 header（明文但需要驗證）
header = b"v=TLS1.3|seq=00000001|type=application_data"

# Plaintext：實際資料
plaintext = b'{"user":"alice","action":"transfer","amount":1000}'

# 加密：AEAD 一次搞定 encryption + authentication
ciphertext = aesgcm.encrypt(nonce, plaintext, header)
print(f"Header (AD): {header}")
print(f"Ciphertext:  {ciphertext.hex()[:64]}...")
print(f"  (包含 {len(plaintext)} bytes 密文 + 16 bytes tag)")

# 正常解密
decrypted = aesgcm.decrypt(nonce, ciphertext, header)
print(f"\n正常解密: {decrypted}")

# === 攻擊一：篡改 ciphertext ===
tampered_ct = bytearray(ciphertext)
tampered_ct[0] ^= 0x01  # 翻轉一個 bit
try:
    aesgcm.decrypt(nonce, bytes(tampered_ct), header)
    print("不應該到這裡")
except Exception as e:
    print(f"\n篡改 ciphertext → 解密失敗: {e}")

# === 攻擊二：篡改 Associated Data ===
tampered_header = b"v=TLS1.3|seq=00000001|type=alert"  # 改 type
try:
    aesgcm.decrypt(nonce, ciphertext, tampered_header)
    print("不應該到這裡")
except Exception as e:
    print(f"篡改 AD → 解密失敗: {e}")

# === 攻擊三：AD 對不上（用不同的 AD 解密）===
try:
    aesgcm.decrypt(nonce, ciphertext, b"")  # 空 AD
    print("不應該到這裡")
except Exception as e:
    print(f"AD 不匹配 → 解密失敗: {e}")

print("\n結論：ciphertext、AD、nonce 任何一個被改過，解密都會失敗")
```

注意 API 的簡潔性：`encrypt(nonce, plaintext, ad)` 和 `decrypt(nonce, ciphertext, ad)`。沒有「先 encrypt 再 MAC」的手動步驟，不需要選組合順序——AEAD 原語把正確的組合封裝好了。

## 對比與取捨

| 特性 | E&M (SSH) | MtE (TLS 1.0) | EtM (IPsec) | AEAD (GCM/ChaCha20) |
|---|---|---|---|---|
| MAC 輸入 | plaintext | plaintext | ciphertext | ciphertext + AD |
| 先驗後解 | 否 | 否 | 是 | 是 |
| Padding Oracle 風險 | N/A | **高** | 無 | 無 |
| 洩漏 plaintext 資訊 | **可能** | 不會 | 不會 | 不會 |
| IND-CCA2 | 不保證 | 不保證 | **是**（正確實作下）| **是**（設計保證）|
| 保護 header/metadata | 需額外處理 | 需額外處理 | 需額外處理 | **內建 AD** |
| 現代推薦 | ✗ | ✗ | ✓（但不如 AEAD） | ✓✓✓ |

## 踩雷集錦

1. **「我用 AES-CBC + HMAC 就等於 AEAD」**：只有 Encrypt-then-MAC 順序才安全，而且 HMAC 必須算在 `IV || ciphertext` 上（包含 IV），而且 encryption key 和 MAC key 必須獨立。做錯任何一步就不安全。AEAD 幫你避免所有這些陷阱。

2. **「AD 可以省略」**：技術上可以傳空的 AD，但如果你的 protocol 有 header/metadata 需要完整性保護，省略 AD 就等於讓攻擊者自由修改這些欄位。TLS 1.3 的 AD 包含 record type、protocol version、record length——少了任何一個都開洞。

3. **「AEAD 保證 plaintext integrity」**：AEAD 保證的是「密文沒被篡改」。如果 sender 本身送出錯誤的 plaintext，AEAD 不會阻止。Authentication 是「驗證密文來自持有 key 的人」，不是「驗證 plaintext 的語意正確」。

4. **「加密後再加 CRC 就有 integrity」**：CRC 不是密碼學 MAC——攻擊者可以同時修改 ciphertext 和 CRC 讓它們一致。WEP（802.11 的前身）就是這樣被打爆的：用 CRC-32 當 integrity check，攻擊者能任意修改封包。

5. **「nonce 是 AEAD 的一部分，我不需要管它」**：AEAD API 需要你提供 nonce，而且 nonce 的正確使用（不重複、不可預測，視演算法而定）是你的責任。GCM 的 nonce reuse 是致命的——Ch 26 會詳細展示。

## 進階：再往深一層

### AEAD 的形式化定義

一個 AEAD scheme 由三個演算法組成：

```
KeyGen() → K
Encrypt(K, nonce, plaintext, AD) → ciphertext || tag
Decrypt(K, nonce, ciphertext || tag, AD) → plaintext 或 ⊥ (reject)
```

安全要求：
- **Confidentiality（IND-CPA）**：ciphertext 不洩漏 plaintext 的任何資訊
- **Integrity（INT-CTXT）**：攻擊者無法偽造能通過 Decrypt 的 (ciphertext, tag) pair
- **IND-CCA2**：上述兩者的組合——即使攻擊者能要求 oracle 解密任意 ciphertext，也無法區分兩個 plaintext 的加密結果

### Misuse-resistant AEAD

標準 AEAD（AES-GCM、ChaCha20-Poly1305）在 nonce reuse 時完全崩塌——不只洩漏 plaintext，還可能洩漏 authentication key。

Nonce-misuse-resistant AEAD（如 AES-GCM-SIV）的安全性退化得更優雅：nonce reuse 只洩漏「兩次加密的 plaintext 是否相同」，不洩漏 plaintext 內容。Ch 27 會詳細比較。

### AEAD 在真實 protocol 中的角色

| Protocol | AEAD 用法 | AD 內容 |
|---|---|---|
| TLS 1.3 | AES-GCM 或 ChaCha20-Poly1305 | record type + version + length |
| WireGuard | ChaCha20-Poly1305 | 空（header 另有 MAC）|
| Signal | AES-CBC + HMAC（EtM 變體）→ 逐步遷移 AEAD | message metadata |
| SSH (modern) | AES-GCM 或 ChaCha20-Poly1305 | packet length |

## 動手練習

1. **bit-flip attack**：修改範例一的程式，讓攻擊者把 `to=alice` 改成 `to=bobby`（提示：`alice` 和 `bobby` 長度不同——你需要思考這會造成什麼問題）

2. **EtM 手動實作**：用 `cryptography` library 手動實作 Encrypt-then-MAC：AES-CTR 加密 → HMAC-SHA256 算在 `nonce || ciphertext` 上。驗證時先驗 MAC 再解密。對比你的 code 和 AESGCM 的 API——哪個更容易寫錯？

3. **AD 的重要性**：修改範例二的程式，模擬 downgrade attack：攻擊者把 AD 中的 `v=TLS1.3` 改成 `v=TLS1.0`。觀察 AEAD 如何阻止這個攻擊。

4. **比較 error message**：寫一個程式分別用 AES-CBC（`cryptography.hazmat`）和 AES-GCM 解密被篡改的 ciphertext。觀察兩者的錯誤訊息差異——CBC 會告訴你 padding error（oracle！），GCM 只告訴你 authentication failed。

## 本章重點整理

- Unauthenticated encryption（AES-CTR / AES-CBC alone）無法防止 ciphertext 被篡改——bit-flip attack 能精確控制 plaintext 的修改
- 三種 Encrypt + MAC 組合中，**Encrypt-then-MAC** 最安全，因為驗證在解密之前，阻斷了 Padding Oracle 等 side-channel
- AEAD 把正確的 encrypt + authenticate 封裝成單一原語，消除手動組合出錯的風險
- Associated Data（AD）保護那些「不需要加密但需要驗證」的 metadata——忽略 AD 會開啟 downgrade、reorder 等攻擊面

## 自我檢核

- [ ] 能解釋 CTR mode bit-flip attack 的原理（XOR 的可交換性）
- [ ] 能畫出 E&M、MtE、EtM 三種組合的資料流，並說出各自的弱點
- [ ] 能說出 MtE 導致 Padding Oracle 的因果鏈
- [ ] 能解釋 IND-CCA2 的遊戲定義（用自己的話）
- [ ] 能舉出 Associated Data 的兩個實際用途

## 延伸閱讀

- **Hugo Krawczyk, "The Order of Encryption and Authentication for Protecting Communications" (2001)**
  - **讀哪裡**：全文，重點在 Section 3 的三種組合比較
  - **學什麼**：EtM 安全性的形式化證明——為什麼 MtE 和 E&M 在一般情況下不保證 IND-CCA2
  - **關聯**：本章三種組合的理論基礎

- **Phillip Rogaway, "Authenticated-Encryption with Associated-Data" (2002)**
  - **讀哪裡**：Section 1-3 的 AEAD 形式化定義
  - **學什麼**：AEAD 作為一個 primitive 的原始定義——為什麼把 AD 納入是必要的
  - **關聯**：本章 AEAD 的學術源頭

- **Serge Vaudenay, "Security Flaws Induced by CBC Padding" (2002)**
  - **讀哪裡**：Section 2-4 的攻擊描述
  - **學什麼**：Padding Oracle Attack 的原始論文——MtE 為什麼在 CBC 模式下特別危險
  - **關聯**：本章 MtE 弱點的具體攻擊，與 Ch 11 的 Padding Oracle 呼應

→ [Ch 26 AES-GCM](./26-aes-gcm.md)
