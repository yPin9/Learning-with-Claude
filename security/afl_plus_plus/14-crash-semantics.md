# Ch 14 — Crash Semantics：讀懂每一個 Signal

> **目標**：能區分不同 crash signal 的語意，理解 AFL++ 如何判斷 crash uniqueness，以及 `out/crashes/` 目錄的結構——讓你在面對幾百個 crash 檔案時，知道從哪裡下手。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

---

## 為什麼需要這個？

AFL++ 跑了 24 小時，你回來看結果：`crashes/` 目錄裡有 312 個檔案。

這 312 個代表什麼？312 個獨立 bug？同一個 bug 的 312 種觸發方式？還是其中只有 3 個真正的 bug，其他都是重複的？

這是 fuzzing 最讓人沮喪的環節——工具幫你找到了 crash，但**分類和理解 crash 的工作仍然是人的責任**。

AFL++ 的 crash 去重機制基於 bitmap（邊界覆蓋），不是 stack trace。這個設計決策在 2013 年有其合理性，但也帶來了特定的誤報和漏報模式。如果你不理解這個機制，你會浪費大量時間在重複分析同一個 bug，或者更糟糕地，漏掉真正的 bug。

這章的目標是讓你能夠：在看到 crash 檔案名稱的瞬間就知道大概發生了什麼；根據 signal 類型設定分析優先序；正確重現每一個 crash；理解哪些 crash 可能是同一個 bug。

---

## 先建立直覺

把一個 crash 想像成一份「案發現場報告」。Signal 是案發類型：

- `SIGSEGV`：有人試圖進入一棟不存在的建築（非法記憶體位址）
- `SIGABRT`：建築物的安全系統自爆（程式主動呼叫 `abort()`）
- `SIGBUS`：有人試圖用錯誤的鑰匙（alignment）開門
- `SIGFPE`：計算機被要求除以零
- `SIGILL`：有人念了一段無意義的咒語（CPU 不認識的 instruction）

AFL++ 的 crash uniqueness 機制則像是：「兩份報告如果描述的是相同的『哪些道路被走過』，就算同一起案件。」這個類比不完美，但能幫助你理解為什麼同一個 bug 可能有多份報告，不同 bug 卻可能被合并。

---

## 橫向連結

- **Ch 10 — Fork Server**：AFL++ 的 signal handler 和 fork server 有直接關係——fork server 攔截 child process 的 termination signal 並回傳給 fuzzer。理解 fork server 讓本章的「底層機制」一節更容易消化。
- **Ch 11 — Havoc / Ch 13 — Dictionary**：dictionary 讓 fuzzer 更快到達深層程式碼，因此可能觸發更多 crash；但 crash 數量暴增時，本章的 triage 技巧就更重要。
- **練習 C — Corpus Triage**：本章是理論，練習 C 是實踐——完整走過 triage 流程。

---

## Crash Signal 語意

### SIGSEGV — Segmentation Fault

**觸發條件**：存取沒有被 mmap 的記憶體位址、寫入唯讀記憶體區段（如 `.text`）、null pointer dereference。

```c
// 典型觸發場景
char *p = NULL;
*p = 'A';   // SIGSEGV: 寫入 address 0

// 或
char buf[16];
memcpy(buf + 9999, input, 4);   // 越界寫入到未 mmap 的地址
```

**對 fuzzer 的意義**：最常見的 crash，通常對應 out-of-bounds write、heap buffer overflow、use-after-free（UAF）。需要 ASAN 或 GDB 才能確認具體 bug 類型。

**Signal number**：11

---

### SIGABRT — Abort

**觸發條件**：
- `abort()` 被直接呼叫
- `assert()` 失敗（等同 `abort()`）
- `malloc` 偵測到 double-free 或 heap corruption（glibc 的 `__malloc_check_fail`）
- **AddressSanitizer（ASan）**：几乎所有 ASan 偵測到的錯誤都以 `SIGABRT` 結束

```c
// glibc heap 保護觸發 SIGABRT
free(ptr);
free(ptr);   // double-free → SIGABRT (without ASan)

// ASan 觸發 SIGABRT
char *buf = malloc(16);
buf[20] = 'A';   // heap-buffer-overflow → ASan → SIGABRT
```

**對 fuzzer 的意義**：啟用 ASan 後，大量 crash 會從 SIGSEGV 變成 SIGABRT。分析 SIGABRT 時，優先看 ASan 的 stderr 輸出（含有詳細的錯誤類型和 stack trace）。

**Signal number**：6

---

### SIGBUS — Bus Error

**觸發條件**：
- **Alignment violation**：對特定架構（非 x86_64）或特定操作，存取未對齊的地址
- **mmap 範圍外**：`mmap` 一個檔案後，存取超過檔案大小的偏移量（x86_64 上常見）
- **硬體錯誤**（罕見）

```c
// mmap 觸發 SIGBUS
int fd = open("small_file", O_RDONLY);
char *p = mmap(NULL, 4096, PROT_READ, MAP_SHARED, fd, 0);
// 如果 small_file 只有 10 bytes，存取 p[100] → SIGBUS
char c = p[100];   // SIGBUS
```

**對 fuzzer 的意義**：在 fuzzing 檔案 parser 時，target 可能用 mmap 讀取輸入，此時輸入大小不符預期會觸發 SIGBUS。相對少見，但往往指向特定的 mmap 使用模式。

**Signal number**：7

---

### SIGFPE — Floating Point Exception

**觸發條件**：
- 整數除零（`INT_MIN / -1` 也會觸發，因為結果溢位）
- `1 / 0`（整數）
- 浮點數異常（取決於 FPU 設定，預設不觸發 signal）

```c
// 整數除零
int a = 10;
int b = 0;
int c = a / b;   // SIGFPE

// 陷阱：INT_MIN / -1 也是 undefined behavior
int x = INT_MIN;
int y = x / -1;  // SIGFPE on x86_64（因為結果是 INT_MAX + 1，溢位）
```

**對 fuzzer 的意義**：在 fuzzing 計算密集的程式（parser 的數值欄位、影像解碼器）時可能出現。不如 SIGSEGV 常見，但代表程式邏輯上的除零路徑沒有防護。

**Signal number**：8

---

### SIGILL — Illegal Instruction

**觸發條件**：
- CPU 執行到不認識的 opcode
- `ud2` instruction（UB 觸發的 trap）
- UBSan（Undefined Behavior Sanitizer）——部分 UBSan 錯誤以 `ud2` + SIGILL 回報

```c
// UBSan 觸發 SIGILL
int arr[4];
int x = arr[10];   // array out-of-bounds → UBSan → SIGILL

// 或 signed integer overflow
int a = INT_MAX;
int b = a + 1;    // signed overflow → UBSan → SIGILL
```

**對 fuzzer 的意義**：啟用 UBSan（`AFL_USE_UBSAN=1`）後，大量 UB 會以 SIGILL 表現。分析 SIGILL 時，同樣要看 stderr 的 UBSan 報告。

**Signal number**：4

---

### Timeout（SIGKILL）— 不是 crash，但要知道

AFL++ 對超過 timeout 的執行用 `SIGKILL` 強制終止，並記錄到 `out/hangs/`，不在 `out/crashes/` 裡。

```bash
# 調整 hang timeout
afl-fuzz -t 5000 ...   # 5 秒 timeout（預設 1000ms）
AFL_HANG_TMOUT=10000   # 超過 10 秒才算 hang（避免誤判慢的 testcase）
```

**對 fuzzer 的意義**：hang 可能代表無窮迴圈、deadlock、或超慢的輸入處理路徑。不要忽略 `hangs/`——有時無窮迴圈和 crash 一樣嚴重（DoS 漏洞）。

---

## `out/crashes/` 目錄結構

每個 crash 是一個獨立檔案，命名格式：

```
id:000000,sig:11,src:000003,time:12345,execs:67890,op:havoc,rep:4
│         │       │          │          │            │        │
│         │       │          │          │            │        └─ repetition count
│         │       │          │          │            └─────────── mutation 操作種類
│         │       │          │          └──────────────────────── 執行次數
│         │       │          └─────────────────────────────────── 時間戳（ms）
│         │       └────────────────────────────────────────────── source seed 的 id
│         └────────────────────────────────────────────────────── signal number
└──────────────────────────────────────────────────────────────── crash 的唯一 id
```

**實際範例**

```bash
ls out/crashes/
# id:000000,sig:11,src:000003,time:12345,execs:67890,op:havoc,rep:4
# id:000001,sig:11,src:000003,time:23456,execs:89012,op:havoc,rep:2
# id:000002,sig:06,src:000007,time:34567,execs:91234,op:splice,rep:8
# id:000003,sig:11,src:000003,time:45678,execs:12345,op:flip1,rep:1
# README.txt
```

**快速分組**

```bash
# 按 signal 統計
ls out/crashes/ | grep -oP 'sig:\K[0-9]+' | sort | uniq -c | sort -rn
#   248 11    ← SIGSEGV (最多)
#    51 06    ← SIGABRT
#    13 11    ← (部分可能是不同 bug)

# 按 source seed 統計（哪個 seed 衍生出最多 crash）
ls out/crashes/ | grep -oP 'src:\K[0-9]+' | sort | uniq -c | sort -rn
```

---

## 底層機制：它是怎麼運作的？

### AFL++ 的 Signal 攔截

```
Fork Server 等待 child
       │
  child 執行 target(testcase)
       │
  target 觸發 SIGSEGV
       │
  kernel 送 signal 給 child process
       │
  child 終止（default SIGSEGV handler = core dump + terminate）
       │
  fork server 呼叫 waitpid()，取得 exit status
       │
  exit status 編碼了 termination signal
  (WTERMSIG(status) == SIGSEGV → 11)
       │
  fork server 透過 SHM pipe 回傳 exit status 給 afl-fuzz
       │
  afl-fuzz 解析 exit status → 記錄 crash
```

AFL++ **不安裝自己的 signal handler** 到 target process 裡（除非使用 persistent mode）。它完全依賴 POSIX 的 `waitpid()` 機制取得 child 的終止原因。這就是為什麼你在 target 裡安裝的 signal handler 不會影響 AFL++ 的 crash 偵測——只要 signal 導致 process 終止，AFL++ 就能看到。

### Crash Uniqueness：Bitmap-Based 去重

AFL++ 用**邊界覆蓋 bitmap（edge coverage bitmap）**判斷 crash 是否為新的 unique crash：

```
每個 crash 發生時，SHM bitmap 的狀態
（記錄了「哪些 (from_basic_block, to_basic_block) 邊界被走過」）

  crash A:  bitmap = [0, 1, 0, 1, 1, 0, 0, 1, ...]
  crash B:  bitmap = [0, 1, 0, 1, 1, 0, 0, 1, ...]  ← 和 crash A 完全相同 → 視為重複
  crash C:  bitmap = [0, 1, 0, 1, 0, 1, 0, 1, ...]  ← 不同 → 新的 unique crash
```

**重要含義**：

```
情境 1：同一個 bug，不同觸發路徑
  Path A → buf[10] overflow → SIGSEGV  (bitmap A)
  Path B → buf[10] overflow → SIGSEGV  (bitmap B ≠ A)
  → AFL++ 記錄為 2 個 crash，但其實是同一個 bug

情境 2：不同 bug，相同 path
  Path X → UAF → SIGSEGV     (bitmap X)
  Path X → null deref → SIGSEGV (bitmap X)（假設走到兩個 bug 的路徑恰好相同）
  → AFL++ 只記錄 1 個 crash，但其實是 2 個不同 bug
```

**Bitmap-based 去重 vs Stack-hash-based 去重**

| 特性 | Bitmap-based（AFL++） | Stack-hash-based（GDB/ASAN/Exploitable） |
|------|----------------------|------------------------------------------|
| 計算成本 | 極低（SHM 比對） | 高（需要完整 unwind） |
| 誤報率（重複 bug 被視為不同） | 高 | 低 |
| 漏報率（不同 bug 被合并） | 中 | 中（相同 stack hash） |
| 需要 debug info | 否 | 是 |
| 適合 fuzzing 期間 | 是（快速） | 否（太慢） |
| 適合 triage 後期 | 否 | 是（更精確） |

結論：AFL++ 的去重機制是為 fuzzing 速度最佳化的，**不是為了精確的 bug 分類**。Triage 時還需要額外工具（GDB、ASAN report、`afl-tmin`）。

---

## 重現 Crash

**基本重現**

```bash
# @@ 模式（檔案輸入）
./target out/crashes/"id:000000,sig:11,src:000003,..."

# stdin 模式
./target < out/crashes/"id:000000,sig:11,src:000003,..."

# 或用變數避免引號問題
CRASH="out/crashes/id:000000,sig:11,src:000003,time:12345,execs:67890,op:havoc,rep:4"
./target "$CRASH"
```

**用 GDB 分析**

```bash
# 直接用 GDB 執行並在 crash 時停下
gdb -q ./target
(gdb) run "$CRASH"
# Program received signal SIGSEGV
(gdb) bt          # backtrace
(gdb) info registers
(gdb) x/20x $rsp  # 看 stack

# 或 core dump 分析
ulimit -c unlimited
./target "$CRASH"
gdb ./target core   # 載入 core dump
```

**用 ASAN 確認 bug 類型**

```bash
# 重新編譯帶 ASAN 的版本
AFL_USE_ASAN=1 afl-clang-fast -o target_asan target.c

# 重現 crash，ASAN 會輸出詳細錯誤報告
./target_asan "$CRASH" 2>&1 | head -40
# ==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x...
# READ of size 4 at 0x... thread T0
#     #0 0x... in parse_input target.c:42
#     #1 0x... in main target.c:78
```

---

## 進一步：Hangs 的處理

AFL++ 把超過 timeout 的執行記錄到 `out/hangs/`，命名格式和 crashes 相同但沒有 `sig:` 欄位：

```
id:000000,src:000005,time:98765,execs:11111,op:havoc,rep:4
```

```bash
# 重現 hang（記得設長一點的 timeout）
timeout 30 ./target out/hangs/"id:000000,..."

# 或用 AFL_HANG_TMOUT 調整判斷閾值
# （預設是 fuzzing timeout 的 10 倍）
AFL_HANG_TMOUT=30000 afl-fuzz ...
```

**SIGKILL 和 SIGTERM 的差別**：AFL++ 對 hang 用 `SIGKILL`（無法被攔截），確保 child process 一定終止。如果你的 target 有 cleanup handler 需要執行（例如寫 log），AFL++ 的 SIGKILL 會跳過這些 handler——這是正常行為。

---

## 對比與取捨

| 特性 | AFL++ bitmap-based | Stack-hash（GDB/Crashwalk） | ASan symbolize |
|------|-------------------|----------------------------|----------------|
| 去重速度 | 即時（微秒） | 慢（需要符號解析） | 中（需要重新執行） |
| 去重精確度 | 低 | 高 | 高 |
| 需要 debug info | 否 | 是 | 是 |
| 能區分同 path 不同 bug | 否 | 是 | 是 |
| 能合并同 bug 不同 path | 否（反而分裂） | 是 | 是 |
| 使用時機 | fuzzing 中，快速過濾新 crash | triage 階段，找獨特 bug | 確認 bug root cause |

---

## 踩雷集錦

**踩雷 1：ASAN crash 幾乎全是 SIGABRT，不是 SIGSEGV**

啟用 ASAN 前，你習慣看 `sig:11`（SIGSEGV）。啟用 ASAN 後，大量 crash 變成 `sig:06`（SIGABRT），因為 ASan 在偵測到錯誤後呼叫 `abort()`。

```bash
# 不要對這個感到困惑
AFL_USE_ASAN=1 afl-clang-fast -o target_asan target.c
afl-fuzz -i seeds/ -o out/ -- ./target_asan @@

ls out/crashes/ | grep 'sig:11'  # 可能很少
ls out/crashes/ | grep 'sig:06'  # 大量出現
# 這是正確行為，ASAN 把更多 bug 轉成可偵測的 SIGABRT
```

解法：對 ASAN session，專注在 `sig:06` crashes，並用 `./target_asan $CRASH 2>&1` 看完整的 ASAN report。

**踩雷 2：crash 數量很多不等於 bug 很多**

AFL++ 跑了一夜，`crashes/` 目錄有 400 個檔案，你可能有 1-5 個真正的 bug。原因：

- 同一個 bug 的不同觸發路徑各自被記錄
- `afl-tmin` 最小化後，多個 crash 可能縮到相同的 testcase
- 同一個記憶體問題（如 heap buffer overflow）在不同偏移量觸發，每次走的 path 略有不同

**解法**：不要直接數 crash 檔案數量，先做 triage（練習 C 有完整流程）。

**踩雷 3：Timeout 不在 `crashes/`，在 `hangs/`**

新手常常找 `crashes/` 找不到 hang，以為 fuzzer 沒有發現它。

```bash
ls out/crashes/   # crash 在這裡
ls out/hangs/     # hang 在這裡，不要忘記看
```

**踩雷 4：Core dump 設定影響重現能力**

```bash
# 確認 core dump 是否啟用
ulimit -c
# 0 表示 core dump 被禁用

# 啟用
ulimit -c unlimited

# 確認 core dump 路徑
cat /proc/sys/kernel/core_pattern
# 如果是 | /usr/share/apport/apport ... 表示 Ubuntu 的 apport 在攔截
# 暫時停用 apport
sudo systemctl stop apport
# 然後 core dump 才會出現在 ./core 或當前目錄
```

**踩雷 5：重現時沒有重現**

crash 在 AFL++ 下穩定出現，但手動重現時不 crash，原因可能有：

1. **ASLR**：AFL++ 關閉了 ASLR（`echo 0 > /proc/sys/kernel/randomize_va_space`），手動重現時 ASLR 是開的，記憶體布局不同。

```bash
# 手動重現時也關 ASLR
echo 0 | sudo tee /proc/sys/kernel/randomize_va_space
./target "$CRASH"
echo 2 | sudo tee /proc/sys/kernel/randomize_va_space   # 還原
# 或
setarch $(uname -m) -R ./target "$CRASH"   # 只對這次執行關 ASLR
```

2. **環境變數**：AFL++ 設定了特定環境變數（如 `AFL_PRELOAD`），手動重現時沒有設定。

3. **工作目錄**：target 依賴相對路徑讀取 config，但你在不同目錄執行。

---

## 進階：再往深一層

**`afl-analyze`：輸入結構分析**

AFL++ 提供了 `afl-analyze` 工具，分析一個 crash testcase 裡哪些 byte 對觸發 crash 是關鍵的：

```bash
afl-analyze -i out/crashes/"id:000000,..." -- ./target @@
```

輸出會標示每個 byte 的「重要性」，幫助你理解 crash 的 root cause。

**`afl-tmin`：縮小 crash testcase**

Crash testcase 通常很大（繼承了 havoc 產生的各種 mutation），`afl-tmin` 把它縮到最小仍然能 crash 的版本：

```bash
afl-tmin -i out/crashes/"id:000000,..." -o minimal_crash -- ./target @@
```

最小化後的 testcase 更容易人工分析，也更容易判斷兩個 crash 是否是同一個 bug（練習 C 會用到）。

**Exploitability 評估：`exploitable` 和 `crashwalk`**

```bash
# GDB plugin: exploitable
# 分析 crash 是否可能被利用
gdb -q ./target
(gdb) source /path/to/exploitable.py
(gdb) run "$CRASH"
(gdb) exploitable

# crashwalk：批量分析所有 crashes 的 exploitability
crashwalk -afl out/crashes/ -- ./target @@
```

`crashwalk` 對每個 crash 跑 GDB + exploitable，輸出 HTML 報告，按 exploitability 排序。這是 triage 後期確定哪些 crash 最值得深入分析的有效工具。

**ASAN + `asan_symbolize`**

ASAN 的 stack trace 預設只有地址，`asan_symbolize` 把它轉成函式名稱：

```bash
./target_asan "$CRASH" 2>&1 | asan_symbolize
# 或
./target_asan "$CRASH" 2>&1 | python3 $(find / -name asan_symbolize.py 2>/dev/null | head -1)
```

---

## 動手練習

**建立一個會產生多種 crash 的 target**

```bash
cat > multi_crash.c << 'EOF'
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <assert.h>

void type_a_path(const char *buf, size_t len) {
    // Path A: heap buffer overflow → SIGSEGV
    char *heap = malloc(8);
    memcpy(heap, buf, len);   // 如果 len > 8，overflow
    free(heap);
}

void type_b_path(const char *buf, size_t len) {
    // Path B: double free → SIGABRT
    char *p = malloc(16);
    memcpy(p, buf, len < 16 ? len : 16);
    free(p);
    if (buf[0] == 'X') {
        free(p);   // double-free
    }
}

void type_c_path(const char *buf, size_t len) {
    // Path C: null deref → SIGSEGV
    int *ptr = NULL;
    if (len > 4 && buf[4] == '\xff') {
        *ptr = 42;   // null deref
    }
}

int main(int argc, char *argv[]) {
    if (argc < 2) return 1;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 1;
    char buf[256];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = '\0';

    if (n < 2) return 0;

    switch (buf[0]) {
        case 'A': type_a_path(buf + 1, n - 1); break;
        case 'B': type_b_path(buf + 1, n - 1); break;
        case 'C': type_c_path(buf + 1, n - 1); break;
    }
    return 0;
}
EOF

afl-clang-fast -o multi_crash multi_crash.c

# 建立 seeds
mkdir -p crash_seeds/
echo -n "A" > crash_seeds/a
echo -n "B" > crash_seeds/b
echo -n "C" > crash_seeds/c
```

**練習 A：收集 crash 並觀察 signal 分布**

```bash
timeout 120 afl-fuzz -i crash_seeds/ -o crash_out/ \
  -- ./multi_crash @@ 2>/dev/null || true

# 觀察 signal 分布
echo "=== Signal distribution ==="
ls crash_out/crashes/ 2>/dev/null | grep -oP 'sig:\K[0-9]+' | sort | uniq -c

echo "=== Total crashes ==="
ls crash_out/crashes/ 2>/dev/null | grep -c 'id:' || echo "0"
```

**練習 B：手動重現並確認 signal**

```bash
# 找一個 SIGSEGV crash
SIGSEGV_CRASH=$(ls crash_out/crashes/ | grep 'sig:11' | head -1)
if [ -n "$SIGSEGV_CRASH" ]; then
    echo "Reproducing SIGSEGV crash: $SIGSEGV_CRASH"
    ./multi_crash "crash_out/crashes/$SIGSEGV_CRASH"
    echo "Exit code: $?"
fi

# 找一個 SIGABRT crash
SIGABRT_CRASH=$(ls crash_out/crashes/ | grep 'sig:06' | head -1)
if [ -n "$SIGABRT_CRASH" ]; then
    echo "Reproducing SIGABRT crash: $SIGABRT_CRASH"
    ./multi_crash "crash_out/crashes/$SIGABRT_CRASH"
    echo "Exit code: $?"
fi
```

**練習 C：用 afl-tmin 縮小 crash**

```bash
# 縮小第一個 crash
FIRST_CRASH=$(ls crash_out/crashes/ | grep 'id:000000' | head -1)
if [ -n "$FIRST_CRASH" ]; then
    afl-tmin -i "crash_out/crashes/$FIRST_CRASH" \
             -o minimal_crash \
             -- ./multi_crash @@
    echo "Original size: $(wc -c < "crash_out/crashes/$FIRST_CRASH") bytes"
    echo "Minimized size: $(wc -c < minimal_crash) bytes"
    xxd minimal_crash
fi
```

---

## 本章重點整理

- 六種主要 crash signal 各有語意：SIGSEGV 是非法記憶體存取、SIGABRT 是主動中止（含 ASan/double-free）、SIGBUS 是 mmap 邊界或 alignment、SIGFPE 是除零、SIGILL 是非法 instruction（含 UBSan）；Timeout 記錄在 `out/hangs/` 而非 `out/crashes/`。
- AFL++ 的 crash uniqueness 基於 edge coverage bitmap，不是 stack trace——同一個 bug 可能因為走不同 path 而被記為多個 crash，不同 bug 可能因為 bitmap 相同而被合并；crash 數量和 bug 數量沒有直接關係。
- `out/crashes/` 的檔名編碼了 signal number（`sig:`）、來源 seed（`src:`）、mutation 操作（`op:`）；重現 crash 時要注意 ASLR 設定；ASAN 啟用後幾乎所有 crash 都是 SIGABRT，這是正常的。

---

## 自我檢核

1. 啟用 ASan 後，原本顯示 `sig:11`（SIGSEGV）的 heap buffer overflow，為什麼可能變成 `sig:06`（SIGABRT）？
2. AFL++ 用 bitmap-based 去重，而不是 stack-hash-based。這導致什麼誤報和漏報？各舉一個具體情境。
3. 你有 200 個 crash 檔案，想快速了解有哪些不同的 signal 類型。寫一條 shell 指令完成這個統計。
4. 為什麼 crash 在 AFL++ 下穩定出現，但手動重現時卻不 crash？列出兩個可能原因和對應的解法。
5. `out/hangs/` 和 `out/crashes/` 的本質差異是什麼？一個無窮迴圈會出現在哪裡？

---

## 延伸閱讀

**AFL++ `docs/triaging_crashes.md`**
核心貢獻：官方 triage 流程指南，涵蓋 `afl-tmin`、GDB 整合、ASAN 分析的標準步驟，以及如何判斷 crash 是否為 unique bug。
讀哪裡：`AFL++ 原始碼目錄/docs/triaging_crashes.md`，或 GitHub 上的最新版。
和本章關聯：本章的理論基礎，練習 C 的實作框架直接來自這份文件。

**"SoK: Sanitizing for Security"（Szekeres et al., IEEE S&P 2019）**
核心貢獻：系統性地分類各種 memory safety 問題（spatial/temporal）與對應的 sanitizer（ASan/MSan/UBSan/SafeStack），解釋了為什麼不同 bug 會觸發不同 signal。
讀哪裡：IEEE DL 或 作者個人頁面的 preprint。
和本章關聯：理解「為什麼 ASan 把 heap-overflow 轉成 SIGABRT 而不是 SIGSEGV」的完整答案就在這篇。

**`exploitable` GDB Plugin（jfoote/exploitable）**
核心貢獻：GDB plugin，對一個 crash 的 exploitability（EXPLOITABLE / PROBABLY\_EXPLOITABLE / UNKNOWN / PROBABLY\_NOT\_EXPLOITABLE）做自動分類，基於 register 值、instruction 類型、crash 地址模式。
讀哪裡：https://github.com/jfoote/exploitable
和本章關聯：triage 後期決定「哪個 crash 值得花時間寫 PoC」時的關鍵工具。

**AFL++ `src/afl-fuzz-run.c`**
核心貢獻：`handle_stop_signal()`、`has_new_bits()` 等函式，展示了 AFL++ 如何從 `waitpid()` 的 exit status 提取 signal number，以及如何比對 crash bitmap 判斷 uniqueness。
讀哪裡：AFL++ 原始碼，搜尋 `WTERMSIG` 和 `save_if_interesting`。
和本章關聯：「底層機制」一節的原始碼依據，能直接看到 bitmap 比對的實作。

---

→ [練習 C — Corpus 最小化 + Crash Triage 完整流程](practice-c-corpus-triage.md)
