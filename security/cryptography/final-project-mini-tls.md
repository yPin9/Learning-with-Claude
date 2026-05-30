# Final Project — Mini-TLS 1.3：手刻一個能完成 Handshake 的 TLS Client

> **目標**：用 Python 手刻 Mini-TLS 1.3 client，能和真實的 `openssl s_server` 完成 1-RTT handshake + 加密通訊，拿到 HTTP response。

---

## 為什麼做這個？

TLS 1.3 是你在 43 章裡學到的幾乎所有東西的交叉點：

| 課程章節 | 在 TLS 1.3 裡的角色 |
|---|---|
| Ch 9-10 AES | record layer 的 bulk encryption |
| Ch 16-17 HMAC / KDF | HKDF-Extract / HKDF-Expand-Label |
| Ch 22-23 ECC / X25519 | key exchange |
| Ch 25-26 AEAD / AES-GCM | record layer 的加密 + 認證 |
| Ch 34 TLS 1.3 | protocol 的完整流程 |

手刻 Mini-TLS 1.3 是把碎片組裝成可運作系統的過程。每個 byte 的位置、每個 length field 的 encoding、每個 key derivation 的 label 都必須精確。

---

## 驗收標準

```
你的 Mini-TLS 1.3 client 必須：

1. 和 openssl s_server 完成 1-RTT handshake
   → cipher suite: TLS_AES_128_GCM_SHA256
   → key exchange: X25519
   → 不需要 certificate verification（self-signed OK）

2. 用衍生的 application traffic key 加密一個 HTTP GET request
3. 解密 server 的 HTTP response 並印出

不需要：certificate chain 驗證、0-RTT、session resumption、key update
```

---

## 環境準備

```bash
python3 --version          # 需要 3.11+
pip install cryptography   # pyca/cryptography，用於 AES-GCM 和 X25519
openssl version            # 需要 3.x

# 生成自簽證書
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
    -keyout server.key -out server.crt -days 365 -nodes \
    -subj "/CN=localhost"

# 啟動測試 server（另一個 terminal）
openssl s_server -accept 4433 -cert server.crt -key server.key \
    -tls1_3 -ciphersuites TLS_AES_128_GCM_SHA256 -www
```

---

## 里程碑地圖

```
M1: HKDF ─────────────────────────────────────────────┐
M2: AES-128-GCM AEAD ────────────────────────────┐    │
M3: X25519 Key Exchange ─────────────────────┐    │    │
                                             ↓    ↓    ↓
M4: ClientHello ────→ 送出 ────→ M5: 解析 ServerHello
                                             │
                                M6: 衍生 handshake keys
                                             │
                                M7: Finished exchange
                                             │
                                M8: HTTP GET → response
```

---

## M1：HKDF

TLS 1.3 的所有 key derivation 基於 HKDF（RFC 5869）。

```python
import hmac, hashlib

HASH = hashlib.sha256
HASH_LEN = 32

def hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    if not salt:
        salt = b'\x00' * HASH_LEN
    return hmac.new(salt, ikm, HASH).digest()

def hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    n = (length + HASH_LEN - 1) // HASH_LEN
    okm, t = b'', b''
    for i in range(1, n + 1):
        t = hmac.new(prk, t + info + bytes([i]), HASH).digest()
        okm += t
    return okm[:length]

def hkdf_expand_label(secret: bytes, label: str,
                       context: bytes, length: int) -> bytes:
    """TLS 1.3 專用：HkdfLabel encoding"""
    tls_label = b"tls13 " + label.encode()
    hkdf_label = (
        length.to_bytes(2, 'big') +
        bytes([len(tls_label)]) + tls_label +
        bytes([len(context)]) + context
    )
    return hkdf_expand(secret, hkdf_label, length)

def derive_secret(secret: bytes, label: str,
                   messages_hash: bytes) -> bytes:
    return hkdf_expand_label(secret, label, messages_hash, HASH_LEN)
```

用 RFC 5869 Test Case 1 驗證：

```python
ikm  = bytes.fromhex("0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b")
salt = bytes.fromhex("000102030405060708090a0b0c")
info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
prk = hkdf_extract(salt, ikm)
okm = hkdf_expand(prk, info, 42)
assert prk == bytes.fromhex(
    "077709362c2e32df0ddc3f0dc47bba6390b6c73bb50f9c3122ec844ad7c2b3e5")
assert okm == bytes.fromhex(
    "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
    "34007208d5b887185865")
```

---

## M2：AES-128-GCM Record Crypto

每個 TLS 1.3 record 的 nonce = `write_iv XOR padded_seq_num`。

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class TLSRecordCrypto:
    def __init__(self, key: bytes, iv: bytes):
        self.aesgcm = AESGCM(key)  # key: 16 bytes
        self.iv = iv                 # iv: 12 bytes
        self.seq_num = 0

    def _make_nonce(self) -> bytes:
        padded_seq = self.seq_num.to_bytes(12, 'big')
        nonce = bytes(a ^ b for a, b in zip(self.iv, padded_seq))
        self.seq_num += 1
        return nonce

    def encrypt(self, content: bytes, content_type: int) -> bytes:
        """加密成 TLSCiphertext（含 5-byte header）"""
        inner = content + bytes([content_type])  # TLSInnerPlaintext
        encrypted_len = len(inner) + 16           # +16 for GCM tag
        # outer header 偽裝成 application_data (0x17)
        aad = bytes([0x17, 0x03, 0x03]) + encrypted_len.to_bytes(2, 'big')
        ciphertext = self.aesgcm.encrypt(self._make_nonce(), inner, aad)
        return aad + ciphertext

    def decrypt(self, record: bytes) -> tuple[bytes, int]:
        """解密 TLSCiphertext，返回 (content, content_type)"""
        aad, encrypted = record[:5], record[5:]
        inner = self.aesgcm.decrypt(self._make_nonce(), encrypted, aad)
        # 去掉尾部 padding，找 content_type（最後一個非零 byte）
        i = len(inner) - 1
        while i >= 0 and inner[i] == 0:
            i -= 1
        return inner[:i], inner[i]
```

---

## M3：X25519 Key Exchange

```python
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey, X25519PublicKey)

def x25519_keygen() -> tuple[bytes, bytes]:
    priv = X25519PrivateKey.generate()
    return priv.private_bytes_raw(), priv.public_key().public_bytes_raw()

def x25519_shared_secret(my_priv: bytes, their_pub: bytes) -> bytes:
    priv = X25519PrivateKey.from_private_bytes(my_priv)
    pub = X25519PublicKey.from_public_bytes(their_pub)
    return priv.exchange(pub)
```

---

## M4：ClientHello

ClientHello 需要精確的 binary encoding。結構：

```
Record Layer:       0x16 | 0x0301 | Length
Handshake:          0x01 | 3-byte Length
ClientHello body:   0x0303 | Random(32B) | SessionID | CipherSuites | Compression
Extensions:         supported_versions(0x002b) | supported_groups(0x000a)
                    | key_share(0x0033) | signature_algorithms(0x000d)
```

```python
import os, struct

def build_extension(ext_type: int, data: bytes) -> bytes:
    return struct.pack('!HH', ext_type, len(data)) + data

def build_client_hello(x25519_pub: bytes) -> tuple[bytes, bytes]:
    """返回 (complete_record, client_random)"""
    client_random = os.urandom(32)
    session_id = os.urandom(32)
    cipher_suites = struct.pack('!HH', 2, 0x1301)
    compression = bytes([1, 0])

    # Extensions
    ext = b''
    ext += build_extension(0x002b, bytes([2, 0x03, 0x04]))         # TLS 1.3
    ext += build_extension(0x000a, struct.pack('!HH', 2, 0x001d))  # x25519
    ks_entry = struct.pack('!HH', 0x001d, 32) + x25519_pub
    ext += build_extension(0x0033, struct.pack('!H', len(ks_entry)) + ks_entry)
    ext += build_extension(0x000d, struct.pack('!HH', 2, 0x0403))  # ecdsa_sha256

    body = (bytes([0x03, 0x03]) + client_random +
            bytes([len(session_id)]) + session_id +
            cipher_suites + compression +
            struct.pack('!H', len(ext)) + ext)

    hs_msg = bytes([0x01]) + len(body).to_bytes(3, 'big') + body
    record = bytes([0x16, 0x03, 0x01]) + struct.pack('!H', len(hs_msg)) + hs_msg
    return record, client_random
```

---

## M5：解析 ServerHello

從 ServerHello 中提取 `server_random`、`cipher_suite`、`x25519_public`。解析邏輯：

```python
def parse_server_hello(data: bytes) -> dict:
    """data = 完整的 TLS record（含 5-byte header）"""
    result = {}
    o = 5                                              # skip record header
    o += 4                                             # skip hs type + length
    o += 2                                             # skip legacy_version
    result['server_random'] = data[o:o+32]; o += 32
    sid_len = data[o]; o += 1 + sid_len                # skip session_id
    result['cipher_suite'] = struct.unpack('!H', data[o:o+2])[0]; o += 2
    o += 1                                             # skip compression

    ext_total = struct.unpack('!H', data[o:o+2])[0]; o += 2
    ext_end = o + ext_total
    while o < ext_end:
        et = struct.unpack('!H', data[o:o+2])[0]; o += 2
        el = struct.unpack('!H', data[o:o+2])[0]; o += 2
        ed = data[o:o+el]; o += el
        if et == 0x0033:  # key_share
            result['x25519_public'] = ed[4:4+struct.unpack('!H', ed[2:4])[0]]
    result['_consumed'] = 5 + struct.unpack('!H', data[3:5])[0]
    return result

def recv_record(sock) -> bytes:
    header = recv_exact(sock, 5)
    length = struct.unpack('!H', header[3:5])[0]
    return header + recv_exact(sock, length)

def recv_exact(sock, n: int) -> bytes:
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk: raise ConnectionError("Connection closed")
        buf += chunk
    return buf
```

---

## M6：衍生 Handshake Keys

TLS 1.3 key schedule 的核心路徑：

```
              0 (HASH_LEN 全零)
              │
              v
PSK ──→ HKDF-Extract = Early Secret
              │
     Derive-Secret(., "derived", "")
              │
              v
DHE ──→ HKDF-Extract = Handshake Secret
              │
      ┌───────┴───────┐
Derive-Secret         Derive-Secret
(., "c hs traffic",   (., "s hs traffic",
 CH..SH hash)          CH..SH hash)
      │                     │
      v                     v
 client_hs_secret     server_hs_secret
   → key(16B), iv(12B)   → key(16B), iv(12B)
```

```python
def derive_handshake_keys(shared_secret: bytes, hello_hash: bytes) -> dict:
    empty_hash = hashlib.sha256(b'').digest()
    early_secret = hkdf_extract(None, b'\x00' * HASH_LEN)
    derived = derive_secret(early_secret, "derived", empty_hash)
    hs_secret = hkdf_extract(derived, shared_secret)

    c_hs = derive_secret(hs_secret, "c hs traffic", hello_hash)
    s_hs = derive_secret(hs_secret, "s hs traffic", hello_hash)
    return {
        'handshake_secret': hs_secret,
        'client_hs_secret': c_hs, 'server_hs_secret': s_hs,
        'client_handshake_key': hkdf_expand_label(c_hs, "key", b'', 16),
        'client_handshake_iv':  hkdf_expand_label(c_hs, "iv",  b'', 12),
        'server_handshake_key': hkdf_expand_label(s_hs, "key", b'', 16),
        'server_handshake_iv':  hkdf_expand_label(s_hs, "iv",  b'', 12),
    }

def derive_application_keys(hs_secret: bytes, hs_hash: bytes) -> dict:
    empty_hash = hashlib.sha256(b'').digest()
    derived = derive_secret(hs_secret, "derived", empty_hash)
    master = hkdf_extract(derived, b'\x00' * HASH_LEN)
    c_ap = derive_secret(master, "c ap traffic", hs_hash)
    s_ap = derive_secret(master, "s ap traffic", hs_hash)
    return {
        'client_app_key': hkdf_expand_label(c_ap, "key", b'', 16),
        'client_app_iv':  hkdf_expand_label(c_ap, "iv",  b'', 12),
        'server_app_key': hkdf_expand_label(s_ap, "key", b'', 16),
        'server_app_iv':  hkdf_expand_label(s_ap, "iv",  b'', 12),
    }
```

---

## M7：Finished Message

```
verify_data = HMAC-SHA256(finished_key, transcript_hash)
finished_key = HKDF-Expand-Label(base_key, "finished", "", HASH_LEN)
```

```python
def compute_finished(base_key: bytes, transcript_hash: bytes) -> bytes:
    finished_key = hkdf_expand_label(base_key, "finished", b'', HASH_LEN)
    return hmac.new(finished_key, transcript_hash, HASH).digest()

def build_finished_message(verify_data: bytes) -> bytes:
    return bytes([0x14]) + len(verify_data).to_bytes(3, 'big') + verify_data
```

---

## M8：完整的 Handshake + HTTP GET

把 M1-M7 組裝成 `MiniTLS13` class。核心流程：

```python
"""用法：python3 mini_tls.py"""
import socket, hashlib, struct

class MiniTLS13:
    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self.sock = None
        self.transcript = b''  # handshake messages（不含 record header）

    def _add_transcript(self, hs_msg: bytes):
        self.transcript += hs_msg

    def _transcript_hash(self) -> bytes:
        return hashlib.sha256(self.transcript).digest()

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))

        # 1. X25519 keygen
        my_priv, my_pub = x25519_keygen()

        # 2. ClientHello → 送出
        ch_record, _ = build_client_hello(my_pub)
        self.sock.sendall(ch_record)
        self._add_transcript(ch_record[5:])  # 不含 record header

        # 3. ServerHello → 解析
        sh_record = recv_record(self.sock)
        sh = parse_server_hello(sh_record)
        self._add_transcript(sh_record[5:])

        # 4. Shared secret + handshake keys
        shared = x25519_shared_secret(my_priv, sh['x25519_public'])
        hs_keys = derive_handshake_keys(shared, self._transcript_hash())
        server_dec = TLSRecordCrypto(
            hs_keys['server_handshake_key'], hs_keys['server_handshake_iv'])

        # 5. 接收加密的 server handshake messages
        #    忽略 ChangeCipherSpec（0x14），解密 0x17 records
        #    直到收到 Finished (type=0x14 inside handshake)
        while True:
            record = recv_record(self.sock)
            if record[0] == 0x14: continue  # ChangeCipherSpec
            content, inner_ct = server_dec.decrypt(record)
            if inner_ct == 22:  # handshake
                # 把解密的 handshake content 加入 transcript
                self._add_transcript(content)
                # 掃描是否包含 Finished（type byte = 0x14）
                if self._contains_finished(content): break

        # 6. 衍生 application keys
        hs_hash = self._transcript_hash()
        app_keys = derive_application_keys(hs_keys['handshake_secret'], hs_hash)

        # 7. 送出 client Finished
        verify = compute_finished(hs_keys['client_hs_secret'], hs_hash)
        client_enc = TLSRecordCrypto(
            hs_keys['client_handshake_key'], hs_keys['client_handshake_iv'])
        self.sock.sendall(
            client_enc.encrypt(build_finished_message(verify), 22))

        # 8. 建立 application 加密/解密器
        self.app_enc = TLSRecordCrypto(
            app_keys['client_app_key'], app_keys['client_app_iv'])
        self.app_dec = TLSRecordCrypto(
            app_keys['server_app_key'], app_keys['server_app_iv'])

    def _contains_finished(self, content: bytes) -> bool:
        o = 0
        while o < len(content):
            if content[o] == 0x14: return True
            msg_len = int.from_bytes(content[o+1:o+4], 'big')
            o += 4 + msg_len
        return False

    def send_http_get(self, path: str = "/") -> str:
        http_req = (f"GET {path} HTTP/1.1\r\n"
                    f"Host: {self.host}\r\nConnection: close\r\n\r\n").encode()
        self.sock.sendall(self.app_enc.encrypt(http_req, 23))
        resp = b''
        while True:
            try:
                record = recv_record(self.sock)
                content, ct = self.app_dec.decrypt(record)
                if ct == 23: resp += content
                elif ct == 21: break  # alert
            except Exception: break
        return resp.decode('utf-8', errors='replace')

    def close(self):
        if self.sock: self.sock.close()

if __name__ == "__main__":
    client = MiniTLS13("localhost", 4433)
    try:
        client.connect()
        print(client.send_http_get("/")[:2000])
    finally:
        client.close()
```

---

## Debug 指南

```
問題 1: ServerHello 解析失敗
  → 用 Wireshark 抓包，確認 byte 偏移
  → openssl s_server -trace 看 server 端 debug output

問題 2: Key 衍生結果不對
  → openssl s_server -keylogfile keys.log
  → 和你衍生的 secret 逐一比對
  → 最常見的錯：transcript 多算或少算了某個 message

問題 3: GCM decryption 失敗
  → 檢查 nonce（seq_num 必須 big-endian pad 到 12 bytes）
  → 檢查 AAD（record header 的 length 要包含 GCM tag 的 16 bytes）
  → 確認 client/server key 沒搞反

問題 4: Finished 失敗
  → server Finished 的 verify_data 用的 transcript 不含 Finished 本身
  → client Finished 的 verify_data 用的 transcript 包含 server Finished
```

**Wireshark 解密**：`openssl s_server -keylogfile keys.log` 產生 key log，在 Wireshark 的 TLS 設定裡指向這個檔案，就能看到解密後的所有 handshake message。

---

## 進階挑戰（選做）

1. **Certificate 驗證**：解析 Certificate message，用 CA cert 驗證 signature chain
2. **多 cipher suite**：加入 TLS_CHACHA20_POLY1305_SHA256（0x1303）
3. **連接真實 server**：連 `https://example.com:443`，需要 SNI extension + cert 驗證
4. **SSLKEYLOGFILE 對照**：驗證你衍生的每一把 key 都和 server export 的一致

---

## 評分標準

```
基本要求（80 分）：
  □ M1: HKDF 通過 RFC 5869 test vector            (10)
  □ M2: AES-128-GCM encrypt/decrypt 正確           (10)
  □ M3: X25519 key exchange 正確                    (5)
  □ M4: ClientHello 被 openssl s_server 接受        (15)
  □ M5: ServerHello 正確解析                         (10)
  □ M6: Handshake key 衍生正確                       (15)
  □ M7: Finished exchange 完成                       (10)
  □ M8: HTTP GET/response 成功                       (5)

加分項（20 分）：
  □ Certificate 驗證                                 (5)
  □ 連接真實 HTTPS server                            (5)
  □ 清楚的 error handling + debug output             (5)
  □ SSLKEYLOGFILE 對照驗證所有 derived keys          (5)
```

---

## 踩雷集錦

### 雷 1：Transcript 的範圍搞錯

```
transcript 只包含 handshake message（type + 3-byte length + body）
✗ 不包含 record layer header（5 bytes）
✗ 不包含 ChangeCipherSpec
✗ server Finished 的 verify_data 用的 transcript 不含 Finished 本身
✗ 忘了把加密的 EncryptedExtensions / Certificate / CertificateVerify 加入
```

### 雷 2：Nonce 的 byte order

`seq_num` 必須 **big-endian** pad 到 12 bytes 再和 `write_iv` XOR。用 little-endian 會讓所有 nonce 都錯。

### 雷 3：Client / Server key 搞反

Client 用 `client_key` 加密、`server_key` 解密。反過來就全錯。handshake 和 application 階段各有一組。

### 雷 4：加密 record 的 outer ContentType

加密後的 record 的 **outer** ContentType 是 0x17（application_data），不是 0x16（handshake）。Inner ContentType 才標示真正的 message 類型。這是 TLS 1.3 防止 middlebox 干擾的偽裝。

### 雷 5：AES-128-GCM 的長度

```
key = 16 bytes（不是 32）
iv  = 12 bytes
tag = 16 bytes（自動附在 ciphertext 後面）
```

---

## 重點整理

```
Mini-TLS 1.3 核心流程：

 1. TCP connect
 2. X25519 keygen → (priv, pub)
 3. build ClientHello（含 pub）→ 送出
 4. recv ServerHello → 解析 server pub
 5. X25519 shared secret
 6. HKDF key schedule → handshake keys
 7. 解密 server EncryptedExtensions / Certificate /
    CertificateVerify / Finished
 8. 驗證 server Finished
 9. 計算 client Finished → 加密送出
10. HKDF key schedule → application keys
11. 加密 HTTP GET → 送出
12. 解密 HTTP response

detail hell 的三大重災區：
  transcript hash 的範圍必須精確
  nonce = write_iv XOR padded_seq_num（big-endian）
  client/server key 方向不能搞反
```

---

## 延伸閱讀

- **RFC 8446：The Transport Layer Security (TLS) Protocol Version 1.3**
  - **讀哪裡**：Section 4（Handshake Protocol）和 Section 7（Cryptographic Computations）
  - **學什麼**：所有 byte-level 細節的權威來源

- **"The Illustrated TLS 1.3 Connection"（tls13.xargs.org）**
  - **讀哪裡**：全文——每個 byte 都有圖解
  - **學什麼**：debug 時逐 byte 對照的最佳參考

- **RFC 5869：HMAC-based Extract-and-Expand Key Derivation Function (HKDF)**
  - **讀哪裡**：Section 2 + Appendix A（test vectors）
  - **學什麼**：驗證你的 M1 實作

---

祝你成功打造你的 Mini-TLS 1.3。當你在 terminal 上看到從加密通道裡解出來的 HTTP response 時，你就真的「從 modular arithmetic 到 TLS handshake」走完了一整條路。
