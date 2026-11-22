# Ch 41 — 效能工程：polling、NAPI busy poll、benchmark

> **目標**：把 io_uring 的「調優旋鈕」一次攤開，並教你怎麼**誠實地量**它們。你會搞懂 SQPOLL 省的是什麼、`IORING_SETUP_IOPOLL` 為什麼非 `O_DIRECT` 不可、網路側的 NAPI busy poll 怎麼註冊、batching 與 registered files/buffers 到底值多少，以及 `DEFER_TASKRUN`/`SINGLE_ISSUER` 對效能格局的影響。最後我們在 WSL 跑一支真的 benchmark，用真實數字看清「io_uring 不是開了就快」。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4（WSL2 實測 `6.18.58.1-microsoft-standard-WSL2`），用 `iou-cc` 編譯。本章的吞吐數字全部是這台機器真的跑出來的；哪些機制在 WSL 跑不動（IOPOLL、NAPI）會明確標注並給出原因。

## 為什麼效能工程要單獨一章

前面 40 章我們一直在教「怎麼把事情做對」——ring 怎麼運作、op 怎麼用、kernel 內部怎麼派發、漏洞怎麼來。但 io_uring 存在的理由是**快**。如果你只是把 `read()` 換成 `io_uring_prep_read()`，很可能發現**沒有變快，甚至變慢**。io_uring 的效能來自一整套互相配合的機制，每個都有前提、有適用場景、有反效果的情況。

這一章的核心訊息只有一句：**io_uring 的效能是「設計出來的」，不是「開啟出來的」。** 我們會把每個旋鈕的物理意義講清楚，然後用實測數字釘死「它值多少、什麼時候值」。

> 全章最重要的職業習慣：**任何調優都要有 before/after 的數字，而且數字要能重現。** 沒量就是沒有。本章後半的 benchmark 方法學，比任何單一旋鈕都重要。

## 先建立直覺：io_uring 的三種「省法」

io_uring 快，快在三個不同的層次，別混為一談：

1. **省 syscall 次數**（batching、SQPOLL）：一次 `io_uring_enter` 提交一把請求，甚至 SQPOLL 下連 enter 都不用。省的是**模式切換**（user↔kernel）的固定成本。
2. **省 per-I/O 的隱藏成本**（registered files/buffers）：每次 I/O 本來要查 fd table、pin user page，註冊後這些只做一次。省的是**每個請求的固定前置工**。
3. **省完成通知的成本**（`IOPOLL` busy poll、NAPI busy poll、`DEFER_TASKRUN`）：不靠中斷/IPI 喚醒，而是主動輪詢完成；或把完成收尾集中到一個點做。省的是**中斷延遲與排程抖動**。

這三層正交。一個真正調好的 io_uring 程式，三層都會動到。下面逐層拆。

## 成本模型：一次 I/O 的時間到底花在哪

在談旋鈕之前，先建立一個粗略的成本模型，這樣你才知道每個旋鈕打的是哪一塊。一次「讀一個 4 KB 區塊」的時間，大致由這幾段組成：

```
 [user→kernel 模式切換] + [fd 查找/引用] + [buffer pin] + [實際 I/O 等待] + [完成通知] + [kernel→user]
   ~數百 ns（syscall）     ~數十 ns        ~數十 ns        0（cache命中）~數十µs（NVMe）  ~數百 ns～µs（中斷）  ~數百 ns
```

每個旋鈕打的是其中一段：

- **batching** 把左右兩端的「模式切換」成本從「每個 I/O 一次」攤成「每批一次」——N 個請求共用一次 enter。
- **SQPOLL** 把提交端的模式切換直接歸零（kernel thread 自己讀 SQ）。
- **registered files** 打「fd 查找/引用」，**fixed buffers** 打「buffer pin」——這兩段本來每個 I/O 都要做，註冊後只做一次。
- **IOPOLL / NAPI busy poll** 打「完成通知」那段——不等中斷，忙碌輪詢，把那幾百 ns ~ µs 的中斷延遲換成 CPU 空轉。

關鍵觀察：當「實際 I/O 等待」很大（真實磁碟/網路）時，其他所有軟體開銷相對變小，io_uring 的價值主要在**讓多個 I/O 等待重疊**（並行）；當「實際 I/O 等待」趨近 0（cache 命中、極快 NVMe）時，軟體開銷占比反而放大，這時 registered/IOPOLL 這些「省固定開銷」的旋鈕才顯出來。**你要先知道自己的瓶頸在哪一段，才知道該轉哪個旋鈕。** 這也是為什麼本章後面每個機制都要實測——沒量，你連瓶頸在哪都不知道。

## 第一層：batching 與 SQPOLL——省 syscall

batching 我們在 [Ch 9](./09-submission-models.md) 講過機制：填一把 SQE，只 `io_uring_submit` 一次。SQPOLL 則讓 kernel 開一個 thread 輪詢 SQ tail，提交端達到**零 syscall**。

這裡只補效能面的關鍵取捨：

- **SQPOLL 不是免費的。** 那個 kernel thread 會**吃掉一整顆 CPU**（忙碌輪詢），閒置一段時間（`sq_thread_idle`）後才睡。你用一顆 CPU 的持續佔用，換提交路徑的零 syscall。這在「有一顆核可以專門燒、且提交極密集」的場景（高頻交易、專用 I/O 執行緒）划算；在一般伺服器上，白送一顆核去空轉通常不划算。
- **batching 幾乎永遠划算**，因為它不花額外資源，只是把 N 次 enter 併成 1 次。唯一的代價是延遲：你攢一批才送，第一個請求的提交被推遲了。低延遲場景要在「批量大小」和「不要攢太久」之間拿捏。
- **SQPOLL 還有一層隱藏成本：喚醒延遲。** SQPOLL thread 閒置超過 `sq_thread_idle`（毫秒級）會進入睡眠，之後你有新請求時第一次要用 `IORING_ENTER_SQ_WAKEUP` 把它叫醒——這次 enter 又變成一個 syscall。也就是說 SQPOLL 只有在**持續密集提交**（thread 從不睡）時才真的零 syscall；提交時斷時續，你反而在「空轉燒 CPU」和「喚醒 syscall」之間兩頭不討好。配合 `IORING_SETUP_SQ_AFF` 把 SQPOLL thread 綁到專用核，是認真用 SQPOLL 的起手式。
- **`IORING_SETUP_COOP_TASKRUN`（5.19+）是 batching 的近親，但省的是完成端。** 預設每個完成都可能用 IPI 打斷你的 task 去跑 task_work；`COOP_TASKRUN` 讓這些收尾「等你下次主動進 kernel 時順便做」，省掉 IPI。它比 `DEFER_TASKRUN` 寬鬆（不要求單一提交者），是一個幾乎無腦開、對多數事件迴圈都正收益的 flag。機制細節見 [Ch 27](./27-task-work-defer.md)。

## 第二層：registered files/buffers——省 per-I/O 前置

[Ch 15](./15-registered-files-buffers.md) 講過機制：`io_uring_register_files` / `register_buffers` 先把 fd 和 buffer 註冊進 ctx，之後用索引引用（`IOSQE_FIXED_FILE`、`prep_read_fixed`）。省的是：

- **fixed file**：免去每次 `fget`/`fput`（查 fd table + 原子引用計數）。
- **fixed buffer**：免去每次把 user page pin 住（`get_user_pages`）再放掉——註冊時一次 pin 好。

這一層「值多少」最容易被高估或低估，所以我們直接量。

## 實測一：batching 讓 io_uring 贏過同步 read——但要夠深才贏

我們寫一支 benchmark：對一個 256 MB 的檔案做 20 萬次隨機 4 KB 讀（先暖機拉進 page cache，量的是**提交/完成路徑本身**的開銷，不是磁碟延遲），兩種方法走**完全相同的 offset 序列**：

- **A**：同步 `pread()` 迴圈，每次一個 syscall。
- **B**：io_uring，佇列深度 `qd`，批次提交、批次收完成。

核心迴圈（完整碼在 `~/io_uring_course/src/ch41_bench.c`）：

```c
while (completed < nreads) {
    while (submitted - completed < qd && next < nreads) {   // 填滿到 qd 個 in-flight
        struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
        if (!sqe) break;
        int slot = next % qd;
        io_uring_prep_read(sqe, fd, bufs + (size_t)slot * BS, BS, offs[next]);
        next++; submitted++;
    }
    io_uring_submit(&ring);
    struct io_uring_cqe *cqe;
    io_uring_wait_cqe(&ring, &cqe);
    unsigned head; int cnt = 0;
    io_uring_for_each_cqe(&ring, head, cqe) { completed++; cnt++; }   // 收一整批
    io_uring_cq_advance(&ring, cnt);
}
```

掃一遍佇列深度，WSL 實測輸出：

```
file=/tmp/bench.dat size=256MB bs=4096 nreads=200000
qd=1   A sync 897420 IOPS   B io_uring 651751 IOPS   speedup 0.73x
qd=4   A sync 926490 IOPS   B io_uring 1000378 IOPS  speedup 1.08x
qd=16  A sync 861109 IOPS   B io_uring 1101715 IOPS  speedup 1.28x
qd=64  A sync 882384 IOPS   B io_uring 1137397 IOPS  speedup 1.29x
qd=128 A sync 901934 IOPS   B io_uring 1181413 IOPS  speedup 1.31x
qd=256 A sync 911156 IOPS   B io_uring 1152293 IOPS  speedup 1.26x
```

這張表是本章最值得盯著看的東西，三個結論：

1. **`qd=1` 時 io_uring 比同步 read 慢（0.73x）。** 一次只送一個請求、送完就等，io_uring 的提交/完成記帳（寫 SQE、推 tail、收 CQE、推 head）比一個 `pread` syscall 還重。**「把 read 換成 io_uring 就會變快」是錯的**——沒有並行、沒有批次，io_uring 只會更慢。這是新手最大的幻滅點，我們寧可一開始就用數字戳破它。
2. **並行度一上來就反轉。** `qd=4` 打平，`qd≥16` 穩定贏 25–30%。io_uring 的價值在**同時有很多 in-flight I/O**時才兌現：一次 enter 提交 128 個請求、收 128 個完成，把 syscall 的固定成本攤到 128 個 I/O 上。
3. **收益會飽和。** 從 `qd=64` 到 `256` 幾乎不再成長（甚至 `qd=256` 略降），因為 syscall 成本早就攤到可忽略，瓶頸轉移到 page cache 拷貝本身。盲目加大佇列只是浪費記憶體。

> 這是 cached read，量的是**軟體路徑開銷**。真正有磁碟/網路延遲時，io_uring 的優勢會更大——因為同步 read 在等 I/O 時整個 thread 卡住，io_uring 卻能讓那段延遲**和別的請求重疊**。我們這裡刻意選最不利於 io_uring 的場景（無真實延遲），它依然在夠深時贏，正說明 batching 本身的價值。

## 實測二：registered files + fixed buffers 再擠出約一成

同一個 workload，這次都用 io_uring，比較**普通 `read` vs `read_fixed`（註冊 buffer + 註冊 fd）**。30 萬次讀，WSL 實測：

```
qd=16  plain read 1071935 IOPS   fixed read_fixed 1191750 IOPS   fixed/plain 1.11x
qd=64  plain read 1158662 IOPS   fixed read_fixed 1265052 IOPS   fixed/plain 1.09x
```

registered 版本多約 **9–11%**。對一個 cached read 來說這不小——省掉的就是每次 `fget/fput` 與 page pin 的純 overhead。在真實高 IOPS 儲存服務（每秒上百萬 I/O）上，這一成直接換算成少一成 CPU。**這就是為什麼 ScyllaDB、SPDK 這類系統一定用 fixed buffer**——它們的 buffer 本來就是預先配好、長期復用的，註冊的前提天生成立。

## 第三層：IOPOLL 與 NAPI busy poll——省完成通知

### storage 側：`IORING_SETUP_IOPOLL`

一般的 block I/O 完成靠**中斷**：裝置做完 DMA，發中斷，CPU 進中斷處理，喚醒等待的請求。中斷有延遲、有排程抖動。對 NVMe 這種微秒級裝置，中斷的固定成本相對 I/O 本身已經不小。

`IORING_SETUP_IOPOLL` 改成**忙碌輪詢**：不睡、不等中斷，CPU 主動問裝置「好了沒」。省掉中斷延遲，代價是那段時間 CPU 全速空轉。它有兩個硬前提：

1. **檔案必須 `O_DIRECT` 開啟**——繞過 page cache。IOPOLL 輪詢的是**裝置佇列**，page cache 命中的讀根本沒有裝置 I/O 可輪詢。
2. **底層 block device 要支援 poll queue**（NVMe 的 poll queue）。普通 SATA、虛擬磁碟沒有。

我們在 WSL 上測它能不能跑：

```c
struct io_uring_params p = { .flags = IORING_SETUP_IOPOLL };
io_uring_queue_init_params(8, &ring, &p);          // setup
int fd = open("/tmp/bench.dat", O_RDONLY | O_DIRECT);
io_uring_prep_read(sqe, fd, buf /*4K 對齊*/, 4096, 0);
```

WSL 實測輸出：

```
IOPOLL setup ok
IOPOLL read res=-95 (Operation not supported)
```

`setup` 成功（kernel 認得這個 flag），但實際讀回 **`-95`（`-EOPNOTSUPP`）**：WSL 的虛擬 block device 沒有 poll queue。這是本章最誠實的一課——**IOPOLL 無法在 WSL 示範真實加速**。要看它的效果，需要一台有 NVMe poll queue 的實體機或雲主機。`O_DIRECT` 本身在 WSL 的 ext4 上可以開（我們另外驗證過 `O_DIRECT` 讀 4096 位元組回傳正常），卡住的是 poll queue 這個硬體前提。

> 這也示範了 io_uring 的錯誤哲學（[Ch 8](./08-cqe-anatomy.md)）：IOPOLL 不支援不是 `setup` 當場失敗，而是**請求完成時從 CQE 的 `res` 以 `-EOPNOTSUPP` 回來**。你要檢查的是 `cqe->res`，不是 syscall 回傳值。

### 網路側：NAPI busy poll

網路收包預設也靠中斷 + softirq（NAPI）。io_uring 從 6.9 起支援**註冊 NAPI busy poll**（`io_uring_register_napi` / `IORING_REGISTER_NAPI`）：等完成時，順便忙碌輪詢 NIC 的 NAPI，把收包延遲從「等中斷」壓到「主動拉」。這對延遲敏感的網路服務（交易、RPC）有感，代價同樣是 CPU 空轉。

這需要支援 busy poll 的實體 NIC 與對應設定，**WSL 的虛擬網路介面上無法有意義地示範**，本章標注為**未在 WSL 實測**。概念上它和 IOPOLL 對稱：都是「用 CPU 空轉換掉中斷延遲」，一個對 storage 佇列，一個對 network 佇列。要實測請在實體機上，對照 `liburing` 的 `test/napi-test.c`。

## 第四層補充：`DEFER_TASKRUN` / `SINGLE_ISSUER` 的效能影響

[Ch 27](./27-task-work-defer.md) 講過這兩個 flag 的機制，這裡只收口效能結論：

- **`IORING_SETUP_DEFER_TASKRUN`**(需配 `SINGLE_ISSUER`，6.1+)：把完成收尾(task_work)延到你**下次 `io_uring_enter(GETEVENTS)`** 時才一次做，而不是每個完成都用 `TWA_SIGNAL` 打斷你。對「一個 thread 專心 submit/reap」的事件迴圈，這省掉大量 IPI 與排程打斷，是**目前單執行緒高吞吐場景的建議預設**。tokio-uring、高效能 proxy 幾乎都開它。
- **`IORING_SETUP_SINGLE_ISSUER`**(6.0+)：向 kernel 保證「只有一個 task 會提交」，讓 kernel 省掉一些同步。它是 `DEFER_TASKRUN` 的前提，本身也去掉若干鎖開銷。代價：你得真的遵守「單一提交者」，違反會拿到 `-EEXIST`。

這兩個不是微調，是**架構選擇**：你的 io_uring 是不是「一個 thread 獨佔一個 ring」(thread-per-core 模型)？是的話開它們，收益明顯；不是的話(多 thread 共享一個 ring)根本不能開。這也預告了 [Ch 42](./42-ecosystem.md) 的生態主題——幾乎所有高效能 io_uring 框架都採 thread-per-core，正是為了能開這些 flag。

## 對比與取捨表

| 旋鈕 | 省什麼 | 代價 | 什麼時候值 | 本章是否實測 |
|---|---|---|---|---|
| batching | syscall 次數 | 首個請求延遲 | 幾乎永遠（有並行時） | 是，qd 掃描 |
| SQPOLL | 提交端 syscall 歸零 | 燒一整顆 CPU | 有專用核 + 提交極密集 | 機制見 Ch 9 |
| registered files | 每次 `fget/fput` | 需先註冊、fd 固定 | fd 長期復用 | 是，~1.1x |
| fixed buffers | 每次 page pin | 需先註冊、buffer 固定 | buffer 預配復用 | 是（併入上行） |
| IOPOLL | 中斷延遲（storage） | CPU 空轉、需 `O_DIRECT`+poll queue | NVMe 低延遲儲存 | 註：WSL `-EOPNOTSUPP` |
| NAPI busy poll | 中斷延遲（network） | CPU 空轉、需支援的 NIC | 低延遲網路 | 註：未在 WSL 實測 |
| DEFER_TASKRUN | IPI / 排程打斷 | 強制單一提交者 | thread-per-core 事件迴圈 | 機制見 Ch 27 |

## 踩雷集錦

1. **拿 `qd=1` 的 microbenchmark 說「io_uring 沒比較快」。** 我們的實測 `qd=1` io_uring 確實慢（0.73x）——因為它根本沒給 io_uring 發揮的條件。量 io_uring 一定要量**有並行深度**的情況，否則你量的是「io_uring 最不擅長的用法」。
2. **只量 microbenchmark 就下結論。** cached read 的 benchmark 量的是軟體路徑開銷，和真實磁碟/網路延遲下的表現可能天差地遠。微基準用來看「單一機制值多少」，**不能**拿來預測整個系統的吞吐。真實結論要用真實 workload（或 fio 跑真實 I/O pattern）。
3. **沒暖機、沒綁核、只跑一次就信。** 第一次跑有 page fault、cache 冷、CPU 還沒升頻；不綁核的話排程器把你的 thread 搬來搬去，數字抖動極大。本章的程式都先暖機、取第二次；正式量測還應 `taskset` 綁核、關掉 CPU 頻率調節、多跑取中位數。我們的 speedup 在重跑間本來就有 ±0.05x 的抖動(例如 `qd` 掃描裡 `0.73→1.08→1.28`)，這是正常的量測雜訊，報告時要誠實標出變異。
4. **以為開了 SQPOLL 就一定快。** SQPOLL 燒一顆 CPU。如果你的核本來就不夠、或提交不夠密集，那顆空轉的核拿去做別的事反而更好。SQPOLL 是「拿 CPU 換延遲」，不是「免費加速」。
5. **IOPOLL 忘了 `O_DIRECT` 或在不支援的裝置上用。** 沒 `O_DIRECT`，IOPOLL 無事可輪詢；裝置沒 poll queue（虛擬磁碟、SATA），直接 `-EOPNOTSUPP`——而且是從 CQE 回來，不是 setup 當場報錯，很容易看漏。

## 進階：怎麼量才算數

本章的程式是「夠用的微基準」，但要拿去說服人，方法學要再上一級：

- **用 fio 的 io_uring engine 當對照。** fio(`ioengine=io_uring`，或 `io_uring_cmd` 走 NVMe passthrough)是業界標準，能設 `iodepth`、`direct=1`、`registerfiles=1`、`fixedbufs=1`、`sqthread_poll=1` 等旋鈕，直接對應本章每個機制。本課 WSL 環境預設沒裝 fio(`apt install fio` 需 sudo)；在實體機上 `fio --name=t --ioengine=io_uring --rw=randread --bs=4k --iodepth=128 --direct=1 --filename=/dev/nvme0n1` 是驗證 IOPOLL/registered 效益的標準起手式。**本課未在 WSL 實測 fio。**
- **看延遲分布，不只看平均。** 吞吐(IOPS)之外，p50/p99/p999 延遲才看得出抖動。io_uring 的一大價值是**尾延遲**穩定(少中斷打斷)，這在平均吞吐上看不出來。
- **量 CPU 成本，不只量吞吐。** SQPOLL、IOPOLL、busy poll 都是「用 CPU 換延遲」。報「快了 20%」而不報「多燒了一顆核」是不誠實的。正確的指標是**每 I/O 的 CPU cycles**，不是裸 IOPOLL。

## 本章重點整理

- io_uring 的效能分三層：省 syscall(batching/SQPOLL)、省 per-I/O 前置(registered files/buffers)、省完成通知(IOPOLL/NAPI/DEFER_TASKRUN)，三者正交，調好的程式三層都動。
- 實測：cached 隨機讀下，io_uring **`qd=1` 比同步 read 慢(0.73x)**，`qd≥16` 穩定贏約 1.3x;registered files+fixed buffers 再多約 1.1x。**io_uring 的快，來自並行深度 + 正確設定，不是換個 API 就有。**
- IOPOLL 需 `O_DIRECT` + 裝置 poll queue，WSL 上 `-EOPNOTSUPP`；NAPI busy poll 需支援的 NIC，未在 WSL 實測。這兩個都是「CPU 空轉換中斷延遲」。
- `DEFER_TASKRUN`/`SINGLE_ISSUER` 是架構選擇(thread-per-core)，不是微調。
- benchmark 的鐵律：有並行深度、暖機、綁核、多跑取中位數、報變異、量 CPU 成本而非只量吞吐，且微基準不能外推到真實 workload。

## 自我檢核

- [ ] 我能解釋為什麼 `qd=1` 時 io_uring 可能比同步 read 慢
- [ ] 我能說出 registered files 和 fixed buffers 各省掉 kernel 裡的哪個動作
- [ ] 我知道 IOPOLL 的兩個硬前提，以及它為什麼在 WSL 回 `-EOPNOTSUPP`
- [ ] 我能說出 SQPOLL 的代價是什麼，什麼場景才划算
- [ ] 我能列出一個「可信的 io_uring benchmark」至少要滿足的四個條件

## 延伸閱讀

### 官方文件 / 原始碼

- **[fio 的 io_uring / io_uring_cmd engine 文件](https://fio.readthedocs.io/en/latest/fio_doc.html)** — Jens Axboe
  - **讀哪裡**：`ioengine=io_uring` 與 `io_uring_cmd` 兩節，以及 `sqthread_poll`、`fixedbufs`、`registerfiles`、`hipri`(IOPOLL) 這些參數
  - **為什麼值得讀**:fio 作者就是 io_uring 作者，這些參數名一一對應本章的機制，是把本章旋鈕拿去真機驗證的標準工具

### 技術文章

- **[io_uring and networking in 2023](https://github.com/axboe/liburing/wiki/io_uring-and-networking-in-2023)** — liburing wiki
  - **這篇說什麼**：網路側效能機制(multishot、provided buffer、zero-copy send、NAPI busy poll)的權威整理
  - **讀哪裡**:busy poll 與 `DEFER_TASKRUN` 段，補齊本章網路側未能實測的部分

- **[Lord of the io_uring — Benchmarking](https://unixism.net/loti/)** — Shuveb Hussain
  - **這篇說什麼**：用具體程式比較 io_uring 與傳統 I/O 的吞吐
  - **讀哪裡**：效能對比章節，和本章的 benchmark 互相印證；注意它和本章一樣強調「深度」的重要

### 原始碼對照

- **Linux 6.18 `io_uring/rw.c` 的 IOPOLL 路徑** 與 `io_uring/napi.c`
  - **讀哪裡**：`io_do_iopoll`(忙碌輪詢完成)、`napi.c` 的 `io_napi_busy_loop`；對照本章 IOPOLL/NAPI 的機制描述
  - **注意**：這是「為什麼需要 `O_DIRECT`」與「busy poll 到底在輪什麼」的最終答案

效能這一章把「io_uring 快在哪、怎麼量」講完了。但 io_uring 的影響力不只在 benchmark——它正在被一整批資料庫、runtime、儲存系統採用，每一個都用自己的方式重新組織架構來榨出這些效能。下一章我們看生態系：誰在用 io_uring、怎麼用、以及誰刻意不用、為什麼。

→ [Ch 42 生態系：tokio-uring、PostgreSQL/Ceph、語言綁定](./42-ecosystem.md)
