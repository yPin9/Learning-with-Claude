# Ch 11 — 一個 Device 怎麼被模擬：PIO/MMIO Dispatch

> **目標**：追蹤「guest 執行 MMIO 寫入 → device 的 `.write()` callback 被呼叫」的完整路徑，每一個中間層都看清楚。

---

## 為什麼需要這個？

Ch 7 我們看到 KVM 把控制權還給 QEMU，Ch 10 我們理解了 `MemoryRegion`/`FlatView` 的全貌。現在要把這兩塊連起來：QEMU 拿到 `KVM_EXIT_MMIO` 之後，到底怎麼知道該呼叫「哪個 device 的哪個 callback」？

答案就在 dispatch 機制裡。理解這個機制有兩個直接的安全用途：

1. **定位 attack surface**：所有 guest 可控的輸入（GPA、port、len、data）最終流向哪個 C 函數？哪個欄位沒有 bound check？
2. **看懂漏洞報告**：任何 QEMU device CVE 的 crash path 都從這裡出發。不懂 dispatch，看 PoC 如同看天書。

---

## 先建立直覺

完整觸發路徑，從 guest 的一條組語指令到 device callback 被呼叫：

```
Guest (Ring 0/3)
│
│  MOV DWORD PTR [0xFEB00000], 0x01   ← 一條 store 指令
│
▼
[硬體 MMU EPT miss / NPT miss]
│
▼
KVM（kernel）
│  vmexit → KVM_EXIT_MMIO
│  填寫 struct kvm_run.mmio：
│    phys_addr = 0xFEB00000
│    data      = [0x01, 0x00, 0x00, 0x00]
│    len       = 4
│    is_write  = 1
│
▼
QEMU userspace  target/i386/kvm/kvm.c
│  kvm_cpu_exec()
│  → case KVM_EXIT_MMIO
│  → cpu_physical_memory_rw()  [old name: address_space_rw]
│
▼
system/memory.c
│  address_space_rw(system_memory_as, 0xFEB00000, ...)
│  → flatview_translate(fv, 0xFEB00000)
│      回傳 MemoryRegionSection (mrs)
│      mrs.mr = &edu->mmio   ← 找到 EducationDevice 的 MemoryRegion
│      mrs.offset_within_region = 0xFEB00000 - BAR_BASE
│
▼
system/memory.c
│  memory_region_dispatch_write(mrs.mr, offset, val, size, attrs)
│  → ops = mr->ops   (= &edu_mmio_ops)
│  → 檢查 valid / impl min_access_size
│  → 若 size > impl.max_access_size → 自動拆分
│
▼
hw/misc/edu.c
   edu_mmio_write(opaque=EduState*, offset, val, size)
   offset 就是「哪個暫存器」
```

PIO（Port I/O）路徑幾乎對稱，差在 AddressSpace 是 `system_io` 而非 `system_memory`：

```
Guest: IN/OUT port 指令
→ KVM_EXIT_IO
→ kvm_handle_io()
→ address_space_rw(system_io_as, port, ...)
→ 同樣走 FlatView → MemoryRegionSection → MemoryRegionOps
```

---

## MMIO Dispatch 路徑逐層展開

### 第一層：KVM_EXIT_MMIO 的資料結構

`struct kvm_run`（linux/kvm.h）裡的 mmio 子結構：

```c
struct {
    __u64 phys_addr;        /* guest physical address */
    __u8  data[8];          /* 最多 8 bytes，實際有效長度看 len */
    __u32 len;              /* 1 / 2 / 4 / 8 */
    __u8  is_write;         /* 1 = write, 0 = read */
} mmio;
```

QEMU 在 `target/i386/kvm/kvm.c` 的 `kvm_cpu_exec()` 裡：

```c
case KVM_EXIT_MMIO:
    DPRINTF("handle_mmio\n");
    /* Called outside BQL in TCG mode, so grab it */
    address_space_rw(&address_space_memory,
                     run->mmio.phys_addr,
                     MEMTXATTRS_UNSPECIFIED,
                     run->mmio.data,
                     run->mmio.len,
                     run->mmio.is_write);
    ret = 0;
    break;
```

`phys_addr` 是 GPA（Guest Physical Address），由 guest 完全控制。`data` 陣列是 guest 要寫的值，或是 QEMU 要填回去的讀取結果。

### 第二層：address_space_rw → flatview_translate

`address_space_rw`（`system/memory.c`）的核心邏輯：

```c
MemTxResult address_space_rw(AddressSpace *as, hwaddr addr,
                              MemTxAttrs attrs, void *buf,
                              hwaddr len, bool is_write)
{
    ...
    FlatView *fv = address_space_to_flatview(as);
    ...
    while (len > 0) {
        MemoryRegionSection mrs =
            flatview_translate(fv, addr, &l, is_write, attrs, &result);
        ...
        if (is_write) {
            result = memory_region_dispatch_write(mrs.mr,
                         addr + mrs.offset_within_address_space - mrs.offset_within_region,
                         val, op, attrs);
        } else {
            result = memory_region_dispatch_read(mrs.mr, ...);
        }
        ...
    }
}
```

`flatview_translate` 的輸出 `MemoryRegionSection` 包含：
- `mrs.mr`：對應的 `MemoryRegion` 指標（指向某個 device 的 MMIO region）
- `mrs.offset_within_region`：GPA 減去該 MemoryRegion 的 base address，也就是暫存器 offset
- `mrs.size`：這次可以一次處理的長度

### 第三層：memory_access_is_direct 判斷

`memory_region_dispatch_write` 進去之前，`memory_access_is_direct()` 先判斷這個區域是否是 RAM（直接 memcpy）。對於 MMIO device，結果是 `false`，才繼續走 ops callback。

```c
/* system/memory.c */
static bool memory_access_is_direct(MemoryRegion *mr, bool is_write,
                                    MemTxAttrs attrs)
{
    if (memory_region_is_ram(mr)) {
        return !(is_write && mr->readonly);
    }
    if (memory_region_is_romd(mr)) {
        return !is_write;
    }
    return false;    /* MMIO device → 走 ops */
}
```

### 第四層：memory_region_dispatch_write

```c
MemTxResult memory_region_dispatch_write(MemoryRegion *mr,
                                         hwaddr addr,
                                         uint64_t data,
                                         MemOp op,
                                         MemTxAttrs attrs)
{
    unsigned size = memop_size(op);
    if (!memory_region_access_valid(mr, addr, size, true, attrs)) {
        return MEMTX_ACCESS_ERROR;   /* valid 範圍檢查失敗 */
    }

    if (mr->ops->write) {
        /* 直接呼叫 or 先做寬度拆分 */
        adjust_endianness(mr, &data, op);
        memory_region_write_accessor(mr, addr, &data, size,
                                     mr->ops_valid.min_access_size,
                                     mr->ops_valid.max_access_size, attrs);
    } else if (mr->ops->write_with_attrs) {
        ...
    }
    ...
}
```

`memory_region_access_valid` 檢查的是 `ops->valid.min/max_access_size`，違反直接回 `MEMTX_ACCESS_ERROR`（bus error）。通過之後，`memory_region_write_accessor` 再處理 `impl.min/max_access_size` 的拆分/合併。

---

## 底層機制：MemoryRegionOps 欄位解剖

`MemoryRegionOps`（`include/exec/memory.h`）是所有 QEMU device 的核心介面：

```c
struct MemoryRegionOps {
    /* 讀寫 callback，擇一實作 */
    uint64_t (*read)(void *opaque, hwaddr addr, unsigned size);
    void     (*write)(void *opaque, hwaddr addr, uint64_t data, unsigned size);

    /* 帶 attrs 的版本（新式），可感知 secure/non-secure */
    MemTxResult (*read_with_attrs)(void *opaque, hwaddr addr, uint64_t *val,
                                   unsigned size, MemTxAttrs attrs);
    MemTxResult (*write_with_attrs)(void *opaque, hwaddr addr, uint64_t val,
                                    unsigned size, MemTxAttrs attrs);

    enum device_endian endianness;   /* DEVICE_LITTLE_ENDIAN 或 BIG */

    struct {
        /* valid：guest 被允許的存取寬度，違反 → bus error，不進 callback */
        unsigned min_access_size;
        unsigned max_access_size;
        bool unaligned;    /* 是否允許非對齊存取 */
        /* 可選：更細的地址範圍限制 */
        bool (*accepts)(void *opaque, hwaddr addr, unsigned size,
                        bool is_write, MemTxAttrs attrs);
    } valid;

    struct {
        /* impl：callback 真正能處理的寬度 */
        /* 若 guest 做 4-byte 但 impl.max=2，QEMU 自動拆成兩個 2-byte 呼叫 */
        unsigned min_access_size;
        unsigned max_access_size;
        bool unaligned;
    } impl;
};
```

**valid vs impl 的差別**，是最容易搞混的地方：

| 欄位 | 作用 | 違反時 |
|------|------|--------|
| `valid.min/max_access_size` | 告訴 QEMU「guest 能做什麼寬度」 | bus error，不呼叫 callback |
| `impl.min/max_access_size` | 告訴 QEMU「我的 callback 能處理什麼寬度」 | QEMU 自動拆分或合併，再呼叫 callback |

**拆分/合併邏輯**的安全含義：假設 device 設定 `impl.min=4, impl.max=4`，但 guest 寫入 2 bytes。若 `valid.min=1` 允許，QEMU 會做 read-modify-write 來合成一個 4-byte 操作。這個合成過程自己就可能出 TOCTOU 或 race condition（後面章節的洞源頭之一）。

**endianness** 由 `adjust_endianness()` 在進 callback 前統一轉換，callback 看到的永遠是主機位元組序（host byte order）。

---

## edu.c 完整範例走讀

`hw/misc/edu.c` 是 QEMU 官方的教學 PCI device，代碼簡潔但功能完整，是讀 QEMU device 原始碼最好的起點。

### edu 的暫存器地圖

```c
/* hw/misc/edu.c 裡的 offset 定義（重組自原始碼） */
#define EDU_STATUS_REG          0x04   /* device status bits */
#define EDU_INTR_STATUS_REG     0x24   /* interrupt status */
#define EDU_INTR_RAISE_REG      0x60   /* write to raise IRQ */
#define EDU_INTR_ACK_REG        0x64   /* write to ack IRQ */
#define EDU_DMA_SRC_REG         0x80   /* DMA source address (GPA) */
#define EDU_DMA_DST_REG         0x88   /* DMA destination address (GPA) */
#define EDU_DMA_CNT_REG         0x90   /* DMA transfer count (bytes) */
#define EDU_DMA_CMD_REG         0x98   /* DMA command / direction */
```

### MemoryRegionOps 定義

```c
static const MemoryRegionOps edu_mmio_ops = {
    .read = edu_mmio_read,
    .write = edu_mmio_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
    .impl = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
};
```

edu 的設計最保守：`valid` 和 `impl` 都是 4/4，只接受 4-byte 對齊存取。guest 做 1-byte 寫入直接 bus error，不進 callback。

### edu_mmio_write 實作

```c
static void edu_mmio_write(void *opaque, hwaddr addr, uint64_t val,
                           unsigned size)
{
    EduState *edu = opaque;   /* device 狀態，由 opaque 轉回 */

    if (addr < 0x80 && size != 4) {
        return;   /* 額外防線（其實 valid 已擋） */
    }

    if (addr >= 0x80 && size != 4 && size != 8) {
        return;
    }

    switch (addr) {
    case 0x04:
        /* status 暫存器：bit 0 是 computing，只有 bit 7 (irqfact) 可寫 */
        edu->status = val & ~EDU_STATUS_COMPUTING;
        break;

    case 0x60:
        /* raise IRQ：寫任意值觸發中斷 */
        edu_raise_irq(edu, val);
        break;

    case 0x64:
        /* ack IRQ：清除 interrupt status 對應 bit */
        edu_lower_irq(edu, val);
        break;

    case 0x80:
        dma_rw(edu, false, &val, &edu->dma.src, false);
        break;
    case 0x88:
        dma_rw(edu, false, &val, &edu->dma.dst, false);
        break;
    case 0x90:
        dma_rw(edu, false, &val, &edu->dma.cnt, false);
        break;
    case 0x98:
        /* DMA command：bit 0 = start, bit 1 = direction */
        if (!(val & EDU_DMA_RUN)) {
            break;
        }
        dma_rw(edu, false, &val, &edu->dma.cmd, false);
        /* 啟動 timer 做非同步 DMA */
        timer_mod(edu->dma_timer,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 100);
        break;
    }
}
```

**關鍵觀察**：
1. `opaque` 是 `void *`，callback 第一件事就是把它 cast 回 `EduState *`。這個 `opaque` 是 device 初始化時傳入的，從 guest 視角看不到，但 UAF 漏洞可能讓這個指標失效。
2. `addr`（即 offset）是 guest 完全控制的。edu 有明確的 `switch`，沒有列出的 offset 直接 fall through 忽略，相對安全。但現實中很多 device 用陣列索引而非 switch，offset OOB 就是 OOB R/W。
3. `size = 0x98` 的 case 啟動了 `timer_mod`，這是 deferred 執行的開始。

### edu_mmio_read 實作

```c
static uint64_t edu_mmio_read(void *opaque, hwaddr addr, unsigned size)
{
    EduState *edu = opaque;
    uint64_t val = ~0ULL;   /* 預設回傳全 1，模擬 bus floating */

    switch (addr) {
    case 0x00:
        val = 0x010000edu;  /* device ID，硬編碼 */
        break;
    case 0x04:
        val = edu->status;
        break;
    case 0x08:
        qemu_mutex_lock(&edu->thr_mutex);
        val = edu->fact;
        qemu_mutex_unlock(&edu->thr_mutex);
        break;
    case 0x24:
        val = edu->intr_status;
        break;
    case 0x80:
        dma_rw(edu, false, &val, &edu->dma.src, true);
        break;
    /* ... 其餘 DMA 暫存器 ... */
    }

    return val;
}
```

注意 `0x08` 這個 case 有 `mutex`，因為背景執行緒也可能寫 `edu->fact`。mutex 保護是對的，但不是所有 device 都這麼謹慎。

### EduState 的初始化與 memory_region_init_io

```c
static void pci_edu_realize(PCIDevice *pdev, Error **errp)
{
    EduState *edu = EDU(pdev);
    ...
    /* 建立 MMIO MemoryRegion，大小 1MB */
    memory_region_init_io(&edu->mmio, OBJECT(edu), &edu_mmio_ops,
                          edu,         /* opaque，傳入 callback */
                          "edu-mmio",
                          1 * MiB);

    /* 把這個 MemoryRegion 作為 PCI BAR 0 */
    pci_register_bar(pdev, 0, PCI_BASE_ADDRESS_SPACE_MEMORY, &edu->mmio);

    /* 初始化 DMA timer，callback = edu_dma_timer */
    edu->dma_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, edu_dma_timer, edu);
    ...
}
```

`memory_region_init_io` 的第四個參數 `edu`（`EduState *`）就是之後每次 callback 收到的 `opaque`。這條線從初始化一直到 callback 呼叫，物件身份就靠這個指標維繫。

### DMA Timer：deferred 執行

```c
static void edu_dma_timer(void *opaque)
{
    EduState *edu = opaque;   /* 與 mmio callback 同一個指標 */
    bool raise_irq = false;

    if (!(edu->dma.cmd & EDU_DMA_RUN)) {
        return;
    }

    if (EDU_DMA_DIR(edu->dma.cmd) == EDU_DMA_FROM_PCI) {
        /* host → guest DMA */
        uint64_t dst = edu->dma.dst;
        pci_dma_write(&edu->pdev, dst, edu->dma_buf + ..., edu->dma.cnt);
    } else {
        /* guest → host DMA */
        uint64_t src = edu->dma.src;
        pci_dma_read(&edu->pdev, src, edu->dma_buf + ..., edu->dma.cnt);
    }

    edu->dma.cmd &= ~EDU_DMA_RUN;
    if (edu->dma.cmd & EDU_DMA_IRQ) {
        raise_irq = true;
    }
    if (raise_irq) {
        edu_raise_irq(edu, EDU_DMA_IRQ);
    }
}
```

這個 timer callback 在 QEMU 的 main loop 執行，不是在 vCPU thread。它用的 `edu->dma.src/dst/cnt` 在 `edu_mmio_write` 已被 guest 寫入。如果 device 在 timer fire 之前被 hot-unplug 銷毀，`opaque` 指向已釋放的記憶體，就是 UAF（Ch 19 的場景）。

---

## PIO 路徑：對比 MMIO

PIO（Port-Mapped I/O）的 dispatch 架構完全對稱，差別只在 `AddressSpace`：

```c
/* target/i386/kvm/kvm.c */
static int kvm_handle_io(uint16_t port, MemTxAttrs attrs, void *data,
                         int direction, int size, uint32_t count)
{
    int i;
    uint8_t *ptr = data;

    for (i = 0; i < count; i++) {
        address_space_rw(&address_space_io,   /* ← system_io，不是 system_memory */
                         port, attrs,
                         ptr, size,
                         direction == KVM_EXIT_IO_OUT);
        ptr += size;
    }
    return 0;
}
```

`address_space_io` 是全域的 `AddressSpace`，對應到 x86 的 64K port 空間（0x0000–0xFFFF）。`port` 是 16-bit，所以這個 `AddressSpace` 底下的 `FlatView` 最大就 64KB。

進到 `address_space_rw` 之後，路徑完全相同：`flatview_translate` → `MemoryRegionSection` → `memory_region_dispatch_read/write` → `ops->read/write`。

---

## 對比與取捨

| 面向 | MMIO | PIO（Port I/O） |
|------|------|-----------------|
| 觸發方式 | load/store 指令 + EPT miss | IN/OUT 指令 |
| KVM exit 類型 | `KVM_EXIT_MMIO` | `KVM_EXIT_IO` |
| AddressSpace | `address_space_memory`（system_memory） | `address_space_io`（system_io） |
| 地址空間大小 | 最大 64-bit 物理地址空間 | 64KB（x86 port 0–0xFFFF） |
| 地址欄位 | `kvm_run.mmio.phys_addr`（64-bit GPA） | `kvm_run.io.port`（16-bit） |
| 存取寬度 | 1/2/4/8 bytes | 1/2/4 bytes |
| 現代硬體趨勢 | 主流（PCIe device 均使用 BAR MMIO） | 傳統（ISA device、legacy PC 外設） |
| MemoryRegionOps | 完全相同介面 | 完全相同介面 |
| 攻擊面寬度 | 更大（GPA 全空間） | 較小（16-bit port） |
| 常見 device | PCIe NIC、GPU、virtio | 8259 PIC、UART 8250、PS/2 |

---

## 踩雷集錦

**1. valid 通過但 impl 沒對齊，以為 callback 收到的 size 是 guest 要求的 size**

`valid.min=1, valid.max=4, impl.min=4, impl.max=4`，guest 做 1-byte 寫入。QEMU 先通過 valid 檢查，然後做 read-modify-write 合成 4-byte 再呼叫 callback。callback 看到的 `size` 是 4，不是 1。如果 callback 自己還另外用 `size` 做分支，行為不如預期。

**2. offset 沒有 bound check，以為 valid.max 擋住了大 offset**

`valid.max_access_size` 限制的是單次存取「寬度」，不是「offset 範圍」。offset 的範圍由 `memory_region_init_io` 傳入的 `size` 決定（即 MemoryRegion 大小），超出 MemoryRegion 範圍的 GPA 根本不會被 translate 到這個 mr。但在 callback 內部，如果用 offset 去索引陣列，陣列大小和 MemoryRegion 大小必須一致，否則 OOB。

**3. opaque cast 之後直接信任欄位，沒想過 UAF**

`(EduState *)opaque` 看起來無害，但 opaque 是初始化時傳入的指標。device hot-unplug 流程如果沒有正確取消所有 pending callback 和 timer，舊的 opaque 指向已 free 的記憶體。

**4. endianness 在 callback 之前已轉換，不需要自己 bswap，結果轉了兩次**

`adjust_endianness()` 在呼叫 callback 前已把 data 轉成主機位元組序。callback 裡再自己 bswap 就轉了兩次，在 BE 主機上跑 LE device 會出現靜默的資料錯誤。

**5. 把 `hwaddr addr` 誤認為 GPA，實際上是相對 MemoryRegion 起點的 offset**

callback 收到的 `addr`（hwaddr）是 GPA 相對該 MemoryRegion 基址的 offset，不是絕對 GPA。要拿到絕對 GPA，需要加回 BAR base address，但通常沒有必要，直接用 offset 當暫存器索引就對了。

---

## 進階：再往深一層

### TCG 路徑（軟體模擬）

沒有 KVM 的時候，QEMU 純軟體翻譯執行。Guest MMIO 存取不走 `KVM_EXIT_MMIO`，而是 TCG 翻譯出來的 helper：

```
tcg_gen_qemu_st_tl()
→ gen_helper_st_i32() [翻譯期生成]
→ helper_le_stl_mmu() [執行期]
→ cpu_stl_data_ra()
→ address_space_rw()    ← 從這裡開始路徑相同
```

TCG 路徑的 `address_space_rw` 和 KVM 路徑共用，dispatch 機制完全一致。差別在 TCG 有 TLB cache，第一次 miss 才走完整 translate，之後 hot path 更快。

### IOMMU 介入

當 VM 啟用 IOMMU（如 Intel VT-d 的 IR/DMAR），DMA 的 GPA 在到達 device 之前還要過 IOMMU 的 IOTLB 查詢。QEMU 的 `PCIDevice` 有 `IOMMUMemoryRegion`，DMA read/write 會先呼叫 IOMMU translate，把 GPA 轉成 HPA 再做 `pci_dma_read/write`。IOMMU bypass 是另一類 escape 路徑（CVE-2019-14835 及相關漏洞）。

### virtio 的 dispatch

virtio device 不直接用 MMIO read/write 傳資料，而是透過 virtqueue descriptor ring。guest 寫 kick register（一次 MMIO write），QEMU 收到後去掃 vring descriptor，把 buffer 地址（GPA）一個個轉成 HVA 後操作。這條路徑的 attack surface 在 descriptor 的 `addr/len` 欄位和 `flags`（indirect, next chain）的解析邏輯，不在 MemoryRegionOps 的 size/offset 本身。

---

## 動手練習

**練習 A：手動追蹤 edu MMIO write**

在 QEMU source 裡，用 `gdb` attach 到 QEMU process，在 `edu_mmio_write` 設 breakpoint。用 guest 執行：

```c
/* guest 程式，寫到 edu BAR0 + 0x04 */
volatile uint32_t *edu_base = (uint32_t *)0xFEB00000; /* 依實際 BAR 調整 */
edu_base[1] = 0x80;   /* offset 0x04 = 1*4 */
```

觀察 backtrace，對照本章的路徑圖，確認每一層都出現。

**練習 B：加一個 offset 為 0x10 的假暫存器**

修改 `edu_mmio_write` 的 switch，加入：

```c
case 0x10:
    edu->my_reg = val;
    break;
```

並在 `EduState` struct 加 `uint32_t my_reg`。重新編譯，用 guest 寫 offset 0x10，用 gdb 確認 `edu->my_reg` 被正確設定。

**練習 C：觀察 valid 攔截**

把 `edu_mmio_ops.valid.min_access_size` 改成 `4`（已經是 4），再從 guest 用 1-byte 存取 edu BAR。用 strace 或 gdb 確認 `memory_region_access_valid` 回傳 false，callback 沒被呼叫，guest 收到 bus error（SIGBUS 或 general protection fault）。

**練習 D：追蹤 DMA timer 的 fire**

在 `edu_dma_timer` 設 breakpoint。從 guest 填好 DMA src/dst/cnt，然後寫 `EDU_DMA_CMD_REG | EDU_DMA_RUN`。觀察 breakpoint 何時觸發，注意它在哪個 thread（應在 QEMU main loop，不是 vCPU thread）。

---

## 本章重點整理

- `KVM_EXIT_MMIO` 帶著 `phys_addr`（GPA）、`data`、`len`、`is_write`，QEMU 以此呼叫 `address_space_rw`。
- `flatview_translate(gpa)` 找到對應的 `MemoryRegion`，計算出相對 offset。
- `memory_region_dispatch_write` 先查 `valid.min/max`（違反 → bus error），再處理 `impl.min/max` 的拆分/合併，最後呼叫 `ops->write`。
- `ops->write(opaque, offset, val, size)` 裡，`offset` 是 guest 完全控制的暫存器索引，沒有 bound check 就是 OOB 根源。
- PIO 路徑和 MMIO 路徑在 `address_space_rw` 之後完全對稱，差別只是 `AddressSpace`。
- `valid` 控制 guest 能做什麼，`impl` 控制 callback 能處理什麼，兩者獨立設定。
- `opaque` 是連結初始化與 callback 的唯一鏈條，UAF 從這裡發生。
- Timer/BH 是 deferred 執行，它們的 `opaque` 與 mmio callback 相同，hot-unplug 沒有正確 cancel 就 UAF。

---

## 自我檢核

1. `KVM_EXIT_MMIO` 的 `phys_addr` 和 callback 收到的 `addr`（hwaddr）是同一個值嗎？不是的話差在哪裡？
2. 如果 device 設定 `valid.min=1, valid.max=4, impl.min=4, impl.max=4`，guest 做 2-byte write，callback 被呼叫幾次？收到的 `size` 是多少？
3. `memory_region_access_valid` 和 `memory_access_is_direct` 分別在 dispatch 路徑的哪個位置？作用各是什麼？
4. edu.c 的 `edu_dma_timer` 在哪個 thread 執行？為什麼這個設計容易導致 UAF？
5. virtio device 的 attack surface 在哪裡？為什麼和普通 MMIO device 不同？

---

## 延伸閱讀

1. **hw/misc/edu.c**（QEMU 9.0 原始碼）— 本章主要範例，建議全文讀一遍，約 400 行，是最精簡的 PCI device 完整實作。

2. **QEMU Memory API 文件**（`docs/devel/memory.rst`）— `MemoryRegion`、`AddressSpace`、`MemoryRegionOps` 的官方設計說明，`valid` vs `impl` 在這裡有官方說明。

3. **LWN: "How QEMU handles memory"（Paolo Bonzini, 2014）** — 雖然年份舊，但 `FlatView`/`MemoryRegion` 的設計動機說明清楚，讀 source 之前看這篇會省很多力氣。

4. **QEMU internals blog by Airbus Security Lab** — "QEMU internals: vCPU IO path" 系列，用圖表呈現 KVM exit 到 device callback 的完整控制流，和本章路徑圖互補。

5. **CVE-2020-14364（QEMU USB OHCI OOB write）** — 一個真實的 `offset` 未做 bound check 導致 OOB write 的案例，patch diff 只有幾行，但路徑和本章完全一致：`mmio_write` → `offset` 索引陣列 → 越界。

---

→ [Ch 12 — 自己寫一個 PCI Device：從零實作 custom device](./12-custom-pci-device.md)
