# Ch 3 — 機率與資訊論速覽

> 目標：補密碼學的另一根支柱。Shannon entropy、unicity distance、PRG / PRF / PRP、IND-CPA / IND-CCA 安全定義的直覺。為後面所有安全證明與攻擊章節打底。

## 為什麼密碼學需要資訊論

**密碼學是「攻擊者能拿到多少資訊」的學問**。Eve 看到 ciphertext 後她**對 plaintext 多了多少 information**？理想情況：**0 bit**。

要量化「資訊」，要有 entropy。Shannon 1948 開創資訊論，他自己 1949 就把它套到密碼學（A Mathematical Theory of Communication 與 Communication Theory of Secrecy Systems 是同年代姐妹篇）。

## Shannon Entropy

```
H(X) = -Σ p(x) × log₂ p(x)
       x∈X
```

讀「H 等於負 sigma p log p」。直覺：**X 的「不確定度」**，越不確定 entropy 越高。

例：

```
公正硬幣：H = -(0.5 × log 0.5 + 0.5 × log 0.5) = 1 bit
作弊硬幣（90% 正面）：H ≈ 0.469 bit
六面骰子：H = log 6 ≈ 2.585 bit
英文字母 plain text：H ≈ 4.5 bit/char（理論 log 26 = 4.7）
                     考慮拼字、語法後實際 ~1.5 bit/char
```

對密碼學的意義：**密鑰 entropy 決定攻擊者的最小工作量**。256-bit 真隨機 key → entropy = 256 bit → 暴力搜尋要 2²⁵⁶ 次。但用「12345678」當 8 byte key → 雖然儲存 64 bit，**entropy 只有約 25 bit**（猜成本，從常見密碼字典中選一個）。

```python
import math

def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    total = len(data)
    return -sum((c/total) * math.log2(c/total) for c in counts if c > 0)

print(shannon_entropy(b"AAAAAAAA"))            # 0.0（全 A，無不確定度）
print(shannon_entropy(b"\x00\x01\x02\x03"))    # 2.0
print(shannon_entropy(open("/dev/urandom", "rb").read(1024)))  # ≈ 7.99
```

**測 RNG 品質常用 entropy 估計**：好 CSPRNG 的輸出 entropy 應該 ≈ 8 bit/byte。差的 RNG 重複 pattern entropy 會明顯低。

## Min-Entropy：更悲觀的測量

Shannon entropy 是平均，**密碼學常用更保守的 min-entropy**：

```
H_min(X) = -log₂ max p(x)
              x
```

考慮「最壞情況：攻擊者最容易猜的那個值」。

例：作弊硬幣 90% 正面：
- Shannon H = 0.469
- Min-entropy H_min = -log₂(0.9) ≈ 0.152

**安全估計用 min-entropy** 比較紮實。NIST SP 800-90B（隨機數標準）用 min-entropy。

## Unicity Distance：要多少 ciphertext 才能唯一解出

Shannon 1949 計算：給定密碼系統，攻擊者需要多少 ciphertext 才能**唯一**確定 key？

```
U = H(K) / D
```

H(K) = key entropy；D = plaintext 冗餘（redundancy，每 char 比 max entropy 少多少）。

英文：max H per char = log 26 ≈ 4.7，實際 ~1.5 → D ≈ 3.2 bit/char。

對 simple substitution cipher：H(K) = log(26!) ≈ 88.4 bit → U ≈ 28 chars。

意思：**只要 28 個英文字母 ciphertext 就能（理論上）唯一還原**。實際攻擊用頻率分析很容易 — Ch 4 古典密碼會展開。

對 AES-256：H(K) = 256 bit → U 對純隨機 plaintext 是 ∞（永遠無法唯一確定）；對英文 plaintext 也要極大量 ciphertext。**這就是現代密碼學的安全感來源**。

## 隨機與偽隨機

```
真隨機 (True Random)
  熱噪聲、放射衰變、量子源
  /dev/random（Linux）有時、/dev/urandom 多數時候
  Intel RDRAND（CPU 內建）
  
偽隨機 (Pseudo-Random)
  CSPRNG（密碼學偽隨機）：seed 後產生看起來像隨機的 bit stream
  非密碼學 PRNG（Mersenne Twister 等）：給統計用，**不要用密碼學**
```

**CSPRNG 必滿足**：

1. **預測抵抗**：知道前面所有輸出，仍無法預測下一個 bit
2. **後向安全**：state 被洩漏，無法回推之前輸出（forward secrecy）

實作：HMAC-DRBG、CTR-DRBG、ChaCha20-DRBG、Linux `getrandom()`。

不要用：`rand()`、`mt19937`、`std::default_random_engine`、`Math.random()` — 全部**非密碼學**，攻擊者收集少量輸出就能預測。

## PRG, PRF, PRP：三層抽象

```
PRG (Pseudo-Random Generator)
  輸入：短 seed (n bit)
  輸出：長 stream (m bit, m >> n)
  例：Stream cipher 的 keystream 生成

PRF (Pseudo-Random Function)
  輸入：key + 任意長 input
  輸出：固定長 output
  保證：對不同 input，output 統計上看起來獨立
  例：HMAC 是 PRF

PRP (Pseudo-Random Permutation)
  輸入：key + n-bit input
  輸出：n-bit output
  保證：對固定 key，是 {0,1}^n 上的 bijection（雙射）
  例：AES 是 PRP
```

**block cipher 是 PRP，hash 是 PRF（或 random oracle 假設）**。這些抽象讓我們**在不知道具體實作**時，仍能討論「這個構造安全嗎」。

## IND-CPA：對稱密碼的安全定義

「ciphertext 看起來像隨機」太模糊。學術上用 **game 定義**：

```
IND-CPA Game (Indistinguishability under Chosen-Plaintext Attack)
─────────────────────────────────────────────────────────
1. Challenger 隨機產 key k
2. Attacker A 任意傳 m → Challenger 回 Enc(k, m)（多次）
3. A 選兩個 m0, m1（同長度）
4. Challenger 擲銅板選 b ∈ {0, 1}，回 c* = Enc(k, m_b)
5. A 看 c* 後猜 b' ∈ {0, 1}
6. A 贏：b' = b

Encryption scheme 安全 iff 任何 polynomial-time A 贏的機率 < 1/2 + negligible
```

直覺：**就算讓 Eve 自己選明文加密看（chosen-plaintext），她仍無法區分兩個密文是哪個明文加的**。

對應的攻擊：能看到密文 + 能控制部分明文的場景（Web 表單、JSON API）。**所有現代對稱密碼必達 IND-CPA**。

**ECB 不滿足 IND-CPA**（同 plaintext 永遠對應同 ciphertext，A 選 m0 = m1 一定贏）— 這就是 Ch 11 為什麼 ECB 死路。

## IND-CCA：更強的安全

CPA 不夠強。Eve 還能 **要求 challenger 解密任意她選的 ciphertext**（chosen-ciphertext attack）：

```
IND-CCA Game
──────────────
1. ... (同 IND-CPA 1-3)
4. Challenger 回 c* = Enc(k, m_b)
5. A 可繼續傳 c ≠ c* 給 challenger 解密（**新增能力**）
6. A 猜 b'
```

CPA：攻擊者只能看 / 加密
CCA：還能解密（除了 challenge ciphertext 本身）

對應現實：**Bleichenbacher / Vaudenay padding oracle** 全屬 CCA 攻擊（攻擊者觀察 server 的解密錯誤訊息）。

**現代密碼學要求 IND-CCA2**（adaptive，Eve 看 challenge 後仍能繼續查詢）— 但純加密達不到，必須加 MAC（這就是 Part 6 AEAD 的存在理由）。

## 生日悖論與安全 bit

「23 個人裡有兩個同生日的機率超過 50%」 — 因為配對數是 C(23, 2) = 253。

對 hash function：**找碰撞的成本 ≈ 2^(n/2)，不是 2^n**：

```
SHA-256 (256-bit) → 找碰撞約需 2^128 次嘗試（生日攻擊）
SHA-1 (160-bit) → 約 2^80 → Google 2017 用 2^63 找到（用 differential attack 加速）
MD5 (128-bit) → 2^64 已破很久（2004 學術，2008 production 攻擊）
```

**設計時 hash 要 2× 安全比特**：要 128-bit 安全，hash 用 256-bit。對稱密碼則 1:1（128-bit AES 抵抗 2^128 嘗試，因為沒有 birthday-style 攻擊）。

## Negligible Function：「實質上 0」的形式定義

「破解機率小到忽略」要嚴格定義：

```
function f(n) is negligible iff for every polynomial p(n),
  exists N s.t. for all n > N, |f(n)| < 1/p(n)
```

直白：**比任何多項式倒數都小**。例：

- `2⁻ⁿ` negligible
- `1/n^100` 不 negligible（仍是多項式）

密碼學證明常結構：「假設 X 困難，破我系統的優勢是 negligible」。實務上 negligible = **2⁻⁸⁰ 以下**（80-bit security 是底線、現在多用 128-bit）。

## Random Oracle Model

理論模型：**hash function 行為是「真正的隨機函式」**。即每次 query 一個新 input，回應從均勻隨機選一個 output（但同 input 永遠回同 output）。

**ROM 證明** 比 standard model 弱（hash 不是真的 random oracle），但工程上夠用。RSA-OAEP、Schnorr 簽章、HMAC 多數安全證明都在 ROM 下進行。

## 動手體會

```python
import os
import math

# CSPRNG entropy 測試
data = os.urandom(10000)
print(shannon_entropy(data))    # ≈ 7.99

# 對比 mt19937
import random
mt = random.Random()
mt.seed(42)
weak = bytes(mt.randint(0, 255) for _ in range(10000))
print(shannon_entropy(weak))    # ≈ 7.99 — 看起來隨機
                                 # 但 entropy 只測 distribution，不測 predictability！
                                 # 知道 seed = 42 就能 predict 全部
```

**重點**：entropy 高不代表安全。CSPRNG 安全在於 **不可預測**，不在 distribution。Mersenne Twister 看起來隨機，但 624 個輸出後 attacker 能完整推 internal state。

## 一個常見誤解

「我的密碼有 entropy 70 bit，安全嗎？」

要看你**比的是什麼**：
- 對暴力 brute-force：70-bit 約 2^70 ≈ 10²¹ 嘗試 — 大規模 GPU 集群幾年能跑完
- 對 NIST 80-bit 底線：剛好 below
- 對 modern 128-bit 標準：嚴重不足

**密碼底線是 2024 年起 128-bit security**。70-bit 對抗 nation-state 級攻擊不夠；對個人攻擊者（單機 GPU）也只值幾個月。

## 自我檢核

- [ ] 我能算簡單分布的 Shannon entropy
- [ ] 我能解釋 min-entropy 與 Shannon entropy 的差別
- [ ] 我能說出 IND-CPA 與 IND-CCA 的差別
- [ ] 我能解釋為什麼 ECB mode 不滿足 IND-CPA
- [ ] 我能說出 PRG / PRF / PRP 各自抽象什麼
- [ ] 我能解釋生日攻擊為什麼讓 hash 安全度減半

下一章進 Part 2 古典密碼，用真實 cipher（Caesar / Vigenère）感受 entropy 與頻率分析在攻擊上的應用。

→ [Ch 4 古典密碼](./04-classical-ciphers.md)
