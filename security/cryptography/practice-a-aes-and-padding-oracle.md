# 練習 A — 手刻 AES-128 + Padding Oracle 攻擊

> 目標：從零手刻 AES-128-ECB encrypt（不用任何 crypto library），用它搭 CBC mode，建一個有 padding oracle 漏洞的 server，最後寫攻擊 client 在不知道 key 的情況下解密整段密文。整合 Ch 9–11 所有內容。

## 題目規格

四個 Phase，逐步遞進。**禁止在 Phase 1-2 使用 `cryptography` 或任何 crypto library** — 全部手刻。Phase 3-4 可以用 `flask`。

最終驗收：你的攻擊 client 能解出一段被 AES-128-CBC 加密的明文（你不知道 key）。

## Phase 1：手刻 AES-128-ECB Encrypt

### 要求

從零實作完整的 AES-128-ECB encrypt。需要手刻的部分：

1. GF(2⁸) 乘法（`gf_mul`）和 `xtime`
2. S-box 生成（GF(2⁸) 逆元 + affine transform）— 或直接用預計算表
3. Key expansion（RotWord, SubWord, Rcon）
4. 四個 round 操作：SubBytes, ShiftRows, MixColumns, AddRoundKey
5. 10-round encrypt 主迴圈（最後一輪無 MixColumns）

### 骨架

```python
# aes_handmade.py

# ─── GF(2⁸) 運算 ───
def xtime(a):
    """GF(2⁸) 中乘以 x"""
    # TODO: 實作
    pass

def gf_mul(a, b):
    """GF(2⁸) 乘法"""
    # TODO: 實作
    pass

# ─── S-box ───
SBOX = []  # TODO: 手刻生成或填入 256 bytes

# ─── State 操作 ───
def bytes_to_state(b):
    """16 bytes → 4x4 state (column-major!)"""
    # TODO: 注意是 column-major 不是 row-major
    pass

def state_to_bytes(s):
    """4x4 state → 16 bytes"""
    pass

# ─── Round 操作 ───
def sub_bytes(state):
    """每個 byte 查 S-box"""
    pass

def shift_rows(state):
    """Row 0 不移, Row 1 左移 1, Row 2 左移 2, Row 3 左移 3"""
    pass

def mix_columns(state):
    """MDS 矩陣乘法 (GF(2⁸))"""
    pass

def add_round_key(state, round_key_bytes):
    """State XOR round key (column-major!)"""
    pass

# ─── Key Expansion ───
RCON = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]

def key_expansion(key):
    """16 bytes key → 176 bytes expanded key"""
    pass

# ─── 主函數 ───
def aes128_encrypt_block(plaintext, key):
    """加密一個 16-byte block，回傳 16-byte ciphertext"""
    assert len(plaintext) == 16 and len(key) == 16
    # TODO
    pass
```

### 驗收標準

用 FIPS-197 附錄 B 的測試向量驗證：

```python
def test_fips197():
    key = bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c')
    pt  = bytes.fromhex('3243f6a8885a308d313198a2e0370734')
    expected_ct = bytes.fromhex('3925841d02dc09fbdc118597196a0b32')
    
    ct = aes128_encrypt_block(pt, key)
    assert ct == expected_ct, f"FAIL: got {ct.hex()}"
    print("[Phase 1] FIPS-197 Appendix B: PASS")

# 額外測試：NIST AES Known Answer Test
def test_nist_kat():
    # ECB-AES128 的 Known Answer Test
    key = bytes.fromhex('00000000000000000000000000000000')
    pt  = bytes.fromhex('f34481ec3cc627bacd5dc3fb08f273e6')
    expected_ct = bytes.fromhex('0336763e966d92595a567cc9ce537f5e')
    
    ct = aes128_encrypt_block(pt, key)
    assert ct == expected_ct, f"FAIL: got {ct.hex()}"
    print("[Phase 1] NIST KAT: PASS")
```

### 常見卡關點

| 症狀 | 原因 | 修法 |
|------|------|------|
| 結果完全錯 | state 用 row-major | 改成 column-major |
| Round 10 結果錯 | 最後一輪有 MixColumns | 拿掉 |
| Key expansion 不對 | Rcon 只 XOR 到 word 的第一 byte | 確認 Rcon 格式 |
| S-box 某些值錯 | affine 忘了 XOR 0x63 | 加上 |

## Phase 2：搭 CBC Mode

### 要求

用 Phase 1 的 `aes128_encrypt_block` 加上 CBC mode：

1. 實作 PKCS#7 padding
2. 實作 AES-128-CBC encrypt
3. 實作 AES-128-CBC decrypt（需要手刻 `aes128_decrypt_block`）

### 骨架

```python
# cbc_mode.py
from aes_handmade import aes128_encrypt_block
import os

# ─── AES Decrypt（需要逆操作） ───
INV_SBOX = []  # TODO: 從 SBOX 反推

def inv_sub_bytes(state):
    pass

def inv_shift_rows(state):
    """Row 1 右移 1, Row 2 右移 2, Row 3 右移 3"""
    pass

def inv_mix_columns(state):
    """用逆矩陣 [0E,0B,0D,09; 09,0E,0B,0D; 0D,09,0E,0B; 0B,0D,09,0E]"""
    pass

def aes128_decrypt_block(ciphertext, key):
    """解密一個 16-byte block"""
    pass

# ─── PKCS#7 Padding ───
def pkcs7_pad(data, block_size=16):
    """加 PKCS#7 padding"""
    pass

def pkcs7_unpad(data, block_size=16):
    """移除 PKCS#7 padding，invalid 時 raise ValueError"""
    pass

# ─── CBC Mode ───
def aes_cbc_encrypt(key, plaintext, iv=None):
    """AES-128-CBC 加密，回傳 IV + ciphertext"""
    if iv is None:
        iv = os.urandom(16)
    padded = pkcs7_pad(plaintext)
    # TODO: CBC chain
    pass

def aes_cbc_decrypt(key, data):
    """AES-128-CBC 解密（data = IV + ciphertext），回傳 plaintext"""
    iv, ct = data[:16], data[16:]
    # TODO: CBC unchain + unpad
    pass
```

### 驗收標準

```python
def test_cbc_roundtrip():
    key = os.urandom(16)
    messages = [
        b'',
        b'A',
        b'Hello, World!',
        b'Exactly 16 bytes',   # 剛好 16 bytes
        b'A' * 31,              # 31 bytes
        b'A' * 32,              # 32 bytes（剛好 2 blocks）
        b'A' * 100,             # 100 bytes
    ]
    for msg in messages:
        ct = aes_cbc_encrypt(key, msg)
        pt = aes_cbc_decrypt(key, ct)
        assert pt == msg, f"FAIL for len={len(msg)}: got {pt!r}"
    print("[Phase 2] CBC roundtrip: ALL PASS")

def test_cbc_against_openssl():
    """用 cryptography 套件驗證手刻結果"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    
    key = bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c')
    iv  = bytes.fromhex('000102030405060708090a0b0c0d0e0f')
    pt  = b'This is a test message for CBC!'
    
    # OpenSSL 版
    padder = padding.PKCS7(128).padder()
    padded = padder.update(pt) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    ct_openssl = enc.update(padded) + enc.finalize()
    
    # 手刻版
    ct_handmade = aes_cbc_encrypt(key, pt, iv=iv)
    ct_handmade = ct_handmade[16:]  # 去掉 prepend 的 IV
    
    assert ct_openssl == ct_handmade, f"Mismatch!\nOpenSSL:  {ct_openssl.hex()}\nHandmade: {ct_handmade.hex()}"
    print("[Phase 2] CBC vs OpenSSL: PASS")
```

## Phase 3：建一個有 Padding Oracle 的 Server

### 要求

用 Flask 建一個 HTTP server，提供兩個 endpoint：

1. `POST /encrypt`：接收 JSON `{"plaintext": "base64..."}` → 加密後回傳 `{"ciphertext": "base64..."}`
2. `POST /decrypt`：接收 JSON `{"ciphertext": "base64..."}` → **如果 padding valid，回傳 200；如果 padding invalid，回傳 400** ← 這就是 oracle

### 骨架

```python
# oracle_server.py
from flask import Flask, request, jsonify
import base64
import os

# 用 Phase 2 的手刻 AES 或 cryptography 套件都行
# 這裡用 cryptography 比較快（Phase 3 的重點是 server，不是 AES）
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

app = Flask(__name__)
SECRET_KEY = os.urandom(16)

@app.route('/encrypt', methods=['POST'])
def encrypt():
    """加密明文"""
    data = request.get_json()
    pt = base64.b64decode(data['plaintext'])
    
    # PKCS7 pad
    pad_len = 16 - (len(pt) % 16)
    padded = pt + bytes([pad_len] * pad_len)
    
    # AES-CBC encrypt
    iv = os.urandom(16)
    cipher = Cipher(algorithms.AES(SECRET_KEY), modes.CBC(iv))
    enc = cipher.encryptor()
    ct = enc.update(padded) + enc.finalize()
    
    result = base64.b64encode(iv + ct).decode()
    return jsonify({'ciphertext': result})

@app.route('/decrypt', methods=['POST'])
def decrypt():
    """解密 — padding oracle 在這裡！"""
    data = request.get_json()
    raw = base64.b64decode(data['ciphertext'])
    iv, ct = raw[:16], raw[16:]
    
    if len(ct) == 0 or len(ct) % 16 != 0:
        return jsonify({'error': 'invalid ciphertext length'}), 400
    
    cipher = Cipher(algorithms.AES(SECRET_KEY), modes.CBC(iv))
    dec = cipher.decryptor()
    pt = dec.update(ct) + dec.finalize()
    
    # 檢查 PKCS7 padding — 這裡洩漏資訊！
    pad_len = pt[-1]
    if pad_len < 1 or pad_len > 16:
        return jsonify({'error': 'invalid padding'}), 400     # ← ORACLE
    
    for i in range(1, pad_len + 1):
        if pt[-i] != pad_len:
            return jsonify({'error': 'invalid padding'}), 400  # ← ORACLE
    
    return jsonify({'status': 'ok'}), 200                       # ← ORACLE

if __name__ == '__main__':
    print(f"[!] Secret key (for debugging): {SECRET_KEY.hex()}")
    app.run(host='127.0.0.1', port=5000, debug=False)
```

### 執行

```bash
# Terminal 1: 啟動 server
python3 oracle_server.py

# Terminal 2: 測試
# 加密
curl -s -X POST http://127.0.0.1:5000/encrypt \
  -H 'Content-Type: application/json' \
  -d '{"plaintext": "'$(echo -n "The quick brown fox" | base64)'"}' | python3 -m json.tool

# 解密（valid padding）
# 把上面得到的 ciphertext 貼過來
curl -s -X POST http://127.0.0.1:5000/decrypt \
  -H 'Content-Type: application/json' \
  -d '{"ciphertext": "PASTE_HERE"}'
# 應該回傳 200

# 修改 ciphertext 一個 byte 再送
# 應該回傳 400（padding invalid）
```

### 驗收標準

- Server 能正常 encrypt/decrypt
- 相同 plaintext 每次 encrypt 結果不同（random IV）
- 修改 ciphertext 的任意 byte 後 decrypt 回傳 400

## Phase 4：Padding Oracle Attack Client

### 要求

寫一個 attack client：

1. 從 server 的 `/encrypt` 取得一段密文
2. **不知道 key**，只靠 `/decrypt` 的 200/400 回應
3. 逐 byte 解密整段密文
4. 顯示攻擊進度和最終解密結果

### 骨架

```python
# oracle_attack.py
import requests
import base64
import sys

SERVER = 'http://127.0.0.1:5000'

def oracle(ciphertext_bytes):
    """送 ciphertext 給 server，回傳 padding 是否 valid"""
    ct_b64 = base64.b64encode(ciphertext_bytes).decode()
    resp = requests.post(f'{SERVER}/decrypt', json={'ciphertext': ct_b64})
    return resp.status_code == 200

def attack_block(prev_block, target_block):
    """解密一個 block（16 bytes）"""
    intermediate = [0] * 16
    
    for byte_pos in range(15, -1, -1):
        pad_value = 16 - byte_pos
        
        # 構造 crafted block
        crafted = bytearray(16)
        for k in range(byte_pos + 1, 16):
            crafted[k] = intermediate[k] ^ pad_value
        
        found = False
        for guess in range(256):
            crafted[byte_pos] = guess
            
            # 送 crafted + target 給 oracle
            test_ct = bytes(crafted) + target_block
            if oracle(test_ct):
                # 防 false positive（最後一 byte 特殊處理）
                if byte_pos == 15:
                    verify = bytearray(crafted)
                    verify[14] ^= 0x01
                    if not oracle(bytes(verify) + target_block):
                        continue
                
                intermediate[byte_pos] = guess ^ pad_value
                found = True
                
                # 進度顯示
                solved = 16 - byte_pos
                sys.stdout.write(f'\r  Block progress: {solved}/16 bytes')
                sys.stdout.flush()
                break
        
        if not found:
            raise RuntimeError(f"Failed at byte_pos={byte_pos}")
    
    print()  # 換行
    
    # 算出 plaintext
    pt_block = bytes([intermediate[i] ^ prev_block[i] for i in range(16)])
    return pt_block

def padding_oracle_attack(ciphertext):
    """完整的 padding oracle 攻擊"""
    blocks = [ciphertext[i:i+16] for i in range(0, len(ciphertext), 16)]
    num_ct_blocks = len(blocks) - 1  # 第一個是 IV
    
    print(f"[*] Ciphertext: {len(ciphertext)} bytes = IV + {num_ct_blocks} blocks")
    print(f"[*] Starting padding oracle attack...")
    
    plaintext = b''
    total_queries = 0
    
    for block_idx in range(1, len(blocks)):
        print(f"[*] Attacking block {block_idx}/{num_ct_blocks}...")
        pt_block = attack_block(blocks[block_idx - 1], blocks[block_idx])
        plaintext += pt_block
    
    # 去 padding
    pad_len = plaintext[-1]
    if 1 <= pad_len <= 16:
        plaintext = plaintext[:-pad_len]
    
    return plaintext

def main():
    # Step 1: 讓 server 加密一段祕密訊息
    secret = b'The magic words are Squeamish Ossifrage -- RSA challenge 1977'
    ct_b64 = requests.post(
        f'{SERVER}/encrypt',
        json={'plaintext': base64.b64encode(secret).decode()}
    ).json()['ciphertext']
    
    ciphertext = base64.b64decode(ct_b64)
    print(f"[*] Got ciphertext ({len(ciphertext)} bytes)")
    
    # Step 2: 攻擊
    recovered = padding_oracle_attack(ciphertext)
    
    print(f"\n[+] Recovered plaintext: {recovered}")
    print(f"[+] Match: {recovered == secret}")

if __name__ == '__main__':
    main()
```

### 進階挑戰

如果 Phase 4 基本版太快寫完：

1. **加速**：用多線程同時攻擊多個 block（block 之間互相獨立）
2. **Timing oracle**：把 server 的 400 response 改成都回 200，但 padding valid 時 `time.sleep(0.01)`。攻擊 client 改用 response time 判斷。
3. **統計**：記錄每個 byte 用了多少次 query，算平均和最差情況。

### 驗收標準

```
$ python3 oracle_server.py &
[!] Secret key (for debugging): a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6

$ python3 oracle_attack.py
[*] Got ciphertext (80 bytes)
[*] Ciphertext: 80 bytes = IV + 4 blocks
[*] Starting padding oracle attack...
[*] Attacking block 1/4...
  Block progress: 16/16 bytes
[*] Attacking block 2/4...
  Block progress: 16/16 bytes
[*] Attacking block 3/4...
  Block progress: 16/16 bytes
[*] Attacking block 4/4...
  Block progress: 16/16 bytes

[+] Recovered plaintext: b'The magic words are Squeamish Ossifrage -- RSA challenge 1977'
[+] Match: True
```

## 完成後的自我檢核

### Phase 1
- [ ] AES-128 手刻 encrypt 通過 FIPS-197 測試向量
- [ ] 能解釋 key expansion 的每一步
- [ ] 知道 state 是 column-major 排列

### Phase 2
- [ ] CBC encrypt/decrypt roundtrip 正確
- [ ] 手刻結果和 OpenSSL 一致
- [ ] PKCS#7 padding 正確處理邊界情況（空字串、剛好對齊）

### Phase 3
- [ ] Server 能 encrypt/decrypt
- [ ] 修改 ciphertext 任一 byte → 400 response
- [ ] 相同 plaintext 每次 encrypt 不同（random IV）

### Phase 4
- [ ] Padding oracle attack 成功解密整段密文
- [ ] 能解釋攻擊的每一步數學（intermediate value、pad_value、XOR 關係）
- [ ] 知道 false positive 的問題和如何處理

## 防禦方法（做完攻擊後回顧）

你剛做完的攻擊在真實世界造成過重大漏洞。防禦方法：

1. **用 AEAD（AES-GCM / ChaCha20-Poly1305）**：先驗證 MAC tag，tag 不對就直接 reject，不走到 padding 檢查
2. **Encrypt-then-MAC**：如果必須用 CBC，先加密再算 HMAC，驗證 HMAC 再解密
3. **Constant-time padding check**：即使做了 MAC，padding 檢查也要 constant-time（防 Lucky Thirteen）
4. **不要回傳不同的 error**：padding error 和 decryption error 回傳一樣的 generic error

```python
# 錯誤示範（你的 oracle server）
if padding_invalid:
    return 400, "invalid padding"    # ← 洩漏資訊

# 正確做法
if mac_invalid or padding_invalid:
    return 400, "decryption failed"  # ← 統一 error，且 constant-time check
```

## 時間預估

| Phase | 預估時間 | 難度 |
|-------|----------|------|
| Phase 1（手刻 AES） | 2-4 小時 | ★★★ 最難（GF(2⁸)、column-major） |
| Phase 2（CBC mode） | 1-2 小時 | ★★ |
| Phase 3（Oracle server） | 30 分鐘 | ★ |
| Phase 4（Attack client） | 1-2 小時 | ★★（數學推導是關鍵） |

---

> **下一個練習**：[練習 B — Hash 與 Length Extension Attack](practice-b-hash-length-extension.md)
