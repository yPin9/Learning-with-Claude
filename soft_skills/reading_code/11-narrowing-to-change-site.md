# Ch 11 — 從 50 萬行收斂到你要改的 200 行

> **目標**：把 Part 2 學到的偵察、追蹤、假設驅動，收束成一個最實用的技能——**定位**。面對一個幾十萬行的專案，你要在時限內從「整座工廠」收斂到「我真正要改/要看的那 200 行」。讀完你掌握六種切入法（症狀字串、公開 API、測試、git 歷史、config 選項、二分法），並能在 redis 上實跑至少兩種，把「想改 maxmemory 淘汰策略」這個需求，在 30 分鐘內定位到 `evict.c` 的關鍵函式。

## 定位是讀碼的最後一哩，也是最值錢的一哩

前四章你學會了追資料（Ch 8）、看控制流（Ch 9）、用假設破案（Ch 10）。但這些都預設一件事：**你已經知道要看哪裡了。** 現實中最大的卡點往往在更前面——「這個 50 萬行的專案，我到底該從哪一行開始？」

這是逆向工程裡最核心的技能之一。攻堅一個大 binary，你不會從 entry point 一路 trace 到目標——那要跑到天荒地老。你會用各種**旁門左道的線索**直接跳到目標附近：字串引用（`Bad password` 這句話在哪被 push？）、import 表（誰呼叫了 `RegOpenKey`？）、交叉引用。**定位的本質，是「不從頭讀，而是找一個能直接跳到目標的線索」。** 讀 source 有一整套對應的線索，而且比 binary 更豐富——你有原始碼、有註解、有測試、有 git 歷史。

一個核心心智模型：**每個「你要改的地方」都在系統裡留下了指紋，而且不只一種。** 你的工作不是「讀懂全部再找到它」，而是「挑一個最快能命中的指紋去查」：

```
   你要改的那 200 行
         ▲
         │  它留下的「指紋」——每一種都是一條捷徑：
         │
   ┌─────┴──────────────────────────────────────────┐
   │ 症狀字串   error message / log 直接 rg           │  最快，有錯誤訊息時首選
   │ 公開 API   從使用者看得到的入口往下追            │  知道功能名時
   │ 測試       哪個 test 覆蓋這功能                  │  有測試時，等於作者標好的地圖
   │ git 歷史   最近誰改了這塊 / 某行怎麼來的          │  「上次改動」線索
   │ config 名  設定選項名反查程式碼                  │  功能由開關控制時
   │ 二分法     git bisect：行為何時變的              │  「以前對現在錯」時
   └────────────────────────────────────────────────┘
```

**選哪一種，取決於你手上有什麼線索。** 有錯誤訊息用症狀字串，有測試用測試，「以前好好的最近壞了」用二分法。熟手的本事在於**看一眼需求就知道該掏哪把鑰匙**。下面逐一拆解，並在 redis 上實跑。

## 切入法一：症狀字串——有錯誤訊息時，最快的捷徑

如果你手上有一句具體的字串——error message、log 行、面板上的提示——那幾乎永遠是最快的入口。因為**那句話一定在某個 source 檔裡被寫死**，直接 rg 就命中。

情境：某個 client 送命令收到 `OOM command not allowed when used memory > 'maxmemory'.`，你想知道這錯誤是哪裡發出的、觸發條件是什麼。實跑（真實輸出）：

```
$ rg -n "OOM command not allowed" src/
src/server.c:1893:  "-OOM command not allowed when used memory > 'maxmemory'.\r\n"));
```

**一條 rg，從十萬行直接命中一行。** 打開 `src/server.c:1892` 附近，看到這句話被包成一個共享物件 `shared.oomerr`。但錯誤字串的定義處通常不是你要改的地方——**你要的是「誰在什麼條件下丟出這個錯誤」**。順藤摸瓜（Ch 8 的技巧），rg 這個共享物件的使用點（真實輸出）：

```
$ rg -n "shared.oomerr" src/*.c
src/server.c:1892:    shared.oomerr = createObject(OBJ_STRING,sdsnew(...));   ← 定義
src/server.c:4050:            rejectCommand(c, shared.oomerr);                 ← 觸發點！
src/script.c:455:        *err = sdsdup(shared.oomerr->ptr);
src/module.c:6416:               sds msg = sdsdup(shared.oomerr->ptr);
```

`src/server.c:4050` 的 `rejectCommand(c, shared.oomerr)` 就是觸發點。看它的上下文（就是 Ch 8 提過的 `processCommand` OOM 區塊，`src/server.c:4037` 起，真實 code）：

```c
    if (server.maxmemory && !isInsideYieldingLongCommand()) {
        int out_of_memory = (performEvictions() == EVICT_FAIL);   // ← 先嘗試淘汰
        ...
        if (out_of_memory && is_denyoom_command) {
            rejectCommand(c, shared.oomerr);                       // ← 淘汰失敗才報 OOM
            return C_OK;
        }
```

**三步從一句錯誤訊息，定位到了核心邏輯**：`rg 錯誤字串 → rg 錯誤物件的使用點 → 讀觸發上下文`。而且這一步還意外收穫了下一個關鍵函式 `performEvictions`——它才是「淘汰」真正發生的地方。**症狀字串常常不直接命中改動點，但它命中的位置離改動點只差一兩跳。**

> 這正是 binary RE 裡「找字串交叉引用」的 source 版。差別是 source 更好用：你能一路 rg 下去追共享物件的每個使用點，binary 裡你得手動看每個 xref。**有錯誤訊息/log 而不先 rg 它，是新手最常見的浪費。**

## 切入法二：config 選項名反查——功能由開關控制時

情境（本章主線需求）：**「我想改 redis 的 maxmemory 淘汰策略」**。使用者面對的介面是配置項 `maxmemory-policy`（值有 `allkeys-lru`、`volatile-ttl` 等）。config 選項名是絕佳的定位線索——它一定在某處被註冊、被讀取。

先反查這個選項名在哪註冊（真實輸出）：

```
$ rg -n '"maxmemory-policy"|"maxmemory"' src/config.c
3133:  createEnumConfig("maxmemory-policy", NULL, MODIFIABLE_CONFIG, maxmemory_policy_enum,
                        server.maxmemory_policy, MAXMEMORY_NO_EVICTION, NULL, NULL),
3211:  createULongLongConfig("maxmemory", NULL, MODIFIABLE_CONFIG, 0, ULLONG_MAX,
                        server.maxmemory, 0, MEMORY_CONFIG, NULL, updateMaxmemory),
```

兩個發現：(1) 選項對到 `server.maxmemory_policy` 這個全域欄位、用 `maxmemory_policy_enum` 這張枚舉表；(2) `maxmemory` 對到 `server.maxmemory`，還掛了個 callback `updateMaxmemory`。**config 反查直接給你「這個選項對應哪個變數」——接下來只要追這個變數在哪被讀。**

追 `server.maxmemory_policy` 這個決定「怎麼淘汰」的欄位，用 rg 做一次全域「熱度掃描」，看它在哪個檔案最密集（真實輸出）：

```
$ rg -c "maxmemory|evict" src/*.c | sort -t: -k2 -rn | head
src/evict.c:121        ← 遙遙領先！
src/server.c:58
src/networking.c:26
src/config.c:22
src/module.c:21
```

**`src/evict.c` 有 121 個相關命中，是第二名的兩倍。** 這是一個極強的定位訊號——**淘汰邏輯的重心，毫無疑問在 `evict.c`。** 你甚至還沒讀一行，就從整個 src/ 收斂到了一個 500 行的檔案。這就是「rg 熱度掃描」的威力：**不是找某一行，而是找『哪個檔案是這個主題的家』。**

打開 `evict.c`，rg 它的函式定義（Ch 5 學過的骨架掃描），`performEvictions`（`src/evict.c:520`）立刻跳出來——它就是我們從切入法一也追到的同一個函式。**兩條獨立的路（症狀字串、config 名）匯聚到同一個目標，這是定位正確的最強確認。** 淘汰策略的核心決策在 `evictionPoolPopulate`（挑淘汰候選）和 `performEvictions`（執行淘汰迴圈，`src/evict.c:677` 那行 `dbGenericDelete` 就是真正刪 key 的地方）。

**需求「改淘汰策略」→ 收斂到 `evict.c` 的 `performEvictions` / `evictionPoolPopulate`，全程約十分鐘、兩條 rg。** 這就是本章標題承諾的「30 分鐘從十萬行到關鍵函式」，實際上更快。

## 切入法三：測試——作者親手畫好的地圖

一個有測試的專案，等於作者幫你標好了「這個功能長怎樣、邊界在哪、怎麼觸發」。**找到覆蓋某功能的測試，你就找到了那個功能的活文件。** 實跑找 maxmemory 的測試：

```
$ ls tests/unit/maxmemory.tcl
tests/unit/maxmemory.tcl

$ head -20 tests/unit/maxmemory.tcl
start_server {tags {"maxmemory" "external:skip"}} {
    r config set maxmemory 11mb
    r config set maxmemory-policy allkeys-lru
    ...
    # fill 5mb using 50 keys of 100kb
    for {set j 0} {$j < 50} {incr j} {
        r setrange $j 100000 x
```

這個測試直接示範了「怎麼觸發淘汰」：設 `maxmemory 11mb`、設 policy、灌到超過限制。**這比你自己猜「怎麼讓淘汰發生」快得多**——測試就是可執行的規格。而且它給了你一個現成的實驗環境：改了 `evict.c` 之後，跑這個測試就知道有沒有改壞。

測試作為定位線索的三重價值：**(1) 告訴你功能怎麼觸發**（省去自己造輸入）、**(2) 定義正確行為**（當 oracle，接 Ch 10 的假設驗證）、**(3) 測試名/tag 本身是關鍵字**（`tags {"maxmemory"}` 幫你反查相關測試）。拿到陌生功能，**先問「有沒有測試覆蓋它」**，常常一步到位。

## 切入法四：git 歷史——「最近改動」與「這行怎麼來的」

程式碼是長出來的，git 記著每一次生長。兩種定位用法：

**(a) 這個檔案/功能最近被誰改過？** 定位「近期相關改動」——你要改的東西，別人可能剛動過，看他們怎麼改是最好的示範。實跑（真實輸出）：

```
$ git log --oneline -6 -- src/evict.c
0b3439692 Change license from BSD-3 to dual RSALv2+SSPLv1 (#13157)
8cd62f82c Refactor the per-slot dict-array db.c into a new kvstore data structure (#12822)
2f6d4daba Fix outdated LFU comments to eliminate confusion (#12244)
9ee1cc33a Make the sampling logic in eviction clearer (#12781)
eb392c0a6 replace calculateKeySlot with known slot in evictionPoolPopulate (#12777)
```

一眼看出淘汰邏輯近期的改動主題：sampling 邏輯（#12781）、evictionPoolPopulate 的 slot 優化（#12777）。**要改淘汰策略，`git show 9ee1cc33a` 看「上次有人動 sampling 邏輯時改了哪些行」，等於拿到一張『這功能的可改點在哪』的地圖。**

**(b) 這行是怎麼來的（pickaxe）？** `git log -S` 找「某個字串/符號首次出現或消失的 commit」。想知道 `maxmemory_policy_enum` 這張枚舉表是哪個 commit 引入的（真實輸出）：

```
$ git log --oneline -S "maxmemory_policy_enum" -- src/config.c
803d765d4 Refactored renaming types in config
50b41b6ad CONFIG SET refactoring: use enums in more places.
8e219224b CONFIG refactoring: configEnum abstraction.
```

pickaxe 讓你直接跳到「引入這個機制的那次改動」，讀那個 commit 的 diff + message，往往比讀最終 code 更快懂設計意圖——因為 commit message 講的是「為什麼」。（注意：本沙包是完整 clone，有 12205 個 commit；若你用 `--depth 1` 淺 clone，得先 `git fetch --unshallow` 才有歷史，見 Ch 0 踩雷。）

## 切入法五：二分法（git bisect）——「以前對、現在錯」時的定位神器

前面幾種定位「code 在哪」。二分法定位的是**「行為是哪次 commit 變的」**——當你面對「某個版本還好好的，現在壞了/變慢了」，而中間有幾百上千個 commit，手動一個個看是災難。`git bisect` 用二分搜尋，`log2(N)` 次就能揪出兇手：1000 個 commit 只要試約 10 次。

流程（概念示範，因需要兩個已知好/壞版本，此處標**未實測，說明用法**）：

```
$ git bisect start
$ git bisect bad                    # 現在這版：壞的
$ git bisect good 7.2.0             # 已知好的舊版
   # git 自動 checkout 中間點，你編譯、跑那個觸發實驗（最好是自動化腳本）
$ git bisect run ./my_test.sh       # 讓 git 自動跑腳本判定每個中間點好壞
   ...
   abc1234 is the first bad commit  # 直接指出引入問題的那次 commit
$ git bisect reset                  # 收工，回到原本狀態
```

`git bisect run` 是精華：給它一個「回 0=good、非 0=bad」的腳本，它全自動二分到底，你去喝杯咖啡回來就有答案。**這是把「定位一個行為變化」從幾小時壓到幾分鐘的核武。** redis 這種有完整測試套件的專案，`bisect run` 直接餵 `runtest --single unit/maxmemory` 就能自動化。

二分法的哲學延伸到沒有 git 的場景：**任何「範圍收窄」都可以二分。** 不確定 bug 在哪半段 code？註解掉一半看還壞不壞。不確定哪個配置項導致問題？砍掉一半配置試。**二分是定位的元技巧**，git bisect 只是它最成熟的工具化。

## 切入法六：公開 API 往下追——知道功能名時

如果你知道要改的是某個「使用者看得到的功能」（一個命令、一個 API 函式），從它的公開入口往下追是最直接的。redis 裡「公開入口」就是命令處理函式：想改 `GET` 的行為？`ctags`/`cscope` 跳到 `getCommand`，往下讀。想改淘汰？從命令表找哪個命令觸發淘汰檢查（`processCommand` 裡的 `performEvictions`）。

這招的關鍵是**找到「使用者概念」到「code 符號」的映射**。redis 的映射在命令表（`src/commands.def`）、config 表（`src/config.c`）、和命名慣例（`XxxCommand`）裡。**先建立「使用者說的功能」對應「哪個符號」，再用 Part 3 的導航工具（ctags/cscope/clangd）從那個符號往下鑽。** 這是最「正統」但也最慢的一種——當更快的線索（症狀字串、config 名、測試）都不適用時的保底方法。

## 對比與取捨：拿到需求，先掏哪把鑰匙？

| 切入法 | 前提線索 | 速度 | 精度 | 最適合的需求形態 |
|---|---|---|---|---|
| **症狀字串** | 有 error/log 字串 | 最快 | 高（常差一兩跳） | 「出現這個錯誤/訊息」 |
| **config 名反查** | 功能由設定控制 | 快 | 高 | 「改某配置項的行為」 |
| **測試** | 專案有測試 | 快 | 高 + 附觸發法 | 「改某功能」且想要現成實驗環境 |
| **git 歷史** | 有完整歷史 | 中 | 中高 | 「這塊最近怎麼改的/這行怎麼來的」 |
| **git bisect** | 有 good/bad 版本 | 中（可自動化） | 極高 | 「以前對現在錯/變慢了」 |
| **公開 API 往下** | 知道功能對應符號 | 慢 | 中 | 其他線索都沒有時的保底 |

**實戰不是選一種，是排序組合**：先用最快的線索（有字串就 rg 字串、有 config 就反查）粗定位到「哪個檔案/函式」，再用測試當實驗環境驗證、用 git 歷史看設計意圖。**多條線索匯聚到同一個目標，就是定位正確的確認**（本章 config 名和症狀字串都指向 `evict.c`/`performEvictions`，這種交叉印證讓你敢下手改）。

## 踩雷集錦

1. **錯誤直覺：「要改一個功能，得先把相關模組整個讀懂」。**
   正確認識：定位的整個精神是**不先讀懂全部**。你只需要收斂到那 200 行、讀懂它們、以及它們的直接上下游。想「先全懂再動手」是新手在大專案前癱瘓的頭號原因。**先定位、再局部深讀**，別反過來。

2. **錯誤直覺：「rg 一個關鍵字沒命中，代表這功能不在這專案」。**
   正確認識：更可能是**你搜的詞不對**。功能可能用不同術語（你搜 `eviction`，code 寫 `evict`/`freeMemory`）、字串被拆開或用巨集拼接、或大小寫不同。搜不到就換同義詞、搜更短的詞根（`evict` 而非 `eviction`）、`rg -i` 忽略大小寫。**「沒搜到」幾乎總是搜法問題，不是不存在。**

3. **錯誤直覺：「錯誤訊息就是問題發生的地方」。**
   正確認識：錯誤訊息的**定義處**通常只是個字串常量（`shared.oomerr` 在 `server.c:1892` 只是宣告）。真正的問題在**丟出它的地方**（`rejectCommand` at `4050`），甚至在更上游「為什麼會 OOM」（`performEvictions` 淘汰失敗）。**命中字串只是起點，要繼續 rg 它的使用點往上游追。**

4. **錯誤直覺：「git bisect 要手動 checkout 每個 commit 慢死了」。**
   正確認識：`git bisect run <script>` 全自動。你只要寫一個「好回 0、壞回非 0」的判定腳本（跑個測試、grep 個輸出），bisect 自己二分到底。不知道有 `run` 而手動 bisect，是把神器當普通工具用。

5. **錯誤直覺：「rg 熱度掃描（哪個檔案命中最多）不準，只是巧合」。**
   正確認識：`rg -c` 全域計數再排序，是**極可靠**的「找主題之家」訊號。`evict.c` 對 `maxmemory|evict` 命中 121 次碾壓其他檔，不是巧合——那就是這個主題的核心實作檔。當然要配合常識排除（config.c 命中多是因為註冊，不是實作），但作為第一收斂步，熱度掃描性價比極高。

## 進階：再往深一層

- **組合拳的標準流程**：真實定位很少單用一招。標準組合是——**症狀（字串/config/需求）粗定位 → rg 熱度掃描收斂到檔案 → ctags/cscope 收斂到函式 → 測試當實驗環境 → git 歷史看設計意圖與可改點 →（若是行為退化）bisect 揪 commit**。把這條流水線內化成肌肉記憶，任何「我要改 X」的需求都能在半小時內落地到具體函式。這正是 Ch 38 要你打造的個人 SOP 的核心。

- **反查「使用者概念 → 符號」的映射表**：每個成熟專案都有幾張「把使用者語言翻譯成 code 符號」的表——redis 的 `commands.def`（命令→函式）、`config.c`（選項→變數）、error 字串表。**新到一個專案，先找到這幾張映射表在哪**，之後所有「改某功能」的需求都能經由它們快速跳轉。找到映射表 = 找到專案的「索引頁」。

- **當專案沒有你要的線索時**：沒有錯誤訊息、沒有測試、沒有清楚的 config 名（爛 code 或內部專案常見）——這時退回動態。跑起來、用 `strace`/`ltrace`（Ch 19）看它做了什麼 syscall、用 gdb 在你猜的入口下斷點看走不走到、用 `perf`/coverage 看某操作點亮了哪些函式。**動態定位是靜態線索失效時的保底**：讓程式自己告訴你它在哪執行。

- **定位「效能問題」的特化**：需求若是「這個操作太慢」，定位手段換成 profiler。`perf record` 跑那個慢操作 → `perf report` 看時間花在哪些函式 → 火焰圖（flame graph）一眼看出熱點。這是「找改動點」的效能版——把「哪 200 行值得改」變成「哪 200 行吃掉 80% 時間」。Ch 19 深入。

## 動手練習

在 `~/reading_code_lab/redis`（完整 clone，有 git 歷史）上做，貼真跑輸出：

1. **症狀字串定位**：挑另一句 redis 錯誤訊息（例如 `WRONGTYPE Operation against a key`），用「rg 字串 → rg 使用點 → 讀觸發上下文」三步，定位到它被丟出的函式。貼出三步的輸出。

2. **config 名 + 熱度掃描**：挑另一個配置項（例如 `appendfsync` 或 `lazyfree-lazy-eviction`），反查它對應的變數，再用 `rg -c` 熱度掃描找出「這個主題的家在哪個檔」。確認你的收斂路徑。

3. **git 定位**：對 `src/evict.c` 跑 `git log --oneline -10`，挑一個看起來像「改了淘汰邏輯」的 commit，`git show <hash>` 讀它的 diff，用兩句話說明它改了什麼、為什麼（從 commit message）。

4. **測試當地圖**：讀 `tests/unit/maxmemory.tcl` 前 40 行，寫出「要手動觸發一次 LRU 淘汰」的最小步驟（config 設什麼、灌多少資料）。這就是你改 `evict.c` 後的驗證腳本。

5. **（選）二分法演練**：在任一有多 commit 的檔上，用 `git bisect start` / `git bisect good <舊hash>` / `git bisect bad HEAD` 手動走兩三步，感受二分如何收斂。用 `git bisect reset` 收工。（不需真的找 bug，體會流程即可。）

## 本章重點整理

- 定位 = 不從頭讀，而是**找一個能直接跳到目標的線索**。這是 binary RE「找字串/xref 跳到目標」的 source 版，且線索更豐富。
- 六種切入法，按線索選：**症狀字串**（有錯誤訊息，最快）、**config 名反查**（功能由開關控制）、**測試**（有測試，附觸發法）、**git 歷史**（最近改動/pickaxe）、**git bisect**（以前對現在錯）、**公開 API 往下**（保底）。
- redis 實戰：「改淘汰策略」→ config 反查 `maxmemory-policy` → `rg -c` 熱度掃描（`evict.c` 121 命中碾壓）→ 收斂到 `performEvictions`/`evictionPoolPopulate`。約十分鐘。
- **多條線索匯聚同一目標 = 定位正確的確認**（症狀字串與 config 名都指向 `evict.c`）。
- `rg -c | sort` 熱度掃描找「主題之家」、`git log -S` pickaxe 找「機制引入處」、`git bisect run` 全自動揪行為退化——三個高槓桿武器。
- 核心心態：**先定位、再局部深讀**，別想「先全懂再動手」。

## 自我檢核

- [ ] 給我一個「改某功能」的需求，我能說出該優先掏哪把定位鑰匙、為什麼。
- [ ] 我能用「rg 字串 → rg 使用點 → 讀上下文」三步，從一句錯誤訊息追到觸發它的函式。
- [ ] 我知道 `rg -c | sort` 熱度掃描能找「主題之家」，也知道要用常識排除註冊/宣告類的假高分。
- [ ] 我能說出 `git bisect run` 為什麼是「行為退化定位」的神器，以及要餵它什麼。
- [ ] 面對大專案，我的預設是「先定位到 200 行再深讀」，而不是「先讀懂整個模組」。

Part 2 到此完整：從第一次接觸的偵察（Ch 5）、找 entry（Ch 6）、建地圖（Ch 7），到追資料（Ch 8）、看控制流（Ch 9）、假設破案（Ch 10）、精準定位（Ch 11）——你已經有了一套完整的攻堅 SOP。現在該把它真刀真槍跑一遍了。接下來進入第一個練習，你將對一個真實 codebase 獨立完成偵察與架構地圖，把這七章的技巧串成一次完整實戰。

→ [練習 A：偵察與架構地圖](./practice-a-recon-and-map.md)
