# 練習 B — SHA-256 + Length Extension Attack

> 目標：從零實作 SHA-256，用手刻版本跑 length extension attack forge token，然後實作 HMAC-SHA-256 驗證 length extension 對 HMAC 無效。

---

## 總覽

這個練習分三個 Phase：

| Phase | 內容 | 你會學到 |
|-------|------|---------|
| 1 | 從零實作 SHA-256 | message schedule、64 rounds、壓縮函式的每一步 |
| 2 | 對手刻 SHA-256 跑 length extension attack | M-D 構造為什麼泄漏 internal state、如何 forge |
| 3 | 實作 HMAC-SHA-256 | 雙層 hash 結構為什麼防禦 length extension |

環境：Python 3.11, Ubuntu 22.04

---

## Phase 1：從零實作 SHA-256

### 規格

實作一個 `SHA256` class，提供以下 API：

```python
hasher = SHA256()
hasher.update(b"Hello, ")
hasher.update(b"world!")
digest = hasher.hexdigest()
assert digest == hashlib.sha256(b"Hello, world!").hexdigest()
```

同時支援一次性呼叫：

```python
assert SHA256(b"abc").hexdigest() == hashlib.sha256(b"abc").hexdigest()
```

### Step 1：常數

SHA-256 需要兩組常數：

```python
import struct

# 初始 hash 值（IV）：前 8 個質數（2, 3, 5, 7, 11, 13, 17, 19）的平方根，
# 取小數部分的前 32 bit
H0 = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
]

# 64 個 round 常數：前 64 個質數的立方根，取小數部分的前 32 bit
K = [
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
```

### Step 2：位元運算輔助函式

SHA-256 的壓縮函式需要以下運算（全部在 32-bit unsigned integer 上操作）：

```python
def rotr(x: int, n: int) -> int:
    """32-bit right rotate"""
    return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF

def shr(x: int, n: int) -> int:
    """32-bit right shift"""
    return x >> n

def ch(e: int, f: int, g: int) -> int:
    """Choice: 根據 e 的每個 bit，選 f 或 g 的對應 bit"""
    return (e & f) ^ (~e & g) & 0xFFFFFFFF

def maj(a: int, b: int, c: int) -> int:
    """Majority: 三個 bit 中取多數"""
    return (a & b) ^ (a & c) ^ (b & c)

def sigma0(a: int) -> int:
    """大 Sigma 0（用在 state update）"""
    return rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)

def sigma1(e: int) -> int:
    """大 Sigma 1（用在 state update）"""
    return rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)

def little_sigma0(x: int) -> int:
    """小 sigma 0（用在 message schedule）"""
    return rotr(x, 7) ^ rotr(x, 18) ^ shr(x, 3)

def little_sigma1(x: int) -> int:
    """小 sigma 1（用在 message schedule）"""
    return rotr(x, 17) ^ rotr(x, 19) ^ shr(x, 10)
```

### Step 3：Padding

SHA-256 的 padding 規則（FIPS 180-4 Section 5.1.1）：

```python
def sha256_pad(msg_len_bytes: int) -> bytes:
    """
    計算 SHA-256 的 padding bytes。
    msg_len_bytes: 原始訊息的 byte 長度。
    回傳: padding bytes（不含原始訊息）。
    """
    bit_len = msg_len_bytes * 8

    # 加 0x80（= 1 bit 後面跟 7 個 0 bit）
    padding = b'\x80'

    # 補零，直到 (msg_len + padding_len) ≡ 56 (mod 64)
    # 因為最後 8 bytes 是長度
    zeros_needed = (56 - (msg_len_bytes + 1) % 64) % 64
    padding += b'\x00' * zeros_needed

    # 附上原始訊息長度（64-bit big-endian）
    padding += struct.pack('>Q', bit_len)

    return padding
```

### Step 4：壓縮函式

```python
def sha256_compress(state: list[int], block: bytes) -> list[int]:
    """SHA-256 壓縮函式：吃 8 個 state word + 64-byte block，回傳更新的 state"""
    assert len(block) == 64

    # --- Message Schedule ---
    # 前 16 個 word 直接從 block 取（big-endian）
    W = list(struct.unpack('>16L', block))

    # 擴展到 64 個 word
    for i in range(16, 64):
        W.append(
            (little_sigma1(W[i-2]) + W[i-7] +
             little_sigma0(W[i-15]) + W[i-16]) & 0xFFFFFFFF
        )

    # --- 64 Rounds ---
    a, b, c, d, e, f, g, h = state

    for i in range(64):
        T1 = (h + sigma1(e) + ch(e, f, g) + K[i] + W[i]) & 0xFFFFFFFF
        T2 = (sigma0(a) + maj(a, b, c)) & 0xFFFFFFFF

        h = g
        g = f
        f = e
        e = (d + T1) & 0xFFFFFFFF
        d = c
        c = b
        b = a
        a = (T1 + T2) & 0xFFFFFFFF

    # --- 加回原始 state ---
    new_state = [
        (state[0] + a) & 0xFFFFFFFF,
        (state[1] + b) & 0xFFFFFFFF,
        (state[2] + c) & 0xFFFFFFFF,
        (state[3] + d) & 0xFFFFFFFF,
        (state[4] + e) & 0xFFFFFFFF,
        (state[5] + f) & 0xFFFFFFFF,
        (state[6] + g) & 0xFFFFFFFF,
        (state[7] + h) & 0xFFFFFFFF,
    ]

    return new_state
```

### Step 5：組裝完整的 SHA256 class

```python
import hashlib  # 只用來驗證

class SHA256:
    """從零實作的 SHA-256"""

    block_size = 64   # bytes
    digest_size = 32  # bytes

    def __init__(self, data: bytes = b""):
        self._state = list(H0)     # 8 個 32-bit state words
        self._buffer = b""          # 未處理的 bytes
        self._total_len = 0         # 已吃進的總 bytes
        if data:
            self.update(data)

    def update(self, data: bytes) -> 'SHA256':
        """餵更多資料"""
        self._buffer += data
        self._total_len += len(data)

        # 每湊滿 64 bytes 就壓縮一個 block
        while len(self._buffer) >= 64:
            block = self._buffer[:64]
            self._buffer = self._buffer[64:]
            self._state = sha256_compress(self._state, block)

        return self

    def digest(self) -> bytes:
        """回傳 32-byte digest（不改變內部狀態）"""
        # 複製狀態，避免 digest() 後還能繼續 update()
        tmp_state = list(self._state)
        tmp_buffer = self._buffer

        # 計算 padding
        padding = sha256_pad(self._total_len)
        tmp_buffer += padding

        # 處理剩餘的 blocks
        while len(tmp_buffer) >= 64:
            block = tmp_buffer[:64]
            tmp_buffer = tmp_buffer[64:]
            tmp_state = sha256_compress(tmp_state, block)

        # 把 8 個 32-bit word 組成 32 bytes
        return struct.pack('>8L', *tmp_state)

    def hexdigest(self) -> str:
        return self.digest().hex()
```

### Step 6：驗證

```python
# NIST 官方測試向量
test_vectors = [
    (b"abc",
     "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    (b"",
     "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    (b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
     "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"),
    (b"a" * 1_000_000,
     "cdc76e5c9914fb9281a1c7e284d73e67f2bf3b3b5ff93b1a943d2c090d0b4f3c"),
]

print("=== SHA-256 驗證 ===")
for msg, expected in test_vectors:
    result = SHA256(msg).hexdigest()
    status = "PASS" if result == expected else "FAIL"
    label = repr(msg) if len(msg) <= 60 else f"b'a' * 1000000"
    print(f"  [{status}] {label}")
    if result != expected:
        print(f"    期望: {expected}")
        print(f"    實際: {result}")

# 測試 incremental update
h = SHA256()
h.update(b"Hello, ")
h.update(b"world!")
assert h.hexdigest() == hashlib.sha256(b"Hello, world!").hexdigest()
print("  [PASS] incremental update")
```

---

## Phase 2：Length Extension Attack

### 攻擊目標

你有一個「簽章 server」，用 `SHA-256(secret || message)` 做 token：

```python
SECRET = b"TopSecretKey2024"  # Server 的 secret（攻擊者不知道內容）

def server_sign(message: bytes) -> str:
    """Server 簽章：回傳 SHA-256(SECRET || message)"""
    return SHA256(SECRET + message).hexdigest()

def server_verify(message: bytes, token: str) -> bool:
    """Server 驗證"""
    expected = SHA256(SECRET + message).hexdigest()
    return expected == token
```

攻擊者的資訊：
- `message = b"user=guest&action=view"`（公開的 API 參數）
- `token = server_sign(message)`（從 HTTP response 取得）
- `secret_len = 16`（已知或猜測）

攻擊者的目標：
- 附加 `b"&action=admin"` 並 forge 出合法 token
- 不知道 SECRET 的內容

### Step 1：提取 internal state

SHA-256 的 hash 輸出就是 8 個 32-bit state word 的直接輸出。攻擊者從 token 提取：

```python
def extract_state(hex_digest: str) -> list[int]:
    """從 SHA-256 hex digest 提取 8 個 32-bit state words"""
    digest_bytes = bytes.fromhex(hex_digest)
    return list(struct.unpack('>8L', digest_bytes))
```

### Step 2：計算原始 padding

攻擊者需要精確重建 `SHA-256(SECRET || message)` 的 padding：

```python
def compute_glue_padding(secret_len: int, message: bytes) -> bytes:
    """
    計算 SHA-256(secret || message) 的 padding bytes。
    攻擊者不需要知道 secret 的內容，只需要知道長度。
    """
    total_len = secret_len + len(message)
    return sha256_pad(total_len)
```

### Step 3：用提取的 state 繼續 hash

這是攻擊的核心——修改 SHA256 class，允許設定 initial state：

```python
class SHA256Extended(SHA256):
    """支援設定 initial state 的 SHA-256（用於 length extension）"""

    def __init__(self, state: list[int], hashed_len: int, data: bytes = b""):
        """
        state: 8 個 32-bit state words（從已知 hash 提取）
        hashed_len: 到目前為止已經被 hash 的總 bytes 數
                   （= secret_len + message_len + padding_len）
        """
        self._state = list(state)
        self._buffer = b""
        self._total_len = hashed_len  # 告訴 padding 計算「前面已有這麼多 bytes」
        if data:
            self.update(data)
```

### Step 4：完整攻擊流程

```python
def length_extension_attack(
    known_hash: str,
    known_message: bytes,
    secret_len: int,
    extension: bytes,
) -> tuple[bytes, str]:
    """
    Length Extension Attack。
    回傳 (forged_message, forged_hash)。
    forged_message 是 server 端 SHA-256(secret || forged_message) == forged_hash。
    """
    # 1. 從已知 hash 提取 internal state
    state = extract_state(known_hash)

    # 2. 計算原始的 glue padding
    glue_padding = compute_glue_padding(secret_len, known_message)

    # 3. 到目前為止被 hash 的總 bytes
    hashed_so_far = secret_len + len(known_message) + len(glue_padding)

    # 4. 用提取的 state，繼續 hash extension
    extended = SHA256Extended(state, hashed_so_far, extension)
    forged_hash = extended.hexdigest()

    # 5. 構造偽造的 message（不含 secret）
    forged_message = known_message + glue_padding + extension

    return forged_message, forged_hash


# ========== 執行攻擊 ==========

message = b"user=guest&action=view"
token = server_sign(message)
extension = b"&action=admin"

print("=== Length Extension Attack ===")
print(f"原始 message: {message}")
print(f"原始 token:   {token}")
print(f"附加資料:     {extension}")
print()

forged_msg, forged_token = length_extension_attack(
    known_hash=token,
    known_message=message,
    secret_len=len(SECRET),
    extension=extension,
)

print(f"偽造 message: {forged_msg}")
print(f"偽造 token:   {forged_token}")
print()

# Server 驗證
is_valid = server_verify(forged_msg, forged_token)
print(f"Server 驗證結果: {is_valid}")
assert is_valid, "攻擊失敗！"
print("攻擊成功：在不知道 secret 的情況下 forge 了合法的 token")
```

### 爆破 secret 長度

如果攻擊者不知道 secret 長度，逐一嘗試：

```python
def brute_force_secret_length(
    known_hash: str,
    known_message: bytes,
    extension: bytes,
    max_len: int = 64,
) -> tuple[bytes, str, int] | None:
    """嘗試 secret_len = 1 到 max_len，直到 forge 成功"""
    for guess_len in range(1, max_len + 1):
        forged_msg, forged_token = length_extension_attack(
            known_hash, known_message, guess_len, extension
        )
        if server_verify(forged_msg, forged_token):
            return forged_msg, forged_token, guess_len
    return None

print("\n=== 爆破 Secret 長度 ===")
result = brute_force_secret_length(token, message, extension)
if result:
    _, _, found_len = result
    print(f"找到 secret 長度: {found_len} bytes")
    assert found_len == len(SECRET)
```

---

## Phase 3：HMAC-SHA-256

### 實作

用 Phase 1 的 SHA256 class 實作 HMAC：

```python
class HMAC_SHA256:
    """用手刻 SHA-256 實作的 HMAC-SHA-256"""

    block_size = 64  # SHA-256 block size

    def __init__(self, key: bytes, message: bytes = b""):
        # 如果 key 比 block 長，先 hash
        if len(key) > self.block_size:
            key = SHA256(key).digest()

        # 補零到 block_size
        key = key.ljust(self.block_size, b'\x00')

        # 計算 ipad 和 opad key
        self._ipad_key = bytes(k ^ 0x36 for k in key)
        self._opad_key = bytes(k ^ 0x5c for k in key)

        # 初始化 inner hash
        self._inner = SHA256(self._ipad_key)

        if message:
            self._inner.update(message)

    def update(self, data: bytes) -> 'HMAC_SHA256':
        """餵更多資料到 inner hash"""
        self._inner.update(data)
        return self

    def digest(self) -> bytes:
        """計算 HMAC digest"""
        # inner hash 的結果
        inner_digest = self._inner.digest()

        # outer hash: H(opad_key || inner_digest)
        outer = SHA256(self._opad_key + inner_digest)
        return outer.digest()

    def hexdigest(self) -> str:
        return self.digest().hex()
```

### 驗證 HMAC 正確性

```python
import hmac as hmac_stdlib

test_cases = [
    (b"key", b"The quick brown fox jumps over the lazy dog"),
    (b"SecretKey123", b"Hello, HMAC!"),
    (b"k" * 100, b"long key test"),   # key 比 block 長
    (b"k", b""),                       # 空 message
]

for key, msg in test_cases:
    expected = hmac_stdlib.new(key, msg, hashlib.sha256).hexdigest()
    result = HMAC_SHA256(key, msg).hexdigest()
    assert result == expected, f"FAIL: key={key!r}, msg={msg[:20]!r}"
print("HMAC-SHA-256 all tests passed")
```

### 驗證 Length Extension 對 HMAC 無效

```python
def server_sign_hmac(message: bytes) -> str:
    """Server 用 HMAC 簽章"""
    return HMAC_SHA256(SECRET, message).hexdigest()

def server_verify_hmac(message: bytes, token: str) -> bool:
    """Server 用 HMAC 驗證"""
    expected = HMAC_SHA256(SECRET, message).hexdigest()
    return expected == token

print("\n=== 驗證 HMAC 防禦 Length Extension ===")

message = b"user=guest&action=view"
hmac_token = server_sign_hmac(message)

# 嘗試 length extension attack（會失敗）
forged_msg, forged_token = length_extension_attack(
    known_hash=hmac_token,
    known_message=message,
    secret_len=len(SECRET),
    extension=b"&action=admin",
)

is_valid = server_verify_hmac(forged_msg, forged_token)
print(f"HMAC token:       {hmac_token}")
print(f"偽造 token:       {forged_token}")
print(f"Server 驗證結果:  {is_valid}")
assert not is_valid, "HMAC 不該被 length extension 攻破！"
print("HMAC 成功防禦了 length extension attack")

print("原因：HMAC 的外層 hash 需要 K ⊕ opad，攻擊者不知道 K → 無法繼續")
```

把以上所有部分放在一個檔案 `sha256_lab.py` 中，用 `python3 sha256_lab.py` 執行。三個 Phase 的測試應該全部 PASS，length extension 攻擊成功，HMAC 防禦成功。

---

## 除錯指南

### 常見 Bug 1：ch() 的 NOT 運算

Python 的 `~x` 對 int 回傳負數（無限精度）。修正：`(e & f) ^ ((~e & 0xFFFFFFFF) & g)` 或 `(e & f) ^ (~e & g) & 0xFFFFFFFF`。

### 常見 Bug 2：Padding 長度欄位

長度欄位是 **bit 數**，不是 byte 數：`struct.pack('>Q', msg_len_bytes * 8)`。

### 常見 Bug 3：hashed_so_far 計算

`hashed_so_far = secret_len + len(message) + len(glue_padding)`——不要忘記加 padding 長度。

### 常見 Bug 4：SHA256Extended 的 _total_len

`_total_len` 必須包含「已經被 hash 的所有 bytes」（含前面的 padding），最終 padding 的長度欄位才正確。

---

## 加分挑戰

1. **效能測量**：測量手刻 SHA-256 和 `hashlib.sha256` 的速度差距（hash 1 MB），預期 100x+ 差距
2. **視覺化壓縮函式**：在 `sha256_compress` 每 round 印出 8 個 state word，觀察 `b"abc"` 的 64 rounds 演變
3. **對 SHA-512 做 Length Extension**：SHA-512 用 64-bit word、1024-bit block、80 rounds，修改實作後跑 length extension
4. **HMAC timing attack**：寫 `==`（不安全）和 constant-time compare 兩個驗證函式，測量 response time 差異

---

## 你學到了什麼

完成這三個 Phase 之後，你應該能回答：

1. SHA-256 的壓縮函式做什麼？message schedule 怎麼把 16 word 擴展成 64 word？
2. 為什麼 SHA-256 的 hash 輸出等於完整的 internal state？這在設計上是有意的嗎？
3. Length extension attack 的三個步驟是什麼？（提取 state → 計算 glue padding → 繼續 hash）
4. 為什麼 HMAC 的雙層結構能防禦 length extension？
5. 如果改用 SHA-3 當底層 hash，length extension 還能不能做？為什麼？
