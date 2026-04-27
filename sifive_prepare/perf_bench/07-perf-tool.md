# Ch 7 — perf record / perf report 實戰

> 目標：把 `perf record` + `perf report` 變成日常工具。能找出 program 的 hot function、解讀 call graph、跟 source / asm 對應。

## 為什麼用 perf record

`perf stat` 量整體、`perf record` 量 **per-PC**。每 N event 中斷、記下當時的 instruction pointer 跟 stack。

產生的資料可以 answer：

- 哪個 function 吃最多 CPU？
- 哪一行 source code？
- 哪條 instruction？
- 呼叫鏈是什麼？

**這是效能優化的第一步**：找 hot function、再 focus 改進。

## 最簡單的用法

```bash
perf record ./program
perf report
```

`perf record` 產生 `perf.data` file、`perf report` 打開 TUI 瀏覽。

TUI 操作：

- 方向鍵移動
- `Enter` 展開 function
- `a` 看 annotated asm
- `Esc` / `q` 退出

## 重要 flag

```bash
# 自訂 sampling frequency
perf record -F 997 ./program          # 997Hz (避免 clock-aligned bias)

# 記錄 call graph (不只 PC)
perf record -g ./program              # frame pointer based
perf record --call-graph dwarf ./prog # DWARF based (更準但慢)
perf record --call-graph lbr ./prog   # Last Branch Record (Intel only)

# 特定 event
perf record -e cache-misses ./program
perf record -e cycles,instructions,branch-misses ./program

# Attach existing process
perf record -p <PID> sleep 10         # 量 10 秒

# System-wide (全系統)
sudo perf record -a sleep 5
```

## Call graph 三種方式

### 1. Frame pointer (`-g fp`)

需要 compile 時 `-fno-omit-frame-pointer`。典型 release build 沒 frame pointer → 這個方式 useless。

### 2. DWARF (`-g dwarf`)

用 debug info 反向推 stack frame。**常用**。需要 compile `-g`。

Overhead 大（複製 stack）。

### 3. Last Branch Record (`-g lbr`)

Intel 獨有 hardware feature。CPU 自動紀錄最近 N 個 branch。超低 overhead。

**RISC-V 沒對應**。用 DWARF。

## 實例：找 hot function

```c
// hello.c
#include <stdio.h>

int slow_func(int n) {
    int s = 0;
    for (int i = 0; i < n; i++) {
        s += i * i;
    }
    return s;
}

int fast_func(int n) {
    return n*(n-1)*(2*n-1)/6;
}

int main() {
    int total = 0;
    for (int i = 0; i < 1000000; i++) total += slow_func(100);
    for (int i = 0; i < 1000000; i++) total += fast_func(100);
    printf("%d\n", total);
    return 0;
}
```

```bash
gcc -O2 -g hello.c -o hello
perf record -F 997 -g --call-graph dwarf ./hello
perf report
```

會看到：

```
Samples: 8K of event 'cycles'
Overhead  Command  Shared Object  Symbol
  97.2%   hello    hello          [.] slow_func
   2.5%   hello    hello          [.] main
   0.3%   hello    libc.so        [.] ...
```

**slow_func 佔 97%**。目標明確。

按 `Enter` → `a` 看 annotated asm：

```
         │   for (int i = 0; i < n; i++) {
         │      mv      a1, a0
    0.00 │      li      a0, 0
    5.10 │      li      a2, 0
         │   }
         │   s += i * i;
   35.20 │      mul     a3, a2, a2
   58.30 │      add     a0, a0, a3
         │      addi    a2, a2, 1
    1.40 │      blt     a2, a1, .loop
```

**mul 跟 add 各佔 35% / 58%**。這是 hot loop 的核心。要改進就改這裡。

## 解讀 Overhead 欄

```
Overhead 欄 = (samples for this symbol) / (total samples) × 100
```

不完全等於「時間佔比」：

- 假設每 10M cycles 取 1 sample
- Sample in foo() 數 / total → 近似 foo 佔 CPU 時間
- 但 **sampling bias**：低頻 event 可能 under-sampled

通常誤差 < 5%。hot function 還是顯然。

## Children vs Self

`perf report` 預設有兩欄：

- **Self**：只算這個 function body 的時間
- **Children**：包含它呼叫的子函式

```
Overhead       Command  Symbol
  Self Children
  5%  90%     ./hello  [.] main         ← 自身 5%，整個 90%（含子呼叫）
  85%  85%    ./hello  [.] slow_func    ← 自身就 85%
```

切 only "Self"：`perf report --no-children`。

切 only "Children"：`perf report --children`（default）。

## Call graph 展開

在 TUI 按 `Enter` 展開 function 的 call graph：

```
- 97.2%  slow_func
    + 95.3% mul
    + 1.8%  add
    + 0.1%  other
```

看清楚呼叫路徑。

## 跨 shared object

程式可能 call `libc.so`、`libm.so` 等。perf report 顯示：

```
20%  [.] fft_butterfly          (program main .text)
15%  libm.so.6  [.] sin          (libm)
10%  libc.so.6  [.] memcpy       (libc)
```

要看 libc source 要裝 debug symbol：

```bash
sudo apt install libc6-dbg
```

然後 perf report 能 annotate libc function。

## 非 interactive 輸出

不想用 TUI：

```bash
perf report --stdio > report.txt
cat report.txt
```

適合 scripting / CI pipeline。

## `perf script`：raw sample dump

每個 sample 一行：

```bash
perf script > out.txt
head out.txt
```

```
hello  12345 1234.567: cycles:
      7f1abc... slow_func+0x14 (/tmp/hello)
      7f1abd... main+0x38 (/tmp/hello)
      7f1ab8... __libc_start_main+0xd5 (/lib/x86_64-linux-gnu/libc.so.6)
```

**flamegraph / 自訂處理從這裡開始**。Ch 9 會用。

## Annotate 單個 function

```bash
perf annotate slow_func
```

印 source + asm 對照、每行 %。

## 限制 sampling profile 到特定 region

想測 program 執行中某段？加 `--control`：

```bash
mkfifo /tmp/perf-ctl.fifo /tmp/perf-ack.fifo
perf record --control=fifo:/tmp/perf-ctl.fifo,/tmp/perf-ack.fifo ./program
# 程式內用 echo enable > /tmp/perf-ctl.fifo 開始, disable 停
```

更簡單的 approach：把程式啟動時 `sleep(5)` 做 warmup、perf -p 接上。

## `-F` frequency 選擇

```
-F 99     每秒 99 次 sample (很低 overhead, noisy)
-F 997    每秒 997 次 (常用, 平衡)
-F 9999   高頻率 (高 overhead, 好 detail)
```

程式跑 1 秒、`-F 997` 拿 1000 個 sample、夠 statistical 意義。跑 100ms、需要 -F 9999。

## Wall-clock vs CPU-cycles

`perf record` 預設 `cycles` event → CPU-cycles sampling。**不包含 idle / blocked time**。

要量「實際時間」（包括 wait on I/O、sleep）用 `perf record -e task-clock`。

## Profile guided optimization 的輸入

PGO（Ch 11 會講）需要 profile 資料。`perf record` 可以產生（不完全跟 compiler 的 PGO format 一致，但可以轉）。

Clang 有 `llvm-profdata` 工具轉換。

## 常見 pitfall

### Pitfall 1：忘記 `-g`

```bash
gcc -O2 prog.c -o prog    # 無 debug
perf record -g ./prog
# Call graph 可能 broken / 錯誤
```

**Always `-g` when profiling**。`-O2 -g` 是 standard。

### Pitfall 2：權限錯

```
perf.data permission denied
```

改 `/proc/sys/kernel/perf_event_paranoid`（Ch 0）。

### Pitfall 3：kernel symbol 顯示 `[unknown]`

```bash
sudo sysctl kernel.kptr_restrict=0
```

### Pitfall 4：JIT code 沒 symbol

JIT （Java、Node.js）code 在 runtime 生成，perf 不知道 symbol。

解法：`/tmp/perf-<pid>.map` file 列 jit symbol。各 language 有對應 perf-map-agent。

### Pitfall 5：symbol 看不到是 `0x7f12345678` 這種地址

Binary 沒 debug symbol 或被 strip。Linux distro 的 package 通常：

```bash
sudo apt install libc6-dbg binutils-riscv64-linux-gnu-dbg
```

## RISC-V 專屬考量

### Frame pointer 問題

RISC-V lp64d 預設 `-fomit-frame-pointer`。`-g fp` call graph 不 work。必用 `-g dwarf`。

### QEMU 跑 perf

`qemu-user` 跑 RISC-V binary 時，**host 的 perf 量到的是 qemu 自己**，不是 RISC-V program。誤導。

唯一 reliable：真 RISC-V hardware 上跑 perf。

### RISC-V PMU 事件支援

不同 core 支援的 hardware event 不同。`perf list` 查 target 硬體。SiFive 的 core 支援逐漸完善中。

## 動手練習

1. 編一個有 multiple function 的 C program、用 perf record/report 找 hot function。
2. 加 `-g` vs 不加 `-g`、比較 perf report 的 symbol quality。
3. 用 `--call-graph dwarf` 觀察深度 call chain。
4. `perf annotate` 一個 hot function，找出哪條 instruction 最佔 time。
5. 用 `perf script > out.txt` 提取 raw sample，write Python script 分析頻率 distribution.

## 自我檢核

- [ ] 我能用 perf record 找 hot function
- [ ] 我知道 `-g dwarf` vs `-g fp` vs `-g lbr` 差異
- [ ] 我能讀 Self vs Children overhead
- [ ] 我能用 `perf annotate` 對照 source + asm
- [ ] 我知道 RISC-V 的 perf limitation（qemu、frame pointer）

下一章看 llvm-mca — 靜態 pipeline analyzer，跟 perf 互補。

→ [Ch 8 llvm-mca：靜態分析 throughput / bottleneck](./08-llvm-mca.md)
