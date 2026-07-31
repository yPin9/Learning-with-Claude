# Ch 33 — VFS 四大物件：superblock/inode/dentry/file

> **目標**：理解 Linux 為什麼能用同一組 `open`/`read`/`write` syscall 操作 ext4、xfs、tmpfs、procfs——因為它們背後都實作了同一組抽象。學完你能在腦中畫出 `fd → struct file → struct dentry → struct inode → struct super_block` 的物件關係圖，看懂 VFS 怎麼用「函式指標表」在 C 語言裡做出物件導向的多型，並寫一個模組遍歷當前 process 開啟的所有檔案、印出它們的完整路徑。

Part 6 從這裡開始。前五個 Part 我們待在 process、記憶體、鎖、中斷這些「不落地」的子系統裡；從這章起，我們處理 kernel 怎麼把「一個檔案」這個抽象，攤平到千百種底層儲存之上。

如果你上過本 repo 的 `linux_commands`，你已經從**使用者視角**認識過這一切：inode 存元資料不存檔名、hard link 是多個檔名指向同一 inode、fd 是 process 開啟檔案的號碼。那門課教你「這些現象是什麼」；這章我們鑽進 kernel，看**這些現象在源碼裡各自對應哪個 struct**、為什麼要這樣切分。

## 為什麼需要這個？

先看沒有 VFS 會怎樣。假設 kernel 直接讓每個檔案系統各自實作 syscall：

- ext4 提供 `ext4_open`、`ext4_read`、`ext4_write`
- xfs 提供 `xfs_open`、`xfs_read`、`xfs_write`
- tmpfs、procfs、NFS 各一套……

那麼使用者空間的 `cat file` 要先知道 `file` 在哪種檔案系統上，才知道該呼叫哪組 syscall。這荒謬——`cat` 不應該關心它讀的是 ext4 還是 NFS。更糟的是，`cat a > b` 若 `a` 在 ext4、`b` 在 tmpfs，這個 pipeline 根本沒法寫。

Unix 的答案是 **VFS（Virtual Filesystem Switch，虛擬檔案系統交換層，也叫 Virtual File System）**：在所有具體檔案系統之上放一層抽象。使用者空間永遠只看到一組 syscall（`open`/`read`/`write`/`stat`/…，見 Ch 4）；VFS 收到 syscall 後，查出「這個檔案屬於哪個檔案系統」，再轉呼叫那個檔案系統註冊的實作函式。

這正是**物件導向的多型（polymorphism）**，只是用 C 手工做出來的：VFS 定義好一組「介面」（一堆函式指標，例如 `read_iter`、`lookup`），每種檔案系統填好自己的實作，VFS 呼叫時透過函式指標分派（dispatch）到正確的實作。你在 C++ 用 virtual function、vtable 得到的東西，kernel 用 `struct file_operations *f_op` 這種「ops 表指標」得到。理解這個「ops 表 = vtable」的類比，是讀懂整個 VFS（乃至整個 driver 模型，Ch 37）的鑰匙。

## 先建立直覺

VFS 用**四個核心物件**描述「檔案存取」這件事。先別看源碼，先記住每個物件回答什麼問題：

```
   super_block   ── 「這個『已掛載的檔案系統』是什麼？」
                    （一次 mount = 一個 super_block；記 fs 類型、block 大小、根在哪）

   inode         ── 「這個『檔案本身』的元資料是什麼？」
                    （權限、大小、時間、指向 data block；★ 不含檔名 ★）

   dentry        ── 「這個『檔名』對應到哪個 inode？」
                    （把路徑中的一段名字，連到一個 inode；路徑查找的快取單元）

   file          ── 「這個『被某 process 開啟的檔案實例』狀態是什麼？」
                    （目前 offset f_pos、開啟旗標；一個 fd 背後就是一個 file）
```

為什麼要切成四個而不是一個大 struct？因為它們的**生命週期與多對一關係都不同**：

- 一個 **inode** 可以被多個 **dentry** 指向——這就是 **hard link**（同一份檔案內容，多個檔名）。所以檔名不能塞進 inode，必須獨立成 dentry。
- 一個 **inode** 可以被多個 **file** 指向——同一個檔案被 `open` 兩次，得到兩個獨立的 fd、兩個獨立的 offset，但操作的是同一份資料。所以「開啟狀態（offset）」不能塞進 inode，必須獨立成 file。
- 所有屬於同一次 mount 的 inode，共享同一個 **super_block**（它們的 block 大小、所屬檔案系統類型都一樣）。

把這三種「多對一」關係擺在一起，四個物件的切分就是必然的，不是隨意的設計。

## 四大物件解剖（源碼導讀）

四大物件的定義集中在兩個 header：`include/linux/fs.h`（super_block、inode、file 及三張 ops 表）和 `include/linux/dcache.h`（dentry）。以下欄位對應 **v6.12**；完整定義去 Bootlin（`https://elixir.bootlin.com/linux/v6.12/source/include/linux/fs.h`）對照。

### super_block：一個已掛載的檔案系統實例

`include/linux/fs.h` 的 `struct super_block`。每次你 `mount` 一個檔案系統（Ch 9 的 mount 使用者視角、本課 Ch 35 會親手 mount 一個自製 fs），kernel 就配一個 `super_block`。關鍵欄位：

```c
struct super_block {
    struct file_system_type  *s_type;      // 這是哪種 fs（ext4? tmpfs?）
    const struct super_operations *s_op;    // ★ 這個 fs 的 ops 表（多型入口）
    struct dentry            *s_root;       // 根目錄的 dentry（掛載點的「/」）
    unsigned long             s_blocksize;  // block 大小（bytes）
    loff_t                    s_maxbytes;   // 這個 fs 單一檔案的上限
    unsigned long             s_magic;      // magic number（如 EXT4 的 0xEF53）
    ...
};
```

`s_op` 指向 `struct super_operations`（同檔），這張表管「檔案系統層級」的操作，例如：

```c
struct super_operations {
    struct inode *(*alloc_inode)(struct super_block *sb);  // 配一個新 inode
    void (*destroy_inode)(struct inode *);
    int  (*write_inode)(struct inode *, struct writeback_control *wbc);  // 把 inode 寫回磁碟
    int  (*statfs)(struct dentry *, struct kstatfs *);     // 支援 statfs() syscall（df 用）
    void (*put_super)(struct super_block *);               // umount 時清理
    ...
};
```

ext4 填的是 `ext4_alloc_inode` 等，tmpfs 填 `shmem_alloc_inode`。`df -h` 看到的容量數字，就是 VFS 呼叫 `sb->s_op->statfs` 分派到具體 fs 得來的。

### inode：一個檔案的元資料（不含檔名）

`include/linux/fs.h` 的 `struct inode`。這是四大物件裡你最該熟的——`linux_commands` 裡 `ls -i` 看到的那個 inode number，`stat` 印出的權限/大小/時間，全在這裡：

```c
struct inode {
    umode_t              i_mode;    // 檔案類型 + 權限（rwxr-xr-x 那串）
    unsigned long        i_ino;     // inode number（ls -i 看到的）
    loff_t               i_size;    // 檔案大小（bytes）
    const struct inode_operations *i_op;   // ★ 元資料操作 ops 表
    const struct file_operations  *i_fop;  // ★ 開啟時要塞給 file 的預設 ops
    struct super_block  *i_sb;      // 我屬於哪個 super_block
    struct address_space *i_mapping; // ★ 指向 page cache（Ch 21）
    ...
};
```

**注意 inode 裡沒有檔名欄位**——這不是偷懶，是設計。檔名屬於「目錄項」，一個 inode 可以有多個檔名（hard link），所以名字放在 dentry 而非 inode。這正是 `linux_commands` 裡「inode 不存名字、目錄才存名字→inode 的對應」的 kernel 對應。

三個特別重要的指標：

- `i_op`（`struct inode_operations`）：管「對這個 inode 做元資料操作」。目錄型 inode 的 `i_op` 才有意義的 `lookup`/`create`/`mkdir`（在目錄裡找/建/刪一個名字）；一般檔案的 `i_op` 有 `getattr`/`setattr`（`stat`/`chmod` 走這裡）。
- `i_fop`（`struct file_operations`）：**當這個 inode 被 `open` 時**，VFS 會把 `i_fop` 拷到新建的 `struct file` 的 `f_op`。這是「inode 決定了它被開啟後能做哪些 read/write 行為」的關鍵接點。
- `i_mapping`（`struct address_space`）：指向這個檔案的 **page cache**。你在 Ch 21 學的 page cache、writeback，就是掛在這裡。讀檔案先查 `i_mapping` 的 page cache 命不命中，是 Ch 34（read 路徑）的主戲。

> **v6.12 陷阱：時間欄位變了。** 老 kernel 的 `inode->i_ctime`（`struct timespec64`）在 6.6～6.11 之間被拆成分離的 `i_ctime_sec` / `i_ctime_nsec` 純量欄位，並要求透過 accessor 存取。在 **v6.12**，直接寫 `inode->i_ctime` **編不過**——那個欄位不存在了。要用 `inode_get_ctime(inode)`、`inode_set_ctime_current(inode)` 這類函式（`inode_get_mtime`/`inode_get_atime` 同理）。你在 Ch 35 寫自製 fs 時會踩到這個，先記住。

### dentry：把檔名連到 inode

`include/linux/dcache.h` 的 `struct dentry`。dentry = **d**irectory **entry**，目錄項。它是四大物件裡最容易被忽略、卻是路徑查找效能命脈的一個：

```c
struct dentry {
    struct dentry          *d_parent;  // 父目錄的 dentry（往上走一層）
    struct qstr             d_name;    // ★ 這一段路徑的名字（如 "etc"、"passwd"）
    struct inode           *d_inode;   // ★ 這個名字對應的 inode（可能為 NULL）
    struct super_block     *d_sb;      // 屬於哪個 super_block
    const struct dentry_operations *d_op;
    ...
};
```

一個 dentry 代表**路徑中的一段名字**。`/etc/passwd` 這條路徑，在 kernel 裡是一串 dentry 鏈：根 dentry（`/`）→ `etc` 的 dentry → `passwd` 的 dentry，每個 dentry 的 `d_parent` 指向上一段，`d_name` 存自己這段名字，`d_inode` 指向這段名字對應的 inode。取一個 dentry 的 inode 慣用 accessor `d_inode(dentry)`（v6.12 仍是 `dentry->d_inode`，但用 accessor 是慣例）。

**為什麼 dentry 要獨立於 inode？** 兩個理由：

1. **hard link**：`ln a b` 之後，`a` 和 `b` 是兩個 dentry，`d_inode` 指向同一個 inode。inode 的 `i_nlink`（link 計數）會是 2。若檔名塞進 inode，這種一對多就無法表達。這是 `linux_commands` 裡「hard link 共享 inode」的 kernel 真身。
2. **路徑查找快取（dcache）**：把「名字 → inode」的對應快取起來，下次查同一路徑不必再問底層檔案系統（那可能要讀磁碟）。所有 dentry 存在一個全域 hash table 裡，`fs/dcache.c` 的 `d_lookup()` 用（父 dentry, 名字）當 key 去查。這就是 **dentry cache（dcache）**，是路徑查找快的根本原因。

有個細節：`d_inode` 可以是 **NULL**——這叫 **negative dentry**（否定 dentry）。當你查一個不存在的檔名（例如反覆 `stat /tmp/notexist`），kernel 會快取一個 `d_inode == NULL` 的 dentry，好讓下次「這名字不存在」的查詢也能命中快取、不必再問磁碟。這是 dcache 一個常被忽略但很聰明的優化。

### file：一個開啟的檔案實例

`include/linux/fs.h` 的 `struct file`。前三個物件描述「靜態的檔案系統結構」，`file` 描述「某個 process 此刻對某檔案的動態開啟狀態」。一個 `open()` syscall 成功，就產生一個 `struct file`，並在 process 的 fd table 裡放一個指向它的項（`linux_commands` 裡的 fd → 這裡的 file）：

```c
struct file {
    struct path                   f_path;    // ★ 內含 dentry + vfsmount（掛載點）
    struct inode                 *f_inode;   // 捷徑：直接指到 inode
    const struct file_operations *f_op;      // ★ read/write/... 的 ops 表
    unsigned int                  f_flags;   // open() 的 flags（O_RDONLY, O_APPEND...）
    fmode_t                       f_mode;    // 讀/寫權限模式
    loff_t                        f_pos;     // ★ 目前讀寫位置（offset）
    struct address_space         *f_mapping; // 指到 inode 的 page cache
    ...
};
```

三個關鍵：

- `f_pos`：**目前的讀寫 offset**。這就是為什麼「開啟狀態」不能塞進 inode——同一檔案被兩個 process `open`，各有各的 `file`、各有各的 `f_pos`，一個讀到一半不影響另一個。`lseek` 改的就是這個欄位。
- `f_op`（`struct file_operations`）：`open` 時從 inode 的 `i_fop` 拷過來。之後所有 `read`/`write`/`mmap`/`ioctl` 都透過它分派。這是 Ch 34（read 路徑）和整個 char device（Ch 38）的核心。
- `f_path`：一個 `struct path`，內含 `struct dentry *dentry` 和 `struct vfsmount *mnt`。為什麼要 mnt？因為同一個 inode 可能透過不同掛載點被看到（bind mount），要靠 mnt 才知道「你是從哪個掛載點進來的」，`/proc/<pid>/fd/N` 的 symlink 才能還原正確的完整路徑。

`file_operations`（`include/linux/fs.h`）是你這門課後面最常打交道的 ops 表：

```c
struct file_operations {
    ssize_t (*read_iter)(struct kiocb *, struct iov_iter *);   // ★ 現代讀路徑
    ssize_t (*write_iter)(struct kiocb *, struct iov_iter *);  // ★ 現代寫路徑
    ssize_t (*read)(struct file *, char __user *, size_t, loff_t *);   // 舊式
    loff_t  (*llseek)(struct file *, loff_t, int);             // lseek 走這
    int     (*mmap)(struct file *, struct vm_area_struct *);   // mmap 走這（接 Ch 19）
    int     (*open)(struct inode *, struct file *);
    int     (*release)(struct inode *, struct file *);         // 最後一個 close 時
    long    (*unlocked_ioctl)(struct file *, unsigned int, unsigned long);
    int     (*iterate_shared)(struct file *, struct dir_context *);  // 讀目錄（getdents）
    ...
};
```

> 現代 kernel 的讀寫主力是 `read_iter`/`write_iter`（吃 `iov_iter`，能一次描述多段緩衝、支援 direct I/O、async I/O），舊的 `read`/`write` 仍在但多數檔案系統只填 iter 版。這在 Ch 34 會詳談。

## 底層機制：四者怎麼串起來

把四個物件與 process 的 fd table 串起來，就是這張圖——**這是這章最該印進腦子的一張圖**。假設 process 開了 `/etc/passwd`，拿到 fd 3：

```
  ┌──────────── task_struct（當前 process，Ch 9）────────────┐
  │  files ─► struct files_struct                           │
  │              fd_array[]:                                 │
  │                [0] ─► file(stdin)                        │
  │                [1] ─► file(stdout)                       │
  │                [2] ─► file(stderr)                       │
  │                [3] ─────────────┐   ← 這就是 linux_commands 的 fd table │
  └────────────────────────────────┼────────────────────────┘
                                    │
                                    ▼
                        ┌──────────────────────┐
                        │   struct file        │   ← 一次 open() 一個
                        │   f_pos  = 1024      │   （offset：讀到哪了）
                        │   f_flags= O_RDONLY  │
                        │   f_op  ──────────────┼──► ext4_file_operations
                        │   f_path.dentry ─┐    │      (.read_iter = ext4_file_read_iter ...)
                        │   f_inode ──┐    │    │
                        └─────────────┼────┼────┘
                                      │    │
             ┌────────────────────────┘    ▼
             │              ┌──────────────────────┐
             │              │   struct dentry      │   "passwd"
             │              │   d_name = "passwd"  │
             │              │   d_parent ──► dentry("etc") ──► dentry("/")
             │              │   d_inode ──┐        │
             │              └─────────────┼────────┘
             │                            │
             ▼                            ▼   （★ 兩條線指到同一個 inode）
          ┌──────────────────────────────────────┐
          │   struct inode  (i_ino = 131074)      │
          │   i_mode = 0644   i_size = 2841       │
          │   i_op  ──► ext4_file_inode_operations │
          │   i_fop ──► ext4_file_operations       │
          │   i_mapping ──► address_space (page cache, Ch 21)
          │   i_sb  ──┐                            │
          └───────────┼────────────────────────────┘
                      │
                      ▼
          ┌──────────────────────────────────────┐
          │   struct super_block                  │
          │   s_type = ext4   s_blocksize = 4096  │
          │   s_op ──► ext4_sops                   │
          │   s_root ──► dentry("/")               │
          └──────────────────────────────────────┘
```

讀這張圖的三個重點：

1. **`fd → file → dentry → inode → super_block` 是一條分派鏈**。使用者拿著 fd（一個小整數），kernel 一路解引用到底層檔案系統的實作。
2. **file 同時指到 dentry 和 inode**（`f_inode` 是捷徑，避免每次都走 `f_path.dentry->d_inode`）。
3. **多對一在圖裡看得見**：另一個 process 若也 `open("/etc/passwd")`，會多一個 `struct file`（自己的 `f_pos`），但 `dentry` 和 `inode` 是同一個（dcache/icache 命中）。若有 hard link `/etc/passwd-bak`，會多一個 `dentry`，但 `inode` 還是同一個。

### 多型：VFS 怎麼分派到 ext4 或 tmpfs

當使用者 `read(fd, buf, n)`，kernel 大致走（細節見 Ch 34）：

```
  sys_read → vfs_read(file, ...) → file->f_op->read_iter(...)
                                        └─ ext4 上 = ext4_file_read_iter
                                        └─ tmpfs 上 = shmem_file_read_iter
                                        └─ procfs 上 = 某個 proc show 函式
```

VFS 那行 `file->f_op->read_iter(...)` 不知道也不關心底層是什麼——它就是解一個函式指標、呼叫它。**填表的動作發生在 mount / open 時**：mount ext4 時，`ext4_fill_super` 把 super_block 的 `s_op` 設成 `ext4_sops`、把根 inode 的 `i_fop` 設成 ext4 的 file_operations；open 時 VFS 把 `i_fop` 拷進 `file->f_op`。到 read 時，指標已經指向正確實作。這就是「C 手工做的 vtable」：`f_op` 是 vtable 指標，`read_iter` 是其中一個 slot。整個 device driver 模型（Ch 37/38）用的是同一套機制。

### 路徑查找（path walk）：open("/a/b/c") 怎麼一段段查

`open("/etc/passwd")` 拿到 fd 之前，kernel 得先把路徑字串走成一個 dentry。主流程在 `fs/namei.c`，入口是 `path_lookupat()`（更外層的 `filename_lookup` 呼叫它），核心迴圈是 `link_path_walk()`——它一段一段地 walk：

```
  path_lookupat()
    └─ link_path_walk()      逐段拆 "etc" / "passwd"，對每段呼叫：
         └─ walk_component()
              ├─ lookup_fast()   先查 dcache（in-memory hash，快）
              └─ lookup_slow()   dcache miss 才問底層 fs：
                     └─ dir_inode->i_op->lookup()   ← 多型！ext4 的 lookup 去讀磁碟目錄
```

每一段名字，先用 `lookup_fast()` 去 dcache（`d_lookup`）找有沒有現成 dentry；命中就直接拿到下一層 dentry，不碰磁碟。miss 才走 `lookup_slow()`，它呼叫**父目錄 inode** 的 `i_op->lookup`——這又是多型，ext4 會去讀磁碟上的目錄 block、找到名字對應的 inode、建一個新 dentry 塞進 dcache。所以第一次查某路徑慢（讀磁碟），之後快（dcache 命中）。這解釋了 `linux_commands` 裡「第一次 `ls` 慢、第二次快」的一部分。

walk 過程還要處理幾個特殊情況（都在 `fs/namei.c`）：

- **mount point**：走到某個 dentry 若是掛載點，要「跳」到被掛上去的 super_block 的根 dentry（`__follow_mount_rcu` 之類），路徑才能穿過掛載邊界。這是 `linux_commands` 裡 mount 之後路徑能穿透的 kernel 機制。
- **symlink**：若某段是符號連結，`i_op->get_link` 拿到連結目標，遞迴地把目標路徑也 walk 一遍（有 `MAXSYMLINKS` = 40 限制，防無限迴圈）。`linux_commands` 裡 `ln -s` 造出的東西在這裡被解開。
- **`..`**：往上走要回到 `d_parent`；但在掛載點上遇到 `..` 還要處理跨掛載回退，比想像複雜。

### RCU-walk vs ref-walk：路徑查找為什麼這麼快

path walk 是 kernel 裡**最熱的路徑之一**（每個 `open`/`stat`/`exec` 都走）。如果每 walk 一段都去對 dentry 加 refcount、拿 spinlock，多核心下這些原子操作和鎖爭用會成為瓶頸。所以 kernel 有兩種 walk 模式：

- **RCU-walk（`LOOKUP_RCU`）**：**完全無鎖、不加 refcount** 地在 dcache 裡飛速穿過整條路徑。它靠 RCU（Ch 27）保證「正在讀的 dentry 不會在我讀的當下被釋放」，靠 seqlock（Ch 28）偵測「我讀的過程中這個 dentry 有沒有被改動」。這是預設模式，快得多。
- **ref-walk**：傳統模式，每 walk 一段就對 dentry 加 refcount、必要時拿鎖。慢，但穩，能處理 RCU-walk 處理不了的情況（例如要 block 讀磁碟、要跟 symlink、遇到需要 revalidate 的網路檔案系統）。

流程是：**先樂觀地用 RCU-walk 衝**，一旦遇到 RCU-walk 搞不定的情況（要睡、要 I/O、seqlock 偵測到有人改了），就呼叫 `try_to_unlazy()`（v6.12 的名字；老 kernel 叫 `unlazy_walk`）**降級到 ref-walk**——它會嘗試把目前這段路徑的 dentry 補上 refcount，成功就繼續用 ref-walk，失敗就整條路徑從頭重走一次 ref-walk。這個「樂觀無鎖、失敗才降級」的模式，正是 Ch 27 RCU 的教科書級應用；path walk 是 RCU 在 kernel 裡最重要的性能戰場之一。`struct nameidata`（`fs/namei.c`）就是承載整個 walk 狀態（目前走到哪、哪個模式、seq 值）的那個 struct。

## 動手：從使用者空間到 kernel 觀測四大物件

四大物件都活在 kernel 記憶體裡，但每一個都有從使用者空間戳它的窗口。全部在 Ch 0 的 QEMU + gdb 環境（或任何 Linux）裡做。

### 1. `/proc/<pid>/fd`：看 fd → file → dentry → inode 的對應

```bash
$ sleep 1000 &
[1] 4242
$ ls -l /proc/4242/fd
lrwx------ 1 ... 0 -> /dev/pts/3
lrwx------ 1 ... 1 -> /dev/pts/3
lrwx------ 1 ... 2 -> /dev/pts/3
```

`/proc/<pid>/fd/N` 是個 symlink，指向那個 fd 背後 `file` 的 `f_path` 還原出來的完整路徑。這條 symlink 怎麼生出來的？procfs 對每個 fd，拿 `file->f_path`（dentry + mnt）呼叫 `d_path()`（`fs/d_path.c`）把 dentry 鏈往上走到根、拼回字串。**這正是你在 `linux_commands` 用 `lsof` / `ls -l /proc/pid/fd` 看到的東西，現在你知道它底層是 `f_path → dentry 鏈 → d_path()`。**

打開一個真檔案再看：

```bash
$ exec 5< /etc/hostname          # 用 fd 5 開 /etc/hostname
$ ls -l /proc/$$/fd/5
lr-x------ 1 ... 5 -> /etc/hostname
$ readlink /proc/$$/fd/5
/etc/hostname
$ exec 5<&-                       # 關掉 fd 5
```

### 2. `slabtop`：看 dentry / inode cache 的規模

dentry 和 inode 都從專屬的 slab cache 配置（Ch 18 的 slub）。v6.12 的 cache 名字是 `dentry`、`inode_cache`，`struct file` 的是 `filp`：

```bash
$ sudo slabtop -o | grep -E 'dentry|inode_cache|filp'
 152880 148263  96%    0.19K   3640   42     29120K dentry
  48762  46011  94%    0.63K   8127    6     32508K inode_cache
   2016   1890  93%    0.25K    126   16       504K filp
```

`dentry` 那行 15 萬個物件不是異常——大量檔案操作（build、find、git status）會塞滿 dcache。這是**故意**的快取，記憶體吃緊時 kernel 會回收（Ch 22 的 reclaim 會 shrink 這些 cache）。

### 3. `drop_caches`：手動清 dentry / inode cache

想觀察「dcache 清空後第一次查路徑變慢」的效果，可以手動清快取：

```bash
$ sync                                  # 先把 dirty page 寫回，最大化可回收量
$ echo 2 | sudo tee /proc/sys/vm/drop_caches   # 2 = 清可回收 slab（含 dentry + inode）
$ sudo slabtop -o | grep dentry         # dentry 數量會掉一大截
```

`drop_caches` 的值（v6.12 仍有效）：`1` = 清 page cache（Ch 21）；`2` = 清可回收 slab（dentry + inode 在內）；`3` = 兩者都清。這是**非破壞性**操作（只丟乾淨的快取，dirty 資料不會丟），但清完之後系統會短暫變慢——一切要重新從磁碟建 cache。生產環境別亂 `echo 3`。

### 4. 寫模組：遍歷當前 process 開啟的檔案，印出 dentry 路徑

這是本章的招牌實作。我們用 `current`（Ch 2）拿到當前 process 的 `files_struct`，遍歷它的 fd table，對每個 `struct file` 印出它的路徑——等於在 kernel 裡手工做一次 `ls /proc/self/fd`。

```c
// list_fds.c —— 遍歷 current process 的所有開啟檔案，印 dentry 路徑
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/fs.h>
#include <linux/fdtable.h>     // files_fdtable, fcheck 等
#include <linux/sched.h>       // current
#include <linux/dcache.h>      // struct dentry, d_path
#include <linux/slab.h>

static int __init list_fds_init(void)
{
    struct files_struct *files = current->files;
    struct fdtable *fdt;
    unsigned int fd;
    char *buf;

    buf = kmalloc(PATH_MAX, GFP_KERNEL);
    if (!buf)
        return -ENOMEM;

    pr_info("list_fds: open files of PID %d (%s):\n",
            current->pid, current->comm);

    /* 讀 fd table 要在 RCU read-side（fd table 可能被並行改） */
    rcu_read_lock();
    fdt = files_fdtable(files);

    for (fd = 0; fd < fdt->max_fds; fd++) {
        struct file *f = rcu_dereference(fdt->fd[fd]);
        char *p;

        if (!f)
            continue;                  // 這個 fd 沒開東西，跳過

        /* d_path 把 file 的 f_path（dentry+mnt）還原成完整路徑字串。
         * 它回傳的指標可能落在 buf 中間（末端對齊），所以用回傳值 p，不是 buf。 */
        p = d_path(&f->f_path, buf, PATH_MAX);
        if (IS_ERR(p)) {
            pr_info("  fd %u -> <d_path err %ld>\n", fd, PTR_ERR(p));
            continue;
        }

        pr_info("  fd %u -> %s  (inode=%lu, f_pos=%lld)\n",
                fd, p,
                file_inode(f)->i_ino,      // 這個 file 對應的 inode number
                f->f_pos);                  // 目前 offset
    }

    rcu_read_unlock();
    kfree(buf);
    return 0;
}

static void __exit list_fds_exit(void) { }

module_init(list_fds_init);
module_exit(list_fds_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("List open files (fd->file->dentry->inode) of insmod'ing process");
```

用 Ch 0 的 Makefile 編出 `list_fds.ko`，然後：

```bash
$ insmod ./list_fds.ko
$ dmesg | tail
list_fds: open files of PID 4321 (insmod):
  fd 0 -> /dev/pts/3   (inode=6, f_pos=0)
  fd 1 -> /dev/pts/3   (inode=6, f_pos=0)
  fd 2 -> /dev/pts/3   (inode=6, f_pos=0)
  fd 3 -> /path/to/list_fds.ko   (inode=131201, f_pos=0)
```

`current->comm` 是 `insmod` 因為 module init 跑在呼叫 `insmod` 的那個 process context（Ch 2）。fd 3 是 `insmod` 自己開來讀 `.ko` 的檔案——你剛剛親手，在 kernel 裡，走完了 `current->files → fdt->fd[3] → struct file → f_path.dentry → d_path()` 這條鏈，把使用者空間的 `/proc/self/fd` 從裡面複刻了一次。

> **為什麼要 `rcu_read_lock()`？** fd table（`fdtable`）是用 RCU 保護的（Ch 27）——別的執行緒可能正在擴充或改寫它。不在 RCU read-side 直接讀 `fdt->fd[]` 會 race。`rcu_dereference` 保證你拿到的是一致的指標。這是把前面「path walk 用 RCU」的道理，用在 fd table 上的一個具體例子。

## 對比與取捨

| 物件 | 一句話 | 多對一關係 | 生命週期 | 對應 syscall/使用者現象 |
|---|---|---|---|---|
| `super_block` | 一次 mount 的檔案系統實例 | 多個 inode → 一個 sb | mount 建、umount 毀 | `mount`/`df`/`statfs` |
| `inode` | 一個檔案的元資料 | 多個 dentry/file → 一個 inode | 檔案存在期間（icache 快取） | `stat`/`chmod`；`ls -i` 的 inode number |
| `dentry` | 檔名 → inode 的對應 | 多個 dentry → 一個 inode（hard link） | dcache 快取，reclaim 可回收 | 路徑查找；`ln`（hard link） |
| `file` | 一個開啟的檔案實例 | 一個 fd → 一個 file | `open` 建、最後一次 `close` 毀 | `open`/`read`/`lseek`；fd |

**替代設計對比**：

| 設計 | 優點 | 缺點 |
|---|---|---|
| VFS（四物件 + ops 表） | 一組 syscall 通吃所有 fs；新增 fs 只要填 ops | 抽象層有間接呼叫成本；學習曲線陡 |
| 每種 fs 各自 syscall（假想） | 沒有抽象開銷 | pipeline 跨 fs 無法寫；`cat` 得懂每種 fs；無法擴充 |
| 把 dentry 併進 inode（省一層） | struct 少一個 | 無法表達 hard link；路徑查找沒有獨立快取單元 |
| 把 file 併進 inode（省一層） | struct 少一個 | 同檔案多次 open 無法各有 offset |

## 踩雷集錦

1. **「inode 裡有檔名」——錯。** inode 沒有名字欄位。檔名在 dentry。一個 inode 可對應多個檔名（hard link），這在 kernel 裡就是「多個 dentry 的 `d_inode` 指向同一 inode」。`stat` 顯示的一切（權限、大小、時間）在 inode，但你打的那個「名字」不在。

2. **「一次 open 就是一個 inode」——錯。** 一次 `open` 產生一個 **`struct file`**（自己的 offset），不是 inode。同一檔案 open 十次是十個 file、一個 inode。把 file 和 inode 搞混，會完全誤解 offset 為什麼是 per-open 的。

3. **在 v6.12 直接寫 `inode->i_ctime` 編不過。** 時間欄位已拆成 `i_ctime_sec`/`i_ctime_nsec` 並要求用 accessor。用 `inode_get_ctime()`/`inode_set_ctime_current()`。抄舊教學/舊 driver 程式碼時最容易中這個。

4. **模組裡直接讀 `fdt->fd[]` 不進 RCU read-side——race。** fd table 是 RCU 保護的，並行的 `dup`/`close`/`open` 可能正在改它。一定要 `rcu_read_lock()` + `rcu_dereference`。這也是為什麼上面的模組那樣寫。

5. **以為 `d_path()` 回傳的字串從 buf 開頭起。** `d_path` 是**從 buf 末端往前**填的（路徑鏈從葉往根拼比較自然），回傳指標指向字串真正的起點，可能在 buf 中間。用回傳值，別直接印 `buf`。

6. **negative dentry「浪費記憶體」——不是 bug。** `d_inode == NULL` 的 dentry 是快取「這名字不存在」的優化，不是洩漏。它們也受 reclaim 管，記憶體吃緊會被回收。

## 進階：再往深一層

- **`open()` 全流程接點**：`do_sys_open` → `do_filp_open`（`fs/namei.c`，內含 path walk）→ `path_openat` → 建 `struct file`（`alloc_empty_file`）→ 把 dentry 的 inode 的 `i_fop` 拷進 `f_op` → `fd_install` 把 file 放進 fd table。這條鏈把本章四物件全串起來了，值得在 gdb 裡 `b do_filp_open` 走一次。

- **`struct file` 是引用計數的（`f_count`）**：`dup`、`fork`（共享 fd table）會讓多個 fd 指向同一 file、增加 refcount；最後一個 close 到 0 才呼叫 `f_op->release` 真正關檔。這解釋了 `linux_commands` 裡「`dup` 出來的 fd 共享 offset」——因為共享同一個 file，同一個 `f_pos`。

- **面試常問**：「hard link 和 symlink 在 kernel 裡差在哪？」答：hard link 是**兩個 dentry 指同一 inode**（同一份檔案，`i_nlink` 增加，無獨立 inode）；symlink 是**一個獨立 inode，內容是目標路徑字串**，walk 時由 `i_op->get_link` 展開、遞迴 walk。前者不能跨檔案系統（inode number 只在單一 sb 內唯一），後者可以。

- **`mount` 命名空間（Ch 49）**：`f_path` 裡的 `vfsmount` 屬於某個 mount namespace。容器（`docker` 課）能讓不同 process 對同一路徑字串走到不同 super_block，靠的就是 per-namespace 的 mount 樹 + `f_path.mnt`。

## 動手練習

1. **在 gdb 裡走一次 path walk**：Ch 0 環境開機到 shell，`b link_path_walk`，在 QEMU 裡 `cat /etc/hostname`，停下後 `bt` 看是誰呼叫進來（應該從 `do_filp_open` 一路下來）。`p nd->last.name` 看目前 walk 到哪一段名字。用 `finish`/`continue` 觀察它一段段推進。

2. **證明 hard link 共享 inode**：`echo hi > a; ln a b; stat -c '%i %h' a b`——兩個檔名印出同一個 inode number、link 數都是 2。再 `rm a; stat -c '%i %h' b`——inode 不變，link 數變 1。你剛觀察到「dentry 消失但 inode 還在（因為還有一個 dentry 指它）」。

3. **證明多次 open 各有 offset**：寫一個小 C 程式，對同一檔案 `open` 兩次得到 fd1、fd2，在 fd1 上 `read` 幾個 byte，然後 `lseek(fd1, 0, SEEK_CUR)` 和 `lseek(fd2, 0, SEEK_CUR)` 各印一次 offset——fd1 前進了、fd2 還在 0。這證明 offset 在 `struct file` 不在 inode。

4. **擴充 list_fds 模組**：加印每個 file 的 `f_flags`（判斷是 `O_RDONLY`/`O_WRONLY`/`O_RDWR`）和 `f_op` 指標值。對照 ext4 檔案和 `/dev/pts` 的 `f_op` 指標是否不同——不同就證明了多型（不同 fs/裝置填了不同 ops 表）。

5. **觀察 dcache 效應**：`echo 2 > /proc/sys/vm/drop_caches` 後對一個深路徑 `find /usr -name something | head` 計時，馬上再跑一次計時。第二次快很多——差的就是 dcache 命中。

## 本章重點整理

- VFS 用四個物件描述檔案存取：`super_block`（一次 mount 的 fs 實例）、`inode`（檔案元資料，**不含檔名**）、`dentry`（檔名→inode，dcache 的單元）、`file`（一次 open 的實例，帶 `f_pos` offset）。它們的切分由三種「多對一」關係決定（hard link、多次 open、同 fs 的多個檔案）。
- 關係鏈是 `fd → struct file → dentry → inode → super_block`；file 同時指 dentry 和 inode，多個 file 可共享一個 inode，多個 dentry 可共享一個 inode。
- VFS 的多型靠**ops 表函式指標**（`s_op`/`i_op`/`i_fop`/`f_op`）——C 手工做的 vtable。`file->f_op->read_iter` 分派到 ext4 或 tmpfs 的實作，這是整個 VFS 與 driver 模型的核心機制。
- 路徑查找在 `fs/namei.c`（`path_lookupat`/`link_path_walk`），先查 dcache（`lookup_fast`），miss 才問底層 fs 的 `i_op->lookup`；用 RCU-walk 無鎖飛過、失敗才 `try_to_unlazy()` 降級 ref-walk——是 RCU 在 kernel 最熱的應用之一。

## 自我檢核

- [ ] 不看筆記，能畫出 `fd → file → dentry → inode → super_block` 的圖，並說出每條箭頭的多對一關係
- [ ] 能解釋「為什麼檔名放在 dentry 不放在 inode」「為什麼 offset 放在 file 不放在 inode」，各舉一個會壞掉的情境
- [ ] 面試被問「VFS 怎麼讓一組 syscall 通吃所有檔案系統」，能用「ops 表 = C 版 vtable + mount/open 時填表」講清楚
- [ ] 能說出 path walk 的 dcache 命中/未命中兩條路，以及 RCU-walk 為什麼比 ref-walk 快、什麼時候降級
- [ ] 能寫出用 `current->files` 遍歷 fd table 印路徑的模組，並說明為什麼要進 RCU read-side、`d_path` 為什麼要用回傳值

## 延伸閱讀

### 官方文件

- **[Documentation/filesystems/vfs.rst](https://www.kernel.org/doc/html/latest/filesystems/vfs.html)** — kernel 官方 VFS 文件
  - **讀哪裡**：「The Directory Cache」「The Inode Object」「The File Object」「Registering and Mounting a Filesystem」四節
  - **和本章的關聯**：本章四物件與 ops 表的**權威定義來源**，每個 method 的語意與呼叫時機都在這；Ch 35 寫自製 fs 前必讀

- **[Documentation/filesystems/path-lookup.rst](https://www.kernel.org/doc/html/latest/filesystems/path-lookup.html)** — path walk 深入
  - **讀哪裡**：整篇，尤其 RCU-walk vs ref-walk 那幾節。作者把 `fs/namei.c` 那套（多數人覺得最難懂的 kernel 程式碼之一）講得極清楚
  - **前提**：讀完本課 Ch 27（RCU）、Ch 28（seqlock）再看，RCU-walk 的機制才吃得下

### 文章

- **[LWN: "RCU-walk: faster pathname lookup in Linux"](https://lwn.net/Articles/419811/)** — Neil Brown
  - **這是什麼**：RCU-walk 設計者級別的解說，講清楚「為什麼 path walk 要無鎖」「降級時發生什麼」
  - **為什麼值得讀**：本章 RCU-walk 一節的加深版；理解 kernel 怎麼把 RCU 用到極致的最佳案例

### 書籍

- **《Understanding the Linux Kernel, 3rd Ed.》** — Bovet & Cesati，第 12 章「The Virtual Filesystem」
  - **這本的定位**：VFS 四物件與 ops 表的**架構骨架**講得最完整；雖然是 2.6 kernel，四物件的設計至今未變（欄位名有增減，以 v6.12 源碼為準）
  - **注意**：書裡的 `i_ctime` 等時間欄位、`d_lookup` 細節在 6.12 已變，讀架構、對源碼看細節

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 13 章「The Virtual Filesystem」
  - **讀哪裡**：四物件與 ops 表那幾節，最白話的入門，比 UTLK 好讀
  - **和本章互補**：本章偏源碼與 v6.12 細節，Love 偏直覺與大方向，兩者搭配

四大物件與它們的關係鏈就位了。下一章我們挑一條最具體的鏈——`read()`——從 syscall 入口一路走到底層儲存，看 `file->f_op->read_iter` 之後 page cache（Ch 21）怎麼命中、miss 時怎麼觸發 block layer 讀磁碟，把這章的靜態結構變成一條會動的執行流程。

→ [Ch 34 一次 read() 的完整路徑](./34-read-path.md)
