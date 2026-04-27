# 練習 A — 寫一份 Coremark 效能報告

> 目標：實際 run Coremark、收集多組數據、寫成一份 professional performance report。Template 對應 SiFive / 客戶 benchmark team 的標準格式。

## 為什麼寫 report

bench 跑完不等於工作完。**report 是 deliverable**。格式合規、分析到位的 report 比數字本身更 impact。

面試時拿出這份 report 讓 interviewer 感受你懂 benchmark 方法論。

## 任務

在你能跑 Coremark 的環境（host native、QEMU-user、真 RISC-V hardware 皆可），做：

1. Build 4 個版本 (不同 `-march` / `-O`)
2. 跑 5+ 次、量數字
3. 算 statistics
4. 寫成 report

## Step 1: 環境

```bash
git clone https://github.com/eembc/coremark
cd coremark
```

看 README 確認 build instruction。

## Step 2: 定義 test matrix

```
Variant 1: -O2                          baseline
Variant 2: -O3                          
Variant 3: -O3 -flto                    
Variant 4: -O3 -flto -march=rv64gc_zba_zbb_zbs_zicond
```

對 RISC-V target：

```bash
# host native (if x86)
make PORT_DIR=linux64 XCC=gcc XCFLAGS="-O2" ITERATIONS=500000

# For RISC-V (example)
make PORT_DIR=linux64 XCC=riscv64-linux-gnu-gcc \
     XCFLAGS="-O2 -march=rv64gc" ITERATIONS=500000
```

## Step 3: 跑 benchmark

```bash
# 對每個 variant
for i in 1 2 3 4 5; do
    ./coremark.exe > result_$i.txt
    sleep 2
done
```

用 hyperfine 自動化：

```bash
hyperfine --warmup 3 --runs 10 \
    --export-json results.json \
    "./coremark-O2.exe" \
    "./coremark-O3.exe" \
    "./coremark-O3-lto.exe" \
    "./coremark-O3-lto-march.exe"
```

## Step 4: 處理數據

```python
import json
import statistics as s

with open("results.json") as f:
    data = json.load(f)

for r in data['results']:
    print(f"{r['command']}: mean={r['mean']:.3f}s std={r['stddev']:.3f}s")
```

或 Python 專屬分析：

```python
import pandas as pd
import scipy.stats as stats

# 讀 10 run 的 Coremark 數字
runs = {
    'O2':       [4500, 4510, 4498, 4505, 4502, 4497, 4506, 4501, 4499, 4503],
    'O3':       [4580, 4585, 4582, 4578, 4583, 4581, 4579, 4584, 4586, 4580],
    'O3-lto':   [4650, 4648, 4652, 4651, 4649, 4653, 4650, 4647, 4654, 4651],
    'O3-lto-march': [4780, 4775, 4782, 4779, 4781, 4778, 4784, 4776, 4780, 4779],
}

for name, vals in runs.items():
    m = s.mean(vals)
    sd = s.stdev(vals)
    cv = sd / m * 100
    print(f"{name:15s}: mean={m:7.1f} stddev={sd:5.1f} CV={cv:4.2f}%")

# Compare O2 vs O3-lto-march
t, p = stats.ttest_ind(runs['O2'], runs['O3-lto-march'])
print(f"O2 vs O3-lto-march: t={t:.2f}, p={p:.4g}")
```

## Step 5: 寫 report

Template：

```markdown
# Coremark Performance Report

**Author**: [Your name]
**Date**: [Date]
**Target**: [CPU / frequency]
**Compiler**: [gcc version]

## Executive Summary

Compared 4 compiler configurations running Coremark on [target].
Best configuration achieved X Coremark/MHz, a Y% improvement over baseline.

## Test Environment

- **CPU**: [details]
- **Memory**: [size]
- **OS**: [version]
- **Compiler**: [full version]
- **Kernel governor**: performance
- **SMT**: disabled
- **ASLR**: disabled
- **Perf governor**: performance

## Methodology

- Coremark v1.0
- Iterations: 500,000 (to guarantee > 10 sec runtime)
- 10 runs per configuration
- Warmup: 3 discarded runs
- All runs on isolated CPU (taskset)

## Results

| Config | Coremark | Coremark/MHz | vs Baseline |
|--------|----------|--------------|-------------|
| O2 (baseline)          | 4502 | 3.75 | 0.0% |
| O3                     | 4582 | 3.82 | +1.8% |
| O3 + LTO               | 4650 | 3.87 | +3.3% |
| O3 + LTO + march=rv64gc_zbb_zba_zbs_zicond | 4780 | 3.98 | +6.2% |

All CV < 0.2%, p < 0.001 between neighbors.

## Analysis

[解釋各個 configuration 的 improvement 來源]

- `-O3` over `-O2`: marginal improvements from aggressive inlining
- `-flto` adds cross-TU optimization, primarily helps function call overhead
- Extra `-march` gives access to Zbb (bitmanip), which is hot in Coremark's list/CRC code

## Recommendations

For production release targeting [this CPU]:
- Use `-O3 -flto -march=rv64gc_zba_zbb_zbs_zicond`
- Expect 6-7% Coremark improvement over baseline `-O2`

For embedded (memory-constrained):
- Use `-Os -flto`
- Trade some speed for size

## Appendix: Raw Data

[table of 10 runs per config]
```

## 進階：對比硬體

如果有多 RISC-V boards：

- SiFive U74 vs P550 vs P670
- 同 Coremark binary 跑三個、對比 MHz 正規化分數

顯示 "microarchitecture delta"、不只是 compiler。

## 加 profile

Report 加一段："為什麼 Zbb 這麼有效"：

```bash
perf stat -e cycles,instructions,branch-misses \
    ./coremark-base.exe vs ./coremark-zbb.exe
```

比對 counts：

```
Coremark (-O2):          120B cycles, 180B instructions (IPC 1.5)
Coremark (zbb):          100B cycles, 150B instructions (IPC 1.5)
```

相同 IPC → 但 instructions 減少（Zbb combine 多條 → 一條）→ 快。

**這種 explanatory paragraph 是 professional report 的加分項**。

## 加 Coremark 內部 breakdown

Coremark 有三個 workload（list、matrix、state machine）。有些版本支援：

```
CoreMark 1.0 : 4780.00 / GCC 11.2 -O3 -flto ...
Per-component breakdown:
  List processing: 38%
  Matrix math: 32%
  State machine: 30%
```

某些 optimization 只對某 component 有效。report 裡指出。

## 檢核 checklist

Report 交出前：

- [ ] 有 executive summary
- [ ] 環境 detail（CPU、OS、compiler、governor）
- [ ] methodology 清楚（run 數、warmup、tune）
- [ ] 數字有 mean + std + CV
- [ ] 有 statistical significance 驗證
- [ ] 有 raw data appendix
- [ ] 有 actionable recommendation

## 面試用

完成後把 report 放 GitHub。面試時：

- 用 report 做 demo
- 解釋每個 configuration 為何 improve
- 聊 "如果我是 SiFive 工程師要如何往上 push"

**這比口頭 claim "我會 benchmark" 可信 100 倍**。

## 自我檢核

- [ ] 我 run 完 4+ 個 Coremark configuration
- [ ] 每個 configuration 10 次 run
- [ ] 我算出 mean、stddev、CV、p-value
- [ ] 我寫出完整 professional report
- [ ] Report 放 GitHub

## 下一步

→ [練習 B：用 llvm-mca 分析一段 hot loop](./practice-b-llvm-mca-case.md)
→ [Final Project：Performance case study](./final-project-perf-case-study.md)
