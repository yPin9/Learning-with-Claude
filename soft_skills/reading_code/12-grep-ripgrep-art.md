# Ch 12 — grep/ripgrep 的藝術

> **目標**：把「文字搜尋」從你隨手打的 `grep foo` 升級成一套精準的偵察武器。學完你能用 ripgrep 分辨「找定義 vs 找使用」、用型別與 glob 把搜尋範圍砍到只剩相關檔、用 context 一眼看清匹配周圍、用進階 regex（含 PCRE2 的 lookaround）挖出純文字工具本來抓不到的東西——並且清楚知道 ripgrep 快在哪、什麼時候該退回 `grep` 或 `git grep`。

> **環境**：WSL2 Ubuntu 22.04，ripgrep 13.0.0（Ubuntu `apt` 套件版）、GNU grep 3.7、git 2.34。所有範例在 Ch 0 的沙包 `~/reading_code_lab/redis`（redis 7.4.0）實跑，輸出照抄。**重要版本差異**：Ubuntu apt 的 ripgrep 13.0.0 **沒有編進 PCRE2**——本章 `--pcre2` 段落會示範這個踩雷，並給你補救方式。跑 `rg --version` 若第二行沒有 `+pcre2`，你就是這個狀況。

## 為什麼文字搜尋是讀碼的第一武器？

Ch 0 講過讀碼的瓶頸是「找」和「連」。所有「找」裡面，**文字搜尋是最快、最通用、啟動成本為零的那一把**。它不懂語法、不需要索引、不需要能編譯——指到目錄就能用。這既是它的弱點（會被同名字串騙），也是它的殺手級優勢：**它對任何語言、任何檔案、註解、log 訊息、設定檔、產生的 code 一視同仁地有效。**

一個資深讀碼者跟新手用文字搜尋的差距不在「會不會用 grep」，在**問對問題、寫對 pattern、圈對範圍**。新手 `grep -r foo .` 得到八百個結果然後放棄；老手用一條 `rg -t c -w 'foo' src` 直接命中十個相關的。這一章就是把後者的直覺拆開講。

先建立一個貫穿全章的心智模型：**文字搜尋是「假設的快速驗證器」**。你在讀碼時腦中不斷冒出假設——「這個值應該是在某處被設定的」「這個 error message 一定對應某段錯誤處理」「這個 flag 應該有個 enum」——每個假設都可以用一條 rg 在零點幾秒內證實或推翻。搜尋得越快越準，你的假設迭代就越快，讀碼就越快。

## ripgrep 基本盤：先跑起來

最基本的用法，在 redis 上找一個函式出現幾次：

```
$ cd ~/reading_code_lab/redis
$ rg -c "createObject" src/*.c | head -5
src/t_zset.c:1
src/t_string.c:1
src/t_stream.c:11
src/t_set.c:3
src/t_list.c:2
```

`-c` = count，每檔幾個匹配。這已經比 `grep` 好用：ripgrep 預設**遞迴、自動忽略 `.gitignore`、自動跳過二進位檔、輸出帶顏色與檔名**，全部零設定。

> **一個你一定會踩的坑（先講，免得你半路卡住）**：`rg pattern`（不給路徑）在**互動 shell** 裡會遞迴搜尋當前目錄；但如果 **stdin 被重導**（例如在腳本裡、在 `wsl -e bash -lc '...'` 裡、透過管線），ripgrep 會轉去**讀 stdin** 而不是搜尋檔案，於是「什麼都找不到」。實測：
> ```
> $ echo "nothing here" | rg "aeCreateEventLoop"
> $ echo "exit=$?"
> exit=1        # 它搜尋的是被 pipe 進來的那行字，不是 src/ 裡的檔！
> ```
> **養成習慣：永遠明確給路徑**（`rg pattern src` 或 `rg pattern .`）。這一個習慣省掉無數「rg 壞了？」的困惑。本章之後所有範例都明確帶路徑。

## 圈範圍：`-t` 型別過濾與 `-g` glob

大專案裡「範圍」比「pattern」更重要。你要找的通常只在 C source，但專案裡混著 JSON、markdown、測試資料。ripgrep 內建語言型別，`-t c` 只搜 C 檔（`.c/.h`）：

```
$ rg -t c -n "aeCreateEventLoop" src | wc -l
5
```

要看有哪些型別、`c` 型別涵蓋什麼副檔名：

```
$ rg --type-list | grep -E '^c:'
c: *.[chH], *.[chH].in, *.cats
```

`-t c` 就是這幾個 glob 的集合。要反過來排除某型別用 `-T`（大寫），例如 `rg -T json` 排除所有 JSON。

需要更精細的檔案控制時，用 `-g` glob（可多個、可用 `!` 排除）。只搜標頭檔：

```
$ rg -g '*.h' -n "aeCreateEventLoop" src
src/ae.h:93:aeEventLoop *aeCreateEventLoop(int setsize);
```

一眼看到：**函式的宣告（declaration）在 `.h`，定義（definition）在 `.c`**。用 glob 分離宣告與定義，是讀 C 的常用招。組合技：`rg -g '*.c' -g '!*test*'` 搜所有 .c 但排除檔名含 test 的。

## context：`-A`/`-B`/`-C` 看清周圍

單獨一行匹配常常不夠。`-A N`（after）、`-B N`（before）、`-C N`（both）帶出上下文。找 redis 主迴圈那一行並看它前後：

```
$ rg -n -C2 "aeMain\(server.el\)" src/server.c
7249-    setOOMScoreAdj(-1);
7250-
7251:    aeMain(server.el);
7252-    aeDeleteEventLoop(server.el);
7253-    return 0;
```

冒號（`7251:`）是匹配行，減號（`7249-`）是 context 行——ripgrep 用這個區分，你一眼分得出哪行才是命中。看函式定義的簽章與開頭幾行，`-A` 最實用：

```
$ rg -n -A3 "^void aeMain" src/ae.c
474:void aeMain(aeEventLoop *eventLoop) {
475-    eventLoop->stop = 0;
476-    while (!eventLoop->stop) {
477-        aeProcessEvents(eventLoop, AE_ALL_EVENTS|
```

三行就看出 `aeMain` 是個 `while (!stop)` 迴圈——這是 redis 的心臟。context 讓你「不用開檔就讀懂一小段」。

## 「找定義」vs「找使用」：兩種本質不同的搜尋

這是讀碼文字搜尋最重要的區分。同一個符號，你要找的是**它在哪被定義**，還是**它在哪被用到**，pattern 寫法完全不同。

**找定義**：定義有固定的語法形狀，用「行首 + 回傳型別 + 函式名 + `(`」把它釘死。找 `createStringObject` 的定義：

```
$ rg -n -e "^robj \*createStringObject\(" src/object.c
102:robj *createStringObject(const char *ptr, size_t len) {
```

`^` 錨定行首（排除縮排的呼叫），`\*` 跳脫指標星號，`\(` 跳脫括號。這一條就精準命中唯一的定義，不會混進任何呼叫點。C 函式定義的通用 pattern：`^\w[\w \*]*\bNAME\s*\(`。

**找使用**：使用散落各處、形狀不固定，這時你要的是「所有出現，但只要這個完整字」——用 `-w`（word boundary）避免 `createStringObject` 匹配到 `createStringObjectFromLongLong`：

```
$ rg -n -w "createStringObject" src/t_string.c | head -5
736:    obja = obja ? getDecodedObject(obja) : createStringObject("",0);
737:    objb = objb ? getDecodedObject(objb) : createStringObject("",0);
```

`-w` 是讀碼救命符。沒有它，搜 `set` 會匹配 `setup`、`offset`、`settings`、`reset`⋯⋯淹死你。**規則：找符號的使用一律先加 `-w`。**

這個「定義 vs 使用」的區分你要刻進肌肉記憶：**找定義 → 錨定語法形狀（`^type name(`）；找使用 → `-w` 整字匹配。** 這也正是文字工具的天花板：它靠 pattern「猜」語法，猜得再好也不懂作用域——同名的區域變數、不同檔案的 static 函式，它一律全中。要真正精準到「這個 `free` 是哪個 `free`」，得靠下一章的語意工具（clangd）。

## `-o` only-matching：把搜尋變成資料萃取

`-o` 只印出「匹配到的那一段」而非整行。這把 ripgrep 從「找行」升級成「萃取欄位」，配合 `sort | uniq -c` 就是一個迷你資料分析器。萃取 `server.c` 裡所有被呼叫的 `xxxCommand` 函式並統計：

```
$ rg -o -e "[a-zA-Z_]+Command\b" src/server.c | sort | uniq -c | sort -rn | head -6
     49 redisCommand
     13 execCommand
     11 rejectCommand
      5 processCommand
      4 pingCommand
      4 isInsideYieldingLongCommand
```

一條指令就得出「這個檔裡哪些 command 被提及最多次」的頻率表。`-o` + `uniq -c` 是我最常用的讀碼 combo 之一：找所有錯誤碼、所有 `#define` 常數、所有某前綴的符號⋯⋯任何有固定形狀的東西都能這樣統計。

## `-l` files-with-matches：先定位「哪些檔」

讀碼常常第一步不是看行，是看**「這個概念散在哪幾個檔」**。`-l` 只列出有匹配的檔名。redis 的 cluster 功能碰到哪些檔？

```
$ rg -l -t c "cluster" src | head -5
src/redismodule.h
src/blocked.c
src/networking.c
src/timeout.c
src/rdb.c
$ rg -l -t c "cluster" src | wc -l
32
```

32 個檔提到 cluster——這告訴你 cluster 是個橫切關注點（cross-cutting concern），不是單一模組。`-l` 給你「模組邊界」的第一印象，是偵察階段（Ch 5）的常用招。反過來 `--files-without-match` 列出**沒有**匹配的檔，用來找漏網之魚。

## 多模式與 `--stats`

一次找多個東西用多個 `-e`（OR 關係）。同時追 `aeMain` 和 `aeStop`：

```
$ rg -n -e "aeMain" -e "aeStop" src/server.c | head
1628: * 1. aeMain - The main server loop
7251:    aeMain(server.el);
```

`--stats` 在搜尋結尾附一份統計，量化你的搜尋規模——判斷「這個 pattern 是不是太寬」很有用：

```
$ rg --stats -t c "createObject" src 2>&1 | tail -8
108 matches
108 matched lines
21 files contained matches
183 files searched
7460 bytes printed
6303756 bytes searched
0.003156 seconds spent searching
0.004572 seconds
```

183 個檔、630 萬 bytes、**3 毫秒**搜完。這個數字順便回答了「ripgrep 到底多快」——下面就談為什麼。

## `--pcre2`：lookaround、backreference，以及一個真實踩雷

ripgrep 預設用 Rust 的正則引擎（線性時間、快、但**不支援 lookaround 和 backreference**）。要用這些進階特性得加 `--pcre2` 切換到 PCRE2 引擎。典型場景：**「找 X 但後面不能接 Y」用 negative lookahead**——例如找 `createObject` 但排除 `createObjectFromLongLong` 那類：

```
$ rg --pcre2 -n -e "createObject(?!Fro)" src/object.c | head -5
PCRE2 is not available in this build of ripgrep
```

**踩雷實錄**：Ubuntu apt 裝的 ripgrep 13.0.0 **沒編進 PCRE2**，直接報錯。確認你的 build：

```
$ rg --version
ripgrep 13.0.0
-SIMD -AVX (compiled)
+SIMD +AVX (runtime)
```

官方預編譯 binary 的版本字串會有一行 `+pcre2`；apt 版沒有，就代表不能用 `--pcre2`。**補救**：（a）從 GitHub releases 下載官方 binary，或 `cargo install ripgrep`（Cargo 版預設帶 PCRE2）；（b）臨時退回系統的 `grep -P`（GNU grep 的 PCRE 模式，同樣支援 lookaround）。用 `grep -P` 達成同樣的 negative lookahead：

```
$ grep -nP "createObject(?!Fro)" src/object.c | head -3
```

> 什麼時候真的需要 PCRE2？lookahead/lookbehind（「前面是 X 後面不是 Y」）、backreference（`\b(\w+)\s+\1\b` 找重複詞）、conditional。讀碼實務上多數搜尋**不需要**——能用基本 regex 就別開 PCRE2，因為 PCRE2 對某些 pattern 可能退化成指數時間（catastrophic backtracking），Rust 引擎則保證線性。把 PCRE2 當「明確需要 lookaround 時才掏出來」的特殊工具。

## `.gitignore` 自動感知：ripgrep 最被低估的特性

ripgrep 預設**尊重 `.gitignore`/`.ignore`/`.rgignore`**，自動跳過建置產物、`node_modules`、`.git` 等。這不只是「乾淨」，是**正確性**問題。redis build 過後 `src/` 裡有一堆 `.o` 目的檔：

```
$ find src -name "*.o" | wc -l
103
$ grep -n '\.o' .gitignore | head
2:*.o
```

103 個 `.o`，`.gitignore` 第 2 行就排除 `*.o`。現在對比 ripgrep 與 `grep -r` 搜同一個字串，看它們各自碰到幾個 `.o`：

```
$ rg --files src | grep -c '\.o$'
0                    # ripgrep 完全不看 .o（.gitignore 排除了）
$ grep -rl "createObject" src 2>/dev/null | grep -c '\.o$'
19                   # grep -r 直搗 19 個二進位 .o，噴一堆亂碼匹配
```

`grep -r` 不懂 `.gitignore`，把編譯產生的二進位 `.o` 也搜了，結果混進 19 個你根本不想要的二進位匹配（還會印一堆 `Binary file matches`）。ripgrep 直接零污染。**這是 ripgrep 相對 grep 最實際的日常優勢**：在真實（build 過的）工作目錄裡，它只搜你會 commit 的原始碼。

（副作用要知道：如果你**真的想搜** `.gitignore` 排除的東西，加 `-u`（= 不看 ignore）、`-uu`（= 連隱藏檔）、`-uuu`（= 連二進位），三個 `u` 越加越「無法無天」，等同 `grep -r` 的行為。）

## 為什麼 ripgrep 這麼快？

三毫秒搜 630 萬 bytes 不是魔法，是三個工程決策疊加（以下依 ripgrep 作者 Andrew Gallant 的技術文章與官方文件，非我杜撰）：

1. **`.gitignore`/隱藏檔剪枝**：最大的加速常常不是「搜得快」，是「**根本不去搜**」。跳過 `.git`、`node_modules`、build 產物，要搜的檔案量直接少一個數量級。這也是為何在真實專案裡 rg 常比 grep 快得誇張——它搜的檔案本來就比較少。
2. **有限自動機 + SIMD**：ripgrep 底層的 Rust `regex` crate 把正則編譯成 DFA，並用 SIMD 指令（如 `memchr` 的向量化）加速掃描。關鍵是它**保證線性時間**——不像 PCE 系列可能 catastrophic backtracking。
3. **平行搜尋**：ripgrep 預設用多執行緒，一個 thread pool 分派檔案並行搜。這台機器有 16 核心（`nproc` = 16），大 repo 下平行化收益顯著。想公平跟單執行緒工具比時可用 `-j1` 限成單執行緒。

要點：**ripgrep 快的來源，「不搜無關檔案」的貢獻往往大於「搜得快」。** 這也回頭印證本章主旨——**圈對範圍**比什麼都重要。

## 對比與取捨

四個文字搜尋工具，什麼時候用哪個：

| 工具 | 預設遞迴 | 懂 .gitignore | 進階 regex | 速度 | 最適合 |
|---|---|---|---|---|---|
| `grep` | 否（要 `-r`） | 否 | `-E`基本、`-P`需另裝 | 慢（單執行緒、逐檔） | 單檔搜尋、極可攜（哪都有）、pipe 過濾 |
| `grep -r` | 是 | **否** | 同上 | 慢，且會搜到二進位/build 產物 | 不建議在 build 過的樹用（見上面 `.o` 慘案） |
| `git grep` | 是 | **只搜已追蹤檔** | `-P` PCRE、`-E` | 快（利用 git 索引，可 `--cached`） | 在 git repo 內、只想搜已 commit 的檔、搜特定 commit/branch |
| `ag` (silver searcher) | 是 | 是 | PCRE | 快（ripgrep 出現前的王者） | 大致等同 rg 的前身，rg 出現後少用 |
| **ripgrep** | 是 | **是** | Rust regex 預設 / `--pcre2` | **最快**（平行+剪枝+SIMD） | **日常首選**：偵察、找符號、跨語言掃 |

實戰策略：**日常一律 `rg`**。只有兩種情況退回別的：（a）在一台沒裝 rg 的機器（老伺服器、精簡容器）——`grep`/`git grep` 到處都有；（b）你要搜「某個歷史 commit 的內容」或「只搜 staged 檔」——這是 `git grep <commit>` 的專屬地盤，rg 做不到（rg 只看工作目錄的當前檔案）。`git grep --cached`、`git grep <sha> -- path` 是 Ch 17 版本考古的利器，留到那章。

## 踩雷集錦

1. **錯誤直覺：「`rg foo` 不給路徑就是搜當前目錄」**。正確認識：**stdin 被重導時 rg 改讀 stdin**，在腳本/pipe/`wsl -lc` 裡會「什麼都找不到」還不報錯。永遠明確給路徑（`rg foo .`）。這是本章最容易讓人以為「工具壞了」的坑。

2. **錯誤直覺：「shell 會原樣把我的 pattern 交給 rg」**。正確認識：**shell 會先吃掉特殊字元**。你打 `rg conn->handler` 會被解讀成 shell 重導（`->` 裡的 `>` 是重導符），或 rg 把 `-` 開頭當旗標。實測我在寫這門課時就中過：`rg -n "conn->handler"` 直接報 `Found argument '->' which wasn't expected`。**解法：用 `-e` 明確指定 pattern（`rg -e "conn->handler"`），或用 `--` 終止旗標解析（`rg -- "-foo"`），並用單引號包 pattern 避免 shell 展開。**

3. **錯誤直覺：「在專案根目錄 `grep -r` 找就對了」**。正確認識：build 過的樹裡 `grep -r` 會搜進 `.o`/`.a`/`.git` 等二進位與產物（上面實測 19 個 `.o`），結果被亂碼污染。用 `rg`（自動跳過）或 `git grep`（只搜追蹤檔）。

4. **錯誤直覺：「加了 `--pcre2` 就能用 lookahead」**。正確認識：**你的 ripgrep 可能沒編進 PCRE2**（apt 版就沒有），會直接報錯。先 `rg --version` 確認有 `+pcre2`，沒有就 `cargo install` 官方版或退回 `grep -P`。

5. **錯誤直覺：「搜 `set` 就能找到 set 相關的 code」**。正確認識：不加 `-w` 會匹配 `offset`/`reset`/`settings`/`setup` 全中，淹沒你。**找符號使用永遠先加 `-w` 整字匹配。** 反之找「以 set 開頭」用 `\bset\w*`、找定義用 `^` 錨定。

## 進階：再往深一層

- **`--json` 輸出接管線**：`rg --json pattern` 吐結構化 JSON（每個匹配含檔名/行/欄/bytes offset），可餵給 `jq` 或你自己的腳本做二次處理。實跑一則（節錄）：
  ```
  $ rg --json -w "aeMain" src/server.c | head -4
  {"type":"begin","data":{"path":{"text":"src/server.c"}}}
  {"type":"match","data":{"path":{"text":"src/server.c"},"lines":{"text":"    aeMain(server.el);\n"},
    "line_number":7251,"absolute_offset":296921,
    "submatches":[{"match":{"text":"aeMain"},"start":4,"end":10}]}}
  {"type":"end","data":{...,"stats":{"elapsed":{"human":"0.000542s"},"matches":2,...}}}
  ```
  每個匹配是一筆 `match` 事件，含精確的行號、byte offset、submatch 的欄位範圍——這是機器可讀的結構，`grep` 給不出來。編輯器整合（如 Neovim 的 telescope、VS Code 搜尋）底層很多就是跑 `rg --json` 再解析這些事件。你自己寫讀碼腳本時，`--json` 讓你穩定地拿到「哪個檔哪一行哪一欄」而不用 regex 去 parse `檔:行:內容` 那種脆弱格式。
- **`-P` 的 replace 與 `-r`**：`rg -r '$1'` 配合 capture group 可以做「搜尋 + 重寫預覽」（rg 本身不改檔，但配合 `sed`/`sd` 能做大規模改名的預演）。讀碼時用來預覽「如果我把這個符號改名，會動到哪些行」。
- **`.rgignore` 客製剪枝**：你可以在讀碼時放一個 `.rgignore` 排除某些你不想看的目錄（例如 `tests/`、`vendored/`），讓每次搜尋自動聚焦核心。這是把「圈範圍」持久化的技巧。
- **`--pre` 預處理器**：搜壓縮檔、PDF、或需要解碼的內容時，`rg --pre <script>` 可以先跑一個轉換程式。讀碼上較少用，但搜 log 壓縮檔時很方便。
- **ripgrep 與 `fzf` 互動化**：`rg` 供料、`fzf` 即時過濾，組成「邊打字邊縮小結果」的互動搜尋。很多人的讀碼工作流核心就是 `rg | fzf | 開檔跳行`。這是把批次搜尋變成互動探索的關鍵一步。

## 動手練習

在 redis 沙包上做，每題都要真跑對照輸出：

1. **定義 vs 使用**：找 `dictAdd` 的**定義**（用 `^` 錨定 pattern），再找它的**使用**（用 `-w`）。比較兩者的結果數量差多少，理解為何找定義要錨定。
2. **範圍收斂**：用 `rg -l -t c "expire"` 找出 redis 的 key 過期機制散在哪些檔。挑其中一個檔用 `-C3` 讀關鍵那幾行。你能不能只靠搜尋就講出「過期大概怎麼運作」？
3. **萃取統計**：用 `rg -o` 萃取 `src/server.c` 裡所有 `server.xxx` 的欄位存取（pattern 提示 `server\.\w+`），統計最常被存取的欄位前十名。這告訴你這個檔最關心哪些全域狀態。
4. **踩雷復現**：故意跑 `rg conn->next src`（不加引號不加 `-e`），看 shell 怎麼報錯，然後用 `-e` 修好它。體會「pattern 要防 shell」。
5. **`.gitignore` 對比**：先 `make -j` 建 redis（產生 `.o`），然後對同一個字串分別跑 `rg` 和 `grep -rl`，數各自碰到幾個 `.o`。親眼看到 ripgrep 的 gitignore 感知如何避開二進位污染。
6. **PCRE2 確認**：跑 `rg --version` 確認你的 build 有沒有 `+pcre2`。有的話試 negative lookahead 排除某後綴；沒有的話用 `grep -P` 達成同樣效果。

## 本章重點整理

- 文字搜尋是讀碼「假設驗證器」：零成本、跨語言、對任何檔有效——代價是不懂語法/作用域。
- **圈範圍 > 寫 pattern**：`-t`（型別）、`-g`（glob）、`-l`（只列檔）把搜尋砍到只剩相關檔，這是速度與精準的主要來源。
- **找定義用 `^type name(` 錨定；找使用用 `-w` 整字**——這個區分要成肌肉記憶。
- `-o` + `sort | uniq -c` 把搜尋變資料萃取；`-C/-A/-B` 讓你不開檔就讀懂一小段。
- ripgrep 快在「不搜無關檔」（gitignore 剪枝）＞平行＞SIMD/DFA 線性掃描。
- 日常一律 `rg`；退回 `git grep` 只為搜歷史 commit / staged；退回 `grep` 只為極端可攜。
- 陷阱：無路徑時讀 stdin、shell 吃掉 pattern 特殊字元、apt 版無 PCRE2、`grep -r` 搜到二進位。

## 自我檢核

- [ ] 我能不查手冊寫出「找某函式定義」和「找某函式所有使用」兩條不同的 rg 指令，並解釋為何不同
- [ ] 我知道 `rg pattern`（不給路徑）在腳本/pipe 裡會失敗的原因，以及正確寫法
- [ ] 我能解釋為何在 build 過的樹裡 `grep -r` 比 `rg` 危險（`.o` 二進位污染）
- [ ] 我能說出 ripgrep 快的三個原因，並知道哪一個貢獻通常最大
- [ ] 遇到「要用 lookahead 但 `--pcre2` 報錯」，我知道怎麼確認 build 並補救
- [ ] 我理解文字搜尋的天花板（不懂作用域、同名全中），以及下一章的語意工具如何突破它

## 延伸閱讀

每條說清楚讀哪、學什麼、關聯。

### 官方文件 / 手冊

- **[ripgrep GUIDE.md（官方使用指南）](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md)**
  - **讀哪裡**：整份不長，重點看 "Automatic filtering"、"Manual filtering: globs"、"Replacements" 三節。
  - **學到什麼**：`.gitignore` 感知的完整規則、glob 與型別過濾的細節、`-r` replace。本章的實務基礎全在這。
  - **關聯**：直接補強本章「圈範圍」與「gitignore 感知」兩節。

- **[ripgrep FAQ — PCRE2 與效能](https://github.com/BurntSushi/ripgrep/blob/master/FAQ.md)**
  - **讀哪裡**："Does ripgrep support PCRE?" 與效能相關問答。
  - **學到什麼**：為何預設不用回溯式引擎、PCRE2 何時該開、build feature 差異。理解本章「apt 版無 PCRE2」踩雷的根因。

### 技術文章（作者親筆，非農場文）

- **[Andrew Gallant, "ripgrep is faster than {grep, ag, git grep, ucg, pt, sift}"](https://blog.burntsushi.net/ripgrep/)**
  - **讀哪裡**：作者本人對 ripgrep 為何快的深度剖析——DFA、SIMD、平行化、gitignore 剪枝逐一拆解，附嚴謹 benchmark 方法論。
  - **學到什麼**：本章「為什麼快」那節的權威出處。順便學到怎麼做**誠實的**效能比較（他很小心地說明測量偏差）。
  - **關聯**：想把本章的效能宣稱驗證到底，讀這篇；也是理解「有限自動機 vs 回溯」的好材料。

### 手冊

- **[`man git-grep`](https://git-scm.com/docs/git-grep)**
  - **讀哪裡**：`--cached`、`<tree-ish>`（搜特定 commit）、`-O`（開編輯器）幾個選項。
  - **學到什麼**：`git grep` 相對 rg 的獨門能力——搜歷史版本、只搜 staged。這是 rg 做不到、Ch 17 版本考古會重度依賴的。
  - **關聯**：補上本章對比表裡 `git grep` 那一格的實作細節。

搜尋能定位「文字出現在哪」，但它永遠不懂「這個 `free` 到底是 libc 的 `free` 還是某 struct 的函式指標欄位」。要跨過這道語法/語意的牆，我們需要真正懂型別與作用域的工具——下一章進入 LSP 與 clangd 的語意世界。

→ [Ch 13 LSP 與語意導航](./13-lsp-semantic-navigation.md)
