# Ch 13 — DMA：device 怎麼讀寫 guest 記憶體

> **目標**：徹底理解 QEMU 裡 device 透過 DMA 存取 guest 物理記憶體的完整機制，以及為什麼缺乏驗證的 DMA 是 VM escape 的核心攻擊面。

> **環境**：QEMU 9.0 / x86-64 / Linux（Ubuntu 22.04/24.04，需自編帶 symbol 的 debug QEMU）

---

## 為什麼需要這個？

在真實硬體上，DMA（Direct Memory Access，直接記憶體存取）讓外設不繞過 CPU、直接讀寫主記憶體——你設定一個物理位址和長度，告訴網卡「把這塊記憶體裡的資料送出去」，網卡就自己去搬，不用軟體一位元組一位元組地複製。這是高吞吐 I/O 的基礎。

在 QEMU 的世界裡，「guest 的物理記憶體」實際上是 **host 行程裡的一段 mmap 區域**。當 guest 告訴一個模擬 device「去我的物理位址 0x1000000 讀 4096 bytes 資料」，這個 device（跑在 host userspace 的一段 C 程式碼）必須能找到 guest 的 0x1000000 對應 host 的哪個虛擬位址，然後去讀它。

**DMA 是 VM escape 的核心攻擊面**，原因非常直接：

1. Device 接受的是 **guest 給的 GPA（Guest Physical Address）**——一個純粹由 guest 軟體控制的數字。
2. 如果 device 不驗證這個位址和長度是否合法（在 guest RAM 範圍內、沒有越界），攻擊者可以讓 device 去讀寫它根本不該碰的 host 記憶體區域。
3. 從 guest 角度，操控 DMA 暫存器和操控其他任何 MMIO 暫存器完全一樣——你只需要能存取 BAR。

歷史上這條路出過多個嚴重 CVE：VENOM（CVE-2015-3456）讓 guest 透過 FDC DMA 觸發 host 的 out-of-bounds write；e1000 系列多個洞都和 DMA 描述符的長度驗證缺失有關。

---

## 先建立直覺

在看任何 API 前，先把 DMA 的角色定位清楚：

```
Guest 軟體（kernel driver / userspace）
        │
        │  寫 DMA 暫存器（MMIO/PIO）
        ▼
 QEMU device emulation（host userspace C code）
        │
        │  呼叫 dma_memory_read / pci_dma_read / address_space_read
        ▼
 AddressSpace / MemoryRegion 翻譯層（Ch 10 的主角）
        │
        │  GPA → 找到對應的 MemoryRegion → 轉成 host VA
        ▼
 Guest RAM（host 行程裡的 mmap 區域，或 bounce buffer）
```

重點：**device 只知道 GPA，它自己不做 GPA→HPA 翻譯**。翻譯是 QEMU 的 memory API 做的。Device 把 GPA 丟給 API，API 回傳資料。Device 能不能相信 guest 給的 GPA 和長度，全靠 device 自己檢查——QEMU 底層 API 不會自動幫你攔截惡意位址（它只負責翻譯，不負責授權）。

---

## 核心 API：四種存取方式

### 1. `dma_memory_read` / `dma_memory_write`

```c
// include/sysemu/dma.h
MemTxResult dma_memory_read(AddressSpace *as, dma_addr_t addr,
                             void *buf, dma_addr_t len, MemTxAttrs attrs);

MemTxResult dma_memory_write(AddressSpace *as, dma_addr_t addr,
                              const void *buf, dma_addr_t len, MemTxAttrs attrs);
```

這是最直接的 DMA 介面。`as` 通常是 `pci_get_address_space(dev)`（PCI device 的 AddressSpace，代表 guest 物理位址空間），`addr` 是 **GPA**，`buf` 是 host 端的緩衝區，`len` 是長度。

`MemTxResult` 告訴你存取是否成功（`MEMTX_OK` = 0）；失敗通常是因為 GPA 不在任何 MemoryRegion 裡。**它不告訴你 GPA 是否「安全」或「來自 guest 授權範圍」。**

`MemTxAttrs` 在一般 device DMA 中用 `MEMTXATTRS_UNSPECIFIED` 即可；它主要用來傳遞安全世界屬性（TrustZone）和 IOMMU hint，暫不深入。

### 2. `pci_dma_read` / `pci_dma_write`

```c
// include/hw/pci/pci.h
static inline MemTxResult pci_dma_read(PCIDevice *dev, dma_addr_t addr,
                                        void *buf, dma_addr_t len)
{
    return dma_memory_read(pci_get_address_space(dev), addr, buf, len,
                           MEMTXATTRS_UNSPECIFIED);
}
```

就是 `dma_memory_read` 的 PCI 包裝，自動帶入 PCI device 的 AddressSpace。在 PCI device 的 `.c` 裡幾乎都用這個，省得每次傳 AddressSpace。

### 3. `address_space_read` / `address_space_write`

```c
// include/exec/memory.h
MemTxResult address_space_read(AddressSpace *as, hwaddr addr, MemTxAttrs attrs,
                                void *buf, hwaddr len);
MemTxResult address_space_write(AddressSpace *as, hwaddr addr, MemTxAttrs attrs,
                                 const void *buf, hwaddr len);
```

比 `dma_memory_*` 更底層。`dma_memory_read` 本身就是 `address_space_read` 的小包裝（加上 dma-reentrancy 保護）。大部分情況下，device 用 `pci_dma_read/write` 即可；`address_space_*` 是 memory API 的通用入口，也用在非 DMA 場合（例如 CPU 存取 memory）。

### 4. `address_space_map` / `address_space_unmap`

這是最重要、也最容易誤解的 API：

```c
// include/exec/memory.h
void *address_space_map(AddressSpace *as, hwaddr addr, hwaddr *plen,
                         bool is_write, MemTxAttrs attrs);
void address_space_unmap(AddressSpace *as, void *buffer, hwaddr len,
                          bool is_write, hwaddr access_len);
```

`address_space_map` 回傳一個 **host 虛擬位址指標**，直接指向 guest 記憶體（或 bounce buffer）。這讓 device 可以做零拷貝操作——拿到指標後直接讀寫，不需要中間 `buf`。

**兩種情況**：
- **直接映射**：如果 guest 記憶體是 QEMU 行程 mmap 的一塊連續 RAM（`memory_region_get_ram_ptr` 有效），`map` 直接回傳那段 host VA，沒有拷貝。
- **Bounce buffer**：如果 GPA 落在某個需要模擬的 MemoryRegion（例如有 `.read`/`.write` callback 的 I/O 區域），QEMU 會分配一個 host 端臨時緩衝區（bounce buffer），在 `map` 時把資料拷貝進去，讓 device 操作；`unmap` 時把結果寫回。

`plen` 是 in/out 參數：傳入你想要的長度，API 可能回傳更小的值（表示只映射了部分，通常是跨 MemoryRegion 邊界）。Device 必須處理這個「短映射」情況，否則只讀/寫了部分資料還以為全做完了。

```c
// 典型用法（未實測，理論預期）
hwaddr len = dma_len;
void *host_ptr = address_space_map(as, gpa, &len, true, MEMTXATTRS_UNSPECIFIED);
if (!host_ptr) {
    /* 映射失敗，GPA 不合法或超出範圍 */
    return;
}
/* 直接操作 host_ptr 指向的 guest 記憶體 */
memcpy(host_ptr, src, len);
address_space_unmap(as, host_ptr, dma_len, true, len);
```

**安全重點**：`address_space_map` 回傳的是真正的 host VA。如果 device 拿到這個指標後繼續往後寫超出 `len` 的範圍，就是在 host 記憶體上做 OOB 寫入——後果和普通 heap overflow 一樣，但觸發者是 guest 的 DMA 操作。

---

## 主範例：edu.c 的 DMA 實作

QEMU 的 `hw/misc/edu.c` 是官方為教學目的寫的示範 device（EDU device），它包含一個完整的 DMA 實作，是理解 DMA 機制的最佳入口。

### EDU device 的 DMA 暫存器

```c
// hw/misc/edu.c（QEMU 9.0）
#define EDU_DMA_START   0x40000
#define EDU_DMA_SIZE    0x1000

/* DMA 暫存器在 BAR0 的 offset */
#define EDU_REG_DMA_SRC    0x80  /* guest 源 GPA（或 device 內部 buffer offset） */
#define EDU_REG_DMA_DST    0x88  /* guest 目的 GPA（或 device 內部 buffer offset） */
#define EDU_REG_DMA_CNT    0x90  /* 傳輸長度（bytes） */
#define EDU_REG_DMA_CMD    0x98  /* 命令：bit 0 = 開始, bit 1 = 方向 */
```

`EDU_DMA_START`（0x40000）是 device 內部的「DMA buffer 區域」起點——**這是 device 認定的「安全」內部 buffer 的 GPA 基底**。在 edu 的設計裡，src 或 dst 至少有一個要是 `EDU_DMA_START` 以上的 device 內部位址；但這個設計本身就是教學用的簡化，實際意義是展示「device 有自己的 buffer」。

### DMA 傳輸狀態機

```c
typedef struct EduState {
    PCIDevice pdev;
    MemoryRegion mmio;

    /* DMA 狀態 */
    struct {
        dma_addr_t src;
        dma_addr_t dst;
        dma_addr_t cnt;
        dma_addr_t cmd;
    } dma;

    uint32_t dma_mask;      /* DMA capable mask，通常 0xFFFFFFFF */
    char dma_buf[EDU_DMA_SIZE];  /* device 內部 DMA buffer */
    /* ... 其他欄位 ... */
    QEMUTimer *dma_timer;
} EduState;
```

### DMA 觸發流程（`edu_dma_timer`）

guest 設定好 `dma.src`、`dma.dst`、`dma.cnt`，然後寫 `dma.cmd |= EDU_DMA_START`（bit 0）。QEMU 的 MMIO write handler 偵測到 cmd 變化後，**不是立刻執行 DMA，而是啟動一個 timer**，讓 DMA 在下一個 main loop tick 裡非同步執行：

```c
static void edu_mmio_write(void *opaque, hwaddr addr, uint64_t val, unsigned size)
{
    EduState *edu = opaque;
    /* ... 其他暫存器處理 ... */

    if (addr == 0x98) {  /* DMA_CMD 暫存器 */
        edu->dma.cmd = val;
        if (val & EDU_DMA_START) {
            /* 啟動 timer，1ms 後觸發 DMA */
            timer_mod(edu->dma_timer,
                      qemu_clock_get_ms(QEMU_CLOCK_VIRTUAL) + 1);
        }
    }
}
```

timer callback `edu_dma_timer` 才是真正做 DMA 的地方：

```c
static void edu_dma_timer(void *opaque)
{
    EduState *edu = opaque;
    bool from_guest;  /* bit 1 決定方向 */

    if (!(edu->dma.cmd & EDU_DMA_START)) {
        return;
    }

    from_guest = !(edu->dma.cmd & EDU_DMA_DIR);  /* DIR bit */

    if (from_guest) {
        /* 從 guest RAM 讀資料到 device 內部 buf */
        /* src 是 GPA，dst 是 device buf 的 offset */
        uint64_t dst = edu->dma.dst;
        /* 邊界：dst 必須在 dma_buf 範圍內 */
        if (dst + edu->dma.cnt <= EDU_DMA_SIZE) {
            pci_dma_read(&edu->pdev,
                         edu->dma.src,                /* GPA */
                         edu->dma_buf + dst,          /* host buf */
                         edu->dma.cnt);
        }
    } else {
        /* 從 device 內部 buf 寫到 guest RAM */
        uint64_t src = edu->dma.src;
        if (src + edu->dma.cnt <= EDU_DMA_SIZE) {
            pci_dma_write(&edu->pdev,
                          edu->dma.dst,               /* GPA */
                          edu->dma_buf + src,         /* host buf */
                          edu->dma.cnt);
        }
    }

    /* 清 START bit，可選觸發 interrupt */
    edu->dma.cmd &= ~EDU_DMA_START;
}
```

（注意：以上程式碼是對 edu.c 邏輯的教學性整理，實際 QEMU 9.0 原始碼的寫法細節略有差異，請以 `hw/misc/edu.c` 原始碼為準。）

### 完整資料流圖

```
Guest driver
  │
  │ (1) 設定 DMA 暫存器
  │     BAR0+0x80 (DMA_SRC) = guest 想讀的 GPA，例如 0x10000
  │     BAR0+0x88 (DMA_DST) = device buf offset，例如 0
  │     BAR0+0x90 (DMA_CNT) = 128  (bytes)
  │     BAR0+0x98 (DMA_CMD) = 1    (START | from_guest)
  │
  ▼ VMEXIT → KVM → QEMU main loop → edu_mmio_write
  │
  │ (2) edu_mmio_write 啟動 dma_timer
  │
  ▼ timer tick → edu_dma_timer
  │
  │ (3) pci_dma_read(dev, gpa=0x10000, host_buf=dma_buf+0, len=128)
  │         │
  │         ▼ dma_memory_read
  │             │
  │             ▼ address_space_read
  │                 │
  │                 ▼ 翻譯 GPA 0x10000 → host VA
  │                   （QEMU RAM MemoryRegion，直接指向 mmap 區域）
  │                   memcpy(host_buf, host_va, 128)
  │
  └──→ edu->dma_buf[0..127] 現在包含 guest 物理位址 0x10000 的 128 bytes
```

**這整條路上，device（edu_dma_timer）沒有驗證 GPA 0x10000 是否是「合法的 guest driver 自己的記憶體」**——它只知道「guest 給了這個位址」。如果 guest 給的 GPA 指向 guest kernel 的 page table、或者超出 guest RAM 大小、或者是某個模擬 device 的 MMIO 區域，`pci_dma_read` 照樣嘗試去讀（行為取決於那個 GPA 對應的 MemoryRegion 是什麼）。

---

## 底層機制：GPA → Host VA 轉換路徑

```
     GPA (guest 給的物理位址)
          │
          ▼
   AddressSpace::root (MemoryRegion 樹根)
          │
          │  flatview_read_continue / memory_region_dispatch_read
          ▼
   找到負責這個 GPA 的 MemoryRegion
     ┌────────────────────────────────────────┐
     │ 情況 A：RAM MemoryRegion               │
     │   memory_region_get_ram_ptr()          │
     │   → 直接回傳 host mmap VA              │
     │   → address_space_map 用直接映射       │
     └────────────────────────────────────────┘
     ┌────────────────────────────────────────┐
     │ 情況 B：I/O MemoryRegion（有 ops）     │
     │   呼叫 ops->read / ops->write          │
     │   address_space_map → bounce buffer    │
     │   （不是直接指向某段 RAM）             │
     └────────────────────────────────────────┘
     ┌────────────────────────────────────────┐
     │ 情況 C：GPA 不在任何 MemoryRegion      │
     │   回傳 MEMTX_DECODE_ERROR              │
     │   address_space_map 回傳 NULL          │
     └────────────────────────────────────────┘
```

**對攻擊者最有利的是情況 A**：guest 給一個合法 RAM 範圍內的 GPA，API 回傳一個真實的 host VA。Device 如果不檢查長度，就能讓 DMA 讀寫任意 guest 物理記憶體。

### IOMMU 的角色（一句帶過）

真實硬體上，IOMMU（Input-Output Memory Management Unit）可以限制 device 的 DMA 只能存取被明確授權的 GPA 範圍——這是「真實」的 DMA 保護。QEMU 有 IOMMU 模擬（`intel-iommu`、`smmuv3`），但大多數 CTF 環境和研究環境不開，攻擊者預設 IOMMU 不擋。Part 7 討論現代 mitigation 時會再碰 IOMMU。

---

## 對比與取捨

| API | 使用場合 | 是否拷貝 | 主要差異 |
|-----|---------|---------|---------|
| `pci_dma_read/write` | PCI device DMA | 總是拷貝到 buf | 最簡單，PCI device 首選 |
| `dma_memory_read/write` | 任何 device DMA | 總是拷貝到 buf | 需手動傳 AddressSpace |
| `address_space_read/write` | 通用記憶體存取 | 總是拷貝到 buf | 底層通用介面，CPU 也用 |
| `address_space_map/unmap` | 大塊資料零拷貝 | 可能零拷貝（直接映射） | 回傳 host ptr，需處理短映射、bounce buffer |
| `dma_memory_map`（`dma.h`） | DMA 零拷貝 | 同 address_space_map | 比 address_space_map 多 dma-reentrancy 保護 |

---

## 踩雷集錦

**錯誤直覺 1：QEMU 底層 API 會驗證 GPA 是否在 guest 授權範圍**

錯。`pci_dma_read` 只管翻譯 GPA 並搬資料，不管 guest 有沒有「授權」這個位址。授權檢查完全是 device 自己的責任。Edu.c 的長度邊界檢查（`dst + cnt <= EDU_DMA_SIZE`）就是 device 自己寫的——你拿掉它，API 照樣執行。

**錯誤直覺 2：`address_space_map` 拿到的指標一定指向 guest RAM**

錯。GPA 如果落在 I/O MemoryRegion（比如某個 device 的 MMIO 區域），`address_space_map` 會 fallback 到 bounce buffer，回傳一個 QEMU 自己 malloc 的指標。這時候 DMA「讀」是先呼叫那個 MMIO MemoryRegion 的 `.read` callback 把資料填進 bounce buffer，再讓 device 去讀 bounce buffer——和直接讀 RAM 的語義完全不同，有時候這本身就能觸發 device 的意外行為（QEMU Reentrancy bug 的根源之一）。

**錯誤直覺 3：`cnt` 沒有驗證最多只是多讀幾個 byte**

不對。`pci_dma_read(dev, gpa, host_buf, cnt)` 裡的 `cnt` 如果超出 `host_buf` 大小，是 **host 端的 OOB 寫入**。GPA 端（guest 側）讀多少不是問題，問題是 host 端的 `buf` 是否夠大——這才是真正的 heap overflow。

**錯誤直覺 4：DMA 只有「從 guest 搬資料到 device」這一個方向**

DMA 是雙向的。Device 也可以透過 `pci_dma_write` 把資料寫進 guest 的任意 GPA。攻擊者如果能控制目標 GPA，就能讓 device 把 host 上的某段資料（包括洩漏出來的位址、偽造的 ROP chain）寫進 guest 記憶體——這是 Part 3 的 infoleak 讀取路徑之一。

**錯誤直覺 5：edu.c 的 DMA 實作是完全安全的教學示範**

不完全對。Edu 的設計有刻意加邊界檢查（`EDU_DMA_SIZE` 限制），但它檢查的是 **device 內部 buffer offset** 的邊界，不是 **GPA** 的邊界——只要 guest 給的 GPA 在合法 RAM 範圍內，edu 完全相信它。這對「用 DMA 讀 guest 其他行程的記憶體」沒有任何防護。

---

## 進階：再往深一層

### DMA Reentrancy（重入）bug

`dma_memory_read/write` 和 `address_space_read/write` 在某些路徑下會觸發 MemoryRegion 的 `.read`/`.write` callback——如果那個 callback 又觸發另一次 DMA，就會遞迴進入 DMA 處理邏輯。QEMU 9.0 之前這類重入 bug 是一個重要的漏洞類型（CVE-2021-3750 / DMA Reentrancy，by Alexander Bulekov）。QEMU 9.0 引入了 `dma_reentrancy_guard` 來緩解，但 device 實作仍需注意。

`dma_memory_read/write`（在 `include/sysemu/dma.h`）和 `address_space_read/write` 的差別之一，就是前者套了一層 reentrancy 保護。

### `dma_memory_map` vs `address_space_map`

`dma.h` 裡有 `dma_memory_map` 是 `address_space_map` 的包裝，加上 reentrancy guard，API 差不多但保護更完整。新 device 應優先用 `dma_memory_map`。

### 跨 MemoryRegion 邊界的短映射

當 DMA 範圍跨越兩個不同的 MemoryRegion 時，`address_space_map` 只能映射到第一個 MemoryRegion 的邊界，回傳的 `*plen` 小於請求長度。Device 如果沒有迴圈處理剩餘部分，就會默默漏掉後半段資料——這不是安全問題，而是 device 功能上的 bug，但容易被忽略。

---

## 動手練習

（以下步驟需要在 Linux 環境下，使用自編 debug QEMU）

1. 啟動帶 EDU device 的 QEMU：
   ```bash
   qemu-system-x86_64 -device edu \
     -kernel bzImage -append "console=ttyS0" \
     -nographic -s
   ```

2. 在 host 另一個 terminal 用 gdb attach QEMU，在 `edu_dma_timer` 設斷點：
   ```bash
   gdb -p $(pgrep qemu-system)
   (gdb) b edu_dma_timer
   (gdb) b pci_dma_read
   ```

3. 在 guest 裡寫一個小 C 程式，mmap `/dev/mem` 或 `/sys/bus/pci/devices/.../resource0`，設定 edu 的 DMA 暫存器（src = 某個你知道內容的 GPA，dst = 0，cnt = 64，cmd = 1）。

4. 觸發 DMA，觀察 host gdb 斷在 `pci_dma_read` 時的參數：`as`、`addr`（就是你的 GPA）、`buf`（指向 `edu->dma_buf`）、`len`（你設的 64）。

5. 修改 cnt 為 `0x2000`（超出 `EDU_DMA_SIZE`），觀察 edu 的邊界檢查是否攔截。

---

## 本章重點整理

- DMA 是 device 透過 GPA 直接讀寫 guest 記憶體的機制；QEMU 裡 guest RAM 是 host mmap 區域，GPA→Host VA 的翻譯由 AddressSpace / MemoryRegion 完成。
- 核心 API 層次：`pci_dma_read/write`（PCI 包裝） → `dma_memory_read/write`（帶 reentrancy 保護） → `address_space_read/write`（底層通用）。
- `address_space_map` 回傳 host VA 指標，可能是直接映射或 bounce buffer；device 必須處理短映射。
- **QEMU API 不驗證 GPA 的授權**，驗證是 device 的責任；缺失驗證 = DMA 成為 guest 控制的讀寫原語。
- EDU device 示範了「暫存器設定 → timer 非同步觸發 → `pci_dma_read/write`」的完整 DMA 生命週期。
- 邊界未檢查的 `cnt` 在 host 端造成 heap OOB；可控的 `dst` GPA 讓 device 把資料寫到 guest 任意物理位址。

---

## 自我檢核

- [ ] 我能說出 `pci_dma_read`、`dma_memory_read`、`address_space_read` 這三個 API 的層次關係，以及各自用在哪。
- [ ] 我能解釋 `address_space_map` 什麼時候回傳直接映射 host VA，什麼時候回傳 bounce buffer。
- [ ] 我能追出 edu.c 裡從 guest 寫 DMA_CMD 到 `pci_dma_read` 被呼叫的完整呼叫鏈（至少四個函式）。
- [ ] 我能解釋為什麼「QEMU API 會自動驗證 GPA 安全性」是一個危險的錯誤假設。
- [ ] 我能說出 DMA Reentrancy bug 的觸發條件和 QEMU 9.0 的緩解措施。

---

## 延伸閱讀

1. **QEMU `hw/misc/edu.c` 原始碼**（QEMU 9.0 tag）
   讀哪裡：`edu_dma_timer`、`edu_mmio_write` 的 DMA_CMD 處理、`EduState` 結構。
   學什麼：看清楚 device 如何用暫存器狀態驅動 DMA，邊界檢查寫在哪裡、可以怎麼拿掉。
   關聯：本章所有程式碼範例的一手來源，任何與教材不符之處以原始碼為準。

2. **QEMU 開發者文件：DMA helpers**（`docs/devel/memory.rst`，QEMU 官方 repo）
   讀哪裡：「DMA helpers」一節，`dma_memory_read/write`、`address_space_map` 的設計說明。
   學什麼：QEMU 官方對這些 API 的語義定義，以及 bounce buffer 的設計理由。
   關聯：本章第二節所有 API 的權威文件。

3. **CVE-2021-3750 DMA Reentrancy 分析**（Alexander Bulekov, 2021，QEMU 開發者 ML 及相關 patch）
   讀哪裡：搜尋「QEMU DMA reentrancy CVE-2021-3750」找到 patch commit 和 discussion。
   學什麼：DMA reentrancy 的觸發路徑，QEMU 9.0 的 `dma_reentrancy_guard` 是怎麼加的。
   關聯：「進階」一節的理論佐證；Ch 19 UAF 章節也會提到 reentrancy 類型。

4. **VENOM（CVE-2015-3456）技術分析**（CrowdStrike, 2015）
   讀哪裡：CrowdStrike 官方 blog 的 VENOM writeup；搜尋「VENOM CVE-2015-3456 FDC buffer overflow」。
   學什麼：FDC（Floppy Disk Controller）DMA buffer 的 OOB write 如何成為第一個廣為人知的 QEMU 逃逸洞，以及 DMA 長度未驗證的真實後果。
   關聯：Ch 23 復刻這個 CVE；這篇是最好的預習。

5. **Nguyen Anh Quynh, "QEMU Escape" 系列**（各大 security conference slides，可搜尋 SlideShare / conference proceedings）
   讀哪裡：找到其中討論 DMA 路徑的章節（通常在「Device Interface」或「Memory Access」部分）。
   學什麼：從攻擊者角度整理 DMA API 的哪些點可以被利用，與本章的防禦性描述對照。
   關聯：Ch 15、Ch 17 infoleak 的基礎視角。

---

> DMA 是 device 和 guest 記憶體之間的橋樑，也是攻擊者從 guest 影響 host heap 的第一條管道。下一章我們要看 host heap 本身的樣子——QEMU 的物件怎麼活在 `g_malloc` 的記憶體裡，function pointer 藏在哪，heap groom 要怎麼做。

→ [Ch 14](./14-qemu-heap.md)
