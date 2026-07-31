# Final Project — 偵探破案：修好壞掉的 daemon

> **目標**：整合整門課（Ch 0–21）的所有工具和方法，偵探破案一個藏了 5 個 bug 的壞掉 daemon——它連不上、會崩潰、記憶體漲、結果不對、偶爾卡住。你要用整套觀察工具（strace/lsof/ss/perf/valgrind/sanitizers/core dump/ptrace）找出每個 bug 並修好。這 5 個 bug 是精心設計的 cascade（connect 失敗 + race + UAF + leak + segfault），強迫你用對的工具系統地破案。完成後你具備「面對任何壞掉的程式，系統化地用工具找出所有問題」的能力——這是本課的終極目標。

## 專案總覽

你接手一個壞掉的 daemon（一個多執行緒的網路服務），它有各種症狀：

```
壞掉的 daemon 的症狀（5 個 bug 的 cascade）：

  症狀 1：啟動後「連不上」（client 連不到服務）
  症狀 2：偶爾崩潰（segfault）
  症狀 3：跑久了記憶體一直漲（leak）
  症狀 4：計數結果不對（有時對有時錯）
  症狀 5：偶爾整個卡住不動
        │
  你的任務：用整套工具找出 5 個 bug，逐一修好
        │
  工具對應（系統化破案）：
    連不上 → strace（看 connect/bind 的 syscall）、ss、lsof -i
    崩潰   → core dump + gdb、ASan
    記憶體漲 → valgrind/ASan、/proc VmRSS
    結果不對 → TSan/helgrind（data race）
    卡住   → strace -p（看卡在哪個 syscall）、/proc/wchan
```

這個 daemon 整合了本課的所有觀察維度——syscall（strace）、狀態（lsof/ss/proc）、效能（perf）、記憶體（valgrind/ASan）、並發（TSan/helgrind）、崩潰（core dump）。破案它需要你綜合運用整套工具，這是 debug 能力的終極考驗。

## 為什麼做這個專案？

這正是真實工作最有挑戰的場景——接手一個別人寫的、壞掉的、多種問題交織的程式（legacy code、出問題的生產服務），你要不靠原作者、不靠完整文件，用工具系統地找出所有問題。這需要的不是「會用某個工具」，而是「面對一堆症狀，知道每個症狀用什麼工具、怎麼系統地破案」。

完成這個專案，你證明了自己具備完整的 debug 能力——這是區分資深和初級工程師的核心。你不再「瞎猜、加 printf、重啟試試」，而是「系統化地觀察、定位、修復、驗證」。這是本課從 Ch 0 到 Ch 21 培養的終極能力的綜合展現，也是你能向任何人證明的硬實力。

## 整合的課程概念

| Bug / 症狀 | 用到的工具與章節 |
|---|---|
| 連不上 | strace（Ch 5）、ss（Ch 9）、lsof -i（Ch 8）|
| segfault | core dump（Ch 21）、gdb、ASan（Ch 18）|
| 記憶體 leak | valgrind memcheck（Ch 15）、ASan（Ch 18）、/proc（Ch 7）|
| data race | TSan（Ch 18）、helgrind（Ch 16）|
| 卡住/deadlock | strace -p（Ch 5）、/proc/wchan（Ch 7）、helgrind（Ch 16）|
| 系統化方法 | 分層觀察（Ch 1）、選工具（Ch 1）|
| 理解底層 | ptrace（Ch 3-4）、/proc（Ch 7）、core（Ch 21）|

整門課至少 70% 的核心概念都用上了——這是 Final Project 的標準。

## 目標程式（broken daemon，藏 5 個 bug）

```c
// broken_daemon.c — 一個藏了 5 個 bug 的多執行緒網路 daemon
// 編譯：gcc -g -O0 broken_daemon.c -o broken_daemon -pthread
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/socket.h>
#include <netinet/in.h>

#define PORT 7777
int request_count = 0;              // 共享計數器
char *log_buffer = NULL;

// Bug 4: data race（request_count 沒鎖保護）
void* handle_client(void *arg) {
    int client_fd = *(int*)arg;
    free(arg);                      // 釋放傳進來的 arg

    char buf[64];
    read(client_fd, buf, sizeof(buf));
    request_count++;                // Bug 4: race！多個 thread 同時 ++

    // Bug 2: use-after-free（log_buffer 可能被別的 thread free 了）
    if (log_buffer) {
        strcpy(log_buffer, buf);    // 可能 UAF
    }

    // Bug 3: 記憶體 leak（response 沒 free）
    char *response = malloc(128);
    sprintf(response, "Handled request #%d\n", request_count);
    write(client_fd, response, strlen(response));
    // 忘了 free(response)！

    close(client_fd);
    return NULL;
}

int main() {
    log_buffer = malloc(64);

    int server_fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(PORT);
    // Bug 1: bind 到 127.0.0.1（只本機）—— 但需求是對外服務
    // 加上沒設 SO_REUSEADDR（重啟時 bind 失敗 "address in use"）
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);

    if (bind(server_fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        perror("bind");             // Bug 1: 重啟時 bind 失敗（沒 SO_REUSEADDR）
        return 1;
    }
    listen(server_fd, 10);
    printf("Daemon listening on 127.0.0.1:%d\n", PORT);

    // Bug 5: 模擬偶爾的 deadlock/segfault（簡化）
    free(log_buffer);               // 在 thread 還會用 log_buffer 時就 free → UAF/segfault
    log_buffer = NULL;

    while (1) {
        int *client_fd = malloc(sizeof(int));
        *client_fd = accept(server_fd, NULL, NULL);
        if (*client_fd < 0) { free(client_fd); continue; }
        pthread_t t;
        pthread_create(&t, NULL, handle_client, client_fd);
        pthread_detach(t);
    }
    return 0;
}
```

## 如果你卡住了

1. 一個症狀一個症狀破——別想一次抓全部。先讓它「能連上」（Bug 1），再抓其他
2. 連不上：`strace ./broken_daemon` 看 bind/listen 的 syscall（bind 到哪、有沒有失敗）；`ss -tlnp` 看它聽哪
3. 崩潰：啟用 core（`ulimit -c unlimited`），崩潰後 `gdb ./broken_daemon core` 的 `bt`；或用 ASan 編譯
4. 記憶體漲：valgrind 或 ASan 的 leak 偵測；`watch grep VmRSS /proc/<pid>/status`
5. 結果不對（race）：TSan 編譯（`-fsanitize=thread`）或 helgrind
6. 卡住：`strace -p <pid>` 看卡在哪個 syscall
7. 用對工具，不要瞎猜或加 printf（並發/記憶體 bug 加 printf 沒用）

## 實作步驟建議

### Step 1：先讓它「能連上」（strace/ss 抓 Bug 1）
### Step 2：抓記憶體 bug（ASan/valgrind 抓 leak/UAF — Bug 2, 3, 5）
### Step 3：抓 data race（TSan/helgrind 抓 Bug 4）
### Step 4：逐一修復所有 bug
### Step 5：用整套工具驗證（全部乾淨）+ 確認 daemon 正常運作

## 完整參考解答

**這是 Final Project，務必自己破案！** 用工具系統地找出每個 bug 才是學習的重點。下面是破案過程和修復。

<details>
<summary>破案過程與修復</summary>

```bash
cd ~/obslab
gcc -g -O0 broken_daemon.c -o broken_daemon -pthread

# ========== Bug 1：連不上（strace + ss）==========
./broken_daemon &
DAEMON=$!
sleep 1
# 從外部連（用對外 IP）
ss -tlnp | grep 7777
# LISTEN 127.0.0.1:7777 ...   ← Bug 1: 只聽 127.0.0.1！外部連不上
# → 根因：bind 到 127.0.0.1（應該 INADDR_ANY 對外）
# 另外：重啟時
kill $DAEMON; ./broken_daemon
# bind: Address already in use   ← Bug 1b: 沒 SO_REUSEADDR（重啟 bind 失敗）

# ========== Bug 2,3,5：記憶體 bug（ASan）==========
gcc -g -fsanitize=address -O1 broken_daemon.c -o bd_asan -pthread
./bd_asan &
sleep 1
# 連一個 client 觸發 handler
echo "test" | nc 127.0.0.1 7777
sleep 1
# ASan 報告：
# heap-use-after-free ... handle_client (log_buffer)   ← Bug 2/5: UAF
#   (main free 了 log_buffer，但 thread 還用它)
# LeakSanitizer: leak ... handle_client (response)      ← Bug 3: response leak
pkill bd_asan

# ========== Bug 4：data race（TSan）==========
gcc -g -fsanitize=thread -O1 broken_daemon.c -o bd_tsan -pthread
./bd_tsan &
sleep 1
# 多個 client 並發
for i in 1 2 3 4 5; do echo "x" | nc 127.0.0.1 7777 & done
sleep 2
# TSan 報告：
# data race ... handle_client (request_count++)         ← Bug 4: race
pkill bd_tsan

# ========== 修復所有 bug ==========
# （見下方 fixed_daemon.c）
```

```c
// fixed_daemon.c — 修好所有 5 個 bug
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/socket.h>
#include <netinet/in.h>

#define PORT 7777
int request_count = 0;
pthread_mutex_t count_lock = PTHREAD_MUTEX_INITIALIZER;   // 修 Bug 4: 加鎖

void* handle_client(void *arg) {
    int client_fd = *(int*)arg;
    free(arg);
    char buf[64] = {0};
    read(client_fd, buf, sizeof(buf) - 1);

    pthread_mutex_lock(&count_lock);    // 修 Bug 4: 鎖保護
    request_count++;
    int my_count = request_count;
    pthread_mutex_unlock(&count_lock);

    // 修 Bug 2/5: 不共用會被 free 的 log_buffer（用 local 的）
    // （或用鎖保護 log_buffer 的生命週期；這裡簡化為移除問題用法）

    char *response = malloc(128);
    sprintf(response, "Handled request #%d\n", my_count);
    write(client_fd, response, strlen(response));
    free(response);                     // 修 Bug 3: free

    close(client_fd);
    return NULL;
}

int main() {
    int server_fd = socket(AF_INET, SOCK_STREAM, 0);
    int opt = 1;
    setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));  // 修 Bug 1b

    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(PORT);
    addr.sin_addr.s_addr = INADDR_ANY;  // 修 Bug 1: 對外（不是 127.0.0.1）

    if (bind(server_fd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        perror("bind"); return 1;
    }
    listen(server_fd, 10);
    printf("Daemon listening on 0.0.0.0:%d\n", PORT);
    // 修 Bug 5: 不在 thread 還會用時 free log_buffer（移除錯誤的 free）

    while (1) {
        int *client_fd = malloc(sizeof(int));
        *client_fd = accept(server_fd, NULL, NULL);
        if (*client_fd < 0) { free(client_fd); continue; }
        pthread_t t;
        pthread_create(&t, NULL, handle_client, client_fd);
        pthread_detach(t);
    }
    return 0;
}
```

```bash
# 驗證修復：全部工具乾淨
gcc -g -O0 fixed_daemon.c -o fixed_daemon -pthread

# 1. 能對外連（Bug 1 修好）
./fixed_daemon &
ss -tlnp | grep 7777   # LISTEN 0.0.0.0:7777 ← 對外了！

# 2. ASan 乾淨（Bug 2/3/5 修好）
gcc -g -fsanitize=address -O1 fixed_daemon.c -o fd_asan -pthread
# 連幾個 client，ASan 無 leak/UAF 報告

# 3. TSan 乾淨（Bug 4 修好）
gcc -g -fsanitize=thread -O1 fixed_daemon.c -o fd_tsan -pthread
# 並發 client，TSan 無 race 報告

# → 所有 bug 修好，所有工具驗證乾淨，daemon 正常運作
```

**破案說明**：

- **系統化**：一個症狀一個工具——連不上用 ss/strace（看聽哪、bind 結果）、記憶體用 ASan、race 用 TSan
- **Bug 1（連不上）**：bind 到 127.0.0.1（只本機）→ 改 INADDR_ANY；沒 SO_REUSEADDR（重啟失敗）→ 加 setsockopt
- **Bug 2/5（UAF）**：main free log_buffer 但 thread 還用 → 移除錯誤的 free / 用鎖管理生命週期
- **Bug 3（leak）**：response 沒 free → 加 free
- **Bug 4（race）**：request_count++ 沒鎖 → 加 mutex
- **修復→驗證閉環**：每個工具從報告 bug 到 0 報告，確認修好
- **核心**：用對工具（不瞎猜、不加 printf），系統地破案——這是整套 debug 能力的綜合

</details>

## 測試用案例

| 症狀 | 工具 | 找到的 bug |
|---|---|---|
| 連不上 | ss -tlnp | bind 127.0.0.1 + 無 SO_REUSEADDR |
| UAF/崩潰 | ASan / core | log_buffer 被 free 後用 |
| 記憶體漲 | ASan leak | response 沒 free |
| 結果不對 | TSan | request_count++ race |
| 修復後 | 全部 | 0 報告 + 正常運作 |

## 延伸挑戰（加分）

- **挑戰一**：用 perf（Ch 12）profile 修好的 daemon，找出效能熱點，優化（如果有的話）

- **挑戰二**：寫一個「健康檢查腳本」——用整套工具（ss/lsof/strace/proc）自動檢查 daemon 的狀態（連線數、fd 數、記憶體、有沒有異常狀態）

- **挑戰三**：用 LD_PRELOAD（Ch 20）的 fault injection 測試 daemon 的錯誤處理——讓某些 malloc/accept 失敗，看 daemon 有沒有正確處理

- **挑戰四**：用 bpftrace（Ch 14）寫一個 one-liner 監控 daemon 的某個行為（如統計每個 client 的 read 大小、連線速率）

- **挑戰五**：模擬生產環境——讓 daemon 在背景跑、設定 core dump、寫一個「崩潰時自動收集診斷」的機制（core + 各種狀態快照）

- **挑戰六**：寫一份「事故報告」——像真實的 SRE 事故報告，記錄每個 bug 的症狀、用什麼工具發現、根因、修復、怎麼預防（這是真實工作的產出）

## 自我檢核

完成這個專案後，你應該能回答：

- [ ] 面對一個有多種症狀的壞掉程式，我能系統化地用工具逐一破案（而非瞎猜）
- [ ] 我知道每種症狀（連不上/崩潰/leak/race/卡住）該用哪個工具
- [ ] 我能用對的工具精確定位每個 bug 的根因（到行）
- [ ] 我會修復並用工具驗證（修復→驗證閉環）
- [ ] 我理解這些工具底層怎麼運作（ptrace/proc/動態連結/core），不只會用
- [ ] 面試被問「你怎麼 debug 一個壞掉的服務」，我能展示這套系統化的方法

## 結語：你現在站在哪裡

完成這門課和這個專案，你已經從「會寫程式但 debug 靠瞎猜」進化到「能系統化地觀察任何程式的行為、定位任何問題」。你知道：

- 程式的真實行為是 syscall（strace），你能看見它（Ch 2-5）
- 工具怎麼運作——你親手用 ptrace 寫了 mini-strace、用 LD_PRELOAD 攔截 library（Ch 3-4, 19-20）
- 系統的當前狀態怎麼觀察（/proc/lsof/ss/sysstat，Ch 7-10）
- 為什麼慢——用 perf 找熱點、用 cachegrind 看 cache（Ch 12, 17）
- 記憶體和並發 bug 怎麼抓——valgrind/sanitizers（Ch 15-18）
- 崩潰後怎麼分析——core dump（Ch 21）
- kernel 內部怎麼觀察——ftrace/bpftrace（Ch 13-14）

這些不是「會用工具」，是**理解程式行為的完整能力**。你能面對任何陌生的、壞掉的程式，用工具系統地找出問題——這正是資深工程師和「只會加 printf 重啟試試」的人的根本差異。

最重要的是，你不被工具限制——因為你理解它們的底層（ptrace、動態連結、/proc、core），當現成工具不夠用時，你能自己造一個（你已經造過 mini-strace 和 LD_PRELOAD 攔截器）。這是本課最珍貴的收穫——「理解工具底層、能自己造工具」的能力。

接下來往哪去？這門課的「精選資料庫」（見 [README](./README.md)）列了進階方向：bpf 課把 bpftrace/eBPF 推到極致（kernel 層可程式化觀測）、gdb 課深入互動式 debug（本課的 ptrace 是它的底層）、Brendan Gregg 的書把效能觀測推到生產級。但更重要的是——**去用它、去 debug 真實的程式**。你的觀察工具是放大鏡，真實的 bug 是最好的老師。

恭喜你走到這裡。你現在有了看穿任何程式行為的眼睛。
