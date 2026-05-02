# Ch 8 — DES / 3DES

> 目標：看 DES 怎麼從 IBM Lucifer 變 NIST 1977 標準，NSA 改 S-box 的「陰謀論」實際發生了什麼（differential cryptanalysis 預先防禦），3DES 的過渡角色，以及為什麼最終被 AES 取代。

## DES 的歷史

```
1971  IBM 的 Horst Feistel 設計 Lucifer cipher
1973  NBS（後來叫 NIST）發 RFI 找加密標準
1974  IBM 提交 Lucifer 變體
1975  NBS 公開草案 — 首次公開的政府密碼標準
1977  正式發布為 DES（Data Encryption Standard）
        FIPS PUB 46
        block size 64-bit, key 56-bit
1999  正式 deprecated
2005  完全 withdrawn
```

DES 是密碼學史上**第一個公開、廣泛使用、可被學術研究**的密碼標準。它的存在催生整個現代密碼學社群 — 之前研究密碼學的人多在 NSA / GCHQ，1977 之後外面也有了具體目標。

## DES 結構：16 輪 Feistel

```
64-bit plaintext
       │
       ▼
Initial Permutation (IP)   ← 重排 64 bit
       │
       ▼
   ┌──────────┐
   │ Round 1  │ ← 用 round_key_1 (48-bit)
   └──────────┘
       │
   ┌──────────┐
   │ Round 2  │
   └──────────┘
       │
       ...
       │
   ┌──────────┐
   │ Round 16 │
   └──────────┘
       │
       ▼
Final Permutation (IP⁻¹)
       │
       ▼
64-bit ciphertext
```

每個 round 是標準 Feistel：

```
L_{i+1} = R_i
R_{i+1} = L_i XOR F(R_i, k_{i+1})
```

F 函式：

```
R_i (32-bit)
       │
       ▼
Expansion E (擴展到 48-bit，重複部分 bit)
       │
       ▼
   XOR round_key_{i+1} (48-bit)
       │
       ▼
切成 8 個 6-bit 群組 → 8 個 S-box（每個 6→4 bit）→ 32-bit
       │
       ▼
Permutation P (32-bit 重排)
       │
       ▼
F(R_i, k) (32-bit 輸出)
```

S-box 是 DES 的核心**非線性元件**。8 個 S-box 各是 6→4 bit 查表（4 行 × 16 列）。

## 那個 NSA 改 S-box 的故事

IBM 原始 Lucifer 的 S-box 與 NSA 修改後的 DES S-box 不同。NSA 還把 key size 從 128-bit 改成 56-bit。

這個介入引發數十年陰謀論：

**陰謀論版**：「NSA 留了後門」。

**真相版**（1990 年代揭露）：

- **Eli Biham 與 Adi Shamir 1990 發現 differential cryptanalysis** — 一種強力新攻擊
- 用這個攻擊**原始 Lucifer 弱很多**
- DES 的 NSA-改 S-box **對 differential attack 反而強**
- IBM 後來承認他們 1974 就知道 differential attack 但被 NSA 要求保密
- **NSA 提早 15 年知道 differential**，幫忙改 S-box 讓 DES 更強

這是密碼學史的奇景：**NSA 介入讓演算法更安全**（雖然他們同時把 key size 砍小，留下 brute-force 餘地）。

## 56-bit Key 的問題

DES key 是 64-bit 但每 byte 留一個 parity bit，**有效 56-bit**。

```
1977：56-bit 在當時 brute-force 約 2 萬美元、1 年（理論）
1998：EFF 的 DES Cracker 機器，56 小時破譯（25 萬美元）
2007：FPGA 集群 < 24 小時
2024：雲端 GPU 集群 < 1 小時
```

Moore's law + ASIC / FPGA 讓 56-bit 早就不夠。DES 1999 deprecated 主要是 brute-force 問題，**不是演算法本身被破**。

實際上 DES 的演算法強度直到今天都「**ok 但 key 太短**」。algorithmic attack 最好成績是 differential / linear，但需要 2⁴³ 個 known plaintext — 仍比 brute-force 更貴。

## 3DES：暫時補救

```
3DES (Triple DES, TDES):
  C = DES_enc(k₃, DES_dec(k₂, DES_enc(k₁, P)))
                                ↑
                           注意中間是 dec
```

中間用 dec 是為了 **key1 = key2 = key3 時退化成普通 DES**（向下兼容）。

key 模式：

- **3-key 3DES (3TDEA)**：k₁, k₂, k₃ 三把獨立 → 168-bit key（有效 112-bit 安全，因 meet-in-the-middle）
- **2-key 3DES (2TDEA)**：k₁ = k₃，k₂ 獨立 → 112-bit key（有效 80-bit）

**3DES 慢**（每 block 跑 3 次 DES，比 AES 慢 3-4 倍），且 64-bit block size 是 hard limit：

> **Sweet32 attack (2016)**：64-bit block 在 birthday bound 2³² block 後（約 32 GB）開始洩漏。HTTPS 用 3DES 的 long-lived TLS connection 在 2016 被實戰攻擊。

NIST 2018 disallow 3DES 用於新系統，2023 完全 retire。

## DES 的遺產

DES 雖死，遺產仍在：

1. **Feistel 結構**：Blowfish、Camellia、TEA 沿用
2. **S-box 設計方法論**：NSA 的 differential-resistant S-box 設計影響 AES
3. **block cipher modes**（ECB、CBC、CFB、OFB、CTR）：原本為 DES 定義，AES 沿用
4. **公開標準審查流程**：1977 DES 開的先河，2001 AES 競賽繼承

學 DES 的價值不在「現在會用它」，是**理解 block cipher design 的演化路徑**。

## DES 程式碼（教學用）

實際你不會自己刻 DES（沒人用了），但範例感受 Feistel：

```python
# 純教學版，不要 production 用
def des_round(L, R, round_key):
    """一個 Feistel round"""
    return R, L ^ des_F(R, round_key)

def des_F(R, K):
    """F 函式：E -> XOR K -> S-box -> P"""
    expanded = expand_32_to_48(R)
    mixed = expanded ^ K
    s_output = apply_8_sboxes(mixed)
    return permute_P(s_output)

# 完整 DES
def des_encrypt(plaintext_64, key_56):
    round_keys = key_schedule(key_56)
    block = initial_permutation(plaintext_64)
    L, R = block >> 32, block & 0xFFFFFFFF
    for i in range(16):
        L, R = des_round(L, R, round_keys[i])
    L, R = R, L  # 最後一輪不交換
    final = (L << 32) | R
    return inverse_initial_permutation(final)
```

**Python `pycryptodome` 提供**（給 legacy 系統）：

```python
from Crypto.Cipher import DES
key = b"8byteKey"  # 64-bit (有效 56)
cipher = DES.new(key, DES.MODE_ECB)
ct = cipher.encrypt(b"8byteMsg")
print(ct.hex())
```

只在「**我必須跟某個 1990 系統互通**」才這樣寫。新系統一律用 AES。

## DESX：另一條補救

DES + key whitening：

```
C = DES_enc(k, P XOR k₁) XOR k₂
```

把 k₁, k₂ 各 64-bit 額外與 input/output XOR。**有效 key 約 119-bit**。

DESX 是 1984 RSA 公司提的快速補救（當時還不知道未來會有 AES）。比 3DES 快很多但仍受 64-bit block 限制。**今天沒人用**。

## AES 的競賽

DES 退場後 NIST 1997 開 AES 競賽：

```
1997  RFP 發布
1998  15 個 candidate
1999  5 個 finalist：MARS, RC6, Rijndael, Serpent, Twofish
2000  Rijndael 勝出
2001  FIPS 197 正式發布
```

選 Rijndael（比利時 Joan Daemen + Vincent Rijmen 設計）的理由：

- 性能：軟硬體都好
- 安全：沒已知 weakness
- 結構：SPN 比 Feistel 更現代
- 簡單：規格相對短

**Serpent 安全更強**（更多輪）但慢；**Twofish 快**但結構複雜。Rijndael 是 sweet spot。

下一章我們從 AES 數學開始，看 GF(2⁸) 怎麼運作。

## 一個常見誤解

「DES 被破是因為 NSA 留了後門」

**不是**。DES 56-bit key 的限制在 1977 年標準化時就**公開**告知（甚至比 IBM 原本提的 128-bit 還弱）。NSA 的算盤是「**夠商業用，我們可以 brute-force**」 — 這個 trade-off 公開討論過。

被破不是後門，是**Moore's law 把 56-bit brute-force 從天文成本拉到日常成本**。NSA 在 1977 估的「我們有預算破，普通公司沒有」，到 1998 EFF 用 25 萬美元做出來時就破功。

## 自我檢核

- [ ] 我能畫出 DES 的整體流程（IP → 16 輪 → IP⁻¹）
- [ ] 我能說出 DES F 函式的步驟（E → XOR → S-box → P）
- [ ] 我能解釋 NSA 改 S-box 的真相（differential resistance）
- [ ] 我能說出 3DES 為什麼中間用 dec
- [ ] 我能解釋 Sweet32 attack 為什麼 64-bit block 是 hard limit
- [ ] 我知道 AES 競賽的 5 個 finalist 與選擇 Rijndael 的理由

下一章從 AES 的數學基礎開始 — GF(2⁸) 從零教，看 SubBytes 與 MixColumns 為什麼不是 random。

→ [Ch 9 AES 數學](./09-aes-math.md)
