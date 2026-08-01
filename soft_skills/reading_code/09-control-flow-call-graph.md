# Ch 9 — 控制流與 call graph

> **目標**：學會用兩種互補的視角看「程式怎麼流動」——靜態 call graph（cflow：列出所有可能的呼叫關係）與動態 call graph（gdb backtrace / uftrace：抓真實跑過的那一條）。讀完你能在 redis 上：用 cflow 產出 `processCommand` 的呼叫樹、用 gdb 抓一次真實命令執行的呼叫鏈、並親眼看到「靜態分析在函式指標分派處會斷、動態一秒穿透」——這正是為什麼兩者缺一不可。

## 兩種地圖：可能性 vs 事實

Ch 8 我們跟著一份資料跑了一條藤。那是「一次執行」的線。這一章我們要看的是**整張控制流的圖**：一個函式底下，可能通往哪些函式？實際又走了哪些？

先立心智模型。call graph 有兩種，回答的是**不同的問題**：

```
  靜態 call graph（cflow）          動態 call graph（gdb/uftrace）
  「有哪些路徑『可能』被走到」       「這一次『實際』走了哪條路」

        processCommand                     processCommand
       /  |  |  |  |  \                          |
   auth arity OOM ... call                      call
                        |                         |
                    (斷在這裡!)            c->cmd->proc(c) ─→ getCommand
                                                  |         （動態看得到目標）
                                             getGenericCommand

   看得到「全部分支」                  看得到「函式指標真正指向誰」
   看不穿函式指標                       看不到「沒被走到的分支」
```

一句話記住取捨：**靜態看全部可能、看不穿 indirection；動態看實際發生、看不到沒走的路。** 兩者不是誰取代誰，是拼一張完整圖的兩半。逆向工程裡這對應得很直接——靜態 call graph 就是 IDA/Ghidra 的 xref 與 call graph 視圖，動態就是在 debugger 裡跑一遍看真實 call stack。你早就在 binary 上這樣做，讀 source 是同一套。

## 靜態 call graph：cflow 從一個函式往下攤

cflow 讀 C source，靜態分析出「誰呼叫誰」，輸出一棵縮排的呼叫樹。它不編譯、不執行，純看語法——所以快、不需環境，但也因此有它看不穿的東西（下面會撞到）。

我們拿 redis 命令處理的核心 `processCommand` 開刀。先控制深度，別一次攤到底（`processCommand` 往下是整個 redis，全攤出來幾千行沒法看）。這是**真實輸出**（`--depth=3` 只展三層，節錄）：

```
$ cflow --depth=3 -m processCommand src/server.c
processCommand() <int processCommand (client *c) at src/server.c:3884>:
    scriptIsTimedout()
    moduleCallCommandFilters()
    reqresAppendRequest()
    blockPostponeClient()
    lookupCommand() <struct redisCommand *lookupCommand (robj **argv, int argc) at src/server.c:3200>:
        lookupCommandLogic() <... at src/server.c:3184>:
    commandCheckExistence() <int commandCheckExistence (client *c, sds *err) at src/server.c:3813>:
        isContainerCommandBySds() <... at src/server.c:3166>:
        sdsnew()
        sdstoupper()
        ...
    commandCheckArity() <int commandCheckArity (client *c, sds *err) at src/server.c:3845>:
        sdsnew()
        sdscatprintf()
    rejectCommandSds() <void rejectCommandSds (client *c, sds s) at src/server.c:3771>:
        flagTransaction()
        addReplyErrorSds()
    getCommandFlags() <uint64_t getCommandFlags (client *c) at src/server.c:3862>:
    ...
```

一眼你就讀出 `processCommand` 的骨架：**查表**（`lookupCommand`）→ **一連串前置檢查**（`commandCheckExistence`、`commandCheckArity`、`getCommandFlags`……）→ 出錯就 **拒絕**（`rejectCommandSds` → `addReplyErrorSds`）。這棵樹把 Ch 8 backtrace 裡「processCommand 做了一堆前置檢查」那句話，展開成了具體清單。**這就是 call graph 的價值：把一個函式的「內部劇情」變成一張可掃視的目錄。**

`-m` 指定 main（樹根），`--depth=N` 控深度。`cflow` 每個節點還附了定義位置（`at src/server.c:3813`），等於自帶跳轉座標。

### 反向 call graph：誰呼叫我？

順向（我呼叫誰）用 `cflow -m`，反向（誰呼叫我）用 `cflow -r`。反向圖回答的是讀碼最常問的「這函式是誰觸發的」。實跑一次找 `getCommand` 的上游（真實輸出）：

```
$ cflow -r --depth=3 src/t_string.c src/server.c | rg -A2 '^getCommand'
getCommand() <void getCommand (client *c) at src/t_string.c:316>
getCommandFlags() <uint64_t getCommandFlags (client *c) at src/server.c:3862>:
    processCommand() <int processCommand (client *c) at src/server.c:3884>
```

這裡藏了本章最重要的一課，先按住不表——你有沒有發現：反向圖裡，`getCommand` 這個節點底下**是空的**？照 Ch 8 我們明明知道 `getCommand` 是被 `call` 呼叫的。cflow 為什麼沒把 `call` 列為 `getCommand` 的呼叫者？

答案就是下一節的主角。

## 靜態分析的天花板：函式指標讓 call graph 斷掉

我們用 cflow 攤 `call` 這個函式往下呼叫誰。redis 所有命令都經過 `call`，所以它的呼叫樹理應包含 `getCommand`、`setCommand` 等所有命令處理函式。實跑（真實輸出，節錄）：

```
$ cflow --depth=2 -m call src/server.c
call() <void call (client *c, int flags) at src/server.c:3524>:
    incrCommandStatsOnError() <... at src/server.c:3470>:
    ustime()
    enterExecutionUnit() <... at src/server.c:1147>:
    exitExecutionUnit() <... at src/server.c:1157>:
    slowlogPushCurrentCommand() <... at src/server.c:3363>:
    replicationFeedMonitors()
    alsoPropagate() <... at src/server.c:3321>:
    afterCommand() <... at src/server.c:3796>:
    ...
```

我實跑後在這棵樹裡 grep `getCommand`、`proc`、`c->cmd->proc`——**命中數是 0**：

```
$ cflow --depth=2 -m call src/server.c | grep -c 'getCommand\|c->cmd->proc\|proc'
0
```

`call` 的呼叫樹裡，**完全沒有任何命令處理函式**。為什麼？打開 `call` 看真相（`src/server.c:3575`，就是 Ch 8 backtrace 裡 `#1` 那個 frame）：

```c
    c->cmd->proc(c);          // ← 這一行呼叫了 getCommand，但 cflow 看不出來
```

`call` 不是寫死 `getCommand(c)`，而是透過函式指標 `c->cmd->proc(c)` 分派。**cflow 是純靜態語法分析，它看到 `c->cmd->proc(c)` 只知道「這裡呼叫了某個函式指標」，但那個指標執行時指向誰，要到 runtime 才決定**——`lookupCommand` 把 `GET` 對到 `getCommand`、`SET` 對到 `setCommand`，這是資料驅動的，不在語法裡。所以：

- cflow 的 `call` 樹裡沒有 `getCommand`（順向斷了）。
- cflow 的 `getCommand` 反向樹裡沒有 `call`（反向也斷了）。

**這就是所有靜態 call graph 工具（cflow、ctags callgraph、甚至 clangd 的 call hierarchy 在某些情況）共同的天花板：碰到函式指標、virtual function、dlopen 動態載入、callback 註冊表，靜態分析就斷片。** redis 這種「命令表 + 函式指標分派」的架構（Ch 23 專講的 indirection）正是重災區——整個系統最關鍵的那個分派點，靜態圖上是一片空白。

> **這不是 cflow 的 bug，是靜態分析的本質極限。** 記住哪些構造會讓靜態 call graph 斷：函式指標、C++ 虛函式、`dlsym`、Lua/Python 等 embedded 語言的 callback、事件迴圈的 handler 註冊。一看到這些，就知道「該叫動態工具上場了」。

## 動態 call graph：gdb 一秒穿透 indirection

靜態斷掉的地方，動態接手。我們要的答案是「`call` 執行時，那個 `c->cmd->proc(c)` 到底跳去哪」。方法就是 Ch 8 用過的：下斷點、跑真請求、看 backtrace。這是**真實輸出**（在 gdb 底下起 `redis-server --port 7777`，斷 `getCommand`，跑 `GET mykey`）：

```
Thread 1 "redis-server" hit Breakpoint 1, getCommand (c=0x7ffff7935700) at src/t_string.c:316
#0  getCommand (c=0x7ffff7935700) at src/t_string.c:316
#1  0x...5f9a15 in call (c=c@entry=0x7ffff7935700, flags=flags@entry=3) at src/server.c:3575
#2  0x...5fba78 in processCommand (c=0x7ffff7935700) at src/server.c:4206
#3  0x...62249c in processCommandAndResetClient (c=0x7ffff7935700) at src/networking.c:2505
#4  processInputBuffer (c=0x7ffff7935700) at src/networking.c:2613
#5  0x...622a68 in readQueryFromClient (conn=<optimized out>) at src/networking.c:2759
...
#9  aeMain (eventLoop=0x7ffff782a140) at src/ae.c:477
#10 0x...5daf7b in main (argc=7, ...) at src/server.c:7251
```

看 `#1`：`call (...) at src/server.c:3575`——**正是 `c->cmd->proc(c)` 那一行**。動態 backtrace 明明白白告訴你：這次 `proc` 指向 `getCommand`。**cflow 斷掉的那條邊，gdb 一個 `bt` 就補上了。** 這就是「動態看得穿 indirection」的實證：函式指標執行時的真正目標，只有跑起來才知道，而 backtrace 直接把它顯示出來。

動態的另一面是：這個 backtrace **只反映「這一次」**。它告訴你 `GET` 走了 `getCommand`，但你看不到「如果送 `SET` 會走 `setCommand`」——那條路徑這次沒被走，就不在圖上。想看 `setCommand`？再斷一次跑 `SET`。**動態圖是「一次執行的切片」，不是全貌。** 這正是它跟靜態互補的地方。

## uftrace：一次錄下整棵動態呼叫樹

gdb backtrace 給你「某一點往上的呼叫鏈」。但如果你想要的是「一次命令執行，往下展開的整棵樹」（誰呼叫誰、各花多久），一個一個下斷點太慢。這時用 **uftrace**——它把函式進出全錄下來，replay 成一棵帶時間的呼叫樹。

uftrace 的動態全錄需要目標**編譯時帶插樁**（`-pg` / `-finstrument-functions` / `-mfentry`）。redis 的 release 二進位是 `-O2` 且無插樁，直接錄不到函式層（實測 `uftrace record` 對 stock redis-server 產不出 function-level 資料——**這點務必知道，不然你會浪費半天**）。要在 redis 上用 uftrace，得 `make CFLAGS="-pg -g -O0"` 重編一份插樁版。為了乾淨示範 uftrace 的**輸出長相與呼叫樹語義**，我用一個結構模仿 redis 分派鏈的小程式實跑（**真實輸出**）：

```c
// uft_demo.c，編譯：gcc -pg -g -O0 -o uft_demo uft_demo.c
int lookupKeyRead(int k){ return k*2; }
int getGenericCommand(int c){ return lookupKeyRead(c); }
void getCommand(int c){ getGenericCommand(c); }
void call(int c){ getCommand(c); }
void processCommand(int c){ call(c); }
int main(){ processCommand(42); return 0; }
```

```
$ uftrace record ./uft_demo && uftrace replay
# DURATION     TID     FUNCTION
            [ 43839] | main() {
            [ 43839] |   processCommand() {
            [ 43839] |     call() {
            [ 43839] |       getCommand() {
            [ 43839] |         getGenericCommand() {
   0.100 us [ 43839] |           lookupKeyRead();
   0.200 us [ 43839] |         } /* getGenericCommand */
   0.300 us [ 43839] |       } /* getCommand */
   0.500 us [ 43839] |     } /* call */
   0.500 us [ 43839] |   } /* processCommand */
   6.100 us [ 43839] | } /* main */
```

這棵樹就是**動態 call graph 的完整形態**：不只有呼叫關係，還有巢狀結構與每層耗時（`getGenericCommand` 花 0.2 us、`call` 花 0.5 us）。跟 gdb backtrace 相比，backtrace 是「一個瞬間往上看」，uftrace 是「一整段執行的完整錄影」。真要在 redis 上跑，把上面的 demo 換成插樁編譯的 redis-server、`uftrace record src/redis-server ...` 再送命令即可，樹的形狀會跟 Ch 8 那個 backtrace 完全吻合，只是往下展得更深、還帶耗時——這對找**熱路徑**（下一節）是決定性的。

> 上面 redis 的 uftrace 輸出標為**未實測，理論預期**：stock 二進位無插樁，需重編。demo 程式的輸出是**真跑照抄**，用來如實呈現 uftrace 的樹狀輸出與語義。gdb backtrace 那段則全部真跑。

## 找熱路徑：哪條路被走最多、花最久

讀大型系統，你不會（也不該）平均用力讀每個函式。**80% 的時間花在 20% 的 code 上**，那 20% 就是熱路徑（hot path）。優先讀熱路徑，性價比最高。怎麼找？

- **動態計數/計時**：uftrace 的耗時欄位直接告訴你哪層慢；`uftrace report` 還能按累計時間排序函式。`perf record` / `perf top`（Ch 19）在 stock 二進位上就能做（不需插樁），採樣出「CPU 時間花在哪些函式」。
- **從架構常識推**：對 redis，熱路徑一定是「事件迴圈 → 讀命令 → 分派 → 命令處理 → 回覆」這條（就是 Ch 8 的藤）。維運與監控類（`serverCron`、`INFO`）、錯誤處理、啟動載入都是冷路徑。**先假設主 I/O 路徑是熱的，再用 perf 驗證**，通常八九不離十。

熱路徑的意義：它是你**精讀**（Ch 4）的首要目標；冷路徑（錯誤處理、罕用命令、啟動配置）用**掃讀**帶過即可。call graph + 熱度資訊，就是幫你分配「哪裡精讀、哪裡掃讀」的地圖。

## 條件分支爆炸：只跟你關心的那一條

真實函式的控制流不是一條直線。`processCommand` 有幾十個 `if`（權限？arity 對嗎？OOM 嗎？在 MULTI 裡嗎？是 write 命令但 replica 唯讀嗎？……），每個 `if` 都可能 `reject` 然後 return。全部展開，控制流圖是一團爆炸的義大利麵。

**應對心法：你不是在讀「這函式的所有可能行為」，你是在追「你這個 case 走哪條」。** 兩個具體做法：

1. **鎖定一個具體輸入，只跟它的路徑。** 你關心 `GET mykey` 正常成功？那所有「命令不存在」「arity 錯」「OOM」「replica 唯讀」的分支，對你這條路徑都是 `if (條件) { reject; return; }` ——**條件不成立就跳過整塊**。讀的時候快速掃過每個 `if`，問「我的 case 會進去嗎？」不會就下一個。這樣幾十個分支裡，你只需真正讀你會走的那 5 個。

2. **用動態幫你剪枝。** 不確定某分支會不會走？gdb 設條件斷點或直接 `next` 單步走一遍，看實際踏過哪些 `if`。動態執行天然幫你把「這次沒走的分支」剪掉——你眼睛看到的每一行都是真的走到的。這是對付分支爆炸最省腦力的辦法：**別在腦裡模擬所有分支，讓 CPU 幫你選路。**

> 逆向工程的老手在 IDA 裡看到一個滿是條件跳轉的函式，也是同一招：不逐條分析所有 branch，而是在 debugger 裡帶著具體輸入跑一遍，看實際 trace 出來的那條路徑，其餘 branch 標記「錯誤處理/邊界，暫略」。讀 source 完全一樣。

## 對比與取捨

| 維度 | 靜態 call graph（cflow） | 動態 call graph（gdb backtrace） | 動態全錄（uftrace/perf） |
|---|---|---|---|
| 回答 | 有哪些呼叫「可能」發生 | 這一點往上的真實呼叫鏈 | 一整段執行往下的完整樹 |
| 看穿函式指標？ | **否**（斷片） | **是** | **是** |
| 看到沒走的分支？ | **是**（全部） | 否 | 否 |
| 需要能跑嗎？ | 否 | 是（要造觸發輸入） | 是（uftrace 還要插樁編譯） |
| 帶耗時/熱度？ | 否 | 否 | **是** |
| 最適合 | 掌握一個函式的內部骨架、找全部可能路徑 | 「這請求怎麼進到這裡的」、破解 indirection | 找熱路徑、看完整執行樹、效能 |

**實戰組合**：先 cflow 攤出靜態骨架（快、看全貌）→ 撞到函式指標/handler 分派看不下去了 → gdb 斷點 + backtrace 補上那條真實邊 → 想看熱路徑或完整下展樹 → uftrace/perf。靜態給你「地圖」，動態給你「這趟實際走的路線 + 路況」。

## 踩雷集錦

1. **錯誤直覺：「cflow 沒列出某函式的呼叫者，代表沒人呼叫它（可能是 dead code）」。**
   正確認識：極可能是**它透過函式指標被呼叫**，靜態工具看不見。redis 所有命令處理函式在 cflow 反向圖裡都「沒有呼叫者」，但它們天天被 `call` 觸發。判定 dead code **絕不能只信靜態 call graph**，要配合 rg 搜「這函式的位址在哪被存進表/指標」（例如 `rg getCommand src/commands.def`）。

2. **錯誤直覺：「動態 backtrace 沒出現某分支，代表那分支是死的」。**
   正確認識：動態只反映**這一次**執行。你這次送 `GET` 沒走 `setCommand`，不代表 `setCommand` 是死的——換個輸入就走了。動態圖是切片不是全貌，用它下「這條路不存在」的結論是經典錯誤。

3. **錯誤直覺：「uftrace 對任何二進位都能錄函式呼叫樹」。**
   正確認識：uftrace 的完整 function-level 錄製需要**編譯時插樁**（`-pg` 等）。對 stock 的 `-O2` 無插樁二進位（如 release redis），實測錄不到函式層（我實跑確認 stock redis-server 產不出 function-level data）。要嘛重編插樁版，要嘛改用 perf 採樣（perf 不需插樁，但給的是採樣統計而非精確樹）。搞不清這點會白忙半天。

4. **錯誤直覺：「函式很多分支，我要全部讀懂才算讀懂這函式」。**
   正確認識：你幾乎從不需要讀懂一個函式的**所有**分支。鎖定你的具體 case，只讀它會走的路徑，其餘 `if (err) reject` 掃過即可。想「完全讀懂」每個分支，是新手在 `processCommand` 這種函式前卡住幾小時的主因。

5. **錯誤直覺：「call graph 越完整越好，`--depth` 開越大越好」。**
   正確認識：`cflow` 不設 depth 從 `main` 往下攤，會吐出整個 redis 幾千行，人腦無法消化。**call graph 的價值在『恰當的抽象層級』**——`--depth=2~3` 看骨架，需要細節再對特定子節點深挖。一次攤到底等於沒攤。

## 進階：再往深一層

- **把 cflow 餵給 graphviz 畫圖**：`cflow` 是文字樹，人腦對「圖」比對「縮排」更快。可以寫個小腳本把 cflow 輸出轉成 `dot` 格式，用 `graphviz` 畫成真正的呼叫圖（Ch 16 給完整 pipeline）。對匯報、對建立團隊共享的架構圖特別有用。

- **clangd 的 call hierarchy**：cflow 純語法、會被巨集和函式指標騙。clangd（Ch 13）基於真實編譯，call hierarchy 更準，且能處理 C++ 的多載與部分虛函式解析。代價是要 `compile_commands.json`、較重。**精度要求高、又是你主力語言時，用 clangd 的 call hierarchy 取代 cflow。** 但注意：C++ 虛函式的動態分派，clangd 靜態也只能給「所有可能的 override」，真正走哪個仍要動態。

- **靜態解 indirection 的進階招**：函式指標分派雖讓 cflow 斷，但那張「命令表」本身是靜態資料，可以直接讀。redis 的命令→函式對應在 `src/commands.def`（自動產生）裡，`rg 'getCommand' src/commands.def` 能找到 `GET` 綁定 `getCommandGetKeys`/`getCommand`。**遇到函式指標分派，除了動態，還可以「去讀那張分派表」**——表在哪，答案就在哪。這是補靜態盲區的第三條路。

- **coverage 當 call graph 的補充**：`gcov`/`llvm-cov` 能標出「跑某輸入時，哪些行/分支被執行到」。這等於給你一張「這次執行點亮了哪些 code」的地圖，是找熱路徑、確認「這分支到底走不走得到」的利器。跑既有測試 + coverage，能快速看出一個功能實際觸及哪些函式。Ch 16 深入。

## 動手練習

在 `~/reading_code_lab/redis` 上做，貼真跑輸出：

1. **靜態骨架**：`cflow --depth=2 -m processCommand src/server.c`，把輸出整理成一句話描述 `processCommand` 的三段式骨架（查表 → 檢查 → 分派/拒絕）。

2. **親手撞天花板**：`cflow --depth=2 -m call src/server.c`，然後 `| grep -c getCommand`。確認得到 `0`，打開 `src/server.c:3575` 看 `c->cmd->proc(c)`，用自己的話寫出「為什麼靜態圖在這裡斷、這代表什麼」。

3. **動態補邊**：在 gdb 底下起 `redis-server --port 7777`，斷 `setCommand`，跑 `redis-cli -p 7777 SET k v`，貼 backtrace。確認 `#1` 是 `call` 且行號指向 `proc` 分派那行——你剛用動態補上了 cflow 斷掉的邊。

4. **只跟一條路徑**：打開 `processCommand`（`src/server.c:3884`），數一數它有幾個會 `reject...; return` 的分支。針對「一個正常 `GET`」，標出哪些分支會被跳過、哪些會真的執行。體會「只跟你的 case」。

5. **（選）熱路徑**：對跑著的 redis 用 `perf top -p $(pgrep -f 'redis-server.*7777')`，一邊用 `redis-benchmark` 打流量，看哪些函式吃最多 CPU，對照你猜的熱路徑。

## 本章重點整理

- call graph 有兩種：**靜態（cflow：所有可能路徑，看不穿函式指標）** vs **動態（gdb/uftrace：實際走的路，看不到沒走的分支）**，互補而非替代。
- redis 的命令分派 `call → c->cmd->proc(c)` 是函式指標，**cflow 在此斷片**（`call` 的樹裡 grep `getCommand` 得 0），**gdb backtrace 一秒穿透**（`#1 call at server.c:3575`）。
- 靜態斷片的通用觸發：函式指標、C++ 虛函式、`dlsym`、embedded 語言 callback、事件 handler 註冊。撞到就換動態。
- uftrace 給帶耗時的完整下展樹，但**需插樁編譯**；stock `-O2` 二進位錄不到函式層（實測），需重編或改用 perf 採樣。
- 找**熱路徑**優先精讀（perf/uftrace 或架構常識判斷）；冷路徑（錯誤處理、啟動、罕用命令）掃讀即可。
- 分支爆炸的解法：**鎖定一個具體輸入只跟它的路徑**，其餘 `if(err) reject` 掃過；或讓 gdb 單步幫你剪枝。

## 自我檢核

- [ ] 我能說出靜態與動態 call graph 各回答什麼問題、各自的致命盲區。
- [ ] 我能解釋為什麼 cflow 的 `call` 樹裡沒有 `getCommand`，並說出這是本質極限不是 bug。
- [ ] 拿到一個函式指標分派點，我知道有哪三條路可以查出它實際指向誰（gdb 動態 / 讀分派表 / clangd）。
- [ ] 我知道 uftrace 為什麼對 stock redis 二進位錄不到函式，該怎麼辦。
- [ ] 面對一個幾十分支的函式，我能只讀我這個 case 會走的那幾條，而不試圖讀懂全部。

我們現在會追資料（Ch 8）、會看控制流全貌（Ch 9），但讀碼真正的引擎還沒登場——**你怎麼在資訊不全時做出正確猜測，並驗證它**。下一章進到全課的 RE 心法核心：假設驅動讀碼。

→ [Ch 10 假設驅動讀碼](./10-hypothesis-driven-reading.md)
