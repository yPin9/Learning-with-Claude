# Ch 20 — Multiboot 與 kernel handover protocol

> **目標**：理解 bootloader 和 kernel 之間的「交棒契約」——Linux x86 boot protocol（setup header、boot_params/zero page、entry points）、Multiboot/Multiboot2 規範，以及 bootloader 如何精確準備 kernel 期待的環境。這是「bootloader 載入 kernel」的最後一塊拼圖。

> **環境**：Linux x86 boot protocol（kernel Documentation/x86/boot.rst），Multiboot2 spec。承接 Ch 9/16（載入 kernel）。原理深挖章。

## 為什麼交棒需要嚴格的契約？

Ch 9（BIOS）和 Ch 16（UEFI）你知道 bootloader 要「載入 kernel 並跳過去」。但「跳過去」不是隨便 `jmp`——kernel 對「它被啟動時的環境」有嚴格的期待：自己被載到哪、某些結構填了什麼、暫存器是什麼狀態。

這個期待就是 **boot protocol**（交棒契約）。bootloader 必須精確遵守，kernel 才能正確接手。違反契約——載錯位址、結構沒填、暫存器不對——kernel 會 panic 或直接當機，且很難 debug（kernel 還沒起來，沒有錯誤訊息）。這章把這個契約講清楚。

## 先建立直覺：交棒是填一張「表格」給 kernel

```
bootloader 交棒給 kernel 的本質：

  kernel 醒來時會問：
    - 記憶體有多少？哪些可用？（memory map）
    - 我的開機參數是什麼？（command line，如 root=/dev/sda2）
    - initramfs 在哪？（位址 + 大小）
    - 螢幕怎麼設定的？（framebuffer）
    - ...
        │
  bootloader 必須在交棒前，把這些答案填進一個
  kernel 約定好的結構（Linux 叫 boot_params / zero page）
        │
  然後跳到 kernel entry point，把這個結構的位址告訴 kernel
        │
  kernel 從結構讀取所有答案，開始初始化
```

交棒像填一張表格給 kernel——bootloader 填好 kernel 需要的所有資訊（記憶體、cmdline、initramfs...），放在約定位置，kernel 接手後讀這張表。表格的格式就是 boot protocol。

## Linux x86 boot protocol：setup header

Linux kernel 的 bzImage 前面有個 **setup header**，描述 kernel 怎麼被載入和啟動：

```
bzImage 的結構（前段）：

  ┌────────────────────────────┐
  │  real-mode setup code      │ ← 16-bit 開機 stub（BIOS 用）
  │  含 setup header           │   描述載入參數、entry points
  ├────────────────────────────┤
  │  protected-mode kernel     │ ← 壓縮的 kernel（Ch 21 解壓）
  │  (compressed vmlinux)      │
  └────────────────────────────┘
```

setup header 的關鍵欄位（在 bzImage 偏移 0x1F1 起）：

```c
// setup header（節錄，kernel arch/x86/include/uapi/asm/bootparam.h）
struct setup_header {
    __u8  setup_sects;        // setup code 佔幾個 sector
    __u16 root_flags;
    __u32 syssize;            // protected-mode kernel 大小
    // ...
    __u16 boot_flag;          // 0xAA55（魔數）
    __u16 jump;
    __u32 header;             // "HdrS" 魔數
    __u16 version;            // boot protocol 版本
    // ...
    __u32 ramdisk_image;      // initramfs 載入位址 ← bootloader 填
    __u32 ramdisk_size;       // initramfs 大小 ← bootloader 填
    // ...
    __u32 cmd_line_ptr;       // command line 位址 ← bootloader 填
    // ...
    __u64 pref_address;       // kernel 偏好的載入位址
    // ...
};
```

bootloader 讀這個 header 知道「kernel 想被載到哪、setup code 多大」，並**填寫**某些欄位（initramfs 位址、cmdline 位址）告訴 kernel 這些東西在哪。

## boot_params：zero page

`boot_params`（俗稱 **zero page**）是 bootloader 傳給 kernel 的完整資訊結構：

```c
// boot_params（zero page）—— bootloader 填好傳給 kernel
struct boot_params {
    struct screen_info screen_info;       // 螢幕/framebuffer 資訊
    // ...
    __u8  e820_entries;                   // e820 記憶體地圖條目數（Ch 3）
    // ...
    struct setup_header hdr;              // 上面的 setup header
    // ...
    struct boot_e820_entry e820_table[E820_MAX_ENTRIES_ZEROPAGE]; // e820 地圖
    // ...
    struct efi_info efi_info;             // UEFI 資訊（memory map 等，Ch 14）
};
```

`boot_params` 包含 kernel 需要的一切：
- **e820_table**：記憶體地圖（BIOS 線，Ch 3）
- **efi_info**：UEFI 記憶體地圖和系統表（UEFI 線，Ch 14）
- **hdr**：setup header（含 initramfs/cmdline 位址）
- **screen_info**：framebuffer

> 為什麼叫 zero page？歷史上這個結構被放在記憶體位址 0（或某個低位址），所以叫「zero page」。bootloader 配置一塊記憶體當 boot_params，填好所有欄位，把它的位址傳給 kernel。kernel 從這裡讀取記憶體地圖、cmdline、initramfs 位址——一切。

## Entry points：32-bit / 64-bit

Linux kernel 提供多個 entry point，bootloader 按情況選：

```
Linux kernel 的 entry points：

  16-bit entry（real-mode）:
    傳統 BIOS 開機，從 setup code 開始
    bootloader 跳這裡，kernel 自己切模式

  32-bit entry（protected mode）:
    偏移 0x100000 等，bootloader 已切到 protected mode
    rsi/esi 指向 boot_params

  64-bit entry（long mode）:
    現代 64-bit bootloader 用
    bootloader 已在 long mode，設好頁表
    rsi 指向 boot_params
        │
  選哪個 entry 取決於 bootloader 把 CPU 帶到哪個模式
```

UEFI bootloader（已在 64-bit，Ch 16）和 EFI stub 用 64-bit entry。傳統 BIOS bootloader 可能用 16-bit entry（讓 kernel 自己切模式）。entry point 的位址和暫存器約定在 boot protocol 規定。

## 完整的交棒流程（概念）

```
bootloader 交棒給 Linux kernel：

  1. 載入 bzImage 進記憶體（Ch 9/16）
        │
  2. 讀 setup header，確認 boot protocol 版本、kernel 要載到哪
        │
  3. 把 protected-mode kernel 搬到它要的位址（pref_address）
        │
  4. 配置並填寫 boot_params（zero page）：
     - 填 e820 / efi memory map（Ch 3/14）
     - 填 cmd_line_ptr（指向 "root=/dev/sda2 ro" 字串）
     - 填 ramdisk_image / ramdisk_size（initramfs 位址和大小）
     - 填 screen_info（framebuffer）
        │
  5.（UEFI）ExitBootServices（Ch 14）
        │
  6. 跳到 kernel entry point：
     - rsi = boot_params 位址（64-bit entry）
     - jmp kernel_entry
        │
  kernel 接手：從 boot_params 讀一切，開始初始化（Ch 21-22）
```

這就是 Ch 9/16 說的「按 handover protocol 跳 kernel」的完整內容。每個欄位都要填對，kernel 才能正確接手。

## Multiboot：通用的 bootloader-kernel 契約

Linux 的 boot protocol 是 Linux 專屬的。**Multiboot** 是個通用規範，讓任何遵守它的 kernel 能被任何支援 Multiboot 的 bootloader（如 GRUB）啟動：

```
Multiboot 的動機：
  每個 OS kernel 各有自己的 boot protocol
  → bootloader 要為每個 OS 寫專門的載入 code
        │
  Multiboot 規範：統一的契約
  - kernel 在開頭放一個 Multiboot header（魔數 + 標記）
  - bootloader 找到 header，按規範載入
  - bootloader 傳一個 Multiboot info 結構（記憶體地圖、cmdline、modules）
        │
  → 任何 Multiboot kernel 能被任何 Multiboot bootloader 啟動
    （GRUB 支援 Multiboot，所以自製 OS 常用 Multiboot 讓 GRUB 載入）
```

Multiboot header（放 kernel 開頭）：

```asm
; Multiboot2 header（自製 OS 放在 kernel 開頭）
section .multiboot
header_start:
    dd 0xE85250D6              ; Multiboot2 魔數
    dd 0                       ; architecture（0 = i386）
    dd header_end - header_start  ; header 長度
    dd -(0xE85250D6 + 0 + (header_end - header_start))  ; checksum
    ; ... tags ...
    dw 0                       ; end tag
    dw 0
    dd 8
header_end:
```

> Multiboot 對自製 OS 特別有用——你不用寫自己的 bootloader，只要在 kernel 放 Multiboot header，GRUB 就能載入你的 OS 並傳記憶體地圖、cmdline 等。這是 OSDev 社群的常見做法（os.phil-opp.com 等教學用 Multiboot + GRUB）。Linux 不用 Multiboot（它有自己的 boot protocol），但理解 Multiboot 能讓你懂「通用交棒契約」的設計。

## 故意弄壞：boot_params 沒填 cmdline

```
bootloader 跳 kernel 但 boot_params 的 cmd_line_ptr 沒填（或填 0）：
        │
  kernel 啟動，要讀 command line（找 root= 參數）
        │
  cmd_line_ptr = 0 或垃圾 → kernel 讀不到 cmdline
        │
  → kernel 不知道 root 在哪（沒有 root= 參數）
  → 後面掛 root 失敗 → "VFS: Unable to mount root fs" → kernel panic（Ch 23）
```

boot_params 的欄位沒填對，kernel 啟動會在某處失敗。沒填 cmdline → 沒有 root= → 掛 root 失敗 panic。沒填 memory map → kernel 不知道記憶體 → 早期就掛。這些失敗很難 debug（kernel 還沒完全起來），所以 bootloader 必須嚴格按 protocol 填好每個欄位。

## 踩雷集錦

1. **boot_params 欄位沒填全**：memory map、cmdline、initramfs 位址都要填。漏任何一個，kernel 在對應的初始化步驟失敗

2. **kernel 載到錯誤位址**：setup header 的 pref_address 說 kernel 要載哪。載錯位址，kernel code 跑不起來

3. **entry point 和 CPU 模式不符**：用 64-bit entry 但 CPU 還在 protected mode（沒切 long mode），或反之。entry point 要對應 bootloader 把 CPU 帶到的模式

4. **暫存器約定錯**：64-bit entry 要 rsi = boot_params 位址。沒設或設錯暫存器，kernel 找不到 boot_params

5. **Multiboot header 不在 kernel 前 8KB**：Multiboot 規範要求 header 在 kernel 開頭的前 8KB 內，且對齊。放太後面 GRUB 找不到，不認為這是 Multiboot kernel

6. **checksum 算錯**：Multiboot header 的 checksum 要讓 magic + arch + length + checksum = 0。算錯 GRUB 拒絕載入

## 進階：boot protocol 的演進與 EFI handover

Linux boot protocol 持續演進，版本號（setup header 的 version 欄位）標記能力：

```
boot protocol 版本演進（部分）：
  2.00：基本協定
  2.06：cmdline 可更長
  2.10：支援更高的載入位址
  2.12：增加 xloadflags（64-bit entry 等）
  2.15：EFI handover protocol
        │
  EFI handover protocol（UEFI 專用的捷徑）：
    UEFI bootloader 不用自己做 ExitBootServices + 填 efi_info
    而是跳到 kernel 的 EFI handover entry，
    把 UEFI 的 image handle 和 system table 傳給 kernel
    → kernel 自己做 ExitBootServices（EFI stub 就是這樣）
```

**EFI handover protocol** 是 UEFI 開機的簡化（Ch 16 的 EFI stub 用它）：bootloader 不用自己處理 ExitBootServices 的麻煩（Ch 14），而是把 UEFI 的 handle/system table 傳給 kernel，讓 kernel 的 EFI stub 自己做。這就是為什麼 EFI stub 能讓 kernel 自己當 bootloader——kernel 內建了處理 UEFI 交棒的 code。理解 boot protocol 的版本演進，你會懂為什麼新舊 bootloader 和 kernel 的相容性取決於它們支援的 protocol 版本。

## 動手練習

1. 看真實 kernel 的 setup header：`xxd /boot/vmlinuz-$(uname -r) | grep -A2 "HdrS"`（找 "HdrS" 魔數，那是 setup header 的開始），或用工具解析 bzImage header

2. 看 kernel 收到的 cmdline 和 boot 資訊：`cat /proc/cmdline`（kernel 收到的命令列，bootloader 填進 boot_params 的）、`dmesg | grep -i "command line"`、`dmesg | grep -i e820`（kernel 收到的記憶體地圖）

3. 研究 boot protocol：讀 kernel 的 `Documentation/x86/boot.rst`，找 setup header 的欄位表，理解哪些是 bootloader 要填的（標 "write" 的）

4. 自製 OS 暖身：寫一個最小的 Multiboot2 header + 一段印字 code，用 GRUB 載入它（`grub-mkrescue` 做 ISO），體驗「GRUB 載入自製 kernel」

## 本章重點整理

- 交棒是嚴格的契約（boot protocol）：bootloader 填好 kernel 期待的環境，kernel 才能正確接手
- Linux x86 boot protocol：bzImage 有 setup header（描述載入參數）；bootloader 填 boot_params/zero page（memory map、cmdline、initramfs 位址）
- kernel 有多個 entry point（16/32/64-bit），bootloader 按 CPU 模式選；64-bit entry 用 rsi = boot_params 位址
- Multiboot 是通用契約（kernel 放 Multiboot header，GRUB 能載入任何 Multiboot kernel），自製 OS 常用
- boot protocol 持續演進（版本號標記能力）；EFI handover protocol 讓 EFI stub 能簡化 UEFI 交棒

## 自我檢核

- [ ] 能解釋為什麼交棒需要嚴格的契約（boot protocol）
- [ ] 知道 boot_params（zero page）裝什麼，bootloader 要填哪些欄位
- [ ] 知道 setup header 的作用，以及 bootloader 從它讀什麼、填什麼
- [ ] 能解釋 Multiboot 解決什麼問題（通用的 bootloader-kernel 契約）
- [ ] 知道 boot_params 欄位沒填對會怎樣（kernel 在對應步驟失敗 panic）

## 延伸閱讀

### 官方文件

- **[Linux kernel: The Linux/x86 Boot Protocol](https://www.kernel.org/doc/html/latest/arch/x86/boot.html)**
  - **讀哪裡**：整份，特別是 setup header 欄位表和 entry points
  - **學什麼**：交棒契約的權威定義，每個欄位 bootloader 要不要填
  - **前提**：本章建立的概念

- **[Multiboot2 Specification](https://www.gnu.org/software/grub/manual/multiboot2/multiboot.html)**
  - **讀哪裡**：header 格式和 boot information 結構
  - **學什麼**：Multiboot 的精確規範，自製 OS 用
  - **前提**：本章

### 部落格 / 文章

- **[os.phil-opp.com: A minimal Multiboot kernel](https://os.phil-opp.com/multiboot-kernel/)** — Philipp Oppermann
  - **這篇說什麼**：用 Multiboot + GRUB 啟動自製 kernel 的完整實作
  - **讀哪裡**：Multiboot header 和 boot 那幾節
  - **為什麼值得讀**：把 Multiboot 契約落地成可跑的 code

→ [Ch 21 bzImage 結構與 kernel 解壓](./21-bzimage-decompress.md)
