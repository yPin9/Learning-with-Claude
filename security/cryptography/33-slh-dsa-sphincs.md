# Ch 33 — SLH-DSA (SPHINCS+)：Hash-Based 簽章與 Hyper-Tree

> **目標**：理解 hash-based 簽章為什麼能提供最保守的長期安全保證，能解釋從 Lamport → WOTS+ → XMSS → SPHINCS+ 的演進邏輯，掌握 SPHINCS+ 的 hyper-tree 結構，知道它為什麼是 NIST 的 backup standard。

## 為什麼需要這個？

Ch 31–32 講了 ML-KEM 和 ML-DSA，兩者都基於 Module-LWE。NIST 把它們選為主力標準。但 NIST 同時選了一個 **backup standard**：SLH-DSA（前身 SPHINCS+，FIPS 205）。

為什麼需要 backup？

**因為 lattice 的安全假設可能被打破。**

Module-LWE 的困難性假設只有 ~20 年的歷史。如果未來有人發現 lattice 問題的高效演算法（不管是經典的還是量子的），ML-KEM 和 ML-DSA 全部失效。NIST 需要一個不依賴 lattice 的方案作為保險。

SLH-DSA 的安全性只依賴 **hash function 的安全性**——這是密碼學中最保守、最久經考驗的假設。如果 SHA-256 / SHAKE 安全，SLH-DSA 就安全。不需要 lattice、不需要 factoring、不需要離散對數。

代價：簽章很大（最多 ~49 KB）。但安全性的確定性無人能比。

## 先建立直覺

Hash-based 簽章的核心觀察：

```
如果你能安全地「用 hash 簽一次」→
  你能用 Merkle tree 把「簽一次」擴展成「簽多次」→
    你能用 hyper-tree 把「簽多次」擴展成「簽非常多次」

one-time → few-time → many-time → stateless many-time
Lamport  → WOTS+    → XMSS     → SPHINCS+
```

## 核心概念：從 Lamport 到 SPHINCS+

### Step 1：Lamport Signature（one-time）

最簡樸的數位簽章——Leslie Lamport（1979）。

```
KeyGen：
  生成 2 × 256 個隨機 256-bit 值（分成兩列）
  
  sk[0][0], sk[0][1]    ← 第 0 bit 的兩個秘密
  sk[1][0], sk[1][1]    ← 第 1 bit 的兩個秘密
  ...
  sk[255][0], sk[255][1] ← 第 255 bit 的兩個秘密
  
  pk[i][j] = H(sk[i][j])   對每個秘密做 hash
  
  公鑰 = 所有 pk[i][j]（512 個 hash 值）
  私鑰 = 所有 sk[i][j]（512 個隨機值）

Sign（簽 256-bit message digest d）：
  對 d 的每個 bit i：
    如果 d[i] = 0 → 公開 sk[i][0]
    如果 d[i] = 1 → 公開 sk[i][1]
  
  簽章 = 256 個秘密值

Verify：
  對 d 的每個 bit i：
    如果 d[i] = 0 → 檢查 H(sig[i]) == pk[i][0]
    如果 d[i] = 1 → 檢查 H(sig[i]) == pk[i][1]
```

```
範例（極簡化，4-bit message）：

  sk =  [  [a₀, b₀],      pk =  [  [H(a₀), H(b₀)],
           [a₁, b₁],                [H(a₁), H(b₁)],
           [a₂, b₂],                [H(a₂), H(b₂)],
           [a₃, b₃]  ]              [H(a₃), H(b₃)]  ]

  message digest d = 1010

  sig = [b₀, a₁, b₂, a₃]     ← d[0]=1→b₀, d[1]=0→a₁, d[2]=1→b₂, d[3]=0→a₃

  verify: H(b₀)==pk[0][1]? H(a₁)==pk[1][0]? H(b₂)==pk[2][1]? H(a₃)==pk[3][0]?
```

Lamport 的問題：

```
1. 私鑰和公鑰巨大
   pk = 512 × 32 bytes = 16,384 bytes
   sig = 256 × 32 bytes = 8,192 bytes

2. 只能簽一次（one-time signature, OTS）
   如果同一個 key 簽兩個不同的 message：
     第一次公開 sk[i][d1[i]]
     第二次公開 sk[i][d2[i]]
     如果 d1[i] ≠ d2[i] → 兩個秘密都被公開 → 偽造者可以簽任何 message
```

### 範例一：Lamport Signature 的 Python 實作

```python
"""Lamport one-time signature"""
import hashlib, os

class LamportOTS:
    def __init__(self, n_bits=256):
        self.n = n_bits

    def keygen(self):
        sk = [[os.urandom(32), os.urandom(32)] for _ in range(self.n)]
        pk = [[hashlib.sha256(sk[i][0]).digest(),
               hashlib.sha256(sk[i][1]).digest()] for i in range(self.n)]
        return pk, sk

    def sign(self, sk, message: bytes):
        digest = hashlib.sha256(message).digest()
        return [sk[i][(digest[i // 8] >> (7 - i % 8)) & 1]
                for i in range(self.n)]

    def verify(self, pk, message: bytes, sig):
        digest = hashlib.sha256(message).digest()
        for i in range(self.n):
            bit = (digest[i // 8] >> (7 - i % 8)) & 1
            if hashlib.sha256(sig[i]).digest() != pk[i][bit]:
                return False
        return True

lamp = LamportOTS()
pk, sk = lamp.keygen()
sig = lamp.sign(sk, b"Sign me once!")
print(f"Valid: {lamp.verify(pk, b'Sign me once!', sig)}")
print(f"pk: {256*2*32/1024:.0f} KB, sig: {256*32/1024:.0f} KB — 太大，且只能簽一次")
```

### Step 2：WOTS+（few-time → 壓縮 OTS）

WOTS+（Winternitz One-Time Signature Scheme Plus）用 hash chain 壓縮 Lamport 的大小。

```
WOTS+ 的核心 idea：用 hash chain 取代 Lamport 的兩列結構

Lamport: 每個 bit 用 2 個秘密值（0 列和 1 列）
WOTS+:   把 message 分成 w-bit chunks，每個 chunk 用一條 hash chain

hash chain（w = 16, 長度 16）：
  sk ──H──→ h₁ ──H──→ h₂ ──H──→ ... ──H──→ h₁₅ = pk

  簽章：如果 chunk value = 5 → 公開 h₅（hash 5 次）
  驗證：從 h₅ 再 hash 10 次，看是否等於 pk（h₁₅）

w = 16（Winternitz parameter）：
  256-bit message → 64 個 4-bit chunks + checksum
  每個 chunk 一條 chain
  pk = 67 個 hash 值 ≈ 2,144 bytes（vs Lamport 的 16 KB）
  sig = 67 個 hash 值 ≈ 2,144 bytes（vs Lamport 的 8 KB）
  
  代價：sign 和 verify 需要更多 hash 運算
```

### Step 3：XMSS（many-time，但 stateful）

```
XMSS（Extended Merkle Signature Scheme）用 Merkle tree 管理多個 WOTS+ 密鑰

                    [root = pk]
                   /            \
              [h01]              [h23]
             /     \            /     \
        [WOTS₀]  [WOTS₁]  [WOTS₂]  [WOTS₃]
        
  4 個 WOTS+ 密鑰 → 可以簽 4 次
  每次簽章附帶 Merkle path（authentication path）

  簽第 2 個 message：
    sig = (WOTS₂ 的簽章, [h23, h01])  ← authentication path
    驗證：用 WOTS₂ 簽章算出 WOTS₂ 的 root
         → 和 h23 合併 hash → 和 h01 合併 hash → 等於 pk?

  tree height h → 可以簽 2^h 次

問題：XMSS 是 stateful（有狀態的）
  必須記住「已經用了哪個 WOTS key」
  如果 state 丟失或重複使用 → 安全性崩潰
  這在分散式系統或備份恢復場景中是致命的
```

### Step 4：SPHINCS+（many-time，stateless）

```
SPHINCS+ 的突破：用 hyper-tree 消除 state

SPHINCS+ = WOTS+ + FORS + Hyper-tree

                    ┌─────────────────────────────┐
                    │        Hyper-tree            │
                    │                               │
  top tree:         │    [root = public key]        │
  (XMSS-like)      │    /                  \       │
                    │  [...]              [...]     │
                    │  /    \            /    \     │
                    │[W₀]  [W₁]  ...  [Wₙ] [Wₙ₊₁]│  ← 每個 W 是 WOTS+
                    │  |    |          |    |       │     簽下一層 tree 的 root
                    │  ↓    ↓          ↓    ↓       │
  layer 1:          │ [T₀] [T₁]  ... [Tₙ] [Tₙ₊₁] │  ← 每個 T 是一棵 XMSS tree
                    │  |    |          |    |       │
                    │  ↓    ↓          ↓    ↓       │
  ...               │ (更多 layers)                 │
                    │  ↓    ↓          ↓    ↓       │
  bottom layer:     │ [FORS₀] [FORS₁] ... [FORSₙ]  │  ← FORS 簽 message
                    └─────────────────────────────┘

FORS（Forest of Random Subsets）：
  - few-time signature scheme
  - 用來簽 message digest
  - 比 WOTS+ 更適合被多次使用（但仍有限制）

Stateless 的關鍵 trick：
  簽章時，用 message 和 secret key 確定性地選擇哪條路徑
  → 不需要記住狀態
  → 同一個 message 永遠走同一條路徑（確定性）
  → 不同 message 走不同路徑（隨機性來自 PRF）
```

## 底層機制：SPHINCS+ 的完整簽章流程

```
SPHINCS+ Sign(sk, M)：

  1. 用 PRF(sk, M) 生成隨機值 R
     optrand = random (if randomized) or 0 (if deterministic)
     R = PRF(sk.prf, optrand, M)

  2. 計算 message digest 和 tree 位址
     (md, idx) = H_msg(R, pk, M)
     idx 決定用 hyper-tree 的哪條路徑

  3. FORS 簽章
     sig_fors = FORS.sign(md, sk, idx)
     pk_fors = FORS.pkFromSig(sig_fors, md)

  4. Hyper-tree 簽章
     sig_ht = HT.sign(pk_fors, sk, idx)
     （從底層 tree 一路簽到 top tree）

  5. sig = (R, sig_fors, sig_ht)

SPHINCS+ Verify(pk, M, sig)：

  1. (md, idx) = H_msg(R, pk, M)
  2. pk_fors = FORS.pkFromSig(sig_fors, md)
  3. HT.verify(pk_fors, sig_ht, pk, idx)
```

```
SPHINCS+ 的參數和 size（FIPS 205）：

  ┌───────────────────┬──────────┬──────────┬───────────┬────────────┐
  │ 變體               │ pk (B)   │ sig (B)  │ NIST Level│ 速度特性    │
  ├───────────────────┼──────────┼──────────┼───────────┼────────────┤
  │ SLH-DSA-128s      │    32    │  7,856   │ 1         │ 小簽章      │
  │ SLH-DSA-128f      │    32    │ 17,088   │ 1         │ 快簽章      │
  │ SLH-DSA-192s      │    48    │ 16,224   │ 3         │ 小簽章      │
  │ SLH-DSA-192f      │    48    │ 35,664   │ 3         │ 快簽章      │
  │ SLH-DSA-256s      │    64    │ 29,792   │ 5         │ 小簽章      │
  │ SLH-DSA-256f      │    64    │ 49,856   │ 5         │ 快簽章      │
  └───────────────────┴──────────┴──────────┴───────────┴────────────┘

  s = small signature（簽章較小，但 sign/verify 較慢）
  f = fast（sign/verify 較快，但簽章較大）

  公鑰極小（32–64 bytes）！但簽章巨大（7.8–49.8 KB）
```

## 進一步用法：SPHINCS+ 的安全性分析

### 範例二：安全假設的保守程度比較

```
方案                安全假設              假設歷史  若被打破的後果
─────────────────────────────────────────────────────────────────
ECDSA P-256        ECDLP                40 年     方案失效，換 PQC
ML-DSA-65          Module-LWE           20 年     ML-KEM 也一起倒
SLH-DSA-128f       SHA-256 安全性       30 年     幾乎所有密碼學都失效
```

結論：SLH-DSA 的假設最保守——如果 SHA-256 不安全，整個密碼學生態都要重建。反過來說，只要 SHA-256 安全，SLH-DSA 就一定安全。這就是 NIST 把它選為 backup standard 的原因。

### 為什麼 SPHINCS+ 是 Stateless 的？

```
XMSS 的問題：stateful
  - 必須追蹤「第幾個 WOTS key 已經用過」
  - 如果 VM snapshot restore → state 倒退 → WOTS key 被重用 → 不安全
  - 在 HSM、load balancer、database backup 場景中都是問題

SPHINCS+ 的解法：確定性路徑選擇
  idx = PRF(sk, M)  
  → 同一個 message 永遠選同一個路徑
  → 不需要記住 state
  → 不同 message 的 idx 看起來是隨機的

  trade-off：
    - hyper-tree 要夠大，使得碰撞（兩個不同 message 選到同一路徑）的機率低
    - tree 越大 → 簽章越大
    - SPHINCS+ 的簽章大是為了保證 statelessness
```

## 對比與取捨

| 特性 | SLH-DSA-128f | ML-DSA-44 | ML-DSA-65 | ECDSA P-256 | XMSS |
|---|---|---|---|---|---|
| 安全假設 | hash function | Module-LWE | Module-LWE | ECDLP | hash function |
| 量子安全 | ✓ | ✓ | ✓ | ✗ | ✓ |
| 公鑰大小 | 32 B | 1312 B | 1952 B | 64 B | ~1 KB |
| 簽章大小 | 17,088 B | 2420 B | 3293 B | 64 B | ~2.5 KB |
| Sign 速度 | ~10 ms | ~0.15 ms | ~0.25 ms | ~0.06 ms | ~1 ms |
| Verify 速度 | ~0.5 ms | ~0.05 ms | ~0.08 ms | ~0.15 ms | ~0.3 ms |
| Stateful | 否 | 否 | 否 | 否 | **是** |
| 假設保守程度 | ★★★★★ | ★★★☆☆ | ★★★☆☆ | ★★★★☆ | ★★★★★ |
| NIST 角色 | backup | primary | primary | legacy | NIST SP 800-208 |
| 適用場景 | 長期保存 | 通用 | 通用 | 暫時 | 特殊用途 |

### 什麼時候該用 SLH-DSA 而不是 ML-DSA？

```
用 SLH-DSA 的場景：
  1. 長期安全需求（文件存檔 50+ 年）
     → lattice 假設可能在 50 年內被打破，hash 不太可能
  
  2. 極端保守的政策（政府、軍事、金融 root CA）
     → 不能承受「20 年後 lattice 被打破」的風險
  
  3. 簽章不頻繁，驗證也不頻繁（firmware update signing）
     → size 和速度不是瓶頸

用 ML-DSA 的場景：
  1. TLS（每個連線都需要簽章 + 驗證）
     → 17 KB 的簽章在每個 handshake 裡太大
  
  2. IoT / 受限環境
     → sign 要 ~10 ms，ML-DSA 的 ~0.25 ms 更合適
  
  3. 需要頻繁簽章的場景（API authentication、JWT）
     → SLH-DSA 太慢

混合策略（defense in depth）：
  Root CA certificate → SLH-DSA（最保守，簽一次）
  Intermediate CA → ML-DSA（平衡安全和效能）
  Leaf certificate → ML-DSA（TLS handshake 需要速度）
```

## 踩雷集錦

1. **「SPHINCS+ 的公鑰只有 32 bytes，比 ML-DSA 好！」**：公鑰確實極小，但簽章高達 17–49 KB。在 TLS 中，certificate 包含公鑰和發行者的簽章，所以整體 size 由簽章主導。SLH-DSA-128f 的一個 certificate ≈ 17 KB 簽章 + overhead，比 ML-DSA-65 的 ~3.3 KB 大 5 倍。

2. **「Hash-based 簽章很慢」**：SLH-DSA-128f 的 sign 大約 10 ms，verify 大約 0.5 ms。對於「簽一次、驗多次」的場景（code signing、CA certificate）這完全可以接受。不能接受的是高頻場景（每秒數千次 TLS handshake）。

3. **「XMSS 是 stateful 的所以不安全」**：XMSS 在正確管理 state 的情況下完全安全（NIST SP 800-208）。問題在於「正確管理 state」在很多實際場景中很困難（VM clone、crash recovery、load balancing）。SLH-DSA 用更大的簽章換來了 statelessness——這是工程上的取捨，不是安全性的取捨。

4. **「SLH-DSA 的安全假設永遠不會被打破」**：SLH-DSA 的安全性依賴 hash function 的多個安全性質（preimage resistance、second-preimage resistance、PRF security）。如果有人找到 SHA-256 的 preimage 攻擊（極不可能，但不是數學上不可能），SLH-DSA 就不安全了。但如果 SHA-256 真的被打破，受影響的遠不止 SLH-DSA——幾乎所有的密碼協議都依賴 hash function。

## 進階：再往深一層

### FORS 的設計

FORS（Forest of Random Subsets）是 SPHINCS+ 底層的 few-time signature。結構：k 棵 Merkle tree，每棵 2^a 個葉子。簽章時把 message digest 分成 k 個 a-bit chunks，每個 chunk 指定一棵 tree 的葉子並公開 authentication path。為什麼不用 WOTS+？因為 WOTS+ 是 one-time（重用完全暴露秘密），而 FORS 是 few-time（重用只暴露部分）。在 SPHINCS+ 中，hash 碰撞可能導致同一個 FORS key 被用兩次——FORS 的 few-time 性質讓碰撞不致命。

### Hash Function 的選擇

```
SPHINCS+ / SLH-DSA 支援三種 hash function：

  1. SHA-256 → SLH-DSA-SHA2 變體
  2. SHAKE256 → SLH-DSA-SHAKE 變體
  3. Haraka → 非標準，不在 FIPS 205 中

NIST FIPS 205 只標準化了 SHA-256 和 SHAKE256 變體

選擇建議：
  - SHAKE256：更靈活（arbitrary output length），在 PQC 生態中更常用
  - SHA-256：硬體加速更廣泛（Intel SHA-NI），在已有 SHA-256 加速的平台上更快
```

### Size-Speed Tradeoff（s vs f 變體）

s（small）和 f（fast）的差異在 hyper-tree 的深度配置。s 變體 tree 更深 → 簽章更小（7,856 B）但更慢（sign ~70 ms）。f 變體 tree 更淺 → 簽章更大（17,088 B）但更快（sign ~10 ms）。選擇取決於你的瓶頸是頻寬還是計算。

## 動手練習

1. **實作 Lamport OTS**：用 Python 實作完整的 Lamport signature（KeyGen / Sign / Verify）。測量公鑰、私鑰、簽章的大小。然後用同一個 key 簽兩個不同的 message，觀察哪些秘密被洩漏。

2. **Merkle Tree**：實作一棵 depth=4 的 Merkle tree（16 個葉子）。實作 `get_auth_path(leaf_idx)` 和 `verify_path(leaf, path, root)`。驗證修改任何一個葉子都會改變 root。

3. **Size 比較**：計算以下場景的 total signature overhead：
   - Certificate chain（3 certs）全部用 SLH-DSA-128f
   - Certificate chain 全部用 ML-DSA-65
   - 混合：root CA 用 SLH-DSA-128s，intermediate 和 leaf 用 ML-DSA-65

4. **WOTS+ Hash Chain**：實作一個 w=16 的 WOTS+ hash chain。給定 secret value sk，計算 chain 的所有中間值和 public key（最終值）。驗證 sign 和 verify 能正確運作。

## 本章重點整理

- Hash-based 簽章的安全性只依賴 hash function——最保守的密碼學假設
- 演進路線：Lamport（one-time）→ WOTS+（壓縮 OTS）→ XMSS（Merkle tree, stateful）→ SPHINCS+（hyper-tree, stateless）
- SPHINCS+ 用確定性路徑選擇（PRF(sk, M)）消除了 state 的需求
- SLH-DSA 的公鑰極小（32–64 B），但簽章很大（7.8–49.8 KB）
- NIST 把 SLH-DSA 選為 backup standard：如果 lattice 假設被打破，SLH-DSA 仍然安全
- 適用場景：長期安全、簽章不頻繁（root CA、firmware signing、文件存檔）

## 自我檢核

- [ ] 能解釋 Lamport signature 的 KeyGen / Sign / Verify 流程
- [ ] 能說出 Lamport 為什麼只能簽一次
- [ ] 能解釋 WOTS+ 如何用 hash chain 壓縮 Lamport 的大小
- [ ] 能區分 XMSS（stateful）和 SPHINCS+（stateless）的差異
- [ ] 能描述 SPHINCS+ hyper-tree 的層次結構
- [ ] 能比較 SLH-DSA 和 ML-DSA 的優缺點，並說出各自適用的場景
- [ ] 能解釋為什麼 SLH-DSA 是 NIST 的 backup standard

## 延伸閱讀

- **NIST FIPS 205, "Stateless Hash-Based Digital Signature Standard"（2024）**
  - **讀哪裡**：Section 4–7（algorithm specification）
  - **學什麼**：SPHINCS+ 的完整規格，包括 FORS、WOTS+、hyper-tree 的精確定義
  - **關聯**：本章所有概念的正式版本

- **Daniel J. Bernstein et al., "SPHINCS+"（submission to NIST PQC, 2017）**
  - **讀哪裡**：Section 1–3（overview, design rationale）
  - **學什麼**：為什麼選 hyper-tree + FORS 的設計、statelessness 的安全性分析
  - **關聯**：本章 hyper-tree 和 FORS 段落的設計動機

- **Andreas Hülsing, "W-OTS+ — Shorter Signatures for Hash-Based Signature Schemes"（2013）**
  - **讀哪裡**：Section 2–3
  - **學什麼**：WOTS+ 如何在 WOTS 的基礎上用 bitmask 提高安全性
  - **關聯**：本章 WOTS+ 段落的理論背景

- **Leslie Lamport, "Constructing Digital Signatures from a One Way Function"（1979, SRI Technical Report）**
  - **讀哪裡**：全文（只有幾頁）
  - **學什麼**：Lamport OTS 的原始提案——密碼學中最簡潔的簽章構造
  - **關聯**：本章 Lamport 段落的理論來源

→ [練習 D：Kyber-512](./practice-d-kyber512.md)
