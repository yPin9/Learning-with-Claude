# Ch 7 — Block Cipher 基礎：Feistel 與 SPN

> **目標**：理解 Feistel network 和 SPN（Substitution-Permutation Network）兩種結構的差異，能口頭解釋 IND-CPA 安全定義，知道為什麼 block cipher 本身不安全（必須配合 mode of operation）。

## 為什麼需要這個？

Ch 6 的結論：完美保密需要和明文等長的 key，工程上不實用。現代密碼學的核心問題變成：**能不能用一個短 key（如 128 或 256 bit）安全地加密任意長度的訊息？**

答案是 block cipher + mode of operation。但這兩者必須分開理解：

- **Block cipher**：一個用固定 key 把固定長度的明文 block 加密成等長密文 block 的函式
- **Mode of operation**：把 block cipher 應用到任意長度訊息的方法（ECB、CBC、CTR 等，Ch 10 詳講）

這一章講 block cipher 本身——它的兩種主流結構（Feistel 和 SPN）、它的數學定義（pseudorandom permutation）、以及為什麼單獨使用它是不安全的。

## 先建立直覺

Block cipher 的核心概念：

```
明文 block（固定長度）    key（固定長度）
      │                      │
      ▼                      ▼
  ┌─────────────────────────────┐
  │        Block Cipher          │
  │   E: {0,1}ⁿ × {0,1}ᵏ →    │
  │         {0,1}ⁿ              │
  └─────────────────────────────┘
                │
                ▼
      密文 block（和明文等長）

n = block size（DES: 64 bit, AES: 128 bit）
k = key size（DES: 56 bit, AES: 128/192/256 bit）

給定一個 key K，E(K, ·) 是一個 permutation（雙射）：
  每個不同的明文 block 映射到不同的密文 block
  可以反向解密：D(K, ·) = E(K, ·)⁻¹
```

為什麼是 permutation？因為如果兩個不同明文映射到相同密文，解密就不唯一了。

Block cipher 的設計目標：讓 E(K, ·) 看起來像一個**隨機選取的 permutation**——也就是 pseudorandom permutation（PRP）。

## Feistel Network：DES 的結構

### 核心概念

Feistel network 是 Horst Feistel（IBM）在 1970 年代提出的結構。DES（Ch 8）和許多後來的 cipher 都基於它。

關鍵洞察：你不需要設計一個完整的 permutation——只需要設計一個**任意的 round function F**（不需要可逆），Feistel 結構保證整體可逆。

### 一輪 Feistel 的 ASCII 圖

```
輸入 block（2n bits）
┌──────────┬──────────┐
│  Lᵢ      │   Rᵢ     │    （左半 n bits + 右半 n bits）
└────┬─────┴────┬─────┘
     │          │
     │          ├────────→ F(Kᵢ, Rᵢ)
     │          │               │
     │     ┌────┘               │
     │     │                    │
     ▼     ▼                    ▼
   Lᵢ₊₁ = Rᵢ          Rᵢ₊₁ = Lᵢ ⊕ F(Kᵢ, Rᵢ)
┌──────────┬──────────┐
│  Lᵢ₊₁    │  Rᵢ₊₁    │
└──────────┴──────────┘

加密：Lᵢ₊₁ = Rᵢ
      Rᵢ₊₁ = Lᵢ ⊕ F(Kᵢ, Rᵢ)

解密（反過來跑）：
      Rᵢ = Lᵢ₊₁
      Lᵢ = Rᵢ₊₁ ⊕ F(Kᵢ, Lᵢ₊₁)

注意：解密不需要 F 的反函式！只需要 F 本身。
這就是 Feistel 的精髓——F 可以是任意函式，整體仍然可逆。
```

### 多輪 Feistel

```
明文 (L₀ | R₀)
    │
    ▼
 Round 1: K₁ → F(K₁, R₀)  → L₁=R₀, R₁=L₀⊕F(K₁,R₀)
    │
    ▼
 Round 2: K₂ → F(K₂, R₁)  → L₂=R₁, R₂=L₁⊕F(K₂,R₁)
    │
    ▼
   ...
    │
    ▼
 Round r: Kᵣ → F(Kᵣ, Rᵣ₋₁) → Lᵣ=Rᵣ₋₁, Rᵣ=Lᵣ₋₁⊕F(Kᵣ,Rᵣ₋₁)
    │
    ▼
密文 (Lᵣ | Rᵣ)

DES: r=16 輪
Blowfish: r=16 輪
Camellia: r=18 或 24 輪
```

### Python 實作

```python
def feistel_encrypt(block: bytes, round_keys: list[bytes], f) -> bytes:
    """通用 Feistel 加密。block 長度必須為偶數。"""
    n = len(block) // 2
    L, R = block[:n], block[n:]

    for ki in round_keys:
        new_L = R
        fout = f(ki, R)
        new_R = bytes(a ^ b for a, b in zip(L, fout))
        L, R = new_L, new_R

    return L + R

def feistel_decrypt(block: bytes, round_keys: list[bytes], f) -> bytes:
    """解密：round key 順序反轉"""
    return feistel_encrypt(block, round_keys[::-1], f)

# 一個玩具 round function（不安全，僅示範結構）
import hashlib

def toy_f(key: bytes, data: bytes) -> bytes:
    h = hashlib.sha256(key + data).digest()
    return h[:len(data)]

# 測試
plaintext = b"ABCDEFGH"  # 8 bytes = 64 bits
keys = [f"key{i}".encode() for i in range(16)]  # 16 輪

ct = feistel_encrypt(plaintext, keys, toy_f)
pt = feistel_decrypt(ct, keys, toy_f)

print(f"明文: {plaintext}")
print(f"密文: {ct.hex()}")
print(f"解密: {pt}")
assert pt == plaintext
```

## SPN（Substitution-Permutation Network）：AES 的結構

### 核心概念

SPN 是 AES 使用的結構。和 Feistel 不同，SPN 在每一輪對**整個 block** 做操作（不是只處理一半）。

SPN 的每一輪有三個操作：
1. **Substitution（S-box）**：非線性替換，提供 confusion
2. **Permutation / Linear mixing**：位元重排或線性變換，提供 diffusion
3. **Key addition（XOR round key）**：混入 key

### AES 一輪的流程圖

```
輸入 State（128 bits = 4×4 bytes 矩陣）
│
├─→ SubBytes    ：每個 byte 通過同一個 S-box（256→256 的查表）
│                  → confusion（非線性）
│
├─→ ShiftRows   ：每一行循環左移不同的量
│                  Row 0: 不動
│                  Row 1: 左移 1 byte
│                  Row 2: 左移 2 bytes
│                  Row 3: 左移 3 bytes
│                  → 跨 column 擴散
│
├─→ MixColumns  ：每一 column 做 GF(2⁸) 上的矩陣乘法
│                  → 同一 column 內的 bytes 互相影響
│                  → diffusion（線性但在 GF(2⁸) 上）
│
└─→ AddRoundKey ：整個 state 和 round key 做 XOR
                   → 混入 key

AES-128: 10 輪（最後一輪省略 MixColumns）
AES-192: 12 輪
AES-256: 14 輪
```

### Feistel vs SPN 的根本差異

Feistel 每輪只處理一半的 block，靠 L/R 交換把資訊擴散到另一半。SPN 每輪處理整個 block。

```
Feistel:                        SPN:
┌─────┬─────┐                  ┌───────────┐
│  L  │  R  │                  │  整個     │
│     │──→F─│─⊕→              │  block    │
│  ⊕←─│     │                  │           │
│     │     │                  │  SubBytes │
└─────┴─────┘                  │ ShiftRows │
每輪只有一半被 F 處理            │MixColumns │
另一半原封不動搬過去              │AddRoundKey│
需要 2 輪才能讓所有 bits 被影響   └───────────┘
                                每輪所有 bits 都被處理
```

## IND-CPA 安全定義

Block cipher 的設計目標是做一個好的 PRP（pseudorandom permutation）。但把 block cipher 用於加密時，需要更嚴格的安全定義：**IND-CPA**（Indistinguishability under Chosen-Plaintext Attack）。

### Challenger Game（挑戰者遊戲）

```
    攻擊者 A                          挑戰者 C
    ─────────                         ────────
                                      隨機選 key K
                                      隨機選 bit b ∈ {0, 1}
    
    1. A 選兩個等長明文 m₀, m₁  ──→  C
    
    2. C 計算 c* = Enc(K, m_b)  ──→  A
       （如果 b=0 加密 m₀；b=1 加密 m₁）
    
    3. A 輸出猜測 b' ∈ {0, 1}
    
    A 可以重複步驟 1-2 多次（多次查詢）
    
    安全定義：對所有 polynomial-time A：
      |Pr[b' = b] - 1/2| ≤ negligible
    
    白話：A 猜對的機率不能顯著超過 1/2（亂猜）
```

### 為什麼 Block Cipher + ECB 不滿足 IND-CPA

ECB（Electronic Codebook）模式：把明文分成 block，每個 block 獨立加密。

```
ECB 加密：
明文:  [B₁][B₂][B₃][B₄]
        │    │    │    │
        ▼    ▼    ▼    ▼
       E_K  E_K  E_K  E_K
        │    │    │    │
        ▼    ▼    ▼    ▼
密文:  [C₁][C₂][C₃][C₄]

問題：相同的明文 block 永遠產生相同的密文 block！
```

IND-CPA 攻擊 ECB：

```
1. A 送 m₀ = [X][X]（兩個相同的 block）
        m₁ = [X][Y]（兩個不同的 block）

2. C 回傳 c* = Enc(K, m_b)

3. A 檢查：如果 c* 的前半和後半相同 → b = 0
            如果 c* 的前半和後半不同 → b = 1

   A 的猜測正確率 = 100%
```

ECB 是 deterministic 的——同一個 key 下，同一個明文永遠產生同一個密文。這讓 IND-CPA 瞬間崩潰。

經典視覺化：ECB 模式加密的 Linux 企鵝圖片（Tux），加密後輪廓仍然清晰可見——因為相同顏色（相同 block）的像素加密成相同密文。

```python
# 示範 ECB 的 deterministic 問題
from hashlib import sha256

def toy_ecb_encrypt(blocks: list[bytes], key: bytes) -> list[bytes]:
    """玩具版 ECB：每個 block 獨立加密"""
    return [sha256(key + b).digest()[:len(b)] for b in blocks]

key = b"mysecretkey"
b1 = b"AAAAAAAA"
b2 = b"BBBBBBBB"

# 加密 [A][A]
ct1 = toy_ecb_encrypt([b1, b1], key)
print(f"[A][A] → [{ct1[0].hex()[:8]}][{ct1[1].hex()[:8]}]")
print(f"  前半 == 後半? {ct1[0] == ct1[1]}")  # True!

# 加密 [A][B]
ct2 = toy_ecb_encrypt([b1, b2], key)
print(f"[A][B] → [{ct2[0].hex()[:8]}][{ct2[1].hex()[:8]}]")
print(f"  前半 == 後半? {ct2[0] == ct2[1]}")  # False

# 攻擊者一看就知道哪個是 [A][A]
```

## 對比與取捨

| 特性 | Feistel Network | SPN |
|---|---|---|
| 代表 cipher | DES, Blowfish, Camellia | AES, PRESENT, GIFT |
| 每輪處理 | 半個 block | 整個 block |
| Round function 要求 | 不需可逆（任意 F） | 每個操作必須可逆（S-box 雙射）|
| 加解密對稱性 | 結構相同，key 順序反轉 | 結構不同，需要 inverse S-box |
| 硬體效率 | 加解密共用電路 | 加密和解密需要不同電路（除非用 CTR mode）|
| Diffusion 速度 | 慢（2 輪才影響全部 bits）| 快（1 輪就影響全部 bits）|
| 安全性證明 | Luby-Rackoff 定理：3 輪 Feistel + PRF → PRP | 無直接結構性證明，靠個別分析 |

## 踩雷集錦

1. **「Block cipher 就是加密」**：block cipher 是一個 PRP（pseudorandom permutation），不是加密方案。加密方案 = block cipher + mode of operation + padding。單獨使用 block cipher（ECB）不滿足 IND-CPA。

2. **「Feistel 比 SPN 弱」**：兩者都能設計出安全的 cipher。DES 的問題是 key 太短（56 bit），不是 Feistel 結構的問題。Camellia（Feistel）的安全性和 AES（SPN）相當。

3. **「S-box 越大越好」**：S-box 越大，confusion 越好，但實作成本（查找表大小）也越高。AES 的 8-bit S-box（256 entries）是在安全性和效率之間的甜蜜點。DES 的 6→4 bit S-box 其實也夠用（問題在 key 長度）。

4. **「IND-CPA 就夠了」**：IND-CPA 只保證被動竊聽的安全。如果攻擊者能修改密文（active attack），還需要 IND-CCA（Ch 25 AEAD 會講）。

5. **「AES 有 10 輪所以比 DES 的 16 輪弱」**：輪數不能跨 cipher 比較。AES 每輪處理整個 128-bit block（SPN），DES 每輪只處理 32 bits（Feistel 的一半）。AES 的 10 輪提供的 diffusion 遠超 DES 的 16 輪。

## 進階：再往深一層

### Luby-Rackoff 定理

Michael Luby 和 Charles Rackoff 在 1988 年證明：如果 F 是一個 secure PRF（pseudorandom function），那麼 3 輪 Feistel network 是 secure PRP，4 輪是 strong PRP（加密和解密都 indistinguishable from random）。

這個定理的意義：它把 Feistel 結構的安全性歸約到 round function F 的安全性。你只需要設計一個好的 F，Feistel 結構保證整體是安全的 PRP。

```
PRF（F 足夠好）
    ↓ Luby-Rackoff
3-round Feistel = PRP（加密方向 indistinguishable）
    ↓
4-round Feistel = Strong PRP（加解密都 indistinguishable）
```

### Avalanche Effect（雪崩效應）

好的 block cipher 應該滿足：明文改變 1 bit → 密文改變約 50% 的 bits。這叫 avalanche effect，是 diffusion 的量化指標。

```python
def count_bit_diff(a: bytes, b: bytes) -> int:
    return sum(bin(x ^ y).count('1') for x, y in zip(a, b))

# 用 AES 測試 avalanche effect
from hashlib import sha256

def toy_block_cipher(key: bytes, block: bytes) -> bytes:
    """模擬 block cipher（用 SHA-256 的前 16 bytes 近似）"""
    return sha256(key + block).digest()[:16]

key = b"0123456789ABCDEF"
p1 = b"ABCDEFGHIJKLMNOP"
p2 = bytearray(p1)
p2[0] ^= 0x01  # 只改 1 bit
p2 = bytes(p2)

c1 = toy_block_cipher(key, p1)
c2 = toy_block_cipher(key, p2)

diff = count_bit_diff(c1, c2)
print(f"明文差異: 1 bit")
print(f"密文差異: {diff} bits / {len(c1)*8} bits = {diff/(len(c1)*8)*100:.1f}%")
# 好的 cipher 應該接近 50%
```

### PRP vs PRF

- **PRF**（Pseudorandom Function）：`F: K × X → Y`，key 固定後看起來像隨機函式
- **PRP**（Pseudorandom Permutation）：`E: K × X → X`，key 固定後看起來像隨機 permutation（雙射）

Block cipher 的目標是 PRP。當 block size 足夠大時，PRP 和 PRF 幾乎無法區分（PRF/PRP switching lemma：區分的 advantage ≤ q²/2ⁿ，其中 q 是查詢次數，n 是 block size）。

## 動手練習

1. **實作 Feistel**：用上面的程式碼框架，把 round 數從 16 改成 1、2、3，觀察 avalanche effect 的變化。幾輪之後才能達到接近 50% 的 bit 翻轉率？

2. **ECB 視覺化**：用 Python Pillow 庫，把一張 BMP 圖片的像素資料用 ECB mode 加密（每 16 bytes 一組），觀察加密後的圖片是否還能看出原始輪廓

3. **IND-CPA 遊戲**：寫一個程式模擬 IND-CPA challenger game。讓「攻擊者」針對 ECB 模式總是能以 100% 正確率猜出 b；換成加了隨機 IV 的 CBC 模式後，正確率降到 ~50%

4. **Feistel 可逆性驗證**：在 `toy_f` 中故意使用一個不可逆的 hash function，驗證 Feistel 解密仍然正確——體會「F 不需要可逆」這個性質

## 本章重點整理

- Block cipher 是固定長度 block 的加密原語（PRP），分為 Feistel（DES）和 SPN（AES）兩大結構；Feistel 的 round function 不需可逆，SPN 的每個操作都必須可逆
- IND-CPA 是「攻擊者選兩個明文，看到其中一個的密文，猜不出是哪個」的 challenger game；ECB mode 因為 deterministic 而無法滿足 IND-CPA
- Block cipher 本身不是加密方案——必須搭配 mode of operation 才能安全地加密任意長度的訊息

## 自我檢核

- [ ] 能畫出 Feistel 一輪的 L/R 交換圖，並解釋為什麼 F 不需可逆
- [ ] 能列出 AES（SPN）每輪的四個操作，並說出各自提供 confusion 還是 diffusion
- [ ] 能口頭解釋 IND-CPA 的 challenger game（不看筆記）
- [ ] 能解釋為什麼 ECB 模式不滿足 IND-CPA（用具體的攻擊）
- [ ] 能區分 PRP 和 PRF，知道 block cipher 的目標是哪個

## 延伸閱讀

- **Luby & Rackoff, "How to Construct Pseudorandom Permutations from Pseudorandom Functions"（1988）**
  - **讀哪裡**：Theorem 1（3-round Feistel → PRP）的陳述和證明思路
  - **學什麼**：Feistel 結構安全性的理論基礎——為什麼 3 輪就夠
  - **關聯**：本章 Feistel 的理論保證

- **Jonathan Katz & Yehuda Lindell,《Introduction to Modern Cryptography》Ch 7**
  - **讀哪裡**：Block cipher 的定義、PRP/PRF 的形式化、IND-CPA 的完整定義
  - **學什麼**：本章直覺版概念的嚴格數學定義
  - **關聯**：想要看完整證明時的首選教科書

- **NIST, "Recommendation for Block Cipher Modes of Operation" (SP 800-38A)**
  - **讀哪裡**：Section 6（各 mode 的定義）
  - **學什麼**：ECB/CBC/CFB/OFB/CTR 五種基本 mode 的官方規格
  - **關聯**：Ch 10 會詳講 mode of operation，這是權威參考

→ [Ch 8 DES 與 3DES](./08-des-3des.md)
