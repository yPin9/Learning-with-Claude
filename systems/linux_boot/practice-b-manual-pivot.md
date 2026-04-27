# 練習 B — 手動 pivot 到 real rootfs

> 目標：在真機 / VM 上用 GRUB 攔截，cmdline 改 `init=/bin/sh` 跳過 systemd，然後手動完成「mount real root → chroot → exec init」這套，像 init script 一樣。

## 任務規格

| # | 任務 | 驗收標準 |
|---|---|---|
| 1 | GRUB 攔截開機 | 進到 GRUB menu、按 e 編輯 |
| 2 | 改 cmdline 加 break=premount | initramfs 在 mount root 前 drop shell |
| 3 | 在 (initramfs) shell 觀察狀態 | 看到 root device、看到 module、能 mount 試 |
| 4 | 用 break=mount 在不同階段 stop | 看到 root 已 mount 但還沒 switch |
| 5 | cmdline 改 init=/bin/sh，手動完成 boot | 從 single shell 走完 init |

## 實驗環境警告

**請在 VM 裡做這個練習，不要在你日常用的機器上做**。雖然不會 brick 任何東西，但很容易在過程中讓系統卡半小時開不了機。

最簡單的 VM：用 QEMU 跑一個你裝好的 Debian / Ubuntu cloud image：

```bash
# 抓 cloud image
wget https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2

# 跑（QEMU 會用 image 內建的 kernel + GRUB）
qemu-system-x86_64 -m 1024 \
  -drive file=debian-12-genericcloud-amd64.qcow2,if=virtio \
  -nographic \
  -enable-kvm
```

或直接用 `virt-install` / `multipass` / VirtualBox 裝一個 Ubuntu desktop / Arch。

## Step 1：進 GRUB menu

開機時：

- 大多數 distro 預設 hide menu — 開機按住 `Shift`（BIOS）或 `Esc`（UEFI）
- 出現 menu 後按 `e` 編輯預設 entry

你會看到類似：

```
linux /boot/vmlinuz-5.15.0-X root=UUID=abcd ro quiet splash
initrd /boot/initrd.img-5.15.0-X
```

## Step 2：break=premount

在 `linux` 行末加 `break=premount`，按 `Ctrl-X` 開機。

開機進入 initramfs，停在：

```
BusyBox v1.30.1 built-in shell (ash)
Enter 'help' for a list of built-in commands.

(initramfs) 
```

這時候 initramfs 跑了，但**還沒 mount real root**。看一下狀態：

```sh
(initramfs) ls /
bin  conf  etc  init  lib  proc  root  run  sbin  scripts  sys  tmp  usr  var

(initramfs) mount
none on /proc type proc (rw)
none on /sys type sysfs (rw)
udev on /dev type devtmpfs (rw)

(initramfs) ls /dev/disk/by-uuid/    # 看磁碟認到嗎
abcd-1234

(initramfs) cat /proc/cmdline
... root=UUID=abcd ... break=premount

(initramfs) lsmod
Module                  Size  Used by
... virtio_blk ...
... ext4 ...
```

接著手動 mount：

```sh
(initramfs) mkdir -p /tmp/test
(initramfs) mount /dev/disk/by-uuid/abcd-1234 /tmp/test
(initramfs) ls /tmp/test
bin  boot  dev  etc  home  lib  lib64  lost+found  media  mnt  opt  proc  root  ...
```

成功 mount 真 root 在 `/tmp/test`。**但這只是練習**，不要繼續 boot 因為 initramfs 後面 script 會被破壞。

按 `exit` 讓 initramfs 繼續正常 boot：

```sh
(initramfs) exit
# initramfs 接續 mount real root + switch_root
```

## Step 3：break=mount

換成 `break=mount`，這次停在 root 已 mount 但還沒 switch 的時候：

```sh
(initramfs) mount
... /dev/sda1 on /root type ext4 (ro,...)

(initramfs) ls /root
bin  boot  dev  etc  home  ...

(initramfs) exit    # 繼續 switch_root
```

dracut / mkinitcpio 還支援 `break=` 其他階段（`pre-trigger`、`pre-mount`、`mount`、`pre-pivot`、`bottom`）。

## Step 4：init=/bin/sh — 完全手動

這是最硬核版本：跳過 initramfs 後續邏輯 **+** 跳過 systemd。

cmdline 改成（記得移除 `quiet splash`）：

```
linux /boot/vmlinuz-... root=UUID=... ro init=/bin/sh
```

**注意**：保留 `root=`，這樣 initramfs 的 init script 會把 root mount 完、switch_root 過去；switch_root 後 kernel 看到 `init=/bin/sh`，exec sh 而不是 systemd。

進入 sh：

```sh
sh-5.0# 
```

`sh-5.0#` 表示你在 bash / sh，**不是** initramfs 的 ash。

```sh
sh-5.0# mount
/dev/sda1 on / type ext4 (ro,relatime)
... 沒有 /proc 沒有 /sys
```

注意：`/proc`、`/sys` 都沒 mount。systemd 平常會 mount 這些。手動補：

```sh
sh-5.0# mount -t proc none /proc
sh-5.0# mount -t sysfs none /sys
sh-5.0# mount -t devtmpfs none /dev
sh-5.0# mount -o remount,rw /
```

現在你有完整可用環境，但 PID 1 是 sh，沒 service、沒 network。

## Step 5：手動進 systemd

兩個方法：

**方法 A**：用 `exec`：

```sh
sh-5.0# exec /lib/systemd/systemd
```

`exec` 取代 PID 1。systemd 接手，正常開機。

**方法 B**：reboot：

```sh
sh-5.0# sync
sh-5.0# reboot -f
```

下次 GRUB 不改 cmdline 就正常開機。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>解答邏輯</summary>

### break=premount 在哪攔截

Debian 的 initramfs 主 script 是 `/init`（解開 initramfs 看就知道）。它呼叫 `/scripts/local`、`/scripts/local-top` 等多個階段。`break=premount` 在 `local` script 接管前停。

完整流程（簡化）：

```
/init
 → 解析 cmdline、mount /proc /sys /dev
 → run /scripts/init-top/*
 → run /scripts/init-premount/*
 → 【break=premount 在這停】
 → run /scripts/local-top/*    (LVM, MD raid, etc.)
 → run /scripts/local-premount/*
 → mount real root
 → 【break=mount 在這停】
 → run /scripts/local-bottom/*
 → switch_root
```

每個階段都可以用對應的 break 點 stop。

### init=/bin/sh 的 PID 1 path

kernel 的 PID 1 找 init 順序：

1. cmdline 的 `init=` 參數
2. cmdline 的 `rdinit=` 參數（但這只在 initramfs 階段用）
3. `/sbin/init`、`/etc/init`、`/bin/init`、`/bin/sh`

`init=/bin/sh` 跑在 **switch_root 之後**（因為 root 已掛上）。如果 root 沒掛 / 不正確，根本到不了 `init=` 那一步。

</details>

## 常見錯誤

| 症狀 | 原因 |
|---|---|
| 找不到 (initramfs) shell | distro 用 systemd-init 不用 break — 試 `rd.break` |
| `init=/bin/sh` 直接 panic | root 沒 mount 上 — 確認 `root=` 對 |
| sh 跑了但很多命令 not found | PATH 不全 — `export PATH=/usr/sbin:/usr/bin:/sbin:/bin` |
| `mount -t proc none /proc` 說 already mounted | 你跑了兩次 — `mount | grep proc` 確認 |
| `exec systemd` 卡死 | 多半 systemd 需要更多東西（`/run`、`/tmp`），先 mount 完整再 exec |

## 測試用例

| 動作 | 預期 |
|---|---|
| `cat /proc/cmdline` 在 initramfs | 看到完整 cmdline 含 `break=premount` |
| `lsmod` 在 initramfs | 看到 root 需要的 driver（virtio_blk, ext4） |
| `mount /dev/sda1 /tmp/test` 在 initramfs | 成功 mount，能 ls 出真 root |
| `exit` 在 initramfs | 繼續正常 boot |
| `init=/bin/sh` 開機後 `mount` | 看到 / 已 mount（ro）、其他都沒 |
| `exec /lib/systemd/systemd` | systemd 接手，service 開始啟動 |

## 進階挑戰

**1. 用 break=premount + 自己 mount root + 自己 switch_root**

完全手動跑 init script 的事情。用：

```sh
(initramfs) mount /dev/sda1 /root
(initramfs) mount --move /proc /root/proc
(initramfs) mount --move /sys  /root/sys
(initramfs) mount --move /dev  /root/dev
(initramfs) exec switch_root /root /sbin/init
```

**2. 改 root 密碼（救機常見場景）**

```
init=/bin/sh
```

進 shell：

```sh
mount -o remount,rw /
passwd root
sync
reboot -f
```

**3. fsck 強制執行**

某些 distro initramfs 跑 `fsck` 失敗會 drop shell，學手動修：

```sh
(initramfs) fsck -y /dev/sda1
(initramfs) exit
```

## 自我檢核

- [ ] 在 GRUB 編輯 cmdline、加 break=premount
- [ ] 在 (initramfs) shell 手動 mount 真 root
- [ ] 用 init=/bin/sh 跳過 systemd
- [ ] 從 single shell 用 `exec systemd` 接回正常 boot
- [ ] 知道每個 break 階段（premount, mount, ...）在做什麼

Part 5 完。下一個 Part 終於到 systemd — userspace 起來怎麼運作。

→ [Ch 20 PID 1 簡史](./20-pid-1-history.md)
