# Final Project — 從零組最小 Linux

> 目標：把整套課程學的東西黏起來。自己 build kernel、自己組 initramfs、自己準備 rootfs，分別用 BIOS 跟 UEFI 兩種方式在 QEMU 跑通。完成的話你對 Linux 開機流程的理解會比 99% 的工程師深。

## 任務規格

完成下列五個 milestone：

| # | 目標 | 驗收 |
|---|---|---|
| M1 | 自己 build 一個 minimal Linux kernel | `make defconfig` + 修剪到能開機 |
| M2 | 組一個 busybox-only initramfs | 能進 `(initramfs) #` shell |
| M3 | 用 BIOS + GRUB 開機 | QEMU 看到 GRUB menu → kernel → shell |
| M4 | 用 UEFI + EFI stub 直接開機 | OVMF + 直接 boot kernel，無 GRUB |
| M5 | 切到 disk 上的 real rootfs | switch_root 到第二顆 disk |

## 期望輸出

完成的目錄結構：

```
my-linux/
├── linux/                    # kernel source
│   └── arch/x86/boot/bzImage
├── initramfs/                # 你組的 initramfs
│   ├── init
│   ├── bin/busybox
│   └── ...
├── rootfs/                   # 完整一點的 rootfs
│   └── ...
├── disk-bios.img             # BIOS 開機磁碟（含 GRUB + kernel）
├── disk-uefi.img             # UEFI 開機磁碟（GPT + ESP）
└── rootfs.img                # 真實 rootfs（要被 switch_root 過去）
```

最後 demo：

```bash
# BIOS
qemu-system-x86_64 -drive file=disk-bios.img,format=raw -nographic

# UEFI (EFI stub direct)
qemu-system-x86_64 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=OVMF_VARS.fd \
  -drive file=disk-uefi.img,format=raw \
  -nographic
```

兩種都跑出 shell 就是成功。

## 實作步驟建議

### M1：Build minimal kernel

```bash
# 抓 source
git clone --depth=1 --branch=v6.6 https://github.com/torvalds/linux.git
cd linux

# defconfig（已經很全）
make defconfig

# 用 menuconfig 砍：CD-ROM、軟碟、wifi、各種 NIC...
# 開機只需要：virtio_blk, virtio_pci, ext4, devtmpfs, tty
make menuconfig

# Build
make -j$(nproc) bzImage

ls arch/x86/boot/bzImage
# arch/x86/boot/bzImage   # ~5-15MB
```

砍 config 是個學習過程。target：bzImage 從 default 60MB 砍到 10MB 以下。

關鍵 config：
- `CONFIG_BLK_DEV_INITRD=y` — initrd 支援
- `CONFIG_RD_GZIP=y` — 壓縮 initramfs
- `CONFIG_DEVTMPFS=y` + `CONFIG_DEVTMPFS_MOUNT=y` — 自動 /dev
- `CONFIG_VIRTIO_BLK=y`、`CONFIG_VIRTIO_PCI=y` — virtio disk
- `CONFIG_EXT4_FS=y` — root fs
- `CONFIG_PRINTK=y` — debug log
- `CONFIG_SERIAL_8250=y` + `CONFIG_SERIAL_8250_CONSOLE=y` — serial console
- `CONFIG_EFI_STUB=y` — UEFI 直接 boot

### M2：Initramfs

複用 Ch 18 做的：

```bash
mkdir -p initramfs/{bin,sbin,etc,proc,sys,dev,mnt/root}
cp /bin/busybox initramfs/bin/
cd initramfs/bin
for c in sh ls cat mount umount cp mkdir rm echo; do
    ln -sf busybox $c
done
cd ../sbin
for c in switch_root reboot poweroff; do
    ln -sf ../bin/busybox $c
done
cd ..

cat > init <<'EOF'
#!/bin/sh
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev
echo "[init] from custom initramfs"

ROOT=$(cat /proc/cmdline | tr ' ' '\n' | grep ^root= | cut -d= -f2)
if [ -n "$ROOT" ] && [ -b "$ROOT" ]; then
    echo "[init] mounting $ROOT"
    mount -o ro "$ROOT" /mnt/root
    mount --move /proc /mnt/root/proc
    mount --move /sys  /mnt/root/sys
    mount --move /dev  /mnt/root/dev
    exec switch_root /mnt/root /sbin/init
fi

echo "[init] no root, dropping to shell"
exec /bin/sh
EOF
chmod +x init

find . -print0 | cpio --null --create --format=newc | gzip > ../initramfs.cpio.gz
```

### M3：BIOS + GRUB disk

```bash
cd ..
# 建 100MB disk
dd if=/dev/zero of=disk-bios.img bs=1M count=100

# Partition: 1 個 ext2 partition 從 LBA 2048
sfdisk disk-bios.img <<EOF
label: dos
start=2048, type=83, bootable
EOF

# Loop mount
sudo losetup -fP --show disk-bios.img
# /dev/loopX
LOOP=$(losetup -j disk-bios.img | head -1 | cut -d: -f1)
sudo mkfs.ext2 ${LOOP}p1

mkdir mnt
sudo mount ${LOOP}p1 mnt

# 拷 kernel + initramfs
sudo mkdir -p mnt/boot/grub
sudo cp linux/arch/x86/boot/bzImage mnt/boot/vmlinuz
sudo cp initramfs.cpio.gz mnt/boot/

# 寫 grub.cfg
sudo tee mnt/boot/grub/grub.cfg > /dev/null <<'EOF'
set timeout=2
set default=0

menuentry "MyLinux (BIOS)" {
    linux /boot/vmlinuz console=ttyS0
    initrd /boot/initramfs.cpio.gz
}
EOF

# 裝 GRUB
sudo grub-install --target=i386-pc --boot-directory=mnt/boot ${LOOP}

sudo umount mnt
sudo losetup -d ${LOOP}

# 跑！
qemu-system-x86_64 -drive file=disk-bios.img,format=raw -nographic
```

成功的話：GRUB menu 倒數 2 秒 → 載 kernel → 解 initramfs → init script → shell。

### M4：UEFI + EFI stub

```bash
# 建 100MB disk，GPT partition
dd if=/dev/zero of=disk-uefi.img bs=1M count=100
sgdisk -n 1:0:+50M -t 1:ef00 -c 1:"ESP" disk-uefi.img

# Loop
LOOP=$(sudo losetup -fP --show disk-uefi.img)

# Format ESP
sudo mkfs.fat -F32 ${LOOP}p1

# Mount + 放檔案
mkdir esp
sudo mount ${LOOP}p1 esp
sudo mkdir -p esp/EFI/BOOT
# bzImage 自帶 EFI stub，可以直接當 .efi
sudo cp linux/arch/x86/boot/bzImage esp/EFI/BOOT/BOOTX64.EFI
sudo cp initramfs.cpio.gz esp/initramfs.cpio.gz

sudo umount esp
sudo losetup -d ${LOOP}

# OVMF VARS
cp /usr/share/OVMF/OVMF_VARS.fd ./OVMF_VARS.fd

# 跑！但這版 fallback 沒有 cmdline，要進 UEFI shell 手動
qemu-system-x86_64 \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE.fd \
  -drive if=pflash,format=raw,file=./OVMF_VARS.fd \
  -drive file=disk-uefi.img,format=raw \
  -nographic
```

進 UEFI shell：

```
Shell> fs0:
FS0:\> EFI\BOOT\BOOTX64.EFI initrd=initramfs.cpio.gz console=ttyS0
```

EFI stub 認 cmdline 的 `initrd=`，自動載 ESP 上的 initramfs。

### M5：Real rootfs + switch_root

```bash
# 建 rootfs disk
dd if=/dev/zero of=rootfs.img bs=1M count=64
mkfs.ext4 rootfs.img

# Mount + 填內容
mkdir rootmnt
sudo mount -o loop rootfs.img rootmnt
sudo mkdir -p rootmnt/{bin,sbin,etc,proc,sys,dev,usr/bin,lib}
sudo cp /bin/busybox rootmnt/bin/
for c in sh ls cat ps mount umount echo; do
    sudo ln -sf busybox rootmnt/bin/$c
done

# /sbin/init = 自寫 script
sudo tee rootmnt/sbin/init > /dev/null <<'EOF'
#!/bin/sh
echo "==========================================="
echo "  PID 1 from REAL rootfs (Final Project!)"
echo "==========================================="
exec /bin/sh
EOF
sudo chmod +x rootmnt/sbin/init

sudo umount rootmnt

# 跑：兩個 disk，第一個是 BIOS+GRUB，第二個是 rootfs
qemu-system-x86_64 \
  -drive file=disk-bios.img,format=raw,if=virtio \
  -drive file=rootfs.img,format=raw,if=virtio \
  -nographic
```

但這個版本，cmdline 沒指定 root，initramfs 直接 drop shell。改 grub.cfg 的 cmdline 加 `root=/dev/vdb`（第二個 virtio disk）：

```
linux /boot/vmlinuz console=ttyS0 root=/dev/vdb
```

重做 disk-bios.img（重 mount + 改 grub.cfg），再跑：

```
[init] from custom initramfs
[init] mounting /dev/vdb
===========================================
  PID 1 from REAL rootfs (Final Project!)
===========================================
/ #
```

成功 switch_root 到 real rootfs。

## 完整參考解答

**寫完再看！不要偷看**，否則學不到東西。

<details>
<summary>所有 script 的整合版</summary>

`build-all.sh`：

```bash
#!/bin/bash
set -e

# === M1: Kernel ===
cd linux
make defconfig
# minimum config patches
scripts/config -e CONFIG_VIRTIO_BLK -e CONFIG_VIRTIO_PCI \
              -e CONFIG_EXT4_FS -e CONFIG_DEVTMPFS \
              -e CONFIG_DEVTMPFS_MOUNT -e CONFIG_EFI_STUB \
              -e CONFIG_SERIAL_8250_CONSOLE
make olddefconfig
make -j$(nproc) bzImage
cd ..

# === M2: Initramfs ===
rm -rf initramfs
mkdir -p initramfs/{bin,sbin,etc,proc,sys,dev,mnt/root}
cp /bin/busybox initramfs/bin/
( cd initramfs/bin && for c in sh ls cat mount umount cp mkdir rm echo; do ln -sf busybox $c; done )
( cd initramfs/sbin && for c in switch_root reboot poweroff; do ln -sf ../bin/busybox $c; done )

cat > initramfs/init <<'EOF'
#!/bin/sh
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev
echo "[init] from custom initramfs"
ROOT=$(cat /proc/cmdline | tr ' ' '\n' | grep ^root= | cut -d= -f2)
if [ -n "$ROOT" ] && [ -b "$ROOT" ]; then
    echo "[init] mounting $ROOT"
    mount -o ro "$ROOT" /mnt/root
    mount --move /proc /mnt/root/proc
    mount --move /sys  /mnt/root/sys
    mount --move /dev  /mnt/root/dev
    exec switch_root /mnt/root /sbin/init
fi
echo "[init] no root, dropping to shell"
exec /bin/sh
EOF
chmod +x initramfs/init

( cd initramfs && find . -print0 | cpio --null --create --format=newc | gzip > ../initramfs.cpio.gz )

# === M3 / M4 / M5 同上 ===
```

</details>

## 常見錯誤

| 症狀 | 原因 |
|---|---|
| kernel build fail "missing libelf" | `sudo apt install libelf-dev libssl-dev bison flex` |
| GRUB install fail "embedding ... not possible" | partition 從 LBA 1 開始，沒留空間給 GRUB stage 1.5 — 從 LBA 2048 |
| OVMF + EFI stub 沒看到 menu | 沒設 fallback 名稱、或路徑錯 — 確認 `EFI/BOOT/BOOTX64.EFI` |
| switch_root 後 `Cannot exec /sbin/init` | rootfs 上 `/sbin/init` 沒 chmod +x |
| init script 跑了但找不到 /dev/vdb | virtio_blk 沒進 kernel — 重編 |
| OVMF 中文亂碼 | UEFI console 是 UTF-16，shell 的 `echo` 用 ASCII — 正常但醜 |

## 進階挑戰

### A：用 systemd 當 PID 1

把 rootfs 換成完整 Debian / Alpine / Arch chroot，systemd 當 init。

```bash
sudo debootstrap bookworm rootmnt http://deb.debian.org/debian/
```

挑戰：systemd 需要 cgroup v2、需要 dbus、需要 udev — 都要在 minimal kernel 裡 enable。

### B：開 Secure Boot

簽自己的 kernel + GRUB，灌進 OVMF 的 db。

### C：UKI (Unified Kernel Image)

把 kernel + initramfs + cmdline + osinfo 打包成一個 .efi（systemd 推的格式），用 `ukify` 工具：

```bash
ukify build --linux=bzImage --initrd=initramfs.cpio.gz --cmdline="console=ttyS0 root=/dev/vdb"
```

放 ESP，UEFI 直接 boot 一個檔案搞定整個。

### D：TPM unsealing

VM 加 TPM 模擬：

```bash
qemu-system-x86_64 ... -tpmdev emulator,id=tpm0,chardev=chrtpm \
  -chardev socket,id=chrtpm,path=/tmp/swtpm-sock \
  -device tpm-tis,tpmdev=tpm0
```

在 initramfs 裡用 `tpm2-tools` 讀 PCR、unseal LUKS key。

### E：netboot

不用 disk image，PXE / iPXE boot kernel + initramfs。

## 自我檢核

- [ ] 自己 build 出 < 15MB 的 minimal kernel
- [ ] 寫出 < 5MB 的 initramfs，能 drop shell + switch_root
- [ ] 用 BIOS + GRUB 在 QEMU boot 起來
- [ ] 用 UEFI + EFI stub 在 OVMF boot 起來
- [ ] 完成 switch_root 到第二顆 disk
- [ ] 整套不用 docker / 別人的 image，全部自己 build

恭喜完課！如果你做完整套，你已經對 Linux 開機有比 95% 工程師都深的理解。後面你能：

- 看到任何 boot bug 都知道從哪段 debug
- 改 distro 不再是黑魔法
- 寫 embedded / cloud image / installer 有底氣
- 看 kernel source `arch/x86/boot/` 不會慌

接下來如果還想深入：

- **ARM boot**：U-Boot、device tree、跟 x86 截然不同的世界
- **kexec / kdump**：boot 一個新 kernel 而不重開機
- **firmware reverse**：用 UEFITool 拆 firmware image
- **coreboot**：開源 BIOS / UEFI 替代品
- **Linux kernel 開發**：正式進 kernel 大門

→ 回到 [課程地圖](./README.md)
