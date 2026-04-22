# Ch 11 — 多執行緒 debug

> 目標：熟練 `info threads` / `thread N` / `thread apply` / non-stop mode / scheduler-locking，能在多 thread 程式中精準定位 race condition、deadlock、crash。

## 多執行緒 debug 的根本困難

Single-thread：bug 依賴執行路徑。
Multi-thread：bug 依賴執行路徑**乘以**多個 thread 的交錯。

你每一次 debug 都**改變了時序**：下斷點、暫停、print — 這些動作都會讓某個 thread 慢一拍。結果是：

- **Heisenbug**：加了 debug 動作就不重現
- **race 只有特定 interleaving 才觸發**
- **deadlock 出現在你不看的時候**

工具層級有兩種處理方式：

1. **在 GDB 裡手動戳**：慢、精細、適合事後分析
2. **ThreadSanitizer（TSan）**：編譯時插入 monitor，執行時抓 race — 類比 ASan

兩個都要會，本章先講 GDB，最後會對 TSan 打補丁。

## 範例程式：`threads.c`

```c
#define _GNU_SOURCE
#include <stdio.h>
#include <pthread.h>
#include <unistd.h>

int counter = 0;
pthread_mutex_t mtx = PTHREAD_MUTEX_INITIALIZER;

void *worker(void *arg) {
    long id = (long)arg;
    for (int i = 0; i < 100000; i++) {
        pthread_mutex_lock(&mtx);
        counter++;
        pthread_mutex_unlock(&mtx);
    }
    printf("thread %ld done\n", id);
    return NULL;
}

int main(void) {
    pthread_t t[4];
    for (long i = 0; i < 4; i++) {
        pthread_create(&t[i], NULL, worker, (void *)i);
    }
    for (int i = 0; i < 4; i++) {
        pthread_join(t[i], NULL);
    }
    printf("counter = %d\n", counter);
    return 0;
}
```

編譯：

```bash
gcc -g -O0 threads.c -o threads -pthread
```

跑幾次，counter 應該都是 400000（因為有 mutex）。改天我們去掉 mutex 看 race。

## 看 thread 清單

進 gdb，跑到 main 裡 thread 都啟動的地方：

```
(gdb) b pthread_join
(gdb) r
...
(gdb) info threads
  Id   Target Id                                  Frame
* 1    Thread 0x7ffff7f87740 (LWP 12345) "threads"  __GI___pthread_join ...
  2    Thread 0x7ffff7f86640 (LWP 12346) "threads"  worker (arg=0x0) at threads.c:11
  3    Thread 0x7ffff7785640 (LWP 12347) "threads"  worker (arg=0x1) at threads.c:11
  4    Thread 0x7ffff6f84640 (LWP 12348) "threads"  worker (arg=0x2) at threads.c:12
  5    Thread 0x7ffff6783640 (LWP 12349) "threads"  worker (arg=0x3) at threads.c:13
```

縮寫 `i th`。

- `*` 標示當前 thread
- **Id**：GDB 內部編號（你用來切換的）
- **Target Id**：pthread id + LWP（Linux 的 kernel thread id）
- **Frame**：當前停在哪裡

## 切 thread

```
(gdb) thread 3          ; 切到 thread 3
[Switching to thread 3 (Thread ...) (LWP 12347)]
#0  worker (arg=0x1) at threads.c:11
11          pthread_mutex_lock(&mtx);
```

縮寫 `t 3`。

切過去後，`bt`、`p`、`info locals` 都是在 thread 3 的 context 下。

## 對所有 thread 下指令：`thread apply`

```
(gdb) thread apply all bt
```

對每個 thread 執行 `bt`，一眼看完所有 thread 當下位置。**deadlock debug 的第一招。**

```
(gdb) thread apply all bt -frame-arguments all -frame-info source-and-location
```

完整版輸出。

只對某幾個 thread：

```
(gdb) thread apply 2-4 bt
(gdb) thread apply 2 3 5 info locals
```

## All-stop 模式（預設）：一暫停全暫停

GDB 預設的「all-stop mode」：任何一個 thread 停下，**所有 thread 都暫停**。

這簡化心智模型：debug 時沒有其他 thread 在偷跑。但副作用：

- 你在 thread A 做事，thread B 也不動 — 不能模擬「正常執行時的競爭」
- 有時候單純想 continue 某個 thread 測試、不動其他 — 做不到

## Non-stop 模式：各自獨立

```
(gdb) set pagination off         ; 先關掉分頁輸出，避免被擋
(gdb) set non-stop on
(gdb) r
```

啟動後：

- 任何 thread 停下（斷點 / signal），**其他 thread 繼續跑**
- `continue` 只動當前 thread，其他不變
- `c -a` 或 `continue -a` 才是「所有 thread 都 continue」

這比較貼近生產環境的真實狀態，但你要小心：你在 debug 某個 thread 時，別的 thread 的執行可能讓你的 state 過期。

**不建議初學時用 non-stop。** 等你遇到「想 debug 一個 thread 但別的 thread 必須繼續吃 queue」的情境再切。

## `scheduler-locking`：凍結其他 thread

在 all-stop mode 下，當你 `step` / `next` 一個 thread 時，其他 thread **會趁機跑一點**。想阻止：

```
(gdb) set scheduler-locking on          ; step/next/continue 時只動當前 thread
(gdb) set scheduler-locking step        ; 只 step/next 鎖住，continue 不鎖（預設推薦）
(gdb) set scheduler-locking off         ; 不鎖，任何動作其他 thread 都可跑（預設）
```

三個選項：

| 模式 | step/next | continue |
|---|---|---|
| `off` | 其他 thread 會跑 | 其他 thread 會跑 |
| `step` | **只動當前** | 其他 thread 會跑 |
| `on` | 只動當前 | 只動當前 |

**`step` 是最常用的設定**：step 時鎖死（不讓其他 thread 亂我思考），continue 時放行（讓系統正常跑）。

建議：

```
(gdb) set scheduler-locking step
```

加進 `~/.gdbinit`（Ch 14）。

## `thread-specific breakpoint`

只在特定 thread 擊中時停：

```
(gdb) b worker thread 3
```

```
(gdb) b threads.c:12 thread 3
```

其他 thread 經過這裡不停。

配合條件：

```
(gdb) b worker thread 3 if i > 50000
```

## 抓 deadlock：thread apply all bt

修改範例，故意 deadlock：

```c
pthread_mutex_t m1 = PTHREAD_MUTEX_INITIALIZER;
pthread_mutex_t m2 = PTHREAD_MUTEX_INITIALIZER;

void *a_then_b(void *arg) {
    pthread_mutex_lock(&m1);
    sleep(1);
    pthread_mutex_lock(&m2);          // 會卡
    pthread_mutex_unlock(&m2);
    pthread_mutex_unlock(&m1);
    return NULL;
}

void *b_then_a(void *arg) {
    pthread_mutex_lock(&m2);
    sleep(1);
    pthread_mutex_lock(&m1);          // 會卡
    pthread_mutex_unlock(&m1);
    pthread_mutex_unlock(&m2);
    return NULL;
}

int main(void) {
    pthread_t t1, t2;
    pthread_create(&t1, NULL, a_then_b, NULL);
    pthread_create(&t2, NULL, b_then_a, NULL);
    pthread_join(t1, NULL);
    pthread_join(t2, NULL);
    return 0;
}
```

跑起來會卡住。在另一個 terminal：

```bash
ps aux | grep threads              # 找 pid
gdb -p <PID>
```

`attach` 到卡住的 process：

```
(gdb) thread apply all bt

Thread 3 (Thread 0x...): worker b_then_a ...
#0  futex_wait (...)
#1  pthread_mutex_lock ... (mutex=&m1)
#2  b_then_a ... at dead.c:25

Thread 2 (Thread 0x...): worker a_then_b ...
#0  futex_wait (...)
#1  pthread_mutex_lock ... (mutex=&m2)
#2  a_then_b ... at dead.c:14

Thread 1 (Thread 0x...): main
#0  futex_wait (...)
#1  pthread_join ...
#2  main at dead.c:...
```

清楚：thread 2 在等 m2、thread 3 在等 m1。交叉鎖，deadlock。

## 抓 race：thread sanitizer

GDB 抓 race 很難（race 是「兩個 thread 沒 sync 存取同個 memory」這種事件，很難定位）。**ThreadSanitizer 是正解**：

```bash
gcc -g -O1 -fsanitize=thread threads_unsynced.c -o race
./race
```

```
==================
WARNING: ThreadSanitizer: data race (pid=12345)
  Write of size 4 at 0x555555558010 by thread T2:
    #0 worker threads.c:11 (race+0x...)

  Previous write of size 4 at 0x555555558010 by thread T1:
    #0 worker threads.c:11 (race+0x...)
...
==================
```

直接告訴你兩個 thread 的哪行互搶同一個記憶體。

**實務建議**：race condition 先上 TSan，它找得到的就找到；找不到的（依賴外部事件的 race、或 TSan 誤報）才到 GDB 手工挖。

## 抓 race：GDB 手工法（沒 TSan 時）

- 對可疑的共享變數下 watchpoint，看哪個 thread 在改
- `info threads` 看各 thread 當下位置
- 用條件斷點鎖定可疑 iteration
- 用 non-stop + scheduler-locking 精細控制 interleaving

但坦白說這很痛。TSan 先行。

## Thread 相關的 convenience variable

```
(gdb) p $_thread          ; 當前 thread id（gdb 編號）
$1 = 3
```

## 常見坑

1. **`info threads` 顯示 `<unavailable>`**：你 attach 到一個剛 fork 但還沒完成 thread 初始化的 process。`c` 一下通常就好。
2. **某個 thread 「消失了」**：它 exit 了，但 pthread join 還沒處理。GDB 會在下次看到。
3. **step 一個 thread 卻動了全部**：忘了 `set scheduler-locking step`。
4. **non-stop mode 下 `print` 打不出結果**：因為那個 thread 還在跑。先 `interrupt` 該 thread，或 `t N` 切到它。
5. **deadlock 在 gdb attach 時就自動解了**：GDB 發 SIGSTOP 可能剛好打斷某個 lock 的 spin — 罕見。attach 多幾次通常會剛好在 stuck 時 catch 到。
6. **thread 名字都是 `"threads"`**：預設沒設名字，用 `pthread_setname_np(pthread_self(), "my-worker")` 可以給每個 thread 命名，`info threads` 會顯示。
7. **mutex debug 輔助**：`set print thread-events on`（預設開）會在 thread 生成 / 終止時印訊息；用 `info mutex`（需 glibc debug info）可以看 mutex 狀態。

## 動手練習

### 練習一：基本 thread 切換

用上面的 `threads.c`：

1. 在 `worker` 裡下斷點 `b worker`，`r`，觀察是哪個 thread 先到。
2. `info threads` 看所有 thread 的位置。
3. `t 3` 切到 thread 3，`bt`，`info locals`。
4. `t 2`、`t 4` 切換，確認每個 thread 有自己的 stack。
5. 試 `thread apply all bt`。

### 練習二：scheduler-locking

1. 打開 `set scheduler-locking step`，在 worker 裡 `next` 幾次，看計數器 `counter` 如何只被當前 thread 改。
2. 關掉（`off`），再 step，看 counter 突然跳很多 — 其他 thread 偷跑了。

### 練習三：deadlock

寫上面的 `dead.c`，跑起來，`gdb -p PID` attach，`thread apply all bt` 確認 deadlock 圖譜。

### 練習四：race

把 mutex 拿掉：

```c
void *worker(void *arg) {
    for (int i = 0; i < 100000; i++) {
        counter++;            // 沒 lock
    }
    return NULL;
}
```

1. 跑幾次 `./threads`，看 counter 每次結果不同（有時剛好對、多半不對）。
2. 用 TSan 編譯跑，看它抓到 race。
3. 試著用 GDB 下 `watch counter`，看觸發頻率（因為無法鎖定交錯，難以實際定位）。

## 自我檢核

- [ ] 我能用 `info threads`、`thread N`、`thread apply all bt` 掌握多 thread 程式狀態
- [ ] 我能設 `scheduler-locking step` 讓 step 操作穩定
- [ ] 我能下 thread-specific breakpoint
- [ ] 我知道 all-stop 跟 non-stop mode 的差別
- [ ] 我能在 deadlock 時 attach 並用 `thread apply all bt` 看全景
- [ ] 我知道 TSan 是抓 race 的首選工具

下一章跨機器：`gdbserver`。讓你在本機 gdb 裡 debug 遠端 Linux、甚至 ARM 板子上的程式。

→ [Ch 12 Remote debugging 與 gdbserver](./12-remote-debugging.md)
