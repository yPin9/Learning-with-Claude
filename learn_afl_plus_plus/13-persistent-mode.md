# Ch 13 — Persistent mode：同一個 process 跑一萬次

> 目標：拆 `__AFL_LOOP(N)` 的巨集展開與 forkserver 的協作；解釋為什麼可以比 fork-per-exec 快一個數量級；講清楚「無狀態殘留」這個義務誰來負責、踩雷時會怎麼表現。

## 效能天花板的下一步

Ch 7 講的 forkserver 把每 iteration 從 fork+exec 變成 fork，throughput 從百等級升到千等級。Deferred forkserver 再砍掉 main() 前的重工作，再升一級。

但 fork() 本身也有成本 — 幾百微秒。如果 target 的 parse 函式本身才幾微秒，fork 就主宰了 90%+ 的時間。**對這類 target，fork 都該省掉**。

**Persistent mode** 的 idea：把 target 的核心邏輯用 `while` loop 包起來，同一個 process 反覆跑 N 次，fork 只有第一次。

## 概念上對比

### Fork-based（普通 forkserver）

```
forkserver ──fork──▶ child 1 (run once, exit)
           ──fork──▶ child 2 (run once, exit)
           ──fork──▶ child 3 (run once, exit)
           ...
```

每次 iteration 一個 child。throughput 上限由 fork 成本決定。

### Persistent mode

```
forkserver ──fork──▶ child (run, run, run, ..., run 10000 times, exit)
           ──fork──▶ child (run, run, ..., exit)
```

一個 child 跑 N 次才 exit。N 通常 1000–100000。

## 寫法：`__AFL_LOOP(N)`

你需要改 target source：

```c
int main(int argc, char **argv) {
    // 重 startup 工作：load config、open DB、...
    init_target();

    __AFL_INIT();   // ← forkserver 在這裡啟動（deferred）

    while (__AFL_LOOP(10000)) {
        // 這段是每 iteration 會重跑的部分
        char buf[65536];
        int len = read(0, buf, sizeof(buf));   // 讀 input（fuzzer 給的）
        parse_and_process(buf, len);
    }

    return 0;
}
```

`__AFL_LOOP(10000)` 是一個巨集。它的展開（簡化版）：

```c
#define __AFL_LOOP(count) \
    ((__afl_persistent_loop(count)) ? 1 : 0)
```

`__afl_persistent_loop()` 在 `afl-compiler-rt.o.c`：

```c
static u32 __afl_persistent_loop(u32 max_cnt) {
    static u8 first_pass = 1;
    static u32 cycle_cnt;

    if (first_pass) {
        // 第一次呼叫 — 進 forkserver
        memset(__afl_area_ptr, 0, MAP_SIZE);   // 重設 bitmap
        __afl_area_ptr[0] = 1;   // 非零保持，確保 "executed at least once"
        cycle_cnt = max_cnt;
        first_pass = 0;
        return 1;
    }

    if (--cycle_cnt) {
        // 還沒跑完 — 通知 fuzzer 上一輪結束，等下一個 input
        raise(SIGSTOP);   // 暫停 child，parent 拿 bitmap，fuzzer 判斷
        __afl_area_ptr[0] = 1;
        return 1;
    }

    // N 輪跑完 — return 0 結束 loop，process exit
    return 0;
}
```

關鍵在 `raise(SIGSTOP)`：讓 child 停在 loop 開頭，parent (forkserver) 發現 child stopped，把 bitmap 給 fuzzer 判斷；fuzzer 下一步可以：

- `SIGCONT` → child 繼續跑下一輪 loop。
- `SIGKILL` → 這個 child 不用了，fork 新的。

## 為什麼快

比較一次 iteration 的成本：

| 階段 | 普通 forkserver | Persistent mode |
|---|---|---|
| fork() | ✓ 幾百 μs | ✗ |
| setup global state | ✓（child 繼承 parent，但 page fault） | ✗（沒做過就 in-memory 了） |
| 跑 target 邏輯 | ✓ | ✓ |
| exit + waitpid | ✓ 幾十 μs | ✗（只 SIGSTOP） |
| SIGSTOP / SIGCONT | ✗ | ✓ 幾 μs |
| 每 N 次做一次 fork | 每次 | 1/N 次 |

實測 throughput：**提升 3–10 倍**。對微小 target 甚至 20x。

## 「無狀態殘留」的義務

Persistent mode 的大陷阱 — 同一個 process 跑 N 次，**global state 會累積**。最常見的失敗模式：

### Case 1：沒釋放的 memory

```c
while (__AFL_LOOP(10000)) {
    char *buf = malloc(1024);
    read(0, buf, 1024);
    parse(buf);
    // 忘記 free(buf)
}
```

每 iteration leak 1KB，跑 10000 次累積 10MB。久了 OOM，child exit，fuzzer 看起來 throughput 忽快忽慢。

### Case 2：靜態狀態

```c
static int counter = 0;

void parse(char *buf) {
    counter++;
    if (counter == 100) crash();
}
```

counter 會跨 iteration 累積。第 100 次 iteration 觸發的「bug」根本不是 bug，而是持續累積的副作用。**這種假 crash 會浪費很多 triage 時間**。

### Case 3：global 資源

```c
while (__AFL_LOOP(10000)) {
    FILE *f = fopen("/tmp/log", "a");
    fprintf(f, "...");
    // 忘記 fclose(f)
}
```

fd 洩漏，跑一段時間就 EMFILE。

### 義務清單

每 iteration 開始時，你必須保證：

- 所有 heap memory 釋放。
- 所有 fd 關閉。
- 所有 global / static 變數 reset。
- 所有 thread-local 狀態清除。
- lib 內部的 static state 清理（SSL session cache、zlib internal state 等）。

最後一項最難 — 有些 lib 的 internal state 沒公開 reset API。對這種 target，**persistent mode 不可用**。

## 實務上怎麼寫 harness

典型的 harness pattern：

```c
int main(int argc, char **argv) {
    // 一次性 setup
    global_init();

    __AFL_INIT();

    // 從 stdin 讀的 wrapper
    u8 *buf = __AFL_FUZZ_TESTCASE_BUF;   // AFL++ 提供的 ring buffer
    while (__AFL_LOOP(10000)) {
        int len = __AFL_FUZZ_TESTCASE_LEN;   // 這一輪的 input 長度

        // 一次性工作在 loop 外，每 iter 只做 target 核心
        target_function(buf, len);

        // 如果 target 內部會累積 state — 手動 reset
        target_reset_state();
    }
    return 0;
}
```

`__AFL_FUZZ_TESTCASE_BUF` / `__AFL_FUZZ_TESTCASE_LEN` 是 AFL++ 提供的 shared memory 機制 — input 不透過 stdin 讀，直接 shmem 共享，比 read() 還快。

## 和 forkserver 的協作

Persistent mode 是 forkserver 的延伸，不是替代。整體流程：

```
afl-fuzz
   │ exec target
   ▼
target 初始化（deferred 到 __AFL_INIT）
   │
   ▼
__afl_start_forkserver                  ┐
   │                                      │ forkserver loop（parent，長期活）
   │ fork ─────▶ child                    │
   ▼             │                        │
等 signal        ▼                        │
   ▲          __afl_persistent_loop       │
   │             │ 第一次                 │
   │             │                        │
   │             ▼ while (loop) {         │
   │                run target once        │
   │                SIGSTOP ──────────────▶│ fuzzer 讀 bitmap
   │                ▲                      │ 決定 SIGCONT / SIGKILL
   │                └──── SIGCONT ◀───────┤
   │                                       │
   │             ... N 次後 exit ─────────▶│ fork 新 child
   │                                       │
   ▼                                       ▼
```

SIGSTOP 信號告訴 parent 「我跑完一輪了」。Parent 通知 fuzzer，等指令。這個 signal-based 機制是 persistent mode 相比 forkserver 的主要新增協定。

## 為什麼要限 N 而不是 while(1)

你可能想問：直接 `while (1)` 不更快？為什麼要限 N？

理由：

1. **記憶體洩漏保險**：即使 harness 寫得小心，lib 內部的 state 還是有可能漸進累積。跑 N 輪後強制換 process，把 leak 歸零。
2. **Page table 壓力**：長跑的 process page table 可能因為 malloc/free 變大，產生 TLB miss。重啟 process 刷新。
3. **崩潰隔離**：如果 child 跑到一半 SIGSEGV，fuzzer 能發現是「這一輪的 input 造成」，但如果是緩慢狀態累積導致的崩潰，fuzzer 很難定位。限 N 減少這種模糊情況。

N 典型值：**1000–100000**。對乾淨 harness 可以拉到 10 萬；對有狀態殘留風險的 lib 要降到 1000 或更低。

## Shared memory fuzzing：再榨一點

除了 `__AFL_LOOP`，AFL++ 還有 `__AFL_FUZZ_TESTCASE_BUF` — 讓 input 透過 shared memory 傳過來，跳過 `read()` 或 `open(file)`：

```c
// 傳統：input 從 stdin
while (__AFL_LOOP(10000)) {
    char buf[65536];
    int len = read(0, buf, sizeof(buf));
    target(buf, len);
}

// Shared memory：input 從 shmem
unsigned char *buf = __AFL_FUZZ_TESTCASE_BUF;
while (__AFL_LOOP(10000)) {
    int len = __AFL_FUZZ_TESTCASE_LEN;
    target(buf, len);
}
```

省掉 `read()` syscall，短 input target 能再快 20–50%。

## 常見誤解

- **「Persistent mode 只能用 source target」**：Frida mode 也支援 persistent（`-O`），但 QEMU mode 較不成熟。
- **「N 設越大越好」**：N 大 → state leak 累積越多。找到能穩定跑完 N 次不崩的最大值即可。
- **「Persistent 絕對比 forkserver 快」**：對 target 本身很慢（>10ms）的情境，省 fork 的收益被稀釋，持平甚至更慢（因為 signal 機制也有成本）。

## 自我檢核

- [ ] 能畫出 `__AFL_LOOP(N)` 和 forkserver 的 signal 協作流程
- [ ] 能列舉 persistent mode 下的 state leak 地雷（heap、fd、static、lib 內部）
- [ ] 知道 SIGSTOP / SIGCONT 在這個機制裡的角色
- [ ] 記得 `__AFL_FUZZ_TESTCASE_BUF` 是 shared memory fuzzing 的進一步加速
- [ ] 能說出 persistent mode 什麼時候該用、什麼時候不該用

下一章進 custom mutator — fuzzer 和你寫的 mutator 合作的 API。

→ [Ch 14 Custom mutator API](./14-custom-mutator.md)
