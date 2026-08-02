# Ch 19 — harness 化網路伺服器

> **目標**: 理解網路 fuzzing 的速度瓶頸在哪、每種加速手法的原理與侷限；能用 preeny desock 把不修改源碼的伺服器接進 afl++；能寫出跳過握手的 in-memory harness；評估一個真實目標應該選哪個層級的 harness。
>
> **環境**: WSL2 Ubuntu 22.04，afl++ 4.x（`~/tools/AFLplusplus/`），gcc 11.4，libFuzzer（llvm-14），preeny（`~/tools/preeny/`）。本章標記哪些步驟在本機實測、哪些是理論預期行為。

---

## 為什麼需要這章

Ch 17 和 Ch 18 各自解決了一個不同的問題：AFLNet 讓 fuzzer 理解訊息序列，StateAFL 讓 fuzzer 理解伺服器內部狀態。兩者都還有一個共通的未解問題——**它們都慢**。

AFLNet 每次 execution 要完成一個完整的 TCP 對話：fuzzer 是外部 client，透過 loopback 連進伺服器，等待回應，解析 response code，然後送下一條訊息。這個迴圈在 loopback 上光是 TCP 三向交握就是 1–3ms，加上 epoll wakeup 的 scheduler quantum，加上每個新 session 的 `accept()` 呼叫，實際上每秒能完成的 execution 數大概是 100–500。

相比之下，一個普通的 in-process libFuzzer harness 可以跑到 50,000–200,000 exec/sec。

差距是 100–1000 倍。速度就是覆蓋率——同樣的 CPU 時間，你能探索的狀態數量差了三個量級。

這章的任務是把這個差距縮小。方法有四種，從最容易實施到最快速，依次是：preeny desock、afl++ persistent mode、in-memory harness、snapshot fuzzing（Part 5 預告）。每種方法都有它適合的場景，沒有哪個是萬用的。

---

## 先建立直覺

### 一次 AFLNet execution 的時間都花在哪裡

```
AFLNet exec timeline（loopback，單一訊息對話）
─────────────────────────────────────────────────────────────────────

t=0ms      fuzzer 呼叫 connect()
            │
            ├── TCP SYN 送出 ──────────► kernel: 1ms
            │   SYN-ACK 回來 ──────────► kernel: 1ms
            │   ACK 送出 ───────────────► kernel: 1ms
            │
t=3ms      connect() 返回
            │
            ├── epoll/accept wakeup ────► scheduler quantum: 1–5ms
            │   server accept() 返回
            │
t=5–8ms    server 進入 connection handler
            │
            ├── fuzzer 送 M1 ──────────► loopback I/O + recv(): ~0.5ms
            │   server 處理 M1
            │   server 送 response
            ├── fuzzer 讀 response ─────► parse response code: ~0.1ms
            │
            ├── (重複 M2, M3 ... Mn)
            │
t=10–20ms  fuzzer 關閉 connection
            server close() + TIME_WAIT
            │
t=15–25ms  forkserver 啟動下一個 exec
────────────────────────────────────────────────────────────────────

實際 exec/sec：50–200（單訊息）；訊息越多越慢

對比 in-process fuzzing：
    LLVMFuzzerTestOneInput() call overhead = ~1μs
    exec/sec：50,000–200,000
```

整個 timeline 裡大多數時間不是在跑程式碼，而是在等 kernel。這不是「網路 fuzzing 的不可避免成本」——這是可以被移除的。

---

## 核心概念

### 1. preeny desock：零源碼修改的管道重導

preeny（https://github.com/zardus/preeny）是一組 `LD_PRELOAD` 函式庫。`desock.so` 做一件事：攔截所有 socket 相關的 libc 呼叫，把它們重導向到 stdin/stdout。

它 override 的函式：

| libc 函式 | desock.so 的行為 |
|---|---|
| `socket()` | 返回 `STDIN_FILENO` (0) |
| `accept()` / `accept4()` | 返回 `STDIN_FILENO` (0) |
| `bind()` | no-op，返回 0 |
| `listen()` | no-op，返回 0 |
| `select()` / `poll()` | 重導向監聽 stdin |
| `setsockopt()` | no-op |

伺服器的業務邏輯完全不變。它呼叫 `recv(fd, buf, len, 0)` 還是呼叫 `recv(fd, buf, len, 0)`——只是那個 `fd` 現在是 0（stdin），資料從 pipe 進來而不是從 TCP socket 進來。

**建置 preeny**

```bash
git clone https://github.com/zardus/preeny ~/tools/preeny
cd ~/tools/preeny
make
# 產生 ~/tools/preeny/lib/desock.so
```

**本段在 WSL2 Ubuntu 22.04 實測過。** make 可能需要 `apt install libini-config-dev`。

**使用方式**

以 Ch 16 建的 echo+auth server 為例：

```bash
# 原本啟動方式（等 TCP 連線）：
./server 9999

# desock 版（從 stdin 讀）：
printf "HELLO\r\nAUTH s3cr3t\r\nECHO hello world\r\n" \
    | LD_PRELOAD=~/tools/preeny/lib/desock.so ./server

# 預期輸出：
# 220 Welcome
# 250 Auth OK
# 250 hello world
```

**本段未實測，為理論預期行為。** 實際驗證步驟：確認 `./server` 使用 `recv()`/`send()` 而非 `read()`/`write()`；若 server 使用 `SO_REUSEPORT` 或 epoll ET mode，可能需要 preeny 的 `dealloc.so` 搭配使用。

接進 afl++ 的寫法：

```bash
afl-fuzz \
    -i seeds/         \
    -o output/        \
    -- env LD_PRELOAD=~/tools/preeny/lib/desock.so \
       ./server @@
```

或者用 `AFL_PRELOAD` 環境變數（afl++ 4.x 支援）：

```bash
afl-fuzz \
    -i seeds/         \
    -o output/        \
    -e "env LD_PRELOAD=~/tools/preeny/lib/desock.so" \
    -- ./server @@
```

速度預期：500–2,000 exec/sec。TCP 延遲消失，剩下的 overhead 是 process fork 和 exec。

### 2. afl++ persistent mode：`__AFL_LOOP` 攤薄 fork 成本

afl++ 的 forkserver 在每次 execution 都要 `fork()` 一個新的 server 進程。`fork()` 本身在 Linux 上大約 100–500μs（取決於記憶體頁表大小）。如果 execution 本身只需要 50μs，fork 成本就佔了 90%。

`__AFL_LOOP(N)` 的作用是讓同一個 server 進程處理 N 個 testcase 再退出，把 fork 成本攤薄 N 倍。

修改 server 的 message processing loop：

```c
#include <unistd.h>

// afl++ 提供的 macro，non-afl 環境會 degrade 成無限迴圈
#ifdef __AFL_HAVE_MANUAL_CONTROL
    __AFL_INIT();
#endif

// 原本的 server main loop
while (__AFL_LOOP(1000)) {
    // 從 stdin 讀取一條完整訊息（已用 desock 重導）
    ssize_t n = recv(conn_fd, buf, sizeof(buf), 0);
    if (n <= 0) break;

    // 處理訊息、更新狀態
    handle_message(buf, n, &session);

    // 重置 session 狀態準備下一輪
    memset(&session, 0, sizeof(session));
}
```

`__AFL_LOOP(1000)` 的語意：
- 第 1 到 999 次呼叫：返回 1，繼續跑
- 第 1000 次：返回 0，進程退出，forkserver 重新 fork
- 每次 loop 開始時，afl++ 會從 pipe 讀取下一個 testcase 並寫到 stdin

搭配 desock 使用，速度可到 1,000–10,000 exec/sec，是純 desock 的 5–10 倍。

**注意**：`__AFL_LOOP` 在非 afl++ 環境（普通執行或 libFuzzer）下需要 fallback。標準寫法：

```c
#ifndef __AFL_LOOP
#define __AFL_LOOP(x) (1)
#define __AFL_INIT()
#endif
```

### 3. In-memory harness：直接呼叫解析函式

這是速度最快的方案，也是最需要工程投入的方案。

思路：不跑整個 server binary，直接把 server 的訊息解析函式（message parser）編譯成一個靜態函式庫，寫一個 libFuzzer harness 直接呼叫它。

前置步驟：找出 server 的核心解析函式

```bash
# 用 nm 看 server binary 的符號表
nm -D ./server | grep -i "parse\|process\|handle\|cmd"

# 典型輸出：
# 00012340 T process_command
# 00013ab0 T parse_header
# 00015c20 T handle_auth
```

如果 server 是 closed source，則需要逆向找到目標函式的地址並用 Unicorn/QEMU harness——這是 Ch 34 的主題。這裡假設有源碼。

**典型 libFuzzer harness**：

```c
#include <stdint.h>
#include <stddef.h>
#include <string.h>

// server.o 提供的函式，連結時用 server_lib.a
extern int  process_command(const char *buf, size_t len, void *session);
extern void session_init(void *session);
extern void session_destroy(void *session);

// session struct 的大小從 server 源碼取得
typedef struct session_ctx session_ctx_t;

// libFuzzer 的進入點
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    // 每次 fuzz 一條訊息
    if (size == 0 || size > 65535) return 0;

    session_ctx_t session;
    session_init(&session);

    // 關鍵：手動把 session 推進到 AUTHENTICATED 狀態
    // 跳過握手，直接 fuzz 認證後的命令處理器
    session.auth_ok = 1;        // server.h 裡定義的欄位
    session.state   = STATE_CMD; // 跳過 HANDSHAKE 狀態

    // 直接呼叫目標函式
    process_command((const char *)data, size, &session);

    session_destroy(&session);
    return 0;
}
```

編譯：

```bash
# 先把 server 編成靜態函式庫
gcc -c server.c -o server.o -fsanitize=address,fuzzer-no-link
ar rcs libserver.a server.o

# 編譯 harness
clang -fsanitize=address,fuzzer \
    harness.c libserver.a      \
    -o fuzz_server
./fuzz_server seeds/
```

這個 harness 的速度：50,000–200,000 exec/sec，接近 libFuzzer 的理論上限。

### 4. 把 forkserver hook 點移到握手之後

對想用 afl++（而不是 libFuzzer）的場景，有一個折衷：在 server 源碼裡，等握手完成後再插入 `__AFL_INIT()`。

```c
int main() {
    // ... bind, listen, accept ...

    // 完成握手流程
    int result = do_handshake(conn_fd);
    if (result != AUTH_OK) { close(conn_fd); return 1; }

    // 在這裡插入 __AFL_INIT()
    // 之後的每次 fork 都從「已認證」狀態開始
#ifdef __AFL_HAVE_MANUAL_CONTROL
    __AFL_INIT();
#endif

    // fuzz 這裡的命令處理邏輯
    while (__AFL_LOOP(1000)) {
        ssize_t n = recv(conn_fd, buf, sizeof(buf), 0);
        process_command(buf, n, &session);
    }
}
```

搭配 desock，這等於「snapshot-on-authenticated-state」的軟體模擬版。fork 發生在握手之後，每次 exec 直接從認證狀態開始處理命令。

速度：1,000–10,000 exec/sec（與 persistent mode 相當）。

---

## 底層機制

### 執行模型光譜

```
速度 (exec/sec)          實作複雜度              手法
─────────────────────────────────────────────────────────────────────

  200K ┤                 ████████████████████   in-memory harness
       │                                        (libFuzzer 直呼函式)
       │
  50K  ┤                 ██████████████         afl++ persistent mode
       │                                        + desock
       │
  10K  ┤                 █████████              forkserver hook 移到
       │                                        握手後 + __AFL_INIT
       │
  2K   ┤                 ██████                 desock + forkserver
       │                                        (不改 server 邏輯)
       │
  500  ┤                 ████                   AFLNet / StateAFL
       │                                        (外部 TCP client)
       │
  100  ┤                 ██                     原始 client replay
       │                                        (無 fuzzing instrumentation)
       │
  10   ┤                 █                      人工測試

─────────────────────────────────────────────────────────────────────
    需要源碼？    無需       無需       需要       需要
    保留 TCP 語意？ 否        否         否         否
```

### desock 攔截路徑

```
server binary                          kernel
     │                                    │
     │  socket(AF_INET, SOCK_STREAM, 0)  │
     │──────────────────────────────────►│  ← 原本：kernel 建立 socket
     │                                    │
     │              LD_PRELOAD 攔截
     │
     ▼
 desock.so::socket()
     │
     └──► return STDIN_FILENO (0)    ← 不進 kernel，直接返回 fd=0
          (從此所有 recv/send 操作
           都對 fd=0 即 stdin 操作)

 後續 accept() 也返回 STDIN_FILENO
 bind() / listen() → no-op

結果：
 afl++ forkserver ──pipe──► stdin (fd=0) ──► server recv()
                 ◄──pipe── stdout (fd=1) ◄── server send()
```

這個機制的關鍵限制：server 必須用 `recv()`/`send()` 或 `read()`/`write()` 操作 socket fd。如果 server 是 event-driven（epoll/kqueue），desock 需要同時 shim `epoll_wait()` 的返回值——preeny 的 `desock_dup.so` 處理這個案例，但行為更複雜。

---

## 進階用法

### 同時跑兩個 fuzzer 實例覆蓋不同狀態深度

In-memory harness 跳過握手是對的，但你也不能放棄握手階段的 bug。正確做法是跑兩個獨立的 fuzzer 實例，共享同一個 afl++ 的 `-x` 字典但用不同的 harness：

```
fuzzer-A: harness-A.c
    session.state = STATE_HANDSHAKE   ← fuzz 握手流程
    目標：找 parse_greeting(), handle_auth() 的 bug
    速度：~100K exec/sec

fuzzer-B: harness-B.c
    session.state = STATE_CMD         ← 跳過握手，fuzz 命令處理
    目標：找 process_command() 的 bug
    速度：~200K exec/sec

afl-fuzz -S fuzzer-a -i seeds/ -o output/ -- ./harness-a
afl-fuzz -S fuzzer-b -i seeds/ -o output/ -- ./harness-b
```

兩個 fuzzer 透過 `-o output/` 的 shared corpus 交叉喂料。

### desock 與 AddressSanitizer 同時啟用

preeny desock 是 LD_PRELOAD，ASAN 也是 LD_PRELOAD。兩個同時用需要正確排序，ASAN 必須在 desock 之前載入（ASAN 要先初始化 allocator）：

```bash
LD_PRELOAD="libasan.so.5:~/tools/preeny/lib/desock.so" \
    ASAN_OPTIONS=detect_leaks=0 \
    afl-fuzz -i seeds/ -o output/ -- ./server
```

`detect_leaks=0` 因為 server 的正常 teardown 可能在 desock 環境下無法完整執行，會誤報 leak。

### 手動 corpus 初始化：不要從空白開始

In-memory harness 初始化 `session.state = STATE_CMD` 後，fuzzer 並不知道這個狀態合法的輸入長什麼樣。種子一開始給合法的命令字串：

```bash
mkdir seeds/
echo -ne "ECHO hello\r\n"          > seeds/01_echo
echo -ne "LIST /home\r\n"          > seeds/02_list
echo -ne "GET /etc/passwd\r\n"     > seeds/03_get
echo -ne "SET key value\r\n"       > seeds/04_set
```

這讓覆蓋率從第一秒就能超過握手閘。

---

## 對比取捨

| 手法 | 速度 (exec/sec) | 需要源碼 | 保留狀態機 | 可用 ASAN | 實作難度 | 適用場景 |
|---|---|---|---|---|---|---|
| AFLNet / StateAFL | 50–500 | 否 | 完整 | 困難 | 低 | 快速探勘、無源碼 |
| desock + forkserver | 500–2K | 否 | 完整（對 pipe） | 可 | 低 | 有源碼但不想改 |
| persistent mode + desock | 1K–10K | 需要修改 | 部分（per-loop 重置） | 可 | 中 | 長期深度 fuzz |
| __AFL_INIT 移後握手 | 1K–10K | 需要修改 | 從認證後開始 | 可 | 中 | 目標是深層命令 handler |
| in-memory harness | 50K–200K | 需要 + 需理解架構 | 手動初始化 | 可 | 高 | 生產級 CVE 狩獵 |
| snapshot (Nyx/kAFL) | 10K–100K | 否（binary） | 完整（VM 快照） | 依 guest | 高 | closed source 深層狀態 |

---

## 踩雷

- **錯誤直覺**: desock 只是工程 trick，不影響 bug finding 能力，所以不值得花時間設定。
  **正確認知**: 速度就是覆蓋率。同樣的 CPU 時間，100x 更快代表 100x 更多路徑被探索、更多邊界條件被觸碰。AFLNet 跑 24 小時能到的覆蓋率，desock + persistent mode 可能 30 分鐘就超過了。

- **錯誤直覺**: in-memory harness 跳過握手就看不到握手前的 bug，所以這種 harness 會漏 CVE。
  **正確認知**: 正確策略是**同時跑兩個** fuzzer 實例——一個從 STATE_HANDSHAKE 開始、一個從 STATE_CMD 開始。兩個 harness 共享 corpus。單一實例如果只跑深層狀態確實會漏掉握手 bug，但這是策略問題不是手法問題。

- **錯誤直覺**: preeny 會改變 server 的業務邏輯，desock 版本跑出的 crash 可能是誤報（只在 desock 環境下才會 crash）。
  **正確認知**: desock 只攔截 socket lifecycle 相關的 syscall（`socket`、`bind`、`listen`、`accept`）。`recv()`/`send()` 的呼叫路徑、buffer 的解析邏輯、狀態機的轉換——全部不動。desock 找到的 buffer overflow 在真實 TCP 環境下同樣存在。唯一的例外是 crash 本身涉及 `getsockname()`、`getpeername()` 等查詢 socket metadata 的呼叫，desock 環境下這些會返回空值，可能觸發本來不會觸發的 code path。

- **錯誤直覺**: epoll-based server（nginx、lighttpd 之類）用 desock 應該直接就能跑。
  **正確認知**: epoll ET mode 的 server 依賴 `EPOLLIN` 事件觸發才會呼叫 `recv()`。preeny 的 `desock.so` 不 shim `epoll_create()`/`epoll_ctl()`/`epoll_wait()`，所以 epoll server 的事件迴圈不會被觸發，server 會卡在 `epoll_wait()` 永遠等不到事件。對這類 server 要用 `desock_dup.so` 加上額外的 shim，或改用 snapshot 方案。

---

## 進階延伸

**LibAFL 的 In-Process-Fork executor**：LibAFL 把 persistent mode 的概念做得更乾淨——`InProcessForkExecutor` 在同一個進程內 fork，不需要修改 target 源碼，coverage map 在 parent 和 child 之間共享。這是 Ch 7 的 executor family 在 server 場景的自然延伸。

**符號連結 vs 函式指針表**：如果 server 用函式指針表實作命令 dispatch（很多 FTP/SMTP server 這樣做），in-memory harness 可以直接針對 dispatch table 裡的每個 handler 各寫一個 harness，實現 per-handler 的精準 fuzz，不需要走完整的 message parsing 路徑。

**GDB + preeny**：`LD_PRELOAD=desock.so gdb ./server` 完全合法。你可以在 gdb 裡跑 desock server、手動 `echo` payload 進 stdin，然後設 breakpoint 在 `process_command`，一步步看 server 的狀態轉換。這是 harness 開發的標準偵錯手法。

**SanCov + desock 的覆蓋率視覺化**：afl++ 產生的 `.cov` 檔可以用 `afl-showmap` 轉換成可讀的覆蓋率報告。搭配 llvm-cov 的 HTML 報告，你能直接看到 in-memory harness 有沒有真的到達你想測的程式碼路徑。

---

## 動手練習

1. 把 Ch 16 建的 echo+auth server 加上 `__AFL_LOOP(1000)`（不加 desock），編譯並跑 afl-fuzz。記錄 exec/sec。然後加上 desock，再跑一次記錄 exec/sec。比較兩個數字，確認倍率差異與本章的理論預期一致。

2. 閱讀 server.c 的源碼，找出 `process_command()` 函式的 signature。寫一個 libFuzzer harness，把 `session.state` 設成 `STATE_CMD`（或你找到的對應常數），編譯並確認 libFuzzer 能正常啟動、exec/sec 在 10K 以上。

3. 在 harness 2 的基礎上，把 `session.state` 改回 `STATE_HANDSHAKE`，觀察覆蓋率是否下降（因為 fuzzer 大多數輸入在握手階段就被拒絕了）。這個實驗是為了親身感受「跳過握手」這個設計決策的實際影響。

4. 用 `nm -D ./server` 列出所有 T（text section）符號，找出至少兩個除了 `process_command` 以外的候選 handler 函式，評估各自適合哪種 harness 策略（直接呼叫 vs. 要先設置複雜的前置狀態）。

---

## 本章重點

- AFLNet/StateAFL 的速度瓶頸來自 TCP connect overhead 和 scheduler quantum，不是協定狀態機本身的限制。
- desock（preeny）用 LD_PRELOAD 把 socket I/O 重導到 stdin/stdout，不修改源碼，速度從 50–500 exec/sec 提升到 500–2K exec/sec。
- `__AFL_LOOP(N)` 把 fork 成本攤薄 N 倍，搭配 desock 可達 1K–10K exec/sec。
- In-memory harness 直接呼叫解析函式，手動初始化 session 狀態，可達 50K–200K exec/sec，是速度最快的方案。
- 跳過握手不代表不測握手——用兩個 fuzzer 實例分別覆蓋不同狀態深度。
- epoll-based server 不能直接用 `desock.so`，需要 `desock_dup.so` 或改用 snapshot 方案。
- Snapshot fuzzing（Part 5）是解決 closed source、epoll server、無法修改源碼場景的終極手段。

---

## 自我檢核

- [ ] 我能說出一次 AFLNet exec 的時間預算裡，最大的三個開銷分別是什麼。
- [ ] 我知道 preeny desock 攔截哪些 libc 函式、不攔截哪些，以及 epoll server 為什麼需要特別處理。
- [ ] 我能不看筆記說出 `__AFL_LOOP(N)` 的語意——第幾次呼叫返回 0、afl++ 在每次 loop 之間做什麼。
- [ ] 我知道 in-memory harness 裡為什麼要手動設 `session.state`，以及不設的話會發生什麼。
- [ ] 我能解釋「跑兩個 fuzzer 實例覆蓋不同狀態深度」的策略，以及為什麼這比單一 harness 更好。
- [ ] 我能填出本章對比表裡每一欄的數值，並解釋 snapshot 方案為什麼沒有「需要源碼」的問題。
- [ ] 我清楚知道 desock 找到的 crash 不是誤報的原因，以及唯一可能的例外案例是什麼。

---

## 延伸閱讀

1. **SNAPFUZZ: An Efficient Fuzzing Framework for Network Applications**（ISSTA 2022，Marius Muench 等）
   - 讀哪個部分：Section 2（Motivation）和 Section 3（Design）
   - 學到什麼：SNAPFUZZ 用 Linux userfaultfd 機制做輕量級 snapshot，讓 server 在握手後 snapshot、每次 exec 從快照還原，不需要修改 server 源碼。論文的 Fig 1 把本章的「執行模型光譜」用實測數字說明得非常清楚，是驗證本章理論預期的最佳材料。
   - 為什麼相關：這是目前把 network fuzzing 速度提升得最系統化的學術論文，是 Part 5 snapshot fuzzing 章節的理論基礎。

2. **preeny GitHub README**（https://github.com/zardus/preeny）
   - 讀哪個部分：`desock.c` 源碼（< 200 行）和 README 的 "Usage" 與 "Caveats" 小節
   - 學到什麼：`desock_dup.so` 和 `desock.so` 的差異（前者用 `dup2` 把 fd=0 複製到 accept 返回的 fd，後者直接返回 fd=0）；哪些 server 架構需要用哪一個。直接讀源碼比任何文件都清楚。
   - 為什麼相關：本章 "踩雷" 第四點的技術細節全部來自 `desock.c` 的實作。

3. **afl++ persistent\_mode.md**（https://github.com/AFLplusplus/AFLplusplus/blob/stable/instrumentation/README.persistent\_mode.md）
   - 讀哪個部分：整份文件（< 300 行），重點是 "Persistent mode" 和 "Deferred forkserver" 兩個小節
   - 學到什麼：`__AFL_LOOP`、`__AFL_INIT`、`__AFL_FUZZ_TESTCASE_BUF` 的完整語意；為什麼 "deferred forkserver" 要和 `__AFL_INIT()` 而不是 `__AFL_LOOP()` 搭配；以及 ASAN 和 persistent mode 同時啟用的注意事項。
   - 為什麼相關：本章 Section 2 的所有 afl++ macro 用法直接對應這份文件，是唯一的 authoritative reference。

---

Ch 18 和 Ch 19 一起解決了 network fuzzing 的兩個核心問題：StateAFL 讓 fuzzer 看得懂狀態，這章讓 fuzzer 跑得夠快。接下來 Ch 20 把這兩個工具拿到真實目標上實際操作。

→ [下一章](./20-protocol-fuzzing-in-practice.md)
