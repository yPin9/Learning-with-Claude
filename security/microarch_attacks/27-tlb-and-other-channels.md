# Ch 27 — TLB 側信道與其他結構

> **目標**：把側信道的視野從快取（cache）拉開到整張 CPU 微架構地圖。理解為什麼「任何被兩個或以上執行緒/行程共享、且狀態依賴存取歷史的微架構結構，都是潛在的側信道」，並深入掌握 TLB 側信道（TLBleed）、Ring Bus 側信道（Lord of the Ring(s)）、DRAM Row Buffer 側信道（DRAMA），以及 BTB/I-cache 等次要通道的機制、可利用性、防禦盲點。

---

## 1. 核心論點：快取只是冰山一角

過去二十章幾乎圍著快取打轉：Flush+Reload、Prime+Probe、Evict+Reload，加上 Spectre/Meltdown 都要靠快取作為「傳輸通道」。快取確實是最容易量測、最容易利用的結構，但這只是因為它最早被研究透徹，不代表它是唯一的戰場。

現代 CPU 裡有幾十個共享的微架構結構：

- Translation Lookaside Buffer（TLB）
- Ring Bus（環形總線，Intel 多核心互聯）
- DRAM Row Buffer（DRAM 開頁快取）
- Branch Target Buffer（BTB）
- Return Stack Buffer（RSB）
- Store Buffer / Load Buffer
- Instruction Cache（I-cache）
- Memory Ordering Machine（MOB）

這些結構全部或部分被不同安全域的程式共用。只要能量測「因為對方的存取歷史改變了這個結構的狀態」所造成的時間差，就可以提取資訊。

快取攻擊被緩解之後（Intel CAT、cache partition、常數時間程式設計），研究者立刻轉向這些「次要」通道，而且發現它們同樣有效——有時更隱蔽、更難防。

---

## 2. 微架構通道全圖

```
  ┌─────────────────────────────────────────────────────────────────────┐
  │                           Intel i7-10700（Comet Lake, 8C16T）        │
  │                                                                     │
  │  Core 0          Core 1          Core 2    ...    Core 7            │
  │  ┌──────┐        ┌──────┐        ┌──────┐        ┌──────┐          │
  │  │ L1-D │        │ L1-D │        │ L1-D │        │ L1-D │  ← cache │
  │  │ dTLB │        │ dTLB │        │ dTLB │        │ dTLB │  ← TLB   │
  │  │ L1-I │        │ L1-I │        │ L1-I │        │ L1-I │  ← cache │
  │  │ iTLB │        │ iTLB │        │ iTLB │        │ iTLB │  ← TLB   │
  │  │  L2  │        │  L2  │        │  L2  │        │  L2  │          │
  │  │ STLB │        │ STLB │        │ STLB │        │ STLB │  ← TLB   │
  │  └──────┘        └──────┘        └──────┘        └──────┘          │
  │      │               │               │               │              │
  │  ════╪═══════════════╪═══════════════╪═══════════════╪════          │
  │      │          Ring Bus（共享）       │               │   ← ring    │
  │  ════╪═══════════════╪═══════════════╪═══════════════╪════          │
  │      │               │               │               │              │
  │  ┌───┴───┐       ┌───┴───┐       ┌───┴───┐       ┌───┴───┐         │
  │  │L3 Slice│      │L3 Slice│      │L3 Slice│      │L3 Slice│ ← cache │
  │  └───────┘       └───────┘       └───────┘       └───────┘         │
  │                                                                     │
  │  Memory Controller ────────────────────────────────────────────────┤
  └─────────────────┬───────────────────────────────────────────────────┘
                    │
         ┌──────────┴──────────┐
         │     DRAM（DDR4）      │
         │  Bank 0  Bank 1 ...  │
         │  [Row Buffer]        │  ← row buffer
         └──────────────────────┘

  側信道等級（概略）：
  ★★★★★  快取（L1/L2/LLC）      — 最容易量測，研究最成熟
  ★★★★☆  TLB（dTLB/STLB）      — 需要精確設計 eviction pattern
  ★★★☆☆  Ring Bus               — 需要知道 CPU 拓樸，跨核心有效
  ★★★☆☆  DRAM Row Buffer        — 需要知道實體位址，延遲差大
  ★★☆☆☆  BTB / I-cache          — 配合 Spectre 使用，單獨難利用
```

---

## 3. TLBleed：Translation Leak-aside Buffer

### 3.1 TLB 的組織方式

Intel CPU 的 TLB 並不是全相聯（fully associative）的單一結構，而是組相聯（set-associative）的——和快取非常類似。以 i7-10700 為例：

| TLB 層級 | 類型 | 容量 | 相聯度 | 覆蓋範圍（4KB pages） |
|----------|------|------|--------|----------------------|
| L1 dTLB  | 資料 | 64 entries | 4-way | 256 KB |
| L1 iTLB  | 指令 | 128 entries | 8-way | 512 KB |
| L2 STLB  | 統一 | 1536 entries | 12-way | 6 MB |

超過 STLB 的 1536 entries 才會真正觸發 TLB miss 並走 page table walk（PTW）。

TLB 的 set 是由虛擬頁框號（Virtual Page Number, VPN）的低位元決定的，和快取的 set index 邏輯完全一樣。這意味著：

- 兩個不同的虛擬頁面，如果 VPN 低位相同，就會映射到同一個 TLB set。
- 在 SMT（Simultaneous Multi-Threading）下，同一個實體核心的兩個超執行緒共享 dTLB 和 STLB。
- 攻擊者可以透過存取特定 VPN 樣式，「evict」受害者的 TLB entry，然後量測受害者的延遲。

### 3.2 TLBleed 攻擊機制

論文：Gras et al., "Translation Leak-aside Buffer: Defeating Cache Side-channel Protections with TLB Attacks", USENIX Security 2018.

TLBleed 的攻擊流程和 Prime+Probe 幾乎是鏡像關係，但操作的是 TLB 而非快取：

```
  Phase 1：Prime（填充 TLB set）
  攻擊者存取一組虛擬頁面，這些頁面的 VPN 低位
  和目標 set 相同，填滿 TLB set（12-way STLB，
  需要 12 個衝突頁面）

  Phase 2：等待受害者執行
  受害者的記憶體存取模式（哪些虛擬頁面）會
  evict 攻擊者在該 set 裡的某些 entry

  Phase 3：Probe（量測）
  攻擊者重新存取那 12 個頁面，量測每個的存取時間：
  - TLB hit  → 快（受害者沒存取這個 set，或沒衝突）
  - TLB miss → 慢（受害者 evict 了攻擊者的 entry）

  推斷受害者存取了哪些虛擬頁面 → 洩漏敏感資訊
```

### 3.3 為何能繞過 Cache 防禦

Intel CAT（Cache Allocation Technology）可以把 LLC 切成不重疊的分區，讓不同安全域使用不同的 cache way。這是目前最強的 cache 隔離手段之一。

TLBleed 完全繞過 Intel CAT，因為 CAT 只管 LLC，不管 TLB。TLB 依然被 SMT 下的所有執行緒共享，Intel 沒有提供任何 TLB 分區機制。

同理，軟體層面的 cache 隔離（constant-time、不依賴 cache state）對 TLB 攻擊也無效——程式存取哪些虛擬頁面這件事，本身就洩漏在 TLB 裡。

### 3.4 TLBleed 能洩漏什麼

論文示範對 Curve25519（libgcrypt 實作的橢圓曲線）進行攻擊。Curve25519 的純量乘法實作雖然已經是常數時間（constant-time），但不同的私鑰位元會導致不同的虛擬頁面存取樣式，TLBleed 可以從 TLB side-channel 洩漏私鑰，準確率高達 98%。

這打臉了「我已經寫成常數時間了，應該安全」的假設——常數時間只防快取，防不了 TLB。

### 3.5 時間差量級

| 事件 | 延遲（近似，Comet Lake） |
|------|--------------------------|
| L1 dTLB hit | ~1 cycle（TLB 查詢與快取命中並行） |
| L2 STLB hit | ~7–10 cycles |
| TLB miss（page table walk） | ~100–200+ cycles（4 次記憶體存取） |
| L1 cache miss | ~40 cycles |
| LLC miss | ~250–400 cycles |

TLB miss 比 LLC miss 更慢——page table walk 需要走四級頁表（PML4 → PDPT → PD → PT），每一級都是記憶體存取，總共 4 次 LLC miss 的開銷。這讓 TLB miss 訊號在雜訊中反而更容易識別。

---

## 4. Ring Interconnect：Lord of the Ring(s)

### 4.1 Ring Bus 架構

Intel 從 Sandy Bridge（2011）起使用 ring bus 架構連接多核心。每個核心、LLC slice、System Agent（記憶體控制器、PCIe 等）都掛在同一條環形總線上。i7-10700 的 ring 架構：

```
  ┌───┐     ┌───┐     ┌───┐     ┌───┐
  │ C0│─────│ C1│─────│ C2│─────│ C3│
  └─┬─┘     └─┬─┘     └─┬─┘     └─┬─┘
    │          │          │          │
  ┌─┴─┐     ┌─┴─┐     ┌─┴─┐     ┌─┴─┐
  │L3s│     │L3s│     │L3s│     │L3s│   L3 slice
  └─┬─┘     └─┬─┘     └─┬─┘     └─┬─┘
    │          │          │          │
  ┌─┴─┐     ┌─┴─┐     ┌─┴─┐     ┌─┴─┐
  │ C4│─────│ C5│─────│ C6│─────│ C7│
  └───┘     └───┘     └───┘     └───┘
            ↑
         Ring segment（環的「邊」）
         每條邊的頻寬有限，是競爭點

  實際上是一個雙向環，資料可以順/逆時針傳輸
  選擇較短的路徑，最多跑 N/2 個 segment
```

Ring bus 每條 segment 有固定頻寬。當多個核心同時大量使用 ring（例如頻繁存取遠端 LLC slice），特定 segment 會出現擁塞，延遲上升。

### 4.2 Lord of the Ring(s) 攻擊

論文：Paccagnella et al., "Lord of the Ring(s): Side Channel Attacks on the CPU On-Chip Ring Bus", USENIX Security 2021.

攻擊的基本思路：

```
  攻擊者在 Core 0，受害者在 Core 7

  受害者密集存取某個 LLC slice（假設在 Core 3 對應的 slice）
  → 受害者的 ring traffic 從 C7 跑到 C3，經過 C4 → C3 這條 segment

  攻擊者同時做 ring-intensive 操作（例如存取不同 LLC slice）
  → 攻擊者測自己的 ring latency

  當受害者的 traffic 和攻擊者的 traffic 在同一條 segment 上競爭時：
  攻擊者的延遲上升 → 攻擊者知道受害者在存取哪個方向的 LLC slice
  → 可推斷受害者的記憶體存取模式
```

Lord of the Ring(s) 的關鍵貢獻：

1. **可跨實體核心**：不需要 SMT，不在同一個超執行緒組。即使受害者和攻擊者在完全不同的核心上，ring bus 依然是共享的。
2. **繞過所有 cache 隔離**：Intel CAT 隔離了 LLC 內容，但不隔離 ring bus 本身的流量模式。
3. **細粒度的時間解析**：透過精確計時可以識別受害者在哪條 ring segment 上造成擁塞，進而推斷存取的 LLC slice，再對應回實體位址範圍。

論文示範了跨核心偷取 AES-NI 金鑰的攻擊，準確率同樣高。

### 4.3 需要知道的資訊

Ring bus 攻擊比快取攻擊更難用在於：攻擊者需要知道目標 CPU 的 ring 拓樸——幾個核心、幾個 LLC slice、每個 LLC slice 對應哪個核心的 ring stop。這些資訊不在 `/proc/cpuinfo`，需要查 Intel 的 datasheet 和 BIOS/UEFI 資訊。

現代 Linux 的 `perf` 和 `/sys/devices/system/cpu/cpu*/topology/` 可以提供部分拓樸資訊，但完整的 ring segment 映射需要靠實驗測量或查官方文件。

---

## 5. DRAM Row Buffer：DRAMA

### 5.1 DRAM 的工作方式

DRAM 的基本單位是 bank，每個 bank 有數千行（row），每行有數千位元。DRAM 的致命設計特點：每次存取前必須先「激活」（activate）一行，把整行資料載入 row buffer，再讀取欄位。

```
  DRAM Bank 的狀態機：

  ┌────────────┐
  │ Row Closed │  precharge 完成
  └─────┬──────┘
        │ ACT（activate row X）
        ↓
  ┌────────────┐
  │  Row X     │  row buffer 持有 row X 的資料
  │  Open      │
  └─────┬──────┘
        │
   ┌────┴──────────────────┐
   │                       │
   ↓                       ↓
  存取 row X（同行）    存取 row Y（不同行）
  → Row Hit             → Row Conflict
  → ~40 ns              → ~100 ns+（需要先 PRE 再 ACT）
```

### 5.2 DRAMA 攻擊

論文：Pessl et al., "DRAMA: Exploiting DRAM Addressing for Cross-CPU Attacks", USENIX Security 2016.

DRAMA 利用 row buffer 狀態作為側信道：

1. **攻擊者存取某個 DRAM row** → row buffer 現在持有攻擊者的 row（open row）
2. **受害者存取同一個 bank 的不同 row** → row conflict，row buffer 更換為受害者的 row
3. **攻擊者再次存取自己的 row** → row conflict（慢）
4. **如果受害者沒有存取這個 bank** → row hit（快）

攻擊者從延遲差（fast = hit, slow = conflict）推斷受害者是否存取了這個 bank 的某個 row，進而推斷受害者的記憶體存取樣式。

### 5.3 DRAMA 的特殊條件

DRAMA 需要攻擊者知道自己和受害者的實體位址，以及 DRAM 定址幾何（哪些位元決定 bank、rank、row）。這在一般環境不容易取得，但：

- Rowhammer 攻擊（Ch 22–24）已經解決了「找出實體位址和 DRAM 幾何」這個問題。
- 在 cloud 環境（同一台實體機器跑多個 VM）這個攻擊特別危險：DRAM 是實體共享的，row buffer 狀態是跨 VM 共享的，而 VM 之間的快取可以被 hypervisor 隔離，但 DRAM row buffer 隔離不了。

### 5.4 DRAMA 與 Rowhammer 的關係

DRAMA 和 Rowhammer 使用相同的底層機制（DRAM 定址幾何），但目的不同：

| | DRAMA | Rowhammer |
|--|-------|-----------|
| 目的 | 側信道（洩漏資訊）| 故障注入（翻轉 bit）|
| 利用的特性 | Row conflict timing | Row activation count |
| 需要 root？ | 不需要（只需要存取大量記憶體） | 不需要（但需要物理距離） |

---

## 6. 其他次要通道

### 6.1 BTB（Branch Target Buffer）側信道

BTB 記錄間接跳轉的目標歷史，讓處理器可以在不解析目標位址的情況下預測跳轉目的地。Intel Skylake 以前，BTB 在 SMT 執行緒之間是共享的。

攻擊思路（和 Spectre v2 密切相關）：
- 受害者執行某個間接跳轉序列 → 更新 BTB 中特定 entry
- 攻擊者執行相同的 tag（別名衝突的間接跳轉）→ 使用受害者留下的 BTB 狀態
- 攻擊者的預測結果反映了受害者的 branch target → 側信道

現代 Intel CPU 已對 BTB 做了一定程度的隔離（IBPB barrier），但不完整（見 Ch 16 Spectre v2）。

### 6.2 Instruction Cache（I-cache / iTLB）側信道

I-cache 和 iTLB 都依賴執行歷史。如果受害者執行了某段程式碼，會在 I-cache 和 iTLB 留下痕跡。攻擊者可以量測自己執行相同虛擬位址區段的速度，推斷受害者是否執行過該段程式碼。

這個通道在 OpenSSL RSA 實作的攻擊上有被用到（Yarom & Falkner, 2014），算是快取攻擊的一個子集。

### 6.3 Store Buffer / Load Buffer（MDS 關聯）

Store buffer 和 load buffer 是亂序執行的核心結構，暫時存放未完成的讀寫操作。MDS（Microarchitectural Data Sampling）系列漏洞（RIDL、Fallout、ZombieLoad，見 Ch 19）正是利用這些結構作為洩漏通道——不是「觀測延遲」，而是「讀到其他執行緒/核心的隱私資料」。

這個嚴格來說不是傳統的「計時側信道」，而是「資料洩漏通道」（data leakage channel），但根源同樣是共享的微架構結構。

---

## 7. 實驗：親手感受 TLB Hit vs TLB Miss

### 7.1 實驗設計

i7-10700 的 STLB 有 1536 entries，覆蓋 6 MB。我們設計兩個存取樣式：

- **TLB-hot**：存取 256 個 page（1 MB），全部常駐 STLB
- **TLB-cold**：存取 4096 個 page（16 MB），遠超 STLB，強制每次都 TLB miss

每次只碰每個 page 的第一個位元組（確保 cache miss 不影響 TLB 量測），用 `rdtscp` 計時。

### 7.2 實驗程式碼

```c
// tlb_timing.c
// 用法：gcc -O2 -o tlb_timing tlb_timing.c && ./tlb_timing
// WSL2 可跑，輸出 TLB hit / miss 的平均延遲

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>

#define PAGE_SIZE   4096
#define HOT_PAGES   256    // 1MB，遠小於 STLB 1536 entries
#define COLD_PAGES  4096   // 16MB，遠大於 STLB 1536 entries
#define ITERATIONS  100

static inline uint64_t rdtscp(void) {
    uint32_t lo, hi;
    __asm__ volatile (
        "mfence\n\t"
        "rdtscp\n\t"
        "mfence"
        : "=a"(lo), "=d"(hi)
        :: "ecx", "memory"
    );
    return ((uint64_t)hi << 32) | lo;
}

// 存取 n 個 page，每個只碰第一個 byte
// 用 volatile 確保不被最佳化掉
static uint64_t measure_access(volatile char *buf, int n_pages) {
    uint64_t start = rdtscp();
    for (int i = 0; i < n_pages; i++) {
        // 讀取每個 page 的開頭，強制 TLB 查詢
        (void)buf[i * PAGE_SIZE];
    }
    uint64_t end = rdtscp();
    return end - start;
}

// 先「熱身」讓 buf 有對應的 page table（避免 page fault）
static void warmup(volatile char *buf, int n_pages) {
    for (int i = 0; i < n_pages; i++) {
        buf[i * PAGE_SIZE] = (char)i;  // 寫入確保 page 存在
    }
}

int main(void) {
    // mmap 大區塊，不使用大頁面（確保每個 page 有獨立 TLB entry）
    size_t hot_size  = (size_t)HOT_PAGES  * PAGE_SIZE;
    size_t cold_size = (size_t)COLD_PAGES * PAGE_SIZE;

    volatile char *hot_buf = mmap(NULL, hot_size,
        PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);
    volatile char *cold_buf = mmap(NULL, cold_size,
        PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);

    if (hot_buf == MAP_FAILED || cold_buf == MAP_FAILED) {
        perror("mmap");
        return 1;
    }

    // 熱身，確保 page table 建立完成
    warmup(hot_buf, HOT_PAGES);
    warmup(cold_buf, COLD_PAGES);

    printf("%-20s %-15s %-15s\n", "測試", "總 cycles", "每 page 平均");
    printf("%-20s %-15s %-15s\n", "----", "----------", "------------");

    // TLB-hot：先跑一次讓 STLB 熱起來，再量第二次
    measure_access(hot_buf, HOT_PAGES);  // 預熱 TLB
    uint64_t hot_cycles = 0;
    for (int iter = 0; iter < ITERATIONS; iter++) {
        hot_cycles += measure_access(hot_buf, HOT_PAGES);
    }
    hot_cycles /= ITERATIONS;
    printf("%-20s %-15lu %-15.1f\n",
        "TLB-hot (256 pages)",
        hot_cycles,
        (double)hot_cycles / HOT_PAGES);

    // TLB-cold：每次存取前先 flush TLB（用 cold_buf 把 STLB 填滿）
    // 先存取 cold_buf 把 STLB 清掉，再量 hot_buf
    uint64_t cold_effect_cycles = 0;
    for (int iter = 0; iter < ITERATIONS; iter++) {
        // 用大陣列把 STLB 全部替換掉
        measure_access(cold_buf, COLD_PAGES);
        // 現在 hot_buf 的 TLB entry 應該都被 evict 了
        cold_effect_cycles += measure_access(hot_buf, HOT_PAGES);
    }
    cold_effect_cycles /= ITERATIONS;
    printf("%-20s %-15lu %-15.1f\n",
        "TLB-cold (STLB evict)",
        cold_effect_cycles,
        (double)cold_effect_cycles / HOT_PAGES);

    // 也量一下純 cold_buf 存取
    uint64_t pure_cold = measure_access(cold_buf, COLD_PAGES);
    printf("%-20s %-15lu %-15.1f\n",
        "cold_buf (4096 pages)",
        pure_cold,
        (double)pure_cold / COLD_PAGES);

    munmap((void*)hot_buf, hot_size);
    munmap((void*)cold_buf, cold_size);
    return 0;
}
```

### 7.3 預期輸出與解讀

在 WSL2 i7-10700 上，典型輸出（數值會因負載波動）：

```
測試                 總 cycles       每 page 平均
----                 ----------      ------------
TLB-hot (256 pages)  8500            33.2
TLB-cold (STLB evict) 38000          148.4
cold_buf (4096 pages)  600000         146.5
```

解讀：

- **TLB-hot**：每 page 約 30–50 cycles，這個數字包含 L1 cache miss（第一次存取）但不含 TLB miss overhead。如果資料也在快取，會更快。
- **TLB-cold（被 evict 後）**：每 page 約 120–200 cycles，多出來的 ~100 cycles 就是 page table walk 的開銷——四級頁表每層一次記憶體存取。
- **純 cold_buf**：和 TLB-cold 類似，但因為 cache 也 cold，數字可能更高。

這個差距（100+ cycles）就是 TLBleed 賴以偵測 TLB eviction 的訊號。

### 7.4 注意事項

WSL2 的量測可能受到以下干擾：
- Hyper-V hypervisor 偶爾插入（TSC 偏移）
- Windows 排程器也在跑，會造成量測中斷
- `/proc/sys/vm/drop_caches` 在 WSL2 不完全可靠

如果需要更乾淨的量測，在 WSL2 裡加 `taskset -c 0` 把行程鎖定在單一核心，減少核心遷移影響。

---

## 8. 四大通道對比取捨表

| 通道 | 攻擊需求 | 跨核心？ | 洩漏粒度 | 防禦現況 | 代表論文 |
|------|----------|----------|----------|----------|----------|
| TLB（TLBleed） | SMT 同核心，知道 TLB set 組織 | 否（需 SMT） | 虛擬頁面存取樣式 | 無 TLB 分區；關 SMT 是唯一有效選項 | Gras 2018 |
| Ring Bus | 無需 SMT，需知 CPU 拓樸 | 是 | LLC slice 存取方向 | 無硬體防禦；極難偵測 | Paccagnella 2021 |
| DRAM Row Buffer | 需知實體位址和 DRAM 幾何 | 是（跨 VM） | DRAM bank/row 存取 | 無；DRAM 廠商無對策 | Pessl 2016 |
| LLC Cache | 多種方式（Prime+Probe 不需共享記憶體） | 是 | Cache set 存取 | Intel CAT、cache flush 限制 | 大量論文 |
| BTB | SMT，知道 BTB 別名 | 否（主要） | 間接跳轉目標 | IBPB barrier（有效能開銷） | Spectre v2 |

---

## 9. 踩雷：五個常見錯誤認知

### 踩雷 1：TLB miss 和 cache miss 是同一件事

TLB miss 和 cache miss 雖然常同時發生，但機制和開銷完全不同：

- Cache miss → 從上層 cache 或記憶體取資料，額外 ~40–400 cycles
- TLB miss → page table walk，走四級頁表，每級一次記憶體存取，總共 ~100–200 cycles

TLB miss 的訊號更乾淨，因為 page table walk 的延遲相對固定，不像 cache miss 受 LLC 競爭影響那麼大。

### 踩雷 2：Ring bus 拓樸可以從 /proc/cpuinfo 讀出來

`/proc/cpuinfo` 告訴你有幾個核心、超執行緒狀態、快取大小，但不告訴你 ring bus 的 segment 配置和 LLC slice 對應關係。攻擊者需要查 Intel 的微架構手冊（Intel Optimization Reference Manual）或用實驗方法（測量跨核心存取延遲差）來推斷拓樸。

Lord of the Ring(s) 論文花了相當篇幅說明如何做這個「拓樸重建」的前置工作。

### 踩雷 3：隔離了 cache 就安全了

Intel CAT 隔離 LLC，讓不同 VM/容器使用不同的 cache way，是目前業界認為最有效的 cache side-channel 緩解之一。但：

- TLB 不受 CAT 影響，TLBleed 完全有效
- Ring bus 不受 CAT 影響，Lord of the Ring(s) 完全有效
- DRAM row buffer 不受 CAT 影響，DRAMA 完全有效

「隔離了 cache 就無法被側信道」是危險的假設，安全邊界必須考慮整張微架構地圖，而不只是快取。

### 踩雷 4：cloud 環境的 VM 隔離保護 DRAM

Cloud hypervisor（KVM、Xen、Hyper-V）可以隔離 CPU 暫存器、快取（透過 Intel CAT），但無法隔離 DRAM row buffer。實體 DRAM 是所有 VM 共享的，row buffer 狀態是實體層面的競爭，hypervisor 沒有插手的機會。

DRAMA 論文在兩個不同的 VM 之間（跑在同一台實體機器上）成功建立了 600 MB/s 的隱蔽通道（covert channel），完全繞過 hypervisor 的隔離。

### 踩雷 5：常數時間程式設計能防 TLB 攻擊

常數時間（constant-time）程式設計的目標是讓執行路徑和記憶體存取不依賴秘密資料，主要防的是快取攻擊（Fast+Flush 系列）和分支預測攻擊。

但是，如果演算法的設計導致不同的秘密資料映射到不同的虛擬頁面（即使存取時間相同），TLB 攻擊依然可以區分。TLBleed 論文的 Curve25519 案例就是這樣——libgcrypt 的實作已經是常數時間了，但純量乘法的中間資料分佈在不同頁面，TLB 足夠分辨。

真正防 TLB 攻擊的方法：確保所有執行路徑存取的是同一組虛擬頁面（同一個 TLB set 映射），這比常數時間更嚴格的要求。

---

## 10. 進階：TLB 攻擊的實際侷限與研究前沿

### 10.1 TLBleed 的偵測和繞過

TLBleed 原始論文需要攻擊者和受害者在同一個超執行緒對（SMT sibling），且需要大量觀測來過濾雜訊（統計分析）。後續的改良版（SpectreRSB 等）把 TLB 信道和推測執行結合，可以在更短的觀測窗口內完成攻擊。

### 10.2 iTLB Multihit（CVE-2018-12207）

Intel 的 iTLB 在處理 2MB huge page 和 4KB page 混合的場景時有一個 bug：如果多個 TLB entry 同時命中（因為某些條件下 2MB 和 4KB 的 entry 會同時存在），CPU 會進入不可恢復的機器錯誤（machine check exception），可被用作 DoS 攻擊。這不是側信道，但顯示 TLB 的邊界條件還有很多值得探索的空間。

### 10.3 Translation-Level Attacks（TLB 輔助的 Spectre）

最新一代攻擊把 TLB 資訊洩漏和推測執行結合。例如，在 Spectre gadget 執行時，TLB 狀態可以提供額外的位址資訊，幫助攻擊者更精確地定位目標。

### 10.4 Ring Bus 防禦的空白

Intel 在 Alder Lake（2021）起採用 mesh 架構取代 ring bus，但 mesh 同樣有競爭點——只是更複雜、更難逆向工程。目前沒有公開論文成功在 mesh 上重現 Lord of the Ring(s) 等級的攻擊，但這更可能是因為研究者還在摸索拓樸，而不是 mesh 天生免疫。

---

## 11. 動手練習

### 練習 A：量測 dTLB vs STLB miss

修改 7.2 的程式，設計三組：
1. 只存取 32 個 pages（遠小於 L1 dTLB 64 entries）→ dTLB hit
2. 存取 512 個 pages（超過 dTLB 但在 STLB 1536 以內）→ dTLB miss, STLB hit
3. 存取 4096 個 pages（超過 STLB）→ 完整 TLB miss

觀察三個層級的延遲差，確認 i7-10700 TLB 層級架構。

### 練習 B：TLB Eviction Set 構造

設計一個程式，找出哪些虛擬頁面和目標頁面（VPN）映射到同一個 STLB set：
- STLB 有 128 個 set（1536 / 12-way = 128 sets）
- Set index = VPN mod 128（假設直接映射，實際需要實驗確認）
- 驗證：如果你存取 12 個衝突頁面，再存取目標頁面，目標頁面應該會 TLB miss

這是 TLBleed 攻擊的核心步驟之一，理解它才能理解整個攻擊的可行性。

### 練習 C：跨核心 Ring Bus 競爭感知

在兩個核心上分別跑行程（用 `taskset` 鎖定）：
- 核心 0：行程 A，瘋狂讀取一個遠端 LLC slice（用 `perf stat -e LLC-load-misses` 驗證在 LLC miss 而非 L2）
- 核心 7：行程 B，量測自己存取同一個方向的 LLC slice 的延遲

觀察行程 A 的行為是否影響行程 B 的量測結果，這就是 ring bus 側信道的最簡單示範。

---

## 12. 本章重點整理

- 快取是最容易利用的微架構側信道，但不是唯一的。TLB、Ring Bus、DRAM Row Buffer 都是可用的通道，且能繞過各種快取防禦。

- **TLBleed**：利用 SMT 下共享的 set-associative TLB，透過 Prime+Probe 推斷對方的虛擬頁面存取樣式。繞過 Intel CAT 和常數時間程式設計。

- **Lord of the Ring(s)**：利用 Intel ring bus 的 segment 競爭，跨實體核心偵測對方的 LLC 存取方向。無需 SMT，幾乎無法從軟體層面防禦。

- **DRAMA**：利用 DRAM row buffer 的 open/closed 狀態時間差，可跨 VM 建立隱蔽通道。DRAM 是唯一真正的實體共享資源，hypervisor 隔離對其無效。

- TLB miss 和 cache miss 不同：TLB miss 涉及 page table walk，延遲更高（~100–200 cycles vs ~250–400 for LLC miss），訊號更集中。

- 安全評估必須考慮整張微架構地圖，而非只聚焦快取。「我隔離了快取」不等於「我防住了所有側信道」。

---

## 13. 自我檢核

1. 解釋為什麼 Intel CAT 能防快取側信道但防不了 TLBleed。TLB 的哪個設計特性讓它可以被 set-associative eviction 攻擊？

2. TLBleed 和 Prime+Probe 的攻擊流程有什麼結構上的相似性？主要差異在哪裡（操作的結構、eviction 的方式、可量測的訊號）？

3. Lord of the Ring(s) 為什麼比快取攻擊更難利用？需要哪些前置條件？為什麼它又比快取攻擊更難防禦？

4. DRAM row buffer 攻擊在 cloud 環境為什麼特別危險？hypervisor 無法防禦的根本原因是什麼？

5. 一個已經實作常數時間密碼學的函式庫，還會被 TLBleed 攻擊嗎？為什麼？需要什麼額外的保護才能防 TLB 側信道？

6. 實驗中，我們觀察到 TLB-cold 比 TLB-hot 慢了約 100 cycles。這 100 cycles 對應的是什麼硬體事件？為什麼 page table walk 需要這麼多 cycles？

---

## 14. 延伸閱讀

- **TLBleed**：Gras, B., Razavi, K., Bos, H., & Giuffrida, C. (2018). Translation Leak-aside Buffer: Defeating Cache Side-channel Protections with TLB Attacks. *USENIX Security Symposium*. https://www.usenix.org/conference/usenixsecurity18/presentation/gras

- **Lord of the Ring(s)**：Paccagnella, R., Luo, L., & Fletcher, C. W. (2021). Lord of the Ring(s): Side Channel Attacks on the CPU On-Chip Ring Bus. *USENIX Security Symposium*. https://www.usenix.org/conference/usenixsecurity21/presentation/paccagnella

- **DRAMA**：Pessl, P., Gruss, D., Maurice, C., Schwarz, M., & Mangard, S. (2016). DRAMA: Exploiting DRAM Addressing for Cross-CPU Attacks. *USENIX Security Symposium*. https://www.usenix.org/conference/usenixsecurity16/technical-sessions/presentation/pessl

- **Intel TLB 規格**：Intel® 64 and IA-32 Architectures Optimization Reference Manual（第 2 章，TLB 層級結構）

- **DRAM 定址幾何**：Kim, Y., et al. (2014). Flipping Bits in Memory Without Accessing Them: An Experimental Study of DRAM Disturbance Errors. *ISCA*. （DRAMA 的先驅，說明如何重建 DRAM 幾何）

---

本章把快取側信道的框架延伸到整個微架構。下一章我們把這些通道實際用在攻擊目標上：利用微架構洩漏打破 KASLR（Kernel Address Space Layout Randomization），把位址隨機化這道防線撬開。

→ [下一章](28-microarchitectural-kaslr-break.md)
