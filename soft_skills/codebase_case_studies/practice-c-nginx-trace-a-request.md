# 練習 C：追一個 HTTP request 的完整處理鏈

> **目標**：限時攻堅。親手追出一個 HTTP request 從 **epoll 就緒 → accept → 讀 request line/header → 走 phase handlers → content handler 產出回應 → output filter chain → 送進 socket** 的完整 call chain。把 Ch 13–17 學到的五張 pattern 卡片（reactor / arena / buffer chain / plugin pipeline / callback state machine）在**真實 code 的一條路徑上**串起來。這是 `reading_code` 練習 B「追一個功能的完整路徑」在 nginx 上的實戰。

> **目標codebase**：nginx `release-1.26.2`（commit `37fe983`）

## 任務規格

一個 `curl http://localhost/index.html` 打進 nginx，一個 worker 處理它、回一個檔案。你要交出**這個過程的完整 handler 鏈**，具體回答：

1. **入口**：worker 的 event loop（`ngx_process_events_and_timers`）epoll 就緒後，這條連線第一個被呼叫的 handler 是誰？從哪來的（誰把它設成這個 handler）？
2. **handler 接力**：這條連線的 `read handler` 從 accept 到讀完 header，**依序換過哪幾個 handler**？每次換的觸發點在哪（哪一行 `rev->handler = ...`）？
3. **進 phase 引擎**：header 讀完後，控制流怎麼進到 11 phase 的引擎？引擎的迴圈長怎樣？
4. **產出回應**：static 檔案的 content handler 是誰、在哪個 phase、怎麼被叫到？它產出的回應是什麼結構（對回 Ch 15 的 buffer chain）？
5. **送出**：回應怎麼流過 output filter chain 送進 socket？入口函式是哪個？

**交付形式**：一張 call chain 圖（函式名 + `檔案:行號`），標出「handler 在哪幾個點被換掉」（狀態機的轉移點），並在每一段旁邊註明「這對應哪張 pattern 卡片」。

## 為什麼這條鏈值得親手追

你可能想：Ch 13–16 已經把每個機制拆過了，直接看解答不就好？

不好。**分開讀懂五個機制，和把它們串成一條完整路徑，是兩種不同的理解。** 前者你腦中有五個孤立的 chunk；後者你才真正「看見」一個請求在 nginx 裡怎麼活過一遍——epoll 怎麼把它交給 handler、handler 怎麼一路換手、記憶體從哪來、回應怎麼流出去。這種「整條路徑的理解」是 onboarding 一個陌生服務、debug 一個線上問題、或評估「我這個改動會影響哪一段」時真正用得上的東西。

而且這條鏈有個教學上的殘酷之處：**它不是一條函式呼叫棧走到底**。傳統程式你 `bt` 一下就看到完整 call stack。但 nginx 的請求處理**被 I/O 切成好幾段**——accept 是一次 event、收到 header 是另一次 event、送回應可能又是幾次 event。每一段都是從 event loop 重新進來、跳到當下該跑的 handler。**所以你追這條鏈時，`bt` 只能看到「當前這一段」，看不到「上一段」——上一段的資訊存在 struct 裡，不在 stack 上。** 這正是卡片五（callback state machine）最反直覺、也最需要親手體會的地方。追過一次，你對所有 event-driven 服務的理解都會升一級。

## 時限

- **偵察 + 建鏈**：50 分鐘。逼自己在時限內用 `rg` 把鏈接出來，別逐行讀。
- **驗證（選配）**：若你 build 起來了，再花 30 分鐘用 gdb 在關鍵函式下中斷點，親眼看鏈跑過去。

計時。讀碼是速度技能，練習 C 的重點是**在壓力下用對策略**，不是讀懂每一行。

## 起手式：你已經知道的錨點

別從零開始。Ch 13–16 已經給你幾個確定的錨點，把它們寫下來當地圖的樁：

- event loop 分派：`ngx_epoll_process_events` 裡 `rev->handler(rev)`（`src/event/modules/ngx_epoll_module.c:901` 附近）——**每次 handler 被叫都是從這裡**。
- phase 引擎：`ngx_http_core_run_phases`（`src/http/ngx_http_core_module.c:863`）。
- content phase checker：`ngx_http_core_content_phase`（`src/http/ngx_http_core_module.c:1252`）。
- static handler：`ngx_http_static_handler`（`src/http/modules/ngx_http_static_module.c:49`），掛在 CONTENT phase。
- 送回應入口：`ngx_http_output_filter`（`src/http/ngx_http_core_module.c:1854`）。

**你的任務是把這些錨點之間的空隙填滿**——尤其是「accept 之後到進 phase 引擎之前」那段 handler 接力。

## 如果你卡住了

不要直接看參考解答。卡住時，照順序試這五個方向（都指向真實檔案，但不直接給答案）：

1. **找 accept 之後連線的第一個 handler 是誰**：listen socket 的 `handler` 在哪被設定？`rg -n "ls->handler = " src/http/ngx_http.c`。accept 到新連線後，`ngx_event_accept`（`src/event/ngx_event_accept.c`）會呼叫 `ls->handler(c)`——它是誰？

2. **找連線 read handler 的第一個賦值**：新連線建好後，誰設了 `c->read->handler`？在 `ngx_http_init_connection`（`src/http/ngx_http_request.c:200`）裡 `rg` 一下 `rev->handler = `。這是 handler 接力的起點。

3. **追 handler 的每一次改寫**：`rg -n "rev->handler = ngx_http" src/http/ngx_http_request.c`。把結果按行號排出來，每一行都是狀態機的一次轉移。想：什麼條件下從 wait_request 換到 process_request_line？從 process_request_line 換到 process_request_headers？

4. **找 header 讀完後的匯流點**：request line 和 headers 都解析完，控制流會匯到哪個函式？`rg -n "ngx_http_process_request\b" src/http/ngx_http_request.c`——`ngx_http_process_request`（`:2053`）。它裡面呼叫了什麼把控制交給 phase 引擎？（提示：找 `ngx_http_handler`。）

5. **確認 content handler 怎麼被 phase 引擎叫到**：回去讀 `ngx_http_core_content_phase`（`:1252`），它對 `ph->handler(r)` 的回傳值怎麼反應？static handler 什麼時候回 `NGX_DECLINED`、什麼時候真的送檔案？產出回應後靠哪個函式送出？（提示：`ngx_http_send_header` + `ngx_http_output_filter`。）

## 分段步驟

把大任務切成五段，一段一段接：

- **第 1 段（reactor → accept）**：從 `ls->handler = ngx_http_init_connection`（`ngx_http.c:1823`）出發，確認 accept 新連線後 `ngx_event_accept` 呼叫 `ls->handler(c)`（`ngx_event_accept.c:313`）進入 `ngx_http_init_connection`。→ 卡片一（reactor）。
- **第 2 段（init → wait_request）**：`ngx_http_init_connection` 裡設 `rev->handler = ngx_http_wait_request_handler`（`ngx_http_request.c:318`）。連線的第一個 read handler 定案。→ 卡片五（state machine 起點）。
- **第 3 段（讀 request line/header 的接力）**：`ngx_http_wait_request_handler` 讀到資料後換手到 `ngx_http_process_request_line`（`:522`），後者解析完 request line 後換到 `ngx_http_process_request_headers`（`:1195`）。→ 卡片五（handler 接力）+ 卡片二/三（用 pool 配 buffer 存 header）。
- **第 4 段（匯流 → phase 引擎）**：header 讀完，`ngx_http_process_request`（`:2053`）把 read/write handler 改成 `ngx_http_request_handler`（`:2136/2137`），並呼叫 `ngx_http_handler(r)` → `ngx_http_core_run_phases(r)`。請求進入 11 phase 引擎。→ 卡片四（plugin pipeline）。
- **第 5 段（content → 送出）**：引擎走到 CONTENT phase，`ngx_http_core_content_phase` 叫 `ngx_http_static_handler`，它讀檔案、造一個 `in_file` 的 buffer chain，經 `ngx_http_send_header` + `ngx_http_output_filter` 流過 output filter chain 送出。→ 卡片三（buffer chain 零拷貝）+ 卡片四（filter chain）。

<details>
<summary>參考解答（先自己追過 50 分鐘再打開）</summary>

### 完整 call chain

```
worker: for(;;) ngx_process_events_and_timers        src/event/ngx_event.c:195
  └─ ngx_process_events (巨集→函式指標)               src/event/ngx_event.h:400
       └─ ngx_epoll_process_events                    src/event/modules/ngx_epoll_module.c:784
            └─ epoll_wait(...)                          :800
            └─ rev->handler(rev)                        :901   ◄── 每次 handler 被叫都從這

── 連線第一次就緒：這是一個「新連線可 accept」的 listen socket 事件 ──
rev->handler == ngx_event_accept                       src/event/ngx_event_accept.c:21
  └─ ls->handler(c)                                     :313   （ls->handler 在 ngx_http.c:1823 被設）
       └─ ngx_http_init_connection                     src/http/ngx_http_request.c:200
            └─ rev->handler = ngx_http_wait_request_handler   :318   ★轉移1
            └─ （若無資料）ngx_add_timer + 等下一次 epoll 就緒

── 連線再次就緒：client 送來了 request bytes ──
rev->handler == ngx_http_wait_request_handler          src/http/ngx_http_request.c:366
  └─ b = ngx_create_temp_buf(c->pool, size)            :400   （從連線 pool 切 header buffer，卡片2/3）
  └─ n = c->recv(c, b->last, size)                      :426
  └─ c->data = ngx_http_create_request(c)              :516   （建 ngx_http_request_t，配 request pool）
  └─ rev->handler = ngx_http_process_request_line       :522   ★轉移2
  └─ ngx_http_process_request_line(rev)                 :523

ngx_http_process_request_line                          src/http/ngx_http_request.c:1083
  └─ 解析 "GET /index.html HTTP/1.1"（狀態機式 parser，ngx_http_parse.c）
  └─ ngx_http_process_request_uri(r)                    :1136
  └─ rev->handler = ngx_http_process_request_headers    :1195   ★轉移3
       （若一次沒讀完 header，反覆被 epoll 叫回這個 handler 續讀）

ngx_http_process_request_headers                       src/http/ngx_http_request.c:1367
  └─ 逐行解析 header，存進 r->headers_in（用 request pool，卡片2）
  └─ header 全部讀完 → ngx_http_process_request(r)       :1529

ngx_http_process_request                               src/http/ngx_http_request.c:2053
  └─ c->read->handler  = ngx_http_request_handler       :2136   ★轉移4（之後的就緒事件走這個）
  └─ c->write->handler = ngx_http_request_handler       :2137
  └─ ngx_http_handler(r)                                :2140

ngx_http_handler                                       src/http/ngx_http_core_module.c:820
  └─ r->phase_handler = 0                               （重設 phase 游標）
  └─ r->write_event_handler = ngx_http_core_run_phases  （之後續跑用）
  └─ ngx_http_core_run_phases(r)                        :858

ngx_http_core_run_phases                               src/http/ngx_http_core_module.c:863   ◄── 卡片4：plugin pipeline 引擎
  └─ while (ph[r->phase_handler].checker)
       rc = ph[r->phase_handler].checker(r, &ph[...])
  ── 依序跑 POST_READ → SERVER_REWRITE → FIND_CONFIG →
     REWRITE → ... → PREACCESS → ACCESS → PRECONTENT → CONTENT ──

ngx_http_core_content_phase                            src/http/ngx_http_core_module.c:1252
  └─ rc = ph->handler(r)   // == ngx_http_static_handler（掛在 CONTENT phase）

ngx_http_static_handler                                src/http/modules/ngx_http_static_module.c:49
  └─ 若 uri 以 '/' 結尾 → return NGX_DECLINED（讓給 autoindex）  ◄── 卡片4：chain of responsibility
  └─ 打開 /index.html，ngx_open_cached_file
  └─ b = ngx_calloc_buf(r->pool)                        （造 buffer，卡片2）
  └─ b->in_file = 1; b->file_pos = 0; b->file_last = len;  ◄── 卡片3：資料在檔案，不在記憶體（零拷貝）
  └─ out.buf = b; out.next = NULL;                       （單元素 buffer chain）
  └─ rc = ngx_http_send_header(r)                        （送 header，走 header filter chain）
  └─ return ngx_http_output_filter(r, &out)              :~294 附近

ngx_http_send_header                                   src/http/ngx_http_core_module.c:1832
  └─ return ngx_http_top_header_filter(r)                :1849   （header filter chain 鏈頭）

ngx_http_output_filter                                 src/http/ngx_http_core_module.c:1854   ◄── 卡片4：filter chain 入口
  └─ rc = ngx_http_top_body_filter(r, in)               :1864
       ── 資料流過 body filter chain（頭插串起）：
          gzip/copy/postpone/... → ngx_http_write_filter（鏈尾）
       ngx_http_write_filter                            src/http/ngx_http_write_filter_module.c:48
          └─ c->send_chain(c, ...)  // Linux 上是 ngx_linux_sendfile_chain
               └─ sendfile(fd, ...) // in_file 的 buf → kernel 直接從 page cache 送 socket，零拷貝
```

### 五個 handler 轉移點（狀態機的心跳）

| 轉移 | 在哪 | 從 → 到 | 觸發 |
|---|---|---|---|
| ★1 | `ngx_http_request.c:318` | (無) → `wait_request_handler` | 新連線建立 |
| ★2 | `:522` | `wait_request_handler` → `process_request_line` | 收到第一批 bytes |
| ★3 | `:1195` | `process_request_line` → `process_request_headers` | request line 解析完 |
| ★4 | `:2136` | `process_request_headers` → `request_handler` | header 全讀完，進 phase 引擎 |
| （引擎內） | `run_phases` 靠 `r->phase_handler` 游標 | phase 一格一格推進 | 每 phase checker 回傳值 |

**這五個轉移就是卡片五（callback state machine）的具體樣貌**：一條連線的處理進度不在任何函式的 stack 上，而在「`c->read->handler` 當下指向誰 + `r->phase_handler` 游標」這兩個 struct 欄位上。每次 epoll 就緒都從 `rev->handler(rev)` 進來，跳到當下該跑的那一步。

### 動態驗證：若你 build 起來了

本鏈以讀 source 建出（call chain 與檔案:行號都在 1.26.2 真 source 核對過）。若你照 Ch 13 把 nginx build 起來並跑起來，可以用 gdb 親眼驗證——**以下為理論預期的操作步驟，作者環境為 Windows、未實跑 gdb**，你在 WSL/Linux build 後應能重現：

```bash
# 1. 用最小設定跑一個前景、單 worker 的 nginx（方便 gdb 附著）
$ cat > /tmp/ngx.conf <<'EOF'
daemon off;
master_process off;          # 單進程，省得追 fork
events { worker_connections 64; }
http {
    server { listen 8080; location / { root /tmp/www; } }
}
EOF
$ mkdir -p /tmp/www && echo "hello" > /tmp/www/index.html
$ gdb --args ./objs/nginx -c /tmp/ngx.conf -p /tmp

# 2. 在 gdb 裡下中斷點，涵蓋鏈上的關鍵轉移
(gdb) break ngx_http_wait_request_handler
(gdb) break ngx_http_process_request_line
(gdb) break ngx_http_core_run_phases
(gdb) break ngx_http_static_handler
(gdb) break ngx_http_output_filter
(gdb) run

# 3. 另開一個終端打一個請求
$ curl http://localhost:8080/index.html

# 4. 回 gdb，每次停下用 bt 看 call stack，親眼確認鏈
(gdb) bt          # 應看到 ...run_phases → content_phase → static_handler
(gdb) continue    # 一路 continue，看 handler 依序被叫
```

**理論預期**：斷點會依 `wait_request_handler` → `process_request_line` → `run_phases` → `static_handler` → `output_filter` 的順序命中；在 `static_handler` 停下時 `bt` 會顯示它是被 `ngx_http_core_content_phase` → `ngx_http_core_run_phases` 叫進來的。這正好動態印證上面靜態追出的鏈。（`master_process off` 讓 nginx 單進程跑，不 fork，gdb 不必追子進程——這是除錯 nginx 的實用小技巧。）

</details>

## 常見的走岔路（追這條鏈時你可能踩的坑）

追之前先知道哪裡容易迷路，能省不少時間：

- **在 `ngx_epoll_process_events` 裡想直接看到「處理 HTTP」的 code**。不會有。epoll 只做 `rev->handler(rev)`，HTTP 邏輯在 `handler` 指向的函式裡，而那是動態的。你得跳出 epoll module、去 `ngx_http_request.c` 找 handler 是誰。
- **以為 `ngx_http_process_request_line` 一次就把整條 request line 讀完**。不一定。client 可能分段送，`recv` 回 `NGX_AGAIN` 時這個 handler 直接 return，等下次 epoll 就緒**再進來同一個 handler**續讀。所以你會看到 handler 裡有「讀到一半就回去」的分支——那不是錯誤路徑，是正常的非阻塞讀。
- **在 `ngx_http_core_run_phases` 裡想找「呼叫 static handler」的直接 code**。找不到直接呼叫——引擎只叫 `ph->checker`，checker（`ngx_http_core_content_phase`）才叫 `ph->handler`（static handler）。**checker 和 handler 兩層**，別漏了中間這層。
- **把 `ngx_http_finalize_request` 當成「請求真的結束」**。它更像「這一階段處理完了，決定下一步」——可能是送回應、可能是等更多 I/O、可能是 keepalive 等下一個請求。別以為看到 finalize 就到終點。
- **想在單一 `bt` 裡看到從 accept 到送回應的完整 stack**。看不到（見上面「為什麼值得追」）。你得靠「handler 怎麼換」把幾段 stack 在腦中拼起來，這是這個練習的核心技能。

## 驗證你的答案

不管有沒有 build，都能自我驗證這幾點：

1. **鏈的每一環都能 `rg` 到**：你圖上每個 `檔案:行號`，隨手 `rg -n "函式名" 檔案` 應該對得上。對不上就是編錯了，回去查。
2. **五個轉移點齊全**：`rg -n "rev->handler = ngx_http\|c->read->handler = ngx_http" src/http/ngx_http_request.c` 的結果應該涵蓋你標的轉移點。
3. **content handler 確實掛在 CONTENT phase**：`rg -n "phases\[NGX_HTTP_CONTENT_PHASE\]" src/http/modules/ngx_http_static_module.c` 應看到 static 的註冊。
4. **每一段都對得上一張 pattern 卡片**：如果某段你講不出對應哪張卡片，表示 Ch 17 那張卡片還沒吃進去，回去重讀。

## 延伸挑戰

追完 static 的路徑後，挑一到兩個往深走：

1. **換成 proxy 路徑**：如果 location 是 `proxy_pass` 而非 static，content phase 走的是 `r->content_handler`（不是 phase 上的 handler）。追 `ngx_http_proxy_handler`——它怎麼把請求丟給 upstream、怎麼在等 upstream 回應時 return `NGX_OK` 讓出執行緒、upstream 就緒時怎麼被 event loop 叫回來續跑。這是卡片五（狀態機）最精彩的體現：一個請求跨越「等 client → 等 upstream → 送 client」多次 I/O 中斷。
2. **追 header 沒一次讀完的情況**：故意想像 client 分兩個 TCP 段送 header。`ngx_http_process_request_headers` 讀到一半 `recv` 回 `NGX_AGAIN` 時怎麼辦？它 return 後 handler 沒換，下次 epoll 就緒又進來同一個 handler 續讀——這就是「同一個 handler 被反覆叫、靠 buffer 累積狀態」的模式。
3. **追 output filter chain 的實際順序**：`rg -n "ngx_http_top_body_filter = " src/http/` 把所有 filter 的頭插點列出來，按「build 連結順序」推出實際執行順序，驗證 gzip 確實在 write 之前。對回 Ch 16 進階的「頭插法 + 連結順序」。

## 自我檢核

- [ ] 我能不看解答，畫出從 `ngx_event_accept` 到 `ngx_http_output_filter` 的完整 handler 鏈與檔案:行號
- [ ] 我能指出五個 handler 轉移點，並解釋每個的觸發條件
- [ ] 我能說出「請求處理進度存在哪兩個 struct 欄位上」（`c->read->handler` + `r->phase_handler`），以及為什麼不在 stack 上
- [ ] 我能把鏈的每一段對到一張 pattern 卡片（reactor / arena / buffer chain / plugin pipeline / state machine）
- [ ] 我能解釋 static handler 產出的回應為什麼是 `in_file` 的 buffer chain、以及它如何走到 `sendfile` 零拷貝
- [ ]（選配）我 build 起 nginx，用 gdb 的斷點 + `bt` 動態驗證了這條鏈

## 延伸閱讀

- **[nginx development guide — Connection / Request 章節](https://nginx.org/en/docs/dev/development_guide.html#http_request)**
  - **讀哪裡**：「Connection」「Request」「Request processing phases」三節；官方對本練習追的這條鏈的權威敘述，追完 code 再讀能校正你的理解
  - **前提**：讀得懂 C
- **`reading_code` 練習 B「追一個功能的完整路徑」與 Ch 18「debugger-driven reading」**
  - **讀哪裡**：練習 B 的方法就是本練習的模板；Ch 18 教怎麼用 gdb 下中斷點驗證靜態追出的鏈（正是選配驗證那段）
  - **前提**：無
- **[Emiller's Guide To Nginx Module Development — The Handler Phase](https://www.evanmiller.org/nginx-modules-guide.html)**
  - **讀哪裡**：「Anatomy of a Handler」一節；從「寫一個 content handler」的角度反看本練習追的 content phase，換視角能加深理解
  - **前提**：讀完 Ch 16

Part 3 到此完成——你已把 nginx 的 event-driven 架構讀透，並在真實 call chain 上串起五張 pattern 卡片。下一個 Part 換一種硬：git 的「資料模型即一切」，content-addressed object store 是完全不同的一類設計美學。

→ [Ch 18 git 偵察：plumbing vs porcelain 與 object model](./18-git-recon-object-model.md)
