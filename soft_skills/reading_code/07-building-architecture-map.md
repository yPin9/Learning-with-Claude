# Ch 7 — 建立架構地圖

> **目標**：把 Ch 5（偵察）與 Ch 6（入口）的零散發現，收斂成一張你能一眼看懂的**架構地圖**——有哪些子系統、分幾層、誰依賴誰、地圖的中心在哪。核心方法：從目錄命名慣例、include 依賴關係、以及「核心資料結構」三管齊下。redis 實戰：找出 `server.h` 裡的兩顆心臟 struct（`redisServer` 與 `client`），畫出一張涵蓋 networking / 指令分派 / 資料型別 / 持久化 / 複製的 ASCII 架構圖，全程用 `rg`/`nm`/`cscope` 真跑佐證。

## 為什麼要「地圖」，而不是繼續往下追？

Ch 6 我們摸到了一條主動脈：`main` → `aeMain` → `readQueryFromClient` → `processCommand` → `call`。順著一條路徑往下追很爽，但有個致命問題：**你只有一條線，沒有面。** 你不知道 `rdb.c` 跟這條線什麼關係、`replication.c` 在整個系統的哪一層、`cluster.c` 要不要現在管。沒有面，你每讀一個新檔都像第一次進門，不知道它在整體的哪個位置。

這就是逆向大型 binary 到某個階段一定要做的事：**從「追一條 call chain」升級到「畫模組圖」**。逆手會把 binary 按功能分塊（這堆函式是網路、那堆是加密、這塊是狀態機），標出塊與塊的呼叫方向。有了這張塊圖，任何一個新函式一出現，你能立刻把它歸到某個塊，理解成本從 O(全域) 降到 O(單塊)。

架構地圖對讀 source 是同一件事。它的價值是**把「這檔在幹嘛」的問題，轉化成「這檔屬於哪個子系統、那子系統在幹嘛」**——後者你已經知道，所以理解新檔的邊際成本大幅下降。這張圖是你讀完整個專案期間反覆回看的底圖。

## 建圖三管齊下：目錄、include、核心 struct

畫架構圖不靠通讀全部程式碼（那要幾週），靠三種**低成本、高訊號**的證據源交叉印證：

```
   證據源              給你什麼                 redis 上怎麼跑
 ┌──────────────┐   ┌────────────────┐   ┌──────────────────┐
 │ 目錄/檔名慣例 │ → │ 子系統的粗切分   │   ls src/*.c，看前綴分組
 │ include 關係  │ → │ 層級與依賴方向   │   rg '#include' 統計
 │ 核心 struct   │ → │ 地圖的中心       │   找誰被最多檔案引用
 └──────────────┘   └────────────────┘   └──────────────────┘
```

三者互補：**目錄慣例**給你「有哪些塊」，**include 關係**給你「塊怎麼疊、誰依賴誰」，**核心 struct**給你「地圖的圓心在哪」。單看任一個都會偏，交叉起來就穩。下面逐一在 redis 上跑。

## 管道一：目錄與檔名慣例——子系統粗切分

Ch 5 已經瞥見 `t_*.c`（type）的命名慣例。現在系統化地把 `src/` 的檔名按前綴/主題分組，這就是子系統的初稿：

```
$ ls src/t_*.c
src/t_hash.c  src/t_list.c  src/t_set.c  src/t_stream.c
src/t_string.c  src/t_zset.c

$ ls src/ae.c src/anet.c src/networking.c src/connection.c src/socket.c
src/ae.c  src/anet.c  src/connection.c  src/networking.c  src/socket.c

$ ls src/rdb.c src/aof.c src/replication.c src/cluster*.c
src/aof.c  src/cluster.c  src/cluster_legacy.c  src/rdb.c  src/replication.c
```

命名慣例直接把子系統框出來：

| 子系統 | 代表檔 | 從命名怎麼看出來 |
|---|---|---|
| 資料型別 | `t_string/list/set/zset/hash/stream.c` | `t_` = type，一型一檔 |
| 網路 I/O | `ae.c`（事件迴圈）、`anet.c`、`connection.c`、`socket.c`、`networking.c` | net/socket/connection 語意 |
| 指令分派 | `server.c`、`call_reply.c`、`commands.def` | server.c 主控 + 指令表 |
| 持久化 | `rdb.c`（快照）、`aof.c`（append-only log） | 兩種持久化各一檔 |
| 複製/高可用 | `replication.c`、`cluster*.c`、`sentinel.c` | replication/cluster 語意 |
| 底層資料結構 | `dict.c`、`sds.c`、`ziplist.c`、`listpack.c`、`quicklist.c`、`intset.c`、`rax.c` | 通用容器，被型別層用 |
| 記憶體/物件 | `object.c`（robj）、`zmalloc.c`、`lazyfree.c`、`evict.c` | object/malloc 語意 |
| 腳本 | `eval.c`、`script_lua.c`、`function*.c` | Lua 腳本引擎 |

**光是分組檔名，redis 的骨架已經浮現八成。** 這是維護者免費給你的模組標籤，比讀任何一行邏輯都快。但檔名分組是「平面」的——它沒告訴你這些塊怎麼疊、誰在上誰在下。那要靠管道二。

## 管道二：include 關係——層級與依賴方向

`#include` 是 C 專案裡最誠實的依賴聲明：`a.c` include `b.h`，代表 a 依賴 b。統計 include 關係，就能推出**依賴方向**與**分層**——被最多人 include 的通常在底層/中心，只 include 別人、沒人 include 的通常在頂層/邊緣。

先看一個關鍵事實：**有多少 `.c` 依賴 `server.h`？**

```
$ rg -l '#include "server.h"' src/*.c | wc -l
58
```

58 個 `.c`（redis src 共 115 個 C 檔）都 include `server.h`。**`server.h` 是整個 codebase 的公共地基**——半數以上的檔案依賴它。這立刻告訴你：想理解 redis，`server.h` 是第一個要讀的檔（它定義了所有子系統共享的核心型別）。這也印證了 redis README 自己的建議：「理解一個程式最簡單的方法，是理解它用的資料結構，所以我們從主標頭 `server.h` 開始。」

再看依賴方向——底層資料結構被誰用。以 `dict.h`（雜湊表）為例：

```
$ rg -l '#include "dict.h"' src/*.c | head
src/cluster.c   src/db.c   src/dict.c   src/object.c
src/sentinel.c  src/server.c ...
```

`dict` 被 `db`、`object`、`cluster`、`server` 全用——它是**最底層的通用容器**，上面所有子系統都依賴它，但它不依賴任何業務邏輯（`dict.c` 只 include 底層工具）。這就是一個乾淨的分層訊號：**依賴只往下流，底層不知道上層存在**。

把 include 關係綜合起來，redis 的分層浮現：

```
        ┌──────────────────────────────────────────┐
  上層  │ 業務子系統：t_*.c / rdb / aof / replication │  依賴 server.h + 底層
        │              / cluster / pubsub / eval      │
        ├──────────────────────────────────────────┤
  中層  │ server.h（共享型別）+ server.c（分派/主控）  │  地基：58 個 .c 依賴它
        │ networking.c（協定）+ ae.c（事件迴圈）       │
        ├──────────────────────────────────────────┤
  底層  │ 通用容器：dict / sds / listpack / quicklist  │  誰都依賴、不依賴業務
        │ / intset / rax；記憶體：zmalloc / object     │
        └──────────────────────────────────────────┘
                     依賴方向：上 → 下（單向）
```

> **建圖技巧**：依賴方向是架構地圖最有價值的一維。畫圖時箭頭一律標「誰依賴誰」，並檢查有沒有**環**——健康架構的依賴是有向無環的（上層依賴下層，反之不成立）。若發現底層檔 include 了上層 header（例如 `dict.c` 去 include `t_zset.h`），那是設計異味或緊耦合點，讀碼與改碼時要特別當心（Ch 30 讀爛 code 會回到這）。

## 管道三：核心 struct——找地圖的中心

架構地圖有一個「中心」——通常是**貫穿整個系統、被最多子系統讀寫的核心資料結構**。找到它，等於找到了所有子系統的交會點。這是 redis README 明說、也是逆向老手的直覺：**「理解程式先理解它的資料結構。」** 資料結構是骨，函式是掛在骨上的肉。

redis 的核心 struct 全在 `server.h`。先看它定義了多少 struct：

```
$ rg -n "^(struct|typedef struct)" src/server.h | wc -l
$ rg -n "struct redisServer \{|typedef struct client \{|struct redisObject \{|typedef struct redisDb \{" src/server.h
903:struct redisObject {
968:typedef struct redisDb {
1157:typedef struct client {
1547:struct redisServer {
```

四個關鍵 struct，兩顆是真正的「地圖中心」：

**中心一：`struct redisServer`（全域伺服器狀態）。** 它是那個貫穿全 codebase 的全域變數 `server` 的型別。看它前幾個欄位就知道它是「整個伺服器的所有狀態」：

```
$ sed -n '1547,1600p' src/server.h   （擷取關鍵欄位）
struct redisServer {
    pid_t pid;                  /* Main process pid. */
    redisDb *db;                /* ← 所有資料庫（key-value 就存這） */
    dict *commands;             /* ← 指令表（processCommand 查它） */
    aeEventLoop *el;            /* ← 事件迴圈（Ch 6 的心臟 server.el） */
    ...
};
```

這個 struct 有 **500 多行欄位**（`1547` 到約 `2083`）——它是 redis 所有子系統的**狀態匯流排**：event loop 在這、所有 DB 在這、指令表在這、複製狀態在這、持久化狀態在這。**每個子系統都透過全域 `server` 這個變數讀寫自己那塊狀態。** 量化一下它的中心地位：

```
$ rg -l '\bserver\.' src/*.c | wc -l
62
```

62 個 `.c`（超過半數）直接讀寫全域 `server`。**這就是地圖的圓心**——所有子系統都圍著這個全域狀態轉。理解 `redisServer` 的欄位分組（哪幾欄是網路、哪幾欄是持久化、哪幾欄是複製），等於拿到了整張架構圖的欄位級索引。

**中心二：`struct client`（單一連線的完整狀態）。** 如果 `redisServer` 是「全域狀態」，`client` 就是「一條請求的生命週期狀態」。看它的欄位，你會發現它**整條主動脈都串在裡面**：

```
$ sed -n '1157,1180p' src/server.h   （擷取關鍵欄位）
typedef struct client {
    uint64_t flags;         /* Client flags: CLIENT_* macros. */
    connection *conn;       /* ← 網路層：這條連線的 socket */
    redisDb *db;            /* ← 資料層：目前 SELECT 的 DB */
    sds querybuf;           /* ← 協定層：累積收到的原始請求 */
    int argc;               /* ← 解析後：參數個數 */
    robj **argv;            /* ← 解析後：參數陣列 */
    struct redisCommand *cmd; /* ← 分派層：查到的指令 */
    ...
} client;
```

這個 struct 是 Ch 6 那條主動脈的**資料化身**：`conn`（網路）→ `querybuf`（讀進來的原始 bytes）→ `argv`/`argc`（解析後的指令參數）→ `cmd`（查到的指令）→ `db`（要操作的資料庫）。**一個 `client` struct 從左到右，正好走完 `readQueryFromClient → processInputBuffer → processCommand → call` 這條線。** 讀懂 `client` 的欄位，你就理解了 redis 處理一個請求的完整資料流——這是 Ch 8（data flow 追蹤）的完美銜接點。

> **找中心 struct 的通法**：（1）看維護者推薦（redis README 直接點名 server.h）；（2）找被最多檔案 include 的 header 裡定義的 struct；（3）用 cscope 反查哪個 struct 被最多地方引用（`cscope -L -0 client` 找所有 `client` 的使用）；（4）找那個「欄位涵蓋多個子系統」的胖 struct——它通常就是匯流排。四招指向同一個答案時，你找到中心了。

## 把三管道合成一張圖

三管道的證據交叉起來，畫出 redis 的架構地圖。這張圖是本章的最終產物——涵蓋五大子系統、標出依賴方向、標出兩顆中心 struct：

```
                    ┌─────────────────────────────────────────┐
   client ──TCP──▶  │            networking (I/O 層)            │
                    │  ae.c: event loop ── aeMain / epoll_wait  │
                    │  networking.c: readQueryFromClient        │
                    │  connection.c/socket.c/anet.c: 連線抽象    │
                    └───────────────┬─────────────────────────┘
                                    │ 每連線一個 [struct client]
                                    │ (conn→querybuf→argv→cmd→db)
                                    ▼
                    ┌─────────────────────────────────────────┐
                    │        指令分派 (command dispatch)         │
                    │  server.c: processInputBuffer(RESP 解析)   │
                    │          → processCommand(查 commands 表)   │
                    │          → call() 執行                     │
                    │  commands.def: 指令表（由 *.json 生成）     │
                    └───────┬───────────────────┬─────────────┘
                            │                   │
              ┌─────────────▼──────┐   ┌────────▼──────────────┐
              │  資料型別 (t_*.c)   │   │  全域狀態              │
              │  string/list/set/  │◀─▶│  [struct redisServer] │
              │  zset/hash/stream  │   │  server.db / .el /    │
              │  db.c: keyspace    │   │  .commands / 複製狀態  │
              └─────────┬──────────┘   └───────────────────────┘
                        │ 資料變更觸發
          ┌─────────────┼──────────────────┐
          ▼             ▼                  ▼
   ┌────────────┐ ┌────────────┐  ┌──────────────────┐
   │ 持久化      │ │ 複製        │  │ 通知/其他          │
   │ rdb.c 快照 │ │ replication │  │ pubsub / keyspace │
   │ aof.c 日誌 │ │ cluster*.c  │  │ notify / expire   │
   └─────┬──────┘ └─────┬──────┘  └──────────────────┘
         └──────────────┴──── 全部依賴底層 ────┐
                                               ▼
   ┌───────────────────────────────────────────────────────┐
   │  底層通用容器：dict / sds / listpack / quicklist /       │
   │  intset / rax；記憶體：zmalloc / object(robj)           │
   │  （被上面所有層依賴，本身不依賴任何業務邏輯）             │
   └───────────────────────────────────────────────────────┘
```

這張圖回答了本章開頭的問題：`rdb.c` 是持久化子系統、掛在資料變更下游；`replication.c` 在複製子系統、跟 cluster 同層；`dict.c` 在最底層、誰都依賴它。**現在任何一個 redis 的檔案出現，你都能把它歸到圖上某一塊**——理解成本從「面對十萬行」降到「面對一塊」。

## 對比與取捨

| 建圖證據源 | 訊號強度 | 成本 | 給你什麼 | 局限 |
|---|---|---|---|---|
| 目錄/檔名慣例 | 中高（若命名規律） | 極低（`ls`） | 子系統粗切分 | 命名混亂的專案失效 |
| include 依賴 | 高（C/C++） | 低（`rg` 統計） | 分層 + 依賴方向 | 巨集/動態載入看不到 |
| 核心 struct | **最高** | 中（要讀 struct） | 地圖中心、子系統交會點 | 得先找對 struct |
| cscope 反查引用 | 高 | 低（需索引） | 量化「誰是中心」 | C/C++ 為主 |
| 通讀原始碼 | 最高但最慢 | 極高 | 一切 | 幾週；違背先廣後深 |

**先廣後深在建圖上的體現**：這張圖刻意停在「子系統與依賴方向」的粒度，不深入任何一塊的內部。哪一塊值得往內畫細圖（例如把「指令分派」展開成 `processCommand` 的完整狀態機），是拿著這張總圖、依你的目標（要改哪、要找什麼洞）再決定的。總圖先行，細圖按需。

**目錄慣例 vs include 關係哪個先信**：命名可能騙人（歷史遺留的 `cluster_legacy.c`、名不符實的檔），`#include` 是編譯器真的吃的、不會騙。兩者衝突時，信 include 依賴。

## 踩雷集錦

1. **一頭栽進追 call chain，不先畫圖**：錯誤直覺是「順著 `processCommand` 往下讀就懂了」。正確認識是——沒有面，你每讀一個新檔都在原地重新定位，理解不累積。先花 30 分鐘畫出子系統總圖，之後每條 call chain 都掛得上圖，理解才複利。

2. **忽略核心 struct，只讀函式**：錯誤直覺是「邏輯在函式裡，struct 只是資料容器」。正確認識是——`struct redisServer`/`client` 才是骨架，函式是掛在骨上的肉。redis README 自己都說「先理解資料結構」。跳過中心 struct 直接讀函式，你會見樹不見林。

3. **把命名慣例當成鐵律**：錯誤直覺是「檔名這樣叫，內容一定是這個」。正確認識是——命名會有歷史殘留（`cluster_legacy.c` 是舊叢集實作、`redis-check-rdb.c` 其實連進 server binary）。命名給你假設，用 include 關係與實際內容驗證。衝突時信編譯器看到的依賴。

4. **依賴圖不標方向**：錯誤直覺是「畫出誰跟誰有關係就夠了」。正確認識是——沒有方向的依賴圖價值減半。架構的健康與否、哪裡是底層、改動會往哪擴散，全靠依賴方向。永遠標箭頭，並找環（環是耦合警訊）。

5. **地圖畫完就當定稿**：錯誤直覺是「架構圖畫一次就好」。正確認識是——第一版圖必然有錯（你還沒讀細節）。它是**假設**，讀的過程中會被推翻、修正。把它當活文件，每次「咦跟我圖上不一樣」就是修圖與加深理解的時機（Ch 10 假設驅動讀碼、Ch 35 外化理解會深入）。

## 進階：再往深一層

- **自動生成 include 依賴圖**：手工統計 include 很累。可以用 `rg -o '#include "\K[^"]+' src/*.c` 抽出所有 include，配合一小段 script 生成 `dot` 檔，用 graphviz 畫成真正的依賴圖（Ch 16 給完整 pipeline）。對超大專案這是唯一可行的建圖法。
- **struct 級的子系統切分**：`redisServer` 有 500 行欄位，可以進一步把它的欄位按註解分區（redis 原始碼真的用註解把欄位分成 "Networking"、"RDB persistence"、"Replication" 等區塊）。讀那些分區註解，等於讀維護者親手畫的子系統清單。
- **cscope 量化中心度**：對候選的每個核心 struct 跑 `cscope -L -0 <struct>`（查所有使用點）數量，用引用數排序，客觀地找出「最中心」的型別。這把「哪個是地圖中心」從直覺變成可量化的度量。
- **跨語言建圖**：非 C 專案的依賴訊號在別處——Java 看 `import` 與 package 結構、Python 看 `import` 與 `__init__.py`、Go 看 package import graph（`go mod graph` / `godepgraph`）、Rust 看 `mod`/`use` 與 `Cargo.toml`。管道不同但三管齊下的方法論（命名 + 依賴 + 核心型別）通用。Ch 29 讀陌生語言會回到這。

## 動手練習

1. **跑三管道**：對 redis 依序跑 `ls src/*.c`（分組檔名）、`rg -l '#include "server.h"' src/*.c | wc -l`（依賴地基）、`rg -n "struct redisServer|typedef struct client" src/server.h`（找中心）。用這三組輸出，不看本章的圖，自己畫一張架構地圖。
2. **量化中心度**：`rg -l '\bserver\.' src/*.c | wc -l` 與 `rg -l '#include "server.h"' src/*.c | wc -l`，得出「多少檔依賴全域 server 狀態」，用數字論證 `redisServer` 是地圖中心。
3. **client struct 走一遍主動脈**：`sed -n '1157,1210p' src/server.h` 讀 `struct client` 的欄位，把 `conn → querybuf → argv → cmd → db` 這幾個欄位對應到 Ch 6 的 `readQueryFromClient → processInputBuffer → processCommand → call` 四個函式，畫出「資料欄位 ↔ 處理函式」的對照。
4. **驗證分層無環**：挑三個底層檔（`dict.c`/`sds.c`/`listpack.c`），`rg '#include' src/dict.c` 確認它們**不** include 任何業務層 header（`t_*.h`、`rdb.h`）。驗證「依賴只往下流」。
5. **換專案建圖**：對一個你偵察過的中型專案（Ch 5 練習挑的那個），用三管道畫一張架構地圖。命名慣例不清楚的專案，你會更依賴 include/import 關係——體會不同專案訊號源強弱不同。

## 本章重點整理

- 架構地圖把「這檔在幹嘛」轉化為「這檔屬於哪個子系統」，讓理解新檔的成本從 O(全域) 降到 O(單塊)。等同逆向裡從「追 call chain」升級到「畫模組圖」。
- **三管齊下**：目錄/檔名慣例（有哪些塊）+ include 依賴（分層與方向）+ 核心 struct（地圖中心），交叉印證。
- redis 的地基是 `server.h`（58/115 個 `.c` 依賴它），圓心是全域 `server`（62 個 `.c` 讀寫它）。
- 兩顆中心 struct：`redisServer`（全域狀態匯流排，500 行欄位涵蓋所有子系統）、`client`（單一請求的完整資料流，`conn→querybuf→argv→cmd→db` 正好串起主動脈）。
- 五大子系統：networking（I/O）、指令分派、資料型別（`t_*`）、持久化（rdb/aof）、複製（replication/cluster），全部依賴底層通用容器（dict/sds/listpack…）。
- 依賴方向是地圖最有價值的一維：標箭頭、找環；命名與 include 衝突時信 include。地圖是活假設，邊讀邊修。

## 自我檢核

- [ ] 不看筆記，能不能說出建架構圖的三個證據源，各自給你什麼、有什麼局限？
- [ ] 為什麼「先畫模組圖」比「一路追 call chain」更能讓理解複利？
- [ ] redis 的地圖中心是哪兩個 struct？你能不能用「被多少檔案依賴/引用」的數字論證它們的中心地位？
- [ ] `struct client` 的欄位如何對應 Ch 6 那條主動脈的四個函式？這說明資料結構與控制流是什麼關係？
- [ ] 為什麼依賴圖一定要標方向、要找環？發現底層 include 上層代表什麼？
- [ ] 面試官問「你怎麼在一週內搞懂一個十萬行專案的架構」，你能不能把三管齊下講成一套方法？

## 延伸閱讀

- **[redis README.md 的 "server.h" 與各檔逐一介紹段落](https://github.com/redis/redis/blob/7.4.0/README.md)**
  - **讀哪裡**：從 "server.h" 一節往下，維護者對 `redisObject`/`client`/`redisServer` 與各主要 `.c` 的逐一導覽。
  - **學到什麼**：維護者親手畫的架構地圖與「先讀資料結構」的方法論。拿它跟你自己三管道畫的圖對照，看你漏了什麼。
  - **關聯**：本章核心 struct 與子系統切分的權威對照。

- **《The Programmer's Brain》— Felienne Hermans（Manning, 2021），第 3–4 章（chunking / mental models）**
  - **讀哪裡**：講 chunking 與心智模型如何降低 working memory 負擔的兩章。
  - **學到什麼**：架構地圖為什麼有效的認知科學基礎——把散落的檔案「打包」成子系統 chunk，正是專家讀碼的核心機制。
  - **關聯**：本章「把 O(全域) 降成 O(單塊)」的理論依據。

- **[John Lakos, 《Large-Scale C++ Software Design》— Section on physical dependencies / levelization](https://www.oreilly.com/library/view/large-scale-c-software/9780201633627/)**
  - **讀哪裡**：physical dependency、levelization、cyclic dependency 的章節。
  - **學到什麼**：`#include` 依賴為什麼是架構的第一手證據、如何從依賴推分層、環為什麼是設計異味——把本章 include 管道的直覺升級成嚴謹方法。
  - **關聯**：本章管道二（include 依賴、分層、找環）的深度來源。

- **[Graphviz + `#include` 依賴圖 how-to（graphviz.org 文件與 cinclude2dot 之類工具）](https://graphviz.org/documentation/)**
  - **讀哪裡**：`dot` 語言基礎與如何從一組節點/邊生成圖。
  - **學到什麼**：怎麼把手工統計不動的大型 include 關係，自動抽取並畫成視覺化依賴圖。
  - **關聯**：本章「進階：自動生成 include 依賴圖」的實作工具，Ch 16 會給完整 pipeline。

架構地圖有了，兩顆中心 struct 也認出來了。但地圖是**靜態的骨架**——它告訴你有哪些塊、怎麼疊，還沒告訴你「一筆資料實際上怎麼在這些塊之間流動」。下一章我們拿 `struct client` 這個資料化身當主角，順藤摸瓜追一筆請求從 socket 進來、被解析、被執行、改動資料、觸發持久化的**完整 data flow**，讓靜態地圖活起來。

→ [Ch 8 順藤摸瓜：data flow 追蹤](./08-data-flow-tracing.md)
