# Ch 4 — 古典密碼：從 Caesar 到 Vigenère

> **目標**：能手算 Caesar / Vigenère / monoalphabetic substitution cipher 的加解密和破譯，理解頻率分析（frequency analysis）為什麼能打爆所有 monoalphabetic cipher。

## 為什麼需要這個？

古典密碼是密碼學的起點，也是「看似安全的東西為什麼不安全」的最佳教材。

從西元前 58 年 Caesar 用 shift cipher 傳軍令，到 16 世紀 Vigenère 號稱「不可破解的密碼」（le chiffre indéchiffrable），再到 19 世紀 Babbage 和 Kasiski 把它打臉——這段歷史反覆教同一件事：

**key space 大不等於安全。**

26! ≈ 4 × 10²⁶ 看起來天文數字，但頻率分析幾分鐘就破。你在這一章學到的教訓，會一路延續到現代密碼：安全必須有數學證明，不能靠「看起來很複雜」。

## 先建立直覺

想像你要寄一封信，不想讓郵差看懂。你有幾種做法：

```
方法一：Caesar（平移）
  明文字母表：A B C D E F G H I J K L M N O P Q R S T U V W X Y Z
  密文字母表：D E F G H I J K L M N O P Q R S T U V W X Y Z A B C
  → 每個字母往後移 3 格（key = 3）
  → key space = 26（太少，暴力試完只要 26 次）

方法二：Monoalphabetic substitution（任意替換）
  明文字母表：A B C D E F G H I J K L M N O P Q R S T U V W X Y Z
  密文字母表：Q W E R T Y U I O P A S D F G H J K L Z X C V B N M
  → 每個字母對應一個任意字母（key = 整張表）
  → key space = 26! ≈ 4 × 10²⁶（看起來超大！但…）

方法三：Vigenère（多表替換）
  key = "KEY"
  第 1 個字母用 shift 10 (K)
  第 2 個字母用 shift 4  (E)
  第 3 個字母用 shift 24 (Y)
  第 4 個字母又用 shift 10 (K)…循環
  → 同一個明文字母可能加密成不同密文（polyalphabetic）
```

核心問題：為什麼方法二的 key space 有 26! 那麼大，卻還是不安全？

## Caesar Cipher：最基礎的替換

加密公式：`C = (P + k) mod 26`
解密公式：`P = (C - k) mod 26`

其中 P 是明文字母（0–25），C 是密文字母，k 是 key（shift 量）。

### 範例一：Python 實作 Caesar cipher + brute force

```python
def caesar_encrypt(plaintext: str, key: int) -> str:
    result = []
    for ch in plaintext.upper():
        if ch.isalpha():
            result.append(chr((ord(ch) - ord('A') + key) % 26 + ord('A')))
        else:
            result.append(ch)
    return ''.join(result)

def caesar_decrypt(ciphertext: str, key: int) -> str:
    return caesar_encrypt(ciphertext, -key)

# 加密
msg = "ATTACK AT DAWN"
key = 7
ct = caesar_encrypt(msg, key)
print(f"明文: {msg}")
print(f"密文: {ct}")       # HAAHJR HA KHDU
print(f"解密: {caesar_decrypt(ct, key)}")  # ATTACK AT DAWN

# Brute force：26 個 key 全試
print("\n--- Brute Force ---")
ct_unknown = "KHOOR ZRUOG"
for k in range(26):
    pt = caesar_decrypt(ct_unknown, k)
    print(f"key={k:2d}: {pt}")
    # key=3 會得到 HELLO WORLD
```

26 個 key 全試一次，人眼掃一遍就找到有意義的那個。Caesar cipher 的安全性等於零。

## 底層機制：它是怎麼運作的？

### Monoalphabetic Substitution 的結構

```
明文:  T H E  Q U I C K  B R O W N  F O X
       ↓ ↓ ↓  ↓ ↓ ↓ ↓ ↓  ↓ ↓ ↓ ↓ ↓  ↓ ↓ ↓
key:   每個字母有固定的替換（26! 種可能的替換表）
       ↓ ↓ ↓  ↓ ↓ ↓ ↓ ↓  ↓ ↓ ↓ ↓ ↓  ↓ ↓ ↓
密文:  Z I T  M X O E A  W K G V F  Y G B
```

看起來很安全，因為 key space 有 26! ≈ 4 × 10²⁶。問題在哪？

### 頻率分析：語言的指紋

英文字母不是等機率出現的。`E` 出現率 ~12.7%，`T` ~9.1%，`A` ~8.2%，而 `Z` 只有 ~0.07%。這個分布是語言的指紋，加密不會改變它——密文中出現最頻繁的字母，大概率就是 `E` 的替身。

```
英文字母頻率（%）:
E ████████████▊  12.7
T █████████▏      9.1
A ████████▏        8.2
O ███████▌         7.5
I ██████▉          7.0
N ██████▊          6.7
S ██████▎          6.3
H ██████           6.1
R █████▉           6.0
...
Z ▏                0.07
```

攻擊者拿到密文後：
1. 統計每個字母出現次數
2. 頻率最高的 → 對應 E
3. 常見二字組（digram）如 TH、HE、IN 進一步驗證
4. 逐步還原整張替換表

## 進一步用法：用頻率分析破 Monoalphabetic Cipher

### 範例二：頻率分析攻擊

```python
from collections import Counter

# 英文字母頻率（降序）
ENGLISH_FREQ = "ETAOINSHRDLCUMWFGYPBVKJXQZ"

def frequency_attack(ciphertext: str) -> dict:
    """根據頻率排序，建立密文→明文的初步映射"""
    # 只統計字母
    letters = [ch for ch in ciphertext.upper() if ch.isalpha()]
    counts = Counter(letters)

    # 按出現頻率降序排列密文字母
    cipher_by_freq = [pair[0] for pair in counts.most_common()]

    # 頻率最高的密文字母 → E，次高 → T，以此類推
    mapping = {}
    for i, cipher_letter in enumerate(cipher_by_freq):
        if i < len(ENGLISH_FREQ):
            mapping[cipher_letter] = ENGLISH_FREQ[i]

    return mapping

def apply_mapping(ciphertext: str, mapping: dict) -> str:
    result = []
    for ch in ciphertext.upper():
        if ch in mapping:
            result.append(mapping[ch].lower())  # 小寫標記「猜測」
        else:
            result.append(ch)
    return ''.join(result)

# 用 monoalphabetic cipher 加密一段英文
import random
alphabet = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
shuffled = alphabet.copy()
random.seed(42)
random.shuffle(shuffled)
sub_table = dict(zip(alphabet, shuffled))

plaintext = (
    "THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG "
    "THIS IS A SIMPLE EXAMPLE OF MONOALPHABETIC "
    "SUBSTITUTION CIPHER WHICH CAN BE BROKEN BY "
    "FREQUENCY ANALYSIS EVEN THOUGH THE KEY SPACE "
    "IS TWENTY SIX FACTORIAL"
)

ciphertext = ''.join(sub_table.get(ch, ch) for ch in plaintext)
print(f"密文: {ciphertext}\n")

# 攻擊
mapping = frequency_attack(ciphertext)
decoded = apply_mapping(ciphertext, mapping)
print(f"初步破譯: {decoded}")
print(f"\n映射表: {mapping}")
# 初步結果不完美，但 E/T/A/O 等高頻字母通常對了
# 實務上攻擊者會根據上下文手動調整
```

這段程式展示的是自動化的第一步。真正的密碼分析師會結合 digram（TH、HE）、trigram（THE、AND）、和上下文推理，幾分鐘內還原整張表。

## 對比與取捨

| 特性 | Caesar | Mono. Substitution | Vigenère |
|---|---|---|---|
| 類型 | monoalphabetic | monoalphabetic | polyalphabetic |
| key space | 26 | 26! ≈ 4×10²⁶ | 26^k（k = key length）|
| 抵抗暴力搜尋 | 否（秒破）| 是（太大）| 視 key 長度 |
| 抵抗頻率分析 | 否 | 否 | 部分（但 Kasiski 能破）|
| 破譯方法 | brute force | 頻率分析 | Kasiski + 頻率分析 |
| 核心弱點 | key 太少 | 語言統計特性 | 重複的 key pattern |

## Vigenère：看似不可破的多表替換

Vigenère cipher 的聰明之處：同一個明文字母會被加密成不同的密文字母，取決於 key 的哪個位置。

```
明文:  A T T A C K A T D A W N
key :  K E Y K E Y K E Y K E Y
密文:  K X R K G I K X B K A L

A 在位置 0 → 加密成 K（shift 10）
A 在位置 3 → 加密成 K（shift 10，剛好同 key 位置）
A 在位置 5 → 不存在這例子，但若有則 shift 不同
```

頻率分析失效了？看起來是。但 Kasiski（1863）發現了致命弱點。

### Kasiski 攻擊的邏輯

核心觀察：如果明文中有重複的片段（如 "THE" 出現兩次），而且它們剛好對齊到 key 的相同位置，密文中也會出現重複片段。

```
key length = 3, key = "KEY"

明文: ... T H E ... T H E ...
key:  ... K E Y ... K E Y ...
密文: ... D L C ... D L C ...
          ↑               ↑
      間距 = 9 = 3 × 3（key length 的倍數）
```

攻擊步驟：
1. 在密文中找所有重複的 3+ 字母片段
2. 計算每對重複片段的間距
3. 對所有間距取 GCD → 這就是 key length（或它的倍數）
4. 知道 key length 後，把密文按 key length 分組
5. 每組用的是同一個 Caesar shift → 對每組做頻率分析

```python
def kasiski_examine(ciphertext: str, min_len: int = 3) -> dict:
    """找密文中重複片段的間距"""
    ct = ciphertext.replace(" ", "").upper()
    distances = []

    for length in range(min_len, min(10, len(ct) // 2)):
        for i in range(len(ct) - length):
            pattern = ct[i:i+length]
            j = ct.find(pattern, i + 1)
            while j != -1:
                distances.append(j - i)
                j = ct.find(pattern, j + 1)

    # 統計間距的公因數
    from math import gcd
    from collections import Counter
    factor_counts = Counter()
    for d in distances:
        for f in range(2, d + 1):
            if d % f == 0:
                factor_counts[f] += 1

    return factor_counts.most_common(5)
```

知道 key length = k 後，把密文每隔 k 個字母取一個字母分成 k 組。每組中的字母都用同一個 shift 加密——回到 Caesar cipher，頻率分析又能用了。

## 踩雷集錦

1. **「Vigenère 是 unbreakable」**：19 世紀流行了 300 年的錯誤信念。Babbage 在 1854 年（未發表）和 Kasiski 在 1863 年分別獨立破解了它。polyalphabetic 不等於安全，只要 key 會重複，統計特性就會洩漏。

2. **「key space 大 = 安全」**：monoalphabetic 的 key space 有 26! ≈ 4×10²⁶（比 AES-128 的 2¹²⁸ ≈ 3.4×10³⁸ 小但仍然天文數字），可是頻率分析根本不需要暴力搜尋。安全性不只取決於 key space 大小，還取決於有沒有比暴力更快的攻擊。

3. **「加密就是把字母換掉」**：substitution 只處理了 confusion（混淆），沒有 diffusion（擴散）。明文的統計結構原封不動地映射到密文。Shannon 在 Ch 6 會正式定義這兩個概念。

4. **忽略非字母字元**：空格、標點本身就是巨大的破譯線索。真實的古典密碼通常會移除空格並分成固定長度的字母組（如 5 個一組），減少這類洩漏。

5. **Caesar 的 key = 0 或 key = 26**：兩者等價，明文 = 密文。新手容易忘記 mod 26 的邊界情況。

## 進階：再往深一層

### 完美的 substitution 存在嗎？

如果 key 和明文一樣長、每個 key 字母完全隨機、只用一次——那就是 one-time pad（OTP）。Ch 6 會證明它是「完美保密」的。古典密碼的問題不是 substitution 這個操作本身，而是 key 的重複使用讓統計攻擊成為可能。

### Index of Coincidence（IC）

William Friedman 在 1920 年代提出 IC（重合指數）：隨機抽兩個字母，它們相同的機率。英文 IC ≈ 0.065，隨機文字 IC ≈ 0.038。IC 能用來估計 Vigenère 的 key length——不同 key length 的假設下，IC 最接近英文的那個就是正確長度。

### Homophonic Substitution

為了對抗頻率分析，有人提出 homophonic substitution：高頻字母（如 E）對應多個密文符號。例如 E → {14, 27, 38, 52}，每次隨機選一個。這能壓平頻率分布，但也不是無敵的——digram 分析和更高階統計仍然能破。

## 動手練習

1. **手算 Caesar**：用 key=13（ROT13）加密 "HELLO WORLD"，再用同一個 key 解密（為什麼 ROT13 加解密相同？）

2. **寫 Vigenère**：實作 `vigenere_encrypt(plaintext, key)` 和 `vigenere_decrypt(ciphertext, key)`，測試 key="LEMON"、plaintext="ATTACKATDAWN"

3. **破譯挑戰**：以下密文是 Caesar cipher，找出 key 和明文：
   `WKLV LV D VHFUHW PHVVDJH`

4. **頻率分析實戰**：用範例二的程式，加密一段 500+ 字元的英文文章，然後只用頻率分析破譯。觀察多長的密文才能可靠地破

## 本章重點整理

- Caesar cipher 的 key space 只有 26，brute force 秒破；monoalphabetic substitution 的 key space 有 26! 但頻率分析秒破——key space 大不代表安全
- Vigenère 是 polyalphabetic cipher，同一明文字母可加密成不同密文，但 key 重複使用讓 Kasiski 攻擊和 IC 分析成為可能
- 古典密碼的核心教訓：安全性需要數學證明，不能靠「看起來很複雜」

## 自我檢核

- [ ] 能手算 Caesar cipher（key=5）對 "CRYPTO" 的加解密
- [ ] 能解釋為什麼 26! 的 key space 仍然不安全（頻率分析不需要暴力搜尋）
- [ ] 能說出 Vigenère 被破解的兩個關鍵人物和他們的方法
- [ ] 能解釋 Kasiski 攻擊的核心觀察（重複片段間距是 key length 的倍數）
- [ ] 能區分 monoalphabetic 和 polyalphabetic cipher

## 延伸閱讀

- **Simon Singh,《The Code Book》Ch 1-3**
  - **讀哪裡**：古典密碼的歷史敘事，從 Caesar 到 Vigenère 的破解
  - **學什麼**：密碼學演進的人文脈絡，為什麼 Vigenère 能騙過歐洲 300 年
  - **關聯**：本章的歷史背景來自這裡，但本章更偏技術和攻擊

- **Al-Kindi, "A Manuscript on Deciphering Cryptographic Messages"（9 世紀）**
  - **讀哪裡**：找英譯版，重點是頻率分析的原始描述
  - **學什麼**：頻率分析不是近代發明——9 世紀的阿拉伯學者 Al-Kindi 已經系統化使用
  - **關聯**：本章頻率分析的理論根源

- **Kasiski, "Die Geheimschriften und die Dechiffrir-Kunst"（1863）**
  - **讀哪裡**：現在很難找到原文，但 Wikipedia "Kasiski examination" 有完整摘要
  - **學什麼**：Kasiski 攻擊的原始推導——如何從重複片段推斷 key length
  - **關聯**：本章 Vigenère 破譯的核心方法

→ [Ch 5 二戰密碼學：Enigma 與 Bombe](./05-wwii-cryptography.md)
