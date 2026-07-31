# Ch 39 — platform driver 與 device tree

> **目標**：搞懂 SoC 上那些「無法自我列舉」的裝置（UART、I2C 控制器、GPIO、時鐘…）到底 kernel 是怎麼知道它們存在的。學完你能讀懂一個 device tree node、寫一個 platform driver、用 `compatible` 字串讓它被 probe，並從 DT 取出暫存器位址與 IRQ。這是 ARM/embedded/MTK 韌體驅動開發的標準起手式。

## 為什麼需要這個？

Ch 37 我們建立了 bus/device/driver 三角：device 掛在 bus 上，driver 註冊到 bus 上，bus 的 `match` 函式負責把 device 配對給 driver，配對成功就呼叫 driver 的 `probe`。這個模型很漂亮，但它有一個沒回答的問題：**device 這個物件是誰、在什麼時候、根據什麼資訊建立出來的？**

對 PCI（Ch 40）來說這問題不存在。PCI 是**可自我列舉（self-enumerating / discoverable）**的匯流排：kernel 開機時掃 PCI config space，每個裝置自己會回報「我是 vendor 0x8086、device 0x1234、我要 256 KB 的 BAR 記憶體、我用 INTA 中斷」。硬體自報家門，kernel 照單建立 `pci_dev`。USB 也一樣，插進去會枚舉。

但 SoC 上的裝置不是這樣。一顆 MediaTek 或 SiFive 的 SoC 裡，UART 控制器就是**焊死在某個實體位址**的一塊暫存器（比如 `0x11002000`），它不掛在任何能枚舉的匯流排上——它直接掛在 CPU 的記憶體映射匯流排（memory-mapped bus）上。你去讀 `0x11002000` 這個位址，硬體上就是那個 UART 的暫存器；但**沒有任何機制讓 kernel「掃描」出這裡有一個 UART**。位址、中斷號、時鐘來源，全都是晶片設計時就定死的、寫在 datasheet 裡的資訊，硬體不會告訴你。

於是問題變成：kernel 怎麼知道這顆 SoC 上有哪些裝置、每個的暫存器在哪、用哪條中斷、吃哪個時鐘？歷史上有兩個答案：

- **舊做法：board file**。每一塊板子在 `arch/arm/mach-*/` 底下有一個 `.c` 檔，把所有裝置的位址、IRQ 用 C 結構**硬編**進去，編進 kernel。問題是：每出一塊新板子就要改 kernel 源碼、重編 kernel；ARM 生態板子成千上萬，這些 board file 一度膨脹到讓 Linus 公開發火（2011 年著名的 "ARM is a f\*\*\*ing pain" 事件），逼出了 device tree。
- **現代做法：device tree（DT）**。把「這塊板子有哪些硬體」這件事，從 C 程式碼裡抽出來，變成一份**獨立的資料檔**，開機時由 bootloader 交給 kernel。kernel 二進位不再綁死某塊板子——同一顆 kernel 配不同的 DT 就能跑不同的板子。

這一章講的就是：**platform bus**（給不能自我列舉的裝置用的偽匯流排）、**device tree**（描述硬體拓撲的資料結構），以及兩者怎麼合作把一個 SoC 裝置 probe 起來。

## 先建立直覺

先把整條「從一份文字檔到 driver 的 probe 被呼叫」的路徑畫出來。這是本章的骨幹，後面每一節都在填其中一段：

```
   ┌── 開發者 / SoC 廠 寫的 ─────────────────────────────────────────┐
   │  foo.dts   (device tree source，人可讀的文字)                   │
   │     uart0: serial@11002000 {                                    │
   │         compatible = "mediatek,mt6795-uart";                    │
   │         reg = <0x11002000 0x1000>;   // 暫存器起點+大小          │
   │         interrupts = <GIC_SPI 91 ...>;                          │
   │         clocks = <&clk26m>;                                     │
   │     };                                                          │
   └───────────────────────────────┬────────────────────────────────┘
                                    │  dtc（device tree compiler）
                                    ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  foo.dtb   (device tree blob，緊湊二進位，kernel 吃這個)          │
   └───────────────────────────────┬────────────────────────────────┘
                                    │  bootloader（U-Boot / UEFI）
                                    │  把 DTB 位址放進暫存器(x0/a1)交棒
                                    ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  kernel 開機：of_* 解析 DTB → 為每個 node 建一個 platform_device  │
   │                                    │                             │
   │      platform_device (name/reg/irq)│                             │
   │                                    ▼                             │
   │      platform bus 的 match：拿 device 的 compatible 字串         │
   │        去比對每個 platform_driver 的 of_match_table              │
   │                                    │  match!                     │
   │                                    ▼                             │
   │      呼叫 driver->probe(pdev)  ← 你的驅動從這裡開始跑            │
   │        platform_get_resource() 拿暫存器位址                      │
   │        platform_get_irq()      拿中斷號                          │
   │        devm_platform_ioremap_resource() 把實體位址映射成虛擬     │
   └─────────────────────────────────────────────────────────────────┘
```

三個關鍵物件要先分清楚：

- **platform_device**：代表一個「掛在 platform bus 上、不能自我列舉」的實體裝置。它攜帶這個裝置的**資源（resource）**——暫存器位址範圍、IRQ 號。在 DT 系統裡，它是 kernel 解析 DTB 時**自動幫你造出來的**。
- **platform_driver**：你寫的驅動。它宣告「我能驅動哪些裝置」（透過 `of_match_table` 裡的 `compatible` 字串），並提供 `probe`/`remove`。
- **platform bus**：一條**偽匯流排（pseudo bus）**。它不對應任何真實硬體匯流排，純粹是 Linux device model 的一個掛載點，讓「記憶體映射、不能枚舉」的裝置也能套用 Ch 37 的 bus/driver/match/probe 框架。

> 如果你上過本 repo 的 `linux_boot`，DTB 是怎麼被 bootloader 載入、放到哪、怎麼透過暫存器（ARM64 是 `x0`、RISC-V 是 `a1`）交給 kernel 的，那門課講過。這一章接手的是「kernel 拿到 DTB 之後怎麼用它」。

## platform bus：給不能枚舉的裝置一個家

platform bus 的本體在 `drivers/base/platform.c`。開機早期，`platform_bus_init()`（同檔）會註冊一條名為 `"platform"` 的 bus（`struct bus_type platform_bus_type`），並建立一個 root device `platform_bus`（sysfs 裡就是 `/sys/devices/platform/`）。之後所有 platform device 都掛在它底下。

這條 bus 的 `match` 函式是 `platform_match()`（`drivers/base/platform.c`）。它決定「一個 platform_device 跟一個 platform_driver 配不配」，比對順序值得記——**它按優先序試四種比對方式**：

1. **`driver_override`**：sysfs 裡人為強制指定的 driver 名，最高優先（除錯/特殊場景用）。
2. **OF（Open Firmware / device tree）比對**：`of_driver_match_device()` → 拿 device 的 `of_node` 的 `compatible` 字串，去比 driver 的 `of_match_table`。**這是 DT 系統的主線**。
3. **ACPI 比對**：`acpi_driver_match_device()`。x86/server 走這條（見後面 ACPI 對比一節）。
4. **名字比對**：直接比 `platform_device.name` 和 `platform_driver.driver.name`。這是最原始的、board file 時代的比對法，DT 系統裡通常用不到。

```c
// drivers/base/platform.c，platform_match() 的骨架（簡化）
static int platform_match(struct device *dev, const struct device_driver *drv)
{
    struct platform_device *pdev = to_platform_device(dev);
    struct platform_driver *pdrv = to_platform_driver(drv);

    if (pdev->driver_override)                       // (1) 強制指定
        return !strcmp(pdev->driver_override, drv->name);

    if (of_driver_match_device(dev, drv))            // (2) device tree 主線
        return 1;

    if (acpi_driver_match_device(dev, drv))          // (3) ACPI（x86）
        return 1;

    if (pdrv->id_table)                              // (4a) platform_device_id 表
        return platform_match_id(pdrv->id_table, pdev) != NULL;

    return (strcmp(pdev->name, drv->name) == 0);     // (4b) 純名字
}
```

一個 platform_driver 用 `platform_driver_register()`（或包裝過的 `module_platform_driver()` 巨集）註冊。註冊會觸發 bus 對所有已存在的 device 跑一輪 match；反過來，一個 device 被加入時也會對所有已註冊 driver 跑 match。match 成功，device model 核心（`really_probe()`，`drivers/base/dd.c`）就呼叫 driver 的 `probe`。**這套 match→probe 的機制在 Ch 37 講過，platform bus 只是它的一個具體實例**。

## platform driver 長什麼樣

一個最小但完整的 platform driver 骨架，把上面的抽象落地：

```c
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/of.h>
#include <linux/io.h>

struct myuart {
    void __iomem *base;   // ioremap 後的虛擬位址
    int irq;
};

static int myuart_probe(struct platform_device *pdev)
{
    struct myuart *up;
    struct resource *res;

    up = devm_kzalloc(&pdev->dev, sizeof(*up), GFP_KERNEL);
    if (!up)
        return -ENOMEM;

    // 從 DT 取暫存器範圍並 ioremap（見下一節）
    up->base = devm_platform_ioremap_resource(pdev, 0);
    if (IS_ERR(up->base))
        return PTR_ERR(up->base);

    // 從 DT 取中斷號
    up->irq = platform_get_irq(pdev, 0);
    if (up->irq < 0)
        return up->irq;

    platform_set_drvdata(pdev, up);   // 存起來，remove/中斷處理時取回
    dev_info(&pdev->dev, "probed: base=%p irq=%d\n", up->base, up->irq);
    return 0;
}

static void myuart_remove(struct platform_device *pdev)
{
    // devm_* 配置的資源會自動釋放，這裡通常只關硬體
    dev_info(&pdev->dev, "removed\n");
}

// 關鍵：這張表決定「這個 driver 能驅動哪些 DT node」
static const struct of_device_id myuart_of_match[] = {
    { .compatible = "myvendor,myuart-v1" },
    { /* sentinel，結尾一定要有一個空項 */ }
};
MODULE_DEVICE_TABLE(of, myuart_of_match);   // 讓 modpost 產生 modalias，支援自動載入

static struct platform_driver myuart_driver = {
    .probe  = myuart_probe,
    .remove = myuart_remove,
    .driver = {
        .name           = "myuart",
        .of_match_table = myuart_of_match,   // 掛上比對表
    },
};
module_platform_driver(myuart_driver);       // 展開成 module_init/exit 樣板

MODULE_LICENSE("GPL");
```

幾個一定要注意的點：

- **`of_match_table` 是靈魂**。match 時走的是 `platform_match()` 的第 2 條路徑，比對的是這張表裡的 `.compatible` 字串。表的最後**一定要有一個空的 sentinel 項**，否則 `of_match_device()` 掃描時會越界（這是新手最常見的當機原因之一）。
- **`.remove` 在 6.12 回傳 `void`**。歷史上 `platform_driver.remove` 回傳 `int`，但回傳值一直被 core 忽略（device 一定會被移除，driver 沒有拒絕的權力）。6.11 起把它改成 `void`（`remove` 而非舊的 `remove_new`）。你若看老程式碼回傳 `int`，那是舊 API。
- **`module_platform_driver(x)`** 是 `module_init(platform_driver_register)` + `module_exit(platform_driver_unregister)` 的樣板巨集，省掉重複的 init/exit。90% 的 platform driver 都用它。
- **`MODULE_DEVICE_TABLE(of, ...)`** 讓 build 時 `modpost` 把 compatible 字串寫進模組的 `modalias`，udev 才能在 DT node 出現時**自動 `modprobe`** 對應模組。少了它，driver 得手動 insmod 才會 match。

## device tree：用資料描述硬體

device tree 本體是一棵**樹**：root node（`/`）底下掛 node，node 底下可以再掛 node，每個 node 有一組 **property（屬性）**。它描述的是硬體的**拓撲**——什麼裝置掛在什麼匯流排上、在哪個位址、用什麼資源。

源碼是 `.dts`（device tree source，文字），用 **`dtc`（device tree compiler）** 編成 `.dtb`（device tree blob，緊湊二進位，也叫 FDT / flattened device tree）。kernel 開機時解析的是 `.dtb`。看一個典型 node：

```dts
/ {
    #address-cells = <2>;    // reg 裡「位址」用幾個 32-bit cell 表示
    #size-cells = <2>;       // reg 裡「大小」用幾個 32-bit cell 表示

    soc {
        compatible = "simple-bus";
        ranges;              // 子 node 的位址直接對應父層位址（無轉換）

        uart0: serial@11002000 {          // 「label: 名稱@單元位址」
            compatible = "mediatek,mt6795-uart",
                         "mediatek,mt6577-uart";   // 可列多個，由具體到通用
            reg = <0x0 0x11002000 0x0 0x1000>;     // 位址 0x11002000，大小 0x1000
            interrupts = <GIC_SPI 91 IRQ_TYPE_LEVEL_LOW>;
            clocks = <&uart_clk>, <&bus_clk>;
            clock-names = "baud", "bus";
            status = "okay";               // "okay" 啟用，"disabled" 停用
        };
    };
};
```

逐個看關鍵 property，它們各自對應 kernel 端一個取值 API：

| property | 意義 | 對應的 kernel 取值 |
|---|---|---|
| `compatible` | 「我這個硬體跟哪些 driver 相容」的字串清單，**由具體到通用** | 拿去比對 `of_device_id.compatible`（match 的核心） |
| `reg` | 暫存器（或記憶體）位址 + 大小，格式由父層 `#address-cells`/`#size-cells` 決定 | `platform_get_resource(IORESOURCE_MEM)` / `devm_platform_ioremap_resource()` |
| `interrupts` | 中斷描述（哪條線、觸發方式），格式由中斷控制器決定 | `platform_get_irq()` / `of_irq_get()` |
| `clocks` + `clock-names` | 這裝置吃哪些時鐘（指向 clock provider node） | `devm_clk_get()`（clock framework，Ch 42） |
| `status` | `"okay"` 才會被實體化成 platform_device；`"disabled"` 直接跳過 | kernel 掃 DT 時檢查 |

`compatible` 為什麼要**由具體到通用列多個**？因為它給 driver 演進留餘地。上面 MT6795 的 UART 硬體其實跟更早的 MT6577 相容，所以列兩個字串。如果沒人寫專門的 `mt6795-uart` driver，kernel 會退而用 `mt6577-uart` 的 driver（它也認得後面那個字串），照樣能跑。這是 DT 的重要慣例：**先試最具體的，退回較通用的**。

`reg` 裡那串數字為什麼要看 `#address-cells`/`#size-cells`？因為一個位址可能超過 32 bit（64-bit 系統很常見），要用兩個 32-bit cell 拼。上面父層宣告 `#address-cells = <2>`、`#size-cells = <2>`，所以 `reg = <0x0 0x11002000 0x0 0x1000>` 讀作：位址 = `(0x0 << 32) | 0x11002000`、大小 = `(0x0 << 32) | 0x1000`。搞錯 cell 數會把位址讀成一團亂碼——這是讀 DT 最常踩的坑。

## 底層機制：DTB 怎麼變成一串 probe

現在把「kernel 拿到 DTB 之後發生什麼」拆開。核心程式碼在 `drivers/of/`（OF = Open Firmware，device tree 的技術根源來自 Open Firmware 標準）。

**第一步：early 解析，攤平成 device_node 樹。** 開機極早期，`start_kernel()`（Ch 3）會呼叫 `setup_arch()`，裡面把 bootloader 傳來的 DTB 用 `unflatten_device_tree()`（`drivers/of/fdt.c`）解開，變成一棵由 `struct device_node`（`include/linux/of.h`）組成的、指標串起來的樹，掛在全域 `of_root` 底下。此時還只是**資料結構**，還沒有任何 `platform_device`。

```
   DTB (flat blob, 緊湊)                device_node 樹 (指標串起來)
   ┌──────────────┐                     of_root
   │ node headers │  unflatten_         │
   │ property blob│  device_tree()      ├── device_node "soc"
   │ string table │  ───────────►       │     ├── device_node "serial@11002000"
   └──────────────┘                     │     │     properties: compatible/reg/...
                                        │     └── device_node "i2c@11007000"
                                        └── device_node "memory@40000000"
```

**第二步：把選定的 node 實體化成 platform_device。** 開機稍晚，`of_platform_default_populate_init()`（`drivers/of/platform.c`，是個 `arch_initcall_sync`）會呼叫 `of_platform_populate()`，遞迴走 device_node 樹，對每個「看起來像裝置」的 node 呼叫 `of_platform_device_create()`（同檔），做三件事：

1. 建一個 `struct platform_device`，把 node 的指標存進 `pdev->dev.of_node`；
2. 用 `of_device_alloc()` 把 node 的 `reg` 解析成 `struct resource`（`IORESOURCE_MEM`），把 `interrupts` 解析成 `IORESOURCE_IRQ`，填進 `pdev->resource[]`；
3. `platform_device_add()` 把它掛上 platform bus。

不是每個 node 都會被實體化。`of_platform_populate()` 只對特定 node 遞迴——例如 `compatible = "simple-bus"` 的匯流排 node 會往下走，`status = "disabled"` 的 node 直接跳過。這就是為什麼 `status` property 這麼關鍵：把它設成 `"disabled"`，這個裝置根本不會變成 platform_device，等於在 DT 層級關掉它。

**第三步：掛上 bus 觸發 match。** `platform_device_add()` 最終走到 device model 核心 `bus_add_device()` → `bus_probe_device()`（`drivers/base/`），對 platform bus 上所有已註冊 driver 跑 `platform_match()`。走到第 2 條路徑（OF 比對）時，`of_match_device()`（`drivers/of/device.c`）拿 `pdev->dev.of_node` 的 `compatible` 字串，逐一比對 driver `of_match_table` 裡的每個 `of_device_id.compatible`。

**第四步：match 成功，probe 被呼叫。** `really_probe()`（`drivers/base/dd.c`）呼叫 `platform_drv_probe()`（`drivers/base/platform.c`）→ 你的 `myuart_probe(pdev)`。到這裡，你的驅動終於開始跑，`pdev` 手上帶著從 DT 解析好的 resource。

整條路徑一圖收束：

```
  bootloader 傳 DTB
        │
        ▼  setup_arch() → unflatten_device_tree()
  device_node 樹 (of_root)
        │
        ▼  of_platform_default_populate_init() (arch_initcall_sync)
  of_platform_populate() 遞迴走樹
        │  對每個 okay 的裝置 node：
        ▼  of_platform_device_create()
  platform_device 建立 (reg→resource MEM, interrupts→resource IRQ)
        │
        ▼  platform_device_add() → bus_probe_device()
  platform_match():  device.compatible  ⟷  driver.of_match_table
        │  match!
        ▼  really_probe() → platform_drv_probe()
  你的 driver->probe(pdev)  ← 拿 resource、ioremap、註冊中斷
```

**這裡有個時序陷阱值得記**：driver 註冊（`module_platform_driver`）和 device 建立（`of_platform_populate`）誰先誰後不一定。若 driver 先註冊，device 後出現，match 在 device 加入時發生；若 device 先在（built-in driver、內建 DT），driver 後 insmod，match 在 driver 註冊時發生。**bus model 保證兩邊任一先到都會觸發 match**，所以你寫 driver 不用擔心順序。但若你的裝置依賴另一個還沒 probe 的裝置（比如 UART 要等 clock provider），`probe` 會回傳 `-EPROBE_DEFER`，core 會把它排進 deferred probe 佇列，等依賴就緒再重試——這是 SoC 驅動最常見的機制，後面踩雷會展開。

## 從 device tree 取資源

probe 拿到 `pdev` 後，實際幹活就是從 DT 把資源取出來、映射、註冊。逐個 API 過一遍。

**取暫存器並映射（最常用）。** 實體位址（如 `0x11002000`）在 kernel 裡不能直接解參考——kernel 跑在虛擬位址空間（Ch 16），必須先把這段實體位址 `ioremap` 成 kernel 虛擬位址。6.x 提供一步到位的包裝：

```c
up->base = devm_platform_ioremap_resource(pdev, 0);   // 索引 0 = 第一個 reg
if (IS_ERR(up->base))
    return PTR_ERR(up->base);
```

`devm_platform_ioremap_resource()`（`drivers/base/platform.c`）內部做三件事：`platform_get_resource(pdev, IORESOURCE_MEM, 0)` 取第一個 MEM 資源 → `devm_ioremap_resource()` 先 `request_mem_region()` 佔用這段位址（防止兩個 driver 搶同一塊）再 `ioremap()` → 回傳虛擬位址。之後你就能 `readl(up->base + REG_OFFSET)` / `writel(val, up->base + REG_OFFSET)` 存取暫存器（MMIO，Ch 41 會深入）。

`devm_` 前綴（managed device resource）很重要：它把資源的生命週期綁在 device 上，device 被移除時**自動反向釋放**，你不必在 `remove` 裡手動 `iounmap`/`release`。這砍掉了 error handling 路徑上大量重複的清理程式碼。

**取中斷號。**

```c
up->irq = platform_get_irq(pdev, 0);   // 索引 0 = 第一條 interrupts
if (up->irq < 0)
    return up->irq;                     // 可能是 -EPROBE_DEFER（中斷控制器還沒好）
```

`platform_get_irq()`（`drivers/base/platform.c`）在 DT 系統裡最終走到 `of_irq_get()`（`drivers/of/irq.c`），它解析 node 的 `interrupts`（或 `interrupts-extended`）property，把「哪條中斷線 + 觸發類型」透過 irq domain 對應成 Linux 全域的 **virtual IRQ 號**。注意這個 virq 號跟 DT 裡寫的硬體中斷號（如 91）**不是同一個數字**——中間隔了一層 irq domain 映射（Ch 29 講中斷控制器）。拿到 virq 後才能 `devm_request_irq(&pdev->dev, up->irq, handler, ...)` 註冊處理函式（Ch 41）。

**直接讀 DT property（driver 專屬的自訂參數）。** 有些配置不是標準 resource，是這個裝置特有的參數，直接從 node 讀：

```c
u32 baud;
if (of_property_read_u32(pdev->dev.of_node, "clock-frequency", &baud) == 0)
    /* DT 裡有寫就用 DT 的值 */ ;

// 讀布林（property 存不存在即真假）
bool has_dma = of_property_read_bool(pdev->dev.of_node, "myvendor,use-dma");

// 讀字串
const char *label;
of_property_read_string(pdev->dev.of_node, "label", &label);
```

`of_property_read_u32()` / `of_property_read_string()` / `of_get_property()` 這一組都在 `drivers/of/property.c` 與 `drivers/of/base.c`。慣例：**自訂 property 要加 vendor 前綴**（`myvendor,use-dma`），標準 property（`reg`/`interrupts`/`clocks`/`clock-frequency`）不加。這避免不同廠商的私有屬性撞名。

**時鐘、電源、pinmux（SoC 驅動的日常）。** SoC 裝置光有暫存器位址還不能動——多半還要**開時鐘、開電、設定 pin mux**，否則讀暫存器可能整個 hang 住（時鐘沒開，匯流排存取卡死）。這三件事各有一套 framework，probe 裡常見這樣：

```c
struct clk *clk = devm_clk_get(&pdev->dev, "baud");  // clock framework
clk_prepare_enable(clk);                              // 開時鐘

struct regulator *reg = devm_regulator_get(&pdev->dev, "vdd"); // regulator framework
regulator_enable(reg);                               // 開電

// pinctrl：把這組 pin 設成 UART 功能（而非 GPIO）
// 多半由 core 在 probe 前依 DT 的 pinctrl-0 自動套用，driver 不必手動呼叫
```

- **clock framework**（`drivers/clk/`）：DT 用 `clocks = <&clk_provider N>` 指向時鐘來源，driver 用 `devm_clk_get()` 拿到、`clk_prepare_enable()` 開啟。
- **regulator framework**（`drivers/regulator/`）：管電源軌，DT 用 `xxx-supply = <&regulator>`。
- **pinctrl framework**（`drivers/pinctrl/`）：管 pin multiplexing——同一根實體接腳可能能當 UART、也能當 GPIO、也能當 I2C，pinmux 決定它現在是哪個。DT 用 `pinctrl-0`/`pinctrl-names`，多數情況 core 會在 probe 前自動套用預設 pin 狀態。

這三個 framework 每一個都能單獨寫一章，這裡只點出它們在 probe 裡的位置——**SoC 驅動 90% 的「為什麼硬體不動」都出在時鐘沒開或 pinmux 沒對**。細節在 Ch 42（電源管理，含 clock/PM）。

## 動手：在 QEMU 上看 device tree、寫 platform driver

QEMU 的 `virt` machine（ARM64 / RISC-V）會**自己產生一份 device tree** 餵給 guest kernel，這給了我們一個現成的實驗場。這一節全程用 ARM64 `virt`（RISC-V 幾乎一樣，把 `aarch64` 換 `riscv64`、`console=ttyS0` 換 `console=ttySIF0`）。

**Step 1：把 QEMU 產生的 DTB 抓出來反編譯。** QEMU 可以把它要餵給 kernel 的 DTB dump 成檔：

```bash
qemu-system-aarch64 -machine virt,dumpdtb=virt.dtb -cpu cortex-a57 -nographic
# 產生 virt.dtb 後 QEMU 直接退出

# 用 dtc 反編譯回可讀的 .dts
dtc -I dtb -O dts virt.dtb -o virt.dts
less virt.dts
```

你會看到 QEMU 幫你造的完整硬體描述：`pl011@9000000`（UART）、`virtio_mmio@...`、`intc@...`（GIC 中斷控制器）、`memory@40000000` 等。找到 UART node：

```dts
pl011@9000000 {
    clock-names = "uartclk", "apb_pclk";
    clocks = <0x8000 0x8000>;
    interrupts = <0x00 0x01 0x04>;
    reg = <0x00 0x9000000 0x00 0x1000>;
    compatible = "arm,pl011", "arm,primecell";
};
```

`reg` 說 UART 暫存器在 `0x9000000`、`compatible` 是 `arm,pl011`——kernel 裡 `drivers/tty/serial/amba-pl011.c` 那個 driver 的 `of_match_table` 就認得這字串，這是你 QEMU console 能出字的原因。

**Step 2：在 guest 裡從 `/proc/device-tree` 反看。** kernel 把解析後的 DT 以檔案系統形式暴露在 `/proc/device-tree`（等同 `/sys/firmware/devicetree/base`）。開機進 shell 後：

```
/ # ls /proc/device-tree/
#address-cells  #size-cells  chosen  cpus  memory@40000000  pl011@9000000  ...
/ # ls /proc/device-tree/pl011@9000000/
compatible  reg  interrupts  clocks  clock-names  ...
/ # cat /proc/device-tree/pl011@9000000/compatible
arm,pl011arm,primecell        # 字串以 \0 分隔，cat 會黏在一起
/ # hexdump -C /proc/device-tree/pl011@9000000/reg
# 看到 09 00 00 00 這段就是 0x9000000（big-endian，DT 一律大端）
```

**每個 property 是一個檔、每個 node 是一個目錄**——這是把 DT 樹直接映射成檔案系統，最直觀的 DT 檢視法。注意 DT 內部一律 **big-endian**，所以 `hexdump` 看到的位元組序是大端。

**Step 3：寫一個 platform driver 綁一個真實 node。** 最省事的實驗：寫個 driver，`compatible` 填 `"arm,pl011"` 以外某個 QEMU DT 裡存在、但沒被別的 driver 認領的 node（例如某個 `virtio_mmio` 若你關掉對應 config），probe 就會被呼叫。但更乾淨的做法是**自己加一個 DT node** 再綁 driver。

用 QEMU 的話，改 DT 最簡單的方式是把 Step 1 的 `virt.dts` 加一個 node，重新編成 dtb，用 `-dtb` 餵回去（覆蓋 QEMU 內建的）：

```dts
// 在 virt.dts 的 root node 底下加：
    mydev@0 {
        compatible = "myvendor,myuart-v1";
        reg = <0x0 0x0 0x0 0x0>;   // 假位址，這實驗不真的存取硬體
        status = "okay";
    };
```

```bash
dtc -I dts -O dtb virt.dts -o virt-mod.dtb
qemu-system-aarch64 -machine virt -cpu cortex-a57 -nographic \
    -kernel Image -initrd initramfs.cpio.gz \
    -append "console=ttyAMA0" \
    -dtb virt-mod.dtb
```

配上前面「platform driver 長什麼樣」那段的 `myuart` driver（把 `devm_platform_ioremap_resource` 那段先拿掉或加 `NULL` 檢查，因為假位址），insmod 後：

```
/ # insmod myuart.ko
/ # dmesg | tail
[   12.3] myuart myvendor,myuart-v1: probed ...
```

看到 `probed` 就對了——**你的 driver 的 `probe` 被呼叫，證明 `compatible` 比對成功、DTB 被正確解析、platform_device 被造出來**。整條「DT node → platform_device → compatible match → probe」的鏈路，你親手跑通了一遍。

**Step 4（觀測）：用 gdb 停在 match 上。** 照 Ch 0 的 gdb 環境，`break platform_match`，`break of_platform_device_create`，`continue`，你能看到開機時 kernel 對每個 node 造 platform_device、對每個 device 跑 match 的完整過程。`break really_probe` 能停在 probe 被呼叫的前一刻，`print dev->of_node->name` 看是哪個裝置。

## 對比與取捨

| 方案 | 硬體描述放哪 | 換板子要重編 kernel？ | 誰在用 | 取捨 |
|---|---|---|---|---|
| **board file**（舊） | 硬編在 `arch/*/mach-*/*.c` | 要 | 已淘汰（少數老 ARM 平台殘留） | 簡單直接但不可擴展，每塊板子改 kernel |
| **device tree**（DT） | 獨立 `.dtb`，開機傳入 | 不用，換 DTB 即可 | ARM/ARM64/RISC-V/PowerPC 主流，**MTK/SiFive SoC 全用** | 一顆 kernel 跑多板；代價是要維護 DT、DT binding 文件 |
| **ACPI** | 韌體提供的 ACPI table（DSDT 等） | 不用 | x86/x86_64、部分 ARM64 server | 標準化程度高、OS 廠愛用；但抽象層厚、除錯難、不適合 deeply embedded |
| **PCI 枚舉**（Ch 40） | 硬體自報（config space） | 不用 | PCIe 裝置 | 硬體可探測，最省事，但只適用能枚舉的匯流排 |

**DT vs ACPI 是常被問的對比**。兩者都在回答「非枚舉硬體怎麼描述」，但哲學不同：DT 是**純資料**——只描述硬體長怎樣，行為邏輯全在 kernel driver 裡；ACPI 除了描述硬體，還內嵌**可執行的 bytecode（AML）**，韌體可以把一部分裝置控制邏輯（開電、按鈕事件）藏在 AML 裡由 kernel 的 ACPI interpreter 執行。DT 的好處是透明、可讀、易於 review；ACPI 的好處是 OS 不必知道板子細節（韌體全包了），適合 OS 廠不想碰硬體差異的伺服器生態。**embedded/SoC 世界（你的 MTK/SiFive 職涯線）幾乎清一色 DT**；x86 server 清一色 ACPI；ARM64 server 兩者都可能遇到。

## 踩雷集錦

1. **`of_match_table` 忘了 sentinel，開機當機**。`of_device_id[]` 陣列結尾**必須**有一個全零的空項 `{ }`。`of_match_device()` 靠 `.compatible[0] == '\0'` 判斷表結束；少了它會一直往後掃到非法記憶體，通常表現為 probe 時 oops。這是新手第一號殺手。

2. **compatible 字串一個字元對不上，driver 靜默不 probe，沒有任何錯誤**。DT 裡寫 `mediatek,mt6795-uart`，driver 表裡寫 `mediatek,mt6795_uart`（底線 vs 連字號），match 直接失敗，kernel 不會報錯——它只是「找不到 driver」而已。裝置就這麼默默不動。debug 第一步永遠是：`cat /proc/device-tree/<node>/compatible` 和 driver 源碼**一字一字比對**。

3. **`reg` 的 cell 數搞錯，位址讀成垃圾**。父 node 的 `#address-cells`/`#size-cells` 決定 `reg` 每個欄位吃幾個 cell。父層是 `<2>` 你卻按 `<1>` 理解，位址會整個錯位。QEMU virt 的 root 是 `#address-cells = <2>`，所以 `reg = <0x0 0x9000000 0x0 0x1000>` 是「位址 0x9000000、大小 0x1000」，不是四個獨立位址。

4. **`status = "disabled"` 的 node 你等它 probe，永遠等不到**。`status` 不是 `"okay"`（或缺省）的 node 根本不會被 `of_platform_populate()` 實體化成 platform_device，自然沒有 match、沒有 probe。DT 覆寫（overlay / board dts）常把某裝置設 disabled，你 debug 時先確認 `status`。

5. **`-EPROBE_DEFER` 不是錯誤，是「稍後再試」**。probe 裡 `devm_clk_get` / `platform_get_irq` 回傳 `-EPROBE_DEFER` 代表依賴（時鐘 provider、中斷控制器）還沒 probe 好。你**必須把這個回傳值原封不動 `return` 出去**，core 才會把你排進 deferred 佇列稍後重試。若你把它當一般錯誤吞掉或印 error，裝置就永遠起不來。看 `/sys/kernel/debug/devices_deferred` 能列出所有卡在 defer 的裝置——SoC 開機驅動不起來，這個檔案是第一站。

## 進階：再往深一層

- **DT overlay：執行期改 DT**。整棵 DT 通常開機時定死，但 overlay 機制（`drivers/of/overlay.c`）允許執行期疊加一段 DT 片段，動態增刪 node。用於可插拔的擴充板（樹莓派 HAT、cape）——插上板子載入對應 overlay，platform_device 就冒出來、driver 就 probe。configfs 介面在 `/sys/kernel/config/device-tree/overlays/`。

- **DT binding 文件與 schema 驗證**。每個 `compatible` 該有哪些 property、型別、必填與否，都寫在 `Documentation/devicetree/bindings/` 底下的 **YAML schema**（6.x 已從舊的 `.txt` 遷到 `.yaml`）。`make dt_binding_check` / `make dtbs_check` 會用 `dt-schema` 工具驗證你的 `.dts` 符不符合 binding。寫新 driver 時，binding 文件跟 driver 是**一起送審**的——upstream 不收沒有 binding 的 driver。

- **`fw_devlink`：把 DT 的依賴關係變成 probe 順序**。6.x 的 `fw_devlink`（`drivers/base/core.c`）會**預先解析** DT 裡的 `clocks`/`interrupts`/`*-supply` 等指向關係，建立 device link，讓 supplier 一定先於 consumer probe，大幅減少 `-EPROBE_DEFER` 的重試次數。這是近年 probe 排序從「靠 defer 硬試」進化到「靠依賴圖排序」的關鍵改進，理解它能解釋很多開機順序問題。

- **面試常問**：「一個 UART 從 DT 到 driver probe，經過哪些步驟？」——能把本章那張總圖背出來（bootloader 傳 DTB → unflatten → of_platform_populate → platform_device → compatible match → probe → ioremap/get_irq）就是滿分答案。「DT 和 ACPI 差在哪？」「compatible 為什麼列多個？」「-EPROBE_DEFER 是什麼、怎麼處理？」也是高頻題。

## MTK 實例連結

MediaTek 的 SoC 全部走 device tree，DT 源碼在 `arch/arm64/boot/dts/mediatek/`（各代 SoC 一個 `.dtsi`，各板子一個 `.dts` include 它）。以 UART 為例，MTK 的 driver 是 `drivers/tty/serial/8250/8250_mtk.c`，它的比對表長這樣（概念）：

```c
static const struct of_device_id mtk8250_of_match[] = {
    { .compatible = "mediatek,mt6577-uart" },
    { /* sentinel */ }
};
```

而 SoC `.dtsi`（如 `mt8195.dtsi`）裡對應的 node：

```dts
uart0: serial@11002000 {
    compatible = "mediatek,mt8195-uart", "mediatek,mt6577-uart";
    reg = <0 0x11002000 0 0x1000>;
    interrupts = <GIC_SPI 91 IRQ_TYPE_LEVEL_LOW>;
    clocks = <&clk26m>, <&infracfg_ao CLK_INFRA_AO_UART0>;
    clock-names = "baud", "bus";
    status = "disabled";     // .dtsi 裡預設關，具體板子的 .dts 再打開
};
```

注意幾個 MTK 慣例，都跟本章對得上：**新 SoC 的 compatible 列自己再列相容的舊型號**（`mt8195-uart` 退回 `mt6577-uart`）；**`.dtsi` 裡 `status = "disabled"`，各板子的 `.dts` 用 `&uart0 { status = "okay"; }` 選擇性打開**（同一顆 SoC 上 UART 有很多路，板子只用到幾路）；**`clocks` 指向 MTK 的 clock controller node**（`infracfg_ao`），這是 MTK clock framework（Ch 42）的入口。你日後在 MTK 改板子、上新周邊，90% 的工作就是：查 datasheet 拿位址/中斷/時鐘 → 寫 DT node → 確認有對應 driver 的 compatible → 開 `status` → 看 dmesg 有沒有 probe。

## 動手練習

1. **反編譯 + 找 driver**：用 `qemu-system-aarch64 -machine virt,dumpdtb=virt.dtb` 抓 DTB，`dtc` 反編譯，挑三個 node（`pl011`、`intc`、任一 `virtio_mmio`），對每個的 `compatible` 字串，去 kernel 源碼 grep 出對應的 driver 檔案（`grep -rl 'arm,pl011' drivers/`）。確認「DT compatible ⟷ driver of_match_table」這條對應鏈。

2. **寫並 probe 你的 platform driver**：照本章 Step 3，加一個 `myvendor,myuart-v1` 的 DT node，寫對應 platform driver，在 QEMU guest insmod，dmesg 看到 `probe` 被呼叫。然後**故意把 driver 的 compatible 改成不匹配的字串**，重新 insmod，確認 probe **不**被呼叫（且沒有任何錯誤訊息）——親身體會踩雷 #2。

3. **gdb 追 populate**：照 Ch 0 環境，`break of_platform_device_create`，`continue`，每次停下 `print np->full_name` 看 kernel 正在為哪個 node 造 platform_device。數一數開機時總共造了幾個。

4. **弄壞 sentinel**：把你 driver 的 `of_match_table` 結尾那個空項 `{ }` 刪掉，重編、insmod，觀察會不會 oops（在 QEMU 裡崩了不心疼）。這讓你記住踩雷 #1 為什麼致命。

5. **玩 status**：把你的 DT node 改成 `status = "disabled"`，重編 dtb 開機，確認 driver 的 probe **完全不被呼叫**（連 device 都沒被造出來）。用 `ls /sys/devices/platform/` 對照有沒有你的裝置。

## 本章重點整理

- SoC 裝置**無法自我列舉**（不像 PCI），kernel 必須靠外部描述才知道有哪些硬體、暫存器在哪、用哪條中斷——現代答案是 **device tree**（舊答案是硬編的 board file）。
- **platform bus** 是給非枚舉裝置的偽匯流排；kernel 解析 DTB 時自動造出 **platform_device**（帶 reg/irq 資源），用 **compatible 字串**比對 platform_driver 的 **of_match_table**，match 就呼叫 **probe**。
- probe 裡從 DT 取資源的三招：`devm_platform_ioremap_resource()`（暫存器實體位址 → ioremap 成虛擬位址）、`platform_get_irq()`（→ virq）、`of_property_read_u32/string`（自訂參數）；SoC 驅動還常要 clk/regulator/pinctrl 三件套。
- 核心程式碼：`drivers/base/platform.c`（platform bus/device/driver、`platform_match`、`devm_platform_ioremap_resource`）、`drivers/of/`（`unflatten_device_tree`、`of_platform_populate`、`of_platform_device_create`、`of_match_device`、`of_irq_get`）。

## 自我檢核

- [ ] 不看筆記，能講出「為什麼 PCI 不需要 device tree 而 SoC 的 UART 需要」
- [ ] 能默寫一條「bootloader 傳 DTB → … → driver probe」的完整鏈路（至少六步）
- [ ] 能寫出一個最小 platform driver 的骨架，並說出 `of_match_table` 和 sentinel 各自的作用
- [ ] 能說出 `devm_platform_ioremap_resource` 在做什麼、為什麼實體位址不能直接用
- [ ] 面試被問「DT 和 ACPI 差在哪」「-EPROBE_DEFER 是什麼、怎麼處理」，能各答出重點
- [ ] 能在 `/proc/device-tree` 找到一個裝置的 compatible 與 reg，並解讀 reg 的 cell 格式

## 延伸閱讀

### 官方文件

- **[Documentation/devicetree/usage-model.rst](https://www.kernel.org/doc/html/latest/devicetree/usage-model.html)**
  - **讀哪裡**：整篇。這是 kernel 官方對「DT 怎麼從 blob 走到 driver probe」最權威的說明，本章底層機制那節就是它的濃縮
  - **和本章的關聯**：`of_platform_populate` 的角色、platform_device 怎麼從 node 生出來，讀這篇補全細節

- **[Documentation/devicetree/bindings/](https://www.kernel.org/doc/html/latest/devicetree/bindings/index.html)**
  - **讀哪裡**：先看 `writing-schema.rst`，再挑一個你熟的裝置（如 serial）的 YAML binding
  - **能學到什麼**：一個 compatible 該有哪些 property、怎麼寫 schema、`make dtbs_check` 怎麼驗證——寫真實 driver 必經

### 規格與工具

- **[Devicetree Specification](https://www.devicetree.org/specifications/)** — devicetree.org
  - **這是什麼**：DT 的正式規格書（node/property/cell 格式、標準 property 定義的權威來源）
  - **讀哪裡**：Chapter 2（Devicetree 基礎概念）、Chapter 3（標準 property）。`#address-cells`/`reg` 的精確語意在這裡

- **[Bootlin: Understanding the Linux kernel device model](https://bootlin.com/doc/training/)** — Bootlin 訓練教材
  - **為什麼值得讀**：Bootlin 是 embedded Linux 領域最紮實的訓練機構，投影片把 platform driver + DT 講得極清楚，配大量真實 driver 範例
  - **前提**：跟完本章、有一顆能跑的 QEMU

### 進階

- **[LWN: fw_devlink 相關系列](https://lwn.net/)** — 在 LWN 搜 "fw_devlink" / "deferred probe"
  - **讀什麼**：deferred probe 與 fw_devlink 怎麼解決 SoC 上錯綜的 probe 依賴排序
  - **和本章的關聯**：把踩雷 #5（`-EPROBE_DEFER`）從「知道要 return 它」提升到「理解 kernel 怎麼從硬試進化到依賴圖排序」

你現在會處理「不能自我列舉」的裝置了。下一章換另一極端：PCI/PCIe 這種**能自我列舉**的匯流排——kernel 怎麼掃 config space、怎麼分配 BAR、怎麼把 `pci_dev` 配對給 driver。你會發現 match→probe 的骨架跟本章一模一樣，只是 device 的來源從「解析 DT」換成「掃描硬體」。

→ [Ch 40 PCI/PCIe 列舉與設定空間](./40-pci-pcie.md)
