# Ch 43 — 橫向對比：io_uring vs epoll vs libaio vs SPDK

> **目標**：把 io_uring 擺進它真正的座標系。你會拿到一張系統性的對比表，橫軸是四種 I/O 範式（epoll、libaio、io_uring、SPDK/DPDK），縱軸是模型、適用場景、延遲/吞吐/CPU 取捨。最重要的是誠實回答一個每個 io_uring 愛好者都該能答的問題：**什麼時候 io_uring 不是答案？** 學完這章，你在技術選型時能說清楚「為什麼選它」和「為什麼不選它」。

> **環境**：本章是概念對比，不含新的 WSL 實測；引用的數字以 [Ch 41](./41-performance-engineering.md) 的實測與各技術的公開特性為準。對比力求誠實，不吹捧 io_uring。

## 為什麼需要一張對比表

io_uring 的教材很容易寫成傳教——「它取代一切、它是未來」。這對學習有害。真相是：**這四種範式各自佔著一塊最適合的生態位**，io_uring 擴張得很快，但遠沒有把其他三個趕盡殺絕。epoll 在網路 readiness 上依然是預設、libaio 在很多儲存棧裡還在服役、SPDK 在極致低延遲的縫隙裡 io_uring 碰不到。

一個工程師的價值，不在於知道「io_uring 很快」，而在於能說出「這個 workload 我選 X，因為 Y」。這一章就是給你那套判斷依據。

## 先建立直覺：兩個軸把四個範式分開

四種範式可以用兩條軸擺清楚：

```
                  同步完成語意                      非同步完成語意
                 (readiness)                        (completion)
  kernel 管理 ┌──────────────────────┬──────────────────────┐
   (走 syscall)│  epoll               │  libaio（僅 O_DIRECT）│
             │  通用、網路為主        │  io_uring（通用）      │
             ├──────────────────────┼──────────────────────┤
  user 接管  │  （少見）             │  SPDK / DPDK          │
 (kernel bypass)│                      │  user-space polling   │
             └──────────────────────┴──────────────────────┘
```

- **第一條軸：readiness vs completion。** epoll 問「可以讀了嗎」（你自己讀）；libaio/io_uring/SPDK 問「幫我讀，做完告訴我」。這是 [Ch 42](./42-ecosystem.md) 反覆講的那條線。
- **第二條軸：kernel 管理 vs kernel bypass。** 前三個都走 kernel（syscall、kernel 驅動）；SPDK/DPDK 把裝置從 kernel 手上搶過來，在 user space 直接驅動硬體、忙碌輪詢，**完全繞過 kernel I/O 棧**。

io_uring 的位置：**completion + kernel 管理**，一個「通用」的格子。它的野心是把 libaio 的格子完全吃掉（它確實在做），並從 epoll 手上搶網路（進行中），但它碰不到 SPDK 那個 kernel bypass 的格子——那是物理上不同的做法。

## 逐一拆解

### epoll：readiness 的王者，網路的預設

epoll 是 Linux 事件驅動網路的事實標準。模型是 readiness：你把一堆 fd 丟給 epoll，`epoll_wait` 回報哪些可讀/可寫，你**自己**在自己的 stack 上 `read()`/`write()`。

- **強項**：成熟、簡單、無處不在；對「大量連線、每個連線少量資料」的網路服務（web server、proxy）極其契合；buffer 你自己管，生命週期單純。
- **死穴**：**對一般檔案（regular file）無效**——磁碟檔案「永遠 readiness」，epoll 根本不能用來做檔案 async I/O。這是 epoll 最根本的限制，也是 io_uring 誕生動機之一（[Ch 1](./01-why-io-uring.md)）。此外每個 I/O 仍是一個 syscall，高頻下 syscall 成本累積。
- **現況**：Go、Node、Python asyncio、絕大多數網路框架的底層仍是 epoll。它不會消失。

### libaio：Linux 原生 AIO 的半成品

libaio（`io_submit`/`io_getevents`）是 io_uring 之前 Linux 唯一的「真非同步」檔案 I/O 介面。它是 completion 模型的先驅，但做得很不完整。

- **強項**：對 `O_DIRECT` 的 storage I/O 能真正非同步、批次提交；資料庫（MySQL InnoDB、舊版 PostgreSQL 的 effective_io_concurrency 路徑）長期靠它。
- **死穴**（io_uring 就是來修這些的）：**只對 `O_DIRECT` 真非同步**——碰到 buffered I/O、或 metadata 操作，它會**悄悄退化成同步阻塞**，這是 libaio 惡名昭彰的「假 async」。而且它只支援讀寫，不支援網路、不支援 `accept`/`fsync`/`openat` 這些；每次提交仍是 syscall。
- **現況**：**正在被 io_uring 取代**。io_uring 在 storage 這塊是 libaio 的嚴格超集（能做它全部，還做得更好、更通用）。新系統沒有理由選 libaio；它的存在主要是歷史遺留。

### io_uring：通用 completion

io_uring 的設計就是「libaio 該有的樣子，再加上網路、加上零 syscall 的可能」。

- **強項**：**通用**——同一個 ring 裡混跑檔案讀寫、網路收發、`accept`、`fsync`、`openat`、`timeout`、甚至 NVMe passthrough（[Ch 44](./44-future.md)）；真非同步（不像 libaio 對 buffered 退化）；批次提交 + 共享記憶體把 syscall 壓到最低、SQPOLL 下甚至零 syscall；registered files/buffers、provided buffer、multishot、zero-copy 這套機制讓它在高 IOPS 下省掉大量 per-I/O overhead。
- **代價**：**複雜**——生命週期、記憶體屏障、completion 模型的架構衝擊（[Ch 42](./42-ecosystem.md)）；**攻擊面大**——近年 kernel 漏洞大戶，多數加固環境預設停用（[Ch 39](./39-mitigations-detection.md)）；跨環境行為不一致（版本、config、被 disable 的可能）。
- **現況**：擴張最快的那一個。storage 上吃掉 libaio，網路上從 epoll 搶地盤，但兩個戰線都還沒完全拿下。

### SPDK / DPDK：kernel bypass，另一個次元

SPDK（storage）與 DPDK（network）不是 io_uring 的改良版，是**完全不同的哲學**：把裝置從 kernel 的驅動手上拿走，用 user-space 驅動 + 忙碌輪詢直接操作硬體佇列，**整條 kernel I/O 棧都不走**。

- **強項**：**延遲的物理極限**——沒有 syscall、沒有中斷、沒有 kernel 棧、沒有 context switch，一個核綁死一個裝置佇列，微秒級甚至次微秒級的尾延遲。NVMe-oF target、高頻交易、電信封包處理這些「延遲就是一切」的場景的最後答案。
- **代價**：**極端**——燒整顆核做輪詢（閒置也燒）；放棄 kernel 的一切（檔案系統、權限、多工共享、記憶體保護的部分好處）；要大頁、要獨佔裝置、要專門的 user-space 驅動；開發與維運成本高昂；一台機器基本上只為這一件事服務。
- **現況**：佔著「極致低延遲」這塊 io_uring 碰不到的生態位，兩者長期共存。

## 深入一層：兩個最關鍵的機制細節

對比表好記，但有兩個細節值得多講一層，因為它們是「io_uring 為什麼會出現」的直接原因。

**為什麼 epoll 對一般檔案「無效」？** epoll 問的是「這個 fd readiness 了嗎」，而 readiness 的語意是「現在讀不會阻塞」。對 socket/pipe 這有意義——資料還沒到就是 not ready。但**一般磁碟檔案永遠是 ready 的**：kernel 認為你隨時可以對它發 `read`。問題是那個 `read` 本身**可能因為要等磁碟而阻塞**——而 epoll 根本沒有「這個 read 會不會等磁碟」這個概念。於是對檔案，epoll 給你的答案永遠是「ready」，`epoll_wait` 立刻返回，你去 `read` 卻可能卡在磁碟上。這就是「epoll 做不了檔案 async I/O」的根本機制：readiness 模型和「磁碟延遲」是錯配的。io_uring 的 completion 模型沒這問題——它說「幫我讀完再通知」，磁碟等多久都在 kernel 那邊非同步等。

**libaio 的「假 async」具體是什麼？** libaio 宣稱非同步，但它只在 `O_DIRECT`（繞過 page cache）時才真的非同步。一旦你對 buffered 檔案（走 page cache）用 libaio，`io_submit` 會在**需要做阻塞動作時悄悄退化成同步**——它就地把 I/O 做完才返回，你的「非同步提交」變成了阻塞呼叫，而且沒有任何錯誤或警告告訴你。更糟的是連 `O_DIRECT` 下，某些 metadata 操作（配置區塊、更新 inode）也可能讓 `io_submit` 阻塞。結果是 libaio 的延遲充滿不可預測的尖刺。io_uring 把這個修乾淨：它有真正的 async 執行路徑（io-wq worker、[Ch 26](./26-io-wq.md)），buffered 也好、metadata 也好，都不會讓提交端阻塞。**「io_uring 是 libaio 該有的樣子」這句話，一半就是指這個「不再假 async」。**

## 對比與取捨總表

| 維度 | epoll | libaio | io_uring | SPDK/DPDK |
|---|---|---|---|---|
| 模型 | readiness | completion | completion | completion + 輪詢 |
| kernel? | kernel | kernel | kernel | **bypass** |
| 檔案 async | 否（檔案無效） | 是，僅 `O_DIRECT` | 是，通用 | 是（自管） |
| 網路 | 是，主場 | 否 | 是，擴張中 | 是（DPDK） |
| metadata/其他 op | 否 | 否 | 是（openat/fsync/…） | 否 |
| syscall/IO | 1+ | 1（批次攤） | 可達 0（SQPOLL） | 0 |
| buffered I/O 真 async | — | 否，退化同步 | 是 | — |
| 延遲極限 | 中 | 中 | 低 | **最低** |
| CPU 效率 | 高（省） | 高 | 高（可燒核換延遲） | 低（必燒核） |
| 複雜度 | 低 | 中 | **高** | **極高** |
| 攻擊面/安全 | 小 | 小 | **大（常被停用）** | 自管（繞過 kernel） |
| 成熟度 | 極成熟 | 成熟（遺留） | 快速演進 | 成熟（小眾） |
| 典型採用 | 幾乎所有網路框架 | MySQL、舊 PG | PG18、ScyllaDB、Ceph | NVMe-oF、HFT、電信 |

## 延遲與 CPU 的數量級直覺

對比表容易讓人以為差異是線性的，其實是**數量級**的。給一個粗略的直覺（實際值隨硬體大幅變動，這裡只為建立量級感）：

- **epoll + 同步 read 的網路往返**：每個 I/O 至少一次 syscall（數百 ns）+ 可能的中斷喚醒 + 排程。延遲落在**數微秒到數十微秒**，且尾延遲受排程抖動影響大。
- **io_uring（batched、registered）**：syscall 成本攤平、可達零 syscall；完成仍走 kernel（中斷或 IOPOLL）。延遲比 epoll 路徑低一截，尾延遲更穩（少中斷打斷），但**仍在 kernel 的數量級**。
- **SPDK（kernel bypass + 忙碌輪詢）**：沒有 syscall、沒有中斷、沒有 context switch，一個核綁死一個佇列。延遲壓到**次微秒**，而且**確定性**極高——這是 io_uring 架構上到不了的地方。

關鍵直覺：**io_uring 和 epoll 的差距是「省掉固定開銷」（同一數量級內的改善）；io_uring 和 SPDK 的差距是「要不要走 kernel」（跨數量級、且關乎確定性）。** 這解釋了為什麼極致低延遲場景即使 io_uring 已經很好，仍會選 kernel bypass——不是 io_uring 不夠快，是它在的那個數量級就是有 kernel 路徑的固定成本。CPU 這一軸則相反：epoll 最省（閒置不燒核），io_uring 可選擇性燒核換延遲，SPDK 必燒核。**沒有一個點同時在延遲和 CPU 上都最優**，這再次印證「不會贏家通吃」。

## 什麼時候 io_uring 不是答案

這是全章最重要的一節。io_uring 很強，但下面這些情況你該選別的：

1. **只做網路 readiness、連線多而資料少、且要極致可攜性。** 選 **epoll**。io_uring 在這塊的優勢沒大到值得你扛它的複雜度與安全爭議；Go/Node 不換 epoll 是理性的。
2. **延遲是唯一指標，願意為它犧牲一切。** 選 **SPDK/DPDK**。io_uring 再快也還在 kernel 裡——它有 syscall（除非 SQPOLL 也燒核）、有 kernel 棧、有中斷路徑。要壓到次微秒尾延遲、要確定性，kernel bypass 才是物理答案。**「io_uring 夠快了」在 HFT/NVMe-oF 的世界裡是不夠的。**
3. **部署環境會停用 io_uring。** 很多加固環境（ChromeOS、Android、部分雲 managed 服務、seccomp 沙箱）預設 `io_uring_disabled=2` 或用 seccomp 擋掉（[Ch 39](./39-mitigations-detection.md)）。你的程式若要跑在這些地方，依賴 io_uring 等於賭它沒被關——該選 epoll + thread pool 當保底。
4. **團隊/系統扛不起它的複雜度與攻擊面。** io_uring 是 kernel 漏洞大戶（Part 4 整個在講這個）。如果你的威脅模型容不得多開一個大 kernel 攻擊面、或團隊沒有能力正確處理 completion 模型的生命週期，**保守的 epoll/同步路徑是負責任的選擇**。
5. **workload 根本沒有並行深度。** [Ch 41](./41-performance-engineering.md) 實測過：`qd=1` 時 io_uring 比同步 read 還慢。一次只有一個 in-flight I/O 的程式，io_uring 只是徒增複雜度。

反過來，**io_uring 是最佳解**的情況也很清楚：通用的高並行 I/O（混檔案 + 網路 + metadata）、要榨 syscall 成本、能跑在你控制得了的新 kernel 上、且願意按 completion 模型設計——這正是 PostgreSQL、ScyllaDB、現代 proxy 的處境。

## 選型實戰：四個具體場景

把上面的原則套到真實情境，練習「說出為什麼」：

**場景一：一台 web proxy，十萬條長連線，每條偶爾傳幾 KB。**
瓶頸是「管理大量 fd 的 readiness」，不是 I/O 吞吐。選 **epoll**。io_uring 在這裡的 syscall 節省不明顯，卻要扛 completion 模型重寫與安全爭議。Go/Node 寫這種服務不換 epoll，是對的。

**場景二：一個資料庫的儲存引擎，每秒上百萬次 4 KB 隨機讀，buffer 預先配好復用。**
瓶頸是 per-I/O 的 syscall 與 overhead。選 **io_uring**，並開 registered files + fixed buffers（[Ch 41](./41-performance-engineering.md) 實測這組合值約 1.3×+1.1×）。這正是 PostgreSQL 18、ScyllaDB 的處境。若還嫌 kernel 路徑太重、且願意獨佔硬體，才考慮往 SPDK 走。

**場景三：一個 NVMe-oF target，SLA 是 p999 延遲次微秒、延遲抖動零容忍。**
瓶頸是 kernel 路徑本身的中斷與排程抖動。選 **SPDK**。io_uring 即使開 IOPOLL 也還在 kernel 裡，壓不到那個確定性。這裡「io_uring 夠快」不成立——要的是 kernel bypass 的物理極限。

**場景四：同一個服務要上架到 ChromeOS / Android / 受 seccomp 限制的 FaaS。**
瓶頸是「io_uring 可能根本被關掉」（`io_uring_disabled=2` 或 seccomp 擋掉，[Ch 39](./39-mitigations-detection.md)）。選 **epoll + thread pool** 當保底；就算你想用 io_uring 也必須寫 fallback。這裡可用性壓倒效能。

四個場景、四個不同答案——這才是「懂對比」的樣子：不是記住誰快，而是先問「瓶頸在哪、環境容不容得下」，再選。

## 實務上常常不是二選一：混合架構

對比表把四個範式分得很清楚，但真實系統**常常同時用好幾個**，因為它們各管一塊：

- **io_uring（storage）+ epoll（network）**：很多系統的磁碟 I/O 走 io_uring（吃它的真 async 與批次），網路仍走成熟的 epoll reactor——尤其是既有服務，網路層改動成本太高、收益不明顯，就只在新加的儲存路徑上用 io_uring。
- **io_uring + 退回同步/thread pool**：為了能跑在「io_uring 可能被停用」的環境（[Ch 39](./39-mitigations-detection.md)），程式偵測到 io_uring 不可用就退回 thread pool——libuv 正是這麼做（io_uring 做不到就回 thread pool）。
- **io_uring 控制面 + SPDK 資料面**：極致低延遲系統可能用 SPDK 跑最熱的資料路徑，但用 io_uring（或一般 syscall）處理控制面、管理面那些不那麼延遲敏感、又需要 kernel 服務（檔案系統、權限）的部分。

這帶出一個比「誰比誰快」更實用的心法：**選型的單位常常不是「整個系統選一個」，而是「每條 I/O 路徑各自選最適合的」。** 一個成熟系統的 I/O 層，很可能是 io_uring、epoll、甚至 SPDK 的混合，各自守著自己最適合的那塊。能畫出「我的系統哪條路徑該用哪個」的人，比只會說「io_uring 最快」的人，選型能力高一個層次。

## 踩雷集錦

1. **把 io_uring 當 epoll 的「更快版」直接替換。** 它是 completion 不是 readiness，buffer 所有權和錯誤時機都不同。這不是升級，是換範式。
2. **以為 io_uring 讓 SPDK 過時。** 兩者在不同次元：io_uring 在 kernel 內把 kernel I/O 做到最好，SPDK 乾脆不要 kernel。極致低延遲場景 io_uring 追不上 kernel bypass，這是架構決定的，不是調優能補的。
3. **還在新專案裡選 libaio。** 除非你被鎖在舊 kernel，否則沒有理由——io_uring 在 storage 上是 libaio 的嚴格超集，而且沒有「buffered 退化成同步」的坑。
4. **對比時只比吞吐、不比 CPU 與尾延遲。** io_uring 可以用「燒核」（SQPOLL/IOPOLL）換延遲，SPDK 更是必燒核。只看 IOPS 不看每 I/O 的 CPU 成本，對比就是假的（[Ch 41](./41-performance-engineering.md) 的 benchmark 鐵律）。
5. **忽略「會不會被停用」這個維度。** 在選型表上，「攻擊面/可被 disable」和延遲、吞吐一樣是硬指標。在加固環境裡，一個「可能被關掉」的介面，可用性為零。

## 進階：為什麼不會有「贏家通吃」

把四個範式放一起看，會發現它們佔的是**不同的物理權衡點**，不是同一條線上的優劣：

- epoll 把「簡單 + 可攜 + 省 CPU」做到頂，代價是不能做檔案、每 I/O 一 syscall。
- io_uring 把「通用 + 真 async + 低 syscall」做到頂，代價是複雜 + 大攻擊面。
- SPDK 把「延遲 + 確定性」做到物理極限，代價是燒核 + 放棄 kernel 的一切。

這三個頂點之間是連續的權衡空間，沒有一個點能同時最優。所以**不會有贏家通吃**——io_uring 會繼續吃掉 libaio（因為它在那個權衡點上嚴格更好），但 epoll 和 SPDK 各自守著 io_uring 到不了的頂點。理解這張權衡圖，比記住任何「X 比 Y 快幾倍」都有用。這也是為什麼下一章談 io_uring 的未來時，方向是「把 completion 模型擴張到更多系統介面」（把更多東西拉進它的格子），而不是「取代一切」。

## 本章重點整理

- 四種範式用兩軸分開：**readiness vs completion**、**kernel 管理 vs kernel bypass**。io_uring = completion + kernel 管理的「通用」格子。
- **epoll**：readiness、網路主場、對檔案無效、極成熟可攜；**libaio**：completion 但只對 `O_DIRECT` 真 async、正被 io_uring 取代；**io_uring**：通用 completion、擴張最快、但複雜且攻擊面大；**SPDK/DPDK**：kernel bypass、延遲物理極限、必燒核。
- **io_uring 不是答案的五種情況**：純網路 readiness 要極致可攜（選 epoll）、延遲至上（選 SPDK）、環境會停用 io_uring、扛不起複雜度/攻擊面、workload 無並行深度。
- 不會有贏家通吃——三個頂點佔著互斥的物理權衡點；io_uring 吃掉 libaio，但碰不到 epoll 與 SPDK 各自的極值。

## 自我檢核

- [ ] 我能用兩條軸把 epoll/libaio/io_uring/SPDK 擺到正確位置
- [ ] 我能說出 epoll 為什麼不能做檔案 async I/O
- [ ] 我能說出 libaio「假 async」具體指什麼，以及 io_uring 怎麼修掉
- [ ] 我能講出至少三種「該選別的、不選 io_uring」的情況，並說清理由
- [ ] 我能解釋為什麼 io_uring 追不上 SPDK 的延遲，且這是架構決定而非調優問題

## 延伸閱讀

### 一手設計文件

- **[Efficient IO with io_uring](https://kernel.dk/io_uring.pdf)** — Jens Axboe
  - **讀哪裡**：開頭對 libaio 缺陷的批判——這是「io_uring 要修 libaio 什麼」最權威的一手說明
  - **和本章關聯**：本章 libaio 那節的「假 async」就是這份文件的核心動機

### 技術對比文章

- **[The rapid growth of io_uring](https://lwn.net/Articles/810414/)** 及 LWN 的 io_uring 系列 — Jonathan Corbet
  - **這篇說什麼**：io_uring 相對既有介面的定位與演進，LWN 的中立視角
  - **讀哪裡**：和 epoll/aio 的對比段，補齊本章的歷史脈絡

### kernel bypass 對照

- **[SPDK 官方文件 — Introduction / NVMe Driver](https://spdk.io/doc/)**
  - **讀哪裡**：user-space polling 與「一個核綁一個裝置佇列」的設計理念
  - **為什麼值得讀**：親眼看 kernel bypass 長什麼樣，你才會真的懂「io_uring 到不了那個延遲頂點」是什麼意思
  - **注意**：SPDK 的程式設計模型和 io_uring 差異巨大，別期待能直接類比

把 io_uring 放進座標系之後，最後一章我們看它往哪裡長——zero-copy receive、ublk、NVMe passthrough、以及「io_uring 作為通用 async syscall 介面」這個最大的願景。那些正是在把更多系統介面，拉進本章那個「通用 completion」的格子裡。

→ [Ch 44 未來：zero-copy rx、ublk、Rust for Linux](./44-future.md)
