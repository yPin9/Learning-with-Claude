# Ch 5 — MBR 與 boot sector

> 目標：拆開 MBR 的 512 bytes，每個欄位是什麼、partition table 怎麼編碼、為什麼 2TB 上限。

## 我們在哪裡

第 3 階段 (Bootloader) 的最開頭。BIOS 把這 512 bytes 載到 `0x7C00`，然後我們的 code 開始跑。

## 512 bytes 的分配

```
 Offset    Size      Content
 ──────    ────      ───────
 0x000     446       Boot code (你寫的 asm)
 0x1BE     16        Partition entry 1
 0x1CE     16        Partition entry 2
 0x1DE     16        Partition entry 3
 0x1EE     16        Partition entry 4
 0x1FE     2         Boot signature (0x55 0xAA)
                     ────
                     512 bytes
```

關鍵數字：

- **446** bytes 給 boot code — 這是 stage 1 的全部
- **64** bytes 給 partition table（4 個 entry × 16 bytes）
- **2** bytes 給 signature `55 AA`

446 bytes 大概 = 100 ~ 200 行 asm。連列印 hello world、讀第二個 sector 都要省著寫。

## Partition entry 結構

每個 entry 16 bytes：

```
 Offset  Size  Field
 ──────  ────  ─────
 0       1     Boot indicator (0x80 = active, 0x00 = inactive)
 1       3     Starting CHS (cylinder/head/sector，舊欄位)
 4       1     Partition type (0x83 = Linux, 0x07 = NTFS, ...)
 5       3     Ending CHS
 8       4     Starting LBA (32-bit)
 12      4     Number of sectors (32-bit)
```

幾個重點：

- **Boot indicator**：哪個 partition 是「active」。傳統上 stage 1 會找 active 的 partition、把它的 boot sector（VBR, Volume Boot Record）讀進來、跳過去。
- **CHS 是廢欄位**：Cylinder/Head/Sector 是磁碟早期的物理座標。現代磁碟全部用 LBA (Logical Block Addressing)。CHS 欄位在 MBR 裡通常填假值。
- **LBA 是 32-bit**：sector 編號最大 `2^32`。每 sector 512 bytes，2^32 × 512 = 2 TB **— 這是 MBR 的 2TB 上限**。

要破 2TB 上限，必須改用 **GPT (GUID Partition Table)**，那是 UEFI 配套的東西，後面 Ch 11 講。

## Partition type 常見值

| 值 | 意義 |
|---|---|
| `0x00` | Empty |
| `0x01` | FAT12 |
| `0x06` | FAT16 |
| `0x07` | NTFS / exFAT |
| `0x0B` | FAT32 |
| `0x0C` | FAT32 LBA |
| `0x82` | Linux swap |
| `0x83` | Linux native |
| `0x8E` | Linux LVM |
| `0xA5` | FreeBSD |
| `0xEE` | GPT protective MBR |
| `0xEF` | EFI System Partition (BIOS 看到時) |

`0xEE` 特別值得提：當磁碟用 GPT 時，第一個 LBA 還是有 MBR，但只有一個 partition entry，type = `0xEE`，cover 整顆磁碟。這叫 **Protective MBR**，目的是讓不認 GPT 的舊工具看到「這顆磁碟是滿的」，不會誤以為是空白磁碟去 format 它。

## Boot signature

最後 2 bytes 必須是 `0x55 0xAA`（little-endian 寫成 `55 AA`）。BIOS 會檢查，少這 2 bytes 直接跳過這個磁碟。

寫 asm 的話最後這樣寫：

```asm
times 510-($-$$) db 0    ; 把前面填到 510
dw 0xAA55                ; signature (little-endian → 55 AA)
```

`times 510-($-$$) db 0` 是 NASM 的 idiom：「填到第 510 個 byte 前」。`($-$$)` 是當前 offset，510 - 當前 offset = 還要填多少個零。

## 完整 hex dump 範例

拿一顆 Linux 機器的 MBR 出來看：

```bash
sudo dd if=/dev/sda bs=512 count=1 status=none | xxd
```

簡化的輸出（GRUB2 的 stage 1）：

```
00000000: eb63 9000 0000 0000 0000 0000 0000 0000  .c..............
00000010: 0000 0000 0000 0000 0000 0000 0000 0000  ................
...
000001b0: 0000 0000 0000 0000 1234 5678 0000 8020  .........4Vx...
000001c0: 2100 8395 8362 0008 0000 00f0 7f06 00fe  !....b..........
000001d0: ffff 0500 ffff 00f8 7f06 0008 8019 0000  ................
000001e0: 0000 0000 0000 0000 0000 0000 0000 0000  ................
000001f0: 0000 0000 0000 0000 0000 0000 0000 55aa  ..............U.
```

看幾個重點：

- `eb 63` 在 offset 0：是 `jmp short +0x63`，跳過後面的 BPB 區（GRUB stage 1 的開頭）
- `0x1b8` 開始 4 bytes：disk signature（NT 用的）
- `0x1be` 開始：第一個 partition entry，`80` = active
- `0x1fe`：`55 aa` signature

## 一個常見誤解：「MBR 就是 boot sector」

**部分對**。

精確的說法：

- **MBR (Master Boot Record)** = 第一個磁碟的第一個 sector。包含 boot code + partition table + signature
- **VBR (Volume Boot Record)** = 每個 partition 的第一個 sector。內容隨檔案系統 (FAT 的 BPB、ext4 的 superblock 不重疊)

stage 1 (MBR) 的 boot code 通常做兩件事：

1. 讀 partition table，找到 active partition
2. 把 active partition 的 VBR 讀進來、跳過去

VBR 再去找 stage 2 / kernel。但 GRUB 不走這條路 — 它把 stage 1.5 塞在 MBR 跟第一個 partition 之間的「保留區」（傳統上 62 個 sector），stage 1 直接讀那塊。

`fdisk -l` 看到 partition 從 LBA 2048 開始就是這個原因：留前面 1MB 給 GRUB stage 1.5。

## MBR 的另一個限制：4 個 primary partition

partition table 只有 4 個 16-byte entry，最多 4 個 partition。1980 年代覺得夠了。

後來想到一個 hack：**extended partition** — 把第 4 個 entry 的 type 設成 `0x05` (extended)，裡面放 link list 形式的 logical partition。但醜得要命，每個 logical partition 還要佔一個 sector 放它自己的 partition table。

GPT 一次支援 128 個 partition，這個 hack 就被淘汰了。

## 動手練習

**1. 看你機器的 MBR**

```bash
sudo dd if=/dev/sda bs=512 count=1 status=none | xxd | tail -10
```

最後一行有 `55aa` 嗎？倒數幾行的 partition entry 你解得開嗎？

**2. 用 `sfdisk` 印 partition table**

```bash
sudo sfdisk -d /dev/sda
```

對照 hex dump 看一次，每個 entry 的 type 跟 LBA range 怎麼來的。

**3. 故意弄壞 signature 看會怎樣**（**只在 QEMU 裡做**！）

```bash
# 建一個假磁碟
dd if=/dev/zero of=disk.img bs=512 count=1024
# 寫一個假 boot sector，但不寫 signature
dd if=/dev/urandom of=disk.img bs=512 count=1 conv=notrunc
# 跑 QEMU
qemu-system-x86_64 -drive format=raw,file=disk.img -nographic
```

SeaBIOS 會說：

```
Boot failed: not a bootable disk
```

把 signature 補上：

```bash
printf '\x55\xaa' | dd of=disk.img bs=1 seek=510 conv=notrunc
```

再跑就會看到 BIOS 真的 jmp 到你的隨機資料 — CPU 跑亂、可能 reset 或 hang。這就是「沒簽名 BIOS 不認、有簽名但 code 是亂的就直接跳」的證明。

## 自我檢核

- [ ] 講得出 MBR 512 bytes 的 446 / 64 / 2 分配
- [ ] partition entry 的 type / LBA / size 各佔幾 byte
- [ ] 知道 MBR 為什麼有 2TB 上限
- [ ] 知道 GPT protective MBR (`0xEE`) 是幹嘛的
- [ ] 知道 partition 為什麼通常從 LBA 2048 開始

下一章我們自己寫一個 boot sector，讓 BIOS 載進來、印 hello world。

→ [Ch 6 動手：自製 hello boot sector](./06-hello-boot-sector.md)
