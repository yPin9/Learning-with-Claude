# Ch 11 — GPT 分區表

> **目標**：拆解 GPT（GUID Partition Table）的結構——protective MBR、GPT header、partition entry array、備份 GPT、CRC 校驗，理解它如何突破 MBR 的限制（2TB、4 分區），以及和 ESP 的關係。

> **環境**：概念為主，配合 `gdisk`/`sgdisk`/`xxd` 檢視真實 GPT。

## 為什麼需要 GPT？

MBR（Ch 5）的限制——最多 4 個 primary 分區、磁碟最大 2TB——在現代是硬傷。8TB、16TB 硬碟普及，MBR 根本定址不到。UEFI 配套的分區方案是 **GPT**（GUID Partition Table），用 64-bit LBA、支援 128 個分區、有冗餘備份和 CRC 校驗。

理解 GPT 結構，你才知道現代磁碟怎麼分區、ESP 在 GPT 裡的角色、以及為什麼 GPT 比 MBR robust 得多。

## 先建立直覺：GPT 是 MBR 的「現代化重寫」

```
MBR 的問題           →  GPT 的解法
─────────              ─────────
4 個分區               →  128 個（預設，可更多）
2TB 上限（32-bit LBA） →  ZB 級（64-bit LBA）
無冗餘（一份壞了全毀） →  主 + 備份兩份
無校驗（壞了不知道）   →  CRC32 校驗
分區類型用 1-byte 碼   →  128-bit GUID（不會撞）
boot code 擠在 512B    →  分區表獨立，不和 boot code 搶空間
```

GPT 不是小修小補，是徹底重寫——把 MBR 的每個限制都解決。同時為了相容，GPT 保留一個「假的 MBR」（protective MBR）放在最前面，讓不懂 GPT 的舊工具不會誤以為磁碟是空的而亂寫。

## GPT 的磁碟佈局

```
GPT 磁碟佈局：

  LBA 0      ┌──────────────────────────┐
             │  Protective MBR           │ ← 假 MBR，保護用
  LBA 1      ├──────────────────────────┤
             │  Primary GPT Header       │ ← 主 GPT header
  LBA 2-33   ├──────────────────────────┤
             │  Partition Entry Array    │ ← 128 個 entry（每個 128B）
             │  (主)                     │
  LBA 34     ├──────────────────────────┤
             │                          │
             │  實際分區資料             │ ← 你的分區（ESP、root...）
             │  (ESP, /, swap...)        │
             │                          │
  磁碟尾-33  ├──────────────────────────┤
             │  Partition Entry Array    │ ← 備份 entry array
             │  (備份)                   │
  磁碟尾-1   ├──────────────────────────┤
             │  Backup GPT Header        │ ← 備份 GPT header
  磁碟尾     └──────────────────────────┘
```

關鍵設計：**主 GPT 在磁碟開頭，備份 GPT 在磁碟結尾**。如果開頭的主 GPT 損壞，能從結尾的備份恢復。這個冗餘是 MBR 沒有的（MBR 只有一份）。

## Protective MBR

GPT 磁碟的 LBA 0 不是真的 MBR，是個「保護性 MBR」：

```
Protective MBR（LBA 0）：
  - 結構和 MBR 一樣（512B、partition table、0x55AA）
  - 但 partition table 只有一個 entry：
    type = 0xEE（GPT protective）
    涵蓋整個磁碟（或前 2TB）
        │
  目的：讓不懂 GPT 的舊工具看到「磁碟已被一個 0xEE 分區佔滿」
        → 不會誤以為磁碟是空的而去寫 MBR、破壞 GPT
```

```bash
# 看 GPT 磁碟的 protective MBR
sudo xxd -s 0x1BE -l 16 /dev/sda
# 應該看到 type byte 是 ee（0xEE = GPT protective）
```

> protective MBR 是個聰明的相容性 hack：它讓 GPT 磁碟在舊工具眼中「看起來像被一個未知分區佔滿的 MBR 磁碟」，舊工具因此不敢亂動。Ch 5 你看真實磁碟的 MBR 時若看到 type 0xEE，就知道那其實是 GPT 磁碟。

## GPT Header

GPT header（LBA 1）描述整個 GPT 的 metadata：

```c
struct gpt_header {              // LBA 1，512 bytes（實際用 92 bytes）
    char     signature[8];       // "EFI PART"（magic）
    uint32_t revision;           // GPT 版本
    uint32_t header_size;        // header 大小（通常 92）
    uint32_t header_crc32;       // header 的 CRC32（自我校驗）
    uint32_t reserved;
    uint64_t current_lba;        // 這個 header 的 LBA（主=1）
    uint64_t backup_lba;         // 備份 header 的 LBA（磁碟尾）
    uint64_t first_usable_lba;   // 第一個可用 LBA（分區資料從這開始）
    uint64_t last_usable_lba;    // 最後可用 LBA
    uint8_t  disk_guid[16];      // 整個磁碟的唯一 GUID
    uint64_t partition_array_lba;// partition entry array 的起始 LBA（主=2）
    uint32_t num_partitions;     // entry 數量（通常 128）
    uint32_t partition_entry_size;// 每個 entry 大小（128）
    uint32_t partition_array_crc32;// entry array 的 CRC32
};
```

關鍵欄位：
- `signature = "EFI PART"`：辨識這是 GPT
- `header_crc32` / `partition_array_crc32`：CRC 校驗，能偵測損壞（MBR 沒有）
- `backup_lba`：備份 header 在哪（冗餘）
- `disk_guid`：整個磁碟的唯一識別碼

## Partition Entry

每個 partition entry（128 bytes）描述一個分區：

```c
struct gpt_partition_entry {     // 128 bytes
    uint8_t  type_guid[16];      // 分區類型 GUID（ESP/Linux/swap...）
    uint8_t  unique_guid[16];    // 這個分區的唯一 GUID
    uint64_t first_lba;          // 分區起始 LBA（64-bit！）
    uint64_t last_lba;           // 分區結束 LBA
    uint64_t attributes;         // 屬性 flags
    char16_t name[36];           // 分區名稱（UTF-16）
};
```

對比 MBR entry（16 bytes，1-byte type），GPT entry（128 bytes）豐富太多：
- **64-bit LBA**：突破 2TB 限制（這是 GPT 的核心優勢）
- **type GUID**（128-bit）：分區類型用 GUID 而非 1-byte 碼，不會撞、能無限擴充
- **unique GUID**：每個分區有唯一 ID（可用於穩定的掛載，`/dev/disk/by-partuuid/`）
- **name**：人類可讀的分區名稱

常見的 type GUID：

```
分區類型 GUID：
  ESP（EFI System Partition）:
    C12A7328-F81F-11D2-BA4B-00A0C93EC93B
  Linux filesystem:
    0FC63DAF-8483-4772-8E79-3D69D8477DE4
  Linux swap:
    0657FD6D-A4AB-43C4-84E5-0933C84B4F4F
  Linux LVM:
    E6D6D379-F507-44C2-A23C-238F2A3DF928
```

ESP 就是用那個特定的 type GUID 標記的——UEFI 韌體找開機分區時，就是找這個 GUID 的分區（Ch 10）。

## 檢視真實的 GPT

```bash
# 用 gdisk 看 GPT（互動式，p 列印分區）
sudo gdisk -l /dev/sda

# 或 sgdisk（腳本友善）
sudo sgdisk -p /dev/sda
# Number  Start (sector)    End (sector)  Size       Code  Name
#    1            2048         1050623   512.0 MiB   EF00  EFI System Partition
#    2         1050624       ...         ...         8300  Linux filesystem

# 看 GPT header 的 signature
sudo dd if=/dev/sda bs=512 skip=1 count=1 2>/dev/null | xxd | head -1
# 開頭應該是 "EFI PART"（45 46 49 20 50 41 52 54）

# 看分區的 type GUID 和 PARTUUID
sudo blkid /dev/sda1
# /dev/sda1: ... PARTLABEL="EFI System Partition" PARTUUID="..."
```

## CRC 校驗與備份恢復

GPT 的 robustness 來自 CRC + 備份：

```
GPT 的自我保護：
  1. header_crc32：每次讀 GPT，韌體/OS 算 CRC 比對
     不符 → header 損壞 → 從備份 header 恢復
  2. partition_array_crc32：同理，entry array 損壞能偵測
  3. 主 GPT（開頭）壞了 → 用備份 GPT（結尾）
     備份壞了 → 用主 GPT
        │
  → 單一位置損壞不會讓整個分區表報廢（MBR 沒這保護）
```

```bash
# gdisk 偵測到 GPT 損壞會警告，並能從備份恢復
sudo gdisk /dev/sda
# 如果主 GPT 壞了：
# Warning! Main partition table CRC mismatch! Loaded backup...
# gdisk 自動從備份載入，你可以 w 寫回修復
```

## 故意對照：MBR 無校驗 vs GPT 有校驗

```
場景：分區表的某個 byte 因磁碟錯誤被改了

MBR：
  - 沒有 CRC，系統不知道分區表壞了
  - 可能讀到錯誤的分區資訊，掛載錯誤或資料損壞
  - 沒有備份可恢復

GPT：
  - CRC 不符，韌體/gdisk 立刻知道「分區表損壞」
  - 從備份 GPT（磁碟另一端）恢復
  - 損壞被偵測且可修復
```

這就是為什麼現代系統一律用 GPT——不只是容量，更是 robustness。MBR 的「一份、無校驗」在大磁碟時代太脆弱。

## 踩雷集錦

1. **用 MBR 工具操作 GPT 磁碟**：老的 `fdisk` 看到 protective MBR 可能誤判。用 `gdisk`/`sgdisk`/`parted` 操作 GPT。新版 `fdisk` 已支援 GPT

2. **破壞 protective MBR**：有人看到 GPT 磁碟的 MBR 是「一個 0xEE 分區」，誤以為磁碟空的去重新分區。那會破壞 GPT。protective MBR 的 0xEE 是「這是 GPT，別碰 MBR」的信號

3. **只修主 GPT 不修備份**：主 GPT 壞了從備份恢復後，要把修好的寫回兩處（主+備份）。gdisk 的 `w` 會同時寫主和備份

4. **ESP 用錯 type GUID**：UEFI 找 ESP 是靠特定 type GUID（C12A7328...）。把分區 type 設錯，韌體認不出 ESP，找不到開機

5. **GPT 和 MBR 混淆磁碟狀態**：一個磁碟要嘛 MBR 要嘛 GPT。轉換（`gdisk` 能 MBR→GPT）要小心，轉換中斷可能兩者都壞

## 進階：hybrid MBR 與 GPT 的相容妥協

有些情況需要 GPT 磁碟同時被 BIOS（只懂 MBR）開機——這催生了醜陋的 **hybrid MBR**：

```
Hybrid MBR（妥協方案，少用）：
  protective MBR 不是「一個 0xEE 涵蓋全磁碟」
  而是放幾個「真的」MBR entry，對應 GPT 的某些分區
        │
  目的：讓 BIOS（看 MBR）和 UEFI（看 GPT）都能開機同一個磁碟
        │
  問題：MBR 和 GPT 兩份分區資訊要手動同步，極易出錯
        → 公認是「能不用就不用」的 hack（macOS Boot Camp 曾用）
```

> hybrid MBR 是「想讓老 BIOS 和新 UEFI 共用一個磁碟」的妥協，但它讓兩份分區表要同步，是 bug 溫床。現代建議：純 GPT（UEFI 開機）或純 MBR（BIOS 開機），不要 hybrid。知道它存在能解釋某些雙開機系統的詭異分區問題。

## 動手練習

1. 看你系統磁碟的 GPT：`sudo gdisk -l /dev/sda`，找出 ESP（type EF00）、各分區的 type 和 PARTUUID。確認 GPT header 的 "EFI PART" signature

2. 看 protective MBR：`sudo xxd -s 0x1BE -l 16 /dev/sda`，確認 type byte 是 0xEE

3. 在一個 image 檔上建 GPT 練習（安全，不碰真磁碟）：`truncate -s 1G test.img; sgdisk -n 1:0:+512M -t 1:EF00 -c 1:"ESP" test.img; sgdisk -n 2:0:0 -t 2:8300 test.img`，然後 `sgdisk -p test.img` 看結果

4. 比較 MBR 和 GPT：在 image 上分別建 MBR（`fdisk`）和 GPT（`gdisk`）分區，用 `xxd` 看前幾個 sector 的差異（MBR 的分區表在 0x1BE，GPT 的在 LBA 1-33）

## 本章重點整理

- GPT 突破 MBR 限制：128 分區（vs 4）、ZB 級（64-bit LBA，vs 2TB）、CRC 校驗、主+備份冗餘
- 佈局：protective MBR（LBA 0，相容保護）→ 主 GPT header（LBA 1）→ entry array（LBA 2-33）→ 分區資料 → 備份 GPT（磁碟尾）
- partition entry（128B）：type GUID、unique GUID、64-bit LBA、name——比 MBR entry 豐富太多
- ESP 用特定 type GUID（C12A7328...）標記，UEFI 韌體靠它找開機分區
- GPT 的 robustness（CRC + 備份）是現代用它的關鍵，不只是容量

## 自我檢核

- [ ] 能畫出 GPT 的磁碟佈局（protective MBR、主/備份 header、entry array）
- [ ] 知道 GPT 突破 MBR 的哪些限制（分區數、容量、校驗、冗餘）
- [ ] 能解釋 protective MBR 的目的（保護 GPT 不被舊工具破壞）
- [ ] 知道 ESP 怎麼被識別（特定 type GUID）
- [ ] 能說出 GPT 比 MBR robust 的原因（CRC 校驗 + 備份恢復）

## 延伸閱讀

### 官方文件

- **[UEFI Spec, Section 5 (GUID Partition Table Format)](https://uefi.org/specifications)**
  - **讀哪裡**：5.3（GPT），header 和 partition entry 的精確格式
  - **學什麼**：GPT 的權威定義，每個欄位的精確語意
  - **前提**：本章

- **[OSDev Wiki: GPT](https://wiki.osdev.org/GPT)**
  - **讀哪裡**：整頁，GPT 結構和 CRC 計算
  - **學什麼**：GPT 的實作細節、如何解析
  - **前提**：本章

### 部落格 / 文章

- **[Rod Smith's "Make the Most of Large Drives with GPT and Linux"](https://www.rodsbooks.com/gdisk/)** — Rod Smith（gdisk 作者）
  - **這篇說什麼**：GPT 的完整實務，gdisk 的設計者寫的；hybrid MBR 的詳細討論
  - **讀哪裡**：GPT basics 和 hybrid MBR 那幾節
  - **為什麼值得讀**：gdisk 作者本人，GPT 知識最權威的實務來源

→ [Ch 12 UEFI Boot Services 與 Runtime Services](./12-uefi-services.md)
