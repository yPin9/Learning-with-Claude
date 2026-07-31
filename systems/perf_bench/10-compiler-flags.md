# Ch 10 — Compiler flag scan：-O2 vs -O3 真相、-march 選擇

> **目標**：破除 compiler flag 的迷思——「-O3 比 -O2 快」「越高越快」大多是錯的。理解各優化等級（-O0/-O1/-O2/-O3/-Os）實際做什麼、為什麼 -O3 有時更慢、-march/-mtune 的選擇（針對特定微架構）、以及怎麼系統地 scan flag 找出對你的 workload 最好的組合。核心信條：每個 flag 都要 benchmark 確認，不要相信「常識」。

> **環境**：Linux，gcc/clang，hyperfine（Ch 0）。RISC-V 或 x86。

## 為什麼 compiler flag 充滿迷思？

「用 -O3 啊，比 -O2 快」——這是業界最常見的迷思之一。實際上 **-O3 不一定比 -O2 快**，有時甚至更慢（code size 變大、I-cache miss、過度 inline）。同樣「-march=native 一定更快」「-funroll-loops 加了就快」這些「常識」大多沒經過驗證。

這章破除這些迷思——理解各 flag 實際做什麼，以及最重要的：**每個 flag 都要 benchmark 確認**。對 compiler 工作這特別重要——SiFive 的 compiler 要為 RISC-V 產生最佳 code，你要知道哪些 flag 真的有用、對什麼 workload 有用、為什麼。不能靠「常識」（常識常錯），要靠測量。這章教你系統地 scan flag、用前面的工具（hyperfine 測、perf 分析）驗證每個 flag 的真實效果。這是 perf_bench 「實測取代空談」信條的核心應用。

## 先建立直覺:優化等級做什麼

```
GCC/Clang 的優化等級（-O0 到 -O3, -Os）：

  -O0：不優化（debug 用，最慢但最好 debug）
    每個變數都在記憶體、沒有任何優化
        │
  -O1：基本優化
    簡單的優化（dead code、常數折疊、基本暫存器分配）
        │
  -O2：標準優化（生產的標準！）
    大部分有用的優化（inline、迴圈優化、向量化(部分)、排程）
    code size 和速度的好平衡
        │
  -O3：激進優化
    -O2 + 更激進（更多 inline、更多向量化、loop unroll）
    code size 變大 → 可能 I-cache miss → 「有時更慢」！
        │
  -Os：優化 code size（不是速度）
    -O2 但避免讓 code 變大的優化（嵌入式/I-cache 受限時有用）
        │
  -Ofast：-O3 + 放寬標準（如 -ffast-math，可能改變數值結果）
        │
  → -O2 是生產標準（好平衡）
    -O3 不一定更快（要 benchmark）
    -Os 對 code size 受限（嵌入式、I-cache）有用
```

關鍵心智：優化等級 **-O2 是生產標準**（速度和 code size 的好平衡）。**-O3 不一定更快**（更激進的優化讓 code size 變大，可能 I-cache miss 而更慢——要 benchmark）。**-Os** 優化 code size（嵌入式/I-cache 受限有用）。**-Ofast** 放寬標準（如 fast-math，可能改變數值結果）。核心：**不要假設「越高越快」，要 benchmark**。

> 這章是 perf_bench 「實測取代空談」的核心應用。用前面的工具（hyperfine 測、perf 分析）驗證 flag 的真實效果。

## -O2 vs -O3:破除迷思

```bash
cd ~/perflab
# 系統地比較 -O2 vs -O3（用真實的 benchmark）
gcc -O2 demo.c -o demo_O2
gcc -O3 demo.c -o demo_O3

# hyperfine 比較（統計嚴謹，Ch 4）
hyperfine './demo_O2' './demo_O3'
# 可能的結果：
# Summary: './demo_O3' ran 1.00 ± 0.02 times faster   ← 幾乎沒差！
# 或： './demo_O2' ran 1.03 ± 0.02 times faster        ← O2 反而快！
# → -O3 不一定比 -O2 快（甚至有時更慢）

# 看 code size 的差別（-O3 通常更大）
size demo_O2 demo_O3
#    text    data     bss
#    1234     ...           demo_O2
#    1567     ...           demo_O3   ← O3 的 code 更大（更多 inline/unroll）

# 為什麼 -O3 有時更慢：
# 1. code size 大 → I-cache miss 增加（Ch 5）
# 2. 過度 inline → 暫存器壓力大、code bloat
# 3. 激進的向量化/unroll 對某些 workload 沒幫助甚至有害
# → -O3 的激進優化「不一定」對你的 workload 有利
```

> **-O3 不一定比 -O2 快——它的激進優化（更多 inline/unroll/向量化）讓 code size 變大，可能 I-cache miss 而更慢**。這是 compiler flag 最大的迷思——「-O3 比 -O2 快」。實際上 hyperfine 比較常顯示**兩者幾乎沒差**，有時 **-O2 反而快**！為什麼 -O3 有時更慢：(1) **code size 變大**（更多 inline、loop unroll——`size` 命令能看到 -O3 的 text 段更大）→ **I-cache miss 增加**（Ch 5——指令也要進 cache，code 太大塞不下 I-cache，取指變慢）；(2) **過度 inline**——把函式內聯到呼叫處，雖然省了呼叫開銷但讓 code bloat、暫存器壓力大；(3) **激進的向量化/unroll** 對某些 workload 沒幫助甚至有害（如分支多的 code 不適合激進向量化）。所以 **-O3 的激進優化「不一定」對你的 workload 有利**——它做了更多優化，但這些優化的代價（code size、I-cache）可能超過收益。**結論**：**-O2 是生產標準**（好平衡），要用 -O3 **必須 benchmark 確認對你的 workload 真的更快**（不能假設）。對 compiler 工作，這個認知很重要——optimization level 的選擇要根據實測，且要理解「為什麼某個 workload -O3 更快/更慢」（通常和 code size/I-cache/向量化適用性有關）。這破除了「越高越快」的迷思——優化是 trade-off，更激進不等於更好。用 hyperfine（統計嚴謹，Ch 4）+ perf（看 I-cache miss 等，Ch 6）驗證和理解 flag 的真實效果。

## -march / -mtune:針對微架構

```bash
# -march：指定「目標指令集」（用哪些指令）
# -mtune：指定「優化目標微架構」（為哪個 CPU 排程，但仍用通用指令集）

# x86 範例
gcc -O2 demo.c -o demo_generic           # 通用（保守，到處能跑）
gcc -O2 -march=native demo.c -o demo_native   # 用本機 CPU 的所有指令（AVX 等）
hyperfine './demo_generic' './demo_native'
# native 可能快（用了 AVX 等新指令）—— 但只能在這台 CPU 跑！

# RISC-V 範例（SiFive 相關）
# -march=rv64gc：基本的 RV64 + 通用擴展（G=IMAFD, C=壓縮）
# -march=rv64gcv：加 V（向量擴展，RVV）—— 如果硬體支援
riscv64-linux-gnu-gcc -O2 -march=rv64gc demo.c -o demo_rv
riscv64-linux-gnu-gcc -O2 -march=rv64gcv demo.c -o demo_rvv   # 用向量擴展
# → -march=rv64gcv 讓 compiler 能用 RVV 向量指令（如果 workload 能向量化）

# -mtune（為特定微架構排程，不限制指令集）
gcc -O2 -mtune=native demo.c -o demo_tuned   # 為本機微架構排程
riscv64-linux-gnu-gcc -O2 -mtune=sifive-u74 demo.c   # 為 SiFive U74 排程

# === march vs mtune 的差別 ===
# -march=X：「用 X 的指令集」（產生的 binary 只能在支援 X 的 CPU 跑）
# -mtune=X：「為 X 優化排程」（但用通用指令集，到處能跑，只是對 X 最佳）
```

> **-march（用哪些指令，限制可跑的 CPU）vs -mtune（為哪個微架構排程，但通用指令集）——對 RISC-V 工作 -march=rv64gcv 啟用向量擴展是關鍵**。兩個容易混淆的 flag：**-march=X**——指定「**用 X 的指令集**」（compiler 能用 X 支援的所有指令，如 AVX、RVV 向量擴展），但產生的 binary **只能在支援 X 的 CPU 跑**（用了新指令，舊 CPU 不認得）；**-mtune=X**——「**為 X 優化排程**」（指令排程、優化決策針對 X 微架構），但**用通用指令集**（binary 到處能跑，只是對 X 最佳）。對 **RISC-V/SiFive 工作**，這特別重要：**-march=rv64gc**（基本 RV64 + 通用擴展）vs **-march=rv64gcv**（加 **V = 向量擴展 RVV**）——`rv64gcv` 讓 compiler 能用 **RVV 向量指令**（如果 workload 能向量化，這對 SIMD 友善的 code 大幅加速）。**-mtune=sifive-u74**（為 SiFive U74 core 排程）讓 compiler 的指令排程針對那個 core 的微架構（pipeline、執行單元）優化。**選擇**：要用新指令集（向量化等）用 `-march`（但限制可跑的硬體）；要為特定 CPU 優化但保持相容用 `-mtune`（通用指令集 + 針對性排程）。常見組合：`-march=最低相容的 + -mtune=目標微架構`（用相容的指令集 + 為目標排程）。對 compiler 工作，理解 march/mtune 是日常——你要為目標 RISC-V core 選對的 march（啟用它支援的擴展如 RVV）和 mtune（為它的微架構排程）。`-march=native`/`-mtune=native`（自動偵測本機 CPU）方便但只適合「在同型 CPU 部署」（用了本機指令，別的 CPU 可能不支援）。這是 perf_bench 對 RISC-V compiler 工作的核心——正確的 march/mtune 選擇直接影響產生的 code 的效能和相容性。

## 系統地 scan flag

```bash
# 系統地 scan flag，找出對你的 workload 最好的組合（不靠常識，靠測量）
cd ~/perflab

# 對一組 flag 系統測量
for flags in "-O2" "-O3" "-O2 -march=native" "-O3 -march=native" "-Os" "-O2 -funroll-loops"; do
    gcc $flags demo.c -o demo_test 2>/dev/null
    echo -n "flags: $flags → "
    # 用 hyperfine 測（這裡簡化用 time，實際用 hyperfine 更嚴謹）
    hyperfine --warmup 3 './demo_test' 2>/dev/null | grep 'Time' | head -1
done
# 系統地比較各 flag 組合的真實效能
# → 找出對「你的 workload」最好的（可能不是 -O3！）

# 個別 flag 的影響（-O2 加單一 flag）
# -funroll-loops、-finline-functions、-ftree-vectorize 等
# 各別測，看哪個對你的 workload 有幫助

# === 用 perf 理解「為什麼某個 flag 有幫助/沒幫助」===
# 如果 -O3 沒幫助，看 perf：
perf stat ./demo_O2 2>&1 | grep -E 'instructions|cache-misses|insn per'
perf stat ./demo_O3 2>&1 | grep -E 'instructions|cache-misses|insn per'
# 比較指令數、I-cache miss → 理解 -O3 的優化是否真的有用
# （如 -O3 指令數變少但 I-cache miss 增加 → 抵消了）
```

> **系統地 scan flag + 用 perf 理解「為什麼」——這是 perf_bench「實測取代空談」的核心，破除「靠常識選 flag」**。優化 flag 的正確方法不是「用常識」（用 -O3 因為「聽說比較快」），而是**系統地 scan + 實測**：(1) 對一組 flag 組合（-O2/-O3/+march/+unroll/-Os…）系統測量（hyperfine，統計嚴謹）；(2) 找出對「**你的 workload**」最好的組合（可能不是 -O3——每個 workload 不同）；(3) 用 **perf 理解「為什麼」**——如果 -O3 沒幫助，比較 -O2 和 -O3 的 perf 數字（指令數、I-cache miss、IPC），理解「-O3 的優化是否真的有用」（如 -O3 指令數變少但 I-cache miss 增加，互相抵消，所以沒淨收益）。這個「scan + 實測 + 用 perf 理解」是 perf_bench 的核心方法——**不靠常識，靠資料**。對 compiler 工作，這個方法是日常——你要為產品選 flag，不能靠「業界都用 -O3」，要實測對目標 workload 的效果，並理解原因（這樣才能改進 compiler——知道為什麼某個優化有用/沒用）。注意 scan flag 要嚴謹（hyperfine 多次測量、看顯著性，Ch 4）——否則可能把雜訊當成「flag 有效」。也要警惕 over-fitting（針對某個特定 benchmark 調 flag，但對真實 workload 沒用——Ch 2 的 gaming）。這章破除了 compiler flag 的迷思——**沒有「最好的 flag」，只有「對這個 workload 最好的 flag」，且要實測確認**。這是 perf_bench 反覆強調的——效能是 workload-specific 的，要測量不要假設。

## 故意弄壞:-O3 反而更慢的案例

```bash
cd ~/perflab
# 一個「-O3 反而更慢」的真實案例（code bloat 造成 I-cache miss）
cat > bloat.c <<'EOF'
#include <stdio.h>
// 很多小函式（-O3 會激進 inline，造成 code bloat）
#define MAKE_FN(n) long fn##n(long x) { return (x * n + n) % (n + 7); }
MAKE_FN(1) MAKE_FN(2) MAKE_FN(3) MAKE_FN(4) MAKE_FN(5)
MAKE_FN(6) MAKE_FN(7) MAKE_FN(8) MAKE_FN(9) MAKE_FN(10)
int main() {
    long total = 0;
    for (long i = 0; i < 50000000L; i++) {
        // 呼叫很多函式（-O3 全 inline → code 變很大）
        total += fn1(i) + fn2(i) + fn3(i) + fn4(i) + fn5(i)
               + fn6(i) + fn7(i) + fn8(i) + fn9(i) + fn10(i);
    }
    printf("%ld\n", total);
    return 0;
}
EOF

gcc -O2 bloat.c -o bloat_O2
gcc -O3 bloat.c -o bloat_O3

# 比較
hyperfine './bloat_O2' './bloat_O3'
# 可能：'./bloat_O2' ran 1.0X faster than './bloat_O3'
#   （或差不多——取決於 CPU 的 I-cache 大小）

# 看 code size
size bloat_O2 bloat_O3
# bloat_O3 的 text 更大（激進 inline）

# 用 perf 看 I-cache miss（-O3 的 code bloat 的代價）
echo "=== O2 ==="
perf stat -e instructions,L1-icache-load-misses ./bloat_O2 2>&1 | grep -E 'icache'
echo "=== O3 ==="
perf stat -e instructions,L1-icache-load-misses ./bloat_O3 2>&1 | grep -E 'icache'
# O3 的 I-cache miss 可能更多（code 大塞不下 I-cache）
# → 證明：-O3 的激進 inline 造成 code bloat → I-cache miss → 可能更慢
#   這就是為什麼「-O3 不一定更快」（優化的代價可能超過收益）
```

> **-O3 的激進 inline 造成 code bloat → I-cache miss → 可能更慢——用 perf 看 I-cache miss 證明「優化的代價可能超過收益」**。這個例子展示「-O3 反而更慢」的真實機制——**code bloat 造成 I-cache miss**。程式有很多小函式，-O3 會**激進地 inline**它們（把函式內聯到呼叫處），雖然省了函式呼叫的開銷，但讓 **code size 大幅增加**（`size` 看到 -O3 的 text 段更大）。code 太大塞不下 **I-cache**（指令 cache，Ch 5）→ **I-cache miss 增加**（perf 的 `L1-icache-load-misses` 顯示 -O3 更多）→ 取指變慢 → 可能**整體更慢**（inline 省的開銷被 I-cache miss 的代價抵消甚至超過）。這證明了「-O3 不一定更快」的具體機制——**優化（inline）的收益（省呼叫）可能小於代價（I-cache miss）**。這是 compiler 優化的本質——**每個優化都是 trade-off**，更激進不等於更好。用 perf 看 I-cache miss 讓你**理解「為什麼 -O3 沒幫助」**（不是「-O3 沒用」，而是「這個 workload 的 inline 收益被 I-cache 代價抵消」）。這個理解對 compiler 工作很重要——它告訴你「優化要考慮代價」（如 inline 要權衡 code size vs 呼叫開銷，這是 compiler 的 inline heuristic 要平衡的）。對 code size 受限的場景（嵌入式、I-cache 小的 RISC-V core），**-Os 或保守的優化**可能比 -O3 好（code 小，I-cache 命中率高）——這是嵌入式 compiler 工作的常見考量。這個例子完美體現 perf_bench 的核心——**用實測和 perf 分析破除「越高越快」的迷思，理解優化的 trade-off**。Part 4 開始你看到 compiler 優化的真相——不是「開更多優化就好」，而是「理解每個優化的收益和代價，針對 workload 選擇」。

## 動手練習

1. -O2 vs -O3：對你的程式用 hyperfine 比較 -O2 和 -O3，看是否真的更快（統計顯著嗎）

2. code size：用 `size` 看不同優化等級的 code size，理解 -O3 為什麼變大

3. march/mtune：理解兩者的差別，對 RISC-V 試 -march=rv64gc vs rv64gcv（如有 RVV）

4. scan flag：系統地測一組 flag 組合，找出對你的 workload 最好的

5. 跑「故意弄壞」：用 perf 看 -O3 的 code bloat 造成的 I-cache miss，理解「優化的代價」

## 本章重點整理

- -O2 是生產標準（速度/code size 好平衡）；-O3 不一定更快（激進優化的 code bloat 可能 I-cache miss）
- -Os 優化 code size（嵌入式/I-cache 受限有用）；-Ofast 放寬標準（fast-math 可能改變數值）
- -march（用哪些指令，限制可跑的 CPU）vs -mtune（為哪個微架構排程，通用指令集）
- RISC-V：-march=rv64gcv 啟用向量擴展 RVV、-mtune=sifive-u74 為 SiFive core 排程
- 核心信條：每個 flag 都要 benchmark 確認 + 用 perf 理解為什麼——不靠常識，靠測量（破除「越高越快」迷思）

## 自我檢核

- [ ] 知道各優化等級實際做什麼，為什麼 -O3 不一定更快
- [ ] 理解 -march 和 -mtune 的差別，RISC-V 的 rv64gcv（向量擴展）
- [ ] 會系統地 scan flag，用 hyperfine 測 + perf 理解
- [ ] 能解釋 -O3 的 code bloat 怎麼造成 I-cache miss 而更慢
- [ ] 內化「每個 flag 都要 benchmark」的信條（不靠常識）

## 延伸閱讀

### 文章

- **[-O3 的迷思](https://easyperf.net/blog/2019/11/27/Hardware-Effects)** / **[O2 vs O3 分析](https://gcc.gnu.org/onlinedocs/gcc/Optimize-Options.html)** — GCC 文件 / 各種
  - **這篇說什麼**：各優化等級實際做什麼、-O3 的 trade-off
  - **為什麼值得讀**：本章 flag 真相的權威

- **[GCC optimization options](https://gcc.gnu.org/onlinedocs/gcc/Optimize-Options.html)** — GCC
  - **讀哪裡**：-O 等級、-march/-mtune 那節
  - **為什麼值得讀**：每個 flag 的權威說明

### RISC-V

- **[RISC-V GCC options](https://gcc.gnu.org/onlinedocs/gcc/RISC-V-Options.html)** — GCC
  - **讀哪裡**：-march（擴展）、-mtune
  - **為什麼值得讀**：RISC-V 特定的 flag（rv64gcv 等）

### 書籍

- **《Performance Analysis and Tuning on Modern CPUs》— compiler 優化章** — Denis Bakhvalov
  - **為什麼值得讀**：compiler 優化的實用分析

下一章看 PGO/BOLT/Propeller——profile-guided 優化全家族，用「程式實際執行的 profile」指導優化。這是現代 compiler 優化最有效的技術之一。

→ [Ch 11 PGO / BOLT / Propeller：profile-guided 全家族](./11-pgo-bolt-propeller.md)
