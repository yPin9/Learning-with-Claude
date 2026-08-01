# Ch 0 — 環境與工具鏈搭建

> **目標**：把整門課會反覆用到的讀碼工具鏈一次配好，並在一個真實開源專案（redis）上驗證每個工具真的能跑。讀完你會知道「讀碼有哪幾類武器、各自解決什麼問題」，而不是遇到 codebase 只會用滑鼠上下捲。

> **環境**：本課主環境是 **WSL2 上的 Ubuntu 22.04.3 LTS**。工具版本：universal-ctags 5.9.0、cscope 15.9、GNU global/gtags 6.6.7、ripgrep 13.0.0、clangd 14、cflow 1.7、cloc 1.90、graphviz(dot) 2.43.0、gdb 12.1、clang 14、bear 3.0.18。macOS 用 Homebrew、原生 Linux 用各自套件管理器裝同一批工具即可；Windows 原生（非 WSL）多數索引工具缺，強烈建議走 WSL。版本差異大的地方我會標注。

## 為什麼需要這個？

先講一個殘酷的事實：**讀碼的瓶頸不是「看」，是「找」和「連」。**

你打開一個 50 萬行的專案，真正的困難從來不是某一行 C 看不懂——你當然看得懂 `for (i = 0; i < n; i++)`。困難是：

- 這個函式**是誰呼叫的**？（往上追）
- 它**呼叫了誰**？（往下追）
- 這個 struct 欄位**在哪裡被改**？（data flow）
- 這行為什麼長這樣、**是哪個 commit 加的**？（歷史）
- 程式真的跑起來時，**這條路徑到底有沒有走到**？（動態）

這些問題如果靠肉眼加 `Ctrl+F` 逐檔翻，你一天讀不完一個模組。專業讀碼的人跟你的差距，一半在方法（Part 1–2、5），另一半就在**工具**——他們把上面每一種問題都變成一條指令，秒級得到答案。

這章不教你「怎麼用得很深」（那是 Part 3 整整九章的事）。這章只做兩件事：**把武器裝好**，**讓你知道每把武器對應哪一種問題**。地圖先有，細節後填。

## 先建立直覺：工具是你在黑暗 codebase 裡的「感官」

想像你被空降到一座沒有地圖、沒開燈的巨大工廠（就是那個陌生 codebase）。你需要的不是「更用力看」，而是一組感官：

```
    問題                        對應的「感官」                   本課章節
 ┌─────────────────────┐    ┌──────────────────────┐
 │ 哪裡出現過這個字？   │ →  │ 文字搜尋  ripgrep     │   Ch 12
 │ 這個符號定義在哪？   │ →  │ 標籤索引  ctags       │   Ch 14
 │ 誰呼叫了它？         │ →  │ 交叉引用  cscope/global│   Ch 14
 │ 語意上的定義/引用？  │ →  │ 語意分析  clangd(LSP) │   Ch 13
 │ 結構上符合某 pattern？│ →  │ 語法樹    tree-sitter │   Ch 15
 │ 呼叫關係長怎樣？     │ →  │ 呼叫圖    cflow/graphviz│  Ch 9,16
 │ 這行的來歷？         │ →  │ 版本考古  git         │   Ch 17
 │ 跑起來實際走哪？     │ →  │ 動態觀察  gdb/strace  │   Ch 18,19
 │ 這專案多大、什麼組成？│ →  │ 規模統計  cloc        │   Ch 5
 └─────────────────────┘    └──────────────────────┘
```

沒有感官，你只能瞎摸。這章把這些感官全部裝上，之後每一章教你怎麼把某個感官用到極致。

> 一個關鍵區分先記著：**文字工具**（ripgrep）不懂程式語法，它只認字元——快、通用、但會被同名字串騙。**語意工具**（clangd）真的懂「這個 `read` 是哪個 `read`」——精準、但要能編譯、要設定。兩者不是替代關係，是**互補**：快速掃用文字工具，精準定位用語意工具。這條線貫穿整門課。

## 分層安裝：六類武器

我們把工具分成六層，對應上面的六種問題。先看完整安裝指令，再逐層解釋。

### 一次裝好（Ubuntu / WSL）

```bash
sudo apt-get update
sudo apt-get install -y \
  ripgrep \
  universal-ctags cscope global \
  clangd-14 \
  cflow cloc graphviz \
  gdb clang bear
```

在我的環境實際跑完後，逐一驗證版本（這是真實輸出）：

```
$ for t in rg ctags cscope gtags clangd-14 cflow cloc dot gdb; do \
    printf "%-12s " "$t"; command -v "$t" >/dev/null && "$t" --version 2>&1 | head -1; done
rg           ripgrep 13.0.0
ctags        Universal Ctags 5.9.0, Copyright (C) 2015 Universal Ctags Team
cscope       cscope: version 15.9
gtags        gtags (Global) 6.6.7
clangd-14    Ubuntu clangd version 14.0.0-1ubuntu1.1
cflow        cflow (GNU cflow) 1.7
cloc         1.90
dot          dot - graphviz version 2.43.0 (0)
```

> **踩雷（第一個就中）**：`ctags` 這個名字有兩個實作——古老的 **Exuberant Ctags**（2009 年後幾乎停更）和活躍維護的 **Universal Ctags**。Ubuntu 的 `universal-ctags` 套件裝的是後者，但指令名還是 `ctags`。你要確認版本字串裡有 `Universal Ctags`，看到 `Exuberant` 就是舊的，很多語言支援缺。macOS 用 `brew install universal-ctags`。

各層在幹嘛：

| 層 | 工具 | 一句話職責 | 深入章節 |
|---|---|---|---|
| 文字搜尋 | ripgrep (`rg`) | 全專案 regex 搜尋，快到你不想再用 grep | Ch 12 |
| 標籤索引 | universal-ctags | 「這符號定義在哪」的離線索引 | Ch 14 |
| 交叉引用 | cscope、GNU global | 「誰呼叫它 / 它呼叫誰」的雙向索引 | Ch 14 |
| 語意分析 | clangd (LSP) | 真懂型別/作用域的精準跳轉 | Ch 13 |
| 呼叫圖 | cflow、graphviz | 靜態呼叫關係 → 畫成圖 | Ch 9、16 |
| 統計/動態/考古 | cloc、gdb、git | 規模、執行、歷史 | Ch 5、17、18、19 |

### clangd 的前置：`compile_commands.json`

其他工具（ripgrep、ctags、cscope）都是「指到目錄就能用」。**clangd 是例外**：它要真的把每個檔案當成編譯單元來分析，所以它需要知道「每個 `.c` 是用什麼旗標編的」——這份資訊叫 **compilation database**，檔名固定 `compile_commands.json`。

產生方式看 build 系統：

- **CMake 專案**：加 `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`，CMake 自動吐一份。
- **Makefile 專案（如 redis）**：用 `bear`（Build EAR）包住 build 過程側錄：
  ```bash
  bear -- make -j$(nproc)     # bear 攔截每次 gcc 呼叫，記成 compile_commands.json
  ```
  我在 redis 上實際跑完的結果（真實輸出）：
  ```
  $ bear -- make -j$(nproc)
  ...
  make[1]: Leaving directory '/home/ypp/reading_code_lab/redis/src'
  $ ls -la compile_commands.json && grep -c '"file"' compile_commands.json
  -rw-r--r-- 1 ypp ypp 206352 ... compile_commands.json
  275
  ```
  275 個編譯單元被側錄下來，clangd 從此就能精準分析每一個 `.c`。
- **Bazel / 其他**：各有外掛，Ch 16 再談。

> 這是新手用 clangd 最常見的卡點：打開編輯器「go to definition」沒反應，十次有九次是**沒有 `compile_commands.json`**，clangd 只能退化成半殘的單檔模式。記住：**clangd 要能跳轉，先要能編譯。**

## 準備練功沙包：clone redis

整門課需要真實 codebase 來練。我們主用 **redis 7.4.0**——C 寫的、中型（src 十萬行）、架構清楚、註解品質高，是讀碼教材的黃金範本。之後很多章都會回到它。

```bash
mkdir -p ~/reading_code_lab && cd ~/reading_code_lab
git clone --depth 1 --branch 7.4.0 https://github.com/redis/redis.git
cd redis
```

先用 `cloc` 對它做第一眼體檢（真實輸出）：

```
$ cloc --quiet src/
-------------------------------------------------------------------------------
Language                     files          blank        comment           code
-------------------------------------------------------------------------------
C                              115          15755          32001         100023
JSON                           401              2              0          24565
C/C++ Header                    68           1220           3045           8638
make                             2            100             72            425
...
```

三個數字已經告訴你很多：**十萬行 C**（主體）、**32001 行註解**（註解/程式碼比約 1:3，算勤勞）、**401 個 JSON**（多半是命令定義表，之後會遇到）。你還沒讀任何一行 code，就已經知道這專案的規模與體質了。這正是 Ch 5「60 分鐘偵察」的第一招。

> **踩雷**：我這裡用 `--depth 1` 做**淺 clone**——只抓最新一個 commit，快、省空間，讀碼夠用。但淺 clone **沒有歷史**，`git log` / `git blame` 只會看到一個 commit（本章後面那個 blame 範例就只有 `c9d29f6` 一筆，正是這個原因）。等到 Ch 17 要做「版本考古」時，得先 `git fetch --unshallow` 補回完整歷史，或一開始就做完整 clone。

## 各武器 30 秒試跑（證明能用）

不深入用法，只證明每把武器在 redis 上真的動。深入是 Part 3 的事。

### 文字搜尋：redis 有幾個 `main`？

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

**八個 `main`**。這是第一個震撼教育：大專案不只一個進入點——多數是測試程式、工具程式、被 `#ifdef` 包起來的自測 harness。真正的伺服器進入點是 `src/server.c:6917`。「找 entry point」沒你想的簡單，Ch 6 專門處理。

### 標籤索引：建 tags，秒查定義位置

```
$ ctags -R --languages=C,C++ src/
$ wc -l tags
11171 tags

$ readtags -t tags main | head -3
main   src/crc64.c      /^int main(int argc, char *argv[]) {$/
main   src/localtime.c  /^int main(void) {$/
main   src/mt19937-64.c /^int main(void)$/
```

`ctags` 掃出 11171 個符號的索引，之後編輯器按一個鍵就能跳到任一函式/struct/巨集的定義。注意它同樣列出多個 `main`——ctags 是**純語法**的，不懂哪個才是「真的」，這點 Ch 14 會講清楚它的極限。

### 交叉引用：誰呼叫了事件迴圈 `aeMain`？

這是文字搜尋做不好、但讀碼最需要的問題——**反向查呼叫者**。

```
$ cscope -b -q -R -s src          # 建交叉引用庫
$ cscope -d -L -3 aeMain          # -3 = 查「誰呼叫 aeMain」
src/server.c            main            7251  aeMain(server.el);
src/redis-benchmark.c   main            1829  else aeMain(config.el);
src/redis-benchmark.c   benchmark        970  if (!config.num_threads) aeMain(config.el);
deps/hiredis/examples/example-ae.c main   59  aeMain(loop);
```

一眼看到：伺服器的主迴圈是 `server.c:7251` 的 `aeMain(server.el)`——**這就是 redis 的心臟**。你剛剛用一條指令，從十萬行裡定位到了整個系統的核心循環。這是 cscope 的殺手級用途，Ch 14 深入。

### 呼叫圖：從 `main` 往下兩層長怎樣？

```
$ cflow --depth=2 -m main src/server.c
main() <int main (int argc, char **argv) at src/server.c:6917>:
    monotonicInit()
    spt_init()
    zmalloc_set_oom_handler()
    redisOutOfMemoryHandler() <... at src/server.c:6712>:
    dictSetHashFunctionSeed()
    ...
```

`cflow` 靜態分析出 `main` 呼叫了哪些函式。配合 `graphviz` 的 `dot` 可以把它畫成真正的圖（Ch 16）。這是建立「架構地圖」的原料。

### 動態與考古（點到為止，各有專章）

```
$ gdb --version | head -1
GNU gdb (Ubuntu 12.1-0ubuntu1~22.04.2) 12.1
$ git blame -L 7251,7251 src/server.c
^c9d29f6 (YaacovHazan 2024-07-28 21:22:20 +0300 7251)     aeMain(server.el);
```

`gdb` 讓你在程式真的跑起來時斷在任一行、看實際走的路徑（Ch 18）；`git blame` 告訴你某行的來歷（Ch 17，需完整歷史——這裡因淺 clone 只顯示一個 commit）。

## 對比與取捨：什麼時候用哪把？

這是這章最該記住的一張表。四種「找符號」的工具不是誰取代誰：

| 工具 | 懂語法嗎 | 要能編譯嗎 | 速度 | 最適合 | 弱點 |
|---|---|---|---|---|---|
| ripgrep | 否（純文字） | 否 | 極快 | 找任意字串、註解、log 訊息、跨語言掃 | 同名符號會全中，不分作用域 |
| ctags | 半（認得定義語法） | 否 | 快 | 「跳到定義」離線索引 | 不會反查呼叫者；同名不分辨 |
| cscope/global | 半 | 否 | 快 | **反查呼叫者/被呼叫**（C 尤強） | C/C++ 為主，語意仍不精準 |
| clangd (LSP) | **是**（真編譯） | **是** | 中 | 精準跳轉、型別、重構、跨 TU | 要 `compile_commands.json`、較重 |

**實戰策略**（貫穿全課）：**先用 ripgrep 快速定位大概位置 → 用 ctags/cscope 建立符號與呼叫關係的骨架 → 需要精準（同名、多載、巨集展開後的真相）時才動用 clangd。** 不要一開始就想靠 clangd 解決一切，它啟動慢、對超大 C 專案容易吃力；也不要全程只用 grep，你會被同名字串淹死。

## 踩雷集錦

1. **以為裝了 `ctags` 就是對的**：Ubuntu 舊版可能裝到 Exuberant Ctags。務必確認版本字串是 `Universal Ctags`，否則現代語言（Go/Rust/TS）支援殘缺。
2. **clangd 不跳轉，狂重裝**：99% 不是 clangd 壞了，是**沒有 `compile_commands.json`**。先產生 compilation database 再說。錯誤直覺是「工具有問題」，正確認識是「工具缺它要的輸入」。
3. **在 Windows 原生硬裝索引工具**：cscope/global/clangd 在原生 Windows 上要嘛缺、要嘛半殘。這門課一律 WSL，別跟環境搏鬥。
4. **淺 clone 卻想做 git 考古**：`--depth 1` 沒有歷史，`git log`/`blame` 只有一個 commit。要考古先 `git fetch --unshallow`。
5. **索引檔忘了更新**：ctags/cscope 建的是**快照**。你 `git pull` 或切 branch 後 code 變了，索引還是舊的，跳轉會跳到錯的行號。養成「大改動後重建索引」的習慣（Ch 14 教怎麼自動化）。

## 進階：再往深一層

- **索引自動化**：每次進 codebase 手動 `ctags -R` 很煩。可以用 git hook（`post-checkout`/`post-merge`）自動重建，或用 `watchman` 監看檔案變動增量更新。Ch 14 會給 script。
- **編輯器整合**：本課刻意**不綁定特定編輯器**——方法論與工具才是重點。但實務上你會把這些工具接進 Neovim（`gutentags` + `nvim-lspconfig`）、VS Code（clangd 外掛）或 Emacs。接法各家不同，原理都是本章這幾把工具在背後跑。
- **容器化你的讀碼環境**：把整套工具鏈包成一個 Docker image（接你的 docker 課），之後 `docker run -v $(pwd):/code` 就能對任何專案開工，不污染主機。
- **`global` vs `cscope`**：兩者都做交叉引用。GNU global（`gtags`）支援更多語言、輸出更適合腳本處理、還能產 HTML 導覽；cscope 的互動式 TUI 在純 C 專案仍很順手。Ch 14 對比。

## 動手練習

1. **裝好整套並驗證版本**：跑完本章安裝指令，逐一 `--version`，確認 `ctags` 是 Universal 版。
2. **clone redis 並 `cloc`**：親手做一次，看你的數字跟本章是否一致（版本相同應該一致）。
3. **找出「假 main」**：用 `rg -n "int main" src/*.c` 列出全部 `main`，然後打開其中一個工具程式（例如 `src/crc64.c` 的 main），確認它確實是自測程式而非伺服器入口。體會「多進入點」。
4. **定位心臟**：用 `cscope` 查 `aeMain` 的呼叫者，確認 `src/server.c` 的那一行。你就找到 redis 的主迴圈了——這是 Ch 6 的預習。
5. **（選）產生 compile_commands.json**：`bear -- make -j$(nproc)`，之後在支援 clangd 的編輯器裡打開 `server.c`，試 go-to-definition。感受語意跳轉 vs ctags 跳轉的差別。

## 本章重點整理

- 讀碼的瓶頸是「找」和「連」，不是「看」；工具把每一種「找」變成秒級指令。
- 六類武器：文字搜尋(rg)、標籤索引(ctags)、交叉引用(cscope/global)、語意分析(clangd)、呼叫圖(cflow/graphviz)、統計/動態/考古(cloc/gdb/git)。
- 核心分野：**文字工具**快而通用但不懂語法，**語意工具**精準但要能編譯——互補，不是替代。
- clangd 要跳轉，先要有 `compile_commands.json`（能編譯）。
- 本課主環境 WSL Ubuntu 22.04；主沙包 redis 7.4.0。

## 自我檢核

- [ ] 不看筆記，能不能說出「文字搜尋工具」和「語意分析工具」的根本差別，以及各自何時用？
- [ ] 能解釋為什麼 clangd 需要 `compile_commands.json`，而 ripgrep 不需要？
- [ ] 面試官問「你拿到一個沒看過的 C 專案，第一步會用哪些工具、各解決什麼問題」，你能不能一口氣講出六類武器？
- [ ] 知道 `ctags` 有兩個實作、以及為什麼要確認是 Universal 版嗎？
- [ ] redis 有八個 `main`——你能說出為什麼，以及哪個才是伺服器真正的入口嗎？

## 延伸閱讀

每條都說清楚讀哪裡、學什麼、前提。

### 官方文件 / 手冊

- **[Universal Ctags 官方文件](https://docs.ctags.io/en/latest/)**
  - **讀哪裡**：先讀 "Building/parsing" 概觀與 "Option files" 一節；其餘當字典查。
  - **學到什麼**：ctags 到底怎麼認符號、支援哪些語言、如何自訂。理解它「純語法、不編譯」的本質，才知道它的極限。
  - **前提**：知道什麼是「符號定義」即可。

- **[ripgrep User Guide (GUIDE.md)](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md)**
  - **讀哪裡**：整份不長，重點看 "Manual filtering" 與 "Common options"。
  - **學到什麼**：`.gitignore` 感知、檔案型別過濾、glob——這些讓 rg 在大 repo 裡比 grep 快一個數量級。Ch 12 會深入，這裡先建立印象。

- **[clangd: Getting started / Compilation database](https://clangd.llvm.org/installation)**
  - **讀哪裡**："Project setup" 與 "Compile commands" 兩節。
  - **學到什麼**：`compile_commands.json` 的角色、怎麼替不同 build 系統產生。這是 clangd 一切功能的地基。
  - **前提**：懂基本的編譯旗標（`-I`、`-D`）會更有感。

### 部落格 / 文章

- **[cscope man page "Using cscope"](https://cscope.sourceforge.net/cscope_man_page.html)**
  - **讀哪裡**："Requesting the initial search" 那張九種查詢的表。
  - **學到什麼**：cscope 的九類查詢（找定義、找呼叫者、找被呼叫者……）正是讀碼最需要的九個問題。這張表值得貼在牆上。
  - **前提**：會基本 C 即可。

### 書籍

- **《The Art of Readable Code》** — Dustin Boswell & Trevor Foucher（O'Reilly, 2011）
  - **這本書的定位**：主題是「怎麼寫出好讀的 code」，但**反過來讀**——理解「好 code 的特徵」能讓你更快判斷陌生 code 的意圖與品質。
  - **讀哪幾章**：Part 1（Surface-Level Improvements）與 Part 4（命名/註解如何洩漏意圖）與本課讀碼視角最互補。

裝好了、沙包也 clone 了。下一章我們先退一步，談一個你可能沒認真想過的問題：**為什麼讀別人的 code 比自己寫還累？** 搞懂這個不對稱，你才知道後面所有技巧在對抗什麼。

→ [Ch 1 讀碼 vs 寫碼的不對稱](./01-reading-vs-writing.md)
