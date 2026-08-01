# Ch 12 — 寫一個自訂 PCI device：BAR、MMIO、config space

> **目標**：從零實作一個故意有洞的 QEMU PCI device（vuln-pci），掌握 MMIO region、BAR 註冊、config space 與 QOM 繼承，讓後面幾章有真實攻擊標的。

> **環境**：QEMU 9.0 / x86-64 / Linux host（Ubuntu 22.04/24.04）

---

## 為什麼需要這個？

Ch 9-11 拆解了 QEMU 的物件模型（QOM）、記憶體子系統、以及 MemoryRegion 怎麼把 guest 的 GPA（Guest Physical Address）範圍攔下來分派給 device handler。理論到位了，但沒有一個真實 device，後面 Part 2b/Part 3 的攻擊就只是在空中打拳。

edu.c（`hw/misc/edu.c`）是 QEMU 官方提供的教學 device，架構乾淨、有 DMA、有中斷，是最好的參考。我們要照它的骨架寫一個「故意有漏洞」的版本——vuln-pci。漏洞不是意外，是精心設計的攻擊標的，Ch 16 的 MMIO OOB 和 Ch 13-15 的 DMA 攻擊都會打這裡。

---

## 先建立直覺

```
QEMU 程序（host userspace）
│
├── QOM（QEMU Object Model）物件樹
│   └── TYPE_PCI_DEVICE
│       └── TYPE_VULN_PCI          ← 我們定義的 TypeInfo
│           ├── VulnState（struct）
│           │   ├── PCIDevice pdev  ← 繼承基底（必須是第一個欄位）
│           │   ├── MemoryRegion mmio
│           │   ├── char buf[0x100]  ← 將被 OOB 存取
│           │   └── uint32_t status
│           └── vuln_mmio_ops（.read/.write）
│
├── PCI config space（256 bytes，存在 pdev.config[]）
│   ├── [0x00-0x01] vendor_id  = 0x1234
│   ├── [0x02-0x03] device_id  = 0x5678
│   ├── [0x10-0x13] BAR0       ← BIOS 初始化後寫入 GPA 基底
│   └── ...
│
└── AddressSpace（guest_address_space）
    └── MemoryRegion 樹
        └── BAR0 region（GPA = BIOS 分配，size = 0x1000）
            ├── offset 0x00  → status 暫存器
            ├── offset 0x04  → buf 讀（無 bound check）
            └── offset 0x10  → buf 寫（無 bound check）

Guest OS 看到的樣子：
  lspci → 1234:5678
  /sys/bus/pci/devices/0000:00:XX.0/resource0 → BAR0 的 GPA
  mmap(resource0) → 直接讀寫 MMIO
```

BAR（Base Address Register）的意義：guest BIOS 啟動時掃描 PCI 匯流排，偵測每個 device 需要多大的 MMIO 視窗，然後分配一段 GPA 範圍並把基底地址寫回 BAR0。從這一刻起，guest 對那段 GPA 的讀寫全部進 QEMU 的 `vuln_mmio_read`/`vuln_mmio_write`。

---

## PCI device 結構與 QOM 繼承

### TypeInfo、PCIDeviceClass 與 realize

QEMU 用 QOM（QEMU Object Model）管理所有 device 的類型系統。新增一個 PCI device 需要三個部分：

```
TypeInfo（類型描述）
  → 告訴 QOM：這個類型叫什麼、繼承誰、class/instance 各多大
  → .class_init = vuln_class_init（填 PCIDeviceClass 欄位）
  → .instance_size = sizeof(VulnState)

PCIDeviceClass（class-level 資料）
  → .realize = vuln_pci_realize（每次實例化時呼叫）
  → .vendor_id / .device_id / .class_id（填進 config space）
  → .revision / .subsystem_vendor_id / .subsystem_id

realize 函式（instance-level 初始化）
  → 建立 MemoryRegion（MMIO region）
  → pci_register_bar()：把 MemoryRegion 掛到 BAR 槽位
  → 選用：pci_msi_init()（我們先略過）
```

`realize` 和一般 OOP 的建構子不同：QOM 先呼叫 `instance_init`（分配結構、設預設值），之後 machine init 串接 bus 時才呼叫 `realize`——到這個時機 PCIBus 已存在，所以 `pci_register_bar` 可以安全呼叫。

### PCIDevice 繼承規則

```c
typedef struct VulnState {
    PCIDevice pdev;        /* 必須是第一個欄位，C 語言繼承慣例 */
    MemoryRegion mmio;
    char buf[0x100];
    uint32_t status;
} VulnState;
```

`PCIDevice`（`include/hw/pci/pci.h`）內部有：
- `uint8_t config[PCI_CONFIG_SPACE_SIZE]`：256 bytes config space，vendor/device ID、BAR、command register 都在裡面。
- `uint8_t cmask[]` / `wmask[]`：哪些 byte 可寫、哪些唯讀的遮罩。

`pdev` 放第一個欄位讓 `OBJECT_CHECK(VulnState, obj, TYPE_VULN_PCI)` 可以安全地把 `PCIDevice*` 強轉成 `VulnState*`，這是整個 QEMU device 框架的基本假設。

---

## 完整 vuln device 程式碼

> **未實測，理論預期**。程式碼結構對照 QEMU 9.0 `hw/misc/edu.c`，語法應正確但未在 Ubuntu host 實際編譯驗證。驗證步驟見「編譯與啟動」一節。

```c
/*
 * vuln-pci — 故意有漏洞的教學用 PCI device
 * 對照 QEMU 9.0 hw/misc/edu.c 結構
 *
 * 已知 bug（故意設計，後面章節的攻擊標的）：
 *   1. buf 讀/寫無 bound check → MMIO OOB（Ch 16）
 *   2. offset 直接當陣列 index → 任意 OOB read/write relative to VulnState
 *   3. DMA 長度無 check → DMA OOB（Ch 13-15）
 */

#include "qemu/osdep.h"
#include "hw/pci/pci.h"
#include "hw/pci/pci_device.h"
#include "hw/qdev-properties.h"
#include "qemu/log.h"
#include "qom/object.h"

/* ── 型別定義 ── */

#define TYPE_VULN_PCI "vuln-pci"
OBJECT_DECLARE_SIMPLE_TYPE(VulnState, VULN_PCI)

/*
 * VulnState：繼承 PCIDevice
 * pdev 必須是第一個欄位（C 繼承慣例）
 *
 * 記憶體佈局（重要！OOB 攻擊會利用這個）：
 *   [pdev ... 248 bytes ...]
 *   [mmio ... MemoryRegion ...]
 *   [buf  0x100 bytes      ]
 *   [status 4 bytes        ]
 */
struct VulnState {
    PCIDevice pdev;

    MemoryRegion mmio;   /* BAR0 的 MMIO region */

    char     buf[0x100]; /* BUG: 讀寫都沒有 bound check */
    uint32_t status;     /* offset 0x00 暫存器；寫入觸發 DMA */
};

/* ── MMIO 暫存器 offset 定義 ── */
#define VULN_REG_STATUS     0x00   /* R/W：狀態暫存器，寫觸發 DMA */
#define VULN_REG_BUF_READ   0x04   /* R：buf 讀取基底（addr = offset - 0x04） */
#define VULN_REG_BUF_WRITE  0x10   /* W：buf 寫入基底（addr = offset - 0x10） */

/* ── MMIO read handler ── */

static uint64_t vuln_mmio_read(void *opaque, hwaddr addr, unsigned size)
{
    VulnState *s = opaque;

    if (addr == VULN_REG_STATUS) {
        return s->status;
    }

    if (addr >= VULN_REG_BUF_READ) {
        /*
         * BUG #1：buf 讀取沒有 bound check。
         * guest 控制 addr（hwaddr 即 GPA offset 進 BAR0），
         * 合法範圍應該是 [0x04, 0x04 + 0xff]，
         * 但 BAR0 size = 0x1000，所以 addr 最大可到 0xfff。
         * idx 最大 = 0xfff - 0x04 = 0xffb，遠超 buf[0x100]。
         * → 任意讀 VulnState 結構體後方的 host 記憶體（相對 OOB）。
         */
        hwaddr idx = addr - VULN_REG_BUF_READ;
        /* 這裡故意不寫：if (idx >= sizeof(s->buf)) return -1; */
        return s->buf[idx];   /* OOB read */
    }

    qemu_log_mask(LOG_GUEST_ERROR,
                  "vuln-pci: bad read at offset 0x%" HWADDR_PRIx "\n", addr);
    return 0;
}

/* ── MMIO write handler ── */

static void vuln_mmio_write(void *opaque, hwaddr addr, uint64_t val,
                            unsigned size)
{
    VulnState *s = opaque;

    if (addr == VULN_REG_STATUS) {
        /*
         * 寫 status 暫存器：
         * 低 16 bit = DMA 長度（BUG #3：無上限 check，Ch 13-15 打這個）
         * 高 16 bit = 控制位元（目前只有 bit 16 = trigger）
         */
        s->status = (uint32_t)val;
        if (val & 0x00010000) {
            /* TODO Ch 13：vuln_dma_execute(s); */
            qemu_log("vuln-pci: DMA triggered, len=%u\n",
                     (uint32_t)(val & 0xffff));
        }
        return;
    }

    if (addr >= VULN_REG_BUF_WRITE) {
        /*
         * BUG #2：buf 寫入沒有 bound check。
         * 同 read 邏輯，idx 可到 0xfef，遠超 buf[0x100]。
         * → 任意寫 VulnState 結構體後方的 host 記憶體（相對 OOB write）。
         * → 可覆蓋 MemoryRegion.ops 指標或其他 QEMU 內部結構（Ch 16）。
         */
        hwaddr idx = addr - VULN_REG_BUF_WRITE;
        /* 這裡故意不寫：if (idx >= sizeof(s->buf)) return; */
        s->buf[idx] = (char)val;   /* OOB write */
        return;
    }

    qemu_log_mask(LOG_GUEST_ERROR,
                  "vuln-pci: bad write at offset 0x%" HWADDR_PRIx
                  " val=0x%" PRIx64 "\n", addr, val);
}

/* ── MemoryRegionOps ── */

static const MemoryRegionOps vuln_mmio_ops = {
    .read  = vuln_mmio_read,
    .write = vuln_mmio_write,
    /* endianness 必填；edu.c 用 DEVICE_LITTLE_ENDIAN */
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 1,
        .max_access_size = 4,
    },
    .impl = {
        .min_access_size = 1,
        .max_access_size = 4,
    },
};

/* ── realize：每次 -device vuln-pci 時執行 ── */

static void vuln_pci_realize(PCIDevice *pdev, Error **errp)
{
    VulnState *s = VULN_PCI(pdev);

    /*
     * memory_region_init_io：建立 MMIO MemoryRegion。
     * 參數：region指標, owner物件, ops, opaque(傳給handler), 名稱, size
     * size = 0x1000（4KB）；比 buf 大得多，OOB 空間充裕。
     */
    memory_region_init_io(&s->mmio, OBJECT(s), &vuln_mmio_ops, s,
                          "vuln-pci-mmio", 0x1000);

    /*
     * pci_register_bar：把 MMIO region 掛到 BAR0（槽位 0）。
     * PCI_BASE_ADDRESS_SPACE_MEMORY = MMIO 型態。
     * guest BIOS 掃描 PCI bus 時會把 GPA 基底寫入 BAR0，
     * 之後 guest 對那段 GPA 的存取全轉進 vuln_mmio_ops。
     */
    pci_register_bar(pdev, 0, PCI_BASE_ADDRESS_SPACE_MEMORY, &s->mmio);

    /* 初始化 buf 為已知值，方便除錯 */
    memset(s->buf, 0x41, sizeof(s->buf));
    s->status = 0;

    /*
     * INTx 中斷：edu.c 同時支援 INTx 和 MSI。
     * 我們先只用 INTx（pci_set_irq）；MSI 見「中斷比較」一節。
     * 不需要額外初始化，PCIDevice 框架預設開 INTx。
     */
}

/* ── instance_init：QOM 物件分配後立即呼叫（比 realize 早）── */

static void vuln_instance_init(Object *obj)
{
    /* 這裡只做不需要 bus 的初始化，目前留空 */
    (void)obj;
}

/* ── class_init：填 PCIDeviceClass ── */

static void vuln_class_init(ObjectClass *klass, void *data)
{
    DeviceClass    *dc  = DEVICE_CLASS(klass);
    PCIDeviceClass *k   = PCI_DEVICE_CLASS(klass);

    k->realize    = vuln_pci_realize;
    k->vendor_id  = 0x1234;   /* 自訂 vendor（非官方分配） */
    k->device_id  = 0x5678;   /* 自訂 device ID */
    k->revision   = 0x00;
    k->class_id   = PCI_CLASS_OTHERS;   /* 0xff00：雜項 */

    /*
     * dc->desc 在 -device help 顯示。
     * SET_MACHINE_COMPAT 和 hotpluggable 在 edu.c 也有設，
     * 我們從簡，只設 desc。
     */
    dc->desc = "vuln-pci: intentionally vulnerable PCI device";
}

/* ── TypeInfo：向 QOM 登記這個類型 ── */

static const TypeInfo vuln_info = {
    .name          = TYPE_VULN_PCI,
    .parent        = TYPE_PCI_DEVICE,
    .instance_size = sizeof(VulnState),
    .instance_init = vuln_instance_init,
    .class_init    = vuln_class_init,
};

/* ── type_init：動態連結器載入 .so 或 binary 啟動時執行 ── */

static void vuln_register_types(void)
{
    type_register_static(&vuln_info);
}

type_init(vuln_register_types)
```

### 程式碼逐段解讀

**`OBJECT_DECLARE_SIMPLE_TYPE`**：展開後等效：

```c
typedef struct VulnState VulnState;
#define VULN_PCI(obj) OBJECT_CHECK(VulnState, (obj), TYPE_VULN_PCI)
```

每次要從 `void *opaque` 或 `PCIDevice *pdev` 拿回 `VulnState*` 就用這個 macro，在 debug build 會做型別斷言。

**`memory_region_init_io`** vs **`memory_region_init_ram`**：前者是 MMIO（讀寫進 handler），後者是一塊 host RAM 直接映射給 guest。我們的 device 走 MMIO，每次 guest 存取都進 `vuln_mmio_read`/`vuln_mmio_write`。

**`pci_register_bar` 的 size 參數必須是 2 的冪**：PCI spec 規定 BAR 的大小必須 power-of-2 且至少 16 bytes。`0x1000`（4KB）符合要求。BIOS 掃描時寫全 1 到 BAR0，讀回來後取補數+1就得到 size，這是 PCI 探測協定。

**OOB 漏洞的關鍵數字**：

```
BAR0 size           = 0x1000
buf[]  size         = 0x100
buf read  range     = [offset 0x04, offset 0xfff]  → idx 最大 0xffb
buf write range     = [offset 0x10, offset 0xfff]  → idx 最大 0xfef
合法 buf index      = [0x00, 0xff]
OOB 超出量          = 最多 ~3.8 KB → 足以覆蓋 MemoryRegion.ops 指標
```

---

## 底層機制：PCI config space 與 BAR 初始化

### Config space（256 bytes）佈局

```
Offset  Size  欄位               說明
0x00    2     Vendor ID          0x1234（class_init 設定）
0x02    2     Device ID          0x5678
0x04    2     Command Register   bit 2 = Bus Master（DMA 用），初始 0
0x06    2     Status Register
0x08    1     Revision ID        0x00
0x09    3     Class Code         0xff0000（PCI_CLASS_OTHERS）
0x0c    1     Cache Line Size
0x0e    1     Header Type        0x00（一般 device）
0x10    4     BAR0               BIOS 寫入 GPA 基底地址（初始 0）
0x14    4     BAR1               未用（0）
...
0x3c    1     Interrupt Line     BIOS 寫入 IRQ 號
0x3d    1     Interrupt Pin      0x01（INTA#）
```

`QEMU pci_register_bar` 呼叫後，`pdev.config[0x10..0x13]` 還是 0；等 guest BIOS（SeaBIOS 或 OVMF）掃 PCI bus 時才寫入實際的 GPA 基底。這個過程透明——guest 驅動直接用 `pci_resource_start(pdev, 0)` 拿 BAR0 基底，不需要知道 BIOS 何時寫的。

### Bus Master bit（Command Register bit 2）

DMA（Direct Memory Access）需要 device 主動讀寫 guest 記憶體。PCI 規定這個能力受 Command Register 的 Bus Master bit 保護：guest OS 必須明確設這個 bit，device 才能發起 bus transaction。

我們的 vuln device 在 Ch 13 加 DMA 時，guest exploit 要先：

```c
/* guest kernel module 或 iopl 存取 */
uint16_t cmd;
pci_read_config_word(pdev, PCI_COMMAND, &cmd);
pci_write_config_word(pdev, PCI_COMMAND, cmd | PCI_COMMAND_MASTER);
```

這一步不可缺，否則 QEMU 側呼叫 `pci_dma_read` 會靜默失敗。

---

## 編譯與啟動步驟

> **未實測，理論預期**。以下步驟在 Ubuntu 22.04/24.04 + QEMU 9.0 source tree 下應該正確，但我們沒有在 Windows host 驗證。若有錯誤請依編譯輸出診斷。

### 1. 放入 source tree

```bash
# 假設 QEMU source 在 ~/qemu-9.0
cp vuln.c ~/qemu-9.0/hw/misc/vuln.c
```

### 2. 修改 hw/misc/meson.build

找到 edu.c 的加入方式，照辦：

```python
# hw/misc/meson.build（片段）
# 找到類似：
softmmu_ss.add(when: 'CONFIG_EDU', if_true: files('edu.c'))
# 在下面加：
softmmu_ss.add(when: 'CONFIG_VULN_PCI', if_true: files('vuln.c'))
```

### 3. 修改 hw/misc/Kconfig

```
# hw/misc/Kconfig（片段）
config EDU
    bool
    default y if TEST_DEVICES
    depends on PCI && MSI_NONBROKEN

# 在下面加：
config VULN_PCI
    bool
    default y if TEST_DEVICES
    depends on PCI
```

`TEST_DEVICES` 是 QEMU 的 softmmu 預設啟用的測試 device 集合，edu.c 也在裡面。掛在這裡讓 `configure` 不需要額外旗標就自動包含我們的 device。

### 4. 編譯

```bash
cd ~/qemu-9.0
mkdir -p build && cd build

# configure：只編 x86_64，開 KVM，debug 資訊保留
../configure \
    --target-list=x86_64-softmmu \
    --enable-kvm \
    --enable-debug \
    --prefix=/usr/local/qemu-vuln

make -j$(nproc)

# 確認 vuln-pci 有進去
./qemu-system-x86_64 -device help 2>&1 | grep vuln
# 預期輸出：vuln-pci             vuln-pci: intentionally vulnerable PCI device
```

### 5. 啟動 QEMU 帶 vuln-pci

```bash
./qemu-system-x86_64 \
    -enable-kvm \
    -m 2G \
    -cpu host \
    -drive file=ubuntu.img,format=qcow2 \
    -device vuln-pci \
    -nographic \
    -serial mon:stdio
```

`-device vuln-pci` 告訴 QEMU 把我們的 device 掛到 PCIe root complex。QEMU 預設用第一個空閒的 PCI slot（通常 0000:00:04.0 或 05.0，視已有 device 而定）。

---

## Guest 端存取 PoC

> **未實測，理論預期**。步驟在標準 Ubuntu guest + Linux 5.15+ 核心下應正確。

### 找到 device

```bash
# guest 內執行
lspci -nn | grep 1234
# 預期：00:04.0 Unclassified device [ffff]: Device 1234:5678 (rev ff)

# 讀 config space（需要 root）
setpci -s 00:04.0 0.W   # Vendor ID → 1234
setpci -s 00:04.0 2.W   # Device ID → 5678
setpci -s 00:04.0 10.L  # BAR0（BIOS 寫入後的 GPA 基底）
```

### 讀 BAR0 位址

```bash
# sysfs 方式（不需要 setpci）
cat /sys/bus/pci/devices/0000:00:04.0/resource
# 第一行是 BAR0：start end flags
# 例：0x00000000fe800000 0x00000000fe800fff 0x0000000000040200
# → BAR0 GPA = 0xfe800000，大小 0x1000
```

### mmap BAR0 直接存取 MMIO

```c
/*
 * guest_poc.c — 最小 PoC：mmap BAR0，讀寫 vuln-pci MMIO
 * 編譯：gcc -O0 -o guest_poc guest_poc.c
 * 執行：sudo ./guest_poc
 */
#include <stdio.h>
#include <stdlib.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <stdint.h>
#include <unistd.h>

#define BAR0_RESOURCE "/sys/bus/pci/devices/0000:00:04.0/resource0"
#define BAR0_SIZE     0x1000

int main(void)
{
    int fd = open(BAR0_RESOURCE, O_RDWR | O_SYNC);
    if (fd < 0) { perror("open"); return 1; }

    volatile uint32_t *mmio = mmap(NULL, BAR0_SIZE,
                                   PROT_READ | PROT_WRITE,
                                   MAP_SHARED, fd, 0);
    if (mmio == MAP_FAILED) { perror("mmap"); return 1; }

    /* 讀 status 暫存器（offset 0x00） */
    printf("status = 0x%08x\n", mmio[0]);   /* mmio[0] = offset 0x00 */

    /* 寫 buf[0]（offset 0x10，寫 'A'） */
    /* mmio 是 uint32_t*，offset 0x10 = index 4 */
    mmio[4] = 0x41414141;   /* 寫 buf[0..3] = "AAAA" */

    /* 讀回 buf[0]（offset 0x04） */
    uint32_t val = mmio[1]; /* mmio[1] = offset 0x04 */
    printf("buf[0..3] = 0x%08x\n", val);    /* 預期 0x41414141 */

    /* OOB 讀測試：offset 0x04 + 0x200 = buf[0x200]，超出 buf[0x100] */
    /* 這讀的是 VulnState 結構體後方的 host 記憶體 */
    uint8_t *mmio8 = (uint8_t *)mmio;
    uint8_t oob_byte = mmio8[0x04 + 0x200];
    printf("OOB read at buf[0x200] = 0x%02x\n", oob_byte);

    munmap((void *)mmio, BAR0_SIZE);
    close(fd);
    return 0;
}
```

`resource0` 是 Linux kernel 為每個 PCI device 的 BAR0 提供的 sysfs 檔案，`mmap` 後對應到的是 guest 的 GPA，kernel 的 PCI 層負責把這個 GPA 的讀寫轉成 MMIO transaction，QEMU 攔下來進我們的 handler。不需要寫 kernel module，直接 userspace 操作。

---

## 底層機制：config space 與 BAR 初始化流程

BIOS（SeaBIOS）啟動時依序：

```
1. 對每個 PCI slot 的 BAR0 寫入 0xffffffff
2. 讀回值：哪些 bit 是 0 就是固定的，不為 0 的 bit 代表 size
   （例如讀回 0xfffff000 → size = ~0xfffff000 + 1 = 0x1000）
3. 分配一段 GPA range（從 PCI MMIO 窗口分配），寫回 BAR0
4. 設 Command Register 的 Memory Space Enable bit（bit 1）

QEMU 側：
pci_register_bar() 設定 PCIDevice.io_regions[0]
每次 guest 寫 BAR0，QEMU 重新算 MemoryRegion 的 GPA 基底，
呼叫 memory_region_add_subregion_overlap() 更新映射
```

這整個流程在 `hw/pci/pci.c:pci_update_mappings()` 裡，每次 guest 寫 config space 就觸發一次。

---

## 中斷：INTx vs MSI vs MSI-X

| 機制 | 觸發方式 | QEMU API | 優缺點 |
|------|----------|----------|--------|
| INTx | 拉低 INTA# 實體線 | `pci_set_irq(pdev, 1)` / `pci_set_irq(pdev, 0)` | 最簡單；共享中斷，效能差；legacy BIOS 必用 |
| MSI | 寫 host memory 特定位址觸發中斷 | `pci_msi_init(pdev, 1, ...)` + `msi_notify(pdev, 0)` | 不共享；需 config space capability；現代 OS 首選 |
| MSI-X | MSI 的擴充版，支援最多 2048 向量 | `pci_msix_init(...)` + `msix_notify(pdev, vec)` | 多 queue 高效能；NVMe/10GbE 標配 |

edu.c 同時實作 INTx 和 MSI：`realize` 裡呼叫 `pci_msi_init`，讀/寫 handler 根據 `msi_enabled(pdev)` 決定用哪種。我們的 vuln device 只用 INTx，夠用了——Ch 13 DMA 完成通知用 `pci_set_irq`，guest 端用 `request_irq` 接。

### MMIO vs PIO

| 機制 | 存取方式 | guest 端 | QEMU 側 |
|------|----------|----------|---------|
| MMIO（Memory-Mapped I/O）| 普通記憶體讀寫指令 | `mmap(resource0)` | `MemoryRegionOps.read/.write` |
| PIO（Port-Mapped I/O）| `in`/`out` x86 指令 | `iopl(3)` + `inb(port)` | `memory_region_init_io` + `pci_register_bar(..., PCI_BASE_ADDRESS_SPACE_IO, ...)` |

現代 device 幾乎全用 MMIO：BAR 空間大、不用特殊指令、可 mmap。PIO 剩下 legacy 用途（IDE controller、8042 PS/2、實驗性 device）。我們的 vuln device 走 MMIO，exploit 最簡單。

---

## 踩雷集錦

**1. `pdev` 不是第一個欄位**

```c
/* 錯誤 */
struct VulnState {
    MemoryRegion mmio;  /* 放在 pdev 前 */
    PCIDevice pdev;
    ...
};
```

`VULN_PCI(obj)` 展開後是 `OBJECT_CHECK`，它假設 `PCIDevice` 就在結構起點。放錯位置 → type cast 取到錯誤記憶體 → 隨機崩潰，通常在 realize 就死。

**2. BAR size 不是 2 的冪**

```c
/* 錯誤 */
memory_region_init_io(&s->mmio, ..., 0x1234);   /* 非 2 的冪 */
```

QEMU 不會報錯，但 guest BIOS 探測時拿到的 size 會被截成 2 的冪，BAR 映射混亂。永遠用 `0x1000`、`0x4000` 這種。

**3. `realize` 裡呼叫 `memory_region_add_subregion`**

不要在 realize 裡手動呼叫 `memory_region_add_subregion`，讓 `pci_register_bar` 管就好。手動加會導致 region 被加兩次，BIOS 重新分配 BAR 時造成 double-map。

**4. 忘了 `.endianness`**

`MemoryRegionOps` 的 `.endianness` 是必填欄位（雖然 C 預設零初始化讓它變 `DEVICE_NATIVE_ENDIAN`）。QEMU 9.0 有 assertion 在 debug build 會 abort。明確寫 `DEVICE_LITTLE_ENDIAN`。

**5. `valid.max_access_size` 設太小**

如果 guest 用 64-bit store 存取 MMIO（`movq` 指令），但 `valid.max_access_size = 4`，QEMU 會把它拆成兩次 4-byte 呼叫——這不是錯，但會讓 OOB exploit 的偏移計算變複雜。統一設成 4，exploit 最好算。

---

## 進階：再往深一層

### PCIe Capability 鏈

PCI 的 config space 256 bytes 在 PCIe 擴充成 4096 bytes（Extended Config Space）。Extra capabilities（MSI-X、Power Management、AER）用 linked list 掛在 config space offset 0x34 指向的 capability chain 上。`pci_add_capability` 分配 capability block。

QEMU 把 config space 相關的 R/W 全包在 `hw/pci/pci.c:pci_host_config_read_common`，每次 guest 讀寫 CF8/CFC port（PCI 的 config port）都過這個函式。

### SR-IOV（Single Root I/O Virtualization）

SR-IOV 讓一個實體 PCI device 虛擬成多個 VF（Virtual Function），每個 VM 拿到一個 VF，直接存取 device，不過 QEMU 的 device emulation。QEMU 的 vfio-pci driver 支援這條路。效能最好，但 device passthrough 給 VM 就失去了 QEMU 的 MMIO handler 攔截能力，也就失去了我們的漏洞入口。

### IOMMU 與 DMA 安全

真實攻擊場景：guest 控制 DMA descriptor（Ch 13）→ 竄改 DMA 目標 GPA → 如果 host 沒開 IOMMU，device 可以讀寫 host 的任意實體記憶體（Host PA）。Intel VT-d（IOMMU）把 device 的 DMA 範圍限制在 IOVA（I/O Virtual Address）空間內，每個 VM 有獨立的 IOMMU domain。QEMU 的 IOMMU emulation（`intel-iommu` device）把 IOVA→GPA 的翻譯搬進 QEMU userspace，不需要真實 VT-d 硬體。我們的 DMA exploit（Ch 13-15）先不考慮 IOMMU，假設 host 沒開（預設情況）。

---

## 動手練習

> **未實測，理論預期**。需要 Linux host + QEMU source + Ubuntu/Debian guest image。

**練習 1**：把 `vuln.c` 加進 QEMU source tree，編譯成功後用 `-device help` 確認 `vuln-pci` 出現在清單。

**練習 2**：在 guest 內寫一個 C 程式，mmap BAR0，讀 status 暫存器，再寫 buf[0..3]，讀回確認符合預期。用 `strace` 確認系統呼叫（open + mmap）的行為。

**練習 3**：用 `setpci` 印出完整 64 bytes config space，對照本章的 offset 表找到 vendor ID、device ID、BAR0 值、command register。把 command register 的 Bus Master bit 設起來（`setpci -s 00:XX.0 4.W=07`），確認不崩潰。

**練習 4**：讀 edu.c 的 `edu_mmio_read`/`edu_mmio_write`，和我們的 `vuln_mmio_read`/`vuln_mmio_write` 對比，列出 edu.c 有但 vuln.c 故意省掉的邊界檢查。

**練習 5**（進階）：在 `vuln_mmio_write` 加 `printf` 印出每次 guest 存取的 offset 和值，再從 guest 端做 OOB write（offset = 0x10 + 0x200 = 0x210），確認 host 端 printf 顯示的 idx = 0x200。

---

## 本章重點整理

- PCI device 在 QEMU 用 QOM 繼承：`VulnState` 的第一個欄位是 `PCIDevice pdev`，`type_init` 向 QOM 登記 TypeInfo。
- `realize` 是實際初始化時機：建 MemoryRegion（`memory_region_init_io`）→ 掛 BAR0（`pci_register_bar`）。
- BAR 的 GPA 基底由 guest BIOS 分配，寫進 config space offset 0x10；guest OS 之後用 `resource0` sysfs 或 `pci_resource_start` 取得。
- vuln device 的三個故意 bug：buf OOB read、buf OOB write、DMA 長度無上限，後面章節分別攻打。
- Config space command register 的 Bus Master bit 控制 DMA 能力，guest exploit 必須先設它。
- INTx 最簡單，MSI/MSI-X 效能更好；我們先用 INTx。

---

## 自我檢核

1. `pci_register_bar` 的第二個參數（bar_num）和第三個參數（type）分別控制什麼？`PCI_BASE_ADDRESS_SPACE_MEMORY` 和 `PCI_BASE_ADDRESS_SPACE_IO` 的差異是什麼？
2. guest BIOS 如何探測 BAR0 的 size？描述寫全 1、讀回、取補數的流程。
3. 我們的 `vuln_mmio_read` 在 offset = 0x04 + 0x300 時讀到的是 `VulnState` 裡哪個欄位附近的記憶體？為什麼這對攻擊有用？
4. Bus Master bit 為什麼是 DMA exploit 的前提？如果 guest 沒設，QEMU 的 `pci_dma_read` 會怎麼樣？
5. `memory_region_init_io` 和 `memory_region_init_ram` 在語意上的差別是什麼？各自適合哪種 device 用途？

---

## 延伸閱讀

1. **`hw/misc/edu.c`**（QEMU 9.0 source）：官方教學 device，實作了 DMA、MSI/INTx、因式分解運算模擬，是本章所有程式碼的原型。直接讀 source 效果最好。
2. **QEMU Developer Documentation — "Writing a new device"**（`docs/devel/writing-device-models.rst`）：QEMU source tree 內的官方 device 開發指引，涵蓋 QOM 機制、MemoryRegion API、device properties、vmstate migration。
3. **PCI Local Bus Specification Rev 3.0**（PCI-SIG）：BAR 探測協定（§6.2.5）、config space 佈局（§6.1）、Bus Master 語意（§3.2）的原始規範。可從 PCI-SIG 官網取得（需會員；摘要版到處都有）。
4. **"QEMU Internals: How QEMU handles guest memory"**（Paolo Bonzini，2020 KVM Forum slides）：MemoryRegion 樹、AddressSpace、FlatView 的設計解說，讀完後 Ch 9-11 的內容會更清晰。
5. **`hw/pci/pci.c`**（QEMU source）：`pci_update_mappings`、`pci_register_bar`、`pci_host_config_read_common` 的實作，搭配本章讀效果最好。

---

→ [Ch 13 — DMA 與 guest 記憶體存取：pci_dma_read/write 與 DMA descriptor 攻擊面](./13-dma-guest-memory.md)
