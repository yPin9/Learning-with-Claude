# Ch 23 — 讀懂 indirection（動態 dispatch）

> **目標**：學會讀「呼叫在哪決定」的 code。當你看到 `c->cmd->proc(c)`、`fe->rfileProc(...)`、`mt->rdb_load(...)`，靜態閱讀走到這裡就斷了——呼叫寫在這裡，實際跳去哪個函式卻要執行期才知道。本章拆解 C 裡各種 indirection（function pointer、callback、手寫 vtable、dispatch table、connection 抽象層），教你兩招破解：**靜態找「註冊點」**（誰把函式塞進那個欄位/表），與**動態用 gdb 抓「這一跳實際落在哪」**。redis 的命令分派是最佳教材。

> **環境**：WSL2 Ubuntu 22.04，沙包 `~/reading_code_lab/redis`（redis 7.4.0，已編出 `redis-server`）。本章的 gdb backtrace、`disassemble`、命令表節錄都是真跑後照抄；gdb 用「redis-server 當 gdb 的 child」方式跑（WSL 上 ptrace attach 較麻煩，讓 gdb 直接 `run` 最穩）。

## 為什麼 indirection 讓靜態閱讀失效

先建立心智模型。一般的函式呼叫，呼叫端寫死了目標：

```c
foo(x);        // 靜態可知：跳去 foo，ctags/cscope 一查就到
```

indirection 打斷了這條線——呼叫端只知道「呼叫某個欄位裡存的函式」，那個欄位存誰要執行期才定：

```c
cmd->proc(c);  // 跳去哪？取決於執行期 cmd 這個指標指向的 struct 裡 proc 欄位存了誰
```

```
   靜態閱讀能看到的                  斷點（indirection）
 ┌──────────────────────┐         ┌────────────────────────────┐
 │ processCommand(c)     │         │  cmd->proc(c)  ← 這裡跳去哪？│
 │   → lookupCommand()   │  ...→   │  取決於：cmd 指向哪個 entry │
 │   → call(c)           │         │  entry.proc 在哪被填的？    │
 └──────────────────────┘         └────────────────────────────┘
                                     ↑ 靜態讀到這裡就斷了
```

這種「呼叫目標存在資料裡」的設計，是 C 實現多型/外掛/回呼的唯一手段（C 沒有 class、沒有 virtual）。你會在任何成熟 C 專案撞到它：命令表、event loop 的 callback、driver 的 ops 結構、plugin 系統。**讀不懂 indirection，你就永遠追不到「這個請求最後到底執行了哪段 code」。**

破解的兩把鑰匙，本章反覆用：

1. **找註冊點（靜態）**：目標函式一定在某處被「塞進」那個欄位/表。找到賦值點（`x.proc = getCommand` 或 build 時生成的表），你就知道所有可能的目標。
2. **抓實際跳轉（動態）**：在 indirection 那一行下 gdb 斷點，`print` 那個函式指標、`bt` 看呼叫鏈，直接看到「這一次跳去了誰」。這是 indirection 讀碼無可取代的一招。

## indirection 的六種常見形式

先認臉。讀 C 會遇到的 indirection 大致這幾種，破解思路一致（找註冊點 + gdb 驗）：

| 形式 | 長相 | 典型場景 | redis 實例 |
|---|---|---|---|
| **裸 function pointer** | `void (*fp)(int); fp(3);` | 可替換的策略 | `dictType` 的 hash 函式 |
| **callback / 回呼** | `register(fd, my_cb)` | event/非同步 | `connSetReadHandler(conn, readQueryFromClient)` |
| **手寫 vtable**（struct of fn ptr） | `obj->ops->save(obj)` | C 版多型 | `moduleType`（`rdb_load`/`rdb_save`...） |
| **dispatch table**（表 → 函式） | `table[cmd].proc(args)` | 命令/opcode 分派 | `redisCommandTable` → `cmd->proc(c)` |
| **C++ 虛函式** | `obj->virt()` | 繼承多型 | （Ch 26 深入） |
| **plugin / 動態載入** | `dlsym(h, "init")()` | 執行期擴充 | redis module 系統 |

它們在組語層都收斂成同一件事：**間接呼叫（`call *reg`）**——跳到暫存器/記憶體裡存的位址，而非寫死的位址。本章結尾會用 `disassemble` 給你看這個 `call *`。

## 實戰一：redis 命令分派——dispatch table 的完整解剖

redis 每個命令（`GET`/`SET`/`LPUSH`...）對應一個處理函式。核心資料結構（真實節錄 `server.h`）：

```c
typedef void redisCommandProc(client *c);      // server.h:2245

struct redisCommand {
    ...
    redisCommandProc *proc;   // server.h:2356  ← 命令實作的函式指標
    ...
};
```

分派的心臟只有一行（`server.c:3575`，在 `call()` 函式裡）：

```c
c->cmd->proc(c);
```

`c->cmd` 是這個 client 當前要執行的命令 entry，`->proc` 是那個 entry 裡存的函式指標。**整個 redis 的命令執行，全靠這一行間接呼叫。** 問題來了：`proc` 到底存了誰？靜態讀 `call()` 看不出來，要找**兩件事**——註冊點（表在哪、怎麼填）與 lookup（`c->cmd` 怎麼定的）。

### 找註冊點：表是 build 時生成的

`grep proc =` 找不到明顯的賦值，因為 redis 的命令表是**生成**的（回顧 Ch 21）。表在 `commands.def`，`GET` 的 entry（真實節錄 `commands.def:11209`）：

```c
{MAKE_CMD("get","Returns the string value of a key.",..., getCommand, 2, CMD_READONLY|CMD_FAST, ...), .args=GET_Args},
```

看到 `getCommand` 了嗎？它被當參數塞進 `MAKE_CMD` 巨集，最終填進 `redisCommand.proc`。這張表由 `populateCommandTable()`（`server.c:3075`）在啟動時載入到一個 dict。所以：

- **所有可能的 `proc` 目標**，就是 `commands.def` 裡每個 entry 的那個函式名（`getCommand`/`setCommand`/`clusterCommand`/...）。這是「找註冊點」給你的完整答案：靜態就能列出全部候選。
- **`c->cmd` 怎麼定的**：`processCommand()` 呼叫 `lookupCommand(c->argv, c->argc)`（`server.c:3200`），用命令名字（`argv[0]`）去那個 dict 查出對應 entry，存進 `c->cmd`。

到此靜態鏈補完了：`argv[0]="get"` → `lookupCommand` 查表 → `c->cmd` 指向 GET 的 entry → `c->cmd->proc` 是 `getCommand` → `c->cmd->proc(c)` 跳進 `getCommand`。但這是**推理**——推理可能錯（表載錯、alias、rename）。要確認，動態驗。

## 實戰二：gdb 抓實際跳轉——把推理變成鐵證

我們讓 gdb 直接 `run` redis-server（當 child），在 `getCommand` 下斷點，另開一個延遲的 client 送 `GET`，看斷下來時的實況。真跑的 gdb 腳本：

```
$ cd ~/reading_code_lab/redis/src
$ cat > /tmp/gdbcmds.txt <<'EOF'
set pagination off
break getCommand
run --port 7803 --save ""
echo \n### IN getCommand ###\n
print c->cmd->fullname
print c->cmd->proc
bt 5
kill
quit
EOF
# 另一個背景 job：等 server 起來後送 GET
$ ( sleep 6; ./redis-cli -p 7803 set foo bar; ./redis-cli -p 7803 get foo ) &
$ gdb -batch -x /tmp/gdbcmds.txt ./redis-server
```

真實輸出（節錄斷點命中後）：

```
### IN getCommand ###
$1 = (sds) 0x7ffff7820e09 "get"
$2 = (redisCommandProc *) 0x55555564ef30 <getCommand>
#0  getCommand (c=0x7ffff7935700) at t_string.c:316
#1  0x00005555555f9a15 in call (c=..., flags=3) at server.c:3575
#2  0x00005555555fba78 in processCommand (c=0x7ffff7935700) at server.c:4206
#3  0x000055555562249c in processCommandAndResetClient (c=...) at networking.c:2505
#4  processInputBuffer (c=0x7ffff7935700) at networking.c:2613
```

這幾行是 indirection 讀碼的黃金證據：

- **`$1 = ... "get"`**：這次分派的命令名確實是 `get`。
- **`$2 = (redisCommandProc *) 0x55555564ef30 <getCommand>`**：`c->cmd->proc` 這個函式指標，執行期實際存的就是 `getCommand` 的位址。**推理被證實**——那一行間接呼叫這次跳進了 `getCommand`。
- **backtrace**：`getCommand` ← `call` **@ server.c:3575**（正是 `c->cmd->proc(c)` 那行！）← `processCommand` ← `processCommandAndResetClient` ← `processInputBuffer`。整條「網路資料進來 → 解析 → 分派 → 執行」的路徑，一次 `bt` 全看到了。

**這就是動態抓跳轉的威力**：`c->cmd->proc(c)` 靜態看是個謎，gdb 一斷，謎底（`getCommand`）與完整呼叫鏈同時現形。換個命令（`LPUSH`）重跑，`$2` 會變成 `<lpushCommand>`——同一行 code，不同執行期目標，這正是 indirection 的本質。

## 實戰三：組語層看 indirection——`call *`

想徹底理解「為什麼靜態讀不到目標」，看那一行在組語長怎樣。`disassemble /s call` 找到 `server.c:3575` 對應的指令（真實輸出節錄）：

```
3575	    c->cmd->proc(c);
   0x...a544d <+413>:	mov    0x80(%r14),%rax      ; rax = c->cmd（取 client 的 cmd 欄位）
   0x...a5454 <+420>:	call   *0x60(%rax)          ; 呼叫 *(cmd + 0x60) —— 即 cmd->proc
```

關鍵是 **`call *0x60(%rax)`**：這是**間接呼叫**——目標位址是「`rax`（也就是 `cmd`）加 0x60 這個記憶體位置**裡存的值**」。`0x60` 就是 `proc` 欄位在 `redisCommand` struct 裡的位移。

對照一般呼叫 `call 0x1234`（直接寫死目標），`call *0x60(%rax)` 的目標**印在資料裡、執行期才確定**。這就是為什麼：

- **反組譯 / 靜態工具看到 `call *...` 就斷了**——它不知道 `rax+0x60` 執行期會是什麼。逆向 binary 時，dispatch table / vtable 的 `call *` 是最難追的點之一，跟讀 source 遇到 `cmd->proc(c)` 是同一個困難的一體兩面（呼應 Ch 2「讀碼即逆向」）。
- **只有兩條路能知道目標**：靜態找出「誰會往那個記憶體位置寫函式位址」（註冊點），或動態在那條指令斷下來看 `rax+0x60` 的實際值（gdb）。本章前兩招正是這兩條路。

## 實戰四：callback（reactor）與手寫 vtable——同一套讀法

命令表是「表 → 函式」，另外兩種 indirection 讀法完全一樣，快速過。

**callback（event loop 的 file event）**：redis 的 `ae` event loop 每個 fd 事件存一對函式指標（真實節錄 `ae.h`）：

```c
typedef void aeFileProc(struct aeEventLoop *eventLoop, int fd, void *clientData, int mask);
typedef struct aeFileEvent {
    aeFileProc *rfileProc;   // 可讀時呼叫
    aeFileProc *wfileProc;   // 可寫時呼叫
} aeFileEvent;
```

**dispatch 點**（`ae.c:417`）：

```c
fe->rfileProc(eventLoop, fd, fe->clientData, mask);   // fd 可讀時，跳去註冊的 callback
```

**註冊點**（`networking.c:123`）：

```c
connSetReadHandler(conn, readQueryFromClient);
```

於是「某 client socket 可讀 → `ae.c:417` 的 `fe->rfileProc(...)` → 跳進 `readQueryFromClient`」。讀法一模一樣：dispatch 點看到函式指標欄位（`rfileProc`），去找誰註冊它（`connSetReadHandler(..., readQueryFromClient)`），要確認就 gdb 在 `readQueryFromClient` 斷點 `bt`。這條路的下一步就接回實戰一的 `processInputBuffer`——整個 redis 的請求流，就是 callback 分派 + 命令表分派串起來的。

**手寫 vtable（module type）**：redis module 用一個 struct 裝一組函式指標，是 C 版的「介面/多型」（真實節錄 `module.c:6943`）：

```c
struct moduleType {
    moduleTypeLoadFunc rdb_load;    // 這個型別怎麼從 RDB 載入
    moduleTypeSaveFunc rdb_save;    // 怎麼存進 RDB
    ...
};
```

RDB 載入時 redis 呼叫 `mt->rdb_load(...)`，跳去該 module 註冊的實作。這就是 C 的「一個 struct 存一整組 ops，靠不同物件填不同函式達成多型」——Linux kernel 的 `file_operations`、`struct net_device_ops` 是同一個模式（Ch 27 會回來）。讀法還是那兩招：找 `mt->rdb_load = ...` 的註冊點（`module.c:6974` 的 `mt->rdb_load = tms->rdb_load`）、gdb 在實作斷點驗。

## 對比與取捨

| 追 indirection 目標的手段 | 給你什麼 | 限制 |
|---|---|---|
| 找註冊點（`grep '\.proc ='` / 生成表 / `Set*Handler`） | **所有可能**的目標，靜態、離線 | 生成/巨集填的表 grep 不到（要展開，見 Ch 22）；動態改寫的欄位看不到 |
| cscope「查符號被賦值處」 | 誰把函式塞進欄位 | 對函式指標賦值的辨識不完美 |
| **gdb 在 dispatch 行斷點 + `print fp` + `bt`** | **這一次實際跳去誰** + 完整呼叫鏈 | 只看到你觸發到的路徑；要能跑起來 |
| `disassemble` 看 `call *` | 確認是間接呼叫、欄位位移 | 不告訴你目標；只證明「靜態不可知」 |
| clangd「find references」 | 函式被取址的所有地方（含當 callback 傳） | 需 compile_commands.json；C 的函式指標仍可能漏 |

**策略**：先靜態找註冊點列出「候選目標集合」（多數情況這就夠：redis 所有 `proc` 就是 `commands.def` 裡那些函式）；當候選太多、或懷疑推理有誤、或要確認「這個特定請求」走哪條，就 gdb 在 dispatch 行斷點看實際 `fp` 值與 `bt`。**靜態給你地圖，動態給你這一趟的實際路線。**

## 踩雷集錦

1. **錯誤直覺：「`cmd->proc(c)` 我 ctags 跳一下就知道跳去哪」。** 正確認識：ctags/cscope 對 `proc` 只會跳到**欄位宣告**（`redisCommandProc *proc;`），不是實際目標。indirection 的目標**不在呼叫端的語法裡**，要去找註冊點或 gdb 抓。這是新手追命令流最常卡死的點。

2. **錯誤直覺：「找不到 `.proc = getCommand` 的賦值，所以這表是空的/我搞錯了」。** 正確認識：表可能是 build 時生成（redis `commands.def`）或巨集填的（`MAKE_CMD(...)`）。**先懷疑生成/巨集**（Ch 21/22），去讀生成器或展開巨集，別以為表憑空存在。

3. **錯誤直覺：「gdb 斷在 `getCommand` 就代表所有 GET 都走這」。** 正確認識：你只證明了「你觸發的那次」。alias、rename、`MULTI`/`EXEC` 包裝、Lua 呼叫、cluster 轉發都可能讓同一個命令名走不同 proc。要全面，得列註冊點的候選集合，動態只驗特定路徑。

4. **錯誤直覺：「函式指標欄位只在初始化時填一次」。** 正確認識：很多欄位執行期會被改（callback 換手、狀態機切 handler）。redis 的 `connSetReadHandler` 就會在不同階段把 read handler 換掉（握手 → 正常讀）。**同一個欄位在不同時刻可能指向不同函式**，靜態只看初始化會漏掉後續改寫。

5. **錯誤直覺：「`call *0x60(%rax)` 是 bug 或混淆」。** 正確認識：那是正常的間接呼叫，`0x60` 是函式指標欄位在 struct 裡的 offset。看到 `call *` 就知道「這是 indirection，目標在資料裡」——這是逆向 dispatch table/vtable 的招牌指令，不是異常。

## 進階：再往深一層

- **connection 抽象層——巢狀的 indirection**：redis 的 `connSetReadHandler`/`connWrite` 底下還有一層 vtable：`connection` struct 有個 `ConnectionType *type`，socket / TLS / unix 各有一份 `ConnectionType`（一組函式指標）。所以 `connRead(conn, ...)` 是「透過 `conn->type->read` 間接呼叫」——**indirection 疊 indirection**。讀這種要一層層剝：先確認 `conn->type` 執行期是哪份（gdb `print *conn->type` 看是 CT_Socket 還是 CT_TLS），再看那份的 `read` 指向誰。多層 vtable 是大型 C 專案的常態，別指望一跳到底。

- **反查「誰可能寫這個函式指標欄位」的系統化做法**：對某個 ops 欄位（如 `rfileProc`），用 cscope 的「查賦值」或 `rg 'rfileProc\s*='` 列出所有寫入點，就得到「這個 slot 執行期可能是哪些函式」的封閉集合。這比逐個 gdb 觸發全面。redis 這種欄位賦值點通常個位數，值得一次列全。

- **`__attribute__((cleanup))` / atexit / signal handler 也是隱形 indirection**：這些機制把「稍後要呼叫的函式」註冊到系統，控制流不經過你讀的那段直線 code。讀到 `atexit(fn)`、`signal(SIGTERM, handler)`、`pthread_cleanup_push` 要意識到「這裡註冊了一個未來會被別人呼叫的入口」——它們是 Ch 24 事件驅動的親戚，同樣要「找註冊點」才追得到。

- **符號化 `call *` 目標的靜態嘗試**：某些靜態分析（如支援 points-to analysis 的工具、或 clang 的 CFG + type-based 過濾）能對間接呼叫推出「可能目標集」。實務上對 C 的函式指標精度有限（同型別的函式都會被算進候選），所以 redis 這種「所有 `redisCommandProc *` 型別的函式」會全被列為 `cmd->proc` 的候選——範圍太寬。這也是為什麼動態（gdb）在追 indirection 上常常比靜態更快給你確定答案。

## 動手練習

1. **換個命令驗跳轉**：把實戰二的 gdb 腳本 `break getCommand` 改成 `break setCommand`，client 送 `SET k v`，確認 `c->cmd->proc` 這次是 `<setCommand>`、`bt` 一樣經過 `call @ server.c:3575`。體會「同一行 dispatch，不同目標」。
2. **列出候選集合**：從 `commands.def` 用 `grep -oE '[a-zA-Z]+Command' commands.def | sort -u | head -30` 抓出所有命令 proc 候選，說明這就是 `c->cmd->proc` 的完整可能目標集。
3. **追 callback 的一整條路**：gdb `break readQueryFromClient`，送任意命令，`bt` 看它從 `ae.c` 的 `fe->rfileProc(...)`（`aeProcessEvents`）一路下來。把「event loop → read callback → 命令分派」串成一張圖。
4. **剝 connection 的巢狀 vtable**：gdb 在 `readQueryFromClient` 斷點後 `print *c->conn->type`，確認你這條連線走的是 `CT_Socket` 還是 `CT_Unix`，說出 `connRead` 會間接跳去哪個實作。
5. **看 `call *`**：`gdb -batch -ex 'disassemble /s call' ./redis-server | grep -A1 'proc(c)'`，找出間接呼叫指令與 `proc` 欄位的 offset，解釋為什麼反組譯到這裡就「斷線」。

## 本章重點整理

- indirection 讓靜態閱讀在 `cmd->proc(c)` 這種地方斷線：呼叫寫在這裡，目標存在資料裡，執行期才定。這是 C 實現多型/callback/plugin 的唯一手段。
- 六種形式（裸 fn ptr、callback、手寫 vtable、dispatch table、C++ virtual、plugin）在組語都收斂成 `call *`（間接呼叫）。
- 兩把鑰匙：**找註冊點**（靜態列出所有可能目標，redis 的 proc 全在 `commands.def`）+ **gdb 抓實際跳轉**（`print fp` + `bt` 看這一趟落在誰、經過哪條鏈）。
- redis 實證：`c->cmd->proc(c)` @ server.c:3575，gdb 證實 GET 跳進 `getCommand`（`$2 = ... <getCommand>`），組語是 `call *0x60(%rax)`。
- 陷阱：ctags 只跳到欄位宣告不是目標；表常是生成/巨集填的（grep 不到）；欄位執行期會被改寫；多層 vtable 要逐層剝（redis 的 connection 抽象）。

## 自我檢核

- [ ] 看到 `c->cmd->proc(c)`，我能不能不靠猜、講出兩條找到實際目標的路（註冊點 / gdb）？
- [ ] redis 的命令 proc 表為什麼 `grep '.proc ='` 找不到明顯賦值？它在哪、怎麼填的？
- [ ] 我能寫出一段 gdb 腳本，證明某個 GET 請求確實跳進 `getCommand`，並看到完整呼叫鏈嗎？
- [ ] `call *0x60(%rax)` 和 `call 0x1234` 差在哪？為什麼前者讓靜態工具追不下去？
- [ ] 同一個 read handler 欄位在握手階段和正常階段可能指向不同函式——我知道怎麼查「這個 slot 所有可能的值」嗎？

## 延伸閱讀

- **[Redis 原始碼：`src/server.c` 的 `call()` 與 `processCommand()`](https://github.com/redis/redis/blob/7.4.0/src/server.c)**
  - **讀哪裡**：`call()`（`c->cmd->proc(c)` 那行前後）與 `processCommand()` 裡 `lookupCommand` → `c->cmd` 的設定。
  - **學到什麼**：一個生產級 dispatch table 從「命令名 → 查表 → 間接呼叫」的完整實現，本章實戰一二的原始出處。
  - **前提**：懂 struct 裡的函式指標欄位。

- **[《Linux Device Drivers, 3rd ed.》第 3 章 "char drivers"（`file_operations`）](https://lwn.net/Kernel/LDD3/)**
  - **讀哪裡**：`file_operations` 結構與 driver 如何填它、VFS 如何 `f_op->read(...)` 分派。
  - **學到什麼**：手寫 vtable（struct of function pointers）在 kernel 的教科書級用法——和 redis 的 `moduleType` 是同一個模式。理解它，Ch 27 讀 kernel 慣例會很順。
  - **前提**：C 函式指標基礎；不需要有 kernel 經驗。

- **[GDB Manual — "Examining the Stack"（backtrace）與 "Breakpoints"](https://sourceware.org/gdb/current/onlinedocs/gdb/Backtrace.html)**
  - **讀哪裡**：`backtrace`/`bt N`、breakpoint `commands`（斷點命中自動執行的腳本）。
  - **學到什麼**：本章 gdb 腳本的每個指令在幹嘛——尤其 `commands` 讓斷點自動 `print`+`bt`+`continue`，是動態追 indirection 的標配。
  - **前提**：Ch 18「debugger-driven reading」的 gdb 基礎。

- **[Eli Bendersky, "The many faces of `operator new` in C++" 系列與其函式指標/vtable 文章](https://eli.thegreenplace.net/tag/c-c)**
  - **讀哪裡**：他站上關於 C function pointer table 與 C++ vtable 記憶體佈局的幾篇（搜 "vtable" / "function pointer"）。
  - **學到什麼**：把 C 的手寫 dispatch 與 C++ 編譯器自動生成的 vtable 對照——理解 `call *offset(%reg)` 在兩者是同一件事，銜接 Ch 26。
  - **前提**：本章看完；懂基本記憶體佈局。

你現在能追「呼叫跳去哪」了。但還有一類 code 連「控制流往哪走」都沒有直線答案——event loop 不是從上讀到下、狀態機的下一步取決於當前 state、非同步回呼把邏輯切成碎片。下一章我們處理狀態機與事件驅動，並基於真讀 `ae.c` 畫出 redis event loop 的流程圖。

→ [Ch 24 讀懂狀態機與事件驅動](./24-reading-state-machines-events.md)
