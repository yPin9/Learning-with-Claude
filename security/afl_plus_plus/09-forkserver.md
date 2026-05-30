# Ch 9 — Forkserver：為什麼 10× 速度提升不是魔法

> **目標**：能解釋 forkserver 為什麼能把 throughput 提升 10–100×；能畫出 fuzzer / forkserver / child 三者的 pipe 通訊；理解 deferred forkserver 的應用場景。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

## 為什麼需要這個？

2014 年 AFL 問世之前，coverage-guided fuzzer 的標準操作是：產生一個 input → `fork()` + `execve()` 跑 target → 等它結束 → 收集結果 → 重複。這個流程笨，但直觀。

問題出在 `execve()`。每次 `execve()` 都要：

1. 核心載入 ELF，解析 segment，設置 stack
2. 動態連結器（`ld.so`）解析所有 shared library，執行 relocation
3. C runtime（`libc`）執行 `.init_array` 裡的建構子
4. target 的 `main()` 才真正開始

這些步驟在「小 target」上可能只花 1–5ms，但對 100 個 library dependency 的真實程式，startup 輕鬆超過 50ms。AFL 的 fuzzing 速度上限就卡在這裡：每個 execution 都要繳這筆 startup 稅，不管你的 mutation 有多快。

AFL 的 forkserver 解決了這個問題，核心 insight 只有一句話：

> **`execve()` 只需要做一次；之後用 `fork()` 複製乾淨的 process state。**

`fork()` 複製 process state（page table、fd、heap、BSS...）遠比 `execve()` 重新載入一切快，通常只需要幾十微秒。這是 forkserver 的全部秘密。

---

## 先建立直覺

把 forkserver 想成一個**等待命令的服務員**，站在廚房門口（startup 完成後的那個點）：

- 廚房備料完成（dynamic linking + libc init + 昂貴的初始化）已經做完了。
- 每次 fuzzer 要一個 execution，服務員就複製自己（`fork()`），複製出來的那個人去處理 input，跑完就消失。
- 服務員本身永遠不處理 input，只負責複製自己。

這樣廚房備料的時間從「每次 execution 都付一次」變成「只付一次」。

一個進一步的最佳化（deferred forkserver）讓服務員可以站在廚房更裡面的位置——甚至在 target 的 application-level 初始化完成之後才「出現」，進一步減少每次 execution 需要重做的工作。

---

## 三方通訊架構

forkserver 不是一個獨立的 process 或 daemon。它是 **target 程式本身**在 startup 完成後進入的一個 loop——同一個 ELF image，只是執行流走到了一段「等待 fuzzer 命令，然後 fork」的程式碼。

三個角色：

| 角色 | 身份 | 職責 |
|------|------|------|
| **afl-fuzz**（parent）| fuzzer 主程序 | 產生 mutation input，讀取 child 的 status，更新 bitmap |
| **forkserver** | target 的 parent loop | 收到命令後 `fork()`，回報 child PID，等待 child 結束後回報 exit status |
| **child** | fork 出來的執行個體 | 真正執行 target 邏輯，處理 mutation input，結束後消失 |

兩條 pipe（fd 號碼是 AFL 約定的固定值）：

- **fd 198**：控制管道（control pipe），方向：afl-fuzz → forkserver
  - fuzzer 寫 4 bytes 到這個 fd，告訴 forkserver「現在 fork 一個 child」
- **fd 199**：狀態管道（status pipe），方向：forkserver → afl-fuzz
  - forkserver 把 child 的 PID（4 bytes）和最後的 wait status（4 bytes）寫回給 fuzzer

---

## 底層機制：三方通訊的完整流程

```
afl-fuzz                 forkserver              child
   │                        │                      │
   │  execve(target)         │                      │
   ├────────────────────────→│                      │
   │                         │ target 啟動           │
   │                         │ dynamic linking       │
   │                         │ libc init             │
   │                         │ .init_array 建構子    │
   │                         │                      │
   │                   ┌─────┤ __afl_start_forkserver() 被呼叫
   │                   │     │                      │
   │ ◄── 握手：fd 199 ──┤     │ 寫 4 bytes 到 fd 199  │
   │   "\x00\x00\x00\x00"    │ （表示 forkserver 就緒）│
   │                   └─────┤                      │
   │                         │                      │
   │  ── Fuzzing Loop ──────────────────────────── ──│
   │                         │                      │
   │ 準備 mutation input       │                      │
   │                         │                      │
   │ 寫 4 bytes 到 fd 198 ────→│                      │
   │   （任意值，作為觸發信號）  │ fork()               │
   │                         ├─────────────────────→│
   │                         │                      │ 繼承 forkserver
   │ ◄── child PID（4B）──────┤                      │ 的記憶體狀態
   │   fd 199                │                      │
   │                         │ waitpid(child)       │ 讀取 mutation input
   │                         │                      │ 執行 target 邏輯
   │                         │                      │ 更新 bitmap（shm）
   │                         │                      │ exit(status)
   │                         │ ◄── child 結束 ───────┤
   │ ◄── exit status（4B）───┤                      │
   │   fd 199                │                      │
   │                         │                      │
   │ 讀取 bitmap（shm）        │                      │
   │ 決定 interesting?         │                      │
   │ 下一個 mutation...        │                      │
   │                         │                      │
   └─────────── 重複 ─────────┘                      │
```

### Bitmap 共享記憶體

bitmap 不是透過 pipe 傳輸的，而是透過 **POSIX shared memory**（`shmget()` / `shmat()`）：

- afl-fuzz 在啟動前建立一塊 64KB 的 shm，把 shm ID 透過環境變數 `__AFL_SHM_ID` 傳給 target。
- forkserver 在初始化時 `shmat()` attach 這塊 shm，得到一個指標（通常叫 `afl_area_ptr`）。
- child 繼承了這個 shm 映射，所以 child 執行時直接寫入 `afl_area_ptr`。
- child 結束後，afl-fuzz 讀取 shm 的內容，判斷這次 execution 的 coverage。

這個設計的好處：bitmap 更新不需要任何 IPC 系統呼叫（write/read pipe），直接寫記憶體，overhead 最低。

---

## Forkserver 協定：byte-level 說明

### 握手（Handshake）

target 的 `__afl_start_forkserver()` 被呼叫時，寫 **4 bytes 的零值** 到 fd 199：

```
fd 199: [0x00][0x00][0x00][0x00]
```

afl-fuzz 等待這 4 bytes 確認 forkserver 就緒。如果在 timeout 內沒收到，afl-fuzz 輸出 `Fork server handshake failed` 並中止。

AFL++ 4.x 擴展了握手協定，forkserver 可以在握手時回報自己支援的功能位元（capabilities），例如是否支援 `__AFL_PERSISTENT_MODE`、`__AFL_DEFER_FORKSVR` 等。這些功能位元在 `FORKSRV_FD + 1`（fd 199）的握手訊息裡用最高位來表示。

### 每次 Iteration

1. afl-fuzz 寫任意 4 bytes 到 fd 198（值不重要，只是觸發信號）。
2. forkserver 呼叫 `fork()`，得到 child PID。
3. forkserver 把 child PID（32-bit int）寫到 fd 199。
4. afl-fuzz 讀取 child PID，記錄下來（用於 `kill()` 如果 timeout）。
5. child 執行 target 邏輯，結束。
6. forkserver 的 `waitpid()` 返回，得到 exit status。
7. forkserver 把 exit status（32-bit int）寫到 fd 199。
8. afl-fuzz 讀取 exit status，判斷 crash / timeout / normal。

整個協定的 IPC 成本：每次 iteration 只有 2 次 4-byte write 和 2 次 4-byte read（各走一條 pipe），overhead 極低。

### 原始碼參照

```
src/afl-fuzz-init.c
  └─ start_forkserver()         # afl-fuzz 端：設置 pipe，spawn target

instrumentation/afl-compiler-rt.o.c
  ├─ __afl_start_forkserver()   # target 端：forkserver loop 的實作
  └─ __afl_map_shm()            # attach bitmap shm
```

`__afl_start_forkserver()` 的核心迴圈（簡化版）：

```c
/* instrumentation/afl-compiler-rt.o.c（簡化） */
void __afl_start_forkserver(void) {
    static u8 tmp[4];

    /* 握手：告訴 fuzzer forkserver 就緒 */
    memset(tmp, 0, 4);
    write(FORKSRV_FD + 1, tmp, 4);  /* fd 199 */

    while (1) {
        pid_t child_pid;
        int   child_status;

        /* 等待 fuzzer 的觸發信號 */
        if (read(FORKSRV_FD, tmp, 4) != 4) break;  /* fd 198 */

        /* fork 出 child */
        child_pid = fork();

        if (child_pid == 0) {
            /* child：關閉 forkserver pipe，繼續執行 target 邏輯 */
            close(FORKSRV_FD);
            close(FORKSRV_FD + 1);
            return;  /* 回到 main() 或呼叫者 */
        }

        /* forkserver：回報 child PID */
        write(FORKSRV_FD + 1, &child_pid, 4);

        /* 等待 child 結束 */
        waitpid(child_pid, &child_status, 0);

        /* 回報 exit status */
        write(FORKSRV_FD + 1, &child_status, 4);
    }

    exit(1);
}
```

---

## 範例一：Deferred Forkserver

預設情況下，forkserver 在 target 的 `.init_array` 建構子全部執行完後立刻啟動——也就是在 `main()` 開始之前。這樣 `main()` 裡的程式碼每次 execution 都要重跑。

如果 `main()` 有昂貴的初始化（讀大型設定檔、建立 lookup table、連接資料庫），每次 fork 出來的 child 都要重做這些工作，浪費了 forkserver 的優化效果。

**Deferred forkserver** 讓你把 fork 點挪到昂貴初始化**之後**：

```c
#include <unistd.h>

int main(int argc, char **argv) {
    /* 這些只做一次，之後每個 fork 出來的 child 都繼承結果 */
    load_config("config.json");       /* 讀 2MB JSON 設定檔 */
    load_dictionary("dict.bin");      /* 建立 256MB lookup table */
    init_regex_engine();              /* 編譯 10000 條 regex pattern */

    __AFL_INIT();   /* forkserver 在這裡啟動，fork 點就在此 */

    /* 每次 iteration 才執行以下邏輯 */
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 1;
    parse_input(f);
    fclose(f);
    return 0;
}
```

`__AFL_INIT()` 展開後就是一個條件式的 `__afl_start_forkserver()` 呼叫——在沒有 AFL++ 環境時（shm ID 不存在），它是個 no-op，不影響正常執行。

**效能影響**：如果 `load_dictionary()` 花了 200ms，在沒有 deferred forkserver 的情況下每次 execution 都要付 200ms，fuzzing 速度上限約 5 execs/sec。加上 `__AFL_INIT()` 後，200ms 只付一次，速度可能跳到 5000 execs/sec（受限於 parsing 本身的速度）。

---

## 效能階梯

```
最慢                                               最快
  │                                                  │
  ▼                                                  ▼

fork + execve        forkserver        deferred          persistent
（每次都重載）       （省 execve）    （省 execve          mode
                                      + 昂貴 init）    （省 fork 本身）

   ~50ms/exec         ~0.5ms/exec      ~0.05ms/exec      ~0.001ms/exec
   (20 exec/s)       (2000 exec/s)   (20000 exec/s)    (100000+ exec/s)
```

每一層的省略：

| 模式 | 每次省略的工作 |
|------|--------------|
| forkserver | `execve()`、dynamic linking、libc init |
| deferred forkserver | 上面全部 + target 的 application-level 初始化 |
| persistent mode（Ch 16）| 上面全部 + `fork()` 系統呼叫本身 |

---

## 範例二：驗證 Forkserver 是否生效

```bash
# 啟用 debug 輸出，過濾 forkserver 相關訊息
AFL_DEBUG=1 afl-fuzz -i seeds/ -o out/ -- ./target @@ 2>&1 | grep -i fork

# 如果 forkserver 正常啟動，會看到類似：
# [*] Spinning up the fork server...
# [+] All right - fork server is up.

# 如果沒看到上面這行，或看到：
# [-] PROGRAM ABORT : Fork server handshake failed
# 代表 forkserver 沒有啟動（target 可能沒有插樁，或 pipe 設置失敗）
```

確認 forkserver 的 fd 設置：

```bash
# 讓 target 在 exec 時印出 fd 清單
AFL_DEBUG=1 ./target seed_file 2>&1 | head -30
# 注意輸出裡有沒有 "afl_start_forkserver" 字樣
# 或者用 strace 確認 pipe fd 的存在
strace -f -e trace=write,read afl-fuzz -i seeds/ -o out/ -- ./target @@ 2>&1 | grep "fd=19[89]"
```

---

## 對比與取捨

| 面向 | fork + execve | forkserver | deferred forkserver | persistent mode |
|------|--------------|------------|---------------------|-----------------|
| exec 速度 | 最慢 | 快 | 更快 | 最快 |
| 設定複雜度 | 零（不需要插樁） | 自動（插樁後自動啟用） | 低（加一行 `__AFL_INIT()`） | 中（`__AFL_LOOP()` + 狀態清理） |
| State isolation | 完整（每次全新 process） | 完整（每次 fork 都是乾淨 state） | 完整（fork 點之後的 state 乾淨） | 需要手動清理 state（Ch 16 深入） |
| 適合場景 | dumb fuzzing / `-n` 模式 | 幾乎所有情況 | target 有昂貴初始化 | 極速 in-process fuzzing |
| Child 繼承什麼 | 無（全新 process） | fork 點時的完整 memory | `__AFL_INIT()` 呼叫時的 memory | 無額外 fork |

---

## 踩雷集錦

**1. 「forkserver 是獨立 process」的誤解**

forkserver 不是一個額外的 daemon 或 helper。它是 target binary 本身，只是在 startup 完成後進入了等待命令的迴圈。執行 `ps aux` 時你看到的就是 target 的 process，沒有額外的 `afl-forkserver` process。

這個誤解會導致你在 debug 時往錯誤方向找問題（例如去找不存在的 afl-forkserver binary）。

**2. Child 繼承了什麼，不繼承什麼**

Child 繼承了 forkserver 在 `fork()` 那一刻的**完整記憶體 state**（heap、stack 內容、開啟的 fd、mmap 的區域）。但有幾件事不繼承或行為不同：

- **`fork()` 之後的隨機性**：`/dev/urandom` 的 fd 是繼承的，但 glibc 的 `rand()` seed 會在 fork 後不同步（各自進化）。如果 target 用 `rand()` 做路徑選擇，每個 child 拿到的序列會不同。
- **copy-on-write（CoW）**：fork 後，page table 是共享的（CoW），只有實際被寫入時才複製。大部分 read-only 的 text/data 段不會產生 copy，overhead 低。
- **不繼承 parent 在 `fork()` 之後的寫入**：afl-fuzz 寫入 mutation input 到 stdin/file，這個動作發生在 `fork()` 之後，child 透過讀檔/stdin 拿到 input，不是透過 shared memory。

**3. Persistent mode 才有 state leak 問題，forkserver 沒有**

Persistent mode（Ch 16）在同一個 process 裡跑多次 iteration，不同次 iteration 之間 state 可能洩漏（例如 malloc 的記憶體沒有 free，或 global variable 沒有 reset）。

Forkserver 不有這個問題：每個 iteration 是一個獨立的 child process，child 結束時 OS 釋放所有資源，下一次 iteration 是全新的 fork，state 完全乾淨。

**4. `__AFL_INIT()` 只能有效呼叫一次**

如果你在程式裡呼叫 `__AFL_INIT()` 兩次，第二次是 no-op（用 static variable 保護）。這個行為是正確的，不是 bug，但如果你試圖用兩個 `__AFL_INIT()` 來「分段初始化」，不會有任何效果。

```c
__AFL_INIT();   /* 第一次：forkserver 啟動 */
do_more_init(); /* 這段每個 child 都要跑 */
__AFL_INIT();   /* 第二次：no-op，不會再 fork */
```

**5. FORKSRV_FD 衝突**

forkserver 固定使用 fd 198 和 fd 199。如果 target 在啟動時恰好打開了大量 fd，導致 fd 198 或 199 被 target 自己佔用，forkserver 會靜默失敗（write/read 到錯誤的 fd）。

診斷方式：`AFL_DEBUG=1` + `strace`，確認 pipe fd 是否被 target 覆蓋。修法：在 target 啟動最早期關閉多餘的 fd，或者用 `AFL_FORKSRV_FD` 環境變數指定一個空閒的 fd 對。

---

## 進階：再往深一層

### Snapshot Fuzzing（SnapFuzz / AFL-Snapshot-LKM）

Deferred forkserver 把 fork 點挪到 application init 之後，但 `fork()` 系統呼叫本身仍然存在，每次 execution 還是要付 `fork()` 的 cost（通常 50–100μs）。

Snapshot fuzzing 的想法更激進：不用 `fork()`，而是用 kernel-level 的 snapshot 機制（類似 CRIU），在特定點對 process state 拍快照，每次 execution 直接恢復快照而不是 fork。

AFL-Snapshot-LKM 是 AFL++ 的一個 kernel module extension，讓 target 可以在 `__AFL_INIT()` 點拍 snapshot，之後每次 iteration 透過 LKM 提供的 `restore_snapshot()` 回到快照點，比 `fork()` 快 2–5×。

```bash
# 需要編譯並載入 kernel module
cd utils/aflpp_driver/
make
sudo insmod ./snapshot.ko

# 然後正常啟動 afl-fuzz，target 會自動偵測 snapshot module 是否存在
afl-fuzz -i seeds/ -o out/ -- ./target @@
```

### 範例三：手動追蹤 Forkserver 協定

用 `strace` 觀察 afl-fuzz 和 forkserver 之間的 pipe 通訊：

```bash
# 開啟 strace，只追蹤 write/read，過濾 fd 198/199
strace -f -e trace=read,write,fork,waitpid \
    afl-fuzz -i seeds/ -o out/ -- ./target @@ 2>&1 \
    | grep -E "fd=(198|199)|SIGCHLD|waitpid"

# 期望看到的模式：
# write(198, "\x00\x00\x00\x00", 4)       <- fuzzer 觸發 fork
# read(199, "\x12\x34\x00\x00", 4)        <- forkserver 回報 child PID
# ... (child 執行)
# read(199, "\x00\x00\x00\x00", 4)        <- forkserver 回報 exit status 0
```

這個 trace 讓你看到每次 iteration 的 pipe 操作頻率，驗證 forkserver 是否正常工作，也讓你量化 IPC overhead（每次 iteration 的 pipe 操作時間）。

### 解讀 afl-fuzz 原始碼

`src/afl-fuzz-init.c` 的 `start_forkserver()` 做了以下事情（對應上面的協定說明）：

```
1. pipe(st_pipe)       # 建立 fd 198/199
2. fork()              # spawn forkserver process（即 target）
3. dup2() 到 fd 198/199 # 讓 target 繼承正確的 fd
4. execve(target)      # target 啟動，走到 __afl_start_forkserver()
5. read(st_pipe[0], 4) # 等待握手
```

`instrumentation/afl-compiler-rt.o.c` 的 `__afl_start_forkserver()` 在 compile-time instrumentation 的情況下被插入到 `.init_array`，確保它在 `main()` 之前被呼叫。在 deferred 模式（`__AFL_INIT()` 巨集）下，它被插入到使用者指定的位置。

---

## 動手練習

1. 寫一個有昂貴初始化的 C 程式（例如在 main 開頭建立一個 10MB 的 lookup table），分別編譯出「沒有 `__AFL_INIT()`」和「有 `__AFL_INIT()` 放在 lookup table 建立後」兩個版本，各跑 2 分鐘，比較 execs/sec。

2. 用 `AFL_DEBUG=1` 啟動一個正常的 fuzz session，找到 afl-fuzz 輸出裡確認 forkserver 就緒的那一行。

3. 故意破壞 forkserver：對一個 **未插樁** 的 binary 用 `afl-fuzz`（不加 `-Q`），觀察 `Fork server handshake failed` 錯誤訊息，讀懂錯誤原因。

4. 用 `strace -f -e trace=fork,write,read` 追蹤一個 fuzz session 的前 10 次 iteration，數一數每次 iteration 有幾次 `write` 和 `read` 發生在 fd 198/199。

5. 閱讀 `instrumentation/afl-compiler-rt.o.c`，找到 `__afl_start_forkserver()` 函式，對照本章的 ASCII 圖確認流程。

---

## 本章重點整理

- **Forkserver 的核心 insight**：`execve()` 只做一次，之後用 `fork()` 複製乾淨的 process state，省掉 dynamic linking + libc init 的反覆代價，throughput 提升 10–100×。
- **三方通訊**用兩條固定 fd（198/199）的 pipe 協調：fuzzer 寫觸發信號，forkserver 回報 child PID 和 exit status，bitmap 透過 shm 共享，不走 pipe。
- **Deferred forkserver**（`__AFL_INIT()`）把 fork 點挪到 application-level 昂貴初始化之後，讓這些初始化也只做一次；效能階梯：bare fork+exec < forkserver < deferred < persistent mode。

---

## 自我檢核

1. 為什麼 forkserver 能比 fork+exec 快 10–100×？省掉的是哪些具體步驟？

2. fd 198 和 fd 199 各自傳輸什麼內容？方向（誰寫、誰讀）各是什麼？

3. bitmap 是透過 pipe 還是 shm 在 afl-fuzz 和 child 之間共享的？為什麼選這個機制？

4. `__AFL_INIT()` 放在 `main()` 最開頭和放在昂貴初始化之後，各有什麼效果？什麼時候應該延後放？

5. forkserver 模式下，不同次 iteration 的 child 之間 state 是否完全隔離？和 persistent mode 的差異是什麼？

6. 如果 target binary 在 fd 199 的位置已經打開了一個無關的 fd，forkserver 會出什麼問題？

---

## 延伸閱讀

**「AFL technical details」（lcamtuf）**（https://lcamtuf.coredump.cx/afl/technical_details.txt）
- 核心貢獻：AFL 作者親筆的設計文件，「The fork server」節解釋了 forkserver 的設計動機和 byte-level 協定
- 讀哪裡：搜尋「THE FORK SERVER」那一段（約 80 行），這是本章的第一手來源
- 和本章關聯：AFL++ 的 forkserver 協定和 AFL 原版幾乎相同，這份文件的說明直接適用

**AFL++ `docs/env_variables.md`**（AFL++ repo 內）
- 核心貢獻：所有影響 forkserver 行為的環境變數完整列表，包含 `AFL_NO_FORKSRV`、`AFL_FORKSRV_FD`、`AFL_DEFER_FORKSRV` 的語意
- 讀哪裡：搜尋 `FORKSRV` 關鍵字，約 15 個相關條目
- 和本章關聯：本章只涵蓋核心機制，env_variables.md 有 edge case 和高階設定

**「SnapFuzz: An Efficient Fuzzing Framework for Network Applications」（ISSTA 2022）**
- 核心貢獻：把 forkserver 的概念推進到 kernel-level snapshot，解釋了 fork() 本身的 cost 以及 snapshot 的取代策略
- 讀哪裡：Section 3「Design」，特別是 3.1「Process State Restoration」
- 和本章關聯：本章的效能階梯最後一步（snapshot fuzzing）的學術背景

**AFL++ `instrumentation/afl-compiler-rt.o.c`**（原始碼）
- 核心貢獻：forkserver 的 target 端實作，`__afl_start_forkserver()` 的完整程式碼
- 讀哪裡：直接從 AFL++ repo checkout 後用 editor 開，搜尋 `__afl_start_forkserver`，約 150 行
- 和本章關聯：本章的 ASCII 圖和程式碼簡化版都基於這份原始碼，對照閱讀可以確認每個細節

→ [Practice B — Instrumentation 模式對比實驗](./practice-b-instrumentation-comparison.md)
