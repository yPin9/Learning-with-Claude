# Ch 10 — Compiler flag scan：-O2 vs -O3 真相、-march 選擇

> 目標：釐清常見 compiler flag 對 RISC-V binary 的實際效果。不是 "更高 = 更快"；每個 flag 都要 benchmark 確認。

## -O 系列的真相

### `-O0`

完全無優化。每個變數存 stack、每個 expression 一步一步。

用途：

- Debug build
- Teaching / learning compiler 行為

效能：**通常比 -O2 慢 3-10×**。別 production。

### `-O1`

基本優化：

- dead code elimination
- constant folding
- simple inline
- 基本 register allocation

比 -O0 快 2-5×。**幾乎沒人用 `-O1`** —— 往上跳到 `-O2`。

### `-O2`（predominant default）

大部分 optimization：

- function inlining (limited by size)
- loop optimization (partial unroll)
- CSE / GVN
- PRE (partial redundancy elimination)
- tail call optimization
- register allocation (greedy)

**Production 標準**。生態系幾乎所有 code 以 `-O2` 為 baseline。

### `-O3`

`-O2` 加激進優化：

- Aggressive inlining
- Complete loop unroll
- **Auto-vectorization**（預設某些 target 才在 -O3）
- 可能 instruction reorder

**現實：相對 `-O2` 實測通常 +0-5%**。某些 benchmark 甚至 regressed（code bloat、I-cache miss）。

```
SPEC CPU 2017 on Intel Skylake:
  -O2: baseline
  -O3: +2% geomean (某些 benchmark -3%)
```

**不要 blind 用 `-O3`**。驗證你的 workload。

### `-Os`

優化 size 而非 speed。

- 不 inline 大 function
- 不 unroll loop
- 選 short instruction sequence（即使慢一點）

用於嵌入式、code density 重要的場景。

### `-Oz`（Clang only）

比 `-Os` 更激進壓 size、可能犧牲更多速度。

### `-Ofast`

`-O3 + -ffast-math`。放寬 FP 約束（允許 reorder、假設 no NaN）。

**慎用**。對需要 IEEE 754 strict 行為的 scientific code 會造成誤差。

## `-march` 對 RISC-V 的影響

```
-march=rv64i       Base ISA 而已
-march=rv64g       base + M/A/F/D + Zicsr/Zifencei
-march=rv64gc      base + G + C (compressed) ← Linux baseline
-march=rv64gcv     + V extension
-march=rv64gc_zba_zbb_zbs       + B extensions (subset)
-march=rv64gc_zba_zbb_zbc_zbs_zicond ← 現代 baseline
```

`-march` 告訴 compiler「可以用什麼指令」。選越多 → compiler 可能產生更好 code、但 binary 只能跑在該 target。

### Profile-based `-march`

- **嵌入式**：精確寫你的 core 支援的（`rv32imac`, `rv32emc`, etc.）
- **Linux distro**：`rv64gc` 是穩妥 baseline（所有 mainstream core 有）
- **追新效能**：`rv64gcv_zba_zbb_zbs_zbkb_zksed_zksh` 等（需 target hardware 支援）

### 用 `-mcpu=`

更精細：

```bash
-mcpu=sifive-u74
-mcpu=sifive-p670
-mcpu=thead-c906
```

Compiler 知道具體 CPU → 用對應 scheduling model → 可能產生更 tuned code。

**效果通常比 -march 小**。scheduling 差異只 2-5%、`-march` 差異可能 15%。

### `-mtune=`

只影響 scheduling、不改 ISA 選擇。

```
-march=rv64gc -mtune=sifive-u74
```

意思：用 rv64gc 指令、按 U74 優化。產生的 binary 在其他 rv64gc 也能跑，但對 U74 最佳。

用於「通用 binary 但針對主要客戶 tune」。

## `-flto` (Link-Time Optimization)

Ch 14 of `elf_linking` 細講。效果摘要：

- **size**: -5% 到 -15%
- **speed**: +2% 到 +10%
- **link time**: 大幅增加
- **debuggability**: 降低

Production release 常開。Development 關。

### 子選項

```
-flto=thin       ThinLTO，parallelism 好，速度介於 no-LTO 跟 full LTO
-flto=full       Full LTO，最慢最大改
-flto=auto       自動選（GCC）
```

ThinLTO 最常用。

## Vector 相關

### `-ftree-vectorize`

啟用 auto-vectorization。GCC `-O3` 自動開。Clang `-O2` 就開。

```bash
clang -O2 -ftree-vectorize ... 
# clang 已 default enable
```

### `-fno-vectorize`

關掉 auto-vectorize。debug 時用。

### `-Rpass=loop-vectorize` (Clang)

印 vectorize 成功/失敗原因：

```bash
clang -O2 -Rpass=loop-vectorize -Rpass-missed=loop-vectorize foo.c -c
# foo.c:10:5: remark: vectorized loop (vectorization width: 4, ...)
# foo.c:15:5: remark: loop not vectorized: unsafe dependent memory operations
```

對改 compiler 人黃金工具。

## 一些「看似 free」的 flag

```
-fno-plt            不走 PLT，對 PIE 小省
-fno-semantic-interposition  library 內 call 不走 PLT（non-hookable）
-fvisibility=hidden  減少 exported symbol
```

每個都能省幾 % 效能，但可能影響 debuggability / interposability。

## 量測每個 flag 的方法

Scientific approach：

```
1. Baseline: -O2
2. Change one flag at a time
3. Benchmark 至少 5 run
4. t-test 是否 significant
5. 多 workload（不只一個 benchmark）
6. 記 regression + improvement
```

不要一次加 5 個 flag 然後 claim 都有用。

## 實測：不同 flag 對 Coremark

真實數字（SiFive U74 @ 1.2 GHz，大約 values）：

```
Flag                                     Coremark  Δ vs -O2
-O0                                        800     -82%
-O1                                       4000     -11%
-O2                                       4500      0
-O2 -mtune=sifive-u74                     4550     +1%
-O3                                       4580     +1.8%
-O3 -flto                                 4650     +3.3%
-O3 -flto -march=rv64gc_zba_zbb_zbs      4750     +5.5%
```

結論：`-O2` 之後的 optimization marginal。`-flto` + 更多 extension 累積才明顯。

## GCC vs Clang 的差異

對同一 C code、同 `-O2 -march=rv64gc`：

- 產生的 asm 不同
- Performance 差異 2-10%（benchmark-dependent）
- **沒有 consistent winner** — 不同 workload 不同贏

SiFive 工程師會**同時 benchmark 兩個 compiler**，各自 claim / improve。

## Profile guided optimization (PGO)

Ch 11 專講。簡版：

```
1. Build with -fprofile-generate
2. Run representative input
3. Build with -fprofile-use
```

兩階段 build 換 10-30% 速度。對某些 workload 效果超越 -O3。

## 基本 flag 建議

**Production release** (最安全)：

```
-O2 -flto
```

**追效能**：

```
-O3 -flto -march=rv64gc_zba_zbb_zbs_zicond -mtune=sifive-p670
```

**Embedded code size**：

```
-Os -flto
```

**Debug build**：

```
-O0 -g
```

## ABI 相關

```
-mabi=lp64d       RV64 hard-float (Linux 標配)
-mabi=lp64         RV64 soft-float
-mabi=ilp32d      RV32 hard-float
```

要跟 libc 一致、否則 link error。

## 別忘的 debug flag

```
-g                 DWARF debug info
-g1 / -g2 / -g3   不同 detail level
-gsplit-dwarf      debug info 拆 .dwo 檔，省 link 時間
-fno-omit-frame-pointer   保留 frame pointer (profile friendly)
```

即使 release 也建議 `-g` —— 不影響效能（runtime 不 load debug），給 profile / debug 用。

## 不要無腦加的 flag

### `-funroll-loops`

強制 unroll 所有 loop、造成 code bloat。`-O3` 已有選擇性 unroll。手動加通常 regression。

### `-ffast-math`

放棄 IEEE 754 嚴謹。科學計算不能用。crypto 不能用。

### `-fomit-frame-pointer`

現在 default (`-O1+`)，不用再寫。但 profile 時加 `-fno-omit-frame-pointer`。

### `-march=native`

只給 host binary。cross compile 不適用。production 多 target binary 更麻煩。

## 實驗 scripting

自動化 flag scan：

```bash
for flag in "" "-flto" "-march=rv64gc_zba_zbb_zbs" "-flto -O3"; do
    gcc $flag foo.c -o foo_$flag
    time_avg=$(hyperfine --warmup 3 --runs 10 ./foo_$flag | grep mean)
    echo "$flag: $time_avg"
done
```

寫這類 script 是 compiler 工程師 daily。

## 動手練習

1. 對同 C file build `-O0` ~ `-O3`，比較 binary size + runtime。
2. 加 `-flto` 對比前後，看 `.text` section 改變。
3. 用 `-Rpass=loop-vectorize` 找出哪些 loop 被 vectorize。
4. 試 `-march=rv64gc_zbb` 對 Coremark 影響。
5. 寫 script 自動化 flag sweep、產 table。

## 常見誤會

1. **「-O3 比 -O2 快」**：大部分情況 ~1%。某些情況更慢。
2. **「-march=native 永遠好」**：只給 host。跨 machine 問題。
3. **「-flto 一定改善」**：某些 code 無效或增加 size。
4. **「-ffast-math 省很多」**：只某些 FP 密集 benchmark、且要接受 precision 損失。
5. **「GCC / Clang 哪個強」**：workload-dependent。沒 winner。

## 自我檢核

- [ ] 我能解釋 -O0 到 -O3 的差異
- [ ] 我知道 -march / -mcpu / -mtune 的分工
- [ ] 我能用 -flto 並解釋其 trade-off
- [ ] 我知道 `-Rpass` 能 reveal vectorization miss
- [ ] 我能自己 scientific 地 benchmark flag 選擇

下一章：PGO / BOLT / Propeller — profile-guided 家族。

→ [Ch 11 PGO / BOLT / Propeller](./11-pgo-bolt-propeller.md)
