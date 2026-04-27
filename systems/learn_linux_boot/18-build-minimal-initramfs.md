# Ch 18 — 動手：自製最小 initramfs

> 目標：從零組一個 initramfs：busybox + 自寫 /init，在 QEMU 跑起來看到 shell。

## 我們在哪裡

第 5 階段 (initramfs) 的實作版。對照 Ch 6（自製 boot sector）跟 Ch 12（自製 UEFI app）。

## 計畫

我們要建一個目錄結構、放進 busybox + 一個 `/init` script，cpio + gzip，然後讓 QEMU 載這個 initramfs + 一個 distro kernel。

```
initramfs/
├── bin/busybox            # 提供所有命令
├── sbin/                  # symlinks 到 busybox
├── usr/{bin,sbin}/        # symlinks
├── proc/                  # mount point
├── sys/                   # mount point
├── dev/                   # mount point
└── init                   # 我們的 init script
```

## 準備 busybox

busybox 是「The Swiss Army knife of Embedded Linux」：一個 binary 提供 sh / ls / mount / cp 等 300+ 命令。

```bash
# Debian / Ubuntu
sudo apt install busybox-static
file /bin/busybox
# /bin/busybox: ELF 64-bit LSB executable, x86-64, statically linked, ...
```

「statically linked」很重要 — initramfs 不一定有 glibc。static busybox 自帶所有 lib。

如果你的 busybox 不是 static，自己 build：

```bash
wget https://busybox.net/downloads/busybox-1.36.1.tar.bz2
tar xf busybox-1.36.1.tar.bz2
cd busybox-1.36.1
make defconfig
# 開 static
sed -i 's/.*CONFIG_STATIC.*/CONFIG_STATIC=y/' .config
make -j$(nproc)
ls _install/bin/busybox
```

## Step 1：建目錄結構

```bash
cd /tmp
mkdir -p initramfs/{bin,sbin,etc,proc,sys,dev,usr/bin,usr/sbin,run,mnt/root}
cd initramfs

# 拷 busybox
cp /bin/busybox bin/

# 建 symlinks（busybox 的命令都是 symlink 到 busybox 本體）
cd bin
for cmd in sh ls cat mount umount mkdir cp mv rm echo pwd ps kill; do
    ln -s busybox $cmd
done
cd ..

# 同樣 sbin、usr/bin、usr/sbin
cd sbin
for cmd in init poweroff reboot halt switch_root; do
    ln -s ../bin/busybox $cmd
done
cd ..
```

或用 busybox 內建的 install 自動做：

```bash
busybox --install bin/    # ❌ 這會 install 到當前 PATH，不要在 host 上做
```

危險，不要在自己 / 上做。我們手動做 symlink 比較安全。

## Step 2：寫 /init

```bash
cat > init <<'EOF'
#!/bin/sh
# Minimal initramfs init script

echo ""
echo "=========================================="
echo "  Hello from custom initramfs!"
echo "=========================================="
echo ""

# Mount essential filesystems
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev

echo "Mounted /proc, /sys, /dev"
echo ""

echo "Kernel: $(uname -a)"
echo "Cmdline: $(cat /proc/cmdline)"
echo ""

echo "Available block devices:"
ls /dev/ | grep -E '^(sd|vd|nvme|hd)' || echo "  (none found)"
echo ""

echo "Dropping to shell. Type 'exit' to halt."
echo ""

# 給一個 shell
exec /bin/sh
EOF

chmod +x init
```

## Step 3：打包成 cpio + gzip

```bash
find . -print0 | cpio --null --create --format=newc | gzip > ../initramfs.cpio.gz
ls -lh ../initramfs.cpio.gz
```

`find -print0` + `cpio --null` 是處理檔名含空白的 idiom。

`--format=newc` = SVR4，kernel 認的格式。

## Step 4：用 QEMU 跑

需要一個 kernel。最簡單：用你機器上的：

```bash
qemu-system-x86_64 \
  -kernel /boot/vmlinuz-$(uname -r) \
  -initrd /tmp/initramfs.cpio.gz \
  -append "console=ttyS0" \
  -nographic \
  -m 256
```

`-kernel` / `-initrd` 是 QEMU 直接 boot 模式 — bypass bootloader，QEMU 自己解析 boot protocol。

`console=ttyS0` 把 kernel log 跟 init 輸出走 serial（給 `-nographic`）。

跑起來你會看到：

```
[    0.000000] Linux version 5.15.0-... 
[    0.000000] Command line: console=ttyS0
...
[    1.234567] Run /init as init process
==========================================
  Hello from custom initramfs!
==========================================

Mounted /proc, /sys, /dev

Kernel: Linux (none) 5.15.0-... 
Cmdline: console=ttyS0

Available block devices:
  (none found)

Dropping to shell. Type 'exit' to halt.

/ # 
```

你拿到一個 shell，跑在你自己的 initramfs 裡。

## 玩你的 shell

```sh
/ # ls /
bin   dev   etc   init  mnt   proc  run   sbin  sys   usr

/ # ps
  PID USER       VSZ STAT COMMAND
    1 0          864 S    /bin/sh
    2 0            0 SW   [kthreadd]
    ...

/ # cat /proc/meminfo | head
MemTotal:         247700 kB
MemFree:          221980 kB
...

/ # mount
none on /proc type proc (rw,relatime)
none on /sys type sysfs (rw,relatime)
none on /dev type devtmpfs (rw,relatime,size=124848k,nr_inodes=31212,mode=755)

/ # exit
```

退出後 kernel panic 因為 PID 1 死了：

```
[  123.456] Kernel panic - not syncing: Attempted to kill init! exitcode=0x00000000
```

這是 PID 1 的特權 / 詛咒：死了 = panic。

## 加 disk 進 QEMU

要看 `/dev/sda` 之類，給 QEMU 一個 virtio disk：

```bash
# 建一個假磁碟
dd if=/dev/zero of=disk.img bs=1M count=64
mkfs.ext4 disk.img

# 跑 QEMU 帶 disk
qemu-system-x86_64 \
  -kernel /boot/vmlinuz-$(uname -r) \
  -initrd /tmp/initramfs.cpio.gz \
  -drive file=disk.img,if=virtio,format=raw \
  -append "console=ttyS0" \
  -nographic \
  -m 256
```

進 shell 後：

```sh
/ # ls /dev/vd*
/dev/vda

/ # mount /dev/vda /mnt/root
/ # ls /mnt/root
lost+found
```

你 mount 了一個 virtio disk。**這正是 initramfs 該做的事**（後面 Ch 19 會 switch_root 到這）。

## /init 進階：根據 cmdline 找 root

實務 initramfs 會 parse cmdline 找 root。簡化版：

```sh
#!/bin/sh
mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev

# 找 root=
ROOT=$(cat /proc/cmdline | tr ' ' '\n' | grep ^root= | cut -d= -f2)

if [ -z "$ROOT" ]; then
    echo "No root= specified, dropping to shell"
    exec /bin/sh
fi

echo "Mounting root from $ROOT"
mount -o ro $ROOT /mnt/root || {
    echo "Failed to mount root!"
    exec /bin/sh
}

echo "Switching to real root..."
exec switch_root /mnt/root /sbin/init
```

把這個拿去打包，用 cmdline 加 `root=/dev/vda` 試試 — 它會 mount + switch_root 到 disk.img。但 disk.img 沒 init binary，會 fail；要先在裡面建 `/sbin/init`。Ch 19 會完整做這一步。

## 一個常見踩雷：忘了 chmod +x init

```bash
ls -l init
# -rw-rw-r-- ...   ❌
```

kernel 沒法 exec 它，會試 `/sbin/init`、`/etc/init`、`/bin/init`、`/bin/sh`，全部失敗就 panic。

**修**：`chmod +x init` 後重新打包 cpio。

## 一個常見踩雷：busybox 是 dynamic linked

```bash
file /bin/busybox
# ... dynamically linked ...   ❌
```

initramfs 沒 ld.so 跟 libc.so，busybox load 不起來。

**修**：用 `busybox-static` 套件，或自己 build static 版本。

## 一個常見踩雷：cpio format 錯

```bash
find . | cpio -o > foo.cpio    # ❌ 預設是 odc 格式
```

kernel 認 newc，不認 odc / bin / crc 等其他格式。

**修**：`cpio --format=newc`。

## 一個常見踩雷：cpio 沒包含目錄本身

```bash
find . -type f | cpio ...      # ❌ 只包含檔案，沒包目錄
```

initramfs 解開後沒目錄結構，mount 點不存在。

**修**：去掉 `-type f`，讓 find 列出檔案 + 目錄。

## 動手練習

**1. 跑通基本版**

照上面 step 跑出 shell。

**2. 加你的客製訊息**

改 `/init` 加 banner、印環境變數、印 timezone。

**3. 加 utility**

把 `vi`、`top`、`free` 等 busybox 命令加進去（建 symlink）。

**4. 列出所有 PCI device**

```sh
/ # ls /sys/bus/pci/devices/
/ # cat /sys/bus/pci/devices/0000:00:00.0/vendor
```

寫進 init script，開機自動印。

**5. 用 dmesg 觀察 initramfs 載入**

```sh
/ # dmesg | grep -i initramfs
[    1.234] Trying to unpack rootfs image as initramfs...
[    1.456] Freeing initrd memory: 1234K
```

「Freeing initrd memory」表示 initramfs 解壓完，原本 cpio image 那塊記憶體 release。

## 自我檢核

- [ ] 自己組一個 initramfs，QEMU 跑出 shell
- [ ] 知道 busybox 必須 static linked
- [ ] 知道 cpio 必須 newc format
- [ ] 知道 `/init` 必須 chmod +x
- [ ] 在 shell 裡 mount /proc /sys /dev、玩 ps / mount

下一章接著做：`switch_root` 切到真實 rootfs。

→ [Ch 19 switch_root / pivot_root](./19-pivot-root.md)
