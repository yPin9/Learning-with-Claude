# Ch 18 — debugger-driven reading

> **目標**：把 gdb 從「除錯壞掉的程式」的工具，重新框架成「讀懂正常程式」的工具。當靜態閱讀撞牆——函式指標不知道指向誰、巨集展開後面目全非、一堆分支不知道真跑時走哪條——讓程式**真的跑起來**，在關鍵處攔下它，直接問它「你現在的呼叫鏈長怎樣、變數是什麼值、下一步往哪走」。這章教你用斷點、backtrace、watchpoint、conditional breakpoint、`print` 資料結構，把一條抽象的執行路徑變成你親眼看過的事實。

> **環境**：WSL2 Ubuntu 22.04，gdb 12.1，沙包 `~/reading_code_lab/redis`（redis 7.4.0）。本章需要 debug 版 redis：
> ```bash
> cd ~/reading_code_lab/redis && make -j$(nproc)   # 產出帶 debug_info 的 src/redis-server
> ```
> 驗證編出來的是 debug 版：`file src/redis-server` 應含 `with debug_info, not stripped`。本章所有 gdb 輸出都是在這個 debug 版 redis 上真跑照抄。redis 預設 build 已帶 `-g -O2`（見 `src/Makefile` 的 `OPTIMIZATION`），符號齊全但有最佳化，某些變數會顯示 `<optimized out>`——這是真實情況，我們照實呈現並說明怎麼應對。

## 為什麼靜態讀會撞牆，動態讀能穿牆

前面整個 Part 3 都在強化你的**靜態**閱讀：rg 找字串、cscope 反查呼叫者、clangd 精準跳轉、git 挖歷史。這些威力巨大，但它們有一個共同的天花板：**它們只能告訴你「可能的」控制流，不能告訴你「實際的」。**

有幾類問題，靜態工具天生答不好，你會反覆撞牆：

- **函式指標 / 動態 dispatch**：`c->cmd->proc(c)` ——這個 `proc` 到底指向哪個函式？靜態看，它可能是幾百個命令處理函式裡的任何一個。cscope 給你「所有被賦值給 proc 的函式」，但**這一次**呼叫走的是哪個？靜態無解。
- **巨集展開後的真相**：一坨巢狀巨集展開後到底變成什麼、真正執行了哪幾行？你可以 `gcc -E` 看展開（Ch 22），但巨集裡有條件分支時，「這次走哪條」還是要跑起來才知道。
- **「到底走哪條分支」**：一個函式開頭十個 `if` 守衛，正常請求進來時實際踩過哪幾個、在哪個 `return` 提早離開？靜態讀你得把所有輸入可能性都在腦裡模擬，累且易錯。
- **呼叫鏈的真實形狀**：這個函式是「誰、透過什麼路徑」呼叫到的？cscope 給你所有**可能的**呼叫者，但**這一次**是從哪條路來的？只有 backtrace 能給你確定答案。

動態讀碼的核心價值就一句話：**它把「可能性」坍縮成「事實」。** 你不再需要在腦中模擬所有分支——你讓真實的輸入驅動程式走一遍，然後在關鍵點攔下它，看它**實際**的狀態。這對「讀懂一個你完全陌生的流程」是降維打擊。

> 定位一下這章和 gdb 課的關係：gdb 課教你「用 gdb 找 bug」，這章只借用 gdb 的一小組能力來「讀懂正常流程」。目標不同——我們不修任何東西，我們只是把 gdb 當成一台能凍結時間、透視變數的顯微鏡。

## 底層機制：gdb 憑什麼能凍結程式、看到變數名

三個機制撐起 debugger-driven reading，理解它們你才知道何時能用、何時失效。

**1. `ptrace` 讓一個行程完全掌控另一個行程。** gdb 透過 `ptrace(2)` 系統呼叫 attach 到目標行程，之後能：讀寫它的記憶體與暫存器、在任意位址設斷點、單步執行。斷點的實作是把目標位址的那個 byte 換成 `int3`（x86 的軟體中斷指令 `0xCC`）；程式執行到那，觸發中斷，控制權回到 gdb。這就是「凍結」的本質——不是暫停時鐘，是攔截一條特殊指令。

**2. DWARF debug info 把「機器位址」翻回「源碼概念」。** `-g` 編出來的 binary 裡有一大塊 DWARF 資訊，記錄了「哪個位址對應哪一行源碼」「這個變數存在哪個暫存器/堆疊偏移」「這個 struct 的每個欄位偏移是多少」。沒有它，gdb 只能給你裸位址和 raw bytes；有了它，gdb 才能 `print c->argv[0]->ptr` 這樣用源碼層的名字問問題，也才能 `break processCommand`（用函式名而非位址下斷）。**這就是為什麼讀碼要用 debug build**——release build 沒 DWARF，gdb 半殘。

**3. 硬體 watchpoint 靠 CPU 的 debug register。** 「監看某個記憶體位址被寫入就停下」——x86 有 4 個 debug register（DR0–DR3）能做到，CPU 在該位址被寫時直接觸發例外。所以 watchpoint 是**硬體加速**的，幾乎零額外成本（相對地，如果監看的範圍超過硬體能力，gdb 會退化成「每步都檢查」的軟體 watchpoint，慢到不可用——後面會遇到）。

記住：**斷點靠 int3、變數名靠 DWARF、watchpoint 靠 debug register**。三者都在，動態讀碼才順。

## 實戰一：攔下命令處理，看真實呼叫鏈

我們來回答一個純靜態很難一次答清的問題：**redis 收到一個 `SET` 命令，從 socket 上的 bytes 到 `processCommand` 被呼叫，中間到底經過哪些函式？**

先在 gdb 裡啟動 redis，在命令處理入口 `processCommand` 下斷。實務上我們用一個 gdb 腳本把「命中斷點後要做的事」寫進 `commands` 區塊，這樣可以非互動地跑完整個觀察流程（互動時你就是手動一條條敲）：

```gdb
set pagination off
break processCommand
commands
  bt
  print (char*)c->argv[0]->ptr
  print c->argc
  print (char*)c->argv[1]->ptr
  detach
  quit
end
run --port 7781 --save ''
```

啟動後，從另一個終端送一個命令觸發斷點：

```bash
./src/redis-cli -p 7781 set mykey hello
```

gdb 真實輸出（照抄）：

```
Thread 1 "redis-server" hit Breakpoint 1, processCommand (c=0x7ffff7935700)
    at /home/ypp/reading_code_lab/redis/src/server.c:3884
3884	int processCommand(client *c) {

#0  processCommand (c=0x7ffff7935700) at src/server.c:3884
#1  0x000055555562249c in processCommandAndResetClient (c=0x7ffff7935700) at src/networking.c:2505
#2  processInputBuffer (c=0x7ffff7935700) at src/networking.c:2613
#3  0x0000555555622a68 in readQueryFromClient (conn=<optimized out>) at src/networking.c:2759
#4  0x0000555555738101 in callHandler (handler=<optimized out>, conn=0x7ffff7829580) at src/connhelpers.h:58
#5  connSocketEventHandler (el=<optimized out>, fd=<optimized out>, clientData=0x7ffff7829580, mask=<optimized out>) at src/socket.c:277
#6  0x00005555555e61f2 in aeProcessEvents (flags=27, eventLoop=0x7ffff782a140) at src/ae.c:417
#7  aeMain (eventLoop=0x7ffff782a140) at src/ae.c:477
#8  0x00005555555daf7b in main (argc=5, argv=<optimized out>) at src/server.c:7251
```

**這張 backtrace（`bt`）一次講清了整條路徑，而且是確定的、不是猜的。** 從下往上讀就是 redis 的請求生命週期：

- `main`（`server.c:7251`）→ `aeMain`：進入事件迴圈（Ch 0 我們用 cscope 定位到的「心臟」，這裡親眼看到它在呼叫鏈底部）。
- `aeProcessEvents`：事件迴圈跑一輪，發現有 fd 可讀。
- `connSocketEventHandler` → `callHandler` → `readQueryFromClient`：socket 可讀事件被派發，讀進 query buffer。
- `processInputBuffer` → `processCommandAndResetClient` → `processCommand`：把 buffer 裡的 RESP 協定解析成命令，送進命令處理。

你剛剛用一條 `bt` 指令，把「一個 SET 命令怎麼從 socket 走到命令處理」這件本來要跨 4 個檔案、逐個 cscope 反查才能拼出來的事，一次看全。**這就是 backtrace 的殺手級用途：它是「這一次執行的真實控制流」的完整快照。**

再看我們順手 `print` 出來的東西：

```
$1 = 0x7ffff785e92b "set"        ← c->argv[0]->ptr：命令名
$2 = 3                           ← c->argc：參數個數（set + key + value）
$3 = 0x7ffff7875073 "mykey"      ← c->argv[1]->ptr：第一個參數
```

`c` 是個不透明的指標 `0x7ffff7935700`，但靠 DWARF，gdb 知道 `client` struct 的佈局，我們就能鑽進去把 `argv[]` 裡的 SDS 字串（redis 的字串型別，`->ptr` 指向實際 bytes）印出來，直接證實「這次進來的確實是 `set mykey ...`、argc=3」。**靜態讀 `client` struct 定義只能知道欄位長什麼樣；動態 print 能知道欄位此刻裝著什麼。**

> **`<optimized out>` 是怎麼回事？** backtrace 裡好幾個參數顯示 `<optimized out>`（如 `conn`、`fd`、`mask`）。這是 `-O2` 最佳化的結果：編譯器把某些變數只放在暫存器、且在那個點已經被覆用，DWARF 無法可靠地指出它此刻的值。這不是 gdb 壞了，是最佳化的固有代價。對付它有兩招：(a) 需要看那個變數時，`frame` 切到它**還活著**的更早/更晚位置再 print；(b) 若這變數對你理解流程很關鍵，重編一份 `-O0` 的 redis（`make CFLAGS="-O0 -g"`）——`-O0` 幾乎不會有 `<optimized out>`，代價是跑得慢、且某些行為（inlining 消失）跟正式版略不同。讀碼多數時候 `-O2` 夠用，backtrace 的函式鏈不受影響。

## 實戰二：conditional breakpoint —— 只在你關心的那次停下

上面的 `break processCommand` 有個問題：redis 啟動、client 連線時會跑一堆命令（`INFO`、`SELECT`、乃至 `redis-cli` 自己送的握手），你的斷點會被**無關的命令**反覆命中，淹沒你想觀察的那一次。

解法是 **conditional breakpoint**：`break <位置> if <條件>`，只有條件成立才真正停下。我們要「只在 `GET` 命令進來時停」，條件寫成「`c->argv[0]->ptr` 這個字串等於 `"get"`」。gdb 內建 `$_streq` 函式可以比字串：

```gdb
break processCommand if $_streq((char*)c->argv[0]->ptr, "get")
commands
  print (char*)c->argv[0]->ptr
  print (char*)c->argv[1]->ptr
end
run --port 7783 --save ''
```

然後故意先送一個 `set`（應該**不**觸發），再送一個 `get`（應該觸發）：

```bash
./src/redis-cli -p 7783 set foo bar
./src/redis-cli -p 7783 get foo
```

真實輸出——斷點**只**在 `get` 那次命中，`set` 那次被條件過濾掉了：

```
===== CONDITIONAL HIT (only for GET) =====
$1 = 0x7ffff785f913 "get"
$2 = 0x7ffff785f943 "foo"
```

`set foo bar` 完全沒讓程式停下，`get foo` 才停。**這就是 conditional breakpoint 的價值：在一個每秒可能跑幾萬次的熱點函式上，只攔截符合你興趣的那一次。** 讀碼時你常常要問「當輸入是 X 時，這個函式怎麼走」——條件斷點讓你精準捕捉那個 X 的情境，不被其他情境干擾。

條件可以任意複雜：`break dbAdd if c->db->id == 0 && key->encoding == OBJ_ENCODING_INT`、`break someFunc if counter > 1000`（跳過前一千次、只看第 1001 次之後）。這是把「我只想看某個特定狀態下的執行」變成一行條件的能力。

搭配的還有 **`tbreak`（temporary breakpoint）**：命中一次後自動刪除，適合「我只想在第一次到達這裡時看一眼」的場景，省得手動 disable。

## 實戰三：watchpoint —— 不是看「哪裡執行」，是看「誰改了這個值」

斷點回答「程式跑到**哪**了」；watchpoint 回答一個完全不同、且純靜態幾乎無解的問題：**這個變數/欄位是被誰改的？在哪一行改的？**

想像你在讀 redis，看到一個全域欄位 `server.dirty`（記錄「自上次存檔以來有多少次資料變更」，用來決定要不要觸發 RDB 存檔）。你想知道：**一個 `SET` 命令執行時，`server.dirty` 是在哪一行、被哪個函式增加的？** 靜態找，你得 grep 所有 `server.dirty` 的賦值點（可能幾十處），再逐一判斷哪個在 SET 路徑上——累且不確定。

watchpoint 直接給答案。我們在 `setKey`（設定 key 的函式）下斷、命中後對 `server.dirty` 掛一個 watchpoint，然後讓它繼續跑，看它在哪裡被改：

```gdb
break setKey
commands
  print server.dirty
  watch server.dirty
  continue
end
run --port 7789 --save ''
```

送一個 SET：

```bash
./src/redis-cli -p 7789 set wk wv
```

真實輸出（照抄）：

```
$1 = 0                                  ← 進 setKey 時 server.dirty = 0
Hardware watchpoint 2: server.dirty

Thread 1 "redis-server" hit Hardware watchpoint 2: server.dirty

Old value = 0
New value = 1
setGenericCommand (c=0x7ffff7935700, flags=0, key=0x7ffff785f930, val=0x7ffff785f948,
    expire=0x0, unit=<optimized out>, ok_reply=0x0, abort_reply=0x0)
    at /home/ypp/reading_code_lab/redis/src/t_string.c:93
93	    notifyKeyspaceEvent(NOTIFY_STRING,"set",key,c->db->id);
```

**答案直接送到面前**：`server.dirty` 從 0 變 1，發生在 `setGenericCommand`（`t_string.c`）——確切地，在 `setKey` 完成後、要發 keyspace 通知（`t_string.c:93`）之前的那個 `server.dirty++`。注意 gdb 停下的位置是**改動完成後**（watchpoint 在寫入後觸發，所以顯示 Old/New value 並停在下一行 93）。你等於用一條 `watch` 指令，從幾十個可能的賦值點裡，精準定位到「這次 SET 路徑上真正動 `server.dirty` 的那一處」。

輸出開頭明確寫 `Hardware watchpoint 2`——這是硬體 watchpoint，靠 debug register 加速，幾乎零成本。

> **watchpoint 的兩個現實限制**：(1) 硬體 watchpoint 只有幾個（x86 通常 4 個），且監看範圍有大小上限（一個 register 蓋 8 bytes）。監看一整個大 struct 或超過硬體能力時，gdb 退化成**軟體 watchpoint**——它會單步執行整個程式、每步檢查那塊記憶體，慢到跑一個 SET 要好幾分鐘甚至更久，實務上不可用。所以 watch 要盡量鎖定**單一純量欄位**（如 `server.dirty` 這種 `long`），別 watch 整個物件。用 `info watchpoints` 可確認它是 hardware 還是 software。(2) 若被監看的變數所在的 scope 離開（區域變數的堆疊被回收），watchpoint 會自動失效並提示——這是正常行為，不是錯誤。

## gdb 讀碼的其他常用招式（讀懂導向，非除錯導向）

上面三個是主力。補齊一組你在「讀懂流程」時會反覆用到的：

| 指令 | 讀碼用途 |
|---|---|
| `bt` / `bt N` | 看**這一次**的完整/前 N 層呼叫鏈——最重要的一招 |
| `frame N` / `up` / `down` | 在 backtrace 各層之間切換，到某層去 print 它的區域變數 |
| `info args` / `info locals` | 印出當前函式的所有參數 / 區域變數——快速掃「此刻的完整狀態」 |
| `next`（n） | 執行一行，**不進入**被呼叫的函式——用來「快進」跳過你不關心的子呼叫 |
| `step`（s） | 執行一行，**進入**被呼叫的函式——用來「鑽進去」看某個呼叫內部怎麼走 |
| `finish` | 執行完當前函式、停在它 return 後——看「這個函式回傳什麼」而不想逐步看它內部 |
| `print expr` / `p/x expr` | 印出任意運算式的值（`/x` 十六進位）——看資料結構此刻的內容 |
| `ptype T` / `p *ptr` | 印出型別佈局 / 解引用整個 struct——快速理解一個陌生資料結構 |
| `display expr` | 每次停下自動印某個運算式——盯著一個變數隨執行變化 |

**`next` vs `step` 是讀碼節奏的關鍵**：你在一個函式裡逐步走，遇到一個你已經懂、不關心的子呼叫（如 log、malloc），用 `next` 一步跳過；遇到「這個呼叫內部我要搞懂」的，用 `step` 鑽進去。`finish` 則是「我進錯了/看夠了，直接跑到這函式結束」的快速逃生。這三個組合起來，讓你像用遙控器一樣控制「讀碼的解析度」——重點區逐行慢放，無聊區快轉。

`info args` / `info locals` 是每次停在陌生函式時的第一反射：一條指令看清「此刻所有變數的值」，比逐個 `print` 快。

## 對比與取捨：靜態讀 vs 動態讀

| 面向 | 靜態讀（rg/cscope/clangd） | 動態讀（gdb） |
|---|---|---|
| 回答的問題 | **可能的**控制流、符號在哪、型別是什麼 | **實際的**控制流、變數此刻的值、真正走的分支 |
| 函式指標/動態 dispatch | 只能列出所有候選，不知這次走哪個 | backtrace 直接顯示這次的真實目標 |
| 覆蓋率 | 看得到所有 code，包括沒被執行的 | 只看得到**被實際觸發**的路徑 |
| 前置成本 | 幾乎零（建索引即可） | 要能編譯（debug build）、要能構造觸發輸入 |
| 速度 | 秒級 | 要啟動、下斷、構造輸入、單步——分鐘級 |
| 最適合 | 建全局地圖、找定義、掃全部可能性 | 攻堅一條**具體**路徑、驗證假設、看真實狀態 |
| 盲點 | 不知道「這次實際怎麼走」 | 沒觸發到的路徑完全看不到；`<optimized out>` |

**正確用法是接力，不是二選一**：先用靜態工具建出「這個功能大概涉及哪些函式」的地圖（Ch 5–9），形成假設（Ch 10）；當靜態讀到某個「我猜它會走這條，但不確定」的岔路口，切換到 gdb，用一個真實輸入驗證。**gdb 是用來「確認假設」和「穿透靜態盲點」的，不是用來從零建地圖的**——用 gdb 逐步走完一個十萬行系統會累死，方向感也差。先靜態、後動態、動態驗證靜態的假設。

## 踩雷集錦

1. **對 release build 下 gdb，函式名/變數名全查不到**。
   - 錯誤直覺：「gdb 怎麼認不得 `processCommand`？」
   - 正確認識：沒有 `-g` 就沒有 DWARF，gdb 只剩裸位址。讀碼一定用 debug build（`file` 確認有 `with debug_info, not stripped`）。發行版套件裝的 redis 通常 stripped，要自己編。

2. **看到 `<optimized out>` 以為 gdb 出錯**。
   - 錯誤直覺：「這變數明明存在，怎麼印不出來？」
   - 正確認識：`-O2` 下編譯器把變數塞暫存器並覆用，該點無法可靠取值。切 `frame` 到它還活著的位置、或重編 `-O0` 版。函式呼叫鏈（bt）本身不受影響，多數讀碼夠用。

3. **在超熱點函式裸下無條件斷點，被無關命中淹死**。
   - 錯誤直覺：「怎麼一直停，根本看不到我要的那次？」
   - 正確認識：熱點函式（`processCommand`、malloc）用 **conditional breakpoint** `if <條件>` 只攔你關心的那次，或用 `ignore <bp> <n>` 跳過前 n 次。

4. **watch 一個大 struct，程式慢到像當機**。
   - 錯誤直覺：「gdb 卡死了。」
   - 正確認識：範圍超過硬體 watchpoint 能力，退化成軟體 watchpoint（每步都檢查），慢幾個數量級。**只 watch 單一純量欄位**（`server.dirty` 這種 `long`），不要 watch 整個物件。`info watchpoints` 可確認它是 hardware 還是 software。

5. **用 gdb 從零讀一個十萬行系統，逐步走到迷路**。
   - 錯誤直覺：「動態讀最準，那我全程 gdb 就好。」
   - 正確認識：動態讀只看得到觸發到的路徑、且逐步很慢，沒有全局視野會迷失方向。**先用靜態工具建地圖與假設，gdb 只用來攻堅/驗證具體的一條路徑**。

6. **redis 在 gdb 裡因某些 signal 被 gdb 攔停，以為程式崩了**。
   - 錯誤直覺：「redis 一跑就收到 signal，是不是壞了？」
   - 正確認識：redis 有些正常機制會用 signal（如 watchdog 的 `SIGALRM`）。若被無關 signal 干擾，用 `handle SIGALRM nostop noprint pass` 讓 gdb 放行不攔截，專心看你要的斷點。

## 進階：再往深一層

- **`gdb` 的 `commands` 腳本與 `-batch`/`-x`**：把整套「下斷 → 命中後 bt + print → continue」寫進腳本，用 `gdb -batch -x script.gdb ./prog` 非互動跑完，輸出可存檔可 diff。本章所有範例就是這樣跑的——這讓「動態讀碼」變得**可重現、可貼進筆記**，而不是每次手動敲一遍。

- **reverse debugging（`record` / `reverse-next` / `reverse-continue`）**：gdb 能記錄執行、然後**倒退**。讀碼時的殺手級用法：程式已經跑過某個關鍵點但你沒停下、想回頭看，`reverse-continue` 直接倒回上一個斷點，不用重跑。代價是 record 模式很慢、記憶體吃重，適合短程式段。

- **Python API 與 pretty-printer**：gdb 內嵌 Python，可寫 pretty-printer 讓複雜資料結構（如 redis 的 SDS、dict、quicklist）`print` 出來就是人類可讀的形式，而非一堆指標。讀一個大量使用自訂容器的 codebase 時，先寫幾個 pretty-printer，後面的動態讀會順暢十倍。這是 gdb 課會深入的主題。

- **`rbreak` 正則批次下斷**：`rbreak ^cluster` 一次在所有 `cluster` 開頭的函式下斷——想「這一次請求碰到了哪些 cluster 函式」時，批次下斷 + `bt` 每次命中，快速勾勒一個子系統被觸碰的範圍。

- **attach 到 running 行程**：不一定要在 gdb 裡啟動。`gdb -p <pid>` attach 到一個已在跑的 redis，在你懷疑有事發生的函式下斷，等真實流量觸發。這對「讀懂生產環境某個偶發路徑」特別有用（前提是那台機器有 debug symbols）。

## 動手練習

在 debug 版 redis 上做（都能真跑）：

1. **重現本章 backtrace**：自己在 `processCommand` 下斷、送一個 `LPUSH mylist a b c`，貼出你的 `bt` 與 `print c->argc`。確認 argc 是 5，並從 backtrace 說出「這個命令從 socket 到 processCommand 經過哪幾個函式」。

2. **conditional breakpoint 精準捕捉**：在 `processCommand` 下條件斷點只攔 `INCR` 命令（`$_streq((char*)c->argv[0]->ptr, "incr")`），連續送 `set c 1`、`get c`、`incr c`，驗證只有 `incr` 那次停下。

3. **watchpoint 找改動點**：對某個 key 先 `set k 0`，然後在 `incrDecrCommand` 下斷、對記錄變更次數的欄位（`server.dirty`）下 watchpoint，送 `incr k`，看它在哪一行被改，並與本章 SET 的結果對比。

4. **`step`/`finish` 控制解析度**：在 `lookupKeyRead` 下斷，命中後用 `step` 鑽進去幾層看它怎麼查 dict，再用 `finish` 直接跑完它、看它回傳的 `robj*`，`print *那個robj` 看查到的值。

5. **`<optimized out>` 對照實驗**：重編一份 `-O0` 的 redis（`make CFLAGS="-O0 -g" -j$(nproc)`），在同一個函式下斷，比較 `info args` 的輸出——體會 `-O2` 下多少變數是 `<optimized out>`、`-O0` 下如何全部現形。

## 本章重點整理

- 動態讀碼的核心價值：**把「可能的控制流」坍縮成「這一次的事實」**——穿透函式指標、動態 dispatch、巨集、多分支這些靜態盲點。
- 三個機制：斷點靠 `int3`、變數名靠 DWARF debug info、watchpoint 靠 CPU debug register。**讀碼一定用 debug build**。
- `bt` 是最重要的一招：一次看全「這一次執行的完整真實呼叫鏈」。
- conditional breakpoint（`break x if cond`）在熱點函式上只攔你關心的那次；watchpoint（`watch field`）回答「誰、在哪一行改了這個值」。
- `next`/`step`/`finish` 控制讀碼解析度：重點區慢放、無聊區快轉、看夠了逃生。
- `-O2` 下 `<optimized out>` 是正常代價，不是 gdb 壞；需要時切 frame 或重編 `-O0`。
- 用法是**接力**：靜態建地圖與假設 → gdb 攻堅/驗證具體一條路徑。別用 gdb 從零讀大系統。

## 自我檢核

- [ ] 說得出三類「靜態讀撞牆、動態讀能穿牆」的具體情境嗎（函式指標／巨集／哪條分支）？
- [ ] 為什麼讀碼要用 debug build？DWARF 提供了什麼、沒有它 gdb 剩下什麼？
- [ ] `bt` 給你的資訊，跟 cscope 反查呼叫者給你的，本質差別是什麼？
- [ ] 在一個每秒命中幾萬次的函式上，你怎麼只停在「輸入是某個特定值」的那一次？
- [ ] watchpoint 回答什麼問題（斷點回答不了的）？為什麼不能隨便 watch 一個大 struct？
- [ ] `<optimized out>` 是什麼、兩種應對方式是什麼？
- [ ] 為什麼「全程只用 gdb 讀一個大系統」是壞策略？正確的靜態↔動態分工是什麼？

## 延伸閱讀

- **[GDB 官方手冊 — "Breakpoints, Watchpoints, and Catchpoints" 一章](https://sourceware.org/gdb/current/onlinedocs/gdb.html/Breakpoints.html)**
  - **讀哪裡**：Conditional Breakpoints、Set Watchpoints、Breakpoint Command Lists 三節。
  - **學到什麼**：條件斷點、硬體 vs 軟體 watchpoint 的精確語義與限制、`commands` 腳本化——本章三個實戰的權威依據。
  - **前提**：知道斷點基本概念即可。

- **[GDB 官方手冊 — "Examining the Stack"（backtrace/frame）](https://sourceware.org/gdb/current/onlinedocs/gdb.html/Stack.html)**
  - **讀哪裡**：`backtrace`、`frame`、`up`/`down` 三節。
  - **學到什麼**：怎麼在呼叫鏈各層之間穿梭、到某層去看它的區域變數——把一張 `bt` 從「靜態圖」變成「可鑽入的活體」。
  - **關聯**：本章實戰一的深化。

- **[DWARF Debugging Standard — Introduction（dwarfstd.org）](https://dwarfstd.org/doc/DWARF5.pdf)**
  - **讀哪裡**：只看 Section 1（Introduction）建立「debug info 到底存了什麼」的直覺，其餘當字典。
  - **學到什麼**：為什麼 gdb 能用源碼名字問問題、為什麼 `-O2` 會產生 `<optimized out>`——理解 DWARF 就理解了 debugger 能力的邊界。
  - **前提**：有 ELF/編譯流程的粗略概念會更有感。

- **[Redis 源碼 — `src/networking.c` 的 `readQueryFromClient` → `processInputBuffer`](https://github.com/redis/redis/blob/7.4.0/src/networking.c)**
  - **讀哪裡**：對照本章 backtrace，把 `readQueryFromClient`、`processInputBuffer`、`processCommandAndResetClient` 三個函式讀一遍。
  - **學到什麼**：backtrace 給你路徑的「骨架」，回頭讀這三個函式的源碼給你「血肉」——動態定位 + 靜態精讀的標準組合。
  - **關聯**：本章實戰一的靜態補完，也是練習 B 的前置。

backtrace 告訴你「呼叫了哪些函式」，但函式**內部真正執行了哪些系統呼叫、跟 OS 和網路發生了什麼互動**，gdb 逐步走很累。下一章換一組更輕的透視工具：不下任何斷點、不改任何 code，直接在**系統呼叫層與函式庫層**旁觀程式的真實行為。

→ [Ch 19 tracing 讀執行](./19-tracing-execution.md)
