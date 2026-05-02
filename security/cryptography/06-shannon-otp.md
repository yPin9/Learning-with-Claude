# Ch 6 — Shannon 與一次性密碼本

> 目標：理解 Shannon 1949 的完美保密（perfect secrecy）證明，以及為什麼一次性密碼本（OTP）在數學上完美但工程上沒用。引出「我們為什麼需要計算安全（computational security）」— 整個現代密碼學的起點。

## Shannon 的兩篇姐妹篇

```
1948  "A Mathematical Theory of Communication"
        建立資訊論，定義 entropy

1949  "Communication Theory of Secrecy Systems"
        把資訊論套到密碼學
        定義 perfect secrecy
        證明 OTP 達成 perfect secrecy
        證明任何 perfect secrecy 系統 |key| ≥ |plaintext|
```

第二篇是**密碼學從工藝變科學的起點**。前一個世代是 Vigenère / Enigma 的「希望沒人破得了」；Shannon 之後是「**這是定義，這是證明**」。

## Perfect Secrecy 定義

```
Encryption scheme (Gen, Enc, Dec) achieves perfect secrecy iff
for every probability distribution over M（明文空間）,
every m ∈ M, every c ∈ C（密文空間）:

  Pr[M = m | C = c] = Pr[M = m]
```

讀：**看到密文 c 後，attacker 對明文是 m 的後驗機率 = 先驗機率**。即看密文**完全沒提供 information**。

等價條件：

```
Pr[Enc(K, m₀) = c] = Pr[Enc(K, m₁) = c]
```

對任意兩個明文 m₀, m₁ 與任何 c — **加密後 distribution 一樣**，attacker 看密文無法區分。

## OTP 達成 perfect secrecy

**OTP 規則**：

1. Key K 與 plaintext M 等長，**真隨機**選自 {0,1}^n
2. 加密：C = M XOR K
3. 解密：M = C XOR K
4. **K 只用一次**，用完丟掉

證明 perfect secrecy：

```
給定任意 c, m，要算 Pr[Enc(K, m) = c]
= Pr[m XOR K = c]
= Pr[K = m XOR c]
= 1 / 2^n   （K 均勻隨機）

對任何 m 都是 1/2^n，所以 Pr[Enc(K, m₀) = c] = Pr[Enc(K, m₁) = c]
完美保密成立 ∎
```

直覺：**任何密文 c，配對任何明文 m 都「同樣機率」是真實 plaintext**。attacker 看密文等於沒看。

## OTP 的工程問題

數學完美 ≠ 工程可用。OTP 三個致命問題：

### 1. Key 與訊息等長

要傳 1 GB 訊息 → 需要 1 GB 真隨機 key。**Alice 與 Bob 怎麼安全交換 1 GB key？**如果有安全 channel 能傳 1 GB key，幹嘛不直接傳訊息？

OTP 把問題從「保密 plaintext」推到「保密同等大小的 key」 — 沒減少難度，只是換了名字。

### 2. Key 不能重用

「one-time」是字面意義 — **key 用第二次直接死**。

```
c1 = m1 XOR K
c2 = m2 XOR K

c1 XOR c2 = m1 XOR m2     ← K 消掉了
```

attacker 拿 c1 XOR c2 = m1 XOR m2 後，可用語言模型還原（英文 plaintext 有大量結構）。

**真實案例**：蘇聯 KGB 一次性密碼本在 1942-1948 重複使用（戰後缺紙）。美國 Venona 計畫破譯出大量蘇聯通訊，揭露 Klaus Fuchs、Rosenberg 等間諜。

### 3. Key 必須真隨機

PRNG 不行。**必須真隨機**（量子 / 熱噪聲 / 物理源）。當年 KGB 用色情雜誌頁碼當 key（夠 random？不夠！），二戰後盟軍用打孔卡 + 物理 random 機。

## Shannon 的 lower bound

Shannon 還證明：**任何達到 perfect secrecy 的系統，都必須 |K| ≥ |M|**。

證明草稿（反證法）：

```
假設 |K| < |M|（key 比 plaintext 短）
給定 c，可能的 plaintext 候選 = {Dec(k, c) : k ∈ K}
這集合大小 ≤ |K| < |M|
故有些 m ∈ M 不在候選裡 → Pr[M = m | C = c] = 0 ≠ Pr[M = m]
矛盾，∎
```

直覺：**有限個 key 對應有限個解密候選，攻擊者能排除大部分 plaintext** — 不是 perfect secrecy。

**結論**：要 perfect secrecy 必付 OTP 的代價。**所以實用密碼學放棄 perfect secrecy，改用 computational security**。

## Computational Security：實用的妥協

放棄「**完全沒資訊洩漏**」的目標，改成「**有限算力下無法區分**」：

```
Computational secrecy（IND-CPA、IND-CCA 等定義）：
任何 polynomial-time attacker，advantage 是 negligible
```

具體：

```
AES-256 perfect secrecy？ → 不是（key 才 256-bit，plaintext 任意長）
AES-256 computationally secure？ → 是（沒人在合理時間破得了）
```

**現代密碼學接受「2^128 次嘗試太貴沒人做」當安全保證**。代價是「**理論上可破**」，但實務上比不上計算極限。

## OTP 仍在哪用？

少數場景至今用 OTP：

1. **核武授權碼**：軍用一次性密碼本（短訊息、極高 stakes）
2. **冷戰熱線**：美蘇直通專線早期用 OTP
3. **頂級間諜通訊**：CIA 給海外特工的「秘密一次性墊本」（key 印在硝化纖維紙上，看完燒掉）
4. **量子安全考量**：post-quantum 時代有人重新提倡 OTP（量子電腦也破不了 perfect secrecy）

但**網際網路完全不用** — bandwidth、key distribution、operational complexity 都不允許。

## XOR 的更深意義

OTP 用 XOR，但 XOR 本身在所有 stream cipher 中都用。為什麼？

```
XOR 的代數性質：
  • 自反：a XOR b XOR b = a
  • 交換 / 結合：a XOR b = b XOR a, (a XOR b) XOR c = a XOR (b XOR c)
  • 0 為單位元：a XOR 0 = a
  • 自身為逆元：a XOR a = 0
```

**XOR 是 GF(2) 上的加法**。stream cipher 把 keystream（PRG output）與 plaintext XOR — 概念上是「**用 PRG 模擬 OTP**」：

```
OTP：c = m XOR K（K 真隨機，等長）
Stream cipher：c = m XOR PRG(seed)（PRG output 看起來像隨機，等長）
```

差異：OTP 有 perfect secrecy；stream cipher 只有 computational security。但 **stream cipher 把 key 從「等長」縮到「128/256-bit seed」**，工程上能用。

ChaCha20、AES-CTR、RC4 全部走這個 idea — stream cipher 是 OTP 的計算近似。

## Two-Time Pad：致命的 key reuse

OTP key 重用兩次：

```python
# 假設兩個英文訊息用同 key 加密
m1 = b"Attack at dawn from the east side"
m2 = b"Retreat to base via the west road"
K  = os.urandom(len(m1))

c1 = bytes(a ^ b for a, b in zip(m1, K))
c2 = bytes(a ^ b for a, b in zip(m2, K))

# Attacker 計算 c1 XOR c2 = m1 XOR m2
xor = bytes(a ^ b for a, b in zip(c1, c2))
# 然後用「滑動猜詞」或語言模型還原
```

**滑動猜詞**：猜測 m1 包含某個常見詞（如 "the "），slide across positions：

```
m1 XOR m2 中某個 substring XOR "the " → 可能是 m2 的某 substring
若解出有意義英文 → 你猜對位置與 m1 那段是 "the "
```

**自動化**：用 n-gram 機率 + 語言模型，幾百 byte 的 two-time pad 通常能完整還原。

CTF 經典題：「給你 10 條密文，全用同一個 key OTP 加密，回復明文」 — 跑滑詞 + 字典即可。

## 一個常見誤解

「我用 SHA-256 雜湊 password 當 OTP key 是不是就完美保密？」

**不是 OTP 了**。OTP 要 key **真隨機 + 等長**。`SHA256(password)` 是固定 256-bit，且 password 通常 entropy 很低（用字典攻擊可猜）— 退化到 password-based encryption（Ch 17 詳述），perfect secrecy 沒有。

只有「真隨機 + 等長 + 一次性」三個條件**全到**才是 OTP。少任何一個 = 普通對稱加密，靠演算法強度（computational security），不靠 perfect secrecy。

## 這章把我們帶到哪

OTP 與 Shannon 證明告訴我們**密碼學的數學極限**。實務上我們**接受 computational security 這個妥協**，往後幾十年的研究都圍繞：

1. 怎麼設計**好用的對稱密碼**（AES、ChaCha20）
2. 怎麼**分發 key**（公鑰密碼、Diffie-Hellman）
3. 怎麼**驗證安全性**（IND-CPA、IND-CCA proof）
4. 怎麼**抵抗實作攻擊**（const-time、side-channel）

從 Ch 7 開始我們進入 Part 3 對稱密碼，先看 block cipher 的 Feistel 與 SPN 兩種架構。

## 自我檢核

- [ ] 我能寫出 perfect secrecy 的形式定義
- [ ] 我能證明 OTP 達成 perfect secrecy
- [ ] 我能說出 Shannon 的 |K| ≥ |M| lower bound 與其意義
- [ ] 我能解釋為什麼 OTP 工程上沒用
- [ ] 我能說明 stream cipher 與 OTP 的關係
- [ ] 我能用滑動猜詞攻擊 two-time pad

下一章開始現代對稱密碼旅程：先看 block cipher 的兩種設計範式。

→ [Ch 7 區塊密碼基礎](./07-block-cipher-basics.md)
