# Ch 17 — signal

> **目標**：深入理解 signal——它是什麼（process 間的非同步通知）、常見 signal（SIGTERM/SIGKILL/SIGINT/SIGHUP...）、kill 命令的真相、signal handler、以及為什麼 SIGKILL 殺不掉某些 process。這是 process 控制的核心機制。

> **環境**：Linux，kill/trap。承接 Ch 14（process 狀態，D 狀態殺不掉）、Ch 15（wait 取得 signal 退出狀態）。原理深挖章。

## 為什麼要懂 signal？

你會 `Ctrl-C` 中斷程式、`kill` 殺 process，但這些底層都是 **signal**——一種「送給 process 的非同步通知」。理解 signal，你能回答：為什麼 `kill` 不一定殺得掉（預設送 SIGTERM 可被忽略）？`kill -9` 和 `kill` 差在哪？為什麼某些程式收到 Ctrl-C 不退出？為什麼關閉終端機後背景程式也死了（SIGHUP）？

signal 是 Unix process 間通訊和控制的核心機制。SysOps 每天用它（重啟服務、終止程式），程式設計師用它處理優雅關閉。理解它，你能精確控制 process 的生死。

## 先建立直覺：signal 是「拍肩膀通知」

```
signal 的本質：一個 process 對另一個 process 的「拍肩膀」

  process A（如你的 shell）              process B（執行中的程式）
        │                                      │
        │  kill -TERM B（送 SIGTERM）           │
        │ ──────────────────────────────────→ │ 「拍肩膀」
        │                                      │ B 暫停手邊的事
        │                                      │ 執行 signal handler
        │                                      │ （或預設行為：終止）
        │
  → signal 是非同步的：B 不知道何時會被「拍」
    被拍時，B 中斷當前工作，處理 signal
        │
  signal 的「行為」（process 收到後做什麼）：
    - 預設行為（如 SIGTERM 預設終止、SIGSTOP 預設暫停）
    - 自訂 handler（程式註冊函式處理）
    - 忽略（某些 signal 可被忽略）
    - 無法改變（SIGKILL/SIGSTOP 不能被捕捉/忽略）
```

signal 像「拍肩膀的通知」——一個 process 送 signal 給另一個，收到的 process 中斷當前工作去處理（執行 handler 或預設行為）。它是非同步的（不知何時來）、有限的（只有編號，沒有額外資料）。理解這個模型，所有 signal 行為就清楚了。

## 常見 signal

```bash
# 看所有 signal
kill -l
# 1) SIGHUP   2) SIGINT   3) SIGQUIT  ...  9) SIGKILL  ... 15) SIGTERM ...
```

最重要的幾個：

```
SIGTERM（15）：「請你終止」（預設的 kill）
  禮貌的終止請求，process 能捕捉它做清理（優雅關閉）
  kill <pid> 預設送這個

SIGKILL（9）：「立刻死」（不能被捕捉/忽略）
  kernel 直接終止 process，不給它清理機會
  kill -9 <pid>；最後手段（但 D 狀態殺不掉，Ch 14）

SIGINT（2）：「中斷」（Ctrl-C）
  你按 Ctrl-C 送這個給前景程式
  程式能捕捉它（如問「確定要退出嗎」）

SIGHUP（1）：「終端機關閉了」（hangup）
  控制終端機關閉時，送給該終端機的 process
  傳統用途；現在也常用來「重新載入設定」（daemon 慣例）

SIGSTOP（19）/SIGTSTP（20）：「暫停」
  SIGSTOP 不能捕捉（強制暫停）；SIGTSTP 是 Ctrl-Z（可捕捉）
  → process 進 T 狀態（Ch 14）

SIGCONT（18）：「繼續」
  讓暫停的 process 恢復（fg/bg 用，Ch 18）

SIGSEGV（11）：「記憶體區段錯誤」
  程式存取非法記憶體 → kernel 送這個 → 預設終止 + core dump

SIGCHLD（17）：「child 狀態變了」
  child 結束/暫停時，kernel 送這個給 parent
  → parent 用它知道該 wait（Ch 15）
```

## kill：送 signal（不只是「殺」）

`kill` 的名字誤導——它是「送 signal」，不只是「殺」：

```bash
# kill 預設送 SIGTERM（15）
kill 1234              # 送 SIGTERM 給 PID 1234（請它終止）

# 指定 signal
kill -TERM 1234        # 明確送 SIGTERM
kill -9 1234           # 送 SIGKILL（強制殺）
kill -KILL 1234        # 同上（用名字）
kill -HUP 1234         # 送 SIGHUP（常用於 reload 設定）
kill -STOP 1234        # 暫停（Ch 14 的 T 狀態）
kill -CONT 1234        # 繼續

# kill 底層是 kill syscall
strace -e kill kill -TERM $$  2>&1 | grep kill
# kill(12345, SIGTERM) = 0   ← kill 命令呼叫 kill syscall

# 其他殺 process 的工具
killall nginx          # 按「名字」殺（殺所有叫 nginx 的）
pkill -f "python script"  # 按命令列模式殺
```

```
kill 不是「殺」，是「送 signal」：
  kill <pid>           送 SIGTERM（請終止，能被處理）
  kill -<signal> <pid> 送指定 signal
        │
  「kill」這名字的由來：早期最常用來終止 process
  但它能送任何 signal（STOP/CONT/HUP/USR1...）
        │
  → kill -STOP 暫停、kill -CONT 繼續、kill -HUP reload
    都用 kill 命令，但不是「殺」
```

> `kill` 是「送 signal」不是「殺」——這名字是歷史誤導（早期最常用來終止）。`kill <pid>` 送 SIGTERM（請終止，禮貌），`kill -9` 送 SIGKILL（強制殺，最後手段），`kill -STOP/-CONT` 暫停/繼續，`kill -HUP` 常用來叫 daemon 重新載入設定。記住層次：**先 `kill`（SIGTERM，給程式清理機會），不行再 `kill -9`（SIGKILL，強制）**。直接 `kill -9` 不給程式清理（可能留下半寫的檔案、未釋放的鎖）——除非 SIGTERM 沒用才上 -9。

## SIGTERM vs SIGKILL：為什麼先禮後兵

```
SIGTERM（15） vs SIGKILL（9）的關鍵差異：

  SIGTERM（kill 預設）：
    送給 process，process「能捕捉」它
    → 程式能執行清理 handler：
      - 關閉檔案、釋放鎖
      - 存檔、通知別人「我要關了」
      - 優雅地結束
    → 但程式也能「忽略」它（賴著不死）

  SIGKILL（kill -9）：
    kernel「直接」終止 process，不通知它
    → 程式「不能捕捉、不能忽略」（無法處理）
    → 立刻死，但沒清理機會（可能留下爛攤子）
        │
  → 先 kill（SIGTERM，給清理機會）
    程式不理 → kill -9（SIGKILL，強制）
  → 直接 -9 = 不給清理 = 可能資料損壞/鎖沒釋放
```

> **先 SIGTERM 後 SIGKILL 是負責任的做法**。SIGTERM 讓程式「優雅關閉」——存檔、關閉連線、釋放資源、清理。SIGKILL 直接終止，不給機會（可能留下半寫的檔案、沒釋放的鎖、不一致的狀態）。systemd 停服務就是這個策略：先 SIGTERM，等一段時間（TimeoutStopSec），不死才 SIGKILL。所以**別動不動就 kill -9**——先 kill（SIGTERM），給程式清理機會，真的賴著才 -9。資料庫、有狀態的服務尤其要 SIGTERM（-9 可能損壞資料）。SIGKILL 是最後手段，不是預設。

## SIGKILL 殺不掉的情況（Ch 14 的延伸）

```
SIGKILL（最強的 signal）也殺不掉的情況：

  1. D 狀態（uninterruptible sleep，Ch 14）：
     process 在等磁碟 I/O，signal 不能中斷
     → kill -9 也要等 I/O 完成才能處理
     → I/O 永遠不完成（磁碟壞/NFS 掛）= 永遠殺不掉

  2. zombie（Ch 14）：
     已經死了，殺它沒意義（要殺/修 parent）

  3. PID 1（init）：
     kernel 保護 PID 1，一般 signal 殺不掉（殺了系統就掛）
        │
  → 「kill -9 都殺不掉」先看狀態（ps -o stat）：
    D → I/O 問題（不是 process 賴著）
    Z → zombie（處理 parent）
```

## signal handler：程式怎麼處理 signal

程式能註冊 handler 自訂 signal 的處理（Part 8 的 trap 是 shell 版）：

```c
// signal handler（C，概念）
#include <signal.h>
#include <stdio.h>
#include <unistd.h>

void handle_term(int sig) {
    printf("Got SIGTERM, cleaning up...\n");
    // 清理：關檔案、釋放資源
    _exit(0);
}

int main() {
    signal(SIGTERM, handle_term);   // 註冊：收到 SIGTERM 時呼叫 handle_term
    // 或更現代的 sigaction()
    while (1) {
        sleep(1);   // 主迴圈
    }
}
```

```
signal handler 的用途：
  - 優雅關閉（收到 SIGTERM → 清理 → 退出）
  - 重新載入設定（收到 SIGHUP → 重讀設定檔，不重啟）
  - 忽略中斷（某些程式忽略 SIGINT，防誤按 Ctrl-C）
        │
  shell 的對應：trap（Part 8 Ch 35）
    trap 'cleanup' TERM INT   → shell 腳本捕捉 signal
        │
  → 寫服務/腳本要處理 SIGTERM（優雅關閉）
    這是生產級程式的標誌（vs 直接被 -9 殺）
```

> signal handler 讓程式「優雅地」回應 signal——收到 SIGTERM 做清理再退出、收到 SIGHUP 重讀設定（不重啟）。這是生產級服務的標誌：能被 SIGTERM 優雅關閉（存檔、釋放、通知），而非只能被 -9 強制殺（留爛攤子）。shell 腳本用 `trap`（Part 8 Ch 35）做同樣的事——`trap 'cleanup' TERM INT` 讓腳本捕捉 signal 做清理。`SIGHUP reload` 是 daemon 慣例（nginx/apache 收到 SIGHUP 重讀設定，不中斷服務）。寫腳本/服務時處理 SIGTERM 是負責任的做法。

## 故意弄壞：寫一個「殺不掉」的程式（用 trap）

```bash
# shell 版：捕捉 SIGTERM 和 SIGINT，「忽略」它們
cat > stubborn.sh <<'EOF'
#!/bin/bash
trap 'echo "Caught signal, ignoring!"' TERM INT
echo "PID: $$. Try to kill me with TERM or Ctrl-C"
while true; do sleep 1; done
EOF
chmod +x stubborn.sh
./stubborn.sh &
PID=$!

kill -TERM $PID            # 送 SIGTERM
# 輸出 "Caught signal, ignoring!"——程式賴著不死（捕捉了 SIGTERM）

kill -9 $PID              # SIGKILL：不能被捕捉，立刻死
# 程式死了（SIGKILL 無法被 trap 捕捉/忽略）
```

這展示 SIGTERM 可被捕捉/忽略（程式賴著），但 SIGKILL（-9）不能——它是 kernel 直接終止，程式無法處理。這就是為什麼「kill 沒用，要 kill -9」（程式捕捉了 SIGTERM）。也展示為什麼 SIGKILL 存在——當程式忽略 SIGTERM 時的最後手段。

## 踩雷集錦

1. **動不動就 kill -9**：SIGKILL 不給程式清理（可能損壞資料、沒釋放鎖）。先 kill（SIGTERM），不行才 -9。資料庫/有狀態服務尤其要 SIGTERM

2. **以為 kill 一定殺得掉**：kill（SIGTERM）能被程式捕捉/忽略（程式賴著）。要強制用 -9。但 D 狀態連 -9 也殺不掉（Ch 14）

3. **kill -9 殺不掉就以為 process 無敵**：先看狀態（ps -o stat）。D 狀態（等 I/O）連 -9 都殺不掉——問題在 I/O，不是 process

4. **混淆 SIGTERM 和 SIGINT**：SIGTERM（15，kill 預設）vs SIGINT（2，Ctrl-C）。程式可能對兩者反應不同（有些忽略 INT 但聽 TERM）

5. **SIGKILL/SIGSTOP 不能被捕捉**：這兩個 signal 無法被 handler/trap 捕捉或忽略（kernel 保證能控制 process）。別試圖 trap 它們

## 進階：signal 的限制與現代替代

傳統 signal 有些設計限制：

```
signal 的限制：
  - 只有編號，沒有額外資料（不能傳訊息，只能傳「哪個 signal」）
  - 非同步、可能丟失（同個 signal 連送多次可能只收到一次）
  - handler 裡能做的事受限（async-signal-safe 函式）
  - 標準 signal 數量有限
        │
  現代補充：
  - real-time signals（SIGRTMIN~SIGRTMAX）：可排隊、能帶資料
  - signalfd：把 signal 變成可 read 的 fd（整合進 event loop）
  - eventfd/pipe self-pipe trick：用 fd 處理 signal（避免 handler 限制）
        │
  → 簡單控制（終止/暫停）用傳統 signal
    複雜 IPC 用其他機制（pipe/socket/shared memory）
```

> 傳統 signal 有限制——只能傳「哪個 signal」（沒有額外資料）、可能丟失（同 signal 連送多次可能合併）、handler 裡只能用 async-signal-safe 函式（很多函式不能在 handler 裡安全呼叫）。現代補充：**real-time signals**（可排隊、能帶資料）、**signalfd**（把 signal 變成 fd，整合進 epoll event loop，避免 handler 的限制）。但簡單的 process 控制（終止/暫停/reload）傳統 signal 就夠。複雜的行程間通訊用 pipe（Ch 20）、socket、shared memory。理解 signal 的限制，你會懂為什麼它適合「通知/控制」但不適合「傳資料」。

## 動手練習

1. 練 kill：`sleep 100 &`，`kill <pid>`（SIGTERM，死）。再寫一個 trap SIGTERM 的腳本（stubborn.sh），`kill` 殺不掉（被捕捉），`kill -9` 才死

2. 看 signal 底層：`strace -e kill kill -TERM $$`（kill syscall）。`kill -l` 看所有 signal

3. 玩 STOP/CONT：`sleep 100 &`，`kill -STOP`（進 T 狀態，Ch 14），`ps -o stat` 確認 T，`kill -CONT` 恢復

4. 跑「故意弄壞」：寫 trap 忽略 SIGTERM/SIGINT 的腳本，確認 kill 和 Ctrl-C 殺不掉，kill -9 才死。理解 SIGKILL 不能被捕捉

## 本章重點整理

- signal 是 process 間的非同步「拍肩膀通知」；只有編號（哪個 signal），process 收到後執行 handler 或預設行為
- 常見 signal：SIGTERM（15，請終止，可捕捉）、SIGKILL（9，強制殺，不可捕捉）、SIGINT（Ctrl-C）、SIGHUP（reload）、SIGSTOP/CONT（暫停/繼續）
- kill 是「送 signal」不是「殺」；先 kill（SIGTERM 給清理機會）再 kill -9（SIGKILL 強制）
- SIGKILL 殺不掉的情況：D 狀態（等 I/O，Ch 14）、zombie（已死）、PID 1（受保護）
- signal handler（程式）/ trap（shell，Part 8）讓程式優雅回應 signal；SIGKILL/SIGSTOP 不能被捕捉

## 自我檢核

- [ ] 能解釋 signal 是什麼（非同步通知）和它的限制（只有編號，沒資料）
- [ ] 知道 SIGTERM 和 SIGKILL 的差別，以及為什麼先禮後兵
- [ ] 知道 kill 是「送 signal」，能送 TERM/KILL/STOP/CONT/HUP
- [ ] 能解釋為什麼 kill -9 殺不掉 D 狀態的 process
- [ ] 知道 signal handler/trap 的用途（優雅關閉、reload），以及哪些 signal 不能被捕捉

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 20-22 (Signals)** — Michael Kerrisk
  - **讀哪幾章**：Ch 20（signal 基礎）、Ch 21（handler）、Ch 22（進階）
  - **這本書的定位**：signal 的權威來源（三章完整講透）
  - **前提**：本章

### 部落格 / 文章

- **[A deep dive into Linux signals](https://jvns.ca/blog/2016/06/13/should-you-be-scared-of-signals/)** — Julia Evans
  - **這篇說什麼**：signal 怎麼運作、為什麼有時可怕、handler 的陷阱
  - **讀哪裡**：整篇
  - **為什麼值得讀**：把 signal 的非同步本質和陷阱講清楚

### 官方文件

- **[signal(7) man page](https://man7.org/linux/man-pages/man7/signal.7.html)**
  - **讀哪裡**：standard signals 那節（所有 signal 的預設行為）、async-signal-safe 那節
  - **學什麼**：每個 signal 的編號、預設行為、能否捕捉的權威表
  - **前提**：本章

→ [Ch 18 job control 與 nohup/disown](./18-job-control.md)
