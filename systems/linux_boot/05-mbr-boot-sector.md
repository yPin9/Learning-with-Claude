# Ch 5 — MBR 與 boot sector

> **目標**：徹底拆解 MBR（Master Boot Record）的 512-byte 結構——boot code（446B）、partition table（64B）、boot signature（2B）、partition entry 的格式、active 旗標，以及 MBR 分區方案的限制（為什麼需要 GPT）。

> **環境**：概念為主，配合 `xxd` 檢視真實 MBR。

## 為什麼 MBR 結構這麼擠？

BIOS 只載入磁碟第一個 sector（512 bytes）並跳過去執行。這 512 bytes 不只要放開機 code，還要放**分區表**（告訴系統磁碟怎麼切分）。兩個東西擠在 512 bytes 裡，加上結尾的 signature——這就是 MBR 的由來，一個極度擁擠的設計。

理解 MBR 的佈局，你才知道為什麼 boot code 只有 446 bytes 可用、partition table 怎麼描述分區、以及為什麼這個 1983 年的設計撐不住現代大磁碟（催生 GPT，Ch 11）。

## 先建立直覺：512 bytes 的三明治

```
MBR（磁碟第一個 sector，512 bytes）：

  偏移 0    ┌──────────────────────────────┐
            │                              │
            │   Boot Code（446 bytes）      │ ← 你的 boot code 在這
            │   （bootstrap）               │
            │                              │
  偏移 446  ├──────────────────────────────┤
            │  Partition Entry 1（16 bytes）│
            │  Partition Entry 2（16 bytes）│ ← 分區表
            │  Partition Entry 3（16 bytes）│   (4 個 entry × 16B = 64B)
            │  Partition Entry 4（16 bytes）│
  偏移 510  ├──────────────────────────────┤
            │  Boot Signature 0x55 0xAA     │ ← 2 bytes
  偏移 512  └──────────────────────────────┘
```

三層三明治：446B 開機 code + 64B 分區表 + 2B 簽章 = 512B。注意開機 code 只有 446 bytes——這就是為什麼 boot sector 幾乎什麼都做不了，只能載入更大的 stage2（Ch 9）。

## Boot Signature：0x55AA

最後 2 bytes 必須是 `0x55 0xAA`（在磁碟上的 byte 順序）。BIOS 讀第一個 sector 後檢查這 2 bytes——是 `0x55AA` 才認為這是可開機 sector。

```
偏移 510: 0x55
偏移 511: 0xAA
        │
  在 assembly 裡寫 `dw 0xAA55`（小端序）
  → 記憶體/磁碟上是 0x55, 0xAA（低 byte 在前）
        │
  這就是 Ch 0 的 `dw 0xaa55`：
  dw（define word）寫 16-bit，小端序，磁碟上是 55 aa
```

> 注意 `dw 0xAA55` 和磁碟上的 byte 順序。`dw`（define word）寫一個 16-bit 值，x86 是小端序（little-endian），所以 `0xAA55` 在磁碟上是 `55 AA`（低 byte 0x55 在前）。新手常搞混寫 `0x55AA` 還是 `0xAA55`——記住：要磁碟上是 `55 AA`，assembly 寫 `dw 0xAA55`。

## Partition Table：4 個 entry

偏移 446 開始是分區表，4 個 partition entry，每個 16 bytes：

```c
struct mbr_partition_entry {     // 16 bytes
    uint8_t  boot_flag;          // 0x80 = active(可開機), 0x00 = 否
    uint8_t  chs_start[3];       // 起始 CHS（已過時，看 LBA）
    uint8_t  partition_type;     // 分區類型（0x83=Linux, 0x07=NTFS, 0xEE=GPT保護...）
    uint8_t  chs_end[3];         // 結束 CHS（已過時）
    uint32_t lba_start;          // 起始 LBA（線性 sector 編號）← 重要
    uint32_t lba_sectors;        // 分區有幾個 sector ← 重要
};
```

各欄位：

| 欄位 | 意義 |
|---|---|
| `boot_flag` | `0x80` = active（這個分區可開機）；`0x00` = 否。傳統上只有一個分區 active |
| `chs_start/end` | 起始/結束的 CHS 位址（過時，現代看 LBA）|
| `partition_type` | 分區類型碼（`0x83` Linux、`0x07` NTFS/exFAT、`0xEE` GPT protective...）|
| `lba_start` | 分區起始的 LBA（從磁碟開頭算第幾個 sector）|
| `lba_sectors` | 分區大小（幾個 sector）|

## Active 旗標與開機

傳統 MBR 開機流程依賴 active 旗標：

```
傳統 MBR 開機（chain loading）：
  1. BIOS 載入 MBR（磁碟第一個 sector）到 0x7C00
  2. MBR 的 boot code 掃描 4 個 partition entry
  3. 找到 boot_flag = 0x80（active）的分區
  4. 載入那個分區的第一個 sector（VBR, Volume Boot Record）到 0x7C00
  5. 跳過去，VBR 接手載入該分區的 OS
        │
  這叫 chain loading：MBR → VBR → OS bootloader
```

但現代 bootloader（GRUB）通常不走這個傳統流程——GRUB 把自己的 code 直接放在 MBR 和後續的「MBR gap」（Ch 19），不依賴 active 旗標的 chain loading。active 旗標主要是 Windows 和傳統 bootloader 用。

## 檢視真實的 MBR

```bash
# 看一個磁碟/image 的 MBR（前 512 bytes）
sudo xxd -l 512 /dev/sda          # 真實磁碟（小心！）
xxd -l 512 disk.img               # image 檔（安全）

# 看 partition table 那段（偏移 446 = 0x1BE）
xxd -s 0x1BE -l 66 disk.img
# 0x1BE 開始 64 bytes 是分區表，最後 2 bytes 是 55 aa

# 用 fdisk 看分區（人類可讀）
fdisk -l disk.img
```

```
xxd 輸出範例（偏移 0x1BE 開始）：
  000001be: 80 20 21 00 83 ... 00 08 00 00 00 f8 0f 00  
            │                                            
            0x80 = active   0x83 = Linux   lba_start  lba_sectors
```

## MBR 分區方案的限制

MBR 是 1983 年的設計，撐不住現代需求：

```
MBR 的硬限制：
  1. 最多 4 個 primary partition
     （只有 4 個 16-byte entry）
     → 用 extended partition 這個 hack 繞過（一個 primary 當容器裝更多 logical partition）

  2. 磁碟最大 2 TB
     lba_start 和 lba_sectors 是 32-bit
     2^32 個 sector × 512 bytes = 2 TB
     → 超過 2TB 的磁碟，MBR 定址不到後面的空間

  3. 沒有冗餘
     MBR 只有一份，壞了整個磁碟的分區資訊就沒了

  4. boot code 只有 446 bytes
     太小，現代 bootloader 要用各種 hack 繞
```

> **2TB 限制**是 MBR 被淘汰的關鍵原因。`lba_sectors` 是 32-bit，最多表示 2^32 個 sector，每 sector 512 bytes = 2TB。現在 4TB、8TB、16TB 硬碟很普遍，MBR 根本定址不到。這直接催生了 GPT（Ch 11），用 64-bit LBA 支援到 ZB 級。如果你用 MBR 分一個 4TB 磁碟，後面 2TB 會無法使用。

## 故意弄壞：partition table 覆蓋 boot code

```asm
; boot sector 的 boot code 寫太長，蓋到 partition table
org 0x7c00
start:
    ; ... 假設你的 code 超過 446 bytes ...
    ; 那麼 code 會延伸到偏移 446（partition table 區）
    ; → 你的 code 把 partition table 當 code 執行，或反之

; 正確：boot code 必須 ≤ 446 bytes，
; 用 times 填充確保 partition table 在偏移 446
times 446-($-$$) db 0    ; 填充到偏移 446
; partition table（64 bytes）
; ... 4 個 entry ...
times 510-($-$$) db 0
dw 0xAA55
```

如果 boot code 超過 446 bytes，會侵入 partition table 區，導致分區資訊損壞或 code 執行到 partition data。這是 MBR 的硬限制——446 bytes 是你的天花板（所以才需要 stage2）。

## 踩雷集錦

1. **boot code 超過 446 bytes**：會蓋到 partition table。446 bytes 是硬上限，超過就要兩階段（Ch 9）

2. **boot signature 的 byte 順序**：要磁碟上是 `55 AA`，assembly 寫 `dw 0xAA55`（小端序）。寫反了 BIOS 不認

3. **以為 MBR 能分超過 4 個 primary 分區**：MBR 只有 4 個 entry。要更多分區用 extended partition（一個 primary 當容器）或改用 GPT

4. **MBR 分超過 2TB 磁碟**：32-bit LBA 限制 2TB。大磁碟用 GPT。用 MBR 分大磁碟後面的空間會丟失

5. **混淆 MBR 和 VBR**：MBR 是整個磁碟的第一個 sector（含分區表）；VBR（Volume Boot Record）是每個分區的第一個 sector。chain loading 是 MBR 載入 active 分區的 VBR

## 進階：MBR、VBR、與 bootloader 的關係

理解三個層次的 boot record：

```
磁碟佈局（MBR 方案）：
  Sector 0:        MBR（含 boot code + partition table）
  Sector 1 ~ N:    「MBR gap」（MBR 和第一個分區之間的空隙）
                   GRUB 把 core.img 藏在這（Ch 19）
  分區 1 的第一個 sector: VBR（Volume Boot Record）
  ...
        │
  傳統 chain loading：MBR boot code → active 分區的 VBR → OS
  GRUB（BIOS）：MBR boot code（GRUB stage1）→ MBR gap 的 core.img → ...
```

MBR gap（MBR 之後、第一個分區之前的空隙，通常約 1MB）是 GRUB 等現代 bootloader 藏 code 的地方——因為 446 bytes 太小。Ch 19 會詳述 GRUB 怎麼用這個 gap。這也是為什麼「分區不要從 sector 1 開始」——要留 gap 給 bootloader。

## 動手練習

1. 建一個有分區的 disk image（`dd` 造空檔 + `fdisk` 分區），用 `xxd -s 0x1BE -l 66` 看 partition table，解讀每個 entry 的 boot_flag、type、lba_start

2. 用 `fdisk -l` 看分區，對照 xxd 的 raw bytes，確認 LBA 值對得上

3. 寫一個 boot sector，故意讓 boot code 超過 446 bytes（塞一堆 nop），看組譯時 `times 446-($-$$)` 會報負數錯誤（組譯器幫你抓到超標）

4. 看你系統真實磁碟的 MBR（`sudo xxd -l 512 /dev/sda`，唯讀安全），判斷它是 MBR 還是 GPT（GPT 的 partition type 是 0xEE，protective MBR）

## 本章重點整理

- MBR 是磁碟第一個 sector（512B）：446B boot code + 64B partition table（4 entry × 16B）+ 2B signature
- boot signature 必須是磁碟上的 `55 AA`（assembly 寫 `dw 0xAA55`，小端序）
- partition entry：boot_flag（0x80=active）、type、lba_start、lba_sectors
- MBR 限制：最多 4 primary 分區、磁碟最大 2TB（32-bit LBA）、boot code 只 446B、無冗餘
- 2TB 限制和 446B 限制催生了 GPT（Ch 11）和兩階段 bootloader（Ch 9）

## 自我檢核

- [ ] 能畫出 MBR 的三段結構（446 + 64 + 2）並說出各段內容
- [ ] 知道 boot signature 的 byte 順序（磁碟 55 AA，asm 寫 0xAA55）
- [ ] 能解讀一個 partition entry 的關鍵欄位（boot_flag、type、lba_start/sectors）
- [ ] 知道 MBR 的兩個主要限制（4 分區、2TB）及它們的成因
- [ ] 知道 MBR gap 是什麼、GRUB 為什麼用它

## 延伸閱讀

### 官方文件

- **[OSDev Wiki: MBR (Partition Table)](https://wiki.osdev.org/MBR_(x86))** 和 **[Partition Table](https://wiki.osdev.org/Partition_Table)**
  - **讀哪裡**：MBR 結構和 partition entry 格式
  - **學什麼**：每個 byte 的精確意義、partition type 碼列表
  - **前提**：無

### 部落格 / 文章

- **[The MBR and the boot process](https://thestarman.pcministry.com/asm/mbr/)** — The Starman's Realm
  - **這篇說什麼**：極詳細的 MBR 結構拆解，含真實 MBR 的 hex dump 逐 byte 解讀
  - **讀哪裡**：MBR structure 那節
  - **為什麼值得讀**：把 MBR 的每個 byte 講到極致，是 MBR 結構的最佳參考

→ [Ch 6 寫一個 boot sector](./06-write-boot-sector.md)
