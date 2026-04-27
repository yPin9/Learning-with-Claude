# Ch 9 — Flame graph 與 on-CPU profiling

> 目標：學會用 FlameGraph 視覺化 perf profile、辨認 hot function + call stack。這是 profile 分析的標準圖型，SiFive / Google / Netflix 都用。

## 什麼是 flame graph

**Flame graph** 是 Brendan Gregg 2011 年發明的視覺化方法。

```
┌─────────────────────────────────────────────────┐
│                      main                        │
├──────────────────────┬──────────────────────────┤
│        foo           │        bar                │
├────────┬─────────────┼─────┬────────┬───────────┤
│  sub1  │    sub2     │baz  │ baz    │    qux    │
├─┬──────┼────┬────────┼──┬──┼───┬────┼───┬───────┤
│ │ sub11│inner|mem_op │a │b │...│....│...│  ...  │
└─┴──────┴────┴────────┴──┴──┴───┴────┴───┴───────┘
```

**Y 軸**：call stack 深度（往上越深）
**X 軸**：time / sample count（寬 = 耗時）
**顏色**：通常隨機（只是為了區分 function）

**找「最寬的火焰」** = hottest 的 function + call stack。

## 產生 flame graph

三步：

```bash
# 1. Collect samples
perf record -F 997 -g ./program

# 2. Convert to text format
perf script > out.perf

# 3. Generate flame graph
stackcollapse-perf.pl out.perf | flamegraph.pl > flame.svg
```

打開 `flame.svg` 在瀏覽器。

### 工具來源

```bash
git clone https://github.com/brendangregg/FlameGraph
export PATH=$PWD/FlameGraph:$PATH
```

`stackcollapse-perf.pl` 跟 `flamegraph.pl` 是核心。

## Interactive flame graph

SVG 支援：

- 點擊 function → zoom 進去
- 右上 "Reset Zoom"
- Ctrl+F → 搜尋 symbol

複雜 profile 必用 interactive。

## 閱讀 flame graph

### 範例：simple program

```
┌────────────────────────────────────────────────┐
│                    main (100%)                  │
├────────────────────────────────────────────────┤
│                slow_func (97%)                  │ ← hot!
├────────────────────────────────────────────────┤
│                multiply (60%)                    │
└────────────────────────────────────────────────┘
```

解讀：

- `main` 佔 100%（entry）
- `slow_func` 佔 97% → 幾乎全部時間在它
- 其中 60% 在 `multiply`（child function）

改進目標：`multiply`。

### 範例：複雜 profile

```
main (100%)
├── parse_input (20%)
│   ├── tokenize (15%)
│   │   └── is_digit (10%) ←─ unexpected hot
│   └── build_ast (5%)
├── process_data (70%)
│   ├── foo (40%)
│   │   ├── bar (20%)
│   │   └── baz (20%)
│   └── qux (30%)
└── output (10%)
```

解讀：

- process_data 佔 70%（明顯 hot path）
- is_digit 佔 10%（小 function 卻 hot → 值得看）
- output 只 10%（不重要）

## 為什麼 flame graph 比 top-down list 好

`perf report` 的 text output 也能列 hot function、但：

- 沒 visual hierarchy
- 大 project 幾百 function 眼花
- 不容易看 "hot call stack"

Flame graph 一眼看出全景。面試 / team presentation 用 flame graph 傳達速度遠快於 text。

## 變種：Inverted flame graph（Icicle）

```
┌────────────────────────────────────────────────┐
│ main                                             │
└───┬──────────────┬──────────────────┬───────────┘
    │              │                   │
    ▼              ▼                   ▼
```

Y 軸反過來：**root 在上、leaf 在下**。對某些 mental model 更 natural。

`flamegraph.pl --inverted`。

## 變種：Differential flame graph

比較 before/after：

```bash
stackcollapse-perf.pl before.perf > before.folded
stackcollapse-perf.pl after.perf > after.folded
difffolded.pl before.folded after.folded | flamegraph.pl > diff.svg
```

**紅色 = 變慢、藍色 = 變快**。效能迴歸分析神器。

## 非 on-CPU flame graph

本章聚焦 on-CPU（CPU 在忙什麼）。其他類型：

- **Off-CPU flame graph**：show where thread 被 block 的時間（lock、I/O wait）
- **Memory flame graph**：show 哪裡 allocation
- **Hot-cold flame graph**：同時顯示 on-CPU + off-CPU

這些用 eBPF 採集。`bpf` 有 cover。

## FlameGraph 的陷阱

### 陷阱 1：Stack 收集錯誤

`-g fp` 在 `-fomit-frame-pointer` build 下 broken。flame graph 顯示：

```
[unknown] (100%)
```

**必用 `-g dwarf`** 或 build 加 `-fno-omit-frame-pointer`。

### 陷阱 2：Inlined function 消失

Compiler inline 掉的 function 在 flame graph 不顯示為 separate box。看到的是 caller function 變寬。

解法：LLVM 有 `--inline-threshold=0`（build time）保留 inline boundary，但會損 performance。

### 陷阱 3：Recursion 造成 stack 無限

Deep recursion (幾千層) 產生奇怪 flame graph。需要 post-process 截斷。

### 陷阱 4：Sampling bias

Sampling rate 不夠 → flame graph 不 representative。`-F 997` 跑至少 5 秒是 minimum。

## 即時 flame graph

```bash
# 邊跑邊看
perf record -F 997 -g -o out.data ./program
# Ctrl+C 後
perf script -i out.data | stackcollapse-perf.pl | flamegraph.pl > flame.svg
```

或用 **`hotspot`**（KDE GUI）直接 live profiling + flame graph。

## System-wide flame graph

整個系統：

```bash
sudo perf record -F 99 -a -g -- sleep 30
perf script | stackcollapse-perf.pl | flamegraph.pl > system.svg
```

低 frequency（99Hz）避免 overhead、30 秒 window。

看 kernel / daemon / 整個系統在幹嘛。

## FlameGraph 輸出的 format

```
main;process_data;foo;bar 42
main;process_data;foo;baz 38
main;parse_input;tokenize 15
...
```

每行：`call_stack value`。stack 用 `;` 分隔、最後是 sample count。

**這個 format 很 portable**。你可以自己產（從 eBPF、JIT profile 等）。

## Per-thread flame graph

```bash
perf script --per-thread | stackcollapse-perf.pl | flamegraph.pl > threaded.svg
```

多 thread program 看每 thread 行為。

## Flame graph 對 RISC-V 的 work

完全一樣。只要 `perf record` 能 work，flame graph 就能 work。

常見 issue：RISC-V binary 在 QEMU 上跑 → perf data 不準 → flame graph 誤導。

## Real-world demo

Facebook / Meta 的 post：用 flame graph 發現 PHP 的某 hash function 在 production 佔 12%。改一行 C → 整體省 3% CPU。

SiFive 內部多半有類似 case。這是 compiler 工程師找 optimization target 的主流方法。

## 替代：跟 flame graph 競爭的視覺化

- **Pyroscope**：持續 profiling 平台（Web UI）
- **Perfetto**：Google 推的 trace viewer
- **gprof2dot**：output call graph dot format
- **speedscope**：上傳 profile 看線上視覺化

對個人開發 flame graph 夠用、生產環境 Pyroscope 類 SaaS 方便。

## 動手練習

1. 裝 FlameGraph repo、生成你第一張 flame graph。
2. 看一個 hot function、在 svg 裡 Ctrl+F 搜尋 symbol 找到位置。
3. 產 before/after 兩張 flame graph，用 `difffolded.pl` 生成差異圖。
4. 用 `perf script --per-thread` 產 threaded flame graph。
5. 試 `hotspot` 或其他 GUI 工具。

## 常見誤會

1. **「Flame graph 橫向一定有 time order」**：不。X 軸是 sample counts（sorted 或 merged by function）。時間順序要用 perf timeline、不是 flame graph。
2. **「寬的一定要優化」**：寬可能是 intrinsic cost（memcpy 實際就是寬）。要能「縮窄」才值得。
3. **「深的一定差」**：不。深只是 call stack 深。stack 100 層仍可 fast。
4. **「缺 symbol 就 hopeless」**：加 debug info 或 install -dbg package 往往解決。
5. **「Flame graph 只適合 simple program」**：complex program 更需要 (否則眼花)。

## 自我檢核

- [ ] 我能生成 flame graph 從 perf data
- [ ] 我能讀 flame graph 找 hot call stack
- [ ] 我知道 inverted / differential / system-wide 變種
- [ ] 我知道為什麼 `-g dwarf` 重要
- [ ] 我能 live demo 給面試官看 flame graph 分析

Part 3 結束。下一章進 compiler-centric 分析 — 各種 flag 的真實效果。

→ [Ch 10 Compiler flag scan：-O2 vs -O3 真相](./10-compiler-flags.md)
