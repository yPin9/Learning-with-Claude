# Ch 0 — 環境搭建：perf / llvm-mca / valgrind / 其他

> **目標**：裝齊效能分析所需的工具、理解每個工具在「測量 → 分析 → 優化」迴路的分工、設定好測量環境（關掉會干擾測量的東西）、用一個簡單範例驗證整套環境。讀完你有一個能做嚴謹效能測量的環境，以及對接下來工具的分工地圖。

> **環境**：Linux（Ubuntu 22.04+ / Debian 12+）。x86-64 或 RISC-V（有硬體最好，沒有用 QEMU）。需要 root 設定 perf 權限和測量環境。

## 為什麼環境設定對「效能測量」特別重要？

一般課程的環境設定就是「裝工具」。但效能測量不同——**測量環境本身會嚴重影響數字**。CPU 頻率動態調整（turbo/降頻）、背景程序搶 CPU、ASLR 造成的記憶體佈局差異、cache 的冷熱狀態——這些都會讓「同一段程式碼」每次測出不同的數字，差異可能 10-30%。如果不控制這些，你的「效能比較」就是在比雜訊。

這章不只裝工具，還教你**設定一個能做嚴謹測量的環境**——這是 perf_bench 和一般工具教學的根本差別。一個沒控制好的測量環境，會讓你得出錯誤結論（「這個優化讓程式快 5%」其實是雜訊）。先把環境搞對，後面的測量才有意義。

## 先建立直覺:測量迴路與工具分工

```
效能工作的迴路（每個階段的工具）：

  1. 測量（measure）：跑 benchmark，拿到數字
     工具：benchmark（Coremark/SPEC）、time、自寫計時
        │
  2. 分析（analyze）：數字為什麼是這樣？瓶頸在哪？
     動態：perf（硬體事件、profiling）、flamegraph
     靜態：llvm-mca（不跑也能分析 throughput/瓶頸）
        │
  3. 假設（hypothesize）：怎麼改能更快？
     compiler flag、PGO、vectorization、演算法
        │
  4. 驗證（validate）：改了真的有效嗎？（回到測量）
     統計嚴謹：多次測量、信賴區間、排除雜訊
        │
  → 效能工作是「測量 → 分析 → 改 → 再測量」的迴路
    工具分動態（跑時測，perf）和靜態（不跑也能分析，llvm-mca）
    本課教你每個工具，以及怎麼嚴謹地走這個迴路
```

關鍵心智：效能工作是「測量 → 分析 → 假設 → 驗證」的迴路。工具分**動態**（跑時測量，perf/flamegraph）和**靜態**（不跑也能分析，llvm-mca）。本課教你每個工具，但更重要的是「**怎麼嚴謹地走這個迴路**」——測量要可重現、分析要找對瓶頸、驗證要排除雜訊。

> 如果你修過 observability_tools 課，perf 和 flamegraph 有重疊（那課的 Ch 12）。但 perf_bench 從「效能測量和 compiler 優化」角度，更深入硬體事件、llvm-mca、PGO/LTO 等 compiler-centric 的內容。

## 安裝工具

```bash
sudo apt update

# === 測量與分析的核心工具 ===
sudo apt install -y \
    linux-tools-common linux-tools-generic \   # perf（核心工具）
    linux-tools-$(uname -r) \                   # 對應 kernel 版本的 perf
    valgrind \                                  # cachegrind/callgrind（精確分析）
    time \                                      # GNU time（比 shell 內建詳細）
    hyperfine                                   # 嚴謹的 benchmark 計時器（推薦！）

# === LLVM 工具（llvm-mca、編譯器）===
sudo apt install -y \
    llvm clang \                                # clang + llvm-mca
    gcc g++                                     # gcc（對比編譯器）

# === flamegraph 工具 ===
git clone https://github.com/brendangregg/FlameGraph ~/FlameGraph

# === benchmark codebases ===
mkdir -p ~/perflab && cd ~/perflab
git clone https://github.com/eembc/coremark      # Coremark（Ch 3）
# embench、SPEC 等後續章節用

# 驗證
perf --version
llvm-mca --version
hyperfine --version
clang --version; gcc --version
```

```bash
# 確認 perf 能用（權限）
perf stat ls > /dev/null
# 如果報權限錯誤，調 perf_event_paranoid（Ch 0 of observability_tools 提過）
cat /proc/sys/kernel/perf_event_paranoid
# sudo sysctl kernel.perf_event_paranoid=-1   # 放寬（測量環境）
```

## 設定嚴謹的測量環境

這是 perf_bench 的關鍵——控制會干擾測量的因素：

```bash
# === 1. 固定 CPU 頻率（最重要！）===
# CPU 預設動態調頻（turbo 加速、省電降頻）→ 同段 code 每次速度不同
# 看當前 governor
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
# powersave / ondemand / performance

# 設成 performance（固定高頻，減少變異）
sudo cpupower frequency-set -g performance 2>/dev/null || \
    echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor

# 更嚴格：關掉 turbo boost（turbo 讓頻率不穩定）
echo 1 | sudo tee /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null

# === 2. 綁定到特定 CPU（避免 process 在核心間遷移）===
taskset -c 2 ./benchmark        # 固定在 CPU 2 跑（測量時用）

# === 3. 關掉 ASLR（讓記憶體佈局每次一樣，減少變異）===
# 測量時暫時關（測完恢復——ASLR 是安全機制）
setarch $(uname -m) -R ./benchmark   # 單次關 ASLR 跑
# 或全域：echo 0 | sudo tee /proc/sys/kernel/randomize_va_space

# === 4. 減少背景干擾 ===
# 測量時關掉不必要的服務、別開瀏覽器、別跑其他重活
# 用 nice/隔離的核心更嚴格（isolcpus）

# === 測完恢復設定 ===
# sudo cpupower frequency-set -g ondemand
# echo 2 | sudo tee /proc/sys/kernel/randomize_va_space   # 恢復 ASLR
```

```
為什麼這些設定影響測量（變異的來源）：

  CPU 動態調頻：turbo 時快、降頻時慢 → 同 code 差 20-30%
  核心遷移：process 在核心間跳 → cache 失效、NUMA 影響
  ASLR：記憶體佈局每次不同 → cache/分支預測行為變 → 差幾 %
  背景干擾：其他 process 搶 CPU/cache → 測量被污染
        │
  → 不控制這些，你測的是「雜訊」不是「程式效能」
    控制後，同 code 多次測量的變異能從 30% 降到 1-2%
    這樣「優化 5%」才有意義（否則淹沒在雜訊裡）
```

> **固定 CPU 頻率、綁核心、關 ASLR、減少背景干擾——這些是「嚴謹測量」的前提，不做的話你在比雜訊**。效能測量最大的陷阱是**變異**——同一段程式碼每次測出不同數字。主要來源：(1) **CPU 動態調頻**（turbo 加速/省電降頻，最大的變異源，可達 20-30%）→ 設 `performance` governor + 關 turbo 固定頻率；(2) **核心遷移**（process 在核心間跳，cache 失效）→ `taskset` 綁定核心；(3) **ASLR**（記憶體佈局每次不同，影響 cache/分支預測）→ 測量時暫時關（`setarch -R`）；(4) **背景干擾**（其他 process 搶資源）→ 測量時減少其他活動。控制這些後，同 code 多次測量的變異能從 30% 降到 1-2%——**這樣「優化 5%」才有統計意義**（否則 5% 淹沒在 30% 的雜訊裡，你根本分不出是真優化還是運氣）。這是 perf_bench 的核心紀律——**測量環境的嚴謹性決定結論的可信度**。一個常見的錯誤是「測一次就下結論」——沒控制環境、沒多次測量，得出的「快了 X%」可能完全是雜訊。Ch 4（統計基本功）會深入「怎麼從多次測量得出可信的結論」。記得**測完恢復設定**（performance governor 耗電、關 ASLR 降低安全性——這些只在測量時用）。

## hyperfine:嚴謹的計時器

```bash
# hyperfine 是比 time 嚴謹的 benchmark 計時器（自動多次跑、算統計）
cd ~/perflab

# 寫一個簡單的計算程式
cat > compute.c <<'EOF'
#include <stdio.h>
int main() {
    volatile long sum = 0;
    for (long i = 0; i < 100000000L; i++) sum += i % 7;
    printf("%ld\n", sum);
    return 0;
}
EOF
gcc -O2 compute.c -o compute

# 用 hyperfine 測（自動跑多次、算平均/標準差/min/max）
hyperfine './compute'
# Benchmark 1: ./compute
#   Time (mean ± σ):     85.2 ms ±   1.3 ms    ← 平均 ± 標準差（變異小=可信）
#   Range (min … max):   83.1 ms …  88.4 ms
# → hyperfine 自動多次測量、給統計（不是 time 的單次數字）

# 比較兩個版本（hyperfine 的殺手功能：A/B 比較 + 顯示差異是否顯著）
gcc -O3 compute.c -o compute_o3
hyperfine './compute' './compute_o3'
# Summary: './compute_o3' ran 1.05 ± 0.02 times faster than './compute'
# → 直接告訴你「O3 比 O2 快 5%，誤差 ±2%」（差異顯著嗎？）

# 對比 time（單次，不嚴謹）
time ./compute    # 只跑一次，數字不可靠（沒統計）
```

> **hyperfine 自動多次測量 + 算統計 + A/B 比較——比 `time`（單次）嚴謹得多，是 benchmark 計時的首選**。`time` 給單次的數字（不可靠——一次測量可能是雜訊）。**hyperfine** 是嚴謹的 benchmark 計時器——它**自動跑多次**、算**統計**（平均 ± 標準差、min/max），讓你看到「變異有多大」（標準差小 = 可信、大 = 環境沒控制好或程式本身變異大）。它的殺手功能是 **A/B 比較**——`hyperfine './a' './b'` 直接告訴你「b 比 a 快 X 倍 ± 誤差」，並判斷差異是否顯著（如果兩者的誤差範圍重疊，差異可能不顯著=可能是雜訊）。這對「驗證優化有沒有效」極有用——`hyperfine './old' './new'` 直接看「新版真的快嗎、快多少、可信嗎」。對比 `time`（單次、無統計、容易誤導），hyperfine 是嚴謹效能比較的標準工具。本課的很多測量會用 hyperfine（嚴謹的牆鐘時間比較）+ perf（深入的硬體事件分析）。記住：**效能比較不能只測一次**——要多次測量看統計（hyperfine 自動做），否則你可能把雜訊當成優化。這是 perf_bench 反覆強調的紀律——統計嚴謹性。

## 驗證整套環境

```bash
cd ~/perflab

# 用一個範例走完整迴路，驗證工具都能用
cat > demo.c <<'EOF'
#include <stdio.h>
long compute(long n) {
    long sum = 0;
    for (long i = 0; i < n; i++) sum += i * i % 13;
    return sum;
}
int main() { printf("%ld\n", compute(50000000L)); return 0; }
EOF
gcc -g -O2 demo.c -o demo

# 1. 測量（hyperfine）
hyperfine './demo'                          # 牆鐘時間 + 統計

# 2. 硬體事件（perf stat）
perf stat ./demo 2>&1 | grep -E 'instructions|cycles|IPC'
# instructions, cycles, IPC（每週期指令數）—— Ch 6 深入

# 3. profiling（perf record，找熱點）
perf record -g ./demo > /dev/null 2>&1
perf report --stdio 2>/dev/null | head -5   # 熱點函式（compute）

# 4. 靜態分析（llvm-mca）—— 對一段組合語言
clang -O2 -S demo.c -o demo.s               # 產生組合語言
# llvm-mca < demo.s    # 靜態分析 throughput（Ch 8 深入）

# → 如果這些都能跑，環境 OK！
echo "Environment verified!"
```

> 這個驗證走完了效能工作的完整迴路——**測量**（hyperfine 看牆鐘時間）、**硬體事件**（perf stat 看 IPC/cycles）、**profiling**（perf record 找熱點）、**靜態分析**（llvm-mca）。如果這些都能跑，你的環境就準備好了。注意這裡只是「能跑」的驗證，每個工具的深入用法在後面章節——Ch 6（perf stat 的硬體事件）、Ch 7（perf record profiling）、Ch 8（llvm-mca）、Ch 9（flamegraph）。建議現在跑一遍，確認工具都裝對、權限都設好——後面每一章都依賴這些。特別確認 perf 能用（權限）、hyperfine 的變異夠小（環境控制好了）——這兩個是後面測量的基礎。

## 動手練習

1. 裝齊工具：跑安裝命令，驗證 perf/llvm-mca/hyperfine/clang/gcc 都能用

2. 控制環境：設 performance governor、看設定前後 hyperfine 的變異（標準差）差別

3. hyperfine 比較：用 hyperfine 比較 `-O2` vs `-O3` 編譯的同個程式，看差異和誤差

4. 走迴路：對 demo.c 跑 hyperfine（測量）+ perf stat（事件）+ perf record（熱點），驗證環境

5. 體會變異：在沒控制環境（不設 governor、開著瀏覽器）vs 控制好的環境，各測同個程式，比較變異

## 本章重點整理

- 效能工作是「測量 → 分析 → 假設 → 驗證」的迴路；工具分動態（perf/flamegraph）和靜態（llvm-mca）
- 測量環境會嚴重影響數字——控制 CPU 頻率（governor/turbo）、綁核心（taskset）、關 ASLR、減少干擾
- 不控制環境的變異可達 30%，控制後降到 1-2%——這樣小優化（5%）才有統計意義
- hyperfine（自動多次測量+統計+A/B 比較）比 time（單次）嚴謹，是 benchmark 計時首選
- 統計嚴謹性決定結論可信度——效能比較不能只測一次（雜訊可能被當成優化）

## 自我檢核

- [ ] 工具裝齊，perf/llvm-mca/hyperfine 都能用
- [ ] 知道為什麼測量環境影響數字，會控制 CPU 頻率/核心/ASLR
- [ ] 會用 hyperfine 做嚴謹的測量和 A/B 比較
- [ ] 理解變異和為什麼不能只測一次
- [ ] 能走完測量→分析的基本迴路

## 延伸閱讀

### 書籍

- **《Systems Performance》— Ch 12-13 (Benchmarking, perf)** — Brendan Gregg
  - **讀哪幾章**：Ch 12（benchmarking 方法論，含變異控制）、Ch 13（perf）
  - **這本書的定位**：效能分析的權威；測量嚴謹性的完整框架
  - **前提**：本章

- **《Performance Analysis and Tuning on Modern CPUs》— Denis Bakhvalov（免費 PDF）**
  - **讀哪幾章**：Part 1（測量方法論、環境控制）
  - **這本書的定位**：現代 CPU 效能分析的實用指南，免費且優質
  - **連結**：[easyperf.net](https://easyperf.net/)

### 工具

- **[hyperfine](https://github.com/sharkdp/hyperfine)** — 命令列 benchmark 工具
  - **讀哪裡**：README 的進階用法（warmup、parameter scan）
  - **為什麼值得讀**：嚴謹計時的標準工具，本課大量使用

- **[perf wiki](https://perf.wiki.kernel.org/)** — Linux perf
  - **為什麼值得讀**：perf 的官方文件，Ch 6-7 會深入

### 文章

- **[Benchmarking 的陷阱](https://www.brendangregg.com/blog/2014-06-09/java-cpu-sampling-using-hprof.html) / 各種 benchmarking pitfalls 文章**
  - **為什麼值得讀**：理解測量的常見錯誤（本章「控制環境」的延伸）

下一章進入 benchmark 哲學——micro vs macro benchmark，選哪個、怎麼避免「測錯東西」的常見錯誤。這是做任何效能測量前要先想清楚的。

→ [Ch 1 Micro vs macro benchmark](./01-micro-vs-macro.md)
