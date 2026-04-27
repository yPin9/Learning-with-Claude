# Ch 17 — initramfs 是什麼

> 目標：搞懂 initramfs 為什麼存在、跟舊的 initrd 差在哪、cpio 格式怎麼長。

## 我們在哪裡

第 5 階段 (initramfs)。kernel 已經 `start_kernel` 完、要找 root filesystem 了。

## 雞生蛋的問題

kernel 啟動後想 mount root filesystem，但：

- root 在 NVMe → 要 NVMe driver
- NVMe driver 是 module → 要從某個檔案系統讀這個 module
- 那個檔案系統是 root → 還沒 mount

**死鎖**。要 mount root 才能讀 driver、要讀 driver 才能 mount root。

幾種解法：

1. **把所有 driver build-in kernel** — kernel 變肥（5MB → 50MB）、不靈活
2. **kernel 自帶一個簡單 initrd** — 早期 Linux 走這條
3. **bootloader 載一個臨時 root（initramfs），kernel 從那讀 driver、再切到真的 root** — 現代做法

選 3。initramfs 就是「臨時 userspace」。

## initrd vs initramfs

兩個常被混用，但有歷史差別：

| 項目 | initrd（舊） | initramfs（新） |
|---|---|---|
| 出現年代 | 1996 | 2002 |
| 形式 | block device image (ext2 / cramfs) | cpio archive |
| Mount 位置 | 真的 mount 成 `/dev/ram0` | unpack 到 ramfs (memory-only fs) |
| 檔案大小 | 固定 | 動態 |
| 切換到 real root | `pivot_root` | `switch_root` |
| 現代狀態 | 幾乎沒人用 | 主流 |

Debian / Ubuntu 還叫 `initrd.img-X.Y`，**內容其實是 initramfs**（cpio + gzip）。檔名只是歷史遺跡。

```bash
file /boot/initrd.img-$(uname -r)
# /boot/initrd.img-X: ASCII cpio archive (SVR4 with no CRC)  ← 早期
# 或
# /boot/initrd.img-X: gzip compressed data, max compression  ← 現代（包了一層）
```

## cpio 是什麼

cpio 是 Unix 早期的 archive 格式，比 tar 古老。比 tar 簡單：每個 entry 一個 header + 檔案內容、結尾 trailer。

```
 ┌─────────────────┐
 │ Header (110B)   │   檔名長度、檔案大小、權限、UID...
 │ Filename (var)  │
 │ Padding (0~3B)  │
 │ File data       │
 │ Padding (0~3B)  │
 ├─────────────────┤
 │ Header          │
 │ Filename        │
 │ ...             │
 ├─────────────────┤
 │ TRAILER!!!      │   結尾標記
 └─────────────────┘
```

Linux kernel 用 **newc format (SVR4)** — header 110 bytes、ASCII 編碼數字、4-byte align。

為什麼 cpio 不用 tar：

- tar 有 100-byte 檔名限制（除非 GNU extension）
- cpio newc 沒限制
- cpio 的 parsing 比 tar 簡單一點
- 歷史選擇，現在改成本太高

## 看 initramfs 內容

```bash
mkdir /tmp/initramfs && cd /tmp/initramfs

# 解開 (initrd 通常是 cpio + gzip)
zcat /boot/initrd.img-$(uname -r) | cpio -idmv

# 看內容
ls
# bin etc init lib lib64 sbin scripts usr var
```

是不是看起來像個小 Linux？因為它**就是**。

幾個重要檔案：

| 檔案 | 作用 |
|---|---|
| `/init` | initramfs 的 PID 1 entry。kernel exec 這個 |
| `/bin/busybox` | 提供 sh / ls / cp / mount 等基本命令 |
| `/lib/modules/*/` | kernel module（NVMe driver 等） |
| `/scripts/` | distro init script（Debian 用） |
| `/etc/udev/` | udev rules |

`/init` 通常是個 shell script 或一支 binary，責任：

1. mount `/proc`、`/sys`、`/dev`
2. 載 kernel module（block device、檔案系統）
3. 找 root device（依 UUID / LABEL）
4. mount root 到 `/mnt/root`（或 `/sysroot`）
5. `switch_root /mnt/root /sbin/init`

Ch 19 會詳細看 switch_root。

## 多層壓縮：concat cpio archive

你有沒有注意過 Debian 的 initramfs：

```bash
file /boot/initrd.img-$(uname -r)
# ASCII cpio archive (SVR4 with no CRC)
```

不是 gzip？確實，Debian 的 initramfs 是 **concatenated cpio**：

```
 [cpio: microcode]
 [cpio + gzip: real initramfs]
```

第一段是 CPU microcode（給 Intel/AMD 的 patch），不壓縮放最前面，kernel 解壓時先 apply 再繼續解。

這樣設計的理由：microcode 必須在 CPU 跑任何 user code 前 apply，放 initramfs 開頭最早被讀到。

```bash
# 看每段
zcat /boot/initrd.img-$(uname -r) | cpio -t 2>/dev/null | head
```

第一個 archive 通常是 `kernel/x86/microcode/AuthenticAMD.bin` 或 GenuineIntel 之類。

## kernel 怎麼接 initramfs

bootloader 把 initramfs 載到 `boot_params.hdr.ramdisk_image`、size 寫 `ramdisk_size`。

kernel 在 `start_kernel` → `init_rootfs()` → `populate_rootfs()`：

```c
static int __init populate_rootfs(void)
{
    /* If we have an initramfs (built into kernel), unpack it */
    if (__initramfs_size > 0) {
        unpack_to_rootfs(__initramfs_start, __initramfs_size);
    }

    /* Then unpack the bootloader-provided initramfs */
    if (initrd_start) {
        unpack_to_rootfs((char *)initrd_start, initrd_end - initrd_start);
    }
    ...
}
```

`unpack_to_rootfs` 把 cpio 解到一個 ramfs（記憶體裡的 fs），mount 為 `/`。

之後 PID 1 (`kernel_init`) 找 `/init`：

```c
if (ramdisk_execute_command) {
    ret = run_init_process(ramdisk_execute_command);
}
```

`ramdisk_execute_command` 預設 `"/init"`，cmdline `rdinit=/foo` 可改。

## kernel 內建 initramfs

`CONFIG_INITRAMFS_SOURCE` 讓你把 initramfs 編進 kernel：

```
CONFIG_INITRAMFS_SOURCE="/path/to/my/initramfs.cpio"
```

這樣 vmlinuz 自帶 initramfs，bootloader 不需要載第二個檔案。Embedded 系統常這樣做。

## 不用 initramfs 的情況

如果 root device 的 driver 是 build-in、root filesystem 也是 build-in（`CONFIG_EXT4_FS=y`），就不需要 initramfs：

```
linux /vmlinuz root=/dev/sda1 ro
# 沒 initrd
```

kernel 直接 mount root。Embedded、單機 server、cloud minimal image 常這樣做。

但桌面 distro 幾乎都用 initramfs，因為要支援各種硬體。

## 一個常見誤解：「initramfs 越小越好」

不全然。initramfs 太小會缺 driver / tool：

- 換硬碟到 NVMe 但 initramfs 沒 NVMe driver → 開不起來
- root 在 LUKS 但 initramfs 沒 cryptsetup → 開不起來
- root 在 LVM 但 initramfs 沒 lvm tool → 開不起來

Debian 的 `mkinitramfs` 有 `-o` 跟 `MODULES=most|dep|netboot|list` 等選項：

- `dep`：只裝這台機器需要的 module（小，但換硬體可能 brick）
- `most`：常見 module 都裝（大，安全）

server 通常 `dep`，桌面 `most`。

## 一個常見誤解：「initramfs 換 root 後就消失」

對。switch_root 的最後一步：

```c
chdir(new_root);
mount(".", "/", NULL, MS_MOVE, NULL);
chroot(".");
```

把 new root 蓋掉舊 root。原來的 ramfs 沒地方掛了，被 GC（kernel 釋放那塊記憶體）。

initramfs 正常開機後**完全消失**。`mount | grep ramfs` 看不到（除非有別的 ramfs，如 `/run`）。

但開機問題的 dmesg 還有 initramfs 跑時的 log。

## 動手練習

**1. 看你機器 initramfs 大小、內容**

```bash
ls -lh /boot/initrd*
file /boot/initrd*

# 解開到 /tmp 看
mkdir /tmp/myinit && cd /tmp/myinit
zcat /boot/initrd.img-$(uname -r) | cpio -idmv 2>&1 | tail
ls
du -sh .
```

**2. 找你機器的 NVMe driver 在 initramfs 裡**

```bash
find /tmp/myinit -name "nvme*" 
```

如果找不到表示是 build-in；如果找到表示是 module。

**3. 看 /init**

```bash
file /tmp/myinit/init
cat /tmp/myinit/init   # 多半是 shell script
```

讀一遍，看它做哪些事。

**4. 拆 multi-stage initramfs**

```bash
# 用 dracut-cpio 之類工具，或手動 split
cd /tmp
cp /boot/initrd.img-$(uname -r) ./initrd.img

# 找第一個 cpio trailer
# (這比較 tricky，看能不能用 binwalk)
binwalk initrd.img
```

binwalk 能看出 microcode segment + main segment 的邊界。

## 自我檢核

- [ ] 講得出為什麼需要 initramfs（雞生蛋問題）
- [ ] 知道 initrd 跟 initramfs 差在哪（cpio vs block image、switch_root vs pivot_root）
- [ ] 知道 cpio newc format 是什麼
- [ ] 解開 initramfs、找到 `/init`
- [ ] 知道 multi-stage cpio（microcode + main）

下一章自己組一個最小 initramfs，busybox + 自寫 /init。

→ [Ch 18 動手：自製最小 initramfs](./18-build-minimal-initramfs.md)
