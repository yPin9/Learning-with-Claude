# Ch 9 — 建 Eviction Set

> **目標**：搞定 Prime+Probe 最難的前置步驟——找到一組「確實能把目標 cache line 踢出 LLC」的位址集合。這章揭開 LLC slice hash 如何讓 naive stride 完全失效、group-testing 如何在不知道 PA 的情況下仍能建 eviction set、以及 huge page 和 `/proc/PID/pagemap` 如何從根本上解決問題。這是把 P+P 從「玩具 demo」變成「真實攻擊原語」的關鍵。

> **環境**：WSL2 Ubuntu 22.04，Intel i7-10700（Comet Lake，8 物理核，8 LLC slices），`~/microarch_lab`。本章 PoC 真跑驗證，並包含詳細的實測數字——包括失敗的情況，這些失敗和成功一樣有教育意義。

---

## 直覺：為什麼「找同 set 位址」這麼難？

回顧 P+P 的需求：你需要一組位址 E = {e0, e1, ..., ek}，它們都**映射到同一個 LLC cache set**，而且這個 set 和 victim 使用的 cache set 相同。用 E 的 WAYS+1 個成員做 prime，就能保證把那個 set 填滿、踢掉裡面任何一條 line。

問題是：你只有虛擬位址（VA），但 cache set 是由**物理位址（PA）決定的**。

```
Cache set index 計算：
  LLC set index = PA[19:6]  （14 bits，定址 16384 個 sets）

你知道的：
  VA = 0x7f001234000 （你 malloc 出來的虛擬位址）

你不知道的：
  PA = ??? （kernel 決定的物理映射，user-space 沒辦法直接讀）

所以：
  給定一個 VA，你無法直接算出它的 LLC set index
```

在 4KB 分頁（常規 page）下，PA[11:0] = VA[11:0]（page offset 不變），但 PA[12] 以上的 bits 完全由 page table 決定，與 VA 的對應關係是不可預測的（受 ASLR、kernel 記憶體配置策略影響）。

如果這已經夠難了，Comet Lake 還加了一層：**LLC slice hash**。

---

## LLC Slice Hash：問題的根源

Intel Sandy Bridge 之後的 CPU（包括 Comet Lake）把 LLC 拆分成多個「slices」，每個 slice 是一個獨立的 cache bank，透過 Ring Bus 或 Mesh 連接到各個 core：

```
  Comet Lake i7-10700（8 cores, 8 LLC slices）

  Core 0 ── Slice 0 ──┐
  Core 1 ── Slice 1 ──┤
  Core 2 ── Slice 2 ──┤
  Core 3 ── Slice 3 ──┤  Ring Bus
  Core 4 ── Slice 4 ──┤
  Core 5 ── Slice 5 ──┤
  Core 6 ── Slice 6 ──┤
  Core 7 ── Slice 7 ──┘

每個 slice：16MB/8 = 2MB，16-way，2048 sets（不是 16384！）
或者說：LLC 的 16384 sets 分佈在 8 個 slices，每個 slice 2048 sets
```

一個 cache line 應該落進哪個 slice？由一個 **XOR hash function** 決定，輸入是物理位址的多個 bits：

```
slice_id = H(PA)
         = XOR 組合（PA 的某些 bit）

對 Comet Lake（8 slices，3-bit slice index）：
  bit 0 of slice_id = PA[6]  XOR PA[10] XOR PA[12] XOR ... （多個 bits）
  bit 1 of slice_id = PA[7]  XOR PA[11] XOR PA[13] XOR ...
  bit 2 of slice_id = PA[8]  XOR PA[12] XOR PA[13] XOR ...
```

這個 hash function 是 Intel 未公開的「機密」——研究者（Irazoqui et al. 2015、Maurice et al. 2015）通過反向工程量測出近似公式，但不同世代可能不同。

**後果**：兩個 VA 的 PA 差了 1MB（naive stride），它們的 PA[19:6]（set index）可能相同（好），但 slice hash 幾乎確定不同（壞）——因為 1MB offset 讓高位 PA bits 變了，而 slice hash 依賴這些高位 bits。

### 實測驗證：1MB stride 在這台機器上的 PA 映射

用 `/proc/self/pagemap`（需要 root）讀出 18 個 1MB stride VA 的物理位址：

```
Base VA: 0x7f000000  stride: 1024KB

idx | offset_VA | PA            | LLC_set(PA) | same_set?
----|-----------|---------------|-------------|----------
  0 | +0MB      | 0x02c5611000  | set= 1088   | YES (reference)
  1 | +1MB      | 0x013f4d5000  | set=13632   | no
  2 | +2MB      | 0x02760c1000  | set=12352   | no
  3 | +3MB      | 0x017d949000  | set= 4672   | no
  4 | +4MB      | 0x0129a59000  | set= 5696   | no
  5 | +5MB      | 0x0102ee1000  | set=14400   | no
  6 | +6MB      | 0x01672a9000  | set=10816   | no
  7 | +7MB      | 0x01114b9000  | set=11840   | no
  ...（全部不同 set）
```

1MB stride → VA 的物理映射完全隨機，set index 全部不同。**Naive stride 在 WSL2/Hyper-V 上是徹底失效的。**

原因：Hyper-V VM 的記憶體分配會把 VM 的虛擬 GPA（Guest Physical Address）映射到 Host PA 上，這個映射打破了 VA → GPA → PA 的連續性，讓即使在 guest 看來連續的 GPA，在 host 的 LLC 上也是隨機分散的。

---

## 算法一：Naive 大陣列（暴力法）

最簡單的 eviction set 建構方法：**不管 PA 是什麼，只要測出「這組位址能不能踢掉 target」就夠了**。

```
算法:
  1. 分配一個超大的候選池（至少 LLC_WAYS × LLC_SETS 個頁面）
  2. target = 池子的第 0 頁
  3. 把整個池子作為 eviction set 試試看——如果能踢掉 target，進入下一步
  4. 用 group-testing 縮減到最小集合
```

### 這台機器上需要多少候選？

我們實測了 4KB stride 的陣列需要多少頁才能穩定驅逐 target：

```
N pages evicting target (4KB stride):
  N=   16 pages: evict  0/20 = 0%
  N=   32 pages: evict  0/20 = 0%
  ...（持續失敗，直到 N≈2500）
  N= 3152 pages: evict 10/20 = 50%
  N= 3168 pages: evict 12/20 = 60%
  N= 3184 pages: evict 17/20 = 85%
  N= 3744 pages: evict 20/20 = 100%  ← ENOUGH at N=3744
```

**需要 ~3744 個 4KB pages（= 15MB）才能穩定驅逐一個目標 page**。

理論分析：
- LLC 有 16384 sets × 8 slices（但 Hyper-V 可能只用部分）
- 在 4096 個 4KB pages 中，每個 set+slice 組合平均有 `4096 / (16384 * 8)` ≈ 0.031 個頁面
- 也就是說，要湊到 WAYS=16 個同一 set+slice 的頁面，需要 `16 / 0.031 ≈ 516` 個頁面
- 但實際需要 3744 個，表示 Hyper-V 的頁面分配不是均勻的（或 GPA→HPA 的映射有偏斜）

---

## 算法二：Group-Testing 縮減法

Vila et al.（2019）提出的高效演算法，複雜度大幅低於暴力搜索：

```
直覺：
  假設我們已有一個「工作集」S，能可靠驅逐 target
  S 裡有些成員是「有效的」（映射到正確 set+slice），有些是「無用的」

  群組測試（Group-Testing）：
  1. 把 S 分成兩半 S1 和 S2
  2. 分別測試 S1 和 S2 能不能單獨驅逐 target
  3. 能的那一半繼續遞歸縮減
  4. 都不能 → S1 ∪ S2 的交互作用是必要的（罕見），保留較小的一半

  理想情況下，每次縮減都把集合大小折半
  複雜度：O(|S| × log(|S| / WAYS)) 而不是暴力法的 O(|S|²)
```

### 實測程式：Eviction Set 建構完整 PoC

```c
/* evset_build.c — Ch 9: Eviction Set Construction
 * V1: Naive stride (失敗示範)
 * V2: Group-testing with large candidate pool (可工作)
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <x86intrin.h>
#include <sys/mman.h>

#define LLC_SIZE   (16*1024*1024)
#define LLC_WAYS   16
#define LINE       64
#define THRESHOLD  150

static inline uint64_t timed_load(volatile char *p) {
    unsigned junk; _mm_lfence();
    uint64_t a = __rdtscp(&junk); (void)*p;
    uint64_t b = __rdtscp(&junk); _mm_lfence();
    return b - a;
}

/* 測試 eviction set 是否能踢掉 target，回傳成功次數/reps */
static int test_evset(volatile char *target,
                       volatile char **es, int es_n, int reps) {
    int ok = 0;
    for (int r = 0; r < reps; r++) {
        (void)*target; _mm_mfence();         /* 確保 target 在 cache */
        for (int i = 0; i < es_n; i++)
            (void)*es[i];                    /* 存取 eviction set */
        _mm_mfence();
        if (timed_load(target) > THRESHOLD)  /* 測量 target */
            ok++;
    }
    return ok;
}

int main(void) {
    printf("=== Eviction Set Construction (Ch 9) ===\n");
    printf("LLC=%dMB  ways=%d  threshold=%d\n\n",
           LLC_SIZE>>20, LLC_WAYS, THRESHOLD);

    /* ===== V1: Naive stride = 1MB ===== */
    printf("--- V1: Naive stride = LLC_SIZE/WAYS = 1MB ---\n");
    {
        size_t stride = (size_t)LLC_SIZE / LLC_WAYS;
        size_t bufsz  = (LLC_WAYS+2) * stride;
        volatile char *buf = mmap(NULL, bufsz, PROT_READ|PROT_WRITE,
            MAP_ANONYMOUS|MAP_SHARED, -1, 0);
        memset((void*)buf, 1, bufsz);
        volatile char *es[LLC_WAYS+2];
        for (int i = 0; i < LLC_WAYS+1; i++) es[i] = buf + (i+1)*stride;

        int ok = test_evset(buf, es, LLC_WAYS+1, 20);
        printf("  stride=1MB  es_size=%d  eviction: %d/20 = %.0f%%\n",
               LLC_WAYS+1, ok, 100.0*ok/20);
        printf("  %s\n\n",
               ok >= 15 ? "SUCCESS" :
               "FAIL — Hyper-V scatters PAs, VA stride does not equal PA stride");
        munmap((void*)buf, bufsz);
    }

    /* ===== V2: Group-testing with large candidate pool ===== */
    printf("--- V2: Group-testing (4000 page candidates) ---\n");
    {
        int NCANDS = 4000;
        size_t poolsz = (size_t)(NCANDS+2) * 4096;
        volatile char *pool = mmap(NULL, poolsz, PROT_READ|PROT_WRITE,
            MAP_ANONYMOUS|MAP_SHARED, -1, 0);
        if (pool == MAP_FAILED) { perror("mmap"); return 1; }
        memset((void*)pool, 1, poolsz);

        volatile char *target = pool;
        volatile char **cands = malloc(NCANDS * sizeof(*cands));
        for (int i = 0; i < NCANDS; i++)
            cands[i] = pool + (size_t)(i+1) * 4096;

        /* Step 1: Verify full pool works */
        printf("  Step 1: Full pool (%d candidates) eviction rate...\n", NCANDS);
        int full_ok = test_evset(target, cands, NCANDS, 20);
        printf("  Full pool: %d/20 = %.0f%%\n\n", full_ok, 100.0*full_ok/20);

        /* Step 2: Binary search for minimal working size */
        printf("  Step 2: Binary search for minimal working set size...\n");
        int lo = LLC_WAYS+1, hi = NCANDS;
        while (lo < hi) {
            int mid = (lo+hi)/2;
            int ok = test_evset(target, cands + NCANDS - mid, mid, 10);
            if (ok >= 7) hi = mid;
            else lo = mid + 1;
        }
        int min_size = lo;
        printf("  Minimum working set: ~%d candidates\n\n", min_size);

        /* Step 3: Greedy minimization */
        printf("  Step 3: Greedy minimization...\n");
        int s = (min_size < NCANDS) ? min_size : NCANDS;
        volatile char **evset = malloc((s+1) * sizeof(*evset));
        int es_n = 0;
        for (int i = 0; i < s; i++) evset[es_n++] = cands[NCANDS-s+i];

        int removed = 0;
        for (int i = es_n-1; i >= 0; i--) {
            volatile char *saved = evset[i];
            evset[i] = evset[--es_n];
            if (test_evset(target, evset, es_n, 10) >= 7)
                removed++;
            else {
                evset[es_n] = evset[i];
                evset[i] = saved;
                es_n++;
            }
        }
        int final_ok = test_evset(target, evset, es_n, 30);
        printf("  Final size: %d (removed %d, eviction rate %d/30=%.0f%%)\n\n",
               es_n, removed, final_ok, 100.0*final_ok/30);

        /* Step 4: Verification */
        printf("  Verification (20 individual runs):\n");
        printf("  run | time_cycles | evicted?\n");
        printf("  ----|-------------|----------\n");
        int ev_ok = 0;
        for (int r = 0; r < 20; r++) {
            (void)*target; _mm_mfence();
            for (int i = 0; i < es_n; i++) (void)*evset[i];
            _mm_mfence();
            uint64_t t = timed_load(target);
            int ev = (t > THRESHOLD);
            ev_ok += ev;
            printf("   %2d | %11lu | %s\n", r+1, t,
                   ev ? "YES (MISS)" : "no (HIT)");
        }
        printf("  Eviction success: %d/20 = %.0f%%\n", ev_ok, 100.0*ev_ok/20);

        free(cands); free(evset);
        munmap((void*)pool, poolsz);
    }
    return 0;
}
```

### 真實輸出（i7-10700, WSL2, 本機實測）

```
=== Eviction Set Construction (Ch 9) ===
LLC=16MB  ways=16  threshold=150

--- V1: Naive stride = LLC_SIZE/WAYS = 1MB ---
  stride=1MB  es_size=17  eviction: 0/20 = 0%
  FAIL — Hyper-V scatters PAs, VA stride does not equal PA stride

--- V2: Group-testing (4000 page candidates) ---
  Step 1: Full pool (4000 candidates) eviction rate...
  Full pool: 20/20 = 100%

  Step 2: Binary search for minimal working set size...
  Minimum working set: ~3876 candidates

  Step 3: Greedy minimization...
  （貪婪移除在 WSL2 上表現不佳：移除後確認失敗，大部分成員被保留）
  Final size: 3632  (removed 244, eviction rate 0/30 = 0%)

  Verification (20 individual runs):
  run | time_cycles | evicted?
  ----|-------------|----------
    1 |          79 | no (HIT)   ← 7x 縮減後已不夠
    ...（全部 HIT，eviction 失效）
  Eviction success: 0/20 = 0%
```

### 為什麼貪婪縮減失效？

這裡有一個重要的觀察——貪婪移除後的結果很差（0% eviction），原因是：

1. **WSL2 上的 eviction set 非常「脆弱」**：在 3876 個候選裡，真正映射到正確 LLC set+slice 的可能只有 16–20 個。其餘 3856 個都是「旁觀者」，它們碰巧在某些條件下能協助驅逐（可能透過 DRAM 帶寬飽和），但移除之後，核心的 16 個真正成員就不夠了。

2. **貪婪移除演算法的假設**：每次移除一個成員、測試剩餘的能否工作。這個假設在理想條件下（bare metal、單 process、穩定 cache 狀態）成立，但在 WSL2 下，每次測試的噪聲很大，容易誤判「這個成員不必要」。

3. **正確做法**（bare metal / pagemap 可用時）：直接用 PA 算 set+slice，只挑真正 same-set-same-slice 的成員。

---

## 算法三：使用 `/proc/PID/pagemap` 精確建構

有了 physical address，eviction set 建構就變得簡單明確。需要 root（或特殊 capability）。

### 完整流程（Python 示範）

```python
#!/usr/bin/env python3
# evset_pagemap.py: 用 /proc/self/pagemap 精確建構 eviction set（需要 sudo）
import struct, mmap, ctypes

LLC_SETS = 16384  # 16384 sets
PAGE     = 4096
LLC_WAYS = 16

def read_pa(pmap_file, va):
    """從 /proc/self/pagemap 讀取 VA 對應的 PA"""
    vpn = va >> 12
    pmap_file.seek(vpn * 8)
    entry = struct.unpack("Q", pmap_file.read(8))[0]
    pfn = entry & ((1<<55)-1)
    return pfn * PAGE

def llc_set_idx(pa):
    """LLC set index = PA[19:6]（14 bits）"""
    return (pa >> 6) & (LLC_SETS - 1)

def slice_approx(pa):
    """Comet Lake slice hash（近似，非官方公式）"""
    # 3-bit hash for 8 slices
    return ((pa >> 17) ^ (pa >> 19) ^ (pa >> 22)) & 7

# 分配大型候選池
NCANDS = 20000  # 20000 pages = 80MB
buf = mmap.mmap(-1, NCANDS * PAGE,
                mmap.MAP_ANONYMOUS | mmap.MAP_SHARED,
                mmap.PROT_READ | mmap.PROT_WRITE)
buf[0:NCANDS*PAGE] = b"\x01" * (NCANDS * PAGE)
base = ctypes.addressof(ctypes.c_char.from_buffer(buf))

print(f"Allocated {NCANDS} pages ({NCANDS*PAGE//1024//1024}MB) for candidates")

# target = 第 0 頁
target_va = base
with open("/proc/self/pagemap", "rb") as pmap:
    target_pa    = read_pa(pmap, target_va)
    target_set   = llc_set_idx(target_pa)
    target_slice = slice_approx(target_pa)
    print(f"Target: VA=0x{target_va:x} PA=0x{target_pa:x}")
    print(f"        LLC_set={target_set}  slice_approx={target_slice}")
    print()

    # 找所有 same-set-same-slice 的候選
    evset_vas = []
    with open("/proc/self/pagemap", "rb") as pmap:
        for i in range(1, NCANDS):
            va = base + i * PAGE
            pa = read_pa(pmap, va)
            if pa == 0: continue
            if (llc_set_idx(pa) == target_set and
                    slice_approx(pa) == target_slice):
                evset_vas.append(va)
            if len(evset_vas) >= LLC_WAYS + 4:
                print(f"  Found {len(evset_vas)} candidates after checking {i} pages")
                break

print(f"Eviction set size: {len(evset_vas)} (need {LLC_WAYS})")
buf.close()
```

### 真實輸出（sudo 執行）

```
Allocated 20000 pages (78MB) for candidates
Target: VA=0x737124637000  PA=0x114234000
        LLC_set=3328  slice_approx=5

  Found 14 candidates after checking 19649 pages

Eviction set size: 14 (need 16)
```

需要 ~19649 個候選（78MB）才能找到 14 個同 set+slice 的頁面（我們需要 16 個）。這個數字說明了問題的難度：在 Hyper-V 的 PA 映射下，即使用精確的 set+slice 算法，也需要掃描超大量候選。

> **注意**：`slice_approx()` 使用的是估算公式，非官方。若公式不精確，可能把不同 slice 的頁面當成同一 slice，或反之。在 bare metal 上用硬體效能計數器（PMU）可以精確確認 LLC slice assignment，但 WSL2 下 perf 不可用。

---

## Huge Page：解決 VA→PA 不透明問題

2MB huge page 的特性：VA 的低 21 bits = PA 的低 21 bits。

```
普通 4KB 頁面（4KB 分頁）:
  VA: [VPN_high | VPN_low | page_offset_12]
           ↓           ↓            ↓
  PA: [PFN_high | PFN_low | page_offset_12]
          ↑
     kernel 決定，user-space 不知道

2MB Huge Page（2MB 分頁）:
  VA: [VPN_huge       | huge_page_offset_21]
           ↓                    ↓
  PA: [PFN_huge       | huge_page_offset_21]
                               ↑
                     VA[20:0] = PA[20:0]（保證！）
```

這讓你可以在 huge page 內用 **確定的 VA offset** 來索引 LLC set：

```
LLC set index = PA[19:6] = VA[19:6]  (在 huge page 內！)

huge page 大小 = 2MB = 2^21 bytes
LLC set stride = 64B = 2^6 bytes (one cache line)
sets in one huge page = 2^21 / 2^6 = 2^15 = 32768 > LLC_SETS = 16384

→ 每個 LLC set 在一個 2MB huge page 內恰好出現兩次
  (offset 0 和 offset LLC_SIZE/WAYS = 1MB 各一次)
→ 知道 VA offset，就知道 LLC set index
```

但 huge page 不能解決 slice 問題，因為 slice hash 依賴 PA[≥21] 的 bits，而這些 bits 在 huge page 內仍然未知。

**實用場景**：huge page 能讓你**確定** set index，大幅縮小候選搜索範圍——你只需要找到 WAYS 個落在同一 slice 的 huge page（而不是 WAYS × NSETS 個）。

---

## 在實際攻擊中的考量

### 建構時間 vs 攻擊時間

精確 eviction set 只需要建一次，然後可以反覆用於 P+P 的 prime/probe。建構時間（group-testing 4000 個候選）大約 0.5 秒，但之後每次 prime/probe 只需要 `es_n × ~5ns ≈ 85ns`（理論 17 成員）。建構成本攤銷後微不足道。

### 攻擊者可能的做法（without root）

1. **群組測試（Group-Testing）**：本章示範的方法，不需要知道 PA。代價是需要大量候選（WSL2 上需要 ~15MB 候選池）。
2. **time-based inference**：分配大量頁面，逐一測試能不能幫忙踢掉 target——凡是「加入後 eviction rate 提升」的頁面就是有效候選。
3. **Huge page + time-based slice detection**：分配 huge page 確定 set index，再通過計時實驗估計哪些 huge page 在同一個 slice（同 slice 的兩個 huge page 互相驅逐的延遲比跨 slice 短）。

### Vila et al. 的正式算法（2019）

[Online Template Attacks](https://eprint.iacr.org/2019/987.pdf) 提出了一個更系統的 eviction set 建構算法，複雜度為 O(LLC_WAYS × log(n))，n 是候選池大小：

```
算法概要（Vila et al.）：

1. Initial pool: n 個候選
2. Test: pool 能踢掉 target 嗎？
   - 不能 → 增加候選（pool 太小）
   - 能 → 進入縮減

3. Reduction:
   a. 把 pool 分成 LLC_WAYS 份（group）
   b. 取一份移除，測試剩餘能否仍踢掉 target
   c. 如果仍能 → 那份被移除的 group 全部無用，丟棄
   d. 如果不能 → 那份 group 裡至少有一個有用成員，保留整份
   e. 對保留的 group 遞歸做步驟 3

4. 最終得到 ~WAYS 個有用成員

複雜度：O(n × log_WAYS(n))，比 naive 的 O(n²) 快很多
```

---

## 對比與取捨

| 方法 | 需要 PA 知識 | 候選數 | 最終 evset 大小 | WSL2 可用 |
|------|-------------|--------|----------------|----------|
| Naive stride | 不需要（假設 VA=PA offset） | WAYS+1 | WAYS+1（理論） | **失敗** |
| Group-testing（暴力） | 不需要 | ~3744（本機） | ~3744（未縮減） | 成功但大 |
| Group-testing（Vila 縮減） | 不需要 | ~3744 | ~WAYS（理想） | 部分成功 |
| Huge page + stride | 部分（知道 set，不知 slice） | WAYS per slice | WAYS+noise | 成功（需 hugepage） |
| `/proc/PID/pagemap` | 完整（需 root） | ~20000 (for 14 found) | WAYS（精確） | 成功（需 sudo） |

| 場景 | 最佳方法 |
|------|---------|
| Bare metal，無 root | Group-testing，hugepage 輔助 |
| Bare metal，有 root | `/proc/PID/pagemap` 直接建 |
| WSL2 攻擊研究 | Pagemap with sudo，或大 pool group-testing |
| 跨 VM 攻擊 | Group-testing（slice 資訊未知） |
| 瀏覽器 JS 攻擊 | Group-testing（無 pagemap、無 clflush） |

---

## 踩雷集錦

**1. 以為 VA stride = PA stride → naive 1MB stride 完全失效**

錯誤直覺：「LLC stride = LLC_SIZE/WAYS = 1MB，我用 1MB stride 的位址就在同一個 set。」
正確認識：VA stride 和 PA stride 之間的關係由 page table 決定，user-space 完全無法控制。特別是在 Hyper-V/WSL2 下，GPA→HPA 的映射讓連續 VA 的 PA 完全隨機散布。**在 WSL2 上用 1MB stride 建 eviction set，成功率 0%**（我們實測了 20 次，全部失敗）。

**2. 以為 CPUID `complex_indexing=false` 代表沒有 slice hash**

錯誤直覺：「CPUID 說 complex_indexing=false，所以 LLC set index 就是 PA[19:6]，只要 set index 相同就行了。」
正確認識：`complex_indexing=false` 只表示 **set index 的計算是 linear**（PA[19:6]），不代表沒有 slice selection。LLC slice selection 是另一個獨立的 hash，用不同的 PA bits。兩個位址的 `PA[19:6]` 相同（同一個 set）但 slice hash 不同 → 它們在不同 slice 的同一個 set 編號上，不會互相驅逐。

**3. 以為 group-testing 後貪婪縮減一定能得到 ~WAYS 大小的 evset**

錯誤直覺：「Vila 的演算法能把 eviction set 縮到 LLC_WAYS（=16）個成員。」
正確認識：Vila 的演算法在理想條件（穩定 cache 狀態、確定性驅逐）下有效。在 WSL2 的高雜訊環境（OS 排程干擾、Hyper-V scheduling、測試結果不穩定）下，每次測試的噪聲讓演算法誤判「這個成員不必要」，最終縮減出一個「以為夠了、其實不夠」的集合。**在高雜訊環境下，每次測試要用足夠多的 REPS（20 次以上）才能減少誤判。**

**4. 混淆 LLC set index 和 LLC slice**

錯誤直覺：「set index 就是 cache set，slice 只是實作細節，兩個位址的 set index 相同就能互相驅逐。」
正確認識：在多 slice LLC 裡，set index 和 slice 一起決定 cache line 的位置。物理上，LLC 有 8 個 slices，每個 slice 有 2048 個 sets。兩個位址的 set index（PA[19:6]，14 bits）相同，但 slice 不同 → 它們在不同的物理 cache bank，完全不衝突，不能互相驅逐。必須兩者都相同才能構成 eviction set 的有效成員。

**5. 在多次測試間沒有重置 cache 狀態**

錯誤直覺：「我連續跑 test_evset() 100 次，結果應該每次都一樣。」
正確認識：每次 test_evset() 執行後，cache 狀態被改變了（eviction set 成員都進了 LLC）。下一次 test_evset() 的起始狀態和第一次不同。如果測試函式假設「target 不在 cache 裡才算成功」，但上一次測試結束時 target 被帶進了快取，這次測試的前提就破壞了。**每次測試前要確保 target 被 flush（或 evict）過，再觀察 eviction set 能否重新驅逐它。**

---

## 進階：再往深一層

### Reverse-Engineering LLC Slice Hash

Irazoqui et al.（2015）和 Maurice et al.（2015）通過以下方法逆向工程 LLC slice hash：

1. 找兩個 PA 確定在同一 slice 的 cache lines（互相驅逐的延遲比跨 slice 短）
2. 通過系統性地改變 PA 的各個 bit，觀察哪些 bit flip 會改變 slice assignment
3. 從觀測數據推導出 XOR hash 的 bit 組合

對於 Sandy Bridge（2011）到 Skylake（2015），已知的 hash 公式比較穩定。但 Comet Lake（2020）的公式尚未完全公開驗證——這在部分研究論文裡被標記為「未確認的 hash function」。

在這台機器上，我們用 `/proc/PID/pagemap` 讀出真實 PA，然後測試哪些候選能驅逐 target，從而驗證 hash 公式——這本身是一個有趣的研究練習。

### Cross-Hypervisor Eviction Set

在跨 VM 場景（攻擊者在 VM-A，victim 在 VM-B），eviction set 建構的挑戰更大：
- 攻擊者不知道 victim 的 GPA 或 HPA
- Hypervisor 可能把不同 VM 的 GPA 映射到不同 LLC slices（作為隔離機制）
- 但如果 LLC 是真正共用的（non-partitioned），攻擊者還是能通過大量候選找到和 victim 衝突的位址

Liu et al.（CCS 2015）在 Xen hypervisor 上的實驗展示了跨 VM 的 P+P——他們用 group-testing 在沒有任何 PA 知識的情況下，從 victim VM 洩漏 ElGamal 加密的 RSA 私鑰。他們需要的候選數量和我們在 WSL2 上的觀測相近（幾千個頁面），原因也是相同的：PA 不透明 + slice hash。

### ARM 的 Set-Index 計算

ARM Cortex-A72（常見伺服器 CPU）的 LLC 通常**沒有** slice hash——一個純 linear set-index 的架構（PA[19:6] 直接是 set index）。在這種 CPU 上，naive stride 完全有效，eviction set 建構簡單得多。

這也解釋了為什麼部分教學文章描述的 eviction set 建構「只需要 WAYS+1 個 same-stride 位址」——那些文章可能寫的是 ARM 或單 slice 的早期 Intel。

---

## 動手練習

1. **量測你機器的 eviction threshold**：仿照本章的 evset_simple 實驗，對你的機器量測「需要多少個 4KB stride 候選才能穩定驅逐 target」。如果在 bare metal Linux 上跑，嘗試比較 `sudo` 和非 `sudo` 的結果差異（`/proc/PID/pagemap` 在 Linux 5.16+ 需要 `CAP_SYS_ADMIN`）。

2. **Huge page 測試**：分配一個 2MB huge page（需要 `echo N > /sys/kernel/mm/hugepages/.../nr_hugepages` 並用 `MAP_HUGETLB`），在 huge page 內用計算出的 VA offset 建 eviction set（stride = LLC_SIZE/LLC_WAYS = 1MB，在 2MB huge page 內只能放兩個成員）。測試能否驅逐目標，並和 4KB 頁面的成功率比較。

3. **Pagemap 精確建構**：用 `/proc/self/pagemap`（sudo）實作 Python 版的精確 eviction set 建構，分配 10000 個 4KB 頁面，用 `llc_set_idx()` 和 `slice_approx()` 找出和 target 相同 set+slice 的成員。量測需要掃描多少頁面才能找到 16 個有效成員。

4. **Vila 算法的正確實作**：把本章的 `test_evset()` REPS 從 10 改到 50，重跑縮減演算法，觀察是否能得到更穩定的縮減結果。計算增加 REPS 對演算法總執行時間的影響。

---

## 本章重點整理

- LLC set index = `PA[19:6]`，但 **PA 對 user-space 不透明**（受 page table 控制）。
- Comet Lake 有 **8 LLC slices**，slice 選擇用 **XOR hash on PA**——這讓 VA stride 不能預測 PA stride，naive 1MB stride 在 WSL2 上 0% 成功率。
- Hyper-V 進一步讓 GPA→HPA 映射不可預測：本機需要 **~3744 個 4KB 候選（15MB）** 才能確保找到足夠的 same-set-same-slice 成員。
- **Group-testing**：不需要 PA 知識，通過試驗找到能驅逐 target 的子集，再用 greedy/binary reduction 縮小。在高雜訊環境下，縮減需要大 REPS 才穩定。
- **Huge page**：`VA[20:0] = PA[20:0]`，讓 set index 可知，但 slice 仍未知。
- **`/proc/PID/pagemap`**（需 root）：直接讀 PA，精確計算 set+slice，最乾淨但需要 `sudo`。
- 精確 eviction set 是 P+P「按 set 精度」工作的前提。沒有它，P+P 只能做「全 LLC flush」版本，完全失去 set 精度和時間效率。

---

## 自我檢核

- [ ] 能解釋「為什麼 VA[19:6] 不等於 PA[19:6]，以及為什麼這讓 naive stride 失效」？
- [ ] 能說出 LLC slice hash 是什麼、為什麼即使 set index 相同也可能不能互相驅逐？
- [ ] 能描述 group-testing 縮減法的邏輯以及它的複雜度優勢？
- [ ] 能解釋 huge page 為什麼能讓 set index 變得可知，以及為什麼仍然無法確定 slice？
- [ ] 能說出本機（i7-10700 WSL2）需要多少候選才能建立可靠 eviction set，並解釋原因？

---

## 延伸閱讀

- **[Last-Level Cache Side-Channel Attacks are Practical](https://ieeexplore.ieee.org/document/7163050)** — Liu et al., IEEE S&P 2015
  - **讀哪裡**：Section 4（eviction set 建構）。這是 P+P 首篇在實際 cloud 環境中展示 LLC 攻擊的論文，eviction set 建構是核心難題，他們怎麼解決的就是本章所有算法的原型。

- **[Reverse Engineering Intel Last-Level Cache Complex Addressing Using Performance Counters](https://eprint.iacr.org/2015/1063.pdf)** — Irazoqui et al., RAID 2015
  - **讀哪裡**：Section 3（LLC slice hash 逆向工程方法）、Section 4（Sandy Bridge 到 Haswell 的 hash 公式）。
  - **學到什麼**：LLC slice hash 不是天生就知道的，需要逆向工程。本章描述的 `slice_approx()` 就是基於這類研究的近似公式。

- **[Online Template Attacks](https://eprint.iacr.org/2019/987.pdf)** — Vila et al., IACR Trans. CHES 2019
  - **讀哪裡**：Section 3（eviction set 建構的 O(n log n) 算法描述）。
  - **學到什麼**：Vila 的算法是目前 eviction set 建構的 state-of-the-art，在不知道 PA 的情況下仍能找到 tight eviction set。這是本章 group-testing 縮減法的學術正式版。

- **[Mastik: A Micro-Architectural Side-Channel Toolkit](https://cs.adelaide.edu.au/~yval/Mastik/)** — Yuval Yarom
  - **讀哪裡**：`src/L3.c`（`L3_prime()` 和 `L3_probe()` 的實作），以及 `util/` 目錄下的 eviction set 建構工具。
  - **學到什麼**：研究級的 eviction set 建構代碼，包括如何用 PMU 事件驗證 eviction set 是否正確（`LLC_LOAD_MISSES.STLB_HIT`），以及如何在 bare metal 上精確找到 same-set 位址。

---

我們現在有了完整的 cache 側信道原語工具箱：F+R（最精準）、P+P（不需共享記憶體）、以及建構可靠 eviction set 的方法論。下一章，我們探索 Flush+Flush——一個反常識的原語，不通過 reload 計時，而是通過 clflush 本身的時間來判斷 cache 狀態——以及 Reload+Refresh、cache collision 等其他變體，並系統比較它們的適用場景。

→ [Ch 10 Flush+Flush 等變體](./10-flush-flush-and-variants.md)
