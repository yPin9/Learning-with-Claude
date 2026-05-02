# Ch 14 — SHA 家族：SHA-1 collision、SHA-2、SHA-3 sponge

> 目標：看 SHA-1 → SHA-2 → SHA-3 三代演化。SHA-1 SHAttered 故事（Google 2017 兩個 PDF 同 hash）、SHA-2 為什麼還能撐、SHA-3 (Keccak) 用 sponge 而非 Merkle-Damgård 的設計勝利。

## SHA 家族簡史

```
1993  SHA-0（NIST 發了 90 天就撤回，未公開原因）
1995  SHA-1（修正版，160-bit）
2002  SHA-2 family：SHA-224 / 256 / 384 / 512
2008  SHA-3 競賽公告（NIST 競賽）
2012  Keccak 勝出
2015  SHA-3 標準化（FIPS 202）
2017  SHA-1 collision（Google SHAttered，2 個 PDF）
```

SHA = Secure Hash Algorithm，NIST 標準系列。

## SHA-1：160-bit Merkle-Damgård

NSA 設計，1995 標準化。內部結構：

```
state = 5 × 32-bit word (a, b, c, d, e) = 160 bit

每 block 處理 (512-bit input)：
  把 16 個 32-bit word 擴展成 80 個 word
  跑 80 輪 round function
  每輪用一個 word 與 state 混合

最終 hash = 5 個 state word 串起來 = 160 bit
```

Round function：

```
for t in 0..79:
    if t < 20:
        f = (b AND c) OR ((NOT b) AND d)
        K = 0x5A827999
    elif t < 40:
        f = b XOR c XOR d
        K = 0x6ED9EBA1
    elif t < 60:
        f = (b AND c) OR (b AND d) OR (c AND d)
        K = 0x8F1BBCDC
    else:
        f = b XOR c XOR d
        K = 0xCA62C1D6
    
    new_a = rotl(a, 5) + f + e + K + W[t]
    e = d
    d = c
    c = rotl(b, 30)
    b = a
    a = new_a
```

完整實作 < 100 行 C。

## SHA-1 的死亡之路

```
1998  SHA-0 被找到 collision pattern
2005  Wang / Yin / Yu paper：SHA-1 collision in 2^69（理論）
2009  McDonald / Hawkes / Pieprzyk：2^57.5（理論）
2015  Stevens et al "freestart collision"
2017  Google "SHAttered"：兩個 PDF 同 SHA-1
        實際成本：6610 CPU-year + 110 GPU-year（約 11 萬美元）
2020  CWI / INRIA "SHA-1 is a Shambles"
        chosen-prefix collision，4.5 萬美元
```

**SHAttered 兩個 PDF**：相同 SHA-1，內容明顯不同（一張紅色，一張藍色封面）。Google 故意公開 demo，逼產業 retire SHA-1。

實際後果：

- **Git**：仍用 SHA-1（但加 collision detection patch，看到 SHAttered 模式直接拒絕）
- **TLS**：早就棄用 SHA-1 簽章
- **CA 證書**：2017 起拒絕簽 SHA-1 證書

## SHA-2：256/384/512

NSA 2001 設計。**結構類似 SHA-1 但變數加大**：

| | SHA-256 | SHA-512 |
|---|---|---|
| Output | 256 bit | 512 bit |
| State | 8 × 32-bit | 8 × 64-bit |
| Block | 512 bit | 1024 bit |
| Rounds | 64 | 80 |
| 字常數 | 64 個 | 80 個 |

**SHA-224 / SHA-384** = SHA-256 / SHA-512 的 truncated 版本（只取前 224/384 bit）。

**SHA-512/256** = SHA-512 但 truncate 到 256 bit、用不同 IV — 64-bit CPU 上比 SHA-256 還快。

### SHA-256 round function

```python
def sigma0(x): return rotr(x, 7) ^ rotr(x, 18) ^ (x >> 3)
def sigma1(x): return rotr(x, 17) ^ rotr(x, 19) ^ (x >> 10)
def Sigma0(x): return rotr(x, 2) ^ rotr(x, 13) ^ rotr(x, 22)
def Sigma1(x): return rotr(x, 6) ^ rotr(x, 11) ^ rotr(x, 25)
def Ch(x, y, z): return (x & y) ^ (~x & z)
def Maj(x, y, z): return (x & y) ^ (x & z) ^ (y & z)

def sha256_round(state, K_t, W_t):
    a, b, c, d, e, f, g, h = state
    T1 = (h + Sigma1(e) + Ch(e, f, g) + K_t + W_t) & 0xFFFFFFFF
    T2 = (Sigma0(a) + Maj(a, b, c)) & 0xFFFFFFFF
    return [
        (T1 + T2) & 0xFFFFFFFF,  # new a
        a, b, c,
        (d + T1) & 0xFFFFFFFF,   # new e
        e, f, g
    ]
```

跑 64 輪後 state += original state（feedforward）。

完整 SHA-256 約 200 行 Python，練習 B 會手刻。

### SHA-2 的安全狀態

**目前沒有已知接近 break 的攻擊**：

- 最佳 collision attack 對 reduced-round（< 31 / 64 輪）SHA-256 才有效
- preimage 對 reduced-round（< 45 / 64 輪）有效
- **完整 SHA-256 仍 brute-force 等級**

預期 SHA-256 至少還能撐 20 年（除非量子或新攻擊出現）。

## SHA-3 / Keccak：完全不同的設計

NIST 2007 開競賽（怕 SHA-2 哪天被破，提前準備）。2012 Keccak 勝出。

設計者：Guido Bertoni、Joan Daemen（AES 共設計）、Michaël Peeters、Gilles Van Assche。

**Keccak 用 sponge 構造**（Ch 13 介紹過骨架），permutation function 叫 Keccak-f[1600]：

```
state = 1600 bit = 5 × 5 × 64

24 個 round，每輪 5 個 step：
  θ (theta)  - column parity mixing
  ρ (rho)    - rotation
  π (pi)     - lane permutation
  χ (chi)    - non-linear (唯一非線性)
  ι (iota)   - round constant
```

state 視為 5×5×64 三維陣列。每個 step 對 state 做變換。

整體：

```
absorb(message):
    for each block:
        state[:r] ^= block
        state = Keccak-f(state)
squeeze():
    output = state[:r]
    state = Keccak-f(state)
    return output
```

`r = rate`，`c = capacity = 1600 - r`。SHA3-256 用 r = 1088, c = 512。

## SHA-3 vs SHA-2 對照

| | SHA-2 | SHA-3 |
|---|---|---|
| 結構 | Merkle-Damgård | Sponge |
| 安全證明 | 啟發式（heuristic） | 較嚴謹 |
| Length extension | 有（用 HMAC 補） | 沒有 |
| 軟體性能（CPU） | SHA-256 在 64-bit 快 | 略慢 SHA-256 |
| 硬體性能 | 中 | 強（FPGA / ASIC 友善） |
| 變體 | 224/256/384/512 | 224/256/384/512 + SHAKE |
| 標準化 | FIPS 180-4 | FIPS 202 |

**SHA-3 不是 SHA-2 的取代**。NIST 明確說 SHA-2 仍可用，SHA-3 是「**備胎 + 不同設計**」。實務多數系統仍用 SHA-256/SHA-512，因為性能好且生態成熟。

## SHAKE：可變長度 hash

SHA-3 標準還包括兩個 **可變長度 output** 的 function：

```
SHAKE128(message, output_length) → 任意長 output
SHAKE256(message, output_length) → 同
```

對應 sponge 的 squeeze 階段可重複輸出。

用途：

- **KDF 替代**：直接 SHAKE256(secret || info, length) 當 KDF
- **隨機 oracle 模擬**：多種協定（Kyber、Dilithium）需要任意長 hash
- **PQ 密碼學基礎**：post-quantum 算法重度依賴 SHAKE

## BLAKE / BLAKE2 / BLAKE3：另一條路

不是 NIST 標準但廣泛使用。BLAKE2（Aumasson 等 2012）特性：

- 比 SHA-2 快很多（軟體上）
- 有 BLAKE2b（64-bit 平台）、BLAKE2s（32-bit 與嵌入式）
- 內建 keyed-hash mode（不需 HMAC）
- libsodium、git LFS 等用

BLAKE3（2020）更快：

- Tree-based（平行加速）
- 單一 spec 適合所有 input size
- argon2 hash function 用變體

選擇：**有 SHA-256 hardware acceleration 用 SHA-256；CPU bound 純軟體用 BLAKE2/3**。

## 實務怎麼選

| 場景 | 選 |
|---|---|
| 一般 hash 需求 | **SHA-256**（最普及） |
| 64-bit CPU 大量資料 | SHA-512 / SHA-512/256 |
| 嵌入式 / 32-bit | SHA-256 或 BLAKE2s |
| 不想處理 length extension | SHA-3 / BLAKE2 / BLAKE3 |
| 需要可變長 output | SHAKE128/256 |
| 性能極致（軟體） | BLAKE3 |
| **不要用** | MD5、SHA-1 |

## 一個常見誤解

「SHA-256 是 SHA-2，所以也快被破」

**錯**。「SHA-2」是 family 名稱，包含 SHA-224/256/384/512。**沒人「破 SHA-2」**，attack 都針對特定 reduced-round 變體。

實際上 SHA-256 design margin 比 SHA-1 強很多（更多輪、更複雜 round function、更大 state）。**保守估計 SHA-256 至少安全到 2040**。

## 自我檢核

- [ ] 我能說出 SHA-1 / SHA-256 / SHA-3 結構差異
- [ ] 我能說出 SHAttered 是什麼以及它的成本
- [ ] 我能寫 SHA-256 的一個 round
- [ ] 我能解釋 sponge 構造為什麼沒 length extension
- [ ] 我能說出 SHAKE 是什麼以及它的用途
- [ ] 我能列出實務上 hash 選擇的 cheat sheet

下一章專門看 length extension attack 的完整實作。

→ [Ch 15 Length extension attack](./15-length-extension-attack.md)
