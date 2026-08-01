# Ch 5 — 第一次接觸：60 分鐘偵察

> **目標**：拿到一個完全陌生的 repo，先用 60 分鐘做「不讀任何一行邏輯」的系統化偵察，建立起這專案「多大、什麼組成、怎麼編、入口在哪、依賴什麼、測試在哪、還活著嗎」的骨架印象。這一小時決定你接下來十小時的效率——偵察沒做，你會一頭栽進錯的檔案裡。本章在 redis 上完整走一遍，並交給你一份可複用的「偵察筆記」模板。

## 為什麼是「偵察」，不是「讀」？

先破除一個新手的直覺：**拿到陌生 codebase 的第一件事不是打開 `main` 開始讀。**

這跟逆向一個陌生 binary 的道理一模一樣。你逆一個沒看過的執行檔，不會一上來就 `objdump -d` 從 entry point 一條指令一條指令追。你會先做偵察：`file` 看它是什麼、`strings` 掃可讀字串、`readelf -h` 看架構與段、`checksec` 看防護。這些動作**沒有讀任何一條指令**，卻在五分鐘內告訴你「這是 x86-64 動態連結、開了 PIE、裡面有 OpenSSL 字串、可能是個網路服務」——你已經知道這是什麼類型的目標、該用哪套打法。

讀 source 的第一小時是同一件事。你要回答的不是「這段邏輯在幹嘛」，而是**「這是什麼類型的目標，我該用哪套讀法」**：

```
   binary RE 偵察              source 偵察（本章）
 ┌──────────────────┐       ┌────────────────────────┐
 │ file             │  ↔    │ README / cloc（是什麼、多大）│
 │ readelf -S       │  ↔    │ 目錄結構（分幾層、怎麼組織）  │
 │ entry point      │  ↔    │ main / entrypoint（從哪跑起）│
 │ 動態連結 needed   │  ↔    │ deps / vendor（依賴誰）      │
 │ symbols/strings  │  ↔    │ 命名慣例 / grep（洩漏架構）   │
 │ 有沒有 debug info │  ↔    │ 註解比 / 文件品質（好不好讀） │
 └──────────────────┘       └────────────────────────┘
```

偵察的產出不是「懂了」，是**一張讓你能決定優先序的地圖**。60 分鐘結束時你應該能講出：這專案哪三個檔最重要、我下一步要精讀哪個、哪些目錄可以先無視。

## 為什麼要計時？因為時間逼出優先序

我堅持給偵察設 60 分鐘硬上限，理由是**不設限你會無限發散**。陌生 codebase 有無窮多的兔子洞，每個檔案都可以往下追一小時。計時做的事，是把你從「我想搞懂一切」逼回「我這一小時最該搞懂什麼」。

這是速度技能的核心心法，跟 CTF 限時打靶一樣：**先廣後深，廣度優先**。第一小時只鋪一層薄薄的全景，哪裡都不深入。哪裡值得深入，是偵察結束後拿著全景圖再決定的。新手最常犯的錯，是在第一小時就跌進某個看起來很關鍵的函式，追了 40 分鐘 call chain，最後發現那根本是邊角料——因為他沒有全景，無從判斷什麼是邊角料。

一個實務的時間分配（可依專案調整）：

```
 0–10 min   README / docs / 專案自我介紹    → 這是什麼、給誰用
10–20 min   build system                    → 怎麼編、產出什麼 binary
20–30 min   目錄結構 + cloc 規模體檢         → 多大、什麼語言、怎麼組織
30–40 min   進入點 + 依賴                    → 從哪跑起、依賴誰
40–50 min   測試 + git 活躍度                → 品質、還活著嗎、哪裡是關鍵
50–60 min   寫偵察筆記                       → 外化，決定下一步深讀哪裡
```

計時不是要你趕，是要你**在每一格結束時強迫抬頭問「我拿到我要的骨架了嗎？沒有就停損，往下一格」**。

下面我們在 redis 7.4.0（沙包 `~/reading_code_lab/redis`）上，把這 60 分鐘一格一格走一遍，全部真跑。

## 第 1 格（0–10 min）：README 與專案自我介紹

第一步永遠是讓專案自己介紹自己。看 `README`、`docs/`、`CONTRIBUTING`、`MANIFESTO` 之類的頂層文件——**只讀開頭幾段和標題**，抓「這是什麼、給誰、解決什麼問題」，不細讀。

先看頂層有哪些自我介紹文件：

```
$ ls -la ~/reading_code_lab/redis
...
-rw-r--r--  1 ypp ypp     9854 00-RELEASENOTES
-rw-r--r--  1 ypp ypp       51 BUGS
-rw-r--r--  1 ypp ypp     5023 CODE_OF_CONDUCT.md
-rw-r--r--  1 ypp ypp     7178 CONTRIBUTING.md
-rw-r--r--  1 ypp ypp    37493 LICENSE.txt
-rw-r--r--  1 ypp ypp     6888 MANIFESTO
-rw-r--r--  1 ypp ypp      151 Makefile
-rw-r--r--  1 ypp ypp    23845 README.md
-rw-r--r--  1 ypp ypp     1480 SECURITY.md
-rw-r--r--  1 ypp ypp     3628 TLS.md
-rw-r--r--  1 ypp ypp   108981 redis.conf
...
```

光是這份清單就洩漏很多：有 `CONTRIBUTING.md`（歡迎外部貢獻，社群活躍）、`SECURITY.md`（有正式的漏洞回報流程，這對安全研究者是重要訊號）、`MANIFESTO`（作者有明確設計哲學，值得後面回頭讀）、`redis.conf` 十萬字組態（可組態性極高的服務）。

讀 README 前 20 行：

```
$ head -20 README.md
This README is just a fast *quick start* document...

What is Redis?
---

Redis is often referred to as a *data structures* server. What this means is
that Redis provides access to mutable data structures via a set of commands,
which are sent using a *server-client* model with TCP sockets and a simple
protocol...
```

一句話定性完成：**「data structures server，server-client 模型，TCP socket，自訂協定」**。你還沒讀一行 code，就已經知道這是個網路服務、核心賣點是資料結構、有自己的 wire protocol。這決定了你後面找 entry point 時要找的是「daemon/server 型」入口（Ch 6），要留意的核心是「資料結構的實作」與「協定解析」。

redis 的 README 還有一個罕見的寶藏——它自帶「Source code layout」章節，直接告訴你原始碼怎麼組織：

```
$ sed -n '268,290p' README.md
* `src`: contains the Redis implementation, written in C.
* `tests`: contains the unit tests, implemented in Tcl.
* `deps`: contains libraries Redis uses. Everything needed to compile Redis
   is inside this directory... Notably `deps` contains a copy of `jemalloc`...
```

**維護者親自寫的架構導覽是偵察期最高價值的文件**，遇到一定精讀。多數專案沒這麼好命，那你就得靠後面幾格自己推。

> 讀 README 的紀律：**抓定性、抓組織、抓 build 指令，不細讀教學內容**。README 常常混了大量「怎麼使用」的內容（redis README 後半在講怎麼跑、怎麼 TLS），那是給使用者的，對讀碼者是雜訊，掃過即可。

## 第 2 格（10–20 min）：build system——怎麼編、產出什麼

**build system 是被嚴重低估的偵察資源。** 它是唯一一份「絕不會騙你」的文件——README 可能過期、註解可能誤導，但 build 檔必須真的能編出東西，所以它精確描述了「哪些檔組成哪個產物」。這對「找入口」（Ch 6）和「畫架構圖」（Ch 7）都是黃金原料。

先認 build 系統類型。看頂層：

```
$ cat Makefile
# Top level makefile, the real shit is at src/Makefile

default: all

.DEFAULT:
	cd src && $(MAKE) $@
...
```

頂層 Makefile 直接告訴你「真正的東西在 `src/Makefile`」（維護者的註解很誠實）。這是 Makefile 專案（不是 CMake/Bazel），編法就是 `make`。README 也證實：

```
It is as simple as:
    % make
```

接著看 `src/Makefile` 最關鍵的部分——**它產出哪些 binary，各由哪些 `.o` 組成**：

```
$ grep -nE 'REDIS_SERVER_NAME|REDIS_SERVER_OBJ|REDIS_CLI_OBJ|^all:' src/Makefile
355:REDIS_SERVER_NAME=redis-server$(PROG_SUFFIX)
357:REDIS_SERVER_OBJ=threads_mngr.o adlist.o quicklist.o ae.o anet.o dict.o
   ebuckets.o mstr.o kvstore.o server.o sds.o zmalloc.o ... networking.o
   util.o object.o db.o replication.o rdb.o t_string.o t_list.o t_set.o
   t_zset.o t_hash.o config.o aof.o pubsub.o multi.o ... cluster.o ...
359:REDIS_CLI_OBJ=anet.o adlist.o dict.o redis-cli.o zmalloc.o ...
366:all: $(REDIS_SERVER_NAME) $(REDIS_SENTINEL_NAME) $(REDIS_CLI_NAME) \
   $(REDIS_BENCHMARK_NAME) $(REDIS_CHECK_RDB_NAME) $(REDIS_CHECK_AOF_NAME)
```

這幾行的資訊量巨大：

- **這專案編出 6 個 binary**（server、sentinel、cli、benchmark、check-rdb、check-aof）——`all:` 那行列完。這解釋了 Ch 0 「為什麼有 8 個 `main`」的一半——多個產物各自有入口。
- **`redis-server` 由 `REDIS_SERVER_OBJ` 那一長串組成**，第一個關鍵字裡就有 `server.o`——所以 `server.c` 幾乎肯定是主入口所在（Ch 6 會坐實）。
- **`REDIS_SERVER_OBJ` 那串 `.o` 名稱本身就是一張模組清單**：`networking`、`replication`、`rdb`、`aof`、`t_string`/`t_list`/`t_set`/`t_zset`/`t_hash`（資料型別）、`cluster`、`pubsub`、`multi`……。你連目錄都還沒細看，build 檔已經把 redis 的子系統列給你了。這是 Ch 7 架構圖的原料。

> **偵察技巧**：看到 build 檔裡把 `.o` 分組成不同 binary，馬上把「哪個 binary 由哪些檔組成」抄下來。這是後面「哪個 `main` 是真的」以及「這檔屬於哪個產物」最可靠的依據——比讀任何文件都準。

## 第 3 格（20–30 min）：cloc 規模體檢 + 目錄結構

現在量化「這專案有多大、什麼語言、註解勤不勤」。`cloc`（count lines of code）一條指令搞定：

```
$ cloc --quiet src/
---------------------------------------------------------------------------
Language                     files      blank     comment       code
---------------------------------------------------------------------------
C                              115      15755       32001      100023
JSON                           401          2           0       24565
C/C++ Header                    69       1220        3045        8643
D                              103          0           0         536
make                             2        100          72         425
Ruby                             1         13           3         113
---------------------------------------------------------------------------
SUM:                           694      19189       35121      143472
---------------------------------------------------------------------------
```

三個數字定生死：

- **十萬行 C**（`code` 欄 100023）——中型專案，一個人幾週能摸熟核心，不是 Linux kernel 那種幾百萬行的怪物。
- **註解 32001 行，比程式碼約 1:3**——註解勤勞，這是好讀的訊號（對照很多商業 codebase 註解比 1:20）。
- **401 個 JSON、24565 行**——反常。純網路服務不該有這麼多 JSON。這是個「值得記下來、之後查」的異常（答案：`src/commands/` 下每個指令一份 JSON 定義檔，是產生指令表的元資料；偵察期不必深追，記下即可）。

再看整個 repo（含 deps、tests）：

```
$ cloc --quiet .
---------------------------------------------------------------------------
Language                     files      blank     comment       code
---------------------------------------------------------------------------
C                              423      29387       42142      178720
Tcl/Tk                         211       8460        4731       47757
JSON                           402          2           0       32762
C/C++ Header                   298       5429       11094       30117
Bourne Shell                    75       3140        1628       17348
...
```

多了兩件事：**Tcl 47757 行**（`tests/` 全用 Tcl 寫，這是 redis 的測試語言，第 5 格會回到）、**C 從 10 萬跳到 17.8 萬**（多出來的 7 萬多在 `deps/`，是 vendored 的 jemalloc/lua/hiredis 等第三方庫）。這告訴你一件重要的事：**你要讀的「redis 本體」是 `src/` 那十萬行，deps 那 7 萬行是別人的庫，先別碰。** 這個切分讓你的閱讀範圍立刻縮小。

接著掃目錄結構——**命名慣例會洩漏架構**：

```
$ ls src/t_*.c
src/t_hash.c  src/t_list.c  src/t_set.c  src/t_stream.c
src/t_string.c  src/t_zset.c
```

`t_` 前綴一看就懂：**t = type**，每個檔是一種 Redis 資料型別的實作。你根本不用讀內容，命名就把「redis 支援哪些資料型別、各自實作在哪」全交代了。同理你會在 `src/` 掃到 `cluster*.c`（叢集）、`rdb.c`/`aof.c`（兩種持久化）、`networking.c`/`anet.c`/`connection.c`（網路層）。**檔名前綴與分組是維護者留下的、免費的模組地圖**，偵察時務必掃過一輪。

## 第 4 格（30–40 min）：進入點 + 依賴

進入點的完整方法論是 Ch 6，這裡偵察只做「快速定位候選」。對 C 專案就是找 `main`：

```
$ rg -n "int main" src/*.c
src/server.c:6917:int main(int argc, char **argv) {
src/redis-cli.c:10572:int main(int argc, char **argv) {
src/redis-benchmark.c:1694:int main(int argc, char **argv) {
src/setproctitle.c:323:int main(int argc, char *argv[]) {
src/crc64.c:157:int main(int argc, char *argv[]) {
...
```

8 個 `main`。偵察期你不需要全部搞懂，只要用第 2 格的 build 知識做一次交叉比對：`REDIS_SERVER_OBJ` 裡有 `server.o`，且 README 定性它是 server——所以 **`src/server.c:6917` 是主入口**，其餘暫時歸類為「工具程式或自測」，先記下不追。Ch 6 會把這個推理做嚴謹。

依賴看兩個地方：**vendored（塞在 repo 裡的第三方碼）** 和 **系統依賴**。vendored 的看 `deps/`：

```
$ ls deps/
Makefile  README.md  fpconv  hdr_histogram  hiredis  jemalloc  linenoise  lua
```

一眼認出：`jemalloc`（記憶體配置器）、`lua`（腳本引擎，對應 redis 的 EVAL）、`hiredis`（redis 自己的 C client，cli/benchmark 會用）、`linenoise`（cli 的行編輯）、`hdr_histogram`（延遲統計）。**這些是「別人的碼」，讀 redis 邏輯時可以整包跳過**，除非你要追的路徑正好穿進去。系統依賴看 README（`libssl-dev`、`libsystemd-dev`）即可。

## 第 5 格（40–50 min）：測試 + git 活躍度

**測試是被低估的文件。** 測試檔案是「這功能該怎麼用、邊界在哪」的可執行規格，而且不會過期（過期的測試會 fail）。偵察期不細讀測試，只看「測試在哪、怎麼組織、覆蓋什麼」——這張測試地圖後面追某功能時，能直接指路到對應實作。

```
$ ls tests/
README.md  assets  cluster  helpers  instances.tcl  integration
modules  sentinel  support  test_helper.tcl  unit

$ ls tests/unit/ | head
acl.tcl  auth.tcl  bitops.tcl  dump.tcl  expire.tcl  functions.tcl ...
$ ls tests/integration/ | head
aof.tcl  failover.tcl  replication.tcl  rdb.tcl  psync2.tcl ...
```

測試目錄的結構本身就是一張功能地圖：`unit/` 按功能切（acl、expire、bitops……），`integration/` 按跨元件情境切（replication、failover、aof）。之後你要讀「replication 怎麼運作」，`tests/integration/replication.tcl` 是最快的行為規格入口。

git 活躍度看專案「還活著嗎、哪裡最近在動」：

```
$ git log --oneline | head -5
c9d29f6 Redis 7.4.0
$ git rev-list --count HEAD
1
```

這裡只有一個 commit——**因為 Ch 0 用 `--depth 1` 淺 clone**，歷史被砍掉了。這正是偵察期會踩到的坑：淺 clone 看不了活躍度。真要評估活躍度得先 `git fetch --unshallow`，然後看 `git log --since='6 months ago' --oneline | wc -l`（近半年 commit 數）、`git shortlog -sn | head`（主要貢獻者）。redis 是每週都有 commit 的活躍專案——這在 GitHub 頁面一眼可見，不必都靠 CLI。git 當考古工具是 Ch 17 的正題，偵察期只要有個「活/半死/已封存」的粗判即可。

順帶看 CI 與貢獻流程，判斷工程品質：

```
$ ls .github/workflows/
ci.yml  codeql-analysis.yml  coverity.yml  daily.yml  external.yml
reply-schemas-linter.yml  spell-check.yml
```

有 CI（`ci.yml`）、有靜態分析（`codeql`、`coverity`）、有拼字檢查——這是工程紀律嚴謹的專案。搭配存在 `CONTRIBUTING.md` 與 `SECURITY.md`，你對「這 codebase 的品質與可信度」有了具體判斷。對安全研究者，`codeql-analysis.yml` 還告訴你「低垂的果實可能已被掃過」，找洞要往靜態分析掃不到的地方去（Ch 32）。

## 第 6 格（50–60 min）：寫偵察筆記

最後十分鐘不做新調查，只**外化**。腦中的印象不算數，寫下來的才算。偵察筆記的價值在於：它逼你把散落的觀察收斂成「下一步做什麼」，並且成為你這個專案的持久記憶——一週後回來不必重做偵察。

下面是我對 redis 跑完這 60 分鐘後填出的筆記（也就是本章交給你的可複用模板，直接抄去用）：

```markdown
# 偵察筆記：redis 7.4.0

## 一句話定性
data structures server；server-client 模型，TCP socket，自訂 RESP 協定。

## 規模體檢（cloc）
- src/：10 萬行 C，註解比 ~1:3（勤勞，好讀）
- 全 repo：17.8 萬行 C，多出的 7 萬在 deps/（第三方，先跳過）
- tests/：4.7 萬行 Tcl
- 異常：src/ 有 401 個 JSON（→ 指令定義元資料，記下待查）

## build
- Makefile 專案。頂層 make → src/Makefile。
- 產出 6 個 binary：redis-server / sentinel / cli / benchmark
  / check-rdb / check-aof。
- redis-server = REDIS_SERVER_OBJ（server.o + networking.o + rdb.o
  + aof.o + replication.o + t_*.o + cluster.o + ...）。

## 進入點（待 Ch 6 坐實）
- 8 個 main；主入口 = src/server.c:6917（server.o 在 SERVER_OBJ 且
  README 定性為 server）。其餘為工具/自測。

## 模組地圖（從檔名/build 推）
- 資料型別：t_string/t_list/t_set/t_zset/t_hash/t_stream
- 網路：networking / anet / connection / socket / ae(事件迴圈)
- 持久化：rdb / aof
- 複製/叢集：replication / cluster / cluster_legacy
- 其他：pubsub / multi(交易) / eval(Lua) / acl

## 依賴
- vendored（deps/，讀邏輯時跳過）：jemalloc, lua, hiredis,
  linenoise, hdr_histogram
- 系統：libc, 選配 openssl(TLS)/systemd

## 測試
- Tcl。unit/ 按功能、integration/ 按情境（replication/failover/aof）。
- 追某功能時，先看對應 .tcl 當行為規格。

## 品質/活躍度
- CI + CodeQL + Coverity + CONTRIBUTING + SECURITY → 工程紀律嚴謹、活躍。
- （淺 clone，活躍度細節待 unshallow）

## 下一步（優先序）
1. Ch 6：坐實 server.c main → aeMain 主迴圈。
2. Ch 7：以 server.h 的 redisServer/client struct 為中心畫架構圖。
3. 待查：src/commands/*.json 如何生成指令表。
```

這份筆記就是這 60 分鐘的全部產出。注意它**沒有任何一行邏輯解讀**——沒解釋任何函式在幹嘛。它只回答了「這是什麼、多大、怎麼組、從哪跑、依賴誰、測試在哪、下一步讀哪」。這正是偵察該做的：**鋪好地圖，把深讀留給後面。**

## 對比與取捨

| 偵察維度 | 用什麼看 | 花幾分鐘 | 產出 | 常見陷阱 |
|---|---|---|---|---|
| 定性 | README / MANIFESTO 標題與開頭 | 5 | 一句話這是什麼 | 陷進使用教學細節 |
| build | 頂層 + `src/Makefile` 的 OBJ/all 目標 | 10 | 產物清單 + 模組原料 | 只看頂層漏掉真正的 build 檔 |
| 規模 | `cloc`（分 src/ 與全 repo） | 3 | 行數/語言/註解比 | 把 deps/ 算進你要讀的量 |
| 結構 | `ls` + 檔名前綴慣例 | 5 | 模組地圖 | 忽略命名慣例的訊號 |
| 入口 | `rg "int main"`（C） | 3 | 主入口候選 | 以為第一個 main 就是主入口 |
| 依賴 | `deps/`、`vendor/`、lockfile | 5 | 哪些可跳過 | 誤讀第三方碼當本體 |
| 測試 | `ls tests/` 結構 | 5 | 功能→測試對照 | 偵察期就細讀測試 |
| 活躍度 | `git log --since` / GitHub | 5 | 活/死/封存 | 淺 clone 看不到而誤判 |

**先廣後深 vs 一路深追**：偵察是刻意的廣度優先。你會忍不住在看到 `processCommand` 時想追下去——忍住，記在筆記「待查」欄，這一小時的任務是鋪滿全景，不是挖任何一口井。挖井是 Ch 8 之後的事，而且要挖對的井，前提是先有全景。

## 踩雷集錦

1. **一上來就讀 `main` 逐行追**：錯誤直覺是「入口在這，順著讀就懂了」。正確認識是——大型專案的 `main` 動輒幾百行初始化，且往下每一步都可以展開成一小時的 call chain，你會在第一個兔子洞裡耗光整個上午卻沒有全景。先做完六格偵察，拿著地圖再決定從哪深入。

2. **把 `cloc` 的總行數當成你要讀的量**：錯誤直覺是「17.8 萬行 C，讀不完」。正確認識是——扣掉 `deps/`（7 萬行第三方）、扣掉測試，你真正要碰的核心可能只有兩三萬行。偵察的一半價值就是**算出「不用讀」的那部分**，讓範圍縮小。永遠分開跑 `cloc src/` 和 `cloc .`。

3. **跳過 build system 直接讀 code**：錯誤直覺是「build 檔是給編譯用的，跟讀碼無關」。正確認識是——build 檔是唯一保證不過期、且精確描述「哪些檔組成哪個產物」的文件。redis 的 `REDIS_SERVER_OBJ` 一行就給了你模組清單和「哪個 main 是真的」的判據。跳過它等於丟掉最可靠的地圖。

4. **淺 clone 卻想評估活躍度**：錯誤直覺是「`git log` 只有一個 commit，這專案是不是死了？」正確認識是——`--depth 1` 砍掉了歷史（Ch 0 就是這樣 clone 的）。要看活躍度先 `git fetch --unshallow`，或直接看 GitHub 的 commit/issue 頻率。別被淺 clone 的假象誤導。

5. **無視命名慣例**：錯誤直覺是「檔名不重要，內容才重要」。正確認識是——`t_*.c`、`cluster_legacy.c`、`redis-check-rdb.c` 這些名字是維護者免費送你的模組標籤。系統化掃一輪檔名前綴，比逐檔打開快十倍就能拼出模組地圖。

## 進階：再往深一層

- **偵察腳本化**：把這 60 分鐘的固定動作寫成一個 `recon.sh`——`cloc src/`、`rg "int main"`、`ls tests/`、`git log --since`、抓 build 目標。之後對任何新 repo `./recon.sh` 五秒吐出骨架。這正是把讀碼工程化的第一步（Part 3 精神）。
- **不同語言的入口偵察**：C 找 `main`；Python 找 `if __name__ == '__main__'` 與 `setup.py`/`pyproject.toml` 的 `entry_points`；Go 找 `func main` 與 `cmd/` 目錄；Node 找 `package.json` 的 `main`/`bin`。Ch 6 系統化處理。
- **從封裝反推**：有 `Dockerfile` 就看 `ENTRYPOINT`/`CMD`，有 systemd unit 就看 `ExecStart`，有 CI 就看它 build 什麼、跑什麼測試。這些「怎麼跑起來」的檔案是繞過猜測、直達真實入口的捷徑。
- **文件品質分級**：養成給 codebase 的文件打個粗分（有架構文件／只有 API 文件／只有 README／沒文件）。分數直接決定你要投多少力氣自己逆推架構——redis 有 README 內建 source layout，是最高等級，省你大把力氣。

## 動手練習

1. **重跑 redis 六格偵察**：照本章順序，親手跑 `cloc src/`、`cloc .`、`cat Makefile`、`grep REDIS_SERVER_OBJ src/Makefile`、`ls src/t_*.c`、`ls tests/`，確認你的輸出跟本章一致，並填出你自己的偵察筆記。
2. **算「不用讀」的比例**：用兩次 `cloc` 算出 redis 中「第三方 deps + 測試」佔總行數的百分比，得出「redis 本體核心」實際要讀的行數。
3. **build 反推模組**：只看 `src/Makefile` 的 `REDIS_SERVER_OBJ`，不看任何其他檔，寫出你猜的 redis 子系統清單。再跟本章第 3 格的檔名慣例對照，看你猜對幾成。
4. **換一個陌生 repo 計時偵察**：找一個你完全沒背景的中型開源專案（建議挑非 C 的，例如某個 Go CLI 或 Python 服務），嚴格計時 60 分鐘走完六格，產出偵察筆記。痛苦的地方（例如找不到入口、看不懂 build）就是你下一章要補的技巧。

## 本章重點整理

- 第一小時是**偵察不是閱讀**：回答「這是什麼類型的目標、該用哪套讀法」，不解讀任何邏輯。等同 binary RE 的 `file`/`readelf`/`strings` 階段。
- **計時逼優先序、先廣後深**：60 分鐘硬上限，六格各有停損點，忍住不跌進兔子洞。
- 六格：README 定性 → build 產物 → cloc+目錄規模 → 入口+依賴 → 測試+git → 寫筆記。
- **build system 與命名慣例是最可靠、最被低估的地圖**：`REDIS_SERVER_OBJ` 給模組清單與真入口，`t_*.c` 給資料型別分佈。
- 偵察的一半價值是**算出「不用讀」的部分**（deps、測試），把閱讀範圍縮小。
- 產出是一份**可複用的偵察筆記**：定性、規模、build、入口、模組、依賴、測試、品質、下一步優先序。

## 自我檢核

- [ ] 不看筆記，能不能說出偵察六格的順序，以及每格用什麼指令、產出什麼？
- [ ] 為什麼我堅持偵察要計時、要先廣後深？不這麼做會發生什麼？
- [ ] 面試官問「你被空降到一個沒看過的十萬行 C 服務，第一小時做什麼」，你能不能把六格講成一套 SOP？
- [ ] redis 有 8 個 `main`，你如何**只靠 build 檔**推出哪個是主入口，而不必讀任何 code？
- [ ] 為什麼 `cloc .` 的總行數會誤導你？你會怎麼算出「真正要讀的核心行數」？
- [ ] 淺 clone 對「評估活躍度」造成什麼問題，怎麼解？

## 延伸閱讀

- **[redis README.md 的 "Source code layout" 章節](https://github.com/redis/redis/blob/7.4.0/README.md)**
  - **讀哪裡**：從 "Source code layout" 一路讀到各檔案逐一介紹（`server.h`、`server.c`、`networking.c`……）。
  - **學到什麼**：一份維護者親手寫的架構導覽長什麼樣、如何當偵察捷徑用。也體會「有這種文件的專案是最高文件等級」的意義。
  - **關聯**：本章第 1 格與 Ch 7 架構圖的直接原料。

- **《Code Reading: The Open Source Perspective》— Diomidis Spinellis（Addison-Wesley, 2003）第 1、2 章**
  - **讀哪裡**：Ch 1 "Introduction"（讀碼為何值得系統學）與 Ch 2 對一個完整小程式的通讀示範。
  - **學到什麼**：一套「拿到專案先看什麼」的通盤方法論；年代久但偵察順序的邏輯不過時。
  - **關聯**：本章六格的思想源頭之一，補理論深度。

- **[cloc 官方 README](https://github.com/AlDanial/cloc)**
  - **讀哪裡**：`--exclude-dir`、`--by-file`、`--diff` 幾個選項的說明。
  - **學到什麼**：怎麼在規模體檢時精準排除 `deps/`/`vendor/`、怎麼按檔案列出最大的檔（找核心檔的捷徑）、怎麼比較兩版之間的行數變化。
  - **關聯**：把本章第 3 格的規模體檢做得更精準。

- **[GitHub 官方文件："About repositories" 與 community health files](https://docs.github.com/en/repositories/creating-and-managing-repositories/about-repositories)**
  - **讀哪裡**：community health files（README/CONTRIBUTING/SECURITY/CODE_OF_CONDUCT）與 Insights 頁的說明。
  - **學到什麼**：這些頂層檔各自代表什麼訊號、GitHub 的 Insights（contributors/commit 頻率）怎麼一眼判斷活躍度——繞過淺 clone 的限制。
  - **關聯**：本章第 5 格「品質與活躍度」的判斷依據。

偵察完成、地圖鋪好了。下一章我們把第 4 格快速帶過的「找入口」做到嚴謹：不同型態的程式（CLI、daemon、library、plugin）入口長得完全不同，我們要能穩定地從一堆 `main` 裡揪出真正的伺服器入口，並順藤摸到那顆跳動的主迴圈。

→ [Ch 6 找 entry point 與主迴圈](./06-finding-entry-points.md)
