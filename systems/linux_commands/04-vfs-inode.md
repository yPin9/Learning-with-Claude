# Ch 4 — VFS 與 inode

> **目標**：深入理解檔案的底層表示——inode 是什麼、它和檔名的分離、VFS（虛擬檔案系統）如何用統一介面抽象不同檔案系統、以及 `stat` 看到的每個欄位對應的底層意義。這是整個檔案系統理解的基石。

> **環境**：Linux，ext4 為主（行為對多數檔案系統通用），stat/statx。原理深挖章。

## 為什麼「檔名不是檔案」這個觀念這麼重要？

新手以為「檔案 = 檔名 + 內容」。但 Linux 的真相更深刻：**檔名和檔案是分離的**。檔案的本體是 **inode**（一個編號的資料結構，存著 metadata 和指向資料的指標）；檔名只是「指向 inode 的一個標籤」。一個 inode 可以有多個檔名（hard link，Ch 6），刪除檔名不一定刪除檔案。

這個分離解釋了無數「謎之行為」：為什麼 `mv` 同磁碟超快、為什麼刪了檔案磁碟空間沒釋放、為什麼 hard link 能存在。理解 inode，你對檔案系統的理解會從表面進到本質。

## 先建立直覺：檔名是標籤，inode 是本體

```
新手的模型（錯）：
  file.txt = 一個叫 file.txt 的東西，裡面有內容

真實的模型：
  目錄裡有一條 entry："file.txt" → inode 12345
                       （標籤）    （指向哪個 inode）
        │
  inode 12345（檔案的「本體」）：
    - metadata：大小、權限、擁有者、時間戳...
    - 指向實際資料 block 的指標
    - link count：有幾個檔名指向我
        │
  → 檔名只是「指向 inode 的標籤」
    inode 才是檔案的本體（內容 + metadata）
```

關鍵心智轉變：`file.txt` 不是檔案，它是「指向某個 inode 的標籤」。inode 才是檔案。一個 inode 可以有多個標籤（多個檔名指向它）；刪標籤不等於刪檔案（除非那是最後一個標籤）。

## inode：檔案的本體

inode（index node）是檔案的核心資料結構。它存什麼？

```
inode 存的東西（注意：不含檔名！）：
  - 檔案類型（一般檔案/目錄/symlink/device...）
  - 權限（rwx，Ch 7）
  - 擁有者 UID / 群組 GID
  - 大小（bytes）
  - 時間戳：
    - atime（access，最後讀取）
    - mtime（modify，最後修改內容）
    - ctime（change，最後改 metadata）
  - link count（有幾個 hard link 指向我）
  - 指向實際資料 block 的指標
        │
  inode「不」存：
  - 檔名（檔名在目錄裡，Ch 5）
  - 路徑（檔案可以有多個路徑/檔名）
```

用 `stat` 看 inode 的內容：

```bash
cd ~/cmdlab
echo "hello" > file.txt
stat file.txt
```

```
  File: file.txt
  Size: 6           Blocks: 8          IO Block: 4096   regular file
Device: 801h/2049d  Inode: 1234567     Links: 1
                            ↑ inode 號   ↑ link count（1 個檔名指向）
Access: (0644/-rw-r--r--)  Uid: ( 1000/  you)   Gid: ( 1000/  you)
                  ↑ 權限              ↑ 擁有者
Access: 2025-05-30 10:00:00       ← atime（最後讀取）
Modify: 2025-05-30 10:00:00       ← mtime（最後改內容）
Change: 2025-05-30 10:00:00       ← ctime（最後改 metadata）
```

`stat` 底層就是呼叫 `stat`/`statx` syscall 讀 inode：

```bash
strace -e statx,stat,newfstatat stat file.txt 2>&1 | grep file.txt
# statx(AT_FDCWD, "file.txt", ...) = 0
#   ↑ stat 命令底層呼叫 statx syscall 讀 inode 的 metadata
```

> 注意 inode **不存檔名**。這是最反直覺也最關鍵的點。檔名存在「目錄」裡（目錄是一張「檔名 → inode 號」的表，Ch 5）。inode 只有編號（Inode: 1234567），沒有名字。一個 inode 可以被多個目錄 entry（多個檔名）指向。這就是 hard link（Ch 6）的基礎。

## 三個時間戳：atime/mtime/ctime

inode 有三個時間戳，常被搞混：

```
atime（access time）：最後「讀取」的時間
  cat file → 更新 atime（你讀了它）
  注意：現代系統常用 relatime/noatime 減少 atime 更新（效能）

mtime（modify time）：最後「修改內容」的時間
  echo x >> file → 更新 mtime（內容變了）
  ls -l 顯示的就是 mtime

ctime（change time）：最後「改 metadata」的時間
  chmod/chown file → 更新 ctime（metadata 變了）
  改內容也會更新 ctime（內容變 = metadata 的 mtime 變）
  注意：ctime 不是 "create time"！是 "change time"
```

```bash
# 觀察三個時間戳怎麼變
stat file.txt | grep -E "Access|Modify|Change"
cat file.txt > /dev/null     # 讀取 → 可能更新 atime（看 mount 選項）
echo "more" >> file.txt      # 改內容 → mtime + ctime 更新
chmod 644 file.txt           # 改權限 → 只有 ctime 更新
stat file.txt | grep -E "Access|Modify|Change"
```

> **ctime 不是 create time**——這是最常見的誤解。ctime 是「change time」（metadata 最後改變）。Linux 傳統上**沒有** create time（建立時間）！（新的 statx + ext4 才有 btime/crtime，但工具支援不一）。所以「檔案什麼時候建立的」在 Linux 經常查不到——只有 atime（讀）、mtime（改內容）、ctime（改 metadata）。記住這個避免誤判。

## VFS：統一不同檔案系統的抽象層

Linux 支援幾十種檔案系統（ext4、btrfs、XFS、FAT、NFS...）。但你用 `cat`、`ls` 不用管底層是哪種檔案系統——因為 **VFS**（Virtual File System）提供統一介面：

```
VFS（虛擬檔案系統）的抽象：

  你的命令（cat, ls, cp...）
        │ 用統一的 syscall（open, read, stat...）
        ▼
  ┌─────────────────────────────────────┐
  │  VFS（虛擬檔案系統層）                 │
  │  定義統一介面：inode、dentry、file... │
  └────┬────────────────────────────────┘
       │ VFS 轉發給具體檔案系統
   ┌───┴────┬────────┬────────┬─────────┐
   ▼        ▼        ▼        ▼         ▼
  ext4    btrfs    XFS     FAT       NFS
  （各自實作 inode/讀寫的細節）
        │
  → 一套 syscall、一套工具，操作所有檔案系統
```

VFS 的價值：

```bash
# 同一個 cat 讀不同檔案系統的檔案，你不用管底層
cat /file_on_ext4        # ext4
cat /mnt/usb/file        # 可能是 FAT
cat /mnt/nfs/file        # 網路檔案系統 NFS
# cat 不知道也不在乎底層是什麼——VFS 統一了介面
```

> VFS 是「一切皆檔案」（Ch 1）哲學的實作基礎。它定義了抽象的 inode、dentry（Ch 5）、file 等概念，每個具體檔案系統（ext4...）實作這些抽象。這讓 `cat`/`ls`/`cp` 等工具能操作任何檔案系統，不用為每種檔案系統寫一套。inode 是 VFS 的核心抽象——不管底層是 ext4 還是 btrfs，VFS 都用「inode」表示一個檔案。本章講的 inode 概念是 VFS 層的（通用），具體檔案系統的 inode 實作細節各異。

## inode 也會用完

inode 是有限的資源（格式化時決定數量）。檔案系統可能「空間還有但 inode 用完」：

```bash
# 看 inode 使用情況
df -i            # -i 顯示 inode（不是空間）
# Filesystem  Inodes  IUsed  IFree IUse% Mounted on
# /dev/sda1   6553600 234567 ...   4%    /
#             ↑ 總 inode  ↑ 已用

# 如果 IUse% 到 100%：
#   即使 df（空間）還有，也無法建立新檔案！
#   "No space left on device"（但其實是 inode 用完）
```

> 「空間還有但建不了檔案」是個經典陷阱。每個檔案（不管多小）都要一個 inode。如果你有海量小檔案（如 mail server 的郵件、cache），可能 inode 先用完。`df` 看空間沒滿，但 `df -i` 看 inode 滿了，建新檔案就 "No space left on device"。這時要刪檔案（釋放 inode）或重新格式化調整 inode 比例。記住：建不了檔案時，`df` 和 `df -i` 都要看。

## 故意弄壞：刪了檔案空間沒釋放

```bash
cd ~/cmdlab
# 建一個大檔案
dd if=/dev/zero of=big.dat bs=1M count=100  # 100MB
df -h . | tail -1            # 看空間

# 在一個 process 開著這個檔案的情況下刪除它
# （模擬：用 tail -f 開著它）
tail -f big.dat &
TAILPID=$!
rm big.dat                   # 刪除檔名
ls big.dat                   # No such file（檔名沒了）
df -h . | tail -1            # 但空間「沒」釋放！
#   因為 tail 還開著這個 inode（link count 0 但有 process 用它）

kill $TAILPID                # 關閉 tail（釋放對 inode 的引用）
df -h . | tail -1            # 現在空間釋放了
```

這展示 inode 的生命週期：`rm` 刪的是**檔名**（目錄 entry），不是 inode。inode 在「link count = 0 **且** 沒有 process 開著它」時才真正釋放。tail 開著它時，link count = 0 但還有引用，inode 不釋放，空間不還。這是 Ch 6（link count）和 Ch 19（fd 對 inode 的引用）的伏筆，也是「刪了檔案空間沒少」的根本原因。

## 踩雷集錦

1. **以為檔名就是檔案**：檔名是「指向 inode 的標籤」，inode 才是檔案本體。一個 inode 可有多個檔名（hard link）。理解這個是檔案系統的關鍵

2. **以為 ctime 是 create time**：ctime 是 change time（metadata 最後改變）。Linux 傳統沒有 create time。要建立時間用 statx 的 btime（且工具支援有限）

3. **以為 inode 存檔名**：inode 不存檔名（檔名在目錄裡，Ch 5）。inode 只有編號。這是 hard link 能存在的原因

4. **空間沒滿卻建不了檔案**：可能 inode 用完。`df -i` 看 inode。海量小檔案會先耗盡 inode

5. **rm 後空間沒釋放**：rm 刪檔名，inode 在「link count 0 且無 process 開著」才釋放。有 process 開著被刪的檔案，空間不還（Ch 19）

## 進階：inode 的資料 block 指標結構

inode 怎麼指向實際資料？對大檔案，這涉及多層間接指標：

```
inode 的資料指標（傳統 ext 風格，簡化）：
  inode 有有限的指標欄位（如 15 個）：
    - 前 12 個：直接指向資料 block（小檔案夠用）
    - 第 13 個：single indirect（指向一個「指標 block」）
    - 第 14 個：double indirect（指標的指標）
    - 第 15 個：triple indirect（三層）
        │
  小檔案：直接指標就夠（快）
  大檔案：用間接指標（多一層查找）
        │
  現代 ext4 改用 extent（連續 block 的範圍），更有效率：
    不存每個 block 的指標，存「從 block X 開始連續 N 個」
```

> inode 的資料指標結構是檔案系統設計的經典問題：小檔案要快（直接指標），大檔案要能定址（間接指標）。傳統 ext2/3 用「直接 + 多層間接」（12 直接 + 1/2/3 層間接）。現代 ext4 用 extent（連續範圍），對大檔案更有效率（不用為每個 block 存指標）。這是 OS 課程（OSTEP）的檔案系統章節主題。理解它能解釋為什麼「很多小檔案」和「少數大檔案」的效能特性不同。

## 動手練習

1. 用 stat 讀 inode：`stat file.txt`，認出 inode 號、link count、權限、三個時間戳。用 `strace -e statx stat file.txt` 確認 stat 底層呼叫 statx

2. 觀察時間戳：建一個檔案，分別 `cat`（看 atime）、`echo >>`（看 mtime+ctime）、`chmod`（看只有 ctime 變）。理解三者的觸發條件

3. 看 inode 使用：`df -i`，看你的檔案系統用了多少 inode。思考什麼情況會耗盡 inode（海量小檔案）

4. 跑「故意弄壞」：用 tail -f 開著一個檔案再 rm 它，看 `df` 空間沒釋放（inode 還被引用）。kill tail 後空間才還。理解 inode 的真正釋放條件

## 本章重點整理

- 檔名和檔案分離：檔名是「指向 inode 的標籤」，inode 是檔案本體（metadata + 資料指標），inode 不存檔名
- inode 存：類型、權限、擁有者、大小、三個時間戳（atime讀/mtime改內容/ctime改metadata）、link count、資料指標
- ctime 是 change time 不是 create time；Linux 傳統沒有 create time
- VFS 是統一不同檔案系統（ext4/btrfs/FAT/NFS）的抽象層，inode 是它的核心抽象，讓一套工具操作所有檔案系統
- inode 是有限資源（df -i）；rm 刪檔名，inode 在「link count 0 且無 process 開著」才真正釋放

## 自我檢核

- [ ] 能用自己的話解釋「檔名不是檔案，inode 才是」
- [ ] 知道 inode 存什麼、不存什麼（不存檔名）
- [ ] 能區分 atime/mtime/ctime 的觸發條件，知道 ctime 不是 create time
- [ ] 能解釋 VFS 是什麼、為什麼一套工具能操作所有檔案系統
- [ ] 能解釋「rm 後空間沒釋放」的原因（inode 還被 process 引用）

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 14 (File Systems), Ch 15 (File Attributes)** — Michael Kerrisk
  - **這本書的定位**：本課的底層聖經
  - **讀哪幾章**：Ch 14（檔案系統、inode）、Ch 15（stat、時間戳、權限）；本章的權威來源
  - **前提**：本章建立的概念

- **《Operating Systems: Three Easy Pieces》— File System Implementation** — Arpaci-Dusseau（免費）
  - **讀哪幾章**：File System Implementation 那幾章（inode、資料指標、間接 block）
  - **這本書的定位**：從 OS 原理講 inode 怎麼實作，本章「進階」段落的延伸
  - **前提**：本章

### 官方文件

- **[stat(2) man page](https://man7.org/linux/man-pages/man2/stat.2.html)** 和 **[statx(2)](https://man7.org/linux/man-pages/man2/statx.2.html)**
  - **讀哪裡**：struct stat 的欄位、statx 的 btime
  - **學什麼**：stat 回傳的每個欄位的精確意義
  - **前提**：本章

→ [Ch 5 目錄與 dentry](./05-directory-dentry.md)
