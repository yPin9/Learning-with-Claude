# Ch 17 — 萃取 pattern：reactor / object pool / plugin pipeline

> **目標**：把讀 nginx 這四章讀到的東西**結晶成可遷移的 pattern 卡片**。每張卡片講清楚：它解什麼問題、長什麼樣（beacon——一眼認出的特徵）、在 nginx 哪裡、你在別的 codebase 哪裡會再遇到、以及自己要用時的關鍵取捨。這章不是複習，是**把具體知識抽象成「一眼認出」的能力**——這正是本課的核心目的。讀完後你合上教材，應該能對著空氣把這五張卡片講出來。

> **目標codebase**：nginx `release-1.26.2`（commit `37fe983`）

## 為什麼需要這個？

`reading_code` 教你方法，這門課練的是**速度**——速度來自 pattern 辨識（Ch 1 講的 chunking）。你讀完 nginx 的 event loop、memory pool、module pipeline，如果只記得「nginx 這樣做」，那是一次性知識；如果能抽象成「reactor pattern」「arena allocator」「plugin pipeline」，那是**可以套到下一個陌生 codebase 的 chunk**。

差別具體是什麼？下次你打開 Redis 的 `ae.c`，看到一個 `for(;;)` 裡 `aeApiPoll` + 對就緒 fd 叫 handler——如果你有 reactor 這張卡片，你一眼 chunk 成「喔這是 reactor，跟 nginx 一樣」，三分鐘看懂；沒有這張卡片，你得從頭逐行推。**這就是專家和新手讀碼速度差一個數量級的原因，不是專家讀得更用力，是他們 chunk 更大。**

這章的五張卡片，每張都會在本課後面的 Part 或你的日常工作中再出現。我把「你會在哪再遇到」明確寫出來，就是為了幫你建立跨 codebase 的連結——pattern 的價值在於它跨專案通用。

## 卡片一：reactor / event loop

**解什麼問題**：一顆執行緒同時服務大量 I/O 連線（C10K），不為每條連線開 thread。

**長什麼樣（beacon）**：
```
for (;;) {
    n = wait_for_ready_fds(timeout);   // epoll_wait / kqueue / poll / select
    for each ready fd:
        fd->handler(fd);                // 查表叫對應 callback
    process_expired_timers();
}
```
一眼認出的特徵：**一個無窮迴圈、一個「等就緒事件」的阻塞點、一張「fd → handler」的分派、非阻塞 socket + callback**。看到 `epoll_wait`/`kqueue`/`poll` 被包在 `for(;;)` 裡、後面跟著對就緒事件的分派迴圈，就是它。

**在 nginx 哪裡**：`ngx_process_events_and_timers`（`src/event/ngx_event.c:195`）→ `ngx_epoll_process_events`（`src/event/modules/ngx_epoll_module.c:784`）的 `epoll_wait` + `rev->handler(rev)`。分派表的樞紐是 `epoll_event.data.ptr` 直接帶回 `ngx_connection_t`。（Ch 14）

**你在哪還會遇到**：
- **Redis** 的 `aeMain` / `ae.c`（`aeApiPoll` + `aeProcessEvents`）——幾乎是 nginx reactor 的精簡版。
- **Node.js** 底下的 **libuv**、**Python** 的 **asyncio**、**libevent/libev**——同一個 pattern 包成函式庫。
- 本課 Part 4 的 **git** 沒有這個（它是命令列工具），但你日後讀任何網路服務都會撞上。

**用時的關鍵取捨**：reactor 換到極省資源，付出的是**心智負擔**——順序邏輯被切成 callback、狀態得攤在 struct 上（見卡片五）。只有一個阻塞點（`epoll_wait`），任何會 block 的操作都得改非阻塞或丟 thread pool，否則整個 loop 卡死。

## 卡片二：arena / region / pool allocator

**解什麼問題**：一批「同生共死」的物件要頻繁配置，逐一 `malloc/free` 太慢、易漏、會碎。

**長什麼樣（beacon）**：
```
pool = create_pool(size);      // 一次跟 OS 要一大塊
p1 = pool_alloc(pool, n1);     // 配置 = 指標往前推，不個別記帳
p2 = pool_alloc(pool, n2);
...
destroy_pool(pool);            // 一次還整塊，不逐一 free
```
一眼認出的特徵：**有 `create/destroy_pool`（或 `_context`/`_arena`/`_region`）這對函式、配置函式回傳指標但沒有對應的個別 free、內部是「指標往前推」**。看到 `xxx_alloc(pool, size)` 而不是 `malloc(size)`，且找不到 `xxx_free(ptr)` 被普遍呼叫，就是它。

**在 nginx 哪裡**：`ngx_pool_t`（`src/core/ngx_palloc.h`）+ `ngx_create_pool`/`ngx_palloc`/`ngx_destroy_pool`（`src/core/ngx_palloc.c`）。核心是 `ngx_palloc_small` 的 `p->d.last = m + size; return m;`。附帶 `cleanup` 鏈把非記憶體資源綁進同一生命週期。（Ch 15）

**你在哪還會遇到**：
- **PostgreSQL** 的 **MemoryContext**（`palloc`/`pfree`/`MemoryContextReset`）——本課 Part 6 冷讀 PostgreSQL executor 時**一定會撞上**，那時你會慶幸讀過 nginx pool。
- **Apache** 的 **apr_pool**（nginx pool 的近親，同一設計哲學）。
- **protobuf** 的 **arena**、**LLVM** 的 **BumpPtrAllocator**、無數遊戲引擎的 frame allocator。

**用時的關鍵取捨**：前提是「一批物件共享同一個生命週期」。符合（一個請求、一幀畫面、一次編譯 pass）就爽——配置飛快、幾乎不漏、不碎；不符合（物件生命週期各異、需個別回收）就別硬套。判斷那一問：**這批東西同生共死嗎？** pool 的邊界要對齊生命週期的邊界（nginx 分 connection pool / request pool 就是這道理）。

## 卡片三：buffer chain 零拷貝

**解什麼問題**：資料來自多處（記憶體 / 檔案 / 壓縮後）、要流過多層處理，複製成本高，想盡量不搬資料。

**長什麼樣（beacon）**：
```
struct buf {
    ptr pos, last;        // 邏輯：有效資料範圍（游標）
    ptr start, end;       // 物理：這塊記憶體邊界
    file_ref, file_pos;   // 或者：資料根本在檔案裡，不在記憶體
    flags;                // last_buf / in_file / ...
};
struct chain { buf *b; chain *next; };   // 用鏈串起多塊
```
一眼認出的特徵：**buf 分「邏輯游標」和「物理邊界」兩組指標、能描述「不在記憶體的資料」（in_file）、用單向鏈串成處理單位、處理時推游標而非搬資料**。

**在 nginx 哪裡**：`ngx_buf_t`/`ngx_chain_t`（`src/core/ngx_buf.h`）。`pos/last`（邏輯）vs `start/end`（物理），`in_file`+`file_pos/last` 讓 `sendfile` 零拷貝送檔案。output filter chain 傳的就是這條鏈。（Ch 15）

**你在哪還會遇到**：
- **Linux kernel** 的 **`sk_buff`**（網路封包的 buffer，同樣分 head/data/tail 指標、能指向不搬的資料）——`kernel_internals` 讀網路堆疊時的核心結構。
- **Java NIO** 的 **ByteBuffer**（position/limit/capacity 就是 pos/last/end 的翻版）、**Netty** 的 **ByteBuf**（甚至有 CompositeByteBuf 做零拷貝拼接）。
- **io_uring**、**scatter-gather I/O**（`iovec`）——同一個「用描述避免複製」的思路。

**用時的關鍵取捨**：零拷貝省的是 CPU 和記憶體頻寬，付出的是**複雜度**——所有權變模糊（這塊 buf 誰能改、誰負責回收）、游標管理容易錯（推錯 `pos`/`last` 就讀錯資料）。適合大流量資料管線，不適合簡單的小資料處理（那時直接複製更清楚）。

## 卡片四：plugin / phase pipeline

**解什麼問題**：系統要可擴充——加功能不改核心。

**長什麼樣（beacon）**，兩種形態：

*階段式*（過關卡）：
```
enum phases { PHASE_A, PHASE_B, PHASE_C, ... };   // 核心寫死的固定順序
// plugin 在註冊 hook 裡把 handler 掛到某 phase：
register() { push(phases[PHASE_B].handlers, my_handler); }
// 引擎沿 phase 走，每格叫 handler，某回傳值表「不認領，換下一個」
```

*串接式*（流水線）：
```
// 每個 plugin init 時頭插進一條全域指標鏈：
init() { next_filter = top_filter; top_filter = my_filter; }
// 資料從 top_filter 進入，每環加工後叫 next_filter，串到底
```
一眼認出的特徵：**核心定義一組固定的 hook / phase / filter 點，功能模組在啟動時把自己「註冊」上去，核心 code 不認識任何具體功能**。看到「一個 module/plugin 結構體 + 一個 register/init 把自己掛到某個核心陣列或指標鏈」，就是它。

**在 nginx 哪裡**：`ngx_module_t`（統一介面，`src/core/ngx_module.h`）+ 11 個 phase（`ngx_http_phases` enum）+ `ngx_http_core_run_phases`（引擎，`src/http/ngx_http_core_module.c:863`）。階段式：static module 在 `postconfiguration` 裡 push 到 `phases[NGX_HTTP_CONTENT_PHASE].handlers`。串接式：output filter chain 頭插 `ngx_http_top_body_filter`。（Ch 16）

**你在哪還會遇到**：
- **Express / Koa** 的 **middleware**（`app.use()` 串成鏈，`next()` 傳遞——串接式的 JS 版）。
- **webpack / Babel / ESLint** 的 **plugin**、**Linux kernel** 的 **LSM hook**、**Apache** 的 module。
- 本課 Part 5 的 **CPython** 也有類似的「type 定義一組 slot、物件協定」的可插拔結構（卡片會在 Ch 26 對照）。

**用時的關鍵取捨**：plugin 架構換到可擴充，付出的是**間接性**——核心 code 到處是函式指標間接跳轉，讀起來繞（`reading_code` Ch 23 的主題）。設計時最難的是**選對 hook 點的粒度**：太少不夠靈活，太多核心變複雜。nginx 的 11 phase 是幾十年打磨出的平衡。

## 卡片五：callback-driven state machine

**解什麼問題**：event-driven 系統裡，一個請求的處理會被 I/O 中斷很多次（等 client、等 upstream、等 disk），不能用 thread 阻塞等——狀態得存起來、之後續跑。

**長什麼樣（beacon）**：
```
// 狀態不在 stack，在 heap 的 struct 上：
struct request {
    int phase;                  // 進度游標（跑到哪了）
    handler_fn read_handler;    // 現在該用哪個 callback
    handler_fn write_handler;
    ... 一堆中間狀態 ...
};
// event loop 就緒時叫 req->read_handler(req)，handler 做一點、更新狀態、可能換 handler、然後 return
```
一眼認出的特徵：**一個又大又雜的 struct（一堆 flag、一堆指標、一個「現在該做什麼」的 handler 欄位），handler 做一小步就 return、把進度存回 struct**。看到 `c->read->handler = some_new_handler;`（處理過程中改自己的 handler），就是狀態機在推進。

**在 nginx 哪裡**：`ngx_connection_t`（`c->read->handler`/`c->write->handler`）+ `ngx_http_request_t`（`r->phase_handler` 游標、`r->write_event_handler`）。handler 隨處理階段換：`ngx_http_wait_request_handler` → `ngx_http_process_request_line` → `ngx_http_process_request_headers` → phase 引擎。`ngx_http_core_run_phases` 靠 `r->phase_handler` 游標可暫停續跑。（Ch 14 + Ch 16）

**你在哪還會遇到**：
- 任何 **async/await** 底下的狀態機——Rust 的 `Future`、C# 的 `async` 都是編譯器幫你把順序 code 拆成這種 struct + 續跑點（`rust` 課的 async 章講得很細）。
- **Redis** 的 client 狀態、**HAProxy** 的 stream state machine、任何 protocol 解析器。
- 本課的 **git** 沒有（同步工具），但這是所有 event-driven 網路程式的共同骨架。

**用時的關鍵取捨**：這個 pattern 是 reactor 的直接後果——選了 reactor（卡片一），就得接受狀態機（卡片五）。好處是省執行緒、能暫停續跑；壞處是**順序邏輯被打散、極難讀**（一個請求的處理散在十幾個 handler 裡，得靠「handler 怎麼換」重建控制流）。這也是為什麼 async/await 這種「寫起來像順序、跑起來像狀態機」的語言特性後來大流行——它把這個 pattern 的醜藏起來了。

## 五張卡片怎麼咬合

這五張不是各自獨立，它們在 nginx 裡是一個整體，環環相扣：

```
   reactor（卡1）epoll 醒來、叫 handler
        │
        ▼
   callback state machine（卡5）handler 推進請求狀態、需要記憶體時
        │
        ▼
   arena pool（卡2）從 request pool 切記憶體，請求結束整塊還
        │
        ▼
   plugin phase pipeline（卡4）請求走過 11 phase，content handler 產出回應
        │
        ▼
   buffer chain（卡3）回應以零拷貝的 buf 鏈，流過 output filter chain，送出
```

**一個請求的完整旅程，就是這五張卡片依序上場。** 這也是為什麼 nginx 是這門課的絕佳教材——它把五個高頻 pattern 用最乾淨的方式擺在一起，讀一個 codebase 拿五張卡。練習 C 會讓你親手把這條旅程追一遍。

## 怎麼把「認出卡片」練成反射

有了五張卡片不代表你就會用。pattern 辨識是肌肉，得刻意練。三個具體練法：

1. **反向預測**：拿到一個新 codebase，先別讀，**先猜它會有哪些卡片**。「這是網路服務 → 大概有 reactor（找 `epoll`/`kqueue`/`poll` 在迴圈裡）+ 某種連線狀態機」。「這是編譯器/DB → 大概有 arena（找 `xxx_alloc(ctx, ...)` 而非 `malloc`）」。帶著預測去讀，命中就強化了卡片，沒命中就發現了新變體——兩種結果都在擴充你的字典。這是 `reading_code` Ch 10「假設驅動讀碼」用在架構層級。

2. **一句話定位**：讀到一段陌生 code，強迫自己用一句話說「這是什麼卡片、beacon 是哪幾行」。說得出來就是 chunk 成功了；說不出來就是還沒 chunk，得再讀。這個「一句話」就是 Ch 2 訓練協定裡的費曼複述，在 pattern 層級的縮小版。

3. **收集變體**：同一張卡片在不同 codebase 有不同長相。arena 在 nginx 是 `ngx_pool_t`、在 PostgreSQL 是有階層的 `MemoryContext`、在 LLVM 是 `BumpPtrAllocator`。**把同一張卡片的三四個變體並排，你就從「認得 nginx 的 pool」升級到「認得所有 arena」**——後者才是可遷移的。本課刻意讓你讀多個 codebase，就是為了餵這個「收集變體」的過程。

練到後來，你讀新 code 的內心獨白會從「這在幹嘛？（逐行推）」變成「喔這是 X 卡片的 Y 變體（一眼 chunk）」。那一刻你的讀碼速度就換了檔。

## 對比與取捨：這五張卡在別的 Part 的對應

| nginx pattern | 同課其他 Part 的對應 | 差異重點 |
|---|---|---|
| reactor（卡1） | （本課無同類，日後 Redis/libuv） | nginx 是最乾淨的 C 版範本 |
| arena pool（卡2） | **PostgreSQL MemoryContext**（Part 6） | PG 的 context 有樹狀階層、可 reset 子 context，比 nginx pool 更精緻 |
| buffer chain（卡3） | （Lua/SQLite 的 buffer 較簡單） | nginx 的 `in_file` 零拷貝是它特有的伺服器需求 |
| plugin pipeline（卡4） | **CPython type slot / object protocol**（Ch 26） | CPython 用 type 的 function slot 做多型，形態不同但同為「核心定義介面、具體填實作」 |
| state machine（卡5） | 三個 VM 的 dispatch loop（Lua Ch4/SQLite Ch9/CPython Ch23） | VM 是「同步狀態機」（一個大迴圈按 PC 走），nginx 是「非同步狀態機」（被 I/O 打斷、續跑）——都是狀態外化，驅動方式不同 |

最後一列值得玩味：**VM 的 dispatch loop 和 nginx 的 event loop 骨架驚人地像**——都是「一個迴圈，取下一個要做的事（opcode / event），查表叫 handler，更新狀態」。差別只在「下一件事」從哪來：VM 從 bytecode 陣列按 PC 取，nginx 從 epoll 取就緒事件。**認出這個同構，你就同時 chunk 了 VM 和 event loop 兩大類 codebase**——這是 pattern 遷移的高光時刻，Ch 27「三個 VM 橫向對照」會再挖。

## 踩雷集錦

1. **把 pattern 當「nginx 的實作細節」記，而不是抽象卡片**。錯誤：記住「nginx 用 `ngx_pool_t`」。正確：記住「arena allocator：一批同生命週期物件、配置=推指標、整塊釋放」——後者才能套到 PostgreSQL、Apache、你自己的 code。**記細節是一次性的，記 pattern 是可複利的。**

2. **以為認出 pattern 就懂了全部**。正確：pattern 給你「這大概是什麼」的快速假設，但具體實作總有 nginx 特有的巧思（instance bit 防 stale event、`d.failed` 優化、filter 頭插法）。**pattern 是入口不是終點**——認出後還是要驗證細節（`reading_code` Ch 10 假設驅動）。

3. **硬把不合適的場景套 pattern**。正確：arena 的前提是同生命週期、reactor 的代價是狀態機。看到別人用了某 pattern，先問「他的場景符合前提嗎」，而不是「這個 pattern 好棒我也要用」。**每張卡片的『取捨』欄比『長什麼樣』欄更重要**——那決定了你該不該用。

4. **只認出單張卡片，沒看到它們怎麼咬合**。正確：nginx 的威力在五張卡片協同（reactor 驅動狀態機、狀態機用 arena、產出走 pipeline、回應是 buffer chain）。讀一個系統要看 pattern 之間的接縫，那才是架構。

## 進階：再往深一層

- **建立你自己的 pattern 字典**：這五張卡片是本課 Ch 30「你的 pattern 字典」的種子。每讀完一個 codebase 就往字典加卡片，格式固定（解什麼問題 / beacon / 在哪 / 別處遇到 / 取捨）。累積到二三十張，你讀任何新 codebase 都在「認卡片」而非「從零推」。
- **反向用 pattern 找 code**：知道 nginx 是 reactor，你可以反過來「猜」它一定有一個 `epoll_wait` 包在 `for(;;)` 裡、一定有防 stale event 的機制、一定有 timer 和 I/O 共用迴圈——帶著 pattern 的預期去讀，比盲讀快得多。這是 `reading_code` Ch 10 假設驅動的高階玩法。
- **pattern 的反模式也要收集**：認得好 pattern 的同時，收集「這樣做會出事」的反例（例如 event loop 裡不小心放了 blocking call、arena 硬要個別 free）。正反都有，判斷才準。

## 本章重點整理

- 五張可遷移卡片：**reactor / event loop**、**arena / pool allocator**、**buffer chain 零拷貝**、**plugin / phase pipeline**、**callback-driven state machine**。
- 每張卡片記四件事就夠用：**解什麼問題、beacon（一眼認出的特徵）、在哪還會遇到、關鍵取捨**。取捨欄最重要——它決定你該不該用。
- 五張卡片在 nginx 裡協同上場，構成一個請求的完整旅程；**pattern 的價值在跨 codebase 遷移**（arena→PostgreSQL、reactor→Redis、state machine→async/await）。
- VM 的 dispatch loop 和 nginx 的 event loop 是同構的狀態機，差別只在「下一件事」從哪取——認出這個同構是 pattern 遷移的高光。

## 自我檢核

- [ ] 我能合上教材，把五張卡片各自的「問題 / beacon / 取捨」講出來
- [ ] 我能說出每張卡片在哪個其他 codebase（Redis/PostgreSQL/kernel sk_buff/Express middleware/async）會再遇到
- [ ] 我能畫出五張卡片在 nginx 裡怎麼咬合成一個請求的旅程
- [ ] 我能解釋「記 pattern」為什麼比「記 nginx 實作細節」更有複利
- [ ] 我能講清 VM dispatch loop 和 event loop 的同構與差異

## 延伸閱讀

- **《The Programmer's Brain》— chunking 與 beacon 章節**（Felienne Hermans, Manning 2021）
  - **讀哪裡**：第 2–3 章；本章「把知識結晶成卡片」的認知科學依據——為什麼 pattern 庫讓你讀得快、beacon 怎麼運作
  - **前提**：無
- **《Pattern-Oriented Software Architecture》Vol. 2（POSA2）**
  - **讀哪裡**：Reactor、Proactor 兩個 pattern 的完整描述；把 nginx 的 reactor 放進學術 pattern 語彙，你會更精準地認出變體
  - **前提**：讀過本章
- **本課 Ch 30「你的 pattern 字典」與 Ch 27「三個 VM 橫向對照」**
  - **讀哪裡**：Ch 30 把六個 codebase 的卡片收成一張總表、Ch 27 深挖 VM 與 event loop 的同構；本章的五張卡片是它們的起點
  - **前提**：讀完各 Part

pattern 卡片備妥。接下來別只是看——練習 C 會給你一個限時任務：親手追一個 HTTP request 從 accept 到回應送出的完整處理鏈，讓這五張卡片在真實 call chain 上活起來。

→ [練習 C：追一個 HTTP request 的完整處理鏈](./practice-c-nginx-trace-a-request.md)
