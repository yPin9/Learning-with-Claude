# Ch 31 — io_uring：SQE/CQE 與 async ring 的 UAF 模式

> **目標**：搞清楚 io_uring 的 ring 模型（SQE/CQE、registered buffers/files）與它的非同步執行模型，說出「為什麼 async 執行天生比同步 syscall 容易出 UAF 與 race」；拆解兩個代表性 CVE 的 root cause（CVE-2023-2598 的 registered buffer OOB、CVE-2022-2602 與 AF_UNIX GC 的 UAF），並理解概念層的利用升級與 `io_uring_disabled` 這個專門為它而生的防禦機制。

> **環境**：Linux 6.6 LTS、x86-64 基線。io_uring 自 5.1（2019）併入主線後幾乎每個版本都有大改動（registered buffer、多 shot poll、`IORING_OP_*` 新操作），本章描述的物件模型與 CVE 細節以 6.x 系列為準；跨版本差異處會標注。

## 從 Linux AIO 到 io_uring：為什麼要換一套非同步 I/O 模型

在 io_uring 之前，Linux 已經有一套非同步 I/O 介面——`io_submit()`/`io_getevents()`（俗稱 Linux AIO，`libaio`）。它的問題是**適用範圍很窄**：只對特定條件下的直接 I/O（`O_DIRECT`）檔案操作有效，網路 socket、一般 buffered I/O 幾乎用不上；介面設計也笨重，每種操作都要套進一個通用但不直覺的 `struct iocb`。於是真實世界裡大量需要高效能 I/O 的程式（資料庫、代理伺服器）繼續用執行緒池模擬非同步（每個 I/O 配一條 thread 去做同步呼叫），犧牲掉非同步模型本該有的效率。

io_uring（Jens Axboe，2019）的目標是做一套**真正通用**的非同步 I/O 框架：不限定操作類型（檔案、網路、甚至後來的 `IORING_OP_ACCEPT`/`CONNECT`），用共享記憶體 ring 取代一次一個的 syscall 呼叫,搭配 `SQPOLL` 模式甚至可以讓整個提交過程完全不需要呼叫 syscall（由一個 kernel thread 持續輪詢 SQ ring）。這個通用性換來的代價，正是本章要講的：**操作類型越多、越通用，`io_kiocb` 要能正確處理的生命週期組合就越多**——Linux AIO 因為應用範圍窄，反而沒有這個問題；io_uring 選擇了「更大的表達力」，也就選擇了「更大的攻擊面」,這和 [Ch 30](./30-netfilter-nftables.md) 開頭講 iptables → nf_tables 的取捨是同一個邏輯，只是這次換成了「同步模型 → 非同步通用框架」。

## 為什麼 io_uring 是礦區：不需要任何 capability 的攻擊面

[Ch 30](./30-netfilter-nftables.md) 的 nf_tables 至少還需要 `CAP_NET_ADMIN`（即使能靠 user namespace 免費拿到）。io_uring 更乾脆：**任何 unprivileged process 呼叫 `io_uring_setup()` 就能直接用**，不需要任何 capability、不需要進任何 namespace。這讓它成為另一種極端——攻擊面大小不是靠權限模型放大的，而是**預設就對所有人開放**。

io_uring 的設計初衷是解決一個真實痛點：傳統的 `read()`/`write()`/`epoll` 模型對高吞吐 I/O（資料庫、儲存系統）而言,每次 I/O 都要做一次 syscall 的 context switch 開銷,而 `io_submit`（Linux AIO）又只支援有限的操作類型、介面笨重。io_uring 用兩個與 kernel 共享記憶體的環形 buffer（submission queue、completion queue）取代「一次 syscall 遞交一個請求」的模型:使用者態把請求寫進共享記憶體,一次系統呼叫（甚至 0 次,配合 `SQPOLL`）就能提交一批請求,完成結果也透過共享記憶體讀回,**把 syscall 開銷從「每個請求一次」降到「每批請求接近零次」**。

這個設計帶來礦區的結構性原因：

1. **非同步執行模型本身就是 race 的溫床**。使用者遞交一個請求後,請求的實際執行可能發生在**任意之後的時間點**——可能立即在遞交時的 context 完成,也可能被丟到 io-wq（io_uring 自己的 worker thread pool）背景執行,也可能等某個資源 ready 才被 poll callback 喚醒執行。對照 [Ch 39](./39-concurrency-model.md) 會講的並發模型,這種「延遲到不確定時間點才執行」正是 race condition 的經典成因:提交請求的 process 可能已經關閉、已經釋放了某個資源,但背景執行的請求還握著指向這些資源的指標。
2. **registered buffer/files 建立了額外的生命週期連結**。io_uring 允許使用者事先註冊一批 buffer（`IORING_REGISTER_BUFFERS`）或檔案描述符（`IORING_REGISTER_FILES`）,之後的操作可以用一個索引直接引用,省掉每次傳指標/fd 的開銷。但這代表 io_uring context（`struct io_ring_ctx`）現在**自己持有一份對這些 buffer/file 的引用**,這份引用的生命週期要和「使用者何時關閉 ring」「這些 buffer/file 本身何時被其他地方釋放」保持一致——多一層引用關係,就多一處生命週期可能算錯的地方。
3. **和其他子系統交互產生意外的生命週期糾纏**。io_uring 的 fd 本身可以被 `SCM_RIGHTS`（AF_UNIX socket 的 fd 傳遞機制）傳遞,於是 io_uring context 可能和 AF_UNIX 的垂圾回收（garbage collection, GC）機制產生循環引用——這正是 CVE-2022-2602 的場景,也是本章要講的「子系統之間的交互」比子系統自己的程式碼更容易出事的又一個例子（[Ch 30](./30-netfilter-nftables.md) 的 nf_tables 展示的是「操作序列之間的交互」，這裡展示的是「子系統之間的交互」）。

### 把三個子系統的礦區成因放在一起看

| 子系統 | 攻擊面怎麼打開 | bug 藏在哪一層 |
|---|---|---|
| nf_tables（[Ch 30](./30-netfilter-nftables.md)） | 需要 `CAP_NET_ADMIN`，但可被 user namespace 免費放大 | 操作序列之間的狀態交互（commit/abort） |
| io_uring（本章） | 預設對所有人開放，不需要任何權限 | 非同步執行的時間窗口 + 跨子系統生命週期假設 |
| eBPF（[Ch 32](./32-ebpf.md)） | 預設視發行版而定，常需 `CAP_SYS_ADMIN` 或 `CAP_BPF`，同樣可被 user namespace 放大 | 單一驗證器（verifier）的正確性 |

三者分別代表「權限模型」「時間模型」「驗證器模型」三種不同的攻擊面放大機制——記住這張表，讀任何一個新子系統的攻擊面分析時，先問自己它屬於哪一種（或哪幾種的組合）。

## 先建立直覺：SQ/CQ ring 怎麼運作

想像兩條單向的輸送帶，一條只能從使用者推向 kernel（submission），一條只能從 kernel 推向使用者（completion），兩邊都不需要排隊等對方確認——寫入自己的輸送帶、更新一個共享的「寫到哪了」指標就好：

```
使用者態                              kernel
─────────                            ──────
                mmap 共享記憶體（io_uring_setup 回傳的 fd mmap 出來）
   ┌─────────────────────────────────────────────────────┐
   │  SQ ring（Submission Queue）                          │
   │  sqe[0] sqe[1] sqe[2] ...  ◀── 使用者填好 SQE，推進 sq_tail │
   └─────────────────────────────────────────────────────┘
          │ io_uring_enter(2)：通知 kernel「有新 SQE 了」（或 SQPOLL 省掉這步）
          ▼
   kernel 從 sq_head 開始消耗 SQE，依 opcode 分派
          │  ├─ 立即能完成的 → 當場處理，寫入 CQE
          │  └─ 需要等待的（I/O、poll）→ 丟給 io-wq 背景 worker，或掛進 poll 等待佇列
          ▼
   ┌─────────────────────────────────────────────────────┐
   │  CQ ring（Completion Queue）                          │
   │  cqe[0] cqe[1] cqe[2] ...  ◀── kernel 寫入結果，推進 cq_tail │
   └─────────────────────────────────────────────────────┘
          │ 使用者態輪詢 cq_head，或 io_uring_enter(2) 帶 IORING_ENTER_GETEVENTS 等待
          ▼
   使用者讀出完成結果
```

這張圖的重點在**中間那一步的分支**：「立即完成」和「丟給背景執行」是同一個 opcode 可能走的兩條路，取決於資源當下是否 ready。背景執行的那條路徑，執行的時間點和提交的時間點之間有一段**不確定長度的視窗**，這段視窗裡使用者態可以做任何事——包括關閉 fd、退出 process、釋放 registered buffer。io_uring 的核心工程難題就是：**怎麼確保這段視窗裡，背景執行緒手上的所有指標都還有效**。

## 物件模型與關鍵資料結構

| 概念 | struct / 介面 | 說明 |
|---|---|---|
| io_uring context | `struct io_ring_ctx` | 一個 `io_uring_setup()` 建立的 ring 實例，持有 SQ/CQ ring 的記憶體、registered buffer/file 表、io-wq 的參照 |
| 提交項目 | `struct io_uring_sqe` | 使用者填寫的請求：opcode（`IORING_OP_READV`/`WRITEV`/`RECV`/`SEND`/`POLL_ADD`/`ACCEPT`……）、fd、buffer 位址/索引、flags |
| 完成項目 | `struct io_uring_cqe` | kernel 寫回的結果：對應哪個 SQE（`user_data`）、回傳值 |
| 內部請求物件 | `struct io_kiocb` | 一個 SQE 被接收後，kernel 在內部建立的執行單元，貫穿整個非同步執行生命週期——從提交到完成都是同一個 `io_kiocb` 在流轉，可能被連結成 chain（`IOSQE_IO_LINK`） |
| registered buffer | `struct io_mapped_ubuf`（經 `IORING_REGISTER_BUFFERS` 建立） | 預先 pin 住一批使用者記憶體頁，之後操作用索引引用，省掉每次的 page fault/pin 開銷 |
| registered files（fixed file table） | `io_ring_ctx` 內的 file 陣列（經 `IORING_REGISTER_FILES` 建立） | 預先把一批 fd 轉成內部索引，操作時用索引取代每次傳 fd |
| 背景執行 worker | io-wq（`struct io_wq`） | 當請求不能立即完成（例如阻塞式檔案 I/O）時，丟給這個 worker pool 執行；5.1 最初版本借用 kernel workqueue，後續版本自己實作一套更輕量的 io-wq |
| 非同步等待 | poll-based 等待佇列 | `POLL_ADD`、`IORING_OP_ACCEPT` 等操作需要等事件 ready，用 kernel 既有的 `wait_queue` 機制掛起，事件到了才回調完成 |

幾個常見 opcode 與它們各自的風險特徵（供你評估「這個操作需要跨多少個資源的生命週期」）：

| opcode | 用途 | 需要協調的資源 |
|---|---|---|
| `IORING_OP_NOP` | 什麼都不做，測試用 | 無 |
| `IORING_OP_READV`/`WRITEV` | 讀寫檔案/socket | fd、使用者記憶體（或 registered buffer） |
| `IORING_OP_RECV`/`SEND` | 網路收發 | socket fd、buffer |
| `IORING_OP_POLL_ADD` | 等待一個 fd 上的事件 | fd、等待佇列 |
| `IORING_OP_ACCEPT` | 等待並接受新連線 | listening socket、新建立的 fd |
| `IORING_OP_LINK_TIMEOUT` | 幫一串連結的請求設超時 | 連結鏈上的所有 `io_kiocb` |
| `IORING_OP_SEND_ZC` | 零拷貝網路傳送 | socket fd、使用者記憶體頁的 pin 狀態 |

opcode 清單隨版本持續增加；一般的經驗法則是：**一個 opcode 需要協調的資源種類越多，它的生命週期管理複雜度越高，歷史上也越容易出事**——`IORING_OP_LINK_TIMEOUT` 與零拷貝類操作正是這個經驗法則下值得特別留意的候選。

讀 source 的入口：`io_uring/io_uring.c`（核心 ring 邏輯與 `io_kiocb` 生命週期）、`io_uring/rsrc.c`（registered buffer/file 管理，CVE-2023-2598 的位置）、`io_uring/net.c`（網路相關 opcode）。

### `io_kiocb` 的生命週期：一張圖看完整路徑

`io_kiocb` 是整章的核心物件——它的生命週期跨越提交、（可能的）背景執行、完成三個階段，期間可能被各種參照方式持有：

```
io_uring_enter(2) 讀入一個 SQE
      │
      ▼
io_submit_sqe()：建立對應的 io_kiocb，依 opcode 分派
      │
      ├─ 能立即完成（如 IORING_OP_NOP）
      │     └─▶ 直接寫入 CQE，io_kiocb 生命週期結束
      │
      ├─ 需要阻塞式 I/O，丟給 io-wq 背景 worker
      │     └─▶ io_kiocb 被 worker thread 持有引用，
      │          期間使用者態可以做任何事（關閉 fd、退出 process）
      │
      └─ 需要等待事件（poll-based），掛進對應資源的 wait_queue
            └─▶ io_kiocb 被那個資源的等待佇列持有引用，
                 事件 ready 時由另一個 context（可能是完全不相關的 process）
                 的程式碼路徑回調喚醒並完成
```

三條分支裡，中間與最後一條都讓 `io_kiocb` 的生命週期**脫離了提交它的那個 process 的直接控制**——提交者可以消失，但 `io_kiocb` 可能還活著，還握著對 registered buffer、registered file、甚至整個 `io_ring_ctx` 的引用。io_uring 的核心工程挑戰，就是確保這整條路徑上每一處引用都用對了 refcount 或 RCU，不多一次釋放也不少一次釋放。

## 典型 bug 類型與成因：兩個公開 CVE

### CVE-2023-2598：registered buffer 的 huge page 優化漏檢非連續頁

- **背景**：`IORING_REGISTER_BUFFERS` 讓使用者預先註冊一塊記憶體給 io_uring 操作重複使用。kernel 內部要把這塊使用者記憶體轉成一組 `bio_vec`（描述一頁一頁實體記憶體的陣列），供後續的 I/O 操作直接引用。6.3-rc1 引入的優化（commit `57bebf807e2a`）針對 huge page 場景：如果這塊記憶體底層是 huge page 組成，就**不要把它拆成一堆單頁的 `bio_vec` entry**，直接用更少、更大的 entry 表示，省記憶體也省後續走訪的開銷。
- **root cause**：這個優化隱含一個假設——「同一個 huge page 範圍內的實體記憶體位址必然連續」。但 Tobias Holl 的公開揭露指出，`io_sqe_buffer_register()`（`io_uring/rsrc.c`）在套用這個優化時，**沒有驗證使用者傳入的這段虛擬位址範圍，底層對應到的實體頁是否真的連續**。也就是說攻擊者可以構造一段虛擬位址上看起來屬於同一個 huge page 範圍、但實際映射到的實體頁並不連續的記憶體,讓 kernel 誤以為後面的操作可以用「起始位址 + 偏移」線性推算,結果 I/O 操作存取到預期之外的實體記憶體——NVD 的描述是「allows out-of-bounds access to physical memory beyond the end of the buffer」。
- **fix**：上游 `776617db78c6`（標題 "io_uring/rsrc: check for nonconsecutive pages"）在套用這個優化前,補上「這些頁是否真的物理連續」的檢查,不連續就乖乖退回逐頁 `bio_vec` 的慢路徑。
- **為什麼危害大**：這不是一般的 heap OOB——它讓攻擊者透過合法的 io_uring I/O 操作,存取到**超出註冊 buffer 範圍的任意實體記憶體**。Holl 的揭露直接指出這能被用來達成完整的本地提權。這是「效能優化引入安全假設」的典型案例：優化本身的邏輯沒錯,錯的是它依賴了一個沒有被驗證的前提。

用偽碼示意這個模式：

```c
/* 偽碼，示意優化邏輯依賴的未驗證假設，不是任何版本的原文 */
static int io_sqe_buffer_register(struct io_ring_ctx *ctx, struct iovec *iov,
                                   struct io_mapped_ubuf **pimu)
{
        pages = pin_user_pages(iov->iov_base, nr_pages);

        if (is_huge_page_aligned(pages, nr_pages)) {
                /* 優化路徑：假設 huge page 範圍內實體頁必然連續，
                 * 漏檢：沒有驗證 pages[] 陣列裡的實體頁位址是否真的逐頁連續 */
                imu->bvec[0] = (struct bio_vec){ .page = pages[0], .len = huge_len };
        } else {
                /* 慢路徑：逐頁建立 bio_vec，天生正確但較慢 */
                for (i = 0; i < nr_pages; i++)
                        imu->bvec[i] = (struct bio_vec){ .page = pages[i], .len = PAGE_SIZE };
        }
        return 0;
}
```

修補後的版本在進優化路徑前，先掃一遍 `pages[]` 確認逐頁的實體位址確實連續，不連續就強制走慢路徑。

### CVE-2022-2602：io_uring registered file 與 AF_UNIX GC 的 UAF

- **背景**：Linux 的 AF_UNIX socket 支援用 `SCM_RIGHTS` 把任意 fd 透過 socket 傳給另一個 process。如果兩個 process 互相用 `SCM_RIGHTS` 傳遞彼此的 fd，可能形成一個「誰也無法從外部直接看到、但彼此互相引用」的循環——Unix domain socket 子系統內建一套垂圾回收機制（`unix_gc()`）專門偵測並打破這類循環，判斷一個 fd 是否「只能被循環內的物件存活引用、外部已經不可達」，是的話就強制回收。
- **root cause**：io_uring 的 fd 本身也可以被 `SCM_RIGHTS` 傳遞，而且 io_uring 可以把普通的檔案 fd 註冊成 registered file。這創造了一個 GC 原本沒設計要處理的場景：**io_uring 正在處理一個針對某個 registered file 的請求，同時這個 io_uring fd（連同它註冊的所有 fd）被另一條路徑判定為 GC 循環的一部分而被強制回收**。Canonical 的 Thadeu Cascardo 在揭露裡的描述是：GC 跑的時候釋放了 io_uring fd 以及它註冊的所有 fd，但此時可能有一個 io_uring 請求**正在**處理這個被註冊的檔案——GC 的「判定可回收」邏輯沒有把「這個 fd 當下是否正被一個 in-flight 的 io_uring 操作使用」納入考量，於是請求繼續執行時,手上的 `struct file` 指標已經是懸空指標。
- **fix**：上游 `0091bfc81741b8d3aeb3b7ab8636f911b2de6e80`（標題 "io_uring/af_unix: defer registered files gc to io_uring release"）的解法是**延後時機**：不讓 GC 直接把 registered file 的垂圾回收和一般 fd 回收混在一起做，而是推遲到 io_uring context 真正釋放（`io_uring_release`）的時候才處理這些 registered file 的 GC，確保不會在請求還在飛的時候被搶先回收。
- **發現者與影響**：David Bouman 與 Billy Jheng Bing Jhong 回報（ZDI-CAN-17428），影響範圍回溯到 5.4.y、5.15.y 等多條 stable 分支。Jann Horn（Google Project Zero）後續公開了一份利用這個「io_uring 與 Unix GC 交互」漏洞類型的分析。

### 兩案例對照

| CVE | bug 所在層次 | 根本原因類型 | 對 Ch 10 分類 |
|---|---|---|---|
| CVE-2023-2598 | registered buffer 的頁面映射優化 | 效能優化引入未驗證的「實體頁連續」假設 | OOB（跨子系統邊界，影響整個實體記憶體） |
| CVE-2022-2602 | registered file 與 AF_UNIX GC 的交互 | 跨子系統生命週期假設不一致（GC 判定邏輯沒考慮 in-flight 請求） | UAF |

共同模式：**io_uring 的 bug 很少是「忘記檢查一個長度欄位」這種單點錯誤，而是「io_uring 自己的生命週期假設,和它互動的另一個子系統（實體記憶體管理、AF_UNIX GC）的生命週期假設,兩者不吻合」**。這是非同步模型的代價——同步 syscall 的生命週期管理只需要考慮「這次呼叫期間」，io_uring 的生命週期要考慮「提交之後、完成之前，這段不確定長度的時間裡，所有相關資源的狀態」。

### 一條不完整但有感的時間線

| 年份 | CVE | 大致性質 |
|---|---|---|
| 2022 | CVE-2022-2602 | registered file 與 AF_UNIX GC 的 UAF（本章案例二） |
| 2023 | CVE-2023-2598 | registered buffer 的實體頁非連續漏檢（本章案例一） |

> 我們只列查證過的兩個案例；io_uring 歷史上還有其他早期（5.x 系列）版本的 race/UAF 報告，但本次 WebSearch 沒有取得足夠細節確認其 root cause，這裡不列未經查證的編號。

io_uring 從 5.1 併入主線起，開發速度一直很快（新 opcode、新優化持續加入），這組時間線反映的是「功能持續擴張」和「生命週期管理複雜度持續上升」同步發生的現象。

## 利用思路（概念，不寫 turnkey）

1. **原語定性**：CVE-2023-2598 給的是一個**跨越 buffer 邊界的實體記憶體讀寫**原語——這比典型 heap UAF 更接近 [Ch 23](./23-dirty-pagetable.md) 的 Dirty Pagetable 風格原語：直接碰到實體記憶體，威力更大也更難精確控制落點。CVE-2022-2602 則是標準的 `struct file` UAF，升級路徑和 [Ch 18](./18-uaf-double-free.md) 的通用 UAF 升級類似。
2. **spray 與 reclaim（CVE-2022-2602 類型）**：`struct file` 被釋放後，用 [Ch 19](./19-spray-objects.md) 的牌庫裡 size 相近的物件去佔位——由於觸發條件涉及 AF_UNIX GC 的時序，實務上的難點主要在**穩定重現 race window**，而不是找 spray 物件本身；這點要搭配 [Ch 41](./41-widening-race-window.md) 的「拉長 race window」技巧（如 `userfaultfd` 讓某個路徑卡在可控的時間點）。
3. **物理記憶體原語的升級（CVE-2023-2598 類型）**：拿到跨邊界的物理記憶體讀寫之後，概念上的下一步是**把它轉換成虛擬位址層的任意讀寫**——這正是 [Ch 23](./23-dirty-pagetable.md) 整章要教的 Dirty Pagetable 類技巧的應用場景之一：用這個原語去改寫 page table entry，讓某段使用者可控的虛擬位址直接映射到任意實體頁。
4. **選擇升級方向**：和 [Ch 30](./30-netfilter-nftables.md) 一樣，現代趨勢是 data-only（[Ch 29](./29-data-only-attack.md)）優先於函式指標劫持。但 io_uring 的 registered buffer 原語因為威力接近「直接碰實體記憶體」，也是少數仍然值得考慮「用它直接構造任意虛擬位址讀寫原語，再用這個原語去打別的子系統」這種**把 io_uring 當成敲門磚，真正的提權動作交給另一個更簡單的原語完成**的策略。

概念骨架（同樣是骨架，不是任何特定 CVE 的完整 exploit）：

```c
/* 概念骨架：CVE-2022-2602 類型的 UAF 升級路徑 */
int main(void) {
        /* 1. 建立 io_uring 實例，不需要任何特殊權限 */
        struct io_uring ring;
        io_uring_queue_init(QUEUE_DEPTH, &ring, 0);

        /* 2. 註冊一批 file，製造 io_uring 與這些 fd 的額外引用關係 */
        int reg_fds[NFILES] = { /* ... */ };
        io_uring_register_files_update(&ring, 0, reg_fds, NFILES);

        /* 3. 提交一個針對 registered file 的長時間操作（例如阻塞式讀），
         *    讓它進入 io-wq 背景執行，製造一段視窗 */
        submit_long_running_op_on_registered_file(&ring);

        /* 4. 透過 AF_UNIX SCM_RIGHTS 構造一個包含這個 io_uring fd 的循環引用，
         *    誘發 unix_gc() 在步驟 3 的操作仍在飛的時候跑起來 */
        trigger_unix_gc_race(&ring);

        /* 5. 此時背景操作可能仍持有一個已經被 GC 釋放的 struct file 指標，
         *    用 Ch19 的 spray 牌庫佔住這塊記憶體，讀出或覆寫內容 */
        int spray_fds[NSPRAY];
        spray_reclaim_objects(spray_fds, NSPRAY);

        /* 6. 升級為 data-only 原語，覆寫 cred 或等價目標 */
        corrupt_target_via_reclaimed_object(spray_fds);

        if (getuid() == 0)
                system("/bin/sh");
        return 0;
}
```

`submit_long_running_op_on_registered_file()`、`trigger_unix_gc_race()` 這兩個函式名稱同樣是佔位——實際要讓 race window 穩定出現，需要精確控制操作類型、觸發 GC 的時間點，這部分請參照 [Ch 41](./41-widening-race-window.md) 的拉長 race window 技巧，不存在通用公式。

## 防禦面：`io_uring_disabled` 這個「整個關掉」的選項

io_uring 的歷史漏洞密度高到讓 Google 和部分 distro 選擇了一個直接的手段：不修 io_uring，直接**整個子系統關掉**。

- **`kernel.io_uring_disabled` sysctl**：由 Google 的 Matteo Rizzo 提出並在 2023 年合入上游（patch 系列演進過程中,merge 進主線的最終版本 commit 為 `76d3ccecfa186af3120e206d62f03db1a94a535f`,具體併入哪個 release 版本請以你的 kernel source 的 `Documentation/admin-guide/sysctl/kernel.rst` 為準，本章查證時未能確認精確版本號）。這個 sysctl 有三個值：
  - `0`（預設）：所有 process 都能呼叫 `io_uring_setup()`。
  - `1`：只有屬於 `kernel.io_uring_group` 這個群組（或有 `CAP_SYS_ADMIN`）的 process 才能建立新的 io_uring 實例；已存在的實例不受影響。
  - `2`：**所有** process（包括 root）呼叫 `io_uring_setup()` 都失敗，不管群組或 capability。
- **ChromeOS 與部分雲端環境已經預設關閉 io_uring**——這不是巧合，是對這個子系統歷史漏洞密度的直接回應。
- **kernelCTF Hardened target 直接關閉 `IO_URING`**（見 [Ch 49](./49-kernelctf-overview.md)），和 `USER_NS` 一起被列為「直接關掉比持續補洞更划算」的子系統。

| 防禦層 | 具體做法 | 擋得住什麼 | 擋不住什麼 |
|---|---|---|---|
| `io_uring_disabled=2` | 完全阻止任何人建立新的 io_uring 實例 | 本章兩個 CVE 的整條攻擊面 | 已存在的 io_uring 實例（需要配合 process 重啟/服務重新部署）；需要重新評估任何依賴 io_uring 做高效能 I/O 的服務（資料庫、代理伺服器）是否會因此退化效能 |
| `io_uring_disabled=1` + 群組白名單 | 只讓信任的服務帳號建立 io_uring | 一般使用者層級的攻擊 | 已在白名單群組內的服務如果有其他漏洞，仍能摸到 io_uring |
| 修補個別 CVE | 保留 io_uring 的效能優勢 | 已知 CVE | 下一個同類型 bug；io_uring 的開發速度快，新 opcode、新優化持續引入新的生命週期管理複雜度 |
| 容器內 seccomp 過濾 `io_uring_setup` | 縮小容器內攻擊面 | 容器內 unprivileged 使用者 | 容器本身若需要高效能 I/O（部分資料庫容器會用）則不適用 |

## 對比取捨表

| 利用方向 | 優點 | 缺點 | 現狀 |
|---|---|---|---|
| 實體記憶體邊界原語升級成任意讀寫（CVE-2023-2598 類型） | 原語威力大，一次拿到接近 Dirty Pagetable 等級的能力 | 需要精確控制 huge page / 實體頁佈局，前置條件較苛刻 | 威力強但觸發條件特化 |
| UAF + spray 標準升級（CVE-2022-2602 類型） | 升級路徑成熟，工具箱完整（Ch 18-21） | race window 窄，需要 Ch 41 的拉長 race window 技巧配合才穩定 | 常見形態 |
| 直接關閉 io_uring（防禦視角） | 一次性消除整類攻擊面 | 犧牲效能優勢，部分高吞吐服務不可接受 | 多個雲端環境與 Hardened target 的選擇 |

## 踩雷集錦

1. **「io_uring 的 bug 都需要特殊權限才能觸發」**：錯。這正是 io_uring 和 nf_tables 最大的差異——`io_uring_setup()` 預設對任何 unprivileged process 開放，不需要任何 capability 或 namespace 操作。
2. **「async 完成 = 立即執行完」**：錯。一個 SQE 提交後可能立即完成，也可能被丟到 io-wq 背景執行，也可能掛在 poll 等待佇列——這段不確定視窗是本章所有 race 類 bug 的根源,不能假設「送出去就等於做完了」。
3. **「registered buffer/file 只是效能優化，沒有安全意義」**：錯。CVE-2023-2598、CVE-2022-2602 都直接出在 registered buffer/file 這層——「預先註冊、之後用索引引用」這個設計本身就創造了額外的生命週期連結，是額外的攻擊面，不是純粹的效能特性。
4. **「`io_uring_disabled=1` 就安全了」**：不完全。`=1` 只擋新建立的實例，且白名單群組內的服務仍然完整暴露在 io_uring 的攻擊面上——如果威脅模型是「惡意的本地 unprivileged 使用者」，`=1` 有效；如果威脅模型包含「白名單群組內服務被攻破後的橫向移動」，`=1` 擋不住。
5. **「io_uring 的漏洞只影響用到 io_uring 的程式」**：錯。`io_uring_setup()` 一旦被呼叫，攻擊面就打開了，不需要系統上其他程式「正在用」io_uring——攻擊者自己的 PoC 程式呼叫一次就夠，不依賴受害系統原本的使用情境（這點和 [Ch 30](./30-netfilter-nftables.md) 的 nf_tables「不需要系統本來就有規則在跑」是同一個邏輯）。
6. **「`SQPOLL` 模式比一般模式更危險，應該優先關掉」**：方向大致對但理由常講錯。`SQPOLL` 本身（一個 kernel thread 常駐輪詢 SQ ring）不是本章兩個 CVE 的成因，它的風險在於**需要更高的權限才能開啟**（早期版本甚至要求 `CAP_SYS_NICE` 等），而且常駐的 kernel thread 本身增加了攻擊面的「常駐時間」，但這是另一個獨立的風險維度，不要和本章的 UAF/OOB 根因混為一談。
7. **「只要用了 `liburing` 這種高階函式庫，就不用管底層生命週期」**：錯。`liburing` 只是簡化了建構 SQE/讀取 CQE 的語法，底層的 `io_kiocb` 生命週期、registered buffer/file 的引用關係,完全沒有被函式庫抽象掉——這些 CVE 全部發生在 kernel 內部,和使用者態用什麼函式庫呼叫無關。

## 進階：再往深一層

- **io-wq 的演進史本身是一部 bug 史**：io_uring 的背景 worker 機制從借用 kernel workqueue（5.1）演進到自己實作的 io-wq，中間經歷多次重寫，每次重寫都是重新檢視生命週期假設的機會，也是重新引入 bug 的機會——讀 `io_uring/io-wq.c` 的 git log 能看到這段演進。
- **多 shot 操作（multishot poll/accept）**：較新版本的 io_uring 支援「一次提交、持續產生多個完成事件」的操作模式（例如 multishot `ACCEPT`），這進一步拉長了單個 `io_kiocb` 的生命週期、增加了它要跨越的時間窗口——值得關注這類新特性上線後的後續修補紀錄。
- **和 [Ch 41](./41-widening-race-window.md) 的連結**：io_uring 類漏洞的利用穩定性高度依賴能不能精確控制 race window，學完並發與 race condition 整個 Part 之後，回來重新評估 CVE-2022-2602 的觸發穩定化策略會更有體感。
- **從防禦角度思考表達力與攻擊面的取捨**：io_uring 用「關掉它」而不是「持續加固它」處理，這和 [Ch 30](./30-netfilter-nftables.md) 結尾的立場一致——當一個子系統的複雜度和歷史 bug 密度達到某個程度，攻擊面縮減比持續加固更划算。把這個邏輯和 [Ch 51](./51-defense-detection.md) 的 hardening 思路連起來。
- **零拷貝（zero-copy）與新 opcode 持續擴張攻擊面**：較新版本的 io_uring 持續加入零拷貝網路傳送（`IORING_OP_SEND_ZC`）、`io_uring` 對檔案系統操作的擴充（`IORING_OP_OPENAT`/`UNLINKAT`）——每個新 opcode 都是一次新的「這個操作在非同步模型下的生命週期要怎麼管」的設計決策，也是新的潛在 bug 來源。追蹤 `io_uring/opdef.c` 裡 opcode 表的成長速度，能直接感受到這個攻擊面持續在擴張，不是一個已經穩定下來的子系統。
- **CVE-2024-1086（nf_tables）與 io_uring 類漏洞的共同危害放大器**：兩者都展示了「單一子系統的 bug，一旦被證明可以穩定利用，很快會被收錄進公開 PoC 甚至 CISA KEV」的現象——這提醒我們評估風險時，不能只看 CVSS 分數,還要看「這個 bug class 是否已經有公開、可重複的利用範本」。

## 動手練習

> 以下操作在**你自己擁有的 Linux VM** 內進行，涉及 kernel 原始碼閱讀的部分**未實測**，為理論預期行為；驗證方式見各步驟說明。

1. **讀兩個 fix commit 的完整 diff**：找 `776617db78c6`（"io_uring/rsrc: check for nonconsecutive pages"）與 `0091bfc81741b8d3aeb3b7ab8636f911b2de6e80`（"io_uring/af_unix: defer registered files gc to io_uring release"），逐行比對修補前後，確認本章的 root cause 描述是否與原始 diff 一致。
2. **觀察一次最小的 io_uring 生命週期**：寫一個最小的 C 程式用 `io_uring_setup()`、提交一個 `IORING_OP_NOP`、讀回 CQE（可以用 `liburing` 簡化，或手刻 `io_uring_enter(2)` 呼叫），用 `strace` 觀察實際發出的 syscall 數量，對照傳統 `read()`/`write()` 做同等操作的 syscall 數量差異。
3. **驗證 `io_uring_disabled` 的行為**：在 VM 裡 `sysctl kernel.io_uring_disabled=2`，確認任何 process（包括 root）呼叫 `io_uring_setup()` 都失敗；改回 `0`，確認恢復正常。記錄你的 kernel 版本是否帶這個 sysctl（如果沒有，查一下你的 kernel 版本相對併入 commit 的時間順序）。
4. **讀 `io_sqe_buffer_register()` 的現行版本**，確認修補後的「非連續頁檢查」具體怎麼實作，和本章偽碼描述的邏輯差異在哪裡。
5. **對照 io_uring 與 Linux AIO 的介面差異**：寫兩個功能相同的最小程式（讀一個檔案的固定長度），一個用 `libaio` 的 `io_submit()`，一個用 `liburing`，比較程式碼複雜度與（若你的 VM 支援 `perf stat`）syscall 次數的差異，驗證本章「表達力換效能」的論點。
6. **讀 Jann Horn 對 CVE-2022-2602 類型漏洞的公開分析**（搜尋關鍵字 "Jann Horn io_uring unix gc"），記下他怎麼描述 race window 的精確觸發條件，對照 [Ch 41](./41-widening-race-window.md) 的技巧清單，標出他用了哪幾種。

## 本章重點整理

- io_uring 的礦區本質：共享記憶體 ring 換來的效能優勢，代價是非同步執行模型天生的生命週期不確定性，加上不需要任何 capability 的預設開放可達性。
- 兩個代表 CVE 展示兩種不同層次的根因：效能優化引入未驗證假設（CVE-2023-2598，實體記憶體邊界原語）、跨子系統生命週期假設不一致（CVE-2022-2602，`struct file` UAF）。
- 利用升級方向依原語性質分叉：實體記憶體邊界原語走向 Dirty Pagetable 類技巧（[Ch 23](./23-dirty-pagetable.md)），標準 UAF 走向 spray + data-only 的通用升級路（[Ch 19](./19-spray-objects.md)、[Ch 29](./29-data-only-attack.md)）。
- 防禦的結構性解法同樣是攻擊面縮減：`io_uring_disabled` 這個專門為此而生的 sysctl，以及 kernelCTF Hardened target 直接關閉整個子系統，都是「關掉比加固更划算」判斷的實踐。

## 自我檢核

- [ ] 不看筆記，能解釋「為什麼非同步執行模型比同步 syscall 更容易出 UAF/race」。
- [ ] 能畫出 SQE 提交到 CQE 完成的完整流程，標出「立即完成」與「背景執行」兩條分支。
- [ ] 能用一句話分別講出 CVE-2023-2598 與 CVE-2022-2602 的根本原因，並說出它們分屬 Ch 10 分類的哪一類。
- [ ] 面試官問「`io_uring_disabled` 和修補個別 CVE 比，取捨是什麼」，你能從攻擊面縮減 vs 效能/功能保留的角度回答。
- [ ] 能解釋 registered buffer/file 這個設計為什麼同時是效能特性也是額外攻擊面。

## 延伸閱讀

### 漏洞分析（第一手或接近第一手來源）

- **[oss-security — CVE-2023-2598 disclosure](https://seclists.org/oss-sec/2023/q2/132)** — Tobias Holl
  - **讀哪裡**：全文，重點在「huge page 優化如何讓實體頁連續性假設失效」的技術說明段落。
  - **和本章的關聯**：本章 CVE-2023-2598 root cause 分析直接取自這份揭露。
  - **前提**：需要先懂 [Ch 7](./07-buddy-allocator.md) 的實體記憶體/page 概念。
- **[oss-security — CVE-2022-2602 disclosure](https://seclists.org/oss-sec/2022/q4/30)** — Thadeu Cascardo（Canonical）
  - **讀哪裡**：全文，重點在「io_uring 請求處理與 Unix GC 的競爭關係」描述。
  - **和本章的關聯**：本章 CVE-2022-2602 root cause 分析的主要來源；也是 [Ch 40](./40-race-conditions.md) 「跨子系統 race」的一個好案例。

### 官方文件 / 原始碼

- **[`io_uring/rsrc.c`（Bootlin Elixir, Linux 6.6）](https://elixir.bootlin.com/linux/v6.6/source/io_uring/rsrc.c)**
  - **讀哪裡**：`io_sqe_buffer_register()` 與相鄰的 huge page 處理邏輯。
  - **學什麼**：親眼看修補後的非連續頁檢查怎麼實作。
- **[liburing](https://github.com/axboe/liburing)**
  - **讀哪裡**：`examples/` 目錄下的最小範例程式。
  - **為什麼值得讀**：不手刻 `io_uring_enter(2)` 呼叫的話，這是建立 io_uring 直覺最快的方式；本章動手練習第 2 步可以直接參考它的範例結構。

### 官方規則與背景

- **[io_uring: add a sysctl to disable io_uring system-wide（patch 討論串）](https://lore.kernel.org/io-uring/20230630151003.3622786-1-matteorizzo@google.com/)** — Matteo Rizzo
  - **讀哪裡**：patch 說明與討論串裡關於「為什麼需要這個 sysctl」的動機陳述。
  - **和本章的關聯**：直接回答本章「防禦面」一節的設計動機，也能看到核心開發者對 io_uring 攻擊面的評估。
- **[Learnings from kCTF VRP's 42 Linux kernel exploits submissions](https://security.googleblog.com/2023/06/learnings-from-kctf-vrps-42-linux.html)** — Google Security Blog（2023）
  - **讀哪裡**：io_uring 相關段落。
  - **和本章的關聯**：官方第一手視角,解釋 io_uring 為何和 nf_tables 並列為歷史上最大的兩個礦區。

### 延伸的公開分析

- **[Black Hat USA 2023 — "Bad io_uring: A New Era Of Rootkit"](https://i.blackhat.com/BH-US-23/Presentations/US-23-Lin-bad_io_uring-wp.pdf)**
  - **讀哪裡**：全篇，這份 whitepaper 從另一個角度（io_uring 被用作 rootkit/規避偵測的工具,而不只是提權漏洞）討論 io_uring 的風險面。
  - **為什麼值得讀**：補充本章沒有展開的「io_uring 本身的合法功能也可能被濫用於規避傳統 syscall 監控」這個防禦視角,和 [Ch 51](./51-defense-detection.md) 的偵測主題直接相關。
  - **前提**：對 EDR/syscall 監控的基本原理有概念會更容易理解動機。

io_uring 靠「預設開放 + 非同步模型」成為礦區；下一章的 eBPF 走的是第三條路——它的攻擊面不是物件圖複雜，也不是非同步 race，而是**一個驗證器的正確性直接等同於整個子系統的安全性**：verifier 一旦犯錯，使用者就能把任意一段「verifier 認證過安全」的程式碼跑進 kernel，直接換到任意讀寫原語。

→ [Ch 32 eBPF：verifier / JIT、map 型漏洞、verifier bypass](./32-ebpf.md)
