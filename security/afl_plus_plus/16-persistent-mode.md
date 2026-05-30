# Ch 16 — Persistent Mode：跑 10000 次，只 Fork 一次

> **目標**：能為任意 target 寫出正確的 persistent mode harness；理解 persistent mode 的 fork-then-loop 機制；能識別 state leak 問題。

> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

---

## 為什麼需要這個？

AFL++ 用 forkserver 加速 fuzzing：程式啟動一次，之後每個 test case 用 `fork()` 產生 child process。
這比每次從頭 `execve()` 快很多，但 `fork()` 本身也不是免費的。

對一個輕量的 parser（如 JSON 解析器），每次 `fork()` 可能佔掉整個執行時間的 30-50%。
這就是 **persistent mode** 要解決的問題：讓一個 process **在自己的 main loop 裡跑 N 次**，完全省掉 fork 的開銷。

兩種模式的差異：

```
Deferred forkserver（預設）：
  parent 執行到插樁點後等待
  每個 test case → fork child → child 跑 target logic → child 死亡
  成本：一次 fork/wait per test case

Persistent mode：
  fork 一次得到 child
  child 在自己的 loop 裡跑 N 次 target logic
  N 次後 child 才死亡
  成本：一次 fork/wait per N test cases
```

實際效果：對輕量 target，persistent mode 比 deferred forkserver 快 3-10x；
對已經很重的 target（每次 iteration 1 秒以上），差距縮小到 1.1-1.2x。

---

## 先建立直覺

想像你在工廠做品管，要測試 10000 個零件：

- **Deferred forkserver**：每個零件都叫一個新工人來測，測完工人離職，再叫下一個——每次都要「上班打卡、拿工具、做事、下班」。
- **Persistent mode**：叫一個工人來，他一直測，測完一個馬上測下一個，只有最後才下班——「打卡」只發生一次。

Persistent mode 的前提是：這個工人（process）在測試每個零件之間，**能回到乾淨的初始狀態**。
如果上一個零件的污跡留在他手上（state leak），會影響下一個測試的結果。

---

## 橫向連結

- **Ch 11（Forkserver）**：Persistent mode 是在 forkserver 基礎上再進一層最佳化。
- **Ch 17（Harness Design）**：Persistent mode 的 harness 是「好 harness」的標準形式。
- **LibFuzzer**：`LLVMFuzzerTestOneInput()` 就是 persistent mode 的標準化介面，AFL++ 也支援。

---

## `__AFL_LOOP()`：最小可行 Harness

```c
#include <stdint.h>
#include <stddef.h>

// 要 fuzz 的函式
void target_func(const uint8_t *data, size_t size);

int main(void) {
    // 告訴 AFL++ 用 SHM 傳輸 input（避免 file I/O）
    __AFL_FUZZ_INIT();

    while (__AFL_LOOP(10000)) {
        uint8_t *buf = __AFL_FUZZ_TESTCASE_BUF;
        size_t   len = __AFL_FUZZ_TESTCASE_LEN;

        target_func(buf, len);
    }
    return 0;
}
```

三個巨集的職責：

| 巨集 | 位置 | 作用 |
|------|------|------|
| `__AFL_FUZZ_INIT()` | main 最前面（在任何初始化之前） | 設定 SHM input 的 fd 和 mapping |
| `__AFL_LOOP(N)` | while 條件 | 向 forkserver 報告「這次 iteration 結束，請給下一個 input」；第 N 次後回傳 0 讓 loop 退出 |
| `__AFL_FUZZ_TESTCASE_BUF` / `__AFL_FUZZ_TESTCASE_LEN` | loop 內 | 取得當前 input 的指標和長度，直接從 SHM 讀 |

---

## `__AFL_LOOP()` 做了什麼？

`__AFL_LOOP()` 在底層呼叫的是 forkserver 的通訊協定。
理解它的執行流程，才能避免踩雷：

```
第一次呼叫 __AFL_LOOP()：
  1. 向 parent（afl-fuzz）發送 "ready for fork" 訊號
  2. parent 呼叫 fork()——此時 child 被 fork 出來
  3. fork 後，child 繼續往下執行（進入 loop body）
  4. Parent 等待 child 的 status 報告

每次 loop 結束時（__AFL_LOOP() 再次被呼叫）：
  1. Child 向 parent 報告 "iteration done, coverage data ready"
  2. Parent 讀取 coverage SHM，判斷是否有新 edge
  3. Parent 可以選擇發送新 input（透過 SHM）
  4. Child 繼續 loop

第 N 次後：
  __AFL_LOOP() 回傳 0
  loop 結束，child 正常退出
  Parent fork 一個新 child 繼續
```

關鍵點：**child 被 fork 的時機是第一次呼叫 `__AFL_LOOP()` 的時候**，不是 `main()` 的開頭。
所以 `__AFL_FUZZ_INIT()` 和任何你想在 fork 前執行的初始化（如載入大型資料集），要放在 `__AFL_LOOP()` 之前。

---

## SHM Input vs File Input

**File input（`@@` 模式）**：
```
afl-fuzz 寫 input 到 /tmp/afl-xxx
target 用 fopen/read 讀取
每次 iteration：disk write + disk read + buffer copy
```

**SHM input（`__AFL_FUZZ_TESTCASE_BUF`）**：
```
afl-fuzz 把 input 寫入 SHM（shared memory）
target 直接讀取 SHM 指標
每次 iteration：memcpy（或直接用指標）
```

對輕量 target（解析 1KB input，耗時 100μs），disk I/O 可能佔 30% 以上的時間。
SHM input 省掉這個成本，是 persistent mode 效能提升的第二個來源。

---

## 底層機制：Fork-Then-Loop 架構

```
afl-fuzz (parent)
│
│  sends: input via SHM
│
├─── fork() ──────────────────────────────────────────┐
│                                                      │
│                               child process          │
│                               │                      │
│                               │ __AFL_FUZZ_INIT()    │
│                               │ while(__AFL_LOOP(N)):│
│  ◄──── "ready" ───────────────┤   iteration 1        │
│  sends new input ─────────────►                      │
│  ◄──── "done, coverage" ──────┤   iteration 2        │
│  sends new input ─────────────►                      │
│         ...                   │   ...                │
│                               │   iteration N        │
│                               │ return 0             │
│                               └──── exits ──────────┘
│
│  ◄──── child exit status ─────
│
└─── fork() again (next N iterations)
```

每個 child process 負責 N 次 iteration，之後正常退出。
如果 child 在第 k 次（k < N）crash，parent 立刻偵測到 child 異常退出，把那次的 input 記錄為 crash，然後 fork 新 child。

---

## 帶 State Reset 的 Harness

State leak 是 persistent mode 最常見的問題。
下面是一個處理每次 iteration 的記憶體 reset 的範例：

```c
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

// 假設 target 有一個全局 context
typedef struct {
    char    name[256];
    int     count;
    void   *heap_data;
} ParseContext;

static ParseContext g_ctx;  // 全局狀態——每次 iteration 都要 reset

void ctx_init(ParseContext *ctx) {
    memset(ctx->name, 0, sizeof(ctx->name));
    ctx->count = 0;
    // 注意：每次重新 malloc，避免 heap 狀態污染
    free(ctx->heap_data);
    ctx->heap_data = malloc(4096);
}

int parse_with_context(ParseContext *ctx, const uint8_t *data, size_t size);

__AFL_FUZZ_INIT();

int main(void) {
    // 一次性初始化：開 context（首次 malloc）
    memset(&g_ctx, 0, sizeof(g_ctx));
    g_ctx.heap_data = malloc(4096);

    while (__AFL_LOOP(1000)) {
        uint8_t *buf = __AFL_FUZZ_TESTCASE_BUF;
        size_t   len = __AFL_FUZZ_TESTCASE_LEN;

        // 每次 iteration 開始：reset state
        ctx_init(&g_ctx);

        // 跑 target logic
        parse_with_context(&g_ctx, buf, len);

        // 不需要 cleanup：下次 loop 開頭的 ctx_init 會處理
    }

    free(g_ctx.heap_data);
    return 0;
}
```

**重點**：
- `malloc(4096)` 在一次性初始化做一次，之後每次 iteration 只 `free + malloc`（或 `memset`）
- 如果 target 的 heap 操作很複雜，`memset` 全域狀態可能不夠——需要完整的 `free + re-init`
- Static 變數和 libc 內部狀態（如 `rand()` 的 seed、`strtok()` 的狀態指標）都要注意

---

## 識別 State Leak

**診斷方法**：把 `__AFL_LOOP(N)` 的 N 設成不同值，比較結果：

```bash
# 測試 1：N=1（等同於普通 forkserver）
cat target_harness.c | sed 's/AFL_LOOP([0-9]*/AFL_LOOP(1/' > target_n1.c
afl-clang-fast -o target_n1 target_n1.c
afl-fuzz -i seeds/ -o out_n1/ -- ./target_n1

# 測試 2：N=1000
afl-clang-fast -o target_n1000 target_harness.c
afl-fuzz -i seeds/ -o out_n1000/ -- ./target_n1000
```

如果兩組發現的 crash 集合**不一樣**，或 N=1 能發現而 N=1000 找不到（或反過來），代表有 state leak 影響了結果。

**常見的 state leak 來源**：

| 來源 | 範例 | 修復方式 |
|------|------|---------|
| 全局變數 | `static int g_error_code;` | 在 loop 頂端 reset |
| Static local 變數 | `static bool initialized = false;` | 在 loop 頂端手動設回 false |
| Heap 狀態 | malloc free list 碎片化 | 接受（通常無害）或用 jemalloc reset |
| File descriptor | 每次 iteration 開新 fd 但沒關 | 記得 close |
| Signal handler 狀態 | SIGALRM 後的 flag | 在 loop 頂端 clear |
| libc errno | 錯誤後 errno 被設定 | 在 loop 頂端 `errno = 0` |

---

## 對比與取捨

| 模式 | 典型 execs/sec（輕量 parser） | 開發複雜度 | State leak 風險 | 適用場景 |
|------|------------------------------|-----------|----------------|---------|
| 無 forkserver（`execve` 每次） | 500-2000 | 零（直接跑 binary） | 無 | 無法修改 target source |
| Deferred forkserver | 2000-10000 | 低（加一行 `AFL_INIT_FORKSERVER()`） | 無 | 有重型初始化的 binary |
| Persistent mode + file input | 10000-50000 | 中（改寫 main）| 有 | Library，可改 source |
| Persistent mode + SHM input | 50000-200000 | 中（同上 + `__AFL_FUZZ_INIT()`）| 有 | Library，追求最高速度 |

數字僅為量級參考，實際差異取決於 target 的邏輯複雜度。

---

## 踩雷集錦

**1. `__AFL_LOOP(N)` 的 N 不要設太大**

N 越大，state 污染越深。發生 crash 前已跑過 N-1 次，heap 狀態和全局變數可能已被多次修改。
發現的 crash 可能在 N=1 模式下無法重現（bug 依賴特定的 state 累積）。

推薦值：`1000`（平衡 performance 和 state 污染風險）。
對確認沒有 state leak 的 target，可以調到 `10000`。

**2. 在 loop 裡 call `exit()` 而不是 `return`**

```c
while (__AFL_LOOP(1000)) {
    // 錯誤：直接 exit() 繞過 forkserver 協定
    if (error_condition) exit(1);  // afl-fuzz 看到非零 exit，記錄為 crash

    // 正確：用 return 或 continue 結束這次 iteration
    if (error_condition) continue;  // 或用 goto 跳到 loop 結尾
}
```

`exit()` 會結束整個 child process，afl-fuzz 把它當成 crash 記錄。
除非真的是 crash，否則應該讓 loop 繼續。

**3. File descriptor leak 在 persistent mode 累積**

```c
while (__AFL_LOOP(1000)) {
    // 錯誤：每次 iteration 都開 fd，沒有對應的 close
    int fd = open("/tmp/some_file", O_RDONLY);
    process_file(fd);
    // 忘記 close(fd)
}
// 跑 1000 次後，process 有 1000 個 open fd
// Linux 預設限制是 1024，之後 open() 會失敗
```

**4. ASAN 和 persistent mode 的 overhead 會累積**

ASAN 在 heap allocation 時加 shadow memory，free 後不立刻回收。
跑 N 次 iteration 後，ASAN 的 overhead 可能讓 process 的 RSS（resident set size）不斷增長，最終 OOM。

解法：用 `__AFL_LOOP(100)` 而不是 `__AFL_LOOP(10000)` 讓 process 定期重啟，讓 ASAN 的記憶體被回收。

**5. `__AFL_FUZZ_INIT()` 必須在 `main()` 最前面**

若你在 `__AFL_FUZZ_INIT()` 之前做了任何會影響 SHM mapping 的操作（如 `mmap()`），可能導致 SHM input 功能失效。
最安全的做法：`main()` 第一行就是 `__AFL_FUZZ_INIT()`。

---

## 進階：再往深一層

### 和 LibFuzzer 的 persistent mode 對比

LibFuzzer 的介面是：

```c
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    // fuzz target 邏輯
    return 0;
}
```

AFL++ 也支援這個介面（透過 `utils/aflpp_driver/`），讓你寫一份 harness 同時支援 AFL++ 和 LibFuzzer。
兩者的底層機制不同：LibFuzzer 在自己的 process 裡 loop，AFL++ 用 forkserver 協定通訊。

### Deferred forkserver 和 persistent mode 組合

你可以同時使用 deferred forkserver（推遲 fork 的時機）和 persistent mode：

```c
int main(void) {
    // 重型初始化：載入資料庫、解碼大型資源
    init_large_dataset();  // 只執行一次

    // 在初始化完成後啟動 forkserver，避免 fork 複製初始化開銷
    __AFL_INIT();

    // SHM input 初始化
    __AFL_FUZZ_INIT();

    while (__AFL_LOOP(1000)) {
        uint8_t *buf = __AFL_FUZZ_TESTCASE_BUF;
        size_t   len = __AFL_FUZZ_TESTCASE_LEN;
        process(buf, len);
    }
    return 0;
}
```

`__AFL_INIT()` 是 deferred forkserver 的觸發點，`__AFL_LOOP()` 是 persistent mode 的 loop。
兩者組合：fork 在初始化後才發生，且每個 child 跑 1000 次。

### 在 loop 裡使用 `setjmp`/`longjmp` 做 crash recovery

若 target 可能 call `abort()` 或觸發 SIGSEGV，你可以用 `setjmp`/`longjmp` 攔截並繼續 loop：

```c
#include <setjmp.h>
#include <signal.h>

static jmp_buf recovery_buf;

void signal_handler(int sig) {
    longjmp(recovery_buf, 1);
}

while (__AFL_LOOP(1000)) {
    signal(SIGSEGV, signal_handler);
    if (setjmp(recovery_buf) == 0) {
        // 正常執行路徑
        target_func(buf, len);
    } else {
        // crash recovery：繼續下一次 iteration
        // 注意：這會隱藏真正的 crash，除非你確定要這樣做
    }
}
```

**警告**：這個技巧用於「你想讓 fuzzer 繼續跑，不被預期的 crash 中斷」的場景。
不要在想發現 crash 的 fuzzing session 裡用，因為它會吃掉 crash signal。

---

## 動手練習

### 練習 1：改寫 CLI tool 為 persistent mode harness

找一個簡單的開源 parser（如 `minijson`、`tomlc99`），：

```c
// 原始使用方式
// int main(int argc, char **argv) { ... parse file ... }

// 改寫為 persistent mode harness
#include <stdint.h>
#include <stddef.h>

int parse(const uint8_t *data, size_t len);  // 提取出的 parse 函式

__AFL_FUZZ_INIT();

int main(void) {
    while (__AFL_LOOP(1000)) {
        uint8_t *buf = __AFL_FUZZ_TESTCASE_BUF;
        size_t   len = __AFL_FUZZ_TESTCASE_LEN;
        parse(buf, len);
    }
    return 0;
}
```

用 `afl-whatsup -s out/` 觀察 execs/sec，和用 `@@` 的版本比較。

### 練習 2：診斷 state leak

```c
// 這個 harness 有刻意的 state leak，找出來並修復
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>

static int call_count = 0;
static char *shared_buffer = NULL;

void buggy_process(const uint8_t *data, size_t size) {
    call_count++;
    // 在第 42 次呼叫時觸發 bug
    if (call_count == 42 && size > 0) {
        shared_buffer[0] = data[0];  // 可能 crash
    }
}

__AFL_FUZZ_INIT();

int main(void) {
    shared_buffer = malloc(1);

    while (__AFL_LOOP(1000)) {
        uint8_t *buf = __AFL_FUZZ_TESTCASE_BUF;
        size_t   len = __AFL_FUZZ_TESTCASE_LEN;
        buggy_process(buf, len);
    }
    return 0;
}
```

問題：`call_count` 沒有 reset，bug 只在第 42 次呼叫才觸發。
N=1 模式：每次 fork 後 call_count 從 0 開始，第 42 個 input 觸發 bug。
N=1000 模式：call_count 在整個 1000 次 loop 裡累積，行為不可預期。

---

## 本章重點整理

- Persistent mode 讓一個 child process 在自己的 loop 裡跑 N 次 iteration，省掉每次 fork 的成本；配合 SHM input（`__AFL_FUZZ_TESTCASE_BUF`）省掉 disk I/O，對輕量 target 能達到普通模式 5-20x 的速度。
- `__AFL_LOOP(N)` 是 forkserver 協定的核心：第一次呼叫時觸發 fork，之後每次呼叫回報 coverage 並接收新 input，第 N 次回傳 0 讓 child 退出。
- State leak 是 persistent mode 最常見的 bug：全局變數、static 變數、fd leak、libc 內部狀態都可能在 iterations 之間累積；診斷方法是比較 N=1 和 N=1000 兩種模式的結果。

---

## 自我檢核

1. Deferred forkserver 和 persistent mode 的成本差異在哪裡？各自省掉什麼？
2. `__AFL_LOOP()` 被呼叫的時候，在 forkserver 協定層面發生了什麼？child 是什麼時候被 fork 的？
3. SHM input 比 file input 快在哪裡？什麼情況下差距最明顯？
4. 列出三種常見的 state leak 來源，各自的修復方式是什麼？
5. 為什麼在 loop 裡用 `exit()` 而不是 `continue` 是錯誤的？
6. ASAN 和 persistent mode 搭配使用時，為什麼要把 N 設小一點？

---

## 延伸閱讀

**AFL++ `docs/persistent_mode.md`**
- 核心貢獻：官方的 persistent mode 完整說明，包含所有巨集的語義、deferred forkserver 和 persistent mode 的組合用法、已知 limitation
- 讀哪裡：全部，篇幅不長（約 3 頁）
- 和本章關聯：本章的 harness 範例直接來源於這份文件，讀完後你能自己寫任何形式的 persistent harness

**LibFuzzer 官方文件（https://llvm.org/docs/LibFuzzer.html）**
- 核心貢獻：`LLVMFuzzerTestOneInput()` 介面的規格；和 AFL++ persistent mode 的設計哲學對比——LibFuzzer 把 harness 和 fuzzer engine 合在一個 binary，AFL++ 是分離的
- 讀哪裡："Fuzz Target" 和 "Usage" 節
- 和本章關聯：AFL++ 支援 LibFuzzer 介面（透過 `aflpp_driver`），理解兩者的差異讓你能寫出同時相容兩個 fuzzer 的 harness

**"FuzzGen: Automatic Fuzzer Generation"（USENIX Security 2020）**
- 核心貢獻：自動從 library 的 API 使用模式生成 persistent mode harness；說明了好的 harness 的形式化定義
- 讀哪裡：Section 3（harness 的形式化定義）和 Section 4（自動生成的演算法）
- 和本章關聯：手寫 harness 之後，理解「自動化」這件事，以及為什麼它困難

→ [下一章：Ch 17 — Harness Design：為任意 Target 量身打造進入點](17-harness-design.md)
