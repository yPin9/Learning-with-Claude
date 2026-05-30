# Ch 5 — 二戰密碼學：Enigma 與 Bombe

> **目標**：能解釋 Enigma 的 rotor 機制和弱點，理解 Turing 的 Bombe 如何利用 known-plaintext attack 打破 Enigma。

## 為什麼需要這個？

Enigma 是密碼學從「手工藝」走向「機械化」的轉折點，也是密碼分析從「語言學」走向「數學 + 工程」的分水嶺。

破解 Enigma 不是靠頻率分析，也不是靠暴力搜尋。Turing 和 Bletchley Park 的團隊利用的是 Enigma 設計中的數學弱點和德軍操作中的人為疏忽。這個故事教的不只是歷史——它是 known-plaintext attack（已知明文攻擊）的經典案例，這類攻擊在現代密碼分析中仍然是核心概念。

歷史學家估計，破解 Enigma 讓二戰縮短了至少兩年，拯救了數百萬條人命。密碼學不是抽象的數學遊戲。

## 先建立直覺

Enigma 的核心思想：把 Ch 4 的 monoalphabetic substitution 升級成「每按一個字母，替換表就變一次」。

```
機械打字機的想像：

操作員按 A → 電流通過三個轉子 + 反射器 → 燈泡 G 亮起
         ↓
       第三轉子轉一格（像里程表）
         ↓
操作員按 A → 電流通過「不同位置」的三個轉子 → 燈泡 T 亮起

同一個字母 A，第一次加密成 G，第二次加密成 T。
→ polyalphabetic，而且 26 × 26 × 26 = 17,576 個不同的替換表才會循環一次。
```

## Enigma 的結構

### 硬體組成

```
鍵盤 → Plugboard → Rotor 3 → Rotor 2 → Rotor 1 → Reflector
                                                        │
燈板 ← Plugboard ← Rotor 3 ← Rotor 2 ← Rotor 1 ←─────┘

電流路徑（加密 A 的過程）：
A → [Plugboard: A↔S] → S
  → [Rotor 3 正向] → F
  → [Rotor 2 正向] → K
  → [Rotor 1 正向] → W
  → [Reflector: W↔M] → M
  → [Rotor 1 反向] → P
  → [Rotor 2 反向] → D
  → [Rotor 3 反向] → G
  → [Plugboard: G↔G] → G（燈 G 亮）
```

### 各部件的角色

**Rotor（轉子）**：每個轉子是 26 條交叉接線的替換表。Wehrmacht 的 Enigma 從 5 個可選轉子中選 3 個，排列順序影響加密結果。每按一個字母，最右邊的轉子轉一格；滿 26 格後帶動中間轉子轉一格（像里程表的進位）。

**Reflector（反射器）**：把 26 個字母兩兩配對（13 對）。電流從左邊進去，從另一個字母出來，然後反向走回三個轉子。反射器讓 Enigma 有一個方便的特性：加密和解密用同一台機器、同一個設定——因為電路是對稱的。

**Plugboard（接線板）**：操作員用短線把某些字母兩兩交換（通常 10 對）。這是在 rotor 之前和之後各做一次額外的替換。

### key space

```
選 3 個 rotor（從 5 個中）:  5 × 4 × 3 = 60
每個 rotor 的起始位置:       26³ = 17,576
Plugboard 10 對:            ≈ 1.5 × 10¹⁴
Ring setting:               26³ = 17,576

總 key space ≈ 1.6 × 10²³
```

這個數字在 1940 年代是天文數字。每天的 key 不同（Wehrmacht 每天更換設定），暴力搜尋不可能。

## 底層機制：它是怎麼運作的？

### Rotor Wiring 的數學

每個 rotor 是一個 permutation（置換）σ：{0,1,...,25} → {0,1,...,25}。轉子轉動 r 格後，置換變成：

```
σ_r(x) = σ(x + r) - r   (mod 26)
```

三個轉子疊加 + 反射器 R + 反向走回：

```
E(x) = P · σ₃⁻¹ · σ₂⁻¹ · σ₁⁻¹ · R · σ₁ · σ₂ · σ₃ · P(x)

其中 P = plugboard 置換
每按一個字母，σ₃ 的 r 值 +1（帶進位到 σ₂、σ₁）
```

### 模擬一個簡化的 Enigma

```python
class SimpleEnigma:
    """3-rotor Enigma 簡化模型（無 plugboard、無 ring setting）"""

    def __init__(self, rotors: list[str], reflector: str, positions: list[int]):
        # rotors: 3 個 26 字元的置換字串
        # reflector: 26 字元的置換字串（必須是 involution：R[R[x]] = x）
        # positions: 3 個初始位置 [0-25]
        self.rotors = [list(r) for r in rotors]
        self.reflector = list(reflector)
        self.pos = list(positions)

    def _step(self):
        """最右轉子每次轉一格；簡化版省略中間轉子進位"""
        self.pos[2] = (self.pos[2] + 1) % 26
        if self.pos[2] == 0:
            self.pos[1] = (self.pos[1] + 1) % 26
            if self.pos[1] == 0:
                self.pos[0] = (self.pos[0] + 1) % 26

    def _pass_rotor(self, x: int, rotor: list, offset: int, reverse: bool = False) -> int:
        if not reverse:
            return (ord(rotor[(x + offset) % 26]) - ord('A') - offset) % 26
        else:
            target = chr((x + offset) % 26 + ord('A'))
            return (rotor.index(target) - offset) % 26

    def encrypt_char(self, ch: str) -> str:
        if not ch.isalpha():
            return ch
        self._step()
        x = ord(ch.upper()) - ord('A')

        # 正向：rotor 3 → 2 → 1
        for i in [2, 1, 0]:
            x = self._pass_rotor(x, self.rotors[i], self.pos[i])

        # 反射器
        x = ord(self.reflector[x]) - ord('A')

        # 反向：rotor 1 → 2 → 3
        for i in [0, 1, 2]:
            x = self._pass_rotor(x, self.rotors[i], self.pos[i], reverse=True)

        return chr(x + ord('A'))

    def process(self, text: str) -> str:
        return ''.join(self.encrypt_char(ch) for ch in text if ch.isalpha())


# 使用歷史 Enigma I 的 rotor I, II, III wiring
ROTOR_I   = "EKMFLGDQVZNTOWYHXUSPAIBRCJ"
ROTOR_II  = "AJDKSIRUXBLHWTMCQGZNPYFVOE"
ROTOR_III = "BDFHJLCPRTXVZNYEIWGAKMUSQO"
REFLECTOR_B = "YRUHQSLDPXNGOKMIEBFZCWVJAT"

enigma = SimpleEnigma(
    rotors=[ROTOR_I, ROTOR_II, ROTOR_III],
    reflector=REFLECTOR_B,
    positions=[0, 0, 0]
)
ct = enigma.process("HELLOWORLD")
print(f"密文: {ct}")

# 解密：重置到同一個初始位置
enigma2 = SimpleEnigma(
    rotors=[ROTOR_I, ROTOR_II, ROTOR_III],
    reflector=REFLECTOR_B,
    positions=[0, 0, 0]
)
pt = enigma2.process(ct)
print(f"解密: {pt}")  # HELLOWORLD
```

注意：加密和解密用同一個 `process()`——這是反射器帶來的對稱性。

## Enigma 的弱點

### 1. 反射器的致命缺陷

反射器保證了「加密字母永遠不等於自己」（a → a 不可能發生）。因為反射器是 involution：如果 A→G，那 G→A。電流進去 A 從 G 出來，不可能從 A 出來。

這看起來無害，但它給了密碼分析師一個強大的篩選工具：如果你猜測某個位置的明文是 E，而密文也是 E——這個猜測一定是錯的。

### 2. 操作習慣的災難

**Wetterbericht（氣象報告）**：Wehrmacht 海軍每天早上固定發送氣象報告，開頭幾乎總是 "WETTERBERICHT"（天氣預報）。這給了 Bletchley Park 每天一段 known-plaintext。

**Crib（已知明文片段）**：除了氣象報告，還有固定格式的開頭語（如 "AN DIE GRUPPE"）、重複的指令格式等。這些可預測的明文片段稱為 crib。

### 3. Key 指示器的重複

早期 Enigma 協議要求操作員選一個隨機 3 字母的 message key，然後把它加密兩次傳送（如 ABCABC → XYZPQR）。重複兩次是為了防傳輸錯誤，但這讓 Rejewski（波蘭數學家）在 1932 年就發現了數學結構——第 1 和第 4 字母用的是同一個轉子位置關係，可以建立置換的循環結構。

## Turing 的 Bombe

### Known-Plaintext Attack 的邏輯

Turing 的 Bombe 不是嘗試所有 key。它利用 crib 做邏輯推導：

```
密文: R W I V T Y R E S X
crib: W E T T E R B E R I   (猜測這段明文是 WETTERBERI...)

位置 0: 明文 W → 密文 R  （Enigma 在位置 0 把 W 加密成 R）
位置 1: 明文 E → 密文 W  （Enigma 在位置 1 把 E 加密成 W）
位置 2: 明文 T → 密文 I
位置 3: 明文 T → 密文 V
...
```

因為「字母不會加密成自己」，如果任何位置的 crib 字母和密文字母相同，那段 crib 的對齊位置一定是錯的。這能快速排除大量錯誤猜測。

接著 Turing 建立了字母之間的連結圖（稱為 menu）：

```
位置 0: W → R    位置 1: E → W    位置 5: R → Y
                       ↗
位置 7: E → X    位置 4: E → T
```

字母 E 出現在多個位置，形成環路（loop）。Bombe 利用這些環路做矛盾推導：假設某個 rotor 設定，追蹤環路，如果出現矛盾（同一個字母在同一位置被映射到兩個不同字母），就排除這個設定。

### Bombe 的硬體

Bombe 是 electromechanical 裝置——銅線、繼電器、旋轉的 drum（模擬 Enigma 的轉子）。它不是 electronic computer（沒有真空管做計算）。

```
Bombe 的工作：
1. 設定 crib 和密文的對應關係
2. 高速旋轉 drum 嘗試不同 rotor 位置
3. 電路自動檢測矛盾
4. 找到沒有矛盾的位置 → 這就是可能的 key（稱為 "stop"）
5. 操作員手動驗證每個 stop
```

一台 Bombe 大約能在 20 分鐘內測完一組 rotor 順序的所有位置。Bletchley Park 在高峰期有超過 200 台 Bombe 同時運行。

## 對比與取捨

| 特性 | Ch 4 古典密碼 | Enigma |
|---|---|---|
| 替換類型 | 固定表（mono）或短循環（poly）| 每字母變一次的 poly |
| 密碼分析方法 | 頻率分析 | known-plaintext + 邏輯推導 |
| key space | 小（Caesar）到中（Vigenère）| ~10²³（巨大）|
| 被破的原因 | 語言統計特性 | 設計缺陷 + 操作疏忽 |
| 破解工具 | 紙筆 | Bombe（機械裝置）|
| 對密碼學的教訓 | key space ≠ 安全 | 數學弱點 + 人為因素同樣致命 |

## 踩雷集錦

1. **「Enigma 被電腦破的」**：Bombe 是 electromechanical 裝置，不是 electronic computer。Colossus（1943）才是電子計算機，但它破的是 Lorenz cipher（另一個德軍密碼機），不是 Enigma。兩者常被混為一談。

2. **「Turing 一個人破了 Enigma」**：Turing 設計了 Bombe，但波蘭數學家 Rejewski、Różycki、Zygalski 在 1932 年就破了早期版本。英國團隊是在波蘭人的成果上繼續發展。Gordon Welchman 也對 Bombe 做了關鍵改進（diagonal board）。

3. **「Enigma 的密碼不安全」**：Enigma 的演算法在當時是強大的。被破不是因為演算法本身太弱，而是因為反射器的數學弱點加上操作規程的人為失誤。如果德軍不重複發送 key indicator、不使用固定格式開頭，破解難度會大幅提升。

4. **「知道了 Enigma 就知道密碼學」**：Enigma 是 pre-Shannon 的設計，沒有嚴格的安全定義。它教的是密碼分析的思維方式，但現代密碼學建立在完全不同的數學基礎上（Ch 6 開始）。

5. **混淆 Enigma 的不同版本**：Wehrmacht（陸軍）、Kriegsmarine（海軍）、Luftwaffe（空軍）用的 Enigma 設定不同。海軍版有 4 個 rotor（M4），更難破。本章描述的主要是 3-rotor Wehrmacht 版。

## 進階：再往深一層

### Rejewski 的數學突破（1932）

Rejewski 利用每天的加密 key indicator（同一個 3 字母加密兩次），建立了置換的 cycle structure。假設某天的 key indicator 讓第 1 和第 4 個字母的加密結果為：

```
位置 1: A→F  B→L  C→W  D→R  ...
位置 4: A→T  B→P  C→Q  D→E  ...
```

組合起來得到一個置換 S = P₄ · P₁⁻¹，Rejewski 分析 S 的 cycle 長度分布，發現它和 rotor 的接線有對應關係。這讓波蘭人在不知道 rotor 接線的情況下推斷出接線！這是密碼分析史上最精彩的數學推導之一。

### Banburismus：Bayesian 推理

Turing 在破解 Kriegsmarine 的 Enigma 時發展了 Banburismus——本質上是 Bayesian 假設檢定。他用 deciban（十分之一 ban）作為 evidence 的計量單位，計算某個假設成立的後驗機率。這個方法比 Bombe 更節省資源，因為它能先篩選出可能的 rotor 順序，再交給 Bombe 做暴力驗證。

### 現代意義

Enigma 的故事在現代密碼學中對應幾個重要原則：
- **Kerckhoffs' principle**：安全性不應依賴演算法保密，而應只依賴 key。Enigma 的演算法被盟軍取得，但 key（每天更換）才是安全的保障。
- **Known-plaintext resistance**：現代 cipher 必須在攻擊者擁有 plaintext-ciphertext pair 的情況下仍然安全。Enigma 在這方面失敗了。
- **Side-channel awareness**：操作習慣（Wetterbericht）是 side-channel 的原始形式——非密碼學本身的資訊洩漏。

## 動手練習

1. **模擬 Enigma**：用範例中的 `SimpleEnigma` 類別，加密 "ENIGMA IS BROKEN"，然後重設位置解密，驗證對稱性

2. **反射器弱點實驗**：加密 "AAAAAAAAAA"，觀察密文中是否出現 A。解釋為什麼不會（反射器的性質）

3. **Crib 排除**：給定密文 "XFGTR" 和 crib "HELLO"，檢查這組對齊是否可能（提示：位置 3 的密文 T 和 crib L 不同，可以；但如果密文是 "XFLTR"，位置 2 的 L = L，不可能）

4. **歷史研究**：查閱 Rejewski 在 1932 年的方法，寫出他如何從 key indicator 的重複中提取 cycle structure 的大致步驟

## 本章重點整理

- Enigma 用 3 個轉子 + 反射器 + 接線板實現每字母不同的 polyalphabetic substitution，key space 約 10²³
- 反射器讓加解密對稱（方便操作），但也造成「字母不加密成自己」的致命弱點；操作習慣（Wetterbericht、key indicator 重複）提供了 known-plaintext
- Turing 的 Bombe 是 electromechanical 裝置，利用 crib 的環路做矛盾推導，不是暴力搜尋所有 key

## 自我檢核

- [ ] 能畫出 Enigma 的電流路徑（鍵盤 → plugboard → 3 rotors → reflector → 3 rotors → plugboard → 燈板）
- [ ] 能解釋反射器為什麼造成「字母不加密成自己」
- [ ] 能說出 known-plaintext attack 的基本邏輯（crib + 矛盾排除）
- [ ] 知道 Bombe 是 electromechanical 而非 electronic computer
- [ ] 能區分 Enigma（Bombe 破）和 Lorenz（Colossus 破）

## 延伸閱讀

- **Andrew Hodges,《Alan Turing: The Enigma》**
  - **讀哪裡**：Ch 4-5（Bletchley Park 時期）
  - **學什麼**：Turing 設計 Bombe 的思考過程和 Banburismus 的邏輯
  - **關聯**：本章 Bombe 部分的歷史細節來源

- **Dermot Turing,《X, Y & Z: The Real Story of How Enigma Was Broken》**
  - **讀哪裡**：前半部波蘭密碼分析師的貢獻
  - **學什麼**：Rejewski 的數學方法，以及波蘭-英國情報合作的政治脈絡
  - **關聯**：本章進階部分 Rejewski 的 cycle structure 分析

- **Crypto Museum: [Enigma 技術細節](https://www.cryptomuseum.com/crypto/enigma/)**
  - **讀哪裡**：各型號 Enigma 的 rotor wiring 和技術規格
  - **學什麼**：不同版本 Enigma（Wehrmacht vs Kriegsmarine M4）的差異
  - **關聯**：想要實作完整 Enigma 模擬器時的權威資料

→ [Ch 6 Shannon 與 One-Time Pad](./06-shannon-otp.md)
