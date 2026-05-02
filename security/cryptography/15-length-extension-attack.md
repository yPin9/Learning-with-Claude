# Ch 15 — Length extension attack

> 目標：搞懂 Merkle-Damgård 的天生缺陷：知道 `H(secret || message)` 的人，不必知道 secret 也能算出 `H(secret || message || padding || extension)`。這直接讓 SHA-1/2 不能當「naive MAC」 — 也是 HMAC 出現的原因。

## Vulnerability 場景

某網站用 hash 當 MAC：

```
url: /api?user=alice&action=read&token=<H(secret||"user=alice&action=read")>
```

server 收到後重算 hash 比對。看似合理 — attacker 不知道 secret 怎麼偽造？

**但 attacker 能做**：把 `&action=admin` 加到 message 後面，**新算 hash 不需要知道 secret**。結果：升級權限。

## 為什麼成立

回憶 Merkle-Damgård：

```
state = IV
for block in message:
    state = f(state, block)
output = state
```

要 hash `M' = M || padding || extension`，等同：

```
state' = IV
for block in M:
    state' = f(state', block)        # 跑完 = state at H(M)
for block in padding || extension:
    state' = f(state', block)
output' = state'
```

**前一段 = H(M) 的最終 state**！Attacker 不需要重跑 M 部分，**直接從 H(M) 當 IV 開始**。

唯一要小心：**padding 是「M 結束時的 padding」，不是 M' 自己的 padding**。所以中間還有一段 "glue padding"。

## SHA-256 padding 規則

回憶（Ch 13 提過簡化版）：

```
1. 加一個 0x80 byte
2. 補 0x00 到還剩 8 byte 達 64 byte 倍數
3. 寫 64-bit big-endian 的「原訊息 bit 長度」

例：
  message = "abc" (3 byte = 24 bit)
  padding = 0x80 + 0x00 × 52 + 0x00...0x18 (8 byte big-endian = 24)
  total = 64 byte
```

要算 `H(secret||M||padding||ext)`，attacker 必須知道 `len(secret||M)` 才能算 padding。多數場景訊息長度可猜（cookie 結構固定）或可枚舉（10-100 byte 範圍）。

## 完整攻擊步驟

```
已知：
  H(secret||M) 的 hash 值 (256-bit)
  M 的內容（明文，不含 secret）
  len(secret||M)  ← 需要知道或枚舉

攻擊：
  1. 設 IV = 接收的 hash 值（拆成 8 個 32-bit word）
  2. 算 glue padding：
     P = SHA-256 padding for length |secret||M|
  3. forge_msg = M || P || ext
  4. 從 IV 開始 hash ext（不重 hash M||P）
  5. 結果 = H(secret || forge_msg)
  6. 把 forge_msg + 新 hash 送給 server
```

## Python 實作骨架

`pycryptodome` 提供 `SHA256.new()` 但不能 set IV。我們要手刻或用 `hashpumpy`：

```bash
pip install hashpumpy
```

但這個 library 在新版 Python 有點麻煩。手刻 SHA-256 with custom IV：

```python
import struct

# SHA-256 常數
K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    # ... 共 64 個
]

def sha256_with_iv(iv, msg):
    """
    用給定的 IV 與訊息（已有 padding 或自己 pad）跑 SHA-256
    """
    state = list(iv)  # 8 個 32-bit
    msg = pad_sha256(msg, len(msg))  # 標準 padding
    for i in range(0, len(msg), 64):
        block = msg[i:i+64]
        state = sha256_compress(state, block)
    return b''.join(s.to_bytes(4, 'big') for s in state)

def length_extension(orig_hash: bytes, orig_len: int, ext: bytes) -> tuple[bytes, bytes]:
    """
    回傳 (forged_message_suffix, new_hash)
    forged_message_suffix 是 glue_padding + ext
    new_hash 是 H(原 message || forged_suffix)
    """
    # 1. 從 orig_hash 取得 IV
    iv = struct.unpack('>8I', orig_hash)
    
    # 2. 算原訊息要 pad 多少
    glue_padding = sha256_glue_padding(orig_len)
    
    # 3. 從這個 IV 開始 hash extension
    # 但要注意這時 hash 函式內部 message length counter 應該從 (orig_len + len(glue_padding)) 開始
    new_total_len = orig_len + len(glue_padding) + len(ext)
    
    # 把 ext 自己 pad 到 block 倍數，包含正確的 total length
    padded_ext = pad_sha256_with_total_len(ext, new_total_len)
    
    state = list(iv)
    for i in range(0, len(padded_ext), 64):
        block = padded_ext[i:i+64]
        state = sha256_compress(state, block)
    new_hash = b''.join(s.to_bytes(4, 'big') for s in state)
    
    return glue_padding + ext, new_hash
```

## 實際攻擊 demo

```python
import hashlib

# 場景：某 API 用 hash 驗證
SECRET = b"super_secret_random_key"

def server_verify(message, hash_hex):
    expected = hashlib.sha256(SECRET + message).hexdigest()
    return expected == hash_hex

def server_action(message, hash_hex):
    if not server_verify(message, hash_hex):
        return "unauthorized"
    if b"admin" in message:
        return "[!] admin action executed"
    return f"normal: {message}"

# Attacker 知道：
known_message = b"user=alice&action=read"
known_hash = hashlib.sha256(SECRET + known_message).hexdigest()
secret_len = len(SECRET)  # 假設 attacker 猜中或枚舉到

# Attacker 要加 "&action=admin"
extension = b"&action=admin"

# 用 length extension 偽造
suffix, new_hash = length_extension(
    bytes.fromhex(known_hash),
    secret_len + len(known_message),
    extension
)

# 偽造的 message: known_message + glue_padding + ext
forged_message = known_message + suffix

result = server_action(forged_message, new_hash.hex())
print(result)
# [!] admin action executed
```

整個攻擊**不需要知道 SECRET**。

## 實際工具：HashPump

完整 production 版：

```bash
git clone https://github.com/bwall/HashPump
cd HashPump
make && sudo make install

# 使用
hashpump \
    --signature <orig_hash> \
    --data <orig_data> \
    --keylength <secret_length> \
    --additional <extension>
```

或 Python 版：

```bash
pip install hashpumpy
```

```python
import hashpumpy
new_hash, new_msg = hashpumpy.hashpump(
    known_hash,
    known_message,
    extension,
    secret_len
)
```

## 影響的 hash

length extension 影響**所有 plain Merkle-Damgård 結構**：

- **MD4, MD5**：影響
- **SHA-0, SHA-1, SHA-256, SHA-512**：影響
- **SHA-512/224, SHA-512/256**：**不**影響（因為 truncate 後 attacker 拿不到完整 state）
- **SHA-3 (Keccak)**：**不**影響（sponge）
- **BLAKE2 / BLAKE3**：**不**影響（特殊結構）

## 修補方式

### 1. HMAC（標準解）

```
HMAC(K, M) = H((K' XOR opad) || H((K' XOR ipad) || M))
```

雙重 hash + 兩個獨立 padding。**外層 hash 把 length extension 阻斷** — attacker 拿到 HMAC 不能 extend。

Ch 16 詳述。

### 2. 換 hash 函式

用 SHA-3、BLAKE2、SHA-512/256 — 結構上免疫 length extension。

### 3. Hash 結尾再加 secret

```
H(secret || message || secret)
```

**仍不安全**：有種變體攻擊能利用。**用 HMAC，不要自己組合**。

### 4. Truncated hash

只用 hash 的前一半（如 SHA-256 取前 128 bit）— attacker 拿到 truncated hash 沒完整 state，不能 extend。

但**不要自己 truncate**：用 SHA-512/256 等正規 truncated 變體。

## 為什麼 HMAC 一定安全

直覺：

```
H(K_inner || M)                    ← attacker 想 extend 這個
但 attacker 看不到這個 hash！

HMAC = H(K_outer || H(K_inner || M))
                  ↑
              外層 hash 把內層結果遮起來
              attacker 只看到外層 hash，但 K_outer 在前面她不知道
              所以 length extension 失效
```

正式證明：HMAC 在 hash 是 PRF 的假設下安全（Bellare 1996）。

## CTF / 真實案例

**Flickr API token (2009)**：用 `MD5(secret || params)` — 被 Wong 用 length extension 偽造 admin 權限 token。

**Many Rails 早期 cookie**：類似 pattern，自此 Rails 預設改 HMAC。

**CTF 經典題**：給你一個 oracle，輸入 message + tag (hash) 它驗證；目標：偽造一個 message 含 "admin=true" 並通過驗證。長度可枚舉 / 給 hint。

## 一個常見誤解

「我用 SHA-256 不會被 SHA-1 那種 collision attack，所以 SHA-256 當 MAC 安全」

**collision attack 與 length extension 是不同攻擊**。SHA-256 對 collision 有強抗性，但 **`SHA256(secret || message)` 仍受 length extension**。**hash 抗 collision ≠ hash 適合當 MAC**。

要 MAC 用 HMAC、Poly1305、KMAC（基於 SHAKE）。**不要用 H(secret || ...)**。

## 自我檢核

- [ ] 我能解釋 length extension 為什麼成立
- [ ] 我能寫 SHA-256 padding 規則
- [ ] 我能用 hashpumpy 或自寫 length_extension 完成攻擊
- [ ] 我知道哪些 hash 受 / 不受影響
- [ ] 我能說出 HMAC 為什麼免疫
- [ ] 我絕不寫 `H(secret || message)` 當 MAC

下一章看 MAC 三種主流構造：HMAC、GMAC、Poly1305。

→ [Ch 16 MAC：HMAC、GMAC、Poly1305](./16-mac-hmac-poly1305.md)
