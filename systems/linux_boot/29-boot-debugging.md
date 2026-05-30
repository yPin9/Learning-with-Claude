# Ch 29 — 開機問題診斷

> **目標**：建立系統性的開機問題診斷能力——判斷卡在哪一階段（firmware/bootloader/kernel/initramfs/init）、各階段的救援工具與技巧、emergency/rescue shell、以及最常見開機問題的診斷流程。這章把全課知識變成實戰的 debug 能力。

> **環境**：GRUB、systemd、QEMU。整合全課（每階段的診斷對應前面各章）。

## 為什麼開機診斷需要全課知識？

開機卡住是最讓人焦慮的問題——你看不到熟悉的系統，可能連 shell 都進不去。但開機是個有清楚階段的接力（Ch 1），每個階段的失敗有不同的症狀和救援方法。

這章把全課知識組織成「診斷地圖」：看到某個症狀 → 判斷卡在哪一階段 → 用對應的救援工具。學完這章，開機問題對你不再是黑盒子恐慌，而是「系統性地定位 + 救援」。

## 先建立直覺：先判斷卡在哪一階段

```
診斷的第一步：開機接力卡在哪一棒（Ch 1）？

  完全黑屏、無任何輸出
    → 韌體階段（firmware）或極早期 kernel
    
  韌體 logo 後「No bootable device」/「No OS」
    → bootloader 找不到（MBR/ESP 問題）
    
  GRUB 選單出現但選了開不了 / 掉到 grub>
    → bootloader → kernel 之間（Ch 18-20）
    
  kernel 訊息跑一些後 panic
    → kernel 早期 / 掛 root（Ch 22-23）
    
  掉到 (initramfs)# shell
    → initramfs 掛不上 root（Ch 24）
    
  進到 emergency.target / 卡在某個服務
    → init/systemd 階段（Ch 26）
        │
  → 每個階段有不同症狀，先定位再救援
```

診斷的核心是「定位階段」。看到症狀，對照開機接力（Ch 1），判斷卡在哪一棒，然後用那一棒對應的救援方法。下面逐階段講。

## 階段一：韌體 / 完全黑屏

```
症狀：完全黑屏、無輸出，或卡在韌體 logo

可能原因：
  - 硬體問題（RAM、CPU、電源）
  - 韌體設定錯誤（開機順序、Secure Boot）
  - 極早期 kernel 崩潰（少見，無輸出）

診斷：
  - 進韌體設定（開機按 Del/F2），檢查開機順序、Secure Boot
  - 換開機裝置測試
  - 極早期 kernel 問題：用 earlyprintk（見下）

救援：
  - 重設韌體設定（CMOS clear）
  - 確認開機裝置和韌體模式（BIOS/UEFI）匹配
```

## 階段二：bootloader 找不到 / GRUB 問題

```
症狀：「No bootable device」或掉到 grub> / grub rescue>

可能原因（Ch 18-19）：
  - MBR/ESP 損壞
  - GRUB 設定錯（grub.cfg 損壞、指向不存在的 kernel）
  - core.img 缺驅動（讀不到 /boot）

診斷與救援（Ch 18）：
  - grub> 命令列手動開機：
    grub> ls                    # 找磁碟分區
    grub> ls (hd0,gpt2)/boot/   # 找 kernel
    grub> set root=(hd0,gpt2)
    grub> linux /boot/vmlinuz-X root=/dev/sda2 ro
    grub> initrd /boot/initrd.img-X
    grub> boot
  - 進系統後修復：sudo update-grub / grub-install
```

## 階段三：kernel panic

```
症狀：kernel 訊息後 "Kernel panic - not syncing: ..."

常見 panic 和成因：
  "Unable to mount root fs"（Ch 23）：
    → root= 參數錯 / 缺 root 的驅動 / initramfs 問題
  "No working init found"（Ch 23/25）：
    → init 不存在或不可執行
  "Attempted to kill init"（Ch 25）：
    → PID 1 退出了

診斷：
  - 讀 panic 前的 kernel 訊息（線索在那）
  - 開機時編輯 GRUB（按 e），加 kernel 參數除錯

救援：
  - 改 root= 參數（grub> 或編輯選單）
  - 用 init=/bin/bash 繞過（Ch 23）
  - 開機加 earlyprintk=serial 看更早的輸出
```

## 階段四：卡在 initramfs

```
症狀：掉到 (initramfs)# 或 dracut emergency shell

原因（Ch 24）：
  - initramfs 缺少掛 root 的驅動（換了儲存沒 update-initramfs）
  - root 裝置找不到（NVMe/LVM/LUKS 問題）

診斷與救援（在 (initramfs)# 裡）：
  cat /proc/cmdline          # 看 root= 是什麼
  ls /dev/                   # root 裝置在不在
  modprobe <driver>          # 手動載入缺的驅動
  # LVM: lvm vgchange -ay
  # LUKS: cryptsetup open /dev/sdaX root
  mount /dev/... /sysroot    # 手動掛 root
  exit                       # 繼續開機（如果掛成功）
        │
  進系統後：sudo update-initramfs -u 修復
```

## 階段五：init/systemd 問題

```
症狀：掉到 emergency.target / rescue.target，或卡在某服務

原因（Ch 26）：
  - 某個服務啟動失敗
  - /etc/fstab 錯誤（掛載失敗）
  - 損壞的設定

診斷與救援：
  systemctl --failed          # 看失敗的服務
  journalctl -xb              # 看本次開機的 log
  journalctl -p err -b        # 只看錯誤
  systemd-analyze blame       # 看卡在哪個服務

  # 救援開機（GRUB 加參數）：
  systemd.unit=rescue.target  # 最小服務 + root shell
  systemd.unit=emergency.target  # 更小（只有 root shell）
```

## 核心救援工具：GRUB 編輯開機參數

最重要的救援技巧——開機時編輯 GRUB 加 kernel 參數：

```
GRUB 開機選單編輯（按 e）：
  在 linux 行（kernel 那行）末尾加參數：

  救援參數：
    init=/bin/bash           最小：PID 1 = bash（繞過 systemd，Ch 23）
    systemd.unit=rescue.target   systemd 救援模式
    single / 1               單用戶模式
    
  除錯參數：
    earlyprintk=serial,ttyS0,115200   最早期輸出走 serial
    debug                    更多 kernel 除錯輸出
    nokaslr                  關 KASLR（位址固定，方便對照，Ch 21）
    rd.break                 在 initramfs 階段停下（dracut，更早救援）
    
  改參數測試：
    root=/dev/sdaX           改 root 裝置
    ro → rw                  讓 root 可寫（救援時要改東西）
```

> 開機時按 `e` 編輯 GRUB 加參數，是 Linux 救援的核心技能。`init=/bin/bash` 給你最深層的救援 shell（繞過所有 init）。`rd.break` 在 initramfs 停下（更早）。`earlyprintk` 看極早期輸出。這些參數讓你能在「正常開機失敗」時，從不同階段切入救援。記住這幾個，開機問題十之八九能救。

## 救援媒體：live USB

當系統完全進不去（連 GRUB 救援都不行），用 **live USB**：

```
live USB 救援：
  從 live USB 開機（一個獨立的可開機 Linux）
        │
  chroot 進你壞掉的系統：
    sudo mount /dev/sdaX /mnt           # 掛你的 root
    sudo mount /dev/sdaY /mnt/boot      # 掛 /boot（如果分開）
    sudo mount /dev/sdaZ /mnt/boot/efi  # 掛 ESP（UEFI）
    for d in dev proc sys; do sudo mount --bind /$d /mnt/$d; done
    sudo chroot /mnt                    # 進入你的系統環境
        │
  在 chroot 裡修復：
    update-grub / grub-install          # 修 GRUB
    update-initramfs -u                 # 修 initramfs
    passwd                              # 改密碼
    編輯 /etc/fstab 等                  # 修設定
```

> live USB + chroot 是「系統完全壞掉」時的終極救援。從 live USB 開一個能用的 Linux，chroot 進你壞掉的系統（把它當成一個目錄樹操作），修復 GRUB/initramfs/設定。這幾乎能救任何開機問題（除了硬體損壞）。chroot 的 bind mount（/dev /proc /sys）是關鍵——讓 chroot 環境有完整的系統介面，grub-install 等工具才能正常運作。

## 系統性診斷流程

```
開機問題的系統性診斷：

  1. 觀察症狀 → 判斷卡在哪一階段（接力哪一棒）
        │
  2. 對應階段的救援：
     韌體 → 韌體設定、硬體
     bootloader → grub> 手動開機
     kernel → 改 root=、init=/bin/bash、earlyprintk
     initramfs → (initramfs)# 手動掛 root、modprobe
     init → rescue.target、journalctl 看 log
        │
  3. 救援進系統後 → 修復根因
     update-grub / update-initramfs / 改設定
        │
  4. 完全進不去 → live USB + chroot
        │
  5. 看 log 找根因：
     journalctl -xb（本次開機）
     journalctl -b -1（上次開機，如果這次起來了）
     dmesg（kernel 訊息）
```

## 踩雷集錦

1. **不先定位階段就亂試**：開機問題的關鍵是「先判斷卡在哪一階段」。亂試各種救援沒效率。先看症狀對應階段

2. **救援時忘記 remount,rw**：救援模式的 root 常是唯讀的。要改東西先 `mount -o remount,rw /`

3. **chroot 忘記 bind mount /dev /proc /sys**：chroot 進壞系統時，沒 bind 這些虛擬檔案系統，grub-install/update-initramfs 會失敗。一定要 bind

4. **改了 root 儲存沒更新 initramfs/grub**：換 root 到 LVM/加密/不同磁碟，沒 update-initramfs 和 update-grub，下次開機失敗。改 root 後一定更新兩者

5. **earlyprintk 沒設對 console**：看極早期輸出要 `earlyprintk=serial,ttyS0,115200` 並在 QEMU/實機接 serial。設錯看不到輸出

## 進階：用 QEMU 重現和 debug 開機問題

QEMU + gdb（Ch 0）能重現和深入 debug 開機問題：

```
用 QEMU debug 開機問題：
  把壞掉的磁碟 image 用 QEMU 開機
    qemu-system-x86_64 -drive file=broken.img -nographic
        │
  能反覆嘗試（不影響真機）、加各種除錯參數
        │
  深入 debug：QEMU + gdb（Ch 0）
    qemu ... -s -S
    gdb：連進去單步追蹤 kernel 早期初始化
        │
  -d int -no-reboot：看 CPU fault（triple fault 等，Part 2）
```

QEMU 的價值在「安全反覆實驗」——把問題磁碟（或它的副本）在 QEMU 開機，能反覆試各種救援參數、加除錯輸出，甚至用 gdb 單步追蹤 kernel。這對「真機開機壞掉但能 dd 出 image」的情況特別有用——在 QEMU 裡慢慢 debug，不用反覆重啟真機。本課全程用 QEMU，這裡是它在診斷上的綜合應用。

## 動手練習

1. 練 GRUB 救援（VM）：故意改 grub.cfg 指向錯 kernel，開機掉到 grub>，用 ls/set root/linux/initrd/boot 手動開機。進系統 update-grub 修復

2. 練 initramfs 救援（VM，承練習 C）：用缺驅動的 initramfs 開機，掉到 (initramfs)#，手動 modprobe + mount 救援

3. 練 systemd 救援：開機時 GRUB 加 `systemd.unit=rescue.target`，進救援模式。`journalctl -xb` 看 log，`systemctl --failed` 看失敗服務

4. 練 chroot 救援：用 live USB（或另一個 VM）chroot 進一個系統，bind mount /dev /proc /sys，跑 update-grub。體驗終極救援

## 本章重點整理

- 開機診斷的核心：先判斷卡在哪一階段（韌體/bootloader/kernel/initramfs/init），再用對應救援
- 各階段症狀：黑屏→韌體；No bootable→bootloader；panic→kernel/root；(initramfs)#→initramfs；emergency→init
- 核心救援技巧：GRUB 按 e 編輯 kernel 參數（init=/bin/bash、rescue.target、earlyprintk、改 root=）
- 終極救援：live USB + chroot（bind mount /dev /proc /sys，修 grub/initramfs/設定）
- 看 log 找根因：journalctl -xb（本次）、-b -1（上次）、dmesg；QEMU 能安全反覆 debug

## 自我檢核

- [ ] 看到開機症狀，能判斷卡在哪一階段
- [ ] 知道每個階段的救援工具（grub> / init=/bin/bash / (initramfs)# / rescue.target）
- [ ] 能用 GRUB 編輯 kernel 參數做救援
- [ ] 能用 live USB + chroot 修復完全壞掉的系統（含 bind mount）
- [ ] 知道改 root 儲存後要更新 grub 和 initramfs

## 延伸閱讀

### 官方文件

- **[Arch Wiki: Boot debugging](https://wiki.archlinux.org/title/General_troubleshooting#Boot_problems)** 和 **[GRUB troubleshooting](https://wiki.archlinux.org/title/GRUB#Troubleshooting)**
  - **讀哪裡**：boot problems 和各階段的救援
  - **學什麼**：實務的開機問題診斷，Arch Wiki 最完整
  - **前提**：本章

- **[systemd: Debugging boot](https://freedesktop.org/wiki/Software/systemd/Debugging/)**
  - **讀哪裡**：debugging boot 那節
  - **學什麼**：systemd 階段的除錯（rescue/emergency、journalctl）
  - **前提**：Ch 26 + 本章

### 部落格 / 文章

- **[Recovering from boot failures](https://opensource.com/article/themes-on-boot-recovery)** 類的實戰文
  - **這篇說什麼**：常見開機失敗的救援案例
  - **讀哪裡**：案例那部分
  - **為什麼值得讀**：實戰案例補充本章的系統性框架

→ [Ch 30 ARM 與其他架構的開機差異](./30-arm-boot.md)
