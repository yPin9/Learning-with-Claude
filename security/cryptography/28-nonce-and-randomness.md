# Ch 28 — Nonce 與隨機性正確使用

> 目標：把「nonce 怎麼產、隨機性從哪來」一次釐清。`/dev/urandom` vs `/dev/random` 的真相、CSPRNG（Fortuna、ChaCha20-based、Linux getrandom()）、Sony PS3 ECDSA 用 const nonce 災難、Debian OpenSSL CVE-2008-0166（兩年內幾乎所有 Debian 機器 SSH key 可預測）。

## Nonce vs IV vs Counter vs Salt

這四個詞常混用，實際定義：

```
Nonce (Number used ONCE)：每次操作不能重複
IV (Initialization Vector)：通常 = nonce，但有些情境要 random / unpredictable
Counter：sequentially incrementing (確保 unique)
Salt：random，公開儲存，不必 unique 但不應重複（rainbow table 防護）
```

實務簡化：

- **AES-GCM nonce**：必 unique（不需 secret，不需 random）
- **AES-CBC IV**：必 random unpredictable（不能 counter）
- **CTR counter**：sequentially（不需 random）
- **Argon2 salt**：random（每用戶獨立）

**用錯場景 → 攻擊立刻**。例：CBC 用 counter 當 IV → predictable IV attack（BEAST）。

## 隨機性來源

Linux：

```
/dev/random
  blocking — entropy pool 沒滿就 block
  傳統觀念「更安全」，現代沒實質差別
  embedded 系統開機可能 hang 等 entropy

/dev/urandom
  non-blocking — pool empty 也直接給
  仍是 CSPRNG（ChaCha20-based 自 Linux 4.8）
  早期觀念有 entropy starvation 風險，現代沒（已預先 seed）
  
getrandom() syscall (Linux 3.17+)
  best of both: block 直到 first seed，之後 non-blocking
  推薦
```

**現代 Linux：用 `getrandom()` 或 `/dev/urandom`**。`/dev/random` 已沒理由用。

```python
import os
key = os.urandom(32)         # 用 getrandom() 後援 /dev/urandom
nonce = os.urandom(12)
```

```c
#include <sys/random.h>
unsigned char key[32];
getrandom(key, 32, 0);
```

## 常見錯誤的 RNG

**絕對不要用**：

```python
import random
random.randbytes(32)         # ✗ Mersenne Twister，攻擊者收集少量 output 預測
random.SystemRandom()        # ✓ 等同 os.urandom

import time
seed = time.time()           # ✗ 可預測 timestamp
```

```c
srand(time(NULL));
unsigned char x = rand();    // ✗ 完全錯
```

```javascript
Math.random()                // ✗ 非密碼學
crypto.getRandomValues()     // ✓ Web Crypto API
```

## 過去災難 #1：Sony PS3 ECDSA

2010 年 fail0verflow 在 27c3 demo。Sony 在 PS3 firmware 簽章用 **常數 nonce k**（每次簽都同 k）。

```
sig_1 = (r, s_1) for m_1   ← 同 k
sig_2 = (r, s_2) for m_2   ← 同 k

由 ECDSA：s = k^-1 (H(m) + rd)
s_1 = k^-1 (H(m_1) + rd)
s_2 = k^-1 (H(m_2) + rd)
s_1 - s_2 = k^-1 (H(m_1) - H(m_2))
k = (H(m_1) - H(m_2)) / (s_1 - s_2)
d = (s_1 × k - H(m_1)) / r
```

attacker 從兩個 firmware 簽章直接算出 Sony 的私鑰。**任意簽 firmware → PS3 越獄**。

之後 PS3 firmware 1.x 全被破，Sony 訴訟 GeoHot，最終庭外和解 2011。**這是 ECDSA 史上最有名的災難**，直接觸發 RFC 6979（deterministic ECDSA）標準化。

## 過去災難 #2：Debian OpenSSL CVE-2008-0166

2006-2008 期間，Debian package maintainer 為了消除 Valgrind 警告，**註解掉 OpenSSL RNG 的 entropy mixing 程式碼**：

```c
// 原始
MD_Update(&m, buf, j);   // 餵 entropy

// Debian patch 註解掉
// MD_Update(&m, buf, j);
```

結果：**OpenSSL RNG 的 seed 只剩 process ID（最多 32768 個值）**。

影響：

- 兩年內 Debian / Ubuntu 產生的所有 SSH host key、user key、SSL cert：**只 32768 種可能 key**
- attacker 預生表 → 對任何 SSH server 試 32768 把 key 直接登入
- **所有受影響的 cert / key 必須 revoke + 重新產**

DSA 簽章影響更深：**任何 DSA 私鑰簽章後，nonce 在 32768 之內 → 私鑰立刻可推算**。

教訓：**密碼學程式碼不要 random patch**。OpenSSL 還在 issue 中放警告：「**這段看似不必要，實際是 entropy 來源**」。Debian 沒看到。

## 過去災難 #3：Bitcoin 早期錢包

2013 Android `SecureRandom` 在某些 device 有 bug：返回的 random 不夠 random。Bitcoin 錢包用 ECDSA，nonce reuse 出現 → **多個用戶私鑰外洩**，比特幣被盜。

修補：Bitcoin 客戶端改用 RFC 6979 deterministic ECDSA。

## 過去災難 #4：Dual_EC_DRBG NSA 後門

2007 NIST SP 800-90A 標準化 4 個 CSPRNG。其中 **Dual_EC_DRBG** 設計時用了兩個 EC point P, Q：

- 標準有規定 P, Q 要怎麼選（NIST 給的）
- 但**選擇背後沒解釋**

2007 Shumow / Ferguson 學術指出：**若 P 與 Q 之間有後門關係**（Q = e × P, e 是 backdoor），知 e 的人能從 32 byte output 預測下一個 output。

2013 Snowden leak 確認：**NSA 實際就是這樣做**。Dual_EC_DRBG 是 NSA "Bullrun" 計劃的一部分 — 故意推動有後門的標準。

RSA Security 公司（嗯，就是 RSA 算法那家）把 Dual_EC_DRBG 設為 BSAFE library 預設。被 leak 後揭露：NSA 付了 1000 萬美元給 RSA Security。

NIST 2014 撤回 Dual_EC_DRBG。**這是公開標準被 NSA 滲透的最有名例子**。

## 現代 CSPRNG

設計目標：

- **後向安全 (backtracking resistance)**：state 洩漏，過去 output 無法回推
- **預測抵抗**：output 看起來像 random，無法預測下一個
- **快**：production 系統高速產生 random

主流：

```
HMAC-DRBG (NIST):
  HMAC 的 chain，每次更新 state
  
CTR-DRBG (NIST):
  AES-CTR mode 當 PRG
  
ChaCha20-DRBG / ChaCha20-based:
  ChaCha20 state 進化
  Linux kernel 4.8+ 用此
  
Fortuna (Schneier-Ferguson):
  多 entropy pool 設計
  歷史性，現代少用
```

Linux kernel 自 5.18 起 RNG 完全重寫（Jason Donenfeld 主導）：用 BLAKE2s + ChaCha20，更乾淨更快。

## getrandom() 細節

```c
#include <sys/random.h>

ssize_t n = getrandom(buf, size, flags);
```

flags：

- `0`：default — block until first seed, 之後 non-blocking
- `GRND_NONBLOCK`：第一次 seed 沒完則 EAGAIN
- `GRND_RANDOM`：用 `/dev/random` 行為（少用）
- `GRND_INSECURE`（5.6+）：不等 seed，可能不安全（早期 boot 用）

**首次 boot 後 getrandom() 不會 block**（kernel 已收集 entropy from boot events）。

## 應用層紀律

```python
# Good
def encrypt_message(plaintext, key):
    nonce = os.urandom(12)
    ct = aes_gcm_encrypt(key, nonce, plaintext)
    return nonce + ct  # 把 nonce 跟 ciphertext 一起送

# Bad
def encrypt_message_bad(plaintext, key):
    static_nonce = b'\x00' * 12   # ✗ 永不重複保證？
    return aes_gcm_encrypt(key, static_nonce, plaintext)
```

```python
# Good for high-volume server
import os, struct, time

class GCMEncryptor:
    def __init__(self, key):
        self.key = key
        self.prefix = os.urandom(8)  # 64-bit instance ID
        self.counter = 0
    def encrypt(self, plaintext):
        self.counter += 1
        nonce = self.prefix + struct.pack('>I', self.counter)
        return aes_gcm_encrypt(self.key, nonce, plaintext)
```

## TLS 對 nonce 的處理

TLS 1.3 GCM nonce 從 sequence number derive：

```
nonce = client_write_iv XOR sequence_number_padded
```

每個 record 一個新 sequence number → nonce 必 unique。**不依賴 RNG**。

這是教科書級的 nonce 管理 — sequence number 自然 unique，不需要 RNG，不會 stateful 出錯（sequence 在 TLS state 內）。

## fork-based 危險

server fork 後：

```python
key = derive_key()
encryptor = GCMEncryptor(key)  # prefix from os.urandom

# parent process forks
pid = os.fork()
# 兩個 process 都有相同 prefix + counter=0
# 兩者開始 encrypt → nonce 重複！
```

修補：fork 後 child reset：

```python
def reset_after_fork():
    encryptor.prefix = os.urandom(8)
    encryptor.counter = 0
os.register_at_fork(after_in_child=reset_after_fork)
```

或用 stateless 設計（counter 來自外部）。

## 一個常見誤解

「`os.urandom` 在容器 / VM 是不是 entropy 不夠？」

**現代不是**。Container / VM 啟動時 host kernel 已有 seed，傳給 guest（virtio-rng / RDRAND fallback）。**首次 boot 都 OK**。

擔心的場景是 **嵌入式裸機**（IoT 開機沒 entropy source），這種要靠：

- HW RNG（Intel RDRAND、ARM TRNG、TPM）
- 預設 seed 從 firmware
- 等使用一段時間累積 entropy（如用戶按鍵）

通用 Linux server / desktop / cloud：`os.urandom` 永遠正確。

## 自我檢核

- [ ] 我能解釋 nonce / IV / counter / salt 的差別
- [ ] 我能說出 `/dev/random` 與 `/dev/urandom` 在現代 Linux 的真實差異
- [ ] 我能講 Sony PS3 ECDSA nonce 重用為什麼洩漏私鑰
- [ ] 我能描述 Debian OpenSSL CVE-2008-0166 的根因
- [ ] 我能說出 Dual_EC_DRBG 後門故事
- [ ] 我能寫 prefix+counter 的 GCM nonce 管理

到這裡 Part 6 結束。下一個 Part 進 post-quantum 認真做 — 量子威脅、lattice、Kyber、Dilithium、SPHINCS+。

→ [Ch 29 量子威脅](./29-quantum-threat.md)
