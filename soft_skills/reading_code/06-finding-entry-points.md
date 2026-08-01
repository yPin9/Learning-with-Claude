# Ch 6 — 找 entry point 與主迴圈

> **目標**：學會對任何型態的程式穩定地定位「執行從哪裡開始」——CLI 的 `main`+arg parse、daemon/server 的 `main`→初始化→event loop、library 的公開 API、framework 的 callback 註冊點、plugin/`.so` 的 init 函式。核心實戰：在 redis 的 8 個 `main` 裡揪出唯一真正的伺服器入口，並用「從 build 產物反推」的硬方法坐實它，再順藤摸到那顆跳動的主迴圈 `aeMain(server.el)`。

## 為什麼「找入口」值得單獨一章？

逆一個 binary，你的第一個定位動作是找 entry point——ELF header 的 `e_entry`。但那是 `_start`（libc 的啟動樁），你真正要的是 `main`，得從 `_start` 呼叫 `__libc_start_main` 的參數裡挖出來。**「程式真正的邏輯從哪一行開始」不是白送的，要定位。**

讀 source 一樣。你以為 C 專案的入口就是 `main`，很簡單——直到 Ch 0 那個震撼教育：redis 的 `src/` 裡有**八個** `int main`。哪個是真的？如果你猜錯，順著一個自測程式的 `main` 讀下去，會讀出一套完全錯的心智模型。

而且入口的長相取決於**程式型態**。找 CLI 工具的入口跟找一個 library 的入口是兩件完全不同的事——library 根本沒有 `main`，它的「入口」是使用者會呼叫的公開 API。搞錯型態，你會拿著錯的地圖找錯的東西。所以這章先建立「按型態分類」的直覺，再給每一類一套定位法。

## 五種程式型態，五種入口

```
型態              「入口」是什麼               怎麼找
────────────────────────────────────────────────────────────
CLI 工具          main() + 參數解析            rg "int main" → 看 argv 怎麼分派
daemon/server     main() → 初始化 → event loop 找 main 後順到 loop 函式
library (.a/.so)  公開 header 的 API           讀 include/ 對外標頭，非 main
framework/callback 你註冊進去的 callback        找「註冊點」(register/hook/handler)
plugin/module(.so) 固定名的 init 函式           找 dlopen 約定的符號 (xxx_init)
```

關鍵心法：**「入口」＝「控制權第一次進到『這個 codebase 的邏輯』的那一點」。** 對可執行檔那是 `main`；對 library 那是「別人呼叫你第一個函式」的地方；對 plugin 那是宿主程式 `dlopen` 後呼叫的約定符號。找入口就是找「控制權交接的邊界」。

下面逐一拆，redis 屬於 daemon/server 型，會是主戰場。

### CLI 工具：main + 參數解析就是地圖

CLI 的 `main` 通常很短，核心是「解析 `argv`，依子命令/旗標分派到不同函式」。所以讀 CLI 入口，重點不是 `main` 本身，是**它的參數解析表**——那張表列出了這個工具能做的所有事。

以 redis 自己的 `redis-cli` 為例，它就是個 CLI（雖然同 repo）：

```
$ rg -n "int main" src/redis-cli.c
src/redis-cli.c:10572:int main(int argc, char **argv) {
```

CLI 入口的典型結構是 `main` → `parseOptions()` → 依旗標設定一個 config struct → 進入互動或執行模式。你讀 CLI 只要抓到那張「旗標 → 行為」的對照，就掌握了它的全部能力邊界。Python 的 `argparse`/`click`、Go 的 `cobra`、Rust 的 `clap` 都是同一個模式：**找到參數定義的地方＝找到功能地圖。**

### daemon/server：main → 初始化 → event loop（重點）

伺服器型的入口有固定三段式節奏，這是你要能一眼認出的模板：

```
main()
  ├─ 1. 解析設定（argv + 設定檔）
  ├─ 2. 初始化（開 socket、建資料結構、建 event loop）
  └─ 3. 進入主迴圈  ← 「心臟」在這，程式 99% 的時間耗在這
       while (!stop) { 等事件 → 分派 handler }
```

**找到 `main` 只是起點，真正要順到的是第 3 段那個 `while` 迴圈。** 那才是伺服器的心臟——所有連線處理、指令執行、背景任務都掛在那個迴圈上。redis 的實戰整章都在做這件事。

### library：沒有 main，公開 header 才是入口

library（`.a`/`.so`）**沒有 `main`**——它是被別人連結、被別人呼叫的。它的「入口」是**對外公開的 API**，通常放在 `include/` 目錄或某個對外標頭裡。

判斷方法：看 build 產物是不是 `.a`/`.so`（不是可執行檔）、看有沒有 `include/` 公開標頭目錄、看有沒有安裝 header 的 install 規則。找到公開標頭後，那些非 `static`、有 doc comment 的函式宣告就是入口清單。redis 的 `deps/hiredis` 就是這種——它是個 library，入口是 `hiredis.h` 裡的 `redisConnect`/`redisCommand`，不是任何 `main`。

### framework/callback：找「註冊點」

用框架寫的程式（web framework、GUI、事件系統），你的 code 是被框架「回呼」的，控制權在框架手上。這種入口是**你把 callback 註冊進框架的那一行**。找法是搜註冊動詞：`register`、`on`、`add.*handler`、`route`、`subscribe`。找到註冊點，就找到「框架在什麼事件下會跳進你的 code」。

### plugin/module（.so）：固定名的 init 函式

外掛（redis module、nginx module、kernel module、各種 `.so` plugin）由宿主 `dlopen` 載入，宿主與外掛約定一個**固定名稱的初始化函式**當入口。redis module 的約定是 `RedisModule_OnLoad`；kernel module 是 `module_init()` 註冊的函式；nginx 是 `ngx_module_t` 結構。找 plugin 入口＝查文件或宿主碼裡「載入外掛後呼叫哪個約定符號」。

## redis 實戰：8 個 main 裡哪個是真的？

回到 Ch 0 的震撼教育。先重現現場：

```
$ rg -n "int main" src/*.c
src/server.c:6917:int main(int argc, char **argv) {
src/redis-cli.c:10572:int main(int argc, char **argv) {
src/redis-benchmark.c:1694:int main(int argc, char **argv) {
src/setproctitle.c:323:int main(int argc, char *argv[]) {
src/crc64.c:157:int main(int argc, char *argv[]) {
src/siphash.c:362:int main(void) {
src/mt19937-64.c:170:int main(void)
src/localtime.c:88:int main(void) {
```

八個。新手會困惑，但有經驗的讀碼者會把它們分成三類，各用不同判據：

**第一類：另一個 binary 的入口（真 main，但不是 server）。** `redis-cli.c` 和 `redis-benchmark.c` 的 main 是真的，但它們是**別的產物**的入口——回顧 Ch 5 的 build 知識，`REDIS_CLI_OBJ` 有 `redis-cli.o`、`REDIS_BENCHMARK_OBJ` 有 `redis-benchmark.o`。它們各自編成 `redis-cli`/`redis-benchmark` binary，不是 `redis-server`。

**第二類：自測 harness（被 `#ifdef` 包起來的假 main）。** 剩下五個（`crc64`/`siphash`/`localtime`/`mt19937-64`/`setproctitle`）都是「這個檔自己的單元測試」。看 `crc64.c`：

```
$ grep -nB2 "int main" src/crc64.c
154-#endif
155-
156-#ifdef REDIS_TEST_MAIN
157:int main(int argc, char *argv[]) {
```

`#ifdef REDIS_TEST_MAIN`——這個 main **只在定義了那個巨集時才存在**。正常 build 不定義它，所以這個 main 根本不會被編進去。逐一檢查其餘四個，全部有守衛（各用不同巨集名，是 redis 的歷史雜訊）：

```
crc64.c        → #ifdef REDIS_TEST_MAIN
siphash.c      → #ifdef SIPHASH_TEST      （包住 siphash_test() 與 main）
localtime.c    → #ifdef LOCALTIME_TEST_MAIN
mt19937-64.c   → #ifdef MT19937_64_MAIN
setproctitle.c → #ifdef SETPROCTITLE_TEST_MAIN
```

**第三類：真正的伺服器入口。** 只剩 `src/server.c:6917`。回顧 Ch 5，`server.o` 在 `REDIS_SERVER_OBJ` 裡、README 定性 redis 是 server——所以這是 `redis-server` 的入口，也就是我們要的。

到這裡你已經靠「build 歸屬 + `#ifdef` 守衛」推出了答案。但推理鏈有點長，有沒有**一條不會錯的硬證據**？有。

## 硬方法：從 build 產物反推真入口

`grep` 是純文字的，它看不出 `#ifdef` 有沒有生效（那五個假 main 在文字上長得跟真的一模一樣）。要一勞永逸地確認，**去問編譯出來的產物**——連結器已經幫你解決了「哪個 main 真的存在」的問題。

Ch 0 已經 `bear -- make` 過，binary 就在 `src/`。用 `nm` 查 `redis-server` 裡的 `main` 符號：

```
$ nm src/redis-server | grep -w main
0000000000086ad0 T main
```

**只有一個 `T main`**（`T` = 定義在 text 段的強符號）。八個 grep 到的 main，最後只有一個真的存在於 binary 裡。連結器不會騙你——那五個假 main 因為 `#ifdef` 沒生效，根本沒產生符號；兩個 CLI/benchmark 的 main 在別的 binary。

再往下一層看得更清楚——逐個 object file 查誰真的匯出了 `main` 符號：

```
$ for o in server crc64 siphash localtime setproctitle mt19937-64; do \
    printf "%-14s " "$o.o"; \
    nm src/$o.o 2>/dev/null | grep -w main || echo "(no main symbol)"; done
server.o       0000000000000000 T main
crc64.o        (no main symbol)
siphash.o      (no main symbol)
localtime.o    (no main symbol)
setproctitle.o (no main symbol)
mt19937-64.o   (no main symbol)
```

**只有 `server.o` 匯出 `T main`**，其餘五個 object file 裡的 main 被 `#ifdef` 編掉了，連符號都沒有。這就是鐵證：`src/server.c` 是 `redis-server` 唯一真正的入口。

> **這招的價值**：`grep "int main"` 給你**候選**，`nm binary | grep main` 給你**答案**。前者是文字層、會被 `#ifdef` 和多產物騙；後者是連結器層、是編譯結果的地面真相。任何時候你對「哪個定義才是真的生效」有疑慮（不只 main，任何被條件編譯包起來的東西），都可以用「問 binary 的符號表」來裁決。這正是 binary RE 直覺移植到讀 source 的典型招式——**當 source 有歧義，去問產物**。

還有一條互補的反推路徑：**從「怎麼跑起來」的封裝反推**。redis 的 systemd unit 直接寫明用哪個 binary：

```
$ grep -E 'Description|ExecStart' utils/systemd-redis_server.service
Description=Redis data structure server
ExecStart=/usr/local/bin/redis-server --supervised systemd --daemonize no
```

`ExecStart` 指名 `redis-server`——這是部署層對「真入口是哪個 binary」的第三方確認。Dockerfile 的 `ENTRYPOINT`、CI 的執行指令、`package.json` 的 `bin` 欄都是同性質的線索：**部署與封裝檔案不會騙你「實際被執行的是哪個」。**

## 順藤摸瓜：從 main 到主迴圈

入口坐實了，現在做伺服器型的第二步——**從 `main` 順到那顆心臟（event loop）**。不逐行讀 `main`（它幾百行都是初始化雜項），只抓三段式骨架。看 `main` 尾段：

```
$ sed -n '7185,7255p' src/server.c   （擷取關鍵行）
    initServer();                 // 7189  ← 第 2 段：初始化
    ...
    InitServerLast();             // 7206
    ...
    loadDataFromDisk();           // 7212  載入持久化資料
    ...
    aeMain(server.el);            // 7251  ← 第 3 段：進入主迴圈（心臟）
    aeDeleteEventLoop(server.el);
    return 0;
```

三段式完整浮現：前面幾千行是設定與 `initServer()`（第 2 段），最後 `aeMain(server.el)`（第 3 段）——**程式從這一行起，99% 的執行時間都待在裡面。** 這正是 Ch 0 用 cscope 反查 `aeMain` 呼叫者時定位到的 `server.c:7251`。

`server.el` 是什麼？在 `initServer()` 裡建立的 event loop：

```
$ rg -n "server.el = aeCreateEventLoop|aeSetBeforeSleepProc|aeSetAfterSleepProc" src/server.c
2657:    server.el = aeCreateEventLoop(server.maxclients+CONFIG_FDSET_INCR);
2772:    aeSetBeforeSleepProc(server.el,beforeSleep);
2773:    aeSetAfterSleepProc(server.el,afterSleep);
```

三行交代了心臟的組裝：`aeCreateEventLoop` 建迴圈、`aeSetBeforeSleepProc(beforeSleep)` 註冊「每輪迴圈睡前要做的事」（redis 把很多關鍵背景工作掛在這，例如把回覆寫回 client、處理過期 key）。**這兩個 `Set...Proc` 就是 redis 版的 callback 註冊點**——呼應前面「framework/callback 型入口＝找註冊點」。

最後掀開 `aeMain` 本體，看心臟真正在跳什麼：

```
$ sed -n '/void aeMain/,/^}/p' src/ae.c
void aeMain(aeEventLoop *eventLoop) {
    eventLoop->stop = 0;
    while (!eventLoop->stop) {
        aeProcessEvents(eventLoop, AE_ALL_EVENTS|
                                   AE_CALL_BEFORE_SLEEP|
                                   AE_CALL_AFTER_SLEEP);
    }
}
```

教科書級的 event loop：一個 `while (!stop)`，每一輪呼叫 `aeProcessEvents`（它內部做 `epoll_wait`/`kqueue`，等 fd 事件，然後分派到註冊的 handler）。**你現在完整掌握了 redis 的執行骨架**：`main` → `initServer`（建 loop、註冊 handler）→ `aeMain` → `while` → `aeProcessEvents` → 分派。整個伺服器就掛在這個迴圈上跑。

順帶把「事件如何分派到指令處理」這條線標出來（完整追蹤是 Ch 8，這裡先建立地圖）——connection 可讀時，事件系統會回呼 `readQueryFromClient`：

```
$ rg -n "readQueryFromClient|processInputBuffer|int processCommand|void call\(" src/networking.c src/server.c | head
src/networking.c:2559:int processInputBuffer(client *c) {
src/networking.c:2655:void readQueryFromClient(connection *conn) {
src/server.c:3524:void call(client *c, int flags) {
src/server.c:3884:int processCommand(client *c) {
```

主迴圈醒來 → `readQueryFromClient`（讀 socket）→ `processInputBuffer`（解析 RESP 協定）→ `processCommand`（找指令）→ `call`（執行）。這條「網路事件 → 指令執行」的鏈，就是 redis 的主動脈。你從一個 `nm` 指令開始，現在已經摸到整條動脈的走向。

## 對比與取捨

| 找入口的方法 | 適用型態 | 準確度 | 成本 | 陷阱 |
|---|---|---|---|---|
| `rg "int main"` | 可執行檔（C/C++） | 給候選，非答案 | 秒 | 多產物 + `#ifdef` 假 main 全中 |
| `nm binary \| grep main` | 已 build 的可執行檔 | **答案（連結器裁決）** | 需先 build | 要能編出 binary |
| build 檔的 OBJ 歸屬 | 多產物專案 | 高 | 分鐘 | 要看懂 Makefile 變數 |
| 讀公開 header | library | 高 | 分鐘 | 誤把內部 header 當公開 |
| 搜註冊動詞 | framework/callback | 中高 | 分鐘 | 註冊可能分散、動態註冊難搜 |
| dlopen 約定符號 | plugin/module | 高 | 需查文件 | 約定名各家不同 |
| 部署封裝（systemd/Docker/CI） | 任何被部署的服務 | 高（實際跑的） | 秒 | 開發用入口 ≠ 生產入口 |

**`grep` vs `nm` 的分野貫穿全課**：文字工具給你候選、快而通用但會被條件編譯騙；問產物（符號表）給你經連結器裁決的地面真相。遇到「哪個定義才生效」的歧義，升級到問產物。

## 踩雷集錦

1. **以為第一個 / 唯一的 `main` 就是主入口**：錯誤直覺是「grep 到 main 就是它了」。正確認識是——大專案常有多產物（各自 main）與自測 harness（`#ifdef` 假 main）。redis 八個 main 只有一個是 server 入口。永遠用 build 歸屬或 `nm binary` 裁決，別信第一個 grep 結果。

2. **被 `#ifdef` 假 main 騙進去讀**：錯誤直覺是「這 main 在，順著讀就懂這檔」。正確認識是——`#ifdef REDIS_TEST_MAIN` 包起來的 main 在正常 build 根本不存在，它是作者的單元測試腳手架。看到 main 先往上看兩行有沒有 `#ifdef` 守衛；有疑慮就 `nm` 查符號在不在。

3. **對 library 找 main**：錯誤直覺是「入口＝main，找不到 main 就卡住」。正確認識是——library 沒有 main，它的入口是公開 API（`include/` 的對外 header）。先判斷 build 產物是可執行檔還是 `.a`/`.so`，型態不同找法完全不同。

4. **找到 main 就停，不順到主迴圈**：錯誤直覺是「入口找到了，任務完成」。正確認識是——對 daemon/server，`main` 只是三段式的第一段，真正的心臟是第三段的 event loop（`aeMain` 的 `while`）。程式 99% 時間在迴圈裡，不順到它等於沒找到「程式實際在做什麼」的地方。

5. **開發入口當成生產入口**：錯誤直覺是「README 教學裡跑的那條指令就是真入口」。正確認識是——很多專案有多個進入方式（開發用 `make run`、測試用 harness、生產用 systemd/Docker）。要理解「實際部署時怎麼跑」，看 systemd unit / Dockerfile 的 `ExecStart`/`ENTRYPOINT`，那才是生產真相。

## 進階：再往深一層

- **`_start` 之前還有東西**：C 程式在 `main` 之前，libc 的 `__libc_start_main` 已經跑過，而且帶 `__attribute__((constructor))` 的函式、C++ 的全域物件建構、`.init_array` 都在 `main` 前執行。讀「為什麼某狀態在 main 第一行就已經被設好」時，去找 constructor 與全域初始化。這是 Ch 22（讀 metaprogramming）與 Ch 27（kernel/系統慣例）的伏筆。
- **多個 event loop / 多執行緒**：redis 主要是單 event loop，但 7.x 有 I/O threads 與 `bio`（background I/O）執行緒。真實伺服器常有多個迴圈（每執行緒一個 `epoll`）。找入口後要問「有幾顆心臟」——`rg "aeCreateEventLoop|epoll_create|pthread_create"` 數一下。Ch 25 讀並發時深入。
- **從 `nm`/符號表逆推更多**：`nm -D`（動態符號）、`objdump -d --start-address` 對照 source、`gdb` 下 `info functions`——當 source 有大量 `#ifdef` 讓你分不清哪段生效時，符號表與反組譯是最終裁判。Ch 28 專講 source ↔ disassembly 對照。
- **entrypoint 反查框架**：對你不熟的框架（Spring、Django、Actix），與其硬找 main，不如查「這框架的 request/事件從哪個約定函式進來」——文件通常有「lifecycle」或「hooks」章節。找到框架的生命週期入口，比找語言層的 main 有用。

## 動手練習

1. **重現八 main 裁決**：`rg -n "int main" src/*.c` 列出全部，逐一 `grep -nB2 "int main"` 檢查 `#ifdef` 守衛，把八個分成「別的 binary / 自測 harness / server 入口」三類。
2. **用 nm 坐實真入口**：`nm src/redis-server | grep -w main` 確認只有一個 `T main`；再對 `crc64.o` 和 `server.o` 各跑一次 `nm ... | grep main`，親眼看到只有 `server.o` 有符號。體會「問產物」比「grep 文字」硬。
3. **順到心臟**：從 `server.c` 的 `main` 尾段找到 `aeMain(server.el)`，再打開 `src/ae.c` 讀 `aeMain` 本體，畫出 `main → initServer → aeMain → while → aeProcessEvents` 的骨架圖。
4. **找 callback 註冊點**：`rg -n "aeSetBeforeSleepProc|aeCreateFileEvent" src/*.c`，找出 redis 把哪些 handler 註冊進 event loop（`acceptTcpHandler`、`readQueryFromClient`……），列出「哪個事件觸發哪個函式」。
5. **換型態練**：找一個 library 專案（例如 `deps/hiredis`）與一個 CLI 專案，各自不用找 main，改用「公開 header」與「參數解析表」定位入口，體會型態不同找法不同。

## 本章重點整理

- 「入口」＝控制權第一次進入這個 codebase 邏輯的邊界；型態不同（CLI/daemon/library/framework/plugin）入口長相完全不同，找法也不同。
- daemon/server 的入口是三段式：`main` → 初始化 → **event loop**；找到 main 只是起點，要順到那顆 `while` 心臟。
- `rg "int main"` 給**候選**，會被多產物與 `#ifdef` 假 main 騙；`nm binary | grep main` 給**答案**（連結器裁決）。有歧義就問產物。
- redis 八個 main：兩個是別的 binary、五個是 `#ifdef` 包的自測 harness、只有 `server.o` 的 `T main` 是真伺服器入口。
- redis 心臟：`main` → `initServer`（建 loop、註冊 `beforeSleep`/handler）→ `aeMain` → `while (!stop) aeProcessEvents`。主動脈：事件 → `readQueryFromClient` → `processInputBuffer` → `processCommand` → `call`。
- 部署封裝（systemd/Docker/CI）是「實際跑哪個入口」的第三方確認。

## 自我檢核

- [ ] 不看筆記，能不能說出五種程式型態各自的「入口」是什麼、怎麼找？
- [ ] redis 八個 main，你能不能把它們分成三類，並說出每類的判據？
- [ ] 為什麼 `nm binary | grep main` 比 `rg "int main"` 可靠？各自在哪一層工作？
- [ ] 對一個 daemon，找到 `main` 之後你還要做什麼才算真的找到「程式在做什麼」？
- [ ] 面試官給你一個沒看過的 C 服務，問「你怎麼確定哪個是真入口」，你能不能講出「build 歸屬 + `#ifdef` 檢查 + nm 符號表 + systemd」這套組合拳？
- [ ] `aeSetBeforeSleepProc` 為什麼可以視為「callback 註冊點」？它跟 framework 型入口有什麼共通性？

## 延伸閱讀

- **[redis src/ae.c 與 ae.h（事件迴圈實作）](https://github.com/redis/redis/blob/7.4.0/src/ae.c)**
  - **讀哪裡**：`aeMain`、`aeProcessEvents`、`aeCreateFileEvent`，以及 `ae_epoll.c`/`ae_kqueue.c` 的後端切換。
  - **學到什麼**：一個乾淨、可讀的生產級 event loop 長什麼樣；「註冊 handler → 迴圈等事件 → 分派」的完整骨架。這是所有 daemon/server 入口的通用模板。
  - **關聯**：本章「順到主迴圈」的一手材料，也是 Ch 24（狀態機與事件驅動）的預習。

- **[man 3 nm 與 `objdump -t` 文件](https://sourceware.org/binutils/docs/binutils/nm.html)**
  - **讀哪裡**：符號類型字母表（`T`/`t`/`U`/`W`/`D`……）那一節。
  - **學到什麼**：怎麼從符號表讀出「哪些符號被定義、哪些是強/弱符號、哪些未解析」。理解 `T main` 只有一個的意義，以及用符號表裁決 source 歧義的通法。
  - **關聯**：本章「從 build 產物反推真入口」的工具基礎，Ch 28 深入 source↔binary 對照。

- **[Redis Modules: an introduction to the API（redis.io 官方 module 文件）](https://redis.io/docs/latest/develop/reference/modules/)**
  - **讀哪裡**："RedisModule_OnLoad" 一節。
  - **學到什麼**：plugin/module 型入口的具體約定——宿主 `dlopen` 後呼叫固定名 `RedisModule_OnLoad`，外掛在裡面註冊指令與型別。對照本章「plugin 入口＝固定名 init 函式」。
  - **關聯**：補齊五種型態裡「plugin/module」那一類的真實範例。

- **《The Linux Programming Interface》— Michael Kerrisk，第 41–42 章（Shared Libraries / dlopen）**
  - **讀哪裡**：dynamic loading（`dlopen`/`dlsym`）與 library constructor/destructor。
  - **學到什麼**：library 與 plugin 在載入時「入口如何被宿主呼叫」的底層機制，以及 `main` 之前 constructor 何時跑。
  - **關聯**：補「library 沒有 main」與「進階：`_start` 之前」兩節的系統層原理。

入口與心臟都定位了，但我們現在只有一條主動脈，還沒有全身的地圖。下一章我們從目錄、檔案、依賴關係、核心資料結構出發，把 redis 的整個架構——網路層、指令分派、資料型別、持久化、複製——畫成一張你能一眼看懂的地圖，並找出這張地圖的中心。

→ [Ch 7 建立架構地圖](./07-building-architecture-map.md)
