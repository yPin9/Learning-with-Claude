# Ch 18 — Profiling 與 flamegraph 製作

> 目標：學會用 BPF 做低開銷 sampling profiler、產生 flamegraph、處理 user space stack trace 的 frame pointer / DWARF 問題、認識 continuous profiling 的世代轉變。

## Sampling profiling 的概念

不是觀察「哪些 function 被呼叫」 — 那會慢死。而是：

```
每秒中斷 N 次（例如 99 次）
  ├─→ 記下當下的 stack trace
  ├─→ 累積到 hash map
  └─→ 印出最常出現的 stack
```

**Statistical sampling**：跑 30 秒抓 99×30 = 2970 個 sample，最常出現的 stack 就是 CPU 花最多時間的地方。

開銷低（每秒 99 次中斷對 CPU 微不足道）、不需改程式、近似精確。

## BPF 做 profiling 的優勢

傳統 `perf record` 已經能做這個 — 但 BPF 多兩個 killer feature：

1. **In-kernel 聚合**：bcc 的 profile 在 kernel 裡用 map 累積、user space 只讀結果。perf record 是「全部 sample 寫到 perf buffer、user space 後處理」 — sample 多時 IO 大。
2. **彈性過濾**：「只 profile 這個 PID」「只 profile 在 kernel mode 的 sample」「只 profile cgroup X」 — 全部一行 filter 解決。

## 用 bcc profile 抓 stack

```bash
# 99 Hz 採樣 30 秒、聚合輸出
sudo profile-bpfcc -F 99 30
```

輸出：

```
    finish_task_switch
    __schedule
    schedule
    futex_wait_queue_me
    ...
    pthread_cond_wait
    - my-app (12345)
        2345
```

每個 stack 後面數字是「30 秒內這個 stack 出現幾次」。最大的 stack 就是熱點。

過濾：

```bash
sudo profile-bpfcc -F 99 -p 12345 30      # 只 profile PID 12345
sudo profile-bpfcc -F 99 -K 30            # 只看 kernel stack
sudo profile-bpfcc -F 99 -U 30            # 只看 user stack
sudo profile-bpfcc -F 99 --pid 12345 30   # 同上
```

## 產 flamegraph

[Brendan Gregg 的 FlameGraph](https://github.com/brendangregg/FlameGraph) 工具：

```bash
git clone https://github.com/brendangregg/FlameGraph.git
export PATH=$PATH:$(pwd)/FlameGraph

# bcc profile 直接出 folded format
sudo profile-bpfcc -F 99 -f 30 > stacks.folded
flamegraph.pl < stacks.folded > flame.svg

# 用瀏覽器打開
xdg-open flame.svg
```

flamegraph 讀法：
- **x 軸是寬度（時間佔比）**，不是時間軸
- **y 軸是 stack 深度**
- 越寬的 box = 越多 sample 落在這
- 點 box 可以 zoom in

## 用 bpftrace 寫 profile（理解原理）

```bash
sudo bpftrace -e '
profile:hz:99 /pid == 12345/ { @[kstack, ustack] = count(); }'
```

結束時印 stack histogram。`kstack` / `ustack` 是 bpftrace 的 builtin — 自動拿當下 stack trace。

## User space stack trace 的痛點

Kernel stack 永遠拿得到（kernel 自己有完整 metadata）。**User space stack 麻煩很多**。

### 1. Frame pointer 問題

預設 GCC `-O2` 會啟用 `-fomit-frame-pointer` — frame pointer register 被當一般 register 用。**沒 frame pointer = stack walker 走不下去**。

解法：
- 重編應用加 `-fno-omit-frame-pointer`
- 或用 DWARF unwinding（下面講）

幾大 distro 開始預設啟用 frame pointer：Fedora 38+、Ubuntu 24.04+。**Production 開 frame pointer 開銷 < 1%**，profile 能力換來的價值遠大於此。

### 2. JIT runtime 問題

Java、Node.js、PyPy 跑 JIT 出來的 native code，通常**沒 symbol**。Stack 抓得到地址，但翻成 function name 翻不出來。

解法：runtime 提供 `perf-PID.map`：

```bash
ls /tmp/perf-12345.map  # JVM 配 -XX:+PreserveFramePointer 寫的
```

bcc/perf 會自動讀。Node.js 用 `--perf-prof` flag。Python（CPython）通常需要特殊 patch 或用 Pyspy。

### 3. DWARF unwinding

如果不能改 frame pointer，可以用 DWARF debug info 解析 stack。`perf` 支援 `--call-graph dwarf`，**但 BPF 端歷史上不支援**。

近年（5.18+）有 `bpf_get_stack` + DWARF helper 的進展，但實務還很青澀。**先靠 frame pointer 比較穩**。

## Continuous profiling — Parca / Pyroscope

傳統 profile 是「跑 30 秒抓一次」。新一代是 **continuous profiling**：常駐 daemon、低 freq sample、長期儲存、可在任意時間點查歷史 profile。

主要 OSS：
- [Parca](https://www.parca.dev/) — Polar Signals 主導，Go + BPF
- [Pyroscope](https://pyroscope.io/) — Grafana 收購後並入 Grafana
- [Phlare](https://grafana.com/oss/phlare/) — 上面那個的後續演進

它們底層都用 BPF 做 stack sampling。架構大致：

```
BPF profile prog (全機器運行)
    ↓ ringbuf
Local agent (compress + symbolize)
    ↓ HTTP / OTLP
Central storage (column store)
    ↓ query
Web UI (zoom 任意時段的 flamegraph)
```

對中型以上的 service fleet，**continuous profiling 已經是 production 必備**。

## off-CPU profiling

Profile 抓的是「在 CPU 上做什麼」。Off-CPU 是「不在 CPU 上等什麼」。BPF 經典工具：

```bash
sudo offcputime-bpfcc -p 12345 -K 10
```

這支工具 attach `finish_task_switch`：每次 process 被排出 CPU 時記下當下 stack 與時間，回到 CPU 時計算 off-CPU 時長，按 stack 聚合。

**On-CPU + Off-CPU 兩個 flamegraph 拼起來，是效能分析最完整的圖像**。

## 一個常見誤解

「Flamegraph 的 x 軸是時間」 — **錯**。

x 軸是「sample 數量佔比」。位置（左/右）通常按 alphabetical 或 stack 順序排，**不代表時間順序**。Differential flamegraph 才有時間維度（紅 = 變慢、綠 = 變快）。

## 動手練習

1. **跑一次完整 flamegraph**：寫個 CPU bound 程式（例如 100M 次 sha256），用 profile + flamegraph 產 SVG。
2. **比對開 / 不開 frame pointer**：編一份 `-fno-omit-frame-pointer`、一份預設，profile 兩個版本看差異。
3. **off-CPU**：profile 一個有 `pthread_cond_wait` / `epoll_wait` 的 server，看 off-CPU flamegraph。
4. **Parca trial**：跑 [Parca demo](https://demo.parca.dev/)，體會 continuous profiling 的價值。

## 自我檢核

- [ ] 我能解釋 sampling profiling 為什麼開銷低
- [ ] 我能用 bcc profile 出 stack folded format
- [ ] 我能用 flamegraph.pl 產 SVG
- [ ] 我能解釋 user stack 抓不到時可能的三種原因
- [ ] 我能描述 continuous profiling 與傳統 profile 的差異

下一站：練習 C — 用 USDT 寫一個 PostgreSQL 慢查詢 tracer。

→ [練習 C：SQL 慢查詢 tracer](./practice-c-sql-slow-query-tracer.md)
