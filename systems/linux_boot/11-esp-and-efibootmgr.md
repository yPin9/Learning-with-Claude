# Ch 11 — ESP、efibootmgr、NVRAM 變數

> 目標：搞清楚 ESP 是什麼、bootloader 放哪裡、NVRAM 變數怎麼描述「從哪開機」、`efibootmgr` 怎麼用。

## 我們在哪裡

第 2 階段（Firmware）跟第 3 階段（Bootloader）的接縫。對照 Ch 5（BIOS 的 MBR + partition table）。

## ESP 是什麼

ESP = **EFI System Partition**。是 GPT 磁碟上一個特殊分割區：

- **檔案系統**：FAT32（spec 規定）
- **partition type GUID**：`C12A7328-F81F-11D2-BA4B-00A0C93EC93B`
- **大小**：通常 100MB ~ 500MB
- **必要性**：UEFI 開機**必須**有 ESP

UEFI firmware 開機時做這件事：

1. 掃所有磁碟，找 GPT 上 type GUID 是 ESP 的 partition
2. mount FAT32（韌體內建 FAT32 driver）
3. 根據 NVRAM 變數找指定的 `.efi` 檔
4. 載入並執行

ESP 在 Linux 上通常 mount 在 `/boot/efi/`：

```bash
mount | grep efi
# /dev/sda1 on /boot/efi type vfat (rw,relatime,...)
```

## ESP 的標準目錄結構

```
/boot/efi/
└── EFI/
    ├── BOOT/
    │   └── BOOTX64.EFI       # fallback bootloader
    ├── ubuntu/
    │   ├── grubx64.efi       # Ubuntu 的 GRUB
    │   ├── shimx64.efi       # Secure Boot shim
    │   └── grub.cfg
    ├── debian/
    │   └── ...
    ├── Microsoft/
    │   └── Boot/
    │       └── bootmgfw.efi   # Windows
    └── systemd/
        └── systemd-bootx64.efi
```

幾個重要約定：

- **`EFI/BOOT/BOOTX64.EFI`**：fallback。當 NVRAM 沒設指定的 boot entry 時，UEFI 會找這個。新 install USB 用這個放 installer
- **每個 OS 一個資料夾**：UEFI spec 推薦 `EFI/<vendor>/`，避免互相覆蓋
- **`.efi` 檔是 PE/COFF**：可以直接被 UEFI 執行

## NVRAM 變數

UEFI 把開機設定存在主機板上一塊 **NVRAM**（non-volatile RAM，通常是 SPI flash）。每個變數有：

- **名稱**：UTF-16 字串，如 `Boot0001`、`BootOrder`
- **GUID**：分組用，避免名稱衝突
- **屬性**：non-volatile / runtime accessible / boot service only
- **值**：bytes

開機相關變數都在 GUID `8be4df61-93ca-11d2-aa0d-00e098032b8c` 下，常用的：

| 變數 | 內容 |
|---|---|
| `BootOrder` | 嘗試開機順序，如 `0001 0003 0002` |
| `BootCurrent` | 這次開機用的 entry |
| `BootNext` | 下次開機強制用這個 (one-shot) |
| `Boot0000` ~ `BootFFFF` | 個別 entry，描述「從哪個 .efi 開機」 |
| `Timeout` | menu 等待秒數 |

每個 `BootXXXX` 變數的內容是一個結構：description + device path + optional data。**device path** 描述硬體路徑：

```
PciRoot(0x0)/Pci(0x1F,0x2)/Sata(0,0,0)/HD(1,GPT,xxxx-uuid,0x800,0x100000)/File(\EFI\ubuntu\grubx64.efi)
```

讀法：「PCI bus 0 → device 0x1F func 2 → SATA controller → 第 1 個 GPT partition (UUID xxx) → 檔案 `\EFI\ubuntu\grubx64.efi`」。

## efibootmgr

操作 NVRAM boot 變數的標準工具。常用命令：

```bash
# 列所有 entry
sudo efibootmgr -v

# 改 boot order
sudo efibootmgr -o 0001,0003,0002

# 設定 BootNext (下次開機只用一次)
sudo efibootmgr -n 0003

# 新增 entry
sudo efibootmgr -c \
    -d /dev/sda \
    -p 1 \
    -L "MyLoader" \
    -l '\EFI\mine\my.efi'

# 刪除 entry
sudo efibootmgr -b 0005 -B

# 改 description
sudo efibootmgr -b 0001 -L "New Name"
```

`-c` (create) 的參數：
- `-d` 磁碟
- `-p` partition number (從 1 開始)
- `-L` 顯示名稱
- `-l` ESP 內檔案路徑（注意是反斜線）

## efibootmgr 完整輸出範例

```
BootCurrent: 0001
Timeout: 0 seconds
BootOrder: 0001,0000,0003,0002
Boot0000* Windows Boot Manager  HD(1,GPT,...)/\EFI\Microsoft\Boot\bootmgfw.efi
Boot0001* ubuntu                HD(1,GPT,...)/\EFI\ubuntu\shimx64.efi
Boot0002* UEFI:CD/DVD Drive     ...
Boot0003* UEFI:Removable Device ...
```

讀法：

- `BootCurrent: 0001` — 這次開機用了 ubuntu
- `Timeout: 0` — 不顯示選單，直接用 BootOrder 第一個
- `BootOrder: 0001,0000,...` — 試 ubuntu，失敗再試 Windows，再試光碟、USB
- `*` 表示 entry active

## 寫 NVRAM 變數的低層方法

`efibootmgr` 透過 `efivarfs`（mount 在 `/sys/firmware/efi/efivars/`）操作。每個變數是檔案：

```bash
ls /sys/firmware/efi/efivars/ | grep ^Boot
# Boot0000-8be4df61-93ca-11d2-aa0d-00e098032b8c
# Boot0001-8be4df61-93ca-11d2-aa0d-00e098032b8c
# BootOrder-8be4df61-93ca-11d2-aa0d-00e098032b8c
```

直接讀：

```bash
sudo cat /sys/firmware/efi/efivars/BootOrder-8be4df61-* | xxd
# 前 4 byte 是 attribute，之後是 boot order list（each 2 byte）
```

**寫**這些檔案會直接改 NVRAM。寫壞了主機板可能不開機（要 jumper / 拆電池清 NVRAM）。所以平常用 `efibootmgr` 比較安全。

## ESP 沒有 fallback path 會怎樣

如果你的 ESP 沒有 `EFI/BOOT/BOOTX64.EFI`，而 NVRAM 也清空了，會發生：

- 韌體找不到任何 boot entry
- 大部分 firmware 直接顯示 setup UI 或 "No boot device"
- 一些 firmware 會試 PXE / network boot

**install USB / Live USB 都把 bootloader 放在 `EFI/BOOT/BOOTX64.EFI`**，所以不需要 NVRAM 設定就能開。Linux 安裝完成後 installer 才會用 efibootmgr 註冊正式的 entry。

## 一個常見踩雷：HP / Dell 把 efibootmgr 寫的 entry 自動刪掉

部分 OEM 韌體有 bug：開機時會「整理」NVRAM，把它認不得的 entry 刪掉，只留自己的。Linux distro 的 entry 開機後消失。

workaround：把 bootloader 改名為 `EFI/BOOT/BOOTX64.EFI` 利用 fallback；或用 systemd-boot 而不是 GRUB；或寫廠商 bug report。

## 一個常見踩雷：在 BIOS 模式裝 Linux，後來想轉 UEFI

不能輕易轉。UEFI 模式需要 GPT partition table + ESP，BIOS 模式通常用 MBR。轉換需要：

1. 把 MBR 轉 GPT（`gdisk` 的 `r` → `g` 命令）
2. 切一塊 ESP（>= 100MB FAT32）
3. 重灌 GRUB 為 UEFI 版本（`grub-install --target=x86_64-efi`）
4. 用 efibootmgr 註冊

實務上重裝會比較簡單。

## 動手練習

**1. 看你機器的 boot entry**

```bash
sudo efibootmgr -v
```

對照 ESP 內容：

```bash
ls /boot/efi/EFI/
sudo find /boot/efi -name "*.efi"
```

每個 NVRAM entry 對應到哪個 `.efi`？

**2. 改 boot order，再改回來**

記下原本的 `BootOrder`：

```bash
sudo efibootmgr | grep BootOrder
# BootOrder: 0001,0000,0003
```

倒過來：

```bash
sudo efibootmgr -o 0003,0000,0001
```

確認：

```bash
sudo efibootmgr | grep BootOrder
```

**改回來**：

```bash
sudo efibootmgr -o 0001,0000,0003
```

**這個動作不會弄壞系統**，但下次開機會試新順序，不確定的話就先改回原樣再 reboot。

**3. 在 OVMF 環境玩 NVRAM**

OVMF 用獨立的 `OVMF_VARS.fd` 存 NVRAM，你可以隨便玩不會影響真機：

```bash
cp /usr/share/OVMF/OVMF_VARS.fd /tmp/test_VARS.fd
qemu-system-x86_64 -m 256 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=/tmp/test_VARS.fd \
  -nographic
```

進 firmware setup 加 boot entry、改 order，全部寫進 `/tmp/test_VARS.fd`。下次起 QEMU 帶這個檔案能看到變更。

## 自我檢核

- [ ] 講得出 ESP 的檔案系統、partition GUID、典型 mount point
- [ ] 知道 `EFI/BOOT/BOOTX64.EFI` 是什麼
- [ ] 知道 BootOrder / BootCurrent / BootNext 各自意義
- [ ] 用 `efibootmgr -v` 看自己機器的 entry
- [ ] 知道 device path 描述什麼

下一章寫一支 minimal UEFI app，自己編譯 + 在 OVMF 跑。

→ [Ch 12 動手：用 gnu-efi 寫 minimal UEFI app](./12-minimal-uefi-app.md)
