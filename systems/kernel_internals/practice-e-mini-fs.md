# 練習 E — ramdisk block device / mini fs

> **這是 Part 6（Ch 33–36）的整合練習。** 這四章你把「檔案」這個抽象從上到下拆完了：VFS 的四大物件 superblock/inode/dentry/file 怎麼把所有檔案系統統一到同一組介面（Ch 33）、一次 `read()` 怎麼從 syscall 一路穿過 VFS、page cache、到底層裝置（Ch 34）、一個最小 in-memory fs 怎麼實作 inode operations 和 file operations（Ch 35）、底層 block layer 怎麼用 bio 和 blk-mq 把讀寫請求送到真正的磁碟（Ch 36）。這個練習把它們拼成一件能動手的事：**做一個真的能用的儲存裝置或檔案系統——不是玩具，是能 `mkfs`、能 `mount`、能 `dd`、能 `ls -la` 的東西。** 你在前四章讀懂的 VFS 物件圖、bio 結構、blk-mq queue，現在要親手接起來，讓它們真的搬動資料。

這個練習給你**兩條路**，各深入一個子系統，選一條主攻（時間夠就兩條都做，它們互補得剛好）：

- **路 A（block device 導向，深入 block layer）**：寫一個 **ramdisk block device 驅動**——用一塊 `vmalloc` 的記憶體（Ch 6）當「磁碟」，實作 blk-mq（Ch 36）的 `queue_rq` 處理每個 bio 的讀寫。掛上後你能 `mkfs.ext4 /dev/myramX` 格式化它、`mount` 起來、當一顆真的磁碟用。這條路讓你看清「一個 block request 怎麼從檔案系統掉到你的 driver、你怎麼把 bio 裡的 page 搬進/搬出你的 backing store」。
- **路 B（filesystem 導向，深入 VFS）**：把 Ch 35 的 in-memory fs **擴成能真正用的檔案系統**——加上真正的檔案內容讀寫（用 page cache / `simple_*` helper）、支援 `mkdir` 巢狀目錄、`rename`、`unlink`、正確的 link count（接 `linux_commands` 的 inode/硬連結）、`ls -la` 顯示正確的 mode/uid/size/mtime。這條路讓你看清「VFS 把哪些事幫你做完了、哪些 operation 你非實作不可、link count 這種 metadata 怎麼在 kernel 裡維護」。
- **共同進階任務（兩條路都要做）**：用 Ch 34 的 ftrace/gdb **追一次 `write` 從 `vfs_write` 到你的裝置/fs 的完整路徑**，畫出實際的呼叫鏈；並**驗證 page cache 的行為**——寫進去的資料不會馬上落到你的 backing store，`sync` 才落（對照 Ch 21 的 dirty writeback）。這一步是整個練習的認知收束：你會親眼看到 page cache 這層 buffer 存在，以及它什麼時候把資料 flush 下來。

## 背景與動機：為什麼要「做一個真的能用的」而不是讀懂就好

前四章你讀了 VFS 物件、bio、blk-mq 的源碼。讀懂設計和能做出來，中間隔著一條很寬的河。這個練習就是逼你過河。

讀源碼時你會有一種「我懂了」的錯覺——`struct bio` 有 `bi_iter` 和 `bi_io_vec`，`queue_rq` 回傳 `BLK_STS_OK`，`inode_operations` 有 `.lookup`/`.create`/`.mkdir`。但真到你要寫的時候，一堆讀源碼不會逼你面對的問題會全部冒出來：blk-mq 的 `tag_set` 到底要填哪些欄位才不會 `insmod` 就 panic？一個 request 裡有好幾個 bio、一個 bio 裡有好幾個 segment，你怎麼遍歷它們把資料搬對？fs 這邊，一個 `inode` 的 `i_nlink` 什麼時候要 `inc` 什麼時候要 `drop`、`dput`/`iput` 的引用計數漏一個會怎樣？這些「讀源碼看不到、動手才會撞到」的細節，正是資深與資淺的分水嶺，也是這個練習的價值所在。

而且這個練習有一個別的練習給不了的爽點：**你做出來的東西能被系統其他部分當真的用**。路 A 做完，`mkfs.ext4 /dev/myram0` 會真的在你的 vmalloc 記憶體上鋪一個 ext4 檔案系統，`mount` 起來 `cp` 檔案進去、`umount`、再 `mount`、檔案還在（只要模組沒卸載）——因為 ext4 這個成熟的檔案系統完全不知道底下是你的玩具 ramdisk，它只看到一個標準 block device 介面。這就是 Ch 36 講的「block layer 的抽象」在你手裡活起來：**你只實作了「把這些 sector 搬進搬出」，整個 ext4 就免費跑在你上面。** 路 B 則是反過來——你的 fs 完全不知道 VFS 上面掛著 `bash`、`ls`、`cat`，它們透過 VFS 的統一介面操作你的 fs，你只實作 VFS 要求的那組 operation。

**全程在 Ch 0 的 QEMU + gdb 環境驗證。** 兩條路都需要 QEMU 裡有對應的 user 工具：路 A 需要 `mkfs.ext4`/`mount`/`dd`（busybox 的 mount 夠用，`mkfs.ext4` 要另外塞 e2fsprogs，見卡關提示 1）；路 B 只需要 busybox 的 `ls`/`mkdir`/`cat`/`ln`。共同進階任務需要 kernel 開 ftrace（`CONFIG_FUNCTION_TRACER`，Ch 34/53 會用），Ch 0 的 config 沒開的話補開一下。

## 先建立心智模型

動手前，先把兩條路各自「資料怎麼流」的圖在腦中畫清楚。

### 路 A：一個寫請求怎麼掉進你的 ramdisk

```
   user: echo hi > /mnt/f     （/mnt 掛的是你 ramdisk 上格式化的 ext4）
        │  write() syscall
        ▼
   VFS: vfs_write → ext4 的 write → 寫進 page cache（Ch 21，先不落盤）
        │  ... 之後 writeback（或 sync）觸發 ...
        ▼
   ext4 把 dirty page 包成 bio：bi_sector（要寫哪個磁區）+ bio_vec[]（哪些 page）
        │  submit_bio()
        ▼
   block layer：把 bio 併進 request，塞進 blk-mq 的 software queue → hardware queue
        │  你的 driver 註冊的 .queue_rq 被呼叫（Ch 36）
        ▼
   ┌──────────────── 你的 ramdisk driver ────────────────┐
   │  queue_rq(rq):                                        │
   │    blk_mq_start_request(rq)                           │
   │    rq_for_each_segment(bvec, rq, iter):   ← 遍歷每段  │
   │       pos = blk_rq_pos(rq) << 9  （sector→byte）      │
   │       把 bvec 的 page 內容 memcpy 到 disk_mem[pos..]  │
   │       （寫）或反向（讀）                              │
   │    blk_mq_end_request(rq, BLK_STS_OK)                 │
   └──────────────────────────────────────────────────────┘
        │
        ▼
   disk_mem[]  ← 你 vmalloc 的那塊「磁碟」，資料真的躺在這
```

四個關鍵認知：

- **你的 driver 只負責「搬 sector」，不懂檔案**（Ch 36）。ext4 才懂 inode、目錄、journal。你收到的是「把第 N 個 sector 的 4KB 搬進來/搬出去」，你完全不需要知道那是哪個檔案的哪一段。這就是 block device 抽象的威力：檔案系統和裝置驅動徹底解耦。
- **一個 request 可能有多個 bio、一個 bio 有多個 segment**（Ch 36）。block layer 會把相鄰 sector 的 bio 併成一個 request 以攤平開銷。你不能只處理一個 bio、一個 page——要用 `rq_for_each_segment` 把整個 request 的每個 segment 都搬到。漏搬 = 資料損毀，`mkfs` 或 `mount` 就會報錯。
- **sector 是 512 bytes，不管你的邏輯 block 多大**（Ch 36）。`blk_rq_pos(rq)` 回傳的是 512-byte sector 編號。要換成 byte offset 就 `<< 9`。這是 block layer 的鐵律，你的 backing store 定址一定要用這個換算，錯了資料全錯位。
- **blk-mq 的 `tag_set` 是整個 driver 能不能起來的關鍵**（Ch 36）。`nr_hw_queues`、`queue_depth`、`ops`（放你的 `.queue_rq`）、`cmd_size` 這些欄位填錯，`blk_mq_alloc_tag_set` 或 `blk_mq_alloc_disk` 會失敗，最惡劣的情況是 `insmod` 直接 panic（見卡關提示 2）。

### 路 B：VFS 幫你做了什麼、你要補什麼

```
   user: ls -la /mnt/mydir      （/mnt 掛的是你的 minifs）
        │  一連串 syscall：openat/getdents/newfstatat...
        ▼
   VFS：把路徑一段段 lookup，走 dentry cache，該建 inode 時呼叫你的 op
        │
        ▼
   ┌──────────────── 你的 minifs ────────────────┐
   │  你「必須」實作的（VFS 沒法替你猜）：           │
   │    inode_operations:                           │
   │      .lookup   ← 目錄裡找一個名字對應的 inode  │
   │      .create   ← 建檔（配 new_inode + i_nlink）│
   │      .mkdir    ← 建目錄（i_nlink 要 +2！ .和..）│
   │      .unlink   ← 刪檔（drop_nlink + iput）      │
   │      .rmdir / .rename                          │
   │    file_operations（一般檔）:                  │
   │      讀寫用 page cache helper（見下）          │
   │                                                │
   │  VFS「免費」幫你做的（你別重造）：              │
   │    dentry cache（名字→inode 的快取）           │
   │    page cache（檔案內容的快取，Ch 21）         │
   │    generic_file_read_iter / write helper       │
   │    simple_* 系列（simple_lookup/simple_link…） │
   └────────────────────────────────────────────────┘
        │
        ▼
   inode 的資料：一般檔的內容躺在 page cache 的 page 裡
                （in-memory fs 不落盤，page cache 就是最終儲存）
```

四個關鍵認知：

- **VFS 已經幫你做掉一大半，別重造輪子**（Ch 33/35）。`dentry` 快取、page cache、路徑解析、權限檢查（`inode_permission`）都是 VFS 的事。kernel 還提供一整套 `simple_*` helper（`simple_lookup`、`simple_link`、`simple_unlink`、`simple_rmdir`、`simple_rename`）專門給 in-memory fs 用——ramfs/tmpfs 就是靠它們。你的工作是「把該掛的 operation 掛上、把 inode 的 metadata 維護對」，不是重寫 VFS。
- **`i_nlink`（link count）是 fs 最容易寫錯的 metadata**（接 `linux_commands` 硬連結）。一般檔剛建出來 `i_nlink = 1`。**目錄剛建出來 `i_nlink = 2`**（一個是它自己在父目錄的 entry，一個是它裡面的 `.`）；每在它底下再建一個子目錄，父目錄的 `i_nlink` 要 **+1**（子目錄的 `..` 指回父）。`ln` 建硬連結 `inc_nlink`，`unlink` 要 `drop_nlink`，掉到 0 且沒人開著才真的釋放。數錯 `ls -la` 的第二欄（link count）就不對，`rmdir` 也會判斷錯「目錄空不空」。
- **in-memory fs 的「儲存」就是 page cache 本身**（Ch 21/35）。ext4 的 page cache 是磁碟內容的快取，flush 後落盤。但 ramfs/你的 minifs **沒有底層磁碟**——檔案內容寫進 page cache 的 page 後就停在那，page cache 就是最終儲存。所以它的 page 永遠是「乾淨」的（沒有 backing store 可落），也永遠不會被 reclaim 換出（除非 swap）。這是理解「為什麼 tmpfs 吃的是記憶體」的關鍵。
- **引用計數：`dput`/`iput`/`dget`/`ihold` 漏一個就洩漏或 UAF**（Ch 33）。`dentry` 和 `inode` 都是 refcount 管理。你在 op 裡拿了 `inode` 要記得對應地放、`d_instantiate` 會消費一個 inode 引用、`new_inode` 給你一個帶引用的 inode。漏 `iput` 洩漏 inode（`rmmod` 時 `still in use` 或 kmemleak 報）、多 `iput` 一次就 UAF。這和練習 C 「碰別人 mm 要配對 refcount」是同一種紀律。

## 任務規格

### 路 A：ramdisk block device（`myram.ko`）

實作一個 blk-mq-based 的 ramdisk block driver。

**核心結構**：一塊 `vmalloc` 的記憶體當磁碟（`disk_mem`，大小可用 module param 設，預設 16 MB）、一個 `struct gendisk`（代表這顆磁碟）、一個 `struct blk_mq_tag_set`（blk-mq 的請求佇列設定）。

**必做**：

1. 模組載入時：`alloc_disk`/`blk_mq_alloc_disk`（v6.12 用 `blk_mq_alloc_disk`，見卡關提示 2）建 gendisk、設好 `disk->fops`、`set_capacity`（單位是 512-byte sector）、`add_disk` 掛上，`/dev/myram0` 出現。
2. 實作 `.queue_rq`：對每個 request，`blk_mq_start_request` → `rq_for_each_segment` 遍歷每個 segment → 依 `rq_data_dir(rq)`（READ/WRITE）在 `disk_mem` 和 segment 的 page 之間 `memcpy` → `blk_mq_end_request(rq, BLK_STS_OK)`。
3. 邊界檢查：`blk_rq_pos(rq) << 9` + 這次要搬的長度不能超過 `disk_mem` 大小，超了回 `BLK_STS_IOERR`。
4. 模組卸載：`del_gendisk` → `put_disk` → `blk_mq_free_tag_set` → `vfree(disk_mem)`，順序別錯（掛上的相反）。

**驗收**：`insmod` 後 `/dev/myram0` 出現、`mkfs.ext4 /dev/myram0` 成功、`mount /dev/myram0 /mnt` 成功、`cp` 檔案進去 `umount` 再 `mount` 檔案還在、`rmmod` 乾淨。

### 路 B：mini filesystem（`minifs.ko`）

把一個 in-memory fs 擴成能真正操作檔案和巢狀目錄的樣子。

**必做**：

1. 註冊 `file_system_type`（`.mount = ...`，用 `mount_nodev` 或 `get_tree_nodev`），`mount -t minifs none /mnt` 能掛上，根目錄可 `ls`。
2. **一般檔的讀寫**：檔案內容用 page cache——`inode->i_mapping->a_ops` 掛 `.read_folio`/`.write_begin`/`.write_end`（照 ramfs/libfs 的做法，或直接用 `ram_aops`），`file_operations` 用 `generic_file_read_iter`/`generic_file_write_iter`。`echo hi > /mnt/f; cat /mnt/f` 要正確。
3. **`create` / `mkdir` 巢狀目錄**：`.create` 建一般檔（`i_nlink=1`）、`.mkdir` 建目錄（`i_nlink=2`，且父目錄 `inc_nlink`）。`mkdir -p /mnt/a/b/c` 要成功、`ls` 看得到層級。
4. **`unlink` / `rmdir`**：刪檔（`drop_nlink` + `iput`）、刪空目錄（父目錄 `drop_nlink`）。`rm`/`rmdir` 要正確，刪非空目錄要回 `-ENOTEMPTY`。
5. **`rename`**：`mv /mnt/a /mnt/b` 要正確（可直接用 `simple_rename`）。
6. **`ls -la` 顯示正確 metadata**：mode、uid/gid、size、link count、mtime 都要對。`.getattr` 用 `simple_getattr` 就夠。

**驗收**：`mount` 成功、檔案讀寫正確、`mkdir -p` 巢狀目錄、`ls -la` 的 link count 正確（目錄 ≥2、每個子目錄讓父 +1）、`rename`/`unlink`/`rmdir` 正確、`umount` + `rmmod` 乾淨、kmemleak 無洩漏。

### 共同進階任務（兩條路都要）

1. **ftrace 追 write 路徑**：用 `function_graph` tracer 或 `trace-cmd`，觸發一次寫（路 A 對掛載的 fs 寫、路 B 直接寫你的 fs），抓出從 `vfs_write` 到你的 `.queue_rq`（路 A）或你的 `.write_end`（路 B）的完整呼叫鏈，畫成一張圖（見期望輸出）。
2. **驗證 page cache 不馬上落盤**：路 A 對掛載的 ext4 寫一個檔案後，**立刻**在你的 driver 裡數「`.queue_rq` 被呼叫幾次」——你會發現寫完 `.queue_rq` 還沒被呼叫（或只有 metadata），資料還在 page cache。然後 `sync`（或等 30 秒 writeback），`.queue_rq` 才被呼叫把 dirty page 刷下來。這直接驗證 Ch 21 的 dirty writeback。路 B 因為沒有 backing store，改成觀察「`echo` 完 page 是 dirty 但 in-memory fs 不 flush」。

### 驗收標準總表

| # | 路 | 檢查項 | 怎麼驗 |
|---|---|---|---|
| A1 | A | `insmod` 後 `/dev/myram0` 出現 | `insmod myram.ko; ls -l /dev/myram0` |
| A2 | A | `mkfs.ext4` 成功 | `mkfs.ext4 -F /dev/myram0` |
| A3 | A | `mount` + 讀寫 + 持久（不卸模組） | `mount /dev/myram0 /mnt; echo hi>/mnt/f; umount /mnt; mount ...; cat /mnt/f` |
| A4 | A | `dd` 讀寫位址對得上（不錯位） | `dd if=/dev/zero of=/dev/myram0 bs=4k count=16; dd if=... 讀回驗證` |
| A5 | A | 越界 request 回 IOERR 不 crash | `dd` 寫超過 capacity |
| A6 | A | `rmmod` 乾淨 | `umount; rmmod myram; dmesg 無異常` |
| B1 | B | `mount -t minifs` 成功、根目錄可 `ls` | `mount -t minifs none /mnt; ls /mnt` |
| B2 | B | 一般檔讀寫正確 | `echo hi>/mnt/f; cat /mnt/f` |
| B3 | B | `mkdir -p` 巢狀目錄 | `mkdir -p /mnt/a/b/c; ls -R /mnt` |
| B4 | B | `ls -la` link count 正確 | 目錄第二欄 ≥2；在目錄下再建子目錄後父目錄 +1 |
| B5 | B | `unlink`/`rmdir`/`rename` 正確 | `rm /mnt/f; rmdir /mnt/a/b/c; mv ...`；刪非空目錄回 ENOTEMPTY |
| B6 | B | `umount`+`rmmod` 乾淨、kmemleak 無洩漏 | `umount; rmmod minifs; echo scan>/sys/kernel/debug/kmemleak` |
| C1 | 共同 | ftrace 抓到 `vfs_write → ... → 你的 op` 完整鏈 | `function_graph` 或 `trace-cmd` |
| C2 | 共同 | 驗證 write 不馬上落盤，`sync` 才落（Ch 21） | 數 `.queue_rq` 次數 / 觀察 dirty page |

## 期望輸出範例

### 路 A：格式化 + 掛載 + 持久

```
/ # insmod /myram.ko
myram: registered /dev/myram0, 16 MB (32768 sectors)
/ # ls -l /dev/myram0
brw-------    1 0        0         254,   0 Jul 31 09:00 /dev/myram0

/ # mkfs.ext4 -F -q /dev/myram0
/ # mount /dev/myram0 /mnt
/ # echo "hello from ramdisk" > /mnt/greeting.txt
/ # cat /mnt/greeting.txt
hello from ramdisk
/ # umount /mnt
/ # mount /dev/myram0 /mnt          # 重新掛載
/ # cat /mnt/greeting.txt           # 檔案還在（資料躺在 vmalloc 記憶體）
hello from ramdisk
/ # umount /mnt
```

`dd` 驗證定址不錯位（寫一個 pattern 到 offset，讀回來對）：

```
/ # dd if=/dev/urandom of=/tmp/pat bs=4k count=1
/ # dd if=/tmp/pat of=/dev/myram0 bs=4k seek=100 count=1   # 寫到第 100 個 4k block
/ # dd if=/dev/myram0 of=/tmp/back bs=4k skip=100 count=1  # 從同位置讀回
/ # cmp /tmp/pat /tmp/back && echo "OK: 定址正確"
OK: 定址正確
```

### 路 B：巢狀目錄 + 正確 link count

```
/ # insmod /minifs.ko
minifs: registered filesystem type 'minifs'
/ # mount -t minifs none /mnt
/ # echo "content" > /mnt/file1
/ # cat /mnt/file1
content
/ # mkdir -p /mnt/dir1/dir2
/ # ls -la /mnt
drwxr-xr-x    3 0    0     0 Jul 31 09:00 .       # 根：link=3（自己 + . + dir1 的 ..）
drwxr-xr-x    3 0    0     0 Jul 31 09:00 ..
-rw-r--r--    1 0    0     8 Jul 31 09:00 file1    # 一般檔：link=1
drwxr-xr-x    3 0    0     0 Jul 31 09:00 dir1     # 有一個子目錄：link=3
/ # ls -la /mnt/dir1
drwxr-xr-x    3 0    0     0 Jul 31 09:00 .
drwxr-xr-x    3 0    0     0 Jul 31 09:00 ..
drwxr-xr-x    2 0    0     0 Jul 31 09:00 dir2     # 空目錄：link=2（. 和父的 entry）
/ # rmdir /mnt/dir1                                 # 刪非空目錄
rmdir: /mnt/dir1: Directory not empty                # 正確回 ENOTEMPTY
/ # rm /mnt/file1; rmdir /mnt/dir1/dir2; rmdir /mnt/dir1
/ # umount /mnt
```

注意 link count 的規律：空目錄 = 2（自己在父目錄的名字 + 內部的 `.`），每多一個子目錄 +1（子目錄的 `..` 回指）。`file1` 是一般檔 = 1。這一欄數對，代表你的 `i_nlink` 維護對了。

### 共同進階：ftrace 抓 write 呼叫鏈（路 A）

```
/ # cd /sys/kernel/tracing
/ # echo function_graph > current_tracer
/ # echo vfs_write > set_graph_function
/ # echo 1 > tracing_on
/ # echo hi > /mnt/f ; sync           # sync 逼 page cache 落盤
/ # echo 0 > tracing_on
/ # cat trace | grep -A30 vfs_write
 vfs_write() {
   ext4_file_write_iter() {
     ... generic_perform_write() {
       ext4_write_begin() { ... }       # 資料進 page cache（Ch 21，此時還沒到你）
       ext4_write_end() { ... }
     }
   }
 }
 ... 稍後 writeback/sync 時 ...
 ext4_writepages() {
   submit_bio() {
     blk_mq_submit_bio() {
       myram_queue_rq() {               # ★ 資料現在才掉進你的 driver
         blk_update_request() ...
       }
     }
   }
 }
```

這張圖是整個練習的認知收束：`echo hi` 當下，資料只走到 `ext4_write_end`（進 page cache 就返回了），**你的 `myram_queue_rq` 沒被呼叫**；直到 `sync` 觸發 writeback，資料才被包成 bio、`submit_bio` 下來，才進你的 driver。你親眼看到 Ch 21 的 dirty writeback：**write 不等於落盤，中間隔著 page cache。**

### 共同進階：驗證 page cache 延遲落盤（路 A 數 queue_rq 次數）

在 driver 加一個 atomic 計數器 + `/sys/module/myram/parameters/nr_io` 或 dmesg：

```
/ # cat /sys/kernel/tracing/... # 或你自己的計數
/ # NR0=$(cat /proc/myram_nr_io)         # 寫前的 IO 次數
/ # echo "some data" > /mnt/f            # 寫一個小檔
/ # NR1=$(cat /proc/myram_nr_io)         # 寫後立刻看
/ # echo "寫完立刻: 多了 $((NR1-NR0)) 次 IO（可能是 0 或只有 metadata）"
寫完立刻: 多了 0 次 IO
/ # sync                                  # 逼 writeback
/ # NR2=$(cat /proc/myram_nr_io)
/ # echo "sync 後: 又多了 $((NR2-NR1)) 次 IO（dirty page 現在才落盤）"
sync 後: 又多了 3 次 IO
```

`echo` 完 IO 次數不變（資料還在 page cache），`sync` 後才增加——這是 Ch 21 dirty writeback 的鐵證，你用自己 driver 的計數器親手量到了。

## 卡關提示

1. **QEMU 的 busybox 裡沒有 `mkfs.ext4`（路 A 必踩）**。busybox 通常不含 e2fsprogs。三個解法：(a) 把 host 的 `mkfs.ext4`（`/sbin/mkfs.ext4`，是 e2fsprogs 的一部分）連同它需要的 lib 塞進 initramfs——但它動態連結，要一起搬 lib，麻煩；(b) 用 **靜態編的 e2fsprogs**（或直接 `apt install e2fsprogs` 後找靜態版）；(c) **最省事**：不用 ext4，改在 host 上先 `mkfs.ext4` 一個 image 檔、或直接測 busybox 內建的 `mkfs.vfat`/`mkfs.minix`（busybox 常含 `mkfs.minix` 和 `mkfs.vfat`）。**推薦先用 `mkfs.minix /dev/myram0`**（minix fs 簡單、busybox 幾乎都內建）驗證你的 ramdisk，通了再挑戰 ext4。驗收 A2 用 minix 也算過。

2. **blk-mq 的 API 在 v6.x 變過，別抄舊教材（路 A 最致命）**。舊教材（LDD3、很多部落格）用 `blk_init_queue` + `blk_alloc_disk` + 老的 request function，v6.12 **全部改了**。v6.12 的正確流程是：填 `struct blk_mq_tag_set`（`.ops = &myram_mq_ops`（含你的 `.queue_rq`）、`.nr_hw_queues = 1`、`.queue_depth = 128`、`.cmd_size = 0`、`.numa_node = NUMA_NO_NODE`）→ `blk_mq_alloc_tag_set(&tag_set)` → **`blk_mq_alloc_disk(&tag_set, NULL, NULL)`**（一步同時建 request queue + gendisk，回傳 `struct gendisk *`）→ 設 `disk->fops`/`disk->private_data`/`set_capacity` → `add_disk(disk)`。`tag_set` 任一欄位沒填（尤其 `.ops` 或 `.nr_hw_queues=0`）會 `insmod` 就 panic 或 `blk_mq_alloc_tag_set` 回錯。這是路 A 最容易卡死的一步，照 v6.12 的 `drivers/block/brd.c`（真正的 ramdisk driver）或 `null_blk` 抄結構。

3. **遍歷 request 要用 `rq_for_each_segment`，不是只處理一個 bio（路 A）**。一個 request 可能併了多個 bio、一個 bio 有多個 `bio_vec` segment。正確寫法：`struct req_iterator iter; struct bio_vec bvec; rq_for_each_segment(bvec, rq, iter) { ... }`——它一次給你一個 segment（一個 `bio_vec`，含 `bv_page`/`bv_offset`/`bv_len`）。用 `kmap_local_page(bvec.bv_page)` 拿到可讀寫的核心虛擬位址（v6.12 用 `kmap_local_page`，不是舊的 `kmap`），`memcpy` 完 `kunmap_local`。`pos` 要用 `blk_rq_pos(rq) << 9` 起算、每搬一個 segment 前進 `bvec.bv_len`。只處理第一個 bio、或忘了在 segment 間前進 `pos`，資料會錯位，`mkfs`/`mount` 直接報損毀。

4. **目錄的 `i_nlink` 陷阱：建目錄要 +2、父目錄要 +1（路 B 最容易錯）**。一般檔 `new_inode` 後 `set_nlink(inode, 1)`。**目錄不一樣**：`inc_nlink(inode)` 讓它從 1 到 2（`.` 自己指自己），然後 `.mkdir` 裡還要對**父目錄** `inc_nlink(dir)`（子目錄的 `..` 指回父）。`libfs` 的 `simple_mkdir` 語義就是這樣，你若自己寫 `.mkdir` 要記得這兩步。刪目錄（`.rmdir`）反過來：`drop_nlink` 子目錄、`drop_nlink` 父目錄。數錯 `ls -la` 第二欄就不對，更嚴重的是 `rmdir` 用 `i_nlink` 判斷目錄空不空（`simple_empty`），數錯會刪錯或刪不掉。直接用 `simple_mkdir`... 但 libfs 沒有現成的 `simple_mkdir`——你要在 `.mkdir` 裡自己 `inc_nlink(dir); d_instantiate(...)`，這正是要你動手的地方。

5. **`iput`/`dput`/`d_instantiate` 的引用計數配對（路 B）**。`new_inode(sb)` 回傳一個帶引用的 inode。`d_instantiate(dentry, inode)` 把 inode 綁到 dentry 上並**消費**那個引用（之後別再對它 `iput`）。若 `create` 中途失敗（例如配不到記憶體），已經 `new_inode` 的要 `iput` 掉再回錯，否則洩漏。`.lookup` 找不到要 `d_add(dentry, NULL)`（negative dentry），找到要 `d_add(dentry, inode)` 且對 inode 補引用（`ihold` 或 `iget`）。漏 `iput` → `rmmod` 時 `VFS: Busy inodes` 或 kmemleak 報；多 `iput` → UAF panic。這和練習 C 「碰別人 mm 配對 refcount」是同一種紀律，用 `simple_*` helper 能避開大半（它們幫你配對好了）。

## 分步實作建議

### 路 A 五步

1. **先讓 `/dev/myram0` 出現，`.queue_rq` 只回 OK 不搬資料**。填 `tag_set`、`blk_mq_alloc_disk`、`set_capacity`、`add_disk`。`.queue_rq` 裡先 `blk_mq_start_request` + `blk_mq_end_request(rq, BLK_STS_OK)`（假裝搬好了）。`insmod` 看 `/dev/myram0` 出現、`dmesg` 無 panic。這步把最容易卡的 blk-mq 骨架先弄對。
2. **實作真正的資料搬移**。`rq_for_each_segment` 遍歷、`kmap_local_page` + `memcpy`（依 `rq_data_dir` 決定方向）、`pos` 用 `blk_rq_pos(rq) << 9` 起算並逐 segment 前進。`dd if=/dev/zero of=/dev/myram0` + `dd if=/dev/myram0` 讀回，用 `cmp` 驗定址對。
3. **加邊界檢查**。搬之前算 `pos + total_len` 不能超 `disk_size`，超了 `blk_mq_end_request(rq, BLK_STS_IOERR)`。`dd` 故意寫超過 capacity，看它回錯不 crash。
4. **格式化 + 掛載**。先 `mkfs.minix /dev/myram0`（busybox 內建，見卡關提示 1）→ `mount` → `cp` 檔案 → `umount` → `mount` → 檔案還在。通了再挑戰 `mkfs.ext4`。
5. **共同進階：加 IO 計數器 + ftrace**。driver 裡 `atomic_inc` 一個計數器（`/proc` 或 dmesg 暴露），做 page cache 延遲落盤實驗；ftrace `function_graph` 抓 `vfs_write → submit_bio → myram_queue_rq`。

### 路 B 五步

1. **先讓空 fs 能 mount，根目錄能 `ls`**。註冊 `file_system_type`、`.mount` 用 `get_tree_nodev`（或 `mount_nodev`）、`fill_super` 裡建根 inode（目錄、`i_op`/`i_fop` 掛好、`i_nlink=2`）。`mount -t minifs none /mnt; ls /mnt` 不 crash。
2. **加一般檔的建立與讀寫**。`.create` 用 `new_inode` + 設 mode + 掛 page-cache aops（`i_mapping->a_ops = &ram_aops` 之類）+ `file_operations`（`generic_file_read_iter`/`generic_file_write_iter`）+ `d_instantiate`。`echo hi > /mnt/f; cat /mnt/f` 正確。
3. **加 `mkdir` + 巢狀 + link count**。`.mkdir` 建目錄 inode（`i_nlink=2`）+ 父 `inc_nlink` + `d_instantiate`。`mkdir -p /mnt/a/b/c`、`ls -la` 檢查 link count（卡關提示 4）。
4. **加 `unlink`/`rmdir`/`rename`**。用 `simple_unlink`/`simple_rmdir`/`simple_rename`（libfs 的，幫你處理 nlink 和 `simple_empty` 檢查），掛到 `inode_operations`。`rm`/`rmdir`/`mv` 測，刪非空目錄要 `-ENOTEMPTY`。
5. **共同進階：ftrace + page cache 觀察**。`function_graph` 抓 `vfs_write → generic_perform_write → 你的 write_end`；觀察寫完 page 是 dirty 但 in-memory fs 不 flush（沒 backing store）。

## 完整參考解答

<details>
<summary>路 A：ramdisk block device（myram.c + Makefile + 測試腳本）</summary>

### `myram.c`

```c
// myram.c — 最小 blk-mq ramdisk block device（練習 E 路 A）
// 用一塊 vmalloc 記憶體當磁碟，實作 blk-mq 的 .queue_rq 搬 bio。
// 掛上後可 mkfs.minix / mkfs.ext4、mount、當真磁碟用。
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/vmalloc.h>
#include <linux/blkdev.h>
#include <linux/blk-mq.h>
#include <linux/hdreg.h>
#include <linux/highmem.h>      // kmap_local_page / kunmap_local

static int size_mb = 16;        // 磁碟大小（MB），module param
module_param(size_mb, int, 0444);

#define SECTOR_SHIFT 9          // 一個 sector = 512 bytes（block layer 鐵律）
#define KERNEL_SECTOR_SIZE (1 << SECTOR_SHIFT)

static int major;
static struct gendisk *myram_disk;
static struct blk_mq_tag_set myram_tag_set;
static char *disk_mem;                          // 我們的「磁碟」（vmalloc）
static size_t disk_size;                        // bytes
static atomic_t nr_io = ATOMIC_INIT(0);         // 進階：數 queue_rq 被呼叫幾次

// 搬一個 request 的所有 segment：disk_mem <-> bio pages
static blk_status_t myram_transfer(struct request *rq)
{
    struct req_iterator iter;
    struct bio_vec bvec;
    // 起始 byte offset：sector 編號 << 9
    loff_t pos = blk_rq_pos(rq) << SECTOR_SHIFT;
    bool write = (rq_data_dir(rq) == WRITE);

    rq_for_each_segment(bvec, rq, iter) {       // 遍歷每個 segment（卡關提示 3）
        void *kaddr;
        unsigned int len = bvec.bv_len;

        // 邊界檢查：超過磁碟大小就回 IOERR（驗收 A5）
        if (pos + len > disk_size) {
            pr_err("myram: out-of-bounds IO at pos=%lld len=%u (disk=%zu)\n",
                   pos, len, disk_size);
            return BLK_STS_IOERR;
        }

        kaddr = kmap_local_page(bvec.bv_page);  // v6.12：kmap_local_page 不是舊 kmap
        if (write)
            memcpy(disk_mem + pos, kaddr + bvec.bv_offset, len);   // 寫：page → disk
        else
            memcpy(kaddr + bvec.bv_offset, disk_mem + pos, len);   // 讀：disk → page
        kunmap_local(kaddr);

        pos += len;                              // 前進到下一段（別忘了！否則錯位）
    }
    return BLK_STS_OK;
}

// blk-mq 的核心 callback：每個 request 進來時被呼叫
static blk_status_t myram_queue_rq(struct blk_mq_hw_ctx *hctx,
                                   const struct blk_mq_queue_data *bd)
{
    struct request *rq = bd->rq;
    blk_status_t st;

    blk_mq_start_request(rq);                    // 告訴 block layer「我開始處理了」
    atomic_inc(&nr_io);                          // 進階：計數（page cache 落盤實驗用）
    st = myram_transfer(rq);
    blk_mq_end_request(rq, st);                  // 完成（成功或 IOERR）
    return BLK_STS_OK;                           // 回 OK 表示「已受理」，實際結果在 end_request
}

static const struct blk_mq_ops myram_mq_ops = {
    .queue_rq = myram_queue_rq,
};

// 讓 mkfs/fdisk 能問「幾個 cylinder/head/sector」（可選但有些工具要）
static int myram_getgeo(struct block_device *bdev, struct hd_geometry *geo)
{
    geo->heads = 4;
    geo->sectors = 16;
    geo->cylinders = (disk_size >> SECTOR_SHIFT) / (4 * 16);
    geo->start = 0;
    return 0;
}

static const struct block_device_operations myram_fops = {
    .owner = THIS_MODULE,
    .getgeo = myram_getgeo,
};

// /proc/myram_nr_io：暴露 IO 計數給 shell 讀（page cache 實驗）
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
static int nr_io_show(struct seq_file *m, void *v)
{
    seq_printf(m, "%d\n", atomic_read(&nr_io));
    return 0;
}
static int nr_io_open(struct inode *i, struct file *f)
{ return single_open(f, nr_io_show, NULL); }
static const struct proc_ops nr_io_pops = {
    .proc_open = nr_io_open, .proc_read = seq_read,
    .proc_lseek = seq_lseek, .proc_release = single_release,
};

static int __init myram_init(void)
{
    int ret;

    disk_size = (size_t)size_mb << 20;
    disk_mem = vzalloc(disk_size);               // vmalloc + 清零（Ch 6）
    if (!disk_mem)
        return -ENOMEM;

    major = register_blkdev(0, "myram");         // 拿一個 major number
    if (major < 0) { ret = major; goto err_vfree; }

    // ---- blk-mq 骨架（v6.12 正確流程，卡關提示 2）----
    memset(&myram_tag_set, 0, sizeof(myram_tag_set));
    myram_tag_set.ops = &myram_mq_ops;
    myram_tag_set.nr_hw_queues = 1;
    myram_tag_set.queue_depth = 128;
    myram_tag_set.numa_node = NUMA_NO_NODE;
    myram_tag_set.cmd_size = 0;
    myram_tag_set.flags = BLK_MQ_F_SHOULD_MERGE;
    ret = blk_mq_alloc_tag_set(&myram_tag_set);
    if (ret) goto err_unreg;

    // 一步建 request queue + gendisk（v6.12 的 blk_mq_alloc_disk）
    myram_disk = blk_mq_alloc_disk(&myram_tag_set, NULL, NULL);
    if (IS_ERR(myram_disk)) { ret = PTR_ERR(myram_disk); goto err_tagset; }

    myram_disk->major = major;
    myram_disk->first_minor = 0;
    myram_disk->minors = 1;
    myram_disk->fops = &myram_fops;
    myram_disk->private_data = NULL;
    snprintf(myram_disk->disk_name, DISK_NAME_LEN, "myram0");
    set_capacity(myram_disk, disk_size >> SECTOR_SHIFT);   // 單位：512-byte sector

    ret = add_disk(myram_disk);                  // 掛上，/dev/myram0 出現
    if (ret) goto err_putdisk;

    proc_create("myram_nr_io", 0444, NULL, &nr_io_pops);
    pr_info("myram: registered /dev/myram0, %d MB (%llu sectors)\n",
            size_mb, (unsigned long long)(disk_size >> SECTOR_SHIFT));
    return 0;

err_putdisk:
    put_disk(myram_disk);
err_tagset:
    blk_mq_free_tag_set(&myram_tag_set);
err_unreg:
    unregister_blkdev(major, "myram");
err_vfree:
    vfree(disk_mem);
    return ret;
}

static void __exit myram_exit(void)
{
    remove_proc_entry("myram_nr_io", NULL);
    del_gendisk(myram_disk);                     // 卸載順序：掛上的相反
    put_disk(myram_disk);
    blk_mq_free_tag_set(&myram_tag_set);
    unregister_blkdev(major, "myram");
    vfree(disk_mem);
    pr_info("myram: unloaded\n");
}

module_init(myram_init);
module_exit(myram_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Practice E path A: blk-mq ramdisk block device");
MODULE_AUTHOR("kernel_internals");
```

**幾個設計決定**：`blk_mq_alloc_disk` 一步取代舊的 `blk_alloc_queue` + `alloc_disk`（v6.x 的重構，卡關提示 2）；`myram_transfer` 用 `rq_for_each_segment` 遍歷整個 request 的每個 segment，`pos` 逐段前進——只處理第一個 bio 會資料錯位（卡關提示 3）；`kmap_local_page` 是 v6.12 建立臨時映射的正解（`kmap` 已過時）；`atomic_t nr_io` + `/proc/myram_nr_io` 是共同進階的 page cache 落盤實驗用。錯誤路徑的 `goto` 階梯確保任一步失敗都乾淨回收。

### `Makefile`

```makefile
# recipe 行首是 Tab 不是空白
obj-m += myram.o
KDIR := /path/to/your/linux-6.12       # 指向 Ch 0 build 的源碼樹
all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
```

### `test_myram.sh`（在 QEMU busybox 裡跑）

```sh
#!/bin/busybox sh
set -e
insmod /myram.ko
ls -l /dev/myram0

# --- 定址正確性（dd pattern 讀寫）---
dd if=/dev/urandom of=/tmp/pat bs=4k count=1 2>/dev/null
dd if=/tmp/pat of=/dev/myram0 bs=4k seek=100 count=1 2>/dev/null
dd if=/dev/myram0 of=/tmp/back bs=4k skip=100 count=1 2>/dev/null
cmp /tmp/pat /tmp/back && echo "OK: 定址正確"

# --- 格式化 + 掛載 + 持久（minix，busybox 內建）---
mkfs.minix /dev/myram0
mkdir -p /mnt
mount -t minix /dev/myram0 /mnt
echo "hello ramdisk" > /mnt/greeting.txt
cat /mnt/greeting.txt
umount /mnt
mount -t minix /dev/myram0 /mnt
echo "重掛後: $(cat /mnt/greeting.txt)"
umount /mnt

# --- page cache 落盤實驗 ---
mount -t minix /dev/myram0 /mnt
N0=$(cat /proc/myram_nr_io)
echo "delayed data" > /mnt/f
N1=$(cat /proc/myram_nr_io)
echo "寫完立刻 IO 增量: $((N1-N0))"
sync
N2=$(cat /proc/myram_nr_io)
echo "sync 後 IO 增量: $((N2-N1))"
umount /mnt

rmmod myram
echo "=== 路 A 全部通過 ==="
```

</details>

<details>
<summary>路 B：mini filesystem（minifs.c + Makefile + 測試腳本）</summary>

### `minifs.c`

```c
// minifs.c — 最小可用 in-memory filesystem（練習 E 路 B）
// 支援：一般檔讀寫（page cache）、mkdir 巢狀目錄、unlink/rmdir/rename、
//       正確的 i_nlink（link count）。基於 libfs 的 simple_* helper。
#include <linux/init.h>
#include <linux/module.h>
#include <linux/fs.h>
#include <linux/fs_context.h>
#include <linux/pagemap.h>
#include <linux/mm.h>
#include <linux/time.h>

#define MINIFS_MAGIC 0x4d494e49      // "MINI"

static const struct inode_operations minifs_dir_iops;
static const struct file_operations  minifs_file_fops;
static const struct address_space_operations minifs_aops;

// libfs 提供 ram_aops（page-cache-backed，無 backing store），這裡直接用它的行為：
// write 進 page cache、read 從 page cache，永不落盤（in-memory fs 的儲存 = page cache）
extern const struct address_space_operations ram_aops;

static struct inode *minifs_get_inode(struct super_block *sb,
                                      const struct inode *dir, umode_t mode)
{
    struct inode *inode = new_inode(sb);         // 帶引用的新 inode（卡關提示 5）
    if (!inode)
        return NULL;

    inode->i_ino = get_next_ino();
    inode_init_owner(&nop_mnt_idmap, inode, dir, mode);
    inode->i_mapping->a_ops = &ram_aops;         // page cache 當儲存
    mapping_set_gfp_mask(inode->i_mapping, GFP_HIGHUSER);
    simple_inode_init_ts(inode);                 // atime/mtime/ctime = now

    switch (mode & S_IFMT) {
    case S_IFREG:                                // 一般檔
        inode->i_op = &simple_dir_inode_operations; // 一般檔其實不需要 dir iop，但無妨
        inode->i_fop = &minifs_file_fops;
        // i_nlink 預設 1（new_inode 給的），正確
        break;
    case S_IFDIR:                                // 目錄
        inode->i_op = &minifs_dir_iops;
        inode->i_fop = &simple_dir_operations;   // libfs 的目錄讀取（getdents）
        inc_nlink(inode);                        // 目錄 nlink: 1 -> 2（. 指自己，卡關提示 4）
        break;
    default:
        init_special_inode(inode, mode, 0);
        break;
    }
    return inode;
}

// --- .create：建一般檔 ---
static int minifs_create(struct mnt_idmap *idmap, struct inode *dir,
                         struct dentry *dentry, umode_t mode, bool excl)
{
    struct inode *inode = minifs_get_inode(dir->i_sb, dir, mode | S_IFREG);
    if (!inode)
        return -ENOSPC;
    d_instantiate(dentry, inode);                // 綁 dentry，消費 inode 引用（卡關提示 5）
    dget(dentry);
    inode_set_mtime_to_ts(dir, inode_set_ctime_current(dir));
    return 0;
}

// --- .mkdir：建目錄，父目錄 nlink +1（卡關提示 4）---
static int minifs_mkdir(struct mnt_idmap *idmap, struct inode *dir,
                        struct dentry *dentry, umode_t mode)
{
    struct inode *inode = minifs_get_inode(dir->i_sb, dir, mode | S_IFDIR);
    if (!inode)
        return -ENOSPC;
    d_instantiate(dentry, inode);
    dget(dentry);
    inc_nlink(dir);                              // 父目錄 nlink +1（子目錄的 .. 回指）
    inode_set_mtime_to_ts(dir, inode_set_ctime_current(dir));
    return 0;
}

// --- 目錄的 inode_operations：lookup/create/mkdir/unlink/rmdir/rename ---
// lookup/unlink/rmdir/rename 直接用 libfs 的 simple_*（幫你處理 nlink 與 empty 檢查）
static const struct inode_operations minifs_dir_iops = {
    .create  = minifs_create,
    .lookup  = simple_lookup,                    // 找名字→dentry（配 dcache）
    .link    = simple_link,                      // 硬連結（inc_nlink）
    .unlink  = simple_unlink,                    // 刪檔（drop_nlink + 更新 mtime）
    .mkdir   = minifs_mkdir,
    .rmdir   = simple_rmdir,                     // 刪空目錄（simple_empty 檢查 + drop_nlink）
    .rename  = simple_rename,                    // mv
};

// --- 一般檔的 file_operations：讀寫走 page cache ---
static const struct file_operations minifs_file_fops = {
    .read_iter  = generic_file_read_iter,        // 從 page cache 讀
    .write_iter = generic_file_write_iter,       // 寫進 page cache
    .mmap       = generic_file_mmap,
    .fsync      = noop_fsync,                     // in-memory，不需要真的 fsync
    .llseek     = generic_file_llseek,
    .splice_read = filemap_splice_read,
};

// --- super block：mount 時建根目錄 inode ---
static int minifs_fill_super(struct super_block *sb, struct fs_context *fc)
{
    struct inode *root;

    sb->s_magic = MINIFS_MAGIC;
    sb->s_blocksize = PAGE_SIZE;
    sb->s_blocksize_bits = PAGE_SHIFT;
    sb->s_maxbytes = MAX_LFS_FILESIZE;
    sb->s_op = &simple_super_operations;         // libfs 的 super ops（statfs 等）
    sb->s_time_gran = 1;

    root = minifs_get_inode(sb, NULL, S_IFDIR | 0755);  // 根目錄，nlink=2
    if (!root)
        return -ENOMEM;
    sb->s_root = d_make_root(root);              // 建根 dentry（失敗會自動 iput root）
    if (!sb->s_root)
        return -ENOMEM;
    return 0;
}

static int minifs_get_tree(struct fs_context *fc)
{
    return get_tree_nodev(fc, minifs_fill_super);  // 無底層裝置的 fs（同 ramfs/tmpfs）
}

static const struct fs_context_operations minifs_ctx_ops = {
    .get_tree = minifs_get_tree,
};

static int minifs_init_fs_context(struct fs_context *fc)
{
    fc->ops = &minifs_ctx_ops;
    return 0;
}

static struct file_system_type minifs_type = {
    .owner           = THIS_MODULE,
    .name            = "minifs",
    .init_fs_context = minifs_init_fs_context,
    .kill_sb         = kill_litter_super,        // 卸載時清掉所有 dentry/inode
    .fs_flags        = FS_USERNS_MOUNT,
};

static int __init minifs_init(void)
{
    int ret = register_filesystem(&minifs_type);
    if (ret)
        return ret;
    pr_info("minifs: registered filesystem type 'minifs'\n");
    return 0;
}

static void __exit minifs_exit(void)
{
    unregister_filesystem(&minifs_type);
    pr_info("minifs: unregistered\n");
}

module_init(minifs_init);
module_exit(minifs_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Practice E path B: minimal in-memory filesystem");
MODULE_AUTHOR("kernel_internals");
```

**幾個設計決定**：整個 fs 站在 libfs（`fs/libfs.c`）的 `simple_*` helper 上——`simple_lookup`/`simple_unlink`/`simple_rmdir`/`simple_rename` 幫你處理 dcache 掛接、`i_nlink` 增減、`simple_empty`（rmdir 判空）、negative dentry，這正是卡關提示 5 說的「用 helper 避開 refcount 地雷」。你**必須自己寫**的只有 `.create` 和 `.mkdir`，因為 nlink 的初始值和「mkdir 要對父目錄 `inc_nlink`」這件事沒有通用 helper 幫你（卡關提示 4）。檔案內容用 `ram_aops`（page-cache-backed，`fs/libfs.c` 匯出）+ `generic_file_*_iter`——寫進 page cache 就是最終儲存，永不落盤，這是 in-memory fs 的本質（Ch 21/35）。`get_tree_nodev` 表示「這個 fs 沒有底層 block device」，和 ramfs/tmpfs 同類。

> **版本註記**：`inode_init_owner`/`simple_inode_init_ts`/`inode_set_ctime_current` 這組 API 是 v6.x 的樣子（timestamp 存取器在 v6.6+ 改過，`mnt_idmap` 參數在 v6.3+ 加入）。若你的 6.12 樹某個 helper 名字對不上，去 `fs/ramfs/inode.c` 看它現在怎麼寫——ramfs 是這個 fs 的直系範本，跟著它的 API 走最保險。

### `Makefile`

```makefile
# recipe 行首是 Tab
obj-m += minifs.o
KDIR := /path/to/your/linux-6.12
all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules
clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean
```

### `test_minifs.sh`（在 QEMU busybox 裡跑）

```sh
#!/bin/busybox sh
set -e
insmod /minifs.ko
mkdir -p /mnt
mount -t minifs none /mnt

# --- 一般檔讀寫 ---
echo "content" > /mnt/file1
echo "讀回: $(cat /mnt/file1)"

# --- 巢狀目錄 + link count ---
mkdir -p /mnt/dir1/dir2
echo "--- ls -la /mnt（看 link count 第二欄）---"
ls -la /mnt
echo "--- 根目錄 nlink 應該是 3（自己 + . + dir1 的 ..）---"

# --- 刪非空目錄應失敗 ---
if rmdir /mnt/dir1 2>/dev/null; then
    echo "FAIL: 刪非空目錄竟成功"
else
    echo "OK: 刪非空目錄正確回 ENOTEMPTY"
fi

# --- rename ---
mv /mnt/file1 /mnt/file2
echo "rename 後: $(ls /mnt | grep file)"

# --- 清乾淨 ---
rm /mnt/file2
rmdir /mnt/dir1/dir2
rmdir /mnt/dir1
umount /mnt
rmmod minifs
echo "=== 路 B 全部通過 ==="

# kmemleak 檢查（若 kernel 開了 CONFIG_DEBUG_KMEMLEAK）
[ -e /sys/kernel/debug/kmemleak ] && {
    echo scan > /sys/kernel/debug/kmemleak
    cat /sys/kernel/debug/kmemleak
}
```

</details>

<details>
<summary>共同進階：ftrace 抓 write 路徑腳本</summary>

```sh
#!/bin/busybox sh
# 追一次 write 從 vfs_write 到你的裝置/fs 的完整路徑
cd /sys/kernel/tracing 2>/dev/null || cd /sys/kernel/debug/tracing
echo 0 > tracing_on
echo function_graph > current_tracer
echo vfs_write > set_graph_function       # 只追 vfs_write 這棵子樹
echo > trace                              # 清空
echo 1 > tracing_on

# 路 A：對掛載的 fs 寫 + sync（逼 page cache 落盤，才看得到 submit_bio → queue_rq）
echo "trace me" > /mnt/traced
sync

echo 0 > tracing_on
echo "=== write 呼叫鏈（找 submit_bio / myram_queue_rq / write_end）==="
cat trace | grep -E "vfs_write|write_begin|write_end|submit_bio|queue_rq|writepage" | head -40
echo nop > current_tracer                 # 關掉 tracer
```

用 `function_graph` + `set_graph_function vfs_write` 只保留 `vfs_write` 這棵呼叫樹，`grep` 出關鍵函式看資料流。路 A 你會看到 `echo` 當下只到 `ext4_write_end`（進 page cache），`sync` 後才 `submit_bio → myram_queue_rq`——證明 page cache 延遲落盤（Ch 21）。

</details>

## 測試用例表

| 測試 | 路 | 操作 | 期望結果 | 驗收 |
|---|---|---|---|---|
| 載入 | A | `insmod myram.ko` | `/dev/myram0` 出現、無 panic | A1 |
| 定址 | A | `dd` 寫 offset 再讀回 `cmp` | 完全相同（不錯位） | A4 |
| 格式化 | A | `mkfs.minix /dev/myram0`（或 ext4）| 成功 | A2 |
| 掛載持久 | A | mount→寫→umount→remount→cat | 檔案還在 | A3 |
| 越界 | A | `dd` 寫超過 capacity | 回 IO error、不 crash | A5 |
| 卸載 | A | `umount; rmmod myram` | 乾淨、`dmesg` 無異常 | A6 |
| 載入 | B | `insmod minifs.ko; mount -t minifs` | `ls /mnt` 不 crash | B1 |
| 讀寫 | B | `echo hi>/mnt/f; cat /mnt/f` | 內容正確 | B2 |
| 巢狀 | B | `mkdir -p /mnt/a/b/c; ls -R /mnt` | 層級正確 | B3 |
| link count | B | `ls -la`，在目錄下建子目錄 | 空目錄 nlink=2、每子目錄讓父 +1 | B4 |
| 刪除 | B | `rm`/`rmdir`/刪非空目錄 | 正確；非空回 ENOTEMPTY | B5 |
| rename | B | `mv /mnt/a /mnt/b` | 成功 | B5 |
| 卸載洩漏 | B | `umount; rmmod; kmemleak scan` | 無 `Busy inodes`、無 leak | B6 |
| ftrace | 共同 | `function_graph` 抓 write | 看到 `vfs_write→...→你的 op` | C1 |
| page cache | 共同 | 寫後數 IO / 觀察 dirty | 寫完不落盤、`sync` 才落 | C2 |

> **要看到「page cache 延遲落盤」得先 `sync`**：路 A 你若只 `echo hi > /mnt/f` 就 `cat trace`，可能完全看不到 `myram_queue_rq`——因為資料還在 page cache，dirty writeback 還沒觸發。加 `sync`（或 `echo 3 > /proc/sys/vm/drop_caches` 前先 sync）逼它落盤，才看得到 `submit_bio → queue_rq`。這個「看不到」本身就是 Ch 21 的教學點：write 不等於 IO。

## 卡關時的 gdb 用法

延續 Ch 0 的 QEMU + gdb（`-s`）。`insmod` 後 `lx-symbols` 載模組符號。

路 A 停進 `queue_rq` 看一個 request 長怎樣：

```gdb
(gdb) lx-symbols
(gdb) break myram_queue_rq
(gdb) continue
# 回 QEMU 觸發一次 IO（dd 或 mount）
(gdb) print bd->rq->__sector           # 要讀寫哪個 sector（blk_rq_pos 的來源）
(gdb) print rq_data_dir(bd->rq)         # 0=READ 1=WRITE
(gdb) print bd->rq->bio                 # 這個 request 的第一個 bio
(gdb) print bd->rq->bio->bi_iter        # bio 的 iterator（sector/size）
```

路 B 停進 `.create`/`.mkdir` 看 inode 和 nlink：

```gdb
(gdb) break minifs_mkdir
(gdb) continue
# 回 QEMU: mkdir /mnt/x
(gdb) print dir->i_nlink                # 建之前父目錄的 nlink
(gdb) next                              # 單步過 inc_nlink(dir)
(gdb) print dir->i_nlink                # 應該 +1 了
(gdb) print dentry->d_name.name         # 要建的名字
```

把這個和 Ch 34 的 read/write 路徑接起來：那章你用 gdb 從 `vfs_write` 一路 `step` 進去，這裡你直接 `break` 在**你自己的 op**上，反過來 `backtrace` 看是誰一路呼叫進來的——你會看到 `vfs_write`（或 `submit_bio`）在 backtrace 的頂端。**這是 Ch 34 那條路徑的終點在你手裡。**

## 踩雷集錦

1. **blk-mq API 抄舊教材（LDD3 的 `blk_init_queue`）→ 編譯失敗或 panic**。v6.12 沒有 `blk_init_queue`、`blk_alloc_disk` 也和舊版不同。正解是 `blk_mq_alloc_tag_set` + `blk_mq_alloc_disk`（一步建 queue + disk）。`tag_set` 的 `.ops`/`.nr_hw_queues`/`.queue_depth` 沒填好會 `insmod` panic。照 v6.12 的 `drivers/block/brd.c` 或 `null_blk` 抄，別信 2015 年的部落格。

2. **只搬第一個 bio、忘了 `rq_for_each_segment` → 資料錯位、mkfs 報損毀**。一個 request 可能併多個 bio、多個 segment。必須用 `rq_for_each_segment` 遍歷全部、`pos` 每段前進 `bv_len`。漏搬或 `pos` 沒前進，寫下去的資料錯位，`mkfs`/`mount` 立刻報 superblock 或 metadata 損毀。

3. **`sector` 當成 byte 用（少了 `<< 9`）→ 定址全錯**。`blk_rq_pos(rq)` 是 512-byte sector 編號，不是 byte。要 byte offset 一定 `<< SECTOR_SHIFT`（9）。忘了移位，你的 backing store 定址縮小 512 倍，`dd` 讀回全錯、`mkfs` 掛掉。這是 block layer 新手最常見的 bug。

4. **目錄 `i_nlink` 數錯（mkdir 忘了對父目錄 `inc_nlink`）→ `ls -la` link count 錯、`rmdir` 判斷錯**。目錄剛建 nlink=2（`.` + 父的 entry），每個子目錄讓父 +1（子的 `..`）。忘了 `inc_nlink(dir)`，`ls -la` 目錄的 link count 少 1，更糟的是 `simple_rmdir` 用 nlink 判斷「目錄空不空」，數錯會讓空目錄刪不掉或非空目錄被誤刪。自己寫 `.mkdir` 時這兩步（`inc_nlink(inode)` + `inc_nlink(dir)`）缺一不可。

5. **`iput`/`dput` 引用計數配對錯 → `rmmod` 報 `Busy inodes` 或 UAF**。`new_inode` 給你帶引用的 inode，`d_instantiate` 消費它（別再 `iput`）。`create` 中途失敗要 `iput` 已建的 inode 再回錯。用 `simple_*` helper 能避開大半（它們幫你配對），但自己寫的 `.create`/`.mkdir` 要小心。漏 `iput` → `umount` 時 `VFS: Busy inodes after unmount` 或 kmemleak 報；多一次 → UAF panic。這和練習 C「碰別人 mm 配對 refcount」同一種紀律。

## 延伸挑戰

1. **路 A 加透明壓縮**：`.queue_rq` 寫入時用 `zlib`/`lz4`（kernel 內建 `crypto` 壓縮 API）壓縮每個 block 再存，讀出時解壓。挑戰在「壓縮後長度不定，backing store 怎麼定址」——你得做一個 block→(offset,len) 的映射表。這讓你理解 zram（kernel 真的有的壓縮 ramdisk）的核心設計。

2. **路 B 支援 xattr（擴充屬性）**：實作 `.listxattr`/`.getxattr`/`.setxattr`（或掛 `simple_xattr_*`），讓 `setfattr -n user.foo -v bar /mnt/f; getfattr -d /mnt/f` 能用。這接到 Ch 47/48（xattr 是 SELinux label 和 capabilities 的載體）。

3. **量 I/O 效能對比 tmpfs**：路 A 掛 ext4 後 `dd if=/dev/zero of=/mnt/big bs=1M count=100` 計時，對照 `tmpfs` 上同樣操作。你會發現你的 ramdisk+ext4 比 tmpfs 慢——因為多了「檔案系統 + block layer + 你的 memcpy」兩層，tmpfs 直接是 page cache。這個差距量化了「block device 抽象的成本」，是 Ch 21/36 的實測收束。

4. **路 A 支援 discard/TRIM**：實作 `.queue_rq` 裡對 `REQ_OP_DISCARD` 的處理（把對應區域 `memset(0)` 並可選擇 `vfree` 那段），讓 `fstrim /mnt` 能回收空間。這是現代 SSD/thin provisioning 的關鍵語義。

5. **路 B 加真正的 inode 上限與 `-ENOSPC`**：現在的 minifs 無限建檔（吃光記憶體）。加一個 inode/空間計數，超過上限回 `-ENOSPC`，`df /mnt` 顯示合理的 total/used（實作 `.statfs` 或用 `simple_statfs` 改）。這讓你理解 fs 的配額與空間會計。

6. **兩條路合體**：把路 B 的 minifs 改成**真的把資料寫到路 A 的 `/dev/myram0`**（而不是 page cache）——實作 `.read_folio`/`.write_begin`/`.write_end` 去讀寫 block device。這就從「in-memory fs」變成「真正的 on-disk fs」，是理解 ext2 這種真檔案系統的第一步（雖然工程量陡增）。

## 自我檢核

- [ ] 不看解答，能說出路 A 的 blk-mq 骨架三件套（`blk_mq_tag_set` → `blk_mq_alloc_disk` → `add_disk`）以及 `tag_set` 至少要填哪些欄位（Ch 36）
- [ ] 能解釋為什麼 `.queue_rq` 要用 `rq_for_each_segment` 遍歷而不能只處理一個 bio，漏了會怎樣（Ch 36）
- [ ] 能說出 `blk_rq_pos(rq)` 的單位是 512-byte sector、換 byte 要 `<< 9`，忘了會怎樣（Ch 36）
- [ ] 能解釋路 B 裡 VFS 幫你做了什麼（dcache/page cache/路徑解析）、你非實作不可的是什麼（`.lookup`/`.create`/`.mkdir` 等），以及 `simple_*` helper 的角色（Ch 33/35）
- [ ] 能說清目錄的 `i_nlink` 規律：空目錄=2、每個子目錄讓父+1，並解釋 mkdir 為什麼要 `inc_nlink(dir)`（接 linux_commands 硬連結）
- [ ] 能說出 `new_inode`/`d_instantiate`/`iput` 的引用計數配對關係，漏一個會發生什麼（Ch 33）
- [ ] 能用 ftrace `function_graph` 抓出 `vfs_write` 到你的 op 的完整呼叫鏈（Ch 34）
- [ ] 能解釋「`echo hi > /mnt/f` 當下資料還在 page cache、`sync` 才落到你的 backing store」，並說出這對應 Ch 21 的 dirty writeback
- [ ] 面試被問「一個 block device driver 最少要實作什麼」，能答出「blk-mq 的 `.queue_rq` + gendisk 註冊 + 把 bio 的 page 搬進搬出 backing store」
- [ ] 面試被問「in-memory fs（tmpfs）的檔案內容存在哪」，能答出「page cache 的 page 本身，沒有 backing store 所以永不落盤」

## 這個練習把哪些章拼在了一起

- **Ch 6 記憶體配置**：路 A 用 `vmalloc`/`vzalloc` 配一大塊連續虛擬位址當「磁碟」；路 B 的 inode 走 slab（`new_inode` 底層）
- **Ch 21 page cache/writeback**：共同進階的核心——write 先進 page cache、`sync`/writeback 才落盤，路 A 用 IO 計數器親手量到延遲落盤，in-memory fs 則是「page cache 即最終儲存」
- **Ch 33 VFS 四物件**：路 B 全程操作 superblock/inode/dentry/file，`i_op`/`i_fop`/`a_ops` 的掛接、`iput`/`dput` 引用計數
- **Ch 34 read/write 完整路徑**：ftrace/gdb 追 `vfs_write` 到你的 op，你做出來的東西就是那條路徑的終點
- **Ch 35 最小 in-memory fs**：路 B 是它的實用化擴充——加真正的讀寫、巢狀目錄、link count、rename
- **Ch 36 block layer/bio/blk-mq**：路 A 全程——`blk_mq_tag_set`、`.queue_rq`、`rq_for_each_segment` 遍歷 bio segment、sector 定址
- **跨課 linux_commands**：`i_nlink`（硬連結 link count）、`ls -la` 的每一欄 metadata、`mkfs`/`mount`/`dd` 的使用者視角，現在你從 kernel 這側把它們實作出來

做完這個練習，你手上有一個能被 `mkfs`/`mount`/`dd` 當真磁碟用的 ramdisk block device（路 A），或一個能 `mkdir -p`、`ls -la` link count 正確、讀寫巢狀目錄的真檔案系統（路 B）——而且你用 ftrace 親眼看過一次 write 從 `vfs_write` 穿過 page cache、writeback 才落到你 driver 的完整路徑。Part 6 的「檔案這個抽象怎麼從 VFS 一路到裝置」到此不只讀懂、還親手接通了。接下來 Part 7 換一個維度：你的 ramdisk 是怎麼在 `/dev` 出現的、`/sys/block/myram0` 那些屬性檔哪來的、driver 和 device 怎麼被 kernel 配對——這一切背後是 kernel 的統一裝置模型：kobject、sysfs、bus、driver。我們從「一個 `struct kobject` 怎麼變成 `/sys` 裡的一個目錄」開始。

→ [Ch 37 Device model：kobject/sysfs/bus/driver](./37-device-model.md)
