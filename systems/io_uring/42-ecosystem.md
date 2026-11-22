# Ch 42 — 生態系：tokio-uring、PostgreSQL/Ceph、語言綁定

> **目標**：離開 benchmark，看 io_uring 在真實世界被誰採用、怎麼採用、以及**誰刻意不採用、為什麼**。你會看到 PostgreSQL 18 把 io_uring 做成一個可切換的 AIO 後端、ScyllaDB/Seastar 與 Rust 的 thread-per-core 陣營為什麼天生契合 io_uring、libuv/Node/Go 為什麼謹慎甚至拒絕，以及 completion 模型如何逼著既有的 reactor 架構重寫。這一章幫你把前 41 章學的機制，對應到「生產系統實際怎麼用」。

> **環境**：本章是生態綜述，不含 WSL 實測程式。所有「誰用了、哪個版本、功能狀態」都經查證並附來源；查不到或不確定的，會明確降級措辭並標注。查證時間：2026-11。io_uring 生態變動極快，讀的時候請以各專案最新文件為準。

## 為什麼要讀生態系

你可能會問：學機制就好，管別人怎麼用做什麼？三個理由：

1. **採用模式就是最好的教材。** PostgreSQL 把 io_uring 做成「可切換後端」、Seastar 做成「reactor backend」、tokio 做成「獨立 runtime」——這三種整合方式，正好對應 io_uring 能切入既有系統的三個層次。看懂它們怎麼整合，比任何教科書都清楚 io_uring 的適用邊界。
2. **「誰不用」比「誰用」更有資訊量。** Go 到今天還是 epoll，libuv 把 io_uring 預設關掉過，這些「拒絕」背後的理由（安全、可攜性、completion 模型的架構衝擊）是你評估「我該不該上 io_uring」時最該先想清楚的。
3. **面試與選型都會問。** 「你會在什麼系統選 io_uring、什麼系統不選」是 kernel/後端面試的常見題，也是真實選型決策。這一章給你有依據的答案。

## 先建立直覺：completion 模型逼著架構改變

io_uring 和 epoll 最根本的差異，不在快慢，在**程式的控制流**：

- **epoll 是 readiness 模型**：核心問「這個 fd 可以讀了嗎」，你收到「可以了」的通知，然後**自己**去 `read()`。你的 buffer 你自己管，read 在你的 call stack 上同步發生。
- **io_uring 是 completion 模型**：你說「幫我把這段讀進這個 buffer」，核心**替你做完**，然後通知「做完了，結果在這」。buffer 的所有權在請求期間交給了 kernel。

這個差異會滲透到整個架構。一個為 epoll 寫的 reactor（libevent、Netty、Node 的 libuv），核心迴圈是「等 readiness → 分派 → 應用自己做 I/O」。要改用 io_uring，不是把 `epoll_wait` 換成 `io_uring_enter` 就好——**buffer 生命週期、錯誤處理時機、取消語意全都變了**。這是為什麼很多既有專案的 io_uring 整合又慢又痛，而**新寫的 thread-per-core 系統**（下面會談）反而最先吃到紅利：它們沒有歷史包袱，可以直接照 completion 模型設計。

> 記住這條主線：**io_uring 對「從零設計的 thread-per-core 系統」是天作之合，對「既有的 epoll reactor」是一場架構手術。** 本章的採用版圖幾乎都能用這條線解釋。

### 具體看：同一個 echo 迴圈，兩種模型

把差異落到程式碼上最清楚。一個 epoll reactor 的收發迴圈（偽碼）：

```c
// epoll：readiness。buffer 是「我的」，read 在我的 stack 上同步發生
for (;;) {
    int n = epoll_wait(epfd, events, MAXEV, -1);
    for (int i = 0; i < n; i++) {
        int fd = events[i].data.fd;
        if (events[i].events & EPOLLIN) {
            char buf[4096];                       // ← buffer 在我的 stack
            ssize_t r = read(fd, buf, sizeof buf); // ← 我自己 read，同步、就地
            if (r > 0) enqueue_write(fd, buf, r);  // 回寫也我自己管
        }
    }
}
```

同一件事改成 io_uring completion 模型：

```c
// io_uring：completion。buffer 的所有權在請求期間「借給 kernel」
for (;;) {
    io_uring_submit_and_wait(&ring, 1);
    struct io_uring_cqe *cqe; unsigned head; int cnt = 0;
    io_uring_for_each_cqe(&ring, head, cqe) {
        struct conn *c = io_uring_cqe_get_data(cqe);   // ← 靠 user_data 找回上下文
        if (c->state == READING) {
            if (cqe->res > 0) {                        // ← 讀「已經完成」，結果在 c->buf
                arm_write(&ring, c, cqe->res);         // 不是我去 write，是再提交一個 write 請求
            } else { close_conn(c); }                  // 錯誤從 cqe->res 來，不是 read 當場
        } else /* WRITING */ {
            arm_read(&ring, c);                         // 寫完了，再 arm 下一個 read
        }
        cnt++;
    }
    io_uring_cq_advance(&ring, cnt);
}
```

三處架構衝擊一眼就看出來：

1. **buffer 所有權變了。** epoll 版 `buf` 在 stack、read 完就沒事；io_uring 版 `c->buf` 必須**在請求存活期間一直有效**（不能是 stack 區域、不能提早 free）——這正是 Part 4 一堆 UAF 的來源。
2. **控制流從「同步就地」變「狀態機」。** epoll 版 read 就在迴圈裡；io_uring 版你得用 `c->state` 記住「這個 CQE 是哪個階段的完成」，整個連線變成顯式狀態機。
3. **錯誤時機變了。** epoll 版 `read` 當場回錯；io_uring 版錯誤從 `cqe->res` 非同步回來（[Ch 8](./08-cqe-anatomy.md)）。既有程式碼裡所有「檢查 read 回傳值」的地方都要搬位置。

這就是「架構手術」的實體。一個寫了十年的 epoll 事件庫，要把這三點全改對，工程量遠超過「換個 API」——這解釋了為什麼 libuv/Node 寧可只在一小塊檔案 I/O 上試水，而全新的 thread-per-core 框架反而最先完整吃到 io_uring。

## 資料庫：PostgreSQL 18 的 AIO 後端

**PostgreSQL 18（2025-09-25 釋出）引入了非同步 I/O 子系統**，這是 io_uring 進入主流資料庫最受矚目的一步。設計上很值得學：

- 新增 `io_method` 設定，三選一：
  - `sync`：維持 18 以前的同步行為（等於沒開 AIO）。
  - `worker`：用一組 worker process 做非同步 I/O，**跨平台、預設值**。
  - `io_uring`：**僅限 Linux**，需要 Postgres 編譯時帶 `--with-liburing`，在夠新的 kernel 上用一個 user/kernel 共享的 ring 來做 I/O，省掉 worker process 的 IPC。
- **現階段只覆蓋讀路徑**（sequential scan、bitmap heap scan、vacuum 等會受益），寫路徑的非同步化標為未來版本可能擴充。
- 效能：有廠商部落格宣稱 Linux + io_uring 可帶來 2–3 倍提升，但也有來源較保守，指出在部分 benchmark 上它和 PG 17 的 streaming I/O 相近。**把 2–3 倍當成「特定 workload 下的上限」而非普遍值。**

PostgreSQL 的做法是整章最值得抄的整合範式：**把 io_uring 做成一個可熱切換的後端**，而不是賭上整個 I/O 層。使用者可以 `io_method=worker` 當保險、`io_method=io_uring` 當進階選項，kernel 不支援或出問題就退回 worker。這種「漸進採用、保留退路」的工程保守主義，正是把一個「有安全爭議、快速演進」的 kernel 介面放進關鍵基礎設施的正確姿態。

## thread-per-core 陣營：Seastar/ScyllaDB、Rust 的 glommio/tokio-uring

io_uring 最天然的歸宿是 **thread-per-core（每核一執行緒）** 架構：每顆 CPU 綁一個 thread、一個 ring，不共享、不加鎖，完全照 completion 模型設計。前一章談的 `SINGLE_ISSUER` / `DEFER_TASKRUN` 就是為這種模型準備的。

**Seastar / ScyllaDB**（C++）：Seastar 是 ScyllaDB 底下的 thread-per-core 框架，很早就加了 **io_uring reactor backend**（用 `--reactor-backend=io_uring` 選）。既有的「對稱」後端讓每個 shard 各自持有一個私有 io_uring 實例，poll 用 `IORING_OP_POLL_ADD`、檔案用 read/write/fsync op。2026 年 ScyllaDB 進一步發表了**非對稱（asymmetric）後端**：把 I/O 處理從應用 shard 挪到專用的 worker core 上，官方文件直言它適合 compute-intensive workload，**對 I/O-bound 的負載反而可能因 worker core 成為瓶頸而無益**——這種誠實的適用邊界標注，正是生產系統該有的態度。

**Rust 陣營**（thread-per-core）：

- **glommio**（DataDog 發起）：純 thread-per-core 的 Rust async runtime，直接建在 io_uring 上。最新版 **0.9.0（2024-03）**，之後未見新 release；README 自承「對一個好的 thread-per-core 系統關鍵的若干功能尚未實作」，最大缺口是 per-shard 記憶體配置器。**狀態：可用但維護活躍度偏低（降級措辭：2024 後未見新版，無法確認目前維護狀態）。**
- **tokio-uring**（tokio 官方的實驗性 crate）：在 tokio 之上提供 io_uring 支援，最新 **0.5.0**。社群討論稱它「沒死，但需要照顧」，存在 bus factor（維護者過於集中）問題，另有社群 fork（如 `tokio-uring-xitca`）以最小維護續命。**狀態：低度維護，非拋棄。**
- **monoio**（ByteDance）：另一個 thread-per-core Rust runtime，Apache Iggy 等專案曾評估採用。

這裡有個誠實的觀察：**Rust 的 io_uring runtime 普遍處於「有人用、但維護能量不足」的狀態。** 原因之一是 tokio 本身的 epoll-based 模型已經夠好，大多數 Rust 服務沒有強到要換 runtime 的動機；只有真的在追求極致 thread-per-core 的場景才會跳進來，而那是個相對小眾的圈子。這不是 io_uring 不好，是「換掉整個 async runtime」的門檻太高。

## 儲存：Ceph 與 SPDK 的對照

**Ceph BlueStore** 的 `KernelDevice` 路徑支援用 io_uring 取代傳統的 libaio/同步 I/O，由組態項控制（原始碼中名為 `bdev_ioring` 一系列，另有 `bdev_ioring_hipri`、`bdev_ioring_interval`）。**降級措辭：本章未能透過官方文件查證其預設值與最低 Ceph 版本；就作者所知它預設關閉、並非 BlueStore 的主要 I/O 路徑，確切狀態請以 Ceph `src/common/options/` 的設定定義與對應版本 release notes 為準。** Ceph 的例子說明一件事：連「本就用 libaio 做 O_DIRECT storage」的系統，io_uring 也是「多一個可選後端」，而非直接取代——和 PostgreSQL 的保守策略一致。

至於 **SPDK**，它走的是完全不同的路（user-space polling、kernel bypass），嚴格說不算 io_uring 的採用者，而是它在「極致低延遲儲存」這塊的**競爭對手**。這條線留到 [Ch 43](./43-comparison.md) 專門對比。

## 觀望與拒絕：libuv/Node 與 Go

這一節比上面都重要——**它們為什麼不（全力）用 io_uring**。

**libuv / Node.js（謹慎、且出過安全事故）**：

- libuv 從 **1.45.0（2023-05-19）** 起用 io_uring 實作部分 `uv_fs` 檔案操作（read/write、fsync/fdatasync、stat/fstat/lstat 家族），kernel 夠新才走 io_uring，否則退回 thread pool。
- 但隨後一路「往回收」：1.46 加了更多 fs op，卻也**在 Android 上停用 io_uring**；1.47 **在 pre-5.10.186 kernel 與 32-bit ARM 上停用**。
- 更關鍵的是安全事故 **CVE-2024-22017**：libuv 的 io_uring 操作若在 `setuid()` **之前**初始化，`setuid()` 之後它仍以原權限執行——等於掉權（drop privilege）失效。影響 Node.js 18.18.0+、20.4.0+、21；官方給的緩解就是**用 `UV_USE_IO_URING=0` 關掉 io_uring**。
- 這整條軌跡是 io_uring「可攜性與安全成本」的縮影：一個號稱加速的功能，因為跨環境行為不一致、又撞上權限模型，最後變成「預設保守、提供開關」。

**Go runtime（到目前仍是 epoll）**：

- Go 的 netpoller 至今走 epoll，社群長期討論（Go issue **#31908**）是否改用 io_uring，但沒有定論、也沒採用。
- 推測的理由（非官方定論，降級措辭）：(1) **安全**——io_uring 是 kernel 層的大攻擊面，很多加固環境預設停用它（見 [Ch 39](./39-mitigations-detection.md)），Go 作為通用 runtime 不願預設依賴一個「可能被關掉」的介面；(2) **可攜性**——Go 要跑在 macOS/Windows/舊 Linux，epoll/kqueue 的抽象更穩；(3) **completion 模型的架構衝擊**——Go 的 netpoller 深度綁定 goroutine 排程，換成 completion 模型要動到 runtime 核心。
- 這是前面那條主線的最強例證：**Go 是典型「有歷史包袱的 epoll reactor」，換 io_uring 的成本遠大於收益，所以它理性地不換。**

## 語言綁定一覽

| 語言 | 綁定 | 層次 | 狀態 |
|---|---|---|---|
| C | **liburing**（Axboe 官方） | 薄封裝，`io_uring_prep_*` helper | 事實標準，本課全程用它 |
| Rust | **io-uring** crate（tokio-rs） | 低階、安全包裝 raw ring | 穩定，glommio/tokio-uring 建在其上 |
| Rust | **glommio / tokio-uring / monoio** | 完整 async runtime | 可用，維護能量不一（見上） |
| C++ | **Seastar** | 框架內建 reactor backend | 生產級（ScyllaDB） |
| Python | 第三方綁定（如 `liburing` PyPI 包） | ctypes/cython 封裝 | 非標準庫；asyncio 預設**不**用 io_uring |

重點提醒：**Python 的 asyncio 預設走 selector（epoll）**，沒有官方 io_uring event loop；要用得靠第三方綁定直接操作 ring，生態遠不如 C/Rust 成熟。這和 Go 的情況類似——高階、重可攜性的語言 runtime，對 io_uring 普遍觀望。

## 語言綁定再深一層：crate、runtime、FFI 的分工

上面的表容易讓人以為「綁定」是一件事，其實分三層，混淆它們是選型常見錯誤：

- **底層 ring 綁定**（Rust 的 `io-uring` crate、C 的 liburing）：只把 raw ring 包成安全/順手的 API，**不**提供 async runtime。你用它仍要自己管事件迴圈。它穩定、職責單一——glommio/tokio-uring 都建在 Rust `io-uring` crate 之上。
- **async runtime**（glommio、tokio-uring、monoio）：在底層綁定之上提供完整的 `async/await` 執行環境。換 runtime 是大決定（影響整個程式的結構），這也是為什麼它們採用門檻高、使用者相對少。
- **FFI / 第三方包**（Python 的各種 io_uring binding）：透過 ctypes/cython 把 liburing 包進高階語言。這類通常是個人或小團隊維護，覆蓋不全、版本跟得慢。

選型含義很具體：如果你是 Rust 寫一個**既有 tokio 服務**，想試 io_uring 的檔案 I/O，不該整個換 runtime（tokio-uring），而可能只在局部用 `io-uring` crate 搭自己的小迴圈——換 runtime 的成本遠大於收益。如果你是**從零寫一個 thread-per-core 系統**，才值得直接選 glommio/monoio。Python 的話，要有心理準備：**標準庫（asyncio）不會幫你用 io_uring**，第三方綁定也遠不如 C/Rust 成熟，現階段用 io_uring 寫 Python 服務是小眾實驗，不是主流選項。

## 對比與取捨表：三種整合範式

| 範式 | 代表 | 怎麼整合 | 代價 | 適合誰 |
|---|---|---|---|---|
| **可切換後端** | PostgreSQL 18、Ceph | io_uring 當選項，保留 worker/libaio 退路 | 要維護多後端 | 關鍵基礎設施、要保守 |
| **reactor backend** | Seastar/ScyllaDB | 在既有 thread-per-core 框架換 I/O 後端 | 框架得先是 thread-per-core | 已是 shared-nothing 架構 |
| **原生 runtime** | glommio、tokio-uring | 整個 async runtime 照 completion 模型重寫 | 換掉 runtime、生態小 | 從零設計、追極致 |
| **謹慎/拒絕** | Go、libuv/Node | 只用一小塊 fs，或完全不用 | — | 重可攜性/安全的通用 runtime |

## 踩雷集錦

1. **以為「大專案都在用 io_uring」等於「我也該全面上」。** 真相更細緻：用的人大多是**可切換後端**（PostgreSQL、Ceph）或**thread-per-core 新系統**（Seastar）。通用 runtime（Go、Node、Python）普遍觀望。看採用模式，別看採用名單。
2. **直接把 epoll reactor 的 `epoll_wait` 換成 `io_uring_enter` 就想收割效能。** completion 模型改了 buffer 所有權與錯誤時機，半吊子移植通常更慢更錯。要嘛照 completion 重新設計，要嘛別移植。
3. **忽略 io_uring 的可攜性成本。** libuv 的軌跡（Android/32-bit ARM/舊 kernel 一路停用）說明：io_uring 的跨環境行為不一致，通用函式庫得準備大量 fallback 與開關。你的服務若要跑在多樣環境，這是真實維護負擔。
4. **以為開了 io_uring 後端就一定比舊路徑快。** PostgreSQL 的實測顯示 io_uring 後端在部分 workload 和 PG 17 的 streaming I/O 相近。後端換了，不代表你的 workload 吃得到——回到 [Ch 41](./41-performance-engineering.md)：要量。
5. **把 SPDK 當成 io_uring 的一種。** SPDK 是 kernel bypass、user-space polling，和 io_uring 是**對手**不是親戚。混淆這兩者會在選型時做出錯誤假設（見 Ch 43）。

## 進階：為什麼 thread-per-core 和 io_uring 互相成就

把這一章收束成一個洞見：io_uring 的 `SINGLE_ISSUER`/`DEFER_TASKRUN`（[Ch 27](./27-task-work-defer.md)）之所以存在，正是因為有 thread-per-core 這種「一個 thread 獨佔一個 ring、絕不共享」的使用者。反過來，thread-per-core 架構之所以能把效能推到極致，又正是因為 io_uring 提供了「單一 task 零鎖提交、完成延後批次處理」的原語。**兩者是共同演化的**：Seastar、glommio、ScyllaDB 的需求推著 io_uring 加這些 flag，這些 flag 又讓更多系統敢採 thread-per-core。理解這個共生關係，你就懂了 io_uring 近年演進的大方向——而這正好帶出下一章的對比，和最後一章的未來。

## 本章重點整理

- io_uring 是 **completion 模型**，和 epoll 的 **readiness 模型**根本不同；這個差異改變 buffer 所有權與錯誤時機，讓「既有 epoll reactor」移植困難，「從零設計的 thread-per-core 系統」最先受益。
- **PostgreSQL 18**（2025-09）把 io_uring 做成可切換的 `io_method`（worker 預設 / io_uring / sync），只覆蓋讀路徑，是「保守採用、保留退路」的範式。
- **thread-per-core 陣營**（Seastar/ScyllaDB、glommio、tokio-uring、monoio）與 io_uring 共生演化；Rust runtime 普遍「有人用、維護能量不足」。
- **Go 仍用 epoll、libuv/Node 謹慎且出過安全事故（CVE-2024-22017）**——通用、重可攜性/安全的 runtime 對 io_uring 普遍觀望，理由是安全攻擊面、可攜性、架構衝擊。
- 整合範式有四種：可切換後端、reactor backend、原生 runtime、謹慎/拒絕；選型要看自己屬於哪一類。

## 自我檢核

- [ ] 我能解釋 readiness 模型與 completion 模型的差異，以及為什麼它讓既有 reactor 難移植
- [ ] 我能說出 PostgreSQL 18 的 `io_method` 三個選項各是什麼、預設哪個
- [ ] 我能說出為什麼 thread-per-core 架構和 io_uring 天生契合
- [ ] 我能講出 Go 不用 io_uring、libuv 把它預設關掉的至少兩個理由
- [ ] 我知道 SPDK 不是 io_uring 的採用者而是競爭對手

## 延伸閱讀

### 官方文件 / release notes

- **[PostgreSQL 18 release notes — Asynchronous I/O](https://www.postgresql.org/docs/current/release-18.html)** 與 [feature matrix 的 AIO 條目](https://www.postgresql.org/about/featurematrix/detail/asynchronous-io-aio)
  - **讀哪裡**：`io_method` 設定與它覆蓋哪些操作的段落
  - **為什麼值得讀**：第一手確認「哪些路徑用了 io_uring、哪些還沒」，別信二手的倍數宣稱

### 專案部落格

- **[Introducing Glommio](https://www.datadoghq.com/blog/engineering/introducing-glommio)** — DataDog
  - **這篇說什麼**：為什麼要在 io_uring 上做 thread-per-core runtime，以及它解決什麼 tokio 解不了的問題
  - **讀哪裡**：thread-per-core 動機那段，和本章的共生演化洞見對讀

- **[Asymmetric io_uring backend for Seastar](https://www.scylladb.com/2026/07/22/asymmetric-io_uring-backend-seastar/)** — ScyllaDB
  - **這篇說什麼**：把 I/O 處理挪到專用核的新 backend，以及它**不**適合哪種 workload
  - **前提**：讀過 Ch 41 的「CPU 空轉換延遲」會更有感

### 社群討論（看「為什麼不用」）

- **[Go issue #31908 — runtime: use io_uring](https://github.com/golang/go/issues/31908)**
  - **讀哪裡**：維護者與社群對安全、可攜性、排程整合的權衡討論
  - **注意**：這是理解「通用 runtime 為何觀望」的最佳一手材料；結論至今未定

io_uring 在生態裡的位置，一半靠「誰用了」定義，另一半靠「和誰競爭」定義。下一章我們把它和 epoll、libaio、SPDK 擺在同一張桌子上系統性對比——包括誠實回答「什麼時候 io_uring 不是答案」。

→ [Ch 43 橫向對比：io_uring vs epoll vs libaio vs SPDK](./43-comparison.md)
