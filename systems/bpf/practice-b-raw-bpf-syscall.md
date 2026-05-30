# 練習 B — 裸 BPF syscall 實作

> **目標**：把 Ch 6–12 學到的東西——program type、maps、helper、syscall 序列——用原始 `bpf()` syscall 實作一個完整的「系統呼叫計數器」，不使用 libbpf、BCC 或任何框架。

## 背景與動機

用框架寫 BPF 程式很方便，但當框架出錯的時候，你不知道底層發生了什麼。這個練習強迫你親手寫每一個 `bpf()` syscall，把 BPF bytecode 直接寫在 C 陣列裡，手動處理 map fd 和 attach。

完成後你會深刻理解：框架幫你省掉了哪些繁瑣但關鍵的工作，以及出問題時要去哪裡查。

## 任務規格

**實作目標**：一個 `syscall_counter` 程式，追蹤系統上每個 syscall 被呼叫了多少次，每隔 2 秒輸出 top 10 最多被呼叫的 syscall。

**技術限制**：
- **不能** 使用 libbpf、BCC 或任何第三方 BPF 框架
- **可以** 使用標準 C 函式庫（`<stdio.h>`, `<stdlib.h>`, `<string.h>` 等）
- BPF 程式的 bytecode 必須直接以 `struct bpf_insn[]` 陣列形式寫在 C 程式裡
- Map 操作只能用 `bpf()` syscall（`BPF_MAP_CREATE`, `BPF_MAP_UPDATE_ELEM`, `BPF_MAP_LOOKUP_ELEM`）
- Attach 只能用 `perf_event_open` + `ioctl`（`PERF_EVENT_IOC_SET_BPF`）

**預期輸出**：

```
=== Top 10 Syscalls at 2025-01-01 12:00:02 ===
Syscall  0 (read):                    15234 calls
Syscall  1 (write):                   12891 calls
Syscall  3 (close):                    9823 calls
Syscall  4 (stat):                     7654 calls
...
```

**驗收標準**：
- 程式能正常 compile（`gcc -o syscall_counter syscall_counter.c`）
- 程式能 load BPF program 並 attach 到 `raw_syscalls/sys_enter` tracepoint
- 每 2 秒輸出一次 top 10 結果
- Ctrl+C 後程式正常退出，BPF program 被 detach

## 如果你卡住了

1. 先把「建立 map」和「update + lookup map」這兩步獨立跑通，再加 BPF program 載入
2. BPF bytecode 的部分可以先用 clang 編譯一個相似的 `.bpf.c`，用 `llvm-objdump -d` 看 bytecode，再手工翻譯成 `bpf_insn` 陣列
3. `log_buf` 是你最好的朋友——`BPF_PROG_LOAD` 失敗時，verifier 的錯誤訊息在這裡
4. 查 `include/uapi/linux/bpf.h` 找所有需要的 BPF opcode 定義（`BPF_ALU64`, `BPF_MOV`, `BPF_CALL` 等）
5. `/sys/kernel/debug/tracing/events/raw_syscalls/sys_enter/id` 是 tracepoint 的 id

## 實作步驟建議

### Step 1：syscall wrapper + map 建立（驗收：bpftool map list 能看到你的 map）

實作 `bpf()` syscall wrapper 和 `BPF_MAP_CREATE` 呼叫。建立一個 `BPF_MAP_TYPE_ARRAY`（key = syscall nr，value = u64 counter）。

驗收：`sudo bpftool map list` 應該能看到你建立的 map。

### Step 2：Map 讀寫測試（驗收：手動 update 再 lookup 能得到正確值）

實作 `BPF_MAP_UPDATE_ELEM` 和 `BPF_MAP_LOOKUP_ELEM`。用 key=1, value=42 測試。

### Step 3：BPF bytecode 設計

這個 BPF 程式需要做：
1. 讀取 syscall 號碼（從 tracepoint context `args[0]`）
2. 用 syscall 號碼作為 key 查找 array map
3. 如果找到，把 value 加 1
4. Return 0

對應的 C 邏輯（轉成 bytecode）：

```c
/* 這是邏輯，你要手寫成 bpf_insn 陣列 */
int count_syscall(struct trace_event_raw_sys_enter *ctx) {
    u32 key = (u32)ctx->id;  /* syscall 號碼在 offset 8 */
    u64 *val = bpf_map_lookup_elem(&map, &key);
    if (val)
        __sync_fetch_and_add(val, 1);
    return 0;
}
```

提示：`raw_syscalls/sys_enter` tracepoint 的 context 裡，syscall 號碼在偏移 8（`struct trace_event_raw_sys_enter.id`）。你需要：
- `ldxw r1, [r1 + 8]`：讀取 syscall 號碼（32-bit）
- 把 key 存到 stack（`r10 - 4`）
- 設定 r1 = map fd，r2 = &key（stack ptr）
- 呼叫 bpf_map_lookup_elem（helper #1）
- 檢查 r0 是否為 NULL
- 如果非 NULL：`lock *(u64 *)(r0 + 0) += 1`（atomic add）
- Return 0

### Step 4：BPF program 載入（驗收：沒有 verifier 錯誤，能看到 prog id）

用 `BPF_PROG_LOAD` 載入你的 bytecode。`prog_type = BPF_PROG_TYPE_TRACEPOINT`。確認 verifier log 沒有錯誤。

```
sudo bpftool prog list | grep <your_prog_name>
```

### Step 5：Attach 到 tracepoint（驗收：run_cnt 隨著 syscall 增加）

讀取 `/sys/kernel/debug/tracing/events/raw_syscalls/sys_enter/id`，用 `perf_event_open` + `PERF_EVENT_IOC_SET_BPF` + `PERF_EVENT_IOC_ENABLE` attach。

執行 `ls` 等命令，然後查 `bpftool prog show id <id>` 的 `run_cnt`，確認有增加。

### Step 6：讀取結果並輸出（驗收：每 2 秒印出 top 10）

每 2 秒遍歷 map 的 key 0–511（常見 syscall 號碼範圍），找出 count 最多的 10 個，輸出。

## 完整參考解答

**先做完再看！**

<details>
<summary>點開參考實作（syscall_counter.c）</summary>

```c
/* syscall_counter.c — 用 raw bpf() syscall 實作的 syscall 計數器 */
#define _GNU_SOURCE
#include <linux/bpf.h>
#include <linux/perf_event.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <sys/ioctl.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <signal.h>
#include <time.h>

/* ==================== syscall wrapper ==================== */
static inline int sys_bpf(int cmd, union bpf_attr *attr, unsigned int size)
{
    return (int)syscall(__NR_bpf, cmd, attr, size);
}

/* ==================== BPF bytecode ==================== */
/*
 * BPF 程式邏輯（C pseudo-code）：
 *   int count(struct trace_event_raw_sys_enter *ctx) {
 *       u32 key = (u32)ctx->id;   // offset 8 in ctx
 *       u64 *val = bpf_map_lookup_elem(&map, &key);
 *       if (val) lock *(u64*)(val) += 1;
 *       return 0;
 *   }
 */

/* BPF insn 輔助 macro（來自 <linux/bpf.h> 的簡化版）*/
#define BPF_ALU64_IMM(OP, DST, IMM)                     \
    ((struct bpf_insn) {                                \
        .code  = BPF_ALU64 | BPF_OP(OP) | BPF_K,       \
        .dst_reg = DST, .src_reg = 0,                   \
        .off = 0, .imm = IMM })

#define BPF_MOV64_REG(DST, SRC)                         \
    ((struct bpf_insn) {                                \
        .code  = BPF_ALU64 | BPF_MOV | BPF_X,          \
        .dst_reg = DST, .src_reg = SRC,                 \
        .off = 0, .imm = 0 })

#define BPF_MOV64_IMM(DST, IMM)                         \
    ((struct bpf_insn) {                                \
        .code  = BPF_ALU64 | BPF_MOV | BPF_K,          \
        .dst_reg = DST, .src_reg = 0,                   \
        .off = 0, .imm = IMM })

#define BPF_STX_MEM(SIZE, DST, SRC, OFF)               \
    ((struct bpf_insn) {                                \
        .code  = BPF_STX | BPF_SIZE(SIZE) | BPF_MEM,   \
        .dst_reg = DST, .src_reg = SRC,                 \
        .off = OFF, .imm = 0 })

#define BPF_LDX_MEM(SIZE, DST, SRC, OFF)               \
    ((struct bpf_insn) {                                \
        .code  = BPF_LDX | BPF_SIZE(SIZE) | BPF_MEM,   \
        .dst_reg = DST, .src_reg = SRC,                 \
        .off = OFF, .imm = 0 })

#define BPF_JMP_IMM(OP, DST, IMM, OFF)                 \
    ((struct bpf_insn) {                                \
        .code  = BPF_JMP | BPF_OP(OP) | BPF_K,         \
        .dst_reg = DST, .src_reg = 0,                   \
        .off = OFF, .imm = IMM })

#define BPF_CALL_HELPER(FUNC_ID)                        \
    ((struct bpf_insn) {                                \
        .code  = BPF_JMP | BPF_CALL,                   \
        .dst_reg = 0, .src_reg = 0,                    \
        .off = 0, .imm = FUNC_ID })

#define BPF_EXIT_INSN()                                 \
    ((struct bpf_insn) {                                \
        .code  = BPF_JMP | BPF_EXIT,                   \
        .dst_reg = 0, .src_reg = 0,                    \
        .off = 0, .imm = 0 })

/* Atomic add: lock *(u64 *)(dst + off) += src */
#define BPF_ATOMIC_ADD(DST, OFF, SRC)                  \
    ((struct bpf_insn) {                               \
        .code  = BPF_STX | BPF_ATOMIC | BPF_DW,       \
        .dst_reg = DST, .src_reg = SRC,                \
        .off = OFF, .imm = BPF_ADD })

/* LD_MAP_FD: 載入 map fd（64-bit immediate，佔兩個 slot）*/
#define BPF_LD_MAP_FD(DST, MAP_FD) \
    ((struct bpf_insn) {           \
        .code = BPF_LD | BPF_DW | BPF_IMM, \
        .dst_reg = DST, .src_reg = BPF_PSEUDO_MAP_FD, \
        .off = 0, .imm = (__u32)(MAP_FD) }), \
    ((struct bpf_insn) {           \
        .code = 0, .dst_reg = 0, .src_reg = 0, \
        .off = 0, .imm = 0 })

static int build_prog(int map_fd, struct bpf_insn **out_insns, int *out_cnt)
{
    /*
     * BPF 寄存器分配：
     *   r1 = ctx（tracepoint 的 args 指標）
     *   r2 = &key（stack 上）
     *   r6 = 儲存 ctx（因為 helper call 後 r1 失效）
     *   r0 = 回傳值（bpf_map_lookup_elem 的結果）
     */
    static struct bpf_insn insns[64];
    int i = 0;

    /* r6 = r1（儲存 ctx，因為 call 之後 r1 失效）*/
    insns[i++] = BPF_MOV64_REG(6, 1);

    /* r7 = 0（用於 key 的初始值）*/
    insns[i++] = BPF_MOV64_IMM(7, 0);

    /* 從 ctx 的 offset 8 讀 syscall id（32-bit）*/
    /* r7 = *(u32 *)(r6 + 8)：ctx->id（raw_syscalls 的 syscall nr）*/
    insns[i++] = BPF_LDX_MEM(BPF_W, 7, 6, 8);

    /* 把 key 存到 stack：*(u32 *)(r10 - 4) = r7 */
    insns[i++] = BPF_STX_MEM(BPF_W, 10, 7, -4);

    /* r1 = map fd（LD_MAP_FD 用兩個 slot）*/
    insns[i++] = (struct bpf_insn){
        .code = BPF_LD | BPF_DW | BPF_IMM,
        .dst_reg = 1, .src_reg = BPF_PSEUDO_MAP_FD,
        .off = 0, .imm = map_fd,
    };
    insns[i++] = (struct bpf_insn){ 0 };  /* wide instruction 的第二個 slot */

    /* r2 = r10 - 4（&key 在 stack）*/
    insns[i++] = BPF_MOV64_REG(2, 10);
    insns[i++] = BPF_ALU64_IMM(BPF_ADD, 2, -4);

    /* call bpf_map_lookup_elem（helper id = 1）*/
    insns[i++] = BPF_CALL_HELPER(1);

    /* if r0 == 0, jump to exit（NULL check）*/
    insns[i++] = BPF_JMP_IMM(BPF_JEQ, 0, 0, 2);

    /* r1 = 1（atomic add 的增量）*/
    insns[i++] = BPF_MOV64_IMM(1, 1);

    /* lock *(u64 *)(r0 + 0) += r1（atomic add）*/
    insns[i++] = BPF_ATOMIC_ADD(0, 0, 1);

    /* r0 = 0（return value）*/
    insns[i++] = BPF_MOV64_IMM(0, 0);

    /* exit */
    insns[i++] = BPF_EXIT_INSN();

    *out_insns = insns;
    *out_cnt = i;
    return 0;
}

/* ==================== 主程式 ==================== */

static volatile int running = 1;
static void handle_sig(int sig) { running = 0; }

int main(void)
{
    signal(SIGINT, handle_sig);
    signal(SIGTERM, handle_sig);

    /* 1. 建立 array map（key = syscall nr, value = count）*/
    union bpf_attr map_attr;
    memset(&map_attr, 0, sizeof(map_attr));
    map_attr.map_type    = BPF_MAP_TYPE_ARRAY;
    map_attr.key_size    = sizeof(__u32);
    map_attr.value_size  = sizeof(__u64);
    map_attr.max_entries = 512;
    strncpy(map_attr.map_name, "sc_counter", sizeof(map_attr.map_name)-1);

    int map_fd = sys_bpf(BPF_MAP_CREATE, &map_attr, sizeof(map_attr));
    if (map_fd < 0) { perror("map create"); return 1; }
    printf("map fd = %d\n", map_fd);

    /* 2. 載入 BPF prog */
    struct bpf_insn *insns;
    int insn_cnt;
    build_prog(map_fd, &insns, &insn_cnt);

    char log_buf[65536];
    union bpf_attr prog_attr;
    memset(&prog_attr, 0, sizeof(prog_attr));
    prog_attr.prog_type = BPF_PROG_TYPE_TRACEPOINT;
    prog_attr.insns     = (__u64)(uintptr_t)insns;
    prog_attr.insn_cnt  = insn_cnt;
    prog_attr.license   = (__u64)(uintptr_t)"GPL";
    prog_attr.log_buf   = (__u64)(uintptr_t)log_buf;
    prog_attr.log_size  = sizeof(log_buf);
    prog_attr.log_level = 1;
    strncpy(prog_attr.prog_name, "sc_tracer", sizeof(prog_attr.prog_name)-1);

    int prog_fd = sys_bpf(BPF_PROG_LOAD, &prog_attr, sizeof(prog_attr));
    if (prog_fd < 0) {
        fprintf(stderr, "prog load failed: %s\n", strerror(errno));
        fprintf(stderr, "verifier log:\n%s\n", log_buf);
        close(map_fd);
        return 1;
    }
    printf("prog fd = %d\n", prog_fd);

    /* 3. 讀取 tracepoint id */
    FILE *f = fopen("/sys/kernel/debug/tracing/events/raw_syscalls/sys_enter/id", "r");
    if (!f) { perror("open tp id"); return 1; }
    int tp_id;
    fscanf(f, "%d", &tp_id);
    fclose(f);
    printf("tracepoint id = %d\n", tp_id);

    /* 4. perf_event_open */
    struct perf_event_attr pattr;
    memset(&pattr, 0, sizeof(pattr));
    pattr.type        = PERF_TYPE_TRACEPOINT;
    pattr.sample_type = PERF_SAMPLE_RAW;
    pattr.config      = tp_id;
    pattr.wakeup_events = 1;

    int pfd = (int)syscall(__NR_perf_event_open, &pattr, -1, 0, -1, 0);
    if (pfd < 0) { perror("perf_event_open"); return 1; }

    if (ioctl(pfd, PERF_EVENT_IOC_SET_BPF, prog_fd) < 0) {
        perror("SET_BPF"); return 1;
    }
    if (ioctl(pfd, PERF_EVENT_IOC_ENABLE, 0) < 0) {
        perror("ENABLE"); return 1;
    }
    printf("attached to raw_syscalls:sys_enter\n");

    /* 5. 每 2 秒輸出 top 10 */
    while (running) {
        sleep(2);

        /* 找 top 10 */
        typedef struct { __u32 nr; __u64 cnt; } entry_t;
        entry_t top[10];
        memset(top, 0, sizeof(top));
        int min_idx = 0;

        for (__u32 key = 0; key < 512; key++) {
            __u64 val;
            union bpf_attr la;
            memset(&la, 0, sizeof(la));
            la.map_fd = map_fd;
            la.key    = (__u64)(uintptr_t)&key;
            la.value  = (__u64)(uintptr_t)&val;
            if (sys_bpf(BPF_MAP_LOOKUP_ELEM, &la, sizeof(la)) < 0) continue;
            if (val > top[min_idx].cnt) {
                top[min_idx] = (entry_t){ key, val };
                /* 更新 min_idx */
                for (int j = 0; j < 10; j++)
                    if (top[j].cnt < top[min_idx].cnt) min_idx = j;
            }
        }

        /* 排序 top 10（簡單插入排序）*/
        for (int a = 0; a < 10; a++)
            for (int b = a+1; b < 10; b++)
                if (top[b].cnt > top[a].cnt) {
                    entry_t t = top[a]; top[a] = top[b]; top[b] = t;
                }

        time_t now = time(NULL);
        printf("\n=== Top 10 Syscalls at %s", ctime(&now));
        for (int j = 0; j < 10; j++) {
            if (top[j].cnt == 0) break;
            printf("Syscall %3u: %llu calls\n", top[j].nr, top[j].cnt);
        }
    }

    /* 6. Cleanup */
    ioctl(pfd, PERF_EVENT_IOC_DISABLE, 0);
    close(pfd);
    close(prog_fd);
    close(map_fd);
    printf("\ncleaned up.\n");
    return 0;
}
```

**編譯和執行**：

```bash
gcc -O2 -o syscall_counter syscall_counter.c
sudo ./syscall_counter
# 在另一個 terminal 執行一些命令（ls, cat, etc.）
# 觀察輸出
```

**解答說明**：

1. BPF bytecode 手寫最難的地方是「wide instruction」（`BPF_LD_MAP_FD`）——它佔兩個 slot，第一個 slot 的 imm 是 map fd 的低 32 bits，第二個 slot 的 imm 是高 32 bits（這裡是 0 因為 fd 是小整數）

2. `BPF_ATOMIC_ADD` 的 opcode 是 `BPF_STX | BPF_ATOMIC | BPF_DW`，imm 是 `BPF_ADD`（這是 Linux 5.12 之後的語法；舊版本用不同的 opcode）

3. verifier 要求讀 ctx 之前，r1 必須是 `PTR_TO_CTX`；所以第一步要把 r1 存到 r6（callee-saved），之後再用 r6 存取 ctx

</details>

## 測試用案例

| 測試項目 | 預期結果 |
|---|---|
| `sudo bpftool map list` | 能看到 `sc_counter` map |
| `sudo bpftool prog list` | 能看到 `sc_tracer` program |
| `sudo bpftool prog show name sc_tracer` | `run_cnt` 隨 syscall 增加 |
| Top 10 輸出 | syscall 0（read）和 1（write）通常是最多的 |
| Ctrl+C 後 `bpftool prog list` | `sc_tracer` 消失（沒有 pin）|

## 延伸挑戰（加分）

- **挑戰一**：把輸出加上 syscall 名稱（需要一個 syscall nr → name 的對應表，從 `/usr/include/asm/unistd_64.h` 或 `/usr/include/sys/syscall.h` 提取）

- **挑戰二**：把 BPF program attach 到**所有** CPU（現在只 attach 到 CPU 0）；提示：`perf_event_open` 的 `cpu` 參數設成 -1 需要 `pid != -1`，或者為每個 CPU 分別 attach

- **挑戰三**：加入 delta 功能——不顯示累計計數，而是顯示過去 2 秒的增量（需要每次 snapshot 後清零或保存上次的值）

- **挑戰四**：用 PERCPU_ARRAY 取代 ARRAY，在 userspace 把各 CPU 的 count 加總，並比較效能

## 自我檢核

- [ ] 能解釋 `BPF_LD_MAP_FD` 為什麼佔兩個 instruction slot
- [ ] 能說出 r1–r5 在 helper call 後的狀態，以及為什麼要先把 ctx 存到 r6
- [ ] 知道 `PERF_EVENT_IOC_SET_BPF` 和 `PERF_EVENT_IOC_ENABLE` 的執行順序，以及搞反了會怎樣
- [ ] 能解釋 close(pfd) 之後 BPF program 為什麼停止執行

→ [Ch 13 bpftrace：動態腳本語言](./13-bpftrace.md)
