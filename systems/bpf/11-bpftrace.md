# Ch 11 — bpftrace：一行解決問題的高階語言

> 目標：學會 bpftrace 的 probe / predicate / action 三層語法、內建變數與函式、用 maps 做聚合，能寫出 90% 日常 observability one-liner。

## bpftrace 在生態裡的位置

把寫 BPF 的方式排成光譜，從最高階到最低階：

```
高階 ────────────────────────────────────────────────► 低階
bpftrace      bcc           libbpf+CO-RE     裸 BPF C + bpf() syscall
(awk-like)   (Python+C)     (C + skeleton)
   ↑                              ↑
 本章                          Ch 13–14
```

**bpftrace 的設計哲學**：仿 awk，把 BPF 包成「一行式語言」 — 不需要 compile pipeline、不需要 user space loader、不需要管 maps 怎麼建。`sudo bpftrace -e '...'` 就跑。

代價是表達力受限 — 不適合寫複雜邏輯、無法做進階 user space 整合。但對 90% 的「我想知道某件事在 kernel 發生幾次 / 多久 / 誰做的」場景，bpftrace 是最快路徑。

## 三層語法

每條 bpftrace statement 結構：

```
probe[, probe...]   /predicate/   { actions }
```

例：

```bash
sudo bpftrace -e '
kprobe:vfs_read
/comm == "cat"/
{
    @reads[pid] = count();
}'
```

讀法：

1. **probe**：`kprobe:vfs_read` — 掛在哪
2. **predicate**：`/comm == "cat"/` — 過濾條件，不過就跳過 action
3. **action**：`{ ... }` — 真正做事的 BPF 邏輯

predicate 可以省略，actions 也可以多條 statement。

## Probe types

bpftrace 支援的 probe 涵蓋 Ch 4 提到的所有 hook：

| Probe 寫法 | 對應機制 |
|---|---|
| `kprobe:func_name` | kprobe |
| `kretprobe:func_name` | kretprobe |
| `kfunc:func_name` / `kretfunc:func_name` | fentry / fexit |
| `tracepoint:category:name` | tracepoint |
| `uprobe:/path/to/binary:func` | uprobe |
| `uretprobe:/path/to/binary:func` | uretprobe |
| `usdt:/path/to/binary:probe_name` | USDT |
| `software:event_name:count` | 軟體事件採樣 |
| `hardware:event_name:count` | 硬體 perf counter |
| `profile:hz:99` | 99 Hz 採樣（profiling） |
| `interval:s:1` | 每秒觸發 |
| `BEGIN` / `END` | bpftrace 啟動 / 結束時各跑一次 |

支援 wildcard：

```bash
sudo bpftrace -e 'kprobe:vfs_* { @[probe] = count(); }'
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_* { @[probe] = count(); }'
```

`probe` 是內建變數 — 當前觸發的 probe 名字。

## 內建變數

最常用的：

| 變數 | 意思 |
|---|---|
| `pid` | 當前 process PID |
| `tid` | 當前 thread TID |
| `comm` | 當前 process command name (16 char) |
| `cpu` | 當前 CPU id |
| `nsecs` | 當前時間（ns） |
| `elapsed` | bpftrace 啟動以來經過 ns |
| `args` | tracepoint / uprobe 的 args struct |
| `arg0`, `arg1`, ... | kprobe 的 raw 參數 |
| `retval` | kretprobe / uretprobe 的回傳值 |
| `probe` | 當前 probe 名字 |
| `func` | 當前 attached function 名字 |

範例：

```bash
sudo bpftrace -e 'kprobe:vfs_read { printf("%s[%d] reading\n", comm, pid); }'
```

## Maps：用 @ 標記

bpftrace 把 BPF map 抽象成 `@`-prefix 變數：

```bash
sudo bpftrace -e '
tracepoint:syscalls:sys_enter_openat
{
    @opens_by_comm[comm] = count();
    @opens_total = count();
}'
```

bpftrace 自動：
- 建 map（type 自己挑：scalar 用 ARRAY、有 key 用 HASH、bucket 用 PERCPU 結構）
- 結束時 print 全部 map 內容
- 處理 race condition

幾種 map "風味"：

```bash
@                 # 純 scalar map
@by_pid[pid]      # hash by pid
@by_comm_pid[comm, pid]    # 多欄位 key
```

Action 不只是 `count()`，還有：

| Action | 用途 |
|---|---|
| `count()` | 計數 |
| `sum(x)` | 累加 |
| `min(x)`, `max(x)`, `avg(x)` | 統計 |
| `hist(x)` | log2 histogram（bucket: 1, 2, 4, 8...） |
| `lhist(x, min, max, step)` | linear histogram |
| `stats(x)` | 一次給 count + avg + sum |

範例 — 量 vfs_read 延遲分布：

```bash
sudo bpftrace -e '
kprobe:vfs_read { @start[tid] = nsecs; }
kretprobe:vfs_read /@start[tid]/ {
    @lat = hist(nsecs - @start[tid]);
    delete(@start[tid]);
}'
```

按 Ctrl+C 結束時自動印：

```
@lat:
[256, 512)             3 |                                                    |
[512, 1K)             89 |@@                                                  |
[1K, 2K)            1234 |@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@|
[2K, 4K)             567 |@@@@@@@@@@@@@@@@@@@@@@                              |
[4K, 8K)              23 |@                                                   |
```

這 3 行 bpftrace 等同於 ftrace + 一個 user space 統計工具加起來幾百行 — 這就是它的威力。

## 實戰範例

### 1. 誰在 spawn 哪些 process

```bash
sudo bpftrace -e 'tracepoint:syscalls:sys_enter_execve {
    printf("%s -> %s\n", comm, str(args->filename));
}'
```

### 2. TCP retransmit by remote IP

```bash
sudo bpftrace -e 'kprobe:tcp_retransmit_skb {
    @retrans[comm] = count();
}'
```

### 3. 慢 syscall (> 10ms)

```bash
sudo bpftrace -e '
tracepoint:raw_syscalls:sys_enter { @start[tid] = nsecs; }
tracepoint:raw_syscalls:sys_exit /@start[tid]/ {
    $lat = nsecs - @start[tid];
    if ($lat > 10000000) {
        printf("%s slow syscall %d: %d ms\n", comm, args->id, $lat / 1000000);
    }
    delete(@start[tid]);
}'
```

### 4. 用 USDT 追 PostgreSQL queries

```bash
sudo bpftrace -e '
usdt:/usr/lib/postgresql/15/bin/postgres:query__start {
    printf("[%d] query: %s\n", pid, str(arg0));
}'
```

## 從 one-liner 到 .bt 檔

複雜的 bpftrace 寫成檔案：

```
// my-tool.bt
#!/usr/bin/env bpftrace

BEGIN { printf("Tracing... Ctrl+C to end.\n"); }

kprobe:vfs_read { @start[tid] = nsecs; }
kretprobe:vfs_read /@start[tid]/ {
    @lat = hist(nsecs - @start[tid]);
    delete(@start[tid]);
}

END { printf("Done.\n"); }
```

```bash
chmod +x my-tool.bt
sudo ./my-tool.bt
```

bpftrace 自帶一堆生產級 .bt scripts（Brendan Gregg 寫的）：

```bash
ls /usr/share/bpftrace/tools/
# biolatency.bt   execsnoop.bt   tcpaccept.bt   ...
```

讀這些檔案是學進階 bpftrace 的最快路徑。

## 限制與何時該換工具

bpftrace 適合：
- 排查「某個現象發生幾次 / 多久 / 誰做的」
- 一次性的 ad-hoc 分析
- prototype（後面再轉 libbpf）

**不適合**：
- 需要 user space 複雜後處理（送 JSON 出去、串 Kafka）
- 需要 attach 後存活很久、與 daemon 整合
- 高吞吐需要極致效能（bpftrace 對 maps / printf 有額外抽象成本）
- 開發團隊要審 / 測 / CI 的生產級工具

複雜場景 → 換 libbpf + CO-RE（Ch 13）。

## 一個常見誤解

「bpftrace 跟 BPF 是兩個東西」 — **錯**。

bpftrace 把腳本編譯成 BPF program → 載入 kernel。底層完全是 BPF。它只是個「BPF 的 frontend」。`sudo bpftool prog list` 就會看到 bpftrace 載的 program。

所以前面 10 章學的 verifier、CO-RE、maps、program type — bpftrace 都用得到，只是被它包起來。

## 動手練習

1. **改 Ch 0 的 openat one-liner**：把它從「印每次 open」改成「按 comm 統計次數，最後印 top」。
2. **量 read latency**：用上面的 vfs_read histogram，在你機器上跑 30 秒，看哪個 bucket 最高。
3. **抓你最常 spawn 什麼**：跑 `sys_enter_execve` 統計 30 分鐘。看你的 dev 工作流長怎樣。
4. **讀 tools 範例**：打開 `/usr/share/bpftrace/tools/biolatency.bt`，逐行讀懂。
5. **故意寫錯**：在 action 裡寫 `for (i = 0; i < pid; i++)` — bpftrace 會怎麼罵你？

## 自我檢核

- [ ] 我能寫 probe / predicate / action 三段 bpftrace statement
- [ ] 我能用 `@` map 做計數、histogram、avg
- [ ] 我能列出至少 5 種 probe type
- [ ] 我能用 `kprobe + kretprobe` 量任何 kernel function 的延遲
- [ ] 我知道什麼時候 bpftrace 不夠用、要換 libbpf

下一章我們下降一層 — 看 bcc 框架（Python 包 C BPF）。雖然 bcc 已經不是新專案的首選，但生產上的 bcc-tools 還是大量在用，你必須讀得懂。

→ [Ch 12 bcc：Python 包 C 的混合方式](./12-bcc.md)
