# Ch 13 — Hash 函式：抗碰撞 / 原像 / 第二原像、Merkle-Damgård

> 目標：搞懂 hash 三種安全屬性（preimage / second preimage / collision resistance）的差別、生日攻擊為什麼讓 collision 比 preimage 容易、Merkle-Damgård 構造怎麼把固定輸入壓縮函式擴展成任意長 hash。

## Hash function 的概念

```
任意長 input  ────►  H(·)  ────► 固定長 output（如 256 bit）
```

要求：

1. **計算快**（毫秒等級對 GB 訊息）
2. **看起來隨機**（output 與 input 統計獨立）
3. **抗某些攻擊**（下面三種）

例：

```python
import hashlib
print(hashlib.sha256(b"hello").hexdigest())
# 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824

print(hashlib.sha256(b"hello!").hexdigest())
# ce06092fb948d9ffac7d1a376e404b26b7575bcc11ee05a4615fef4fec3a308b
```

input 改一個 bit → output 完全不同。**雪崩效應 (avalanche effect)** 是 hash 的基本性質。

## 三種安全屬性

```
1. 抗原像 (Preimage Resistance)
   給 y，找 x 使 H(x) = y
   難度：~ 2^n（對 n-bit hash）
   
2. 抗第二原像 (Second Preimage Resistance)
   給 x，找 x' ≠ x 使 H(x') = H(x)
   難度：~ 2^n
   
3. 抗碰撞 (Collision Resistance)
   找任意 (x, x') 使 x ≠ x' 且 H(x) = H(x')
   難度：~ 2^(n/2)（生日攻擊）
```

注意 **collision 的難度是 n/2**，不是 n。生日攻擊讓 attacker 能找到任意碰撞，比找特定值的 preimage 容易得多。

## 生日悖論

「23 個人裡有兩個同生日的機率超過 50%」 — 因為配對數是 C(23, 2) = 253。

對 hash function 同理：

```
要在 N 個元素的空間中找到一個碰撞，
平均需要 √N ≈ 1.25 √N 次嘗試
```

對 n-bit hash：

- preimage：2^n 次
- second preimage：2^n 次
- collision：**2^(n/2)** 次

例：

| Hash | n bits | collision 成本 |
|---|---|---|
| MD5 | 128 | 2⁶⁴（1990s 算力可達） |
| SHA-1 | 160 | 2⁸⁰（2017 Google 用 2⁶³ 找到，靠 differential） |
| SHA-256 | 256 | 2¹²⁸（不可能） |
| SHA-512 | 512 | 2²⁵⁶（過量） |

**設計密碼系統要 hash size = 安全 bit × 2**。要 128-bit security → 用 SHA-256。

## 生日攻擊範例

```python
import hashlib
import random

def find_collision_truncated(bits=24):
    """truncate hash 到指定 bits 找 collision（演示用）"""
    seen = {}
    n = 0
    while True:
        n += 1
        msg = random.randbytes(16)
        h = int.from_bytes(hashlib.sha256(msg).digest(), 'big') >> (256 - bits)
        if h in seen and seen[h] != msg:
            return seen[h], msg, n
        seen[h] = msg

x1, x2, n = find_collision_truncated(24)
print(f"找到 24-bit collision，嘗試 {n} 次")
print(f"  x1 = {x1.hex()}")
print(f"  x2 = {x2.hex()}")
# 預期 n ≈ 2^12 = 4096 左右
```

## Merkle-Damgård 構造

把「**固定 input 大小**」的 compression function 擴展成「**任意長 input**」的 hash：

```
                     IV
                     │
                     ▼
M_1 ──► f ──► state_1
                     │
                     ▼
M_2 ──► f ──► state_2
                     │
                     ▼
M_3 ──► f ──► state_3
                     │
                     ...
                     │
                     ▼
M_k ──► f ──► state_k = output
```

`f`：compression function，輸入 (state, message_block) → 新 state。

關鍵步驟：

1. **padding**：把 message 補到 block size 倍數，**最後 64 bit 寫總長度**（Merkle-Damgård strengthening）
2. **每 block 餵 f**：state 累積
3. **最後 state = hash output**

MD5、SHA-1、SHA-2 都用這個構造。**只是 compression function 不同**。

```python
def merkle_damgard_skeleton(message, IV, f, block_size=64):
    # padding（簡化版，與 SHA-256 規範類似）
    msg_len = len(message)
    message += b'\x80'                    # 一個 0x80 byte
    while (len(message) + 8) % block_size != 0:
        message += b'\x00'
    message += (msg_len * 8).to_bytes(8, 'big')
    # iterate
    state = IV
    for i in range(0, len(message), block_size):
        block = message[i:i+block_size]
        state = f(state, block)
    return state
```

## Merkle-Damgård 的天生缺陷：length extension

Merkle-Damgård 有個討厭的性質：**知道 H(M) 的人能算 H(M || padding || M')**，**不需要知道 M 的內容**。

原理：

```
H(M) 的最終 state = compression chain 的結尾
這個 state 等同「給定 H(M) 為 IV」 → 繼續 hash 任何 M' 都能算
```

實作步驟：

```
1. attacker 知道 H(secret || message) 與 |secret || message| 的長度
2. 構造 H 的 padding（"glue padding"）
3. 把 padding + extension 接在 M 後面
4. 用 H(secret || message) 當 IV，hash extension
5. 結果 = H(secret || message || padding || extension)
```

**這是 Ch 15 length extension attack 的核心**。Ch 15 會展開實作。

對應的後果：**Merkle-Damgård 結構的 hash 不能直接當 MAC**：

```c
// 危險：直接用 SHA-256 當 MAC
mac = SHA256(secret || message)
// attacker 拿 mac 後能算 SHA256(secret || message || extension)
// 等同偽造一條訊息的 MAC
```

正確做法：用 **HMAC**（Ch 16 詳述）— **HMAC 是為了補這個漏洞而生**。

## Compression function 的設計

compression function 自己也是 mini block cipher 的概念：

```
input: (state, message_block) — 共幾百 bit
output: new_state — 同 state size

設計要求：
  抗碰撞：找 (s1, m1) ≠ (s2, m2) 使 f(s1, m1) = f(s2, m2) 困難
  抗原像
  非線性
```

SHA-2 家族的 f 是個**單向 mini cipher**（用 message block 做 key、state 做 plaintext，加密一輪 = compression 一輪）。Davies-Meyer 構造。

## SHA-3 / Keccak：sponge 構造

SHA-2 仍 Merkle-Damgård。**SHA-3** 用完全不同的 **sponge construction**：

```
       absorb phase                  squeeze phase
      ┌──────────────────┐          ┌──────────────┐
M_1   M_2   M_3   M_4              output_1  output_2
 │     │     │     │                 │         │
 ▼     ▼     ▼     ▼                 │         │
[r] ► [r] ► [r] ► [r]               [r]   ►   [r]  ► ...
[c] ► [c] ► [c] ► [c]               [c]   ►   [c]
 │     │     │     │                 ▲         ▲
 │     │     │     │                 │         │
 └─►f──┴─►f──┴─►f──┴─►f─►f─►f...─►f──┴────────f┘
```

把 message block XOR 進 state 的 **rate** 部分（前 r bit），剩下 **capacity** 部分（c bit）不直接受影響但混合。

**好處**：

- **沒 length extension**：sponge 結構天生抗 extension
- **可變 output size**：squeeze 多次得到任意長 output
- **設計優雅**：理論證明更乾淨

Keccak 2008 由 Bertoni 等設計，2012 NIST SHA-3 競賽勝出，2015 標準化（FIPS 202）。下章詳述。

## Hash 的應用

不只是「給字串個指紋」。Hash 在密碼學處處可見：

| 應用 | 例子 |
|---|---|
| **完整性驗證** | 軟體下載 SHA-256 比對 |
| **MAC 構造** | HMAC、KMAC |
| **數位簽章** | 簽 hash 而非整訊息 |
| **密碼儲存** | bcrypt、Argon2 |
| **隨機 oracle** | 簽章 / KEM 安全證明 |
| **Merkle tree** | Bitcoin、Git、IPFS |
| **commitment** | Pedersen / 簡單 hash commitment |
| **PRG / KDF** | HKDF 用 HMAC-SHA256 當 PRF |

## 一個常見誤解

「MD5 已經死了，但 SHA-256 跟 SHA-1 一樣可能哪天被破」

**SHA-256 比 SHA-1 強得多，且機制上不會「同樣」被破**：

- **MD5**：differential attack 1996 起累積，2004 production attack
- **SHA-1**：類似 differential，2005 學術 attack，2017 Google 找到 production collision
- **SHA-256**：完全不同 design margin（更多輪、更大 state），無已知接近 break 的攻擊

但 **SHA-256 的 Merkle-Damgård 結構天生有 length extension** — 這不是「被破」，是設計特性。要避免靠 HMAC 或換 SHA-3。

## 自我檢核

- [ ] 我能說出 hash 三種安全屬性的定義
- [ ] 我能解釋為什麼 collision 比 preimage 容易（生日攻擊）
- [ ] 我能畫出 Merkle-Damgård 構造
- [ ] 我能解釋 length extension attack 為什麼 Merkle-Damgård 天生有
- [ ] 我能說出 sponge 構造為什麼沒這個問題
- [ ] 我能列出 hash 的至少 5 種應用

下一章看 SHA 家族細節：SHA-1 SHAttered 故事、SHA-2 三變體、SHA-3 sponge。

→ [Ch 14 SHA 家族](./14-sha-family.md)
