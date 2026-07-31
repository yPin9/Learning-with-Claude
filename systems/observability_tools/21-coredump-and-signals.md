# Ch 21 — core dump 與 signal

> **目標**：掌握「崩潰後分析」——core dump（崩潰瞬間的記憶體快照）怎麼產生、怎麼用 gdb 分析 core 找出崩潰點、signal 怎麼觸發 core dump、以及在生產環境怎麼設定和收集 core。前面的工具大多在程式「執行時」觀察，這章補上「崩潰後」的分析——當程式已經死了，core dump 是你唯一的線索。這是 debug「程式崩潰但沒有現場」的關鍵。

> **環境**：Linux，gdb，core dump（要設定 ulimit/sysctl）。`gcc -g`。

## 為什麼需要崩潰後分析？

很多時候程式崩潰時你**不在現場**——生產服務半夜崩潰、客戶端崩潰你看不到、間歇性崩潰難重現。等你發現時，程式已經死了。怎麼 debug「已經死掉的程式」？答案是 **core dump**——它是程式崩潰瞬間的**記憶體快照**（暫存器、堆疊、變數的當時狀態）。

core dump 讓你能「事後重建崩潰現場」——用 gdb 開 core，看「崩潰時在哪個函式、哪一行、變數是什麼、呼叫堆疊怎樣」。這是 debug「程式崩潰但沒有現場」的關鍵，也是生產環境 debug 崩潰的標準方法（崩潰時自動產生 core，事後分析）。這章補上前面工具的「執行時觀察」之外的「崩潰後分析」——你的觀察能力就涵蓋了程式的整個生命週期（執行時、崩潰時、崩潰後）。

## 先建立直覺:崩潰現場的照片

```
core dump = 程式崩潰瞬間的「現場照片」

  程式正常跑 → 崩潰（如 SIGSEGV）→ kernel 把當時的記憶體狀態
              「dump」成一個檔案（core）
        │
  core 裡有什麼（崩潰瞬間的快照）：
    - 暫存器的值（PC = 崩潰在哪條指令）
    - 呼叫堆疊（崩潰時的函式呼叫鏈）
    - 變數的值（當時的記憶體內容）
    - 哪個 signal 造成崩潰
        │
  用 gdb 開 core（重建現場）：
    gdb ./prog core
    → 看崩潰在哪個函式、哪一行、變數是什麼
    → 像「崩潰當下用 gdb 看」，但是事後
        │
  → core dump 是「崩潰現場的照片」
    程式死了，但 core 保留了死亡瞬間的一切
    gdb 開 core = 重建現場做事後鑑定
```

關鍵心智：core dump 是程式崩潰瞬間的「現場照片」——kernel 把崩潰時的記憶體狀態（暫存器、堆疊、變數、哪個 signal）dump 成檔案。用 gdb 開 core 重建現場——看崩潰在哪個函式、哪一行、變數是什麼。像「崩潰當下用 gdb 看」，但是事後（程式已經死了，core 保留了死亡瞬間）。

> core dump 由 signal（Ch 2）觸發——SIGSEGV/SIGABRT 等的預設行為是「終止 + 產生 core」。如果對 signal 不熟，回看 [Ch 2](./02-process-syscall-fd-model.md)。gdb 分析 core 用到 ELF/符號（Ch 11）。

## 啟用並產生 core dump

```bash
# === 啟用 core dump（預設常常關閉）===
ulimit -c                        # 看當前 core 大小限制（0 = 關閉）
ulimit -c unlimited              # 啟用（不限大小，當前 shell）

# core 檔案放哪、叫什麼（由 sysctl 控制）
cat /proc/sys/kernel/core_pattern
# core 或 |/usr/lib/systemd/systemd-coredump ...（systemd 系統用 coredumpctl）

# 簡單設定：core 放當前目錄，含 PID
# sudo sysctl kernel.core_pattern=core.%e.%p   （%e 程式名 %p PID）

# === 產生一個 core dump ===
cd ~/obslab
cat > crash.c <<'EOF'
#include <stdio.h>
#include <string.h>
void process(char *data) {
    char *p = NULL;
    strcpy(p, data);   // 寫 NULL → SIGSEGV！
}
int main() {
    char buf[100] = "hello";
    process(buf);
    return 0;
}
EOF
gcc -g -O0 crash.c -o crash

ulimit -c unlimited
./crash
# Segmentation fault (core dumped)   ← 崩潰 + 產生 core

ls core*                            # core 檔案產生了
# 或 systemd 系統：
coredumpctl list                   # 列出收集的 core
```

> **core dump 預設常常關閉（ulimit -c 0），生產環境要主動啟用——這是「崩潰時能事後分析」的前提**。core dump 不是預設就有——`ulimit -c` 常是 0（關閉），要 `ulimit -c unlimited` 啟用（否則崩潰時不產生 core，你就沒有現場可分析）。core 檔案的位置和命名由 **`kernel.core_pattern`**（sysctl）控制——可以是簡單的檔名（`core.%e.%p`，%e 程式名 %p PID）或交給 **systemd-coredump**（現代系統，用 `coredumpctl` 管理）。**生產環境的關鍵設定**：(1) 啟用 core（在服務的 systemd unit 設 `LimitCORE=infinity`，或全域 ulimit）；(2) 設定 core_pattern（放哪、命名，含 PID/時間避免覆蓋）；(3) 確保有空間（core 可能很大——整個 process 的記憶體）。很多「生產服務崩潰但不知為什麼」就是因為**沒啟用 core dump**——崩潰時沒留下現場，只能瞎猜。啟用 core 後，崩潰時自動產生快照，你能事後分析。這是生產環境 debug 崩潰的標準做法——不是「等它再崩潰時盯著」（可能很久才再發生），而是「啟用 core，崩潰時自動留現場，事後分析」。`coredumpctl`（systemd）讓 core 管理更方便（自動收集、`coredumpctl gdb` 直接用 gdb 開最近的 core）。記住：**要能事後分析崩潰，先啟用 core dump**。

## 用 gdb 分析 core

```bash
# === 用 gdb 開 core，重建崩潰現場 ===
gdb ./crash core                   # 或 coredumpctl gdb
# 或 gdb ./crash core.crash.12345

# gdb 裡的分析命令：
# (gdb) bt                         # backtrace：崩潰時的呼叫堆疊
# #0  process (data=...) at crash.c:5     ← 崩潰在 process() 的第 5 行！
# #1  main () at crash.c:10               ← 是 main 呼叫 process 的
# → 直接看到「崩潰在哪個函式、哪一行、怎麼被呼叫的」

# (gdb) frame 0                    # 切到崩潰的那一幀
# (gdb) print p                    # 看變數 p 的值
# $1 = 0x0                         ← p 是 NULL！（崩潰原因：strcpy 到 NULL）
# (gdb) print data                 # 看其他變數
# (gdb) info registers             # 看暫存器（PC 等）
# (gdb) list                       # 看崩潰點附近的程式碼
# → 完整重建崩潰現場：在哪、為什麼（p=NULL）、變數狀態

# 一行命令快速看 backtrace（不進互動 gdb）：
gdb -batch -ex bt ./crash core 2>/dev/null
# #0 process ... crash.c:5
# #1 main ... crash.c:10
```

```
gdb 分析 core 的關鍵命令：
  bt / backtrace    崩潰時的呼叫堆疊（最重要！看崩潰在哪、怎麼來的）
  frame N           切到第 N 幀
  print var         看變數的值（崩潰時的狀態）
  info registers    看暫存器（PC = 崩潰指令）
  list              看崩潰點的程式碼
  info locals       看局部變數
        │
  → bt 先看「崩潰在哪、呼叫鏈」
    然後 print 變數找「為什麼崩潰」（如 p=NULL）
    要 -g 編譯才有行號和變數名
```

> **`bt`（backtrace）是分析 core 的第一命令——它顯示「崩潰在哪個函式、哪一行、怎麼被呼叫的」**。用 `gdb ./prog core` 開 core 後，最重要的命令是 **`bt`（backtrace）**——它顯示崩潰時的**呼叫堆疊**：崩潰在哪個函式（`#0 process ... crash.c:5`）、是誰呼叫的（`#1 main ... crash.c:10`）。這直接告訴你「崩潰在 process() 的第 5 行，是 main 呼叫的」。然後 **`print 變數`** 看崩潰時的變數狀態——`print p` 顯示 `p = 0x0`（NULL），找到崩潰原因（strcpy 到 NULL 指標）。`frame N`（切到某一幀看那層的變數）、`info locals`（局部變數）、`info registers`（暫存器，PC 是崩潰指令）、`list`（崩潰點程式碼）。這完整重建了崩潰現場——**在哪崩潰（bt）、為什麼崩潰（print 變數找出 NULL/越界/壞值）**。需要 **`-g` 編譯**（debug symbols）才有行號和變數名（否則只有位址，難分析——這是為什麼生產 build 也常保留 debug symbols 或分離存放）。快速看：`gdb -batch -ex bt ./prog core`（不進互動 gdb，直接印 backtrace）。這個「core + gdb bt」是 debug 崩潰的標準流程——比「加 printf 重現」強太多（不用重現，core 就是現場）。對「程式崩潰但不知為什麼」（生產崩潰、間歇崩潰、客戶現場崩潰），啟用 core + gdb 分析是黃金方法。理解它，你能 debug「已經死掉的程式」——觀察能力涵蓋崩潰後。

## signal 與崩潰

```bash
# core dump 由 signal 觸發（Ch 2 的 signal）
# 哪些 signal 會產生 core（預設行為）：
#   SIGSEGV（11）：記憶體存取錯誤（最常見）
#   SIGABRT（6）：abort()，assert 失敗，C++ 異常未捕捉，glibc 偵測到記憶體損壞
#   SIGFPE（8）：算術錯誤（除以 0）
#   SIGILL（4）：非法指令
#   SIGBUS（7）：匯流排錯誤（對齊錯誤等）

# 看一個程式被什麼 signal 殺（崩潰原因的第一線索）
./crash; echo "exit: $?"
# exit: 139   = 128 + 11（SIGSEGV）→ 被 SIGSEGV 殺
# 退出碼 128+N = 被 signal N 殺（Ch 2）

# 用 strace 看崩潰瞬間（崩潰前在做什麼 + 收到什麼 signal）
strace ./crash 2>&1 | tail -3
# strcpy 對應的記憶體操作... 
# --- SIGSEGV {si_addr=NULL} ---     ← 收到 SIGSEGV，位址 NULL
# +++ killed by SIGSEGV (core dumped) +++

# === 自己處理 signal 產生診斷（進階）===
# 程式可以註冊 SIGSEGV handler，在崩潰時印出 backtrace
# （用 backtrace() 函式，或寫 core 前記錄資訊）
```

> **退出碼 128+N 告訴你「被哪個 signal 殺」——這是崩潰原因的第一線索，SIGSEGV（記憶體）和 SIGABRT（abort/記憶體損壞）最常見**。core dump 由 **signal** 觸發（Ch 2）——會產生 core 的 signal 各代表不同的崩潰原因：**SIGSEGV**（記憶體存取錯誤，最常見——NULL 解參考、越界、UAF）、**SIGABRT**（abort()——assert 失敗、C++ 未捕捉異常、**glibc 偵測到記憶體損壞**如 double-free/heap 損壞，這是重要線索：SIGABRT + glibc 訊息 = 記憶體損壞）、**SIGFPE**（除以 0 等算術錯誤）、**SIGBUS**（對齊/匯流排錯誤）。**退出碼 128+N = 被 signal N 殺**（Ch 2）——`./crash; echo $?` 顯示 139 = 128+11 = SIGSEGV。這是崩潰原因的第一線索（看退出碼就知道是哪類崩潰）。`strace ... | tail` 看崩潰瞬間——崩潰前在做什麼 + 收到什麼 signal（`--- SIGSEGV {si_addr=NULL} ---` 還告訴你崩潰位址，NULL = 解參考 NULL）。進階：程式可以**自己註冊 SIGSEGV handler**，在崩潰時印出 backtrace（用 `backtrace()` 函式）或記錄診斷資訊——很多生產服務這樣做（崩潰時自己記錄 backtrace 到 log，配合 core dump）。理解 signal 和崩潰的關係，你 debug 崩潰時：(1) 看退出碼（128+N → 哪個 signal → 哪類崩潰）；(2) SIGSEGV → 記憶體存取錯誤（看 core 的 bt + 變數）；(3) SIGABRT → 可能記憶體損壞（看 glibc 訊息）；(4) 用 core + gdb 找精確崩潰點。這把 Ch 2 的 signal 知識和崩潰分析連起來——崩潰是 signal 觸發的，signal 類型是崩潰原因的線索。

## 故意弄壞:從 core 找崩潰根因

```bash
cd ~/obslab
# 一個崩潰但「現場不明顯」的程式
cat > tricky_crash.c <<'EOF'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct { char name[20]; int *data; } Record;
Record* create_record(const char *name) {
    Record *r = malloc(sizeof(Record));
    strncpy(r->name, name, 19);
    r->data = NULL;   // bug: data 沒分配，但後面會用
    return r;
}
int sum_data(Record *r) {
    int total = 0;
    for (int i = 0; i < 5; i++) {
        total += r->data[i];   // 崩潰：r->data 是 NULL！
    }
    return total;
}
int main() {
    Record *r = create_record("test");
    printf("sum = %d\n", sum_data(r));   // 這裡會崩潰
    free(r);
    return 0;
}
EOF
gcc -g -O0 tricky_crash.c -o tricky_crash

ulimit -c unlimited
./tricky_crash
# Segmentation fault (core dumped)

# 用 core 找根因（不用重現、不用加 printf）
gdb -batch -ex 'bt' -ex 'frame 0' -ex 'print r' -ex 'print r->data' ./tricky_crash core* 2>/dev/null
# #0  sum_data (r=0x...) at tricky_crash.c:14    ← 崩潰在 sum_data 第 14 行
# #1  main () at tricky_crash.c:20               ← main 呼叫的
# $1 = (Record *) 0x...                          ← r 不是 NULL（r 有效）
# $2 = (int *) 0x0                               ← 但 r->data 是 NULL！
# → 找到根因：r->data 是 NULL（create_record 沒分配 data），
#   sum_data 解參考 NULL → 崩潰
#   修法：create_record 要 malloc data，或 sum_data 檢查 data != NULL
```

> **core + gdb 找出「r->data 是 NULL」這種讀碼難看出的崩潰根因——不用重現、不用加 printf，core 就是現場**。這個例子展示 core dump 的威力——`tricky_crash` 崩潰在 `sum_data` 的 `r->data[i]`，但**為什麼**？讀碼可能看不出（要追蹤 r->data 怎麼來的）。用 **core + gdb** 直接看崩潰現場：`bt` 顯示「崩潰在 sum_data:14，main 呼叫的」；`print r` 顯示 r 有效（不是 r 的問題）；`print r->data` 顯示 **`0x0`（NULL）**——找到根因！r->data 是 NULL（`create_record` 把它設成 NULL 沒分配），`sum_data` 解參考 NULL 陣列 → 崩潰。整個過程**不用重現崩潰、不用加 printf**——core 保留了崩潰瞬間的一切，gdb 直接看出「哪個變數是壞值」。這比傳統 debug（「加 printf 重現看哪裡崩潰」）強太多——尤其對**難重現的崩潰**（間歇性、生產環境、特定輸入才觸發），core 是唯一的線索。這完成了本課的觀察能力光譜——**執行時觀察**（strace/perf/valgrind 等，前面的章節）+ **崩潰後分析**（core + gdb，這章）。你現在能 debug 程式的整個生命週期：正常執行時看行為（strace）、找效能問題（perf）、抓記憶體/並發 bug（valgrind/sanitizers）、崩潰後找根因（core）。這是完整的 debug 能力——無論問題是「卡住、慢、漏記憶體、race、還是崩潰」，你都有對應的工具和方法。Final Project 會綜合這一切，用一個藏了多種 bug 的壞掉 daemon 考驗你的整套 debug 能力。

## 動手練習

1. 啟用 core：`ulimit -c unlimited`，跑 crash.c，確認產生 core

2. gdb 分析：用 gdb 開 core，`bt` 看崩潰點、`print` 看變數，找崩潰原因

3. 退出碼：看崩潰程式的退出碼（128+N），對應到 signal，理解崩潰類型

4. strace 崩潰：用 strace 看崩潰瞬間，找 `--- SIGSEGV ---` 和崩潰位址

5. 跑「故意弄壞」：用 core + gdb 找 tricky_crash.c 的 NULL 指標根因（不重現、不加 printf）

## 本章重點整理

- core dump 是崩潰瞬間的記憶體快照（暫存器/堆疊/變數/signal）——「崩潰現場的照片」
- 啟用：ulimit -c unlimited + 設定 core_pattern；生產環境要主動啟用（否則崩潰沒現場）
- gdb 分析 core：`bt`（崩潰在哪、呼叫鏈，最重要）+ `print 變數`（為什麼崩潰）；要 -g
- signal 觸發 core：SIGSEGV（記憶體）/SIGABRT（abort/記憶體損壞）/SIGFPE（算術）；退出碼 128+N
- core 讓你 debug「已死掉的程式」——不用重現、不加 printf，對難重現的崩潰是唯一線索

## 自我檢核

- [ ] 知道 core dump 是什麼，怎麼啟用和產生
- [ ] 會用 gdb 開 core，用 bt/print 找崩潰點和原因
- [ ] 知道哪些 signal 產生 core，退出碼怎麼對應 signal
- [ ] 理解 core 對 debug 難重現崩潰的價值
- [ ] 能用 core + gdb 找崩潰根因（不重現、不加 printf）

## 延伸閱讀

### 文章

- **[Core dump 完整指南](https://www.gnu.org/software/libc/manual/html_node/Program-Error-Signals.html) + [How to analyze core dumps](https://opensource.com/article/20/8/linux-systemd-coredumpctl)** — 各種
  - **這篇說什麼**：core dump 的啟用、產生、用 gdb/coredumpctl 分析
  - **為什麼值得讀**：本章的實戰擴充

### 官方文件

- **[core(5) man page](https://man7.org/linux/man-pages/man5/core.5.html)** — Linux man-pages
  - **讀哪裡**：core_pattern、ulimit 的設定
  - **為什麼值得讀**：core dump 設定的權威

- **[gdb 文件](https://sourceware.org/gdb/current/onlinedocs/gdb/)** — GNU
  - **讀哪裡**：core 分析、backtrace 那節
  - **為什麼值得讀**：gdb 分析 core 的權威；gdb 課會深入

### 書籍

- **《The Art of Debugging with GDB》— core dump 章**
  - **為什麼值得讀**：用 gdb debug（含 core）的權威

Part 7（進階自製工具）和所有章節到此完成。最後是 Final Project——一個藏了 5 個 bug 的壞掉 daemon，用整套工具偵探破案，整合全課的 debug 能力。

→ [Final Project：偵探破案 — 修好壞掉的 daemon](./final-project-broken-daemon.md)
