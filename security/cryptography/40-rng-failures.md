# Ch 40 — 隨機數失敗史：Randomness Failure = Catastrophic Failure

> **目標**：從 Debian OpenSSL、PS3、Dual_EC_DRBG 三個案例提煉出「randomness failure = catastrophic failure」的教訓，建立正確的 entropy source hierarchy。

---

## 為什麼需要這個？

[Ch 28 — Nonce 與隨機性](./28-nonce-randomness.md) 教了三個案例的技術細節。本章的焦點不同：**從工程和系統設計的角度，提煉出 randomness failure 為什麼是最 catastrophic 的 crypto failure，以及如何在 application 層級防範。**

密碼學的每一層都依賴 randomness：

| 層級 | 需要 randomness 的地方 | failure 的後果 |
|---|---|---|
| Key generation | 生成 AES key、RSA key pair | key 被預測 → 所有加密失效 |
| Nonce / IV | AES-GCM nonce、ECDSA k 值 | nonce 重複 → key 被還原 |
| Protocol | TLS ClientHello.random | handshake 被重放或偽造 |
| Padding | RSA OAEP 的 random padding | padding 可預測 → chosen ciphertext attack |

Randomness failure 的恐怖之處：**它是 silent failure**。你的程式不會 crash，不會報錯，所有 test 都 pass——但產生的 key 可能只有 2^20 的 entropy 而不是 2^128。你完全不知道，直到有人攻擊你。

---

## 先建立直覺

```
正常的 key generation：
  entropy pool（256 bit entropy）
      ↓
  CSPRNG
      ↓
  key = 0xA3 F7 2B 91 ... （128 bit, 2^128 種可能）

  攻擊者 brute force：2^128 次嘗試 → 宇宙滅亡也跑不完

Debian OpenSSL bug（2006-2008）：
  entropy pool（被 Valgrind 修正砍掉）
      ↓
  CSPRNG（只吃 PID 作為 seed）
      ↓
  key = f(PID)    （PID 最多 32,768 種）

  攻擊者 brute force：32,768 次嘗試 → 幾秒鐘
```

---

## 核心概念：三大 Randomness Failure 案例

### 案例一：Debian OpenSSL（2006-2008）

**發生了什麼**：

2006 年，一位 Debian 維護者在修 Valgrind warning 時，註解掉了 OpenSSL 的 entropy 收集 code 中的兩行。

```c
/* 原始 OpenSSL code（md_rand.c）*/
MD_Update(&m, buf, j);           /* ← Valgrind 報告：使用未初始化的記憶體 */
                                  /*    （這是故意的！未初始化記憶體是 entropy source）*/
MD_Update(&m, &(md_c[0]), sizeof(md_c));  /* ← 也被註解掉 */
```

維護者的想法：「Valgrind 說這裡用了 uninitialized memory，一定是 bug。」他把那兩行刪了。

結果：OpenSSL 的 PRNG seed 只剩下 `getpid()` 的返回值。Linux 的 PID 範圍預設是 1 ~ 32768。**所有用 Debian / Ubuntu 的 OpenSSL 在 2006-2008 年間生成的 key 都來自不超過 32,768 個 seed。**

**影響**：

- 所有在這段期間用 `ssh-keygen` 生成的 SSH key → 可被暴力破解
- 所有在這段期間用 `openssl genrsa` 生成的 SSL 證書 → 私鑰可被還原
- HD Moore（Metasploit 作者）在 bug 公開後 2 小時內就生成了所有可能的 key 的資料庫

```
時間線：
2006-09-17  Debian 維護者提交 patch，刪除 entropy 收集
2006-09-17  → 所有 Debian-based 系統的 OpenSSL 開始產生弱 key
（沉默了 20 個月，沒有人發現）
2008-05-13  Luciano Bello 發現 bug，CVE-2008-0166 公開
2008-05-13  HD Moore 開始 brute force 所有可能的 key
2008-05-14  所有 key 的 database 上線（opensslblacklist）
```

**教訓**：

1. 不要在不理解 code 意圖的情況下「修」它——那兩行「使用未初始化記憶體」是 **故意的 entropy source**
2. Crypto code 的 review 需要 crypto expertise，不能只靠 static analysis tool
3. 2 年的 silent failure——你的 key 看起來和正常的 key 一模一樣

### 案例二：PS3 ECDSA（2010）

**發生了什麼**：

Sony 用 ECDSA 簽署 PS3 的 firmware。ECDSA 的簽章公式：

```
簽章 (r, s)：
  r = (k × G).x mod n     ← k 是每次簽章的隨機 nonce
  s = k⁻¹ (hash(m) + r × private_key) mod n

如果 k 是真隨機 → 每次簽章的 r 不同 → private_key 安全

如果 k 是固定值（Sony 的做法）：
  兩個簽章 (r₁, s₁) 和 (r₂, s₂)，且 r₁ = r₂（因為 k 相同）

  s₁ = k⁻¹ (hash(m₁) + r × d) mod n
  s₂ = k⁻¹ (hash(m₂) + r × d) mod n

  s₁ - s₂ = k⁻¹ (hash(m₁) - hash(m₂)) mod n
  k = (hash(m₁) - hash(m₂)) / (s₁ - s₂) mod n  ← 已知！

  有了 k，代回去：
  d = (s₁ × k - hash(m₁)) × r⁻¹ mod n  ← private key 被還原！
```

fail0verflow 團隊在 2010 年 27C3 演講上展示了攻擊。Sony 在所有 firmware update 中使用 **同一個 k 值**——不是 weak random，不是 biased random，而是**完全固定**。

**教訓**：

1. ECDSA 的安全性 100% 依賴每次簽章的 k 是不可預測的
2. 兩個簽章用同一個 k → private key 直接洩漏（不是 brute force，是代數運算）
3. 連大公司的工程師都能犯這種錯——這就是為什麼 EdDSA 用 deterministic nonce（Ch 23）

### 案例三：Dual_EC_DRBG（NSA backdoor）

**發生了什麼**：

NIST 在 2006 年標準化了四個 PRNG，其中一個叫 Dual_EC_DRBG（Dual Elliptic Curve Deterministic Random Bit Generator）。

它的設計用了兩個橢圓曲線上的點 P 和 Q。Shumow 和 Ferguson 在 2007 年指出：如果你知道 P 和 Q 之間的 discrete log 關係（e 使得 Q = eP），你就能從 output 推斷 internal state → 預測所有未來的 output。

```
Dual_EC_DRBG 的結構：

  state_new = (state_old × P).x    ← 更新 state
  output    = (state_old × Q).x    ← 生成 output

如果攻擊者知道 e 使得 Q = e × P：
  1. 觀察 output = (s × Q).x
  2. 在曲線上找到 s × Q（output 是 x 座標，y 可以算）
  3. s × P = s × (Q / e) = (s × Q) / e = (s × Q) × e⁻¹
  4. state_new = (s × P).x → 攻擊者算出 internal state
  5. 預測所有未來的 output
```

2013 年 Snowden 洩漏的文件證實：**NSA 付了 RSA Security 一千萬美元**讓他們把 Dual_EC_DRBG 設成 BSAFE library 的預設 PRNG。

**教訓**：

1. 標準 != 安全——NIST 標準化了一個有 backdoor 的 PRNG
2. 如果一個密碼學構造的安全性取決於「特定常數是怎麼選的」，那它就有 nothing-up-my-sleeve problem
3. 2013 年後 Dual_EC_DRBG 被撤出 NIST 標準，RSA Security 的聲譽受到嚴重打擊

---

## 底層機制：正確的 Entropy Source Hierarchy

### Entropy 從哪裡來

```
┌───────────────────────────────────────────────────────────┐
│                   Application Layer                        │
│  secrets.token_bytes(32)  /  os.urandom(32)               │
│                         ↑                                  │
│                 System CSPRNG API                          │
│  ┌────────────┬────────────┬──────────────┐               │
│  │ Linux      │ Windows    │ macOS        │               │
│  │ getrandom()│ BCryptGen  │ SecRandom    │               │
│  │ /dev/urand │ Random()   │ CopyBytes()  │               │
│  └────────────┴────────────┴──────────────┘               │
│                         ↑                                  │
│                  Kernel CSPRNG                             │
│  ChaCha20-based (Linux 4.8+) / Fortuna (FreeBSD)         │
│                         ↑                                  │
│              Entropy Pool (混合多個來源)                    │
│                         ↑                                  │
│  ┌──────────┬──────────┬──────────┬──────────┐            │
│  │ Interrupt│ Disk I/O │ Network  │ Hardware │            │
│  │ timing   │ timing   │ jitter   │ RNG      │            │
│  │          │          │          │ (RDRAND) │            │
│  └──────────┴──────────┴──────────┴──────────┘            │
└───────────────────────────────────────────────────────────┘
```

### 各層的職責

| 層級 | 做什麼 | 不做什麼 |
|---|---|---|
| Hardware RNG (RDRAND/RDSEED) | 提供物理 entropy | 不直接暴露給 app（可能有 backdoor） |
| Kernel entropy pool | 混合多個 entropy source | 不保證即時可用（啟動初期可能不足） |
| Kernel CSPRNG | 把 entropy 擴展成無限 stream | 不暴露 internal state |
| System API (getrandom) | 提供 blocking / non-blocking 選項 | 不讓 app bypass kernel |
| Application API (os.urandom) | 跨平台封裝 | 不需要 app 自己管 entropy |

### 正確的 API 選擇

```python
"""
各語言 / 系統取得密碼學安全隨機數的正確 API
"""

# === Python ===
import secrets
import os

# 推薦：secrets module（Python 3.6+，明確為密碼學設計）
token = secrets.token_bytes(32)        # 32 bytes 隨機
token_hex = secrets.token_hex(32)      # 64 hex chars

# 也可以：os.urandom（底層一樣，但 secrets 更語意明確）
raw = os.urandom(32)

# ✗ 不要用：random module（Mersenne Twister，可預測！）
# import random  ← 不要用於密碼學
```

```c
/* === C (Linux) === */
#include <sys/random.h>

/* 推薦：getrandom()（Linux 3.17+）*/
unsigned char buf[32];
ssize_t ret = getrandom(buf, sizeof(buf), 0);
/* flags = 0：如果 entropy pool 未初始化，block */
/* flags = GRND_NONBLOCK：不 block，pool 未初始化時返回 -1 */

/* 也可以：/dev/urandom（老方法，但在 boot 初期可能 entropy 不足）*/
/* FILE *f = fopen("/dev/urandom", "r"); fread(buf, 1, 32, f); */

/* ✗ 不要用：rand() / srand()（C 標準的偽隨機，不是 CSPRNG）*/
```

---

## 進一步用法：Hedging — 降低 RNG 失敗的風險

### 什麼是 Hedging

即使 RNG 不完美（entropy 不足、被降級、硬體故障），也能讓密碼學操作不至於完全崩壞。

### Deterministic nonce（EdDSA 的解法）

EdDSA（RFC 8032）：`k = SHA-512(private_key_seed || message)`——deterministic，不依賴 RNG。同一個 (key, message) 永遠產生同一個 k。trade-off：fault injection 可能洩漏 key（RFC 6979 有考慮這個 edge case）。

### Hedged signature（最佳實踐）

結合兩者：`k = HMAC(private_key, message || random_bytes)`。random 好 → 雙重保護；random 壞 → 退化成 deterministic nonce，不崩壞；message 被 fault injection → random 保護你。NIST SP 800-186 和 RFC 6979 都推薦。

---

## 對比與取捨

| 策略 | 優點 | 缺點 | 適用場景 |
|---|---|---|---|
| Pure random nonce | 不需要 message binding | RNG 壞了就崩壞 | 已棄用 |
| Deterministic nonce (RFC 6979) | 不依賴 RNG | 對 fault injection 敏感 | 資源受限設備 |
| Hedged nonce | 雙重保護 | 稍微複雜 | 生產系統推薦 |
| Hardware RNG only | 物理 entropy | 可能有 backdoor（RDRAND）| 不應單獨使用 |
| Kernel CSPRNG | 混合多個來源 | boot 初期 entropy 不足 | 系統層級 |

| RNG API | 安全性 | 平台 | 備註 |
|---|---|---|---|
| `secrets.token_bytes()` | CSPRNG | Python (跨平台) | 推薦 |
| `os.urandom()` | CSPRNG | Python (跨平台) | 也可以，語意沒 secrets 明確 |
| `random.random()` | **不安全** | Python | Mersenne Twister，可預測 |
| `getrandom()` | CSPRNG | Linux 3.17+ | 推薦 |
| `/dev/urandom` | CSPRNG | UNIX | boot 初期可能 entropy 不足 |
| `/dev/random` | CSPRNG (blocking) | Linux | 不推薦（blocking 無意義，Linux 5.18+ 兩者等效） |
| `BCryptGenRandom()` | CSPRNG | Windows | 推薦 |
| `rand()` / `srand()` | **不安全** | C 標準 | LCG，完全可預測 |

---

## 踩雷集錦

### 雷 1：「我用 `random.random()` 做密碼學」

Python 的 `random` module 底層是 Mersenne Twister（MT19937）。MT 不是 CSPRNG：

- 觀察 624 個 32-bit output 就能完全恢復 internal state
- 恢復 state 後可以預測所有未來的 output
- `random.seed(time.time())` → seed 只有幾 bit 的 entropy

```python
# ✗ 永遠不要這樣做
import random
key = random.randbytes(16)  # Python 3.9+，但底層是 MT → 不安全

# ✓ 正確做法
import secrets
key = secrets.token_bytes(16)  # 底層用 os.urandom → CSPRNG
```

### 雷 2：`/dev/urandom` 在系統啟動初期可能 entropy 不足

Linux 在剛啟動時，entropy pool 可能還沒收集到足夠的 entropy。在這個階段從 `/dev/urandom` 讀取可能得到低 entropy 的輸出。

```
Linux 的行為（5.18 之前）：
  /dev/random  → entropy pool 不足時 block
  /dev/urandom → 永遠不 block（即使 entropy 不足）

  → 系統啟動的最初幾秒，/dev/urandom 可能輸出低 entropy 的數據
  → 如果你的服務在 boot 時立刻生成 key → key 可能是弱的

解法：
  getrandom(buf, len, 0)  → 在 entropy pool 初始化完成前 block
  → 保證拿到的一定是高 entropy 的數據

Linux 5.18+：
  /dev/random 和 /dev/urandom 的行為統一
  → 都在 pool 初始化前 block
  → 初始化後都不 block
```

### 雷 3：「我用統計測試驗證 RNG，通過了就安全」

**統計測試（NIST SP 800-22、Diehard、TestU01）能檢測出明顯的 weakness，但無法證明安全性。**

- Mersenne Twister 通過 BigCrush 測試 → 但完全可預測
- Dual_EC_DRBG 通過所有 NIST 統計測試 → 但有 NSA backdoor
- 一個低 entropy 的 seed 擴展出的 stream 可以通過統計測試 → 但攻擊者知道 seed 就知道一切

統計測試只是 necessary condition，不是 sufficient condition。

### 雷 4：「我在虛擬機裡跑，entropy 夠嗎？」

VM 的 entropy source 比 bare-metal 少（interrupt timing 被 hypervisor 抽象化、disk I/O timing 更 deterministic）。更危險的是 VM clone 後兩個 VM 的 entropy pool 一模一樣——生成的 key 可能相同。解法：boot 後 re-seed、使用 virtio-rng、安裝 haveged。

### 雷 5：RDRAND 不應該是唯一的 entropy source

2019 年研究發現某些 AMD CPU 的 RDRAND 在特定條件下返回固定值。RDRAND 內部設計不公開，無法審計。Linux kernel 的做法：把 RDRAND 混入 entropy pool，但不作為唯一 source——defense in depth。

---

## 進階

### Mersenne Twister 的 State Recovery

MT19937 的 state 是 624 個 32-bit 整數。MT 的 output 只是 state 經過 tempering（4 步 bit manipulation），tempering 是可逆的。觀察 624 個 `getrandbits(32)` output → 反推每個 state word → 完全恢復 internal state → 預測所有未來 output。動手練習中會實作這個攻擊。

### Forward Secrecy in CSPRNG

Linux CSPRNG（ChaCha20-based, 4.8+）提供 forward secrecy：`state_t+1 = ChaCha20(state_t, counter)`。知道 `state_t+1` 能預測未來，但 ChaCha20 是 one-way，無法反推 `state_t`——過去的 output 安全。

### 驗證 Application 的 RNG

1. **Code review**：搜索 `random.random`、`rand()`、`Math.random()` 等不安全 API
2. **Runtime 檢查**：啟動時讀 `/proc/sys/kernel/random/entropy_avail`，低於 256 就 log warning
3. **Testing**：mock RNG 為全零，確認密碼學操作會失敗（不是靜靜產生弱 key）

---

## 動手練習

1. **Mersenne Twister 破解**：用 Python 的 `random` module 產生 624 個 `getrandbits(32)` output，實作 `untemper` 函式恢復 internal state，預測第 625 個 output。

2. **比較 `random` vs `secrets` 的 output**：分別用 `random.randbytes(1000000)` 和 `secrets.token_bytes(1000000)` 產生 1 MB 隨機數據，用 `zlib.compress` 比較壓縮率。兩者的壓縮率應該都接近 1.0——但這不代表 `random` 是安全的（解釋為什麼）。

3. **檢查 entropy**：在 Linux 上用 `cat /proc/sys/kernel/random/entropy_avail` 查看可用 entropy。啟動一個大量消耗 entropy 的程式（例如連續 `dd if=/dev/random of=/dev/null bs=32 count=1000`），觀察 entropy_avail 的變化。

4. **Hedged nonce 實作**：實作一個 hedged ECDSA nonce 生成器：`k = HMAC-SHA256(private_key, message || os.urandom(32))`。寫測試驗證：即使 `os.urandom` 固定返回全零，每個不同的 message 仍然得到不同的 k。

---

## 重點整理

```
Randomness failure 是最 catastrophic 的 crypto failure：
  key 被預測 → 所有加密失效
  nonce 重複 → private key 洩漏（ECDSA）
  而且是 silent failure → 你的 test 全部 pass

三大案例：
  Debian OpenSSL → PID as only seed → 32,768 種 key
  PS3 ECDSA     → 固定 nonce k     → private key 一行代數就算出來
  Dual_EC_DRBG  → NSA backdoor     → 標準 != 安全

正確的 entropy 架構：
  Hardware RNG → Kernel entropy pool → Kernel CSPRNG → System API → App API
  任何一層都不應該是唯一的 entropy source

正確的 API：
  Python: secrets.token_bytes() 或 os.urandom()
  C:      getrandom() 或 /dev/urandom
  ✗ 不要用：random.random()、rand()、srand()

Hedging：
  nonce = HMAC(key, message || random)
  → RNG 壞了 → 退化成 deterministic nonce → 不崩壞
  → message 被篡改 → random 保護你
```

---

## 自我檢核

- [ ] 能解釋為什麼 randomness failure 是 silent failure（不 crash、不報錯）
- [ ] 能用自己的話說出 Debian OpenSSL bug 的因果鏈
- [ ] 能寫出 ECDSA 用同一個 k 簽兩次後洩漏 private key 的代數推導
- [ ] 能解釋 Dual_EC_DRBG 的 backdoor 機制（Q = eP 的數學意義）
- [ ] 能畫出正確的 entropy source hierarchy（從 hardware 到 application）
- [ ] 知道 Python 的 `random.random()` 為什麼不能用於密碼學（MT19937 state recovery）
- [ ] 能解釋 hedged nonce 的工作原理和優勢
- [ ] 知道 `/dev/urandom` 在 boot 初期的風險

---

## 延伸閱讀

- **"Mining Your Ps and Qs: Detection of Widespread Weak Keys in Network Devices"（Heninger et al., USENIX Security 2012）**
  - **讀哪裡**：Section 3（RSA key factoring due to shared primes）和 Section 5（root cause: poor entropy at boot）
  - **學什麼**：大規模掃描發現 0.2% 的 HTTPS 和 0.03% 的 SSH RSA key 因為 entropy 不足而可被因式分解——real-world randomness failure 的大規模證據
  - **關聯**：Debian 案例的延伸——不只是 Debian，所有 embedded device 都有 boot-time entropy 問題

- **"Dual EC: A Standardized Back Door"（Checkoway et al., 2014）**
  - **讀哪裡**：Section 2（Dual_EC_DRBG 的數學結構）和 Section 4（如何利用 backdoor）
  - **學什麼**：Dual_EC_DRBG backdoor 的完整技術分析——從數學到實作
  - **關聯**：本章 Dual_EC 案例的深入版

- **RFC 6979：Deterministic Usage of the Digital Signature Algorithm (DSA) and Elliptic Curve Digital Signature Algorithm (ECDSA)**
  - **讀哪裡**：Section 3（deterministic k generation）
  - **學什麼**：用 HMAC-DRBG 從 private key 和 message 確定性地生成 nonce——消除 RNG dependency
  - **關聯**：本章 hedging 策略的標準化版本

---

→ [Ch 41 — 密碼分析方法](./41-cryptanalysis-methods.md)
