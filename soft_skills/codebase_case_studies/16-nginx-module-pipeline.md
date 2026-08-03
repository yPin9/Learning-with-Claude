# Ch 16 — module / handler pipeline

> **目標**：搞懂 nginx 怎麼把「一個 HTTP 請求該經過哪些處理」組織成一條**可插拔的 pipeline**。拆三樣東西：`ngx_module_t`（module 的統一介面）、11 個 HTTP **phase**（請求處理的固定階段序列）、以及 **output filter chain**（回應在一串 filter 上流過去）。這是 plugin 架構的教科書範本——一個核心定義固定的「掛勾點」，功能全部做成外掛掛上去。

> **目標codebase**：nginx `release-1.26.2`（commit `37fe983`）

## 為什麼需要這個？

nginx 的功能清單長得嚇人：static 檔案、反向代理、gzip、SSL、快取、rewrite、access 控制、limit_req……但 nginx 的**核心**（core + event + http 框架）並不知道這些功能存在。它只定義了一套「請求處理有哪幾個階段、回應要流過哪串 filter」的骨架，**所有具體功能都是 module 掛在這個骨架上**。

這解決一個真實的架構難題：**怎麼讓一個系統可擴充，又不讓每加一個功能就得改核心？** 答案是 plugin 架構——核心定義**掛勾點（hook）** 和**統一介面**，功能實作成符合介面的 module，在啟動時把自己註冊到對的掛勾點。核心的 code 從此不用動，加功能 = 加 module。

你會在無數地方遇到這個 pattern：Linux kernel 的 module、Apache 的 module、VS Code 的 extension、webpack 的 plugin、Express 的 middleware。**nginx 的 phase pipeline 是這個 pattern 在 C 裡最清爽的一個實作**，Ch 17 會把它收斂成可遷移的卡片。

## 先建立直覺：一條有固定站台的流水線

在讀 code 前，先建這張圖。一個 HTTP 請求進來，nginx 讓它走過一條**站台順序固定**的流水線：

```
   一個 HTTP request 走過的 phase 流水線（順序固定）：

   ┌──────────────────┐
   │ POST_READ        │  剛讀完 header（realip 之類在這動）
   ├──────────────────┤
   │ SERVER_REWRITE   │  server 層級 rewrite
   ├──────────────────┤
   │ FIND_CONFIG      │  ★ 比對 location，決定用哪套設定（核心固定，不可插）
   ├──────────────────┤
   │ REWRITE          │  location 層級 rewrite
   ├──────────────────┤
   │ POST_REWRITE     │  rewrite 後處理（核心固定）
   ├──────────────────┤
   │ PREACCESS        │  limit_req、limit_conn 在這
   ├──────────────────┤
   │ ACCESS           │  access 控制（allow/deny、auth）
   ├──────────────────┤
   │ POST_ACCESS      │  satisfy 邏輯（核心固定）
   ├──────────────────┤
   │ PRECONTENT       │  try_files、mirror 在這
   ├──────────────────┤
   │ CONTENT          │  ★★ 產生回應：static / proxy / fastcgi 等 content handler
   ├──────────────────┤
   │ LOG              │  記 access log
   └──────────────────┘
            │
            ▼  回應（一條 buffer chain）接著往下流過 output filter chain：
   [gzip filter] → [chunked filter] → ... → [header filter] → [write filter → socket]
```

三個要點先記住：

1. **phase 的順序是核心寫死的**（就是 Ch 15 沒細講的那個 enum）。module 不能改順序，只能**往某個 phase 掛自己的 handler**。這是 plugin 架構的精髓：**核心定義框架，plugin 只填內容**。
2. **有幾個 phase 是核心專用的**（FIND_CONFIG、POST_REWRITE、POST_ACCESS），module 掛不進去——它們是流水線的固定機構。
3. **回應的處理是另一條鏈**：content phase 產出回應後，資料（Ch 15 的 buffer chain）流過一串 **output filter**，每個 filter 加工一下（壓縮、分塊、加 header），最後由 write filter 送進 socket。

## 核心一：`ngx_module_t`——所有 module 的統一介面

先看「module」到底是什麼。所有 module——不管是 event module（epoll）、core module、還是 http module（static/proxy）——都是同一個型別 `ngx_module_t`（真跑 `sed -n '/^struct ngx_module_s {/,/};/p' src/core/ngx_module.h`，節選）：

```c
// src/core/ngx_module.h（1.26.2，節選）
struct ngx_module_s {
    ngx_uint_t            ctx_index;
    ngx_uint_t            index;
    char                 *name;
    ...
    void                 *ctx;         // ★ 型別相依的 context（http module 指向 ngx_http_module_t）
    ngx_command_t        *commands;    // ★ 這個 module 認得的設定指令
    ngx_uint_t            type;        // ★ NGX_HTTP_MODULE / NGX_EVENT_MODULE / NGX_CORE_MODULE

    ngx_int_t           (*init_master)(ngx_log_t *log);
    ngx_int_t           (*init_module)(ngx_cycle_t *cycle);
    ngx_int_t           (*init_process)(ngx_cycle_t *cycle);   // 生命週期掛勾
    ...
    void                (*exit_master)(ngx_cycle_t *cycle);
    ...
};
```

三個關鍵欄位：

- **`type`**：module 的種類，值很有意思——`NGX_HTTP_MODULE` 是 `0x50545448`，也就是 ASCII 的 `"HTTP"`；`NGX_EVENT_MODULE` 是 `"EVNT"`、`NGX_CORE_MODULE` 是 `"CORE"`（真跑 `rg -n "define NGX_HTTP_MODULE" src/http/ngx_http_config.h` 看那句 `/* "HTTP" */` 註解）。用可讀字串的 magic number 當型別標籤，是 nginx 的小慣例。
- **`ctx`**：`void *`，指向**型別相依的介面**。對 http module，它指向 `ngx_http_module_t`（下面看）；對 event module，指向 `ngx_event_module_t`。**這是 nginx 做「泛型」的手法：`ngx_module_t` 是所有 module 的共同外殼，`ctx` 塞各類 module 特有的一組回呼。**
- **`commands`**：這個 module 認得哪些 `nginx.conf` 指令（如 `gzip on`）。設定解析時，nginx 掃所有 module 的 `commands` 找誰認得這個指令。

看一個真實 module 的定義——最小的 static module（真跑 `sed -n '32,45p' src/http/modules/ngx_http_static_module.c`）：

```c
// src/http/modules/ngx_http_static_module.c:32（1.26.2）
ngx_module_t  ngx_http_static_module = {
    NGX_MODULE_V1,                         // 展開成 index/version/signature 等樣板欄位
    &ngx_http_static_module_ctx,           // ★ ctx：指向 http module 的介面
    NULL,                                  // module directives（static 沒有自己的指令）
    NGX_HTTP_MODULE,                       // ★ type
    NULL, NULL, NULL, NULL,                // init master/module/process/thread（都不用）
    NULL, NULL, NULL,
    NGX_MODULE_V1_PADDING
};
```

`NGX_MODULE_V1` 是個巨集，幫你填掉一堆固定的樣板欄位（真跑 `rg -n -A2 "define NGX_MODULE_V1 " src/core/ngx_module.h`：`NGX_MODULE_UNSET_INDEX, ..., nginx_version, NGX_MODULE_SIGNATURE`）。**讀 module 定義時，你的眼睛要跳過 `NGX_MODULE_V1`/`NGX_MODULE_V1_PADDING`（樣板），聚焦在 `ctx`、`commands`、`type` 這幾格**——它們才是這個 module 的個性。

`ctx` 指向的 `ngx_http_module_t` 是 http module 真正的介面（真跑 `sed -n '/init_main_conf.*conf);/,/} ngx_http_module_t;/p'`，或直接看它）：

```c
// src/http/ngx_http_config.h（1.26.2）
typedef struct {
    ngx_int_t   (*preconfiguration)(ngx_conf_t *cf);
    ngx_int_t   (*postconfiguration)(ngx_conf_t *cf);   // ★ module 在這裡註冊自己的 handler
    void       *(*create_main_conf)(ngx_conf_t *cf);
    char       *(*init_main_conf)(ngx_conf_t *cf, void *conf);
    void       *(*create_srv_conf)(ngx_conf_t *cf);
    char       *(*merge_srv_conf)(ngx_conf_t *cf, void *prev, void *conf);
    void       *(*create_loc_conf)(ngx_conf_t *cf);
    char       *(*merge_loc_conf)(ngx_conf_t *cf, void *prev, void *conf);
} ngx_http_module_t;
```

一組回呼，nginx 在不同時機呼叫：解析設定前後（`pre/postconfiguration`）、建立各層級設定（`create_*_conf`）、合併設定（`merge_*_conf`）。**這就是「統一介面」——nginx 不需要知道 static 或 proxy 具體做什麼，它只需要知道「每個 http module 都有這 8 個 hook」，在對的時機一個一個叫過去。**

## 核心二：11 個 phase——請求處理的固定階段

phase 的順序定義在一個 enum（Ch 15 埋的伏筆，真跑 `sed -n '/typedef enum {/,/} ngx_http_phases;/p' src/http/ngx_http_core_module.h`）：

```c
// src/http/ngx_http_core_module.h（1.26.2）
typedef enum {
    NGX_HTTP_POST_READ_PHASE = 0,
    NGX_HTTP_SERVER_REWRITE_PHASE,
    NGX_HTTP_FIND_CONFIG_PHASE,
    NGX_HTTP_REWRITE_PHASE,
    NGX_HTTP_POST_REWRITE_PHASE,
    NGX_HTTP_PREACCESS_PHASE,
    NGX_HTTP_ACCESS_PHASE,
    NGX_HTTP_POST_ACCESS_PHASE,
    NGX_HTTP_PRECONTENT_PHASE,
    NGX_HTTP_CONTENT_PHASE,
    NGX_HTTP_LOG_PHASE
} ngx_http_phases;
```

**11 個 phase，順序即這個 enum 的宣告順序。** 這個 enum 是整個 HTTP 處理骨架的定義——每個請求就是按這 11 站依序走。

module 怎麼把自己掛到某個 phase？在它的 `postconfiguration` 回呼裡，往對應 phase 的 handler 陣列 `push` 一個函式指標。看 static module 怎麼把自己掛到 CONTENT phase（真跑 `sed -n '282,300p' src/http/modules/ngx_http_static_module.c`）：

```c
// src/http/modules/ngx_http_static_module.c:282（1.26.2）
static ngx_int_t
ngx_http_static_init(ngx_conf_t *cf)                    // 這就是 postconfiguration
{
    ngx_http_handler_pt        *h;
    ngx_http_core_main_conf_t  *cmcf;

    cmcf = ngx_http_conf_get_module_main_conf(cf, ngx_http_core_module);

    h = ngx_array_push(&cmcf->phases[NGX_HTTP_CONTENT_PHASE].handlers);   // ★ 掛到 CONTENT phase
    if (h == NULL) {
        return NGX_ERROR;
    }
    *h = ngx_http_static_handler;                        // ★ 把自己的 handler 填進去
    return NGX_OK;
}
```

**這 5 行是 plugin 註冊的全部：拿到核心的 `phases[NGX_HTTP_CONTENT_PHASE].handlers` 陣列，push 自己的 handler 進去。** static、autoindex、gzip_static 都是這樣把自己掛到 CONTENT phase，nginx 跑到 CONTENT phase 時會依序試這些 handler，直到有一個「認領」這個請求（回傳非 `NGX_DECLINED`）。

啟動時，`ngx_http_init_phase_handlers`（`src/http/ngx_http.c`）把各 phase 陣列裡的 handler 攤平成一個線性的 `phase_engine.handlers` 陣列，每格是一個 `ngx_http_phase_handler_t`（真跑 `sed -n '/^struct ngx_http_phase_handler_s {/,/};/p' src/http/ngx_http_core_module.h`）：

```c
// src/http/ngx_http_core_module.h（1.26.2）
struct ngx_http_phase_handler_s {
    ngx_http_phase_handler_pt  checker;   // ★ 這個 phase 的「檢查器」（怎麼跑這格）
    ngx_http_handler_pt        handler;   // module 掛上來的實際 handler
    ngx_uint_t                 next;      // 這個 phase 結束後跳到哪一格（跳過同 phase 其餘）
};
```

注意 `checker` 和 `handler` 是**兩層**：`handler` 是 module 提供的做事函式，`checker` 是核心提供的「怎麼在這個 phase 裡跑 handler、根據回傳值決定下一步」的驅動器。不同 phase 有不同 checker（generic/rewrite/access/content...），因為不同 phase 對 handler 回傳值的處理邏輯不同。

## 底層機制：`ngx_http_core_run_phases`——引擎怎麼跑

現在看驅動整條 pipeline 的引擎（真跑 `sed -n '/^ngx_http_core_run_phases/,/^}/p' src/http/ngx_http_core_module.c`）：

```c
// src/http/ngx_http_core_module.c:863（1.26.2）
void
ngx_http_core_run_phases(ngx_http_request_t *r)
{
    ngx_int_t                   rc;
    ngx_http_phase_handler_t   *ph;
    ngx_http_core_main_conf_t  *cmcf;

    cmcf = ngx_http_get_module_main_conf(r, ngx_http_core_module);
    ph = cmcf->phase_engine.handlers;                 // 攤平後的 handler 陣列

    while (ph[r->phase_handler].checker) {            // ★ 沿陣列一格一格走
        rc = ph[r->phase_handler].checker(r, &ph[r->phase_handler]);  // ★★ 叫這格的 checker
        if (rc == NGX_OK) {
            return;                                    // checker 說「先停」（等 I/O 等）
        }
    }
}
```

**整個 HTTP 請求的處理引擎就這麼小。** 一個 `while` 迴圈，沿 `phase_engine.handlers` 陣列走，對每格叫它的 `checker`。`r->phase_handler` 是「現在走到第幾格」的游標，checker 會推進它（`r->phase_handler++`）或跳格（用 `ph->next`）。

**這裡藏著一個 event-driven 的關鍵細節**：`checker` 回傳 `NGX_OK` 時 `run_phases` **直接 return**——不是處理完了，而是「這格要等 I/O（例如 proxy 要等 upstream 回應），先讓出 worker 執行緒」。等 I/O 就緒，Ch 14 的 event loop 會再呼叫 `r->write_event_handler`（它被設成 `ngx_http_core_run_phases`），從 `r->phase_handler` 這個游標**接著上次的位置繼續跑**。**請求的處理進度存在 `r->phase_handler` 這個 struct 欄位上，不在 stack 上**——這正是 Ch 14 說的 callback-driven state machine，在 phase 層級的體現。

看 CONTENT phase 的 checker（handler pipeline 的高潮，真跑 `sed -n '1252,1300p' src/http/ngx_http_core_module.c`，節選）：

```c
// src/http/ngx_http_core_module.c:1252（1.26.2，節選）
ngx_int_t
ngx_http_core_content_phase(ngx_http_request_t *r, ngx_http_phase_handler_t *ph)
{
    ...
    if (r->content_handler) {                          // location 綁了專屬 content handler？
        r->write_event_handler = ngx_http_request_empty_handler;
        ngx_http_finalize_request(r, r->content_handler(r));   // 直接叫它（如 proxy）
        return NGX_OK;
    }
    ...
    rc = ph->handler(r);                               // ★ 否則試 phase 上掛的 handler

    if (rc != NGX_DECLINED) {                          // 有人認領（非 DECLINED）
        ngx_http_finalize_request(r, rc);              // 收尾
        return NGX_OK;
    }

    /* rc == NGX_DECLINED */                           // 這個 handler 不認領
    ph++;                                              // 試下一個 CONTENT handler
    if (ph->checker) {
        r->phase_handler++;
        return NGX_AGAIN;                              // 繼續 while 迴圈
    }
    ...
    ngx_http_finalize_request(r, NGX_HTTP_NOT_FOUND);  // 沒人認領 → 404
    return NGX_OK;
}
```

**`NGX_DECLINED` 是這裡的靈魂**：一個 content handler 回 `NGX_DECLINED` 表示「這請求不歸我管，換下一個」。static handler 看到 URI 以 `/` 結尾就 `return NGX_DECLINED`（真跑看 `ngx_http_static_handler` 開頭 `if (r->uri.data[r->uri.len - 1] == '/') return NGX_DECLINED;`），把機會讓給 autoindex。這是 **chain of responsibility** pattern：一串 handler 依序試，各自決定認領或放行。

### `ngx_http_request_t`：狀態機的載體

phase 引擎能「暫停續跑」，靠的是把狀態存在 `ngx_http_request_t` 這個大 struct 上。看它幾個關鍵欄位（真跑 `sed -n '/^struct ngx_http_request_s {/,/};/p' src/http/ngx_http_request.h`，節選）：

```c
// src/http/ngx_http_request.h（1.26.2，節選）
struct ngx_http_request_s {
    ngx_connection_t          *connection;         // 這個請求的連線
    ngx_http_event_handler_pt  read_event_handler;  // ★ 讀就緒時該跑什麼
    ngx_http_event_handler_pt  write_event_handler; // ★ 寫就緒時該跑什麼
    ngx_pool_t                *pool;                // ★ 這個請求的 memory pool（Ch 15）
    ngx_http_headers_in_t      headers_in;          // 解析出的請求 header
    ngx_http_headers_out_t     headers_out;         // 要送回的回應 header
    ngx_str_t                  uri;
    ngx_int_t                  phase_handler;       // ★★ phase 引擎的游標（跑到第幾格）
    ngx_http_handler_pt        content_handler;     // location 綁的專屬 content handler
    ...
};
```

**一個請求的全部狀態都在這裡**：`phase_handler`（走到哪個 phase）、`read/write_event_handler`（下次 I/O 就緒時跳哪）、`headers_in/out`（解析和產出的 header）、`pool`（配置都從這切）。**這就是 Ch 17 卡片五（callback state machine）的實體**——nginx 不用執行緒 stack 保存「請求處理到哪」，全存在這個 struct，所以能被 I/O 打斷任意多次再續跑。讀 event-driven 服務時，找出「這個又大又雜的 struct」就是找到了狀態機的載體；`phase_handler` 這種「游標」欄位是它的 beacon。

## 核心三：output filter chain——回應流過的鏈

content handler 產出回應（一條 Ch 15 的 buffer chain）後，資料要流過一串 **output filter**。看它怎麼串起來——這裡有個很妙的手法（真跑 `rg -n "ngx_http_top_body_filter = \|ngx_http_next_body_filter = ngx_http_top_body_filter" src/http/*.c src/http/modules/ngx_http_gzip_filter_module.c`）：

```
src/http/ngx_http_write_filter_module.c:368:  ngx_http_top_body_filter = ngx_http_write_filter;
src/http/ngx_http_copy_filter_module.c:392:   ngx_http_next_body_filter = ngx_http_top_body_filter;
src/http/ngx_http_copy_filter_module.c:393:   ngx_http_top_body_filter = ngx_http_copy_filter;
src/http/ngx_http_gzip_filter_module.c:1131:  ngx_http_next_body_filter = ngx_http_top_body_filter;
src/http/ngx_http_gzip_filter_module.c:1132:  ngx_http_top_body_filter = ngx_http_gzip_body_filter;
```

看懂這個模式：每個 filter module 在初始化時做**兩句**——
```c
ngx_http_next_body_filter = ngx_http_top_body_filter;   // 記住「原本的頭」是我的下一個
ngx_http_top_body_filter  = ngx_http_gzip_body_filter;  // 我變成新的頭
```

**這是一個用全域指標串出來的單向鏈，而且是「後註冊的排前面」（頭插法）。** `ngx_http_top_body_filter` 永遠指向鏈頭，每個 filter 把自己插到最前、把舊頭記成自己的 `next`。發送回應時從 `ngx_http_top_body_filter` 進入，每個 filter 加工完呼叫自己的 `next`，一路傳到底層的 write filter 送進 socket。

送回應的入口是 `ngx_http_output_filter`（真跑 `sed -n '1854,1872p' src/http/ngx_http_core_module.c`）：

```c
// src/http/ngx_http_core_module.c:1854（1.26.2，節選）
ngx_int_t
ngx_http_output_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    ngx_int_t          rc;
    ...
    rc = ngx_http_top_body_filter(r, in);    // ★ 從鏈頭進入 filter chain
    ...
    return rc;
}
```

filter 的順序由「連結進 nginx 的順序」決定（configure 時排定），所以 gzip 一定在 write 之前跑（先壓縮、再送）。**這是 plugin pipeline 的另一種形態：不是掛到固定 phase，而是串成一條處理鏈，資料流過每一環。** 對照 Ch 14 的 `ngx_event_actions`（函式指標表做多型）和這裡（函式指標鏈做管線），你會發現 nginx 反覆用函式指標做各種可插拔結構——這是 C 沒有語言級 plugin 機制時的標準解法。

## 對比與取捨：兩種 pipeline 形態

| 形態 | phase handler（input 側） | filter chain（output 側） |
|---|---|---|
| 結構 | 固定順序的 phase 陣列，每 phase 一組 handler | 全域指標串的單向鏈 |
| 註冊方式 | `postconfiguration` 裡 push 到 `phases[X].handlers` | init 時頭插到 `ngx_http_top_*_filter` |
| 順序決定 | phase enum 寫死（核心定義） | 連結順序（configure 排定） |
| 認領/放行 | `NGX_DECLINED` 換下一個（chain of responsibility） | 每個 filter 都跑，各自決定要不要動資料 |
| 適合 | 「請求該過哪些關卡」的階段式決策 | 「回應該被怎麼加工」的串接式轉換 |

一個是**階段式**（過關卡，某關卡可能有多個候選 handler 輪流試），一個是**串接式**（流水線，每環都跑、依序轉換）。兩種都是 plugin 架構，但形狀不同——**認出「這是階段式還是串接式 pipeline」能幫你快速判斷一個可擴充系統的擴充點在哪**。

## 踩雷集錦

1. **讀 module 定義被 `NGX_MODULE_V1` 一堆 `NULL` 淹沒**。錯誤直覺：「這麼多欄位要一個個看懂」。正確：`NGX_MODULE_V1`/`NGX_MODULE_V1_PADDING` 是樣板巨集、一長串 `NULL` 是「這個 hook 我不用」。**眼睛跳過樣板，只看 `ctx`、`commands`、`type` 三格**，那才是 module 的個性。

2. **想在 `ngx_module_t` 裡找 http module 的具體邏輯**。正確：`ngx_module_t` 只是共同外殼，http module 的真正介面在 `ctx` 指向的 `ngx_http_module_t`（8 個 conf/config hook），handler 註冊在 `postconfiguration` 裡。**追一個 module 做什麼，先跳到它的 `ctx`。**

3. **以為 module 能改 phase 順序或插新 phase**。正確：11 個 phase 的順序是 enum 寫死的核心骨架，module 只能**往現有 phase 掛 handler**，且有幾個 phase（FIND_CONFIG/POST_REWRITE/POST_ACCESS）是核心專用、掛不進去。plugin 填內容，不改框架。

4. **看到 content handler 回 `NGX_DECLINED` 以為是錯誤**。正確：`NGX_DECLINED` 是「我不認領這個請求，換下一個 handler」的正常訊號（chain of responsibility），不是 error。static 對目錄請求就回它，讓給 autoindex。讀 nginx 的回傳值要分清 `NGX_OK`/`NGX_DECLINED`/`NGX_AGAIN`/`NGX_ERROR` 各自的語意。

5. **`run_phases` 回傳 `NGX_OK` 就 return，以為請求處理完了**。正確：checker 回 `NGX_OK` 常常是「這格要等 I/O，先讓出執行緒」，不是「做完了」。處理進度存在 `r->phase_handler` 游標上，event loop 之後會再進來從游標接續——**phase 引擎是可暫停續跑的狀態機，不是一次跑到底的函式**。

## 進階：再往深一層

- **`objs/ngx_modules.c` 就是 module 陣列的真身**：Ch 13 configure 產出的這個檔案，列出這次 build 進去的所有 module（`ngx_modules[]`）。`ngx_preinit_modules`（`src/core/ngx_module.c:26`）啟動時掃這個陣列，給每個 module 編 index。**看一個 nginx build 裝了什麼、順序如何，看這個檔案最快。**
- **filter 順序為什麼是頭插法還能對？** filter 靠 configure 時的連結順序排定，nginx 的 build 系統刻意讓 write filter 最先連結（所以在鏈尾）、copy/gzip 等後連結（在鏈頭）。頭插法 + 連結順序控制，等於「宣告順序的逆序」= 執行順序。這是個容易看錯的地方——`reading_code` Ch 21「讀懂 build system」會告訴你順序其實由 build 檔決定。
- **header filter 和 body filter 是兩條平行的鏈**：`ngx_http_top_header_filter` 處理回應 header、`ngx_http_top_body_filter` 處理 body，機制相同但分開。送回應時先跑 header filter chain、再跑 body filter chain。

## 本章重點整理

- nginx 是 **plugin 架構**：核心定義掛勾點與統一介面，功能全做成 module 掛上去，加功能不改核心。
- `ngx_module_t` 是所有 module 的共同外殼，`ctx` 指向型別相依介面（http module 的是 `ngx_http_module_t` 的 8 個 hook）；讀時跳過 `NGX_MODULE_V1` 樣板，看 `ctx/commands/type`。
- HTTP 請求走 **11 個固定順序的 phase**（enum 寫死）；module 在 `postconfiguration` 裡 push handler 到 `phases[X].handlers`；引擎 `ngx_http_core_run_phases` 沿攤平的陣列一格一格叫 `checker`。
- 引擎是**可暫停續跑的狀態機**：進度存在 `r->phase_handler` 游標，等 I/O 時 return、event loop 再進來續跑。content phase 用 `NGX_DECLINED` 做 chain of responsibility。
- **output filter chain** 是全域指標頭插串出的單向鏈，回應（buffer chain）從 `ngx_http_top_body_filter` 流過每個 filter 加工，最後 write filter 送進 socket。

## 自我檢核

- [ ] 我能解釋 `ngx_module_t` 為什麼用 `void *ctx` 做泛型，以及 http module 的真正介面在哪
- [ ] 我能寫出一個 module 把自己掛到某 phase 的那 5 行（`ngx_array_push` 到 `phases[X].handlers`）
- [ ] 我能說出 `ngx_http_core_run_phases` 的迴圈結構，以及 `r->phase_handler` 游標為什麼是狀態機的關鍵
- [ ] 我能解釋 content phase 的 `NGX_DECLINED` 是什麼 pattern（chain of responsibility）
- [ ] 我能說清 output filter chain 為什麼用頭插法、順序其實由連結順序決定

## 延伸閱讀

- **[Emiller's Guide To Nginx Module Development](https://www.evanmiller.org/nginx-modules-guide.html)**
  - **讀哪裡**：整篇，尤其「Handlers」與「Filters」兩節；這是講 nginx module 機制最清楚的社群文件，配著本章的 source 讀，phase/filter 會立體起來
  - **前提**：讀得懂 C
- **[nginx development guide — HTTP request processing phases](https://nginx.org/en/docs/dev/development_guide.html#http_phases)**
  - **讀哪裡**：「Phases」一節；官方對 11 個 phase 各自用途的權威說明
  - **前提**：無
- **`reading_code` Ch 23「讀懂 indirection」**
  - **讀哪裡**：函式指標表/鏈的追法；本章的 `ngx_http_module_t` hook、phase handler、filter chain 全是函式指標間接，是這章技巧的重度應用場
  - **前提**：無

三個機制（event loop、memory pool、module pipeline）都拆過了。下一章把 nginx 這幾個可遷移的設計 pattern 收斂成卡片，並連到你在其他 Part 會再遇到的同類 idiom。

→ [Ch 17 萃取 pattern：reactor / object pool / plugin pipeline](./17-nginx-patterns-extracted.md)
