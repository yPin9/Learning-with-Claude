# Ch 22 — 取得韌體：dump 與解包

> **目標**：讓你在拿到一個目標裝置後，知道用什麼管道取得韌體 image、取得後如何解包與判斷結構，建立「拿到一坨 bin 之後的決策流程」——這是 Part 4 逆向工作的最前置步驟。

逆向韌體前先得有韌體。聽起來廢話，但實際上「怎麼拿到 image」決定了你能拿到多乾淨的資料、花多少時間、需不需要硬體。本章把管道分成六類，從最簡單（廠商官網）到最複雜（硬體 SPI dump），依序講。

---

## 為什麼取得韌體這麼重要

韌體逆向的敵人不是 IDA 或 Ghidra，而是「你看到的 bin 是不是真正跑在裝置上的東西」。廠商公開的 update package、OS 端讀出的 dump、從 flash chip 直接吸出的 image，這三者在某些裝置上完全一樣，在某些裝置上差了好幾層封裝。把封裝搞錯，整個逆向方向就走偏。

---

## 管道一：廠商 update package（最簡單）

### BIOS .exe 提取

PC 主機板廠商（ASUS、Gigabyte、MSI、Lenovo、Dell）通常在官網提供 Windows .exe 形式的 BIOS 更新工具。這個 .exe 裡面打包了真正的 ROM image。

```bash
# WSL 環境，先下載 BIOS 更新 .exe
# 用 7-Zip 或 binwalk 直接解它

# 方法一：binwalk（真跑）
binwalk BIOS-update.exe

# 典型輸出（示範）：
# DECIMAL   HEXADECIMAL  DESCRIPTION
# 0         0x0          PE32 executable
# 524288    0x80000      LZMA compressed data
# 8388608   0x800000     UEFI firmware volume, GUID: ...
```

有些廠商 .exe 用 7-Zip SFX 格式自解壓，直接：

```bash
7z x BIOS-update.exe -o./extracted/
# 或
cabextract BIOS-update.exe
```

解出來通常有 `.bin`、`.cap`、`.rom`、`.fd` 副檔名的 image。

### .cap 格式（ASUS）

ASUS 的 BIOS update 包是 `.cap` 格式，它是 UEFI Capsule Update 的一種封裝（見下面管道二）：

```
.cap 檔案佈局：
  [EFI_CAPSULE_HEADER]       ← 標準 UEFI capsule header
  [CAPSULE_PAYLOAD_HEADER]   ← ASUS 自定義 header
  [ROM IMAGE]                ← 真正的 SPI flash 內容
```

用 `binwalk` 或 `uefi-firmware-parser` 可以穿透到 ROM IMAGE。

### Dell / HP 的 BIOS update

Dell 用 `.exe` 包 WinPE 環境，裡面有 `.rcv` 或 `.hdr` 格式的 BIOS image，7-Zip 解包後再跑 `binwalk`。HP 類似，有時需要找 `HPBIOSUPDREC.exe` 或 `hpqFlash` 工具附帶的 `.bin`。

---

## 管道二：UEFI Capsule 提取

UEFI Capsule Update（spec 定義在 UEFI 2.10 §23）是標準的韌體更新機制。Capsule image 本身是一個有 header 的 binary，後面接著真正的 firmware volume。

### Capsule header 結構

```
EFI_CAPSULE_HEADER：
  GUID      CapsuleGuid;          // 識別 capsule 類型
  UINT32    HeaderSize;           // header 大小
  UINT32    Flags;                // CAPSULE_FLAGS_POPULATE_SYSTEM_TABLE 等
  UINT32    CapsuleImageSize;     // 總大小（含 header）
```

常見 GUID：
- `BD57621C-A594-4315-9AA7-3160A0AA58BC`：Windows UX capsule
- `6DCBD5ED-E82D-4C44-BDA1-7194199AD92A`：EFI System Resource Table（ESRT）entry

### 提取方法

```bash
# binwalk 自動辨識 capsule header
binwalk -e firmware.cap

# 或用 python uefi_firmware（見管道六的真跑示範）
python3 -c "
import uefi_firmware
with open('firmware.cap', 'rb') as f:
    data = f.read()
parser = uefi_firmware.AutoParser(data)
parser.parse()
parser.dump('output_dir/')
"
```

---

## 管道三：從 OS 端讀 SPI flash

這一節有多個子管道，從完全軟體到需要真實硬體，逐一說明。

### 3a：Intel Flash Programming Tool（FPT）

Intel 提供 `fptw64.exe`（Windows）和 `fpt`（Linux），是原廠工具，能在已支援的 Intel 平台從 OS 端透過 SPI controller MMIO 讀取 SPI flash 內容。

**注意**：新版 Intel 平台（12th Gen+）預設有 BIOS Lock，FPT 無法讀 BIOS region，只能讀 ME region 或 GbE region，BIOS region 讀到全 FF。要讀 BIOS region 需要先透過 BIOS 介面關掉 BIOS Lock（某些廠商 BIOS 提供 Flash Programming Mode）。

```bash
# Windows（未實測，需要管理員）
fptw64.exe -d bios_dump.bin -BIOS   # 只讀 BIOS region
fptw64.exe -d full_dump.bin          # 讀整個 SPI flash（含 ME/GbE）

# 失敗常見錯誤
# Error 167: Protected Range Registers are set
# → BIOS 保護生效，只能讀未被保護的 region
```

### 3b：AFUWIN（AMI Firmware Update）

AMI UEFI 韌體常用 AFUWIN/AFUDOS 工具。在 DOS 或 WinPE 下執行，能繞開部分 BIOS Lock：

```
AFUDOS.exe BIOS.ROM /O          # 讀出（Output）
AFUWIN.exe BIOS.ROM /O /GAN     # 讀出，包含 GbE/ME region
```

**未實測**：真實效果依廠商 BIOS 設定而異，Protected Range Registers 啟用後仍然失敗。

### 3c：chipsec spi dump（未安裝）

CHIPSEC 提供 SPI dump 功能，繞過 OS 直接存取 SPI controller 暫存器：

```bash
# 未安裝，以下為指令格式，非真跑輸出
sudo python chipsec_main.py -m common.spi.spi_protected_ranges
sudo python chipsec_util.py spi dump spi.bin
```

CHIPSEC 的 `spi dump` 透過 `/dev/mem`（需要 kernel module）存取 SPI flash 控制器的 MMIO 暫存器，效果比 FPT 更底層，但依然受 Protected Range Registers 約束。

**未實測**：本課環境未安裝 CHIPSEC。原理參考 `chipsec/hal/spi.py`，關鍵函數是 `read_spi_to_file()`，它直接操作 SPI Host Interface Registers（`SPI_BASE + 0x50`）。

### 3d：Linux /dev/mtd 與 sysfs

嵌入式 Linux 裝置（路由器、IoT、工業控制器）通常把 flash 暴露成 `/dev/mtd*`：

```bash
# 查看 MTD 分區
cat /proc/mtd
# 輸出範例：
# dev:  size   erasesize  name
# mtd0: 00040000 00010000 "boot"
# mtd1: 00010000 00010000 "env"
# mtd2: 00700000 00010000 "firmware"
# mtd3: 00800000 00010000 "rootfs"

# dump 韌體分區（裝置上跑，或 adb shell）
dd if=/dev/mtd2 of=/tmp/firmware.bin bs=65536
# 或
cat /dev/mtd2ro > /tmp/firmware.bin

# 複製到本機
adb pull /tmp/firmware.bin .
# 或
scp root@192.168.1.1:/tmp/firmware.bin .
```

有些平台用 `/dev/mmcblk0p*`（eMMC）暴露分區，邏輯相同。

---

## 管道四：coreboot cbfstool

裝了 coreboot 的機器（Chromebook、部分 Purism/System76）可以用 `cbfstool` 直接操作 CBFS（Coreboot File System）：

```bash
# 安裝 cbfstool（WSL 下從源碼編）
git clone https://review.coreboot.org/coreboot.git --depth 1
cd coreboot/util/cbfstool && make
sudo make install

# 讀出整個 coreboot ROM
# 需要先用 flashrom 或其他方式取得 coreboot.rom

# 列出 CBFS 內容
cbfstool coreboot.rom print

# 輸出示意：
# Name                      Offset      Type     Size
# fallback/romstage         0x80        stage    98304
# fallback/ramstage         0x18140     stage    532344
# vbt.bin                   0x9c440     raw      2408
# config                    0x9cdc0     raw      3108

# 提取特定模組
cbfstool coreboot.rom extract -n fallback/ramstage -f ramstage.elf
```

Chromebook 有 Google Security Chip（GSC）保護 SPI，用 `flashrom` 從 OS 讀通常只能讀未保護的 region。要完整 dump 需要 SuzyQ Debug Cable（Google 官方 debug adapter）或拆機接 SPI programmer。

---

## 管道五：ARM 端韌體取得

### 5a：eMMC/UFS 直接 dump

嵌入式裝置（手機、路由器、機上盒）把所有分區存在 eMMC 或 UFS。取得 root 後：

```bash
# 找 eMMC 裝置
ls /dev/mmcblk*
# 常見：/dev/mmcblk0（eMMC），/dev/mmcblk0p1, p2... （分區）

# dump 特定分區（以 abl/bootloader 為例）
adb shell
cat /proc/partitions      # 看分區佈局
dd if=/dev/block/by-name/abl of=/data/local/tmp/abl.bin bs=1M
adb pull /data/local/tmp/abl.bin .

# 完整 eMMC dump（慢，依容量而定）
dd if=/dev/mmcblk0 of=/data/local/tmp/full.bin bs=4M
```

MTK 平台的分區名有時叫 `preloader`、`lk`、`boot`、`vendor_boot`，對應 boot chain 不同階段（見 [Ch 20](./20-mtk-vendor-soc.md)）。

### 5b：mtkclient BROM dump（未實測硬體）

mtkclient 透過 MTK SoC 的 BROM USB 下載協定，在 BROM 模式下可以讀寫 eMMC：

```bash
pip install mtkclient
# 裝置進入 BROM 模式（通電前按 Vol+ Vol- 或短接 BROM test point）
python mtk.py rf full_dump.bin   # 讀整個 eMMC
python mtk.py rl partitions.txt  # 讀所有分區
```

**未實測**：需要真實 MTK 裝置 + 進入 BROM 模式 + SBC_EN=0（或 BROM exploit payload）。詳見 [Ch 20](./20-mtk-vendor-soc.md) 的 mtkclient 段落。

### 5c：fastboot

Android 裝置解鎖 bootloader 後可用 fastboot dump 分區：

```bash
fastboot getvar all           # 查裝置資訊
fastboot oem dump partition   # 部分廠商支援

# 更通用：adb root + dd（需要 userdebug 或 rooted 裝置）
adb root
adb shell dd if=/dev/block/by-name/boot of=/data/local/tmp/boot.img bs=4M
adb pull /data/local/tmp/boot.img .

# 解包 boot.img
sudo apt install android-tools-mkbootimg
unpackbootimg -i boot.img -o boot_out/
# 得到 kernel（zImage）、ramdisk（initrd）等
```

### 5d：JTAG / SPI programmer dump（未實測硬體）

要完整控制的終極手段。JTAG 讓你在 CPU 暫停狀態下讀記憶體，SPI programmer 直接從 flash chip 吸：

```
SPI dump 流程（未實測）：
  1. 找到 SPI flash chip（通常是 Winbond W25Q 系列）
  2. 接 CH341A 或 Bus Pirate + SOP8 夾子
  3. flashrom 讀取：
     flashrom -p ch341a_spi -r dump.bin
  4. 比對 ID：
     flashrom -p ch341a_spi --flash-name

JTAG dump 流程（未實測）：
  1. 找 JTAG 測試點（TCK/TMS/TDI/TDO/TRST/GND）
  2. 接 OpenOCD + J-Link/FTDI 轉接器
  3. openocd -f target/xxx.cfg
     > halt
     > dump_image mem.bin 0x00000000 0x10000000
```

---

## 管道六：從量產 image 網路抓

廠商不一定直接提供 ROM bin，但很多地方藏著 image：

| 來源 | 說明 | 工具 |
|------|------|------|
| LVFS（fwupd） | `https://fwupd.org/lvfs/devicelist`，含大量 PC BIOS capsule | `fwupdmgr get-updates; fwupdmgr download` |
| 廠商 FTP/CDN | 很多廠商 CDN 暴露，URL 結構可猜 | `wget`, `curl` |
| OTA update 攔截 | 手機更新流量 MITM，抓 `.zip` 或 `.payload` | Frida + mitmproxy |
| ROM sharing 社群 | XDA、4PDA 等，注意 repack 修改 | 人工下載 |
| Android OTA payload | `payload.bin` 包含所有分區 | `payload-dumper-go` |

```bash
# Android OTA payload 解包（真跑）
# 先安裝 payload-dumper-go
wget https://github.com/ssut/payload-dumper-go/releases/latest/download/payload-dumper-go_linux_amd64.tar.gz
tar xf payload-dumper-go_linux_amd64.tar.gz
chmod +x payload-dumper-go

# 解包（假設你有 OTA zip）
unzip ota_update.zip payload.bin
./payload-dumper-go payload.bin
# 輸出：boot.img, system.img, vendor.img ... 到 ./output/ 目錄
```

---

## 解包與分析工具

取得 raw binary 之後，下一步是拆開它看裡面有什麼。

### binwalk（真跑）

binwalk 是最常用的韌體分析工具，用 magic bytes + entropy 分析自動偵測壓縮段、filesystem、ELF、UEFI FV 等。

```bash
# 安裝（WSL Ubuntu 22.04）
sudo apt install binwalk

# 基本分析：顯示發現的資料結構
binwalk firmware.bin

# 輸出示例（UEFI ROM）：
# DECIMAL    HEXADECIMAL  DESCRIPTION
# 0          0x0          LZMA compressed data, ...
# 65536      0x10000      Intel/EFI Firmware Volume, GUID: ...
# 131072     0x20000      Intel/EFI Firmware Volume, GUID: ...
# 6225920    0x5F0000     Intel Microcode, version 0x..., ...

# 自動提取所有找到的東西
binwalk -e firmware.bin
# 提取結果放在 _firmware.bin.extracted/

# 遞迴提取（多層封裝）
binwalk -Me firmware.bin

# 只看 entropy 圖（判斷加密/壓縮區段）
binwalk -E firmware.bin
# 高 entropy（≈1.0）= 壓縮或加密
# 低 entropy（≈0.0）= 空白或固定 pattern
# 中 entropy（≈0.6-0.8）= 程式碼或資料
```

### entropy 圖判讀

```
entropy 值含義：
 0.00 ── 全零或重複 byte（未使用 flash 空間）
 0.40 ── 英文文字、未壓縮程式碼
 0.70 ── 典型 ARM/x86 程式碼 mix
 0.85 ── LZMA/zlib 壓縮資料
 0.99 ── AES 加密資料 or 真隨機

看到 entropy 圖裡有「高平台然後突然降」的形狀，
通常是：[壓縮段][解壓後的程式碼]，或[加密段][padding]
```

實際跑：

```bash
# 對 OVMF_CODE.fd 跑 entropy（OVMF 是 WSL 環境已有的）
binwalk -E /usr/share/ovmf/OVMF_CODE.fd
# → 會生成 entropy 折線圖（需要 matplotlib）
# 若沒有 matplotlib：
pip3 install matplotlib
binwalk -E /usr/share/ovmf/OVMF_CODE.fd
```

### unblob

比 binwalk 更現代的替代工具，對嵌入式韌體（squashfs、cramfs、JFFS2 等）支援更好：

```bash
pip3 install unblob
# 或
docker run --rm -v $(pwd):/data ghcr.io/onekey-sec/unblob:latest /data/firmware.bin -o /data/output/

# 比 binwalk 好在：更少誤判、輸出結構更乾淨、支援更多格式
```

---

## 拿到一坨 bin 之後的決策流程圖

```
拿到 firmware.bin
        │
        ▼
 binwalk firmware.bin
        │
        ├─ 看到 UEFI Firmware Volume？
        │       │
        │       ▼
        │  UEFI ROM → 用 UEFITool / uefi-firmware-parser 解
        │              （見 Ch 23）
        │
        ├─ 看到 LZMA / zlib / LZ4 壓縮？
        │       │
        │       ▼
        │  binwalk -e 或 unblob 提取壓縮段
        │  再對提取出的檔案重新跑 binwalk
        │
        ├─ 看到 Linux cramfs / squashfs / JFFS2？
        │       │
        │       ▼
        │  unsquashfs / jefferson / cramfsck 拆出 rootfs
        │  找 bootloader（u-boot.bin、preloader）位置
        │
        ├─ 看到 Android boot.img / sparse image？
        │       │
        │       ▼
        │  unpackbootimg / simg2img 處理
        │
        ├─ entropy ≈ 1.0 全圖？
        │       │
        │       ▼
        │  高度懷疑加密 → 找廠商解密 key
        │  方向：① firmware 的 update binary 裡找 decrypt routine
        │         ② 已知平台找公開研究（MTK/瑞芯微/海思等各有已知 key）
        │         ③ UART/JTAG 取得執行中記憶體（解密後）
        │
        ├─ 看到 flat binary 無明顯結構？
        │       │
        │       ▼
        │  嘗試 Ghidra 載入（設定正確 load address 很重要）
        │  ARM：通常 0x80000000 或 0x00000000
        │  UEFI TE module：從 TE header 讀 BaseOfCode
        │
        └─ 什麼都沒看到？
                │
                ▼
         重新確認取得方式正確
         可能：① 只有外層 wrapper，沒解到真正的 payload
               ② 格式是廠商 proprietary，需要找廠商工具
               ③ 真的是加密的
```

---

## 各管道的可取得性速覽

| 管道 | 難度 | 需要硬體 | 能否取得完整 flash | 備註 |
|------|------|---------|-------------------|------|
| 廠商 update package | ★☆☆ | 否 | 通常是（BIOS region） | 最快路線，從這裡開始 |
| UEFI Capsule 提取 | ★☆☆ | 否 | 是（capsule payload） | 標準格式 |
| OS SPI（FPT/chipsec） | ★★☆ | 否（但需 root） | 否（BIOS region 通常鎖） | Protected Range 擋住 |
| Linux /dev/mtd | ★★☆ | 需要 root shell | 是（所有 MTD 分區） | 路由器/IoT 常用 |
| cbfstool（coreboot） | ★★☆ | 否 | 是（CBFS 範圍） | coreboot 平台 |
| mtkclient BROM | ★★★ | 是（BROM 模式） | 是（整個 eMMC） | 需要 SBC_EN=0 或 exploit |
| fastboot + adb dd | ★★☆ | 是（Android 裝置） | 是（各分區） | 需解鎖 bootloader |
| JTAG/SPI programmer | ★★★★ | 是（programmer） | 是（整個 flash） | 未實測，最強但最慢 |
| 網路抓/LVFS | ★☆☆ | 否 | 依廠商而定 | LVFS 最方便 |

---

## 真跑示範：binwalk 分析 OVMF

WSL 環境已安裝 OVMF，可以直接拿來練習：

```bash
# 確認 OVMF 位置
ls /usr/share/ovmf/
# OVMF_CODE.fd  OVMF_VARS.fd

# 基本掃描
binwalk /usr/share/ovmf/OVMF_CODE.fd

# 預期輸出（節錄）：
# DECIMAL    HEXADECIMAL  DESCRIPTION
# 0          0x0          Intel/EFI Firmware Volume, GUID: ...
# 65536      0x10000      Intel/EFI Firmware Volume, GUID: ...
# ...

# 提取全部
mkdir -p /tmp/ovmf_extracted
binwalk -e /usr/share/ovmf/OVMF_CODE.fd -C /tmp/ovmf_extracted/

# 看提取結果
ls /tmp/ovmf_extracted/
find /tmp/ovmf_extracted/ -name "*.efi" | head -20
# → 應該看到一堆 DXE module 的 .efi 檔案
```

用 `file` 命令確認提取出的模組：

```bash
find /tmp/ovmf_extracted/ -name "*.efi" -exec file {} \; | head -10
# 輸出示例：
# /tmp/ovmf_extracted/_OVMF_CODE.fd.extracted/10.efi: PE32+ executable (EFI application) x86-64
# /tmp/ovmf_extracted/_OVMF_CODE.fd.extracted/20.efi: PE32+ executable (EFI DXE driver) x86-64
```

這些就是後面 Ch 23 要用 `uefi_firmware` 和 UEFITool 進一步解析的東西。

---

## 踩雷

1. **binwalk -e 提取的不一定是正確 image**：binwalk 會對每個找到的 magic bytes 都試著提取，但有時 magic bytes 在壓縮資料中間「意外出現」，提取出垃圾。看到提取結果很詭異時，先用 `-v`（verbose）確認 magic bytes 是否在合理 offset。

2. **FPT 讀出全 0xFF 不代表沒有 BIOS**：Protected Range Registers（PR0-PR4）啟用後，SPI 控制器讀到 0xFF。這是保護在作用，不是 flash 是空的。要繞過 PR 需要進 SMM 或用硬體 programmer。

3. **廠商 update .exe 有時是解密前的 image**：某些廠商（海思、某些 MediaTek ODM）在 update binary 裡放的是加密 blob，update tool 在記憶體裡解密後才送到 flash。拿到 .exe 跑 binwalk 什麼都看不到就是這個情況。

4. **eMMC dd 完整需要巨大空間**：一顆 64GB eMMC 完整 dump 是 64GB。先用 `cat /proc/partitions` 找到你真正需要的分區（bootloader 通常在前面幾十 MB），只 dump 你要的部分。

5. **binwalk 的 entropy 圖需要 matplotlib**：`binwalk -E` 依賴 Python matplotlib。沒有的話先 `pip3 install matplotlib`，否則只印 entropy 數值而不生成圖。

6. **mtkclient 和真實裝置的 SBC_EN 狀態**：量產手機 SBC_EN=1，mtkclient 需要 BROM exploit payload 才能進入。開發板或工程機可能 SBC_EN=0，mtkclient 直接能操作。取到裝置先確認 efuse 狀態再決定路線，別假設兩者都能直接用。

---

## 進階延伸

- **自動化韌體爬蟲**：LVFS 有公開 API，可以寫腳本定期爬取新版 BIOS capsule，配合 binwalk 自動解包，建立個人的「韌體資料庫」方便 diffing（見 Ch 26）。

- **Android payload.bin 格式**：`payload.bin` 是 Chrome OS 的 update 格式移植到 Android，用 protobuf 定義分區元資料，`payload-dumper-go` 比 Python 版快很多，值得在工具箱裡常駐。

- **UFS 的 dump 限制**：高端手機已從 eMMC 換到 UFS（Universal Flash Storage），UFS 的存取協定比 eMMC 複雜，目前公開的 BROM 工具支援度不如 eMMC，硬體 dump 需要 UFS JTAG probe（例如 Riff Box 2）。

---

## 動手練習

**練習 1：binwalk 分析 OVMF**（真跑）

```bash
binwalk -e /usr/share/ovmf/OVMF_CODE.fd -C /tmp/ovmf_out/
ls /tmp/ovmf_out/
# 回答：找到幾個 Firmware Volume？最大的 FV 有多少 bytes？
```

**練習 2：entropy 分析**（真跑）

下載任一路由器韌體（OpenWRT 或廠商固件）：

```bash
wget https://downloads.openwrt.org/releases/23.05.5/targets/x86/64/openwrt-23.05.5-x86-64-combined-ext4.img.gz
gunzip openwrt-23.05.5-x86-64-combined-ext4.img.gz
binwalk -E openwrt-23.05.5-x86-64-combined-ext4.img
# 根據 entropy 圖，哪個 offset 範圍是壓縮資料？
```

**練習 3：決策流程實戰**（思維演練）

拿以下三種 bin，套用本章決策流程圖，列出你的分析步驟：
- A：`binwalk` 顯示第 0x10000 offset 有 UEFI Firmware Volume
- B：整個 bin 的 entropy 圖是 0.99 的平線
- C：`binwalk` 發現 LZMA compressed data 在 0x200，提取後又看到 squashfs

---

## 本章重點

- 取得韌體有六個管道：廠商 package、UEFI capsule、OS 端 SPI、Linux /dev/mtd、ARM eMMC/mtkclient、網路抓。從廠商 package 開始是最快路線。
- SPI flash 的 BIOS region 在現代平台幾乎都有保護（PR0-4），OS 端軟體工具通常讀不到，需要硬體 programmer 或 SMM 級別的 bypass。
- binwalk 是起點工具：magic bytes 掃描 + entropy 圖 + 自動提取，三個功能都要會用。
- entropy ≈ 1.0 代表加密，找 decrypt routine 或用 UART/JTAG 取執行時記憶體。
- 決策流程：拿到 bin → binwalk 掃描 → 判斷結構類型 → 用對應工具解包 → 遞迴直到拿到真正的 code。

---

## 自我檢核

- [ ] 能說出取得韌體的六個管道，並各舉一個適用場景
- [ ] 知道為什麼 FPT/chipsec 從 OS 讀 BIOS region 通常失敗（Protected Range Registers）
- [ ] 能用 `binwalk -e` 對 OVMF 提取並在輸出目錄找到 .efi 模組
- [ ] 能解釋 entropy 圖的高/中/低三種狀態各代表什麼
- [ ] 知道 Android OTA payload.bin 用什麼工具解包
- [ ] 拿到一個全 0.99 entropy 的 bin，知道下一步應該怎麼做

---

## 延伸閱讀

1. **LVFS（Linux Vendor Firmware Service）文件與 API**（`fwupd.org/lvfs`）
   讀哪裡：`https://lvfs.readthedocs.io/en/latest/` 的 API 章節，以及各裝置的 metainfo.xml 格式說明
   學什麼：理解 fwupd capsule 格式、ESRT（EFI System Resource Table）entry、如何爬取與解析 LVFS 資料庫作為韌體 diffing 的來源
   關聯：直接支持本章「網路抓」管道，以及 Ch 26（韌體 diffing）的資料來源

2. **binwalk 源碼與 magic database**（`github.com/ReFirmLabs/binwalk`）
   讀哪裡：`src/binwalk/magic/` 目錄下的 magic files，以及 `src/binwalk/modules/extractor.py`
   學什麼：binwalk 如何識別各種格式（magic bytes + offset + context）、如何新增自訂格式支援、extraction command 的設定方式
   關聯：理解 binwalk 的識別邏輯後，才能判斷哪些「發現」是真的、哪些是誤判，直接影響本章解包品質

3. **"Reverse Engineering Firmware" — Quarkslab blog（2022）**（`blog.quarkslab.com`，搜尋該標題）
   讀哪裡：完整系列，特別是第一篇「Obtaining and Unpacking Firmware」
   學什麼：嵌入式韌體（路由器/IoT）的取得方法、各廠商 OTA 格式的差異、unblob vs binwalk 的選擇場景、加密韌體的實戰繞過案例
   關聯：補足本章 ARM 管道的嵌入式細節，是本章 + Ch 25（ARM bootloader 逆向）的橋接閱讀

→ [下一章](./23-uefi-firmware-parsing.md)
