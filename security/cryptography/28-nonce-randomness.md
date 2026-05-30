# Ch 28 — Nonce 與隨機性：三個毀滅性失敗案例

> **目標**：從三個真實案例（Sony PS3 ECDSA nonce reuse、Debian OpenSSL CVE-2008-0166、Dual_EC_DRBG 後門）理解 nonce/randomness 失敗的毀滅性後果，掌握正確的 CSPRNG 用法。

## 為什麼需要這個？

前三章你學了 AEAD 的三個演算法（AES-GCM、ChaCha20-Poly1305、AES-GCM-SIV），每一個都需要 nonce，有些還需要隨機數來產生 key。

但演算法再完美，**如果 nonce 或隨機數出問題，整個系統會從根基崩塌。**

這不是理論上的擔憂。歷史上最嚴重的密碼學災難，幾乎都和隨機數或 nonce 有關：

- Sony 讓 PS3 的整個安全模型瞬間崩塌——因為 ECDSA 簽章用了固定 nonce
- Debian 讓全球數百萬台機器的 SSH key 可預測——因為維護者刪了一行 seeding code
- NSA 在 NIST 標準裡植入後門——通過控制 PRNG 的參數

這一章用這三個案例，讓你記住：**隨機數是密碼學的命脈，弄錯它就等於沒有密碼學。**

## 先建立直覺

```
密碼學系統的信任鏈：

  演算法設計 → 正確實作 → 安全的隨機數來源 → 正確使用 nonce/IV
       ↑            ↑              ↑                  ↑
    學術界驗證    open source   OS 核心提供        應用層責任

  只要任何一環斷裂，整個系統的安全性歸零。
  而「安全的隨機數來源」是最容易被忽視的一環。
```

Nonce 和隨機數不完全相同：

```
隨機數（random number）：
  - 必須不可預測（unpredictable）
  - 通常用 CSPRNG 產生
  - 例：key generation、IV for CBC

Nonce（number used once）：
  - 必須不重複（unique）
  - 不一定要不可預測——counter 就可以
  - 例：GCM 的 96-bit nonce 可以是 counter
  - 但某些用途（如 ECDSA 的 k）同時要求 unique + unpredictable

共通點：弄錯任何一個都會摧毀安全性。
```

## 案例一：Sony PS3 ECDSA nonce reuse（2010）

### 發生了什麼

Sony 用 ECDSA（Elliptic Curve Digital Signature Algorithm）簽署所有能在 PS3 上執行的軟體。如果你想在 PS3 上跑自製程式，你需要 Sony 的簽章——而 Sony 的 private key 是秘密。

2010 年 12 月，fail0verflow 團隊在 CCC（Chaos Communication Congress）發表了一個驚人的發現：

**Sony 在每一次 ECDSA 簽章中都使用了相同的 nonce k。**

不是「偶爾重複」——是每一次都用同一個值。

### 為什麼 ECDSA 的 nonce 必須唯一：數學推導

ECDSA 簽章的核心（簡化版）：

```
ECDSA 簽章（curve 的 order 為 n，generator 為 G）：

Private key: d（秘密）
Public key:  Q = d·G（公開）

簽署訊息 m：
1. 選擇隨機 nonce k（每次必須不同且不可預測）
2. 計算 R = k·G，取 R 的 x 座標 r
3. 計算 s = k⁻¹ · (hash(m) + r·d)  mod n
4. 簽章 = (r, s)

驗證：
  用 (r, s) 和 Q 驗證等式是否成立（細節省略）
```

如果兩次簽章用了**相同的 k**：

```
簽章 1：s₁ = k⁻¹ · (hash(m₁) + r·d)  mod n
簽章 2：s₂ = k⁻¹ · (hash(m₂) + r·d)  mod n

（注意：同一個 k → 同一個 r）

s₁ - s₂ = k⁻¹ · (hash(m₁) - hash(m₂))  mod n

解出 k：
  k = (hash(m₁) - hash(m₂)) · (s₁ - s₂)⁻¹  mod n

知道 k 後，解出 private key d：
  d = (s₁·k - hash(m₁)) · r⁻¹  mod n
```

攻擊者需要的只是：
1. 兩個用同一個 k 簽的簽章 (r₁, s₁) 和 (r₂, s₂)（r₁ = r₂ 因為 k 相同）
2. 對應的訊息 m₁ 和 m₂
3. 一行中學代數

### 範例一：ECDSA nonce reuse 攻擊的 Python 模擬

```python
"""
ECDSA nonce reuse 攻擊：從兩個用同一 nonce 的簽章中還原 private key

警告：這是教育用途的攻擊演示，不得用於未授權的系統
"""
from hashlib import sha256
from ecdsa import SECP256k1, SigningKey
from ecdsa.numbertheory import inverse_mod

# 產生 key pair
sk = SigningKey.generate(curve=SECP256k1)
vk = sk.get_verifying_key()
private_key_int = sk.privkey.secret_multiplier
n = SECP256k1.order  # curve order

print(f"Private key (secret): {private_key_int}")
print(f"  (攻擊者不知道這個值，目標是算出它)")

# Sony 的致命錯誤：固定 nonce
FIXED_K = 42  # Sony 用了一個固定值（真實案例中是另一個常數）

# 簽章 1
m1 = b"firmware_update_v3.50.bin"
h1 = int(sha256(m1).hexdigest(), 16)
sig1 = sk.sign(m1, hashfunc=sha256, k=FIXED_K)
r1 = int.from_bytes(sig1[:32], 'big')
s1 = int.from_bytes(sig1[32:], 'big')
print(f"\n簽章 1: r={r1}, s={s1}")

# 簽章 2（同一個 k！）
m2 = b"game_patch_v1.02.bin"
h2 = int(sha256(m2).hexdigest(), 16)
sig2 = sk.sign(m2, hashfunc=sha256, k=FIXED_K)
r2 = int.from_bytes(sig2[:32], 'big')
s2 = int.from_bytes(sig2[32:], 'big')
print(f"簽章 2: r={r2}, s={s2}")

# 攻擊者觀察到 r1 == r2（兩個簽章的 r 相同 → 同一個 k）
assert r1 == r2, "如果 r 不同，nonce 就不同，攻擊不適用"
print(f"\nr1 == r2 → 確認 nonce 重複！")

# === 攻擊：還原 k ===
# k = (h1 - h2) * (s1 - s2)^(-1) mod n
k_recovered = ((h1 - h2) * inverse_mod(s1 - s2, n)) % n
print(f"\n還原 k: {k_recovered}")
assert k_recovered == FIXED_K

# === 攻擊：還原 private key d ===
# d = (s1*k - h1) * r^(-1) mod n
d_recovered = ((s1 * k_recovered - h1) * inverse_mod(r1, n)) % n
print(f"還原 private key: {d_recovered}")
print(f"真實 private key: {private_key_int}")
assert d_recovered == private_key_int

print("\n攻擊成功！攻擊者現在擁有 Sony 的 private key。")
print("後果：能簽署任何程式在 PS3 上執行 → PS3 的安全模型完全崩塌")
```

### 後果

fail0verflow 在 30 分鐘的演講中現場算出了 Sony 的 private key。從此任何人都能簽署自製韌體、盜版遊戲、自製作業系統（Linux on PS3）。Sony 無法透過韌體更新修復——因為 private key 已經洩漏，唯一的辦法是換 key，但那意味著所有已發行的遊戲光碟都無法驗證。

Sony 的法律回應是起訴 fail0verflow 和 George Hotz（geohot），但技術上已經無法挽回。

## 案例二：Debian OpenSSL（CVE-2008-0166, 2008）

### 發生了什麼

2006 年 5 月，Debian 的一位維護者 Kurt Roeckx 在修一個 Valgrind 的 warning 時，**註解掉了 OpenSSL 中的兩行關鍵 code**。

原始 code（`md_rand.c`）：

```c
// OpenSSL 原始碼中的 entropy mixing
MD_Update(&m, buf, j);    /* 從 buffer 混入 entropy */
                           /* ← Valgrind 報告這裡使用了 uninitialized memory */

MD_Update(&m, &(md_c[0]), sizeof(md_c));  /* 混入其他 entropy 來源 */
```

Debian 維護者把它改成：

```c
// MD_Update(&m, buf, j);    /* 被註解掉了！Valgrind 不再警告 */

MD_Update(&m, &(md_c[0]), sizeof(md_c));
```

問題：`buf` 包含了來自 `/dev/urandom` 和其他來源的 entropy。刪掉這行後，OpenSSL 的 PRNG 唯一的 entropy 來源變成了 **process ID（PID）**。

### 影響範圍

```
PID 在 Linux 上的範圍：1 ~ 32768（預設 /proc/sys/kernel/pid_max）

只有 ~32768 種可能的 seed → 只有 ~32768 種可能的 key

所有 Debian 和 Ubuntu（基於 Debian）系統在 2006.09 ~ 2008.05 之間
產生的密鑰都可以被預測：

  - SSH host key → 攻擊者可以偽裝成任何 server
  - SSH user key → 攻擊者可以登入任何帳戶
  - SSL/TLS certificate → 攻擊者可以做 MITM
  - OpenVPN key → 攻擊者可以解密 VPN 流量
  - DNSSEC key → 攻擊者可以偽造 DNS
```

### 時間線

```
2006-09-17  有問題的 patch 被 commit（Debian bug #363516）
2006-09-28  OpenSSL 上游維護者被問到，但溝通不清楚
            （維護者以為只問了一行，不知道另一行也被刪）
2008-05-13  Luciano Bello 發現問題，CVE-2008-0166 公開
            → 整整 20 個月的窗口！
2008-05-13  Debian 釋出修復版本
2008-05-15  HD Moore 發布所有 32768 個可能的 SSH key 的 lookup table
            → 任何人都能在幾秒內測試一個 key 是否 vulnerable
```

### 為什麼 2 年沒人發現

1. 產生的 key 在統計上看起來完全正常（通過所有 randomness test）
2. 不同的 PID 產生不同的 key → 同一台機器的不同服務有不同的 key → 「看起來沒問題」
3. 問題在 **entropy source** 而不是 PRNG 的演算法——PRNG 本身是正確的，只是輸入的 entropy 不夠

```python
"""模擬 Debian OpenSSL 弱 key：entropy 只有 PID"""
import hashlib

def weak_keygen(pid: int) -> bytes:
    """只用 PID 作為 seed → 只有 32768 種可能的 key"""
    return hashlib.sha256(pid.to_bytes(4, 'big')).digest()

# 攻擊：暴力搜尋所有可能的 key
target_pid = 12345
target_key = weak_keygen(target_pid)

for pid in range(1, 32769):
    if weak_keygen(pid) == target_key:
        print(f"找到！PID={pid}, key={target_key.hex()[:32]}...")
        break
# 32768 次 SHA-256 → 毫秒級完成
# 期望 key 空間 2^256，實際 key 空間 2^15 → 安全性歸零
```

### 教訓

- **不要修改你不完全理解的密碼學 code**——Valgrind 的 warning 是正確的（`buf` 確實包含 uninitialized memory），但那正是 entropy 的來源
- **Code review 不夠**——這個 patch 被 review 過了，但 reviewer 不理解 OpenSSL PRNG 的 entropy 混合機制
- **統計測試無法抓到這類問題**——產生的 key 在位元分布上完全正常，只是來自太小的空間

## 案例三：Dual_EC_DRBG 後門（2004–2013）

### 發生了什麼

Dual_EC_DRBG（Dual Elliptic Curve Deterministic Random Bit Generator）是一個由 NSA 提出、NIST 在 2006 年標準化的 PRNG。它在 2013 年被 Edward Snowden 的洩漏文件揭露很可能包含 NSA 植入的後門。

### 演算法概要

```
Dual_EC_DRBG 的核心：

兩個橢圓曲線上的點 P 和 Q（都在 NIST 標準中給定）

state₀ = seed（初始狀態）

每一步：
  sᵢ₊₁ = x(sᵢ · P)      ← 新的內部狀態（x 座標）
  rᵢ   = x(sᵢ · Q)      ← 輸出位元（x 座標，截掉前 16 bit）

  P 和 Q 是 NIST 給定的「隨機」點
  → 但如果 NSA 知道 e = dlog_P(Q)（即 Q = e·P），
    就能從輸出 r 回推內部狀態 s
```

### 後門的數學

```
如果 Q = e·P（NSA 知道 e），那：

攻擊者觀察到 rᵢ = x(sᵢ · Q)

  sᵢ · Q = sᵢ · (e·P) = e · (sᵢ · P) = e · Rᵢ

  其中 Rᵢ 的 x 座標是 sᵢ₊₁

攻擊者知道 rᵢ → 可以從 x 座標還原 sᵢ · Q（嘗試兩個 y 座標）
→ 計算 e⁻¹ · (sᵢ · Q) = sᵢ · P → 得到 sᵢ₊₁ 的 x 座標
→ 內部狀態已知 → 能預測所有後續輸出
```

關鍵：截掉的 16 bit 意味著攻擊者需要嘗試 2¹⁶ = 65536 種可能——對 NSA 來說是微不足道的成本。

### 時間線與政治

```
2004     NSA 向 NIST 提交 Dual_EC_DRBG
2006     NIST SP 800-90A 標準化（包含 Dual_EC_DRBG）
2007     Shumow & Ferguson 在 Crypto conference 指出 P/Q 關係可能是後門
         → NIST 和 NSA 沒有正面回應
         → RSA Security 把 Dual_EC_DRBG 設為 BSAFE library 的預設
2013-09  Snowden 文件揭露：NSA 的 SIGINT Enabling Project 花了 $2.5 億/年
         來植入後門，Dual_EC_DRBG 被明確提及
2013-09  NIST 建議停用 Dual_EC_DRBG
2013-12  Reuters 報導 RSA Security 收了 NSA $10M 把 Dual_EC_DRBG 設為預設
2014     NIST SP 800-90A Rev.1 移除 Dual_EC_DRBG
```

### 為什麼能通過所有測試

Dual_EC_DRBG 的輸出能通過 NIST SP 800-22 的所有統計隨機性測試。原因：

1. 橢圓曲線的數學結構確保了輸出在統計上的均勻分布
2. 後門不影響統計性質——它只是讓知道 e 的人能預測輸出
3. 統計測試測的是「是否看起來隨機」，不是「是否不可預測」

```
統計測試 ≠ 密碼學安全

通過 NIST 統計測試的 PRNG：
  ✓ Dual_EC_DRBG（有後門）
  ✓ Mersenne Twister（非密碼學安全，可從 624 個輸出完全預測）
  ✓ 任何足夠長的 π 的小數展開

密碼學安全（CSPRNG）的要求：
  1. 輸出在統計上均勻 ← 統計測試能驗證
  2. 知道前 n 個 bit 不能預測第 n+1 個 ← 統計測試無法驗證
  3. 知道內部狀態不能回推之前的輸出（forward secrecy）
```

## 底層機制：正確的隨機數來源

### Linux 的 entropy 架構

```
Linux 的隨機數來源（kernel ≥ 5.17）：

硬體 entropy 來源：
  ├── CPU 時鐘抖動（jitter）
  ├── 中斷時序（interrupt timing）
  ├── 磁碟 I/O 時序
  ├── 網路封包時序
  ├── RDRAND / RDSEED 指令（x86）
  └── 硬體 TRNG（如有）
        │
        ▼
  ┌─────────────────┐
  │  entropy pool    │ ← ChaCha20-based CSPRNG（kernel ≥ 5.6）
  │  (kernel space)  │    之前用 SHA-1-based 設計
  └───────┬─────────┘
          │
    ┌─────┼─────────────────────┐
    ▼     ▼                     ▼
/dev/random  /dev/urandom   getrandom(2)
```

### /dev/random vs /dev/urandom vs getrandom(2)

| 特性 | /dev/random | /dev/urandom | getrandom(2) |
|---|---|---|---|
| **行為** | 舊版 kernel 會 block when entropy low | 永不 block | 只在 boot 初始 seed 前 block |
| **品質** | 和 urandom 相同（kernel ≥ 5.6）| 和 random 相同（kernel ≥ 5.6）| 最佳選擇 |
| **Boot 安全** | block 直到有足夠 entropy | **不 block** → boot 早期可能不安全 | block 直到初始 seed 完成 |
| **推薦** | 不需要特別使用 | 可以用，但 getrandom 更好 | **推薦** |
| **syscall vs 檔案** | 檔案描述子 | 檔案描述子 | **syscall**（不需要 fd） |

### 範例二：正確取得密碼學安全隨機數

```python
"""
正確 vs 錯誤的隨機數取法
"""
import os, secrets, random

# === 正確：os.urandom / secrets（底層都用 OS 的 CSPRNG）===
key = os.urandom(32)                    # Linux: getrandom(2), Windows: BCryptGenRandom
token = secrets.token_hex(32)           # 方便的 API wrapper
safe_int = secrets.randbelow(2**128)    # [0, 2^128) 的均勻分布
print(f"os.urandom:   {key.hex()[:32]}...")
print(f"secrets:      {token[:32]}...")

# === 錯誤：random 模組（Mersenne Twister → 可預測）===
# 從 624 個 32-bit 輸出就能完全克隆內部狀態
state = random.getstate()      # 攻擊者拿到 state →
random.setstate(state)         # → 能預測所有後續輸出
print(f"\nrandom.random() = {random.random()}  ← 可預測，禁用於密碼學")
```

各平台的正確 CSPRNG 選擇：

| 平台 | 推薦 | 次選 | 避免 |
|---|---|---|---|
| Linux | `getrandom(2)` flags=0 | `/dev/urandom` | `/dev/random`（舊 kernel 不必要 block）|
| Windows | `BCryptGenRandom()` | `RtlGenRandom()` | `CryptGenRandom()`（deprecated）|
| macOS | `getentropy(2)` | `/dev/urandom` | — |
| Python | `secrets` / `os.urandom()` | — | `random`（Mersenne Twister）|
| C/C++ | `libsodium randombytes_buf()` | `RAND_bytes()` | `rand()`, `srand()` |

## 踩雷集錦

1. **「/dev/random 比 /dev/urandom 安全」**：在 Linux kernel ≥ 5.6，兩者用同一個 CSPRNG（ChaCha20-based），輸出品質完全相同。/dev/random 在舊 kernel 上會 block when entropy estimate is low，但這個 entropy estimate 本身就不精確。**getrandom(2) 是正確選擇——它在 boot seed 完成前 block，之後永不 block。**

2. **「nonce 和 random number 是同一回事」**：nonce（number used once）的核心要求是「不重複」，不一定要不可預測。AES-GCM 的 nonce 可以是 counter（甚至更推薦 counter，因為沒有 birthday collision 風險）。但 ECDSA 的 nonce k 必須是 unpredictable + unique——用 counter 反而不安全，因為 counter 可預測。

3. **「通過 randomness test 就安全」**：Dual_EC_DRBG 能通過所有 NIST SP 800-22 的測試。Mersenne Twister 也能。通過統計測試只意味著「輸出看起來均勻分布」，不代表「輸出不可預測」。密碼學安全需要 computational unpredictability，不是 statistical uniformity。

4. **「用 time() 當 seed 就夠了」**：`time()` 的精度是秒，攻擊者知道你大概什麼時候產生 key → 搜尋空間只有幾秒 → 幾千個可能的 seed。即使用 `time_ns()`，攻擊者能估計到微秒級。**永遠用 CSPRNG，不要自己做 seeding。**

5. **「VM snapshot 不影響隨機數」**：VM snapshot/restore 會把 PRNG 的內部狀態一起 snapshot。Restore 後 PRNG 會從同一個狀態繼續 → 產生和之前完全相同的「隨機數」。這就是為什麼 cloud 環境需要特別處理（重新 seed PRNG、使用 counter-based nonce）。AES-GCM-SIV（Ch 27）的設計動機之一就是 VM snapshot。

## 進階：再往深一層

### RFC 6979：確定性 ECDSA nonce

Sony PS3 事件後，密碼學社群提出了 RFC 6979（2013）：用 HMAC-based 的確定性方法從 private key + message 產生 nonce k。

```
RFC 6979 的 nonce 生成：
  k = HMAC_drbg(private_key, hash(message))

優點：
  - 完全確定性（同一個 message + key → 同一個 k）
  - 不需要 RNG → 不可能因為 RNG 問題出事
  - 同一個 message 簽兩次得到同一個簽章（有時是優點，有時是缺點）

缺點：
  - 如果 private key 洩漏（side-channel），k 也洩漏
  - 不提供 hedging（和隨機 nonce 不同，無法抵抗 fault attack）
```

EdDSA（Ed25519，Ch 23 學過的）從設計上就是確定性簽章——nonce 從 private key 和 message 派生，不需要外部隨機數。這是從 Sony PS3 事件中學到的教訓。

### Hedged signatures：兩全其美

最新的做法：`k = HMAC(private_key, hash(message) || random_bytes)`——結合確定性和隨機。RNG 正常時提供額外 entropy；RNG 壞掉時 private_key + message 仍然保底。比純確定性更抵抗 fault attack。

### Mining Your Ps and Qs

Heninger et al.（USENIX Security 2012）掃描全網路的 SSH/TLS 公鑰，發現 0.2% 的 TLS hosts 和 1.06% 的 SSH hosts 共享了 key。原因：embedded device boot 時 entropy 不足。更致命的是，兩個 RSA key 共享 prime factor p → `gcd(n₁, n₂) = p` → 兩個 private key 都洩漏。這項研究直接推動了 Linux kernel 改進 early-boot entropy collection。

## 動手練習

1. **ECDSA nonce reuse 攻擊**：修改範例一的程式，用不同的曲線（如 NIST P-256）重現攻擊。驗證攻擊只需要兩個簽章和中學代數。

2. **弱 seed 演示**：寫一個程式模擬 Debian 弱 key 問題——用 PID 作為唯一的 seed 產生 1000 個 SSH-style key，然後寫一個攻擊者程式在幾毫秒內 brute-force 所有可能的 key。

3. **CSPRNG vs PRNG 比較**：寫一個 benchmark，比較 `os.urandom()`、`secrets.token_bytes()`、`random.randbytes()`（Python 3.9+）的效能。然後用 `random.getstate()` 和 `random.setstate()` 展示 Mersenne Twister 可以被完全克隆。

4. **RFC 6979 實作**：用 Python `ecdsa` 庫對同一個 message 簽章兩次——一次用 random nonce，一次用 RFC 6979 確定性 nonce。驗證確定性版本兩次得到相同簽章。

## 本章重點整理

- Sony PS3：ECDSA 用固定 nonce → 兩個簽章聯立方程解出 private key → 整個平台安全模型崩塌
- Debian OpenSSL：刪一行 entropy seeding code → PRNG 只有 PID 作為 entropy → 所有 key 可在 32768 次嘗試內 brute-force
- Dual_EC_DRBG：NSA 可能控制了 P/Q 的 discrete log 關係 → 能預測 PRNG 輸出 → 通過所有統計測試但不安全
- 正確做法：用 CSPRNG（getrandom(2) / BCryptGenRandom / os.urandom）；確定性簽章（RFC 6979 / EdDSA）消除對 RNG 的依賴

## 自我檢核

- [ ] 能用數學推導 ECDSA nonce reuse 如何洩漏 private key（聯立方程）
- [ ] 能說出 Debian OpenSSL 弱 key 的 root cause 和影響範圍
- [ ] 能解釋 Dual_EC_DRBG 後門的數學原理（Q = e·P → 能回推內部狀態）
- [ ] 能說出 /dev/random、/dev/urandom、getrandom(2) 的差異和推薦用法
- [ ] 能區分 nonce（unique，可以是 counter）和 random number（unpredictable）

## 延伸閱讀

- **fail0verflow, "Console Hacking 2010 — PS3 Epic Fail" (CCC 2010)**
  - **讀哪裡**：YouTube 錄影的 15:00–30:00（ECDSA nonce reuse 的現場演示）
  - **學什麼**：真實的 reverse engineering + 密碼學攻擊的完整流程——從韌體提取到 private key 還原
  - **關聯**：本章案例一的原始演講

- **Nadia Heninger et al., "Mining Your Ps and Qs: Detection of Widespread Weak Keys in Network Devices" (USENIX Security 2012)**
  - **讀哪裡**：Section 3-5 的大規模掃描結果和 GCD factoring 攻擊
  - **學什麼**：real-world 中 entropy 不足的規模——0.2% 的 TLS key 和 1% 的 SSH key 有弱點
  - **關聯**：本章進階段的大規模 key weakness

- **Daniel J. Bernstein, Tanja Lange, Ruben Niederhagen, "Dual EC: A Standardized Back Door" (2015)**
  - **讀哪裡**：全文，重點在 Section 4 的後門數學和 Section 6 的標準化過程
  - **學什麼**：Dual_EC_DRBG 後門的完整技術分析和政治脈絡
  - **關聯**：本章案例三的學術級深度分析

- **RFC 6979 "Deterministic Usage of the Digital Signature Algorithm (DSA) and Elliptic Curve Digital Signature Algorithm (ECDSA)" (2013)**
  - **讀哪裡**：Section 3 的 HMAC-DRBG-based nonce generation
  - **學什麼**：Sony PS3 事件後的工程解答——如何消除 ECDSA 對 RNG 的依賴
  - **關聯**：本章進階段的 RFC 6979

→ [Ch 29 量子威脅](./29-quantum-threat.md)
