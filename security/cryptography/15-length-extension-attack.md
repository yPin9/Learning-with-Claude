# Ch 15 — Length Extension Attack

> 目標：能解釋 length extension attack 的原理，理解它打的是 Merkle-Damgård 構造的結構弱點，能用 Python 實作 PoC 對 SHA-256 forge 出合法的 hash。

---

## 為什麼需要理解 Length Extension

Ch 13 提到 Merkle-Damgård 構造有一個結構弱點：最終 hash 值就是 internal state。Ch 14 解釋了 SHA-3 的 sponge 構造為什麼天生免疫。這一章要把理論變成攻擊——你會親手 exploit 這個弱點。

這不是紙上談兵。現實中的 length extension 漏洞被打過很多次：

- **Flickr API（2009）**：用 `MD5(secret || params)` 做請求簽章，被 length extension 偽造
- **多個 Web 框架**：用 `SHA1(secret || cookie)` 驗證 cookie 完整性，被 length extension 繞過
- **自製 MAC**：任何用 `H(key || message)` 做 MAC 的系統都有這個漏洞

---

## 先建立直覺

想像一台自動蓋章機：

1. 你把文件放進去，機器從頭讀到尾，蓋一個章（hash）
2. 蓋完的章上記錄了機器讀完文件後的「內部狀態」
3. 攻擊者看到章（hash），就知道機器的內部狀態
4. 攻擊者不需要拿到原始文件，直接把機器「設定到那個狀態」，然後繼續餵新的內容
5. 出來的章是合法的——和機器從頭讀原文件 + 新內容得到的一模一樣

這就是 length extension attack 的全部。

---

## 核心概念：攻擊場景

### 場景設定

Server 用一個 secret key `K` 和 SHA-256 來「簽章」API 請求：

```
token = SHA-256(K || message)
```

Client 發送 `(message, token)` 給 server，server 用自己的 `K` 重新算一次來驗證。

攻擊者知道：
- `message`（API 參數是公開的）
- `token = SHA-256(K || message)`（在 HTTP response 中）
- `len(K)`（或者可以逐一嘗試）

攻擊者不知道：
- `K` 的具體內容

**攻擊目標**：在不知道 `K` 的情況下，算出 `SHA-256(K || message || padding || extension)` 的合法 token。

### 為什麼能做到

回顧 Merkle-Damgård 構造：

```
K || message
    │
    ▼   填充 + 長度
┌────────┬────────┬──────────┐
│ block₁ │ block₂ │ padding  │
└───┬────┴───┬────┴────┬─────┘
    │        │         │
IV─→[f]────→[f]─────→[f]────→ token = SHA-256(K || message)
```

關鍵觀察：

1. `token` 就是最後一個壓縮函式 `f` 的輸出，也就是完整的 internal state
2. 如果攻擊者把 `token` 拆成 8 個 32-bit word，當作新的 IV
3. 從那個 state 開始，繼續 hash `extension` 的內容
4. 效果等同於 hash 了 `K || message || padding || extension`

```
K || message || padding(原始) || extension
    │
    ▼
┌────────┬────────┬──────────┬───────────┬──────────┐
│ block₁ │ block₂ │ orig pad │ extension │ new pad  │
└───┬────┴───┬────┴────┬─────┴─────┬─────┴────┬─────┘
    │        │         │           │           │
IV─→[f]────→[f]─────→[f]────────→[f]────────→[f]──→ forged_token
                       ↑
              這個 state = token
              攻擊者從這裡開始
```

攻擊者不需要知道 `K`——只需要知道 `len(K)` 來正確計算 padding。

---

## 底層機制：SHA-256 Padding 規則

要執行 length extension，攻擊者必須精確重建原始訊息的 padding。

SHA-256 的 padding 規則（Ch 13 快速回顧）：

```
原始訊息: M (長度 L bits)

填充後:
M || 1 || 0...0 || length(64-bit big-endian)
     │     │              │
     │     │              └─ L 的 64-bit 表示
     │     └─ 補零到總長度 ≡ 448 (mod 512)
     └─ 固定加一個 1 bit（0x80 byte）
```

具體例子——`K = b"SECRET"` (6 bytes = 48 bits), `message = b"amount=100"`（10 bytes = 80 bits）：

```
原始資料: b"SECRETamount=100"  (16 bytes = 128 bits)

填充:
  b"SECRETamount=100"                    # 16 bytes 原始資料
  b"\x80"                                # 1 byte: 0x80
  b"\x00" * 39                           # 39 bytes: 補零到 448 bits = 56 bytes
  b"\x00\x00\x00\x00\x00\x00\x00\x80"   # 8 bytes: 128 bits = 0x80 big-endian

填充後總長: 16 + 1 + 39 + 8 = 64 bytes = 512 bits = 1 block
```

攻擊者構造的偽造訊息：

```
forged_message = message || padding || extension
               = b"amount=100" || (padding bytes) || b"&admin=true"
```

Server 收到 `(forged_message, forged_token)`，用 `K` 重算：

```
SHA-256(K || forged_message)
= SHA-256(K || message || padding || extension)
= forged_token  ← 攻擊者算出來的！
```

驗證通過。攻擊成功。

---

## 範例一：對 SHA-256 跑 Length Extension（Python PoC）

我們需要一個能設定 initial state 的 SHA-256 實作。Python 的 `hashlib` 不暴露這個 API，所以我們用純 Python 實作 SHA-256 的關鍵部分。

```python
import struct
import hashlib

# SHA-256 常數
K256 = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]

def rr(x, n):
    """32-bit right rotate"""
    return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF

def sha256_compress(state: list[int], block: bytes) -> list[int]:
    """SHA-256 壓縮函式：吃 8 個 32-bit state word 和一個 64-byte block"""
    assert len(block) == 64
    # Message schedule
    W = list(struct.unpack(">16L", block))
    for i in range(16, 64):
        s0 = rr(W[i-15], 7) ^ rr(W[i-15], 18) ^ (W[i-15] >> 3)
        s1 = rr(W[i-2], 17) ^ rr(W[i-2], 19) ^ (W[i-2] >> 10)
        W.append((W[i-16] + s0 + W[i-7] + s1) & 0xFFFFFFFF)

    a, b, c, d, e, f, g, h = state
    for i in range(64):
        S1 = rr(e, 6) ^ rr(e, 11) ^ rr(e, 25)
        ch = (e & f) ^ (~e & g) & 0xFFFFFFFF
        temp1 = (h + S1 + ch + K256[i] + W[i]) & 0xFFFFFFFF
        S0 = rr(a, 2) ^ rr(a, 13) ^ rr(a, 22)
        maj = (a & b) ^ (a & c) ^ (b & c)
        temp2 = (S0 + maj) & 0xFFFFFFFF

        h = g
        g = f
        f = e
        e = (d + temp1) & 0xFFFFFFFF
        d = c
        c = b
        b = a
        a = (temp1 + temp2) & 0xFFFFFFFF

    return [(s + x) & 0xFFFFFFFF for s, x in
            zip(state, [a, b, c, d, e, f, g, h])]

def sha256_padding(msg_len_bytes: int) -> bytes:
    """計算 SHA-256 的 padding（不含原始訊息）"""
    bit_len = msg_len_bytes * 8
    # 加 0x80，然後補零到 56 mod 64，最後加 8-byte 長度
    padding = b'\x80'
    # 目前總長 = msg_len_bytes + 1 (0x80)
    # 需要總長 ≡ 56 (mod 64)
    pad_zeros = (56 - (msg_len_bytes + 1) % 64) % 64
    padding += b'\x00' * pad_zeros
    padding += struct.pack(">Q", bit_len)
    return padding

def length_extension_attack(
    original_hash: str,    # 已知的 SHA-256(secret || message)
    original_msg: bytes,   # 已知的 message
    secret_len: int,       # 已知（或猜測）的 secret 長度
    extension: bytes       # 攻擊者想附加的資料
) -> tuple[bytes, str]:
    """
    回傳 (forged_message, forged_hash)
    forged_message = original_msg || padding || extension
    forged_hash = SHA-256(secret || forged_message)
    """
    # Step 1: 從 original_hash 提取 internal state
    h_bytes = bytes.fromhex(original_hash)
    state = list(struct.unpack(">8L", h_bytes))

    # Step 2: 計算原始的 padding
    # 原始被 hash 的總長 = secret_len + len(original_msg)
    total_original_len = secret_len + len(original_msg)
    glue_padding = sha256_padding(total_original_len)

    # Step 3: 計算 extension 的 hash
    # 到目前為止「已被 hash 的總長度」
    hashed_so_far = total_original_len + len(glue_padding)

    # 對 extension 做 padding
    ext_padded = extension + sha256_padding(hashed_so_far + len(extension))

    # 把 extension 分成 64-byte blocks，用已知的 state 繼續壓縮
    for i in range(0, len(ext_padded), 64):
        block = ext_padded[i:i+64]
        state = sha256_compress(state, block)

    forged_hash = ''.join(f'{x:08x}' for x in state)
    forged_message = original_msg + glue_padding + extension

    return forged_message, forged_hash


# ========== 攻擊示範 ==========

secret = b"MYSECRETKEY"  # Server 的 secret（攻擊者不知道）
message = b"amount=100&to=alice"
extension = b"&admin=true"

# Server 計算的合法 token
original_token = hashlib.sha256(secret + message).hexdigest()
print(f"[Server] 原始 token: {original_token}")
print(f"[Server] 原始 message: {message}")

# 攻擊者執行 length extension
forged_msg, forged_token = length_extension_attack(
    original_hash=original_token,
    original_msg=message,
    secret_len=len(secret),  # 攻擊者知道或猜測這個值
    extension=extension
)

print(f"\n[Attacker] 偽造 message: {forged_msg}")
print(f"[Attacker] 偽造 token:   {forged_token}")

# Server 驗證：用 secret 重算
verify_token = hashlib.sha256(secret + forged_msg).hexdigest()
print(f"\n[Server] 驗證 token:     {verify_token}")
print(f"[Server] 驗證通過: {verify_token == forged_token}")
```

預期輸出：

```
[Server] 原始 token: (64 hex chars)
[Server] 原始 message: b'amount=100&to=alice'

[Attacker] 偽造 message: b'amount=100&to=alice\x80\x00...\x00\xe8&admin=true'
[Attacker] 偽造 token:   (64 hex chars)

[Server] 驗證 token:     (same 64 hex chars)
[Server] 驗證通過: True
```

攻擊成功：攻擊者在不知道 `secret` 的情況下，成功附加了 `&admin=true` 並算出合法的 token。

---

## 範例二：SHA-3 和 HMAC 不受影響

### SHA-3 免疫

```python
import hashlib

secret = b"MYSECRETKEY"
message = b"amount=100&to=alice"

# SHA-3 的 hash
sha3_token = hashlib.sha3_256(secret + message).hexdigest()
print(f"SHA3-256 token: {sha3_token}")

# SHA-3 用 sponge 構造：
# hash 值只是 state 的 rate 部分
# 攻擊者不知道 capacity（512 bits），無法繼續 hash
# → Length extension 不可能
print("SHA-3 不受 length extension 影響：")
print("  hash 只暴露 rate，不暴露 capacity")
print(f"  SHA3-256 的 capacity = 512 bits → 攻擊者缺 512 bits 的資訊")
```

### HMAC 免疫

```python
import hmac, hashlib

secret = b"MYSECRETKEY"
message = b"amount=100&to=alice"
mac = hmac.new(secret, message, hashlib.sha256).hexdigest()
print(f"HMAC-SHA-256: {mac}")
# HMAC(K, M) = H((K ⊕ opad) || H((K ⊕ ipad) || M))
# 外層 hash 需要 K ⊕ opad → 攻擊者不知道 K → 無法做 length extension
# 即使對內層 hash 做 extension，結果要過外層 hash → 缺 key → 無效
```

---

## 對比與取捨

| 方案 | Length Extension 安全 | 效能 | 標準化 | 適用場景 |
|------|----------------------|------|--------|---------|
| `H(key \|\| msg)` | 不安全 | 快（1 次 hash） | 無 | 永遠不要用 |
| `H(msg \|\| key)` | 安全*（但有理論弱點）| 快（1 次 hash） | 無 | 不推薦 |
| HMAC-SHA-256 | 安全 | 中（3 次 hash） | RFC 2104 / FIPS 198 | 最佳實踐 |
| SHA-3(key \|\| msg) | 安全（sponge 天生免疫） | 中 | NIST FIPS 202 | 可用但推薦 KMAC |
| KMAC | 安全 | 中 | NIST SP 800-185 | SHA-3 生態的最佳實踐 |

\* `H(msg || key)` 不受 length extension，因為 key 在最後。但 Preneel 和 van Oorschot 在 1995 年指出它有其他理論弱點。不要用。

### 為什麼不猜 secret 長度？

攻擊者不知道 secret 長度怎麼辦？窮舉。通常 secret 是 16、32、或 64 bytes。攻擊者對每個可能的長度各試一次，只要一個成功就行。伺服器通常不會限速這種驗證請求（因為每個看起來都是正常請求）。

---

## 踩雷集錦

### 踩雷 1：認為 `H(msg || key)` 安全所以用它

`H(msg || key)` 確實不受 length extension——因為 key 在最後，攻擊者不能從 hash 繼續（那需要知道 key）。但它有其他問題：如果壓縮函式有弱點（比如碰撞），攻擊者能找到 `msg1 ≠ msg2` 使得 `H(msg1 || key) = H(msg2 || key)`（不需要知道 key）。HMAC 的安全證明涵蓋了這種情況，`H(msg || key)` 沒有。

### 踩雷 2：以為 secret 長就安全

length extension 不需要 brute-force secret 的內容。不管 secret 是 16 bytes 還是 1024 bytes，攻擊者只要知道長度就能攻擊。secret 的長度是要保密的參數，但只靠長度保密不是安全方案。

### 踩雷 3：用截斷 hash 防禦

有人想：「如果我只輸出 hash 的前 128 bit，攻擊者就不知道完整的 internal state 了！」沒用。SHA-256 的 internal state 是 256 bit，而 hash 輸出也是 256 bit——截斷到 128 bit 確實讓攻擊者少了一半資訊，但這不是設計上的安全保證。用 HMAC。

### 踩雷 4：忘記 padding 也會出現在偽造的 message 中

length extension 偽造出的 message 不是乾淨的 `message || extension`，而是 `message || padding_bytes || extension`。那些 padding bytes（`\x80\x00...\x00<length>`）會出現在 message 中間。如果 server 會解析 message 並拒絕包含 `\x00` 的輸入，攻擊可能失敗。但很多 HTTP server 不檢查這個。

---

## 進階

### 對 MD5 和 SHA-1 也適用

length extension 不是 SHA-256 的 bug——它是所有 Merkle-Damgård hash 的結構性弱點。MD5（128-bit state = 128-bit output）、SHA-1（160-bit state = 160-bit output）、SHA-512（512-bit state = 512-bit output）全部受影響。

### HashPump 工具

`HashPump`（https://github.com/bwall/HashPump）是 C 語言的 length extension 工具，CTF 常用：`hashpump -s <hash> -d <data> -a <extension> -k <secret_len>`。

### Flickr API 漏洞（2009）

Flickr 的 API 簽章用 `MD5(secret || sorted_params)`。攻擊者透過 length extension 附加 `&perms=delete`（權限提升），forge 出合法簽章。修復：改用 HMAC。

---

## 動手練習

1. **手動計算 padding**：對 `secret = b"KEY"` (3 bytes) 和 `message = b"data"` (4 bytes)，手動算出 SHA-256 的 padding bytes。驗證填充後的總長是 512 bit 的整數倍

2. **爆破 secret 長度**：修改 PoC，讓攻擊者不知道 secret 長度，對長度 1 到 64 逐一嘗試，直到 forge 出的 token 驗證通過。測量需要幾次嘗試

3. **對 MD5 做 length extension**：修改 PoC 改用 MD5（128-bit state = 4 個 32-bit word）。提示：MD5 用 little-endian，不是 big-endian

4. **（挑戰）防禦驗證**：寫一個 server 端的驗證函式，分別用 `H(key||msg)` 和 HMAC，發送 extension 請求，驗證前者可以被攻破、後者不行

---

## 重點整理

1. **Length extension 打的是 M-D 構造**：hash 輸出 = 完整 internal state → 攻擊者拿到 state → 可以繼續 hash
2. **攻擊條件**：知道 `H(secret || msg)`、`msg`、`len(secret)`。不需要知道 `secret` 的內容
3. **攻擊結果**：能算出 `H(secret || msg || padding || extension)` 的合法 hash
4. **偽造的 message 包含 padding bytes**：中間會有 `\x80\x00...\x00<length>` 垃圾
5. **防禦**：用 HMAC（Ch 16）或 SHA-3。永遠不要用 `H(key || msg)` 做 MAC

---

## 自我檢核

1. length extension attack 需要知道哪些資訊？不需要知道什麼？
2. 為什麼 SHA-256 的 hash 輸出等於完整的 internal state？
3. 偽造出的 message 和原始 message 有什麼不同（除了 extension）？
4. 為什麼 HMAC 不受 length extension 影響？
5. 為什麼 SHA-3 不受 length extension 影響？

---

## 延伸閱讀

- **Duong & Rizzo (2009)**："Flickr's API Signature Forgery Vulnerability"——現實世界的 length extension 攻擊案例
- **Vaudenay (2001)**："Security Flaws Induced by CBC Padding"——雖然是 padding oracle，但同一作者也討論了 hash 構造的弱點
- **HashPump**：https://github.com/bwall/HashPump ——CTF 用的 length extension 工具
- **Cryptopals Set 4, Challenge 29-30**：MD4/SHA-1 的 length extension 練習

---

## 下一章預告

[Ch 16 — MAC：HMAC、Poly1305](./16-mac-hmac-poly1305.md)：length extension 告訴我們「不能用裸 hash 做 MAC」，那正確的 MAC 該怎麼做？HMAC 的雙層 hash 結構為什麼安全？Poly1305 的 Wegman-Carter 構造又是什麼？
