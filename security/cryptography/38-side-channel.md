# Ch 38 — Side-Channel Attack：你的演算法沒錯，但你的實作洩密了

> **目標**：能分類 timing / power / cache side-channel，理解 FLUSH+RELOAD 和 PRIME+PROBE 的原理，知道為什麼 AES 的 T-table 實作不安全。

---

## 為什麼需要這個？

你花了 37 章學會怎麼「正確」使用密碼學原語——選對演算法、用對模式、不 reuse nonce、不自己設計 cipher。

但攻擊者不一定正面突破你的演算法。

2017 年，Google Project Zero 公開了 Spectre 和 Meltdown——兩個利用 CPU 微架構特性的攻擊，能從 JavaScript 裡跨 process 讀取記憶體。攻擊的不是 AES，不是 RSA，不是任何密碼學演算法。攻擊的是 **CPU cache 的行為模式**。

這就是 side-channel attack（側通道攻擊）的核心思想：**攻擊的不是演算法的數學，而是 implementation 在執行演算法時洩漏的物理資訊**。

執行時間、耗電量、電磁輻射、cache hit/miss 的 pattern——這些「副產品」看起來和密碼學無關，但它們會洩漏 secret key 的 bit。

---

## 先建立直覺

想像你在猜保險箱的密碼鎖：

```
場景一：純數學攻擊
  密碼鎖有 4 位數 → 10,000 種組合
  你一個一個試 → brute force

場景二：Side-channel attack
  你發現轉到正確的數字時，鎖內部會發出輕微的「喀」聲
  你一個數字一個數字轉，聽到「喀」就確認這位
  4 位數 × 10 個候選 = 最多 40 次嘗試

  攻擊的不是鎖的設計，而是鎖在運作時的聲音洩漏
```

這個比喻精確對應了 timing attack：密碼學運算在處理不同 key bit 時花的時間不同，攻擊者測量時間差來逐 bit 還原 key。

---

## 核心概念：四大類 Side-Channel

### 1. Timing Attack（計時攻擊）

**原理**：不同的 input（或不同的 key bit）導致不同的執行路徑，花不同的時間。

Paul Kocher 在 1996 年的 CRYPTO 論文裡展示：RSA 的 square-and-multiply 演算法在處理 key bit = 1 和 key bit = 0 時，執行的操作不同——bit = 1 時多做一次 modular multiplication。攻擊者精確測量每次 RSA 解密的時間，統計分析後逐 bit 還原私鑰。

```
RSA 的 square-and-multiply（從 MSB 到 LSB 掃描 key bit）：

result = 1
for each bit b in private_key (MSB → LSB):
    result = result² mod n          ← 每個 bit 都做（square）
    if b == 1:
        result = result × base mod n  ← 只有 bit=1 才做（multiply）

bit = 0：只做 square          → 時間 T₀
bit = 1：做 square + multiply → 時間 T₁ > T₀

攻擊者測量 N 次解密的總時間，
用統計方法分離出每個 bit 的貢獻 → 逐 bit 還原 private key
```

### 2. Power Analysis（功耗分析）

**原理**：CPU 執行不同指令時消耗的電力不同。用示波器量 IC 的電源線，就能看到每個 clock cycle 的功耗波形。

兩種變體：

- **SPA（Simple Power Analysis，簡單功耗分析）**：直接觀察功耗波形，從波形的高低模式辨識出 key bit。RSA 的 square-and-multiply 在功耗波形上清楚可見——square 是一個 pattern，multiply 是另一個 pattern。
- **DPA（Differential Power Analysis，差分功耗分析）**：Kocher、Jaffe、Jun 在 1999 年提出。收集大量加密運算的功耗波形，用統計方法（correlation）分離出與 key bit 相關的微小功耗差異。

```
SPA vs DPA：

SPA：
  ┌──────────────────────────────────────────┐
  │ 功耗波形                                  │
  │     ╱╲    ╱╲╱╲    ╱╲    ╱╲╱╲    ╱╲      │
  │   ╱    ╲╱        ╲    ╲╱        ╲    ╲   │
  │  S      S    M    S      S    M    S     │
  │                                          │
  │  key bit: 0       1       0       1      │
  │  S = square, M = multiply                │
  │  直接從波形讀出 key bit pattern           │
  └──────────────────────────────────────────┘

DPA：
  收集 10,000 次加密的功耗波形
  → 按假設的 key bit 分成兩組
  → 計算兩組的平均功耗差
  → 正確的 key bit 假設會讓差異最大化
  → 逐 bit 還原 key
```

DPA 對智慧卡（smart card）是毀滅性的——攻擊者只需要一台示波器和一個讀卡機。1999 年的論文發表後，整個智慧卡產業被迫全面加入 countermeasure。

### 3. EM（電磁輻射攻擊）

**原理**：和 power analysis 類似，但觀察的不是電源線的電流，而是 IC 輻射的電磁波。

優勢：**不需要物理接觸**。用高靈敏度天線可以在幾公尺外收集 EM 訊號。TEMPEST（美國 NSA 的機密計畫，現已部分解密）就是針對 EM side-channel 的攻防。

2020 年的研究展示，用 EM probe 可以從 10 公尺外的筆電還原 RSA 私鑰——前提是目標反覆做 RSA 解密，讓攻擊者收集足夠的波形。

### 4. Cache Attack（快取攻擊）

**原理**：CPU cache 的 access pattern 取決於 memory address，而 memory address 可能取決於 secret data（例如 AES T-table lookup 的 index）。

這是 2010 年代以來最活躍的 side-channel 研究方向，因為它 **不需要物理 access**——只需要和 victim 跑在同一台機器上（同一台 cloud VM 就夠了）。

---

## 底層機制：Cache Attack 詳解

### CPU Cache 的基本運作

```
CPU Core
  ├── L1 Cache (32-64 KB, ~1 ns)      ← 每個 core 私有
  ├── L2 Cache (256 KB-1 MB, ~3 ns)   ← 每個 core 私有
  └── L3 Cache (數 MB-數十 MB, ~10 ns) ← 所有 core 共享 ★
          │
    Main Memory (數 GB, ~100 ns)

Cache 的單位是 cache line（通常 64 bytes）。
CPU 存取一個 memory address 時：
  1. 先查 L1 → L2 → L3（hit → 快）
  2. 全部 miss → 從 main memory 載入（慢）
  3. 載入時整條 cache line 都會被填入 cache
```

關鍵洞見：**cache hit 和 cache miss 的時間差（~100 ns vs ~1 ns）是可測量的**。如果 victim 的 memory access pattern 取決於 secret data，攻擊者就能通過測量 cache timing 推斷 secret。

### FLUSH+RELOAD

Yarom 和 Falkner 在 2014 年 USENIX Security 發表的攻擊。

**前提**：attacker 和 victim 共享同一個 memory page（例如 shared library——`libcrypto.so` 在多個 process 之間只有一份物理記憶體）。

```
FLUSH+RELOAD 三步驟：

時間軸 ──────────────────────────────────────────────────→

步驟 1: FLUSH
  攻擊者對目標 cache line 執行 clflush 指令
  → 把那條 cache line 從所有 cache 層級踢出去

          L1   L2   L3    Memory
  target: [ ]  [ ]  [ ]   [data]  ← cache 全空

步驟 2: WAIT
  等待 victim 執行密碼運算
  如果 victim 存取了目標 address → 那條 cache line 被載入 cache
  如果 victim 沒存取 → cache line 仍然空

  情況 A（victim 存取了）：
          L1   L2   L3    Memory
  target: [✓]  [✓]  [✓]  [data]  ← cache 被填充

  情況 B（victim 沒存取）：
          L1   L2   L3    Memory
  target: [ ]  [ ]  [ ]   [data]  ← 仍然空

步驟 3: RELOAD
  攻擊者存取同一個 address，測量 access time
  → 快（cache hit, ~10 ns）  → victim 存取過 → 情況 A
  → 慢（cache miss, ~100 ns）→ victim 沒存取 → 情況 B

攻擊者重複 FLUSH → WAIT → RELOAD，
每次 RELOAD 的時間告訴攻擊者 victim 那一輪有沒有存取目標 address。
```

精確度：L3 cache 的 hit/miss 差異約 50-100 ns，用 `rdtsc`（Read Time-Stamp Counter）指令可以精確到個位數 cycle 的解析度。

### PRIME+PROBE

比 FLUSH+RELOAD 更通用——**不需要 shared memory**。

**原理**：攻擊者先把整個 cache set 填滿自己的資料（PRIME），然後等 victim 執行，最後檢查哪些 cache line 被 victim 的存取踢出去了（PROBE）。

```
PRIME+PROBE：

Cache 的結構（以 8-way set associative 為例）：

Set 0: [Way0][Way1][Way2][Way3][Way4][Way5][Way6][Way7]
Set 1: [Way0][Way1][Way2][Way3][Way4][Way5][Way6][Way7]
...
Set N: [Way0][Way1][Way2][Way3][Way4][Way5][Way6][Way7]

步驟 1: PRIME
  攻擊者存取 8 個 address（映射到同一個 cache set）
  → 把 Set K 的 8 個 way 全部填滿攻擊者的資料

  Set K: [A0][A1][A2][A3][A4][A5][A6][A7]  ← 全是攻擊者的

步驟 2: WAIT
  等待 victim 執行
  如果 victim 的某個存取映射到 Set K → 踢掉一個 way

  Set K: [A0][A1][V!][A3][A4][A5][A6][A7]  ← Way2 被 victim 換掉

步驟 3: PROBE
  攻擊者重新存取 8 個 address，測量每個的 access time
  → A2 變慢了（cache miss）→ victim 存取了映射到 Set K 的 address

攻擊者知道哪些 cache set 被 victim 碰過
→ 推斷 victim 的 memory access pattern
→ 推斷 secret data
```

**FLUSH+RELOAD vs PRIME+PROBE**：

| 面向 | FLUSH+RELOAD | PRIME+PROBE |
|---|---|---|
| 需要 shared memory | 是（shared library） | 否 |
| 精確度 | cache line 級別（64 bytes） | cache set 級別（較粗） |
| 噪音 | 低（直接觀察特定 line） | 較高（需要統計多次） |
| 跨 VM | 需要 memory dedup | 天生可跨 VM |
| 典型場景 | 攻擊 shared library（OpenSSL） | 攻擊 cloud co-tenant |

---

## 進一步用法：AES T-table 為什麼不安全

### 範例一：Python timing attack — 逐 byte 爆破 strcmp

這個 PoC 展示最基本的 timing side-channel：`strcmp` 的 short-circuit 行為讓正確的前綴比錯誤的前綴多花一點時間。

```python
"""
Timing attack PoC：利用 byte-by-byte 比較的時間差
逐字節還原 secret token。

注意：Python 的 GC 和 OS 排程引入大量噪音，
需要多次測量取 median 才能看到差異。
實際的 remote timing attack 需要更精密的統計。
"""
import time
import string
import statistics

SECRET_TOKEN = b"s3cR3t_K3y!"  # 假設這是 server 端的 secret

def vulnerable_compare(user_input: bytes, secret: bytes) -> bool:
    """
    模擬 naive strcmp：逐 byte 比較，
    第一個不同的 byte 就 return False。
    """
    if len(user_input) != len(secret):
        return False
    for i in range(len(secret)):
        if user_input[i] != secret[i]:
            return False  # ← 提前返回！匹配越多 byte，花越多時間
    return True

def measure_time(candidate: bytes, iterations: int = 5000) -> float:
    """測量比較 candidate 與 secret 的 median 時間"""
    times = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        vulnerable_compare(candidate, SECRET_TOKEN)
        elapsed = time.perf_counter_ns() - start
        times.append(elapsed)
    return statistics.median(times)

def timing_attack():
    """逐 byte 還原 secret"""
    known = bytearray(len(SECRET_TOKEN))
    charset = string.printable.encode()

    for pos in range(len(SECRET_TOKEN)):
        best_char = 0
        best_time = 0

        for c in charset:
            candidate = bytes(known)
            # 把當前位置設成候選字元
            test = bytearray(candidate)
            test[pos] = c
            test = bytes(test)

            t = measure_time(test)
            if t > best_time:
                best_time = t
                best_char = c

        known[pos] = best_char
        print(f"Position {pos}: found '{chr(best_char)}' "
              f"(time: {best_time} ns)")

    print(f"\nRecovered: {bytes(known)}")
    print(f"Actual:    {SECRET_TOKEN}")
    print(f"Match:     {bytes(known) == SECRET_TOKEN}")

if __name__ == "__main__":
    timing_attack()
```

**結果**：在低噪音環境下，這個 PoC 能逐 byte 還原 secret。每個位置只需要嘗試所有 printable 字元（~100 次比較），總共 ~100 × len(secret) 次——遠少於暴力搜索的 256^len(secret)。

### AES T-table 的 Cache Side-Channel

AES 的高效軟體實作（包括 OpenSSL 在 AES-NI 之前的版本）使用 T-table：四張 1 KB 的 lookup table（T0, T1, T2, T3），每張 256 個 32-bit entry。

```
AES 一輪的運算（T-table 實作）：

e_j = T0[s0[a]] ⊕ T1[s1[b]] ⊕ T2[s2[c]] ⊕ T3[s3[d]] ⊕ round_key[j]

其中 s0[a] = state byte，取決於 plaintext ⊕ key

問題：T-table 的 index 取決於 (plaintext ⊕ key)

攻擊者知道 plaintext（chosen-plaintext 或已知明文）
→ 如果能知道 T-table 的哪個 index 被存取
→ 就能算出 key byte

Cache line = 64 bytes = 16 個 T-table entry
→ 攻擊者至少能知道 index 的高 4 bit（哪條 cache line）
→ 對 key byte 的搜索空間從 256 縮小到 16
→ 重複收集足夠多的 (plaintext, cache observation) pair
→ 用統計方法完全還原 key
```

Bernstein 在 2005 年用 remote timing attack（不是 cache probe，而是 T-table access 導致的整體計時差異）在遠端還原了 AES key。因果鏈：`plaintext XOR key → T-table index → cache hit/miss → 執行時間差異 → 統計還原 key`。

### 範例二：FLUSH+RELOAD 攻擊 AES T-table 的步驟

前提：攻擊者和 victim 同機、libcrypto.so shared → T-table 物理記憶體共享。

```
1. FLUSH: 對 T0 的 16 條 cache line 全部 clflush
2. TRIGGER: victim 執行 AES → 存取 T0[plaintext[0] ⊕ key[0]]
3. RELOAD: 依序存取 16 條 line，測量時間
   → Line i 是 hit → index ∈ [16i, 16i+15]
   → key[0] 候選從 256 縮小到 16
4. 重複 ~200-500 次（不同 plaintext）→ 完全還原 key[0]
5. 對 key[1]~key[15] 重複 → 還原完整 AES-128 key
```

---

## 對比與取捨

| 面向 | Timing | Power (SPA/DPA) | EM | Cache |
|---|---|---|---|---|
| 需要物理 access | 不一定（remote 可行） | 是（要接電源線） | 近距離（數公尺） | 不需要（co-located 即可） |
| 適用環境 | 任何 networked system | 嵌入式/智慧卡/IoT | 嵌入式/桌機 | cloud、shared hosting |
| 測量設備 | 高精度計時器 | 示波器 | EM probe + 示波器 | 只需要 user-level code |
| 噪音程度 | 中（OS/network 噪音） | 低（物理量測精準） | 中 | 中-高（cache 共用引入噪音） |
| 典型目標 | RSA, ECDSA, string compare | AES on smart card | RSA on embedded | AES T-table, RSA |
| 防禦方法 | constant-time code | masking, shuffling | EM shielding | bitsliced impl, AES-NI |

---

## 踩雷集錦

### 雷 1：「我的程式跑在 cloud 上，不會被 side-channel」

Spectre（2018）證明了 cross-VM side-channel 是現實的。攻擊者只需要租一台和你同 physical host 的 VM，就能用 cache side-channel 觀察你的行為。AWS、Azure、GCP 都因此緊急修了 CPU microcode 和 kernel。

FLUSH+RELOAD 在開啟 memory deduplication（KSM）的 hypervisor 上直接可行。PRIME+PROBE 則天生不需要 shared memory。

### 雷 2：「compiler optimization 會修好 timing leak」

恰好相反——**compiler 是 constant-time code 的天敵**。

```c
// 你寫的 constant-time comparison
uint32_t ct_compare(const uint8_t *a, const uint8_t *b, size_t n) {
    uint32_t diff = 0;
    for (size_t i = 0; i < n; i++)
        diff |= a[i] ^ b[i];
    return diff;
}

// compiler 看到：「diff 只要非零就確定結果了」
// -O2 可能優化成：
uint32_t ct_compare_optimized(const uint8_t *a, const uint8_t *b, size_t n) {
    for (size_t i = 0; i < n; i++)
        if (a[i] != b[i]) return 1;  // ← 提前返回！不再 constant-time
    return 0;
}
```

這不是假設——GCC 和 Clang 在 `-O2` 以上確實會做這種優化。Ch 39 會教如何防範。

### 雷 3：Power analysis 不是理論——智慧卡被 DPA 打爆是商業現實

1999 年 Kocher 等人發表 DPA 後，所有主要智慧卡廠商（NXP、Infineon、ST）都被迫加入 hardware countermeasure。EMVCo（信用卡標準組織）要求通過 side-channel evaluation 才能上市。Common Criteria 的 AVA_VAN 評估等級就是在測 side-channel resistance。

一台 DPA 攻擊設備（示波器 + 讀卡機 + 軟體）的成本在 2024 年約 $5,000-$50,000——對國家級攻擊者或犯罪組織完全可承受。

### 雷 4：「用 AES-NI 就安全了」

部分正確。AES-NI 是 Intel/AMD 的硬體指令，AES 運算在 CPU pipeline 內完成，不經過 T-table → 天生 constant-time。但前提是你的 crypto library 確實使用了 AES-NI path。

OpenSSL 在偵測到 AES-NI 時會自動使用硬體路徑。但如果你跑在不支援 AES-NI 的 CPU 上（某些 ARM、舊 x86），OpenSSL 會 fallback 到 T-table 軟體實作——**回到 vulnerable 狀態**。

### 雷 5：Spectre 和 Meltdown 不只是 CPU bug——它們是整個計算模型的假設崩壞

Spectre 利用的是 CPU 的 speculative execution（推測執行）：CPU 在 branch 結果出來之前先猜一條路徑執行，猜錯再 rollback。但 rollback 不會清除 cache 狀態——attacker 用 cache timing 讀出 speculative 路徑存取的資料。

這代表**即使你的 code 永遠不會真正執行某條路徑，CPU 的推測執行也可能在 cache 裡留下痕跡**。

---

## 進階

### Microarchitectural Side-Channel 的分類學

```
Microarchitectural Side-Channel：
├── Cache-based
│   ├── FLUSH+RELOAD (2014, Yarom & Falkner)
│   ├── PRIME+PROBE (2005, Osvik et al.)
│   ├── FLUSH+FLUSH (2016, 不 reload, 用 clflush 的時間)
│   ├── EVICT+TIME (2006, 觀察 victim 的總執行時間)
│   └── PRIME+ABORT (2017, 用 TSX abort 代替 timing)
│
├── Speculative Execution
│   ├── Spectre v1 (bounds check bypass)
│   ├── Spectre v2 (branch target injection)
│   ├── Meltdown (rogue data cache load)
│   ├── Foreshadow / L1TF (L1 terminal fault)
│   └── MDS (Microarchitectural Data Sampling)
│
├── TLB-based
│   └── TLBleed (2018, 從 TLB 觀察 page access pattern)
│
└── Contention-based
    ├── Port contention (2019, 觀察 execution port 的競爭)
    └── Memory bus contention
```

### Masking Countermeasure

把 secret 拆成多個 share：`share_1 = s XOR m`、`share_2 = m`。單獨觀察任一 share 的功耗和 s 無關（m 是 random）。攻擊者必須同時觀察兩個 share → 需要 second-order DPA（更難）。d 個 share 抵擋 d-1 階 DPA，但計算開銷隨 order 指數增長。

### Rowhammer

嚴格來說不是 side-channel 而是 fault injection：反覆存取 DRAM row → 相鄰 row 的 bit 翻轉 → 如果翻轉在 page table entry 或 RSA key 裡 → game over。

---

## 動手練習

1. **Timing attack PoC**：執行範例一的 Python timing attack。調整 `iterations` 參數，觀察在多少次測量後能穩定還原 secret 的每個 byte。

2. **用 `perf stat` 觀察 cache miss**：在 Linux 上用 `perf stat -e cache-misses,cache-references` 跑一個 AES 加密程式，分別用不同的 plaintext，觀察 cache miss 數量是否有差異。

3. **研究 OpenSSL 的 AES 路徑**：用 `openssl speed -evp aes-128-ecb` 測速，然後用 `OPENSSL_ia32cap="~0x200000200000000"` 環境變數禁用 AES-NI 重測。比較兩者的速度差異，思考 fallback path 的 side-channel 風險。

4. **閱讀 Spectre PoC**：Google Project Zero 的 Spectre PoC（C code）展示了如何用 cache timing 讀取跨 boundary 的記憶體。閱讀 code，辨識出 FLUSH+RELOAD 的三個步驟在哪裡。

---

## 重點整理

```
Side-channel attack 的本質：
  攻擊的不是演算法的數學安全性
  攻擊的是 implementation 在執行時洩漏的物理資訊

四大類 side-channel：
  Timing  → 不同 input 的執行時間不同
  Power   → 不同指令的功耗不同（SPA 直接讀、DPA 統計分析）
  EM      → 電磁輻射（和 power 類似，但可以遠距離）
  Cache   → CPU cache 的 hit/miss pattern 洩漏 memory access pattern

Cache attack 兩大方法：
  FLUSH+RELOAD → 需要 shared memory，精確度高，L3 cache line 級別
  PRIME+PROBE  → 不需要 shared memory，噪音較大，cache set 級別

AES T-table 的問題：
  T-table index = plaintext ⊕ key → 取決於 secret
  cache side-channel 洩漏 index → 洩漏 key
  防禦：AES-NI 硬體指令 或 bitsliced 軟體實作

防禦策略：
  constant-time code → Ch 39 詳述
  masking → 把 secret 拆成多個 share
  AES-NI / bitsliced → 避免 secret-dependent memory access
```

---

## 自我檢核

- [ ] 能用自己的話解釋 side-channel attack 和 algorithmic attack 的根本區別
- [ ] 能分別解釋 timing、power、EM、cache 四類 side-channel 的觀察對象
- [ ] 能畫出 FLUSH+RELOAD 的三步驟流程，標出在哪個步驟得到 1 bit 資訊
- [ ] 能解釋 PRIME+PROBE 為什麼不需要 shared memory
- [ ] 能解釋 AES T-table 為什麼對 cache side-channel vulnerable（key-dependent index → key-dependent cache access）
- [ ] 能說出至少兩種 cache side-channel 的防禦方法
- [ ] 知道 Spectre 利用的是 speculative execution + cache side-channel 的組合
- [ ] 知道 compiler optimization 可能破壞 constant-time code

---

## 延伸閱讀

- **"Timing Attacks on Implementations of Diffie-Hellman, RSA, DSS, and Other Systems"（Paul Kocher, CRYPTO 1996）**
  - **讀哪裡**：Section 3（RSA timing attack）——用統計方法從 RSA 解密的時間差逐 bit 還原私鑰
  - **學什麼**：timing side-channel 的開山之作；數學不難但 insight 深刻
  - **關聯**：本章 timing attack 的理論基礎

- **"FLUSH+RELOAD: A High Resolution, Low Noise, L3 Cache Side-Channel Attack"（Yarom & Falkner, USENIX Security 2014）**
  - **讀哪裡**：Section 3（attack methodology）和 Section 5（attack on AES）
  - **學什麼**：目前最精確的 cache side-channel 方法；clear write-up，步驟清晰
  - **關聯**：本章 FLUSH+RELOAD 的原始論文

- **"Cache-timing attacks on AES"（Daniel J. Bernstein, 2005）**
  - **讀哪裡**：Section 2（the attack）和 Section 7（countermeasures）
  - **學什麼**：用 remote timing（不是 cache probe）還原 AES key——證明即使不 co-located 也能攻擊
  - **關聯**：本章 T-table timing leak 的量化分析

- **"Spectre Attacks: Exploiting Speculative Execution"（Kocher et al., 2019）**
  - **讀哪裡**：Section 4（Spectre Variant 1）的 PoC
  - **學什麼**：CPU 推測執行如何和 cache side-channel 組合成跨 boundary 的資料洩漏

---

→ [Ch 39 — Constant-Time Programming](./39-constant-time.md)
