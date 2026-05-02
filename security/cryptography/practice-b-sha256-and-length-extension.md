# 練習 B — 實作 SHA-256 + HMAC + length extension 攻擊

> 目標：手刻 SHA-256（Python + C）與 HMAC，然後對自己寫的 SHA-1 跑 length extension attack — 給一個 `H(secret || message)` 與長度，無 secret 算出 `H(secret || message || ext)` 的合法 hash。

## 任務規格

| Part | 內容 | 語言 |
|---|---|---|
| 1 | SHA-256 純 Python 教學版，通過 NIST test vector | Python |
| 2 | SHA-1 純 Python 版（length extension demo 用） | Python |
| 3 | HMAC-SHA256 並通過 RFC 4231 test vector | Python |
| 4 | C 版 SHA-256，與 OpenSSL 比對 | C |
| 5 | Length extension attacker：給 H(secret || msg) 與 secret 長度，產 forged hash | Python |
| 6 | Vulnerable server demo：用 `H(secret || cmd)` 當 token，被攻破 | Python |

## 期望輸出

### Part 1
```bash
$ python sha256.py test
NIST test vectors ✓ (3/3)
1000 random tests vs hashlib ✓
```

### Part 5
```bash
$ python length_ext.py
[*] Original message: user=alice&action=read
[*] Original tag: 8f4e9c...
[*] Forging extension: &action=admin
[*] Forged message: user=alice&action=read<glue><ext>
[*] Forged tag: a23f87...
```

### Part 6
```bash
$ python vuln_server.py &
$ python attacker.py
[+] Original token works (200 OK)
[!] Forged "admin" token also works (200 OK)
```

## 實作步驟建議

### Step 1：SHA-256 Python 版

關鍵組件：

```python
import struct

# 8 個初始 hash value（前 8 個質數的小數部分平方根）
H0 = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]

# 64 個 round constant（前 64 個質數的立方根）
K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    # ... 共 64 個
]

def rotr(x, n, w=32):
    return ((x >> n) | (x << (w - n))) & ((1 << w) - 1)

def sha256_padding(message):
    msg_len = len(message)
    bit_len = msg_len * 8
    message = bytearray(message) + b'\x80'
    while (len(message) + 8) % 64 != 0:
        message += b'\x00'
    message += bit_len.to_bytes(8, 'big')
    return bytes(message)

def sha256_compress(state, block):
    """處理一個 64-byte block"""
    W = list(struct.unpack('>16I', block))
    for i in range(16, 64):
        s0 = rotr(W[i-15], 7) ^ rotr(W[i-15], 18) ^ (W[i-15] >> 3)
        s1 = rotr(W[i-2], 17) ^ rotr(W[i-2], 19) ^ (W[i-2] >> 10)
        W.append((W[i-16] + s0 + W[i-7] + s1) & 0xFFFFFFFF)
    
    a, b, c, d, e, f, g, h = state
    for i in range(64):
        S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)
        ch = (e & f) ^ (~e & g & 0xFFFFFFFF)
        temp1 = (h + S1 + ch + K[i] + W[i]) & 0xFFFFFFFF
        S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)
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
    return [(s + v) & 0xFFFFFFFF for s, v in zip(state, [a, b, c, d, e, f, g, h])]

def sha256(message):
    padded = sha256_padding(message)
    state = H0[:]
    for i in range(0, len(padded), 64):
        state = sha256_compress(state, padded[i:i+64])
    return b''.join(s.to_bytes(4, 'big') for s in state)

# Test
import hashlib
for _ in range(1000):
    import os
    msg = os.urandom(100)
    assert sha256(msg) == hashlib.sha256(msg).digest()
print("✓ 1000 random tests pass")
```

### Step 2：SHA-1（給 length ext 用）

結構類似但 5 × 32-bit state、80 rounds：

```python
def sha1(message):
    H = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0]
    padded = sha256_padding(message)  # 同 padding 規則
    for i in range(0, len(padded), 64):
        H = sha1_compress(H, padded[i:i+64])
    return b''.join(s.to_bytes(4, 'big') for s in H)

def sha1_compress(state, block):
    W = list(struct.unpack('>16I', block))
    for i in range(16, 80):
        W.append(rotl((W[i-3] ^ W[i-8] ^ W[i-14] ^ W[i-16]), 1) & 0xFFFFFFFF)
    a, b, c, d, e = state
    for i in range(80):
        if i < 20: f = (b & c) | ((~b) & d & 0xFFFFFFFF); k = 0x5A827999
        elif i < 40: f = b ^ c ^ d; k = 0x6ED9EBA1
        elif i < 60: f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDC
        else: f = b ^ c ^ d; k = 0xCA62C1D6
        new_a = (rotl(a, 5) + f + e + k + W[i]) & 0xFFFFFFFF
        e, d, c, b, a = d, c, rotl(b, 30) & 0xFFFFFFFF, a, new_a
    return [(s + v) & 0xFFFFFFFF for s, v in zip(state, [a, b, c, d, e])]
```

### Step 3：HMAC

直接照公式：

```python
def hmac_sha256(key, message):
    if len(key) > 64:
        key = sha256(key)
    key = key + b'\x00' * (64 - len(key))
    ipad = bytes(b ^ 0x36 for b in key)
    opad = bytes(b ^ 0x5C for b in key)
    inner = sha256(ipad + message)
    return sha256(opad + inner)

# Test RFC 4231 vector
key = bytes.fromhex("0b" * 20)
msg = b"Hi There"
expected = bytes.fromhex(
    "b0344c61d8db38535ca8afceaf0bf12b"
    "881dc200c9833da726e9376c2e32cff7")
assert hmac_sha256(key, msg) == expected
print("✓ HMAC test")
```

### Step 4：C 版 SHA-256

完整 C 實作 100-150 行。重點：

```c
#include <stdint.h>
#include <string.h>

static const uint32_t K[64] = { 0x428a2f98, ... };

#define ROTR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))
#define CH(x,y,z) (((x) & (y)) ^ (~(x) & (z)))
#define MAJ(x,y,z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define BSIG0(x) (ROTR(x,2) ^ ROTR(x,13) ^ ROTR(x,22))
#define BSIG1(x) (ROTR(x,6) ^ ROTR(x,11) ^ ROTR(x,25))
#define SSIG0(x) (ROTR(x,7) ^ ROTR(x,18) ^ ((x) >> 3))
#define SSIG1(x) (ROTR(x,17) ^ ROTR(x,19) ^ ((x) >> 10))

void sha256_compress(uint32_t state[8], const uint8_t block[64]) {
    uint32_t W[64];
    for (int i = 0; i < 16; i++) {
        W[i] = ((uint32_t)block[i*4] << 24) | ((uint32_t)block[i*4+1] << 16)
             | ((uint32_t)block[i*4+2] << 8) | block[i*4+3];
    }
    for (int i = 16; i < 64; i++)
        W[i] = SSIG1(W[i-2]) + W[i-7] + SSIG0(W[i-15]) + W[i-16];
    
    uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
    uint32_t e = state[4], f = state[5], g = state[6], h = state[7];
    for (int i = 0; i < 64; i++) {
        uint32_t T1 = h + BSIG1(e) + CH(e,f,g) + K[i] + W[i];
        uint32_t T2 = BSIG0(a) + MAJ(a,b,c);
        h = g; g = f; f = e;
        e = d + T1;
        d = c; c = b; b = a;
        a = T1 + T2;
    }
    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}
```

編譯 + 測試 + 比對 OpenSSL：

```bash
gcc -O2 sha256.c main.c -o sha256_test
./sha256_test "abc"
# ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad

openssl dgst -sha256 <<< "abc"
# 一致
```

### Step 5：Length extension

```python
def sha256_state_to_iv(state_bytes: bytes):
    """從 hash 還原 IV (state)"""
    return list(struct.unpack('>8I', state_bytes))

def sha256_glue_padding(orig_len_bytes: int) -> bytes:
    """SHA-256 對長度 orig_len_bytes 的訊息會加什麼 padding"""
    bit_len = orig_len_bytes * 8
    pad = b'\x80'
    while (orig_len_bytes + len(pad) + 8) % 64 != 0:
        pad += b'\x00'
    pad += bit_len.to_bytes(8, 'big')
    return pad

def length_extension_sha256(orig_hash, orig_len, ext):
    """
    orig_hash: bytes (32-byte SHA-256 of secret||msg)
    orig_len: secret + msg 的總長 (要枚舉)
    ext: 想加的 extension
    回傳 (forged_message_suffix, new_hash)
    """
    glue = sha256_glue_padding(orig_len)
    
    # 重設 SHA-256 從 orig_hash 為 state，並繼續處理 ext
    state = sha256_state_to_iv(orig_hash)
    
    # ext 自己 padding，但要記得 total length 包括 secret + msg + glue + ext
    new_total = orig_len + len(glue) + len(ext)
    bit_len = new_total * 8
    padded_ext = bytearray(ext) + b'\x80'
    while (new_total + len(padded_ext) - len(ext) + 8) % 64 != 0:
        padded_ext += b'\x00'
    padded_ext += bit_len.to_bytes(8, 'big')
    
    for i in range(0, len(padded_ext), 64):
        state = sha256_compress(state, padded_ext[i:i+64])
    
    new_hash = b''.join(s.to_bytes(4, 'big') for s in state)
    return glue + ext, new_hash
```

### Step 6：Vulnerable server demo

```python
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer

SECRET = b"super_secret_random_key_xxx"

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        msg = qs['msg'][0].encode()
        tag = qs['tag'][0]
        expected = hashlib.sha256(SECRET + msg).hexdigest()
        if expected == tag:
            self.send_response(200)
            self.end_headers()
            if b'admin' in msg:
                self.wfile.write(b"[!] admin command executed")
            else:
                self.wfile.write(b"normal user command")
        else:
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"forbidden")

if __name__ == '__main__':
    HTTPServer(('localhost', 8000), Handler).serve_forever()
```

Attacker：

```python
import requests
import hashlib

# Attacker 知道一個合法 (msg, tag)
known_msg = b"user=alice&action=read"
known_tag = hashlib.sha256(b"super_secret_random_key_xxx" + known_msg).hexdigest()
# 或 attacker 從某個觀察到的 request 拿到

# Attacker 枚舉 secret 長度（這裡 = 27）
for secret_len in range(8, 40):
    suffix, new_hash = length_extension_sha256(
        bytes.fromhex(known_tag),
        secret_len + len(known_msg),
        b"&action=admin"
    )
    forged_msg = known_msg + suffix
    r = requests.get(
        f"http://localhost:8000/?msg={forged_msg.hex()}&tag={new_hash.hex()}"
    )
    if r.status_code == 200 and b'admin' in r.content:
        print(f"[+] secret_len = {secret_len}")
        print(f"[!] {r.text}")
        break
```

## 完整參考解答

**先寫過再看**。

<details>
<summary>SHA-256 完整 Python（簡化版）</summary>

見 step 1 的範例 — 補完 K 常數即可工作。約 60 行可跑。

</details>

<details>
<summary>Length extension 完整自動化</summary>

```python
def auto_length_extension(orig_hash, orig_msg, ext, oracle_url, max_secret_len=64):
    """
    枚舉 secret 長度，找一個能讓 oracle 接受 forged token 的長度。
    回傳 (secret_len, forged_msg, new_tag)
    """
    for secret_len in range(1, max_secret_len + 1):
        suffix, new_hash = length_extension_sha256(
            bytes.fromhex(orig_hash),
            secret_len + len(orig_msg),
            ext
        )
        forged_msg = orig_msg + suffix
        # 試 oracle
        r = requests.get(f"{oracle_url}?msg={forged_msg.hex()}&tag={new_hash.hex()}")
        if r.status_code == 200:
            return secret_len, forged_msg, new_hash.hex()
    return None
```

</details>

## 測試用例

1. **SHA-256 NIST 三組 test vector** 全通過
2. **HMAC RFC 4231 七組 test vector** 全通過
3. **C 版本** 1 GB 訊息 hash 速度：與 OpenSSL `sha256sum` 相差 < 3×（純 C 沒 SHA-NI 加速）
4. **Length extension** 攻擊：給 secret length 12，能成功偽造
5. **修補後測試**：把 server 改用 `hmac.new(SECRET, msg, sha256).hexdigest()` → 攻擊應失敗

## 自我檢核

- [ ] 我能寫完整 SHA-256（Python + C）並通過 NIST test vector
- [ ] 我能寫 SHA-1 與看出它與 SHA-256 結構差異
- [ ] 我能寫 HMAC-SHA256 並通過 RFC 4231 vectors
- [ ] 我能完成 length extension attack 並破自己 server
- [ ] 我能解釋為什麼 HMAC 修好了 length extension
- [ ] 我能寫 const-time MAC verify

下一個 Part 進公鑰密碼世界 — Diffie-Hellman、RSA、橢圓曲線、PKI。

→ [Ch 18 公鑰動機與 Diffie-Hellman](./18-public-key-and-dh.md)
