# Ch 14 — bpftrace（debug 視角）

> **目標**：從 debug 角度認識 bpftrace——可程式化的動態 tracer，讓你用簡短的腳本「自訂要觀察什麼、怎麼統計」。理解它和前面工具的關係（它能做 strace/ftrace 做的事，但可程式化）、幾個實用的 debug one-liner、以及它為什麼強大（在 kernel 裡安全執行你的觀察邏輯）。本課只講 debug 視角——bpftrace/eBPF 的深入留給 bpf 課。這章展示「現代觀察」的威力，為 bpf 課鋪墊。

> **環境**：Linux 4.9+（建議 5.x），bpftrace（Ch 0）。需要 root。

## 為什麼 bpftrace 是「現代觀察」？

前面的工具各有固定的功能——strace 看 syscall、ftrace 看 kernel 函式、perf 找熱點。但它們的「觀察邏輯」是固定的（你只能用它提供的選項）。如果你想「自訂」觀察——例如「統計每個 process 呼叫 read 的次數和大小分布」「只在某個條件下記錄」「即時計算某個指標」——這些固定工具做不到。

**bpftrace** 是「可程式化的 tracer」——你寫簡短的腳本，定義「在哪個事件（syscall/kernel 函式/tracepoint）觸發、做什麼（記錄/統計/過濾）」。它在 kernel 裡**安全地執行**你的觀察邏輯（用 eBPF，bpf 課深入）。這讓觀察變得無限靈活——你能精確地問「我想知道的那個問題」，而非受限於工具的固定功能。本課從 debug 角度展示它的威力（深入的 eBPF 程式設計留給 bpf 課），讓你看到「現代觀察」能做到什麼。

## 先建立直覺:可程式化的觀察

```
固定工具 vs 可程式化（bpftrace）：

  固定工具（strace/ftrace/perf）：
    功能固定，你選選項，它給固定格式的輸出
    「我只能問它設計好的問題」
        │
  bpftrace（可程式化）：
    你寫腳本：「在這個事件，做這件事」
    bpftrace 'tracepoint:syscalls:sys_enter_read { @[comm] = count(); }'
    → 「每次有人呼叫 read，按程式名計數」
    「我能問任何我想問的問題」
        │
  bpftrace 程式的結構：
    事件 { 動作 }
    probe（在哪觸發）{ action（做什麼）}
        │
  → bpftrace = 你定義觀察邏輯，在 kernel 安全執行
    無限靈活（自訂統計/過濾/即時計算）
    這是「現代觀察」—— 不被工具的固定功能限制
```

關鍵心智：前面的工具功能固定（你只能問它設計好的問題）。**bpftrace 是可程式化的**——你寫 `事件 { 動作 }` 的腳本，定義「在哪個事件觸發、做什麼」，bpftrace 在 kernel 裡安全執行。這讓觀察無限靈活——你能問「任何想問的問題」，不被工具的固定功能限制。

> bpftrace 能做 strace（Ch 5）、ftrace（Ch 13）做的事，但可程式化。它用 eBPF（bpf 課深入）在 kernel 安全執行。tracepoint（Ch 13）是它常用的觀察點。本課只講 debug 視角。

## bpftrace 的基本結構

```bash
# bpftrace 程式 = 一串「probe { action }」
# probe：在哪觸發；action：做什麼

# === 最簡單：每次 read syscall 印一行 ===
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_read { printf("read by %s\n", comm); }'
# 每次有 process 呼叫 read，印出程式名（comm = command）

# === 統計：按程式名計數 read 次數 ===
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_read { @[comm] = count(); }'
# @[comm] = count()：按程式名累加
# Ctrl-C 結束時印出：
# @[bash]: 12
# @[myapp]: 3847        ← myapp 呼叫 read 3847 次（可能太多 = bug）
# → 一行腳本做到 strace 要過濾統計才能做的事

# === probe 的類型（在哪觸發）===
# tracepoint:syscalls:sys_enter_read   syscall 進入
# kprobe:vfs_read                       kernel 函式（任意）
# uprobe:/bin/bash:readline             使用者空間函式
# profile:hz:99                         定時取樣（99Hz）
# interval:s:1                          每秒一次

# === 內建變數 ===
# comm   程式名
# pid    process ID
# arg0/arg1...  probe 的參數
# nsecs  時間戳
```

> **bpftrace 的 `probe { action }` 結構讓你一行腳本做到「自訂統計」——如「按程式名統計 read 次數」**。bpftrace 程式是一串 `probe { action }`——**probe** 定義「在哪觸發」（`tracepoint:syscalls:sys_enter_read` = read syscall 進入、`kprobe:vfs_read` = kernel 函式、`uprobe:...` = 使用者函式、`profile:hz:99` = 定時取樣），**action** 定義「做什麼」（printf 印出、`@[key] = count()` 統計）。威力在於**自訂統計**——`@[comm] = count()` 一行就做到「按程式名統計 read 次數」（strace 要 trace 全部再過濾統計才能做，bpftrace 直接在 kernel 統計）。內建變數（`comm` 程式名、`pid`、`arg0/arg1` probe 參數、`nsecs` 時間）讓你存取觀察的上下文。這展示了 bpftrace 的核心優勢——**你定義要統計什麼**，而非受限於工具的固定輸出。`@` 開頭的是「map」（關聯陣列，用於統計）——`@[key] = count()`（計數）、`@[key] = sum(x)`（加總）、`@ = hist(x)`（直方圖）。這些讓你在 kernel 裡即時統計，而非把所有原始資料拉到使用者空間再處理（高效、低開銷）。一行 bpftrace 常能取代「strace + 一堆文字處理」。

## 實用的 debug one-liner

```bash
# bpftrace 的威力在「一行解決特定問題」

# 1. 哪個 process 開最多檔案（找 fd 洩漏的源頭）
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_openat { @[comm] = count(); }'

# 2. read 的大小分布（直方圖）
sudo bpftrace -e 'tracepoint:syscalls:sys_exit_read /args->ret > 0/ { @bytes = hist(args->ret); }'
# @bytes:
# [16, 32)     50 |@@@@@@
# [32, 64)    200 |@@@@@@@@@@@@@@@@@@@@@@@@
# → read 的回傳大小分布（一眼看出 read 的模式）

# 3. 統計 syscall 的延遲（哪個 syscall 慢）
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_* { @start[tid] = nsecs; }
                  tracepoint:syscalls:sys_exit_* /@start[tid]/ {
                    @ns[probe] = hist(nsecs - @start[tid]); delete(@start[tid]); }'

# 4. 哪個 process 在發網路連線
sudo bpftrace -e 'kprobe:tcp_connect { printf("%s connecting\n", comm); }'

# 5. 統計 process 的 CPU 排程延遲（為什麼卡）
# （需要 sched tracepoint，較複雜，bpf 課深入）

# 6. 追蹤特定 process 的所有 read
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_read /comm == "myapp"/ {
                    printf("myapp read fd %d size %d\n", args->fd, args->count); }'
# /condition/ 是過濾器（只在條件成立時 action）
```

> **bpftrace 的 one-liner 能「一行解決特定 debug 問題」——直方圖、過濾、按條件統計，這些 strace 做不到或要很多後處理**。幾個展示威力的 one-liner：**直方圖**（`hist(args->ret)` 看 read 大小的分布——一眼看出「read 都是小的還是大的」，這是 strace 做不到的，它只能逐行顯示）；**過濾**（`/comm == "myapp"/` 只觀察特定 process，`/args->ret > 0/` 只在條件成立時——在 kernel 層過濾，比 strace 全抓再 grep 高效）；**按條件統計延遲**（統計每個 syscall 的延遲分布，找出哪個 syscall 慢）。這些展示了 bpftrace 比固定工具強的地方：(1) **自訂統計**（count/sum/hist/avg，在 kernel 即時算）；(2) **過濾**（`/condition/`，只觀察你要的，低開銷）；(3) **跨事件關聯**（記錄 enter 時間、exit 時算延遲）。這讓你精確地問「我想知道的問題」——「read 的大小分布」「myapp 的每個 read 的 fd 和大小」「哪個 syscall 最慢」。對比 strace（看完整行為但難統計）、ftrace（kernel 函式但固定格式）、perf（找熱點但 CPU 為主），bpftrace 是「可程式化的瑞士刀」。實務上 bpftrace 有大量現成的 one-liner（Brendan Gregg 的 bpftrace 教學有幾十個），覆蓋常見的 debug 需求——你常能找到或微調一個 one-liner 解決你的問題。這是「現代觀察」的威力——靈活、精確、低開銷。

## bpftrace vs 前面的工具

```
bpftrace 和前面工具的關係（它能做它們的事，但可程式化）：

  能做 strace 的事：trace syscall（但可統計/過濾）
    bpftrace -e 'tracepoint:syscalls:sys_enter_* { @[probe] = count(); }'
    = strace -c 的功能，但更靈活
        │
  能做 ftrace 的事：trace kernel 函式（kprobe）
    bpftrace -e 'kprobe:vfs_read { @[comm] = count(); }'
        │
  能做 perf 的事：取樣（profile probe）
    bpftrace -e 'profile:hz:99 { @[ustack] = count(); }'
    = perf 的 CPU profiling
        │
  獨特：跨事件關聯、自訂統計、即時計算、過濾
    這些固定工具做不到
        │
  → bpftrace 是「統一的可程式化觀察」
    但代價：要學它的語言、需要較新 kernel、深入要懂 eBPF
    （所以前面的固定工具仍有價值——簡單問題用簡單工具）
```

> **bpftrace 能做 strace/ftrace/perf 的事但可程式化——不過簡單問題仍用簡單工具，bpftrace 的甜蜜點是「需要自訂統計/過濾」**。bpftrace 很強大——它能 trace syscall（像 strace）、kernel 函式（像 ftrace 的 kprobe）、取樣（像 perf 的 profile），而且**可程式化**（自訂統計、過濾、跨事件關聯）。但這不代表它取代所有工具——**簡單問題仍用簡單工具**：「看一個程式的 syscall」用 `strace`（直接、不用寫腳本）、「找 CPU 熱點」用 `perf`（火焰圖直觀）、「快速看一個程式做什麼」用 strace/ltrace。bpftrace 的**甜蜜點**是「需要自訂觀察」——統計（按某維度計數/分布）、過濾（複雜條件）、跨事件關聯（算延遲）、即時計算指標——這些固定工具做不到或很笨拙。代價：要學 bpftrace 的語言、需要較新 kernel（4.9+，建議 5.x）、深入要懂 eBPF（bpf 課）。所以選工具：簡單直接的觀察用固定工具（strace/perf），需要自訂統計/過濾/關聯用 bpftrace。理解這個定位，你不會「什麼都用 bpftrace」（殺雞用牛刀）也不會「不知道有 bpftrace」（遇到自訂需求卡住）。**本課到此為止的 bpftrace 是「debug 視角」**——讓你知道它存在、能做什麼、何時用。**深入的 eBPF 程式設計（自己寫 eBPF 程式、各種 probe、生產級的觀測工具）留給 bpf 課**——那是一個大主題（eBPF 是 Linux 觀測/網路/安全的革命性技術）。這章讓你看到「現代觀察」的威力，並為 bpf 課鋪墊——你已經理解了觀察的基礎（ptrace/strace/ftrace/tracepoint），bpf 課會把可程式化觀察推到極致。

## 故意弄壞:用 bpftrace 自訂觀察

```bash
# 用 bpftrace 解一個「固定工具難解」的問題
# 問題：myapp 偶爾很慢，想知道「它的 read 大小分布」和「哪些 read 慢」

cd ~/obslab
# 一個會做各種大小 read 的程式
cat > reader.c <<'EOF'
#include <unistd.h>
#include <fcntl.h>
int main() {
    int fd = open("/etc/passwd", O_RDONLY);
    char buf[4096];
    for (int i = 0; i < 1000; i++) {
        lseek(fd, 0, SEEK_SET);
        read(fd, buf, (i % 4 + 1) * 100);   // 不同大小的 read
        usleep(1000);
    }
    close(fd);
    return 0;
}
EOF
gcc -o reader reader.c

# 用 bpftrace 看「reader 的 read 大小分布」（固定工具難做的統計）
sudo bpftrace -e '
tracepoint:syscalls:sys_enter_read /comm == "reader"/ {
    @read_sizes = hist(args->count);   // read 大小的直方圖
}' &
BPF=$!
./reader
sleep 1
sudo kill $BPF
# @read_sizes:
# [100, 200)   250 |@@@@@@@@
# [200, 400)   500 |@@@@@@@@@@@@@@@@
# [400, 512)   250 |@@@@@@@@
# → 一眼看出 reader 的 read 大小分布！
#   這是 strace 做不到的（它逐行顯示，不統計分布）

# 對比：strace 只能逐行顯示，要自己統計
# strace -e read ./reader 2>&1 | grep read | awk '{...統計...}'  # 麻煩
# bpftrace 一個 hist() 就做到
```

> **bpftrace 一個 `hist()` 做到「read 大小分布」——這是 strace 逐行顯示難以統計、bpftrace 在 kernel 即時統計的展示**。這個例子展示 bpftrace 的甜蜜點——「自訂統計」。問題是「reader 的 read 大小分布如何」，固定工具難解：strace 逐行顯示每個 read（`read(3, ..., 100)`、`read(3, ..., 200)`…），你要自己抓出大小、統計分布（用 awk 等後處理，麻煩）。而 bpftrace 一個 `@read_sizes = hist(args->count)` 就在 kernel 即時統計出**直方圖**——一眼看出「read 大小集中在哪、分布如何」。配合 `/comm == "reader"/`（只觀察那個程式）過濾。這是「可程式化觀察」的價值——你精確地問「我想知道的（大小分布）」，bpftrace 直接給你（而非逐行原始資料要你自己處理）。這類「需要統計/分布/過濾」的觀察需求，bpftrace 是最佳工具。它在 kernel 層即時統計（低開銷，不用把每個事件拉到使用者空間），這也是它能用於生產環境的原因（像 perf 一樣低開銷，但更靈活）。這完成了 Part 5 的「現代 tracing」——從 perf（取樣找熱點）、ftrace（kernel 函式）到 bpftrace（可程式化）。你看到了觀察工具的演進：從固定功能（strace/perf）到可程式化（bpftrace/eBPF）。eBPF 是 Linux 觀測的未來，bpf 課會深入——但你現在已經理解了它的 debug 價值和在觀察工具光譜中的位置。

## 動手練習

1. 第一個 bpftrace：`bpftrace -e 'tracepoint:syscalls:sys_enter_read { printf("%s\n", comm); }'`，看誰在 read

2. 統計：`@[comm] = count()` 統計哪個程式呼叫某 syscall 最多

3. 直方圖：用 `hist()` 看某個值（read 大小、syscall 延遲）的分布

4. 過濾：用 `/comm == "..."/` 只觀察特定程式

5. 跑「故意弄壞」：用 bpftrace 看 reader 的 read 大小分布，對比 strace 要自己統計的麻煩

## 本章重點整理

- bpftrace 是可程式化的 tracer：你寫 `probe { action }` 定義「在哪觸發、做什麼」，在 kernel 安全執行（eBPF）
- 能做 strace/ftrace/perf 的事，但可程式化——自訂統計（count/sum/hist）、過濾（/condition/）、跨事件關聯
- probe 類型：tracepoint（syscall/kernel 事件）、kprobe（kernel 函式）、uprobe（使用者函式）、profile（取樣）
- 甜蜜點：需要自訂統計/分布/過濾/關聯時（固定工具做不到）；簡單問題仍用簡單工具（strace/perf）
- 本課只講 debug 視角——eBPF 深入（寫 eBPF 程式、生產級觀測）留給 bpf 課；這章展示「現代觀察」威力

## 自我檢核

- [ ] 理解 bpftrace 的「可程式化」和固定工具的差別
- [ ] 會寫簡單的 bpftrace one-liner（probe + 統計/過濾）
- [ ] 知道 bpftrace 能做 strace/ftrace/perf 的事但更靈活
- [ ] 知道 bpftrace 的甜蜜點（自訂統計/過濾）和何時用簡單工具
- [ ] 理解 bpftrace 在觀察工具光譜的位置（現代、可程式化，eBPF 深入留給 bpf 課）

## 延伸閱讀

### 必讀資源

- **[bpftrace one-liner 教學](https://github.com/bpftrace/bpftrace/blob/master/docs/tutorial_one_liners.md)** — bpftrace 專案
  - **讀哪裡**：tutorial（12 個 one-liner 漸進教學）
  - **為什麼值得讀**：bpftrace 入門的最佳資源，從零學會寫 one-liner

- **[BPF Performance Tools](https://www.brendangregg.com/bpf-performance-tools-book.html)** — Brendan Gregg
  - **讀哪幾章**：Ch 1-5（bpftrace 基礎和方法論）
  - **這本書的定位**：bpftrace/eBPF 觀測的權威；接 bpf 課的橋樑
  - **前提**：本課 + 想深入

### 本課相關

- **bpf 課**
  - **為什麼值得讀**：eBPF 的完整深入（寫 eBPF 程式、Tracing/Networking/Security、生產級 agent）；本章是它的 debug 視角預覽

### 官方文件

- **[bpftrace reference](https://github.com/bpftrace/bpftrace/blob/master/docs/reference_guide.md)** — bpftrace
  - **讀哪裡**：probe 類型、內建函式
  - **為什麼值得讀**：bpftrace 語言的權威參考

Part 5（現代 tracing）到此完成——你掌握了 perf（找熱點）、ftrace（kernel 函式）、bpftrace（可程式化）。接下來 Part 6 進入記憶體與正確性——valgrind 和 sanitizers，debug 記憶體錯誤、leak、並發 bug。

→ [Ch 15 valgrind memcheck](./15-valgrind-memcheck.md)
