# Ch 10 — B-tree 與 pager

> **目標**：讀懂執行期的下半——`btree.c` 怎麼把「一堆固定大小的頁」組織成可查找的 **B-tree**（table btree 存資料、index btree 存索引），`pager.c` 怎麼在 B-tree 底下墊一層 **page cache + 交易 + WAL/rollback journal**，讓「改一頁」變成「crash 也不會壞的原子交易」。追一次 `sqlite3PagerGet` → `sqlite3OsRead` 的真實 call chain。這是 `OP_SeekRowid`/`OP_Column`（Ch 9）底下真正碰資料的那兩層。

> **目標codebase**：SQLite `version-3.47.2`（commit `262de1b`）

## 為什麼需要這個？

Ch 9 我們追到 `OP_SeekRowid`：VDBE 說「去 cursor 0 找 rowid==2 的那列」。但 cursor 底下是什麼？磁碟上的 `.db` 檔其實就是**一長串固定大小的頁**（預設 4096 bytes 一頁）。從「一串位元組」到「能 `WHERE id=2` 快速查找」，中間隔著兩層抽象，這章就是拆這兩層：

- **B-tree 層（`btree.c`）**：把那些頁組織成 B+tree，讓「找 key」變成從 root page 往下走幾層的二分查找。它面對的是「頁」，不管頁怎麼來、怎麼寫回、crash 怎麼辦。
- **Pager 層（`pager.c`）**：在 B-tree 底下提供「給我第 N 頁的內容」「我要改第 N 頁」「幫我把這批修改變成一個原子交易」。它管 page cache、鎖、journal/WAL——**ACID 裡的 A（原子性）和 D（持久性）主要在這層實作**。

這是一個經典的**分層解耦**：B-tree 只管「樹怎麼組織」，pager 只管「頁怎麼快取和交易化」。你的 `database_internals` 課是「自己實作一個」，這章是讀「SQLite 這個被驗證了幾十億次的實作怎麼做」——兩者對照著看，收穫最大。

## 先建立直覺

先把「檔案 = 一串頁」這件事刻進腦子：

```
   test.db 檔案（磁碟上）
   ┌────────┬────────┬────────┬────────┬────────┐
   │ page 1 │ page 2 │ page 3 │ page 4 │ page 5 │ ...  每頁 4096 bytes
   └────────┴────────┴────────┴────────┴────────┘
      ▲         ▲
      │         └─ 表 t 的 root page（EXPLAIN 裡的 root=2）
      └─ page 1 特殊：檔頭 100 bytes + sqlite_schema 表
```

page 1 是特別的：前 100 bytes 是資料庫檔頭（magic、page size、encoding…），其後放 `sqlite_schema`（存所有表/索引的定義和它們的 root page 號）。**「表 t 的資料在 root page 2」這個資訊，就是查 `sqlite_schema` 得知的**——這是為什麼 EXPLAIN 的 `OpenRead` 會寫 `root=2`。

B-tree 把這些頁串成一棵樹：

```
   一棵 table B-tree（存資料，key = rowid）
                  ┌─────────────┐
                  │ root (page2)│  內部頁：只存 (key, 子頁號)
                  │ [k<10 | ...]│  導航用
                  └──────┬──────┘
             ┌───────────┴───────────┐
        ┌────▼────┐             ┌─────▼────┐
        │ page 7  │             │ page 9   │  葉頁：存真正的 record
        │rowid1..5│             │rowid6..12│  (rowid → 整列資料)
        └─────────┘             └──────────┘
```

- **table btree**：key 是 rowid（整數），葉頁存整列資料。這是 `SELECT ... WHERE id=?`（id 是 rowid）快的原因——一次樹查找。
- **index btree**：key 是被索引的欄位值，葉頁存「該值 → 對應的 rowid」。`CREATE INDEX` 就是多建一棵這種樹。

pager 墊在最底下：

```
   B-tree 說「給我 page 9 的內容」
        │  sqlite3PagerGet(pager, 9, &page)
        ▼
   ┌──────────────────────────────┐
   │ Pager（page cache）           │
   │  page 9 在 cache 裡嗎？        │
   │   ├─ 在 → 直接回傳（不碰磁碟）  │  ← 熱路徑
   │   └─ 不在 → readDbPage →       │
   │            sqlite3OsRead → 磁碟 │  ← 冷路徑
   └──────────────────────────────┘
```

## 核心一：B-tree 的家底——`BtShared` 與 `MemPage`

打開 `src/btreeInt.h`，B-tree 層的中心 struct 是 `BtShared`（一個開啟的資料庫檔共享一個）：

```c
struct BtShared {
  Pager *pPager;        /* The page cache */          ← 指向下一層 pager
  sqlite3 *db;          /* Database connection currently using this Btree */
  BtCursor *pCursor;    /* A list of all open cursors */
  MemPage *pPage1;      /* First page of the database */
  ...
  u16 maxLocal;         /* Maximum local payload in non-LEAFDATA tables */
  u16 minLocal;         /* Minimum local payload in non-LEAFDATA tables */
  ...
  u32 pageSize;         /* Total number of bytes on a page */   ← 4096
  u32 usableSize;       /* Number of usable bytes on each page */
  u32 nPage;            /* Number of pages in the database */
  sqlite3_mutex *mutex; /* Non-recursive mutex ... */
};
```
（`src/btreeInt.h`，`struct BtShared`，v3.47.2）

第一個欄位 `Pager *pPager` 就是**接縫**：B-tree 要頁時，就找 `pPager`。`maxLocal`/`minLocal` 這對欄位透露一個 B-tree 的核心難點——**一筆 record 太大放不進一頁怎麼辦**（overflow page），SQLite 用 min/max local payload 決定「一筆 cell 在本頁存多少、多的溢位到 overflow 頁」。這是 `btree.c` 之所以 11K 行、最複雜的來源之一（分裂、合併、overflow、平衡）。

**讀 B-tree 的策略**：`reading_code` Ch 11 收斂——別碰分裂/合併/平衡那幾千行（那是「寫入時維護樹形」的邏輯），我們只追**讀路徑**：從 root 走到葉、找到那筆 record。讀路徑的入口就是 Ch 9 的 `OP_SeekRowid` 呼叫的 `sqlite3BtreeTableMoveto`。

## 核心二：一次查找——`sqlite3BtreeTableMoveto`

```c
int sqlite3BtreeTableMoveto(
  BtCursor *pCur,          /* The cursor to be moved */
  i64 intKey,              /* The table key */               ← 要找的 rowid
  int biasRight,
  int *pRes                /* Write search results here */
){
  ...
  /* If the cursor is already positioned at the point we are trying
  ** to move to, then just return without doing any work */
  if( pCur->eState==CURSOR_VALID && (pCur->curFlags & BTCF_ValidNKey)!=0 ){
    if( pCur->info.nKey==intKey ){
      *pRes = 0;
```
（`src/btree.c:5727-5745`，v3.47.2）

開頭就是一個 **fast path**：如果 cursor 已經停在要找的 key 上，直接回傳、不做任何事。這又是 SQLite 到處都是的「先擋掉最常見的 no-op 情況」慣例（和 Ch 9 的 `OP_Column` offset 快取同一種思路）。

真正的查找往下走，會反覆呼叫 `getAndInitPage` 把「下一層要走的子頁」從 pager 撈上來，在頁內做二分查找決定往哪個子頁走，直到葉頁。`getAndInitPage` 就是 B-tree 跨進 pager 的門：

```c
static int getAndInitPage(
  BtShared *pBt,
  Pgno pgno,                      /* Number of the page to get */
  MemPage **ppPage,
  int bReadOnly
){
  ...
  rc = sqlite3PagerGet(pBt->pPager, pgno, (DbPage**)&pDbPage, bReadOnly);
```
（`src/btree.c:2369-2384`，v3.47.2）

看到沒——`getAndInitPage` 的核心就一句 `sqlite3PagerGet(pBt->pPager, pgno, ...)`：「pager 啊，給我第 `pgno` 頁」。**B-tree 完全不知道這頁是從 cache 拿的還是從磁碟讀的、有沒有經過 WAL**——那是 pager 的事。這就是分層的威力：B-tree 只需要「給我某頁」這個抽象。

## 核心三：pager 給頁——`sqlite3PagerGet` 與 page cache

```c
int sqlite3PagerGet(
  Pager *pPager,
  Pgno pgno,          /* Page number to fetch */
  DbPage **ppPage,
  int flags
){
  ...
  /* Normal, high-speed version of sqlite3PagerGet() */
  return pPager->xGet(pPager, pgno, ppPage, flags);
}
```
（`src/pager.c:5707-5726`，v3.47.2）

**這裡有個「你會被騙到」的 indirection**：`sqlite3PagerGet` 本體幾乎沒做事，只是 `return pPager->xGet(...)`——一個函式指標。第一次讀你會困惑「快取邏輯在哪？」。答案：`xGet` 在 pager 開啟時被設成 `getPageNormal`（`src/pager.c:5516`），SQLite 用函式指標在「正常模式」和「錯誤/特殊模式」之間切換 getter。要讀快取邏輯，去 `getPageNormal`，不是 `sqlite3PagerGet`。這是 `reading_code` Ch 23（讀懂 indirection）的活題目——**函式指標把你的直線閱讀切斷了，你得先找出它現在指向誰**。

`getPageNormal` 的核心邏輯（概念）：先問 page cache（`pcache.c`/`pcache1.c`）「這頁在不在記憶體？」——在就直接回（cache hit，熱路徑，完全不碰磁碟）；不在就配一個新的 page frame，呼叫 `readDbPage` 去把它讀進來。

## 底層機制：追一次真實的 disk read call chain

把 Ch 9 到本章串成一條完整的線，這也是**練習 B 要你追的路徑**。從 VDBE 要一欄資料，到真正碰磁碟：

```
OP_SeekRowid (vdbe.c:5426)          「找 rowid==2 的列」
   │
   ▼ sqlite3BtreeTableMoveto (btree.c:5727)   在 B-tree 裡二分查找
   │
   ▼ getAndInitPage (btree.c:2369)            「我需要某一頁」
   │
   ▼ sqlite3PagerGet (pager.c:5707)           → pPager->xGet(...)
   │
   ▼ getPageNormal (pager.c:5516)             查 page cache
   │   ├─ cache hit → 回傳（不碰磁碟）
   │   └─ cache miss ↓
   ▼ readDbPage (pager.c:3018)                真的要讀磁碟了
   │
   ▼ sqlite3OsRead (os.c:88)                  → id->pMethods->xRead(...)
   │
   ▼ unixRead (os_unix.c:3347)                最終 pread() syscall
   │
   ▼ 作業系統把 page 2 的 4096 bytes 讀進記憶體
```

`readDbPage` 的核心（注意它先問 WAL）：

```c
static int readDbPage(PgHdr *pPg){
  Pager *pPager = pPg->pPager;
  ...
  if( pagerUseWal(pPager) ){
    rc = sqlite3WalFindFrame(pPager->pWal, pPg->pgno, &iFrame);  ← WAL 裡有這頁的新版本嗎？
    ...
  }
  if( iFrame ){
    rc = sqlite3WalReadFrame(pPager->pWal, iFrame, ...);          ← 從 WAL 讀
  }else
  {
    i64 iOffset = (pPg->pgno-1)*(i64)pPager->pageSize;            ← 頁號換算成 byte offset
    rc = sqlite3OsRead(pPager->fd, pPg->pData, pPager->pageSize, iOffset);  ← 從主檔讀
  }
```
（`src/pager.c:3018-3040`，v3.47.2）

看那句 `iOffset = (pPg->pgno-1)*pageSize`：**這就是「頁號 → 檔案 byte offset」的換算**（page 1 從 offset 0 起，所以 `-1`）。整個資料庫「檔案是一串頁」的抽象，最終就落在這一行乘法上。`sqlite3OsRead` 再往下一層是純函式指標分派（我們在 Ch 8 看過）：

```c
int sqlite3OsRead(sqlite3_file *id, void *pBuf, int amt, i64 offset){
  DO_OS_MALLOC_TEST(id);
  return id->pMethods->xRead(id, pBuf, amt, offset);
}
```
（`src/os.c:88`，v3.47.2）

`id->pMethods->xRead` 在 Unix 上就是 `unixRead`（`os_unix.c:3347`，內部 `pread`/`read` syscall）。**這一層就是可插拔 VFS 的接縫，Ch 12 專講。**

**這條 call chain 是本 Part 的骨幹**。把它記住，你就有了「一條 SQL 從 text 到 disk」的完整脊椎——上半（text → bytecode）在 Ch 8/9，下半（bytecode → disk）就是這條線。

## 核心四：pager 的另一半——交易與 crash 恢復

pager 不只是快取，它的靈魂是**讓一批頁修改變成原子交易**。SQLite 有兩種機制，pager 都支援，靠 `Pager.journalMode` 切換：

```c
struct Pager {
  sqlite3_vfs *pVfs;          /* OS functions to use for IO */
  ...
  u8 journalMode;             /* One of the PAGER_JOURNALMODE_* values */
  ...
  u8 eState;                  /* Pager state (OPEN, READER, WRITER_LOCKED..) */
  u8 eLock;                   /* Current lock held on database file */
  Pgno dbSize;                /* Number of pages in the database */
  Pgno dbOrigSize;            /* dbSize before the current transaction */
  ...
};
```
（`src/pager.c`，`struct Pager`，v3.47.2）

`eState`（pager 狀態機：OPEN → READER → WRITER_LOCKED → …）和 `eLock`（目前持有的檔鎖）是 pager 最核心的兩個欄位——**pager 本質是一個狀態機**，`reading_code` Ch 24（讀懂狀態機）的實戰目標。兩種交易機制：

**Rollback journal（傳統模式）**：改一頁前，先把該頁的**原始內容**複製到一個 `.db-journal` 檔。改到一半 crash？重開時 SQLite 看到殘留的 journal，把原始內容**倒回**主檔——回到交易前的乾淨狀態。commit 成功則刪掉 journal。核心思想：**先備份舊值，crash 就還原**。

**WAL（Write-Ahead Logging，較新、預設常用）**：反過來——**新值先寫到一個 `.db-wal` 檔**（append-only），主檔暫時不動。讀取時 pager 先問 WAL「這頁有沒有更新的版本」（就是上面 `readDbPage` 那句 `sqlite3WalFindFrame`），有就讀 WAL 的、沒有才讀主檔。之後某個時機 `checkpoint` 把 WAL 的內容合併回主檔。WAL 的好處：讀寫可並行（讀者讀主檔+已有 WAL，寫者 append 新 frame，不互相阻塞）。

```
   Rollback journal                    WAL
   ┌──────────┐                        ┌──────────┐
   │ 改頁前    │                        │ 改頁時    │
   │ 舊值 → journal│                    │ 新值 → wal（append）│
   │ 主檔就地改│                        │ 主檔不動  │
   └──────────┘                        └──────────┘
   crash → journal 倒回主檔            crash → 未 commit 的 wal frame 忽略
   讀寫互斥                            讀寫可並行、checkpoint 才合併回主檔
```

**讀這兩套的策略**：不要一開始就鑽 WAL 的 frame 格式和 checkpoint 演算法（那是 `wal.c` 幾千行）。先抓住「journal = 備份舊值倒回、WAL = 新值先寫別處」這兩句心法，再挑一個（建議 rollback journal，較簡單）順著 `sqlite3PagerCommitPhaseOne`/`pager_playback` 追一次。ACID 的原子性怎麼從「就是備份/前寫」這麼樸素的想法長出來，是這章最值得的收穫。

## 對比與取捨

| | Table B-tree | Index B-tree |
|---|---|---|
| key | rowid（整數） | 被索引欄位的值 |
| 葉頁存什麼 | 整列資料 | 索引值 → rowid |
| 對應 SQL | `WHERE id=?`（rowid 查找） | `WHERE x=?`（有 index 時） |

| | Rollback journal | WAL |
|---|---|---|
| 寫什麼到別的檔 | 改動前的**舊值** | 改動後的**新值** |
| crash 恢復 | journal 倒回主檔 | 忽略未 commit 的 frame |
| 讀寫並行 | 互斥 | 可並行 |
| checkpoint | 不需要 | 需要（把 WAL 合回主檔） |
| 讀碼難度 | 較低，建議先讀 | 較高（`wal.c`） |

## 踩雷集錦

1. **以為 `sqlite3PagerGet` 裡有快取邏輯**：沒有。它只 `return pPager->xGet(...)`（函式指標，正常模式指向 `getPageNormal`）。快取和讀盤在 `getPageNormal`/`readDbPage`。這是函式指標把直線閱讀切斷的經典陷阱——先找出 `xGet` 現在指向誰，再繼續讀。
2. **想把 `btree.c` 從頭讀到尾**：11K 行裡大半是「寫入時維護樹形」（分裂/合併/平衡/overflow）。讀「一條 SELECT 怎麼查」只需要**讀路徑**（`sqlite3BtreeTableMoveto` → `getAndInitPage`），寫路徑先跳過。`reading_code` Ch 11 收斂的硬考驗。
3. **把 B-tree 和 pager 的職責搞混**：B-tree 管「樹怎麼組織、key 怎麼找」，它面對的抽象是「給我某頁」；pager 管「頁怎麼快取、怎麼交易化、crash 怎麼辦」。`sqlite3PagerGet` 這個呼叫就是兩層的分界線。搞清楚職責，你才知道某個 bug/行為該去哪層找。
4. **以為 WAL 是「更好的 journal」、rollback 是過時的**：兩者是不同 trade-off。WAL 讀寫並行、但需要 checkpoint、對某些網路檔案系統不適用；rollback 簡單、跨平台穩。SQLite 兩個都留著讓你選（`PRAGMA journal_mode`）。讀碼別預設「新的一定取代舊的」。
5. **忽略 `readDbPage` 開頭那段 WAL 查詢，以為讀頁就是直接讀主檔**：在 WAL 模式下，一頁的「最新版本」可能在 `.db-wal` 裡而非主檔。`readDbPage` 先 `sqlite3WalFindFrame` 問 WAL、再決定從哪讀。漏看這段，你會誤解 WAL 模式下的讀路徑。

## 進階：再往深一層

- **`sqlite_schema` 是怎麼被讀出來的**：EXPLAIN 裡 `OpenRead` 直接寫 `root=2`，那是因為 prepare 階段 SQLite 已經讀過 `sqlite_schema`（永遠在 page 1 之後）拿到每個表的 root page。開一個全新的 db、下第一條查詢，追 `sqlite3InitOne`（`src/prepare.c`）看它怎麼 bootstrap 讀 schema——這是「資料庫如何認識自己」的高光。
- **page cache 是可插拔的**：`pcache.c` 是介面、`pcache1.c` 是預設實作，兩者用 `sqlite3_pcache_methods2` 這組函式指標解耦——和 VFS 同一個 pattern（Ch 12）。你能塞自己的 page cache 實作。
- **和你 `database_internals` 課的對照讀法**：拿你課裡「自己實作 B-tree」的那一章，和 `btree.c` 並排。你會發現核心思想一致（root→葉的查找、cell 在頁內的佈局），但 SQLite 多了海量的邊界處理（overflow、corruption 偵測、cursor 失效重定位）——**「玩具實作 → 生產實作」的差距，90% 在邊界處理和防禦**，這正好接 Ch 11「防禦式 C」。

## 本章重點整理

- 磁碟上的 `.db` 是**一串固定大小的頁**（預設 4096）；page 1 存檔頭 + `sqlite_schema`（每個表的 root page 號從這查）。
- **B-tree 層（`btree.c`）** 把頁組織成樹：table btree（key=rowid，葉存整列）、index btree（key=索引值，葉存 rowid）。讀路徑入口 `sqlite3BtreeTableMoveto` → `getAndInitPage`。
- **Pager 層（`pager.c`）** 提供「給我某頁」+ page cache + 交易。`sqlite3PagerGet` 是 B-tree/pager 的分界（但真正邏輯在 `xGet`→`getPageNormal`，函式指標 indirection）。
- 完整 disk read call chain：`OP_SeekRowid` → `sqlite3BtreeTableMoveto` → `getAndInitPage` → `sqlite3PagerGet` → `getPageNormal` → `readDbPage` → `sqlite3OsRead` → `unixRead`。頁號換 byte offset 就是 `(pgno-1)*pageSize`。
- 交易兩機制：**rollback journal（備份舊值、crash 倒回）** vs **WAL（新值先 append、crash 忽略未 commit）**。pager 本質是個狀態機（`eState`/`eLock`）。

## 自我檢核

- [ ] 我能畫出「檔案=一串頁 → B-tree → pager cache → 磁碟」的分層，並說出每層的職責邊界
- [ ] 我能默寫從 `OP_SeekRowid` 到 `unixRead` 的完整 call chain（至少講出關鍵幾站）
- [ ] 我能解釋 `sqlite3PagerGet` 為什麼「看起來什麼都沒做」（函式指標），快取邏輯實際在哪
- [ ] 我能用一句話分別講清 rollback journal 和 WAL 的核心思想與 crash 恢復方式
- [ ] 我知道讀 `btree.c` 要先讀「讀路徑」、跳過「寫入時維護樹形」的幾千行

## 延伸閱讀

- **[SQLite Database File Format](https://www.sqlite.org/fileformat2.html)**（官方）
  - **讀哪裡**：Section 1（The Database Header）、Section 1.6（B-tree Pages）。讀完你會知道 page 1 的 100-byte 檔頭每個 byte 是什麼、B-tree cell 在頁內怎麼佈局——`btree.c` 的所有魔法數字都能在這裡查到出處。
  - **前提**：讀完本章。
- **[Write-Ahead Logging](https://www.sqlite.org/wal.html) 與 [Atomic Commit In SQLite](https://www.sqlite.org/atomiccommit.html)**（官方）
  - **讀哪裡**：atomiccommit.html 整頁（rollback journal 怎麼做到原子 commit，圖文並茂）；wal.html 前半（WAL 的讀寫並行模型）。這兩份把「pager 怎麼實作 ACID 的 A、D」講得比讀 `pager.c` 還清楚，配著 code 讀事半功倍。
  - **前提**：讀完本章交易那節。
- **`reading_code` Ch 24「讀懂狀態機與事件驅動」**
  - **讀哪裡**：狀態機的讀法（找 state enum、找 transition）。pager 的 `eState`（OPEN/READER/WRITER_LOCKED…）就是一個教科書狀態機，拿它當練習對象再好不過。
  - **前提**：無。

到這裡，一條 SQL 從 text 到 disk 的完整路徑（Ch 8 地圖 → Ch 9 bytecode → Ch 10 磁碟）已經打通。下一章我們換個視角——不追路徑，而是讀 SQLite 的**風格**：它為什麼敢自稱「地表最可靠的 C 之一」？答案藏在 `assert`/`testcase()`/`ALWAYS`/`NEVER` 這些防禦式巨集和它 100% MC/DC 的測試文化裡。這是一種可遷移的「讀高品質 C」的技能。

→ [Ch 11 讀 SQLite 的防禦式 C](./11-sqlite-defensive-c.md)
