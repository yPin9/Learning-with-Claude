# Ch 41 — 密碼分析方法：Breaking Ciphers Without Brute Force

> **目標**：能解釋 differential / linear / algebraic / meet-in-the-middle 四種密碼分析方法（cryptanalysis）的核心思路，理解為什麼 AES 能抵擋前兩者。

---

## 為什麼需要這個？

你已經知道「不要自己設計 cipher」。但你有沒有想過：密碼學者用什麼方法判斷一個 cipher 是不是安全的？

答案不是「跑 10 年都沒人破」。密碼學者會主動嘗試用已知的分析方法攻擊 cipher，然後**證明**攻擊的複雜度高於某個閾值。如果沒有任何已知方法比 brute force 更快，才認為 cipher 是安全的。

本章介紹四種最重要的密碼分析方法。你不需要能自己做 cryptanalysis（那是密碼學者的工作），但你需要**理解這些方法在做什麼**，才能：

1. 看懂 cipher 的安全性論證（「為什麼 AES 選了這組 S-box」）
2. 理解 cipher 被淘汰的原因（「為什麼 DES 的 S-box 被設計得很詭異」）
3. 在評估新 cipher 時知道要問什麼問題

---

## 先建立直覺

密碼分析的核心目標：**找到比 brute force 更快的方法來恢復 key 或 plaintext。**

```
Brute force on AES-128：
  嘗試所有 2^128 個可能的 key → 需要 ~3.4 × 10^38 次 AES 運算
  目前最快的超級電腦也跑不完

Cryptanalysis 的目標：
  找到一個 shortcut，讓攻擊複雜度從 2^128 降到... 更低
  例如：2^126.1（biclique attack on AES-128，2011）
  → 理論上比 brute force 快 4 倍
  → 但仍然是 2^126 → 實際不可行
  → 所以 AES-128 仍被認為安全

如果攻擊複雜度降到 2^80 以下 → cipher 被認為 broken
（不是「有人真的破了」，而是「理論上可行」）
```

四種方法的核心 idea，一句話版本：

| 方法 | 核心 idea |
|---|---|
| Differential | 追蹤 input 的差值（difference）如何通過 cipher 傳播 |
| Linear | 找 input/output bit 的線性近似（XOR 關係） |
| Algebraic | 把 cipher 表示成方程組，用數學方法解方程 |
| Meet-in-the-middle | 從加密方向和解密方向同時搜索，在中間碰頭 |

---

## 核心概念一：Differential Cryptanalysis

### 歷史

Eli Biham 和 Adi Shamir 在 1990 年發表，展示了對 DES 的攻擊。但後來 IBM 的 DES 設計者（Don Coppersmith）透露：**NSA 在 1974 年就知道 differential cryptanalysis**，所以他們修改了 DES 的 S-box 來抵擋這種攻擊——只是不告訴外界為什麼那樣選 S-box。

### 原理

觀察 cipher 對 **input difference** 的反應。

```
核心 idea：

  P₁ = 某個 plaintext
  P₂ = P₁ ⊕ ΔP        （和 P₁ 有特定的 difference ΔP）

  用同一個 key K 加密：
  C₁ = Enc(K, P₁)
  C₂ = Enc(K, P₂)

  ΔC = C₁ ⊕ C₂        （output difference）

  如果 cipher 是「理想的」：
    → ΔC 是完全隨機的，和 K 無關
    → 攻擊者無法從 (ΔP, ΔC) 推出 K 的任何資訊

  但如果 cipher 的某些組件不理想：
    → 特定的 ΔP 在通過 cipher 後，某些 ΔC 出現的機率比其他高
    → 這就是 differential characteristic（差分特性）
    → 攻擊者可以利用這個 bias 來恢復 key
```

### 攻擊流程（以 DES 為例）

```
Differential cryptanalysis 的步驟：

1. 找 differential characteristic：
   分析 S-box 的 DDT（Difference Distribution Table）
   → 找到 high-probability differential path

   例如：輸入差 ΔP = 0x00400000 通過 DES 的 R 輪後，
   輸出差 ΔC = 0x00000004 的機率是 p = 2^(-6)
   （理想情況下應該是 2^(-32)）

   DDT 的結構（以 4-bit S-box 為例）：

   ΔX \ ΔY │ 0  1  2  3  4  5  6  7  8  9  A  B  C  D  E  F
   ─────────┼───────────────────────────────────────────────────
      0     │ 16 0  0  0  0  0  0  0  0  0  0  0  0  0  0  0
      1     │  0 0  2  2  0  4  2  0  0  2  0  2  0  2  0  0
      2     │  0 0  0  4  2  0  0  2  0  0  2  0  2  0  2  2
      ...

   DDT[ΔX][ΔY] = 該 S-box 有多少 input pair 的差是 ΔX
                   且 output pair 的差是 ΔY

   高值 → high-probability differential → 可利用

2. 收集 chosen-plaintext pair：
   攻擊者選 2^m 對 (P₁, P₂ = P₁ ⊕ ΔP)，取得對應的 (C₁, C₂)
   m 取決於 differential probability 的倒數

3. 分析最後一輪：
   對最後一輪的 subkey 做猜測
   → 反推最後一輪的 input
   → 檢查 input difference 是否符合 predicted differential
   → 正確的 subkey 猜測會讓 match 次數顯著高於 random

4. 恢復完整 key：
   拿到最後一輪的 subkey → 用 key schedule 的關係推出完整 key

         P₁ ─→ [Round 1] → [Round 2] → ... → [Round R-1] → [Round R] → C₁
                                                    ↑
         ΔP                               attacker 追蹤
         ↓                                 differential
         P₂ ─→ [Round 1] → [Round 2] → ... → [Round R-1] → [Round R] → C₂

   攻擊者猜測 Round R 的 subkey → 反推 Round R 的 input difference
   → 和 differential path 的預測比對
```

### 複雜度

- **DES（16 rounds）**：Biham & Shamir 的 differential attack 需要 2^47 chosen plaintexts——比 brute force（2^56）快，但需要大量 chosen plaintext（不太實際）
- **AES-128（10 rounds）**：AES 的 S-box 被設計為 max differential probability = 2^(-6) per S-box。經過 4 輪後，any differential characteristic 的 probability < 2^(-150)——比 2^(-128) 還小 → **differential attack 比 brute force 更慢** → AES provably resistant

---

## 核心概念二：Linear Cryptanalysis

### 歷史

Mitsuru Matsui 在 1993 年發表，是對 DES 的已知最佳攻擊之一。

### 原理

找 plaintext bits、ciphertext bits、key bits 之間的 **線性近似（linear approximation）**。

```
核心 idea：
  理想 cipher：P[i] ⊕ C[j] ⊕ K[k] = 0 的機率 = 1/2（任何 XOR 組合都 50/50）
  實際 cipher：某些組合偏離 1/2 → bias ε → 需要 N ≈ 1/ε² 個 known-plaintext

攻擊流程：
1. 建 LAT（Linear Approximation Table）：分析 S-box 所有 input/output mask 的 bias
2. Piling-up lemma 串聯多輪：總 bias = 2^(n-1) × ε₁ × ... × εₙ（每多一輪 bias 減半）
3. 收集 1/ε² 個 known-plaintext pair
4. 用觀察到的 bias 正負號 → 恢復 key bit
```

### 複雜度

- **DES（16 rounds）**：Matsui 的 linear attack 需要 2^43 known plaintexts——目前對 DES 的最佳 single-key attack
- **AES-128**：AES 的 S-box max linear bias = 2^(-3)。4 輪的 linear trail 的 max bias < 2^(-75) → 需要 > 2^(150) known plaintexts → **遠超 brute force → AES provably resistant**

---

## 核心概念三：Algebraic Attack

### 原理

把 cipher 的運算表示成 **多項式方程組**（在 GF(2) 上的 boolean equations 或 GF(2^n) 上的 multivariate equations），然後用數學方法解方程。

```
核心 idea：

  加密運算 C = Enc(K, P) 可以展開成一組方程：

  AES 的一個 S-box（在 GF(2) 上）：
    y₁ = f₁(x₁, x₂, ..., x₈, k₁, k₂, ..., k₈)
    y₂ = f₂(x₁, x₂, ..., x₈, k₁, k₂, ..., k₈)
    ...
    y₈ = f₈(x₁, x₂, ..., x₈, k₁, k₂, ..., k₈)

  其中 fᵢ 是 degree-2 或 degree-3 的 boolean polynomial

  AES-128 全展開：
    ~8000 個方程，~1600 個未知數（128 key bits + intermediate state bits）

  如果能解這組方程 → 得到 key

解方程的方法：
  1. Gröbner basis（Buchberger algorithm）
     → 把方程組化簡成 triangular form → 回代求解
     → 理論上可行，實際複雜度依賴方程的結構

  2. SAT solver
     → 把方程轉成 CNF（Conjunctive Normal Form）
     → 用 DPLL / CDCL 等 SAT solver 求解
     → 對 round-reduced cipher 有效（5-6 輪 AES 可以用 SAT 解）

  3. XL / XSL algorithm
     → 2002 年 Courtois & Pieprzyk 聲稱對 AES 有效
     → 但分析中的假設被質疑
     → 目前不認為比 brute force 更快
```

### 實際效果

```
Algebraic attack 的威力取決於方程的「degree」和「over-determination」：

低 degree + 高 over-determination → 容易解
  例：某些 stream cipher（Toyocrypt、LILI-128）
  → S-box 的 algebraic degree 低 → 方程容易化簡
  → 被 algebraic attack 實際破解

高 degree + 低 over-determination → 困難
  例：AES
  → S-box 是 GF(2⁸) 上的求逆 → algebraic degree 7
  → 10 輪的方程組極其複雜
  → 目前沒有實際可行的 algebraic attack

Stream cipher 更容易被打：
  stream cipher 的 state update 通常用 LFSR（線性）+ 非線性 filter
  → LFSR 部分的方程是線性的 → 大幅降低整體方程的 degree
  → algebraic attack 可以高效求解
  → 這就是為什麼現代 stream cipher（ChaCha20）不用 LFSR
```

### 對 AES 的 algebraic attack 現狀

| 年份 | 研究者 | 方法 | 聲稱複雜度 | 學界反應 |
|---|---|---|---|---|
| 2002 | Courtois & Pieprzyk | XSL | 2^100 | 假設被質疑，不被接受 |
| 2009 | 多組研究者 | Gröbner basis | 無法比 brute force 快 | 方程組太大 |
| 2012 | SAT-based | MiniSat on 5-round AES | 可行（minutes） | 只能攻擊 reduced rounds |
| 現狀 | — | — | 全 10-round AES 無實際 algebraic attack | AES 被認為安全 |

---

## 核心概念四：Meet-in-the-Middle（MITM）

### 歷史

Diffie 和 Hellman 在 1977 年提出，用來說明為什麼 Double-DES（2DES）不安全。

### 原理

利用「加密方向」和「解密方向」可以分別獨立計算的特性，把搜索空間從乘法關係降低到加法關係。

```
Double-DES（2DES）：
  C = DES(K₂, DES(K₁, P))

  Key space：2^56 × 2^56 = 2^112（表面上）

  Brute force 2DES：嘗試所有 2^112 個 (K₁, K₂) pair → 太慢

Meet-in-the-Middle attack：

  Step 1: 從加密方向
    對所有 2^56 個 K₁，計算 M = DES(K₁, P)
    把 (K₁, M) 存進 hash table
    → 需要 2^56 次 DES + 2^56 × 64 bit storage

  Step 2: 從解密方向
    對所有 2^56 個 K₂，計算 M' = DES⁻¹(K₂, C)
    在 hash table 裡查找 M' 是否存在
    → 如果存在：找到一個候選 (K₁, K₂)

  Step 3: 驗證
    用第二對 (P₂, C₂) 驗證候選的 (K₁, K₂) 是否正確

  ┌──────────────────────────────────────────────────────┐
  │                                                      │
  │  P ──→ DES(K₁,·) ──→ M ──→ DES(K₂,·) ──→ C        │
  │                        ↑                              │
  │         加密方向 ──→  中間 ←── 解密方向               │
  │         窮舉 K₁       碰頭！    窮舉 K₂               │
  │                                                      │
  └──────────────────────────────────────────────────────┘

  複雜度：
    Time:    2^56 + 2^56 = 2^57（加法！不是乘法）
    Storage: 2^56 × 8 bytes ≈ 512 PB（很大但理論上可行）

  結論：2DES 的有效安全性是 2^57，不是 2^112
  → 加倍 key 長度 ≠ 加倍安全性
```

### 範例一：為什麼 3DES 用 EDE（Encrypt-Decrypt-Encrypt）

```
3DES-EDE：C = DES(K₃, DES⁻¹(K₂, DES(K₁, P)))

  如果 K₁ = K₂ = K₃ → 退化成 single DES
  → 向後相容！

MITM on 3DES：
  把 3DES 看成兩段：
    段一：DES(K₁, P)             → 2^56 個中間值
    段二：DES⁻¹(K₂, DES⁻¹(K₃, C)) → 2^112 個中間值

  MITM 複雜度：2^56 + 2^112 ≈ 2^112

  → 3DES 的有效安全性約 2^112
  → 不是 2^168（3 × 56）
  → 但 2^112 仍然足夠（雖然已經不推薦用 3DES 了）
```

### 範例：MITM 的複雜度比較

```
4-bit key double cipher（演示用）：

Brute force：16 × 16 = 256 次嘗試（乘法）
MITM：16 + 16 = 32 次嘗試（加法）

→ 動手練習：自己寫一個 4-bit-key 的簡化 cipher，
  實作 MITM attack，驗證嘗試次數確實是加法而不是乘法。
```

---

## 底層機制：AES 的安全性論證

AES 的設計者（Daemen 和 Rijmen）在 proposal 中提供了明確的安全性論證：

```
AES 抵擋 differential/linear cryptanalysis 的設計：

1. S-box 的選擇：
   AES S-box = GF(2⁸) 求逆 + affine transformation
   → max differential probability per S-box = 2^(-6)
   → max linear bias per S-box = 2^(-3)
   → 這是 8-bit S-box 能達到的理論最佳值

2. MixColumns 的 Branch Number：
   MixColumns 的 branch number = 5
   → 任何 non-trivial differential/linear trail
     在連續 4 輪內至少激活 5²= 25 個 S-box

3. 4 輪的安全下界（Wide Trail Strategy）：

   Differential：
   4 輪至少激活 25 個 S-box
   → 4-round differential probability ≤ (2^(-6))^25 = 2^(-150)
   → 全 10 輪的 probability 遠小於 2^(-128)
   → AES-128 的 differential attack 比 brute force 慢 → provably secure

   Linear：
   4 輪至少激活 25 個 S-box
   → 4-round linear bias ≤ (2^(-3))^25 × 2^24 = 2^(-51)
   → 需要 2^(102) known plaintexts（1/ε² ）
   → 全 10 輪需要的 data 遠超可能的量 → provably secure

   Wide Trail Strategy：SubBytes（低 max DP/bias）+ ShiftRows（跨 column 擴散）
   + MixColumns（branch number = 5）→ 4 輪 ≥ 25 active S-box → provably secure
```

---

## 進一步用法：DES 的 S-box 之謎

### 範例：計算 DES S-box 的 DDT

```python
"""計算 DES S-box 1 的 max differential probability"""
DES_SBOX1 = [
    [14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7],
    [0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8],
    [4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0],
    [15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13],
]

def sbox_lookup(sbox, x):
    row = ((x >> 5) << 1) | (x & 1)
    col = (x >> 1) & 0xF
    return sbox[row][col]

def compute_ddt(sbox):
    ddt = [[0]*16 for _ in range(64)]
    for x in range(64):
        for dx in range(64):
            dy = sbox_lookup(sbox, x) ^ sbox_lookup(sbox, x ^ dx)
            ddt[dx][dy] += 1
    return ddt

ddt = compute_ddt(DES_SBOX1)
max_prob = max(ddt[dx][dy] for dx in range(1,64) for dy in range(16)) / 64
print(f"DES S-box 1 max DP: {max_prob:.4f}")  # ≈ 0.25 (= 16/64)
# 比較：random S-box 的 max DP 通常更高 → DES S-box 是 optimized 的
```

---

## 對比與取捨

| 方法 | 攻擊類型 | 需要的資料 | 適用的 cipher 類型 | 對 AES 的效果 |
|---|---|---|---|---|
| Differential | Chosen-plaintext | 2^(1/p) pairs | Block cipher (weak S-box) | 無效（provably resistant） |
| Linear | Known-plaintext | 1/ε² pairs | Block cipher (linear S-box) | 無效（provably resistant） |
| Algebraic | Known-plaintext | 少量 | Stream cipher, reduced-round block cipher | 僅對 reduced rounds 有效 |
| MITM | Known-plaintext | 少量 (1-2 pairs) | Multiple encryption | 不適用（AES 是 single cipher） |

---

## 踩雷集錦

### 雷 1：「AES 已經被 algebraic attack 破了」

2002 年 Courtois 和 Pieprzyk 的 XSL 論文聲稱對 AES 有效。但他們的分析中有一個關鍵假設（linearization 的獨立性）被多組研究者質疑。截至 2025 年，沒有任何實際的 algebraic attack 能比 brute force 更快地攻擊全 10-round AES-128。

AES-128 上已知最佳的 theoretical attack 是 biclique attack（2011），複雜度 2^(126.1)——比 brute force 快不到 4 倍，完全不可行。

### 雷 2：「Differential cryptanalysis 是 Biham & Shamir 發明的」

他們是第一個公開發表的（1990）。但 IBM 的 DES 設計團隊在 1974 年就知道這種攻擊（他們叫它 T-attack），NSA 也知道。DES 的 S-box 被特別設計來抵擋 differential cryptanalysis——只是設計文件被保密了 20 年。

### 雷 3：「round-reduced attack 代表 cipher 不安全」

密碼學論文經常報告對 「7-round AES」或「6-round AES」的攻擊。AES-128 有 10 輪。攻擊 7 輪不代表能攻擊 10 輪——每多一輪，攻擊複雜度通常指數增長。

但 round-reduced attack 是重要的指標：如果有人攻擊到 8-round AES，安全邊際就只剩 2 輪。這就是為什麼 AES 選了 10 輪而不是理論最低需要的 4 輪——**安全邊際（security margin）是設計原則**。

### 雷 4：混淆 theoretical break 和 practical break

| 術語 | 定義 | 例子 |
|---|---|---|
| Theoretical break | 任何比 brute force 快的方法 | Biclique on AES-128: 2^(126.1) |
| Practical break | 用合理的資源在合理的時間內破解 | RC4 在 TLS 中的 bias attack |
| Academic break | 有效但需要不切實際的資料量 | Differential on full DES: 2^47 chosen-plaintexts |

AES 有 theoretical break（biclique, 2^126.1）但沒有 practical break。DES 有 academic break（differential/linear）和 practical break（56-bit key 可暴力搜索）。

### 雷 5：「我的 cipher 有 256-bit key 所以安全」

Key length 是 upper bound，不是 actual security。

- 2DES 有 112-bit key，但 MITM 讓有效安全性降到 57-bit
- 如果 cipher 的 S-box 有高 differential probability，攻擊複雜度可能遠低於 key length
- **安全性取決於 cipher 的結構，不取決於 key 的長度**

---

## 進階

### Impossible Differential

找 **probability = 0 的 differential**（某些 ΔP 不可能產生某些 ΔC）→ 逐步排除 wrong keys。對 AES 可以攻擊到 7-round（比 differential/linear 的 round-reduced 結果更好）。

### Integral Cryptanalysis（Square Attack）

AES 設計者自己提出的方法：選 256 個 plaintext，某 byte 位置遍歷 0x00-0xFF、其他固定。3 輪後某些 byte 的 XOR sum = 0（S-box 是 bijection）。猜測第 4 輪 subkey 驗證此性質。可攻擊到 6-round AES-128。

### Biclique Attack

2011 年 Bogdanov 等人提出，目前對 AES 的最佳攻擊：AES-128 降到 2^(126.1)、AES-256 降到 2^(254.4)。理論上比 brute force 快不到 4 倍——完全不可行，AES 仍被認為安全。

---

## 動手練習

1. **計算 DDT**：用範例中的 code 計算 DES S-box 1 的完整 DDT。找出 max differential probability 和對應的 (ΔX, ΔY)。

2. **比較 S-box 品質**：生成 10 個 random 6→4 S-box，計算每個的 max differential probability。和 DES S-box 1 比較——DES 的 S-box 是否明顯更好？

3. **MITM 概念實作**：修改範例二的 code，把 key space 擴大到 8-bit（每把 key 8 bit，double encryption 的 key space 是 2^16）。測量 brute force 和 MITM 分別需要多少次加密/解密。

4. **閱讀 AES proposal**：Daemen 和 Rijmen 的 "The Design of Rijndael"（書或原始 NIST submission）。找到 Wide Trail Strategy 的段落，驗證本章提到的 branch number = 5 和 25 active S-boxes。

---

## 重點整理

```
Differential Cryptanalysis：
  追蹤 input difference 通過 cipher 的傳播
  找 high-probability differential → chosen-plaintext attack
  AES 防禦：max DP = 2^(-6), 4 輪 ≥ 25 active S-box → provably secure

Linear Cryptanalysis：
  找 input/output bit 的線性近似（偏離 1/2 的 bias）
  需要 1/ε² 個 known-plaintext
  AES 防禦：max bias = 2^(-3), 同樣的 active S-box 下界 → provably secure

Algebraic Attack：
  把 cipher 表示成多項式方程組，用 Gröbner basis 或 SAT 解
  對 stream cipher 有效，對 AES 全 rounds 無效

Meet-in-the-Middle：
  從加密和解密方向同時搜索，在中間碰頭
  把 2DES 的安全性從 2^112 降到 2^57
  → 這就是為什麼用 3DES（3 × 56 = 168 bit key, 有效 112 bit）

AES 的設計哲學（Wide Trail Strategy）：
  SubBytes   → 低 max DP 和 bias 的 S-box
  ShiftRows  → 防止 difference 局限在一個 column
  MixColumns → branch number = 5，擴散到多列
  → 4 輪後 ≥ 25 active S-boxes → differential/linear 都不可行
```

---

## 自我檢核

- [ ] 能用自己的話解釋 differential cryptanalysis 的核心 idea（追蹤 input difference）
- [ ] 能解釋 DDT 是什麼、max differential probability 的意義
- [ ] 能用自己的話解釋 linear cryptanalysis 的核心 idea（找 XOR 的線性近似）
- [ ] 能解釋 Piling-up lemma 為什麼讓多輪的 linear bias 指數衰減
- [ ] 能解釋 MITM attack 為什麼讓 2DES 的有效安全性從 2^112 降到 2^57
- [ ] 能解釋 AES 的 Wide Trail Strategy 如何同時抵擋 differential 和 linear
- [ ] 能區分 theoretical break、practical break、academic break
- [ ] 知道 DES 的 S-box 被 NSA 修改過的歷史

---

## 延伸閱讀

- **"Differential Cryptanalysis of DES-like Cryptosystems"（Biham & Shamir, Journal of Cryptology 1991）**
  - **讀哪裡**：Section 2（差分的定義）和 Section 4（對 DES 的攻擊）
  - **學什麼**：differential cryptanalysis 的開山之作；數學清晰，流程完整
  - **關聯**：本章 differential 段落的原始論文

- **"Linear Cryptanalysis Method for DES Cipher"（Matsui, EUROCRYPT 1993）**
  - **讀哪裡**：Section 2（線性近似的定義）和 Section 3（Piling-up lemma）
  - **學什麼**：linear cryptanalysis 的完整方法論——從 LAT 到串聯多輪到 key recovery
  - **關聯**：本章 linear 段落的原始論文

- **"The Design of Rijndael"（Daemen & Rijmen, Springer 2002）**
  - **讀哪裡**：Ch 9（Wide Trail Strategy）和 Ch 7（安全性分析）
  - **學什麼**：AES 設計者如何用 branch number 和 active S-box counting 證明安全性
  - **關聯**：本章 AES 安全性論證的權威來源

- **"Biclique Cryptanalysis of the Full AES"（Bogdanov et al., ASIACRYPT 2011）**
  - **讀哪裡**：Section 3（biclique 構造）
  - **學什麼**：目前對 AES 的最佳攻擊——從 2^128 降到 2^(126.1)，為什麼這不代表 AES 被破
  - **關聯**：本章「practical vs theoretical break」的具體例子

---

→ [Ch 42 — 收尾：密碼工程的 Do/Don't](./42-wrap-up.md)
