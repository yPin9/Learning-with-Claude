# Final Project — 偵探破案：故意 broken 的 daemon

> 目標：拿到一支「看似正常但藏 5 種 bug」的 daemon，用整套工具像偵探一樣還原所有犯罪事實，寫一份 incident report，最後修好。完成後你完全內化整套課的工具搭配。

## 場景

你接手了同事辭職前留下的 `metric-collector` 程式 —— 號稱「定期收集系統 metric、寫 log、推到 collector server」。實際運作：

- 跑久了 RAM 越來越大（leak？）
- 偶爾 segfault
- log 檔有時內容亂掉（race？UAF？）
- collector server 接到的 metric **數字不太對**
- 偶爾整個 daemon hang 住，不再寫 log 但 process 還在

5 個症狀，至少 5 個 bug。你的任務：**把每個 bug 抓出來、解釋清楚、修好**。

## source code

```c
// metric-collector.c
// gcc -O0 -g -pthread metric-collector.c -o collector
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <pthread.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <signal.h>

#define MAX_METRICS 1024
#define COLLECTOR_HOST "127.0.0.1"
#define COLLECTOR_PORT 9100

typedef struct metric {
    char *name;
    double value;
    long timestamp;
} metric_t;

static metric_t *metrics[MAX_METRICS];
static int n_metrics = 0;
static int log_fd = -1;
static int sock_fd = -1;
static volatile int stop = 0;

static void log_msg(const char *prefix, const char *msg) {
    char buf[512];
    int n = snprintf(buf, sizeof(buf), "[%s] %s\n", prefix, msg);
    write(log_fd, buf, n);
}

static metric_t *make_metric(const char *name, double value) {
    metric_t *m = malloc(sizeof(metric_t));
    m->name = strdup(name);
    m->value = value;
    m->timestamp = time(NULL);
    return m;
}

static void *collector_thread(void *_) {
    while (!stop) {
        // 收集：模擬讀 /proc 拿一個值
        FILE *fp = fopen("/proc/loadavg", "r");
        double load;
        fscanf(fp, "%lf", &load);
        fclose(fp);

        metric_t *m = make_metric("load.1min", load);
        if (n_metrics < MAX_METRICS) {
            metrics[n_metrics++] = m;
        }

        char buf[128];
        snprintf(buf, sizeof(buf), "collected load=%.2f n=%d", load, n_metrics);
        log_msg("collect", buf);
        sleep(1);
    }
    return NULL;
}

static void *sender_thread(void *_) {
    while (!stop) {
        // 每 5 秒推一次
        sleep(5);

        if (n_metrics == 0) continue;

        for (int i = 0; i < n_metrics; i++) {
            char buf[256];
            int n = snprintf(buf, sizeof(buf), "%s=%.2f t=%ld\n",
                             metrics[i]->name,
                             metrics[i]->value,
                             metrics[i]->timestamp);
            send(sock_fd, buf, n, 0);

            char log[300];
            snprintf(log, sizeof(log), "sent %s", metrics[i]->name);
            log_msg("send", log);
        }

        // 清空
        for (int i = 0; i < n_metrics; i++) {
            free(metrics[i]);
        }
        n_metrics = 0;
    }
    return NULL;
}

int main(int argc, char *argv[]) {
    log_fd = open("/tmp/collector.log",
                  O_CREAT | O_WRONLY | O_APPEND, 0644);

    sock_fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(COLLECTOR_PORT);
    inet_pton(AF_INET, COLLECTOR_HOST, &addr.sin_addr);
    if (connect(sock_fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        log_msg("init", "connect failed, will retry");
    }

    pthread_t t1, t2;
    pthread_create(&t1, NULL, collector_thread, NULL);
    pthread_create(&t2, NULL, sender_thread, NULL);

    pthread_join(t1, NULL);
    pthread_join(t2, NULL);

    close(log_fd);
    close(sock_fd);
    return 0;
}
```

```bash
gcc -O0 -g -pthread metric-collector.c -o collector

# 模擬 collector server
nc -lk 9100 > /tmp/received.txt &

# 跑
./collector &
sleep 30
kill %2
```

幾分鐘後跡象：

- `/tmp/collector.log` 有時 line 混亂
- `/tmp/received.txt` 有重複或缺失 metric
- top 看 RSS 慢慢長
- 偶爾 SEGV

## 偵查任務

| # | 用什麼工具 | 找什麼 |
|---|---|---|
| 1 | `valgrind --tool=helgrind` 或 TSan | race condition |
| 2 | `valgrind --leak-check=full` 或 ASan | memory leak |
| 3 | ASan | UAF / OOB |
| 4 | `strace -f -y` | I/O / fd 問題 |
| 5 | `coredumpctl gdb` | 偶發 segfault 現場 |
| 6 | `lsof` / `ss` | 連線狀態 |
| 7 | `bpftrace` | 確認某個 syscall 的頻率 |
| 8 | reading source | 邏輯 bug |

每題自己跑、寫下 root cause、寫 fix。最後合成完整修正版。

## 期望的 incident report 格式

```markdown
# Incident Report: metric-collector

## Bugs found

### Bug 1: race on n_metrics / metrics[]
- Tool: TSan
- Evidence: ...
- Root cause: collector_thread / sender_thread 同時讀寫 n_metrics 沒 lock
- Fix: 加 mutex

### Bug 2: ...

## Statistics
- 5 bugs found
- 4 hours debugging
- ...
```

## 完整參考解答

**先做完上面再看！**

<details>
<summary>5 個 bug + root cause + 修</summary>

### Bug 1: race on `metrics[]` / `n_metrics`

兩個 thread 同時讀寫，沒 lock。

TSan 訊息：

```
WARNING: ThreadSanitizer: data race ...
  Read of size 4 at 0x... by thread T2:
    #0 sender_thread metric-collector.c:78
  Previous write of size 4 at 0x... by thread T1:
    #0 collector_thread metric-collector.c:55
```

修：加 mutex 或用 thread-safe queue。

### Bug 2: UAF — sender free 後 collector 還在用

sender_thread free metrics 然後 set `n_metrics = 0`。但**collector 在 sender 跑這段時可能正在 access `metrics[i]`**（race + UAF）。ASan 抓得到。

```
==1234==ERROR: AddressSanitizer: heap-use-after-free
```

修：collect / send 用獨立 buffer + swap。

### Bug 3: fd race / log 亂

`log_msg` 用全域 `log_fd`，兩個 thread 同時 write 大訊息可能 interleave。`write` 對小 buffer 是 atomic（< PIPE_BUF），對大 message 不是。

修：log_msg 內加 mutex；或用 `O_APPEND` + 確保每 write 是單一 atomic（< PIPE_BUF = 4096 byte）。

實際上 source 裡 buf 只 512 byte，每 write 在 4096 內應該 atomic。但兩個 thread 各自 build buf 後同時 write，**順序不確定**，造成 log 看起來亂（不同 thread 的訊息穿插）。**這不是 bug 是 feature**，但易誤判。

如果 collector / sender 同時 fprintf 到 stdio，那 stdio buffer 會有真的 race。

### Bug 4: segfault on `fopen` failure

```c
FILE *fp = fopen("/proc/loadavg", "r");
double load;
fscanf(fp, "%lf", &load);    // ← 如果 fp 是 NULL，segfault
```

`/proc/loadavg` 通常存在，但若有 race（mount / namespace），fopen 可能 fail。

修：

```c
if (!fp) { log_msg("err", "fopen failed"); continue; }
```

### Bug 5: leak when connect fails

```c
if (connect(sock_fd, ...) < 0) {
    log_msg("init", "connect failed, will retry");
}
```

`will retry` 但其實沒 retry 邏輯。`sock_fd` 還是無效，後續 send 都失敗（**沒檢查 send 回傳**）。

加上 `n_metrics` 一直長到 1024 後，**之後所有新 metric 被丟棄但已分配的 metric_t 不會 free**（除非 send 跑到、但 send fail metric 還是 free —— 等等）：

仔細看 sender，**每次 send 後都 free**，不管 send 成不成功。所以這條 path 沒 leak。

但 connect fail 時 sock_fd 是無效的，send 會 EBADF / EPIPE，**但 log 一次就 50K 失敗**。

修：connect fail 立刻 exit；或實作 retry。

### Bug 6: `n_metrics` 滿時 leak

```c
metric_t *m = make_metric("load.1min", load);
if (n_metrics < MAX_METRICS) {
    metrics[n_metrics++] = m;
}
// 如果 n_metrics == MAX_METRICS，m 沒進 array，沒 free → leak
```

ASan / valgrind 抓得到。每 sender_thread loop 一次清，但 collector 比 sender 快 5x，幾秒就 leak。

修：

```c
if (n_metrics < MAX_METRICS) {
    metrics[n_metrics++] = m;
} else {
    free(m->name);
    free(m);
}
```

### Bug 7: 真正讓 collector 數字錯的 race

sender 在 free 跟 reset 之間，collector 可能 push 新 metric 到 `metrics[1024]` —— OOB write。ASan 抓 stack-buffer-overflow。

實際是 global array，所以 ASan 報 global-buffer-overflow。

修：用 mutex 圍 free / reset / push 整段。

### 完整修正版（簡略）

```c
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;

static void *collector_thread(void *_) {
    while (!stop) {
        FILE *fp = fopen("/proc/loadavg", "r");
        if (!fp) { sleep(1); continue; }
        double load;
        if (fscanf(fp, "%lf", &load) != 1) { fclose(fp); sleep(1); continue; }
        fclose(fp);

        metric_t *m = make_metric("load.1min", load);

        pthread_mutex_lock(&lock);
        if (n_metrics < MAX_METRICS) {
            metrics[n_metrics++] = m;
        } else {
            free(m->name); free(m);
        }
        pthread_mutex_unlock(&lock);

        sleep(1);
    }
    return NULL;
}

static void *sender_thread(void *_) {
    while (!stop) {
        sleep(5);

        // Snapshot under lock
        metric_t *batch[MAX_METRICS];
        int n;
        pthread_mutex_lock(&lock);
        n = n_metrics;
        memcpy(batch, metrics, n * sizeof(metric_t*));
        n_metrics = 0;
        pthread_mutex_unlock(&lock);

        for (int i = 0; i < n; i++) {
            char buf[256];
            int len = snprintf(buf, sizeof(buf), "%s=%.2f t=%ld\n",
                               batch[i]->name, batch[i]->value, batch[i]->timestamp);
            ssize_t r = send(sock_fd, buf, len, 0);
            if (r < 0) {
                // log + reconnect
            }
            free(batch[i]->name);
            free(batch[i]);
        }
    }
    return NULL;
}
```

</details>

## 偵查順序建議

**第一輪：static check**
```bash
# 編譯時開 warnings
gcc -Wall -Wextra -O2 -pthread metric-collector.c -o /dev/null
```

**第二輪：sanitizer**
```bash
gcc -O1 -g -pthread -fsanitize=thread metric-collector.c -o c-tsan
./c-tsan &
sleep 10; kill %1

gcc -O1 -g -pthread -fsanitize=address metric-collector.c -o c-asan
./c-asan &
sleep 10; kill %1
```

**第三輪：runtime observation**
```bash
./collector &
PID=$!

# 看 fd / RSS 變化
watch -n 1 "echo === fd; ls /proc/$PID/fd | wc -l; echo === rss; cat /proc/$PID/status | grep VmRSS"

# 看 syscall
sudo strace -p $PID -c &
sleep 30; kill %2

# 看 lib call (limited use)
ltrace -p $PID -c &
sleep 10; kill %3
```

**第四輪：crash**
```bash
ulimit -c unlimited
./collector &
# 等 crash
coredumpctl gdb -1
(gdb) bt full
```

## 進階挑戰

**A. 加 metric**：把整個 daemon 加上 prometheus-format 的自我 metric。`/metrics` endpoint 暴露自己 RSS、處理 metric 數、send 失敗數。

**B. 用 bpftrace 監控**：寫一支 bpftrace script，attach uprobe 到 `make_metric` / `send`，histogram 顯示 latency。

**C. fault injection**：用 LD_PRELOAD 攔 `connect`，10% 機率回 ECONNREFUSED。觀察修好的 daemon 怎麼 retry。

**D. 寫成 systemd service**：加 unit file，learn_linux_boot 學的東西用上。

**E. 用 ASan + libfuzzer 對 metric parser fuzz**（如果你加了 input parser）。

## 最終驗收

- [ ] 找出 5+ 個 bug、寫 incident report
- [ ] 每個 bug 對應到工具的 evidence（screenshot / log）
- [ ] 修正版跑 helgrind / TSan / ASan / valgrind 全乾淨
- [ ] 修正版跑 1 小時 RSS 穩定、core 沒產生
- [ ] 修正版送出的 metric 數字 = 收到 metric 數字（無遺失）

## 自我檢核

- [ ] strace / lsof / ss / valgrind / sanitizer / coredumpctl 至少各用過一次
- [ ] 知道每種 bug 對應哪種工具最快
- [ ] 寫過 incident report
- [ ] 修正版不再有任何工具報錯

恭喜完課！整套 22 章 + 3 練習 + 1 final 完成的話，你對「Linux 程式行為觀察」的工具掌握度應該超過 95% 工程師。

接下來如果還想深入：

- **Linux kernel 觀察**：`learn_bpf` 的 eBPF 深入、kprobe / fentry 自寫
- **performance engineering**：Brendan Gregg 的 BPF Performance Tools 一書
- **production debugging**：寫 SRE / incident response，把這套搬進 oncall
- **security observability**：auditd、Falco、tetragon —— 這套工具往安全方向延伸

→ 回到 [課程地圖](./README.md)
