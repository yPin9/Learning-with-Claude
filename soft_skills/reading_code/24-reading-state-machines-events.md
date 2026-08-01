# Ch 24 — 讀懂狀態機與事件驅動

> **目標**：學會讀「沒有直線控制流」的 code。event loop 不是從上讀到下、狀態機的下一步取決於當前 `state` 變數、非同步回呼把一件邏輯上連續的事切成好幾個彼此不相鄰的函式片段。本章給你一套固定讀法：**先找 event loop 的骨架 → 找 state 變數 → 找 transition（誰改 state）→ 畫出狀態轉移圖**。以 redis 的 `ae` event loop（reactor pattern）與 replica 端 `syncWithMaster` 這個明寫 FSM 為真實教材，最後畫出基於真讀 `ae.c` 的流程圖。

> **環境**：WSL2 Ubuntu 22.04，沙包 `~/reading_code_lab/redis`（redis 7.4.0）。本章的 `ae.c` / `replication.c` 節錄、`rg` 抓 state 欄位與 transition 都是真讀後照抄；ASCII 圖基於實際讀 `aeMain`/`aeProcessEvents`/`syncWithMaster` 繪製。

## 為什麼事件驅動的 code 沒辦法「從上讀到下」

先建立心智模型。你讀一般函式，控制流是直線：進入 → 依序執行 → 返回，讀完整個函式就懂了。事件驅動打破這個假設：

```
   直線 code（好讀）              事件驅動 code（暈眩）
 ┌──────────────┐              ┌──────────────────────────┐
 │ step1();     │              │  while(!stop)            │
 │ step2();     │  vs          │    poll(fds);            │
 │ step3();     │              │    for each ready fd:    │
 │ return;      │              │       fd->callback(...)  │ ← 跳去哪個 callback？
 └──────────────┘              │  （回到 poll，等下一個）  │   什麼時候再回來？
   讀完就懂                     └──────────────────────────┘
```

三個讓閱讀失效的特徵：

- **控制反轉（IoC）**：不是你的 code 呼叫函式庫，是 event loop 在「有事發生時」回呼你註冊的函式。主流程是那個 `while` 迴圈，你的業務邏輯散落在一堆 callback 裡（呼應 Ch 23 的 indirection——callback 就是註冊進 loop 的函式指標）。
- **狀態外顯**：一件邏輯上連續的事（如 replica 跟 master 握手：連線→ping→auth→psync→傳輸）被切成好幾次「socket 可讀 → 回呼 → 做一小步 → 返回等下次可讀」。**進度不存在呼叫堆疊裡，存在一個 `state` 變數裡。** 你不追那個變數，就不知道「現在做到哪一步、下一步是什麼」。
- **時間上不相鄰**：狀態機的 `REPL_STATE_RECEIVE_PING_REPLY` 和 `REPL_STATE_RECEIVE_AUTH_REPLY` 兩段 code 在原始碼裡緊鄰，執行上卻隔著一次網路往返、中間 event loop 可能處理了幾千個別的請求。

破解的固定四步（本章骨架）：

```
1. 找 event loop 骨架  → while(!stop){ poll; dispatch; }  在哪
2. 找 state 變數        → 哪個欄位存「現在在哪個狀態」
3. 找 transition        → 哪些行寫 state =（改變狀態的點）
4. 畫狀態轉移圖         → 把 (現態 → 事件 → 次態) 外化成圖
```

## 第一步：找 event loop 骨架——redis 的 `aeMain`

任何事件驅動程式的核心都是一個「等事件 → 分派」的迴圈。redis 的在 `ae.c`，主迴圈 `aeMain` 短到不可思議（真實節錄，完整照抄）：

```c
void aeMain(aeEventLoop *eventLoop) {
    eventLoop->stop = 0;
    while (!eventLoop->stop) {
        aeProcessEvents(eventLoop, AE_ALL_EVENTS|
                                   AE_CALL_BEFORE_SLEEP|
                                   AE_CALL_AFTER_SLEEP);
    }
}
```

**這 5 行就是 redis 的心臟**（Ch 0 用 cscope 定位過它 `server.c:7251` 被 `main` 呼叫）。整個伺服器的一生：只要沒人喊停，就反覆呼叫 `aeProcessEvents`。所有命令處理、複製、過期、持久化，全是這個迴圈某一輪裡「某個 fd 可讀/可寫/計時器到期」觸發的 callback。

**讀事件驅動程式的第一招**：先找到這個 `while` 迴圈（通常叫 `*_main` / `*_loop` / `run` / `event_base_dispatch`），確認「主流程就是它，其餘都是被它回呼的碎片」。找到它，你就有了唯一的直線骨架，剩下的 callback 都掛在它上面。

## 第二步：讀 `aeProcessEvents`——reactor pattern 的一輪

`aeMain` 每轉一圈做的事在 `aeProcessEvents`。這是 **reactor pattern** 的教科書實現：等（poll）多個 fd，哪個就緒就分派給對應 handler。抓它的骨架（真實節錄，刪去細節與註解）：

```c
int aeProcessEvents(aeEventLoop *eventLoop, int flags) {
    ...
    if (eventLoop->beforesleep != NULL && (flags & AE_CALL_BEFORE_SLEEP))
        eventLoop->beforesleep(eventLoop);          // 睡前 hook（fsync、送 reply…）

    numevents = aeApiPoll(eventLoop, tvp);          // ← 阻塞在這，等 fd 就緒或 timeout

    if (eventLoop->aftersleep != NULL && flags & AE_CALL_AFTER_SLEEP)
        eventLoop->aftersleep(eventLoop);           // 醒來 hook

    for (j = 0; j < numevents; j++) {               // 對每個就緒的 fd
        aeFileEvent *fe = &eventLoop->events[fd];
        ...
        if (!invert && fe->mask & mask & AE_READABLE) {
            fe->rfileProc(eventLoop,fd,fe->clientData,mask);   // 分派：可讀 → read callback
        }
        if (fe->mask & mask & AE_WRITABLE) {
            fe->wfileProc(eventLoop,fd,fe->clientData,mask);   // 分派：可寫 → write callback
        }
        ...
    }
    /* Check time events */
    if (flags & AE_TIME_EVENTS)
        processed += processTimeEvents(eventLoop);   // 計時器事件（serverCron 等）
    ...
}
```

一輪的骨架清清楚楚：**beforesleep hook → poll 等事件 → aftersleep hook → 逐個就緒 fd 分派 read/write callback → 處理計時器事件**。`aeApiPoll` 是平台多工層（Ch 21 講過在 Linux 上 `#include "ae_epoll.c"`，底下是 `epoll_wait`）。

注意 `fe->rfileProc(...)` / `fe->wfileProc(...)`——正是 Ch 23 的 callback indirection。redis 的每個 client socket 都把 `readQueryFromClient` 註冊成 `rfileProc`，所以「client 送資料進來」這件事，就是 `aeApiPoll` 回報該 fd 可讀 → `aeProcessEvents` 這個 for 迴圈呼叫 `fe->rfileProc` → 跳進 `readQueryFromClient` → 解析 → 命令分派（Ch 23 實戰二那條 `bt` 的上游）。**event loop 與 command dispatch 在這裡接起來了。**

那個 `invert` / `AE_BARRIER` 的細節值得一提：正常先讀後寫（讀進 query 後可能馬上就能回 reply，省一輪），但某些情況（如 AOF fsync 後才回覆）要反過來先寫後讀。**讀 event loop 要留意這種「事件順序被刻意調換」的地方**——它往往藏著正確性的關鍵（這裡是持久化的 durability 保證）。

## 第三步：找 state 變數與 transition——`syncWithMaster` 這個明寫 FSM

event loop 讓一件連續的事被切成碎片，那「進度」存哪？存 state 變數。redis replica 端跟 master 的握手是最清楚的明寫狀態機（explicit FSM）。

**先找 state 變數與它的取值**（`server.h:430` 那個 enum，真實節錄）：

```c
typedef enum {
    REPL_STATE_NONE = 0,            /* 沒在複製 */
    REPL_STATE_CONNECT,             /* 該去連 master 了 */
    REPL_STATE_CONNECTING,          /* 連線中 */
    REPL_STATE_RECEIVE_PING_REPLY,  /* 等 PING 回覆 */
    REPL_STATE_RECEIVE_AUTH_REPLY,  /* 等 AUTH 回覆 */
    REPL_STATE_RECEIVE_PORT_REPLY,  /* 等 REPLCONF 回覆 */
    ...
    REPL_STATE_RECEIVE_PSYNC_REPLY, /* 等 PSYNC 回覆 */
    REPL_STATE_TRANSFER,            /* 正在收 RDB */
    REPL_STATE_CONNECTED,           /* 完成，進入正常複製 */
} ...;
```

**光讀這個 enum，整個握手協議的步驟就攤開了**：連線 → ping → auth → 報 port/ip/capa → psync → 傳 RDB → 完成。這是讀狀態機的第一個大禮：**state 的列舉本身就是流程文件**。存這個值的欄位是 `server.repl_state`。

**再找 transition（誰改 state）**。用 rg 一把抓所有寫入點：

```
$ rg -n 'server\.repl_state = REPL_STATE_' src/replication.c | head
replication.c:2635:    server.repl_state = REPL_STATE_RECEIVE_PING_REPLY;
replication.c:2669:    server.repl_state = REPL_STATE_SEND_HANDSHAKE;
replication.c:2726:    server.repl_state = REPL_STATE_RECEIVE_AUTH_REPLY;
replication.c:2759:    server.repl_state = REPL_STATE_RECEIVE_IP_REPLY;
replication.c:2793:    server.repl_state = REPL_STATE_SEND_PSYNC;
replication.c:2807:    server.repl_state = REPL_STATE_RECEIVE_PSYNC_REPLY;
replication.c:2891:    server.repl_state = REPL_STATE_TRANSFER;
...
```

**這串賦值就是狀態轉移的「邊」**。每一行是「在某狀態做完一步後，把 state 推進到下一步」。而驅動它的引擎是 `syncWithMaster()`（`replication.c:2608`），它的結構是一串 `if (state == X) { 做 X 這步; state = 下一步; }`（真實節錄骨架）：

```c
void syncWithMaster(connection *conn) {
    ...
    if (server.repl_state == REPL_STATE_CONNECTING) {
        ...
        err = sendCommand(conn,"PING",NULL);
        server.repl_state = REPL_STATE_RECEIVE_PING_REPLY;
        return;                                    // ← 做完一步就返回，等下次可讀
    }
    if (server.repl_state == REPL_STATE_RECEIVE_PING_REPLY) {
        err = receiveSynchronousResponse(conn);    // 讀 PING 的回覆
        ...
        server.repl_state = REPL_STATE_SEND_HANDSHAKE;
        // fall through 或 return，進下一步
    }
    if (server.repl_state == REPL_STATE_RECEIVE_AUTH_REPLY) { ... }
    ...
}
```

## 關鍵：這個 FSM 是被 event loop「重複呼叫」推進的

這是事件驅動狀態機最反直覺、也最該讀懂的一點。`syncWithMaster` 不是用一個 `while` 迴圈一口氣跑完所有狀態——它每次只做**一步**就 `return`，然後把自己重新註冊成該連線的 read handler（真實節錄 `replication.c:2633`）：

```c
    connSetReadHandler(conn, syncWithMaster);   // 把自己註冊回去，等 master 回覆讓 socket 可讀
```

於是流程是：

```
event loop 某輪  → conn 可讀 → 呼叫 syncWithMaster(conn)
                                  → 看 repl_state == CONNECTING
                                  → 送 PING，state = RECEIVE_PING_REPLY，return
（回到 event loop，處理別的 fd……master 回了 PONG，conn 又可讀）
event loop 某輪  → conn 可讀 → 又呼叫 syncWithMaster(conn)
                                  → 看 repl_state == RECEIVE_PING_REPLY
                                  → 讀 PONG，state = SEND_HANDSHAKE，繼續……
```

**同一個函式被 event loop 呼叫很多次，每次靠 `repl_state` 決定「這次該做哪一步」。** 呼叫堆疊每次都是全新的（`syncWithMaster` ← event loop），進度完全靠 `server.repl_state` 這個外部變數記著。這就是為什麼——

**讀這種 code 千萬別想「從函式頂讀到底跟著跑一遍」**。它一次只執行一個 `if` 分支就返回。正確讀法是：把 `syncWithMaster` 當成「一張狀態轉移表的實作」，每個 `if (state==X)` 是表的一列，讀的是「X 狀態下做什麼、跳去哪」，而不是順序執行。redis 作者自己都在 `replication.c:2813` 寫了 `"syncWithMaster(): state machine error"`——它明確就是個狀態機。

## 第四步：畫狀態轉移圖——外化才算讀懂

狀態機讀到這裡，腦中已經暈了。**唯一的解藥是畫圖**（呼應 Ch 35「外化理解」）。把上面 rg 抓到的 transition 整理成 (現態 → 次態)，畫成 ASCII（基於真讀 `replication.c` 的 `syncWithMaster` + enum 繪製）：

```
                  觸發：connectWithMaster() 建立非阻塞連線
  REPL_STATE_CONNECT ──────────────────────────────► REPL_STATE_CONNECTING
                                                            │ conn 建立 (socket 可寫)
                                                            │ 送 PING
                                                            ▼
                                              REPL_STATE_RECEIVE_PING_REPLY
                                                            │ 收到 PONG，送 AUTH(可選)
                                                            ▼
                                              REPL_STATE_RECEIVE_AUTH_REPLY
                                                            │ 送 REPLCONF listening-port
                                                            ▼
                                              REPL_STATE_RECEIVE_PORT_REPLY
                                                            │ 送 REPLCONF ip-address
                                                            ▼
                                              REPL_STATE_RECEIVE_IP_REPLY
                                                            │ 送 REPLCONF capa
                                                            ▼
                                              REPL_STATE_RECEIVE_CAPA_REPLY
                                                            │ 送 PSYNC
                                                            ▼
                                              REPL_STATE_RECEIVE_PSYNC_REPLY
                                                            │ master 同意 FULLRESYNC
                                                            ▼
                                                REPL_STATE_TRANSFER ──收完 RDB──► REPL_STATE_CONNECTED
                                                     │                                 （進入正常增量複製）
     任一步 goto error / 連線斷 ────────────────────┴──────────────────────► REPL_STATE_CONNECT（重來）
```

**這張圖就是讀懂的證明**。有了它，你回頭看 `syncWithMaster` 那一長串 `if`，每個分支對應圖上一條邊，瞬間有結構。任何事件驅動狀態機——TCP 狀態機、TLS 握手、cluster gossip、client 讀寫狀態——都用這四步拆，畫出圖你才真的懂「它現在可能在哪、下一步能去哪、什麼事件觸發轉移」。

## 明寫 FSM vs 隱含狀態機

redis 的 `repl_state` 是**明寫**狀態機：有具名 enum、有集中的 state 變數、transition 是顯眼的 `state = X`。這是最好讀的一種。但很多狀態機是**隱含**的，沒有 state 變數，狀態藏在「哪些欄位有沒有被設」「當前註冊的是哪個 callback」裡：

```
明寫 FSM（好讀）                    隱含狀態機（難讀）
──────────────────────            ────────────────────────────
enum { S_A, S_B, S_C };           （沒有 enum）
x->state = S_B;                   x->buf 有沒有值？x->handler 是誰？
switch (x->state) {...}           if (x->flags & FLAG_X) ...  散落各處
```

redis 也有隱含的：client 的讀寫「狀態」一部分靠 `client->flags`（一堆 bit：`CLIENT_BLOCKED`、`CLIENT_MULTI`、`CLIENT_CLOSE_AFTER_REPLY`...）表達，一部分靠「當前 read/write handler 註冊的是哪個函式」表達（握手期是 `syncWithMaster`，正常期換成 `readQueryFromClient`——callback 換手本身就是換狀態）。

**讀隱含狀態機的招**：找那些「被到處檢查、也被到處設定」的 flag 欄位（`rg 'c->flags & CLIENT_'` 看它被讀在哪、`rg 'c->flags \|= CLIENT_'` 看被設在哪），把 flag 組合當成隱含 state。以及注意「callback 被換掉」的點（`connSetReadHandler` 換手）——換 handler 等於換狀態。這比明寫 FSM 難，但方法論一樣：找 state（這裡是 flag/handler）→ 找 transition（設 flag / 換 handler 的點）→ 畫圖。

## 對比與取捨

| 讀事件驅動/狀態機的手段 | 給你什麼 | 限制 |
|---|---|---|
| 找 `while` 主迴圈 | 唯一的直線骨架，確認 IoC 結構 | 只給骨架，業務邏輯在 callback 裡 |
| 讀 state enum | 流程的全部步驟（免費的文件） | 僅限明寫 FSM；隱含狀態機沒有 |
| `rg 'state = '` 抓 transition | 所有狀態轉移的邊 | 隱含狀態機要改抓 flag/handler 賦值 |
| 畫狀態轉移圖 | 把碎片組回結構，真正的理解 | 手工、費時；但無可取代 |
| gdb 斷點看 state 值 | 「這一刻實際在哪個狀態」+ 觸發事件 | 只看到你觸發到的路徑 |
| 讀 callback 註冊點（Ch 23） | 哪個事件觸發哪段邏輯 | callback 執行期會換手，要動態確認 |

**策略**：先鎖定主迴圈（骨架）→ 對每個要追的子系統找它的 state 變數（明寫看 enum、隱含看 flag/handler）→ rg 抓 transition → 畫圖。要確認「線上這台現在卡在哪個狀態」，gdb `print server.repl_state`（或看 `INFO replication` 的 `master_link_status`，redis 把 state 對外暴露成人話）。

## 踩雷集錦

1. **錯誤直覺：「`syncWithMaster` 從頭讀到尾就是它跑一遍的流程」。** 正確認識：它一次只執行一個 `if (state==X)` 分支就 `return`，靠 event loop 反覆呼叫、靠 `repl_state` 記進度。**把它讀成「狀態轉移表」，不是「順序腳本」。** 這是讀事件驅動 FSM 最大的認知陷阱。

2. **錯誤直覺：「event loop 的 code 我找 `main` 順著讀就懂」。** 正確認識：`main` 只是把 fd 註冊好、callback 掛上、然後 `aeMain` 就進了 `while` 迴圈——之後的控制流全在「哪個 fd 就緒 → 跳哪個 callback」，原始碼順序毫無意義。**找到 `while` 迴圈與 poll 呼叫，才是入口。**

3. **錯誤直覺：「這個 handler 函式只會被呼叫一次」。** 正確認識：event loop 的 callback 天生會被呼叫無數次（每次 fd 就緒一次）。狀態機 handler 更是每步一次。**看到 callback 別假設它跑一遍就結束**，要問「它每次被呼叫做什麼、靠什麼記住上次做到哪」。

4. **錯誤直覺：「沒有 `state` 變數 = 這段沒有狀態機」。** 正確認識：隱含狀態機把狀態藏在 flag bit、buffer 有無、當前註冊的 callback 裡。redis client 的行為就靠 `c->flags` 一堆 bit + handler 換手驅動。**找不到 enum，就去找被反覆讀寫的 flag 欄位與 `Set*Handler` 換手點。**

5. **錯誤直覺：「event loop 的事件順序無所謂，都會處理到」。** 正確認識：順序常常是正確性的關鍵。redis 的 `AE_BARRIER`（先寫後讀，用於 fsync-then-reply 的 durability）就是刻意調換順序。`beforesleep`/`aftersleep` hook 在 poll 前後做的事（送 reply、fsync AOF）也對正確性至關重要。**讀到「刻意安排的事件順序」要停下來想它保證了什麼。**

## 進階：再往深一層

- **time event 與 `serverCron`——被 event loop 驅動的「背景工作」**：`aeProcessEvents` 尾端的 `processTimeEvents` 處理計時器事件，redis 最重要的一個是 `serverCron`（預設每 100ms 一次），負責過期 key 抽樣刪除、client 逾時、rehash 推進、統計等。**這是「沒有專屬執行緒的背景任務」怎麼實現的**：不是開 thread，是掛一個週期性 time event 讓 event loop 每輪順便跑。讀到「某件事定期發生但找不到跑它的 thread」，去 event loop 的 time event 清單找。

- **狀態機的「錯誤/超時邊」最容易被漏讀**：讀 FSM 時人們盯著 happy path（一步步成功推進），但真正的複雜度在錯誤轉移——`syncWithMaster` 裡滿地的 `goto error` / `goto write_error` 全部把 state 重設回 `REPL_STATE_CONNECT`（重來）。**畫圖時務必補上「任一步失敗 → 回到哪」的邊**，否則你對這個狀態機的理解是殘缺的（也是漏洞常藏的地方，接 Ch 32）。

- **多個狀態機交織**：真實系統常有好幾台狀態機同時跑並互相影響。redis 一條 replica 連線同時牽動 `server.repl_state`（複製握手）、該 client 的 `c->flags`（讀寫狀態）、可能還有 cluster 的 gossip 狀態。**讀的時候一次只追一台狀態機、畫一張圖**，追完再看它們在哪些點互相觸發（如握手完成 `REPL_STATE_CONNECTED` 會改變 client 的 flag、換掉 read handler）。別想一次把所有狀態塞進一張圖。

- **async 狀態機 = 手寫的 coroutine**：`syncWithMaster` 這種「做一步、存狀態、返回、被重新喚醒」的模式，本質是在沒有語言級 async/await 的 C 裡**手寫協程**——`repl_state` 就是被外顯出來的「協程執行到哪一行」。理解這點，你讀別的語言的 async（Rust 的 `Future`、C++ coroutine、JS 的 event loop）會發現是同一件事的不同包裝：狀態被編譯器藏進 state machine，而 C 是作者手動維護那個 state。這條連結讓「讀事件驅動」變成跨語言可遷移的技能（接 Ch 29）。

## 動手練習

1. **驗證主迴圈**：用 cscope 或 `rg -n 'aeMain\(' src/`，確認 `aeMain(server.el)` 在 `server.c` 被 `main` 呼叫；讀 `aeMain` 5 行，說出「redis 一生就是這個 while」。
2. **畫 client 讀路徑**：從 `aeProcessEvents` 的 `fe->rfileProc(...)` 出發，追到 client socket 的 read handler `readQueryFromClient`，畫出「event loop → poll → fd 可讀 → readQueryFromClient → 命令分派」一張流程圖。
3. **重畫複製 FSM**：用 `rg 'server\.repl_state = ' src/replication.c` 抓全部 transition，自己重畫一次狀態轉移圖，**特別補上所有 `goto error` 指向的重來邊**。
4. **找隱含狀態**：`rg 'c->flags \|= CLIENT_' src/ | head -30` 列出 client flag 的設定點，挑 `CLIENT_BLOCKED`，找出它在哪被設、在哪被檢查、在哪被清，說明「阻塞中的 client」這個隱含狀態怎麼運作。
5. **（選）gdb 看 state**：讓 redis 當某個 master 的 replica（`replicaof`），gdb 在 `syncWithMaster` 斷點，每次命中 `print server.repl_state`，親眼看 state 一步步推進，對照你畫的圖。

## 本章重點整理

- 事件驅動 code 沒有直線控制流：控制反轉（event loop 回呼你）、狀態外顯（進度存 state 變數不存呼叫堆疊）、時間上不相鄰（一件事被切成多次回呼）。
- 固定四步：**找 event loop 骨架（`while`+poll）→ 找 state 變數（明寫看 enum、隱含看 flag/handler）→ 找 transition（`state =` / 換 handler 的點）→ 畫狀態轉移圖**。
- redis 實證：`aeMain` 5 行是心臟；`aeProcessEvents` 是 reactor 一輪（beforesleep → poll → 分派 read/write callback → time events）；`syncWithMaster` 是明寫 FSM，靠 event loop 反覆呼叫 + `repl_state` 推進。
- 最大認知陷阱：狀態機 handler 一次只做一步就返回，別讀成順序腳本，要讀成「狀態轉移表」。
- 隱含狀態機（redis `client->flags` + callback 換手）方法論相同；錯誤/超時轉移最易漏讀，畫圖務必補上；async 狀態機本質是手寫協程，跨語言可遷移。

## 自我檢核

- [ ] 拿到一個事件驅動程式，我能不能在 2 分鐘內找到那個 `while`+poll 主迴圈、指出「主流程就是它」？
- [ ] `syncWithMaster` 為什麼不能「從頭讀到尾當成它跑一遍的流程」？它靠什麼記住做到哪一步？
- [ ] 我能不看筆記，用四步（骨架/state/transition/畫圖）拆解 redis 的複製握手狀態機嗎？
- [ ] 遇到沒有 `state` enum 的 code，我怎麼判斷它其實是個隱含狀態機、狀態藏在哪？
- [ ] redis 的 `serverCron` 定期跑，但沒有專屬 thread——它靠什麼機制被驅動？

## 延伸閱讀

- **[Redis 原始碼：`src/ae.c`（`aeMain` / `aeProcessEvents` / `processTimeEvents`）](https://github.com/redis/redis/blob/7.4.0/src/ae.c)**
  - **讀哪裡**：`aeMain`、`aeProcessEvents` 整個、`processTimeEvents`。配 `ae_epoll.c` 看 `aeApiPoll` 底層。
  - **學到什麼**：一個乾淨、可讀的 reactor pattern 完整實現——本章骨架的原始出處。redis 的 ae 常被當作「最好讀的 event loop 教材」。
  - **前提**：懂 `epoll`/`select` 的基本概念、Ch 23 的 callback indirection。

- **[Redis 原始碼：`src/replication.c` 的 `syncWithMaster`](https://github.com/redis/redis/blob/7.4.0/src/replication.c)**
  - **讀哪裡**：`syncWithMaster()` 整個函式，配 `server.h` 的 `REPL_STATE_*` enum。
  - **學到什麼**：一個生產級、event-loop 驅動的明寫狀態機，含完整錯誤轉移（`goto error`）——本章第三四步的原始出處。
  - **前提**：本章看完；懂 replica 握手大致要做哪些事（連線/認證/psync）。

- **[《Pattern-Oriented Software Architecture, Vol.2》— Reactor pattern 章節](https://www.dre.vanderbilt.edu/~schmidt/POSA/POSA2/)**
  - **讀哪裡**：Reactor 與 Proactor 兩個 pattern 的意圖、結構、後果。
  - **學到什麼**：reactor（redis/nginx/libevent 都用）與 proactor（IOCP、io_uring 風格）的本質差異——理解你讀的 event loop 屬於哪一種、為什麼那樣設計。
  - **前提**：讀過本章、看過 redis ae 之後再讀最有感。

- **[Bob Nystrom, "Game Programming Patterns" — State 與 Event Queue 章](https://gameprogrammingpatterns.com/state.html)**
  - **讀哪裡**："State"（明寫 FSM vs 用類別/函式指標實現）與 "Event Queue" 兩章，線上免費。
  - **學到什麼**：把狀態機講到直覺見底的一份材料——明寫 state、隱含 state、狀態機退化成 event queue 的關係。跨領域（遊戲）但方法論直接可搬到讀系統程式。
  - **前提**：無；當作狀態機的直覺補強。

到這裡，你已經能拆解 build、巨集、indirection、狀態機這四種讓靜態閱讀失效的結構。還剩一種硬骨頭：多執行緒/並發——控制流不只非線性，還同時有好幾條在跑、彼此靠鎖與記憶體序互動。下一章我們處理並發程式怎麼讀。

→ [Ch 25 讀懂並發程式](./25-reading-concurrency.md)
