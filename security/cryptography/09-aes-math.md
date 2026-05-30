# Ch 9 — GF(2⁸) 從零推導：SubBytes 與 MixColumns 的數學基礎

> 目標：從零建構 GF(2⁸) 有限體的加法和乘法，推導 AES 中 SubBytes（S-box = GF(2⁸) 取逆 + affine transform）和 MixColumns（GF(2⁸) 上矩陣乘法）的完整數學過程。讀完這章後你能用 Python 從頭生成 AES S-box，並理解為什麼 Rijndael 的每一步都不是任意的。

## 環境

| 工具 | 版本 |
|------|------|
| Python | 3.11+ |
| Ubuntu | 22.04 |
| 額外套件 | 無（本章純手刻） |

```bash
python3 --version   # 確認 3.11+
```

## 為什麼 AES 需要有限體

「為什麼不用普通的模 256 算術？」這是最常見的問題。

答案分三層：

1. **可逆性（Invertibility）**：AES 每一步都必須可逆（解密要走回去）。GF(2⁸) 是一個 field，**每個非零元素都有乘法逆元**。普通的 Z/256Z 不是 field — 例如 2 × 128 = 256 ≡ 0 (mod 256)，所以 2 沒有乘法逆元。
2. **Confusion**：Shannon 定義的 confusion 要求密文的每一位都和 key 有複雜的非線性關係。GF(2⁸) 的乘法逆元提供了高度非線性。
3. **Diffusion**：MixColumns 在 GF(2⁸) 上做矩陣乘法，保證一個 byte 的變動擴散到整個 column。普通 XOR 做不到這種擴散。

**一句話：AES 選 GF(2⁸) 是因為它是最小的、能在 8-bit 上運算的 field，同時提供可逆性、非線性和擴散。**

## 先建立直覺：什麼是有限體

有限體（Finite Field / Galois Field）是一個有限集合，上面定義了加法和乘法，滿足：

- 加法和乘法都**封閉**（結果還在集合裡）
- 加法和乘法都有**逆元**（除了乘法的零）
- 分配律成立

最簡單的有限體：GF(2) = {0, 1}，加法是 XOR，乘法是 AND。

```
GF(2) 加法：          GF(2) 乘法：
0 + 0 = 0             0 × 0 = 0
0 + 1 = 1             0 × 1 = 0
1 + 0 = 1             1 × 0 = 0
1 + 1 = 0  ← XOR!    1 × 1 = 1
```

GF(2⁸) 是 GF(2) 的擴展 — 把 8 個 GF(2) 元素排成多項式，定義新的乘法規則。

## 核心概念：GF(2⁸) 的構造

### 多項式表示

GF(2⁸) 的每個元素是一個最高 7 次的多項式，**係數在 GF(2) 中**（也就是 0 或 1）。

一個 byte `0xB6 = 0b10110110` 對應多項式：

```
x⁷ + x⁵ + x⁴ + x² + x¹
│     │    │    │    │
bit7  bit5 bit4 bit2 bit1
```

所以 GF(2⁸) 有 2⁸ = 256 個元素，恰好對應 0x00 到 0xFF。

### 加法 = XOR

兩個多項式相加，係數在 GF(2) 上做加法（XOR）：

```
  (x⁷ + x⁵ + x⁴ + x² + x)       = 0xB6
+ (x⁷ + x⁶ + x⁵ + x)             = 0xE2
──────────────────────────
  (x⁶ + x⁴ + x²)                  = 0x54
```

注意 x⁷ + x⁷ = 0（GF(2) 中 1+1=0），x⁵ + x⁵ = 0，x + x = 0。

**在 byte 層級，GF(2⁸) 的加法就是 XOR。**

```python
# GF(2⁸) 加法
def gf_add(a, b):
    return a ^ b

assert gf_add(0xB6, 0xE2) == 0x54
```

### 不可約多項式（Irreducible Polynomial）

乘法比較麻煩。兩個多項式相乘可能超過 7 次，需要 mod 一個 8 次多項式把結果壓回去。

AES 選的不可約多項式：

```
m(x) = x⁸ + x⁴ + x³ + x + 1     (hex: 0x11B)
```

為什麼選這個？因為它在 GF(2) 上**不可分解**（irreducible），就像整數中的質數。GF(2) 上 8 次不可約多項式有 30 個，Rijndael 選了 `0x11B`（沒有特殊的數學原因，就是從中挑了一個）。

### 乘法：多項式乘法 mod m(x)

```
a(x) × b(x) mod m(x)
```

具體做法（shift-and-XOR，又叫 Russian Peasant Multiplication）：

```python
def gf_mul(a, b):
    """GF(2⁸) 乘法：多項式乘法 mod x⁸+x⁴+x³+x+1"""
    result = 0
    for i in range(8):
        if b & 1:          # b 的第 i 位是 1 → 加上 a×xⁱ
            result ^= a
        a = xtime(a)       # a ← a × x (mod m(x))
        b >>= 1
    return result
```

關鍵的 `xtime` 操作：

```python
def xtime(a):
    """在 GF(2⁸) 中乘以 x（左移一位，溢出就 XOR 0x1B）"""
    a <<= 1
    if a & 0x100:          # 超過 8 bit → 需要 mod m(x)
        a ^= 0x11B         # x⁸ mod m(x) = x⁴+x³+x+1 = 0x1B
    return a & 0xFF
```

**為什麼 `xtime` 是 XOR `0x1B` 而不是 `0x11B`？** 左移一位後第 8 bit 是 1，表示多項式有 x⁸ 項。x⁸ mod m(x) = x⁴+x³+x+1（因為 m(x) = x⁸ + x⁴+x³+x+1，所以 x⁸ = x⁴+x³+x+1 mod m(x)）。XOR `0x11B` 的效果和 XOR `0x1B` 再清掉 bit 8 相同，最後 `& 0xFF` 確保結果是 8 bit。

### 驗證

```python
# 0x57 × 0x83 在 GF(2⁸) 中
# FIPS-197 附錄的範例
assert gf_mul(0x57, 0x83) == 0xC1

# 乘法逆元驗證
assert gf_mul(0x57, 0xFE) == 0x01  # 0xFE 是 0x57 的逆元
```

## 底層機制：SubBytes 的數學推導

### 第一步：GF(2⁸) 求逆

S-box 的第一步是對每個 byte 取 GF(2⁸) 乘法逆元（0 映射到 0）。

為什麼取逆能提供 non-linearity？考慮函數 f(x) = x⁻¹ 在 GF(2⁸) 中：

```
如果 x 變一點，x⁻¹ 的變化和 x 之間沒有簡單的線性關係。
這正是 confusion 需要的 — 讓攻擊者無法用線性方程組描述 S-box。
```

用數學語言：GF(2⁸) 的逆元映射有最高的代數次數（algebraic degree = 7），意味著它用 GF(2) 上的多項式表示時需要 7 次項。線性函數的代數次數是 1，次數越高越「非線性」。

**暴力求逆**：

```python
def gf_inverse(a):
    """用暴力法找 GF(2⁸) 的乘法逆元"""
    if a == 0:
        return 0
    for x in range(1, 256):
        if gf_mul(a, x) == 1:
            return x
    raise ValueError(f"No inverse for {a}")  # 不應該到這裡
```

更高效的方式是用 Fermat's little theorem：在 GF(2⁸) 中，a⁻¹ = a²⁵⁴（因為 a²⁵⁵ = 1）：

```python
def gf_inverse_fermat(a):
    """用 Fermat's little theorem: a⁻¹ = a^(2⁸-2) = a^254"""
    if a == 0:
        return 0
    result = a
    for _ in range(6):        # 計算 a^254
        result = gf_mul(result, result)  # 平方
        result = gf_mul(result, a)       # 乘以 a
    result = gf_mul(result, result)      # 最後一次平方（不乘 a）
    return result
```

### 第二步：Affine Transform

取逆後，還需要一個 affine transform（仿射變換）。為什麼？

**因為 GF(2⁸) 的逆元雖然非線性，但它有一個弱點：它是自己的逆（involution）— 也就是 f(f(x)) = x。這會讓 S-box 有不想要的「不動點」結構。加上 affine transform 打破這個對稱性。**

Affine transform 的定義：

```
b = A · a ⊕ c
```

其中 A 是 8×8 binary 矩陣，c = 0x63：

```
A = ┌ 1 0 0 0 1 1 1 1 ┐     c = 0x63 = 0b01100011
    │ 1 1 0 0 0 1 1 1 │
    │ 1 1 1 0 0 0 1 1 │
    │ 1 1 1 1 0 0 0 1 │
    │ 1 1 1 1 1 0 0 0 │
    │ 0 1 1 1 1 1 0 0 │
    │ 0 0 1 1 1 1 1 0 │
    └ 0 0 0 1 1 1 1 1 ┘
```

這個矩陣是 circular — 每行是上一行右移一位。用 Python 實作：

```python
def affine_transform(byte):
    """AES S-box 的 affine 部分"""
    result = 0
    c = 0x63
    for i in range(8):
        # 對 byte 的每個 bit，做 circular shift 後 XOR
        bit = 0
        for j in range(8):
            # A[i][j] = 1 if (i+j)%8 的位置，具體是 circular pattern
            bit ^= (byte >> ((i + j) % 8)) & 1
        result |= (bit << i)
    return result ^ c
```

更直接的實作方式（位操作）：

```python
def affine_transform_bitwise(b):
    """用 bit rotation 實作 affine transform"""
    # b ⊕ ROTL(b,1) ⊕ ROTL(b,2) ⊕ ROTL(b,3) ⊕ ROTL(b,4) ⊕ 0x63
    def rotl8(x, n):
        return ((x << n) | (x >> (8 - n))) & 0xFF
    return b ^ rotl8(b,1) ^ rotl8(b,2) ^ rotl8(b,3) ^ rotl8(b,4) ^ 0x63
```

### 完整 S-box 生成

```python
def generate_sbox():
    """從零生成 AES S-box"""
    sbox = [0] * 256
    for i in range(256):
        inv = gf_inverse(i)                  # 第一步：GF(2⁸) 取逆
        sbox[i] = affine_transform_bitwise(inv)  # 第二步：affine transform
    return sbox

sbox = generate_sbox()

# 驗證已知值
assert sbox[0x00] == 0x63   # 0 的逆定義為 0，affine(0) = 0x63
assert sbox[0x01] == 0x7C   # 1 的逆是 1，affine(1) = 0x7C
assert sbox[0x53] == 0xED   # FIPS-197 的範例值
```

完整 S-box 表（和 FIPS-197 附錄 B 一致）：

```
     0  1  2  3  4  5  6  7  8  9  A  B  C  D  E  F
0   63 7C 77 7B F2 6B 6F C5 30 01 67 2B FE D7 AB 76
1   CA 82 C9 7D FA 59 47 F0 AD D4 A2 AF 9C A4 72 C0
2   B7 FD 93 26 36 3F F7 CC 34 A5 E5 F1 71 D8 31 15
3   04 C7 23 C3 18 96 05 9A 07 12 80 E2 EB 27 B2 75
...（共 256 bytes）
```

## 進一步用法：MixColumns 的數學

### MixColumns 的定義

AES 的 state 是 4×4 byte 矩陣。MixColumns 對每一 **column**（4 bytes）做矩陣乘法：

```
┌ d₀ ┐     ┌ 02 03 01 01 ┐   ┌ s₀ ┐
│ d₁ │  =  │ 01 02 03 01 │ × │ s₁ │    (GF(2⁸) 上)
│ d₂ │     │ 01 01 02 03 │   │ s₂ │
└ d₃ ┘     └ 03 01 01 02 ┘   └ s₃ ┘
```

這裡 `02`、`03`、`01` 是 GF(2⁸) 中的元素。乘法和加法都在 GF(2⁸) 上進行。

### 為什麼是這個矩陣

這個矩陣是 **Maximum Distance Separable (MDS)**：任意 4×4 子矩陣的行列式非零。

MDS 的效果：**如果 input column 有 k 個 byte 不同於零（1 ≤ k ≤ 4），output 至少有 5-k 個 byte 不同於零。** 也就是改 1 byte 影響至少 4 byte，改 2 byte 影響至少 3 byte。

這保證了最大的 diffusion — 結合 ShiftRows（跨 column 打散），兩輪之後每一個 output byte 都依賴所有 16 個 input byte。

### MixColumns 實作

```python
def mix_column(col):
    """MixColumns 的一個 column（4 bytes）"""
    a = col[:]
    d = [0] * 4
    d[0] = gf_mul(0x02, a[0]) ^ gf_mul(0x03, a[1]) ^ a[2] ^ a[3]
    d[1] = a[0] ^ gf_mul(0x02, a[1]) ^ gf_mul(0x03, a[2]) ^ a[3]
    d[2] = a[0] ^ a[1] ^ gf_mul(0x02, a[2]) ^ gf_mul(0x03, a[3])
    d[3] = gf_mul(0x03, a[0]) ^ a[1] ^ a[2] ^ gf_mul(0x02, a[3])
    return d
```

注意：乘以 `0x02` 就是 `xtime`，乘以 `0x03` = `xtime(a) ^ a`。所以 MixColumns 只需要 `xtime` 和 XOR。

### InvMixColumns

解密需要逆矩陣：

```
┌ 0E 0B 0D 09 ┐
│ 09 0E 0B 0D │
│ 0D 09 0E 0B │
└ 0B 0D 09 0E ┘
```

驗證：兩個矩陣相乘（在 GF(2⁸) 上）= 單位矩陣。

```python
def inv_mix_column(col):
    """InvMixColumns"""
    a = col[:]
    d = [0] * 4
    d[0] = gf_mul(0x0E,a[0]) ^ gf_mul(0x0B,a[1]) ^ gf_mul(0x0D,a[2]) ^ gf_mul(0x09,a[3])
    d[1] = gf_mul(0x09,a[0]) ^ gf_mul(0x0E,a[1]) ^ gf_mul(0x0B,a[2]) ^ gf_mul(0x0D,a[3])
    d[2] = gf_mul(0x0D,a[0]) ^ gf_mul(0x09,a[1]) ^ gf_mul(0x0E,a[2]) ^ gf_mul(0x0B,a[3])
    d[3] = gf_mul(0x0B,a[0]) ^ gf_mul(0x0D,a[1]) ^ gf_mul(0x09,a[2]) ^ gf_mul(0x0E,a[3])
    return d
```

## AES 四步操作總覽

| 操作 | 提供什麼 | 數學基礎 |
|------|----------|----------|
| SubBytes | Confusion（非線性） | GF(2⁸) 逆元 + affine |
| ShiftRows | Diffusion（行間混合） | byte 位移（trivial） |
| MixColumns | Diffusion（列內混合） | GF(2⁸) MDS 矩陣乘法 |
| AddRoundKey | Key dependency | XOR |

每步的完整流程圖在 Ch 10。

## 對比與取捨

| | GF(2⁸) (AES) | Z/256Z | GF(2)⁸ (向量空間) |
|---|---|---|---|
| 加法 | XOR | mod 256 加法 | XOR（相同） |
| 乘法 | 多項式乘法 mod m(x) | mod 256 乘法 | 無乘法定義 |
| 是 field? | 是 | 否（2,4,... 無逆元） | 否（無乘法） |
| 逆元存在? | 所有非零元素 | 只有奇數 | N/A |
| 硬體效率 | 查表 / AES-NI | 普通 ALU | 普通 XOR |
| AES 適用 | ✓ 完美 | ✗ 不可逆 | ✗ 無乘法 |

## 踩雷集錦

### 雷 1：GF(2⁸) 不是 Z/256Z

```python
# 錯誤理解
def wrong_gf_add(a, b):
    return (a + b) % 256   # 這是 Z/256Z，不是 GF(2⁸)

# 正確
def correct_gf_add(a, b):
    return a ^ b            # GF(2⁸) 加法是 XOR

# 0x01 + 0x01：
# Z/256Z: 2
# GF(2⁸): 0 （自己的加法逆元是自己）
```

### 雷 2：S-box 不是隨機表

有人看到 S-box 以為是「隨機選的 256 個 byte 的排列」。

**S-box 每一個值都有嚴格的數學推導**：GF(2⁸) 取逆 → affine transform。這個結構保證了：
- 最高的代數次數（algebraic degree = 7）
- 最低的差分均勻度（differential uniformity = 4）
- 沒有不動點（S(x) ≠ x 對所有 x）
- 沒有反不動點（S(x) ≠ x̄ 對所有 x）

### 雷 3：xtime 溢出忘記 XOR

```python
# 錯誤：只做左移
def wrong_xtime(a):
    return (a << 1) & 0xFF

# 正確：溢出時 XOR 0x1B
def correct_xtime(a):
    result = a << 1
    if result & 0x100:
        result ^= 0x11B
    return result & 0xFF
```

### 雷 4：MixColumns 和 SubBytes 順序弄反

AES 加密的順序是 SubBytes → ShiftRows → MixColumns → AddRoundKey。**最後一輪沒有 MixColumns**。

弄反順序不會報錯，但你的結果和 FIPS-197 不一致 — 而且可能引入安全漏洞。

### 雷 5：把 affine transform 中的 XOR 0x63 忘了

```python
# 錯：只做矩陣乘法，忘了加常數
def wrong_affine(b):
    return b ^ rotl8(b,1) ^ rotl8(b,2) ^ rotl8(b,3) ^ rotl8(b,4)
    # 少了 ^ 0x63

# 對：
def correct_affine(b):
    return b ^ rotl8(b,1) ^ rotl8(b,2) ^ rotl8(b,3) ^ rotl8(b,4) ^ 0x63
```

## 進階：GF(2⁸) 的代數結構

### 為什麼 GF(2⁸) 存在

有限體存在的充要條件：元素個數是質數的冪次 p^n。

- GF(2) = 2¹：存在
- GF(4) = 2²：存在
- GF(256) = 2⁸：存在
- GF(6)：**不存在**（6 = 2×3，不是質數冪次）

### 生成元（Generator）

GF(2⁸) 的乘法群（去掉 0）是 cyclic group，有生成元 g 使得 g⁰, g¹, ..., g²⁵⁴ 跑遍所有 1~255。

AES 的 m(x) = x⁸+x⁴+x³+x+1 下，g = 0x03 是一個生成元：

```python
# 驗證 0x03 是生成元
seen = set()
val = 1
for i in range(255):
    seen.add(val)
    val = gf_mul(val, 0x03)
assert len(seen) == 255  # 確實跑遍了 1~255
assert val == 1           # g^255 = 1
```

有了生成元，可以用 log/exp 表加速乘法：

```python
# 建 log 和 exp 表
LOG = [0] * 256
EXP = [0] * 256
val = 1
for i in range(255):
    EXP[i] = val
    LOG[val] = i
    val = gf_mul(val, 0x03)
EXP[255] = EXP[0]  # wrap around

def gf_mul_fast(a, b):
    """用 log/exp 表做 GF(2⁸) 乘法"""
    if a == 0 or b == 0:
        return 0
    return EXP[(LOG[a] + LOG[b]) % 255]

def gf_inverse_fast(a):
    """用 log/exp 表求逆"""
    if a == 0:
        return 0
    return EXP[255 - LOG[a]]
```

### Differential Uniformity（差分均勻度）

AES S-box 的差分均勻度是 4：對任意非零 Δx，方程 S(x ⊕ Δx) ⊕ S(x) = Δy 最多有 4 個解。

這是 8-bit 排列能達到的**最佳值**（理論下界是 2，稱為 Almost Perfect Nonlinear / APN，但 8-bit APN 排列至今沒找到）。

差分均勻度越低，差分密碼分析（differential cryptanalysis）越難突破。

## 動手練習

1. **手算 GF(2⁸) 乘法**：不用程式，用紙筆算 0x57 × 0x13 mod (x⁸+x⁴+x³+x+1)。提示：展開成多項式乘法再 mod。

2. **完整 S-box 生成**：用本章的 `gf_mul`、`gf_inverse`、`affine_transform_bitwise` 生成完整的 256-byte S-box，和 FIPS-197 附錄 B 對照。

3. **MixColumns 驗證**：取 FIPS-197 附錄 B 的某一輪的 state，手動對一個 column 做 MixColumns，驗證結果正確。

4. **生成元搜尋**：寫程式找出 GF(2⁸) 在 m(x) = 0x11B 下的所有生成元。共有多少個？（提示：歐拉函數 φ(255)）

## 重點整理

- GF(2⁸) 的加法是 XOR，乘法是多項式乘法 mod 不可約多項式 x⁸+x⁴+x³+x+1
- `xtime` 操作 = 左移一位，溢出則 XOR 0x1B — 這是 GF(2⁸) 乘法的基礎
- SubBytes = GF(2⁸) 取逆（提供非線性）+ affine transform（打破逆元的對稱性）
- MixColumns = GF(2⁸) 上的 MDS 矩陣乘法（提供最大擴散）
- AES 的每一步都有明確的數學目的：SubBytes 負責 confusion，ShiftRows + MixColumns 負責 diffusion，AddRoundKey 混入 key
- GF(2⁸) ≠ Z/256Z — 前者是 field，後者不是

## 自我檢核

- [ ] 能用 Python 從零實作 GF(2⁸) 加法和乘法
- [ ] 能解釋 xtime 操作為什麼是 XOR 0x1B
- [ ] 能說出 SubBytes 的兩步（取逆 + affine）各自提供什麼安全性質
- [ ] 能解釋 MDS 矩陣的擴散保證
- [ ] 能用手刻的程式生成正確的 AES S-box

## 延伸閱讀

- **FIPS-197**：AES 標準文件，§4.2 Multiplication, §5.1.1 SubBytes, §5.1.3 MixColumns
- **Daemen & Rijmen "The Design of Rijndael" (2002)**：作者自己的設計書，Chapter 3 詳述數學選擇
- **Nyberg 1994 "Differentially Uniform Mappings for Cryptography"**：S-box 設計的理論基礎
- **Lidl & Niederreiter "Finite Fields" (1997)**：有限體的完整數學參考

---

> **下一章**：[Ch 10 — AES 完整實作：Rijndael 全流程與 AES-NI](10-aes-implementation.md) — 把這章的數學組裝成完整的 AES-128 encrypt/decrypt。
