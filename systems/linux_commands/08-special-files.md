# Ch 8 — 特殊檔案：device/pipe/socket

> **目標**：理解「一切皆檔案」哲學的具體展現——字元/區塊裝置（/dev/null、/dev/sda）、named pipe（FIFO）、socket、以及 major/minor number，看清這些「不是真檔案的檔案」如何用統一的檔案介面被操作。

> **環境**：Linux，/dev。承接 Ch 1（一切皆檔案）、Ch 4-7（inode/權限）。

## 為什麼「假檔案」這麼重要？

Ch 1 講了「一切皆檔案」哲學。這章看它的具體展現：`/dev/null`（黑洞）、`/dev/sda`（磁碟）、`/dev/random`（隨機數）、named pipe、socket——這些**不是磁碟上的資料**，但都用「檔案」介面（open/read/write）操作。

理解這些特殊檔案，你會徹底懂「一切皆檔案」的威力：同一個 `cat`、同一個 `>` 重導向，能操作黑洞、磁碟、隨機數產生器、行程間通訊管道。這是 Unix 設計最優雅的地方，也是很多命令列技巧的基礎。

## 先建立直覺：檔案類型遠不只「一般檔案」

```
ls -l 第一個字元（檔案類型）：
  -  一般檔案（regular file）   ← Ch 4 講的
  d  目錄（directory）          ← Ch 5 講的
  l  symbolic link             ← Ch 6 講的
  c  字元裝置（character device）← 本章：/dev/null, /dev/tty
  b  區塊裝置（block device）   ← 本章：/dev/sda（磁碟）
  p  named pipe / FIFO          ← 本章：行程間通訊
  s  socket                    ← 本章：行程間/網路通訊
        │
  後四種是「特殊檔案」：
  它們不是磁碟上的資料，是 kernel 提供的「介面」
  但都能用 open/read/write 操作（一切皆檔案）
```

關鍵心智：這些特殊檔案是「kernel 暴露的介面」，不是儲存的資料。`/dev/null` 不存任何東西（寫進去就消失）；`/dev/sda` 是磁碟的存取介面（讀寫它 = 讀寫磁碟原始資料）。它們「長得像檔案」（有路徑、有權限、能 open/read/write），但行為由 kernel 的 driver 決定。

## 字元裝置：/dev/null、/dev/zero、/dev/random

```bash
ls -l /dev/null /dev/zero /dev/random /dev/urandom
# crw-rw-rw- 1 root root 1, 3 ... /dev/null
# │              ↑major,minor
# c = 字元裝置
```

常用的字元裝置：

```
/dev/null：「黑洞」
  寫進去 → 消失（丟棄）
  讀它 → 立刻 EOF（空）
  用途：丟棄不要的輸出（command > /dev/null）

/dev/zero：「無限的零」
  讀它 → 源源不絕的 \0（零位元組）
  用途：建立指定大小的空檔案、清空磁碟

/dev/random、/dev/urandom：「隨機數產生器」
  讀它 → 隨機位元組
  用途：產生密鑰、隨機資料
```

```bash
# /dev/null：黑洞
echo "this disappears" > /dev/null    # 寫進去就消失
cat /dev/null                         # 什麼都沒有（立刻 EOF）

# /dev/zero：產生指定大小的零檔案
dd if=/dev/zero of=~/cmdlab/zeros.bin bs=1M count=10  # 10MB 的零

# /dev/urandom：隨機資料
head -c 16 /dev/urandom | xxd          # 16 bytes 隨機，hex 顯示
# 00000000: a3f5 9c2e ...  ← 每次不同

# 用 strace 看 /dev/null 真的被 open/write
strace -e openat,write echo "x" 2>&1 > /dev/null | grep null
# （重導向到 /dev/null 時，write 寫到那個 fd，資料被 kernel 丟棄）
```

> `/dev/null` 是命令列最常用的特殊檔案——「黑洞」，丟棄不要的輸出。`command > /dev/null 2>&1` 是「丟棄所有輸出」的慣用法（Ch 19 詳述 2>&1）。`/dev/zero` 產生零（建空檔案、初始化）。`/dev/urandom` 是密碼學隨機數來源。這些都用「檔案」介面——`>` 寫、`cat`/`head` 讀——但行為由 kernel driver 決定（null 丟棄、zero 給零、urandom 給隨機）。這就是「一切皆檔案」：一套介面，driver 決定行為。

## 區塊裝置：/dev/sda（磁碟）

```bash
ls -l /dev/sda* 2>/dev/null || ls -l /dev/vda* 2>/dev/null
# brw-rw---- 1 root disk 8, 0 ... /dev/sda      ← 整顆磁碟
# brw-rw---- 1 root disk 8, 1 ... /dev/sda1     ← 第一個分區
# b = 區塊裝置
```

```
區塊裝置 vs 字元裝置：
  字元裝置（c）：以「位元組流」存取（一個一個 byte）
    /dev/null, /dev/tty, /dev/random
        │
  區塊裝置（b）：以「區塊」存取，有 buffer cache
    /dev/sda（磁碟）, /dev/sda1（分區）
    讀寫以 block（如 512 bytes / 4KB）為單位
    kernel 有 buffer 快取（多次讀同一 block 不用每次碰硬碟）
        │
  → 磁碟是區塊裝置（讀寫 block + 快取）
    終端機、隨機數是字元裝置（位元組流，無快取）
```

> 區塊裝置（`/dev/sda`）是磁碟的「原始存取介面」——讀寫它 = 直接讀寫磁碟的 raw bytes（繞過檔案系統）。這就是為什麼 `dd if=/dev/sda`（讀磁碟原始資料）能做磁碟克隆、`mkfs.ext4 /dev/sda1`（格式化）能在區塊裝置上建檔案系統。**危險**：直接寫區塊裝置（`dd of=/dev/sda`）會覆蓋磁碟原始資料，破壞檔案系統——這是 `dd` 被叫「disk destroyer」的原因。區塊裝置的威力（直接存取磁碟）也是它的危險（一個指令毀掉整顆磁碟）。

## major/minor number：裝置的身份證

裝置檔案不用 inode 的資料指標（Ch 4），而是用 **major/minor number** 標識它對應哪個 driver/裝置：

```bash
ls -l /dev/null /dev/sda 2>/dev/null
# crw-rw-rw- 1 root root 1, 3 ... /dev/null   ← major=1, minor=3
# brw-rw---- 1 root disk 8, 0 ... /dev/sda    ← major=8, minor=0
#                            ↑ major, minor

# 用 stat 看
stat -c "%t %T" /dev/null     # major minor（hex）
```

```
major / minor number：
  major：哪個「driver」（裝置類型）
    1 = memory devices（null, zero, random...）
    8 = SCSI/SATA 磁碟（sda, sdb...）
        │
  minor：同一 driver 下的「哪個具體裝置」
    /dev/sda minor=0（整顆）, /dev/sda1 minor=1（分區1）
        │
  → kernel 用 (major, minor) 找到對應的 driver 和裝置
  → 裝置檔案的 inode 不存資料，存的是 (major, minor)
```

> major/minor number 是裝置的「身份證」。major 說「用哪個 driver」（記憶體 driver、磁碟 driver...），minor 說「driver 管的哪個具體裝置」。kernel 看到你 open `/dev/sda`，從 inode 取得 (8, 0)，找到 SATA driver 和第 0 號磁碟。這解釋了裝置檔案和一般檔案的根本不同：一般檔案的 inode 指向資料 block（Ch 4），裝置檔案的 inode 存 (major, minor)，指向一個 driver。你能用 `mknod` 手動建裝置檔案（指定 major/minor），雖然現代用 udev 自動管理。

## named pipe（FIFO）：行程間通訊

named pipe（FIFO）是一種特殊檔案，讓兩個行程透過「檔案」通訊：

```bash
cd ~/cmdlab
# 建立一個 named pipe
mkfifo mypipe
ls -l mypipe
# prw-r--r-- 1 you you 0 ... mypipe     ← p = named pipe

# 在一個終端機寫，另一個讀（行程間通訊）
# 終端機 1：
echo "hello through pipe" > mypipe      # 寫（會阻塞，等有人讀）
# 終端機 2：
cat mypipe                              # 讀（收到 "hello through pipe"）
```

```
named pipe（FIFO）：
  像一根「水管」：一端寫，另一端讀
  資料不存磁碟（在 kernel 的 buffer）
  寫的一端阻塞，直到有人讀（同步）
        │
  和匿名 pipe（Ch 20 的 | ）的差別：
  匿名 pipe：只在親屬行程間（shell 的 | ），沒有名字
  named pipe：有檔名，任意行程都能用（透過檔名連接）
        │
  用途：讓沒有親屬關係的行程通訊（透過共同的 pipe 檔名）
```

named pipe 是 Ch 20（pipe）的預習——它是「有名字的 pipe」，讓任意行程透過檔名通訊。匿名 pipe（`cmd1 | cmd2`）只在 shell 的親屬行程間用。named pipe 解決「兩個獨立啟動的程式怎麼通訊」。

## socket：行程間/網路通訊

socket 也是特殊檔案（type `s`），用於更複雜的通訊（雙向、網路）：

```bash
# 看系統的 unix socket（行程間通訊）
ls -l /run/*.sock 2>/dev/null | head
# srw-rw-rw- ... /run/docker.sock      ← s = socket
ls -l /var/run/systemd/private 2>/dev/null
```

```
socket 的兩種：
  Unix domain socket（檔案系統上的 socket 檔案）：
    本機行程間通訊，如 /run/docker.sock
    比 pipe 強（雙向、有連線概念）
        │
  network socket（沒有檔案系統路徑）：
    TCP/UDP 網路通訊
    不在 /dev 或檔案系統（用 IP:port 標識）
        │
  socket 也用檔案介面（read/write/close）操作
  但建立和連接用特殊的 socket syscall（socket/bind/connect）
```

socket 是網路和進階 IPC 的基礎，本課不深入（網路課程的主題）。這裡的重點是：socket 也是「一切皆檔案」的一員——一旦建立連線，讀寫用 read/write（檔案介面），雖然建立過程用特殊 syscall。

## 故意弄壞：dd 寫錯裝置

```bash
# 安全的 dd（在 sandbox 建檔案）
dd if=/dev/zero of=~/cmdlab/test.img bs=1M count=10   # OK，寫到普通檔案

# 危險示範（不要真做！）：
# dd if=/dev/zero of=/dev/sda bs=1M
#   ↑ 把零寫到整顆磁碟 → 摧毀所有資料和分區表
#     "disk destroyer" 的由來
#
# 為什麼危險：/dev/sda 是區塊裝置（磁碟原始存取）
#   寫它 = 直接覆蓋磁碟 raw bytes，繞過所有保護
#   一個指令毀掉整顆磁碟（無法復原）
```

> `dd of=/dev/sda` 的危險展示了區塊裝置的雙面性。區塊裝置讓你直接存取磁碟（克隆、格式化、救援的強大工具），但也讓你能一個指令摧毀整顆磁碟。`dd` 沒有確認、沒有 undo，寫錯 `of=` 就災難。**永遠三次確認 dd 的 of= 指向哪個裝置**（`lsblk` 確認）。這是「一切皆檔案」威力的反面——磁碟是檔案，所以你能用檔案工具操作它，包括不小心摧毀它。

## 踩雷集錦

1. **以為 /dev 裡的是真檔案**：/dev 裡是裝置檔案（kernel 介面），不是磁碟資料。/dev/null 不存東西、/dev/sda 是磁碟存取介面。它們的 inode 存 (major, minor)，不存資料

2. **dd 寫錯裝置摧毀磁碟**：區塊裝置（/dev/sda）直接存取磁碟。`dd of=/dev/sda` 覆蓋整顆磁碟無法復原。永遠確認 of= 指向哪（lsblk）

3. **混淆字元裝置和區塊裝置**：字元（c，位元組流，如 null/tty）vs 區塊（b，block + 快取，如磁碟）。ls -l 第一字元區分

4. **named pipe 的阻塞行為困惑**：寫 named pipe 會阻塞直到有人讀（同步）。一個人寫沒人讀，會卡住。理解它是「同步的水管」

5. **以為 socket 和檔案一樣建立**：socket 用特殊 syscall（socket/bind/connect）建立，雖然之後用 read/write。不是 `touch` 能建的

## 進階：/dev 怎麼來的——udev 與 devtmpfs

`/dev` 裡的裝置檔案怎麼產生的？現代用 **udev** 動態管理：

```
/dev 的演進：
  早期：靜態 /dev（手動 mknod 建所有可能的裝置檔案）
    → 幾千個裝置檔案，大部分用不到
        │
  現代：devtmpfs + udev（動態）
    devtmpfs：kernel 偵測到裝置就在 /dev 建檔案
    udev：userspace daemon，管理裝置檔案的命名、權限、symlink
        │
  插入 USB → kernel 偵測 → devtmpfs 建 /dev/sdb
    → udev 根據規則設權限、建 /dev/disk/by-id/... symlink
        │
  → /dev 反映「當前實際存在的裝置」（即時、動態）
```

```bash
# 看 udev 規則
ls /etc/udev/rules.d/ /lib/udev/rules.d/ 2>/dev/null | head
# 看裝置的穩定 symlink（by-id, by-uuid）
ls -l /dev/disk/by-id/ 2>/dev/null | head
#   ↑ udev 建的穩定名稱（不像 sda/sdb 可能變動）
```

> `/dev` 是動態的（devtmpfs + udev）——kernel 偵測到裝置就建檔案，udev 管理命名和權限。這就是為什麼插 USB 馬上出現 `/dev/sdb`，拔掉就消失。udev 還建「穩定 symlink」（`/dev/disk/by-uuid/...`）——因為 `sda`/`sdb` 的編號可能隨插拔順序變，但 UUID 不變。掛載磁碟用 UUID（`/etc/fstab` 裡）比用 `/dev/sda1` 穩定。理解 udev，你會懂為什麼裝置名稱有時會變、以及怎麼用穩定名稱（by-uuid/by-id）。

## 動手練習

1. 玩特殊檔案：`echo x > /dev/null`（消失）、`head -c 20 /dev/zero | xxd`（零）、`head -c 16 /dev/urandom | xxd`（隨機）。理解 driver 決定行為

2. 看裝置類型：`ls -l /dev/null /dev/sda /dev/tty`（如果有），認出 c（字元）、b（區塊）。看 major/minor number

3. 玩 named pipe：`mkfifo ~/cmdlab/pipe`，一個終端機 `echo hi > pipe`，另一個 `cat pipe`。觀察寫的一端阻塞直到讀

4. 看 /dev 的動態：`ls -l /dev/disk/by-uuid/`（udev 建的穩定 symlink），對比 `/dev/sda`。理解為什麼掛載用 UUID 更穩定

## 本章重點整理

- 特殊檔案是「kernel 暴露的介面」不是儲存的資料：字元裝置（c）、區塊裝置（b）、named pipe（p）、socket（s）
- 常用字元裝置：/dev/null（黑洞）、/dev/zero（零）、/dev/urandom（隨機）——用檔案介面，driver 決定行為
- 區塊裝置（/dev/sda）是磁碟原始存取（block + 快取）；dd 寫它能克隆/格式化，也能摧毀磁碟
- major/minor number 是裝置身份證（major=driver，minor=具體裝置）；裝置 inode 存 (major,minor) 不存資料
- named pipe（FIFO）讓任意行程透過檔名通訊（Ch 20 的預習）；/dev 由 devtmpfs+udev 動態管理

## 自我檢核

- [ ] 能說出 ls -l 的檔案類型字元（c/b/p/s）各代表什麼特殊檔案
- [ ] 能解釋 /dev/null、/dev/zero、/dev/urandom 的行為，以及「driver 決定行為」
- [ ] 知道區塊裝置（/dev/sda）是磁碟原始存取，以及 dd 的危險
- [ ] 知道 major/minor number 的作用（裝置身份證）
- [ ] 知道 named pipe 和匿名 pipe（| ）的差別，以及 /dev 怎麼動態產生（udev）

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 14 (File Systems - device files), Ch 44 (Pipes and FIFOs)** — Michael Kerrisk
  - **讀哪幾章**：device files 那節、Ch 44（FIFO）
  - **這本書的定位**：特殊檔案和 IPC 的權威來源
  - **前提**：本章

### 官方文件

- **[null(4)](https://man7.org/linux/man-pages/man4/null.4.html)**, **[random(4)](https://man7.org/linux/man-pages/man4/random.4.html)**, **[fifo(7)](https://man7.org/linux/man-pages/man7/fifo.7.html)** man pages
  - **讀哪裡**：各裝置/FIFO 的行為
  - **學什麼**：特殊檔案的精確語意（注意是 section 4 = 裝置，7 = 概念）
  - **前提**：本章 + Ch 2（man section）

### 部落格 / 文章

- **[/dev/null and friends](https://www.linusakesson.net/programming/tty/)** 或 udev 相關文章
  - **這篇說什麼**：特殊裝置檔案的實際運作
  - **讀哪裡**：device files 那部分
  - **為什麼值得讀**：補充本章的裝置檔案實作細節

→ [Ch 9 mount 與檔案系統階層](./09-mount-fhs.md)
