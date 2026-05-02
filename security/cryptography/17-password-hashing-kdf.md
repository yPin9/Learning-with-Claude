# Ch 17 — 密碼雜湊與 KDF：PBKDF2、bcrypt、scrypt、Argon2

> 目標：搞懂為什麼存密碼**不能**直接 SHA-256（GPU 一秒跑幾十億次）、PBKDF2 / bcrypt 的 work factor 概念、scrypt / Argon2 的 memory-hard 設計、HKDF 的 extract-then-expand pattern。

## 為什麼一般 hash 不適合存密碼

```python
# 危險寫法
hashed = hashlib.sha256(password).hexdigest()
```

問題：

1. **GPU brute force 極快**：RTX 4090 跑 SHA-256 約 100 GH/s（10¹¹/秒）
2. **沒 salt → rainbow table 攻擊**：預先算常見密碼 hash，直接查表
3. **同密碼同 hash → 一次破多個帳號**

**密碼專用 hash 必須**：

- **慢**（故意）：每次計算 100 ms 以上
- **salt**：每用戶獨立 salt 防 rainbow table
- **memory-hard**（理想）：GPU 沒大量 memory 優勢消失

## PBKDF2：iteration-based

RFC 2898（2000）。原理：**反覆做 HMAC**：

```
PBKDF2(password, salt, iterations, dkLen) =
    T_1 || T_2 || ... || T_l   （取前 dkLen byte）

其中 T_i = F(password, salt, iterations, i)
F = U_1 XOR U_2 XOR ... XOR U_iter
U_1 = HMAC(password, salt || INT(i))
U_j = HMAC(password, U_{j-1})
```

迭代數 (`iterations`) = work factor。NIST SP 800-132 建議至少 1000，**現在實務 ≥ 600,000**（OWASP 2024 推薦）。

```python
import hashlib
import hmac

def pbkdf2_hmac_sha256(password, salt, iterations, dk_len=32):
    blocks = []
    for block_index in range(1, (dk_len + 31) // 32 + 1):
        u = hmac.new(password, salt + block_index.to_bytes(4, 'big'), hashlib.sha256).digest()
        result = u
        for _ in range(iterations - 1):
            u = hmac.new(password, u, hashlib.sha256).digest()
            result = bytes(a ^ b for a, b in zip(result, u))
        blocks.append(result)
    return b''.join(blocks)[:dk_len]

# 或用內建
import hashlib
key = hashlib.pbkdf2_hmac('sha256', b'password', b'salt', 600000, 32)
```

**PBKDF2 弱點：不是 memory-hard**。GPU / ASIC 仍能加速幾百倍。

## bcrypt：Blowfish-based

OpenBSD 1999 提（Provos & Mazières）。基於 Blowfish 的 expensive key schedule：

```
bcrypt(password, salt, cost) → 60-byte string

cost = log2(iterations)，常見 10-14
cost=12 約 250 ms（普通 CPU）
```

**設計 trick**：bcrypt 內部用大量 4 KB S-box，**對 GPU 不友善**（GPU 慢於 CPU 在這個 workload）。

但 bcrypt：

- **input password 限 72 byte**（Blowfish key size 上限）
- **沒有現代 memory-hard 保護**

```python
import bcrypt

salt = bcrypt.gensalt(rounds=12)
hashed = bcrypt.hashpw(b"password", salt)
# hashed = b"$2b$12$<22-char-salt><31-char-hash>"

# verify
ok = bcrypt.checkpw(b"password", hashed)
```

bcrypt 用了 25 年，**仍可接受**但不是首選。

## scrypt：memory-hard 開創

Percival 2009 設計。**首個明確 memory-hard 的密碼 hash**：

```
scrypt(password, salt, N, r, p, dkLen)
  N = CPU/memory cost (建議 2¹⁵ - 2²⁰)
  r = block size factor (8)
  p = parallelization (1)
```

內部用 `ROMix` 算法：

1. 把 PBKDF2(password) 當 seed
2. 在 memory 中產生 N 個 chunk
3. 隨機讀寫這 N 個 chunk
4. 最後 hash 整體

**memory 需求 = O(N × r × 128 byte)**。N=2²⁰, r=8 約 1 GB memory。

**好處**：GPU、ASIC、FPGA 因 memory bandwidth 不足而慢下來。

scrypt 用於 Litecoin（PoW）、Tarsnap（備份）、Blockstack。

```python
import hashlib

key = hashlib.scrypt(
    password=b'password',
    salt=b'salt',
    n=2**14, r=8, p=1,
    dklen=32,
    maxmem=2**26
)
```

## Argon2：當代最佳

Password Hashing Competition 2013-2015 的勝者。Biryukov / Dinu / Khovratovich 設計。**RFC 9106 (2021) 標準化**。

三個變體：

- **Argon2d**：data-dependent memory access（抗 GPU 最強，但有 side-channel 風險）
- **Argon2i**：data-independent（抗 side-channel）
- **Argon2id**：先 i 再 d（混合，**推薦**）

參數：

- **m**：memory cost（KiB）
- **t**：time cost（iterations）
- **p**：parallelism（threads）

OWASP 2024 推薦：**Argon2id, m=19456 (~19 MB), t=2, p=1**。

```python
from argon2 import PasswordHasher

ph = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
hashed = ph.hash("password")
# hashed = "$argon2id$v=19$m=19456,t=2,p=1$<salt>$<hash>"

try:
    ph.verify(hashed, "password")
except argon2.exceptions.VerifyMismatchError:
    print("wrong password")
```

## 對照與選擇

```
2024 推薦排名（密碼儲存）：
  1. Argon2id (OWASP 第一推薦)
  2. scrypt (仍可接受)
  3. bcrypt (legacy 系統)
  4. PBKDF2-SHA256 (FIPS 兼容需求才用，效能不及 Argon2)

絕對不要用：
  - SHA-256 / SHA-512 直接 hash
  - MD5 / SHA-1 任何形式
  - 自己 roll
```

實務 library：

- Python: `argon2-cffi`、`bcrypt`
- Java: `bouncycastle` Argon2
- Go: `golang.org/x/crypto/argon2`
- Rust: `argon2`、`bcrypt`

## HKDF：另一種 KDF

PBKDF2/bcrypt/Argon2 都假設 input 是「**low-entropy password**」，故意慢。

但很多場景 input 已是 high-entropy（DH shared secret、隨機 nonce）— **不需要慢**，要的是「**從一個 key 衍生多個 key**」。

**HKDF (HMAC-based KDF)** by Krawczyk 2010, RFC 5869：

```
HKDF = Extract + Expand

Extract: PRK = HMAC(salt, IKM)        # Pseudo-Random Key
Expand:  OKM = HMAC chain with PRK + info + counter
```

用法：

```python
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

shared_secret = b"some 32-byte high entropy key"
hkdf = HKDF(
    algorithm=hashes.SHA256(),
    length=64,           # 想要 64 byte output
    salt=b"salt",         # public salt or None
    info=b"context info"  # 區分不同用途
)
keys = hkdf.derive(shared_secret)
enc_key = keys[:32]
mac_key = keys[32:]
```

**HKDF 是 TLS 1.3 的核心 KDF**（從 master secret 衍生 traffic key、IV、Finished key）。

差別：

| | PBKDF2/Argon2 | HKDF |
|---|---|---|
| Input | low-entropy password | high-entropy key |
| 設計目標 | 慢（防 brute force） | 快（衍生多 key） |
| 用途 | 密碼儲存 | TLS / Signal / Noise key derivation |

**用對 KDF 是工程紀律**。把 HKDF 用在密碼上 → 攻擊者快速 brute force；把 Argon2 用在 TLS → 每個 connection 慢 100 ms。

## Salt 與 Pepper

- **Salt**：每用戶獨立、隨機、儲存與 hash 一起
- **Pepper**：全系統共享 secret，**不存** DB（存 KMS / env var）

```
DB:  user, salt, hash = Argon2(password, salt + pepper)
KMS: pepper
```

DB leak → attacker 仍要找 pepper（在 KMS 裡）。增加一層防禦。

OWASP 推薦：**production 系統用 pepper**，不只是 salt。

## 一個常見誤解

「我用 SHA-256 加 salt 是不是就安全？」

**只解決 rainbow table**，沒解決 **brute force**。SHA-256 太快 — GPU 對特定 (salt, hash) 仍能秒試十億次密碼。

要安全的密碼儲存：**慢 + salt + (optional) pepper**。Argon2id 是 2024 的標準答案。

## 自我檢核

- [ ] 我能解釋為什麼 SHA-256 不適合存密碼
- [ ] 我能寫 PBKDF2-HMAC-SHA256 並設定合理 iteration
- [ ] 我能說出 bcrypt 對 GPU 不友善的設計巧思
- [ ] 我能解釋 scrypt 與 Argon2 的 memory-hard 概念
- [ ] 我能說出 PBKDF2 / HKDF 兩者用途差別
- [ ] 我知道 OWASP 2024 推薦 Argon2id 與其參數

到這裡 Part 4 章節結束。下一個是練習 B — 手刻 SHA-256 + HMAC，並對自己寫的 SHA-1 跑 length extension attack。

→ [練習 B：SHA-256 + length extension](./practice-b-sha256-and-length-extension.md)
