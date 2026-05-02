# Ch 27 — 磁碟與儲存

> 目標：能用 `df`/`du`/`lsblk`/`mount` 查看磁碟使用情況，理解掛載機制和分區的基本概念。

## 儲存的抽象層

```
硬體：物理磁碟（/dev/sda、/dev/nvme0n1）
  ↓
分區：/dev/sda1、/dev/sda2（fdisk/parted 管理）
  ↓
檔案系統：ext4、xfs、btrfs（格式化後才能用）
  ↓
掛載點：/、/home、/var（mount 到目錄樹）
```

Linux 把所有儲存都整合進一棵目錄樹，不像 Windows 用磁碟代號（C:、D:）。

## lsblk：看磁碟結構

```bash
lsblk              # 列出所有區塊裝置
lsblk -f           # 也顯示檔案系統類型和 UUID
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT  # 自訂欄位
```

輸出範例：

```
NAME        MAJ:MIN  RM   SIZE RO TYPE MOUNTPOINT
sda           8:0     0   100G  0 disk
├─sda1        8:1     0     1G  0 part /boot
├─sda2        8:2     0    20G  0 part /
└─sda3        8:3     0    79G  0 part /home
nvme0n1     259:0     0   500G  0 disk
└─nvme0n1p1 259:1     0   500G  0 part /data
```

## df：磁碟使用率

```bash
df -h              # human-readable（KB/MB/GB）
df -h /home        # 只看 /home 所在的分區
df -i              # 看 inode 使用率（別忘了這個！）
df -hT             # 也顯示 filesystem type
```

輸出範例：

```
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda2        20G   12G  7.1G  63% /
/dev/sda3        79G   45G   30G  61% /home
```

**inode 滿了也會導致「No space left on device」，即使磁碟空間還有**：

```bash
df -i    # 如果 IUse% 很高，是 inode 問題
```

## du：目錄大小

```bash
du -sh /var/log          # -s = summarize（只顯示總計），-h = human-readable
du -sh /var/log/*        # 每個子目錄各自顯示
du -sh /* 2>/dev/null    # 根目錄每個子目錄
du -h --max-depth=1 /    # 限制遞迴深度

# 找最大的目錄
du -h /var 2>/dev/null | sort -rh | head -10

# 找最大的檔案
find /var -type f -exec du -sh {} \; 2>/dev/null | sort -rh | head -10
# 更快的方式：
find /var -type f -size +100M 2>/dev/null -exec ls -lh {} \;
```

## mount / umount

### 查看當前掛載

```bash
mount              # 列出所有掛載點
mount | grep "^/"  # 只看實際磁碟
findmnt            # 更好看的樹狀輸出
findmnt -t ext4    # 只看 ext4
cat /proc/mounts   # 核心看到的掛載表
```

### 手動掛載

```bash
# 掛載 USB 隨身碟
sudo mount /dev/sdb1 /mnt/usb
sudo mount -t vfat /dev/sdb1 /mnt/usb   # 指定 filesystem type
sudo mount -o ro /dev/sdb1 /mnt/usb     # 只讀掛載

# 掛載 ISO 檔
sudo mount -o loop ubuntu.iso /mnt/iso

# 卸載
sudo umount /mnt/usb
sudo umount /dev/sdb1   # 同上（用裝置名也行）
```

### /etc/fstab：開機自動掛載

```bash
cat /etc/fstab
```

格式：
```
UUID=xxx  /home  ext4  defaults  0  2
裝置      掛載點  類型  選項    dump  pass
```

用 UUID 而不是 `/dev/sdX`，因為裝置名可能在重開機後改變。

## 常見空間問題排查

```bash
# 磁碟滿了，找大檔案
du -h / --max-depth=2 2>/dev/null | sort -rh | head -20

# 找大的 log 檔案
find /var/log -name "*.log" -size +50M 2>/dev/null | xargs ls -lh

# 清理常見垃圾
sudo journalctl --vacuum-size=500M    # systemd journal 限制 500MB
sudo apt clean                        # 清 apt cache（Debian/Ubuntu）
sudo dnf clean all                    # 清 dnf cache（RHEL/Fedora）

# 找被刪除但還被行程持有的檔案（佔用空間但 ls 看不到）
lsof | grep "(deleted)"
```

## 動手練習

```bash
# 1. 查看系統磁碟狀況
df -h
df -i
lsblk -f

# 2. 找 /var 下最大的東西
du -h --max-depth=2 /var 2>/dev/null | sort -rh | head -10

# 3. 找大檔案
find /usr -type f -size +10M 2>/dev/null | xargs ls -lh | sort -k5 -rh | head -10

# 4. 建立一個 tmpfs（RAM-based filesystem）
sudo mkdir -p /mnt/ramdisk
sudo mount -t tmpfs -o size=64m tmpfs /mnt/ramdisk
df -h /mnt/ramdisk
# 用完後卸載
sudo umount /mnt/ramdisk

# 5. 用 dd 測試磁碟寫入速度
dd if=/dev/zero of=/tmp/testfile bs=1M count=100 oflag=direct 2>&1
rm /tmp/testfile
```

## 自我檢核

- [ ] 知道 `df` 看分區使用率，`du` 看目錄大小
- [ ] 記得磁碟滿了可能是 inode 問題（用 `df -i` 確認）
- [ ] 能用 `lsblk` 看整個磁碟/分區結構
- [ ] 知道 `findmnt` 比 `mount` 輸出更清楚

→ [Ch 28 網路命令](./28-network-commands.md)
