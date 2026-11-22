# Ch 9 — 提交模型：SQPOLL、batching、GETEVENTS

> **目標**：把「怎麼提交」這件事講滿。前面我們一直是「填一個、submit、等一個」，本章看三種把 syscall 壓低的手段：**批次提交**（填一把只 submit 一次）、**`submit_and_wait`**（提交與等待併成一次 enter）、以及最關鍵的 **SQPOLL**（kernel 開一個 thread 輪詢 SQ，達到提交端**零 syscall**）。順帶把 `io_uring_enter` 的 `min_complete`/`GETEVENTS` 語意、以及 `COOP_TASKRUN`/`DEFER_TASKRUN` 對節奏的影響講清楚。

> **環境**：kernel 6.18、liburing 2.16、gcc 11.4，用 `iou-cc` 編譯。SQPOLL 範例在 WSL2 以**非特權**使用者實測（5.11 起 SQPOLL 不再強制 root）。

## 為什麼提交模型值得單獨一章

Ch 1 講過 io_uring 的一大賣點是**把 syscall 省掉**——Spectre/Meltdown 緩解（KPTI）之後，每次進出 kernel 都變貴，而高頻 I/O 服務一秒可能要提交數十萬個請求。如果每個請求一次 `io_uring_enter`，syscall 開銷就把共享環省下的好處吃回去了。

所以 io_uring 提供一整組「怎麼提交」的策略，讓你按場景選：低頻就老實 submit、中頻批次攤銷、極高頻直接讓 kernel thread 輪詢連 syscall 都免。這章就是這組策略的地圖。理解它們不只為了效能——Part 3 讀 kernel 提交路徑、Part 4 分析某些 race（SQPOLL thread 和主 thread 的競態）時，這些模型是前提。

## 先建立直覺：三個層次的「省」

```
每請求一次 enter   ──批次──▶   一批一次 enter   ──SQPOLL──▶   零 enter（kernel 輪詢）
     低頻                        中高頻                          極高頻／低延遲
  submit×N                  submit×1 / N 請求              submit 多半不下 syscall
```

三個層次逐步減少「進 kernel」的次數：

1. **批次**：填 N 個 SQE，只呼叫一次 `io_uring_submit`——一次 enter 提交 N 個請求，把 syscall 成本攤到 N 個請求上。
2. **submit_and_wait**：連「提交」和「等完成」都併進同一次 enter，再省一次。
3. **SQPOLL**：kernel 側有個 thread 一直盯著 SQ 的 tail，你推了 tail 它自己看到，**連那一次 enter 都不用下**。

下面逐個拆，最後用 SQPOLL 實測收尾。

一個貫穿全章的提醒：這些模型**不是互斥的選項，而是可以疊加的層次**。你幾乎總是會用批次（填一把再 submit），多半會用 `submit_and_wait`（提交等待合一），在這之上**才**考慮要不要再上 SQPOLL。它們解決的是同一個問題的不同量級——批次把 N 攤成 1，SQPOLL 把 1 壓成 0。先把便宜的（批次）吃乾淨，再評估貴的（SQPOLL）值不值得。

## 批次提交：填一把，submit 一次

這是最基本、也最常被忽略的優化。`io_uring_submit` 提交的是**自上次 submit 以來你填的所有 SQE**，不是一個。所以把能一起送的請求填成一批：

```c
// 一次提交 N 個 read，只一次 io_uring_submit
for (int i = 0; i < N; i++) {
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    if (!sqe) {                       // ← SQ 填滿了
        io_uring_submit(&ring);       //    先把積著的送出，騰空間
        sqe = io_uring_get_sqe(&ring);//    再拿
    }
    io_uring_prep_read(sqe, fds[i], bufs[i], len, 0);
    io_uring_sqe_set_data64(sqe, i);
}
int n = io_uring_submit(&ring);       // 一次 enter 提交整批
```

Ch 6 的 `ch06_batch` 已經跑過這個模式（3 個 read 一次 submit）。這裡補兩個批次才會遇到的細節：

**`io_uring_get_sqe` 回 `NULL` 必須處理。** SQ 深度有限（這裡假設 N 可能大於深度）。填到環滿，`get_sqe` 回 `NULL`，你得先 `submit` 把已填的送走、騰出槽，再繼續填。Ch 6 單請求範例沒滿才省略這段，批次時它是**必要**的——不檢查就是對 `NULL` 解參考 segfault（Ch 6 踩雷第 1 條）。

**提交順序 ≠ 填寫順序，也 ≠ 完成順序。** 批次裡的請求 kernel 可以並行處理、亂序完成（Ch 6 實測過 0、2、1）。要強制順序得用 linked ops（Ch 16）。批次只保證「一起送」，不保證「照順序做」。

**`io_uring_submit` 的回傳值要認真看。** 它回的是「這次實際提交了幾個 SQE」。正常情況等於你填的數，但在 SQ 滿、或 kernel 當場擋下某些 SQE（見下）時可能**少於**你填的——這時沒提交的那幾個還在環裡，你得再 submit 一次把它們送走，而不是假設「submit 回來就全送出去了」。忽略回傳值、在批次高壓下就會悄悄漏掉請求。

批次的效益很直接：原本 N 次 enter 變 1 次。在中高頻場景，光這一步就能把 syscall 開銷降一個數量級。

**批次裡有一個 SQE 不合法會怎樣？** 預設行為是：kernel 提交到遇到第一個**無法提交**的 SQE（例如欄位組合非法、當場就能判定錯誤的那種）就**停下**，`io_uring_submit` 回傳「成功提交了幾個」——後面的不提交。注意這裡講的是「提交階段就被擋下」的錯誤，不是「提交成功但執行失敗」（後者照常各回一個帶負 `res` 的 CQE，Ch 8）。若你希望 kernel **跳過**壞的、把後面好的也提交完，setup 時加 `IORING_SETUP_SUBMIT_ALL`——它讓提交不因單一 SQE 的錯而中斷整批。批次提交一大把時，這個 flag 讓一顆老鼠屎不會卡住整鍋。

## `submit_and_wait`：提交與等待併成一次 enter

Ch 6 已經用過 `io_uring_submit_and_wait(ring, wait_nr)`，這裡講清它在 syscall 層面省了什麼。對照兩種寫法：

```c
// 寫法 A：兩次 syscall
io_uring_submit(&ring);                        // enter #1：提交
io_uring_wait_cqe_nr(&ring, &cqe, 3);          // enter #2：等 3 個完成

// 寫法 B：一次 syscall
io_uring_submit_and_wait(&ring, 3);            // enter：提交 + 等 3 個，一次搞定
```

`io_uring_enter(fd, to_submit, min_complete, flags, ...)` 這個 syscall 本來就能**同時**做提交和等待——`to_submit` 說提交幾個、`min_complete` + `IORING_ENTER_GETEVENTS` 說等幾個。Ch 2 的 raw 版本就是這樣一次 enter 做兩件事（`io_uring_enter(ring_fd, 1, 1, IORING_ENTER_GETEVENTS)`）。`submit_and_wait` 就是把這個能力包好：一次 enter，`to_submit` = 你填的數、`min_complete` = `wait_nr`。

這是 event loop 的標準心跳：每輪「把這輪要送的送出去，順便等至少一個回來」用一次 `submit_and_wait` 完成，而不是 submit + wait 兩次進 kernel。這個「心跳」模式你會在 Part 2 的伺服器範例（練習 B 的 echo server）反覆看到——迴圈底部一次 `submit_and_wait`，收割、處理、再回到頂部填下一批，是幾乎所有 io_uring 服務的骨架。

### 算一筆帳：處理 1000 個請求各要幾次 enter

把三種模型放進同一個情境——一輪要處理 1000 個 read——數 `io_uring_enter` 次數，差距一眼就看出來：

| 做法 | enter 次數 | 說明 |
|---|---|---|
| 每請求 submit + 每請求 wait | 2000 | 1000 次提交 + 1000 次等待，最糟 |
| 每請求 submit_and_wait | 1000 | 提交等待併一次，但仍一請求一次 |
| 批次 submit_and_wait（深度 256） | ≈8 | 1000 / 256 ≈ 4 批，每批一次提交 + 收割攤銷 |
| SQPOLL（thread 醒著） | ≈0 | 提交完全不進 kernel，poll thread 自己取 |

從 2000 掉到個位數，就是這一章的全部重點。KPTI 之後一次 enter 的成本是幾百到上千個 cycle，2000 次和 8 次的差距，在高頻服務裡就是吞吐量的數量級差異。注意這筆帳的前提是**請求能成批到來**——若請求是零散、一個一個來的（低頻互動式），批次無從批起，這時 SQPOLL 的「不管來幾個都零 syscall」才是唯一能再往下壓的手段，但代價是燒一核。

### `min_complete` 與 `GETEVENTS` 的語意

`io_uring_enter` 的 `min_complete` 配 `IORING_ENTER_GETEVENTS` flag，語意是「**阻塞到至少 `min_complete` 個完成事件可取**才返回」：

- `min_complete = 0`（或沒設 `GETEVENTS`）：只提交，不等，立刻返回。這就是純 `io_uring_submit`。
- `min_complete = 1`：至少等一個完成。`io_uring_wait_cqe` 底層就是這個。
- `min_complete = N`：等齊 N 個再返回，減少「醒來 → 只收到一個 → 又睡」的來回。批次等待時用，攤銷喚醒成本。

> 要注意 `min_complete` 是「**至少**」不是「剛好」：返回時環裡可能有多於 N 個 CQE（其他請求也完成了）。所以返回後仍要用 `for_each_cqe` 把現成的全收光，別只收 N 個（Ch 6 的收割骨架）。

liburing 的 `io_uring_wait_cqe_timeout`、`io_uring_submit_and_wait_timeout` 還能在這基礎上加逾時，底層走 `IORING_ENTER_EXT_ARG`（擴充參數，6.18 用 `EXT_ARG`/`EXT_ARG_REG` 傳逾時結構），不必額外塞一個 timeout SQE。Ch 17 談 timeout 時會對比這兩條路。

> 為什麼「帶逾時的等待」要靠 `EXT_ARG` 而非塞一個 timeout SQE？因為後者會多佔一個 SQE 槽、多產一個 CQE，還要你收拾它；`EXT_ARG` 把逾時當 `io_uring_enter` 的參數直接傳，等待本身就帶時限，乾淨得多。這是 io_uring 不斷把「常見模式」收進 syscall 參數、減少樣板的又一例。

## SQPOLL：讓 kernel thread 替你輪詢 SQ

批次和 submit_and_wait 都還是「每批至少一次 enter」。SQPOLL 把最後這次 enter 也拿掉。

### 機制

`IORING_SETUP_SQPOLL`（setup 時設）讓 kernel 建立一個**專屬的 poll thread**，這個 thread 不停地看你的 SQ tail（共享記憶體，它直接讀）。你填好 SQE、store-release 推 tail（`io_uring_submit` 內部做），poll thread 下一輪輪詢就看到新 tail，自己把請求取走處理——**你不必下 `io_uring_enter`**。這就是「提交端零 syscall」：請求的提交完全發生在使用者態寫記憶體，kernel 那頭自己來拿。

代價也明確：這個 poll thread **燒一顆 CPU 輪詢**。所以 SQPOLL 是拿「一核的 CPU」換「提交零 syscall + 極低延遲」，只對**持續高頻**的場景划算。偶爾才提交一個請求卻養一個輪詢 thread，純浪費。

### `sq_thread_idle`：輪詢 thread 的省電閥

一直滿載輪詢太耗電，所以 poll thread 閒置 `sq_thread_idle` 毫秒（你在 `params.sq_thread_idle` 設）沒新請求後會**去睡**。睡著之後它就不再看 SQ 了——這時你再提交，它看不到，怎麼辦？

### `IORING_SQ_NEED_WAKEUP`：睡著了要喚醒

poll thread 睡前會在 SQ 環的 flags 設上 `IORING_SQ_NEED_WAKEUP`。`io_uring_submit` 內部**每次都檢查這個位元**：

- **沒設**（thread 醒著）：submit 只是推個 tail，**不下任何 syscall**，直接返回。這是常態熱路徑。
- **有設**（thread 睡了）：submit 下一次 `io_uring_enter(..., IORING_ENTER_SQ_WAKEUP)` 把 thread 叫醒，再讓它處理。

好消息是這整套判斷 liburing 的 `io_uring_submit` 幫你包好了——你用同一個 `io_uring_submit`，它自動在「不下 syscall」和「下喚醒 syscall」之間切換。你唯一要做的是**把 `sq_thread_idle` 調得夠長**：若你的提交間隔小於 idle，thread 一直醒著，就一直零 syscall；idle 設太短，thread 老去睡，每次提交都要喚醒，SQPOLL 的好處就沒了。

換句話說，`sq_thread_idle` 是在「反應延遲」和「省電」之間調：設大，thread 長醒、提交永遠免 syscall，但閒時也燒 CPU；設小，閒時省電，但一忙起來的前幾次提交要付喚醒成本。對穩定高負載，設大（甚至幾秒）讓它長醒；對突發負載，就得權衡。

### 實測 SQPOLL

建一個 SQPOLL ring，填 3 個 read、submit、收完成：

```c
// ch09_sqpoll.c（節錄）
struct io_uring_params p; memset(&p, 0, sizeof p);
p.flags = IORING_SETUP_SQPOLL;
p.sq_thread_idle = 2000;                    // 閒置 2000ms 才睡
io_uring_queue_init_params(8, &ring, &p);
printf("SQPOLL_NONFIXED=%s idle=%ums\n",
       (p.features & IORING_FEAT_SQPOLL_NONFIXED) ? "yes" : "no", p.sq_thread_idle);

int fd = open("/etc/hostname", O_RDONLY);
char buf[64] = {0};
for (int i = 0; i < 3; i++) {
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_read(sqe, fd, buf, sizeof(buf) - 1, 0);
    io_uring_sqe_set_data64(sqe, i);
}
int submitted = io_uring_submit(&ring);     // 熱路徑下這裡不下 syscall
/* ...wait_cqe 收 3 個... */
```

實測輸出（非特權使用者）：

```
SQPOLL ring up. SQPOLL_NONFIXED=yes idle=2000ms
submitted 3 SQEs (SQPOLL: usually no io_uring_enter)
  tag 0: res=16
  tag 1: res=16
  tag 2: res=16
```

三個 read 都成功。`SQPOLL_NONFIXED=yes` 表示這顆 kernel 允許 SQPOLL 搭配**非 registered** 的 fd（下一節細談）。

### 用 strace 看它到底下了幾次 enter

光看輸出看不出「省 syscall」，用 `strace` 數 `io_uring_enter`：

```bash
strace -f -e trace=io_uring_enter,io_uring_setup ./ch09_sqpoll
```

實測關鍵幾行：

```
io_uring_setup(8, {flags=IORING_SETUP_SQPOLL|0x10000, ..., sq_thread_idle=2000, ...}) = 3
io_uring_enter(3, 3, 0, IORING_ENTER_SQ_WAKEUP, NULL, 8) = 3
submitted 3 SQEs (SQPOLL: usually no io_uring_enter)
```

整個程式 `io_uring_enter` 只被呼叫 **2 次**（實測計數）。逐一解釋：

- setup 回來後 poll thread 剛建立、還在暖身或已進入待喚醒狀態，所以**第一次** `io_uring_submit` 偵測到 `IORING_SQ_NEED_WAKEUP`，下了一次 `io_uring_enter(..., IORING_ENTER_SQ_WAKEUP)`——注意參數是 **WAKEUP** 不是普通提交，它只是叫醒 thread。這是 SQPOLL「剛啟動或剛睡醒的第一下」難免的一次。
- 另一次 enter 來自第一個 `io_uring_wait_cqe`：那一刻 CQE 還沒好，所以它進 kernel 等；之後 CQE 陸續進環，後兩次 `wait_cqe` 直接在共享記憶體讀到，**不下 syscall**。
- **關鍵對比**：若不是 SQPOLL，3 個 read 至少要 submit 的那次 enter（含提交）＋ 等待的 enter。SQPOLL 下，提交那次變成「thread 醒著時完全免」——這個範例因為 thread 剛啟動才付了一次喚醒成本；在**持續**提交的真實服務裡，thread 保持醒著，submit 連喚醒都不用，提交端是真正的零 syscall。

> `strace` 裡 setup 的 flags 還多了個 `0x10000`，那是 `IORING_SETUP_NO_SQARRAY`（1<<16）——liburing 2.16 預設啟用，省掉 Ch 2 提過的 SQ indices 間接層。這和 SQPOLL 無關，是 liburing 的獨立優化，看到不用慌。

### 誰在用 SQPOLL

SQPOLL 不是實驗室玩具。儲存引擎與低延遲資料庫是它的主場：這類系統本來就會把若干 CPU 核**專職**給 I/O（shard-per-core 架構），拿一核養 poll thread 對它們不是負擔而是設計的一部分。對「每個核跑一個 reactor、永遠有請求在飛」的系統，SQPOLL 的「提交端零 syscall」正中下懷；反過來，一個偶爾才做 I/O 的 CLI 工具或低併發服務開 SQPOLL，就是白燒一核。判準只有一條：**你的 I/O 夠不夠密、密到值得獻祭一核常駐輪詢**。

### SQPOLL 的兩個實務要點

**registered files（Ch 15）**：早期 SQPOLL **強制**所有 fd 必須先 register（`IORING_REGISTER_FILES`），因為 poll thread 在另一個 kernel context 跑，拿不到你 process 的 fd 表。5.11 之後有了 `IORING_FEAT_SQPOLL_NONFIXED`（我們實測 `=yes`），放寬成普通 fd 也能用。但**效能上**仍建議 register——poll thread 每次用普通 fd 都要查 fd 表、加引用計數，registered 則省掉這些。SQPOLL + registered files 是高效能寫法的標配。

**CPU 綁定 `IORING_SETUP_SQ_AFF`**：poll thread 預設可能被排到任何核。設 `IORING_SETUP_SQ_AFF` 並在 `params.sq_thread_cpu` 指定一顆 CPU，把 poll thread 釘在上面。好處是：(1) 不和你的工作 thread 搶核、(2) cache locality 更穩。通常把 poll thread 綁到一顆專用核，工作 thread 綁到其他核，彼此不干擾。這是 SQPOLL 調校的常見第一步。

**多 ring 共用一個 poll thread（`IORING_SETUP_ATTACH_WQ`）**：每個 SQPOLL ring 預設養**一個**自己的 poll thread。若你的服務開了 8 個 ring（每核一個），預設就是 8 個 thread 各燒一核——太奢侈。`IORING_SETUP_ATTACH_WQ`（配 `params.wq_fd` 指向既有 ring）讓多個 ring **共用同一個 poll thread**，一個 thread 輪詢多條 SQ。這把 SQPOLL 的 CPU 成本從「每 ring 一核」壓到「全部共一核」，是多 ring 架構用 SQPOLL 的前提。代價是那一個 thread 要輪詢更多 SQ，單 ring 的提交延遲可能略升。

### 再省一點：registered ring fd

就算不用 SQPOLL，提交時每次 `io_uring_enter(ring_fd, ...)` 都要 kernel 把 `ring_fd` 這個 fd 查成內部的 ring 物件——這也是成本。`io_uring_register(IORING_REGISTER_RING_FDS)`（或 setup 時的 `IORING_SETUP_REGISTERED_FD_ONLY`）把 ring 自己註冊成一個**內部索引**，之後 enter 帶 `IORING_ENTER_REGISTERED_RING` flag、傳索引而非 fd，省掉 fd 查表。liburing 的 `io_uring_queue_init` 搭配 `io_uring_register_ring_fd(&ring)` 就能開啟，之後 `io_uring_submit` 自動走註冊版。這是在「批次都用上了、還想再擠」時的小優化，單獨看省不多，但高頻下每次 enter 省一點查表累積起來可觀。SQPOLL 不需要它（本來就不下 enter），兩者各管一段。

## COOP_TASKRUN / TASKRUN_FLAG / DEFER_TASKRUN：完成節奏的三個開關

前面都在講**提交**端省 syscall。還有一組 setup flag 影響**完成**端的節奏——io_uring 完成一個請求後，常需要在你的 task context 跑一小段「task work」（填 CQE、喚醒等待者）。這段 work 何時跑、怎麼跑，由這三個 flag 調，概覽如下（內部實作留給 Ch 27）：

| flag | 作用（概覽） | 代價／前提 |
|---|---|---|
| `IORING_SETUP_COOP_TASKRUN` | 完成的 task work **不**用 IPI 強制中斷你的 task，等你自然進 kernel（下次 syscall）時才跑 | 完成的「通知」稍延遲；省掉 IPI 與被打斷的開銷 |
| `IORING_SETUP_TASKRUN_FLAG` | 搭配上者：有 task work 待跑時，在 SQ flags 設 `IORING_SQ_TASKRUN`，讓你知道「該進 kernel 跑一下」 | 必須和 COOP_TASKRUN 一起 |
| `IORING_SETUP_DEFER_TASKRUN` | 完成的 task work **延到你主動 `GETEVENTS` 時**才跑，而非任何 syscall；完成處理集中、可預測 | 需配 `IORING_SETUP_SINGLE_ISSUER`（只有一個 task 提交） |

直覺上的取捨：

- **預設**（都不設）：請求在別的 context（io-wq、中斷）完成後，用 IPI 把你的 task 打斷去跑 task work——即時，但 IPI 和被打斷有成本。
- **COOP_TASKRUN**：別急著 IPI，等你反正要進 kernel 時順便跑。適合你本來就頻繁進出 kernel 的 workload，省下 IPI。
- **DEFER_TASKRUN**：更進一步，task work 只在你**要收完成**（`GETEVENTS`）時跑。完成處理全集中在你可控的時點，延遲抖動小、cache 更友善——這是目前高效能單 issuer 服務（如 tokio-uring、某些 proxy）的推薦組合，但要你保證只有一個 thread 提交（`SINGLE_ISSUER`）。

這三個對「你什麼時候、在哪個 context 看到完成」影響很大，是 Part 3（task_work、DEFER_TASKRUN 內部）和 Part 5（效能工程）的重點。本章先讓你知道它們存在、以及「提交節奏」之外還有個「完成節奏」可調。**實務建議**：單 thread 提交的新服務，優先試 `DEFER_TASKRUN | SINGLE_ISSUER`，它往往是延遲最穩的組合。

為什麼 `DEFER_TASKRUN` 延遲更穩？因為預設模式下，請求可能在**中斷或 io-wq worker** 的 context 隨機完成、隨機打斷你的主迴圈去跑 task work，完成處理的時點不可預測，造成延遲抖動。`DEFER_TASKRUN` 把所有 task work 收束到「你主動 `GETEVENTS`」那一刻統一跑——完成處理全部發生在你的收割點，可預測、cache 熱、沒有 IPI。代價就是你必須規律地進 kernel 收割（否則 task work 一直不跑、完成一直不浮現），而且只能有一個 issuer。對一個「迴圈底部固定 `submit_and_wait` 一次」的典型 event loop，這恰好天然契合。

## 對比與取捨：哪種提交模型配哪種場景

| 模型 | syscall/請求 | CPU 成本 | 適合 | 不適合 |
|---|---|---|---|---|
| 每請求 submit | 1 enter | 低 | 低頻、簡單程式 | 高頻（syscall 淹沒） |
| 批次 submit | 1 enter / N 請求 | 低 | 中高頻、請求可成批 | 請求零散到來 |
| submit_and_wait | 1 enter / 一輪 | 低 | 標準 event loop | — |
| SQPOLL | ≈0（thread 醒著時） | **高**（燒一核輪詢） | 持續高頻、低延遲敏感 | 低頻（白燒 CPU） |
| + DEFER_TASKRUN | 完成節奏集中 | 低 | 單 issuer 高效能服務 | 多 thread 同時提交 |

一句話決策：**低頻用普通 submit，event loop 用 submit_and_wait，確定要榨延遲且養得起一核再上 SQPOLL，單 issuer 服務配 DEFER_TASKRUN。** 不要一上來就 SQPOLL——它是重武器，多數服務批次 + submit_and_wait 就夠了。

還有一個常被忽略的順序問題：**先把程式寫對、能跑、測過，再談這些提交模型的調校**。SQPOLL、DEFER_TASKRUN 這些都是在「已經正確」之上榨效能的手段；在功能還沒穩之前就堆上一身 flag，只會讓 debug 變難（poll thread 的競態、DEFER 的 issuer 限制都會咬你）。先正確，再快。

## 踩雷集錦

1. **批次填 SQE 不處理 `get_sqe` 回 `NULL`**：SQ 填滿後 `get_sqe` 回 `NULL`，沒先 `submit` 騰空間就繼續填 → 對 `NULL` 解參考 segfault。批次一定要在 `get_sqe` 後檢查、滿了先 submit。
2. **把 `min_complete` 當「剛好這麼多」**：它是「至少」。返回時環裡可能更多 CQE，只收 `min_complete` 個會漏收，要用 `for_each_cqe` 收光。
3. **SQPOLL 的 `sq_thread_idle` 設太短**：提交間隔一超過 idle，poll thread 就去睡，每次提交都要付喚醒 enter，SQPOLL 的零 syscall 優勢蒸發。idle 要設得比你的提交間隔長。
4. **低頻場景開 SQPOLL**：養一個輪詢 thread 燒整顆 CPU，卻一秒才提交幾個請求——純浪費電和核。SQPOLL 只對持續高頻划算。
5. **SQPOLL 下以為 `io_uring_submit` 一定不下 syscall**：thread 睡著時（剛啟動、或閒置超過 idle）第一下仍會下 `IORING_ENTER_SQ_WAKEUP`。零 syscall 是「thread 醒著時」的穩態，不是絕對。
6. **DEFER_TASKRUN 不配 SINGLE_ISSUER，或多 thread 提交**：`DEFER_TASKRUN` 要求只有一個 task 提交；違反會 `-EINVAL` 或行為不符預期。它不是免費加速鍵，有前提。
7. **SQPOLL 下從多個 thread 同時 `io_uring_submit`**：SQ 的 tail 由使用者端推進，多 thread 同時推同一條 SQ 會互相踩踏（除非你自己加鎖）。SQPOLL 不會幫你把提交端變 thread-safe——它只負責 kernel 那頭輪詢。要嘛單 thread 提交，要嘛每 thread 一個 ring（可配 `ATTACH_WQ` 共用 poll thread）。
8. **以為 SQPOLL 的 poll thread 會永遠在跑**：它閒置 `sq_thread_idle` 後就睡，`htop` 看那顆核的佔用會掉回去。這是正常省電，不是壞了；下次提交 `io_uring_submit` 會自動喚醒它。

## 本章重點整理

- 三層省 syscall：**批次**（N 請求一次 enter）→ **`submit_and_wait`**（提交與等待併一次 enter）→ **SQPOLL**（kernel poll thread 輪詢 SQ，提交端零 syscall）。
- `io_uring_enter` 的 `min_complete` + `IORING_ENTER_GETEVENTS` 是「至少等這麼多完成」；`submit_and_wait` 就是它的封裝。
- SQPOLL：`sq_thread_idle` 控制 poll thread 何時去睡，睡前設 `IORING_SQ_NEED_WAKEUP`，`io_uring_submit` 自動判斷要不要下 `SQ_WAKEUP` enter。建議搭 registered files 與 `SQ_AFF` CPU 綁定。實測 3 個 read 僅 2 次 `io_uring_enter`，熱路徑提交免 syscall。
- `COOP_TASKRUN`/`TASKRUN_FLAG`/`DEFER_TASKRUN` 調的是**完成節奏**（task work 何時、在哪跑）；`DEFER_TASKRUN | SINGLE_ISSUER` 是單 issuer 高效能服務的推薦組合，內部實作見 Part 3。
- 決策：低頻 submit、event loop submit_and_wait、持續高頻且養得起一核才上 SQPOLL。

## 自我檢核

- [ ] 我能解釋批次提交怎麼把 N 次 enter 變 1 次，以及為什麼批次填 SQE 一定要處理 `get_sqe` 回 `NULL`
- [ ] 我知道 `io_uring_submit_and_wait` 相對 submit + wait 省了什麼，以及 `min_complete` 是「至少」而非「剛好」
- [ ] 我能講清 SQPOLL 怎麼達到提交零 syscall、`sq_thread_idle` 和 `IORING_SQ_NEED_WAKEUP` 各扮演什麼角色
- [ ] 我能說出 SQPOLL 什麼時候仍會下 `io_uring_enter`，以及為什麼建議搭 registered files 和 SQ_AFF
- [ ] 我能分辨「提交節奏」（批次/SQPOLL）和「完成節奏」（COOP/DEFER_TASKRUN）是兩件不同的事
- [ ] 給一個 workload，我能判斷該用哪種提交模型、該不該上 SQPOLL

## 延伸閱讀

### 官方文件 / 原始碼

- **[io_uring_enter(2)](https://man7.org/linux/man-pages/man2/io_uring_enter.2.html)**
  - **讀哪裡**：`to_submit`/`min_complete`/flags 的完整語意，以及 `IORING_ENTER_SQ_WAKEUP`、`IORING_ENTER_GETEVENTS`、`IORING_ENTER_EXT_ARG` 各做什麼——本章每個 flag 的一手定義
  - **為什麼值得讀**：提交模型的所有行為最終都落在這個 syscall 的參數上，讀它比讀任何二手說明準

- **[io_uring/sqpoll.c](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/io_uring/sqpoll.c)**（v6.18）
  - **讀哪裡**：`io_sq_thread`（poll thread 的主迴圈）、`io_sq_thread_idle` 相關邏輯——親眼看 poll thread 怎麼輪詢、何時設 `NEED_WAKEUP` 去睡
  - **前提**：Ch 2 的 ring 記憶體模型讀過；Part 3 會逐行解這個檔

### 技術文章

- **[io_uring and networking in 2023](https://github.com/axboe/liburing/wiki/io_uring-and-networking-in-2023)** — liburing wiki
  - **這篇說什麼**：`DEFER_TASKRUN`、`SINGLE_ISSUER`、`COOP_TASKRUN` 的推薦用法與適用場景，作者群的第一手建議
  - **讀哪裡**：ring setup 建議那節——它直接告訴你新服務該開哪些 flag
  - **前提**：本章讀完，對提交/完成節奏有概念後最有收穫

- **[An introduction to SQPOLL](https://unixism.net/loti/tutorial/sq_poll.html)** — Lord of the io_uring
  - **這篇說什麼**：SQPOLL 的完整範例與喚醒邏輯逐步解說，和本章實測互相印證
  - **讀哪裡**：喚醒判斷那段；它的 raw 版本讓你看到 liburing 幫你包掉的 `NEED_WAKEUP` 檢查
  - **前提**：會基本 io_uring 流程即可

### 效能參考

- **[Efficient IO with io_uring](https://kernel.dk/io_uring.pdf)** — Jens Axboe
  - **讀哪裡**：polled IO 與 kernel side polling 兩節，SQPOLL 設計動機的原文
  - **和本章關聯**：本章的「拿一核換零 syscall」取捨，作者在這裡給了數據支撐

提交模型講完，Part 1（核心模型）就完整了：ring 記憶體、memory ordering、liburing、SQE、CQE、提交模型——io_uring 的骨架你全拆過一遍。接下來是練習 A：回到 Ch 2 的 raw 層次，不用 liburing，親手刻一個完整的 `cat`，把 setup、mmap、提交、完成、錯誤處理全部自己走一遍，徹底焊死底層直覺。之後 Part 2 就放心交給 liburing，專心學各種 op。

→ [練習 A — 手刻 raw io_uring cat](./practice-a-raw-cat.md)
