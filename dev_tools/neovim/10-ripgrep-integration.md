# Ch 10 — ripgrep 深度整合

> **目標**：搞懂 telescope 的 `live_grep` 底層那台引擎——ripgrep（rg），並把它變成你讀大 repo 全文搜的主武器。學完你知道 rg 為什麼快、能用 glob / 型別 / 排除目錄把搜尋範圍砍到只剩相關檔、會用 `live_grep` 的 `<C-space>` 二次過濾、能透過 `additional_args` 把 rg 旗標傳進 telescope、並且清楚什麼時候該跳出 nvim 直接在 shell 用 rg。

> **環境**：Neovim v0.12.4，WSL2 / Ubuntu，ripgrep 13.0.0。本章所有 rg 輸出都是在真專案 `/tmp/lua`（github.com/lua/lua，35 個 `.c` + 28 個 `.h`）實跑照抄。**版本注意**：Ubuntu 的 rg 13.0.0 沒編進 PCRE2（`rg --version` 第二行沒有 `+pcre2`），lookaround 那類進階 regex 要另外處理——本章用得到的都不依賴它。

## 為什麼需要這個？

Ch 9 教你按 `<leader>fg` 開 `live_grep`，但沒告訴你它底下是誰在幹活。答案是 **ripgrep**：你在 picker 打字，telescope 每次改字就重跑一次 `rg`，把命中丟給 sorter 排序、previewer 預覽。**telescope 的 `live_grep` 只是 rg 的一層互動皮**。

這代表兩件事。第一，rg 的能力就是 `live_grep` 的能力上限——rg 能做的過濾（限副檔名、排除目錄、詞邊界），你在 telescope 裡都能用；rg 做不到的（如 PCRE2 沒編進去的 lookaround），telescope 也做不到。第二，**懂 rg 的旗標，你才能把 `live_grep` 從「搜一坨」調成「精準命中」**。

讀大型 C 專案時，全文搜是你最快、啟動成本為零的偵察武器（這點 reading_code Ch 12 講透了）。它不懂語法、不需索引、不用能編譯——指到目錄就能用，對任何語言、註解、log、設定檔一視同仁。但新手 `grep -r foo .` 得到八百個結果就放棄，老手用一條 `rg -t c -w foo src` 直接命中十個。差距不在會不會用，在**問對問題、寫對 pattern、圈對範圍**。這章把那直覺搬進 nvim。

## 先建立直覺：rg 為什麼快

```
   傳統 grep -r              ripgrep
   ─────────────            ─────────────
   單執行緒逐檔              多執行緒並行掃多檔
   掃所有檔（含 .git/       自動讀 .gitignore，
     build/ node_modules）    跳過被 ignore 的
   掃二進位檔（噪音）        自動偵測並跳過二進位檔
   正則引擎較慢             Rust 寫的、有限自動機正則引擎，
                             SIMD 加速搜尋
```

四個原因讓 rg 在大 repo 快 grep 好幾倍：

1. **並行**：rg 用多執行緒同時掃多個檔。核越多越快，grep 吃不到這個。
2. **gitignore 感知**：rg 預設讀 `.gitignore` / `.ignore`，自動跳過 build 產物、`.git/`、`node_modules/` 這些你根本不想搜的。**搜得少 = 搜得快**，而且結果不被噪音淹。
3. **跳二進位**：自動偵測二進位檔並跳過，不會噴一堆 `Binary file matches`。
4. **快的正則引擎**：Rust 寫的、基於有限自動機（不會災難性回溯）、對簡單 literal 有 SIMD 加速。

理解「gitignore 感知」很關鍵，因為它有兩面。好處是預設乾淨。壞處是**你要找的東西如果被 gitignore 了，rg 預設找不到**——這是 telescope `find_files`「找不到某檔」和 `live_grep`「搜不到某字」的常見原因。要搜被 ignore 的檔加 `-u`（`--no-ignore`，一個 `-u` 不理 gitignore、`-uu` 連 hidden 也搜、`-uuu` 連二進位也搜）。

## rg 直接在 shell 用：nvim 之外的補充

有些搜尋在 shell 直接跑比開 telescope 快——尤其是「我只想看個 count」或「要 pipe 給別的工具」。這些是你 nvim 外的補充武器，全部在 `/tmp/lua` 真跑：

**詞邊界 `-w`：找精確的識別字，不被子字串污染。** 找 `luaL_newstate` 這個函式（`-n` 帶行號）：

```
$ cd /tmp/lua
$ rg -w "luaL_newstate" -n *.c *.h
ltests.h:126:#define luaL_newstate()  \
lauxlib.h:104:LUALIB_API lua_State *(luaL_newstate) (void);
lua.c:779:  lua_State *L = luaL_newstate();  /* create state */
lauxlib.c:1197:LUALIB_API lua_State *(luaL_newstate) (void) {
```

四行就把「宣告在哪（`.h`）、定義在哪（`.c` 那行 `{`）、誰呼叫（`lua.c`）」全攤開。`-w` 讓 `newstate` 不會誤中 `luaL_newstate_extra` 之類的東西。

**型別過濾 `-t`：只搜某語言。** 數 `lua_pcall` 在各 C 檔出現幾次（`-c` = count）：

```
$ rg -c "lua_pcall" *.c | head -6
lua.c:4
ltests.c:4
ldo.c:7
ldblib.c:1
lbaselib.c:2
lapi.c:1
```

`ldo.c` 出現 7 次——這暗示 protected call 的核心邏輯在 `ldo.c`。**count 是偵察利器：出現最多次的檔通常是那功能的核心**。`-t c` 等同「只搜 `.c`/`.h`」，在混著 markdown/JSON 的專案能砍掉一堆噪音。

**glob 分離宣告與定義。** 只搜 header 找宣告：

```
$ rg -g "*.h" -n "lua_newstate" .
./lua.h:163:LUA_API lua_State *(lua_newstate) (lua_Alloc f, void *ud, unsigned seed);
./ltests.h:127:	lua_newstate(debug_realloc, &l_memcontrol, luaL_makeseed(NULL))
```

`-g '*.h'` 只看標頭，一眼看到 `lua_newstate` 的正式簽章。反過來 `-g '*.c'` 找定義。組合 `-g '*.c' -g '!*test*'` 搜所有 .c 但排除檔名含 test 的。

**context `-A`/`-B`/`-C`：看命中周圍。** 看某函式的簽章與開頭：

```
$ rg -n -A2 "LUA_API int lua_pcallk" lapi.c
1076:LUA_API int lua_pcallk (lua_State *L, int nargs, int nresults, int errfunc,
1077-                        lua_KContext ctx, lua_KFunction k) {
1078-  struct CallS c;
```

冒號（`1076:`）是命中行，減號（`1077-`）是 context。`-A N`（after）、`-B N`（before）、`-C N`（兩邊）。找函式定義時 `-A3`、找「這變數哪來的」時 `-B3`。

> **一個一定會踩的坑**：`rg pattern`（不給路徑）在互動 shell 遞迴搜當前目錄，但**在腳本 / pipe / stdin 被重導時，rg 會轉去讀 stdin** 而不是搜檔案，於是「什麼都找不到」。養成習慣：**永遠明確給路徑**（`rg pattern .` 或 `rg pattern src`）。這一個習慣省掉無數「rg 是不是壞了」的困惑。

## 讀大 repo 的搜尋策略：圈範圍比 pattern 重要

大專案裡，**「搜哪裡」比「搜什麼」更決定成敗**。同一個 pattern，全 repo 搜炸出兩千個命中，圈對範圍剩十個。策略優先序：

1. **先圈語言 / 副檔名**：讀 C 專案幾乎永遠 `-t c`（或 telescope 裡設 additional_args）。把 markdown、JSON、測試 fixture 全排掉。
2. **再圈目錄**：知道功能大概在哪個子目錄，直接指路徑（`rg foo src/net`）。kernel 那種規模，指對子系統目錄能把搜尋從幾萬檔砍到幾百檔。
3. **用 `-w` 圈詞邊界**：找識別字時幾乎都該加 `-w`，避免子字串污染。
4. **排除目錄**：`-g '!tests/'`、`-g '!third_party/'` 把不相干的大目錄砍掉。
5. **命中還太多就二次過濾**：telescope live_grep 的 `<C-space>`（下一節）或 shell 裡 `rg foo src | rg bar` 二段管線收窄。

這對應 reading_code 的「假設驅動」：每個假設（「這功能應該在 net 子系統」「這 error 應該有對應的 enum」）都用一條圈好範圍的 rg 在零點幾秒內證實或推翻。範圍圈得準，假設迭代就快。

## telescope 裡的 rg：live_grep 的進階操作

回到 nvim。`live_grep` 底層是 rg，這些操作讓它從「搜一坨」變「精準命中」。**互動 UI 無法貼截圖，以下為鍵位操作；底層 rg 命令與 telescope 命令已在 Ch 9 headless 驗證存在。**

**`<C-space>` 二次過濾**：`live_grep` 的殺手鍵。你搜 `throw` 得到一堆命中，按 `<C-space>` 把當前結果「凍」成新的搜尋範圍，再打字就是**在這批結果裡再過濾**。讀碼情境：先 `luaD_throw` 找到所有 throw，`<C-space>` 後打 `ERRMEM` 只留記憶體錯誤那幾個。等同 shell 的 `rg throw | rg ERRMEM` 但在互動 UI 裡做。

**`additional_args` 把 rg 旗標傳進 telescope**：`live_grep` 預設不加 `-w`、不限型別。要讓它只搜 header 或加詞邊界，透過 picker 的 `additional_args`。做一個「只搜 `.h` 的 live_grep」keymap：

```lua
-- 往 config 的 keys 加：只搜 header 的 live_grep（傳 rg 的 --glob 給 telescope）
{ "<leader>fh", function()
    require("telescope.builtin").live_grep({
      additional_args = function() return { "--glob", "*.h" } end,
    })
  end },
```

`additional_args` 回傳的 table 會原封不動接到 rg 命令後面。你能傳 `-w`（詞邊界）、`-t c`（型別）、`--no-ignore`（搜被 ignore 的）等任何 rg 旗標。**這是把「圈範圍」焊進一個專用 picker 的方法**——常用的範圍做成專屬 keymap，不用每次手調。

**`grep_string` 就是把游標下的字丟給 rg**（Ch 9 講過）：`<leader>fw`，等同 `rg -w <游標下的字>`。省去手打。

## 連 reading_code Ch 12：grep 的藝術是同一套

這章的 rg 直覺跟 `reading_code` Ch 12「grep/ripgrep 的藝術」是**鏡像**——那邊在純 shell 裡把 rg 練成偵察武器（型別過濾、glob、context、PCRE2 lookaround、`git grep` 的取捨），這邊把同一套能力接進 telescope 的 `live_grep` 與 `grep_string`。

差別只在「互動 vs 一次性」：telescope 適合「邊搜邊看 previewer、邊收窄」的探索式搜尋；shell rg 適合「我知道要什麼、要 count 或要 pipe」的一次性查詢。**兩個都要會，看情境切**。reading_code 那章把 rg 的每個旗標講到底（含 PCRE2 沒編進去的踩雷、`git grep` 何時更快），是這章的完整版，讀碼卡在「搜不到」時回去翻它。

## 鍵位 / 命令表

| 情境 | 操作 | 說明 |
|---|---|---|
| nvim：全文即時搜 | `<leader>fg` → 打字 | live_grep，底層 rg |
| nvim：結果內二次過濾 | picker 裡 `<C-space>` → 再打字 | 凍住當前結果再篩 |
| nvim：搜游標下的字 | `<leader>fw` | grep_string，等同 `rg -w <字>` |
| nvim：只搜 header | `<leader>fh`（上面加的） | live_grep + `--glob *.h` |
| shell：精確識別字 | `rg -w foo src` | 詞邊界，不中子字串 |
| shell：只搜某語言 | `rg -t c foo src` | 型別過濾 |
| shell：限副檔名 | `rg -g '*.h' foo .` | glob，可多個、`!` 排除 |
| shell：看命中周圍 | `rg -A3 -B3 foo .` | context |
| shell：數出現次數 | `rg -c foo *.c` | count，找核心檔 |
| shell：搜被 gitignore 的 | `rg -u foo .` | `-uu` 含 hidden、`-uuu` 含二進位 |

## 對比與取捨

| 工具 | 何時用 | 別用時機 |
|---|---|---|
| **telescope live_grep** | 探索式：邊搜邊看預覽、想二次收窄 | 只要 count / 要 pipe 給別的工具 |
| **shell rg** | 一次性查詢、要 count、要 pipe、寫進腳本 | 想邊看 previewer 邊挑 |
| **git grep** | 只搜 tracked 檔、想搜歷史版本 | 想搜未 tracked 的產生檔 |
| **grep** | rg 沒裝的機器（幾乎不會） | 有 rg 就別用，慢又吵 |

| rg 旗標 | 作用 | 讀碼用途 |
|---|---|---|
| `-w` | 詞邊界 | 找識別字不被子字串污染 |
| `-t c` / `-T json` | 含 / 排除型別 | 圈語言範圍 |
| `-g '*.h'` / `-g '!test*'` | glob 含 / 排除 | 分離宣告定義、砍測試 |
| `-c` | 每檔命中數 | 找功能核心檔 |
| `-A/-B/-C` | context | 看命中周圍 |
| `-u/-uu/-uuu` | 逐級無視 ignore | 搜被 gitignore 的檔 |

## 踩雷集錦

1. **live_grep 搜不到某字，但你確定它在**：多半該檔被 `.gitignore` 了（產生的檔、build 產物）。rg 預設尊重 gitignore，telescope 繼承這行為。用 `additional_args` 傳 `--no-ignore`，或直接在 shell `rg -u`。
2. **telescope live_grep 完全空的**：沒裝 `rg`。`live_grep` 底層硬依賴 ripgrep，沒有它整個 picker 是空的。`:checkhealth telescope` 會點名缺哪個外部工具。
3. **`grep_string` / live_grep 炸出上千結果**：pattern 太泛（單字母、常見英文字）。加 `-w` 詞邊界、圈型別 `-t c`、或用 `<C-space>` 二次過濾。別硬翻兩千個命中。
4. **shell 裡 `rg foo` 突然沒結果**：stdin 被重導了（在腳本 / pipe / 某些 SSH 情境）。rg 轉去讀 stdin 不搜檔案。**永遠明確給路徑** `rg foo .`。
5. **想用 lookaround / backreference 卻報錯**：Ubuntu apt 的 rg 13.0.0 沒編進 PCRE2，`--pcre2` 那類進階 regex 不能用（`rg --version` 第二行沒 `+pcre2` 就是這狀況）。改用預設引擎能表達的寫法，或裝有 PCRE2 的 rg 版本（reading_code Ch 12 有補救方式）。

## 進階：再往深一層

- **`--json` 輸出**：`rg --json` 吐結構化 JSON，適合餵給腳本或別的工具做二次處理。telescope 底層某種程度就在解析 rg 的輸出。
- **`.ignore` / `.rgignore` 檔**：在專案根放 `.rgignore` 可以只給 rg 額外的忽略規則（不動 `.gitignore`）。讀碼時把「總是不想搜的大目錄」寫進去，全 repo 搜自動乾淨。
- **`--pre` 前處理**：rg 能對特定副檔名先跑一個前處理器再搜（例如搜 `.gz` 前先解壓、搜 PDF 前先抽文字）。讀碼偶爾對壓縮的 log 有用。
- **`-t c` 涵蓋什麼**：`rg --type-list | grep '^c:'` 看 c 型別的 glob 定義（`*.[chH]` 等）。內建型別不夠精確時用 `--type-add` 自訂。

## 本章重點整理

- telescope 的 `live_grep` 底層就是 **ripgrep**；rg 的能力就是 live_grep 的能力上限。
- rg 快的四個原因：**並行、gitignore 感知、跳二進位、快的正則引擎**。gitignore 感知有兩面——預設乾淨，但被 ignore 的東西預設搜不到（`-u` 解）。
- 大 repo 搜尋策略：**圈範圍比寫 pattern 重要**——先圈語言（`-t c`）、再圈目錄、加 `-w` 詞邊界、排除大目錄、命中太多就二次過濾。
- telescope 裡：`<C-space>` 二次過濾、`additional_args` 把 rg 旗標（glob / 型別 / 詞邊界）傳進 live_grep、`grep_string` 搜游標下的字。
- 這章跟 `reading_code` Ch 12 是**鏡像**：telescope 適合探索式、shell rg 適合一次性/count/pipe，兩個都要會。

## 自我檢核

- [ ] 我能說出 rg 快的四個原因，並解釋 gitignore 感知為什麼會導致「搜不到」
- [ ] 我知道大 repo 該先圈範圍，能列出 `-t`、`-g`、`-w`、目錄路徑各圈什麼
- [ ] 我能用 `<C-space>` 在 live_grep 結果裡二次過濾，說得出它等同 shell 的哪個管線
- [ ] 我會用 `additional_args` 做一個「只搜某副檔名」的專用 live_grep keymap
- [ ] 我知道 telescope live_grep 空的第一個要查什麼（rg 有沒有裝），以及 stdin 重導的坑

## 延伸閱讀

- **`reading_code` Ch 12「grep/ripgrep 的藝術」**（本課的姊妹課）
  - **讀哪裡**：整章；這是 rg 的完整版——型別過濾、glob、context、PCRE2 lookaround 與它沒編進去的踩雷、`git grep` 何時更快。本章是它接進 telescope 的鏡像，搜不到時回去翻
- **[ripgrep GUIDE.md](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md)** — Andrew Gallant（rg 作者）
  - **讀哪裡**：`Automatic filtering`（gitignore 行為）、`Manual filtering: globs`、`Common options`；把 rg 從「會用」讀到「懂為什麼」
- **`rg --help`（或 `man rg`）**
  - **讀哪裡**：`-t/--type`、`-g/--glob`、`-u/--unrestricted`、`-w/--word-regexp` 這幾個；讀碼 90% 只用這幾個旗標
- **對照本課**：Ch 9（telescope 四件套，live_grep 是其一個 picker）；Ch 12（把 rg 命中透過 `<C-q>` 送 quickfix 變工作清單）

rg 讓你在大 repo 搜得快又準。但 telescope 不是唯一的搜尋前端——有另一條路叫 fzf-lua，底層用 C 的 fzf，在超大結果集比 telescope 順。下一章我們看這條替代路線與它的取捨，讓你知道何時該切換。

→ [Ch 11 fzf-lua 路線與取捨](./11-fzf-lua.md)
