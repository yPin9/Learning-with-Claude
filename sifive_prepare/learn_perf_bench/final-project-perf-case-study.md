# Final Project — Performance Case Study

> 目標：整合全課程 — 選一個真實 workload、profile、找 bottleneck、propose compiler-level optimization、validate。產出 2-3 頁 professional report。這份 report 可以直接給 SiFive 面試當 portfolio。

## 為什麼這是好 final project

1. **模擬 SiFive 工作流**：拿到 benchmark regression → profile → propose → validate
2. **整合 Ch 1-14 的知識**：每章的技能都會用上
3. **可放 GitHub**：做完直接當履歷 showcase
4. **Scale 可調**：小 workload 一天、大 case study 兩週

## 流程

```
Phase 1: Pick a workload (1 day)
Phase 2: Establish baseline (1-2 days)
Phase 3: Profile to find hot function (1-2 days)
Phase 4: Analyze bottleneck (2-3 days)
Phase 5: Propose + implement optimization (3-7 days)
Phase 6: Validate + regression test (1-2 days)
Phase 7: Write report (1-2 days)
```

總共 10-20 天。一般人選 2 週 scope。

## Phase 1: Pick workload

選擇 criterion：

- **Real-world**：不是 toy code
- **Compute-bound**：profile 有意義（否則 I/O bound 工具 useless）
- **Reproducible**：能固定 input 多次跑
- **Open-source**：方便 share / review

### 候選

**Level 1（簡單）**：
- `zlib` compression/decompression
- `libpng` decode
- `JSON parsing` (simdjson)
- `regex matching` (pcre)

**Level 2（中等）**：
- `OpenSSL AES`（crypto）
- `libjpeg-turbo` decode
- `miniz` compression
- `SQLite query`

**Level 3（進階）**：
- `Coremark` + specific sub-benchmark
- `SPEC` 某個 single benchmark (if you have license)
- `FFmpeg transcode` (一個 codec)

**建議**：第一次做選 Level 1。

## Phase 2: Establish baseline

Build workload、準備固定 input、量 baseline 效能：

```bash
# Example: zlib compress
git clone https://github.com/madler/zlib
cd zlib
./configure
make

# 準備 input
dd if=/dev/urandom of=test.dat bs=1M count=100

# Baseline measurement
hyperfine --warmup 3 --runs 10 \
    './minigzip -d < test.gz > /dev/null'
```

記錄：mean、stddev、CV、p-value。

## Phase 3: Profile

```bash
perf record -F 997 -g --call-graph dwarf ./minigzip -d < test.gz > /dev/null
perf report
```

或 FlameGraph：

```bash
perf script | stackcollapse-perf.pl | flamegraph.pl > flame.svg
```

**找 top 3 hot function**。通常佔 50%+ total time。

典型結果（zlib）：

```
30% inflate_fast
25% adler32
15% inflate
...
```

## Phase 4: Analyze

選 top 1 (`inflate_fast`)。看 asm：

```bash
objdump -d libz.so | less
# 搜尋 inflate_fast
```

跑 perf annotate：

```bash
perf annotate inflate_fast
```

看哪些 instruction 佔時間、哪個 basic block 熱。

### 跑 llvm-mca 深度分析

抓 hot inner loop asm、加 MCA markers、run。找 bottleneck 類型：

- Long dependency chain → needs reordering
- Resource saturation → needs different instruction mix
- Memory bound → needs prefetch or cache-friendly layout

### 用 perf stat 驗證

```bash
perf stat -d ./minigzip -d < test.gz > /dev/null
```

看 IPC、cache miss、branch miss。給 diagnosis 方向。

## Phase 5: Propose optimization

**不要 hack source**。Propose compiler-level 改動。

### 可能的提案

**A. Pattern-based**：發現 compiler 漏某個 pattern
```
"inflate_fast 中的這 4 條 asm 序列可以用 X 指令 1 條取代，
 但 LLVM 的 DAGCombine 沒認這個 pattern。建議在 
 RISCVISelLowering.cpp 加一條 combine rule。"
```

**B. Scheduling-based**：scheduler 排得不好
```
"SiFive P670 的 sched model 沒反映 mul + load 的 pipelining。
 實際 hardware 可 parallel、model 串行化。改 SiFive P670 
 sched model 的 WriteIMul 資源定義。"
```

**C. Intrinsic-based**：某 op 沒對應 intrinsic
```
"inflate 用的 CRC calculation 應該能用 `clmul`（Zbc）。
 但 LLVM 沒對 CRC pattern 自動 match。建議加 pattern 
 或至少 intrinsic 讓 source 可呼叫。"
```

**D. Vectorization**：loop 該 vec 但沒
```
"adler32 的 inner loop 是完美 vectorize candidate。
 Compiler report miss 原因是 cost model 保守。tune 
 vectorizer cost 讓它願意 vectorize RVV。"
```

選一個，formalize。

### 實作 MVP

如果有能力，**實際 prototype** 改動：

- 加 DAGCombine rule
- 改 scheduling model
- 手寫 intrinsic 版 source → compare asm + perf

這讓 proposal 有 data 背書，比純紙上談兵強 10 倍。

## Phase 6: Validate

對你的 optimization：

1. **Micro**：改前後 asm 差異
2. **Meso**：isolated function benchmark
3. **Macro**：full workload benchmark
4. **Regression**：其他 workload 沒變慢

寫對比 table：

| Config | Baseline | Your Opt | Δ |
|--------|----------|----------|---|
| inflate_fast (micro) | 100 ns | 75 ns | -25% |
| minigzip (meso) | 2.3 s | 2.0 s | -13% |
| other benchmark A | 1.0 s | 1.0 s | 0% (safe) |
| other benchmark B | 0.8 s | 0.78 s | -2% (pleasant) |

## Phase 7: Report

Format：

```markdown
# Performance Case Study: [Workload]

**Author**: Your name
**Date**: 2026-XX-XX
**Target**: [CPU]

## Executive Summary

Analyzed [workload] performance on [target]. Identified 
[X% / description] optimization opportunity in [hot function]. 
Propose compiler-level fix in [where]. Prototype shows 
[gain]% improvement with no regression in [other 
benchmarks tested].

## Workload
[description, why picked]

## Baseline
[number, environment, method]

## Profile
[flamegraph + perf report excerpt]
Top 3 hot functions:
1. inflate_fast (30%)
2. adler32 (25%)
3. inflate (15%)

## Bottleneck Analysis

### inflate_fast
[perf annotate + llvm-mca details]

Diagnosis: [what's wrong]

## Optimization Proposal

[What to change, where, why]

### Prototype (if any)
[details + code snippet + patch link]

## Validation

[before / after tables]
- Micro: inflate_fast speed Δ
- Macro: full minigzip speed Δ  
- Regression: other 5 workloads tested, no significant change

## Discussion

### Alternative approaches considered
[didn't go with these because ...]

### Generalization
[this pattern can also help XXX]

## References

[links to relevant LLVM commits, blog posts, specs]

## Appendix: Raw Data
[full numbers, for reproducibility]
```

## 評估標準

**60 分**（入門）：
- 完成 Phase 1-4（identify hot + propose）
- 紙上 proposal，no implementation

**75 分**：
- 加 Phase 5 prototype（even if rough）
- 有 validation 數據

**90 分**：
- Proposal 是 real LLVM-level 改動
- Prototype compile 得起來
- Micro + macro benchmark 驗證

**100 分**：
- 改動 submitted 成 LLVM PR
- 有 upstream engagement
- Community feedback

即使 60 分也是強履歷。越高越好、但不 force 到 100。

## 推薦 workload + optimization 組合

以下是 pre-thought combination，方便你入手：

### Combo 1: zlib + Zbb popcount

- Workload: zlib compress
- Hot function 可能含 popcount-style code
- Zbb 可以加速

### Combo 2: libpng + RVV

- Workload: PNG decode
- Hot inner loop (filter unfold) 適合 vectorize
- 檢查 RVV auto-vec、若無 propose 改進

### Combo 3: miniz + scheduling

- Workload: miniz (zlib alternative)
- 找 hot function、看 sched 有無改善空間
- Propose sched model tune

### Combo 4: Coremark + pattern

- Workload: Coremark 的 list 或 matrix phase
- 找 compiler 漏的 pattern
- Propose DAGCombine rule

## GitHub showcase

Repo 結構：

```
perf-case-study-minigzip/
├── README.md               ← main report
├── data/
│   ├── baseline.csv
│   ├── optimized.csv
│   ├── regression.csv
│   └── flamegraphs/
├── scripts/
│   ├── bench.sh
│   ├── analyze.py
│   └── plot.py
├── prototype/
│   └── llvm-patch.diff
└── docs/
    ├── proposal.md
    └── background.md
```

面試時 Cursor 帶 interviewer 走過這個 repo、5 分鐘總結。

## 給履歷的條目

```
Performance Case Study: minigzip Decompression on RISC-V SiFive U74
- Profiled 100MB compression workload, identified inflate_fast as top hot
- Diagnosed backend pressure bottleneck via llvm-mca pipeline analysis
- Proposed Zbb-based pattern match for CRC inner loop
- Prototyped LLVM DAGCombine rule, measured 12% minigzip speedup
- No regression across 5 other benchmarks (Coremark, SPEC subset, custom)
- GitHub: [link]
```

## 最後

這 project 完成後你有：

- 一份 professional performance report
- GitHub portfolio
- Performance analysis 從頭到尾的 experience
- 面試時 demo 的彈藥

**這是 SiFive job spec 第二條 responsibility 的直接證明**。

---

## 自我檢核

- [ ] 我選好 workload 並 establish baseline
- [ ] Profile 找到 hot function
- [ ] Analysis 用 llvm-mca + perf 雙角度
- [ ] Proposal 是 compiler-level（不是 source hack）
- [ ] Validation 有 micro + macro + regression
- [ ] Report 放 GitHub、LinkedIn 可 link

完成任一 Stretch Goal (prototype / LLVM PR)：你已經是 SiFive level compiler engineer。
