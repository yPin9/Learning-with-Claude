# 練習 A — 用 strace 抓真實 bug

> 目標：用 Ch 5 學的 strace 技術，在 4 個刻意設計的有 bug 程式上找出問題、解釋給自己聽、修好。

## 任務規格

| # | bug 程式 | 症狀 | 你要做的 |
|---|---|---|---|
| 1 | `bug1-config.c` | 啟動立刻 exit | 用 strace 找出哪個檔案載入失敗 |
| 2 | `bug2-stuck.c` | 卡住不動 | 用 strace -p 找出卡哪個 syscall 跟 fd |
| 3 | `bug3-slow.c` | 變很慢 | 用 strace -c 找出哪個 syscall 暴走 |
| 4 | `bug4-fork.c` | child 不見 | 用 strace -ff 找出 child 死在哪 |

每題的 source 在下面，自己編譯、自己診斷、自己修。

## bug 1：載 config 失敗

```c
// bug1-config.c — 模擬「啟動需要載 config」的 daemon
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    FILE *fp = fopen("config.ini", "r");
    if (!fp) {
        fprintf(stderr, "Failed to load config\n");
        return 1;
    }
    char buf[256];
    while (fgets(buf, sizeof(buf), fp)) {
        // 假裝 parse
    }
    fclose(fp);
    printf("Server starting...\n");
    while (1) sleep(1);
    return 0;
}
```

`gcc bug1-config.c -o bug1`

跑：

```bash
./bug1
# Failed to load config
```

**任務**：用 strace 找出實際在哪個路徑找 config。修正方法可以是：把 config 放對位置、改 cwd、給絕對路徑。

## bug 2：卡住

```c
// bug2-stuck.c — 模擬 server 等不到 client
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>

int main(void) {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    int one = 1;
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(9999);
    addr.sin_addr.s_addr = INADDR_ANY;

    bind(s, (struct sockaddr*)&addr, sizeof(addr));
    listen(s, 5);
    printf("Listening on 9999... (pid=%d)\n", getpid());

    int c = accept(s, NULL, NULL);
    char buf[256];
    int n = read(c, buf, sizeof(buf));
    printf("Got %d bytes: %.*s\n", n, n, buf);
    close(c);
    close(s);
    return 0;
}
```

`gcc bug2-stuck.c -o bug2`

```bash
./bug2 &
# Listening on 9999... (pid=1234)
# 然後就沒反應
```

**任務**：用 `strace -p PID -y` 看它停在哪個 syscall、那個 fd 是什麼。然後思考為什麼卡。最後用 `nc localhost 9999` 證明你的診斷對。

## bug 3：變慢

```c
// bug3-slow.c — 模擬有人不小心一個 byte 一個 byte 寫
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>

int main(void) {
    int fd = open("/tmp/slow.log", O_CREAT | O_WRONLY | O_TRUNC, 0644);
    if (fd < 0) { perror("open"); return 1; }

    const char *msg = "this is a fairly long message that should be written quickly\n";
    for (int i = 0; i < 1000; i++) {
        const char *p = msg;
        while (*p) {
            write(fd, p, 1);   // 每次 1 byte!
            p++;
        }
    }
    close(fd);
    return 0;
}
```

`gcc bug3-slow.c -o bug3`

```bash
time ./bug3
# real    0m0.X 秒（看你機器，但比預期慢）
```

**任務**：用 `strace -c ./bug3` 看 syscall 分布。算一下總共幾次 write。重寫成 buffer write 看快多少。

## bug 4：child 不見

```c
// bug4-fork.c — child 在某些路徑下會不正常
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/wait.h>

int main(void) {
    int fd = open("/tmp/test_dir/child.log", O_CREAT | O_WRONLY, 0644);

    pid_t pid = fork();
    if (pid == 0) {
        // child
        if (fd < 0) {
            // child 沒 check fd，繼續寫
            write(fd, "child wrote\n", 12);
            exit(0);
        }
        write(fd, "child wrote\n", 12);
        close(fd);
        exit(0);
    }
    wait(NULL);
    if (fd >= 0) close(fd);
    printf("Done\n");
    return 0;
}
```

`gcc bug4-fork.c -o bug4`

```bash
./bug4
# Done    （但 /tmp/test_dir/child.log 不存在因為 /tmp/test_dir 不存在）
ls /tmp/test_dir/  # 沒這目錄
```

**任務**：用 `strace -ff -o trace ./bug4` 看 parent 跟 child 的 trace。`ls trace.*` 應該看到兩個檔。觀察 child 的 write 回傳值。**重點：child 的 write fail 但程式不知道**。修正方法：check `fd < 0` 就 exit，或先 mkdir。

## 期望輸出範例

### bug 1 偵測

```bash
strace -e openat,access ./bug1 2>&1 | tail
# openat(AT_FDCWD, "config.ini", O_RDONLY) = -1 ENOENT (No such file or directory)
# Failed to load config
```

清楚看到它在 cwd 找 `config.ini`。

### bug 2 偵測

```bash
sudo strace -p PID -y -tt
# 12:34:56.123456 accept(3<TCP:[0.0.0.0:9999]>, ...) = ?    （阻塞中）
```

`accept(3<TCP:[0.0.0.0:9999]>)` 一目了然 — 卡在等連線。

### bug 3 偵測

```bash
strace -c ./bug3
# % time     seconds  usecs/call     calls    errors syscall
# ------ ----------- ----------- --------- --------- ----------------
#  98.50    0.450000           7     61000           write
#   1.20    0.005000          50       100           openat
# ...
```

看到 61000 次 write 馬上知道有問題。改用 fwrite 或 buffered write。

### bug 4 偵測

```bash
strace -ff -o trace ./bug4
ls trace.*
# trace.5678  trace.5679

cat trace.5679    # child
# ...
# write(3, "child wrote\n", 12) = -1 EBADF (Bad file descriptor)
# exit_group(0) = ?
```

child 的 write 是 EBADF — 因為 open 失敗 fd = -1。

## 完整參考解答

**先做完上面再看！**

<details>
<summary>每題解答 + 修正</summary>

### bug 1

診斷：`config.ini` 不存在於 cwd。

修：

```c
// 改成絕對路徑或檢查多個位置
const char *paths[] = {"./config.ini", "/etc/myapp.conf", NULL};
FILE *fp = NULL;
for (int i = 0; paths[i]; i++) {
    fp = fopen(paths[i], "r");
    if (fp) break;
}
if (!fp) { fprintf(stderr, "Failed to load config from any location\n"); return 1; }
```

### bug 2

診斷：accept 阻塞等連線、沒人來連。**程式行為正確，只是設計時要嘛 client 沒跑、要嘛是測試忘了 connect**。

「修」（其實是測試）：

```bash
nc localhost 9999 <<< "hello"
# bug2 收到後印 "Got 6 bytes: hello"
```

如果情境是「server 預期 systemd socket activation」，那要改用 sd_listen_fds(3)。

### bug 3

診斷：61000 次 write，每次 1 byte。

修：

```c
// 用 buffer，或用 stdio
FILE *fp = fopen("/tmp/slow.log", "w");
for (int i = 0; i < 1000; i++) {
    fputs(msg, fp);   // stdio 自動 buffer
}
fclose(fp);
```

或：

```c
// 自己 buffer
char bigbuf[4096];
size_t off = 0;
for (int i = 0; i < 1000; i++) {
    size_t len = strlen(msg);
    if (off + len > sizeof(bigbuf)) { write(fd, bigbuf, off); off = 0; }
    memcpy(bigbuf + off, msg, len);
    off += len;
}
if (off) write(fd, bigbuf, off);
```

`time` 比較：原版可能 0.05s，buffered 0.001s，**50x 快**。

### bug 4

診斷：`/tmp/test_dir/` 不存在，open 回 -1。fork 後 fd = -1 被繼承，child 也是 -1，write 失敗但沒 check。

修：

```c
int fd = open("/tmp/test_dir/child.log", O_CREAT | O_WRONLY, 0644);
if (fd < 0) {
    perror("open");
    return 1;
}
```

或：

```c
mkdir("/tmp/test_dir", 0755);    // ignore EEXIST
int fd = open("/tmp/test_dir/child.log", O_CREAT | O_WRONLY, 0644);
if (fd < 0) { perror("open"); return 1; }
```

</details>

## 常見錯誤

| 症狀 | 原因 |
|---|---|
| `strace -p PID` 失敗 "Operation not permitted" | `ptrace_scope` 設了 1，`sudo` 或設 0 |
| `strace -ff -o trace` 看不到 child | 沒加 `-f` 或 child 直接 exec 走另一支 |
| `-c` 印的時間都 0 | 程式跑太快，跑久一點 |
| `-y` 沒效果 | 舊版 strace 沒這 flag，升級 |
| 程式加 strace 後不重現問題 | 真的 race condition，strace 改了 timing — 換 ASan / TSan |

## 進階挑戰

**A. 寫一支「自動診斷」script**：給 PID，跑 `strace -p PID -c -f` 收集 5 秒 summary，自動分析「futex 太多 = lock 競爭」「read/write 太多 = IO bound」「epoll_wait 太多 = 事件少」等。

**B. 把 bug2 改成多 client**：accept 後 fork 處理，用 `strace -ff` 看 fork 出來的 worker 怎麼跑。

**C. 拿一個你自己寫過的程式 strace 一次**：印出來的 syscall 全部解釋清楚。意外地會看到很多自己沒預期的東西（multiple stat、額外 read 等）。

## 自我檢核

- [ ] 4 題 bug 都自己診斷出來
- [ ] `strace -e ... -y -tt -p PID` 用得順
- [ ] `-c` summary 看得懂、能挑出熱點
- [ ] 知道 child 的 fd 繼承會帶過去 bad fd
- [ ] 看到 `accept(...)` 阻塞知道下一步該配 ss / lsof 看 fd 對端

下個 Part 進 process / file / network 觀察工具。strace 看不到的東西這群看得到。

→ [Ch 7 /proc 完整漫遊](./07-proc-filesystem-tour.md)
