# Ch 9 — AES 數學：GF(2⁸) 與 SubBytes / MixColumns 推導

> 目標：從零教 GF(2⁸) 有限體（Rijndael 多項式 x⁸+x⁴+x³+x+1）、為什麼 SubBytes 是 GF(2⁸) inverse + 仿射變換、MixColumns 是 GF(2⁸) 上 4×4 矩陣乘。看 AES 的數學選擇不是 random — 每個決定都有抗 differential / linear cryptanalysis 的理由。

## 為什麼用有限體？

block cipher 要 confusion + diffusion。**MixColumns 提供 diffusion**（一個 byte 變動影響整 column），**SubBytes 提供 confusion**（非線性）。

兩者都需要「**運算能逆**」（解密要還原）+「**運算性質好**」（不是 random）。

整數 mod 256 雖然有加法，但乘法不形成 group（256 不是質數，多數元素沒乘法逆）。**有限體 GF(2⁸) 解決這個** — 每個非零元素都有乘法逆，且大小剛好 256（一個 byte）。

## GF(2)：最小有限體

```
GF(2) = {0, 1}
加法 = XOR
乘法 = AND
```

```
+ | 0 1        × | 0 1
─ | ─ ─        ─ | ─ ─
0 | 0 1        0 | 0 0
1 | 1 0        1 | 0 1
```

兩個元素，運算結果不出 {0, 1}。**bit 級運算自然形成 GF(2)**。

## GF(2)[x]：多項式環

把 GF(2) 升一級：**係數在 GF(2) 的多項式集合**。

```
0
1
x
x + 1
x²
x² + 1
x² + x
x² + x + 1
x³
... 共 2^(n+1) 個 deg ≤ n 的多項式
```

加法：對應係數 XOR。例：

```
(x³ + x + 1) + (x² + x) = x³ + x² + 1
```

乘法：標準多項式乘法（**係數仍 mod 2**）。例：

```
(x + 1)(x + 1) = x² + 2x + 1 = x² + 1   (2 ≡ 0 mod 2)
```

## GF(2⁸)：8-bit 多項式 mod 不可約多項式

GF(2⁸) 是**有限體**，有 256 個元素，每個用一個 8-bit byte 表示：

```
byte b₇b₆b₅b₄b₃b₂b₁b₀
↔ 多項式 b₇x⁷ + b₆x⁶ + b₅x⁵ + b₄x⁴ + b₃x³ + b₂x² + b₁x + b₀

例：
0x57 = 01010111 ↔ x⁶ + x⁴ + x² + x + 1
0x83 = 10000011 ↔ x⁷ + x + 1
```

**運算規則**：

- 加法 = XOR（多項式加法 mod 2）
- 乘法 = 多項式乘法 **再 mod 一個固定的不可約多項式**

AES 用的不可約多項式：

```
m(x) = x⁸ + x⁴ + x³ + x + 1
0x11B（9-bit 表示）
```

這個選擇是 Rijndael 設計者（Daemen / Rijmen）拍板的。**只要不可約**（在 GF(2)[x] 中無法因式分解），任何 deg-8 不可約多項式都可以 — 他們選 0x11B 是因為 Hamming weight 低、便於硬體實作。

## GF(2⁸) 加法

簡單到只能 XOR：

```python
def gf_add(a, b):
    return a ^ b

print(hex(gf_add(0x57, 0x83)))  # 0xd4
```

## GF(2⁸) 乘法

兩 byte 多項式相乘，**結果可能 deg > 7，必須 mod m(x)**：

```python
def gf_mul(a, b, mod=0x11B):
    result = 0
    while b:
        if b & 1:
            result ^= a
        # a *= x，即左移 1
        carry = a & 0x80
        a = (a << 1) & 0xFF
        if carry:
            a ^= mod & 0xFF   # 約簡：減去 m(x) 的低 8 位
        b >>= 1
    return result

print(hex(gf_mul(0x57, 0x83)))  # 0xc1
```

驗算：
- (x⁶+x⁴+x²+x+1)(x⁷+x+1) = x¹³+x¹¹+x⁹+x⁸+x⁷+x⁷+x⁵+x³+x²+x+x⁶+x⁴+x²+x+1
- 化簡 = x¹³+x¹¹+x⁹+x⁸+x⁶+x⁵+x⁴+x³+1
- mod (x⁸+x⁴+x³+x+1) = x⁷+x⁶+1
- = 0xC1 ✓

## GF(2⁸) inverse：核心 trick

每個非零元素都有 inverse（這是有限體的保證），但怎麼算？

**方法 1：擴展 Euclidean 在 GF(2)[x] 上**（類比 Ch 2 的 modular inverse）。

**方法 2：費馬小定理變體**：

```
在 GF(2⁸) 中：a^254 = a^(-1)（對所有非零 a）
因為 GF(2⁸)* 大小是 255 = 2⁸ - 1
a^255 = 1 → a^254 × a = 1 → a^254 = a⁻¹
```

```python
def gf_inv(a):
    if a == 0:
        return 0  # 0 沒 inverse，但 SubBytes 規定 0 → 0
    # a^254 = a^(128+64+32+16+8+4+2)
    result = 1
    base = a
    for _ in range(8):
        result = gf_mul(result, base)  # 不對，這是 a^9，要重寫
    # 正確：square-and-multiply
    return gf_pow(a, 254)

def gf_pow(a, n):
    result = 1
    while n:
        if n & 1:
            result = gf_mul(result, a)
        a = gf_mul(a, a)
        n >>= 1
    return result
```

**方法 3：lookup table**（production 軟體實作，事先算好 256-byte 表）。

實作上 AES 的 SubBytes 直接用 256-byte S-box（預算好）— 不在運行時跑 inverse。但**S-box 是怎麼來的就靠這個數學**。

## SubBytes：S-box 的真實意義

```
SubBytes 對每個 byte b 做：
  1. b' = b⁻¹ in GF(2⁸)（0 → 0）
  2. b'' = A · b' + c（仿射變換）
     A 是 8×8 矩陣（在 GF(2) 上）
     c = 0x63 是常數
```

仿射變換目的：**打破 GF(2⁸) inverse 的代數結構**，讓 attacker 用代數方法不容易分析。

具體 A 矩陣（標準，FIPS 197）：

```
A =
1 0 0 0 1 1 1 1
1 1 0 0 0 1 1 1
1 1 1 0 0 0 1 1
1 1 1 1 0 0 0 1
1 1 1 1 1 0 0 0
0 1 1 1 1 1 0 0
0 0 1 1 1 1 1 0
0 0 0 1 1 1 1 1
```

每個 column 是循環移位 — 設計上方便硬體實作。

**S-box 的設計原則**：

1. 抗 differential cryptanalysis：S-box 的 differential probability 低（Rijndael 的 max DP = 4/256 = 2⁻⁶）
2. 抗 linear cryptanalysis：linear bias 低
3. 完整 bit balance：每個 output bit 是所有 input bit 的非線性函式
4. 沒 fixed point（S(x) = x）也沒 anti-fixed point（S(x) = ~x）

**這些都是 Daemen / Rijmen 設計時的明確目標**，不是隨便挑的 8×8 table。

S-box 完整 256 值在 FIPS 197 公開，多數實作直接複製。

```python
SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5,
    0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    # ... 256 values total
]

def sub_bytes(state):
    return bytes(SBOX[b] for b in state)
```

## MixColumns：GF(2⁸) 矩陣乘

State（128 bit = 16 byte）排成 4×4 byte 矩陣（column-major）：

```
state =
| s_00 s_01 s_02 s_03 |
| s_10 s_11 s_12 s_13 |
| s_20 s_21 s_22 s_23 |
| s_30 s_31 s_32 s_33 |
```

MixColumns 對每個 column 獨立做：

```
| s'_0 |     | 02 03 01 01 |   | s_0 |
| s'_1 |  =  | 01 02 03 01 | × | s_1 |
| s'_2 |     | 01 01 02 03 |   | s_2 |
| s'_3 |     | 03 01 01 02 |   | s_3 |
```

矩陣乘是**在 GF(2⁸) 上**，加法 = XOR，乘法 = `gf_mul`。

具體：

```
s'_0 = (02 · s_0) ⊕ (03 · s_1) ⊕ s_2 ⊕ s_3
s'_1 = s_0 ⊕ (02 · s_1) ⊕ (03 · s_2) ⊕ s_3
s'_2 = s_0 ⊕ s_1 ⊕ (02 · s_2) ⊕ (03 · s_3)
s'_3 = (03 · s_0) ⊕ s_1 ⊕ s_2 ⊕ (02 · s_3)
```

`02 · x` 在 GF(2⁸) 是「左移 1，若 carry 則 XOR 0x1b」 — 硬體一條指令。`03 · x = 02 · x ⊕ x`。

```python
def xtime(b):
    return ((b << 1) ^ 0x1b) & 0xFF if b & 0x80 else (b << 1) & 0xFF

def mix_single_column(col):
    s0, s1, s2, s3 = col
    s_0 = xtime(s0) ^ (xtime(s1) ^ s1) ^ s2 ^ s3
    s_1 = s0 ^ xtime(s1) ^ (xtime(s2) ^ s2) ^ s3
    s_2 = s0 ^ s1 ^ xtime(s2) ^ (xtime(s3) ^ s3)
    s_3 = (xtime(s0) ^ s0) ^ s1 ^ s2 ^ xtime(s3)
    return [s_0, s_1, s_2, s_3]
```

## Branch number：MixColumns 的安全證明

「branch number」是 MixColumns 矩陣的關鍵指標：

```
branch number = min { wt(x) + wt(M·x) : x ≠ 0 }
其中 wt 是 byte-wise 非零個數
```

意思：「**input 與 output 加起來最少有幾個 byte 不為 0**」。

Rijndael 的 MixColumns matrix branch number = 5。意思：**輸入有 1 個非零 byte → 輸出至少 4 個非零 byte**（5 - 1 = 4）。

這個性質保證 **2 輪 AES 後 16 個 byte 全部受影響**（full diffusion）。實務上 AES 跑 10 / 12 / 14 輪，遠超過 full diffusion 需求。

## ShiftRows：簡單但關鍵

```
| s_00 s_01 s_02 s_03 |     | s_00 s_01 s_02 s_03 |
| s_10 s_11 s_12 s_13 |  →  | s_11 s_12 s_13 s_10 |   ← row 1 左移 1
| s_20 s_21 s_22 s_23 |     | s_22 s_23 s_20 s_21 |   ← row 2 左移 2
| s_30 s_31 s_32 s_33 |     | s_33 s_30 s_31 s_32 |   ← row 3 左移 3
```

**為什麼要這個**？沒 ShiftRows 的話，MixColumns 只在 column 內擴散，整 cipher 退化成 4 個獨立 32-bit cipher 並行。**ShiftRows 讓 column 之間混合**。

## AddRoundKey

最簡單的步驟：

```
state = state XOR round_key   (128-bit XOR)
```

整個 round key（也是 4×4 matrix）逐 byte XOR 進 state。**這是唯一引入 key 的步驟**。

## 這 4 個步驟拼起來 = AES 一輪

```
AddRoundKey
SubBytes
ShiftRows
MixColumns
AddRoundKey
SubBytes
ShiftRows
MixColumns
... 重複 (10 輪 for AES-128)
SubBytes
ShiftRows
AddRoundKey      ← 最後一輪沒 MixColumns
```

下一章 Ch 10 把這些 piece 組起來成完整 AES。

## 一個常見誤解

「為什麼 AES 用 GF(2⁸)，不用普通整數 mod 256？」

**因為要乘法逆**。整數 mod 256 不是 field — 偶數沒乘法逆（gcd(even, 256) ≠ 1）。沒乘法逆 → SubBytes 的核心步驟做不出來。

GF(2⁸) 大小恰好 256（一個 byte），且**每個非零元素都有 inverse** — 這是 field 的定義。AES 設計者選 GF(2⁸) 是**為了能做 inverse-based S-box**。

## 自我檢核

- [ ] 我能解釋 GF(2) 與 GF(2)[x] 的差別
- [ ] 我能寫 `gf_mul(a, b)` 在 GF(2⁸) 上
- [ ] 我能說出 SubBytes 的兩步（inverse + 仿射）
- [ ] 我能用 GF(2⁸) 算 `02 · 0x57` 與 `03 · 0x83`
- [ ] 我能解釋 MixColumns 的 branch number 是什麼
- [ ] 我能說出 ShiftRows 為什麼存在

下一章把 4 個步驟串成完整 AES 實作（Python 教學版 + C 性能版）。

→ [Ch 10 AES 完整實作](./10-aes-implementation.md)
