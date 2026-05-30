# Ch 13 — Hash 函式基礎

> 目標：能定義碰撞抗性（Collision Resistance）、原像抗性（Preimage Resistance）、第二原像抗性（Second Preimage Resistance）三個安全屬性，理解 Merkle-Damgård 構造的運作方式，知道 birthday bound 為什麼讓碰撞比你想像中容易。

---

## 為什麼需要 Hash 函式

前 12 章我們處理的都是「保密」——怎麼把明文變成只有持有金鑰的人才能還原的密文。但密碼學有另一半叫「完整性（Integrity）」：

- 我從網路下載了一個 ISO，怎麼確認它沒被篡改？
- 我收到一個 message，怎麼確認它是你發的、沒被中間人改過？
- 我要把 password 存進資料庫，但不想存明文——存什麼？

以上三個問題的答案都指向同一個原語：**密碼學雜湊函式（Cryptographic Hash Function）**。

它吃任意長度的輸入，吐固定長度的輸出（稱為 digest 或 hash value），而且滿足三個安全屬性（下面會逐一拆解）。

---

## 先建立直覺

把 hash 函式想成一台「指紋機」：

```
任意長度的文件  ──→  [ Hash Function ]  ──→  固定長度的指紋（digest）
                                              256-bit（SHA-256）
                                              160-bit（SHA-1）
```

三個關鍵性質（先用直覺版，嚴格定義下面給）：

1. **確定性**：同一份輸入永遠得到同一個 digest
2. **不可逆**：拿到 digest 無法反推出原始輸入
3. **雪崩效應（Avalanche Effect）**：改一個 bit 的輸入，digest 會面目全非

---

## 核心概念：三個安全屬性

### 屬性一：原像抗性（Preimage Resistance）

給你一個 hash 值 `h`，你找不到任何 `m` 使得 `H(m) = h`。

白話：hash 是單向函式——往前算很快，往回推做不到。

破壞原像抗性意味著什麼？攻擊者拿到你資料庫裡的 password hash，能直接算出一個合法的 password。

### 屬性二：第二原像抗性（Second Preimage Resistance）

給你一個特定的訊息 `m₁`，你找不到另一個 `m₂ ≠ m₁` 使得 `H(m₁) = H(m₂)`。

白話：給你一份特定的文件，你造不出另一份不同的文件卻有相同 hash。

破壞第二原像抗性意味著什麼？攻擊者能把一份合法合約替換成惡意版本，而 hash 不變。

### 屬性三：碰撞抗性（Collision Resistance）

你找不到任何一對 `(m₁, m₂)` 使得 `m₁ ≠ m₂` 且 `H(m₁) = H(m₂)`。

白話：你無法**自由選擇**兩份不同文件讓它們 hash 相同。

注意碰撞抗性和第二原像抗性的差別：碰撞抗性允許攻擊者**兩邊都自己挑**，所以比第二原像抗性更強（更難達到）。

### 三者的關係

```
碰撞抗性  ──蘊含──→  第二原像抗性
                         │
                    （不蘊含）
                         │
                    原像抗性
```

碰撞抗性是最強的——如果一個 hash 函式有碰撞抗性，它自動有第二原像抗性。但碰撞抗性不蘊含原像抗性（理論上可以構造反例）。實務上，SHA-256 三個屬性都滿足。

---

## 範例一：用 Python hashlib 算 SHA-256

```python
import hashlib

# 基本用法
msg = b"Hello, cryptography!"
digest = hashlib.sha256(msg).hexdigest()
print(f"SHA-256: {digest}")
# SHA-256: 2d8c2f6d978ca21712b5f6de36c9d31fa8e96a4fa5d8ff8b0188dfb9e7c171bb

# 驗證確定性：同一輸入，同一輸出
assert hashlib.sha256(msg).hexdigest() == digest

# 觀察雪崩效應：改一個字母
msg2 = b"Hello, Cryptography!"  # C 大寫
digest2 = hashlib.sha256(msg2).hexdigest()
print(f"SHA-256: {digest2}")
# 完全不同的 digest

# 計算兩個 digest 有多少 bit 不同
d1 = int(digest, 16)
d2 = int(digest2, 16)
diff_bits = bin(d1 ^ d2).count('1')
print(f"不同的 bit 數: {diff_bits} / 256")
# 大約 128——接近一半，這就是雪崩效應
```

不管輸入多長，SHA-256 的 digest 永遠是 256 bit（64 個十六進位字元）：

```python
# 空字串也有 hash
print(hashlib.sha256(b"").hexdigest())
# e3b0c44298fc1c149afbf4c8996fb924...

# 1 MB 的資料也是 256 bit
print(len(hashlib.sha256(b"A" * 1_000_000).hexdigest()))
# 64（字元）= 256 bit
```

---

## 底層機制：Birthday Paradox 與碰撞的數學

### Birthday Problem

一間教室要多少人，才有 50% 的機率有兩人同一天生日？

直覺猜 183（365 的一半）？錯。答案是 **23 人**。

原因：你不是在問「某人跟你同一天生日」（那確實要約 183 人），你是在問「任意兩人同一天生日」。23 人有 C(23, 2) = 253 對，每一對都有機會碰撞。

### Birthday Bound

這個數學推廣到 hash 函式：

- 一個 n-bit 的 hash 有 2ⁿ 個可能的輸出
- 要找到碰撞，平均需要 **2^(n/2)** 次 hash 運算

推導（簡化版）：

```
假設 H 的輸出空間大小為 N = 2ⁿ

隨機選 q 個不同的訊息，都沒碰撞的機率：
P(no collision) = (1 - 1/N)(1 - 2/N)...(1 - (q-1)/N)

用 1-x ≈ e^(-x) 近似：
P(no collision) ≈ e^(-q(q-1)/(2N))

令 P(no collision) = 0.5，解出：
q ≈ 1.177 × √N = 1.177 × 2^(n/2)
```

對 SHA-256（n=256）：碰撞需要 ~2¹²⁸ 次 hash——目前不可行。
對 SHA-1（n=160）：碰撞需要 ~2⁸⁰ 次——理論上已經被攻破到 2⁶³（SHAttered，Ch 14）。

### 安全等級與 birthday bound 的關係

| Hash 函式 | 輸出長度 | 碰撞抗性（birthday bound）| 原像抗性 |
|-----------|----------|--------------------------|----------|
| SHA-1     | 160 bit  | 2⁸⁰（理論，實際已被打到 2⁶³）| 2¹⁶⁰ |
| SHA-256   | 256 bit  | 2¹²⁸                     | 2²⁵⁶ |
| SHA-512   | 512 bit  | 2²⁵⁶                     | 2⁵¹² |
| SHA3-256  | 256 bit  | 2¹²⁸                     | 2²⁵⁶ |

重點：**碰撞永遠比原像容易攻擊**。設計系統時，安全等級由碰撞抗性決定，不是原像抗性。

---

## 底層機制：Merkle-Damgård 構造

SHA-1 和 SHA-2 都用 Merkle-Damgård（M-D）構造。這個構造把「設計一個能吃任意長度輸入的 hash」這個大問題，簡化成「設計一個吃固定長度輸入的壓縮函式（Compression Function）」。

### 構造流程

```
訊息 M                  填充 + 長度編碼
  │                         │
  ▼                         ▼
┌─────────┬─────────┬─────────┬──────────┐
│  m₁     │  m₂     │  m₃     │ padding  │
│ (block) │ (block) │ (block) │ +length  │
└────┬────┴────┬────┴────┬────┴────┬─────┘
     │         │         │         │
     ▼         ▼         ▼         ▼
IV ─→[  f  ]─→[  f  ]─→[  f  ]─→[  f  ]─→ H(M)
      壓縮      壓縮      壓縮      壓縮
      函式      函式      函式      函式
```

步驟：

1. **填充（Padding）**：把訊息填充到 block 大小的整數倍。SHA-256 的 block 是 512 bit。填充方式：先加一個 `1` bit，再加 `0` bits，最後附上原始訊息長度（64-bit big-endian）
2. **初始值（IV）**：一組固定的常數，SHA-256 用前 8 個質數平方根的小數部分
3. **鏈式壓縮**：每個 block 和前一步的 state 一起送進壓縮函式 `f`，輸出新的 state
4. **最終輸出**：最後一個壓縮函式的輸出就是 hash 值

### Merkle-Damgård 的安全保證

定理（Merkle-Damgård）：如果壓縮函式 `f` 是碰撞抗性的，那整個 hash 函式 `H` 也是碰撞抗性的。

這個定理把問題簡化了——密碼學家只需要設計一個安全的壓縮函式，M-D 構造保證整個 hash 安全。

### Merkle-Damgård 的弱點：Length Extension

M-D 有一個結構性弱點：**最終的 hash 值就是最後一步壓縮函式的 internal state**。

這意味著：

- 你拿到 `H(M)`，等於拿到了壓縮函式處理完 M 之後的 state
- 你可以從這個 state 繼續「接著 hash」更多資料
- 具體來說：知道 `H(M)` 和 `len(M)`，就能算出 `H(M || padding || extension)`，**不需要知道 M 的內容**

這就是 Length Extension Attack，Ch 15 會完整展開。現在只需要記住：**不能用 `H(secret || message)` 當 MAC**。

---

## 範例二：Birthday Attack 的 Python 模擬

我們用一個截斷到 n bit 的 hash 來模擬 birthday attack，驗證 2^(n/2) 的理論：

```python
import hashlib
import os

def truncated_hash(msg: bytes, n_bits: int) -> int:
    """取 SHA-256 的前 n_bits bit"""
    full = hashlib.sha256(msg).digest()
    # 轉成 int，取前 n_bits
    full_int = int.from_bytes(full, 'big')
    return full_int >> (256 - n_bits)

def birthday_attack(n_bits: int) -> tuple[bytes, bytes, int]:
    """
    對 n_bits 的 hash 跑 birthday attack。
    回傳 (msg1, msg2, attempts)，其中 H(msg1) == H(msg2)。
    """
    seen: dict[int, bytes] = {}
    attempts = 0

    while True:
        msg = os.urandom(16)  # 隨機 16 bytes 訊息
        h = truncated_hash(msg, n_bits)
        attempts += 1

        if h in seen and seen[h] != msg:
            return seen[h], msg, attempts
        seen[h] = msg

# 跑多次取平均
def benchmark(n_bits: int, trials: int = 20) -> None:
    total = 0
    for _ in range(trials):
        _, _, attempts = birthday_attack(n_bits)
        total += attempts
    avg = total / trials
    theoretical = 2 ** (n_bits / 2) * 1.177
    print(f"n={n_bits:2d} bit | 平均 {avg:8.0f} 次 | "
          f"理論 {theoretical:8.0f} 次 | "
          f"比值 {avg/theoretical:.2f}")

print("Birthday Attack 模擬結果：")
print("-" * 60)
for n in [16, 20, 24, 28, 32]:
    benchmark(n)
```

預期輸出（數字會有隨機浮動）：

```
Birthday Attack 模擬結果：
------------------------------------------------------------
n=16 bit | 平均      302 次 | 理論      301 次 | 比值 1.00
n=20 bit | 平均     1195 次 | 理論     1206 次 | 比值 0.99
n=24 bit | 平均     4810 次 | 理論     4824 次 | 比值 1.00
n=28 bit | 平均    19400 次 | 理論    19296 次 | 比值 1.01
n=32 bit | 平均    77200 次 | 理論    77184 次 | 比值 1.00
```

觀察：實驗值和理論的 1.177 × 2^(n/2) 幾乎一致。這就是 birthday bound 的威力——碰撞來得比直覺快得多。

---

## 對比與取捨

| 維度 | MD5 | SHA-1 | SHA-256 | SHA-3 (Keccak) | BLAKE3 |
|------|-----|-------|---------|-----------------|--------|
| 輸出長度 | 128 bit | 160 bit | 256 bit | 可變（預設 256） | 可變（預設 256） |
| 構造 | Merkle-Damgård | Merkle-Damgård | Merkle-Damgård | Sponge | Merkle tree + BLAKE |
| 碰撞抗性 | 已破（2⁴·³） | 已破（2⁶³） | 安全（2¹²⁸） | 安全（2¹²⁸） | 安全（2¹²⁸） |
| 速度（大檔案） | 快 | 快 | 中 | 中 | 極快（平行化） |
| Length Extension | 有 | 有 | 有 | 無 | 無 |
| 現在該用嗎 | 絕對不 | 絕對不 | 是 | 是 | 是（非 NIST 標準） |

選擇指引：
- **一般用途**：SHA-256（最廣泛支援、NIST 標準）
- **需要速度**：BLAKE3（Rust 生態常見，但缺 NIST 認證）
- **需要避免 length extension**：SHA-3 或 BLAKE3
- **要做 MAC**：用 HMAC（Ch 16），不要裸用 hash

---

## 踩雷集錦

### 踩雷 1：用 MD5 或 SHA-1 做任何安全用途

MD5 的碰撞在 2004 年被 Xiaoyun Wang 打破，2012 年被用來偽造 Flame 惡意軟體的微軟簽章。SHA-1 的碰撞在 2017 年被 SHAttered 打破。兩者都不能用於任何需要碰撞抗性的場景——不管是簽章驗證、證書、還是 HMAC 的底層 hash（雖然 HMAC-MD5 在理論上仍安全，但沒理由冒這個風險）。

### 踩雷 2：把 hash 當加密用

hash 不是加密。加密是可逆的（有 key 就能解密），hash 是不可逆的（設計上無法從 digest 還原 input）。「用 SHA-256 加密資料」這句話本身就是矛盾的。

### 踩雷 3：以為「沒碰撞」等於安全

碰撞抗性只是 hash 的三個安全屬性之一。有些 hash（如 CRC32）不是為安全設計的——它沒有原像抗性，攻擊者能輕鬆構造任意 CRC32 值的輸入。CRC 是 error-detection code，不是 cryptographic hash。

### 踩雷 4：用 `H(secret || message)` 做 MAC

Merkle-Damgård hash 有 length extension 弱點。攻擊者知道 `H(secret || message)` 和 `len(secret)`，就能在不知道 secret 的情況下算出 `H(secret || message || padding || attacker_data)`。正確做法是用 HMAC（Ch 16）。

### 踩雷 5：忽略 hash 輸出的 timing side-channel

比較兩個 hash 值時用 `==` 會導致 timing attack——相同 prefix 越長比較越慢，攻擊者能逐 byte 猜。用 `hmac.compare_digest()` 做 constant-time 比較：

```python
import hmac

# 錯誤：timing attack
if received_hash == expected_hash:  # 不安全
    pass

# 正確：constant-time 比較
if hmac.compare_digest(received_hash, expected_hash):
    pass
```

---

## 進階

### Merkle Tree

把 hash 組成二元樹結構，用於驗證大量資料的完整性：

```
         H(H₁₂ || H₃₄)         <- root hash
        /              \
   H(H₁ || H₂)    H(H₃ || H₄)
   /        \       /        \
 H(D₁)   H(D₂)  H(D₃)   H(D₄)  <- leaf hashes
  D₁       D₂     D₃       D₄    <- 資料塊
```

Merkle Tree 的優勢：只需要 O(log n) 個 hash 就能證明某個資料塊屬於這棵樹。Git、BitTorrent、區塊鏈、Certificate Transparency 都用這個結構。

### Hash 的隨機 Oracle 模型（Random Oracle Model）

安全證明中常假設 hash 函式是 random oracle：一個完美的隨機函式，對每個新輸入獨立均勻隨機選一個輸出。現實中沒有真正的 random oracle，但這個模型讓安全證明變得可處理。Merkle-Damgård 構造的 hash 不是 random oracle（length extension 就是反例），但在很多場景下仍然夠用。

### 多碰撞（Multi-collision）

Joux 在 2004 年證明：對 Merkle-Damgård hash，找 2ᵗ 個訊息全部碰撞到同一個 hash 值，只需要 t × 2^(n/2) 的工作量（而不是天真估計的 2^((2ᵗ-1)×n/2ᵗ)）。這個結果對串接兩個獨立 hash `H₁(m) || H₂(m)` 的安全性有致命影響——它的碰撞抗性不是 max(n₁, n₂)，而是接近 max(n₁/2, n₂/2)。

---

## 動手練習

1. **雪崩效應測量**：寫一個程式，對 10000 對只差一個 bit 的隨機輸入算 SHA-256，統計每一對 digest 有多少 bit 不同。畫出分佈圖（應該是以 128 為中心的常態分佈）

2. **birthday attack 完整實驗**：修改範例二的程式，對 n = 8, 12, 16, 20, 24 各跑 100 次，畫出 log₂(平均嘗試次數) vs n/2 的散佈圖，驗證線性關係

3. **Merkle-Damgård 填充**：用 SHA-256 的填充規則，手動對 `b"abc"` 做 padding，然後驗證填充後的 block 長度是 512 bit。提示：`b"abc"` 是 24 bit，padding 要加 `0x80` + 零 bytes + 8-byte big-endian 長度

4. **（挑戰）hash 碰撞視覺化**：對 8-bit 截斷 hash，窮舉所有可能的 2-byte 輸入，找出所有碰撞對，統計每個 hash 值有多少個 preimage

---

## 重點整理

1. **密碼學 hash 有三個安全屬性**：原像抗性（不可逆）、第二原像抗性（不能替換）、碰撞抗性（不能自由碰撞）。碰撞抗性最強
2. **Birthday bound**：n-bit hash 的碰撞抗性是 2^(n/2)，不是 2ⁿ。SHA-256 的碰撞抗性是 128-bit 安全等級
3. **Merkle-Damgård 構造**：把「吃任意長度」的問題簡化成「設計固定長度壓縮函式」。SHA-1、SHA-2 都用這個構造
4. **M-D 的弱點**：最終 hash 值就是 internal state，導致 length extension attack。不能用 `H(secret || msg)` 當 MAC
5. **現在該用**：SHA-256（最廣泛）或 SHA-3（無 length extension）。MD5 和 SHA-1 絕對不用於安全用途

---

## 自我檢核

1. 碰撞抗性和第二原像抗性的差別是什麼？哪個更強？
2. 為什麼 SHA-256 的碰撞抗性是 2¹²⁸ 而不是 2²⁵⁶？
3. Merkle-Damgård 構造的四個步驟是什麼？
4. 為什麼 `H(secret || message)` 不能當 MAC？length extension 的原因是什麼？
5. 比較兩個 hash 值時為什麼不能用 `==`？該用什麼？

---

## 延伸閱讀

- **Rogaway & Shrimpton (2004)**："Cryptographic Hash-Function Basics"——三個安全屬性的嚴格定義和關係
- **Joux (2004)**："Multicollisions in Iterated Hash Functions"——M-D 多碰撞的經典論文
- **Boneh & Shoup, Ch 8**：hash 函式的形式化安全定義和 birthday bound 的完整推導
- **Serious Cryptography, Ch 6**：hash 函式的工程導向介紹

---

## 下一章預告

[Ch 14 — SHA 家族](./14-sha-family.md)：SHA-1 的 SHAttered 碰撞攻擊、SHA-2 的內部結構、SHA-3 的 sponge 構造——為什麼 sponge 天生沒有 length extension 問題。
