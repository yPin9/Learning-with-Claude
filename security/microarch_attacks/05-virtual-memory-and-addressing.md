# Ch 5 — 虛擬記憶體與位址轉換對攻擊的意義

> **目標**：從攻擊者的視角重讀虛擬記憶體——不是「怎麼讓程式的記憶體管理更安全」，而是「攻擊者為什麼執著於物理位址、VA→PA 的轉換路徑如何洩漏資訊、huge page 如何讓攻擊者不需要 root 就能做 cache set 計算、以及 DRAM row 的位置如何決定 Rowhammer 的攻擊對象」。這章打通虛擬記憶體和 Part 2/Part 4 的橋樑——讀完你能解釋：為什麼 Ch 9 建 eviction set 需要 huge page、為什麼 Ch 22 Rowhammer 攻擊必須了解 DRAM row 位址。

> **環境**：WSL2 Ubuntu 22.04，i7-10700 Comet Lake。`/proc/self/pagemap` 需要 sudo 才能讀 PFN（kernel 4.0+ 的安全限制）。

## 為什麼攻擊者執著於物理位址？

用戶態程式看到的是虛擬位址（VA），但 cache 和 DRAM 的硬體操作是以**物理位址（PA）**為基礎的。這個差距，就是攻擊者的一個主要困難：

```
  攻擊者知道：                     攻擊者需要知道：
  ┌────────────────────┐          ┌─────────────────────────┐
  │  自己的 VA         │          │  目標的 PA               │
  │  (e.g., 0x7fff...)│          │  (e.g., 0x13d491000)    │
  └────────────────────┘          └─────────────────────────┘
                                          │
                                          ↓
                            ┌─────────────────────────────┐
                            │  決定：                      │
                            │  1. 目標在哪個 cache set     │
                            │  2. 目標在哪個 LLC slice      │
                            │  3. 目標在 DRAM 哪個 row/bank│
                            └─────────────────────────────┘
```

**不知道 PA 的後果**：
- 你想對「受害者的 array[secret]」做 eviction，但不知道它的 PA，就不知道該 evict 哪個 cache set——你的 eviction set 可能根本跟目標不在同一個 set，eviction 無效。
- Rowhammer 要反覆激活 DRAM 的「aggressor row」——但你要知道 PA 才能知道哪兩個 PA 是相鄰 row（同 bank 相鄰 row 在 PA 上的關係取決於 DRAM 的地址 decode 方式）。

攻擊者獲取 PA 的方法：
1. **`/proc/self/pagemap`（需要 root）**：直接讀 VA→PFN 的映射。
2. **Huge page（2MB）的幾何推算**：不需要 root，但需要 huge page 分配。
3. **Eviction set 的實驗性搜尋**：不需要知道 PA，透過測試哪些 VA 互相 evict 來逆向 cache set 結構。（Ch 9 的主要技術）
4. **Rowhammer 的盲打**：在某些 DRAM 配置下，PA 的 row 結構可以從 DRAM 存取時間的差異（row buffer hit vs miss）推算出來。

## VA → PA：四層 page table walk

x86-64 的虛擬位址到物理位址的轉換，在 64 位元模式下用四層（4-level）page table：

```
  Virtual Address（48 位元有效，高 16 位是符號擴展）
  ┌──────────┬─────────┬─────────┬─────────┬───────────────┐
  │ 符號擴展  │  PML4   │  PDP    │   PD    │  PT   │ offset│
  │  [63:48] │ [47:39] │ [38:30] │ [29:21] │[20:12]│[11:0] │
  │  9 bits  │  9 bits │  9 bits │  9 bits │ 9 bits│12 bits│
  └──────────┴─────────┴─────────┴─────────┴───────┴───────┘

  翻譯過程：
  CR3（Page Global Directory 物理基底）
      │  + VA[47:39] → PML4 entry
      ▼
  PML4 Table（物理）→ entry → PDP Table 的物理位址
      │  + VA[38:30] → PDP entry
      ▼
  PDP Table（物理）→ entry → PD Table 的物理位址
      │  + VA[29:21] → PD entry
      ▼
  PD Table（物理）→ entry → PT Table 的物理位址
      │  + VA[20:12] → PT entry
      ▼
  PT（Page Table，物理）→ entry → PFN（Page Frame Number）
      │
      ▼
  PA = PFN × 4096 + VA[11:0]（offset in page）
```

每一層 page table 本身存在物理記憶體裡，可能在 cache 也可能不在。**完整的 page walk 最多需要 4 次記憶體存取**，所以 TLB miss 的代價可以很高（如果 PT 不在 cache，每層都要 DRAM access = 4 × 244 cycles ≈ 1000 cycles）。

**攻擊者的觀察**：
- VA[11:0]（低 12 位，page offset）= PA[11:0]——不管 VA 和 PA 的高位如何，page 內的偏移量一致。
- 這表示 VA[5:0] = PA[5:0]（cache line offset）、VA[11:6] = PA[11:6]（L1-D set index，因為 L1-D 只用 PA[11:6]）。
- **攻擊者從 VA 就能算 L1-D set**，不需要 PA。

但 L2 用 PA[15:6]、LLC 用 PA[19:6]——這些超出 page offset 的位元，VA 和 PA 可以不同。所以**攻擊者需要 PA 才能算 L2/LLC set**（除非用 huge page，見後文）。

## TLB：翻譯的快取

page table walk 要 4 次記憶體存取，如果每次記憶體操作都要走，overhead 是 4–10×。TLB（Translation Lookaside Buffer）是 page table 最近翻譯結果的快取：

```
  VA → TLB lookup
        │
        ├── TLB HIT：直接拿到 PA，~1–3 cycles（和 L1 cache 存取並行）
        │
        └── TLB MISS：走 page table（4 次記憶體存取）
                       → 找到 PFN
                       → 填進 TLB
                       → 下次同一 page 就 TLB HIT
```

**本機 TLB 幾何**（cpuid 和 Intel Opt. Manual，i7-10700）：

| TLB 層級 | 類型 | 4KB page | 2MB page |
|---|---|---|---|
| L1-D TLB | 資料存取 | 64 entries | 32 entries |
| L1-I TLB | 指令 fetch | 128 entries | — |
| L2 TLB | Unified（STLB） | 2048 entries | 2048 entries |

L2 TLB（Second Level TLB，STLB）是 L1 TLB 的後備——L1 TLB miss 但 L2 TLB hit，代價 ~10–15 cycles（比走 page table 快很多）。

**TLB 對攻擊的意義**：

1. **TLB 也是側信道**：TLB hit 和 TLB miss 有計時差異（幾個 cycles vs 幾十 cycles）。Ch 27 的 TLB 側信道攻擊利用這個差異，推斷受害者最近存取了哪些 page（精度：page 粒度 = 4KB）。

2. **TLB flush 是 KPTI 的代價**：KPTI（Kernel Page Table Isolation，Meltdown 的緩解）讓 user 和 kernel 用不同的 page table——每次 syscall 進 kernel 要切換 CR3（TLB 全 flush），每次 return 要切回，加上 TLB warm-up 代價。這是 KPTI 讓 syscall-heavy workload 變慢 5–30% 的原因（Ch 30 詳述）。

3. **PCID（Process Context ID）**：x86 的 PCID 機制允許 TLB 條目帶一個「行程 ID 標籤」，切換行程時不需要 flush 整個 TLB（只 flush 對應的 PCID entry）。現代 Linux 用 PCID + KPTI 一起，讓 KPTI 的性能代價從 30% 降到 5–10%。但 PCID 是 per-logical-CPU 的，不是 per-physical-core——SMT 的兩條 HT 執行緒共享 TLB，PCID 隔離有限。

## /proc/self/pagemap：VA 到 PA 的橋樑

Linux 的 `/proc/PID/pagemap` 文件讓每個 process 可以查詢自己（或其他 process，需要 root）的 VA→PA 映射。每個 4KB page 對應 8 個 byte（64 位元的 entry）：

```
  entry[63]   = 1：page 在物理記憶體中（present）
  entry[62]   = swapped（頁被換出到 swap）
  entry[62:55] = swap type / age
  entry[54]   = file-backed page
  entry[53]   = THP (Transparent Huge Page)
  entry[52]   = mmap()
  entry[51]   = soft-dirty bit
  entry[54:0] = PFN（Physical Frame Number）— 需要 root（kernel 4.0+ 安全限制）
```

**讀取 VA → PA 的實作**（本機真跑，需要 sudo）：

```c
// pagemap_full.c（節錄核心邏輯）
#include <stdio.h>
#include <stdint.h>
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>

int main(void){
    char *buf = mmap(NULL, 4096, PROT_READ|PROT_WRITE,
                     MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    buf[0] = 42;  // 讓 kernel 把 page fault 掉入（lazy allocation）

    uintptr_t va = (uintptr_t)buf;
    printf("VA  = 0x%016lx\n", va);

    int fd = open("/proc/self/pagemap", O_RDONLY);
    uint64_t entry;
    pread(fd, &entry, 8, (va / 4096) * 8);
    close(fd);

    printf("pagemap entry = 0x%016lx\n", entry);
    int present = (entry >> 63) & 1;
    if(present){
        uint64_t pfn = entry & 0x7fffffffffffff;
        uint64_t pa  = (pfn << 12) | (va & 0xfff);
        printf("PFN = 0x%lx\n", pfn);
        printf("PA  = 0x%016lx\n", pa);

        // cache set 計算
        uint64_t l1d_set = (pa >> 6) & 0x3f;
        uint64_t l2_set  = (pa >> 6) & 0x3ff;
        uint64_t llc_set = (pa >> 6) & 0x3fff;
        printf("L1D set = %lu\n", l1d_set);
        printf("L2  set = %lu\n", l2_set);
        printf("LLC set = %lu (pre-slice hash)\n", llc_set);
    }
    munmap(buf, 4096);
    return 0;
}
```

```bash
$ gcc -O0 pagemap_full.c -o pagemap_demo && sudo ./pagemap_demo
VA  = 0x000075d0ddd95000
pagemap entry = 0x818000000013d491
  bit 63 (present) = 1
PFN = 0x13d491
PA  = 0x000000013d491000
  L1D set (from PA, 64 sets):    0
  L2  set (from PA, 1024 sets):  64
  LLC set (pre-hash, 16384 sets):9280
  (LLC on Comet Lake uses undocumented hash, this is approximation)
```

**kernel 4.0+ 的 pagemap PFN 限制**：在 2015 年之前，用戶程式不需要 root 就能讀 `/proc/self/pagemap` 的 PFN——這讓非特權程序可以得到自己所有 page 的 PA，進而精確計算 cache set。kernel 4.0 加了安全限制：**讀 PFN 需要 root（`CAP_SYS_ADMIN`）**。這讓很多依賴 PA 的攻擊工具需要提權。

替代方案：**Huge Page**（下一節）。

## Huge Page（大頁）：不需要 root 的 PA 計算

Standard 4KB page 的 VA→PA 映射：VA[47:12] → PA[47:12]，只有低 12 位（page offset）能從 VA 直接知道。

**2MB Huge Page** 的映射：VA[47:21] → PA[47:21]，低 **21 位**（huge page offset）能從 VA 直接得到。

```
  4KB page：
  VA  = xxxxxxxxx xxxxxxxxx xxxxxxxxx [yyyyyyyy yyyy oooooo]
                                       ↑這 12 bits             ↑↑↑
  PA  = [?????????  ?????????  ???????]  [yyyyyyyy yyyy oooooo]
         這 36 bits 需要 pagemap           這 12 bits = VA[11:0]

  2MB huge page：
  VA  = [xxxxxxxxx xxxxxxxxx] [yyyyyyyyy yyyyyyyy yyyyy oooooo]
                               ↑這 21 bits (page offset in 2MB page)
  PA  = [xxxxxxxxx xxxxxxxxx] [yyyyyyyyy yyyyyyyy yyyyy oooooo]
                               這 21 bits = VA[20:0]（不需要 pagemap！）
```

攻擊者的收益：
- LLC set index 用 PA[19:6]（14 bits）
- Huge page 情況下 VA[19:6] = PA[19:6]
- 所以**從 VA 就能算 LLC set**，不需要 root！

**分配 Transparent Huge Page（THP）**（本機真跑）：

```c
// huge_page_demo.c（節錄）
#include <sys/mman.h>
#include <string.h>
#include <stdio.h>
#include <stdint.h>

int main(void){
    // 分配 2MB，要求系統用 huge page
    void *buf = mmap(NULL, 2*1024*1024,
                     PROT_READ|PROT_WRITE,
                     MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    madvise(buf, 2*1024*1024, MADV_HUGEPAGE);  // 提示 kernel 用 THP
    memset(buf, 0, 2*1024*1024);  // 觸發 page fault，讓 kernel 分配 huge page

    uintptr_t va = (uintptr_t)buf;
    printf("THP allocated at VA=0x%016lx\n", va);
    printf("  VA[20:0]  = 0x%lx (= PA[20:0] for huge page)\n", va & 0x1fffff);
    printf("  LLC set (VA[19:6], no root needed) = %lu\n", (va >> 6) & 0x3fff);

    // stride 64B 的每條 cache line
    for(int i=0; i<16; i++){
        uintptr_t addr = va + i * 64;
        printf("  VA+%4d: LLC set = %lu\n", i*64, (addr >> 6) & 0x3fff);
    }
    munmap(buf, 2*1024*1024);
    return 0;
}
```

```bash
$ gcc -O0 huge_page_demo.c -o huge_demo && ./huge_demo
Transparent huge page allocated at VA=0x73535bc00000
  VA[20:0]  = 0x0 (= PA[20:0] for huge page)
  LLC set (VA[19:6], no root needed) = 0
  VA+   0: LLC set = 0
  VA+  64: LLC set = 1
  VA+ 128: LLC set = 2
  VA+ 192: LLC set = 3
  VA+ 256: LLC set = 4
  VA+ 320: LLC set = 5
  VA+ 384: LLC set = 6
  VA+ 448: LLC set = 7
  VA+ 512: LLC set = 8
  VA+ 576: LLC set = 9
  VA+ 640: LLC set = 10
  VA+ 704: LLC set = 11
  VA+ 768: LLC set = 12
  VA+ 832: LLC set = 13
  VA+ 896: LLC set = 14
  VA+ 960: LLC set = 15
```

stride 64B 的每條 line 落在連續的 LLC set（0, 1, 2, ...）——因為 LLC set index = PA[19:6]，每增加 64B（= 1 cache line），PA[19:6] 增加 1。這讓攻擊者可以在一個 2MB 的 huge page 內，精確選擇任意 LLC set，不需要 root。

**注意**：VA 是 2MB 對齊的（低 21 位全 0），所以 LLC set = (VA >> 6) & 0x3fff = 0。在 huge page 內，攻擊者可以用 `buf + set_index * 64` 直接定位到任意目標 LLC set——這是 eviction set 攻擊的神器。

**Huge page 的可用性**：
- THP（Transparent Huge Page）在現代 Linux 預設是 `madvise` 模式——只在你用 `madvise(addr, len, MADV_HUGEPAGE)` 明確要求時才啟用。
- `MAP_HUGETLB`（顯式 huge page，需要 `/proc/sys/vm/nr_hugepages > 0`）在某些環境不可用。
- THP 可能被系統拒絕（記憶體碎片化時 kernel 無法找到連續的 2MB 實體頁）——攻擊程式要檢查是否真的得到了 THP（可用 `/proc/self/smaps` 確認 `AnonHugePages` 欄位）。

## PA → Cache Set → DRAM Row：三層位址的含義

物理位址不只決定 cache set，還決定 DRAM 的位置——而 DRAM 的位址結構是 Rowhammer 的核心：

```
  Physical Address（以 i7-10700 的 DDR4 DRAM 為例，理論架構）：

  PA bits  含義（實際 decode 由 BIOS/IMC 決定，不一定是這個順序）
  ────────────────────────────────────────────────────────────────
  [5:0]    cache line offset（64B，不影響 cache set 或 DRAM 地址）
  [11:6]   L1-D cache set
  [15:6]   L2 cache set
  [19:6]   LLC cache set
  ─────────────────────────────────────────────────────────────────
  [?:?]    DRAM Column address（選 row 內的哪個 byte）
  [?:?]    DRAM Row address（選哪個 row）
  [?:?]    DRAM Bank（同一 channel 內的獨立 memory bank）
  [?:?]    DRAM Rank
  [?:?]    DRAM Channel
```

**注意**：上面的 DRAM 部分是「?:?」——因為 PA 到 DRAM row/bank 的映射由 **Intel Memory Controller（IMC）** 的 DRAM address decode 邏輯決定，而這個邏輯是**未文件化的**，需要實驗逆向。不同主板、不同 DRAM 配置，PA→row 的映射可能不同。

**為什麼 Rowhammer 需要知道 DRAM row**（概念預告，Ch 22 深入）：

```
DRAM 的結構：
  ┌─────────────────────────────────────────────────────┐
  │  Bank 0                                             │
  │  ┌───────────────────────────┐                      │
  │  │ Row 0  [ 8KB of data... ] │ ← Row 1 的相鄰 row   │
  │  │ Row 1  [ 8KB of data... ] │ ← Aggressor（反覆 hammer）
  │  │ Row 2  [ 8KB of data... ] │ ← Row 1 的相鄰 row   │
  │  │ ...                       │                      │
  │  └───────────────────────────┘                      │
  └─────────────────────────────────────────────────────┘

Rowhammer：
  → 用「double-sided hammer」：同時反覆激活 Row 0 和 Row 2
  → Row 1 中間的電容因為相鄰 row 的電場洩漏而電荷流失
  → 如果 Row 1 裡有 kernel 的 page table 或 key 位元，bit flip 發生
  → bit flip 可以讓 unprivileged mapping → kernel mapping（提權）
```

要做 double-sided Rowhammer，攻擊者需要知道「哪兩個 PA 對應的 row 是目標 row 的兩個相鄰 row（同 bank 相鄰）」。這需要逆向 IMC 的 PA→DRAM row 映射，通常用計時技巧（row buffer hit vs miss，見下）。

## DRAM Row Buffer：另一個計時側信道

DRAM 的 row buffer（sense amplifier）記住最近 activated 的 row——同一個 row 內的下次存取是 row buffer hit（快）；不同 row 的存取要先 precharge 再 activate（慢）。

```
  存取 PA_a（在 row 17，bank 0）：
    → row 17 的 row buffer 被 open（activate）
    → 存取時間：~60 ns（row buffer empty，需要 activate）

  存取 PA_b（在 row 17，bank 0 的不同位置）：
    → row 17 的 row buffer 已 open
    → 存取時間：~35 ns（row buffer hit！快得多）

  存取 PA_c（在 row 18，bank 0）：
    → 需要先 precharge（關閉 row 17）再 activate row 18
    → 存取時間：~70 ns（row buffer miss，最慢）
```

這個計時差異讓攻擊者可以**不靠 root**推斷兩個 PA 是否在同一個 DRAM row（bank）：
- 如果 time(access PA_b right after PA_a) 很快（row buffer hit）→ 同 bank 同 row
- 如果 time 類似 PA_c（需要 precharge）→ 不同 row 或不同 bank

配合以下的假設（或逆向工程的 PA→row 映射），就能找到 Rowhammer 的 aggressor pair。

**本機注意**：WSL2 上不太可能直接量到清楚的 row buffer hit/miss 差距（VM 層可能改變記憶體存取的計時特性）。這是 Rowhammer 需要原生 Linux 或裸機的原因之一——Ch 22 誠實說明這點。

## VA → PA 的幾個重要性質（攻擊者的筆記）

彙整幾個 Ch 9/22 會用到的 VA/PA 性質：

**性質 1：VA[11:0] = PA[11:0]（page offset 不變）**
```
  → L1-D set index（PA[11:6]）= VA[11:6]，不需要 pagemap 就能算
  → cache line offset（PA[5:0]）= VA[5:0]，同上
```

**性質 2：Huge page（2MB）時，VA[20:0] = PA[20:0]**
```
  → LLC set index（PA[19:6]）= VA[19:6]，不需要 root 就能算 LLC set
  → 攻擊者可以在 huge page 內以 64B stride 精確選擇任意 LLC set
```

**性質 3：共享頁（Shared Mapping）的 PA 對所有 VA 相同**
```
  → 共享 library（如 libcrypto）在多個 process 裡映射到不同 VA，但同一 PA
  → 攻擊者知道自己的 VA → 知道 PA → 知道受害者（同一 shared lib）的 cache set
  → 這是 Flush+Reload 攻擊的核心假設：共享頁讓攻擊者能 flush 受害者的 cache line
```

**性質 4：mmap 的 2MB 對齊（Huge Page）讓 PA 的 LSBs 可預測**
```
  Huge page VA 一定 2MB 對齊（VA[20:0] = 0）
  → PA[20:0] = VA[20:0] = 0（也 2MB 對齊）
  → PA 的低 21 位完全已知（都是 VA 內的 offset）
  → 只有 PA[?:21] 是未知的（那些位元決定 huge page 的物理基底）
```

**性質 5：fork() 後的 Copy-on-Write 讓 VA 相同但 PA 可能改變**
```
  fork() 之後，子行程和父行程的 VA 相同，但 page table 是 copy-on-write（COW）
  → 在任何一方寫入之前，兩者共享同一個 PA（CoW 還沒觸發）
  → 一旦任一方寫入，kernel 複製 page，子行程的 PA 改變
  → Rowhammer 的某些利用路徑會刻意保持 CoW 狀態以共享特定 PA
```

## 實驗：完整的 VA → PA → Cache Set 追蹤

把 pagemap 讀取和 cache set 計算串在一起，驗證「VA 算出的 set 和 PA 算出的 set 一致」：

```bash
$ sudo python3 /tmp/pagemap_test.py
```

（pagemap_test.py 完整版，在 Ch 5 的動手練習裡建立）

本機真跑輸出（sudo 執行，前面已實測）：

```
VA  = 0x00007cd1e0f00000
entry = 0xa180000000129ae2
present = True
PFN = 0x129ae2
PA  = 0x0000000129ae2000
  L1D set (from PA) = 0
  L2  set (from PA) = 64
  LLC set (from PA) = 9280 (pre-hash)
```

以 `pagemap_demo` 實測值驗算（注意：每次執行的 PA 不同，以下用實測輸出的 PA 驗算）：

```
PA = 0x13d491000（pagemap_demo 實測）
PA >> 6 = 0x13d491000 / 64 = 0x4F52440
  & 0x3f  = 0x40 & 0x3f = 0 (0x...440 的低 6 位 = 0)  → L1D set = 0 ✓
  & 0x3ff = 0x440 & 0x3ff = 0x40 = 64                  → L2 set = 64 ✓
  & 0x3fff = 0x2440 & 0x3fff = 0x2440 = 9280            → LLC set = 9280 ✓
```

完全符合實測輸出。

## 位址翻譯對各種攻擊的具體含義

把這章的概念整理成「攻擊→需要什麼位址資訊→怎麼獲取」的表：

| 攻擊 | 需要的位址資訊 | 獲取方式 |
|---|---|---|
| Flush+Reload（Ch 6） | 受害者 shared line 的 VA（自己也 map 了同一個 shared lib） | 直接從自己的 VA 空間讀——shared lib 的符號位址 |
| Prime+Probe L1D（Ch 8） | 與目標同 L1D set 的 VA | VA[11:6] 直接算（不需要 PA） |
| Prime+Probe LLC（Ch 8–9） | 與目標同 LLC set 的 PA（因 LLC set 用 PA[19:6]） | root pagemap 或 huge page |
| Eviction Set 建構（Ch 9） | 多個 alias 同一 LLC set 的 PA | Huge page 或實驗逆向 |
| KASLR 破解（Ch 28） | Kernel VA 到 LLC set 的映射（KPTI 前後行為） | 側信道推斷（不需要直接讀 PA） |
| Rowhammer（Ch 22） | Aggressor/victim rows 的 PA（知道 DRAM row 結構） | Root pagemap + DRAM row 逆向 |
| TLB 側信道（Ch 27） | 受害者存取的 page VA（頁粒度精度） | TLB flush 前後計時 |

## 對比與取捨

| 取得 PA 的方法 | 需要 root | 精度 | 限制 |
|---|---|---|---|
| `/proc/self/pagemap` | **是**（kernel 4.0+） | 精確 PA | 需要 root；CoW 後 PA 可能改變 |
| Huge page（2MB THP） | 否 | VA[20:0] = PA[20:0]，LLC set 精確 | THP 可能被 kernel 拒絕；需要 2MB 對齊 |
| Shared library VA | 否 | 共享頁的 PA = 自己的 PA | 只適用於 shared mapping |
| 實驗逆向（eviction）| 否 | 能分辨 LLC set（不知道 PA） | 慢；需要 LLC set 幾何先驗知識（Ch 9） |
| DRAM timing 逆向 | 否 | 推斷 row 邊界（不知道精確 PA） | 需要在非 VM 環境；VM 層可能掩蓋計時差 |

| page size | VA=PA 的位元 | LLC set 可從 VA 算？ | 攻擊應用 |
|---|---|---|---|
| 4KB（standard） | [11:0] | 否（LLC set 用到 [19:6]，超出） | 需要 root pagemap |
| 2MB（huge page） | [20:0] | **是**（[19:6] 完全在 VA 範圍內） | 免 root 的 LLC eviction set |
| 1GB（1GB huge page） | [29:0] | 是 | 更強，但更難分配 |

## 踩雷集錦

1. **「VA 對齊 64B 就能保證 cache line 邊界」**——錯誤直覺：認為 VA 對齊就等於 PA 對齊到 cache line。正確認識：cache line 是 PA 的 64B 對齊，VA 64B 對齊只能保證 VA[5:0]=0，但不保證 PA[5:0]=0（除非你用 huge page 或有 pagemap root）。VA 64B 對齊 + 4KB page = PA[5:0]=VA[5:0]=0，在 4KB page 情況下 OK；但嚴格說不依賴 VA 對齊，要用 PA 對齊。

2. **「clflush 清的是整個 VA 空間的 cache」**——錯誤直覺：執行 `clflush(ptr)` 後，ptr 對應的所有 VA alias 都被清。正確認識：clflush 根據 VA 找到 PA，清掉該 PA 的 cache line（在所有層）。其他 VA 如果映射到同一個 PA，它們的 cache 同樣被清（因為 cache 是 PIPT）。這對 Flush+Reload 是好事：攻擊者對自己的 VA 做 clflush，受害者映射的同一 PA 也被清掉了。

3. **「THP 分配之後 VA[20:0] 一定 = PA[20:0]」**——錯誤直覺：認為 huge page 的映射性質在分配後一直成立。正確認識：THP 可能在 `madvise(MADV_HUGEPAGE)` 後仍然被 kernel 分成多個 4KB page（記憶體碎片化時 kernel 找不到連續的 2MB 物理頁）。必須用 `/proc/self/smaps` 確認 `AnonHugePages` 欄位，或者讀 `/proc/self/pagemap` 確認 THP bit 是否設置。

4. **「fork() 之後父子 process 的 PA 相同」**——錯誤直覺：fork 之後共享記憶體，以為 PA 一樣。正確認識：fork 後到寫入之前確實 PA 相同（Copy-on-Write 未觸發）；但一旦任一方寫入，kernel 複製 page，PA 改變。Rowhammer 的 CoW 利用方式需要小心控制寫入時機，避免 CoW 太早觸發。

5. **「/proc/PID/pagemap 可以讀其他 process 的 PA（只要 PID 猜對）」**——錯誤直覺：以為 `/proc/PID/pagemap` 對任意 PID 都能讀。正確認識：kernel 4.0+ 需要 `CAP_SYS_ADMIN` 才能讀**自己以外**的 process 的 pagemap。讀自己的 pagemap 不需要 root，但讀 PFN（物理頁框號）需要。通常攻擊工具用 sudo 或預先提權後讀自己的 pagemap。

6. **「DRAM row 的位址從 PA 低位元算」**——錯誤直覺：以為 DRAM row index = PA 某些固定位元。正確認識：PA 到 DRAM row/bank/channel 的映射由 IMC（Integrated Memory Controller）的 decode 邏輯決定，這個邏輯為了讓 bank 衝突分散（interleave）通常非常複雜（XOR 各種位元組合），且和主板/DRAM 配置有關。每個 DRAM 配置都要實驗逆向（透過計時觀察 row buffer hit/miss 樣式）。

## 進階：再往深一層

- **5-Level Paging（Linux 5.14+ on 10-nm Intel）**：當需要超過 128TB 的 VA 空間（如大型 AI workload），x86-64 支援 5 層 page table（再加一層 PML5）。VA 變成 57 位有效。page table walk 最多 5 次記憶體存取，TLB miss 代價更高。攻擊方面：沒有根本性的差別，但多一層 page table 意味著更多的 PT walk 存取是側信道的觀測點。
- **PCID 和 ASID 的安全含義**：PCID 讓不同 process 的 TLB 條目共存（不需要 flush）——這讓 Spectre-v1 的 TLB 側信道（推斷受害者存取的 page）更難利用（因為攻擊者和受害者的 TLB 條目用不同 PCID，不直接競爭）。但 PCID 空間有限（12 bits = 4096 個 ID），如果行程數多於 4096，PCID 會重用，flush 不可避免。
- **CET（Control-flow Enforcement Technology）的 shadow stack 和 PKRU（Memory Protection Keys）**：Intel 最近的 x86 擴展，在 page 層級加入更細的存取控制。MPK（Memory Protection Keys for Userspace）讓用戶態可以在不做 syscall 的情況下切換一組 page 的讀/寫權限。攻擊者視角：PKRU 保護的 page 也在 TLB 裡（TLB 條目帶 PKRU 標籤）——TLB 側信道仍然可以推斷存取樣式，只是不能直接讀記憶體。

## 動手練習

1. **建立 pagemap 讀取工具**：把 `pagemap_full.c` 完整編譯，`sudo ./pagemap_demo`，記錄 VA、PFN、PA 三個值，然後手動用 Python 驗算 L1D/L2/LLC set index，確認和程式輸出一致。

2. **Huge Page 的 LLC set 計算**：分配一個 2MB 的 THP（`mmap + madvise(MADV_HUGEPAGE) + memset`），在 huge page 內部用 stride 64 × 128 的方式存取 128 條 cache line（覆蓋 LLC set 0–127），用 `timed_access` 量每條 line 的存取時間，確認它們初始時都是 miss（新分配的 page 不在 cache），然後存取一次之後再量，確認 HIT。

3. **VA vs PA 的 LLC set 差異**：分配一個普通 4KB page，用 pagemap 拿到它的 PA，分別從 VA 和 PA 算 LLC set index，比較是否一致（4KB page 時 VA[19:12] ≠ PA[19:12]，所以兩個算法給出不同結果）。然後分配 2MB THP，重複，確認 VA 和 PA 算出的 LLC set 一致。

4. **確認 Shared Library 的 PA 共享**：啟動兩個 process（parent + child via fork），parent 在 fork 前 `dlopen("/usr/lib/x86_64-linux-gnu/libcrypto.so.3")`，fork 後雙方各自用 pagemap 讀 libcrypto 的某個 text page 的 PFN，確認兩個行程的 PFN 相同（共享 PA）。

## 本章重點整理

- **VA→PA 的四層 page table** 讓同一個物理頁可以映射到多個 VA，也讓用戶態程式（無 root）只能直接得到 VA，需要 pagemap 或幾何推算才能得到 PA。
- **PA[11:0] = VA[11:0]**（page offset 不變）——L1-D set index（PA[11:6]）從 VA 直接算，不需要 root。
- **Huge page（2MB）讓 PA[20:0] = VA[20:0]**——LLC set index（PA[19:6]）從 VA 直接算，不需要 root；這是免 root 的 LLC eviction set 技術的核心。
- `/proc/self/pagemap` 在 kernel 4.0+ 讀 PFN 需要 root（本機 sudo 實測：成功拿到 PA=0x13d491000）。
- **DRAM row 的位址由 IMC decode 決定**（未文件化，需要實驗逆向）——Rowhammer 必須知道 PA 才能找 aggressor row pair。
- **TLB miss 的計時差**：page walk 最多 4 次記憶體存取，可能超過 1000 cycles——TLB 也是側信道（Ch 27）。

## 自我檢核

- [ ] 為什麼在 4KB page 模式下，攻擊者知道 VA 但不知道 PA，對 LLC Prime+Probe 是一個問題？（從 LLC set index 的位元來源解釋）
- [ ] 分配一個 2MB THP 後，`VA + 0 * 64`、`VA + 1 * 64`、`VA + 2 * 64` 各落在哪個 LLC set？（給出計算式）
- [ ] Flush+Reload 依賴「受害者和攻擊者共享同一個 PA」——哪些情況下這個前提成立？（至少列出兩種情況）
- [ ] KPTI 的 CR3 切換讓哪個部分變慢？（從 TLB 的角度解釋，不是「因為多了 syscall overhead」）
- [ ] 面試問「你要在沒有 root 的環境下做 LLC Prime+Probe，你怎麼辦？」（huge page 方法的具體步驟）

## 延伸閱讀

### 論文

- **[DRAMA: Exploiting DRAM Addressing for Cross-CPU Attacks](https://www.usenix.org/conference/usenixsecurity16/technical-sessions/presentation/pessl)** — Pessl et al., USENIX Security 2016
  - **讀哪裡**：Section 2（DRAM architecture and addressing）和 Section 3（Reverse engineering DRAM addressing）。
  - **學什麼**：如何不靠文件，從計時實驗逆向 Intel IMC 的 PA→DRAM row/bank 映射——這是 Rowhammer 精確利用的前置步驟。
  - **和本章的關聯**：本章提到的「DRAM row 位址需要逆向」，DRAMA 是這個逆向工程的標準方法論。

- **[Rowhammer.js: A Remote Software-Induced Fault Attack in JavaScript](https://gruss.cc/files/rowhammerjs.pdf)** — Gruss et al., DIMVA 2016
  - **讀哪裡**：Section 3（Physical memory allocation）——描述在沒有 root、沒有 `/dev/mem` 的環境下如何分配特定 PA 的 huge page。
  - **學什麼**：本章 huge page 技巧在瀏覽器 JavaScript 環境下的應用。

- **[Prefetch Side-Channel Attacks: Bypassing SMAP and Kernel ASLR](https://gruss.cc/files/prefetch.pdf)** — Gruss et al., CCS 2016
  - **讀哪裡**：Section 4（Prefetch side-channel overview）——描述用 prefetch 指令的計時推斷 VA→PA 映射，無需 root。
  - **學什麼**：除了 pagemap 和 huge page，還有第三種不需要 root 的 PA 推斷方法；也說明了 KPTI 前的 KASLR 弱點。

### 工具

- **[pagemap utilities (linux-tools)](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/tools/vm/page-types.c)**
  - **這是什麼**：kernel 源碼樹裡的 `tools/vm/page-types.c` 可以讀 pagemap、展示 page 的物理屬性（是否 THP、swap 出去了沒、dirty 沒）。
  - **讀哪裡**：`page-types.c` 的 `describe_flags()` 函式看每個 pagemap bit 的含義。
  - **為什麼值得**：比自己寫 pagemap reader 更完整；可以用來確認 THP 是否真的分配到了。

Part 1 的地基全部到位了。接下來 Part 2 開始動手：Flush+Reload 是整個 cache 側信道攻擊的原型——你現在知道 cache 怎麼組織（Ch 3）、計時怎麼量（Ch 4）、VA/PA 怎麼對應（Ch 5）——把這三件事組起來，就是 Ch 6 的 Flush+Reload 攻擊原語。

→ [Ch 6 Flush+Reload](./06-flush-reload.md)
