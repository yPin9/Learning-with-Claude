# Ch 8 — DES 與 3DES

> **目標**：理解 DES 的 Feistel 結構和 56-bit key，能解釋 NSA 改 S-box 的歷史爭議，知道 3DES 的 EDE 結構和為什麼被淘汰。

## 為什麼需要這個？

DES（Data Encryption Standard）是密碼學史上最重要的 block cipher——不是因為它現在還安全（早就不安全了），而是因為：

1. 它是第一個由政府標準化的 cipher（1977 年，NIST 前身 NBS 發布）
2. 它的設計過程引發了「NSA 是在幫忙還是植入後門？」的歷史性爭議
3. 它的被破促成了 AES 的誕生（Ch 9）
4. 它的 Feistel 結構和 S-box 設計至今影響密碼學

3DES 是 DES 的延壽方案——用三次 DES 來補救 key 太短的問題。但 64-bit block size 在高吞吐量場景下有致命缺陷（Sweet32 攻擊），2023 年正式退役。

理解 DES 的設計和死亡，你才能理解為什麼 AES 要求 128-bit block、為什麼 key schedule 設計很重要、為什麼「短 key」是不可修補的致命傷。

## 先建立直覺

DES 的全貌：

```
明文（64 bits）
│
├── Initial Permutation (IP)
│
├── 16 輪 Feistel
│   │
│   ├── Round 1:  L₀, R₀ → L₁=R₀, R₁=L₀ ⊕ F(K₁, R₀)
│   ├── Round 2:  L₁, R₁ → L₂=R₁, R₂=L₁ ⊕ F(K₂, R₁)
│   ├── ...
│   └── Round 16: L₁₅, R₁₅ → L₁₆, R₁₆
│
├── 32-bit swap（最後一輪不 swap——等價於做了但不顯示）
│
└── Final Permutation (IP⁻¹)
│
密文（64 bits）

Key（實際 56 bits，含 8 bits parity）
│
└── Key Schedule → 生成 K₁, K₂, ..., K₁₆（每個 48 bits）
```

重要數字：
- **Block size**: 64 bits
- **Key size**: 56 bits（輸入 64 bits 但 8 bits 是 parity，不參與加密）
- **Rounds**: 16
- **Round key size**: 48 bits

## DES 的 Round Function F

這是 DES 的核心——每一輪中，F 函式處理右半 32 bits：

```
R（32 bits）
│
├── Expansion (E)：32 bits → 48 bits
│   把 32 bits 擴展成 48 bits（某些 bits 重複使用）
│   → 讓 32-bit R 能和 48-bit round key 做 XOR
│
├── ⊕ Kᵢ（48-bit round key）
│
├── S-boxes：48 bits → 32 bits
│   分成 8 組（每組 6 bits）
│   每組通過一個 S-box（6→4 bits 的查表）
│   8 × 4 = 32 bits
│   → 這是 DES 唯一的非線性操作（confusion 的來源）
│
└── Permutation (P)：32 bits → 32 bits
    位元重排
    → diffusion：讓一個 S-box 的輸出影響下一輪多個 S-box 的輸入

F(Kᵢ, R) = P( S( E(R) ⊕ Kᵢ ) )
```

### S-box 的細節

每個 S-box 是一個 6→4 bits 的函式，用 4×16 的查表實現：

```
輸入 6 bits: b₁b₂b₃b₄b₅b₆
  行號 = b₁b₆（最外兩 bits）→ 0-3
  列號 = b₂b₃b₄b₅（中間四 bits）→ 0-15

S-box 1 的查表（歷史真實值）：
     0  1  2  3  4  5  6  7  8  9 10 11 12 13 14 15
0: [14  4 13  1  2 15 11  8  3 10  6 12  5  9  0  7]
1: [ 0 15  7  4 14  2 13  1 10  6 12 11  9  5  3  8]
2: [ 4  1 14  8 13  6  2 11 15 12  9  7  3 10  5  0]
3: [15 12  8  2  4  9  1  7  5 11  3 14 10  0  6 13]
```

S-box 是整個 DES 的安全核心。沒有 S-box，DES 就是一堆線性操作（XOR + permutation），可以用線性代數秒破。

## 底層機制：它是怎麼運作的？

### Key Schedule

DES 從 56-bit master key 生成 16 個 48-bit round key：

```
64-bit 輸入 key（含 8 parity bits）
│
├── PC-1 (Permuted Choice 1)：64 → 56 bits（丟掉 parity）
│   分成 C₀（28 bits）和 D₀（28 bits）
│
├── Round 1: C₁ = LS₁(C₀), D₁ = LS₁(D₀)  → PC-2 → K₁ (48 bits)
├── Round 2: C₂ = LS₂(C₁), D₂ = LS₂(D₁)  → PC-2 → K₂
├── ...
└── Round 16: C₁₆ = LS₁₆(C₁₅), D₁₆ = LS₁₆(D₁₅) → PC-2 → K₁₆

LS = Left Shift（循環左移 1 或 2 bits，視輪數而定）
PC-2 (Permuted Choice 2)：56 → 48 bits（選取 + 重排）
```

### Python 簡化實作

```python
# DES 的 S-box（只列 S1，完整版有 8 個）
S1 = [
    [14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7],
    [0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8],
    [4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0],
    [15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13],
]

def sbox_lookup(sbox: list[list[int]], six_bits: int) -> int:
    """6-bit 輸入 → 4-bit 輸出"""
    row = ((six_bits >> 5) & 1) << 1 | (six_bits & 1)
    col = (six_bits >> 1) & 0xF
    return sbox[row][col]

# 測試
inp = 0b100110  # 6 bits
out = sbox_lookup(S1, inp)
print(f"S1({inp:06b}) = {out:04b} ({out})")

# 展示 S-box 的非線性：改 1 bit 輸入，觀察輸出變化
print("\nS-box 非線性測試（翻轉每一 bit）:")
for bit in range(6):
    flipped = inp ^ (1 << bit)
    out_flipped = sbox_lookup(S1, flipped)
    diff = out ^ out_flipped
    print(f"  翻轉 bit {bit}: {flipped:06b} → {out_flipped:04b}  (差異: {bin(diff).count('1')} bits)")
```

## NSA 和 S-box：後門還是加固？

這是密碼學史上最精彩的爭議之一。

### 時間線

1. **1973**：NBS（現 NIST）公開徵求加密標準
2. **1974**：IBM 提交 Lucifer cipher（128-bit key）
3. **1975**：NBS 發布修改後的 DES——key 被砍到 56 bits，S-box 被 NSA 改過
4. **1975-1977**：學術界炸鍋——Diffie 和 Hellman 公開質疑 NSA 是否植入了後門

### 爭議焦點

```
IBM 原版 Lucifer        NSA 修改後的 DES
──────────────        ──────────────
128-bit key      →    56-bit key（砍了一半！）
Lucifer S-boxes  →    不同的 S-boxes（NSA 的設計）
```

學術界的懷疑：
- 56-bit key 是不是故意留弱讓 NSA 能暴力破解？
- S-box 是不是藏了 trapdoor？

### 1994 年的揭曉

Don Coppersmith（IBM）在 1994 年終於公開了 DES S-box 的設計準則。NSA 修改 S-box 的原因是：**他們知道 differential cryptanalysis（差分密碼分析）**，但這個技術要到 1990 年才被 Biham 和 Shamir 「重新發現」。

NSA 在 1970 年代就已經知道差分密碼分析，並且修改了 S-box 使其抵抗這種攻擊。IBM 原版的 S-box 不夠強。

S-box 的設計準則包括：
- 每個 S-box 的每個輸出 bit 都不是輸入 bits 的線性函式
- 改變 1 個輸入 bit，至少 2 個輸出 bits 改變
- 輸入和輸出的 XOR distribution table 盡可能均勻（抵抗差分分析）

```
結論：
  S-box → NSA 是在加強 DES，不是植入後門
  56-bit key → 這個就比較曖昧了。NSA 可能確實希望保留暴力破解的能力。
               1998 年 EFF 的 Deep Crack 用 25 萬美元和 56 小時暴力破了 DES，
               而 NSA 的預算...遠不止 25 萬美元。
```

## 56-bit Key 的死亡

### EFF Deep Crack（1998）

Electronic Frontier Foundation（EFF）花了 25 萬美元建造了一台專用硬體 "Deep Crack"：

```
Deep Crack 規格：
  1,856 個自製 ASIC 晶片
  每秒測試 ~9 × 10¹⁰ 個 DES key
  平均破解時間：56 小時（最壞 ~112 小時）
  造價：$250,000（1998 年）

DES key space = 2⁵⁶ ≈ 7.2 × 10¹⁶
以今天的硬體，暴力破解 DES 只要幾小時。
```

Deep Crack 的意義不在於它有多強大，而在於它證明了 56-bit key **對任何有點預算的攻擊者都不安全**。

## 3DES：延壽方案

### EDE 結構

3DES（Triple DES）用三個 DES 操作串聯，但不是三次加密（EEE），而是 Encrypt-Decrypt-Encrypt（EDE）：

```
明文 → DES_Encrypt(K₁) → DES_Decrypt(K₂) → DES_Encrypt(K₃) → 密文

為什麼是 EDE 而不是 EEE？
  → 向後相容：如果 K₁ = K₂ = K₃，EDE 退化為單 DES
    E(K) → D(K) → E(K) = E(K)（中間的 D 和第一個 E 抵消）
  → 這讓 3DES 系統能和只支援 DES 的舊系統互通
```

### Key 選項

```
Keying Option 1: K₁ ≠ K₂ ≠ K₃（三個獨立 key）
  有效 key: 168 bits
  實際安全: ~112 bits（因為 meet-in-the-middle）

Keying Option 2: K₁ ≠ K₂, K₃ = K₁（兩個獨立 key）
  有效 key: 112 bits
  實際安全: ~80 bits（較弱，但常用）

Keying Option 3: K₁ = K₂ = K₃（等同單 DES）
  有效 key: 56 bits
  只用於向後相容，不提供額外安全
```

### 為什麼不是 168-bit 安全？Meet-in-the-Middle 攻擊

```
2DES 的情況（假設 2DES = E(K₂, E(K₁, P))）：
  對已知 plaintext-ciphertext pair (P, C)：
  1. 對所有 2⁵⁶ 個 K₁，計算 E(K₁, P)，存入表
  2. 對所有 2⁵⁶ 個 K₂，計算 D(K₂, C)，在表中查找匹配
  3. 時間 = 2 × 2⁵⁶ ≈ 2⁵⁷，空間 = 2⁵⁶
  → 2DES 的 112-bit key 只提供 ~57-bit 安全！

3DES Keying Option 1 的情況：
  類似的攻擊把 168-bit key 打到 ~112-bit 安全
  仍然足夠用（2¹¹² 次操作在可見的未來不可行）
```

```python
# 概念展示：meet-in-the-middle 攻擊 2DES
# （用小 key space 的玩具 cipher 代替真 DES）

def toy_encrypt(key: int, block: int) -> int:
    """4-bit key, 8-bit block 的玩具 cipher"""
    return (block + key * 37) & 0xFF

def toy_decrypt(key: int, block: int) -> int:
    """暴力找反函式（玩具版才能這樣做）"""
    for p in range(256):
        if toy_encrypt(key, p) == block:
            return p
    return -1

# 2DES: C = E(K2, E(K1, P))
P, K1_real, K2_real = 0x42, 7, 11
intermediate = toy_encrypt(K1_real, P)
C = toy_encrypt(K2_real, intermediate)

# Meet-in-the-middle 攻擊
forward = {}  # E(k1, P) → k1
for k1 in range(16):  # 4-bit key space
    forward[toy_encrypt(k1, P)] = k1

for k2 in range(16):
    mid = toy_decrypt(k2, C)
    if mid in forward:
        k1_found = forward[mid]
        print(f"找到 key pair: K1={k1_found}, K2={k2}")
        if k1_found == K1_real and k2 == K2_real:
            print("  ← 正確的 key!")
```

## Sweet32 攻擊：64-bit Block 的問題

3DES 的 key 長度補好了，但 64-bit block size 帶來了另一個問題。

### Birthday Bound

在 CBC 模式下，加密 2^(n/2) 個 block 後，兩個密文 block 相同的機率超過 50%。對 64-bit block，這是 2³² 個 block ≈ 32 GB。

```
Sweet32 攻擊（2016）：
  1. 攻擊者讓受害者（如 HTTPS 連線）傳送大量已知明文
  2. 在 ~32 GB 資料後，兩個 CBC 密文 block 碰撞
  3. CBC 的碰撞洩漏明文 XOR：
     Cᵢ = Cⱼ → E(K, Pᵢ ⊕ Cᵢ₋₁) = E(K, Pⱼ ⊕ Cⱼ₋₁)
     → Pᵢ ⊕ Cᵢ₋₁ = Pⱼ ⊕ Cⱼ₋₁
     → 已知 Cᵢ₋₁, Cⱼ₋₁, 和其中一個 P → 推出另一個 P
  
  在 HTTPS 場景下，32 GB 看似很多，
  但長時間的 session（或 HTTP/2 multiplexing）可以累積到。
  
  → NIST 2023 年正式禁止 3DES 用於加密
```

## 對比與取捨

| 特性 | DES | 3DES (Keying Option 1) | AES-128 |
|---|---|---|---|
| 發布年份 | 1977 | 1998 標準化 | 2001 |
| Block size | 64 bits | 64 bits | 128 bits |
| Key size | 56 bits | 168 bits | 128 bits |
| 有效安全 | ~0（可暴力）| ~112 bits | ~128 bits |
| 速度 | 慢 | DES 的 1/3 | 快（有 AES-NI）|
| Birthday bound | 2³² blocks | 2³² blocks | 2⁶⁴ blocks |
| 現狀 | 已淘汰 | 2023 禁止加密 | 現行標準 |

## 踩雷集錦

1. **「3DES 有 168-bit 安全」**：meet-in-the-middle 攻擊把它打到 ~112 bits。Keying Option 2 更慘，只有 ~80 bits。

2. **「DES 的問題是演算法不好」**：DES 的 Feistel 結構和 S-box 設計在 1977 年是頂級水準。問題是 56-bit key 太短。如果 IBM 的原始提案（128-bit key）被採用，DES 可能撐更久。

3. **「NSA 在 DES 裡放了後門」**：如上所述，S-box 的修改是加強而非削弱。但 56-bit key 的選擇確實可疑——NSA 可能希望保留暴力破解的能力。

4. **「3DES 已經完全淘汰」**：NIST 在 2023 年禁止 3DES 用於加密，但仍允許用於解密（讀取舊資料）。遺留系統（legacy systems）中仍然大量存在 3DES，尤其是金融業的支付系統。

5. **「DES 的 Initial Permutation 增加安全性」**：IP 和 FP（Final Permutation）不增加任何密碼學安全性——它們是為了方便 1970 年代的硬體實作而設計的位元重排。移除它們不影響 DES 的安全強度。

## 進階：再往深一層

### Differential Cryptanalysis vs DES

Biham 和 Shamir 在 1990 年「重新發現」了差分密碼分析。他們的攻擊對 full 16-round DES 需要 2⁴⁷ 個 chosen plaintext——比暴力搜尋（2⁵⁵ 個 known plaintext）好，但 chosen plaintext 的要求讓它在實務上難以執行。

有趣的是，NSA 在設計 DES S-box 時已經知道差分分析，並且做了特別設計讓 DES 抵抗它。Coppersmith 公布的 S-box 設計準則之一就是：每個 S-box 的差分分布表中，最大機率不超過 1/4。

### Linear Cryptanalysis

Matsui 在 1993 年提出線性密碼分析，對 DES 需要 2⁴³ 個 known plaintext。這是第一個在實驗上成功破解 full DES 的密碼分析方法（比暴力搜尋快，且只需要 known plaintext 而非 chosen plaintext）。

```
DES 的攻擊歷史：
  1977: 發布
  1990: 差分分析（2⁴⁷ chosen PT，理論上）
  1993: 線性分析（2⁴³ known PT，實驗成功）
  1998: Deep Crack 暴力破解（56 小時）
  1999: distributed.net + Deep Crack（22 小時 15 分）
  2017+: 隨便一塊 FPGA 就能在幾小時內暴力破解
```

### DES 變體：DESX 和 DES-X

RIVEST 在 1984 年提出 DESX：

```
DESX(K1, K2, K3, P) = K3 ⊕ DES(K2, K1 ⊕ P)

在 DES 前後各 XOR 一個 whitening key。
把暴力搜尋從 2⁵⁶ 提升到 ~2¹²⁰（在不考慮其他攻擊下）。
比 3DES 快（只做一次 DES），但安全性分析不如 3DES 成熟。
```

## 動手練習

1. **S-box 分析**：寫程式生成 DES S1 的完整差分分布表（DDT）。對每個輸入差分 Δx（6 bits），計算所有輸入 x 產生的輸出差分 Δy = S(x) ⊕ S(x⊕Δx) 的分布。最大的非零 entry 是多少？

2. **Key space 計算**：如果你有一台每秒能測試 10¹² 個 DES key 的機器，平均多久能暴力破解 DES？3DES（112-bit 有效安全）呢？

3. **Meet-in-the-middle 實作**：用上面的玩具 cipher，實作完整的 meet-in-the-middle 攻擊。統計需要多少個 known plaintext-ciphertext pair 才能唯一確定 key pair

4. **Birthday bound 計算**：用 Python 模擬 birthday problem——隨機產生 64-bit 值，觀察多少次後出現碰撞（用 set 檢測）。這個數字和理論值 2³² ≈ 4.3×10⁹ 差多少？

## 本章重點整理

- DES 是 16-round Feistel cipher，56-bit key、64-bit block；S-box 是唯一的非線性元件，提供 confusion；NSA 修改 S-box 是為了加強抵抗差分分析，不是植入後門
- 56-bit key 在 1998 年被 EFF Deep Crack 以 25 萬美元暴力破解；3DES 用 EDE 結構延壽，但 meet-in-the-middle 讓 168-bit key 只有 ~112-bit 有效安全
- 3DES 的 64-bit block size 導致 Sweet32 攻擊（birthday bound 在 2³² blocks ≈ 32 GB），NIST 2023 年正式禁止 3DES 用於加密

## 自我檢核

- [ ] 能說出 DES 的 block size、key size、round 數
- [ ] 能解釋 DES round function F 的四個步驟（Expansion → XOR key → S-boxes → Permutation）
- [ ] 能說出 NSA 修改 S-box 的真正原因（抵抗差分分析，不是後門）
- [ ] 能解釋 3DES 為什麼用 EDE 而不是 EEE（向後相容）
- [ ] 能解釋為什麼 3DES 168-bit key 只有 ~112-bit 安全（meet-in-the-middle）
- [ ] 知道 Sweet32 攻擊的原因（64-bit block 的 birthday bound）

## 延伸閱讀

- **Don Coppersmith, "The Data Encryption Standard (DES) and its strength against attacks"（1994）**
  - **讀哪裡**：Section 3（S-box 設計準則）
  - **學什麼**：NSA 修改 S-box 的真正理由——他們在 1970 年代就知道差分分析
  - **關聯**：本章 NSA 爭議的第一手資料

- **EFF,《Cracking DES: Secrets of Encryption Research, Wiretap Politics & Chip Design》（1998）**
  - **讀哪裡**：Deep Crack 的設計和政治脈絡
  - **學什麼**：為什麼 EFF 要花 25 萬美元建一台破 DES 的機器——這是政治聲明也是技術展示
  - **關聯**：本章 Deep Crack 段落的完整故事

- **Karthikeyan Bhargavan & Gaetan Leurent, "On the Practical (In-)Security of 64-bit Block Ciphers"（2016）**
  - **讀哪裡**：Sweet32 攻擊的描述（Section 4）
  - **學什麼**：64-bit block 在長 session 下的 birthday 攻擊實務細節
  - **關聯**：本章 Sweet32 段落的原始論文

→ [Ch 9 AES：現代對稱加密的基石](./09-aes.md)
