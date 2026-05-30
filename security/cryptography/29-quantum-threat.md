# Ch 29 — 量子威脅：Shor、Grover、以及 Harvest-Now-Decrypt-Later

> **目標**：能解釋 Shor's algorithm 為什麼打爆 RSA / ECC / DH，Grover's algorithm 為什麼只影響對稱加密的有效安全等級減半，理解 harvest-now-decrypt-later 的威脅模型，掌握 NIST PQC 標準化時間線。

## 為什麼需要這個？

你在 Part 5 花了七章學的公鑰密碼——RSA、DH、ECDSA、EdDSA、X25519——全部建立在兩個數學難題上：

1. **大整數分解**（RSA）
2. **離散對數**（DH、ECC）

1994 年，Peter Shor 發表了一個量子演算法（Shor's algorithm），證明量子電腦能在多項式時間內解決這兩個問題。換句話說：一台夠大的量子電腦能把 RSA-2048 和 P-256 在幾小時內打穿。

這不是科幻。Google、IBM、微軟正在砸數十億美元造量子電腦。NIST 從 2016 年啟動後量子密碼學（Post-Quantum Cryptography, PQC）標準化，2024 年 8 月正式發布 FIPS 203/204/205。Chrome 和 Cloudflare 已經在跑 hybrid PQ key exchange。

你需要理解威脅的範圍和時間線，才能在後續章節理解 NIST 為什麼選了 lattice 和 hash-based 方案。

## 先建立直覺

想像你有一把鎖，鎖的安全性取決於「試鑰匙」的速度：

```
傳統電腦（classical computer）：
  - 分解 N = p × q → 暴力要試 ~√N 個因子（NFS 更快，但仍是 sub-exponential）
  - 離散對數 → Pollard's rho: ~√(group order)
  - 搜尋 → 逐一嘗試 N 種可能

量子電腦（quantum computer）：
  - 分解 N = p × q → Shor: polynomial time（完全打穿）
  - 離散對數 → Shor: polynomial time（完全打穿）
  - 搜尋 → Grover: √N 次（加速，但不是打穿）
```

兩個演算法對密碼學的影響截然不同：
- **Shor**：徹底消滅公鑰密碼中基於 factoring / DLP 的方案
- **Grover**：把對稱密碼的有效安全等級減半（AES-128 → 64-bit security，AES-256 → 128-bit security）

## 核心概念：Shor's Algorithm 的高層直覺

### 範例一：為什麼「找週期」能分解整數

Shor's algorithm 的核心觀察：**分解整數可以歸結為找週期（period finding）**。

```
目標：分解 N = 15

步驟 1：隨機選 a = 7（gcd(7, 15) = 1，互質）

步驟 2：找 f(x) = 7^x mod 15 的週期 r

  x:    0   1   2   3   4   5   6   7   8  ...
  7^x:  1   7  49 343  ...
  mod15: 1   7   4  13   1   7   4  13   1  ...
                                ↑ 週期 r = 4

步驟 3：用週期 r 來分解

  如果 r 是偶數（r = 4 ✓），計算：
    gcd(7^(r/2) - 1, N) = gcd(7² - 1, 15) = gcd(48, 15) = 3  ✓
    gcd(7^(r/2) + 1, N) = gcd(7² + 1, 15) = gcd(50, 15) = 5  ✓

  15 = 3 × 5  → 分解成功
```

這個歸結的數學依據是 number theory（Ch 2 講過的內容）。關鍵在於：

- **傳統電腦找週期**：需要指數時間（或者 sub-exponential 的 NFS）
- **量子電腦找週期**：用量子傅立葉變換（Quantum Fourier Transform, QFT）在多項式時間內完成

### QFT 做了什麼？

```
傳統方式：                           量子方式：
逐一計算 f(0), f(1), ..., f(N)      同時計算所有 f(x)
然後找重複模式                        （量子疊加態）
                                     ↓
                                     QFT 把疊加態轉換成頻域
                                     ↓
                                     測量得到頻率 → 直接得到週期 r

時間複雜度：O(N) ~ O(e^n)            時間複雜度：O(n³)（n = bit length）
```

你不需要懂量子力學的細節。你需要記住的是：

> **Shor's algorithm 把分解 N-bit 整數從 sub-exponential（NFS: ~e^{n^{1/3}}）變成 polynomial（~n³）。這對 RSA、DH、ECC 是滅頂之災。**

## 底層機制：量子威脅的完整影響

### Shor 打爆的東西

```
┌──────────────────────────────────────────────────────────┐
│                Shor's Algorithm 影響範圍                  │
├───────────────┬──────────────────┬───────────────────────┤
│ 密碼系統       │ 數學難題          │ 量子電腦下的命運      │
├───────────────┼──────────────────┼───────────────────────┤
│ RSA           │ 大整數分解        │ ☠ 完全破解            │
│ DH            │ 離散對數（Z_p*）  │ ☠ 完全破解            │
│ DSA           │ 離散對數（Z_p*）  │ ☠ 完全破解            │
│ ECDH / ECDSA  │ 橢圓曲線離散對數  │ ☠ 完全破解            │
│ EdDSA         │ 橢圓曲線離散對數  │ ☠ 完全破解            │
│ X25519        │ 橢圓曲線離散對數  │ ☠ 完全破解            │
├───────────────┼──────────────────┼───────────────────────┤
│ AES-128       │ brute force      │ ⚠ 有效安全性降為 64  │
│ AES-256       │ brute force      │ ✓ 有效安全性 128     │
│ SHA-256       │ preimage         │ ✓ 有效安全性 128     │
│ ChaCha20      │ brute force      │ ✓ 有效安全性 128     │
└───────────────┴──────────────────┴───────────────────────┘
```

### Grover's Algorithm：對稱加密的減半效應

Grover's algorithm（1996）解決的是「非結構化搜尋問題」（unstructured search）：在 N 個元素中找目標，傳統要 O(N) 次，Grover 做到 O(√N) 次。

對密碼學的影響：

```
對稱密碼的安全等級 = key space 的平方根（在量子攻擊下）

AES-128: key space = 2^128
  → Grover: 2^64 次量子操作
  → 有效安全等級 = 64 bit（不夠安全）

AES-256: key space = 2^256
  → Grover: 2^128 次量子操作
  → 有效安全等級 = 128 bit（足夠安全）

SHA-256 preimage: 2^256
  → Grover: 2^128 次量子操作
  → 仍然安全
```

為什麼 Grover 不像 Shor 那麼致命？

1. **Grover 只是加速，不是打穿**：從 O(N) 到 O(√N)，不是從指數到多項式
2. **解法是加大 key**：把 AES-128 換成 AES-256，問題解決
3. **Grover 有實作限制**：需要超長的量子相干時間（coherence time），實際加速可能低於理論值

### Python 模擬：Grover 的加速效果

```python
"""
模擬 Grover 搜尋 vs 經典搜尋的複雜度差異
（不需要量子電腦——用數學計算比較）
"""
import math

def compare_search_effort(key_bits: int) -> dict:
    """比較經典搜尋和 Grover 搜尋的複雜度"""
    classical = 2 ** key_bits          # 經典：最壞 2^n 次
    grover = 2 ** (key_bits // 2)      # Grover：~2^(n/2) 次
    return {
        "key_bits": key_bits,
        "classical_ops": f"2^{key_bits}",
        "grover_ops": f"2^{key_bits // 2}",
        "effective_security": key_bits // 2,
        "still_secure": key_bits // 2 >= 128,
    }

# 各種對稱密碼在量子攻擊下的有效安全等級
ciphers = [
    ("AES-128", 128),
    ("AES-192", 192),
    ("AES-256", 256),
    ("ChaCha20", 256),
    ("3DES (112-bit)", 112),
]

print(f"{'密碼系統':<20} {'經典':<12} {'Grover':<12} {'有效安全等級':<15} {'量子安全?'}")
print("-" * 75)
for name, bits in ciphers:
    r = compare_search_effort(bits)
    secure = "✓" if r["still_secure"] else "✗"
    print(f"{name:<20} {r['classical_ops']:<12} {r['grover_ops']:<12} "
          f"{r['effective_security']:<15} {secure}")

# 輸出：
# 密碼系統             經典          Grover       有效安全等級       量子安全?
# ---------------------------------------------------------------------------
# AES-128              2^128        2^64         64              ✗
# AES-192              2^192        2^96         96              ✗
# AES-256              2^256        2^128        128             ✓
# ChaCha20             2^256        2^128        128             ✓
# 3DES (112-bit)       2^112        2^56         56              ✗
```

## 進一步用法：Harvest-Now-Decrypt-Later

### 範例二：為什麼「現在」就要行動

```
時間線：
  2024 ─────── 2030 ─────── 2035 ─────── 2040
  │                                        │
  │  攻擊者錄下今天的密文                    │  量子電腦成熟
  │  （TLS session、VPN、email）             │  → 解密 2024 年的密文
  │                                        │
  ▼                                        ▼
  harvest                                  decrypt

這叫做 Harvest-Now-Decrypt-Later (HNDL) 攻擊

受害者清單：
  - 國家機密（保密期限 > 30 年）
  - 醫療紀錄（HIPAA 要求保存 50 年）
  - 金融交易紀錄（法規要求保存 7-25 年）
  - 長期 PKI（CA root certificate，有效期 20+ 年）
```

HNDL 的威脅不是假設性的。情報機構有動機也有能力錄下大量加密流量。當量子電腦到來時，這些密文全部變成明文。

### 量子電腦的現狀（截至 2025 年初）

```
破解 RSA-2048 需要什麼？
  - 理論估計：~4,000 logical qubits + 數小時
  - 但 1 logical qubit ≈ 1,000-10,000 physical qubits（糾錯開銷）
  - 實際需要：~400 萬到 4,000 萬 physical qubits

目前最大的量子電腦（2024-2025）：
  - IBM Condor: 1,121 physical qubits（2023 年底）
  - Google Willow: 105 physical qubits，但糾錯能力突破（2024）
  - Atom Computing: 1,225 physical qubits（2023）
  
gap = 至少 3-4 個數量級

樂觀估計：2035-2040 年可能有密碼學相關的量子電腦（CRQC）
悲觀估計：2045-2050 年
但是：沒有人能確定。HNDL 攻擊者不需要等——現在就在錄了
```

## NIST PQC 標準化時間線

```
2016 ── NIST 徵求 PQC 候選方案
         ↓ 收到 82 個提案
2017 ── Round 1：69 個方案進入
         ↓ 淘汰 + 分析
2019 ── Round 2：26 個方案
         ↓
2020 ── Round 3：7 個 finalist + 8 個 alternate
         ↓
2022 ── 選出 4 個方案：
         - CRYSTALS-Kyber  → KEM（基於 Module-LWE）
         - CRYSTALS-Dilithium → 簽章（基於 Module-LWE + Fiat-Shamir）
         - FALCON → 簽章（基於 NTRU lattice）
         - SPHINCS+ → 簽章（基於 hash）
         ↓
2024 ── 正式標準發布：
         - FIPS 203: ML-KEM（前 Kyber）     ← Ch 31
         - FIPS 204: ML-DSA（前 Dilithium） ← Ch 32
         - FIPS 205: SLH-DSA（前 SPHINCS+） ← Ch 33
         ↓
2025+ ── 遷移期：
         - NSA CNSA 2.0 要求 2030 年前完成 PQC 遷移
         - Chrome / Cloudflare 已在 TLS 中部署 hybrid PQ
         - Signal 已用 PQXDH（X25519 + ML-KEM）
```

## 對比與取捨

| 特性 | Shor's Algorithm | Grover's Algorithm |
|---|---|---|
| 攻擊目標 | factoring, DLP | unstructured search |
| 加速程度 | 指數 → 多項式 | O(N) → O(√N) |
| 受影響的密碼 | RSA, DH, ECC, DSA | AES, ChaCha20, SHA（preimage）|
| 威脅等級 | ☠ 完全消滅 | ⚠ 安全等級減半 |
| 對策 | 換演算法（PQC） | 加大 key（AES-256）|
| 實作難度 | 需要大量 logical qubits | 需要超長 coherence time |
| 預估時間 | 2035-2045 | 實際加速可能更晚 |

| PQC 方案家族 | 代表 | NIST 選了？ | 優勢 | 劣勢 |
|---|---|---|---|---|
| Lattice-based | Kyber, Dilithium | ✓（主力）| key 小、速度快、功能多 | 數學假設相對年輕 |
| Hash-based | SPHINCS+ | ✓（備用）| 安全假設最保守 | 簽章巨大（~50KB）|
| Code-based | Classic McEliece | 進入 Round 4 | 假設歷史悠久（40 年）| key 巨大（~1MB）|
| Isogeny-based | SIKE | ✗（被破解）| key 極小 | 2022 被打穿（Castryck-Decru）|

## 踩雷集錦

1. **「量子電腦已經能破 RSA」**：截至 2025 年初，最大的量子電腦有 ~1,100 physical qubits。破 RSA-2048 需要 ~400 萬 physical qubits。差距超過 3 個數量級。不要被媒體標題誤導——量子電腦對密碼學的威脅是真實的，但不是今天。

2. **「AES 也完了」**：Grover 只是把有效安全等級減半。AES-256 在量子攻擊下仍有 128-bit security，完全夠用。你不需要為對稱密碼擔心——把 AES-128 升級到 AES-256 就行。

3. **「等量子電腦出來再換」**：HNDL 攻擊意味著遷移的 deadline 是現在，不是「量子電腦出來的那天」。如果你的資料保密期限是 20 年，而量子電腦在 15 年後到來，你已經遲了 5 年。

4. **「SIKE 很小很快，應該用它」**：SIKE（Supersingular Isogeny Key Encapsulation）在 2022 年被 Castryck 和 Decru 用經典電腦（不需要量子電腦！）在一小時內打穿。這是一個慘痛的教訓：新的數學假設需要更長時間的審查。

5. **「PQC 的安全性已經被證明了」**：lattice-based 方案的安全性基於 LWE/Module-LWE 的困難性假設。這些假設雖然經過多年研究，但不像 factoring 有 50 年的歷史。NIST 選 SPHINCS+ 作為備用方案的原因之一，就是它的安全假設（hash function 的安全性）比 lattice 更保守。

## 進階：再往深一層

### Quantum Key Distribution (QKD) 不是 PQC

QKD 利用量子力學的物理性質（光子的量子態）來分發密鑰。它和 PQC 是完全不同的路線：

```
PQC（Post-Quantum Cryptography）：
  - 經典電腦上跑的演算法
  - 數學上抵抗量子攻擊
  - 軟體升級就能部署
  - NIST 標準化的東西

QKD（Quantum Key Distribution）：
  - 需要量子通訊硬體（光纖 / 衛星）
  - 物理上保證安全
  - 需要新的基礎設施
  - 距離限制（~100km without repeaters）
  - 不是本課的重點
```

### Mosca's Theorem：什麼時候該開始遷移？

```
設：
  x = 你的資料需要保密多少年
  y = 遷移到 PQC 需要多少年
  z = 量子電腦還要多少年才到來

如果 x + y > z，你已經遲了。

範例：
  政府機密：x = 30 年，y = 5 年 → 如果 z < 35 年，現在就要遷移
  醫療紀錄：x = 50 年，y = 3 年 → 如果 z < 53 年，現在就要遷移
  一般 HTTPS：x = 2 年，y = 1 年 → 如果 z < 3 年，才需要擔心
```

### Hybrid Mode：過渡策略

```
經典 + PQC 的混合模式：

TLS 1.3 hybrid key exchange（Chrome / Cloudflare 已部署）：
  shared_secret = X25519_shared_secret || ML-KEM_shared_secret

為什麼用 hybrid？
  1. 如果 PQC 方案被攻破 → X25519 仍然保護你（抵抗經典攻擊）
  2. 如果量子電腦到來 → ML-KEM 保護你（抵抗量子攻擊）
  3. 兩邊都安全才有可能不安全（defense in depth）
```

## 動手練習

1. **Shor 的歸結**：手算用 a=2 分解 N=21。找 f(x) = 2^x mod 21 的週期 r，然後用 gcd(2^(r/2) ± 1, 21) 求因子。

2. **Grover 的影響**：計算以下密碼系統在量子攻擊下的有效安全等級，判斷是否需要升級：
   - AES-128-GCM
   - AES-256-GCM
   - ChaCha20-Poly1305（256-bit key）
   - SHA-256（collision resistance）
   - SHA-256（preimage resistance）

3. **Mosca 分析**：你的公司儲存客戶金融資料（保密期限 25 年），遷移到 PQC 預估需要 3 年。假設量子電腦在 2040 年到來，你最晚什麼時候開始遷移？

4. **HNDL 情境模擬**：列出你目前工作中使用的三種加密通訊（例如 TLS、VPN、SSH），評估每一種的 HNDL 風險等級（資料保密期限 vs 量子電腦時間線）。

## 本章重點整理

- Shor's algorithm 把 factoring 和 DLP 從 sub-exponential 降到 polynomial time，徹底消滅 RSA、DH、ECC
- Grover's algorithm 把對稱密碼的有效安全等級減半（AES-128 → 64-bit），但 AES-256 仍然安全
- Harvest-Now-Decrypt-Later 攻擊意味著遷移 deadline 是現在，不是量子電腦到來的那天
- NIST 2024 年發布 FIPS 203（ML-KEM）、204（ML-DSA）、205（SLH-DSA）作為 PQC 標準
- SIKE 在 2022 年被經典攻擊打穿，說明新數學假設需要長時間審查
- Hybrid mode（經典 + PQC）是目前最穩健的遷移策略

## 自我檢核

- [ ] 能用一句話解釋 Shor's algorithm 的核心思路（找週期 → 分解整數）
- [ ] 能列舉至少四個被 Shor 打爆的密碼系統
- [ ] 能解釋為什麼 Grover 對 AES-256 不構成致命威脅
- [ ] 能解釋 harvest-now-decrypt-later 攻擊模型
- [ ] 能畫出 NIST PQC 標準化的時間線（2016 → 2024）
- [ ] 能說出 NIST 最終選了哪三個 PQC 標準和它們對應的 FIPS 編號
- [ ] 能解釋 hybrid mode 的設計邏輯和好處

## 延伸閱讀

- **Peter Shor, "Algorithms for Quantum Computation: Discrete Logarithms and Factoring"（1994）**
  - **讀哪裡**：Section 5–7（period finding → factoring 的歸結）
  - **學什麼**：原始論文意外地好讀——Shor 的寫作能力很強
  - **關聯**：本章 Shor's algorithm 的理論來源

- **Lov Grover, "A fast quantum mechanical algorithm for database search"（1996）**
  - **讀哪裡**：前 5 頁的高層描述
  - **學什麼**：√N 加速的直覺——為什麼不能做得更快（tight bound）
  - **關聯**：本章 Grover 對對稱密碼的影響

- **NIST Post-Quantum Cryptography: Selected Algorithms 2022**
  - **讀哪裡**：公告頁面和技術摘要（不需要讀完整的 specification）
  - **學什麼**：NIST 為什麼選了 Kyber / Dilithium / SPHINCS+，為什麼淘汰了其他方案
  - **關聯**：本章 NIST 時間線和 Part 7 後續章節的基礎

- **Michele Mosca, "Cybersecurity in an era with quantum computers"（2018）**
  - **讀哪裡**：Section 2（Mosca's theorem 的正式描述）
  - **學什麼**：如何量化 PQC 遷移的緊迫程度
  - **關聯**：本章進階段落的遷移決策框架

→ [Ch 30 Lattice 基礎](./30-lattice-basics.md)
