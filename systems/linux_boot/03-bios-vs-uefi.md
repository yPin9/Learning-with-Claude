# Ch 3 — BIOS vs UEFI 路線總覽

> 目標：把兩條開機路線並列看，知道差在哪、為什麼分家、各自的優缺點。

## 我們在哪裡

第 2 階段（Firmware）+ 第 3 階段（Bootloader）。這是 BIOS 跟 UEFI 唯一不同的地方。

## 兩條路線的高層對照

```
 BIOS path:                          UEFI path:

 [POST]                              [POST]
   │                                   │
   ▼                                   ▼
 找 boot device (依 BIOS 設定)       讀 NVMe/SATA、找 ESP 分割區
   │                                   │
   ▼                                   ▼
 讀第 0 個 sector (MBR, 512B)       讀 ESP/EFI/<vendor>/<x>.efi
   │                                   │
   ▼                                   ▼
 載到 0x7C00、jmp 過去              載 PE/COFF executable、call EntryPoint
   │                                   │
   ▼                                   ▼
 stage 1 → stage 1.5 → stage 2     直接呼叫 GRUB2 / systemd-boot
   │                                   │
   ▼                                   ▼
 載 kernel + initramfs              載 kernel + initramfs
   │                                   │
   └────────────┬──────────────────────┘
                ▼
            jmp to kernel
```

關鍵差異：**BIOS 給你 512 bytes 的房間，UEFI 給你一整顆 PE executable**。

## BIOS 路線：為什麼這麼擠

BIOS 是 1981 年 IBM PC 的設計，沒人想得到 40 年後我們還在用。它的 boot 流程：

1. 韌體在開機後讀第一個磁碟的 sector 0（512 bytes）
2. 檢查最後 2 個 byte 是不是 `0x55 0xAA`（boot signature）
3. 是的話，把這 512 bytes 載到 `0x7C00`，然後 `jmp 0x0000:0x7C00`

整個 bootloader 第一段就是 512 bytes，扣掉 partition table（最後 64 bytes）跟 signature，**真正能寫 code 的只有 446 bytes**。

446 bytes 連讀檔系統都不夠寫。所以實務上：

- **stage 1**（446 bytes）：只做一件事 — 讀 stage 2
- **stage 1.5**（在 MBR 後面到第一個 partition 之間的「保留區」）：認得簡單檔案系統，能讀 `/boot/grub`
- **stage 2**：完整 GRUB，有 menu、有腳本、有 module loader

這是 BIOS 路線的全部痛苦來源 — 一切都因為 512 bytes 太少。

## UEFI 路線：擺脫 512 bytes 的詛咒

UEFI 是 Intel 在 1998 年起的 EFI 計畫，2005 年改名 UEFI 並開放標準。它徹底重寫 boot：

1. 韌體本身就是個小 OS，有檔案系統 driver、網路 driver
2. 它讀一個 FAT32 分割區叫 ESP (EFI System Partition)
3. 在 ESP 裡找 `\EFI\BOOT\BOOTX64.EFI`（fallback）或 NVRAM 變數指定的 `.efi` 檔
4. 把 `.efi`（PE/COFF 格式，跟 Windows .exe 同 family）載入記憶體
5. CPU 已經在 long mode（!），直接 call entry point

沒有 512 bytes 限制。bootloader 可以是任意大小、用 C 寫、有完整的 graphics、有 mouse、有 network — 你看過的 ASUS / MSI 那種炫炮 BIOS 設定畫面就是 UEFI app。

## 詳細對照表

| 項目 | BIOS | UEFI |
|---|---|---|
| 出生年份 | 1981 | 2005（spec 1.0） |
| 啟動時 CPU 模式 | Real mode | Long mode |
| Bootloader 大小限制 | 446 bytes (stage 1) | 無實質上限 |
| Bootloader 格式 | Raw binary | PE/COFF |
| 開機資訊存放 | 主機板 CMOS | NVRAM 變數 + ESP 上的檔案 |
| 分割表 | MBR (4 個 primary partition、2TB 上限) | GPT (128 partition、ZB 等級上限) |
| 開機選單 | 自己寫 / GRUB stage 2 | 韌體自帶（按 F12 那個） |
| 安全機制 | 無 | Secure Boot（簽章驗證） |
| Network boot | PXE（要主機板支援） | 內建（HTTP / iSCSI 都有） |
| 開機速度 | 慢（多階段、real mode 切換） | 快（直接 long mode） |
| 寫 bootloader 的痛苦度 | 高（asm + 容量限制） | 低（C + 完整 lib） |

## 一個常見誤解：「UEFI 取代 BIOS」

**部分對，但模糊**。

實情是：

- 早期所有 UEFI 韌體都帶一個叫 **CSM (Compatibility Support Module)** 的東西，可以模擬 BIOS 行為跑 legacy bootloader。也就是「UEFI 韌體 + BIOS 模式啟動」這種組合
- 2020 年左右 Intel 開始要求 OEM 移除 CSM，新主機板沒有 BIOS 模式可選
- 所以技術上「BIOS」現在常常是「UEFI 韌體裡的 BIOS 模擬」，不是真的 BIOS

對學習來說：學 BIOS 路線**不是學歷史**。原因：

1. embedded、舊機器、雲端某些 image 還在用
2. BIOS 的東西都很小（asm + 512 bytes），是學 boot 最好的起點
3. UEFI 的概念建立在 BIOS 上，懂 BIOS 後 UEFI 哪些是改進、哪些是新觀念才看得清楚

## 怎麼判斷你的機器在跑哪個

```bash
# 方法 1：看 /sys/firmware/efi 存不存在
ls /sys/firmware/efi 2>/dev/null && echo "UEFI" || echo "BIOS"

# 方法 2：efibootmgr
sudo efibootmgr -v   # UEFI 才會有輸出

# 方法 3：dmidecode
sudo dmidecode -t bios
```

絕大多數 2015 年後的機器是 UEFI。雲 VM 看廠商：

- AWS EC2：早期 BIOS，新的 instance type 開始支援 UEFI
- GCP：UEFI（Shielded VM 強制）
- Azure：兩種都有，看 generation

## 為什麼這系列要兩條都教

「現在都 UEFI 了，學 BIOS 幹嘛」這種想法漏了三件事：

1. **PXE / netboot / iPXE** 大量場景還是 BIOS 思維（因為要相容萬國機型）
2. **學 protected / long mode 切換**只能在 BIOS 路線學 — UEFI 一上來就 long mode 了，你看不到模式切換
3. **理解 OS 設計史**對 systems 工程師重要：知道為什麼 PE 格式、為什麼 GPT、為什麼 NVRAM，必須對照舊的看才有 fu

所以後面 Part 2（BIOS）跟 Part 3（UEFI）會走同樣的事情兩遍，從不同角度。

## 動手練習

去你機器上找出這幾個東西，搞清楚是哪條路線：

```bash
# 1. 你的開機順序
sudo efibootmgr -v 2>/dev/null || echo "你是 BIOS 機器"

# 2. ESP 在哪（如果是 UEFI）
mount | grep -i efi
df -h | grep -i efi

# 3. ESP 裡面長什麼樣
ls /boot/efi/EFI/    # 或 /efi/EFI/

# 4. 找 .efi 檔
sudo find /boot -name "*.efi" 2>/dev/null

# 5. 對照看 GRUB 的 cfg
cat /boot/grub/grub.cfg 2>/dev/null | head -30
# 或
cat /boot/efi/EFI/*/grub.cfg 2>/dev/null | head -30
```

如果你看到 `\EFI\debian\grubx64.efi`、`\EFI\ubuntu\shimx64.efi` 這種路徑，就是 UEFI；如果你的 `/boot` 直接掛在 `/dev/sda1` 而沒有獨立 ESP，可能是 BIOS。

## 自我檢核

- [ ] 兩條路線在哪一段分岔、哪一段又合流，畫得出來
- [ ] BIOS 為什麼有 446 bytes 的限制
- [ ] UEFI bootloader 是什麼格式、放在哪
- [ ] 知道自己機器是哪種

下一章開始 Part 2，BIOS 路線從 reset vector 講起。

→ [Ch 4 BIOS POST、reset vector、INT 服務](./04-bios-post-and-int.md)
