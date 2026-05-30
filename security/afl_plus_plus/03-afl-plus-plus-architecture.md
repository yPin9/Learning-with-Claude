# Ch 3 — AFL++ 架構總覽：元件、IPC、主 loop

> **目標**：能畫出 AFL++ 的完整架構圖，說清楚各元件的職責與互動。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

## 為什麼需要看架構？

很多人裝好 AFL++ 之後把它當黑盒用——`afl-fuzz -i in -o out ./target @@`，然後盯著 status screen 等 crash。這在簡單場景下夠用，但遇到問題時完全沒有診斷能力：

- 為什麼 `exec speed` 突然掉了一半？（可能是 forkserver 握手失敗，fallback 到 execve）
- 為什麼 `map density` 一直是 0.1%？（可能是 target 沒有被正確插樁）
- 為什麼多個 AFL++ 實例之間的 corpus 沒有同步？（filesystem 同步的時機問題）

這些問題都需要你知道「AFL++ 的哪個部分負責什麼、它們用什麼方式溝通」才能回答。

## 先建立直覺

AFL++ 的架構可以從三個視角理解：

**視角一：程序層面**
- 兩個程序（process）：`afl-fuzz`（主 fuzzer）和 `target`（被測程式）
- 它們之間用共享記憶體傳 bitmap，用管道傳控制信號

**視角二：工具鏈層面**
- `afl-cc` / `afl-clang-fast` 在編譯時把插樁邏輯插入 target
- 這些邏輯在 target 執行時更新 bitmap，並實作 forkserver 協議

**視角三：資料流層面**
- Input（測試用例）從 fuzzer 流向 target（透過檔案或 stdin）
- Coverage（bitmap diff）從 target 流向 fuzzer（透過共享記憶體）
- 控制信號從 fuzzer 流向 target（透過管道）
- 狀態（stats、crash、queue）從 fuzzer 寫到 filesystem

## 三大元件

### 元件一：afl-fuzz

**職責**：整個 fuzzing loop 的大腦——決定跑哪個 seed、怎麼 mutate、是否保留新的測試用例。

主 loop 的高層流程：

```
初始化
  │
  ├── 設定共享記憶體（SHM bitmap）
  ├── 啟動 target（forkserver 握手）
  └── 載入初始 corpus

主 loop（無限迴圈）
  │
  ▼
pick seed（從 queue 裡選一個，根據 power schedule）
  │
  ▼
mutate（deterministic 階段 → havoc 階段 → splice 階段）
  │
  ▼
execute（透過 forkserver 讓 target 跑這個測試用例）
  │
  ▼
check bitmap（這次執行有沒有觸發新的邊？）
  │
  ├─[有新邊]──→ save to queue，更新 schedule
  │
  └─[沒有]────→ discard，繼續下一個 mutation
```

主 loop 的程式碼入口在 `src/afl-fuzz.c` 的 `main()`，實際 loop 邏輯在 `fuzz_one()` 函式（定義在 `src/afl-fuzz-mutators.c`）。

### 元件二：afl-cc / afl-clang-fast

**職責**：compiler wrapper，在編譯 target 時把兩件事插入 binary：
1. **Coverage instrumentation**：在每條邊（基本塊間的跳轉）插入 bitmap 更新程式碼
2. **Forkserver 邏輯**：在 `main()` 之前插入 forkserver 的初始化和協議處理程式碼

`afl-clang-fast` 是現在的主力 wrapper，基於 LLVM 的 SanitizerCoverage 框架：

```bash
# 這個指令做的不只是「編譯」
afl-clang-fast -O2 -o target target.c

# 等價於（簡化）：
clang -O2 \
  -fsanitize-coverage=trace-pc-guard \    # Coverage instrumentation
  -fno-omit-frame-pointer \               # 方便 debug
  /path/to/afl-compiler-rt.o \           # Forkserver + bitmap update runtime
  target.c -o target
```

插樁後的 target binary 內部包含：
- `__afl_area_ptr`：指向 bitmap 的指標（在執行時指向 fuzzer 建立的 SHM）
- `__afl_forkserver_start`：forkserver 初始化函式
- 每條邊的 `__afl_trace_pc_guard` 呼叫（bitmap byte ++）

### 元件三：afl-forkserver

**職責**：forkserver 不是獨立程序，而是**嵌入在 target binary 內部的一段程式碼**（由 `afl-compiler-rt.o.c` 提供）。它在 `main()` 之前執行，負責：

1. 和 afl-fuzz 建立通訊（透過預先設定的 fd）
2. 等待 afl-fuzz 的指令
3. 收到指令後 `fork()` 出 child process
4. Child 執行一個測試用例然後退出
5. Forkserver（parent）把 child 的退出狀態回報給 afl-fuzz
6. 回到步驟 2，等下一個指令

關鍵點：**forkserver 在 child 裡面並不再次執行**——`fork()` 後 child 直接從 `main()` 開始往下跑，不再進入 forkserver loop。這樣 dynamic linker 的初始化成本（載入 .so、執行 constructors）只付一次。

## 四個 IPC 機制

```
afl-fuzz process                          target process（含 forkserver）
     │                                            │
     │  ①  共享記憶體（SHM）bitmap                │
     │◄──────────────────────────────────────────►│
     │      64KB，target 寫，fuzzer 讀             │
     │                                            │
     │  ②  管道（Pipe）— forkserver protocol      │
     │◄──────────────────────────────────────────►│
     │      fd 198（fuzzer→target），              │
     │      fd 199（target→fuzzer）               │
     │                                            │
     │  ③  filesystem — Status / Queue / Crash    │
     │      out/fuzzer_stats（每秒更新）            │
     │      out/queue/（新 seed）                  │
     │      out/crashes/（crash input）            │
     │                                            │
     │  ④  Signal                                 │
     │──────────────────────────────────────────► │
     │      SIGKILL（timeout 時送給 child）         │
```

### IPC ①：Shared Memory（共享記憶體）bitmap

**建立方式**：afl-fuzz 在啟動時呼叫 `shm_open()` 或 `shmget()` 建立 65536 bytes（64KB）的共享記憶體區段。

**Target 怎麼找到 SHM**：afl-fuzz 把 SHM 的 ID 寫入環境變數 `__AFL_SHM_ID`，target process 在啟動時（forkserver 初始化時）讀取這個環境變數，呼叫 `shmat()` 把 SHM attach 到自己的 address space。

**資料流向**：
- Target 寫：每次執行一條邊時，`bitmap[edge_id]++`
- Fuzzer 讀：執行結束後，和之前的 bitmap 做 XOR，看有沒有新的邊

```c
// afl-compiler-rt.o.c（簡化）
// 這段程式碼在每條邊執行時被呼叫
void __afl_trace(uint32_t x) {
    // x 是 compile time 指定的 edge ID（亂數）
    // __afl_area_ptr 指向 SHM
    __afl_area_ptr[x ^ __afl_prev_loc]++;
    __afl_prev_loc = x >> 1;
    // x ^ prev_loc 就是 edge 的 hash，把「從哪來、到哪去」壓縮成一個數字
}
```

注意 `x ^ __afl_prev_loc` 的設計：單純記錄「基本塊被執行」不夠，AFL 想記錄的是**邊**（從 A 跳到 B）。把當前 block ID 和前一個 block ID XOR 在一起，就能用一個 byte 代表一條邊。

### IPC ②：Pipe — Forkserver Protocol

forkserver 用兩個管道和 afl-fuzz 通訊，固定使用 fd 198（fuzzer 寫，target 讀）和 fd 199（target 寫，fuzzer 讀）。

協議非常簡單：

```
afl-fuzz                          forkserver（target 內部）
   │                                     │
   │  ── write(198, "\x00\x00\x00\x00") ──►│   「準備 fork 一個 child」
   │                                     │
   │                                     │  fork()
   │                                     │   │
   │                                     │   └─ child：執行測試用例，退出
   │                                     │
   │◄── write(199, child_pid, 4) ─────── │   「child 的 PID 是這個」
   │                                     │
   │           （等 child 結束）           │
   │                                     │
   │◄── write(199, exit_status, 4) ───── │   「child 的退出狀態」
   │                                     │
   └──────── 繼續下一個測試用例 ──────────►│
```

退出狀態如果是 crash（signal），afl-fuzz 會把這個測試用例存進 `out/crashes/`。

### IPC ③：Filesystem

- `out/fuzzer_stats`：純文字，afl-fuzz 每秒更新，`afl-whatsup` 讀取這個檔案
- `out/queue/`：每個「有新 coverage 的」測試用例存在這裡
- `out/crashes/`：導致 crash 的測試用例
- `out/hangs/`：導致 timeout 的測試用例
- `out/.synced/`：多實例同步用的目錄（記錄哪些 seed 已經從其他 instance 同步過來）

多個 afl-fuzz 實例之間的 corpus 同步完全透過 filesystem：每個 instance 定期掃描其他 instance 的 queue 目錄，把沒見過的 seed 拷貝過來。這是一個**刻意的設計選擇**——不用 SHM 做多實例同步，因為 filesystem 天然提供持久化和崩潰恢復。

### IPC ④：Signal

afl-fuzz 用 `SIGKILL` 處理 timeout：設一個 timer，如果 child 在 timeout 時間內沒有結束，afl-fuzz 送 `SIGKILL` 給 child，然後把這個測試用例記錄為 hang。

## 完整架構圖

```
┌─────────────────────────────────────────────────────────────────┐
│                        afl-fuzz process                          │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌────────────────────┐ │
│  │  Corpus Mgr  │    │ Mutation Eng │    │  Schedule Engine   │ │
│  │              │    │              │    │                    │ │
│  │ queue/       │◄──►│ deterministic│◄──►│ power schedule:    │ │
│  │ crashes/     │    │ havoc        │    │ fast/explore/mmopt │ │
│  │ hangs/       │    │ splice       │    │                    │ │
│  └──────┬───────┘    └──────┬───────┘    └────────────────────┘ │
│         │                  │                                     │
│         ▼                  ▼                                     │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    Main Fuzzer Loop                        │  │
│  │  pick_seed → mutate → execute → check_bitmap → save?      │  │
│  └───────────────────────┬───────────────────────────────────┘  │
│                          │                                       │
└──────────────────────────┼───────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────────┐
        │  IPC 機制         │                      │
        │                  ▼                      │
        │  ┌─────────────────────┐                │
        │  │   Pipe fd 198/199   │                │
        │  │   forkserver 協議   │                │
        │  └──────────┬──────────┘                │
        │             │                            │
        │  ┌──────────▼──────────────────────────┐│
        │  │     SHM bitmap（64KB）               ││
        │  │     __AFL_SHM_ID env var             ││
        │  └──────────────────────────────────────┘│
        └───────────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                      target process                              │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                  forkserver（嵌入的程式碼）                  │ │
│  │                                                            │ │
│  │  attach SHM → wait on fd198 → fork() → report child pid   │ │
│  │  → wait child exit → report exit status → loop            │ │
│  └──────────────────────────────────────────────────────────── │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              coverage instrumentation                       │ │
│  │                                                            │ │
│  │  每條 edge：__afl_trace(edge_id)                           │ │
│  │    → bitmap[edge_id ^ prev_loc]++                         │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  [application code: main() → parse input → do stuff → exit]     │
└─────────────────────────────────────────────────────────────────┘
```

## 各 Mode 的架構差異

### Source Instrumentation（LLVM mode，預設）

```
編譯時：
afl-clang-fast target.c
    │
    ▼
clang + LLVM pass（SanitizerCoverage 或 AFL 自訂 pass）
    │ 插入 __afl_trace() 呼叫
    ▼
target binary（含 forkserver + coverage instrumentation）

執行時：
target binary 直接連到 SHM，覆蓋路徑最短，overhead 最小
速度：約 1x（基準）
```

### QEMU Mode（binary-only target）

```
不重新編譯 target，改用修改版的 QEMU user-mode emulator：

afl-fuzz -Q -i in -o out ./target_no_instrumentation @@
    │
    ▼
afl-qemu-trace（修改版 QEMU）
    │  在 translation block 邊界插入 bitmap 更新
    │  模擬 forkserver 協議
    ▼
target binary（在 QEMU 虛擬環境下執行，沒有重新插樁）

速度：約 1/2 ~ 1/5 x（相比 source instrumentation）
原因：QEMU 的 basic block 翻譯有 overhead，
     且每次翻譯後的 cache 被 fork 清掉一部分
```

### Frida Mode

```
比 QEMU mode 更輕量的動態插樁，適合 iOS/Android binary 或有 antidebug 的程式：

AFL_USE_FRIDA=1 afl-fuzz -O -i in -o out ./target @@
    │
    ▼
Frida agent（動態注入）
    │  attach 到 target process，在 function 邊界插入 hook
    │  透過 Frida 的 API 更新 bitmap
    ▼
target binary（不需要重新編譯，Frida 在 runtime 插樁）

速度：介於 QEMU mode 和 source instrumentation 之間（對某些 target）
優點：比 QEMU 更容易處理有反偵測機制的 binary
```

## 底層機制：SHM 的建立與生命週期

```c
// afl-fuzz.c（簡化）

// 1. afl-fuzz 建立 SHM
int shm_id = shmget(IPC_PRIVATE, MAP_SIZE, IPC_CREAT | IPC_EXCL | 0600);
// MAP_SIZE = 65536（64KB）

// 2. 把 SHM ID 設定為環境變數，target 啟動時會讀取
char shm_str[11];
sprintf(shm_str, "%d", shm_id);
setenv(SHM_ENV_VAR, shm_str, 1);
// SHM_ENV_VAR = "__AFL_SHM_ID"

// 3. afl-fuzz 自己也 attach 這個 SHM
u8 *trace_bits = shmat(shm_id, NULL, 0);
// trace_bits 就是 fuzzer 這側看到的 bitmap

// 4. 每次執行前，清零 bitmap
memset(trace_bits, 0, MAP_SIZE);

// 5. 執行完成後，檢查 bitmap 有沒有新內容
u8 has_new = has_new_bits(trace_bits, virgin_bits, MAP_SIZE);
```

```c
// afl-compiler-rt.o.c（簡化）
// target 啟動時執行

void __afl_map_shm(void) {
    char *id_str = getenv(SHM_ENV_VAR);    // 讀取 "__AFL_SHM_ID"
    if (id_str) {
        int shm_id = atoi(id_str);
        __afl_area_ptr = shmat(shm_id, NULL, 0);  // attach SHM
    }
    // 如果沒有 SHM_ENV_VAR，target 會 attach 到一個靜態 dummy bitmap
    // 這樣不插樁的 target 也能跑，只是不回報 coverage
}
```

**方向確認**：SHM 是 **target 寫、fuzzer 讀**。很多人把方向搞反，因為直覺上「fuzzer 控制一切」，但 coverage 資訊是從 target 流向 fuzzer 的。

## 對比：有 forkserver vs 沒有 forkserver

| 面向 | 有 forkserver | 沒有 forkserver（fallback） |
|------|------------|-------------------------|
| 每個測試用例的成本 | `fork()` + 執行 | `execve()` + 動態連結 + 執行 |
| 動態連結庫的初始化 | 只在第一次付 | 每次都付 |
| 典型速度差異 | 基準 | 5-10x 慢（視 .so 數量） |
| 何時會 fallback | target 沒有插樁，或 forkserver 握手失敗 | - |
| afl-fuzz 的警告 | 無 | `[!] PROGRAM ABORT : Fork server handshake failed` |

## 踩雷集錦

**1. SHM 方向搞反**

SHM 中 bitmap 的資料從 target 流向 fuzzer（target 寫入、fuzzer 讀取）。fuzzer 寫入 SHM 的只有「在執行前清零」這個動作。把方向搞反會導致你在讀程式碼時完全搞不懂資料流。

**2. QEMU mode 的速度損失被低估**

很多人以為 QEMU mode 「差不多」，但實際上對有大量 branch 的 target，overhead 可以達到 5-10x。原因是 QEMU 的 basic block 翻譯 cache 在每次 fork 後需要重建，這個成本在 coverage 密集的程式上很顯著。在決定用 QEMU mode 之前，先估算你的 target 有多少 BB。

**3. 多個 afl-fuzz 實例用 filesystem 同步，不是 SHM**

每個 afl-fuzz 實例有自己獨立的 SHM bitmap。多實例之間沒有共享 SHM——它們透過 filesystem（`out/.synced/` 目錄）定期同步 queue。所以跑多個實例時，不要期望它們「即時」看到彼此的新 seed，同步有 latency。

**4. forkserver 在 child 裡不再執行 forkserver loop**

`fork()` 後，parent（forkserver）繼續在 forkserver loop 等待下一個指令，child 則跳過 forkserver 的初始化直接從 `main()` 繼續執行。如果你在 forkserver 初始化之後、`main()` 之前有某些 setup 邏輯，那些邏輯每次 fork 都會重跑——這是 deferred forkserver 設計要解決的問題（見 Ch 16）。

**5. target 崩潰時 SHM 的狀態**

如果 target 在執行中途 crash，SHM 中的 bitmap 可能只有部分內容（crash 前執行過的邊）。AFL++ 會收到 child 的 crash signal，然後照樣讀取 bitmap 檢查新 coverage。所以 crash 本身不會讓 coverage 資訊消失。

## 進階：Forkserver 協議的完整細節

```
forkserver 初始化時，向 afl-fuzz 發送一個 4-byte 的「hello」：
    write(FORKSRV_FD + 1, &status, 4)   // FORKSRV_FD + 1 = 199

這個 4-byte 可以攜帶 capability flags，告訴 fuzzer 「我支援哪些功能」：
    bit 0: FSRV_OPT_ENABLED          - forkserver 已啟用
    bit 1: FSRV_OPT_SHDMEM_FUZZ     - 支援 shared memory fuzzing（更快的輸入傳遞）
    bit 2: FSRV_OPT_AUTODICT         - target 有自動提取的 token dictionary
    bit 8: FSRV_OPT_NEWCOV           - 支援 coverage tracking 的額外功能

afl-fuzz 收到 hello 後開始主迴圈。每次需要跑一個測試用例：
    1. write(FORKSRV_FD, &was_killed, 4)  // 告訴 forkserver 「開始 fork」
    2. forkserver fork()，把 child PID 寫回：
       write(FORKSRV_FD + 1, &child_pid, 4)
    3. child 執行測試用例（讀取測試輸入，跑 target 邏輯）
    4. child 結束，forkserver 透過 waitpid() 得到 exit status：
       write(FORKSRV_FD + 1, &status, 4)
    5. afl-fuzz 收到 exit status，判斷是 normal exit / crash / timeout
```

AFL++ 4.x 支援 **shared memory fuzzing**（`FSRV_OPT_SHDMEM_FUZZ`）：把測試用例也放在 SHM 裡，避免每次測試都要寫檔案，可以提升 10-20% 的 exec/s。

```bash
# 啟用 SHM fuzzing（需要 target 有對應的 harness 支援）
export AFL_LLVM_ALLOWLIST=harness.c  # 只對 harness 插樁（例子）
afl-clang-fast -o target_shm harness.c target_lib.c
# AFL++ 會自動檢測 forkserver 是否支援 SHM fuzzing 並啟用
```

## 動手練習

1. **觀察 SHM 的存在**：
   ```bash
   # 在一個終端啟動 afl-fuzz
   afl-fuzz -i in -o out ./target @@
   # 在另一個終端觀察 SHM
   ipcs -m | grep $(id -u)   # 列出當前 user 的 SHM segment
   # 你應該會看到一個大小 65536 的 SHM
   ```

2. **確認 forkserver 是否啟用**：
   ```bash
   # 查看 fuzzer_stats 裡的 forkserver 狀態
   cat out/fuzzer_stats | grep forkserver
   # 如果沒有 forkserver，exec_speed 會顯著下降
   ```

3. **計算 forkserver 的速度優勢**：
   ```bash
   # 直接執行 target 100 次，測量時間
   time for i in $(seq 100); do ./target < /dev/urandom; done
   # 用 AFL++ 跑 10 秒，看 exec_speed
   # 比較兩個數字（注意 AFL++ 的數字是穩態，第一個包含冷啟動）
   ```

## 本章重點整理

- AFL++ 由三個主要元件構成：`afl-fuzz`（主 loop + 策略）、`afl-cc`（插樁工具鏈）、forkserver（嵌入 target 的協議層）；用四種 IPC 機制（SHM bitmap、pipe、filesystem、signal）連接
- SHM bitmap 的資料流向是 target 寫、fuzzer 讀；forkserver 用 fd 198/199 兩個管道和 fuzzer 通訊
- QEMU mode 和 Frida mode 是對 binary-only target 的架構替代方案，以 2-5x 速度損失換取「不需要原始碼」的能力

## 自我檢核

1. `__AFL_SHM_ID` 這個環境變數裡存的是什麼？target process 怎麼用它找到 bitmap？

2. Forkserver 協議用哪兩個 fd 通訊？哪個方向是「fuzzer 送指令給 target」，哪個方向是「target 回報 child 狀態」？

3. 為什麼多個 afl-fuzz 實例不用 SHM 做 corpus 同步，而是用 filesystem？這個設計有什麼好處和壞處？

4. 如果你在 afl-fuzz 的 status screen 看到 `exec speed: 50/sec`，但你估計這個 target 直接跑應該有 1000/sec，最可能的診斷方向是什麼？

5. QEMU mode 比 source instrumentation 慢的根本原因是什麼？單純是「多了一層模擬」嗎？

## 延伸閱讀

### 論文

- **[AFL++: Combining Incremental Steps of Fuzzing Research](https://www.usenix.org/conference/woot20/presentation/fioraldi)** — Fioraldi et al., WOOT 2020
  - **核心貢獻**：完整描述 AFL++ 各元件的設計，包含 forkserver 協議和 SHM bitmap 的整合
  - **讀哪裡**：Section 4（Implementation）——forkserver 協議和 bitmap 機制的最權威描述
  - **和本章的關聯**：本章的架構圖是這一節的視覺化版本

### 部落格 / 技術文章

- **[AFL Technical Details](https://lcamtuf.coredump.cx/afl/technical_details.txt)** — lcamtuf
  - forkserver 設計的第一手說明，包含為什麼不用 `ptrace`、為什麼不用 `execve`
  - 讀「The fork server」那一節，3 分鐘

- **[AFL++ Fuzzing in Depth](https://github.com/AFLplusplus/AFLplusplus/blob/stable/docs/fuzzing_in_depth.md)** — 官方文件
  - 架構那一節有更詳細的選項說明
  - 是本章的「操作手冊」對應版本

### 官方文件

- **[AFL++ source: src/afl-fuzz.c](https://github.com/AFLplusplus/AFLplusplus/blob/stable/src/afl-fuzz.c)** — main loop 入口
  - 搜尋 `fuzz_one()` 找到主 loop 的起點（約 2000 行，先看函式結構，不要逐行讀）

- **[AFL++ source: instrumentation/afl-compiler-rt.o.c](https://github.com/AFLplusplus/AFLplusplus/blob/stable/instrumentation/afl-compiler-rt.o.c)** — forkserver + bitmap runtime
  - `__afl_forkserver_start()` 就是本章描述的 forkserver 邏輯，讀前 300 行

→ [Ch 4 — Source Tree 導覽](./04-source-tree-walkthrough.md)
