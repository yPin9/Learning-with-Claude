# Ch 34 — 檔案系統攻擊面：ext4 / f2fs / ntfs3 image parsing、fuzzing 礦區

> **目標**：搞清楚「為什麼 kernel 裡的檔案系統 parser 是 bug 密度最高的礦區之一」——攻擊者不需要送封包、不需要呼叫奇怪的 syscall，只要讓 kernel 去 `mount` 一個精心構造的 disk image，kernel 就會用 ring 0 的權限解析不受信任的 on-disk metadata。我們要能說出 ext4 / f2fs / ntfs3 三者的攻擊面差異、fs bug 的典型成因（對應 [Ch 10](./10-vulnerability-taxonomy.md) 的分類）、Syzkaller 為什麼在 fs 上特別能挖，以及防禦的關鍵：誰有資格觸發 mount。

> **環境**：Linux 6.6 LTS、x86-64 基線。ntfs3 於 5.15 進入 mainline（就我所知），所以 6.6 上有；較舊的 LTS 沒有。本章的 shell 指令（`mkfs`、`debugfs`、`mount`）與 kernel 行為描述**未在本環境實測**（作者環境為 Windows，沒有可用的 Linux kernel 實驗環境），屬於理論預期行為，驗證方式在「動手練習」節。CVE 資訊以 WebSearch 查到的 NVD / Red Hat / syzbot 紀錄為準，查不到的我們只描述模式，不給編號。

## 為什麼 fs parser 值得單獨講？

先看一個對照。[Ch 30](./30-netfilter-nftables.md) 的 nf_tables 要先拿到 `CAP_NET_ADMIN` 才摸得到，[Ch 31](./31-io-uring.md) 的 io_uring 要先 setup 一個 ring。檔案系統的特別之處在於**輸入來源不在 syscall 參數裡，而在一塊「儲存媒體」上**：

```
一般 syscall 攻擊面                        檔案系統 image 攻擊面
────────────────────                       ────────────────────────
user process ──syscall args──▶ kernel      攻擊者 ──(寫好)──▶ disk image
   (你控制幾個 int / buffer)                  (整塊 metadata：superblock、inode
                                              table、extent tree、journal、xattr…)
                                                        │
                                           USB 隨身碟 / loop device / 雲端映像 /
                                           容器 volume / 下載的 .img
                                                        │
                                                        ▼
                                           kernel 的 fs driver 用 ring 0
                                           逐欄位 parse、信任、做指標運算
```

三個因素讓它成為礦區：

1. **輸入空間巨大且結構複雜**。一個 ext4 image 有 superblock、block group descriptor、inode、extent tree（B-tree 的變體）、htree 目錄、xattr、journal、orphan list……每一種結構都有 offset、length、count、「指向另一個 block 的 block 號」。每個欄位都是攻擊者可控的，而且欄位之間有**一致性約束**（例如 inode 的 `i_size` 要和 extent 涵蓋範圍相容），parser 漏檢一條約束就是一個 bug。
2. **歷史包袱重**。ext4 從 ext2/ext3 演化而來，兼容舊 feature flag；ntfs3 要吃 Windows 世界二十年的格式變體；f2fs 為 flash 設計，metadata 有 NAT / SIT / SSA / checkpoint 多份互相引用的表。程式碼量大、路徑多、跨版本相容的分支多。
3. **信任模型模糊**。fs driver 的作者長期把「image 是我自己 mkfs 出來的」當成預設情境，出錯頂多是 fsck 修的事。後來 USB 隨身碟、容器映像、雲端 VM disk 讓「image 來自不受信任的人」成為真實情境，但 driver 的 hardening 沒有同步跟上。

> 如果你對 kernel 漏洞分類的詞彙（OOB、UAF、integer、uninit）還不熟，先回看 [Ch 10 — kernel 漏洞類型學](./10-vulnerability-taxonomy.md)。

歷史 bug 密度的一個直觀指標：看 syzbot 的 fs 類別、或看 stable 樹的 changelog，`ext4:`、`f2fs:`、`ntfs3:`、`btrfs:`、`jfs:`、`hfsplus:` 開頭的 commit 幾乎每週都有，而且相當比例的 commit message 帶著 `KASAN: slab-out-of-bounds`、`KASAN: use-after-free` 的 syzbot 報告。我們不給精確比例（沒有可靠的統計來源可引用）。

## 先建立直覺：mount 一個 image 的時候 kernel 做了什麼

把 `mount -t ext4 evil.img /mnt` 想成「請 kernel 當一個很信任人的圖書館員：你遞給他一本目錄（superblock），他就照目錄上的頁碼去翻書架」。目錄寫錯，他會翻到不存在的書架，甚至走出書庫。

```
mount(2) / fsopen(2)+fsconfig(2)+fsmount(2)
   │
   ▼
 fs_context 建立、解析 mount options                ← 【面 A】option 字串 parser
   │                                                   （CVE-2022-0185 在這一層）
   ▼
 fs_type->init_fs_context / get_tree
   │
   ▼
 讀 block device → fill_super()
   │   ├─ 讀 superblock，驗 magic / feature flags     ← 【面 B】superblock 欄位驗證
   │   ├─ 讀 group descriptors / checkpoint / MFT     ← 【面 B】全域 metadata
   │   ├─ journal replay（ext4 jbd2 / ntfs3 $LogFile）← 【面 C】replay 會「執行」log 內容
   │   └─ 讀 root inode、orphan list cleanup
   ▼
 mount 完成 ────────────────────────────────────────
   │
   ▼  之後每個 VFS 操作
 open / read / write / rename / unlink / truncate / getxattr…
   │                                                  ← 【面 D】runtime 操作
   ▼                                                     走到「mount 時沒驗到」的 metadata
 walk extent tree / htree / xattr block / run list
```

這張圖的重點是**面 D**：mount 成功不代表 image 乾淨。許多公開 CVE 的描述句型都是「mount a crafted image **and then perform some operations**」（見後文 CVE-2018-10879、CVE-2019-19447 的 NVD 描述），因為 fill_super 只驗了一部分，真正的 bug 在後續操作碰到沒驗過的欄位時才爆。

## fs driver 的物件模型：你要認得的資料結構

不管哪個 fs，攻擊面都落在「把 on-disk 結構轉成 in-memory 結構」的那一層。下表是我們讀 source 時該找的東西：

| 概念 | ext4 | f2fs | ntfs3 |
|---|---|---|---|
| on-disk 超級區塊 | `struct ext4_super_block` | `struct f2fs_super_block` | boot sector + `$Volume` |
| in-memory 超級區塊資訊 | `struct ext4_sb_info`（`sb->s_fs_info`） | `struct f2fs_sb_info` | `struct ntfs_sb_info` |
| 檔案 metadata | `struct ext4_inode` → `ext4_inode_info` | `struct f2fs_inode`（node block 內） | MFT record（`struct MFT_REC`） |
| 資料位置索引 | extent tree（`ext4_extent_header` / `ext4_extent`） | node 間接樹 + NAT 映射 | run list（`struct runs_tree`，attribute 內 packed 格式） |
| 目錄 | linear / htree（`dx_*`） | dentry block + hash 階層 | `$INDEX_ROOT` / `$INDEX_ALLOCATION`（B+tree） |
| 擴充屬性 | xattr（inode 內 + 獨立 block） | xattr node | `$EA` attribute |
| crash 一致性 | jbd2 journal | checkpoint + roll-forward recovery | `$LogFile`（`fslog.c` replay） |
| 主要 source 位置 | `fs/ext4/` | `fs/f2fs/` | `fs/ntfs3/` |

讀 source 的入口：ext4 看 `fs/ext4/super.c` 的 `ext4_fill_super()`；f2fs 看 `fs/f2fs/super.c` 的 `f2fs_fill_super()` 以及 `sanity_check_raw_super()`、`f2fs_sanity_check_ckpt()`、`sanity_check_inode()` 這一組「驗證函式」；ntfs3 看 `fs/ntfs3/super.c` 的 `ntfs_fill_super()`，再往 `record.c`（attribute 列舉）、`run.c`（run list pack/unpack）、`index.c`（目錄 B+tree）、`fslog.c`（log replay）追。

這裡有一個閱讀技巧：**找「驗證函式」和「使用函式」的距離**。驗證集中在 mount 時（好）還是每次使用前就近驗證（更好）？如果某個欄位在 `fill_super` 裡驗一次、之後三個函式直接信任，那三個函式就是候選 bug 點。

## 三個 fs 的攻擊面差異

### ext4：最成熟、驗證最多，但路徑也最多

ext4 是 Linux 最廣用的 fs，被 fuzz 的歷史最長。它有幾層防禦：

- **metadata checksum**（`metadata_csum` feature，crc32c）。**注意：checksum 不是安全機制**。攻擊者自己構造 image，當然會算好正確的 crc32c。checksum 防的是隨機 bit rot，不是惡意輸入。這是新手最常搞混的一點，後面踩雷再講。
- **`__ext4_ext_check`、`ext4_valid_inode`、`ext4_check_dir_entry`** 這類 sanity check 在讀取 metadata 時做結構驗證。
- **維護者立場**（就我所知，請以 linux-ext4 list 的討論為準）：ext4 維護者長期認為「mount 一個惡意構造的 image」不屬於 kernel 要提供安全保證的威脅模型，修這類 bug 是 robustness 工作，不是 CVE 級的安全修補。這個立場直接影響你怎麼評估一個 ext4 image bug 的價值：它在 kernelCTF 這類「本地 unprivileged → root」情境裡通常**打不到**，因為一般 user 沒資格 mount（後文防禦節）。

儘管如此，ext4 仍然持續出 bug，WebSearch 查到的公開紀錄：

| CVE | 位置 | 類型（NVD / Red Hat 描述） |
|---|---|---|
| CVE-2018-10876 | `ext4_ext_remove_space()` | crafted image mount 後操作造成 UAF |
| CVE-2018-10879 | `ext4_xattr_set_entry()` | crafted image 內 rename 檔案造成 UAF |
| CVE-2019-19447 | `ext4_put_super`（`dump_orphan_list`，`fs/ext4/super.c`） | mount、操作、umount 流程造成 UAF |
| CVE-2022-1184 | `dx_insert_block()`（`fs/ext4/namei.c`） | 損毀 image 造成 UAF（KASAN 報告） |
| CVE-2022-50673 | orphan inode cleanup | crafted image，Red Hat 評為 Low |

觀察：ext4 的 bug 集中在**不在 mount 主路徑的深處**——extent 移除、xattr 修改、htree 插入、orphan 清理、umount。這些路徑複雜、狀態多，驗證容易漏。

### f2fs：為 flash 設計，metadata 多份互相引用

f2fs 的 metadata 分成 NAT（Node Address Table）、SIT（Segment Information Table）、SSA（Segment Summary Area）、checkpoint 兩份輪流寫。一個 node 的位置要查 NAT，一個 segment 的狀態要查 SIT，彼此要一致。這種「多表交叉引用」天生讓驗證更難：每個表單獨看合法，組合起來不一致就會出事。

f2fs 的另一個特點：Android 的 `/data` 預設常用 f2fs（就我所知，現代 Android 裝置大多如此，各廠商不一），所以 f2fs 是行動裝置上的真實攻擊面；但 `/data` 上的 image 是裝置自己建的，惡意 image 要靠「外接儲存」或「已取得部分權限後的損毀」才能觸發。f2fs 的 hardening 方式是 `sanity_check_*` 系列函式，在 `f2fs_fill_super` 與讀 inode 時大量加檢查；這些檢查的 commit 歷史本身就是一份 bug 地圖。

### ntfs3：相對新、程式碼量大、bug 多

ntfs3（Paragon 貢獻，5.15 合入 mainline，就我所知）取代舊的唯讀 `ntfs` driver，支援讀寫。它的風險特徵：

- **新**：被 fuzz 與 code review 的歲月短，syzbot 一旦把它納入 corpus，短時間內報出大量 bug。
- **格式複雜**：NTFS 的 attribute 是 packed、可變長度、可 resident / non-resident 的結構，run list 用 variable-length encoding。parse 這種格式最容易出 integer 與 OOB 問題。
- **log replay**（`fslog.c`）：mount 時會 replay `$LogFile`，也就是 **在 mount 階段就執行來自 image 的操作描述**。2026 年編號的 CVE 裡有 `read_log_rec_buf()` 的 OOB read（搜尋結果摘要所述，crafted image 在 mount 時可觸發），模式與此一致。

WebSearch 查到的 ntfs3 公開紀錄：

| 識別 | 位置 | 類型 |
|---|---|---|
| CVE-2023-53988 | index header（`INDEX_HDR`）使用前未驗證 | OOB read；fix 是「use metadata 前先驗 index header」 |
| CVE-2023-54063 | `indx_insert_into_buffer` | OOB read，syzbot 報告 |
| （無 CVE，syzbot） | `run_unpack()`（`fs/ntfs3/run.c`） | `slab-out-of-bounds`，stable 修補加了「packed runs 的 offset 必須落在 attribute size 內」檢查 |
| CVE-2023-45896 | mount 時讀 kernel memory | 狀態有爭議：supplier 認為「需要攻擊者改過的 image」不算漏洞 |

最後一列值得停下來想：同一個 bug，有人標 CVE、有人說這不是漏洞。這直接對應前面講的威脅模型爭議。

### 三者對照

| 面向 | ext4 | f2fs | ntfs3 |
|---|---|---|---|
| 成熟度 | 最高 | 中 | 最低（5.15 起） |
| 格式複雜度來源 | extent tree、htree、journal | 多表交叉（NAT/SIT/SSA/CP） | packed attribute、run list、log replay |
| 典型 bug 位置 | 深路徑（remove_space、xattr、htree） | sanity check 沒覆蓋的 node/segment | attribute / run / index 的長度運算 |
| 主要實際部署 | 幾乎所有 Linux 發行版 root fs | Android `/data`、部分 flash 裝置 | 與 Windows 共用的外接碟、雙系統 |
| 能被 unprivileged 直接 mount？ | 否（block device fs） | 否 | 否 |
| 對 LPE 的實用性 | 低（要先有 mount 權限） | 低 | 低到中（桌面自動掛載情境） |

## 典型 bug 類型與成因

把 fs bug 對回 [Ch 10](./10-vulnerability-taxonomy.md) 的分類，我們可以歸納出幾個反覆出現的 root cause 模式。

### 模式一：長度 / 偏移欄位未檢查 → OOB（slab-out-of-bounds）

on-disk 結構裡有「offset」「length」「count」，driver 讀進來後拿去做指標運算：

```c
/* 偽碼，說明模式；不是任何一個 kernel 版本的原文 */
struct attr_hdr *attr = (void *)rec + le16_to_cpu(rec->attr_off);   /* 攻擊者控制 */
u32 len = le32_to_cpu(attr->size);                                   /* 攻擊者控制 */
u8 *run = (u8 *)attr + le16_to_cpu(attr->run_off);                   /* 攻擊者控制 */
/* 漏檢：run_off 是否小於 len？len 是否超出 rec 範圍？ */
run_unpack(run, ...);   /* 往後讀到 attr 之外，甚至 record buffer 之外 */
```

ntfs3 的 `run_unpack()` 修補（「packed runs 的 offset 必須落在 attribute size 內」）就是這個模式。CVE-2023-53988 的「use index header 前先驗證」也是。

### 模式二：生命週期錯誤 → UAF

ext4 的 CVE-2018-10876、CVE-2018-10879、CVE-2019-19447、CVE-2022-1184 都是 UAF。成因通常是：某個 in-memory 物件（buffer head、extent path、orphan list 節點、xattr entry）被釋放後，另一條路徑還拿著指標，因為**損毀的 metadata 讓正常情況不會發生的路徑組合發生了**。例如 `ext4_find_extent()` 對 inode root extent header 缺驗證（2025 年 linux-ext4 list 上的報告，無 CVE 編號，搜尋結果摘要所述），造成後續使用錯誤的 depth / entry 數。

### 模式三：整數問題

`le16` / `le32` 欄位相加、相乘、減法。unsigned 下溢是最經典的一類。我們用一個**不是 on-disk image、但屬於 fs 掛載流程**的例子來看 root cause，因為它同時是下一章 [Ch 36](./36-user-namespace.md) 的主角。

### 案例：CVE-2022-0185（fs_context 的 `legacy_parse_param` 整數下溢）

> 本案例的事實來自 WebSearch：Red Hat bugzilla 2040358、oss-security 2022-01-18 通報、COS kernel 鏡像的 fix commit 頁面。上游 fix commit 編號 `722d94847de2` 是這些鏡像引用的，我們沒有直接開 kernel.org 的頁面確認，請你自己 `git show` 驗證。

背景：新的 mount API（`fsopen` / `fsconfig` / `fsmount`）以 `struct fs_context` 承接 mount options。對於**還沒轉換成新 API** 的舊 fs，kernel 提供 `legacy_fs_context` 路徑，`legacy_parse_param()` 把一條條 `key=value` 選項串接進一個 buffer。這個 buffer 就我所知是 `PAGE_SIZE` 大小（4096 bytes，因為 mount options 歷史上就是以一個 page 的 `void *data` 傳給 `mount(2)`；請以 source 為準）。

bug：長度檢查寫成

```c
/* 偽碼，還原 oss-security 描述的邏輯 */
if (len > PAGE_SIZE - 2 - size)      /* size 是 buffer 已用的長度 */
        return invalf(...);
/* ... 之後 memcpy(ctx->legacy_data + size, param->string, len) ... */
```

`PAGE_SIZE - 2 - size` 的型別是 unsigned。當 `size` 超過 `PAGE_SIZE - 2`，減法下溢成一個巨大的正數，`len > 巨大值` 恆為 false，檢查等於不存在；後續 copy 超出 buffer，heap overflow。修補（搜尋結果摘要所述）是改成 `size + len + 2 > PAGE_SIZE`，去掉減法。

這個 bug 在 fs 攻擊面的意義：

1. 它**不需要惡意 image**，條件是能呼叫 `fsconfig`。但 `fsconfig` 設定的是某個 fs 的 context，需要 `CAP_SYS_ADMIN`。
2. 沒有 root 時，攻擊者透過 `unshare(CLONE_NEWUSER | CLONE_NEWNS)` 進入新的 user namespace，在那裡取得 namespace 內的 `CAP_SYS_ADMIN`，就能對**允許在 user namespace 內掛載**的 fs 走這條路徑。oss-security 的通報明確寫了這個條件，且說這個 bug 從 5.1 起存在。
3. 這個案例同時展示：**fs 攻擊面 = image parser + option parser + mount 流程本身**。

類型對照 Ch 10：integer（underflow）→ heap OOB write。spray 與利用方式屬於 [Ch 17 — heap overflow](./17-heap-overflow.md) 的範疇，這裡不展開。

### 模式四：uninit / info leak

fs 讀進來的結構如果有 padding、未初始化的欄位，被複製回 user space（例如 `getdents`、`stat`、`FS_IOC_*` ioctl）會造成資訊洩漏，對應 [Ch 14](./14-kaslr-infoleak.md)。CVE-2023-45896（ntfs3 在 mount 時讀到 kernel memory）屬於「OOB read 轉成讀取 kernel 記憶體」這一類。

## 為什麼 Syzkaller 在 fs 上特別能挖

[Ch 43](./43-syzkaller.md) 會完整講 Syzkaller 原理。這裡先說 fs 為什麼是它的強項：

1. **image 是可變異的位元組陣列**。fuzzer 最擅長的就是對一段 buffer 做 mutation。Syzkaller 為此提供了 pseudo-syscall `syz_mount_image`（就我所知這個名稱與機制，請以 syzkaller `sys/linux/` 描述檔為準）：把一個壓縮過的 image 解開、掛載到指定 fs type，然後 fuzzer 接著對掛載點做 syscall 序列。
2. **corpus 可以用真實 mkfs 的產物當種子**。從合法 image 出發，bit flip / 欄位改寫比從零生成有效率得多，因為 magic 與基本結構已經通過。
3. **KASAN 把 silent corruption 變成 crash**。fs 的 OOB 常常只越界讀幾個 byte，沒有 KASAN 根本看不出來。syzbot 的 `slab-out-of-bounds` 報告幾乎都來自 KASAN（見 [Ch 44](./44-debug-toolchain.md)）。
4. **「mount + 操作」的二階段結構符合 fuzzer 的 sequence 模型**。fuzzer 本來就在做「建立資源 → 對資源呼叫操作」。
5. **成本低**。不需要硬體、不需要網路，一個 VM 就能反覆 mount 數千次。

反面是：fs fuzzing 的 bug 量太大，維護者處理不完，許多報告長期處於 open 狀態，這也是 syzbot dashboard 上 fs 類別堆積的原因。

## 防禦面：誰有資格觸發 mount，比 parser 本身更重要

我們的觀點：**修 parser bug 是打地鼠，限制誰能餵 image 才是結構性防禦**。

### 一、禁止 unprivileged mount 是關鍵

block device 型 fs（ext4、f2fs、ntfs3、btrfs、xfs……）**沒有被標記為可在 user namespace 內掛載**（就我所知 `FS_USERNS_MOUNT` flag 只在 tmpfs、proc、sysfs、devpts、overlayfs、fuse 等少數 fs 上設定；請在 source 裡 grep `FS_USERNS_MOUNT` 驗證）。所以：

```
unprivileged user
   │
   ├─ unshare(CLONE_NEWUSER|CLONE_NEWNS) ──▶ 取得 ns 內 CAP_SYS_ADMIN
   │        │
   │        └─ mount -t ext4 /dev/loop0 ──▶ 失敗（fs 沒有 FS_USERNS_MOUNT，
   │                                           而且 loop device 本身也是全域資源）
   │
   └─ 但：mount -t tmpfs / overlay / fuse ──▶ 成功（這些 fs 的 parser 反而成為攻擊面，見 Ch 36）
```

這解釋了為什麼 kernelCTF 這類「本地 unprivileged」場景裡，ext4/ntfs3 image bug 很少被拿來當入口，而 netfilter、io_uring 一直是主戰場：前者要先有 mount 權限。

### 二、USB 自動掛載讓「沒有 root」變成假象

桌面環境（udisks2 + polkit）允許「active local session 的使用者」掛載外接儲存。於是：

```
攻擊者插入惡意 USB 隨身碟（物理接觸）
   │
   ▼
udev 偵測 → desktop 的 automounter（udisks2）以 root 身分呼叫 mount(2)
   │
   ▼
kernel 的 fs driver 在 ring 0 parse 惡意 image
```

使用者什麼都沒做，kernel 已經開始解析了。就我所知，這正是 ntfs3 / exfat / vfat 這類「外接碟常見 fs」被當作 physical attack surface 重點審查的原因。防禦：

- 鎖屏時不自動掛載（desktop 設定與 polkit 規則，各發行版不同）。
- 關閉不需要的 fs driver：`/etc/modprobe.d/` 內用 `install ntfs3 /bin/false` 或 `blacklist`（注意 `blacklist` 只擋 alias 自動載入，擋不住 `modprobe ntfs3` 直接指名；`install ... /bin/false` 才擋得徹底，這點請在自己機器上實測）。
- USBGuard 之類的 USB 授權機制，限制新裝置。
- 把不信任的 image 交給 **FUSE**（user space 的 `ntfs-3g`）：bug 落在 user process 而不是 kernel，爆炸半徑小得多。

### 三、其他縱深

| 手段 | 作用 | 限制 |
|---|---|---|
| `nosuid,nodev,noexec` mount flags | 掛載後不能靠 image 內的 setuid binary / device node 提權 | 擋的是「掛載後濫用」，擋不了 parser 本身的 bug |
| 自訂 module 黑名單 | 縮減 attack surface | 要維護；`CONFIG_` 層級編譯期移除更徹底 |
| 容器裡不給 `CAP_SYS_ADMIN` 與 `/dev/loop*` | 容器內無法 mount block fs | 特權容器等於沒防 |
| KASAN / KFENCE | 偵測用，不是防護；KFENCE 可上線採樣 | 有效能成本；KFENCE 採樣機率低 |
| 在隔離 VM 內 parse | 把 kernel parser 放在可犧牲的 VM（雲端服務處理使用者上傳 image 的做法） | 運維成本 |

## 對比取捨表

| 策略 | 防到的威脅 | 沒防到 | 成本 |
|---|---|---|---|
| 修 parser（加 sanity check） | 該欄位的 bug | 下一個欄位的 bug | 持續，永遠做不完 |
| 禁止 unprivileged / 自動 mount | 一般 user 直接餵 image | 已有 root 的攻擊者、物理自動掛載 | 低 |
| 用 FUSE 跑 fs | kernel 側 bug | FUSE daemon 自己的 bug（風險降到 user space） | 效能下降 |
| 不編入 fs driver | 該 driver 的所有 bug | 需要該 fs 時就不能用 | 取決於需求 |
| 隔離 VM parse 不信任 image | 整體 | VM escape | 運維 |

## 踩雷集錦

1. **「有 checksum 就不會被構造 image」**：錯。`metadata_csum` 的 crc32c 是完整性檢查不是認證，攻擊者掌握 image 全部內容，自己算就好。checksum 防的是 bit rot。
2. **「fill_super 通過就代表 image 合法」**：錯。mount 只驗關鍵 metadata，extent、xattr、htree 在被操作時才走到，所以 CVE 描述才會寫「mount and then operate」。fuzzer 與 exploit 都要在 mount 之後做足夠多樣的操作。
3. **「ext4 image bug 都能提權」**：不一定。要看誰能 mount（見防禦節）。把一個 `slab-out-of-bounds` 當成 LPE 之前，先問「攻擊者的 uid 能不能觸發 mount」，答案多半是不能。這是評估 CVE 實際價值時最常漏掉的一步。
4. **「CVE 編號越多代表越危險」**：不成立。ntfs3 的多個 CVE 是 OOB read 與 DoS，供應鏈與 CVE 編號機構（CNA）對 crafted image 的政策不一致（CVE-2023-45896 就被 supplier 認為不算漏洞）。看 root cause 與可控度，不看編號數量。
5. **「mount 選項字串不算攻擊面」**：CVE-2022-0185 的 root cause 在 `legacy_parse_param` 的長度運算，跟 image 內容無關。option parser 也是 parser。
6. **`blacklist` 不等於不能載入**：`blacklist ntfs3` 只擋 alias 觸發的自動載入，`modprobe ntfs3` 仍可手動載入；要硬擋需 `install ntfs3 /bin/false` 或編譯期移除。未實測，請在自己的環境驗證。

## 進階：再往深一層

- **crash 一致性機制本身是攻擊面**：ext4 的 jbd2 journal replay、ntfs3 的 `$LogFile` replay、f2fs 的 roll-forward recovery，都是在 mount 階段「依 image 內容重新執行寫入」。replay 程式碼路徑比一般讀路徑少被測，而且在 `fill_super` 早期就執行，狀態尚未完整建立，更容易出錯。
- **跨 fs 的共用層**：VFS 的 `fs_context`、`iomap`、`buffer_head`、page cache。共用層的 bug（如 CVE-2022-0185）影響所有走該路徑的 fs，修補一次覆蓋全部。
- **Rust fs / 記憶體安全語言**：Linux 6.x 的 Rust 基礎建設還沒有 production 級 block fs（就我所知，請查最新 status）。這方向的價值是從根源消除 OOB 與 UAF，但 `unsafe` 邊界與 FFI 仍會留下 bug。
- **面向 LPE 的 fs 目標**：對 unprivileged LPE 而言，真正的 fs 礦區是**可在 user namespace 內掛載的 fs**：overlayfs、FUSE、tmpfs 的 mount / option / 操作路徑。這串到 [Ch 36](./36-user-namespace.md)。
- **從 N-day 學**：挑一個 ntfs3 的 syzbot 報告，照 [Ch 42](./42-patch-to-rootcause.md) 的方法：讀 fix commit、還原觸發條件、判斷可控度。

## 動手練習

> 以下操作在**你自己擁有的 Linux VM**（建議 Ch 0 搭的 QEMU 環境或可丟棄的 VM）內進行，**未實測**，預期行為為理論推導。切勿對你不擁有的裝置做。

1. **觀察 mount 的 kernel 呼叫路徑**：在 VM 內建立一個小 ext4 image，用 `strace` 與 `ftrace`（`function_graph` tracer 過濾 `ext4_fill_super`）觀察 mount 時呼叫了哪些驗證函式。對照 `fs/ext4/super.c`，標出「驗證集中在哪」。
   ```bash
   dd if=/dev/zero of=t.img bs=1M count=16
   mkfs.ext4 -q t.img
   sudo mount -o loop t.img /mnt && ls /mnt && sudo umount /mnt
   ```
2. **確認 `FS_USERNS_MOUNT` 的分布**：在 Linux 6.6 source 裡 `grep -rn FS_USERNS_MOUNT fs/`，列出所有設了這個 flag 的 fs，核對本章宣稱。
3. **讀一個 ntfs3 fix**：找 `fs/ntfs3/run.c` 中 `run_unpack` 的 offset 檢查 commit（`git log -S'run_unpack' -- fs/ntfs3/run.c`），寫下「修補前缺的是什麼約束」。
4. **讀 CVE-2022-0185 的 fix**：`git show 722d94847de2`（若 hash 不符，用 `git log --grep='legacy_parse_param'` 找），確認 `fs/fs_context.c` 的改動，並把我們偽碼與真實 source 對照，修正我們可能還原錯的地方。
5. **觀察 USB 自動掛載**：在桌面 VM 內，看 `udisksctl monitor` 與 `journalctl -f`，插入（VM 的虛擬）儲存裝置，記錄是誰呼叫了 `mount`、以什麼身分。

## 本章重點整理

- fs 攻擊面的本質：kernel 以 ring 0 權限 parse 不受信任的 on-disk metadata；輸入空間大、格式複雜、歷史包袱重。
- bug 類型回到 Ch 10 的老面孔：長度/偏移未檢查的 OOB、生命週期的 UAF、integer underflow、info leak；多數在「mount 之後的深路徑」才爆。
- ext4 最成熟、ntfs3 最新且 bug 多、f2fs 靠 sanity_check 補洞；三者都屬 block fs，**unprivileged user 不能直接 mount**，這比 parser 品質更決定實際危害。
- 防禦優先序：限制誰能 mount > 關掉不需要的 driver > 隔離 parse > 修 parser。
- Syzkaller 擅長 fs，因為 image 可 mutation、KASAN 能抓 silent OOB、`mount + 操作` 符合 sequence 模型。

## 自我檢核

- [ ] 不看筆記，能用自己的話解釋「為什麼 `metadata_csum` 不能防惡意 image」。
- [ ] 能畫出 mount 流程的四個攻擊面（option parser、superblock/全域 metadata、log replay、runtime 操作）並各舉一個例子。
- [ ] 面試官問「ntfs3 為什麼 bug 比 ext4 多」，你能從成熟度、格式複雜度、log replay 三個角度回答。
- [ ] 能解釋為什麼 CVE-2022-0185 的 `PAGE_SIZE - 2 - size` 檢查失效，以及修補為什麼改成加法。
- [ ] 給你一個 ext4 UAF 的 syzbot 報告，你能判斷它在「unprivileged local」情境下是否可達，依據是什麼。
- [ ] 想一個你自己機器上會自動掛載外接碟的情境，列出三個可行的緩解。

## 延伸閱讀

### 官方文件 / 原始碼

- **[Linux kernel `fs/ntfs3/`（Bootlin Elixir）](https://elixir.bootlin.com/linux/v6.6/source/fs/ntfs3)**
  - **讀哪裡**：`run.c` 的 `run_unpack`、`record.c` 的 attribute 列舉（`mi_enum_attr`）、`index.c`。
  - **為什麼值得讀**：這幾個檔案就是本章「長度/偏移未檢查」模式的原始現場，讀完能直接辨認這類 bug 的樣子。
  - **前提**：會讀 C、知道 `le16_to_cpu` 這類 endian 轉換。
- **[Linux kernel `fs/f2fs/super.c`](https://elixir.bootlin.com/linux/v6.6/source/fs/f2fs/super.c)**
  - **讀哪裡**：`sanity_check_raw_super()` 與 `f2fs_sanity_check_ckpt()`。
  - **學什麼**：一個 fs 如何把「結構驗證」集中成函式，以及每個檢查背後對應過哪個 fuzz bug（`git blame` 這些檢查行）。

### 漏洞資料

- **[Red Hat Bugzilla 2040358 — CVE-2022-0185](https://bugzilla.redhat.com/show_bug.cgi?id=2040358)** 與 **[oss-security 2022-01-18 通報](https://www.openwall.com/lists/oss-security/2022/01/18/8)**
  - **讀哪裡**：通報全文，找「unprivileged user namespace」與 `PAGE_SIZE - 2 - size` 的段落。
  - **和本章的關聯**：fs option parser 型 bug 的標準案例，也是下一章 user namespace 放大攻擊面的前導。
- **[Red Hat CVE-2018-10879 / NVD CVE-2018-10876 頁面](https://nvd.nist.gov/vuln/detail/CVE-2018-10876)**
  - **讀哪裡**：描述句與引用的 kernel bugzilla（bugzilla.kernel.org 上的原始 crafted image 重現步驟）。
  - **學什麼**：「mount 後操作」的重現流程長什麼樣；前提是你能讀懂 ext4 extent tree 基本概念。

### 部落格 / 其他

- **[syzbot dashboard（syzkaller.appspot.com）](https://syzkaller.appspot.com/upstream)**
  - **讀哪裡**：篩選 `ntfs3`、`ext4`、`f2fs` 類別，點開一個 `KASAN: slab-out-of-bounds` 報告看 call trace 與 reproducer。
  - **為什麼值得讀**：一手看 fs bug 的分布與修補速度；對照 [Ch 43](./43-syzkaller.md)。

fs 是「給 kernel 一塊資料讓它自己 parse」的代表；下一章要看的 TTY、keyring、watch_queue、pipe，則是另一種礦區：它們本身的 bug 有限，但它們的 object 是 heap 利用裡最常用的 spray 與原語來源。

→ [Ch 35 TTY / keyring / watch_queue / pipe：經典 spray 與原語來源](./35-tty-keyring-watchqueue.md)
