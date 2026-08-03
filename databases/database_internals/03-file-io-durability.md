# Ch 3 — 檔案 I/O 與持久性

> **目標**：搞清楚一個 `write()` 呼叫到底把資料送到哪裡、資料庫為什麼要用固定大小的 page、`fsync` 的真正語意（以及它的謊言），並且親手寫一個能按 page_id 讀寫的 `PageFile`，在 WSL 上跑通 round-trip 測試。

---

## 3.1 page / block 的三個層次

在談 I/O 之前必須把「page」這個詞解開——它在三個不同上下文裡出現，指的是三種粒度，混淆了之後所有關於 I/O 的推理都會錯。

### 3.1.1 磁碟 sector

磁碟（HDD 或 SSD 邏輯模擬的區塊）的最小定址單位是 **sector（磁區）**。傳統 HDD 是 512 B，現代 Advanced Format HDD 與大多數 SSD 是 **4096 B（4 KiB）**。

Sector 是硬體原子性的邊界：一個 sector 的寫入通常是原子的（要嘛整個 sector 寫完，要嘛沒寫）。但跨 sector 的寫入沒有任何保證——斷電就是斷電。

### 3.1.2 OS page（記憶體頁面）

OS 管理實體記憶體的單位。x86-64 預設是 **4 KiB**，可以開 huge page（2 MiB 或 1 GiB）。`mmap`、page cache、TLB 都以這個單位操作。

OS page 和磁碟 sector 在現代硬體上剛好大小一致（都是 4 KiB），但這只是巧合，不要依賴它。

### 3.1.3 資料庫 page

資料庫自己定義的最小 I/O 單位，通常是 **4 KiB / 8 KiB / 16 KiB**：

| 系統 | 預設 page size |
|------|---------------|
| SQLite | 4 KiB |
| PostgreSQL | 8 KiB |
| InnoDB (MySQL) | 16 KiB |
| LevelDB SSTable block | 4 KiB |

為什麼資料庫要自己定義這個邊界，而不是直接用系統呼叫隨意寫？

**原因一：I/O 原子性**。資料庫希望每次寫入的最小單位能以「整個寫完或完全沒寫」的方式落盤。選和磁碟 sector 對齊的大小，能最大化硬體層面的原子性保證（儘管 torn write 問題在 8 KiB page 就出現了，這是 WAL 存在的理由，Ch 17 再談）。

**原因二：I/O 對齊（alignment）**。OS 的 page cache 以 OS page 為單位管理，如果資料庫寫入不對齊，一次邏輯寫入可能讀改寫兩個 OS page，製造額外 I/O。

**原因三：buffer pool 管理**。資料庫自己管理一個 buffer pool（記憶體裡的 page 快取），固定大小讓分配、替換（LRU/Clock）都變成簡單的陣列操作，不需要動態記憶體管理。

### 3.1.4 三個層次的關係

```
應用層（資料庫）
┌────────────────────────────────────────┐
│  DB page 0  │  DB page 1  │  DB page 2  │   ← 資料庫的邏輯分頁 (8 KiB each)
└──────┬──────┴──────┬───────┴──────┬──────┘
       │             │              │
       ▼             ▼              ▼
OS 核心（page cache）
┌────────┬────────┬────────┬────────┬────────┬────────┐
│OS pg 0 │OS pg 1 │OS pg 2 │OS pg 3 │OS pg 4 │OS pg 5 │  ← 4 KiB OS pages
└────────┴────────┴────────┴────────┴────────┴────────┘
       │             │              │
       ▼             ▼              ▼
磁碟
┌──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┬──┐
│s0│s1│s2│s3│s4│s5│s6│s7│s8│s9│..│..│  ← 4 KiB sectors（Advanced Format）
└──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┴──┘
```

一個 8 KiB 的 DB page 對應兩個 4 KiB OS page，再對應兩個 4 KiB sector。寫 DB page 0 時，OS 的 page cache dirty 了 OS pg 0 和 OS pg 1，最終 flush 到 sector 0 和 sector 1。

---

## 3.2 Rust 檔案 I/O

### 3.2.1 OpenOptions：控制 fd 的打開語意

```rust
use std::fs::OpenOptions;

let file = OpenOptions::new()
    .read(true)
    .write(true)
    .create(true)        // 不存在就建立（等同 O_CREAT）
    .open("data.db")?;
```

Rust 的 `OpenOptions` 對應 POSIX `open()` 的 flags：

| Rust 方法 | POSIX flag | 語意 |
|-----------|-----------|------|
| `.read(true)` | `O_RDONLY` / `O_RDWR` | 可讀 |
| `.write(true)` | `O_WRONLY` / `O_RDWR` | 可寫 |
| `.create(true)` | `O_CREAT` | 不存在時建立 |
| `.create_new(true)` | `O_CREAT \| O_EXCL` | 已存在則 error（原子建立） |
| `.truncate(true)` | `O_TRUNC` | 打開時清空 |
| `.append(true)` | `O_APPEND` | 每次寫入都 seek 到尾端（原子追加） |

資料庫的 data file 典型組合是 `read + write + create`（不截斷，不追加），因為我們要隨機讀寫任意 page。

### 3.2.2 Read / Write / Seek

Rust 的核心 I/O trait：

```rust
use std::io::{Read, Write, Seek, SeekFrom};

// Seek：把 file offset 移到特定位置
file.seek(SeekFrom::Start(offset))?;   // 從檔案開頭
file.seek(SeekFrom::End(-1024))?;      // 從檔案尾端往前
file.seek(SeekFrom::Current(0))?;      // 查詢目前位置（不移動）

// Read：從目前 offset 讀，推進 offset
file.read_exact(&mut buf)?;            // 讀滿整個 buf，否則 error

// Write：從目前 offset 寫，推進 offset
file.write_all(&data)?;                // 全部寫完，否則 error（底層 write() 可能部分寫）
```

`read_exact` 和 `write_all` 是重要習慣：底層的 `read()` / `write()` syscall 可能因 signal 中斷或 partial I/O 而傳回比要求的更少位元組，`read_exact` / `write_all` 會自動重試直到完成或真正錯誤。

### 3.2.3 PageFile：按 page_id 讀寫的最小封裝

以下是本課程的第一個核心資料結構，**WSL 實測通過**：

```rust
use std::fs::{File, OpenOptions};
use std::io::{Read, Write, Seek, SeekFrom};

const PAGE_SIZE: usize = 4096;

/// 按 page_id 定址的裸檔案 I/O 封裝。
/// 不含 buffer pool——page 每次都直接讀寫 OS page cache。
struct PageFile {
    file: File,
}

impl PageFile {
    fn open(path: &str) -> std::io::Result<Self> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .open(path)?;
        Ok(PageFile { file })
    }

    /// 把 data 寫到 page_id 對應的位置。
    /// page_id = 0 → byte offset 0；page_id = 1 → byte offset 4096；以此類推。
    fn write_page(&mut self, page_id: u64, data: &[u8; PAGE_SIZE]) -> std::io::Result<()> {
        let offset = page_id * PAGE_SIZE as u64;
        self.file.seek(SeekFrom::Start(offset))?;
        self.file.write_all(data)?;
        Ok(())
    }

    /// 從 page_id 對應的位置讀出一整個 page。
    fn read_page(&mut self, page_id: u64) -> std::io::Result<[u8; PAGE_SIZE]> {
        let offset = page_id * PAGE_SIZE as u64;
        self.file.seek(SeekFrom::Start(offset))?;
        let mut buf = [0u8; PAGE_SIZE];
        self.file.read_exact(&mut buf)?;
        Ok(buf)
    }

    /// 把所有 dirty data 和 metadata 都持久化到非揮發性介質。
    fn sync(&self) -> std::io::Result<()> {
        self.file.sync_all()
    }
}
```

### 3.2.4 邊界：seek 超過檔案尾端再 write

```rust
// 假設檔案現在是空的（長度 0）
// 我們直接寫 page_id = 2，跳過了 page 0 和 page 1
pf.write_page(2, &page2)?;
```

這在 Linux 上完全合法。`lseek()` 允許 offset 超過檔案當前大小；接著 `write()` 時，檔案長度擴展，中間跳過的區間（page 0 到 page 1，共 8 KiB）形成一個 **hole（空洞）**。

讀取 hole 的行為由 POSIX 定義：**讀回全 0**。這是核心對稀疏檔案（sparse file）的保證，不是偶然。`du` 指令會顯示稀疏檔案的磁碟實際佔用遠小於其大小，因為 hole 不佔 block。

對資料庫的意義：預分配一個 1 GB 的稀疏檔案只需要寫一個 block 就能建立邏輯空間，之後按需使用。SQLite 的 WAL 格式就利用了這個特性。

---

## 3.3 page cache（頁面快取）

### 3.3.1 write() 只是寫進 kernel memory

在 Linux 上，`write()` syscall 把資料寫到 **page cache**，然後就回傳成功了。

```
應用程式  ──write()──▶  page cache (kernel DRAM)  ──（非同步）──▶  磁碟
                                ▲
                           dirty bit = 1
```

Page cache 裡的被改過的 page 叫做 **dirty page**。Kernel 保證這個 dirty page 最終會寫到磁碟，但「什麼時候」是由 kernel 決定的，不是應用程式。

### 3.3.2 dirty page 如何 flush

Kernel 有兩個機制把 dirty page 寫回磁碟：

**時間觸發**：`vm.dirty_expire_centisecs`（預設 3000，即 30 秒）。一個 dirty page 存在超過這個時間就會被排進 flush 佇列。

**壓力觸發**：當 dirty page 占記憶體比例超過 `vm.dirty_ratio`（預設 20%），任何 `write()` 都會被阻塞，直到 dirty ratio 降回 `vm.dirty_background_ratio`（預設 10%）。

執行 flush 的是 kernel 背景執行緒 `kworker`（舊版叫 `pdflush`、`kjournald`），不是應用程式的 thread。

```bash
# 查看目前系統 dirty 設定
sysctl vm.dirty_ratio vm.dirty_background_ratio vm.dirty_expire_centisecs
# 查看目前有多少 dirty page
grep -E 'Dirty|Writeback' /proc/meminfo
```

### 3.3.3 對資料庫的意義

**write() 成功 ≠ 資料到磁碟**。斷電後，在 page cache 裡、還沒 flush 的 dirty page 全部丟失。應用程式完全無感知，因為從它的視角，`write()` 已經成功。

這就是資料庫必須主動呼叫 `fsync` 的根本原因。

---

## 3.4 fsync 為何必要，以及它的謊言

### 3.4.1 fsync 做什麼

```c
// POSIX
int fsync(int fd);
```

`fsync` 做兩件事：
1. 把 fd 對應檔案的所有 dirty page 從 page cache 刷到磁碟控制器
2. 發出 **cache flush 命令**給磁碟控制器（ATA Cache Flush / SCSI Synchronize Cache），強制磁碟把自己的 write buffer 寫到非揮發性介質

只有這兩步都完成，`fsync` 才回傳。此時資料對斷電是安全的。

在 Rust 中：

```rust
// sync_all() = fsync()：同步 data + metadata（inode mtime、size）
file.sync_all()?;

// sync_data() = fdatasync()：只同步 data，跳過不影響讀取正確性的 metadata
file.sync_data()?;
```

`fdatasync` 比 `fsync` 少一次 inode 更新，在大量小檔案的場景可以節省不少 IOPS。但對於資料庫的 data file，這個差異通常不重要——瓶頸在 data page 本身。

### 3.4.2 fsync 的謊言

**謊言一：部分 SSD 的 FTL 會撒謊。**

某些消費級（和部分企業級）SSD 的韌體（FTL，Flash Translation Layer）在收到 cache flush 命令後，可能回傳成功但資料仍在 SSD 的 DRAM cache 裡，沒有寫到 NAND flash。如果 SSD 沒有電容（super capacitor）作為斷電保護，這時斷電就是資料丟失。這種行為嚴格說是違反 ATA 規格的，但市面上確實有這樣的產品。判斷方法：查 SSD 規格表是否標注「power-loss protection」。

**謊言二：ext4 data=writeback 模式。**

Linux ext4 有三種 journal 模式：`data=journal`（最安全）、`data=ordered`（預設）、`data=writeback`（最快）。在 `data=writeback` 模式下，fsync 保證 metadata（inode）持久化，但 **data block 的持久化時序不保證**。這意味著 fsync 後斷電，inode 說檔案已經更新，但 data 可能還是舊的，甚至是 garbage。

查看目前掛載的 journal 模式：

```bash
tune2fs -l /dev/sda1 | grep -i journal
dmesg | grep -i 'ext4.*mount'
```

**謊言三：fsync 失敗後 re-open 的 page cache 狀態未定義。**

這是 PostgreSQL 踩到的真實 bug，發生在 2018 年前後（PostgreSQL commit `9ccdd7f`）。

事件序列：
1. PostgreSQL 在後台 writer 中呼叫 `fsync(fd)`，傳回 `ENOSPC`（磁碟空間不足）
2. PostgreSQL 10 之前的版本：收到錯誤，把 fd 關掉，之後重新 open 同一個檔案
3. 問題在於：Linux 的 page cache 是按 inode 而非 fd 管理的。fd 關掉後，page cache 裡仍然有 dirty page，而且錯誤位元（error bit）已經被前一次 `fsync` 消耗掉了
4. 新的 fd open 後再呼叫 `fsync`，可能傳回成功，但 dirty page 仍然沒有落盤
5. PostgreSQL 以為資料已持久化，繼續推進 checkpoint，但磁碟上的資料是舊的

PostgreSQL 10 之後的修法：遇到 `fsync` 回傳任何錯誤，直接 **PANIC**（crash），讓 crash recovery 重跑，而不是靠應用層重試。這個「遇到不確定狀態就 crash」的策略看起來激進，但比「繼續跑但資料不一致」要正確。

參考：[PostgreSQL fsync bug discussion](https://www.postgresql.org/message-id/CAMsr+YHh+5Oq4xziwwoEfhoTZgr07vdGG+hu=1adXx59aTeaoQ@mail.gmail.com)

### 3.4.3 fdatasync vs fsync 的選擇

```
fsync     = data flush + inode flush（mtime、size、atime）
fdatasync = data flush（size 改變時也會更新 inode，但跳過 mtime）
```

資料庫的 WAL 檔案：每次 commit 都要 fsync（或 fdatasync），因為 WAL 是 sequential append，size 必然改變，fdatasync 也會更新 inode size。兩者差異不大，實務上多數 DB 用 fdatasync 減少 overhead。

資料庫的 data file：checkpoint 時才 fsync，不需要每次寫 page 都 sync。

---

## 3.5 O_DIRECT

### 3.5.1 用途與代價

`O_DIRECT` 告訴 kernel：不要用 page cache，讓應用程式的 buffer 直接和磁碟溝通（透過 DMA）。

資料庫自帶 buffer pool，page cache 對它是重複的記憶體（double buffering），浪費實體記憶體也多一次記憶體複製。`O_DIRECT` 讓資料庫完全掌控什麼時候寫磁碟，不依賴 kernel 的 dirty page flush 時序。PostgreSQL 的 `wal_sync_method = open_datasync` 和 InnoDB 的 `innodb_flush_method = O_DIRECT` 都走這條路。

代價：**對齊限制**。`O_DIRECT` 要求：
- buffer 的起始位址對齊 sector size（512 B 或 4096 B，依裝置而定）
- read/write 的 byte count 是 sector size 的倍數
- file offset 也是 sector size 的倍數

不滿足任一條件，`write()` 就會回傳 `EINVAL`。

### 3.5.2 Rust 使用 O_DIRECT

Rust 的 `std::fs` 不直接暴露 `O_DIRECT`，需要透過 `libc` crate 呼叫底層 `open()`。

以下範例**未編譯驗證，理論預期**（需要在 `Cargo.toml` 加 `libc = "0.2"` 依賴）：

```rust
use std::fs::File;
use std::os::unix::io::FromRawFd;
use std::alloc::{alloc, dealloc, Layout};

#[cfg(target_os = "linux")]
fn open_direct(path: &str) -> std::io::Result<File> {
    use std::ffi::CString;
    use libc::{O_RDWR, O_CREAT, O_DIRECT};

    let c_path = CString::new(path).unwrap();
    let fd = unsafe {
        libc::open(
            c_path.as_ptr(),
            O_RDWR | O_CREAT | O_DIRECT,
            0o644,
        )
    };
    if fd < 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(unsafe { File::from_raw_fd(fd) })
}

/// 分配對齊到 align 的 buffer
fn alloc_aligned(size: usize, align: usize) -> (*mut u8, Layout) {
    let layout = Layout::from_size_align(size, align).unwrap();
    let ptr = unsafe { alloc(layout) };
    assert!(!ptr.is_null());
    (ptr, layout)
}
```

使用對齊記憶體寫入：

```rust
const SECTOR_SIZE: usize = 4096;
const PAGE_SIZE: usize = 4096;

// 必須用對齊記憶體，stack 上的 [u8; 4096] 不保證對齊到 4096
let (ptr, layout) = alloc_aligned(PAGE_SIZE, SECTOR_SIZE);
let buf: &mut [u8] = unsafe { std::slice::from_raw_parts_mut(ptr, PAGE_SIZE) };

// ... 填資料後寫入 O_DIRECT fd ...

unsafe { dealloc(ptr, layout) };
```

注意：`std::alloc::Layout` 要求 `align` 必須是 2 的冪，且不超過某個系統最大值。4096 在所有常見平台上都合法。

### 3.5.3 O_DIRECT 不等於更快

這是常見的錯誤直覺。O_DIRECT 繞過 page cache，意味著：
- 沒有 read-ahead（kernel 不會預讀相鄰 page）
- 沒有 write coalescing（每次 write 都直接落盤）
- 沒有 OS 層面的 buffer（你的 buffer pool 大小決定一切）

對於 **sequential scan** 大量資料，page cache 的 read-ahead 往往比 O_DIRECT + 手動預讀快，因為 kernel 的 read-ahead 演算法已經很成熟。O_DIRECT 的優勢在於：buffer pool 已經夠大、不想浪費記憶體在雙重快取上的場景。

---

## 3.6 WAL 概念預告

### 3.6.1 Torn write 問題

假設我們有一個 8 KiB 的 DB page，它跨越兩個 4 KiB 磁碟 sector。寫入時斷電，可能只寫完第一個 sector：

```
寫入前：  [舊資料 sector 0][舊資料 sector 1]
                ↓ 斷電
寫入後：  [新資料 sector 0][舊資料 sector 1]  ← torn write
```

讀回這個 page 得到的是一半新一半舊的混合，checksum 對不上，但我們不知道哪一半是對的，也不知道怎麼修復。這就是 **torn write（撕裂寫入）**問題。

In-place 更新永遠面對這個問題，而且 `fsync` 無法解決它——fsync 只保證「寫到磁碟」，不保證「寫入過程中斷電可以恢復」。

### 3.6.2 WAL 的核心承諾

**Write-Ahead Log（預寫式日誌）**的解法：

1. 把要做的修改先寫成 log record 追加到 WAL 檔案
2. 對 WAL 做 `fsync`
3. 只有 WAL 持久化後，才能改 data page
4. data page 可以不立即 fsync（checkpoint 時再批次做）

WAL 的 log record 是追加（append-only）寫入，每次寫的是一個小 record，遠小於一個 page，不存在 torn write 問題（sector 層面原子）。即使斷電，重啟後從 WAL 重新 apply log record 就能恢復。

這個 pattern 是 Ch 17 的核心。在那之前，我們只需要記住：**先持久化意圖，再執行動作**。

---

## 3.7 最小 PageFile 完整範例

以下是本章的完整實作，包含 round-trip 測試，**WSL 實測通過（cargo test, Rust 1.97.1）**。

```rust
use std::fs::{File, OpenOptions};
use std::io::{Read, Write, Seek, SeekFrom};

pub const PAGE_SIZE: usize = 4096;

/// 以 page_id 定址的裸檔案 I/O 層。
///
/// 這不是 buffer pool——每次 read_page / write_page 都直接走 OS syscall，
/// 讓 OS page cache 決定是否實際觸發磁碟 I/O。
/// 要保證持久性，呼叫者必須在適當時機呼叫 sync()。
pub struct PageFile {
    file: File,
}

impl PageFile {
    /// 打開（或建立）一個 page 檔案。
    pub fn open(path: &str) -> std::io::Result<Self> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .open(path)?;
        Ok(PageFile { file })
    }

    /// 把 data 寫到 page_id 對應的檔案位置。
    ///
    /// 若 page_id 超過目前檔案大小，中間的空洞在 Linux 上讀回全 0。
    pub fn write_page(&mut self, page_id: u64, data: &[u8; PAGE_SIZE]) -> std::io::Result<()> {
        let offset = page_id * PAGE_SIZE as u64;
        self.file.seek(SeekFrom::Start(offset))?;
        self.file.write_all(data)?;
        Ok(())
    }

    /// 從 page_id 對應的位置讀出完整的 PAGE_SIZE bytes。
    pub fn read_page(&mut self, page_id: u64) -> std::io::Result<[u8; PAGE_SIZE]> {
        let offset = page_id * PAGE_SIZE as u64;
        self.file.seek(SeekFrom::Start(offset))?;
        let mut buf = [0u8; PAGE_SIZE];
        self.file.read_exact(&mut buf)?;
        Ok(buf)
    }

    /// 強制把所有 dirty data 和 inode metadata 持久化。
    /// 對應 fsync(2)。
    pub fn sync(&self) -> std::io::Result<()> {
        self.file.sync_all()
    }
}

fn main() {
    println!("PageFile: use read_page / write_page / sync");
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn test_round_trip() {
        let path = "/tmp/test_page_round_trip.db";
        let _ = fs::remove_file(path);  // 清理殘留

        let mut pf = PageFile::open(path).unwrap();

        // 寫 page 0：邊界 byte 填特殊值
        let mut page0 = [0u8; PAGE_SIZE];
        page0[0] = 0xDE;
        page0[1] = 0xAD;
        page0[PAGE_SIZE - 1] = 0xFF;
        pf.write_page(0, &page0).unwrap();

        // 直接寫 page 2，跳過 page 1，製造稀疏檔案空洞
        let mut page2 = [0u8; PAGE_SIZE];
        page2[0] = 0xBE;
        page2[1] = 0xEF;
        pf.write_page(2, &page2).unwrap();

        // sync 保證以上兩個 page 持久化
        pf.sync().unwrap();

        // 讀回並驗證 page 0
        let back0 = pf.read_page(0).unwrap();
        assert_eq!(back0[0], 0xDE);
        assert_eq!(back0[1], 0xAD);
        assert_eq!(back0[PAGE_SIZE - 1], 0xFF);

        // page 1 從未寫過，Linux 稀疏檔案保證讀到全 0
        let back1 = pf.read_page(1).unwrap();
        assert!(
            back1.iter().all(|&b| b == 0),
            "sparse hole should read as zero"
        );

        // 讀回並驗證 page 2
        let back2 = pf.read_page(2).unwrap();
        assert_eq!(back2[0], 0xBE);
        assert_eq!(back2[1], 0xEF);

        let _ = fs::remove_file(path);
    }
}
```

執行：

```bash
mkdir -p /tmp/db_pagetest && cd /tmp/db_pagetest
cargo init --name pagetest
# 把上面的程式碼貼到 src/main.rs
cargo test
# 輸出：test tests::test_round_trip ... ok
```

---

## 3.8 I/O 策略對比

| 策略 | Crash safety | I/O 放大 | 複雜度 | 典型用途 |
|------|-------------|---------|--------|---------|
| `write()` only | 無（斷電丟資料） | 低 | 最低 | 暫存、可重建的 cache |
| `write()` + `fsync()` | 高（保證到磁碟，注意謊言） | 中 | 低 | WAL 持久化 |
| `O_DIRECT` + `fsync()` | 高（繞過 page cache） | 中 | 高（對齊要求） | 資料庫 data file（自帶 buffer pool） |
| `mmap` + `msync()` | 中（msync 前仍靠 kernel flush） | 低（零複製讀） | 中 | 讀多寫少的索引 |

---

## 踩雷

**「write() 回傳成功 = 資料在磁碟」**
錯。`write()` 回傳成功只表示資料在 OS page cache，kernel 的 dirty page flush 是非同步的。不呼叫 fsync，斷電就丟。

**「fsync() 回傳成功 = 資料一定安全」**
錯。部分 SSD 的 FTL 會撒謊，回報 flush 完成但資料在 DRAM cache。ext4 的 `data=writeback` 模式下，fsync 保護 metadata 但不保護 data。PostgreSQL 遇到 fsync ENOSPC 後 re-open 的 bug 也是在這個假設上翻的車。

**「O_DIRECT 一定比 page cache 快」**
錯。O_DIRECT 繞過 read-ahead 和 write coalescing，對 sequential scan 常常更慢。它的優點是消除 double buffering（當應用程式自己管 buffer pool 時），而不是「更快」。

**「seek 到超過檔案尾端再 write 會失敗或 padding 隨機值」**
錯。Linux 允許這個操作，產生稀疏檔案，hole 讀回全 0，不佔磁碟 block。這是 POSIX 規範的行為，可以安全依賴（在 Linux 上）。

**「fdatasync 和 fsync 對資料庫沒有區別」**
不完全對。fsync 多一次 inode 的 mtime 更新，在高頻 fsync 場景（每個事務 commit 都 fsync WAL）累積起來有可觀的 overhead。PostgreSQL 對 WAL 提供 `fdatasync`、`open_sync`、`open_datasync` 三種選項，不是沒有原因的。

---

## 本章重點整理

- 資料庫 page 是應用層自定義的 I/O 原子單位，通常對齊 OS page（4 KiB）以避免讀改寫放大。
- `write()` 只把資料送到 OS page cache，不保證任何持久性。
- Kernel 的 dirty page flush 由 `vm.dirty_expire_centisecs` 和 `vm.dirty_ratio` 控制，對應用程式透明。
- `fsync(fd)` = 把 dirty page 刷到磁碟 + 發 cache flush 命令。Rust 的 `File::sync_all()` 對應 `fsync`，`File::sync_data()` 對應 `fdatasync`。
- fsync 有三個已知謊言：SSD FTL 撒謊、ext4 data=writeback 模式、fsync 失敗後 re-open 的 page cache 狀態未定義。
- `O_DIRECT` 繞過 page cache，需要對齊記憶體和對齊 offset，Rust 需要 `libc` crate 才能使用。
- Torn write 是 in-place 更新的根本問題，fsync 解決不了它，WAL 才能解決。

---

## 自我檢核

- [ ] 我能說清楚 OS page、磁碟 sector、資料庫 page 各是什麼、大小通常是多少。
- [ ] 我能解釋為什麼資料庫用固定大小的 page，而不是可變長度的 record。
- [ ] 我能說出 `write()` 後資料在哪裡、kernel 什麼條件下才會把它寫到磁碟。
- [ ] 我知道 `fsync` 和 `fdatasync` 的差異，以及它在 SSD 上可能無效的情境。
- [ ] 我能說出 PostgreSQL fsync bug 的事件序列（為什麼 re-open 後 fsync 會撒謊）。
- [ ] 我能解釋 torn write 是什麼，以及它和 WAL 的關係。
- [ ] 我能不看筆記地寫出 `PageFile::write_page` 和 `read_page` 的實作。

---

## 延伸閱讀

1. **《Database Internals》Ch 2** — Alex Petrov。B-tree on-disk format、page 結構、page size 選擇的 tradeoff，與本章直接銜接。

2. **《Designing Data-Intensive Applications》Ch 3** — Martin Kleppmann。從更高層次解釋 B-tree 和 LSM 的持久性承諾，適合在讀完本章後建立大圖景。

3. **[PostgreSQL fsync bug report and fix](https://www.postgresql.org/message-id/CAMsr+YHh+5Oq4xziwwoEfhoTZgr07vdGG+hu=1adXx59aTeaoQ@mail.gmail.com)** — PostgreSQL mailing list, 2018。直接閱讀 bug 討論，理解 kernel page cache error propagation 的細節。

4. **`man 2 fsync`** — Linux man pages。注意 NOTES 段落關於 ext4 data=writeback 和 disk cache 的警告，這些不在正文而在 edge case 說明裡，容易被跳過。

5. **[Ensuring data reaches disk](https://lwn.net/Articles/457667/)** — LWN.net, 2011。介紹 fsync、fdatasync、O_DIRECT、barrier 的底層機制，雖然年份較舊但核心機制沒有改變。

---

本章把「磁碟 I/O 的真實語意」說清楚了。下一章在這個基礎上建立 slotted page 的格式——page 裡面怎麼存變長的 record、怎麼管理空閒空間。這才是 buffer pool 之上真正的儲存結構起點。

→ [下一章](./04-page-storage.md)
