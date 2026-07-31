# Ch 40 — PCI/PCIe 列舉：config space、BAR、MMIO

> **目標**：搞懂 PCI/PCIe 這種**能自我列舉（self-enumerating）**的匯流排——kernel 開機時怎麼掃描出所有裝置、讀出它們是誰、算出它們要多少資源、把實體位址分配給它們、再把 `pci_dev` 配對給 driver。學完你能讀懂一顆 PCIe 裝置的 config space、用 `lspci` 拆解 BAR 與 capability、用 `pci_ioremap_bar` 把裝置暫存器映射進來存取（MMIO）、寫一個最小 `pci_driver` 在 QEMU 裡被 probe，並知道為什麼現代裝置都用 MSI-X 而不是共享中斷線。伺服器/桌面上的網卡、GPU、NVMe 全是 PCIe，這章是它們驅動的共同底座。

## 為什麼需要這個？

Ch 39 我們處理的是 SoC 上那些**焊死在固定實體位址、無法被掃描出來**的裝置。UART 就是 `0x11002000` 那塊暫存器，硬體不會告訴你它在那裡——所以我們需要 device tree 把「這塊板子有哪些硬體、各在哪」寫成一份資料檔，開機時交給 kernel。kernel 是靠「讀一份別人寫好的清單」才知道裝置存在的。

PCI 走的是相反的路。它從一開始（1992 年，Intel 主導）就把「**裝置能自報家門**」設計進協定裡。你把一張 PCIe 網卡插進主機板的 slot，開機時 kernel（或更早的韌體）沿著匯流排掃過去，對每一個可能的位置問一句「有人在嗎？」，在的裝置會回答：「我在。我的 vendor 是 0x8086（Intel）、device 是 0x10d3（82574L 網卡）、我屬於 class 0x0200（乙太網路控制器）、我需要一塊 128 KB 的 MMIO 空間、我用 INTA 這條中斷線。」kernel 照著這份自報資訊，動態建出一個 `struct pci_dev`，分配實體位址範圍給它，然後拿它的 vendor/device ID 去比對已註冊的 driver——比對成功就 `probe`。

這就是**列舉（enumeration）**：無需任何人事先描述硬體拓撲，kernel 靠掃描匯流排就能自己發現整棵裝置樹、問出每個裝置的身份與資源需求。對比 Ch 39，差別是根本性的：

| | 可列舉匯流排（PCI/PCIe、USB） | 不可列舉匯流排（platform / SoC） |
|---|---|---|
| 裝置怎麼被發現 | kernel 掃描硬體，裝置自報 | 讀 device tree / ACPI（人寫的清單） |
| 位址誰決定 | 韌體/kernel 動態分配（看 BAR 需求） | 晶片設計時定死，寫在 DT 的 `reg` |
| 換一張新卡 | 插上開機即認得 | 要改 DT / board file |
| 身份從哪來 | config space 的 vendor/device ID | DT 的 `compatible` 字串 |
| 典型場景 | 伺服器/桌面：網卡、GPU、NVMe、音效 | 手機/嵌入式 SoC：UART、I2C、GPIO |

值得注意的是：match→probe 那套骨架（Ch 37）**完全一樣**——都是 device 掛上 bus、driver 註冊到 bus、bus 的 `match` 配對、成功就 `probe`。變的只有「device 這個物件從哪來」：Ch 39 是解析 DT 生出來的，這章是掃描硬體生出來的。你會看到 Linux 的 driver core 抽象在這裡再一次證明它的價值。

## 先建立直覺

先把 PCI 的拓撲畫清楚，這是理解後面所有東西的地圖。現代 PCIe 是一棵樹，根在 **root complex**（整合在 CPU/晶片組裡，是 CPU 通往 PCIe 世界的出口）：

```
                    ┌─────────── CPU / 記憶體 ───────────┐
                    │                                    │
                    │        Root Complex                │  ← PCIe 的根，CPU 的出口
                    └───────┬──────────────┬─────────────┘
                            │ bus 0        │
              ┌─────────────┴───┐      ┌───┴──────────────┐
        00:1c.0 (bridge)    00:02.0             ...
        PCI-to-PCI bridge   GPU (device)
              │ bus 3
      ┌───────┴────────┬─────────────────┐
   03:00.0          03:00.1           04:00.0
   NIC function 0   NIC function 1    NVMe SSD
   (雙埠網卡的兩個 function)
```

三個關鍵詞，合稱 **BDF（Bus/Device/Function）定址**，這就是 PCI 世界的「門牌號」：

- **Bus（匯流排）**：一條 PCI segment。root complex 底下是 bus 0，每經過一個 bridge 就多一段新 bus。
- **Device（裝置）**：一條 bus 上最多 32 個裝置槽位（0–31）。
- **Function（功能）**：一個裝置最多 8 個 function（0–7）。多 function 是硬體上一顆晶片對外呈現多個獨立邏輯裝置——雙埠網卡就是一顆晶片、兩個 function，各自有獨立的 config space 與驅動實例。

你在 `lspci` 看到的 `03:00.1` 就是 bus 0x03、device 0x00、function 0x1。每一個 (B, D, F) 組合對應一份獨立的 **config space**——那 256 位元組（PCIe 擴充到 4 KB）的自我描述資料，是這整章的核心。

**PCIe 不是舊 PCI**，這點務必分清。舊的 parallel PCI 是**共享匯流排**：一條並行匯流排掛好幾個裝置，同一時間只有一個能講話，頻寬大家分。PCIe（2003 起）改成**點對點的序列連結（serial point-to-point）+ switch 交換架構**：每個裝置有自己專屬的一條 link（由若干 lane 組成，×1/×4/×8/×16），彼此不搶頻寬，中間用 switch 像網路交換器一樣轉發封包（TLP，Transaction Layer Packet）。

但——**軟體模型幾乎沒變**。PCIe 刻意做到「電氣層翻天覆地、程式看到的還是 PCI」：config space 佈局相容、BDF 定址不變、BAR 一樣、原本的 PCI driver 大多不用改就能跑 PCIe 裝置。這是 PCIe 能快速取代 PCI 的關鍵設計決定，也是為什麼這章不需要分「PCI 章」和「PCIe 章」——軟體視角它們是同一套。PCIe 真正**新增**的是我們後面會講的 MSI/MSI-X、擴充 config space（>256 byte）、電源管理與 AER 這些 capability。

## config space：裝置的自我描述表

每個 function 有一塊 config space。前 64 位元組是**標準 header**，格式由 PCI spec 定死，所有裝置一致；後面是 capability list。這是 Type 0 header（一般裝置，非 bridge）的關鍵欄位佈局：

```
 offset  ┌────────────────┬────────────────┐
  0x00   │   Device ID    │   Vendor ID    │  ← driver match 的依據
  0x04   │     Status     │    Command     │  ← Command: 開/關 MMIO、I/O、bus master
  0x08   │        Class Code       │ Rev   │  ← class code: 這是網卡?GPU?NVMe?
  0x0C   │ BIST │ Hdr │ Lat │ CacheLine    │  ← Hdr type bit7: 是否多 function
  0x10   │            BAR0                 │  ┐
  0x14   │            BAR1                 │  │
  0x18   │            BAR2                 │  ├─ 6 個 BAR：宣告要多少 MMIO/IO 空間
  0x1C   │            BAR3                 │  │
  0x20   │            BAR4                 │  │
  0x24   │            BAR5                 │  ┘
  0x28   │       Cardbus CIS Pointer      │
  0x2C   │  Subsystem ID  │ Subsys Vendor │  ← 常用來細分同晶片的不同板子
  0x30   │       Expansion ROM Base       │
  0x34   │  reserved            │ Cap Ptr │  ← 指向 capability list 起點
  0x38   │           reserved             │
  0x3C   │ MaxLat│MinGnt│ IntPin │ IntLine│  ← IntLine: legacy INTx 中斷線
         └────────────────────────────────┘
  0x40+  capability list（MSI / MSI-X / PCIe / PM / ...，鏈狀串接）
  0x100+ PCIe 擴充 config space（AER、SR-IOV… 只 MMCONFIG 存取得到）
```

**怎麼讀 config space**——這是 PCI 與眾不同的地方，它不是普通的記憶體映射，而是有一套專屬存取機制。x86 上有新舊兩法：

- **舊法（CF8/CFC port I/O）**：x86 保留了兩個 I/O port，`0xCF8`（address port）和 `0xCFC`（data port）。你把「想讀哪個 BDF 的哪個 offset」寫進 `0xCF8`，再從 `0xCFC` 讀出那 4 個位元組。這是最原始的方式，源碼在 `arch/x86/pci/direct.c`（`pci_conf1_read`）。缺點：一次只能碰 256 byte（offset 只有 8 bit），碰不到 PCIe 的擴充區。

- **新法（MMCONFIG / ECAM）**：PCIe 引入 **Enhanced Configuration Access Mechanism**，把整個 config space 直接記憶體映射進實體位址空間。韌體（透過 ACPI 的 MCFG 表）告訴 kernel「所有裝置的 config space 映射在實體位址 base 起的一大塊」，位址編碼是 `base + (bus<<20 | dev<<15 | func<<12 | offset)`。這樣一個裝置佔 4 KB，剛好容納擴充 config space。x86 的 MMCONFIG 支援在 `arch/x86/pci/mmconfig_*.c`，通用存取包裝在 `drivers/pci/ecam.c`。**只有走 MMCONFIG 才讀得到 offset 0x100 以上**（AER、SR-IOV 這些擴充 capability 都在那）。

不管哪種底層機制，kernel 給 driver 的是統一 API：`pci_read_config_word(pdev, PCI_VENDOR_ID, &v)` 之類，宣告在 `include/linux/pci.h`，實作在 `drivers/pci/access.c`。你寫 driver 時不必管底下是 CF8 還是 MMCONFIG。

幾個欄位值得單獨點名，因為它們決定了後面的一切：

- **Vendor ID / Device ID**（offset 0x00）：這是 driver match 的鑰匙（接 Ch 37）。driver 在 `id_table` 裡列出它認得的 (vendor, device) 組合，bus 的 match 拿裝置 config space 讀到的 ID 去比對。vendor ID 由 PCI-SIG 統一分配（0x8086=Intel、0x10de=NVIDIA、0x1af4=Red Hat/virtio……），是全球唯一的。
- **Class Code**（offset 0x08 的高 24 bit）：三層分類（class/subclass/prog-if），例如 0x010802 = NVMe controller。用途是即使沒有專屬 driver，通用 driver 也能靠 class 認出「這是個 NVMe」而接手。
- **BAR0–BAR5**（offset 0x10–0x24）：下一節主角，裝置用它宣告要多少 MMIO/IO 空間。
- **Capability Pointer**（offset 0x34）：指向 capability list 的起點，MSI/MSI-X/PCIe/電源管理等進階功能都掛在這條鏈上。

## BAR 與資源配置：裝置暫存器怎麼進到 kernel 位址空間

**BAR（Base Address Register，基底位址暫存器）**是 PCI 自我列舉最精巧的一環。它同時解決兩個問題：裝置怎麼說「我要多少空間」，以及韌體/kernel 怎麼回答「那你的空間在實體位址的這裡」。

機制是這樣的（這是面試常考的細節）：一個 BAR 是 32 bit 暫存器，但它不是單純存位址。裝置出廠時把 BAR 的**低位元寫死成 0**——寫死的位數代表它要的空間大小。列舉時 kernel 玩一個技巧：往 BAR **全寫 1**，再讀回來，讀到的值裡低位那串 0 就告訴你大小。比如寫全 1、讀回 `0xFFFF0000`，代表低 16 bit 是不可寫的 0，這 BAR 要 64 KB（2^16）。演算法在 `drivers/pci/probe.c` 的 `__pci_read_base()`。

算出大小後，kernel（或開機前的韌體）從實體位址空間裡挑一段不衝突的區間，把起始位址**寫回 BAR** 的高位。從此這個 BAR 就「落地」到一個實體位址範圍。BAR 最低幾個 bit 是類型旗標：bit0 = 0 表示這是 memory space（MMIO）、= 1 表示 I/O space；memory BAR 的 bit1–2 表示是否 64 bit（兩個相鄰 BAR 合成一個 64 bit 位址，因為現代裝置的 MMIO 可能在 4 GB 以上）。

到這裡，裝置的暫存器有了實體位址。但 kernel 程式碼跑在**虛擬位址空間**（Ch 16），不能直接碰實體位址。最後一步是 driver 做的：把 BAR 的實體範圍 **ioremap** 成 kernel 虛擬位址：

```c
phys_addr_t start = pci_resource_start(pdev, 0);   // BAR0 的實體起始位址
resource_size_t len = pci_resource_len(pdev, 0);   // BAR0 的長度
void __iomem *regs = pci_ioremap_bar(pdev, 0);     // 映射成 kernel 虛擬位址

writel(0x1, regs + DEVICE_CTRL_REG);   // 寫裝置的控制暫存器（MMIO）
u32 status = readl(regs + DEVICE_STATUS_REG);   // 讀狀態暫存器
```

`pci_resource_start` / `pci_resource_len` 從 `pci_dev->resource[]` 陣列取出列舉時算好的 BAR 資訊；`pci_ioremap_bar`（`drivers/pci/pci.c`）是包好的 `ioremap`，把那段**實體 MMIO 位址**建一個 kernel 頁表映射，回傳一個 `__iomem` 指標。之後對 `regs + offset` 用 `readl`/`writel` 存取，實際上就是對裝置暫存器的讀寫——這就是 **MMIO（Memory-Mapped I/O）**：把裝置暫存器當記憶體來讀寫，CPU 的 load/store 指令直接打到裝置上。

> **為什麼一定要 `readl`/`writel` 而不是直接 `*ptr`**：MMIO 位址不是普通 RAM，編譯器和 CPU 的優化（重排、合併、快取）套在它上面會出災難性錯誤——你以為寫了兩次暫存器，編譯器合併成一次；你以為讀到新狀態，CPU 給你快取的舊值。`readl`/`writel` 內含 volatile 存取與必要的記憶體屏障（Ch 23、Ch 24），保證讀寫真的打到裝置、順序正確。`__iomem` 這個 annotation 就是提醒你（和 sparse 靜態檢查器）：這不是普通指標，別直接解參考。

`__iomem` 那個 `void __iomem *` 型別搭配 `readl`/`writel` 是 kernel 存取 MMIO 的鐵律，違反了輕則資料錯亂、重則硬體行為詭異。

## PCI 驅動框架：從 pci_driver 到 probe

有了 config space（身份）和 BAR（資源），現在看 driver 怎麼寫。骨架和 Ch 37/Ch 39 同構，只是換成 PCI 版的 API，核心結構是 `struct pci_driver`（`include/linux/pci.h`）：

```c
static const struct pci_device_id my_ids[] = {
    { PCI_DEVICE(0x1af4, 0x1000) },   // vendor 0x1af4 (Red Hat/virtio), device 0x1000
    { 0, }                            // 結尾哨兵
};
MODULE_DEVICE_TABLE(pci, my_ids);     // 讓 udev/modules.alias 能自動 modprobe

static int my_probe(struct pci_dev *pdev, const struct pci_device_id *id)
{
    int err;

    err = pci_enable_device(pdev);            // 1. 上電、啟用 config space 的 memory/IO 解碼
    if (err)
        return err;

    err = pci_request_regions(pdev, "my_drv"); // 2. 向 kernel 「認領」這個裝置的 BAR 區域
    if (err)                                    //    避免兩個 driver 撞同一塊 MMIO
        goto disable;

    my_regs = pci_ioremap_bar(pdev, 0);        // 3. 把 BAR0 映射進來（見上一節）
    if (!my_regs) { err = -ENOMEM; goto release; }

    pci_set_master(pdev);                      // 4. 設 bus master bit，開啟裝置發起 DMA 的能力

    /* ... 這裡分配中斷、初始化裝置 ... */
    return 0;

release:
    pci_release_regions(pdev);
disable:
    pci_disable_device(pdev);
    return err;
}

static struct pci_driver my_driver = {
    .name     = "my_drv",
    .id_table = my_ids,
    .probe    = my_probe,
    .remove   = my_remove,
};
module_pci_driver(my_driver);   // 展開成 module_init/exit 幫你註冊/註銷
```

逐步看幾個關鍵 API 在做什麼、為什麼需要：

- **`pci_register_driver`**（被 `module_pci_driver` 包起來）：把 driver 註冊到 PCI bus。註冊當下 kernel 會拿它的 `id_table` 去掃已列舉出的每個 `pci_dev`，match 到就 `probe`——所以你 `insmod` 後，若裝置已在，probe 立刻被呼叫。
- **`pci_enable_device`**（`drivers/pci/pci.c`）：真正把裝置「打開」。它會設 config space 的 Command 暫存器，啟用 memory space / I/O space 的位址解碼——沒 enable，裝置根本不理會打到它 BAR 的存取。也會處理電源（從 D3 喚回 D0）。
- **`pci_request_regions`**：向 kernel 的資源管理登記「這些 BAR 歸我用了」。這是互斥機制——若別的 driver 已認領同一塊，你會拿到 `-EBUSY`。這防止兩個 driver 同時 poke 同一個裝置的暫存器。
- **`pci_set_master`**：設 config space Command 暫存器的 **Bus Master Enable** bit。這一步授予裝置**主動發起 DMA**的權力——沒它，裝置只能被動被 CPU 讀寫，不能自己搬資料進出主記憶體。網卡收到封包要 DMA 進 RAM、NVMe 要 DMA 讀寫資料，全靠這個 bit。DMA 的完整故事是 Ch 41。

順序有講究：enable → request regions → ioremap → set master，是標準的資源獲取序，`remove` 時反序釋放。漏掉任何一步（尤其忘了 `pci_set_master` 導致 DMA 不動）是新手最常見的 bug。

## 中斷：從 legacy INTx 到 MSI/MSI-X

裝置要通知 CPU「我有事了」（封包來了、DMA 完成、錯誤發生），靠中斷。PCI 中斷有兩代機制，理解它們的差異是理解現代高效能裝置的關鍵。

**Legacy INTx（傳統中斷線）**：舊 PCI 有四條實體中斷線 INTA#–INTD#，是**電平觸發、可共享**的側頻訊號線。config space 的 Interrupt Pin（0x3D）說裝置用哪條、Interrupt Line（0x3C）記錄它接到中斷控制器（Ch 29）的哪個腳。問題一堆：

- **共享**：多個裝置擠同一條 INTx 線，中斷來了 kernel 不知道是誰，得逐一問「是你嗎？」（讀每個裝置的狀態暫存器），慢且醜。
- **一個裝置一條線**：多佇列裝置（多核網卡想每個 CPU 一條中斷）辦不到。
- **與 DMA 有 race**：INTx 是獨立於資料路徑的側頻線，可能中斷先到、DMA 寫入的資料還沒到記憶體（順序問題）。

**MSI / MSI-X（Message Signaled Interrupts，訊息式中斷）**：PCIe 的解法是**取消專門的中斷線**，改成「裝置要中斷時，就往一個特定的記憶體位址寫一個特定的值」——這個 memory write 走的是和資料一樣的 PCIe TLP 通道，root complex 認得這個特殊寫入，把它翻譯成一個中斷送給指定的 CPU。中斷變成「一次記憶體寫入」，帶來幾個決定性好處：

- **無共享、無需輪詢**：每個中斷源有自己的 (位址, 資料) 組合，root complex 一看就知道是誰、是哪個向量，直接對號入座呼叫對應 handler。
- **每裝置多向量**：MSI 最多 32 個向量，**MSI-X 最多 2048 個**。這是多佇列網卡（Ch 44）的命脈——每個接收佇列一個 MSI-X 向量，可以各自導到不同 CPU，收包負載真正並行化（接 Ch 15 的 SMP）。
- **與 DMA 同通道、自然排序**：中斷是走資料通道的一次寫入，排在 DMA 資料之後，中斷到達時資料保證已在記憶體——消除了 INTx 的 race。
- **可導向特定 CPU**：中斷的目標位址編碼了目標 CPU，配合 IRQ affinity 能把不同佇列的中斷釘在不同核，這是網路多佇列（RSS / Ch 44）與 NVMe 多佇列（Ch 36）擴展性的基礎。

driver 端統一用 `pci_alloc_irq_vectors`（`drivers/pci/msi/`）：

```c
int nvec = pci_alloc_irq_vectors(pdev, 1, 8,
                                 PCI_IRQ_MSIX | PCI_IRQ_MSI | PCI_IRQ_INTX);
// 想要 1~8 個向量，優先 MSI-X，退而 MSI，再退 legacy INTx
if (nvec < 0) return nvec;

for (int i = 0; i < nvec; i++) {
    int irq = pci_irq_vector(pdev, i);          // 取第 i 個向量的 Linux IRQ 號
    request_irq(irq, my_handler, 0, "my_drv", &my_queues[i]);
}
```

這個 API 把「MSI-X 拿不到就降級 MSI、再降級 INTx」的邏輯統一封裝，driver 不必自己分三種路徑寫。這是 v4.x 後推薦的現代寫法，取代了舊的 `pci_enable_msix` 那套。

> **為什麼 PCIe 幾乎清一色用 MSI-X**：現代裝置動輒幾十個佇列、要跑在幾十核的伺服器上，INTx 的「一條共享線」在架構上就撐不起來。MSI-X 讓「N 個佇列 × N 個 CPU」的並行中斷成為可能，這是十萬級 IOPS 的 NVMe（Ch 36）和百萬 PPS 的網卡（Ch 44）能吃滿多核的前提。`cat /proc/interrupts` 你會看到現代網卡列一整排 `xxx-TxRx-0`、`-1`、`-2`……每個對應一個 MSI-X 向量、綁一顆 CPU。

## 底層機制：一次完整列舉怎麼跑

把上面的零件串成一條開機時的執行流程。這是本章的靈魂——讀懂它，你腦中就有一張「kernel 從無到有發現整棵 PCI 樹」的動畫。主源碼在 `drivers/pci/probe.c`：

```
   開機 / hotplug
        │
        ▼
   pci_scan_root_bus / pci_host_probe        ← 從 root complex 建 root bus (bus 0)
        │
        ▼
   pci_scan_child_bus ──► 對 bus 上每個 device slot (0~31)：
        │                    pci_scan_slot()
        │                       └─ 對每個 function (0~7)：
        │                            pci_scan_single_device()
        │                               │
        │  ┌────────────────────────────┘
        │  ▼
        │  pci_bus_read_dev_vendor_id()   ← 讀 offset 0x00
        │        │
        │   讀回 0xFFFFFFFF ? ── 是 ──► 這個 (B,D,F) 沒裝置，跳過
        │        │ 否
        │        ▼
        │  建立 struct pci_dev，填 vendor/device/class
        │        │
        │        ▼
        │  pci_setup_device() → __pci_read_base()   ← 探測每個 BAR 大小（寫全1讀回）
        │        │                                     結果存進 pci_dev->resource[]
        │        │
        │  發現這是個 bridge (class 0x0604)? ── 是 ──► 遞迴 pci_scan_child_bus 掃它底下那段新 bus
        │        │ 否
        │        ▼
   （掃描階段結束，整棵 pci_dev 樹建好，但 BAR 還沒分配實體位址）
        │
        ▼
   pci_assign_unassigned_bus_resources()      ← 遍歷 resource[]，從實體位址池分配
        │                                        不衝突的區間，寫回每個 BAR
        ▼
   pci_bus_add_devices() ──► 對每個 pci_dev：device_add()
        │                       └─ 觸發 PCI bus 的 match（比對 driver id_table）
        ▼                          match 成功 → pci_device_probe() → 你的 .probe()
   driver 的 probe 被呼叫（enable / ioremap / set_master / alloc irq）
```

三個階段分得很清楚，理解這個分段是關鍵：

1. **掃描（scan）**：遞迴走遍每個 (B, D, F)，讀 vendor ID 判斷有沒有裝置（讀回全 1 = 空槽），有就建 `pci_dev` 並探測 BAR 大小。碰到 bridge 就遞迴進它底下的 bus——這就是「樹」怎麼被走出來的。此階段結束，`pci_dev` 樹完整，但 BAR 只知道**要多大**，還沒有實體位址。

2. **分配（assign）**：`pci_assign_unassigned_bus_resources()` 統一從實體位址池挑不衝突的區間，寫回每個 BAR。為什麼要分成獨立一階段而不是掃到就分配？因為要**全局視角**才能避免衝突、才能做對齊與 bridge window 的巢狀約束（bridge 底下所有裝置的位址必須落在 bridge 的 window 內）。多數情況韌體（BIOS/UEFI）開機時已經分配好了，kernel 沿用；但 kernel 保留重新分配的能力（`pci=realloc` 開機參數就是強制它重算）。

3. **綁定（bind）**：`pci_bus_add_devices()` 把每個 `pci_dev` 正式 `device_add` 進 driver core，這一步觸發 Ch 37 那套 match→probe。到此，硬體發現完全交棒給熟悉的 driver 模型。

這個「掃描建樹 → 分配資源 → 綁定 driver」的三段式，就是「可自我列舉」四個字的具體展開。對比 Ch 39：platform device 沒有第 1、2 階段（沒得掃、位址早在 DT 裡定死），直接從 DT 生 device 進第 3 階段。

## 動手：在 QEMU 加一個 PCI 裝置並 probe 它

我們用 QEMU 憑空插一張 PCI 裝置，寫一個最小 `pci_driver` 看它被 probe。這比在真機上安全太多，也是你日後開發真實驅動的標準沙盒。

**Step 1：QEMU 加一個 PCI 裝置。** 用 QEMU 內建的 `edu` 裝置——它是官方為教學設計的最小 PCI 裝置，vendor 0x1234、device 0x11e8，有一個 BAR 和支援 MSI/中斷：

```bash
qemu-system-x86_64 \
    -kernel arch/x86/boot/bzImage \
    -initrd initramfs.cpio.gz \
    -append "console=ttyS0 nokaslr" \
    -nographic -m 512M \
    -device edu                     # ← 插一張 edu PCI 裝置
```

（`edu` 若未編進你的 QEMU，改用 `-device pci-testdev` 或 virtio 裝置如 `-device virtio-net-pci` 也行，只要對應調整下面的 vendor/device ID。）

**Step 2：開機後在 QEMU 的 shell 裡看它被列舉出來：**

```
/ # lspci -nn                       # busybox 的 lspci 或另外放一個 pciutils
00:04.0 ... [1234:11e8]             # edu 裝置：vendor 1234, device 11e8
/ # ls /sys/bus/pci/devices/        # driver core 幫每個 pci_dev 建的 sysfs 目錄
0000:00:04.0  ...
/ # cat /sys/bus/pci/devices/0000:00:04.0/resource   # 看它的 BAR 分配到哪
0x00000000febf1000 0x00000000febf1fff 0x0004...      # BAR0 的實體位址範圍
```

裝置憑空出現在 `/sys/bus/pci/devices/`，位址已分配好——這就是列舉的成果，你什麼都沒描述，kernel 自己掃出來的。

**Step 3：寫最小 driver。** `edu` 裝置的 vendor/device 是 (0x1234, 0x11e8)：

```c
// edu_min.c
#include <linux/module.h>
#include <linux/pci.h>

static int edu_probe(struct pci_dev *pdev, const struct pci_device_id *id)
{
    void __iomem *bar;
    u32 ident;
    int err;

    err = pci_enable_device(pdev);
    if (err) return err;
    err = pci_request_regions(pdev, "edu_min");
    if (err) goto disable;

    bar = pci_ioremap_bar(pdev, 0);          // edu 的 BAR0
    if (!bar) { err = -ENOMEM; goto release; }

    ident = readl(bar + 0x00);               // edu 規格：offset 0 是識別暫存器
    pci_info(pdev, "edu probed! BAR0 ident register = 0x%08x\n", ident);

    pci_iounmap(pdev, bar);
    pci_release_regions(pdev);
    pci_disable_device(pdev);
    return 0;                                // demo 完就收工

release:
    pci_release_regions(pdev);
disable:
    pci_disable_device(pdev);
    return err;
}

static const struct pci_device_id edu_ids[] = {
    { PCI_DEVICE(0x1234, 0x11e8) },
    { 0, }
};
MODULE_DEVICE_TABLE(pci, edu_ids);

static struct pci_driver edu_driver = {
    .name = "edu_min", .id_table = edu_ids, .probe = edu_probe,
};
module_pci_driver(edu_driver);
MODULE_LICENSE("GPL");
```

編好（Makefile 同 Ch 0）、放進 initramfs、開機後：

```
/ # insmod /edu_min.ko
edu_min 0000:00:04.0: edu probed! BAR0 ident register = 0x010000edu
```

`insmod` 當下，`pci_register_driver` 拿 `edu_ids` 掃到已列舉的 edu 裝置、match 成功、`edu_probe` 被呼叫——你剛剛親手走了一遍「driver 註冊 → match → probe → ioremap BAR → 讀裝置暫存器（MMIO）」的完整鏈路。`0xedu` 那串是 edu 裝置規格裡的魔術識別值，讀到它證明你的 MMIO 真的打到裝置了。

**Step 4：看中斷。** 若你的裝置與 driver 有分配 MSI（edu 支援），開機後：

```
/ # cat /proc/interrupts | grep -i edu       # 或 grep MSI / 你的 driver 名
```

會看到對應的中斷計數列。網卡類裝置這裡會是一整排 MSI-X 向量、各綁一顆 CPU。

## 進階：SR-IOV、電源、AER 各點一下

config space 的 capability list 上還掛著幾個生產環境重要、面試會問的東西，各給一個座標：

- **SR-IOV（Single Root I/O Virtualization）**：一張實體網卡（PF，Physical Function）可以「分身」成數十個 VF（Virtual Function），每個 VF 有自己的 config space、BAR、佇列，能**直接 passthrough 給一個 VM**，讓 VM 幾乎原生速度存取硬體、繞過 hypervisor 的軟體交換。雲端伺服器高效能網路的基石。source 在 `drivers/pci/iov.c`，`echo N > /sys/.../sriov_numvfs` 就能生出 N 個 VF。

- **電源管理 capability**：PCI 裝置有 D0（全開）到 D3（深睡）的電源狀態，透過 PM capability 切換。這串到 Ch 42 的整體電源管理框架——`pci_enable_device` 會把裝置從 D3 喚回 D0，suspend 時反向。

- **AER（Advanced Error Reporting）**：PCIe 的擴充 capability（在 offset 0x100 以上，只 MMCONFIG 讀得到），讓裝置能上報 correctable/uncorrectable 錯誤（link 錯誤、TLP 錯誤），kernel 能記錄甚至嘗試恢復，而不是靜默資料損毀。伺服器可靠性的重要一環，source 在 `drivers/pci/pcie/aer.c`。

- **手動戳 config space：`setpci`**。`lspci` 是唯讀漂亮版，`setpci` 能直接讀寫 config space 的任意 offset，除錯時無價：

```bash
setpci -s 00:04.0 VENDOR_ID          # 讀 vendor id（等於 config offset 0x00）
setpci -s 00:04.0 COMMAND            # 讀 Command 暫存器
setpci -s 00:04.0 COMMAND=0x07       # 寫 Command：開 I/O+memory+bus master（危險，會動真硬體）
```

## 對比與取捨

| 面向 | Legacy PCI | PCIe |
|---|---|---|
| 電氣拓撲 | 共享並行匯流排 | 點對點序列 link + switch |
| 頻寬 | 所有裝置分享 | 每個 link 獨佔 |
| 軟體模型 | config space / BDF / BAR | **相同**（刻意相容） |
| 中斷 | INTx 共享中斷線為主 | MSI/MSI-X 為主 |
| config space | 256 byte（CF8/CFC 可達） | 4 KB（需 MMCONFIG/ECAM） |

| 中斷機制 | INTx | MSI | MSI-X |
|---|---|---|---|
| 本質 | 專用側頻線、電平觸發 | 記憶體寫入 | 記憶體寫入 |
| 可共享 | 是（缺點） | 否 | 否 |
| 最多向量 | 4 條線 | 32 | 2048 |
| 導向特定 CPU | 難 | 有限 | 可（每向量獨立） |
| 與 DMA 排序 | 有 race | 天然有序 | 天然有序 |
| 現代裝置 | 幾乎淘汰 | 少量用 | 主流 |

| 匯流排類型 | 列舉方式 | 位址來源 | 代表 |
|---|---|---|---|
| PCI/PCIe | 掃描硬體、裝置自報（本章） | 韌體/kernel 分配 BAR | 網卡、GPU、NVMe |
| platform | 讀 device tree / ACPI（Ch 39） | DT `reg` 定死 | SoC 上的 UART、I2C |
| USB | 插入時枚舉 | 動態分配 | 隨身碟、鍵鼠 |

## 踩雷集錦

1. **忘了 `pci_set_master`，DMA 靜悄悄不動**：`pci_enable_device` 只開了 CPU 存取裝置暫存器（MMIO）的能力，**沒開裝置主動 DMA 的能力**。網卡收包 DMA、NVMe 讀寫全靠 bus master bit。忘了設，probe 一切正常、暫存器讀寫都對，就是資料永遠 DMA 不進來——最難查的 bug 之一。DMA 細節見 Ch 41。

2. **以為 BAR 裡存的是位址**：出廠時 BAR 的高位是**未定的**、低位是**寫死的 0**（編碼大小）。實體位址是韌體/kernel 列舉時**寫進去**的。BAR 是「大小宣告 + 位址欄位」的合體，不是單純位址。理解「寫全 1 讀回探大小」這個把戲是讀懂列舉的關鍵。

3. **直接解參考 `__iomem` 指標（`*regs`）而非 `readl`/`writel`**：MMIO 不是普通 RAM，編譯器/CPU 的優化會合併、重排、快取存取，導致寫少了、讀到舊值。一定要用 `readl`/`writel`（含 volatile + barrier）。`__iomem` annotation 就是在警告你這件事，sparse 會抓直接解參考。

4. **在 MMCONFIG 不可用時想讀 offset 0x100 以上**：擴充 config space（AER、SR-IOV 等）只有透過 MMCONFIG/ECAM 才碰得到。老舊平台或韌體沒提供 MCFG 表時 kernel 退回 CF8/CFC，那些擴充 capability 就讀不到（`lspci` 會少一截）。看到 `lspci -vvv` 少了 PCIe 擴充區，先查是不是 MMCONFIG 沒起來。

5. **`pci_enable_device` 沒檢查回傳值**：它可能因資源衝突、電源問題失敗。跟 Ch 39 的 platform driver 一樣，probe 裡每個資源獲取步驟都要檢查並在失敗時**反序釋放**（goto 錯誤標籤鏈），漏一個就是資源洩漏。

6. **BDF 不是穩定 ID**：`0000:03:00.0` 這種位址會因插槽、bridge 拓撲、韌體版本而變，別拿它當裝置的持久識別。要穩定綁定用 vendor/device/subsystem ID 或 udev 規則。

## 動手練習

1. **拆一張真卡的 config space**：在你的 host（不是 QEMU）跑 `lspci -vvv`，挑一張網卡或 NVMe，找出它的 vendor/device ID、class code、每個 BAR 的大小與類型（memory/IO、32/64 bit）、以及它用 MSI 還是 MSI-X（capability 那段）。對照本章的 config space 圖，把每個欄位認出來。

2. **用 setpci 讀 Command 暫存器**：`setpci -s <BDF> COMMAND`，解讀 bit0（IO enable）、bit1（memory enable）、bit2（bus master）。找一張正在用的裝置，確認它 bus master bit（bit2）是 1——這就是踩雷 #1 講的那個 bit，driver 幫它設好了。

3. **在 QEMU 用 gdb 停在列舉過程**：`break pci_scan_single_device`，開機時它會為每個 (B,D,F) 停一次，`print pdev->vendor` 看每個裝置的 vendor id 被讀出來的瞬間。再 `break __pci_read_base` 看 BAR 大小怎麼被探測出來。這是把「列舉」從文字變成你親眼看到的動畫。

4. **弄壞 id_table**：把 Step 3 driver 的 device id 從 0x11e8 改成 0x9999，重編 `insmod`，觀察 probe **不會**被呼叫（match 失敗）——這驗證了 match 完全靠 config space 的 ID 比對，證明 Ch 37 的骨架在 PCI 上原封不動地成立。

5. **看 MSI-X 向量綁 CPU**：host 上 `cat /proc/interrupts | grep <你的網卡>`，數它有幾個 MSI-X 向量，再 `cat /proc/irq/<N>/smp_affinity` 看每個向量綁哪顆 CPU。這是 Ch 44 網路多佇列擴展性的物理基礎。

## 本章重點整理

- PCI/PCIe 的核心優勢是**可自我列舉**：kernel 掃描匯流排，裝置透過 config space 自報 vendor/device/class 與資源需求，無需 device tree（對比 Ch 39）。match→probe 骨架與 Ch 37 完全相同，只是 device 來源從「解析 DT」換成「掃描硬體」。
- config space（256 byte / PCIe 4 KB）是裝置的自我描述表；vendor/device ID 供 driver match、class code 供通用 driver 認類、**BAR 宣告資源需求**、capability list 掛 MSI/PM/AER。x86 用 CF8/CFC（舊）或 MMCONFIG/ECAM（新，才碰得到擴充區）存取。
- 列舉三段式：**掃描**建 `pci_dev` 樹並探 BAR 大小（寫全 1 讀回）→ **分配**把實體位址寫回 BAR → **綁定** device_add 觸發 match/probe。driver 用 `pci_resource_start` + `pci_ioremap_bar` 把 BAR 實體位址 ioremap 成 kernel 虛擬位址，靠 `readl`/`writel` 做 MMIO。
- driver 骨架：`pci_driver` + `id_table`，probe 裡 `pci_enable_device` → `pci_request_regions` → `pci_ioremap_bar` → `pci_set_master`（開 DMA，接 Ch 41）；中斷用 `pci_alloc_irq_vectors` 拿 MSI-X（多向量、無共享、可綁 CPU，接 Ch 15/29/44），這是現代高效能裝置的標配。

## 自我檢核

- [ ] 不看筆記，能講清楚「PCI 可自我列舉」到底指什麼、跟 Ch 39 的 device tree 差在哪
- [ ] 能解釋 BAR 怎麼同時表達「我要多少空間」和「我的空間在哪」——包括「寫全 1 讀回探大小」的把戲
- [ ] 能說出從裝置的 BAR 實體位址到 driver 能 `readl` 的 kernel 虛擬位址，中間 `pci_ioremap_bar` 做了什麼
- [ ] 能說出 `pci_enable_device` 和 `pci_set_master` 各開什麼能力，忘了後者會出什麼 bug
- [ ] 面試被問「PCIe 為什麼用 MSI-X 不用 INTx」，能講出無共享、多向量、可綁 CPU、與 DMA 天然排序這幾點
- [ ] 能默寫出列舉的三個階段（掃描/分配/綁定）各做什麼、為什麼分配要獨立成一階段

## 延伸閱讀

### 官方文件

- **[Documentation/PCI/pci.rst](https://www.kernel.org/doc/html/latest/PCI/pci.html)**
  - **讀哪裡**：整篇，尤其「How to write Linux PCI drivers」那節。這是 kernel 官方的 PCI driver 撰寫指南，本章 driver 骨架就是它的濃縮
  - **和本章的關聯**：`pci_enable_device` / `pci_request_regions` / `pci_set_master` / `pci_alloc_irq_vectors` 的正式語意與正確呼叫順序在這裡，寫真實 driver 前該通讀

- **[Documentation/PCI/msi-howto.rst](https://www.kernel.org/doc/html/latest/PCI/msi-howto.html)**
  - **讀哪裡**：整篇。MSI/MSI-X 的 API（`pci_alloc_irq_vectors` 家族）與設計理由的權威說明
  - **能學到什麼**：MSI vs MSI-X 的差異細節、多向量怎麼分配與綁 IRQ、為什麼舊的 `pci_enable_msix` 被取代

### 深入文章 / 教材

- **[QEMU edu device spec](https://github.com/qemu/qemu/blob/master/docs/specs/edu.rst)** — QEMU 官方
  - **這是什麼**：本章動手用的 `edu` 教學裝置的完整暫存器規格
  - **為什麼值得讀**：它是為「學寫 PCI driver」而生的最小裝置，規格短、有 MMIO/DMA/中斷各一個最小例子，是你動手寫 driver 的最佳靶子。配 QEMU 源碼 `hw/misc/edu.c` 看裝置那側怎麼實作，對照你的 driver 這側，兩邊打通

- **[Bootlin: PCI drivers 訓練章節](https://bootlin.com/doc/training/linux-kernel/)** — Bootlin
  - **讀哪裡**：PCI 那個模組的投影片。Bootlin 的 embedded Linux 教材一貫紮實，把列舉、BAR、MSI 配真實 driver 講清楚
  - **前提**：跟完本章、有一顆能跑的 QEMU

### 書籍 / 規格

- **《Linux Device Drivers, 3rd Ed.》（LDD3）** — Corbet / Rubini / Kroah-Hartman
  - **讀哪裡**：Ch 12「PCI Drivers」。經典的 PCI driver 整章講解，config space、BAR、probe 一路到中斷
  - **注意**：書對應 2.6，MSI-X API（`pci_alloc_irq_vectors`）是後來才有的，中斷那節以本章與官方 msi-howto 為準；但 config space / BAR / 列舉的概念完全不過時

- **《PCI Express System Architecture》** — MindShare（Ravi Budruk 等）
  - **這本書的定位**：想從電氣/協定層真正搞懂 PCIe（TLP、link training、封包格式）就讀這本，是業界公認的 PCIe 聖經
  - **注意**：偏硬體/協定，超出本章的軟體視角。做硬體驗證或高階 driver 除錯（AER、link 問題）時才需要下潛到這層

你現在會處理「能自我列舉」的裝置了：掃出來、分配 BAR、ioremap、match/probe。但 probe 只是把裝置認出來——真正的效能戲碼是裝置怎麼**主動搬資料**（DMA）、怎麼**通知 CPU**（中斷處理下半部）、以及怎麼把裝置記憶體**直接映射給使用者空間**（mmap）。下一章我們把 `pci_set_master` 開啟的那個 DMA 能力展開，看一個高效能裝置驅動的資料路徑到底怎麼跑。

→ [Ch 41 中斷驅動裝置：DMA 與 mmap](./41-interrupt-dma-mmap.md)
