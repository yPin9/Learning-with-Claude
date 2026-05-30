# Ch 17 — 密碼雜湊與 KDF

> 目標：能區分一般 hash 和密碼 hash 的用途差異，理解 PBKDF2 / bcrypt / scrypt / Argon2 的設計取捨，知道 KDF 在密碼學系統中的另一個用途——從 password 衍生 encryption key。

---

## 為什麼需要密碼雜湊

你是一個網站工程師，使用者註冊時給你 password，你要存到資料庫。存什麼？

### 方案一：存明文

```
users 表：
| user  | password     |
|-------|-------------|
| alice | P@ssw0rd123 |
| bob   | hunter2     |
```

資料庫被拖（database breach）→ 所有密碼直接洩漏。2012 年 LinkedIn 被拖 1.17 億帳號，用的是無 salt 的 SHA-1——等效於存明文。

### 方案二：存 SHA-256 hash

```python
import hashlib
stored = hashlib.sha256(b"P@ssw0rd123").hexdigest()
# 驗證時：hashlib.sha256(user_input).hexdigest() == stored
```

看起來對？問題是 **SHA-256 太快了**。

2024 年一張 RTX 4090 每秒能算 ~10 billion 個 SHA-256。一個 8 字元的小寫 + 數字密碼有 36⁸ ≈ 2.8 × 10¹² 個可能——GPU 用暴力法 280 秒跑完。加上 rainbow table（預計算的 hash ↔ password 對照表），破解更快。

**核心問題**：一般的 hash 設計目標是「快」。但密碼 hash 需要「慢」——讓暴力破解的成本高到不值得。

---

## 先建立直覺

密碼 hash 的三個設計目標：

```
1. 故意慢（Slow）
   → 一次驗證要花 100ms 以上，讓暴力破解從「秒破」變成「年破」

2. Memory-hard
   → 需要大量記憶體，讓 GPU/ASIC 並行攻擊的成本暴增
   → GPU 有大量 core 但每個 core 的記憶體很小

3. 加鹽（Salt）
   → 每個使用者的 hash 加不同的隨機 salt
   → 讓 rainbow table 失效（預計算表只對特定 salt 有效）
```

---

## 核心概念：Salt 為什麼重要

### 沒有 Salt 的問題

```python
import hashlib

# 兩個使用者碰巧用同一個密碼
h1 = hashlib.sha256(b"password123").hexdigest()
h2 = hashlib.sha256(b"password123").hexdigest()
assert h1 == h2  # hash 完全一樣！

# 攻擊者看到兩個相同的 hash → 知道這兩個使用者用同一密碼
# 更糟：攻擊者可以用預計算的 rainbow table 直接查
```

### 加 Salt

```python
import hashlib
import os

def hash_password(password: bytes) -> tuple[bytes, str]:
    salt = os.urandom(16)  # 16 bytes 隨機 salt
    h = hashlib.sha256(salt + password).hexdigest()
    return salt, h

salt1, h1 = hash_password(b"password123")
salt2, h2 = hash_password(b"password123")
assert h1 != h2  # 不同 salt → 不同 hash，即使密碼相同
```

Salt 的要求：
- **隨機**：每次生成新的，不能用 username 或其他可預測值
- **足夠長**：至少 16 bytes（128 bit），讓 rainbow table 不可行
- **存在資料庫裡**：salt 不是 secret，它和 hash 一起存

Salt 防的是**預計算攻擊**（rainbow table、lookup table）。它不防暴力破解——攻擊者知道 salt，還是能一個一個試。要防暴力破解，需要「慢」。

---

## PBKDF2：最老的標準

PBKDF2（Password-Based Key Derivation Function 2，RFC 8018）的做法很粗暴：**把 HMAC 做很多次**。

```
DK = PBKDF2(PRF, Password, Salt, c, dkLen)

其中：
  PRF = 偽隨機函式（通常 HMAC-SHA-256）
  c = iteration count（迭代次數）
  dkLen = 輸出長度
```

每次 hash 要做 c 次 HMAC → c = 600,000 時（NIST 2023 建議值），一次驗證要花 ~100ms。

```python
import hashlib
import os
import time

password = b"MySecurePassword2024"
salt = os.urandom(16)

# PBKDF2 with 600000 iterations
start = time.time()
dk = hashlib.pbkdf2_hmac(
    'sha256',       # PRF
    password,       # password
    salt,           # salt
    600_000,        # iterations
    dklen=32        # 輸出 32 bytes
)
elapsed = time.time() - start

print(f"PBKDF2 結果: {dk.hex()}")
print(f"耗時: {elapsed*1000:.0f} ms")
print(f"Salt: {salt.hex()}")
```

### PBKDF2 的問題

PBKDF2 只是「做很多次 HMAC」——計算是純 CPU 運算，不需要記憶體。GPU 有數千個 core 可以平行跑 PBKDF2，每個 core 的記憶體需求幾乎為零。

2024 年一張 RTX 4090 跑 PBKDF2-HMAC-SHA256（c=600,000）：~20,000 hashes/sec。聽起來慢？但 8 張卡就是 160,000/sec。8 字元密碼在幾天內破完。

**PBKDF2 缺 memory-hardness**——這是它最大的弱點。

---

## bcrypt：Blowfish-based

bcrypt（1999，Niels Provos & David Mazières）基於 Blowfish cipher 的 key schedule，設計上就比 PBKDF2 難用 GPU 加速。

```python
# pip install bcrypt
import bcrypt, time

password = b"MySecurePassword2024"
start = time.time()
hashed = bcrypt.hashpw(password, bcrypt.gensalt(rounds=12))
elapsed = time.time() - start
print(f"bcrypt hash: {hashed.decode()}")  # $2b$12$SALT...HASH
print(f"耗時: {elapsed*1000:.0f} ms")
assert bcrypt.checkpw(password, hashed)
```

### bcrypt 的特點

1. **內建 salt**：`bcrypt.gensalt()` 自動生成 128-bit salt，編碼在 hash 字串裡
2. **cost factor**：每增加 1，計算時間翻倍。12 ≈ 250ms，14 ≈ 1s
3. **固定 4 KB 記憶體**：bcrypt 的 Blowfish key schedule 需要 4 KB 的 S-box。GPU 的 cache 很小，每個 thread 要獨立一份 4 KB → 大量平行時 memory bandwidth 成為瓶頸
4. **72 bytes 限制**：bcrypt 只取密碼的前 72 bytes。超過 72 bytes 的部分被截斷

### bcrypt 的缺點

4 KB 在 2024 年不算多。ASIC 可以把 4 KB 的 S-box 硬接線路（hard-wire），讓 bcrypt 的 memory 優勢消失。bcrypt 不是 memory-hard 的——它只是「比 PBKDF2 多用一點記憶體」。

---

## scrypt：Memory-Hard

scrypt（2009，Colin Percival）是第一個 memory-hard 的密碼 hash。

核心想法：**強制使用大量記憶體（通常 16 MB 以上），讓 GPU/ASIC 的平行攻擊成本暴增**。

```
scrypt(P, S, N, r, p, dkLen)

N = CPU/memory cost（必須是 2 的冪，通常 2¹⁴ 到 2²⁰）
r = block size factor（通常 8）
p = parallelization factor（通常 1）
記憶體需求 ≈ 128 × N × r bytes
```

```python
import hashlib, time

password = b"MySecurePassword2024"
salt = b"random_salt_value123"  # 實務上用 os.urandom(16)
start = time.time()
dk = hashlib.scrypt(password, salt=salt, n=2**14, r=8, p=1, dklen=32)
elapsed = time.time() - start
print(f"scrypt: {dk.hex()}, 耗時: {elapsed*1000:.0f} ms")
print(f"記憶體需求: {128 * 2**14 * 8 / (1024*1024):.0f} MB")  # 16 MB
```

### scrypt 的 memory-hardness 原理

scrypt 內部建一個大的記憶體陣列 V[0..N-1]（ROMix），然後做 N 次偽隨機存取——每一步用前一步的結果決定下一個要讀的 index（`j = X mod N`）。不能預測存取模式 → 不能省掉記憶體。如果攻擊者只存一部分 V，就必須重新計算被省掉的 block → 時間成本暴增。

### scrypt 的缺點

scrypt 的 memory-hard 迴圈是 sequential 的（不能多核加速驗證），N/r/p 三個參數交互作用不直觀，且記憶體存取模式洩漏 side-channel 資訊。

---

## Argon2：2015 PHC Winner

Argon2 是 2015 年 Password Hashing Competition（PHC）的冠軍，被認為是目前最佳的密碼 hash 選擇。

### 三個變體

| 變體 | 特點 | 適用場景 |
|------|------|---------|
| Argon2d | data-dependent 記憶體存取 → 最大 GPU resistance | 加密貨幣、不怕 side-channel 的場景 |
| Argon2i | data-independent 記憶體存取 → 抗 side-channel | 密碼 hash（怕 side-channel） |
| Argon2id | 前半 Argon2i + 後半 Argon2d | **推薦的預設選擇** |

### 範例：Argon2id

```python
# pip install argon2-cffi
from argon2 import PasswordHasher
import time

ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4,
                     hash_len=32, type=ph.Type.ID)
password = "MySecurePassword2024"
start = time.time()
hashed = ph.hash(password)
print(f"Argon2id: {hashed}")  # $argon2id$v=19$m=65536,t=3,p=4$SALT$HASH
print(f"耗時: {(time.time()-start)*1000:.0f} ms")
assert ph.verify(hashed, password)
```

### Argon2 的設計優勢

1. **三維參數調控**：時間（t）、記憶體（m）、平行度（p）獨立調整
2. **Memory-hard 且可平行化**：記憶體存取在多個 lane 上平行進行，server 端可以利用多核加速驗證
3. **Side-channel 防護**：Argon2i 和 Argon2id 的前半段用 data-independent 存取模式
4. **PHC 競賽驗證**：經過密碼學社群的公開審查

### Argon2 的參數建議（OWASP 2024）

| 場景 | time_cost | memory_cost | parallelism |
|------|-----------|-------------|-------------|
| 低延遲 | 1 | 65536 (64MB) | 4 |
| 平衡 | 3 | 65536 (64MB) | 4 |
| 高安全 | 4 | 131072 (128MB) | 8 |

原則：先把 memory_cost 調到 server 能承受的最大值，再調 time_cost 讓單次驗證 ≤ 1 秒。

---

## 底層機制：為什麼 Memory-Hardness 有效

### GPU 的架構限制

GPU 有 4096+ cores 但每個 SM 只有 ~48 KB shared memory。CPU 有 4-16 cores 但每 core 可用數十 MB cache/RAM。

- SHA-256 / PBKDF2：每個 GPU core 需要 ~256 bytes state → 4096 cores 只需 1 MB → GPU 完勝
- Argon2（64 MB memory）：每個 core 需要 64 MB → 4096 cores 需要 256 TB → 不可能

GPU 只能跑少數 Argon2 instance（受 VRAM 限制），CPU 少數 core 但每個有足夠記憶體 → GPU 優勢被抹平。

---

## KDF 的另一個用途：衍生加密金鑰

密碼 hash 不是 KDF 的唯一用途。KDF（Key Derivation Function）還用於：

### 從 password 衍生 encryption key

```python
import hashlib, os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

password = b"user-entered-password"
salt = os.urandom(16)
key = hashlib.pbkdf2_hmac('sha256', password, salt, 600_000, dklen=32)

aesgcm = AESGCM(key)
nonce = os.urandom(12)
ciphertext = aesgcm.encrypt(nonce, b"sensitive data", None)

# 解密：同一密碼 + salt → 同一 key
key2 = hashlib.pbkdf2_hmac('sha256', password, salt, 600_000, dklen=32)
assert aesgcm.decrypt(nonce, ciphertext, None) == b"sensitive data"
```

### HKDF：從 shared secret 衍生多個 key

HKDF（HMAC-based Key Derivation Function，RFC 5869）用於 TLS 1.3、Signal Protocol 等需要從一個 shared secret 衍生多個 key 的場景。

```python
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
import os

shared_secret = os.urandom(32)  # DH key exchange 後的 shared secret

client_key = HKDF(
    algorithm=hashes.SHA256(), length=32, salt=None, info=b"client write key",
).derive(shared_secret)

server_key = HKDF(
    algorithm=hashes.SHA256(), length=32, salt=None, info=b"server write key",
).derive(shared_secret)

assert client_key != server_key  # 不同 info → 不同 key
```

HKDF 和密碼 hash 的差別：HKDF 的輸入是高熵的 shared secret，不需要故意做慢。密碼 hash 的輸入是低熵的人類密碼，必須做慢來彌補低熵。

---

## 完整對比表

| 維度 | PBKDF2 | bcrypt | scrypt | Argon2id |
|------|--------|--------|--------|----------|
| 年份 | 2000 | 1999 | 2009 | 2015 |
| Memory-hard | 否 | 否（4 KB） | 是 | 是 |
| GPU resistance | 低 | 中 | 高 | 最高 |
| Side-channel 防護 | N/A | N/A | 弱 | 強（id 模式）|
| 參數靈活度 | 1（iterations） | 1（cost） | 3（N, r, p） | 3（t, m, p） |
| 可平行化驗證 | 弱 | 弱 | 弱 | 強 |
| 最大密碼長度 | 無限制 | 72 bytes | 無限制 | 無限制 |
| 標準化 | NIST SP 800-132 | 無 NIST 標準 | RFC 7914 | RFC 9106 |
| 推薦程度（2024+）| 可接受 | 可接受 | 好 | 最佳 |

### 選擇指引

```
2024+ 新專案 → Argon2id（RFC 9106，OWASP 推薦）
既有系統用 bcrypt → 繼續用，不急著遷移（bcrypt 仍然安全）
需要 NIST 認證 → PBKDF2（SP 800-132，但要用夠高的 iteration）
需要從 secret 衍生 key → HKDF（RFC 5869，不是密碼 hash）
```

---

## 踩雷集錦

### 踩雷 1：用 SHA-256 直接 hash 密碼

SHA-256 太快。一張 GPU 一秒算 10 billion 次。密碼 hash 必須故意慢。

### 踩雷 2：Salt 用 username

Salt 必須是隨機的（`os.urandom(16)`）。用 username 當 salt 意味著同名的使用者在不同網站有相同 salt → 攻擊者可以跨網站做 rainbow table。

### 踩雷 3：bcrypt 截斷 72 bytes 卻不知道

bcrypt 只取密碼的前 72 bytes。如果你允許使用者輸入超長密碼（或者用密碼管理器生成的密碼），超過 72 bytes 的部分被忽略。解法：先 hash password 再 bcrypt——`bcrypt(SHA-256(password))`，但要注意 SHA-256 output 是 32 bytes binary，需要 base64 encode 再 bcrypt。

### 踩雷 4：PBKDF2 的 iteration count 太低

Django 的預設從 36,000（Django 3.0）一路升到 720,000（Django 5.0）。NIST SP 800-132 的 2023 年建議是至少 600,000。如果你的系統用 10,000——太低了，升級。

### 踩雷 5：Argon2 的 memory_cost 太低

Argon2 的安全性核心來自 memory-hardness。如果 memory_cost 設成 1024 KB（1 MB），GPU 輕鬆平行——等於放棄了 Argon2 最大的優勢。至少 64 MB。

---

## 進階

### Pepper：除了 Salt 還有什麼

Pepper 是一個不存在資料庫裡的 secret（存在 HSM、環境變數、或設定檔中）：

```
stored = Argon2id(password, salt, pepper)
```

資料庫被拖時，攻擊者有 salt 但沒有 pepper → 暴力破解不可行。但 pepper 帶來 key management 的複雜度——pepper 遺失 = 所有密碼無法驗證。

### Credential Stuffing 和 Rate Limiting

密碼 hash 防的是「資料庫被拖後的離線破解」。線上攻擊（credential stuffing）要靠 rate limiting、2FA、CAPTCHA 來防。

### Password Hash 遷移策略

如果你要從 bcrypt 遷移到 Argon2：

1. 不能要求所有使用者重新設密碼
2. **wrap 策略**：`Argon2(bcrypt_hash)`——下次使用者登入時，用 bcrypt 驗證舊 hash，然後用 Argon2 重新 hash 明文密碼
3. 資料庫同時存 hash 格式版本號，讓驗證邏輯根據版本選擇演算法

---

## 動手練習

1. **速度比較**：分別用 SHA-256（1 次）、PBKDF2（600,000 iterations）、bcrypt（cost=12）、scrypt（N=2¹⁴）、Argon2id（t=3, m=64MB）hash 同一個密碼，測量各自耗時，做成表格

2. **Rainbow table 破解**：用 SHA-256（無 salt）hash 100 個常見密碼（可以找 `rockyou.txt` 的前 100 個），然後寫一個 rainbow table lookup 把它們全部還原。接著對同樣 100 個密碼加 salt 重做，驗證 rainbow table 失效

3. **HKDF 衍生多個 key**：從一個 shared secret 用 HKDF 衍生 4 個不同的 key（client write key、server write key、client write IV、server write IV），驗證每個 key 都不同

4. **（挑戰）Argon2 參數調優**：寫一個 benchmarking script，固定 time_cost=3，測試 memory_cost 從 1 MB 到 256 MB（每次翻倍）的耗時變化，畫出 memory vs time 的圖

---

## 重點整理

1. **密碼不能用 SHA-256 直接 hash**：SHA-256 太快，GPU 一秒 10 billion 次，暴力破解輕鬆
2. **密碼 hash 三要素**：故意慢、memory-hard、加 salt
3. **PBKDF2 不是 memory-hard**：GPU 可以大量平行。NIST 建議 ≥600,000 iterations
4. **bcrypt 有 72 bytes 限制和固定 4 KB memory**：比 PBKDF2 好，但不是 memory-hard
5. **scrypt 是第一個 memory-hard 密碼 hash**：但有 side-channel 弱點且不能平行驗證
6. **Argon2id 是 2024+ 的最佳選擇**：memory-hard + side-channel 防護 + 可平行化
7. **HKDF 用於從高熵 secret 衍生多個 key**：不要和密碼 hash 搞混——HKDF 的輸入是高熵的，不需要慢

---

## 自我檢核

1. 為什麼 SHA-256 不適合 hash 密碼？數字是什麼？
2. Salt 防的是什麼攻擊？它不防什麼攻擊？
3. Memory-hardness 為什麼能有效對抗 GPU 攻擊？
4. bcrypt 的 72 bytes 限制在什麼場景下會造成問題？
5. HKDF 和 Argon2 的使用場景有什麼不同？輸入的熵有什麼差異？

---

## 延伸閱讀

- **RFC 9106**：Argon2 的完整規格
- **RFC 5869**：HKDF 的完整規格
- **RFC 8018**：PKCS#5 / PBKDF2
- **RFC 7914**：scrypt
- **OWASP Password Storage Cheat Sheet**：密碼 hash 的最新工程建議
- **Boneh, Corrigan-Gibbs, Schechter (2016)**："Balloon Hashing"——有正式 memory-hardness 證明的密碼 hash
- **Serious Cryptography, Ch 7**：密碼 hash 和 KDF 的工程導向介紹

---

## 下一章預告

[練習 B — SHA-256 + Length Extension](./practice-b-sha256-and-length-extension.md)：手刻 SHA-256 + 對自寫 SHA-256 跑 length extension attack + 實作 HMAC-SHA-256 驗證防禦。
