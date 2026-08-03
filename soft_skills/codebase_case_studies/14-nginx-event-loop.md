# Ch 14 — event loop / reactor：epoll 封裝

> **目標**：鑽進 worker 的心臟。搞懂 nginx 怎麼用**一顆執行緒、一個 `epoll_wait`、一堆 callback**，撐起成千上萬條連線——也就是 C10K 的解法。追出 `ngx_process_events_and_timers` → `ngx_epoll_process_events` → `epoll_wait` → 分派 handler 這條主動脈，並看清 nginx 如何把「哪個 I/O 多工機制」抽象成一組函式指標，讓上層迴圈完全不知道底下是 epoll 還是 kqueue。

> **目標codebase**：nginx `release-1.26.2`（commit `37fe983`）

## 為什麼需要這個？

先講清楚 nginx 要解的問題：**C10K**——一台機器同時處理一萬條連線。

老派伺服器（Apache prefork）的解法是「一條連線一個 process/thread」。一萬條連線 = 一萬個 thread。問題致命：每個 thread 幾 MB stack、context switch 成本、鎖競爭，記憶體和排程開銷把機器壓垮。而且大部分時間這些 thread 都**卡在 I/O 上睡覺**（等 client 傳資料、等 disk、等 upstream），純粹浪費。

nginx 反過來：**一顆執行緒不睡，用 `epoll` 一次問 kernel「這一萬條連線裡，哪幾條現在有事？」，只處理有事的那幾條，處理完立刻回去問下一批。** 連線的狀態不放在 thread stack（沒有一萬個 stack），而是放在 heap 上的 struct，靠 callback 一次推進一點。這就是 **reactor pattern**：

> reactor = 一個 event demultiplexer（epoll_wait）+ 一張「fd → handler」的分派表。迴圈不斷 demux 出就緒事件，查表叫對應的 handler。

你在別處一定見過這個結構——libevent、Node.js 的 libuv、Redis 的 `aeMain`、Python 的 `asyncio`。全都是同一個 pattern 的變體。讀懂 nginx 這一個，你等於拿到一把讀所有 event loop 的鑰匙。這正是 Ch 17 要收斂的 pattern 卡片之一。

## 先建立直覺：reactor 的一輪長怎樣

在打開 code 前，先把一輪迴圈在腦中演一遍：

```
   ┌────────────────────────────────────────────────────────────┐
   │  worker 的一輪（ngx_process_events_and_timers）             │
   │                                                            │
   │  1. 問 timer red-black tree：最近的 timer 還有多久到期？   │
   │     → 這個時間當作 epoll_wait 的 timeout                    │
   │                                                            │
   │  2. epoll_wait(timeout)  ── 阻塞在這裡，直到：             │
   │        (a) 有 fd 就緒，或                                   │
   │        (b) timeout 到（有 timer 該處理了）                  │
   │                                                            │
   │  3. 對每個就緒的 fd：                                       │
   │        從 epoll event 拿回 ngx_connection_t                │
   │        可讀 → 呼叫 c->read->handler(rev)                    │
   │        可寫 → 呼叫 c->write->handler(wev)                   │
   │                                                            │
   │  4. 處理所有到期的 timer（ngx_event_expire_timers）        │
   │                                                            │
   │  回到 1                                                     │
   └────────────────────────────────────────────────────────────┘
```

三個關鍵設計，先記住：

1. **只有一個地方會阻塞：`epoll_wait`。** 除此之外沒有任何 blocking call——所有 socket 都是 non-blocking，讀不到就回 `EAGAIN`，handler 直接 return，等下一次就緒事件。這是「非阻塞 + callback」的全部祕密。
2. **timer 和 I/O 用同一個迴圈處理。** epoll_wait 的 timeout 設成「最近 timer 的到期時間」，所以 timer 到期時 epoll_wait 剛好醒來，不需要另開執行緒管 timer。
3. **「哪個 fd 有事」和「這個 fd 該做什麼」分離。** epoll 只告訴你「fd 就緒」，該做什麼是 fd 綁的那個 handler 決定的。這個 handler 會隨連線狀態變（等 header 時是一個 handler、送 body 時是另一個）——這就是狀態機。

## 核心：`ngx_process_events_and_timers`——一輪的骨架

Ch 13 追到 worker 迴圈每輪呼叫 `ngx_process_events_and_timers`。打開它（`src/event/ngx_event.c:195`），骨架如下（真跑 `sed -n '195,265p' src/event/ngx_event.c`，此處節選）：

```c
// src/event/ngx_event.c:195（1.26.2，節選）
void
ngx_process_events_and_timers(ngx_cycle_t *cycle)
{
    ngx_uint_t  flags;
    ngx_msec_t  timer, delta;

    if (ngx_timer_resolution) {
        timer = NGX_TIMER_INFINITE;
        flags = 0;
    } else {
        timer = ngx_event_find_timer();        // ★ 1. 問 timer 樹：多久後有 timer 到期
        flags = NGX_UPDATE_TIME;
        ...
    }

    if (ngx_use_accept_mutex) {                // accept 互斥：避免 thundering herd
        if (ngx_accept_disabled > 0) {
            ngx_accept_disabled--;
        } else {
            if (ngx_trylock_accept_mutex(cycle) == NGX_ERROR) {
                return;
            }
            if (ngx_accept_mutex_held) {
                flags |= NGX_POST_EVENTS;       // 我拿到 accept 鎖：事件先 post 排隊
            } else {
                if (timer == NGX_TIMER_INFINITE || timer > ngx_accept_mutex_delay) {
                    timer = ngx_accept_mutex_delay;
                }
            }
        }
    }
    ...
    delta = ngx_current_msec;

    (void) ngx_process_events(cycle, timer, flags);   // ★★ 2. 這一句就是 epoll_wait + 分派

    delta = ngx_current_msec - delta;
    ...
    ngx_event_process_posted(cycle, &ngx_posted_accept_events);  // 先跑排隊的 accept 事件

    if (ngx_accept_mutex_held) {
        ngx_shmtx_unlock(&ngx_accept_mutex);          // 放掉 accept 鎖
    }

    ngx_event_expire_timers();                        // ★ 3. 處理到期 timer

    ngx_event_process_posted(cycle, &ngx_posted_events);  // 再跑其他排隊事件
}
```

對照剛才的直覺圖，四個步驟一一對上：

- `ngx_event_find_timer()`（`:206`）→ 圖的第 1 步，問 timer 樹。
- `ngx_process_events(cycle, timer, flags)`（`:248`）→ 圖的第 2、3 步，epoll_wait + 分派。**這是全章重點，馬上拆。**
- `ngx_event_expire_timers()`（`:263`）→ 圖的第 4 步，處理到期 timer。

中間那一大段 `ngx_use_accept_mutex` 是 nginx 的一個精巧設計，先記個名字，本章末尾「進階」再回來。

### 第一個 indirection 陷阱：`ngx_process_events` 不是函式

你想「跳進 `ngx_process_events` 看它怎麼跑」，`rg` 一下（真跑）：

```bash
$ rg -n "ngx_process_events\b" src/event/ngx_event.h
400:#define ngx_process_events   ngx_event_actions.process_events
```

**它不是函式，是一個巨集，展開成 `ngx_event_actions.process_events`——一個函式指標。** 這是 `reading_code` Ch 22（巨集）+ Ch 23（indirection）的雙重陷阱：你以為在呼叫函式，其實在透過一張表間接跳轉。表在哪？`ngx_event_actions` 是一個 `ngx_event_actions_t`，被某個 event module 在初始化時填進去。

這正是 nginx 抽象 I/O 多工機制的手法。看 `ngx_event_actions_t`（`rg -n "} ngx_event_actions_t" src/event/ngx_event.h` 找到它，內容節選）：

```c
// src/event/ngx_event.h（1.26.2，ngx_event_actions_t 節選）
typedef struct {
    ngx_int_t  (*add)(ngx_event_t *ev, ngx_int_t event, ngx_uint_t flags);
    ngx_int_t  (*del)(ngx_event_t *ev, ngx_int_t event, ngx_uint_t flags);
    ...
    ngx_int_t  (*process_events)(ngx_cycle_t *cycle, ngx_msec_t timer,
                                 ngx_uint_t flags);   // ★ 就是這個
    ngx_int_t  (*init)(ngx_cycle_t *cycle, ngx_msec_t timer);
    void       (*done)(ngx_cycle_t *cycle);
} ngx_event_actions_t;
```

一組函式指標。Linux 上會被 epoll module 填成 epoll 的實作，FreeBSD 上填成 kqueue，Solaris 上填成 eventport……**上層的 `ngx_process_events_and_timers` 完全不知道底下是誰**，它只呼叫 `ngx_process_events` 這個巨集。這是 C 裡最經典的 vtable / strategy pattern——用函式指標做多型。看它在 epoll module 裡怎麼被填（真跑）：

```c
// src/event/modules/ngx_epoll_module.c:185-199（1.26.2，節選）
    {
        ngx_epoll_add_event,             /* add an event */
        ngx_epoll_del_event,             /* delete an event */
        ...
        ngx_epoll_process_events,        /* process the events */  ★
        ngx_epoll_init,                  /* init the events */
        ngx_epoll_done,                  /* done the events */
    }
```

所以 `ngx_process_events(...)` 這一句在 Linux 上真正跳到的，是 `ngx_epoll_process_events`。**失敗是教材：如果你只 `rg "ngx_process_events"` 找函式定義，會一無所獲，然後懷疑人生。正解是認出它是巨集 → 展開成函式指標 → 找誰填了這張表。** 讀 nginx 這種高度抽象的 C，這個「巨集→指標→實作」的三跳，你會不斷遇到。

## 底層機制：`ngx_epoll_process_events`——epoll_wait 與分派

現在跳到真身 `src/event/ngx_epoll_module.c:784`。這是 reactor 的核心（真跑 `sed -n '784,935p'`，此處節選最關鍵的骨幹）：

```c
// src/event/modules/ngx_epoll_module.c:784（1.26.2，大量節選）
static ngx_int_t
ngx_epoll_process_events(ngx_cycle_t *cycle, ngx_msec_t timer, ngx_uint_t flags)
{
    int                events;
    ngx_connection_t  *c;
    ...
    events = epoll_wait(ep, event_list, (int) nevents, timer);   // ★ 唯一的阻塞點

    err = (events == -1) ? ngx_errno : 0;

    if (flags & NGX_UPDATE_TIME || ngx_event_timer_alarm) {
        ngx_time_update();                    // 醒來第一件事：更新 nginx 的快取時鐘
    }
    ...
    for (i = 0; i < events; i++) {
        c = event_list[i].data.ptr;           // ★ 從 epoll event 拿回連線物件

        instance = (uintptr_t) c & 1;         // 低位藏了 instance 旗標（防 stale event）
        c = (ngx_connection_t *) ((uintptr_t) c & (uintptr_t) ~1);

        rev = c->read;

        if (c->fd == -1 || rev->instance != instance) {
            /* the stale event from a file descriptor that was just closed */
            continue;                          // ★ 過濾這一輪內被關掉的 fd 的殘留事件
        }

        revents = event_list[i].events;
        ...
        if ((revents & EPOLLIN) && rev->active) {   // 可讀
            rev->ready = 1;
            ...
            if (flags & NGX_POST_EVENTS) {
                queue = rev->accept ? &ngx_posted_accept_events
                                    : &ngx_posted_events;
                ngx_post_event(rev, queue);    // 拿了 accept 鎖時：先排隊，晚點跑
            } else {
                rev->handler(rev);             // ★★★ 直接呼叫這條連線的 read handler
            }
        }

        wev = c->write;
        if ((revents & EPOLLOUT) && wev->active) {  // 可寫
            ...
            if (flags & NGX_POST_EVENTS) {
                ngx_post_event(wev, &ngx_posted_events);
            } else {
                wev->handler(wev);             // ★★★ 呼叫 write handler
            }
        }
    }
    return NGX_OK;
}
```

整個 reactor 的靈魂就在三行：

```c
events = epoll_wait(ep, event_list, nevents, timer);   // 問 kernel 誰就緒
c = event_list[i].data.ptr;                            // 從事件拿回連線
rev->handler(rev);                                     // 呼叫那條連線該做的事
```

**`data.ptr` 是整個設計的樞紐。** 當初把 fd 加進 epoll 時，nginx 就在 `epoll_event.data.ptr` 塞了指向 `ngx_connection_t` 的指標（低位還藏了 instance 旗標）。所以 epoll 回報「fd X 就緒」時，nginx 不需要另外查表「fd X 是哪條連線」——kernel 幫它把連線物件原封不動帶回來了。這是 reactor pattern 裡「fd → handler 分派表」的一個極省的實作：分派資訊直接搭 epoll 的便車存在 kernel 裡。

**`rev->handler(rev)` 這一句是「事件驅動」四個字的全部。** epoll 說「這條連線可讀了」，nginx 就叫它的 read handler。這個 handler 是誰？取決於連線現在處於哪個狀態：

- 剛 accept 的 listen socket → handler 是 `ngx_event_accept`
- 等 HTTP request 的連線 → handler 是 `ngx_http_wait_request_handler`
- 正在讀 header → handler 是 `ngx_http_process_request_line` / `..._headers`
- 該送回應了 → write handler 是 `ngx_http_request_handler`

**同一條連線，handler 會隨處理進度換來換去。** 這就是 callback-driven state machine：狀態不在 stack，在 `ngx_connection_t` + 「現在綁的是哪個 handler」上。練習 C 會親手追一遍這個 handler 怎麼一路換過去。

### stale event 這個坑，順手講

上面那段 `if (c->fd == -1 || rev->instance != instance) continue;` 值得停一下。想像一個場景：這一輪 epoll_wait 回報了 3 個就緒事件，你處理第 1 個時把某條連線關了（`close(fd)`），而第 3 個事件剛好是那條已關連線的殘留。如果直接 `handler` 下去，就會操作到已釋放的物件——use-after-free。

nginx 用 `instance` 這一個 bit 解決：每次連線被重用（fd 回收再分配），instance bit 翻轉一次。epoll event 裡藏著事件產生當下的 instance；處理時比對連線當前的 instance，對不上就知道「這是舊連線的殘留事件」，`continue` 跳過。**這是 event-driven 程式的經典陷阱與經典解法**，也是 `reading_code` Ch 24「讀懂狀態機與事件驅動」會警告你的那類 bug。你讀任何 epoll 封裝時都該找一下「它怎麼防 stale event」。

## 核心：timer red-black tree——I/O 與 timer 共用一個迴圈

伺服器到處是 timeout：client header 讀太慢要斷、keepalive 閒置要關、upstream 沒回應要放棄。這些 timer 怎麼跟 epoll 共存？

nginx 把所有 timer 放進一棵**紅黑樹**，key 是到期時間。真跑：

```bash
$ rg -n "ngx_event_timer_rbtree" src/event/ngx_event_timer.c src/event/ngx_event_timer.h
src/event/ngx_event_timer.h:28:extern ngx_rbtree_t  ngx_event_timer_rbtree;
src/event/ngx_event_timer.c:13:ngx_rbtree_t  ngx_event_timer_rbtree;
```

`ngx_event_find_timer()`（迴圈開頭呼叫的）就是去這棵樹取「最早到期的那個 timer 還有多久」（真跑 `sed -n '/^ngx_event_find_timer/,/^}/p'`）：

```c
// src/event/ngx_event_timer.c:33（1.26.2，節選）
ngx_msec_t
ngx_event_find_timer(void)
{
    ngx_msec_int_t      timer;
    ngx_rbtree_node_t  *node, *root, *sentinel;

    if (ngx_event_timer_rbtree.root == &ngx_event_timer_sentinel) {
        return NGX_TIMER_INFINITE;        // 沒有 timer → epoll_wait 無限等
    }
    root = ngx_event_timer_rbtree.root;
    sentinel = ngx_event_timer_rbtree.sentinel;

    node = ngx_rbtree_min(root, sentinel);   // ★ 紅黑樹最左節點 = 最早到期
    timer = (ngx_msec_int_t) (node->key - ngx_current_msec);
    return (ngx_msec_t) (timer > 0 ? timer : 0);
}
```

紅黑樹的 `min`（最左節點）就是最快到期的 timer。用它到期前的時間當 `epoll_wait` 的 timeout，**巧妙之處**：epoll_wait 要嘛被 I/O 就緒喚醒（處理連線），要嘛 timeout 到（剛好該處理 timer 了），兩種情況都醒得剛剛好——**沒有 busy-wait，沒有另開 timer 執行緒**。醒來後 `ngx_event_expire_timers()` 從樹裡把所有到期的 timer 摘出來、呼叫它們的 handler。

為什麼用紅黑樹不用最小堆？紅黑樹支援 O(log n) 的**任意刪除**——連線提早完成時要把它的 timer 從樹裡拿掉，堆做這件事很麻煩，紅黑樹很自然。這是資料結構選型跟著使用模式走的一個好範本。

## 對比與取捨：三種並發模型

| 模型 | 代表 | 一條連線的成本 | 阻塞在哪 | 心智負擔 |
|---|---|---|---|---|
| thread-per-conn | Apache prefork | 一個 thread（幾 MB stack） | 每個 thread 各自阻塞在 I/O | 低（順序寫，但貴） |
| event loop / reactor | **nginx**、Redis、Node | 一個 struct（幾百 byte） | 只有 `epoll_wait` 阻塞 | 高（callback、狀態機） |
| coroutine / 綠色執行緒 | Go、Rust async | 一個 goroutine/future | runtime 幫你在 I/O 點讓出 | 中（看起來順序，底下是 event loop） |

reactor 的取捨很清楚：**極省資源（一萬連線一顆執行緒扛）換來心智負擔（順序邏輯被切成一堆 callback，狀態得自己攤在 struct 上）**。這也是為什麼 Go/async 這類 coroutine 模型後來流行——它們想「寫起來像 thread-per-conn（順序），跑起來像 reactor（省）」。但底層機制你懂了 nginx 這一套，就懂了它們在幫你隱藏什麼。

## 踩雷集錦

1. **`rg "ngx_process_events"` 找不到函式定義，以為讀漏**。正確：它是巨集（`ngx_event.h:400`），展開成函式指標 `ngx_event_actions.process_events`。找實作要找「誰填了 `ngx_event_actions`」——Linux 上是 epoll module 的 `ngx_epoll_process_events`。**巨集→函式指標→實作，這個三跳是讀 nginx 的日常。**

2. **以為 `epoll_wait` 之外還有別的地方會 block**。正確：reactor 的鐵律是「只有一個阻塞點」。所有 socket non-blocking，讀不到回 `EAGAIN`，handler 直接 return。如果你在 handler 裡看到會阻塞的呼叫，那要嘛是 bug，要嘛是丟給 thread pool（nginx 的 `aio threads`）。

3. **以為一條連線由固定一個函式處理**。正確：連線的 read/write handler 會隨狀態換（wait_request → process_request_line → process_headers → ...）。**狀態在「現在綁哪個 handler」上，不在某個函式的 stack 上。** 找「這連線現在會跑什麼」要看 `c->read->handler` 當下指向誰。

4. **忽略 stale event 的防護，以為 `for` 迴圈裡直接 `handler` 就好**。正確：同一輪裡前面的事件可能關掉後面事件的 fd，nginx 用 `instance` bit + `c->fd == -1` 過濾殘留事件防 use-after-free。讀任何 epoll 封裝都該找這道防線。

5. **把 timer 想成另開一個執行緒管**。正確：timer 和 I/O 共用同一個迴圈——紅黑樹取最近到期時間當 `epoll_wait` 的 timeout，兩者用一顆執行緒處理。**「timer 就是設對 epoll_wait 的 timeout」是 reactor 的關鍵一招。**

## 進階：再往深一層

- **`ngx_accept_mutex` 與 thundering herd**：所有 worker 都繼承同一個 listen fd。若不協調，一個連線進來會**同時喚醒所有 worker** 去 accept（驚群效應），只有一個成功、其餘白醒。nginx 用一把跨進程互斥鎖（`ngx_accept_mutex`，`src/event/ngx_event.c` 開頭那段 `ngx_trylock_accept_mutex`），同一時間只讓一個 worker 監聽 accept 事件。拿到鎖的 worker 還會把事件先 `ngx_post_event` 排隊、盡快放掉鎖，減少持鎖時間。現代 Linux 也可以改用 `SO_REUSEPORT` 讓 kernel 直接分派，繞過這把鎖。
- **`ngx_posted_events` 延後佇列**：有些事件不適合在 epoll 迴圈裡當場處理（例如持著 accept 鎖時），nginx 先 `ngx_post_event` 塞進 queue，放掉鎖後再 `ngx_event_process_posted` 一次跑掉。這是「先收集、後處理」的批次化技巧。
- **邊緣觸發（edge-triggered）**：epoll module init 時對連線用 `EPOLLET`（`NGX_USE_CLEAR_EVENT`）。ET 模式下 handler 必須一次把資料讀到 `EAGAIN` 為止（否則會漏事件）——這是為什麼 nginx 的 read handler 都在迴圈裡讀到 `EAGAIN`。`reading_code` Ch 24 對 ET/LT 的差異有背景。
- **連線是預配的 object pool，不是每次 `malloc`**：`event.data.ptr` 帶回的 `ngx_connection_t` 從哪來？不是 accept 時 `malloc` 的——nginx 啟動時就一次配好 `worker_connections` 個連線物件（`cycle->connections`）和對應的讀/寫事件物件（`cycle->read_events`/`write_events`），用一條 `free_connections` 鏈管理空閒的。accept 到新連線就從鏈頭取一個、`ngx_get_connection`，關閉時 `ngx_free_connection` 歸還。**這是 object pool pattern（Ch 17 卡片二的近親）：高並發下連物件配置都預先做掉，請求路徑上零配置。** 這也是為什麼 `worker_connections` 是硬上限——物件池就那麼大。`rg -n "ngx_get_connection\|free_connections" src/core/ngx_connection.c` 可追。

## 本章重點整理

- nginx worker 是一個 **reactor**：`epoll_wait` demux 出就緒 fd → 從 `event.data.ptr` 拿回 `ngx_connection_t` → 呼叫 `c->read/write->handler`。全章靈魂就這三行。
- 主動脈：`ngx_process_events_and_timers`（`ngx_event.c:195`）→ `ngx_process_events`（巨集/函式指標）→ `ngx_epoll_process_events`（`ngx_epoll_module.c:784`）→ `epoll_wait`。
- I/O 多工機制被抽象成 `ngx_event_actions_t` 函式指標表，上層迴圈不知道底下是 epoll 還是 kqueue——C 版的 strategy pattern。
- timer 放紅黑樹，取最近到期時間當 `epoll_wait` 的 timeout，**I/O 與 timer 共用一顆執行緒、一個迴圈**。
- 只有 `epoll_wait` 會 block；連線狀態在 struct + 「現在綁哪個 handler」上，不在 stack——callback-driven state machine。

## 自我檢核

- [ ] 我能把 `ngx_process_events_and_timers` 的一輪四步（找 timer → epoll_wait → 分派 handler → 處理 timer）對到 code 的行號
- [ ] 我能解釋為什麼 `rg "ngx_process_events"` 找不到函式，以及正確的追法（巨集→函式指標→epoll 實作）
- [ ] 我能說出 `event.data.ptr` 為什麼是分派的樞紐、`instance` bit 為什麼能防 stale event
- [ ] 我能解釋 timer 紅黑樹如何跟 epoll_wait 的 timeout 綁在一起，讓 I/O 和 timer 共用一個迴圈
- [ ] 我能對比 thread-per-conn vs reactor vs coroutine 的取捨，並說出 reactor 換到的是什麼、付出的是什麼

## 延伸閱讀

- **Dan Kegel, [The C10K problem](http://www.kegel.com/c10k.html)**
  - **讀哪裡**：整篇，尤其「I/O Strategies」一節；這是 nginx event loop 設計的歷史動機，讀完你才懂為什麼要這麼折騰
  - **前提**：懂 socket 基本 API
- **Douglas Schmidt, [Reactor pattern（POSA）](https://www.dre.vanderbilt.edu/~schmidt/PDF/reactor-siemens.pdf)**
  - **讀哪裡**：pattern 的結構圖與 participants；把 nginx 的具體實作對回這張抽象圖，你就能一眼認出所有 event loop
  - **前提**：無
- **`reading_code` Ch 23「讀懂 indirection」與 Ch 24「讀懂狀態機與事件驅動」**
  - **讀哪裡**：Ch 23 講函式指標/vtable 的追法（正是 `ngx_event_actions`）、Ch 24 講 event-driven 的 stale event/狀態機陷阱（正是本章的 handler 切換）
  - **前提**：無

reactor 的骨架清楚了：epoll 醒來、拿回連線、叫 handler。但 handler 一跑起來要配置記憶體、要組回應——nginx 不用 `malloc/free` 逐一管理，而是一套 memory pool + buffer chain。下一章拆這個 C 系統程式最可遷移的 pattern。

→ [Ch 15 memory pool、buffer chain、資料結構慣例](./15-nginx-memory-and-buffers.md)
