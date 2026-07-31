# Ch 9 — Flame graph 與 on-CPU profiling

> **目標**：用 flame graph 視覺化 perf profile——把 perf record 的取樣資料變成一張圖，一眼看出熱點和呼叫關係。理解怎麼產生、怎麼讀（寬度=佔 CPU、Y 軸=呼叫堆疊）、differential flame graph（比較兩個版本的差異）、以及它為什麼是 profile 分析的標準視覺化。比 perf report 的文字列表直觀，複雜程式特別有用。

> **環境**：Linux，perf + FlameGraph 工具（Ch 0 已 clone）。

## 為什麼需要 flame graph？

Ch 7 的 `perf report` 給文字列表（哪個函式吃多少 CPU）——對簡單程式夠用。但對**複雜程式**（很多函式、深的呼叫堆疊、複雜的呼叫關係），文字列表難看出全貌——你看到一堆函式各佔幾 %，但難理解「整體的時間分布」和「呼叫關係」。

**flame graph**（火焰圖，Brendan Gregg 發明）把 profile 視覺化——一張圖一眼看出「時間花在哪、誰呼叫的、整體分布」。它是 profile 分析的**標準視覺化**——SiFive、Google、Netflix 都用。更強的是 **differential flame graph**（比較兩個版本，看「優化讓哪裡變快/變慢」）——這對 compiler 工作（驗證優化效果、看 regression）極有用。這章把 flame graph 用熟，這是 profile 分析的標準工具。

> flame graph 在 observability_tools 課的 Ch 12 介紹過。這裡從「效能優化和 compiler 工作」角度，更注重 differential flame graph（比較版本）。

## 先建立直覺:火焰圖怎麼讀

```
flame graph（火焰圖）的讀法：

  ┌────────────────────────────────────────┐
  │           compute (70%)                 │ ← 寬度 = 佔 CPU 比例
  │  ┌──────────────────────────────────┐  │
  │  │  process (90%)                   │  │ ← Y 軸 = 呼叫堆疊深度
  │  │  ┌────────┐  ┌─────────────────┐ │  │
  │  │  │parse20%│  │ main            │ │  │
  │  │  └────────┘  └─────────────────┘ │  │
  │  └──────────────────────────────────┘  │
  └────────────────────────────────────────┘
        │
  讀法：
    X 軸（寬度）：函式佔 CPU 的比例（越寬越吃 CPU）★ 找寬的
    Y 軸（高度）：呼叫堆疊（下面呼叫上面，塔頂是實際執行的）
    顏色：通常隨機（不代表意義，只是區分）
        │
  → 找「寬的塔」= 熱點（佔 CPU 多的）
    塔頂的寬函式 = 實際在執行的熱點
    一圖看出「整體時間分布 + 呼叫關係」
        │
  互動（SVG）：可點擊放大、搜尋函式
```

關鍵心智：flame graph 把 profile 視覺化——**X 軸寬度 = 函式佔 CPU 比例**（找寬的塔=熱點）、**Y 軸高度 = 呼叫堆疊深度**（下面呼叫上面，塔頂是實際執行的）、顏色隨機（只是區分）。一圖看出「整體時間分布 + 呼叫關係」，比文字列表直觀。

## 產生 flame graph

```bash
cd ~/perflab
gcc -O2 -g -fno-omit-frame-pointer surprise.c -o surprise 2>/dev/null

# === 產生 flame graph ===
# 1. perf record 取樣（要 call graph）
perf record -F 999 -g ./surprise > /dev/null 2>&1

# 2. perf script 輸出 + FlameGraph 工具轉成 SVG
perf script | ~/FlameGraph/stackcollapse-perf.pl | ~/FlameGraph/flamegraph.pl > flame.svg

# 3. 用瀏覽器開 flame.svg（互動式：點擊放大、搜尋）
# firefox flame.svg

# === 一行產生（方便）===
perf record -F 999 -g -- ./surprise > /dev/null 2>&1
perf script | ~/FlameGraph/stackcollapse-perf.pl | ~/FlameGraph/flamegraph.pl > flame.svg
echo "Open flame.svg in browser"

# 看 flame graph：
# - 找最寬的塔 = 熱點（如 sprintf 相關的塔很寬 = 它吃最多 CPU）
# - 塔的高度 = 呼叫堆疊（main → ... → sprintf）
# - 點擊某個塊 = 放大看細節
```

```
flame graph 的優勢（vs perf report 文字）：

  perf report（文字）：
    一列一個函式 + overhead %
    對「簡單程式」夠用
    但複雜程式難看出「整體分布 + 呼叫關係」
        │
  flame graph（視覺）：
    一圖看出整體（哪些寬=熱點、呼叫關係、分布）
    複雜程式（深堆疊、多函式）一目了然
    互動（點擊放大、搜尋）
        │
  → flame graph 適合複雜程式的整體理解
    perf report 適合快速看 top 函式
    兩者互補（flame graph 看全貌、perf report 看精確數字）
```

> **flame graph 適合複雜程式的「整體理解」——一圖看出時間分布和呼叫關係，比 perf report 文字列表直觀**。產生 flame graph 的流程：`perf record -g`（取樣+call graph）→ `perf script`（輸出取樣資料）→ FlameGraph 工具的 `stackcollapse-perf.pl`（折疊堆疊）+ `flamegraph.pl`（產生 SVG）→ 用瀏覽器開（互動式）。讀法是「找寬的塔」（熱點）。flame graph 比 `perf report`（文字列表）的優勢在**複雜程式**——對有很多函式、深呼叫堆疊、複雜呼叫關係的程式，文字列表難看出「整體的時間分布和呼叫關係」（你看到一堆函式各佔幾%，但腦中難拼出全貌），而 flame graph **一圖看盡**（哪些塔寬=熱點、呼叫關係怎樣、時間怎麼分布）。互動的 SVG 還能點擊放大（聚焦某個子樹）、搜尋函式（高亮所有匹配的）。**兩者互補**——flame graph 看全貌和呼叫關係、perf report 看精確的數字。對 compiler 工作，flame graph 幫你理解「整個程式的時間分布」（哪些是熱點、優化哪裡影響大），這比逐函式看數字更有全局觀。記住產生 flame graph 需要正確的編譯選項（`-g -fno-omit-frame-pointer`，Ch 7——讓 call graph 準確，否則塔的堆疊不對）。flame graph 是 profile 分析的標準視覺化，學會讀它（找寬的塔）和產生它（perf + FlameGraph 工具），你看複雜程式的效能就像看地圖。

## differential flame graph:比較版本

```bash
# differential flame graph：比較兩個版本（看優化讓哪裡變快/變慢）
cd ~/perflab

# 假設有 old 和 new 兩個版本
gcc -O2 -g -fno-omit-frame-pointer surprise.c -o old 2>/dev/null
# 優化版（如把 sprintf 移到迴圈外，假設）
gcc -O2 -g -fno-omit-frame-pointer surprise.c -o new 2>/dev/null

# 各自 profile
perf record -F 999 -g -o old.data -- ./old > /dev/null 2>&1
perf record -F 999 -g -o new.data -- ./new > /dev/null 2>&1

# 產生折疊的堆疊
perf script -i old.data | ~/FlameGraph/stackcollapse-perf.pl > old.folded
perf script -i new.data | ~/FlameGraph/stackcollapse-perf.pl > new.folded

# differential flame graph（比較差異）
~/FlameGraph/difffolded.pl old.folded new.folded | ~/FlameGraph/flamegraph.pl > diff.svg
# diff.svg 用顏色顯示差異：
#   紅色 = new 比 old 花更多時間（變慢/regression）
#   藍色 = new 比 old 花更少時間（變快/優化）
# → 一眼看出「優化讓哪些函式變快、哪些變慢（regression）」

# 對 compiler 工作的價值：
#   改了 compiler → 比較 before/after 的 flame graph
#   → 看「優化讓哪裡變快」（驗證有效）+「哪裡意外變慢」（regression）
```

> **differential flame graph 用顏色顯示「優化讓哪裡變快（藍）變慢（紅）」——對 compiler 工作驗證優化和抓 regression 極有用**。**differential flame graph** 是 flame graph 的強大延伸——它**比較兩個版本**（old vs new），用顏色顯示差異：**紅色 = new 比 old 花更多時間**（變慢/regression）、**藍色 = new 比 old 花更少時間**（變快/優化）。產生方法：各自 profile → 折疊堆疊 → `difffolded.pl` 比較 → flamegraph。這對 **compiler 工作極有價值**——你改了 compiler 的某個優化，比較 before/after：(1) **驗證優化有效**（看藍色——優化讓哪些函式變快）；(2) **抓 regression**（看紅色——優化意外讓哪些變慢——這是 compiler 改動的常見風險，一個優化幫了某些 code 但傷了其他）。這個「視覺化的版本比較」比「比較兩個數字」資訊豐富得多——你看到**整體的變化分布**（哪裡變快、哪裡變慢、變化多大），而非只是「整體快了 X%」。對 compiler 開發，differential flame graph 是驗證優化和 debug regression 的利器——當一個 compiler 改動「整體效能沒變或變差」，differential flame graph 揭示「某些地方變快了但某些地方變慢了（抵消）」，幫你理解和修正。這是 perf_bench 對 compiler 工作的直接應用——**用 differential flame graph 驗證 compiler 優化的效果和影響**。配合 Ch 4 的統計（多次測量看顯著性），你能嚴謹地評估 compiler 改動（差異顯著嗎 + 影響在哪）。

## on-CPU vs off-CPU profiling

```
on-CPU vs off-CPU profiling（兩種視角）：

  on-CPU（前面講的）：
    profile「CPU 在執行什麼」（占用 CPU 的時間）
    flame graph 顯示「CPU 時間花在哪個函式」
    適合：CPU-bound 的問題（計算密集）
        │
  off-CPU：
    profile「程式『不在 CPU 上』的時間」（等待）
    等 IO、等鎖、等網路、被排程出去
    → 程式「卡住」但不占 CPU 的時間
    適合：等待密集的問題（IO/鎖/網路 bound）
        │
  → on-CPU flame graph 看「CPU 在算什麼」
    off-CPU flame graph 看「程式在等什麼」
    完整分析常兩者都要（CPU 時間 + 等待時間）
        │
  perf_bench 主要 on-CPU（CPU-bound，Ch 0 說過專注 CPU-bound）
  off-CPU 對 IO/並發問題重要（用 perf sched 或 eBPF）
```

> **on-CPU profiling 看「CPU 在算什麼」、off-CPU 看「程式在等什麼」——perf_bench 主要 on-CPU（CPU-bound）**。profiling 有兩種視角：**on-CPU**（前面講的）——profile「CPU 在執行什麼」（占用 CPU 的時間），flame graph 顯示「CPU 時間花在哪個函式」，適合 **CPU-bound** 問題（計算密集）；**off-CPU**——profile「程式**不在 CPU 上**的時間」（等待——等 IO、等鎖、等網路、被排程出去），這是程式「卡住但不占 CPU」的時間，適合**等待密集**的問題（IO/鎖/網路 bound）。兩者互補——完整分析常要看「CPU 時間（on-CPU）+ 等待時間（off-CPU）」。**perf_bench 主要關注 on-CPU**（Ch 0 說過本課專注 CPU-bound 的效能，compiler 優化主要影響 CPU 計算）——off-CPU（IO/並發的等待）是 observability_tools 課（strace 看卡在哪、ss 看連線）和 bpf 課（off-CPU flame graph 用 eBPF）的領域。但知道有 off-CPU 這個視角很重要——當一個程式「慢但 CPU 不忙」（on-CPU profiling 顯示 CPU 沒在做什麼），問題在「等待」（off-CPU），要用 off-CPU 分析。對 compiler/CPU 效能工作（CPU-bound），on-CPU flame graph 是主力——它顯示「CPU 的計算時間花在哪」，這正是 compiler 優化的目標。理解 on-CPU vs off-CPU，你知道「flame graph 看的是 CPU 時間」，當問題是等待時要換 off-CPU 視角。Part 3（profiling 工具）到此完成——你掌握了 perf record/report（找熱點）、llvm-mca（靜態指令分析）、flame graph（視覺化 + 比較版本）。

## 故意弄壞:用 flame graph 看優化效果

```bash
cd ~/perflab
# 用 flame graph 視覺化「優化前後的差異」
# 優化前：每次迴圈都 sprintf（surprise.c）
gcc -O2 -g -fno-omit-frame-pointer surprise.c -o before 2>/dev/null

# 優化後：sprintf 移到迴圈外
cat > optimized.c <<'EOF'
#include <stdio.h>
long complex_calc(long n) {
    long r = 0;
    for (int i = 0; i < 50; i++) r += (n * i) % 13;
    return r;
}
int main() {
    char buf[100];
    long total = 0;
    for (long i = 0; i < 1000000L; i++) {
        total += complex_calc(i);     // 計算
    }
    sprintf(buf, "Result: %ld\n", total);   // sprintf 只一次（移出迴圈）
    printf("%s", buf);
    return 0;
}
EOF
gcc -O2 -g -fno-omit-frame-pointer optimized.c -o after

# 比較執行時間（hyperfine，Ch 0）
hyperfine './before' './after'
# './after' ran 3.5 ± 0.1 times faster   ← 優化讓它快 3.5 倍！

# 產生 differential flame graph 看「哪裡變快」
perf record -F 999 -g -o b.data -- ./before > /dev/null 2>&1
perf record -F 999 -g -o a.data -- ./after > /dev/null 2>&1
perf script -i b.data | ~/FlameGraph/stackcollapse-perf.pl > b.folded
perf script -i a.data | ~/FlameGraph/stackcollapse-perf.pl > a.folded
~/FlameGraph/difffolded.pl b.folded a.folded | ~/FlameGraph/flamegraph.pl > diff.svg
# diff.svg：sprintf 的塔變藍（消失）→ 優化讓 sprintf 的時間沒了

# → 完整的優化流程（perf_bench 的綜合）：
#   1. profile 找熱點（sprintf，Ch 7）
#   2. 優化（移出迴圈）
#   3. hyperfine 驗證（快 3.5 倍，統計顯著，Ch 4）
#   4. differential flame graph 看「哪裡變快」（sprintf 消失）
```

> 這個例子展示了 perf_bench 前 9 章的綜合應用——**完整的優化流程**：(1) **profile 找熱點**（Ch 7，perf 找出 sprintf 是熱點，不是直覺以為的 complex_calc）；(2) **優化**（把 sprintf 移出迴圈——每次迴圈都格式化但只用最後一次，移到迴圈外只做一次）；(3) **hyperfine 驗證**（Ch 4 的統計嚴謹——快 3.5 倍，看是否顯著）；(4) **differential flame graph 看「哪裡變快」**（sprintf 的塔變藍/消失，確認優化生效在預期的地方）。這個流程整合了測量（hyperfine）、profiling（perf 找熱點）、視覺化（flame graph 看差異）、統計（顯著性）——這正是 perf_bench 的核心工作流。注意這是「演算法/程式層」的優化（移出迴圈），但同樣的流程適用於 **compiler 優化**——改 compiler、profile 找熱點、驗證（hyperfine + differential flame graph）。Part 3 的工具（perf record/report、llvm-mca、flame graph）讓你能定位熱點、分析瓶頸、視覺化和比較——這是 Part 4（compiler-centric 優化）的基礎。接下來 Part 4 進入「compiler 怎麼影響效能」——flag、PGO、LTO、vectorization——以及「從 hot loop 倒推該加什麼優化」，這是 perf_bench 對 SiFive compiler 工作最直接的對口。

## 動手練習

1. 產生 flame graph：對一個程式產生 flame graph，用瀏覽器開，找最寬的塔（熱點）

2. 讀 flame graph：理解寬度（佔 CPU）和高度（呼叫堆疊），點擊放大

3. differential：對優化前後的版本產生 differential flame graph，看哪裡變快（藍）變慢（紅）

4. 完整流程：跑「故意弄壞」的優化流程（profile 找熱點 → 優化 → hyperfine 驗證 → diff flame graph）

5. 理解 on/off-CPU：思考一個「慢但 CPU 不忙」的問題該用 off-CPU（vs 本課的 on-CPU）

## 本章重點整理

- flame graph 視覺化 profile：X 軸寬度=佔 CPU 比例（找寬的塔）、Y 軸=呼叫堆疊；複雜程式特別直觀
- 產生：perf record -g → perf script → FlameGraph 工具（stackcollapse + flamegraph）→ SVG（互動）
- differential flame graph 比較版本：紅=變慢（regression）、藍=變快（優化）——對 compiler 驗證優化+抓 regression
- on-CPU（CPU 在算什麼，CPU-bound）vs off-CPU（程式在等什麼，IO/鎖 bound）；perf_bench 主要 on-CPU
- 完整優化流程：profile 找熱點 → 優化 → hyperfine 驗證（統計）→ differential flame graph 看哪裡變快

## 自我檢核

- [ ] 會產生和讀 flame graph（寬度=熱點、高度=呼叫堆疊）
- [ ] 會用 differential flame graph 比較版本（看變快/變慢）
- [ ] 知道 differential flame graph 對 compiler 工作的價值（驗證+抓 regression）
- [ ] 理解 on-CPU vs off-CPU profiling 的差別
- [ ] 能走完整的優化流程（profile→優化→驗證→視覺化）

## 延伸閱讀

### 必讀

- **[Flame Graphs](https://www.brendangregg.com/flamegraphs.html)** — Brendan Gregg
  - **讀哪裡**：CPU flame graphs、differential flame graphs
  - **為什麼值得讀**：火焰圖發明者的權威資源

- **[FlameGraph GitHub](https://github.com/brendangregg/FlameGraph)** — Brendan Gregg
  - **讀哪裡**：README、difffolded.pl 的用法
  - **為什麼值得讀**：工具本身和用法

### 文章

- **[Differential flame graphs](https://www.brendangregg.com/blog/2014-11-09/differential-flame-graphs.html)** — Brendan Gregg
  - **這篇說什麼**：differential flame graph 怎麼用、怎麼讀
  - **為什麼值得讀**：本章 differential 那節的權威

### 書籍

- **《Systems Performance》— Ch 6 (flame graphs)** — Brendan Gregg
  - **為什麼值得讀**：flame graph 放進效能分析的框架

Part 3（Profiling 工具）到此完成。接下來 Part 4 是 perf_bench 的重頭戲——compiler-centric 的效能分析：compiler flag 的真相、PGO/LTO/vectorization。這是和 SiFive compiler 工作最直接對口的部分。

→ [Ch 10 Compiler flag scan：-O2 vs -O3 真相、-march 選擇](./10-compiler-flags.md)
