# Ch 17 — valgrind callgrind / massif / cachegrind

> 目標：用 valgrind 的 profiling tools 看 call graph、heap 用量、cache miss —— **不用 hardware counter** 也能 profile，適合精細 / 跨平台分析。

## 三個 tool 的角色

| Tool | 看什麼 | 對應傳統工具 |
|---|---|---|
| **callgrind** | call graph + 每個 function cycle 估計 | gprof，perf record |
| **massif** | heap 用量 timeline | heaptrack，自己塞 hook |
| **cachegrind** | L1 / LL cache miss 模擬 | perf stat -e cache-misses |

跟 perf 比：
- perf 用 hardware counter，**真實但採樣**（會漏 / 採樣偏差）
- valgrind 是 simulator，**精確（每次 access 都算）但慢，且模擬的 cache 不一定跟實機 match**

valgrind profiling 的最大價值：**不需要 root 權限、不依賴 perf event、跨平台、結果可重現**。

## callgrind

```bash
valgrind --tool=callgrind ./myprog
```

跑完產生 `callgrind.out.PID`。

```bash
callgrind_annotate callgrind.out.1234 | less
```

```
--------------------------------------------------------------------------------
            Ir  file:function
--------------------------------------------------------------------------------
123,456,789  ???:???
 45,678,901  myprog.c:hot_function [./myprog]
 12,345,678  libc.so.6:strlen
  3,456,789  myprog.c:helper [./myprog]
```

`Ir` = instruction read = 該 function 執行的指令數。**比 cycle 穩定**（不受 CPU pipeline 影響）。

## kcachegrind GUI

```bash
sudo apt install kcachegrind
kcachegrind callgrind.out.1234
```

GUI 顯示：

- 每個 function 的 self / inclusive cost
- Caller / callee graph
- Source 對照（每行多少 Ir）
- Call graph 視覺化

**callgrind + kcachegrind 是分析「為什麼這 function 慢」的標準組合**。

## callgrind 加 cache simulation

```bash
valgrind --tool=callgrind --simulate-cache=yes ./myprog
```

額外 cache miss 統計：

```
Ir  Dr  Dw  D1mr  D1mw  DLmr  DLmw
123 45  67  8     9     10    11
```

| 欄 | 意義 |
|---|---|
| Ir | instruction reads |
| Dr | data reads |
| Dw | data writes |
| D1mr | L1 data miss read |
| D1mw | L1 data miss write |
| DLmr | last-level data miss read |
| DLmw | last-level data miss write |

每個 function 的 cache miss 一目了然。

## cachegrind

`callgrind --simulate-cache` 已經涵蓋。`cachegrind` 是更專注 cache 的 standalone tool：

```bash
valgrind --tool=cachegrind ./myprog
cg_annotate cachegrind.out.PID
```

純看 cache、不算 call graph。比 callgrind 簡單但少資訊。一般用 callgrind 就好。

## massif — heap profiler

```bash
valgrind --tool=massif ./myprog
```

跑完產生 `massif.out.PID`。

```bash
ms_print massif.out.1234 | less
```

```
    MB
1.234^                                                     :::
     |                                                  :::: ::
     |                                              ::::: : : :
     |                                          :::::: :::: : :
     |                                       :::: : ::: : :: ::
     |                                   :::::: : ::: :  : :: ::
     |                                ::::: : :: : ::: :  : :: ::
     |                            :::::: ::: : :: : ::: :  : :: ::
     |                         ::::: : ::: ::: : :: : ::: :  : :: ::
     |                      ::::: ::: ::: ::: ::: : :: : ::: :  : :: ::
     |                  ::::: : ::: ::: ::: ::: ::: : :: : ::: :  : :: ::
     |               ::::: ::: : ::: ::: ::: ::: ::: : :: : ::: :  : :: ::
     |            :::: : ::: ::: : ::: ::: ::: ::: ::: : :: : ::: :  : :: ::
     |          @@: : ::: ::: ::: : ::: ::: ::: ::: ::: : :: : ::: :  : :: ::
   0 +----------------------------------------------------------------------->Mi
     0                                                                  1.234

Number of snapshots: 50
 Detailed snapshots: ...
```

ASCII 圖顯示 heap 隨時間變化。每個 snapshot 還能看 callstack：

```
98.76% (1,234,567B) (heap allocation functions) malloc/new/new[]
->50.00% (623,456B) 0x401234: alloc_buffer (myprog.c:45)
| ->50.00% (623,456B) 0x401345: process_request (myprog.c:78)
|   ->50.00% (623,456B) 0x401456: main (myprog.c:120)
->48.76% (601,234B) 0x402345: another_alloc (myprog.c:90)
```

「heap 50% 是 alloc_buffer 配置的」。**找誰吃記憶體最強工具**。

## massif 加 detail

```bash
valgrind --tool=massif --detailed-freq=1 --threshold=0.1 ./myprog
```

`--detailed-freq=N` 每 N 個 snapshot 一個 detailed（含 stack）。`--threshold=0.1` 顯示佔 0.1% 以上的。

也支援 stack 跟 page-level allocation：

```bash
valgrind --tool=massif --stacks=yes ./myprog       # 連 stack 也算
valgrind --tool=massif --pages-as-heap=yes ./myprog # mmap / brk 也算
```

## massif-visualizer GUI

```bash
sudo apt install massif-visualizer
massif-visualizer massif.out.1234
```

互動式圖表，比 ms_print 好看。

## 一個常見場景：找熱點 function

```bash
valgrind --tool=callgrind ./myprog
kcachegrind callgrind.out.PID
```

按 inclusive cost 排序，找最頂端 function。展開看 callee 分布。比 perf 慢但**精確**。

## 一個常見場景：heap 蜂蜜陷阱

「我的 server 跑 1 小時 RAM 越來越大但 valgrind memcheck 沒抓到 leak」 → massif：

```bash
valgrind --tool=massif --time-unit=B ./myserver
# ... 跑 30 分鐘 ...
ms_print massif.out.PID
```

看 timeline 哪段在漲，detailed snapshot 看是哪個 alloc path。

很多時候不是「leak」（指標還在）但「fragmentation 或 cache 一直長」。

## 一個常見場景：cache friendly 改寫

```c
struct point { int x, y, z; };
struct point pts[10000];

// 對比：
for (int i = 0; i < 10000; i++) sum += pts[i].x;     // 訪問 1/3 cache line
for (int i = 0; i < 10000; i++) sum += pts[i].x + pts[i].y + pts[i].z;  // 全用
```

跑 cachegrind 比較 D1mr / DLmr 數字。改成 SoA（struct of arrays）通常 miss 大降。

## 一個常見踩雷：valgrind cache 模型不準

valgrind 用簡化 cache 模型（單 core、固定 line size）。模擬數值跟實機**有差**。趨勢對的，絕對值別當真。

要實機 cache miss 還是 perf：

```bash
perf stat -e cache-misses,cache-references ./myprog
```

## 一個常見踩雷：callgrind 太慢

callgrind 比 memcheck 還慢（追每個 call）。跑大 workload 不切實際。

對策：

```bash
valgrind --tool=callgrind --instr-atstart=no ./myprog
```

預設不錄。手動觸發：

```c
#include <valgrind/callgrind.h>
CALLGRIND_START_INSTRUMENTATION;
hot_section();
CALLGRIND_STOP_INSTRUMENTATION;
```

只 profile 你關心的段。

## 一個常見踩雷：massif snapshot 太疏

massif 預設按 instruction 數量取 snapshot，long-running daemon 可能整個 timeline 只有幾個點。

```bash
valgrind --tool=massif --time-unit=ms ./myprog
```

按真實時間取。或自己控制：

```c
#include <valgrind/massif.h>
MASSIF_TAKE_SNAPSHOT;
```

## 動手練習

**1. callgrind 一個遞迴**

```c
// fib.c
#include <stdio.h>
int fib(int n) { return n < 2 ? n : fib(n-1) + fib(n-2); }
int main() { printf("%d\n", fib(30)); }
```

```bash
gcc -g -O0 fib.c -o fib
valgrind --tool=callgrind ./fib
kcachegrind callgrind.out.*
```

看 fib() 自我 recurse 的 call graph、Ir 數字。

**2. cache miss 對比**

寫 row-major vs column-major matrix scan：

```c
#define N 4096
int a[N][N];
// row major
for (int i = 0; i < N; i++)
    for (int j = 0; j < N; j++) sum += a[i][j];

// column major
for (int j = 0; j < N; j++)
    for (int i = 0; i < N; i++) sum += a[i][j];
```

兩個版本各 build 一次，跑 cachegrind 比 D1mr / DLmr。

**3. massif 看 leak**

寫個程式每 100ms malloc 1KB（不 free）。跑 massif 1 分鐘：

```bash
valgrind --tool=massif --time-unit=ms ./leak &
sleep 60
kill %1
ms_print massif.out.*
```

看到 heap 線性上升。

**4. massif 看 stack**

開 `--stacks=yes` 跑遞迴 fib，看 stack 段。

**5. selective 啟動 callgrind**

加 `CALLGRIND_START_INSTRUMENTATION` 圍住關鍵段，跑 `valgrind --tool=callgrind --instr-atstart=no`。比較資料量。

## 自我檢核

- [ ] 用 callgrind + kcachegrind 看過 call graph
- [ ] 知道 Ir 是什麼、為什麼比 cycle 穩定
- [ ] cachegrind / callgrind --simulate-cache 看 D1mr / DLmr
- [ ] 用 massif 看過 heap timeline
- [ ] 知道 valgrind cache 模型不真實，趨勢可信絕對值不可
- [ ] 知道 selective instrumentation 怎麼做

下一章看 sanitizer — 編譯時插的更快替代品。

→ [Ch 18 Sanitizers (ASan / UBSan / TSan / MSan)](./18-sanitizers.md)
