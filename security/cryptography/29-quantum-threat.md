# Ch 29 — 量子威脅：Shor、Grover、harvest now decrypt later

> 目標：搞懂量子電腦對密碼學的威脅。Shor 算法為什麼幹掉 RSA / DH / ECC（quantum period finding）、Grover 對對稱密碼只是「key 加倍」、harvest now decrypt later 為什麼讓 PQC 不能等、NIST 2016-2024 PQC 標準化過程。

## 量子電腦在密碼學能做什麼

兩個關鍵量子算法：

```
Shor algorithm (1994):
  factor n in polynomial time  → 殺 RSA
  discrete log in polynomial time → 殺 DH / ECC
  
Grover algorithm (1996):
  search 2^n space in 2^(n/2) operations → 對稱密碼有效但只 sqrt 加速
```

**Shor 是真威脅**：把當前公鑰密碼從 sub-exponential（GNFS 對 RSA）或 exponential（ECDLP）變成 polynomial。

**Grover 不是大事**：對稱密碼 key size 加倍解。AES-128 → AES-256 即可。

## Shor 算法直覺

要 factor `n = p × q`，Shor:

1. 隨機選 `a < n`
2. 用量子電路找 `f(x) = a^x mod n` 的週期 r
3. 若 r 是偶數且 a^(r/2) ≢ -1 (mod n)：`gcd(a^(r/2) ± 1, n)` 給出 p 或 q

關鍵 step 2：**量子電路用 superposition 同時試所有 x**，量子 Fourier transform 抽出週期。**這在古典電腦做不到**（要試 exponentially many x）。

對 ECDLP 類似 idea：找 group order 的一個 multiple → 推算 secret scalar。

## 為什麼還沒發生

雖然 Shor 1994 提出，現實量子電腦離破 2048-bit RSA 還很遠。

**需要的 qubits**：

```
2048-bit RSA：~ 4000 logical qubit (估)
  考慮 error correction → ~ 20-100 million physical qubit
  
今天：
  IBM Condor: 1121 physical qubit (2023)
  Google Sycamore: 70 qubit
  
進展估：2030-2040 年可能達到
```

但這些都是「多年估計」，不是物理定律。突破可能瞬間發生（如 superconducting → topological → ...）。**密碼學者保守做最壞打算**。

## Harvest Now Decrypt Later

```
2024 年 attacker：
  錄下 TLS / VPN traffic
  存著 → 等 2035 量子電腦上線
  
2035 年：
  用 Shor 算 RSA 2048-bit / ECDH 256 → 破 traffic
  解 2024 年的所有 secret
```

對 long-lived secret 已經是即時威脅：

- 國家機密（保密期 50+ 年）
- 醫療紀錄（lifetime）
- 知識產權
- Snowden 爆料說 NSA 已大量蒐集 encrypted traffic

**這就是為什麼 PQC 不能等量子電腦真的出現**。NIST 急著標準化，企業急著遷移。**遷移本身要 5-10 年**（生態軟硬體全換）。

## Mosca's Theorem

加拿大密碼學者 Michele Mosca 提：

```
若 X = "你的 secret 要保密多少年"
若 Y = "PQC 遷移要多少年"
若 Z = "量子電腦多少年後可破現有密碼"

如果 X + Y > Z → 你已經晚了
```

例：金融機構 transaction 要 secret 30 年（X=30），PQC 遷移要 7 年（Y=7），量子電腦估 25 年後（Z=25）。**X + Y = 37 > 25** → 應該昨天就開始遷移。

## NIST PQC 標準化過程

```
2016    NIST 公告 PQC 標準化計劃
2017    第一輪 candidate（69 個）
2019    第二輪（26 個）
2020    第三輪（7 個 finalist + 8 個 alternate）
2022    選出獲選者：
        KEM: Kyber
        Signature: Dilithium, FALCON, SPHINCS+
        其他延後
2024-08 FIPS 203 (ML-KEM, formerly Kyber) 正式發布
        FIPS 204 (ML-DSA, formerly Dilithium)
        FIPS 205 (SLH-DSA, formerly SPHINCS+)
2025+   FIPS 206 (FN-DSA, FALCON 變體) 預期
```

**ML-KEM** = Module-Lattice KEM（Kyber 改名）
**ML-DSA** = Module-Lattice DSA（Dilithium 改名）
**SLH-DSA** = StateLess Hash-Based DSA（SPHINCS+）

## 第四輪：KEM 多元化

NIST 2022 開始第四輪，找 **基於不同數學假設的 KEM**（不只 lattice）：

- **HQC**：code-based
- **BIKE**：code-based (QC-MDPC)
- **Classic McEliece**：1978 提出，code-based，極大 public key (1 MB) 但極穩
- **SIKE**：isogeny-based — **2022 被 Castryck-Decru 攻破**（古典電腦幾小時破）→ 退出

第四輪預計 2025-2026 標準化第二個 KEM。**多元化**避免「全壓 lattice 一旦 lattice 被破就完蛋」。

## 哪些算法仍安全

```
對稱密碼（AES-256, ChaCha20, SHA-256）：
  Grover 加速 → 安全 bit 減半
  AES-128 → 64-bit (危險)
  AES-256 → 128-bit (足夠)
  
公鑰密碼（RSA, DH, ECC）：
  Shor 算法 → 完全破
  必換成 PQC
  
Hash（SHA-256, SHA-3）：
  Grover 加速 → collision 從 2^128 到 2^85 (BHT)
  仍可接受，重要應用建議 SHA-384
  
MAC（HMAC-SHA256, Poly1305）：
  本質對稱 → key 加倍即可
```

**簡單規則**：對稱算法用 256-bit、公鑰換成 PQC。

## 量子安全等級（NIST 定義）

```
Level 1: 安全度 ≈ AES-128 對 Grover (= 2^128 quantum operation)
Level 2: 安全度 ≈ SHA-256 collision 對 Grover
Level 3: 安全度 ≈ AES-192 對 Grover
Level 4: 安全度 ≈ SHA-384 collision 對 Grover
Level 5: 安全度 ≈ AES-256 對 Grover
```

PQC 算法各有 level 1/3/5 變體。**多數系統 Level 1 夠**（≈ 256-bit ECC 等同安全）。

## 業界遷移現況

```
2023  Cloudflare 部分支援 X25519+Kyber768 hybrid TLS
2023  Apple iMessage PQ3（Kyber）
2024  Signal 推 PQXDH（X3DH + Kyber）
2024  Chrome 預設 hybrid X25519+Kyber768 for TLS 1.3
2024  AWS KMS 開始 PQC 試 enrolment
```

**普遍策略：hybrid**（古典 + PQC 並用）：

```
TLS handshake key exchange:
  shared = X25519_secret || Kyber768_secret
  → KDF → final key
```

優點：

- 古典側仍 fallback（PQC 萬一被破還有古典）
- 漸進部署（不必一次換完）
- 標準化過程的安全 margin

缺點：略多 bandwidth + computation。

## Hybrid 細節

```
TLS 1.3 X25519+Kyber768 hybrid:
  client send: X25519_pubkey || Kyber768_ciphertext
  server send: X25519_pubkey || Kyber768_ciphertext
  
  shared_X = X25519 ECDH
  shared_K = Kyber decap
  shared = HKDF(X25519_shared || Kyber_shared)
```

要把兩個 secret 綁在 KDF 中，**只破一個不夠**。

## 為什麼不直接全換 PQC

擔心：

1. **PQC 算法相對年輕**（10-15 年 cryptanalysis）vs ECC（30+ 年）
2. **lattice 假設可能有未發現的攻擊**
3. **實作 bug 多**（PQC code 還在成熟）

**hybrid 是過渡期保險**。預計 2030-2035 PQC 信心夠 → 全切。

## 程式範例：用 PQC

```python
# Python 還沒有 stable Kyber library，但可用：
# - liboqs（Open Quantum Safe）的 Python binding
# - pqcrypto package（partial, kyber-py）

from kyber_py.ml_kem import ML_KEM_768

# Alice 端
ek, dk = ML_KEM_768.keygen()  # encapsulation key, decapsulation key

# Bob 端
shared_secret_bob, ct = ML_KEM_768.encaps(ek)

# Alice 端
shared_secret_alice = ML_KEM_768.decaps(dk, ct)

assert shared_secret_alice == shared_secret_bob
```

實作細節 Ch 31 詳述。

## 一個常見誤解

「量子電腦離我們還很遠，我不需要管 PQC」

**錯**。Mosca 定理告訴你，**遷移本身比量子電腦上線早很多年**。即使量子破 RSA 是 2040 年，如果你的 secret 要保密 20 年（醫療、國防），**現在就要遷移**。

且**現在錄下的 traffic 在 2040 年會被破**。Harvest now decrypt later 是現役威脅。

## 自我檢核

- [ ] 我能解釋 Shor 算法為什麼幹掉公鑰密碼
- [ ] 我能解釋 Grover 為什麼只「半破」對稱密碼
- [ ] 我能說出 Mosca's theorem
- [ ] 我能列出 NIST PQC 三個獲選者（ML-KEM、ML-DSA、SLH-DSA）
- [ ] 我能解釋 hybrid（古典 + PQC）的優點
- [ ] 我能說出 harvest now decrypt later 為什麼是現役威脅

下一章看 lattice 基礎 — PQC 大宗算法的數學底子。

→ [Ch 30 Lattice 基礎](./30-lattice-basics.md)
