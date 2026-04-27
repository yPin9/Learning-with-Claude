# Ch 7 — Forkserver：AFL 最漂亮的設計

> 目標：解釋為什麼不 fork + exec 每次重跑；forkserver 如何在 `main()` 前先停住、重複 fork 乾淨的 child；`__AFL_INIT()` 做了什麼、deferred forkserver 把起點往後挪到哪。

## 最笨的作法會有多慢

fuzzer 要做「餵 input、看結果、再餵」的迴圈。最直覺的實作是：

```c
while (...) {
    char *input = mutate();
    pid_t pid = fork();
    if (pid == 0) {
        execve("./target", args, env);   // 啟動 target
    }
    waitpid(pid, &status, 0);
    if (has_new_coverage()) save(input);
}
```

每次 iteration 都 fork + exec。看起來合理，但成本是：

- `execve()`：kernel 重新載入 ELF、重做 page mapping。
- Dynamic linker (`ld-linux.so`) 解析 `needed libraries`，重新 load `libc.so.6`、`libssl.so` ...。
- 每個 `.init_array` constructor 重跑。
- 程式自己的 global ctor、env 解析、open() config files ...

對一個 parser tool，這些 startup cost 可能 1–10 ms — 每秒最多 100–1000 次 iteration。對一個真正的 C++ app（加上 STL 的 iostream 初始化之類），每秒 30–100 次都有可能。**fuzz 跑一週能試的 input 數量被 startup cost 鎖死**。

## Forkserver 的 insight

觀察：**exec 只需要做一次**。ELF 載入完、dynamic linker 解析完、static initializer 跑完 — 這之後的 process state 是可以重複使用的。

Forkserver 的 idea：

1. 讓 target 照常 exec 一次。
2. 但在 `main()` 被呼叫之前，搶走控制權，進入一個 loop：
   - 等 fuzzer 的信號。
   - 收到信號 → `fork()` 一個 child。child 是**完全相同的 process state** 的 copy（包括已經載入的 libc、已經初始化的 globals）。
   - Child 繼續往下跑（剛好從 `main()` 入口），真的執行 target 邏輯。
   - Parent（forkserver）回報 child status 給 fuzzer，繼續等下一個信號。

用 `fork()` 代替 `exec()` 的複雜 startup 工作。`fork()` 在 Linux 下靠 COW（copy-on-write）非常便宜，通常幾百微秒。

```
第一次：
  afl-fuzz ──exec──▶ target
                        │ 在 main() 前被 AFL 劫持
                        ▼
                     forkserver loop (parent)
                        │ fork
                        ▼
                     child（第一次 iteration）

之後：
  afl-fuzz ──signal──▶ forkserver ──fork──▶ child（第 N 次 iteration）
                        │                      │
                        │                      ▼ 跑完
                        ◀──── wait() ──── status
                        │
                        ▼
  afl-fuzz ◀── status ──
```

## 兩條 pipe 的 protocol

Forkserver 和 afl-fuzz 用兩個 fd 溝通：

```
#define FORKSRV_FD 198    // control 方向：fuzzer 寫，forkserver 讀
                          // status  方向：forkserver 寫，fuzzer 讀（FD+1 = 199）
```

所以 forkserver 那邊看到的 fd 是 198（讀）和 199（寫）。afl-fuzz 會 dup 自己那邊的 fd 到這兩個號碼，然後 fork/exec target — 於是 target 一起手就有這對 pipe。

### 握手

afl-fuzz 先發一個 4-byte hello：

```c
// afl-fuzz: afl-forkserver.c
int32_t hello;
read(fsrv->fsrv_st_fd, &hello, 4);   // 讀 forkserver 發來的 hello
```

target 側（`afl-compiler-rt.o.c` 的 `__afl_start_forkserver`）：

```c
static u32 was_killed;
u8 tmp[4] = {0, 0, 0, 0};
if (write(FORKSRV_FD + 1, tmp, 4) != 4) return;  // hello to fuzzer
```

握手同時攜帶 forkserver 版本、特殊能力等資訊（AFL++ 在這裡 negotiate dirty map、shmem fuzzing 等 feature）。

### 執行一次的協定

握手完畢後，每 iteration：

```
fuzzer              forkserver
──────              ──────────
write ctrl: 4 bytes  →
(告訴 forkserver go)
                     read ctrl
                     fork()
                        │
                        ├─── child 跑 target
                        │
                     read child_pid     ← write
  child_pid         ←─── write
                     waitpid(child)
  status            ←─── write
```

- 第一個 `write`：通常是 0x00000000 就表示 go。
- `child_pid`：fuzzer 要知道，不然它要殺 child 時不知道殺誰（timeout）。
- `status`：waitpid 的返回值 — SIGSEGV? exit code?

只有 4+4+4 = 12 bytes 的通訊，很便宜。

## 實際長怎樣：`afl-compiler-rt.o.c` 片段

簡化版的 forkserver code（實際有更多 AFL++ 特殊能力）：

```c
static void __afl_start_forkserver(void) {
    u8 tmp[4] = {0, 0, 0, 0};

    // 1. 向 fuzzer 打招呼
    if (write(FORKSRV_FD + 1, tmp, 4) != 4) return;

    while (1) {
        u32 was_killed;

        // 2. 等 fuzzer 的信號
        if (read(FORKSRV_FD, &was_killed, 4) != 4) _exit(1);

        // 3. fork 乾淨的 child
        child_pid = fork();
        if (child_pid < 0) _exit(4);

        if (!child_pid) {
            // child: 關掉 pipe、繼續執行原 main()
            close(FORKSRV_FD);
            close(FORKSRV_FD + 1);
            return;   // return 回原本 main() 前的劫持點，繼續跑
        }

        // parent: 報 child_pid、等完、報 status
        if (write(FORKSRV_FD + 1, &child_pid, 4) != 4) _exit(5);

        int status;
        if (waitpid(child_pid, &status, 0) < 0) _exit(6);

        if (write(FORKSRV_FD + 1, &status, 4) != 4) _exit(7);
    }
}
```

幾個細節：

- **bitmap 不用重設**：child 繼承 parent 的 SHM attachment，child 寫 bitmap = fuzzer 看得見。fuzzer 每 iteration 開始前自己 `memset(trace_bits, 0)`。
- **child 繼承了 libc 初始化狀態**：所有 `.ctors` 已經跑過。這是主要的效能收益來源。
- **Forkserver 本身不跑 target code**：它只做 fork + wait。

## 插樁怎麼把 forkserver 放進去

兩種時機：

### 選項 1：`.init_array` / constructor

`afl-compiler-rt.o.c` 裡有一個 `__attribute__((constructor))` 函式，會在 `main()` 前、`.init_array` 階段自動執行。它做：

1. `shmat()` 連上 fuzzer 開的 SHM（id 從 `__AFL_SHM_ID` env 拿）。
2. 設 `__afl_area_ptr = shm_addr`，讓後續 instrumentation 寫對地方。
3. 呼叫 `__afl_start_forkserver()` — 進入 loop，不再回來（除非是 child）。

child 從 `fork()` return 後，才真的跑到 `main()`。

### 選項 2：Deferred forkserver

問題來了：有些 target 在 `main()` 進入後還會做一堆重工作 — 讀 config、load 大 data file、parse command-line。如果 forkserver 在 `main()` 前就啟動，這些工作每次 iteration 都要重做。

解法：**把 forkserver 啟動點往後挪**。你在 target source 裡手動呼叫 `__AFL_INIT()`：

```c
int main(int argc, char **argv) {
    load_config();        // 這些重工作
    load_dictionary();    // 只做一次
    __AFL_INIT();         // ← forkserver 在這裡起動
    
    // 每個 fuzz iteration 才跑以下
    read_input(argv[1]);
    parse();
    process();
    return 0;
}
```

`__AFL_INIT()` 呼叫 `__afl_manual_init()`，後者若還沒初始化 forkserver 就在這裡開。之前的程式碼在第一次 exec 時跑過，後面的 iteration 從 `__AFL_INIT` 後繼續 — 省一堆重工作。

這叫 **deferred forkserver**，是 AFL 對有昂貴 startup 的 target 的殺手鐧。實測對 LLVM、SQLite 這類 target 有 2–5x 提速。

## 和 Persistent mode 的關係

Deferred forkserver 把 fork 點往後挪，但每次 iteration 還是 fork + exec 一次 target 函式。**Persistent mode 更進一步**：連 fork 都省掉，把一次執行包在 loop 裡。詳見 Ch 13。

粗略效能階梯：

| 模式 | exec/s（典型） |
|---|---|
| 純 fork + exec | 100–1000 |
| Forkserver（main 前） | 1000–5000 |
| Deferred forkserver | 5000–20000 |
| Persistent mode | 10000–100000+ |

## 常見誤解

- **「forkserver 是獨立 process」**：某種意義上是（main() 前的那個 process 留在 forkserver loop 裡），但它和 target 原始 binary 是同一個 image，只是 entry point 被 hijack 了。
- **「每次 fork 的 child 完全乾淨」**：**不是**。child 繼承了 parent 的 globals、已 load 的 library、已 open 的 file descriptors。「乾淨」只是相對於上一次 iteration 的 mutation 結果 — 因為 mutation 發生在 child 側，child exit 後 parent 不受影響。
- **「global static 在 iteration 間會被保留」**：在 fork + exec 的版本是保留的（因為每次都 exec），forkserver 版本 child 用完就 exit — 也是每次獨立。**persistent mode 才會有 state leak 問題**。

## 自我檢核

- [ ] 能解釋 fork vs exec 的成本差異、為什麼 fork 便宜
- [ ] 能畫出 forkserver / fuzzer / child 三者的 pipe 通訊
- [ ] 知道 `__AFL_INIT()` 是做什麼的、deferred forkserver 用在什麼場景
- [ ] 能說出 child 繼承了什麼、不繼承什麼
- [ ] 記得 fd 198 / 199 是 control / status pipe

下一章進 fuzzer 本體 — queue entry 怎麼管理、什麼是 favored minset。

→ [Ch 8 Corpus 生命週期與 favored minset](./08-corpus-lifecycle.md)
