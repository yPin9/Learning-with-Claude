# Ch 17 — Bootloader 的角色與生態

> **目標**：理解 bootloader 在開機接力中的定位、它解決了韌體和 kernel 之間的什麼鴻溝，並橫向比較主流 bootloader（GRUB、systemd-boot、U-Boot、syslinux、rEFInd）的設計取向與適用場景。

## 為什麼需要 bootloader？韌體不能直接開 kernel 嗎？

你已經能自己載入 kernel（練習 A 的 BIOS 線、練習 B 的 UEFI 線），甚至 EFI stub 讓 kernel 自己當 bootloader（Ch 16）。那為什麼還需要 GRUB 這種獨立的 bootloader？

```
韌體和 kernel 之間的鴻溝（bootloader 填補）：

  韌體只懂：
    - 開機裝置（磁碟/USB）
    - 簡單的檔案系統（UEFI 懂 FAT；BIOS 什麼都不懂）
    - 一個 .efi（UEFI）或一個 512B sector（BIOS）

  使用者想要：
    - 開機選單（多 OS、多 kernel 版本）
    - 從各種檔案系統讀 kernel（ext4、btrfs、XFS、LVM、加密磁碟...）
    - 救援模式、編輯開機參數
    - 統一的開機體驗（不管 BIOS 還是 UEFI）

  → bootloader 填補這個鴻溝
```

bootloader 的核心價值：它懂韌體不懂的東西（複雜檔案系統、多 OS）、提供韌體沒有的功能（選單、救援），並抽象掉 BIOS/UEFI 的差異。

## 先建立直覺：bootloader 是「開機的中間管理層」

```
沒有 bootloader（EFI stub 直接開）：
  韌體 → kernel（kernel 必須在 ESP 的 FAT 上，單一 OS）

有 bootloader（GRUB）：
  韌體 → GRUB → 選單 → 從任意檔案系統讀任意 kernel
         │
         GRUB 提供：
         - 多 OS / 多 kernel 選單
         - ext4/btrfs/LVM/LUKS 等檔案系統支援
         - 開機參數編輯
         - 救援 shell
```

bootloader 是「開機的中間管理層」——它讓開機從「韌體直接執行一個固定的東西」變成「有選擇、有功能、跨檔案系統」。對單一 OS 的簡單系統，EFI stub 就夠；對多 OS、複雜儲存（LVM/加密）、要救援功能的系統，bootloader 不可少。

## 主流 bootloader 比較

### GRUB（GRand Unified Bootloader）

最主流、功能最全的 Linux bootloader：

```
GRUB 特點：
  - 支援 BIOS 和 UEFI（同一個 GRUB，兩種韌體都能用）
  - 內建大量檔案系統驅動（ext4/btrfs/XFS/FAT/LVM/LUKS...）
  - 模組化（core + 動態載入模組，Ch 18）
  - 強大的 grub.cfg 腳本語言（條件、迴圈、變數）
  - 多 OS 開機（chainload Windows、其他 Linux）
  - 救援 shell、命令列
        │
  代價：複雜、龐大、設定容易出錯
```

GRUB 是「全能但複雜」——幾乎什麼都能做，但設定檔（grub.cfg）複雜，更新機制（grub-mkconfig）有時令人困惑。多數主流發行版（Ubuntu、Debian、Fedora）預設用 GRUB。Ch 18-19 深入 GRUB。

### systemd-boot（原 gummiboot）

簡單的 UEFI-only bootloader：

```
systemd-boot 特點：
  - 只支援 UEFI（不支援 BIOS）
  - 極簡：只是個選單，列出 ESP 上的 .efi/kernel
  - 設定簡單（每個開機項一個小文字檔）
  - 依賴 EFI stub（kernel 自己當 bootloader）
        │
  哲學：「韌體已經能讀 FAT、執行 .efi，bootloader 只要提供選單就好」
```

```
# systemd-boot 的開機項設定（/boot/loader/entries/arch.conf）
title   Arch Linux
linux   /vmlinuz-linux
initrd  /initramfs-linux.img
options root=/dev/sda2 rw
```

systemd-boot 是「少即是多」的代表——它不重新實作檔案系統（用韌體的 FAT 支援）、不做複雜邏輯，只提供選單。適合「純 UEFI、kernel 在 ESP、不需要複雜功能」的系統。Arch、某些 minimalist 發行版偏好它。

### U-Boot（Das U-Boot）

嵌入式系統的標準 bootloader：

```
U-Boot 特點：
  - 嵌入式霸主（ARM、RISC-V、MIPS 開發板）
  - 支援極多硬體平台和開機介面
  - 強大的命令列（能 debug 硬體、網路開機、改記憶體）
  - 用 device tree（Ch 30）描述硬體
        │
  場景：樹莓派、開發板、路由器、各種嵌入式 Linux
```

U-Boot 是嵌入式世界的 GRUB——當你的目標不是 PC 而是 ARM 開發板、IoT 裝置，bootloader 通常是 U-Boot。它處理 PC 沒有的問題（沒有 BIOS/UEFI 的裸硬體、device tree、各種開機儲存介面）。Ch 30（ARM 開機）會碰到。

### syslinux / isolinux

輕量的 BIOS bootloader：

```
syslinux 家族：
  - SYSLINUX：從 FAT 開機
  - ISOLINUX：從 CD/ISO 開機（live USB/光碟常用）
  - PXELINUX：網路開機（PXE）
  - EXTLINUX：從 ext 檔案系統開機
        │
  特點：輕量、簡單、設定容易
  場景：live USB、安裝媒體、網路開機
```

syslinux 家族輕量簡單，常用於 live USB 和安裝媒體（你裝 Linux 用的 USB 開機通常是 isolinux/syslinux）。功能比 GRUB 少但夠用且好設定。

### rEFInd

漂亮的 UEFI boot manager：

```
rEFInd 特點：
  - UEFI boot manager（圖形化選單，能自動偵測 OS）
  - 漂亮的圖示介面
  - 自動掃描 ESP 和其他分區找可開機的 OS
        │
  場景：多 OS（尤其 macOS + Linux + Windows）、想要好看選單
```

## 對比與選擇

| Bootloader | 韌體 | 複雜度 | 檔案系統 | 適用場景 |
|---|---|---|---|---|
| **GRUB** | BIOS + UEFI | 高 | 內建大量 | 主流發行版、多 OS、複雜儲存 |
| **systemd-boot** | UEFI only | 低 | 靠韌體 FAT | 純 UEFI、單純系統、Arch |
| **U-Boot** | 嵌入式 | 中-高 | 多種 | ARM/RISC-V 開發板、IoT |
| **syslinux** | BIOS（主）| 低 | FAT/ext | live USB、安裝媒體 |
| **rEFInd** | UEFI | 低 | 靠韌體 | 多 OS、要好看選單 |
| **EFI stub** | UEFI | 無 | 靠韌體 FAT | 單一 OS、無選單需求 |

> **選擇原則**：複雜需求（多 OS、LVM/加密 root、BIOS+UEFI 都要支援）用 GRUB；純 UEFI 單純系統用 systemd-boot 或 EFI stub；嵌入式用 U-Boot；live/安裝媒體用 syslinux。沒有「最好的 bootloader」——看需求。GRUB 最全能但最複雜；systemd-boot/EFI stub 最簡單但功能少。

## 故意對照：GRUB vs EFI stub 開同一個 kernel

```
用 GRUB 開機：
  韌體 → grubx64.efi → 讀 grub.cfg → 顯示選單 →
  從 /boot（可能在 ext4/LVM/加密）讀 vmlinuz → 載入 → 跳 kernel
        │
  好處：kernel 能放任意檔案系統、有選單、能救援
  代價：GRUB 本身要安裝維護、grub.cfg 要生成

用 EFI stub 開機：
  韌體 → 直接執行 vmlinuz（在 ESP 的 FAT 上）→ kernel 自己跑
        │
  好處：極簡，沒有中間人
  代價：kernel 必須在 ESP 的 FAT、單一 OS、無選單
```

這個對照體現了「功能 vs 簡單」的取捨。如果你的 root 在 LVM 或加密磁碟，kernel 通常放 `/boot`（在那個複雜儲存上），EFI stub 讀不到（它只懂 ESP 的 FAT），就需要 GRUB（它有 LVM/LUKS 驅動）。這是為什麼複雜系統用 GRUB。

## 踩雷集錦

1. **以為「bootloader 越多功能越好」**：功能多 = 複雜 = 出錯面大。簡單系統用 GRUB 是殺雞用牛刀。按需選擇

2. **systemd-boot 想開 BIOS 系統**：systemd-boot 只支援 UEFI。BIOS 系統用不了它（用 GRUB 或 syslinux）

3. **EFI stub 但 kernel 在 ext4 的 /boot**：EFI stub 靠韌體讀 FAT，讀不到 ext4 的 kernel。kernel 要在 ESP（FAT），或用 GRUB

4. **混淆 bootloader 和 boot manager**：boot manager（rEFInd、UEFI 內建）只「選擇並啟動」開機項；bootloader（GRUB）還會「載入 kernel、提供功能」。有時界線模糊（systemd-boot 介於兩者）

5. **U-Boot 用在 PC**：U-Boot 是嵌入式的，PC 用 GRUB/systemd-boot。反之 GRUB 也能用在某些 ARM，但嵌入式生態以 U-Boot 為主

## 進階：bootloader 的趨勢——直接開機與 unified kernel image

開機生態正在簡化：

```
趨勢一：直接 UEFI 開機（少一層 bootloader）
  EFI stub 讓 kernel 自己當 bootloader
  systemd-boot 只提供選單
  → 對簡單系統，傳統 bootloader（GRUB）變可選

趨勢二：Unified Kernel Image (UKI)
  把 kernel + initramfs + command line + microcode
  打包成「一個簽署的 .efi」
  → 韌體執行這個 UKI，一切都在裡面
  → 簡化 Secure Boot（簽一個 .efi 而非多個元件）
  → systemd 推動的現代開機方案
```

**UKI（Unified Kernel Image）** 是值得關注的趨勢：把開機需要的所有東西（kernel、initramfs、cmdline）打包成單一簽署的 `.efi`。好處是 Secure Boot 簡化（簽一個檔案）、開機元件統一管理。systemd 和某些發行版（如 Fedora 的某些配置）在推動這個。理解這個趨勢，你會懂為什麼「傳統 GRUB 的地位在某些場景被挑戰」——不是 GRUB 不好，而是簡單系統有更輕量的選擇。

> **認識論誠實**：「GRUB 是不是過時了」是有爭議的。GRUB 仍是多數發行版預設，因為它最全能（多 OS、複雜儲存、BIOS+UEFI）。但對純 UEFI 單一 OS，systemd-boot/UKI 更簡單。這是「功能 vs 簡單」的持續辯論，沒有定論。本課 Ch 18-19 深入 GRUB（因為它最主流且教最多概念），但你要知道生態在演進。

## 動手練習

1. 看你系統用哪個 bootloader：`ls /boot/`（有 grub/ 目錄通常是 GRUB）、`ls /boot/efi/EFI/`（看有哪些 bootloader 的 .efi）、`bootctl status`（如果有 systemd-boot）

2. 比較設定複雜度：看 GRUB 的 `/boot/grub/grub.cfg`（複雜、自動生成）vs systemd-boot 的 `/boot/loader/entries/*.conf`（如果有，簡單文字）

3. 研究你的開機鏈：UEFI 系統用 `efibootmgr -v` 看韌體執行哪個 .efi，那個 .efi 是哪個 bootloader

4. 概念練習：給幾個場景（單一 Arch + UEFI、Ubuntu 雙開機 Windows、樹莓派、製作 live USB），判斷各該用哪個 bootloader

## 本章重點整理

- bootloader 填補韌體（只懂簡單開機）和 kernel（要從複雜儲存載入、要選單）之間的鴻溝
- GRUB：全能但複雜（BIOS+UEFI、大量檔案系統、多 OS）；systemd-boot：簡單（UEFI-only、只提供選單、靠 EFI stub）
- U-Boot：嵌入式標準；syslinux：live/安裝媒體；rEFInd：漂亮的 UEFI boot manager；EFI stub：無 bootloader
- 選擇看需求：複雜系統用 GRUB，純 UEFI 單純系統用 systemd-boot/EFI stub，嵌入式用 U-Boot
- 趨勢：直接 UEFI 開機、UKI（unified kernel image）簡化開機和 Secure Boot

## 自我檢核

- [ ] 能解釋 bootloader 填補韌體和 kernel 之間的什麼鴻溝
- [ ] 能說出 GRUB 和 systemd-boot 的設計哲學差異（全能複雜 vs 簡單）
- [ ] 知道什麼場景該用哪個 bootloader（至少 GRUB / systemd-boot / U-Boot / syslinux）
- [ ] 能解釋為什麼複雜儲存（LVM/加密 root）通常需要 GRUB 而非 EFI stub
- [ ] 知道 UKI 是什麼、它解決什麼（簡化開機元件和 Secure Boot）

## 延伸閱讀

### 官方文件

- **[Arch Wiki: Boot loaders](https://wiki.archlinux.org/title/Arch_boot_process#Boot_loader)**
  - **讀哪裡**：boot loaders 比較表和各 bootloader 的條目
  - **學什麼**：各 bootloader 的實務比較和設定，Arch Wiki 是最完整的
  - **前提**：本章

### 部落格 / 文章

- **[Brave new world of UEFI and the future of bootloaders](https://0pointer.net/blog/brave-new-trusted-boot-world.html)** — Lennart Poettering（systemd 作者）
  - **這篇說什麼**：systemd 對開機未來的願景，UKI、直接 UEFI 開機、trusted boot
  - **讀哪裡**：整篇
  - **為什麼值得讀**：理解開機生態的演進方向，來自推動這個方向的核心人物

- **[A tour of the GRUB boot loader](https://lwn.net/Articles/728858/)** — LWN
  - **這篇說什麼**：GRUB 的設計和在生態中的定位
  - **讀哪裡**：overview 那節
  - **為什麼值得讀**：為 Ch 18 的 GRUB 深入鋪墊

→ [Ch 18 GRUB 深入：架構與模組](./18-grub-internals.md)
