# Ch 30 — ARM 與其他架構的開機差異

> **目標**：理解 ARM 開機和 x86 的根本差異——沒有 BIOS/real mode 的赤裸啟動、Boot ROM、U-Boot、device tree（取代 x86 的硬體自描述）、ARM 的 UEFI（SystemReady），以及 coreboot 等開源韌體，把全課的 x86 知識放進更廣的架構脈絡。

> **環境**：概念為主，涉及 ARM（樹莓派等）、device tree。承接全課的 x86 開機知識。

## 為什麼要看 ARM？

本課 29 章都在講 x86（BIOS/UEFI）。但 ARM 是另一個巨大的世界——手機、平板、樹莓派、伺服器（AWS Graviton）、Apple Silicon 都是 ARM。ARM 的開機和 x86 有根本差異，理解這些差異能讓你的開機知識完整，並看出「x86 的某些東西是歷史包袱還是必要設計」。

```
為什麼 ARM 開機和 x86 不同：
  x86：背負四十年相容包袱（real mode、A20、BIOS...）
        每顆 CPU 上電當 8086（Ch 2）
        
  ARM：沒有這個包袱（相對年輕、多樣化）
        上電直接是原生模式，沒有 real mode 那套
        但有自己的問題（硬體極度多樣、無標準韌體）
        │
  對比兩者，能看清「哪些 x86 設計是歷史債、哪些是真需求」
```

## 先建立直覺：ARM 沒有「統一的 PC 韌體」

```
x86 的世界（相對統一）：
  幾乎所有 x86 PC 都有 BIOS/UEFI（標準韌體）
  硬體能自我描述（PCI 枚舉、ACPI、e820...）
  → 一個 kernel 能在大多數 x86 PC 開機

ARM 的世界（極度多樣）：
  ARM 是 IP core，各廠商（高通、聯發科、樹莓派...）做自己的 SoC
  每個 SoC 的記憶體佈局、周邊、時鐘都不同
  沒有統一的「ARM BIOS」
  硬體不能自我枚舉（沒有 PCI 那種標準枚舉）
  → 需要「device tree」告訴 kernel 硬體長什麼樣
        │
  ARM 開機的核心挑戰：硬體多樣 + 無標準韌體 + 硬體不自描述
```

這是 ARM 和 x86 最根本的差異：x86 有統一的韌體和自描述硬體；ARM 硬體極度多樣、無統一韌體、硬體不自描述。這催生了 ARM 開機的不同設計（device tree、U-Boot）。

## ARM 的開機流程

```
ARM 的典型開機流程（以 SoC 為例）：

  上電
        │
  Boot ROM（SoC 內建的唯讀韌體，廠商燒死的）
    - 極小，初始化最基本的東西
    - 從預定位置（eMMC/SD/SPI flash）載入下一階段
        │
  第一階段 bootloader（SPL / 廠商的）
    - 初始化 DRAM（記憶體訓練）
    - 載入主 bootloader
        │
  U-Boot（主 bootloader，Ch 17）
    - 初始化更多硬體
    - 載入 kernel + device tree + initramfs
    - 把 device tree 傳給 kernel
        │
  kernel（用 device tree 知道硬體長怎樣）
        │
  → initramfs → init（這之後和 x86 類似）
```

對比 x86：x86 是「BIOS/UEFI（統一韌體）→ bootloader → kernel」；ARM 是「Boot ROM（廠商燒死）→ SPL → U-Boot → kernel」，且多了「初始化 DRAM、傳 device tree」這些 x86 韌體幫你做的事。

## Device Tree：取代 x86 的硬體自描述

ARM 最關鍵的概念是 **device tree**——一個描述硬體的資料結構，告訴 kernel「這個板子有什麼硬體、在哪」：

```
為什麼 ARM 需要 device tree：
  x86：硬體能自我描述
    - PCI 匯流排能枚舉（問每個裝置「你是誰」）
    - ACPI 表描述系統
    - e820 描述記憶體（Ch 3）
    → kernel 能「探索」硬體

  ARM（嵌入式）：硬體不能自我描述
    - 周邊直接接在 SoC 的記憶體位址上（memory-mapped）
    - 沒有枚舉機制——kernel 怎麼知道 UART 在哪個位址？時鐘多少？
    → 需要有人「告訴」kernel 硬體佈局
        │
  device tree = 描述硬體的資料結構
    "UART0 在位址 0x10000000，中斷號 5，時鐘 24MHz..."
    "記憶體從 0x40000000 開始，512MB..."
        │
  bootloader 把 device tree 傳給 kernel，kernel 據此驅動硬體
```

device tree 的格式（`.dts` 原始碼，編譯成 `.dtb`）：

```
// device tree source（.dts）片段（概念）
/ {
    memory@40000000 {
        device_type = "memory";
        reg = <0x40000000 0x20000000>;  // 記憶體：起始 0x40000000，512MB
    };

    uart0: serial@10000000 {
        compatible = "arm,pl011";        // 哪種 UART（kernel 據此選驅動）
        reg = <0x10000000 0x1000>;       // UART 暫存器位址
        interrupts = <5>;                // 中斷號
        clock-frequency = <24000000>;    // 時鐘 24MHz
    };
    // ... 描述板子上每個硬體 ...
};
```

```bash
# 看你的 ARM 系統的 device tree（如樹莓派）
ls /sys/firmware/devicetree/base/      # device tree 暴露在這
cat /proc/device-tree/model            # 板子型號
# 或看 .dtb 檔
ls /boot/*.dtb
dtc -I dtb -O dts /boot/bcm2711-rpi-4-b.dtb   # 反編譯 .dtb 看內容
```

> device tree 是 ARM 開機最核心的概念，也是 x86 沒有的。它解決「硬體不能自描述」的問題——用一個資料結構明確告訴 kernel 硬體佈局。一個 kernel 配不同的 device tree，能在不同的 ARM 板子上跑（kernel 通用，device tree 描述特定板子）。這就是為什麼 ARM Linux 的 `/boot` 有一堆 `.dtb`（每個板子一個）。理解 device tree，你會懂 ARM 開機和 x86 的根本不同。

## ARM 的 UEFI：SystemReady

伺服器級 ARM（AWS Graviton、Ampere）和部分 ARM 裝置走 **UEFI**（不是 U-Boot + device tree）：

```
ARM 的兩條路線：
  嵌入式路線（樹莓派、開發板、手機）：
    Boot ROM → U-Boot → device tree → kernel
    硬體多樣，device tree 描述
    
  伺服器/標準化路線（ARM SystemReady）：
    ARM 推的標準：讓 ARM 也有 UEFI + ACPI（像 x86）
    UEFI 韌體 → bootloader（GRUB）→ kernel
    硬體用 ACPI 描述（或 device tree），標準化
        │
  → SystemReady 讓 ARM 伺服器能像 x86 一樣「一個 OS image 到處跑」
    （不用為每個板子客製 device tree + bootloader）
```

> ARM 的碎片化（每個板子不同）對伺服器是災難——你不能為每個 ARM 伺服器客製 OS。**ARM SystemReady** 是標準化運動：讓 ARM 也用 UEFI + ACPI（像 x86），硬體標準化描述，一個 OS image 能在符合標準的 ARM 機器上開機。AWS Graviton、Ampere 等 ARM 伺服器走這條路。這呼應全課的 UEFI 知識——你學的 UEFI（Part 3）在 ARM SystemReady 上同樣適用。這是 ARM 向 x86 的「標準化」靠攏（而 x86 的 device tree 興趣不大）。兩個架構在中間相遇。

## Apple Silicon：另一種 ARM 開機

```
Apple Silicon（M1/M2...）的開機：
  - 自訂的 Boot ROM + iBoot（Apple 的 bootloader）
  - 不是 UEFI 也不是標準 U-Boot
  - Asahi Linux 專案逆向工程了它，讓 Linux 能在 M1 開機
        │
  展示 ARM 開機的多樣性——連 Apple 都有自己一套
```

## x86 vs ARM 開機對照

| 面向 | x86 | ARM（嵌入式）| ARM（SystemReady）|
|---|---|---|---|
| 上電狀態 | 16-bit real mode（Ch 2）| 原生模式（無 real mode）| 原生模式 |
| 韌體 | BIOS/UEFI（統一）| Boot ROM + U-Boot（廠商）| UEFI |
| 硬體描述 | PCI 枚舉 + ACPI + e820 | device tree | ACPI/device tree |
| bootloader | GRUB/systemd-boot | U-Boot | GRUB |
| 相容包袱 | 重（real mode、A20...）| 輕 | 輕 |
| 碎片化 | 低（統一 PC）| 高（每板不同）| 低（標準化）|

## 故意對照：x86 的歷史包袱 vs ARM 的乾淨

```
從 ARM 回看 x86，看清哪些是歷史債：

  real mode（Ch 2）：
    x86 為了相容 8086 而有，每次開機要爬模式（Ch 7-8）
    ARM 沒有——上電直接原生模式
    → real mode 是純歷史債

  A20 line（Ch 2）：
    x86 為了相容位址回繞的荒謬 hack
    ARM 沒有
    → 純歷史債

  device tree vs PCI 枚舉：
    x86 的硬體自描述（PCI/ACPI）是真設計優勢（kernel 能探索硬體）
    ARM 缺這個，要 device tree 補
    → 這是 x86 的真優勢，不是包袱
        │
  結論：x86 的模式切換、A20 是歷史債；
        硬體自描述是真設計優勢
```

> 從 ARM 對照 x86，能區分「歷史包袱」和「真設計」。real mode、A20 是純歷史債（ARM 證明不需要它們）。但 x86 的硬體自描述（PCI 枚舉、ACPI）是真優勢——它讓 kernel 能探索硬體，而 ARM 缺這個要靠 device tree 補。這個對照讓你對 x86 開機的理解更立體：不是所有複雜都是壞的，要分辨歷史債和必要設計。

## coreboot：開源韌體

最後提一個跨架構的主題——**coreboot**（Ch 1/10 提過）：

```
coreboot：開源的韌體實作
  取代專有的 BIOS/UEFI 韌體本身
        │
  做最小化的硬體初始化，然後執行 payload：
    - SeaBIOS payload（提供 BIOS 介面）
    - TianoCore payload（提供 UEFI 介面）
    - Linux as payload（直接開機 Linux，極快）
    - U-Boot payload
        │
  目標：最小化專有 code、可審計、快速開機
  用於：Chromebook（出貨用 coreboot）、隱私/安全敏感場景
```

coreboot 是「把韌體本身開源」的運動——傳統 BIOS/UEFI 韌體是專有黑盒子（Ch 10 的爭議），coreboot 用開源實作取代，最小化專有 code。Chromebook 就用 coreboot。它跨架構（x86、ARM 都支援），體現了「對封閉韌體的不信任」這個 Ch 10/27 反覆出現的主題的最徹底回應。

## 動手練習

1. 如果你有樹莓派或 ARM 開發板：看 device tree（`cat /proc/device-tree/model`、`ls /boot/*.dtb`），用 `dtc` 反編譯一個 `.dtb` 看硬體描述

2. 對照 x86 和 ARM：列出本課學的 x86 開機概念（real mode、GDT、BIOS/UEFI、e820...），判斷每個在 ARM 有沒有對應、是歷史債還是真設計

3. 研究 SystemReady：讀 ARM SystemReady 的概念，理解它如何讓 ARM 伺服器用 UEFI（你 Part 3 學的）

4. 看 coreboot：瀏覽 coreboot 文件，理解它如何用 payload 機制提供 BIOS/UEFI/直接開機，以及為什麼 Chromebook 用它

## 本章重點整理

- ARM 開機和 x86 的根本差異：無 real mode 包袱、硬體極度多樣、無統一韌體、硬體不自描述
- ARM 流程：Boot ROM（廠商燒死）→ SPL（初始化 DRAM）→ U-Boot → kernel，多了 x86 韌體代勞的事
- device tree 取代 x86 的硬體自描述：用資料結構告訴 kernel 硬體佈局（一個 kernel 配不同 dtb 跑不同板）
- ARM SystemReady：讓 ARM 伺服器用 UEFI + ACPI（標準化，像 x86），你學的 UEFI 同樣適用
- 從 ARM 對照看清 x86：real mode/A20 是歷史債，硬體自描述是真優勢；coreboot 是開源韌體運動

## 自我檢核

- [ ] 能說出 ARM 開機和 x86 的根本差異（無 real mode、硬體多樣、device tree）
- [ ] 能解釋 device tree 解決什麼問題（硬體不能自描述）
- [ ] 知道 ARM SystemReady 如何讓 ARM 用 UEFI（和本課 Part 3 的關係）
- [ ] 能從 ARM 對照判斷 x86 的哪些開機設計是歷史債、哪些是真優勢
- [ ] 知道 coreboot 是什麼、它的目標（開源韌體、最小化專有 code）

## 延伸閱讀

### 官方文件

- **[Device Tree Specification](https://www.devicetree.org/specifications/)**
  - **讀哪裡**：overview 和 basic concepts
  - **學什麼**：device tree 的格式和概念
  - **前提**：本章

- **[U-Boot documentation](https://docs.u-boot.org/)** 和 **[coreboot documentation](https://doc.coreboot.org/)**
  - **讀哪裡**：U-Boot 的 boot flow、coreboot 的 payload
  - **學什麼**：ARM bootloader 和開源韌體的細節
  - **前提**：本章 + Ch 17

### 部落格 / 文章

- **[Booting ARM Linux](https://www.kernel.org/doc/html/latest/arch/arm/booting.html)** — Linux kernel ARM boot doc
  - **這篇說什麼**：ARM Linux 開機的 kernel 端要求（device tree、暫存器約定）
  - **讀哪裡**：整份
  - **為什麼值得讀**：ARM 開機的權威 kernel 文件，對照 x86 的 boot protocol（Ch 20）

- **[Asahi Linux: booting on Apple Silicon](https://asahilinux.org/)** 
  - **這篇說什麼**：在 Apple Silicon 上開機 Linux 的逆向工程
  - **讀哪裡**：開機相關的技術文章
  - **為什麼值得讀**：展示 ARM 開機的極端多樣性（Apple 自訂一套）

→ [Final Project：從零組一個可開機系統](./final-project-boot-system.md)
