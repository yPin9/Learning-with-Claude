# Ch 18 — GRUB 深入：架構與模組

> **目標**：拆解 GRUB 2 的內部架構——boot.img / core.img / 模組系統、開機的兩階段（boot → normal）、grub.cfg 與 grub-mkconfig 的關係、以及 GRUB 的命令列環境，讓你能 debug GRUB 開機問題而非只會跑 update-grub。

> **環境**：GRUB 2.06（Debian 12 / Ubuntu 22.04）。本章以 GRUB 2 為準（GRUB legacy 已淘汰）。

## 為什麼要懂 GRUB 內部？

多數人對 GRUB 的認識止於「跑 `update-grub`，它就會弄好」。但當 GRUB 出問題時——開機掉到 `grub>` 命令列、找不到 kernel、`error: unknown filesystem`——你需要知道 GRUB 內部怎麼運作，才能救。

GRUB 不是個黑盒子。它有清楚的架構：一個極小的初始 image、一個核心 image、和動態載入的模組。理解這個架構，你就能在 GRUB 救援模式手動開機、診斷設定問題、理解 `update-grub` 到底做了什麼。

## 先建立直覺：GRUB 是個模組化的「迷你作業系統」

```
GRUB 2 的架構（模組化）：

  boot.img（極小，BIOS 的 MBR 或 UEFI 的小 stub）
        │ 載入
  core.img（GRUB 核心 + 必要模組）
        │ 包含：
        │  - 基本的檔案系統驅動（讀 /boot 需要的）
        │  - 開機所需的核心邏輯
        │  - 能載入更多模組
        ▼
  normal 模式（完整 GRUB 環境）
        │ 動態載入模組：
        │  - ext4.mod, btrfs.mod（檔案系統）
        │  - lvm.mod, luks.mod（複雜儲存）
        │  - gfxterm.mod（圖形選單）
        │  - ...
        ▼
  讀 grub.cfg → 顯示選單 → 載入 kernel
```

GRUB 像個迷你 OS——它有檔案系統驅動、命令列、腳本語言、模組系統。這個複雜度是它全能的代價（Ch 17）。理解模組化架構是理解 GRUB 的鑰匙。

## boot.img 與 core.img

GRUB 的開機分幾個 image，因韌體（BIOS/UEFI）而異（Ch 19 詳述差異），這裡看 BIOS 版的概念：

```
BIOS GRUB 的 image：

  boot.img（446 bytes，放 MBR 的 boot code 區）
    任務：載入 core.img 的第一個 sector
    （太小，只能做這一件事）
        │
  core.img（放 MBR gap，Ch 5/19）
    = diskboot.img（載入剩餘 core.img）
    + kernel.img（GRUB 核心，不是 Linux kernel！）
    + 嵌入的必要模組（讀 /boot 的檔案系統驅動）
        │
  core.img 跑起來後，能讀 /boot/grub/，載入更多模組
```

> **術語陷阱**：GRUB 的 `kernel.img` 是「GRUB 自己的核心」，**不是** Linux kernel。GRUB 內部把自己的核心叫 kernel.img，容易和 Linux kernel 混淆。本課說「kernel」指 Linux kernel，GRUB 的核心明確叫「GRUB core」。

core.img 的關鍵：它嵌入了「讀 `/boot` 需要的檔案系統驅動」。如果你的 `/boot` 在 ext4，core.img 嵌入 ext4 驅動；在 LVM，嵌入 lvm + 底層檔案系統驅動。這就是為什麼 `grub-install` 要知道你的 `/boot` 在哪種儲存——它要把對的驅動嵌進 core.img。

## 兩階段：boot 與 normal

GRUB 開機分兩個階段：

```
GRUB 的兩階段：

  階段 1：boot（最小環境）
    core.img 跑起來，有基本檔案系統存取
    但功能有限（沒有選單、沒有完整模組）
    任務：找到 /boot/grub/，進入 normal 模式
        │
  階段 2：normal（完整環境）
    載入 normal.mod，進入完整 GRUB
    讀 grub.cfg、載入選單模組、顯示選單
    這是你看到的 GRUB 開機選單
        │
  如果 normal 模式失敗（找不到 grub.cfg 等）：
    掉回 "rescue" 模式（grub rescue>，極簡命令列）
```

兩個失敗模式你可能遇到：
- `grub>`（normal 模式的命令列）：normal 載入了但 grub.cfg 有問題，能用較多指令
- `grub rescue>`（rescue 模式）：連 normal 都沒載入，只有極少指令，要手動載入模組

## grub.cfg：GRUB 的設定

`grub.cfg` 是 GRUB 的主設定，定義開機選單和各開機項：

```
# /boot/grub/grub.cfg（節錄，實際是自動生成的）
set timeout=5
set default=0

menuentry 'Ubuntu' {
    insmod ext2              # 載入 ext 檔案系統模組
    set root='hd0,gpt2'      # /boot 在哪個磁碟分區
    linux /vmlinuz-6.1.0 root=/dev/sda2 ro quiet
    #     ↑ 載入 kernel    ↑ kernel 命令列參數
    initrd /initrd.img-6.1.0 # 載入 initramfs
}

menuentry 'Ubuntu (recovery mode)' {
    # ... 救援模式開機項 ...
}

menuentry 'Windows Boot Manager' {
    insmod chain
    chainloader /EFI/Microsoft/Boot/bootmgfw.efi  # chainload Windows
}
```

關鍵指令：
- `set root=`：設定「從哪個分區讀檔案」
- `linux /vmlinuz...`：載入 Linux kernel，後面是 kernel 命令列
- `initrd /initrd.img...`：載入 initramfs
- `chainloader`：把控制權交給另一個 bootloader（如 Windows）

> **不要手動編輯 grub.cfg！** 它是自動生成的（`grub-mkconfig` / `update-grub`）。手改會在下次更新時被覆蓋。要改設定，改 `/etc/default/grub`（全域設定）或 `/etc/grub.d/`（生成腳本），然後跑 `update-grub` 重新生成。下面詳述。

## grub-mkconfig：生成 grub.cfg

`grub.cfg` 由 `grub-mkconfig`（Ubuntu/Debian 包裝成 `update-grub`）生成：

```
grub.cfg 的生成流程：

  /etc/default/grub          ← 全域設定（timeout、預設項、kernel 參數）
        +
  /etc/grub.d/*              ← 生成腳本（每個負責一部分）
    00_header                 設定 timeout、預設項
    10_linux                  掃描 /boot 的 kernel，生成 Linux 開機項
    30_os-prober              偵測其他 OS（Windows...），生成開機項
    40_custom                 你的自訂開機項
        │
  grub-mkconfig 跑這些腳本
        ▼
  /boot/grub/grub.cfg（自動生成，不要手改）
```

```bash
# 改 GRUB 設定的正確流程
sudo vim /etc/default/grub
#   GRUB_TIMEOUT=5
#   GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"
#   GRUB_DEFAULT=0

# 重新生成 grub.cfg
sudo update-grub          # Debian/Ubuntu
# 或
sudo grub-mkconfig -o /boot/grub/grub.cfg   # 通用
```

理解這個流程，你就知道：改 GRUB 設定改 `/etc/default/grub`（不是 grub.cfg），跑 `update-grub` 套用。`update-grub` 掃描 `/boot` 找到的 kernel、用 os-prober 偵測其他 OS，生成完整的 grub.cfg。

## GRUB 命令列：手動開機（救援）

當 GRUB 掉到 `grub>` 命令列（找不到正常開機），你能手動開機：

```
# 在 grub> 命令列手動開機
grub> ls                              # 列出磁碟和分區
(hd0) (hd0,gpt1) (hd0,gpt2) ...
grub> ls (hd0,gpt2)/                  # 看某分區的內容
grub> ls (hd0,gpt2)/boot/             # 找 kernel
vmlinuz-6.1.0 initrd.img-6.1.0 ...
grub> set root=(hd0,gpt2)             # 設定 root 分區
grub> linux /boot/vmlinuz-6.1.0 root=/dev/sda2 ro
grub> initrd /boot/initrd.img-6.1.0
grub> boot                            # 開機！
```

```
# grub rescue> 模式（更受限，要先載入模組）
grub rescue> ls
grub rescue> set prefix=(hd0,gpt2)/boot/grub
grub rescue> insmod normal            # 載入 normal 模組
grub rescue> normal                   # 進入完整 GRUB
```

> 能在 `grub>` 手動開機是救援的核心技能。開機壞掉（grub.cfg 損壞、kernel 路徑變了）時，你 `ls` 找到 kernel、`set root`、`linux`、`initrd`、`boot` 就能手動進系統，然後修復。這比重灌快得多。記住這套指令。

## 故意弄壞：grub.cfg 指向不存在的 kernel

```bash
# 模擬（VM 安全）：手動把 grub.cfg 的 kernel 版本改錯
# 或更常見的：kernel 更新後 grub.cfg 沒更新，指向舊 kernel
# 開機時：
# error: file '/vmlinuz-6.1.0-OLD' not found
# Press any key to continue... → 掉到 grub> 或選單

# 救援：在 grub> 手動指向正確的 kernel
grub> ls (hd0,gpt2)/boot/      # 找實際存在的 kernel
grub> linux /boot/vmlinuz-6.1.0-NEW root=/dev/sda2 ro
grub> initrd /boot/initrd.img-6.1.0-NEW
grub> boot
# 進系統後 sudo update-grub 修復 grub.cfg
```

grub.cfg 指向不存在的 kernel（常因 kernel 更新後沒跑 update-grub，或 /boot 空間不足導致 kernel 沒裝好）會開機失敗。救援：手動指向存在的 kernel 開機，進系統後 `update-grub` 重新生成正確的 grub.cfg。

## 踩雷集錦

1. **手動編輯 grub.cfg**：它是自動生成的，手改會被 update-grub 覆蓋。改 `/etc/default/grub` 或 `/etc/grub.d/`

2. **混淆 GRUB 的 kernel.img 和 Linux kernel**：GRUB 的 kernel.img 是 GRUB 自己的核心。Linux kernel 是 vmlinuz。兩個不同東西

3. **core.img 缺少 /boot 的檔案系統驅動**：如果 /boot 在 ext4 但 core.img 沒嵌 ext4 驅動，GRUB 讀不到 /boot。grub-install 要正確偵測 /boot 的儲存類型

4. **grub> 和 grub rescue> 搞混**：grub>（normal 載入了，指令多）；grub rescue>（極簡，要先 insmod normal）。不同的救援步驟

5. **改 /etc/default/grub 後忘記 update-grub**：改了設定但沒重新生成 grub.cfg，設定不生效。改完一定 `update-grub`

6. **GRUB_TIMEOUT=0 + 隱藏選單導致進不了選單**：設了 0 timeout 且隱藏選單，開機時來不及進 GRUB 選單救援。開機時長按 Shift（BIOS）或 Esc（UEFI）強制顯示選單

## 進階：GRUB 的模組嵌入與 grub-install

`grub-install` 做的事比「裝 GRUB」複雜——它要決定 core.img 嵌入哪些模組：

```
grub-install 的智慧：
  1. 偵測 /boot 在哪種儲存（ext4？LVM？LUKS？RAID？）
  2. 決定 core.img 要嵌入哪些模組才能讀到 /boot
     - /boot 在 ext4 → 嵌 ext2.mod
     - /boot 在 LVM → 嵌 lvm.mod + 底層 fs
     - /boot 在 LUKS → 嵌 luks.mod + cryptodisk.mod
  3. 生成 core.img（含這些模組）
  4. 把 core.img 寫到對的位置（MBR gap / ESP，Ch 19）
        │
  → 確保 GRUB 一開機就能讀到 /boot（雞生蛋問題：
    讀 grub.cfg 需要檔案系統驅動，但驅動在 /boot... 
    所以必要的驅動要「嵌進 core.img」，不能放 /boot）
```

這個「雞生蛋」問題是 GRUB 架構的核心挑戰：要讀 `/boot/grub/grub.cfg` 需要檔案系統驅動，但驅動模組也在 `/boot`。解法：把「讀 /boot 必需的驅動」**嵌進 core.img**（core.img 不在 /boot，在 MBR gap 或 ESP）。`grub-install` 的智慧就在於正確判斷要嵌哪些。這也是為什麼換 /boot 的儲存方式（如改成 LVM）後要重跑 grub-install——core.img 要重新嵌入對的驅動。

## 動手練習

1. 探索你的 GRUB：`ls /boot/grub/`（看 core.img、模組）、`cat /boot/grub/grub.cfg | head -50`（看生成的設定）、`cat /etc/default/grub`（看你能改的設定）

2. 看模組：`ls /boot/grub/x86_64-efi/`（或 i386-pc，BIOS），看有哪些 `.mod`（檔案系統、功能模組）

3. 練 GRUB 命令列（VM）：開機時進 GRUB，按 `c` 進命令列，練 `ls`、`ls (hd0,gpt2)/`、`set root=`、手動 `linux`/`initrd`/`boot`

4. 安全地改設定：改 `/etc/default/grub` 的 `GRUB_TIMEOUT`，跑 `sudo update-grub`，重開機確認 timeout 變了

## 本章重點整理

- GRUB 2 是模組化的迷你 OS：boot.img（極小）→ core.img（核心+必要模組）→ normal 模式（完整環境，動態載入模組）
- 兩階段：boot（最小，找 /boot/grub）→ normal（完整，讀 grub.cfg 顯示選單）；失敗掉到 grub> 或 grub rescue>
- grub.cfg 是自動生成的（grub-mkconfig/update-grub），不要手改；改設定改 /etc/default/grub + /etc/grub.d/
- GRUB 命令列能手動開機（ls/set root/linux/initrd/boot），是救援核心技能
- grub-install 的智慧：偵測 /boot 儲存類型，把必要驅動嵌進 core.img（解決「讀 /boot 需要驅動但驅動在 /boot」的雞生蛋問題）

## 自我檢核

- [ ] 能畫出 GRUB 的 image 階層（boot.img → core.img → normal）
- [ ] 知道為什麼不能手改 grub.cfg，以及改設定的正確流程
- [ ] 能在 grub> 命令列手動開機（ls/set root/linux/initrd/boot）
- [ ] 能解釋 core.img 為什麼要嵌入檔案系統驅動（雞生蛋問題）
- [ ] 知道 grub>（normal）和 grub rescue> 的差別

## 延伸閱讀

### 官方文件

- **[GNU GRUB Manual](https://www.gnu.org/software/grub/manual/grub/grub.html)**
  - **讀哪裡**：Booting（命令列開機）、Configuration（grub.cfg）、Images（boot.img/core.img）
  - **學什麼**：GRUB 的權威文件，命令列指令和設定的完整參考
  - **前提**：本章

### 部落格 / 文章

- **[How GRUB 2 boots](https://wiki.archlinux.org/title/GRUB)** — Arch Wiki GRUB
  - **讀哪裡**：installation 和 configuration 那幾節
  - **學什麼**：GRUB 的實務細節、grub-install 的選項、各種儲存的處理
  - **前提**：本章

- **[Resurrecting GRUB from rescue mode](https://wiki.archlinux.org/title/GRUB#GRUB_rescue)** — Arch Wiki
  - **這篇說什麼**：grub rescue> 模式的救援步驟
  - **讀哪裡**：rescue 那節
  - **為什麼值得讀**：實戰救援指南，開機壞掉時的救命知識

→ [Ch 19 GRUB 在 BIOS 與 UEFI 的差異](./19-grub-bios-uefi.md)
