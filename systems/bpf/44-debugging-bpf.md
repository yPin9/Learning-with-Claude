# Ch 44 — Debugging：verifier 錯誤與 bpf_printk

> **目標**：建立系統性的 BPF debugging 工作流程——讀懂 verifier log（從錯誤訊息定位到 source line）、用 bpf_printk 做 printk-style debugging、用 bpftool prog dump 分析執行狀態，以及常見的 verifier 拒絕模式和修法。

## Debugging 工具箱總覽

```
BPF Debugging 工具：

1. Verifier log（最重要）
   - 載入失敗時：描述哪條指令被拒絕，以及原因
   - 透過 log_level 控制詳細程度

2. bpf_printk / bpf_trace_printk
   - 在 BPF 程式裡輸出 debug 訊息
   - 讀取：sudo cat /sys/kernel/debug/tracing/trace_pipe

3. bpftool prog dump xlated
   - 顯示 BPF 指令，帶 source line 對應（有 BTF 時）

4. bpftool prog dump jited
   - 顯示 JIT 後的 native code

5. bpftool prog profile
   - 顯示 BPF program 的 instruction 執行統計

6. bpftool prog run（BPF_PROG_TEST_RUN）
   - 用測試 packet 觸發 BPF program
```

## 讀懂 Verifier Log

**開啟 verbose verifier log**：

```c
/* libbpf：在 open_opts 裡設定 verifier log */
struct bpf_object_open_opts opts = {
    .sz               = sizeof(opts),
    .kernel_log_size  = 1 << 20,  /* 1 MB log buffer */
    .kernel_log_level = 2,        /* 2 = verbose，3 = 最詳細 */
};
char *log = malloc(1 << 20);
opts.kernel_log_buf = log;

struct myprog_bpf *skel = myprog_bpf__open_opts(&opts);
if (!skel) { fprintf(stderr, "verifier log:\n%s\n", log); }
```

**典型的 verifier log 格式**：

```
0: (bf) r7 = r1                        ; r7 = ctx（tracepoint）
1: (b7) r6 = 0                         ; r6 = 0
2: (85) call bpf_get_current_task_btf#170
3: (bf) r8 = r0                        ; r8 = task
4: (15) if r8 == 0x0 goto pc+20       ; NULL check
; u64 start = BPF_CORE_READ(task, ...);
5: (79) r1 = *(u64 *)(r8 +3984)       ; read task->se.exec_start（CO-RE relocate）
...
42: (79) r1 = *(u64 *)(r0 +0)         ; 嘗試讀 r0 的內容
R0 invalid mem access 'map_value_or_null'
  ; 原因：r0 是 map lookup 的回傳值，還沒做 NULL check

processed 43 insns (limit 1000000) max_states_per_insn 0 total_states 1 peak_states 1
```

**讀 log 的步驟**：

1. 找 `R<N> invalid mem access` 或 `cannot access` 等錯誤關鍵字
2. 看前面的指令，找出那個 register 最後一次被賦值（map lookup / helper call）
3. 向上追蹤，找出為什麼那個 register 的型別不符合
4. 在 source code 裡找到對應的操作，加上必要的 check

## 常見 Verifier 錯誤和修法

### 錯誤一：`R0 type=map_value_or_null expected=map_value`

**原因**：map lookup 後沒有 NULL check。

**修法**：

```c
/* 錯誤 */
u64 *val = bpf_map_lookup_elem(&map, &key);
*val += 1;  // REJECTED

/* 正確 */
u64 *val = bpf_map_lookup_elem(&map, &key);
if (!val) return 0;  // NULL check
*val += 1;  // OK
```

### 錯誤二：`R1 !read_ok`（或 `!write_ok`）

**原因**：使用了未初始化的 register。

```c
/* 錯誤 */
u32 key;  /* 未初始化 */
bpf_map_lookup_elem(&map, &key);  // verifier 看到 key 是 NOT_INIT

/* 正確 */
u32 key = 0;
bpf_map_lookup_elem(&map, &key);
```

### 錯誤三：`invalid mem access 'inv'` 或 `R1 invalid mem access 'scalar'`

**原因**：嘗試對 SCALAR_VALUE 做 dereference。

```c
/* 錯誤 */
u64 ptr = map_lookup_result;
*(u64 *)ptr = 42;  // ptr 是 scalar，不是 pointer

/* 正確：ptr 必須是 map_value pointer 型別 */
u64 *ptr = bpf_map_lookup_elem(&map, &key);
if (!ptr) return 0;
*ptr = 42;
```

### 錯誤四：`combined stack size N exceeds 512`

**原因**：BPF stack 超過 512 bytes。

```c
/* 錯誤 */
char buf[600];  // 太大

/* 修法：用 per-CPU array 當 heap */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, char[600]);
} heap SEC(".maps");

u32 k = 0;
char *buf = bpf_map_lookup_elem(&heap, &k);
if (!buf) return 0;
```

### 錯誤五：`back-edge from insn N to M not allowed`（舊 kernel）

**原因**：kernel 5.3 之前不允許 loop。

```c
/* 修法：使用 unroll pragma */
#pragma unroll
for (int i = 0; i < 10; i++) { ... }

/* 或 kernel 5.17+ 用 bpf_loop() */
bpf_loop(count, my_callback, &data, 0);
```

## bpf_printk Debug

```c
/* 在 BPF 程式裡輸出 debug 訊息 */
bpf_printk("pid=%d map_val=%llu\n", pid, val);

/* 讀取輸出 */
/* sudo cat /sys/kernel/debug/tracing/trace_pipe */
```

**限制**：
- kernel 5.13 之前最多 3 個 format arguments
- 每個 `bpf_printk` 呼叫都有 overhead（約 100–300 ns）
- 不要在 hot path（每個 packet、每個 syscall）使用
- 適合用在 debug build，不適合 production

```c
/* Debug-only logging 模式 */
const volatile bool debug_mode = false;  /* rodata，可從 userspace 設定 */

if (debug_mode)
    bpf_printk("debug: %d\n", val);
```

## bpftool prog dump xlated

```bash
# 顯示帶 source line 的 BPF 指令
sudo bpftool prog dump xlated name my_prog linum

# 輸出：
# int my_prog(struct xdp_md * ctx):
# ; void *data = (void *)(long)ctx->data;  ← source line
#    0: (61) r2 = *(u32 *)(r1 +0)
#    1: (bf) r8 = r2
# ; void *data_end = ...
#    2: (61) r2 = *(u32 *)(r1 +4)
# ; struct ethhdr *eth = data;
# ; if ((void *)(eth + 1) > data_end)      ← 這一行的 bounds check
#    3: (bf) r7 = r8
#    4: (07) r7 += 14                      ← eth + 1（14 bytes）
#    5: (2d) if r7 > r2 goto pc+5
```

## bpftool prog profile

```bash
# 顯示 instruction-level 的執行統計
sudo bpftool prog profile id <prog-id> duration 10

# 輸出：
#    3.3M count    jit_noinline  (percent 60.1%)  ← 最熱的 instruction
#    1.2M count    ...
```

## BPF_PROG_TEST_RUN：測試 BPF 程式

```c
/* 在 userspace 提供 test input，觸發 BPF program 執行 */
#include <linux/bpf.h>

struct bpf_test_run_opts opts = {
    .sz       = sizeof(opts),
    .data_in  = test_packet,     /* 測試封包 */
    .data_size_in = sizeof(test_packet),
    .data_out = result_buf,
    .data_size_out = sizeof(result_buf),
    .repeat   = 1000,            /* 重複 1000 次，測量效能 */
};

int err = bpf_prog_test_run_opts(prog_fd, &opts);
/* opts.duration 包含 1000 次執行的總時間（ns）*/
printf("average latency: %llu ns\n", opts.duration / 1000);
```

## 完整的 Debug Workflow

```
BPF 程式寫好但 load 失敗：
  1. 開啟 verbose verifier log（log_level = 2）
  2. 找錯誤關鍵字（invalid mem access、!read_ok 等）
  3. 對應到 source line（用 linum 選項 dump xlated）
  4. 修正問題，重新 load

BPF 程式 load 成功但行為不正確：
  1. 用 bpf_printk 在關鍵路徑加 debug 輸出
  2. 用 bpftool prog dump xlated 確認 CO-RE relocation 是否正確
  3. 用 bpftool prog profile 確認 hot path 是否如預期
  4. 用 BPF_PROG_TEST_RUN 用已知的 input 測試

BPF 程式效能不夠：
  1. 用 bpftool prog profile 找 hot instruction
  2. 考慮 per-CPU map（減少 lock contention）
  3. 減少 helper call（每次 helper call 有固定 overhead）
  4. 考慮拆分成 tail call chain（分散工作）
```

## 踩雷集錦

1. **verifier log 太短（被截斷）**：預設 log buffer 可能太小；用 `log_size = 1 << 20`（1 MB）確保看到完整 log

2. **`bpf_printk` 在 NMI context 不工作**：在 perf_event/PMU context，`bpf_printk` 可能靜默失敗；改用 ring buffer

3. **linum 選項需要 BTF**：`bpftool prog dump xlated linum` 需要 `.bpf.o` 裡有 `.BTF.ext` section（需要 `-g` flag 編譯）；沒有 BTF 就看不到 source line 對應

4. **JIT 輸出的 instruction 數和 xlated 不 1:1**：一條 BPF 指令可能對應多條 x86 指令；用 comment（`; source line`）定位，不要用指令計數

## 動手練習

1. 故意寫一個有 verifier 錯誤的 BPF 程式（map lookup 沒有 NULL check），觀察 verbose verifier log，找到問題所在，修正並重新 load

2. 用 `bpftool prog profile` 測量你寫的某個 BPF 程式的 hot instruction；嘗試用 per-CPU map 替換 regular map，比較 profile 結果

3. 用 `BPF_PROG_TEST_RUN` 測試一個 XDP program，提供一個已知的封包 payload，確認 program 的回傳值和行為符合預期

## 本章重點整理

- Verifier log 是最重要的 debug 工具：`log_level = 2` 開 verbose，找錯誤關鍵字後向上追蹤 register 的 type chain
- `bpf_printk` 適合 debug build，hot path 上不要用
- `bpftool prog dump xlated linum` 把 BPF 指令對應到 source line
- `BPF_PROG_TEST_RUN` 讓你用已知 input 測試 BPF program 的行為和效能

## 自我檢核

- [ ] 給一段有 verifier 錯誤的 BPF code，能定位問題並修正
- [ ] 知道 `bpftool prog dump xlated linum` 需要什麼前置條件（BTF，-g flag）
- [ ] 能說出 `bpf_printk` 的 overhead 和適用場景
- [ ] 知道 verifier log 的詳細程度由哪個參數控制

## 延伸閱讀

### 官方文件

- **[BPF verifier documentation](https://www.kernel.org/doc/html/latest/bpf/verifier.html)**
  - **讀哪裡**：整份；特別是 register state tracking 那一節
  - **學什麼**：verifier 的決策邏輯；能幫你預測哪些 pattern 會被 reject

### 部落格

- **[Debugging BPF programs](https://nakryiko.com/posts/bpf-tips-printk/)** — Andrii Nakryiko
  - **這篇說什麼**：`bpf_printk` 的詳細說明，包括 kernel 5.13+ 的改進
  - **讀哪裡**：整篇
  - **為什麼值得讀**：作者是 libbpf 維護者，說的就是工具設計者的視角

→ [練習 F：生產用 observability agent](./practice-f-observability-agent.md)
