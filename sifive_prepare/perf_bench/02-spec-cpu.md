# Ch 2 — SPEC CPU：業界 benchmark 之王

> 目標：理解 SPEC CPU 的歷史、結構、報告規範、常見誤用。就算你不花錢買 license，懂這套 benchmark 方法論是 SiFive 面試基本功。

## 為什麼 SPEC 重要

**SPEC CPU 是業界最受認可的 general-purpose CPU benchmark**。所有 CPU 廠（Intel、AMD、Arm、Apple、SiFive、T-Head）發佈新 chip 時都要公布 SPEC 分數。

- 每 5-10 年出新版（2000 → 2006 → 2017 → 2025？）
- 由 SPEC （Standard Performance Evaluation Corporation）維護
- 需買 license（~$2000-5000 USD）
- 成為「這 chip 多快」的共同語言

**不懂 SPEC 不能跟 benchmarking team 對話**。

## SPEC CPU 的版本

| 版本 | 年 | 狀態 |
|------|-----|------|
| SPEC CPU89 | 1989 | 古早 |
| SPEC CPU92 | 1992 | |
| SPEC CPU95 | 1995 | |
| SPEC CPU2000 | 2000 | 退休 |
| SPEC CPU2006 | 2006 | retired (2018) |
| **SPEC CPU2017** | 2017 | **current** |
| SPEC CPU 2025 (?) | TBD | |

本課以 **CPU2017** 為主。2026 時點仍是 active。

## CPU2017 的結構

```
SPEC CPU2017
├── SPECrate (throughput)          多個 copy 並跑
│   ├── rate_int (10 個 integer)
│   └── rate_fp (13 個 FP)
└── SPECspeed (latency)            單個 run, time to finish
    ├── speed_int (10 個 integer)
    └── speed_fp (10 個 FP)
```

- **SPECrate**：跑 N 份相同 benchmark（N = hyper-thread 數）、衡量 throughput。對 server 有意義。
- **SPECspeed**：跑一份、衡量單核效能。對 latency-sensitive 有意義。

## SPECrate_int：10 個 integer 測試

```
500.perlbench_r     Perl interpreter
502.gcc_r           GCC compiler
505.mcf_r           Memory-hungry combinatorial optimization
520.omnetpp_r       Network simulator
523.xalancbmk_r     XML transformation
525.x264_r          Video encoding
531.deepsjeng_r     AI (chess)
541.leela_r         AI (Go)
548.exchange2_r     Sudoku solver
557.xz_r            Data compression
```

各自代表不同 workload：

- `perlbench` / `gcc`：compiler workload，pointer-chasing 多
- `mcf`：memory-bound，cache miss 高
- `x264`：SIMD-heavy
- `deepsjeng` / `leela`：branch-heavy

**不同 CPU 在不同 benchmark 表現差很大**。這是 SPEC 的 power —— reveal weaknesses。

## SPECrate_fp：13 個 FP 測試

```
503.bwaves_r        Fluid dynamics
507.cactuBSSN_r     General relativity
508.namd_r          Molecular dynamics
510.parest_r        Finite element analysis
511.povray_r        Ray tracing
519.lbm_r           Fluid dynamics
521.wrf_r           Weather
526.blender_r       3D rendering
527.cam4_r          Atmospheric modeling
538.imagick_r       Image manipulation
544.nab_r           Molecular dynamics
549.fotonik3d_r     Electromagnetics
554.roms_r          Regional ocean model
```

都是 scientific computing。跟 server / desktop 用途差距大。

## 一份 SPEC 分數怎麼算

1. 對每個 benchmark 跑 3 次、取 median time
2. 算 **ratio**：`reference_time / your_time`
   - reference machine: 一台 2017 的 Intel Xeon，所以你跑得比它快 ratio > 1
3. 對所有 benchmark 的 ratio 算 **geometric mean**
4. 那個 geomean 就是 SPEC score

範例：

```
Benchmark     Reference  Yours     Ratio
perlbench_r   1600s      400s      4.00
gcc_r         1800s      500s      3.60
...
                                   ↓
                        geomean = 3.5
                        SPECrate int = 3.5
```

**geomean 而非 mean**：因為每個 benchmark 的 ratio 分布不均、幾何平均 better handles outlier。Ch 4 會講。

## Reportable vs Non-reportable

SPEC 有嚴格規範：

**Reportable run**：

- 所有 benchmark 必須 3 次 pass
- 用 "base" flags（所有 benchmark 同一 flag）
- 跑在生產 kernel + driver
- 提交到 spec.org 前需 review
- **這是公告的官方分數**

**Non-reportable (peak)**：

- 每個 benchmark 可用不同 flag（手動 tune）
- 可以激進優化
- 比較各 compiler / flag 效果

發佈的公告通常兩個都有。`base` 是 conservative、`peak` 是 max。

## Base / Peak / Strict rules

規範多到一本書（SPEC runrules.html）。重點：

- **所有 benchmark 用同 compiler version**
- **不能改 source code**（除了 spec 允許的 portability change）
- **flags 要合理**（不能硬寫 benchmark-specific 的 hack）
- **3 runs 中位數**

違反 → 提交 rejected.

## 跑一次 SPEC 的流程

假設你買了 license、有 DVD image：

```bash
# Install SPEC
cd /cpu2017/
./install.sh

# Source env
source shrc

# Choose config
cd config/
cp -a example-linux-x86-gcc.cfg my.cfg
# Edit my.cfg (compiler, flags)

# Run reportable
runcpu --config=my.cfg --action=run --noreportable intrate
# --noreportable: 快跑 + 不嚴格檢查

# Run reportable
runcpu --config=my.cfg intrate
# 跑 3 次 + 完整檢查，慢 3-5 小時

# Results in:
result/ directory
```

**第一次跑常常 fail**：某 benchmark 沒 build、flag 不對、disk 不夠大（SPEC 吃 30 GB+）。慢慢調。

## Config file 的神秘

`.cfg` 文件幾百行、控制所有 build + run 細節：

```
default:
    CC           = gcc
    CXX          = g++
    FC           = gfortran
    OPTIMIZE     = -O3 -march=native
    
integer:
    OPTIMIZE     = -O3 -march=native -flto

500.perlbench_r:
    PORTABILITY += -std=gnu89
    
502.gcc_r:
    EXTRA_FLAGS  = -fpermissive
```

Per-benchmark 的 portability flag 很多、SPEC 官方有 template。

## RISC-V 上的 SPEC

RISC-V 跑 SPEC CPU2017 可以但要注意：

- **編譯 issue**：某些 benchmark (`xz_r`) 要手動 port
- **Memory 需求**：`mcf_r` / `wrf_r` 要 4GB+。 RISC-V SBC 常只 4-8 GB，剛夠
- **長時間**：SiFive U74 (1.5 GHz) 跑完一次完整 SPEC 約 8-24 小時
- **ABI 一致**：`lp64d`、`-march=rv64gc` 以上是 standard

SiFive 官方每個 new core release 都跑 SPEC。看他們的 datasheet / whitepaper。

## SPEC 的商業意義

- Intel / AMD / Arm / Apple 的 marketing：「New CPU is 20% faster in SPEC int」
- Cloud vendor 選 CPU 時參考 SPEC rate
- OEM 合約裡寫 SPEC 最低分數

所以 **compiler 優化 1% 在 SPEC 上 → 商業上大事**。SiFive 之類的客戶極在意。

## SPEC 以外

SPEC CPU 不代表一切：

- **Cloud workload** → CloudSuite
- **AI** → MLPerf, TensorFlow bench
- **Android** → AnTuTu, Geekbench
- **Desktop** → Cinebench, Geekbench
- **Crypto** → cryptoPP bench
- **Embedded** → Coremark, Embench (Ch 3)

現實：**多benchmark 並行**。SPEC 是 common baseline。

## 「SPEC 分數怎麼 cheat」

歷史上有些廠商/compiler 被抓 SPEC-specific 優化：

- 某 compiler 認 `451.gobmk` 的 input 格式、generate specialized code
- 某 compiler 對 `401.bzip2` 的 source pattern 有 hand-tuned rewrite
- Benchmark-specific PGO

**SPEC 規則禁止這些**。但某些 flag（`-fprofile-use`）本身合法、只是要對所有 benchmark 一致。

**SiFive 不會做這種 cheat**—— 正派廠商的 compiler 工作是「all workload 都變好」，不是「SPEC 數字好看」。

## 讀 SPEC 官方 result

在 <https://www.spec.org/cpu2017/results/> 你會看到幾千份 submission。每份：

- System config
- Compiler flags (per benchmark)
- Ratio per benchmark
- Final geomean

讀這些 results 可以學：

- 不同硬體的相對效能
- 哪個 benchmark 的 ratio 跨平台差異大（→ 對那個 workload 敏感）
- Flag 組合的效果

## SPEC 的限制

- 代表傳統 desktop/server compute
- 沒 GPU、沒 DB、沒 network
- 2017 release、某些 workload 已過時
- 單線程 + 多 copy，不代表真正 multi-thread application

**別當聖經**。跟你的實際 workload 比對。

## 動手練習

如果沒 SPEC license：

1. 讀 SPEC CPU2017 的 benchmark 列表、每個 read 簡介，挑 3 個寫「這測什麼」。
2. 去 <https://www.spec.org/cpu2017/results/> 挑兩個 CPU (e.g., Apple M2 vs AMD Ryzen)，對比 result.
3. 讀 SiFive P870 / Ventana Veyron 的 whitepaper，看他們 claim 的 SPEC 分數。
4. 找 Coremark 跟 Dhrystone 的 source，理解它們相對 SPEC 更輕量。
5. 寫一份 "如果我有 SPEC license 會怎麼 setup RISC-V benchmark" 的 plan。

有 license 的話：

1. Install SPEC CPU2017。
2. 用 `--noreportable` 先 build check。
3. 挑一個 integer benchmark 跑完。
4. 改 flag (`-O2` vs `-O3`) 對比。
5. 讀一份 full reportable result 的 raw file。

## 常見誤會

1. **「SPEC 分數越高一定好」**：只代表 SPEC workload。你的 workload 可能反向。
2. **「SPECrate = 多核分數」**：SPECrate 是 throughput（多 copy），不是 concurrent workload。
3. **「我可以用 benchmark-specific flag」**：peak 可以，base 不可以。商用 marketing 通常引 base。
4. **「現代 CPU 都同一 ratio」**：不。mcf / xalancbmk 特別能 reveal memory bandwidth 弱點。
5. **「一次 run 數字信得過」**：SPEC 要求 3 次取中位數。

## 自我檢核

- [ ] 我能列出 SPEC CPU2017 integer 10 個 benchmark 的 3-5 個
- [ ] 我知道 SPECrate vs SPECspeed 差別
- [ ] 我能解釋 base vs peak
- [ ] 我知道 geomean 為什麼是 SPEC 的 aggregate 方式
- [ ] 我能讀 spec.org 上 result 並解讀

下一章看 Coremark / Embench — RISC-V 社群更常用的、相對輕量的 benchmark。

→ [Ch 3 Coremark / Embench：RISC-V 與嵌入式主力](./03-coremark-embench.md)
