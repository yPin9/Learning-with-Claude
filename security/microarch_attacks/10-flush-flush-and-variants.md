# Ch 10 — Flush+Flush 等變體

> **目標**：F+R（Flush+Reload）是快取側通道的主線，但它有個致命弱點——reload 那步會製造大量 LLC miss，現代 HPC 偵測器（Ch 33）一眼就能抓到。這章介紹三個更隱蔽或更快的替代武器：Flush+Flush（F+F）、Prime+Abort（TSX 通道）、Evict+Time。讀完你會清楚每種變體的訊號來源、適用場景，以及各自的死穴——而不只是背一張名詞表。

---

## 為什麼 F+R 不夠隱蔽？

回頭看 F+R 的攻擊循環：

```
攻擊者                          victim
  │  clflush(target)              │
  │  ───────────────────────────► │  （踢出）
  │                               │
  │                               │  存取 target
  │                               │  → line 被載入快取
  │  timed_access(target)         │
  │  讀它，計時                   │
  │  快 → victim 存取過           │
  │  慢 → victim 沒存取           │
```

這個設計有三個可觀測特徵，每一個都是偵測鉤：

1. **持續的 LLC miss**：reload 那步每次都得走一趟 DRAM（或下層 cache）——攻擊者自己製造了大量 Last-Level Cache miss。用 `perf stat -e LLC-load-misses` 或 Intel PT 任何一個 HPC 偵測框架都能在幾秒內抓到異常。

2. **`clflush` 的頻率**：某些 OS 或 hypervisor 可以監控 `clflush` 的執行密度（它是特權指令在某些虛擬化環境下會 VM exit）。

3. **記憶體存取樣式**：反覆 probe 同一組位址——記憶體存取的時序樣式本身就是特徵。

F+F 的切入點是第一個弱點：**能不能完全不做 reload，就把「target 有沒有在快取裡」的資訊讀出來**？

---

## Flush+Flush：用 clflush 自己的執行時間差洩漏

### 關鍵洞察

`clflush` 不是等時操作。它對「已在快取的 cache line」和「不在快取的 cache line」執行時，耗費的時間不同：

```
情況 A：target 在快取裡                情況 B：target 不在快取裡
┌──────────────────────────────┐       ┌──────────────────────────────┐
│  clflush(target)             │       │  clflush(target)             │
│                              │       │                              │
│  CPU 需要：                  │       │  CPU 需要：                  │
│  1. 找到這條 line            │       │  1. 查快取階層               │
│  2. 若 dirty → 寫回          │       │  2. 找不到 → no-op 快速返回  │
│  3. 從各 cache 層移除        │       │                              │
│  → 較短（約 ~100–130 cycles）│       │  → 較長（約 ~150–180 cycles）│
└──────────────────────────────┘       └──────────────────────────────┘
```

等一下——這個直覺是對的，但具體哪個快、哪個慢，**和 F+R 的直覺方向相反**，而且訊號差距小很多（可能只差 20–50 cycles，而 F+R 的 hit/miss 差 200 cycles）。讓我們先建立正確的直覺再看數字。

### 為什麼 cached line 的 clflush 「可能」更快？

F+F 論文（Gruss 2016）的量測在 Intel Sandy Bridge 上顯示：對已在快取的 line 執行 clflush，時間**比未快取時短約 10–40 cycles**。這是因為：

- 已快取的 line：clflush 在 L1/L2 就找到了，快速 invalidate 後返回
- 未快取的 line：clflush 需要廣播一個 snoop 訊息給快取階層中所有層，確認「確實不在裡面」才能返回——這個確認過程本身有 latency

但這個差距極度依賴 CPU 微架構。在某些架構上差距幾乎為零；在另一些上方向甚至相反（未快取反而更快，因為 miss 快速返回）。**這是 F+F 的根本弱點：訊號小、與硬體強耦合**。

### F+F 攻擊循環

```
攻擊者                          victim
  │  clflush(target)              │   （第一次，把 line 踢出）
  │  ───────────────────────────► │
  │                               │
  │                               │  存取 target（或沒有）
  │                               │
  │  t0 = rdtscp()                │
  │  clflush(target)              │   ← 這是訊號來源！不是 reload！
  │  t1 = rdtscp()                │
  │  delta = t1 - t0              │
  │                               │
  │  delta 小 → victim 存取過     │   （line 在快取，clflush 快）
  │  delta 大 → victim 沒存取     │   （line 不在快取，clflush 慢）
```

注意：**攻擊者全程不讀 target**。沒有 load，沒有 cache miss——這就是 F+F 的隱蔽性來源。

---

## 真跑：量 clflush 對兩種狀態的時間差

以下程式測量同一條 cache line 在兩種狀態下執行 `clflush` 的時間差。這在 i7-10700 + WSL2 Ubuntu 22.04 上可以直接編譯執行。

```c
/* ff_timing.c
 * 量 clflush 在 cached vs. uncached 兩種狀態下的執行時間差
 * 編譯：gcc -O0 -o ff_timing ff_timing.c
 * 執行：taskset -c 2 ./ff_timing
 */
#include <x86intrin.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CACHE_HIT_THRESHOLD 150
#define ROUNDS              200000
#define WARMUP              1000

/* 全域 probe array，避免被放在 stack 上造成 page fault 噪音 */
static volatile char probe[4096 * 16];

/*
 * 計時一次 clflush 的執行時間。
 * 注意：這裡計時的是 clflush 本身，不是存取。
 */
static inline uint64_t time_clflush(volatile char *p)
{
    unsigned junk;
    _mm_mfence();                            /* 確保前面的存取都完成 */
    uint64_t t0 = __rdtscp(&junk);
    _mm_clflush((void *)p);                  /* 計時這個！ */
    _mm_mfence();                            /* 不讓後面的指令跑到前面 */
    uint64_t t1 = __rdtscp(&junk);
    return t1 - t0;
}

int main(void)
{
    volatile char *target = &probe[4096];    /* 選中間一條 line，遠離邊界 */

    uint64_t sum_cached   = 0;
    uint64_t sum_uncached = 0;
    uint64_t cnt_cached   = 0;
    uint64_t cnt_uncached = 0;

    /* warmup：讓 CPU 進入穩定狀態，丟棄前 WARMUP 次結果 */
    for (int i = 0; i < WARMUP; i++) {
        (void)*target;
        time_clflush(target);
        time_clflush(target);
    }

    for (int r = 0; r < ROUNDS; r++) {
        uint64_t t_cached, t_uncached;

        /* --- 情況 A：line 在快取裡 ---
         * 先 touch（保證在快取），再計時 clflush */
        _mm_mfence();
        (void)*target;                       /* touch：把 line 載入 L1 */
        _mm_mfence();
        t_cached = time_clflush(target);     /* 計時：此時 line 在快取 */

        /* --- 情況 B：line 不在快取裡 ---
         * line 已被上一個 clflush 踢出；不 touch，直接計時 clflush */
        _mm_mfence();
        /* target 此時已不在快取（剛被上面的 clflush 踢出） */
        t_uncached = time_clflush(target);   /* 計時：此時 line 不在快取 */

        /* 過濾離群值（超過 500 cycles 可能是中斷或 OS 干擾） */
        if (t_cached < 500 && t_uncached < 500) {
            sum_cached   += t_cached;
            sum_uncached += t_uncached;
            cnt_cached++;
            cnt_uncached++;
        }
    }

    printf("=== Flush+Flush 時間差量測 (i7-10700 WSL2) ===\n");
    printf("樣本數（有效）：%lu 次（共 %d 次）\n",
           cnt_cached, ROUNDS);
    printf("\n");
    printf("情況 A — clflush on CACHED line   : 平均 %lu cycles\n",
           cnt_cached   ? sum_cached   / cnt_cached   : 0);
    printf("情況 B — clflush on UNCACHED line : 平均 %lu cycles\n",
           cnt_uncached ? sum_uncached / cnt_uncached : 0);
    printf("\n");

    long diff = (long)(cnt_cached ? sum_cached / cnt_cached : 0)
              - (long)(cnt_uncached ? sum_uncached / cnt_uncached : 0);
    printf("差值（A - B）：%ld cycles\n", diff);
    printf("\n");

    if (diff < 0) {
        printf("結果：cached clflush 比 uncached 快 %ld cycles\n", -diff);
        printf("→ F+F 可行：cached 時 clflush 執行更快（符合 Gruss 2016）\n");
    } else if (diff > 0) {
        printf("結果：cached clflush 比 uncached 慢 %ld cycles\n", diff);
        printf("→ 此微架構上 clflush 方向相反（仍可用，但門檻要反過來）\n");
    } else {
        printf("結果：幾乎無差異——F+F 在此機器/環境上可能不可行\n");
    }

    printf("\n參考：Ch0 校準值 HIT=24, MISS=244, THRESHOLD=%d\n",
           CACHE_HIT_THRESHOLD);
    return 0;
}
```

### 理論預期輸出（未在 WSL2 真跑，基於文獻數字）

> **說明**：以下輸出是依據 Gruss 2016（F+F 原始論文）及 Intel Comet Lake 微架構文件推算的**理論預期值**。實際在 i7-10700 WSL2 跑出來的數字會因 Hyper-V 虛擬化噪音、OS 干擾、prefetcher 狀態而有差異，不保證精確吻合。

```
=== Flush+Flush 時間差量測 (i7-10700 WSL2) ===
樣本數（有效）：197843 次（共 200000 次）

情況 A — clflush on CACHED line   : 平均 112 cycles
情況 B — clflush on UNCACHED line : 平均 165 cycles

差值（A - B）：-53 cycles

結果：cached clflush 比 uncached 快 53 cycles
→ F+F 可行：cached 時 clflush 執行更快（符合 Gruss 2016）

參考：Ch0 校準值 HIT=24, MISS=244, THRESHOLD=150
```

注意這個數字與 Ch 0 的 HIT/MISS 分佈的對比：

| 方法 | 「有在快取」信號 | 「沒在快取」信號 | 差距 |
|------|----------------|----------------|------|
| F+R（timed reload） | ~24 cycles | ~244 cycles | ~220 cycles |
| F+F（timed clflush） | ~112 cycles | ~165 cycles | ~53 cycles |

F+F 的訊號差距只有 F+R 的四分之一——這就是為什麼 F+F 需要更多樣本、更乾淨的環境，才能建出可靠的 covert channel。**隱蔽性換來的是訊號品質下降**，這個取捨要記在心裡。

### F+F 的門檻設定

F+F 的偵測邏輯與 F+R 相同，只是訊號來源和門檻值不同：

```c
/* F+F probe：計時 clflush 本身，不 reload */
static inline int ff_probe(volatile char *p)
{
    uint64_t t = time_clflush(p);
    /*
     * 若 t < F+F 門檻 → line 在快取（clflush 快）→ victim 存取過 → 回傳 1
     * 若 t ≥ F+F 門檻 → line 不在快取（clflush 慢）→ victim 沒存取 → 回傳 0
     *
     * 門檻要在你的機器上跑 ff_timing.c 後取 (A+B)/2
     * 理論預期：(112 + 165) / 2 ≈ 138 cycles
     */
    return t < 138;  /* 依你的機器調整！ */
}
```

---

## Prime+Abort：完全不計時的 TSX 通道

> **未實測，理論預期**：i7-10700 Comet Lake 的 TSX 已被 Intel Microcode 更新（2019–2021 年，修補 TAA/PLATYPUS 漏洞）停用。以下內容基於 Disselkoen 2017 原始論文，附 `#ifdef __RTM__` 保護，不需要 TSX 硬體也能讀、能理解原理。

### 概念：用交易記憶體的「中止」作為側通道

Intel TSX（Transactional Synchronization Extensions）的 RTM（Restricted Transactional Memory）允許你把一段程式碼包進一個交易（transaction）。交易執行期間，若有其他執行緒修改了同一個 cache set，這個交易會**中止（abort）**。

Prime+Abort 把這個中止當成偵測訊號：

```
攻擊者                              victim
  │  (1) Prime eviction set E        │   把 target set 填滿自己的 lines
  │                                  │
  │  (2) XBEGIN → 開始交易           │
  │      如果交易沒中止...            │
  │      ... 代表 E 還在快取         │
  │      ... 代表 victim 沒碰 target │
  │      XEND → commit               │
  │                                  │   victim 存取 target
  │                                  │   → target 被載入 → 踢出 E 的 line
  │                                  │   → TSX 偵測到 E 的 cache line 被驅逐
  │                                  │   → 標記這個交易「需要中止」
  │  (3) XBEGIN → 再開始一個交易     │
  │      ... 交易中止！              │   ← 這就是洩漏！
  │      → victim 存取過 target      │
```

整個過程**沒有任何計時**。中止 = 1，不中止 = 0，訊號是離散的布林值。這讓 Prime+Abort 完全不受計時噪音影響，也不受 TSC 精度限制。

### 概念程式碼（附 RTM 保護）

```c
/* prime_abort_concept.c
 * 理論展示，需要 TSX 硬體（i7-10700 上 TSX 已被禁用）
 * 編譯：gcc -O0 -mrtm -o prime_abort prime_abort_concept.c
 *
 * 未實測，理論預期（基於 Disselkoen 2017）
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>

#ifdef __RTM__
#include <immintrin.h>   /* XBEGIN, XEND, XABORT, _XBEGIN_STARTED */
#endif

/* eviction set（從 Ch 9 建出來的，這裡用假的佔位符） */
static volatile char evset[64 * 16];  /* 16 個 cache lines，與 target 同 set */

/*
 * 回傳 1 = victim 存取過 target（交易中止）
 * 回傳 0 = victim 沒存取（交易 commit）
 */
static int prime_abort_probe(void)
{
#ifdef __RTM__
    /* Step 1: Prime — 把 eviction set 全部載入快取 */
    for (int i = 0; i < 16; i++)
        (void)evset[i * 64];

    /* Step 2: 開始交易，等 victim 行動 */
    int aborted = 0;
    unsigned status = _xbegin();   /* RTM 交易開始 */

    if (status == _XBEGIN_STARTED) {
        /*
         * 交易內：讀 eviction set 的每條 line（製造 TSX 追蹤依賴）
         * 若 victim 把 target 載入，踢出了 evset 其中一條 line，
         * 交易會在這裡中止，跳到 else 分支
         */
        for (int i = 0; i < 16; i++)
            (void)evset[i * 64];   /* 讀，產生 TSX read-set 追蹤 */

        _xend();   /* commit，代表 evset 沒被動 → victim 沒存取 target */
        aborted = 0;
    } else {
        /* 中止！代表 victim 存取了 target，踢出了 evset → 回報 1 */
        aborted = 1;
    }

    return aborted;
#else
    fprintf(stderr, "RTM not available (TSX disabled by microcode)\n");
    return -1;
#endif
}

int main(void)
{
    printf("=== Prime+Abort 概念展示 ===\n");
#ifdef __RTM__
    printf("RTM 可用（罕見）\n");
    int r = prime_abort_probe();
    printf("probe 結果：%d\n", r);
#else
    printf("RTM 不可用——i7-10700 TSX 已被 microcode 停用\n");
    printf("（Intel TSX 在 2021 年後大量型號被 microcode 關掉，\n");
    printf(" 包括 CVE-2019-11135 TAA 和後續 PLATYPUS 修補。）\n");
    printf("在舊 Skylake/Broadwell 上可嘗試跑完整版。\n");
#endif
    return 0;
}
```

### 為什麼 Prime+Abort 更快？

P+Abort 不需要 Prime+Probe 的逐 line reload probe 步驟——一個交易的 commit/abort 就涵蓋整個 eviction set 的狀態，相當於並行 probe 多條 line。Disselkoen 2017 報告的 channel 頻寬是 1.5 Mbit/s，比同期 F+F 的 ∼500 Kbit/s 快三倍。

---

## Evict+Time：最古老的無共享記憶體方式

Evict+Time 出自 Osvik 2006（AES cache attack 的奠基論文），是所有快取側通道裡**前提條件最寬鬆**的一種——它不需要 `clflush`、不需要共享記憶體，甚至不需要精準的計時，只需要能**呼叫 victim function**。

### 攻擊邏輯

```
時序：
  T1: 呼叫 victim()  → 量執行時間 baseline = T
  T2: 呼叫 victim()  → 量執行時間 ≈ T（快取熱了）
  T3: evict(S)       → 踢出某個 cache set S 的所有 lines
  T4: 呼叫 victim()  → 量執行時間

  如果 T4 >> T2：代表 victim 使用了 set S 的資料——洩漏！
  如果 T4 ≈ T2：代表 victim 沒用 set S
```

### 適用場景：AES 的 T-table 攻擊

AES 的 table-based 實作（OpenSSL 早期版本）內部用 4 個 256-entry 的 lookup table（T0–T3）。key 的不同 byte 會讓 AES 存取 T0–T3 的不同 index。Evict+Time 的做法是：

1. 讓 AES 跑幾次，讓 T-table 全部在快取（baseline 時間短）
2. 用 eviction set 踢出 T0 的某個 cache set（對應 T0 的某些 index）
3. 再讓 AES 跑一次，量時間
4. 若時間變長：代表這次 AES 存取了剛才被踢的那些 index——洩漏了 key 的部分資訊

### 代碼框架（概念展示）

```c
/* evict_time_concept.c
 * Evict+Time 框架示意——不含真正的 AES victim 或 eviction set 建構
 * （eviction set 建構見 Ch 9；AES 攻擊細節見 Ch 11）
 * 編譯：gcc -O0 -o evict_time evict_time_concept.c
 */
#include <x86intrin.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* 假的 victim function：存取一個 lookup table */
static uint8_t fake_table[4096];
static volatile uint8_t sink;   /* 防止編譯器優化掉存取 */

static void victim_function(uint8_t key_byte)
{
    /* 模擬 AES T-table lookup：key 決定存取哪個 index */
    sink = fake_table[key_byte * 16];  /* 每個 entry 佔一個 cache line */
}

/* 量 victim_function 的執行時間 */
static uint64_t time_victim(uint8_t key_byte)
{
    unsigned junk;
    _mm_mfence();
    uint64_t t0 = __rdtscp(&junk);
    victim_function(key_byte);
    _mm_mfence();
    uint64_t t1 = __rdtscp(&junk);
    return t1 - t0;
}

/* 模擬踢出某個 index 對應的 cache set（真正的實作需要 Ch 9 的 eviction set） */
static void evict_index(int idx)
{
    /* 簡化版：直接 clflush（真實 Evict+Time 不能用 clflush 時要換成 eviction set） */
    _mm_clflush(&fake_table[idx * 16]);
}

int main(void)
{
    memset(fake_table, 0xAB, sizeof(fake_table));

    /* Step 1: warmup，讓 table 全部在快取 */
    for (int i = 0; i < 256; i++)
        victim_function((uint8_t)i);

    uint8_t target_key = 42;

    /* Step 2: baseline 時間（快取熱） */
    uint64_t t_baseline = time_victim(target_key);

    /* Step 3: 踢出 target_key 對應的 cache set */
    evict_index(target_key);

    /* Step 4: 再量一次 */
    uint64_t t_after_evict = time_victim(target_key);

    printf("=== Evict+Time 概念展示 ===\n");
    printf("baseline 時間（快取熱）：%lu cycles\n", t_baseline);
    printf("evict 後時間：           %lu cycles\n", t_after_evict);
    printf("\n");

    if (t_after_evict > t_baseline + 50) {
        printf("差值 %lu cycles → victim 存取了被踢的 cache set\n",
               t_after_evict - t_baseline);
        printf("→ key byte 資訊洩漏！\n");
    } else {
        printf("差值不顯著（%lu cycles）→ victim 可能沒存取那個 set\n",
               t_after_evict > t_baseline ? t_after_evict - t_baseline : 0);
    }

    return 0;
}
```

### Evict+Time 的根本限制

Evict+Time 量的是**整個 victim function 的執行時間**，不是某條 line 的狀態。這帶來幾個問題：

1. **粒度太粗**：function 裡有很多快取存取，被踢掉一個 set 只影響一小部分時間，訊號被稀釋
2. **需要主動呼叫 victim**：攻擊者必須有辦法觸發 victim 執行，不適合純被動竊聽
3. **雜訊大**：function 的執行時間受到各種因素影響（分支預測、指令快取等），不只快取資料
4. **現代 AES 已有 AES-NI**：硬體 AES 指令不用 T-table，Evict+Time 的 AES 攻擊場景已大幅萎縮

---

## 對比與取捨

| 特性 | Flush+Reload | Flush+Flush | Prime+Abort | Evict+Time |
|------|-------------|-------------|-------------|------------|
| **訊號來源** | reload 時間（24 vs 244 c） | clflush 時間（~112 vs ~165 c） | TSX 交易中止（布林） | victim 函式總時間 |
| **訊號強度** | 強（~220 c 差距） | 弱（~50 c 差距） | 完美（離散） | 極弱（稀釋） |
| **製造 LLC miss** | 是 | **否** | **否** | 否（但呼叫 victim 時有） |
| **HPC 偵測難度** | 容易（LLC miss 暴增） | 難（無 miss） | 難（無計時，無 miss） | 中等 |
| **需要共享記憶體** | 是 | 是 | **否** | **否** |
| **需要 clflush** | 是 | 是 | **否** | **否**（可用 eviction set） |
| **需要特殊硬體** | 否 | 否 | TSX（大多已禁） | 否 |
| **需要呼叫 victim** | 否（被動竊聽） | 否 | 否 | **是** |
| **攻擊速度** | 中 | 快 | 很快 | 慢 |
| **實作難度** | 低 | 低 | 高（需建 eviction set + TSX） | 中 |
| **典型場景** | 跨行程 spy | 防偵測環境 | 雲端無共享記憶體 | AES T-table（歷史）|

最重要的一行是「製造 LLC miss」——這是現代偵測器最常用的鉤子。F+F 和 P+Abort 都繞過了它，代價是訊號品質下降（F+F）或硬體依賴（P+Abort）。

---

## 踩雷集錦

**雷一：F+F 的方向在某些機器上是反的**

Gruss 2016 的實驗（Sandy Bridge）顯示 cached clflush 更快。但這不是規律，不是規格——是 CPU 內部實作的副作用。在 Skylake+、Zen 2 上，某些情況下 uncached clflush 反而更快（因為 miss 快速返回路徑比 hit + invalidate 還短）。**跑 `ff_timing.c` 前不要假設方向**，讓數據告訴你。

**雷二：`mfence` 擺錯位置，量到的是亂序執行後的時間**

計時 clflush 最常見的 bug：把 `_mm_mfence()` 忘在 clflush 之後。沒有 mfence，`rdtscp` 和 `clflush` 的亂序執行可能讓你量到負數或幾乎為零的時間差——debugger 看起來正常，數字就是不對。記得在 `__rdtscp(&junk)` 前後都要圍 mfence 或 lfence。

**雷三：F+F 的 channel 在 WSL2 下訊號更差**

Hyper-V 的 VM exit 和虛擬化層會在隨機時間點打斷你的量測，把原本 50 cycles 的差距淹沒在 200+ cycles 的噪音裡。在 WSL2 跑 F+F 需要：(a) 大量重複取中位數、(b) 過濾離群值（>500 cycles）、(c) 跑在固定核心（`taskset -c 2`）。就算做到這些，訊號仍然比原生 Linux 差。

**雷四：Prime+Abort 在現代 Intel 上幾乎必然失敗**

TSX 從 2019 年 TAA（Transactional Asynchronous Abort，CVE-2019-11135）修補後，Intel 用 microcode 更新在大多數 CPU 上直接關掉了整個 TSX 功能。`cpuid` 回報 TSX 旗標為 0，`XBEGIN` 永遠返回 abort 狀態。別浪費時間 debug 程式碼——先用 `cpuid | grep -i tsx` 確認硬體。

**雷五：Evict+Time 的計時包含指令快取效果**

victim function 第一次呼叫（指令快取冷）比第二次慢，這個差距可能比你試圖量的資料快取差距還大。確保 warmup 夠多次，讓指令快取熱透再開始計時，否則你量的是指令 miss 而不是資料 miss。

---

## 進階：再往深一層

**F+F 的實際頻寬**：Gruss 2016 報告在真實 AES-128 加密場景下，F+F 能以 496 KB/s 的速率傳輸 covert channel 資訊，而 F+R 是 373 KB/s——F+F 更快的原因是 probe 一次只需要一個 `clflush`，省掉了 reload 的 DRAM latency。雖然 F+F 訊號弱，但每個訊號採樣的時間也短，整體頻寬反而更高。

**Prime+Abort 的多集合同時偵測**：標準 Prime+Probe 每次只能偵測一個 cache set（要逐 set 輪流 probe）。P+Abort 可以把多個 eviction set 同時放進同一個交易的 read-set，一次交易涵蓋多個 set——這讓攻擊者可以在一個 RTM 交易週期內同時監控 AES 的多個 T-table set。

**F+F 的反偵測應用**：一些惡意軟體（APT 框架）已有把 F+F 作為隱蔽 exfiltration channel 的研究案例——在被 EDR 監控的環境裡，製造 LLC miss 的 covert channel 幾乎立刻被告警；F+F 因為沒有 miss，能在這種環境下存活更久。理解攻擊者的選擇，才能設計正確的偵測策略（Ch 33 會回頭談這個）。

**Evict+Time 的現代復活**：雖然 AES-NI 消滅了 T-table 場景，但 Evict+Time 的框架在 JavaScript JIT 引擎的快取攻擊中有復活跡象——不能用 clflush（瀏覽器沙箱封了），只能用 eviction set，而且只能量 JS 函式的執行時間。這讓 Evict+Time 在「有限環境」下仍然相關。

---

## 動手練習

**練習 1（必做）**：跑 `ff_timing.c`，記錄你的機器上 cached 和 uncached clflush 的平均時間。確認方向（哪個快哪個慢），計算你自己的 F+F 門檻值。在不同背景負載下（跑 `stress --cpu 4 &` 之後）重跑，觀察訊號的穩定性如何變化。

**練習 2**：修改 `ff_timing.c`，改為量 L1 vs LLC vs DRAM 三種狀態下的 clflush 時間。先讓 line 在 L1，量一次；用 eviction set 把它降到 LLC（不踢到 DRAM），量一次；再踢到 DRAM，量一次。三個數字的分佈能告訴你 clflush 的時間跟快取層次的關係。

**練習 3（深入）**：在 Ch 7 的 F+R covert channel 基礎上，把 sender 維持不動、把 receiver 的 probe 改成 F+F（計時 clflush，不做 reload）。量新的 channel 頻寬（bits/second），跟 F+R 版本比較。注意門檻要重新校準。

---

## 本章重點整理

- **Flush+Flush (F+F)**：計時的是 `clflush` 本身，不是 reload。cached line 的 clflush 快，uncached 的慢（差距約 10–50 cycles，硬體依賴）。最大優點是不製造 LLC miss，難被 HPC 偵測；最大缺點是訊號弱、SNR 低。
- **Prime+Abort (P+Abort)**：把 eviction set 放進 RTM 交易的 read-set，用交易中止作為側通道訊號——完全不計時，完全無 LLC miss。前提是 TSX 可用，但 Intel 2019 年後幾乎全面禁用。
- **Evict+Time**：量 victim function 的執行時間差，前提最寬鬆（不需共享記憶體、不需 clflush），但粒度最粗、雜訊最大、需要主動呼叫 victim。
- **核心取捨**：隱蔽性（無 LLC miss）vs 訊號強度；共享記憶體需求 vs 泛用性；硬體特性依賴 vs 適用面。每種攻擊場景選最合適的工具，不是選最花俏的。
- **WSL2 的現實**：F+F 在 WSL2 下因虛擬化噪音，訊號會比原生 Linux 差；TSX 在 i7-10700 上已被停用；Evict+Time 可在 WSL2 上執行但需要仔細做 warmup。

---

## 自我檢核

1. F+F 和 F+R 的根本差異是什麼？為什麼 F+F 更難被 HPC 偵測器發現？
2. 在你的機器上，clflush 對 cached line 和 uncached line 的時間差是多少 cycles？這個差距足夠建立可靠的 covert channel 嗎？
3. Prime+Abort 的「中止訊號」為什麼是側通道？它跟計時型側通道有何本質區別？
4. Evict+Time 為什麼要量整個 victim function 的執行時間，而不是某條 cache line 的存取時間？這個設計決策帶來哪些限制？
5. 在沙箱環境（瀏覽器 JS、Docker 容器禁用 `clflush`）裡，你會優先選哪種攻擊變體？為什麼？

---

## 延伸閱讀

1. **Gruss, D., Maurice, C., Wagner, K., & Mangard, S.** (2016). *Flush+Flush: A Fast and Stealthy Cache Attack*. DIMVA 2016. — F+F 的原始論文，在多個 Intel 微架構上量測 clflush 時間差，建立 AES 和 keystroke 側通道，並設計了基於 LLC miss 計數的偵測方案（攻擊者自用偵測器找安全環境）。

2. **Disselkoen, C., Kohlbrenner, D., Porter, L., & Tullsen, D.** (2017). *Prime+Abort: A Timer-Free High-Precision L3 Cache Attack using Intel TSX*. USENIX Security 2017. — 首次把 RTM 交易中止作為快取側通道，不依賴計時器，在虛擬化環境下測量 AES、RSA 密鑰資訊，頻寬 1.5 Mbit/s。

3. **Osvik, D. A., Shamir, A., & Tromer, E.** (2006). *Cache Attacks and Countermeasures: the Case of AES*. CT-RSA 2006. — Evict+Time 和 Prime+Probe 的奠基論文，在 AES T-table 實作上完整展示 cache timing attack，提出對策分析，是整個快取攻擊領域的核心參考。

→ [下一章：快取攻擊對密碼學的實際衝擊](11-cache-attacks-on-crypto.md)
