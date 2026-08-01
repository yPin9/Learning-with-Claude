# Ch 21 — 讀懂 build system

> **目標**：學會把 build 檔當成整個專案的「地圖」來讀。build system 不只是「怎麼編」，它精確地告訴你：哪些檔案真的被編進主 binary、模組邊界在哪、開了哪些 feature（`-D`）、依賴哪些外部 library、哪些平台分支被選中。讀懂 build，你在讀第一行 code 之前就已經有了架構骨架。

> **環境**：WSL2 Ubuntu 22.04，沙包 `~/reading_code_lab/redis`（redis 7.4.0，已預建 `compile_commands.json` / tags / cscope.out）。本章所有 `make -n`、`comm`、compile_commands.json 解析都是在此真跑後照抄。

## 為什麼先讀 build，而不是先讀 code？

先講一個逆向工程的老直覺：**你要先知道 binary 是怎麼被組出來的，才知道要逆向什麼。** 讀 source 一模一樣。

一個大專案的 `src/` 目錄下往往躺著幾百個 `.c`——但其中一大票是測試 harness、獨立工具、被 `#ifdef` 關掉的平台後端、實驗性死碼。你如果一頭栽進去逐檔讀，會浪費大把時間讀根本不會進主程式的東西。build system 是唯一一份**權威清單**，回答這幾個致命問題：

```
   問題                                 build 檔在哪回答
 ┌──────────────────────────────┐    ┌────────────────────────┐
 │ 哪些 .c 真的被編進主 binary？ │ →  │ OBJ 清單 / target 依賴 │
 │ 這專案切成哪幾個模組/產物？   │ →  │ 各 target（server/cli）│
 │ 開了哪些 feature、關了哪些？  │ →  │ -D 巨集、條件分支      │
 │ 依賴哪些外部 library？        │ →  │ -l / -I / link line    │
 │ 這平台走哪條分支？            │ →  │ uname 判斷 + ifeq      │
 │ 有沒有 build 時生成的 code？  │ →  │ 生成規則（.def/.h）    │
 └──────────────────────────────┘    └────────────────────────┘
```

心智模型：**build graph 是這個專案「自己畫的架構圖」**，而且它不會騙你——註解會過期、README 會說謊，但如果某個 `.c` 沒進 build，它 100% 不在最終程式裡。這是你在陌生 codebase 裡最可靠的第一手情報。

## 各 build 系統怎麼讀：一頁速查

你會遇到的 build 系統不多，讀法各異但目標一致——**都是為了拿到那份「真實編譯指令 + 檔案清單」**。

| 系統 | 標誌檔 | 怎麼快速抓地圖 | 讀碼者要盯什麼 |
|---|---|---|---|
| **Make** | `Makefile` | `make -n`（乾跑）、讀 OBJ 變數與 target 依賴 | `_OBJ=` 清單、`%.o: %.c` 規則、`ifeq`/`ifdef` 分支 |
| **CMake** | `CMakeLists.txt` | `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`，讀 `compile_commands.json` | `add_executable`/`add_library` 的 source 清單、`target_compile_definitions`、`option()` |
| **autotools** | `configure` / `configure.ac` / `Makefile.am` | 跑 `./configure` 後讀生成的 `config.h` + `Makefile` | `config.h` 裡的 `HAVE_*`、`AC_CHECK_*`、`_SOURCES=` |
| **Ninja** | `build.ninja`（多半機器生成） | 別手讀原始 ninja；`ninja -t commands <target>` 印真實指令、`ninja -t graph` 印依賴圖 | 它是 CMake/Meson/GN 的後端，往上找生成它的那層 |
| **Meson** | `meson.build` | `meson setup build` 後 `meson introspect --targets`、讀 compile_commands.json | `executable()`/`library()` 的 source list、`configuration_data` |
| **Bazel** | `BUILD` / `BUILD.bazel` | `bazel query 'deps(//target)'`、`bazel aquery`（看實際 action 指令） | `cc_library`/`cc_binary` 的 `srcs`/`deps`、`select()` 條件 |

三個通則：

1. **不要把生成物當手寫檔讀。** `build.ninja`、autotools 生成的 `Makefile`、CMake 的 `CMakeCache.txt` 都是機器產物，往上找它們的「來源」（`CMakeLists.txt` / `meson.build`）才是人類意圖所在。
2. **`compile_commands.json` 是共通貨幣。** 不管上游是哪個系統，只要能吐出這份 compilation database，你就有了每個編譯單元的真實旗標——這也是 clangd（Ch 13）與很多分析工具的地基。
3. **能乾跑就乾跑。** `make -n` / `ninja -t commands` / `bazel aquery` 都能在**不真的編譯**的前提下印出「將要執行的指令」。這是讀 build 最省事的一招：不用逆推變數展開，直接看展開後的真相。

## 實戰一：讀 redis 的 Makefile——先抓「產物清單」

redis 用手寫 Makefile（`src/Makefile`）。讀手寫 Makefile 的第一步不是從頭讀到尾，而是**直接跳到 target 與 OBJ 變數**——那裡濃縮了整個架構。

redis 定義了幾個產物（真實節錄，`src/Makefile:355-361`）：

```make
REDIS_SERVER_NAME=redis-server$(PROG_SUFFIX)
REDIS_SERVER_OBJ=threads_mngr.o adlist.o quicklist.o ae.o anet.o dict.o ... server.o ... commands.o strl.o connection.o unix.o logreqres.o
REDIS_CLI_NAME=redis-cli$(PROG_SUFFIX)
REDIS_CLI_OBJ=anet.o adlist.o dict.o redis-cli.o zmalloc.o ... cli_commands.o
REDIS_BENCHMARK_NAME=redis-benchmark$(PROG_SUFFIX)
REDIS_BENCHMARK_OBJ=ae.o anet.o redis-benchmark.o adlist.o dict.o ...
```

光是這三行 OBJ 清單就洩漏了大量架構資訊：

- **`redis-server`** 吃了 ~98 個 `.o`——這是主體。`server.o`、`networking.o`、`db.o`、`replication.o`、`rdb.o`、`cluster.o`、`t_string.o`/`t_list.o`/`t_set.o`/...（各資料型別一個檔）。你還沒讀 code，就知道 redis 按「資料型別 + 子系統」切檔。
- **`redis-cli`** 與 **`redis-benchmark`** 是獨立小程式，共用了 `anet.o`（網路）、`adlist.o`（雙向鏈表）、`dict.o`（雜湊表）、`crc*`、`siphash.o`——這些是**共用基礎設施**，被三個產物都吃進去。看到一個 `.o` 出現在多個 target，它八成是底層 util。
- `redis-sentinel` / `redis-check-rdb` / `redis-check-aof` 不是獨立編譯——它們是 `redis-server` 的複本（`$(REDIS_INSTALL)` 複製同一個 binary，靠 argv[0] 分辨身份）。這是讀 build 才看得出的設計：**一個 binary，多重人格**。

## 實戰二：`make -n` 乾跑——看真實編譯指令

讀 Makefile 最怕的是變數層層展開（`FINAL_CFLAGS=$(STD) $(WARN) $(OPT)...`），你手動追會追到眼花。**別追，讓 make 幫你展開**：

```
$ cd ~/reading_code_lab/redis/src
$ touch server.c              # 讓 server.o 過期，才會出現在乾跑輸出
$ make -n redis-server
```

真實輸出（節錄編譯 `server.c` 那一行，去掉終端上色的 printf 前綴）：

```
cc -pedantic -DREDIS_STATIC= -std=gnu11 -Wall -W -Wno-missing-field-initializers \
   -Werror=deprecated-declarations -Wstrict-prototypes -O3 -flto=auto \
   -fno-omit-frame-pointer -g -ggdb \
   -I../deps/hiredis -I../deps/linenoise -I../deps/lua/src -I../deps/hdr_histogram \
   -I../deps/fpconv -DUSE_JEMALLOC -I../deps/jemalloc/include \
   -MMD -o server.o -c server.c
```

這一行把所有前面追不完的變數**攤平**成事實。讀碼者從中讀到：

- **`-DUSE_JEMALLOC` + `-I../deps/jemalloc/include`**：這份 build 用 jemalloc 當 allocator（不是 libc malloc）。所以你讀到 `zmalloc.c` 時要知道它底下接的是 jemalloc；`je_*` 符號從哪來、為什麼有 `MALLOC=jemalloc` 的條件都對上了。
- **`-std=gnu11`**：用 GNU C11，所以 code 裡會出現 `_Atomic`、匿名 struct 等 C11/GNU 擴充。這解釋了為什麼某些寫法「看起來不像標準 C」。
- **`-flto=auto`**：開了 LTO（link-time optimization）。這對讀碼影響不大，但對「為什麼 gdb 裡某些函式被 inline 掉了」有解釋力。
- 依賴的 `deps/`：hiredis、lua、hdr_histogram、fpconv、jemalloc——外部 library 一覽無遺。

link 那一行同樣攤平（真實輸出節錄）：

```
cc -O3 -flto=auto ... -rdynamic -o redis-server threads_mngr.o adlist.o ... logreqres.o \
   ../deps/hiredis/libhiredis.a ../deps/lua/src/liblua.a \
   ../deps/hdr_histogram/libhdrhistogram.a ../deps/fpconv/libfpconv.a \
   ../deps/jemalloc/lib/libjemalloc.a -lm -ldl -pthread -lrt
```

`-rdynamic`（匯出動態符號，redis module 系統要用）、link 進去的 4 個靜態 lib + `-lm -ldl -pthread -lrt`——**這就是 redis-server 的完整成分表**。`-pthread` + `-lrt` 告訴你它用了 POSIX thread 與 realtime（定時器 / `clock_gettime`）。

> **`make -n` 的表親**：`make --trace` 會多印「為什麼這條規則被觸發」（哪個 prerequisite 過期），對搞不清楚「為什麼這檔會重編」時很有用。`make -p` 印出 make 內部的完整資料庫（所有變數與規則的最終值），是暴力但徹底的除錯手段。

## 實戰三：compile_commands.json——每個檔的真實旗標

`make -n` 給你「將要跑的指令」，但它是文字流。若要**程式化**地問「server.c 到底用哪些 `-D`」，讀那份 `bear` 側錄出來的 `compile_commands.json`：

```
$ cd ~/reading_code_lab/redis
$ python3 -c '
import json
d = json.load(open("compile_commands.json"))
print("編譯單元數:", len(d))
e = [x for x in d if x["file"].endswith("/server.c")][0]
print("keys:", list(e.keys()))
print("dir :", e["directory"])
flags = [a for a in e["arguments"] if a.startswith(("-D","-I"))]
print("D/I :", flags)'
```

真實輸出：

```
編譯單元數: 275
keys: ['arguments', 'directory', 'file', 'output']
dir : /home/ypp/reading_code_lab/redis/src
D/I : ['-DREDIS_STATIC=', '-I../deps/hiredis', '-I../deps/linenoise', '-I../deps/lua/src', '-I../deps/hdr_histogram', '-I../deps/fpconv', '-DUSE_JEMALLOC', '-I../deps/jemalloc/include']
```

**275 個編譯單元**——比主 binary 的 ~98 個多得多，因為它側錄了整個 `make` 過程：deps、tests、工具全算進去。這個數字本身就是情報：`ls src/*.c` 只有 107 個，但 build 過程碰了 275 個 TU，代表大量 code 藏在 `deps/` 與 `tests/`。

實務價值：clangd 靠這份檔才能對每個檔案用**正確的旗標**做語意分析。若你讀某段 code 被 `#ifdef USE_JEMALLOC` 包住，clangd 因為知道這個 `-D` 有開，會把那段標成 active（不是灰掉的 dead branch）——這是純文字工具（ripgrep）做不到的（Ch 13 展開）。

> 注意 CDB 每筆可能是 `command`（單一字串）或 `arguments`（陣列）形式，兩者擇一。redis 這份是 `arguments`；用 `bear` 較新版本側錄出的多為 `arguments`。寫解析腳本時兩種都要處理（見延伸閱讀的格式規格）。

## 實戰四：找出「哪些 .c 其實沒被編進去」

這是讀 build 最實用的一招：**排除法**。`src/` 下 107 個 `.c`，但主 binary 的 OBJ 清單只列了一部分。差集就是「你暫時可以不讀」的檔——但差集裡也埋著陷阱，正好教你 build 的一個非直覺點。

```
$ cd ~/reading_code_lab/redis/src
$ ls *.c | wc -l
107
$ comm -23 \
    <(ls *.c | sed 's/\.c//' | sort) \
    <(grep -hE '^REDIS_(SERVER|CLI|BENCHMARK)_OBJ=' Makefile \
        | tr ' ' '\n' | grep '\.o$' | sed 's/\.o//' | sort -u)
ae_epoll
ae_evport
ae_kqueue
ae_select
threads_mngr
```

（`threads_mngr` 是 `comm` 排序邊界造成的假陽性——它其實在 `REDIS_SERVER_OBJ` 開頭。真正有意思的是那四個 `ae_*`。這也順帶提醒你：機械化的差集會有雜訊，結論一定要回頭驗。）

這四個 `ae_epoll.c` / `ae_kqueue.c` / ... **沒有**出現在任何 OBJ 清單，但它們絕對被編進了 redis——怎麼回事？看 `ae.c`（真實節錄 `ae.c:30-42`）：

```c
/* Include the best multiplexing layer supported by this system.
 * The following should be ordered by performances, descending. */
#ifdef HAVE_EVPORT
#include "ae_evport.c"
#else
    #ifdef HAVE_EPOLL
    #include "ae_epoll.c"        // ← Linux 走這條
    #else
        #ifdef HAVE_KQUEUE
        #include "ae_kqueue.c"   // ← macOS/BSD
        #else
        #include "ae_select.c"   // ← fallback
        #endif
    #endif
#endif
```

**它們是被 `#include` 進 `ae.c` 的**，不是獨立編譯單元。這是 redis 選 event backend 的手法：編譯期靠平台巨集選一個 `.c` 直接 `#include`。讀碼啟示有兩層：

1. 「不在 OBJ 清單」≠「死碼」。C 允許 `#include *.c`，build 清單只列了頂層 TU。你要交叉比對 `#include` 才知道全貌。
2. 你在 Linux 上讀 redis 的 event loop，只需要讀 `ae_epoll.c`——另外三個是別的平台的分支，先跳過。**build（平台巨集）幫你砍掉了 3/4 的閱讀量。**

## 實戰五：build 時生成的 code——別找一個不存在的檔

最後一個坑：有些 `.c`/`.h` 是 build **當場生成**的，你在 git repo 裡搜不到、或搜到的是模板。redis 的命令定義就是這樣（`src/Makefile:453-459` 真實節錄）：

```make
ifneq (,$(PYTHON))
$(COMMANDS_DEF_FILENAME).def: commands/*.json ../utils/generate-command-code.py
	$(QUIET_GEN)$(PYTHON) ../utils/generate-command-code.py $(GEN_COMMANDS_FLAGS)

fmtargs.h: ../utils/generate-fmtargs.py
	...
endif

commands.c: $(COMMANDS_DEF_FILENAME).def
```

也就是：`commands.def`（被 `commands.c` `#include`）是由 `utils/generate-command-code.py` 讀 `src/commands/*.json`（還記得 Ch 0 `cloc` 看到的那 401 個 JSON 嗎？）**生成**出來的。如果你在讀「`GET` 命令的參數規格從哪來」，直接搜 `commands.c` 只會看到一堆 `#include`——真正的定義在**生成流程**裡：JSON → python → `.def`。

讀碼啟示：**當你在 source 裡找不到某個看起來一定存在的定義，先懷疑它是生成的。** 看 build 檔裡有沒有「跑 script 產出 `.c`/`.h`」的規則，順著 script 與它的輸入（這裡是 JSON）去讀，才找得到源頭。這是 code generation（X-macro 的親戚，Ch 22 詳談）在 build 層的體現。

## 對比與取捨

| 抓 build 資訊的手段 | 給你什麼 | 代價 / 限制 |
|---|---|---|
| 肉眼讀 Makefile/CMakeLists | 意圖、模組結構、條件邏輯 | 變數展開要腦補；大檔費時 |
| `make -n` / `ninja -t commands` | **攤平後的真實指令**，零腦補 | 只印「會跑的」；已是最新則不印（需 `touch`） |
| `compile_commands.json` | 每 TU 的精確旗標，可程式化查詢 | 要先跑一次 build（bear/CMake export）產生 |
| `bear -- make` 側錄 | 對任何怪 build 系統都能生出 CDB | 得真的能編過；慢 |
| OBJ/target 差集分析 | 快速砍掉「不進 binary」的檔 | 會被 `#include *.c`、生成檔騙（如上兩例） |

**策略**：拿到專案先 `make -n`（或對應乾跑）看一個檔的真實編譯指令，抓 `-D`/`-I`/依賴；再讀 OBJ/target 清單抓模組邊界；需要精準時產 `compile_commands.json` 餵 clangd。**讀 build 的順序是「攤平指令 → 產物清單 → 條件分支 → 生成規則」，由具體到抽象。**

## 踩雷集錦

1. **錯誤直覺：「`src/` 下的 `.c` 都是程式的一部分」。** 正確認識：一大票是測試 harness、獨立工具、其他平台的後端。**唯一權威是 build 的 OBJ/target 清單**，不是目錄列表。redis 的 `ae_epoll.c` 反過來證明「不在清單也可能被 `#include` 編進去」——兩個方向都要驗。

2. **錯誤直覺：「Makefile 裡寫的旗標就是實際用的」。** 正確認識：條件分支（`ifeq`/`ifdef`）、平台判斷（`uname_S`/`uname_M`）、可覆寫變數（`OPTIMIZATION?=`）讓最終旗標高度依環境。**別讀原始碼推，跑 `make -n` 看攤平結果**。redis 的 `MALLOC` 在 Linux 是 jemalloc、其他平台是 libc，光讀不跑會猜錯。

3. **錯誤直覺：「找不到某定義 = 專案有 bug / 我搜錯了」。** 正確認識：它可能是 build 時生成的。redis 的 `commands.def`、`fmtargs.h`、`release.h`（`mkreleasehdr.sh` 生成）在乾淨 checkout 裡根本不存在，編一次才出現。**先查 build 有沒有生成規則。**

4. **錯誤直覺：「`build.ninja` / 生成的 `Makefile` 就是該讀的 build 檔」。** 正確認識：那是機器產物，難讀且不反映人類意圖。往上找 `CMakeLists.txt` / `meson.build` / `configure.ac`。讀生成物只在「懷疑生成器有 bug」時才做。

5. **錯誤直覺：「compile_commands.json 有 275 筆 = 主程式有 275 個檔」。** 正確認識：那是**整個 build 過程**（含 deps/tests/工具）碰過的所有 TU，遠多於主 binary。要算主程式規模，看 OBJ 清單（~98），不是 CDB 總數。

## 進階：再往深一層

- **`compile_commands.json` 的兩種來源之差**：CMake `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` 是「宣告式」——CMake 知道每個 target 的旗標，直接吐出，準確且含所有 TU（即使沒真編）。`bear` 是「側錄式」——攔 `exec` 系統呼叫記下每次 `gcc`，只錄到**實際跑過**的編譯，若某檔因為已 up-to-date 沒重編就漏掉（所以側錄前常先 `make clean`）。讀碼者要知道自己手上這份是哪種來的，才知道它完不完整。

- **Bazel 的 `aquery` 才是真相**：Bazel 的 `BUILD` 檔高度宣告式，`cc_library` 的 `copts`/`defines` 加上 `select()` 條件、toolchain、`--config` 疊起來後，最終旗標很難靠讀 `BUILD` 推出。`bazel aquery 'mnemonic("CppCompile", //your:target)'` 會印出**實際 action 的完整命令列**——等同 Bazel 世界的 `make -n`。遇到 Bazel 專案，先學會 `query`/`aquery`，不要硬啃 `BUILD`。

- **autotools 的 `config.h` 是 feature 開關的總表**：autotools 專案（如很多 GNU 工具）跑完 `./configure` 會生成 `config.h`，裡面一堆 `#define HAVE_XXX 1` / `/* #undef HAVE_YYY */`。**這份檔是「這台機器上哪些 feature 有」的權威快照**，讀懂它就知道成堆 `#ifdef HAVE_*` 分支哪條 active。讀 autotools 專案別去啃 `configure`（那是 m4 生成的兩萬行 shell 地獄），直接讀 `configure.ac`（人寫的）與生成的 `config.h`。

- **反查「這個 `-D` 開了會改變哪些 code」**：拿到 `-DUSE_JEMALLOC` 後，`rg 'USE_JEMALLOC' src/` 就能列出所有受這個 feature 影響的分支點。把「build 開的 feature」與「code 裡的 `#ifdef`」連起來，你就能精準判斷「這份 build 下，哪些程式碼路徑是活的」。這是 build 讀法與巨集讀法（Ch 22）的交界。

## 動手練習

1. **攤平一條編譯指令**：在 redis 沙包 `touch src/networking.c` 後 `make -n redis-server | grep networking.c`，把展開後的 `-D`/`-I`/`-std` 全部列出，說出每個 feature 巨集的意義。
2. **產物差集**：用 `comm` 比對 `ls src/*.c` 與 `REDIS_SERVER_OBJ` 清單，找出「不在 server 但在 cli/benchmark」的檔，說明為什麼（提示：cli 專屬的 `cli_common.c`、`cli_commands.c`）。
3. **追一個生成檔**：從 `src/Makefile` 找到 `commands.def` 的生成規則，順著 `utils/generate-command-code.py` 與 `src/commands/GET.json`，說出「`GET` 命令的 arity 定義最終如何進到 binary」。
4. **抓平台分支**：用 `rg 'HAVE_EPOLL|HAVE_KQUEUE|HAVE_EVPORT' src/` 找出 event backend 的所有分支點，判斷你這台（Linux）走哪個 `ae_*.c`，確認另外三個可以不讀。
5. **（選）換系統練手**：clone 一個 CMake 專案（如 `fmt` 或 `spdlog`），`cmake -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`，讀 `build/compile_commands.json` 抓出它的 `-D` 與 include 路徑，對照 `CMakeLists.txt` 的 `target_compile_definitions`。

## 本章重點整理

- build system 是專案「自己畫的架構圖」：哪些檔進 binary、模組邊界、feature 開關、外部依賴、平台分支，全在裡面，而且不會像註解那樣過期。
- 讀 build 的四步：**攤平真實指令（`make -n`）→ 讀產物 / OBJ 清單抓模組 → 看條件分支抓平台 / feature → 找生成規則抓隱藏 code**。
- `compile_commands.json` 是跨 build 系統的共通貨幣，也是 clangd 精準分析的地基；分清它是 CMake 宣告式吐的還是 bear 側錄的。
- 兩個非直覺陷阱：不在 OBJ 清單的檔可能被 `#include`（redis `ae_epoll.c`）；找不到的定義可能是 build 時生成的（redis `commands.def`）。
- 別讀機器生成的 build 檔（`build.ninja` / autotools 的 `Makefile`），往上找人寫的來源；Bazel 用 `aquery` 看真相。

## 自我檢核

- [ ] 拿到一個陌生 C 專案，我能不能在 5 分鐘內用一條指令印出「某個檔的真實編譯旗標」？（`make -n` / `ninja -t commands` / 讀 CDB）
- [ ] redis 的 `redis-sentinel` 是獨立 binary 嗎？我能從 build 檔說出它跟 `redis-server` 的關係嗎？
- [ ] 為什麼 `src/` 有 107 個 `.c`，但 `ae_epoll.c` 不在任何 OBJ 清單裡卻仍被編進 redis？
- [ ] 我知道 `compile_commands.json` 的 275 筆為什麼比主 binary 的 ~98 個檔多嗎？
- [ ] 遇到 Bazel 專案，我第一個下的指令是什麼、為什麼不直接讀 `BUILD`？

## 延伸閱讀

- **[GNU Make Manual — "Instead of Executing Recipes"（`-n`/`-t`/`-p`）](https://www.gnu.org/software/make/manual/html_node/Instead-of-Execution.html)**
  - **讀哪裡**：`--just-print`（`-n`）、`--print-data-base`（`-p`）、`--trace` 三節。
  - **學到什麼**：怎麼在不真編的前提下逼 make 吐出真相；`-p` 看變數最終值。讀手寫 Makefile 的必備乾跑技巧，本章實戰二三的原理。
  - **前提**：知道 make 的 target / prerequisite / recipe 三元結構。

- **[clangd — JSON Compilation Database 格式規格（LLVM 文件）](https://clang.llvm.org/docs/JSONCompilationDatabase.html)**
  - **讀哪裡**：整份很短，重點看 `command` vs `arguments` 兩種欄位形式、`directory` 的意義。
  - **學到什麼**：`compile_commands.json` 每一筆的精確語意（本章 redis 這份是 `arguments` 形式）。理解它才知道 clangd / clang-tidy / 各種靜態分析為何都要這份檔。
  - **前提**：懂 `-I`/`-D`/`-c` 這些基本編譯旗標。

- **[Bazel — "Action Graph Query (aquery)" 文件](https://bazel.build/query/aquery)**
  - **讀哪裡**：`aquery` 的 `mnemonic()` 過濾範例，以及 `bazel query 'deps(//target)'`。
  - **學到什麼**：在高度宣告式的 Bazel 世界裡如何拿到「實際跑的編譯指令」與依賴圖——等同 Bazel 版的 `make -n`。遇到 Google 系開源（gRPC、Envoy、部分 TensorFlow）必用。
  - **前提**：知道 `cc_library`/`cc_binary`/`srcs`/`deps` 的基本概念。

- **[Autotools Mythbuster — `configure.ac` 與 `config.h` 章節](https://autotools.io/index.html)**
  - **讀哪裡**："Configuration" 一章講 `AC_CHECK_*` 如何生成 `HAVE_*` 巨集、寫進 `config.h`。
  - **學到什麼**：讀 autotools 專案時「別啃 configure、去讀 configure.ac + config.h」的原理與實作。理解成堆 `#ifdef HAVE_*` 的來源。
  - **前提**：碰過至少一個 `./configure && make` 專案。

讀懂了 build 這張地圖，你知道哪些 code 是活的、開了哪些 feature。但當你打開那些活的 `.c`，會發現真正被編譯的東西常常「藏在巨集後面」——你看到的 `serverAssert(...)` 展開後是完全不同的一串 code。下一章我們拿起 `gcc -E` 這把手術刀，把 preprocessor 的障眼法一層層剝開。

→ [Ch 22 讀懂巨集與 metaprogramming](./22-reading-macros-metaprogramming.md)
