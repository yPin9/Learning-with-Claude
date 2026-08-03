# Ch 15 — memory pool、buffer chain、資料結構慣例

> **目標**：拆 nginx 的記憶體策略——`ngx_pool_t`（arena/region allocator）和 `ngx_buf_t`/`ngx_chain_t`（零拷貝 buffer 鏈）。搞懂 nginx 為什麼幾乎不用 `free`、一個請求結束時如何一次釋放整塊記憶體，以及資料如何在 buffer 鏈上流動而不被反覆複製。這是全 C 系統程式界最可遷移的 pattern 之一，你在 Apache、PostgreSQL、protobuf-c 甚至遊戲引擎裡都會再遇到。

> **目標codebase**：nginx `release-1.26.2`（commit `37fe983`）

## 為什麼需要這個？

回到 Ch 14 的場景：epoll 醒來，某條連線可讀，handler 開始處理一個 HTTP 請求。這個過程要配置**幾十上百塊小記憶體**——解析 header 要存 key/value、URI 要存、要組回應的 buffer……如果每一塊都 `malloc`、用完 `free`，會踩到三個坑：

1. **碎片化**：無數次小配置/釋放把 heap 打成碎片，長時間跑的伺服器記憶體效率越來越差。
2. **忘記 free = 洩漏**：請求處理路徑上百個分岐（各種錯誤提早 return），每條路徑都要記得 free 對的東西——極易漏，而伺服器一漏就是持續累積的洩漏。
3. **`malloc`/`free` 本身有成本**：高並發下，每秒幾十萬次 `malloc`/`free` 的鎖競爭和簿記開銷不容忽視。

nginx 的解法叫 **arena / region allocator**（它叫 memory pool）：

> **一個請求配一個 pool。處理過程中所有小配置都從這個 pool 切出去（指標往前推，不個別記帳）。請求結束時，整個 pool 一次釋放——不需要逐一 free 任何東西。**

這招把「N 次 malloc + N 次 free + 記得配對」變成「一次建 pool + N 次超便宜的指標推進 + 一次 destroy」。洩漏問題消失了（反正整塊會還），碎片問題消失了（連續配置），效能問題也解決了（配置只是指標加法）。代價是：你**不能個別釋放**其中一塊——但請求處理場景根本不需要，反正請求一結束全部一起丟。**這是一個「用生命週期換管理複雜度」的漂亮取捨**，也是本章要你認出的 pattern。

## 先建立直覺：pool 就是一條往前推的緞帶

在讀 struct 前先在腦中畫出 pool 的樣子：

```
   ngx_create_pool(size) 一次跟 OS 要一大塊：
   ┌────────────────────────────────────────────────────────────┐
   │ [pool header] │ last→        可用空間        ←end            │
   └───────────────┴────────────────────────────────────────────┘
                    ▲                                    ▲
                    last（下一塊從這裡切）              end（這塊到哪為止）

   ngx_palloc(pool, 40)：把 last 往前推 40，回傳原本的 last
   ┌───────────────┬──────┬─────────────────────────────────────┐
   │ [pool header] │ 40B  │ last→   剩下的       ←end            │
   └───────────────┴──────┴─────────────────────────────────────┘

   一塊切滿了？再 malloc 一塊，用 d.next 串成鏈：
   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ block 1  │──►│ block 2  │──►│ block 3  │──► NULL
   └──────────┘   └──────────┘   └──────────┘
     d.next          d.next         d.next

   ngx_destroy_pool()：沿 d.next 走一遍，每塊 free 一次。整個 pool 沒了。
```

三個要點：

1. **配置 = 指標往前推。** `ngx_palloc` 的核心就是 `p->d.last += size`——沒有 header、沒有 free list、沒有簿記。這是它比 `malloc` 快得多的原因。
2. **一塊不夠就串下一塊。** pool 不是單一 buffer，是一條 block 鏈，切滿一塊 `malloc` 新的接上去。
3. **沒有個別 free。** `ngx_pfree` 存在但幾乎不用（只對「大配置」有意義，見下）。正常回收靠 `ngx_destroy_pool` 一次還整條鏈。

## 核心：`ngx_pool_t` 的結構

打開 `src/core/ngx_palloc.h`，看 pool 的兩個 struct（真跑 `sed -n '48,66p' src/core/ngx_palloc.h`）：

```c
// src/core/ngx_palloc.h（1.26.2）
typedef struct {
    u_char               *last;    // ★ 下一塊從這裡切
    u_char               *end;     // ★ 這個 block 的邊界
    ngx_pool_t           *next;    // ★ 串到下一個 block
    ngx_uint_t            failed;   // 這個 block 配置失敗過幾次（優化用）
} ngx_pool_data_t;

struct ngx_pool_s {
    ngx_pool_data_t       d;        // 本 block 的配置狀態（就是上面那個）
    size_t                max;      // 小/大配置的分界（超過走 large）
    ngx_pool_t           *current;  // 目前該從哪個 block 開始找空間
    ngx_chain_t          *chain;    // 快取的 buffer chain（重用）
    ngx_pool_large_t     *large;    // ★ 大配置的鏈（單獨 malloc，可個別 free）
    ngx_pool_cleanup_t   *cleanup;  // ★ 清理回呼（釋放非記憶體資源）
    ngx_log_t            *log;
};
```

三個欄位值得停下來：

- **`d`（`ngx_pool_data_t`）**：`last`/`end` 就是那條緞帶的兩端，`next` 把 blocks 串起來。**pool header 本身就長在它管理的那塊記憶體最前面**（下面 `ngx_create_pool` 會看到），省一次配置。
- **`max`**：小配置和大配置的分水嶺。小的（`<= max`）從 block 緞帶切；大的（`> max`）直接 `malloc` 一塊、掛進 `large` 鏈。為什麼分？因為超大配置塞不進 block（會浪費、或直接放不下），而且大塊有時需要**提早個別釋放**——`large` 鏈上的東西可以 `ngx_pfree`，緞帶上的不行。
- **`cleanup`**：pool 不只管記憶體。有些資源（開啟的 fd、暫存檔）不是記憶體、不能靠「還記憶體」回收。`cleanup` 是一串回呼，`ngx_destroy_pool` 時逐一呼叫——這是 arena allocator 的重要延伸：**把「非記憶體資源的釋放」也掛到同一個生命週期上**。

## 底層機制一：`ngx_create_pool` 與 `ngx_palloc`

看 pool 怎麼生出來（真跑 `sed -n '19,42p' src/core/ngx_palloc.c`）：

```c
// src/core/ngx_palloc.c:19（1.26.2，節選）
ngx_pool_t *
ngx_create_pool(size_t size, ngx_log_t *log)
{
    ngx_pool_t  *p;

    p = ngx_memalign(NGX_POOL_ALIGNMENT, size, log);   // 跟 OS 要一大塊
    if (p == NULL) {
        return NULL;
    }

    p->d.last = (u_char *) p + sizeof(ngx_pool_t);      // ★ last 跳過 header
    p->d.end = (u_char *) p + size;                     // end = 這塊的尾
    p->d.next = NULL;
    p->d.failed = 0;

    size = size - sizeof(ngx_pool_t);
    p->max = (size < NGX_MAX_ALLOC_FROM_POOL) ? size : NGX_MAX_ALLOC_FROM_POOL;

    p->current = p;
    p->chain = NULL;
    p->large = NULL;
    p->cleanup = NULL;
    p->log = log;
    return p;
}
```

注意 `p->d.last = (u_char *) p + sizeof(ngx_pool_t)`——**pool 的 header 就住在它管的那塊記憶體開頭，`last` 從 header 後面開始切**。一次 `memalign` 同時搞定「header + 可用空間」，這是 arena allocator 的常見手法。

配置的入口是 `ngx_palloc`（真跑 `sed -n '122,133p' src/core/ngx_palloc.c`）：

```c
// src/core/ngx_palloc.c:122（1.26.2）
void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
#if !(NGX_DEBUG_PALLOC)
    if (size <= pool->max) {
        return ngx_palloc_small(pool, size, 1);   // 小配置：從緞帶切
    }
#endif
    return ngx_palloc_large(pool, size);          // 大配置：單獨 malloc + 掛 large 鏈
}
```

一個 `if` 分流：小的走 `ngx_palloc_small`，大的走 `ngx_palloc_large`。（`ngx_palloc` 會對齊、`ngx_pnalloc` 不對齊，差別只在傳給 `ngx_palloc_small` 的最後那個 `align` 參數。）

小配置的核心 `ngx_palloc_small`（真跑 `sed -n '149,175p' src/core/ngx_palloc.c`）:

```c
// src/core/ngx_palloc.c:149（1.26.2，節選）
static ngx_inline void *
ngx_palloc_small(ngx_pool_t *pool, size_t size, ngx_uint_t align)
{
    u_char      *m;
    ngx_pool_t  *p;

    p = pool->current;
    do {
        m = p->d.last;
        if (align) {
            m = ngx_align_ptr(m, NGX_ALIGNMENT);   // 對齊
        }
        if ((size_t) (p->d.end - m) >= size) {     // ★ 這塊還放得下？
            p->d.last = m + size;                  // ★★ 核心：指標往前推
            return m;                              // 回傳切出來的位置
        }
        p = p->d.next;                             // 這塊滿了，看下一塊
    } while (p);

    return ngx_palloc_block(pool, size);           // 全滿 → 開新 block
}
```

**整個配置的核心就是 `p->d.last = m + size; return m;`——指標往前推，回傳原位置。** 沒有 free list 搜尋、沒有 size class、沒有 header 記帳。這就是為什麼 arena 配置可以比通用 `malloc` 快一個數量級。塊滿了就 `ngx_palloc_block` 再 `memalign` 一塊、串到 `d.next`（真跑看 `ngx_palloc_block` 在 `:178`，裡面 `new->d.last = m + size; ... p->d.next = new;`）。

`ngx_palloc_block` 還有個小巧思：`if (p->d.failed++ > 4) pool->current = p->d.next;`——如果某個 block 已經連續配置失敗 5 次（幾乎滿了），就把 `current` 往後移，**下次配置不再從頭掃那些注定放不下的滿 block**。這是拿使用統計做的一個實用優化，讀 code 時容易掃過去，值得留意。

## 底層機制二：`ngx_destroy_pool`——一次還整條鏈

回收的核心（真跑 `sed -n '47,98p' src/core/ngx_palloc.c`，節選）：

```c
// src/core/ngx_palloc.c:47（1.26.2，節選）
void
ngx_destroy_pool(ngx_pool_t *pool)
{
    ngx_pool_t          *p, *n;
    ngx_pool_large_t    *l;
    ngx_pool_cleanup_t  *c;

    for (c = pool->cleanup; c; c = c->next) {    // ★ 1. 先跑所有 cleanup 回呼
        if (c->handler) {
            c->handler(c->data);                 //    關 fd、刪暫存檔等
        }
    }
    ...
    for (l = pool->large; l; l = l->next) {      // ★ 2. free 所有大配置
        if (l->alloc) {
            ngx_free(l->alloc);
        }
    }

    for (p = pool, n = pool->d.next; /* void */; p = n, n = n->d.next) {
        ngx_free(p);                             // ★ 3. 沿 d.next 走，每個 block free 一次
        if (n == NULL) {
            break;
        }
    }
}
```

三步，順序有講究：

1. **先跑 `cleanup` 回呼**——趁記憶體還在，讓那些「非記憶體資源」（fd、暫存檔）先被正確關掉。
2. **free `large` 鏈**——那些單獨 `malloc` 的大塊。
3. **沿 `d.next` 走，逐個 block `ngx_free`**——把緞帶的每一節還給 OS。

**注意第 3 步：不管你在這個 pool 裡切過幾百塊小記憶體，這裡完全不管它們——它們全長在 block 裡，free 掉 block 就一起回收了。** 這就是「一次釋放整塊」的具體樣貌。對照一下：如果用傳統 `malloc/free`，這裡得逐一 free 那幾百塊，還得確保沒漏。arena 把這件事變成「走一條 block 鏈」。

## 核心：`ngx_buf_t` 與 `ngx_chain_t`——零拷貝 buffer 鏈

記憶體配置解決了，資料怎麼流？nginx 送一個回應可能來自多處：記憶體裡的 header、`sendfile` 直送的檔案 body、gzip 過的一段……如果每一段都複製到一個大 buffer 再送，複製成本高得離譜。nginx 用 **buffer chain** 避免複製。

看 `ngx_buf_t`（真跑 `sed -n '/^struct ngx_buf_s {/,/};/p' src/core/ngx_buf.h`，節選）：

```c
// src/core/ngx_buf.h（1.26.2，節選）
struct ngx_buf_s {
    u_char          *pos;        // ★ 目前讀到哪（消費者的游標）
    u_char          *last;       // ★ 有效資料到哪為止
    off_t            file_pos;    // 若資料在檔案：檔案偏移起點
    off_t            file_last;   //               檔案偏移終點

    u_char          *start;       // buffer 記憶體的頭
    u_char          *end;         // buffer 記憶體的尾
    ngx_buf_tag_t    tag;         // 誰配的（filter 認領自己的 buf 用）
    ngx_file_t      *file;        // 資料若在檔案，指向它

    unsigned         temporary:1; // 內容可改（在可寫記憶體）
    unsigned         memory:1;    // 內容在唯讀記憶體，不可改
    unsigned         mmap:1;      // 內容是 mmap 的
    unsigned         in_file:1;   // ★ 資料在檔案裡（不在記憶體！）
    unsigned         flush:1;     // 要求 flush
    unsigned         last_buf:1;  // ★ 整個回應的最後一塊
    unsigned         last_in_chain:1;
    ...
};
```

`ngx_buf_t` 的巧妙在於它**可以描述「不在記憶體裡」的資料**：`in_file` 為真時，資料在 `file` 指的檔案的 `file_pos..file_last` 區間，記憶體裡根本沒有它的 copy。這是 `sendfile` 零拷貝的基礎——nginx 送檔案時不把檔案讀進記憶體再送，而是造一個 `in_file` 的 buf，交給 kernel 的 `sendfile()` 直接從 page cache 送到 socket，資料一次都沒進 nginx 的位址空間。

`pos`/`last` 和 `start`/`end` 的分工也要看清：`start`/`end` 是**這塊記憶體的物理邊界**（配了多大），`pos`/`last` 是**有效資料的邏輯範圍**（現在裝了多少、消費到哪）。消費者讀掉一部分就把 `pos` 往前推，不用搬資料。

多個 buf 用 `ngx_chain_t` 串成鏈（真跑 `sed -n '/^struct ngx_chain_s {/,/};/p' src/core/ngx_buf.h`）：

```c
// src/core/ngx_buf.h（1.26.2）
struct ngx_chain_s {
    ngx_buf_t    *buf;
    ngx_chain_t  *next;
};
```

就是一個單向鏈結串列的節點，每個節點掛一個 `ngx_buf_t`。一個 HTTP 回應在 nginx 內部就是一條 `ngx_chain_t`：

```
   一個回應的 buffer chain：

   chain ──► [buf: header, in memory]  pos───last
     │
     next ──► [buf: file body, in_file] file_pos───file_last  (資料在 disk，不佔記憶體)
     │
     next ──► [buf: trailer, last_buf=1]
     │
     next ──► NULL
```

**這條鏈流過一整串 filter（Ch 16 的 output filter chain），每個 filter 可以改鏈、加 buf、消費 buf，但資料本體盡量不複製——只傳指標、推游標。** 這就是「零拷貝 buffer 鏈」pattern：資料的所有權和位置用 struct 描述，處理管線傳遞的是「描述」，不是「資料」。

## 對比與取捨：arena vs 通用 malloc

| 面向 | 通用 `malloc`/`free` | arena/pool（nginx） |
|---|---|---|
| 配置成本 | 找 free list、記帳、可能加鎖 | 指標往前推，幾乎零成本 |
| 個別釋放 | 支援（`free(p)`） | **不支援**（只能整塊 destroy；large 例外） |
| 洩漏風險 | 每條路徑都要記得配對 free | 生命週期到就整塊還，幾乎不會漏 |
| 碎片 | 長跑會碎 | 連續配置，不碎 |
| 適用場景 | 生命週期各異、需個別回收 | **一批物件共享同一個生命週期**（一個請求、一次連線） |
| 心智負擔 | 高（誰 own、何時 free） | 低（綁生命週期，不用想個別回收） |

關鍵在最後兩列：**arena 的前提是「一批物件共享同一個生命週期」**。HTTP 請求完美符合——請求開始建 pool、請求結束 destroy pool，中間所有東西同生共死。如果你的物件生命週期各不相同、需要個別回收，arena 就不適用，硬套反而綁手綁腳。**認出「這批東西同生共死嗎？」是判斷該不該用 arena 的那一問。**

## 踩雷集錦

1. **想 `ngx_pfree` 一塊小記憶體來省空間**。錯誤直覺：「用完就 free 是好習慣」。正確：小配置在 block 緞帶上，`ngx_pfree` 對它基本無效（`rg -n "^ngx_pfree" src/core/ngx_palloc.c` 看它只掃 `large` 鏈）。arena 的設計就是**不個別釋放小塊**，你想省的那點空間會在 pool destroy 時整批還。硬 free 反而破壞了 pattern 的簡潔。

2. **以為 `ngx_buf_t` 裡一定有資料的 copy**。正確：`in_file` 為真時資料在**檔案**裡（`file` + `file_pos/file_last`），記憶體裡沒有。`sendfile` 靠這個做真正的零拷貝。把 buf 當成「資料容器」會誤判記憶體用量——它是「資料的描述」。

3. **搞混 `start/end` 和 `pos/last`**。正確：`start/end` 是物理記憶體邊界（配了多大），`pos/last` 是邏輯有效範圍（裝了多少、消費到哪）。讀 buffer 處理 code 時，動的通常是 `pos/last`（推游標），`start/end` 很少變（那是這塊記憶體的固定邊界）。

4. **看到 `ngx_palloc_small` 裡的 `do...while` 迴圈以為在做複雜搜尋**。正確：它只是沿 `d.next` 依序看「哪個 block 還放得下」，配上 `current` 跳過已滿的 block 的優化。沒有 best-fit / size class 那套——arena 刻意不做那些，快才是重點。

5. **忽略 `cleanup`，以為 pool 只管記憶體**。正確：`cleanup` 回呼讓 pool 把 fd、暫存檔這類非記憶體資源也綁進同一個生命週期，`ngx_destroy_pool` 會先跑它們。**這是 arena pattern 的重要延伸：統一的生命週期管理，不只管記憶體。** 你自己實作 arena 時很容易漏掉這一層。

## 進階：再往深一層

- **`ngx_reset_pool`**：連線 keepalive 時要重用 pool 而不重建。它的核心（真跑 `sed -n '100,122p' src/core/ngx_palloc.c`，節選）：
  ```c
  // src/core/ngx_palloc.c:100（1.26.2，節選）
  void
  ngx_reset_pool(ngx_pool_t *pool)
  {
      for (l = pool->large; l; l = l->next) {   // 大配置照樣 free 掉
          if (l->alloc) { ngx_free(l->alloc); }
      }
      for (p = pool; p; p = p->d.next) {         // ★ 每個 block 的 last 拉回開頭
          p->d.last = (u_char *) p + sizeof(ngx_pool_t);
          p->d.failed = 0;
      }
      pool->current = pool;
      pool->large = NULL;
  }
  ```
  **把每個 block 的 `d.last` 拉回開頭 = 一瞬間「清空」整個 pool，記憶體不還給 OS，下個請求接著用。** 這比 destroy + create 便宜太多——省了 `free` 整條 block 鏈再重新 `memalign`。這是 arena 的一個關鍵變體：**reset（重用）vs destroy（歸還）**。注意它不跑 `cleanup` 回呼（reset 不是生命週期結束），這是它和 `ngx_destroy_pool` 的重要差異——讀的時候容易忽略。
- **兩層 pool**：nginx 每條連線有一個 `connection pool`（活得久，跨多個 keepalive 請求），每個請求有一個更小的 `request pool`（請求結束就 destroy）。生命週期不同的東西放不同 pool——這是 arena 用得好的關鍵：**pool 的邊界要對齊生命週期的邊界**。`rg -n "connection_pool_size\|request_pool_size" src/http/ngx_http_core_module.h` 看設定項。
- **`ngx_pool_cleanup_add`**：想把某個資源掛進 pool 的清理鏈，用它註冊一個 `handler`。讀任何用 arena 的專案，`cleanup` 註冊點是理解「資源何時被釋放」的關鍵——`reading_code` Ch 8「data flow 追蹤」在追資源生命週期時會用到。

## 本章重點整理

- nginx 用 **arena/pool allocator**（`ngx_pool_t`）取代逐一 `malloc/free`：配置 = 指標往前推（`d.last += size`），回收 = 整塊 destroy。**一批同生命週期的物件（一個請求）共享一個 pool、一起釋放。**
- pool 分小配置（緞帶切，不能個別 free）與大配置（單獨 malloc、掛 `large` 鏈、可個別 free），`cleanup` 鏈把非記憶體資源綁進同一生命週期。
- `ngx_buf_t`/`ngx_chain_t` 是**零拷貝 buffer 鏈**：buf 用 `pos/last`（邏輯）+ `start/end`（物理）描述記憶體資料，或用 `in_file`+`file_pos/last` 描述**檔案裡**的資料（`sendfile` 零拷貝的基礎）。處理管線傳「描述」不傳「資料」。
- arena 的取捨：極省的配置 + 幾乎零洩漏風險 換 不能個別回收。前提是「一批物件同生命週期」——認出這個前提是判斷該不該用 arena 的關鍵。

## 自我檢核

- [ ] 我能說出 `ngx_palloc_small` 配置的核心那兩行（推 `d.last`、回傳 `m`），以及為什麼它比 `malloc` 快
- [ ] 我能解釋 `ngx_destroy_pool` 為什麼不用逐一 free 那幾百塊小配置
- [ ] 我能區分 pool 的小配置（緞帶）與大配置（large 鏈），以及為什麼要分
- [ ] 我能說出 `ngx_buf_t` 如何描述「在檔案裡、不在記憶體」的資料，以及這對零拷貝的意義
- [ ] 我能講清 arena 適用的前提（一批物件同生命週期），並舉一個不適用的反例

## 延伸閱讀

- **[nginx development guide — Pool / Buffer 章節](https://nginx.org/en/docs/dev/development_guide.html#pool)**
  - **讀哪裡**：「Pool」「Buffer」「Chain」三小節；官方對這幾個 struct 的欄位語意講得比註解清楚，配著 source 讀
  - **前提**：讀得懂 C struct
- **《The Garbage Collection Handbook》— region-based memory management 一節**（Jones, Hosking, Moss）
  - **讀哪裡**：region/arena 那節；把 nginx pool 放進「region-based memory management」這個更大的學術脈絡，你會發現 PostgreSQL 的 MemoryContext、Apache 的 apr_pool 都是同一家族
  - **前提**：無
- **`reading_code` Ch 8「順藤摸瓜：data flow 追蹤」**
  - **讀哪裡**：追資源生命週期的方法；本章的 pool 生命週期、buf 在 chain 上的流動，正是 data flow 追蹤的實戰對象
  - **前提**：無

記憶體和資料流的地基有了。下一章我們看 nginx 怎麼把「一個 HTTP 請求該經過哪些處理」組織成一條可插拔的 pipeline——module 結構、11 個 phase、filter 鏈。這是 plugin 架構的教科書範本。

→ [Ch 16 module / handler pipeline](./16-nginx-module-pipeline.md)
