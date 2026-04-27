# 練習 B — fd 劫持事件調查

> 目標：給你一個壞掉的 daemon source code 跟症狀，用 lsof / ss / strace / /proc 一路追，找出「為什麼 daemon 寫到不該寫的地方」。

## 場景

你接手了同事寫的 `worker daemon`。它號稱：

- 接收 stdin 上的指令
- 處理完寫到 `/var/log/worker.log`
- 每 5 秒 ping 一個內部 metric server

實際看起來：

- 程式跑了
- `/var/log/worker.log` 是空的
- 同事說 production 環境的 `/var/log/worker.log` 有時候**會變成根本不該被寫到的奇怪內容**

你要找出 root cause 並修。

## source code

```c
// broken-worker.c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

int main(void) {
    // open log file
    int log = open("/var/log/worker.log", O_CREAT | O_WRONLY | O_APPEND, 0644);
    if (log < 0) {
        perror("open log");
        return 1;
    }

    // open metric socket
    int metric = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(9090);
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
    if (connect(metric, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        // metric server 可能沒起來，silently 忽略
        close(metric);
        metric = -1;
    }

    // 把 stderr 改寫到 log
    dup2(log, 2);

    // 主迴圈：讀 stdin、處理、寫 log
    char buf[256];
    int counter = 0;
    while (fgets(buf, sizeof(buf), stdin)) {
        // 處理（簡化）
        size_t len = strlen(buf);
        if (len > 0 && buf[len-1] == '\n') buf[--len] = '\0';

        // 寫 log
        dprintf(log, "[%d] processed: %s\n", counter++, buf);

        // 每 5 個指令 ping metric
        if (counter % 5 == 0 && metric > 0) {
            const char *ping = "PING\n";
            send(metric, ping, strlen(ping), 0);
        }
    }

    close(log);
    if (metric > 0) close(metric);
    return 0;
}
```

```bash
gcc broken-worker.c -o broken-worker
sudo touch /var/log/worker.log
sudo chmod 666 /var/log/worker.log
```

跑：

```bash
./broken-worker
hello
world
quit
^D
```

## 你看到的症狀

**症狀 1**：`/var/log/worker.log` 是空的，明明印了好幾個 "processed"。

**症狀 2**（有時）：log 裡出現 `PING\n PING\n` 的內容，**不是處理紀錄**。

## 調查任務

完成下列偵查：

| # | 任務 | 工具 |
|---|---|---|
| 1 | 確認 daemon 真的有寫東西 | `strace` |
| 2 | 看 daemon 開了哪些 fd | `lsof -p` 或 `/proc/PID/fd/` |
| 3 | 看每個 fd 對應到什麼 | `lsof` 跟 `/proc/PID/fd` |
| 4 | 確認 metric server 連得上 vs 連不上的差別 | 一次有 listen 一次沒 |
| 5 | 解釋 root cause | （邏輯思考） |
| 6 | 修 | 改 source |

## 期望輸出範例（過程）

```
$ ./broken-worker &
PID=$!
echo "test1" | nc localhost 9090   # 假裝 metric server 沒起
echo "test1"

# strace observation
$ sudo strace -p $PID -e trace=openat,socket,connect,dprintf,write,send -y
openat(AT_FDCWD, "/var/log/worker.log", O_WRONLY|O_APPEND|O_CREAT, 0644) = 3</var/log/worker.log>
socket(AF_INET, SOCK_STREAM, 0) = 4<TCP:[...]>
connect(4<TCP:[...]>, ..., 16) = -1 ECONNREFUSED (Connection refused)
close(4<TCP:[...]>)
dup2(3</var/log/worker.log>, 2) = 2</var/log/worker.log>
write(3</var/log/worker.log>, "[0] processed: test1\n", 21) = 21

# fd inspection
$ ls -l /proc/$PID/fd/
0 -> /dev/pts/0
1 -> /dev/pts/0
2 -> /var/log/worker.log     ← stderr 已被 redirect
3 -> /var/log/worker.log     ← log fd
（沒有 metric fd 因為 connect 失敗 close 了）
```

## 完整參考解答

**先做完上面再看！**

<details>
<summary>診斷 + root cause + 修</summary>

### 症狀 1：log 看起來空

實驗：

```bash
$ ./broken-worker
hello
^D
$ cat /var/log/worker.log
```

看起來空。但 strace 明明印了 write 21 byte 成功。

**原因**：`O_APPEND` flag。每次 write 都 append，不是 truncate。所以「空」是 cat 顯示時 buffer 還沒 flush，或你看的時間點不對。再 cat 一次：

```bash
$ cat /var/log/worker.log
[0] processed: hello
```

實際有寫，只是看的時機問題。**這不是 bug**，但容易誤判。

### 症狀 2：log 出現 PING

這個是真 bug。

實驗：先讓 metric server listen：

```bash
$ nc -l 9090 &           # listen 9090
$ ./broken-worker &
$ for i in 1 2 3 4 5; do echo "msg$i"; done | ./broken-worker
```

斷開 nc 後再次跑會發生不同行為。

**root cause**：

仔細看 source：

```c
int metric = socket(...);     // 拿到 fd，假設 = 4
if (connect(metric, ...) < 0) {
    close(metric);            // close fd 4
    metric = -1;
}

dup2(log, 2);                  // 把 stderr (fd 2) 改成 log

while (...) {
    dprintf(log, ...);
    if (... && metric > 0) {
        send(metric, ping, ...);
    }
}
```

問題出在「fd recycling」：

1. metric socket 拿到 fd 4
2. connect fail，close(4)
3. **後續任何 open / socket 拿到的 fd 4 就是「另一個東西」**
4. metric 還等於 4（沒設 -1，等等！其實 source 設了 `metric = -1`，所以這條路 OK）

但等一下，看 source：connect fail 時設 `metric = -1`，所以 send 那行 `metric > 0` 是 false，不會 send。**這條路其實沒 bug**。

但如果 connect 成功呢？fd 4 是 socket，dprintf 寫 log 用 fd 3 (log)，send 寫 metric 用 fd 4 (socket)。兩條 fd 不重疊，理論上 OK。

**真 bug 在更微妙的地方**：

```c
int log = open("/var/log/worker.log", ...);   // fd 3
int metric = socket(...);                      // fd 4
// ... (connect 成功)
dup2(log, 2);                                  // fd 2 = log
// ...
```

這裡沒問題。但如果 stdin 因為某種原因被 close（pipe 斷、stdin 關），main loop 退出，**程式 exit 時的順序**：

```c
close(log);
if (metric > 0) close(metric);
```

也沒問題。

那 PING 怎麼會出現在 log？

**真兇**：這是個多 worker 場景。如果 daemon **被 fork** 而原 parent 沒 close metric fd 就 exec 別的東西...

實驗：

```bash
$ ./broken-worker &
PID=$!

# 模擬：另一個 process 偷偷 fork 並繼承 fd
cat /proc/$PID/fd/   # 看 fd table
```

但 source 裡沒有 fork，所以這條路也不通。

**真正的 root cause（我藏的這個）**：

注意 `metric = socket(AF_INET, SOCK_STREAM, 0)`。Socket 拿到 fd 通常是低 number（3 是 log）。在某些 race 下：

```
open log     → fd 3
socket       → fd 4
connect fail → close(4)
↓
某個 inherited fd 或 lib 開了某東西占 fd 4
↓
send(4, "PING") → 寫到那個東西，可能是某個被 inherited 的檔案
```

**更實際的 bug**：dup2 用法錯。

`dup2(log, 2)` 把 stderr 接到 log，意圖是「把錯誤訊息也寫進 log」。但 `dprintf(log, ...)` 用 fd 3（log），如果之後有人 close(3) 而 fd 3 被 reuse 成 socket：

```
open log        → fd 3
socket          → fd 4
... main loop
某 lib close(3)  → fd 3 釋出
某 lib socket   → 拿到 fd 3
dprintf(log=3)  → 寫到 socket！
```

source 裡沒這 race，但**實務上 lib（getaddrinfo 等）會偷偷 open / close fd**。

### 「正解」：set fd to -1 after close + 用 FILE* 不是 raw fd

修：

```c
// 用 FILE* 比較安全
FILE *log = fopen("/var/log/worker.log", "a");
setvbuf(log, NULL, _IOLBF, 0);   // line buffering

int metric = -1;
int s = socket(...);
if (connect(s, ...) >= 0) {
    metric = s;
} else {
    close(s);
}

// stderr 還是要 redirect
int log_fd = fileno(log);
dup2(log_fd, 2);

while (fgets(buf, sizeof(buf), stdin)) {
    fprintf(log, "[%d] processed: %s\n", counter++, buf);
    fflush(log);
    if (... && metric >= 0) {
        send(metric, ping, ...);
    }
}

if (metric >= 0) close(metric);
fclose(log);
```

關鍵改進：
- 用 FILE*：fopen / fprintf 內部會處理 fd 重映射，比直接 fd safer
- `metric >= 0` 而不是 `> 0`（fd 0 是合法的）
- 永遠對 close 過的 fd 設 -1

### 真正最容易踩的 bug

source 還有一個我沒提的潛在問題：**metric > 0** vs **metric >= 0**。fd 0 是 stdin，是合法的 fd。如果某個 race 讓 socket 拿到 fd 0，`metric > 0` 是 false，整個 metric 邏輯被 disable。

雖然實際上 socket() 不會回 0（除非 stdin 已 close），但**永遠用 `>= 0`** 是好習慣。

</details>

## 教學要點（解釋給你自己聽）

完成後你應該能回答：

1. **fd 是 process 私有的整數，但 kernel 內部 open file description 共享** — fork 後 parent / child 的 fd table 拷貝，但指向同 open file
2. **fd 數字會 reuse**：close 後的 fd 號可能立刻被下個 open 拿走
3. **dprintf / write 用 raw fd 危險**：fd 號變了你不知道。fopen / FILE* 比較安全
4. **strace -y 是 fd debug 神器**：直接顯示 fd 對應 path / socket
5. **lsof + /proc/PID/fd 互相印證**

## 進階挑戰

**A. 寫個 fd-track LD_PRELOAD**：攔 open / socket / close，記錄 fd 變化跟 stack。Ch 20 工具的雛形。

**B. 故意讓 worker 在 fd 0/1/2 被 close 後 socket**：用 `closefrom(0)` + `bash <&-`，看 socket 真的拿到 fd 0/1。

**C. 拿你公司 / 開源 server 的 source 找類似 bug**：「metric > 0」「fd > 0」grep 一遍。

## 自我檢核

- [ ] 用 strace 看到 daemon 的 fd 操作
- [ ] 用 lsof / /proc/PID/fd 認出 fd 對應物件
- [ ] 知道 fd 數字會 reuse、`> 0` vs `>= 0` 差別
- [ ] 知道 dup2 的副作用
- [ ] 知道為什麼 raw fd debug 比 FILE* 危險

下個 Part 看靜態檢視 — binary 內部結構，跟 dynamic 觀察互補。

→ [Ch 11 ELF 靜態檢視](./11-elf-static-inspection.md)
