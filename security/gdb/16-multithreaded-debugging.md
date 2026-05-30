# Ch 16 — 多執行緒除錯

> **目標**：掌握 GDB 的多執行緒 debug——`info threads`、切換 thread、`thread apply`、scheduler-locking、thread-specific 斷點、檢視各 thread 的 stack 與 TLS。理解為什麼多執行緒 bug（race、deadlock）難抓，以及 GDB 能/不能幫你什麼。

> **環境**：GDB 13/14，Linux x86_64，pthread。編譯記得 `-pthread`。

## 為什麼多執行緒 debug 是另一個檔次

單執行緒 debug 你有「程式碼一行行往下走」的線性直覺。多執行緒打碎這個直覺：多個 thread 同時在跑、交錯執行、共享記憶體、彼此競爭。bug 變成：

- **race condition**：兩個 thread 同時改一個變數，結果取決於誰先到——時有時無、難重現。
- **deadlock**：A 等 B 的鎖、B 等 A 的鎖，互相卡死。
- **觀察者效應**：你一加斷點/print，timing 變了，race 就不出現了（Heisenbug）。

GDB 在這裡是強力但不萬能的工具。它能讓你看清「現在每個 thread 在哪、在等什麼」，但 race 的「重現」本身就難——這也是 Ch 35 rr（record-replay）存在的理由。

## 先建立直覺：多個執行點

```c
// thread_demo.c — gcc -g -O0 -pthread thread_demo.c -o thread_demo
#include <stdio.h>
#include <pthread.h>
long counter = 0;
void *worker(void *arg) {
    long id = (long)arg;
    for (int i = 0; i < 100000; i++)
        counter++;                  // ← race！多 thread 同時 counter++
    return NULL;
}
int main(void) {
    pthread_t t[4];
    for (long i = 0; i < 4; i++) pthread_create(&t[i], NULL, worker, (void*)i);
    for (int i = 0; i < 4; i++) pthread_join(t[i], NULL);
    printf("counter = %ld (expected 400000)\n", counter);   // 幾乎一定 < 400000
    return 0;
}
```

```
   單執行緒：一個 $pc 在程式裡走
   多執行緒：N 個 $pc 同時在程式裡走，各有各的 stack、暫存器
        thread 1: 在 worker 的 counter++
        thread 2: 在 worker 的 counter++   ← 同時！這就是 race
        thread 3: 在 pthread_join 等待
        main:     在 pthread_join 等待
```

每個 thread 在 Linux 是一個獨立的可排程實體（有自己的 TID），GDB 對每個都維護 ptrace 關係（Ch 2 提過）。

## `info threads`：看所有 thread

```
(gdb) info threads
  Id   Target Id                  Frame
* 1    Thread 0x7ffff7d.. (LWP 1234) "thread_demo" main () at thread_demo.c:14
  2    Thread 0x7ffff75.. (LWP 1235) "thread_demo" worker (arg=0x0) at thread_demo.c:7
  3    Thread 0x7ffff6d.. (LWP 1236) "thread_demo" worker (arg=0x1) at thread_demo.c:7
  ...
```

- `*` 標示**當前 focus 的 thread**（你的指令作用在它身上）
- `Id`：GDB 的 thread 編號（1, 2, 3…）
- `LWP`：Linux 的 TID（kernel 眼中的 thread id）
- `Frame`：每個 thread 此刻停在哪——一眼看出誰在工作、誰在等鎖

## 切換 thread 與 thread apply

```
(gdb) thread 3               # 切換 focus 到 thread 3；簡寫 t 3
(gdb) bt                     # 現在 bt 看的是 thread 3 的 stack
(gdb) print id               # thread 3 的區域變數

(gdb) thread apply all bt    # 對「所有」thread 各做一次 bt——deadlock 分析神器！
(gdb) thread apply all bt -frame-arguments scalars   # 精簡版
(gdb) thread apply 2-4 print id   # 對 thread 2,3,4 各做一次
```

`thread apply all bt` 是多執行緒 debug 最重要的一招：**一次看到所有 thread 的呼叫鏈**。deadlock 時，你能看到「thread 2 卡在 lock A 等 lock B、thread 3 卡在 lock B 等 lock A」——死結一目了然。

## scheduler-locking：控制 step 時誰在動

這是多執行緒 debug 最容易出錯、也最重要的設定。當你 `step` 一個 thread，**其他 thread 預設也會跑一點**（因為 GDB 讓整個 process 動一下）。結果：你想單步 thread 2，按一下 `next`，發現 thread 3 已經偷跑了一大段，狀態全亂。

```
(gdb) set scheduler-locking on       # step/continue 時「只有」當前 thread 動，其他凍結
(gdb) set scheduler-locking step     # 只在 step 時鎖（continue 時放開）← 推薦
(gdb) set scheduler-locking off      # 預設：所有 thread 一起動
(gdb) show scheduler-locking
```

| 設定 | step 時 | continue 時 |
|---|---|---|
| `off`（預設） | 全部 thread 動 | 全部動 |
| `step` | **只當前 thread 動** | 全部動 |
| `on` | 只當前 thread 動 | 只當前 thread 動 |

**`scheduler-locking step` 是日常推薦**：單步時專注一個 thread（不被別的干擾），但 continue 時讓大家正常跑（避免人為製造 deadlock）。強烈建議寫進 `.gdbinit`。

> 警告：`scheduler-locking on` 時 `continue` 只跑當前 thread。如果當前 thread 去等一個「需要別的 thread 才能釋放的鎖」，你就**人為製造了 deadlock**——GDB 永遠回不來。這是新手常見的「GDB 卡死」原因。需要 continue 跑出結果時記得設回 `step` 或 `off`。

## thread-specific 斷點

只對特定 thread 生效的斷點：

```
(gdb) break worker thread 3          # 只有 thread 3 跑到 worker 才停
(gdb) break thread_demo.c:8 thread 2 if i > 50000   # thread-specific + 條件
```

debug「只有某個 worker 出問題」時，避免每個 thread 都觸發斷點的噪音。

## 檢視 thread-local storage（TLS）

`__thread` / `thread_local` 變數每個 thread 有自己一份：

```
(gdb) print errno                    # errno 是 TLS——印的是「當前 focus thread」的
(gdb) thread 2
(gdb) print errno                    # thread 2 的 errno（可能不同）
(gdb) thread apply all print errno   # 比較所有 thread 的 errno
```

TLS 變數的值依當前 focus thread 而定。debug「為什麼 thread 2 的 errno 是 EBADF」時，先切到 thread 2。

## 一個 race condition 的觀察流程

對 `thread_demo.c` 的 `counter++` race：

```
(gdb) set scheduler-locking step
(gdb) break worker
(gdb) run
(gdb) info threads                   # 看 4 個 worker + main
(gdb) thread 2
(gdb) disassemble                    # counter++ 其實是 load/add/store 三條指令！
   ...
   mov    counter, %rax              # load
   add    $1, %rax                   # increment
   mov    %rax, counter              # store ← 三步之間別的 thread 可能插隊
```

race 的根源在這：`counter++` 不是原子操作，是 load-add-store 三步。thread 2 load 了舊值，還沒 store，thread 3 也 load 了同樣的舊值——兩個 +1 變成一個。GDB 讓你看到這個非原子性，但要「穩定重現」race 的特定交錯，純 GDB 很難（timing 太敏感），這正是 Ch 35 rr 的舞台。練習 C 會深入。

## 踩雷集錦

1. **`scheduler-locking on` 造成 GDB 卡死**：當前 thread continue 去等別的 thread 才能放的鎖。設回 `step` 或 `off`。
2. **step 一個 thread 結果別的偷跑**：沒設 `scheduler-locking step`，其他 thread 在你單步時動了。日常設 `step`。
3. **race 加了斷點就不出現（Heisenbug）**：斷點改變 timing。改用 watchpoint（影響較小）、rr（Ch 35 確定性重播）、或 ThreadSanitizer（編譯期）。
4. **`print x` 印錯 thread 的值**：忘了先 `thread N` 切到對的 thread。TLS 變數尤其如此。
5. **deadlock 不知卡在哪**：第一招永遠是 `thread apply all bt`，看每個 thread 在等什麼。
6. **新建 thread 沒被通知**：`set print thread-events on`（預設開）讓 thread 建立/結束時印一行，否則 thread 默默出現你不知道。

## 進階：再往深一層

- **`$_thread` convenience variable**：當前 thread 編號，寫腳本時用（`thread apply all` 裡判斷）。
- **non-stop mode（Ch 15）+ 多執行緒**：只停出問題的 thread、其他續跑，debug 線上服務時的黃金組合。
- **deadlock 的鎖分析**：`thread apply all bt` 後，看每個 thread 卡在哪個 `pthread_mutex_lock`，配合 `print mutex->__data.__owner` 看鎖被誰持有（glibc 的 mutex 內部結構）。
- **ThreadSanitizer (TSan)**：`gcc -fsanitize=thread`——編譯期插樁，能在 race **發生時**自動報告，比 GDB 手動抓 race 有效得多。GDB 用於「事後檢視狀態」，TSan 用於「主動偵測 race」，互補。
- **rr（Ch 35）**：record 一次執行（含 thread 交錯），可以重複、reverse 地重播**同一個** race——把不可重現的變可重現。多執行緒 debug 的終極武器。
- **`info threads` 的 LWP 與 `/proc/<pid>/task`**：每個 thread 在 `/proc/<pid>/task/<tid>` 有自己的資訊，GDB 的 LWP 就是這個 tid。

## 動手練習

1. 對 `thread_demo.c`，`info threads` 看 4 個 worker + main，理解 `*`、Id、LWP。
2. `thread apply all bt` 一次看所有 thread 的 stack。
3. 不設 scheduler-locking，`step` 一個 thread，觀察 counter 被別的 thread 改動；再 `set scheduler-locking step` 重來，確認單步時 counter 不被干擾。
4. 故意 `set scheduler-locking on` 然後對一個在等 join 的 thread `continue`，體驗「人為 deadlock / GDB 卡住」，按 Ctrl-C 救回。
5. `disassemble worker` 看 `counter++` 的 load-add-store 三條指令，理解 race 的非原子根源。
6. 寫一個 A/B 互鎖的 deadlock 程式，跑到卡死後 Ctrl-C，用 `thread apply all bt` 找出死結。

## 本章重點整理

- 多執行緒 = 多個 `$pc` 同時跑，各有 stack/暫存器/TLS；每個 thread 是獨立可排程實體（LWP/TID）。
- `info threads` 看全貌（`*`=focus）；`thread N` 切換；`thread apply all bt` 是 deadlock 分析第一招。
- scheduler-locking：`step`（單步只動當前、推薦）/ `on`（完全鎖、小心 deadlock）/ `off`（預設全動）。
- thread-specific 斷點 `break ... thread N`；TLS 變數依 focus thread。
- race 難抓（Heisenbug、timing 敏感）；GDB 看狀態，TSan 偵測 race，rr 重現 race——三者互補。

## 自我檢核

- [ ] `thread apply all bt` 解決什麼問題？deadlock 時為什麼是第一招？
- [ ] scheduler-locking 的三個值各是什麼行為？日常該設哪個、為什麼？
- [ ] 什麼情況 `scheduler-locking on` 會讓 GDB「卡死」？
- [ ] 為什麼 `counter++` 會有 race？用 GDB 怎麼看出它的非原子性？
- [ ] race 加了斷點就消失，你有哪些替代手段？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Debugging Programs with Multiple Threads](https://sourceware.org/gdb/current/onlinedocs/gdb/Threads.html)**
  - **讀哪裡**：info threads、thread apply、thread-specific breakpoints、scheduler-locking、`set print thread-events`。
  - **和本章的關聯**：本章所有指令的權威。

### 部落格 / 文章

- **[ThreadSanitizer](https://github.com/google/sanitizers/wiki/ThreadSanitizerCppManual)** — Google sanitizers
  - **這篇說什麼**：TSan 怎麼自動偵測 data race。
  - **為什麼值得讀**：GDB 抓 race 很被動，TSan 主動；真實工作兩者並用。

- **[Debugging multithreaded deadlocks with GDB](https://www.gnu.org/software/libc/manual/html_node/POSIX-Threads.html)** 配 glibc pthread 文件
  - **為什麼值得讀**：理解 pthread mutex 內部結構，才能在 GDB 裡看「鎖被誰持有」。

### 進階（伏筆）

- **[rr: Record and Replay](https://rr-project.org/)**
  - **和本章的關聯**：把不可重現的 race 變可重現；Ch 35 完整展開。

下一章是並行 debug 的另一半：多行程——fork/exec 時 GDB 跟誰走、follow-fork-mode、debug client/server 與 fork 炸彈式的程式。

→ [Ch 17 多行程與 fork / exec](./17-multiprocess-fork-exec.md)
