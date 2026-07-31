# Ch 12 — perf 基礎

> **目標**：掌握 perf——Linux 的效能分析瑞士刀，用「取樣」（sampling）找出程式的時間花在哪個函式（CPU profiling）、火焰圖（flame graph）的解讀、perf stat 看硬體事件（cache miss/分支預測）、以及為什麼 perf 的開銷比 strace 小。從「程式做什麼」（strace）進到「時間花在哪」（perf）。這是 debug「為什麼慢」的核心工具。

> **環境**：Linux，perf（linux-tools，Ch 0）。需要 perf_event_paranoid 設定（Ch 0）或 sudo。

## 為什麼需要 perf？

前面的工具看「程式做什麼」（strace 看 syscall、ltrace 看 library 呼叫）。但「為什麼慢」常常不是 syscall 的問題，而是「**CPU 時間花在哪個函式**」——某個熱點函式被呼叫太多次、某個迴圈太慢、某段計算太重。strace 看不到這個（它看 syscall，不看 CPU 在哪個函式裡）。

**perf** 是效能分析的核心——它用「取樣」（sampling）定期記錄「CPU 現在在執行哪個函式」，統計出「時間花在哪」。這回答了「為什麼慢」最重要的問題——找出**熱點**（hotspot，吃最多 CPU 的函式）。理解 perf 和火焰圖，你能從「程式跑很慢」定位到「是這個函式吃了 80% 的 CPU」，然後針對性優化。這是效能優化的入口，也是 sysstat（Ch 10，系統層）之後的「process 內部」效能分析。

## 先建立直覺:取樣 vs 攔截

```
perf 的取樣（sampling）vs strace 的攔截（tracing）：

  strace（攔截）：攔截「每一個」syscall
    完整（不漏任何 syscall）但開銷大（每個都暫停）
    適合：看「做了什麼」（行為）
        │
  perf（取樣）：定期「拍快照」看 CPU 在哪
    如每秒 1000 次：「現在在執行哪個函式？」記下來
    統計：哪個函式出現最多次 = 吃最多 CPU（熱點）
    不完整（取樣，可能漏）但開銷小（不攔截每個）
    適合：看「時間花在哪」（效能）
        │
  類比：
    strace = 記錄你說的每一句話（完整但累）
    perf = 每隔幾秒拍一張照（看你大部分時間在做什麼）
        │
  → perf 用「取樣統計」找熱點，開銷小
    適合「為什麼慢」（時間花在哪個函式）
```

關鍵心智：perf 用「**取樣**」（定期拍快照看 CPU 在執行哪個函式）統計時間花在哪，而非 strace 的「攔截每個 syscall」。取樣不完整但開銷小，適合找**熱點**（吃最多 CPU 的函式）。類比：strace 記錄每句話（完整但累），perf 每隔幾秒拍照（看你大部分時間在做什麼）。

> perf 和 strace 互補——strace 看「做了什麼 syscall」（行為），perf 看「CPU 時間花在哪個函式」（效能）。如果對 strace 的攔截機制不熟，回看 [Ch 5](./05-strace-complete-guide.md)。perf 是 Ch 10（sysstat 系統層）之後的「process 內部」效能。

## perf 的核心命令

```bash
cd ~/obslab
# 一個有「熱點」的程式（某函式吃很多 CPU）
cat > slow.c <<'EOF'
#include <stdio.h>
long slow_function() {        // 吃 CPU 的熱點
    long sum = 0;
    for (long i = 0; i < 2000000000L; i++) sum += i % 7;
    return sum;
}
long fast_function() { return 42; }
int main() {
    long r = slow_function() + fast_function();
    printf("%ld\n", r);
    return 0;
}
EOF
gcc -g -O2 slow.c -o slow

# === perf stat：整體效能統計（硬體事件）===
perf stat ./slow
# task-clock, context-switches, cycles, instructions,
# cache-references, cache-misses, branches, branch-misses
# → 程式的硬體層效能（IPC、cache miss 率、分支預測）

# === perf record + report：CPU profiling（找熱點）===
perf record -g ./slow            # -g 記錄呼叫堆疊
perf report                      # 看哪個函式吃最多 CPU
# Overhead  Command  Symbol
#   98.50%  slow     slow_function    ← 98% 的 CPU 在 slow_function！
#    1.00%  slow     ...
# → 立刻看出熱點是 slow_function

# === perf top：即時的 perf（像 top 但看函式）===
perf top                         # 即時看「系統現在哪個函式吃 CPU」
perf top -p <PID>                # 特定 process

# === 取樣頻率 ===
perf record -F 999 -g ./slow     # -F 999：每秒取樣 999 次
```

> **`perf record -g` + `perf report` 是找 CPU 熱點的核心——它直接告訴你「哪個函式吃最多 CPU」**。perf 的核心工作流：**`perf record -g`**（記錄取樣資料，`-g` 記錄呼叫堆疊讓你知道「誰呼叫了熱點函式」）→ **`perf report`**（分析，按「Overhead」排序顯示哪個函式吃最多 CPU）。上面的例子，perf report 直接顯示 `98.50% slow_function`——立刻找出熱點（98% 的 CPU 都在這個函式）。這是 debug「為什麼慢」最直接的方法——不用猜哪裡慢，perf 統計出來給你。**`perf stat`**（整體統計）看硬體層的效能事件：cycles（CPU 週期）、instructions（指令數，IPC = instructions/cycles 是效率指標）、cache-misses（快取失誤——高表示記憶體存取模式差，是效能殺手）、branch-misses（分支預測失誤）。這些硬體事件揭示「為什麼這個函式慢」（cache miss 多 = 記憶體存取慢、branch miss 多 = 分支難預測）。**`perf top`**（即時版，像 top 但看函式）看「系統現在哪個函式吃 CPU」——適合「系統突然變慢，現在哪個函式在燒 CPU」。記住核心：**`perf record -g ./prog` + `perf report`** 找熱點，這是效能優化的第一步（找出 80% 時間花在哪，針對性優化）。`-F`（取樣頻率，預設約 1000Hz，更高更精確但開銷大）。

## 火焰圖:視覺化熱點

```bash
# 火焰圖（flame graph）：把 perf 的呼叫堆疊視覺化
# 安裝 FlameGraph 工具
# git clone https://github.com/brendangregg/FlameGraph

# 產生火焰圖
perf record -g ./slow
perf script | ./FlameGraph/stackcollapse-perf.pl | ./FlameGraph/flamegraph.pl > flame.svg
# 用瀏覽器開 flame.svg

# 或用 perf 內建（新版）
# perf record -g ./slow && perf report --stdio
```

```
火焰圖怎麼讀（Brendan Gregg 的發明）：

  ┌─────────────────────────────────────┐
  │                                     │
  │       slow_function (98%)           │ ← 寬度 = 佔 CPU 比例
  │  ┌──────────────────────────────┐  │
  │  │  main (calls slow_function)  │  │ ← Y 軸 = 呼叫堆疊深度
  │  └──────────────────────────────┘  │
  └─────────────────────────────────────┘
        │
  讀法：
    X 軸（寬度）：函式佔 CPU 的比例（越寬越吃 CPU）★
    Y 軸（高度）：呼叫堆疊深度（誰呼叫誰）
    顏色：通常隨機（不代表意義）
        │
  → 找「寬的塔」（佔 CPU 多的）= 熱點
    塔頂的寬函式 = 實際在執行的熱點
    一眼看出「時間花在哪、誰呼叫的」
```

> **火焰圖把 perf 的取樣視覺化——「寬的塔」就是熱點，一眼看出時間花在哪、誰呼叫的**。火焰圖（flame graph，Brendan Gregg 發明）是 perf 資料的視覺化——把「呼叫堆疊取樣」畫成圖。讀法：**X 軸（寬度）= 函式佔 CPU 的比例**（越寬越吃 CPU，這是關鍵——找最寬的）；**Y 軸（高度）= 呼叫堆疊深度**（下面的函式呼叫上面的，塔頂是實際在執行的）；顏色通常隨機（不代表意義）。讀火焰圖就是**找「寬的塔」**——寬度大的函式吃最多 CPU，塔頂的寬函式是實際的熱點。這比 `perf report` 的文字列表直觀——你一眼看出「整個程式的時間分布」（哪些函式寬=吃 CPU，呼叫關係怎樣）。火焰圖對複雜程式特別有用（很多函式、深的呼叫堆疊）——文字列表難看出全貌，火焰圖一圖看盡。產生方法：`perf record -g` + Brendan Gregg 的 FlameGraph 腳本（stackcollapse + flamegraph）→ SVG（互動式，可點擊放大）。火焰圖是效能分析的標準視覺化——學會讀它，你看一個程式的效能瓶頸就像看地圖。它也用於其他取樣資料（不只 CPU，也可以是記憶體分配、off-CPU 等）。記住：**寬 = 吃 CPU = 熱點，找最寬的塔去優化**。

## perf 為什麼開銷小

```
perf 為什麼比 strace 開銷小（理解觀察的代價，Ch 3 的 Heisenbug）：

  strace：用 ptrace 攔截「每個」syscall
    每個 syscall 都暫停 tracee → tracer 處理 → 繼續
    開銷大（程式變慢數倍，影響時序）
        │
  perf：用「硬體效能計數器」+ 取樣
    CPU 有專門的效能計數器硬體（PMU）
    perf 設定「每 N 個事件/N 微秒中斷一次」記錄當前 PC（程式計數器）
    不攔截每個操作，只「定期取樣」→ 開銷小（通常 <5%）
        │
  → perf 的低開銷讓它能用於「生產環境」
    （strace 開銷大，不適合 trace 生產服務）
    perf 用硬體 + 取樣，影響小，能 profile 真實負載
        │
  代價：取樣不完整（可能漏掉短暫的事件）
    但對「找熱點」（時間花在哪）夠好（熱點會被多次取樣到）
```

> **perf 用「硬體效能計數器 + 取樣」所以開銷小（<5%），能用於生產環境——這是它和 strace 的關鍵差別**。回到 Ch 3 的「觀察的代價」（Heisenbug）——strace 用 ptrace 攔截**每個** syscall（每個都暫停），開銷大（程式慢數倍，影響時序），不適合 trace 生產服務。**perf 不同**——它用 CPU 的**硬體效能計數器（PMU）** + **取樣**：設定「每 N 個 CPU 週期/N 微秒中斷一次」，中斷時記錄「當前在執行哪個函式（PC，程式計數器）」，不攔截每個操作，只定期拍快照。所以開銷小（通常 <5%）——這讓 perf 能**用於生產環境**（profile 真實的線上負載，找出生產環境的熱點，而 strace 開銷太大不敢用在生產）。代價是**取樣不完整**（可能漏掉短暫的事件——只執行一次的函式可能沒被取樣到），但對「找熱點」（時間花在哪）夠好——因為熱點會被**多次取樣到**（吃 CPU 多 = 取樣時常在執行 = 統計上出現多次）。這個「低開銷取樣」是 perf 的設計哲學——犧牲完整性換低開銷，適合效能分析（要找的是「大部分時間花哪」，不是「每個細節」）。理解這個，你知道何時用 perf（找熱點、生產環境、效能）vs strace（看完整行為、debug 邏輯、開發環境）。它們是不同取捨的工具——perf 取樣低開銷找熱點、strace 完整攔截看行為。

## 故意弄壞:用 perf 找熱點

```bash
# 用 perf 找出「為什麼慢」的熱點
cd ~/obslab
cat > app.c <<'EOF'
#include <stdio.h>
#include <string.h>
// 故意低效的字串處理
int count_chars(const char *s, char c) {
    int count = 0;
    for (int i = 0; i < strlen(s); i++) {   // bug: 每次迴圈都 strlen！O(n²)
        if (s[i] == c) count++;
    }
    return count;
}
int main() {
    char big[100000];
    memset(big, 'a', sizeof(big) - 1);
    big[99999] = '\0';
    big[50000] = 'b';
    for (int i = 0; i < 100; i++) {
        count_chars(big, 'b');   // 呼叫 100 次（每次 O(n²)）
    }
    printf("done\n");
    return 0;
}
EOF
gcc -g -O0 app.c -o app

# 程式很慢（O(n²) 的 strlen in loop）
time ./app    # 花好幾秒

# 用 perf 找熱點
perf record -g ./app
perf report --stdio | head -15
# Overhead  Symbol
#   ~80%    strlen           ← strlen 吃最多 CPU！（每次迴圈都呼叫）
#   ~15%    count_chars
# → perf 揭示：strlen 是熱點（因為在迴圈條件裡每次都呼叫）
#   bug：for (i=0; i<strlen(s); i++) 每次都 O(n) 的 strlen → 整體 O(n²)
#   修法：strlen 提到迴圈外 → int len = strlen(s); for(i=0;i<len;i++)

# 驗證修法（把 strlen 提出迴圈）後再 perf，熱點消失，快很多
```

> **perf 揪出「strlen in loop」這種 O(n²) 的經典效能 bug——它顯示「時間花在哪個函式」，直接指向問題**。這個 bug 很經典——`for (i=0; i<strlen(s); i++)` 在**迴圈條件裡每次都呼叫 strlen**（O(n)），所以整體變成 O(n²)（n 次迴圈 × 每次 O(n) 的 strlen）。對大字串這慢到爆。讀原始碼**可能看不出**（`strlen(s)` 看起來沒問題）。但 perf 直接顯示 **`80% strlen`**——揪出「時間都花在 strlen」，立刻意識到「strlen 被呼叫太多次」，找到 bug（迴圈條件裡的 strlen）。**修法**：把 strlen 提到迴圈外（`int len = strlen(s); for(i=0;i<len;i++)`）—— O(n²) 變 O(n)，快幾百倍。這展示了 perf 的核心價值——**它把「程式慢」這個模糊問題，定位到「具體哪個函式吃 CPU」**，然後你針對那個函式找原因（為什麼它吃這麼多——被呼叫太多次？演算法差？）。這是效能優化的標準流程：**perf 找熱點 → 分析為什麼那個函式吃 CPU → 優化它**。不要「猜哪裡慢然後瞎優化」（常常優化錯地方）——用 perf 量測，優化真正的熱點（80/20 法則——20% 的程式碼吃 80% 的時間，優化那 20%）。perf 是「資料驅動的效能優化」的核心工具，配合火焰圖視覺化，讓你的優化有的放矢。

## 動手練習

1. perf stat：對一個程式 `perf stat`，看 cycles/instructions/cache-misses，理解硬體事件

2. perf record/report：對 slow.c 用 `perf record -g` + `perf report`，找出熱點 slow_function

3. 火焰圖：產生一個程式的火焰圖（FlameGraph 工具），學讀「寬的塔=熱點」

4. perf top：跑一個吃 CPU 的程式，用 `perf top` 即時看哪個函式吃 CPU

5. 跑「故意弄壞」：用 perf 找出 app.c 的 strlen-in-loop 熱點，修正後驗證變快

## 本章重點整理

- perf 用「取樣」（定期拍快照看 CPU 在哪個函式）找熱點，vs strace 攔截每個 syscall——適合「為什麼慢」
- 核心命令：`perf record -g` + `perf report`（找熱點）、`perf stat`（硬體事件 cache-miss/IPC）、`perf top`（即時）
- 火焰圖視覺化熱點：X 軸寬度=佔 CPU 比例（找寬的塔）、Y 軸=呼叫堆疊深度
- perf 用硬體計數器+取樣，開銷小（<5%）能用於生產環境；strace 攔截開銷大；代價是取樣不完整
- 效能優化流程：perf 找熱點 → 分析為什麼吃 CPU → 優化（資料驅動，優化真正的 20%）

## 自我檢核

- [ ] 理解 perf 的取樣 vs strace 的攔截，以及各自適合什麼
- [ ] 會用 `perf record -g` + `perf report` 找熱點
- [ ] 會讀火焰圖（寬=熱點），知道怎麼產生
- [ ] 知道 perf 為什麼開銷小（硬體計數器+取樣），能用於生產
- [ ] 能用 perf 走「找熱點 → 優化」的效能分析流程

## 延伸閱讀

### 必讀資源

- **[perf Examples](https://www.brendangregg.com/perf.html)** — Brendan Gregg
  - **這篇說什麼**：perf 的所有用法和範例（最完整的 perf 資源）
  - **讀哪裡**：整頁（放手邊查）
  - **為什麼值得讀**：perf 的權威資源，本章用法的完整版

- **[Flame Graphs](https://www.brendangregg.com/flamegraphs.html)** — Brendan Gregg
  - **核心貢獻**：火焰圖的發明者解釋怎麼產生和讀
  - **讀哪裡**：CPU flame graphs 那節
  - **為什麼值得讀**：火焰圖的原始來源

### 書籍

- **《Systems Performance》— Ch 13 (perf)** — Brendan Gregg
  - **讀哪幾章**：Ch 13（perf 完整）、Ch 5（取樣 vs tracing 方法論）
  - **這本書的定位**：perf 和效能分析的權威
  - **前提**：本章 + Ch 10

### 官方文件

- **[perf wiki](https://perf.wiki.kernel.org/)** — Linux kernel
  - **讀哪裡**：tutorial
  - **為什麼值得讀**：perf 的官方文件

下一章看 ftrace——kernel 內建的函式 tracer，能 trace kernel 內部的函式呼叫。從「使用者空間的效能」（perf）進到「kernel 內部的觀察」。

→ [Ch 13 ftrace 與 tracefs](./13-ftrace-and-tracefs.md)
