# Ch 1 — 一切皆檔案：VFS 與 inode

> 目標：理解 Linux「一切皆檔案」的設計哲學，掌握 inode 結構，用 `stat` 讀出 inode 資訊，知道 hard link 和 symlink 的底層差異。

## 一切皆檔案

Linux 把幾乎所有東西都抽象成檔案介面：

```
普通檔案    /home/alice/notes.txt
目錄        /home/alice/
設備        /dev/sda     （硬碟）
             /dev/null    （黑洞）
             /dev/urandom （亂數源）
管線        /proc/PID/fd/3
Socket      /run/systemd/private/...
虛擬 FS     /proc/cpuinfo （CPU 資訊）
             /proc/meminfo （記憶體資訊）
             /sys/class/net/eth0/  （網路介面）
```

好處：所有東西都可以用同一套 `open()`/`read()`/`write()`/`close()` 系統呼叫操作。

## VFS：虛擬檔案系統

```
應用程式
    │  open() / read() / write()
    ▼
  VFS（Virtual Filesystem）  ← 統一介面層
    │
    ├── ext4 driver    → 實體磁碟
    ├── tmpfs driver   → 記憶體
    ├── procfs driver  → /proc（核心資料結構）
    └── sysfs driver   → /sys（裝置樹）
```

VFS 是核心裡的一層抽象，讓應用程式不需要知道底層是哪種檔案系統。

## inode：檔案的真實身份

**inode**（index node）存放一個檔案的所有元資料，除了檔名之外的一切：

```
inode #12345
├── 檔案類型（普通檔案、目錄、symlink...）
├── 權限（rwxrwxrwx）
├── 擁有者（UID、GID）
├── 大小（bytes）
├── 時間戳（atime, mtime, ctime）
├── 硬連結計數（link count）
└── 資料區塊指標（指向真正存資料的磁碟區塊）
```

**目錄** 是一張表，存的是「檔名 → inode 號碼」的對映：

```
目錄 /home/alice/
├── "notes.txt" → inode #12345
├── "photos"    → inode #98765
└── "."         → inode #11111  （自己）
└── ".."        → inode #10000  （上一層目錄）
```

所以「檔名」只是目錄裡的一個條目，不屬於 inode。

## stat：讀 inode 資訊

```bash
stat notes.txt
```

輸出：

```
  File: notes.txt
  Size: 42              Blocks: 8          IO Block: 4096   regular file
Device: 8,1             Inode: 12345       Links: 1
Access: (0644/-rw-r--r--)  Uid: ( 1000/  alice)   Gid: ( 1000/  alice)
Access: 2024-01-15 09:00:00.000000000 +0800
Modify: 2024-01-14 23:30:00.000000000 +0800
Change: 2024-01-14 23:30:00.000000000 +0800
 Birth: 2024-01-14 20:00:00.000000000 +0800
```

三個時間戳意義不同：

| 時間戳 | 縮寫 | 更新時機 |
|--------|------|---------|
| `Access` | atime | 讀取檔案內容時 |
| `Modify` | mtime | 修改檔案**內容**時 |
| `Change` | ctime | 修改 inode **元資料**時（包含 mtime 改變、chmod、chown）|

一個常見誤解：ctime 不是「建立時間」（creation time），是「change time」。建立時間是 `Birth`（部分 FS 不支援）。

## Hard Link vs Symlink

**Hard link**：目錄裡新增一個「另一個名字 → 同一個 inode」的條目：

```bash
ln notes.txt notes_backup.txt
stat notes.txt      # Links: 2
stat notes_backup.txt  # 同一個 inode 號碼！
```

```
目錄：
"notes.txt"        → inode #12345
"notes_backup.txt" → inode #12345  ← 同一個

inode #12345（link count = 2）
└── 資料區塊
```

刪掉 `notes.txt` 只是把目錄條目移除，inode 的 link count 從 2 變 1，資料還在。link count 歸 0 才真正釋放磁碟空間。

**Symlink（soft link）**：一個特殊檔案，內容是另一個路徑的字串：

```bash
ln -s notes.txt notes_link.txt
stat notes_link.txt   # 這是一個不同的 inode，類型是 symlink
ls -la notes_link.txt
# lrwxrwxrwx  notes_link.txt -> notes.txt
```

```
"notes_link.txt" → inode #99999（type: symlink）
                   內容 = "notes.txt"

inode #12345（原始檔案）
```

刪掉原始檔案，symlink 就「懸空」（dangling symlink），指向不存在的目標。

| | Hard Link | Symlink |
|--|-----------|---------|
| 跨檔案系統 | 不行 | 可以 |
| 指向目錄 | 通常不行 | 可以 |
| 原始刪除後 | 資料還在 | 懸空 |
| `ls -la` 開頭字元 | `-` | `l` |

## /proc：核心資料的檔案介面

```bash
cat /proc/cpuinfo       # CPU 規格
cat /proc/meminfo       # 記憶體用量
cat /proc/version       # kernel 版本
ls /proc/$$             # $$ 是目前 shell 的 PID
cat /proc/$$/status     # 目前 shell 行程的狀態
cat /proc/$$/cmdline    # 啟動指令
```

`/proc` 裡的檔案不佔磁碟空間，每次讀取時核心動態產生內容。

## 動手練習

```bash
# 1. 建立一個 hard link，確認它們有相同的 inode 號碼
echo "hello" > test.txt
ln test.txt test_hard.txt
stat test.txt | grep Inode
stat test_hard.txt | grep Inode    # 應該相同

# 2. 刪掉原始檔，hard link 還在嗎？
rm test.txt
cat test_hard.txt    # 還能讀！

# 3. 建立 symlink，刪掉原始檔，看 dangling symlink
echo "world" > original.txt
ln -s original.txt sym_link.txt
rm original.txt
cat sym_link.txt     # 應該報錯
ls -la sym_link.txt  # 連結目標會顯示為紅色（懸空）

# 4. 看 /proc 裡的自己
echo "目前 PID = $$"
ls /proc/$$
cat /proc/$$/status | head -10
```

## 自我檢核

- [ ] 理解 inode 存的是什麼，不存的是什麼（檔名）
- [ ] 能用 `stat` 讀出 inode 號碼、link count、三個時間戳
- [ ] 理解 hard link 和 symlink 的底層差異
- [ ] 知道 ctime 是「change time」不是「creation time」

→ [Ch 2 目錄樹與路徑](./02-directory-tree-and-paths.md)
