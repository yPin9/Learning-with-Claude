# Ch 23 — 開機排錯

> 目標：開機壞了怎麼辦。從哪一段壞、症狀長什麼樣、用什麼工具看、怎麼救。

## 我們在哪裡

整個流程的「事後」。這一章不照階段順序，照症狀組織。

## 排錯第一原則：先確定壞在哪一段

回到 Ch 1 的 8 階段地圖：

```
 1. CPU reset
 2. Firmware (BIOS/UEFI POST)
 3. Bootloader (GRUB/systemd-boot)
 4. Kernel
 5. initramfs
 6. switch_root
 7. PID 1 (systemd)
 8. getty / login
```

**每個階段的失敗症狀不同**，先看症狀對得到哪段：

| 症狀 | 多半哪段壞 |
|---|---|
| 螢幕全黑、沒嗶聲、沒任何輸出 | 1 / 2（硬體 / firmware） |
| BIOS logo 後直接 "no bootable device" | 2（找不到 boot device） |
| GRUB 跑到一半噴錯 / "error: file not found" | 3（bootloader） |
| GRUB menu 出來但選了之後 panic | 4（kernel） |
| Kernel 載入後 "Cannot mount root filesystem" | 4 / 5（找不到 root） |
| 進 (initramfs) shell | 5（initramfs 主動 drop） |
| systemd 啟動但卡在 "Waiting for X" | 7 |
| 登入畫面卡 / login 後黑屏 | 8 / GUI / display manager |

## 階段 2 / 3 — Firmware / GRUB 壞

**症狀**：開機停在 BIOS / GRUB 階段、進不了 kernel。

**工具**：

- 進 firmware setup（按 F2 / Del / F12，看廠商）
- 用 install USB 開機、選 "rescue" / "try without installing"
- `efibootmgr` 從 live USB 看 / 改 NVRAM

**常見場景**：

### "no bootable device"

- BIOS：沒找到 active partition / boot signature
- UEFI：BootOrder 全部 fail / 沒有 fallback `EFI/BOOT/BOOTX64.EFI`

修：

```bash
# 從 live USB
sudo efibootmgr -v                    # 看 NVRAM 狀況
sudo efibootmgr -c -d /dev/sda -p 1 -L "Linux" -l '\EFI\ubuntu\grubx64.efi'
```

或進 BIOS 設「啟用 CSM / legacy boot」（如果裝的是 BIOS 模式）。

### "error: symbol not found" 之類 GRUB 錯誤

通常是 GRUB 跟磁碟結構不同步（你重 partition 過、改了 UUID）。

從 live USB 修：

```bash
sudo mount /dev/sda2 /mnt
sudo mount /dev/sda1 /mnt/boot/efi   # ESP
sudo mount --bind /dev  /mnt/dev
sudo mount --bind /sys  /mnt/sys
sudo mount --bind /proc /mnt/proc
sudo chroot /mnt
update-grub
grub-install /dev/sda
exit
```

### GRUB 進到 rescue mode

```
grub rescue>
```

GRUB stage 1 跑了但載 stage 2 / module 失敗。最常見：`/boot` 在 LVM 但 GRUB 沒裝 LVM module。

```
grub rescue> ls
(hd0) (hd0,gpt1) (hd0,gpt2) ...
grub rescue> ls (hd0,gpt2)/
... 看裡面有沒有 boot/grub/
grub rescue> set prefix=(hd0,gpt2)/boot/grub
grub rescue> set root=(hd0,gpt2)
grub rescue> insmod normal
grub rescue> normal
```

進入正常 GRUB menu 後修 `grub-install` 重灌。

## 階段 4 — Kernel panic

**症狀**：載入 vmlinuz 後 panic。

dmesg 來不及存（系統死了），但 kernel 會把 panic 訊息印在 console。**用相機拍下來**。

常見訊息：

### "Cannot mount root filesystem"

- `root=` 寫錯
- root device 的 driver 沒包進 kernel / initramfs
- root 加密但 initramfs 沒 cryptsetup

修：開 GRUB 編輯 cmdline 把 `root=` 改對；或從 live USB 重 build initramfs：

```bash
sudo dracut --regenerate-all --force      # Fedora
sudo update-initramfs -u -k all           # Debian/Ubuntu
sudo mkinitcpio -p linux                  # Arch
```

### "VFS: Unable to mount root fs on unknown-block(0,0)"

跟上面同類，但更原始 — kernel 連 device 都沒看到。多半是 driver 缺。

### "kernel BUG at ..." / "Oops"

kernel bug。截圖、回報 distro。短期 workaround：用舊 kernel（GRUB 的「Advanced options」有舊版）。

### Black screen after grub

kernel 載了但 framebuffer driver 不對 → 螢幕看不到 log。

cmdline 加 `nomodeset`：用 BIOS 的 VESA framebuffer，不靠 kernel KMS driver。多半能 boot 進去 debug。

## 階段 5 — initramfs

**症狀**：掉到 `(initramfs)` shell。

initramfs 的 init script 主動 drop shell（找不到 root、UUID 不對、解 LUKS 失敗等）。

```sh
(initramfs) ls /dev/disk/by-uuid/        # 確認 UUID 對不對
(initramfs) dmesg | tail                 # 看為什麼 fail
(initramfs) cat /proc/cmdline            # 確認 root= 對
(initramfs) modprobe ext4                # 試手動載 driver
(initramfs) mount /dev/sda1 /root        # 試手動 mount
```

確認問題後：

- 如果是 UUID 對的上 → 直接 `exit` 繼續 boot
- 如果 root device 沒出現 → driver 問題，從 live USB 重 build initramfs

## 階段 7 — systemd

**症狀**：systemd 跑了，但卡在某個 service / target。

工具：

```bash
# 從 live USB chroot 進去
sudo journalctl -b -1                # 上次開機的 log
sudo journalctl -b -1 -p err          # 只看 error
sudo systemctl --failed               # 哪些 service 失敗
sudo systemd-analyze blame | head     # 哪個花最久
sudo systemd-analyze critical-chain   # critical path
```

`-b` 是 boot 編號，`0` = 這次、`-1` = 上次、`-2` = 上上次。

### 常見：卡在 "A start job is running for ..."

systemd 在等某個 service 完成。預設 timeout 90 秒。常見：

- `dev-disk-by\x2duuid-XXX.device` — 等 device 出現（根本沒接這顆磁碟）
- `network-online.target` — 等網路（設了 `Wants=` 但沒網路）

修：

- 移掉 `/etc/fstab` 不存在的 entry
- 改 `Wants=` → 不要 wait

### 進 emergency mode

開機加 cmdline `systemd.unit=emergency.target` 進 emergency。輸入 root 密碼登入後可 debug。

```
$ journalctl -xb
$ systemctl --failed
$ systemctl status broken.service
$ systemctl mask broken.service       # 讓 boot 跳過這個 service
$ systemctl daemon-reload
$ systemctl default                   # 嘗試繼續 boot
```

`mask` 是「禁止這個 service 啟動」，比 `disable` 強 — 連手動 start 都不行。debug 時很有用。

## journalctl 救命指南

```bash
journalctl -b                          # 這次 boot 全部
journalctl -b -1                       # 上次 boot
journalctl -b -p err                   # 只 error
journalctl -u sshd                     # 只 sshd
journalctl -u sshd --since "1 hour ago"
journalctl -k                          # 只 kernel (= dmesg)
journalctl -f                          # tail -f
journalctl --list-boots                # 列所有 boot
journalctl --disk-usage                # log 佔多少空間
```

`-x` 加詳細解釋，特別有用：

```bash
journalctl -xb -p err
```

每個 error 後面附 systemd 給的解釋 + 相關 doc 連結。

## systemd-analyze 完整工具

```bash
systemd-analyze                         # boot 時間
systemd-analyze blame                   # 每 service 啟動時間
systemd-analyze critical-chain          # boot critical path
systemd-analyze critical-chain sshd     # 到 sshd 的 path
systemd-analyze plot > boot.svg         # 圖示
systemd-analyze verify foo.service      # 檢查 unit syntax
systemd-analyze cat-config systemd/system.conf   # 看完整 config
systemd-analyze security                # 列每個 service security 分數
```

`security` 很實用：systemd 給每個 service 一個分數（`UNSAFE` / `MEDIUM` / `OK` / `GOOD`），列出可以加哪些 sandbox option。

## fsck 救機

如果 root filesystem 損壞（強制斷電、磁碟 bad sector），開機可能：

```
... fsck failed ...
... drop to maintenance shell ...
```

從 live USB 跑 fsck：

```bash
sudo fsck -y /dev/sda2
```

`-y` 自動回答 yes。**對 ext4 安全**，但對 XFS 看 manual。

## 重灌 GRUB 標準動線

從 live USB：

```bash
sudo mount /dev/sda2 /mnt              # root
sudo mount /dev/sda1 /mnt/boot/efi     # ESP
for d in dev sys proc run; do
    sudo mount --bind /$d /mnt/$d
done
sudo chroot /mnt

# 在 chroot 裡
update-grub                            # 或 grub-mkconfig
grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=ubuntu
# 或 BIOS:
grub-install /dev/sda

exit

# 出 chroot
sudo umount -R /mnt
sudo reboot
```

## 一個常見誤解：「dmesg 永遠顯示開機 log」

`dmesg` 看的是 kernel ring buffer，**會被新訊息覆蓋**。長時間運行的機器，dmesg 看不到開機訊息。

要看歷史 boot：`journalctl -k -b -1`（跨 boot 的 kernel log）。

## 一個常見誤解：「systemctl restart 一定能修問題」

如果 service 設了 `Restart=on-failure`、`StartLimitBurst=3`，連續失敗會被 systemd 標記為 too-fast，不再 restart：

```
foo.service: Start request repeated too quickly.
foo.service: Failed with result 'start-limit-hit'.
```

要 reset：

```bash
sudo systemctl reset-failed foo.service
sudo systemctl start foo.service
```

## 動手練習

**1. 故意弄壞 fstab**（VM 裡）

```bash
echo "UUID=fakefakefake /mnt/foo ext4 defaults 0 0" | sudo tee -a /etc/fstab
sudo reboot
```

開機卡在 "A start job is running for..."。看怎麼從 emergency mode 修。

**2. 故意 mask sshd**

```bash
sudo systemctl mask sshd
sudo systemctl restart sshd
# Failed to restart: Unit is masked.
sudo systemctl unmask sshd
```

**3. 跑 systemd-analyze security**

```bash
systemd-analyze security
```

看你機器有哪些 service 是 UNSAFE，找一個改 unit 加 sandbox option（PrivateTmp、ProtectSystem 等）後重跑看分數。

**4. 看 journal disk usage**

```bash
journalctl --disk-usage
sudo journalctl --vacuum-size=500M       # 限制 500MB
sudo journalctl --vacuum-time=2weeks      # 兩週前的清掉
```

**5. 練 chroot 救機**

VM 裡開 live USB（或拷一個 cloud image），mount + chroot 進原系統，跑 update-grub。練熟了真的需要救機才不會手忙腳亂。

## 自我檢核

- [ ] 看到症狀能對到哪一段壞
- [ ] 知道 `journalctl -b -1`、`-xb`、`-u` 的用法
- [ ] 跑過 `systemd-analyze blame / critical-chain / plot / security`
- [ ] 練過從 live USB chroot 修 GRUB
- [ ] 知道 `mask` 跟 `disable` 差別、`reset-failed` 是幹嘛的
- [ ] 知道 `nomodeset` 救黑屏、`init=/bin/sh` 救密碼

Part 6 結束。下一個 Part 7 看安全相關 + 最終整合專案。

→ [Ch 24 Secure Boot、TPM、measured boot](./24-secure-boot-and-tpm.md)
