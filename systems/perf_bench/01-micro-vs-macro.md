# Ch 1 — Micro vs macro benchmark：選哪個、怎麼避免錯誤

> **目標**：區分 micro-benchmark（測單個函式/迴圈）和 macro-benchmark（測完整 workload）的差別、各自的適用情境和陷阱、以及怎麼避免「測了半天結果沒意義」的常見錯誤（dead code elimination、warmup、不真實的輸入）。這是做任何效能測量前要先想清楚的——選錯 benchmark 類型或踩到陷阱，你的數字毫無意義。

> **環境**：Linux，gcc/clang，hyperfine（Ch 0）。

## 為什麼先談 benchmark 哲學？

新手做效能測量常犯的錯不是「工具不會用」，而是「**測了錯的東西**」——寫了個 micro-benchmark 測某函式，但編譯器把整個函式優化掉了（dead code elimination），測到的是空迴圈；或測一個 workload 但輸入不真實，結果和生產環境完全不同。這些錯誤讓你「測了半天結果沒意義」，甚至得出錯誤結論去優化錯的地方。

所以做測量前要先想清楚：我要測什麼（一個函式還是完整 workload）？用哪種 benchmark？有沒有踩到讓結果失真的陷阱？這章講 micro vs macro 的選擇和各自的陷阱——這是效能工作的「方法論基礎」，比工具更重要。選對 benchmark、避開陷阱，後面的測量才有意義。

## 先建立直覺:顯微鏡 vs 全景照

```
micro-benchmark vs macro-benchmark：

  micro-benchmark（顯微鏡）：測「一小段」
    例：測 memcpy、測某個 sort 函式、測一個 hot loop
    優點：聚焦、快、易控制變因、能精確比較某個改動
    缺點：脫離真實情境（在真實程式裡，cache 狀態/分支歷史不同）
        │
  macro-benchmark（全景照）：測「完整 workload」
    例：跑整個 SPEC、跑真實的應用 workload
    優點：反映真實效能（真實的 cache/分支/記憶體行為）
    缺點：慢、多變因、難定位「是哪裡的改動造成差異」
        │
  → 選哪個看目的：
    優化某個函式/驗證某個 compiler 改動 → micro（聚焦）
    評估整體效能/真實情境 → macro（真實）
    常常兩者都要：micro 找方向、macro 驗證真實效果
        │
  最危險的錯誤：micro-benchmark 的結果「不代表真實情境」
    （函式在隔離測 vs 在真實程式裡，行為可能差很多）
```

關鍵心智：**micro-benchmark**（顯微鏡）測一小段（聚焦、易控制，但脫離真實情境）；**macro-benchmark**（全景照）測完整 workload（真實，但慢、難定位）。選哪個看目的——優化某函式用 micro、評估整體用 macro，常常兩者都要。最危險的是 micro 的結果「不代表真實情境」（隔離測 vs 真實程式裡行為差很多）。

> 這章是 perf_bench 的方法論基礎，比工具更重要。後面的 SPEC（Ch 2）是 macro、Coremark（Ch 3）介於兩者、統計（Ch 4）處理測量的可信度。

## micro-benchmark 的陷阱

micro-benchmark 看似簡單但陷阱很多，這些陷阱讓無數人測出無意義的數字：

```c
// 陷阱 1：dead code elimination（編譯器優化掉你要測的東西）
// 錯誤的 micro-benchmark
#include <stdio.h>
#include <time.h>
long compute(long n) { return n * n % 13; }
int main() {
    clock_t start = clock();
    for (long i = 0; i < 100000000L; i++) {
        compute(i);              // 結果沒用 → 編譯器可能整個刪掉！
    }
    clock_t end = clock();
    printf("Time: %f\n", (double)(end - start) / CLOCKS_PER_SEC);
    // -O2 編譯：測到的時間接近 0（compute 被優化掉了）
    return 0;
}
```

```c
// 正確：用 volatile 或累積結果，防止優化掉
int main() {
    volatile long sink;          // volatile 防止優化
    clock_t start = clock();
    for (long i = 0; i < 100000000L; i++) {
        sink = compute(i);       // 結果被「用」了 → 不會被優化掉
    }
    clock_t end = clock();
    printf("Time: %f, sink=%ld\n", (double)(end - start) / CLOCKS_PER_SEC, sink);
    return 0;
}
```

```
micro-benchmark 的常見陷阱：

  1. Dead code elimination：
     編譯器發現「結果沒用」→ 刪掉你要測的程式碼
     → 測到空迴圈（時間接近 0）
     解法：volatile、累積結果、用 DoNotOptimize（benchmark 框架）
        │
  2. Constant folding：
     編譯器發現輸入是常數 → 編譯時就算好
     compute(5) 變成編譯時的常數 → 測到的是「載入常數」
     解法：用 runtime 的變數當輸入（編譯器不知道值）
        │
  3. 沒有 warmup：
     第一次跑：cache 冷、分支預測器沒學習、頻率還沒升
     → 第一次的數字偏慢，要先 warmup
        │
  4. 太小的 workload：
     迴圈太短 → 測到的主要是計時開銷和雜訊，不是程式
     → workload 要夠大（至少跑幾毫秒，最好幾十毫秒）
        │
  5. 不真實的輸入/狀態：
     測 sort 用已排序的陣列、測 cache 用剛好放進 cache 的資料
     → 真實情境的行為完全不同
```

> **micro-benchmark 最大的陷阱是「編譯器優化掉你要測的東西」（dead code elimination / constant folding）——測到的是空迴圈或常數載入**。寫 micro-benchmark 最容易踩的坑是**編譯器太聰明**：(1) **Dead code elimination**——如果你算了某個值但**沒用它**，編譯器（特別是 -O2/-O3）會發現「這結果沒用」直接**刪掉整段程式碼**，你測到的是空迴圈（時間接近 0，你以為「這函式超快」其實根本沒跑）；(2) **Constant folding**——如果輸入是**編譯時已知的常數**，編譯器會在編譯時就算好結果，你測到的是「載入一個常數」不是「計算」。**解法**：用 `volatile`（防止優化掉）、累積/使用結果（讓編譯器知道結果有用）、用 runtime 的變數當輸入（編譯器不知道值，不能 constant fold）。專業的 benchmark 框架（Google Benchmark）有 `DoNotOptimize()` / `ClobberMemory()` 來防這些。其他陷阱：**沒 warmup**（第一次跑 cache 冷、分支預測沒學習、頻率沒升——要先跑幾次暖機）、**workload 太小**（測到的主要是計時開銷和雜訊）、**不真實的輸入**（測 sort 用已排序陣列、測 cache 用剛好放得下的資料——真實情境行為完全不同）。這些陷阱讓無數 micro-benchmark 測出無意義的數字。**寫 micro-benchmark 一定要驗證「編譯器真的有跑你要測的程式碼」**（看組合語言、看時間合不合理、用 volatile）。這是 perf_bench 的重要紀律——micro-benchmark 容易測錯，要小心驗證。

## 用框架避免陷阱:Google Benchmark

```cpp
// 用 Google Benchmark（專業的 micro-benchmark 框架）避免陷阱
// 它處理 warmup、自動決定迴圈次數、防 dead code elimination
#include <benchmark/benchmark.h>

static long compute(long n) { return n * n % 13; }

static void BM_Compute(benchmark::State& state) {
    long n = state.range(0);          // runtime 輸入（防 constant fold）
    for (auto _ : state) {            // 框架管理迴圈（warmup、次數）
        long result = compute(n);
        benchmark::DoNotOptimize(result);  // 防 dead code elimination
    }
}
BENCHMARK(BM_Compute)->Arg(12345);    // 用 12345 當輸入
BENCHMARK_MAIN();

// 編譯：g++ -O2 bench.cpp -lbenchmark -lpthread -o bench
// 跑：./bench
// BM_Compute/12345    2.5 ns    ← 精確、可信的單次呼叫時間
```

```
為什麼用框架（vs 自己寫計時）：

  框架幫你處理（你自己寫容易錯的）：
    - warmup（先跑幾次暖機）
    - 自動決定迴圈次數（跑夠久才準）
    - 防 dead code elimination（DoNotOptimize）
    - 計時的精確性（高解析度計時器）
    - 統計（多次測量、變異）
        │
  → micro-benchmark 用框架（Google Benchmark/nanobench）
    別自己寫計時迴圈（容易踩陷阱）
    macro-benchmark 用 hyperfine（測整個程式的牆鐘時間）
```

> **micro-benchmark 用框架（Google Benchmark），別自己寫計時迴圈——框架幫你避開所有陷阱**。自己寫計時迴圈（clock() + for loop）容易踩前述所有陷阱（dead code、沒 warmup、迴圈次數不對）。**專業的 micro-benchmark 框架**（C++ 的 Google Benchmark、nanobench）幫你處理這些：**warmup**（先暖機）、**自動決定迴圈次數**（跑夠久才準確）、**防 dead code elimination**（`DoNotOptimize()` 告訴編譯器「這結果有用，別刪」、`ClobberMemory()` 防止記憶體優化）、**精確計時**（高解析度計時器）、**統計**（多次測量看變異）。用法：把要測的程式碼放進 `for (auto _ : state)` 迴圈（框架管理），用 `state.range()` 傳 runtime 輸入（防 constant fold），用 `DoNotOptimize()` 防優化掉。框架輸出「每次呼叫的精確時間」（如 2.5 ns）。**分工**：**micro-benchmark 用框架**（測單個函式/迴圈的精確時間，避開陷阱）；**macro-benchmark 用 hyperfine**（測整個程式的牆鐘時間，Ch 0）。記住——**micro-benchmark 不要自己寫計時**，用框架（除非你很清楚所有陷阱）。這是專業 vs 業餘的差別——業餘的自己寫計時迴圈（充滿陷阱），專業的用框架（可信的數字）。

## macro-benchmark 的考量

```
macro-benchmark（完整 workload）的考量：

  1. 代表性（representativeness）：
     workload 要代表「真實使用情境」
     測一個 web server → 用真實的請求模式（不是全打同一個 URL）
        │
  2. 規模（scale）：
     太小的 workload 不反映真實（cache/記憶體行為不同）
        │
  3. 變因控制（前一章）：
     macro 變因更多 → 更要控制環境、多次測量
        │
  4. 定位困難：
     macro 測到「整體慢了 5%」，但「是哪裡？」要再用 profiling（perf）
        │
  → macro 的價值：反映真實效能
    但要搭配 micro/profiling 定位「真實效能差異來自哪」
        │
  常見 macro benchmark：
    SPEC CPU（Ch 2）：業界標準，多種真實 workload
    應用層 benchmark：真實的應用（資料庫、web server、編譯器自己）
```

> **macro-benchmark 反映真實效能，但要「代表性」（真實的 workload）+ 搭配 profiling 定位差異來自哪**。macro-benchmark 測完整 workload，價值是**反映真實效能**（真實的 cache/分支/記憶體行為，micro 測不出來的）。但有幾個考量：(1) **代表性**——workload 要代表真實使用情境（測 web server 用真實的請求模式，不是全打同一個 URL——否則 cache 命中率不真實）；(2) **規模**——太小不反映真實；(3) **變因控制**（macro 變因更多，更要控制環境、多次測量，Ch 0）；(4) **定位困難**——macro 測到「整體慢了 5%」，但**「是哪裡慢」**要再用 profiling（perf，Ch 7）定位。所以 macro 和 micro/profiling **互補**：macro 評估真實效能、profiling 定位差異來自哪個函式、micro 深入分析那個函式。常見的 macro benchmark：**SPEC CPU**（Ch 2，業界標準，多種真實 workload）、應用層 benchmark（真實的資料庫/web server/編譯器）。perf_bench 的工作流常是：**macro 發現「整體效能變化」→ profiling 定位「哪個函式」→ micro 分析「那個函式為什麼」→ 改 → macro 驗證「真實效果」**。理解 micro 和 macro 的互補，你知道在效能工作的不同階段用哪種——不會「只測 micro 就下結論真實效能」（micro 不代表真實）也不會「只測 macro 不定位」（不知道哪裡要優化）。

## 故意弄壞:dead code elimination 的震撼

```bash
cd ~/perflab
# 展示 dead code elimination 怎麼讓 micro-benchmark 失真
cat > bad_bench.c <<'EOF'
#include <stdio.h>
#include <time.h>
long compute(long n) {
    long r = 0;
    for (int i = 0; i < 100; i++) r += n * i % 13;   // 一些計算
    return r;
}
int main() {
    clock_t start = clock();
    for (long i = 0; i < 10000000L; i++) {
        compute(i);              // 結果沒用！
    }
    clock_t end = clock();
    printf("Time: %f sec\n", (double)(end - start) / CLOCKS_PER_SEC);
    return 0;
}
EOF

# -O0（不優化）：compute 真的跑
gcc -O0 bad_bench.c -o bad_O0
./bad_O0    # Time: 0.8 sec （真的算了）

# -O2（優化）：compute 被優化掉！
gcc -O2 bad_bench.c -o bad_O2
./bad_O2    # Time: 0.000001 sec （接近 0！compute 整個被刪了）
# → 同樣的程式碼，-O2 測到「接近 0」—— 因為結果沒用，編譯器刪掉了
#   如果你用這個 benchmark 下結論「compute 超快」→ 完全錯誤！

# 驗證：看組合語言，-O2 版本根本沒有 compute 的迴圈
gcc -O2 -S bad_bench.c -o bad_O2.s
grep -c 'imul\|idiv' bad_O2.s    # 計算指令很少或沒有（被優化掉）

# 修正：用 volatile/累積結果
cat > good_bench.c <<'EOF'
#include <stdio.h>
#include <time.h>
long compute(long n) {
    long r = 0;
    for (int i = 0; i < 100; i++) r += n * i % 13;
    return r;
}
int main() {
    volatile long sink = 0;
    clock_t start = clock();
    for (long i = 0; i < 10000000L; i++) {
        sink += compute(i);      // 累積到 volatile → 不能被優化掉
    }
    clock_t end = clock();
    printf("Time: %f sec, sink=%ld\n", (double)(end - start) / CLOCKS_PER_SEC, sink);
    return 0;
}
EOF
gcc -O2 good_bench.c -o good_O2
./good_O2    # Time: 0.5 sec （真的測到了 compute 的時間）
# → 加 volatile 後，-O2 也真的跑 compute（測到真實時間）
```

> **同樣的 micro-benchmark，-O2 測到「接近 0」（dead code elimination）vs 加 volatile 後測到真實時間——這個震撼讓你永遠記得驗證 benchmark**。這個實驗展示 micro-benchmark 最危險的陷阱——`bad_bench.c` 的 `compute(i)` 結果沒用，所以 **-O2 把整個 compute 優化掉**，測到「接近 0」（你以為 compute 超快，其實它根本沒跑！）。對比 -O0（不優化，真的跑）測到 0.8 秒——**同樣的程式碼，-O2 和 -O0 差了百萬倍**，純粹因為 dead code elimination。如果你用 `bad_bench` 下結論「compute 很快」或「這個優化讓它快了 100 萬倍」，**完全錯誤**。修正是加 `volatile`（`sink += compute(i)`，結果被用了）——-O2 也真的跑 compute，測到真實的 0.5 秒。**這個震撼的教訓**：micro-benchmark 一定要**驗證「編譯器真的跑了你要測的程式碼」**——方法：(1) 看時間合不合理（接近 0 = 可疑）；(2) 看組合語言（你要測的指令在嗎）；(3) 用 volatile/框架防優化。這是 perf_bench 的核心紀律——**不要相信沒驗證的 micro-benchmark**。無數人因為這個陷阱得出錯誤結論（「我的優化讓程式快了 1000 倍」其實是編譯器把舊版優化掉了，比較的是空迴圈 vs 真實程式）。永遠驗證你的 benchmark 真的在測你以為的東西。Ch 4（統計）和練習會反覆訓練這個「驗證 benchmark 有效性」的習慣。

## 動手練習

1. 跑「故意弄壞」：親眼看 dead code elimination（-O0 vs -O2 差百萬倍），用 volatile 修正

2. 驗證 benchmark：寫一個 micro-benchmark，看組合語言確認「要測的程式碼真的有編進去」

3. constant folding：寫一個輸入是常數 vs runtime 變數的 benchmark，看差別

4. micro vs macro：對一個程式，寫 micro-benchmark 測某函式 + 用 hyperfine 測整個程式，比較

5. 用框架（選做）：裝 Google Benchmark，寫一個正確的 micro-benchmark，對比自己寫的計時

## 本章重點整理

- micro-benchmark（測一小段，聚焦但脫離真實）vs macro-benchmark（測完整 workload，真實但難定位）
- micro 的陷阱：dead code elimination（結果沒用被優化掉）、constant folding（常數輸入編譯時算好）、沒 warmup、太小、不真實輸入
- 修陷阱：volatile/DoNotOptimize 防優化、runtime 變數防 constant fold、warmup、夠大的 workload
- micro-benchmark 用框架（Google Benchmark），別自己寫計時迴圈（容易踩陷阱）；macro 用 hyperfine
- 一定要驗證 benchmark「真的測了你以為的東西」（看時間/組合語言）——dead code elimination 能讓結果差百萬倍

## 自我檢核

- [ ] 能區分 micro 和 macro benchmark，知道各自適用情境
- [ ] 知道 micro-benchmark 的主要陷阱（dead code/constant fold/warmup）
- [ ] 會用 volatile/框架防止編譯器優化掉要測的東西
- [ ] 知道要驗證 benchmark 的有效性（看組合語言/時間）
- [ ] 理解 micro/macro/profiling 的互補（評估真實 + 定位 + 深入分析）

## 延伸閱讀

### 文章

- **[Benchmarking pitfalls](https://easyperf.net/blog/2019/08/02/Perf-measurement-environment-on-Linux)** — Denis Bakhvalov
  - **這篇說什麼**：micro-benchmark 的陷阱和正確做法
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章陷阱的權威深入版

- **[Google Benchmark 文件](https://github.com/google/benchmark/blob/main/docs/user_guide.md)** — Google
  - **讀哪裡**：DoNotOptimize、防優化那節
  - **為什麼值得讀**：專業 micro-benchmark 框架的用法

### 書籍

- **《Performance Analysis and Tuning on Modern CPUs》— benchmarking 章** — Denis Bakhvalov
  - **讀哪幾章**：micro vs macro、measurement 那幾章
  - **這本書的定位**：現代 CPU 效能分析，benchmark 方法論

### 影片

- **[CppCon: Benchmarking C++ Code](https://www.youtube.com/watch?v=zWxSZcpeS8Q)** — Bryce Lelbach
  - **為什麼值得讀**：micro-benchmark 的陷阱和框架用法（C++ 角度）

下一章看 SPEC CPU——業界 benchmark 之王，理解它的結構、怎麼跑、怎麼解讀，以及它在 compiler/CPU 效能評估的角色。

→ [Ch 2 SPEC CPU：業界 benchmark 之王](./02-spec-cpu.md)
