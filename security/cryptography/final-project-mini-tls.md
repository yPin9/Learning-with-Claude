# Final Project — 手刻 Mini-TLS 1.3，與真實 OpenSSL server 互通

> 目標：把整門課集大成。從零刻一個能跟真實 OpenSSL server 互通的 mini-TLS 1.3 client（與一個簡化的 server）— 自寫 X25519 ECDH key exchange、ed25519 簽章驗證、AES-128-GCM AEAD、HKDF schedule、ClientHello / ServerHello / Finished 全套訊息流。互通性測試後，再做一個 nonce reuse demo：故意讓自己 server 重複 nonce，自己當 attacker 解密看到 plaintext。

## 任務規格

```
Project:        mini-tls-1.3
Hardware:       任意 Linux / macOS
Toolchain:      Python 3.11+ + cryptography library（驗證對照用）
                + 自己 implement 的 module（核心交付物）
Lines:          ~ 1500 lines (含 test)

Deliverables:
1. tls_client.py    — 能連 openssl s_server 的 mini-TLS 1.3 client
2. tls_server.py    — 簡化 server（接 mini-client + 真 client）
3. nonce_reuse_demo.py — 故意 nonce reuse 看資訊洩漏
4. test_interop.sh  — 互通測試 script
5. writeup.md       — 整體架構與遇到的坑
```

## 期望輸出

```bash
# Test 1: mini-client → openssl s_server
$ openssl s_server -cert server.crt -key server.key -tls1_3 -port 4443 &
$ python tls_client.py localhost 4443
[*] ClientHello sent
[*] ServerHello received (key_share: x25519)
[*] Certificate verified (CN=localhost)
[*] Finished verified
[+] Handshake complete
[+] Encrypted message: "GET / HTTP/1.1\r\n\r\n"
[+] Server response: "HTTP/1.1 200 OK..."

# Test 2: real curl → mini-server
$ python tls_server.py 4444 &
$ curl --tlsv1.3 -k https://localhost:4444/
[+] mini-server received curl request
[+] sent encrypted response

# Test 3: nonce reuse demo
$ python nonce_reuse_demo.py
[!] Server using STATIC nonce (intentionally bug)
[*] sending message 1 ciphertext: aabbcc...
[*] sending message 2 ciphertext: ddeeff...
[!] attacker captured both
[+] XOR analysis recovers plaintext: "GET /admin"
```

## 實作步驟建議

### Milestone 1：Crypto primitive

從 cryptography library 取必要 primitive（不自己 reimplement，但要懂內部）：

```python
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey, X25519PublicKey
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF, HKDFExpand
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.x509 import load_der_x509_certificate
```

確認你能 X25519 ECDH、AES-GCM encrypt/decrypt、HKDF derive。

### Milestone 2：HKDF Schedule

實作 TLS 1.3 的 key schedule：

```python
def hkdf_extract(salt, ikm):
    return hmac.new(salt, ikm, hashlib.sha256).digest()

def hkdf_expand_label(secret, label, context, length):
    full_label = b"tls13 " + label
    info = (length.to_bytes(2, 'big') 
            + len(full_label).to_bytes(1, 'big') + full_label
            + len(context).to_bytes(1, 'big') + context)
    return HKDFExpand(algorithm=hashes.SHA256(), length=length, info=info).derive(secret)

def derive_secret(secret, label, transcript):
    transcript_hash = hashlib.sha256(transcript).digest()
    return hkdf_expand_label(secret, label, transcript_hash, 32)
```

完整 schedule（從 PSK + DH 派生 handshake / master secrets）。

### Milestone 3：ClientHello

序列化 TLS 1.3 ClientHello message：

```python
def build_client_hello(client_random, x25519_pub):
    """build TLS 1.3 ClientHello"""
    # legacy_version (TLS 1.2 = 0x0303 for compatibility)
    legacy_version = b'\x03\x03'
    # 32-byte random
    # legacy_session_id (empty for TLS 1.3)
    # cipher_suites: TLS_AES_128_GCM_SHA256
    # legacy_compression_methods (null)
    # extensions:
    #   supported_versions: TLS 1.3
    #   key_share: x25519
    #   signature_algorithms: rsa_pss_pss_sha256, ed25519, ecdsa_p256_sha256
    #   supported_groups: x25519
    #   server_name (SNI): "localhost"
    
    extensions = build_extensions(x25519_pub)
    body = (legacy_version + client_random + b'\x00'  # session_id len
            + b'\x00\x02\x13\x01'  # cipher suite TLS_AES_128_GCM_SHA256
            + b'\x01\x00'  # compression
            + len(extensions).to_bytes(2, 'big') + extensions)
    
    handshake = b'\x01' + len(body).to_bytes(3, 'big') + body  # type=1
    record = b'\x16\x03\x01' + len(handshake).to_bytes(2, 'big') + handshake
    return record
```

每個 byte 對 TLS 1.3 spec 看 RFC 8446 sections 4.1.2 and 4.2。

### Milestone 4：解析 ServerHello

```python
def parse_server_hello(data):
    """從 record 取出 ServerHello 欄位"""
    # record header (5 byte): type, version, length
    record_type = data[0]
    assert record_type == 0x16  # handshake
    record_len = int.from_bytes(data[3:5], 'big')
    handshake = data[5:5+record_len]
    
    # handshake header
    msg_type = handshake[0]
    assert msg_type == 0x02  # ServerHello
    msg_len = int.from_bytes(handshake[1:4], 'big')
    body = handshake[4:4+msg_len]
    
    # parse body
    server_random = body[2:34]
    session_id_len = body[34]
    cursor = 35 + session_id_len
    cipher_suite = body[cursor:cursor+2]
    cursor += 2 + 1  # cipher + compression
    extensions_len = int.from_bytes(body[cursor:cursor+2], 'big')
    cursor += 2
    
    # parse extensions：找 key_share (0x33) 取 server x25519 pub
    extensions = body[cursor:cursor+extensions_len]
    server_pubkey = parse_key_share(extensions)
    
    return server_random, cipher_suite, server_pubkey
```

### Milestone 5：DH + Schedule

```python
# 算 shared secret
shared = client_priv.exchange(server_pub)

# Build transcript
transcript = client_hello_body + server_hello_body
transcript_hash = hashlib.sha256(transcript).digest()

# Schedule
early_secret = hkdf_extract(b'\x00' * 32, b'\x00' * 32)
empty_hash = hashlib.sha256(b'').digest()
derived_for_handshake = hkdf_expand_label(early_secret, b"derived", empty_hash, 32)
handshake_secret = hkdf_extract(derived_for_handshake, shared)

client_handshake_secret = derive_secret(handshake_secret, b"c hs traffic", transcript)
server_handshake_secret = derive_secret(handshake_secret, b"s hs traffic", transcript)

client_handshake_key = hkdf_expand_label(client_handshake_secret, b"key", b'', 16)
client_handshake_iv = hkdf_expand_label(client_handshake_secret, b"iv", b'', 12)
server_handshake_key = hkdf_expand_label(server_handshake_secret, b"key", b'', 16)
server_handshake_iv = hkdf_expand_label(server_handshake_secret, b"iv", b'', 12)
```

### Milestone 6：解 ServerHello 之後的加密 message

server 後面的 message（EncryptedExtensions、Certificate、CertificateVerify、Finished）都用 `server_handshake_key/iv` 加密。

```python
def decrypt_handshake_record(server_handshake_key, server_handshake_iv, record):
    # record format: type(1) + version(2) + length(2) + ciphertext
    ciphertext = record[5:]
    aesgcm = AESGCM(server_handshake_key)
    nonce = xor_bytes(server_handshake_iv,
                      record_seq.to_bytes(12, 'big'))
    aad = record[:5]  # the record header
    plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
    record_seq += 1
    return plaintext
```

`plaintext` 含一個 inner type byte + actual handshake message。

### Milestone 7：驗 Certificate + CertificateVerify

```python
# Parse Certificate message
cert_chain = parse_certificate_message(decrypted)
server_cert = cert_chain[0]

# Verify CertificateVerify
def verify_cert_verify(server_cert, signature, transcript):
    # build sig_input
    prefix = b' ' * 64 + b"TLS 1.3, server CertificateVerify\x00"
    transcript_hash = hashlib.sha256(transcript).digest()
    sig_input = prefix + transcript_hash
    
    # 用 cert pub key 驗
    pub_key = server_cert.public_key()
    pub_key.verify(signature, sig_input, padding.PSS(...), hashes.SHA256())
```

### Milestone 8：算 Finished + 送 client Finished

```python
# Server Finished
finished_key = hkdf_expand_label(server_handshake_secret, b"finished", b'', 32)
expected_server_finished = hmac.new(finished_key, transcript_hash, hashlib.sha256).digest()

# 與 server 送的 Finished 比對

# Client Finished
client_finished_key = hkdf_expand_label(client_handshake_secret, b"finished", b'', 32)
client_finished = hmac.new(client_finished_key, transcript_hash, hashlib.sha256).digest()

# 加密送出
```

### Milestone 9：application keys + send/recv data

```python
# Master secret
derived = hkdf_expand_label(handshake_secret, b"derived", empty_hash, 32)
master_secret = hkdf_extract(derived, b'\x00' * 32)

# Application keys
client_app_secret = derive_secret(master_secret, b"c ap traffic", transcript)
server_app_secret = derive_secret(master_secret, b"s ap traffic", transcript)
client_app_key = hkdf_expand_label(client_app_secret, b"key", b'', 16)
client_app_iv = hkdf_expand_label(client_app_secret, b"iv", b'', 12)
# ... server keys

# 用這些 send GET request、receive HTTP response
```

### Milestone 10：mini-server

對稱結構，但 server 端要：

- 載入 cert + private key
- 響應 ClientHello（產自己 x25519 pub、選 cipher suite）
- 簽 transcript
- 送加密 messages

可以先做 client，再用 client 與 openssl s_server 互通驗證；做完 server 後用 mini-client 與 mini-server 互通。

### Milestone 11：nonce reuse demo

故意把 server 改成用 static nonce：

```python
# Vulnerable server
def encrypt_app_data_VULNERABLE(self, plaintext):
    # 不應該這樣寫！這裡刻意 nonce reuse
    static_nonce = b'\x00' * 12
    return self.aesgcm.encrypt(static_nonce, plaintext, b'')
```

attacker:

```python
# 收兩個 ciphertext
ct1 = mini_server.encrypt(b"GET /admin")
ct2 = mini_server.encrypt(b"GET /public")

# attacker 算 ct1 XOR ct2
# = (m1 XOR keystream) XOR (m2 XOR keystream)
# = m1 XOR m2

# 用滑動猜詞還原 m1, m2
xored = bytes(a ^ b for a, b in zip(ct1, ct2))
# 假設 m1 與 m2 都是 ASCII，且其中一個含 "GET /"
recovered = xor_with_guess(xored, b"GET /")
```

## 完整參考解答

非常大型 project，full reference solution 約 1500 行。**最好自己寫過再對照**。

可參考開源實作（學完看，不要先抄）：

- **pure Python TLS 實作**：`tlslite-ng`（github），但太大
- **教學用**：搜「TLS 1.3 from scratch python」找 blog post + sample code

## 測試用例

1. **mini-client + openssl s_server**：能完成 handshake + 收一條 HTTP response
2. **mini-server + curl --tlsv1.3**：curl 能成功連、看到 mini-server 響應
3. **mini-client + mini-server**：自家互通
4. **wrong cert**：server 給錯 cert → client 拒絕
5. **modified ServerHello**：MITM 改 server x25519 pub → handshake 失敗（Finished MAC 不對）
6. **Nonce reuse demo**：能還原至少一個 plaintext

## 自我檢核（最終 boss）

- [ ] 我能寫並 send 完整 ClientHello（含 extensions）
- [ ] 我能 parse ServerHello、Certificate、CertificateVerify、Finished
- [ ] 我能跑 HKDF schedule 派生 handshake / application keys
- [ ] 我能用 AES-128-GCM 加密 / 解密 record
- [ ] 我能驗 server cert（chain to known root）
- [ ] 我能驗 CertificateVerify 簽章
- [ ] 我能驗 Server Finished 與送 Client Finished
- [ ] 我能與真實 openssl s_server 互通
- [ ] 我能 demo nonce reuse 還原 plaintext
- [ ] 我寫了 writeup.md 紀錄整個 process（含遇到的坑）

完成後請考慮把 project push 到 GitHub。**這是 senior security engineer 履歷的閃光點**。

## 後話

整門密碼學課到此結束。你寫過：

- Caesar / Vigenère 攻擊與破譯
- AES-128 從零（含 GF(2⁸) 數學）
- Padding oracle attack
- SHA-256 + HMAC + length extension
- RSA + Wiener / Hastad 攻擊
- Kyber-512 simplified
- Mini-TLS 1.3 與互通

從古典到 post-quantum，從學術到 production，從 attack 到 defense。**這個 portfolio 已經比多數 working security engineer 還深**。

接下來有興趣往：

- **學術**：讀 Boneh-Shoup textbook、上 IACR ePrint
- **應用密碼學**：CryptoHack 全做、Cryptopals 全做、貢獻 libsodium / ring / cryptography
- **PQC**：研讀 Kyber/Dilithium/SPHINCS+ paper、liboqs 內部
- **協定**：TLS 1.3 / Signal / Noise spec 細讀
- **CTF / pentest**：DEF CON CTF、InCTF crypto tracks

學密碼學不是學一個技能，是學「**怎麼從根本不信任攻擊者**」這種思維。希望這 42 章 + 4 練習 + 1 final 值得你的時間。

— 課程結束 —
