# Ch 36 — Block layer：bio、request queue、blk-mq

> **目標**：搞懂檔案系統與實體磁碟驅動之間那一層——block layer——在做什麼。學完你能說出一個 page cache miss 怎麼變成一個 `bio`、`bio` 怎麼被 blk-mq 排隊/合併/送進 NVMe 或 SATA 驅動、I/O scheduler 在哪裡插手，並能用 `blktrace`、`iostat`、`/sys/block/*/queue/` 在真機上看這條路每一段真的怎麼跑。

這一章是 Part 6（儲存與檔案系統）的收尾。前面 Ch 33 講 VFS 四物件、Ch 34 穿了一次 `read()` 從 syscall 到磁碟的完整線、Ch 35 寫了一個純記憶體的檔案系統（根本不碰磁碟）。現在我們補上 Ch 34 裡刻意含糊帶過的那一段——「建 bio → block layer → 送裝置 → 磁碟完成 IRQ 喚醒」——把 `read()` 的下半段拆開看。

## 為什麼需要這個？

Ch 21 的 page cache 命中是純記憶體搬運，微秒級。但 cache miss 呢？readahead 決定「要讀哪幾個 page」之後，總得有人真的去跟磁碟要資料。這個「有人」就是 block layer。

一句話的理解（「miss 就去讀磁碟」）在下面這些問題面前立刻不夠用：

- 為什麼同樣是 SSD，把 I/O scheduler 從 `bfq` 換成 `none`，資料庫的尾延遲會差一倍？
- 一百個 process 同時對同一顆磁碟發 I/O，kernel 憑什麼決定誰先誰後？
- 為什麼循序讀比隨機讀快那麼多，即使在「沒有磁頭」的 SSD 上也還是有差？
- `dd` 讀磁碟時 `iostat` 那一排欄位（`aqu-sz`、`rareq-sz`、`%util`）到底在量什麼、哪個爆了代表卡在哪？
- 為什麼一個卡在讀磁碟的 process `kill -9` 殺不掉、`ps` 顯示 `D`？

這些答案都在 block layer——檔案系統之下、裝置驅動之上那一層——怎麼把請求排隊、合併、送硬體、等完成裡。

先想清楚它解決什麼問題。檔案系統（Ch 33/35）眼中的世界是「inode 的第 N 個邏輯 block」。磁碟眼中的世界是「LBA（logical block address）第 M 個 sector，一個 sector 傳統上 512 bytes」。這兩個世界之間隔著一堆事情：

- **翻譯**：檔案系統說「讀 inode 42 的 block 7」，經過 extent / block mapping 算出「這對應磁碟 sector 8192~8199」。這一步是檔案系統做的，算完就交給 block layer。
- **排程與合併**：如果同時有一百個 I/O 請求進來，其中十個剛好是磁碟上相鄰的 sector，把它們併成一個大請求可以省掉九次「重新定位磁頭」的 seek。旋轉磁碟的 seek 是毫秒級，能省就省。
- **抽象掉硬體差異**：檔案系統不該關心底下是 SATA SSD、NVMe、還是 USB 隨身碟。block layer 給檔案系統一個統一介面（丟 `bio` 進來），底下對接各種裝置驅動。

沒有 block layer 會怎樣？每個檔案系統都得自己會講 NVMe 協定、自己排程 I/O、自己合併請求——五個檔案系統乘上十種磁碟就是五十份重複又難維護的程式碼。block layer 把「怎麼跟一塊 block 裝置打交道」這件事抽出來，收成一層。

> **block 裝置 vs char 裝置**（Ch 38 會細講）：block 裝置（磁碟）可以隨機定址、以固定大小的 block 為單位存取、而且中間墊了一層 page cache；char 裝置（序列埠、鍵盤）是 byte 流、通常不能隨機定址、不經 page cache。這一章講的整套機制只適用 block 裝置。

## 先建立直覺

先把整層的分層圖釘在腦子裡。這是 Ch 34 那張大圖的「下磁碟」那一段的放大：

```
  檔案系統（ext4 / xfs / Ch 35 的 mini fs）
        │  「讀 inode 的 block 7」→ 算出 sector 8192，要放進哪幾個 page
        ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │  bio  = 一次 block I/O 的描述                                      │
  │  ┌──────────────┐   bi_iter: 目標 sector 8192、長度               │
  │  │ struct bio   │   bi_bdev:  哪個 block 裝置                      │
  │  │  bi_io_vec ─────┐ bi_opf:   READ / WRITE / flags                │
  │  └──────────────┘  │                                              │
  │      bio_vec 陣列   ▼   每個 bio_vec = (page, offset, len)          │
  │   [0] ─► page A （Ch 17 的實體頁，通常是 page cache 的某個 folio）  │
  │   [1] ─► page B                                                    │
  │   [2] ─► page C                                                    │
  └─────────────────────────────────────────────────────────────────┘
        │  submit_bio(bio)
        ▼
  ┌─────────────────────── blk-mq（multi-queue block layer）─────────┐
  │                                                                   │
  │  bio ─► 包成 struct request（可合併多個 bio）                       │
  │                                                                   │
  │   per-CPU software queue（接 Ch 7 per-CPU）                        │
  │   ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐                     │
  │   │ CPU0 sw│ │ CPU1 sw│ │ CPU2 sw│ │ CPU3 sw│  ← 提交端無全域鎖    │
  │   └───┬────┘ └───┬────┘ └───┬────┘ └───┬────┘                     │
  │       │          │  I/O scheduler（mq-deadline / bfq / none）      │
  │       └────┬─────┴────┬─────┴─────────┘                           │
  │            ▼          ▼                                            │
  │      hardware dispatch queue（對應裝置的硬體佇列）                  │
  │      ┌──────────┐  ┌──────────┐                                   │
  │      │ hctx 0   │  │ hctx 1   │  ← NVMe 有幾條硬體佇列就有幾個      │
  │      └────┬─────┘  └────┬─────┘                                   │
  └───────────┼─────────────┼─────────────────────────────────────────┘
              ▼             ▼
       裝置驅動（nvme / ahci-SATA / virtio-blk）
              │  把 request 翻成硬體指令，設定 DMA（Ch 41）
              ▼
        硬體（NVMe SSD / SATA 磁碟）
              │  ……傳輸……傳完發完成中斷（Ch 29）
              ▼
        IRQ handler → blk_mq_complete_request → 喚醒等待的 process（Ch 26）
```

三個心智錨點：

1. **`bio` 是 block I/O 的原子描述**。它說的是「把這幾個記憶體頁（`bio_vec` 陣列，每個指向一個 Ch 17 的 page）讀/寫到裝置的哪個 sector 範圍」。注意方向：`bio` 裡的 page 就是 page cache 的頁；讀是「裝置 → page」，寫是「page → 裝置」。
2. **blk-mq 的關鍵字是「per-CPU 提交」**。提交端每個 CPU 有自己的 software queue，彼此不搶鎖（對比舊架構的全域鎖，下面講）。這是為了餵飽 NVMe 這種能同時吃幾十萬 IOPS 的硬體。
3. **I/O scheduler 夾在中間**。它決定「software queue 裡的請求以什麼順序、要不要合併、要不要延遲」送到 hardware queue。旋轉磁碟要它幫忙排（省 seek），NVMe 常常直接 `none`（沒 seek 可省，排反而是開銷）。

三個名詞先分清楚，後面通篇會用，混了會讀不下去：

| 名詞 | 是什麼 | 誰的語言 |
|---|---|---|
| **sector** | 磁碟定址的最小單位，傳統 512 bytes（`bi_sector` 用它計數） | 硬體 / 裝置 |
| **bio** | 一次 block I/O 的描述：一段 sector 範圍 ↔ 一組 page | 檔案系統 → block layer |
| **request** | 一或多個合併過的 bio，一次要送給裝置的操作，帶一個 tag | block layer → 驅動 |

流向是 `bio` 進來、合併成 `request`、`request` 帶 tag 送驅動、驅動翻成對 `sector` 的硬體指令。「上層講 bio、下層講 request、硬體講 sector」——記住這條翻譯鏈，源碼裡函式名帶 `bio_` 還是 `rq_`/`request_` 你就知道自己站在哪一層。

## bio：block I/O 的基本單位

`bio` 定義在 `include/linux/blk_types.h` 的 `struct bio`（不是 `bio.h`——`bio.h` 放的是操作 bio 的函式，結構本體在 `blk_types.h`；這是常見的找不到的點）。挑幾個關鍵欄位看：

```c
struct bio {
    struct block_device *bi_bdev;   // 目標 block 裝置
    blk_opf_t            bi_opf;     // 操作 + flags：REQ_OP_READ/WRITE | REQ_SYNC...
    struct bvec_iter     bi_iter;    // 目前進度：起始 sector（bi_sector）+ 剩餘長度
    bio_end_io_t        *bi_end_io;  // I/O 完成時的 callback
    struct bio_vec      *bi_io_vec;  // ← 核心：描述記憶體那端的 (page, offset, len) 陣列
    unsigned short       bi_vcnt;    // bi_io_vec 陣列有幾個元素
    ...
};
```

`bio_vec`（`include/linux/bvec.h`）長這樣，一個元素描述「記憶體裡一段連續的資料」：

```c
struct bio_vec {
    struct page *bv_page;    // 哪一個實體頁（Ch 17）
    unsigned int bv_len;     // 這段有幾 bytes
    unsigned int bv_offset;  // 頁內偏移
};
```

所以一個 `bio` 描述的是一件事的兩端：**裝置端**是一段連續的 sector 範圍（`bi_iter.bi_sector` + 長度），**記憶體端**是一組 `(page, offset, len)`（`bi_io_vec` 陣列）。這組 page 在記憶體裡不需要連續——這正是 `bio_vec` 陣列存在的理由：磁碟上連續的一段資料，可以散落在記憶體裡好幾個不相鄰的 page（scatter-gather I/O）。DMA 引擎（Ch 41）也支援 scatter-gather，剛好對上。

**bio 從哪來？** 接 Ch 21 / Ch 34。page cache miss 時，readahead（`mm/readahead.c`）決定「本頁 + 後面幾頁一起讀」，配置好對應的 folio（一組 page），呼叫檔案系統的 `address_space_operations->readahead`（例如 ext4）。檔案系統把邏輯 block 對應到磁碟 sector，用 `bio_alloc` 之類的介面配一個 `bio`、把那些 folio 的 page 掛進 `bi_io_vec`、填好目標 sector，然後 `submit_bio(bio)` 丟給 block layer。這就是 Ch 34 那張圖裡「建 bio ──► block layer」那一格的實際內容。

`submit_bio()`（`block/blk-core.c`）是檔案系統交棒給 block layer 的門。往下它會走到 `submit_bio_noacct` → `blk_mq_submit_bio`（`block/blk-mq.c`），進入 blk-mq 的世界。

用最小骨架看一個 bio 怎麼被組出來、丟出去（這是驅動與檔案系統程式碼裡到處可見的樣板，練習 E 你會自己寫接收端）：

```c
struct bio *bio = bio_alloc(bdev, nr_pages, REQ_OP_READ, GFP_KERNEL);
bio->bi_iter.bi_sector = 8192;          // 目標：磁碟 sector 8192（LBA）
bio_add_page(bio, page, PAGE_SIZE, 0);  // 掛一個 page 進 bi_io_vec（可呼叫多次掛多頁）
bio->bi_end_io = my_read_endio;         // I/O 完成時 kernel 回呼這裡
submit_bio(bio);                        // 交給 block layer，函式立刻返回（非同步）
```

三個重點：`bio_add_page` 每呼叫一次就多一個 `bio_vec`（記憶體端可散落多頁）；`bi_sector` 是裝置端的起點；`submit_bio` 是**非同步**的——它不等 I/O 完成就返回，完成的通知走 `bi_end_io` callback（在磁碟中斷的 context 裡跑，Ch 29）。「提交」和「完成」在時間與 CPU context 上是分開的兩件事，這是理解 block layer 一切非同步行為的起點。

### bio 的 split 與 merge

`bio` 不是原封不動送進硬體的，中間可能被拆、被併：

- **split**：一個 `bio` 可能太大，超過裝置的限制（例如單次 DMA 最大長度、`max_sectors`、或跨越硬體不允許的邊界）。block layer 會用 `blk_mq_split_bio` / `bio_split` 把它切成幾個符合限制的小 bio。裝置的限制放在 `struct queue_limits`（`include/linux/blkdev.h`），可透過 `/sys/block/<dev>/queue/max_sectors_kb` 等看到。
- **merge**：反過來，如果新來的 `bio` 目標 sector 剛好接在佇列裡某個既有 request 的尾巴（back merge）或頭（front merge），block layer 會把它併進去，變成一個更大的 request。對旋轉磁碟這是關鍵優化——一次 seek 讀更多資料。合併發生在 `blk_mq_sched_bio_merge` / `blk_attempt_plug_merge` 等處。

split 和 merge 是 block layer 存在感最強的兩件事：它站在檔案系統一堆零碎請求，和硬體一次能吞多少的限制之間，做整形。

merge 分兩種，值得分清楚：

- **back merge**：新 bio 的起始 sector 剛好等於既有 request 的「結束 sector」，接在尾巴。這是最常見的一種——循序讀寫時後一塊接前一塊，一路 back merge 併成大 request。
- **front merge**：新 bio 的結束 sector 剛好等於既有 request 的「起始 sector」，接在頭。較少見（要往回接），有些 scheduler 甚至不做 front merge。

判斷能不能合併，除了 sector 相鄰，還要滿足裝置限制（合併後不能超過 `max_sectors`、`max_segments`——一個 request 能帶幾個不連續記憶體段）。這也是為什麼 `queue_limits` 的每個欄位都可能讓一次合併失敗——block layer 一邊想併大、一邊被硬體上限卡住，`max_sectors_kb` 調大常能提升循序吞吐就是這個道理。

## request queue 與 blk-mq：為什麼要多佇列

### 舊架構的問題

blk-mq 之前（大約 3.13 引入、5.0 起成為唯一路徑），block layer 是**單佇列**的：每個 block 裝置一個 `struct request_queue`，配一把 `queue_lock` 自旋鎖。所有 CPU 提交 I/O、I/O scheduler 排序、驅動取請求，全部要搶這一把鎖。

在旋轉磁碟時代這沒問題——磁碟本來就慢（幾百 IOPS），鎖的爭用不是瓶頸，瓶頸是磁頭 seek。但 SSD、尤其 NVMe 出現後局面翻轉：

- NVMe 一顆能做幾十萬到上百萬 IOPS，且**硬體本身就有多條佇列**（NVMe 規格支援最多 64K 個 submission/completion queue pair，通常每個 CPU 綁一條）。
- 這麼高的 IOPS 下，那把全域 `queue_lock` 變成擠死的門——幾十個 CPU 排隊搶一把鎖去提交 I/O，鎖爭用（cacheline bouncing，接 Ch 23）本身就吃掉大半 CPU，硬體的並行能力完全發揮不出來。

一句話：**單佇列 + 全域鎖是為慢速旋轉磁碟設計的，撞上快速多佇列硬體就成了瓶頸**。

### blk-mq 的設計

blk-mq（multi-queue block layer，`block/blk-mq.c`）用兩層佇列拆掉全域鎖：

- **software staging queue（`blk_mq_ctx`）**：**per-CPU**（接 Ch 7 的 per-CPU 思路）。每個 CPU 提交 I/O 時放進自己的 software queue，不跟別的 CPU 搶鎖。提交路徑因此無鎖爭用——這是 blk-mq 拿掉全域鎖的核心手段。
- **hardware dispatch queue（`blk_mq_hw_ctx`，簡稱 hctx）**：對應**裝置的硬體佇列**。NVMe 有幾條硬體佇列，就配幾個 hctx。software queue 裡的請求最終被 dispatch 到 hctx，再由驅動送進對應的硬體佇列。

`blk_mq_ctx`（software）到 `blk_mq_hw_ctx`（hardware）的映射由 `blk_mq_map_queues` 建立，通常照 CPU 拓樸把 per-CPU 的 software queue 分配給硬體佇列。理想狀況是「CPU N 提交的 I/O，走 CPU N 綁的硬體佇列，完成中斷也回到 CPU N」——提交、傳輸、完成全在同一顆 CPU 的 cache 上，不跨核，把 cacheline bouncing 降到最低。

具體想像一台 4 核機器配一顆有 4 條硬體佇列的 NVMe：blk-mq 會建 4 個 software queue（每 CPU 一個）+ 4 個 hctx（每硬體佇列一個），一對一綁。CPU2 上的 process 讀檔案，I/O 進 CPU2 的 software queue、dispatch 到 hctx2、送進 NVMe 的第 2 條硬體佇列，NVMe 的第 2 條完成佇列的中斷也導向 CPU2。整條路不碰其他 CPU 的 cacheline，四顆核可以完全並行地各跑各的 I/O——這就是 blk-mq 能餵飽 NVMe 的原因。反觀舊架構：四顆核全擠一把 `queue_lock`，同一時間只有一顆核能碰佇列，NVMe 四條硬體佇列有三條在餓肚子。

軟硬佇列不一定一對一。硬體佇列比 CPU 少時（便宜的 NVMe 可能只有 1~2 條），多個 software queue 會映射到同一個 hctx，那個 hctx 就成了那幾顆 CPU 的匯流點——這時提交端仍是 per-CPU 無鎖，但 dispatch 到硬體那一步會有一定程度的收斂。`nr_hw_queues`（`/sys/block/*/queue/`）看得到實際幾條。

### struct request 與 tag

`bio` 進了 blk-mq 會被包成 `struct request`（`include/linux/blk-mq.h`）。一個 `request` 可以帶多個合併過的 bio（`req->bio` 是一串），代表「一次要送給裝置的 I/O 操作」。`bio` 是檔案系統/上層的語言，`request` 是 block layer 對驅動的語言。

**tag** 是 blk-mq 的另一個關鍵機制。每個 hardware queue 有一組固定數量的 tag（一個 bitmap，`sbitmap`），一個 tag 就是一個「in-flight 請求的槽位」。要提交 request 得先搶到一個 tag；沒 tag 就代表硬體佇列滿了，得等。tag 同時當作 request 的識別碼——硬體完成時回報 tag，驅動用 tag 就能 O(1) 找回對應的 `request`（`blk_mq_tag_to_rq`），不用去佇列裡線性搜。tag 的總數就是這條佇列的 queue depth，透過 `/sys/block/<dev>/queue/nr_requests` 可以看到/調整。

為什麼用 tag 而不是把 request 串成鏈結串列排隊？因為硬體佇列本身就是「一組編號的槽位」。NVMe 的 submission queue 就是一個環狀陣列，每個 slot 有索引。tag 直接對應這個硬體槽位索引，提交時「搶 tag = 佔一個硬體槽」，完成時硬體回報槽位索引 = 回報 tag。整個 in-flight 追蹤退化成一個 bitmap 的 set/clear + 一次陣列索引，沒有鏈結串列走訪、沒有鎖住整條佇列去搜尋。`sbitmap`（scalable bitmap）還把 bitmap 切成多段、每段配一把鎖，連搶 tag 這一步都盡量分散、減少爭用——又是一次「per-CPU/分段化去掉全域鎖」的體現。

### dispatch：request 怎麼真的到驅動

request 進了 software queue 不代表馬上送硬體。dispatch 這一步由 `blk_mq_run_hw_queue`（`block/blk-mq.c`）驅動：它把 hctx 對應的 software queue（經 I/O scheduler 排序後）的 request 取出，逐個呼叫驅動註冊的 `->queue_rq`（`struct blk_mq_ops` 的成員）。`queue_rq` 是驅動的心臟——nvme 驅動在這裡把 `request` 翻成 NVMe 指令、填進 submission queue、敲門鈴（doorbell）通知硬體；ahci（SATA）驅動在這裡填 command slot。dispatch 可能同步發生（提交時順路把佇列跑一遍）或被延後（透過 workqueue，Ch 30）在別的 context 跑，取決於當下能不能拿到 tag、驅動回不回 busy。這一層是 blk-mq 通用框架與具體裝置驅動的接縫：上面所有 bio/request/tag/scheduler 都是裝置無關的，`queue_rq` 之後才是 NVMe 或 SATA 各自的協定。

## I/O scheduler：電梯演算法還在不在

I/O scheduler 決定 software queue 裡的請求以什麼順序、要不要合併、要不要延後，才 dispatch 到 hardware queue。v6.12 的 blk-mq 世界裡主要有四個：

| scheduler | 適合 | 做什麼 |
|---|---|---|
| `none` | NVMe / 快速 SSD | 不排序，FIFO 直接 dispatch。沒 seek 可省時，排序純屬開銷 |
| `mq-deadline` | 一般 SATA SSD / 混合負載 | 給每個請求一個 deadline，避免某個請求被餓死；讀優先於寫；輕度合併 |
| `bfq` | 桌面 / 互動負載、旋轉磁碟 | Budget Fair Queueing，按 process 分配 I/O 頻寬，追求互動延遲公平 |
| `kyber` | 高速裝置、多佇列 | 用延遲目標做節流，輕量，適合快速裝置 |

為什麼旋轉磁碟需要「電梯演算法」（elevator algorithm）？磁頭在碟片上移動，seek 是毫秒級的機械動作。如果請求亂序來（sector 100、5000、200、6000），磁頭來回甩，慢死。電梯演算法像大樓電梯——不按叫的先後跑，而是順著一個方向掃過去，路過的樓層順便停。把請求按 sector 排序後掃一遍，seek 總距離大幅縮短。這是舊 block layer 的 `cfq`/`deadline`/`noop` 的年代主旋律。

**為什麼 NVMe 常用 `none`？** NVMe 是純電子、隨機存取，沒有磁頭、沒有 seek penalty——sector 100 和 sector 6000 的存取成本一樣。這時電梯演算法排序不但沒好處，排序本身的 CPU 開銷還拖慢了本來能跑幾十萬 IOPS 的裝置。所以現代 NVMe 預設常是 `none`（或輕量的 `kyber`）。這正好呼應 `linux_commands` 課裡看過的 `/sys/block/*/queue/scheduler`——那個檔案第一個項就是可選的 scheduler，中括號 `[]` 標的是當前生效的：

```
$ cat /sys/block/nvme0n1/queue/scheduler
[none] mq-deadline kyber bfq
```

換 scheduler 就是 `echo mq-deadline > /sys/block/nvme0n1/queue/scheduler`（要 root）。這是最直接能感受 block layer 的地方——換一個排程策略，用 `fio` 打不同負載，看延遲/吞吐怎麼變。

## plug/unplug：批次提交

如果每個 `bio` 一來就立刻 dispatch 到硬體，會錯過合併的機會，也讓每次提交都付一次「進 blk-mq 核心邏輯 + 可能碰硬體」的固定開銷。**plugging** 是個攢批的優化。

想像水管的塞子：一段時間內把 request 攢在一個 per-task 的暫存清單（`struct blk_plug`，`include/linux/blkdev.h`）裡，不急著送硬體；等攢夠了或該送了（unplug），一次性 flush 出去。攢批期間相鄰的 bio 有機會互相 merge，且一次 dispatch 攤平了固定開銷。

流程長這樣：

```
blk_start_plug(&plug);          // 塞上塞子（通常在 read/write 路徑起點）
  ... 這段期間的 submit_bio 會攢進 plug->mq_list，期間嘗試 plug merge ...
blk_finish_plug(&plug);         // 拔塞子 → blk_mq_flush_plug_list 一次送出
```

`blk_start_plug` / `blk_finish_plug` 定義在 `block/blk-core.c`。實際的檔案系統讀寫路徑（例如 `filemap` 的 readahead、writeback）會在外層包上 plug，讓一批 I/O 攢在一起提交。plug 也會在快睡著（`io_schedule` 之前）自動 flush，避免請求卡在 plug 裡卻讓 process 去睡等一個還沒送出的 I/O——那會死等。

## 底層機制：一次 read miss 的完整下半段

現在把 Ch 34 那條線的下半段補齊，從 page cache miss 一路到資料回來、process 被喚醒。這是 Ch 34「慢路徑」那一欄的展開：

```
  (Ch 21) filemap_read 查 page cache ─► MISS（folio 不在或非 uptodate）
        │
        ▼
  (Ch 21) readahead：決定讀本頁 + 後幾頁，配好 folio（一組 page）
        │  呼叫 a_ops->readahead（ext4 等）
        ▼
  檔案系統：邏輯 block → 磁碟 sector，bio_alloc 配 bio，
            把 folio 的 page 掛進 bi_io_vec，設好 bi_sector、bi_end_io
        │
        ▼
  submit_bio(bio)  ──►  blk_mq_submit_bio          (block/blk-mq.c)
        │
        ├─ 可能 bio_split（超過裝置限制就切）
        ├─ 嘗試 plug merge / scheduler merge（相鄰請求併起來）
        ├─ 包成 struct request，搶一個 tag
        ▼
  放進 per-CPU software queue → I/O scheduler → dispatch 到 hctx
        │
        ▼
  驅動 queue_rq（nvme / ahci）：把 request 翻成硬體指令
        │  設定 DMA 描述子（Ch 41），指向 bio_vec 的那些 page
        ▼
  ─────── 此時上層 process 呼叫 io_schedule 去睡（TASK_UNINTERRUPTIBLE，Ch 9/26）
          這就是 ps 看到的 D 狀態——kill -9 也叫不醒，因為它在等硬體
  ───────
        │  ……NVMe/SATA 硬體透過 DMA 把 sector 資料搬進那些 page……
        ▼
  傳輸完成 → 硬體發完成中斷（Ch 29 的 top half）
        │
        ▼
  IRQ handler → blk_mq_complete_request → bio 的 bi_end_io callback
        │  標記 folio uptodate（資料現在有效了）
        │  歸還 tag
        ▼
  喚醒等這個 folio 的 process（folio unlock → wake_up，Ch 26）
        │
        ▼
  (Ch 34) process 醒來，回到 filemap_read，folio 現在 uptodate
        │  copy_folio_to_iter → copy_to_user，read() 回傳
```

幾個接點要對上前面章節：

- **睡在哪、被誰喚醒**：process 送出 I/O 後不是忙等，是 `io_schedule` 讓出 CPU 去睡（Ch 9 的 `TASK_UNINTERRUPTIBLE`）。喚醒它的是**磁碟完成中斷**（Ch 29 的中斷路徑）觸發的 `bi_end_io` callback。這就是為什麼卡在 I/O 的 process 是 `D` 狀態、`kill -9` 殺不掉——它在核心裡等一個硬體事件，signal 送不進去。這一段在 Ch 34 講過使用者視角，這裡看的是 block layer 這端怎麼觸發喚醒。
- **DMA 直接寫進 page cache 的頁**：驅動設定 DMA（Ch 41）時，目標位址就是 `bio_vec` 裡那些 page 的實體位址。硬體把資料直接搬進 page cache 的頁，CPU 全程不用逐 byte 搬——這是 block I/O 快的關鍵之一。
- **completion 也要回對 CPU**：blk-mq 盡量讓完成中斷回到當初提交的那顆 CPU（`blk_mq_complete_request` 可能透過 IPI 導向），維持提交/完成同核，少跨核 cache 抖動。

## 動手：用工具看 block layer 真的怎麼跑

這一層看不見摸不著，但有一整套工具能把它照出來。以下都在你 Ch 0 的 QEMU 環境或任何 Linux 真機上能做（QEMU 裡 `-drive` 掛一個 virtio-blk 磁碟就有 `/dev/vda` 可玩）。

### iostat：看每個裝置的 I/O 統計

```bash
iostat -x 1 /dev/nvme0n1
```

`-x` 給擴充欄位。重點看：

- `r/s`、`w/s`：每秒讀/寫次數（IOPS）
- `rareq-sz`、`wareq-sz`：平均每次 I/O 的大小（KB）——太小代表沒合併好
- `aqu-sz`（average queue size）：佇列裡平均堆了幾個請求——持續很大代表裝置吃不消
- `%util`：裝置忙碌比例——接近 100% 代表 I/O 飽和（但對能並行的 NVMe，`%util` 100% 不一定代表滿載，這是個 NVMe 上會誤導的老指標）

### blktrace / blkparse：追每一個 bio

`blktrace`（接 `observability_tools` 課）是 block layer 的顯微鏡，記錄每個 request 在 block layer 各階段的事件：

```bash
# 一個終端跑 blktrace 收集裝置事件
sudo blktrace -d /dev/nvme0n1 -o trace

# 另一個終端製造 I/O
dd if=/dev/nvme0n1 of=/dev/null bs=4k count=1000 iflag=direct

# Ctrl-C 停止 blktrace 後，解析
blkparse -i trace
```

`blkparse` 輸出的事件代號就是 block layer 的生命週期，值得認得幾個：

- `Q`（Queued）：bio 進入 block layer
- `G`（Get request）：配到一個 request
- `M`（Merge）：這個 bio 被併進既有 request——看到 M 代表合併生效了
- `I`（Inserted）：request 插入 I/O scheduler
- `D`（Dispatched）：request 送給驅動/硬體
- `C`（Completed）：I/O 完成（硬體回來了）

`Q → C` 的時間差就是這個 I/O 的端到端延遲。看到一堆 `M` 代表你的負載合併得很好；看到 `Q` 後很久才 `D` 代表卡在排程/佇列。想更高階可以用 `btt`（blktrace 的分析工具）算各階段平均耗時（`Q2D` = 進 block layer 到送硬體、`D2C` = 硬體實際處理，兩者一拆你就知道延遲是花在 kernel 排隊還是裝置本身）。

### biosnoop：用 eBPF 看每個 I/O（接 bpf 課）

`blktrace` 之外，block layer 有一組 tracepoint（`block:block_rq_issue`、`block:block_rq_complete` 等），bcc/bpftrace 的 `biosnoop` 就掛在上面，能逐一印出「哪個 process、對哪個裝置、讀/寫、幾 KB、延遲多少」：

```bash
sudo biosnoop            # bcc 版；或 bpftrace 一行版掛 block_rq_complete
# PID    COMM   DISK    T SECTOR    BYTES  LAT(ms)
# 3821   dd     nvme0n1 R 8192      4096   0.23
```

比 `blktrace` 好處是直接把 I/O 歸因到 process（`blkparse` 只看得到裝置層），排查「是哪個程式在狂打磁碟」時更直接。這正是 `bpf` 課裡 block tracepoint 的實戰用法——概念在本章，工具在那門課。

### /sys/block/<dev>/queue/：看與調參數

這是 block layer 對外的旋鈕面板：

```bash
cd /sys/block/nvme0n1/queue

cat scheduler        # 當前與可選的 I/O scheduler，[none] mq-deadline ...
cat nr_requests      # 佇列深度（tag 數量），能 in-flight 幾個 request
cat max_sectors_kb   # 單一 request 最大多少 KB（影響 bio 會不會被 split）
cat rotational       # 1=旋轉磁碟，0=SSD/NVMe（kernel 據此選預設策略）
cat nr_hw_queues     # 有幾條硬體佇列（blk-mq 的 hctx 數）
```

換 scheduler、調 `nr_requests`，再用 `fio` 打固定負載對比延遲/吞吐，是理解 block layer 最有效的實驗。例如把 NVMe 從 `none` 換成 `bfq`，量測隨機讀延遲有沒有變差——通常會，因為你在快裝置上加了本來不需要的排序開銷。

## 寫路徑：對稱但不對稱

到這裡都以讀為主線（呼應 Ch 34）。寫路徑的骨架對稱——一樣是 bio → blk-mq → 驅動 → 硬體——但有兩個關鍵不同，值得單獨點出：

- **寫可以延遲，讀通常等著要**。`write()` 系統呼叫多半是把資料丟進 page cache 就返回（write-back，Ch 21），標記 folio 為 dirty，真正下磁碟是後來 writeback kthread 的事。讀不行——process 呼叫 `read` 就是要那份資料，miss 就得同步等磁碟。所以寫路徑有更大的攢批空間：writeback 可以把大量 dirty folio 攢起來、排序、合併成幾個大 bio 一次寫回，plug 在寫路徑的效益比讀更明顯。
- **寫多了 barrier / flush 語意**。資料庫、檔案系統的 journal 需要「這批寫真的落到碟片、不是還躺在裝置 cache 裡」的保證。這靠帶 `REQ_PREFLUSH` / `REQ_FUA` flag 的特殊 bio（`fsync`、`FLUSH` 命令）強制裝置把 volatile cache 刷到持久媒體。block layer 有一套 flush machinery（`block/blk-flush.c`）處理這些屏障請求的順序——它們不能被亂序合併，否則「先寫資料再寫 commit record」的順序保證就破了。

寫路徑的完整處理（dirty 追蹤、`balance_dirty_pages` 節流、writeback kthread）主要在 Ch 21，這裡只點出它跟讀在 block layer 這端的差異。

## 為什麼要懂這一層

這章不是為了寫磁碟驅動才學（雖然練習 E 會寫）。理解 block layer 直接對應幾類真實問題：

- **I/O 效能診斷**：系統慢、`load average` 高但 CPU 不忙，多半是 I/O 卡住。`iostat` 看到 `aqu-sz` 爆高、`blkparse` 看到 `Q → D` 拖很久，你才知道是佇列滿了還是排程延遲，而不是瞎猜。
- **資料庫調優**（接 `perf_bench` 課）：資料庫是 block layer 的重度用戶。要不要用 `O_DIRECT` 繞過 page cache（DB 自己管快取，不想跟 kernel page cache 重複）、選哪個 I/O scheduler、`nr_requests` 調多大、要不要 `io_uring`——每個決定都要懂底下 block layer 怎麼運作才不會亂調。「把 NVMe 的 scheduler 從 bfq 改成 none，DB 的 p99 延遲降一半」這種調優，前提是你知道 bfq 在快裝置上是純開銷。
- **看懂觀測工具**（接 `observability_tools` / `bpf` 課）：`blktrace`、`biosnoop`（bcc/bpftrace 的 block tracepoint）、`iostat` 的每個欄位，背後都是本章的 bio/request/queue 概念。工具給你數字，概念讓你讀懂數字。

## 對比與取捨

| 主題 | 選項 A | 選項 B | 取捨 |
|---|---|---|---|
| block layer 架構 | 舊單佇列 + 全域鎖 | blk-mq 多佇列 | 單佇列對慢磁碟夠用且簡單；多佇列對高速多佇列硬體必要，複雜度換擴展性。v5.0 起只剩 blk-mq |
| I/O scheduler（NVMe） | `none` | `mq-deadline`/`bfq` | none 最低開銷、發揮 NVMe 並行；有 scheduler 換來公平/deadline 保證，但排序開銷在快裝置上多半得不償失 |
| I/O scheduler（HDD） | `mq-deadline`/`bfq` | `none` | 旋轉磁碟一定要排序省 seek；none 在 HDD 上會讓磁頭亂甩，吞吐崩掉 |
| 提交方式 | 每個 bio 立即 dispatch | plug 攢批 | 立即送延遲低但錯過合併、固定開銷高；plug 攢批提升合併率與吞吐，代價是極小的提交延遲 |
| 傳資料 | CPU 逐 byte 搬（PIO） | DMA | DMA 讓硬體直接搬進 page，釋放 CPU；PIO 只在極簡/早期硬體用，現代 block 裝置一律 DMA（Ch 41） |

## 踩雷集錦

1. **「`bio` 定義在 `bio.h`」——錯**。`struct bio` 本體在 `include/linux/blk_types.h`；`include/linux/bio.h` 放的是操作 bio 的函式與 inline helper（`bio_alloc`、`bio_add_page`、`bio_for_each_segment` 等）。找結構欄位別在 `bio.h` 裡繞。

2. **以為 `bio` 的記憶體端一定連續**。不是。`bio_vec` 陣列就是為了描述「磁碟連續、記憶體分散」的 scatter-gather。假設它連續會在你自己寫驅動或讀源碼算位址時出錯。

3. **在 NVMe 上開 `bfq` 求「更好」**。方向反了。`bfq`/`mq-deadline` 的排序與公平邏輯是為 seek 有成本的裝置設計的；在無 seek penalty 的 NVMe 上，排序是純開銷，通常讓延遲更差、吞吐更低。快裝置的預設 `none` 不是偷懶，是對的。

4. **把 `%util` 100% 當成 NVMe 滿載**。`%util` 這個指標假設裝置一次只能處理一個請求（旋轉磁碟時代的模型）。NVMe 能同時並行幾十個請求，`%util` 到 100% 可能只是「隨時有請求在飛」，離真正飽和還很遠。看 NVMe 飽和要看 `aqu-sz`、延遲、以及和裝置額定 IOPS 的比較。

5. **以為換了 `scheduler` 對 tmpfs / Ch 35 那種 fs 有用**。純記憶體檔案系統（tmpfs、Ch 35 的 mini fs）根本不產生 bio、不下磁碟、不經 block layer——`/sys/block` 底下也沒有它們。I/O scheduler 只對真的有 backing block 裝置的檔案系統（ext4/xfs 在磁碟上）有意義。

6. **D 狀態的 process 想用 signal 救**。等 block I/O 的 process 睡在 `TASK_UNINTERRUPTIBLE`，signal 進不去，`kill -9` 沒用（Ch 9/26）。要嘛等 I/O 完成（硬體回來、中斷喚醒它），要嘛底層裝置卡死時只能重開。這不是 bug，是為了避免 I/O 中途被 signal 打斷造成不一致的刻意設計。

## 進階：再往深一層

- **多重佇列映射（`blk_mq_map_queues`）**：blk-mq 支援不只一組 CPU→hctx 映射。NVMe 可以為 read、write（poll）、default 各建一組映射（`HCTX_TYPE_DEFAULT/READ/POLL`），讓不同類型的 I/O 走不同硬體佇列，進一步降低干擾。
- **polling 模式（hybrid polling）**：對超低延遲需求（NVMe + 需要幾微秒延遲），中斷本身的開銷都嫌大。blk-mq 支援 `io_poll`——提交後 CPU 直接輪詢完成佇列，不睡不等中斷。犧牲 CPU 換延遲，資料庫/儲存密集場景會用。
- **`io_uring` 與 block layer**：現代高效能 I/O（`io_uring`）繞過傳統 syscall 的 per-call 開銷，但底下打到磁碟時仍走 blk-mq 這條路。理解 block layer 是理解 `io_uring` 為什麼快（以及它省的是哪一段開銷）的前提。
- **面試常問**：「blk-mq 相對舊 block layer 解決什麼問題」（全域鎖 → per-CPU 無鎖提交，餵飽多佇列硬體）、「為什麼 NVMe 用 none scheduler」（無 seek penalty，排序純開銷）、「D 狀態是什麼、為什麼 kill 不掉」（`TASK_UNINTERRUPTIBLE` 等 block I/O）。這三題基本是 block layer 的核心考點。

## 動手練習

1. **看你機器的 scheduler 並換掉**：`cat /sys/block/<dev>/queue/scheduler` 看當前值。若是 NVMe（`none`），`echo mq-deadline` 換上去，用 `fio --name=randread --rw=randread --bs=4k --numjobs=4 --iodepth=32 --runtime=30 --ioengine=libaio --direct=1 --filename=/dev/<dev>` 各跑一次（換前換後），比較 `clat`（completion latency）。fio 參數對照本章概念：`--bs=4k` 每個 I/O 4KB（一個 page，最小 bio）、`--iodepth=32` 讓 32 個 request 同時 in-flight（吃 32 個 tag，測 queue depth 效益）、`--numjobs=4` 四個 process 並發（測 per-CPU software queue 是否真的無爭用）、`--direct=1` 走 `O_DIRECT` 繞過 page cache（否則你量到的是 cache 命中不是 block layer）。記下換 scheduler 前後的 `clat` p99 差異並解釋方向——快裝置上 `none` 通常贏。

2. **用 blktrace 抓一次合併**：跑 `sudo blktrace -d /dev/vda -o t` 收集，另開終端做一段**循序**大量寫（`dd if=/dev/zero of=/mnt/test/f bs=1M count=100 oflag=direct`），停止後 `blkparse -i t | grep ' M '`——看有沒有 `M`（merge）事件。循序 I/O 應該併得很多。再改成**隨機**小 I/O，對比 merge 事件變少。

3. **弄出一個 D 狀態**：在慢裝置（或 QEMU 裡故意限速的 `-drive` 加 `throttling`）上跑大量 `dd` 直接讀，另開終端 `ps -eo pid,stat,cmd | grep dd`，抓到 `D` 或 `D+`。試 `kill -9` 那個 pid，觀察它殺不掉直到 I/O 完成。把這個現象和 Ch 9 的 `TASK_UNINTERRUPTIBLE` 對起來。

4. **算一個 bio 的兩端**：讀 `include/linux/blk_types.h` 的 `struct bio` 和 `include/linux/bvec.h` 的 `bio_vec`，在紙上畫出「讀一個 16KB 檔案片段（4 個 4KB page，記憶體不連續）到磁碟 sector 2048」時，`bi_iter.bi_sector`、`bi_vcnt`、每個 `bio_vec` 的 `bv_page/bv_len/bv_offset` 各是什麼。這題確認你真的懂 bio 的兩端。

## 本章重點整理

- block layer 夾在檔案系統（Ch 33/35）與裝置驅動之間，把「讀 inode 的第 N 個 block」翻成「對磁碟 sector 的 I/O」，並負責 split/merge/排程。
- `bio`（`include/linux/blk_types.h`）是 block I/O 的基本單位：一端是裝置 sector 範圍，一端是 `bio_vec` 陣列指向的一組 page（page cache 的頁，Ch 17）。page cache miss 經 readahead 產生 bio，`submit_bio` 提交。
- blk-mq（`block/blk-mq.c`）用 per-CPU software queue（Ch 7）+ 硬體 dispatch queue 拆掉舊架構的全域鎖，餵飽 NVMe 這種高 IOPS 多佇列硬體；`struct request` + tag 是它對驅動的語言。
- I/O scheduler（`block/` 的 mq-deadline/bfq/kyber/none）：旋轉磁碟要排序省 seek，NVMe 無 seek penalty 故常用 none。plug/unplug 攢批提交提升合併率、攤平開銷。

## 自我檢核

- [ ] 不看筆記，能畫出「fs → bio → blk-mq（per-CPU sw queue → hw queue）→ 驅動 → 硬體」的分層，並說出每層做什麼
- [ ] 能解釋一個 `bio` 的「裝置端」和「記憶體端」各是什麼、為什麼記憶體端是陣列（scatter-gather）
- [ ] 面試被問「blk-mq 解決了什麼問題」，能講出「舊單佇列全域鎖 → per-CPU 無鎖提交，對應多佇列硬體」
- [ ] 能說出為什麼 NVMe 常用 `none` scheduler、旋轉磁碟卻需要電梯演算法
- [ ] 能用 `cat /sys/block/*/queue/scheduler` 看/換 scheduler，用 `blkparse` 認出 Q/M/D/C 事件
- [ ] 能把「D 狀態 process 殺不掉」連回 block I/O 的睡眠與磁碟中斷喚醒（Ch 9/26/29）

## 延伸閱讀

### 官方文件

- **[Documentation/block/blk-mq.rst](https://www.kernel.org/doc/html/latest/block/blk-mq.html)**
  - **讀哪裡**：整篇，很短。官方對 blk-mq 兩層佇列（software staging + hardware dispatch）與設計動機的說明
  - **和本章的關聯**：本章「blk-mq 的設計」一節就是它的展開；讀源碼 `block/blk-mq.c` 前先讀這篇建立框架

- **[Documentation/block/](https://www.kernel.org/doc/html/latest/block/index.html)**
  - **讀哪裡**：`stat.rst`（`/sys/block` 統計欄位定義）、`queue-sysfs.rst`（`/sys/block/*/queue/` 每個旋鈕的意義）
  - **能學到什麼**：本章「動手」那節看到的 `nr_requests`、`max_sectors_kb`、`scheduler` 官方逐項解釋，調參前必查

### LWN 文章

- **[The multiqueue block layer](https://lwn.net/Articles/552904/)** — Jonathan Corbet, LWN
  - **讀哪裡**：整篇。blk-mq 剛引入時的權威解說，把「為什麼單佇列全域鎖擋住高速硬體、兩層佇列怎麼拆」講得最清楚
  - **前提**：知道自旋鎖與 cacheline 爭用（Ch 23/25）

- **[Two new block I/O schedulers for 4.12](https://lwn.net/Articles/720675/)** — LWN
  - **能學到什麼**：kyber 與 bfq 進 mainline 的背景與各自的設計目標，補足本章 scheduler 表格背後的取捨

### 書籍 / 工具

- **《Linux Kernel Development, 3rd Ed.》** — Robert Love，"The Block I/O Layer" 一章
  - **這本書的定位**：把 bio、request、request_queue 的關係講得最好讀的入門；注意它成書於單佇列時代，blk-mq 的部分以上面 LWN + 官方 doc 為準
  - **怎麼配本章**：先讀 Love 建立 bio/request 直覺，再用 LWN 補上 blk-mq 的多佇列演進

- **[blktrace User Guide](https://www.kernel.org/doc/html/latest/block/blktrace.html)**（配 `observability_tools` 課）
  - **這是什麼**：`blktrace`/`blkparse`/`btt` 的官方用法與事件代號（Q/G/M/I/D/C）完整表
  - **為什麼值得用**：本章「動手」的 blktrace 段只給了最常用幾個事件；要做認真的 I/O 延遲拆解（各階段耗時）看這篇加 `btt`

block layer 這一層補完，Part 6 的儲存路徑就從 VFS 抽象（Ch 33）、read 全鏈（Ch 34）、記憶體檔案系統（Ch 35）到真的下磁碟（本章）都通了。接下來練習 E 讓你把這整套裝起來——寫一個 ramdisk block device，親手實作 `submit_bio` 的接收端，再把 Ch 35 的 mini fs 掛到它上面，讓「檔案系統 → block 裝置」這條線在你自己的程式碼裡跑一遍。

→ [練習 E：ramdisk block device / mini fs](./practice-e-mini-fs.md)
