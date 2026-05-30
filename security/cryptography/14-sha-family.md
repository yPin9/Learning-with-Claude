# Ch 14 — SHA 家族（深挖章）

> 目標：能比較 SHA-1 / SHA-2 / SHA-3 的結構差異，理解 SHA-1 碰撞的 SHAttered 攻擊原理，解釋 SHA-3 的 sponge 構造為什麼天生沒有 length extension 問題。

---

## 為什麼需要理解 SHA 家族的演化

你在 `hashlib` 裡看到 `sha1`、`sha256`、`sha384`、`sha512`、`sha3_256`，它們不是同一個演算法加不同參數——它們是三代完全不同的設計，背後的構造、安全假設、弱點都不一樣。

- SHA-1：已死。2017 年被 Google/CWI 實際碰撞攻破
- SHA-2：現役主力，但繼承了 Merkle-Damgård 的 length extension 弱點
- SHA-3：NIST 2015 年的新標準，用 sponge 構造取代 M-D，天生免疫 length extension

理解它們的差異，你才能在設計系統時做出正確選擇，而不是隨便挑一個「看起來數字最大的」。

---

## 先建立直覺：三代 SHA 的一句話總結

```
SHA-1  (1995) ── Merkle-Damgård, 160-bit ── 已被碰撞攻破 ── 不要用
SHA-2  (2001) ── Merkle-Damgård, 256/384/512-bit ── 安全但有 length extension ── 主力
SHA-3  (2015) ── Sponge (Keccak), 可變長度 ── 安全且無 length extension ── 備選
```

---

## SHA-1：160-bit 的 Merkle-Damgård

### 結構

SHA-1 是標準的 Merkle-Damgård 構造（Ch 13）：

- **Block 大小**：512 bit
- **State 大小**：160 bit（5 個 32-bit word：A, B, C, D, E）
- **Round 數**：80
- **壓縮函式**：每 round 用一個非線性函式 f(B, C, D)，分四段各 20 rounds，每段用不同的 f 和常數 K

```
                  512-bit message block
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
IV ──→[ Round 0-19 ]──→[ Round 20-39 ]──→[ Round 40-59 ]──→[ Round 60-79 ]──→ H
       f = Ch(B,C,D)    f = Parity       f = Maj(B,C,D)    f = Parity
       K = 0x5A827999    K = 0x6ED9EBA1   K = 0x8F1BBCDC    K = 0xCA62C1D6
```

### 為什麼 SHA-1 被打破

SHA-1 的碰撞理論攻擊在 2005 年由 Xiaoyun Wang 團隊提出（複雜度 2⁶⁹，低於 birthday bound 的 2⁸⁰）。但理論歸理論，真正產出碰撞是另一回事。

### SHAttered Attack（2017）

Google 和 CWI Amsterdam 合作，花了 6500 CPU-year 和 110 GPU-year，產出了兩個不同的 PDF 檔案，SHA-1 hash 完全相同：

```
SHA-1(shattered-1.pdf) = 38762cf7f55934b34d179ae6a4c80cadccbb7f0a
SHA-1(shattered-2.pdf) = 38762cf7f55934b34d179ae6a4c80cadccbb7f0a
```

兩個 PDF 打開來看到不同的圖片（一個藍色背景、一個紅色背景），但 SHA-1 hash 一模一樣。

**攻擊的核心思路**：

1. 攻擊者不需要讓兩個完全隨機的檔案碰撞——那太難了
2. SHAttered 使用 **chosen-prefix collision**：精心構造兩個 message block，讓 SHA-1 處理完這兩個 block 之後 internal state 相同
3. 從那之後，兩個檔案的後半段可以完全一樣，hash 自然相同
4. PDF 的格式允許在「碰撞區域」之外控制顯示內容，所以兩個 PDF 看起來不同

**複雜度**：~2⁶³.¹ SHA-1 運算，遠低於 brute-force 的 2⁸⁰。

```python
import hashlib

# 驗證 SHAttered（如果你有那兩個 PDF）
# 可以從 https://shattered.io/ 下載
# with open("shattered-1.pdf", "rb") as f:
#     print(hashlib.sha1(f.read()).hexdigest())
# with open("shattered-2.pdf", "rb") as f:
#     print(hashlib.sha1(f.read()).hexdigest())
# 兩者相同！

# 用 Python 驗證 SHA-1 和 SHA-256 的差異
msg = b"SHAttered showed SHA-1 is broken"
print(f"SHA-1:   {hashlib.sha1(msg).hexdigest()}")
print(f"SHA-256: {hashlib.sha256(msg).hexdigest()}")
```

### 現實影響

- **Git**：Git 的 commit hash 用 SHA-1。SHAttered 之後 Git 加了偵測機制（`sha1collisiondetection`），但遷移到 SHA-256 的計畫已經在進行
- **TLS 憑證**：2017 年之前有些 CA 還在用 SHA-1 簽 TLS 憑證。SHAttered 之後瀏覽器全面拒絕 SHA-1 憑證
- **文件簽章**：任何用 SHA-1 做完整性驗證的系統都有被攻擊的風險

---

## SHA-2 家族：SHA-256 / SHA-384 / SHA-512

### 結構

SHA-2 仍然是 Merkle-Damgård 構造，但壓縮函式設計更強。SHA-2 不是一個演算法，而是一個家族：

| 變體 | 輸出 | Block | State | Word | Rounds | 安全等級（碰撞）|
|------|------|-------|-------|------|--------|-----------------|
| SHA-224 | 224 bit | 512 bit | 256 bit | 32-bit | 64 | 2¹¹² |
| SHA-256 | 256 bit | 512 bit | 256 bit | 32-bit | 64 | 2¹²⁸ |
| SHA-384 | 384 bit | 1024 bit | 512 bit | 64-bit | 80 | 2¹⁹² |
| SHA-512 | 512 bit | 1024 bit | 512 bit | 64-bit | 80 | 2²⁵⁶ |
| SHA-512/256 | 256 bit | 1024 bit | 512 bit | 64-bit | 80 | 2¹²⁸ |

SHA-224 和 SHA-384 分別是 SHA-256 和 SHA-512 的截斷版本（使用不同的 IV）。

SHA-512/256 是在 64-bit 機器上比 SHA-256 更快的替代——它用 SHA-512 的 64-bit 運算，但輸出截斷到 256 bit。

### SHA-256 壓縮函式詳解

SHA-256 的每一 round 做以下運算（8 個 32-bit state word：a, b, c, d, e, f, g, h）：

```
          a    b    c    d    e    f    g    h
          │    │    │    │    │    │    │    │
          │    │    │    │    │    │    │    │
          ▼    │    │    │    ▼    │    │    │
        Σ₀(a)  │    │    │  Σ₁(e)  │    │    │
          │    │    │    │    │    │    │    │
          ▼    ▼    ▼    │    ▼    ▼    ▼    │
       Maj(a,b,c)  │    │  Ch(e,f,g)   │    │
          │        │    │    │         │    │
          │        │    │    ▼         │    ▼
          │        │    │   (+)◄── Kᵢ  │   (+)◄── Kᵢ
          │        │    │    │         │    │
          │        │    │    ▼         │    ▼
          │        │    │   (+)◄── Wᵢ  │   (+)◄── Wᵢ
          │        │    │    │         │    │
          │        │    │    ▼         │    │
          │        │    │   (+)◄── h   │    │
          │        │    │    │         │    │
          ▼        │    │    ▼         │    │
         (+)───────┘    │   T₁        │    │
          │             │    │         │    │
          │             │    ├────────▶(+)   │
          │             │    │         │    │
          ▼             ▼    │         ▼    ▼
        a'=T₁+T₂ b'=a  c'=b  d'=c  e'=d+T₁ f'=e  g'=f  h'=g

其中：
  Ch(e, f, g)  = (e AND f) XOR (NOT e AND g)    選擇函式
  Maj(a, b, c) = (a AND b) XOR (a AND c) XOR (b AND c)  多數函式
  Σ₀(a) = ROTR²(a) XOR ROTR¹³(a) XOR ROTR²²(a)
  Σ₁(e) = ROTR⁶(e) XOR ROTR¹¹(e) XOR ROTR²⁵(e)
  Kᵢ = 64 個常數（前 64 個質數立方根的小數部分）
  Wᵢ = message schedule（從 16-word block 擴展成 64 words）
```

### Message Schedule

SHA-256 的 message block 是 16 個 32-bit word（W₀ 到 W₁₅），但有 64 rounds，所以需要擴展：

```
W₀ ... W₁₅ ← 直接從 message block 取
Wᵢ = σ₁(Wᵢ₋₂) + Wᵢ₋₇ + σ₀(Wᵢ₋₁₅) + Wᵢ₋₁₆    (i = 16..63)

σ₀(x) = ROTR⁷(x) XOR ROTR¹⁸(x) XOR SHR³(x)
σ₁(x) = ROTR¹⁷(x) XOR ROTR¹⁹(x) XOR SHR¹⁰(x)
```

### 範例一：SHA-256 逐步觀察

```python
import hashlib
import struct

def sha256_state_after_first_block(msg_block: bytes) -> list[int]:
    """
    觀察 SHA-256 處理第一個 block 後的 internal state。
    注意：hashlib 不暴露 internal state，
    所以我們用一個 trick：如果輸入剛好是一個 block + padding，
    最終 hash 就是第一個 block 的 state。
    """
    # SHA-256 IV（前 8 個質數平方根的小數部分）
    IV = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ]
    print("SHA-256 IV:")
    for i, v in enumerate(IV):
        print(f"  H[{i}] = {v:#010x}")
    return IV

# 觀察不同 SHA-2 變體
msgs = [b"The quick brown fox jumps over the lazy dog"]
for msg in msgs:
    print(f"\n輸入: {msg.decode()}")
    print(f"SHA-224:     {hashlib.sha224(msg).hexdigest()}")
    print(f"SHA-256:     {hashlib.sha256(msg).hexdigest()}")
    print(f"SHA-384:     {hashlib.sha384(msg).hexdigest()}")
    print(f"SHA-512:     {hashlib.sha512(msg).hexdigest()}")
    print(f"SHA-512/256: {hashlib.new('sha512_256', msg).hexdigest()}")

sha256_state_after_first_block(b"")
```

### SHA-2 的安全現況

截至 2026 年，SHA-2 家族沒有任何實際的碰撞攻擊。對 SHA-256 最好的理論攻擊能打到 31 rounds（共 64 rounds），離全部 rounds 還很遠。SHA-2 在可預見的未來仍然安全。

但 SHA-2 仍然是 Merkle-Damgård——仍有 length extension 弱點。

---

## SHA-3 / Keccak：Sponge 構造

### 背景

2007 年，NIST 意識到 SHA-1 已經不安全，而 SHA-2 和 SHA-1 共享相似的構造（都是 M-D），萬一 M-D 被發現結構性弱點，SHA-2 也會連帶受影響。NIST 發起了 SHA-3 競賽，要求一個**結構完全不同**的 hash 函式。

2012 年，Keccak（由 Guido Bertoni、Joan Daemen、Michaël Peeters、Gilles Van Assche 設計）贏得競賽，成為 SHA-3（FIPS 202, 2015）。

Keccak 用的不是 Merkle-Damgård，而是 **sponge 構造**。

### Sponge 構造

Sponge 的核心思想：維護一個大的 internal state，分成兩部分——rate（和外界交換資料的部分）和 capacity（隱藏的部分，不直接暴露）。

```
                         ┌─── rate (r) ───┐┌── capacity (c) ──┐
                         │                 ││                   │
State (b = r + c bits):  │   r bits        ││    c bits         │
                         │   (公開交換)     ││    (隱藏保護)      │
                         └─────────────────┘└───────────────────┘

Keccak 的 state 大小：b = 1600 bits
SHA3-256: r = 1088, c = 512   → 安全等級 c/2 = 256 bit（碰撞 128 bit）
SHA3-512: r = 576,  c = 1024  → 安全等級 c/2 = 512 bit（碰撞 256 bit）
```

### 兩個階段：Absorb 和 Squeeze

```
      輸入訊息（切成 r-bit blocks）
         │
         ▼
    ┌─────────┐
    │ 初始     │
    │ state=0  │  1600 bits，全零
    └────┬────┘
         │
    ═════╪══════════════════ Absorb Phase ═════════════════════
         │
         ▼
    ┌─────────────────────┐
    │ XOR m₁ into rate    │  只 XOR 前 r bits
    │ ┌──────┬──────────┐ │
    │ │ rate │ capacity │ │
    │ │⊕ m₁  │(不動)    │ │
    │ └──────┴──────────┘ │
    └────────┬────────────┘
             │
             ▼
    ┌─────────────────────┐
    │    f (Keccak-f)     │  置換函式，攪拌整個 1600-bit state
    │    24 rounds        │
    └────────┬────────────┘
             │
             ▼
    ┌─────────────────────┐
    │ XOR m₂ into rate    │  第二個 block
    │ ┌──────┬──────────┐ │
    │ │ rate │ capacity │ │
    │ │⊕ m₂  │(不動)    │ │
    │ └──────┴──────────┘ │
    └────────┬────────────┘
             │
             ▼
    ┌─────────────────────┐
    │    f (Keccak-f)     │
    └────────┬────────────┘
             │
        ... 重複直到所有 block 吸收完 ...
             │
    ═════════╪══════════════ Squeeze Phase ════════════════════
             │
             ▼
    ┌─────────────────────┐
    │ 輸出 rate 的前 d bits │  d = 所需的 digest 長度
    │ ┌──────┬──────────┐ │
    │ │ rate │ capacity │ │
    │ │→out  │(不輸出)  │ │
    │ └──────┴──────────┘ │
    └─────────────────────┘
    
    如果 d > r，再做一次 f，再輸出 r bits，
    重複直到湊滿 d bits（這就是 XOF 的基礎）。
```

### 為什麼 Sponge 沒有 Length Extension

回顧 Merkle-Damgård 的問題：最終 hash 值就是 internal state 的全部。攻擊者拿到 hash 就拿到了 state，可以從那裡繼續。

Sponge 不同：

1. **hash 值只是 state 的一部分（rate）**。capacity 的 c bits 永遠不會直接輸出
2. 攻擊者拿到 hash（rate 的前 d bits），但不知道 capacity 的值
3. 沒有完整的 state，就無法繼續 hash 更多資料

```
Merkle-Damgård:  hash = 完整 internal state  → 攻擊者有完整 state → 能繼續
Sponge:          hash = rate 的一部分         → 攻擊者缺 capacity → 不能繼續
```

這就是 sponge 天生免疫 length extension 的原因。不需要像 HMAC 那樣加額外結構來防禦。

### 範例二：Python 中的 SHA-3 和 SHAKE

```python
import hashlib

msg = b"Keccak won the SHA-3 competition"

# SHA-3 固定長度輸出
print(f"SHA3-256: {hashlib.sha3_256(msg).hexdigest()}")
print(f"SHA3-512: {hashlib.sha3_512(msg).hexdigest()}")

# SHAKE：可變長度輸出（XOF = eXtendable Output Function）
# SHAKE128 和 SHAKE256 可以輸出任意長度
shake = hashlib.shake_256(msg)
print(f"SHAKE256 (32 bytes): {shake.hexdigest(32)}")
print(f"SHAKE256 (64 bytes): {shake.hexdigest(64)}")
print(f"SHAKE256 (128 bytes): {shake.hexdigest(128)}")

# 前 32 bytes 是一樣的——SHAKE 的輸出是前綴一致的
assert shake.hexdigest(64)[:64] == shake.hexdigest(32)
```

### XOF（eXtendable Output Function）

SHAKE128 和 SHAKE256 是 sponge 構造的自然延伸——因為 squeeze phase 可以無限繼續（每次 squeeze 出 r bits），所以 sponge 天生支援可變長度輸出。

M-D 構造做不到——它的 output 就是 state，state 是固定大小。

XOF 的用途：

- **KDF**：從一個 seed 衍生任意長度的 key material
- **確定性隨機**：需要任意長度偽隨機 bytes 的場景
- **Kyber / Dilithium 的內部構造**：Post-Quantum 演算法大量使用 SHAKE 做內部的隨機展開

---

## Keccak-f 置換函式

Keccak 的 state 被組織成一個 5×5×64 的三維 bit array（5×5 lanes，每 lane 64 bits = 1600 bits 總共）。

每一 round 做 5 個步驟（θ, ρ, π, χ, ι）：

| 步驟 | 作用 | 密碼學目的 |
|------|------|-----------|
| θ (theta) | 每個 bit XOR 相鄰兩行的 parity | Diffusion（擴散） |
| ρ (rho) | 每個 lane 做不同量的旋轉 | Diffusion |
| π (pi) | 把 lanes 重新排列（permutation） | Diffusion |
| χ (chi) | 非線性運算：每 row 做 `a ⊕ (¬b ∧ c)` | Confusion（混淆）——唯一的非線性步驟 |
| ι (iota) | XOR 一個 round 常數 | 打破對稱性 |

24 rounds 的 Keccak-f[1600] 提供了充分的 diffusion 和 confusion。

---

## 三代 SHA 完整對比

| 維度 | SHA-1 | SHA-256 | SHA-512 | SHA3-256 | SHAKE256 |
|------|-------|---------|---------|----------|----------|
| 年份 | 1995 | 2001 | 2001 | 2015 | 2015 |
| 構造 | Merkle-Damgård | Merkle-Damgård | Merkle-Damgård | Sponge | Sponge |
| 輸出 | 160 bit | 256 bit | 512 bit | 256 bit | 可變 |
| Block | 512 bit | 512 bit | 1024 bit | 1088 bit (rate) | 1088 bit (rate) |
| Rounds | 80 | 64 | 80 | 24 | 24 |
| 碰撞安全 | 已破 (2⁶³) | 2¹²⁸ | 2²⁵⁶ | 2¹²⁸ | 取決於輸出長度 |
| Length Extension | 有 | 有 | 有 | 無 | 無 |
| XOF 支援 | 無 | 無 | 無 | 無 | 有 |
| 64-bit 效能 | 中 | 中 | 快 | 中 | 中 |
| 硬體加速 | 少 | SHA-NI（Intel/AMD）| 少 | 少 | 少 |

### 該選哪個？

```
需要 NIST 標準 + 最廣泛相容 → SHA-256
需要高安全等級（>128-bit 碰撞）→ SHA-512 或 SHA3-512
需要避免 length extension → SHA-3
需要可變長度輸出（XOF） → SHAKE256
需要最快速度（非 NIST）→ BLAKE3
跑在 64-bit CPU 且不需 NIST → SHA-512（比 SHA-256 快，因為用 64-bit 運算）
```

---

## 踩雷集錦

### 踩雷 1：認為 SHA-256 比 SHA-512 安全

在 64-bit 系統上 SHA-512 比 SHA-256 快（每 round 做 64-bit 運算，pipeline 更滿），而且安全等級更高（256-bit 碰撞抗性 vs 128-bit）。如果你的系統沒有 SHA-NI 硬體加速，SHA-512/256 是 256-bit 輸出中最快的 NIST 選項。

### 踩雷 2：以為 SHA-3 很慢所以不用

SHA-3 在純軟體實作中比 SHA-256 慢，這是事實。但差距不像很多人想的那麼大——在有些平台上差距不到 2x。如果你的瓶頸不在 hashing（大多數應用都是如此），SHA-3 的 length extension 免疫性和結構多樣性（不跟 SHA-2 共享弱點）是值得的。

### 踩雷 3：用 SHA-3 取代 HMAC-SHA-256

SHA-3 雖然沒有 length extension，但「直接用 `SHA3(key || msg)` 做 MAC」仍然不是最佳實踐。NIST 推薦的做法是用 KMAC（Keccak-based MAC，NIST SP 800-185），它基於 cSHAKE 並正式處理了 domain separation。

### 踩雷 4：混淆 SHA-2 家族的成員

SHA-224 不是「SHA-256 截斷到 224 bit」那麼隨意——它用不同的 IV。同理，SHA-384 用不同的 IV 於 SHA-512。這確保截斷版和完整版的 digest 不會有前綴關係。

### 踩雷 5：忽略 Git 的 SHA-1 問題

Git 到 2024 年仍然預設用 SHA-1（雖然有 SHA-256 模式的實驗支援）。SHAttered 之後 Git 加入了 `sha1collisiondetection` 來偵測已知的碰撞模式，但這不能防禦未來的新碰撞方法。如果你的安全模型依賴 Git hash 的完整性，要注意這個風險。

---

## 進階

### SHA-256 的 Length Extension 實際狀況

SHA-256 的 hash 值就是 8 個 32-bit state words 直接輸出。攻擊者拿到 `SHA-256(M)` 就拿到了：

```python
# SHA-256 hash = H[0] || H[1] || ... || H[7]
# 每個 H[i] 是壓縮函式最後的 32-bit state word
# 攻擊者把這 8 個 word 設為新的 "IV"，繼續 hash 更多 blocks
```

Ch 15 會完整實作這個攻擊。

### SHA-512/256 和 SHA-512/224

NIST 在 FIPS 180-4 中加入了 SHA-512/t：用 SHA-512 的運算但截斷輸出。好處是在 64-bit 機器上比 SHA-256 快，同時用不同 IV 確保安全性。SHA-512/256 適合需要 256-bit output 但跑在 64-bit 平台的場景。

### TupleHash 和 cSHAKE

NIST SP 800-185 定義了基於 Keccak 的衍生函式：

- **cSHAKE**：customizable SHAKE——加入 function name 和 customization string，做 domain separation
- **TupleHash**：對多個字串做 hash，而且保證不同的切割方式不會碰撞
- **ParallelHash**：支援平行化的 hash

這些都是 sponge 構造的自然擴展，M-D 構造做不到。

### BLAKE3 值得一提

BLAKE3 不是 SHA 家族，但越來越流行。它用 Merkle tree 結構支援無限平行化，在現代 CPU 上速度可以超過 SHA-256 的 10 倍。缺點是沒有 NIST 標準化。Rust 生態的 `b3sum` 工具用的就是 BLAKE3。

---

## 動手練習

1. **SHA-2 家族比較**：對同一個 1 MB 的隨機資料，分別用 SHA-224、SHA-256、SHA-384、SHA-512、SHA-512/256 計算 hash，測量各自的速度，在 64-bit 機器上驗證 SHA-512 比 SHA-256 快

2. **SHAKE 的 XOF 性質**：用 `hashlib.shake_256`，對同一輸入分別輸出 16, 32, 64, 128, 256 bytes，驗證短輸出是長輸出的 prefix

3. **SHAttered 驗證**：從 https://shattered.io/ 下載那兩個 PDF，分別算 SHA-1 和 SHA-256，驗證 SHA-1 相同但 SHA-256 不同

4. **（挑戰）Keccak round 函式**：實作 Keccak-f[1600] 的 θ 步驟（column parity + XOR）。提示：state 用 5×5 的 64-bit int 陣列表示

---

## 重點整理

1. **SHA-1 已死**：SHAttered（2017）以 2⁶³ 的複雜度產出了真實碰撞。任何安全用途都不該用 SHA-1
2. **SHA-2 是現役主力**：SHA-256 最廣泛，SHA-512 在 64-bit 機器上更快。但 SHA-2 是 M-D 構造，有 length extension 弱點
3. **SHA-3 用 sponge 構造**：state 分 rate（公開）和 capacity（隱藏），hash 只輸出 rate 的一部分，所以天生沒有 length extension
4. **Sponge 天生支援 XOF**：SHAKE128/256 能輸出任意長度，M-D 做不到
5. **選擇指引**：一般用 SHA-256；需要避免 length extension 用 SHA-3；需要速度用 BLAKE3（非 NIST）；做 MAC 用 HMAC 或 KMAC，不要裸 hash

---

## 自我檢核

1. SHA-1 的 SHAttered 攻擊複雜度是多少？birthday bound 是多少？它打破了哪個安全屬性？
2. SHA-256 和 SHA-512 在結構上有什麼差異？為什麼 SHA-512 在 64-bit 機器上更快？
3. Sponge 構造的 rate 和 capacity 分別是什麼？為什麼 capacity 越大越安全？
4. 為什麼 sponge 沒有 length extension 問題？用一句話解釋
5. SHAKE256 和 SHA3-256 的差別是什麼？SHAKE 能做而 SHA-3 不能做的事是什麼？

---

## 延伸閱讀

- **SHAttered 官方網站**：https://shattered.io/ ——下載碰撞 PDF 和技術論文
- **FIPS 180-4**：SHA-1 和 SHA-2 的完整規格
- **FIPS 202**：SHA-3 和 SHAKE 的完整規格
- **NIST SP 800-185**：cSHAKE、KMAC、TupleHash、ParallelHash 的規格
- **Keccak Team 網站**：https://keccak.team/ ——設計者的官方資源
- **Serious Cryptography, Ch 6**：SHA-2 和 SHA-3 的工程導向比較

---

## 下一章預告

[Ch 15 — Length Extension Attack](./15-length-extension-attack.md)：實作 length extension attack——你會親手對 SHA-256 跑這個攻擊，理解為什麼 `H(secret || message)` 不能當 MAC。
