# Ch 1 — 觀察工具全景

> **目標**：建立整門課的地圖——把所有觀察工具放在一張「觀察什麼、在哪一層、用哪個工具」的全景圖上，理解 dynamic（動態，跑時觀察）vs static（靜態，不跑也能看）的分野、以及「遇到問題該從哪個工具下手」的決策框架。讀完你有一張心智地圖，知道後面每章的工具在整體中的位置，以及實際 debug 時怎麼選工具。

> **環境**：概念章，搭配各工具的一行示範。

## 為什麼先看全景？

觀察工具很多（strace/ltrace/lsof/perf/valgrind/ftrace/bpftrace…），一開始就一個個學容易迷失——學了 strace 卻不知道它和 perf 的分工、遇到問題不知道該用哪個。所以先看全景：把所有工具放在一張地圖上，理解每個觀察「什麼」、在「哪一層」、什麼問題該用它。

這張地圖是後面所有章節的座標系。之後每學一個工具，你都知道「它在地圖的哪裡、解決哪類問題」。更重要的是建立**選工具的直覺**——真實 debug 時，「程式卡住了」「記憶體漲」「為什麼慢」各該從哪個工具下手。這章給你這個框架。

## 先建立直覺:醫生的診斷工具

```
觀察工具 = 醫生診斷病人的各種儀器

  病人（你的程式）有各種「症狀」，用不同儀器診斷：
        │
  「它在做什麼」（行為）：
    → 聽診器（strace）：聽它對 kernel 說什麼
    → X 光（ltrace）：看它呼叫哪些 library
        │
  「它現在的狀態」（快照）：
    → 體溫/血壓（/proc, lsof, ss）：當前的 process/fd/連線狀態
        │
  「為什麼慢」（效能）：
    → 心電圖（perf）：時間花在哪、CPU 在幹嘛
        │
  「哪裡壞了」（正確性）：
    → 血液檢查（valgrind/sanitizers）：記憶體/並發的隱疾
        │
  → 不同症狀用不同儀器
    好醫生知道「這個症狀該用哪個儀器」
    這章教你建立這個診斷直覺
```

關鍵心智：觀察工具像醫生的診斷儀器——不同「症狀」（卡住/漏記憶體/慢/崩潰）用不同儀器（strace/valgrind/perf）。本課教你每個儀器怎麼用、怎麼運作，以及最重要的——「這個症狀該用哪個儀器」的診斷直覺。

## 全景圖:工具的座標系

```
觀察工具全景（按「觀察什麼」分類）：

  ┌─────────────────────────────────────────────────────┐
  │ 動態行為（dynamic，程式跑時觀察）                      │
  ├─────────────────────────────────────────────────────┤
  │  strace    syscall 層（程式對 kernel 的請求）  Ch 5   │
  │  ltrace    library 層（library 函式呼叫）       Ch 6   │
  │  perf      效能（CPU/時間花哪、profiling）       Ch 12  │
  │  ftrace    kernel 內部函式 trace                Ch 13  │
  │  bpftrace  可程式化的動態 trace                  Ch 14  │
  │  valgrind  記憶體/並發/profiling（插樁模擬）     Ch 15-17│
  │  sanitizers編譯期插樁的執行期檢查                Ch 18  │
  ├─────────────────────────────────────────────────────┤
  │ 系統狀態（snapshot，當前狀態快照）                    │
  ├─────────────────────────────────────────────────────┤
  │  /proc     process/系統狀態（一切的來源）       Ch 7   │
  │  lsof      開啟的檔案/fd                         Ch 8   │
  │  ss        網路連線/socket                       Ch 9   │
  │  ps/top    process 列表/資源                     Ch 10  │
  │  vmstat等  系統資源統計（CPU/記憶體/IO）         Ch 10  │
  ├─────────────────────────────────────────────────────┤
  │ 靜態分析（static，不跑程式也能看）                    │
  ├─────────────────────────────────────────────────────┤
  │  nm/objdump/readelf  ELF 二進位的結構/符號/反組譯 Ch 11│
  └─────────────────────────────────────────────────────┘
        │
  + 自製工具（理解工具底層）：
    ptrace（Ch 3-4 寫 mini-strace, Ch 19 注入）
    LD_PRELOAD（Ch 20 攔截 library）
```

> **觀察工具分三大類：動態（跑時觀察行為/效能）、系統狀態（當前快照）、靜態（不跑也能看二進位）——這個分類是選工具的第一層判斷**。**動態工具**在程式**跑的時候**觀察它的行為——strace（syscall 層）、ltrace（library 層）、perf（效能）、valgrind（記憶體/並發）。**系統狀態工具**看**當前的快照**——/proc（一切狀態的來源）、lsof（開啟的 fd）、ss（網路連線）、ps（process）。**靜態工具**不用跑程式就能看**二進位的結構**——nm/objdump/readelf 看 ELF 的符號、反組譯、結構。選工具的第一層判斷：你要看「行為」（動態）、「現在的狀態」（快照）、還是「二進位本身」（靜態）？「程式卡在某個操作」→ 動態（strace 看它卡在哪個 syscall）；「它開了哪些檔案」→ 狀態（lsof）；「這個 binary 用了哪些 library」→ 靜態（readelf）。本課的特色是還教你**自製工具**（用 ptrace 寫 mini-strace、用 LD_PRELOAD 攔截）——理解工具底層，你就不被現成工具限制。這張全景圖是後面每章的座標——記住它，學每個工具時對照「它在地圖的哪裡」。

## 選工具的決策框架

```
遇到問題該用哪個工具（決策框架）：

  「程式卡住/沒反應」：
    → strace：看它卡在哪個 syscall（read? futex? connect?）
      卡在 read = 在等輸入；卡在 futex = 在等鎖；卡在 connect = 連不上
        │
  「程式崩潰（segfault）」：
    → 先看 core dump（Ch 21）或 sanitizer（ASan，Ch 18）找崩潰點
    → valgrind 看是不是記憶體錯誤
        │
  「記憶體一直漲（leak）」：
    → valgrind memcheck（Ch 15）或 ASan 的 leak 偵測
        │
  「為什麼這麼慢」：
    → perf（Ch 12）：profiling 看時間花在哪個函式
    → strace -T：看哪個 syscall 慢
        │
  「並發 bug（race/deadlock）」：
    → helgrind/TSan（Ch 16/18）：偵測 data race
    → strace 看 futex（鎖的 syscall）
        │
  「檔案/網路問題」：
    → lsof（開了什麼）、ss（連線狀態）、strace（看 open/connect）
        │
  → 從「症狀」對應到工具，是 debug 的第一步
    本課讓你對每個工具夠熟，能快速選對
```

> **「症狀 → 工具」的對應是 debug 的第一步——這個決策框架比死記工具用法有價值**。真實 debug 時，先從症狀判斷該用哪個工具：**卡住** → strace 看卡在哪個 syscall（這是 strace 最強的用途之一——`strace -p <卡住的PID>` 立刻看到它在等什麼：卡在 `read` 是等輸入、卡在 `futex` 是等鎖、卡在 `connect` 是連不上）；**崩潰** → core dump（Ch 21）或 ASan（Ch 18）找崩潰點；**記憶體漲** → valgrind/ASan 找 leak；**慢** → perf 做 profiling（看時間花在哪）或 `strace -T`（看慢的 syscall）；**並發 bug** → helgrind/TSan 偵測 race；**檔案/網路問題** → lsof/ss/strace。這個框架讓你不會「拿到問題不知從何下手」——先判斷症狀類型，就知道該用哪層的工具。本課的目標是讓你對每個工具夠熟，能快速選對並有效使用。練習和 Final Project 會反覆訓練這個「症狀 → 工具 → 定位」的流程。記住：**好的 debugger 不是會用所有工具，而是知道「這個問題該用哪個工具」**——這個直覺是本課要培養的核心能力。

## dynamic vs static:兩種根本的觀察

```
動態（dynamic）vs 靜態（static）觀察的根本差別：

  靜態（static）：不跑程式，看「二進位本身」
    nm/objdump/readelf（Ch 11）
    看：符號、函式、依賴的 library、組合語言
    優點：不用跑、安全（不執行可疑程式）、看「全貌」
    限制：看不到「實際執行時發生什麼」（哪條路徑、什麼值）
        │
  動態（dynamic）：跑程式，看「實際行為」
    strace/ltrace/perf/valgrind
    看：實際的 syscall、實際的值、實際走的路徑
    優點：看到「真實發生什麼」
    限制：要跑（可能有副作用）、只看到「這次執行」的路徑
        │
  → 互補：靜態看「可能做什麼」（全貌）
    動態看「實際做了什麼」（這次執行）
    複雜問題常兩者結合（如逆向工程：靜態看結構 + 動態看行為）
```

> **靜態（看二進位）和動態（看執行）是互補的兩種觀察——靜態看「可能做什麼」，動態看「實際做了什麼」**。**靜態分析**（Ch 11 的 nm/objdump/readelf）不執行程式，直接看二進位的結構——符號表、依賴的 library、反組譯的程式碼。優點：不用跑（安全，分析可疑程式不會執行它）、看「全貌」（所有函式、所有路徑）。限制：看不到「實際執行時走哪條路徑、變數是什麼值」。**動態分析**（strace/ltrace/perf/valgrind）執行程式，看實際行為——真實的 syscall、真實的值、真實走的路徑。優點：看到「真的發生什麼」。限制：要執行（可能有副作用）、只看到「這次執行」走的路徑（其他分支沒走到就看不到）。兩者**互補**——靜態看「程式可能做什麼」（結構全貌），動態看「程式這次實際做了什麼」（執行軌跡）。複雜問題常結合：逆向工程一個可疑程式，先靜態看結構（不執行，安全），再在隔離環境動態看行為。理解這個二分，你選工具時多一層判斷——「我要看二進位結構（靜態）還是執行行為（動態）」。本課大部分是動態工具（debug 的主力），Ch 11 補上靜態分析。

## 故意弄壞:用不同工具看同一個程式

```bash
# 一個程式，用不同工具從不同層觀察（建立分層直覺）
cd ~/obslab
cat > demo.c <<'EOF'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main() {
    char *buf = malloc(100);          // library 層：malloc
    strcpy(buf, "hello");             // library 層：strcpy
    FILE *f = fopen("/tmp/demo.txt", "w");  // syscall 層：openat
    fprintf(f, "%s\n", buf);          // syscall 層：write
    fclose(f);
    free(buf);
    return 0;
}
EOF
gcc -g -O0 demo.c -o demo

# syscall 層（strace）：看它對 kernel 做什麼
strace -e trace=openat,write,close ./demo
# openat(... "/tmp/demo.txt" ...) = 3    ← fopen 底層
# write(3, "hello\n", 6) = 6             ← fprintf 底層

# library 層（ltrace）：看它呼叫哪些 library 函式
ltrace ./demo
# malloc(100) = 0x...                    ← 看到 malloc！
# strcpy(0x..., "hello") = 0x...         ← 看到 strcpy！
# fopen("/tmp/demo.txt", "w") = 0x...
# → strace 看不到 malloc/strcpy（它們不是 syscall），ltrace 看得到

# 記憶體層（valgrind）：看記憶體用得對不對
valgrind ./demo 2>&1 | grep -E 'ERROR|leak'
# 這個程式沒 leak（free 了）→ valgrind 不報錯

# → 同一個程式，strace/ltrace/valgrind 看到不同層次
#   選對工具才看得到你要找的東西
```

> **同一個程式，strace 看 syscall、ltrace 看 library 呼叫、valgrind 看記憶體——選對層才看得到你要找的東西**。這個實驗展示分層觀察的核心：`malloc`/`strcpy` 是 **library 函式**（不是 syscall），所以 **strace 看不到它們**（strace 只看 syscall），但 **ltrace 看得到**。而 `fopen`/`fprintf` 底層的 `openat`/`write` 是 syscall，strace 看得到。`malloc` 的記憶體用得對不對（leak/越界）要 **valgrind** 才看得到。所以**選錯工具就找不到問題**——如果你想找 malloc 的 leak 卻用 strace，永遠找不到（strace 看不到 malloc）。這就是為什麼要建立「分層觀察」的直覺——先判斷問題在哪一層（syscall? library? 記憶體?），再選對應的工具。這個實驗也預告了後面的章節：strace（Ch 5，syscall 層）、ltrace（Ch 6，library 層）、valgrind（Ch 15，記憶體層）。建議自己跑一遍，親眼看「同一個程式在不同工具下顯示不同的東西」——這建立了本課最重要的直覺：**觀察是分層的，選對層才看得到**。

## 動手練習

1. 畫全景圖：不看書，憑記憶畫出「觀察什麼 → 哪一層 → 哪個工具」的全景

2. 跑分層觀察：對 demo.c 用 strace/ltrace/valgrind，看每個顯示什麼不同（為什麼 strace 看不到 malloc）

3. 練決策框架：對幾個症狀（卡住/leak/慢/segfault）說出該用哪個工具

4. 動態 vs 靜態：對一個程式用 strace（動態）和 readelf（靜態，Ch 11 預習），理解差別

5. 選工具實戰：想一個你遇過的程式問題，判斷該用本課哪個工具

## 本章重點整理

- 觀察工具分三大類：動態（跑時看行為/效能：strace/ltrace/perf/valgrind）、系統狀態（快照：/proc/lsof/ss）、靜態（看二進位：nm/objdump/readelf）
- 選工具第一層判斷：看「行為」（動態）、「現在狀態」（快照）、還是「二進位本身」（靜態）
- 「症狀 → 工具」決策框架：卡住→strace、leak→valgrind、慢→perf、race→helgrind/TSan、檔案/網路→lsof/ss
- 動態 vs 靜態互補：靜態看「可能做什麼」（全貌、不執行），動態看「實際做了什麼」（執行軌跡）
- 觀察是分層的：同一程式 strace 看 syscall、ltrace 看 library、valgrind 看記憶體——選對層才看得到

## 自我檢核

- [ ] 能畫出觀察工具的全景圖（觀察什麼/哪一層/哪個工具）
- [ ] 能用「症狀 → 工具」框架，對常見問題選出合適工具
- [ ] 理解動態 vs 靜態觀察的差別和互補
- [ ] 知道為什麼 strace 看不到 malloc（分層），該用什麼看
- [ ] 對本課每個工具在地圖的位置有初步認識

## 延伸閱讀

### 文章

- **[Linux debugging tools 概覽](https://jvns.ca/blog/2016/07/03/debugging-tools/)** — Julia Evans
  - **這篇說什麼**：各種 Linux debug 工具的全景和怎麼選
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章「工具全景」的最佳補充，把工具放進實用框架

- **[Brendan Gregg 的 Linux Performance 工具圖](https://www.brendangregg.com/linuxperf.html)** — Brendan Gregg
  - **這篇說什麼**：一張著名的圖，標出每個工具觀察 Linux 的哪個部分
  - **讀哪裡**：那張工具圖
  - **為什麼值得讀**：本課全景圖的「效能版」權威，把工具對應到系統的各部分

### 書籍

- **《Systems Performance》— Ch 4 (Observability Tools)** — Brendan Gregg
  - **讀哪幾章**：Ch 4（觀測工具的分類和方法論）
  - **這本書的定位**：觀測方法論的權威，把工具放進系統分析的框架
  - **前提**：本章

下一章我們補完基礎——process/syscall/fd/signal 模型。理解這些「被觀察的對象」是什麼，後面的工具才有意義（你要先懂 process 是什麼，才能理解 strace 在觀察什麼）。

→ [Ch 2 process / syscall / fd / signal 模型](./02-process-syscall-fd-model.md)
