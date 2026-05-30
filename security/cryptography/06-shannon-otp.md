# Ch 6 — Shannon 與 One-Time Pad

> **目標**：能證明 OTP 滿足完美保密（perfect secrecy），能解釋為什麼 OTP 在工程上不實用，理解 Shannon 1949 論文的核心貢獻。

## 為什麼需要這個？

Ch 4-5 看到古典密碼被各種手段破解——頻率分析、Kasiski、Bombe。一個自然的問題：有沒有一種加密方法是「數學上不可能被破解」的？

答案是有，而且它驚人地簡單：One-Time Pad（OTP，一次性密碼本）。

但 OTP 的重要性不在於它本身（它在工程上幾乎不可用），而在於 Claude Shannon 在 1949 年用它建立了密碼學的第一個嚴格數學框架。Shannon 給了「安全」一個精確的數學定義，讓密碼學從藝術變成科學。從這章開始，密碼學不再是「看起來安全」，而是「可以證明安全」。

## 先建立直覺

想像你收到一個密文字母 `X`。你知道加密方法是「把明文和 key 做 XOR」。

```
如果 key 是完全隨機且只用一次：

密文 X 可能來自：
  明文 A + key (A⊕X)  → 機率 1/26
  明文 B + key (B⊕X)  → 機率 1/26
  明文 C + key (C⊕X)  → 機率 1/26
  ...
  明文 Z + key (Z⊕X)  → 機率 1/26

攻擊者看到密文 X，每個明文字母的機率都一樣！
密文沒有透露任何關於明文的資訊。

對比 Caesar：
密文 X 只能來自 26 種 key 中的一種。
如果攻擊者知道語言是英文，某些明文的機率遠高於其他。
```

OTP 的魔法：key 的隨機性完全淹沒了明文的統計結構。

## One-Time Pad 的定義

**加密**：`C = P ⊕ K`（按位元 XOR）
**解密**：`P = C ⊕ K`

三個嚴格要求：
1. **Key 長度 ≥ 明文長度**（每個明文位元都有對應的 key 位元）
2. **Key 完全隨機**（uniform random，不是 pseudorandom）
3. **Key 只用一次**（one-time：每條訊息用不同的 key）

三個條件缺任何一個，OTP 的安全保證都不成立。

### 範例一：Python OTP 加解密

```python
import os

def otp_encrypt(plaintext: bytes, key: bytes) -> bytes:
    assert len(key) >= len(plaintext), "Key 必須至少和明文一樣長"
    return bytes(p ^ k for p, k in zip(plaintext, key))

def otp_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    return otp_encrypt(ciphertext, key)  # XOR 是自反的

# 用 os.urandom() 產生真隨機 key
message = b"ATTACK AT DAWN"
key = os.urandom(len(message))

ciphertext = otp_encrypt(message, key)
print(f"明文:   {message}")
print(f"Key:    {key.hex()}")
print(f"密文:   {ciphertext.hex()}")

decrypted = otp_decrypt(ciphertext, key)
print(f"解密:   {decrypted}")
assert decrypted == message

# 重點：同一個密文，用不同的 key 可以解出任意明文
fake_target = b"RETREAT ASAP!!"
fake_key = bytes(c ^ f for c, f in zip(ciphertext, fake_target))
print(f"\n偽造 key: {fake_key.hex()}")
print(f"用偽造 key 解密: {otp_decrypt(ciphertext, fake_key)}")
# 輸出：b'RETREAT ASAP!!'
# 攻擊者無法分辨哪個 key 才是「真的」
```

最後那段程式展示了 OTP 安全性的直覺：同一段密文可以被「解密」成任意等長的明文，只要你選對 key。攻擊者拿到密文後，所有等長的明文都同樣可能。

## 底層機制：它是怎麼運作的？

### 完美保密的嚴格定義（Shannon, 1949）

一個加密方案 (Gen, Enc, Dec) 滿足**完美保密（perfect secrecy）**，當且僅當：

```
對所有明文 m₁, m₂ 和所有密文 c：
  Pr[Enc(K, m₁) = c] = Pr[Enc(K, m₂) = c]

其中 K 是隨機選取的 key。
```

白話翻譯：不管真正的明文是什麼，產生同一個密文的機率都一樣。密文不偏袒任何明文。

等價表述：`Pr[M = m | C = c] = Pr[M = m]`——看到密文之後，你對明文的信念（後驗機率）和看到密文之前（先驗機率）完全一樣。密文給了你零資訊。

### OTP 滿足完美保密：證明

**定理**：OTP 滿足完美保密。

**證明**（直覺版）：

```
加密：C = M ⊕ K，其中 K ~ Uniform({0,1}ⁿ)

對任意明文 m 和密文 c：
  Pr[Enc(K, m) = c]
= Pr[m ⊕ K = c]
= Pr[K = m ⊕ c]
= 1/2ⁿ         （因為 K 是 uniform random）

這個機率不依賴 m！
所以對任意 m₁, m₂：
  Pr[Enc(K, m₁) = c] = 1/2ⁿ = Pr[Enc(K, m₂) = c]   ∎
```

**證明**（Bayes 版）：

```
Pr[M = m | C = c]
= Pr[C = c | M = m] × Pr[M = m] / Pr[C = c]        （Bayes）
= (1/2ⁿ) × Pr[M = m] / (1/2ⁿ)                       （代入上面的結果）
= Pr[M = m]                                           ∎

後驗 = 先驗 → 密文給了零資訊
```

### Shannon 的下界定理

Shannon 同時證明了：要達到完美保密，**key space 必須至少和 message space 一樣大**。也就是 |K| ≥ |M|。

這意味著完美保密的代價是巨大的：你要傳 1 GB 的訊息，就需要 1 GB 的隨機 key。而且這個 key 必須安全地傳給對方——但如果你有安全通道傳 key，為什麼不直接用那個通道傳訊息？

這就是 OTP 的工程困境。

## 進一步用法：Key Reuse Attack

### 範例二：重複使用 key 的災難

```python
import os

key = os.urandom(100)

# 兩條訊息用同一個 key 加密
m1 = b"ATTACK THE NORTH GATE AT MIDNIGHT"
m2 = b"SEND MORE TROOPS TO THE EAST WALL"

c1 = bytes(p ^ k for p, k in zip(m1, key))
c2 = bytes(p ^ k for p, k in zip(m2, key))

# 攻擊者不知道 key，但可以 XOR 兩個密文
c1_xor_c2 = bytes(a ^ b for a, b in zip(c1, c2))

# c1 ⊕ c2 = (m1 ⊕ k) ⊕ (m2 ⊕ k) = m1 ⊕ m2
# key 被消掉了！
m1_xor_m2 = bytes(a ^ b for a, b in zip(m1, m2))

print(f"c1 ⊕ c2 = {c1_xor_c2.hex()}")
print(f"m1 ⊕ m2 = {m1_xor_m2.hex()}")
assert c1_xor_c2[:len(m1)] == m1_xor_m2  # 完全相同

# m1 ⊕ m2 洩漏了大量資訊：
# - 兩個明文相同位置的字母相同 → XOR = 0
# - 英文有強烈的統計結構 → crib dragging 可以逐步還原兩條明文
print(f"\nm1 ⊕ m2 的前 10 bytes:")
for i in range(min(10, len(m1_xor_m2))):
    print(f"  位置 {i}: {m1_xor_m2[i]:3d}  ({'相同!' if m1_xor_m2[i] == 0 else ''})")
```

**Crib dragging 攻擊**：攻擊者猜一個常見詞（如 "THE "），在 m1⊕m2 的每個位置嘗試 XOR，如果得到有意義的英文片段，就找到了另一條明文的對應部分。逐步拼湊，最終還原兩條明文。

歷史案例：VENONA 計畫（1943-1980）——美國 NSA 破解蘇聯外交電報，正是因為蘇聯在 1942 年因為 OTP 本子印刷不夠，重複使用了部分 key。

## 對比與取捨

| 特性 | OTP | 現代 stream cipher | 現代 block cipher |
|---|---|---|---|
| 安全性 | 完美保密（可證明）| 計算安全（假設 PRG 安全）| 計算安全（假設 PRP 安全）|
| key 長度 | = 明文長度 | 固定（如 256 bit）| 固定（如 128/256 bit）|
| key 重用 | 絕對不行 | 不行（需要 nonce）| 需要 mode of operation |
| 實用性 | 極低 | 高 | 高 |
| 數學保證 | 無條件安全 | 條件安全（依賴假設）| 條件安全（依賴假設）|

## Shannon 1949 的其他貢獻

Shannon 在同一篇論文（"Communication Theory of Secrecy Systems"）中還提出了兩個影響至今的概念：

### Confusion（混淆）

密文的每個位元應該依賴 key 的多個位元。目的是讓密文和 key 之間的關係盡可能複雜，攻擊者無法從密文推斷 key。

在現代 block cipher 中，confusion 由 S-box 實現（Ch 7-8）。

### Diffusion（擴散）

明文的一個位元改變，應該影響密文的多個位元。目的是消除明文的統計結構——如果明文中 `E` 的頻率高，加密後不應該在密文中留下任何統計痕跡。

在現代 block cipher 中，diffusion 由 ShiftRows + MixColumns（AES）或 Feistel 結構中的 expansion permutation（DES）實現。

```
Ch 4 古典密碼的問題：
  Caesar    → 有 confusion（shift），沒有 diffusion
  Mono sub  → 有 confusion（替換），沒有 diffusion
  Vigenère  → 有一點 confusion，仍然沒有 diffusion
  
  → 明文的統計結構直接映射到密文 → 頻率分析能破

Shannon 的處方：
  好的密碼需要同時具備 confusion 和 diffusion
  → 這成為所有現代 block cipher 設計的指導原則
```

## 踩雷集錦

1. **「OTP 只是 XOR」**：XOR 是 OTP 使用的操作，但 OTP 的安全保證來自三個條件：key ≥ 明文長度、key 完全隨機、key 只用一次。少了任何一個條件，XOR 加密就是不安全的 stream cipher。

2. **「OTP 是最好的加密」**：在理論上是（完美保密），但在工程上幾乎不可用。key distribution problem 讓它在大多數場景下毫無意義——你要安全地傳遞和明文等長的 key，這和直接安全傳遞明文一樣困難。

3. **「用 PRNG 產生的 key 也算 OTP」**：不算。pseudorandom number generator 的輸出不是真隨機，它有一個短的 seed。用 PRNG 生成的 key stream 做 XOR 加密叫做 stream cipher（Ch 11），不叫 OTP，安全性也從「完美保密」降到「計算安全」。

4. **「OTP 只用在冷戰時期」**：OTP 至今仍在某些場景使用——外交通訊的最高機密等級、量子密鑰分發（QKD）的底層就是 OTP（用量子通道解決 key distribution）。

5. **「Shannon 只是證明了 OTP」**：Shannon 1949 的貢獻遠不止 OTP。他建立了整個 secrecy systems 的數學框架，定義了 perfect secrecy、unicity distance、confusion/diffusion——這些概念是現代密碼學的基石。

## 進階：再往深一層

### Unicity Distance

Shannon 定義了 **unicity distance**（唯一解距離）：攻擊者需要多長的密文才能唯一確定明文？

```
對英文（每字母約 1.3 bits 的資訊量）和 key 長度 k bits：
  unicity distance ≈ k / D

其中 D = log₂(26) - H（每字母的冗餘度）≈ 3.4 bits

Caesar (k = log₂26 ≈ 4.7 bits):
  unicity distance ≈ 4.7 / 3.4 ≈ 1.4 字母（不到 2 個字母就能唯一確定 key）

Mono sub (k = log₂(26!) ≈ 88.4 bits):
  unicity distance ≈ 88.4 / 3.4 ≈ 26 字母（大約一行）

OTP (k = 明文長度 × 8 bits):
  unicity distance = ∞（永遠無法唯一確定）
```

Unicity distance 解釋了為什麼 monoalphabetic cipher 只要一段 26 字母的密文就能破——和 key space 的大小無關，和語言的冗餘度有關。

### 從 Perfect Secrecy 到 Computational Security

Shannon 的完美保密定義要求 |K| ≥ |M|，這在實用上太嚴格。現代密碼學（Goldwasser 和 Micali，1982）放鬆了這個要求：

- 不要求對所有攻擊者安全，只要求對**計算受限**的攻擊者安全（polynomial-time adversary）
- 不要求零資訊洩漏，只要求洩漏的資訊**可忽略**（negligible）

這個「計算安全」（computational security）的框架讓我們可以用 256-bit 的 key 加密任意長度的訊息——只要沒有 polynomial-time 的演算法能破它。Ch 7 的 IND-CPA 定義就建立在這個框架上。

## 動手練習

1. **實作 OTP**：加密 "HELLO" 並解密，驗證正確性。然後用同一個密文和不同的 key 「解密」出 "WORLD"——體會完美保密的含義

2. **Key reuse 攻擊**：用同一個 key 加密兩段英文，XOR 兩個密文，嘗試 crib dragging（用 " THE " 作為 crib 在每個位置滑動，XOR 後看有沒有有意義的片段）

3. **計算 unicity distance**：假設一個加密方案的 key 長度是 56 bits（DES），英文冗餘度 D ≈ 3.4 bits/letter，算出 unicity distance 是多少字母？這對 DES 的安全性意味著什麼？

4. **閱讀 Shannon 1949**：找到 "Communication Theory of Secrecy Systems"，讀 Section 2（perfect secrecy 的定義）和 Section 11（confusion & diffusion）。用自己的話寫出 confusion 和 diffusion 的定義

## 本章重點整理

- OTP 滿足完美保密：密文不透露明文的任何資訊，證明的核心是 key 的完全隨機性讓每個密文對應每個明文的機率相等
- OTP 在工程上不實用：key 必須和明文等長、完全隨機、只用一次——key distribution problem 讓大多數場景無法使用
- Shannon 1949 的貢獻超越 OTP：他建立了密碼學的數學框架，定義了完美保密、unicity distance、confusion & diffusion，為所有現代密碼設計奠定基礎

## 自我檢核

- [ ] 能用一段話解釋為什麼 OTP 是完美保密的（不需要寫數學，但邏輯要對）
- [ ] 能說出 OTP 的三個嚴格要求，以及違反任何一個的後果
- [ ] 能解釋 key reuse attack 的原理（XOR 兩個密文 = XOR 兩個明文）
- [ ] 能區分 perfect secrecy 和 computational security
- [ ] 能用自己的話定義 confusion 和 diffusion

## 延伸閱讀

- **Claude Shannon, "Communication Theory of Secrecy Systems"（1949）**
  - **讀哪裡**：Section 2（perfect secrecy 定義和證明）、Section 11（confusion & diffusion）
  - **學什麼**：現代密碼學的數學起點；Shannon 如何把資訊理論應用到密碼學
  - **關聯**：本章的數學基礎直接來自這篇論文

- **Steven Bellovin, "Frank Miller: Inventor of the One-Time Pad"（2011）**
  - **讀哪裡**：全文（短篇論文）
  - **學什麼**：OTP 的真正發明者不是 Vernam（1917）而是 Frank Miller（1882）——歷史比你以為的更早
  - **關聯**：補充 OTP 的歷史脈絡

- **NSA VENONA project declassified documents**
  - **讀哪裡**：NSA 公開的 VENONA 檔案摘要，找 "duplicate key" 的部分
  - **學什麼**：蘇聯因為重複使用 OTP key 導致外交電報被美國破解的真實案例
  - **關聯**：本章 key reuse attack 的歷史實例

→ [Ch 7 Block Cipher 基礎：Feistel 與 SPN](./07-block-cipher-basics.md)
