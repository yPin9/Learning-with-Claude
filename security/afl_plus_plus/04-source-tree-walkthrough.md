# Ch 4 — Source Tree 導覽：在 10 萬行 code 裡找到你要的東西

> **目標**：能在 AFL++ 原始碼中找到任何功能的實作位置，不再迷失在 10 萬行 code 裡。
> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

## 為什麼要看 source tree？

AFL++ 的文件說明了「怎麼用」，但要理解「為什麼這樣設計」、「這個行為來自哪裡」、「我想客製化某個部分要改哪個檔案」，你得進原始碼。

問題是 AFL++ 有超過 10 萬行 C/C++ code，而且功能散佈在多個目錄。沒有地圖，你會在 `afl-fuzz.c` 找不到的地方浪費一個小時。

這一章的目標是給你那張地圖。

## 先建立直覺

AFL++ 的原始碼按**功能邊界**而非按「執行順序」組織。這意味著：

- 想找「fuzzer 怎麼選 seed」→ 不在 `afl-fuzz.c`，在 `afl-fuzz-queue.c`
- 想找「bitmap 怎麼比對新舊 coverage」→ 不在 main loop，在 `afl-fuzz-bitmap.c`
- 想找「CmpLog 的邏輯」→ 在 `afl-fuzz-cmplog.c`，和 `instrumentation/` 下的插樁部分

先記這個原則：**`afl-fuzz.c` 是入口和膠水，實際邏輯在各個 `-xxxxx.c` 模組裡。**

## 頂層目錄結構

```
AFLplusplus/
│
├── src/                    # afl-fuzz 主程式（8個模組化 .c 檔）
│   ├── afl-fuzz.c             # main(), 參數解析, 初始化
│   ├── afl-fuzz-bitmap.c      # bitmap 操作, coverage 比對
│   ├── afl-fuzz-queue.c       # corpus 管理, seed 排程
│   ├── afl-fuzz-mutators.c    # deterministic + havoc mutation
│   ├── afl-fuzz-run.c         # target 執行, timeout 管理
│   ├── afl-fuzz-stats.c       # status screen 更新
│   ├── afl-fuzz-init.c        # 環境初始化, forkserver 握手
│   ├── afl-fuzz-cmplog.c      # CmpLog / RedQueen 邏輯
│   ├── afl-common.c           # 共用工具函式（所有工具都用）
│   ├── afl-cc.c               # compiler wrapper 主程式
│   ├── afl-forkserver.c       # forkserver 客戶端（fuzzer 側）
│   └── afl-sharedmem.c        # SHM 建立與管理
│
├── include/                # 標頭檔
│   ├── afl-fuzz.h             # 核心 struct 定義（最重要）
│   ├── afl-forkserver.h       # forkserver 結構和常數
│   ├── alloc-inl.h            # 自訂記憶體配置（內嵌函式）
│   ├── config.h               # 編譯時常數（MAP_SIZE = 65536 在這裡）
│   └── types.h                # u8/u16/u32/u64 等 typedef
│
├── instrumentation/        # LLVM pass + compiler runtime
│   ├── afl-compiler-rt.o.c    # forkserver 實作 + bitmap 更新 runtime
│   ├── afl-llvm-pass.so.cc    # PCGUARD 和 CLASSIC 的 LLVM pass
│   ├── afl-llvm-lto-instrumentation.so.cc  # LTO pass
│   ├── afl-llvm-cmplog-routines.so.cc      # CmpLog 插樁 pass
│   ├── afl-llvm-dict2file.so.cc            # 自動 token 提取 pass
│   └── GNUmakefile            # 這個目錄的獨立 Makefile
│
├── qemu_mode/              # QEMU 修改版（binary-only target）
│   ├── patches/               # QEMU 的 AFL++ 特有 patch
│   ├── qemuafl/               # 修改版 QEMU source
│   └── build_qemu_support.sh  # 建構腳本
│
├── frida_mode/             # Frida-based 動態插樁
│   ├── src/                   # Frida agent 原始碼
│   ├── include/
│   └── GNUmakefile
│
├── unicorn_mode/           # Unicorn engine（模擬器 fuzzing）
│   └── ...
│
├── utils/                  # 輔助工具（不是 fuzzer 核心）
│   ├── afl-cmin.py            # Corpus minimizer（Python 版）
│   ├── afl-cmin.bash          # Corpus minimizer（bash 版）
│   ├── afl-plot               # Coverage 視覺化（gnuplot）
│   ├── crash_triage/          # Crash 分析輔助
│   └── optimin/               # 實驗性的 corpus 最佳化
│
├── custom_mutators/        # 官方 custom mutator 範例
│   ├── grammar_mutator/       # Grammar-based mutator（基於 tree-sitter）
│   ├── libprotobuf-mutator/   # Protobuf-based mutator
│   ├── radamsa/               # Radamsa 整合
│   └── ...                    # 其他範例
│
├── docs/                   # 文件
│   ├── fuzzing_in_depth.md    # 主要操作手冊
│   ├── INSTALL.md             # 安裝指南
│   ├── env_variables.md       # 所有環境變數說明
│   ├── custom_mutators.md     # Custom mutator API 文件
│   └── internals/             # 內部機制說明
│
├── testcases/              # 初始 seed corpus（各格式）
│   ├── images/gif/
│   ├── multimedia/mp3/
│   └── ...
│
├── afl-fuzz                # 編譯後的主程式（不在 src/）
├── afl-cc                  # Compiler wrapper
├── afl-clang-fast          # → symlink to afl-cc
├── afl-clang-fast++        # → symlink to afl-cc
├── afl-clang-lto           # → symlink to afl-cc
├── afl-showmap             # Coverage 觀察工具
├── afl-tmin                # Test case minimizer
└── GNUmakefile             # 頂層 Makefile
```

## `src/` 的模組切分：每個檔案負責什麼

### `afl-fuzz.c`：main() + 膠水

這個檔案比你想的**短**——大約 1000 行，主要做三件事：

1. 解析命令列參數（`-i`、`-o`、`-p` 等）
2. 呼叫 `afl-fuzz-init.c` 的初始化函式
3. 進入主 loop（`while (1) { fuzz_one(afl); }`）

**不要**在這裡找 mutation 邏輯、bitmap 比對、seed 選擇——這些都在別的模組。

```c
// src/afl-fuzz.c 的 main() 結構（簡化）
int main(int argc, char **argv) {
    // 1. 配置初始化
    afl_state_t *afl = calloc(1, sizeof(afl_state_t));
    
    // 2. 解析參數（很長的 while-getopt 迴圈）
    while ((opt = getopt(...)) != -1) { ... }
    
    // 3. 環境初始化（afl-fuzz-init.c）
    setup_signal_handlers();
    check_asan_opts(afl);
    setup_shm(afl);           // 建立 SHM bitmap
    init_count_class16();      // 初始化 bucket 表
    setup_dirs_fds(afl);       // 建立 out/ 目錄結構
    read_testcases(afl);       // 載入初始 corpus
    pivot_inputs(afl);         // 把 corpus 複製到 queue/
    
    // 4. 啟動 forkserver
    afl_fsrv_start(&afl->fsrv, afl->argv, &afl->stop_soon, ...);
    
    // 5. 初始化 coverage（跑所有初始 seed 一次）
    perform_dry_run(afl);
    
    // 6. 主 loop
    while (!stop_soon) {
        if (!afl->queue_cur) { cull_queue(afl); ... }
        fuzz_one(afl);          // 一次完整的 seed+mutate+run+check
        ...
    }
}
```

### `afl-fuzz-bitmap.c`：Coverage 的計算核心

**最重要的兩個函式**：

`has_new_bits()`：比較這次執行的 bitmap 和累積的 virgin_bits，判斷有沒有新 coverage。

```c
// 簡化版邏輯
u8 has_new_bits(u8 *virgin_map, afl_state_t *afl) {
    u64 *current = (u64 *)afl->fsrv.trace_bits;  // 這次執行的 bitmap
    u64 *virgin  = (u64 *)virgin_map;             // 從沒見過的 bit（初始全 0xFF）
    
    for (i = 0; i < MAP_SIZE / 8; i++) {
        if (current[i] && (current[i] & virgin[i])) {
            // 有 bit 在 virgin_map 裡還是新的
            *virgin &= ~(*current);  // 標記為「已見過」
            ret = 2;                  // ret=2：有全新的邊
        }
    }
    return ret;  // 0: 無新內容, 1: count 改變, 2: 新邊
}
```

`classify_counts()`：把 bitmap 的 byte 值 bucket 化——AFL++ 不關心一條邊執行了 17 次還是 18 次，只關心執行次數的「級別」（1、2、3-4、5-8、9-16、17-32、33-128、129+）。

### `afl-fuzz-queue.c`：Corpus 管理

負責兩件事：

**1. Seed 選擇**（`fuzz_one()` 最開始呼叫的步驟之一）：根據 power schedule 決定下一個要 mutate 的 seed。

**2. Favored minset 計算**（`cull_queue()`）：找出能覆蓋所有已知邊的「最小 seed 集合」，把這些 seed 標記為 `favored`。非 favored 的 seed 只有 10% 機率被選到。

```c
// src/afl-fuzz-queue.c
// cull_queue() 的核心邏輯（簡化）
void cull_queue(afl_state_t *afl) {
    // 用 top_rated[] 陣列追蹤「哪個 seed 最能代表這條邊」
    // top_rated[edge_id] = 最快/最小的能覆蓋這條邊的 seed
    
    memset(temp_v, 255, afl->fsrv.map_size);  // 全部邊都還沒被覆蓋
    
    for (q = afl->queue; q; q = q->next) {
        q->favored = 0;  // 先全部清掉
    }
    
    for (i = 0; i < afl->fsrv.map_size; i++) {
        if (afl->top_rated[i]) {
            // 這條邊有 top-rated seed
            if (temp_v[i]) {
                // 這條邊還沒被選過的 seed 覆蓋到
                mark_as_favored(afl->top_rated[i]);  // 標記為 favored
                // 把這個 seed 覆蓋的所有邊從 temp_v 中移除
                ...
            }
        }
    }
}
```

### `afl-fuzz-mutators.c`：Mutation 邏輯

這個檔案包含 `fuzz_one()` 的大部分實作，分三個階段：

**Deterministic（確定性）階段**：每個 bit/byte/word 都系統性地嘗試，順序固定，可重現。

```
bit flips:      每個 bit 翻轉（1/2/4 個 bit 連續翻）
byte flips:     每個 byte 翻轉（1/2/4 個 byte 連續翻）
arithmetics:    對 byte/word/dword 做 +1 到 +35 / -1 到 -35
known ints:     替換為常見的邊界值（0、-1、INT_MAX、0xFF00 等）
```

**Havoc（混亂）階段**：隨機選 mutation 操作，每輪選 `2 * stage_max` 次。包含 deterministic 的所有操作加上：
- 替換為 dictionary token
- 複製/刪除/插入 block
- 如果有 CmpLog：插入在 CmpLog 中觀察到的值

**Splice（嫁接）階段**：把兩個 seed 的前半段和後半段拼在一起，嘗試製造新的輸入結構。

### `afl-fuzz-run.c`：執行引擎

最重要的函式是 `run_target()`：

```c
// 簡化邏輯
fsrv_run_result_t run_target(afl_state_t *afl, u32 timeout) {
    // 1. 清零 bitmap（準備接收這次執行的 coverage）
    memset(afl->fsrv.trace_bits, 0, afl->fsrv.map_size);
    
    // 2. 透過 forkserver 執行一次 target
    // （寫 4 bytes 到 fd 198，等 fd 199 回傳 child PID 和 exit status）
    
    // 3. 設定 timeout alarm
    setitimer(ITIMER_REAL, &it, NULL);
    
    // 4. 等待 child 結束
    waitpid(child_pid, &status, ...);
    
    // 5. 判斷結果
    if (WIFSIGNALED(status))   return FSRV_RUN_CRASH;
    if (timed_out)             return FSRV_RUN_TMOUT;
    return FSRV_RUN_OK;
}
```

### `afl-fuzz-init.c`：啟動序列

包含整個 fuzzing session 開始時的所有初始化：
- `setup_shm()`：建立和 attach 共享記憶體
- `read_testcases()`：從 `-i` 目錄載入初始 corpus
- `perform_dry_run()`：跑所有初始 seed 一次，建立基準 coverage
- `check_binary()`：確認 target binary 有被正確插樁

最值得看的是 `perform_dry_run()` 裡的 crash 和 timeout 偵測——它會對初始 corpus 裡的每個 seed 做完整的 coverage 分析，並警告「你的 seed 本身就會 crash」這類問題。

### `afl-fuzz-cmplog.c`：CmpLog 的實作

CmpLog（Coverage Measurement with Logged Comparisons）的核心邏輯：

1. **插樁側**（`instrumentation/afl-llvm-cmplog-routines.so.cc`）：在每個比較指令（`icmp`、`switch`、`strcmp` 等）記錄操作數（operands）
2. **fuzzer 側**（`afl-fuzz-cmplog.c`）：分析 CmpLog 的輸出，找到「input 中哪些 bytes 對應到這個比較的某個操作數」，然後用「正確的值」去替換

```bash
# CmpLog 需要兩個步驟：
# 1. 編譯 target 時啟用 CmpLog 插樁
AFL_LLVM_CMPLOG=1 afl-clang-fast -o target_cmplog target.c

# 2. 啟動 fuzzer 時指定 CmpLog binary
afl-fuzz -i in -o out -c ./target_cmplog ./target_regular @@
# -c 指定的是 CmpLog binary，主 target 是普通插樁版本
# 兩個 binary 必須來自同一份原始碼，只差在插樁方式
```

## `instrumentation/` 的重要檔案

### `afl-compiler-rt.o.c`：Runtime Library

這個檔案編譯後會 link 進每一個用 `afl-clang-fast` 編譯的 target binary。包含：

- `__afl_forkserver_start()`：forkserver 的完整實作（等待 fuzzer 信號、fork、回報狀態）
- `__afl_trace()`：每條邊執行時被呼叫，更新 bitmap
- `__afl_map_shm()`：初始化時 attach 到 SHM bitmap
- `__sanitizer_cov_trace_pc_guard()`：SanitizerCoverage 的 callback，轉接到 AFL++ 的 bitmap 邏輯

找 forkserver 協議的最權威實作：搜尋 `FORKSRV_FD`，找到讀 fd 198、寫 fd 199 的程式碼。

### `afl-llvm-pass.so.cc`：LLVM 插樁 Pass

這個 LLVM pass 在編譯時遍歷每個函式的基本塊（basic block），在適當的位置插入 coverage 追蹤的呼叫。

AFL++ 有兩種 LLVM 插樁模式：

**CLASSIC mode**（AFL 的原始方式）：
```
對每個基本塊 BB：
  在 BB 的入口插入：
    cur_location = <random constant>   // 這個基本塊的 ID
    afl_area_ptr[cur_location ^ prev_location]++
    prev_location = cur_location >> 1
```

**PCGUARD mode**（現在的預設，基於 SanitizerCoverage）：
```
利用 clang 的 -fsanitize-coverage=trace-pc-guard
讓 clang 自動在每個 BB 加上 __sanitizer_cov_trace_pc_guard() 呼叫
AFL++ 的 runtime 攔截這個呼叫，更新 bitmap
```

PCGUARD 的優點：和 clang 的 coverage 框架整合，更穩定，對 C++ exception、longjmp 的處理更正確。

### `afl-llvm-lto-instrumentation.so.cc`：LTO Pass

LTO（Link-Time Optimization，鏈結時最佳化）在連結階段才做插樁，這時所有的 translation unit 都已知，可以：

1. **全域分配 edge ID**：消除不同 .o 之間的 ID 碰撞
2. **更精確的 edge 計數**：可以看到跨 TU 的 callgraph
3. **更低的 collision rate**：理論上可以做到 collision-free

代價：編譯速度更慢（因為要保留整個 IR 到連結階段），且對某些 target（有 linker script 或複雜 link order）可能失敗。

## 如何快速定位一個功能

**方法一：從問題出發，找關鍵字**

```bash
# 想找 power schedule 的計算位置
grep -n "calculate_score\|perf_score" src/afl-fuzz-queue.c | head -20

# 想找 has_new_bits() 的實作
grep -n "has_new_bits" src/afl-fuzz-bitmap.c | head -20

# 想找 forkserver 握手的實作
grep -n "FORKSRV_FD\|forkserver" src/afl-forkserver.c | head -30

# 想找 timeout 怎麼處理
grep -n "SIGKILL\|setitimer\|ITIMER_REAL" src/afl-fuzz-run.c | head -20
```

**方法二：從 afl-fuzz.h 的 struct 出發**

當你想理解「AFL++ 的 state 有哪些欄位」時，先看 `include/afl-fuzz.h`：

```bash
# 找 afl_state_t 的定義（所有全局 state 都在這個 struct 裡）
grep -n "afl_state_t" include/afl-fuzz.h | head -5

# 找 queue_entry 的定義（每個 seed 的資料結構）
grep -n "queue_entry" include/afl-fuzz.h | head -10
```

**方法三：從環境變數反查功能**

AFL++ 的大量功能是透過環境變數控制的，`docs/env_variables.md` 列出所有環境變數。找到你感興趣的 env var 後：

```bash
# 假設你想知道 AFL_DISABLE_TRIM 做了什麼
grep -rn "AFL_DISABLE_TRIM" src/ | head -10
# 找到 getenv("AFL_DISABLE_TRIM") 的地方，看周圍的程式碼
```

**方法四：用 ctags 或 LSP 跳轉**

在本地 build AFL++ 之後，用 ctags 建立索引：

```bash
cd AFLplusplus
ctags -R src/ include/ instrumentation/
# 然後在 vim 裡用 Ctrl-] 跳轉到函式定義
```

或用 VSCode + clangd（需要 `compile_commands.json`，AFL++ 的 Makefile 可以產生）。

## 動手練習

### 練習一：找 has_new_bits() 的實作

```bash
cd AFLplusplus

# 找函式定義
grep -n "has_new_bits" src/afl-fuzz-bitmap.c | head -20

# 找函式宣告
grep -n "has_new_bits" include/afl-fuzz.h | head -5

# 找所有呼叫點
grep -rn "has_new_bits" src/ | grep -v "\.h:" | head -20
```

預期輸出：定義在 `afl-fuzz-bitmap.c`，呼叫點在 `afl-fuzz-run.c`（執行完成後）和 `afl-fuzz-init.c`（dry run 時）。

### 練習二：找 power schedule 的計算

```bash
# 計算每個 seed 的 performance score
grep -n "calculate_score\|perf_score" src/afl-fuzz-queue.c | head -20

# 找不同 schedule 的 case
grep -n "case FAST:\|case EXPLORE:\|case EXPLOIT:" src/afl-fuzz-queue.c | head -20
```

### 練習三：追蹤 bitmap 的一次完整生命週期

從「fuzzer 清零 bitmap」到「fuzzer 讀取 bitmap 判斷有新 coverage」的完整路徑：

```bash
# 清零 bitmap
grep -n "memset.*trace_bits\|MEM_BARRIER" src/afl-fuzz-run.c | head -10

# 執行 target（forkserver 協議）
grep -n "FORKSRV_FD\|write.*198\|read.*199" src/afl-forkserver.c | head -20

# 判斷新 coverage
grep -n "has_new_bits\|virgin_bits" src/afl-fuzz-run.c | head -20
```

### 練習四：找 CmpLog 的比較值提取

```bash
# fuzzer 側：如何使用 CmpLog 的輸出
grep -n "cmplog\|rtn_\|colorization" src/afl-fuzz-cmplog.c | head -30

# 插樁側：如何記錄比較值
grep -n "__cmplog_\|cmp_map" instrumentation/afl-compiler-rt.o.c | head -20
```

### 練習五：確認 MAP_SIZE

```bash
# bitmap 的大小定義在哪裡？
grep -n "MAP_SIZE\|65536\|1 << 16" include/config.h | head -10
```

## 重要常數：先記住這些

| 常數 | 值 | 定義位置 | 意義 |
|------|---|---------|------|
| `MAP_SIZE` | 65536 | `include/config.h` | SHM bitmap 大小（64KB） |
| `FORKSRV_FD` | 198 | `include/config.h` | Forkserver 的基礎 fd |
| `SHM_ENV_VAR` | `"__AFL_SHM_ID"` | `include/config.h` | 傳 SHM ID 的環境變數 |
| `EXEC_TIMEOUT` | 1000 | `include/config.h` | 預設 timeout（ms） |
| `HAVOC_CYCLES` | 256 | `include/config.h` | Havoc 每輪的基準次數 |
| `MAX_FILE` | 1MB | `include/config.h` | 最大測試用例大小 |

所有這些常數都在 `include/config.h`，調整行為前先確認這個檔案。

## `include/afl-fuzz.h`：迷失時先看這裡

這個標頭檔定義了兩個你必須知道的 struct：

**`afl_state_t`**：整個 fuzzing session 的全局 state。包含：

```c
// 部分欄位（簡化）
struct afl_state {
    // Fuzzing 狀態
    struct queue_entry *queue;       // corpus queue
    struct queue_entry *queue_cur;   // 當前正在 fuzz 的 seed
    struct queue_entry **top_rated;  // 每條邊的最佳 seed
    
    // Coverage
    u8 *virgin_bits;                 // 從未見過的 coverage（初始全 0xFF）
    u8 *virgin_tmout;                // timeout 相關的 coverage
    u8 *virgin_crash;                // crash 相關的 coverage
    
    // Schedule
    u32 queue_cycle;                 // 第幾輪 queue
    u64 total_execs;                 // 總執行次數
    enum power_schedule schedule;    // 當前的 power schedule
    
    // Forkserver
    afl_forkserver_t fsrv;          // forkserver 的狀態
    
    // CmpLog
    u8 *cmplog_binary;              // CmpLog binary 路徑
    ...
};
```

**`queue_entry`**：corpus 中每個 seed 的資料：

```c
struct queue_entry {
    u8 *fname;                  // 檔案路徑
    u32 len;                    // 輸入長度
    u8  was_fuzzed;             // 是否已被 fuzz 過
    u8  favored;                // 是否在 favored minset 裡
    u32 bitmap_size;            // 這個 seed 覆蓋的邊數
    u64 exec_us;                // 執行時間（微秒）
    double perf_score;          // power schedule 分數
    struct queue_entry *next;   // 鏈結串列
};
```

## 對比：各模組的關鍵函式

| 模組 | 最重要的函式 | 在哪找 |
|------|-----------|-------|
| bitmap | `has_new_bits()`, `classify_counts()` | `src/afl-fuzz-bitmap.c` |
| queue | `cull_queue()`, `calculate_score()` | `src/afl-fuzz-queue.c` |
| mutation | `fuzz_one()`, `havoc_stage()` | `src/afl-fuzz-mutators.c` |
| execution | `run_target()`, `write_to_testcase()` | `src/afl-fuzz-run.c` |
| init | `perform_dry_run()`, `setup_shm()` | `src/afl-fuzz-init.c` |
| cmplog | `cmplog_exec_target()`, `colorization()` | `src/afl-fuzz-cmplog.c` |
| forkserver | `afl_fsrv_start()`, `afl_fsrv_run_target()` | `src/afl-forkserver.c` |

## 踩雷集錦

**1. 在 `afl-fuzz.c` 裡找不到的東西比找得到的多**

`afl-fuzz.c` 本身只有約 1000 行，主要是 main() 和參數解析。任何超過 50 行的功能幾乎都在別的模組。第一次搜尋某個功能，優先搜尋整個 `src/` 目錄而不是只看 `afl-fuzz.c`。

**2. `include/afl-fuzz.h` 是整個程式碼的骨架**

當你不知道某個變數是什麼類型、某個欄位有什麼含義，先看這個頭檔。它有幾千行，但 struct 定義之間有清楚的 comment 分隔。

**3. instrumentation/ 下的 .cc 是 C++，不是 C**

LLVM pass 是用 C++ 寫的（`.so.cc` 後綴），和 `src/` 下的純 C 是不同的語言。如果你看到 `llvm::Function` 這種類型，你在看的是 LLVM C++ API，不是 AFL++ 的 fuzzer 邏輯。

**4. afl-compiler-rt.o.c 雖然叫 `.c` 但行為很特殊**

這個 runtime 被編譯成 `.o` 靜態連結進每個 target——它不是一個獨立可執行的程式，也不是一個正常的 .so。它的程式碼執行在 target process 的 address space 裡，而不是 fuzzer process 裡。

**5. `utils/` 目錄下的工具不是核心 fuzzer 的一部分**

`afl-cmin`（corpus minimizer）、`afl-tmin`（testcase minimizer）、`afl-plot`（圖形化）都是獨立工具，有自己的 main()，不是 `afl-fuzz` 的一部分。它們用到 `afl-common.c` 的共用函式，但邏輯上完全獨立。

## 進階：用 Git History 理解設計演進

原始碼目前的樣子只是快照。理解**為什麼是這樣**，需要看 git history：

```bash
cd AFLplusplus

# 看最近 50 個 commit 的概況
git log --oneline -50

# 看某個特定功能最後幾次被改動的時間
git log --oneline -- src/afl-fuzz-cmplog.c | head -10

# 看某個函式的修改歷史（需要 git log -S 搜尋）
git log -S "has_new_bits" --oneline | head -10

# 找哪個 commit 引入了 MOpt
git log --oneline --all | grep -i "mopt\|swarm" | head -10
```

活躍的修改集中在幾個地方：
- `src/afl-fuzz-mutators.c`：mutation 策略持續在調整
- `src/afl-fuzz-cmplog.c`：CmpLog 精確度的改進
- `instrumentation/afl-llvm-*.cc`：LLVM pass 隨 LLVM 版本更新

相對穩定的部分：
- `src/afl-forkserver.c`：forkserver 協議幾乎沒變過
- `include/config.h`：常數偶爾調整但不頻繁

## 動手練習（延伸）

**挑戰：追蹤一個環境變數從設定到生效的完整路徑**

選 `AFL_LLVM_LAF_ALL=1`，從這個環境變數被讀取，到實際改變 instrumentation 行為的完整程式碼路徑：

```bash
# 1. 在哪裡讀取這個 env var？
grep -rn "AFL_LLVM_LAF_ALL" . | grep -v ".md:" | head -20

# 2. 讀取後設定了什麼 flag？
grep -n "laf_all\|use_laf\|split_compare" instrumentation/ -r | head -20

# 3. 這個 flag 在 LLVM pass 裡如何影響插樁？
grep -n "split_compare\|LAF" instrumentation/afl-llvm-pass.so.cc | head -20
```

## 本章重點整理

- AFL++ 的原始碼按功能邊界模組化：`afl-fuzz.c` 只是入口，bitmap 比對在 `afl-fuzz-bitmap.c`，seed 管理在 `afl-fuzz-queue.c`，mutation 在 `afl-fuzz-mutators.c`——找功能前先對照模組表
- `include/afl-fuzz.h` 是整個程式的骨架，迷失方向時先在這裡找 struct 定義，`include/config.h` 是所有重要常數的來源
- `instrumentation/` 下的 `afl-compiler-rt.o.c` 是執行在 target process 裡的 runtime（不是 fuzzer 本身），`afl-llvm-pass.so.cc` 是編譯時的 LLVM pass——兩者分工明確，概念上要分清楚

## 自我檢核

1. 如果你想找「AFL++ 怎麼決定一個 seed 的 power score」，你會先看哪個檔案的哪個函式？

2. `include/config.h` 裡的 `MAP_SIZE` 是 65536（64KB）。如果你想對一個有 100 萬條邊的超大程式用 AFL++，你需要改哪個地方，改成什麼值才能降低 bitmap collision？

3. `afl-compiler-rt.o.c` 裡的程式碼執行在哪個 process 的 address space 裡？它和 `src/afl-fuzz.c` 裡的程式碼有沒有共享任何記憶體空間（除了 SHM）？

4. 如果你想實作一個「當某個特定函式被呼叫時就算有新 coverage」的客製化 coverage，你需要修改哪個檔案？

5. `cull_queue()` 函式做的事情叫什麼？它的輸出（哪些 seed 被標記為 favored）會如何影響 fuzzing 效率？

## 延伸閱讀

### 部落格 / 技術文章

- **[AFL++ Internals: A Developer's Perspective](https://github.com/AFLplusplus/AFLplusplus/blob/stable/docs/internals)** — 官方 internals 文件
  - 對各模組有摘要性說明，是本章的官方版本
  - 讀完本章後對照這個文件，看有沒有理解不一致的地方

- **[Fuzzing with AFL++ (workshop)](https://github.com/AFLplusplus/AFLplusplus/blob/stable/docs/fuzzing_in_depth.md)** — AFL++ 官方深度指南
  - 第一節「Fuzzing with AFL++」有對 source tree 的高層說明

### 官方文件

- **[AFL++ GitHub: src/](https://github.com/AFLplusplus/AFLplusplus/tree/stable/src)** — 所有 .c 原始碼
  - 每個檔案頂部有 copyright 和簡短說明，10 秒可以知道這個檔案的用途

- **[AFL++ GitHub: include/afl-fuzz.h](https://github.com/AFLplusplus/AFLplusplus/blob/stable/include/afl-fuzz.h)** — 核心 struct 定義
  - `afl_state_t` 的完整欄位列表，加上 comment 說明每個欄位的含義

- **[AFL++ GitHub: include/config.h](https://github.com/AFLplusplus/AFLplusplus/blob/stable/include/config.h)** — 所有編譯時常數
  - 每個常數都有 comment 說明，調整行為的第一個入口

→ [Practice A — 第一個 Session](./practice-a-first-session.md)
