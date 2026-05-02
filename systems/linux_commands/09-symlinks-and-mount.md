# Ch 9 — 符號連結與掛載概念

> 目標：深入理解 symlink 的陷阱、掌握 `df`/`du` 查磁碟用量，建立掛載（mount）的直觀概念。

## Symlink 的常見陷阱

Ch 1 講了 symlink 的原理，這章補幾個實際場景的陷阱。

**相對 symlink vs 絕對 symlink**：

```bash
# 絕對 symlink（路徑寫死，不管 link 放哪都能用）
ln -s /etc/nginx/nginx.conf /home/alice/nginx.conf

# 相對 symlink（路徑相對於 link 本身的位置）
cd /home/alice
ln -s ../../etc/nginx/nginx.conf nginx.conf
# 把這個 link 移到別的地方就壞了
```

一般優先用**絕對路徑** symlink，除非你確定 link 和目標會一起移動。

**symlink 指向目錄時的 trailing slash**：

```bash
ln -s /var/log logs

ls logs       # 等同 ls /var/log（列出目錄內容）
ls logs/      # 也是等同 ls /var/log

rm logs       # 刪 symlink 本身
rm -r logs/   # 刪 /var/log 的內容！（trailing slash 讓 rm 追進去）
```

**tail slash 很危險**，`rm -r` 一個指向目錄的 symlink 加上 `/` 會刪掉目標內容。

**readlink：查 symlink 指向哪裡**

```bash
readlink /etc/alternatives/python3
# /usr/bin/python3.10

readlink -f /etc/alternatives/python3  # -f = 完整解析所有層的 symlink
# /usr/bin/python3.10

ls -la /etc/alternatives/python3       # 也可以用 ls -la 看
```

## df：磁碟空間用量

```bash
df                          # 所有掛載的 filesystem
df -h                       # -h = human readable（K/M/G）
df -H                       # -H = SI 單位（1k=1000，不是1024）
df /home                    # 只看 /home 所在的 filesystem
df -T                       # -T = 顯示 filesystem 類型
df -i                       # -i = 顯示 inode 用量（而非 block 用量）
```

輸出說明：

```
Filesystem     1K-blocks  Used Available Use% Mounted on
/dev/sda1      20480000   8192000  11264000  43% /
tmpfs            1024000         0   1024000   0% /dev/shm
```

- `1K-blocks` = 總 block 數（磁碟大小）
- `Use%` = 使用百分比
- `Mounted on` = 這個 filesystem 掛載在哪個路徑

`df -i` 看 inode 用量——有時候磁碟 block 還有空間，但 inode 耗盡了（大量小檔案），一樣不能建新檔案。

## du：目錄用量

```bash
du -sh /var/log             # -s = summarize（只顯示總計），-h = human readable
du -sh /var/log/*           # /var/log 每個子項目的大小
du -sh ~                    # 家目錄的總大小
du -h --max-depth=1 /var    # 只顯示 1 層深
du -h --max-depth=2 /var/log | sort -rh | head -10  # 找最大的目錄
```

`du` 統計的是**實際佔用的 block 數**，`-s` 是最常用的選項。

找出磁碟吃完的元兇：

```bash
# 一層一層往下找最大的子目錄
du -h --max-depth=1 / 2>/dev/null | sort -rh | head -10
du -h --max-depth=1 /var 2>/dev/null | sort -rh | head -10
```

## 掛載（Mount）概念

Linux 只有一棵目錄樹（從 `/` 開始），所有 filesystem 都**掛載**到這棵樹的某個節點上：

```
/                   ← 根 filesystem（通常在 /dev/sda1）
├── home/           ← 可能是另一個磁區（/dev/sda2）掛載在這裡
├── boot/           ← /dev/sda2 或 /dev/sda3
├── proc/           ← 虛擬 FS（procfs），不在磁碟上
├── sys/            ← 虛擬 FS（sysfs）
└── mnt/
    └── usb/        ← USB 隨身碟掛載在這裡（/dev/sdb1）
```

```bash
# 查看目前所有掛載
mount                       # 所有掛載（輸出很多）
mount | grep "^/dev"        # 只看實體磁碟
lsblk                       # 更漂亮的樹狀顯示
lsblk -f                    # 也顯示 filesystem 類型和 UUID
cat /proc/mounts            # 核心的掛載表
```

```bash
# 手動掛載（需要 root）
sudo mount /dev/sdb1 /mnt/usb
sudo mount -t ext4 /dev/sdb1 /mnt/usb   # 指定 FS 類型
sudo mount -o ro /dev/sdb1 /mnt/usb     # -o ro = 唯讀掛載

# 卸載
sudo umount /mnt/usb
sudo umount /dev/sdb1       # 也可以用裝置路徑
```

## /etc/fstab：開機自動掛載

```bash
cat /etc/fstab
```

格式（六欄）：

```
UUID=xxx   /boot   ext4   defaults   0  2
UUID=yyy   /       ext4   errors=remount-ro  0  1
UUID=zzz   /home   ext4   defaults   0  2
tmpfs      /tmp    tmpfs  defaults   0  0
```

- 第 1 欄：裝置（用 UUID 比用 `/dev/sda1` 更穩定，裝置名可能改變）
- 第 2 欄：掛載點
- 第 3 欄：filesystem 類型
- 第 4 欄：掛載選項（`defaults`、`ro`、`noexec`...）
- 第 5 欄：dump（備份，通常 0）
- 第 6 欄：fsck 順序（根目錄 1，其他 2，不檢查 0）

## 動手練習

```bash
# 1. 查看磁碟空間
df -h
df -i   # inode 用量

# 2. 找最佔空間的目錄
du -h --max-depth=1 /var 2>/dev/null | sort -rh | head -5

# 3. 查看掛載情況
lsblk
mount | grep "^/"

# 4. symlink 陷阱測試
mkdir /tmp/mydir
ln -s /tmp/mydir /tmp/mylink

# 用 readlink 確認
readlink /tmp/mylink

# 測試 trailing slash 差異（小心，只在測試目錄做）
ls /tmp/mylink     # 列目錄內容
# 不要用 rm -r /tmp/mylink/（會刪 /tmp/mydir 內容）
rm /tmp/mylink     # 正確：只刪 symlink 本身
```

## 自我檢核

- [ ] 知道相對 symlink 移動後可能失效，優先用絕對路徑
- [ ] 理解 `rm -r symlink/`（有 trailing slash）會刪目標目錄的內容
- [ ] 能用 `du -h --max-depth=1 | sort -rh` 快速找磁碟殺手
- [ ] 理解 Linux 的掛載概念：所有 FS 都掛到同一棵目錄樹

→ [練習 A：檔案系統偵探](./practice-a-filesystem-detective.md)
