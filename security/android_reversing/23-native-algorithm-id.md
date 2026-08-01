# Ch 23 — native 演算法識別與加密還原

> **目標**：拿到一段 native 反組譯碼，你要能在一分鐘內認出「這是 AES / MD5 / SHA / CRC / TEA / base64」，而不是逐行讀組語。方法是**看常數指紋**——每個標準演算法都有它獨一無二、寫死在程式碼裡的魔數（magic constant）與查找表（lookup table）。這章教你認這些指紋、解釋每個魔數的來源，並用 Python 把它們**實際算出來**，讓你知道 IDA 裡看到的那串數字為什麼是它。最後把 findcrypt 這類自動掃描工具的思路講清楚。

> **環境**：本章所有魔數常數（AES S-box、MD5/SHA 初始值、SHA-256 K 表、TEA delta、CRC32 表、base64 alphabet）都用 **Python 3.12** 在本機**實際算出**，輸出標「**實際輸出**」——你看到的每個十六進位值都是跑出來的，不是抄的。IDA/Ghidra 逆 `.so` 的截圖描述為代表性說明（沙箱無 IDA）。

## 為什麼需要這個？

現代 App 把值錢的東西——請求簽名、資料加密、授權校驗——放進 native `.so`，正是賭你逆不動組語。但這裡有個對逆向者極其有利的事實：**幾乎沒有人自己重新發明加密演算法**。他們用的是 AES、MD5、SHA-256、HMAC、TEA 這些標準演算法，而標準演算法為了正確性，**必須**把一組固定的常數寫死在程式裡。這些常數就是指紋。

你不需要讀懂 `libcrypto` 那個 AES 函式的每一條 `eor`/`and`，你只要在它的資料段看到 `63 7C 77 7B F2 6B 6F C5...` 這 256 個 byte——那是 AES 的 S-box，全世界只有 AES（和它的近親）用這張表。認出它，這整個函式就是 AES，剩下的只是找 key 和 mode。

這一章是 Ch 22（IDA/Ghidra 逆 `.so`）的直接延伸：Ch 22 教你怎麼把 `.so` 打開、看反組譯；這一章教你打開之後**怎麼快速認出裡面在算什麼**。認出演算法，你才能把 native 的加密**還原**成一段你能重放的程式碼（練習 C 就是幹這個）。

## 先建立直覺：演算法 = 一組寫死的常數 + 一段運算

先建立一個核心心智模型：**一個標準密碼演算法，可以被它的常數唯一辨識**。原因是這些常數不是隨便取的，是規格書（FIPS、RFC）**強制規定**的值——任何正確實作都必須用一模一樣的值，否則算出來的結果跟別人對不上，加解密就失敗。

```
   一個加密函式在記憶體裡長這樣：
 ┌─────────────────────────────────────────────┐
 │  .rodata (唯讀資料段)                          │
 │    ┌──────────────────────────────────┐       │
 │    │ 常數 / 查找表 (constant / S-box)  │◀──── 指紋在這！
 │    │   AES S-box: 63 7C 77 7B ...      │       │
 │    │   SHA init:  67452301 EFCDAB89... │       │
 │    └──────────────────────────────────┘       │
 │              │ 被下面的 code 讀取               │
 │  .text (程式碼段)                              │
 │    ┌──────────────────────────────────┐       │
 │    │ 一堆 xor / shift / add / lookup   │◀──── 運算（難讀，但你不用讀）
 │    └──────────────────────────────────┘       │
 └─────────────────────────────────────────────┘

 逆向策略：不讀 .text 的運算，先掃 .rodata 認常數 → 認出演算法 → 只逆「key/iv 怎麼餵進去」
```

這個策略的威力在於**你把「讀懂整個演算法」這個難題，換成了「認一組常數」這個查表題**。運算再怎麼混淆、再怎麼展開迴圈，只要它是標準演算法，那組常數就藏不住（除非它連常數都動態生成——那是進階對抗，最後一節談）。

每個常數的來源都有故事。標準演算法的設計者為了讓常數「看起來沒有後門」（nothing-up-my-sleeve number），常用數學上人盡皆知的值：質數的平方根、立方根的小數部分、黃金比例。這不是裝飾——知道來源，你就能**自己算出來驗證**，而不是死背一串十六進位。下面每一個我們都算給你看。

## 指紋一：AES —— S-box 那 256 個 byte

AES 最好認的指紋是它的 **S-box（substitution box，替換盒）**：一張 256 byte 的查找表，用於 `SubBytes` 步驟。這張表的每個 byte 是「在 GF(2⁸) 有限體裡求乘法反元素，再做一個仿射變換」的結果——聽起來嚇人，但它就是一組**固定**的 256 個值。

我們從數學定義**自己算一遍**，證明它就是那張全世界都在用的表（**實際輸出**）：

```python
# 從 GF(2^8) 定義算 AES S-box，不查表
def gmul_inv(a):                      # 在 GF(2^8) (模 0x11b) 求 a 的乘法反元素
    if a == 0: return 0
    for b in range(256):
        p=0; x=a; y=b
        for _ in range(8):            # x*y in GF(2^8)
            if y&1: p^=x
            hi=x&0x80; x=(x<<1)&0xff
            if hi: x^=0x1b
            y>>=1
        if p==1: return b
    return 0
def affine(b):                        # 仿射變換 + 常數 0x63
    s=b
    for i in range(1,5): s^=((b<<i)|(b>>(8-i)))&0xff
    return s^0x63
sbox=[affine(gmul_inv(i)) for i in range(256)]
print(" ".join(f"{x:02x}" for x in sbox[:16]))
```

```
63 7c 77 7b f2 6b 6f c5 30 01 67 2b fe d7 ab 76
```

**這 16 個 byte —— `63 7c 77 7b f2 6b 6f c5 30 01 67 2b fe d7 ab 76` —— 就是 AES 的指紋**。在 IDA 的 `.rodata` 裡按 byte 檢視，只要開頭撞見這串，這個 `.so` 一定有 AES。反過來的 **inverse S-box** 開頭是 `52 09 6a d5 30 36 a5 38...`，那代表有 AES 解密。

其他 AES 線索：
- **Rcon（round constant）**：`01 02 04 08 10 20 40 80 1b 36`——金鑰擴展用，一串 2 的次方接 GF 溢位後的 `1b 36`。看到這串短序列也是 AES。
- **T-tables**：某些高效實作（OpenSSL）把 S-box 和 MixColumns 合併成 4 張 1KB 的表（`Te0..Te3`），開頭是 `c66363a5 f87c7c84...`。看到 `c66363a5` 就是 T-table 版 AES。

> **認出 AES 之後還要找三件事**：(1) **mode**——ECB / CBC / CTR / GCM，看有沒有用到 IV、有沒有 XOR 前一塊密文（CBC 特徵）；(2) **key**——通常是傳進函式的一個指標，Ch 24 動態調試時在這下斷點 dump 出來最快；(3) **key 長度**——輪數 10/12/14 對應 128/192/256 bit。光認出「是 AES」不夠，還原要靠這三個。

## 指紋二：MD5 與 SHA 家族 —— 那四個 / 五個初始值

雜湊函式的指紋是它的**初始化向量（IV，initial hash values）**：一組寫死的起始暫存器值。

**MD5** 的初始值是四個 32-bit 常數，來源是「小端排列的 0..F 遞增序列」（**實際輸出**）：

```python
for v in (0x67452301,0xefcdab89,0x98badcfe,0x10325476):
    print(f"  0x{v:08x}")
```

```
  0x67452301
  0xefcdab89
  0x98badcfe
  0x10325476
```

仔細看：`67 45 23 01` 反過來是 `01 23 45 67`，`ef cd ab 89` 反過來是 `89 ab cd ef`——就是 `0123456789abcdef` 拆兩半、每半以小端存放。這是 MD5 設計者選的 nothing-up-my-sleeve 值。在 IDA 看到這四個 dword 連在一起，這函式就是 MD5。

**SHA-1** 用同樣的四個，**再多一個** `0xC3D2E1F0`（**實際輸出**）：

```
  0x67452301
  0xefcdab89
  0x98badcfe
  0x10325476
  0xc3d2e1f0
```

所以區分 MD5 與 SHA-1 就看**有沒有第五個 `C3D2E1F0`**：有 → SHA-1，沒有 → MD5。

**SHA-256** 的初始值完全不同，來源是**前 8 個質數（2,3,5,7,11,13,17,19）平方根的小數部分**乘以 2³²。我們算給你看它為什麼是那些值（**實際輸出**）：

```python
import math
for p in (2,3,5,7,11,13,17,19):
    frac = math.sqrt(p) - int(math.sqrt(p))     # 平方根的小數部分
    print(f"  sqrt({p}) -> 0x{int(frac*(1<<32)):08x}")
```

```
  sqrt(2) -> 0x6a09e667
  sqrt(3) -> 0xbb67ae85
  sqrt(5) -> 0x3c6ef372
  sqrt(7) -> 0xa54ff53a
  sqrt(11) -> 0x510e527f
  sqrt(13) -> 0x9b05688c
  sqrt(17) -> 0x1f83d9ab
  sqrt(19) -> 0x5be0cd19
```

看到 `6a09e667`（√2 的小數部分）你就認得 SHA-256。這個值太有名了，findcrypt 之類的工具第一個掃的就是它。

SHA-256 還有第二組指紋更難藏：它的 **K 表（round constants）** 有 64 個常數，來源是**前 64 個質數立方根**的小數部分（**實際輸出**）：

```python
for p in (2,3,5,7):
    print(f"  cbrt({p}) -> 0x{int(((p**(1/3.0))%1)*(1<<32)):08x}")
```

```
  cbrt(2) -> 0x428a2f98
  cbrt(3) -> 0x71374491
  cbrt(5) -> 0xb5c0fbcf
  cbrt(7) -> 0xe9b5dba5
```

`428a2f98 71374491 b5c0fbcf e9b5dba5...` 這 64 個 dword 連在一起，是 SHA-256 藏不掉的招牌（SHA-512 用 80 個 64-bit 常數，開頭 `428a2f98d728ae22`——看得出跟 SHA-256 同源但是 64-bit）。

| 演算法 | 指紋常數 | 來源 | 怎麼區分 |
|---|---|---|---|
| MD5 | `67452301 efcdab89 98badcfe 10325476` | `0123...ef` 小端 | 只有這 4 個 |
| SHA-1 | 上面 4 個 + `c3d2e1f0` | 同 MD5 + 一個 | 多第 5 個 |
| SHA-256 | init `6a09e667...`；K 表 `428a2f98...` | √質數 / ∛質數 | init 撞 `6a09e667` |
| SHA-512 | `6a09e667f3bcc908...`（64-bit） | √質數（更多位） | 64-bit 版的 SHA-256 init |

## 指紋三：TEA/XTEA —— 那個黃金比例 delta

TEA（Tiny Encryption Algorithm）與它的變種 XTEA/XXTEA 在 App 裡出奇地常見，因為它**短小、好嵌、沒有查找表**，開發者愛拿它當自製加密。它沒有 S-box，但它有一個藏不住的魔數：**delta = `0x9e3779b9`**。

這個值不是亂取的，它是**黃金比例的小數部分乘以 2³²**（**實際輸出**）：

```python
delta = int((5**0.5 - 1)/2 * (1<<32))    # (√5 - 1)/2 = 0.618... 黃金比例
print(f"  0x{delta:08x}")
```

```
  0x9e3779b9
```

在反組譯碼裡看到一個迴圈，每輪把某個累加器加上 `0x9E3779B9`，配合 `<<4`、`>>5`、`xor`——那 99% 是 TEA/XTEA。這個常數幾乎是 TEA 的同義詞。（附帶一提，同樣的 `0x9e3779b9` 也出現在一些雜湊函式如 Jenkins hash 裡，因為黃金比例是很好的「攪拌」乘數——所以看到它先想 TEA，但也留意 hash 的可能。）

TEA 是可逆的（對稱加密），我們驗一次 encrypt→decrypt round-trip，順便讓你看 delta 怎麼用（**實際輸出**）：

```python
def tea_encrypt(v, key):
    v0,v1=v; s=0; delta=0x9e3779b9; k0,k1,k2,k3=key
    for _ in range(32):
        s=(s+delta)&0xffffffff
        v0=(v0+(((v1<<4)+k0 ^ v1+s ^ (v1>>5)+k1)))&0xffffffff
        v1=(v1+(((v0<<4)+k2 ^ v0+s ^ (v0>>5)+k3)))&0xffffffff
    return v0,v1
```

```
  plaintext:  deadbeef cafebabe
  ciphertext: 0d4c1dea 4b37cfc1
  decrypted:  deadbeef cafebabe  match=True
```

還原 TEA 時的重點：找到那 4 個 32-bit 的 key（`k0..k3`）與輪數（標準 32 輪），你就能在 host 上重寫一份解密。key 通常在 `.data` 或函式參數裡。

## 指紋補充：ChaCha20/Salsa20、MD5 T-table、Rcon、RC4

前面幾個是最常撞見的，但實務上你還會碰到這幾個，一起收進指紋庫。

**ChaCha20 / Salsa20** 的招牌是一個**明文 ASCII 常數** `"expand 32-byte k"`（16 byte 的 sigma 常數，用在狀態初始化）。它以 4 個小端 dword 存在記憶體（**實際輸出**）：

```python
sigma = b"expand 32-byte k"
print(" ".join(f"0x{int.from_bytes(sigma[i:i+4],'little'):08x}" for i in range(0,16,4)))
```

```
0x61707865 0x3320646e 0x79622d32 0x6b206574
```

在字串窗看到 `expand 32-byte k`（或 16-byte key 版的 `expand 16-byte k`），就是 ChaCha/Salsa。這是現代 App（尤其用 libsodium/BoringSSL）越來越常見的串流加密。

**MD5 的第二組指紋——T-table**：除了 init 那四個值，MD5 還有 64 個 round 常數 `K[i] = floor(2³² × |sin(i+1)|)`（**實際輸出**）：

```python
import math
for i in range(4):
    print(f"  K[{i}] = 0x{int(abs(math.sin(i+1))*(1<<32)):08x}")
```

```
  K[0] = 0xd76aa478
  K[1] = 0xe8c7b756
  K[2] = 0x242070db
  K[3] = 0xc1bdceee
```

`d76aa478 e8c7b756 242070db c1bdceee...` 這 64 個 dword 是 MD5 藏不掉的第二指紋——比 init 更難躲，因為就算開發者改了 init 的載入方式，運算裡的 T-table 還在。來源是「正弦函式」——又一個 nothing-up-my-sleeve 選擇。

**AES Rcon**（金鑰擴展的 round constant）——一串短序列（**實際輸出**）：

```
01 02 04 08 10 20 40 80 1b 36
```

前面是 2 的次方，到 `0x80` 後左移溢位、GF 約化成 `1b`、再翻倍成 `36`。看到這串 10 byte 序列是 AES 金鑰擴展的鐵證。

**RC4 是例外——它幾乎沒有指紋**。RC4 的初始化是「S 盒填成 identity permutation `00 01 02 03 ... FF` 再依 key 洗牌」，沒有任何魔數常數。所以 **findcrypt 抓不到 RC4**。你只能靠**運算形狀**認：一個 256-byte 陣列先被填成遞增序列（`for i: S[i]=i`）、接著一個 `i,j` 雙指標交換迴圈、輸出時 `S[(S[i]+S[j])&0xff]`——這個「填 identity + swap loop」的結構就是 RC4 的簽名，但要讀運算、不能靠掃常數。這是「有些演算法沒常數指紋」的重要反例。

## 指紋四：CRC —— 那張 256 項的表 / 那個 poly

CRC（cyclic redundancy check）不是加密，是**校驗**——App 常用它做完整性檢查（Ch 32 的主題）。它的指紋是**查找表**或**多項式（polynomial）常數**。

最常見的 **CRC32（標準 poly，反射版）** 用 `0xEDB88320`。表是從這個 poly 生成的，我們算前幾項（**實際輸出**）：

```python
def crc_table():
    tab=[]
    for n in range(256):
        c=n
        for _ in range(8):
            c=(0xEDB88320 ^ (c>>1)) if (c&1) else (c>>1)
        tab.append(c)
    return tab
t=crc_table()
print("  table[0..3]:", " ".join(f"0x{x:08x}" for x in t[:4]))
```

```
  table[0..3]: 0x00000000 0x77073096 0xee0e612c 0x990951ba
```

指紋是 **`table[1] = 0x77073096`**（`table[0]` 永遠是 0，沒鑑別度）。在資料段看到一張 256 個 dword 的表、第二項是 `77073096`，就是標準 CRC32。看到 poly 常數 `0xEDB88320`（反射）或 `0x04C11DB7`（正向）本身也算指紋。

其他 CRC 變種認 poly：CRC-16 的 `0xA001`（Modbus）、`0x1021`（CCITT）。這些短常數在程式碼裡當立即數出現。

## 指紋五：base64 —— 那個 alphabet 字串

base64 不是加密（是編碼），但它**極常見**且常被誤認成加密，所以要會一眼識破。它的指紋是那個 64 字元的 **alphabet 字串**，通常以明文字串常數躺在 `.rodata`：

```
標準:  ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/
URL 安全:  ...同上但結尾是 -_ （+/ 換成 -_，用於 URL）
```

在 IDA 的字串視窗（Shift+F12）看到這串 `ABCD...+/`，這個 `.so` 就有 base64。**魔改的 base64** 是逆向的常見陷阱：開發者把 alphabet 的字元順序打亂（例如 `BCDA...`），輸出看起來像 base64 但用標準解碼器解不開——這時你要做的是**把它魔改後的 alphabet 抓出來**（就在字串常數裡），用它建一個自訂解碼表。認出「這是 base64（只是換了表）」比死磕輸出重要得多。

> **base64 為什麼常被誤認成加密**：它的輸出是一串看似隨機的 `A-Za-z0-9`，不懂的人以為是密文。判準就兩點：base64 **沒有 key**，且長度嚴格是輸入的 4/3（含 `=` padding）。看到輸出結尾有 `=` 或 `==`、字元集只有那 64 個，先當 base64 試解。它常是「真加密之後再包一層編碼方便傳輸」，解開 base64 才看到底下的密文。

## 底層機制：findcrypt 是怎麼自動掃的

上面全是人工認。findcrypt（IDA 外掛，及 Ghidra 的等價功能、`yara` 的 crypto 規則）把這件事自動化，原理**出奇地簡單**：

```
findcrypt 的核心邏輯
 ┌────────────────────────────────────────────────┐
 │ 1. 內建一個「已知常數指紋」資料庫                  │
 │      AES S-box   → 63 7C 77 7B F2 6B 6F C5 ...   │
 │      SHA-256 init→ 6A 09 E6 67 BB 67 AE 85 ...   │
 │      TEA delta   → B9 79 37 9E (小端)            │
 │      CRC32 table → ... 96 30 07 77 ...           │
 │      (數十到數百條)                               │
 │                                                  │
 │ 2. 掃過整個 binary 的每個 section 的 bytes        │
 │                                                  │
 │ 3. 用 memmem / Boyer-Moore 找這些 byte 序列       │
 │      命中 → 報「在 0xXXXX 找到 AES S-box」        │
 └────────────────────────────────────────────────┘
```

本質就是**在二進位裡做多模式字串搜尋**，搜的目標是那些寫死的常數。理解這點你會得到兩個實用推論：

1. **你可以手動 findcrypt**：不裝外掛時，直接在 IDA 的 hex view 用 search bytes 搜 `63 7C 77 7B`（AES）或 `67 45 23 01`（MD5/SHA）就行。findcrypt 只是把這動作批量化。
2. **findcrypt 有盲點**：它靠**明文常數**。如果開發者把 S-box **加密存放、執行期才解密**，或**動態生成**（像上面我們算 S-box 那樣，用程式碼算出來而不是存表），靜態掃就撲空。這時得靠動態（Ch 24 在解密後的記憶體 dump）——這正是靜態卡住、動態接手的典型場景。

小 Python 版的「findcrypt」讓你看清它多簡單——我們建一個假的 `.rodata`（塞進 AES S-box、TEA delta、ChaCha sigma），掃它（**實際輸出**）：

```python
FINGERPRINTS = {
    "AES S-box":     bytes.fromhex("637c777bf26b6fc5"),
    "MD5 init(LE)":  bytes.fromhex("0123456789abcdef"),
    "SHA-256 init":  bytes.fromhex("6a09e667bb67ae85"),
    "TEA delta(LE)": bytes.fromhex("b937379e"),      # 0x9e3779b9 小端
    "CRC32 tbl[1]":  bytes.fromhex("96300777"),      # 0x77073096 小端
    "ChaCha sigma":  b"expand 32-byte k",
}
def scan(blob):
    for name, sig in FINGERPRINTS.items():
        idx = blob.find(sig)
        if idx >= 0:
            print(f"  [+] {name:14s} @ offset {idx}")

blob = b"\x00"*32 + bytes.fromhex("637c777bf26b6fc530016726") + b"junk" \
     + bytes.fromhex("b937379e") + b"more" + b"expand 32-byte k" + b"\x00"*16
scan(blob)
```

```
  [+] AES S-box      @ offset 32
  [+] TEA delta(LE)  @ offset 48
  [+] ChaCha sigma   @ offset 56
```

把一個真 `.so` 的 bytes 餵進 `scan()`，命中就報。**注意每個指紋都以小端 byte 序列存**（`0x9e3779b9` 存成 `b9 37 37 9e`，我上面寫成 `b937379e` 是因為記憶體裡就是這個順序）——這正是踩雷集錦第 5 條的重點：你搜的是 byte 序列，不是 dword 常數。真正的 findcrypt 只是規則更多、會同時試大小端與部分匹配。

## 對比與取捨：認演算法的幾種手段

| 手段 | 怎麼做 | 優點 | 侷限 |
|---|---|---|---|
| **人工認常數** | 掃 `.rodata` 對指紋 | 不裝工具、理解最深 | 慢、要記指紋 |
| **findcrypt / yara** | 外掛自動掃 | 快、批量 | 只認明文常數，動態生成/加密表撲空 |
| **看 API 呼叫** | 找 `import` 的 `MD5_Init`/`AES_encrypt`/`CCCrypt` | 有符號時最快 | strip / 靜態連結後符號沒了 |
| **動態 hook** | Frida hook 已知庫函式印參數 | 直接拿到明文/key | 要能執行、要先知道 hook 誰 |
| **看資料流形狀** | 輸出長度 = 16 倍數(AES 塊)、32/64 hex(SHA) | 沒常數時的旁證 | 只是旁證，不確定 |

實務是**組合拳**：先 findcrypt 掃一遍（快），掃不到的用人工看常數與資料流形狀猜，猜到候選再用 Frida hook（Ch 14）印中間值確認。

## 踩雷集錦

1. **把 base64 當加密**：輸出一串 `A-Za-z0-9+/`、結尾 `=`——先當 base64 解。它沒有 key，是編碼不是加密。魔改 alphabet 也是把換過的表抓出來即可，不要當成破解了什麼強密碼。
2. **看到 `0x9e3779b9` 只想到 TEA**：它也是好用的雜湊攪拌常數（Jenkins hash、fibonacci hashing）。先假設 TEA，但若周圍沒有 `<<4/>>5/xor` 的塊加密結構，考慮它是 hash 的乘數。
3. **以為認出演算法就完事**：認出「是 AES」只是開始。mode（ECB/CBC/CTR/GCM）、key、IV 三者不找齊，你重放不出來。認演算法省的是「讀懂運算」，找 key/iv/mode 的功還是要下。
4. **S-box 被動態生成就以為沒有 AES**：像本章開頭我們用程式算 S-box 一樣，有些實作不存表、執行期算。findcrypt 掃不到不代表沒有——看運算結構（16 byte 塊、10/12/14 輪、有替換有位移）也能認，或動態 dump 生成後的表。
5. **大小端看反**：`0x9e3779b9` 在小端記憶體裡是 byte 序列 `b9 37 37 9e`。你在 hex view 搜的是 byte 序列，記得把 dword 常數轉成對的位元組順序，不然搜不到。
6. **HMAC 誤認成純雜湊**：HMAC-SHA256 底層就是 SHA-256，你會看到 SHA-256 的指紋，但它多了 key 與 ipad/opad（`0x36`/`0x5c` 重複）。看到 SHA 指紋 + `0x36363636...`/`0x5c5c5c5c...` 的 pad，是 HMAC 不是裸 SHA。

## 進階：再往深一層

- **魔改演算法（modified crypto）**：對抗逆向的常見手法是把標準演算法「改一點點」——換 S-box、改 delta、多/少幾輪、改 IV。這時 findcrypt 認得出「像 AES」但你直接套 OpenSSL 解不開。做法是逆出**它到底改了哪裡**（哪個常數不一樣），在你的重實作裡改同樣的地方。認出「這是魔改 AES」比認出「這是 AES」值錢。
- **白盒密碼（white-box crypto）**：更狠的一層，把 key **融進查找表**，程式碼裡完全沒有獨立的 key，S-box 也被 key 相關的巨大表取代（好幾 MB 的表）。findcrypt 完全失效。攻擊白盒是專門的研究領域（DFA/差分故障分析），超出本課，但你要能認出「這一大坨表 + 沒有明顯 key = 可能是白盒」。
- **動態生成常數躲靜態掃描**：程式啟動時才把 S-box/K 表算出來寫進記憶體。靜態 `.rodata` 乾乾淨淨。破法是 Ch 24 動態調試，在演算法執行**之後**、常數已在記憶體時 dump——這是靜態與動態互補的又一個例子。
- **常數當作 anti-analysis 的誘餌**：少數狠角色故意放一張**假的** AES S-box 引 findcrypt 命中，真正的加密在別處。所以 findcrypt 報命中後，還是要看那張表**有沒有真的被關鍵路徑用到**（交叉引用 xref），別被誘餌帶偏。

## 動手練習

1. 把本章算 AES S-box、MD5/SHA init、SHA-256 K 表、TEA delta、CRC32 表的 Python 全部自己跑一遍，確認每個十六進位值都跟本章「實際輸出」一致。**親手算過的常數才記得住**。
2. 寫一個 30 行的迷你 findcrypt：讀一個檔案的 bytes，用本章的 `FINGERPRINTS` 掃，報命中位置。拿一個含 OpenSSL 的 `.so`（或任何 binary）餵進去試。
3. 找一段 base64（含 `=` padding）與一段魔改 base64（自己把 alphabet 打亂編碼一段）。寫解碼器：標準的直接 `base64.b64decode`，魔改的先用打亂的 alphabet 建 translation table 再解。體會「認出是 base64、抓出 alphabet」的流程。
4. （若有 arm64 AVD 或真機）把一個用了 AES 的 App 的 `.so` 拉出來，在 IDA/Ghidra 開 hex view，search bytes `63 7C 77 7B`，看能不能命中 S-box，並跳到 xref 找出用它的函式。

## 本章重點整理

- 標準密碼演算法 = **一組規格強制的常數 + 運算**；認常數就等於認演算法，不必讀懂運算。
- **AES** 認 S-box `63 7C 77 7B...`；**MD5** 認 `67452301 efcdab89 98badcfe 10325476`；**SHA-1** 多 `c3d2e1f0`；**SHA-256** 認 init `6a09e667` 或 K 表 `428a2f98`；**TEA** 認 delta `0x9e3779b9`（黃金比例）；**CRC32** 認表 `table[1]=0x77073096` 或 poly `0xEDB88320`；**base64** 認 alphabet 字串。
- 每個常數都有 nothing-up-my-sleeve 來源（質數平方根/立方根、黃金比例、遞增序列），本章**用 Python 全算出來驗證**。
- **findcrypt = 在 binary 裡搜已知常數指紋**；它的盲點是動態生成/加密存放的常數，那要靠動態 dump（Ch 24）。
- 認出演算法只是起點，還要找 **mode / key / iv** 才能還原重放。

## 自我檢核

- [ ] 不看筆記，能說出 AES、MD5、SHA-256、TEA、CRC32 各自的指紋常數
- [ ] 能解釋 MD5 init 的 `67452301` 和 TEA delta `0x9e3779b9` 各自的數學來源
- [ ] 能區分 MD5 / SHA-1 / SHA-256 三者的初始值差在哪
- [ ] 能講清楚 findcrypt 的原理，以及它為什麼會漏掉動態生成的常數
- [ ] 看到一串 `A-Za-z0-9+/` 結尾帶 `=` 的輸出，知道先當 base64 而非加密，並知道怎麼處理魔改 alphabet
- [ ] 知道「認出是 AES」之後還要找哪三件事才能重放

## 延伸閱讀

- **[IDA FindCrypt / FindCrypt2 原理與原始碼](https://github.com/polymorf/findcrypt-yara)** — polymorf
  - **讀哪裡**：它的 yara 規則檔（`findcrypt.rules`），一條一條就是各演算法的常數指紋
  - **和本章的關聯**：本章「findcrypt 是搜常數」的具體實現；讀它的規則等於背下所有指紋
- **[FIPS 197 — AES 標準](https://csrc.nist.gov/pubs/fips/197/final)** — NIST
  - **讀哪裡**：S-box 那張表與它的 GF(2⁸) 定義；Rcon
  - **為什麼值得讀**：AES S-box 的權威定義，本章我們算的就是這張表；想確認「這 256 byte 對不對」查它
- **[RFC 1321 (MD5) / RFC 6234 (SHA)](https://www.rfc-editor.org/rfc/rfc6234)** — IETF
  - **讀哪裡**：初始值與常數那節
  - **和本章的關聯**：MD5/SHA init 值的一手來源，附參考實作 C 碼，逆向時對照
- **[HackTricks — Reversing common crypto](https://book.hacktricks.wiki/en/reversing/common-api-used-in-malware.html)**
  - **這篇說什麼**：逆向常見加密/編碼的識別 cheat sheet
  - **讀哪裡**：crypto constant 與 API 那幾段，卡住時快速對照

認出演算法、鎖定「key 在某個指標」之後，靜態往往就到頭了——因為 key 是執行期才算出來的。下一章我們把 native 跑起來，用 IDA remote / lldb / gdbserver 在關鍵函式下斷點，直接把記憶體裡的 key、iv、明文 dump 出來。

→ [Ch 24 動態調試 native](./24-native-dynamic-debug.md)
