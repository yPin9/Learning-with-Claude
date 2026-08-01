# 練習 B — 追一個功能的完整路徑

> **目標**：把 Part 3 整套工具鏈（rg、ctags/cscope、gdb、uftrace）**組合起來**，追一個真實命令從「socket 上收到 bytes」到「回覆送出」的完整路徑，產出三樣可交付物：**call graph（呼叫圖）**、**data flow 圖（資料流）**、**每個關鍵函式一句話**。這是 Part 3 的畢業考——單一工具都不夠，你要靠工具接力才追得穿一條真實路徑。

## 背景

你已經學會了每一把武器：rg 快速定位、cscope 反查呼叫者、clangd 精準跳轉、gdb 動態看真實控制流、uftrace 看函式呼叫樹。但真實讀碼從來不是「用一把工具」——是**用對的工具解對的子問題，再把答案拼成一張完整的路徑圖**。

這個練習模擬一個極常見的真實任務：**「我要改 redis 的 GET 命令行為，但我得先完全搞懂一個 GET 從進來到出去經過了什麼。」** 這正是 Ch 11「從 50 萬行收斂到你要改的 200 行」的具體演練——只是這次你要追的不是「哪 200 行」，而是「一整條執行路徑」。

追一條完整路徑的難點在於它跨越多個層次：**協定解析（bytes → command）→ 命令分派（command → handler）→ 業務邏輯（handler 做事）→ 回覆序列化（result → bytes）。** 沒有一把工具能一次看穿全部——靜態工具看得到結構但不知道實際走哪條；gdb 看得到真實路徑但逐步很慢；uftrace 看得到函式樹但要能插樁。你要學會**在正確的層次切換正確的工具**。

## 任務規格

在 redis 沙包上，追一個命令的完整路徑。**建議追 `GET key`**（讀路徑，乾淨、經典），也可以選 `SET key val`（寫路徑）。若你想挑戰,可換 CPython 追一個 bytecode 的執行,或 git 追一個 `git add` 的路徑——但參考解答以 redis GET 為準。

### 要交付的三樣東西

**1. Call graph（呼叫圖）**：從進入點到最深的業務邏輯,列出這條路徑上依序呼叫的關鍵函式,標出每個函式在哪個檔案/大概行號。可以是文字縮排樹,也可以用 graphviz 畫。

**2. Data flow 圖（資料流）**：追一個關鍵資料（GET 的 key 字串,或回傳的 value）怎麼在這條路徑上流動與變形——從 socket buffer 裡的 raw bytes,到解析成 `robj`,到查表得到 value,到序列化回 RESP。

**3. 每個關鍵函式一句話**：路徑上每個關鍵函式,用一句話說清「它負責什麼」。這是你真正「讀懂」的證明——講不出一句話,就是還沒懂。

### 工具使用要求（必須綜合）

- **rg**：定位命令的 handler 函式、找它在命令表裡怎麼註冊的。
- **cscope**：反查某個核心函式「被誰呼叫」,補全靜態呼叫關係。
- **gdb**：動態驗證真實路徑（下斷點 + backtrace）——這是**確認靜態猜測**的關鍵一步。
- **uftrace**（選,若你願意重編 `-pg` 版）：看完整函式呼叫樹,或用它驗證你的 call graph。

**核心紀律**：先用靜態工具（rg/cscope）**猜**出路徑,再用 gdb **驗**真實路徑。靜態給你假設,動態給你事實(這正是 Ch 10 + Ch 18 的合流)。

## 期望輸出範例

你的 call graph 大概會長這樣(局部):

```
main
└─ aeMain                          事件迴圈(心臟)
   └─ aeProcessEvents
      └─ readQueryFromClient       socket 可讀 → 讀進 query buffer
         └─ processInputBuffer     解析 RESP → 填 c->argv[]
            └─ processCommand      查命令表 + 一堆守衛檢查
               └─ call
                  └─ getCommand    GET 的 handler
                     └─ ...        (往下追到查表)
```

data flow 大概:

```
socket bytes: "*2\r\n$3\r\nget\r\n$3\r\nfoo\r\n"
   │ readQueryFromClient 讀進 c->querybuf
   ▼
c->argv[] = [ robj("get"), robj("foo") ]   ← processInputBuffer 解析 RESP
   │ getCommand 拿 c->argv[1]
   ▼
查 db 的 hash table,得到 value robj
   │ addReplyBulk 序列化
   ▼
socket bytes: "$5\r\nhello\r\n"   ← 回覆
```

## 卡住提示

- **找不到 GET 的 handler?** rg 搜 `getCommand`(命令 handler 慣例是 `<命令>Command`)。找它在哪個命令表註冊,rg `getCommand` 在 `commands.def` 裡的那一行。
- **不確定 `processCommand` 之後怎麼到 `getCommand`?** 中間隔著一個 `call()`——別漏了它。用 gdb 在 `getCommand` 下斷、`bt` 一看就全清楚,不要純靠靜態猜(你會漏掉 `call` 這層)。
- **gdb 斷點沒觸發?** 確認你送的命令真的觸發那個 handler(先 `set foo x` 建 key,再 `get foo`)。用完整路徑 `./src/redis-cli` 不是裸 `redis-cli`。
- **`<optimized out>` 看不到參數?** 正常(Ch 18),`-O2` 的代價。函式呼叫鏈(bt)不受影響,夠你畫 call graph;真要看變數重編 `-O0`。
- **想追到「查表」那一層但迷路?** GET 的核心讀取鏈是 `getCommand → getGenericCommand → lookupKeyReadOrReply → lookupKeyRead → lookupKeyReadWithFlags → lookupKey → dbFind`。在 `lookupKeyReadWithFlags` 下斷 + `bt` 驗證。

## 實作步驟建議

### Step 1：rg 定位 handler 與命令註冊(靜態,秒級)

先找 GET 的 handler 定義,再找它怎麼被註冊進命令表——這回答「命令名 `get` 怎麼對應到函式」。

### Step 2：cscope/rg 靜態勾勒路徑上半段(猜)

從 `processCommand` 往下猜:它怎麼從命令名找到 handler、中間經過什麼。這一步你會**猜**出一條路徑,但可能漏環節(如 `call`)。

### Step 3：gdb 動態驗證真實路徑(驗)

在 `getCommand` 下斷,送一個真的 GET,`bt` 看**完整真實呼叫鏈**——這一步會補全你靜態漏掉的環節,把「猜的路徑」變成「確定的路徑」。

### Step 4：gdb 往下追到查表層

在 `lookupKeyReadWithFlags` 下斷,`bt`,看 GET handler 內部怎麼一路呼叫到真正的 hash table 查詢。

### Step 5：追 data flow

盯著 key 字串:它在 `c->argv[1]->ptr`、傳進 `lookupKey`、查 `dbFind`。盯著回傳 value:`lookupKeyReadOrReply` 拿到 `robj`、`addReplyBulk` 序列化回 socket。

### Step 6：組裝三樣交付物

把上面的觀察組成 call graph、data flow、每函式一句話。

## 完整參考解答

**自己追完再看!** 這裡是我實際在 redis 7.4.0 沙包上,用 rg + cscope + gdb 真跑追出來的完整路徑(所有輸出照抄真跑結果)。

<details>
<summary>點開完整參考解答</summary>

### Step 1 真跑：rg 定位 handler 與註冊

```
$ rg -n "void getCommand" src/t_string.c
316:void getCommand(client *c) {
```

`getCommand` 在 `src/t_string.c:316`。它怎麼被註冊成 `get` 命令的 handler?查命令表 `commands.def`(從 JSON 自動生成):

```
$ rg -n "getCommand" src/commands.def
11209:{MAKE_CMD("get","Returns the string value of a key.","O(1)","1.0.0",...,getCommand,2,CMD_READONLY|CMD_FAST,...)...}
```

**這一行是關鍵**:`MAKE_CMD("get", ..., getCommand, 2, CMD_READONLY|CMD_FAST, ...)`——命令名 `"get"` 綁定到函式 `getCommand`,arity 是 2(命令 + 一個 key),旗標 `CMD_READONLY|CMD_FAST`。

**這裡有個重要的讀碼發現**:redis **沒有**一個 `registerCommand(name, proc)` 這樣的執行時註冊函式(這正是 Ch 20 我們戳破 LLM 幻覺的那個例子)。命令表是一張**靜態生成的大陣列**,`processCommand` 靠查這張表(用命令名當 key)找到對應的 handler 函式指標。理解這點,你就理解了 redis 命令分派的骨架。

### Step 3 真跑：gdb 驗證完整真實路徑

在 `getCommand` 下斷,送一個真的 GET,取 backtrace(真實輸出照抄):

```gdb
break getCommand
commands
  bt
  print (char*)c->argv[1]->ptr
end
run --port 7801 --save ''
```

```bash
./src/redis-cli -p 7801 set foo hello    # 先建 key
./src/redis-cli -p 7801 get foo          # 觸發斷點
```

gdb 真實 backtrace:

```
#0  getCommand (c=0x7ffff7935700) at src/t_string.c:316
#1  0x00005555555f9a15 in call (c=c@entry=0x7ffff7935700, flags=flags@entry=3) at src/server.c:3575
#2  0x00005555555fba78 in processCommand (c=0x7ffff7935700) at src/server.c:4206
#3  0x000055555562249c in processCommandAndResetClient (c=0x7ffff7935700) at src/networking.c:2505
#4  processInputBuffer (c=0x7ffff7935700) at src/networking.c:2613
#5  0x0000555555622a68 in readQueryFromClient (conn=<optimized out>) at src/networking.c:2759
#6  0x0000555555738101 in callHandler (handler=<optimized out>, conn=0x7ffff78295c0) at src/connhelpers.h:58
#7  connSocketEventHandler (el=<optimized out>, fd=<optimized out>, ...) at src/socket.c:277
#8  0x00005555555e61f2 in aeProcessEvents (flags=27, eventLoop=0x7ffff782a140) at src/ae.c:417
#9  aeMain (eventLoop=0x7ffff782a140) at src/ae.c:477
#10 0x00005555555daf7b in main (argc=5, argv=<optimized out>) at src/server.c:7251

$1 = 0x7ffff785e943 "foo"
```

**這一張 bt 把整條路徑的骨架一次確定了。** 注意兩件靜態很難一次看清的事:(a) `processCommand`(#2) 到 `getCommand`(#0) 之間隔著一個 **`call`(#1, server.c:3575)**——這是純靜態猜路徑最容易漏掉的一環;(b) key 字串 `"foo"` 此刻就在 `c->argv[1]->ptr`,證實了 RESP 已被解析成 `c->argv[]`。

### Step 4 真跑：往下追到查表層

在 `lookupKeyReadWithFlags` 下斷,取 backtrace,看 `getCommand` 內部怎麼一路呼叫到真正的 hash table 查詢:

```
#0  lookupKeyReadWithFlags (...) at src/db.c:140
#1  lookupKeyRead (...) at src/db.c:146
#2  lookupKeyReadOrReply (...) at src/db.c:164
（往上是 getGenericCommand → getCommand → call → ...）
```

再用 rg 補完最底層(gdb 停在 db.c:140 `return lookupKey(db, key, flags);`):

```
$ rg -n "robj \*lookupKey\b|dbFind" src/db.c
75:robj *lookupKey(redisDb *db, robj *key, int flags) {
76:    dictEntry *de = dbFind(db, key->ptr);
```

`lookupKey`(db.c:75)呼叫 `dbFind(db, key->ptr)`(db.c:76)——**這就是真正查 hash table 的地方**,拿 key 字串去 dict 裡找對應的 `dictEntry`。

回覆側,rg 查 `getGenericCommand` 怎麼把 value 送回去:

```
$ rg -n "lookupKeyReadOrReply|addReplyBulk" src/t_string.c
305:    if ((o = lookupKeyReadOrReply(c,c->argv[1],shared.null[c->resp])) == NULL)
312:    addReplyBulk(c,o);
```

`getGenericCommand`(t_string.c:302)先 `lookupKeyReadOrReply` 拿到 value `robj *o`,再 `addReplyBulk(c, o)`(t_string.c:312)把它序列化成 RESP 送回 client。查不到 key 時(305 行 == NULL)回 null reply。

### 交付物 1：完整 call graph

```
main                              [server.c:6917]      進程入口、初始化
└─ aeMain                         [ae.c:474]           事件迴圈(redis 的心臟)
   └─ aeProcessEvents             [ae.c:417]           一輪事件處理,發現 fd 可讀
      └─ connSocketEventHandler   [socket.c:277]       socket 可讀事件派發
         └─ callHandler → readQueryFromClient  [networking.c:2759]  讀 socket → query buffer
            └─ processInputBuffer          [networking.c:2613]  解析 RESP → 填 c->argv[]
               └─ processCommandAndResetClient [networking.c:2505]
                  └─ processCommand        [server.c:4206]  查命令表 + 守衛檢查
                     └─ call               [server.c:3575]  真正呼叫 handler(易漏!)
                        └─ getCommand      [t_string.c:316] GET 的 handler
                           └─ getGenericCommand        [t_string.c:302]
                              ├─ lookupKeyReadOrReply  [db.c:164]  查 key、找不到就回覆 null
                              │  └─ lookupKeyRead      [db.c:145]
                              │     └─ lookupKeyReadWithFlags [db.c:138]
                              │        └─ lookupKey    [db.c:75]
                              │           └─ dbFind    [db.c:76 呼叫]  ← 真正查 hash table
                              └─ addReplyBulk          [t_string.c:312]  序列化 value 回 RESP
```

### 交付物 2：data flow 圖

```
① socket 上的 raw bytes(RESP 協定)
   "*2\r\n$3\r\nget\r\n$3\r\nfoo\r\n"
        │  readQueryFromClient: recv 進 c->querybuf(SDS 字串)
        ▼
② query buffer(未解析)
        │  processInputBuffer: 依 RESP 切成陣列元素
        ▼
③ c->argv[] = [ robj("get"), robj("foo") ]   argc=2
        │  processCommand: 用 argv[0]="get" 查命令表 → c->cmd = getCommand 的表項
        │  call → getCommand → getGenericCommand: 取 c->argv[1]->ptr = "foo"
        ▼
④ key 字串 "foo" 傳進 lookupKey → dbFind(db, "foo")
        │  在 db 的 dict(hash table)裡查 "foo"
        ▼
⑤ 查到 value robj *o(內容 "hello")
        │  addReplyBulk(c, o): 依 RESP 格式序列化
        ▼
⑥ 回覆 socket bytes
   "$5\r\nhello\r\n"
```

**key 的變形軌跡**:raw bytes → query buffer(SDS)→ `robj`(在 argv[])→ 查表的 lookup key。**value 的軌跡**:dict 裡的 `robj` → `addReplyBulk` 序列化 → socket bytes。這條 data flow 就是「一個 GET 到底把什麼資料搬去哪、變成什麼」的完整答案。

### 交付物 3：每個關鍵函式一句話

| 函式 | 一句話 |
|---|---|
| `aeMain` | redis 的事件迴圈心臟,無限跑 `aeProcessEvents` 等事件 |
| `readQueryFromClient` | socket 可讀時,把 bytes 讀進 client 的 query buffer |
| `processInputBuffer` | 依 RESP 協定把 query buffer 解析成 `c->argv[]` 命令陣列 |
| `processCommand` | 用命令名查命令表拿到 handler,跑一堆守衛檢查(權限/arity/記憶體…) |
| `call` | 真正呼叫命令 handler 的那一層,順便做統計/傳播/AOF 等 |
| `getCommand` | GET 命令的入口 handler,轉呼 `getGenericCommand` |
| `getGenericCommand` | GET 的實際邏輯:查 key、找不到回 null、找到就序列化回覆 |
| `lookupKeyReadOrReply` | 讀取一個 key,查不到就直接幫你回覆(reply)一個 null |
| `lookupKeyRead` / `...WithFlags` | 以「讀」語意查 key(會處理過期等副作用) |
| `lookupKey` | 真正去 db 的 dict 查 key,回傳 value 的 `robj` |
| `dbFind` | 在 hash table 裡用 key 字串找 `dictEntry`——最底層的查表 |
| `addReplyBulk` | 把一個 `robj` value 依 RESP 格式序列化,寫進回覆 buffer |

</details>

## 測試用例(驗證你追對了)

追完後,你應該能回答這些——答不出就是還沒真懂:

1. `processCommand` 和 `getCommand` 之間隔著哪個函式?(答:`call`。純靜態最容易漏它,gdb 的 bt 一看就有。)
2. GET 的 key 字串,在被查表之前,存在哪個欄位?(答:`c->argv[1]->ptr`。)
3. redis 有沒有一個「執行時註冊命令 handler」的函式?命令名怎麼對應到函式?(答:沒有;靠 `commands.def` 裡靜態生成的命令表 + `MAKE_CMD` 把 `"get"` 綁到 `getCommand`。)
4. 真正查 hash table 的最底層函式是哪個?(答:`dbFind`,被 `lookupKey` 呼叫。)
5. value 是怎麼變回 socket 上的 bytes 的?(答:`addReplyBulk` 依 RESP 序列化。)

## 延伸挑戰

- **追 SET(寫路徑)**:重做一次追 `SET foo bar`。你會發現路徑前半段(到 `processCommand → call`)完全一樣,分歧點在 handler(`setCommand → setGenericCommand`)。順手驗證 Ch 18 我們看到的 `server.dirty++` 就在寫路徑上。畫出 GET 與 SET 路徑的**共同前綴 + 分歧點**——這是理解「命令分派」抽象的關鍵。
- **uftrace 全樹驗證**:重編一份 `-pg` 的 redis(`make CFLAGS="-pg -g"`),用 uftrace record 對一個 GET 抓完整函式樹,和你手工用 gdb 拼的 call graph 對照——看你有沒有漏掉任何中間函式。這是「靜態猜 + gdb 驗 + uftrace 全覆蓋」三重交叉驗證。
- **strace 補協定層**:對 `redis-cli get foo` 跑 `strace -e trace=network`(Ch 19),把 wire 上的 `sendto("*2\r\n$3\r\nget...")` / `recvfrom("$5\r\nhello\r\n")` 接到你 data flow 圖的兩端——這樣你的路徑圖就從「函式」一路貫穿到「syscall」,完整無缺口。
- **換個 codebase**:對 CPython 追一個 `len()` 內建函式的執行路徑(從 bytecode `CALL` 到 `builtin_len`),或對 git 追 `git hash-object` 的路徑。體會「同一套追蹤方法論,換個專案照樣用」——這才是這門課真正要你帶走的。

## 自我檢核

- [ ] 我能不能說清楚「先用 rg/cscope 猜路徑、再用 gdb 驗真實路徑」為什麼是對的順序,以及純靠靜態會漏掉什麼(如 `call` 層)?
- [ ] 我的 call graph 是不是真的一路追到了「查 hash table」的最底層,而不是停在 handler 就收工?
- [ ] 我的 data flow 圖能不能說清 key 和 value 各自怎麼變形(bytes ↔ SDS ↔ robj ↔ dict entry)?
- [ ] 我能不能對路徑上每個關鍵函式講出一句話?(講不出的那幾個,就是我還沒真懂、要回去補的。)
- [ ] 我有沒有真的綜合用了多把工具(不是只用 gdb,也不是只用 rg),讓它們互相補位、互相驗證?
- [ ] 換一個我沒追過的命令(如 `SET`、`INCR`),我有沒有信心用同一套流程獨立追出來?

追穿一條完整路徑,你就掌握了讀懂任何「輸入→處理→輸出」系統的通用手法。Part 3 的工具鏈到此收尾。接下來 Part 4 換個維度:不再是「用什麼工具」,而是「讀懂特定**結構**」——build system、巨集、indirection、狀態機、並發、C++ 複雜性、kernel 慣例。第一站,我們處理一個你追路徑時可能已經隱約撞到的東西:**這坨 code 到底是怎麼被編出來的?**

→ [Ch 21 讀懂 build system](./21-reading-build-systems.md)
