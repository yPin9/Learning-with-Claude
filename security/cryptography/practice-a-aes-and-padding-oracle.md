# 練習 A — 手刻 AES-128 + padding oracle 解密

> 目標：把 Part 3 的 GF(2⁸)、Rijndael、CBC、padding 全部串起來。手刻 AES-128（Python 教學版 + C 性能版）、產 ECB penguin 圖，最後實作 CBC padding oracle attack — 給定一個會回 "padding error" 的 server，把任意密文還原成 plaintext。

## 任務規格

| Part | 內容 | 語言 |
|---|---|---|
| 1 | AES-128 ECB encrypt/decrypt + NIST test vector 通過 | Python 為主 |
| 2 | 把 BMP 圖片用 ECB 加密，肉眼看到 penguin pattern | Python |
| 3 | AES-128 CBC encrypt/decrypt + PKCS#7 padding | Python |
| 4 | C 版本 AES-128 ECB 實作（用 GCC、與 OpenSSL 比對） | C |
| 5 | 寫一個 padding oracle server（HTTP，回不同 status 給 padding 對 / 錯） | Python |
| 6 | 寫 padding oracle attacker：給目標 ciphertext，還原 plaintext | Python |

## 期望輸出

### Part 1

```bash
$ python aes.py test
Test vector NIST FIPS-197 ✓
1000 random tests vs cryptography library ✓
```

### Part 2

```
[輸入]  tux.bmp
[輸出]  tux_ecb_encrypted.bmp
肉眼能看出企鵝形狀
```

### Part 6

```bash
$ python attacker.py http://localhost:8000 <ciphertext_hex>
[*] decrypting block 1/3
[*] decrypting block 2/3
[*] decrypting block 3/3
plaintext: "the secret message is hidden here in CBC"
```

## 實作步驟建議

### Step 1：AES-128 Python 教學版

從 Ch 10 的 `sub_bytes` `shift_rows` `mix_columns` `add_round_key` 開始，組成完整 `aes128_encrypt`。

**測試 NIST FIPS-197 Appendix B**：

```python
key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
pt  = bytes.fromhex("00112233445566778899aabbccddeeff")
expected_ct = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
```

通過 → 進 step 2。

### Step 2：ECB penguin

需要 BMP 圖（uncompressed format）：

```bash
# 隨便找一張 Tux 圖，轉 BMP
convert tux.png tux.bmp
```

```python
def ecb_penguin(input_bmp, output_bmp, key):
    with open(input_bmp, 'rb') as f:
        data = f.read()
    # BMP header 一般 54 byte，留著不加密
    header = data[:54]
    pixels = data[54:]
    # padding 到 16 倍數
    pixels += b'\x00' * (16 - len(pixels) % 16)
    encrypted = b''
    for i in range(0, len(pixels), 16):
        encrypted += aes128_encrypt(pixels[i:i+16], key)
    with open(output_bmp, 'wb') as f:
        f.write(header + encrypted[:len(data)-54])
```

打開輸出 BMP，仍能看到企鵝。

### Step 3：CBC + padding

實作 `cbc_encrypt`、`cbc_decrypt`、PKCS#7 `pad` / `unpad`：

```python
def pkcs7_pad(data, block_size=16):
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)

def pkcs7_unpad(data):
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError("invalid padding")
    if data[-pad_len:] != bytes([pad_len] * pad_len):
        raise ValueError("invalid padding")
    return data[:-pad_len]
```

用 `cryptography` library 對照驗證。

### Step 4：C 版 AES

更貼近 production，用 lookup table 加速：

```c
#include <stdint.h>
#include <string.h>

static const uint8_t SBOX[256] = { 0x63, 0x7c, ... };  /* 256 values */

void sub_bytes(uint8_t state[16]) {
    for (int i = 0; i < 16; i++) state[i] = SBOX[state[i]];
}

void shift_rows(uint8_t state[16]) {
    uint8_t t;
    /* row 1 */
    t = state[1]; state[1] = state[5]; state[5] = state[9];
    state[9] = state[13]; state[13] = t;
    /* row 2 */
    t = state[2]; state[2] = state[10]; state[10] = t;
    t = state[6]; state[6] = state[14]; state[14] = t;
    /* row 3 */
    t = state[3]; state[3] = state[15]; state[15] = state[11];
    state[11] = state[7]; state[7] = t;
}

uint8_t xtime(uint8_t b) {
    return (b << 1) ^ (((b >> 7) & 1) * 0x1b);
}

void mix_columns(uint8_t state[16]) {
    for (int c = 0; c < 4; c++) {
        uint8_t s0 = state[c*4], s1 = state[c*4+1];
        uint8_t s2 = state[c*4+2], s3 = state[c*4+3];
        uint8_t t = s0 ^ s1 ^ s2 ^ s3;
        state[c*4]   ^= t ^ xtime(s0 ^ s1);
        state[c*4+1] ^= t ^ xtime(s1 ^ s2);
        state[c*4+2] ^= t ^ xtime(s2 ^ s3);
        state[c*4+3] ^= t ^ xtime(s3 ^ s0);
    }
}

void add_round_key(uint8_t state[16], const uint8_t round_key[16]) {
    for (int i = 0; i < 16; i++) state[i] ^= round_key[i];
}

void aes128_encrypt(const uint8_t in[16], const uint8_t key[16], uint8_t out[16]) {
    uint8_t round_keys[176];
    key_expansion(key, round_keys);
    memcpy(out, in, 16);
    add_round_key(out, &round_keys[0]);
    for (int r = 1; r < 10; r++) {
        sub_bytes(out);
        shift_rows(out);
        mix_columns(out);
        add_round_key(out, &round_keys[r * 16]);
    }
    sub_bytes(out);
    shift_rows(out);
    add_round_key(out, &round_keys[160]);
}
```

編譯：

```bash
gcc -O2 -Wall aes.c main.c -o aes_test
```

### Step 5：Padding oracle server

簡單 HTTP server 接受 ciphertext，解密看 padding，回 200/400：

```python
from http.server import BaseHTTPRequestHandler, HTTPServer
import binascii

KEY = b"YELLOW SUBMARINE"  # 16 byte
SECRET = b"the secret message is hidden here in CBC"

class OracleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # path = /check?ct=<hex>
        try:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            ct = bytes.fromhex(qs['ct'][0])
            iv, blocks = ct[:16], ct[16:]
            pt = cbc_decrypt(blocks, KEY, iv)
            try:
                pkcs7_unpad(pt)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            except ValueError:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid padding")
        except Exception as e:
            self.send_response(500)
            self.end_headers()

def serve():
    HTTPServer(('localhost', 8000), OracleHandler).serve_forever()
```

啟動後可用 curl 試：

```bash
curl -i "http://localhost:8000/check?ct=00...00"
```

### Step 6：Padding oracle attacker

實作 `padding_oracle_attack`：

```python
import requests

def oracle(ciphertext_hex):
    r = requests.get(f"http://localhost:8000/check?ct={ciphertext_hex}")
    return r.status_code == 200

def attack(target_ct):
    blocks = [target_ct[i:i+16] for i in range(0, len(target_ct), 16)]
    plaintext = b''
    for i in range(1, len(blocks)):
        decrypted_block = decrypt_block(blocks[i-1], blocks[i], oracle)
        plaintext += decrypted_block
        print(f"[*] block {i}/{len(blocks)-1}: {decrypted_block}")
    return plaintext
```

`decrypt_block` 用 Ch 11 提的 byte-by-byte logic。

完整攻擊預計 10-30 秒（取決於 server 速度）。

## 完整參考解答

**先寫過再看**。

<details>
<summary>AES-128 完整 Python 解答骨架</summary>

```python
import struct

SBOX = [...]  # FIPS 197 完整 256 值
INV_SBOX = [...]
RCON = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]

def xtime(b):
    return ((b << 1) ^ 0x1b) & 0xFF if b & 0x80 else (b << 1) & 0xFF

def sub_bytes(state): return [SBOX[b] for b in state]
def inv_sub_bytes(state): return [INV_SBOX[b] for b in state]

def shift_rows(state):
    s = state.copy()
    s[1], s[5], s[9], s[13] = s[5], s[9], s[13], s[1]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[15], s[3], s[7], s[11]
    return s

def inv_shift_rows(state):
    s = state.copy()
    s[1], s[5], s[9], s[13] = s[13], s[1], s[5], s[9]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[7], s[11], s[15], s[3]
    return s

def mix_columns(state):
    s = state.copy()
    for c in range(4):
        s0, s1, s2, s3 = s[c*4], s[c*4+1], s[c*4+2], s[c*4+3]
        s[c*4]   = xtime(s0) ^ (xtime(s1) ^ s1) ^ s2 ^ s3
        s[c*4+1] = s0 ^ xtime(s1) ^ (xtime(s2) ^ s2) ^ s3
        s[c*4+2] = s0 ^ s1 ^ xtime(s2) ^ (xtime(s3) ^ s3)
        s[c*4+3] = (xtime(s0) ^ s0) ^ s1 ^ s2 ^ xtime(s3)
    return s

def inv_mix_columns(state):
    def gf_mul(a, b):
        result = 0
        for _ in range(8):
            if b & 1:
                result ^= a
            a = xtime(a)
            b >>= 1
        return result
    s = state.copy()
    for c in range(4):
        s0, s1, s2, s3 = s[c*4], s[c*4+1], s[c*4+2], s[c*4+3]
        s[c*4]   = gf_mul(s0, 0x0e) ^ gf_mul(s1, 0x0b) ^ gf_mul(s2, 0x0d) ^ gf_mul(s3, 0x09)
        s[c*4+1] = gf_mul(s0, 0x09) ^ gf_mul(s1, 0x0e) ^ gf_mul(s2, 0x0b) ^ gf_mul(s3, 0x0d)
        s[c*4+2] = gf_mul(s0, 0x0d) ^ gf_mul(s1, 0x09) ^ gf_mul(s2, 0x0e) ^ gf_mul(s3, 0x0b)
        s[c*4+3] = gf_mul(s0, 0x0b) ^ gf_mul(s1, 0x0d) ^ gf_mul(s2, 0x09) ^ gf_mul(s3, 0x0e)
    return s

def add_round_key(state, key):
    return [s ^ k for s, k in zip(state, key)]

def key_expansion(key):
    words = [list(key[i*4:(i+1)*4]) for i in range(4)]
    for i in range(4, 44):
        temp = words[i-1].copy()
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= RCON[i // 4]
        new_word = [words[i-4][j] ^ temp[j] for j in range(4)]
        words.append(new_word)
    return [bytes(b for w in words[r*4:(r+1)*4] for b in w) for r in range(11)]

def aes128_encrypt(pt, key):
    rks = key_expansion(key)
    state = list(pt)
    state = add_round_key(state, rks[0])
    for r in range(1, 10):
        state = sub_bytes(state)
        state = shift_rows(state)
        state = mix_columns(state)
        state = add_round_key(state, rks[r])
    state = sub_bytes(state)
    state = shift_rows(state)
    state = add_round_key(state, rks[10])
    return bytes(state)

def aes128_decrypt(ct, key):
    rks = key_expansion(key)
    state = list(ct)
    state = add_round_key(state, rks[10])
    for r in range(9, 0, -1):
        state = inv_shift_rows(state)
        state = inv_sub_bytes(state)
        state = add_round_key(state, rks[r])
        state = inv_mix_columns(state)
    state = inv_shift_rows(state)
    state = inv_sub_bytes(state)
    state = add_round_key(state, rks[0])
    return bytes(state)
```

</details>

<details>
<summary>Padding oracle attack 完整解答</summary>

```python
import requests

def oracle(ciphertext: bytes) -> bool:
    r = requests.get(f"http://localhost:8000/check?ct={ciphertext.hex()}")
    return r.status_code == 200

def decrypt_block(prev: bytes, target: bytes) -> bytes:
    """用 padding oracle 解一個 block"""
    intermediate = bytearray(16)
    for byte_pos in range(15, -1, -1):
        pad_value = 16 - byte_pos
        for guess in range(256):
            fake_prev = bytearray(16)
            for k in range(byte_pos+1, 16):
                fake_prev[k] = intermediate[k] ^ pad_value
            fake_prev[byte_pos] = guess
            if oracle(bytes(fake_prev) + target):
                # 排除「正巧 byte_pos 在 plaintext 中是 pad_value-1」的 false positive
                # 改 byte_pos-1 看是否仍 OK
                if byte_pos > 0:
                    fake_prev[byte_pos - 1] ^= 1
                    if not oracle(bytes(fake_prev) + target):
                        continue
                intermediate[byte_pos] = guess ^ pad_value
                break
    return bytes(a ^ b for a, b in zip(intermediate, prev))

def attack(ciphertext: bytes) -> bytes:
    blocks = [ciphertext[i:i+16] for i in range(0, len(ciphertext), 16)]
    plaintext = b''
    for i in range(1, len(blocks)):
        print(f"[*] block {i}/{len(blocks)-1}")
        plaintext += decrypt_block(blocks[i-1], blocks[i])
    # 去掉 PKCS#7 padding
    return plaintext[:-plaintext[-1]]

if __name__ == '__main__':
    import sys
    ct = bytes.fromhex(sys.argv[1])
    pt = attack(ct)
    print(f"plaintext: {pt}")
```

</details>

## 測試用例

1. **AES-128 一致性**：與 `cryptography` library 對照 1000 次隨機測試
2. **CBC + 解密**：自己 encrypt 再 decrypt 還原原文
3. **ECB penguin**：肉眼看 BMP 仍能辨識企鵝
4. **Padding oracle 攻擊速度**：48-byte ciphertext 在 localhost 應 30 秒內解完
5. **修復後測試**：把 server 的 padding error 統一回 200（不洩漏） → 攻擊應失敗

## 自我檢核

- [ ] 我能寫完整 AES-128 encrypt + decrypt 並通過 NIST test vector
- [ ] 我用 ECB 加密圖片仍能看到形狀
- [ ] 我能寫 CBC + PKCS#7 padding 並對照 library
- [ ] 我寫的 C 版本與 Python 版本一致
- [ ] 我能寫 padding oracle server 與對應 attacker
- [ ] 我能解釋為什麼 oracle 即使只回 200/400 就讓 attacker 完全解密

下一個 Part 進 hash 與 MAC 世界。

→ [Ch 13 Hash 函式](./13-hash-functions.md)
