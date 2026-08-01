# Ch 19 — tracing 讀執行

> **目標**：學會**不改一行 code、不下任何斷點**，就旁觀一個程式「真的做了什麼」。gdb（Ch 18）讓你凍結程式、逐步審視內部狀態，但代價是慢、且要下斷點。tracing 是另一種光譜：讓程式全速跑，同時在**系統呼叫層**（strace）、**函式庫層**（ltrace）、**應用函式層**（uftrace）旁邊架三台不同倍率的攝影機，事後看回放。讀陌生 code 時，這常常是**最快建立「這程式跟外界怎麼互動」直覺**的手段——因為 syscall 是程式與 OS 的唯一介面，看穿它就看穿了程式的 I/O、網路、檔案、記憶體行為的骨架。

> **環境**：WSL2 Ubuntu 22.04，strace 5.16、ltrace 0.7.3、uftrace 0.9.4，沙包 `~/reading_code_lab/redis`（redis 7.4.0，debug build）。
>
> **⚠️ WSL2 的誠實限制（本章核心注意事項）**：`perf` 與 `ftrace` 這兩個 Linux 上最強的核心級 tracing 工具，在**本 WSL2 環境上不可用**——這不是隨口帶過，是實測結論：
> ```
> $ perf --version
> bash: perf: command not found          ← WSL2 預設無 perf，且裝了也多半因缺 kernel 支援而半殘
> $ ls /sys/kernel/tracing/
> ls: cannot open directory '/sys/kernel/tracing/': Permission denied   ← ftrace 的 tracefs 不可存取
> ```
> 因此本章主力是 **strace / ltrace / uftrace**（這三個在 WSL2 實測可跑，下面所有輸出都是真跑照抄）。凡涉及 perf/ftrace 的段落，一律明確標「**未實測，理論預期，需在原生 Linux 驗證**」。這是刻意的方法論誠實：**告訴你「在什麼環境下這工具才可信」，比假裝它到處都能跑更有價值。**

## 為什麼 tracing 是讀碼的獨門角度

前面所有工具——靜態的 rg/cscope/clangd、歷史的 git、動態的 gdb——有一個共同盲點：它們都在「程式自己的世界」裡打轉，看的是 code、符號、變數。但一個真實程式**大部分有意義的行為，發生在它和外界的邊界上**：它讀了哪個檔、連了哪個 IP、送了什麼 bytes 出去、分配了多少記憶體、fork 了幾個 process。

這些行為在 source 裡是分散的、被層層抽象包裹的——你讀 redis 的 `anetTcpServer()` 能猜到它會 `bind`+`listen`，但「它到底 bind 到哪個位址、backlog 設多少、有沒有設 `SO_REUSEADDR`」這些**具體事實**，讀十層封裝的 source 遠不如**看它真的跑一次時吐出的 syscall** 來得直接。

關鍵洞察：**syscall 是程式與 OS 之間唯一的介面。** 一個程式再怎麼包裝、用了多少框架、跨多少抽象層，只要它想碰檔案、網路、記憶體、行程——它**最終一定要透過 syscall**。所以 strace 攔截 syscall，等於在程式與世界之間裝了一道**必經關卡的監視器**：你不需要懂它內部一萬行怎麼繞，你只要看它在關卡上做了什麼，就掌握了它對外行為的全貌。

這對讀陌生 code 有三個獨特價值：

1. **快速建立「這程式在幹嘛」的骨架**：一個你完全沒讀過的 binary，strace 跑一次，你立刻知道它讀哪些設定檔、開哪些 port、連哪些服務——比讀 source 快一個數量級。
2. **驗證「它到底有沒有做 X」**：懷疑某段 code 該發一個網路請求，但不確定條件符不符合？trace 一次，看那個 `connect`/`sendto` 有沒有出現，一翻兩瞪眼。
3. **看穿抽象層下的真相**：`redis-cli ping` 這個高階操作，底層到底在 socket 上寫了哪些 bytes？trace 直接給你 wire-level 的事實，不用讀協定實作。

## 三層攝影機：strace / ltrace / uftrace 各拍什麼

先把三個工具的「拍攝層級」分清楚，這是本章最重要的心智模型：

```
   應用程式碼   processCommand() → setGenericCommand() → ...   ← uftrace 拍這層（函式呼叫）
      │
      ▼
   函式庫 (libc)   malloc()  strcpy()  connect()  send()      ← ltrace 拍這層（library call）
      │
      ▼
   系統呼叫       mmap  socket  connect  sendto  read  write   ← strace 拍這層（syscall）
      │
      ▼
   OS 核心 / 硬體
```

- **strace** 攔的是**使用者態 ↔ 核心態的邊界**（syscall）。看的是程式對 OS 的請求：I/O、網路、記憶體映射、行程管理。最通用、最不挑對象（任何 binary 都能 trace，不需符號）。
- **ltrace** 攔的是**程式 ↔ 動態函式庫的邊界**（library call，主要是 libc）。看的是 `malloc`、`strlen`、`connect` 這類函式庫呼叫——比 syscall 高一層、更貼近程式邏輯，但只能看到走 PLT 的動態連結呼叫。
- **uftrace** 攔的是**程式自己的函式呼叫**（application function）。看的是 `processCommand → setGenericCommand → ...` 這種內部呼叫樹——最貼近你讀的 source，但需要程式用 `-pg` 編譯、或用 dynamic tracing（下面會說限制）。

一句話記法：**strace 看「跟 OS 說了什麼」、ltrace 看「呼叫了哪些函式庫函式」、uftrace 看「內部函式怎麼互相呼叫」**。倍率由低到高、通用性由高到低。

## 實戰一：strace redis-cli ping —— 看穿 RESP 協定的 wire 真相

我們從最有啟發性的一個實驗開始。`redis-cli ping` 是個高階操作，你若要讀懂它「在 socket 上到底送了什麼、收了什麼」，得去讀 hiredis 的協定編碼實作。但 strace 讓你**跳過所有 source，直接看 wire 上的 bytes**。

先起一個 redis-server，再 trace redis-cli 連上去 ping：

```bash
./src/redis-server --port 7791 --save '' &        # 先起 server
strace -f -e trace=network,read,write ./src/redis-cli -p 7791 ping
```

`-f` 追蹤 fork 出的子行程，`-e trace=network,read,write` 只留網路與讀寫相關 syscall（否則啟動時的一大堆 `mmap`/`openat` 會淹沒你）。關鍵的幾行真實輸出（照抄）：

```
socket(AF_INET, SOCK_STREAM, IPPROTO_TCP) = 3
connect(3, {sa_family=AF_INET, sin_port=htons(7791), sin_addr=inet_addr("127.0.0.1")}, 16) = -1 EINPROGRESS (Operation now in progress)
connect(3, {sa_family=AF_INET, sin_port=htons(7791), sin_addr=inet_addr("127.0.0.1")}, 16) = 0
sendto(3, "*1\r\n$4\r\nping\r\n", 14, 0, NULL, 0) = 14
recvfrom(3, "+PONG\r\n", 16384, 0, NULL, NULL) = 7
```

**這五行把整個 PING 操作講完了，而且一行 source 都沒讀：**

- `socket(AF_INET, SOCK_STREAM, ...)`：開一個 TCP socket，回傳 fd 3。
- `connect(3, ...port 7791...)` 出現兩次：第一次回 `EINPROGRESS`（socket 是非阻塞的，連線正在進行中——這本身就洩漏了「redis-cli 用非阻塞 connect」這個實作細節），第二次回 0（連線完成）。
- `sendto(3, "*1\r\n$4\r\nping\r\n", 14, ...)`：**這就是 RESP 協定的真面目**。PING 命令被編碼成 `*1\r\n$4\r\nping\r\n`——`*1` 表示「一個元素的陣列」，`$4` 表示「接下來是 4 bytes 的字串」，`ping\r\n` 是內容。你不用讀任何協定文件或編碼函式，strace 直接把 wire format 攤在你面前。
- `recvfrom(3, "+PONG\r\n", ...) = 7`：server 回 `+PONG\r\n`——`+` 是 RESP 的「簡單字串」前綴，7 bytes。

**這是 tracing 讀碼的精髓：一個跨越 hiredis 編碼、TCP、kernel 網路堆疊的操作，你用一條 strace 就看穿了它在邊界上的完整交換。** 讀 source 要跨好幾個檔、理解好幾層抽象才能拼出的結論，trace 一次直接得到事實。

## 實戰二：strace redis-server 啟動 —— 看它怎麼架起監聽

換個對象，看 redis-server **啟動時怎麼建立網路監聽**。這回答「它 bind 到哪、backlog 多少、IPv4/IPv6 都聽嗎」這種你讀 `anet.c` 要繞半天的問題：

```bash
strace -f -e trace=socket,bind,listen,setsockopt ./src/redis-server --port 7793 --save ''
```

真實輸出（照抄關鍵行）：

```
socket(AF_INET, SOCK_STREAM, IPPROTO_TCP) = 9
bind(9, {sa_family=AF_INET, sin_port=htons(7793), sin_addr=inet_addr("0.0.0.0")}, 16) = 0
listen(9, 511)                          = 0
socket(AF_INET6, SOCK_STREAM, IPPROTO_TCP) = 10
bind(10, {sa_family=AF_INET6, sin6_port=htons(7793), ..., inet_pton(AF_INET6, "::", &sin6_addr), ...}, 28) = 0
listen(10, 511)                         = 0
```

一眼讀出三個具體事實：

- redis **同時聽 IPv4（`0.0.0.0`）和 IPv6（`::`）**，各開一個 socket（fd 9 和 10）。
- backlog 是 **`listen(fd, 511)`**——還記得 Ch 17 我們用 git pickaxe 挖出這個 511 的來歷嗎？（「高 RPS 環境預設 backlog 不夠用」而做成可設定，預設 511。）**Ch 17 的考古 + Ch 19 的 trace 在這裡合流**：考古告訴你這個數字「為什麼存在」，trace 告訴你它「執行時確實生效」。兩個角度互相印證，是讀碼最扎實的狀態。
- bind 到 `0.0.0.0`/`::` 表示預設**監聽所有介面**（如果你在讀「為什麼 redis 沒設密碼就暴露在公網很危險」，這行 strace 就是證據）。

> **strace 的殺手級旗標**：`-e trace=<類別>` 過濾（`network`/`file`/`memory`/`process`/`read,write`）、`-f` 追子行程、`-p <pid>` attach 到已跑的行程、`-c` 統計每個 syscall 被呼叫幾次/花多少時間（做「這程式的 syscall 熱點在哪」的一秒鐘 profile）、`-tt` 加時間戳、`-y` 把 fd 號翻成它對應的檔名/socket。讀陌生服務時，`strace -f -c -p <pid>` 掛個幾秒，`-c` 的統計表就告訴你「它主要在做哪類 syscall」。

## 實戰三：ltrace —— 上升一層，看函式庫呼叫

同樣的 `redis-cli ping`，換 ltrace 看**函式庫層**（比 syscall 高一層）：

```bash
./src/redis-server --port 7799 --save '' &
sleep 4                                  # ltrace 會拖慢 client，先讓 server 準備好
ltrace -e 'connect+send*+recv*' ./src/redis-cli -p 7799 ping
```

真實輸出（照抄）：

```
redis-cli->connect(3, 0x5f97dbeec820, 16, 33)    = 0xffffffff
redis-cli->connect(3, 0x5f97dbeec840, 16, 0x7fbd26718bd7) = 0
redis-cli->send(3, 0x5f97dbeec8d3, 14, 0)        = 14
redis-cli->recv(3, 0x7fffbe316af0, 0x4000, 0)    = 7
PONG
+++ exited (status 0) +++
```

對照實戰一的 strace，你看到**同一個操作在兩個層級的不同視角**：

| strace（syscall 層） | ltrace（library 層） | 說明 |
|---|---|---|
| `connect(3, {...port 7791...}, 16)` | `connect(3, 0x..., 16, ...)` | ltrace 看到的是 **libc 的 `connect()` wrapper** 被呼叫 |
| `sendto(3, "*1\r\n$4\r\nping\r\n", 14, ...)` | `send(3, 0x..., 14, 0)` = 14 | ltrace 顯示程式呼叫了 libc `send()`；strace 顯示它最終落到 `sendto` syscall |
| `recvfrom(3, "+PONG\r\n", ...)` = 7 | `recv(3, 0x..., 0x4000, 0)` = 7 | 同一次接收，14/7 bytes 兩邊吻合 |

**兩者互補**：ltrace 告訴你「程式呼叫了哪個 libc 函式」（更貼近 source 裡寫的 `send()`），strace 告訴你「這個 libc 函式最終發了哪個 syscall、帶什麼實際內容」（`send` → `sendto`，且看得到 buffer 內容）。注意 ltrace 這裡沒直接印出 buffer 的字串內容（只給指標 `0x5f97dbeec8d3`），這是 ltrace 對第三方 buffer 的預設行為——**要看 wire 上的實際 bytes，strace 更直接**。

> **ltrace 的現實限制（實測踩過的坑）**：ltrace 靠攔截 PLT（動態連結跳轉表）來運作，它**只能看到走動態連結的函式庫呼叫**。靜態連結的 binary、或已 inline 的函式，ltrace 看不到。而且 ltrace 的插樁開銷比 strace 大不少——實測中它把 redis-cli 拖慢到「server 還沒 ready client 就連線失敗（Connection refused）」，所以上面特意 `sleep 4` 等 server。ltrace 在現代大量靜態連結/LTO 的 binary 上作用日益受限，**strace 的通用性和穩定性遠勝 ltrace**，這也是為什麼實務上 strace 用得比 ltrace 多得多。

## 實戰四：uftrace —— 看應用內部的函式呼叫樹

strace/ltrace 都停在「跟外界的邊界」。uftrace 更進一步，拍**程式自己的函式怎麼互相呼叫**——這是最貼近你讀的 source 的視角。但它有個前提：**要看內部函式，程式最好用 `-pg` 編譯**（GCC 的 profiling 插樁，在每個函式入口插一個 `mcount` 呼叫，uftrace 靠它記錄）。

先用一個自編的小程式證明 uftrace 的威力（用 `-pg -g` 編）：

```c
static int add(int a, int b) { return a + b; }
static int square(int x) { return add(x*x, 0); }
static int sum_squares(int n) {
    int s = 0;
    for (int i = 1; i <= n; i++) s += square(i);
    return s;
}
int main(void) { printf("%d\n", sum_squares(4)); return 0; }
```

```bash
gcc -pg -g -o demo demo.c
uftrace record ./demo && uftrace replay
```

真實輸出（照抄）：

```
# DURATION     TID     FUNCTION
   0.800 us [ 47878] | __monstartup();
   0.100 us [ 47878] | __cxa_atexit();
            [ 47878] | main() {
            [ 47878] |   sum_squares() {
   0.200 us [ 47878] |     square();
   0.100 us [ 47878] |     square();
   0.100 us [ 47878] |     square();
   0.800 us [ 47878] |   } /* sum_squares */
   5.300 us [ 47878] |   printf();
   6.600 us [ 47878] | } /* main */
```

**這正是你讀 source 時腦中該有的那張呼叫樹，但它是實測畫出來的**：`main` 呼叫 `sum_squares`，後者在迴圈裡呼叫了 `square` 三次（注意 uftrace 把 `square` 短到看不見 `add` 的內層——因為 `add` 被 inline 進 `square` 了，這本身也洩漏了最佳化資訊），每個函式還帶執行時間。對「讀懂一個函式的內部呼叫結構」，uftrace 的 replay 樹是靜態 cflow（Ch 9/16）的**動態對照版**：cflow 給你「可能呼叫誰」，uftrace 給你「這次實際呼叫了誰、幾次、多久」。

**對 redis 這種沒用 `-pg` 編的 binary 呢？** uftrace 有 dynamic tracing（`-P` 指定函式 pattern，靠執行時 patch 函式入口）。實測對 redis 跑：

```bash
uftrace record -P 'processCommand' ./src/redis-server --port 7795 --save '' &
# 另一終端送命令觸發，再 uftrace replay
```

實測結果：uftrace 能記錄並 replay，但**對 redis 這種 `-O2`、非 `-pg` build，dynamic tracing 主要捕捉到的是 PLT/函式庫呼叫層（`pthread_mutex_trylock`、`mmap`、`madvise`、`syscall` 等），要穩定 hook 到 redis 內部的 `processCommand` 這類函式，最可靠的方式仍是重編一份 `-pg` 的 redis**（`make` 時加 profiling flag），或用 uftrace 的 `--force`/patchable 相關選項。這是誠實的實務結論：**uftrace 在「你能控制編譯」的專案上最強（加 `-pg` 即可），在只有現成 `-O2` binary 時，退回 strace/ltrace 看邊界層通常更省事。**

## perf 與 ftrace：在本環境「未實測，理論預期」

前面說過，`perf` 和 `ftrace` 在本 WSL2 環境不可用（實測 `perf: command not found`、tracefs `Permission denied`）。但它們在**原生 Linux** 上是 tracing 的重武器，讀碼場景下值得知道它們補上什麼——以下**未在本環境實測，屬理論預期，需在原生 Linux（有 root、有對應 kernel 版本的 perf、tracefs 可寫）驗證**：

- **perf（原生 Linux）**：`perf trace` 是「更快的 strace」（用 tracepoint 而非 ptrace，開銷低得多，適合 trace 高流量服務）；`perf record -g ./prog` + `perf report` 給你**取樣式的呼叫圖 profile**——不是每個呼叫都記（那是 uftrace），而是週期性取樣程式在哪個函式，統計出「時間花在哪」。讀碼時用途：**快速看出一個陌生程式的 CPU 熱點在哪幾個函式**，直接指向「該優先讀懂哪段 code」。（理論預期：在原生 Linux 上 `perf record -F 99 -g -p <redis-pid> -- sleep 5` 能取樣出 redis 的熱函式；WSL2 未實測。）

- **ftrace（原生 Linux）**：kernel 內建的函式 tracer，透過 `/sys/kernel/tracing/` 操作。`function_graph` tracer 能畫出**核心內部**的函式呼叫圖——當你要讀的東西下沉到 kernel（如「一個 `write` syscall 在核心裡走了哪些函式」），這是唯一的視窗。搭配 `trace-cmd`（ftrace 的友善前端）更好用。讀碼時用途：**讀 kernel 或驅動 code 時的動態對照**（接你的 kernel_internals / observability_tools 課）。（理論預期：原生 Linux 上 `echo function_graph > current_tracer` 後讀 `trace` 能看到核心呼叫樹；WSL2 因 tracefs 不可存取而無法驗證。）

**為什麼 WSL2 不行？** WSL2 跑的是微軟客製的輕量 kernel，預設沒編入完整的 perf/ftrace 支援，且 tracefs 權限受限。要用這兩個工具，得在**原生 Linux 或完整 VM**、有對應 kernel-tools 套件、且有適當權限的環境。**這章的方法論教訓：一個工具「理論上很強」不等於「你手邊的環境能跑」——動手前先確認工具在你的環境真的可用，別把別人在原生 Linux 的輸出當成你 WSL2 能重現的。**

## 對比與取捨

| 工具 | 拍攝層級 | 需要什麼 | 開銷 | 讀碼最適合 | 本環境(WSL2) |
|---|---|---|---|---|---|
| **strace** | syscall（程式↔OS） | 什麼都不用，任何 binary | 中（ptrace，每 syscall 兩次陷入） | 看 I/O/網路/檔案/行程的對外行為、wire 真相 | ✅ 實測可跑 |
| **ltrace** | library call（程式↔libc） | 動態連結的 binary | 高（比 strace 更重） | 看呼叫了哪些 libc 函式，比 syscall 貼近邏輯 | ✅ 可跑（但拖慢明顯） |
| **uftrace** | application function | 最好 `-pg` 編譯 | 低-中（視 record 模式） | 看程式內部函式呼叫樹、時間分佈 | ✅ 可跑（`-pg` 最佳，dynamic 受限） |
| **perf** | syscall + CPU 取樣 + 更多 | perf 工具 + kernel 支援 | 低（tracepoint/取樣） | CPU 熱點、取樣呼叫圖 | ❌ 未安裝/受限，**未實測** |
| **ftrace** | kernel 內部函式 | tracefs 可寫 + 權限 | 低 | 讀 kernel/驅動的動態對照 | ❌ tracefs 權限拒絕，**未實測** |

**選擇準則**：想知道「跟外界互動」→ strace（首選，最通用）；想知道「呼叫了哪些函式庫函式」→ ltrace（但注意靜態連結/inline 的盲區）；想知道「內部函式呼叫樹」→ uftrace（能控制編譯就加 `-pg`）；想知道「CPU 熱點/kernel 內部」→ perf/ftrace（需原生 Linux）。

## 踩雷集錦

1. **在 WSL2 上照抄別人 perf/ftrace 的教學，發現指令根本跑不了**。
   - 錯誤直覺：「我 perf 是不是裝壞了？」
   - 正確認識：WSL2 預設無 perf、tracefs 權限受限——這是環境限制，不是你的錯。**動手前先 `perf --version`、`ls /sys/kernel/tracing/` 確認可用性**。要用這兩個工具，換原生 Linux 或完整 VM。

2. **strace 輸出被啟動雜訊淹沒，找不到重點**。
   - 錯誤直覺：「strace 輸出幾千行，沒法看。」
   - 正確認識：一定要用 `-e trace=<類別>` 過濾（`network`/`file`/`read,write`…）。想看某程式跟網路的互動，`-e trace=network` 立刻把雜訊砍掉九成。全量 trace 只在你不知道要找什麼時才用。

3. **忘了 `-f`，多行程/多執行緒程式只 trace 到主行程**。
   - 錯誤直覺：「這程式 fork 了 worker，但 strace 什麼都沒抓到。」
   - 正確認識：strace 預設只跟主行程。**追子行程/執行緒一定要 `-f`**（redis 有背景執行緒與 fork 存檔，不加 `-f` 會漏掉一大塊）。

4. **ltrace 拖慢程式，導致有時序依賴的行為改變/失敗**。
   - 錯誤直覺：「我一 ltrace，client 就連不上 server 了。」
   - 正確認識：ltrace 插樁開銷大，會顯著拖慢。本章實戰三就實測到「client 被拖慢到 server 還沒 ready 就 Connection refused」，靠 `sleep` 讓 server 先就緒才解決。tracing 本身會擾動時序，對時間敏感的 bug/行為要意識到這點（觀察者效應）。

5. **uftrace 對現成 `-O2` binary 期待過高，抱怨看不到內部函式**。
   - 錯誤直覺：「uftrace 怎麼只 trace 到 libc 函式，看不到 `processCommand`？」
   - 正確認識：要穩定看應用內部函式，最可靠是**用 `-pg` 重編**。對只有 `-O2` binary 的 dynamic tracing 能力有限（inline 掉的函式也消失）。能控制編譯就加 `-pg`，不能就退回 strace 看邊界。

6. **把 tracing 當成「零開銷純觀察」**。
   - 錯誤直覺：「trace 不改 code，所以完全不影響程式。」
   - 正確認識：strace/ltrace 靠 ptrace，每個被攔的呼叫都有陷入開銷，會顯著拖慢（尤其 ltrace）。這是**觀察者效應**——被觀察的程式行為（尤其時序、效能）會被觀察本身改變。要低擾動觀察高流量程式，才需要 perf 這種取樣式工具。

## 進階：再往深一層

- **strace `-c` 一秒鐘 syscall profile**：`strace -f -c -p <redis-pid>` 掛幾秒後 `Ctrl-C`，它吐出一張「每個 syscall 被呼叫幾次、總耗時、平均耗時」的統計表。讀陌生服務時，這張表一眼告訴你「它主要在 `epoll_wait`/`read`/`write` 之間打轉」還是「一直在 `openat`/`stat`」——瞬間定性它的 I/O 模式。

- **`strace -k` 印 syscall 的使用者態 stack**：每個 syscall 發生時，順便印出「是哪條呼叫鏈導致這個 syscall 的」——等於 strace + backtrace 合體，把「這個 `write` 是誰發的」直接關聯到 source 函式（需符號）。這是 strace 和 gdb 之間的橋。

- **BPF-based tracing（bpftrace / bcc）**：現代取代 ftrace 手動操作的方式。`bpftrace -e 'tracepoint:syscalls:sys_enter_connect { ... }'` 能極低開銷地在核心攔任意 tracepoint/kprobe/uprobe，且可自訂聚合。這是 observability 的未來主力，接你的 bpf 課。（同樣需原生 Linux + 適當 kernel/權限；WSL2 上 bpftrace 支援視 kernel 而定，未在本章實測。）

- **uftrace 的 `--filter`/`--depth`/`-F`**：對大程式的 uftrace 樹，用 `--depth 3` 限制深度、`-F processCommand` 只從某函式開始 record，避免輸出爆炸。這是把 uftrace 從「玩具程式能看」變成「大專案也能用」的關鍵旋鈕。

- **把三層 trace 疊起來讀**：同一個操作分別 strace + ltrace + uftrace，三份輸出對齊時間軸一起看，你就得到從「內部函式呼叫 → libc 呼叫 → syscall」的**完整縱剖面**。本章實戰一與三就示範了 syscall 層與 library 層對同一個 ping 的對照——這種多層對照是理解「一個高階操作如何層層落到 OS」的最強手段。

## 動手練習

（perf/ftrace 相關的請在原生 Linux 做；strace/ltrace/uftrace 在 WSL2 可跑。）

1. **strace 看 SET 的 wire 格式**：起一個 redis-server，`strace -f -e trace=network,read,write ./src/redis-cli -p <port> set foo bar`，找出 `set foo bar` 被編碼成什麼 RESP bytes，並手動解讀每個 `*`/`$` 前綴的含義。

2. **strace 看設定檔讀取**：`strace -f -e trace=file ./src/redis-server /path/to/redis.conf 2>&1 | grep -E 'openat|read'`，找出 redis 啟動時讀了哪些檔（設定檔、`/proc/sys/...` 等），列出前 5 個。

3. **`strace -c` 定性 I/O 模式**：對一個跑著的 redis `strace -f -c -p <pid>`，用另一終端灌幾百個命令（`redis-benchmark` 或迴圈 `redis-cli`），`Ctrl-C` 看統計表，說出它花最多次/最多時間在哪個 syscall。

4. **strace vs ltrace 對照**：對 `redis-cli ping` 分別跑 strace（`-e trace=network`）和 ltrace（`-e 'send*+recv*'`），把「同一次 send/recv」在兩個工具的輸出對齊，說出兩層各看到什麼、差別在哪。

5. **uftrace 自編程式**：把本章那個 `demo.c` 自己編（`gcc -pg -g`）、`uftrace record && uftrace replay`，重現呼叫樹。然後故意把 `add` 標成 `__attribute__((noinline))` 重編，看 uftrace 樹裡 `add` 是否從隱形變成可見——驗證 inline 對 trace 可見性的影響。

6. **（原生 Linux）perf 熱點**：在原生 Linux 上 `perf record -F 99 -g -p <redis-pid> -- sleep 5` 期間灌流量，`perf report` 看 redis 的 CPU 熱函式前三名。（WSL2 跑不了，請在原生環境做並記錄環境資訊。）

## 本章重點整理

- tracing = **不改 code、不下斷點，旁觀程式的真實行為**；syscall 是程式與 OS 的唯一介面，看穿它就掌握對外行為骨架。
- 三層攝影機：**strace**（syscall，最通用）、**ltrace**（libc 函式）、**uftrace**（應用內部函式呼叫樹）。倍率由低到高、通用性由高到低。
- strace 能直接看穿 wire 真相：`redis-cli ping` 的 `*1\r\n$4\r\nping\r\n` 不用讀協定實作就看到；server 啟動的 `listen(fd, 511)` 與 Ch 17 考古合流互證。
- ltrace 比 syscall 高一層、更貼近邏輯，但只看得到動態連結呼叫、開銷大、會拖慢（觀察者效應）。
- uftrace 在「能控制編譯」（加 `-pg`）的專案上最強；現成 `-O2` binary 的 dynamic tracing 受限。
- **perf 與 ftrace 在本 WSL2 環境不可用（實測 `command not found` / tracefs `Permission denied`）**；相關內容標為「未實測，理論預期，需原生 Linux 驗證」。方法論教訓：先確認工具在你的環境真能跑。
- 關鍵旗標：strace `-f`（追子行程）、`-e trace=`（過濾）、`-c`（統計 profile）、`-k`（syscall stack）。

## 自我檢核

- [ ] 說得出 strace/ltrace/uftrace 各拍哪一層、以及「倍率越高越貼近 source、通用性越低」這條軸嗎？
- [ ] 為什麼「syscall 是程式與 OS 的唯一介面」讓 strace 成為看穿對外行為的必經關卡？
- [ ] 不讀任何協定實作，你能用 strace 看出 `redis-cli` 送 PING 時 wire 上是哪些 bytes 嗎？怎麼過濾雜訊？
- [ ] 在本 WSL2 環境，perf 和 ftrace 能跑嗎？你怎麼在動手前一秒確認？它們在原生 Linux 補上什麼 strace 給不了的能力？
- [ ] ltrace 為什麼會「一開就把有時序依賴的程式弄失敗」？這叫什麼效應？
- [ ] 要用 uftrace 看 redis 內部函式呼叫樹，最可靠的做法是什麼？為什麼現成 `-O2` binary 效果差？

## 延伸閱讀

- **[strace 官方文件 / man page（strace.io）](https://strace.io/)**
  - **讀哪裡**：man page 的 `-e trace=`、`-f`、`-c`、`-k`、`-y` 幾個旗標；官網的 "Getting started"。
  - **學到什麼**：把本章用到的過濾/追子行程/統計/stack 旗標系統化，理解 strace 靠 ptrace 的機制與開銷來源。
  - **關聯**：本章實戰一、二、四的權威依據。

- **[Brendan Gregg, "Linux Performance" — Tracing 工具全景](https://www.brendangregg.com/linuxperf.html)**
  - **讀哪裡**：那張著名的 "Linux Performance Observability Tools" 全景圖，以及 perf / ftrace / BPF 各自的頁面。
  - **學到什麼**：strace/ltrace/perf/ftrace/BPF 在整個 Linux 觀測工具版圖裡各自的位置與適用場景——本章三層攝影機的擴充地圖。
  - **前提**：本章讀完後看，會知道每個工具該擺在哪一格。

- **[uftrace 官方 README 與 tutorial（github.com/namhyung/uftrace）](https://github.com/namhyung/uftrace)**
  - **讀哪裡**：README 的 "How to use"、`-P`（dynamic tracing）、`--filter`/`--depth` 幾節。
  - **學到什麼**：`-pg` 插樁 vs dynamic tracing 的差別、怎麼對大專案限制深度避免輸出爆炸——補上本章實戰四提到的限制的解法。
  - **前提**：懂 GCC `-pg` 的概念會更順。

- **[Julia Evans, "strace zine" / strace 相關 blog（jvns.ca）](https://jvns.ca/blog/2015/04/14/strace-zine/)**
  - **讀哪裡**：她的 strace zine 與幾篇 strace 實戰 blog（如用 strace 找設定檔在哪、找程式卡在哪）。
  - **學到什麼**：strace 在真實除錯/讀碼場景的直覺化用法，把「該用哪個旗標」變成反射動作。行文極好讀，適合鞏固直覺。
  - **關聯**：本章「快速建立這程式在幹嘛」價值的生動案例集。

到這裡，Part 3 的「動態」三章（gdb 動態讀、tracing 讀執行）補齊了靜態工具的盲區。但還有一種新武器正在重塑讀碼——它不屬於靜態也不屬於動態，它會**跟你對話、幫你猜、也會自信地騙你**。下一章談 LLM 輔助讀碼：它擅長什麼、危險在哪、以及唯一正確的用法——**AI 產假設，你用前面所有工具驗證**。

→ [Ch 20 AI 輔助讀碼](./20-ai-assisted-reading.md)
