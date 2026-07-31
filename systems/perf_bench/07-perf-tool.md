# Ch 7 — perf record / perf report 實戰

> **目標**：把 `perf record` + `perf report` 變成日常工具——找出程式的熱點函式、解讀 call graph（誰呼叫熱點）、annotate 到 source/組合語言（精確到哪一行、哪條指令）、用不同事件 profile（不只 CPU，也可 cache-miss profiling）。Ch 6 的 perf stat 給整體指標，這章用 perf record 定位「具體哪個函式/哪一行」是瓶頸。

> **環境**：Linux，perf（Ch 0）。程式用 `-g` 編譯（debug symbols + frame pointer）。

## 為什麼從 perf stat 進到 perf record？

Ch 6 的 `perf stat` 給你**整體指標**（IPC、cache-miss rate）——告訴你「程式整體的瓶頸類型」（記憶體 bound 還是 compute bound）。但它不告訴你「**是哪個函式、哪一行**」造成的。要優化，你需要定位到具體的程式碼。

`perf record` + `perf report` 做這個——它用取樣（Ch 12 of observability_tools 講過 perf 的取樣機制）統計「CPU 時間花在哪個函式」，找出**熱點**（hotspot）。然後 annotate 到 source 和組合語言，看「熱點函式的哪一行、哪條指令」最花時間。這是「從整體瓶頸到具體程式碼」的關鍵——找到熱點才能針對性優化（80/20 法則，20% 的程式碼吃 80% 的時間）。這章把 perf record/report 用熟，這是 profiling 的日常工具。

## 先建立直覺:取樣找熱點

```
perf record = 取樣統計「CPU 時間花在哪」

  perf record 定期取樣（如每秒 1000 次）：
    「現在 CPU 在執行哪個函式？」記下來
        │
  取樣結束 → perf report 統計：
    哪個函式被取樣到最多次 = 吃最多 CPU = 熱點
        │
  perf report 顯示：
    Overhead  Function
      85%     compute        ← 85% 的 CPU 在 compute（熱點！）
      10%     process
       5%     ...
        │
  進一步：
    annotate：熱點函式的「哪一行/哪條指令」最花時間
    call graph（-g）：誰呼叫了熱點（呼叫鏈）
        │
  → perf record 找「具體哪個函式/哪一行」是瓶頸
    perf stat 給整體指標，perf record 定位到程式碼
    這是優化的目標定位（先找熱點才優化）
```

關鍵心智：`perf record` 用取樣統計「CPU 時間花在哪個函式」找出**熱點**（吃最多 CPU 的）。`perf report` 顯示熱點函式，annotate 到具體的行和指令，call graph 顯示誰呼叫熱點。這是「從整體瓶頸（perf stat）到具體程式碼」的定位——找到熱點才能針對性優化。

> perf record 的取樣機制和 observability_tools 課的 Ch 12 相同。這裡從「效能優化」角度，更注重 annotate 到組合語言和用不同事件 profile。

## perf record / report 核心用法

```bash
cd ~/perflab
gcc -g -O2 demo.c -o demo

# === perf record：取樣 ===
perf record -g ./demo            # -g 記錄 call graph（誰呼叫誰）
# 產生 perf.data

# === perf report：分析 ===
perf report                      # 互動式（瀏覽熱點、展開 call graph）
perf report --stdio              # 文字輸出
# Overhead  Command  Symbol
#   85.20%  demo     [.] compute      ← 熱點：compute 吃 85% CPU
#    8.30%  demo     [.] main
#    ...

# === call graph（誰呼叫熱點）===
perf report -g graph,0.5 --stdio | head -20
# 看「compute 是被誰呼叫的」（呼叫鏈）
# - 85% compute
#     - main                       ← main 呼叫 compute

# === annotate：精確到行/指令 ===
perf annotate compute            # 熱點函式 compute 的每行/每指令的 overhead
# 或在 perf report 裡按 'a' annotate 選定的函式
#  Percent | Source / Disassembly
#   30.00  |   imul %rax, %rax       ← 這條乘法指令吃 30%
#   25.00  |   idiv %rcx             ← 除法吃 25%（除法慢！）
# → 精確看出「熱點函式的哪條指令最花時間」

# === 取樣頻率 ===
perf record -F 999 -g ./demo     # -F 999：每秒取樣 999 次（更高更精確）
```

> **`perf record -g` + `perf report` 找熱點，`perf annotate` 精確到「哪條指令」——這是從函式到指令的定位**。perf 的 profiling 工作流：**`perf record -g`**（取樣，`-g` 記錄 call graph）→ **`perf report`**（顯示熱點函式，按 Overhead 排序——一眼看出「compute 吃 85% CPU」）。`-g` 的 **call graph** 讓你看「熱點被誰呼叫」（呼叫鏈——`compute` 是 `main` 呼叫的）——這對「熱點函式被很多地方呼叫」時定位「主要的呼叫者」有用。最強的是 **`perf annotate`**——它顯示熱點函式的**每一行/每條指令的 overhead**，精確到「**哪條指令最花時間**」（如「這條 idiv 除法指令吃 25%」——除法是慢指令，Ch 5）。這是 profiling 的最深定位——不只「哪個函式慢」，而是「函式裡的哪條指令慢」。對 compiler 工作這特別有用——你看 annotate 知道「compiler 產生的哪條指令是瓶頸」（如一條昂貴的除法、一個 cache miss 的 load），進而想「compiler 能不能產生更好的程式碼」（如用乘法代替除法、改善記憶體存取）。`-F`（取樣頻率，預設約 1000Hz，更高更精確但開銷大、檔案大）。記住核心：**`perf record -g ./prog` + `perf report`** 找熱點函式、**`perf annotate`** 看熱點的哪條指令——這是效能優化的定位流程（找到 80% 時間花在哪，針對性優化）。

## 用不同事件 profile

```bash
# perf record 不只能 profile CPU 時間，也能 profile 其他事件
# === cache-miss profiling（哪個函式 cache miss 最多）===
perf record -e cache-misses -g ./demo
perf report --stdio | head
# Overhead  Symbol
#   70%     compute     ← compute 造成 70% 的 cache miss（記憶體瓶頸在這）
# → 找出「哪個函式造成最多 cache miss」（不只 CPU 時間）

# === branch-miss profiling（哪個函式分支猜錯最多）===
perf record -e branch-misses -g ./demo
perf report --stdio | head
# → 找出「哪個函式有難預測的分支」

# === LLC-miss profiling（最致命的 cache miss）===
perf record -e LLC-load-misses -g ./demo

# === 結合 perf stat（整體）+ perf record（定位）===
# 1. perf stat：整體 cache-miss rate 高 → 記憶體瓶頸
# 2. perf record -e cache-misses：定位「是哪個函式的 cache miss」
# 3. perf annotate：那個函式的「哪一行」cache miss（哪個記憶體存取）
# → 從「整體記憶體瓶頸」定位到「具體哪行記憶體存取」
```

> **`perf record -e cache-misses` 定位「哪個函式造成最多 cache miss」——這把 Ch 6 的整體 cache-miss 細化到具體程式碼**。perf record 不只 profile CPU 時間，還能 profile **任何 PMU 事件**——`-e cache-misses`（哪個函式 cache miss 最多）、`-e branch-misses`（哪個函式分支猜錯最多）、`-e LLC-load-misses`（最致命的 cache miss）。這把 Ch 6 的「整體指標」細化到「具體程式碼」——**完整的定位流程**：(1) **`perf stat`** 看整體（cache-miss rate 高 → 記憶體瓶頸，Ch 6）；(2) **`perf record -e cache-misses`** 定位「**是哪個函式**的 cache miss」；(3) **`perf annotate`** 看那個函式的「**哪一行**」cache miss（哪個記憶體存取造成的）。這個三層定位（整體 → 函式 → 行）讓你精確找到「問題的根源」——不是「程式 cache miss 多」（太籠統），而是「`compute` 函式的第 15 行的 `arr[idx]` 存取造成 70% 的 cache miss」（精確，可針對性優化）。這是 perf_bench 的核心技能——**用對的事件 profile，定位到具體的程式碼**。對 compiler 工作，這指導優化——知道「哪個記憶體存取是 cache miss 熱點」，compiler 能加 prefetch、改善 data layout、loop tiling 等記憶體優化；知道「哪個分支是 branch-miss 熱點」，PGO 能優化它。這個「事件 profiling」是 perf 比一般 profiler（只看 CPU 時間）強的地方——它能 profile 任何硬體事件，精確定位各類瓶頸（CPU 時間、cache、分支）到具體程式碼。掌握它，你的效能分析就有了「精確定位任何瓶頸」的能力。

## 編譯選項對 profiling 的影響

```
profiling 需要的編譯選項（影響準確性）：

  -g（debug symbols）：
    讓 perf 顯示「函式名/行號」（否則只有位址，看不懂）
    必須！否則 perf report 只有一堆位址
        │
  frame pointer（-fno-omit-frame-pointer）：
    讓 call graph 準確（-g 取樣需要 frame pointer 來 unwind 堆疊）
    現代編譯器預設 omit frame pointer（省一個暫存器）
    → profiling 時加 -fno-omit-frame-pointer（call graph 才準）
    或用 DWARF unwind（perf record --call-graph dwarf，較慢但不需 frame pointer）
        │
  優化等級：
    -O0：profiling 失真（沒優化，行為和 release 不同）
    -O2/-O3：真實的 release 行為（但 inline 讓函式邊界模糊）
    → profiling 用「和 release 一樣的優化」+ 加 -g
        │
  → profiling 編譯：-O2 -g -fno-omit-frame-pointer
    （release 的優化 + debug symbols + frame pointer for call graph）
```

> **profiling 要用「release 的優化 + `-g` + frame pointer」——優化等級要和真實情況一樣，但加 debug symbols 讓 perf 看得懂**。profiling 的編譯選項影響準確性：(1) **`-g`（debug symbols）必須**——否則 perf report 只顯示一堆位址（看不懂是哪個函式/行），加 `-g` 才有函式名和行號；(2) **frame pointer**——`-g` 的 call graph 需要 frame pointer 來「unwind 堆疊」（追溯呼叫鏈），但現代編譯器預設 **omit frame pointer**（省一個暫存器給優化用），所以 profiling 時要加 **`-fno-omit-frame-pointer`**（call graph 才準確），或用 `perf record --call-graph dwarf`（用 DWARF 資訊 unwind，不需 frame pointer 但較慢、檔案大）；(3) **優化等級**——`-O0`（不優化）的 profiling **失真**（行為和 release 不同，你測的不是真實情況），要用**和 release 一樣的優化**（`-O2`/`-O3`）+ 加 `-g`（debug symbols 不影響優化，只是多了符號資訊）。所以 profiling 的標準編譯：**`-O2 -g -fno-omit-frame-pointer`**（release 優化 + debug 符號 + frame pointer）。這是個重要的細節——很多人 profiling 時用 `-O0 -g`（以為要 debug build），結果測的是「沒優化的程式」，和真實的 release 效能完全不同（優化會 inline、消除、重排，行為差很多）。**要 profile 真實效能，用真實的優化等級**。`-O2/-O3` 的一個小問題是 **inline**（函式被內聯，函式邊界模糊，perf 可能歸到呼叫者）——但這是真實情況（release 就是這樣），不影響「找熱點」（熱點還是那段程式碼）。理解 profiling 的編譯選項，你的 profiling 才準確（測真實效能、call graph 正確、看得懂符號）——這是常被忽略但影響結果的細節。

## 故意弄壞:profiling 找出意外的熱點

```bash
cd ~/perflab
# 一個「熱點不在你以為的地方」的程式
cat > surprise.c <<'EOF'
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
// 你以為慢的：複雜的計算
long complex_calc(long n) {
    long r = 0;
    for (int i = 0; i < 50; i++) r += (n * i) % 13;
    return r;
}
// 你沒注意的：字串格式化（其實很慢）
void format_result(char *buf, long val) {
    sprintf(buf, "Result: %ld (hex: %lx, oct: %lo)\n", val, val, val);  // sprintf 慢
}
int main() {
    char buf[100];
    long total = 0;
    for (long i = 0; i < 1000000L; i++) {
        total += complex_calc(i);
        format_result(buf, total);   // 每次都格式化（其實這裡慢）
    }
    printf("%s", buf);
    return 0;
}
EOF
gcc -O2 -g -fno-omit-frame-pointer surprise.c -o surprise

# 直覺：以為 complex_calc 慢（看起來計算多）
# perf 揭示真相
perf record -g ./surprise > /dev/null 2>&1
perf report --stdio 2>/dev/null | grep -E '%' | head -8
# Overhead  Symbol
#   60%     [sprintf 相關]        ← sprintf 才是熱點！（不是 complex_calc）
#   25%     complex_calc
#   ...
# → 真相：sprintf（字串格式化）才是熱點，不是你以為的 complex_calc
#   sprintf 要 parse 格式字串、轉多種進制 → 比簡單計算慢
#   優化方向：減少 sprintf 呼叫，或用更快的格式化
# → profiling 的價值：揪出「你沒想到的熱點」，避免優化錯地方
```

> **perf 揪出「sprintf 才是熱點」這種你沒想到的瓶頸——避免你優化錯地方（瞎優化以為慢的 complex_calc）**。這個例子展示 profiling 最重要的價值——**揪出意外的熱點**。直覺上你以為 `complex_calc`（看起來計算多）是瓶頸，可能花時間去優化它。但 perf 揭示真相——**`sprintf` 才是熱點**（吃 60% CPU）！為什麼？sprintf 要 parse 格式字串、處理多種進制轉換（十進位/十六進位/八進位）——這比簡單的算術迴圈慢得多（字串格式化是出名的慢操作）。如果你沒 profile 就「優化以為慢的 complex_calc」，你會**優化錯地方**（complex_calc 只占 25%，優化它效果有限），而真正的瓶頸 sprintf 沒碰到。**profiling 讓你優化對的地方**——資料顯示 sprintf 是熱點，優化方向是「減少 sprintf 呼叫（這個例子每次迴圈都格式化但只用最後一次，可以移到迴圈外）或用更快的格式化」。這是 perf_bench 反覆強調的——**永遠先 profile 再優化，不要憑直覺**。人對「哪裡慢」的直覺常常錯（像這個例子），profiling 給你資料驅動的真相。這呼應 observability_tools 課的 perf（找熱點）——但 perf_bench 更強調「用 profiling 指導優化決策」。對 compiler 工作，profiling 也指導「該優化什麼」——找出真實的熱點（不是猜測），針對它改善。記住這個教訓：**不 profile 就優化 = 瞎優化**，很可能優化錯地方、浪費時間、甚至沒效果。profiling 是優化的前提——先找到真正的熱點（資料），再針對性優化。

## 動手練習

1. 找熱點：對一個程式 `perf record -g` + `perf report`，找出熱點函式

2. annotate：用 `perf annotate` 看熱點函式的哪一行/哪條指令最花時間

3. call graph：用 `-g` 看熱點被誰呼叫（呼叫鏈）

4. 事件 profiling：用 `perf record -e cache-misses` 定位哪個函式 cache miss 最多

5. 跑「故意弄壞」：profile surprise.c，看 sprintf 是意外的熱點，理解「先 profile 再優化」

## 本章重點整理

- perf record（取樣）+ perf report（分析）找熱點函式；perf stat 給整體指標，perf record 定位到程式碼
- perf annotate 精確到「哪一行/哪條指令」最花時間；-g 的 call graph 顯示誰呼叫熱點
- 能 profile 任何 PMU 事件（-e cache-misses/branch-misses）定位「哪個函式造成 cache/branch miss」
- profiling 編譯：-O2（release 優化）-g（符號）-fno-omit-frame-pointer（call graph）——優化等級要和真實一樣
- 永遠先 profile 再優化——直覺常錯（surprise.c 的 sprintf 才是熱點），profiling 揪出意外的熱點避免優化錯地方

## 自我檢核

- [ ] 會用 perf record/report 找熱點函式
- [ ] 會用 perf annotate 看熱點的哪條指令最花時間
- [ ] 知道怎麼用不同事件 profile（cache-miss/branch-miss）
- [ ] 知道 profiling 的正確編譯選項（-O2 -g -fno-omit-frame-pointer）
- [ ] 理解「先 profile 再優化」，profiling 揪出意外熱點的價值

## 延伸閱讀

### 必讀

- **[perf Examples](https://www.brendangregg.com/perf.html)** — Brendan Gregg
  - **讀哪裡**：perf record/report/annotate 那幾節
  - **為什麼值得讀**：perf 最完整的資源，本章用法的權威

### 書籍

- **《Systems Performance》— Ch 13 (perf)** — Brendan Gregg
  - **讀哪幾章**：Ch 13（perf record/report/annotate）
  - **這本書的定位**：perf 的權威

### 文章

- **[perf 的 frame pointer 與 call graph](https://www.brendangregg.com/blog/2014-06-22/perf-cpu-sample.html)** — Brendan Gregg
  - **這篇說什麼**：profiling 的 call graph 怎麼運作（frame pointer vs DWARF）
  - **為什麼值得讀**：本章「編譯選項」的深入版

下一章看 llvm-mca——靜態分析工具，不用跑程式就能分析一段組合語言的 throughput 和瓶頸。這和 perf（動態）互補，對 compiler 工作特別有用（分析 compiler 產生的程式碼）。

→ [Ch 8 llvm-mca：靜態分析 throughput / bottleneck](./08-llvm-mca.md)
