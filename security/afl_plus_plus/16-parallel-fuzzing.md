# Ch 16 — Parallel fuzzing：master / secondary 分工

> 目標：解釋 `-M` 跑 deterministic、`-S` 跑 havoc 的歷史原因以及現代 AFL++ 下的變化；拆 queue sync 的檔案系統協定；說明 CMPLOG secondary 為什麼另外一條 pipeline。

## 為什麼要並行

單一 fuzzer 的瓶頸：

- 一條 fuzz pipeline = 一個 CPU core。多核機器浪費。
- 一套 mutation 策略。不同 target 吃的策略不一樣，單 fuzzer 無法同時試多條路。
- 一套 sanitizer。ASan 慢但抓 bug 多，純 coverage 快但看不到記憶體錯誤 — 單 fuzzer 選一個就沒另一個。

Parallel fuzzing 同時開多個 fuzzer 實例，**每個 instance 跑在不同 core、配不同策略、共享發現**。這是現代 fuzzing 的標配。

## 原 AFL 的 `-M` / `-S` 設計

歷史上 AFL 把 instance 分兩類：

- **Master（`-M`）**：每個 session 只有一個。跑 deterministic + havoc。它負責「做功德」— 把 deterministic 那套窮舉完整跑完。
- **Secondary（`-S`）**：可以有多個。跳過 deterministic，只跑 havoc。它負責「打游擊」— 大量隨機探索。

這個分工的邏輯：deterministic 是窮舉式的，跑一次就夠，派一個 instance 去做；其他 instance 不必重複這份工，專心 havoc 更有價值。

```bash
# Terminal 1
afl-fuzz -M main -i seeds/ -o sync_dir/ -- ./target @@

# Terminal 2
afl-fuzz -S fuzzer01 -i seeds/ -o sync_dir/ -- ./target @@

# Terminal 3
afl-fuzz -S fuzzer02 -i seeds/ -o sync_dir/ -- ./target @@
```

共享 `sync_dir`，每個 instance 在裡面有自己的子目錄：

```
sync_dir/
├── main/
│   ├── queue/
│   ├── crashes/
│   └── fuzzer_stats
├── fuzzer01/
│   ├── queue/
│   └── ...
└── fuzzer02/
    ├── queue/
    └── ...
```

## Queue sync：檔案系統當通訊管道

每個 fuzzer 固定時間（預設每 20 秒）掃其他 instance 的 `queue/` 目錄：

```c
// 虛擬 code，真實在 afl-fuzz-init.c 的 sync_fuzzers()
for each other_instance in sync_dir:
    for each file in other_instance/queue/:
        if not already_imported(file):
            run this file through my target
            if has_new_bits:
                add to my queue (with bookkeeping)
            mark_imported(file)
```

重點：

- **只讀對方的 queue**：queue entry 都是有新 coverage 的 input。直接跑它們很有效。
- **只 import 有新 coverage 的**：對方的 new-coverage input 在我這邊可能已經 redundant（因為我可能已經走過那條 edge）。只留真的對我也是新的。
- **加自己的 id**：import 進來的 entry 檔名會標記來源（`sync:fuzzer01,id:000042`）。

這個 sync 是檔案系統級別，**不需要 socket、不需要共享 memory**。乾淨、robust。代價是有幾秒延遲（不是 real-time）— 對 fuzzing 這個多是 OK 的。

## AFL++ 的現代變化：`-M` 不再那麼特殊

Ch 9 提到 AFL++ 預設關閉 deterministic。這讓 `-M` 的原始理由（「有人要做 deterministic」）變淡了。現代 AFL++ 的 `-M` 還有幾個殘留意義：

1. **狀態 file 獨特性**：fuzzer 的 state file 格式略不同，方便辨識。
2. **某些 tool 預期有 `main`**：`afl-whatsup`、`afl-plot` 等 reporting 工具假設有一個 main instance。
3. **historical compatibility**：給老 script 用。

實務上現在通常這樣跑：

```bash
# 第一個 instance 用 -M，保持相容
afl-fuzz -M main -p fast -i seeds/ -o sync/ -- ./target @@

# 其他用 -S，配不同 schedule
afl-fuzz -S slave1 -p coe -i seeds/ -o sync/ -- ./target @@
afl-fuzz -S slave2 -p explore -i seeds/ -o sync/ -- ./target @@
afl-fuzz -S slave3 -p rare -i seeds/ -o sync/ -- ./target @@
```

## 多樣性是 parallel 的核心價值

最好的 parallel 配置不是「多個同樣的 fuzzer 跑」，而是「**刻意讓每個 instance 策略不同**」。這樣 swarm 覆蓋的搜尋空間才寬。

建議的 diversity 軸：

### Power schedule

`-p fast` / `-p coe` / `-p explore` / `-p rare` / `-p quad` — Ch 10 講過的那些。每個 instance 配不同 schedule，探索傾向不同。

### Mutator 偏好

`-L 0` 開 MOpt — 有的 instance 開有的不開。

### Sanitizer 組合

編幾份 binary：

- `target.asan`：有 ASan
- `target.msan`：有 MSan（要能 build）
- `target.ubsan`：有 UBSan
- `target.plain`：無 sanitizer，純跑 coverage（快）

然後分工：

```bash
afl-fuzz -M main -i seeds/ -o sync/ -- ./target.plain @@
afl-fuzz -S asan -i seeds/ -o sync/ -- ./target.asan @@
afl-fuzz -S ubsan -i seeds/ -o sync/ -- ./target.ubsan @@
```

`target.plain` 跑得快負責 coverage expansion，`target.asan` 跑較慢但 import 到 plain 找的 input 時會發現記憶體 bug。**分工從而 symmetric：所有 instance 共享 coverage 發現，但 crash-detection 能力不同**。

### Instrumentation 模式

兩個 instance 用不同 instrumentation：

- 一個 PCGUARD（有 collision 但 build 快）
- 一個 LTO（collision-free）

這讓 edge ID 的隨機基底不同 — 某些 collision 在一個 instance 裡被遮蔽，在另一個 instance 能看見。

### Dictionary / Custom mutator

一個 instance 開 grammar mutator，另一個不開。開的跑結構化探索，不開的跑 raw byte-level — 互相 sync 能拿到不同 flavor 的 input。

## CMPLOG secondary：獨立 pipeline

Ch 12 提過 CMPLOG 要 `-c cmplog_binary`。這個概念在 parallel 下延伸 — **指定某個 instance 專門跑 CMPLOG**：

```bash
# 主 instance 不開 cmplog（throughput 優先）
afl-fuzz -M main -i seeds/ -o sync/ -- ./target @@

# 另一個 instance 開 cmplog（負責破 magic bytes）
afl-fuzz -S cmplog_runner -c ./target.cmplog -i seeds/ -o sync/ -- ./target @@
```

cmplog_runner 跑 redqueen I2S 替換，找到的新 input 透過 sync 傳給 main。main 繼續快速跑。

## 配比建議

假設 8-core 機器，典型分配：

```
1 × main (fast + plain)          ← 主力 coverage
2 × secondary (explore + plain)  ← 隨機探索
1 × cmplog (rare + cmplog)       ← magic bytes 破解
1 × secondary (fast + asan)      ← 抓記憶體 bug
1 × secondary (explore + ubsan)  ← 抓 UB
1 × secondary (coe + grammar)    ← grammar mutator
1 × reserve                      ← 預留給 OS
```

7 個 fuzzer + 1 個系統保留。具體配比依 target 調。

## Monitoring parallel session

工具：

- `afl-whatsup sync_dir/`：列出所有 instance 的狀態（exec/s、cycles、paths...）
- `afl-plot sync_dir/main/ plot_out/`：畫某個 instance 的 execution trend
- `tail -f sync_dir/*/fuzzer_stats`：即時看各家狀態

關鍵觀察：**總 exec/s** 是不是各 instance 加總（大致應該是）；**paths_total 在幾個 cycle 後是否還在長**（長得越快越好）；**`last_path` 各 instance 相近嗎**（差距大表示有 instance 慢了）。

## Sync 的成本

每 20 秒 sync 一次，對有大 queue 的 session 可能耗時。對小 target 不明顯，對 long-running session 可用：

- `AFL_IMPORT_FIRST=1`：跑之前先 import 一輪，之後 sync 頻率降。
- `AFL_NO_AUTODICT=1`：關掉 auto-dict 的 embedding 自動 load（如果已經有 dict）。

## 常見誤解

- **「越多 instance 越好」**：不完全。sync 成本和 queue 重複率隨 instance 數增加。8 core 開 4–6 個主力 + 1–2 個 specialist 通常效益最高。
- **「-M 現在還很重要」**：不。現代 AFL++ 下 -M 和 -S 的差異很小，主要是歷史相容。
- **「sync 目錄一定要在 local disk」**：最好是，但 network FS（如果無 lock 問題）也能用。SSD vs HDD 差異在 sync 頻繁時可見。

## 自我檢核

- [ ] 能說出 `-M` 和 `-S` 的歷史分工以及為什麼現在差異變小
- [ ] 知道 queue sync 用檔案系統做通訊、有幾秒延遲
- [ ] 能列舉 parallel 配置的 diversity 軸（schedule、sanitizer、instrumentation、mutator）
- [ ] 知道 CMPLOG 可以讓專門 instance 跑
- [ ] 記得 `afl-whatsup` 是看 session 健康狀態的主要工具

下一章看如何把 fuzzer 跑出來的 crash 清理、縮短、判定 unique — crash triage 的演算法。

→ [Ch 17 Crash triage：uniqueness、tmin、cmin 的演算法](./17-crash-triage.md)
