# Ch 8 — llvm-mca：靜態分析 throughput / bottleneck

> **目標**：用 llvm-mca（LLVM Machine Code Analyzer）靜態分析一段組合語言的 pipeline 利用率、throughput、瓶頸——不用跑程式、不用硬體，就能預測「這段 code 在某個微架構上跑多快、瓶頸在哪」。理解它怎麼運作（用 CPU 的 scheduling model 模擬）、和 perf（動態）的互補、以及它對 compiler 工作的價值（分析 compiler 產生的 hot loop）。這是找 micro-optimization 機會的神器。

> **環境**：Linux，llvm-mca（Ch 0 已裝）。clang 產生組合語言。

## 為什麼需要靜態分析？

Ch 7 的 perf 是動態分析（跑程式、取樣）——強大但需要：能跑的硬體（要分析 RISC-V 的程式碼，要有 RISC-V 硬體或 QEMU）、實際執行（有副作用）、只看到「這次執行」。有時你想「**不跑程式就分析一段 hot loop**」——尤其 compiler 工作：你改了 compiler，產生了一段組合語言，想知道「這段 code 理論上跑多快、瓶頸在哪」，不想每次都跑完整 benchmark。

**llvm-mca** 做這個——它用 CPU 的 **scheduling model**（描述微架構的指令延遲、執行單元、throughput）**模擬**一段組合語言的執行，預測 throughput、pipeline 利用率、瓶頸。這對 compiler 工作極有用——快速分析「compiler 產生的這段 code 好不好」，找出 micro-optimization 機會（如「這個瓶頸在除法單元」「指令排程不好」）。這章把 llvm-mca 用熟，這是 compiler-centric 效能分析的神器。

## 先建立直覺:用模型預測效能

```
llvm-mca = 用「微架構模型」靜態預測一段 code 的效能

  perf（動態）：真的跑，測量實際效能
  llvm-mca（靜態）：不跑，用模型「預測」效能
        │
  llvm-mca 怎麼預測：
    1. 讀一段組合語言
    2. 用 CPU 的 scheduling model（每條指令的延遲、用哪個執行單元、throughput）
    3. 模擬「亂序執行這段 code」會怎樣
    4. 預測：throughput（每 iteration 幾個 cycle）、瓶頸（哪個資源滿了）
        │
  → 不用硬體、不用跑，就能分析
    特別適合：
      - 分析 hot loop（一小段重複執行的）
      - compiler 工作（分析產生的 code）
      - 比較不同的 code 序列（哪個理論上更快）
      - 分析其他架構（如有 RISC-V model，不用 RISC-V 硬體）
        │
  注意：是「模型預測」，不是真實測量
    模型有假設（理想 cache、無分支 miss）→ 和真實有差距
    但對「指令層的 throughput/瓶頸」很準
```

關鍵心智：llvm-mca 用 CPU 的 **scheduling model**（微架構的指令延遲、執行單元、throughput）**靜態預測**一段組合語言的效能——不用跑、不用硬體。它預測 throughput（每 iteration 幾 cycle）和瓶頸（哪個資源滿了）。適合分析 hot loop、compiler 產生的 code、比較 code 序列。注意是「模型預測」（有理想化假設），不是真實測量。

> llvm-mca 是靜態分析（Ch 1 of observability_tools 的 static），和 perf（動態）互補。它用的 scheduling model 是 compiler 後端的核心（如果你修過 compiler_backend 課的 Ch 13 scheduling model，這裡是它的應用）。

## llvm-mca 的基本用法

```bash
cd ~/perflab
# 寫一段有 hot loop 的程式
cat > loop.c <<'EOF'
void compute(int *a, int *b, int *c, int n) {
    for (int i = 0; i < n; i++) {
        c[i] = a[i] * b[i] + a[i];   // hot loop body
    }
}
EOF

# 產生組合語言（加 llvm-mca 標記）
clang -O2 -S loop.c -o loop.s
# 看 hot loop 的組合語言
cat loop.s

# === 用 llvm-mca 分析整個檔案 ===
llvm-mca loop.s
# 或分析特定區段（用 # LLVM-MCA-BEGIN / # LLVM-MCA-END 標記）

# llvm-mca 的輸出（核心資訊）：
# Iterations:        100
# Instructions:      500           ← 總指令數
# Total Cycles:      350           ← 總 cycle 數
# Total uOps:        600           ← micro-ops
#
# Dispatch Width:    4             ← 每 cycle 能發射 4 條（superscalar）
# uOps Per Cycle:    1.71          ← 實際每 cycle 的 uOps（< 4 = 沒餵飽）
# IPC:               1.43          ← 預測的 IPC
# Block RThroughput: 3.5           ← 每 iteration 的 throughput（cycle）
#       │
# → 一眼看出：這段 code 每 iteration 約 3.5 cycle，IPC 1.43
#   dispatch width 4 但只用 1.71 → 沒餵飽（有改善空間）

# === 指定目標 CPU（不同微架構的 model）===
llvm-mca -mcpu=skylake loop.s       # Intel Skylake 的 model
llvm-mca -mcpu=znver3 loop.s        # AMD Zen3
llvm-mca -march=riscv64 -mcpu=sifive-u74 loop.s   # RISC-V（SiFive！）
# → 同一段 code 在不同微架構的預測效能（不用那些硬體）
```

> **llvm-mca 用 `-mcpu` 指定目標微架構，預測「這段 code 在那個 CPU 跑多快」——對 RISC-V/compiler 工作不需硬體就能分析**。llvm-mca 分析一段組合語言，輸出核心效能預測：**Block RThroughput**（每 iteration 的 throughput，幾個 cycle——這是 hot loop 最重要的指標，越小越快）、**IPC**（預測的每週期指令數）、**uOps Per Cycle**（實際每 cycle 發射的 micro-ops——和 Dispatch Width 比較，如果遠小於 dispatch width 表示「沒餵飽」CPU，有改善空間）。最強大的是 **`-mcpu`**——指定目標微架構的 scheduling model，預測「這段 code 在**那個 CPU**跑多快」。這對 **RISC-V/compiler 工作極有價值**——`llvm-mca -mcpu=sifive-u74`（SiFive 的 core）能預測 code 在 SiFive core 的效能，**不需要 SiFive 硬體**！這讓 compiler 開發者能快速分析「我產生的這段 code 在目標 CPU 好不好」，不用每次都在硬體上跑完整 benchmark。同一段 code 在不同 `-mcpu`（Skylake/Zen3/SiFive）的預測，讓你看「這段 code 在不同微架構的表現」（如某段 code 在某 CPU 因為執行單元配置不同而表現不同）。這是 llvm-mca 對 compiler 工作的核心價值——**快速、不需硬體、針對目標微架構的靜態效能分析**。當你改了 compiler 的某個優化，產生了不同的組合語言，llvm-mca 能立刻告訴你「新的 code 序列在目標 CPU 理論上更快還更慢」，不用跑完整 benchmark（快速迭代）。

## 解讀瓶頸

```bash
# llvm-mca 的瓶頸分析（哪個資源是限制）
llvm-mca -bottleneck-analysis loop.s
# Cycles with backend pressure increase: 40%
#   Resource pressure ...
#   - SKLPort0: 30%     ← Port 0（某執行單元）壓力大（瓶頸！）
#   - SKLPort1: 10%
#       │
# → 看出瓶頸在「Port 0」（某類執行單元，如乘法/除法單元）
#   表示「這類指令太多，那個單元忙不過來」

# 詳細的 resource pressure（每條指令用哪個 port）
llvm-mca -all-views loop.s | head -40
# 顯示每條指令的 resource 使用、延遲、throughput

# === timeline view（看指令在 pipeline 怎麼流動）===
llvm-mca -timeline loop.s
# 顯示每條指令在哪個 cycle 進入哪個階段（D=dispatch, e=execute, R=retire）
# [0,0]     DeeeeER   .    .   addl    ← 這條指令的 pipeline 時間線
# [0,1]     D====eeeeER    .   imull   ← 這條等了（=）才能執行（相依/資源）
# → 看出哪條指令「等待」（相依或資源衝突）
```

```
llvm-mca 的瓶頸類型：

  資源瓶頸（resource bound）：
    某個執行單元（port）滿了
    例：太多除法 → 除法單元忙 → 瓶頸
    優化：減少那類指令、用別的指令替代
        │
  相依瓶頸（dependency bound）：
    指令鏈相依（B 要等 A 的結果）→ 沒平行度
    例：a = a + x（每次都要等前一次）
    優化：打破相依鏈（用多個累加器）
        │
  dispatch/retire 瓶頸：
    dispatch width 或 retire 限制
        │
  → llvm-mca 告訴你瓶頸類型，指導 micro-optimization
    resource bound → 換指令/減少那類指令
    dependency bound → 打破相依鏈
```

> **llvm-mca 的瓶頸分析（resource bound vs dependency bound）指導 micro-optimization——資源瓶頸換指令、相依瓶頸打破相依鏈**。llvm-mca 的 `-bottleneck-analysis` 告訴你**瓶頸類型**：**資源瓶頸（resource bound）**——某個執行單元（port）滿了（如太多除法指令讓除法單元忙不過來），優化方向是「減少那類指令或用別的指令替代」（如用乘法+移位代替除法）；**相依瓶頸（dependency bound）**——指令鏈相依（B 要等 A 的結果，沒有平行度可榨），優化方向是「打破相依鏈」（如用多個累加器讓 CPU 能平行算）。`-timeline` view 視覺化「指令在 pipeline 怎麼流動」——你能看到哪條指令「等待」（顯示 `=`，因為相依或資源衝突），精確理解瓶頸。這個瓶頸分析對 **micro-optimization** 極有價值——它告訴你「為什麼這段 hot loop 慢」（資源還是相依），進而知道「怎麼改」。經典的優化是**打破相依鏈**——如 `for(i...) sum += a[i]`（每次累加都要等前一次，相依瓶頸）改成用 4 個累加器（`sum0 += a[i]; sum1 += a[i+1]...`，4 個獨立的鏈能平行算，最後加總）——llvm-mca 能驗證這個改動讓 throughput 提升。對 compiler 工作，llvm-mca 指導「compiler 該怎麼產生更好的 code」——如果分析顯示「除法單元是瓶頸」，compiler 可以用 strength reduction（除法換乘法）；如果「相依鏈是瓶頸」，compiler 可以做 loop unrolling + 多累加器。理解 llvm-mca 的瓶頸分析，你能在指令層找出和指導 micro-optimization——這是 perf_bench 對 compiler 工作最直接的應用。

## llvm-mca vs perf:互補

```
llvm-mca（靜態）vs perf（動態）：

  llvm-mca（靜態模型預測）：
    優點：不用硬體/不用跑、快、針對特定 CPU、分析指令層
          理想化（無 cache miss/branch miss）→ 純看「指令排程/資源」
    缺點：不含真實的 cache/branch 行為（理想假設）
          只分析一小段（hot loop），不是整個程式
        │
  perf（動態真實測量）：
    優點：真實效能（含 cache/branch）、整個程式
    缺點：要硬體/要跑、有真實的雜訊
        │
  → 互補：
    perf 找熱點（哪個 hot loop）+ 測真實效能
    llvm-mca 分析熱點的「指令層瓶頸」（資源/相依，理想情況）
        │
  compiler 工作流：
    perf 找熱點 → 看熱點的組合語言 → llvm-mca 分析指令瓶頸
    → 想 compiler 怎麼產生更好的 code → 驗證（llvm-mca + perf）
        │
  注意 llvm-mca 的限制：理想模型，和真實有差距
    它說「快 10%」不代表真實快 10%（沒算 cache/branch）
    但「指令層的瓶頸」分析很準（資源/相依）
```

> **llvm-mca（靜態，理想化，分析指令排程/資源）和 perf（動態，真實，含 cache/branch）互補——compiler 工作流結合兩者**。llvm-mca 和 perf 各有取捨：**llvm-mca**（靜態模型）——不用硬體/不用跑、快、針對特定 CPU、**理想化**（假設無 cache miss/branch miss，純看「指令排程和執行資源」），但不含真實的 cache/branch 行為、只分析一小段（hot loop）；**perf**（動態真實）——真實效能（含 cache/branch）、整個程式，但要硬體/要跑、有雜訊。**互補使用**：**perf 找熱點**（哪個 hot loop 最花時間）+ 測真實效能，**llvm-mca 分析熱點的指令層瓶頸**（資源/相依，理想情況下）。**compiler 工作流**：perf 找熱點 → 看熱點的組合語言 → llvm-mca 分析「指令層瓶頸」（這段 code 的資源/相依瓶頸）→ 想「compiler 怎麼產生更好的 code」→ 驗證（llvm-mca 看指令層改善 + perf 看真實效能改善）。**重要的限制認知**：llvm-mca 是**理想模型**——它說「這段 code 快 10%」**不代表真實快 10%**（因為它沒算 cache miss/branch miss——真實情況這些可能主導）。但 llvm-mca 對「**指令層的瓶頸**」（資源衝突、相依鏈）分析**很準**（這些是確定的微架構行為）。所以用 llvm-mca 分析「指令排程/資源瓶頸」（它的強項），用 perf 驗證「真實效能」（含記憶體/分支）。對 compute-bound 的 hot loop（cache 行為好，瓶頸在指令），llvm-mca 的預測接近真實；對 memory-bound 的（瓶頸在 cache miss），llvm-mca 的理想假設和真實差很多（要靠 perf/cachegrind）。理解兩者的互補和各自的強項/限制，你能在 compiler 效能工作中正確地用——llvm-mca 快速分析指令層、perf 驗證真實效能。這是 perf_bench 的核心工具組合（靜態 + 動態）。

## 故意弄壞:打破相依鏈

```bash
cd ~/perflab
# 展示「相依鏈瓶頸」和打破它（llvm-mca 驗證）
# 版本 1：單一累加器（相依鏈）
cat > dep.c <<'EOF'
float sum_dep(float *a, int n) {
    float sum = 0;
    for (int i = 0; i < n; i++) sum += a[i];  // 相依：每次都要等前一次的 sum
    return sum;
}
EOF
clang -O2 -ffast-math -S dep.c -o dep.s
echo "=== 單一累加器（相依鏈）==="
llvm-mca dep.s 2>/dev/null | grep -E 'Block RThroughput|IPC'
# Block RThroughput: 較高（如 4.0）→ 慢（相依鏈限制平行度）

# 版本 2：多累加器（打破相依鏈）
cat > nodep.c <<'EOF'
float sum_nodep(float *a, int n) {
    float s0=0, s1=0, s2=0, s3=0;
    for (int i = 0; i < n; i += 4) {
        s0 += a[i]; s1 += a[i+1]; s2 += a[i+2]; s3 += a[i+3];  // 4 個獨立的鏈
    }
    return s0 + s1 + s2 + s3;
}
EOF
clang -O2 -ffast-math -S nodep.c -o nodep.s
echo "=== 多累加器（打破相依）==="
llvm-mca nodep.s 2>/dev/null | grep -E 'Block RThroughput|IPC'
# Block RThroughput: 較低（如 1.5）→ 快（4 個獨立鏈能平行）

# → llvm-mca 驗證：多累加器打破相依鏈，throughput 提升
#   (浮點加法有延遲，單一累加器每次要等前一次 → 沒平行
#    4 個累加器 → CPU 能同時算 4 個 → 餵飽執行單元)
# 這是 compiler 的 loop unrolling + 多累加器優化（-ffast-math 才允許重排浮點）
```

> **llvm-mca 驗證「多累加器打破相依鏈」讓 throughput 提升——這是經典的 micro-optimization，也是 compiler 的優化**。這個例子展示 llvm-mca 在 micro-optimization 的價值——分析和驗證「打破相依鏈」。**單一累加器**（`sum += a[i]`）有**相依鏈**——每次累加都要等前一次的 `sum`（浮點加法有延遲，如 4 cycle），所以每 4 cycle 才能做一次加法（沒有平行度），llvm-mca 顯示較高的 Block RThroughput（慢）。**多累加器**（4 個獨立的 `s0/s1/s2/s3`）**打破了相依鏈**——4 個獨立的累加鏈能**平行**執行（CPU 同時算 4 個），餵飽執行單元，llvm-mca 顯示較低的 throughput（快）。llvm-mca **驗證**了這個優化（throughput 從 4.0 降到 1.5）——不用跑就能確認「多累加器更快」。這是經典的 micro-optimization——**打破相依鏈以增加指令級平行**。注意需要 `-ffast-math`（允許重排浮點運算——因為浮點加法不嚴格結合，重排可能改變結果，compiler 預設不做，要 `-ffast-math` 允許）。這也是 **compiler 的優化**——loop unrolling + 多累加器（compiler 在 `-ffast-math` 下會自動做）。對 compiler 工作，這個例子展示了：(1) 用 llvm-mca 分析「相依鏈瓶頸」；(2) 驗證「打破相依鏈」的優化效果；(3) 理解 compiler 怎麼做這個優化（unroll + 多累加器，需要 fast-math）。這是 perf_bench 的核心——**用工具（llvm-mca）分析指令層的瓶頸（相依鏈），驗證優化（多累加器），理解 compiler 的角色**。Part 3 的 llvm-mca 讓你能在指令層分析和優化 hot loop——這對 compiler 效能工作（產生更好的 code）是直接的工具。

## 動手練習

1. 基本分析：對一段 hot loop 的組合語言用 llvm-mca，看 Block RThroughput 和 IPC

2. 不同 CPU：用 `-mcpu` 分析同段 code 在不同微架構（如 skylake vs sifive-u74）

3. 瓶頸分析：用 `-bottleneck-analysis` 看瓶頸（resource 還是 dependency）

4. timeline：用 `-timeline` 看指令在 pipeline 的流動，找「等待」的指令

5. 跑「故意弄壞」：用 llvm-mca 比較單累加器 vs 多累加器的 throughput，理解打破相依鏈

## 本章重點整理

- llvm-mca 用 CPU 的 scheduling model 靜態預測一段組合語言的 throughput/瓶頸——不用硬體、不用跑
- `-mcpu` 指定目標微架構（如 sifive-u74）→ 預測在那個 CPU 的效能（對 RISC-V/compiler 不需硬體）
- 瓶頸類型：resource bound（執行單元滿，換指令）vs dependency bound（相依鏈，打破它）
- llvm-mca（靜態，理想化，分析指令排程/資源）和 perf（動態，真實，含 cache/branch）互補
- llvm-mca 是理想模型（無 cache/branch miss）——指令層瓶頸準，但「快 X%」不代表真實（要 perf 驗證）

## 自我檢核

- [ ] 知道 llvm-mca 怎麼運作（scheduling model 靜態預測），和 perf 的差別
- [ ] 會用 llvm-mca 分析 throughput 和瓶頸（resource/dependency）
- [ ] 知道 `-mcpu` 能分析不同微架構（對 RISC-V 不需硬體的價值）
- [ ] 理解 llvm-mca 的限制（理想模型，不含 cache/branch）
- [ ] 會用 llvm-mca 驗證 micro-optimization（如打破相依鏈）

## 延伸閱讀

### 官方文件

- **[llvm-mca 文件](https://llvm.org/docs/CommandGuide/llvm-mca.html)** — LLVM
  - **讀哪裡**：輸出解讀、bottleneck-analysis、timeline
  - **為什麼值得讀**：llvm-mca 的權威

### 文章

- **[Using llvm-mca](https://easyperf.net/blog/2018/10/03/Analyzing-performance-of-VPGATHER-instruction)** — Denis Bakhvalov
  - **這篇說什麼**：用 llvm-mca 分析具體的 code 序列
  - **為什麼值得讀**：本章 llvm-mca 的實戰範例

### 相關

- **compiler_backend 課的 scheduling model 章**
  - **為什麼值得讀**：llvm-mca 用的 scheduling model 就是 compiler 後端的核心（指令延遲/資源描述）

下一章看 flame graph——把 profiling 結果視覺化的標準工具。從 perf record 的資料產生火焰圖，一眼看出熱點和呼叫關係。

→ [Ch 9 Flame graph 與 on-CPU profiling](./09-flamegraph.md)
