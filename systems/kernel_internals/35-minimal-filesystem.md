# Ch 35 — 寫一個最小 in-memory filesystem

> **目標**：親手寫一個能 `mount`、能 `touch`/`mkdir`/`echo`/`cat`/`ls` 的最小記憶體檔案系統（一個能編譯載入的核心模組），從實作中真正搞懂 Ch 33 的四大物件是「誰在什麼時候把它們填出來」、Ch 34 那些你一直在呼叫的 ops（`read_iter`/`lookup`/`fill_super`）是「怎麼被實作、怎麼被接上」。學完你有一個能在 QEMU 裡跑的檔案系統，並能用 ftrace 看見自己的 `read_iter` 被 kernel 呼叫。

前兩章你站在**呼叫方**：`vfs_read` 呼叫 `file->f_op->read_iter`，path walk 呼叫 `dir_inode->i_op->lookup`。你看得懂那些函式指標怎麼被分派，但沒填過那張表。這章把角色翻過來——**你來當被呼叫的那一方**，實作 `fill_super`、配 inode、填 ops 表，讓 VFS 反過來呼叫你的程式碼。這是把 VFS 從「讀懂」變成「會做」的一章，程式碼佔比高。

## 為什麼從記憶體檔案系統開始？

一個真實的磁碟檔案系統（ext4）要處理的東西是這樣的：on-disk 的 superblock/inode/目錄格式怎麼佈局、block 怎麼配置與回收、extent tree 怎麼把「檔案第 N 塊」映射到「磁碟第 M 個磁區」、crash 之後怎麼靠 journaling 不壞掉、bio 怎麼下到 block layer（Ch 36）。這些**大半和 VFS 介面本身無關**，是「怎麼把位元組可靠地放到旋轉的鐵盤上」的問題。

如果你第一次寫檔案系統就挑 ext4 那種，你會淹沒在持久化與 block 配置的細節裡，反而看不清 VFS 這層抽象。所以我們反過來：**寫一個資料只活在 RAM、關機就沒的檔案系統**。這樣一來：

- **不碰 block layer**：檔案內容直接放進 page cache 的 folio，`read_folio` 只要「把頁清零」而不是「發 bio 讀磁碟」——Ch 34 慢路徑那一整段對記憶體 fs 根本不存在。
- **不管持久化**：沒有 on-disk 格式要設計，沒有 mount 時要從磁碟讀 superblock，沒有 fsync 要落盤。
- **可以大量複用 kernel 現成的 helper**：`fs/libfs.c` 有一整套 `simple_*` 函式，專門給這種「元資料只在記憶體、不落磁碟」的檔案系統用。你能把注意力全放在「VFS 物件怎麼被建、ops 表怎麼接」上。

這正是 kernel 自己的 **ramfs**（`fs/ramfs/`）在做的事——ramfs 是「page cache 直接當檔案系統」的極簡實作，tmpfs（`mm/shmem.c`）是它加上 swap 支援與大小限制的產品級版本。我們這章寫的 `myfs`，本質是把 ramfs 讀懂、再自己寫一遍，去掉能去掉的、留下骨架。**讀完 ramfs 的 220 行，你就懂了 Linux 最小的完整檔案系統長什麼樣**，這章就是帶你走這條路。

## 先建立直覺：一個 fs 模組怎麼掛進 VFS

寫檔案系統模組，核心是回答一個問題：**從 `insmod` 到使用者 `cat` 一個檔案，VFS 在什麼時間點、呼叫你的哪個函式、要你填哪個物件？** 先把這條時間線畫出來，後面每段程式碼都對得上：

```
   insmod myfs.ko
        │
        ▼
   ① register_filesystem(&myfs_fs_type)      ← 模組 init 時做一次
        把「有一種 fs 叫 myfs」登記進 kernel 的 file_systems 全域鏈
        （之後 `mount -t myfs` 才找得到你）
        │
   ─────┼──────  使用者敲：mount -t myfs none /mnt/myfs
        │
        ▼
   ② VFS 依 -t myfs 找到你的 myfs_fs_type
        呼叫 fs_type->init_fs_context(fc)      ← 現代 mount API（fs_context）
        你設好 fc->ops，指向自己的 get_tree 回呼
        │
        ▼
   ③ VFS 呼叫 fc->ops->get_tree
        你呼叫 get_tree_nodev(fc, myfs_fill_super)
        VFS 配一個 struct super_block，回頭呼叫你的：
        │
        ▼
   ④ myfs_fill_super(sb, fc)                  ← 這是整個 fs 的核心
        - 設 sb->s_op   = &myfs_super_ops       （super 層 ops 表）
        - 設 sb->s_magic / s_blocksize / s_maxbytes …
        - 建「根目錄的 inode」（一個 S_IFDIR 的 inode）
        - 用 d_make_root() 把根 inode 包成 sb->s_root（根 dentry）
        │
        ▼   掛載完成，/mnt/myfs 現在是你的 fs 的根
        │
   ─────┼──────  使用者敲：touch /mnt/myfs/hello
        │
        ▼
   ⑤ VFS path walk 到 /mnt/myfs（你的根 dentry）
        呼叫 根inode->i_op->create        ← 你填的 create 回呼
        你配一個「一般檔案 inode」（S_IFREG），d_instantiate 把它接上 dentry
        │
        ▼
   ─────┼──────  使用者敲：echo hi > /mnt/myfs/hello ; cat /mnt/myfs/hello
        │
        ▼
   ⑥ open → read/write 走 file->f_op（=你檔案 inode 的 i_fop）
        write_iter → 資料進 page cache 的 folio（用 ram_aops）
        read_iter  → 從 folio copy_to_user（Ch 34 的快路徑，但永遠命中）
```

三個要記住的錨點：

1. **`register_filesystem` 只是「登記型別」**，不建任何檔案。真正建東西是在 mount 時的 `fill_super`。
2. **`fill_super` 是整個檔案系統的地基**——它建 super_block、設 ops、造出「根 inode + 根 dentry」。掛載成功與否全看它。這是這章你要寫得最仔細的函式。
3. **建檔案（`create`）、建目錄（`mkdir`）發生在使用者操作時**，由你填在「目錄 inode 的 `i_op`」裡的回呼被 VFS 呼叫。目錄 inode 的 ops 和一般檔案 inode 的 ops **不一樣**——這是新手最容易漏的分岔。

## Step 1：註冊檔案系統型別（`file_system_type`）

一切從 `struct file_system_type`（`include/linux/fs.h`）開始。它描述「有這麼一種檔案系統」，關鍵是告訴 VFS「要 mount 我時，走哪個 mount 回呼」。

v6.12（以及所有現代 kernel）的 mount 有兩套 API，這裡要講清楚，因為抄舊教學最容易在這裡踩雷：

- **舊 API**：`file_system_type` 填 `.mount`，指向一個回傳 `struct dentry *` 的函式（如 `mount_nodev`）。這條路在 v6.12 仍能編，但已是 legacy。
- **現代 API（fs_context，本章用這個）**：填 `.init_fs_context`，指向一個初始化 `struct fs_context` 的函式。mount 的參數解析、super_block 的建立都透過 `fs_context` 這個「掛載中的狀態物件」進行。ramfs 在 v6.12 就是走這條（`fs/ramfs/inode.c` 的 `ramfs_init_fs_context`）。新寫的檔案系統應該用這套。

```c
static struct file_system_type myfs_fs_type = {
    .owner          = THIS_MODULE,
    .name           = "myfs",              // mount -t myfs 的那個 "myfs"
    .init_fs_context = myfs_init_fs_context, // 現代 mount 入口（下一步實作）
    .kill_sb        = kill_litter_super,   // umount 時清理：libfs 提供的通用版
    .fs_flags       = FS_USERNS_MOUNT,     // 允許在 user namespace 裡掛（ramfs 也設這個）
};
```

- `.name`：`mount -t <name>` 用來找你的字串。
- `.init_fs_context`：現代 mount 的入口。VFS 收到 `mount -t myfs` 後呼叫它。
- `.kill_sb`：umount（最後一個引用消失）時 VFS 呼叫它清理 super_block。我們直接用 libfs 的 **`kill_litter_super`**（`fs/libfs.c`）——它是給「純記憶體、dentry 樹要一併拆掉」的 fs 用的通用清理函式，會把掛在 super_block 底下的 dentry 樹釋放乾淨。記憶體 fs 用它就對了，不用自己寫。
- `FS_USERNS_MOUNT`：讓非 root 也能在自己的 user namespace 裡掛（和 `docker` 課的 namespace 相關）；不設也能用 root 掛，這裡跟 ramfs 一致設上。

模組的 init/exit 就是「登記」與「撤銷登記」這個型別：

```c
static int __init myfs_init(void)
{
    return register_filesystem(&myfs_fs_type);   // 登記進 kernel 的 file_systems 鏈
}

static void __exit myfs_exit(void)
{
    unregister_filesystem(&myfs_fs_type);        // 撤銷（前提：沒有實例還掛著）
}
```

> **踩雷預告**：`unregister_filesystem` 若在還有 myfs 實例掛著時被呼叫（模組被 `rmmod`），會失敗或造成問題。實務上，只要還有 mount 用著這個 fs，模組的引用計數（`.owner = THIS_MODULE`）就不為零，`rmmod` 會被擋下（回 `Resource busy`）。所以卸模組前要先 `umount` 所有 myfs 掛載點——這是 `.owner` 這欄在替你把關。

## Step 2：mount 回呼——`fs_context` 三步接力

現代 mount 的 `fs_context` API 看起來繞，但骨架很固定，記憶體 fs 幾乎是照抄 ramfs。三步：

**第一步，`init_fs_context`**：VFS 給你一個空的 `struct fs_context *fc`，你把 `fc->ops` 設成自己的 `fs_context_operations`：

```c
static int myfs_init_fs_context(struct fs_context *fc)
{
    fc->ops = &myfs_context_ops;    // 告訴 VFS：後續的 get_tree 等回呼在這張表
    return 0;
}
```

**第二步，`fs_context_operations`**：這張表裡最關鍵的是 `get_tree`——它負責「生出這次 mount 的 super_block（連同根 dentry）」：

```c
static const struct fs_context_operations myfs_context_ops = {
    .get_tree = myfs_get_tree,
    // 有需要解析 mount 參數（-o size=... 之類）才填 .parse_param；
    // 我們的最小 fs 不吃參數，省略。
};

static int myfs_get_tree(struct fs_context *fc)
{
    /* get_tree_nodev：給「沒有背後 block device 的 fs」用的通用 get_tree。
     * 它替我們配一個 super_block，然後回頭呼叫我們的 fill_super。
     * （磁碟 fs 會改用 get_tree_bdev，那會去開一個 block device——我們不需要。） */
    return get_tree_nodev(fc, myfs_fill_super);
}
```

`get_tree_nodev`（`fs/super.c`）就是「記憶體 fs 專用」的關鍵——它跳過「開啟並讀取一個 block device」那整段（那是磁碟 fs 的事），直接配一個 anonymous super_block，把控制權交回你的 `fill_super`。**這一個函式，就是「不碰 block layer」在源碼層的體現**。

**第三步，`fill_super`**：見下一節，它是主戲。

> 為什麼要三步這麼繞？因為現代 mount 把「解析參數」「重掛（remount）」「建 super」拆成 `fs_context` 上的獨立階段，好支援 `fsopen`/`fsconfig`/`fsmount` 這組新 syscall（更細緻、可分步驟的掛載）。對我們的最小 fs 用不上這些彈性，但 API 形狀是固定的，照抄即可。舊的一步式 `.mount` 回呼（`mount_nodev`）語意上等價，只是把三步壓成一步。

## Step 3：`fill_super`——建 super_block、設 ops、造根 inode

這是整個檔案系統的地基。VFS 已經替你配好一個空的 `super_block`，`fill_super` 要把它填成「可用的檔案系統根」。三件事：填 super_block 的欄位、設 `s_op`、建「根目錄 inode + 根 dentry」。

```c
#define MYFS_MAGIC 0x6d796673   /* "myfs" 的 ASCII，當 s_magic 用 */

static const struct super_operations myfs_super_ops = {
    .statfs      = simple_statfs,        // 支援 statfs()/df：libfs 通用版
    .drop_inode  = generic_delete_inode, // inode 最後引用消失就直接刪（記憶體 fs 不需保留）
    // 沒有 write_inode——我們不落磁碟，inode 不需寫回
};

static int myfs_fill_super(struct super_block *sb, struct fs_context *fc)
{
    struct inode *root_inode;

    /* ① 填 super_block 的基本欄位 */
    sb->s_maxbytes      = MAX_LFS_FILESIZE;  // 單檔上限：受記憶體限制，設成型別上限即可
    sb->s_blocksize     = PAGE_SIZE;         // 記憶體 fs 以「頁」為 block
    sb->s_blocksize_bits = PAGE_SHIFT;
    sb->s_magic         = MYFS_MAGIC;        // 給 statfs 回報用的 magic number
    sb->s_op            = &myfs_super_ops;   // ★ 掛上 super 層 ops 表（多型入口）
    sb->s_time_gran     = 1;                 // 時間戳粒度（奈秒）

    /* ② 建根目錄的 inode（一個 S_IFDIR 型別的 inode） */
    root_inode = myfs_make_inode(sb, NULL, S_IFDIR | 0755, 0);
    if (!root_inode)
        return -ENOMEM;

    /* ③ 把根 inode 包成根 dentry，掛到 sb->s_root。
     * d_make_root 內建：失敗時會 iput 掉 root_inode（不用自己清） */
    sb->s_root = d_make_root(root_inode);
    if (!sb->s_root)
        return -ENOMEM;   // d_make_root 已經幫我們 iput root_inode 了

    return 0;
}
```

逐點看設計意圖：

- **`s_blocksize = PAGE_SIZE`、`s_blocksize_bits = PAGE_SHIFT`**：磁碟 fs 的 block 大小是磁碟格式決定的（ext4 常見 4KB），我們沒有磁碟，「block」對記憶體 fs 就是「頁」。這也讓 `df` 顯示的數字是以頁為單位算的。
- **`s_op = &myfs_super_ops`**：這一行就是 Ch 33 說的「mount 時把 ops 表填進 super_block」——之後 `df` 打進來，VFS 走 `sb->s_op->statfs`，分派到我們填的 `simple_statfs`。
- **`d_make_root`**（`fs/dcache.c`）：這是根目錄專用的 helper，把一個 inode 包成 super_block 的根 dentry（`s_root`）。根 dentry 特殊（沒有父 dentry、`d_parent` 指自己），所以有專用函式。**失敗時它會自動 `iput` 掉你傳進去的 inode**，所以 `③` 失敗直接回 `-ENOMEM`，不用自己清 `root_inode`（自己再 iput 會 double-free）。這是一個容易寫錯的資源管理細節。

`fill_super` 回傳 0，掛載就成功了——`/mnt/myfs` 現在是一個空目錄（只有根）。接下來的檔案/目錄，靠使用者操作觸發。

## Step 4：inode 工廠——區分目錄與一般檔案

`myfs_make_inode` 是這個 fs 被呼叫最頻繁的函式：建根目錄要它、`create` 建檔要它、`mkdir` 建子目錄也要它。它的核心決策是——**依 `i_mode` 的型別，填不同的 ops 表**。這正是 Ch 33 「目錄 inode 和一般檔案 inode 的 `i_op`/`i_fop` 不同」的實作現場。

```c
static const struct inode_operations myfs_dir_inode_operations;  // 前向宣告（下一步定義）

static struct inode *myfs_make_inode(struct super_block *sb,
                                     const struct inode *dir,
                                     umode_t mode, dev_t dev)
{
    struct inode *inode = new_inode(sb);   // 從 sb 配一個新 inode（配號、掛上 icache）
    if (!inode)
        return NULL;

    inode->i_ino = get_next_ino();         // 給一個 inode number（libfs 的簡單遞增計數）
    inode_init_owner(&nop_mnt_idmap, inode, dir, mode);  // 設 uid/gid（承襲父目錄）+ i_mode

    /* v6.12：時間欄位改用 accessor，直接寫 inode->i_atime 編不過（Ch 33 陷阱） */
    simple_inode_init_ts(inode);           // 把 a/m/c time 一次設成 current time

    switch (mode & S_IFMT) {               // ★ 依「檔案型別」分派不同 ops
    case S_IFDIR:
        /* 目錄：i_op 提供 lookup/create/mkdir…；i_fop 提供 readdir（getdents）*/
        inode->i_op  = &myfs_dir_inode_operations;
        inode->i_fop = &simple_dir_operations;   // libfs 通用「讀目錄」實作
        inc_nlink(inode);   // 目錄自己的 "." 讓 link 數從 1 變 2（目錄至少 nlink=2）
        break;

    case S_IFREG:
        /* 一般檔案：內容用 page cache，讀寫走 generic_file_*，設好 aops */
        inode->i_op  = &myfs_file_inode_operations;  // getattr/setattr（下一步）
        inode->i_fop = &myfs_file_operations;        // read_iter/write_iter/mmap（下一步）
        inode->i_mapping->a_ops = &ram_aops;         // ★ page cache 的 aops（見 Step 6）
        break;

    default:
        /* 其他型別（裝置節點、fifo、socket）交給 VFS 通用初始化 */
        init_special_inode(inode, mode, dev);
        break;
    }
    return inode;
}
```

這個函式把 Ch 33 的抽象變成了三行具體決策，值得停下來對照：

- **`new_inode(sb)`**（`fs/inode.c`）：配一個 inode 並掛進這個 super_block 的 inode 清單。記憶體 fs 用它就夠——我們不需要自訂 `alloc_inode`（那是磁碟 fs 為了在 inode 後面附掛自己的 on-disk 資訊才做的，ramfs 也沒做）。
- **`inode_init_owner(&nop_mnt_idmap, inode, dir, mode)`**：設定 `i_uid`/`i_gid`（一般承襲父目錄 `dir`）與 `i_mode`。第一個參數 `nop_mnt_idmap` 是 v6.12 的 idmapped-mount 支援（普通掛載用 no-op 版即可，ramfs 也這樣傳）。
- **`switch (mode & S_IFMT)` 就是那個關鍵分岔**：
  - **目錄**（`S_IFDIR`）：`i_op` 掛 `myfs_dir_inode_operations`（有 `lookup`/`create`/`mkdir`），`i_fop` 掛 `simple_dir_operations`（libfs 提供的「列目錄」實作，`ls` 走它）。
  - **一般檔案**（`S_IFREG`）：`i_op` 掛只有 `getattr`/`setattr` 的表（一般檔案不需要 `lookup`——你不能在檔案「裡面」查名字），`i_fop` 掛有 `read_iter`/`write_iter` 的表，並把 `i_mapping->a_ops` 設成 `ram_aops`（決定內容怎麼進出 page cache）。
- **`inc_nlink`（目錄）**：目錄的 link 數至少是 2（自己的名字 + 內部的 `.`）。這是 `ls -l` 目錄看到 link 數 ≥2 的由來（`linux_commands` 課的現象，這裡是它的 kernel 實作）。

**這個 switch 就是為什麼 Ch 33 要把 `i_op`/`i_fop` 拆成兩張表**：inode 的型別決定了它能做什麼操作，而「決定」這件事就發生在你建 inode 的這一刻。

## Step 5：目錄操作——`create` / `mkdir` / `lookup`

使用者 `touch`、`mkdir`、path walk 時，VFS 呼叫的是「目錄 inode」的 `i_op`。這張表大部分能直接借用 libfs 的 `simple_*`，只有「建立東西」的 `create`/`mkdir`/`mknod` 要自己寫（因為要用我們的 inode 工廠）。

```c
/* 共用底：配一個 inode 並把它接到 dentry 上（d_instantiate） */
static int myfs_mknod(struct mnt_idmap *idmap, struct inode *dir,
                      struct dentry *dentry, umode_t mode, dev_t dev)
{
    struct inode *inode = myfs_make_inode(dir->i_sb, dir, mode, dev);
    if (!inode)
        return -ENOSPC;         // 記憶體不夠 → 沒空間（No space left）

    d_instantiate(dentry, inode);  // ★ 把新 inode 綁到這個 dentry（名字 → inode 生效）
    dget(dentry);                  // dcache 對這個 dentry 多持一個引用（記憶體 fs 慣例，讓它常駐）
    inode_set_mtime_to_ts(dir, inode_set_ctime_current(dir));  // 更新父目錄時間戳
    return 0;
}

/* touch / open(O_CREAT) 建一般檔案 → VFS 呼叫 dir_inode->i_op->create */
static int myfs_create(struct mnt_idmap *idmap, struct inode *dir,
                       struct dentry *dentry, umode_t mode, bool excl)
{
    return myfs_mknod(idmap, dir, dentry, mode | S_IFREG, 0);
}

/* mkdir → VFS 呼叫 dir_inode->i_op->mkdir */
static int myfs_mkdir(struct mnt_idmap *idmap, struct inode *dir,
                      struct dentry *dentry, umode_t mode)
{
    int ret = myfs_mknod(idmap, dir, dentry, mode | S_IFDIR, 0);
    if (!ret)
        inc_nlink(dir);   // 新子目錄的 ".." 指向父目錄 → 父目錄 link 數 +1
    return ret;
}

static const struct inode_operations myfs_dir_inode_operations = {
    .create  = myfs_create,
    .lookup  = simple_lookup,     // ★ 路徑查找：libfs 通用版（見下方說明）
    .link    = simple_link,       // hard link
    .unlink  = simple_unlink,     // rm 一般檔案
    .mkdir   = myfs_mkdir,
    .rmdir   = simple_rmdir,      // rmdir 空目錄
    .mknod   = myfs_mknod,        // mknod（建裝置節點/fifo）
    .rename  = simple_rename,     // mv
};
```

幾個要看懂的點：

- **`d_instantiate`（`fs/dcache.c`）是「把名字接到 inode」的動作**。path walk 走到 `hello` 這個名字時，VFS 先建一個 negative dentry（`d_inode == NULL`，Ch 33）；你的 `create` 配好 inode 後，`d_instantiate` 把 inode 綁上去，negative dentry 就變成正常 dentry。**這一步就是 Ch 33 「dentry 的 `d_inode` 指向 inode」那條線被建立的瞬間。**
- **`simple_lookup`（`fs/libfs.c`）看起來反直覺**：對一個「元資料只在記憶體」的 fs，`lookup` 幾乎不做事——因為所有存在過的檔案，其 dentry 都還在 dcache 裡（我們 `dget` 讓它常駐）。查一個不存在的名字時，`simple_lookup` 就回一個 negative dentry。這和磁碟 fs 的 `lookup` 天差地別：ext4 的 `lookup` 要去讀磁碟目錄 block 找名字（Ch 33 path walk 的 `lookup_slow` 那條慢路徑），而記憶體 fs 的「磁碟」就是 dcache 本身，`lookup` 沒有「更底層」可問。**這是「記憶體 fs 不碰 block layer」在 lookup 這條路上的體現**。
- **`create` 為什麼把 `mode | S_IFREG` 傳下去？** VFS 呼叫 `create` 時 `mode` 只帶權限位（如 0644），沒帶型別位。是我們決定「create 建的是一般檔案」，所以 OR 上 `S_IFREG`；`mkdir` 則 OR 上 `S_IFDIR`。這個型別位一路傳到 `myfs_make_inode` 的那個 switch，決定填哪張 ops 表。
- **`mkdir` 為什麼要 `inc_nlink(dir)`？** 新建的子目錄裡有個 `..` 指回父目錄，這讓父目錄的 link 數 +1。這就是為什麼「一個目錄底下每多一個子目錄，`ls -ld` 看到的 link 數就 +1」——每個子目錄的 `..` 都算父目錄的一個 hard link。

## Step 6：一般檔案的讀寫——直接借 page cache

到這裡，掛載能成、能建檔建目錄、`ls` 能列出來了。剩最後一塊：檔案內容的讀寫。這是 Ch 34 一整章的主題，但對記憶體 fs，我們幾乎不用自己寫——**因為 kernel 的 `generic_file_*` 一族函式，配上一組「不落磁碟」的 `address_space_operations`，就是完整的記憶體檔案讀寫**。

```c
static const struct file_operations myfs_file_operations = {
    .read_iter   = generic_file_read_iter,   // ★ Ch 34 的 read 路徑，直接借用
    .write_iter  = generic_file_write_iter,  // ★ write 路徑，直接借用
    .mmap        = generic_file_mmap,        // mmap 也走 page cache（Ch 34 的 mmap 對比節）
    .fsync       = noop_fsync,               // 記憶體 fs 沒磁碟可 sync → 空操作
    .splice_read = filemap_splice_read,
    .llseek      = generic_file_llseek,      // lseek 改 f_pos
};

static const struct inode_operations myfs_file_inode_operations = {
    .setattr = simple_setattr,   // chmod/chown/truncate 的元資料改動
    .getattr = simple_getattr,   // stat 走這
};
```

`file_operations` 這張表，欄位全是 `generic_file_*`——**你在 Ch 34 追的那條 `vfs_read → generic_file_read_iter → filemap_read` 路徑，現在被你原封不動接上了**。差別只在最底層：`filemap_read` miss 時會呼叫 `a_ops->read_folio` 去「拿資料」。磁碟 fs 的 `read_folio` 發 bio 讀磁碟；我們的 `read_folio` 呢？

答案是那個 `i_mapping->a_ops = &ram_aops` —— **`ram_aops`（`fs/libfs.c`，v6.12 有 `EXPORT_SYMBOL`）就是「page cache 直接當儲存」的 aops**。它的關鍵成員：

- **`read_folio`**：對記憶體 fs，一個「還沒被寫過」的頁，內容就是全零。所以 `ram_aops` 的 read_folio 只是**把 folio 清零、標成 uptodate**——不發任何 I/O。這就是 Ch 34 慢路徑那一整段（readahead、bio、睡眠、中斷喚醒）在記憶體 fs 裡**整個消失**的原因：沒有「更底層」要去讀，缺的頁清零就是它的正確內容。
- **`write_begin` / `write_end`**：`write_iter` 寫資料前後的鉤子，負責在 page cache 裡準備好 folio、寫完標 dirty。因為沒有 writeback 目標，這些 dirty 頁就一直留在記憶體——這正是「檔案內容存活在 RAM」的實作：**資料就是 page cache 的 folio，page cache 就是這個檔案系統的儲存**。

用 `ram_aops` 這一個 export 出來的 struct，我們就白拿了 ramfs 的檔案讀寫。這在 v6.12 之所以可行，是因為 kernel 把這組「in-memory aops」抽出來放進 libfs 並 export（早期版本 `ram_aops` 在 ramfs 內部、模組拿不到，得自己拼 `simple_read_folio` + `simple_write_begin` + `simple_write_end`）。

> **這一步是整章「複用 helper」哲學的高潮**：一個能讀寫檔案的檔案系統，file_operations 全是 `generic_file_*`、aops 是現成的 `ram_aops`——你一行讀寫邏輯都沒寫，卻有完整的 buffered read/write/mmap，還自動享有 Ch 21 的 page cache、Ch 34 的 readahead 框架（雖然對記憶體 fs readahead 沒實際 I/O）。**VFS 抽象的價值，在你這裡省掉的這幾百行裡看得最清楚。**

## 底層機制：把六步串成一張掛載—操作全圖

把前面六步接起來，就是 `myfs` 從註冊到讀寫的完整資料結構圖。這張圖是這章的骨架，值得對著程式碼逐條確認：

```
  register_filesystem(&myfs_fs_type)
        │  登記型別，name="myfs"
        ▼
  ┌────────────────────── mount -t myfs none /mnt/myfs ──────────────────────┐
  │  init_fs_context → get_tree_nodev(fill_super)                            │
  │        │                                                                 │
  │        ▼   myfs_fill_super():                                            │
  │   ┌─────────────────────┐                                               │
  │   │ struct super_block  │  s_op ──► myfs_super_ops (.statfs=simple_statfs)│
  │   │ s_magic=MYFS_MAGIC  │  s_blocksize=PAGE_SIZE                         │
  │   │ s_root ──┐          │                                               │
  │   └──────────┼──────────┘                                               │
  │              ▼                                                           │
  │        根 dentry ("/")  ── d_inode ──► ┌──────────────────────────┐     │
  │       (d_make_root)                    │ 根 inode  S_IFDIR|0755    │     │
  │                                        │ i_op  ─► myfs_dir_inode_ops│    │
  │                                        │           (.create/.lookup/.mkdir)│
  │                                        │ i_fop ─► simple_dir_operations │  │
  │                                        └──────────────────────────┘     │
  └──────────────────────────────────────────────────────────────────────────┘
        │
        │  touch /mnt/myfs/hello  →  根inode->i_op->create = myfs_create
        ▼
  ┌──────────────────────────┐        d_instantiate         ┌──────────────┐
  │ dentry "hello"           │ ◄─────────────────────────── │ myfs_mknod   │
  │ d_inode ──┐              │                              └──────────────┘
  └───────────┼──────────────┘
              ▼
  ┌──────────────────────────────────┐
  │ 檔案 inode  S_IFREG|0644          │
  │ i_op  ─► myfs_file_inode_ops      │  (.getattr=simple_getattr …)
  │ i_fop ─► myfs_file_operations     │  (.read_iter=generic_file_read_iter …)
  │ i_mapping->a_ops ─► ram_aops      │  ★ 內容存活在 page cache 的 folio
  └──────────────────────────────────┘
              │
              │  echo hi > hello  →  write_iter → 資料進 folio（標 dirty，留在 RAM）
              │  cat hello        →  read_iter → filemap_read 命中 folio → copy_to_user
              ▼
        （沒有 bio、沒有磁碟、沒有 D 狀態睡眠——Ch 34 慢路徑對 myfs 不存在）
```

讀這張圖的三個重點：

1. **左半（mount 期）建的是「靜態骨架」**：super_block + 根 inode/dentry。右半（操作期）建的是「使用者的檔案」：每個 `touch`/`mkdir` 長出一組 dentry + inode。
2. **每個 inode 的 ops 表在它出生時就定好**（Step 4 的 switch），之後 VFS 的所有分派都循著這些指標走——這就是 Ch 33「ops 表 = C 版 vtable」的活教材，你親手填了三張表（super/dir-inode/file）。
3. **檔案內容那條線終點是 page cache，不是磁碟**。這是記憶體 fs 的定義性特徵，也是它比 ext4 少掉一整個 block layer 的原因。

## 動手：完整模組 + 掛載測試 + ftrace 觀測

### 完整模組程式碼

把前六步拼成一個可編譯的模組 `myfs.c`（v6.12）。這是這章的核心產出——它能在 Ch 0 的 QEMU 裡掛起來、做各種檔案操作。

```c
// myfs.c —— 最小 in-memory 檔案系統（可 mount/touch/mkdir/echo/cat/ls）
#include <linux/module.h>
#include <linux/fs.h>
#include <linux/fs_context.h>      // struct fs_context, fs_context_operations
#include <linux/pagemap.h>         // PAGE_SIZE, ram_aops 相關
#include <linux/dcache.h>
#include <linux/mount.h>

#define MYFS_MAGIC 0x6d796673      /* "myfs" */

/* ── 前向宣告：三張 ops 表互相引用 ── */
static const struct inode_operations myfs_dir_inode_operations;
static const struct inode_operations myfs_file_inode_operations;
static const struct file_operations  myfs_file_operations;

/* ── inode 工廠：依型別填不同 ops（Step 4）── */
static struct inode *myfs_make_inode(struct super_block *sb,
                                     const struct inode *dir,
                                     umode_t mode, dev_t dev)
{
    struct inode *inode = new_inode(sb);
    if (!inode)
        return NULL;

    inode->i_ino = get_next_ino();
    inode_init_owner(&nop_mnt_idmap, inode, dir, mode);
    simple_inode_init_ts(inode);          // v6.12：設 a/m/c time（用 accessor，Ch 33 陷阱）

    switch (mode & S_IFMT) {
    case S_IFDIR:
        inode->i_op  = &myfs_dir_inode_operations;
        inode->i_fop = &simple_dir_operations;
        inc_nlink(inode);                 // 目錄 nlink 至少 2（含 "."）
        break;
    case S_IFREG:
        inode->i_op  = &myfs_file_inode_operations;
        inode->i_fop = &myfs_file_operations;
        inode->i_mapping->a_ops = &ram_aops;   // page cache 當儲存
        break;
    default:
        init_special_inode(inode, mode, dev);
        break;
    }
    return inode;
}

/* ── 目錄操作：create/mkdir/mknod 自己寫，其餘借 libfs（Step 5）── */
static int myfs_mknod(struct mnt_idmap *idmap, struct inode *dir,
                      struct dentry *dentry, umode_t mode, dev_t dev)
{
    struct inode *inode = myfs_make_inode(dir->i_sb, dir, mode, dev);
    if (!inode)
        return -ENOSPC;
    d_instantiate(dentry, inode);
    dget(dentry);                         // 讓 dentry 常駐 dcache（記憶體 fs 慣例）
    inode_set_mtime_to_ts(dir, inode_set_ctime_current(dir));
    return 0;
}

static int myfs_create(struct mnt_idmap *idmap, struct inode *dir,
                       struct dentry *dentry, umode_t mode, bool excl)
{
    return myfs_mknod(idmap, dir, dentry, mode | S_IFREG, 0);
}

static int myfs_mkdir(struct mnt_idmap *idmap, struct inode *dir,
                      struct dentry *dentry, umode_t mode)
{
    int ret = myfs_mknod(idmap, dir, dentry, mode | S_IFDIR, 0);
    if (!ret)
        inc_nlink(dir);                   // 子目錄的 ".." → 父目錄 nlink +1
    return ret;
}

static const struct inode_operations myfs_dir_inode_operations = {
    .create = myfs_create,
    .lookup = simple_lookup,
    .link   = simple_link,
    .unlink = simple_unlink,
    .mkdir  = myfs_mkdir,
    .rmdir  = simple_rmdir,
    .mknod  = myfs_mknod,
    .rename = simple_rename,
};

/* ── 一般檔案：讀寫全借 generic_file_* + ram_aops（Step 6）── */
static const struct file_operations myfs_file_operations = {
    .read_iter   = generic_file_read_iter,
    .write_iter  = generic_file_write_iter,
    .mmap        = generic_file_mmap,
    .fsync       = noop_fsync,
    .splice_read = filemap_splice_read,
    .llseek      = generic_file_llseek,
};

static const struct inode_operations myfs_file_inode_operations = {
    .setattr = simple_setattr,
    .getattr = simple_getattr,
};

/* ── super 層 ops（Step 3）── */
static const struct super_operations myfs_super_ops = {
    .statfs     = simple_statfs,
    .drop_inode = generic_delete_inode,
};

/* ── fill_super：建 super_block + 根 inode/dentry（Step 3）── */
static int myfs_fill_super(struct super_block *sb, struct fs_context *fc)
{
    struct inode *root_inode;

    sb->s_maxbytes       = MAX_LFS_FILESIZE;
    sb->s_blocksize      = PAGE_SIZE;
    sb->s_blocksize_bits = PAGE_SHIFT;
    sb->s_magic          = MYFS_MAGIC;
    sb->s_op             = &myfs_super_ops;
    sb->s_time_gran      = 1;

    root_inode = myfs_make_inode(sb, NULL, S_IFDIR | 0755, 0);
    if (!root_inode)
        return -ENOMEM;

    sb->s_root = d_make_root(root_inode);  // 失敗會自動 iput(root_inode)
    if (!sb->s_root)
        return -ENOMEM;
    return 0;
}

/* ── fs_context 三步接力（Step 2）── */
static int myfs_get_tree(struct fs_context *fc)
{
    return get_tree_nodev(fc, myfs_fill_super);   // 無 block device 的通用 get_tree
}

static const struct fs_context_operations myfs_context_ops = {
    .get_tree = myfs_get_tree,
};

static int myfs_init_fs_context(struct fs_context *fc)
{
    fc->ops = &myfs_context_ops;
    return 0;
}

/* ── 檔案系統型別（Step 1）── */
static struct file_system_type myfs_fs_type = {
    .owner           = THIS_MODULE,
    .name            = "myfs",
    .init_fs_context = myfs_init_fs_context,
    .kill_sb         = kill_litter_super,
    .fs_flags        = FS_USERNS_MOUNT,
};

static int __init myfs_init(void)
{
    return register_filesystem(&myfs_fs_type);
}

static void __exit myfs_exit(void)
{
    unregister_filesystem(&myfs_fs_type);
}

module_init(myfs_init);
module_exit(myfs_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Minimal in-memory filesystem for kernel_internals Ch 35");
```

用 Ch 0 的 Makefile 編出 `myfs.ko`（`obj-m += myfs.o`），放進 initramfs（或 `scp` 進 QEMU）。

### 掛載與操作測試

QEMU 開機到 shell 後：

```bash
# 1. 載入模組並確認型別註冊成功
/ # insmod /myfs.ko
/ # cat /proc/filesystems | grep myfs
        myfs                        # ← register_filesystem 生效，出現在這裡

# 2. 掛載（source 用 none，因為沒有背後 device）
/ # mkdir -p /mnt/myfs
/ # mount -t myfs none /mnt/myfs
/ # mount | grep myfs
none on /mnt/myfs type myfs (rw,relatime)

# 3. 建檔、寫、讀
/ # touch /mnt/myfs/hello        # → myfs_create
/ # echo "kernel internals" > /mnt/myfs/hello   # → write_iter → page cache
/ # cat /mnt/myfs/hello          # → read_iter → 命中 folio
kernel internals

# 4. 建目錄、列出
/ # mkdir /mnt/myfs/dir          # → myfs_mkdir
/ # ls -la /mnt/myfs             # → simple_dir_operations（列目錄）
drwxr-xr-x  ... .
drwxr-xr-x  ... ..
-rw-r--r--  ... hello
drwxr-xr-x  ... dir

# 5. 驗證是純記憶體：df 看它、stat 看 magic/inode
/ # df -h /mnt/myfs              # → simple_statfs
/ # stat -f /mnt/myfs           # ID/type 對應 MYFS_MAGIC

# 6. 卸載順序（先 umount 再 rmmod，否則 rmmod 被 .owner 擋）
/ # umount /mnt/myfs
/ # rmmod myfs
```

每一條指令背後都對應你填的一個 ops：`touch` → `myfs_create`、`cat` → `read_iter`、`ls` → `simple_dir_operations`、`df` → `simple_statfs`。**你剛剛看著使用者空間的每個檔案操作，被 VFS 分派到你寫的函式。**

### 用 ftrace 看見自己的 read_iter 被呼叫

Ch 34 用 ftrace 追過 `vfs_read` 往下的呼叫鏈。現在追同一條路，看 VFS 是不是真的分派進你的 `myfs` 檔案（read_iter 是 `generic_file_read_iter`，因為我們借用它）：

```bash
/ # cd /sys/kernel/tracing
/ # echo function_graph > current_tracer
/ # echo generic_file_read_iter > set_graph_function   # 從這裡開始畫
/ # echo 1 > tracing_on
/ # cat /mnt/myfs/hello > /dev/null                     # 觸發 myfs 的 read
/ # echo 0 > tracing_on
/ # cat trace
```

你會看到（記憶體 fs 永遠命中，沒有 `submit_bio`）：

```
 generic_file_read_iter() {
   filemap_read() {
     filemap_get_pages() { ... }        # 查 page cache（myfs 的 folio）
     copy_folio_to_iter() { ... }       # copy_to_user
   }
 }
```

對照 Ch 34 磁碟 fs 冷讀那份 trace（裡面有 readahead、`submit_bio`），這裡**乾乾淨淨沒有下磁碟那一段**——這就是「記憶體 fs 不碰 block layer」在 trace 裡看得見的證據。想追你自己寫的函式（不是借來的 generic），把 `set_graph_function` 改成 `myfs_create`，然後在 QEMU 裡 `touch /mnt/myfs/x`，就能看見自己的 `myfs_create → myfs_mknod → d_instantiate` 被呼叫。

## 對比與取捨：myfs 對比真實 fs（ext4）少了什麼

記憶體 fs 是最好的教學起點，正因為它**故意少掉**了磁碟 fs 的一大半複雜度。把差距列清楚，你就知道 Ch 36 之後還有什麼在等你：

| 面向 | myfs（記憶體 fs） | ext4（磁碟 fs） |
|---|---|---|
| 掛載 | `get_tree_nodev`：配 anon super_block，不開 device | `get_tree_bdev`：開 block device，從磁碟讀 superblock |
| inode 元資料 | `new_inode` 配在記憶體，關機就沒 | 有 on-disk inode 格式，`write_inode` 落盤，mount 時讀回 |
| 檔案內容 | page cache 的 folio 就是儲存（`ram_aops`） | page cache 是快取，真身在磁碟；`read_folio` 發 bio 讀 |
| 「檔案第 N 頁 → 哪裡」 | 不需要——頁就在記憶體，缺頁清零即是 | extent tree / block map：翻譯成磁碟磁區（Ch 36 前置） |
| 目錄查找（lookup） | `simple_lookup`：dcache 就是全部，沒更底層 | 讀磁碟目錄 block 找名字（path walk 的慢路徑） |
| 崩潰一致性 | 無（本來就不持久） | journaling（jbd2）：crash 後 replay/rollback |
| 空間管理 | 受總記憶體限制，無 block 配置 | block/inode bitmap、多層 allocator、碎片管理 |
| fsync | `noop_fsync`（沒磁碟可 sync） | 真的把 dirty 頁 + metadata 寫回並等落盤 |

一句話總結差距：**myfs 把「VFS 介面」實作完整，把「持久化 + block 管理 + 崩潰一致性」整組省掉**。這三組省掉的東西，正是 Ch 36（block layer/bio/blk-mq）以及一個真實磁碟 fs 的絕大部分工作量。你先把 VFS 介面這層吃透，之後看 ext4 才不會被 block 層的細節帶偏。

**替代設計對比（記憶體 fs 之間）**：

| 方案 | 定位 | 和 myfs 差在哪 |
|---|---|---|
| ramfs（`fs/ramfs/`） | kernel 內建、最接近 myfs | 多了 symlink/tmpfile 支援；myfs 是它的教學裁剪版 |
| tmpfs（`mm/shmem.c`） | 產品級記憶體 fs | 加上：可設大小上限、頁能換到 swap（Ch 22）、完整 xattr —— 複雜度高一個量級 |
| 自己拼 aops | 早期 kernel 沒 export `ram_aops` 時的做法 | 要自己組 `simple_read_folio`+`simple_write_begin`+`simple_write_end`；v6.12 有 `ram_aops` 就不必了 |

## 踩雷集錦

1. **`d_make_root` 失敗後又自己 `iput(root_inode)`——double free。** `d_make_root` 失敗時**已經**幫你 `iput` 掉傳進去的 inode 了。所以 `fill_super` 裡 `d_make_root` 回 NULL 時，直接 `return -ENOMEM`，不要再碰 `root_inode`。這是 fill_super 最經典的資源管理錯誤。

2. **忘了在 `create`/`mkdir` OR 上型別位（`S_IFREG`/`S_IFDIR`）。** VFS 傳給 `create` 的 `mode` 只有權限位，沒有型別位。你不 OR `S_IFREG`，`myfs_make_inode` 的 `switch (mode & S_IFMT)` 就落進 `default`（`init_special_inode`），建出來的不是一般檔案，`cat` 會行為錯亂。型別由你在 create/mkdir 決定並補上。

3. **在 v6.12 直接寫 `inode->i_mtime = current_time(inode)`——編不過。** 時間欄位已改成 accessor（Ch 33 講過）。用 `simple_inode_init_ts(inode)` 一次設好，或 `inode_set_mtime_current`/`inode_set_ctime_current`。抄舊 fs 教學（多半是 4.x/5.x 的）在這裡必中。

4. **`rmmod` 前沒 `umount`，得到 `Resource busy`。** 只要還有 myfs 實例掛著，`.owner = THIS_MODULE` 讓模組引用計數非零，`rmmod` 被擋。這不是 bug，是防止你把「正在被使用的檔案系統程式碼」抽掉。順序永遠是：`umount` 所有掛載點 → `rmmod`。

5. **以為 `mount -t myfs /dev/xxx /mnt` 要給 device。** 記憶體 fs 沒有背後 device，source 給 `none`（或任意字串，被忽略）即可：`mount -t myfs none /mnt/myfs`。給一個真 device 路徑不會讓它變持久，因為我們走的是 `get_tree_nodev`——那個參數根本沒被用。

6. **少填 `i_fop`（目錄的 `simple_dir_operations`），`ls` 失敗或 kernel warning。** 目錄 inode 的 `i_fop` 一定要有「讀目錄」實作（`iterate_shared`/`readdir`），記憶體 fs 用 `simple_dir_operations`。漏了它，`ls /mnt/myfs` 會報錯或 VFS 抱怨目錄不可讀。目錄和一般檔案的 `i_fop` 是兩張不同的表，別搞混。

## 進階：再往深一層

- **加 symlink 支援**：填 `myfs_dir_inode_operations.symlink`，用 `page_symlink`（把目標路徑寫進 inode 的 page cache）並設 inode 的 `i_op` 為 `simple_symlink_inode_operations`（有 `get_link`）。ramfs 的 `ramfs_symlink` 就是範本。這讓你的 fs 能 `ln -s`（接 Ch 33 的 symlink 展開）。

- **加 mount 參數解析（`-o`）**：填 `fs_context_operations.parse_param`，用 `fs_parameter_spec` 描述接受哪些參數（如 tmpfs 的 `size=`）。ramfs 的 `ramfs_fs_parameters` + `ramfs_parse_param` 是最小範本。這是你想讓 fs 可調（限大小、設 mode）時的入口。

- **自訂 inode（附掛私有資料）**：磁碟 fs 常自訂 `alloc_inode`/`destroy_inode`，在 `struct inode` 外面包一層自己的 struct（如 `struct ext4_inode_info`），用 `container_of` 取回。記憶體 fs 通常不需要，但當你要在 inode 上掛額外狀態（如自訂的 file 內容結構、而非借 page cache）時就得這樣做。這是理解「為什麼 ext4 要 `alloc_inode` 而 ramfs 不用」的關鍵。

- **面試常問**：「寫一個最小檔案系統要實作哪些東西？」——答：註冊 `file_system_type`（`init_fs_context` → `get_tree` → `fill_super`）；`fill_super` 建 super_block、設 `s_op`、造根 inode/dentry；目錄 inode 的 `i_op` 提供 `create`/`mkdir`/`lookup`；一般檔案的 `i_fop` 提供 `read_iter`/`write_iter`、`i_mapping->a_ops` 決定內容進出。記憶體 fs 可大量借 `simple_*` 與 `generic_file_*`，磁碟 fs 才要自己實作 aops 的 `read_folio`（發 bio）與 on-disk 格式。

- **和練習 E 的銜接**：練習 E（`practice-e-mini-fs.md`）會擴充這個 myfs——加 symlink/参數、或反過來做一個**有背後 block device 的 ramdisk**（走 `get_tree_bdev`，這就把 Ch 36 的 block layer 牽進來了）。這章的 myfs 是那個練習的起點程式碼。

## 動手練習

1. **把 myfs 跑通一遍**：編出 `myfs.ko`，在 QEMU 裡完成「insmod → mount → touch → echo → cat → mkdir → ls → umount → rmmod」全流程。每一步用 `dmesg` 確認沒有 warning。這是後面所有擴充的基礎。

2. **用 ftrace 抓自己的函式**：`echo myfs_create > set_graph_function`，開 function_graph，在 QEMU 裡 `touch /mnt/myfs/a`，`cat trace` 看 `myfs_create → myfs_mknod → d_instantiate` 的呼叫鏈。你會親眼看到 VFS 分派進你寫的程式碼。

3. **故意漏掉一張 ops，看 VFS 怎麼抱怨**：把 `myfs_make_inode` 裡目錄那支的 `inode->i_fop = &simple_dir_operations` 註解掉，重編、掛載、`ls /mnt/myfs`——觀察錯誤訊息，理解「目錄一定要有 readdir 實作」。改回來。

4. **證明它是純記憶體**：`echo data > /mnt/myfs/f`，然後 `umount /mnt/myfs` 再 `mount -t myfs none /mnt/myfs`——`f` 不見了。因為 umount 時 `kill_litter_super` 把 dentry 樹連同 inode/page cache 全拆了，重掛是全新的空 fs。這就是「不持久」的直接證據。

5. **對比 ext4 的 read trace**：在 QEMU 裡對一個真 ext4 檔案（若有）或用 Ch 34 的方法，冷讀時追 `generic_file_read_iter`，看到 `submit_bio`；再對 myfs 檔案追同一函式，確認**沒有** `submit_bio`。把兩份 trace 並排，就是「記憶體 fs vs 磁碟 fs 差在 block layer」的實證。

6. **讀 ramfs 源碼對照**：打開 `fs/ramfs/inode.c`（220 行左右），把它的 `ramfs_get_inode`/`ramfs_mknod`/`ramfs_fill_super`/`ramfs_init_fs_context` 和你的 myfs 逐一對照。你會發現 myfs 幾乎是 ramfs 去掉 symlink/tmpfile/參數後的裁剪版。這是「讀懂一個真實的最小 fs」最快的路。

## 本章重點整理

- 寫一個檔案系統模組的骨架是固定的：`register_filesystem(&fs_type)` 登記型別 → mount 時 `init_fs_context → get_tree_nodev → fill_super` 三步接力 → `fill_super` 建 super_block、設 `s_op`、用 `d_make_root` 造根 inode/dentry。`register` 只登記型別，真正建東西在 mount。
- inode 工廠（`myfs_make_inode`）的核心是 `switch (mode & S_IFMT)`：目錄填 `myfs_dir_inode_operations` + `simple_dir_operations`，一般檔案填 `read_iter`/`write_iter` + `i_mapping->a_ops = ram_aops`。**inode 的型別在出生時決定它掛哪張 ops 表**——這是 Ch 33「i_op/i_fop 為何拆兩張表」的實作現場。
- 記憶體 fs 大量複用 kernel helper：目錄操作借 `simple_lookup`/`simple_link`/`simple_rmdir`/`simple_rename`、super 借 `simple_statfs`、清理借 `kill_litter_super`、檔案讀寫借 `generic_file_*` + `ram_aops`（v6.12 已 export）。你幾乎不寫讀寫邏輯，卻有完整 buffered I/O + mmap——這是 VFS 抽象價值的最直接展示。
- 記憶體 fs 是最好的教學起點，因為它**故意省掉** on-disk 格式、block 配置、journaling、發 bio 讀磁碟——Ch 34 慢路徑那一整段對它不存在。這些省掉的東西正是 Ch 36 block layer 與真實磁碟 fs 的主要工作量。

## 自我檢核

- [ ] 不看筆記，能說出從 `insmod` 到 `mount` 再到 `cat` 一個檔案，VFS 依序呼叫了你哪些函式（`register_filesystem`→`init_fs_context`→`get_tree`→`fill_super`→`create`→`read_iter`）
- [ ] 能解釋 `fill_super` 必做的三件事，以及 `d_make_root` 失敗後為什麼**不能**再 `iput` 根 inode
- [ ] 能說清楚「目錄 inode」和「一般檔案 inode」的 `i_op`/`i_fop` 為什麼不同、各掛什麼、在哪一行決定的
- [ ] 能講出記憶體 fs 為什麼能用 `ram_aops` + `generic_file_*` 白拿讀寫，以及它和 ext4 的 `read_folio` 差在哪（清零 vs 發 bio）
- [ ] 面試被問「寫一個最小檔案系統要做什麼」，能結構化答出：註冊型別 + fill_super 建 super/根 + 目錄 ops（create/lookup/mkdir）+ 檔案 ops（read/write/aops），並點出記憶體 fs 能借哪些 helper
- [ ] 能列出 myfs 對比 ext4 少了哪些東西（持久化/block 配置/journaling/bio），並知道這些是 Ch 36 的主題

## 延伸閱讀

### 官方文件與源碼

- **`fs/ramfs/inode.c`（v6.12）** — [elixir](https://elixir.bootlin.com/linux/v6.12/source/fs/ramfs/inode.c)
  - **讀哪裡**：整份，約 220 行。`ramfs_get_inode`（對應 myfs 的 inode 工廠）、`ramfs_mknod`/`ramfs_mkdir`/`ramfs_create`、`ramfs_fill_super`、`ramfs_init_fs_context`、`ramfs_fs_type`
  - **和本章關聯**：myfs 就是 ramfs 的教學裁剪版；把兩者逐函式對照，是驗證你真的懂這章的最好方法。ramfs 多出來的 symlink/tmpfile/參數解析，正是本章「進階」那節的擴充方向

- **`fs/libfs.c`（v6.12）** — [elixir](https://elixir.bootlin.com/linux/v6.12/source/fs/libfs.c)
  - **讀哪裡**：`simple_lookup`、`simple_dir_operations`/`simple_dir_inode_operations`、`simple_statfs`、`simple_setattr`/`simple_getattr`、以及 `ram_aops` 的定義
  - **能學到什麼**：你這章借的每個 `simple_*` helper 到底做了什麼。特別看 `ram_aops` 的 `read_folio`——理解「缺頁清零即正確內容」為什麼對記憶體 fs 成立

- **[Documentation/filesystems/vfs.rst](https://www.kernel.org/doc/html/latest/filesystems/vfs.html)** — kernel 官方 VFS 文件
  - **讀哪裡**：「Registering and Mounting a Filesystem」「The Superblock Object」「The Inode Object」三節，配 Ch 33 已讀過的部分
  - **和本章關聯**：本章實作的每個 method（`fill_super`、`create`、`lookup`、`read_iter`）的語意與呼叫時機的權威定義

- **[Documentation/filesystems/mount_api.rst](https://www.kernel.org/doc/html/latest/filesystems/mount_api.html)** — 現代 fs_context mount API
  - **讀哪裡**：`fs_context`、`fs_context_operations`、`get_tree_*` 家族那幾節
  - **前提**：本章 Step 2 的三步接力就是照這篇；想搞懂 `get_tree_nodev` vs `get_tree_bdev` 的分工（記憶體 fs vs 磁碟 fs）在這裡

### 書籍

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，第 13 章「The Virtual Filesystem」
  - **讀哪裡**：四物件與 ops 表那幾節，先建立「填表就是實作 fs」的直覺再回來看程式碼
  - **注意**：書用的是舊 mount API（`.get_sb`/`.mount`），fs_context 是後來的；架構觀念通用，API 以 v6.12 本章為準

四大物件你讀懂了（Ch 33）、read 路徑你追過了（Ch 34）、現在你把這些介面親手實作了一遍——一個能掛能讀寫的檔案系統就在你手上。myfs 之所以能整段跳過磁碟，靠的是「內容就在 page cache、不必發 bio」。下一章我們就去拆那個被跳過的世界：bio 提交之後，在 block layer 的 blk-mq 裡到底怎麼排隊、合併、下到真正的 NVMe/SATA 裝置——把 Ch 34 停在「submit_bio」的那條線，接著走完。

→ [Ch 36 block layer：bio 與 blk-mq](./36-block-layer-blkmq.md)
