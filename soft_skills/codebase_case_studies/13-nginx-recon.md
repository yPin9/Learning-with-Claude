# Ch 13 — nginx 偵察：master/worker 與模組化

> **目標**：用 `reading_code` Ch 5 的 60 分鐘偵察 SOP，在陌生的 nginx source 上建出第一張架構地圖：目錄怎麼分、進程模型長怎樣、entry point 在哪、一個 worker 醒著的時候到底在跑什麼迴圈。這章不深挖任何機制，只求「知道每塊在哪、往哪走」——把地圖畫對，後面三章才追得動。

> **目標codebase**：nginx `release-1.26.2`（commit `37fe983`）

## 為什麼需要這個？

你前面攻過 Lua（Part 1）和 SQLite（Part 2）。它們有一個共同點：**單進程、單執行緒、被動被呼叫**。你餵一段 code 給 Lua VM，它跑完回來；你送一句 SQL 給 SQLite，它算完回來。控制流是你在推的。

nginx 完全不是這回事。它是一個**常駐的網路伺服器**——沒有人「呼叫」它，是**外面的世界（成千上萬個 TCP 連線）在對它丟事件**，它得自己醒著、自己等、自己分派。這是一整類新的 codebase：event-driven、多進程、非阻塞。第一次讀這種東西，最容易犯的錯是**拿讀函式庫的直覺去讀它**——想找一個 `main` 直直讀到 `return`，結果掉進一個 `for(;;)` 的無窮迴圈裡，找不到出口，以為自己讀錯了。

沒讀錯。伺服器的「出口」就是「永遠不出去」。這章要先把這個心智模型建起來，再談 code。

nginx 值得這樣讀的理由：它是 event-driven 高並發架構的教科書級典範。**memory pool、module pipeline、reactor event loop** 這三個 pattern，你在無數 C 系統程式裡會一再遇到——Redis、HAProxy、libevent、甚至你自己以後要寫的任何高並發服務。讀懂 nginx 怎麼組織，等於一次拿到三張可遷移的設計卡片。

## 先建立直覺：伺服器不是函式，是常駐迴圈

在打開任何檔案前，先在腦中裝好這張圖。一個典型的 event-driven 伺服器長這樣：

```
   啟動一次                     然後永遠在這裡轉
  ┌─────────┐      ┌──────────────────────────────────────┐
  │ 讀設定  │      │  for (;;) {                           │
  │ 綁 port │ ───► │    等一批就緒的 fd（epoll_wait）      │ ◄─┐
  │ fork    │      │    對每個就緒 fd 呼叫它的 handler     │   │ 永遠
  │ workers │      │    處理到期的 timer                   │   │ 不
  └─────────┘      │  }                                    │ ──┘ 出去
                   └──────────────────────────────────────┘
```

nginx 把這個模型攤成**兩層進程**：

```
        ┌─────────────────────────────────────────────┐
        │  master process（不處理任何 HTTP 請求）      │
        │  - 讀 nginx.conf、綁 listen socket           │
        │  - fork 出 N 個 worker                        │
        │  - 用 signal 管理 worker（reload/reap/quit）  │
        │  - 自己 sigsuspend 睡著，被 signal 叫醒       │
        └───────────────┬───────────────┬───────────────┘
                 fork   │        fork   │        fork
            ┌───────────▼──┐  ┌─────────▼────┐  ┌──────────────┐
            │ worker 0     │  │ worker 1     │  │ worker 2 ... │
            │ event loop   │  │ event loop   │  │ event loop   │
            │ 處理連線     │  │ 處理連線     │  │ 處理連線     │
            └──────────────┘  └──────────────┘  └──────────────┘
              每個都是單執行緒、跑同一份 event loop
```

記住兩件事，後面才不會迷路：

1. **master 不碰請求**。它是個管家，管 worker 的生死，自己睡在 `sigsuspend` 上。你要找「怎麼處理 HTTP」，不要去 master 那裡找。
2. **每個 worker 是一個單執行緒的 event loop**。它靠非阻塞 I/O + 事件回呼撐起成千上萬條連線，不是一條連線開一個 thread。這是 nginx 能扛 C10K 的根本，也是 Ch 14 的主題。

## 第一步：目錄結構——nginx 怎麼分家

偵察的第一動作永遠是 `ls`。真跑：

```bash
$ cd /tmp/rd_nginx
$ ls src/
core  event  http  mail  misc  os  stream
```

七個目錄，一眼分出三類：

| 目錄 | 幹嘛的 | 這門課要不要讀 |
|---|---|---|
| `core/` | 地基：memory pool、buffer、字串、hash、紅黑樹、cycle、module 框架、`main` | 讀（Ch 13/15/16） |
| `event/` | event loop 抽象層 + 各平台 I/O 多工封裝（epoll/kqueue/select…） | 讀（Ch 14） |
| `os/unix/` | 作業系統相依層：process 管理、fork、channel、recv/send | 讀（Ch 13 進程模型） |
| `http/` | HTTP 協定實作：request 解析、phase handlers、filter chain、各種 module | 讀（Ch 16 + 練習 C） |
| `stream/` | 通用 TCP/UDP 代理（非 HTTP） | 不讀 |
| `mail/` | mail proxy（IMAP/POP3/SMTP） | 不讀 |
| `misc/` | 邊角料（`ngx_google_perftools_module` 等） | 不讀 |

`reading_code` Ch 11 的收斂技巧在此第一次上場：**七個目錄我們只讀四個**（`core`/`event`/`os`/`http`），`stream`/`mail`/`misc` 直接畫叉。這不是偷懶——這是攻堅。你想在 60 分鐘內建出地圖，就得先決定哪些不看。

看一下每個目錄多大，心裡有個規模感（真跑）：

```bash
$ for d in core event os http; do
    printf "%-8s %6s files  %8s lines\n" "$d" \
      "$(find src/$d -name '*.c' -o -name '*.h' | wc -l)" \
      "$(cat $(find src/$d -name '*.c' -o -name '*.h') | wc -l)"
  done
```

`core` 和 `event` 都是幾千行等級、`http` 大得多（HTTP 協定本身複雜）。我們只攻 `http` 裡的幾條關鍵路徑，不碰它的絕大部分 module。

## 第二步：找 entry point——`main` 在哪、往哪走

`reading_code` Ch 6 教過：找 entry point 先 `rg` `main`。C 專案的 `main` 通常很好認。真跑：

```bash
$ rg -n "^main\(" src/core/nginx.c
197:main(int argc, char *const *argv)
```

`main` 在 `src/core/nginx.c:197`。打開它，別逐行讀，**掃**——找出「啟動階段做了哪幾件大事、最後跳去哪」。骨架是：

```c
// src/core/nginx.c，ngx_main 的關鍵幾步（1.26.2，節選、略去錯誤處理）
main(int argc, char *const *argv)
{
    ...
    if (ngx_get_options(argc, argv) != NGX_OK) {   // 解析命令列 -c -s 等
        return 1;
    }
    ...
    cycle = ngx_init_cycle(&init_cycle);           // ★ 核心：讀設定、建 cycle
    ...
    if (ngx_process == NGX_PROCESS_SINGLE) {
        ngx_single_process_cycle(cycle);           // 單進程模式（除錯用）
    } else {
        ngx_master_process_cycle(cycle);           // ★ 正常模式：master 迴圈
    }
}
```

（`ngx_init_cycle` 呼叫點在 `src/core/nginx.c:293`，`ngx_master_process_cycle` 在 `main` 尾端。真跑 `rg -n "ngx_init_cycle\|ngx_master_process_cycle" src/core/nginx.c` 核對。）

三個 beacon 已經浮出來，記在地圖上：

- **`ngx_init_cycle`**（`src/core/ngx_cycle.c:39`）：nginx 的「建立世界」函式。讀 `nginx.conf`、初始化所有 module、開 listen socket、配置 memory pool——一個 `ngx_cycle_t` 就是「這一輪 nginx 運行的完整狀態」。reload 時就是建一個新 cycle、切過去、丟掉舊的。
- **`ngx_master_process_cycle`**（`src/os/unix/ngx_process_cycle.c:74`）：master 的無窮迴圈。
- **`ngx_worker_process_cycle`**（`src/os/unix/ngx_process_cycle.c:699`）：worker 的無窮迴圈——這才是我們真正要追的。

**這是你第一次感受到「entry point 不是終點」**。`main` 只是把世界建好、fork 出 worker，然後自己躺進 master 迴圈。真正處理請求的地方，在 fork 出去的 worker 裡的**另一個**迴圈。讀伺服器就是要一路追到那個迴圈為止。

## 第三步：進程模型——master 怎麼生出 worker

`rg` 一下 worker 是怎麼被造出來的（真跑）：

```bash
$ rg -n "ngx_start_worker_processes|ngx_spawn_process|ngx_worker_process_cycle" \
     src/os/unix/ngx_process_cycle.c | head
14:static void ngx_start_worker_processes(ngx_cycle_t *cycle, ngx_int_t n, ...
74:ngx_master_process_cycle(ngx_cycle_t *cycle)
130:    ngx_start_worker_processes(cycle, ccf->worker_processes, ...
336:ngx_start_worker_processes(ngx_cycle_t *cycle, ngx_int_t n, ngx_int_t type)
344:    ngx_spawn_process(cycle, ngx_worker_process_cycle, ...
699:ngx_worker_process_cycle(ngx_cycle_t *cycle, void *data)
```

鏈條清楚了：`ngx_master_process_cycle` → `ngx_start_worker_processes`（`:336`）→ 對每個 worker 呼叫 `ngx_spawn_process`（`:344`），把 `ngx_worker_process_cycle` 當 callback 傳進去。而 `ngx_spawn_process` 裡就是那個 `fork()`（真跑 `rg -n "pid = fork" src/os/unix/ngx_process.c`）：

```c
// src/os/unix/ngx_process.c:186（1.26.2，節選）
    pid = fork();

    switch (pid) {
    case -1:
        ngx_log_error(NGX_LOG_ALERT, cycle->log, ngx_errno,
                      "fork() failed while spawning \"%s\"", name);
        ...
    case 0:                          // 子進程（worker）
        ngx_parent = ngx_pid;
        ngx_pid = ngx_getpid();
        proc(cycle, data);           // proc == ngx_worker_process_cycle
        break;
    ...
    }
```

`fork()` 回 0 的那一支就是新 worker，它直接呼叫 `proc(cycle, data)`——也就是進入 `ngx_worker_process_cycle`，然後**再也不回來**。這就是進程模型的核心：**listen socket 在 fork 之前就開好了，fork 之後每個 worker 都繼承同一個 listen fd，一起 accept**。（誰真的 accept 到，靠 `ngx_accept_mutex` 協調，避免 thundering herd——Ch 14 會碰到。）

### master 迴圈：睡在 signal 上

看 master 迴圈的骨架（真跑 `sed -n '74,260p' src/os/unix/ngx_process_cycle.c` 掃過）：

```c
// src/os/unix/ngx_process_cycle.c（1.26.2，master 迴圈骨架、大量節選）
ngx_master_process_cycle(ngx_cycle_t *cycle)
{
    ...
    ngx_start_worker_processes(cycle, ccf->worker_processes, NGX_PROCESS_RESPAWN);
    ...
    for ( ;; ) {
        ...
        ngx_log_debug0(NGX_LOG_DEBUG_EVENT, cycle->log, 0, "sigsuspend");
        sigsuspend(&set);                    // ★ 睡著，等 signal

        if (ngx_reap) {                      // 有 worker 死了 → 回收
            ngx_reap = 0;
            live = ngx_reap_children(cycle);
        }
        if (ngx_terminate) { ... }           // SIGTERM → 收工
        if (ngx_quit) { ... }                // SIGQUIT → 優雅關閉
        if (ngx_reconfigure) { ... ngx_start_worker_processes(...); }  // SIGHUP → reload
        ...
    }
}
```

**master 的整個工作就是這個 for 迴圈：`sigsuspend` 睡著 → 被 signal 叫醒 → 看哪個旗標被設起來 → 做對應的事（reap/reload/quit）→ 繼續睡。** `ngx_reap`、`ngx_terminate` 這些不是普通變數，是 signal handler 設的旗標（`volatile sig_atomic_t`）。這是經典的 self-pipe / signal-flag 模式——signal handler 只做「設旗標」這件最小的事，真正的處理留給主迴圈，避免在 signal context 裡做危險操作。

**master 從頭到尾沒碰過任何 HTTP 請求。** 想找請求處理的，往 worker 走。

### worker 迴圈：這才是主戰場

```c
// src/os/unix/ngx_process_cycle.c:699（1.26.2，節選）
ngx_worker_process_cycle(ngx_cycle_t *cycle, void *data)
{
    ...
    ngx_worker_process_init(cycle, worker);
    ngx_setproctitle("worker process");

    for ( ;; ) {
        if (ngx_exiting) { ... }
        ngx_log_debug0(NGX_LOG_DEBUG_EVENT, cycle->log, 0, "worker cycle");

        ngx_process_events_and_timers(cycle);      // ★★★ event loop 的一輪

        if (ngx_terminate) { ... }
        if (ngx_quit) { ... }
        if (ngx_reopen) { ... }
    }
}
```

找到了。worker 的整個生命就是這個 `for(;;)`，而**每一輪就是一次 `ngx_process_events_and_timers(cycle)`**。這一個 function 就是 event loop 的心臟——`src/event/ngx_event.c:195`。Ch 14 整章在拆它。

現在你的地圖有了最重要的一條主動脈：

```
main (nginx.c:197)
  └─ ngx_init_cycle (ngx_cycle.c:39)        建立世界：讀 conf、init module、開 listen socket
  └─ ngx_master_process_cycle (process_cycle.c:74)
       ├─ ngx_start_worker_processes → ngx_spawn_process → fork()
       │    └─(子進程)─► ngx_worker_process_cycle (process_cycle.c:699)
       │                   └─ for(;;) ngx_process_events_and_timers  ◄── Ch 14 從這裡開始
       └─ for(;;) sigsuspend → 處理 reap/reload/quit signal
```

## 底層機制：build 依賴——讀伺服器的第一個現實坑

Ch 0 說過「能 build 的目標，你能用 gdb 動態驗證」。nginx 能 build，但它有**編譯期依賴**，這是讀陌生 C 伺服器第一天最常見的卡點——不是你環境壞了，是它本來就需要幾個系統套件。

nginx 用自己手寫的 `./auto/configure`（不是 autotools）。第一次跑，大概率卡在這裡（真跑）：

```bash
$ cd /tmp/rd_nginx
$ ./auto/configure
...
checking for PCRE2 library ... not found
checking for PCRE library ... not found
./configure: error: the HTTP rewrite module requires the PCRE library.
You can either disable the module by using --without-http_rewrite_module
option, or install the PCRE library into the system, or build the PCRE
library statically from the source with nginx by using --with-pcre=<path>
option.
```

**這訊息本身就是教材。** 它告訴你三條路：裝 PCRE、指來源路徑、或**關掉需要它的 module**。nginx 的 `configure` 很體貼——它明說「哪個 module 要它、怎麼關」。

`http_rewrite_module` 需要 PCRE（Perl 相容正則，`location ~ /regex` 那種正則比對靠它），gzip 需要 zlib。兩條路：

```bash
# 路 A：裝依賴（要真跑 nginx 才選這條）
$ sudo apt-get install -y libpcre2-dev zlib1g-dev libssl-dev
$ ./auto/configure && make

# 路 B：本課以「讀」為主，關掉需要外部庫的 module 就能 configure 過
$ ./auto/configure --without-http_rewrite_module --without-http_gzip_module
```

`configure` 過了會產出 `objs/ngx_modules.c`（列出這次 build 進去的所有 module）和 `objs/Makefile`。**`objs/ngx_modules.c` 值得一看**——它就是 Ch 16 要講的「module 陣列」的真身，configure 根據你的選項生成它。

> 提醒：本 Part 的重點是**讀**，不是跑。跑得動就 build 起來（Ch 14/練習 C 能用 gdb 在 `ngx_epoll_process_events`、`ngx_http_wait_request_handler` 下中斷點驗證）；跑不動就照著 source 走 call chain，該標「理論預期」的地方會標。無論如何，**`configure` 的依賴坑一定要親自踩一次**——這是讀所有 C 伺服器都會遇到的第一關。

## 對比與取捨：讀伺服器 vs 讀函式庫

| 面向 | 函式庫（Lua/SQLite） | 伺服器（nginx） | 讀法差異 |
|---|---|---|---|
| 控制流 | 你呼叫它，它回來 | 它常駐，事件驅動它 | 找 `for(;;)` 主迴圈，不是找 `return` |
| entry point | `main` 讀到底就懂 | `main` 只是啟動，真戲在別的迴圈 | 一路追到 worker 的 event loop |
| 進程 | 單進程 | master/worker 多進程 | 先分清「哪個進程做什麼」，別在 master 找請求 |
| 並發 | 通常不管 | 單執行緒 event loop 扛萬條連線 | 建立「非阻塞 + callback」的心智模型 |
| 狀態 | 在 stack 上（函式呼叫） | 在 heap 的 struct 上（連線散在各處） | 狀態機驅動，不是 call stack 驅動 |

最後一列是 nginx（和所有 event-driven 伺服器）最反直覺的地方：**一條連線的處理狀態不在某個函式的 stack frame 上，而是散在一個 `ngx_connection_t` / `ngx_http_request_t` struct 裡**，靠 event 回呼一次推進一點。Ch 17 會把這個 pattern（callback-driven state machine）單獨拉出來講。

## 踩雷集錦

1. **在 `main` 裡想讀到「處理 HTTP 請求」的 code**。錯誤直覺：「entry point 一路讀下去就會看到主邏輯」。正確：`main` 只負責建 cycle + fork worker，然後躺進 master 迴圈。請求處理在 fork 出去的 worker 的 `ngx_process_events_and_timers` 裡。**伺服器的 entry point 和主邏輯之間隔著一個 fork 和一層 event loop。**

2. **以為 master 進程會處理連線**。錯誤直覺：「master 是主進程，主邏輯應該在它那」。正確：master 是純管家，`sigsuspend` 睡著、被 signal 叫醒管 worker 生死，一個 HTTP byte 都不碰。你在 master 裡永遠找不到 HTTP 邏輯。

3. **看到 `for(;;)` 沒有 break 就以為是 bug 或死迴圈**。正確：伺服器的主迴圈**本來就該永遠不出去**，它靠 signal 旗標（`ngx_terminate` 等）在迴圈內部決定何時 `exit`。「沒有出口」是特性，不是缺陷。

4. **`ngx_reap` / `ngx_terminate` 這些變數在 code 裡找不到被賦值的地方，以為讀漏了**。正確：它們是被 **signal handler** 設的（`grep` 一下 `ngx_signal_handler`），不是被正常控制流賦值。event-driven + signal 的 code，很多「值從哪來」的答案在 signal handler 裡，不在主流程——`reading_code` Ch 24「讀懂狀態機與事件驅動」正是講這個。

5. **configure 失敗就懷疑自己環境**。正確：缺 PCRE/zlib 造成的 configure 失敗是**常態**，讀 nginx 的錯誤訊息最後幾行，它直接告訴你關哪個 module 或裝哪個庫。

## 進階：再往深一層

- **`ngx_cycle_t` 是「一輪運行的全世界」**：這是理解 nginx 全局狀態的鑰匙。看它的關鍵欄位（真跑 `sed -n '/^struct ngx_cycle_s {/,/};/p' src/core/ngx_cycle.h`，節選）：
  ```c
  // src/core/ngx_cycle.h（1.26.2，節選）
  struct ngx_cycle_s {
      void                  ****conf_ctx;      // 所有 module 的設定（四層指標！）
      ngx_pool_t               *pool;          // 這個 cycle 的 memory pool
      ngx_module_t            **modules;       // 這次 build/load 的 module 陣列
      ngx_connection_t         *free_connections;  // 空閒連線鏈
      ngx_connection_t         *connections;   // 連線池（預配一大塊）
      ngx_event_t              *read_events;   // 讀事件池
      ngx_event_t              *write_events;  // 寫事件池
      ngx_array_t               listening;     // listen socket 陣列
      ngx_str_t                 conf_file;     // 設定檔路徑
      ...
  };
  ```
  `connections`/`read_events`/`write_events` 是三個**預先配好的大陣列**——nginx 啟動時一次配足 `worker_connections` 個連線物件和事件物件，執行期從 `free_connections` 鏈取用、用完歸還，**完全不在請求路徑上 `malloc` 連線**（object pool 的又一個應用）。`conf_ctx` 是四層指標（`void ****`）——這是 nginx 最惡名昭彰的型別，因為設定分 main/srv/loc 三層 × module 陣列，`reading_code` Ch 23「讀懂 indirection」的極限測試。理解「reload = 建新 cycle + 平滑切換 + 丟舊 cycle」（`ngx_init_cycle` 會傳入 `old_cycle`），你就懂了 nginx 為何能 `-s reload` 不中斷服務：新舊 cycle 短暫共存，舊連線在舊 cycle 上跑完，新連線走新 cycle。
- **為什麼是多進程而不是多執行緒？** nginx 選 process 不選 thread：隔離性好（一個 worker crash 不拖垮別人）、無鎖共享少（每個 worker 自己的連線池）、`SO_REUSEPORT` 讓 kernel 幫忙分派 accept。這是刻意的架構取捨，`reading_code` Ch 25「讀懂並發程式」的視角在此有用。
- **`ngx_spawn_process` 的 channel 機制**：master 和 worker 之間用 `socketpair` 建的 channel 傳指令（`src/os/unix/ngx_channel.c`）。這是 IPC 的一個乾淨範本，行有餘力可讀。

## 本章重點整理

- nginx 是 event-driven 多進程伺服器：**master 管家（不碰請求）+ N 個 worker（各跑一個單執行緒 event loop）**。
- entry：`main`（`nginx.c:197`）→ `ngx_init_cycle`（建世界）→ `ngx_master_process_cycle` →（fork）→ `ngx_worker_process_cycle` → `for(;;) ngx_process_events_and_timers`。**真正的主戰場是最後那個迴圈**，Ch 14 從那裡開始。
- 讀伺服器的心智模型 ≠ 讀函式庫：找主迴圈不找 return、狀態在 struct 不在 stack、值可能來自 signal handler。
- build 依賴（PCRE/zlib）是讀 C 伺服器的第一個現實坑，`configure` 會直接告訴你怎麼繞。
- 偵察就是**果斷取捨**：七個目錄只讀四個，`http` 只攻幾條路徑。

## 自我檢核

- [ ] 我能畫出 master/worker 進程模型，並說出 master 為什麼不處理請求
- [ ] 我能從 `main` 一路指出到 worker event loop 的完整函式鏈與檔案:行號
- [ ] 我知道 `ngx_process_events_and_timers` 在哪、為什麼它是下一章的目標
- [ ] 我理解「伺服器主迴圈沒有出口」是特性，`ngx_terminate` 等旗標來自 signal handler
- [ ] 我親自跑過 `./auto/configure`，踩過（或看懂了）PCRE/zlib 依賴坑

## 延伸閱讀

- **[nginx development guide — Introduction](https://nginx.org/en/docs/dev/development_guide.html)**（官方開發者指南）
  - **讀哪裡**：「Introduction」與「Core」兩節；官方親自講 cycle、pool、module、connection 的心智模型，讀 code 前先看能省一半力氣
  - **前提**：會讀 C
- **《The Architecture of Open Source Applications》Vol. II — nginx 章**（[aosabook.org](https://aosabook.org/en/v2/nginx.html)）
  - **讀哪裡**：整章，尤其「Worker Model」與「nginx Internals」；nginx 開發者親自導讀架構，是本 Part 的絕佳對照
  - **前提**：無
- **`reading_code` Ch 5「第一次接觸：60 分鐘偵察」與 Ch 6「找 entry point 與主迴圈」**
  - **讀哪裡**：這兩章的 SOP 就是本章的方法論來源；本章是把它套在 nginx 上的實戰
  - **前提**：無

master/worker 的骨架有了。下一章我們鑽進 worker 的心臟——那個 `for(;;)` 每一輪都在跑的 event loop，看 nginx 怎麼用一顆執行緒、一個 `epoll_wait`，撐起成千上萬條連線。

→ [Ch 14 event loop / reactor：epoll 封裝](./14-nginx-event-loop.md)
