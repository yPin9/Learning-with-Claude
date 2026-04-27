# Ch 1 — 從電源到登入畫面的全景

> 目標：先看森林。記住整個 boot 流程的階段順序，後面每一章才知道自己在地圖的哪裡。

## 為什麼先講全景

開機這條鏈每個階段都有人寫了一本書。如果一上來就鑽 GDT 的 segment descriptor，你會在第三章迷路。

先記住這張圖，後面每一章開頭我都會標「我們在這裡」：

```
 [按下電源]
      │
      ▼
 ┌─────────────────────────┐
 │ 1. CPU reset            │ ── 第一條指令在 0xFFFFFFF0
 └─────────────┬───────────┘
               │
               ▼
 ┌─────────────────────────┐
 │ 2. Firmware             │ ── BIOS 或 UEFI
 │    (POST → 找開機裝置)  │
 └─────────────┬───────────┘
               │
        ┌──────┴───────┐
        ▼              ▼
   [BIOS path]    [UEFI path]
        │              │
   讀 MBR 512B    讀 ESP 上的 .EFI
        │              │
        ▼              ▼
 ┌─────────────────────────┐
 │ 3. Bootloader           │ ── GRUB / systemd-boot
 │    (選 kernel、傳參數) │
 └─────────────┬───────────┘
               │
               ▼
 ┌─────────────────────────┐
 │ 4. Kernel               │ ── vmlinuz 解壓
 │    (硬體初始化)         │ ── start_kernel()
 └─────────────┬───────────┘
               │
               ▼
 ┌─────────────────────────┐
 │ 5. initramfs            │ ── 早期 userspace
 │    (掛 root、載 driver) │
 └─────────────┬───────────┘
               │
               ▼
 ┌─────────────────────────┐
 │ 6. switch_root          │ ── 切到真正的 /
 └─────────────┬───────────┘
               │
               ▼
 ┌─────────────────────────┐
 │ 7. PID 1 (systemd)      │ ── 拉起所有 service
 └─────────────┬───────────┘
               │
               ▼
 ┌─────────────────────────┐
 │ 8. getty / login        │ ── 你看到的登入畫面
 └─────────────────────────┘
```

整個 Linux 開機就這 8 段。後面 25 章每一章都在拆其中一兩段。

## 每段在做什麼（30 秒版）

**1. CPU reset** — 開機那一瞬間，CPU 在 real mode、ip 指向一個固定位址（x86 是 `0xFFFFFFF0`）、執行第一條指令。這個位址是硬連線的，不可改。

**2. Firmware (BIOS / UEFI)** — 主機板上的韌體跑 POST（power-on self test），檢查記憶體、找硬碟，根據設定決定從哪個裝置開機。

**3. Bootloader** — 韌體把 bootloader 載進記憶體，bootloader 負責找 kernel、給使用者選單、把 kernel 載到記憶體並跳過去。

**4. Kernel** — vmlinuz 是壓縮的 kernel image。它先自解壓，然後做硬體偵測、建立記憶體管理、排程器，最後呼叫 `start_kernel()`。

**5. initramfs** — kernel 不會直接掛 root filesystem，因為它還不知道你的 root 在哪個磁碟、什麼檔案系統、要不要先解密。initramfs 是一個臨時的 userspace，它的工作就是：載 driver、解 LUKS、組 RAID，然後告訴 kernel：「真的 root 在這裡」。

**6. switch_root** — initramfs 跑完它的 `/init` script，呼叫 `switch_root` 系統呼叫，把 root 從 ramfs 切到真實磁碟。

**7. PID 1 (systemd)** — switch_root 後，kernel 把 PID 1 換成新 root 上的 `/sbin/init`（通常是 systemd）。systemd 接手，根據 unit dependency 啟動所有 service。

**8. getty / login** — 最後 systemd 拉起 `getty@tty1.service` 或 `gdm.service`，你看到登入畫面。

## 一個常見誤解

「kernel 啟動 = 看到登入畫面」**不對**。

kernel `start_kernel()` 跑完之後到登入畫面之間還有 4 個階段（initramfs、switch_root、systemd 啟動所有 service、getty）。常常使用者抱怨「開機很慢」，慢的不是 kernel 而是後面 systemd 拉一堆 service。

`systemd-analyze` 跑一次你會發現：

```
Startup finished in 1.2s (firmware) + 0.8s (loader) + 1.5s (kernel) + 4.3s (initrd) + 12.7s (userspace) = 20.5s
```

userspace 通常是大頭。Ch 23 會教怎麼看這個。

## BIOS 跟 UEFI 在哪段分岔

第 2、3 段。第 1 段（CPU reset）兩邊一樣，第 4 段以後（kernel）兩邊也幾乎一樣。

差別只在「韌體怎麼找到 bootloader、bootloader 怎麼載 kernel」。Ch 3 會詳細對照。

## 這系列的重點分布

我故意在每段花的力氣不一樣：

| 階段 | 章節數 | 為什麼 |
|---|---|---|
| 1 CPU reset + 2 Firmware | Ch 2–4, 10 | x86 模式切換很反直覺，要花時間 |
| 3 Bootloader | Ch 5–9, 11–13 | 自製 boot sector + 自製 UEFI app，動手最多 |
| 4 Kernel | Ch 14–16 | kernel 內部不深挖（那是 kernel pwn 的事） |
| 5 initramfs | Ch 17–19 | 自製一個最小 initramfs，這是 boot 的精華 |
| 6 switch_root | Ch 19 | 一章說完 |
| 7 PID 1 | Ch 20–23 | systemd 是個怪物，分四章 |
| 8 getty | 不單獨講 | 太瑣碎，Ch 22 帶過 |

## 動手練習

去你自己的 Linux 機器跑這幾條，把每一階段對應到實際檔案 / 程序：

```bash
# 第 2 段：你的 firmware
sudo dmidecode -s bios-vendor
sudo dmidecode -s bios-version

# 第 3 段：你的 bootloader
ls /boot                    # 看 grub.cfg、vmlinuz、initramfs 都在這
sudo efibootmgr -v          # UEFI 的話，看 boot entry 順序

# 第 4 段：kernel image
ls -lh /boot/vmlinuz-*

# 第 5 段：initramfs
ls -lh /boot/initrd.img-*   # Debian
ls -lh /boot/initramfs-*    # Arch / Fedora
file /boot/initramfs-*      # 看它是 cpio + gzip / xz / zstd

# 第 7 段：你的 init
ls -l /sbin/init            # 通常 symlink 到 systemd
systemctl --version

# 跨整個流程的時間分析
systemd-analyze
systemd-analyze blame | head -20
```

對照那張地圖看，每個檔案落在哪一段。

## 自我檢核

- [ ] 能默畫出 8 個階段的順序
- [ ] 知道 BIOS 跟 UEFI 只在第 2、3 段分岔
- [ ] 知道「kernel 啟動完」不等於「看到登入畫面」
- [ ] 跑過 `systemd-analyze`，知道時間花在哪

下一章看每段第 1 段：CPU reset 之後到底在哪個模式跑、為什麼後面要切來切去。

→ [Ch 2 x86 三種 CPU 模式](./02-x86-cpu-modes.md)
