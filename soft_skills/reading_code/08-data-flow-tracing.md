# Ch 8 — 順藤摸瓜：data flow 追蹤

> **目標**：學會挑一個具體的「資料」（一個 request、一個 buffer、一個變數），把它從進入系統的那一刻起，一路跟到它離開系統為止。這是讀懂任何 I/O 密集系統的第一硬技巧。讀完你能拿 redis 的一個 `GET key`，用 rg + cscope + gdb 把整條「socket 讀入 → 協定解析 → 命令查表 → 命令處理 → 回覆寫出」的路徑親手追出來，並且知道每一段「資料換了什麼身分、被誰改」。

## 為什麼是「順藤摸瓜」，而不是「從頭讀到尾」

你已經在 Ch 6、Ch 7 找到了 entry point 和粗略的架構地圖。但架構地圖是靜態的骨架——它告訴你「有哪些模組」，不告訴你「一次真實請求怎麼流過這些模組」。這兩者的差距，就像看城市地圖 vs 跟著一台計程車跑一趟。

逆向工程裡我們早就知道這招：拿到一個 binary，最有效率的理解方式不是從 `.text` 頭讀到尾，而是**挑一個你能控制的輸入**（一個封包、一個檔案、一個按鍵），下斷點看它變成什麼、流到哪、觸發哪些函式。source 讀碼完全一樣。程式碼是死的、資料是活的——**跟著活的走，死的自己會解釋自己**。

一個關鍵的心智模型先立起來：

```
  一個資料在系統裡會不斷「換身分」。追蹤 = 追它每一次換身分。

  raw bytes            "*2\r\n$3\r\nGET\r\n$5\r\nmykey\r\n"   ← 網路上的位元組
     │  (socket read)
     ▼
  querybuf (sds)       c->querybuf                          ← 進了 client 的緩衝區
     │  (protocol parse)
     ▼
  argv[] (robj*)       c->argv[0]="GET", c->argv[1]="mykey" ← 拆成命令 + 參數物件
     │  (command lookup)
     ▼
  cmd (redisCommand*)  c->cmd->proc = getCommand            ← 綁定到處理函式
     │  (dispatch → handler)
     ▼
  value (robj*)        o = lookupKeyRead(db, key)           ← 從 keyspace 取出值
     │  (encode reply)
     ▼
  reply buffer         c->buf / c->reply                    ← 排進輸出緩衝區
     │  (socket write)
     ▼
  raw bytes            "$5\r\nhello\r\n"                     ← 位元組回到網路
```

同一份「使用者要的資料」，在這條路上先後以 **bytes → sds → robj → redisCommand → robj → bytes** 的身分存在。追 data flow 的本質，就是**在每個箭頭上找到「誰負責這次轉換」**。找到全部箭頭，你就懂了這條路。

## Source 與 Sink：先借漏洞獵人的一副眼鏡

在正式追之前，先裝上一副之後 Part 5「找漏洞式讀碼」（Ch 32）會反覆用的眼鏡，這裡先建立直覺：

- **Source（源）**：不可信資料進入系統的點。對 redis 而言，最大的 source 就是 `connRead()`——從 socket 讀進來的那些 bytes，完全由對端（可能是攻擊者）控制。
- **Sink（匯）**：資料造成實際效果的點。可能是寫回 socket、寫進 keyspace、`memcpy` 到某個 buffer、當成長度去 `malloc`。

data flow 追蹤在功能理解上是「看懂一個請求怎麼被服務」；在安全視角上是「看一個**受污染（tainted）**的輸入怎麼流到危險的 sink」。**是同一條路，只是問的問題不同**。這章我們主要用功能視角追 `GET`，但每追一步，我會順帶點出它的 source/sink 意義——這副眼鏡之後你會一直戴著。

> 記住這個對應：**Source = 逆向裡的「使用者輸入」，Sink = 逆向裡的「危險 API」，data flow = 兩者之間的路徑**。整門課到 Ch 32 會把它變成一套系統化的漏洞獵法。

## 選一條藤：`GET mykey` 的完整資料流

我們追 redis 最簡單的讀命令 `GET`。選它有三個理由：路徑完整（走完整條 socket→parse→dispatch→reply）、沒有寫入的複雜性（不碰持久化、複製）、而且短到可以一次讀完。**追蹤永遠從最簡單的代表性案例開始**——先把骨幹追通，再處理變體。

我們的沙包還是 `~/reading_code_lab/redis`（redis 7.4.0，已預建 tags/cscope.out）。

### 第 0 段：藤的頭在哪？——找到 source

先問最基本的問題：位元組是從哪個函式進系統的？我們知道 redis 是事件驅動的（Ch 6 找到 `aeMain`），所以「socket 可讀」會觸發一個 read handler。用 cscope 反查誰設定了 read handler，以及函式定義在哪（真實輸出）：

```
$ cscope -d -L -0 readQueryFromClient        # -0 = 找這個符號的出現/定義
src/networking.c  createClient          123  connSetReadHandler(conn, readQueryFromClient);
src/networking.c  readQueryFromClient   2655 void readQueryFromClient(connection *conn) {
```

`createClient` 在每個連線建立時，把 `readQueryFromClient` 註冊成「這條連線可讀時要呼叫的函式」。所以 **`readQueryFromClient` 就是 source 端的藤頭**。打開它看關鍵幾行（真實 code，`src/networking.c:2655` 起，中間略去邊界檢查）：

```c
void readQueryFromClient(connection *conn) {
    client *c = connGetPrivateData(conn);
    ...
    qblen = sdslen(c->querybuf);
    ...
    nread = connRead(c->conn, c->querybuf+qblen, readlen);   // ← SOURCE：位元組進系統
    ...
    sdsIncrLen(c->querybuf,nread);                            // querybuf 長度增加 nread
    ...
    if (processInputBuffer(c) == C_ERR)                       // ← 交給下一段：解析
         c = NULL;
```

三行就是這一段的全部劇情：`connRead` 把 socket 的 bytes 讀進 `c->querybuf+qblen`（append 到既有緩衝區尾端），`sdsIncrLen` 把 sds 的長度更新，然後把控制權交給 `processInputBuffer`。

**資料現在的身分**：`c->querybuf`，一個 sds 字串，內容是 raw RESP 協定位元組 `*2\r\n$3\r\nGET\r\n$5\r\nmykey\r\n`。這是最純的 tainted source——`connRead` 讀進來的每個 byte 都是對端說了算。

> **追 data flow 的第一個實戰技巧**：`connRead` 的第二個參數 `c->querybuf+qblen` 就是「資料落地的位址」。追一個 buffer，就盯著「誰往這裡寫」。`connRead`（寫入）、`sdsIncrLen`（更新長度）——這兩個是 `querybuf` 生命週期裡「被改」的點。之後只要問「querybuf 還在哪被改」，就用 `rg 'c->querybuf'` 掃一遍——我實跑這條 rg，`src/networking.c` 裡對 `c->querybuf` 的讀寫有數十處，全是這條藤的節點。

### 第 1 段：bytes → argv[]，協定解析

`processInputBuffer` 決定這是 inline 還是 multibulk 協定，`GET` 走的是 multibulk（`*` 開頭），所以進 `processMultiBulkBuffer`。這個函式是整條藤最「髒」的一段——它在做的事就是**逆向裡最經典的「手寫協定 parser」**：讀長度前綴、切出欄位、做邊界檢查。我們不逐行讀完（那是精讀模式的事），只追「argv 是在哪一行誕生的」。用 rg 直接打靶（真實輸出）：

```
$ rg -n 'c->argv\[c->argc' src/networking.c
2429:  c->argv[c->argc++] = createObject(OBJ_STRING,c->querybuf);
2437:  c->argv[c->argc++] = createStringObject(c->querybuf+c->qb_pos,c->bulklen);
```

兩個賦值點，正是**藤在這一段換身分的地方**。看 `src/networking.c:2420` 附近的真實 code：

```c
/* Optimization: if a non-master client's buffer contains JUST our bulk element
 * instead of creating a new object by *copying* the sds we
 * just use the current sds string. */
if (!(c->flags & CLIENT_MASTER) &&
    c->qb_pos == 0 &&
    c->bulklen >= PROTO_MBULK_BIG_ARG &&
    sdslen(c->querybuf) == (size_t)(c->bulklen+2))
{
    c->argv[c->argc++] = createObject(OBJ_STRING,c->querybuf);   // ← 零拷貝：直接接管 sds
    ...
} else {
    c->argv[c->argc++] =
        createStringObject(c->querybuf+c->qb_pos,c->bulklen);    // ← 一般路徑：從 querybuf 拷貝出物件
    c->argv_len_sum += c->bulklen;
    c->qb_pos += c->bulklen+2;                                   // ← 游標往前跳過這欄位 + CRLF
}
```

這裡有兩個對讀碼者極重要的觀察：

1. **身分轉換點確認**：`createStringObject(c->querybuf+c->qb_pos, c->bulklen)` 把 querybuf 裡的一段 bytes 變成一個 `robj`（Redis Object）。`GET mykey` 解析完，`c->argv[0]` 是字串物件 `"GET"`，`c->argv[1]` 是 `"mykey"`，`c->argc == 2`。**藤從 sds 換身分成 robj 陣列**。
2. **一個效能優化透露的實作真相**：那個 `if` 分支（`createObject(OBJ_STRING, c->querybuf)`）是「大參數零拷貝」——當整個 querybuf 剛好只裝一個大 bulk，它不拷貝，直接把 sds 交給 robj、然後 querybuf 換一塊新的。**這種「優化分支」在追 data flow 時要特別留意**：同一個資料，在不同輸入下走的身分轉換路徑不同（拷貝 vs 接管）。逆向時這正是漏洞常藏的地方——兩條路徑的生命週期管理不對稱，就是 UAF/double-free 的溫床。功能追蹤時你可以先只跟一般路徑（`else` 分支），但心裡要記著另一條存在。

`c->qb_pos` 是解析游標，它把「已消化到 querybuf 的哪個位置」記下來。追 buffer 時，**「游標變數」和「buffer 本身」要一起追**——buffer 是資料，游標是狀態，兩者共同決定「下一個 byte 從哪讀」。

### 第 2 段：argv[] → cmd，命令查表

argv 就緒後，控制權從 `processInputBuffer` → `processCommandAndResetClient` → `processCommand`。藤在這一段不換身分（還是那個 argv），但**多長出一個關聯**：`argv[0]` 這個字串要對應到一個處理函式。看 `src/server.c:3924`（`processCommand` 內）：

```c
c->cmd = c->lastcmd = c->realcmd = lookupCommand(c->argv,c->argc);
```

`lookupCommand` 拿 `argv[0]`（`"GET"`）去 `server.commands` 這張命令表（一個 dict）查，回傳一個 `struct redisCommand *`。用 cscope 確認這條查表鏈（真實輸出）：

```
$ cscope -d -L -1 lookupCommand              # -1 = 找 lookupCommand 的定義
src/server.c  lookupCommand  3200  struct redisCommand *lookupCommand(robj **argv, int argc) {
```

```c
struct redisCommand *lookupCommand(robj **argv, int argc) {
    return lookupCommandLogic(server.commands,argv,argc,0);
}
```

**藤現在多帶了一個身分標籤**：`c->cmd`，一個 `redisCommand` 結構，其中 `c->cmd->proc` 是函式指標，對 `GET` 而言就指向 `getCommand`。這是 redis 的核心 indirection——命令分派靠函式指標，不是一堆 `if/else`（Ch 23 專講這種 indirection，這裡先埋點）。

> **source/sink 眼鏡**：注意此刻 `argv[0]` 仍是 tainted 的（使用者送什麼命令名都行）。`lookupCommand` 查不到就回 NULL，redis 會回 `unknown command`——這正是「不可信輸入被驗證」的一道閘。追 data flow 時，**每一道「查表 / 驗證 / 拒絕」都是 tainted 資料被「消毒（sanitize）」或「收窄」的點**，值得標記下來。

### 第 3 段：dispatch → handler，真正處理

`processCommand` 做完一堆前置檢查（權限、arity、OOM……，這些 Ch 9 會用 call graph 攤開）後，呼叫 `call(c, flags)`，`call` 再透過函式指標呼叫 `c->cmd->proc(c)`——對 `GET` 就是 `getCommand(c)`。看 `src/t_string.c:302` 起的真實 code：

```c
int getGenericCommand(client *c) {
    robj *o;

    if ((o = lookupKeyReadOrReply(c,c->argv[1],shared.null[c->resp])) == NULL)
        return C_OK;                        // 找不到 key → 回 nil，藤在這裡就結束

    if (checkType(c,o,OBJ_STRING)) {        // 型別不對（不是字串）→ 回錯誤
        return C_ERR;
    }

    addReplyBulk(c,o);                       // ← 把值排進回覆緩衝區（往 sink 走）
    return C_OK;
}

void getCommand(client *c) {
    getGenericCommand(c);
}
```

三行邏輯講完 `GET` 的全部語義：

1. `lookupKeyReadOrReply(c, c->argv[1], ...)`——**藤在這裡從「參數」勾出「值」**。它拿 `argv[1]`（`"mykey"`）當 key，去 `c->db` 的 keyspace 查，回傳存的那個 `robj *o`（值 `"hello"`）。這是一個新的 robj 進入藤：**使用者給的 key，換出了系統裡存的 value**。
2. `checkType`——型別驗證閘。
3. `addReplyBulk(c, o)`——把值 `o` 送往回覆路徑。**藤從 keyspace 的 value 準備變回 bytes**。

用 cscope 確認 `getGenericCommand` 呼叫了哪些函式，驗證我們讀的沒漏（真實輸出）：

```
$ cscope -d -L -2 getGenericCommand          # -2 = 找它呼叫了誰
src/t_string.c  lookupKeyReadOrReply  305  if ((o = lookupKeyReadOrReply(c,c->argv[1],shared.null[c->resp])) == NULL)
src/t_string.c  checkType             308  if (checkType(c,o,OBJ_STRING)) {
src/t_string.c  addReplyBulk          312  addReplyBulk(c,o);
```

三個 callee，跟我們讀到的三行完全對上。**cscope 的「找被呼叫者」是驗證你讀碼理解的最快手段**——你以為某函式做了 A、B、C，一查 callee 列表對不對得上，立刻知道有沒有漏讀分支。

### 第 4 段：value → reply buffer → bytes，走向 sink

`addReplyBulk` 把值編碼成 RESP 回覆並排進輸出緩衝區。看 `src/networking.c:1041`：

```c
void addReplyBulk(client *c, robj *obj) {
    addReplyBulkLen(c,obj);          // 寫 "$5\r\n"（長度前綴）
    addReply(c,obj);                 // 寫 "hello"（值本身）
    addReplyProto(c,"\r\n",2);       // 寫結尾 "\r\n"
}
```

三次 append，拼出 `$5\r\nhello\r\n`。這些 append 最終落到 `_addReplyToBufferOrList`（`src/networking.c:387`），它先塞進固定的 `c->buf`，塞不下才掛到 `c->reply` 這個 list。**藤在這裡從 robj 換回 bytes**，暫存在 client 的輸出緩衝區。

最後一段：這些 bytes 不是在 `getCommand` 裡直接 `write()` 出去的——redis 是事件驅動，回覆會等到事件迴圈下一輪「連線可寫」時，由 write handler 批次送出。這一步在功能追蹤裡是「sink 的最後一哩」，機制細節（`writeToClient`、`handleClientsWithPendingWrites`）我們留到 Ch 24 讀事件驅動時再追。到此，藤走完了一整圈：**bytes 進，bytes 出**。

## 用 gdb 把整條藤一次性驗證（真跑）

上面是靜態讀 + cscope 佐證。但**靜態讀永遠有「我以為它會走這條」的風險**。data flow 追蹤的黃金驗證手段是動態：在藤的某個節點下斷點，看真實請求跑到這裡時，call stack 長什麼樣、資料是什麼值。這一步把「我推論的路徑」變成「我看到的路徑」。

我在沙包上實跑了一次：起一台 `redis-server --port 7777` 在 gdb 底下，在 `getCommand` 下斷點，先 `SET mykey hello`（不觸發斷點），再 `GET mykey`（觸發），dump backtrace。這是**真實輸出**（位址已縮短）：

```
$ gdb -q -batch -x get.gdb src/redis-server      # get.gdb: break getCommand; run ...; bt; continue
   （另一 shell）$ redis-cli -p 7777 SET mykey hello   → OK
   （另一 shell）$ redis-cli -p 7777 GET mykey         → hello

Thread 1 "redis-server" hit Breakpoint 1, getCommand (c=0x7ffff7935700) at src/t_string.c:316
#0  getCommand (c=0x7ffff7935700) at src/t_string.c:316
#1  0x...5f9a15 in call (c=c@entry=0x7ffff7935700, flags=flags@entry=3) at src/server.c:3575
#2  0x...5fba78 in processCommand (c=0x7ffff7935700) at src/server.c:4206
#3  0x...62249c in processCommandAndResetClient (c=0x7ffff7935700) at src/networking.c:2505
#4  processInputBuffer (c=0x7ffff7935700) at src/networking.c:2613
#5  0x...622a68 in readQueryFromClient (conn=<optimized out>) at src/networking.c:2759
#6  0x...738101 in callHandler (handler=<optimized out>, conn=...) at src/connhelpers.h:58
#7  connSocketEventHandler (el=..., fd=..., clientData=..., mask=...) at src/socket.c:277
#8  0x...5e61f2 in aeProcessEvents (flags=27, eventLoop=0x7ffff782a140) at src/ae.c:417
#9  aeMain (eventLoop=0x7ffff782a140) at src/ae.c:477
#10 0x...5daf7b in main (argc=7, argv=<optimized out>) at src/server.c:7251
```

這個 backtrace 是整章的結案陳詞。**由下往上讀**（`#10 → #0`），它就是我們追的藤，一個 frame 都不差：

```
main                          (server.c:7251)   進程入口
  aeMain                      (ae.c:477)         事件迴圈——redis 的心臟（Ch 6）
    aeProcessEvents           (ae.c:417)         處理一批就緒事件
      connSocketEventHandler  (socket.c:277)     這條連線可讀
        readQueryFromClient   (networking.c)     第 0 段：SOURCE，connRead 讀入 querybuf
          processInputBuffer  (networking.c)     第 1 段：協定解析，切出 argv
            processCommand    (server.c:4206)    第 2 段：lookupCommand 查表
              call            (server.c:3575)    第 3 段：透過函式指標分派
                getCommand    (t_string.c:316)   ← 我們的命令處理，藤到值的地方
```

`GET mykey` 真的回了 `hello`。**backtrace 就是動態版的 data flow**——每一個 stack frame 都是藤流過的一個模組。當你不確定「這條請求到底怎麼進到某函式的」，最快的答案永遠是：在那函式下斷點，跑一次，看 `bt`。這比讀十遍 code 都準。

> **一個直接可複製的 SOP**：追任何一條 data flow，先靜態（rg/cscope 找出賦值點與呼叫鏈）→ 再動態（gdb 在藤頭與藤尾各下一個斷點，`bt` 對照）。靜態告訴你「有哪些可能路徑」，動態告訴你「這次真的走了哪條」。兩者對不上的地方，就是你理解錯了、或有你沒料到的分支——那正是最值錢的發現。

## 對比與取捨

| 手段 | 回答什麼 | 強項 | 弱項 | 何時用 |
|---|---|---|---|---|
| **rg 追賦值點** | 「這個變數/欄位在哪被寫？」 | 快、全域、跨檔 | 被同名騙、不分作用域、看不到執行時真值 | 找藤的「換身分」節點 |
| **cscope -2 / -3**（callee/caller） | 「這函式呼叫誰 / 誰呼叫它？」 | 驗證讀碼理解、反查藤的上下游 | 函式指標分派看不穿（`c->cmd->proc`） | 對照靜態呼叫鏈、找藤頭 |
| **gdb backtrace** | 「這次請求實際走了哪條路？」 | 100% 真實、含函式指標實際目標、可看變數值 | 要能跑、要造得出觸發輸入 | 驗證整條藤、破解 indirection |
| **靜態純讀** | 「這段邏輯做什麼？」 | 不需環境、能看全部分支 | 慢、容易腦補走錯分支 | 讀單一函式內部語義 |

核心取捨是**靜態的「完整但可能不準」 vs 動態的「準但只看到這一次」**。函式指標分派（`c->cmd->proc(c)`）是分水嶺：靜態工具在這裡會斷掉（cscope 不知道 `proc` 指向誰），gdb 一個 `bt` 就穿過去了。**凡是遇到 indirection，優先動態。**

## 踩雷集錦

1. **錯誤直覺：「追 data flow 就是一路 grep 變數名」。**
   正確認識：變數會**換身分**（sds → robj → bytes），換身分之後名字就變了，grep 舊名字追不下去。你要追的是「同一份使用者資料」這個概念，不是某個變數名。在每個轉換函式（`createStringObject`、`lookupKeyRead`、`addReplyBulk`）處**主動切換追蹤目標**，才不會在 `createStringObject` 之後就跟丟。

2. **錯誤直覺：「backtrace 由上往下讀」。**
   正確認識：backtrace 是**呼叫堆疊**，`#0` 是當前最內層、編號越大越外層。追 data flow 要**由大編號往小編號讀**（`main` → `getCommand`），那才是資料流動的方向。習慣性從 `#0` 讀起，你會把因果讀反。

3. **錯誤直覺：「看到 `if` 分支就每條都追」。**
   正確認識：真實 code 一半以上是錯誤處理與邊界分支（`processMultiBulkBuffer` 裡一堆 `PROTO_INLINE_MAX_SIZE`、`PROTO_MBULK_BIG_ARG` 檢查）。**先只追 happy path**（正常 `GET` 成功的那條），把主幹追通，錯誤分支等你需要時再回頭。一開始就每個分支都追，你會在協定 parser 裡淹死。

4. **錯誤直覺：「zero-copy 優化分支不重要，跳過」。**
   正確認識：功能上可以先跳過，但**那正是安全視角最該看的地方**。`createObject(OBJ_STRING, c->querybuf)` 這種「接管而非拷貝」的分支，改變了資料的所有權與生命週期。UAF、double-free 幾乎都出在「某條路徑接管了、另一條路徑又 free 了」的不對稱上。功能讀碼可略，漏洞讀碼必看——這是同一段 code 的兩張臉。

5. **錯誤直覺：「gdb 斷點沒觸發，一定是 code 沒走到」。**
   正確認識：更常見的原因是**編譯器內聯（inline）把符號吃掉了**。redis 用 `-O2` 編，很多小函式被內聯，斷點打在 `getGenericCommand` 可能因內聯而位置不如預期——所以我上面選了斷 `getCommand`（實測乾淨命中）。斷點打不進去時，換斷父函式，或 `info line` 確認那行還在。

## 進階：再往深一層

- **雙向追蹤**：我們是「順流」追（source → sink）。反過來也常用——**逆流**：從一個可疑的 sink（比如某個 `memcpy(dst, src, len)`）往回追 `len` 是哪來的、有沒有經過檢查。逆流追蹤靠的是 cscope 的 `-3`（找 caller）一層層往上爬，配合「這個值在哪被賦值」的 rg。Ch 32 找漏洞時，逆流從 sink 追 taint 是主力技術。

- **跨執行緒的藤**：redis 6+ 有 I/O threading，`readQueryFromClient` 開頭那句 `if (postponeClientRead(c)) return;` 就是把讀取工作丟給 I/O 執行緒的岔路。**藤一旦跨執行緒，靜態追蹤會斷**——資料被塞進一個 list，由另一個執行緒撿起來、呼叫鏈在 source 檔案裡接不起來。這時 gdb 的 `thread apply all bt`（看所有執行緒的 backtrace）是唯一能重新接上藤的工具。Ch 25 讀並發時深入。

- **資料的「影分身」——複製與持久化**：一個寫命令（`SET`）的藤不只走到 keyspace 就結束，它還會分岔。看 `setGenericCommand`（`src/t_string.c:62`）尾段的真實 code：`setKey(c,c->db,key,val,setkey_flags)` 寫進 keyspace 後，緊接著 `server.dirty++`（觸發持久化計數）、`notifyKeyspaceEvent(NOTIFY_STRING,"set",key,c->db->id)`（發 keyspace 通知），命令本身還會被 `call` 上層 propagate 給 replica。**一個寫入 = 一條主藤 + 數條副藤**。我實跑 `SET` 的 backtrace 還揭露一個彩蛋：`setCommand → setGenericCommand → getGenericCommand`——因為 `SET ... GET` 選項複用了 GET 的讀取邏輯。讀寫命令時要有意識地問「這個副作用還衍生了哪些藤」。

- **把追蹤自動化**：手動下斷點追一條藤很慢。進階做法是用 gdb 的 `commands`（斷點自動執行腳本）、或 `ftrace`/`uftrace`/`bpftrace` 一次性錄下整條呼叫鏈（Ch 19 專講）。對「這條路徑到底跑不跑得到、跑幾次」這類問題，tracing 比逐一斷點高效一個數量級。

## 動手練習

在 `~/reading_code_lab/redis` 上做，全部要求貼出你真跑的指令與輸出：

1. **追另一條藤**：把本章對 `GET` 做的，對 `SET mykey hello` 完整做一遍。重點觀察它跟 `GET` 的分岔——在 `setGenericCommand`（`src/t_string.c:62`）裡找出「值寫進 keyspace」的那一行（提示：`setKey`），以及三條副藤（`server.dirty`、`notifyKeyspaceEvent`、複製）。用 gdb 斷 `setCommand`，貼出 backtrace。

2. **驗證身分轉換點**：用 gdb 斷在 `getGenericCommand`，用 `p (char*)((robj*)c->argv[1])->ptr` 印出 key 的實際字串，確認它就是你送的 `mykey`。（注意 `-O2` 下某些欄位或 frame 可能 `optimized out`，若印不出改斷更外層 frame，或 `p c->argv[1]` 先看物件。）

3. **逆流一次**：挑 `addReplyBulkCBuffer`（一個 reply sink），用 `cscope -d -L -3 addReplyBulkCBuffer` 往上追三層 caller，畫出「哪些命令用了這個 sink」的小圖。體會逆流追蹤。

4. **找一條會斷的藤**：用 rg 找 `postponeClientRead` 的定義，讀懂 I/O threading 那條岔路為什麼會讓靜態追蹤斷掉。寫兩句話說明「這時你會改用什麼工具接回藤」。

## 本章重點整理

- data flow 追蹤 = 挑一個具體資料，跟它穿越整個系統；比從頭讀到尾高效得多。
- 資料在系統裡不斷**換身分**（bytes → sds → robj → cmd → robj → bytes），追蹤 = 在每個轉換函式處切換追蹤目標。
- `GET` 的藤：`readQueryFromClient`(source) → `processMultiBulkBuffer`(parse) → `lookupCommand`(查表) → `call`→`getCommand`(dispatch) → `lookupKeyRead`(取值) → `addReplyBulk`(sink)。
- **source/sink 眼鏡**：功能追蹤與漏洞追蹤是同一條路，只是問的問題不同；每道「查表/驗證/拒絕」都是 taint 被消毒或收窄的點。
- 方法論：**先靜態**（rg 找賦值點、cscope 找呼叫鏈）**再動態**（gdb 斷點 + backtrace 驗證）。兩者對不上處最值錢。
- backtrace **由外層（大編號）往內層（小編號）讀**才是資料流方向；函式指標分派用 gdb 一秒穿透。

## 自我檢核

- [ ] 不看筆記，我能畫出 `GET` 的資料流五段圖，並說出每一段「資料換了什麼身分、誰負責轉換」。
- [ ] 我能解釋為什麼「一路 grep 變數名」追不完一條藤，該怎麼辦。
- [ ] 拿到一個 backtrace，我能立刻說出該從哪一端讀、為什麼，並把它翻譯成一條 data flow。
- [ ] 我能說出遇到函式指標分派（`c->cmd->proc`）時，靜態工具為何會斷、該換什麼工具。
- [ ] 我能用一句話解釋 source / sink，並指出 `GET` 這條藤上的 source 與 sink 各是哪個函式。

追一條藤能讓你看懂「一次請求怎麼流」，但一條藤只是圖上的一條線。下一章我們退一步，看**控制流的全貌**——用 cflow 靜態產出呼叫樹、用 gdb/uftrace 抓真實走過的路徑，並學會在條件分支爆炸時「只跟你關心的那一條」。

→ [Ch 9 控制流與 call graph](./09-control-flow-call-graph.md)
