# Ch 19 — switch_root / pivot_root

> 目標：搞清楚 initramfs 切到真實 rootfs 的兩種方式、它們做了什麼系統呼叫、為什麼這樣設計。

## 我們在哪裡

第 6 階段。initramfs 跑完它的工作，要把 root 換成真實磁碟上的 rootfs。

## 為什麼不能直接 chroot

直覺想：mount 真 root 到 `/mnt/root`、`chroot /mnt/root /sbin/init` 不就好了？

不行。`chroot` 只是把 process 的 root 改掉，**但其他 process 還在舊 root**。而且：

- 舊 root 還掛著、佔記憶體（initramfs 整個 ramfs 還在）
- 新 process 看到的 mount table 還含舊掛載
- `/proc/PID/root` 顯示的還是舊路徑

我們要的是：

- **整個 namespace 換 root**
- **舊 root unmount + 釋放記憶體**
- **新 process 是真的 PID 1**

這需要 `pivot_root` 或 `switch_root` 這種專門系統呼叫 / 工具。

## pivot_root 是什麼

`pivot_root(new_root, put_old)` 系統呼叫做兩件事：

1. 把目前 mount tree 的 root 換成 `new_root`
2. 把舊 root 移到 `put_old`（必須在 new_root 內）

範例：

```c
mount("/dev/sda1", "/mnt/root", "ext4", 0, NULL);
chdir("/mnt/root");
pivot_root(".", "old_root");
chroot(".");
umount2("/old_root", MNT_DETACH);
```

跑完之後：
- 新 root 是 `/dev/sda1`
- 原本的 ramfs 在 `/old_root`
- `umount` 後完全消失

`pivot_root` 是 `initrd` 時代的工具（block device 形式的 initrd）。

## switch_root 是什麼

`switch_root` 是針對 initramfs（ramfs）設計的更簡單版本。它不是系統呼叫，而是 **userspace 工具**（busybox 跟 systemd 各有實作）。

它做的事：

1. 把舊 ramfs 上的所有檔案 **delete**（釋放記憶體）
2. 把 new root mount move 到 `/`
3. exec 指定的 init binary

簡化的 C code：

```c
int switch_root(const char *new_root, const char *init)
{
    /* 1. 在 new_root 下建立必要的 mount move */

    /* 2. 移動 mount */
    chdir(new_root);
    mount(".", "/", NULL, MS_MOVE, NULL);
    chroot(".");

    /* 3. 重設 cwd */
    chdir("/");

    /* 4. exec init */
    execv(init, ...);
}
```

關鍵：`MS_MOVE` flag 讓 mount move 而不是 mount on top。

## switch_root 的「刪光舊 root」

很多教程跳過這個。實際 switch_root 在 move mount 前會 **遞迴 unlink 舊 root 上的所有檔案**：

```c
recursive_unlink(old_root_dir, dev_of_old_root);
```

只刪同一個 device 上的檔案 — 確保不誤刪 mount 進來的子 fs。

為什麼要刪？因為 ramfs 不會自動釋放記憶體，必須 unlink 檔案才釋放。move mount 後舊 ramfs 沒地方掛了，但檔案還佔記憶體 — 所以要先 delete。

「Freeing unused kernel memory」之後 dmesg 看到 RAM 釋放就是這。

## 實作：完整 initramfs + switch_root

延續 Ch 18 的 initramfs，加 switch_root 邏輯。

`init`：

```sh
#!/bin/sh

mount -t proc none /proc
mount -t sysfs none /sys
mount -t devtmpfs none /dev

echo "[init] Looking for root..."
sleep 1     # 等 udev / driver 偵測

ROOT=$(cat /proc/cmdline | tr ' ' '\n' | grep ^root= | cut -d= -f2)
[ -z "$ROOT" ] && ROOT=/dev/vda

# 等待 device 出現
TIMEOUT=10
while [ ! -b "$ROOT" ] && [ $TIMEOUT -gt 0 ]; do
    echo "[init] Waiting for $ROOT..."
    sleep 1
    TIMEOUT=$((TIMEOUT - 1))
done

if [ ! -b "$ROOT" ]; then
    echo "[init] $ROOT not found, dropping to shell"
    exec /bin/sh
fi

echo "[init] Mounting $ROOT to /mnt/root..."
mount -o ro "$ROOT" /mnt/root || {
    echo "[init] Mount failed!"
    exec /bin/sh
}

echo "[init] Moving /proc /sys /dev to new root..."
mount --move /proc /mnt/root/proc
mount --move /sys  /mnt/root/sys
mount --move /dev  /mnt/root/dev

echo "[init] Switching to real root..."
exec switch_root /mnt/root /sbin/init
```

## 準備真實 rootfs (disk.img)

要 switch_root 過去，disk 上必須有 `/sbin/init`。建一個最小的：

```bash
# 建 disk
dd if=/dev/zero of=disk.img bs=1M count=64
mkfs.ext4 disk.img

# Mount 來填內容
mkdir /tmp/rootmnt
sudo mount -o loop disk.img /tmp/rootmnt
sudo mkdir -p /tmp/rootmnt/{bin,sbin,etc,proc,sys,dev,usr/bin}
sudo cp /bin/busybox /tmp/rootmnt/bin/
for c in sh ls cat ps mount umount; do
    sudo ln -sf busybox /tmp/rootmnt/bin/$c
done
sudo ln -sf ../bin/busybox /tmp/rootmnt/sbin/init

# 寫個 fake /sbin/init（busybox init 也行）
sudo tee /tmp/rootmnt/sbin/init >/dev/null <<'EOF'
#!/bin/sh
echo ""
echo "==========================================="
echo "  Hello from REAL root!"
echo "==========================================="
echo ""
echo "rootfs check:"
ls -la /
echo ""
echo "Dropping to shell."
exec /bin/sh
EOF
sudo chmod +x /tmp/rootmnt/sbin/init

# 要不就用 symlink 到 busybox
# sudo ln -sf ../bin/busybox /tmp/rootmnt/sbin/init

sudo umount /tmp/rootmnt
```

## 跑起來

```bash
# 重新打包 initramfs
cd /tmp/initramfs
find . -print0 | cpio --null --create --format=newc | gzip > ../initramfs.cpio.gz

# QEMU + 兩個 disk: kernel 與 initramfs + virtio disk
qemu-system-x86_64 \
  -kernel /boot/vmlinuz-$(uname -r) \
  -initrd /tmp/initramfs.cpio.gz \
  -drive file=/tmp/disk.img,if=virtio,format=raw \
  -append "console=ttyS0 root=/dev/vda" \
  -nographic \
  -m 256
```

成功的話會看到：

```
[init] Looking for root...
[init] Mounting /dev/vda to /mnt/root...
[init] Moving /proc /sys /dev to new root...
[init] Switching to real root...

===========================================
  Hello from REAL root!
===========================================

rootfs check:
total 17
drwxr-xr-x   12 0   0           1024 ...
...

Dropping to shell.
/ # 
```

你的 PID 1 已經在 disk.img 的 rootfs 裡了。檢查：

```sh
/ # mount
/dev/vda on / type ext4 (ro,...)
none on /proc type proc (rw,...)
none on /sys type sysfs (rw,...)
none on /dev type devtmpfs (rw,...)

/ # ls /old_root || echo "old root gone"
old root gone
```

舊 ramfs 完全消失。

## switch_root 跟 pivot_root 對照

| 項目 | switch_root | pivot_root |
|---|---|---|
| 用於 | initramfs (ramfs) | initrd (block device) |
| 是 | userspace tool | syscall |
| 舊 root | 完全 unlink + 釋放 | 移到指定路徑、要手動 umount |
| put_old 路徑 | 不需要 | 需要 |
| 釋放 RAM | ✅ | 自動（umount 後） |

## 一個常見踩雷：忘了 mount move /proc

switch_root 後新 root 沒 `/proc`、`/sys`、`/dev`。systemd / 其他 process 啟動時找不到 `/proc/cmdline`、`/proc/self`，馬上死。

修：在 switch_root 前 `mount --move /proc /mnt/root/proc` 等。

## 一個常見踩雷：switch_root 用 sh script 寫

switch_root 必須是 PID 1 直接 exec。寫成：

```sh
switch_root /mnt/root /sbin/init        # ❌ shell fork 一個 child
```

那個 switch_root 是 child process，PID 不是 1。它做完後 parent shell 還是 PID 1 但已經沒事做。

修：用 `exec`：

```sh
exec switch_root /mnt/root /sbin/init   # ✅ 取代 shell process
```

## 一個常見踩雷：new root 的 init binary 不存在 / 不能執行

```sh
exec switch_root /mnt/root /sbin/init
# init: Can't open /sbin/init: No such file or directory
```

或：

```
init: /sbin/init: Exec format error
```

確認：

- `ls /mnt/root/sbin/init` 存在
- `chmod +x` 過
- 是 native binary（如果是 cross-compile，arch 對得上）
- 如果是 dynamic binary，依賴的 lib 在 `/mnt/root/lib`

## 一個常見踩雷：忘了 sleep 等 udev

NVMe / virtio device 需要時間出現。switch_root 之前 `sleep 1` 看似 ugly 但有效。實務 dracut / mkinitcpio 用 udev 等 device event。

## 動手練習

**1. 跑通基本版**

照上面 step 跑出 "Hello from REAL root!"。

**2. 把 /sbin/init 換成 systemd**

把 host 機器的 systemd binary 拷到 disk.img 的 `/lib/systemd/systemd`，建 symlink `/sbin/init` 指過去。看 systemd 能不能在 minimal env 起來（多半起不來，缺 libs，但很有教育性）。

**3. 把 root device 改成不存在**

cmdline `root=/dev/vdz`，看 init script 怎麼 timeout 進 shell。

**4. 比較 switch_root 跟手動做的**

先試 switch_root，再試自己用 mount --move + chroot 做。看 `/old_root` 是否殘留。

**5. 用 strace switch_root**

```sh
strace busybox switch_root /mnt/root /bin/sh
```

看實際的 syscall sequence：unlink、mount、chroot、execve。

## 自我檢核

- [ ] 自己 initramfs + switch_root 到真 disk
- [ ] 知道 switch_root 跟 pivot_root 差別
- [ ] 知道為什麼要 `exec switch_root`（不能 fork）
- [ ] 知道為什麼要先 mount --move /proc /sys /dev
- [ ] 看 dmesg 確認 ramfs 釋放了

下一個是 Part 5 的整合練習：自己手動把整個 boot 流程操作一次。

→ [練習 B：手動 pivot 到 real rootfs](./practice-b-manual-pivot.md)
