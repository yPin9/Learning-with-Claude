# Ch 19 — GRUB 在 BIOS 與 UEFI 的差異

> **目標**：理解同一個 GRUB 在 BIOS 和 UEFI 下的安裝與運作差異——BIOS GRUB 如何用 MBR + MBR gap、UEFI GRUB 如何用 ESP 上的 `grubx64.efi`、`grub-install` 的不同行為，以及這如何呼應 Part 2（BIOS）和 Part 3（UEFI）的底層差異。

> **環境**：GRUB 2.06。本章把 Part 2/3 的底層知識和 GRUB 連起來。

## 為什麼同一個 GRUB 有兩種安裝方式？

GRUB 支援 BIOS 和 UEFI（Ch 17）——但這兩種韌體的開機機制根本不同（Part 2 vs Part 3）。BIOS 載入 MBR 的 512 bytes（Ch 5），UEFI 執行 ESP 上的 `.efi`（Ch 10）。同一個 GRUB 要適應這兩種完全不同的「韌體怎麼把控制權交給我」的方式。

理解 GRUB 的 BIOS 版和 UEFI 版差異，能把你在 Part 2/3 學的底層知識（MBR gap、ESP、`.efi`）和實際的 GRUB 安裝連起來——你會懂 `grub-install` 在兩種模式下到底做什麼。

## 先建立直覺：GRUB 適應兩種「入口」

```
BIOS 入口（Part 2）：               UEFI 入口（Part 3）：
  韌體載入 MBR 512B → 跳 0x7C00      韌體執行 ESP 上的 .efi
        │                                 │
  GRUB 的 boot.img 放 MBR 的            GRUB 的 grubx64.efi 放 ESP
  boot code 區（446B）                  /EFI/<distro>/grubx64.efi
        │                                 │
  boot.img 載入 core.img               grubx64.efi 是個完整的
  （core.img 放 MBR gap）               UEFI application（含 GRUB core）
        │                                 │
        └──────────┬──────────────────────┘
                   ▼
          GRUB normal 模式（之後兩種一樣）
          讀 grub.cfg、選單、載入 kernel
```

關鍵：GRUB 的「入口」適應韌體，但進入 normal 模式後，兩種的行為一樣（讀 grub.cfg、選單、載入 kernel）。差異全在「韌體怎麼啟動 GRUB」這個入口。

## BIOS GRUB：MBR + MBR gap

BIOS 下，GRUB 用 Ch 5 的 MBR 結構和 MBR gap：

```
BIOS GRUB 的磁碟佈局：

  Sector 0 (MBR):
    boot.img（446 bytes，放 MBR 的 boot code 區）
    + partition table（64B）+ signature（2B）
        │
  Sector 1 ~ 第一個分區之前（MBR gap，通常約 1MB）:
    core.img（GRUB core + 嵌入的模組）
        │
  分區（/boot 在某個分區）:
    /boot/grub/（grub.cfg、其他模組）

開機流程：
  韌體載 MBR 的 boot.img 到 0x7C00（Ch 5）
        │
  boot.img 載入 MBR gap 的 core.img
        │
  core.img 進 normal 模式，讀 /boot/grub/grub.cfg
```

> **MBR gap 的關鍵作用**（Ch 5 提過）：boot.img 只有 446 bytes，放不下 GRUB core。所以 core.img 放在「MBR 和第一個分區之間的空隙」（MBR gap）。這就是為什麼分區不該從 sector 1 開始——要留 gap 給 bootloader。現代分區工具（gdisk/parted）預設第一個分區從 sector 2048 開始（留 1MB gap），就是為了 GRUB 這種需求。

```bash
# 安裝 BIOS GRUB
sudo grub-install --target=i386-pc /dev/sda
#                 ───────────────  ─────────
#                 BIOS 模式         整個磁碟（裝到 MBR + gap）
#   注意是 /dev/sda（整個磁碟），不是 /dev/sda1（分區）
#   因為 boot.img 要寫 MBR，core.img 要寫 MBR gap
```

## UEFI GRUB：ESP 上的 .efi

UEFI 下，GRUB 是 ESP 上的一個 `.efi`（Ch 10/13）：

```
UEFI GRUB 的佈局：

  ESP（/boot/efi，FAT32）:
    /EFI/<distro>/grubx64.efi    ← GRUB 的 UEFI application
                                    （這就是 core.img 包成 .efi）
    /EFI/<distro>/grub.cfg       ← 可能有一個小的 grub.cfg（指向真正的）
    （或 shimx64.efi，Secure Boot，Ch 27）
        │
  /boot/grub/:
    grub.cfg（真正的設定）、模組
        │
  NVRAM:
    Boot#### 變數指向 /EFI/<distro>/grubx64.efi（Ch 15）

開機流程：
  韌體讀 BootOrder → 執行 /EFI/<distro>/grubx64.efi（Ch 10）
        │
  grubx64.efi（含 GRUB core）跑起來，進 normal 模式
        │
  讀 /boot/grub/grub.cfg，選單，載入 kernel
```

```bash
# 安裝 UEFI GRUB
sudo grub-install --target=x86_64-efi \
    --efi-directory=/boot/efi \        # ESP 掛載點
    --bootloader-id=ubuntu             # /EFI/ubuntu/
#   它會：
#   1. 把 grubx64.efi 放到 /boot/efi/EFI/ubuntu/
#   2. 用 efibootmgr 建一個 NVRAM 開機項指向它（Ch 15）
#   注意：不需要指定 /dev/sda（UEFI 不寫 MBR，寫 ESP 檔案）
```

## BIOS vs UEFI GRUB 對照

| 面向 | BIOS GRUB | UEFI GRUB |
|---|---|---|
| GRUB target | `i386-pc` | `x86_64-efi` |
| 入口 | MBR 的 boot.img（446B）| ESP 的 grubx64.efi |
| core 放哪 | MBR gap | ESP 的 .efi 檔案 |
| 安裝目標 | `/dev/sda`（整磁碟）| `/boot/efi`（ESP）|
| 開機項註冊 | 無（靠 MBR）| efibootmgr 寫 NVRAM（Ch 15）|
| 模組位置 | `/boot/grub/i386-pc/` | `/boot/grub/x86_64-efi/` |
| Secure Boot | 不支援 | 支援（透過 shim，Ch 27）|

> **同一台機器只能是其中一種**（除非 hybrid，Ch 11 的不建議方案）。你的系統是 BIOS 開機就裝 i386-pc GRUB，UEFI 開機就裝 x86_64-efi GRUB。`grub-install` 的 target 要對應韌體模式。裝錯（UEFI 系統裝 i386-pc）開不了機。判斷韌體模式：`[ -d /sys/firmware/efi ] && echo UEFI || echo BIOS`（Ch 1）。

## 為什麼 UEFI GRUB 更簡單

對比兩種安裝，UEFI GRUB 概念上更簡單：

```
BIOS GRUB 的複雜性：
  - boot.img 要寫 MBR（446B 限制）
  - core.img 要寫 MBR gap（要有足夠 gap）
  - 嵌入的模組要對（讀 /boot 的驅動）
  - 沒有開機項管理（靠 MBR 的 active 旗標或直接 MBR）

UEFI GRUB 的簡單性：
  - grubx64.efi 就是個檔案，放 ESP（FAT，韌體能讀）
  - 不用擠 446B、不用 MBR gap
  - efibootmgr 管理開機項（結構化、可程式化，Ch 15）
  - 韌體直接執行 .efi（不用 boot.img 載入 core.img 的兩步）
```

這呼應 Part 2/3 的整體對照：UEFI 的「檔案 + 韌體服務」模型比 BIOS 的「512B sector + 自己求生」模型乾淨。GRUB 的 UEFI 版享受了這個好處。

## 故意對照：grub-install 在兩種模式裝錯

```bash
# 錯誤：UEFI 系統用 BIOS target
sudo grub-install --target=i386-pc /dev/sda
# 它會嘗試寫 MBR + MBR gap
# 但 UEFI 系統的磁碟是 GPT，可能沒有 MBR gap（protective MBR 佔了）
# → grub-install 可能報錯，或裝了但 UEFI 韌體不會執行 MBR 的東西
# → 開不了機

# 錯誤：BIOS 系統用 UEFI target
sudo grub-install --target=x86_64-efi --efi-directory=/boot/efi
# BIOS 系統沒有 ESP、沒有 NVRAM 開機項機制
# → grub-install 報錯（找不到 efi 變數）或裝了但 BIOS 不會讀 ESP

# 正確：先判斷韌體模式，用對的 target
[ -d /sys/firmware/efi ] && TARGET=x86_64-efi || TARGET=i386-pc
```

target 和韌體模式不符是常見的開機失敗原因（尤其重灌、修復 GRUB 時）。永遠先確認系統是 BIOS 還是 UEFI 開機，用對應的 target。

## 踩雷集錦

1. **target 和韌體模式不符**：UEFI 系統裝 i386-pc，或 BIOS 系統裝 x86_64-efi。開不了機。先判斷韌體模式（`/sys/firmware/efi`）

2. **BIOS GRUB 裝到分區而非整磁碟**：`grub-install /dev/sda1`（分區）而非 `/dev/sda`（整磁碟）。boot.img 要寫 MBR（整磁碟的 sector 0），裝分區是錯的

3. **GPT 磁碟用 BIOS GRUB 但沒有 BIOS boot partition**：GPT + BIOS GRUB 需要一個特殊的「BIOS boot partition」（type EF02）放 core.img（因為 GPT 沒有 MBR gap 的傳統空間）。沒有它 grub-install 失敗

4. **UEFI GRUB 但 ESP 沒掛載**：`grub-install` 要 ESP 掛在 `/boot/efi`。ESP 沒掛載，grub-install 找不到地方放 grubx64.efi

5. **改了韌體模式（BIOS↔UEFI）沒重裝 GRUB**：在 BIOS 裝的系統，主機板改成 UEFI 開機，原本的 BIOS GRUB 不會被執行。要重裝對應 target 的 GRUB

## 進階：GPT + BIOS 的 BIOS boot partition

一個容易踩的組合：GPT 磁碟 + BIOS 開機。問題：GPT 沒有 MBR 的傳統 gap（protective MBR 佔了 sector 0），core.img 沒地方放。

```
GPT + BIOS GRUB 的解法：BIOS boot partition

  在 GPT 上建一個特殊分區：
    type GUID = 21686148-6449-6E6F-744E-656564454649
    （BIOS boot partition，gdisk 代碼 EF02）
    大小約 1MB，不格式化
        │
  grub-install 把 core.img 寫進這個分區（而非 MBR gap）
        │
  → GPT 磁碟也能用 BIOS GRUB
```

```bash
# 為 GPT + BIOS 建 BIOS boot partition
sudo sgdisk -n 1:0:+1M -t 1:EF02 /dev/sda   # EF02 = BIOS boot partition
# 然後 grub-install --target=i386-pc 會用它放 core.img
```

> 這個 BIOS boot partition 是「想在 GPT 磁碟用 BIOS 開機」的解法。它解決「GPT 沒有 MBR gap」的問題。如果你看到一個 1MB 的未格式化 EF02 分區，那就是它。現代多數 GPT 系統用 UEFI 開機（不需要這個），但 GPT + BIOS 的組合（如某些舊主機板配大磁碟）需要它。理解它能解釋這個看似多餘的小分區。

## 動手練習

1. 判斷你系統的 GRUB 模式：`ls /boot/grub/`（有 `i386-pc/` 是 BIOS，有 `x86_64-efi/` 是 UEFI），對照 `[ -d /sys/firmware/efi ]` 的判斷

2. UEFI 系統：看 `ls /boot/efi/EFI/<distro>/`，找 grubx64.efi（可能還有 shimx64.efi）。`efibootmgr -v` 看開機項指向它

3. 看 grub-install 的選項：`grub-install --help`，找 `--target`、`--efi-directory`、`--bootloader-id`，理解兩種模式的不同參數

4. 看磁碟佈局：`sudo gdisk -l /dev/sda`，如果是 BIOS+GPT，找有沒有 EF02（BIOS boot partition）；如果是 UEFI，找 EF00（ESP）

## 本章重點整理

- 同一個 GRUB 適應兩種韌體入口：BIOS 用 MBR boot.img + MBR gap 的 core.img；UEFI 用 ESP 的 grubx64.efi
- BIOS GRUB：`--target=i386-pc`，裝到 `/dev/sda`（整磁碟），core.img 在 MBR gap，無開機項管理
- UEFI GRUB：`--target=x86_64-efi`，裝到 `/boot/efi`（ESP），efibootmgr 寫 NVRAM 開機項，支援 Secure Boot
- 進入 normal 模式後兩種行為一樣（讀 grub.cfg、選單、載入 kernel）；差異全在入口
- GPT + BIOS 需要 BIOS boot partition（EF02）放 core.img（GPT 沒有 MBR gap）

## 自我檢核

- [ ] 能說出 BIOS GRUB 和 UEFI GRUB 在「韌體怎麼啟動 GRUB」上的差異
- [ ] 知道 BIOS GRUB 的 core.img 放哪（MBR gap）、UEFI GRUB 放哪（ESP 的 .efi）
- [ ] 知道兩種模式 grub-install 的不同（target、安裝目標、開機項）
- [ ] 能判斷系統該用哪種 GRUB target，裝錯會怎樣
- [ ] 知道 GPT + BIOS 為什麼需要 BIOS boot partition

## 延伸閱讀

### 官方文件

- **[GNU GRUB Manual: Installation](https://www.gnu.org/software/grub/manual/grub/grub.html#Installation)**
  - **讀哪裡**：installation 那節，BIOS 和 UEFI 的不同
  - **學什麼**：grub-install 的所有選項和兩種模式的細節
  - **前提**：本章 + Ch 18

### 部落格 / 文章

- **[Arch Wiki: GRUB (BIOS vs UEFI)](https://wiki.archlinux.org/title/GRUB)**
  - **讀哪裡**：BIOS systems 和 UEFI systems 兩節，以及 GPT/MBR 的組合
  - **學什麼**：各種韌體+分區表組合的 GRUB 安裝，包括 BIOS boot partition
  - **前提**：本章

→ [Ch 20 Multiboot 與 kernel handover protocol](./20-multiboot-handover.md)
