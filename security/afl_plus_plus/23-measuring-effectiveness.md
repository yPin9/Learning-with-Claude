# Ch 23 — Measuring Effectiveness：Coverage Metric 與 Fuzzing 評估方法

> **目標**：能設計公平的 fuzzer effectiveness 評估實驗；理解 coverage metric 的局限性；能解讀 AFL++ 的 coverage 統計。

> **環境**：AFL++ 4.09c, Ubuntu 22.04 LTS, x86_64

---

## 為什麼需要這個？

你調整了 AFL++ 的設定——換了 mutator、加了 CmpLog、換成 LTO build——coverage 數字上升了 15%。
這代表你找到更多 bug 了嗎？

不一定。

Coverage 是 **proxy metric**（代理指標），不是 ground truth（真實目標）。
真實目標是「找到多少 bug」。但 bug 不能在實驗前先算好，所以我們用 coverage 作為替代。
問題是，這個替代有時候非常不忠實：

- Coverage 增加但沒有找到新 bug（走了很多 error handling path，但那裡沒有記憶體 bug）。
- Coverage 不高但找到嚴重 bug（某個低頻觸發的路徑有 use-after-free）。
- 兩個 fuzzer 的 coverage 一樣，但 bug 數量差 3 倍（coverage metric 的 granularity 不夠）。

理解 coverage 的局限性，才能設計有意義的比較實驗，而不是被數字欺騙。

---

## 先建立直覺

把 code coverage 想成地圖上標記「你去過哪裡」。

你去過全台灣每個縣市（高 coverage），不代表你找到了所有藏寶點（bug）。
有些藏寶點在你去過的地方，但你沒仔細找（覆蓋但未發現）。
有些藏寶點在你沒去過的地方（未覆蓋 = 確定沒找）。

Coverage 的作用：**coverage 高是必要條件，但不是充分條件**。
沒有覆蓋到的 code path 裡的 bug，你永遠找不到。
覆蓋到了，不代表一定找到 bug。

---

## 橫向連結

- **Ch 5（Edge Coverage Bitmap）**：本章的 `total_edges` 和 `map_density` 都是 bitmap 的統計。
- **Ch 3（AFL++ Architecture）**：`fuzzer_stats` 的各欄位定義。
- **Ch 24（Fuzzer Comparison）**：用本章的方法論比較不同 fuzzer。

---

## AFL++ 的 Coverage 輸出解讀

### fuzzer_stats 欄位

AFL++ 運行時持續更新 `out/default/fuzzer_stats`：

```
start_time        : 1716000000
last_update       : 1716003600
last_new_find     : 1716003580
fuzzer_pid        : 12345
cycles_done       : 42
cycles_wo_finds   : 3
time_wo_finds     : 1234
execs_done        : 5678901
execs_per_sec     : 2345.67
execs_since_crash : 100000
paths_total       : 1234
paths_found       : 1234
paths_favored     : 89
paths_imported    : 0
max_depth         : 15
cur_item          : 456
pending_favs      : 12
pending_total     : 789
stability         : 100.00%
bitmap_cvg        : 4.23%
unique_crashes    : 7
unique_hangs      : 2
total_execs       : 5678901
var_byte_count    : 0
total_edges       : 21345
```

關鍵欄位解讀：

**`total_edges`**：bitmap 裡曾經被 hit 過的 edge 數量。
這是你的 fuzzer 探索到的 edge 總數，不是 target 的 edge 總數。
如果 target 共有 50000 個 edge，你的 `total_edges` = 21345，coverage rate = 42.7%。

**`bitmap_cvg`（map density）**：bitmap 的使用率。
計算方式：`total_edges / MAP_SIZE`，預設 `MAP_SIZE = 65536`。
4.23% 的 map density 表示 bitmap 中有 2772 個 slot 被使用（65536 * 0.0423）。
這個數值遠小於 `total_edges`（21345），因為多個 edge 會 hash 到同一個 slot。

**`map_density > 60%`** 是警告：hash collision 嚴重影響 coverage 準確度。
解法：`AFL_MAP_SIZE=262144 afl-fuzz ...`（擴大 4 倍）或重新用 `AFL_LLVM_MAP_SIZE=262144` 編譯。

**`execs_per_sec`**：每秒執行次數，是 fuzzer 健康度的最重要指標。
目標值（persistent mode）：10000–100000 execs/sec。
正常 file-based target：1000–5000 execs/sec。
Network service / QEMU：100–500 execs/sec。

**`stability`**：相同 input 執行兩次，bitmap 結果一致的比例。
< 90% 說明 target 有非決定性行為（race condition、ASLR 影響、時間相關邏輯）。
非決定性行為讓 AFL++ 的 coverage 追蹤不可靠。

---

## Branch Coverage vs Edge Coverage：計算差異

這兩個詞在不同工具裡定義不同，混用會造成比較結果不可靠。

```c
void foo(int a, int b) {
    if (a > 0) {         // branch 1：true/false = 2 branches
        do_something();  // edge: foo_entry → if_true
    }                    // edge: foo_entry → if_false（else path）
    if (b > 0) {         // branch 2：true/false = 2 branches
        do_other();      // edge: if_true → if2_true
    }                    // edge: if_true → if2_false
}                        // edge: if_false → if2_true
                         // edge: if_false → if2_false
```

對這個函式：
- **Branch coverage**：4 個 branch（2 個 if，各有 true/false）
- **Edge coverage**：6 個 edge（block 之間的轉移邊）
- **Line coverage**：行數（最粗粒度，幾乎沒有診斷價值）

AFL++ 用 **edge coverage**（更精細），lcov 預設報告 **branch + line coverage**。
直接比較 AFL++ 的 `total_edges` 和 lcov 的 branch coverage 是蘋果比橘子。

---

## 用 llvm-cov 算 Source-Level Coverage

AFL++ 的 bitmap 給你 edge 數量，但看不出是哪些函式、哪些行被覆蓋。
用 LLVM coverage instrumentation 可以得到精確的 source-level coverage：

```bash
# Step 1：用 coverage instrumentation 重新編譯
# 不用 afl-clang-lto，用原生 clang（兩個 build 是分開的）
CC=clang CFLAGS="-fprofile-instr-generate -fcoverage-mapping -g" \
    ./configure --disable-shared
make -j4 -o target_cov  # 假設輸出是 target_cov

# Step 2：對 AFL++ 產生的 corpus，每個 input 各跑一次
mkdir -p profiles
for f in out/*/queue/id:*; do
    base=$(basename "$f")
    LLVM_PROFILE_FILE="profiles/${base}.profraw" \
        ./target_cov "$f" 2>/dev/null || true
done

# Step 3：合併所有 profraw
llvm-profdata merge profiles/*.profraw -o merged.profdata

# Step 4：查看 coverage 報告
llvm-cov show ./target_cov \
    -instr-profile=merged.profdata \
    -format=html \
    -output-dir=./coverage_report/
# 用瀏覽器打開 coverage_report/index.html

# Step 5：取得摘要數字
llvm-cov report ./target_cov -instr-profile=merged.profdata
```

輸出範例：

```
Filename         Regions    Missed Regions    Cover    Functions    Missed    Cover    Lines    Missed    Cover
------------------------------------------------------------------------------------------------------------
png.c              1234           234         81.0%         89         12       86.5%     4567      567     87.6%
pngrutil.c          567           189         66.7%         34          8       76.5%     2345      678     71.1%
------------------------------------------------------------------------------------------------------------
TOTAL              1801           423         76.5%        123         20       83.7%     6912     1245     82.0%
```

「76.5% region coverage」代表還有 23.5% 的 code path 沒有被任何 input 觸發過——那些路徑裡可能有 bug。

---

## 底層機制：Coverage 資料的流動

```
┌─────────────────────────────────────────────────────────────────┐
│                  Coverage 資料流                                  │
│                                                                 │
│  AFL++ fuzzer                                                   │
│  ├── 維護 SHM bitmap（64KB，edge hit counts）                   │
│  ├── 每次 exec 後讀取 bitmap，更新 fuzzer_stats                  │
│  └── total_edges = bitmap 中非零 slot 的數量                    │
│                                                                 │
│  LLVM coverage（分開的 build）                                   │
│  ├── target 執行時，每個 region/branch 都有計數器                │
│  ├── 程式結束時，寫入 .profraw 檔案                              │
│  ├── llvm-profdata 合併多個 .profraw                            │
│  └── llvm-cov 把計數器對應回 source code 行號                   │
│                                                                 │
│  兩者的 edge 定義不同：                                           │
│  AFL++ edge = hash(prev_basic_block XOR cur_basic_block)        │
│  LLVM region = source code 裡兩個 sequence point 之間的程式碼   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 比較多個 Fuzzer 的常見陷阱

Klees et al.（CCS 2018）分析了 32 篇 fuzzing 論文，發現絕大多數的比較實驗都有嚴重方法論問題。

### 陷阱 1：跑時間太短

「LibFuzzer 跑 1 小時比 AFL++ 找更多 edge」—— 這可能只是因為 LibFuzzer 的 in-process 執行速度快，在 1 小時內跑了更多次。跑夠長的時間，coverage 曲線通常會趨近。

**修正**：至少跑 24 小時，最好 48 小時以上；畫出 coverage 隨時間的變化曲線（time-to-coverage curve），而不是只看結束時的數字。

### 陷阱 2：只用一次 run

Fuzzer 有隨機性（random seed、random mutation choice）。一次 run 的結果可能是異常值。

**修正**：每個 fuzzer 各跑 N 次（Klees et al. 建議至少 5 次），報告中位數（median）和四分位距（IQR），用 Mann-Whitney U test 做統計顯著性測試。

```python
# 計算統計顯著性
from scipy import stats
import numpy as np

# 5 次 run 的 coverage 數字
afl_pp_coverage = [4321, 4456, 4289, 4512, 4398]
libfuzzer_coverage = [4123, 4234, 4089, 4312, 4198]

u_stat, p_value = stats.mannwhitneyu(afl_pp_coverage, libfuzzer_coverage,
                                      alternative='two-sided')
print(f"p-value: {p_value:.4f}")
# p < 0.05 才算統計顯著
```

### 陷阱 3：seed corpus 不同

「用我自己挑的 seed 來比較」—— 如果 AFL++ 的 seed 恰好有更好的起點，結果對 AFL++ 有利，但這不是 AFL++ 本身比較好的證據。

**修正**：兩個 fuzzer 用完全相同的 seed corpus；如果比較 zero-knowledge 情況，都用空 corpus 或同一個最小 seed。

### 陷阱 4：忽略 target 的代表性

「在 libpng 上 fuzzer A 贏」—— 不代表在所有 target 上都贏。

**修正**：在多個 target 上測試，計算 A12 effect size（某個 fuzzer 在隨機取一次 run 時贏的機率），而不是只報告「X 個 target 中，A 贏了 Y 個」。

---

## 進一步用法：Coverage 增長曲線分析

Coverage 增長曲線是評估 fuzzer 進展最直觀的工具：

```python
#!/usr/bin/env python3
# plot_coverage.py
import os
import re
import matplotlib.pyplot as plt

def read_plot_data(out_dir):
    """讀取 AFL++ 的 plot_data 檔案"""
    plot_file = os.path.join(out_dir, "default", "plot_data")
    times, edges = [], []
    with open(plot_file) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().split(",")
            # 格式：unix_time, cycles_done, cur_item, paths_total,
            #        pending_total, pending_fav, map_size, saved_crashes, ...
            try:
                t = int(parts[0])
                edges_val = int(parts[6])  # map_size (total_edges)
                times.append(t)
                edges.append(edges_val)
            except (ValueError, IndexError):
                continue
    return times, edges

# 畫出多個 fuzzer 的 coverage 曲線
fig, ax = plt.subplots(figsize=(10, 6))

for label, out_dir in [("AFL++ default", "out_default"),
                        ("AFL++ + CmpLog", "out_cmplog"),
                        ("AFL++ LTO", "out_lto")]:
    times, edges = read_plot_data(out_dir)
    # 轉成「相對時間（小時）」
    t0 = times[0]
    rel_times = [(t - t0) / 3600 for t in times]
    ax.plot(rel_times, edges, label=label)

ax.set_xlabel("Time (hours)")
ax.set_ylabel("Total edges covered")
ax.legend()
ax.grid(True, alpha=0.3)
plt.savefig("coverage_curve.png", dpi=150)
```

Coverage 曲線的解讀：

```
edges
 ^
 |         ___________
 |        /
 |       /
 |      /
 |_____/
 +──────────────────→ time
    ↑    ↑
    快速成長期  趨平期
```

**快速成長期**（初始幾小時）：fuzzer 正在探索 target 的基本結構，每個新 input 都能觸發很多新 edge。

**趨平期**：fuzzer 已探索大部分「容易到達」的 path，剩下的都需要特定的 magic bytes 組合或複雜的 state 序列。

趨平不代表沒有更多 edge 可以找——可能是 hard-to-reach path，需要 CmpLog、dictionary、或不同的 mutator strategy 才能突破。

---

## 對比與取捨

### 不同 Coverage 指標的比較

| 指標 | 粒度 | AFL++ 支援 | lcov 支援 | 和 bug 的相關性 | 計算成本 |
|------|------|-----------|-----------|--------------|---------|
| Line coverage | 粗（整行） | 否 | 是 | 低（同一行的不同路徑看不出來） | 低 |
| Branch coverage | 中（每個 if 的 T/F） | 部分 | 是 | 中 | 低 |
| Edge coverage | 細（block 間轉移） | 是（bitmap） | 部分 | 中高 | 中 |
| Path coverage | 極細（完整執行路徑） | 否 | 否 | 高 | 指數級（不可實用） |
| Function coverage | 最粗（有無呼叫） | 否 | 是 | 極低 | 很低 |

AFL++ 的 bitmap 追蹤的是 edge coverage，但用 hash，所以有 collision（高 map density 時準確度下降）。

---

## 踩雷集錦

1. **`map_density > 60%` 沒有增大 `AFL_MAP_SIZE`**：collision 讓 AFL++ 以為某些 edge 被 hit 過（其實沒有），或反過來。coverage 數字失真，fuzzer 做出錯誤的 input 保留決策。`MAP_SIZE=65536` 支援到約 32768 個不碰撞的 edge（50% 負載因子）；如果 `total_edges > 30000`，就應該增大 MAP_SIZE。

2. **Coverage 增長曲線「趨平」被誤解為「已完成」**：趨平代表「用現有策略很難再找到新 edge」，不代表沒有未覆蓋的 code。可能的原因：（a）hard-to-reach path 需要 CmpLog/grammar；（b）target 有 initialization barrier（需要特定協定握手才能進入 parse 邏輯）；（c）seed corpus 太差，起點就限制了探索方向。

3. **用固定 seed 比較兩個 fuzzer，seed 恰好對其中一個有利**：一個設計好的 seed（比如包含 PNG header 的 seed 對 PNG parser fuzzer）讓 fuzzer A 在前幾小時就跑到高 coverage，而 fuzzer B 從隨機 bytes 開始需要更長時間。結論說「A 在 1 小時內比 B 好」可能只是 seed bias。

4. **報告 mean 而非 median**：Fuzzer 的 coverage 分佈通常是有 outlier 的（某次 run 碰到好的 random seed 爆發）。Mean 被 outlier 拉高，median 更能代表典型 run 的表現。

5. **不用相同 CPU 時間比較，用掛鐘時間（wall clock time）**：libFuzzer in-process 比 AFL++ fork-based 快 3-5x，在相同 wall clock time 下做的 exec 更多。公平的比較應該用相同的 exec 次數，或相同的 CPU 時間（不是掛鐘時間）。

---

## 進階：再往深一層

### FuzzBench：標準化的 Fuzzer Benchmarking

Google 的 FuzzBench（https://google.github.io/fuzzbench/）提供了一套標準化的 fuzzer 比較平台：
- 固定的 target 集合（20+ 個真實的開源 library）
- 固定的 eval 方法（多次 run、中位數、A12 effect size）
- 公開的實驗結果（每個月更新）

在聲稱「fuzzer X 比 Y 好」之前，先看 FuzzBench 的最新結果。

### Mutation Analysis for Fuzzing（Böhme et al.）

除了 coverage，**mutation score** 是另一個評估指標：
- 在 target 的 source code 裡人工注入 bug（mutation，例如把 `>` 改成 `>=`）
- 看 fuzzer 能殺死（找到）多少個這些人工 bug
- Mutation score = 被找到的 bug 數 / 總注入 bug 數

Mutation score 比 coverage 更直接衡量 fuzzer 找 bug 的能力，但計算成本高（需要為每個 mutation 重新編譯）。

---

## 動手練習

### 練習 1：讀取並視覺化 AFL++ 的 coverage 統計

```bash
# 先確保有一個正在跑或已完成的 AFL++ session
# 讀取 fuzzer_stats
cat out/default/fuzzer_stats | grep -E "total_edges|bitmap_cvg|execs_per_sec|unique_crashes"

# 計算 map density（collision 風險）
python3 -c "
total_edges = int(input('total_edges: '))
map_size = 65536  # 預設值
density = total_edges / map_size * 100
print(f'Map density: {density:.1f}%')
if density > 60:
    print('WARNING: High collision risk! Consider AFL_MAP_SIZE=262144')
elif density > 40:
    print('NOTICE: Moderate collision. Monitor.')
else:
    print('OK: Low collision risk.')
"
```

### 練習 2：用 llvm-cov 算 source-level coverage

```bash
# 以 libpng 為例（需要先完成 Ch Final Project 的環境設置）
# 假設已有 AFL++ corpus 在 out/default/queue/

cd /tmp/libpng-1.6.40

# coverage build
CC=clang CFLAGS="-fprofile-instr-generate -fcoverage-mapping -g" \
    ./configure --disable-shared --prefix=/tmp/libpng_cov
make -j4

# 寫一個簡單的 harness wrapper（從檔案讀）
cat > /tmp/cov_runner.c << 'EOF'
#include <stdio.h>
#include <stdlib.h>
#include "png.h"

int main(int argc, char **argv) {
    if (argc < 2) return 1;
    FILE *fp = fopen(argv[1], "rb");
    if (!fp) return 1;
    png_structp png = png_create_read_struct(PNG_LIBPNG_VER_STRING,
                                              NULL, NULL, NULL);
    png_infop info = png_create_info_struct(png);
    if (setjmp(png_jmpbuf(png))) {
        png_destroy_read_struct(&png, &info, NULL);
        fclose(fp);
        return 1;
    }
    png_init_io(png, fp);
    png_read_info(png, info);
    png_destroy_read_struct(&png, &info, NULL);
    fclose(fp);
    return 0;
}
EOF
clang -fprofile-instr-generate -fcoverage-mapping -g \
    /tmp/cov_runner.c -I. -L.libs -lpng16 -lz -o /tmp/cov_runner

# 對 corpus 裡的每個 input 執行
mkdir -p /tmp/profiles
for f in /path/to/out/default/queue/id:*; do
    base=$(basename "$f")
    LLVM_PROFILE_FILE="/tmp/profiles/${base}.profraw" \
        /tmp/cov_runner "$f" 2>/dev/null || true
done

# 合併並顯示摘要
llvm-profdata merge /tmp/profiles/*.profraw -o /tmp/merged.profdata
llvm-cov report /tmp/cov_runner -instr-profile=/tmp/merged.profdata
```

---

## 本章重點整理

- **Coverage 是 proxy metric**，不是 bug 數量的直接衡量；coverage 高是必要條件，但 coverage 增長不等比例轉化成 bug 發現率，不要過度信任單一數字。
- **`map_density > 60%` 是 collision 警告**；要擴大 `AFL_MAP_SIZE`，否則 AFL++ 的 coverage 追蹤失真，影響 input 保留決策。
- **公平的 fuzzer 比較需要**：相同 seed corpus、多次 run（≥5）、足夠長的時間（≥24h）、報告中位數+統計顯著性；單次 run 的結果不可信。

---

## 自我檢核

1. `fuzzer_stats` 裡 `bitmap_cvg = 72.3%`。這代表什麼問題？你會怎麼解決？
2. 你的 coverage 曲線在第 3 小時後完全趨平。列出三個可能的原因，以及每個原因對應的解法。
3. 想比較 AFL++ 和 libFuzzer 在 libpng 上的表現，設計一個最小的公平實驗方案（說明 seed、時間、run 次數、統計方法）。
4. `total_edges = 5000`，但 `llvm-cov report` 顯示 function coverage = 42%。這兩個數字不矛盾嗎？分別代表什麼？
5. 一個 fuzzer 跑了 1 小時，coverage 增長了 3000 edges，但沒有找到任何 crash。另一個 fuzzer 跑了 1 小時，只增長了 1000 edges，但找到了 2 個 heap overflow。哪個 fuzzer「更有效」？說明你的判斷標準。

---

## 延伸閱讀

- **"Evaluating Fuzz Testing"（Klees et al., CCS 2018）**
  核心貢獻：分析 32 篇 fuzzing 論文的方法論缺陷，建立 fuzzer 比較的統計標準（多次 run、Mann-Whitney U test、A12 effect size）。這篇是 fuzzing benchmark 的必讀，幾乎所有嚴肅的 fuzzing 論文都引用它。
  讀哪裡：Section 4（統計方法的完整說明）和 Section 5（常見錯誤的逐條分析）。先讀 Section 5 的 takeaway，再回頭讀 Section 4 的細節。
  和本章關聯：本章「比較多個 fuzzer 的常見陷阱」直接來自這篇論文的 findings。

- **"FuzzBench: An Open Fuzzer Benchmarking Platform and Service"（Metzman et al., FSE 2021）**
  核心貢獻：Google 開源的標準化 fuzzer benchmark 平台，提供可重現的比較環境，是業界最廣泛使用的 fuzzer 評估基準。
  讀哪裡：Section 3（target 選擇和實驗設計）；直接訪問 https://google.github.io/fuzzbench/報告頁面看最新結果。
  和本章關聯：用 FuzzBench 的方法論設計自己的比較實驗；也是確認「我的 AFL++ 設定有沒有效」的外部基準。

- **LLVM Code Coverage（https://llvm.org/docs/SourceBasedCodeCoverage.html）**
  核心貢獻：LLVM 的 source-based coverage instrumentation 文件，說明 `-fprofile-instr-generate` 和 `llvm-cov` 的完整用法。
  讀哪裡：「Compiling with coverage」和「Creating coverage reports」兩節；命令列範例直接可以用。
  和本章關聯：本章「用 llvm-cov 算 source-level coverage」的技術基礎；AFL++ 的 bitmap coverage 和這裡的 source-level coverage 是互補的。

---

→ 下一章：[Ch 24 — Fuzzer Comparison](24-fuzzer-comparison.md)
