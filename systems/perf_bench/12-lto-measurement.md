# Ch 12 — LTO 效果量測

> **目標**：理解 LTO（Link-Time Optimization，連結時優化）——它怎麼做「跨檔案的優化」（普通編譯只能優化單一檔案內）、為什麼有用（跨檔案 inline、全域分析）、它的代價（編譯時間、記憶體）、Full LTO vs ThinLTO 的取捨、以及怎麼量測它的真實效果（破除「LTO 總是有用」的迷思）。LTO 是現代編譯的標準工具，但效果要量測確認。

> **環境**：Linux，gcc/clang（LTO 內建）。hyperfine（Ch 0）。

## 為什麼需要 LTO？

普通編譯是**一個檔案一個檔案**編譯（每個 .c 編成 .o），最後連結起來。問題是：**每個檔案只能看到自己**——compiler 優化 `a.c` 時看不到 `b.c` 的函式，所以**跨檔案的優化做不了**（如把 `b.c` 的小函式 inline 到 `a.c`、跨檔案的常數傳播、移除沒用到的全域函式）。

**LTO（Link-Time Optimization）** 解決這個——它把優化延後到**連結時**，那時 compiler 能看到**整個程式**（所有檔案），做**跨檔案的優化**（跨檔案 inline、全域死碼移除、跨檔案的常數傳播）。這對由很多小檔案組成的程式（大部分真實程式）有用——能 inline 跨檔案的小函式、移除沒用的 code。LTO 是現代編譯的標準工具。但它有代價（編譯時間、記憶體），且效果**因程式而異**——這章講 LTO 的原理、取捨、和怎麼量測效果（不要假設「LTO 總是有用」）。

## 先建立直覺:跨檔案的視野

```
普通編譯 vs LTO：

  普通編譯（per-file）：
    a.c → a.o（只看 a.c 優化）
    b.c → b.o（只看 b.c 優化）
    連結：a.o + b.o → 程式（連結器只是「拼接」，不優化）
        │
    問題：a.c 呼叫 b.c 的小函式 small()
      compiler 優化 a.c 時看不到 small() 的內容
      → 不能 inline small()（跨檔案，看不到）
        │
  LTO（link-time）：
    a.c → a.o（含中間表示 IR，不是最終機器碼）
    b.c → b.o（含 IR）
    連結時：把所有 IR 合起來，看「整個程式」優化
      → 能 inline small()（跨檔案，現在看得到）
      → 能移除沒用到的全域函式（全域分析）
      → 能跨檔案常數傳播
        │
  → LTO 把優化延後到連結時，獲得「整個程式」的視野
    能做跨檔案優化（inline、死碼、常數傳播）
    代價：連結時要優化整個程式（慢、耗記憶體）
```

關鍵心智：普通編譯一個檔案一個檔案優化（看不到別的檔案），所以**跨檔案優化做不了**。LTO 把優化延後到**連結時**，那時能看到**整個程式**，做**跨檔案優化**（跨檔案 inline、全域死碼移除、常數傳播）。代價是連結時要優化整個程式（慢、耗記憶體）。

> LTO 的跨檔案 inline 用到 Ch 10 的 inline 概念。它和 PGO（Ch 11）可結合（LTO + PGO）。

## 用 LTO 並量測

```bash
cd ~/perflab
# 建一個多檔案的程式（LTO 才有意義）
cat > util.c <<'EOF'
int small_helper(int x) { return x * 2 + 1; }   // 小函式，跨檔案
long compute_util(long n) {
    long sum = 0;
    for (long i = 0; i < n; i++) sum += small_helper(i);
    return sum;
}
EOF
cat > main.c <<'EOF'
#include <stdio.h>
extern long compute_util(long n);
int main() { printf("%ld\n", compute_util(100000000L)); return 0; }
EOF

# 普通編譯（per-file，small_helper 不能跨檔案 inline）
gcc -O2 -c util.c -o util.o
gcc -O2 -c main.c -o main.o
gcc -O2 util.o main.o -o prog_nolto

# LTO 編譯（連結時優化，能跨檔案 inline）
gcc -O2 -flto -c util.c -o util_lto.o
gcc -O2 -flto -c main.c -o main_lto.o
gcc -O2 -flto util_lto.o main_lto.o -o prog_lto

# 比較
hyperfine './prog_nolto' './prog_lto'
# './prog_lto' ran 1.X faster   ← LTO 提升（跨檔案 inline 等）
# → LTO 能 inline small_helper（跨檔案），普通編譯不能

# 看 code 的差別（LTO 可能 inline 後 code 不同）
size prog_nolto prog_lto

# 編譯時間的代價
echo "=== 普通編譯時間 ==="
time (gcc -O2 -c util.c -o u.o && gcc -O2 -c main.c -o m.o && gcc -O2 u.o m.o -o p1) 2>&1 | grep real
echo "=== LTO 編譯時間 ==="
time (gcc -O2 -flto -c util.c -o ul.o && gcc -O2 -flto -c main.c -o ml.o && gcc -O2 -flto ul.o ml.o -o p2) 2>&1 | grep real
# LTO 的連結時間更長（要優化整個程式）
```

> **LTO 能做跨檔案 inline 和全域死碼移除——對多檔案程式有用，但代價是連結時間和記憶體**。LTO 用法：編譯時加 **`-flto`**（產生含中間表示 IR 的 .o，不是最終機器碼），連結時也加 `-flto`（這時做跨檔案優化）。效果：**跨檔案 inline**（把別的檔案的小函式 inline 進來，省呼叫開銷）、**全域死碼移除**（移除整個程式沒用到的函式）、**跨檔案常數傳播**。對由很多小檔案組成的程式（大部分真實程式），LTO 能提升效能（特別是有很多跨檔案小函式呼叫的）。但 **LTO 有代價**：(1) **連結時間大幅增加**（連結時要優化整個程式，對大型程式可能慢很多——這是 LTO 最大的代價，影響開發迭代速度）；(2) **記憶體**（連結時要把整個程式的 IR 載入優化，大型程式耗大量記憶體）。所以 LTO 是 trade-off——**獲得跨檔案優化（效能）vs 編譯時間和記憶體**。對 production release（重視最終效能、編譯時間可接受）LTO 常值得；對快速開發迭代（重視編譯速度）可能不值得。這個 trade-off 要根據專案權衡——用 LTO 前量測「效能提升多少 vs 編譯時間增加多少」，判斷是否值得。這也是 perf_bench 的核心——**不假設「LTO 總是有用」，要量測效果和代價**（下節破除這個迷思）。

## Full LTO vs ThinLTO

```
Full LTO vs ThinLTO（解決 LTO 的編譯時間問題）：

  Full LTO（傳統）：
    連結時把「整個程式」的 IR 合起來一次優化
    優點：最徹底的跨程式優化
    缺點：慢（單執行緒優化整個程式）、耗記憶體、不可平行
        │
  ThinLTO（LLVM，現代）：
    連結時做「輕量的全域分析」+「平行的局部優化」
    1. 快速的全域分析（哪些函式該跨模組 inline）
    2. 平行地優化各模組（多執行緒，可分散式）
    優點：快很多（可平行）、可擴展到大型程式
    缺點：略不如 Full LTO 徹底（但接近，且快很多）
        │
  → ThinLTO 是現代大型程式的選擇
    Full LTO 的效能 + 接近普通編譯的可擴展性
    Chrome、LLVM 自己都用 ThinLTO（大型程式）
        │
  用法：
    clang -flto=thin（ThinLTO）
    clang -flto=full 或 -flto（Full LTO）
```

> **ThinLTO（平行、可擴展）解決了 Full LTO「慢」的問題——是現代大型程式的選擇，效能接近 Full LTO 但快很多**。LTO 的「連結時間慢」問題（特別是大型程式）促成了 **ThinLTO**（LLVM 開發）。**Full LTO**（傳統）連結時把整個程式的 IR 合起來**一次優化**——最徹底，但**慢**（單執行緒優化整個程式）、耗記憶體、不可平行。**ThinLTO** 用不同的方法——(1) 連結時做**輕量的全域分析**（只決定「哪些函式該跨模組 inline」，不做完整的全域優化）；(2) 然後**平行地優化各模組**（多執行緒，甚至可分散式）。這讓 ThinLTO **快很多**（可平行、可擴展到大型程式）、效能**接近 Full LTO**（雖然略不如最徹底的 Full LTO，但接近，且快太多了）。所以 **ThinLTO 是現代大型程式的選擇**——Chrome、LLVM 自己都用 ThinLTO（它們的 code 太大，Full LTO 慢到不實際，ThinLTO 給了「LTO 的效能 + 可接受的編譯時間」）。用法：`clang -flto=thin`（ThinLTO）vs `-flto=full`/`-flto`（Full LTO）。對 compiler 工作，理解 Full vs Thin LTO 的取捨很重要——大型程式幾乎都該用 ThinLTO（Full LTO 不可擴展），小程式用 Full LTO 也行（編譯時間可接受）。ThinLTO 是「讓 LTO 可擴展」的工程創新——它犧牲一點點徹底性換取大幅的可擴展性，這個 trade-off 對大型程式是對的。理解這個，你知道部署 LTO 時選哪個（大型程式 ThinLTO、小程式都可以）。這也是 LLVM 對 compiler 領域的重要貢獻——讓 LTO 從「小程式的奢侈品」變成「大型程式也能用的標準工具」。

## 故意弄壞:LTO 不一定有用的案例

```bash
cd ~/perflab
# LTO 對「單一檔案」或「沒有跨檔案優化機會」的程式沒幫助
# 案例 1：單一檔案程式（LTO 沒有跨檔案優化的對象）
gcc -O2 demo.c -o demo_single_nolto
gcc -O2 -flto demo.c -o demo_single_lto
hyperfine './demo_single_nolto' './demo_single_lto'
# 幾乎沒差！（單一檔案，LTO 沒有跨檔案優化可做）
# → LTO 對單一檔案程式沒幫助（沒有跨檔案的對象）

# 案例 2：已經沒有跨檔案小函式的程式
# 如果程式的熱點不涉及跨檔案呼叫，LTO 幫不上
# → LTO 的效果取決於「有沒有跨檔案優化的機會」

# 量測 LTO 的代價（編譯時間 vs 效能提升）
echo "=== 評估 LTO 是否值得 ==="
echo "1. 效能提升: 用 hyperfine 比較 LTO vs 非 LTO"
echo "2. 編譯時間代價: 比較編譯時間"
echo "3. 判斷: 提升的效能 vs 增加的編譯時間，值得嗎？"
# → 不要假設「LTO 總是有用」
#   單一檔案、沒跨檔案優化機會 → LTO 沒幫助（但增加編譯時間）
#   多檔案、很多跨檔案小函式 → LTO 有幫助（值得）
#   要量測確認（效能提升 vs 編譯代價）

# 對 compiler 工作的啟示：
# LTO 是「有條件有用」的優化（取決於程式結構）
# 不是「開了就一定快」—— 要量測、理解為什麼有用/沒用
```

> **LTO 對「單一檔案或沒跨檔案優化機會」的程式沒幫助——破除「LTO 總是有用」的迷思，要量測效能提升 vs 編譯代價**。這個例子破除 LTO 的迷思——「LTO 總是有用」是錯的。**LTO 的效果取決於「有沒有跨檔案優化的機會」**：(1) **單一檔案程式** → LTO **沒幫助**（沒有跨檔案優化的對象——所有 code 在一個檔案，普通編譯就能看到全部）；(2) **熱點不涉及跨檔案呼叫的程式** → LTO 也幫不上（沒有可優化的跨檔案呼叫）；(3) **多檔案、很多跨檔案小函式的程式** → LTO **有幫助**（能跨檔案 inline、全域分析）。所以**不要假設「LTO 總是有用」**——它是「**有條件有用**」的優化（取決於程式結構）。判斷 LTO 是否值得：量測**效能提升**（hyperfine 比較 LTO vs 非 LTO）vs **編譯代價**（編譯時間增加多少），權衡是否值得。對 compiler 工作的啟示——LTO 不是「開了就一定快」，要根據程式結構判斷（有跨檔案優化機會才有用），且要量測確認（提升 vs 代價）。這呼應 perf_bench 的核心信條——**破除迷思（「LTO/O3/向量化總是有用」），用實測確認**。每個優化都是「在某些條件下有用」，不是「無條件的好」。理解「LTO 何時有用、何時沒用、為什麼」，你才能正確地用它（對的程式用、量測確認），而非盲目套用。這是專業 vs 業餘的差別——業餘的「開所有優化」，專業的「理解每個優化的適用條件、量測確認、針對 workload 選擇」。Part 4 的 LTO 章再次強化了這個信條——compiler 優化是 workload/structure-specific 的，要量測不要假設。

## 動手練習

1. LTO 多檔案：建一個多檔案程式（有跨檔案小函式），比較 LTO vs 非 LTO（hyperfine）

2. LTO 代價：量測 LTO 的編譯時間 vs 非 LTO，理解 trade-off

3. Full vs Thin：用 `-flto=full` vs `-flto=thin`（clang），比較編譯時間和效能

4. LTO 沒用的案例：對單一檔案程式用 LTO，看沒有提升（破除「總是有用」）

5. 判斷 LTO：對一個程式，量測「效能提升 vs 編譯代價」，判斷 LTO 是否值得

## 本章重點整理

- LTO 把優化延後到連結時，獲得「整個程式」視野，做跨檔案優化（inline、全域死碼、常數傳播）
- LTO 用 `-flto`（編譯和連結都加）；對多檔案、有跨檔案小函式的程式有用
- 代價：連結時間大幅增加、耗記憶體——是 trade-off（效能 vs 編譯時間）
- Full LTO（徹底但慢、不可平行）vs ThinLTO（平行、可擴展、接近 Full 效能）——大型程式用 ThinLTO
- LTO 不是「總是有用」——單一檔案/沒跨檔案優化機會則沒幫助；要量測效能提升 vs 編譯代價

## 自我檢核

- [ ] 理解 LTO 怎麼做跨檔案優化（連結時的整個程式視野）
- [ ] 知道 LTO 的代價（編譯時間、記憶體）和 trade-off
- [ ] 知道 Full LTO vs ThinLTO 的差別，大型程式為什麼用 ThinLTO
- [ ] 知道 LTO 何時有用、何時沒用（取決於跨檔案優化機會）
- [ ] 內化「不假設 LTO 總是有用，要量測」的信條

## 延伸閱讀

### 文章

- **[ThinLTO](https://blog.llvm.org/2016/06/thinlto-scalable-and-incremental-lto.html)** — LLVM Blog
  - **核心貢獻**：ThinLTO 的設計（可擴展的 LTO）
  - **為什麼值得讀**：ThinLTO 的權威

- **[GCC LTO 文件](https://gcc.gnu.org/onlinedocs/gccint/LTO-Overview.html)** — GCC
  - **讀哪裡**：LTO 的運作和選項
  - **為什麼值得讀**：LTO 機制的權威

### 論文

- **[ThinLTO paper](https://research.google/pubs/pub47584/)** — Google（CGO 2017）
  - **為什麼值得讀**：ThinLTO 的學術設計

下一章看 vectorization——SIMD/向量化怎麼讓一條指令處理多個資料，以及怎麼讀 compiler 的 vectorization report（為什麼某個迴圈沒被向量化）。這對 RISC-V 的 RVV（向量擴展）特別相關。

→ [Ch 13 Vectorization report 閱讀](./13-vectorization-reports.md)
