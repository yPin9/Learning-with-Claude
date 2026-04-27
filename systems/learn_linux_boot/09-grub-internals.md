# Ch 9 — GRUB 內部結構

> 目標：拆 GRUB 兩代版本，知道 stage 1 / 1.5 / 2 怎麼分工、`grub.cfg` 怎麼跑、模組怎麼載入。

## 我們在哪裡

第 3 階段 (Bootloader) 的真實版。前面 7 章從零寫了 hello boot sector + 模式切換，但實務上沒人手寫 bootloader — 大家用 GRUB。

## GRUB 兩代

- **GRUB Legacy (0.9x)**：1995 ~ 2005 主流，現在幾乎沒人用了
- **GRUB 2 (1.9x ~ 2.x)**：2009 起取代 Legacy，現代主流

兩代設計理念差很多：Legacy 的 stage 全部寫死、`menu.lst` 是純 config；GRUB 2 模組化 + Turing-complete script。我們重點看 GRUB 2，但也對照 Legacy。

## GRUB Legacy 的三段式

```
 Stage 1 (boot.img, 512 bytes)
    ↓ 讀
 Stage 1.5 (e2fs_stage1_5 等, 約 8KB)
    ↓ 讀
 Stage 2 (stage2, 100KB+)
    ↓ 讀
 menu.lst → kernel + initramfs
```

- **Stage 1**：MBR 那 446 bytes。任務：讀 stage 1.5。stage 1.5 的位置寫死在 stage 1 的 byte 裡（embed 時填進去）
- **Stage 1.5**：在 MBR 後面、第一個 partition 前面的「保留區」（傳統 62 sector ≈ 31KB）。每個檔案系統一個版本：`e2fs_stage1_5`、`reiserfs_stage1_5`、`fat_stage1_5`...。stage 1.5 認得對應檔案系統，能去 `/boot/grub/stage2` 讀檔
- **Stage 2**：完整 GRUB，有 menu、有 shell、能解 `menu.lst`

`menu.lst` 範例：

```
default 0
timeout 5

title Linux
    root (hd0,0)
    kernel /boot/vmlinuz root=/dev/sda1
    initrd /boot/initrd.img
```

`(hd0,0)` 是 GRUB 的磁碟記法：第 0 個磁碟的第 0 個 partition。從 0 開始數，是 GRUB Legacy 的特色。

## GRUB 2 的設計

最大改變：**模組化**。GRUB 2 core 很小，所有檔案系統 driver、network driver、GUI 都是 module（`.mod`）。

```
 boot.img (446 bytes, MBR)
    ↓ 讀
 core.img (32KB ~ ?, 在保留區或 ESP)
    ↓ 包含
    ├── lzma_decompress.img
    ├── diskboot.img
    ├── kernel.img (GRUB 2 自己的 kernel)
    └── 內嵌 module (biosdisk.mod, ext2.mod, ...)

 core.img 起來後讀 /boot/grub/grub.cfg
    ↓
 載更多 module、印 menu、讀 vmlinuz + initramfs
```

幾個重要概念：

**core.img**：不是 stage 1.5。它是 GRUB 2 的核心，被 `grub-install` 動態組出來，包含：
- 從 MBR 直接跳過來的 entry code (`diskboot.img`)
- 解壓函式 (`lzma_decompress.img`)
- GRUB 2 kernel
- 一組「啟動需要的 module」 — 通常是磁碟驅動 + 該機器的檔案系統 driver

**grub.cfg**：取代 `menu.lst`。它**不是純 config**，是 Turing-complete script，可以 `if`、可以 `for`、可以呼叫 function。

```
set default=0
set timeout=5

menuentry 'Ubuntu' {
    insmod ext2
    set root='hd0,gpt2'
    linux /boot/vmlinuz root=UUID=abcd...
    initrd /boot/initrd.img
}
```

`insmod ext2` — 動態載入 ext2 module。GRUB 2 才能這樣做。

`linux` 跟 `initrd` 是 GRUB 2 命令，分別載 kernel 跟 initramfs。背後做的事 Ch 14 / Ch 17 會詳細講。

## GRUB 2 在 BIOS 機器的目錄

```
/boot/grub/
├── grub.cfg              # 主 config，由 grub-mkconfig 產生
├── grubenv               # 動態變數（saved_entry 等）
├── i386-pc/              # BIOS 平台的 module
│   ├── ext2.mod
│   ├── normal.mod
│   ├── linux.mod
│   ├── ...
│   └── core.img.gz
├── fonts/
└── locale/
```

所有 module 在 `i386-pc/`。GRUB 2 跑起來後可以 `insmod xxx` 載任何一個。

## GRUB 2 在 UEFI 機器的目錄

```
/boot/efi/EFI/<distro>/
├── grubx64.efi          # GRUB 2 的 UEFI bootloader
├── grub.cfg             # 通常很短，只 chainload 真正的 cfg
└── ...

/boot/grub/
├── grub.cfg             # 真正的主 config
├── x86_64-efi/          # UEFI 平台的 module
└── ...
```

UEFI 模式下 `grubx64.efi` 是個 PE/COFF executable（**不是 raw binary**），UEFI firmware 直接 load + execute。後面 Ch 12 / 13 會講細節。

## `grub.cfg` 是怎麼產生的

你不該直接編輯 `/boot/grub/grub.cfg` — 它是被 `grub-mkconfig` 產生的，下次 update 會被覆蓋。

正確做法：改 `/etc/default/grub` 跟 `/etc/grub.d/*` 裡的 script，然後跑：

```bash
sudo grub-mkconfig -o /boot/grub/grub.cfg
# 或 Debian 慣用的 wrapper
sudo update-grub
```

`/etc/default/grub` 設常用變數：

```bash
GRUB_DEFAULT=0
GRUB_TIMEOUT=5
GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"
GRUB_CMDLINE_LINUX=""
GRUB_DISABLE_OS_PROBER=false
```

`/etc/grub.d/*` 是個 script directory：

```
00_header        # 設變數
05_debian_theme  # 顯示主題
10_linux         # 掃 /boot/vmlinuz-* 加進 menu
20_linux_xen     # Xen 的 entry
30_os-prober     # 偵測其他 OS
40_custom        # 你自己的 entry
41_custom        # /boot/grub/custom.cfg 的 hook
```

`grub-mkconfig` 依檔名順序跑這些 script，組出 `grub.cfg`。

## GRUB shell

GRUB 2 內建一個 shell，按 `c` 進去（在 menu 畫面）：

```
grub> ls
(hd0) (hd0,gpt1) (hd0,gpt2)
grub> ls (hd0,gpt2)/
boot/ etc/ home/ ...
grub> linux (hd0,gpt2)/boot/vmlinuz-5.15 root=/dev/sda2
grub> initrd (hd0,gpt2)/boot/initrd.img-5.15
grub> boot
```

開機 GG 時這是救命稻草。Ch 23 排錯會用到。

## GRUB 2 命令對照表

| 命令 | 用途 |
|---|---|
| `set default=0` | 預設 menu entry |
| `set timeout=5` | 等待秒數 |
| `menuentry 'X' { }` | 定義一個 menu 項目 |
| `linux <path> [args]` | 載 Linux kernel |
| `initrd <path>` | 載 initramfs |
| `multiboot <path>` | 載 multiboot kernel (如 Hurd) |
| `chainloader <path>` | 把控制權交給另一個 bootloader (用於 BIOS chain Windows) |
| `insmod <module>` | 載 module |
| `search --no-floppy --fs-uuid <UUID>` | 找含指定 UUID 的 partition |

`search --fs-uuid` 在 cfg 裡很常見：

```
search --no-floppy --fs-uuid --set=root abcd-1234
```

把 root 變數設成「含這個 UUID 的 partition」。比寫死 `(hd0,gpt2)` 安全，因為磁碟順序可能變。

## 一個常見誤解：「升 kernel 一定要動 GRUB」

不一定。如果新 kernel 放在 `/boot` 下並命名為 `vmlinuz-X.Y`，而你的 distro 啟用了 `10_linux` script，下次 `update-grub` 會自動加 entry。

很多 distro（Debian, Ubuntu）的 `apt install linux-image-X.Y` 會幫你跑 `update-grub`，所以你連手動都不用。

## 一個常見誤解：「`grub.cfg` 改了要 reboot 才生效」

對也不對。`grub.cfg` 在開機時被 GRUB 讀，所以「下一次開機」就生效。但 GRUB 自身沒有「reload config」的服務（unlike systemd），所以不能在開機後 hot reload。

## 動手練習

**1. 看你機器的 grub.cfg**

```bash
cat /boot/grub/grub.cfg | less
# 找 menuentry
grep -E "menuentry|linux|initrd" /boot/grub/grub.cfg
```

數一下你有幾個 menu entry、每個用哪個 vmlinuz、傳什麼 cmdline。

**2. 看 GRUB 用了哪些 module**

```bash
ls /boot/grub/i386-pc/ | head -30           # BIOS
ls /boot/grub/x86_64-efi/ | head -30        # UEFI
```

**3. 改 GRUB_TIMEOUT 試試**

```bash
sudo vi /etc/default/grub
# 改 GRUB_TIMEOUT=10
sudo update-grub
# 下次開機會等 10 秒
```

**4. 在 GRUB shell 裡手動開機**

下次開機按 `c` 進 GRUB shell，照下面做：

```
grub> ls
grub> ls (hd0,gpt2)/boot/         # 找 vmlinuz
grub> linux (hd0,gpt2)/boot/vmlinuz-X.Y root=/dev/sda2
grub> initrd (hd0,gpt2)/boot/initrd.img-X.Y
grub> boot
```

成功的話你開進系統，這代表你完全 bypass 了 `grub.cfg`。

**5. 故意打錯 root=**

在 GRUB menu 按 `e` 編輯 entry，把 `root=...` 改成不存在的，按 `Ctrl-X` 開機。kernel 會 panic 噴 "Cannot mount root filesystem"。

這就是 Ch 23 排錯會大量碰到的場景。**先在安全環境試一次比未來緊急時試好**。

## 自我檢核

- [ ] 講得出 GRUB Legacy 三 stage 的分工
- [ ] 知道 GRUB 2 為什麼模組化、core.img 是什麼
- [ ] 知道 `grub.cfg` 不該手改，要改 `/etc/default/grub` + `update-grub`
- [ ] 進過 GRUB shell、跑過 `linux` + `initrd` + `boot`
- [ ] 知道 BIOS 跟 UEFI 的 GRUB 目錄差在哪

Part 2 結束。下一章開始 Part 3 — UEFI。從架構開始。

→ [Ch 10 UEFI 架構：Boot Services / Runtime Services / Protocol](./10-uefi-architecture.md)
