# Ch 11 — PGO / BOLT / Propeller：profile-guided 全家族

> 目標：理解 profile-guided optimization 的三種流派 — PGO（compiler-level）、BOLT（binary-level）、Propeller（linker-level）。這些是 production release 常用的效能 booster。

## 概念：profile-guided 是什麼

一般 compile：compiler 不知道 runtime 行為、只能做保守假設（e.g., branch 50/50 猜）。

**Profile-guided**：先跑代表性 input、收集 profile、再 compile 時用 profile 指導優化：

- 哪個 branch 常 taken？
- 哪些 function 常被 call？
- 哪些 code path hot？

效能提升典型 10-30%。**production release 常用**。

## Three flavor

| 流派 | 介入時機 | 範例 |
|------|---------|------|
| **PGO** | Compile | GCC / Clang `-fprofile-generate/-fprofile-use` |
| **AutoFDO** | Compile | Google 的 PGO variant, 用 perf profile |
| **BOLT** | Post-link (binary rewrite) | Facebook 的 tool |
| **Propeller** | Link time | Google 推的、ThinLTO 兼容 |

每個介入點不同、trade-off 不同。

## PGO：Compiler Level

### 傳統 instrumented PGO

兩階段 build：

**Stage 1：Generate**

```bash
clang -fprofile-generate=/tmp/prof -O2 foo.c -o foo-gen
./foo-gen input.txt
# 產生 /tmp/prof/*.profraw
llvm-profdata merge -output=foo.profdata /tmp/prof/
```

`-fprofile-generate` 插入 instrumentation code (count branch, function call)。執行 input → 收集 profile。

**Stage 2：Use**

```bash
clang -fprofile-use=foo.profdata -O2 foo.c -o foo-opt
```

用 profile 重 compile、optimize based on data。

### AutoFDO：from perf

Instrumented PGO 有 overhead（instrument 版本慢 2-3×）。**AutoFDO** 用 perf 的 profile：

```bash
# 1. Build with -g
clang -g -O2 foo.c -o foo

# 2. Run with perf
perf record -e br_inst_retired.near_taken -F 997 -g ./foo

# 3. Convert to AutoFDO profile
create_llvm_prof --binary=foo --profile=perf.data --out=foo.afdo

# 4. Rebuild
clang -fprofile-sample-use=foo.afdo -O2 foo.c -o foo-opt
```

**優勢**：Production binary 就能 profile、不用 instrumented build。

### PGO 的實際效果

常見 improvement：

- Chrome / Firefox：+10-15% faster
- MySQL：+10% QPS
- Linux kernel：+1-3% on SPEC

提升來自：

- **Hot function inline**：小 hot function 一定 inline
- **Cold function outline**：cold 不 inline、保持 I-cache clean
- **Branch layout**：hot path straight-line、cold path jump-aside
- **Register allocation prioritization**：hot function 優先用 good reg

## BOLT：Binary Level

**BOLT (Binary Optimization and Layout Tool)** 是 Facebook 2019 開源的工具。操作 **已 linked binary**，不需要 source 或 recompile：

```
Binary + perf profile → BOLT → Optimized Binary
```

**優勢**：

- 不用 build from source
- 可以 optimize 別人 compile 好的 binary
- 比 PGO 晚介入 → 可以做 compiler / linker 做不到的 layout

**典型 gain**：PGO 之上再 +5-10%。

### BOLT 主要優化

1. **Function reordering**：把 hot function 放 `.text` 前面（I-cache friendly）
2. **Basic block reordering**：hot path 直線、cold 跳開
3. **Function splitting**：cold BB 移到獨立 section
4. **Inline expansion**：某些 very-hot inline
5. **Jump instruction optimization**：短 branch 取代長 branch

### 跑 BOLT

```bash
# 1. Build binary (with special relocations kept)
clang -Wl,-q foo.c -o foo

# 2. Run with perf
perf record -e cycles:u -j any,u -o perf.data ./foo

# 3. Feed to BOLT
perf2bolt -p perf.data -o foo.fdata ./foo
llvm-bolt foo -o foo.bolted -data foo.fdata -reorder-functions=hfsort+ ...
```

## Propeller：Linker Level

Google 推的中介方案：

- 比 BOLT 更 integrated（跟 linker 合作）
- 比 PGO 晚介入（已 codegen 完）
- ThinLTO 兼容

實務上 2026 還在 adopt，比 BOLT 年輕。

## 選哪個

```
需要 fastest: BOLT > Propeller > PGO > no profile
需要最簡單: PGO (two-step build)
需要 no source: BOLT
生產 deploy: PGO + BOLT 疊加最強
```

## AutoFDO + BOLT 組合拳

Google 內部 C++ code 典型流程：

```
1. Build with PGO (AutoFDO) → +10%
2. BOLT optimize → +5-10%
Total: +15-20% vs no profile
```

對 Chrome 這 level 的 codebase，幾百工程師年的 optimization opportunity。

## Profile 的 representativeness

**所有 profile-guided 的前提**：profile 代表 production workload。

如果用 micro-benchmark profile 然後 deploy production → profile mismatch → 可能變慢。

實務：

- **多 workload profile 混合**
- **定期 refresh profile**
- **監控 production metric** 驗證

## RISC-V 上的 PGO

LLVM 支援 RISC-V PGO（AutoFDO、instrumented）。GCC 也支援。但：

- **perf profile** 需要 RISC-V 硬體
- Cross-compilation 的 PGO 需要 run target 再 profile
- 工具鏈成熟度仍在追趕

SiFive 內部肯定用 PGO 對 benchmark。公開文件少。

## 一個 PGO 真實例子

對 SQLite 的 `.so`：

```
Baseline (-O2):          100% (1.00 GB/s insert)
PGO (AutoFDO):           105% (1.05 GB/s)
PGO + BOLT:              112% (1.12 GB/s)
PGO + BOLT + ThinLTO:    118% (1.18 GB/s)
```

累積幾種優化 → 18% 改進。**production 級別的差異**。

## BOLT 的硬體需求

BOLT 的 profile collection 需要 **Intel LBR**（Last Branch Record）或類似 feature。AMD Zen 2+ 也支援。**RISC-V 目前沒 LBR equivalent**。

所以 RISC-V 的 BOLT support 處於早期。**2024 有 patches、還不成熟**。SiFive 可能在 develop。

## Profile 的 privacy issue

Production profile 可能含敏感 data（hot path 反映用戶 usage pattern）。某些 case 不能隨意 share。

Google / Meta 有 anonymized PGO workflow。小公司通常跑 synthetic benchmark 當 profile。

## 跟 LTO 的互動

PGO + LTO 疊加效果：

```
Baseline:          100
PGO:               108
LTO:               103
PGO + LTO:         112
```

**Combine 通常比單用強**。現代 release 標配 both。

## 常見誤會

1. **「PGO 只對 large app 有用」**：不。一千行 benchmark 也能受益。
2. **「PGO 自動改到完美」**：profile-mismatch 就會退步。還是要驗證。
3. **「BOLT 取代 PGO」**：補充關係。BOLT+PGO > PGO > BOLT。
4. **「AutoFDO = 慢」**：不。AutoFDO 用 production binary、比 instrumented PGO 更 scalable。
5. **「PGO 影響 correctness」**：不。只改 codegen、語意保證相同。

## 動手練習

1. 對一個小 C program 做完整 PGO workflow（instrumented）、比較 runtime。
2. 嘗試 AutoFDO：用 perf record → create_llvm_prof → rebuild。
3. 安裝 BOLT、對一個 binary 做 optimization、比較 size + speed。
4. 讀 LLVM BOLT 的 README，列出它主要 optimization。
5. 設計一個 workflow：「客戶 A 用 X benchmark，客戶 B 用 Y」 — 你怎麼 build PGO version？

## 自我檢核

- [ ] 我能解釋 PGO / AutoFDO / BOLT / Propeller 的差異
- [ ] 我能完整跑 instrumented PGO 兩階段 build
- [ ] 我知道 BOLT 是 post-link 的
- [ ] 我知道 RISC-V 上 BOLT 的限制（沒 LBR）
- [ ] 我能 articulate 為什麼 profile representativeness 重要

下一章獨立看 LTO 的效果量測。

→ [Ch 12 LTO 效果量測](./12-lto-measurement.md)
