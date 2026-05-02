# Ch 4 — 古典密碼：Caesar、Vigenère、頻率分析

> 目標：用古典密碼當 baseline 體驗「為什麼簡單 substitute 會被秒破」。實作 Caesar、Vigenère、Hill，並用頻率分析破譯，建立後面章節的「攻擊優先」直覺。

## 為什麼還要學這些「古董」

兩個理由：

1. **教 attacker mindset 最便宜**：頻率分析 / Kasiski / index of coincidence 你能在筆記本上推，現代密碼複雜很多但 attack pattern 結構類似
2. **歷史上是真實工具**：羅馬帝國軍事通訊（Caesar）、文藝復興外交（Vigenère）、二戰前期（一些變種）— 不是純玩具

但**請別在 production 用古典密碼**。「我用 Caesar 加密 token 沒人猜得到」這種話會讓你被開除。

## Caesar Cipher：所有密碼的祖宗

**規則**：每個字母往後移 k 位（k 是 key）。

```
plaintext:  H E L L O
key:        3 (shift 3)
ciphertext: K H O O R
```

```python
def caesar_enc(text: str, k: int) -> str:
    out = []
    for c in text.upper():
        if c.isalpha():
            out.append(chr((ord(c) - ord('A') + k) % 26 + ord('A')))
        else:
            out.append(c)
    return ''.join(out)

def caesar_dec(text: str, k: int) -> str:
    return caesar_enc(text, -k)

print(caesar_enc("HELLO WORLD", 3))   # KHOOR ZRUOG
```

**安全性**：key space = 26（試 26 次就破了）。

```python
# 暴力破譯
ct = "KHOOR ZRUOG"
for k in range(26):
    print(f"k={k:2d}: {caesar_dec(ct, k)}")
```

跑出來看哪個是英文。**人眼掃 26 行很快**，腳本 + 英文字典自動化更快。

## ROT13：Caesar 的特例

`k = 13` 的 Caesar 叫 **ROT13**。特性：**enc = dec**（連續做兩次回到原文）。

ROT13 在早期 Usenet 用來「**遮蔽 spoiler**」 — 不是加密用，是「不想被誤讀」用。

```python
# Python 內建
import codecs
print(codecs.encode("Hello, World", "rot_13"))  # Uryyb, Jbeyq
```

## Substitution Cipher：把 Caesar 推廣

**任意排列 26 字母**：

```
plain:  A B C D E F G H I J K L M N O P Q R S T U V W X Y Z
cipher: Q W E R T Y U I O P A S D F G H J K L Z X C V B N M
```

key space = 26! ≈ 4 × 10²⁶。看似不可破，但**頻率分析**讓它兒戲。

## 頻率分析：古典密碼學殺手

英文字母頻率（已知公開）：

```
E  12.7%       N  6.7%        D  4.3%        ...
T  9.1%        I  6.3%        L  4.0%
A  8.2%        O  6.0%        C  2.8%
O  7.5%        S  6.3%        U  2.8%
I  7.0%        H  6.1%        M  2.4%
N  6.8%        R  5.9%        W  2.4%
S  6.3%        D  4.3%
```

英文 substitute cipher：算 ciphertext 中各字母頻率，最常出現的那個極可能對應 E。然後對照 ETAOIN SHRDLU 順序排其他。

```python
from collections import Counter

def freq_analyze(ct: str):
    only_alpha = ''.join(c for c in ct.upper() if c.isalpha())
    total = len(only_alpha)
    counter = Counter(only_alpha)
    return [(c, n/total) for c, n in counter.most_common()]

ct = """KCMA HVSTSUMA EOLU OF ULIVMQHSE  XLA H VYS WSGCG LO
EBSDD WLT IZ LEPS WL XLAS XLALSH XLAEPS HVS RLOGRMSU"""
for c, f in freq_analyze(ct)[:10]:
    print(f"{c}: {f:.3f}")
```

頻率最高的 → 試 E、T、A 等熱門字母。**100 字以上的 ciphertext 破譯成功率 > 95%**。

## Vigenère：多 alphabet substitute

15 世紀 Blaise de Vigenère 發明（其實是 Bellaso 早 100 年，但被誤稱）。

**規則**：用一個 keyword（如 "KEY"）反覆對應到 plaintext 上 shift：

```
plaintext:  ATTACK AT DAWN
keyword:    KEYKEY KE YKEY
ciphertext: KXRKGK KX BKAR
```

每個位置 shift 量 = `key[i % len(key)] - 'A'`。

```python
def vigenere_enc(text, key):
    out = []
    key = key.upper()
    j = 0
    for c in text.upper():
        if c.isalpha():
            shift = ord(key[j % len(key)]) - ord('A')
            out.append(chr((ord(c) - ord('A') + shift) % 26 + ord('A')))
            j += 1
        else:
            out.append(c)
    return ''.join(out)

print(vigenere_enc("ATTACKATDAWN", "KEY"))   # KXRKGKKXBKAR
```

**18-19 世紀號稱「**le chiffre indéchiffrable**」**（不可破密碼） — 因為單純頻率分析失敗（每個 plaintext 字母對應多個 ciphertext 字母，看起來頻率被抹平）。

## Kasiski 攻擊：1863 破 Vigenère

Friedrich Kasiski 觀察到：**ciphertext 中重複的子字串很可能來自同 plaintext 子串對齊到同 key 位置**。

```
plaintext:  THE WEATHER IS NICE THE QUICK FOX
keyword:    KEYKEYKEYKEYKEYKEYKEY KEYKE YKE
                                  ↑
                              "THE" 出現兩次
                              位置差是 keyword 長度倍數
```

步驟：

1. 找重複的 3-gram 或更長子字串
2. 計算它們之間的距離
3. 距離的 GCD 通常是 keyword 長度倍數
4. 知道 keyword 長度後，把 ciphertext 拆成 N 個 sub-stream，每個是純 Caesar
5. 對每個 sub-stream 做頻率分析

```python
from collections import defaultdict
from math import gcd
from functools import reduce

def kasiski(ct, n=3):
    """回傳重複 n-gram 之間距離的可能 keyword 長度"""
    only = ''.join(c for c in ct.upper() if c.isalpha())
    positions = defaultdict(list)
    for i in range(len(only) - n + 1):
        positions[only[i:i+n]].append(i)
    distances = []
    for ngram, locs in positions.items():
        if len(locs) > 1:
            for i in range(1, len(locs)):
                distances.append(locs[i] - locs[0])
    if not distances:
        return None
    return reduce(gcd, distances)
```

幾百個字母的 ciphertext 通常能準確算出 keyword 長度。

## Index of Coincidence：自動化決定 key 長度

更現代：**index of coincidence (IC)** 統計兩隨機字母相同的機率。

```
英文文本：IC ≈ 0.067
完全隨機：IC ≈ 0.038
Vigenère ciphertext：IC ≈ 0.038（看起來像隨機）
但分成 N 個 sub-stream（N = key 長度）後，
每個 sub-stream 的 IC ≈ 0.067（每個是 Caesar 變的英文）
```

```python
def index_of_coincidence(text):
    text = ''.join(c for c in text.upper() if c.isalpha())
    n = len(text)
    if n < 2: return 0
    counts = Counter(text)
    return sum(c * (c - 1) for c in counts.values()) / (n * (n - 1))

def find_key_length(ct, max_len=20):
    for L in range(1, max_len + 1):
        avg_ic = sum(index_of_coincidence(ct[i::L]) for i in range(L)) / L
        print(f"L={L}: IC={avg_ic:.4f}")
```

正確 L 對應的 IC 突然跳到 ~0.067。**對 Vigenère 完全自動化破譯**。

## Hill Cipher：矩陣密碼

20 世紀 Lester Hill 提：**把 plaintext 切成 n-vector，用 n×n 矩陣（mod 26）做線性變換**。

```
ciphertext = K × plaintext (mod 26)
```

K 是 key（一個 n×n 可逆矩陣）。

**線性 = 全完蛋**：known-plaintext attack 直接解線性方程。給 attacker 一段「我知道 plaintext 與對應 ciphertext」就能算出 K。

意義：**現代密碼必須非線性**（AES SubBytes 引入 GF(2⁸) inverse 就是為了打破線性）。

## OTP：祖父輩中唯一安全的

**One-Time Pad**：key 與 plaintext 等長，**完全隨機**，**只用一次**：

```
ciphertext = plaintext XOR key
```

Shannon 1949 證明這是 **完美保密**（perfect secrecy）— ciphertext 對 plaintext 提供 0 bit 資訊。Ch 6 會展開證明。

但工程上沒用：要安全交換**和訊息一樣長的 key** — 你能交換這個 key，幹嘛不直接交換訊息？

OTP 仍在某些 nation-state 場景用（核武熱線、間諜通訊），但網際網路時代沒戲。

## 二戰前後過渡：rotor 機

從手工 cipher 走向機械化的橋：

- **Hagelin C-36**（瑞典，二戰中立）
- **Enigma**（德軍，下章專講）
- **Purple**（日軍外交）
- **SIGABA**（美國，二戰唯一完全沒被破的機）

機械化讓 key space 從 10²⁰ 級跳到 10²⁰⁰ 級。**但設計缺陷**（Enigma：reflector 不能 self-encrypt + operator habit）讓盟軍仍能破譯。Ch 5 詳述。

## 動手練習：頻率分析破 Caesar

```python
ENGLISH_FREQ = {
    'E': 0.127, 'T': 0.091, 'A': 0.082, 'O': 0.075, 'I': 0.070,
    'N': 0.068, 'S': 0.063, 'H': 0.061, 'R': 0.060, 'D': 0.043,
    # ...
}

def chi_squared(text):
    """text 與英文 distribution 的卡方值，越小越像英文"""
    only = ''.join(c for c in text.upper() if c.isalpha())
    n = len(only)
    if n == 0: return float('inf')
    counts = Counter(only)
    chi = 0
    for c, expected_freq in ENGLISH_FREQ.items():
        observed = counts.get(c, 0)
        expected = expected_freq * n
        if expected > 0:
            chi += (observed - expected) ** 2 / expected
    return chi

def break_caesar(ct):
    best_k, best_chi = 0, float('inf')
    for k in range(26):
        decoded = caesar_dec(ct, k)
        chi = chi_squared(decoded)
        if chi < best_chi:
            best_k, best_chi = k, chi
    return best_k, caesar_dec(ct, best_k)

ct = "KHOOR ZRUOG WKLV LV D KLGGHQ PHVVDJH"
k, pt = break_caesar(ct)
print(f"key={k}: {pt}")
```

完全自動，不用人眼。**這個技術擴展到 Vigenère、Enigma 都成立**：找一個 statistic（chi-squared / IC），讓電腦試所有 key candidate，回傳 best match。

## 一個常見誤解

「字母替換已經是過去式，現代密碼學不用這些」

**你錯了**。AES 內部 SubBytes 步驟就是 substitution（用 256-byte S-box）；hash function 內部也常用 byte substitution。**substitution 是現代密碼學最基本的非線性元件**。古典 substitution 失敗在 key space 太小且結構太規律，不在 substitution 本身。

## 自我檢核

- [ ] 我能寫 Caesar 與 Vigenère encrypt/decrypt
- [ ] 我能用頻率分析破 substitution cipher
- [ ] 我能用 IC 估計 Vigenère key 長度
- [ ] 我能解釋為什麼 Hill cipher 線性導致全破
- [ ] 我知道 OTP 是「完美保密」但工程上沒用
- [ ] 我看得出現代密碼仍用 substitution（如 AES SubBytes）

下一章看二戰密碼學的高潮：Enigma、Bombe、Bletchley Park 與 Turing 的故事。

→ [Ch 5 二戰密碼學](./05-wwii-cryptography.md)
