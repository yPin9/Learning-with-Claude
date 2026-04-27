# Ch 14 — bzImage / vmlinuz 結構與 Linux boot protocol

> 目標：拆開 `vmlinuz`、看清楚 setup header / kernel image / boot params 怎麼配合，bootloader 跟 kernel 之間 ABI 是什麼樣子。

## 我們在哪裡

第 4 階段 (Kernel) 的開頭。bootloader 把這個 binary 載進記憶體，然後我們要跳進去。

## vmlinuz / vmlinux / bzImage 的關係

- **`vmlinux`**：kernel build 出來的 ELF executable，含 debug symbol，不能直接開機
- **`vmlinux.bin`**：把 vmlinux 用 `objcopy` 抽出 raw binary，去掉 ELF header
- **壓縮版**：`vmlinux.bin` 用 gzip / lzma / lzo / xz / zstd 壓縮，得到 `vmlinux.bin.gz` 之類
- **`bzImage`** = "big zImage" = setup header + 解壓器 + 壓縮 kernel
- **`vmlinuz`**：歷史檔名，指向 bzImage（或某些情況指 zImage）

```
 vmlinuz / bzImage:
 ┌────────────────────────────────────┐
 │ Setup code (real mode boot stub)   │  ~16-32KB
 │ ├ MZ header (PE 偽裝給 UEFI 用)    │
 │ ├ Setup header (boot protocol)     │
 │ └ Real-mode boot code              │
 ├────────────────────────────────────┤
 │ Compressed kernel + decompressor   │  幾 MB
 │ ├ Decompressor (head_64.S, etc.)   │
 │ └ Compressed vmlinux.bin           │
 └────────────────────────────────────┘
```

關鍵：**bzImage 的開頭可以同時當 BIOS 16-bit boot stub 也能當 UEFI PE executable**。這是個天才的 hack。

## MZ + PE：UEFI 的偽裝

bzImage 的第一個 byte 是 `M`，第二個是 `Z` — 這是 DOS MZ executable 的 magic number。但 `MZ` 也是 PE/COFF header 的開頭。

所以：

- **BIOS 看**：MZ 是 16-bit code，跳過去就跑
- **UEFI 看**：MZ 是 PE header，找後面 PE signature、解析 PE，就當 UEFI app 跑

這代表 **UEFI 可以直接 boot bzImage**，不需要 GRUB（叫做 "EFI stub"）：

```bash
# 直接把 vmlinuz 當 UEFI bootloader
sudo efibootmgr -c -d /dev/sda -p 1 -L "Linux" \
  -l '\vmlinuz-5.15' -u 'root=/dev/sda2 initrd=\initrd.img-5.15'
```

`-u` 是 cmdline + initrd 路徑。kernel 的 EFI stub 自己讀 initrd。

## Setup Header — bootloader/kernel ABI

bzImage 的 offset `0x01F1` 開始是 **setup header**，bootloader 跟 kernel 用這個結構傳資訊。完整定義在 `Documentation/x86/boot.rst`。

關鍵欄位（簡化）：

```
 Offset  Field             描述
 ──────  ─────             ────
 0x1F1   setup_sects       setup 段有幾個 512-byte sector
 0x1F2   root_flags        舊欄位，現在不用
 0x1F4   syssize           protected-mode kernel 大小（in 16-byte unit）
 0x1FE   boot_flag         0xAA55（real-mode boot signature）
 0x200   jump              跳到 real-mode entry
 0x202   header_magic      "HdrS" — 識別這是 Linux header
 0x206   version           boot protocol 版本 (e.g. 0x020F)
 0x210   type_of_loader    bootloader ID（GRUB = 0x7x、syslinux = 0x3x...）
 0x211   loadflags         flag bit
 0x214   code32_start      protected-mode kernel 在記憶體的位址
 0x218   ramdisk_image     initrd 在記憶體的位址 (32-bit, deprecated for >4G)
 0x21C   ramdisk_size      initrd 大小
 0x220   bootsect_kludge   舊欄位
 0x228   cmd_line_ptr      cmdline 字串的位址
 0x22C   ramdisk_max       initrd 最大允許位址
 0x238   cmd_line_size     cmdline 最大長度
 0x250   setup_data        ext data linked list
 0x258   pref_address      kernel 想被載到哪
 0x260   init_size         運行時 kernel 需要的記憶體
```

bootloader 的工作：

1. 把 bzImage 載到記憶體
2. **修改** setup header 的某些欄位（type_of_loader、cmd_line_ptr、ramdisk_image、ramdisk_size...）
3. 把 cmdline 字串放在 cmd_line_ptr 指向的位置
4. 把 initrd 載到 ramdisk_image 位置
5. 跳到 setup code（real-mode 路線）或 EFI stub（UEFI 路線）

## Boot Protocol 演進

| Protocol | 出現年代 | 主要新增 |
|---|---|---|
| 1.x | early 90s | 基本 |
| 2.00 | 1997 | bzImage 大檔支援 |
| 2.02 | 1999 | cmdline 從 boot params 拿位址 |
| 2.06 | 2007 | cmdline_size 欄位 |
| 2.08 | 2008 | crc32 校驗 |
| 2.09 | 2009 | setup_data linked list |
| 2.10 | 2010 | EFI handover protocol |
| 2.11 | 2010 | xloadflags |
| 2.13 | 2013 | x86_64 64-bit kernel entry |
| 2.15 | 2020 | EFI mixed mode |

現代 kernel 用 2.15，但 bootloader 必須能支援多版本（compat 用）。

## 兩條進入 kernel 的路

### 路徑 1：Real-mode entry（傳統 BIOS）

bootloader 把 setup code 載到 `0x10000` ~ `0x17FFF`，protected-mode kernel 載到 `0x100000` (1MB)，跳到 `setup_code + 0x200`。

setup code 在 real mode 跑：

1. 確認 BIOS、抓 memory map (E820)
2. 抓 CPU 資訊
3. 抓 video mode
4. 抓硬碟、APM 等
5. 切到 protected mode（已經 32-bit）
6. 跳到 `code32_start`（解壓器）

`arch/x86/boot/header.S` 跟 `main.c` 是這段。

### 路徑 2：EFI handover（UEFI 直接）

bootloader（GRUB 或直接 EFI stub）已經在 long mode、UEFI 環境下。Linux EFI stub 直接從 `efi_main` 接手：

1. 用 UEFI Boot Services 抓 memory map、framebuffer
2. 跳過 real-mode setup（因為 UEFI 已經提供等價資訊）
3. 直接呼叫 `ExitBootServices`
4. 跳到 64-bit entry `extract_kernel`

`arch/x86/boot/compressed/head_64.S` 跟 `eboot.c` 是這段。

EFI stub 路徑大約**省 200ms** — 跳過 BIOS 風格的硬體偵測。

## boot_params 結構（給 kernel 的「啟動清單」）

setup code（或 EFI stub）跑完後組出一個 `struct boot_params` 傳給 kernel。位置從 `code32_start` 旁邊算。

```c
struct boot_params {
    struct screen_info screen_info;       // VGA 模式
    struct apm_bios_info apm_bios_info;
    ...
    struct setup_header hdr;              // 上面那個 header 的 in-memory 版本
    ...
    struct e820_entry e820_table[128];    // memory map
    __u8 e820_entries;
    ...
};
```

kernel 進 `start_kernel` 後 parse 這個 struct，知道：

- 有多少 RAM、哪些區段是 reserved
- cmdline 字串在哪
- initrd 在哪、多大
- framebuffer 設定

## E820 memory map

BIOS / UEFI 提供「哪些位址範圍是 RAM、哪些是 reserved」的資訊，叫 E820 map（因為 BIOS INT 15h, AX=E820h）。

```
range                  type
─────                  ────
0x000000 - 0x09FBFF    Usable RAM
0x09FC00 - 0x09FFFF    Reserved (BIOS data area extension)
0x0E0000 - 0x0FFFFF    Reserved (BIOS / VGA ROM)
0x100000 - 0xBFFEFFFF  Usable RAM
0xBFFEF000 - 0xBFFFFFFF Reserved (ACPI)
0xFEE00000 - 0xFEE00FFF Reserved (LAPIC)
0xFFFC0000 - 0xFFFFFFFF Reserved (BIOS)
```

types:
- 1 = Usable
- 2 = Reserved
- 3 = ACPI reclaimable
- 4 = ACPI NVS
- 5 = Bad RAM

kernel 用這個 map 決定哪裡能放 page、哪裡是 device MMIO 不能踩。

跑 `dmesg | grep e820` 看你機器的：

```
[    0.000000] BIOS-e820: [mem 0x0000000000000000-0x000000000009fbff] usable
[    0.000000] BIOS-e820: [mem 0x000000000009fc00-0x000000000009ffff] reserved
[    0.000000] BIOS-e820: [mem 0x00000000000e0000-0x00000000000fffff] reserved
[    0.000000] BIOS-e820: [mem 0x0000000000100000-0x00000000bffeefff] usable
...
```

## 一個常見誤解：「kernel = vmlinuz」

vmlinuz 是 **packaged kernel image**，含：
- setup code（不會跑進 OS）
- 解壓器（不會跑進 OS）
- 壓縮的 kernel binary

真正的 kernel 是裡面的 vmlinux.bin。boot 過程的「解壓」就是把 vmlinux.bin 拆出來、解壓到記憶體裡執行。

開機後跑 `cat /proc/kallsyms | head` 看到的 symbol 都來自 vmlinux.bin，不是 vmlinuz 殼層。

## 一個常見誤解：「kernel 自己會找硬碟」

不會。kernel 啟動時**只認得 cmdline 上 `root=` 指定的東西**：

- `root=/dev/sda1` — 直接給 device node
- `root=UUID=xxx` — 透過 udev / devicemanager 解析
- `root=LABEL=foo`

如果 device 還沒被 driver 認到（典型 NVMe + 沒載 driver），kernel 找不到 root，panic 噴 "Cannot mount root filesystem"。

這就是為什麼需要 initramfs — 在 initramfs 裡先載 driver，再給 kernel 真正的 root。

## 動手練習

**1. 看你機器的 vmlinuz 開頭**

```bash
sudo xxd /boot/vmlinuz-$(uname -r) | head -5
# 第一行應該看到 "MZ" 或 ELF 標記
file /boot/vmlinuz-$(uname -r)
# Linux kernel x86 boot executable bzImage, ...
```

**2. 看 setup header**

```bash
# 用 hexdump 抽 0x1F1 開始的 setup_sects
sudo dd if=/boot/vmlinuz-$(uname -r) bs=1 skip=497 count=1 status=none | xxd
# offset 0x1F1 = 497
```

對照 `Documentation/x86/boot.rst` 解讀。

**3. 看 boot_params 在 dmesg**

```bash
sudo dmesg | grep -i "boot protocol"
# Linux version ... boot protocol 2.15, ...
```

**4. 用 EFI stub 直接 boot**

如果你機器是 UEFI，可以註冊一個 entry 不經 GRUB 直接 boot kernel：

```bash
sudo efibootmgr -c -d /dev/sda -p 1 -L "DirectKernel" \
  -l '\vmlinuz-5.15.0-X' \
  -u 'root=/dev/sda2 ro initrd=\initrd.img-5.15.0-X quiet'
```

**`-d` 跟 `-p` 改成你的磁碟跟 ESP partition 號**。實驗前先記下原本的 BootOrder。

**5. 看 kernel 自帶的 EFI stub**

```bash
file /boot/vmlinuz-$(uname -r)
# Linux kernel x86 boot executable bzImage, version ..., RW-rootFS, swap_dev 0xN, Normal VGA
# 注意有沒有寫 "EFI"
```

```bash
# 用 objdump 看 PE header
objdump -p /boot/vmlinuz-$(uname -r) 2>&1 | head -20
```

## 自我檢核

- [ ] 知道 vmlinux / bzImage / vmlinuz 三個名詞的關係
- [ ] 知道 bzImage 開頭 MZ 的雙重身份（DOS + PE）
- [ ] 講得出 setup header 至少 5 個欄位
- [ ] 知道 real-mode entry 跟 EFI handover 兩條路徑
- [ ] 知道 boot_params + e820 是什麼

下一章看 `arch/x86/boot/` 真實 source code，從 setup 怎麼走到 `start_kernel`。

→ [Ch 15 arch/x86/boot/ 從 setup 到 start_kernel](./15-arch-x86-boot.md)
