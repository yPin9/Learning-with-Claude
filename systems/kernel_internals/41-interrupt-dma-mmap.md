# Ch 41 — 中斷驅動裝置、DMA、mmap

> **目標**：搞懂高速裝置怎麼不佔用 CPU 就把資料搬進 RAM（DMA）、為什麼裝置看到的位址跟 CPU 的實體位址不一樣（bus address / IOMMU）、cache 一致性為什麼會咬人，以及怎麼把一塊 kernel/裝置記憶體 mmap 給 userspace 做零複製。學完你能寫一個用 `dma_alloc_coherent` 配 DMA buffer、再用 `remap_pfn_range` 把它 mmap 給 user 程式的模組，並在腦中畫出「發起 I/O → DMA 搬資料 → 完成中斷 → bottom half 收尾」的完整裝置 I/O 模型。

前面幾章把裝置一層層剝開：Ch 37 講 device model（kobject/sysfs 怎麼把裝置掛上樹）、Ch 38 講 char device 怎麼給 userspace `read`/`write`/`mmap`、Ch 39 講 platform driver 怎麼從 device tree 拿資源、Ch 40 講 PCI 怎麼列舉出一張卡並拿到它的 BAR 與 IRQ。到目前為止，「驅動怎麼跟裝置搬資料」我們一直含糊帶過。這章補上——而且是效能的核心。網卡（Ch 44）、磁碟（Ch 36）、GPU 之所以快，全靠這章的東西。

## 為什麼需要這個？

想像你要從網卡收一個 1500 bytes 的封包。最直白的做法：CPU 在網卡的資料暫存器上，一次讀一個 word（4 或 8 bytes），讀完再存進 RAM。這叫 **PIO（Programmed I/O）**——CPU 親手一個一個搬。

問題有兩層。第一，**慢**：MMIO 讀寫（Ch 40）走的是 uncached、非 posted 的匯流排交易，一次動輒上百個 CPU cycle；1500 bytes 要幾百次讀，加起來是幾萬 cycle。第二，**佔 CPU**：這幾萬 cycle CPU 什麼別的事都做不了，就在那邊當搬運工。10 Gbps 網卡一秒能收上百萬個封包，用 PIO 搬，CPU 全部拿去搬資料都不夠。

解法是把 CPU 從搬運工的位置上請下來。裝置裡有一個小引擎叫 **DMA 引擎（Direct Memory Access engine）**，你只要告訴它「把資料寫到實體位址 X、寫 N bytes」，它就自己去讀寫 RAM，搬完發一個中斷通知 CPU。搬的過程 CPU 完全自由，可以去跑別的 task。

```
   PIO（CPU 當搬運工）                    DMA（裝置自己搬）
   ┌──────┐                              ┌──────┐
   │ CPU  │  ①讀 device reg              │ CPU  │  ①寫 desc：「搬到 addr X, N bytes」
   │      │──── MMIO read ────►┌──────┐  │      │──── 設定 ──►┌──────────┐
   │ 忙到 │◄─── 一個 word ─────│device│  │ 自由 │             │  device  │
   │ 死   │  ②寫進 RAM         │      │  │ 去跑 │             │ DMA 引擎 │
   │      │──── store ──►┌───┐ └──────┘  │ 別的 │             └────┬─────┘
   │      │              │RAM│           │ task │  ②裝置直接讀寫   │ DMA
   │  ③重複幾百次        └───┘           │      │      ┌───┐◄──────┘
   └──────┘                              └──┬───┘      │RAM│
     整段時間 CPU 卡死                      │◄── ③完成中斷 └───┘
                                          搬完才回來收尾（bottom half）
```

一句話：**PIO 讓 CPU 搬資料，DMA 讓裝置搬資料、CPU 只負責發起與收尾。** 所有講究吞吐量的裝置都用 DMA。這章剩下的篇幅都在處理「讓裝置自己去讀寫 RAM」帶來的三個麻煩。

## 先建立直覺：DMA 的三個麻煩

讓裝置直接碰 RAM，聽起來美好，但 CPU 和裝置對「記憶體」的認知並不一致。有三個坑要填：

**麻煩一：位址不一樣。** CPU 用虛擬位址，MMU（Ch 16）把它翻成實體位址。但 DMA 引擎不經過 CPU 的 MMU——它掛在匯流排上，看到的是**匯流排位址（bus address）**，也常叫 **DMA 位址（DMA address）**。在最單純的 x86 上，bus address 剛好等於 physical address，所以很多人以為它們一樣。一旦中間插了 **IOMMU**（把裝置的存取再翻譯一層），或某些嵌入式平台匯流排上有位址偏移，兩者就分家了。**驅動絕對不能把 CPU 拿到的實體位址直接餵給裝置**——要餵的是 DMA API 回給你的那個 `dma_addr_t`。

**麻煩二：cache 不同步。** CPU 寫資料通常先進 cache（Ch 23），不見得馬上落到 RAM。如果此時叫裝置去 RAM 讀，裝置讀到的是舊資料（cache 裡的新值還沒 flush）。反過來，裝置 DMA 寫進 RAM 後，CPU 的 cache 裡可能還存著那塊位址的舊副本，CPU 讀到的是過期資料。這就是 **DMA 的 cache 一致性（cache coherence）問題**——Ch 23 講的 cache coherence 在這裡具體咬人。x86 的匯流排硬體會自動維持 DMA 一致性（DMA 是 coherent 的），但很多 ARM/MIPS 平台不會，需要驅動手動 flush/invalidate cache。DMA API 的存在就是為了把這件事抽象掉，讓同一份驅動碼在兩種平台都對。

**麻煩三：裝置定址能力有限。** 一個 32-bit 的老裝置，它的 DMA 引擎位址暫存器只有 32 bit，只能定址低 4 GB。如果你的 buffer 被配在 4 GB 以上的實體位址，這個裝置根本搆不到。這就是 Ch 17 講的 `ZONE_DMA32`（低 4 GB）與 `ZONE_DMA`（低 16 MB，給更古老的裝置）存在的原因——buddy allocator 特別留出低位址區，好讓這些裝置有記憶體可用。驅動要用 `dma_set_mask` 宣告「我這裝置能定址幾 bit」，kernel 才知道要從哪個 zone 配、或是否需要 IOMMU / bounce buffer 幫忙。

DMA API 就是為了一次解決這三個麻煩而設計的。你照它的規矩走，這三坑它幫你填；你繞過它直接用 `virt_to_phys` 餵位址給裝置，三個坑遲早都會掉進去。

## DMA API：兩種記憶體、一套規矩

核心標頭是 `include/linux/dma-mapping.h`，實作在 `kernel/dma/`（`mapping.c`、`direct.c`、`swiotlb.c` 等），權威文件是 `Documentation/core-api/dma-api.rst` 與 `dma-api-howto.rst`。DMA API 給你兩種記憶體，對應兩種使用情境。

### coherent / consistent DMA：常駐、不用手動同步

```c
dma_addr_t dma_handle;
void *cpu_addr = dma_alloc_coherent(dev, size, &dma_handle, GFP_KERNEL);
// cpu_addr   : CPU 用這個指標讀寫
// dma_handle : 餵給裝置的 DMA 位址（寫進裝置暫存器的就是它）
...
dma_free_coherent(dev, size, cpu_addr, dma_handle);
```

`dma_alloc_coherent`（`kernel/dma/mapping.c` 的 `dma_alloc_attrs`）配一塊 CPU 和裝置**看法一致**的記憶體。「一致」的意思是：CPU 寫了裝置馬上看得到、裝置寫了 CPU 馬上看得到，**不用手動 sync**。在需要維護一致性的平台上，kernel 會把這塊記憶體設成 uncached（或用其他一致性機制），代價是 CPU 存取它比一般 cached 記憶體慢。

它一次回你**兩個位址**，這是全章最該記住的一件事：`cpu_addr` 是 CPU 端的虛擬位址（你的 C 程式 deref 它），`dma_handle`（型別 `dma_addr_t`）是裝置端的 DMA 位址（你寫進裝置 DMA 暫存器的就是它）。兩者指向同一塊實體記憶體，但**數值不一定相同**（有 IOMMU 時幾乎一定不同）。

適合什麼？**裝置和 CPU 都要頻繁存取、生命週期長**的東西——最典型是**描述符環（descriptor ring）**。網卡（Ch 44）的 RX/TX ring、NVMe（Ch 36）的 submission/completion queue，都是 `dma_alloc_coherent` 配的。因為 CPU 一直在更新描述符、裝置一直在讀描述符，如果每次都要手動 sync 會又煩又慢，所以直接配一塊一致的記憶體常駐。

### streaming DMA：一次性、要手動 sync

```c
// 已經有一塊 buffer（例如上層傳下來的 sk_buff 資料、或某個 kmalloc 的 buffer）
dma_addr_t dma_handle = dma_map_single(dev, buf, size, DMA_TO_DEVICE);
if (dma_mapping_error(dev, dma_handle)) { /* 處理失敗 */ }

// 把 dma_handle 寫進裝置，觸發傳輸，等完成中斷 ...

dma_unmap_single(dev, dma_handle, size, DMA_TO_DEVICE);
```

streaming DMA（`dma_map_single` / `dma_map_page` / `dma_map_sg`，都在 `kernel/dma/mapping.c`）對應的是**「我已經有一塊 buffer，想讓裝置對它做一次性傳輸，做完就解除」**的情境。它不配新記憶體，而是把你既有的 buffer **映射**給裝置用——在有 IOMMU 的平台建立 IOMMU 頁表項、回你一個 DMA 位址；在需要 bounce 的平台可能複製到低位址暫存區。

streaming 的關鍵字是**方向**與**手動同步**：

- 方向 `enum dma_data_direction`（`include/linux/dma-direction.h`）：`DMA_TO_DEVICE`（CPU→裝置，如送封包）、`DMA_FROM_DEVICE`（裝置→CPU，如收封包）、`DMA_BIDIRECTIONAL`、`DMA_NONE`。方向決定 map/unmap 時該 flush 還是 invalidate cache，**填錯方向會在非一致平台上讀到髒資料，且極難 debug**。
- **map 之後、unmap 之前，這塊 buffer 的所有權在裝置手上**，CPU 不該碰它。若中途 CPU 真的要看，得用 `dma_sync_single_for_cpu`（把所有權暫時要回來、invalidate cache 讓 CPU 讀到新值）看完再 `dma_sync_single_for_device` 還回去。

**散布/聚集（scatter/gather）**：一塊邏輯 buffer 在實體上常是碎的（好幾個不連續 page）。`dma_map_sg`（配 `struct scatterlist`）一次把整串碎片映射給裝置，裝置的 SG 引擎逐段搬。這是磁碟與網路 I/O 的常態，因為 user buffer 幾乎不可能實體連續。有 IOMMU 時 `dma_map_sg` 還能把碎片「黏合」成裝置眼中連續的一段 IOVA，回傳的 nents 可能比傳入少——這是 IOMMU 的額外好處。

### 所有權轉移：map/sync/unmap 到底對 cache 做什麼

streaming 那句「所有權」不是比喻，它精確對應一連串 cache 操作。以在非一致平台（多數 ARM）送封包（`DMA_TO_DEVICE`）為例，把每一步實際發生的事攤開：

```
   CPU 填好 buffer（資料還在 CPU cache 裡，RAM 是舊的）
        │
   dma_map_single(dev, buf, len, DMA_TO_DEVICE)
        │   → clean/flush cache：把 cache 的新值寫回 RAM，讓裝置待會讀得到
        │   → 所有權交給裝置。此後 CPU 不准寫 buf
        ▼
   裝置 DMA 讀 RAM、送出去 ────────► （CPU 去做別的事）
        │
   dma_unmap_single(dev, handle, len, DMA_TO_DEVICE)
        │   → 拆掉 IOMMU 映射；TO_DEVICE 方向這裡通常不需動 cache
        │   → 所有權還給 CPU
```

收封包（`DMA_FROM_DEVICE`）方向相反：`dma_map_single` 時把這塊的 cache 行 **invalidate**（丟掉可能過期的副本），裝置 DMA 寫進 RAM 後，`dma_unmap`（或 `dma_sync_single_for_cpu`）再 invalidate 一次，確保 CPU 接下來讀的是 RAM 裡裝置寫的新值、不是 cache 裡的舊值。**方向決定的就是這裡 clean 還是 invalidate**——這解釋了踩雷集錦第 2 條為何方向填反會讀到髒資料。

在 x86（DMA 硬體一致）上，這些 sync 是 no-op，所以你在 x86 開發永遠不會因為忘了它而出錯；一移植到 ARM 就爆。**正確的驅動即使在 x86 也照規矩呼叫 sync/unmap**，這樣同一份碼在兩種平台都對。

### 宣告定址能力：dma_set_mask

裝置一 probe 出來（Ch 40），驅動就該宣告它能定址幾 bit：

```c
// 這張卡是 64-bit DMA capable
if (dma_set_mask_and_coherent(dev, DMA_BIT_MASK(64))) {
    // 退而求其次試 32-bit
    if (dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32)))
        return -EIO;   // 連 32-bit 都不行，放棄
}
```

`dma_set_mask`（streaming 用）與 `dma_set_coherent_mask`（coherent 用），或一次設兩者的 `dma_set_mask_and_coherent`，告訴 DMA 層這裝置搆得到多高的位址。宣告 32-bit，之後 `dma_alloc_coherent` 就會從 `ZONE_DMA32`（Ch 17）配、或在配到高記憶體時透過 IOMMU / SWIOTLB bounce buffer 幫忙。宣告錯（明明只能 32-bit 卻報 64-bit）會讓裝置 DMA 到搆不到的位址，資料默默壞掉。這就是 Ch 17 的 zone 劃分在驅動層現身的地方。

## IOMMU：裝置的 MMU

CPU 有 MMU 把虛擬位址翻成實體位址，並隔離各行程。裝置也需要一個對等物，這就是 **IOMMU（I/O Memory Management Unit）**——x86 上是 Intel VT-d / AMD-Vi，ARM 上是 SMMU（System MMU，見 arm 課的 SMMU 章）。它坐在裝置與記憶體之間，把裝置發出的 DMA 位址（IOVA，I/O virtual address）翻譯成真正的實體位址。

```
   沒有 IOMMU：                         有 IOMMU：
   device ── DMA addr ──► RAM          device ── IOVA ──►┌───────┐── phys addr ──► RAM
            (= 實體位址，                                │ IOMMU │
             裝置想寫哪就寫哪)                            │ 頁表  │  只允許被映射過的頁
                                                         └───────┘  其餘一律擋下
```

IOMMU 帶來三個好處，每個都對得上其他章：

1. **隔離與保護**：沒有 IOMMU 時，一個惡意或壞掉的裝置可以 DMA 到**任意**實體位址——包括 kernel 程式碼、其他行程的記憶體。這就是 DMA attack（惡意 Thunderbolt/PCIe 裝置直接讀走整台機器記憶體，見 kernel_pwn 課的 DMA 攻擊）。開了 IOMMU 後，裝置只能碰「被明確映射給它」的頁，其餘一律被 IOMMU 擋下並回報 fault。這也是 `dma_map_*` 在 IOMMU 平台真正做的事：建立一條「這裝置這次可以碰這塊」的臨時頁表項，unmap 就拆掉。

2. **讓 32-bit 裝置搆到高記憶體**：IOMMU 可以把一個低於 4 GB 的 IOVA 映射到 4 GB 以上的實體頁。於是 32-bit 裝置給出 32-bit 位址，IOMMU 翻譯後打到高記憶體，不必真的把 buffer 搬到低位址（沒 IOMMU 時就得靠 SWIOTLB bounce buffer 複製一份，見 `kernel/dma/swiotlb.c`）。

3. **虛擬化直通（passthrough）**：把整個 PCI 裝置指派給虛擬機（VFIO，下節），IOMMU 保證 guest 的 DMA 出不了它被分配的記憶體範圍。

看你的機器 IOMMU 分組：

```bash
ls /sys/kernel/iommu_groups/                          # 有這目錄代表 IOMMU 開著
ls /sys/kernel/iommu_groups/0/devices/                # group 0 裡有哪些裝置
dmesg | grep -e DMAR -e IOMMU -e AMD-Vi               # 開機時 IOMMU 初始化訊息
```

**IOMMU group** 是隔離的最小單位——同一 group 裡的裝置無法彼此隔離（通常因為它們共用一段匯流排拓撲或有 P2P 能力），要嘛整組直通、要嘛整組留給 host。這在 VFIO 直通時是硬約束。

**dma-buf** 順帶一提：`Documentation/driver-api/dma-buf.rst` 定義的 dma-buf 是一套讓不同裝置/驅動**共享同一塊 DMA buffer** 的框架（例如相機解碼出的 frame 直接給 GPU 顯示，不經 CPU 複製）。它是 zero-copy 跨裝置管線（camera→GPU→display）的基礎，這裡點到，細節超出本章。

## 底層機制一：同一個 dma_map，三條路

DMA API 之所以能讓同一份驅動碼跨平台，是因為它把「怎麼變出 DMA 位址」藏在後端。`dma_map_single` 進到 `kernel/dma/mapping.c` 的 `dma_map_page_attrs` 後，看這裝置掛的是哪個 `dma_map_ops`，分派到三條路之一：

```
   dma_map_single(dev, buf, len, dir)
        │
        ├─► ① direct（kernel/dma/direct.c）── 沒 IOMMU、裝置搆得到
        │      DMA addr = phys_to_dma(phys)  多半就是實體位址（或加 dma-ranges 偏移）
        │      非一致平台在這裡做 cache clean/invalidate
        │
        ├─► ② IOMMU（drivers/iommu/…）── 有 IOMMU
        │      配一段 IOVA、在 IOMMU 頁表填「這 IOVA → 這實體頁」
        │      回傳 IOVA 當 DMA 位址；unmap 時拆掉頁表項（隔離就靠這）
        │
        └─► ③ SWIOTLB（kernel/dma/swiotlb.c）── 沒 IOMMU 但裝置搆不到這塊高記憶體
               在低位址預留區配一塊 bounce buffer，把資料複製過去
               回傳 bounce buffer 的低位址；效能較差，是相容後盾
```

`dma_alloc_coherent` 同理分派。你寫驅動時完全不必知道跑在哪條路上——這正是抽象的價值：x86 桌機（多半 direct 或 IOMMU）、開了 VT-d 的伺服器（IOMMU）、一台沒 IOMMU 的 32-bit ARM SoC（direct + 必要時 SWIOTLB），同一份 `dma_map_single` 都對。理解這三條路，你就懂了為什麼「宣告錯 mask」會害你莫名走 SWIOTLB（第 5 條進階），以及為什麼 IOMMU 能同時給你隔離與高記憶體存取。

## 底層機制二：完整的裝置 I/O 模型

把中斷（Ch 29）、DMA、bottom half（Ch 30）串起來，就是現代裝置驅動的標準骨架。以「收一個封包」為例：

```
時間軸 ──────────────────────────────────────────────────────────────►

 ① 驅動準備（開機/收包後 refill）
    dma_alloc_coherent 配 RX ring（描述符環，coherent）
    每個 RX 描述符指向一塊 dma_map 過的 buffer
    把 ring 的 DMA 位址寫進裝置暫存器，告訴裝置「資料放這」

 ② 裝置收到封包，自己 DMA 進 RAM       ← CPU 完全沒參與，去跑別的 task
    device ─DMA─► RX buffer（實體記憶體）
    device 更新描述符：「這格填好了，len=1500」

 ③ 裝置發「DMA 完成中斷」（Ch 29）
    IRQ line ───► CPU ───► 你註冊的 top half（hardirq handler）
    ┌─ top half（關中斷、要快）───────────────────┐
    │  只做最少：ack 硬體、關掉這條 IRQ、          │
    │  排一個 bottom half（napi_schedule / 見 Ch44）│
    │  馬上返回                                     │
    └───────────────────────────────────────────────┘

 ④ bottom half（softirq / NAPI poll，Ch 30）  ← 開中斷、可久一點
    ┌────────────────────────────────────────────────┐
    │  dma_sync/unmap 把 buffer 所有權要回 CPU        │
    │  讀描述符、把封包往上層協定堆疊送（Ch 43/44）   │
    │  refill：給裝置新的空 buffer（回到 ①）          │
    └────────────────────────────────────────────────┘
```

這個「**top half 只 ack + 排程、真正的活丟給 bottom half**」的分工，正是 Ch 29/30 講的 top/bottom half 拆分在真實裝置上的樣子。為什麼非拆不可？因為 hardirq handler 執行時通常關著中斷（至少關著同條 IRQ），拖太久會漏掉後續中斷、拉高延遲。所以 handler 裡只做「快到不能再快」的事，剩下的搬到 softirq/tasklet/workqueue/threaded IRQ（Ch 30/31）——那裡開著中斷，可以慢慢做。

descriptor ring 為什麼用 coherent 而 packet buffer 為什麼用 streaming？ring 是 CPU 和裝置**輪流頻繁讀寫**的共享結構（CPU 填、裝置消費、裝置回填、CPU 讀），適合一致記憶體免同步；packet buffer 是**一次性**的（裝置寫一次、CPU 讀一次），用完就換，適合 streaming map/unmap。這個搭配是所有高速裝置驅動的通用模式。

## 動手：DMA buffer + mmap 給 userspace

我們寫一個模組，做兩件事：用 `dma_alloc_coherent` 配一塊 DMA buffer 並印出 CPU 位址 vs DMA 位址（讓你親眼看到它們不同）、再用 `remap_pfn_range` 把這塊 buffer mmap 給 user 程式讀寫（零複製）。這串起 Ch 38 的 `file_operations->mmap` 與 Ch 19 的 VMA。

沒有真裝置，我們用 platform device 自己造一個 `struct device`（`dma_alloc_coherent` 需要一個 `dev` 來查詢 DMA 能力）。

```c
// dmabuf_demo.c
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/miscdevice.h>
#include <linux/dma-mapping.h>
#include <linux/mm.h>
#include <linux/fs.h>

#define BUF_SIZE  (4 * PAGE_SIZE)   // 16 KB，PAGE_SIZE 對齊，mmap 才好處理

static struct platform_device *pdev;
static void      *cpu_addr;         // CPU 端虛擬位址
static dma_addr_t  dma_handle;      // 裝置端 DMA 位址

// --- mmap：把 DMA buffer 映進 user 位址空間（接 Ch 38 / Ch 19） ---
static int demo_mmap(struct file *filp, struct vm_area_struct *vma)
{
    unsigned long size = vma->vm_end - vma->vm_start;
    unsigned long pfn;

    if (size > BUF_SIZE)
        return -EINVAL;                     // 別讓 user 映射超過我們配的量

    // cpu_addr 是 dma_alloc_coherent 配的一般 RAM，用 virt_to_phys 算 pfn
    // （若映射的是裝置 MMIO，改用 io_remap_pfn_range，pfn 來自 BAR 實體位址）
    pfn = virt_to_phys(cpu_addr) >> PAGE_SHIFT;

    // 把 [vm_start, vm_end) 這段 user VA 直接指向我們的實體頁——零複製
    if (remap_pfn_range(vma, vma->vm_start, pfn, size, vma->vm_page_prot))
        return -EAGAIN;

    return 0;
}

static const struct file_operations demo_fops = {
    .owner = THIS_MODULE,
    .mmap  = demo_mmap,
};

static struct miscdevice demo_misc = {
    .minor = MISC_DYNAMIC_MINOR,
    .name  = "dmabuf_demo",              // 會出現在 /dev/dmabuf_demo（Ch 38 misc device）
    .fops  = &demo_fops,
};

static int __init demo_init(void)
{
    int ret;

    // 造一個 platform device，好有個 struct device 給 DMA API 用
    pdev = platform_device_register_simple("dmabuf_demo", -1, NULL, 0);
    if (IS_ERR(pdev))
        return PTR_ERR(pdev);

    // 宣告定址能力（Ch 17 zone / IOMMU 靠這決定怎麼配）
    ret = dma_set_coherent_mask(&pdev->dev, DMA_BIT_MASK(32));
    if (ret)
        goto err_pdev;

    // 配一塊 CPU 與裝置一致的 DMA buffer
    cpu_addr = dma_alloc_coherent(&pdev->dev, BUF_SIZE, &dma_handle, GFP_KERNEL);
    if (!cpu_addr) { ret = -ENOMEM; goto err_pdev; }

    // 關鍵：印出兩個位址，你會看到它們不相等
    pr_info("dmabuf_demo: cpu_addr(VA)=%px  phys=%pa  dma_handle=%pad  size=%d\n",
            cpu_addr, &(phys_addr_t){virt_to_phys(cpu_addr)}, &dma_handle, BUF_SIZE);

    // 在 buffer 裡寫個記號，等下 user 端 mmap 後應該讀得到
    snprintf(cpu_addr, BUF_SIZE, "hello from kernel DMA buffer\n");

    ret = misc_register(&demo_misc);      // 註冊 /dev/dmabuf_demo
    if (ret) goto err_dma;

    return 0;

err_dma:
    dma_free_coherent(&pdev->dev, BUF_SIZE, cpu_addr, dma_handle);
err_pdev:
    platform_device_unregister(pdev);
    return ret;
}

static void __exit demo_exit(void)
{
    misc_deregister(&demo_misc);
    dma_free_coherent(&pdev->dev, BUF_SIZE, cpu_addr, dma_handle);
    platform_device_unregister(pdev);
    pr_info("dmabuf_demo: unloaded\n");
}

module_init(demo_init);
module_exit(demo_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("DMA coherent buffer + mmap to userspace demo");
```

> **`%pa` / `%pad` / `%px`**：kernel 的 printk 有專用格式符。`%pad` 印 `dma_addr_t`、`%pa` 印 `phys_addr_t`（都要傳位址 `&`，因為型別寬度依平台而定）、`%px` 印裸指標（一般日誌用 `%p` 會被雜湊化以防資訊洩漏；這裡教學用 `%px` 印真值，正式驅動別這樣）。

user 端程式，`mmap` 這個 char device 然後直接讀寫：

```c
// user_mmap.c  —— gcc user_mmap.c -o user_mmap
#include <stdio.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

#define BUF_SIZE (4 * 4096)

int main(void)
{
    int fd = open("/dev/dmabuf_demo", O_RDWR);
    if (fd < 0) { perror("open"); return 1; }

    // mmap 觸發 kernel 的 demo_mmap → remap_pfn_range
    char *p = mmap(NULL, BUF_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (p == MAP_FAILED) { perror("mmap"); return 1; }

    printf("kernel wrote: %s", p);        // 讀到 kernel 在 buffer 裡寫的記號
    sprintf(p + 128, "hello back from userspace\n");   // 反過來寫，kernel 也看得到

    munmap(p, BUF_SIZE);
    close(fd);
    return 0;
}
```

在 QEMU 裡跑：

```
/ # insmod /dmabuf_demo.ko
dmabuf_demo: cpu_addr(VA)=ffff8880... phys=0x... dma_handle=0x... size=16384
/ # ./user_mmap
kernel wrote: hello from kernel DMA buffer
```

你看到的 `cpu_addr` 是 kernel 虛擬位址、`phys` 是實體位址、`dma_handle` 是裝置位址。在沒開 IOMMU 的 QEMU x86 上，`phys` 和 `dma_handle` 數值會**相等**（bus address == physical address）；把 QEMU 加上 `-device intel-iommu` 並在 guest 開 `intel_iommu=on`，`dma_handle` 就會變成一個 IOMMU 翻譯過的、和 `phys` 不同的 IOVA。這是親眼確認「DMA 位址 ≠ 實體位址」最直接的實驗。

> **踩雷預告**：`remap_pfn_range` 對 `dma_alloc_coherent` 回的記憶體是簡化教學版。嚴格來說，coherent 記憶體在某些平台是特殊映射（可能 uncached），正式做法應該用 `dma_mmap_coherent`（`kernel/dma/mapping.c`），它會用正確的 page attribute 把 coherent buffer 映給 user。這裡用 `remap_pfn_range` 是為了讓你看清「pfn → user VA」這條最原始的機制（接 Ch 19 VMA），生產驅動請用 `dma_mmap_coherent`。

### remap_pfn_range 在 VMA 上到底做了什麼

`mmap` syscall 走到你的 `demo_mmap`（Ch 38 的 `file_operations->mmap`）時，kernel 已經替這次映射建好一個 `struct vm_area_struct`（VMA，Ch 19），`vma->vm_start`~`vma->vm_end` 是分配給 user 的虛擬位址範圍，但**還沒有任何頁表項**。`remap_pfn_range(vma, addr, pfn, size, prot)`（`mm/memory.c`）做的事：從 `pfn` 起，一頁一頁在 user 的頁表裡填好「這段 user VA → 這些實體頁」的 PTE，並標記 VMA 為 `VM_PFNMAP | VM_IO`——意思是「這裡的頁不是普通匿名/檔案頁，別對它做 rmap、別 swap、別 CoW」。填完之後 user 一 deref 指標就直接命中這些實體頁，中間沒有複製、沒有 page fault，這就是零複製的本質。

映射**裝置 MMIO**（BAR，Ch 40）而非一般 RAM 時，要用 `io_remap_pfn_range`（`include/linux/io.h`）。它和 `remap_pfn_range` 幾乎一樣，差別在會套上架構特定的 I/O page attribute（把該頁設成 device/uncached memory，避免 CPU 對 MMIO 做 cache 或亂序合併）。pfn 來自 BAR 的實體位址（`pci_resource_start(pdev, bar) >> PAGE_SHIFT`）。framebuffer 直接映 VRAM 給 user、DPDK 把網卡暫存器映進 userspace，走的都是 `io_remap_pfn_range` 這條。**規則簡單記**：映一般 RAM/DMA buffer 用 `remap_pfn_range`（coherent 用 `dma_mmap_coherent`），映裝置暫存器/MMIO 用 `io_remap_pfn_range`。

## 對比與取捨

| 方式 | 誰搬資料 | CPU 佔用 | 延遲/吞吐 | 適用 |
|---|---|---|---|---|
| PIO（MMIO 讀寫） | CPU | 高，全程卡住 | 小量資料延遲低、大量吞吐差 | 少量控制暫存器讀寫、慢速裝置 |
| DMA | 裝置 DMA 引擎 | 低，只發起+收尾 | 大量資料吞吐高，但有 setup 成本 | 網卡/磁碟/GPU 等高速批量傳輸 |

| DMA 記憶體種類 | API | 要手動 sync？ | 生命週期 | 典型用途 |
|---|---|---|---|---|
| coherent / consistent | `dma_alloc_coherent` | 否 | 長（常駐） | 描述符環、queue（Ch 44 RX/TX ring、NVMe queue）|
| streaming | `dma_map_single/sg` | 是（或 `dma_sync_*`）| 短（一次傳輸） | 封包/磁碟資料 buffer |

| 隔離方式 | 有 IOMMU | 沒 IOMMU |
|---|---|---|
| 裝置能碰的記憶體 | 只有被 map 的頁 | 任意實體位址（危險）|
| 32-bit 裝置存取高記憶體 | IOMMU 翻譯 | SWIOTLB bounce buffer 複製 |
| VM 直通 | 支援（VFIO）| 不安全，基本不做 |

## 踩雷集錦

1. **把 `virt_to_phys` / `__pa()` 的結果直接餵給裝置**。錯誤直覺：「實體位址不就是裝置要的位址嗎？」正確認識：裝置要的是 **DMA 位址（bus address）**，只有在沒 IOMMU、匯流排無偏移的平台上它才碰巧等於實體位址。永遠用 DMA API 回的 `dma_addr_t`（`dma_handle`）餵裝置，你的驅動才能在有 IOMMU 的機器與 ARM 平台上活下來。

2. **streaming DMA 的方向填錯**。`DMA_TO_DEVICE` 與 `DMA_FROM_DEVICE` 決定 cache 該 flush 還是 invalidate。在 x86（DMA 一致）上填錯不會出事，於是你以為對了；一搬到 ARM 就讀到髒資料、間歇性壞封包，而且極難重現。方向要照資料真實流向填，且 map 用什麼方向、unmap 就用什麼方向。

3. **map 之後 CPU 還去碰那塊 buffer**。streaming map 後，buffer 所有權在裝置手上，CPU 直接讀寫會踩到 cache 一致性——CPU 寫的可能被裝置蓋掉、CPU 讀的可能是舊值。要碰得先 `dma_sync_single_for_cpu` 借回來、碰完 `dma_sync_single_for_device` 還回去。

4. **忘了 `dma_mapping_error` 檢查**。`dma_map_single` 可能失敗（IOMMU 頁表滿、bounce buffer 用盡），回傳一個無效位址。不檢查就把它寫進裝置，裝置會 DMA 到亂七八糟的地方。map 完一定要 `if (dma_mapping_error(dev, handle))`。

5. **`dma_alloc_coherent` 拿去當高頻大量記憶體用**。它在需維護一致性的平台是 uncached、且從有限的一致池配，慢且量少。它是給描述符環那種「小、常駐、雙方常讀寫」的東西用的，不是拿來當一般 buffer 池。大量一次性資料用 streaming map 既有的 buffer。

6. **`remap_pfn_range` 映射 highmem 或非連續記憶體**。它要求目標實體記憶體連續且 pfn 有效。拿 `vmalloc`（實體不連續，Ch 6）配的 buffer 去 `remap_pfn_range` 會錯；那種要用 `vm_insert_page` 之類逐頁處理，或 fault handler（`vma->vm_ops->fault`）按需映射。

## 進階：再往深一層

- **SWIOTLB（bounce buffer）**：`kernel/dma/swiotlb.c`。當裝置搆不到某塊高記憶體、又沒 IOMMU 幫忙時，kernel 在低位址預留一塊「彈跳緩衝」，DMA 先進 bounce buffer，再由 CPU 複製到真正目的地（或反向）。這是相容性後盾，但多一次複製、傷效能。`dmesg | grep -i swiotlb` 看它有沒有被啟用。這解釋了為什麼 `dma_set_mask` 的正確宣告很重要——宣告太保守（明明 64-bit 卻報 32-bit）會逼 kernel 無謂地走 bounce buffer。

- **VFIO（Virtual Function I/O）**：`Documentation/driver-api/vfio.rst`。把整個裝置從 host 驅動手上搶過來、透過 IOMMU 安全地交給 userspace 或虛擬機直接驅動。這是兩個世界的地基：一邊是 **DPDK / SPDK** 這類 userspace 高效能驅動（繞過 kernel 網路/儲存堆疊，直接在 user space polling 裝置，見 networking 課的 kernel bypass），一邊是 **VM PCI passthrough**（把顯卡直通給 guest）。VFIO 能安全，全靠 IOMMU 把 userspace/guest 的 DMA 關在它的記憶體範圍內。這也接 bpf 課談虛擬化與 XDP 的 zero-copy。

- **`dma-ranges` 與匯流排偏移**：某些 SoC（尤其 ARM，見 arm 課與 device tree Ch 39）的匯流排上，裝置看到的位址和 CPU 實體位址差一個固定偏移。這在 device tree 用 `dma-ranges` 描述，DMA API 讀它來正確換算。這是「DMA 位址 ≠ 實體位址」在沒 IOMMU 平台上的另一種成因。

- **面試常問**：「PIO 和 DMA 差別？」「coherent 和 streaming DMA 各用在哪？為什麼描述符環用 coherent？」「為什麼裝置要用 DMA 位址而不是實體位址？」「IOMMU 解決什麼問題？（隔離 + 32-bit 存取高記憶體 + 虛擬化直通）」「一個網卡收包從中斷到 bottom half 的完整流程？」——這五題把本章串起來，是驅動/韌體職缺的高頻題。

## 動手練習

1. **看兩個位址**：跑上面的模組，記下 `phys` 與 `dma_handle`。在沒 IOMMU 的 QEMU 上它們應相等。用 `qemu-system-x86_64 ... -machine q35 -device intel-iommu`、guest `-append "... intel_iommu=on"` 重開，再看一次——`dma_handle` 應該變成不同的 IOVA。寫下你的觀察並解釋為什麼。

2. **user↔kernel 雙向共享**：擴充 user 程式，在 mmap 的 buffer 裡寫一段字串（`sprintf(p, ...)`）；改模組加一個 `rmmod` 時（或加個 debugfs 檔）印出 buffer 內容，確認 kernel 讀到 user 寫的東西。這證明 `remap_pfn_range` 建立的是真正共享的同一塊實體記憶體（零複製）。

3. **看 IOMMU groups**：`ls /sys/kernel/iommu_groups/`、`find /sys/kernel/iommu_groups/ -type l | head`，把你機器上的裝置對應到 group。想一想：為什麼有些 group 有多個裝置？（提示：無法彼此隔離的裝置被歸在同一組。）`dmesg | grep -e DMAR -e IOMMU` 看初始化。

4. **弄壞方向**（觀念題，x86 上不會壞給你看，但要能推理）：如果一個 ARM 驅動收包時把 `dma_map_single` 的方向寫成 `DMA_TO_DEVICE`（該用 `DMA_FROM_DEVICE`），會發生什麼？從 cache invalidate/flush 的角度解釋 CPU 為什麼會讀到舊資料。

5. **改用 `dma_mmap_coherent`**：把模組的 `demo_mmap` 從 `remap_pfn_range` 改成 `dma_mmap_coherent(&pdev->dev, vma, cpu_addr, dma_handle, BUF_SIZE)`，比較兩者。理解為什麼後者對 coherent 記憶體更正確（page attribute 對）。

## 本章重點整理

- **DMA 讓裝置自己讀寫 RAM，CPU 只發起與收尾**——這是所有高速裝置（網卡/磁碟/GPU）快的根本；PIO 讓 CPU 當搬運工，只適合少量控制暫存器。
- 讓裝置碰 RAM 帶來三個麻煩，DMA API 幫你解：**位址不同**（用 `dma_addr_t` 別用實體位址）、**cache 不同步**（coherent 免管、streaming 靠方向與 `dma_sync`）、**定址能力有限**（`dma_set_mask` + ZONE_DMA32）。
- **coherent DMA**（`dma_alloc_coherent`）配一致記憶體給常駐的描述符環；**streaming DMA**（`dma_map_single/sg`）把既有 buffer 一次性映射給裝置，用完 unmap，要顧方向。
- **IOMMU** 是裝置的 MMU：隔離保護（擋 DMA attack）、讓 32-bit 裝置搆到高記憶體、支撐 VFIO 直通；`/sys/kernel/iommu_groups/` 看分組。
- 完整裝置 I/O 模型 = **發起 DMA → 裝置搬 → 完成中斷（top half 只 ack+排程）→ bottom half（sync/unmap、送上層、refill）**，串起 Ch 29 中斷與 Ch 30 bottom half。
- `remap_pfn_range` / `io_remap_pfn_range`（coherent 用 `dma_mmap_coherent`）把裝置/DMA 記憶體直接映進 user 位址空間，達成零複製（framebuffer、DPDK、VFIO）。

## 自我檢核

- [ ] 不看筆記，能講清楚 PIO 與 DMA 的差別，以及為什麼 10 Gbps 網卡非用 DMA 不可
- [ ] 能解釋「裝置看到的位址為什麼不一定等於 CPU 實體位址」，並說出至少兩種成因（IOMMU、匯流排偏移）
- [ ] 能說出 coherent 與 streaming DMA 的差別，以及為什麼描述符環用前者、封包 buffer 用後者
- [ ] 面試被問「IOMMU 解決什麼問題」，能答出隔離保護、32-bit 存取高記憶體、虛擬化直通三點
- [ ] 能在腦中畫出「發起 → DMA → 完成中斷 → top/bottom half」的完整 I/O 流程，並指出哪一步對應 Ch 29、哪一步對應 Ch 30
- [ ] 能寫出用 `dma_alloc_coherent` 配 buffer、`remap_pfn_range`（或 `dma_mmap_coherent`）mmap 給 user 的模組

## 延伸閱讀

### 官方文件

- **[Documentation/core-api/dma-api.rst](https://www.kernel.org/doc/html/latest/core-api/dma-api.html)** 與 **[dma-api-howto.rst](https://www.kernel.org/doc/html/latest/core-api/dma-api-howto.html)**
  - **讀哪裡**：`dma-api-howto` 整篇先讀（它是給驅動作者的實務教學，coherent vs streaming、方向、mask 都在裡面），`dma-api` 當 API 參考手冊查
  - **和本章的關聯**：本章 DMA API 那幾節就是它的濃縮；寫真實驅動時這兩篇是權威依據

- **[Documentation/driver-api/dma-buf.rst](https://www.kernel.org/doc/html/latest/driver-api/dma-buf.html)** 與 **[vfio.rst](https://www.kernel.org/doc/html/latest/driver-api/vfio.html)**
  - **讀哪裡**：dma-buf 讀概念與 fence 部分、vfio 讀 group/container 模型
  - **能學到什麼**：跨裝置零複製共享（dma-buf）與 userspace/VM 直通（VFIO）如何建立在 DMA + IOMMU 之上，接本章進階節

### 書籍

- **《Linux Device Drivers, 3rd Ed.》(LDD3)** — Corbet, Rubini, Kroah-Hartman，第 15 章 Memory Mapping and DMA
  - **這章的定位**：`mmap`、`remap_pfn_range`、DMA API 的經典解說，本章動手部分的思路源頭
  - **注意**：書對應舊 kernel，`dma_alloc_coherent` 早期叫 `pci_alloc_consistent`、部分 API 已改名；概念全對，簽名以 v6.12 的 `include/linux/dma-mapping.h` 為準

- **《Understanding the Linux Kernel, 3rd Ed.》** — Bovet & Cesati，I/O 與裝置章節
  - **讀哪裡**：DMA 與 I/O 位址空間相關節
  - **為什麼值得讀**：把 DMA 放進 x86 記憶體/匯流排架構的脈絡講，補齊本章沒展開的硬體背景

### 交叉索引

- **[Bootlin Elixir — kernel/dma/](https://elixir.bootlin.com/linux/v6.12/source/kernel/dma)** 與 **[include/linux/dma-mapping.h](https://elixir.bootlin.com/linux/v6.12/source/include/linux/dma-mapping.h)**
  - **怎麼用**：`dma_alloc_coherent` 跳到 `mapping.c` 的 `dma_alloc_attrs`、順著看它怎麼分派到 `direct.c`（無 IOMMU 直配）、`swiotlb.c`（bounce）、或 IOMMU 後端。看清「同一個 API 在不同平台走不同路」是理解 DMA 抽象的關鍵

裝置怎麼高效搬資料講完了。下一章我們換個角度看裝置：不是它多快，而是它多省——CPU 怎麼在沒事做時降頻、休眠，裝置與系統的電源怎麼管。

→ [Ch 42 電源管理：cpuidle / cpufreq / runtime PM](./42-power-management.md)
